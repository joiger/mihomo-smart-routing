# -*- coding: utf-8 -*-
"""
Aegis — Smart Routing & Multi-Subscription Merger
Merges multiple VPN subscriptions (supporting HWID & Anti-Bot cookies) into a single,
clean Mihomo config with smart routing for AI, Media, Discord, Games, and Russian services.
"""

import os
import sys
import json
import base64
import http.cookiejar
import urllib.parse
import concurrent.futures
import requests
import yaml

class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

def _mask_token_in_ci(val):
    if not val:
        return
    val_str = str(val).strip()
    if os.getenv("GITHUB_ACTIONS") == "true" and len(val_str) > 4:
        print(f"::add-mask::{val_str}", flush=True)

def _mask_proxies_in_ci(proxies):
    if os.getenv("GITHUB_ACTIONS") == "true":
        for p in proxies:
            if not isinstance(p, dict):
                continue
            uuid = p.get("uuid")
            if uuid:
                _mask_token_in_ci(uuid)
            pwd = p.get("password")
            if pwd:
                _mask_token_in_ci(pwd)

def parse_vless_uri(uri, prefix=""):
    if not uri.startswith("vless://"):
        return None
    try:
        rest = uri[len("vless://"):]
        if "#" in rest:
            rest, name = rest.split("#", 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = "Unnamed"
        
        if prefix:
            name = f"[{prefix}] {name}"
            
        user_info, host_port_query = rest.split("@", 1)
        uuid = user_info
        if "?" in host_port_query:
            host_port, query = host_port_query.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query))
        else:
            host_port = host_port_query
            params = {}
        
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            port = int(port)
        else:
            host = host_port
            port = 443
            
        proxy = {
            "name": name,
            "type": "vless",
            "server": host,
            "port": port,
            "uuid": uuid,
            "udp": True,
            "packet-encoding": "xudp"
        }
        
        security = params.get("security", "none").lower()
        if security in ["tls", "reality"]:
            proxy["tls"] = True
            sni = params.get("sni") or params.get("servername") or host
            proxy["servername"] = sni
            fp = params.get("fp") or params.get("client-fingerprint") or "chrome"
            proxy["client-fingerprint"] = fp
            
            if security == "reality":
                ropts = {}
                if "pbk" in params: ropts["public-key"] = params["pbk"]
                if "public-key" in params: ropts["public-key"] = params["public-key"]
                if "sid" in params: ropts["short-id"] = params["sid"]
                if "short-id" in params: ropts["short-id"] = params["short-id"]
                if "spx" in params: ropts["spider-x"] = params["spx"]
                proxy["reality-opts"] = ropts
        
        flow = params.get("flow")
        if flow:
            proxy["flow"] = flow
            
        net = params.get("type", "tcp").lower()
        proxy["network"] = net
        
        if net == "ws":
            ws_opts = {}
            if "path" in params: ws_opts["path"] = urllib.parse.unquote(params["path"])
            if "host" in params: ws_opts["headers"] = {"Host": params["host"]}
            proxy["ws-opts"] = ws_opts
        elif net == "grpc":
            grpc_opts = {}
            if "serviceName" in params: grpc_opts["grpc-service-name"] = params["serviceName"]
            proxy["grpc-opts"] = grpc_opts
        elif net in ["xhttp", "splithttp"]:
            proxy["network"] = "xhttp"
            xhttp_opts = {}
            if "path" in params: xhttp_opts["path"] = urllib.parse.unquote(params["path"])
            if "mode" in params: xhttp_opts["mode"] = params["mode"]
            proxy["xhttp-opts"] = xhttp_opts
            
        return proxy
    except Exception as e:
        print(f"Error parsing URI: {e}")
        return None

