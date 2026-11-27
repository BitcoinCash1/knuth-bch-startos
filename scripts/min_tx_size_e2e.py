#!/usr/bin/env python3
"""End-to-end: the minimum transaction size in blocks, kth against BCHN 29.2.0.

The rule: a transaction of a block, the coinbase included, has at least 65
bytes from Upgrade 9 (kth: Descartes) and 100 bytes from Magnetic Anomaly
(kth: Euclid) until then. BCHN 29.2.0 applies it in ContextualCheckBlock
(bad-txns-undersize); its regtest has both upgrades from the first block, so
the minimum there is 65 bytes.

BCHN regtest mines 120 blocks to P2SH(OP_TRUE). kth syncs them from BCHN and
is stopped, and its data directory is kept: every phase below starts kth from
that copy, at the same tip as BCHN, with no peer but the one the phase uses.
Every block is built here on that tip:

- a block whose coinbase is 64 bytes;
- a block with an ordinary 64-byte transaction, a spend of the coinbase of
  block 1 (P2SH(OP_TRUE), unlocked by pushing OP_TRUE) into one output of no
  value locked by OP_RETURN OP_0;
- the same two at 65 bytes, as controls.

Each 64-byte block goes to kth and to BCHN twice, as two blocks with different
timestamps: once by submitblock, and once from a peer made here. kth dials
that peer as its one outbound peer, in the place BCHN holds in the sync; the
peer dials BCHN. The peer announces the block with a headers message, answers
getheaders with it, and sends the block when the node asks for it (getdata),
or unasked after a while. A 65-byte coinbase block goes to kth from the same
peer, to show that path connecting a block. Last, the 65-byte coinbase block
and then the 65-byte transaction block on it go to both nodes by submitblock,
and both must connect them.

KTH_EXPECT says which kth this is: "before" (k-nuth/kth master) must connect
the 64-byte blocks, "after" (the change) must refuse them, by submitblock with
bad-txns-undersize. BCHN must refuse them in both rows.

Standard library only. Writes <out>/min_tx_size.json and <out>/min_tx_size.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BCHN = ("127.0.0.1", 18443)
BCHN_P2P = 18444
KTH = ("127.0.0.1", 19332)
KTH_P2P = 18555
PEER_PORT = 18666
AUTH = "e2e"
RPC_BASIC = base64.b64encode(b"e2e:e2e").decode()
CONTAINER = "kth-min-tx-size"
BCHN_DIR = Path("/tmp/bchn-min-tx-size")
DATA = Path("/tmp/kth-min-tx-size-data")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out").resolve()
CHAIN_LENGTH = 120
MAGIC = bytes.fromhex("dab5bffa")   # regtest
EVIDENCE: dict = {"ok": False, "error": "started", "expect": None, "phases": [], "kth_commit": None}
_next_id = 0


def fail(message: str) -> NoReturn:
    EVIDENCE["ok"] = False
    EVIDENCE["error"] = message
    raise SystemExit(message)


def progress(message: str) -> None:
    print(message, flush=True)


# --- RPC ---------------------------------------------------------------------

def post(node, body: bytes, timeout: float = 120) -> dict:
    request = Request(f"http://{node[0]}:{node[1]}/", body,
                      {"Authorization": f"Basic {RPC_BASIC}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read() or b""
    except (URLError, OSError) as error:
        return {"status": None, "transport": f"{type(error).__name__}: {error}", "json": None, "raw": b""}
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return {"status": status, "json": parsed, "raw": raw}


def call(node, method: str, *params, timeout: float = 120):
    global _next_id
    _next_id += 1
    reply = post(node, json.dumps({"jsonrpc": "1.0", "id": _next_id, "method": method,
                                   "params": list(params)}).encode(), timeout)
    parsed = reply["json"]
    if isinstance(parsed, dict):
        return parsed.get("result"), parsed.get("error")
    return None, {"transport": reply.get("transport"), "status": reply.get("status"),
                  "raw": (reply.get("raw") or b"")[:200].decode("utf-8", "replace")}


def must(node, method: str, *params, timeout: float = 120):
    result, error = call(node, method, *params, timeout=timeout)
    if error is not None:
        fail(f"{'kth' if node == KTH else 'BCHN'} {method} failed: {error}")
    return result


def rpc_wait(node, name: str, seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        result, error = call(node, "getblockcount", timeout=10)
        if error is None and result is not None:
            return
        last = error
        time.sleep(1)
    fail(f"{name} never answered getblockcount within {seconds}s: {last}")


def submit(node, raw: bytes):
    """submitblock's answer: its result (None is null), or its error."""
    result, error = call(node, "submitblock", raw.hex())
    if error is not None:
        return {"error": error}
    return result


