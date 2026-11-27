#!/usr/bin/env python3
"""End-to-end: templates from a node that follows blocks arriving seconds apart.

BCHN A mines on regtest; the follower takes A's blocks over P2P and serves
BCH P2Pool's templates. The follower is kth (TB_FOLLOWER=kth) or a second BCHN
(TB_FOLLOWER=bchn), set up the same way: A is its only block source, and
P2Pool takes its templates with getblocktemplate (template_source = "rpc"),
polling every 10 s. With TB_NODE_P2P=1 P2Pool also connects to the
follower's P2P port (node_p2p_address) and fetches on each block it
announces, as the chipnet pool does with the Start9's Knuth. No ZMQ
publisher is one the pool listens to, so the two followers wake P2Pool alike.

Phases:
- quiet: a block every 20 s, the baseline;
- burst: A mines in bursts of 1 to 5 blocks, 0.5 to 4 s apart, for
  TB_BURST_SECONDS, so the follower holds headers whose blocks it has not
  connected yet, as when chipnet blocks came seconds apart;
- settle: no blocks.

Through all three, a probe asks the follower for getblocktemplate every
PROBE_INTERVAL seconds and reads its block and header heights before and after.
The row reports what P2Pool saw (its fetches, failures and withdrawals, from
its own log) and what the probe saw (answers, refusals, the longest time
without a template, and whether any template was built on a block the
follower had not connected).

Through the bursts A's wallet also sends one ordinary 1 sat/byte payment a
second. A mines nothing below 2 sat/byte (-blockmintxfee), so they stay in
its mempool, and once the follower has caught up the row counts the ones A
holds and the follower's mempool and template do not: a peer announces a
transaction once, so one the follower ignored is missed for good.

Then the transaction phase: A's wallet sends 40 more payments, and the
follower's template must carry every one of them A's mempool holds. On kth
this is done twice: with node.sigop_fee_satoshis at kth's BCH default (0),
and on a fresh kth with 100, where each payment's two P2PKH outputs cost 200
satoshis more than it pays, so kth refuses them and its template stays empty.

Standard library only. Writes <out>/template-burst.json.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BCHN = ("127.0.0.1", 18443)
BCHN_P2P = 18444
BCHN_B = ("127.0.0.1", 18453)
BCHN_B_P2P = 18454
KTH = ("127.0.0.1", 19332)
KTH_P2P = 18555
P2POOL_SV1 = 3333
P2POOL_SV2 = 3336
API_PORT = 46884
# Nothing publishes here: both followers wake P2Pool through their P2P port.
ZMQ_UNUSED = "tcp://127.0.0.1:28399"
LIBP2P = "/ip4/127.0.0.1/tcp/6884"
AUTH = "e2e"
RPC_BASIC = base64.b64encode(b"e2e:e2e").decode()
CONTAINER = "kth-template-burst"

# key-utils test vector, p2poolv2_config/src/lib.rs sv2_authority_tests.
SV2_PUBLIC = "9bDuixKmZqAJnrmP746n8zU1wyAQRrus7th9dxnkPg6RzQvCnan"
SV2_SECRET = "zmBEmPhqo3A92FkiLVvyCz6htc3e53ph3ZbD4ASqGaLjwnFLi"

BCHN_DIR = Path("/tmp/bchn-tb-a")
BCHN_B_DIR = Path("/tmp/bchn-tb-b")
DATA = Path("/tmp/kth-tb-data")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out").resolve()

FOLLOWER = os.environ.get("TB_FOLLOWER", "kth")
# TB_NODE_P2P=1: P2Pool also connects to the follower's P2P port
# (node_p2p_address) and fetches on its block announcements, as the chipnet
# pool does. 0: P2Pool polls alone, and the follower's only peer is A.
NODE_P2P = os.environ.get("TB_NODE_P2P", "1") == "1"
# TB_RESTARTS=N: after the bursts, P2Pool is killed (SIGKILL) and started again
# N times, each right after A mines five blocks, while A goes on mining a
# block a second. P2Pool's startup asks getblockchaininfo for
# initialblockdownload and, while it is true, waits 300 s before it asks again
# (preflight.rs, wait_for_bitcoin_node_synced).
RESTARTS = int(os.environ.get("TB_RESTARTS") or "0")
RESTART_WAIT = 330
QUIET_SECONDS = int(os.environ.get("TB_QUIET_SECONDS") or "60")
BURST_SECONDS = int(os.environ.get("TB_BURST_SECONDS") or "300")
SETTLE_SECONDS = int(os.environ.get("TB_SETTLE_SECONDS") or "30")
PROBE_INTERVAL = 0.5
# (blocks, then seconds to the next burst), in turn: 17 blocks in 16 s a cycle.
BURSTS = [(1, 2.0), (1, 1.0), (2, 3.0), (1, 0.5), (3, 2.0), (1, 1.5), (5, 4.0), (1, 1.0), (2, 1.0)]
PAYMENTS = 40
# One ordinary payment from A's wallet every STREAM_INTERVAL seconds through
# the bursts. A mines no transaction below 2 sat/byte (-blockmintxfee), so the
# 1 sat/byte payments stay in its mempool, and what the follower does not hold
# at the end is what it missed.
STREAM_INTERVAL = 1.0
BLOCK_MIN_FEE_BCH_PER_KB = "0.00002"
# BCHN's relay floor and kth's byte fee: 1 satoshi per byte.
FEE_RATE_BCH_PER_KB = "0.00001"

# P2Pool's own lines (e3cf4c88): each template fetch starts with the first,
# and a fetch that failed after its retries ends with one of the others.
P2POOL_FETCH = "Requesting getblocktemplate with params"
P2POOL_FAILED = ("Error polling block template", "Error getting block template after")
P2POOL_WITHDRAWN = "Withdrawing mining work"
P2POOL_BACK = "The node gives templates again"
STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z")

EVIDENCE: dict = {
    "ok": False,
    "error": "started",
    "follower": FOLLOWER,
    "delay_ms": os.environ.get("TB_DELAY_MS") or "0",
    "node_p2p": NODE_P2P,
    "phases": {},
    "probe": {},
    "p2pool": {},
    "transactions": {},
    "heights": {},
}
_next_id = 0
_id_lock = threading.Lock()
P2POOL = None
HANDLES: list = []
PROBES: list[dict] = []
STOP_PROBE = threading.Event()
STREAM: list[dict] = []
STREAM_ERRORS: list[str] = []
STOP_STREAM = threading.Event()


def fail(message: str) -> NoReturn:
    EVIDENCE["error"] = message
    raise SystemExit(message)


def progress(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# -----------------------------------------------------------------------------
# RPC
# -----------------------------------------------------------------------------

def post(node, body: bytes, timeout: float = 60) -> dict:
    request = Request(
        f"http://{node[0]}:{node[1]}/",
        body,
        {"Authorization": f"Basic {RPC_BASIC}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read() or b""
    except (URLError, OSError) as error:
        return {"status": None, "transport": f"{type(error).__name__}: {error}", "raw": b"", "json": None}
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return {"status": status, "raw": raw, "json": parsed}


def call(node, method: str, *params, timeout: float = 60):
    global _next_id
    with _id_lock:
        _next_id += 1
        ident = _next_id
    reply = post(node, json.dumps({"id": ident, "method": method, "params": list(params)}).encode(), timeout)
    parsed = reply["json"]
    if isinstance(parsed, dict):
        return parsed.get("result"), parsed.get("error")
    raw = reply.get("raw") or b""
    return None, {
        "transport": reply.get("transport"),
        "status": reply.get("status"),
        "raw": raw[:200].decode("utf-8", "replace"),
    }


def must(node, method: str, *params, timeout: float = 60):
    result, error = call(node, method, *params, timeout=timeout)
    if error is not None:
        fail(f"{method} on {node[1]} failed: {error}")
    return result


def rpc_wait(node, name: str, seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        result, error = call(node, "getblockchaininfo")
        if error is None and result:
            return
        last = error
        time.sleep(1)
    fail(f"{name} never answered getblockchaininfo within {seconds}s: {last}")


def follower() -> tuple[str, int]:
    return KTH if FOLLOWER == "kth" else BCHN_B


def follower_p2p() -> int:
    return KTH_P2P if FOLLOWER == "kth" else BCHN_B_P2P


def bitcoin_cli(datadir: Path, port: int, args: list[str], timeout: float = 120) -> str:
    try:
        result = subprocess.run(
            ["bitcoin-cli", "-regtest", f"-datadir={datadir}", f"-rpcuser={AUTH}",
             f"-rpcpassword={AUTH}", f"-rpcport={port}", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(f"bitcoin-cli {' '.join(args)} timed out after {timeout}s")
    if result.returncode != 0:
        fail(f"bitcoin-cli {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def cli(*args: str, timeout: float = 120) -> str:
    return bitcoin_cli(BCHN_DIR, BCHN[1], list(args), timeout)


# -----------------------------------------------------------------------------
# Processes
# -----------------------------------------------------------------------------

def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.splitlines()[-lines:])


def kth_log_text() -> str:
    return "\n".join(read_text(path) for path in sorted(DATA.glob("kth-*.log")))


# kth's own lines (CyberAshven/kth fix/sync-*), as the debug log writes them:
# a peer is "<address> <user agent>". A is the outbound peer on BCHN_P2P;
# P2Pool's listener (e3cf4c88, node_p2p_listener.rs) advertises no services
# and calls itself /P2Poolv2:<version>/.
KTH_CLAIM = re.compile(r"\[block_download\] Peer (\S+)(.*?) claiming chunk \d+ \(blocks (\d+)-(\d+)\)")
KTH_STIMULUS = re.compile(r"\[sync_coordinator\] Header stimulus \d+ \(.*?\) to (\S+)(.*?) from height")
KTH_DISCONNECTED = re.compile(r"\[peer_provider\] Peer disconnected: (\S+)(.*?) \(nonce=")
KTH_NEW_PEER = re.compile(r"\[peer_provider\] New peer: (\S+)(.*?) \(nonce=")
KTH_CLOSED = re.compile(r"\[block_download\] Peer (\S+)(.*?)(?:sent no block|did not finish|sent a block).*closing the session")


def summarize_kth_peers(label: str) -> dict:
    """What kth's debug log says about whom it asked for what: chunk claims
    and header requests by peer, peers that came and went, and sessions the
    block download closed. "a" is BCHN A; "no_services" is a peer whose user
    agent is P2Pool's, which advertises no services."""
    text = read_text(DATA / f"kth-{label}-debug.log")
    a_suffix = f":{BCHN_P2P}"

    def who(address: str, rest: str) -> str:
        if address.endswith(a_suffix):
            return "a"
        if "P2Poolv2" in rest:
            return "no_services"
        return "other"

    record: dict = {"log_bytes": len(text)}
    for name, pattern in (("chunk_claims", KTH_CLAIM), ("header_requests", KTH_STIMULUS),
                          ("peers_connected", KTH_NEW_PEER), ("peers_disconnected", KTH_DISCONNECTED),
                          ("sessions_closed_by_download", KTH_CLOSED)):
        counts = {"a": 0, "no_services": 0, "other": 0}
        for match in pattern.finditer(text):
            counts[who(match.group(1), match.group(2))] += 1
        record[name] = counts
    record["no_body_range_in_flight"] = text.count("the previous range is still in flight")
    record["excluded_serving_no_blocks"] = text.count("from block download: it serves no blocks")
    record["recent_blocks_only"] = text.count("serves only recent blocks: chunks from height")
    return record


