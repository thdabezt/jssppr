@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m jssppr %*
    exit /b %errorlevel%
)

echo The project virtual environment was not found:
echo   %~dp0.venv\Scripts\python.exe
echo.
echo Restore or create the project .venv before starting JSSPPR.
pause
exit /b 1