def tip(node) -> str | None:
    return call(node, "getbestblockhash")[0]


# --- Serialization ------------------------------------------------------------

def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def varint(value: int) -> bytes:
    if value < 0xFD:
        return value.to_bytes(1, "little")
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    return b"\xfe" + value.to_bytes(4, "little")


def push(data: bytes) -> bytes:
    assert len(data) < 0x4C
    return bytes([len(data)]) + data


def script_number(value: int) -> bytes:
    out = bytearray()
    while value:
        out.append(value & 0xFF)
        value >>= 8
    if out and out[-1] & 0x80:
        out.append(0)
    return bytes(out)


OP_0, OP_TRUE, OP_RETURN = 0x00, 0x51, 0x6A
TRUE_SCRIPT = bytes([OP_TRUE])
# P2SH(OP_TRUE), read from BCHN once it runs: hashlib's RIPEMD-160 is missing
# where OpenSSL 3 leaves out its legacy provider.
P2SH_TRUE = b""


def tx(version: int, inputs: list[tuple[bytes, int, bytes]], outputs: list[tuple[int, bytes]],
       locktime: int = 0) -> bytes:
    raw = version.to_bytes(4, "little") + varint(len(inputs))
    for prev, index, script_sig in inputs:
        raw += prev + index.to_bytes(4, "little") + varint(len(script_sig)) + script_sig
        raw += (0xFFFFFFFF).to_bytes(4, "little")
    raw += varint(len(outputs))
    for value, script in outputs:
        raw += value.to_bytes(8, "little") + varint(len(script)) + script
    return raw + locktime.to_bytes(4, "little")


def txid(raw: bytes) -> bytes:
    """Internal byte order."""
    return sha256d(raw)


def coinbase(height: int, value: int) -> bytes:
    """An ordinary coinbase, well over the minimum."""
    script_sig = push(script_number(height)) + push(b"kth min tx size e2e")
    return tx(1, [(b"\x00" * 32, 0xFFFFFFFF, script_sig)], [(value, P2SH_TRUE)])


def sized_coinbase(height: int, value: int, size: int) -> bytes:
    """A coinbase of exactly `size` bytes: the height, then a push of zeros,
    in its input script, and its value locked to OP_TRUE."""
    height_push = push(script_number(height))
    padding = size - len(tx(1, [(b"\x00" * 32, 0xFFFFFFFF, height_push)], [(value, TRUE_SCRIPT)]))
    assert padding >= 0, (size, padding)
    script_sig = height_push + (push(b"\x00" * (padding - 1)) if padding else b"")
    raw = tx(1, [(b"\x00" * 32, 0xFFFFFFFF, script_sig)], [(value, TRUE_SCRIPT)])
    assert len(raw) == size, (size, len(raw))
    return raw


def sized_spend(prev_txid_display: str, index: int, size: int) -> bytes:
    """A spend of a P2SH(OP_TRUE) output of exactly `size` bytes: OP_TRUE
    pushed in the input script, and one output of no value locked by
    OP_RETURN and padding of OP_0. The whole input is the fee."""
    prev = bytes.fromhex(prev_txid_display)[::-1]
    script_sig = push(TRUE_SCRIPT)
    pad = size - len(tx(2, [(prev, index, script_sig)], [(0, b"")]))
    assert pad >= 1, (size, pad)
    raw = tx(2, [(prev, index, script_sig)], [(0, bytes([OP_RETURN]) + bytes([OP_0]) * (pad - 1))])
    assert len(raw) == size, (size, len(raw))
    return raw


def merkle_root(hashes: list[bytes]) -> bytes:
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def mined_block(prev_display: str, timestamp: int, bits: int, txs: list[bytes]) -> bytes:
    """A block on `prev_display`, its nonce ground to `bits` (regtest: a few tries)."""
    root = merkle_root([txid(raw) for raw in txs])
    exponent, mantissa = bits >> 24, bits & 0xFFFFFF
    target = mantissa << (8 * (exponent - 3))
    head = ((0x20000000).to_bytes(4, "little") + bytes.fromhex(prev_display)[::-1] + root
            + timestamp.to_bytes(4, "little") + bits.to_bytes(4, "little"))
    for nonce in range(1 << 24):
        header = head + nonce.to_bytes(4, "little")
        if int.from_bytes(sha256d(header), "little") <= target:
            return header + varint(len(txs)) + b"".join(txs)
    fail("no nonce met the regtest target")


