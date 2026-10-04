#!/usr/bin/env bash
# ========================================================
# Настройка ежечасного обновления подписок Aegis через cron
# ========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/sync.py"
CRON_JOB="0 * * * * $(which python3 || echo python3) \"$SCRIPT_PATH\" >/dev/null 2>&1"

(crontab -l 2>/dev/null | grep -Fv "$SCRIPT_PATH" ; echo "$CRON_JOB") | crontab -

echo "[OK] Ежечасное автообновление Aegis настроено через cron:"
echo "     $CRON_JOB"
