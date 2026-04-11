#!/usr/bin/env python3
"""
Sentiment layer health for Sygnif: finance-agent HTTP + docker log signals.

On urgent findings, sends Telegram via @sygnif_agent_bot (AGENT_BOT_TOKEN + AGENT_CHAT_ID),
reusing scripts/cron_tg_notify.send_agent_telegram.

Also flags **entry tags** whose **last N closes are all losses** (default N=5) per spot/futures DB.

Env (optional):
  SENTIMENT_WATCH_ALERT_COOLDOWN_SEC  default 14400 (4h per alert key)
  SENTIMENT_WATCH_DOCKER_TAIL         default 1500 lines per container
  SENTIMENT_WATCH_HTTP_FAILS          default 3  (count SYGNIF_SENTIMENT_HTTP failed in docker tail)
  SENTIMENT_WATCH_CLAUDE_ERRS         default 4  (Claude API error, MS2)
  TAG_STREAK_CONSECUTIVE_LOSSES       default 5  (consecutive losing closes, same enter_tag)
  TAG_STREAK_DISTINCT_SCAN            default 500  (recent closed rows scanned for distinct tags)
  TAG_STREAK_ALERT_COOLDOWN_SEC       default 86400 (per instance+tag Telegram cooldown)
  SYGNIF_REPO                         default ~/SYGNIF
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("SYGNIF_REPO", str(Path.home() / "SYGNIF"))).resolve()
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cron_tg_notify import (  # noqa: E402
    resolve_agent_env_path_or_none,
    send_agent_telegram,
)

STATE_PATH = REPO / "user_data" / "logs" / "sentiment_watch_state.json"
COOLDOWN = int(os.environ.get("SENTIMENT_WATCH_ALERT_COOLDOWN_SEC", str(4 * 3600)))
DOCKER_TAIL = int(os.environ.get("SENTIMENT_WATCH_DOCKER_TAIL", "1500"))
HTTP_FAILS = int(os.environ.get("SENTIMENT_WATCH_HTTP_FAILS", "3"))
CLAUDE_ERRS = int(os.environ.get("SENTIMENT_WATCH_CLAUDE_ERRS", "4"))
TAG_STREAK_N = int(os.environ.get("TAG_STREAK_CONSECUTIVE_LOSSES", "5"))
TAG_STREAK_SCAN = int(os.environ.get("TAG_STREAK_DISTINCT_SCAN", "500"))
TAG_STREAK_COOLDOWN = float(os.environ.get("TAG_STREAK_ALERT_COOLDOWN_SEC", str(86400)))


def _parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _use_local_http(env: dict[str, str]) -> bool:
    v = (env.get("SYGNIF_USE_LOCAL_FINANCE_AGENT_HTTP") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    return bool((env.get("SYGNIF_SENTIMENT_HTTP_URL") or "").strip())


def _finance_base_url(env: dict[str, str]) -> str:
    u = (env.get("SYGNIF_SENTIMENT_HTTP_URL") or "").strip().rstrip("/")
    if u:
        if "/sygnif/sentiment" in u:
            return u.split("/sygnif/sentiment")[0].rstrip("/") or "http://127.0.0.1:8091"
        if "/sygnif" in u:
            return u.split("/sygnif")[0].rstrip("/") or "http://127.0.0.1:8091"
        return u
    host = (env.get("FINANCE_AGENT_HTTP_HOST") or "127.0.0.1").strip()
    port = (env.get("FINANCE_AGENT_HTTP_PORT") or "8091").strip()
    return f"http://{host}:{port}"


def _check_health_get(base: str) -> str | None:
    import urllib.error
    import urllib.request

    url = base.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            code = getattr(r, "status", None) or getattr(r, "getcode", lambda: 200)()
            if code != 200:
                return f"{url} status={code}"
    except urllib.error.HTTPError as e:
        return f"{url} HTTP {e.code}"
    except Exception as e:
        return f"{url}: {e}"
    return None


def _check_sentiment_post(base: str) -> str | None:
    import urllib.error
    import urllib.request

    post_url = base.rstrip("/") + "/sygnif/sentiment"
    payload = json.dumps(
        {
            "token": "BTC",
            "price": 90000.0,
            "ta_score": 48.0,
            "headlines": ["sygnif_sentiment_health_probe"],
            "include_live": False,
        }
    ).encode()
    req = urllib.request.Request(
        post_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode()
            data = json.loads(raw)
            if not data.get("ok"):
                return f"{post_url} ok=false: {data!r}"[:500]
    except urllib.error.HTTPError as e:
        body = (e.read() or b"").decode(errors="replace")[:300]
        return f"{post_url} HTTP {e.code} {body}"
    except Exception as e:
        return f"{post_url}: {e}"
    return None


def _docker_logs(containers: tuple[str, ...]) -> str:
    chunks: list[str] = []
    for name in containers:
        try:
            p = subprocess.run(
                ["docker", "logs", name, "--tail", str(DOCKER_TAIL)],
                capture_output=True,
                text=True,
                timeout=90,
            )
            chunks.append(p.stdout or "")
            chunks.append(p.stderr or "")
        except Exception as e:
            chunks.append(f"\n[{name}] docker error: {e}\n")
    return "\n".join(chunks)


def find_entry_tag_loss_streaks(
    db_path: Path,
    instance: str,
    *,
    streak: int = TAG_STREAK_N,
    scan_limit: int = TAG_STREAK_SCAN,
) -> list[tuple[str, str]]:
    """
    Return [(enter_tag, detail_line), ...] for tags whose last `streak` closed trades
    are all losses (close_profit < 0). Empty if DB missing or no hits.
    """
    if not db_path.is_file() or streak < 2:
        return []
    out: list[tuple[str, str]] = []
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT enter_tag
            FROM (
                SELECT enter_tag
                FROM trades
                WHERE is_open = 0 AND IFNULL(enter_tag, '') != ''
                ORDER BY close_date DESC
                LIMIT ?
            ) AS recent
            """,
            (scan_limit,),
        )
        tags = [r[0] for r in cur.fetchall() if r[0]]
        for tag in tags:
            cur.execute(
                """
                SELECT close_profit, close_date
                FROM trades
                WHERE is_open = 0 AND enter_tag = ?
                ORDER BY close_date DESC
                LIMIT ?
                """,
                (tag, streak),
            )
            rows = cur.fetchall()
            if len(rows) < streak:
                continue
            profits = [float(r[0] or 0.0) for r in rows]
            if not all(p < 0.0 for p in profits):
                continue
            last_dt = rows[0][1]
            worst = min(profits)
            line = (
                f"`{instance}` **{tag}** — last **{streak}** closes all losses "
                f"(close_profit < 0); worst **{worst:.4f}**; latest close **{last_dt}**"
            )
            out.append((tag, line))
    finally:
        conn.close()
    return out


