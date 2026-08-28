#!/bin/sh
echo "[AIGCPannel] AIGCPannel is the product. DashBox is the finishing engine. Canonical: ./start-aigcpannel.sh"
exec python3 "$(dirname "$0")/start-aigcpannel.py" "$@"
