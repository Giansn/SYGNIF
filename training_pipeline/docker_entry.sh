#!/bin/sh
set -e
INTERVAL="${TRAINING_LOOP_SECONDS:-21600}"
echo "[entry] sygnif-training-pipeline loop every ${INTERVAL}s"
while true; do
  python3 /app/training_pipeline/channel_training.py
  sleep "$INTERVAL"
done
