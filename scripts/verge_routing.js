// Clash Verge profile script: persists optimizations across subscription updates.
var OPTIONS = (typeof OPTIONS !== 'undefined') ? OPTIONS : { primary_pool_size: 5 };

function isSubscriptionPlaceholder(proxy) {
  if ('server' in proxy && ['', '127.0.0.1', '0.0.0.0', 'localhost'].includes(String(proxy.server).trim().toLowerCase())) return true;
  if (['select', 'selector', 'url-test', 'fallback', 'load-balance', 'direct', 'reject', 'dns'].includes(String(proxy.type || '').toLowerCase())) return true;
  const name = String(proxy.name || '').replace(/^\s*\[[^\]]+\]\s*/, '').toLowerCase();
  const words = name.replace(/[^\p{L}\p{N}_]+/gu, ' ').trim();
  if (['тех. работах', 'техработах', 'maintenance', 'остаток', 'трафик', 'traffic',
    'истека', 'expire', 'информация', 'подписка', 'только tg бот', 'tg бот',
    'купить', 'новости', 'news', 'update'].some(token => name.includes(token))) return true;
  if (/авто\s*выбор|auto\s*select|autoselect|best\s*ping|load\s*balanc|баланс(?:ер|иров)/u.test(words)) return true;
  if (['balance', 'balancer', 'баланс', 'разделитель', 'separator'].includes(words)) return true;
  const tokens = words.split(/\s+/);
  const markers = ['ниже', 'выше', 'далее', 'below', 'above'];
  const devices = ['телефон', 'телефона', 'телефонов', 'мобильных', 'пк', 'компьютера', 'phone', 'mobile', 'desktop'];
  if (tokens.some((token, index) => markers.includes(token) && tokens.slice(index + 1).some(t => devices.includes(t)))) return true;
  if (/^(?:для\s+)?(?:телефона|телефонов|мобильных устройств|пк|компьютера|компьютеров)$/u.test(words)) return true;
  if (/^(?:for\s+)?(?:phones?|mobile|desktop|pc)(?:\s+only)?$/u.test(words)) return true;
  if (/(?:⏬.*⏬|⏫.*⏫|⬇.*⬇|⬆.*⬆)/u.test(name)) return true;
  if (/если\s+(?:глушат|не\s+работает)\s+интернет/u.test(words)) return true;
  return !words;
}

function primaryPool(candidates, preferred = []) {
  const size = Math.max(1, Math.min(10, OPTIONS.primary_pool_size || 5));
  const ordered = [...new Set([...preferred.filter(n => candidates.includes(n)), ...candidates])];
  const chosen = [], providers = new Set();
  for (const name of ordered) {
    const provider = name.match(/^\[([^\]]+)\]/)?.[1] || name;
    if (!providers.has(provider)) { chosen.push(name); providers.add(provider); }
    if (chosen.length >= size) return chosen;
  }
  for (const name of ordered) {
    if (!chosen.includes(name)) chosen.push(name);
    if (chosen.length >= size) break;
  }
  return chosen;
}

function isUsNode(name) {
  const n = name.toLowerCase();
  return ['🇺🇸', 'сша', 'usa', 'united states', 'america', 'new york', 'нью-йорк', 'los angeles', 'лос-анджелес', 'chicago', 'чикаго', 'miami', 'майами', 'seattle', 'сиэтл', 'dallas', 'даллас', 'california', 'калифорния', 'ashburn', 'эшберн'].some(c => n.includes(c));
}

