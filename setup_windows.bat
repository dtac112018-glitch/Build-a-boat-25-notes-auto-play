@echo off
setlocal
cd /d "%~dp0"
echo Setting up Speaker Cue Mapper (Windows)...
where py >nul 2>nul
if errorlevel 1 goto no_python
if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
set "PYTHON=%~dp0.venv\Scripts\python.exe"
echo Checking yt-dlp...
"%PYTHON%" -c "import yt_dlp" >nul 2>nul
if errorlevel 1 (
  echo yt-dlp is missing. Installing it into the app environment...
  "%PYTHON%" -m pip install --upgrade pip
  if errorlevel 1 goto failed
  "%PYTHON%" -m pip install "yt-dlp[default]"
  if errorlevel 1 goto failed
) else (
  echo yt-dlp is already installed; skipping that installation.
)
echo Checking remaining app dependencies...
"%PYTHON%" -m pip install -r requirements_windows.txt
if errorlevel 1 goto failed
"%PYTHON%" -c "import tkinter, numpy, mido, imageio_ffmpeg, yt_dlp" >nul 2>nul
if errorlevel 1 goto missing_modules
echo.
echo Setup complete. Double-click run_windows.bat.
pause
exit /b 0
:no_python
echo Python 3 launcher not found. Install Python 3 from https://www.python.org/downloads/windows/ with Tcl/Tk enabled.
pause
exit /b 1
:missing_modules
echo Required imports failed. Check setup output and confirm Python was installed with Tcl/Tk.
pause
exit /b 1
:failed
echo Setup failed. Check your internet connection and Python installation.
pause
exit /b 1
