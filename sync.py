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
import re

class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

def _sanitize_error(e):
    """
    Sanitizes exception messages to prevent leaking subscription tokens or credentials.
    """
    msg = str(e)
    # Redact query parameters in URLs: ?token=secret -> ?token=***
    msg = re.sub(r"([?&][a-zA-Z0-9_-]+=)[^&\s'\"]+", r"\1***", msg)
    # Redact user credentials in URLs/URIs: ://user:pass@ -> ://***:***@
    msg = re.sub(r"://([^:]+:[^@]+)@", r"://***:***@", msg)
    return msg

def _mask_token_in_ci(val):
    if not val:
        return
    val_str = str(val).strip()
    if os.getenv("GITHUB_ACTIONS") == "true" and len(val_str) > 4:
        print(f"::add-mask::{val_str}", flush=True)
        # If URL, also mask isolated query parameter values, path segments, and credentials
        if "://" in val_str:
            try:
                parsed = urllib.parse.urlparse(val_str)
                if parsed.query:
                    for _, v in urllib.parse.parse_qsl(parsed.query):
                        if len(v) > 4:
                            print(f"::add-mask::{v}", flush=True)
                for part in parsed.path.split("/"):
                    if len(part) > 8:
                        print(f"::add-mask::{part}", flush=True)
                if parsed.username and len(parsed.username) > 4:
                    print(f"::add-mask::{parsed.username}", flush=True)
                if parsed.password and len(parsed.password) > 4:
                    print(f"::add-mask::{parsed.password}", flush=True)
            except Exception:
                pass

def _mask_proxies_in_ci(proxies):
    if os.getenv("GITHUB_ACTIONS") == "true":
        for p in proxies:
            if not isinstance(p, dict):
                continue
            for k in ["uuid", "password"]:
                val = p.get(k)
                if val:
                    _mask_token_in_ci(val)
            ropts = p.get("reality-opts")
            if isinstance(ropts, dict):
                pbk = ropts.get("public-key")
                if pbk:
                    _mask_token_in_ci(pbk)

def _parse_host_port(host_port_raw, default_port=443):
    """
    Robust host and port parsing handling IPv6 brackets, trailing slashes/paths, and port fallbacks.
    """
    host_port = host_port_raw.strip()
    if "/" in host_port:
        host_port = host_port.split("/", 1)[0].strip()

    if host_port.startswith("["):
        closing_idx = host_port.find("]")
        if closing_idx != -1:
            host = host_port[1:closing_idx].strip()
            rem = host_port[closing_idx+1:]
            try:
                port = int(rem[1:]) if rem.startswith(":") else default_port
            except ValueError:
                port = default_port
        else:
            host = host_port.strip("[]")
            port = default_port
    elif host_port.count(":") > 1:
        host = host_port
        port = default_port
    elif ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = default_port
    else:
        host = host_port
        port = default_port
    return host, port

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

        if "@" not in rest:
            return None

        user_info, host_port_query = rest.split("@", 1)
        uuid = urllib.parse.unquote(user_info).strip()
        if not uuid:
            return None

        if "?" in host_port_query:
            host_port_raw, query = host_port_query.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query))
        else:
            host_port_raw = host_port_query
            params = {}

        host, port = _parse_host_port(host_port_raw, default_port=443)
        if not host:
            return None

        proxy = {
            "name": name,
            "type": "vless",
            "server": host,
            "port": port,
            "uuid": uuid,
            "udp": True,
            "packet-encoding": "xudp"
        }

        security = params.get("security", "").lower()
        is_tls = security in ["tls", "reality"] or params.get("tls") in ["1", "true", "tls"]
        if is_tls:
            proxy["tls"] = True
            sni = params.get("sni") or params.get("servername") or host
            proxy["servername"] = sni
            fp = params.get("fp") or params.get("client-fingerprint") or "chrome"
            proxy["client-fingerprint"] = fp

            if security == "reality":
                ropts = {}
                pbk = params.get("pbk") or params.get("public-key")
                if pbk:
                    ropts["public-key"] = pbk
                sid = params.get("sid") or params.get("short-id")
                if sid:
                    ropts["short-id"] = sid
                spx = params.get("spx") or params.get("spider-x")
                if spx:
                    ropts["spider-x"] = spx
                if ropts.get("public-key"):
                    proxy["reality-opts"] = ropts

        if params.get("allowInsecure") == "1" or params.get("insecure") == "1":
            proxy["skip-cert-verify"] = True

        if "alpn" in params:
            alpn = [x.strip() for x in params["alpn"].split(",") if x.strip()]
            if alpn:
                proxy["alpn"] = alpn

        flow = params.get("flow")
        if flow:
            proxy["flow"] = flow

        net = params.get("type", "tcp").lower()
        proxy["network"] = net

        if net == "ws":
            ws_opts = {}
            if "path" in params: ws_opts["path"] = urllib.parse.unquote(params["path"])
            if "host" in params: ws_opts["headers"] = {"Host": params["host"]}
            if ws_opts: proxy["ws-opts"] = ws_opts
        elif net == "grpc":
            grpc_opts = {}
            sn = params.get("serviceName") or params.get("service_name") or params.get("service-name") or params.get("sn")
            if sn: grpc_opts["grpc-service-name"] = sn
            if grpc_opts: proxy["grpc-opts"] = grpc_opts
        elif net in ["xhttp", "splithttp"]:
            proxy["network"] = "xhttp"
            xhttp_opts = {}
            if "path" in params: xhttp_opts["path"] = urllib.parse.unquote(params["path"])
            if "mode" in params: xhttp_opts["mode"] = params["mode"]
            if xhttp_opts: proxy["xhttp-opts"] = xhttp_opts

        return proxy
    except Exception as e:
        print(f"Error parsing VLESS URI: {_sanitize_error(e)}")
        return None

