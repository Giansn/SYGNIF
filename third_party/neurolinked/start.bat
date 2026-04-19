@echo off
title NeuroLinked Brain - Live
color 0A
echo.
echo  ============================================
echo   NEUROLINKED - Brain Starting...
echo  ============================================
echo.
echo   Dashboard:  http://localhost:8000
echo   Sygnif summary API: http://localhost:8000/api/sygnif/summary
echo.
echo   Press Ctrl+C to stop the brain
echo   (Brain auto-saves every 5 minutes)
echo  ============================================
echo.

:: Open dashboard in default browser
start "" http://localhost:8000

:: Start the brain
python run.py
pause
