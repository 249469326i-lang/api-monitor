@echo off
echo ========================================
echo API Monitor Rebuild Script
echo ========================================
echo.

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

echo [2/4] Removing old executable...
if exist "API-Monitor.exe" (
    del /F /Q "API-Monitor.exe"
    echo Old file deleted
) else (
    echo No old file
)
echo.

echo [3/4] Building new executable...
echo This may take 30-60 seconds...
pyinstaller --noconfirm --clean API-Monitor.spec
echo.

echo [4/4] Moving files...
set /a move_attempts=0
:move_retry
if exist "dist\API-Monitor.exe" (
    move /Y "dist\API-Monitor.exe" "API-Monitor.exe" >nul 2>&1
    if exist "API-Monitor.exe" goto move_done
)
set /a move_attempts+=1
if %move_attempts% lss 15 (
    echo move retry (%move_attempts%/15), waiting 1s...
    ping -n 2 127.0.0.1 >nul 2>&1
    goto move_retry
)
echo Build FAILED: cannot move dist\API-Monitor.exe to API-Monitor.exe
if "%CI%"=="" pause
exit /b 1

:move_done
echo Build SUCCESS!
echo.
echo ========================================
for /f "delims=" %%v in ('python -c "from main import __version__; print(__version__)" 2^>nul') do echo API Monitor v%%v
echo ========================================
echo.
echo Starting new version...
start "" "API-Monitor.exe"

echo Cleaning temp files...
if exist "build" rmdir /S /Q "build" >nul 2>&1
if exist "dist" rmdir /S /Q "dist" >nul 2>&1
if exist "__pycache__" rmdir /S /Q "__pycache__" >nul 2>&1
