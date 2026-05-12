"""
NeuroLinked — MCP client setup (Sygnif / editors)

Registers the NeuroLinked MCP server with clients that support MCP
(e.g. Claude Desktop, Claude Code, Cursor). Run once after installation.

Works on Windows, Mac, and Linux.

Usage: python setup_claude.py
"""

import os
import json
import sys
import platform


def get_brain_dir():
    return os.path.dirname(os.path.abspath(__file__))


def get_python_command():
    """Get the best python command that will work from any context."""
    # Use the full path to the running Python interpreter
    exe = sys.executable
    if exe and os.path.exists(exe):
        return exe
    # Fallback: try common names
    if platform.system() != "Windows":
        return "python3"
    return "python"


def get_claude_desktop_config_path():
    """Get Claude Desktop config path for any OS."""
    system = platform.system()

    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return os.path.join(appdata, "Claude", "claude_desktop_config.json")

    elif system == "Darwin":  # macOS
        home = os.path.expanduser("~")
        return os.path.join(home, "Library", "Application Support", "Claude",
                            "claude_desktop_config.json")

    elif system == "Linux":
        # Try XDG config first, fall back to ~/.config
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        if not xdg:
            xdg = os.path.join(os.path.expanduser("~"), ".config")
        return os.path.join(xdg, "Claude", "claude_desktop_config.json")

    return None


def setup_claude_code():
    """Set up Claude Code to auto-connect to the brain."""
    brain_dir = get_brain_dir()

    # Create .claude directory in brain folder
    claude_dir = os.path.join(brain_dir, ".claude")
    os.makedirs(claude_dir, exist_ok=True)

    # The CLAUDE.md is already in the project root - Claude Code reads it automatically
    claude_md = os.path.join(brain_dir, "CLAUDE.md")
    if os.path.exists(claude_md):
        print("[OK] CLAUDE.md found - Claude Code will auto-connect when working in this folder")
    else:
        print("[INFO] Creating CLAUDE.md...")
        _create_claude_md(claude_md)
        print("[OK] CLAUDE.md created")

    return True


def setup_claude_desktop():
    """Set up Claude Desktop MCP connection (Windows, Mac, Linux)."""
    brain_dir = get_brain_dir()
    python_exe = get_python_command()
    mcp_script = os.path.join(brain_dir, "mcp_server.py")

    config_path = get_claude_desktop_config_path()
    if not config_path:
        print("[SKIP] Could not detect Claude Desktop config location for this OS")
        return False

    # Create MCP server entry for NeuroLinked
    mcp_entry = {
        "command": python_exe,
        "args": [mcp_script],
        "env": {}
    }

    # Read existing config or create new one
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            print(f"[OK] Found existing Claude Desktop config")
        except Exception:
            config = {}

    # Add or update NeuroLinked MCP server
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    config["mcpServers"]["neurolinked-brain"] = mcp_entry

    # Write config
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[OK] Claude Desktop configured!")
    print(f"     Config: {config_path}")
    print(f"     Python: {python_exe}")
    print(f"     MCP:    {mcp_script}")
    print(f"     >> Restart Claude Desktop to connect <<")

    return True


def setup_claude_code_global():
    """Add NeuroLinked to Claude Code's global settings."""
    brain_dir = get_brain_dir()
    python_exe = get_python_command()
    mcp_script = os.path.join(brain_dir, "mcp_server.py")

    # Claude Code settings location
    home = os.path.expanduser("~")
    claude_settings = os.path.join(home, ".claude", "settings.json")

    # Create MCP server config for Claude Code
    mcp_config = {
        "command": python_exe,
        "args": [mcp_script]
    }

    settings = {}
    if os.path.exists(claude_settings):
        try:
            with open(claude_settings, "r") as f:
                settings = json.load(f)
        except Exception:
            pass

    if "mcpServers" not in settings:
        settings["mcpServers"] = {}

    settings["mcpServers"]["neurolinked-brain"] = mcp_config

    os.makedirs(os.path.dirname(claude_settings), exist_ok=True)
    with open(claude_settings, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"[OK] Claude Code global settings updated")
    print(f"     Settings: {claude_settings}")

    return True