def block_hash(raw: bytes) -> str:
    return sha256d(raw[:80])[::-1].hex()


# --- A peer ---------------------------------------------------------------------

def p2p_message(command: bytes, payload: bytes) -> bytes:
    return (MAGIC + command.ljust(12, b"\0") + struct.pack("<I", len(payload))
            + sha256d(payload)[:4] + payload)


class Peer:
    """A P2P connection to a node that announces one block with a headers
    message and sends it when asked. It answers pings, answers getheaders
    with the block's header, and records what the node sent."""

    def __init__(self, sock: socket.socket, port: int, block: bytes, start_height: int):
        self.block = block
        self.hash = sha256d(block[:80])
        self.events: list[str] = []
        self.buffer = b""
        self.closed = False
        self.version_sent = False
        self.sock = sock
        address = (struct.pack("<Q", 1) + bytes(10) + b"\xff\xff" + socket.inet_aton("127.0.0.1")
                   + struct.pack(">H", port))
        self.version = (struct.pack("<iQq", 70016, 1, int(time.time())) + address + address
                        + struct.pack("<Q", int.from_bytes(os.urandom(8), "little"))
                        + varint(len(b"/min-tx-size-e2e/")) + b"/min-tx-size-e2e/"
                        + struct.pack("<i", start_height) + b"\x01")

    @classmethod
    def dial(cls, port: int, block: bytes, start_height: int) -> "Peer":
        """The peer connects to the node and speaks first."""
        peer = cls(socket.create_connection(("127.0.0.1", port), timeout=30), port, block, start_height)
        peer.send(b"version", peer.version)
        peer.version_sent = True
        peer.events.append(f"dialed 127.0.0.1:{port}")
        return peer

    @classmethod
    def accept(cls, listener: socket.socket, block: bytes, start_height: int, seconds: float):
        """The node connects to the peer, which answers its version."""
        listener.settimeout(seconds)
        try:
            sock, remote = listener.accept()
        except socket.timeout:
            return None
        peer = cls(sock, PEER_PORT, block, start_height)
        peer.events.append(f"accepted the node's connection from {remote[0]}:{remote[1]}")
        return peer

    def send(self, command: bytes, payload: bytes) -> None:
        self.sock.sendall(p2p_message(command, payload))

    def read(self, timeout: float):
        deadline = time.monotonic() + timeout
        while True:
            if len(self.buffer) >= 24:
                if self.buffer[:4] != MAGIC:
                    self.events.append("bad magic")
                    self.closed = True
                    return None
                size, = struct.unpack("<I", self.buffer[16:20])
                if len(self.buffer) >= 24 + size:
                    command = self.buffer[4:16].rstrip(b"\0")
                    payload = self.buffer[24:24 + size]
                    self.buffer = self.buffer[24 + size:]
                    return command, payload
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.closed:
                return None
            self.sock.settimeout(min(remaining, 1.0))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as error:
                self.events.append(f"connection error: {error}")
                self.closed = True
                return None
            if not chunk:
                self.events.append("disconnected by the node")
                self.closed = True
                return None
            self.buffer += chunk

    def handshake(self, seconds: float = 30) -> bool:
        got_version = got_verack = False
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not (got_version and got_verack):
            message = self.read(deadline - time.monotonic())
            if message is None:
                break
            command, payload = message
            if command == b"version":
                got_version = True
                if not self.version_sent:
                    self.send(b"version", self.version)
                    self.version_sent = True
                self.send(b"verack", b"")
            elif command == b"verack":
                got_verack = True
            elif command == b"ping":
                self.send(b"pong", payload)
        self.events.append(f"handshake {'done' if got_version and got_verack else 'not done'}")
        return got_version and got_verack

    def announce_and_serve(self, seconds: float, unasked_after: float) -> dict:
        """Announce the block, then answer the node for at most `seconds`, and
        15 s more once the block is sent: a node that refuses it may drop the
        connection, which is worth recording."""
        header_message = varint(1) + self.block[:80] + b"\x00"
        self.send(b"headers", header_message)
        self.events.append("sent headers")
        asked = sent = False
        sent_at = 0.0
        started = time.monotonic()
        while (time.monotonic() - started < seconds and not self.closed
               and (not sent or time.monotonic() - sent_at < 15)):
            message = self.read(1.0)
            if message is None:
                if not sent and not self.closed and time.monotonic() - started > unasked_after:
                    self.send(b"block", self.block)
                    self.events.append("sent the block unasked")
                    sent, sent_at = True, time.monotonic()
                continue
            command, payload = message
            if command == b"ping":
                self.send(b"pong", payload)
            elif command == b"getheaders":
                self.send(b"headers", header_message)
                self.events.append("answered getheaders")
            elif command == b"getdata":
                count = payload[0]
                wanted = [payload[1 + 36 * i:1 + 36 * (i + 1)] for i in range(count)] if count < 0xFD else []
                for entry in wanted:
                    kind, = struct.unpack("<I", entry[:4])
                    if entry[4:] == self.hash and kind in (2, 4) and not sent:
                        asked = True
                        self.send(b"block", self.block)
                        self.events.append(f"asked for the block (getdata type {kind}), sent it")
                        sent, sent_at = True, time.monotonic()
        return {"asked": asked, "sent": sent}

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --- Nodes --------------------------------------------------------------------

