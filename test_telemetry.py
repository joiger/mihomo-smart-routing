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

if __name__ == "__main__":
    unittest.main()
