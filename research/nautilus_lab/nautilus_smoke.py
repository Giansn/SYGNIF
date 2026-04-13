#!/usr/bin/env python3
"""Verify nautilus_trader import inside the nautilus-research image."""
import json

try:
    import nautilus_trader

    ver = getattr(nautilus_trader, "__version__", "unknown")
    print(json.dumps({"ok": True, "nautilus_trader": ver}))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
    raise SystemExit(1)
