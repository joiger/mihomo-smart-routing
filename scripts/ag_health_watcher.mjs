import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import { runTriageIfCooldownPassed, applyFix } from './incident_auto_triage.mjs';

const LOG_PATH = 'C:\\Users\\hitsugi ni ochita\\AppData\\Roaming\\Antigravity\\logs\\language_server.log';
const UNLOCKER_REPORT = 'C:\\Users\\hitsugi ni ochita\\Desktop\\Antigravity Unlocker - отчёт.txt';
const HTTP_PORT = 9750;
const PROXY_PORT = 7897;

let activeWs = null;
let latestIncident = null;
let currentStatus = {
  state: 'ok', // 'ok' (white), 'yellow' (healing/in-flight), 'red' (error)
  knobColor: '#ffffff',
  knobBloom: '0 1px 3px rgba(0,0,0,0.35)',
  trackBg: 'rgba(255, 255, 255, 0.22)',
  tooltip: 'Antigravity AI: 🟢 Онлайн (Aegis 🤖 Auto-AI-Stable) · Всё в порядке',
  ping: 100,
  route: 'Aegis 🤖 Auto-AI-Stable',
  diagnosis: null
};

// 1. Lightweight Local API
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
  console.log(`[Aegis Watcher] Self-healing API запущен на http://127.0.0.1:${HTTP_PORT}`);
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

// Check TCP connectivity to Clash mixed-port
function checkProxyAlive(port = PROXY_PORT, timeout = 500) {
  return new Promise(resolve => {
    const socket = new net.Socket();
    let isResolved = false;
    socket.setTimeout(timeout);
    socket.once('connect', () => {
      isResolved = true;
      socket.destroy();
      resolve(true);
    });
    socket.once('timeout', () => {
      if (!isResolved) {
        isResolved = true;
        socket.destroy();
        resolve(false);
      }
    });
    socket.once('error', () => {
      if (!isResolved) {
        isResolved = true;
        socket.destroy();
        resolve(false);
      }
    });
    socket.connect(port, '127.0.0.1');
  });
}