def _state_key_tag_fragment(tag: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:120]


def _docker_signals(text: str) -> list[str]:
    out: list[str] = []
    n_http = text.count("SYGNIF_SENTIMENT_HTTP failed")
    if n_http >= HTTP_FAILS:
        out.append(f"Docker logs: {n_http}× `SYGNIF_SENTIMENT_HTTP failed` (≥{HTTP_FAILS}) in recent tail")
    n_claude = text.count("Claude API error")
    if n_claude >= CLAUDE_ERRS:
        out.append(f"Docker logs: {n_claude}× `Claude API error` (≥{CLAUDE_ERRS}) in recent tail")
    return out


def _load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(d: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _telegram_configured(_env: dict[str, str]) -> bool:
    """True if any known .env (SYGNIF or xrp_claude_bot) has agent bot keys."""
    return resolve_agent_env_path_or_none() is not None


def _alert(key: str, title: str, body: str, env: dict[str, str], *, cooldown: float | None = None) -> bool:
    if not _telegram_configured(env):
        print("sentiment_watch: AGENT_BOT_TOKEN + AGENT_CHAT_ID missing — no Telegram", file=sys.stderr)
        return False
    cd = COOLDOWN if cooldown is None else float(cooldown)
    now = time.time()
    st = _load_state()
    last = float(st.get(key, 0) or 0)
    if now - last < cd:
        return False
    st[key] = now
    _save_state(st)
    p = resolve_agent_env_path_or_none()
    send_agent_telegram(title, body, env_path=p)
    return True


def main() -> int:
    env = _parse_dotenv(REPO / ".env")
    issues: list[str] = []

    if _use_local_http(env):
        base = _finance_base_url(env)
        h = _check_health_get(base)
        if h:
            issues.append(f"**Health GET** — {h}")
        p = _check_sentiment_post(base)
        if p:
            issues.append(f"**Sentiment POST** — {p}")
    else:
        print("sentiment_watch: local HTTP not enabled — skipping HTTP probes (set SYGNIF_USE_LOCAL_FINANCE_AGENT_HTTP=1 or SYGNIF_SENTIMENT_HTTP_URL)")

    log_text = _docker_logs(("freqtrade", "freqtrade-futures"))
    issues.extend(_docker_signals(log_text))

    if issues:
        body = "\n".join(f"• {x}" for x in issues)
        title = "URGENT · Sygnif sentiment layer"
        if _alert("sentiment_urgent", title, body, env):
            print("sentiment_watch: Telegram alert sent (sentiment/docker)")
        else:
            print("sentiment_watch: sentiment/docker issues but cooldown or Telegram skip")

    dbs = {
        "spot": REPO / "user_data" / "tradesv3.sqlite",
        "futures": REPO / "user_data" / "tradesv3-futures.sqlite",
    }
    tag_alerts = 0
    for inst, path in dbs.items():
        for tag, line in find_entry_tag_loss_streaks(path, inst):
            key = f"tag_streak:{inst}:{_state_key_tag_fragment(tag)}"
            if _alert(
                key,
                "URGENT · Entry tag loss streak",
                line,
                env,
                cooldown=TAG_STREAK_COOLDOWN,
            ):
                tag_alerts += 1
                print(f"sentiment_watch: Telegram tag_streak sent ({inst} {tag})")

    if not issues and tag_alerts == 0:
        print("sentiment_watch: OK (no urgent signals)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
