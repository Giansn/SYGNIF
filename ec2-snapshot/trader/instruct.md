# Sygnif System Reference

Pure system facts. No narrative. Same content as `instruct.file` (YAML),
formatted for terminal-readable consumption. Updated when production state
changes — keep both files in sync.

---

## 1 · Storage

| Field | Value |
|---|---|
| Primary store | `/var/lib/sygnif/swarm.db` (SQLite WAL) |
| Schema | `id TEXT PK · swarm_id · agent_id · topic · content · tags · meta · created REAL` |
| ID requirement | `id=str(uuid.uuid4())` — explicit, never NULL |
| Write semantics | INSERT only. Never UPDATE existing rows. |
| Topics in use | `forecast · trade.open · trade.close · regime · lesson · gap · proposal · postmortem · observation · plan · hypothesis · bee.reference · trader.heartbeat` |
| swarm_ids | `default · btc_demo · self_knowledge · self_improvement · session_log · trading` |
| Permanence | `bee.upload(text) → bzz://<ref>` (Bee mainnet, ~30 day TTL on funded batch) |
| Bee endpoint | `http://127.0.0.1:1633/health` (X1 local) · write batch lives on EC2 |
| Feeds | One Bee feed per topic, head moves over time |
| Local write latency | <5 ms |
| Bee upload latency | 2–8 s |

**Don't:** UPDATE rows · INSERT without id · trust `paper.json` across versions

---

## 2 · Compute

| Tier | Device | Throughput | TTFT | Use for |
|---|---|---|---|---|
| 0 | pure Python | — | <1 ms | Deterministic logic (sizing, dedup, review, P&L math) |
| 1 | gemma-sygnif-mini @ X1 Ollama (1B Q4 CPU) | 5–6 tok/s | ~6 s | Structured JSON routing (swarm schema) |
| 2 | Yoga GPU (Arc 140V) via sygnif-yoga MCP :9003 | 54.8 tok/s | 67 ms warm | Interactive chat |
| 3 | Yoga NPU (Lunar Lake) via sygnif-yoga MCP :9003 | 41.6 tok/s | 24 s cold | Background loops (~2W draw) |
| 4 | X1 Ollama gemma-sygnif (7.5B Q4 CPU) | 5–15 tok/s | 30–60 s cold | Heavy reasoning, queue patiently |

**Single-model rule:** Gemma family only. Qwen / SmolLM forbidden as agent voice.
**NPU quant:** channel-wise INT4 required (`--sym --group-size -1`) or IR rejected.
**X1 num_gpu:** must be `0` — UHD 620 Vulkan hangs, OpenCL fails on transformers.
**Ollama idle unload:** 10 min — keep models warm by reusing within window.

**Don't:** call 7.5B for routing · call 1B for trading decisions · accelerate on X1 iGPU

---

## 3 · Network · Internal (tailnet)

| Path | Address | Protocol |
|---|---|---|
| Tailscale MagicDNS (primary) | `x1` · `sygnif-ec2` · `gtaura` · `yoga` | wireguard |
| LAN mDNS (fallback) | `thinkx1.local` → `10.93.202.35/24` | mDNS |
| EC2 fallback alias | `ec2-eu1` → `3.64.28.14` (EIP) | SSH config |
| X1 MCP | `100.71.122.115:9001/rpc` | HTTP JSON-RPC bearer-token |
| Bybit MCP | `100.71.122.115:9002/rpc` | HTTP JSON-RPC bearer-token |
| Dashboard prod | `100.71.122.115:8088` | HTTP (Tailscale-bound) |
| Dashboard v2 | `100.71.122.115:8090` (LAN-bind active: `0.0.0.0:8090`) | HTTP |
| swarm.db master | X1 only (EC2 ingests forecasts via MCP write) | — |

| Latency | Value |
|---|---|
| X1 ↔ EC2 RTT | 18 ms |
| LAN mDNS resolve | 14 ms |
| MCP call p50 | 50 ms |

