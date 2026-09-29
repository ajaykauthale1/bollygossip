@echo off
REM Daily entry point for Windows Task Scheduler.
REM Task Scheduler > Create Basic Task > Daily > Start a program: this .bat file.
REM Settings (ffmpeg folder, music file, ...) come from config.json in this folder.
cd /d "%~dp0"
REM UTF-8 so post titles with Hindi text or emoji log cleanly.
set PYTHONUTF8=1
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
if not exist output mkdir output
python pipeline.py %* >> output\run.log 2>&1
