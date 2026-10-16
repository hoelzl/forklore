#!/usr/bin/env bash
# Sync the repo into the Kimi preview layout at /mnt/agents/output/app:
# web source -> app/ (build there), backend packages -> app/backend/.
# app/ is preview packaging only; forklore/ is the source of truth.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=/mnt/agents/output/app

for f in src index.html package.json package-lock.json vite.config.ts \
         tailwind.config.js postcss.config.js tsconfig.json \
         tsconfig.app.json tsconfig.node.json components.json eslint.config.js; do
  rm -rf "$APP/$f"
  cp -r "$REPO/web/$f" "$APP/$f"
done

cd "$APP"
npm install
npm run build

for pkg in core server strategies; do
  rm -rf "backend/$pkg"
  cp -r "$REPO/$pkg" "backend/$pkg"
  find "backend/$pkg" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
done

echo "preview layout synced; save a version with website_version_manager (dynamic, $APP)"