def p2pool_log_text() -> str:
    parts = [read_text(OUT / "p2pool-console.log")]
    if OUT.is_dir():
        for path in sorted(OUT.glob("p2pool-file.log*")):
            parts.append(read_text(path))
    return "\n".join(parts)


def spawn(cmd: list[str], log_path: Path, env: dict | None = None) -> subprocess.Popen:
    handle = open(log_path, "w", encoding="utf-8")
    HANDLES.append(handle)
    return subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env=env)


def stop_process(proc, seconds: int = 15) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)


def container_running() -> bool:
    result = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                            capture_output=True, text=True, timeout=15)
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_follower() -> None:
    if FOLLOWER == "kth" and not container_running():
        fail("kth exited. log tail:\n" + tail(kth_log_text(), 40))


def ensure_p2pool() -> None:
    if P2POOL is not None and P2POOL.poll() is not None:
        fail(f"P2Pool exited {P2POOL.returncode}. log tail:\n" + tail(p2pool_log_text(), 40))


def start_bchn() -> None:
    if shutil.which("bitcoind") is None:
        fail("missing bitcoind on PATH")
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
         f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}", f"-rpcport={BCHN[1]}", f"-port={BCHN_P2P}",
         "-listen=1", "-bind=127.0.0.1", "-txindex=1", "-fallbackfee=0.00001",
         f"-paytxfee={FEE_RATE_BCH_PER_KB}", f"-blockmintxfee={BLOCK_MIN_FEE_BCH_PER_KB}",
         "-allowunconnectedmining"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        fail(f"bitcoind A failed to start: {(result.stderr or result.stdout).strip()}")
    rpc_wait(BCHN, "BCHN A")


