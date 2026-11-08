#!/usr/bin/env python3
"""End-to-end: kth's SV2 Job Declaration client against BCH P2Pool on BCHN regtest.

Row 25 (KTH_JD_ROW=25, branch test/sv2-jd-client) has no Requested Payout
Outputs extension. P2Pool refuses the plain coinbase, kth falls back to solo,
and the device's blocks pay sv2.mining.address.

Row 25b (KTH_JD_ROW=25b, branch test/sv2-jd-payout-outputs) negotiates
extension 0x5052. P2Pool accepts the custom jobs, credits a share, and a
block on BCHN pays the PPLNS split.

Row 25c (KTH_JD_ROW=25c, branch test/sv2-jd-large-templates) is row 25b with
BCH large templates (0x4C54) and a mempool that standard Job Declaration
cannot carry: more than 65,535 transactions and more than one U24 frame of
transaction data. The PPLNS block must hold that whole mempool.

Row tdp (KTH_JD_ROW=tdp, branch test/tdp-lt-jd) has no Job Declaration.
kth runs only its Template Provider, and P2Pool takes its templates from it
(template_source = "sv2", over Noise pinned to the authority key kth prints).
SRI's mining_device mines on P2Pool's own SV2 port. kth's mempool is row
25c's, beyond what standard Template Distribution carries, so P2Pool must be
granted BCH large templates (0x4C54) and take kth's push. A block found on
that template goes back to kth with SubmitSolution, and BCHN must accept it
with more than 65,535 transactions and more than 16 MiB. A kth without
Template Distribution paging answers RequestTransactionData with
template-too-large, and the row fails when no large block comes within the
budget. The row records each block's PPLNS verdict but does not require one.

How a PPLNS block is proven (P2Pool BCH v2, read before changing a row):
- P2Pool BCH takes the donation and fee per share ("cuts-per-share" in its
  protocol string). Each share in the PPLNS window gives its own cut off its
  own weight, and the donation address is paid as an ordinary payee. There is
  never a separate donation output.
- The node logs its default at start: "Donating N basis points of each block
  this node finds to ADDRESS." A miner's login may choose another with
  donation=<percent>; this harness sends none, so the node default applies.
- A block found while the window holds only the share chain's genesis pays
  the genesis payee (an unspendable NUMS key) alone, with no donation: genesis
  has no cut. That block is recorded and never counts as the proof.
- The proof is a block whose outputs equal P2Pool's own /metrics distribution
  exactly, that pays the miner's payout address, and whose donation payee is
  more than zero and at most N basis points of the block.

Standard library only. Writes <out>/jd-p2pool-e2e.json.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Ports follow scripts/knuth_rpc_e2e.py where that file already chose them.
BCHN = ("127.0.0.1", 18443)
BCHN_P2P = 18444
BCHN_B = ("127.0.0.1", 18453)
BCHN_B_P2P = 18454
KTH = ("127.0.0.1", 19332)
KTH_P2P = 18555
SV2_PORT = 18442
P2POOL_SV1 = 3333
P2POOL_SV2 = 3336
API_PORT = 46884
ZMQ = "tcp://127.0.0.1:28332"
LIBP2P = "/ip4/127.0.0.1/tcp/6884"
AUTH = "e2e"
RPC_BASIC = base64.b64encode(b"e2e:e2e").decode()
CONTAINER = "kth-jd"

# key-utils test vector, p2poolv2_config/src/lib.rs sv2_authority_tests.
# The node never generates this pair.
SV2_PUBLIC = "9bDuixKmZqAJnrmP746n8zU1wyAQRrus7th9dxnkPg6RzQvCnan"
SV2_SECRET = "zmBEmPhqo3A92FkiLVvyCz6htc3e53ph3ZbD4ASqGaLjwnFLi"
# bitcoin_cash_donation_cashaddr(Regtest).
DONATION = "bchreg:qze95tc2dqrnltvxfa5yhunwx5952f34fvsm6us7sv"

# P2Pool HASHES_PER_DIFFICULTY, and kth share_interval_seconds.
DIFF1_HASHES = 2 ** 32
SHARE_INTERVAL = 20
FLOOR_MARGIN = 1.1
MAX_MARGIN = 2.0
MINE_BUDGET = 1200
# P(no credited share) is about e^-CREDIT_TAIL, 0.25% at 6.
CREDIT_TAIL = 6
EASY_BLOCK_LIMIT = 8

BCHN_DIR = Path("/tmp/bchn-jd-a")
BCHN_B_DIR = Path("/tmp/bchn-jd-b")
DATA = Path("/tmp/kth-jd-data")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out").resolve()

EVIDENCE: dict = {
    "ok": False,
    "error": "started",
    "row": os.environ.get("KTH_JD_ROW"),
    "addresses": {},
    "settings": {},
    "kth_argv": [],
    "heights": {},
    "blocks": [],
    "p2pool": {
        "shares_accepted_total": 0,
        "accepted_work": 0,
        "metrics_snapshots": [],
    },
    "kth_log_lines": [],
    "p2pool_log_lines": [],
    "assertions": {},
    "hashrate": {},
}
_next_id = 0
P2POOL = None
DEVICE = None
HANDLES: list = []
SEEN_SNAPSHOTS: set = set()


def fail(message: str) -> NoReturn:
    EVIDENCE["ok"] = False
    EVIDENCE["error"] = message
    raise SystemExit(message)


def progress(message: str) -> None:
    print(message, flush=True)


def redact(text: str) -> str:
    return text.replace(SV2_SECRET, "[redacted]")


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
    _next_id += 1
    reply = post(node, json.dumps({"id": _next_id, "method": method, "params": list(params)}).encode(), timeout)
    parsed = reply["json"]
    if isinstance(parsed, dict):
        return parsed.get("result"), parsed.get("error")
    raw = reply.get("raw") or b""
    return None, {
        "transport": reply.get("transport"),
        "status": reply.get("status"),
        "raw": raw[:200].decode("utf-8", "replace"),
    }


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


def bitcoin_cli(datadir: Path, port: int, args: list[str], check: bool = True,
                timeout: float | None = 120) -> subprocess.CompletedProcess:
    if shutil.which("bitcoin-cli") is None:
        fail("missing bitcoin-cli on PATH (the workflow fetches BCHN 29.2.0)")
    try:
        result = subprocess.run(
            ["bitcoin-cli", "-regtest", f"-datadir={datadir}", f"-rpcuser={AUTH}",
             f"-rpcpassword={AUTH}", f"-rpcport={port}", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(f"bitcoin-cli {' '.join(args)} timed out after {timeout}s")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(f"bitcoin-cli {' '.join(args)} failed: {detail}")
    return result


def cli(*args: str) -> str:
    return bitcoin_cli(BCHN_DIR, BCHN[1], list(args)).stdout.strip()


def bchn_hash(height: int):
    result = bitcoin_cli(BCHN_DIR, BCHN[1], ["getblockhash", str(height)], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def http_get(path: str, timeout: float = 15) -> tuple[int, str]:
    request = Request(f"http://127.0.0.1:{API_PORT}{path}")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        return error.code, (error.read() or b"").decode("utf-8", "replace")
    except (URLError, OSError, TimeoutError) as error:
        return 0, f"{type(error).__name__}: {error}"


def http_json(path: str):
    status, body = http_get(path)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body[:4000]


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"missing environment variable {name}")
    return value


def require_file(path: str, what: str) -> str:
    if not path or not Path(path).is_file():
        fail(f"missing {what}: {path or '(empty)'}")
    return str(Path(path).resolve())


def find_kth(src: Path) -> str:
    given = os.environ.get("KTH_BIN", "").strip()
    if given:
        return require_file(given, "kth node-exe binary (KTH_BIN)")
    build = src / "build"
    if build.is_dir():
        for path in build.rglob("kth"):
            if path.is_file() and os.access(path, os.X_OK) and "node-exe" in path.parts:
                return str(path.resolve())
    fail(f"kth node-exe binary not found under {src / 'build'} (set KTH_BIN)")


def tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.splitlines()[-lines:])


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def kth_log_text() -> str:
    parts = [read_text(DATA / "kth-debug.log"), read_text(DATA / "kth-error.log"), read_text(OUT / "kth-docker.log")]
    return "\n".join(parts)


def p2pool_log_text() -> str:
    # logging.rs uses a daily RollingFileAppender, so the file log is
    # p2pool-file.log.<date>, not the configured prefix alone. Console
    # output is the process stderr captured in p2pool-console.log.
    parts = [read_text(OUT / "p2pool-console.log")]
    if OUT.is_dir():
        for path in sorted(OUT.glob("p2pool-file.log*")):
            parts.append(read_text(path))
    return "\n".join(parts)


def matching_lines(text: str, needles: tuple[str, ...], limit: int = 200) -> list[str]:
    found = [redact(line)[:500] for line in text.splitlines() if any(needle in line for needle in needles)]
    return found[-limit:]


def container_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        fail("docker inspect of the kth container timed out after 15s")
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_kth() -> None:
    if not container_running():
        fail("kth container exited before the check finished. log tail:\n" + tail(kth_log_text(), 40))


def ensure_p2pool() -> None:
    if P2POOL is not None and P2POOL.poll() is not None:
        fail(f"P2Pool exited {P2POOL.returncode}. log tail:\n" + tail(p2pool_log_text(), 40))


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
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            pass


def wallet_address() -> str:
    result = bitcoin_cli(BCHN_DIR, BCHN[1], ["getnewaddress"], check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    bitcoin_cli(BCHN_DIR, BCHN[1], ["createwallet", "e2e"])
    return cli("getnewaddress")


def bchn_relay_floor() -> list[str]:
    # Rows 25c and tdp: kth announces the spends it accepts, and BCHN A is its
    # only peer. A floor above the spends' fee rate keeps them out of A's
    # mempool, so A's next tip cannot confirm them. The fan-outs pay more and
    # still get in. kth connects to its configured peer once, at start, so A
    # stays online.
    if EVIDENCE.get("row") not in LARGE_ROWS:
        return []
    return [f"-minrelaytxfee={LARGE_RELAY_FLOOR_SATS_PER_KB / 100_000_000:.8f}"]


def start_bchn() -> None:
    if shutil.which("bitcoind") is None:
        fail("missing bitcoind on PATH (the workflow fetches BCHN 29.2.0)")
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        started = subprocess.run(
            ["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
             f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}", f"-rpcport={BCHN[1]}", f"-port={BCHN_P2P}",
             "-listen=1", "-txindex=1", "-fallbackfee=0.0001", "-allowunconnectedmining",
             f"-zmqpubhashblock={ZMQ}", *bchn_relay_floor()],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail("bitcoind did not daemonize within 30s")
    if started.returncode != 0:
        fail(f"bitcoind failed to start: {(started.stderr or started.stdout).strip()}")
    rpc_wait(BCHN, "BCHN A")


def start_bchn_b() -> None:
    # Quickstart section 1: getblocktemplate refuses until the node has a peer.
    # docs/bch-job-declaration.md runs the second node with the wallet disabled.
    BCHN_B_DIR.mkdir(parents=True, exist_ok=True)
    try:
        started = subprocess.run(
            ["bitcoind", "-regtest", f"-datadir={BCHN_B_DIR}", "-daemon", "-server",
             f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}", f"-rpcport={BCHN_B[1]}", f"-port={BCHN_B_P2P}",
             "-listen=0", "-dnsseed=0", "-disablewallet=1",
             f"-connect=127.0.0.1:{BCHN_P2P}"],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail("second bitcoind did not daemonize within 30s")
    if started.returncode != 0:
        fail(f"second bitcoind failed to start: {(started.stderr or started.stdout).strip()}")
    rpc_wait(BCHN_B, "BCHN B")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        count = cli("getconnectioncount")
        if count.isdigit() and int(count) >= 1:
            EVIDENCE["heights"]["bchn_peers"] = int(count)
            return
        time.sleep(1)
    fail("BCHN A had no peer within 60s, so getblocktemplate would return 'Bitcoin is not connected!'")


def toml_string(value: str) -> str:
    if '"' in value or "\n" in value:
        fail(f"refusing to write this value into p2pool.toml: {value!r}")
    return f'"{value}"'


def write_p2pool_config(payout: str, provider_key: str | None = None) -> dict:
    """With provider_key (row tdp), P2Pool takes its templates from kth's
    Template Provider on SV2_PORT over Noise, pinned to that authority key."""
    store = OUT / "p2pool-store"
    log_file = OUT / "p2pool-file.log"
    stats = OUT / "p2pool-stats"
    stats.mkdir(parents=True, exist_ok=True)
    config = OUT / "p2pool.toml"
    source = "rpc" if provider_key is None else "sv2"
    provider_address = f"127.0.0.1:{SV2_PORT}"
    provider = "" if provider_key is None else (
        f"template_provider_address = {toml_string(provider_address)}\n"
        f"template_provider_authority_public_key = {toml_string(provider_key)}\n"
    )
    # Keys are the fields Config::load accepts. Values and the reasons are in the report.
    text = f"""# Regtest config for the kth Job Declaration check.
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
zmqpubhashblock = {toml_string(ZMQ)}
chain = "bitcoin-cash"
bitcoin_cash_network = "regtest"
bch_share_chain = "mini"
sv2_port = {P2POOL_SV2}
sv2_authority_public_key = {toml_string(SV2_PUBLIC)}
sv2_authority_secret_key = {toml_string(SV2_SECRET)}
template_source = {toml_string(source)}
{provider}network = "regtest"
version_mask = "1fffffe0"
pool_signature = "P2Poolv2"
wait_for_chain_sync = false

