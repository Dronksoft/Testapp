@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title GoldSense v1.1.0

set "SRC_DIR=%~dp0"
if "%SRC_DIR:~-1%"=="\" set "SRC_DIR=%SRC_DIR:~0,-1%"

if defined GS_INSTALL_DIR (
    set "INSTALL_DIR=%GS_INSTALL_DIR%"
) else (
    set "INSTALL_DIR=%SRC_DIR%"
)

set "ENV_PYTHON=%INSTALL_DIR%\env\python.exe"
set "MAIN_PY=%SRC_DIR%\src\main.py"

echo.
echo  GoldSense v1.1.0  --  Merchant Inventory Inspector
echo  Repo:    %SRC_DIR%
echo  Python:  %ENV_PYTHON%
echo.

if not exist "%ENV_PYTHON%" (
    echo  [ERROR] Environment not found. Run SETUP.bat option 1 first.
    set "OPEN="
    set /p "OPEN=  Open SETUP.bat now? Y or N: "
    if /I "!OPEN!"=="Y" start "" "%SRC_DIR%\INSTALL.bat"
    goto :done
)
if not exist "%MAIN_PY%" (
    echo  [ERROR] src\main.py not found.
    goto :done
)

"%ENV_PYTHON%" --version
echo.
echo  Hotkeys: F6=Begin/Halt  F7=Next  F8=Hold  ESC=Halt
echo.
"%ENV_PYTHON%" "%MAIN_PY%"
set "EC=%ERRORLEVEL%"
echo.
if %EC% NEQ 0 (
    echo  [ERROR] Exited with code %EC%. Check logs\ folder.
) else (
    echo  Exited normally.
)

:done
echo.
pause
