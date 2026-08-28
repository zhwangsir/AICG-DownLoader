#!/bin/sh
echo "[AIGCPannel] AIGCPannel is the product. DashBox engine on :8080."
exec python3 "$(dirname "$0")/start-aigcpannel.py" "$@"