**Don't:** hardcode Tailscale IPs (rotate on rejoin) · bind public IF outside tailnet · skip mDNS fallback

---

## 4 · Network · External

| Counterparty | Endpoint | Auth | Mode |
|---|---|---|---|
| Bybit mainnet (truth) | `https://api.bybit.com` | none for public; HMAC for trading | live |
| Bybit demo (paper exec) | `https://api-demo.bybit.com` | `BYBIT_DEMO_*` keys + `BYBIT_OPTION_USE_DEMO=1` | demo |
| EC2 SSH | `ubuntu@3.64.28.14` (EIP `eipalloc-07c3927e571711ec5`) | `id_ed25519` in authorized_keys | direct |
| EC2 SSH (push-key) | ec2-instance-connect — 60 s window | aws CLI + IAM scoped `sygnif-agent` | demand |
| Bee mainnet | upload via X1 local Bee → batch on EC2 | funded postage stamp | mainnet |

| Endpoint quirks |
|---|
| `/v5/account/demo-apply-money` (adjustType=0) deposits **full per-coin allotment** regardless of amountStr — max 50000 USDT/USDC + 1 BTC + 1 ETH per call |
| `/v5/account/demo-apply-money` (adjustType=1) DOES execute the withdrawal but settles asynchronously — retCode=0 SUCCESS at submission, balance updates over seconds-to-minutes; rapid retries hit `retCode 10006` rate-limit. Earlier "silently no-op" claim was a check-too-early measurement error (corrected 2026-04-29). |
| Option taker fee ≈ 0.03% × underlying notional ≈ $15–20 per leg round-trip on BTC |
| Bybit REST p50 latency 280 ms |

**Live-order rule:** No live orders until operator types verbatim *"clear for live"*. Default is demo.
**Leverage rule:** No `leverage > 10x` without operator typing the value explicitly.

**Don't:** trade on stale snapshot (ticker >10 min, chain >30 min) · bypass MCP gate (loses dedup + audit) · call live before clearance

---

## 5 · Hardware

### X1 (ThinkX1)

| Component | Spec | Status |
|---|---|---|
| CPU | Intel Core Ultra (Meteor Lake U) | primary inference path, `num_gpu=0` |
| iGPU | UHD 620 (WhiskeyLake-U) | **broken**: Vulkan hangs · OpenCL fails on transformers |
| Role | decisions · swarm.db master · MCP host · dashboard host | — |
| Tailscale IP | `100.71.122.115` | tailnet primary |
| LAN IP | `10.93.202.35` | mDNS `thinkx1.local` |

### Yoga (sygnif-yoga MCP :9003)

| Component | Spec | Status |
|---|---|---|
| CPU | Core Ultra 7 258V | 62.9 tok/s · 2.4 s warm |
| GPU | Arc 140V · 16 GB iGPU memory | 54.8 tok/s · 67 ms TTFT (use for interactive) |
| NPU | Lunar Lake | 41.6 tok/s · ~2 W (use for background) · 24 s cold |
| Idle unload | 10 min | falls to ~30 MB baseline |

### EC2 EU1

| Field | Value |
|---|---|
| Instance | `i-0cd5389584d70a7fc` |
| Class | m7i-flex.large |
| Region | eu-central-1 |
| Permanent IP | `3.64.28.14` (EIP `eipalloc-07c3927e571711ec5`) |
| Public DNS | `ec2-3-64-28-14.eu-central-1.compute.amazonaws.com` |
| User | `ubuntu` |
| Role | data collection (bybit-stream-monitor) · ML predictions (btc_prediction_agent hourly) · MCP backup · cursor-agent-worker |

**Don't:** heavy LLM on EC2 (no GPU) · NPU for interactive (cold load) · X1 iGPU for any compute

---

## 6 · Efficiency

