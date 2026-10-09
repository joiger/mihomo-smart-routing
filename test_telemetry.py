# -*- coding: utf-8 -*-
"""
QA Lead Unit Tests for Aegis Telemetry Database & Geo-Block Detection
"""

import os
import time
import unittest
import tempfile

from telemetry_db import (
    init_db, record_probe, cleanup_sliding_window, 
    recalculate_scores, get_prioritized_proxies, calculate_node_score,
    get_db_connection
)
from geo_block_detector import is_response_blocked, evaluate_ping_result
from sync import build_mihomo_config

class TestAegisTelemetry(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.temp_db_path)

    def tearDown(self):
        try:
            os.close(self.temp_db_fd)
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_geo_block_detection(self):
        # 1. 403 Forbidden
        blocked, reason = is_response_blocked(403, "")
        self.assertTrue(blocked)
        self.assertIn("403", reason)

        # 2. 200 OK with blockpage text
        blocked, reason = is_response_blocked(200, "<html><body>Sorry, OpenAI is not available in your country.</body></html>")
        self.assertTrue(blocked)
        self.assertIn("not available in your country", reason)

        # 3. 204 Clean
        blocked, _ = is_response_blocked(204, "")
        self.assertFalse(blocked)

        # 4. evaluate_ping_result conversion
        eff_lat, is_blk, _ = evaluate_ping_result(200, 45, "Service is not available in your country")
        self.assertEqual(eff_lat, -1)
        self.assertEqual(is_blk, 1)

        eff_lat_ok, is_blk_ok, _ = evaluate_ping_result(204, 38, "")
        self.assertEqual(eff_lat_ok, 38)
        self.assertEqual(is_blk_ok, 0)

    def test_sliding_window_cleanup(self):
        now = int(time.time())
        old_time = now - (8 * 86400) # 8 days ago
        recent_time = now - (2 * 86400) # 2 days ago

        record_probe("OldNode", 50, network_type="wifi", timestamp=old_time, db_path=self.temp_db_path)
        record_probe("RecentNode", 40, network_type="wifi", timestamp=recent_time, db_path=self.temp_db_path)

        deleted = cleanup_sliding_window(self.temp_db_path, days=7)
        self.assertEqual(deleted, 1)

        conn = get_db_connection(self.temp_db_path)
        rows = conn.execute("SELECT proxy_name FROM node_telemetry").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "RecentNode")

    def test_dual_network_scoring_wifi_vs_cellular(self):
        now = int(time.time())
        
        # Node FI: fast on Wi-Fi (35ms, 0% loss), but dead on LTE (timeout, 100% loss)
        for _ in range(5):
            record_probe("FI-Fast", 35, network_type="wifi", packet_loss_rate=0.0, timestamp=now, db_path=self.temp_db_path)
            record_probe("FI-Fast", -1, network_type="cellular", packet_loss_rate=1.0, timestamp=now, db_path=self.temp_db_path)

        # Node Bridge: moderate on Wi-Fi (50ms, 0% loss), and great on LTE (50ms, 0% loss)
        for _ in range(5):
            record_probe("Bridge-Relay", 50, network_type="wifi", packet_loss_rate=0.0, timestamp=now, db_path=self.temp_db_path)
            record_probe("Bridge-Relay", 50, network_type="cellular", packet_loss_rate=0.0, timestamp=now, db_path=self.temp_db_path)

        # Node Blocked: fast (40ms) but geo-blocked for OpenAI
        for _ in range(5):
            record_probe("Blocked-Node", 40, network_type="wifi", is_geo_blocked=1, timestamp=now, db_path=self.temp_db_path)
            record_probe("Blocked-Node", 40, network_type="cellular", is_geo_blocked=1, timestamp=now, db_path=self.temp_db_path)

        recalculate_scores(self.temp_db_path)

        candidates = ["Blocked-Node", "Bridge-Relay", "FI-Fast"]

        # On Wi-Fi: FI-Fast must be #1, Bridge-Relay #2, Blocked-Node #3 (at the bottom)
        wifi_ranked = get_prioritized_proxies(candidates, network_type="wifi", db_path=self.temp_db_path)
        self.assertEqual(wifi_ranked, ["FI-Fast", "Bridge-Relay", "Blocked-Node"])

        # On Cellular: Bridge-Relay must be #1, FI-Fast demoted due to 100% packet loss!
        cellular_ranked = get_prioritized_proxies(candidates, network_type="cellular", db_path=self.temp_db_path)
        self.assertEqual(cellular_ranked[0], "Bridge-Relay")
        self.assertIn("Blocked-Node", cellular_ranked)

    def test_russian_block_phrase_detection(self):
        from geo_block_detector import BLOCK_PATTERNS
        for pattern in [
            "не поддерживается в вашей стране",
            "пока не поддерживается",
            "gemini пока не поддерживается"
        ]:
            self.assertIn(pattern, BLOCK_PATTERNS)

        test_cases = [
            ("<html><body>Сервис не поддерживается в вашей стране.</body></html>", "не поддерживается в вашей стране"),
            ("<html><body>К сожалению, данный сервис пока не поддерживается.</body></html>", "пока не поддерживается"),
            ("<html><body>Gemini пока не поддерживается в вашей стране</body></html>", "gemini пока не поддерживается"),
            ("<html><body>GEMINI ПОКА НЕ ПОДДЕРЖИВАЕТСЯ В ВАШЕЙ СТРАНЕ</body></html>", "gemini пока не поддерживается"),
        ]
        for html_body, expected_pattern in test_cases:
            blocked, reason = is_response_blocked(200, html_body)
            self.assertTrue(blocked, f"Failed to detect block in: {html_body}")
            self.assertIn(expected_pattern, reason)

            eff_lat, is_blk, blk_reason = evaluate_ping_result(200, 50, html_body)
            self.assertEqual(eff_lat, -1)
            self.assertEqual(is_blk, 1)
            self.assertIn("Block pattern detected", blk_reason)

    def test_auto_ai_stable_and_antigravity_bypass_config(self):
        sample_proxies = [
            {"name": "🇺🇸 USA Premium", "type": "vless", "server": "us.node.com", "port": 443, "uuid": "u1"},
            {"name": "🇩🇪 Germany HighSpeed", "type": "vless", "server": "de.node.com", "port": 443, "uuid": "u2"},
            {"name": "🇫🇮 Finland Local", "type": "vless", "server": "fi.node.com", "port": 443, "uuid": "u3"},
        ]
        cfg = build_mihomo_config(sample_proxies)

        # 1. Verify Auto-AI-Stable proxy-group specification
        ai_stable_groups = [g for g in cfg["proxy-groups"] if g["name"] == "🤖 Auto-AI-Stable"]
        self.assertEqual(len(ai_stable_groups), 1, "🤖 Auto-AI-Stable group should exist in proxy-groups")
        ai_stable = ai_stable_groups[0]
        self.assertEqual(ai_stable["type"], "fallback")
        self.assertEqual(ai_stable["url"], "https://generativelanguage.googleapis.com")
        self.assertEqual(ai_stable["expected-status"], "404")
        self.assertEqual(ai_stable["interval"], 60)
        self.assertEqual(ai_stable["timeout"], 5000)
        self.assertFalse(ai_stable["lazy"])
        self.assertEqual(ai_stable["max-failed-times"], 2)
        self.assertIn("🇺🇸 USA Premium", ai_stable["proxies"])
        self.assertIn("🇩🇪 Germany HighSpeed", ai_stable["proxies"])

        # 2. Verify PROXY group includes 🤖 Auto-AI-Stable
        proxy_group = next(g for g in cfg["proxy-groups"] if g["name"] == "PROXY")
        self.assertIn("🤖 Auto-AI-Stable", proxy_group["proxies"])

        # 3. Verify 🤖 AI-Services category group default_options (🤖 AI-Max-Trust is primary, fail-closed REJECT killswitch, no DIRECT)
        ai_services_group = next(g for g in cfg["proxy-groups"] if g["name"] == "🤖 AI-Services")
        self.assertEqual(
            ai_services_group["proxies"][:5],
            ["🤖 AI-Max-Trust", "🤖 Auto-AI-Stable", "Auto-Fallback", "🛡️ Mobile-Bypass", "REJECT"]
        )
        self.assertNotIn("DIRECT", ai_services_group["proxies"], "🤖 AI-Services must not contain DIRECT under any circumstances")

        # 4. Verify Antigravity and GitHub Gist rules
        rules = cfg["rules"]
        self.assertIn("DOMAIN,cloudcode-pa.googleapis.com,🤖 AI-Services", rules)
        self.assertIn("DOMAIN,daily-cloudcode-pa.googleapis.com,🤖 AI-Services", rules)
        self.assertIn("DOMAIN,gist.githubusercontent.com,🛡️ Mobile-Bypass", rules)
        self.assertIn("DOMAIN-SUFFIX,events.data.microsoft.com,REJECT", rules)
        self.assertIn("DOMAIN-SUFFIX,wns.windows.com,DIRECT", rules)
        self.assertIn("DOMAIN-SUFFIX,push.services.mozilla.com,DIRECT", rules)
        self.assertIn("DOMAIN-SUFFIX,bitwarden.com,DIRECT", rules)
        self.assertIn("IP-CIDR,45.155.204.190/32,DIRECT,no-resolve", rules)
        self.assertIn("IP-CIDR,83.220.169.155/32,DIRECT,no-resolve", rules)

        # 5. Verify TUN process mode (default strict, overridable)
        self.assertEqual(cfg["tun"]["find-process-mode"], "strict")
        cfg_custom_proc = build_mihomo_config(sample_proxies, user_options={"find_process_mode": "always"})
        self.assertEqual(cfg_custom_proc["tun"]["find-process-mode"], "always")

        # 6. Verify dedicated service categories and auto-fallback pools
        group_names = [g["name"] for g in cfg["proxy-groups"]]
        expected_cats = [
            "🤖 AI-Services", "✈️ Telegram", "💬 Discord",
            "💻 Dev-Infrastructure", "🎬 Media-Streaming",
            "🪙 Crypto-Finance", "🎯 Games"
        ]
        for cat in expected_cats:
            self.assertIn(cat, group_names, f"Category group '{cat}' must exist")

        expected_auto_groups = [
            "🤖 Auto-AI-Stable", "✈️ Auto-TG-Stable", "💬 Auto-Discord-Stable",
            "💻 Auto-Dev-Stable", "🎬 Auto-Media-Fast",
            "🪙 Auto-Crypto-Stable", "🎯 Auto-Games-Stable"
        ]
        for auto_g in expected_auto_groups:
            self.assertIn(auto_g, group_names, f"Auto-fallback group '{auto_g}' must exist")
            grp = next(g for g in cfg["proxy-groups"] if g["name"] == auto_g)
            self.assertEqual(grp["type"], "fallback")
            self.assertFalse(grp.get("lazy", False), f"{auto_g} must be active (lazy=False)")

        # Verify Gaming category defaults to DIRECT first
        gaming_grp = next(g for g in cfg["proxy-groups"] if g["name"] == "🎯 Games")
        self.assertEqual(gaming_grp["proxies"][0], "DIRECT")

        # 7. Edge cases: Empty proxies, few proxies (<3), and non-AI-only proxies
        cfg_empty = build_mihomo_config([])
        ai_stable_empty = next(g for g in cfg_empty["proxy-groups"] if g["name"] == "🤖 Auto-AI-Stable")
        self.assertEqual(ai_stable_empty["proxies"], [])

        # Two proxies edge case: must not crash and pool takes available proxies
        two_nodes = [
            {"name": "Node1", "type": "vless", "server": "n1.com", "port": 443, "uuid": "u1"},
            {"name": "Node2", "type": "vless", "server": "n2.com", "port": 443, "uuid": "u2"},
        ]
        cfg_two = build_mihomo_config(two_nodes)
        dev_two = next(g for g in cfg_two["proxy-groups"] if g["name"] == "💻 Auto-Dev-Stable")
        self.assertEqual(len(dev_two["proxies"]), 2)

        ru_only = [{"name": "🇷🇺 Moscow Relay", "type": "vless", "server": "ru.node.com", "port": 443, "uuid": "u4"}]
        cfg_ru = build_mihomo_config(ru_only)
        ai_stable_ru = next(g for g in cfg_ru["proxy-groups"] if g["name"] == "🤖 Auto-AI-Stable")
        self.assertEqual(ai_stable_ru["proxies"], ["🇷🇺 Moscow Relay"])

    def test_ai_max_trust_single_ip_dns_country(self):
        sample_proxies = [
            {"name": "🇺🇸 USA Premium #1", "type": "vless", "server": "us1.node.com", "port": 443, "uuid": "u1"},
            {"name": "🇺🇸 USA New York #2", "type": "vless", "server": "us2.node.com", "port": 443, "uuid": "u2"},
            {"name": "🇩🇪 Germany HighSpeed", "type": "vless", "server": "de.node.com", "port": 443, "uuid": "u3"},
            {"name": "🇸🇪 Sweden Fast", "type": "vless", "server": "se.node.com", "port": 443, "uuid": "u4"},
            {"name": "🇫🇮 Finland Local", "type": "vless", "server": "fi.node.com", "port": 443, "uuid": "u5"},
        ]
        user_options = {
            "routing": {
                "ai_max_trust_nodes": ["🇺🇸 USA New York #2"]
            }
        }
        cfg = build_mihomo_config(sample_proxies, user_options=user_options)

        # 1. 🤖 AI-Max-Trust must exist and be type select (sticky single IP, no flapping)
        mt_group = next((g for g in cfg["proxy-groups"] if g["name"] == "🤖 AI-Max-Trust"), None)
        self.assertIsNotNone(mt_group, "🤖 AI-Max-Trust group must exist")
        self.assertEqual(mt_group["type"], "select", "AI-Max-Trust must be select type to guarantee single sticky IP")

        # 2. Single Country: all proxies in AI-Max-Trust must be US nodes (strictly one country)
        self.assertEqual(len(mt_group["proxies"]), 2)
        self.assertIn("🇺🇸 USA Premium #1", mt_group["proxies"])
        self.assertIn("🇺🇸 USA New York #2", mt_group["proxies"])
        self.assertNotIn("🇩🇪 Germany HighSpeed", mt_group["proxies"])
        self.assertNotIn("🇸🇪 Sweden Fast", mt_group["proxies"])
        self.assertNotIn("🇫🇮 Finland Local", mt_group["proxies"])

        # Preferred node is honored as first proxy
        self.assertEqual(mt_group["proxies"][0], "🇺🇸 USA New York #2")

        # 3. Available in PROXY and 🤖 AI-Services (default active in 🤖 AI-Services)
        proxy_group = next(g for g in cfg["proxy-groups"] if g["name"] == "PROXY")
        self.assertIn("🤖 AI-Max-Trust", proxy_group["proxies"])

        ai_services = next(g for g in cfg["proxy-groups"] if g["name"] == "🤖 AI-Services")
        self.assertIn("🤖 AI-Max-Trust", ai_services["proxies"])
        self.assertEqual(ai_services["proxies"][0], "🤖 AI-Max-Trust", "🤖 AI-Max-Trust must be the default active option in 🤖 AI-Services")
        self.assertNotIn("DIRECT", ai_services["proxies"], "🤖 AI-Services must not contain DIRECT")
        self.assertIn("REJECT", ai_services["proxies"], "🤖 AI-Services must contain REJECT as fail-closed killswitch")

        # 4. Single DNS: respect-rules enabled, bootstrap host defined, and AI domains routed to secure DoH bound to 🤖 AI-Max-Trust
        dns = cfg["dns"]
        self.assertTrue(dns.get("respect-rules", False), "DNS respect-rules must be enabled")
        self.assertEqual(cfg.get("hosts", {}).get("dns.google"), "8.8.8.8", "dns.google host bootstrap must be defined")
        policy = dns.get("nameserver-policy", {})
        for domain_pattern in [
            "+.openai.com", "+.chatgpt.com", "+.anthropic.com", "+.claude.ai",
            "+.generativelanguage.googleapis.com", "+.aistudio.google.com",
            "+.gemini.google.com", "+.deepseek.com", "+.openrouter.ai", "+.x.ai",
            "+.cloudcode-pa.googleapis.com", "+.daily-cloudcode-pa.googleapis.com",
            "+.apikeys.googleapis.com",
            "geosite:openai", "geosite:anthropic", "geosite:google-gemini"
        ]:
            self.assertEqual(policy.get(domain_pattern), "https://dns.google/dns-query#🤖 AI-Max-Trust",
                             f"Domain {domain_pattern} must use https://dns.google/dns-query#🤖 AI-Max-Trust in nameserver-policy")

        # 5. Service fallback groups (Antigravity, Claude, OpenAI, Google AI) all align with US nodes and honor preferred node
        for service_name in ["Aegis-Antigravity", "Aegis-Google-AI", "Aegis-Claude", "Aegis-OpenAI"]:
            grp = next((g for g in cfg["proxy-groups"] if g["name"] == service_name), None)
            self.assertIsNotNone(grp, f"Service group {service_name} must exist")
            self.assertEqual(grp["proxies"][0], "🇺🇸 USA New York #2",
                             f"{service_name} first proxy must match preferred US node")
            self.assertNotIn("🇩🇪 Germany HighSpeed", grp["proxies"],
                             f"{service_name} must exclude non-US proxies to preserve single country")
            self.assertNotIn("🇸🇪 Sweden Fast", grp["proxies"])
            self.assertNotIn("🇫🇮 Finland Local", grp["proxies"])
            self.assertNotIn("DIRECT", grp["proxies"], f"{service_name} must never contain DIRECT")

        # 6. Edge cases: Empty proxies and no US nodes
        cfg_empty = build_mihomo_config([])
        mt_empty = next(g for g in cfg_empty["proxy-groups"] if g["name"] == "🤖 AI-Max-Trust")
        self.assertEqual(mt_empty["proxies"], [])

        no_us_nodes = [
            {"name": "🇩🇪 Germany HighSpeed", "type": "vless", "server": "de.node.com", "port": 443, "uuid": "u3"},
            {"name": "🇫🇮 Finland Local", "type": "vless", "server": "fi.node.com", "port": 443, "uuid": "u5"},
        ]
        cfg_no_us = build_mihomo_config(no_us_nodes)
        mt_no_us = next(g for g in cfg_no_us["proxy-groups"] if g["name"] == "🤖 AI-Max-Trust")
        self.assertEqual(len(mt_no_us["proxies"]), 2)

if __name__ == "__main__":
    unittest.main()


