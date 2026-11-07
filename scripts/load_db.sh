#!/usr/bin/env bash
# Load a scenario directory (must live under ./data) into the compose `db` service.
#   scripts/load_db.sh data/scenario_42
set -euo pipefail

dir="${1:?usage: scripts/load_db.sh data/<scenario_dir>}"
dir="${dir%/}"
dir="${dir#./}"
cd "$(dirname "$0")/.."
case "$dir" in
  data/*) ;;
  *) echo "scenario dir must be under ./data (that is what the container mounts)" >&2; exit 1 ;;
esac
[ -f "$dir/hubs.csv" ] || { echo "no hubs.csv in $dir; run the simulator first" >&2; exit 1; }

# Git Bash on Windows rewrites /data/... arguments into Windows paths without this.
export MSYS_NO_PATHCONV=1

docker compose exec -T db psql -U piggyback -d piggyback -v ON_ERROR_STOP=1 \
  -v dir="/data/${dir#data/}" -f /db/load.sql