function main(config) {
  const removed = new Set((config.proxies || []).filter(isSubscriptionPlaceholder).map(p => p.name));
  config.proxies = (config.proxies || []).filter(p => !removed.has(p.name));
  const groups = config['proxy-groups'] || [];
  for (const group of groups) group.proxies = (group.proxies || []).filter(name => !removed.has(name));
  const upsert = group => {
    const old = groups.find(g => g.name === group.name);
    if (old) Object.assign(old, group); else groups.push(group);
  };
  for (const [name, reserveName, key, interval, lazy] of [
    ['Auto-Fallback', 'Aegis-Reserve', 'primary_nodes', 30, false],
    ['🛡️ Mobile-Bypass', 'Aegis-Mobile-Reserve', 'mobile_nodes', 60, true],
    ['🤖 Auto-AI-Stable', 'Aegis-AI-Reserve', 'ai_nodes', 60, false],
    ['✈️ Auto-TG-Stable', '✈️ TG-Reserve', 'tg_nodes', 30, false],
    ['💬 Auto-Discord-Stable', '💬 Discord-Reserve', 'discord_nodes', 30, false],
    ['💻 Auto-Dev-Stable', '💻 Dev-Reserve', 'dev_nodes', 30, false],
    ['🎬 Auto-Media-Fast', '🎬 Media-Reserve', 'media_nodes', 30, false],
    ['🪙 Auto-Crypto-Stable', '🪙 Crypto-Reserve', 'crypto_nodes', 30, false],
    ['🎯 Auto-Games-Stable', '🎯 Games-Reserve', 'games_nodes', 30, false]
  ]) {
    const group = groups.find(g => g.name === name);
    if (!group) continue;
    const oldReserve = groups.find(g => g.name === reserveName);
    const original = [...new Set([...(group.proxies || []).filter(n => n !== reserveName), ...(oldReserve?.proxies || [])])];
    const candidates = [...new Set([...(OPTIONS.node_order || []).filter(n => original.includes(n)), ...original])];
    const selected = primaryPool(candidates, OPTIONS[key]);
    const reserve = candidates.filter(n => !selected.includes(n));
    Object.assign(group, {proxies: [...selected, ...(reserve.length ? [reserveName] : [])],
      interval, timeout: key === 'ai_nodes' ? 5000 : 4000, lazy, 'max-failed-times': 2});
    if (reserve.length) upsert({ name: reserveName, type: 'fallback', proxies: reserve,
      url: group.url, interval: 180, timeout: group.timeout, lazy: true,
      'max-failed-times': 3, hidden: true,
      ...(group['expected-status'] ? {'expected-status': group['expected-status']} : {}) });
  }
  const fast = groups.find(g => g.name === 'Auto-UrlTest');
  if (fast) Object.assign(fast, {proxies: primaryPool(fast.proxies, OPTIONS.primary_nodes),
    interval: 60, timeout: 4000, tolerance: 100, lazy: true});
  const ai = groups.find(g => g.name === '🤖 Auto-AI-Stable');
  const maxTrust = groups.find(g => g.name === '🤖 AI-Max-Trust');
  let poolProxies = [];
  if (ai || maxTrust) {
    let candidates = [];
    if (maxTrust) candidates.push(...(maxTrust.proxies || []));
    if (ai) candidates.push(...(ai.proxies || []));
    const aiReserve = groups.find(g => g.name === 'Aegis-AI-Reserve');
    if (aiReserve) candidates.push(...(aiReserve.proxies || []));
    candidates = [...new Set(candidates)];

    const usCandidates = candidates.filter(isUsNode);
    const preferredMax = OPTIONS.ai_max_trust_nodes || OPTIONS.ai_nodes || [];
    const preferredUs = preferredMax.filter(p => isUsNode(p) && usCandidates.includes(p));
    const rankedUs = (OPTIONS.node_order || []).filter(p => isUsNode(p) && usCandidates.includes(p));
    const finalUs = [...new Set([...preferredUs, ...rankedUs, ...usCandidates])];
    poolProxies = finalUs.length ? finalUs : candidates;

    upsert({
      name: '🤖 AI-Max-Trust',
      type: 'select',
      proxies: poolProxies
    });

    const aiSrv = groups.find(g => g.name === '🤖 AI-Services');
    if (aiSrv) {
      if ((aiSrv.proxies || []).includes('🤖 AI-Max-Trust')) {
        aiSrv.proxies = ['🤖 AI-Max-Trust', ...aiSrv.proxies.filter(p => p !== '🤖 AI-Max-Trust')];
      } else {
        aiSrv.proxies = ['🤖 AI-Max-Trust', ...(aiSrv.proxies || [])];
      }
    }
  }
  const aiSrv = groups.find(g => g.name === '🤖 AI-Services');
  if (aiSrv) {
    let proxies = (aiSrv.proxies || []).map(p => p === 'DIRECT' ? 'REJECT' : p).filter(p => p !== 'DIRECT');
    if (!proxies.includes('REJECT')) {
      proxies.push('REJECT');
    }
    proxies = [...new Set(proxies)];
    if (proxies.includes('🤖 AI-Max-Trust')) {
      proxies = ['🤖 AI-Max-Trust', ...proxies.filter(p => p !== '🤖 AI-Max-Trust')];
    }
    aiSrv.proxies = proxies;
  }
  if (ai) {
    const rules = config.rules || [];
    const serviceRules = [];
    for (const [name, url, expected, domains] of [
      ['Aegis-Antigravity', 'https://daily-cloudcode-pa.googleapis.com/', '404', ['cloudcode-pa.googleapis.com', 'daily-cloudcode-pa.googleapis.com']],
      ['Aegis-Google-AI', 'https://generativelanguage.googleapis.com', '404', ['aistudio.google.com', 'alkalimakersuite-pa.clients6.google.com', 'alkalimakersuite-pa.googleapis.com', 'jetski-webchannel.googleapis.com', 'makersuite.google.com', 'generativelanguage.googleapis.com', 'ai.google.dev', 'console.cloud.google.com', 'cloud.google.com', 'cloudresourcemanager.googleapis.com', 'cloudconsole-pa.clients6.google.com', 'serviceusage.googleapis.com', 'developerprofiles-pa.googleapis.com', 'cloudbilling.googleapis.com', 'apikeys.googleapis.com', 'iam.googleapis.com', 'apis.google.com', 'www.googleapis.com']],
      ['Aegis-Claude', 'https://api.anthropic.com/', '404', ['api.anthropic.com']],
      ['Aegis-OpenAI', 'https://api.openai.com/v1/models', '401', ['api.openai.com']]
    ]) {
      const isAiService = ['Aegis-Antigravity', 'Aegis-Google-AI', 'Aegis-Claude', 'Aegis-OpenAI'].includes(name);
      let proxies = isAiService
        ? [...(poolProxies.length ? poolProxies : ai.proxies)]
        : [...ai.proxies];
      proxies = proxies.filter(p => p !== 'DIRECT');
      if (!proxies.length) {
        proxies = ['REJECT'];
      }
      upsert({name, type: 'fallback', proxies, url, 'expected-status': expected,
        interval: isAiService ? 60 : 120, timeout: isAiService ? 7000 : 5000, lazy: !isAiService, 'max-failed-times': 2, hidden: true});
      for (const domain of domains) {
        serviceRules.push(`DOMAIN,${domain},${name}`);
        serviceRules.push(`DOMAIN-SUFFIX,${domain},${name}`);
      }
    }
    let index = rules.findIndex(r => r.includes('🤖 AI-Services') || r.startsWith('MATCH,'));
    if (index < 0) index = rules.length;
    rules.splice(index, 0, ...serviceRules.filter(rule => !rules.includes(rule)));
    config.rules = rules;
  }
  const aiKeywords = [
    'openai', 'chatgpt', 'anthropic', 'claude', 'gemini', 'deepmind',
    'deepseek', 'perplexity', 'mistral', 'cloudcode-pa', 'generativelanguage',
    'aistudio', 'makersuite', 'alkalimakersuite', 'jetski-webchannel',
    'openrouter', 'groq', 'x.ai', 'grok'
  ];
  if (config.rules) {
    config.rules = config.rules.map(r => {
      const parts = r.split(',').map(p => p.trim());
      if (parts.length >= 3 && parts[2] === 'DIRECT') {
        const pattern = parts[1].toLowerCase();
        if (aiKeywords.some(k => pattern.includes(k))) {
          parts[2] = 'REJECT';
          return parts.join(',');
        }
      }
      return r;
    });
  }
  if (config.dns) {
    config.hosts = config.hosts || {};
    if (!config.hosts['dns.google']) {
      config.hosts['dns.google'] = '8.8.8.8';
    }
    config.dns['nameserver-policy'] = config.dns['nameserver-policy'] || {};
    const aiTarget = 'https://dns.google/dns-query#🤖 AI-Max-Trust';
    const aiDnsDomains = [
      '+.openai.com', '+.chatgpt.com', '+.oaistatic.com', '+.oaiusercontent.com',
      '+.anthropic.com', '+.claude.ai', '+.claudeusercontent.com',
      '+.gemini.google.com', '+.generativelanguage.googleapis.com',
      '+.aistudio.google.com', '+.ai.google.dev', '+.deepmind.google', '+.deepmind.com',
      '+.perplexity.ai', '+.mistral.ai', '+.deepseek.com',
      '+.openrouter.ai', '+.x.ai', '+.grok.com',
      '+.cloudcode-pa.googleapis.com', '+.daily-cloudcode-pa.googleapis.com',
      '+.proactivebackend-pa.googleapis.com', '+.alkalimakersuite-pa.clients6.google.com',
      '+.alkalimakersuite-pa.googleapis.com', '+.jetski-webchannel.googleapis.com',
      '+.makersuite.google.com', '+.console.cloud.google.com',
      '+.cloudresourcemanager.googleapis.com', '+.cloudconsole-pa.clients6.google.com',
      '+.serviceusage.googleapis.com', '+.developerprofiles-pa.googleapis.com',
      '+.cloudbilling.googleapis.com', '+.apikeys.googleapis.com',
      'geosite:openai', 'geosite:anthropic', 'geosite:google-gemini'
    ];
    for (const d of aiDnsDomains) {
      config.dns['nameserver-policy'][d] = aiTarget;
    }
  }
  return config;
}
