@echo off
setlocal
cd /d "%~dp0"

rem ==== editeaza aici ====
set "ASTRA=C:\astra\astra.exe"
set "PORT=8080"
rem =======================

where py >nul 2>nul
if %errorlevel%==0 (
    py astra_web.py --astra "%ASTRA%" --port %PORT%
) else (
    python astra_web.py --astra "%ASTRA%" --port %PORT%
)
pause
