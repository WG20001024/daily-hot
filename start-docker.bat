@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title Daily Hot - Docker Manager

REM ============================================================
REM  Daily Hot - one-click Docker helper
REM  Requirements: Docker Desktop
REM                https://www.docker.com/products/docker-desktop/
REM ============================================================

where docker >nul 2>nul
if errorlevel 1 goto NODOCKER

docker info >nul 2>nul
if errorlevel 1 goto NOTRUNNING

docker compose version >nul 2>nul
if not errorlevel 1 goto SETV2
docker-compose version >nul 2>nul
if not errorlevel 1 goto SETV1
echo [ERROR] Docker is installed but "docker compose" was not found.
echo         Please update Docker Desktop to v4 or newer.
pause
exit /b 1

:SETV2
set "DC=docker compose"
goto MENU

:SETV1
set "DC=docker-compose"
goto MENU

:NODOCKER
echo.
echo [ERROR] Docker is NOT installed on this machine.
echo.
echo Please install Docker Desktop first:
echo     https://www.docker.com/products/docker-desktop/
echo.
echo After installing, launch Docker Desktop and wait until it says
echo "Engine running", then run this script again.
echo.
pause
exit /b 1

:NOTRUNNING
echo.
echo [ERROR] Docker is installed but not running.
echo.
echo Please start Docker Desktop and wait until the status shows
echo "Engine running", then run this script again.
echo.
pause
exit /b 1

:MENU
cls
echo ============================================================
echo   Daily Hot - Docker Manager
echo ============================================================
echo   1) Start      (build image + start containers)
echo   2) Stop       (containers stay configured, data kept)
echo   3) Restart
echo   4) Logs       (follow web container, Ctrl+C to exit)
echo   5) Rebuild    (force rebuild, no cache)
echo   6) Status
echo   7) Reset      (DELETE ALL DATA and start over)
echo   0) Exit
echo ============================================================
echo.
set /p CHOICE=Choose [0-7]: 

if "%CHOICE%"=="1" goto START
if "%CHOICE%"=="2" goto STOP
if "%CHOICE%"=="3" goto RESTART
if "%CHOICE%"=="4" goto LOGS
if "%CHOICE%"=="5" goto REBUILD
if "%CHOICE%"=="6" goto STATUS
if "%CHOICE%"=="7" goto RESET
if "%CHOICE%"=="0" goto END
goto MENU

:START
set "WEB_PORT=5000"
if exist .env (
    for /f "usebackq tokens=1,2 delims==" %%A in (`findstr /b /i "WEB_PORT=" .env`) do set "WEB_PORT=%%B"
)
echo.
echo Checking whether port %WEB_PORT% is free...
netstat -ano | findstr /r /c:":%WEB_PORT% " | findstr /i LISTENING >nul
if not errorlevel 1 goto PORTTAKEN
echo Port %WEB_PORT% is free.
goto DOBUILD

:PORTTAKEN
echo.
echo [WARNING] Port %WEB_PORT% is already used by another program.
echo          Most likely the local Flask app (python app.py) is running.
echo.
echo   [K] Stop the local app and continue on port %WEB_PORT%
echo   [C] Continue anyway on port %WEB_PORT%
echo   [M] Go back to menu (then edit WEB_PORT in .env)
echo.
set /p PCH=Choose [K/C/M]: 
if /i "%PCH%"=="K" goto KILLLOCAL
if /i "%PCH%"=="C" goto DOBUILD
goto MENU

:KILLLOCAL
echo.
echo Stopping local processes that listen on port %WEB_PORT% ...
for /f "usebackq tokens=5" %%P in (`netstat -ano ^| findstr /r /c:":%WEB_PORT% " ^| findstr /i LISTENING`) do taskkill /F /PID %%P >nul 2>nul
timeout /t 2 /nobreak >nul
echo Done.
goto DOBUILD

:DOBUILD
echo.
echo [1/2] Building image (first run takes a few minutes)...
%DC% build
if errorlevel 1 goto FAIL
echo.
echo [2/2] Starting containers...
%DC% up -d
if errorlevel 1 goto FAIL
echo.
echo Waiting for the service to become healthy...
timeout /t 10 /nobreak >nul
echo.
echo Done. Opening http://localhost:%WEB_PORT%
start "" http://localhost:%WEB_PORT%
echo.
echo Tip: if the page is blank, check logs with:  %DC% logs -f web
pause
goto MENU

:STOP
echo.
%DC% down
echo Stopped. Data is kept in the mysql-data volume.
pause
goto MENU

:RESTART
echo.
%DC% restart
pause
goto MENU

:LOGS
echo.
echo Following logs (press Ctrl+C to stop)...
%DC% logs -f web
pause
goto MENU

:REBUILD
echo.
echo Rebuilding without cache...
%DC% build --no-cache
if errorlevel 1 goto FAIL
%DC% up -d --force-recreate
if errorlevel 1 goto FAIL
echo Rebuild done.
pause
goto MENU

:STATUS
echo.
%DC% ps
echo.
echo ---- web health ----
docker inspect --format "{{.State.Health.Status}}" daily-hot-web 2>nul
pause
goto MENU

:RESET
echo.
echo [WARNING] This will DELETE ALL DATA in the database.
set /p CONF=Type YES to confirm: 
if /i not "%CONF%"=="YES" goto MENU
%DC% down -v
echo All containers and volumes removed.
pause
goto MENU

:FAIL
echo.
echo [ERROR] Command failed. See the message above.
echo Common fixes:
echo   - Port in use:        set WEB_PORT=5001 in .env
echo   - Build network error:
echo       docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .
pause
goto MENU

:END
exit /b 0
