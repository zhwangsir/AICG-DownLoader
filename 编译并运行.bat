@echo off
cd /d "%~dp0"

rem --- locate cargo (PATH first, then default rustup location) ---
set "CARGO="
where cargo >nul 2>nul && set "CARGO=cargo"
if not defined CARGO if exist "%USERPROFILE%\.cargo\bin\cargo.exe" set "CARGO=%USERPROFILE%\.cargo\bin\cargo.exe"

if not defined CARGO (
  echo [ERROR] cargo not found.
  echo   Tried PATH and "%USERPROFILE%\.cargo\bin\cargo.exe"
  echo   Install Rust from https://rustup.rs, REBOOT, then run again.
  echo.
  pause
  exit /b 1
)

echo Using cargo: %CARGO%
"%CARGO%" --version
echo.
echo Building release. First time downloads ~500 deps, please wait a few minutes...
echo.
"%CARGO%" build --release
if errorlevel 1 (
  echo.
  echo [BUILD FAILED] Please send the error text above to Claude.
  echo   Common cause: missing MSVC C++ Build Tools (link.exe).
  pause
  exit /b 1
)

echo.
echo [OK] Build finished. Launching the downloader...
copy /y config.json "target\release\config.json" >nul 2>nul
start "" "target\release\comfy-downloader.exe"
echo You can close this window now.
timeout /t 8 >nul