def start_bchn() -> None:
    if shutil.which("bitcoind") is None:
        fail("missing bitcoind on PATH (the workflow fetches BCHN 29.2.0)")
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    started = subprocess.run(
        ["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
         f"-rpcuser={AUTH}", f"-rpcpassword={AUTH}", f"-rpcport={BCHN[1]}", f"-port={BCHN_P2P}",
         "-listen=1", "-disablewallet=1", "-debug=net", "-debug=validation"],
        capture_output=True, text=True, timeout=30)
    if started.returncode != 0:
        fail(f"bitcoind failed to start: {(started.stderr or started.stdout).strip()}")
    rpc_wait(BCHN, "BCHN")


def kth_paths() -> tuple[str, Path, str]:
    image = os.environ.get("KTH_IMAGE", "").strip()
    src = Path(os.environ.get("KTH_SRC", "").strip() or ".")
    binary = os.environ.get("KTH_BIN", "").strip()
    if not image or not binary:
        fail("KTH_IMAGE and KTH_BIN must be set")
    return image, src, binary


def start_kth(phase: str, peer: int | None) -> None:
    """kth on /data/e2e, logging to files named after the phase. Its one
    outbound peer is 127.0.0.1:`peer`, or none; it listens for none. It runs
    in a directory of the phase's own, where it keeps peers.dat and
    banlist.dat: a peer remembered or banned in one phase is not dialed or
    refused in the next (kth holds one connection per address, and every
    peer here is 127.0.0.1)."""
    image, src, binary = kth_paths()
    relative = Path(binary).resolve().relative_to(src.resolve())
    workdir = f"/data/cwd-{phase}"
    argv = [
        "--init_run", "--network", "regtest", "--node.display", "daemon",
        "--db.directory", "/data/e2e", "--db.db_mode", "full",
        "--net.inbound_port", str(KTH_P2P), "--net.inbound_connections", "0",
        "--net.outbound_connections", "1" if peer else "0",
        *(["--net.peer", f"127.0.0.1:{peer}"] if peer else []),
        "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", str(KTH[1]),
        "--rpc.user", AUTH, "--rpc.password", AUTH,
        "--log.debug_file", f"/data/kth-debug-{phase}.log", "--log.error_file", f"/data/kth-error-{phase}.log",
    ]
    EVIDENCE.setdefault("kth_argv", {})[phase] = argv
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=60)
    result = subprocess.run(
        ["docker", "run", "--detach", "--name", CONTAINER, "--network", "host", "--platform", "linux/amd64",
         "-v", f"{src.resolve()}:/src", "-v", f"{DATA}:/data", "-w", workdir,
         "--entrypoint", "/src/" + relative.as_posix(), image, *argv],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        fail(f"docker run of kth failed: {(result.stderr or result.stdout).strip()}")
    rpc_wait(KTH, f"kth ({phase})")


def stop_kth(phase: str) -> None:
    subprocess.run(["docker", "stop", "-t", "60", CONTAINER], capture_output=True, timeout=120)
    try:
        logs = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True, timeout=30)
        (OUT / f"kth-docker-{phase}.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        pass
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=60)


