"""Freqtrade API client for spot and futures instances."""
import logging
import requests
from config import FT_INSTANCES

logger = logging.getLogger("overseer.ft")

# Cached JWT tokens per instance
_tokens: dict[str, str] = {}


def _login(instance: dict) -> str:
    """Authenticate and return JWT token."""
    resp = requests.post(
        f"{instance['url']}/token/login",
        auth=(instance["user"], instance["pass"]),
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _get(instance: dict, endpoint: str) -> dict | list:
    """GET from freqtrade API with auto-login on 401."""
    name = instance["name"]
    if name not in _tokens:
        _tokens[name] = _login(instance)

    url = f"{instance['url']}/{endpoint}"
    headers = {"Authorization": f"Bearer {_tokens[name]}"}

    resp = requests.get(url, headers=headers, timeout=5)
    if resp.status_code == 401:
        _tokens[name] = _login(instance)
        headers["Authorization"] = f"Bearer {_tokens[name]}"
        resp = requests.get(url, headers=headers, timeout=5)

    resp.raise_for_status()
    return resp.json()


def _post(instance: dict, endpoint: str, payload: dict) -> dict | list:
    name = instance["name"]
    if name not in _tokens:
        _tokens[name] = _login(instance)

    url = f"{instance['url']}/{endpoint}"
    headers = {"Authorization": f"Bearer {_tokens[name]}"}

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 401:
        _tokens[name] = _login(instance)
        headers["Authorization"] = f"Bearer {_tokens[name]}"
        resp = requests.post(url, headers=headers, json=payload, timeout=30)

    resp.raise_for_status()
    return resp.json()


def get_show_config(instance: dict) -> dict:
    """Freqtrade running config (includes dry_run)."""
    r = _get(instance, "show_config")
    return r if isinstance(r, dict) else {}


def forceenter(
    instance: dict,
    *,
    pair: str,
    side: str = "long",
    stake_amount: float | str = 50,
    enter_tag: str = "overseer_ensure_entry",
) -> dict | list:
    # Freqtrade API schema: stakeamount, entry_tag (see api_schemas.ForceEnterPayload)
    body: dict = {"pair": pair, "side": side, "entry_tag": enter_tag}
    if stake_amount is not None:
        body["stakeamount"] = stake_amount
    return _post(instance, "forceenter", body)


def get_open_trades(instance: dict) -> list[dict]:
    """Get open trades with normalized fields."""
    try:
        trades = _get(instance, "status")
        result = []
        for t in trades:
            result.append({
                "trade_id": t.get("trade_id"),
                "pair": t.get("pair", ""),
                "profit_pct": (t.get("profit_ratio", 0) or 0) * 100,
                "profit_abs": t.get("profit_abs", 0) or 0,
                "stake_amount": t.get("stake_amount", 0) or 0,
                "open_rate": t.get("open_rate", 0) or 0,
                "current_rate": t.get("current_rate", 0) or 0,
                "trade_duration": t.get("trade_duration", 0) or 0,
                "enter_tag": t.get("enter_tag", ""),
                "instance": instance["name"],
            })
        return result
    except Exception as e:
        logger.error(f"Failed to get trades from {instance['name']}: {e}")
        return []


def get_profit(instance: dict) -> dict:
    """Get profit summary."""
    try:
        p = _get(instance, "profit")
        return {
            "profit_all": p.get("profit_all_coin", 0) or 0,
            "profit_closed": p.get("profit_closed_coin", 0) or 0,
            "winning_trades": p.get("winning_trades", 0),
            "losing_trades": p.get("losing_trades", 0),
            "best_pair": p.get("best_pair", ""),
            "instance": instance["name"],
        }
    except Exception as e:
        logger.error(f"Failed to get profit from {instance['name']}: {e}")
        return {"instance": instance["name"]}


def get_all_trades() -> list[dict]:
    """Get open trades from all instances."""
    all_trades = []
    for inst in FT_INSTANCES:
        all_trades.extend(get_open_trades(inst))
    return all_trades


def get_all_profits() -> list[dict]:
    """Get profit summaries from all instances."""
    return [get_profit(inst) for inst in FT_INSTANCES]


def is_available(instance: dict) -> bool:
    """Check if freqtrade instance is responding."""
    try:
        resp = requests.get(f"{instance['url']}/ping", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False
