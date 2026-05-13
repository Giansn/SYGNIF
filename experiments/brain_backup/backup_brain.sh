#!/bin/bash
# One-shot brain backup: EC2 → local X1, with manifest + insights snapshot.
set -euo pipefail
TS="$(date -u +%Y%m%d_%H%M%SZ)"
DEST="C:/Users/giank/Backups/sygnif-brain/${TS}"
mkdir -p "$DEST"

echo "============================================================"
echo "  SYGNIF brain backup → ${DEST}"
echo "============================================================"

# ─────────────────────────────────────────────────────────────────────
# [1] capture brain_insights JSON snapshots (the "insight" side)
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "[1] capturing brain_insights JSON snapshots"
for endpoint in api/state api/stats; do
    out_file="${DEST}/insights_$(echo $endpoint | tr / _).json"
    ssh ec2-eu1 "curl -s http://127.0.0.1:8890/${endpoint}" > "${out_file}"
    sz=$(wc -c < "${out_file}")
    printf "  + insights_%s : %s bytes\n" "$(echo $endpoint | tr / _)" "${sz}"
done

# ─────────────────────────────────────────────────────────────────────
# [2] pull live.json + meta.json (small, fast)
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "[2] pulling live.json + meta.json"
for f in live.json meta.json; do
    scp -q "ec2-eu1:/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/${f}" "${DEST}/${f}"
    sz=$(wc -c < "${DEST}/${f}")
    printf "  + %s : %s bytes\n" "${f}" "${sz}"
done

# ─────────────────────────────────────────────────────────────────────
# [3] pull regions/ + synapses/ (small, ~840 KB total)
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "[3] pulling regions/ + synapses/"
ssh ec2-eu1 'cd /home/ubuntu/SYGNIF/third_party/neurolinked/brain_state \
    && tar czf - regions synapses' \
    | tar -xzf - -C "${DEST}/"
echo "  + $(find ${DEST}/regions -type f 2>/dev/null | wc -l) region files"
echo "  + $(find ${DEST}/synapses -type f 2>/dev/null | wc -l) synapse files"

# ─────────────────────────────────────────────────────────────────────
# [4] pull knowledge.db (the big one — 186 MB)
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "[4] pulling knowledge.db (186 MB) — gzip-compressed transfer"
ssh ec2-eu1 'cd /home/ubuntu/SYGNIF/third_party/neurolinked/brain_state \
    && gzip -c knowledge.db' > "${DEST}/knowledge.db.gz"
sz=$(stat -c%s "${DEST}/knowledge.db.gz" 2>/dev/null || stat -f%z "${DEST}/knowledge.db.gz")
printf "  + knowledge.db.gz : %d MB (compressed)\n" "$((sz / 1048576))"

# ─────────────────────────────────────────────────────────────────────
# [5] build manifest
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "[5] building MANIFEST.json"
python <<PY
import json, pathlib, hashlib, time

dest = pathlib.Path(r"${DEST}".replace("/", "\\\\"))
manifest = {
    "schema": "sygnif.brain_backup.v1",
    "captured_at_utc": "${TS}",
    "captured_at_ts":  int(time.time()),
    "source_host":     "ec2-eu1",
    "source_path":     "/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/",
    "files":           {},
}
# live.json + meta.json + insights → embed inline for self-contained manifest
for inline in ("live.json", "meta.json", "insights_api_state.json", "insights_api_stats.json"):
    p = dest / inline
    if p.exists():
        try:
            manifest[inline.replace(".json","")] = json.loads(p.read_text())
        except Exception:
            manifest[inline.replace(".json","")] = {"_raw": p.read_text()[:200]}

# checksums for the binaries we pulled
for f in sorted(dest.rglob("*")):
    if not f.is_file(): continue
    rel = str(f.relative_to(dest)).replace("\\\\", "/")
    h = hashlib.sha256(f.read_bytes()).hexdigest() if f.stat().st_size < 50_000_000 else None
    manifest["files"][rel] = {
        "size": f.stat().st_size,
        "sha256": h,
        "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(f.stat().st_mtime)),
    }

(dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str))
print(f"  + MANIFEST.json with {len(manifest['files'])} file entries")

# Headline numbers
live = manifest.get("live", {})
print(f"\n  Brain state captured:")
print(f"    step_count:        {live.get('step_count',0):,}")
print(f"    development_stage: {live.get('development_stage','?')}")
print(f"    total_neurons:     {live.get('total_neurons','?')}")
print(f"    total_synapses:    {live.get('total_synapses','?')}")
print(f"    uptime hours:      {live.get('uptime',0)/3600:.1f}")
print(f"    steps/sec:         {live.get('steps_per_second','?')}")
nm = live.get("neuromodulators", {}) or {}
print(f"    neuromodulators:   dopamine={nm.get('dopamine','?')} serotonin={nm.get('serotonin','?')}")
PY

# ─────────────────────────────────────────────────────────────────────
# [6] write restore README
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "[6] writing RESTORE.md"
cat > "${DEST}/RESTORE.md" <<EOF
# SYGNIF brain backup — captured ${TS}

## How to restore

\`\`\`bash
TARGET=/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state

# 0. stop the brain
sudo systemctl stop sygnif-neurolinked

# 1. archive the current state (just in case)
sudo mv \$TARGET \${TARGET}.replaced-\$(date +%Y%m%d_%H%M%S)
sudo mkdir -p \$TARGET

# 2. copy the backup files
scp -r ${DEST}/regions ec2-eu1:/tmp/brain_restore/
scp -r ${DEST}/synapses ec2-eu1:/tmp/brain_restore/
scp ${DEST}/live.json ${DEST}/meta.json ec2-eu1:/tmp/brain_restore/
scp ${DEST}/knowledge.db.gz ec2-eu1:/tmp/brain_restore/
ssh ec2-eu1 "cd /tmp/brain_restore && gunzip knowledge.db.gz && sudo cp -r * \$TARGET/ && sudo chown -R ubuntu:ubuntu \$TARGET"

# 3. restart the brain
sudo systemctl start sygnif-neurolinked

# 4. verify
ssh ec2-eu1 "curl -s http://127.0.0.1:8889/api/state | python3 -m json.tool | head -20"
\`\`\`

## What's in this backup

| Component | Purpose |
|---|---|
| live.json | 1Hz heartbeat (step_count, neuromodulators) |
| meta.json | Last full-persistence checkpoint metadata |
| regions/*.json | Per-region neuron arrays (v, u, binding_strength) |
| synapses/*.npz | STDP-evolved weight matrices (between regions) |
| knowledge.db.gz | model2vec-encoded text memories (gzipped 186 MB → ~80 MB) |
| insights_api_state.json | brain_insights /api/state snapshot at backup time |
| insights_api_stats.json | brain_insights /api/stats snapshot at backup time |
| MANIFEST.json | sha256 checksums + sizes + inline live/meta/insights |

## NOT in this backup (intentionally excluded)

- backups/ on EC2 (~1.8 GB of stacked 7-min rolling snapshots — redundant)
- swarm.db (runtime event store — separate concern)
EOF

# ─────────────────────────────────────────────────────────────────────
# [7] summary
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  BACKUP COMPLETE"
echo "============================================================"
echo ""
echo "  destination: ${DEST}"
du -sh "${DEST}"
echo ""
echo "  files:"
find "${DEST}" -type f -printf "    %12s  %P\n" 2>/dev/null \
    || find "${DEST}" -type f -exec ls -la {} \; | awk '{printf "    %12s  %s\n", $5, $NF}'
