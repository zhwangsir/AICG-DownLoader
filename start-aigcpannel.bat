@echo off
cd /d "%~dp0"
echo [AIGCPannel] AIGCPannel is the product. Finishing engine on :8080.
py -3 start-aigcpannel.py %*
