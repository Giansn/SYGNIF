"""
Obsidian vault → NeuroLinked knowledge + text sensory channel.

Obsidian stores notes as Markdown on disk. Point ``NEUROLINKED_OBSIDIAN_VAULT`` at your
vault root; changed ``.md`` files are indexed into ``KnowledgeStore`` (searchable like
other memories) and a compact digest is encoded into the brain's text pathway.

This does not bundle the Obsidian editor; it reads the same on-disk format Obsidian uses.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

# Directory name segments (under vault root) to skip when scanning
_SKIP_DIR_PARTS = frozenset({
    ".obsidian",
    ".git",
    "node_modules",
    ".trash",
})


def parse_frontmatter(text: str) -> tuple[str | None, str]:
    """Split ``(yaml_inner, body)`` if a leading ``---`` / ``---`` block exists; else ``(None, text)``."""
    if not text or not text.startswith("---"):
        return None, text
    raw_lines = text.splitlines(keepends=True)
    if not raw_lines or raw_lines[0].strip() != "---":
        return None, text
    for i in range(1, len(raw_lines)):
        if raw_lines[i].strip() == "---":
            fm = "".join(raw_lines[1:i])
            body = "".join(raw_lines[i + 1 :])
            return fm, body
    return None, text


def strip_frontmatter(text: str) -> str:
    """Remove leading YAML frontmatter (Obsidian / common Markdown convention)."""
    _, body = parse_frontmatter(text)
    return body.lstrip("\n")


_SKIP_INDEX_RE = re.compile(
    r"(?im)^\s*sygnif_neurolinked_skip_index:\s*true\s*$",
)


def frontmatter_skips_neurolinked_index(frontmatter: str | None) -> bool:
    """When ``true``, vault sync only refreshes fingerprints (no knowledge row / no digest)."""
    if not frontmatter:
        return False
    return bool(_SKIP_INDEX_RE.search(frontmatter))


def _vault_state_path(knowledge_db_path: str | None) -> Path:
    if knowledge_db_path:
        base = Path(knowledge_db_path).resolve().parent
    else:
        base = Path(__file__).resolve().parents[1] / "brain_state"
    base.mkdir(parents=True, exist_ok=True)
    return base / "obsidian_vault_sync.json"


def _iter_markdown_files(vault: Path):
    for path in vault.rglob("*.md"):
        try:
            rel = path.relative_to(vault)
        except ValueError:
            continue
        if any(p in _SKIP_DIR_PARTS for p in rel.parts[:-1]):
            continue
        yield path, rel.as_posix()


def _read_note_body(path: Path, max_bytes: int) -> str:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return raw.decode("utf-8", errors="replace")


def sync_obsidian_vault_once(
    vault_root: str,
    *,
    knowledge_store: Any,
    brain: Any | None = None,
    text_encoder: Any | None = None,
    state_path: str | Path | None = None,
    max_file_bytes: int = 1_500_000,
    store_body_max: int = 120_000,
    inject_digest_max: int = 8000,
) -> dict[str, Any]:
    """
    Scan vault, upsert changed notes into ``knowledge_store``, optionally inject a text digest.

    Returns counts and paths for logging.
    """
    vault = Path(vault_root).expanduser().resolve()
    out: dict[str, Any] = {
        "ok": False,
        "vault": str(vault),
        "scanned": 0,
        "stored": 0,
        "skipped_mirror": 0,
        "skipped_missing": False,
        "digest_chars": 0,
    }

    if not vault.is_dir():
        out["skipped_missing"] = True
        return out

    dbp = getattr(knowledge_store, "db_path", None)
    sp = Path(state_path) if state_path else _vault_state_path(str(dbp) if dbp else None)

    state: dict[str, Any] = {}
    if sp.is_file():
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    prev_root = state.get("vault_root")
    fps: dict[str, str] = dict(state.get("fingerprints") or {})
    if prev_root and Path(prev_root).resolve() != vault:
        fps = {}

    stored_paths: list[str] = []
    digest_parts: list[str] = []

    for path, rel_posix in _iter_markdown_files(vault):
        out["scanned"] += 1
        try:
            raw_text = _read_note_body(path, max_file_bytes)
        except OSError:
            continue
        fm_raw, body_after_fm = parse_frontmatter(raw_text)
        skip_knowledge = frontmatter_skips_neurolinked_index(fm_raw)
        body = body_after_fm.strip()
        if not body:
            body = "(empty note)"
        h = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        if fps.get(rel_posix) == h:
            continue
        fps[rel_posix] = h

        if skip_knowledge:
            out["skipped_mirror"] += 1
            continue

        text_for_store = body if len(body) <= store_body_max else (
            body[: store_body_max - 80] + "\n\n… [truncated for knowledge store]"
        )
        store_blob = f"[Obsidian · {rel_posix}]\n\n{text_for_store}"
        tags = ["obsidian", "vault", Path(rel_posix).stem.lower()[:48]]
        meta = {
            "vault_path": rel_posix,
            "vault_root": str(vault),
            "mtime": time.time(),
        }
        try:
            if hasattr(knowledge_store, "store"):
                knowledge_store.store(
                    store_blob,
                    source="obsidian_vault",
                    tags=tags,
                    metadata=meta,
                )
        except Exception:
            continue

        out["stored"] += 1
        stored_paths.append(rel_posix)
        snippet = body.replace("\r\n", "\n").replace("\n", " ")[:400]
        digest_parts.append(f"--- {rel_posix} ---\n{snippet}")

    state_out = {
        "vault_root": str(vault),
        "fingerprints": fps,
        "last_sync_unix": time.time(),
    }
    try:
        sp.write_text(json.dumps(state_out, indent=2), encoding="utf-8")
    except OSError:
        pass

    if digest_parts and brain is not None and text_encoder is not None:
        digest = "OBSIDIAN_VAULT_SYNC\n" + "\n\n".join(digest_parts)
        if len(digest) > inject_digest_max:
            digest = digest[: inject_digest_max - 40] + "\n… [digest truncated]"
        try:
            vec = text_encoder.encode(digest)
            brain.inject_sensory_input("text", vec, executive_boost=False)
            out["digest_chars"] = len(digest)
        except Exception:
            pass

    out["ok"] = True
    out["paths_stored"] = stored_paths
    return out
