@echo off
rem Vigil PC compute worker bootstrap. This file is DELIBERATELY dumb: it only
rem pulls the latest code and runs one cycle, forever. All real logic lives in
rem pc_worker.py, which this pull keeps fresh automatically -- you should never
rem need to edit or update this file.
rem
rem Setup (once): install Python 3.11+ and Git for Windows, clone the repo,
rem copy vigil-pc.cfg.example to vigil-pc.cfg and fill in your app URL and
rem SIM_TOKEN, then double-click this file (or add it to Task Scheduler).
cd /d "%~dp0"
title Vigil PC worker
echo [vigil-pc] bootstrap starting in %CD%

:loop
git pull --ff-only
if exist requirements.txt (
  python -m pip install -q -r requirements.txt
)
python pc_worker.py
echo [vigil-pc] sleeping 10 minutes (Ctrl+C or close window to stop)...
timeout /t 600 /nobreak >nul
goto loop
