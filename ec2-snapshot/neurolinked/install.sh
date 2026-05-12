#!/bin/bash

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN} ============================================${NC}"
echo -e "${CYAN}  NEUROLINKED - Neuromorphic Brain System${NC}"
echo -e "${CYAN}  One-Time Setup (Mac/Linux)${NC}"
echo -e "${CYAN} ============================================${NC}"
echo ""

# ---- Step 1: Check Python ----
echo "[1/5] Checking Python installation..."
if command -v python3 &> /dev/null; then
    PYVER=$(python3 --version 2>&1)
    echo "        Found $PYVER"
    PY=python3
    PIP=pip3
elif command -v python &> /dev/null; then
    PYVER=$(python --version 2>&1)
    echo "        Found $PYVER"
    PY=python
    PIP=pip
else
    echo ""
    echo -e "${RED}  ERROR: Python is not installed.${NC}"
    echo ""
    echo "  Please install Python 3.10 or newer:"
    echo "    Mac:   brew install python3"
    echo "           OR download from https://python.org/downloads"
    echo "    Linux: sudo apt install python3 python3-pip"
    echo ""
    exit 1
fi

# ---- Step 2: Install core dependencies ----
echo ""
echo "[2/5] Installing core dependencies..."
echo "        numpy, scipy, fastapi, uvicorn, websockets..."
$PIP install --quiet numpy scipy fastapi "uvicorn[standard]" websockets 2>/dev/null
if [ $? -ne 0 ]; then
    echo "        Retrying with --user flag..."
    $PIP install --quiet --user numpy scipy fastapi "uvicorn[standard]" websockets
fi
echo "        Core dependencies installed."

# ---- Step 3: Install optional dependencies ----
echo ""
echo "[3/5] Installing optional dependencies..."
$PIP install --quiet Pillow 2>/dev/null
echo "        Pillow installed (screen observation)"
$PIP install --quiet opencv-python-headless 2>/dev/null
echo "        OpenCV installed (webcam support)"
$PIP install --quiet sounddevice 2>/dev/null
echo "        SoundDevice installed (microphone support)"
$PIP install --quiet mss 2>/dev/null
echo "        mss installed (fast screen capture)"
$PIP install --quiet pytesseract 2>/dev/null
echo "        pytesseract installed (OCR reading)"
$PIP install --quiet pygetwindow 2>/dev/null
echo "        pygetwindow installed (active window detection)"
echo ""
echo "        OCR NOTE: For screen text reading, install Tesseract:"
echo "          Mac:   brew install tesseract"
echo "          Linux: sudo apt install tesseract-ocr"
echo "        Without Tesseract, screen observation still works (motion only)"

# ---- Step 4: Create brain_state directory ----
echo ""
echo "[4/5] Setting up directories..."
mkdir -p brain_state
echo "        brain_state directory ready."

# ---- Step 5: Set up Claude connection ----
echo ""
echo "[5/5] Setting up Claude connection..."
$PY setup_claude.py 2>/dev/null
if [ $? -ne 0 ]; then
    echo "        Claude setup will be done on first run."
fi

echo ""
echo -e "${GREEN} ============================================${NC}"
echo -e "${GREEN}  SETUP COMPLETE!${NC}"
echo -e "${GREEN} ============================================${NC}"
echo ""
echo "  To start the brain:"
echo "    Run:  ./start.sh"
echo "    Or:   python3 run.py"
echo ""
echo "  Dashboard opens at: http://localhost:8000"
echo ""
echo "  To connect Claude:"
echo "    Run: python3 setup_claude.py"
echo ""
echo -e "${GREEN} ============================================${NC}"
echo ""
