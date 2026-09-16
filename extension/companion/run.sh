#!/usr/bin/env bash
# Start the SnappyMake IMAP companion (standalone, stdlib-only).
#
# Optional overrides:
#   COMPANION_PORT=8799 COMPANION_HOST=127.0.0.1 \
#   ADSPOWER_BASE=http://local.adspower.net:50325 ADSPOWER_API_KEY=... \
#   IMAP_HOST=imap.gmail.com IMAP_PORT=993 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
exec "$PY" companion.py
