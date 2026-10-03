# -*- coding: utf-8 -*-
"""
Mihomo (Clash.Meta) Smart Routing & Multi-Subscription Merger
Merges multiple VPN subscriptions (supporting HWID & Anti-Bot cookies) into a single,
clean Mihomo config with smart routing for AI, Media, Discord, Games, and Russian services.
"""

import os
import sys
import json
import base64
import subprocess
import urllib.parse
import urllib.request
import yaml

class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

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
    url = sub_config.get("url")
    headers = sub_config.get("headers", {})
    use_cookies = sub_config.get("use_cookies", False)
    
    print(f"[*] Fetching '{name}'...")
    
    # Use curl.exe for maximum compatibility with TLS renegotiation and cookies
    cmd = ["curl.exe", "-s", "-L", "--max-time", "15"]
    
    if use_cookies:
        cookie_file = f"{name}_cookies.txt"
        cmd.extend(["-b", cookie_file, "-c", cookie_file])
        
    for h_key, h_val in headers.items():
        cmd.extend(["-H", f"{h_key}: {h_val}"])
        
    cmd.append(url)
    
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not res.stdout:
        print(f"  [!] Retrying fetch for '{name}'...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
    raw = res.stdout.strip()
    
    # Try YAML first (if subscription returns clash config directly)
    try:
        data = yaml.safe_load(raw.decode("utf-8", errors="ignore"))
        if isinstance(data, dict) and "proxies" in data:
            proxies = data["proxies"]
            for p in proxies:
                p["name"] = f"[{name}] {p['name']}"
            print(f"  -> Got {len(proxies)} proxies (YAML) from '{name}'")
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
                
    print(f"  -> Got {len(proxies)} proxies (URIs) from '{name}'")
    return proxies

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    
    if not os.path.exists(config_path):
        print(f"[!] config.json not found in {script_dir}!")
        print("    Please copy config.example.json to config.json and fill in your subscription links.")
        return 1
        
    with open(config_path, "r", encoding="utf-8") as f:
        user_config = json.load(f)
        
    subs = user_config.get("subscriptions", [])
    if not subs:
        print("[!] No subscriptions defined in config.json.")
        return 1
        
    all_proxies = []
    for sub in subs:
        try:
            proxies = fetch_subscription(sub)
            all_proxies.extend(proxies)
        except Exception as e:
            print(f"  [!] Failed to fetch {sub.get('name')}: {e}")
            
    if not all_proxies:
        print("[ERROR] No proxies could be extracted. Aborting.")
        return 1
        
    # Deduplicate proxy names
    seen = set()
    unique_proxies = []
    for p in all_proxies:
        name = p["name"]
        counter = 1
        orig_name = name
        while name in seen:
            counter += 1
            name = f"{orig_name} #{counter}"
        p["name"] = name
        seen.add(name)
        unique_proxies.append(p)
        
    proxy_names = [p["name"] for p in unique_proxies]
    print(f"\n[INFO] Total active unique proxies assembled: {len(unique_proxies)}")
    
    # Priority sorting for AI Services (US, DE, NL, UK, SE, FI)
    def ai_priority(name):
        n = name.lower()
        if any(c in n for c in ["сша", "usa", "united states"]):
            return 1
        if any(c in n for c in ["германия", "germany", "нидерланды", "netherlands", "великобритания", "united kingdom"]):
            return 2
        if any(c in n for c in ["швеция", "финляндия", "sweden", "finland"]):
            return 3
        if any(c in n for c in ["moscow", "🇷🇺", "россия", "russia"]):
            return 99
        return 10

    ai_proxies = sorted([p for p in proxy_names if ai_priority(p) < 90], key=ai_priority)
    if not ai_proxies:
        ai_proxies = proxy_names

    opts = user_config.get("options", {})
    
    final_config = {
        "mixed-port": opts.get("mixed_port", 7890),
        "socks-port": opts.get("socks_port", 7891),
        "redir-port": opts.get("redir_port", 7892),
        "allow-lan": opts.get("allow_lan", True),
        "mode": opts.get("mode", "rule"),
        "log-level": opts.get("log_level", "info"),
        "ipv6": False,
        "external-controller": "127.0.0.1:9090",
        "dns": {
            "enable": True,
            "use-hosts": True,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "default-nameserver": ["77.88.8.8", "8.8.8.8"],
            "nameserver": ["77.88.8.8", "8.8.8.8"],
            "fake-ip-filter": [
                "*.lan", "*.local", "localhost", "time.*", "ntp.*",
                "+.pool.ntp.org", "stun.*", "*.msftconnecttest.com", "*.msftncsi.com"
            ]
        },
        "proxies": unique_proxies,
        "proxy-groups": [
            {
                "name": "PROXY",
                "type": "select",
                "proxies": [
                    "Auto-UrlTest",
                    "Auto-Fallback",
                    "🤖 AI-Services",
                    "🎬 Media-Streaming",
                    "💬 Discord",
                    "🎯 Games"
                ] + proxy_names
            },
            {
                "name": "Auto-UrlTest",
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
                "proxies": proxy_names
            },
            {
                "name": "Auto-Fallback",
                "type": "fallback",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
                "proxies": proxy_names
            },
            {
                "name": "🤖 AI-Services",
                "type": "select",
                "proxies": [
                    "Auto-AI-UrlTest",
                    "Auto-AI-Fallback"
                ] + ai_proxies
            },
            {
                "name": "Auto-AI-UrlTest",
                "type": "url-test",
                "url": "https://generativelanguage.googleapis.com",
                "interval": 300,
                "tolerance": 50,
                "proxies": ai_proxies
            },
            {
                "name": "Auto-AI-Fallback",
                "type": "fallback",
                "url": "https://generativelanguage.googleapis.com",
                "interval": 300,
                "proxies": ai_proxies
            },
            {
                "name": "🎬 Media-Streaming",
                "type": "select",
                "proxies": [
                    "Auto-Media-UrlTest",
                    "Auto-Media-Fallback"
                ] + proxy_names
            },
            {
                "name": "Auto-Media-UrlTest",
                "type": "url-test",
                "url": "https://www.youtube.com/generate_204",
                "interval": 300,
                "tolerance": 50,
                "proxies": proxy_names
            },
            {
                "name": "Auto-Media-Fallback",
                "type": "fallback",
                "url": "https://www.youtube.com/generate_204",
                "interval": 300,
                "proxies": proxy_names
            },
            {
                "name": "💬 Discord",
                "type": "select",
                "proxies": [
                    "Auto-Discord-UrlTest",
                    "Auto-UrlTest"
                ] + proxy_names
            },
            {
                "name": "Auto-Discord-UrlTest",
                "type": "url-test",
                "url": "https://discord.com",
                "interval": 300,
                "tolerance": 50,
                "proxies": proxy_names
            },
            {
                "name": "🎯 Games",
                "type": "select",
                "proxies": [
                    "DIRECT",
                    "Auto-UrlTest"
                ] + proxy_names
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
            
            # 3. Discord
            "GEOSITE,discord,💬 Discord",
            "DOMAIN-SUFFIX,discord.com,💬 Discord",
            "DOMAIN-SUFFIX,discord.gg,💬 Discord",
            "DOMAIN-SUFFIX,discord.media,💬 Discord",
            "DOMAIN-SUFFIX,discordapp.com,💬 Discord",
            "DOMAIN-SUFFIX,discordapp.net,💬 Discord",
            
            # 4. Media & Streaming
            "DOMAIN-SUFFIX,googlevideo.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,youtube.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,ytimg.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,youtu.be,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,soundcloud.com,🎬 Media-Streaming",
            "DOMAIN-SUFFIX,sndcdn.com,🎬 Media-Streaming",
            
            # 5. AI Services & Google Ecosystem
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
            
            # 6. Russian Services (Direct)
            "DOMAIN-SUFFIX,ru,DIRECT",
            "DOMAIN-SUFFIX,su,DIRECT",
            "DOMAIN-SUFFIX,xn--p1ai,DIRECT",
            "DOMAIN-SUFFIX,yandex.ru,DIRECT",
            "DOMAIN-SUFFIX,vk.com,DIRECT",
            "DOMAIN-SUFFIX,sberbank.ru,DIRECT",
            "DOMAIN-SUFFIX,tbank.ru,DIRECT",
            "DOMAIN-SUFFIX,gosuslugi.ru,DIRECT",
            "GEOIP,RU,DIRECT",
            
            # 7. Match All Other
            "MATCH,PROXY"
        ]
    }
    
    out_files = opts.get("output_files", ["./3-in-1_VPN.yaml"])
    for out in out_files:
        expanded = os.path.expanduser(os.path.expandvars(out))
        os.makedirs(os.path.dirname(os.path.abspath(expanded)), exist_ok=True)
        print(f"[INFO] Writing config to: {expanded}")
        with open(expanded, "w", encoding="utf-8") as f:
            yaml.dump(final_config, f, Dumper=NoAliasDumper, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2)
            
    print("\n" + "="*50)
    print(f"SUCCESS: Synced {len(subs)} subscriptions! Total proxies: {len(unique_proxies)}")
    print("="*50)
    return 0

if __name__ == "__main__":
    sys.exit(main())
