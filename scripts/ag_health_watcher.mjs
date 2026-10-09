import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import { runTriageIfCooldownPassed, applyFix, harvestLogs, currentModelEvent, currentRegionBlocked } from './incident_auto_triage.mjs';

process.on('uncaughtException', (err) => {
  console.error('[Aegis Watcher] Uncaught Exception:', err);
});
process.on('unhandledRejection', (reason) => {
  console.error('[Aegis Watcher] Unhandled Rejection:', reason);
});

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

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.log(`[Aegis Watcher] Порт ${HTTP_PORT} уже используется, фоновый процесс продолжает работу с интерфейсом.`);
  } else {
    console.error('[Aegis Watcher] Ошибка HTTP сервера:', err);
  }
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
  const liveLogs = harvestLogs();

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
      const lastEvent = currentModelEvent(chunk);

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

    } catch (e) {}
  }

  isBlocked = currentRegionBlocked(liveLogs);
  if (liveLogs.gate && Math.abs(Date.now() / 1000 - liveLogs.gate.at) < 120) {
    route = liveLogs.gate.route || route;
    const activeRoute = liveLogs.gate.routes?.find(item => item.label === route);
    if (activeRoute?.latency_ms != null) ping = activeRoute.latency_ms;
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
      knobBloom: '0 0 8px rgba(239, 68, 68, 0.95), 0 0 16px rgba(239, 68, 68, 0.6)',
      trackBg: 'rgba(239, 68, 68, 0.3)',
      tooltip: isOffline
        ? 'Antigravity AI: 🔴 Прокси недоступен! Идёт самоисцеление...'
        : 'Antigravity AI: 🔴 Гео-блок Google 400! Идёт самоисцеление...',
      ping,
      route,
      diagnosis: latestIncident?.title || 'Сбой маршрута или блокировка'
    };

    // Autonomous Self-Healing: immediately heal if needed
    if (triageRes) applyFix(latestIncident?.rootCause || 'PROXY_OFFLINE');
  } else if (isBusy) {
    currentStatus = {
      state: 'yellow',
      knobColor: '#f59e0b',
      knobBloom: '0 0 8px rgba(245, 158, 11, 0.95), 0 0 15px rgba(245, 158, 11, 0.5)',
      trackBg: 'rgba(245, 158, 11, 0.25)',
      tooltip: `Antigravity AI: 🟡 Ожидание ответа... (~${ping}мс)`,
      ping,
      route,
      diagnosis: null
    };
  } else {
    // Штатный режим: чистый белый кружочек с аккуратным ореолом
    currentStatus = {
      state: 'ok',
      knobColor: '#ffffff',
      knobBloom: '0 0 6px rgba(255, 255, 255, 0.8), 0 1px 3px rgba(0, 0, 0, 0.6)',
      trackBg: 'rgba(0, 0, 0, 0.55)',
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

      // 1. Autonomous Self-Healing & Auto-Retry Loop (Runs independently of prompt bar)
      if (!window._agAutoRetrySetup) {
        window._agAutoRetrySetup = true;
        window._agRetryCount = 0;
        window._agLastRetryTime = 0;
        window._agRetrying = false;
        const MAX_RETRIES = 50;

        function findRetryTarget() {
          if (window._agRetrying) return null;

          // 1. Check all buttons for retry / try again
          const allButtons = Array.from(document.querySelectorAll('button'));
          for (const b of allButtons) {
            if (b.id && (b.id.startsWith('ag-toggle') || b.id === 'ag-fix-action-btn')) continue;
            if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;

            const text = (b.textContent || '').trim().toLowerCase();
            const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
            const title = (b.getAttribute('title') || '').trim().toLowerCase();

            if (text === 'retry' || text === 'try again' || text === 'повторить' || 
                text === 'повторить попытку' || text === 'regenerate' || text === 'перезапустить') {
              return { element: b, text: b.textContent.trim() };
            }
            if (aria === 'retry' || aria === 'try again' || aria === 'повторить' || 
                title === 'retry' || title === 'try again') {
              return { element: b, text: aria || title };
            }
          }

          // 2. Button in error cards / banners
          for (const b of allButtons) {
            if (b.id && b.id.startsWith('ag-')) continue;
            if (b.disabled) continue;
            const text = (b.textContent || '').trim().toLowerCase();
            if (text.includes('retry') || text.includes('повтор') || text.includes('try again')) {
              const isInsideError = b.closest('[role="alert"], [class*="destructive"], [class*="card"], [class*="error"]');
              if (isInsideError) {
                return { element: b, text: b.textContent.trim() };
              }
            }
          }

          // 3. Inline error replay icon in [data-testid="user-input-step"]
          const userSteps = document.querySelectorAll('[data-testid="user-input-step"]');
          for (const step of userSteps) {
            const errorContainers = step.querySelectorAll('.text-secondary-foreground, .text-destructive, [class*="error"], [class*="red"]');
            for (const ec of errorContainers) {
              const clickables = ec.querySelectorAll('svg.cursor-pointer, button, [role="button"], span.cursor-pointer');
              for (const el of clickables) {
                if (el.id && el.id.startsWith('ag-')) continue;
                return { element: el, text: 'Inline Replay Icon' };
              }
            }
          }

          // 4. Elements with tooltip pointing to "Retry" / "Try again"
          const tooltips = Array.from(document.querySelectorAll('[id]')).filter(el => {
            const t = (el.textContent || '').trim().toLowerCase();
            return t === 'retry' || t === 'try again' || t === 'повторить' || t === 'повторить попытку';
          });
          for (const tt of tooltips) {
            const trigger = document.querySelector(\`[data-tooltip-id="\${tt.id}"]\`);
            if (trigger && !trigger.disabled && !trigger.classList.contains('pointer-events-none')) {
              return { element: trigger, text: 'Tooltip: ' + tt.textContent.trim() };
            }
          }

          return null;
        }

        function triggerClick(element) {
          if (!element) return;
          try {
            element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
            element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, view: window }));
            element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
          } catch (e) {}

          if (typeof element.click === 'function') {
            try { element.click(); } catch (e) {}
          }
          try {
            element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
          } catch (e) {}
        }

        const checkAndAutoRetry = () => {
          const isAutoActive = localStorage.getItem('ag_auto_retry_active') !== 'false';
          if (!isAutoActive) return;
          if (window._agRetrying) return;

          const target = findRetryTarget();
          if (target && target.element) {
            const now = Date.now();
            if (now - window._agLastRetryTime > 3500) {
              if (window._agRetryCount < MAX_RETRIES) {
                window._agLastRetryTime = now;
                window._agRetryCount++;
                window._agRetrying = true;

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
                toast.innerText = \`🔄 Обнаружена ошибка: авто-нажатие Retry (\${window._agRetryCount}/\${MAX_RETRIES})...\`;
                document.body.appendChild(toast);

                setTimeout(() => {
                  try {
                    triggerClick(target.element);
                    console.log(\`[Aegis Auto-Retry] Успешно нажат Retry (\${target.text}) [Попытка \${window._agRetryCount}/\${MAX_RETRIES}]\`);
                  } catch (err) {
                    console.error('[Aegis Auto-Retry] Ошибка нажатия:', err);
                  }
                  setTimeout(() => {
                    window._agRetrying = false;
                    if (toast) { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }
                  }, 2500);
                }, 1400);
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
            // Reset counter when clean for 20 seconds
            if (Date.now() - window._agLastRetryTime > 20000) {
              window._agRetryCount = 0;
            }
          }
        };

        setInterval(checkAndAutoRetry, 1000);
      }

      // 2. Locate model selector button
      const btns = Array.from(document.querySelectorAll('button'));
      const modelBtn = btns.find(b => {
        const t = (b.innerText || '').trim();
        return b.className.includes('rounded-full') && (t.includes('Gemini') || t.includes('Flash') || t.includes('Claude') || t.includes('GPT') || /\\bPro\\b/.test(t));
      });

      if (!modelBtn) return;

      // Clean up legacy wrapper if present
      const oldWrapper = document.getElementById('ag-controls-container');
      if (oldWrapper) oldWrapper.remove();

      // The model selector container inside the horizontal flex bar
      const modelContainer = modelBtn.parentElement.parentElement;
      const flexBar = modelContainer ? modelContainer.parentElement : modelBtn.parentElement;

      // 3. Distinct, High-End Antigravity-styled Capsule placed directly to the RIGHT on the same horizontal line
      let toggle = document.getElementById('ag-auto-retry-toggle');
      if (!toggle || toggle.parentElement !== flexBar || !document.getElementById('ag-toggle-icon')) {
        if (toggle) toggle.remove();
        toggle = document.createElement('div');
        toggle.id = 'ag-auto-retry-toggle';
        toggle.className = 'flex items-center h-7 gap-2 rounded-full px-2.5 text-xs select-none outline-none transition-all cursor-pointer';
        toggle.style.cssText = \`
          background: rgba(255, 255, 255, 0.07);
          backdrop-filter: blur(12px);
          border: 1px solid rgba(255, 255, 255, 0.16);
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
          margin-left: 6px;
          flex-shrink: 0;
          display: inline-flex;
          align-self: center;
          transition: background-color 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        \`;

        const isEnabled = localStorage.getItem('ag_auto_retry_active') !== 'false';

        toggle.innerHTML = \`
          <span id="ag-toggle-icon" style="display:flex;align-items:center;color:\${isEnabled ? '#3b82f6' : 'currentColor'};opacity:\${isEnabled ? '1' : '0.45'};transition:all 0.2s;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
              <path d="M3 3v5h5"/>
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
              <path d="M16 21h5v-5"/>
            </svg>
          </span>
          <span id="ag-toggle-label" class="font-medium text-secondary-foreground" style="
            font-size: 11.5px;
            letter-spacing: 0.2px;
            opacity: \${isEnabled ? '1' : '0.55'};
            transition: opacity 0.2s;
          ">Auto-retry</span>
          <div id="ag-toggle-track" style="
            width: 28px;
            height: 16px;
            background: \${isEnabled ? (status.state === 'red' ? 'rgba(239, 68, 68, 0.35)' : (status.state === 'yellow' ? 'rgba(245, 158, 11, 0.35)' : '#27272a')) : 'rgba(120, 120, 128, 0.2)'};
            border: 1px solid \${isEnabled ? (status.state === 'red' ? '#ef4444' : (status.state === 'yellow' ? '#f59e0b' : 'rgba(0, 0, 0, 0.2)')) : 'rgba(120, 120, 128, 0.3)'};
            border-radius: 9999px;
            position: relative;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            box-sizing: border-box;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.3);
          ">
            <div id="ag-toggle-thumb" style="
              width: 12px;
              height: 12px;
              background: \${isEnabled ? status.knobColor : '#9ca3af'};
              box-shadow: \${isEnabled ? status.knobBloom : 'none'};
              border-radius: 50%;
              position: absolute;
              top: 1px;
              left: 1px;
              transform: translateX(\${isEnabled ? '12px' : '0px'});
              transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), background-color 0.25s ease, box-shadow 0.25s ease;
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
          const icon = document.getElementById('ag-toggle-icon');

          if (track) {
            track.style.background = next ? (status.state === 'red' ? 'rgba(239, 68, 68, 0.35)' : (status.state === 'yellow' ? 'rgba(245, 158, 11, 0.35)' : '#27272a')) : 'rgba(120, 120, 128, 0.2)';
            track.style.borderColor = next ? (status.state === 'red' ? '#ef4444' : (status.state === 'yellow' ? '#f59e0b' : 'rgba(0, 0, 0, 0.2)')) : 'rgba(120, 120, 128, 0.3)';
          }
          if (thumb) {
            thumb.style.transform = \`translateX(\${next ? '12px' : '0px'})\`;
            thumb.style.background = next ? status.knobColor : '#9ca3af';
            thumb.style.boxShadow = next ? status.knobBloom : 'none';
          }
          if (label) {
            label.style.opacity = next ? '1' : '0.55';
          }
          if (icon) {
            icon.style.color = next ? '#3b82f6' : 'currentColor';
            icon.style.opacity = next ? '1' : '0.45';
          }

          let toast = document.getElementById('ag-toast');
          if (toast) toast.remove();
          toast = document.createElement('div');
          toast.id = 'ag-toast';
          toast.className = 'border border-border text-foreground shadow-lg';
          toast.style.cssText = \`
            position: fixed;
            bottom: 56px;
            left: 310px;
            background: rgba(24, 24, 27, 0.95);
            backdrop-filter: blur(10px);
            color: #f4f4f5;
            border-radius: 8px;
            padding: 7px 14px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 11.5px;
            z-index: 999999999;
            pointer-events: none;
            transition: opacity 0.3s ease;
          \`;
          toast.innerText = next ? '⚡ Авто-перезапуск задач (50 попыток): ВКЛЮЧЕН' : '⚪ Авто-перезапуск задач: ВЫКЛЮЧЕН';
          document.body.appendChild(toast);
          setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 2200);
        };

        if (modelContainer) {
          modelContainer.insertAdjacentElement('afterend', toggle);
        } else {
          flexBar.appendChild(toggle);
        }
      }

      // Update state live
      const isEnabled = localStorage.getItem('ag_auto_retry_active') !== 'false';
      const track = document.getElementById('ag-toggle-track');
      const thumb = document.getElementById('ag-toggle-thumb');
      const icon = document.getElementById('ag-toggle-icon');
      const label = document.getElementById('ag-toggle-label');
      if (track && isEnabled) {
        track.style.background = status.state === 'red' ? 'rgba(239, 68, 68, 0.35)' : (status.state === 'yellow' ? 'rgba(245, 158, 11, 0.35)' : '#27272a');
        track.style.borderColor = status.state === 'red' ? '#ef4444' : (status.state === 'yellow' ? '#f59e0b' : 'rgba(0, 0, 0, 0.2)');
      }
      if (thumb && isEnabled) {
        thumb.style.background = status.knobColor;
        thumb.style.boxShadow = status.knobBloom;
      }
      if (icon) {
        icon.style.color = isEnabled ? '#3b82f6' : 'currentColor';
        icon.style.opacity = isEnabled ? '1' : '0.45';
      }
      if (label) {
        label.style.opacity = isEnabled ? '1' : '0.55';
      }
      toggle.title = status.tooltip;
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
          try {
            activeWs = ws;
            await parseHealth();
            updateDOM(ws);
          } catch (err) {
            console.error('[Aegis Watcher] ws.onopen error:', err);
          }
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
  try {
    await parseHealth();
    if (activeWs && activeWs.readyState === WebSocket.OPEN) {
      updateDOM(activeWs);
    }
  } catch (err) {
    console.error('[Aegis Watcher] Poll loop error:', err);
  }
}, 2500);

connectToElectron();
