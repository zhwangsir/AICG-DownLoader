#!/bin/sh
echo "[DashBox] DashBox is the user-facing product. Delegating to start-dashbox."
exec python3 "$(dirname "$0")/start-dashbox.py" "$@"
