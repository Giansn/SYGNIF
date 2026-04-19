# How to Update NeuroLinked Without Losing Your Brain

Your brain's memories, learned synapse weights, development stage, and knowledge
store all live in the `brain_state/` folder. Code updates should NEVER touch
that folder.

## The Safe Update Process

### Windows
1. **Stop the brain** (close the terminal running start.bat, or press Ctrl+C)
2. Download the new release zip from Discord
3. Extract it to a **NEW** folder (don't overwrite the old one yet)
4. Copy the `brain_state/` folder from your old install → into the new folder
5. Delete the old install folder
6. Run `install.bat` (only if it's a new dependency) then `start.bat`

### Mac / Linux
Same steps as Windows but use `install.sh` and `start.sh`.

## Or the even easier way

Just replace these files/folders in your existing install with the new versions:

    brain/         (the simulation code)
    dashboard/     (the 3D visualization)
    sensory/       (input encoders)
    server.py      (API server)
    run.py         (launcher)
    mcp_server.py  (MCP / Sygnif tooling)
    setup_claude.py
    install.bat / install.sh
    start.bat / start.sh

**LEAVE THESE ALONE** (these contain your brain's life):

    brain_state/          <-- ALL your memories & learned synapses
    brain_state/backups/  <-- Automatic versioned backups (last 10 saves)

## Built-In Safety Features

As of V1.1, the brain protects itself from accidental wipes:

1. **Auto-backup before every save** — `brain_state/backups/` keeps the last 10
   versions automatically. If anything goes wrong, you can roll back.

2. **Save-lock on mismatch** — If you start the brain with a different neuron
   count than your saved state, saving is LOCKED until you either:
   - Restart with the correct neuron count (run.py auto-detects this now), or
   - Explicitly unlock it via the API (confirms you want to discard old state)

3. **Auto-matching neuron count** — `python run.py` with no args now reads your
   saved state and matches its neuron count automatically. You won't accidentally
   boot at 100k and lock out your 10k brain anymore.

## Backup Management

View all backups:
    GET  http://localhost:8000/api/brain/backups

Restore a specific backup:
    POST http://localhost:8000/api/brain/restore-backup
    Body: {"name": "20260414_013328_ADOLESCENT_5600000steps_10000n"}

Check if save is locked:
    GET  http://localhost:8000/api/brain/lock-status

Force-unlock (discards old state):
    POST http://localhost:8000/api/brain/unlock
    Body: {"confirm": true}

## Start Fresh (Nuclear Option)

If you really want to start over:
    python run.py --fresh

This ignores the saved state but keeps backups so you can come back if needed.
