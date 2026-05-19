#!/usr/bin/env bash

set -euo pipefail

echo "=== docker-entrypoint.sh started (user: $(whoami)) ===" >&2

# ── DELi config setup ─────────────────────────────────────────
# /root/.deli is a FILE (not a directory) in this version of deli
CONFIG_FILE="/root/.deli"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "  deli config : initializing..." >&2
    deli config init --overwrite
else
    echo "  deli config : already exists, skipping init" >&2
fi

echo "  deli config : contents:" >&2
cat "$CONFIG_FILE" >&2
echo "  deli config : OK" >&2

# ── Verify baked-in data is present ───────────────────────────
echo "  deli data   : checking /opt/deli_data/buildingblocks/ ..." >&2
if [ -z "$(ls -A /opt/deli_data/buildingblocks/ 2>/dev/null)" ]; then
    echo "  deli data   : WARNING — /opt/deli_data/buildingblocks/ is empty!" >&2
else
    echo "  deli data   : OK ($(ls /opt/deli_data/buildingblocks/ | wc -l) items found)" >&2
fi

exec "$@"