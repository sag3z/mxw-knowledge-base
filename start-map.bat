@echo off
chcp 65001 >nul
rem Поиск по смыслу берёт эмбеддинги через OpenCode API Manager — запускаем его, если не запущен
tasklist /FI "IMAGENAME eq OpenCodeAPIManager.exe" | find /I "OpenCodeAPIManager.exe" >nul || start "" "%LOCALAPPDATA%\Programs\OpenCodeAPIManager\OpenCodeAPIManager.exe"
cd /d "%~dp0"
set PYTHONUTF8=1
rem Сервер уже работает — просто открываем карту
curl.exe -s -o nul -m 3 http://127.0.0.1:8765/api/graph && (start "" http://127.0.0.1:8765 & exit /b)
start "MXW сервер (не закрывай, пока пользуешься картой)" /min python -u app\server.py 8765
rem ждём, пока сервер поднимется (до ~30 с), потом открываем браузер
for /L %%i in (1,1,30) do (
  curl.exe -s -o nul -m 2 http://127.0.0.1:8765/api/graph && goto open
  timeout /t 1 /nobreak >nul
)
:open
start "" http://127.0.0.1:8765
