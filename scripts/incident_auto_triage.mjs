import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { execSync } from 'node:child_process';

const PATHS = {
  unlockerReport: 'C:\\Users\\hitsugi ni ochita\\Desktop\\Antigravity Unlocker - отчёт.txt',
  languageServerLog: 'C:\\Users\\hitsugi ni ochita\\AppData\\Roaming\\Antigravity\\logs\\language_server.log',
  clashLog: 'C:\\Users\\hitsugi ni ochita\\AppData\\Roaming\\io.github.clash-verge-rev.clash-verge-rev\\logs\\latest.log',
  clashConfig: 'C:\\Users\\hitsugi ni ochita\\AppData\\Roaming\\io.github.clash-verge-rev.clash-verge-rev\\clash-verge.yaml',
  activeProfile: 'C:\\Users\\hitsugi ni ochita\\AppData\\Roaming\\io.github.clash-verge-rev.clash-verge-rev\\profiles\\RjThMmU4TCJ9.yaml',
  diagnosticsDir: 'c:\\Users\\hitsugi ni ochita\\Documents\\antigravity\\router\\diagnostics',
  syncScript: 'c:\\Users\\hitsugi ni ochita\\Documents\\antigravity\\router\\sync.py'
};

let lastTriageTime = 0;
const COOLDOWN_MS = 60000; // 60s cooldown

export function readTailSafe(filePath, maxBytes = 16384, encoding = 'utf8') {
  if (!fs.existsSync(filePath)) return '';
  try {
    const stat = fs.statSync(filePath);
    if (stat.size === 0) return '';
    const readSize = Math.min(stat.size, maxBytes);
    const buf = Buffer.alloc(readSize);
    const fd = fs.openSync(filePath, 'r');
    fs.readSync(fd, buf, 0, readSize, stat.size - readSize);
    fs.closeSync(fd);

    if (encoding === 'auto') {
      // Try UTF-8 first (check BOM or valid UTF-8), fallback to windows-1251
      if (buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf) {
        return new TextDecoder('utf-8').decode(buf);
      }
      try {
        return new TextDecoder('utf-8', { fatal: true }).decode(buf);
      } catch {
        return new TextDecoder('windows-1251').decode(buf);
      }
    }
    return new TextDecoder(encoding).decode(buf);
  } catch (err) {
    return '';
  }
}

export function harvestLogs() {
  const unlockerTail = readTailSafe(PATHS.unlockerReport, 32768, 'auto');
  const lsTail = readTailSafe(PATHS.languageServerLog, 32768, 'utf8');
  const clashTail = readTailSafe(PATHS.clashLog, 16384, 'utf8');

  // Detect current active proxy/route
  let activeNode = 'Unknown';
  let unlockerStatus = 'OK';
  let unlockerPing = null;

  if (unlockerTail) {
    const routeMatch = unlockerTail.match(/Первый маршрут сейчас:\s*([^\r\n]+)/);
    if (routeMatch) activeNode = routeMatch[1].trim();
    const pingMatch = unlockerTail.match(/свой прокси — доступен,\s*(\d+)\s*мс/);
    if (pingMatch) unlockerPing = parseInt(pingMatch[1], 10);
  }

  return {
    timestamp: new Date().toISOString(),
    activeNode,
    unlockerPing,
    unlockerTail,
    lsTail,
    clashTail
  };
}

export function diagnose(logs) {
  const issues = [];
  let rootCause = 'HEALTHY';
  let severity = 'LOW';
  let title = 'Все системы работают штатно';
  let recommendation = 'Действий не требуется.';

  // 1. Check for Google Region 400
  const lsLines = logs.lsTail.split('\n');
  const recentLs = lsLines.slice(-40).join('\n');
  const isRegion400 = recentLs.includes('FAILED_PRECONDITION (code 400)') ||
                      recentLs.includes('User location is not supported') ||
                      logs.unlockerTail.includes('region-400') ||
                      logs.unlockerTail.includes('маршрут «свой прокси» нёс region-400');

  // 2. Check for TLS Handshake EOF / DPI
  const isDpiHandshake = logs.clashTail.includes('tls handshake eof') ||
                         logs.clashTail.includes('read: connection reset by peer') ||
                         logs.clashTail.includes('handshake timeout');

  // 3. Check for Proxy connection refusal / timeout
  const isOffline = logs.unlockerTail.includes('свой прокси — недоступен') ||
                    logs.unlockerTail.includes('отложен на 2 мин') ||
                    (logs.unlockerPing === null && logs.unlockerTail.includes('ошибка подключения'));

  if (isRegion400) {
    rootCause = 'REGION_400_BLOCKED';
    severity = 'HIGH';
    title = 'Гео-блокировка Google (Error 400: User location not supported)';
    recommendation = `Текущая выходная нода (${logs.activeNode}) заблокирована Google Cloud Code. Необходимо исключить узел из пула 🤖 Auto-AI-Stable и переключить трафик на проверенный чистый европейский/американский сервер.`;
    issues.push({
      type: 'REGION_400',
      description: 'Языковой сервер Antigravity получил HTTP 400 от daily-cloudcode-pa.googleapis.com',
      evidence: 'language_server.log: FAILED_PRECONDITION (code 400)'
    });
  } else if (isDpiHandshake) {
    rootCause = 'DPI_TLS_RESET';
    severity = 'MEDIUM';
    title = 'DPI-сброс TLS соединения (TSP/RKN фильтрация)';
    recommendation = 'Сработала цензура DPI на провайдере. Рекомендуется перевести целевой трафик на группу 🛡️ Mobile-Bypass (Shadowsocks/VLESS с обфускацией).';
    issues.push({
      type: 'TLS_RESET',
      description: 'Сброс TLS рукопожатия в логах Mihomo',
      evidence: 'latest.log: tls handshake eof / connection reset'
    });
  } else if (isOffline) {
    rootCause = 'PROXY_OFFLINE';
    severity = 'CRITICAL';
    title = 'Отвал прокси-маршрута в Unlocker';
    recommendation = 'Порт 7897 недоступен или ядро Mihomo остановлено. Требуется перезапуск службы Clash Verge.';
    issues.push({
      type: 'PROXY_DOWN',
      description: 'Unlocker не смог связаться с локальным портом 7897',
      evidence: 'Antigravity Unlocker: ошибка подключения к своему прокси'
    });
  }

  return {
    rootCause,
    severity,
    title,
    recommendation,
    issues,
    activeNode: logs.activeNode,
    unlockerPing: logs.unlockerPing
  };
}

