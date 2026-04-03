@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title TH4 Shop-Bot v1.1.0

set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PIP_ROOT_USER_ACTION=ignore"

rem ================================================================
rem  TH4 Shop-Bot v1.1.0  --  The Hell 4 Gold-Find Item Scanner
rem  Fully portable. Unzip anywhere, run INSTALL.bat, done.
rem ================================================================

set "SRC_DIR=%~dp0"
if "%SRC_DIR:~-1%"=="\" set "SRC_DIR=%SRC_DIR:~0,-1%"

if defined TH4BOT_INSTALL_DIR (
    set "INSTALL_DIR=%TH4BOT_INSTALL_DIR%"
) else if not "%~1"=="" (
    set "INSTALL_DIR=%~1"
) else (
    set "INSTALL_DIR=%SRC_DIR%"
)

set "CONDA_DIR=%INSTALL_DIR%\_conda"
set "ENV_DIR=%INSTALL_DIR%\env"
set "TOOLS_DIR=%INSTALL_DIR%\_tools"
set "BACKUPS_DIR=%TOOLS_DIR%\backups"
set "LOG=%TOOLS_DIR%\last_run.log"
set "PINFILE=%TOOLS_DIR%\pinned.txt"

set "CONDA_EXE=%CONDA_DIR%\Scripts\conda.exe"
set "ENV_PYTHON=%ENV_DIR%\python.exe"

set "APP_VERSION=1.1.0"
set "PYTHON_VER=3.11"

set "PIN_PILLOW=10.3.0"
set "PIN_NUMPY=1.26.4"
set "PIN_KEYBOARD=0.13.5"
set "PIN_PYAUTOGUI=0.9.54"
set "PIN_RAPIDOCR=1.3.22"
set "PIN_ONNXRUNTIME=1.19.2"

if not exist "%INSTALL_DIR%"  mkdir "%INSTALL_DIR%"
if not exist "%TOOLS_DIR%"    mkdir "%TOOLS_DIR%"
if not exist "%BACKUPS_DIR%"  mkdir "%BACKUPS_DIR%"

call :timestamp TS_START
echo [%TS_START%] TH4 Shop-Bot installer v%APP_VERSION% > "%LOG%"
echo Repo:    %SRC_DIR%    >> "%LOG%"
echo Install: %INSTALL_DIR% >> "%LOG%"

rem ================================================================
rem  MAIN MENU
rem ================================================================
:MENU
cls
echo.
echo  +--------------------------------------------------------------+
echo  ^|  TH4 Shop-Bot v%APP_VERSION%  --  The Hell 4 Gold-Find Scanner   ^|
echo  +--------------------------------------------------------------+
echo  ^|   1)  Install    -- First-time setup                        ^|
echo  ^|   2)  Launch     -- Start the Shop-Bot                      ^|
echo  ^|   3)  Update     -- Upgrade packages                        ^|
echo  ^|   4)  Repair     -- Fix / restore environment               ^|
echo  ^|   5)  About      -- How it works                            ^|
echo  ^|   6)  Exit                                                   ^|
echo  +--------------------------------------------------------------+
if exist "%ENV_PYTHON%" (
    echo  Status: [INSTALLED]   env at %INSTALL_DIR%\env\
) else (
    echo  Status: [NOT INSTALLED]  -- run option 1 first
)
echo  Repo:    %SRC_DIR%
echo  Install: %INSTALL_DIR%
echo  +--------------------------------------------------------------+
echo.

set "CHOICE="
set /p "CHOICE=  Enter option (1-6): "
echo.

if "!CHOICE!"=="1" goto :DO_INSTALL
if "!CHOICE!"=="2" goto :DO_LAUNCH
if "!CHOICE!"=="3" goto :DO_UPDATE
if "!CHOICE!"=="4" goto :DO_REPAIR
if "!CHOICE!"=="5" goto :DO_ABOUT
if "!CHOICE!"=="6" goto :EXIT
echo  Invalid choice.
timeout /t 2 >nul
goto :MENU

:DO_INSTALL
echo  --- INSTALL ---
echo.
if exist "%ENV_PYTHON%" (
    set "REINSTALL="
    set /p "REINSTALL=  Reinstall from scratch? Y or N: "
    if /I "!REINSTALL!"=="Y" (
        rd /s /q "%ENV_DIR%"
    ) else (
        echo  Skipped. Use option 3 to update.
        call :pause_return
        goto :MENU
    )
)
call :step_conda
if errorlevel 1 goto :install_failed
call :step_env
if errorlevel 1 goto :install_failed
call :step_packages
if errorlevel 1 goto :install_failed
echo.
echo  +--------------------------------------------------------------+
echo  ^|  INSTALL COMPLETE!                                           ^|
echo  ^|  1. Open The Hell 4 and enter Griswold's shop.              ^|
echo  ^|  2. Launch bot (option 2), click Calibrate, set grid.      ^|
echo  ^|  3. Press START (F6) and let it scan!                       ^|
echo  ^|  Hotkeys: F6=Start/Stop  F7=Continue  F8=Pause  ESC=Stop  ^|
echo  +--------------------------------------------------------------+
call :pause_return
goto :MENU

:install_failed
echo.
echo  INSTALL DID NOT COMPLETE. Check messages above.
echo  Log: %LOG%
call :pause_return
goto :MENU

