# NeuroLinked - Neuromorphic Brain System

A living, learning neuromorphic brain that runs locally on your computer and integrates with **Sygnif** tooling, dashboards, and MCP-capable editors. Watch it grow, learn, and form associations in real-time through a stunning 3D visualization.

## What Is This?

NeuroLinked is a biologically-inspired artificial brain built with 100,000+ spiking neurons across 11 brain regions. It uses real neuroscience principles:

- **Spiking neurons** (Izhikevich model) that fire like real brain cells
- **STDP learning** - connections strengthen when neurons fire together ("neurons that fire together, wire together")
- **11 brain regions** - Sensory Cortex, Association Cortex, Hippocampus, Prefrontal Cortex, Motor Cortex, Cerebellum, Brainstem, and more
- **Neuromodulators** - dopamine (learning), acetylcholine (attention), norepinephrine (arousal), serotonin (calm)
- **Memory consolidation** - the hippocampus replays and strengthens important patterns
- **Development stages** - the brain matures from EMBRYONIC through JUVENILE and ADOLESCENT to MATURE

The brain grows as you feed it information. It automatically groups related inputs, forms associations, and strengthens pathways that get used repeatedly.

## Quick Start (3 Steps)

### Step 1: Install
Double-click **`install.bat`** and wait for it to finish. This installs all dependencies automatically.

Requirements:
- Windows 10/11
- Python 3.10 or newer ([download here](https://python.org/downloads) - check "Add Python to PATH" during install)

### Step 2: Start the Brain
Double-click **`start.bat`**. The brain starts and your browser opens to the 3D dashboard.

### Step 3: Connect MCP clients (optional)
The installer can register NeuroLinked as an MCP server for editors that support MCP (e.g. Claude Desktop, Claude Code, Cursor). Re-run setup if needed:
```
python setup_claude.py
```

Restart your MCP client after setup so brain tools appear.

## How it connects (MCP + HTTP)

NeuroLinked uses the **Model Context Protocol (MCP)** so agents and IDEs can call brain tools. Brain state for automation is also available over HTTP, e.g. **GET `/api/sygnif/summary`** (Sygnif CLI and host scripts use this). No cloud LLM is required for the core simulation.

### MCP-capable clients
The setup script adds NeuroLinked as an MCP server in the client config you choose. After setup:
1. Restart the client
2. NeuroLinked tools appear in the tools menu
3. Agents can read brain state, send observations, recall knowledge, and more

### Project context
Working in the NeuroLinked folder uses **CLAUDE.md** here as local documentation for agent-assisted edits (filename is a Cursor/Claude Code convention; content describes **Sygnif** HTTP APIs).

### Available Tools (via MCP)
| Tool | What it does |
|------|-------------|
| `read_brain` | Read brain state - attention, learning rate, active regions, memories |
| `brain_insights` | Get insights - novelty detection, energy, memory replay |
| `send_to_brain` | Send text/actions/context for the brain to learn |
| `brain_learned` | See what the brain has learned - associations, pathways, growth |
| `brain_status` | Check if the brain is running and connected |
| `save_brain` | Save brain state to disk immediately |
| `start_screen_observation` | Start watching your screen (brain learns from visual activity) |
| `stop_screen_observation` | Stop screen watching |

## The 3D Dashboard

Open http://localhost:8000 while the brain is running to see:

- **Live 3D brain** - color-coded neurons firing in real-time
- **Region activity bars** - see which brain regions are most active
- **Neuromodulator levels** - dopamine, acetylcholine, norepinephrine, serotonin
- **Development stage** - watch the brain mature over time
- **Claude Bridge panel** - connection status and interaction count
- **Text input** - type directly to feed the brain
- **Controls** - webcam, microphone, screen observation, save/load

## How the Brain Grows

Every time you (or Claude) send information to the brain:
1. The input gets encoded into spike patterns in the **Sensory Cortex**
2. The **Feature Layer** detects patterns and edges
3. The **Association Cortex** binds related features together
4. The **Concept Layer** forms abstract representations
5. The **Hippocampus** stores important patterns as memories
6. **STDP learning** strengthens connections that are used together
7. Over time, the brain develops stronger pathways for frequently-seen patterns

The brain auto-groups related inputs. If you feed it similar topics, it will activate the same concept neurons and build stronger associations between them.

## Brain Persistence

- The brain **auto-saves every 5 minutes** to the `brain_state/` folder
- It **auto-loads** the saved state when you start it again
- Use the SAVE button on the dashboard for immediate saves
- All synapse weights, memories, neuromodulator levels, and development progress are preserved

## File Structure

```
NeuroLinked/
  brain/           - Core neural engine
  sensory/         - Text, vision, audio encoders
  dashboard/       - 3D visualization (Three.js)
  brain_state/     - Saved brain data (auto-created)
  server.py        - FastAPI server
  mcp_server.py    - Claude MCP connection
  run.py           - Entry point
  install.bat      - One-time setup
  start.bat        - Launch the brain
  setup_claude.py  - Configure Claude connection
```

## Advanced Usage

### Scale the brain
```bash
python run.py --neurons 250000    # 250K neurons
python run.py --neurons 1000000   # Full 1M scale (needs 16GB+ RAM)
```

### Custom port
```bash
python run.py --port 9000
```

### Screen observation
The brain can watch your screen and learn from visual activity. Enable it from the dashboard or via Claude with the `start_screen_observation` tool.

## Troubleshooting

**Brain won't start?**
- Make sure Python 3.10+ is installed and in PATH
- Run `install.bat` again to reinstall dependencies

**Claude can't connect?**
- Make sure the brain is running (`start.bat`)
- Run `python setup_claude.py` to reconfigure
- Restart Claude Desktop after setup

**Dashboard shows no neurons?**
- Wait 10-30 seconds for the brain to initialize
- Refresh the browser page

**High CPU usage?**
- The default 100K neurons is optimized for modern hardware
- Use `python run.py --neurons 25000` for lower-end machines

## Requirements

- **OS**: Windows 10/11
- **Python**: 3.10 or newer
- **RAM**: 4GB minimum (8GB+ recommended for 100K+ neurons)
- **Browser**: Chrome, Edge, or Firefox (for 3D dashboard)
- **No API keys needed** - everything runs locally
