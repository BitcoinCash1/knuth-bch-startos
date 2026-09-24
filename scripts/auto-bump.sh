#!/usr/bin/env bash
# Bump the package to a new upstream Knuth release and open a pull request.
#
#   scripts/auto-bump.sh <upstream-tag>      e.g. scripts/auto-bump.sh v1.4.0
#
# Sets `ARG KNUTH_VERSION` in the Dockerfile (without the leading "v", which is
# what conan expects), sets startos/versions/current.ts to `<upstream>:0`,
# resets ALLOW_DOWNGRADE to false, then commits on `auto-bump/<tag>` and opens a
# PR against master. Merging the PR is what releases it.
#
# DRY_RUN=1 edits and commits locally but skips the push and the PR.
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "Usage: $0 <upstream-tag>" >&2
  exit 1
fi
CURRENT_FILE=startos/versions/current.ts
UPSTREAM="${TAG#v}"
if ! echo "$UPSTREAM" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "Tag $TAG is not a plain x.y.z release — not bumping" >&2
  exit 1
fi

CURRENT_VERSION=$(sed -nE "s/^[[:space:]]*version:[[:space:]]*'([^']+)'.*/\1/p" "$CURRENT_FILE" | head -1)
CURRENT_UPSTREAM="${CURRENT_VERSION%%:*}"
if [ "$CURRENT_UPSTREAM" = "$UPSTREAM" ]; then
  echo "Already at $UPSTREAM — no bump needed"
  exit 0
fi
# Never move the version downwards: StartOS cannot migrate to a lower upstream.
HIGHEST=$(printf '%s\n%s\n' "$CURRENT_UPSTREAM" "$UPSTREAM" | sort -V | tail -1)
if [ "$HIGHEST" = "$CURRENT_UPSTREAM" ]; then
  echo "::warning::Tag $TAG is older than the packaged version $CURRENT_UPSTREAM — refusing to downgrade"
  exit 0
fi

NEW_VERSION="${UPSTREAM}:0"
echo "Bumping $CURRENT_VERSION -> $NEW_VERSION ($TAG)"

python3 - "$CURRENT_FILE" "$TAG" "$UPSTREAM" "$NEW_VERSION" <<'PY'
import re, sys
current, tag, upstream, new_version = sys.argv[1:]
d = open('Dockerfile').read()
d, a = re.subn(r"(?m)^ARG KNUTH_VERSION=.*$", f"ARG KNUTH_VERSION={upstream}", d, count=1)
assert a == 1, 'ARG KNUTH_VERSION not found'
open('Dockerfile', 'w').write(d)
src = open(current).read()
src, n = re.subn(r"(\n\s*version:\s*)'[^']+'", rf"\g<1>'{new_version}'", src, count=1)
assert n == 1, 'version line not found'
# Release notes are rewritten for review in the PR; translations are added there.
src, n = re.subn(
    r"releaseNotes:\s*(\{.*?\n  \}|'[^']*'|`[^`]*`),",
    "releaseNotes: {\n    en_US: 'Updates Knuth to upstream " + tag + ".',\n  },",
    src, count=1, flags=re.S)
assert n == 1, 'releaseNotes not found'
src = re.sub(r"const ALLOW_DOWNGRADE = (true|false)", "const ALLOW_DOWNGRADE = false", src)
open(current, 'w').write(src)
PY

BRANCH="auto-bump/${TAG}"
git checkout -b "$BRANCH"
git add Dockerfile "$CURRENT_FILE"
git -c user.name="github-actions[bot]" \
    -c user.email="github-actions[bot]@users.noreply.github.com" \
    commit -m "feat: bump Knuth to upstream ${TAG} (${NEW_VERSION})"

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN: committed on $BRANCH, not pushed"
  exit 0
fi

git push origin "$BRANCH"
gh pr create --base master --head "$BRANCH" \
  --title "Bump Knuth to upstream ${TAG} (${NEW_VERSION})" \
  --body "Automated bump to upstream Knuth ${TAG}. Review the release notes (add translations) before merging; merging releases ${NEW_VERSION}."
