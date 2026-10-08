# -*- coding: utf-8 -*-
"""
Aegis Regional Block & Censorship Detector
Inspects HTTP health-check responses and body contents to identify
'Not available in your country', Cloudflare 1020, and GeoIP bans,
treating them as packet drops / failed probes.
"""

from typing import Tuple

BLOCK_PATTERNS = [
    "not available in your country",
    "unsupported_country",
    "access denied",
    "error code 1020",
    "blocked by cloudflare",
    "sorry, you have been blocked",
    "location not supported",
    "service unavailable in your region",
    "gemini пока не поддерживается",
    "не поддерживается в вашей стране",
    "пока не поддерживается"
]

def is_response_blocked(status_code: int, body_text: str = "") -> Tuple[bool, str]:
    """
    Evaluates HTTP status code and response body for regional blocks.
    Returns (is_blocked, reason).
    """
    if status_code in (403, 451, 1020):
        return (True, f"HTTP {status_code} Forbidden / Geo-Restricted")

    if body_text:
        lower_body = body_text.lower()
        for pattern in BLOCK_PATTERNS:
            if pattern in lower_body:
                return (True, f"Block pattern detected in body: '{pattern}'")

    return (False, "")

def evaluate_ping_result(status_code: int, latency_ms: int, body_text: str = "") -> Tuple[int, int, str]:
    """
    Processes health-check result:
    If blocked by geo-filtering, converts latency to -1 (treating as drop) and flags is_geo_blocked = 1.
    Returns (effective_latency_ms, is_geo_blocked, reason).
    """
    blocked, reason = is_response_blocked(status_code, body_text)
    if blocked:
        # Treat as failed probe (packet drop / infinite latency)
        return (-1, 1, reason)

    if status_code not in (200, 204, 301, 302):
        return (-1, 0, f"HTTP error {status_code}")

    return (latency_ms, 0, "OK")
