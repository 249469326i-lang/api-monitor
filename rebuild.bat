@echo off
echo ========================================
echo API Monitor Rebuild Script
echo ========================================
echo.

REM Stop running process
echo [1/4] Checking for running process...
tasklist | find /I "API-Monitor.exe" >nul
if %errorlevel%==0 (
    echo Found running process, stopping...
    taskkill /F /IM "API-Monitor.exe" >nul 2>&1
    timeout /t 2 /nobreak >nul
    echo Stopped
) else (
    echo No running process
)
echo.

REM Delete old exe
echo [2/4] Removing old executable...
if exist "API-Monitor.exe" (
    del /F /Q "API-Monitor.exe"
    echo Old file deleted
) else (
    echo No old file
)
echo.

REM Rebuild with PyInstaller
echo [3/4] Building new executable...
echo This may take 30-60 seconds...
pyinstaller --noconfirm --clean CC-Switch-Monitor.spec
echo.

REM Move result
echo [4/4] Moving files...
if exist "dist\API-Monitor.exe" (
    move /Y "dist\API-Monitor.exe" "API-Monitor.exe" >nul
    echo Build SUCCESS!
    echo.
    echo ========================================
    echo API Monitor v3.0.0
    echo ========================================
    echo.
    echo Starting new version...
    start "" "API-Monitor.exe"
) else (
    echo Build FAILED, please check error messages
    echo.
    pause
)

REM Cleanup temp files
if exist "build" rmdir /S /Q "build" >nul 2>&1
if exist "dist" rmdir /S /Q "dist" >nul 2>&1
if exist "__pycache__" rmdir /S /Q "__pycache__" >nul 2>&1
