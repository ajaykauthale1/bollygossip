@echo off
REM Long scandal video from a script:  run_scandal.bat scripts\my_story.txt
REM Output: scandal_long\output\<script>_<date-time>\<script>.mp4  (log: run.log in this folder)
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_scandal.bat scripts\your_script.txt
  exit /b 1
)
set PYTHONUTF8=1
"%~dp0..\.venv\Scripts\python.exe" scandal.py %*
