#!/usr/bin/env python3
"""
Timeframe controller via Freqtrade REST API + file watcher
Watches /tmp/tf_change file for timeframe commands
Usage: echo "5m" > /tmp/tf_change  (or via cron/webhook)
"""
import os, json, time, subprocess, sys, requests

STRATEGY = "/home/ubuntu/xrp_claude_bot/user_data/strategies/SygnifStrategy.py"
CONFIG = "/home/ubuntu/xrp_claude_bot/user_data/config.json"
VALID = ["5m", "15m", "30m", "1h", "4h"]

def send_tg(msg):
    with open(CONFIG) as f:
        c = json.load(f)
    t, cid = c["telegram"]["token"], c["telegram"]["chat_id"]
    requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                  json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"})

def get_tf():
    with open(STRATEGY) as f:
        for l in f:
            if 'timeframe = "' in l and "self." not in l:
                return l.strip().split('"')[1]
    return "1h"

def set_tf(tf):
    with open(STRATEGY) as f:
        code = f.read()
    code = code.replace(f'timeframe = "{get_tf()}"', f'timeframe = "{tf}"', 1)
    with open(STRATEGY, "w") as f:
        f.write(code)

def change(tf):
    cur = get_tf()
    if cur == tf:
        send_tg(f"Timeframe ist bereits *{tf}*")
        return
    send_tg(f"Wechsle: *{cur}* -> *{tf}*\nRestart...")
    set_tf(tf)
    subprocess.run(["docker", "restart", "freqtrade"])
    time.sleep(12)
    send_tg(f"Timeframe *{tf}* aktiv!")

if len(sys.argv) > 1 and sys.argv[1] in VALID:
    change(sys.argv[1])
else:
    print(f"Usage: {sys.argv[0]} [5m|15m|30m|1h|4h]")
    print(f"Current: {get_tf()}")
