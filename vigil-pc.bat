@echo off
rem Vigil PC worker bootstrap, v2 -- FROZEN. This file must never be updated
rem again: cmd reads a running .bat by byte offset, so a git pull that
rem rewrites it mid-run can corrupt the loop. All logic (fast git checks,
rem pulls, sim cadence) lives in pc_loop.py, which updates ITSELF and exits;
rem this loop just restarts it on the fresh code.
rem
rem Setup: see DEPLOY.md ("PC compute worker"). Close this window to stop.
cd /d "%~dp0"
title Vigil PC worker
echo [vigil-pc] bootstrap v2 starting in %CD%
git pull --ff-only

:loop
python pc_loop.py
echo [vigil-pc] loop exited (update restart or error) - relaunching in 15s...
timeout /t 15 /nobreak >nul
goto loop
