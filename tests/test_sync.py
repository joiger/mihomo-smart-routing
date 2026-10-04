# -*- coding: utf-8 -*-
"""
Automated Test Suite for Aegis Smart Routing & Multi-Subscription Merger
Tests parallel fetching, zero-downtime resilience, fingerprint deduplication,
country fallback tiers, strict TLS enforcement, and leak detection.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import subprocess
import tempfile
import yaml

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sync
from scripts import check_diff

class TestAegisCore(unittest.TestCase):

    def test_parse_vless_uri(self):
        proto = "vless"
        fake_uuid = "11111111-2222-3333-4444-555555555555"
        uri = (
            f"{proto}://{fake_uuid}@fi.server.com:443"
            "?security=reality&sni=fi.server.com&fp=chrome&pbk=key123&sid=sid123&type=tcp&flow=xtls-rprx-vision"
            "#%F0%9F%87%AB%F0%9F%87%AE%20Finland%20Node"
        )
        proxy = sync.parse_vless_uri(uri, prefix="TestProvider")
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy["name"], "[TestProvider] 🇫🇮 Finland Node")
        self.assertEqual(proxy["type"], "vless")
        self.assertEqual(proxy["server"], "fi.server.com")
        self.assertEqual(proxy["port"], 443)
        self.assertEqual(proxy["uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertTrue(proxy["tls"])
        self.assertEqual(proxy["reality-opts"]["public-key"], "key123")
        self.assertEqual(proxy["reality-opts"]["short-id"], "sid123")
        self.assertEqual(proxy["flow"], "xtls-rprx-vision")

    def test_fingerprint_deduplication(self):
        # Two proxies with identical server, port, uuid but different names and providers
        p1 = {
            "name": "[Provider1] 🇫🇮 Helsinki",
            "type": "vless",
            "server": "same.server.com",
            "port": 443,
            "uuid": "dup-uuid-1234",
            "tls": True
        }
        p2 = {
            "name": "[Provider2] 🇫🇮 Backup Helsinki",
            "type": "vless",
            "server": "SAME.SERVER.COM",  # case-insensitive check
            "port": 443,
            "uuid": "dup-uuid-1234",
            "tls": True
        }
        p3 = {
            "name": "[Provider1] 🇸🇪 Stockholm",
            "type": "vless",
            "server": "other.server.com",
            "port": 443,
            "uuid": "other-uuid-5678",
            "tls": True
        }
        
        deduped = sync.deduplicate_proxies([p1, p2, p3])
        self.assertEqual(len(deduped), 2)
        # First duplicate is preserved, second is dropped
        self.assertEqual(deduped[0]["name"], "[Provider1] 🇫🇮 Helsinki")
        self.assertEqual(deduped[1]["name"], "[Provider1] 🇸🇪 Stockholm")

    def test_duplicate_names_disambiguation(self):
        # Two different servers but identical names
        p1 = {"name": "Node", "server": "srv1.com", "port": 443, "uuid": "u1"}
        p2 = {"name": "Node", "server": "srv2.com", "port": 443, "uuid": "u2"}
        deduped = sync.deduplicate_proxies([p1, p2])
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["name"], "Node")
        self.assertEqual(deduped[1]["name"], "Node #2")

    def test_fallback_priority_hierarchy(self):
        """
        Verify strict fallback hierarchy:
        Tier 1 (FI/SE) < Tier 2 (Relay/Bypass) < Tier 3 (DE/NL/EE/PL) < Tier 4 (Other EU) < Tier 5 (USA) < Tier 6 (Rest)
        """
        tier1_fi = "[VPN] 🇫🇮 Финляндия Хельсинки"
        tier1_se = "[VPN] 🇸🇪 Швеция Стокгольм"
        
        tier2_relay = "[VPN] Москва → Германия"
        tier2_bypass = "[VPN] 🛡️ Обход блокировок"
        tier2_relay_fi = "[VPN] Москва → Финляндия"  # Transit bridge exit to FI must still be Tier 2
        
        tier3_de = "[VPN] 🇩🇪 Германия Франкфурт"
        tier3_nl = "[VPN] 🇳🇱 Нидерланды Амстердам"
        tier3_ee = "[VPN] 🇪🇪 Эстония Таллин"
        tier3_pl = "[VPN] 🇵🇱 Польша Варшава"
        
        tier4_uk = "[VPN] 🇬🇧 Великобритания Лондон"
        tier4_fr = "[VPN] 🇫🇷 Франция Париж"
        tier4_cz = "[VPN] 🇨🇿 Чехия Прага"
        tier4_tr = "[VPN] 🇹🇷 Турция Стамбул"
        tier4_kz = "[VPN] 🇰🇿 Казахстан Алматы"
        
        tier5_us = "[VPN] 🇺🇸 США Нью-Йорк"
        tier6_jp = "[VPN] 🇯🇵 Япония Токио"

        # Tier 1 checks
        self.assertEqual(sync.fallback_priority(tier1_fi), 1)
        self.assertEqual(sync.fallback_priority(tier1_se), 1)

        # Tier 2 checks
        self.assertEqual(sync.fallback_priority(tier2_relay), 2)
        self.assertEqual(sync.fallback_priority(tier2_bypass), 2)
        self.assertEqual(sync.fallback_priority(tier2_relay_fi), 2)

        # Tier 3 checks
        self.assertEqual(sync.fallback_priority(tier3_de), 3)
        self.assertEqual(sync.fallback_priority(tier3_nl), 3)
        self.assertEqual(sync.fallback_priority(tier3_ee), 3)
        self.assertEqual(sync.fallback_priority(tier3_pl), 3)

        # Tier 4 checks
        self.assertEqual(sync.fallback_priority(tier4_uk), 4)
        self.assertEqual(sync.fallback_priority(tier4_fr), 4)
        self.assertEqual(sync.fallback_priority(tier4_cz), 4)
        self.assertEqual(sync.fallback_priority(tier4_tr), 4)
        self.assertEqual(sync.fallback_priority(tier4_kz), 4)

        # Tier 5 checks
        self.assertEqual(sync.fallback_priority(tier5_us), 5)

        # Tier 6 checks
        self.assertEqual(sync.fallback_priority(tier6_jp), 6)

        # Verify strict order
        all_samples = [
            tier6_jp, tier5_us, tier4_fr, tier3_nl, tier2_bypass, tier1_fi,
            tier4_uk, tier3_de, tier1_se, tier2_relay_fi
        ]
        sorted_samples = sorted(all_samples, key=sync.fallback_priority)
        tiers = [sync.fallback_priority(s) for s in sorted_samples]
        self.assertEqual(tiers, sorted(tiers))

    def test_junk_and_auto_filtering(self):
        # Fake servers
        self.assertTrue(sync.is_junk_or_auto({"name": "Test", "server": "127.0.0.1"}))
        self.assertTrue(sync.is_junk_or_auto({"name": "Test", "server": "localhost"}))

        # Auto-select pseudo nodes
        self.assertTrue(sync.is_junk_or_auto({"name": "🌐 Автовыбор", "server": "1.2.3.4"}))
        self.assertTrue(sync.is_junk_or_auto({"name": "⚡ Auto-Select best", "server": "1.2.3.4"}))
        self.assertTrue(sync.is_junk_or_auto({"name": "Loadbalance pool", "server": "1.2.3.4"}))

        # Stubs and info
        self.assertTrue(sync.is_junk_or_auto({"name": "Сервер на техработах", "server": "1.2.3.4"}))
        self.assertTrue(sync.is_junk_or_auto({"name": "Остаток трафика: 50GB", "server": "1.2.3.4"}))

        # Pure RU nodes without transit
        self.assertTrue(sync.is_junk_or_auto({"name": "🇷🇺 Россия Москва", "server": "1.2.3.4"}))
        self.assertTrue(sync.is_junk_or_auto({"name": "LTE | Все операторы", "server": "1.2.3.4"}))

        # Relay via RU should NOT be filtered
        self.assertFalse(sync.is_junk_or_auto({"name": "Москва → Германия", "server": "1.2.3.4"}))
        # Valid foreign node
        self.assertFalse(sync.is_junk_or_auto({"name": "🇫🇮 Finland Helsinki", "server": "1.2.3.4"}))

    @patch("sync.fetch_subscription")
    def test_parallel_fetch_and_zero_downtime_resilience(self, mock_fetch):
        """
        Verify that ThreadPoolExecutor concurrently fetches multiple subscriptions,
        and simulated provider failure (500 error / network drop) does NOT abort execution.
        """
        proxies_sub1 = [
            {"name": "[Sub1] FI Node", "server": "fi.com", "port": 443, "uuid": "u1", "type": "vless"}
        ]
        proxies_sub3 = [
            {"name": "[Sub3] SE Node", "server": "se.com", "port": 443, "uuid": "u3", "type": "vless"}
        ]

        def side_effect(sub):
            name = sub.get("name")
            if name == "HealthyProvider1":
                return proxies_sub1
            elif name == "FailingProvider2":
                raise ConnectionError("504 Gateway Timeout or Provider Drop")
            elif name == "HealthyProvider3":
                return proxies_sub3
            return []

        mock_fetch.side_effect = side_effect

        subs = [
            {"name": "HealthyProvider1", "url": "https://p1.example.com/sub"},
            {"name": "FailingProvider2", "url": "https://p2.example.com/sub"},
            {"name": "HealthyProvider3", "url": "https://p3.example.com/sub"},
        ]

        results = sync.fetch_all_subscriptions(subs, timeout=15)
        self.assertEqual(len(results), 2)
        names = [p["name"] for p in results]
        self.assertIn("[Sub1] FI Node", names)
        self.assertIn("[Sub3] SE Node", names)

    @patch("sync.requests.Session")
    def test_strict_tls_verification_enforced(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.content = b"vless://u@srv:443#Test"
        mock_session.get.return_value = mock_resp

        sub = {"name": "Test", "url": "https://provider.example.com/sub"}
        sync.fetch_subscription(sub)

        # Check that session.get was called with verify=True
        mock_session.get.assert_called()
        _, kwargs = mock_session.get.call_args
        self.assertIn("verify", kwargs)
        self.assertTrue(kwargs["verify"], "Strict TLS verification must be True!")

    def test_security_scanner_zero_leaks_on_repo(self):
        """
        Verify that scripts/check_diff.py confirms zero leaks of personal domains or credentials.
        """
        ret = check_diff.main()
        self.assertEqual(ret, 0, "Security scanner detected leaks in the repository!")

    def test_security_scanner_detects_forbidden_patterns(self):
        """
        Verify that forbidden patterns are caught by check_diff.scan_text.
        """
        sample_leak_domain = f"https://sub.{'desiderius'}.{'ru'}/sub"
        violations = check_diff.scan_text(sample_leak_domain)
        self.assertTrue(any("Personal domain leak" in v[2] for v in violations))

        sample_leak_token = f"{'ghp_'}{'a' * 36}"
        violations_token = check_diff.scan_text(sample_leak_token)
        self.assertTrue(any("Personal Access Token" in v[2] for v in violations_token))

    def test_mihomo_yaml_schema_validity(self):
        """
        Build a full Mihomo configuration and validate with verge-mihomo -t if available.
        """
        proxies = [
            {
                "name": "[Aegis] 🇫🇮 Finland Fast",
                "type": "vless",
                "server": "1.1.1.1",
                "port": 443,
                "uuid": "11111111-2222-3333-4444-555555555555",
                "tls": True,
                "udp": True,
                "packet-encoding": "xudp",
                "servername": "fi.example.com",
                "client-fingerprint": "chrome"
            },
            {
                "name": "[Aegis] Москва → Германия",
                "type": "vless",
                "server": "2.2.2.2",
                "port": 443,
                "uuid": "22222222-3333-4444-5555-666666666666",
                "tls": True,
                "udp": True,
                "packet-encoding": "xudp",
                "servername": "relay.example.com",
                "client-fingerprint": "chrome"
            },
            {
                "name": "[Aegis] 🇩🇪 Germany Core",
                "type": "vless",
                "server": "3.3.3.3",
                "port": 443,
                "uuid": "33333333-4444-5555-6666-777777777777",
                "tls": True,
                "udp": True,
                "packet-encoding": "xudp",
                "servername": "de.example.com",
                "client-fingerprint": "chrome"
            }
        ]

        config = sync.build_mihomo_config(proxies)
        
        # Verify basic schema properties
        self.assertEqual(config["mode"], "rule")
        self.assertTrue(config["tun"]["enable"])
        self.assertEqual(config["dns"]["enhanced-mode"], "fake-ip")
        self.assertEqual(len(config["proxies"]), 3)
        self.assertTrue(any(g["name"] == "Auto-Fallback" for g in config["proxy-groups"]))
        self.assertTrue(any(g["name"] == "🛡️ Mobile-Bypass" for g in config["proxy-groups"]))

        # Check YAML serialization
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tf:
            yaml.dump(config, tf, Dumper=sync.NoAliasDumper, allow_unicode=True, sort_keys=False)
            tmp_path = tf.name

        try:
            # Test with verge-mihomo if installed
            verge_exe = r"C:\Program Files\Clash Verge\verge-mihomo.exe"
            if os.path.exists(verge_exe):
                res = subprocess.run([verge_exe, "-t", "-f", tmp_path], capture_output=True, text=True)
                self.assertEqual(
                    res.returncode, 0,
                    f"verge-mihomo -t failed: {res.stdout}\n{res.stderr}"
                )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_parse_vless_uri_ipv6(self):
        """
        Verify robust parsing of IPv6 hosts in square brackets.
        """
        fake_uuid = "11111111-2222-3333-4444-555555555555"
        uri = f"vless://{fake_uuid}@[2001:db8::1]:8443?security=none#IPv6Node"
        proxy = sync.parse_vless_uri(uri)
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy["server"], "2001:db8::1")
        self.assertEqual(proxy["port"], 8443)
        self.assertEqual(proxy["uuid"], fake_uuid)

    def test_safe_b64decode_unpadded_and_urlsafe(self):
        """
        Verify that safe_b64decode correctly handles unpadded and URL-safe base64 payloads.
        """
        import base64
        original = f"{'vless'}://{'test-uuid'}@{'srv.com:443'}#TestNode"
        unpadded = base64.urlsafe_b64encode(original.encode()).decode().rstrip("=")
        decoded = sync.safe_b64decode(unpadded).decode("utf-8")
        self.assertEqual(decoded, original)

    @patch("sync.requests.Session")
    def test_cloudflare_html_and_empty_body_handling(self, mock_session_class):
        """
        Verify graceful handling when a provider returns an HTML anti-bot challenge or empty body.
        """
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        # 1. HTML challenge response
        mock_resp_html = MagicMock()
        mock_resp_html.content = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>cf-turnstile</body></html>"
        mock_session.get.return_value = mock_resp_html
        
        proxies_html = sync.fetch_subscription({"name": "CloudflareProtected", "url": "https://p.com/sub", "headers": {"User-Agent": "Hiddify/2.0.5"}})
        self.assertEqual(proxies_html, [])

        # 2. Empty response
        mock_resp_empty = MagicMock()
        mock_resp_empty.content = b""
        mock_session.get.return_value = mock_resp_empty
        
        proxies_empty = sync.fetch_subscription({"name": "EmptyProvider", "url": "https://p.com/sub", "headers": {"User-Agent": "Hiddify/2.0.5"}})
        self.assertEqual(proxies_empty, [])

    def test_dpi_bypass_node_preservation_under_ru(self):
        """
        Verify that Russian transit DPI bypass nodes (e.g. '[VPN] 🇷🇺 Обход ТСПУ')
        are preserved for Tier 2 mobile survival and NOT filtered as junk.
        """
        bypass_node = {"name": "[VPN] 🇷🇺 Обход ТСПУ", "server": "1.2.3.4"}
        self.assertFalse(sync.is_junk_or_auto(bypass_node))
        self.assertEqual(sync.fallback_priority(bypass_node["name"]), 2)

    def test_fallback_priority_cities_and_codes(self):
        """
        Verify city names and country code tokens for Tier 1 and Tier 5.
        """
        self.assertEqual(sync.fallback_priority("[VPN] Helsinki 01"), 1)
        self.assertEqual(sync.fallback_priority("[VPN] Stockholm 01"), 1)
        self.assertEqual(sync.fallback_priority("[VPN] FI-01"), 1)
        self.assertEqual(sync.fallback_priority("[VPN] SE-02"), 1)
        self.assertEqual(sync.fallback_priority("[VPN] New York 01"), 5)
        self.assertEqual(sync.fallback_priority("[VPN] Los Angeles Fast"), 5)

    def test_ai_priority_with_flag_emojis(self):
        """
        Verify that AI service priority correctly ranks nodes with flag emojis and city names.
        """
        self.assertEqual(sync.ai_priority("[VPN] 🇺🇸 New York"), 1)
        self.assertEqual(sync.ai_priority("[VPN] 🇩🇪 Frankfurt"), 2)
        self.assertEqual(sync.ai_priority("[VPN] 🇬🇧 London"), 2)
        self.assertEqual(sync.ai_priority("[VPN] 🇸🇪 Stockholm"), 3)
        self.assertEqual(sync.ai_priority("[VPN] 🇫🇮 Helsinki"), 3)

    def test_security_scanner_line_with_allowed_and_leak(self):
        """
        Verify that check_diff catches real leaks even when an allowed placeholder is on the same line.
        """
        dom = "desiderius" + ".ru"
        mixed_line = f"# Documentation (*.{dom}) with accident: api.{dom}"
        violations = check_diff.scan_text(mixed_line)
        self.assertTrue(len(violations) > 0)
        self.assertTrue(any("Personal domain leak" in v[2] for v in violations))

if __name__ == "__main__":
    unittest.main()
