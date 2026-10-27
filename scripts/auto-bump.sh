#!/usr/bin/env bash
set -euo pipefail

DISPATCHED_TAG="${1:-}"
if [ -z "$DISPATCHED_TAG" ]; then
  echo "Usage: $0 <tag>" >&2
  exit 1
fi

CURRENT_FILE=startos/versions/current.ts
NEW_VERSION=$(python3 scripts/update_version.py --check "$DISPATCHED_TAG")
if [ -z "$NEW_VERSION" ]; then
  echo "Already at ${DISPATCHED_TAG#v} or newer — no bump needed"
  exit 0
fi

# A release tag can appear before its binary/image is usable. Validate before
# either package pin is edited or committed; failed checks retry next schedule.
bash scripts/check-upstream-image.sh "$DISPATCHED_TAG"
python3 scripts/update_version.py "$DISPATCHED_TAG" >/dev/null

git config user.name "CyberAshven"
git config user.email "130158492+CyberAshven@users.noreply.github.com"
git add "$CURRENT_FILE" Dockerfile
git commit -m "feat: auto-bump to upstream ${DISPATCHED_TAG} (v${NEW_VERSION})"
git push origin master
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "updated=true" >> "$GITHUB_OUTPUT"; fi
echo "Version bump committed"