def test_connection():
    """Test if the brain is reachable (GET /api/sygnif/summary)."""
    import urllib.request
    import urllib.error

    print("[TEST] Checking brain connection at http://localhost:8000 ...")

    try:
        req = urllib.request.Request(
            "http://localhost:8000/api/sygnif/summary",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            stage = data.get("stage", "unknown")
            step = data.get("step", 0)
            print(f"[OK] Brain is LIVE! Stage: {stage}, Step: {step:,}")
            return True
    except urllib.error.URLError:
        print("[INFO] Brain is not running right now - that's OK!")
        print("       Start it first with start.bat (Windows) or ./start.sh (Mac/Linux)")
        print("       Then your MCP client can connect when you use it.")
        return False
    except Exception as e:
        print(f"[WARN] Connection test error: {e}")
        print("       Start the brain first, then try again.")
        return False


def _create_claude_md(path):
    content = """# NeuroLinked Brain — Sygnif integration

This project has a neuromorphic brain running at http://localhost:8000.

## Quick API Reference
- GET /api/sygnif/summary - Read brain state (canonical)
- GET /api/claude/summary - Legacy redirect to /api/sygnif/summary
- POST /api/claude/observe - Send observations (body: {"type":"text","content":"...","source":"sygnif"})
- GET /api/claude/insights - Get brain insights
- GET /api/claude/recall?q=topic - Recall knowledge about a topic
- GET /api/claude/search?q=query - Full-text search all knowledge
- POST /api/claude/remember - Store knowledge (body: {"text":"...","source":"sygnif"})
- POST /api/brain/save - Save brain state

## MCP tools
When connected via MCP, clients expose: read_brain, brain_insights, send_to_brain,
save_brain, recall_knowledge, search_brain_memory, remember, brain_knowledge_stats,
start_screen_observation, stop_screen_observation, brain_learned, brain_status.
"""
    with open(path, "w") as f:
        f.write(content)


def main():
    print()
    print("  ==========================================")
    print("  NEUROLINKED - MCP / Sygnif connection setup")
    print(f"  OS: {platform.system()} {platform.release()}")
    print("  ==========================================")
    print()

    # Step 1: Claude Code (local project)
    print("[1/4] Setting up Claude Code (project-level)...")
    setup_claude_code()
    print()

    # Step 2: Claude Code (global MCP)
    print("[2/4] Setting up Claude Code (global MCP server)...")
    try:
        setup_claude_code_global()
    except Exception as e:
        print(f"[SKIP] Could not set up global Claude Code: {e}")
    print()

    # Step 3: Claude Desktop
    print("[3/4] Setting up Claude Desktop...")
    try:
        setup_claude_desktop()
    except Exception as e:
        print(f"[SKIP] Could not set up Claude Desktop: {e}")
    print()

    # Step 4: Test connection
    print("[4/4] Testing brain connection...")
    brain_live = test_connection()
    print()

    print("  ==========================================")
    print("  Setup complete!")
    print()
    if platform.system() == "Windows":
        print("  To start the brain:  double-click start.bat")
    else:
        print("  To start the brain:  ./start.sh")
    print()
    print("  IMPORTANT:")
    print("    1. Start the brain FIRST (it must be running)")
    print("    2. THEN open Claude (Desktop or Code)")
    print("    3. Claude auto-connects via MCP")
    print()
    print("  If Claude doesn't see the brain tools:")
    print("    - Make sure the brain is running (http://localhost:8000)")
    print("    - Restart Claude Desktop completely")
    print("    - For Claude Code: close and reopen your terminal")
    print()
    print("  No API key needed - everything runs locally.")
    print("  ==========================================")
    print()


if __name__ == "__main__":
    main()
