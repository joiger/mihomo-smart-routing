# Teamwork Project Prompt — Draft

> Status: Completed & Verified
> Goal: Rebrand to Aegis, Secret Gist deployment, FlClash UI clean up, and Category Constructor
> Requested team: Small, focused team

This is a single self-contained fix; keep it small and focused.
Rebrand the project to **Aegis** and implement, audit, optimize, document, and release the 100% private Secret Gist synchronization architecture for the Mihomo Smart Routing project.

Working directory: `C:\Users\hitsugi ni ochita\.gemini\antigravity\scratch\mihomo-smart-routing`
Integrity mode: development

## Requirements

### R1. Rebranding to Aegis
- Update project branding across code, documentation, headers, and release to **Aegis** (`Aegis — Smart Routing & Multi-Subscription Merger`).

### R2. 100% Private Secret Gist Deployment & Log Masking
- The GitHub Actions workflow (`.github/workflows/sync-hourly.yml`) must deploy the generated Mihomo configuration exclusively to a private GitHub Secret Gist (`public: false`).
- Under no circumstances should the Gist ID, subscription URLs, or VLESS UUIDs be exposed in the public repository, branches, or unmasked in GitHub Actions workflow step summaries or console logs.
- The workflow must use `::add-mask::` on sensitive runtime tokens.

### R3. High-Performance Parallel Fetch & Zero-Downtime Resilience
- Refactor subscription retrieval in `sync.py` to use `concurrent.futures.ThreadPoolExecutor` for concurrent asynchronous fetching across providers (reducing total sync time by ~70%).
- Implement zero-downtime resilience: if one provider fails (timeout, 5xx error, network drop), `sync.py` must log a graceful warning and build the configuration from all remaining healthy providers rather than aborting execution.

### R4. Auto-Fallback Country Priority & Fingerprint Deduplication
- Auto-Fallback priority:
  - **Tier 1:** Finland 🇫🇮 and Sweden 🇸🇪 (lowest physical latency ~30–45 ms).
  - **Tier 2:** Transit bridges (`Москва → EU`) and DPI bypass nodes (`Обход`) for mobile LTE survival under TSPU white-lists.
  - **Tier 3:** Germany 🇩🇪, Netherlands 🇳🇱, Estonia 🇪🇪, Poland 🇵🇱.
  - **Tier 4:** Rest of Europe / regional (UK, France, Czechia, Turkey, Kazakhstan).
  - **Tier 5:** USA 🇺🇸 and distant destinations.
- Implement fingerprint-based node deduplication (`server + port + uuid`) to eliminate redundant proxy entries.

### R5. Security Hardening & Strict TLS Verification
- Enforce strict TLS validation on all outbound HTTP requests in `sync.py` (preventing MitM attacks).
- Add automated pre-commit scanning of git diff to guarantee no personal domains (`*.desiderius.ru`), private tokens, or credentials are accidentally tracked.

### R6. Complete Documentation (README.md) & Release Publication (v1.2.0)
- Update `README.md` with Aegis branding and step-by-step setup for new users (generating classic GitHub PAT with `gist` scope, adding `SUBSCRIPTIONS`, `GIST_TOKEN`, and `GIST_ID` secrets).
- Accurate UI instructions for importing the unversioned raw Secret Gist URL into FlClash (Android/iOS) and Clash Verge Rev (PC).
- Document FlClash clean UI layout and modular Category Constructor.
- Publish GitHub Release `v1.2.0` with a comprehensive changelog.

### R7. FlClash UI Simplification & Modular Category Constructor
- Eliminate nested child groups (`Auto-AI-Fallback`, `Auto-Media-UrlTest`, etc.) from FlClash UI.
- Direct pin connectors (`Auto-Fallback`, `Auto-UrlTest`, `DIRECT`) at the top of each category selector followed by nodes.
- Modular `Category Constructor` enabling custom routing categories in `config.json`.

## Acceptance Criteria

### Security & Privacy
- [x] No `release` branch exists in the repository.
- [x] Secret Gist is created with `public: false` and does not appear on the user's public profile.
- [x] Automated git diff scan confirms zero leaks of personal domains or credentials (`check_diff.py` passes).
- [x] Workflow logs and Step Summary do not expose unmasked Gist IDs or proxy tokens.

### Performance & Resilience
- [x] Multiple subscriptions are fetched concurrently using `ThreadPoolExecutor` within a 15-second total timeout.
- [x] A simulated failure of one provider still generates a valid, working config from the remaining providers.
- [x] Proxies are deduplicated by `(server, port, uuid)`.
- [x] Fallback priorities strictly match Tier 1 (FI/SE) ➔ Tier 2 (Relay/Bypass) ➔ Tier 3 (DE/NL/EE/PL) ➔ Tier 4 (Other EU) ➔ Tier 5 (USA).

### UI & Architecture (FlClash & Category Constructor)
- [x] Redundant child groups (`Auto-AI-Fallback`, `Auto-Media-UrlTest`, `Auto-Discord-UrlTest`) eliminated from FlClash.
- [x] Category groups provide pinned `Auto-Fallback`, `Auto-UrlTest`, `DIRECT` connectors followed directly by prioritized locations.
- [x] Modular Category Constructor implemented in `sync.py` and documented in `config.example.json` and `README.md`.

### Quality & Delivery
- [x] `sync.py` passes syntax, type consistency, and lint checks with exit code 0.
- [x] The generated YAML passes `verge-mihomo -t` configuration testing without schema errors (validated in 193ms).
- [x] `README.md` is updated with **Aegis** branding, formatted cleanly, and reflects the Secret Gist and Constructor flow.
- [x] Release `v1.2.0` is published and updated on GitHub with complete release notes.

---
*Status: Completed & Verified*