def fetch_subscription(sub_config):
    name = sub_config.get("name", "VPN")
    url = sub_config.get("url", "")
    headers = dict(sub_config.get("headers", {}))
    use_cookies = sub_config.get("use_cookies", False)
    
    if not url:
        print(f"  [!] Missing URL for provider '{name}'.")
        return []

    _mask_token_in_ci(url)
    print(f"[*] Fetching '{name}'...")

    if "User-Agent" not in headers:
        headers["User-Agent"] = "Clash-verge/1.7.7"

    session = requests.Session()
    cookie_file = f"{name}_cookies.txt"
    jar = None
    if use_cookies:
        try:
            jar = http.cookiejar.MozillaCookieJar(cookie_file)
            if os.path.exists(cookie_file):
                jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar
        except Exception as e:
            print(f"  [!] Note: Could not load cookie file '{cookie_file}': {e}")

    # Enforce strict TLS validation on outbound requests (prevent MitM attacks)
    try:
        resp = session.get(
            url,
            headers=headers,
            timeout=15,
            verify=True,
            allow_redirects=True
        )
        resp.raise_for_status()
    except requests.exceptions.SSLError as e:
        print(f"  [!] TLS verification FAILED for '{name}' (possible MitM or invalid cert): {e}")
        raise
    except Exception as e:
        print(f"  [!] Retrying fetch for '{name}' due to error: {e}")
        resp = session.get(
            url,
            headers=headers,
            timeout=15,
            verify=True,
            allow_redirects=True
        )
        resp.raise_for_status()

    if use_cookies and jar is not None:
        try:
            jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass

    raw = resp.content.strip()

    # Try YAML first (if subscription returns clash config directly)
    try:
        data = yaml.safe_load(raw.decode("utf-8", errors="ignore"))
        if isinstance(data, dict) and data.get("proxies"):
            proxies = data["proxies"]
            for p in proxies:
                p["name"] = f"[{name}] {p['name']}"
            print(f"  -> Got {len(proxies)} proxies (YAML) from '{name}'")
            _mask_proxies_in_ci(proxies)
            return proxies
    except Exception:
        pass

    # Try Base64 of VLESS/VMess URIs
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
    except Exception:
        decoded = raw.decode("utf-8", errors="ignore")

    proxies = []
    for line in decoded.splitlines():
        line = line.strip()
        if line:
            p = parse_vless_uri(line, prefix=name)
            if p:
                proxies.append(p)

    if not proxies and headers.get("User-Agent") != "Hiddify/2.0.5":
        print(f"  [i] 0 proxies found with default UA. Retrying '{name}' with Hiddify UA...")
        sub_retry = dict(sub_config)
        sub_retry["headers"] = dict(headers)
        sub_retry["headers"]["User-Agent"] = "Hiddify/2.0.5"
        return fetch_subscription(sub_retry)

    print(f"  -> Got {len(proxies)} proxies (URIs) from '{name}'")
    _mask_proxies_in_ci(proxies)
    return proxies