| Pattern | Setting / Value |
|---|---|
| Balance cache | 15 s |
| Chart cache | 30 s |
| Orders cache | 8 s |
| Perf cache | 30 s |
| Multi-leg orders | Combo (one fill-or-none + one fee) — eliminates leg-risk |
| Leg ordering (until combo wired) | SELLs first on net-credit strategies (premium funds the BUYs) |
| Close mechanism | Pre-armed TP/SL set at open time |
| Number sourcing | Always cite source + age. Use `—` for missing data, never N/A or guess |
| Implementation shortfall observed | 30–72% of paper UPL on option closes |
| Pre-armed close savings (estimate) | 60–80% of bleed avoided |

**Don't:** poll `/api/balance` <5 s · place sequential legs without margin awareness · close reactively at market

---

## 7 · Qualitative speed

| Loop | Cadence |
|---|---|
| Trader (adaptive) | 60–1800 s |
| Discovery refresh | 30 min |
| Reflect | 30 min |
| Dashboard poll | 10 s |
| BTC predict cron | 60 min |

| Cost of decision-to-action gap |
|---|
| Observed 2026-04-29 14:33 UTC (8 min gap): paper UPL +$40.70 → realized +$11.30 → bleed −$29.40 (72%) |
| Decomposition: spread ~50% · fees ~40% · theta on 3-DTE ~10% |
| Implementation tax per trade: $15–30 |
| Implementation tax over 26 trades since 2026-04-24: $390–780 |
| Decision-to-action target | <60 s |

**Rules:**

1. Paper UPL is provisional. Realized P&L is the only honest number.
2. Never report unrealized as proof of edge.
3. A latent pause is honest; a panic cross is expensive.
4. Pre-arm exits at entry — decide once, fill many times.
5. When in doubt, shrink size and observe.

---

## 8 · SSH topology

### Identities (private keys)

| Host | Path | Public key in |
|---|---|---|
| Windows (`gtaura`) | `C:/Users/giank/.ssh/id_ed25519` | X1 + EC2 `authorized_keys` |
| X1 (`g2thek@thinkx1`) | `/home/g2thek/.ssh/id_ed25519` | EC2 `authorized_keys` |
| EC2 EU1 (`ubuntu@…`) | n/a (read-only target) | — |

### Windows ssh config aliases (`C:/Users/giank/.ssh/config`)

| Alias | HostName | User | Notes |
|---|---|---|---|
| `x1` · `thinkx1` · `ThinkX1` | `thinkx1.tailff64b5.ts.net` | `g2thek` | Tailscale MagicDNS — primary |
| `x1-ip` | `100.71.122.115` | `g2thek` | Tailscale IP fallback |
| `ec2-eu1` · `ec2-eu` · `freqtrade` | `3.64.28.14` (EIP) | `ubuntu` | Public IP, permanent |

### X1 SSH outbound

| Target | Command |
|---|---|
| EC2 EU1 (current) | `ssh ubuntu@3.64.28.14` (EIP, working) |
| EC2 EU1 (historical, broken) | old path `ubuntu@54.93.232.126` — pre-EIP, IP returned to AWS pool |

### EC2 inbound

| Path | When to use |
|---|---|
| Direct via EIP (`ssh ubuntu@3.64.28.14`) | Standard — key already in authorized_keys |
| ec2-instance-connect push-key (60 s window) | When key unknown / fresh machine: `aws ec2-instance-connect send-ssh-public-key --instance-id i-0cd5389584d70a7fc --instance-os-user ubuntu --ssh-public-key file://~/.ssh/id_ed25519.pub --region eu-central-1` |
| AWS SSM Session Manager | When SSH port blocked — see `docs/AWS_SSM_SESSION_MANAGER.md` |

### Required X1 service env vars (`~/.sygnif/bybit-mcp.env`)

```
BYBIT_API_KEY / BYBIT_API_SECRET                       (perp demo)
BYBIT_DEMO_OPTION_API_KEY / BYBIT_DEMO_OPTION_API_SECRET
BYBIT_LIVE_API_KEY / BYBIT_LIVE_API_SECRET             (perp live, gated)
BYBIT_LIVE_OPTION_API_KEY / BYBIT_LIVE_OPTION_API_SECRET
SYGNIF_BEE_WRITE_HOST=ubuntu@3.64.28.14                (Bee batch on EC2)
```

