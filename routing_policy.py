"""Small, diverse primary pools with complete fallback coverage."""
import re
from node_filter import strip_subscription_placeholders


def primary_pool(candidates, size=5, preferred=None):
    candidates = list(dict.fromkeys(candidates))
    size = max(1, min(10, int(size)))
    preferred = [name for name in (preferred or []) if name in candidates]
    ordered = list(dict.fromkeys(preferred + candidates))
    picked, providers = [], set()
    # Preserve the best candidate, then diversify before filling remaining slots.
    for name in ordered:
        match = re.match(r"\[([^]]+)\]", name)
        provider = match.group(1) if match else name
        if provider not in providers:
            picked.append(name)
            providers.add(provider)
        if len(picked) >= size:
            return picked
    for name in ordered:
        if name not in picked:
            picked.append(name)
        if len(picked) >= size:
            break
    return picked


def is_us_node(name):
    n = name.lower()
    return any(c in n for c in [
        '🇺🇸', 'сша', 'usa', 'united states', 'america',
        'new york', 'нью-йорк', 'los angeles', 'лос-анджелес', 'chicago', 'чикаго',
        'miami', 'майами', 'seattle', 'сиэтл', 'dallas', 'даллас',
        'california', 'калифорния', 'ashburn', 'эшберн'
    ])


