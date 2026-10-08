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
        self.assertEqual(ai_stable["interval"], 180)
        self.assertEqual(ai_stable["timeout"], 3000)
        self.assertFalse(ai_stable["lazy"])
        self.assertEqual(ai_stable["max-failed-times"], 3)
        self.assertIn("🇺🇸 USA Premium", ai_stable["proxies"])
        self.assertIn("🇩🇪 Germany HighSpeed", ai_stable["proxies"])

        # 2. Verify PROXY group includes 🤖 Auto-AI-Stable
        proxy_group = next(g for g in cfg["proxy-groups"] if g["name"] == "PROXY")
        self.assertIn("🤖 Auto-AI-Stable", proxy_group["proxies"])

        # 3. Verify 🤖 AI-Services category group default_options
        ai_services_group = next(g for g in cfg["proxy-groups"] if g["name"] == "🤖 AI-Services")
        self.assertEqual(
            ai_services_group["proxies"][:4],
            ["🤖 Auto-AI-Stable", "Auto-Fallback", "🛡️ Mobile-Bypass", "DIRECT"]
        )

        # 4. Verify fake-ip-filter entries
        fake_ip_filter = cfg["dns"]["fake-ip-filter"]
        self.assertIn("cloudcode-pa.googleapis.com", fake_ip_filter)
        self.assertIn("daily-cloudcode-pa.googleapis.com", fake_ip_filter)

        # 5. Verify Antigravity Unlocker DIRECT bypass rules and order
        rules = cfg["rules"]
        self.assertIn("DOMAIN,cloudcode-pa.googleapis.com,DIRECT", rules)
        self.assertIn("DOMAIN,daily-cloudcode-pa.googleapis.com,DIRECT", rules)
        self.assertIn("IP-CIDR,45.155.204.190/32,DIRECT,no-resolve", rules)
        self.assertIn("IP-CIDR,83.220.169.155/32,DIRECT,no-resolve", rules)

        idx_unlocker_domain = rules.index("DOMAIN,cloudcode-pa.googleapis.com,DIRECT")
        idx_ai_googleapis = rules.index("DOMAIN-SUFFIX,googleapis.com,🤖 AI-Services")
        self.assertLess(
            idx_unlocker_domain, idx_ai_googleapis,
            "Antigravity Unlocker bypass rules must precede general googleapis rules in rules order"
        )

        # 6. Edge cases: Empty proxies and non-AI-only proxies
        cfg_empty = build_mihomo_config([])
        ai_stable_empty = next(g for g in cfg_empty["proxy-groups"] if g["name"] == "🤖 Auto-AI-Stable")
        self.assertEqual(ai_stable_empty["proxies"], [])

        ru_only = [{"name": "🇷🇺 Moscow Relay", "type": "vless", "server": "ru.node.com", "port": 443, "uuid": "u4"}]
        cfg_ru = build_mihomo_config(ru_only)
        ai_stable_ru = next(g for g in cfg_ru["proxy-groups"] if g["name"] == "🤖 Auto-AI-Stable")
        self.assertEqual(ai_stable_ru["proxies"], ["🇷🇺 Moscow Relay"])

if __name__ == "__main__":
    unittest.main()