def start_bchn_b() -> None:
    BCHN_B_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["bitcoind", "-regtest", f"-datadir={BCHN_B_DIR}", "-daemon", "-server",
         f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}", f"-rpcport={BCHN_B[1]}", f"-port={BCHN_B_P2P}",
         "-listen=1", "-bind=127.0.0.1", "-dnsseed=0", "-disablewallet=1",
         f"-connect=127.0.0.1:{BCHN_P2P}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        fail(f"bitcoind B failed to start: {(result.stderr or result.stdout).strip()}")
    rpc_wait(BCHN_B, "BCHN B")


def start_kth(label: str, sigop_fee: str | None) -> None:
    """A fresh kth with A as its only outbound peer, inbound open for P2Pool's
    P2P listener. `sigop_fee` None leaves node.sigop_fee_satoshis at kth's
    default. Each start has its own database and logs under `label`: the
    container writes them as root, so they are not removed between starts."""
    image = os.environ["KTH_IMAGE"]
    src = Path(os.environ["KTH_SRC"]).resolve()
    binary = Path(os.environ["KTH_BIN"]).resolve()
    DATA.mkdir(parents=True, exist_ok=True)
    relative = binary.relative_to(src)
    argv = [
        "--init_run", "--network", "regtest", "--node.display", "daemon",
        "--db.directory", f"/data/db-{label}", "--db.db_mode", "full",
        "--net.inbound_port", str(KTH_P2P), "--net.inbound_connections", "8",
        "--net.outbound_connections", "1", "--net.peer", f"127.0.0.1:{BCHN_P2P}",
        "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", str(KTH[1]),
        "--rpc.user", AUTH, "--rpc.password", AUTH,
        "--log.debug_file", f"/data/kth-{label}-debug.log",
        "--log.error_file", f"/data/kth-{label}-error.log",
    ]
    if sigop_fee is not None:
        argv += ["--node.sigop_fee_satoshis", sigop_fee]
    # TB_KTH_ARGS: more kth options, as a package passes them (the chipnet
    # backend package writes node.minimum_output_satoshis = 0).
    argv += os.environ.get("TB_KTH_ARGS", "").split()
    EVIDENCE.setdefault("kth_argv", []).append(argv)
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=30)
    result = subprocess.run(
        ["docker", "run", "--detach", "--name", CONTAINER, "--network", "host",
         "--platform", "linux/amd64", "-v", f"{src}:/src", "-v", f"{DATA}:/data",
         "-w", "/src/" + relative.parent.as_posix(), "--entrypoint", "./" + relative.name,
         image, *argv],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        fail(f"docker run of kth failed: {(result.stderr or result.stdout).strip()}")
    rpc_wait(KTH, "kth")


def stop_kth() -> None:
    subprocess.run(["docker", "stop", "-t", "20", CONTAINER], capture_output=True, timeout=45)
    logs = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, timeout=30)
    (OUT / f"kth-docker.{int(time.time())}.log").write_bytes(logs.stdout + logs.stderr)
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=30)