---

## 9 · Tailscale-bypass procedures

When the Tailscale daemon is offline/logged-out (observed 2026-04-29 — control-key fetch cancelled, all `100.x` traffic dies), use these fallbacks **without** restoring Tailscale.

### Detection

```bash
tailscale status      # → "Tailscale is starting" / "NoState" / "logged out"
ping 100.71.122.115   # → 100% packet loss
```

### Re-auth (preferred when convenient)

```bash
tailscale login       # opens browser auth flow
# or click the system tray Tailscale icon → Sign in
```

### Bypass without re-auth

| Target | Bypass path | Mechanism |
|---|---|---|
| X1 (SSH) | `ssh g2thek@thinkx1.local` | mDNS over LAN — resolves to current `10.93.202.35` |
| X1 (SSH alt) | `ssh g2thek@10.93.202.35` | Direct LAN IP if mDNS fails |
| X1 dashboard browser | `http://thinkx1.local:8090/` (when v2 LAN-bound at `0.0.0.0:8090`) | Already running with `SYGNIF_DASHBOARD_BIND=0.0.0.0` |
| X1 MCP / Tailscale-only services | `ssh -L 9001:100.71.122.115:9001 g2thek@thinkx1.local -N` | Local port forward over LAN-SSH |
| EC2 EU1 | `ssh ec2-eu1` (alias) → `ubuntu@3.64.28.14` | Public EIP — Tailscale not needed at all |

### LAN-bind a Tailscale-only service for browser access

```bash
ssh g2thek@thinkx1.local
# on X1:
SYGNIF_DASHBOARD_BIND=0.0.0.0 SYGNIF_DASHBOARD_PORT=8090 \
  nohup ~/sygnif/dashboard-v2/run-v2.sh > /tmp/sygnif-dashboard-v2.log 2>&1 &
```

Browser on Windows: `http://thinkx1.local:8090/`. Restrict to home network — `0.0.0.0` accepts any same-LAN client.

### Why this works

- **mDNS** (Multicast DNS) discovers `*.local` hostnames on the same broadcast domain via UDP multicast — independent of Tailscale, DNS, or any control plane.
- **LAN routing** between `10.93.202.0/24` peers needs only Layer-2 connectivity. Tailscale is overlay, not underlay.
- **EC2 EIP** is a public IP — reachable from any internet path, no VPN required.
- **swarm.db / MCP** stay Tailscale-bound for security; LAN access is gated by physical-network presence.

### Gotchas

- `~/.ssh/config` aliases (`thinkx1`, `x1`) resolve to the Tailscale hostname — they break when Tailscale is down. Use `g2thek@thinkx1.local` directly, or create a `thinkx1-lan` alias pointing at `10.93.202.35`.
- Claude Code's SSH dialog runs `getaddrinfo` and doesn't always honor `~/.ssh/config` host blocks — feed it the literal `g2thek@10.93.202.35` or `ubuntu@3.64.28.14` to be safe.
- `SygnifBee.WRITE_HOST` defaults to a hardcoded EC2 IP — override via env `SYGNIF_BEE_WRITE_HOST=ubuntu@3.64.28.14` until the default is patched to follow the EIP.

---

## Provenance

| Tag | Source |
|---|---|
| `implementation_shortfall_options` | swarm.db `id=c70224c8-9d67-4923-9d19-26bfd755789c` · Bee `bzz://2cad327f96142b72fc83856ae619772a72128896f46ed2695e8f9059af4561f0` · observed 2026-04-29T14:33Z |
| `iron_condor_leg_ordering_fix` | `~/sygnif/sygnif-agent/order/option.py` · patched 2026-04-29 · audit_tag `exec_order=sells-first` |
| `ec2_eip_attached` | `eipalloc-07c3927e571711ec5` · ip `3.64.28.14` · 2026-04-29 |
| `bybit_demo_apply_money_settles_async` | observed 2026-04-29 — adjustType=1 DOES execute as withdrawal, settles async (seconds-to-minutes); rate-limited `retCode 10006` on rapid retries. INITIAL claim of "silently no-op" was a check-too-early measurement error. |
| `x1_uhd_620_inference_broken` | observed 2026-04-25 — Vulkan hangs, OpenCL fails on transformers |