def restore_snapshot() -> None:
    """The kth data directory as it was when kth stopped at BCHN's tip."""
    subprocess.run(["sudo", "rm", "-rf", str(DATA / "e2e")], check=True, timeout=120)
    subprocess.run(["sudo", "cp", "-a", str(DATA / "snapshot"), str(DATA / "e2e")], check=True, timeout=300)


def wait_tip(node, wanted: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if tip(node) == wanted:
            return True
        time.sleep(1)
    return tip(node) == wanted


def kth_log_lines(phase: str, needles: tuple[str, ...], limit: int = 12) -> list[str]:
    lines: list[str] = []
    for name in (f"kth-debug-{phase}.log", f"kth-error-{phase}.log"):
        try:
            text = subprocess.run(["sudo", "cat", str(DATA / name)], capture_output=True, text=True,
                                  timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in text.splitlines():
            if any(needle in line for needle in needles):
                lines.append(line.strip()[:300])
    return lines[-limit:]


def header_known(node, hash_display: str):
    result, error = call(node, "getblockheader", hash_display)
    if error is not None:
        return {"error": error}
    return {"height": result.get("height"), "confirmations": result.get("confirmations")}


def bchn_tip_status(hash_display: str) -> str | None:
    for entry in must(BCHN, "getchaintips"):
        if entry.get("hash") == hash_display:
            return entry.get("status")
    return None


def cleanup() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=60)
    if shutil.which("bitcoin-cli"):
        subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", f"-rpcuser={AUTH}",
                        f"-rpcpassword={AUTH}", f"-rpcport={BCHN[1]}", "stop"], capture_output=True, timeout=30)
    OUT.mkdir(parents=True, exist_ok=True)
    for path in sorted(DATA.glob("kth-*.log")):
        subprocess.run(["sudo", "cp", str(path), str(OUT / path.name)], capture_output=True, timeout=30)
    subprocess.run(["sudo", "chmod", "-R", "a+rw", str(OUT)], capture_output=True, timeout=30)
    if (BCHN_DIR / "regtest" / "debug.log").is_file():
        shutil.copy(BCHN_DIR / "regtest" / "debug.log", OUT / "bchn-debug.log")


# --- The phases -------------------------------------------------------------------

KTH_REFUSAL_NEEDLES = ("under the minimum size", "undersize", "rejected", "refus")