async function parseHealth() {
  let isBlocked = false;
  let isBusy = false;
  let isOffline = false;
  let ping = 100;
  let route = 'Aegis 🤖 Auto-AI-Stable';

  // 1. Probe Clash mixed-port
  const proxyAlive = await checkProxyAlive();
  if (!proxyAlive) {
    isOffline = true;
  }

  // 2. Truthful Parse of language_server.log (Chronological from tail)
  if (fs.existsSync(LOG_PATH)) {
    try {
      const stats = fs.statSync(LOG_PATH);
      const readSize = Math.min(stats.size, 32768);
      const buffer = Buffer.alloc(readSize);
      const fd = fs.openSync(LOG_PATH, 'r');
      fs.readSync(fd, buffer, 0, readSize, stats.size - readSize);
      fs.closeSync(fd);

      const chunk = buffer.toString('utf8');
      const lines = chunk.split('\n').filter(l => l.trim().length > 0);

      // Walk backwards to find the LAST model event
      let lastEvent = null;
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i];
        if (line.includes('streamGenerateContent') && line.includes('ResponseID:')) {
          lastEvent = 'SUCCESS';
          break;
        }
        if (line.includes('FAILED_PRECONDITION (code 400)') || line.includes('region-400')) {
          lastEvent = 'BLOCKED_400';
          break;
        }
        if (line.includes('SEND_USER_CASCADE_MESSAGE') && !lastEvent) {
          lastEvent = 'IN_FLIGHT';
          break;
        }
      }

      if (lastEvent === 'BLOCKED_400') {
        isBlocked = true;
      } else if (lastEvent === 'IN_FLIGHT') {
        isBusy = true;
      }
    } catch (e) {}
  }

  // 3. Unlocker report
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

      // Check if unlocker report ended with a model success
      const uLines = rep.split('\n').filter(l => l.trim().length > 0);
      const lastULines = uLines.slice(-10).join('\n');
      if (lastULines.includes('модель ответила (x1)')) {
        // If the very latest event was success, don't trigger blocked
        isBlocked = false;
        isOffline = false;
      }
    } catch (e) {}
  }

  // Determine State
  if (isBlocked || isOffline) {
    const triageRes = runTriageIfCooldownPassed(false);
    if (triageRes) {
      latestIncident = triageRes.diagnosis;
    }

    currentStatus = {
      state: 'red',
      knobColor: '#ef4444',
      knobBloom: '0 0 6px rgba(239, 68, 68, 0.95), 0 0 11px rgba(239, 68, 68, 0.5)',
      trackBg: 'rgba(239, 68, 68, 0.35)',
      tooltip: isOffline
        ? 'Antigravity AI: 🔴 Прокси недоступен! Идёт самоисцеление...'
        : 'Antigravity AI: 🔴 Гео-блок Google 400! Идёт самоисцеление...',
      ping,
      route,
      diagnosis: latestIncident?.title || 'Сбой маршрута или блокировка'
    };

    // Autonomous Self-Healing: immediately heal if needed
    applyFix(latestIncident?.rootCause || 'REGION_400_BLOCKED');
  } else if (isBusy) {
    currentStatus = {
      state: 'yellow',
      knobColor: '#f59e0b',
      knobBloom: '0 0 6px rgba(245, 158, 11, 0.9), 0 0 10px rgba(245, 158, 11, 0.45)',
      trackBg: 'rgba(245, 158, 11, 0.28)',
      tooltip: `Antigravity AI: 🟡 Ожидание ответа... (~${ping}мс)`,
      ping,
      route,
      diagnosis: null
    };
  } else {
    // Штатный режим: чистый белый кружочек, не выбивается из темы Antigravity!
    currentStatus = {
      state: 'ok',
      knobColor: '#ffffff',
      knobBloom: '0 1px 3px rgba(0,0,0,0.35)',
      trackBg: 'rgba(255, 255, 255, 0.2)',
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

      // 1. Remove obsolete standalone dots or old containers
      const legacyDot = document.getElementById('ag-model-health-dot');
      if (legacyDot) legacyDot.remove();
      const legacyOldDot = document.getElementById('ag-health-dot');
      if (legacyOldDot) legacyOldDot.remove();
      const legacyTip = document.getElementById('ag-health-tooltip');
      if (legacyTip) legacyTip.remove();

      // 2. Locate model selector button
      const btns = Array.from(document.querySelectorAll('button'));
      const modelBtn = btns.find(b => {
        const t = (b.innerText || '').trim();
        return b.className.includes('rounded-full') && (t.includes('Gemini') || t.includes('Flash') || t.includes('Claude') || t.includes('GPT') || /\\bPro\\b/.test(t));
      });

      if (!modelBtn) return;

      // Ensure native-matching wrapper in chat prompt bar
      let wrapper = document.getElementById('ag-controls-container');
      if (!wrapper || wrapper.parentElement !== modelBtn.parentElement) {
        if (wrapper) wrapper.remove();
        wrapper = document.createElement('div');
        wrapper.id = 'ag-controls-container';
        wrapper.style.cssText = 'display:inline-flex;align-items:center;margin-left:6px;user-select:none;flex-shrink:0;';
        modelBtn.insertAdjacentElement('afterend', wrapper);
      }

      // 3. Native Antigravity-styled Toggle Capsule with Integrated Indicator Knob
      let toggle = document.getElementById('ag-auto-retry-toggle');
      if (!toggle || toggle.parentElement !== wrapper || !toggle.className.includes('rounded-full')) {
        if (toggle) toggle.remove();
        toggle = document.createElement('div');
        toggle.id = 'ag-auto-retry-toggle';
        toggle.className = 'flex items-center h-7 gap-1.5 rounded-full px-2 text-xs text-secondary-foreground select-none outline-none hover:bg-secondary transition-colors cursor-pointer';
        toggle.style.cssText = 'background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08);';

        const isEnabled = localStorage.getItem('ag_auto_retry_active') !== 'false';

        toggle.innerHTML = \`
          <span id="ag-toggle-label" style="
            font-size: 11px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-weight: 500;
            color: \${isEnabled ? '#e4e4e7' : '#71717a'};
            transition: color 0.2s;
          ">Auto-retry</span>
          <div id="ag-toggle-track" style="
            width: 26px;
            height: 14px;
            background: \${isEnabled ? status.trackBg : 'rgba(255,255,255,0.1)'};
            border-radius: 9999px;
            position: relative;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            box-sizing: border-box;
          ">
            <div id="ag-toggle-thumb" style="
              width: 10px;
              height: 10px;
              background: \${isEnabled ? status.knobColor : '#71717a'};
              box-shadow: \${isEnabled ? status.knobBloom : 'none'};
              border-radius: 50%;
              position: absolute;
              top: 2px;
              left: 2px;
              transform: translateX(\${isEnabled ? '12px' : '0px'});
              transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), background-color 0.25s ease, box-shadow 0.25s ease;
            "></div>
          </div>
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
            track.style.background = next ? status.trackBg : 'rgba(255,255,255,0.1)';
          }
          if (thumb) {
            thumb.style.transform = \`translateX(\${next ? '12px' : '0px'})\`;
            thumb.style.background = next ? status.knobColor : '#71717a';
            thumb.style.boxShadow = next ? status.knobBloom : 'none';
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
          toast.innerText = next ? '⚡ Авто-перезапуск задач (50 попыток): ВКЛЮЧЕН' : '⚪ Авто-перезапуск задач: ВЫКЛЮЧЕН';
          document.body.appendChild(toast);
          setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 2200);
        };

        wrapper.appendChild(toggle);
      }

      // Update state live
      const isEnabled = localStorage.getItem('ag_auto_retry_active') !== 'false';
      const track = document.getElementById('ag-toggle-track');
      const thumb = document.getElementById('ag-toggle-thumb');
      if (track && isEnabled) {
        track.style.background = status.trackBg;
      }
      if (thumb && isEnabled) {
        thumb.style.background = status.knobColor;
        thumb.style.boxShadow = status.knobBloom;
      }
      toggle.title = status.tooltip;

      // 4. Autonomous Self-Healing & Auto-Retry Loop with 50-Attempt Cutoff
      if (!window._agAutoRetrySetup) {
        window._agAutoRetrySetup = true;
        window._agRetryCount = 0;
        window._agLastRetryTime = 0;
        const MAX_RETRIES = 50;

        const checkAndAutoRetry = () => {
          const isAutoActive = localStorage.getItem('ag_auto_retry_active') !== 'false';
          if (!isAutoActive) return;

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
            if (now - window._agLastRetryTime > 3500) {
              if (window._agRetryCount < MAX_RETRIES) {
                window._agLastRetryTime = now;
                window._agRetryCount++;

                // Trigger self-healing API in background
                fetch('http://127.0.0.1:9750/api/fix', { method: 'POST' }).catch(() => {});

                // Informative Toast with count (X/50)
                let toast = document.getElementById('ag-toast');
                if (toast) toast.remove();
                toast = document.createElement('div');
                toast.id = 'ag-toast';
                toast.style.cssText = \`
                  position: fixed;
                  bottom: 56px;
                  left: 310px;
                  background: rgba(24, 24, 27, 0.96);
                  backdrop-filter: blur(10px);
                  color: #ffffff;
                  font-weight: 500;
                  border: 1px solid rgba(255, 255, 255, 0.15);
                  border-radius: 8px;
                  padding: 7px 14px;
                  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                  font-size: 11px;
                  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
                  z-index: 999999999;
                  pointer-events: none;
                  transition: opacity 0.3s ease;
                \`;
                toast.innerText = \`🔄 Самоисцеление и перезапуск задачи (\${window._agRetryCount}/\${MAX_RETRIES})...\`;
                document.body.appendChild(toast);

                setTimeout(() => {
                  try {
                    retryBtn.click();
                    console.log(\`[Aegis Auto-Retry] Попытка \${window._agRetryCount}/\${MAX_RETRIES} выполнена\`);
                  } catch (err) {}
                  setTimeout(() => {
                    if (toast) { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }
                  }, 2500);
                }, 1500);
              } else {
                // Reached 50 retries cutoff limit
                let toast = document.getElementById('ag-toast');
                if (toast) toast.remove();
                toast = document.createElement('div');
                toast.id = 'ag-toast';
                toast.style.cssText = \`
                  position: fixed;
                  bottom: 56px;
                  left: 310px;
                  background: rgba(239, 68, 68, 0.95);
                  color: #ffffff;
                  font-weight: 600;
                  border-radius: 8px;
                  padding: 8px 14px;
                  font-size: 11px;
                  z-index: 999999999;
                \`;
                toast.innerText = '⚠️ Достигнут лимит 50 авто-повторений. Проверьте сеть вручную.';
                document.body.appendChild(toast);
              }
            }
          } else {
            // Reset counter when generation runs cleanly for 20 seconds
            if (Date.now() - window._agLastRetryTime > 20000) {
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
        ws.onopen = async () => {
          activeWs = ws;
          await parseHealth();
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
setInterval(async () => {
  await parseHealth();
  if (activeWs && activeWs.readyState === WebSocket.OPEN) {
    updateDOM(activeWs);
  }
}, 2500);

connectToElectron();
