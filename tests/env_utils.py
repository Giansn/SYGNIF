"""Shared test helpers (not collected as tests)."""

from __future__ import annotations

import os

import pytest


def delenv_strict(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """``monkeypatch.delenv(..., raising=True)`` even when ``name`` was unset in the parent env."""
    if name not in os.environ:
        monkeypatch.setenv(name, "")
    monkeypatch.delenv(name, raising=True)
