#!/bin/bash
# Apply status patch (compact /status view) before starting freqtrade
python3 /freqtrade/user_data/status_patch.py 2>/dev/null
exec freqtrade "$@"
