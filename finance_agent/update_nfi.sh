#!/bin/bash
# Pull latest NostalgiaForInfinity and re-index with GitNexus
cd /home/ubuntu/NostalgiaForInfinity
git pull --ff-only 2>&1
npx gitnexus analyze --skills 2>&1 | tail -5
echo "NFI updated: $(date)"
