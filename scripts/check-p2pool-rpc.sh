#!/usr/bin/env bash
# Mine through the RPC proxy against a real regtest kth in the package image,
# as a BCH P2Pool node does: templates, proposals, submitblock, a relayed
# transaction with its fee and SigChecks, raw headers and addresses
# (scripts/e2e_p2pool_regtest.py). The proxy and the check come from this
# checkout, so a change to rpc_compat.py is tested against the pinned kth.
set -euo pipefail

VERSION=${1:-}
VERSION=${VERSION#v}
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]]; then
  echo "Expected an upstream release version" >&2
  exit 1
fi
# check-upstream-image.sh leaves this image when it builds the package recipe;
# otherwise build it here (the official image has no python3 or proxy).
IMAGE="local/knuth-upstream-check:$VERSION"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build --platform linux/amd64 --build-arg "KNUTH_VERSION=$VERSION" --tag "$IMAGE" .
fi

docker run --rm --network none --platform linux/amd64 \
  -v "$PWD/scripts:/checks:ro" --entrypoint /bin/bash "$IMAGE" -c '
set -euo pipefail
mkdir -p /data/smoke
/usr/local/bin/kth --init_run --network regtest --node.display daemon \
  --db.directory /data/smoke --net.outbound_connections 0 --net.inbound_connections 0 \
  --rpc.enabled true --rpc.bind 127.0.0.1 --rpc.port 19332 \
  --rpc.user smoke --rpc.password smoke > /tmp/kth.log 2>&1 &
for attempt in $(seq 1 60); do
  [ -d /data/smoke/blocks ] && break
  sleep 1
done
python3 /checks/rpc_compat.py --bind 127.0.0.1 --port 18443 \
  --backend http://127.0.0.1:19332/ --blocks /data/smoke/blocks \
  --config /dev/null --store /data/smoke/store.json --user smoke --password smoke \
  --binary /usr/local/bin/kth --debug-log /tmp/rpc-compat-debug.log > /tmp/proxy.log 2>&1 &
if ! python3 /checks/e2e_p2pool_regtest.py http://127.0.0.1:18443/; then
  echo "--- kth log" >&2; tail -40 /tmp/kth.log >&2 || true
  echo "--- proxy log" >&2; tail -40 /tmp/proxy.log >&2 || true
  echo "--- proxy debug log" >&2; tail -40 /tmp/rpc-compat-debug.log >&2 || true
  exit 1
fi'