def run() -> None:
    expect = os.environ.get("KTH_EXPECT", "").strip()
    if expect not in ("before", "after"):
        fail("KTH_EXPECT must be 'before' or 'after'")
    EVIDENCE["expect"] = expect
    OUT.mkdir(parents=True, exist_ok=True)
    commit = Path("kth-commit.txt")
    if commit.is_file():
        EVIDENCE["kth_commit"] = commit.read_text(encoding="utf-8").strip()

    start_bchn()
    global P2SH_TRUE
    address = must(BCHN, "decodescript", TRUE_SCRIPT.hex())["p2sh"]
    P2SH_TRUE = bytes.fromhex(must(BCHN, "validateaddress", address)["scriptPubKey"])
    progress(f"mining {CHAIN_LENGTH} blocks to P2SH(OP_TRUE) {address}")
    must(BCHN, "generatetoaddress", CHAIN_LENGTH, address, timeout=600)

    # kth syncs from BCHN once; its stopped data directory is every phase's start.
    subprocess.run(["sudo", "rm", "-rf", str(DATA)], capture_output=True, timeout=120)
    DATA.mkdir(parents=True, exist_ok=True)
    start_kth("sync", peer=BCHN_P2P)
    shared = must(BCHN, "getbestblockhash")
    if not wait_tip(KTH, shared, 600):
        fail(f"kth did not reach BCHN's tip {shared} within 600s")
    stop_kth("sync")
    subprocess.run(["sudo", "cp", "-a", str(DATA / "e2e"), str(DATA / "snapshot")], check=True, timeout=300)

    header = must(BCHN, "getblockheader", shared)
    height = header["height"]
    bits = int(header["bits"], 16)
    # generatetoaddress stamps a block at MTP + 1 when it mines faster than one
    # a second, so the median time past of this tip can run ahead of the clock.
    now = max(int(time.time()), header["mediantime"] + 1)
    new_height = height + 1
    subsidy = (50 * 100_000_000) >> (new_height // 150)
    first = must(BCHN, "getblock", must(BCHN, "getblockhash", 1), 2)["tx"][0]
    first_txid, first_value = first["txid"], round(first["vout"][0]["value"] * 100_000_000)
    EVIDENCE["chain"] = {"tip": shared, "height": height, "bits": header["bits"], "spent": f"{first_txid}:0"}

    def coinbase_block(size: int, stamp: int) -> bytes:
        return mined_block(shared, stamp, bits, [sized_coinbase(new_height, subsidy, size)])

    def spend_block(size: int, stamp: int, parent: str, at: int) -> bytes:
        value = ((50 * 100_000_000) >> (at // 150)) + first_value
        return mined_block(parent, stamp, bits, [coinbase(at, value), sized_spend(first_txid, 0, size)])

    blocks = {
        "64-byte coinbase, submitblock": coinbase_block(64, now),
        "64-byte transaction, submitblock": spend_block(64, now + 1, shared, new_height),
        "64-byte coinbase, peer": coinbase_block(64, now + 2),
        "64-byte transaction, peer": spend_block(64, now + 3, shared, new_height),
        "65-byte coinbase, peer": coinbase_block(65, now + 4),
        "65-byte coinbase, submitblock": coinbase_block(65, now + 5),
    }
    blocks["65-byte transaction, submitblock"] = spend_block(
        65, now + 6, block_hash(blocks["65-byte coinbase, submitblock"]), new_height + 1)
    EVIDENCE["blocks"] = {name: {"hash": block_hash(raw), "bytes": len(raw)} for name, raw in blocks.items()}

    phases = []

    def by_submitblock(name: str) -> None:
        raw = blocks[name]
        hash_display = block_hash(raw)
        phase = "rpc-" + name.split(",")[0].replace(" ", "-")
        restore_snapshot()
        start_kth(phase, peer=None)
        if tip(KTH) != shared:
            fail(f"kth did not start at the shared tip for '{name}'")
        kth_answer = submit(KTH, raw)
        time.sleep(2)
        row = {"block": name, "hash": hash_display, "delivery": "submitblock",
               "kth": {"submitblock": kth_answer, "tip_is_block": tip(KTH) == hash_display,
                       "tip": tip(KTH), "getblockheader": header_known(KTH, hash_display),
                       "log": kth_log_lines(phase, KTH_REFUSAL_NEEDLES)}}
        bchn_answer = submit(BCHN, raw)
        row["bchn"] = {"submitblock": bchn_answer, "tip_is_block": tip(BCHN) == hash_display,
                       "tip": tip(BCHN), "chaintip_status": bchn_tip_status(hash_display)}
        stop_kth(phase)
        phases.append(row)
        progress(json.dumps(row))

    def by_peer(name: str, to_bchn: bool = True) -> None:
        raw = blocks[name]
        hash_display = block_hash(raw)
        phase = "peer-" + name.split(",")[0].replace(" ", "-")
        restore_snapshot()
        listener = socket.create_server(("127.0.0.1", PEER_PORT))
        try:
            start_kth(phase, peer=PEER_PORT)
            if tip(KTH) != shared:
                fail(f"kth did not start at the shared tip for '{name}'")
            peer = Peer.accept(listener, raw, new_height, 120)
        finally:
            listener.close()
        if peer is None:
            fail(f"kth never dialed the peer for '{name}'")
        handshake = peer.handshake()
        served = (peer.announce_and_serve(seconds=90, unasked_after=45)
                  if handshake else {"asked": False, "sent": False})
        connected = wait_tip(KTH, hash_display, 30)
        peer.close()
        row = {"block": name, "hash": hash_display, "delivery": "peer",
               "kth": {"peer": served, "peer_events": peer.events, "tip_is_block": connected,
                       "tip": tip(KTH), "getblockheader": header_known(KTH, hash_display),
                       "log": kth_log_lines(phase, KTH_REFUSAL_NEEDLES)}}
        if to_bchn:
            bchn_peer = Peer.dial(BCHN_P2P, raw, new_height)
            bchn_handshake = bchn_peer.handshake()
            bchn_served = (bchn_peer.announce_and_serve(seconds=40, unasked_after=15)
                           if bchn_handshake else {"asked": False, "sent": False})
            time.sleep(2)
            bchn_peer.close()
            row["bchn"] = {"peer": bchn_served, "peer_events": bchn_peer.events,
                           "tip_is_block": tip(BCHN) == hash_display, "tip": tip(BCHN),
                           "chaintip_status": bchn_tip_status(hash_display)}
        stop_kth(phase)
        phases.append(row)
        progress(json.dumps(row))

    by_submitblock("64-byte coinbase, submitblock")
    by_submitblock("64-byte transaction, submitblock")
    by_peer("64-byte coinbase, peer")
    by_peer("64-byte transaction, peer")
    by_peer("65-byte coinbase, peer", to_bchn=False)

    # The controls at 65 bytes, by submitblock to both, one on the other.
    restore_snapshot()
    start_kth("rpc-controls", peer=None)
    for name in ("65-byte coinbase, submitblock", "65-byte transaction, submitblock"):
        raw = blocks[name]
        hash_display = block_hash(raw)
        kth_answer = submit(KTH, raw)
        bchn_answer = submit(BCHN, raw)
        row = {"block": name, "hash": hash_display, "delivery": "submitblock",
               "kth": {"submitblock": kth_answer, "tip_is_block": wait_tip(KTH, hash_display, 30)},
               "bchn": {"submitblock": bchn_answer, "tip_is_block": wait_tip(BCHN, hash_display, 30)}}
        phases.append(row)
        progress(json.dumps(row))
    stop_kth("rpc-controls")
    EVIDENCE["phases"] = phases

    # What each row must show.
    problems = []
    for row in phases:
        name = row["block"]
        kth = row["kth"]
        bchn = row.get("bchn")
        if name.startswith("64-byte"):
            if bchn is not None:
                if bchn["tip_is_block"]:
                    problems.append(f"BCHN connected '{name}'")
                if row["delivery"] == "submitblock" and bchn["submitblock"] != "bad-txns-undersize":
                    problems.append(f"BCHN answered '{name}' with {bchn['submitblock']!r}")
                if row["delivery"] == "peer" and not bchn["peer"]["sent"]:
                    problems.append(f"the peer never sent '{name}' to BCHN")
            if row["delivery"] == "peer" and not kth["peer"]["sent"]:
                problems.append(f"the peer never sent '{name}' to kth")
            if expect == "before":
                if not kth["tip_is_block"]:
                    problems.append(f"kth (before) did not connect '{name}'")
                if row["delivery"] == "submitblock" and kth["submitblock"] is not None:
                    problems.append(f"kth (before) answered '{name}' with {kth['submitblock']!r}")
            else:
                if kth["tip_is_block"]:
                    problems.append(f"kth (after) connected '{name}'")
                if row["delivery"] == "submitblock" and kth["submitblock"] != "bad-txns-undersize":
                    problems.append(f"kth (after) answered '{name}' with {kth['submitblock']!r}")
        else:
            if not kth["tip_is_block"]:
                problems.append(f"kth did not connect the control '{name}'")
            if row["delivery"] == "submitblock" and kth["submitblock"] is not None:
                problems.append(f"kth answered the control '{name}' with {kth['submitblock']!r}")
            if bchn is not None and (not bchn["tip_is_block"] or bchn["submitblock"] not in (None, "duplicate")):
                problems.append(f"BCHN did not connect the control '{name}': {bchn}")

    def verdict(side, delivery: str) -> str:
        if side is None:
            return "(not sent)"
        state = "connected" if side["tip_is_block"] else "not connected"
        if delivery == "submitblock":
            return f"`{json.dumps(side['submitblock'])}`, {state}"
        how = "asked for it (getdata)" if side["peer"]["asked"] else "sent unasked"
        return f"{how}, {state}"

    lines = [f"kth {expect}: {EVIDENCE.get('kth_commit')}", "",
             "| Block | Delivery | kth | BCHN 29.2.0 |", "|---|---|---|---|"]
    for row in phases:
        lines.append(f"| {row['block'].split(',')[0]} | {row['delivery']} | "
                     f"{verdict(row['kth'], row['delivery'])} | {verdict(row.get('bchn'), row['delivery'])} |")
    (OUT / "min_tx_size.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)

    if problems:
        fail("; ".join(problems))
    EVIDENCE["ok"] = True
    EVIDENCE["error"] = None


def main() -> None:
    try:
        run()
    finally:
        try:
            cleanup()
        finally:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "min_tx_size.json").write_text(json.dumps(EVIDENCE, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
