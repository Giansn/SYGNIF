@echo off
title NeuroLinked Brain - Setup
color 0B
echo.
echo  ============================================
echo   NEUROLINKED - Neuromorphic Brain System
echo   One-Time Setup
echo  ============================================
echo.

:: ---- Step 1: Check Python ----
echo [1/5] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python is not installed or not in PATH.
    echo.
    echo  Please install Python 3.10 or newer:
    echo    1. Go to https://python.org/downloads
    echo    2. Download Python 3.12 or 3.13
    echo    3. IMPORTANT: Check "Add Python to PATH" during install
    echo    4. Re-run this installer after Python is installed
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo         Found Python %PYVER%

:: ---- Step 2: Install core dependencies ----
echo.
echo [2/5] Installing core dependencies...
echo         numpy, scipy, fastapi, uvicorn, websockets...
pip install --quiet numpy scipy fastapi "uvicorn[standard]" websockets 2>nul
if %errorlevel% neq 0 (
    echo         Retrying with --user flag...
    pip install --quiet --user numpy scipy fastapi "uvicorn[standard]" websockets
)
echo         Core dependencies installed.

:: ---- Step 3: Install optional dependencies ----
echo.
echo [3/5] Installing optional dependencies...
pip install --quiet Pillow 2>nul
echo         Pillow installed (screen observation)
pip install --quiet opencv-python-headless 2>nul
echo         OpenCV installed (webcam support)
pip install --quiet sounddevice 2>nul
echo         SoundDevice installed (microphone support)
pip install --quiet mss 2>nul
echo         mss installed (fast screen capture)
pip install --quiet pytesseract 2>nul
echo         pytesseract installed (OCR reading)
pip install --quiet pygetwindow 2>nul
echo         pygetwindow installed (active window detection)
echo.
echo         OCR NOTE: For screen text reading you also need Tesseract:
echo           Download: https://github.com/UB-Mannheim/tesseract/wiki
echo           Install it with "Add to PATH" checked
echo           Without Tesseract, screen observation still works (motion only)

:: ---- Step 4: Create brain_state directory ----
echo.
echo [4/5] Setting up directories...
if not exist "brain_state" mkdir brain_state
echo         brain_state directory ready.

:: ---- Step 5: Set up Claude connection ----
echo.
echo [5/5] Setting up Claude connection...
python setup_claude.py 2>nul
if %errorlevel% neq 0 (
    echo         Claude setup will be done on first run.
)

echo.
echo  ============================================
echo   SETUP COMPLETE!
echo  ============================================
echo.
echo   To start the brain:
echo     Double-click  start.bat
echo     Or run:       python run.py
echo.
echo   Dashboard opens at: http://localhost:8000
echo.
echo   To connect Claude:
echo     Run: python setup_claude.py
echo     (Sets up automatic Claude connection)
echo.
echo  ============================================
echo.
pause
