@echo off
rem MXW map: starts the local server (app\server.py, port 8765) and opens the browser.
rem ASCII only + CRLF line endings: cmd.exe misreads UTF-8 / LF batch files.
cd /d "%~dp0"
set PYTHONUTF8=1
rem Server already running - just open the map
curl.exe -s -o nul -m 3 http://127.0.0.1:8765/api/graph && (start "" http://127.0.0.1:8765 & exit /b)
where python >nul 2>nul || (echo Python not found. Install Python 3 from python.org & pause & exit /b 1)
start "MXW server - keep this window open while using the map" /min python -u app\server.py 8765
rem wait for the server (up to ~30 s), then open the browser
for /L %%i in (1,1,30) do (
  curl.exe -s -o nul -m 2 http://127.0.0.1:8765/api/graph && goto open
  timeout /t 1 /nobreak >nul
)
echo Server did not respond in 30 s - see the "MXW server" window for errors.
pause
exit /b 1
:open
start "" http://127.0.0.1:8765
