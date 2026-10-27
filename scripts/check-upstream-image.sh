#!/usr/bin/env bash
# Validate the released binary and real JSON-RPC before changing package pins.
set -euo pipefail

VERSION=${1:-}
VERSION=${VERSION#v}
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]]; then
  echo "Expected an upstream release version" >&2
  exit 1
fi
IMAGE="ghcr.io/k-nuth/kth:$VERSION"
if ! docker pull --platform linux/amd64 "$IMAGE"; then
  # GHCR availability need not block packaging. The package Dockerfile carries
  # the pinned upstream build recipe; it builds the same release from source.
  IMAGE="local/knuth-upstream-check:$VERSION"
  echo "Official image unavailable; checking the pinned upstream build recipe"
  docker build --platform linux/amd64 --build-arg "KNUTH_VERSION=$VERSION" --tag "$IMAGE" .
fi

ACTUAL=$(docker run --rm --network none --platform linux/amd64 \
  --entrypoint /usr/local/bin/kth "$IMAGE" --version)
printf '%s\n' "$ACTUAL"
if ! grep -Eq "Knuth v${VERSION//./\\.}([[:space:]-]|$)" <<< "$ACTUAL"; then
  echo "The upstream binary does not match $VERSION" >&2
  exit 1
fi

CID=''
cleanup() {
  if [ -n "$CID" ]; then docker rm -fv "$CID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
# This disposable regtest instance has no network interface. Probe its loopback
# listener through bash's /dev/tcp so no utilities need adding to the image.
CID=$(docker run --detach --network none --platform linux/amd64 \
  --entrypoint /usr/local/bin/kth "$IMAGE" \
  --init_run --network regtest --node.display daemon \
  --db.directory /data/smoke --net.outbound_connections 0 --net.inbound_connections 0 \
  --rpc.enabled true --rpc.bind 127.0.0.1 --rpc.port 19332 \
  --rpc.user smoke --rpc.password smoke)
for attempt in $(seq 1 45); do
  if RESPONSE=$(timeout 3s docker exec "$CID" /bin/bash -c '
    body='\''{"jsonrpc":"2.0","id":1,"method":"getblockchaininfo","params":[]}'\''
    exec 3<>/dev/tcp/127.0.0.1/19332
    printf "POST / HTTP/1.1\r\nHost: localhost\r\nAuthorization: Basic c21va2U6c21va2U=\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: %s\r\n\r\n%s" "${#body}" "$body" >&3
    cat <&3
  ' 2>/dev/null); then
    if printf '%s' "$RESPONSE" | python3 -c '
import json, sys
raw=sys.stdin.read()
head, body=raw.split("\n\n", 1) if "\n\n" in raw else raw.split("\r\n\r\n", 1)
value=json.loads(body)
result=value.get("result") or {}
assert head.splitlines()[0].split()[1] == "200"
assert value.get("error") is None
assert result.get("chain", "").lower() == "regtest"
assert isinstance(result.get("blocks"), int)
' 2>/dev/null; then
      echo "$IMAGE passed the version and loopback JSON-RPC checks"
      exit 0
    fi
  fi
  if [ "$(docker inspect --format '{{.State.Running}}' "$CID")" != true ]; then
    echo "The upstream node exited before JSON-RPC became ready" >&2
    docker logs --tail 30 "$CID" >&2
    exit 1
  fi
  sleep 1
done
echo "The upstream JSON-RPC readiness check timed out" >&2
docker logs --tail 30 "$CID" >&2
exit 1
