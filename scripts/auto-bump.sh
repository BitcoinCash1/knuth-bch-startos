#!/usr/bin/env bash
set -euo pipefail

DISPATCHED_TAG="${1:-}"
if [ -z "$DISPATCHED_TAG" ]; then
  echo "Usage: $0 <tag>" >&2
  exit 1
fi

# Knuth tags as v0.83.0 — strip leading v for Start9 version
CLEAN_TAG="${DISPATCHED_TAG#v}"

CURRENT_FILE=startos/versions/current.ts
CURRENT_VERSION=$(grep -E "version:[[:space:]]*'" "$CURRENT_FILE" \
  | head -1 | sed -E "s/.*version:[[:space:]]*'([^']+)'.*/\1/")
CURRENT_UPSTREAM="${CURRENT_VERSION%%:*}"

if [ "$CURRENT_UPSTREAM" = "$CLEAN_TAG" ]; then
  echo "Already at $CLEAN_TAG — no bump needed"
  exit 0
fi
echo "Bumping $CURRENT_UPSTREAM -> $CLEAN_TAG"

NEW_VERSION="${CLEAN_TAG}:0"

# current.ts is edited in place: new version, fresh release notes, and
# ALLOW_DOWNGRADE back to false for the new upstream.
python3 - "$CURRENT_FILE" "$NEW_VERSION" "$DISPATCHED_TAG" <<'PY'
import re, sys
path, new_version, tag = sys.argv[1:]
src = open(path).read()
src, n = re.subn(r"(\n\s*version:\s*)'[^']+'", rf"\g<1>'{new_version}'", src, count=1)
assert n == 1, 'version line not found'
src, n = re.subn(r"releaseNotes:\s*(\{.*?\n  \}|(?:'[^']*'(?:\s*\+\s*'[^']*')*)|`[^`]*`),",
                 f"releaseNotes: 'Upstream {tag}.',", src, count=1, flags=re.S)
assert n == 1, 'releaseNotes not found'
src = re.sub(r"const ALLOW_DOWNGRADE = (true|false)", "const ALLOW_DOWNGRADE = false", src)
open(path, 'w').write(src)
PY

# The Dockerfile wants the version without the leading v (conan: 1.3.0).
sed -i "s|^ARG KNUTH_VERSION=.*|ARG KNUTH_VERSION=${CLEAN_TAG}|" Dockerfile

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add "$CURRENT_FILE" Dockerfile
git commit -m "feat: auto-bump to upstream ${DISPATCHED_TAG} (v${NEW_VERSION})"
git push origin master
echo "Version bump committed"