def parse_trojan_uri(uri, prefix=""):
    if not uri.startswith("trojan://"):
        return None
    try:
        rest = uri[len("trojan://"):]
        if "#" in rest:
            rest, name = rest.split("#", 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = "Unnamed"

        if prefix:
            name = f"[{prefix}] {name}"

        if "@" not in rest:
            return None

        password_raw, host_port_query = rest.split("@", 1)
        password = urllib.parse.unquote(password_raw).strip()
        if not password:
            return None

        if "?" in host_port_query:
            host_port_raw, query = host_port_query.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query))
        else:
            host_port_raw = host_port_query
            params = {}

        host, port = _parse_host_port(host_port_raw, default_port=443)
        if not host:
            return None

        sni = params.get("sni") or params.get("peer") or params.get("servername") or host
        proxy = {
            "name": name,
            "type": "trojan",
            "server": host,
            "port": port,
            "password": password,
            "udp": True,
            "sni": sni
        }
        if params.get("allowInsecure") == "1" or params.get("insecure") == "1":
            proxy["skip-cert-verify"] = True
        if "alpn" in params:
            alpn = [x.strip() for x in params["alpn"].split(",") if x.strip()]
            if alpn:
                proxy["alpn"] = alpn
        net = params.get("type", "tcp").lower()
        if net == "ws":
            proxy["network"] = "ws"
            ws_opts = {}
            if "path" in params: ws_opts["path"] = urllib.parse.unquote(params["path"])
            if "host" in params: ws_opts["headers"] = {"Host": params["host"]}
            if ws_opts: proxy["ws-opts"] = ws_opts
        elif net == "grpc":
            proxy["network"] = "grpc"
            sn = params.get("serviceName") or params.get("service_name") or params.get("service-name") or params.get("sn")
            if sn: proxy["grpc-opts"] = {"grpc-service-name": sn}
        return proxy
    except Exception as e:
        print(f"Error parsing Trojan URI: {_sanitize_error(e)}")
        return None

