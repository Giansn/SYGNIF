"""
NeuroLinked MCP Server — exposes the brain to Sygnif and MCP-capable clients.

This Model Context Protocol (MCP) server exposes brain tools for editors and
agents (e.g. Cursor, Claude Desktop) when configured in the client.

The brain must be running (start.bat / start.sh) for this to work.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse


def _brain_base_url() -> str:
    """NeuroLinked HTTP base (Sygnif systemd default :8889; upstream default :8000)."""
    for key in (
        "NEUROLINKED_BRAIN_URL",
        "SYGNIF_NEUROLINKED_HTTP_URL",
        "SYGNIF_NEUROLINKED_HOST_URL",
    ):
        v = (os.environ.get(key) or "").strip().rstrip("/")
        if v:
            return v
    return "http://127.0.0.1:8000"


BRAIN_URL = _brain_base_url()
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries


def make_request(path, method="GET", data=None):
    """Make HTTP request to the brain server with retry logic."""
    import time

    url = f"{BRAIN_URL}{path}"
    headers = {"Content-Type": "application/json"}

    if data:
        body = json.dumps(data).encode("utf-8")
        req_factory = lambda: urllib.request.Request(url, data=body, headers=headers, method=method)
    else:
        req_factory = lambda: urllib.request.Request(url, headers=headers, method=method)

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            req = req_factory()
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
                continue
            return {
                "error": (
                    f"Cannot connect to NeuroLinked Brain at {BRAIN_URL}. "
                    "Make sure the brain is running first!\n\n"
                    "  Windows:  double-click start.bat\n"
                    "  Mac/Linux: ./start.sh\n"
                    "  Sygnif EC2:  systemctl status sygnif-neurolinked (often :8889); "
                    "set NEUROLINKED_BRAIN_URL or SYGNIF_NEUROLINKED_HTTP_URL for MCP.\n\n"
                    "Then try this tool again."
                )
            }
        except Exception as e:
            return {"error": str(e)}


def handle_tool_call(name, arguments):
    """Handle an MCP tool call."""
    if name == "read_brain":
        return make_request("/api/sygnif/summary")

    elif name == "brain_insights":
        return make_request("/api/claude/insights")

    elif name == "send_to_brain":
        obs_type = arguments.get("type", "text")
        content = arguments.get("content", "")
        return make_request("/api/claude/observe", "POST", {
            "type": obs_type,
            "content": content,
            "source": "sygnif"
        })

    elif name == "save_brain":
        return make_request("/api/brain/save", "POST")

    elif name == "brain_status":
        return make_request("/api/claude/status")

    elif name == "brain_learned":
        fmt = arguments.get("format", "summary")
        if fmt == "detailed":
            return make_request("/api/claude/learned")
        return make_request("/api/claude/learned/summary")

    elif name == "recall_knowledge":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        return make_request(f"/api/claude/recall?q={urllib.parse.quote(query)}&limit={limit}")

    elif name == "search_brain_memory":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 20)
        return make_request(f"/api/claude/search?q={urllib.parse.quote(query)}&limit={limit}")

    elif name == "remember":
        text = arguments.get("text", "")
        source = arguments.get("source", "sygnif")
        tags = arguments.get("tags", None)
        data = {"text": text, "source": source}
        if tags:
            data["tags"] = tags
        return make_request("/api/claude/remember", "POST", data)

    elif name == "brain_knowledge_stats":
        return make_request("/api/claude/knowledge")

    elif name == "start_screen_observation":
        return make_request("/api/screen/start", "POST")

    elif name == "stop_screen_observation":
        return make_request("/api/screen/stop", "POST")

    return {"error": f"Unknown tool: {name}"}


# MCP Protocol - stdio transport
TOOLS = [
    {
        "name": "read_brain",
        "description": "Read the NeuroLinked brain's current state - attention level, learning rate, arousal, surprise, active regions, memories, and motor output. Use this to understand what the brain is experiencing.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "brain_insights",
        "description": "Get insights from the brain - high surprise (novelty detected), low energy, memory replay, reflex triggers, working memory load. These are actionable signals from neural activity.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "send_to_brain",
        "description": "Send an observation to the brain so it can learn. Send 'text' for information, 'action' for things you did (boosts learning), or 'context' for what you're working on (boosts alertness).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["text", "action", "context"],
                    "description": "Type of observation: text (information), action (something you did), context (current task)"
                },
                "content": {
                    "type": "string",
                    "description": "The content to send to the brain"
                }
            },
            "required": ["type", "content"]
        }
    },
    {
        "name": "save_brain",
        "description": "Save the brain's current state to disk. The brain auto-saves every 5 minutes, but use this for an immediate save.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "brain_status",
        "description": "Check if the brain is running and connected. Shows session uptime, interaction count, and screen observer state.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "start_screen_observation",
        "description": "Start the brain's screen observer - it watches your screen and learns from visual activity.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "stop_screen_observation",
        "description": "Stop the brain's screen observer.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "brain_learned",
        "description": "See what the brain has learned - discovered associations, strongest pathways, memory usage, synapse growth, and region specialization. Use 'summary' for a plain-English report or 'detailed' for structured data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Output format: 'summary' for plain English, 'detailed' for structured JSON data"
                }
            }
        }
    },
    {
        "name": "recall_knowledge",
        "description": "Recall stored knowledge about a specific topic. The brain remembers everything sent to it and can retrieve facts, notes, and observations by topic. Use this to look up what the brain knows about something specific.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic or keywords to recall (e.g. 'client preferences', 'meeting notes', 'project goals')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 10)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_brain_memory",
        "description": "Full-text search across ALL stored knowledge in the brain. Searches the complete text of every observation, note, and fact ever stored. More thorough than recall.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text (e.g. 'blue branding', 'Q3 revenue')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 20)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "remember",
        "description": "Store a new piece of knowledge in the brain's memory. Use this to save facts, notes, decisions, meeting summaries, client info, or anything that should be remembered for later. The brain stores the full text and makes it searchable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The knowledge to store (full text, notes, facts, etc.)"
                },
                "source": {
                    "type": "string",
                    "description": "Where this came from (e.g. 'sygnif', 'user', 'meeting')"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional topic tags for easier recall"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "brain_knowledge_stats",
        "description": "Get statistics about the brain's knowledge store - total entries, topics, sources, and recent additions. Shows how much the brain knows and what it knows about.",
        "inputSchema": {"type": "object", "properties": {}}
    },
]


def write_message(msg):
    """Write a JSON-RPC message to stdout."""
    content = json.dumps(msg)
    header = f"Content-Length: {len(content)}\r\n\r\n"
    sys.stdout.write(header + content)
    sys.stdout.flush()


def read_message():
    """Read a JSON-RPC message from stdin."""
    # Read headers
    headers = {}
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            break
        key, value = line.split(": ", 1)
        headers[key] = value

    # Read content
    length = int(headers.get("Content-Length", 0))
    if length > 0:
        content = sys.stdin.read(length)
        return json.loads(content)
    return None


def main():
    """Run the MCP server on stdio."""
    while True:
        try:
            msg = read_message()
            if msg is None:
                break

            method = msg.get("method", "")
            msg_id = msg.get("id")

            if method == "initialize":
                write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "neurolinked-brain",
                            "version": "1.0.0"
                        }
                    }
                })

            elif method == "notifications/initialized":
                pass  # No response needed

            elif method == "tools/list":
                write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": TOOLS}
                })

            elif method == "tools/call":
                tool_name = msg["params"]["name"]
                arguments = msg["params"].get("arguments", {})
                result = handle_tool_call(tool_name, arguments)
                write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": json.dumps(result, indent=2, default=str)
                        }]
                    }
                })

            elif msg_id is not None:
                write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                })

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            sys.stderr.write(f"MCP Error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
