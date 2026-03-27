#!/bin/bash
# Varroda API indítása
# Futtatás: bash start_api.sh
cd "$(dirname "$0")"
nohup .venv/bin/python3 api.py > api.log 2>&1 &
echo "API elindítva, PID: $!, port: 5050"
echo "Log: $(pwd)/api.log"
