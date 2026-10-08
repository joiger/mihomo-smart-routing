import http from 'node:http';
import fs from 'node:fs';
import { runTriageIfCooldownPassed, applyFix } from './incident_auto_triage.mjs';

const LOG_PATH = 'C:\\Users\\hitsugi ni ochita\\AppData\\Roaming\\Antigravity\\logs\\language_server.log';
const UNLOCKER_REPORT = 'C:\\Users\\hitsugi ni ochita\\Desktop\\Antigravity Unlocker - отчёт.txt';
const HTTP_PORT = 9750;

let activeWs = null;
let latestIncident = null;
let currentStatus = {
  state: 'green',
  color: '#10b981',
  bloom: '0 0 4px rgba(16, 185, 129, 0.9), 0 0 8px rgba(16, 185, 129, 0.45)',
  tooltip: 'Antigravity AI: 🟢 Онлайн (Aegis 🤖 Auto-AI-Stable) · 100мс',
  ping: 100,
  route: 'Aegis 🤖 Auto-AI-Stable',
  diagnosis: null
};

// 1. Lightweight Local Remediation Server
const server = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  if (req.url === '/api/status' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ status: currentStatus, incident: latestIncident }));
    return;
  }

  if (req.url === '/api/fix' && req.method === 'POST') {
    const rootCause = latestIncident?.rootCause || 'REGION_400_BLOCKED';
    const fixResult = applyFix(rootCause);
    parseHealth();
    if (activeWs && activeWs.readyState === WebSocket.OPEN) {
      updateDOM(activeWs);
    }
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: true, result: fixResult }));
    return;
  }

  res.writeHead(404);
  res.end('Not found');
});

server.listen(HTTP_PORT, '127.0.0.1', () => {
  console.log(`[Aegis Watcher] Remediation API запущен на http://127.0.0.1:${HTTP_PORT}`);
});

function getCDPInfo() {
  if (!fs.existsSync(LOG_PATH)) return null;
  try {
    const content = fs.readFileSync(LOG_PATH, 'utf8');
    const lines = content.split('\n');
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].includes('Electron WS URL:')) {
        return lines[i].split('Electron WS URL:')[1].trim();
      }
    }
  } catch (e) {}
  return null;
}

function parseHealth() {
  let isBlocked = false;
  let isBusy = false;
  let isOffline = false;
  let ping = 100;
  let route = 'Aegis 🤖 Auto-AI-Stable';

  if (fs.existsSync(UNLOCKER_REPORT)) {
    try {
      const repBuf = fs.readFileSync(UNLOCKER_REPORT);
      let rep = '';
      try {
        rep = new TextDecoder('utf-8', { fatal: true }).decode(repBuf);
      } catch {
        rep = new TextDecoder('windows-1251').decode(repBuf);
      }

      if (rep.includes('Первый маршрут сейчас:')) {
        const m = rep.match(/Первый маршрут сейчас:\s*([^\r\n]+)/);
        if (m) route = m[1].trim();
      }
      if (rep.includes('свой прокси — доступен')) {
        const m = rep.match(/свой прокси — доступен,\s*(\d+)\s*мс/);
        if (m) ping = parseInt(m[1], 10);
      }
      if (rep.includes('свой прокси — недоступен') || rep.includes('отложен на 2 мин')) {
        isOffline = true;
      }
    } catch (e) {}
  }

  if (fs.existsSync(LOG_PATH)) {
    try {
      const stats = fs.statSync(LOG_PATH);
      const readSize = Math.min(stats.size, 16384);
      const buffer = Buffer.alloc(readSize);
      const fd = fs.openSync(LOG_PATH, 'r');
      fs.readSync(fd, buffer, 0, readSize, stats.size - readSize);
      fs.closeSync(fd);

      const chunk = buffer.toString('utf8');
      const lines = chunk.split('\n');
      const recent = lines.slice(-25).join('\n');

      if (recent.includes('FAILED_PRECONDITION (code 400)') || recent.includes('region-400')) {
        isBlocked = true;
      }
      if (recent.includes('SEND_USER_CASCADE_MESSAGE') && !recent.includes('streamGenerateContent')) {
        isBusy = true;
      }
    } catch (e) {}
  }

  if (isBlocked || isOffline) {
    const triageRes = runTriageIfCooldownPassed(false);
    if (triageRes) {
      latestIncident = triageRes.diagnosis;
    }

    currentStatus = {
      state: 'red',
      color: '#ef4444',
      bloom: '0 0 4px rgba(239, 68, 68, 0.95), 0 0 9px rgba(239, 68, 68, 0.5)',
      tooltip: isOffline
        ? 'Antigravity AI: 🔴 Прокси недоступен! Нажмите для исправления'
        : 'Antigravity AI: 🔴 Ошибка региона 400! Нажмите для исправления',
      ping,
      route,
      diagnosis: latestIncident?.title || 'Обнаружена блокировка маршрута'
    };
  } else if (isBusy) {
    currentStatus = {
      state: 'yellow',
      color: '#f59e0b',
      bloom: '0 0 4px rgba(245, 158, 11, 0.9), 0 0 8px rgba(245, 158, 11, 0.45)',
      tooltip: `Antigravity AI: 🟡 Ожидание ответа... (~${ping}мс)`,
      ping,
      route,
      diagnosis: null
    };
  } else {
    currentStatus = {
      state: 'green',
      color: '#10b981',
      bloom: '0 0 4px rgba(16, 185, 129, 0.9), 0 0 8px rgba(16, 185, 129, 0.45)',
      tooltip: `Antigravity AI: 🟢 Онлайн (${route}) · ${ping}мс`,
      ping,
      route,
      diagnosis: null
    };
  }
}

