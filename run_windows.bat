@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup_windows.bat
if errorlevel 1 exit /b 1
"%~dp0.venv\Scripts\python.exe" -c "import yt_dlp, numpy, mido, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 call setup_windows.bat
if errorlevel 1 exit /b 1
set "PATH=%~dp0.venv\Scripts;%PATH%"
"%~dp0.venv\Scripts\python.exe" speaker_cue_mapper_windows.py
if errorlevel 1 (
  echo.
  echo The app exited with an error. Check the message above.
  pause
)
