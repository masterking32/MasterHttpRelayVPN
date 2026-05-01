@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM -------- MasterHttpRelayVPN one-click launcher (Windows) --------
REM Creates a local virtualenv, installs deps, runs the setup wizard
REM if needed, then starts the proxy. Also checks and installs CA cert
REM if not already trusted.

set "VENV_DIR=.venv"
set "PY="

where py >nul 2>&1
if !errorlevel!==0 (
    set "PY=py -3"
) else (
    where python >nul 2>&1
    if !errorlevel!==0 (
        set "PY=python"
    )
)

if "%PY%"=="" (
    echo [X] Python 3.10+ was not found on PATH.
    echo     Install from https://www.python.org/downloads/ and re-run this script.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [*] Creating virtual environment in %VENV_DIR% ...
    %PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [X] Failed to create virtualenv.
        pause
        exit /b 1
    )
)

set "VPY=%VENV_DIR%\Scripts\python.exe"

echo [*] Installing dependencies ...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip >nul
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo [!] PyPI install failed. Retrying via runflare mirror ...
    "%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt ^
        -i https://mirror-pypi.runflare.com/simple/ ^
        --trusted-host mirror-pypi.runflare.com
    if errorlevel 1 (
        echo [X] Could not install dependencies.
        pause
        exit /b 1
    )
)

if not exist "config.json" (
    echo [*] No config.json found — launching setup wizard ...
    "%VPY%" setup.py
    if errorlevel 1 (
        echo [X] Setup cancelled.
        pause
        exit /b 1
    )
)

REM -------- Check for uninstall flag --------
echo %* | findstr /C:"--uninstall-cert" >nul
if not errorlevel 1 (
    echo [*] Uninstalling CA certificate ...
    "%VPY%" main.py --uninstall-cert
    exit /b %errorlevel%
)

REM -------- Check for adblock update flag --------
echo %* | findstr /C:"--update-adblock" >nul
if not errorlevel 1 (
    echo.
    echo [*] Force-refreshing adblock blocklists ...
    echo.
    "%VPY%" main.py --update-adblock
    set "RC=%errorlevel%"
    if "!RC!"=="0" (
        echo.
        echo [+] Adblock lists updated successfully.
    ) else (
        echo.
        echo [!] Adblock update failed. Check the output above.
    )
    pause
    exit /b !RC!
)

REM -------- Auto-update check (skip with --skip-update) --------
set "_SKIP_UPDATE=0"
echo %* | findstr /C:"--skip-update" >nul
if not errorlevel 1 set "_SKIP_UPDATE=1"

if "!_SKIP_UPDATE!"=="0" (
    echo [*] Checking for updates ...
    "%VPY%" main.py --update
    set "UPDATE_RC=!errorlevel!"
    if "!UPDATE_RC!"=="2" (
        echo.
        echo [*] Update applied -- re-installing dependencies ...
        "%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
        echo [+] Ready. Starting with updated version ...
        echo.
    ) else if "!UPDATE_RC!"=="1" (
        echo [!] Update failed -- starting with current version.
    )
)

REM Strip --skip-update before passing args to main.py
set "_FWDARGS=%*"
set "_FWDARGS=!_FWDARGS:--skip-update=!"

echo.
echo [*] Starting MasterHttpRelayVPN ...
echo.
"%VPY%" main.py !_FWDARGS!
set "RC=!errorlevel!"
if not "!RC!"=="0" pause
exit /b !RC!