function updateDOM(ws) {
  const statusJson = JSON.stringify(currentStatus);
  const incidentJson = JSON.stringify(latestIncident || {});

  const js = `
    (() => {
      const status = ${statusJson};
      const incident = ${incidentJson};

      // 1. Clean up legacy elements
      const legacyDot = document.getElementById('ag-health-dot');
      if (legacyDot) legacyDot.remove();
      const legacyTip = document.getElementById('ag-health-tooltip');
      if (legacyTip) legacyTip.remove();

      // 2. Find the exact model button in the prompt bar
      const btns = Array.from(document.querySelectorAll('button'));
      const modelBtn = btns.find(b => {
        const t = (b.innerText || '').trim();
        return b.className.includes('rounded-full') && (t.includes('Gemini') || t.includes('Flash') || t.includes('Claude') || t.includes('GPT') || /\\bPro\\b/.test(t));
      });

      if (!modelBtn) return;

      // Ensure unified container
      let container = document.getElementById('ag-controls-container');
      if (!container || container.parentElement !== modelBtn.parentElement) {
        if (container) container.remove();
        container = document.createElement('div');
        container.id = 'ag-controls-container';
        container.style.cssText = 'display:inline-flex;align-items:center;gap:6px;margin-left:6px;user-select:none;flex-shrink:0;';
        modelBtn.insertAdjacentElement('afterend', container);
      }

      // --- A. STATUS DOT ---
      let dot = document.getElementById('ag-model-health-dot');
      if (!dot || dot.parentElement !== container) {
        if (dot) dot.remove();
        dot = document.createElement('span');
        dot.id = 'ag-model-health-dot';
        dot.style.cssText = \`
          width: 7px;
          height: 7px;
          min-width: 7px;
          min-height: 7px;
          border-radius: 50%;
          cursor: pointer;
          pointer-events: auto;
          flex-shrink: 0;
          transition: background-color 0.25s ease, box-shadow 0.25s ease;
        \`;
        dot.onclick = (e) => {
          e.stopPropagation();
          let popup = document.getElementById('ag-model-health-popup');
          if (popup) { popup.remove(); return; }
          popup = document.createElement('div');
          popup.id = 'ag-model-health-popup';
          popup.style.cssText = \`
            position: fixed;
            bottom: 52px;
            left: 310px;
            background: rgba(24, 24, 27, 0.96);
            backdrop-filter: blur(12px);
            color: #f4f4f5;
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 10px;
            padding: 10px 14px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 11px;
            box-shadow: 0 12px 28px rgba(0,0,0,0.5);
            z-index: 999999999;
            pointer-events: auto;
            max-width: 320px;
          \`;

          const isRed = status.state === 'red';
          const title = isRed ? '🔴 Сбой доступа к модели' : (status.state === 'yellow' ? '🟡 Запрос в обработке' : '🟢 Соединение стабильно');
          const desc = isRed
            ? (status.diagnosis || 'Ошибка региона или сбой прокси-маршрута')
            : (status.route + ' · ' + status.ping + 'мс');

          let fixBtnHtml = '';
          if (isRed) {
            fixBtnHtml = \`
              <button id="ag-fix-action-btn" style="
                margin-top: 8px;
                width: 100%;
                background: #ef4444;
                color: #ffffff;
                font-weight: 600;
                font-size: 11px;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 5px;
                box-shadow: 0 0 6px rgba(239, 68, 68, 0.5);
                transition: opacity 0.2s ease;
              ">⚡ Применить исправление</button>
            \`;
          }

          popup.innerHTML = \`
            <div style="display:flex;align-items:center;gap:6px;font-weight:600;margin-bottom:4px;font-size:12px;">
              <span style="width:7px;height:7px;border-radius:50%;background:\${status.color};box-shadow:\${status.bloom};"></span>
              \${title}
            </div>
            <div style="color:#a1a1aa;font-size:11px;line-height:1.4;">\${desc}</div>
            \${fixBtnHtml}
          \`;

          document.body.appendChild(popup);

          if (isRed) {
            const fixBtn = document.getElementById('ag-fix-action-btn');
            if (fixBtn) {
              fixBtn.onclick = async (ev) => {
                ev.stopPropagation();
                fixBtn.disabled = true;
                fixBtn.innerText = '⏳ Применение исправления...';
                try {
                  const res = await fetch('http://127.0.0.1:9750/api/fix', { method: 'POST' });
                  const data = await res.json();
                  if (data.ok) {
                    fixBtn.style.background = '#10b981';
                    fixBtn.style.boxShadow = '0 0 6px rgba(16, 185, 129, 0.5)';
                    fixBtn.innerText = '✅ Исправлено! Маршрут обновлен';
                    setTimeout(() => { popup.remove(); }, 1800);
                  } else {
                    fixBtn.innerText = '❌ Ошибка применения';
                  }
                } catch (e) {
                  fixBtn.innerText = '❌ Сервер исправления недоступен';
                }
              };
            }
          }

          const closer = () => { popup.remove(); document.removeEventListener('click', closer); };
          setTimeout(() => document.addEventListener('click', closer), 50);
        };

        container.appendChild(dot);
      }

      // Static styling with soft neon bloom
      dot.style.backgroundColor = status.color;
      dot.style.boxShadow = status.bloom;
      dot.title = status.tooltip;

      // --- B. AUTO-RETRY TOGGLE SWITCH (ТУМБЛЕР) ---
      let toggle = document.getElementById('ag-auto-retry-toggle');
      if (!toggle || toggle.parentElement !== container) {
        if (toggle) toggle.remove();
        toggle = document.createElement('div');
        toggle.id = 'ag-auto-retry-toggle';
        toggle.title = 'Авто-перезапуск задач при ошибках (ВКЛ / ВЫКЛ)';
        toggle.style.cssText = 'display:inline-flex;align-items:center;gap:5px;cursor:pointer;padding:2px 4px;border-radius:6px;transition:background 0.2s;';

        const isEnabled = localStorage.getItem('ag_auto_retry_active') !== 'false'; // default ON

        toggle.innerHTML = \`
          <div id="ag-toggle-track" style="
            width: 24px;
            height: 14px;
            background: \${isEnabled ? '#10b981' : 'rgba(255,255,255,0.18)'};
            box-shadow: \${isEnabled ? '0 0 6px rgba(16, 185, 129, 0.4)' : 'none'};
            border-radius: 9999px;
            position: relative;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            box-sizing: border-box;
          ">
            <div id="ag-toggle-thumb" style="
              width: 10px;
              height: 10px;
              background: #ffffff;
              border-radius: 50%;
              position: absolute;
              top: 2px;
              left: 2px;
              transform: translateX(\${isEnabled ? '10px' : '0px'});
              transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
              box-shadow: 0 1px 3px rgba(0,0,0,0.3);
            "></div>
          </div>
          <span id="ag-toggle-label" style="
            font-size: 11px;
            color: \${isEnabled ? '#e4e4e7' : '#71717a'};
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-weight: 500;
            transition: color 0.2s;
          ">Auto-retry</span>
        \`;

        toggle.onclick = (e) => {
          e.stopPropagation();
          const cur = localStorage.getItem('ag_auto_retry_active') !== 'false';
          const next = !cur;
          localStorage.setItem('ag_auto_retry_active', next ? 'true' : 'false');

          const track = document.getElementById('ag-toggle-track');
          const thumb = document.getElementById('ag-toggle-thumb');
          const label = document.getElementById('ag-toggle-label');

          if (track) {
            track.style.background = next ? '#10b981' : 'rgba(255,255,255,0.18)';
            track.style.boxShadow = next ? '0 0 6px rgba(16, 185, 129, 0.4)' : 'none';
          }
          if (thumb) {
            thumb.style.transform = \`translateX(\${next ? '10px' : '0px'})\`;
          }
          if (label) {
            label.style.color = next ? '#e4e4e7' : '#71717a';
          }

          let toast = document.getElementById('ag-toast');
          if (toast) toast.remove();
          toast = document.createElement('div');
          toast.id = 'ag-toast';
          toast.style.cssText = \`
            position: fixed;
            bottom: 56px;
            left: 310px;
            background: rgba(24, 24, 27, 0.95);
            backdrop-filter: blur(10px);
            color: #f4f4f5;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 6px 12px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 11px;
            box-shadow: 0 8px 20px rgba(0,0,0,0.4);
            z-index: 999999999;
            pointer-events: none;
            transition: opacity 0.3s ease;
          \`;
          toast.innerText = next ? '⚡ Авто-перезапуск задач при ошибках: ВКЛЮЧЕН' : '⚪ Авто-перезапуск задач при ошибках: ВЫКЛЮЧЕН';
          document.body.appendChild(toast);
          setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 2200);
        };

        container.appendChild(toggle);
      }

      // --- C. AUTO-RETRY BACKGROUND ENGINE ---
      if (!window._agAutoRetrySetup) {
        window._agAutoRetrySetup = true;
        window._agRetryCount = 0;
        window._agLastRetryTime = 0;

        const checkAndAutoRetry = () => {
          const isEnabled = localStorage.getItem('ag_auto_retry_active') !== 'false';
          if (!isEnabled) return;

          const btns = Array.from(document.querySelectorAll('button'));
          let retryBtn = null;

          for (const b of btns) {
            if (b.id && b.id.startsWith('ag-')) continue;
            const txt = (b.innerText || '').trim().toLowerCase();
            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
            const title = (b.title || '').toLowerCase();

            if (txt === 'retry' || txt === 'try again' || txt === 'повторить' || txt === 'повторить попытку' || txt === 'regenerate') {
              retryBtn = b;
              break;
            }
            if (aria === 'retry' || aria === 'try again' || aria === 'повторить' || title === 'retry') {
              retryBtn = b;
              break;
            }
            if ((txt.includes('retry') || txt.includes('повтор')) && (b.closest('[role="alert"]') || b.closest('.error') || b.closest('[class*="destructive"]'))) {
              retryBtn = b;
              break;
            }
          }

          if (retryBtn) {
            const now = Date.now();
            if (now - window._agLastRetryTime > 4000) {
              if (window._agRetryCount < 3) {
                window._agLastRetryTime = now;
                window._agRetryCount++;

                let toast = document.getElementById('ag-toast');
                if (toast) toast.remove();
                toast = document.createElement('div');
                toast.id = 'ag-toast';
                toast.style.cssText = \`
                  position: fixed;
                  bottom: 56px;
                  left: 310px;
                  background: rgba(16, 185, 129, 0.95);
                  backdrop-filter: blur(10px);
                  color: #ffffff;
                  font-weight: 600;
                  border-radius: 8px;
                  padding: 7px 14px;
                  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                  font-size: 11px;
                  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
                  z-index: 999999999;
                  pointer-events: none;
                  transition: opacity 0.3s ease;
                \`;
                toast.innerText = \`🔄 Обнаружена ошибка: авто-перезапуск задачи (\${window._agRetryCount}/3)...\`;
                document.body.appendChild(toast);

                setTimeout(() => {
                  try {
                    retryBtn.click();
                    console.log('[Aegis Auto-Retry] Успешно нажат Retry');
                  } catch (err) {}
                  setTimeout(() => {
                    if (toast) { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }
                  }, 2500);
                }, 1400);
              }
            }
          } else {
            if (Date.now() - window._agLastRetryTime > 15000) {
              window._agRetryCount = 0;
            }
          }
        };

        setInterval(checkAndAutoRetry, 1000);
      }
    })()
  `;

  try {
    ws.send(JSON.stringify({
      id: Math.floor(Math.random() * 100000),
      method: 'Runtime.evaluate',
      params: { expression: js }
    }));
  } catch (e) {}
}

function connectToElectron() {
  const wsUrl = getCDPInfo();
  if (!wsUrl) {
    setTimeout(connectToElectron, 2000);
    return;
  }
  const port = new URL(wsUrl).port;

  http.get(`http://127.0.0.1:${port}/json`, (res) => {
    let data = '';
    res.on('data', c => data += c);
    res.on('end', () => {
      try {
        const targets = JSON.parse(data);
        const page = targets.find(t => t.type === 'page');
        if (!page) {
          setTimeout(connectToElectron, 2000);
          return;
        }

        const ws = new WebSocket(page.webSocketDebuggerUrl);
        ws.onopen = () => {
          activeWs = ws;
          parseHealth();
          updateDOM(ws);
        };
        ws.onclose = () => {
          activeWs = null;
          setTimeout(connectToElectron, 2000);
        };
        ws.onerror = () => {
          activeWs = null;
        };
      } catch (e) {
        setTimeout(connectToElectron, 2000);
      }
    });
  }).on('error', () => {
    setTimeout(connectToElectron, 3000);
  });
}

// Background poll loop
setInterval(() => {
  parseHealth();
  if (activeWs && activeWs.readyState === WebSocket.OPEN) {
    updateDOM(activeWs);
  }
}, 2500);

connectToElectron();
