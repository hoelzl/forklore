#!/usr/bin/env bash
# Export an incremental git bundle of everything on main since the last
# GitHub sync, then move the sync tag. Usage:
#
#   scripts/export_update.sh [output.bundle]
#
# The recipient applies it with:
#
#   git pull <output.bundle> main && git push origin main
#
# (A bundle works as a read-only remote, so no credentials are needed.)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$(dirname "$REPO")/forklore-update.bundle}"

cd "$REPO"
N=$(git rev-list --count main --not synced-github)
if [ "$N" -eq 0 ]; then
  echo "nothing new since synced-github; no bundle written"
  exit 0
fi
git bundle create "$OUT" main --not synced-github
git tag -f synced-github main
echo "wrote $OUT ($N new commit(s) since last sync)"
