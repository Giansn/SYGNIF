#!/bin/bash
# Sygnif system up/down Telegram notifier
# Usage: notify.sh up|down

SPOT_TOKEN="8753646984:AAFF8SQK2PUrcm1BnLBDzV5iNxPHR1KDbRY"
FUTURES_TOKEN="8016276540:AAFeBQfuJ0nYm68yGvNlBkEWuwaRtKVCtOQ"
CHAT_ID="1134139785"

send_tg() {
  local token="$1" msg="$2"
  curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    -d chat_id="$CHAT_ID" -d text="$msg" -d parse_mode=HTML >/dev/null 2>&1
}

get_bot_info() {
  # Returns: trades mode (e.g. "3 dry" or "5 live")
  local port="$1"
  local tk
  tk=$(curl -s --max-time 5 -X POST "http://localhost:${port}/api/v1/token/login" \
    -H "Authorization: Basic $(echo -n 'freqtrader:CHANGE_ME' | base64)" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
  [ -z "$tk" ] && echo "? ?" && return
  python3 -c "
import sys, json, urllib.request
hdr = {'Authorization': 'Bearer $tk'}
def api(ep):
    r = urllib.request.urlopen(urllib.request.Request('http://localhost:${port}/api/v1/' + ep, headers=hdr), timeout=5)
    return json.loads(r.read())
try:
    trades = len(api('status'))
    cfg = api('show_config')
    mode = 'DRY' if cfg.get('dry_run') else 'LIVE'
    print(f'{trades} {mode}')
except:
    print('? ?')
" 2>/dev/null
}

case "$1" in
  down)
    read spot_trades spot_mode <<< $(get_bot_info 8080)
    read fut_trades fut_mode <<< $(get_bot_info 8081)
    send_tg "$SPOT_TOKEN" "⛔ <b>System down.</b> ${spot_trades} open trades. [${spot_mode}]"
    send_tg "$FUTURES_TOKEN" "⛔ <b>System down.</b> ${fut_trades} open trades. [${fut_mode}]"
    ;;
  up)
    # Wait for APIs to be ready
    for i in $(seq 1 30); do
      curl -s --max-time 2 http://localhost:8080/api/v1/ping >/dev/null 2>&1 && \
      curl -s --max-time 2 http://localhost:8081/api/v1/ping >/dev/null 2>&1 && break
      sleep 2
    done
    read spot_trades spot_mode <<< $(get_bot_info 8080)
    read fut_trades fut_mode <<< $(get_bot_info 8081)
    send_tg "$SPOT_TOKEN" "✅ <b>System up.</b> ${spot_trades} trades monitored. [${spot_mode}]"
    send_tg "$FUTURES_TOKEN" "✅ <b>System up.</b> ${fut_trades} trades monitored. [${fut_mode}]"
    ;;
esac
