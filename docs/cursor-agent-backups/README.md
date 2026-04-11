# Cursor subagent backups

Point-in-time copies of **`.cursor/agents/*.md`** (Cursor subagent prompts) for recovery if files are overwritten.

| Snapshot | Files |
|----------|--------|
| 2026-04-11 | `finance-agent-2026-04-11.md`, `btc-specialist-2026-04-11.md` |

**Canonical live paths:** `.cursor/agents/finance-agent.md`, `.cursor/agents/btc-specialist.md`  
**Skill router:** `.cursor/skills/finance-agent/SKILL.md` → links to fused KB in `agents/`.

Refresh a snapshot after intentional edits:

```bash
cp .cursor/agents/finance-agent.md "docs/cursor-agent-backups/finance-agent-$(date +%F).md"
cp .cursor/agents/btc-specialist.md "docs/cursor-agent-backups/btc-specialist-$(date +%F).md"
```