def wait_follower_height(height: int, seconds: int = 300) -> float:
    started = time.monotonic()
    deadline = started + seconds
    last = None
    while time.monotonic() < deadline:
        ensure_follower()
        result, error = call(follower(), "getblockcount")
        if error is None and result is not None:
            last = int(result)
            if last >= height:
                return round(time.monotonic() - started, 1)
        time.sleep(0.5)
    fail(f"the follower did not reach height {height} within {seconds}s (at {last})")


def toml_string(value: str) -> str:
    if '"' in value or "\n" in value:
        fail(f"refusing to write this value into p2pool.toml: {value!r}")
    return f'"{value}"'


def write_p2pool_config(payout: str) -> Path:
    store = OUT / "p2pool-store"
    stats = OUT / "p2pool-stats"
    stats.mkdir(parents=True, exist_ok=True)
    node = follower()
    config = OUT / "p2pool.toml"
    address = toml_string(f"127.0.0.1:{follower_p2p()}")
    node_p2p = f"node_p2p_address = {address}\n" if NODE_P2P else ""
    config.write_text(f"""# Regtest config for the template burst check.
[network]
listen_address = {toml_string(LIBP2P)}
max_pending_incoming = 10
max_pending_outgoing = 10
max_established_incoming = 50
max_established_outgoing = 50
max_established_per_peer = 2
max_workbase_per_second = 10
max_userworkbase_per_second = 10
max_miningshare_per_second = 100
max_inventory_per_second = 100
max_transaction_per_second = 100
max_requests_per_second = 100
dial_timeout_secs = 30
dns_seed = false

[store]
path = {toml_string(str(store))}
pplns_ttl_days = 7

[stratum]
hostname = "127.0.0.1"
port = {P2POOL_SV1}
start_difficulty = 1
minimum_difficulty = 1
maximum_difficulty = 1
bootstrap_address = {toml_string(payout)}
zmqpubhashblock = {toml_string(ZMQ_UNUSED)}
{node_p2p}chain = "bitcoin-cash"
bitcoin_cash_network = "regtest"
sv2_port = {P2POOL_SV2}
sv2_authority_public_key = {toml_string(SV2_PUBLIC)}
sv2_authority_secret_key = {toml_string(SV2_SECRET)}
template_source = "rpc"
network = "regtest"
version_mask = "1fffffe0"
pool_signature = "P2Poolv2"
wait_for_chain_sync = false

[bitcoinrpc]
url = "http://{node[0]}:{node[1]}"
username = {toml_string(AUTH)}
password = {toml_string(AUTH)}

[logging]
file = {toml_string(str(OUT / "p2pool-file.log"))}
console = true
level = "info,bitcoindrpc=debug,p2poolv2_lib::stratum::work=debug"
stats_dir = {toml_string(str(stats))}

[api]
hostname = "127.0.0.1"
port = {API_PORT}
""", encoding="utf-8")
    return config


