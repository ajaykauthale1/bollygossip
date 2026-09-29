#!/usr/bin/env bash
# Cron entry point (settings come from config.json). Example crontab line, 16:00 UTC daily:
#   0 16 * * * /path/to/viralbolly-pipeline/run_daily.sh >> /path/to/viralbolly-pipeline/output/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate
python3 pipeline.py "$@"   # settings come from config.json
