#!/usr/bin/env bash
# Aegis — Smart Routing & Multi-Subscription Merger
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
python3 "$DIR/sync.py"