def parse_ss_uri(uri, prefix=""):
    if not uri.startswith("ss://"):
        return None
    try:
        rest = uri[len("ss://"):]
        if "#" in rest:
            rest, name = rest.split("#", 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = "Unnamed"

        if prefix:
            name = f"[{prefix}] {name}"

        cipher = ""
        password = ""
        host = ""
        port = 8388

        if "@" in rest:
            # SIP002 format: ss://base64(method:password)@hostname:port
            user_info, host_port_part = rest.split("@", 1)
            if "?" in host_port_part:
                host_port_raw = host_port_part.split("?", 1)[0]
            else:
                host_port_raw = host_port_part
            host, port = _parse_host_port(host_port_raw, default_port=8388)

            if ":" in user_info:
                cipher, password = user_info.split(":", 1)
            else:
                try:
                    decoded_ui = safe_b64decode(user_info).decode("utf-8", errors="ignore")
                    if ":" in decoded_ui:
                        cipher, password = decoded_ui.split(":", 1)
                except Exception:
                    pass
        else:
            # Legacy format: ss://base64(method:password@hostname:port)
            try:
                decoded_all = safe_b64decode(rest).decode("utf-8", errors="ignore")
                if "@" in decoded_all:
                    user_info, host_port_raw = decoded_all.split("@", 1)
                    if ":" in user_info:
                        cipher, password = user_info.split(":", 1)
                    host, port = _parse_host_port(host_port_raw, default_port=8388)
            except Exception:
                pass

        if not host or not cipher or not password:
            return None

        return {
            "name": name,
            "type": "ss",
            "server": host,
            "port": port,
            "cipher": cipher.strip(),
            "password": password.strip(),
            "udp": True
        }
    except Exception as e:
        print(f"Error parsing Shadowsocks URI: {_sanitize_error(e)}")
        return None

def parse_vmess_uri(uri, prefix=""):
    if not uri.startswith("vmess://"):
        return None
    try:
        raw_b64 = uri[len("vmess://"):].strip()
        decoded_json = safe_b64decode(raw_b64).decode("utf-8", errors="ignore")
        obj = json.loads(decoded_json)

        name = str(obj.get("ps", "Unnamed")).strip()
        if prefix:
            name = f"[{prefix}] {name}"

        host = str(obj.get("add", "")).strip().strip("[]")
        try:
            port = int(obj.get("port", 443))
        except (ValueError, TypeError):
            port = 443

        uuid = str(obj.get("id", "")).strip()
        if not host or not uuid:
            return None

        try:
            aid = int(obj.get("aid", 0))
        except (ValueError, TypeError):
            aid = 0

        proxy = {
            "name": name,
            "type": "vmess",
            "server": host,
            "port": port,
            "uuid": uuid,
            "alterId": aid,
            "cipher": obj.get("scy", "auto") or "auto",
            "udp": True
        }

        tls_val = str(obj.get("tls", "")).lower()
        if tls_val in ["tls", "1", "true"]:
            proxy["tls"] = True
            sni = obj.get("sni") or obj.get("host")
            if sni:
                proxy["servername"] = sni

        net = str(obj.get("net", "tcp")).lower()
        if net == "ws":
            proxy["network"] = "ws"
            ws_opts = {}
            if obj.get("path"): ws_opts["path"] = obj["path"]
            if obj.get("host"): ws_opts["headers"] = {"Host": obj["host"]}
            if ws_opts: proxy["ws-opts"] = ws_opts
        elif net == "grpc":
            proxy["network"] = "grpc"
            sn = obj.get("path") or obj.get("serviceName")
            if sn: proxy["grpc-opts"] = {"grpc-service-name": sn}

        return proxy
    except Exception as e:
        print(f"Error parsing VMess URI: {_sanitize_error(e)}")
        return None

def parse_hy2_uri(uri, prefix=""):
    if not (uri.startswith("hysteria2://") or uri.startswith("hy2://")):
        return None
    try:
        prefix_len = len("hysteria2://") if uri.startswith("hysteria2://") else len("hy2://")
        rest = uri[prefix_len:]
        if "#" in rest:
            rest, name = rest.split("#", 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = "Unnamed"

        if prefix:
            name = f"[{prefix}] {name}"

        if "@" not in rest:
            return None

        password_raw, host_port_query = rest.split("@", 1)
        password = urllib.parse.unquote(password_raw).strip()
        if not password:
            return None

        if "?" in host_port_query:
            host_port_raw, query = host_port_query.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query))
        else:
            host_port_raw = host_port_query
            params = {}

        host, port = _parse_host_port(host_port_raw, default_port=443)
        if not host:
            return None

        sni = params.get("sni") or host
        proxy = {
            "name": name,
            "type": "hysteria2",
            "server": host,
            "port": port,
            "password": password,
            "sni": sni
        }
        if params.get("insecure") == "1" or params.get("allowInsecure") == "1":
            proxy["skip-cert-verify"] = True
        return proxy
    except Exception as e:
        print(f"Error parsing Hysteria2 URI: {_sanitize_error(e)}")
        return None

