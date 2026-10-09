# -*- coding: utf-8 -*-
"""
Aegis Adaptive Telemetry Database Module
Implements dual-network logging (Wi-Fi vs Cellular) in SQLite (WAL mode),
7-day rolling window auto-cleanup, and scoring formula for dynamic fallback prioritization.
"""

import os
import sys
import time
import sqlite3
import subprocess
from typing import List, Dict, Tuple, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telemetry.db")
GEO_BLOCK_COOLDOWN_SECONDS = 15 * 60

def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_db(db_path: str = DEFAULT_DB_PATH):
    """
    Initializes SQLite tables for telemetry and precalculated scores.
    """
    conn = get_db_connection(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                network_type TEXT NOT NULL CHECK(network_type IN ('wifi', 'cellular')),
                category TEXT NOT NULL,
                proxy_name TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                packet_loss_rate REAL DEFAULT 0.0,
                is_geo_blocked INTEGER DEFAULT 0
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_telemetry_query 
            ON node_telemetry(network_type, category, timestamp);
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_scores (
                network_type TEXT NOT NULL,
                category TEXT NOT NULL,
                proxy_name TEXT NOT NULL,
                calculated_score REAL NOT NULL,
                uptime_percent REAL NOT NULL,
                avg_latency INTEGER NOT NULL,
                last_updated INTEGER NOT NULL,
                PRIMARY KEY (network_type, category, proxy_name)
            );
        """)
    conn.close()

def cleanup_sliding_window(db_path: str = DEFAULT_DB_PATH, days: int = 7) -> int:
    """
    Purges records older than `days` (default 7 days) to prevent database bloat.
    """
    cutoff = int(time.time()) - (days * 86400)
    conn = get_db_connection(db_path)
    with conn:
        cursor = conn.execute("DELETE FROM node_telemetry WHERE timestamp < ?", (cutoff,))
        deleted_count = cursor.rowcount
    conn.close()
    return deleted_count

def record_probe(
    proxy_name: str,
    latency_ms: int,
    network_type: str = "wifi",
    category: str = "general",
    packet_loss_rate: float = 0.0,
    is_geo_blocked: int = 0,
    db_path: str = DEFAULT_DB_PATH,
    timestamp: Optional[int] = None
):
    """
    Records a single probe observation in the database.
    """
    if timestamp is None:
        timestamp = int(time.time())
        
    net_type = network_type.lower()
    if net_type not in ("wifi", "cellular"):
        net_type = "wifi"

    conn = get_db_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO node_telemetry (
                timestamp, network_type, category, proxy_name,
                latency_ms, packet_loss_rate, is_geo_blocked
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, net_type, category, proxy_name, latency_ms, packet_loss_rate, is_geo_blocked))
    conn.close()

def detect_current_network_type() -> str:
    """
    Detects whether the local machine is currently connected via Wi-Fi or Cellular.
    Defaults to 'wifi'.
    """
    env_net = os.getenv("AEGIS_NETWORK_TYPE", "").strip().lower()
    if env_net in ("wifi", "cellular"):
        return env_net

    if sys.platform == "win32":
        try:
            cmd = ["powershell", "-NoProfile", "-Command", 
                   "(Get-NetConnectionProfile).InterfaceAlias"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            out = res.stdout.lower()
            if "cellular" in out or "mobile" in out or "модем" in out or "сотовая" in out:
                return "cellular"
        except Exception:
            pass

    return "wifi"

def calculate_node_score(avg_latency: float, packet_loss: float, is_blocked: int, uptime: float) -> float:
    """
    QA Lead Scoring Formula:
    Score = (AvgLatency * (1 + 4 * PacketLoss)) + (IsBlocked * 100000) + ((100 - Uptime) * 50)
    Lower score = higher priority.
    """
    loss = max(0.0, min(1.0, packet_loss))
    upt = max(0.0, min(100.0, uptime))
    base = avg_latency * (1.0 + 4.0 * loss)
    block_penalty = 100000.0 if is_blocked else 0.0
    uptime_penalty = (100.0 - upt) * 50.0
    return base + block_penalty + uptime_penalty

def recalculate_scores(db_path: str = DEFAULT_DB_PATH):
    """
    Aggregates metrics over the 7-day rolling window and updates `node_scores`.
    """
    init_db(db_path)
    cleanup_sliding_window(db_path, days=7)
    
    conn = get_db_connection(db_path)
    now = int(time.time())
    
    with conn:
        cursor = conn.execute("""
            SELECT network_type, category, proxy_name,
                   AVG(CASE WHEN latency_ms > 0 THEN latency_ms ELSE NULL END) as avg_lat,
                   AVG(packet_loss_rate) as avg_loss,
                   0 as has_block,
                   COUNT(*) as total_probes,
                   SUM(CASE WHEN latency_ms > 0 THEN 1 ELSE 0 END) as successful_probes
            FROM node_telemetry
            GROUP BY network_type, category, proxy_name
        """)
        rows = cursor.fetchall()
        
        for row in rows:
            net_type, category, proxy_name, avg_lat, avg_loss, has_block, total, successful = row
            avg_lat = avg_lat if avg_lat is not None else 9999.0
            avg_loss = avg_loss if avg_loss is not None else 1.0
            # A block is service/network specific and expires. A later successful
            # observation clears it immediately; transport failures do not clear it.
            latest = conn.execute("""
                SELECT timestamp, is_geo_blocked FROM node_telemetry
                WHERE network_type = ? AND category = ? AND proxy_name = ?
                  AND (is_geo_blocked = 1 OR (latency_ms > 0 AND is_geo_blocked = 0))
                ORDER BY timestamp DESC, id DESC LIMIT 1
            """, (net_type, category, proxy_name)).fetchone()
            has_block = int(bool(latest and latest[1] and
                                 now - latest[0] < GEO_BLOCK_COOLDOWN_SECONDS))
            uptime = (successful / total * 100.0) if total > 0 else 0.0
            
            score = calculate_node_score(avg_lat, avg_loss, has_block, uptime)
            
            conn.execute("""
                INSERT OR REPLACE INTO node_scores (
                    network_type, category, proxy_name, calculated_score,
                    uptime_percent, avg_latency, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (net_type, category, proxy_name, score, uptime, int(avg_lat), now))
            
    conn.close()

def get_prioritized_proxies(
    candidate_proxies: List[str],
    network_type: str = "wifi",
    category: str = "general",
    db_path: str = DEFAULT_DB_PATH
) -> List[str]:
    """
    Returns candidate_proxies sorted by telemetry Score (lowest first).
    Proxies without history retain their relative positions at the tail.
    """
    if not os.path.exists(db_path):
        return candidate_proxies

    conn = get_db_connection(db_path)
    scores = {}
    try:
        cursor = conn.execute("""
            SELECT proxy_name, calculated_score, uptime_percent, avg_latency
            FROM node_scores
            WHERE network_type = ? AND category = ?
        """, (network_type, category))
        for row in cursor.fetchall():
            scores[row[0]] = row[1]
    except Exception:
        pass
    finally:
        conn.close()

    if not scores:
        return candidate_proxies

    # Sort: scored nodes by score, unscored nodes by original order after scored nodes
    def sort_key(p_name):
        if p_name in scores:
            return (0, scores[p_name])
        return (1, 0)

    return sorted(candidate_proxies, key=sort_key)
