"""
Noren / Trade-smart REST client (OAuth header injection).

Upstream reference: https://github.com/trade-smart/TradesmartApioAuth-py
Uses the published ``NorenRestApiOAuth`` package (``NorenRestApiPy.NorenApi``).

Credentials (no secrets in repo):
- ``TRADESMART_NOREN_HOST`` — REST base, e.g. ``https://rama.kambala.co.in/NorenWClientTP/``
- ``TRADESMART_NOREN_WEBSOCKET`` — WS template URL from your broker (must include ``{access_token}`` if you use WS).
- ``TRADESMART_ACCESS_TOKEN``, ``TRADESMART_UID``, ``TRADESMART_ACCOUNT_ID`` — OAuth session.

Or ``TRADESMART_CRED_YAML`` pointing to a YAML file with ``Access_token`` / ``access_token``, ``UID``, ``Account_ID``
(compatible with the upstream ``cred.yml`` layout).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from NorenRestApiPy.NorenApi import NorenApi
except ImportError as exc:  # pragma: no cover - import guard for optional dep
    raise ImportError(
        "NorenRestApiOAuth is required. Install: pip install -r finance_agent/requirements-tradesmart.txt"
    ) from exc


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _load_yaml_creds(path: Path) -> dict[str, Any]:
    import yaml  # noqa: PLC0415

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(k, str):
            out[k] = v
    return out


def _pick_token(d: dict[str, Any]) -> str:
    for key in ("Access_token", "access_token", "ACCESS_TOKEN"):
        v = d.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _pick_uid(d: dict[str, Any]) -> str:
    for key in ("UID", "uid", "USERID", "userid"):
        v = d.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _pick_account(d: dict[str, Any]) -> str:
    for key in ("Account_ID", "account_id", "actid", "AID"):
        v = d.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def load_tradesmart_oauth_from_env() -> tuple[str, str, str]:
    """
    Return (access_token, uid, account_id) from env or ``TRADESMART_CRED_YAML``.
    """
    yml = _env("TRADESMART_CRED_YAML")
    if yml:
        d = _load_yaml_creds(Path(yml).expanduser())
        tok, uid, aid = _pick_token(d), _pick_uid(d), _pick_account(d)
    else:
        tok = _env("TRADESMART_ACCESS_TOKEN")
        uid = _env("TRADESMART_UID")
        aid = _env("TRADESMART_ACCOUNT_ID")
    if not tok or not uid or not aid:
        raise RuntimeError(
            "TradeSmart OAuth incomplete: set TRADESMART_ACCESS_TOKEN, TRADESMART_UID, "
            "TRADESMART_ACCOUNT_ID (or TRADESMART_CRED_YAML with those fields)."
        )
    return tok, uid, aid


def default_noren_hosts() -> tuple[str, str]:
    """Defaults aligned with trade-smart ``api_helper.NorenApiPy``."""
    h = _env("TRADESMART_NOREN_HOST", "https://rama.kambala.co.in/NorenWClientTP/")
    w = _env("TRADESMART_NOREN_WEBSOCKET", "wss://rama.kambala.co.in/NorenWS/")
    if not h.endswith("/"):
        h = h + "/"
    return h, w


def build_noren_api() -> NorenApi:
    host, ws = default_noren_hosts()
    api = NorenApi(host, ws)
    tok, uid, aid = load_tradesmart_oauth_from_env()
    api.injectOAuthHeader(tok, uid, aid)
    return api