def parse_proxy_uri(uri, prefix=""):
    """
    Unified multi-protocol proxy parser for VLESS, Trojan, Shadowsocks, VMess, and Hysteria2.
    """
    u = uri.strip()
    if u.startswith("vless://"):
        return parse_vless_uri(u, prefix=prefix)
    elif u.startswith("trojan://"):
        return parse_trojan_uri(u, prefix=prefix)
    elif u.startswith("ss://"):
        return parse_ss_uri(u, prefix=prefix)
    elif u.startswith("vmess://"):
        return parse_vmess_uri(u, prefix=prefix)
    elif u.startswith("hysteria2://") or u.startswith("hy2://"):
        return parse_hy2_uri(u, prefix=prefix)
    return None

def safe_b64decode(data):
    """
    Safely decodes base64 data, handling missing padding, URL-safe characters, and whitespace.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = data.strip().replace(b"\r", b"").replace(b"\n", b"")
    data = data.replace(b"-", b"+").replace(b"_", b"/")
    missing_padding = len(data) % 4
    if missing_padding:
        data += b"=" * (4 - missing_padding)
    return base64.b64decode(data)

def fetch_subscription(sub_config, timeout=10):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    name = sub_config.get("name", "VPN")
    url = sub_config.get("url", "")
    headers = dict(sub_config.get("headers", {}))
    use_cookies = sub_config.get("use_cookies", False)

    if not url:
        print(f"  [!] Missing URL for provider '{name}'.")
        return []

    _mask_token_in_ci(url)
    print(f"[*] Fetching '{name}'...")

    # Normalize User-Agent header
    ua_keys = [k for k in list(headers.keys()) if k.lower() == "user-agent"]
    current_ua = headers[ua_keys[0]] if ua_keys else "Clash-verge/1.7.7"
    for k in ua_keys:
        del headers[k]
    headers["User-Agent"] = current_ua

    session = requests.Session()
    cookie_file = os.path.join(script_dir, f"{name}_cookies.txt")
    jar = None
    if use_cookies:
        try:
            jar = http.cookiejar.MozillaCookieJar(cookie_file)
            if os.path.exists(cookie_file):
                jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar
        except Exception as e:
            print(f"  [!] Note: Could not load cookie file '{cookie_file}': {_sanitize_error(e)}")

    # Partition timeout across attempts so total time respects caller's timeout budget
    attempt1_timeout = max(3, int(timeout * 0.6))
    attempt2_timeout = max(3, timeout - attempt1_timeout)

    # Enforce strict TLS validation on outbound requests (prevent MitM attacks)
    try:
        resp = session.get(
            url,
            headers=headers,
            timeout=attempt1_timeout,
            verify=True,
            allow_redirects=True
        )
        resp.raise_for_status()
    except requests.exceptions.SSLError as e:
        print(f"  [!] TLS verification FAILED for '{name}' (possible MitM or invalid cert): {_sanitize_error(e)}")
        raise
    except Exception as e:
        print(f"  [!] Retrying fetch for '{name}' due to error: {_sanitize_error(e)}")
        try:
            resp = session.get(
                url,
                headers=headers,
                timeout=attempt2_timeout,
                verify=True,
                allow_redirects=True
            )
            resp.raise_for_status()
        except requests.exceptions.SSLError as e2:
            print(f"  [!] TLS verification FAILED on retry for '{name}': {_sanitize_error(e2)}")
            raise
        except Exception as e2:
            raise

    if use_cookies and jar is not None:
        try:
            jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass

    raw = resp.content.strip()

    if not raw:
        print(f"  [!] Warning: Provider '{name}' returned an empty response body.")
        return []

    # Check for HTML / anti-bot challenge
    is_html = b"<html" in raw.lower() or b"<!doctype html" in raw.lower()
    if is_html:
        print(f"  [!] Provider '{name}' returned an HTML page (possible Cloudflare / anti-bot challenge).")

    # 1. Try YAML first (if subscription returns clash config directly)
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

    # 2. Check if already plain text containing known proxy URIs
    KNOWN_PROTOCOLS = ("vless://", "vmess://", "trojan://", "ss://", "hysteria2://", "hy2://")
    raw_str = raw.decode("utf-8", errors="ignore")
    decoded = ""
    if any(p in raw_str for p in KNOWN_PROTOCOLS):
        decoded = raw_str
    else:
        # 3. Try Base64 of URIs (with safe unpadded / urlsafe decoder)
        try:
            decoded = safe_b64decode(raw).decode("utf-8", errors="ignore")
        except Exception:
            decoded = raw_str

    proxies = []
    for line in decoded.splitlines():
        line = line.strip()
        if line:
            p = parse_proxy_uri(line, prefix=name)
            if p:
                proxies.append(p)

    if not proxies and headers.get("User-Agent") != "Hiddify/2.0.5" and not is_html:
        print(f"  [i] 0 proxies found with default UA. Retrying '{name}' with Hiddify UA...")
        sub_retry = dict(sub_config)
        sub_retry["headers"] = dict(headers)
        sub_retry["headers"]["User-Agent"] = "Hiddify/2.0.5"
        return fetch_subscription(sub_retry, timeout=max(3, timeout // 2))

    print(f"  -> Got {len(proxies)} proxies (URIs) from '{name}'")
    _mask_proxies_in_ci(proxies)
    return proxies

def fetch_all_subscriptions(subs, timeout=15):
    """
    Fetches subscriptions concurrently using ThreadPoolExecutor within a total timeout.
    Provides zero-downtime resilience: single provider failures do not abort execution.
    Non-blocking timeout: guarantees executor does not hang if individual providers stall.
    """
    if not subs:
        return []

    all_proxies = []
    max_workers = min(len(subs), 10)
    worker_timeout = min(10, timeout)
    print(f"[*] Concurrently fetching {len(subs)} subscription(s) (workers: {max_workers}, timeout: {timeout}s)...")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_sub = {executor.submit(fetch_subscription, sub, timeout=worker_timeout): sub for sub in subs}
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
                print(f"  [!] Warning: Provider '{sub_name}' failed to fetch ({type(e).__name__}: {_sanitize_error(e)}). Continuing with remaining providers.")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return all_proxies

def is_relay_or_bypass(n):
    return any(k in n for k in ["→", "->", "обход", "bypass", "relay"])

def is_junk_or_auto(p):
    name = p.get("name", "").lower()
    server = str(p.get("server", "")).strip().lower()
    
    # 1. Fake or empty servers
    if not server or server in ["127.0.0.1", "0.0.0.0", "localhost"]:
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
    # Exclude relay chains & DPI bypass nodes (e.g. 'москва → германия', '🇷🇺 Обход')
    is_relay = is_relay_or_bypass(name)
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
    server = str(p.get("server", "")).strip().lower().strip("[]")
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
        name = p.get("name", "Unnamed")
        counter = 1
        orig_name = name
        while name in seen_names:
            counter += 1
            name = f"{orig_name} #{counter}"
        p["name"] = name
        seen_names.add(name)
        unique_proxies.append(p)

    return unique_proxies

TIER1_REGEX = re.compile(r"(?:^|[\s\[\]\-_(])(fi|se)(?:[\s\[\]\-_)]|$)", re.IGNORECASE)

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
    if not is_relay:
        if any(k in n for k in [
            "🇫🇮", "финлянди", "finland", "helsinki", "хельсинки", "espoo", "эспоо", "tampere", "тампере",
            "🇸🇪", "швеци", "sweden", "stockholm", "стокгольм", "malmo", "мальмё", "malmö", "gothenburg", "гётеборг"
        ]) or bool(TIER1_REGEX.search(name)):
            return 1

    # Tier 2: Transit bridges & DPI bypasses (Instant failover for Mobile LTE under TSPU / white-lists)
    if is_relay:
        return 2

    # Tier 3: Core Near-EU (Germany, Netherlands, Estonia, Poland, Latvia, Lithuania)
    if any(k in n for k in [
        "🇩🇪", "германи", "germany", "frankfurt", "франкфурт", "berlin", "берлин",
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
    if any(k in n for k in [
        "🇺🇸", "сша", "usa", "united states", "america",
        "new york", "нью-йорк", "los angeles", "лос-анджелес", "chicago", "чикаго",
        "miami", "майами", "seattle", "сиэтл", "dallas", "даллас", "california", "калифорния", "ashburn", "эшберн"
    ]):
        return 5

    return 6

def ai_priority(name):
    """
    Priority sorting for AI Services (US, DE, NL, UK, SE, FI).
    """
    n = name.lower()
    if any(c in n for c in [
        "🇺🇸", "сша", "usa", "united states", "america",
        "new york", "нью-йорк", "los angeles", "лос-анджелес", "chicago", "чикаго", "miami", "майами", "seattle", "сиэтл"
    ]):
        return 1
    if any(c in n for c in [
        "🇩🇪", "германия", "германи", "germany", "frankfurt", "франкфурт",
        "🇳🇱", "нидерланды", "нидерланд", "netherlands", "amsterdam", "амстердам",
        "🇬🇧", "великобритания", "великобритан", "united kingdom", "uk", "london", "лондон"
    ]):
        return 2
    if any(c in n for c in [
        "🇸🇪", "швеция", "швеци", "sweden", "stockholm", "стокгольм",
        "🇫🇮", "финляндия", "финлянди", "finland", "helsinki", "хельсинки"
    ]):
        return 3
    if any(c in n for c in ["moscow", "🇷🇺", "россия", "russia"]) and not is_relay_or_bypass(n):
        return 99
    return 10

DEFAULT_CATEGORIES = [
    {
        "name": "🎯 Games",
        "default_options": ["DIRECT", "Auto-Fallback", "🛡️ Mobile-Bypass", "Auto-UrlTest"],
        "rules": [
            "GEOSITE,steam",
            "GEOSITE,epicgames",
            "GEOSITE,riot",
            "GEOSITE,blizzard",
            "GEOSITE,ea",
            "DOMAIN-SUFFIX,steampowered.com",
            "DOMAIN-SUFFIX,steamcommunity.com",
            "DOMAIN-SUFFIX,steamserver.net",
            "DOMAIN-SUFFIX,epicgames.com",
            "DOMAIN-SUFFIX,riotgames.com",
            "DOMAIN-SUFFIX,leagueoflegends.com",
            "DOMAIN-SUFFIX,ea.com",
            "DOMAIN-SUFFIX,battle.net",
            "DOMAIN-SUFFIX,blizzard.com",
        ]
    },
    {
        "name": "✈️ Telegram",
        "default_options": ["Auto-Fallback", "🛡️ Mobile-Bypass", "Auto-UrlTest", "DIRECT"],
        "rules": [
            "GEOSITE,telegram",
            "DOMAIN-SUFFIX,t.me",
            "DOMAIN-SUFFIX,telegram.org",
            "DOMAIN-SUFFIX,telegram.me",
            "DOMAIN-SUFFIX,tdesktop.com",
            "DOMAIN-SUFFIX,telegra.ph",
            "GEOIP,telegram",
            "IP-CIDR,91.108.4.0/22,no-resolve",
            "IP-CIDR,91.108.8.0/22,no-resolve",
            "IP-CIDR,91.108.12.0/22,no-resolve",
            "IP-CIDR,91.108.16.0/22,no-resolve",
            "IP-CIDR,91.108.20.0/22,no-resolve",
            "IP-CIDR,91.108.56.0/22,no-resolve",
            "IP-CIDR,149.154.160.0/20,no-resolve",
            "IP-CIDR,185.76.151.0/24,no-resolve",
        ]
    },
    {
        "name": "💬 Discord",
        "default_options": ["Auto-Fallback", "🛡️ Mobile-Bypass", "Auto-UrlTest", "DIRECT"],
        "rules": [
            "GEOSITE,discord",
            "DOMAIN-SUFFIX,discord.com",
            "DOMAIN-SUFFIX,discord.gg",
            "DOMAIN-SUFFIX,discord.media",
            "DOMAIN-SUFFIX,discordapp.com",
            "DOMAIN-SUFFIX,discordapp.net",
        ]
    },
    {
        "name": "🎬 Media-Streaming",
        "default_options": ["Auto-Fallback", "🛡️ Mobile-Bypass", "Auto-UrlTest", "DIRECT"],
        "rules": [
            "DOMAIN-SUFFIX,googlevideo.com",
            "DOMAIN-SUFFIX,youtube.com",
            "DOMAIN-SUFFIX,ytimg.com",
            "DOMAIN-SUFFIX,youtu.be",
            "DOMAIN-SUFFIX,soundcloud.com",
            "DOMAIN-SUFFIX,sndcdn.com",
        ]
    },
    {
        "name": "🤖 AI-Services",
        "priority": "ai",
        "default_options": ["Auto-Fallback", "🛡️ Mobile-Bypass", "Auto-UrlTest", "DIRECT"],
        "rules": [
            "DOMAIN-SUFFIX,gemini.google.com",
            "DOMAIN-SUFFIX,generativelanguage.googleapis.com",
            "DOMAIN-SUFFIX,aistudio.google.com",
            "DOMAIN-SUFFIX,deepmind.google",
            "DOMAIN-SUFFIX,deepmind.com",
            "DOMAIN-SUFFIX,proactivebackend-pa.googleapis.com",
            "DOMAIN-SUFFIX,alkalimakersuite-pa.googleapis.com",
            "DOMAIN-SUFFIX,google.com",
            "DOMAIN-SUFFIX,googleapis.com",
            "DOMAIN-SUFFIX,gstatic.com",
            "DOMAIN-SUFFIX,googleusercontent.com",
            "DOMAIN-SUFFIX,anthropic.com",
            "DOMAIN-SUFFIX,claude.ai",
            "DOMAIN-SUFFIX,openai.com",
            "DOMAIN-SUFFIX,chatgpt.com",
            "GEOSITE,google-gemini",
            "GEOSITE,openai",
            "GEOSITE,anthropic",
        ]
    }
]

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

    opts = user_options or {}

    # Adaptive Telemetry Prioritization (Wi-Fi vs Cellular & Geo-block penalization)
    try:
        from telemetry_db import get_prioritized_proxies, detect_current_network_type, recalculate_scores, DEFAULT_DB_PATH
        if os.path.exists(DEFAULT_DB_PATH):
            recalculate_scores(DEFAULT_DB_PATH)
            current_net = opts.get("network_type") or detect_current_network_type()
            fallback_proxies = get_prioritized_proxies(fallback_proxies, network_type=current_net, category="general", db_path=DEFAULT_DB_PATH)
            mobile_proxies = get_prioritized_proxies(mobile_proxies, network_type="cellular", category="general", db_path=DEFAULT_DB_PATH)
            ai_proxies = get_prioritized_proxies(ai_proxies, network_type=current_net, category="ai", db_path=DEFAULT_DB_PATH)
    except Exception:
        # Graceful fallback to static priority tiers
        pass

    # Category Constructor: custom user categories or defaults
    categories = opts.get("categories") or DEFAULT_CATEGORIES
    category_names = [cat["name"] for cat in categories]

    # Clean FlClash UI: Only top-level connectors + category selectors
    # No redundant child groups like Auto-AI-Fallback or Auto-Media-UrlTest!
    proxy_groups = [
        {
            "name": "PROXY",
            "type": "select",
            "proxies": [
                "Auto-Fallback",
                "🛡️ Mobile-Bypass",
                "Auto-UrlTest",
            ] + category_names + fallback_proxies
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
        }
    ]

    # Build category selector groups
    for cat in categories:
        cat_name = cat["name"]
        cat_priority = cat.get("priority", "fallback")
        if cat_priority == "ai":
            cat_proxies = ai_proxies
        else:
            cat_proxies = fallback_proxies

        default_opts = cat.get("default_options", ["Auto-Fallback", "Auto-UrlTest", "DIRECT"])
        proxy_groups.append({
            "name": cat_name,
            "type": cat.get("type", "select"),
            "proxies": default_opts + cat_proxies
        })

    # Assemble rules modularly
    base_rules = [
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
    ]

    category_rules = []
    for cat in categories:
        cat_name = cat["name"]
        for r in cat.get("rules", []):
            parts = [p.strip() for p in r.split(",")]
            if len(parts) == 2:
                category_rules.append(f"{parts[0]},{parts[1]},{cat_name}")
            elif len(parts) == 3 and parts[2].lower() == "no-resolve":
                category_rules.append(f"{parts[0]},{parts[1]},{cat_name},no-resolve")
            else:
                category_rules.append(r)

    direct_ru_rules = [
        # Russian Services (Direct)
        "DOMAIN-SUFFIX,ru,DIRECT",
        "DOMAIN-SUFFIX,su,DIRECT",
        "DOMAIN-SUFFIX,xn--p1ai,DIRECT",
        "DOMAIN-SUFFIX,yandex.ru,DIRECT",
        "DOMAIN-SUFFIX,vk.com,DIRECT",
        "DOMAIN-SUFFIX,sberbank.ru,DIRECT",
        "DOMAIN-SUFFIX,tbank.ru,DIRECT",
        "DOMAIN-SUFFIX,gosuslugi.ru,DIRECT",
        "GEOIP,RU,DIRECT",
        
        # Match All Other
        "MATCH,PROXY"
    ]

    final_config = {
        "mixed-port": opts.get("mixed_port", opts.get("mixed-port", 7890)),
        "socks-port": opts.get("socks_port", opts.get("socks-port", 7891)),
        "redir-port": opts.get("redir_port", opts.get("redir-port", 7892)),
        "allow-lan": opts.get("allow_lan", opts.get("allow-lan", True)),
        "mode": opts.get("mode", "rule"),
        "log-level": opts.get("log_level", opts.get("log-level", "info")),
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
        "proxy-groups": proxy_groups,
        "rules": base_rules + category_rules + direct_ru_rules
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
                if "|" in line:
                    name, url = [x.strip() for x in line.split("|", 1)]
                elif "=" in line and not line.startswith("http://") and not line.startswith("https://"):
                    name, url = [x.strip() for x in line.split("=", 1)]
                
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

    # Mask all subscription URLs in CI
    for s in subs:
        if isinstance(s, dict) and s.get("url"):
            _mask_token_in_ci(s["url"])

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
    
    if not clean_proxies:
        print("[ERROR] No valid proxies remaining after filtering out junk/auto/RU nodes. Aborting.")
        return 1
        
    # Deduplicate proxies by (server, port, uuid) fingerprint and ensure unique names
    unique_proxies = deduplicate_proxies(clean_proxies)
    print(f"[INFO] Total active unique proxies assembled: {len(unique_proxies)}")

    opts = user_config.get("options", {})
    final_config = build_mihomo_config(unique_proxies, user_options=opts)
    
    env_out = os.getenv("OUTPUT_FILE", "").strip()
    if env_out:
        out_files = [env_out]
    elif opts.get("output_file"):
        out_files = [opts["output_file"]]
    else:
        out_files = opts.get("output_files", ["./Aegis.yaml", "./3-in-1_VPN.yaml"])
        
    for out in out_files:
        expanded = os.path.expanduser(os.path.expandvars(out))
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(os.path.join(script_dir, expanded))
        out_dir = os.path.dirname(expanded)
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
