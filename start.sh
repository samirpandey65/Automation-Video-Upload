#!/usr/bin/env bash
cd "$(dirname "$0")"
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "ERROR: Do not run start.sh with sudo/root."
    echo "Run: ./start.sh"
    echo "For systemd, use the included install_systemd.sh script."
    exit 1
fi

# Repair files that may have been created as root by an earlier installation.
mkdir -p data credentials/tokens logs
chmod 700 data credentials/tokens logs 2>/dev/null || true

if [ -f data/.session_secret ]; then
    chmod 600 data/.session_secret 2>/dev/null || true
fi

if [ -f ".venv/bin/python" ]; then
    exec ./.venv/bin/python run.py
elif command -v python3 >/dev/null 2>&1; then
    echo "Virtual environment not found. Setting up..."
    python3 -m venv .venv
    ./.venv/bin/python -m pip install -r requirements.txt
    exec ./.venv/bin/python run.py
else
    echo "Python 3 is required."
    exit 1
fi
