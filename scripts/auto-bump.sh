#!/usr/bin/env bash
set -euo pipefail

DISPATCHED_TAG="${1:-}"
if [ -z "$DISPATCHED_TAG" ]; then
  echo "Usage: $0 <tag>" >&2
  exit 1
fi

CURRENT_FILE=startos/versions/current.ts
NEW_VERSION=$(python3 scripts/update_version.py "$DISPATCHED_TAG")
if [ -z "$NEW_VERSION" ]; then
  echo "Already at ${DISPATCHED_TAG#v} — no bump needed"
  exit 0
fi

git config user.name "CyberAshven"
git config user.email "130158492+CyberAshven@users.noreply.github.com"
git add "$CURRENT_FILE" Dockerfile
git commit -m "feat: auto-bump to upstream ${DISPATCHED_TAG} (v${NEW_VERSION})"
git push origin master
echo "Version bump committed"