[bitcoinrpc]
url = "http://127.0.0.1:{BCHN[1]}"
username = {toml_string(AUTH)}
password = {toml_string(AUTH)}

[logging]
file = {toml_string(str(log_file))}
console = true
level = "debug"
stats_dir = {toml_string(str(stats))}

[api]
hostname = "127.0.0.1"
port = {API_PORT}
"""
    config.write_text(text, encoding="utf-8")
    settings = {
        "config": str(config),
        "sv2_port": P2POOL_SV2,
        "sv2_authority_public_key": SV2_PUBLIC,
        "sv2_authority_secret_key": "(redacted; published test vector, written to p2pool.toml)",
        "bootstrap_address": payout,
        "chain": "bitcoin-cash",
        "bitcoin_cash_network": "regtest",
        "bch_share_chain": "mini",
        "template_source": source,
        "start_difficulty": 1,
        "minimum_difficulty": 1,
        "maximum_difficulty": 1,
        "wait_for_chain_sync": False,
        "version_mask": "1fffffe0",
        "logging_level": "debug",
        "donation": "unset, so the node default applies (read from its start-up log)",
        "donation_address": DONATION,
        "miner_address": None,
    }
    if provider_key is not None:
        settings["template_provider_address"] = provider_address
        settings["template_provider_authority_public_key"] = provider_key
    return settings


def share_address(cli_bin: str, cashaddr: str) -> str:
    try:
        result = subprocess.run(
            [cli_bin, "bch-share-address", "--network", "regtest", cashaddr],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail("p2poolv2_cli bch-share-address timed out after 30s")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(f"p2poolv2_cli bch-share-address failed: {detail}")
    printed = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    if not printed.startswith("rp2pool:"):
        fail(f"bch-share-address did not print an rp2pool address: {printed!r} stderr={result.stderr.strip()!r}")
    return printed


def start_p2pool(binary: str, config: Path) -> None:
    global P2POOL
    env = os.environ.copy()
    env.pop("RUST_LOG", None)
    P2POOL = spawn([binary, "--config", str(config)], OUT / "p2pool-console.log", env)
    deadline = time.monotonic() + 120
    last = ""
    sv2_open = False
    while time.monotonic() < deadline:
        ensure_p2pool()
        status, body = http_get("/health")
        if not sv2_open:
            sv2_open = port_open(P2POOL_SV2)
        if sv2_open and status == 200 and body.strip() == "OK":
            _, metrics = http_get("/metrics")
            EVIDENCE["p2pool"]["metrics_body"] = metrics
            progress(f"P2Pool SV2 port {P2POOL_SV2} accepted a connection and /health is OK")
            return
        last = f"tcp={sv2_open} health={status} {body.strip()!r}"
        time.sleep(1)
    fail(
        "P2Pool SV2 port did not accept a connection with /health OK within 120s "
        f"({last}). log tail:\n" + tail(p2pool_log_text(), 40)
    )


def start_kth(image: str, src: Path, binary: str, solo: str, identity: str | None) -> None:
    """Without identity (row tdp) kth runs only its Template Provider: no
    Mining Protocol and no JD client. Block announcements need no setting;
    the branch has #814 merged, so kth announces its blocks to BCHN A."""
    DATA.mkdir(parents=True, exist_ok=True)
    # kth creates and initializes its database only when the directory is
    # absent; a directory that exists is opened as an existing database.
    shutil.rmtree(DATA / "e2e", ignore_errors=True)
    try:
        relative = Path(binary).resolve().relative_to(src.resolve())
    except ValueError:
        fail(f"kth binary {binary} is not inside KTH_SRC {src}")
    workdir = "/src/" + relative.parent.as_posix()
    argv = [
        "--init_run", "--network", "regtest", "--node.display", "daemon",
        "--db.directory", "/data/e2e", "--db.db_mode", "full",
        "--net.inbound_port", str(KTH_P2P), "--net.inbound_connections", "0",
        "--net.outbound_connections", "1", "--net.peer", f"127.0.0.1:{BCHN_P2P}",
        "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", str(KTH[1]),
        "--rpc.user", AUTH, "--rpc.password", AUTH,
        "--log.debug_file", "/data/kth-debug.log", "--log.error_file", "/data/kth-error.log",
        "--sv2.enabled", "true", "--sv2.bind", "127.0.0.1", "--sv2.port", str(SV2_PORT),
        "--sv2.noise", "true",
        "--sv2.static_key_file", "/data/sv2-static.key",
        "--sv2.authority_key_file", "/data/sv2-authority.key",
    ]
    if identity is not None:
        argv += [
            "--sv2.mining.enabled", "true", "--sv2.mining.address", solo,
            "--sv2.jd.enabled", "true",
            "--sv2.jd.mining_address", f"127.0.0.1:{P2POOL_SV2}",
            "--sv2.jd.mining_authority_public_key", SV2_PUBLIC,
            "--sv2.jd.jds_address", f"127.0.0.1:{P2POOL_SV2}",
            "--sv2.jd.jds_authority_public_key", SV2_PUBLIC,
            "--sv2.jd.user_identity", identity,
            "--sv2.jd.mode", "full-template",
            "--sv2.jd.response_timeout_seconds", "10",
        ]
    EVIDENCE["kth_argv"] = argv
    try:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        fail("docker rm of a previous kth container timed out after 30s")
    try:
        result = subprocess.run(
            ["docker", "run", "--detach", "--name", CONTAINER, "--network", "host",
             "--platform", "linux/amd64",
             "-v", f"{src.resolve()}:/src", "-v", f"{DATA}:/data",
             "-w", workdir, "--entrypoint", "./" + relative.name, image, *argv],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        fail("docker run of kth timed out after 60s")
    if result.returncode != 0:
        fail(f"docker run of kth failed: {(result.stderr or result.stdout).strip()}")
    rpc_wait(KTH, "kth")
    target = int(cli("getblockcount"))
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        ensure_kth()
        height, error = call(KTH, "getblockcount")
        if error is None and height is not None and int(height) >= target:
            EVIDENCE["heights"]["kth_synced"] = int(height)
            return
        time.sleep(1)
    fail(f"kth did not reach BCHN height {target} within 240s. log tail:\n" + tail(kth_log_text(), 30))


def authority_key() -> str:
    deadline = time.monotonic() + 60
    pattern = re.compile(r"Noise authority public key: (\w+)")
    while time.monotonic() < deadline:
        ensure_kth()
        found = pattern.findall(kth_log_text())
        if found:
            return found[-1]
        time.sleep(1)
    fail("kth did not print 'Noise authority public key' within 60s. log tail:\n" + tail(kth_log_text(), 30))


def wait_kth_line(predicate, seconds: int, what: str) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ensure_kth()
        ensure_p2pool()
        text = kth_log_text()
        if predicate(text):
            return text
        time.sleep(1)
    fail(f"timed out after {seconds}s waiting for {what}. kth log tail:\n" + tail(kth_log_text(), 30))


def kth_height():
    result, error = call(KTH, "getblockcount")
    if error is not None or result is None:
        return None
    return int(result)


def parse_metrics(text: str) -> dict:
    values: dict = {}
    coinbase = []
    pattern = re.compile(r'coinbase_output\{index="(\d+)",address="([^"]*)"\} (\d+)')
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            coinbase.append({
                "index": int(match.group(1)),
                "address": match.group(2),
                "sats": int(match.group(3)),
            })
            continue
        parts = line.split()
        if len(parts) == 2 and parts[0] in (
            "shares_accepted_total", "shares_rejected_total", "coinbase_total",
            "network_difficulty", "pool_difficulty",
        ):
            values[parts[0]] = parts[1]
    values["coinbase_output"] = coinbase
    return values


def accepted_work(body) -> int:
    if not isinstance(body, dict):
        return 0
    total = 0
    for miner in body.get("miners") or []:
        try:
            total += int(miner.get("accepted_work") or 0)
        except (TypeError, ValueError):
            continue
    return total


def share_credit() -> int:
    status, body = http_get("/metrics")
    parsed = parse_metrics(body) if status == 200 else {}
    try:
        accepted = int(float(parsed.get("shares_accepted_total", "0")))
    except ValueError:
        accepted = 0
    _, miners = http_json("/miners")
    work = accepted_work(miners)
    pool = EVIDENCE["p2pool"]
    pool["shares_accepted_total"] = accepted
    pool["accepted_work"] = work
    pool["metrics_status"] = status
    snapshot = parsed.get("coinbase_output") or []
    key = tuple(sorted((item["sats"], item["address"]) for item in snapshot))
    if snapshot and key not in SEEN_SNAPSHOTS and len(pool["metrics_snapshots"]) < 20:
        SEEN_SNAPSHOTS.add(key)
        pool["metrics_snapshots"].append(snapshot)
    return max(accepted, work)


def same_address(left: str, right: str) -> bool:
    a, b = left.lower(), right.lower()
    return a == b or (a.split(":")[-1] != "" and a.split(":")[-1] == b.split(":")[-1])


def describe_block(height: int) -> dict:
    # parse_float keeps satoshi amounts exact. json's default float does not.
    raw = json.loads(cli("getblock", cli("getblockhash", str(height)), "1"))
    coinbase = json.loads(cli("getrawtransaction", raw["tx"][0], "1"), parse_float=Decimal)
    outputs = []
    for vout in coinbase["vout"]:
        script = vout["scriptPubKey"]
        addresses = list(script.get("addresses") or [])
        if script.get("address"):
            addresses.append(script["address"])
        amount = vout["value"] if isinstance(vout["value"], Decimal) else Decimal(str(vout["value"]))
        outputs.append({
            "n": vout["n"],
            "value": format(amount, "f"),
            "sats": int((amount * Decimal(100_000_000)).to_integral_value(rounding=ROUND_HALF_UP)),
            "addresses": addresses,
            "type": script.get("type"),
        })
    return {"height": height, "hash": raw["hash"], "coinbase_outputs": outputs}


def pays(outputs: list[dict], address: str) -> bool:
    return any(
        output["sats"] > 0 and any(same_address(item, address) for item in output["addresses"])
        for output in outputs
    )


def node_donation() -> tuple[int, str] | None:
    """The donation P2Pool logs at start: basis points and address."""
    found = re.search(r"Donating (\d+) basis points of each block this node finds to (\S+?)\.",
                      p2pool_log_text())
    return (int(found.group(1)), found.group(2)) if found else None


def pplns_verdict(block: dict, solo: str) -> dict:
    outputs = block["coinbase_outputs"]
    logged = node_donation()
    bps, donation_address = logged if logged else (0, DONATION)
    positive = [output for output in outputs if output["sats"] > 0]
    zero = [output for output in outputs if output["sats"] == 0]
    donation = [
        output for output in positive
        if any(same_address(item, donation_address) for item in output["addresses"])
    ]
    others = [
        output for output in positive
        if not any(same_address(item, donation_address) for item in output["addresses"])
    ]
    total = sum(output["sats"] for output in positive)
    expected = (total * bps) // 10000
    donation_sats = sum(output["sats"] for output in donation)
    # P2Pool BCH takes the donation per share ("cuts-per-share"): each window
    # share's own cut comes off its own weight, and the donation address is a
    # payee like any other. So its amount is at most the logged bps of the
    # block, less when the window also holds shares without cuts (genesis).
    near = (bps == 0 and not donation) or (bool(donation) and 0 < donation_sats <= expected + 10_000)
    solo_paid = pays(outputs, solo)
    block_sats = sorted(output["sats"] for output in positive)
    metrics_match = False
    for snapshot in EVIDENCE["p2pool"]["metrics_snapshots"]:
        snap_sats = sorted(item["sats"] for item in snapshot if item["sats"] > 0)
        if snap_sats == block_sats and block_sats:
            metrics_match = True
            break
    # A block found while the PPLNS window holds only the share chain's genesis
    # pays that genesis payee alone: genesis has no cut, so the donation payee
    # is absent there. The block that proves kth's pooled mining pays the
    # miner's own payout address and the donation payee its shares' cuts
    # create, and is exactly P2Pool's distribution.
    miner = EVIDENCE["addresses"].get("payout", "")
    pays_miner = any(any(same_address(item, miner) for item in output["addresses"])
                     for output in others)
    matched = (metrics_match and pays_miner and near and not solo_paid
               and len(zero) >= 1)
    return {
        "donation_sats": donation_sats,
        "expected_donation_sats": expected,
        "pays_miner": pays_miner,
        "donation_bps_logged": bps,
        "donation_address": donation_address,
        "donation_as_logged": near,
        "pays_solo": solo_paid,
        "other_positive_outputs": len(others),
        "zero_value_outputs": len(zero),
        "metrics_sats_matched": metrics_match,
        "match": matched,
    }


def ensure_on_bchn(height: int, wait: int = 20) -> dict:
    kth_hash, error = call(KTH, "getblockhash", height)
    if error or not kth_hash:
        fail(f"kth has no block at height {height}: {error}")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if bchn_hash(height) == kth_hash:
            return {"height": height, "hash": kth_hash, "path": "already on BCHN"}
        time.sleep(1)
    raw, error = call(KTH, "getblock", kth_hash, 0, timeout=max(60, wait))
    if error or not isinstance(raw, str) or not raw:
        fail(f"kth getblock {kth_hash} did not return raw hex: {error}")
    accepted, submit_error = call(BCHN, "submitblock", raw, timeout=max(60, wait))
    got = bchn_hash(height)
    if got != kth_hash:
        fail(
            f"BCHN did not accept kth block {height} {kth_hash}: "
            f"submitblock={accepted!r} error={submit_error!r} height_hash={got!r}"
        )
    return {
        "height": height,
        "hash": kth_hash,
        "path": "submitblock",
        "submitblock": accepted if submit_error is None else submit_error,
    }


def pull_blocks(cursor: int, stop_at: int, phase: str = "mine",
                wait: int = 20) -> tuple[int, list[dict]]:
    height = kth_height()
    if height is None:
        return cursor, []
    found = []
    while cursor < min(height, stop_at):
        cursor += 1
        landed = ensure_on_bchn(cursor, wait)
        block = describe_block(cursor)
        block["landed"] = landed
        block["phase"] = phase
        EVIDENCE["blocks"].append(block)
        found.append(block)
    return cursor, found


def device_command(binary: str, pubkey: str, cores: int | None, multiplier: float | None,
                   port: int = SV2_PORT, user: str = "kth-jd-e2e") -> list[str]:
    # Rows 25, 25b and 25c mine on kth's SV2 port; row tdp on P2Pool's, with
    # P2Pool's authority key and a P2Pool username.
    command = [
        binary, "--address-pool", f"127.0.0.1:{port}",
        "--pubkey-pool", pubkey, "--id-user", user,
    ]
    if cores is not None:
        command += ["--cores", str(cores)]
    if multiplier is not None:
        command += ["--nominal-hashrate-multiplier", f"{multiplier:.8g}"]
    if shutil.which("stdbuf"):
        command = ["stdbuf", "-oL", "-eL", *command]
    return command


def start_device(command: list[str], log_path: Path) -> subprocess.Popen:
    global DEVICE
    DEVICE = spawn(command, log_path)
    return DEVICE


def stop_device() -> None:
    global DEVICE
    stop_process(DEVICE)
    DEVICE = None


def plan_multiplier(printed_mh: int) -> tuple[float, float, float]:
    # format_mhs rounds to an integer. printed-0.5 is the low side of that rounding.
    if printed_mh <= 0:
        fail(
            "mining_device reported 0 MH/s, so a difficulty-1 downstream target "
            "cannot be derived from the measurement"
        )
    conservative_hps = (printed_mh - 0.5) * 1_000_000
    margin_budget = MINE_BUDGET - 30
    max_margin = margin_budget * conservative_hps / DIFF1_HASHES
    if max_margin < FLOOR_MARGIN:
        seconds = FLOOR_MARGIN * DIFF1_HASHES / conservative_hps
        fail(
            f"mining_device measured about {printed_mh} MH/s. A share at difficulty 1 "
            f"would take about {seconds:.0f}s, past the {MINE_BUDGET}s budget"
        )
    # Budget allows it: use FLOOR_MARGIN (1.1), not MAX_MARGIN (2.0).
    margin = min(FLOOR_MARGIN, max_margin)
    multiplier = margin * (DIFF1_HASHES / SHARE_INTERVAL) / conservative_hps
    expected = margin * DIFF1_HASHES / conservative_hps
    return multiplier, expected, margin


def mine_budget(printed_mh: int) -> tuple[int, float]:
    # A downstream share enters P2Pool's PPLNS window only when it also meets
    # the share chain's difficulty, which /metrics reports as pool_difficulty.
    # On P2Pool alpha the regtest mini chain starts at its anchor, about 4.5
    # (MINI_ANCHOR_TARGET 0x1c384bd7), which is also the easiest target that
    # chain allows. At about 20 MH/s one credited share then takes about 16
    # minutes on average, so 1200 s missed it in about one run in three. The
    # budget covers CREDIT_TAIL average waits for a credited share and one more
    # block to pay the split.
    status, body = http_get("/metrics")
    parsed = parse_metrics(body) if status == 200 else {}
    try:
        difficulty = float(parsed.get("pool_difficulty", "0"))
    except ValueError:
        difficulty = 0.0
    if difficulty <= 1:
        return MINE_BUDGET, difficulty
    hps = (printed_mh - 0.5) * 1_000_000
    credit = difficulty * DIFF1_HASHES / hps
    block = FLOOR_MARGIN * DIFF1_HASHES / hps
    return max(MINE_BUDGET, int(CREDIT_TAIL * credit + block)), difficulty


HASHRATE_LINE = re.compile(r"Measured CPU hashrate ≈ ([0-9][0-9,]*) MH/s")


def probe_hashrate(binary: str, pubkey: str, before: int) -> int:
    log_path = OUT / "mining-device-probe.log"
    proc = start_device(device_command(binary, pubkey, None, None), log_path)
    pattern = HASHRATE_LINE
    deadline = time.monotonic() + 45
    printed = None
    while time.monotonic() < deadline:
        ensure_kth()
        text = read_text(log_path)
        match = pattern.search(text)
        if match:
            printed = int(match.group(1).replace(",", ""))
            break
        if proc.poll() is not None:
            fail(f"mining_device exited {proc.returncode} during the hashrate probe. log:\n{text[-2000:]}")
        time.sleep(0.2)
    stop_device()
    # The line is logged before the channel opens. Read again after the
    # process exits, in case the formatter flushed on the way down.
    if printed is None:
        match = pattern.search(read_text(log_path))
        if match:
            printed = int(match.group(1).replace(",", ""))
    if printed is None:
        fail("mining_device did not print 'Measured CPU hashrate' within 45s. log:\n"
             + read_text(log_path)[-2000:])
    height = kth_height() or before
    if height > before + 2:
        fail(
            f"the hashrate probe connected {height - before} blocks before it was stopped. "
            "The measurement log was late, so the difficulty-1 multiplier was not applied in time"
        )
    return printed


# The single-pool client (#832) logs the first; the failover client (#833),
# with one configured entry, logs the second when it falls back to solo.
SOLO_LINES = ("continuing solo mining", "Active entry: solo")


def fallback_line(text: str) -> str:
    lines = [line for line in text.splitlines() if any(solo in line for solo in SOLO_LINES)]
    return redact(lines[-1]) if lines else ""


def refused(text: str) -> bool:
    return any(solo in text for solo in SOLO_LINES) and (
        "pool refused the local custom job" in text or "JDS refused the local template" in text
    )


def row25(binary: str, pubkey: str, solo: str) -> None:
    progress("row 25: waiting for P2Pool to refuse the plain coinbase")
    text = wait_kth_line(refused, 90, "kth to log the pool's refusal and continue solo mining")
    if "Pool accepted local template as job" in text:
        fail("row 25: kth logged 'Pool accepted local template as job' without the payout extension")
    # P2Pool refuses a plain coinbase either at its JDS, answering
    # DeclareMiningJob.Error with its own code (it logs nothing then), or at
    # the pool, which logs "Rejected SV2 custom job" before it answers through
    # a non-blocking writer. Either one is P2Pool's refusal.
    jds_refusal = "JDS refused the local template: invalid-coinbase-tx" in text

    def pool_refusal() -> bool:
        return "Rejected SV2 custom job" in p2pool_log_text()

    seen_rejection = jds_refusal or pool_refusal()
    rejection_deadline = time.monotonic() + 15
    while not seen_rejection and time.monotonic() < rejection_deadline:
        time.sleep(1)
        ensure_p2pool()
        seen_rejection = pool_refusal()
    if not seen_rejection:
        fail(
            "row 25: kth fell back, but neither P2Pool's JDS code invalid-coinbase-tx nor its "
            "'Rejected SV2 custom job' log line was seen. P2Pool tail:\n"
            + tail(p2pool_log_text(), 40)
        )
    EVIDENCE["refusal"] = "jds invalid-coinbase-tx" if jds_refusal else "pool rejected custom job"
    before = int(cli("getblockcount"))
    EVIDENCE["heights"]["before_device"] = before
    proc = start_device(device_command(binary, pubkey, 1, None), OUT / "mining-device.log")
    cursor = before
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        ensure_kth()
        ensure_p2pool()
        alive = proc.poll() is None
        if not alive:
            fail("mining_device exited before a block paying sv2.mining.address. log:\n"
                 + read_text(OUT / "mining-device.log")[-2000:])
        if "Pool accepted local template as job" in kth_log_text():
            fail("row 25: kth logged 'Pool accepted local template as job' after the refusal")
        cursor, blocks = pull_blocks(cursor, before + 12)
        for block in blocks:
            if pays(block["coinbase_outputs"], solo) and not pays(block["coinbase_outputs"], DONATION):
                if proc.poll() is not None:
                    fail("mining_device exited before the solo block was observed. log:\n"
                         + read_text(OUT / "mining-device.log")[-2000:])
                EVIDENCE["assertions"] = {
                    "pool_refused": True,
                    "p2pool_refusal": EVIDENCE["refusal"],
                    "pool_accepted_absent": True,
                    "device_still_running": True,
                    "solo_block": {"height": block["height"], "hash": block["hash"]},
                }
                progress(f"row 25: block {block['height']} pays the solo address")
                return
        height = kth_height()
        if height is not None and height > before + 12:
            fail("row 25: 12 new blocks and none pays only sv2.mining.address")
        time.sleep(1)
    fail("row 25: no block paying sv2.mining.address within 180s. blocks:\n"
         + json.dumps(EVIDENCE["blocks"], indent=2)[:2000])


def row25b(binary: str, pubkey: str, solo: str) -> None:
    progress(f"row {EVIDENCE['row']}: waiting for P2Pool to accept the custom job")
    deadline = time.monotonic() + 120
    accepted = False
    while time.monotonic() < deadline:
        ensure_kth()
        ensure_p2pool()
        if "Pool accepted local template as job" in kth_log_text():
            accepted = True
            break
        time.sleep(1)
    if not accepted:
        line = fallback_line(kth_log_text())
        fail(
            f"row {EVIDENCE['row']}: kth did not log 'Pool accepted local template as job' "
            "within 120s. "
            + (f"fallback: {line}" if line else "no 'continuing solo mining' line either")
        )
    before_probe = int(cli("getblockcount"))
    printed = probe_hashrate(binary, pubkey, before_probe)
    multiplier, expected, margin = plan_multiplier(printed)
    EVIDENCE["hashrate"] = {
        "printed_mh_s": printed,
        "margin": round(margin, 4),
        "nominal_hashrate_multiplier": multiplier,
        "expected_seconds": round(expected, 1),
    }
    progress(
        f"row {EVIDENCE['row']}: measured {printed} MH/s, multiplier {multiplier:.4f}, "
        f"expected share about {expected:.0f}s"
    )
    # A block the probe connected has to be on BCHN before the next one can extend it.
    # It is recorded with phase "probe" and cannot satisfy the PPLNS assertion.
    caught = kth_height()
    if caught is None:
        fail("kth RPC stopped answering after the hashrate probe")
    _, _ = pull_blocks(before_probe, caught, "probe")
    if EVIDENCE["row"] == "25c":
        probe_end = caught
        # After the probe, so the probe's blocks cannot take the mempool.
        EVIDENCE["large_mempool"] = fill_large_mempool(EVIDENCE["addresses"]["funder"])
        # Declarations follow tips. One more tip makes kth declare the large
        # template. Count acceptances only from here: the setup blocks above
        # each produced one for a template without the spends.
        accepted_before = kth_log_text().count("Pool accepted local template as job")
        EVIDENCE["large_mempool"]["declaration_tip"] = empty_tip("the PPLNS block")
        deadline = time.monotonic() + 600
        while kth_log_text().count("Pool accepted local template as job") <= accepted_before:
            ensure_kth()
            ensure_p2pool()
            line = fallback_line(kth_log_text())
            if time.monotonic() > deadline:
                fail("row 25c: P2Pool did not accept the large template within 600s. "
                     + (f"fallback: {line}" if line else ""))
            time.sleep(2)
        progress("row 25c: P2Pool accepted the large template")
        caught = kth_height()
        if caught is None:
            fail("kth RPC stopped answering after the large template")
        _, _ = pull_blocks(probe_end, caught, "setup")
    before = kth_height()
    if before is None:
        fail("kth RPC stopped answering after the hashrate probe")
    EVIDENCE["heights"]["before_device"] = before
    budget, pool_difficulty = mine_budget(printed)
    EVIDENCE["hashrate"]["pool_difficulty"] = pool_difficulty
    EVIDENCE["hashrate"]["mine_budget_s"] = budget
    progress(f"row {EVIDENCE['row']}: pool difficulty {pool_difficulty:g}, mining budget {budget}s")
    proc = start_device(
        device_command(binary, pubkey, None, multiplier),
        OUT / "mining-device.log",
    )
    cursor = before
    matched = None
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        ensure_kth()
        ensure_p2pool()
        if proc.poll() is not None:
            fail("mining_device exited before a credited share and a PPLNS block. log:\n"
                 + read_text(OUT / "mining-device.log")[-2000:])
        shares = share_credit()
        height = kth_height() or before
        if shares == 0 and height > before + EASY_BLOCK_LIMIT:
            pull_blocks(cursor, before + EASY_BLOCK_LIMIT)
            fail(
                "downstream shares are easier than P2Pool difficulty 1; "
                f"kth connected {height - before} blocks while shares_accepted_total stayed 0, "
                "so those shares were not forwarded"
            )
        # Land a bounded batch, then re-check the share counter. With a
        # credited share, a later batch can still hold the PPLNS block.
        # Without one, never look past the easy-block limit above.
        stop_at = cursor + EASY_BLOCK_LIMIT
        if shares == 0:
            stop_at = min(stop_at, before + EASY_BLOCK_LIMIT)
        cursor, blocks = pull_blocks(cursor, stop_at)
        # Row 25c: the first block after the large declaration is the one that
        # can hold the spends, whoever its PPLNS window pays.
        if EVIDENCE["row"] == "25c" and blocks and "large_template" not in EVIDENCE:
            EVIDENCE["large_template"] = require_large_block(blocks[0]["height"])
        for block in blocks:
            verdict = pplns_verdict(block, solo)
            block["pplns"] = verdict
            if verdict["match"] and matched is None:
                matched = block
        if shares > 0 and matched is not None:
            EVIDENCE["assertions"] = {
                "pool_accepted": True,
                "shares_accepted_total": EVIDENCE["p2pool"]["shares_accepted_total"],
                "accepted_work": EVIDENCE["p2pool"]["accepted_work"],
                "pplns_block": {"height": matched["height"], "hash": matched["hash"]},
                "metrics_sats_matched": matched["pplns"]["metrics_sats_matched"],
                "later_fallback": bool(fallback_line(kth_log_text())),
            }
            if EVIDENCE["row"] == "25c":
                EVIDENCE["assertions"]["large_template"] = EVIDENCE["large_template"]
            progress(f"row {EVIDENCE['row']}: share credited and block {matched['height']} "
                     "pays the PPLNS split")
            return
        time.sleep(2)
    line = fallback_line(kth_log_text())
    fail(
        f"row {EVIDENCE['row']}: no credited share and PPLNS block within "
        f"{budget}s (shares_accepted_total={EVIDENCE['p2pool']['shares_accepted_total']}, "
        f"accepted_work={EVIDENCE['p2pool']['accepted_work']}). "
        + (f"fallback: {line}. " if line else "")
        + "blocks:\n" + json.dumps(EVIDENCE["blocks"], indent=2)[:3000]
    )


# TEST ONLY: the mempool of rows 25c and tdp. Every output is P2SH(OP_TRUE),
# which regtest relays as nonstandard and anyone can spend with the scriptSig
# 01 51 (a push of the redeem script OP_1), so the harness signs nothing.
LARGE_ROWS = ("25c", "tdp")
LARGE_FUNDING_BLOCKS = 50
LARGE_FANOUT = 2_000
LARGE_SPENDS = LARGE_FUNDING_BLOCKS * LARGE_FANOUT  # 100,000 > 65,535
LARGE_FANOUT_SATS = 1_000_000
LARGE_FEE_SATS = 1_000
# Satoshis per 1,000 bytes. A spend pays 1,000 sats for 187 bytes (5,348/kB),
# under the floor. A fan-out is about 64,100 bytes and pays
# LARGE_FANOUT_FEE_SATS (about 15,600/kB), over it.
LARGE_RELAY_FLOOR_SATS_PER_KB = 8_000
LARGE_FANOUT_FEE_SATS = 1_000_000
# One 90-byte OP_RETURN per spend makes each 187 bytes, so the spends total
# 18,700,000 bytes: more than 2^24 - 1, within regtest's 32 MB floor.
LARGE_PADDING = bytes(range(90))
U24_MAX = (1 << 24) - 1
SEQ0_64K_MAX = 65_535


def varint(value: int) -> bytes:
    if value < 0xFD:
        return value.to_bytes(1, "little")
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    return b"\xfe" + value.to_bytes(4, "little")


def output(sats: int, script: bytes) -> bytes:
    return sats.to_bytes(8, "little") + varint(len(script)) + script


def spend_true(txid: str, vout: int, outputs: list[bytes]) -> str:
    """Version 2, one P2SH(OP_TRUE) input, the given outputs, lock time 0."""
    script_sig = bytes([0x01, 0x51])
    raw = (2).to_bytes(4, "little") + varint(1)
    raw += bytes.fromhex(txid)[::-1] + vout.to_bytes(4, "little")
    raw += varint(len(script_sig)) + script_sig + (0xFFFFFFFF).to_bytes(4, "little")
    raw += varint(len(outputs)) + b"".join(outputs) + (0).to_bytes(4, "little")
    return raw.hex()


def sats(value) -> int:
    return int((Decimal(str(value)) * 100_000_000).to_integral_value(ROUND_HALF_UP))


def send_to_kth(raws: list[str]) -> int:
    """sendrawtransaction to kth in JSON-RPC batches; returns how many it took."""
    global _next_id
    taken = 0
    for start in range(0, len(raws), 500):
        chunk = raws[start:start + 500]
        body = []
        for raw in chunk:
            _next_id += 1
            body.append({"id": _next_id, "method": "sendrawtransaction", "params": [raw]})
        reply = post(KTH, json.dumps(body).encode(), timeout=300)
        if reply["status"] is None:
            # kth may have applied the batch; resending would read as refusals.
            fail(f"row {EVIDENCE['row']}: the batch request to kth was lost: {reply.get('transport')}")
        answers = reply["json"]
        if isinstance(answers, list) and len(answers) != len(chunk):
            fail(f"row {EVIDENCE['row']}: kth answered {len(answers)} of a {len(chunk)}-transaction batch")
        if not isinstance(answers, list):
            # No batch support: one call per transaction.
            for raw in chunk:
                result, error = call(KTH, "sendrawtransaction", raw, timeout=60)
                if error is not None:
                    fail(f"row {EVIDENCE['row']}: kth refused a spend: {error}")
                taken += 1
            continue
        for answer in answers:
            if not isinstance(answer, dict) or answer.get("error") is not None:
                fail(f"row {EVIDENCE['row']}: kth refused a spend in a batch: {str(answer)[:300]}")
            taken += 1
        if start % 20_000 == 0:
            progress(f"row {EVIDENCE['row']}: kth took {taken} of {len(raws)} spends")
    return taken


LARGE_FUNDING: dict = {}


def fund_large_mempool(funder: str) -> None:
    """Mine the P2SH(OP_TRUE) coinbases of rows 25c and tdp, and their
    maturity, during setup.

    This runs before P2Pool and kth start. Mining 150 tips in a second while
    Job Declaration runs would race each declared job against the next tip.
    """
    decoded = json.loads(cli("decodescript", "51"))
    address = decoded["p2sh"]
    progress(f"row {EVIDENCE['row']}: mining {LARGE_FUNDING_BLOCKS} coinbases to P2SH(OP_TRUE) {address}")
    hashes = json.loads(cli("generatetoaddress", str(LARGE_FUNDING_BLOCKS), address))
    bitcoin_cli(BCHN_DIR, BCHN[1], ["generatetoaddress", "100", funder], timeout=300)
    LARGE_FUNDING.update(address=address, hashes=hashes)


def fill_large_mempool(funder: str) -> dict:
    """Leave kth with a mempool of LARGE_SPENDS spends; BCHN does not see them."""
    address = LARGE_FUNDING["address"]
    hashes = LARGE_FUNDING["hashes"]
    script = bytes.fromhex(json.loads(cli("validateaddress", address))["scriptPubKey"])
    fanouts = []
    for block_hash in hashes:
        block = json.loads(cli("getblock", block_hash, "2"))
        coinbase = block["tx"][0]
        value = sats(coinbase["vout"][0]["value"])
        change = value - LARGE_FANOUT * LARGE_FANOUT_SATS - LARGE_FANOUT_FEE_SATS
        if change < 0:
            fail(f"row {EVIDENCE['row']}: coinbase {coinbase['txid']} pays {value} sats, "
                 "too little for the fan-out")
        outs = [output(LARGE_FANOUT_SATS, script) for _ in range(LARGE_FANOUT)]
        if change > 0:
            outs.append(output(change, script))
        txid = cli("sendrawtransaction", spend_true(coinbase["txid"], 0, outs))
        fanouts.append(txid)
    # Confirm the fan-outs so every spend has a confirmed parent.
    bitcoin_cli(BCHN_DIR, BCHN[1], ["generatetoaddress", "1", funder], timeout=300)
    target = int(cli("getblockcount"))
    deadline = time.monotonic() + 300
    while (kth_height() or 0) < target:
        ensure_kth()
        if time.monotonic() > deadline:
            fail(f"row {EVIDENCE['row']}: kth did not reach height {target} after the fan-out")
        time.sleep(1)
    padding = bytes([0x6A, 0x4C, len(LARGE_PADDING)]) + LARGE_PADDING
    raws = []
    for txid in fanouts:
        for vout in range(LARGE_FANOUT):
            raws.append(spend_true(txid, vout, [
                output(LARGE_FANOUT_SATS - LARGE_FEE_SATS, script),
                output(0, padding),
            ]))
    total = sum(len(raw) // 2 for raw in raws)
    progress(f"row {EVIDENCE['row']}: sending {len(raws)} spends, {total} bytes, to kth")
    taken = send_to_kth(raws)
    relayed = json.loads(cli("getmempoolinfo"))
    if int(relayed.get("size", 0)) != 0:
        fail(f"row {EVIDENCE['row']}: BCHN A took {relayed.get('size')} of the spends kth relayed; "
             "the relay floor did not keep them out")
    info, error = call(KTH, "getmempoolinfo")
    if error is not None or not info or int(info.get("size", 0)) < LARGE_SPENDS:
        fail(f"row {EVIDENCE['row']}: kth mempool after the spends: {info!r} {error!r}")
    if total <= U24_MAX or len(raws) <= SEQ0_64K_MAX:
        fail(f"row {EVIDENCE['row']}: the spends are not beyond the standard limits "
             f"({len(raws)} txs, {total} bytes)")
    return {
        "address": address,
        "funding_blocks": LARGE_FUNDING_BLOCKS,
        "fanouts": len(fanouts),
        "spends": len(raws),
        "spend_bytes": total,
        "kth_accepted": taken,
        "kth_mempool": info,
    }


def require_large_block(height: int) -> dict:
    block = json.loads(cli("getblock", cli("getblockhash", str(height)), "1"))
    count, size = int(block["nTx"]), int(block["size"])
    if count <= SEQ0_64K_MAX + 1 or size <= U24_MAX:
        fail(f"row {EVIDENCE['row']}: block {height} has {count} transactions and {size} bytes; "
             "the large template was not mined")
    return {"height": height, "transactions": count, "bytes": size}


def empty_tip(until: str) -> str:
    """Mine one BCHN tip, which must confirm none of kth's spends. kth builds
    its declarations and templates on tips, so the next one holds the spends."""
    tip = json.loads(bitcoin_cli(BCHN_DIR, BCHN[1],
        ["generatetoaddress", "1", EVIDENCE["addresses"]["funder"]], timeout=120).stdout)[0]
    tip_block = json.loads(cli("getblock", tip, "1"))
    if int(tip_block["nTx"]) != 1:
        # kth relayed the spends to BCHN, which mined them into this tip.
        fail(f"row {EVIDENCE['row']}: BCHN's extra tip {tip} holds {tip_block['nTx']} transactions; "
             f"the spends must stay in kth's mempool until {until}")
    return tip


# TEST ONLY: row tdp. P2Pool's Template Distribution client
# (p2poolv2_lib/src/stratum/sv2/template_distribution.rs on P2Pool's
# feat/tdp-large-templates) logs, among others:
#   "Template Provider at {address} granted the large templates extension
#    (0x4C54); it pushes each template's transactions"
#   "Template Provider at {address} did not grant the large templates
#    extension (0x4C54); asking for each template's transactions"
#   "Template Provider at {address} did not answer RequestExtensions within
#    {seconds}s; asking for each template's transactions"
#   "Template Provider refused transactions for template {id}: {error_code}"
#   "Template Provider template {id} at height {height} with {count} transactions"
#   "Sent block {hash} to the Template Provider for template {id}"
#   "Block {hash} does not fit SubmitSolution; submitting it over RPC"
#   "Block {hash} could not reach the Template Provider; submitting it over RPC"
#   "Template Provider connection to {address} ended: {reason}; reconnecting
#    to {address} in {seconds}s"
# A template is published only after its list gives its merkle path. A
# connection granted the extension never sends RequestTransactionData, and
# one core Success carries at most 65,535 transactions, so a published
# template with more came in kth's push.
TDP_GRANTED = "granted the large templates extension (0x4C54)"
TDP_NOT_GRANTED = (
    "did not grant the large templates extension (0x4C54)",
    "did not answer RequestExtensions within",
)
TDP_PUBLISHED = re.compile(r"Template Provider template (\d+) at height (\d+) with (\d+) transactions")
TDP_REFUSED = re.compile(r"Template Provider refused transactions for template (\d+): (\S+)")
TDP_SENT = re.compile(r"Sent block ([0-9a-f]{64}) to the Template Provider for template (\d+)")
TDP_OVER_RPC = (
    "does not fit SubmitSolution; submitting it over RPC",
    "could not reach the Template Provider; submitting it over RPC",
)
TDP_ENDED = "Template Provider connection to"
TOO_LARGE = "template-too-large"
# kth's Template Provider (src/node/src/sv2/template_provider.cpp) logs
#   "[sv2] Submitted block {hash}: accepted"
#   "[sv2] Refusing transaction data for template {id}: too large for
#    RequestTransactionData.Success"
KTH_SUBMITTED = re.compile(r"\[sv2\] Submitted block ([0-9a-f]{64}): (.+)")
KTH_TOO_LARGE = "[sv2] Refusing transaction data for template"
# Seconds for P2Pool to log kth's answer to RequestExtensions (P2Pool itself
# waits 10 s for it), for kth's large template to reach P2Pool (row 25c's wait
# for its declaration), and for kth and then BCHN to take a large block.
TDP_SETUP_WAIT = 60
TDP_TEMPLATE_WAIT = 600
TDP_LANDING_WAIT = 180


def published_templates(text: str) -> dict[int, dict]:
    """The templates P2Pool published, by id. The console and file logs both
    hold each line."""
    found: dict[int, dict] = {}
    for match in TDP_PUBLISHED.finditer(text):
        template_id = int(match.group(1))
        found[template_id] = {
            "template_id": template_id,
            "height": int(match.group(2)),
            "transactions": int(match.group(3)),
        }
    return found


def large_templates(text: str) -> dict[int, dict]:
    """Published templates with more transactions than one core Success carries."""
    return {template_id: item for template_id, item in published_templates(text).items()
            if item["transactions"] > SEQ0_64K_MAX}


def too_large_refusals(text: str) -> list[int]:
    return sorted({int(match.group(1)) for match in TDP_REFUSED.finditer(text)
                   if match.group(2) == TOO_LARGE})


def kth_verdict(block_hash: str) -> str | None:
    """What kth's Template Provider logged for a solution's block, if anything."""
    verdict = None
    for match in KTH_SUBMITTED.finditer(kth_log_text()):
        if match.group(1) == block_hash:
            verdict = match.group(2).strip()
    return verdict


def device_hashrate(proc: subprocess.Popen, log_path: Path) -> int:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        ensure_kth()
        ensure_p2pool()
        match = HASHRATE_LINE.search(read_text(log_path))
        if match:
            return int(match.group(1).replace(",", ""))
        if proc.poll() is not None:
            fail(f"mining_device exited {proc.returncode} before it measured its hashrate. log:\n"
                 + read_text(log_path)[-2000:])
        time.sleep(0.2)
    fail("mining_device did not print 'Measured CPU hashrate' within 45s. log:\n"
         + read_text(log_path)[-2000:])


def block_budget(printed_mh: int) -> tuple[int, float]:
    # Row tdp: the device mines on P2Pool's own SV2 port. P2Pool opens a
    # channel at the difficulty its nominal hashrate asks for, held between
    # minimum_difficulty and maximum_difficulty (initial_difficulty in
    # stratum/sv2/mining.rs), both 1 here, and a regtest block needs far less,
    # so every share the device finds is a block. One takes DIFF1_HASHES
    # hashes on average, and CREDIT_TAIL such waits miss a block about
    # e^-CREDIT_TAIL of the time.
    if printed_mh <= 0:
        fail("mining_device reported 0 MH/s, so the time to a difficulty-1 block cannot be derived")
    hps = (printed_mh - 0.5) * 1_000_000
    expected = DIFF1_HASHES / hps
    if FLOOR_MARGIN * expected > MINE_BUDGET - 30:
        fail(f"mining_device measured about {printed_mh} MH/s. A block at difficulty 1 "
             f"would take about {expected:.0f}s, past the {MINE_BUDGET}s budget")
    return max(MINE_BUDGET, int(CREDIT_TAIL * expected)), expected


def no_large_block(budget: int, answer: str) -> NoReturn:
    """Row tdp's failure when the budget ends with no large block: what kth
    answered P2Pool, and why P2Pool had no large template to mine."""
    row = EVIDENCE["row"]
    text = p2pool_log_text()
    refused = too_large_refusals(text)
    kth_refusal = matching_lines(kth_log_text(), (KTH_TOO_LARGE,), 1)
    largest = max(published_templates(text).values(),
                  key=lambda item: item["transactions"], default=None)
    ended = matching_lines(text, (TDP_ENDED,), 1)
    parts = [
        f"row {row}: no block of more than {SEQ0_64K_MAX:,} transactions and {U24_MAX:,} bytes "
        f"went back to kth through SubmitSolution within the {budget}s budget."
    ]
    if TDP_GRANTED not in answer:
        parts.append(f"kth did not grant 0x4C54 on Template Distribution (P2Pool: {answer.strip()}).")
    if refused:
        parts.append(
            f"kth answered RequestTransactionData for template(s) {refused} with {TOO_LARGE}"
            + (f" ({kth_refusal[-1].strip()})" if kth_refusal else "")
            + ": this kth has no Template Distribution paging, so P2Pool never had the "
            "large template to mine."
        )
    if largest:
        parts.append(f"The largest template P2Pool published held {largest['transactions']:,} "
                     f"transactions (template {largest['template_id']}, height {largest['height']}).")
    if ended:
        parts.append(f"Last provider connection end: {ended[-1].strip()}")
    fail(" ".join(parts) + " blocks:\n" + json.dumps(EVIDENCE["blocks"], indent=2)[:3000])


def row_tdp(binary: str, identity: str, solo: str) -> None:
    row = EVIDENCE["row"]
    tdp: dict = {}
    EVIDENCE["template_distribution"] = tdp
    progress(f"row {row}: waiting for kth's answer to P2Pool's RequestExtensions (0x4C54)")
    deadline = time.monotonic() + TDP_SETUP_WAIT
    while True:
        ensure_kth()
        ensure_p2pool()
        answers = matching_lines(p2pool_log_text(), (TDP_GRANTED, *TDP_NOT_GRANTED))
        if answers:
            break
        if time.monotonic() > deadline:
            fail(f"row {row}: P2Pool logged no answer from kth's Template Provider to "
                 f"RequestExtensions within {TDP_SETUP_WAIT}s, so its Template Distribution "
                 "client never set up. P2Pool tail:\n" + tail(p2pool_log_text(), 40))
        time.sleep(1)
    answer = answers[-1]
    tdp["extension_answer"] = answer
    tdp["granted"] = TDP_GRANTED in answer
    # Not a failure yet: a kth without the extension goes on to answer
    # template-too-large, which the failure at the end of the budget names.
    progress(f"row {row}: kth {'granted' if tdp['granted'] else 'did not grant'} 0x4C54")

    EVIDENCE["large_mempool"] = fill_large_mempool(EVIDENCE["addresses"]["funder"])
    # kth's Template Provider builds a template on each tip, never on a
    # mempool change, so one more tip makes it build one from the spends.
    EVIDENCE["large_mempool"]["template_tip"] = empty_tip("the large block")
    progress(f"row {row}: waiting for kth's template of the spends to reach P2Pool")
    deadline = time.monotonic() + TDP_TEMPLATE_WAIT
    while True:
        ensure_kth()
        ensure_p2pool()
        text = p2pool_log_text()
        large, refused = large_templates(text), too_large_refusals(text)
        if large or refused:
            break
        if time.monotonic() > deadline:
            ended = matching_lines(text, (TDP_ENDED,), 3)
            fail(f"row {row}: within {TDP_TEMPLATE_WAIT}s of the tip, P2Pool published no template "
                 f"of more than {SEQ0_64K_MAX:,} transactions and logged no {TOO_LARGE} refusal. "
                 + (f"Provider connection: {' | '.join(ended)}. " if ended else "")
                 + "P2Pool tail:\n" + tail(text, 40))
        time.sleep(2)
    tdp["large_templates"] = list(large.values())
    tdp["refused_templates"] = refused
    if large:
        progress(f"row {row}: P2Pool published kth's pushed templates {tdp['large_templates']}")
    else:
        progress(f"row {row}: kth answered {TOO_LARGE} for template(s) {refused}; "
                 "mining the budget anyway")

    before = kth_height()
    if before is None:
        fail("kth RPC stopped answering after the large template")
    EVIDENCE["heights"]["before_device"] = before
    log_path = OUT / "mining-device.log"
    proc = start_device(
        device_command(binary, SV2_PUBLIC, None, None, port=P2POOL_SV2, user=identity),
        log_path,
    )
    printed = device_hashrate(proc, log_path)
    budget, expected = block_budget(printed)
    EVIDENCE["hashrate"] = {
        "printed_mh_s": printed,
        "channel_difficulty": 1,
        "expected_block_seconds": round(expected, 1),
        "mine_budget_s": budget,
    }
    progress(f"row {row}: measured {printed} MH/s, a block about every {expected:.0f}s, "
             f"mining budget {budget}s")
    cursor = before
    sent = None
    deadline = time.monotonic() + budget
    while sent is None:
        ensure_kth()
        ensure_p2pool()
        if proc.poll() is not None:
            fail("mining_device exited before a block on the large template. log:\n"
                 + read_text(log_path)[-2000:])
        if time.monotonic() > deadline:
            no_large_block(budget, answer)
        share_credit()
        text = p2pool_log_text()
        large = large_templates(text)
        over_rpc = matching_lines(text, TDP_OVER_RPC, 1)
        if over_rpc:
            fail(f"row {row}: P2Pool sent a found block over RPC instead of SubmitSolution: "
                 f"{over_rpc[-1].strip()}")
        for match in TDP_SENT.finditer(text):
            if int(match.group(2)) in large:
                sent = (match.group(1), int(match.group(2)))
                break
        cursor, blocks = pull_blocks(cursor, cursor + EASY_BLOCK_LIMIT, wait=TDP_LANDING_WAIT)
        for block in blocks:
            block["pplns"] = pplns_verdict(block, solo)
        if sent is None:
            time.sleep(2)
    stop_device()

    block_hash, template_id = sent
    template = large[template_id]
    height = template["height"]
    tdp["p2pool_sent"] = matching_lines(text, (f"Sent block {block_hash}",), 1)[-1].strip()
    progress(f"row {row}: P2Pool sent block {block_hash} on template {template_id} to kth")
    # kth logs its verdict once the block is connected or refused.
    deadline = time.monotonic() + TDP_LANDING_WAIT
    while True:
        ensure_kth()
        verdict = kth_verdict(block_hash)
        kth_hash, _ = call(KTH, "getblockhash", height)
        if verdict is not None or kth_hash == block_hash:
            break
        if time.monotonic() > deadline:
            fail(f"row {row}: kth logged no '[sv2] Submitted block {block_hash}' and has no such "
                 f"block at height {height} {TDP_LANDING_WAIT}s after P2Pool's SubmitSolution. "
                 "kth tail:\n" + tail(kth_log_text(), 30))
        time.sleep(1)
    tdp["kth_submitted"] = verdict
    if verdict is not None and not verdict.startswith("accepted"):
        fail(f"row {row}: kth refused block {block_hash} from P2Pool's SubmitSolution: {verdict}")
    cursor, _ = pull_blocks(cursor, height, wait=TDP_LANDING_WAIT)
    block = next((item for item in EVIDENCE["blocks"] if item["hash"] == block_hash), None)
    if block is None:
        fail(f"row {row}: kth's block at height {height} is not {block_hash}, the block P2Pool sent")
    block.setdefault("pplns", pplns_verdict(block, solo))
    # BCHN accepted it: ensure_on_bchn found it at its height, or submitted it.
    shape = require_large_block(height)
    if shape["transactions"] != template["transactions"] + 1:
        fail(f"row {row}: block {height} has {shape['transactions']} transactions, but P2Pool's "
             f"template {template_id} had {template['transactions']} and the coinbase")
    if TDP_GRANTED not in p2pool_log_text():
        fail(f"row {row}: block {height} is large, but P2Pool never logged '{TDP_GRANTED}'")
    tip = cli("getbestblockhash")
    EVIDENCE["large_template"] = shape
    EVIDENCE["assertions"] = {
        "extension_granted": True,
        "large_template": template,
        "large_block": {**shape, "hash": block_hash},
        "p2pool_submit_solution": tdp["p2pool_sent"],
        "kth_submitted": verdict or "(not in kth's log; kth's chain has the block)",
        "bchn_path": block["landed"]["path"],
        "bchn_tip": tip,
        "bchn_tip_is_block": tip == block_hash,
        "pplns_match": block["pplns"]["match"],
    }
    tdp["p2pool_lines"] = matching_lines(
        p2pool_log_text(), ("Template Provider", "Submitting BCH block candidate"), 40)
    progress(f"row {row}: block {height} {block_hash}, {shape['transactions']:,} transactions and "
             f"{shape['bytes']:,} bytes, went through SubmitSolution to kth and BCHN accepted it")


def record_p2pool_api() -> None:
    pool = EVIDENCE["p2pool"]
    for name, path in (
        ("miners", "/miners"),
        ("chain_info", "/chain_info"),
        ("found_blocks", "/found_blocks"),
        ("shares", "/shares"),
    ):
        status, body = http_json(path)
        pool[name] = body if status == 200 else {"status": status, "body": body}
    share_credit()


def copy_tail(src: Path, dest: Path, limit: int = 400_000) -> None:
    if not src.is_file():
        return
    dest.write_text(read_text(src)[-limit:], encoding="utf-8")


def attach_logs() -> None:
    EVIDENCE["kth_log_lines"] = matching_lines(
        kth_log_text(),
        ("[sv2 jd]", "Noise authority public key", "[sv2] Cannot start", "[sv2] failed",
         "[sv2] Submitted block", KTH_TOO_LARGE),
    )
    EVIDENCE["p2pool_log_lines"] = matching_lines(
        p2pool_log_text(),
        ("SV2 share", "Rejected SV2 custom job", "Block submitted", "invalid-custom-job",
         "Stratum V2 listening", "Error polling block template", "Failed to load",
         "Template Provider", "Submitting BCH block candidate"),
    )


def _cleanup_step(errors: list[str], label: str, action) -> None:
    # SystemExit must not escape: fail() is for the check, and a raise here
    # would skip the rest of the finally block that writes the JSON.
    try:
        action()
    except Exception as error:
        errors.append(f"{label}: {type(error).__name__}: {error}")


def cleanup() -> None:
    errors: list[str] = []

    def stop_container() -> None:
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "stop", "-t", "20", CONTAINER],
            capture_output=True, timeout=45,
        )

    def save_docker_logs() -> None:
        if shutil.which("docker") is None:
            return
        result = subprocess.run(
            ["docker", "logs", CONTAINER],
            capture_output=True, timeout=30,
        )
        (OUT / "kth-docker.log").write_bytes(result.stdout + result.stderr)

    def copy_kth_logs() -> None:
        for path in (DATA / "kth-debug.log", DATA / "kth-error.log"):
            if not path.is_file():
                continue
            target = OUT / path.name
            if path.resolve() != target.resolve():
                target.write_bytes(path.read_bytes())

    def remove_container() -> None:
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "rm", "-f", CONTAINER],
            capture_output=True, timeout=30,
        )

    def stop_bitcoinds() -> None:
        if shutil.which("bitcoin-cli") is None:
            return
        for datadir, port in ((BCHN_DIR, BCHN[1]), (BCHN_B_DIR, BCHN_B[1])):
            subprocess.run(
                ["bitcoin-cli", "-regtest", f"-datadir={datadir}", f"-rpcuser={AUTH}",
                 f"-rpcpassword={AUTH}", f"-rpcport={port}", "stop"],
                capture_output=True, text=True, timeout=30,
            )

    def copy_bchn_logs() -> None:
        copy_tail(BCHN_DIR / "regtest" / "debug.log", OUT / "bchn-debug.tail.log")
        copy_tail(BCHN_B_DIR / "regtest" / "debug.log", OUT / "bchn-b-debug.tail.log")

    def close_handles() -> None:
        for handle in HANDLES:
            try:
                handle.close()
            except OSError:
                pass

    _cleanup_step(errors, "mining_device", stop_device)
    _cleanup_step(errors, "docker stop", stop_container)
    _cleanup_step(errors, "docker logs", save_docker_logs)
    _cleanup_step(errors, "kth log copy", copy_kth_logs)
    _cleanup_step(errors, "docker rm", remove_container)
    _cleanup_step(errors, "p2pool stop", lambda: stop_process(P2POOL))
    _cleanup_step(errors, "log handles", close_handles)
    _cleanup_step(errors, "bitcoind stop", stop_bitcoinds)
    _cleanup_step(errors, "bchn log copy", copy_bchn_logs)
    if errors:
        EVIDENCE["cleanup_error"] = "; ".join(errors)