def fetch_all_subscriptions(subs, timeout=15):
    """
    Fetches subscriptions concurrently using ThreadPoolExecutor within a total timeout.
    Provides zero-downtime resilience: single provider failures do not abort execution.
    """
    if not subs:
        return []

    all_proxies = []
    max_workers = min(len(subs), 10)
    print(f"[*] Concurrently fetching {len(subs)} subscription(s) (workers: {max_workers}, timeout: {timeout}s)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sub = {executor.submit(fetch_subscription, sub): sub for sub in subs}
        done, not_done = concurrent.futures.wait(future_to_sub.keys(), timeout=timeout)

        for future in not_done:
            sub = future_to_sub[future]
            sub_name = sub.get("name", "Unknown")
            print(f"  [!] Warning: Provider '{sub_name}' exceeded total timeout of {timeout}s. Skipping.")
            future.cancel()

        for future in done:
            sub = future_to_sub[future]
            sub_name = sub.get("name", "Unknown")
            try:
                proxies = future.result()
                if proxies:
                    all_proxies.extend(proxies)
                else:
                    print(f"  [!] Warning: Provider '{sub_name}' returned 0 proxies.")
            except Exception as e:
                print(f"  [!] Warning: Provider '{sub_name}' failed to fetch ({type(e).__name__}: {e}). Continuing with remaining providers.")

    return all_proxies

def is_junk_or_auto(p):
    name = p.get("name", "").lower()
    server = str(p.get("server", "")).lower()
    
    # 1. Fake servers
    if server in ["127.0.0.1", "0.0.0.0", "localhost"]:
        return True
        
    # 2. Auto-select pseudo nodes
    auto_keywords = [
        "автовыбор", "авто-выбор", "auto-select", "autoselect", 
        "bestping", "best-ping", "balance", "loadbalance", "load-balance"
    ]
    if any(k in name for k in auto_keywords):
        return True
        
    # 3. Informational / maintenance / service stubs
    stub_keywords = [
        "тех. работах", "техработах", "maintenance", "остаток", "трафик", "traffic",
        "истека", "expire", "информация", "подписка", "только tg бот", "tg бот",
        "купить", "новости", "news", "update"
    ]
    if any(k in name for k in stub_keywords):
        return True
        
    # 4. Pure Russian nodes (waste ping / blocked destinations)
    # Exclude relay chains that exit outside RU (e.g. 'москва → германия')
    is_relay = "→" in name or "->" in name
    if not is_relay:
        if "🇷🇺" in p.get("name", ""):
            return True
        if any(k in name for k in ["россия", "russia"]):
            return True
        if "lte | все операторы" in name:
            return True
            
    return False

def get_proxy_fingerprint(p):
    """
    Generates a unique fingerprint tuple (server, port, uuid) for proxy deduplication.
    """
    server = str(p.get("server", "")).strip().lower()
    port = str(p.get("port", "")).strip()
    uuid = str(p.get("uuid", "") or p.get("password", "")).strip().lower()
    if server and port and uuid:
        return (server, port, uuid)
    elif server and port:
        return (server, port, "")
    return None

def deduplicate_proxies(proxies):
    """
    Deduplicates proxies based on (server, port, uuid) fingerprint,
    then ensures unique proxy names for Mihomo/Clash compatibility.
    """
    deduped = []
    seen_fingerprints = set()
    dup_count = 0

    for p in proxies:
        fp = get_proxy_fingerprint(p)
        if fp:
            if fp in seen_fingerprints:
                dup_count += 1
                continue
            seen_fingerprints.add(fp)
        deduped.append(p)

    if dup_count > 0:
        print(f"[*] Fingerprint deduplication: removed {dup_count} duplicate proxies.")

    # Ensure unique names
    seen_names = set()
    unique_proxies = []
    for p in deduped:
        name = p["name"]
        counter = 1
        orig_name = name
        while name in seen_names:
            counter += 1
            name = f"{orig_name} #{counter}"
        p["name"] = name
        seen_names.add(name)
        unique_proxies.append(p)

    return unique_proxies

def is_relay_or_bypass(n):
    return any(k in n for k in ["→", "->", "обход", "bypass", "relay"])

def fallback_priority(name):
    """
    Priority sorting for Fallback pool:
    Tier 1: Finland 🇫🇮 & Sweden 🇸🇪 (lowest physical latency ~30–45 ms).
    Tier 2: Transit bridges ('Москва → EU') and DPI bypass nodes ('Обход') for mobile LTE TSPU survival.
    Tier 3: Germany 🇩🇪, Netherlands 🇳🇱, Estonia 🇪🇪, Poland 🇵🇱 (also Latvia, Lithuania).
    Tier 4: Rest of Europe / regional (UK, France, Czechia, Turkey, Kazakhstan, Austria, Switzerland, Italy, Spain).
    Tier 5: USA 🇺🇸 and distant destinations.
    Tier 6: Other / unknown.
    """
    n = name.lower()
    is_relay = is_relay_or_bypass(n)

    # Tier 1: Finland & Sweden direct (lowest physical latency ~30–45 ms)
    if not is_relay and any(k in n for k in ["🇫🇮", "финлянди", "finland", "🇸🇪", "швеци", "sweden"]):
        return 1

    # Tier 2: Transit bridges & DPI bypasses (Instant failover for Mobile LTE under TSPU / white-lists)
    if is_relay:
        return 2

    # Tier 3: Core Near-EU (Germany, Netherlands, Estonia, Poland, Latvia, Lithuania)
    if any(k in n for k in [
        "🇩🇪", "германи", "germany", "frankfurt", "франкфурт",
        "🇳🇱", "нидерланд", "netherlands", "holland", "амстердам", "amsterdam",
        "🇪🇪", "эстони", "estonia", "таллин", "tallinn",
        "🇵🇱", "польш", "poland", "варшав", "warsaw",
        "🇱🇻", "латви", "latvia", "рига", "riga",
        "🇱🇹", "литв", "lithuania", "вильнюс", "vilnius"
    ]):
        return 3

    # Tier 4: Other Europe / Regional (UK, France, Czechia, Turkey, Kazakhstan, Austria, Switzerland, Italy, Spain)
    if any(k in n for k in [
        "🇬🇧", "united kingdom", "великобритан", "англия", "лондон", "london", "uk",
        "🇫🇷", "франци", "france", "париж", "paris",
        "🇨🇿", "чехи", "czechia", "czech", "прага", "prague",
        "🇹🇷", "турци", "türkiye", "turkey", "стамбул", "istanbul",
        "🇰🇿", "казахстан", "kazakhstan", "алматы", "almaty", "астана", "astana",
        "🇦🇹", "австри", "austria", "вена", "vienna",
        "🇨🇭", "швейцари", "switzerland", "цюрих", "zurich",
        "🇮🇹", "итали", "italy", "рим", "milan", "милан",
        "🇪🇸", "испани", "spain", "мадрид", "madrid"
    ]):
        return 4

    # Tier 5: USA / Americas
    if any(k in n for k in ["🇺🇸", "сша", "usa", "united states", "america"]):
        return 5

    return 6

def ai_priority(name):
    """
    Priority sorting for AI Services (US, DE, NL, UK, SE, FI).
    """
    n = name.lower()
    if any(c in n for c in ["сша", "usa", "united states", "america"]):
        return 1
    if any(c in n for c in ["германия", "germany", "нидерланды", "netherlands", "великобритания", "united kingdom", "uk"]):
        return 2
    if any(c in n for c in ["швеция", "финляндия", "sweden", "finland"]):
        return 3
    if any(c in n for c in ["moscow", "🇷🇺", "россия", "russia"]):
        return 99
    return 10

def build_mihomo_config(unique_proxies, user_options=None):
    if user_options is None:
        user_options = {}

    proxy_names = [p["name"] for p in unique_proxies]
    fallback_proxies = sorted(proxy_names, key=fallback_priority)

    # Dedicated list for Mobile LTE / DPI bypass (Relays & Bypasses first)
    relays_and_bypasses = [p for p in fallback_proxies if is_relay_or_bypass(p.lower())]
    mobile_proxies = (relays_and_bypasses + [p for p in fallback_proxies if p not in relays_and_bypasses]) if relays_and_bypasses else fallback_proxies

    # Priority sorting for AI Services
    ai_proxies = sorted([p for p in proxy_names if ai_priority(p) < 90], key=ai_priority)
    if not ai_proxies:
        ai_proxies = fallback_proxies

    opts = user_options

    final_config = {
        "mixed-port": opts.get("mixed_port", 7890),
        "socks-port": opts.get("socks_port", 7891),
        "redir-port": opts.get("redir_port", 7892),
        "allow-lan": opts.get("allow_lan", True),
        "mode": opts.get("mode", "rule"),
        "log-level": opts.get("log_level", "info"),
        "ipv6": False,
        "tcp-concurrent": True,
        "keep-alive-interval": 15,
        "external-controller": "127.0.0.1:9090",
        "tun": {
            "enable": True,
            "stack": "mixed",
            "dns-hijack": ["any:53", "tcp://any:53"],
            "auto-route": True,
            "auto-detect-interface": True,
            "strict-route": True,
            "endpoint-independent-nat": True,
            "mtu": 1420
        },
        "dns": {
            "enable": True,
            "use-hosts": True,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "default-nameserver": ["77.88.8.8", "1.1.1.1"],
            "nameserver": [
                "https://dns.google/dns-query",
                "https://common.dot.dns.yandex.net/dns-query",
                "77.88.8.8",
                "1.1.1.1"
            ],
            "nameserver-policy": {
                "+.ru": "77.88.8.8",
                "+.su": "77.88.8.8",
                "+.xn--p1ai": "77.88.8.8",
                "+.yandex.net": "77.88.8.8",
                "+.vk.com": "77.88.8.8",
                "+.gosuslugi.ru": "77.88.8.8",
                "+.sberbank.ru": "77.88.8.8",
                "+.tbank.ru": "77.88.8.8"
            },
            "proxy-server-nameserver": [
                "77.88.8.8",
                "1.1.1.1"
            ],
            "fake-ip-filter": [
                "*.lan", "*.local", "localhost", "time.*", "ntp.*",
                "+.pool.ntp.org", "stun.*", "*.msftconnecttest.com", "*.msftncsi.com",
                "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
                "clients3.google.com", "+.clients.google.com", "*.push.apple.com"
            ]
        },
        "proxies": unique_proxies,
        "proxy-groups": [
            {
                "name": "PROXY",
                "type": "select",
                "proxies": [
                    "Auto-Fallback",
                    "🛡️ Mobile-Bypass",
                    "Auto-UrlTest",
                    "🤖 AI-Services",
                    "🎬 Media-Streaming",
                    "💬 Discord",
                    "✈️ Telegram",
                    "🎯 Games"
                ] + fallback_proxies
            },
            {
                "name": "Auto-Fallback",
                "type": "fallback",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 20,
                "timeout": 2500,
                "lazy": False,
                "max-failed-times": 2,
                "proxies": fallback_proxies
            },
            {
                "name": "🛡️ Mobile-Bypass",
                "type": "fallback",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 20,
                "timeout": 2500,
                "lazy": False,
                "max-failed-times": 2,
                "proxies": mobile_proxies
            },
            {
                "name": "Auto-UrlTest",
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 30,
                "timeout": 2500,
                "tolerance": 50,
                "lazy": False,
                "proxies": fallback_proxies
            },
            {
                "name": "🤖 AI-Services",
                "type": "select",
                "proxies": [
                    "Auto-AI-Fallback",
                    "Auto-AI-UrlTest"
                ] + ai_proxies
            },
            {
                "name": "Auto-AI-Fallback",
                "type": "fallback",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 20,
                "timeout": 2500,
                "lazy": False,
                "max-failed-times": 2,
                "proxies": ai_proxies
            },
            {
                "name": "Auto-AI-UrlTest",
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 30,
                "timeout": 2500,
                "tolerance": 50,
                "lazy": False,
                "proxies": ai_proxies
            },
            {
                "name": "🎬 Media-Streaming",
                "type": "select",
                "proxies": [
                    "Auto-Media-Fallback",
                    "Auto-Media-UrlTest"
                ] + fallback_proxies
            },
            {
                "name": "Auto-Media-Fallback",
                "type": "fallback",
                "url": "https://www.youtube.com/generate_204",
                "interval": 20,
                "timeout": 2500,
                "lazy": False,
                "max-failed-times": 2,
                "proxies": fallback_proxies
            },
            {
                "name": "Auto-Media-UrlTest",
                "type": "url-test",
                "url": "https://www.youtube.com/generate_204",
                "interval": 30,
                "timeout": 2500,
                "tolerance": 50,
                "lazy": False,
                "proxies": fallback_proxies
            },
            {
                "name": "💬 Discord",
                "type": "select",
                "proxies": [
                    "Auto-Fallback",
                    "Auto-Discord-UrlTest"
                ] + fallback_proxies
            },
            {
                "name": "Auto-Discord-UrlTest",
                "type": "url-test",
                "url": "https://discord.com",
                "interval": 30,
                "timeout": 2500,
                "tolerance": 50,
                "lazy": False,
                "proxies": fallback_proxies
            },
            {
                "name": "✈️ Telegram",
                "type": "select",
                "proxies": [
                    "Auto-Fallback",
                    "Auto-UrlTest"
                ] + fallback_proxies
            },
            {
                "name": "🎯 Games",
                "type": "select",
                "proxies": [
                    "DIRECT",
                    "Auto-Fallback"
                ] + fallback_proxies
            }
        ],
        "rules": [
            # 1. Local & Loopback
            "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
            "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
            "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
            "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
            "DOMAIN,localhost,DIRECT",
            "GEOIP,private,DIRECT,no-resolve",
            "GEOSITE,private,DIRECT,no-resolve",
            
            # Android Connectivity & System Direct
            "DOMAIN,clients3.google.com,DIRECT",
            "DOMAIN,connectivitycheck.gstatic.com,DIRECT",
            "DOMAIN,connectivitycheck.android.com,DIRECT",
            "DOMAIN-SUFFIX,gvt1.com,DIRECT",
            "DOMAIN-SUFFIX,gvt2.com,DIRECT",
            "DOMAIN-SUFFIX,push.apple.com,DIRECT",
            
            # 2. Games (Steam, Epic, Riot, Blizzard, EA) - DIRECT by default
            "GEOSITE,steam,🎯 Games",
            "GEOSITE,epicgames,🎯 Games",
            "GEOSITE,riot,🎯 Games",
            "GEOSITE,blizzard,🎯 Games",
            "GEOSITE,ea,🎯 Games",
            "DOMAIN-SUFFIX,steampowered.com,🎯 Games",
            "DOMAIN-SUFFIX,steamcommunity.com,🎯 Games",
            "DOMAIN-SUFFIX,steamserver.net,🎯 Games",
            "DOMAIN-SUFFIX,epicgames.com,🎯 Games",
            "DOMAIN-SUFFIX,riotgames.com,🎯 Games",
            "DOMAIN-SUFFIX,leagueoflegends.com,🎯 Games",
            "DOMAIN-SUFFIX,ea.com,🎯 Games",
            "DOMAIN-SUFFIX,battle.net,🎯 Games",
            "DOMAIN-SUFFIX,blizzard.com,🎯 Games",
            
            # 3. Telegram
            "GEOSITE,telegram,✈️ Telegram",
            "DOMAIN-SUFFIX,t.me,✈️ Telegram",
            "DOMAIN-SUFFIX,telegram.org,✈️ Telegram",
            "DOMAIN-SUFFIX,telegram.me,✈️ Telegram",
            "DOMAIN-SUFFIX,tdesktop.com,✈️ Telegram",
            "DOMAIN-SUFFIX,telegra.ph,✈️ Telegram",
            "GEOIP,telegram,✈️ Telegram",
            "IP-CIDR,91.108.4.0/22,✈️ Telegram,no-resolve",
            "IP-CIDR,91.108.8.0/22,✈️ Telegram,no-resolve",
            "IP-CIDR,91.108.12.0/22,✈️ Telegram,no-resolve",
            "IP-CIDR,91.108.16.0/22,✈️ Telegram,no-resolve",
            "IP-CIDR,91.108.20.0/22,✈️ Telegram,no-resolve",
            "IP-CIDR,91.108.56.0/22,✈️ Telegram,no-resolve",
            "IP-CIDR,149.154.160.0/20,✈️ Telegram,no-resolve",
            "IP-CIDR,185.76.151.0/24,✈️ Telegram,no-resolve",
            
            # 4. Discord
            "GEOSITE,discord,💬 Discord",
            "DOMAIN-SUFFIX,discord.com,💬 Discord",
            "DOMAIN-SUFFIX,discord.gg,💬 Discord",
            "DOMAIN-SUFFIX,discord.media,💬 Discord",
            "DOMAIN-SUFFIX,discordapp.com,💬 Discord",
            "DOMAIN-SUFFIX,discordapp.net,💬 Discord",
            
            # 5. Media & Streaming
            "DOMAIN-SUFFIX,googlevideo.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,youtube.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,ytimg.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,youtu.be,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,soundcloud.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,sndcdn.com,🎬 Media-Streaming",
            
            # 6. AI Services & Google Ecosystem
            "DOMAIN-SUFFIX,gemini.google.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,generativelanguage.googleapis.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,aistudio.google.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,deepmind.google,🤖 AI-Services",
            "DOMAIN-SUFFIX,deepmind.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,proactivebackend-pa.googleapis.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,alkalimakersuite-pa.googleapis.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,google.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,googleapis.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,gstatic.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,googleusercontent.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,anthropic.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,claude.ai,🤖 AI-Services",
            "DOMAIN-SUFFIX,openai.com,🤖 AI-Services",
            "DOMAIN-SUFFIX,chatgpt.com,🤖 AI-Services",
            "GEOSITE,google-gemini,🤖 AI-Services",
            "GEOSITE,openai,🤖 AI-Services",
            "GEOSITE,anthropic,🤖 AI-Services",
            
            # 7. Russian Services (Direct)
            "DOMAIN-SUFFIX,ru,DIRECT",
            "DOMAIN-SUFFIX,su,DIRECT",
            "DOMAIN-SUFFIX,xn--p1ai,DIRECT",
            "DOMAIN-SUFFIX,yandex.ru,DIRECT",
            "DOMAIN-SUFFIX,vk.com,DIRECT",
            "DOMAIN-SUFFIX,sberbank.ru,DIRECT",
            "DOMAIN-SUFFIX,tbank.ru,DIRECT",
            "DOMAIN-SUFFIX,gosuslugi.ru,DIRECT",
            "GEOIP,RU,DIRECT",
            
            # 8. Match All Other
            "MATCH,PROXY"
        ]
    }
    return final_config

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    user_config = {}
    subs = []
    
    # 1. Check SUBSCRIPTIONS environment variable (ideal for GitHub Actions / Docker)
    env_subs = os.getenv("SUBSCRIPTIONS", "").strip()
    if env_subs:
        print("[*] Detected SUBSCRIPTIONS environment variable.")
        # Try JSON first
        if env_subs.startswith("{") or env_subs.startswith("["):
            try:
                parsed = json.loads(env_subs)
                if isinstance(parsed, dict):
                    user_config = parsed
                    subs = user_config.get("subscriptions", [])
                elif isinstance(parsed, list):
                    subs = [{"name": f"Sub-{i+1}", "url": u} if isinstance(u, str) else u for i, u in enumerate(parsed)]
            except Exception:
                pass
                
        # Parse line by line
        if not subs:
            for idx, line in enumerate(env_subs.splitlines(), start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name = ""
                url = line
                if "=" in line:
                    name, url = [x.strip() for x in line.split("=", 1)]
                elif "|" in line:
                    name, url = [x.strip() for x in line.split("|", 1)]
                
                if not name:
                    try:
                        netloc = urllib.parse.urlparse(url).netloc
                        name = netloc.split(":")[0] or f"Sub-{idx}"
                    except Exception:
                        name = f"Sub-{idx}"
                        
                subs.append({
                    "name": name,
                    "url": url,
                    "headers": {
                        "User-Agent": "Clash-verge/1.7.7",
                        "x-hwid": "e60058b76ce8305c486e9e421a91cfc2"
                    },
                    "use_cookies": True
                })
    
    # 2. If no env variable, load config.json
    if not subs:
        if not os.path.exists(config_path):
            print(f"[!] config.json not found in {script_dir}!")
            print("    Please copy config.example.json to config.json or set SUBSCRIPTIONS env variable.")
            return 1
            
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        subs = user_config.get("subscriptions", [])
        
    if not subs:
        print("[!] No subscriptions defined. Aborting.")
        return 1

    # High-Performance Parallel Fetch across providers with 15-second total timeout
    all_proxies = fetch_all_subscriptions(subs, timeout=15)
            
    if not all_proxies:
        print("[ERROR] No proxies could be extracted from any provider. Aborting.")
        return 1

    # Filter out auto-select pseudo-nodes, stubs, and pure Russian nodes
    clean_proxies = []
    dropped_count = 0
    for p in all_proxies:
        if is_junk_or_auto(p):
            dropped_count += 1
        else:
            clean_proxies.append(p)
            
    print(f"[*] Filtered out {dropped_count} junk/auto/RU nodes. Remaining active: {len(clean_proxies)}")
        
    # Deduplicate proxies by (server, port, uuid) fingerprint and ensure unique names
    unique_proxies = deduplicate_proxies(clean_proxies)
    print(f"[INFO] Total active unique proxies assembled: {len(unique_proxies)}")

    opts = user_config.get("options", {})
    final_config = build_mihomo_config(unique_proxies, user_options=opts)
    
    env_out = os.getenv("OUTPUT_FILE", "").strip()
    if env_out:
        out_files = [env_out]
    else:
        out_files = opts.get("output_files", ["./3-in-1_VPN.yaml"])
        
    for out in out_files:
        expanded = os.path.expanduser(os.path.expandvars(out))
        out_dir = os.path.dirname(os.path.abspath(expanded))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        print(f"[INFO] Writing config to: {expanded}")
        with open(expanded, "w", encoding="utf-8") as f:
            yaml.dump(final_config, f, Dumper=NoAliasDumper, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2)
            
    print("\n" + "="*50)
    print(f"SUCCESS: Aegis synced {len(subs)} subscriptions! Total proxies: {len(unique_proxies)}")
    print("="*50)
    return 0

if __name__ == "__main__":
    import argparse
    import time
    
    parser = argparse.ArgumentParser(description="Aegis — Smart Routing & Multi-Subscription Merger")
    parser.add_argument("--interval", type=float, default=0, help="Run repeatedly every N hours (e.g. --interval 1 for hourly sync)")
    args = parser.parse_args()

    if args.interval > 0:
        interval_secs = int(args.interval * 3600)
        print(f"[*] Starting Aegis auto-sync daemon (every {args.interval} hour(s))...")
        while True:
            try:
                main()
            except Exception as e:
                print(f"[!] Error during scheduled sync: {e}")
            print(f"[*] Next sync in {args.interval} hour(s). Waiting...")
            time.sleep(interval_secs)
    else:
        sys.exit(main())
