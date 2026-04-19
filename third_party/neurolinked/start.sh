#!/bin/bash

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
NC='\033[0m'

echo ""
echo -e "${GREEN} ============================================${NC}"
echo -e "${GREEN}  NEUROLINKED - Brain Starting...${NC}"
echo -e "${GREEN} ============================================${NC}"
echo ""
echo "  Dashboard:  http://localhost:8000"
echo "  Sygnif summary API: http://localhost:8000/api/sygnif/summary"
echo ""
echo "  Press Ctrl+C to stop the brain"
echo "  (Brain auto-saves every 5 minutes)"
echo -e "${GREEN} ============================================${NC}"
echo ""

# Detect Python command
if command -v python3 &> /dev/null; then
    PY=python3
else
    PY=python
fi

# Open dashboard in default browser
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    open http://localhost:8000 &
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Linux
    xdg-open http://localhost:8000 2>/dev/null &
fi

# Start the brain
$PY run.py