def optimize_routing(config, options=None):
    strip_subscription_placeholders(config)
    options = options or {}
    groups = config.get('proxy-groups', [])
    existing = {group['name']: group for group in groups}
    size = options.get('primary_pool_size', 5)
    for name, reserve_name, key, interval, lazy in [
        ('Auto-Fallback', 'Aegis-Reserve', 'primary_nodes', 30, False),
        ('🛡️ Mobile-Bypass', 'Aegis-Mobile-Reserve', 'mobile_nodes', 60, True),
        ('🤖 Auto-AI-Stable', 'Aegis-AI-Reserve', 'ai_nodes', 60, False),
        ('✈️ Auto-TG-Stable', '✈️ TG-Reserve', 'tg_nodes', 30, False),
        ('💬 Auto-Discord-Stable', '💬 Discord-Reserve', 'discord_nodes', 30, False),
        ('💻 Auto-Dev-Stable', '💻 Dev-Reserve', 'dev_nodes', 30, False),
        ('🎬 Auto-Media-Fast', '🎬 Media-Reserve', 'media_nodes', 30, False),
        ('🪙 Auto-Crypto-Stable', '🪙 Crypto-Reserve', 'crypto_nodes', 30, False),
        ('🎯 Auto-Games-Stable', '🎯 Games-Reserve', 'games_nodes', 30, False),
    ]:
        group = existing.get(name)
        if not group:
            continue
        candidates = [p for p in group.get('proxies', []) if p != reserve_name]
        # Re-applying an overlay must preserve the original reserve members.
        if reserve_name in existing:
            candidates = list(dict.fromkeys(candidates + existing[reserve_name].get('proxies', [])))
        ranked = [name for name in options.get('node_order', []) if name in candidates]
        candidates = list(dict.fromkeys(ranked + candidates))
        main = primary_pool(candidates, size, options.get(key))
        reserve = [p for p in candidates if p not in main]
        group.update(proxies=main + ([reserve_name] if reserve else []),
                     interval=interval, timeout=5000 if key == 'ai_nodes' else 4000,
                     lazy=lazy)
        group['max-failed-times'] = 2
        if reserve:
            backup = {'name': reserve_name, 'type': 'fallback', 'proxies': reserve,
                      'url': group['url'], 'interval': 180, 'timeout': group['timeout'],
                      'lazy': True, 'max-failed-times': 3, 'hidden': True}
            if 'expected-status' in group:
                backup['expected-status'] = group['expected-status']
            if reserve_name in existing:
                existing[reserve_name].update(backup)
            else:
                existing[reserve_name] = backup
                groups.append(backup)
    fast = existing.get('Auto-UrlTest')
    if fast:
        fast.update(proxies=primary_pool(fast.get('proxies', []), size, options.get('primary_nodes')),
                    interval=60, timeout=4000, tolerance=100, lazy=True)
    # Transport checks are separate for each service; they do not prove an account
    # is eligible or that authenticated model generation will succeed.
    ai = existing.get('🤖 Auto-AI-Stable')
    max_trust = existing.get('🤖 AI-Max-Trust')
    pool_proxies = []
    if ai or max_trust:
        candidates = []
        if max_trust:
            candidates.extend(max_trust.get('proxies', []))
        if ai:
            candidates.extend(ai.get('proxies', []))
        if 'Aegis-AI-Reserve' in existing:
            candidates.extend(existing['Aegis-AI-Reserve'].get('proxies', []))
        candidates = list(dict.fromkeys(candidates))

        us_candidates = [p for p in candidates if is_us_node(p)]
        preferred_max = options.get('ai_max_trust_nodes', options.get('ai_nodes', []))
        preferred_us = [p for p in preferred_max if is_us_node(p) and p in us_candidates]
        ranked_us = [p for p in options.get('node_order', []) if is_us_node(p) and p in us_candidates]
        final_us = list(dict.fromkeys(preferred_us + ranked_us + us_candidates))
        pool_proxies = final_us if final_us else candidates

        mt_group = {
            'name': '🤖 AI-Max-Trust',
            'type': 'select',
            'proxies': pool_proxies
        }
        if '🤖 AI-Max-Trust' in existing:
            existing['🤖 AI-Max-Trust'].update(mt_group)
        else:
            existing['🤖 AI-Max-Trust'] = mt_group
            groups.append(mt_group)

        ai_srv = existing.get('🤖 AI-Services')
        if ai_srv:
            current_srv_proxies = ai_srv.get('proxies', [])
            if '🤖 AI-Max-Trust' in current_srv_proxies:
                ai_srv['proxies'] = ['🤖 AI-Max-Trust'] + [p for p in current_srv_proxies if p != '🤖 AI-Max-Trust']
            else:
                ai_srv['proxies'] = ['🤖 AI-Max-Trust'] + current_srv_proxies

    ai_srv = existing.get('🤖 AI-Services')
    if ai_srv:
        proxies = [('REJECT' if p == 'DIRECT' else p) for p in ai_srv.get('proxies', [])]
        proxies = [p for p in proxies if p != 'DIRECT']
        if 'REJECT' not in proxies:
            proxies.append('REJECT')
        proxies = list(dict.fromkeys(proxies))
        if '🤖 AI-Max-Trust' in proxies:
            proxies = ['🤖 AI-Max-Trust'] + [p for p in proxies if p != '🤖 AI-Max-Trust']
        ai_srv['proxies'] = proxies

    if ai:
        services = [
            ('Aegis-Antigravity', 'https://daily-cloudcode-pa.googleapis.com/', '404',
             ['cloudcode-pa.googleapis.com', 'daily-cloudcode-pa.googleapis.com']),
            ('Aegis-Google-AI', 'https://generativelanguage.googleapis.com', '404',
             ['aistudio.google.com', 'alkalimakersuite-pa.clients6.google.com', 'alkalimakersuite-pa.googleapis.com', 'jetski-webchannel.googleapis.com', 'makersuite.google.com', 'generativelanguage.googleapis.com', 'ai.google.dev', 'console.cloud.google.com', 'cloud.google.com', 'cloudresourcemanager.googleapis.com', 'cloudconsole-pa.clients6.google.com', 'serviceusage.googleapis.com', 'developerprofiles-pa.googleapis.com', 'cloudbilling.googleapis.com', 'apikeys.googleapis.com', 'iam.googleapis.com', 'apis.google.com', 'www.googleapis.com']),
            ('Aegis-Claude', 'https://api.anthropic.com/', '404', ['api.anthropic.com']),
            ('Aegis-OpenAI', 'https://api.openai.com/v1/models', '401', ['api.openai.com']),
        ]
        service_rules = []
        for name, url, expected, domains in services:
            is_ai_service = name in ('Aegis-Antigravity', 'Aegis-Google-AI', 'Aegis-Claude', 'Aegis-OpenAI')
            if is_ai_service:
                proxies = list(pool_proxies if pool_proxies else ai['proxies'])
                interval = 60
                timeout = 7000
                lazy = False
            else:
                proxies = list(ai['proxies'])
                interval = 120
                timeout = 5000
                lazy = True
            proxies = [p for p in proxies if p != 'DIRECT']
            if not proxies:
                proxies = ['REJECT']
            group = {'name': name, 'type': 'fallback', 'proxies': proxies,
                     'url': url, 'expected-status': expected, 'interval': interval,
                     'timeout': timeout, 'lazy': lazy, 'max-failed-times': 2, 'hidden': True}
            if name in existing:
                existing[name].update(group)
            else:
                groups.append(group)
            for domain in domains:
                service_rules.append('DOMAIN,' + domain + ',' + name)
                service_rules.append('DOMAIN-SUFFIX,' + domain + ',' + name)
        rules = config.get('rules', [])
        # Keep local/loopback rules ahead of the new service rules for Unlocker.
        index = next((i for i, rule in enumerate(rules)
                      if '🤖 AI-Services' in rule or rule.startswith('MATCH,')), len(rules))
        rules[index:index] = [rule for rule in service_rules if rule not in rules]

    ai_keywords = (
        'openai', 'chatgpt', 'anthropic', 'claude', 'gemini', 'deepmind',
        'deepseek', 'perplexity', 'mistral', 'cloudcode-pa', 'generativelanguage',
        'aistudio', 'makersuite', 'alkalimakersuite', 'jetski-webchannel',
        'openrouter', 'groq', 'x.ai', 'grok'
    )
    if 'rules' in config:
        sanitized_rules = []
        for r in config['rules']:
            parts = [p.strip() for p in r.split(',')]
            if len(parts) >= 3 and parts[2] == 'DIRECT':
                pattern = parts[1].lower()
                if any(k in pattern for k in ai_keywords):
                    parts[2] = 'REJECT'
                    r = ','.join(parts)
            sanitized_rules.append(r)
        config['rules'] = sanitized_rules

    dns = config.get('dns')
    if isinstance(dns, dict):
        hosts = config.setdefault('hosts', {})
        if 'dns.google' not in hosts:
            hosts['dns.google'] = '8.8.8.8'
        ns_policy = dns.setdefault('nameserver-policy', {})
        ai_target = 'https://dns.google/dns-query#🤖 AI-Max-Trust'
        ai_dns_domains = [
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
        ]
        for d in ai_dns_domains:
            ns_policy[d] = ai_target

    return config