def write_evidence() -> None:
    try:
        (OUT / "jd-p2pool-e2e.json").write_text(
            json.dumps(EVIDENCE, indent=2) + "\n", encoding="utf-8",
        )
    except OSError as error:
        print(f"could not write evidence: {error}", flush=True)


def run() -> None:
    if shutil.which("docker") is None:
        fail("missing docker")
    image = require_env("KTH_IMAGE")
    row = require_env("KTH_JD_ROW")
    if row not in ("25", "25b", "25c", "tdp"):
        fail(f"KTH_JD_ROW must be 25, 25b, 25c or tdp, got {row!r}")
    EVIDENCE["row"] = row
    src = Path(require_env("KTH_SRC")).resolve()
    if not src.is_dir():
        fail(f"missing kth source tree (KTH_SRC): {src}")
    binary = find_kth(src)
    p2pool_bin = require_file(require_env("P2POOL_BIN"), "p2poolv2 binary")
    p2pool_cli = require_file(require_env("P2POOL_CLI"), "p2poolv2_cli binary")
    device_bin = require_file(require_env("KTH_MINING_DEVICE"), "mining_device binary")
    EVIDENCE["binaries"] = {
        "kth": binary, "p2pool": p2pool_bin, "p2pool_cli": p2pool_cli, "mining_device": device_bin,
        "image": image,
    }

    progress("starting BCHN regtest")
    start_bchn()
    addresses = [wallet_address() for _ in range(4)]
    if len(set(addresses)) != 4:
        fail(f"BCHN did not return four distinct addresses: {addresses}")
    funder, solo, payout, share_source = addresses
    EVIDENCE["addresses"] = {
        "funder": funder, "solo": solo, "payout": payout, "share_source": share_source,
    }
    progress("mining 101 blocks")
    bitcoin_cli(BCHN_DIR, BCHN[1], ["generatetoaddress", "101", funder], timeout=180)
    if row in LARGE_ROWS:
        fund_large_mempool(funder)
    EVIDENCE["heights"]["after_funding"] = int(cli("getblockcount"))
    start_bchn_b()

    share = share_address(p2pool_cli, share_source)
    identity = f"{payout},p2p={share}"
    if len(identity.encode()) > 255 or len(payout.encode()) > 128:
        fail(f"user identity does not fit kth's 255 bytes or P2Pool's 128-byte username: {identity}")
    EVIDENCE["addresses"]["share"] = share
    EVIDENCE["addresses"]["user_identity"] = identity

    if row == "tdp":
        # P2Pool pins the authority key kth prints, so kth starts first. Its
        # JD client is off, so nothing in kth waits for P2Pool.
        progress("starting kth")
        start_kth(image, src, binary, solo, None)
        pubkey = authority_key()
        EVIDENCE["kth_authority_public_key"] = pubkey
        if not port_open(SV2_PORT):
            fail(f"kth SV2 port {SV2_PORT} is not accepting connections")
        EVIDENCE["settings"] = write_p2pool_config(payout, pubkey)
        EVIDENCE["settings"]["user_identity"] = identity
        progress("starting P2Pool")
        start_p2pool(p2pool_bin, OUT / "p2pool.toml")
        row_tdp(device_bin, identity, solo)
    else:
        EVIDENCE["settings"] = write_p2pool_config(payout)
        EVIDENCE["settings"]["user_identity"] = identity
        EVIDENCE["settings"]["sv2_mining_address"] = solo

        progress("starting P2Pool")
        start_p2pool(p2pool_bin, OUT / "p2pool.toml")
        progress("starting kth")
        start_kth(image, src, binary, solo, identity)
        pubkey = authority_key()
        EVIDENCE["kth_authority_public_key"] = pubkey
        if not port_open(SV2_PORT):
            fail(f"kth SV2 port {SV2_PORT} is not accepting connections")

        if row == "25":
            row25(device_bin, pubkey, solo)
        else:
            row25b(device_bin, pubkey, solo)
    record_p2pool_api()
    EVIDENCE["heights"]["bchn_after"] = int(cli("getblockcount"))
    EVIDENCE["heights"]["kth_after"] = kth_height()
    EVIDENCE["ok"] = True
    EVIDENCE["error"] = None


def main() -> None:
    if len(sys.argv) > 2:
        raise SystemExit("usage: jd_p2pool_e2e.py <output-directory>")
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
        try:
            attach_logs()
        except Exception as error:
            EVIDENCE["log_error"] = f"{type(error).__name__}: {error}"
        write_evidence()
        print(f"evidence: {OUT / 'jd-p2pool-e2e.json'}", flush=True)


if __name__ == "__main__":
    main()
