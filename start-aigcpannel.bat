@echo off
cd /d "%~dp0"
echo [DashBox] start-aigcpannel is a thin wrapper. DashBox is the product.
py -3 start-dashbox.py %*