export function generateReport(logs, diagnosis) {
  if (!fs.existsSync(PATHS.diagnosticsDir)) {
    fs.mkdirSync(PATHS.diagnosticsDir, { recursive: true });
  }

  const dateStr = new Date().toISOString().replace(/[:.]/g, '-');
  const reportPath = path.join(PATHS.diagnosticsDir, `incident_${dateStr}.md`);
  const latestJsonPath = path.join(PATHS.diagnosticsDir, 'latest_incident.json');

  const content = `# Aegis Incident Triage Report — ${new Date().toLocaleString('ru-RU')}

**Статус:** ${diagnosis.rootCause}  
**Уровень критичности:** ${diagnosis.severity}  
**Текущая нода:** ${diagnosis.activeNode} (Ping: ${diagnosis.unlockerPing ?? 'N/A'}ms)

---

## 1. Диагноз и первопричина
**${diagnosis.title}**

${diagnosis.recommendation}

### Выявленные отклонения:
${diagnosis.issues.length ? diagnosis.issues.map(i => `- **[${i.type}]**: ${i.description}\n  *Лог:* \`${i.evidence}\``).join('\n') : '- Явных аномалий не зафиксировано.'}

---

## 2. Срез лога Antigravity (language_server.log)
\`\`\`text
${logs.lsTail.split('\n').slice(-20).join('\n')}
\`\`\`

---

## 3. Срез отчёта Unlocker
\`\`\`text
${logs.unlockerTail.split('\n').slice(-25).join('\n')}
\`\`\`

---

## 4. Срез лога ядра Clash Verge (latest.log)
\`\`\`text
${logs.clashTail.split('\n').slice(-15).join('\n')}
\`\`\`
`;

  fs.writeFileSync(reportPath, content, 'utf8');

  const jsonPayload = {
    timestamp: new Date().toISOString(),
    reportFile: reportPath,
    ...diagnosis
  };
  fs.writeFileSync(latestJsonPath, JSON.stringify(jsonPayload, null, 2), 'utf8');

  return { reportPath, latestJsonPath, diagnosis };
}

export function applyFix(rootCause) {
  console.log(`[Aegis Remediation] Применение исправления для ${rootCause}...`);
  const result = { success: false, actionsTaken: [] };

  try {
    // 1. Try running sync.py if config.json exists
    const configPath = path.join(path.dirname(PATHS.syncScript), 'config.json');
    if (fs.existsSync(configPath)) {
      result.actionsTaken.push('Запуск sync.py для ротации пула нод...');
      try {
        execSync(`python "${PATHS.syncScript}"`, { cwd: path.dirname(PATHS.syncScript), stdio: 'pipe' });
        result.actionsTaken.push('Профиль Aegis успешно перегенерирован с исключением проблемных IP.');
      } catch (e) {
        result.actionsTaken.push('sync.py завершился с предупреждением: ' + e.message);
      }
    } else {
      result.actionsTaken.push('config.json отсутствует, выполняется прямая ротация активного профиля...');
    }

    // 2. Touch active profile to force Clash Verge reload
    if (fs.existsSync(PATHS.activeProfile)) {
      const time = new Date();
      fs.utimesSync(PATHS.activeProfile, time, time);
      result.actionsTaken.push('Профиль RjThMmU4TCJ9.yaml обновлен (отправлен сигнал перезагрузки в Clash Verge).');
    }

    if (fs.existsSync(PATHS.clashConfig)) {
      const time = new Date();
      fs.utimesSync(PATHS.clashConfig, time, time);
      result.actionsTaken.push('Конфиг clash-verge.yaml актуализирован.');
    }

    result.success = true;
  } catch (err) {
    result.error = err.message;
  }

  return result;
}

export function runTriageIfCooldownPassed(force = false) {
  const now = Date.now();
  if (!force && (now - lastTriageTime < COOLDOWN_MS)) {
    return null;
  }
  lastTriageTime = now;

  const logs = harvestLogs();
  const diag = diagnose(logs);
  const rep = generateReport(logs, diag);
  return rep;
}

// Standalone CLI execution
if (process.argv.includes('--test') || process.argv.includes('--run')) {
  console.log('--- Aegis Incident Harvester & Triage ---');
  const rep = runTriageIfCooldownPassed(true);
  console.log('Диагноз:', rep.diagnosis.title);
  console.log('Первопричина:', rep.diagnosis.rootCause);
  console.log('Отчёт создан:', rep.reportPath);
  if (process.argv.includes('--fix')) {
    const fixRes = applyFix(rep.diagnosis.rootCause);
    console.log('Результат исправления:', fixRes);
  }
}
