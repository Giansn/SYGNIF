#!/usr/bin/env python3
"""
Link Swarm launchers to the SYGNIF repo **and** other project trees on the machine.

**Dotenv merge (``setdefault`` unless noted):** first writer wins for each key.

1. ``SYGNIF_SECRETS_ENV_FILE`` (if set)
2. ``~/xrp_claude_bot/.env``
3. ``<repo>/.env`` — same precedence as before sibling-linking (**xrp wins** over repo on duplicates)
4. Sibling / instance ``.env`` files (see roots below) — **only keys not already set** by 1–3
5. ``<repo>/swarm_operator.env`` — **overrides** (ACK / demo keys / operator overrides)
6. ``--env-file`` / ``extra_env_file`` — **overrides**

**Which directories load sibling ``.env``:**

- Off: ``SYGNIF_SWARM_LINK_INSTANCE=0`` / ``false`` / ``no`` / ``off`` — only steps 1, 3–6 (legacy behaviour).
- On (default): ``SYGNIF_INSTANCE_ROOTS`` — colon-separated absolute or ``~`` paths; each existing
  directory with a ``.env`` is loaded.
- If ``SYGNIF_INSTANCE_ROOTS`` is unset: load ``.env`` from **well-known** sibling names under
  ``$HOME`` when those directories exist (truthcoin-dc, trade_overseer, finance_agent, …).
- If ``SYGNIF_INSTANCE_ROOTS_SCAN=1`` (or ``all``): also include **every** non-hidden
  ``$HOME/*`` directory that looks like a project (``.git``, ``.env``, ``pyproject.toml``,
  ``Cargo.toml``, ``package.json``, or ``go.mod`` at top level), excluding ``SYGNIF_INSTANCE_ROOTS_EXCLUDE``
  (colon-separated **names** only).

**Python path for child processes** (``swarm_auto_predict_protocol_loop`` subprocess only):

- ``SYGNIF_SWARM_EXTEND_PYTHONPATH=1``: prepend the same instance root list to ``PYTHONPATH`` in the
  subprocess environment so ``btc_predict_protocol_loop`` can import packages from sibling repos.
  Does not change the already-running interpreter (use only where a new Python is spawned).

Repo root is never duplicated in the sibling list.
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_MARKERS = (
    ".git",
    ".env",
    "pyproject.toml",
    "Cargo.toml",
    "package.json",
    "go.mod",
)

_HOME_SCAN_SKIP = frozenset(
    {
        ".cache",
        ".cargo",
        ".rustup",
        ".local",
        ".cursor",
        ".cursor-server",
        ".config",
        ".venvs",
        ".venv",
        ".npm",
        ".docker",
        ".ssh",
        ".ipython",
        ".jupyter",
        ".lake_cache",
        ".mcp-auth",
        ".wakatime",
        ".brainsync",
        ".bsync_core",
        ".gitnexus",
        ".agent",
        ".agent-mem",
        ".agents",
        ".claude",
        ".vscode",
        ".pytest_cache",
        ".ruff_cache",
        "logs",
        "intel",
    }
)

_DEFAULT_SIBLING_NAMES: tuple[str, ...] = (
    "truthcoin-dc",
    "trade_overseer",
    "finance_agent",
    "xrp_claude_bot",
    "NostalgiaForInfinity",
    "nautilus_trader",
    "Network",
    "Network-1",
    "nn-zero-to-hero",
    "repos",
    "system-overview",
    "network-dev-agents",
)


def _env_truthy(name: str, *, default: bool | None = None) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return bool(default) if default is not None else False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _link_instance() -> bool:
    """Default **on** — set ``SYGNIF_SWARM_LINK_INSTANCE=0`` to disable sibling discovery."""
    raw = os.environ.get("SYGNIF_SWARM_LINK_INSTANCE")
    if raw is None or not str(raw).strip():
        return True
    return _env_truthy("SYGNIF_SWARM_LINK_INSTANCE")


def _scan_enabled() -> bool:
    return _env_truthy("SYGNIF_INSTANCE_ROOTS_SCAN")


def _exclude_names() -> frozenset[str]:
    raw = (os.environ.get("SYGNIF_INSTANCE_ROOTS_EXCLUDE") or "").strip()
    if not raw:
        return frozenset()
    parts = {p.strip() for p in raw.split(":") if p.strip()}
    return frozenset(parts)


def _looks_like_project(d: Path) -> bool:
    return any((d / name).exists() for name in _PROJECT_MARKERS)


def _home_scan_dirs(*, exclude: frozenset[str]) -> list[Path]:
    home = Path.home()
    out: list[Path] = []
    try:
        it = sorted(home.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for p in it:
        if not p.is_dir() or p.name.startswith("."):
            continue
        if p.name in _HOME_SCAN_SKIP or p.name in exclude:
            continue
        if _looks_like_project(p):
            out.append(p.resolve())
    return out


def _explicit_roots_from_env() -> list[Path] | None:
    raw = (os.environ.get("SYGNIF_INSTANCE_ROOTS") or "").strip()
    if not raw:
        return None
    out: list[Path] = []
    for part in raw.split(":"):
        part = part.strip()
        if not part:
            continue
        p = Path(part).expanduser().resolve()
        if p.is_dir():
            out.append(p)
    return out


def _default_named_siblings() -> list[Path]:
    home = Path.home()
    out: list[Path] = []
    for name in _DEFAULT_SIBLING_NAMES:
        p = (home / name).resolve()
        if p.is_dir():
            out.append(p)
    return out


def instance_roots(repo_root: Path) -> list[Path]:
    """
    Directories to merge ``.env`` from and optionally prepend to ``PYTHONPATH``.

    ``repo_root`` is excluded from the returned list.
    """
    repo = repo_root.resolve()
    explicit = _explicit_roots_from_env()
    seen: set[Path] = set()
    ordered: list[Path] = []

    def add(p: Path) -> None:
        p = p.resolve()
        if p == repo or not p.is_dir():
            return
        if p in seen:
            return
        seen.add(p)
        ordered.append(p)

    if not _link_instance():
        return ordered

    exclude = _exclude_names()
    if explicit is not None:
        for p in explicit:
            if p.name not in exclude:
                add(p)
        return ordered

    for p in _default_named_siblings():
        if p.name not in exclude:
            add(p)

    if _scan_enabled():
        for p in _home_scan_dirs(exclude=exclude):
            add(p)

    return ordered


def load_dotenv_file(path: Path, *, override: bool = False) -> None:
    """Parse ``KEY=VALUE`` lines into ``os.environ`` (``setdefault`` unless ``override``)."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        if override:
            os.environ[k] = v
        else:
            os.environ.setdefault(k, v)


