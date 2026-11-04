@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" install.py
if errorlevel 1 goto failed
echo.
echo Setup finished. Launch Start.vbs and enable Mumble RU in Mumble settings.
pause
exit /b 0
:failed
echo.
echo Setup failed. Requires Python 3.11 x64 with tkinter and Internet access.
pause
exit /b 1
