# Aegis — Development & Agent Guidelines

## 1. Project Overview
**Aegis** is an intelligent, high-availability multi-subscription aggregator and dynamic routing engine built for **Mihomo (Clash.Meta)** clients (FlClash, Clash Verge, etc.). It automates node health monitoring, deduplication, latency scoring, and tiered failover (Tiers 1–5).

- **Repository Root**: `c:\Users\hitsugi ni ochita\Documents\antigravity\router`
- **Core Engine**: `sync.py` (fetching, parsing, deduplication, YAML config generation)
- **Telemetry Layer**: `telemetry_db.py` (SQLite scoring, history tracking, latency metrics)
- **Detection**: `geo_block_detector.py` (heuristic detection of geo-blocks and censored domains)
- **Documentation & Artifacts**: `docs/`

---

## 2. Codebase Memory & Graph-First Navigation
This project is fully indexed in **Codebase Memory**:
- **AST & Symbols**: Parsed via Tree-sitter (Python, YAML, Bash, HTML).
- **Interactive 3D UI**: `http://localhost:9749`
- **Agent Workflow**: Prefer semantic graph queries (`get_architecture`, `search_graph`, `trace_path`, `detect_changes`) over heavy full-file dumps.

---

## 3. Core Architecture & Routing Tiers
- **Tier 1 (Premium / Direct)**: Low-latency direct nodes (~30–45ms).
- **Tier 2 (Anti-DPI / Relay)**: Shadowsocks/VLESS anti-censorship relays for mobile/LTE failover.
- **Tier 3 (Service Specific)**: Rule-routed groups for AI (Claude, OpenAI), Media, Banking, Dev.
- **Tier 4 (Regional Fallbacks)**: Country-specific pools (EU, US, TR, JP, etc.).
- **Tier 5 (Global Catch-All)**: Auto-fallback pool with zero-downtime guarantees.

---

## 4. Coding & Security Rules
- **Zero-Downtime Guarantee**: If upstream subscription fetch fails (5xx, timeout, invalid format), Aegis must gracefully fall back to cached proxies without breaking downstream clients.
- **Secret Hygiene**: NEVER hardcode private subscription tokens, Gist API keys, or personal server passwords into git-tracked files. Use `config.json` / environment variables.
- **Test Integrity**: Always run `python -m unittest discover tests` and `python test_telemetry.py` before finalizing changes.
