#!/usr/bin/env python3
"""
Telegram Timeframe Controller for Freqtrade
Commands: /tf5m /tf15m /tf30m /tf1h /tf4h /status
"""
import os
import sys
import json
import subprocess
import time
import requests

STRATEGY_PATH = "/home/ubuntu/xrp_claude_bot/user_data/strategies/SygnifStrategy.py"
CONFIG_PATH = "/home/ubuntu/xrp_claude_bot/user_data/config.json"
VALID_TIMEFRAMES = {
    "/tf5m": "5m",
    "/tf15m": "15m",
    "/tf30m": "30m",
    "/tf1h": "1h",
    "/tf4h": "4h",
}

def get_telegram_config():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    return config["telegram"]["token"], config["telegram"]["chat_id"]

def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

def get_current_timeframe():
    with open(STRATEGY_PATH, "r") as f:
        for line in f:
            if 'timeframe = "' in line and "self." not in line:
                return line.strip().split('"')[1]
    return "unknown"

def set_timeframe(new_tf):
    with open(STRATEGY_PATH, "r") as f:
        code = f.read()
    
    current = get_current_timeframe()
    code = code.replace(f'timeframe = "{current}"', f'timeframe = "{new_tf}"', 1)
    
    with open(STRATEGY_PATH, "w") as f:
        f.write(code)

def restart_bot():
    subprocess.run(["docker", "restart", "freqtrade"], check=True)
    time.sleep(10)

def main():
    token, chat_id = get_telegram_config()
    
    send_message(token, chat_id, 
        "*Timeframe Controller aktiv*\n\n"
        "Befehle:\n"
        "`/tf5m`  - 5 Minuten\n"
        "`/tf15m` - 15 Minuten\n"
        "`/tf30m` - 30 Minuten\n"
        "`/tf1h`  - 1 Stunde\n"
        "`/tf4h`  - 4 Stunden\n"
        "`/tfstatus` - Aktueller Timeframe"
    )
    
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            resp = requests.get(url, params={"offset": offset, "timeout": 30})
            updates = resp.json().get("result", [])
            
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                user_chat_id = str(msg.get("chat", {}).get("id", ""))
                
                if user_chat_id != str(chat_id):
                    continue
                
                if text in VALID_TIMEFRAMES:
                    new_tf = VALID_TIMEFRAMES[text]
                    current = get_current_timeframe()
                    
                    if current == new_tf:
                        send_message(token, chat_id, f"Timeframe ist bereits *{new_tf}*")
                        continue
                    
                    send_message(token, chat_id, f"Wechsle Timeframe: *{current}* -> *{new_tf}*\nBot wird neugestartet...")
                    set_timeframe(new_tf)
                    restart_bot()
                    send_message(token, chat_id, f"Timeframe auf *{new_tf}* gewechselt! Bot laeuft.")
                
                elif text == "/tfstatus":
                    current = get_current_timeframe()
                    send_message(token, chat_id, f"Aktueller Timeframe: *{current}*")
        
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
