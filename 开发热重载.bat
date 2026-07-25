@echo off
setlocal
REM ============================================================
REM  Dev hot-reload loop (debug build, console visible).
REM  Save a source file -> auto rebuild -> auto restart the app.
REM  One-time setup: cargo install cargo-watch
REM  Uses the lld linker via .cargo/config.toml for faster relinks.
REM ============================================================

REM Fallback if cargo is not on PATH (fresh Rust install / stale shell).
where cargo >nul 2>nul
if errorlevel 1 set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

REM -c clears the console between runs; -x run rebuilds and launches.
cargo watch -c -x run

endlocal