def http_get(path: str, timeout: float = 15) -> tuple[int, str]:
    try:
        with urlopen(Request(f"http://127.0.0.1:{API_PORT}{path}"), timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        return error.code, (error.read() or b"").decode("utf-8", "replace")
    except (URLError, OSError, TimeoutError) as error:
        return 0, f"{type(error).__name__}: {error}"


def start_p2pool(binary: str, config: Path) -> None:
    global P2POOL
    env = os.environ.copy()
    env.pop("RUST_LOG", None)
    P2POOL = spawn([binary, "--config", str(config)], OUT / "p2pool-console.log", env)
    deadline = time.monotonic() + 120
    last = ""
    while time.monotonic() < deadline:
        ensure_p2pool()
        status, body = http_get("/health")
        if status == 200 and body.strip() == "OK":
            progress("P2Pool /health is OK")
            return
        last = f"health={status} {body.strip()[:200]!r}"
        time.sleep(1)
    fail(f"P2Pool /health was not OK within 120s ({last}). log tail:\n" + tail(p2pool_log_text(), 40))


# -----------------------------------------------------------------------------
# The probe
# -----------------------------------------------------------------------------

def heights(node) -> tuple[int | None, int | None, bool | None]:
    result, error = call(node, "getblockchaininfo", timeout=10)
    if error is not None or not isinstance(result, dict):
        return None, None, None
    return result.get("blocks"), result.get("headers"), result.get("initialblockdownload")


def probe_loop() -> None:
    node = follower()
    while not STOP_PROBE.is_set():
        started = time.time()
        blocks_before, headers_before, ibd_before = heights(node)
        result, error = call(node, "getblocktemplate", timeout=30)
        answered = time.time()
        blocks_after, headers_after, _ = heights(node)
        entry = {
            "t": round(started, 3),
            "answered": round(answered, 3),
            "blocks_before": blocks_before,
            "headers_before": headers_before,
            "blocks_after": blocks_after,
            "headers_after": headers_after,
            "ibd": ibd_before,
        }
        if error is None and isinstance(result, dict):
            entry["ok"] = True
            entry["height"] = result.get("height")
            entry["previous"] = result.get("previousblockhash")
        else:
            entry["ok"] = False
            entry["error"] = (error or {}).get("message") if isinstance(error, dict) else str(error)
            if not entry["error"]:
                entry["error"] = json.dumps(error)[:200]
        PROBES.append(entry)
        STOP_PROBE.wait(max(0.0, PROBE_INTERVAL - (time.time() - started)))


def longest_gap(times: list[float], start: float, end: float) -> float:
    """The longest stretch in [start, end] with no answer in `times`."""
    points = [start] + sorted(t for t in times if start <= t <= end) + [end]
    return round(max(b - a for a, b in zip(points, points[1:])), 3)


def summarize_probe(window: tuple[float, float], a_hashes: dict[int, str]) -> dict:
    start, end = window
    inside = [p for p in PROBES if start <= p["t"] <= end]
    served = [p for p in inside if p["ok"]]
    refused = [p for p in inside if not p["ok"]]
    reasons: dict[str, int] = {}
    for p in refused:
        reasons[p["error"]] = reasons.get(p["error"], 0) + 1
    ahead = [p for p in inside if p["blocks_before"] is not None and p["headers_before"] is not None
             and p["headers_before"] > p["blocks_before"]]
    served_ahead = [p for p in served if p in ahead]
    # Built on a block the follower had not connected: a parent above the
    # height it reported once the answer was in, or a parent that is not A's
    # block at that height (no reorganization happens in this row).
    unconnected = []
    for p in served:
        parent = (p.get("height") or 0) - 1
        if p["blocks_after"] is not None and parent > p["blocks_after"]:
            unconnected.append(p)
        elif parent in a_hashes and a_hashes[parent] != p.get("previous"):
            unconnected.append(p)
    gaps = [p["headers_before"] - p["blocks_before"] for p in ahead]
    new_tip = [p for previous, p in zip(inside, inside[1:])
               if p["blocks_before"] is not None and previous["blocks_before"] is not None
               and p["blocks_before"] > previous["blocks_before"]]
    streak = 0.0
    run_start = None
    for p in inside:
        if p["ok"]:
            run_start = None
            continue
        if run_start is None:
            run_start = p["t"]
        streak = max(streak, p["answered"] - run_start)
    # The longest stretch the follower held headers past its blocks: from the
    # first probe that read them ahead to the first that read them level (or
    # the end of the window, for a follower that never caught up again).
    ahead_longest = 0.0
    ahead_start = None
    for p in inside:
        if p["blocks_before"] is None or p["headers_before"] is None:
            continue
        if p["headers_before"] > p["blocks_before"]:
            if ahead_start is None:
                ahead_start = p["t"]
            continue
        if ahead_start is not None:
            ahead_longest = max(ahead_longest, p["t"] - ahead_start)
            ahead_start = None
    if ahead_start is not None:
        ahead_longest = max(ahead_longest, end - ahead_start)
    return {
        "requests": len(inside),
        "served": len(served),
        "refused": len(refused),
        "refusal_reasons": reasons,
        "longest_without_template_s": longest_gap([p["answered"] for p in served], start, end),
        "longest_refusal_run_s": round(streak, 3),
        "requests_with_headers_ahead": len(ahead),
        "served_with_headers_ahead": len(served_ahead),
        "max_headers_ahead": max(gaps) if gaps else 0,
        "longest_headers_ahead_s": round(ahead_longest, 3),
        "templates_on_unconnected_blocks": len(unconnected),
        "unconnected_examples": unconnected[:5],
        # What a client reading getblockchaininfo saw: initialblockdownload
        # true, at any sample and at the samples that see a new tip (P2Pool
        # reads it on each new tip, payout_tree_executor.rs).
        "ibd_true": sum(1 for p in inside if p.get("ibd")),
        "new_tip_samples": len(new_tip),
        "ibd_true_at_new_tip": sum(1 for p in new_tip if p.get("ibd")),
    }


# -----------------------------------------------------------------------------
# What P2Pool saw
# -----------------------------------------------------------------------------

def stamp_of(line: str) -> float | None:
    match = STAMP.match(line)
    if not match:
        return None
    text = match.group(1)
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in text else "%Y-%m-%dT%H:%M:%S"
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()


def summarize_p2pool(window: tuple[float, float]) -> dict:
    """Fetches from P2Pool's console log: a fetch is a success unless a failure
    line follows it before the next fetch starts (the template task fetches one
    at a time)."""
    start, end = window
    events = []
    # The file log: the console one carries ANSI colours around the stamps.
    text = "\n".join(read_text(path) for path in sorted(OUT.glob("p2pool-file.log*")))
    for line in text.splitlines():
        at = stamp_of(line)
        if at is None:
            continue
        if P2POOL_FETCH in line:
            events.append(("fetch", at, line))
        elif any(marker in line for marker in P2POOL_FAILED):
            events.append(("failed", at, line))
        elif P2POOL_WITHDRAWN in line:
            events.append(("withdrawn", at, line))
        elif P2POOL_BACK in line:
            events.append(("back", at, line))
    fetches: list[dict] = []
    for kind, at, line in events:
        if kind == "fetch":
            fetches.append({"t": at, "ok": True})
        elif kind == "failed" and fetches:
            fetches[-1]["ok"] = False
            fetches[-1]["error"] = line.split(": ", 1)[-1][:200]
    inside = [f for f in fetches if start <= f["t"] <= end]
    failed = [f for f in inside if not f["ok"]]
    reasons: dict[str, int] = {}
    for f in failed:
        reasons[f.get("error", "")] = reasons.get(f.get("error", ""), 0) + 1
    withdrawn = [line[:300] for kind, at, line in events if kind == "withdrawn" and start <= at <= end]
    return {
        "fetches": len(inside),
        "templates": len(inside) - len(failed),
        "failed_fetches": len(failed),
        "failure_reasons": reasons,
        "longest_without_template_s": longest_gap([f["t"] for f in inside if f["ok"]], start, end),
        "work_withdrawn": len(withdrawn),
        "withdrawn_lines": withdrawn[:10],
    }


# -----------------------------------------------------------------------------
# Phases
# -----------------------------------------------------------------------------

def mine(count: int, address: str) -> None:
    must(BCHN, "generatetoaddress", count, address, timeout=120)


def quiet(seconds: int, address: str) -> int:
    blocks = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ensure_follower()
        ensure_p2pool()
        mine(1, address)
        blocks += 1
        time.sleep(min(20.0, max(0.0, deadline - time.monotonic())))
    return blocks


def stream_loop(addresses: list[str]) -> None:
    sent = 0
    while not STOP_STREAM.is_set():
        started = time.time()
        txid, error = call(BCHN, "sendtoaddress", addresses[sent % len(addresses)], 0.001)
        if error is None and isinstance(txid, str):
            STREAM.append({"txid": txid, "t": round(started, 3)})
        else:
            STREAM_ERRORS.append(str(error)[:200])
        sent += 1
        STOP_STREAM.wait(max(0.0, STREAM_INTERVAL - (time.time() - started)))


def restart_rounds(count: int, address: str, p2pool_bin: str, config: Path) -> None:
    """Kill P2Pool and start it again, `count` times, each right after a
    five-block burst, and record how long each start took to its first
    template fetch and whether it waited for the node to sync."""
    global P2POOL
    rounds = []
    for number in range(1, count + 1):
        P2POOL.kill()
        P2POOL.wait(15)
        mine(5, address)
        started = time.time()
        env = os.environ.copy()
        env.pop("RUST_LOG", None)
        P2POOL = spawn([p2pool_bin, "--config", str(config)], OUT / f"p2pool-console-restart{number}.log", env)
        fetched = None
        waits = 0
        deadline = started + RESTART_WAIT
        while time.time() < deadline:
            ensure_follower()
            if P2POOL.poll() is not None:
                break
            mine(1, address)
            text = "\n".join(read_text(path) for path in sorted(OUT.glob("p2pool-file.log*")))
            waits = 0
            for line in text.splitlines():
                at = stamp_of(line)
                if at is None or at < started:
                    continue
                if "Bitcoin node still syncing" in line:
                    waits += 1
                elif P2POOL_FETCH in line and fetched is None:
                    fetched = at
            if fetched is not None:
                break
            time.sleep(1)
        record = {
            "round": number,
            "waited_for_sync": waits,
            "first_template_after_s": None if fetched is None else round(fetched - started, 1),
            "p2pool_exited": P2POOL.poll(),
        }
        rounds.append(record)
        progress(f"restart {number}: first template after {record['first_template_after_s']} s, "
                 f"waited for sync {waits} time(s)")
    EVIDENCE["p2pool"]["restarts"] = rounds


def compare_stream() -> dict:
    """The stream's payments that A holds and the follower does not. A peer
    announces a transaction once, so one the follower ignored stays missed;
    the follower is given until its count stops moving for 30 s (at most
    120 s) to take what is still on its way."""
    node = follower()
    stream = {entry["txid"] for entry in STREAM}
    a_pool = set(must(BCHN, "getrawmempool")) & stream
    deadline = time.monotonic() + 120
    held = -1
    since = time.monotonic()
    f_pool: set[str] = set()
    while time.monotonic() < deadline:
        ensure_follower()
        result, _ = call(node, "getrawmempool")
        f_pool = set(result) if isinstance(result, list) else set()
        have = len(a_pool & f_pool)
        if have == len(a_pool):
            break
        if have != held:
            held, since = have, time.monotonic()
        elif time.monotonic() - since >= 30:
            break
        time.sleep(2)
    template, error = template_txids(node)
    missed = sorted(a_pool - f_pool)
    record = {
        "sent": len(STREAM),
        "send_errors": len(STREAM_ERRORS),
        "send_error_examples": STREAM_ERRORS[:3],
        "in_bchn_a_mempool": len(a_pool),
        "in_follower_mempool": len(a_pool & f_pool),
        "in_follower_template": len(a_pool & set(template)),
        "missed": len(missed),
        "missed_examples": missed[:5],
        "follower_template_error": error,
    }
    EVIDENCE["transactions"]["stream"] = record
    progress(f"stream: {record['sent']} sent, A holds {record['in_bchn_a_mempool']}, "
             f"the follower's mempool {record['in_follower_mempool']}, its template "
             f"{record['in_follower_template']}, missed {record['missed']}")
    return record


def burst(seconds: int, address: str) -> dict:
    blocks = 0
    rounds = 0
    addresses = [cli("getnewaddress") for _ in range(20)]
    STOP_STREAM.clear()
    streamer = threading.Thread(target=stream_loop, args=(addresses,), daemon=True)
    streamer.start()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        count, pause = BURSTS[rounds % len(BURSTS)]
        ensure_follower()
        ensure_p2pool()
        mine(count, address)
        blocks += count
        rounds += 1
        time.sleep(pause)
    STOP_STREAM.set()
    streamer.join(30)
    return {"blocks": blocks, "rounds": rounds, "payments": len(STREAM)}


def template_txids(node) -> tuple[list[str], dict | None]:
    result, error = call(node, "getblocktemplate", timeout=60)
    if error is not None or not isinstance(result, dict):
        return [], error
    return sorted(tx.get("txid") or tx.get("hash") for tx in result.get("transactions", [])), None


def pay(count: int, label: str) -> list[str]:
    txids = []
    for _ in range(count):
        address = cli("getnewaddress")
        txids.append(cli("sendtoaddress", address, "0.01"))
    sizes = []
    for txid in txids[:5]:
        entry = json.loads(cli("getmempoolentry", txid))
        fee = entry.get("fees", {}).get("base", entry.get("fee"))
        sizes.append({"txid": txid, "size": entry.get("size"), "fee_bch": fee})
    EVIDENCE["transactions"][f"{label}_sample"] = sizes
    return txids


def compare_templates(label: str, txids: list[str], wait_seconds: int, expect_all: bool) -> dict:
    """Wait for the follower's template to hold `txids` (or for `wait_seconds`
    to pass when they are expected to stay out), then compare it with A's."""
    node = follower()
    wanted = set(txids)
    deadline = time.monotonic() + wait_seconds
    held: list[str] = []
    error = None
    while time.monotonic() < deadline:
        ensure_follower()
        held, error = template_txids(node)
        if expect_all and wanted <= set(held):
            break
        time.sleep(2)
    # A mines nothing below 2 sat/byte, so its mempool, not its template, is
    # what BCHN holds of these payments.
    a_pool = set(must(BCHN, "getrawmempool"))
    mempool, _ = call(node, "getrawmempool")
    f_pool = set(mempool) if isinstance(mempool, list) else set()
    record = {
        "sent": len(txids),
        "in_bchn_a_mempool": len(wanted & a_pool),
        "in_follower_mempool": len(wanted & f_pool),
        "in_follower_template": len(wanted & set(held)),
        "follower_template_error": error,
        "same_transactions": wanted & a_pool == wanted & set(held),
        "only_in_bchn_a": sorted((wanted & a_pool) - set(held))[:10],
    }
    EVIDENCE["transactions"][label] = record
    progress(f"{label}: A's mempool holds {record['in_bchn_a_mempool']}/{len(txids)}, "
             f"the follower's template {record['in_follower_template']}/{len(txids)}")
    return record


def test_accept(label: str, txids: list[str]) -> None:
    """kth's own verdict on each payment, through testmempoolaccept."""
    reasons: dict[str, int] = {}
    for txid in txids:
        raw = cli("getrawtransaction", txid)
        result, error = call(KTH, "testmempoolaccept", [raw])
        if error is not None or not isinstance(result, list) or not result:
            key = f"error: {error}"
        else:
            verdict = result[0]
            key = "allowed" if verdict.get("allowed") else str(verdict.get("reject-reason"))
        reasons[key] = reasons.get(key, 0) + 1
    EVIDENCE["transactions"][f"{label}_testmempoolaccept"] = reasons
    progress(f"{label}: kth testmempoolaccept {reasons}")


def run() -> None:
    if FOLLOWER not in ("kth", "bchn"):
        fail(f"TB_FOLLOWER must be kth or bchn, got {FOLLOWER!r}")
    p2pool_bin = os.environ["P2POOL_BIN"]
    progress("starting BCHN A")
    start_bchn()
    try:
        address = cli("getnewaddress")
    except SystemExit:
        cli("createwallet", "e2e")
        address = cli("getnewaddress")
    payout = cli("getnewaddress")
    cli("settxfee", FEE_RATE_BCH_PER_KB)
    mine(150, address)
    EVIDENCE["heights"]["funded"] = int(cli("getblockcount"))

    if FOLLOWER == "kth":
        progress("starting kth (node.sigop_fee_satoshis at its default)")
        # TB_KTH_SIGOP_FEE: the sigop fee the package writes (0), named
        # explicitly; unset leaves kth's default.
        start_kth("default", os.environ.get("TB_KTH_SIGOP_FEE") or None)
    else:
        progress("starting BCHN B")
        start_bchn_b()
    EVIDENCE["heights"]["follower_synced_after_s"] = wait_follower_height(EVIDENCE["heights"]["funded"])
    info = must(follower(), "getnetworkinfo")
    EVIDENCE["follower_subversion"] = info.get("subversion") if isinstance(info, dict) else None

    progress("starting P2Pool")
    start_p2pool(p2pool_bin, write_p2pool_config(payout))
    time.sleep(5)

    threading.Thread(target=probe_loop, daemon=True).start()
    t0 = time.time()
    progress(f"quiet phase, {QUIET_SECONDS}s")
    quiet_blocks = quiet(QUIET_SECONDS, address)
    t1 = time.time()
    progress(f"burst phase, {BURST_SECONDS}s")
    bursts = burst(BURST_SECONDS, address)
    t2 = time.time()
    progress(f"settle phase, {SETTLE_SECONDS}s")
    time.sleep(SETTLE_SECONDS)
    t3 = time.time()
    STOP_PROBE.set()
    time.sleep(1)
    top = int(cli("getblockcount"))
    EVIDENCE["heights"]["after_burst"] = top
    caught_up = None
    try:
        caught_up = wait_follower_height(top, 120)
    except SystemExit as error:
        EVIDENCE["heights"]["follower_not_caught_up"] = str(error)
    EVIDENCE["heights"]["follower_caught_up_after_s"] = caught_up
    # Where each one stands once the follower was given its two minutes.
    EVIDENCE["heights"]["bchn_final"] = int(cli("getblockcount"))
    final, _ = call(follower(), "getblockchaininfo", timeout=10)
    if isinstance(final, dict):
        EVIDENCE["heights"]["follower_final_blocks"] = final.get("blocks")
        EVIDENCE["heights"]["follower_final_headers"] = final.get("headers")
    EVIDENCE["phases"] = {
        "quiet": {"start": t0, "end": t1, "blocks": quiet_blocks},
        "burst": {"start": t1, "end": t2, **bursts},
        "settle": {"start": t2, "end": t3},
    }
    a_hashes = {}
    first = EVIDENCE["heights"]["funded"]
    for height in range(first, top + 1):
        a_hashes[height] = must(BCHN, "getblockhash", height)
    EVIDENCE["probe"] = {
        "quiet": summarize_probe((t0, t1), a_hashes),
        "burst_and_settle": summarize_probe((t1, t3), a_hashes),
    }
    EVIDENCE["p2pool"] = {
        "quiet": summarize_p2pool((t0, t1)),
        "burst_and_settle": summarize_p2pool((t1, t3)),
    }
    (OUT / "probe.json").write_text(json.dumps(PROBES) + "\n", encoding="utf-8")
    burst_probe = EVIDENCE["probe"]["burst_and_settle"]
    burst_pool = EVIDENCE["p2pool"]["burst_and_settle"]
    progress(f"probe: {burst_probe['served']} served, {burst_probe['refused']} refused, "
             f"longest without a template {burst_probe['longest_without_template_s']}s, "
             f"max headers ahead {burst_probe['max_headers_ahead']}, "
             f"longest with headers ahead {burst_probe['longest_headers_ahead_s']}s, "
             f"served with headers ahead {burst_probe['served_with_headers_ahead']}, "
             f"on unconnected blocks {burst_probe['templates_on_unconnected_blocks']}")
    progress(f"P2Pool: {burst_pool['fetches']} fetches, {burst_pool['failed_fetches']} failed, "
             f"longest without a template {burst_pool['longest_without_template_s']}s, "
             f"work withdrawn {burst_pool['work_withdrawn']} times")
    if RESTARTS > 0 and caught_up is not None:
        restart_rounds(RESTARTS, address, p2pool_bin, OUT / "p2pool.toml")
    stop_process(P2POOL)
    if caught_up is None:
        fail("the follower stopped following A: " + EVIDENCE["heights"]["follower_not_caught_up"])
    compare_stream()

    progress("transactions at 1 sat/byte, the follower as configured")
    first_payments = pay(PAYMENTS, "default")
    compare_templates("default", first_payments, 180, expect_all=True)
    if FOLLOWER == "kth":
        stop_kth()
        progress("a fresh kth with node.sigop_fee_satoshis = 100")
        start_kth("sigop100", "100")
        wait_follower_height(int(cli("getblockcount")))
        # A's inv to an inbound peer waits about 5 s on average; a minute is
        # several rounds of it.
        second_payments = pay(PAYMENTS, "sigop_fee_100")
        compare_templates("sigop_fee_100", second_payments, 60, expect_all=False)
        test_accept("sigop_fee_100", second_payments[:5])

    EVIDENCE["ok"] = True
    EVIDENCE["error"] = None


def cleanup() -> None:
    STOP_PROBE.set()
    errors = []
    for label, action in (
        ("p2pool", lambda: stop_process(P2POOL)),
        ("kth", lambda: stop_kth() if FOLLOWER == "kth" and shutil.which("docker") else None),
        ("kth logs", lambda: [shutil.copy(path, OUT / path.name) for path in DATA.glob("kth-*.log")]),
        # The run the bursts went through, read once kth has stopped and
        # written all of it.
        ("kth peers", lambda: EVIDENCE.__setitem__("kth_peers", summarize_kth_peers("default"))
            if FOLLOWER == "kth" else None),
        ("bitcoind", lambda: [subprocess.run(
            ["bitcoin-cli", "-regtest", f"-datadir={d}", f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}",
             f"-rpcport={p}", "stop"], capture_output=True, timeout=30)
            for d, p in ((BCHN_DIR, BCHN[1]), (BCHN_B_DIR, BCHN_B[1]))]),
        ("handles", lambda: [h.close() for h in HANDLES]),
    ):
        try:
            action()
        except Exception as error:
            errors.append(f"{label}: {type(error).__name__}: {error}")
    for name, src in (("bchn-a-debug.log", BCHN_DIR / "regtest" / "debug.log"),
                      ("bchn-b-debug.log", BCHN_B_DIR / "regtest" / "debug.log")):
        if src.is_file():
            (OUT / name).write_text(read_text(src)[-400_000:], encoding="utf-8")
    if errors:
        EVIDENCE["cleanup_error"] = "; ".join(errors)


def write_evidence() -> None:
    (OUT / "template-burst.json").write_text(json.dumps(EVIDENCE, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_evidence()
    try:
        run()
    except SystemExit as error:
        EVIDENCE["ok"] = False
        if str(error):
            EVIDENCE["error"] = str(error)
        raise
    except Exception as error:
        EVIDENCE["ok"] = False
        EVIDENCE["error"] = f"{type(error).__name__}: {error}"
        raise SystemExit(EVIDENCE["error"]) from error
    finally:
        try:
            cleanup()
        except Exception as error:
            EVIDENCE["cleanup_error"] = f"{type(error).__name__}: {error}"
        write_evidence()
        print(f"evidence: {OUT / 'template-burst.json'}", flush=True)


if __name__ == "__main__":
    main()