:DO_LAUNCH
echo  --- LAUNCH ---
if not exist "%ENV_PYTHON%" (
    echo  [ERROR] Run option 1 first.
    call :pause_return
    goto :MENU
)
if not exist "%SRC_DIR%\src\main.py" (
    echo  [ERROR] src\main.py not found.
    call :pause_return
    goto :MENU
)
"%ENV_PYTHON%" --version
echo.
echo  Hotkeys: F6=Start/Stop  F7=Continue  F8=Pause  ESC=Emergency
echo.
"%ENV_PYTHON%" "%SRC_DIR%\src\main.py"
set "EC=%ERRORLEVEL%"
echo.
if %EC% NEQ 0 (
    echo  [ERROR] Shop-Bot exited with code %EC%. Check logs\ folder.
) else (
    echo  Shop-Bot exited normally.
)
call :pause_return
goto :MENU

:DO_UPDATE
echo  --- UPDATE ---
if not exist "%ENV_PYTHON%" (
    echo  [ERROR] Run option 1 first.
    call :pause_return
    goto :MENU
)
call :write_pinfile
"%ENV_PYTHON%" -m pip install --prefer-binary --upgrade -r "%PINFILE%"
echo  Update complete.
call :pause_return
goto :MENU

:DO_REPAIR
cls
echo.
echo  +--------------------------------------------------------------+
echo  ^|  REPAIR                                                      ^|
echo  ^|   1)  Reinstall packages                                     ^|
echo  ^|   2)  Full clean rebuild                                     ^|
echo  ^|   3)  Back                                                   ^|
echo  +--------------------------------------------------------------+
echo.
set "RCHOICE="
set /p "RCHOICE=  Enter option (1-3): "
if "!RCHOICE!"=="1" (
    call :write_pinfile
    "%ENV_PYTHON%" -m pip install --prefer-binary --force-reinstall -r "%PINFILE%"
    call :pause_return
    goto :MENU
)
if "!RCHOICE!"=="2" (
    set "CONFIRM="
    set /p "CONFIRM=  Type YES to confirm full rebuild: "
    if /I "!CONFIRM!"=="YES" (
        if exist "%ENV_DIR%" rd /s /q "%ENV_DIR%"
        call :step_env
        call :step_packages
    ) else (
        echo  Cancelled.
    )
    call :pause_return
    goto :MENU
)
goto :MENU

:DO_ABOUT
cls
echo.
echo  TH4 Shop-Bot v%APP_VERSION%
echo  Scans Griswold's shop for items with flat +to Gold Find.
echo  Compares against equipped item (via ALT comparison tooltip).
echo  Stops only when shop item beats or matches equipped.
echo  Dual GF items (flat + %%) require mandatory manual dismiss.
echo  Ignore list bypasses comparison for specific items.
echo.
echo  Hotkeys: F6=Start/Stop  F7=Continue  F8=Pause  ESC=Emergency
echo.
call :pause_return
goto :MENU

:EXIT
echo  Goodbye!
timeout /t 1 >nul
exit /b 0

:step_conda
echo  [1/3] Conda
if exist "%CONDA_EXE%" ( echo        Found local conda. & exit /b 0 )
for %%X in (conda.exe conda.bat) do (
    for /f "delims=" %%C in ('where %%X 2^>nul') do (
        set "CONDA_EXE=%%C" & echo        System conda: %%C & exit /b 0
    )
)
echo        Downloading Miniconda3...
set "MINI_URL=https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
set "MINI_INST=%TOOLS_DIR%\miniconda_installer.exe"
call :grab "%MINI_INST%" "%MINI_URL%"
if not exist "%MINI_INST%" ( echo  [ERROR] Miniconda download failed. & exit /b 1 )
start /wait "" "%MINI_INST%" /InstallationType=JustMe /AddToPath=0 /RegisterPython=0 /S /D=%CONDA_DIR%
del "%MINI_INST%" >nul 2>&1
if not exist "%CONDA_EXE%" ( echo  [ERROR] Miniconda install failed. & exit /b 1 )
exit /b 0

:step_env
echo  [2/3] Python Environment
if exist "%ENV_PYTHON%" ( echo        Already exists. & exit /b 0 )
"%CONDA_EXE%" create --prefix "%ENV_DIR%" python=%PYTHON_VER% -y
if errorlevel 1 ( echo  [ERROR] conda create failed. & exit /b 1 )
if not exist "%ENV_PYTHON%" ( echo  [ERROR] python.exe missing after create. & exit /b 1 )
exit /b 0

:step_packages
echo  [3/3] Packages
"%ENV_PYTHON%" -m pip install --quiet -U pip setuptools wheel
call :write_pinfile
"%ENV_PYTHON%" -m pip install --prefer-binary --upgrade-strategy only-if-needed -r "%PINFILE%"
if errorlevel 1 (
    for /f "usebackq tokens=*" %%L in ("%PINFILE%") do (
        "%ENV_PYTHON%" -m pip install --prefer-binary "%%L" 2>>"%LOG%"
    )
)
echo        Packages done.
exit /b 0

:write_pinfile
(
    echo Pillow^>=%PIN_PILLOW%
    echo numpy^>=%PIN_NUMPY%
    echo keyboard^>=%PIN_KEYBOARD%
    echo pyautogui^>=%PIN_PYAUTOGUI%
    echo rapidocr-onnxruntime^>=%PIN_RAPIDOCR%
    echo onnxruntime^>=%PIN_ONNXRUNTIME%
) > "%PINFILE%"
exit /b 0

:grab
set "_G_DST=%~1"
set "_G_URL=%~2"
if not exist "%~dp1" mkdir "%~dp1"
if exist "%_G_DST%" ( echo        %~nx1 already present. & exit /b 0 )
curl -L -o "%_G_DST%" "%_G_URL%" --ssl-no-revoke --progress-bar
if errorlevel 1 ( echo        [ERROR] Download failed: %~nx1 & exit /b 1 )
exit /b 0

:pause_return
echo.
pause
exit /b 0

:timestamp
for /f "delims=" %%T in ('powershell -NoLogo -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "%~1=%%T"
exit /b 0