def apply_swarm_instance_env(repo_root: Path, *, extra_env_file: Path | None = None) -> None:
    """Load dotenv chain for Swarm / predict-protocol launchers (see module docstring)."""
    repo_root = repo_root.resolve()
    raw = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if raw:
        load_dotenv_file(Path(raw).expanduser(), override=False)
    load_dotenv_file(Path.home() / "xrp_claude_bot" / ".env", override=False)
    load_dotenv_file(repo_root / ".env", override=False)
    for root in instance_roots(repo_root):
        load_dotenv_file(root / ".env", override=False)
    load_dotenv_file(repo_root / "swarm_operator.env", override=True)
    if extra_env_file is not None:
        load_dotenv_file(extra_env_file, override=True)


def subprocess_env_with_instance_pythonpath(repo_root: Path) -> dict[str, str]:
    """
    Copy of ``os.environ`` with ``PYTHONPATH`` extended when
    ``SYGNIF_SWARM_EXTEND_PYTHONPATH`` is truthy.
    """
    env = dict(os.environ)
    if not _env_truthy("SYGNIF_SWARM_EXTEND_PYTHONPATH"):
        return env
    roots = [str(p) for p in instance_roots(repo_root)]
    if not roots:
        return env
    chunk = os.pathsep.join(roots)
    prev = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = f"{chunk}{os.pathsep}{prev}" if prev else chunk
    return env


def append_instance_roots_to_syspath(repo_root: Path) -> None:
    """
    For the **current** process: append instance roots to ``sys.path`` (low import priority).

    Used when ``SYGNIF_SWARM_EXTEND_PYTHONPATH`` is set but no subprocess is spawned.
    """
    import sys

    if not _env_truthy("SYGNIF_SWARM_EXTEND_PYTHONPATH"):
        return
    for p in instance_roots(repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.append(s)
