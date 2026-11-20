@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install. Then run this again.
  pause
  exit /b 1
)
if not exist .venv (
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Install failed. Copy the red error text above and send it to Claude.
  pause
  exit /b 1
)
echo.
echo Done! Double-click create-shortcut.bat to put Live Subs on your desktop.
pause
