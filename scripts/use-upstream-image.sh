#!/usr/bin/env bash
# Switch the package to the official ghcr.io/k-nuth/kth image as soon as that
# image is published with the JSON-RPC server compiled in, then push to master.
#
#   scripts/use-upstream-image.sh
#
# Does nothing while the Dockerfile already wraps the official image, while the
# image for the pinned KNUTH_VERSION is not published, or while it lacks RPC.
# Writes switched=true to $GITHUB_OUTPUT when it pushed the switch.
#
# DRY_RUN=1 edits and commits locally but skips the push.
set -euo pipefail

if grep -q '^FROM .*ghcr.io/k-nuth/kth:' Dockerfile; then
  echo "Already wrapping the official image"
  exit 0
fi

VERSION=$(sed -nE 's/^ARG KNUTH_VERSION=v?(.*)$/\1/p' Dockerfile | head -1)
IMAGE="ghcr.io/k-nuth/kth:${VERSION}"

docker pull --platform linux/amd64 "$IMAGE" >/dev/null 2>&1 || true
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "$IMAGE is not published — keeping the source build"
  exit 0
fi

# Only an RPC build contains the server's listening message (src/node/src/rpc/
# server.cpp is compiled only with KTH_WITH_RPC).
WORK=$(mktemp -d)
CID=$(docker create --platform linux/amd64 "$IMAGE")
docker cp "$CID:/usr/local/bin/kth" "$WORK/kth" >/dev/null
docker rm "$CID" >/dev/null
if ! grep -aq 'JSON-RPC server listening' "$WORK/kth"; then
  echo "$IMAGE has no JSON-RPC server yet — keeping the source build"
  rm -rf "$WORK"
  exit 0
fi
rm -rf "$WORK"

echo "$IMAGE ships JSON-RPC — switching to the official image"
sed "s|^ARG KNUTH_VERSION=.*|ARG KNUTH_VERSION=${VERSION}|" scripts/Dockerfile.upstream > Dockerfile

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add Dockerfile
git commit -m "feat: use the official Knuth image now that it ships JSON-RPC (${VERSION})"

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN: committed, not pushed"
  exit 0
fi

git push origin master
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "switched=true" >> "$GITHUB_OUTPUT"; fi