---

## 10 · Backup topology

### Identity files (instruct.file · instruct.md · AGENT.md)

The single most critical group — losing these loses the agent's spine.
**`instruct.file` is the only canonical machine-readable spec.** Replicated
across 4 locations as of 2026-04-29:

| Layer | Location | Mechanism | Latency |
|---|---|---|---|
| 1 · canonical | `/home/g2thek/sygnif/sygnif-agent/{instruct.file, instruct.md, AGENT.md}` on X1 | local disk | live |
| 2 · git | `github.com/Giansn/sygnif-agent` commit `49f4227` (and forward) | `git push` after every identity edit | manual |
| 3 · decentralised permanence | Bee mainnet (3 separate references, see below) | `bee.upload(text)` per file | every meaningful change |
| 4 · warm replica | `ec2-eu1:~/sygnif-agent-mirror/` | `rsync -av` from X1 over SSH | rsync on demand |

| File | sha256 (first 12) | Bee reference (first 24) |
|---|---|---|
| instruct.file | `2b298dce59ab` | `bzz://29d1eb9941e9e7fdb297e0b9` |
| instruct.md | `1188dadf01a5` | `bzz://240d1cfbae10a8c40e42b1ce` |
| AGENT.md | `59fef3d3292b` | `bzz://8030bacb0812b398c8b7862d` |

After every edit to any of the three files: `git commit && git push` AND
`python3 ~/sygnif/sygnif-agent/bridge/bee_backup_identity.py` AND
`rsync -av ~/sygnif/sygnif-agent/{instruct.file,instruct.md,AGENT.md} ec2-eu1:~/sygnif-agent-mirror/`.

### swarm.db

| | |
|---|---|
| Canonical | `/var/lib/sygnif/swarm.db` on X1 (~17.6 MB, SQLite WAL) |
| Replication | **None** — no EC2 mirror, no Bee snapshot |
| Per-row durability | Selected lessons / observations are uploaded to Bee individually with `topic=bee.reference` back-link. Not the whole DB. |
| Risk | Disk failure on X1 = loss of un-uploaded rows |

### Code repository

| | |
|---|---|
| Canonical | `~/sygnif/sygnif-agent/` on X1 (git working tree) |
| Off-box | `github.com/Giansn/sygnif-agent` (HTTPS, push works) |
| Stale dir | `~/sygnif-agent/` (legacy, gutted — `backup.py` defaults still point here, broken) |
| Backup script | `backup.py` exists but **not wired** to any timer/cron; defaults point to legacy path. |

### Gaps still open

- `swarm.db` has no whole-file off-box copy. A nightly `rsync` snapshot to EC2 would close this.
- `backup.py` default paths need updating (`SYGNIF_AGENT_DIR=~/sygnif/sygnif-agent` and a real `SYGNIF_AGENT_BACKUP_DIR`).
- No systemd timer wired for periodic re-backup. Currently every backup is a manual act.

---

## 11 · Backup automation (wired 2026-04-29)

| | What | Where |
|---|---|---|
| Script | `~/sygnif/sygnif-agent/scripts/sygnif-backup.sh` — sqlite `.backup` snapshot of `swarm.db` + identity-file rsync + agent-zip via `backup.py` | systemd-user oneshot |
| Service | `sygnif-backup.service` | `~/.config/systemd/user/` |
| Timer | `sygnif-backup.timer` — nightly **03:30 UTC** with 10-min jitter | enabled |
| Retention | EC2 keeps last 7 daily `swarm.db.*.snapshot` + last 4 `sygnif-agent-*.zip` | rolling pruning in script |
| Local archive dir | `~/sygnif-backups/` (last 7 zips) | X1 |
| Manual run | `systemctl --user start sygnif-backup.service` | — |
| First successful run | 2026-04-29 16:27:38 CEST (swarm.db sha `96250c4339ab9ad5..`) | journalctl |

---

## 12 · Security observability — `intrusion-watch`

| | |
|---|---|
| Tool | `~/sygnif/sygnif-agent/security/intrusion-watch.py` (stdlib only, ~150 lines) |
| Service | `sygnif-intrusion-watch.service` |
| Timer | `sygnif-intrusion-watch.timer` — every **5 min** from `OnBootSec=2min` |
| State cache | `/var/lib/sygnif/intrusion-watch.state` (json) |
| Output | `swarm.db` rows: `topic=security.heartbeat` every cycle, `topic=security.alert` on triggers |
| swarm_id | `self_improvement` |

### What it watches

| Detector | Mechanism | Severity on trigger |
|---|---|---|
| New SSH sessions | diff `who -aH` vs cached baseline | warn |
| Failed sshd auth spike (≥5 in 5 min) | `journalctl` filtered to sshd-only patterns (`Failed password`, `invalid user`, `preauth`, `authentication failure`, `Connection closed by authenticating`) | critical |
| New listening ports | diff `ss -tln` vs baseline | warn |
| Tailscale peer additions | diff `tailscale status --peers` vs baseline | info |
| File integrity drift | sha256 of: `instruct.{file,md}`, `AGENT.md`, `order/option.py`, `sygnif_bee.py`, `~/.ssh/authorized_keys`, `~/.ssh/id_ed25519.pub`, `~/.sygnif/{bybit,dashboard,waiaas}-mcp.env` | warn |

### Limitations

- Without root or membership in `adm` / `systemd-journal` group, the user-scope `journalctl` does not see the full `/var/log/auth.log`. For complete sshd telemetry, either grant the user journal access or run `intrusion-watch` as root via a separate system-scope service.
- File integrity covers only the listed paths; consider extending if new sensitive files appear.
- No notification channel beyond swarm.db today. Telegram/email forwarding would require wiring `SYGNIF_HEDGE_BOT_TOKEN` (already in env) into a small dispatcher reading `security.alert` rows.

### Audit-time findings (2026-04-29)

- ✅ ufw active; SSH restricted to `192.168.1.0/24` LAN + `100.64.0.0/10` Tailscale; auto unattended-upgrades on
- ✅ All env files in `~/.sygnif/` are mode `600`; `~/.ssh/id_ed25519` mode `600`
- ✅ MCP services (`9001`, `9002`, `8088`) bound to Tailscale interface only
- ✅ Ollama (`11434`), Yoga MCP (`9003`), Bee API (`1633`) bound to localhost only
- ⚠ Bee libp2p `:1634` listens on `0.0.0.0` (intentional — peer discovery; ufw allows)
- ⚠ Dashboard v2 `:8090` LAN-bound (`0.0.0.0`) since the Tailscale outage. Restore to Tailscale-only when convenient.
- ⚠ systemd unit hardening absent on `sygnif-bybit-mcp`, `sygnif-x1-mcp`, `sygnif-dashboard`, `sygnif-trader` — no `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`. Add for defense in depth.
- 🐛 Crash-looping services (every ~3 s): `sygnif-channeler.service` (exit 203/EXEC, binary missing), `sygnif-llamacpp-tunnel.service` (exit 255, dead pod target). Pollutes journal. Either disable or fix the missing target.
- ⚠ Stale outbound SYN attempts to old EC2 IP `54.93.232.126` from X1 — something still references the pre-EIP address. Worth grepping configs.
- ✅ `100.68.115.95` root pts/0 session — identified as `tailscale-ssh-console-giansn-github` (legitimate, operator-initiated)
