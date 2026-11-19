#!/usr/bin/env python3
"""TEST ONLY. End-to-end check of kth's own JSON-RPC, with no proxy, against
the node calls Fulcrum, BCH Explorer, ASICSeer and EloPool make.

A BCHN regtest node mines a chain with wallet transactions; kth (built from the
package Dockerfile) syncs it over P2P. Every call then goes to both nodes, so a
kth failure is shown next to what BCHN answers for the very same request. The
transport checks send each client's exact request bytes and read the reply the
way that client's own code does.

Writes <out>/results.json and <out>/results.md, and prints the table.
Standard library only.
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
import os
import re
import select
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-out")
BCHN_DIR = Path("/tmp/bchn-regtest")
BCHN = ("127.0.0.1", 18443)
KTH = ("127.0.0.1", 19332)
NODES = {"kth": KTH, "bchn": BCHN}
AUTH = base64.b64encode(b"e2e:e2e").decode()
# Branches with the Stratum V2 Template Provider (row 21) set KTH_SV2=1; the
# option does not exist on the others, where kth would refuse to start.
SV2 = os.environ.get("KTH_SV2") == "1"
SV2_PORT = 18442
# Branches with outbound SOCKS5 (#803) set KTH_PROXY=1: kth then reaches BCHN only
# through the relay below, which records what it was asked to connect to.
PROXY = os.environ.get("KTH_PROXY") == "1"
PROXY_PORT = 19050
# Branches with SHV set KTH_SHV=1: kth keeps its MMR index and takes inbound P2P
# connections, so the light client below can ask it for proofs.
SHV = os.environ.get("KTH_SHV") == "1"
# KTH_FILTERS=1 (compact filters served to light wallets): kth runs with
# node.compact_filters and takes inbound P2P for the light client below.
FILTERS = os.environ.get("KTH_FILTERS") == "1"
# KTH_SCRIPT_FILTERS=1, with KTH_FILTERS=1: the branch serves the script
# filter (filter type 1) too, with its range and proof messages.
SCRIPT_FILTERS = FILTERS and os.environ.get("KTH_SCRIPT_FILTERS") == "1"
# KTH_RPA_FILTERS=1, with KTH_FILTERS=1: the branch serves the input prefix
# filter (filter type 2) for wallets of reusable payment addresses (row 1010).
RPA_FILTERS = FILTERS and os.environ.get("KTH_RPA_FILTERS") == "1"
# The first filter type the branch does not serve.
UNSERVED_TYPE = b"\x03" if RPA_FILTERS else b"\x02" if SCRIPT_FILTERS else b"\x01"
# KTH_BLOOM=1 (bloom filters, BIP37): kth runs with node.bloom_filters and
# takes inbound P2P; the same light client asks BCHN too.
BLOOM = os.environ.get("KTH_BLOOM") == "1"
# KTH_NEUTRINO_BIN, with KTH_FILTERS=1: the built scripts/neutrino-client, a
# wallet's node connection made with the gcash/neutrino library itself.
NEUTRINO_BIN = os.environ.get("KTH_NEUTRINO_BIN") or ""
# KTH_EXPLORER_BACKEND, with KTH_SCRIPT_FILTERS=1: the backend directory of a
# built BCH Explorer (CyberAshven/bitcoin-cash-explorer, feat/p2p-light-client),
# whose 'p2p' backend reads addresses from kth as a light client (row 1008,
# scripts/explorer-p2p-e2e.js).
EXPLORER_BACKEND = os.environ.get("KTH_EXPLORER_BACKEND") or ""
KTH_P2P_PORT = 18555
# KTH_ELECTRUM_BRIDGE and KTH_FULCRUM, with KTH_SCRIPT_FILTERS=1 and
# KTH_RPA_FILTERS=1 (row 1011): a built bch-electrum-bridge, which answers
# Electrum wallets as a P2P light client of kth, and a Fulcrum release, which
# indexes BCHN over its RPC, get the same Electrum calls.
ELECTRUM_BRIDGE = os.environ.get("KTH_ELECTRUM_BRIDGE") or ""
FULCRUM = os.environ.get("KTH_FULCRUM") or ""
FULCRUM_PORT = 50011
BRIDGE_PORTS = (50012, 50013)
# KTH_STALL=1 (#773 row 27): kth reaches BCHN only through a relay that never
# delivers kth's first request for blocks, so that request times out while the
# peer stays connected.
STALL = os.environ.get("KTH_STALL") == "1"
STALL_PORT = 19444
# KTH_LONG=<n> (#773 row 27): BCHN mines n more blocks before kth starts, for a
# long block download.
LONG = int(os.environ.get("KTH_LONG") or 0)
# KTH_NOISE=1 with KTH_SV2=1 (#773 row 22): kth's Template Provider speaks only
# Noise, and the row 21 and 23 clients reach it through scripts/sv2-noise-bridge,
# whose Noise side is the Stratum Reference Implementation's own crates.
NOISE = os.environ.get("KTH_NOISE") == "1"
NOISE_BRIDGE = os.environ.get("KTH_NOISE_BRIDGE") or "scripts/sv2-noise-bridge/target/release/sv2-noise-bridge"
NOISE_BRIDGE_PORT = 18452
SV2_CLIENT_PORT = NOISE_BRIDGE_PORT if NOISE else SV2_PORT
SV2_TRANSPORT = "Noise, SRI crates" if NOISE else "plaintext"
# The sv2-spec 4.7.1 test vector: a valid authority key that is not kth's.
OTHER_AUTHORITY = "9bXiEd8boQVhq7WddEcERUL5tyyJVFYdU8th3HfbNXK3Yw6GRXh"
# KTH_I2P=1 (I2P through SAM, stacked on #803): an i2pd router runs beside the
# nodes and kth takes I2P peers through its SAM bridge. A second kth whose only
# peer is the first one's I2P address must sync the chain through the router.
I2P = os.environ.get("KTH_I2P") == "1"
I2PD_IMAGE = "purplei2p/i2pd:release-2.61.0"
SAM_PORT = 7656
KTH_B = ("127.0.0.1", 19342)
# KTH_SERVE=1 (kth answers peers' getheaders and getdata): a second kth whose
# only peer is the first one must sync the chain from it.
SERVE = os.environ.get("KTH_SERVE") == "1"
# KTH_ANNOUNCE=1 (kth announces new blocks to its peers, k-nuth/kth#812): a BCHN
# whose only peer is kth must hear of each new block from kth.
ANNOUNCE = os.environ.get("KTH_ANNOUNCE") == "1"
# KTH_COMPACT=1 (compact blocks, BIP152, k-nuth/kth#812 item 2): blocks must move
# between BCHN and kth as cmpctblock in both directions, with no full block.
COMPACT = os.environ.get("KTH_COMPACT") == "1"
# KTH_SV2_MINING=1 (#773 row 24, the Mining Protocol server): SRI's own CPU
# mining_device mines on kth over Noise, and BCHN must accept its blocks.
SV2_MINING = os.environ.get("KTH_SV2_MINING") == "1"
# KTH_LONGPOLL=1 (k-nuth/kth#874): a getblocktemplate that sends its template's
# longpollid back is held until BCHN mines a block. It mines one more block, so
# it runs last and only when asked.
LONGPOLL = os.environ.get("KTH_LONGPOLL") == "1"
MINING_DEVICE = os.environ.get("KTH_MINING_DEVICE") or "sv2-apps/target/release/mining_device"
MINING_ADDRESS = ""
BCHN_B_DIR = Path("/tmp/bchn-b-regtest")
BCHN_B = ("127.0.0.1", 18453)
RESULTS: dict = {"setup": {}, "methods": [], "transport": [], "mining": [], "rows": []}


# ---- plain JSON-RPC ---------------------------------------------------------

def post(node, body: bytes, timeout: float = 60) -> dict:
    request = Request(f"http://{node[0]}:{node[1]}/", body,
                      {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"})
    started = time.monotonic()
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
    return {"status": status, "raw": raw, "json": parsed,
            "ms": round((time.monotonic() - started) * 1000)}


_next_id = 0


def call(node, method: str, *params):
    """The body every app here sends: no "jsonrpc" key, integer id."""
    global _next_id
    _next_id += 1
    reply = post(node, json.dumps({"id": _next_id, "method": method, "params": list(params)}).encode())
    j = reply["json"]
    if isinstance(j, dict):
        return j.get("result"), j.get("error")
    return None, {"transport": reply.get("transport"), "status": reply["status"],
                  "raw": reply["raw"][:200].decode("utf-8", "replace")}


def cli(*args) -> str:
    return subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_DIR}", "-rpcuser=e2e",
                           "-rpcpassword=e2e", "-rpcport=18443", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


# ---- nodes ------------------------------------------------------------------

def wait_for(node, name: str, seconds: int = 180) -> None:
    for _ in range(seconds):
        result, error = call(node, "getblockchaininfo")
        if error is None and result:
            return
        time.sleep(1)
    raise SystemExit(f"{name} never answered getblockchaininfo")


def start_bchn() -> None:
    BCHN_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bitcoind", "-regtest", f"-datadir={BCHN_DIR}", "-daemon", "-server",
                    "-rpcuser=e2e", "-rpcpassword=e2e", "-rpcport=18443", "-port=18444",
                    "-listen=1", "-txindex=1", "-fallbackfee=0.0001", "-allowunconnectedmining",
                    "-zmqpubhashblock=tcp://127.0.0.1:28332"], check=True)
    wait_for(BCHN, "BCHN")


def start_kth() -> None:
    subprocess.run(["docker", "run", "--detach", "--name", "kth", "--network", "host",
                    "--platform", "linux/amd64", "--entrypoint", "/usr/local/bin/kth", "knuth-e2e",
                    "--init_run", "--network", "regtest", "--node.display", "daemon",
                    "--db.directory", "/data/e2e", "--db.db_mode", "full",
                    "--net.inbound_port", str(KTH_P2P_PORT),
                    "--net.inbound_connections",
                    "4" if SHV or I2P or SERVE or ANNOUNCE or COMPACT or FILTERS or BLOOM else "0",
                    *(["--node.mmr_index", "true"] if SHV else []),
                    *(["--node.compact_filters", "true"] if FILTERS else []),
                    *(["--node.bloom_filters", "true"] if BLOOM else []),
                    "--net.outbound_connections", "1",
                    "--net.peer", (f"{BCHN_I2P.address}:0" if I2P
                                   else f"127.0.0.1:{STALL_PORT if STALL else 18444}"),
                    "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", "19332",
                    "--rpc.user", "e2e", "--rpc.password", "e2e",
                    "--log.debug_file", "/data/kth-debug.log", "--log.error_file", "/data/kth-error.log",
                    *(["--sv2.enabled", "true", "--sv2.bind", "127.0.0.1", "--sv2.port", str(SV2_PORT)]
                      if SV2 else []),
                    *(["--sv2.mining.enabled", "true", "--sv2.mining.address", MINING_ADDRESS]
                      if SV2 and SV2_MINING else []),
                    *(["--sv2.noise", "true", "--sv2.static_key_file", "/data/sv2-static.key",
                       "--sv2.authority_key_file", "/data/sv2-authority.key"] if SV2 and NOISE else []),
                    *(["--net.proxy", f"127.0.0.1:{PROXY_PORT}"] if PROXY else []),
                    *(["--net.i2psam", f"127.0.0.1:{SAM_PORT}", "--net.i2p_accept_incoming", "true",
                       "--net.i2p_private_key_file", "/data/i2p_private_key"] if I2P else [])],
                   check=True)
    wait_for(KTH, "kth")


class Socks5Relay:
    """TEST ONLY. A minimal SOCKS5 server (RFC 1928, with RFC 1929 username and
    password) that relays to the requested destination and records each request:
    what kth asked for, the credentials it sent, and the local port of the relay's
    own connection onward, which is the port BCHN sees for that peer."""

    def __init__(self, port: int):
        self.port, self.requests, self.lock = port, [], threading.Lock()
        self.server = socket.create_server(("127.0.0.1", port))

    def start(self) -> None:
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    @staticmethod
    def _read(sock: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def _serve(self, client: socket.socket) -> None:
        record: dict = {}
        try:
            version, count = self._read(client, 2)
            methods = list(self._read(client, count))
            record["offered"] = methods
            method = 0x02 if 0x02 in methods else 0x00
            client.sendall(bytes([5, method]))
            if method == 0x02:
                self._read(client, 1)
                user = self._read(client, self._read(client, 1)[0])
                self._read(client, self._read(client, 1)[0])
                record["user"] = user.decode(errors="replace")
                client.sendall(b"\x01\x00")
            _, command, _, kind = self._read(client, 4)
            if kind == 1:
                host = socket.inet_ntoa(self._read(client, 4))
            elif kind == 3:
                host = self._read(client, self._read(client, 1)[0]).decode()
            else:
                host = socket.inet_ntop(socket.AF_INET6, self._read(client, 16))
            port = struct.unpack(">H", self._read(client, 2))[0]
            record.update({"command": command, "address_type": kind, "host": host, "port": port})
            upstream = socket.create_connection((host, port), timeout=10)
            upstream.settimeout(None)
            record["relay_port"] = upstream.getsockname()[1]
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        except (OSError, ValueError, ConnectionError) as error:
            record["error"] = str(error)
            client.close()
            with self.lock:
                self.requests.append(record)
            return
        with self.lock:
            self.requests.append(record)

        def pipe(source: socket.socket, sink: socket.socket) -> None:
            try:
                while chunk := source.recv(65536):
                    sink.sendall(chunk)
            except OSError:
                pass
            for s in (source, sink):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


RELAY: Socks5Relay | None = None


class DropFirstBlockRequest:
    """TEST ONLY. A TCP relay from kth to BCHN's P2P port that forwards every
    message except kth's first getdata for blocks, which it swallows and records.
    BCHN never sees that request, so kth's wait for those blocks times out while
    the connection itself stays healthy: pings and everything else still pass."""

    def __init__(self, port: int, upstream: tuple):
        self.upstream, self.dropped, self.lock = upstream, None, threading.Lock()
        self.server = socket.create_server(("127.0.0.1", port))

    def start(self) -> None:
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
                upstream = socket.create_connection(self.upstream, timeout=10)
                upstream.settimeout(None)
            except OSError:
                return
            threading.Thread(target=self._filter, args=(client, upstream), daemon=True).start()
            threading.Thread(target=self._pipe, args=(upstream, client), daemon=True).start()

    @staticmethod
    def _close(*socks: socket.socket) -> None:
        for s in socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _pipe(self, source: socket.socket, sink: socket.socket) -> None:
        try:
            while chunk := source.recv(65536):
                sink.sendall(chunk)
        except OSError:
            pass
        self._close(source, sink)

    def _filter(self, source: socket.socket, sink: socket.socket) -> None:
        """kth to BCHN, one whole message at a time."""
        buffer = b""
        try:
            while chunk := source.recv(65536):
                buffer += chunk
                while len(buffer) >= 24:
                    size, = struct.unpack("<I", buffer[16:20])
                    if len(buffer) < 24 + size:
                        break
                    message, buffer = buffer[:24 + size], buffer[24 + size:]
                    if not self._swallow(message):
                        sink.sendall(message)
        except OSError:
            pass
        self._close(source, sink)

    def _swallow(self, message: bytes) -> bool:
        if message[4:16].rstrip(b"\0") != b"getdata":
            return False
        payload = message[24:]
        count, o = read_compact(payload, 0)
        kinds = {struct.unpack("<I", payload[o + 36 * i:o + 36 * i + 4])[0] for i in range(count)}
        if not kinds & {2, 3, 4}:  # block, filtered block, compact block
            return False
        with self.lock:
            if self.dropped is not None:
                return False
            self.dropped = {"blocks asked for": count, "at": time.monotonic()}
        return True


STALLER: DropFirstBlockRequest | None = None
BRIDGE: subprocess.Popen | None = None
NOISE_KEYS: dict = {}


def sam_line(sock: socket.socket) -> str:
    data = b""
    while not data.endswith(b"\n"):
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("the SAM bridge closed the socket")
        data += byte
    return data.decode(errors="replace").strip()


def sam_ask(sock: socket.socket, command: str) -> dict:
    sock.sendall((command + "\n").encode())
    reply = sam_line(sock)
    fields = dict(part.split("=", 1) for part in reply.split()[2:] if "=" in part)
    fields["reply"] = reply[:200]
    return fields


def sam_hello(timeout: float) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", SAM_PORT), timeout=timeout)
    if sam_ask(sock, "HELLO VERSION MIN=3.1 MAX=3.1").get("RESULT") != "OK":
        raise ConnectionError("SAM HELLO refused")
    return sock


def i2p_b32(destination_base64: str) -> str:
    destination = base64.b64decode(destination_base64.replace("-", "+").replace("~", "/"))
    return base64.b32encode(hashlib.sha256(destination).digest()).decode().lower().rstrip("=") + ".b32.i2p"


class SamService:
    """TEST ONLY. Publishes BCHN's P2P port as an I2P destination through
    i2pd's SAM bridge, written here from the SAM v3.1 specification so it shares
    no code with kth's SAM client. Every I2P stream it accepts is relayed to
    BCHN, and the local port of that relay connection is recorded: it is the
    port BCHN sees for the peer."""

    def __init__(self, target: tuple[str, int]):
        self.target, self.address, self.accepted, self.relay_ports = target, None, [], []
        self.id = "bchn" + os.urandom(4).hex()
        self.control: socket.socket | None = None

    def start(self) -> None:
        # The router opens its SAM port a little after its container starts.
        deadline = time.monotonic() + 180
        while self.control is None:
            try:
                # A new router builds its first tunnels before the session is ready.
                self.control = sam_hello(600)
            except ConnectionRefusedError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
        keys = sam_ask(self.control, "DEST GENERATE SIGNATURE_TYPE=7")
        status = sam_ask(self.control, f"SESSION CREATE STYLE=STREAM ID={self.id} DESTINATION={keys['PRIV']} "
                                       "SIGNATURE_TYPE=7 i2cp.leaseSetEncType=4,0")
        if status.get("RESULT") != "OK":
            raise ConnectionError(f"SESSION CREATE: {status['reply']}")
        self.address = i2p_b32(keys["PUB"])
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                stream = sam_hello(30)
                status = sam_ask(stream, f"STREAM ACCEPT ID={self.id} SILENT=false")
                if status.get("RESULT") != "OK":
                    stream.close()
                    time.sleep(2)
                    continue
                stream.settimeout(None)
                caller = sam_line(stream)  # the caller's destination, then the stream
                upstream = socket.create_connection(self.target, timeout=10)
                upstream.settimeout(None)
                self.accepted.append(i2p_b32(caller.split()[0]))
                self.relay_ports.append(upstream.getsockname()[1])
                for source, sink in ((stream, upstream), (upstream, stream)):
                    threading.Thread(target=self._pipe, args=(source, sink), daemon=True).start()
            except (OSError, ValueError):
                time.sleep(2)

    @staticmethod
    def _pipe(source: socket.socket, sink: socket.socket) -> None:
        try:
            while chunk := source.recv(65536):
                sink.sendall(chunk)
        except OSError:
            pass
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


BCHN_I2P: SamService | None = None


def start_noise_bridge() -> None:
    """kth prints its authority key when the Noise listener starts. The bridge
    pins that printed key, as an operator gives it to a pool or Job Declarator;
    key-utils' own encoding of the authority secret's public key is kept to
    compare with it."""
    global BRIDGE
    log, secret = Path("/tmp/kth-debug-noise.log"), Path("/tmp/sv2-authority.key")
    for _ in range(60):
        subprocess.run(["docker", "cp", "kth:/data/kth-debug.log", str(log)], capture_output=True)
        found = re.findall(r"Noise authority public key: (\w+)", log.read_text(errors="replace")
                           if log.exists() else "")
        if found:
            NOISE_KEYS["printed by kth"] = found[-1]
            break
        time.sleep(1)
    if subprocess.run(["docker", "cp", "kth:/data/sv2-authority.key", str(secret)],
                      capture_output=True).returncode == 0:
        NOISE_KEYS["key-utils, from the secret file"] = subprocess.run(
            [NOISE_BRIDGE, "--public-key-of", str(secret)], capture_output=True, text=True).stdout.strip()
    RESULTS["setup"]["noise_keys"] = NOISE_KEYS
    if "printed by kth" not in NOISE_KEYS:
        return
    BRIDGE = subprocess.Popen([NOISE_BRIDGE, "--listen", f"127.0.0.1:{NOISE_BRIDGE_PORT}",
                               "--connect", f"127.0.0.1:{SV2_PORT}",
                               "--authority", NOISE_KEYS["printed by kth"]],
                              stdout=open(OUT / "noise-bridge.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(1)


def kth_log(lines: int = 40) -> str:
    out = subprocess.run(["docker", "logs", "--tail", str(lines), "kth"], capture_output=True, text=True)
    return out.stdout + out.stderr


def wait_kth_height(height: int, seconds: int) -> bool:
    for _ in range(seconds):
        info, error = call(KTH, "getblockchaininfo")
        if error is None and info and info.get("blocks", -1) >= height:
            return True
        time.sleep(1)
    return False


# ---- block building (from the knuth-mining-probe branch) --------------------

def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def push(data: bytes) -> bytes:
    return bytes([len(data)]) + data


def script_number(n: int) -> bytes:
    out = bytearray()
    while n:
        out.append(n & 0xFF)
        n >>= 8
    if out and out[-1] & 0x80:
        out.append(0)
    return bytes(out) or b"\x00"


def coinbase(height: int, value: int, tag: bytes) -> bytes:
    script_sig = push(script_number(height)) + push(tag.ljust(40, b"."))
    out_script = b"\x51"  # OP_TRUE
    return (struct.pack("<i", 2) + b"\x01" + b"\x00" * 32 + b"\xff\xff\xff\xff"
            + bytes([len(script_sig)]) + script_sig + b"\xff\xff\xff\xff"
            + b"\x01" + struct.pack("<q", value) + bytes([len(out_script)]) + out_script
            + struct.pack("<I", 0))


def fold_branch(coinbase_hash: bytes, branch: list[str]) -> bytes:
    """The block's Merkle root from its coinbase hash and a template's
    `merkle` branch (hashes in display order)."""
    root = coinbase_hash
    for sibling in branch:
        root = sha256d(root + bytes.fromhex(sibling)[::-1])
    return root


def merkle_branch(txids: list[str]) -> list[str]:
    """The coinbase's Merkle branch over transactions in block order (txids in
    display order), as getblocktemplatelight gives it."""
    level = [b""] + [bytes.fromhex(t)[::-1] for t in txids]
    branch = []
    while len(level) > 1:
        branch.append(level[1][::-1].hex())
        if len(level) % 2:
            level.append(level[-1])
        level = [b""] + [sha256d(level[i] + level[i + 1]) for i in range(2, len(level), 2)]
    return branch


def solve_coinbase_only(template: dict, tag: bytes, branch: list[str] | None = None) -> tuple[str, str, str]:
    """Coinbase-only block on a template: (block hex, block hash, coinbase hex).
    The coinbase claims the bare regtest subsidy: the template's coinbasevalue
    may include fees of transactions this block leaves out. With a light
    template's `branch`, the root covers the transactions the node adds to the
    block under its job id, so submitblocklight takes it."""
    subsidy = (50 * 100_000_000) >> (template["height"] // 150)
    base = coinbase(template["height"], subsidy, tag)
    root = fold_branch(sha256d(base), branch or [])
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template.get("mintime", 0))
    for nonce in range(1 << 32):
        header = (struct.pack("<I", template["version"]) + prev + root
                  + struct.pack("<III", when, bits, nonce))
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    return (header + b"\x01" + base).hex(), sha256d(header)[::-1].hex(), base.hex()


# ---- chain setup ------------------------------------------------------------

def wallet_address() -> str:
    try:
        return cli("getnewaddress")
    except subprocess.CalledProcessError:
        cli("createwallet", "e2e")  # no wallet loaded yet; BCHN may load a default one
        return cli("getnewaddress")


def make_chain() -> dict:
    """BCHN only: a mined chain with a block of wallet transactions."""
    addr = wallet_address()
    cli("generatetoaddress", "110", addr)
    if LONG:
        # After the wallet's own coinbases: regtest halves the subsidy every 150
        # blocks, so these pay nothing. To a script the wallet does not own, so
        # its coin selection stays small.
        elsewhere = cashaddr_retype(addr, 1)
        for left in range(LONG, 0, -1000):
            cli("generatetoaddress", str(min(left, 1000)), elsewhere)
    others = [cli("getnewaddress") for _ in range(3)]
    confirmed = [cli("sendtoaddress", a, "1.5") for a in others]
    tx_block = json.loads(cli("generatetoaddress", "1", addr))[0]
    cli("generatetoaddress", "2", addr)
    block = json.loads(cli("getblock", tx_block, "1"))
    return {"addr": addr, "others": others, "height": int(cli("getblockcount")),
            "tx_block": tx_block, "tx_block_height": block["height"],
            "coinbase_txid": block["tx"][0], "confirmed_txid": confirmed[0], "confirmed": confirmed}


def make_mempool(s: dict) -> dict:
    """BCHN only: two relayed mempool transactions and two signed, unsent ones.
    One output of the confirmed transaction is locked first so gettxout has an
    unspent output to look up."""
    s["unspent_n"] = next(n for n in range(4)
                          if cli("gettxout", s["confirmed_txid"], str(n)) not in ("", "null"))
    cli("lockunspent", "false", json.dumps([{"txid": s["confirmed_txid"], "vout": s["unspent_n"]}]))
    s["mempool_txids"] = [cli("sendtoaddress", a, "0.25") for a in s["others"][:2]]

    def fresh_tx() -> str:
        utxo = next(u for u in json.loads(cli("listunspent", "1"))
                    if u["amount"] >= 1 and u["txid"] not in s["confirmed"])
        cli("lockunspent", "false", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]))
        raw = cli("createrawtransaction",
                  json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]),
                  json.dumps({cli("getnewaddress"): round(utxo["amount"] - 0.001, 8)}))
        return json.loads(cli("signrawtransactionwithwallet", raw))["hex"]

    s["fresh_tx_send"], s["fresh_tx_test"], s["fresh_tx_row5"] = fresh_tx(), fresh_tx(), fresh_tx()
    s["fresh_tx_row4"] = fresh_tx()
    s["fresh_tx_row11"] = fresh_tx()
    s["fresh_tx_pr874"] = fresh_tx()
    s["tip"] = cli("getbestblockhash")
    return s


def setup() -> dict:
    global RELAY, STALLER, BCHN_I2P
    if I2P:
        # Started first: a new router needs minutes to reseed and build tunnels.
        subprocess.run(["docker", "run", "--detach", "--name", "i2pd", "--network", "host", I2PD_IMAGE,
                        "--sam.enabled=true", "--sam.address=127.0.0.1", f"--sam.port={SAM_PORT}",
                        "--http.enabled=false", "--loglevel=warn"], check=True)
    start_bchn()
    s = make_chain()
    if I2P:
        BCHN_I2P = SamService(("127.0.0.1", 18444))
        BCHN_I2P.start()
        RESULTS["setup"]["bchn_i2p_address"] = BCHN_I2P.address
    if PROXY:
        RELAY = Socks5Relay(PROXY_PORT)
        RELAY.start()
    if STALL:
        STALLER = DropFirstBlockRequest(STALL_PORT, ("127.0.0.1", 18444))
        STALLER.start()
    global MINING_ADDRESS
    if SV2_MINING:
        MINING_ADDRESS = wallet_address()
        RESULTS["setup"]["sv2_mining_address"] = MINING_ADDRESS
    started = time.monotonic()
    start_kth()
    if SV2 and NOISE:
        start_noise_bridge()
    if STALL:
        row27_request_timeout(s)
    synced = wait_kth_height(s["height"], 900)
    RESULTS["setup"]["kth_synced_bchn_chain"] = synced
    RESULTS["setup"]["kth_sync_seconds"] = round(time.monotonic() - started)
    RESULTS["setup"]["bchn_height"] = s["height"]
    if LONG:
        row(27, "node operators (initial block download)",
            f"block download of {s['height']} blocks reaches BCHN's tip with no restart", synced,
            {"blocks": s["height"], "seconds": RESULTS["setup"]["kth_sync_seconds"],
             "kth height": call(KTH, "getblockcount")[0]})
    s = make_mempool(s)
    time.sleep(15)  # time for kth to hear about the relayed transactions
    return s


def row27_request_timeout(s: dict) -> None:
    """Block download goes on after a request for blocks times out while the
    peer stays connected (#773 row 27). The relay swallows kth's first request;
    kth's wait for those blocks times out after 60 seconds, and then kth has to
    ask again by itself: no block is mined and no peer comes or goes meanwhile."""
    client = "node operators (initial block download)"
    check = "block download reaches BCHN's tip after kth's first block request times out, with no restart"
    deadline = time.monotonic() + 180
    while STALLER.dropped is None and time.monotonic() < deadline:
        time.sleep(1)
    if STALLER.dropped is None:
        row(27, client, check, False, {"error": "kth asked BCHN for no blocks within 180 seconds"})
        return
    seen: dict = {"tip": s["height"], "blocks asked for in the dropped request": STALLER.dropped["blocks asked for"]}
    # 60 seconds of request timeout, then time to ask again and download.
    reached = wait_kth_height(s["height"], 240)
    seen["reached the tip"] = reached
    seen["seconds from the drop"] = round(time.monotonic() - STALLER.dropped["at"])
    seen["kth height"] = call(KTH, "getblockcount")[0]
    seen["BCHN peers (the relay)"] = len(json.loads(cli("getpeerinfo")))
    row(27, client, check, reached, seen)
    if not reached:
        # What an operator does today: a restart recreates the download workers,
        # so the rest of the checks still run on a synced kth.
        subprocess.run(["docker", "restart", "-t", "15", "kth"], capture_output=True)
        wait_for(KTH, "kth")


# ---- method matrix ------------------------------------------------------------

def method_check(method, params, apps, *, needed=(), same=None, tolerated_codes=(), label=None,
                 bchn_params=None):
    """Call both nodes. Verdict: ok, GAP, or n/a (BCHN fails the same call).
    bchn_params: the form the app sends to BCHN when it differs (Fulcrum picks
    the estimatefee form by node name)."""
    kr, ke = call(KTH, method, *params)
    br, be = call(BCHN, method, *(params if bchn_params is None else bchn_params))
    row = {"call": label or f"{method} {json.dumps(params)[:70]}", "apps": apps,
           "kth": brief(kr, ke), "bchn": brief(br, be)}
    if be is not None:
        row["verdict"] = "n/a: BCHN fails this call too"
    elif ke is not None:
        code = ke.get("code") if isinstance(ke, dict) else None
        tolerated = code in tolerated_codes
        row["verdict"] = "GAP (kth errors; the app tolerates this code)" if tolerated else "GAP"
    else:
        problems = []
        if needed and isinstance(kr, dict):
            missing = [f for f in needed if f not in kr]
            if missing:
                problems.append(f"missing fields the apps read: {missing}")
        if isinstance(kr, dict) and isinstance(br, dict):
            extra = sorted(set(br) - set(kr) - set(needed))
            if extra:
                row["fields_only_bchn_has"] = extra
        if same is not None:
            note = same(kr, br)
            if note:
                problems.append(note)
        row["verdict"] = "GAP (" + "; ".join(problems) + ")" if problems else "ok"
    RESULTS["methods"].append(row)
    return kr, br


def brief(result, error, limit: int = 160):
    if error is not None:
        return {"error": error}
    text = json.dumps(result)
    return {"result": text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"}


def eq(kr, br):
    return None if kr == br else "differs from BCHN"


def methods(s: dict) -> None:
    tip, blk, txid = s["tip"], s["tx_block"], s["confirmed_txid"]

    def chaininfo_same(k, b):
        notes = []
        for f in ("blocks", "bestblockhash", "chain"):
            if k.get(f) != b.get(f):
                notes.append(f"{f} {k.get(f)!r} vs BCHN {b.get(f)!r}")
        if k.get("initialblockdownload") is not False:
            notes.append(f"initialblockdownload is {k.get('initialblockdownload')!r} at the tip "
                         "(Fulcrum never opens its port while it is true)")
        if k.get("headers") != k.get("blocks"):
            notes.append("headers != blocks (the Explorer indexer waits for equality)")
        return "; ".join(notes) or None

    method_check("help", ["help"], "Fulcrum! (auth check every connect)")
    method_check("uptime", [], "Fulcrum probe")
    method_check("getnetworkinfo", [], "Fulcrum!, Explorer, pool stats",
                 needed=("subversion", "version", "relayfee", "warnings"))
    method_check("getblockchaininfo", [], "Fulcrum!, Explorer!, ASICSeer!, EloPool",
                 needed=("blocks", "chain", "bestblockhash", "headers", "initialblockdownload",
                         "difficulty", "mediantime", "verificationprogress", "chainwork",
                         "size_on_disk", "pruned", "warnings"), same=chaininfo_same)
    method_check("getblockcount", [], "Explorer!, pool health", same=eq)
    method_check("getbestblockhash", [], "Explorer", same=eq)
    method_check("getblockhash", [0], "Fulcrum!", same=eq)
    method_check("getblockhash", [s["tx_block_height"]], "Fulcrum!, Explorer!, pools", same=eq)
    method_check("getblock", [blk, False], "Fulcrum! (raw block)", same=eq)
    method_check("getblock", [blk, 0], "Explorer route", same=eq)
    method_check("getblock", [blk], "Explorer!",
                 needed=("hash", "height", "version", "time", "bits", "nonce", "difficulty",
                         "merkleroot", "nTx", "size", "previousblockhash", "mediantime",
                         "confirmations"),
                 same=lambda k, b: None if k.get("tx") == b.get("tx") else "tx list differs")
    method_check("getblock", [blk, 1], "Explorer indexer",
                 same=lambda k, b: None if k.get("tx") == b.get("tx") else "tx list differs")

    def verbose2_same(k, b):
        kt, bt = k.get("tx") or [], b.get("tx") or []
        if [t.get("txid") for t in kt] != [t.get("txid") for t in bt]:
            return "tx list differs"
        if any("fee" not in t for t in kt[1:]):
            return "tx[].fee missing (Explorer reads it)"
        if [t.get("fee") for t in kt[1:]] != [t.get("fee") for t in bt[1:]]:
            return "tx[].fee differs from BCHN's"
        if "fee" in kt[0]:
            return "the coinbase has a fee field"
        return None

    method_check("getblock", [blk, 2], "Explorer!", same=verbose2_same)
    def patterns_same(k, b):
        """Every scriptSig's and scriptPubKey's byteCodePattern, as BCHN's;
        then the verbosity 2 checks, reported after the patterns."""
        def patterns(block):
            found = []
            for t in block.get("tx") or []:
                for i in t.get("vin") or []:
                    if "scriptSig" in i:
                        found.append(("vin", t.get("txid"), i["scriptSig"].get("byteCodePattern")))
                for o in t.get("vout") or []:
                    found.append(("vout", t.get("txid"), (o.get("scriptPubKey") or {}).get("byteCodePattern")))
            return found
        kp, bp = patterns(k), patterns(b)
        if len(kp) != len(bp):
            return f"{len(kp)} scripts against {len(bp)}"
        # patternAsm is script text, which kth writes in its own notation in
        # every asm field; the other fields are compared with BCHN's.
        def bytes_only(p):
            return {f: v for f, v in (p or {}).items() if f != "patternAsm"} if p is not None else None
        for (side, txid, mine), (_, _, theirs) in zip(kp, bp):
            if bytes_only(mine) != bytes_only(theirs) or (mine is None) != (theirs is None):
                return f"{side} of {txid[:12]}: {json.dumps(mine)[:160]} vs {json.dumps(theirs)[:160]}"
        rest = verbose2_same(k, b)
        same = f"fingerprint, pattern, data and error equal to BCHN's in all {len(kp)} scripts"
        return f"{same}; {rest}" if rest else None

    method_check("getblock", [blk, 2, True], "Explorer reorg/route", same=patterns_same)
    method_check("getblockheader", [blk, False], "Explorer!", same=eq)
    method_check("getblockheader", [blk, True], "Fulcrum client, package health",
                 needed=("confirmations", "height", "time"))
    method_check("getblockstats", [blk], "Explorer! (unless -32601)", tolerated_codes=(-32601,))
    method_check("getrawtransaction", [txid, False], "Fulcrum!", same=eq)
    method_check("getrawtransaction", [s["coinbase_txid"], False], "Fulcrum txindex check", same=eq)

    def verbose_tx_same(k, b):
        if k.get("hex") != b.get("hex"):
            return "hex differs"
        spk = ((k.get("vout") or [{}])[0]).get("scriptPubKey") or {}
        missing = [f for f in ("hex", "asm", "type", "addresses") if f not in spk]
        return f"vout[0].scriptPubKey lacks {missing} (Explorer reads them)" if missing else None

    method_check("getrawtransaction", [txid, True], "Explorer!, Fulcrum client",
                 needed=("txid", "vin", "vout", "confirmations", "blockhash", "blocktime"),
                 same=verbose_tx_same)
    method_check("getrawtransaction", [s["mempool_txids"][0], False], "Fulcrum! (mempool tx)", same=eq)

    def bchn_fields_equal(k, b, path=""):
        """Every field BCHN gives, kth gives with the same value; asm and a byte
        code pattern's patternAsm aside (kth spells opcodes its own way, as the
        getblock patterns check above also allows) and kth's own extra fields
        allowed. The first difference, or None."""
        if isinstance(b, dict):
            if not isinstance(k, dict):
                return f"{path or '.'} is not an object"
            for key, value in b.items():
                if key in ("asm", "patternAsm"):
                    continue
                if key not in k:
                    return f"{path}.{key} missing"
                note = bchn_fields_equal(k[key], value, f"{path}.{key}")
                if note:
                    return note
            return None
        if isinstance(b, list):
            if not isinstance(k, list) or len(k) != len(b):
                return f"{path} has {len(k) if isinstance(k, list) else 'no'} entries, BCHN {len(b)}"
            for i, (kv, bv) in enumerate(zip(k, b)):
                note = bchn_fields_equal(kv, bv, f"{path}[{i}]")
                if note:
                    return note
            return None
        return None if k == b else f"{path}: kth {str(k)[:40]}, BCHN {str(b)[:40]}"

    # Verbosity 2 (prevouts and fee) and patterns, the Explorer's form with the
    # block hash its Electrum history gives, and for a mempool transaction.
    method_check("getrawtransaction", [txid, 2, blk, True], "Explorer (block hash, patterns)",
                 same=bchn_fields_equal)
    method_check("getrawtransaction", [txid, 2, blk], "Explorer (block hash)", same=bchn_fields_equal)
    method_check("getrawtransaction", [s["mempool_txids"][0], 2, "", True], "Explorer! (mempool tx)",
                 same=bchn_fields_equal)
    # Every statistic of the block of wallet transactions and of the tip, value
    # for value, by hash and by height.
    tip_height = call(BCHN, "getblockcount")[0]
    method_check("getblockstats", [blk], "Explorer (values)", same=bchn_fields_equal,
                 label=f"getblockstats [tx block] values")
    method_check("getblockstats", [tip_height], "Explorer (values, by height)", same=bchn_fields_equal,
                 label=f"getblockstats [{tip_height}] values")

    def mempool_same(k, b):
        missing = sorted(set(b) - set(k)) if isinstance(b, list) else None
        return f"kth mempool lacks {len(missing)} of BCHN's {len(b)} txs" if missing else None

    method_check("getrawmempool", [False], "Fulcrum!", same=mempool_same)
    method_check("getrawmempool", [], "Explorer!", same=mempool_same)
    method_check("getrawmempool", [True], "Explorer")
    method_check("getmempoolentry", [s["mempool_txids"][0]], "Explorer (tolerated)")
    method_check("getmempoolinfo", [], "Fulcrum client, Explorer, pool stats")
    method_check("getzmqnotifications", [], "Fulcrum (optional, else polls)")
    method_check("getdsprooflist", [0], "Fulcrum (optional)")
    method_check("estimatefee", [6], "Fulcrum client (sends [n] to non-BCHN nodes)", bchn_params=[],
                 label="estimatefee [6] to kth, [] to BCHN")
    method_check("validateaddress", [s["addr"]], "ASICSeer!, Explorer route, Fulcrum admin",
                 needed=("isvalid", "address", "scriptPubKey"),
                 same=lambda k, b: None if k.get("isvalid") is True else "isvalid is not true")
    def txout_same(k, b):
        # kth adds its own confirmations_ticks, and the two nodes spell a
        # script's asm differently; every other field BCHN gives is the same.
        extra = sorted(set(k) - set(b) - {"confirmations_ticks"})
        if extra:
            return f"fields BCHN does not have: {extra}"

        def plain(script):
            return {key: value for key, value in (script or {}).items() if key != "asm"}

        differing = sorted(key for key, value in b.items()
                           if (plain(k.get(key)) != plain(value) if key == "scriptPubKey" else k.get(key) != value))
        return f"differs from BCHN in {differing}" if differing else None

    method_check("gettxout", [txid, s["unspent_n"]], "Explorer route", same=txout_same)
    method_check("gettxout", [txid, s["unspent_n"], False], "Explorer route", same=txout_same)
    method_check("testmempoolaccept", [[s["fresh_tx_test"]]], "Explorer route")
    method_check("getindexinfo", [], "Explorer indexer! (unless -32601)", tolerated_codes=(-32601,))
    method_check("getchaintips", [], "Explorer (tolerated)")
    method_check("getnetworkhashps", [1, s["tx_block_height"]], "Explorer indexer!")
    method_check("getnetworkhashps", [1, 1], "Explorer indexer!")
    method_check("getnetworkhashps", [1008], "Explorer route")
    method_check("getdifficulty", [], "Explorer (tolerated)")
    method_check("getmininginfo", [], "pool stats")
    gbt_fields = ("previousblockhash", "target", "version", "curtime", "bits", "height",
                  "coinbasevalue", "transactions")
    method_check("getblocktemplate",
                 [{"capabilities": ["coinbasetxn", "workid", "coinbase/append"], "rules": []}],
                 "ASICSeer!", needed=gbt_fields, label="getblocktemplate (ASICSeer request)")
    method_check("getblocktemplate",
                 [{"capabilities": ["coinbasetxn", "workid", "coinbase/append"]}],
                 "EloPool!", needed=gbt_fields, label="getblocktemplate (EloPool request)")
    method_check("preciousblock", [tip], "pools (result ignored)")


# ---- client transports ----------------------------------------------------------

class LineReader:
    """Port of ckpool's read_socket_line: returns (len, line) with the '\\n'
    stripped, len 0 on timeout without a whole line, -1 on close/error."""

    def __init__(self, sock: socket.socket):
        self.sock, self.buf = sock, b""

    def read_line(self, timeout: float) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return 0, self.buf
            ready, _, _ = select.select([self.sock], [], [], left)
            if not ready:
                return 0, self.buf
            chunk = self.sock.recv(65536)
            if not chunk:
                return -1, self.buf
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return len(line), line

    def read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self.buf) < n and time.monotonic() < deadline:
            ready, _, _ = select.select([self.sock], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def ckpool_request(node, body: bytes) -> bytes:
    return (f"POST / HTTP/1.1\nAuthorization: Basic {AUTH}\nHost: {node[0]}:{node[1]}\n"
            f"Content-type: application/json\nContent-Length: {len(body)}\n\n").encode() + body


def elopool_exchange(node, body: bytes, timeout: float = 15) -> tuple[bool, str]:
    """EloPool's json_rpc_call read path (ckpool.c): status line must start
    'HTTP/1.1 200 OK', then lines are read until one starts with '{'."""
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(ckpool_request(node, body))
        r = LineReader(s)
        n, line = r.read_line(timeout)
        if n < 1:
            return False, f"no status line (read_socket_line returned {n})"
        if not line[:15].lower() == b"http/1.1 200 ok":
            return False, f"status line not ok: {line[:60]!r}"
        while True:
            n, line = r.read_line(timeout)
            if n < 1:
                return False, (f"no JSON line: read_socket_line returned {n} "
                               f"({'timeout' if n == 0 else 'closed'}) with {len(line)} unread bytes "
                               f"{line[:60]!r}")
            if line.startswith(b"{"):
                break
        try:
            json.loads(line)
        except ValueError as e:
            return False, f"JSON line does not parse: {e}"
        return True, "body read as one JSON line"


def asicseer_exchange(node, body: bytes, timeout: float = 15) -> tuple[bool, str]:
    """ASICSeer's json_rpc_call read path: status 'HTTP/1.1 200 OK', a
    'Content-Length: ' line, then the very next line must have length 1 (the
    '\\r' of a CRLF blank line), then exactly Content-Length bytes."""
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(ckpool_request(node, body))
        r = LineReader(s)
        n, line = r.read_line(timeout)
        if n < 1 or line[:15].lower() != b"http/1.1 200 ok":
            return False, f"status line not ok: {line[:60]!r} (len {n})"
        length = -1
        while length < 0:
            n, line = r.read_line(timeout)
            if n < 1:
                return False, f"no Content-Length line (read_socket_line returned {n})"
            if line[:16].lower() == b"content-length: ":
                length = int(line[16:].strip())
                n, blank = r.read_line(timeout)
                if n != 1:
                    return False, (f"line after Content-Length is {blank!r} (len {n}), "
                                   "ASICSeer needs the blank line right after it")
        data = r.read_exact(length, timeout)
        try:
            json.loads(data)
        except ValueError as e:
            return False, f"body does not parse: {e}"
        return True, "body read by Content-Length"


def http_responses(data: bytes) -> list[dict]:
    """Split a byte stream into HTTP/1.1 responses framed by Content-Length."""
    out = []
    while data:
        head, sep, rest = data.partition(b"\r\n\r\n")
        if not sep:
            break
        lines = head.split(b"\r\n")
        headers = {}
        for h in lines[1:]:
            k, _, v = h.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
        length = int(headers.get("content-length", "-1"))
        if length < 0 or len(rest) < length:
            out.append({"status_line": lines[0].decode(), "headers": headers, "body": rest, "short": True})
            break
        out.append({"status_line": lines[0].decode(), "headers": headers, "body": rest[:length]})
        data = rest[length:]
    return out


def fulcrum_exchange(node, timeout: float = 15) -> tuple[bool, str]:
    """Fulcrum's HttpConnection: CRLF headers, Content-Type application/json-rpc,
    body with a CRLF suffix counted in Content-Length, three requests pipelined
    on one keep-alive connection. Each reply must be HTTP/1.1, carry
    Content-Type and Content-Length, and echo the integer id with a result key
    (or an error with code and message)."""
    reqs = [("help", ["help"]), ("getblockchaininfo", []), ("getnetworkinfo", [])]
    payload = b""
    for i, (m, p) in enumerate(reqs, 1):
        body = json.dumps({"id": i, "method": m, "params": p}).encode() + b"\r\n"
        payload += (f"POST / HTTP/1.1\r\nHost: {node[0]}:{node[1]}\r\n"
                    f"Content-Type: application/json-rpc\r\nAuthorization: Basic {AUTH}\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(payload)
        data, deadline = b"", time.monotonic() + timeout
        while len(http_responses(data)) < len(reqs) and time.monotonic() < deadline:
            ready, _, _ = select.select([s], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    replies = http_responses(data)
    problems = []
    if len(replies) < len(reqs):
        problems.append(f"{len(replies)} of {len(reqs)} pipelined replies came back")
    for i, rep in enumerate(replies, 1):
        if not rep["status_line"].startswith("HTTP/1.1"):
            problems.append(f"reply {i} status line {rep['status_line']!r}")
        for h in ("content-type", "content-length"):
            if h not in rep["headers"]:
                problems.append(f"reply {i} has no {h} header")
        try:
            j = json.loads(rep["body"])
        except ValueError:
            problems.append(f"reply {i} body is not JSON")
            continue
        if not isinstance(j, dict) or j.get("id") != i or not isinstance(j.get("id"), int):
            problems.append(f"reply {i} id is {j.get('id') if isinstance(j, dict) else j!r}")
        elif j.get("error") is None and "result" not in j:
            problems.append(f"reply {i} has no result key")
        elif j.get("error") is not None and not ({"code", "message"} <= set(j["error"])):
            problems.append(f"reply {i} error lacks code/message")
    return (not problems), ("; ".join(problems) or "3 pipelined replies framed as Fulcrum expects")


def explorer_exchange(node, timeout: float = 15) -> tuple[bool, str]:
    """BCH Explorer (Node http, agent:false): Host and Content-Length only, no
    Content-Type; one connection per request."""
    body = json.dumps({"method": "getblockcount", "params": [], "id": int(time.time() * 1000)}).encode()
    req = (f"POST / HTTP/1.1\r\nHost: {node[0]}:{node[1]}\r\nAuthorization: Basic {AUTH}\r\n"
           f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
    with socket.create_connection(node, timeout=timeout) as s:
        s.sendall(req)
        data, deadline = b"", time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([s], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    replies = http_responses(data)
    if not replies:
        return False, f"no HTTP reply: {data[:80]!r}"
    try:
        j = json.loads(replies[0]["body"])
    except ValueError:
        return False, f"body not JSON: {replies[0]['body'][:80]!r}"
    if j.get("error") is not None:
        return False, f"error {j['error']}"
    return True, f"result {j.get('result')}"


def transport() -> None:
    cases = [
        ("Fulcrum: 3 pipelined keep-alive requests", "Fulcrum!", lambda n: fulcrum_exchange(n)),
        ("Explorer: no Content-Type, Connection: close", "Explorer!", lambda n: explorer_exchange(n)),
        ("EloPool: getblockchaininfo, no id, no params", "EloPool!",
         lambda n: elopool_exchange(n, b'{"method": "getblockchaininfo"}\n')),
        ("EloPool: getblocktemplate (patched id 0)", "EloPool!",
         lambda n: elopool_exchange(n, b'{"id":0,"method": "getblocktemplate", "params": [{"capabilities": '
                                       b'["coinbasetxn", "workid", "coinbase/append"]}]}\n')),
        ("ASICSeer: getblockchaininfo (patched id 0)", "ASICSeer!",
         lambda n: asicseer_exchange(n, b'{"id":0,"method": "getblockchaininfo", "params": []}\n')),
        ("ASICSeer: getblockcount, no params", "ASICSeer",
         lambda n: asicseer_exchange(n, b'{"id":0,"method": "getblockcount"}\n')),
    ]
    for name, apps, fn in cases:
        row = {"case": name, "apps": apps}
        for label, node in NODES.items():
            try:
                ok, note = fn(node)
            except OSError as e:
                ok, note = False, f"{type(e).__name__}: {e}"
            row[label] = {"ok": ok, "note": note}
        row["verdict"] = ("ok" if row["kth"]["ok"] else
                          "n/a: BCHN fails too" if not row["bchn"]["ok"] else "GAP")
        RESULTS["transport"].append(row)
    # Facts behind the cases above.
    body = json.dumps({"id": 1, "method": "getblockcount", "params": []}).encode()
    for label, node in NODES.items():
        reply = post(node, body)
        RESULTS["setup"][f"{label}_body_ends_with_newline"] = reply["raw"].endswith(b"\n")
        batch = post(node, b'[{"id":1,"method":"getblockcount","params":[]},'
                           b'{"id":2,"method":"getbestblockhash","params":[]}]')
        RESULTS["setup"][f"{label}_batch_reply_unused_by_apps"] = batch["raw"][:120].decode("utf-8", "replace")


# ---- mining -----------------------------------------------------------------

def mining(s: dict) -> None:
    def step(name, node, method, *params):
        result, error = call(node, method, *params)
        RESULTS["mining"].append({"step": name, "node": "kth" if node == KTH else "bchn",
                                  **brief(result, error, 220)})
        return result, error

    template, error = step("light template", KTH, "getblocktemplatelight")
    if error is not None or not template:
        return
    RESULTS["mining"][-1]["merkle_branch_present"] = "merkle" in template
    block, block_hash, coinbase_hex = solve_coinbase_only(template, b"knuth-rpc-e2e", template.get("merkle"))
    RESULTS["mining"].append({"step": "built the coinbase on kth's light template, root over its job's "
                              f"{len(template.get('merkle') or [])}-hash merkle branch", "node": "-",
                              "result": block_hash})
    step("decoderawtransaction on the coinbase (EloPool exits if this fails)", KTH,
         "decoderawtransaction", coinbase_hex)
    step("decoderawtransaction on the coinbase", BCHN, "decoderawtransaction", coinbase_hex)
    start = call(KTH, "getblockcount")[0]
    accepted, error = step("submitblocklight the block", KTH, "submitblocklight", block, template["job_id"])
    s["row1"] = {"submitblocklight": accepted if error is None else error, "accepted": error is None and
                 accepted is None, "kth tip": call(KTH, "getbestblockhash")[0], "block": block_hash,
                 "kth height": call(KTH, "getblockcount")[0], "template height": template["height"]}
    # Row 2: BCHN hears of the block from kth alone, before it is given it below.
    reached = False
    if s["row1"]["accepted"]:
        for _ in range(20):
            if call(BCHN, "getbestblockhash")[0] == block_hash:
                reached = True
                break
            time.sleep(1)
    s["row2"] = {"kth accepted it": s["row1"]["accepted"], "BCHN tip is the block": reached,
                 "BCHN tip": call(BCHN, "getbestblockhash")[0], "block": block_hash}
    time.sleep(3)
    step("submitblock (classic) the same block", KTH, "submitblock", block)
    time.sleep(3)
    after = call(KTH, "getblockcount")[0]
    RESULTS["mining"].append({"step": f"kth height before {start}, after both submits {after}",
                              "node": "kth", "result": "grew" if after and start and after > start
                              else "did not grow"})
    step("control: submitblock the very same block to BCHN", BCHN, "submitblock", block)
    bchn_tip = call(BCHN, "getbestblockhash")[0]
    RESULTS["mining"].append({"step": "BCHN tip is the submitted block", "node": "bchn",
                              "result": bchn_tip == block_hash})
    synced = wait_kth_height(start + 1, 120)
    RESULTS["mining"].append({"step": "kth then syncs that block from BCHN over P2P", "node": "kth",
                              "result": synced})
    step("sendrawtransaction a fresh tx", KTH, "sendrawtransaction", s["fresh_tx_send"])
    step("sendrawtransaction the same tx", BCHN, "sendrawtransaction", s["fresh_tx_send"])


# ---- #773 rows: each client's own acceptance of the reply ------------------------

CASHADDR = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def cashaddr_polymod(values) -> int:
    c = 1
    for d in values:
        c0 = c >> 35
        c = ((c & 0x07ffffffff) << 5) ^ d
        for bit, gen in enumerate((0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470)):
            if c0 & (1 << bit):
                c ^= gen
    return c ^ 1


def cashaddr_convert(data, frm: int, to: int, pad: bool) -> list:
    acc = bits = 0
    out = []
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & ((1 << to) - 1))
    if pad and bits:
        out.append((acc << (to - bits)) & ((1 << to) - 1))
    return out


def cashaddr_retype(address: str, new_type: int) -> str:
    """The same hash under another CashAddr type (0 P2PKH, 1 P2SH, 2 and 3
    their token-aware forms)."""
    prefix, data = address.split(":")
    values = [CASHADDR.index(c) for c in data][:-8]
    raw = cashaddr_convert(values, 5, 8, False)
    raw[0] = (new_type << 3) | (raw[0] & 0x07)
    payload = cashaddr_convert(raw, 8, 5, True)
    mod = cashaddr_polymod([ord(x) & 0x1f for x in prefix] + [0] + payload + [0] * 8)
    return prefix + ":" + "".join(CASHADDR[v] for v in payload + [(mod >> 5 * (7 - i)) & 31 for i in range(8)])


def raw_exchange(node, request: bytes, timeout: float = 15) -> bytes:
    with socket.create_connection(node, timeout=timeout) as sock:
        sock.sendall(request)
        data, deadline = b"", time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([sock], [], [], deadline - time.monotonic())
            if not ready:
                break
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if http_responses(data) and not http_responses(data)[0].get("short"):
                break
    return data


def asicseer_json(node, body: bytes):
    """ASICseer's json_rpc_call, keeping the parsed reply: (ok, note, json)."""
    with socket.create_connection(node, timeout=15) as sock:
        sock.sendall(ckpool_request(node, body))
        r = LineReader(sock)
        n, line = r.read_line(15)
        if n < 1 or line[:15].lower() != b"http/1.1 200 ok":
            return False, f"status line {line[:60]!r}", None
        length = -1
        while length < 0:
            n, line = r.read_line(15)
            if n < 1:
                return False, "no Content-Length line", None
            if line[:16].lower() == b"content-length: ":
                length = int(line[16:].strip())
                n, _ = r.read_line(15)
                if n != 1:
                    return False, "no blank line right after Content-Length", None
        try:
            return True, "read", json.loads(r.read_exact(length, 15))
        except ValueError as error:
            return False, f"body does not parse: {error}", None


def row(number: int, client: str, check: str, ok: bool, detail) -> None:
    RESULTS["rows"].append({"row": number, "client": client, "check": check,
                            "verdict": "ok" if ok else "GAP",
                            "detail": detail if isinstance(detail, str) else json.dumps(detail)[:400]})


def row5_merkle(s: dict) -> None:
    """A miner on getblocktemplatelight builds a block that carries a
    transaction: the coinbase folded with the template's merkle branch must
    give the root of the block it submits, which BCHN then accepts."""
    client, check = "light-template miner", "block with a transaction, root from `merkle`, accepted by BCHN"
    txid, _ = call(KTH, "sendrawtransaction", s["fresh_tx_row5"])
    call(BCHN, "sendrawtransaction", s["fresh_tx_row5"])
    time.sleep(7)  # past kth's template refresh window under mempool change
    template, error = call(KTH, "getblocktemplatelight")
    if error is not None or not template:
        return row(5, client, check, False, {"template": error})
    branch = template.get("merkle")
    if not branch:
        return row(5, client, check, False, {"merkle": branch, "kth mempool": call(KTH, "getrawmempool")[0]})
    # The transactions of kth's full template on the same tip, in its order.
    full, error = call(KTH, "getblocktemplate")
    if error is not None or not full or full.get("previousblockhash") != template["previousblockhash"]:
        return row(5, client, check, False, {"getblocktemplate": error or "another tip"})
    txids = [t["txid"] for t in full.get("transactions", [])]
    expected = merkle_branch(txids)
    if expected != branch:
        return row(5, client, check, False, {"light merkle": branch, "branch over getblocktemplate's "
                                             f"{len(txids)} transactions": expected})
    txs = [bytes.fromhex(t["data"]) for t in full["transactions"]]
    subsidy = (50 * 100_000_000) >> (template["height"] // 150)
    base = coinbase(template["height"], subsidy, b"knuth-rpc-e2e row 5")
    root = fold_branch(sha256d(base), branch)
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template["mintime"])
    for nonce in range(1 << 32):
        header = struct.pack("<I", template["version"]) + prev + root + struct.pack("<III", when, bits, nonce)
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    block = header + bytes([1 + len(txs)]) + base + b"".join(txs)
    accepted, error = call(BCHN, "submitblock", block.hex())
    tip = call(BCHN, "getbestblockhash")[0]
    ok = error is None and accepted is None and tip == sha256d(header)[::-1].hex()
    row(5, client, check, ok, {"transactions": len(txs), "light merkle equals the branch over them": True,
                               "bchn submitblock": accepted if error is None else error,
                               "bchn tip is the block": tip == sha256d(header)[::-1].hex()})


def row6_validateaddress(s: dict) -> None:
    """ASICseer's validate_address on its payout address: isvalid true, a hex
    scriptPubKey for the coinbase output, and isscript. The scriptPubKey must
    be the one the address pays to (BCHN's answer for the same address)."""
    client = "ASICseer pool (validate_address)"
    plain = s["addr"]
    for label, address in (("P2PKH", plain), ("token-aware P2PKH", cashaddr_retype(plain, 2)),
                           ("P2SH", cashaddr_retype(plain, 1)), ("token-aware P2SH", cashaddr_retype(plain, 3))):
        body = json.dumps({"method": "validateaddress", "params": [address]}).encode() + b"\n"
        ok, note, reply = asicseer_json(KTH, body)
        if not ok:
            # ASICseer's bare-LF request and its line reader need #770 and #771;
            # send the same body over CRLF so this row is judged on its reply.
            transport = note
            reply = post(KTH, body)["json"]
            ok, note = isinstance(reply, dict), f"over CRLF; ASICseer's own transport: {transport}"
        bchn = call(BCHN, "validateaddress", address)[0] or {}
        result = (reply or {}).get("result") or {}
        spk = result.get("scriptPubKey")
        accepted = (ok and result.get("isvalid") is True and isinstance(spk, str) and len(spk) % 2 == 0
                    and "isscript" in result)
        row(6, client, f"{label} {address}", accepted and spk == bchn.get("scriptPubKey")
            and result.get("isscript") == bchn.get("isscript") and result.get("address") == bchn.get("address"),
            {"kth": reply.get("result") if isinstance(reply, dict) else None, "note": note, "bchn": bchn})
    wrong = "bitcoincash:qr7fzmep8g7h7ymfxy74lgc0v950j3r2959lhtxxsl"  # a mainnet address on regtest
    reply = call(KTH, "validateaddress", wrong)
    row(6, client, "mainnet address on regtest is not valid", reply[1] is None and reply[0] == {"isvalid": False},
        {"kth": reply})


def row8_blockchaininfo(s: dict) -> None:
    """Fulcrum's GetChainInfoTask parse, the Explorer's blocks == headers gate,
    and the remaining fields, against the same chain on BCHN."""
    info, error = call(KTH, "getblockchaininfo")
    bchn = call(BCHN, "getblockchaininfo")[0] or {}
    if error is not None or not isinstance(info, dict):
        return row(8, "Fulcrum", "getblockchaininfo parses", False, {"kth": error})
    fulcrum = (isinstance(info.get("blocks"), int) and info["blocks"] >= 0 and bool(info.get("chain"))
               and len(bytes.fromhex(info.get("bestblockhash", ""))) == 32)
    row(8, "Fulcrum", "GetChainInfoTask parse; initialblockdownload false at the tip",
        fulcrum and info.get("initialblockdownload") is False,
        {f: info.get(f) for f in ("blocks", "chain", "bestblockhash", "initialblockdownload")})
    row(8, "BCH Explorer", "indexer gate blocks == headers", info.get("blocks") == info.get("headers"),
        {"blocks": info.get("blocks"), "headers": info.get("headers")})
    same = {f: (info.get(f), bchn.get(f)) for f in ("mediantime", "chainwork")}
    row(8, "Fulcrum, pool status pages", "mediantime and chainwork equal BCHN's on the same chain",
        all(a == b and a is not None for a, b in same.values()), same)
    kinds = {"verificationprogress": (int, float), "size_on_disk": int, "warnings": str}
    present = {f: info.get(f) for f in kinds}
    row(8, "Fulcrum, pool status pages", "verificationprogress, size_on_disk, warnings present",
        all(isinstance(info.get(f), t) for f, t in kinds.items()), present)


def row15_help(s: dict) -> None:
    """Fulcrum's connection check: `help ["help"]` must succeed."""
    body = json.dumps({"id": 1, "method": "help", "params": ["help"]}).encode() + b"\r\n"
    request = (f"POST / HTTP/1.1\r\nHost: {KTH[0]}:{KTH[1]}\r\nContent-Type: application/json-rpc\r\n"
               f"Authorization: Basic {AUTH}\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
    replies = http_responses(raw_exchange(KTH, request))
    reply = json.loads(replies[0]["body"]) if replies else None
    ok = isinstance(reply, dict) and reply.get("error") is None and "result" in reply and reply.get("id") == 1
    row(15, "Fulcrum", "help [\"help\"] succeeds", ok, reply)
    listing = call(KTH, "help")
    row(15, "operator", "help lists the methods", listing[1] is None and "getblockcount" in (listing[0] or ""),
        listing)


def row16_confirmations(s: dict) -> None:
    """Fulcrum's verbose getblockheader: confirmations and height."""
    tip = call(KTH, "getblockcount")[0]
    header, error = call(KTH, "getblockheader", s["tx_block"], True)
    bchn = call(BCHN, "getblockheader", s["tx_block"], True)[0] or {}
    ok = (error is None and isinstance(header, dict) and isinstance(header.get("confirmations"), int)
          and header.get("height") == s["tx_block_height"]
          and header["confirmations"] == tip - s["tx_block_height"] + 1 == bchn.get("confirmations"))
    row(16, "Fulcrum", "getblockheader [hash, true] confirmations", ok,
        {"kth": header if error is None else error, "bchn confirmations": bchn.get("confirmations"), "tip": tip})


def row18_estimatefee(s: dict) -> None:
    """Fulcrum's one-argument estimatefee for nodes it does not know to take
    none; it passes the number back to the wallet."""
    fee, error = call(KTH, "estimatefee", 6)
    ok = error is None and isinstance(fee, (int, float)) and (fee >= 0 or fee == -1)
    row(18, "Fulcrum", "estimatefee [6] returns a fee rate", ok,
        {"kth": fee if error is None else error, "bchn estimatefee []": call(BCHN, "estimatefee")[0]})


def row19_networkhashps(s: dict) -> None:
    """The Explorer's indexer and mining page requests; on the same chain the
    rate must equal BCHN's."""
    for params in ([1, s["tx_block_height"]], [1, 1], [1008]):
        rate, error = call(KTH, "getnetworkhashps", *params)
        bchn = call(BCHN, "getnetworkhashps", *params)[0]
        ok = (error is None and isinstance(rate, (int, float)) and isinstance(bchn, (int, float))
              and abs(rate - bchn) <= 1e-9 * max(abs(bchn), 1e-30))
        row(19, "BCH Explorer", f"getnetworkhashps {json.dumps(params)}", ok,
            {"kth": rate if error is None else error, "bchn": bchn})


def keys_missing(kth, bchn, skip=()) -> list:
    """Keys BCHN's reply has that kth's lacks, recursively, ignoring `skip`."""
    missing = []
    if isinstance(bchn, dict):
        if not isinstance(kth, dict):
            return ["<object>"]
        for key, value in bchn.items():
            if key in skip:
                continue
            if key not in kth:
                missing.append(key)
            else:
                missing += [f"{key}.{m}" for m in keys_missing(kth[key], value, skip)]
    elif isinstance(bchn, list) and bchn and isinstance(kth, list) and kth:
        missing += [f"[0].{m}" for m in keys_missing(kth[0], bchn[0], skip)]
    return missing


def row7_decoderawtransaction(s: dict) -> None:
    """EloPool's startup call: decoderawtransaction on its coinbase must answer
    HTTP 200 with JSON, or EloPool exits. The shape is set against BCHN's for
    the same transaction (#370 asks for that comparison)."""
    template = call(KTH, "getblocktemplatelight")[0] or {"height": 1}
    coinbase_hex = coinbase(template["height"], 50 * 100_000_000, b"knuth-rpc-e2e row 7").hex()
    body = json.dumps({"method": "decoderawtransaction", "params": [coinbase_hex]}).encode()
    ok, note = elopool_exchange(KTH, body + b"\n")
    reply = post(KTH, body)["json"] or {}
    if not ok:
        note = f"over CRLF; EloPool's own transport: {note}"
    result = reply.get("result")
    row(7, "EloPool", "decoderawtransaction on its coinbase answers JSON", isinstance(result, dict),
        {"note": note, "kth": result if result is not None else reply.get("error")})
    for label, txid in (("coinbase", s["coinbase_txid"]), ("spend", s["confirmed_txid"])):
        raw = call(BCHN, "getrawtransaction", txid, False)[0]
        kth, error = call(KTH, "decoderawtransaction", raw)
        bchn = call(BCHN, "decoderawtransaction", raw)[0]
        missing = keys_missing(kth, bchn, skip=("asm",)) if error is None else ["<error>"]
        same = error is None and all(kth.get(k) == bchn.get(k) for k in ("txid", "size", "locktime", "version"))
        outs = error is None and [(o["value"], o["scriptPubKey"]["hex"], o["scriptPubKey"].get("addresses"))
                                  for o in kth["vout"]] == [(o["value"], o["scriptPubKey"]["hex"],
                                                            o["scriptPubKey"].get("addresses")) for o in bchn["vout"]]
        row(7, "shape vs bitcoind (#370)", f"decoderawtransaction {label}: same fields and values",
            same and outs and not missing, {"missing": missing, "kth": kth if error is None else error})


def row9_chain(s: dict) -> None:
    """EloPool and ASICseer map `chain` to the payout address prefix."""
    known = ("main", "test", "test4", "scale", "chip", "regtest")
    for method in ("getblockchaininfo", "getmininginfo"):
        kth = (call(KTH, method)[0] or {}).get("chain")
        bchn = (call(BCHN, method)[0] or {}).get("chain")
        row(9, "EloPool, ASICseer pool", f"{method} chain is a name the pools map", kth in known and kth == bchn,
            {"kth": kth, "bchn": bchn})


def row13_gettxout(s: dict) -> None:
    """The Explorer's gettxout [txid, n] and [txid, n, false]: an unspent output
    is described, a spent one is null."""
    txid, n = s["confirmed_txid"], s["unspent_n"]
    for params in ([txid, n], [txid, n, False]):
        kth, error = call(KTH, "gettxout", *params)
        bchn = call(BCHN, "gettxout", *params)[0]
        same = error is None and isinstance(kth, dict) and isinstance(bchn, dict) and all(
            kth.get(k) == bchn.get(k) for k in ("bestblock", "confirmations", "value", "coinbase")) and \
            kth["scriptPubKey"]["hex"] == bchn["scriptPubKey"]["hex"]
        row(13, "BCH Explorer", f"gettxout {json.dumps(params)[:24]}... unspent", same,
            {"kth": kth if error is None else error, "bchn": bchn,
             "missing": keys_missing(kth, bchn, skip=("asm",)) if error is None else None})
    # An output spent in a block: the confirmed transaction's own input.
    spent = (call(BCHN, "getrawtransaction", txid, True)[0] or {}).get("vin", [{}])[0]
    kth, error = call(KTH, "gettxout", spent.get("txid"), spent.get("vout"))
    bchn = call(BCHN, "gettxout", spent.get("txid"), spent.get("vout"))[0]
    row(13, "BCH Explorer", "gettxout on an output spent in a block is null",
        error is None and kth is None and bchn is None, {"kth": kth if error is None else error, "bchn": bchn})


def row14_getnetworkinfo(s: dict) -> None:
    """Fulcrum's node detection (subversion, version, relayfee, warnings), the
    Explorer's subversion, and pool status pages' connections."""
    info, error = call(KTH, "getnetworkinfo")
    bchn = call(BCHN, "getnetworkinfo")[0] or {}
    if error is not None or not isinstance(info, dict):
        return row(14, "Fulcrum", "getnetworkinfo answers", False, error)
    fulcrum = (isinstance(info.get("subversion"), str) and info["subversion"].startswith("/")
               and isinstance(info.get("version"), int) and info["version"] > 0
               and isinstance(info.get("relayfee"), (int, float)) and isinstance(info.get("warnings"), str))
    row(14, "Fulcrum", "subversion, version, relayfee, warnings", fulcrum,
        {f: info.get(f) for f in ("subversion", "version", "relayfee", "warnings")})
    row(14, "pool status pages", "connections counts the BCHN peer",
        isinstance(info.get("connections"), int) and info["connections"] >= 1,
        {"connections": info.get("connections"), "missing vs bitcoind": keys_missing(
            info, bchn, skip=("networks", "localaddresses", "timeoffset", "excessutxocharge",
                              "connections_in", "connections_out"))})


def row20_getchaintips(s: dict) -> None:
    """The Explorer's getchaintips at startup: the active tip is BCHN's."""
    tips, error = call(KTH, "getchaintips")
    bchn = call(BCHN, "getchaintips")[0] or []
    active = [t for t in (tips or []) if isinstance(t, dict) and t.get("status") == "active"]
    bchn_active = [t for t in bchn if t.get("status") == "active"]
    row(20, "BCH Explorer", "getchaintips lists the active tip", error is None and len(active) == 1
        and active[0].get("hash") == bchn_active[0]["hash"] and active[0].get("height") == bchn_active[0]["height"],
        {"kth": tips if error is None else error, "bchn": bchn})


def compact_size(n: int) -> bytes:
    if n < 0xfd:
        return bytes([n])
    if n <= 0xffff:
        return b"\xfd" + struct.pack("<H", n)
    return b"\xfe" + struct.pack("<I", n)


def row4_getblocktemplate(s: dict) -> None:
    """ASICseer's and EloPool's getblocktemplate requests; then a block built
    the way they build it (their coinbase, the template's transactions in its
    order) must be accepted by BCHN."""
    for client, request in (
            ("ASICseer pool", {"capabilities": ["coinbasetxn", "workid", "coinbase/append"], "rules": []}),
            ("EloPool", {"capabilities": ["coinbasetxn", "workid", "coinbase/append"]})):
        # A block another row just mined may still be connecting: kth then
        # answers that the connected chain has not caught up, as BCHN answers
        # while it is downloading. Wait that out, briefly.
        for _ in range(30):
            template, error = call(KTH, "getblocktemplate", request)
            if error is None or "not caught up" not in str(error):
                break
            time.sleep(1)
        fields = ("previousblockhash", "target", "version", "curtime", "bits", "height", "coinbasevalue",
                  "transactions")
        ok = error is None and isinstance(template, dict) and all(f in template for f in fields) and \
            template["version"] != 0 and all({"data", "txid", "hash"} <= set(t) for t in template["transactions"])
        row(4, client, "getblocktemplate has the fields the pool reads", ok,
            {"kth": {k: v for k, v in (template or {}).items() if k != "transactions"} if error is None else error,
             "transactions": len((template or {}).get("transactions", []))})
    # A transaction for the template to carry: kth's mempool only has what it
    # is sent (relay from peers is #371).
    call(KTH, "sendrawtransaction", s["fresh_tx_row4"])
    call(BCHN, "sendrawtransaction", s["fresh_tx_row4"])
    time.sleep(7)  # past kth's template refresh window under mempool change
    template, error = call(KTH, "getblocktemplate", {"capabilities": ["coinbasetxn", "workid", "coinbase/append"]})
    if error is not None or not template:
        return row(4, "ASICseer pool, EloPool", "block from kth's template accepted by BCHN", False, error)
    txs = [bytes.fromhex(t["data"]) for t in template["transactions"]]
    fees = sum(t.get("fee", 0) for t in template["transactions"])
    subsidy = (50 * 100_000_000) >> (template["height"] // 150)
    base = coinbase(template["height"], subsidy + fees, b"knuth-rpc-e2e row 4")
    level = [sha256d(base)] + [sha256d(t) for t in txs]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    prev = bytes.fromhex(template["previousblockhash"])[::-1]
    bits = int(template["bits"], 16)
    target = (bits & 0xFFFFFF) << (8 * ((bits >> 24) - 3))
    when = max(template["curtime"], template["mintime"])
    for nonce in range(1 << 32):
        header = struct.pack("<I", template["version"]) + prev + level[0] + struct.pack("<III", when, bits, nonce)
        if int.from_bytes(sha256d(header)[::-1], "big") <= target:
            break
    block = header + compact_size(1 + len(txs)) + base + b"".join(txs)
    accepted, error = call(BCHN, "submitblock", block.hex())
    tip = call(BCHN, "getbestblockhash")[0]
    row(4, "ASICseer pool, EloPool", "block built from kth's getblocktemplate accepted by BCHN",
        error is None and accepted is None and tip == sha256d(header)[::-1].hex(),
        {"transactions": len(txs), "fees": fees, "bchn submitblock": accepted if error is None else error,
         "bchn tip is the block": tip == sha256d(header)[::-1].hex()})
    wait_kth_height(template["height"], 120)


def row12_block_reads(s: dict) -> None:
    """Fulcrum's raw block and mempool transaction reads, and the Explorer's
    verbose block and transaction reads."""
    blk = s["tx_block"]
    raw, error = call(KTH, "getblock", blk, False)
    bchn_raw = call(BCHN, "getblock", blk, False)[0]
    ok = error is None and isinstance(raw, str) and sha256d(bytes.fromhex(raw[:160]))[::-1].hex() == blk \
        and raw == bchn_raw
    row(12, "Fulcrum", "getblock [hash, false]: the block, its header hashing to the hash", ok,
        {"kth": (raw[:64] + "...") if isinstance(raw, str) else error})
    pool = call(KTH, "getrawmempool", False)[0] or []
    for txid in pool[:2]:
        kth, error = call(KTH, "getrawtransaction", txid, False)
        bchn = call(BCHN, "getrawtransaction", txid, False)[0]
        # The transaction is the one asked for when its bytes hash to the txid.
        # Compared with BCHN's copy only when BCHN holds it: a transaction sent
        # to kth alone is in no other mempool.
        ok = error is None and isinstance(kth, str) and sha256d(bytes.fromhex(kth))[::-1].hex() == txid \
            and (bchn is None or kth == bchn)
        row(12, "Fulcrum", f"getrawtransaction [{txid[:12]}..., false] for a mempool tx", ok,
            {"kth": (kth[:40] + "...") if isinstance(kth, str) else error, "in BCHN's mempool": bchn is not None})
    kth, error = call(KTH, "getrawtransaction", s["coinbase_txid"], False)
    if error is None:
        # kth finds it through an output of it that is still unspent, and no
        # transaction index. One with every output spent still needs its block
        # hash: the coinbase the confirmed wallet transaction spent.
        bchn = call(BCHN, "getrawtransaction", s["coinbase_txid"], False)[0]
        found = isinstance(kth, str) and sha256d(bytes.fromhex(kth))[::-1].hex() == s["coinbase_txid"]
        row(12, "Fulcrum", "a confirmed tx without its block hash is found through an unspent output",
            found and kth == bchn,
            {"kth": (kth[:40] + "...") if isinstance(kth, str) else kth, "same bytes as BCHN": kth == bchn})
        located = call(KTH, "getrawtransaction", s["coinbase_txid"], True)[0] or {}
        there = call(BCHN, "getrawtransaction", s["coinbase_txid"], True)[0] or {}
        row(12, "BCH Explorer", "the same call, verbose: the block it is in and its confirmations",
            located.get("blockhash") == there.get("blockhash") == s["tx_block"]
            and located.get("confirmations") == there.get("confirmations"),
            {"kth": {k: located.get(k) for k in ("blockhash", "confirmations")},
             "bchn": {k: there.get(k) for k in ("blockhash", "confirmations")}})
        spender = call(BCHN, "getrawtransaction", s["confirmed_txid"], True)[0] or {}
        spent_txid = ((spender.get("vin") or [{}])[0]).get("txid")
        outputs = [cli("gettxout", spent_txid, str(n)) for n in range(2)] if spent_txid else ["?"]
        gone, gone_error = call(KTH, "getrawtransaction", spent_txid, False)
        row(12, "Fulcrum", "a confirmed tx with every output spent still needs its block hash: error -5, "
            "which Fulcrum tolerates",
            spent_txid is not None and all(o in ("", "null") for o in outputs)
            and gone is None and gone_error is not None and gone_error.get("code") == -5,
            {"txid": spent_txid, "unspent outputs on BCHN": sum(o not in ("", "null") for o in outputs),
             "kth": gone_error})
    else:
        row(12, "Fulcrum", "a confirmed tx without its block hash is error -5, which Fulcrum tolerates",
            error.get("code") == -5, {"kth": error})
    for verbosity in (1, 2):
        kth, error = call(KTH, "getblock", blk, verbosity)
        bchn = call(BCHN, "getblock", blk, verbosity)[0]
        if error is None and not isinstance(kth, dict):
            error = {"not an object": str(kth)[:40]}
        missing = keys_missing(kth, bchn, skip=("asm", "fee", "ablastate")) if error is None else ["<error>"]
        same = error is None and all(kth.get(k) == bchn.get(k) for k in (
            "hash", "confirmations", "size", "height", "version", "merkleroot", "time", "mediantime", "nonce",
            "bits", "chainwork", "nTx", "previousblockhash", "nextblockhash"))
        txs = error is None and (kth["tx"] == bchn["tx"] if verbosity == 1 else
                                 [t["txid"] for t in kth["tx"]] == [t["txid"] for t in bchn["tx"]])
        row(12, "BCH Explorer", f"getblock [hash, {verbosity}] fields and values", same and txs and not missing,
            {"missing": missing, "differs": [k for k in ("confirmations", "mediantime", "chainwork", "nextblockhash",
                                                          "size") if error is None and kth.get(k) != bchn.get(k)]})
    kth, error = call(KTH, "getrawtransaction", s["confirmed_txid"], True, blk)
    bchn = call(BCHN, "getrawtransaction", s["confirmed_txid"], True, blk)[0]
    if error is None and not isinstance(kth, dict):
        error = {"not an object": str(kth)[:40]}
    same = error is None and all(kth.get(k) == bchn.get(k) for k in ("txid", "hex", "blockhash", "confirmations",
                                                                      "time", "blocktime", "in_active_chain"))
    missing = keys_missing(kth, bchn, skip=("asm",)) if error is None else ["<error>"]
    row(12, "BCH Explorer", "getrawtransaction [txid, true, blockhash]", same and not missing,
        {"kth": {k: kth.get(k) for k in ("blockhash", "confirmations", "time", "in_active_chain")}
         if error is None else error, "missing": missing})


def row10_relay_in(s: dict) -> None:
    """Transactions relayed by a peer reach kth's mempool: the wallet
    transactions BCHN made right after kth synced, and one it makes now, long
    after, are in kth's mempool too."""
    client = "Fulcrum, BCH Explorer, miners"
    # The light-template row may have mined them into a block since: then
    # kth has them confirmed, which counts as having received them.
    kth = set(call(KTH, "getrawmempool")[0] or [])
    recent = set()
    height = call(KTH, "getblockcount")[0] or 0
    for h in range(max(0, height - 5), height + 1):
        block, _ = call(KTH, "getblock", call(KTH, "getblockhash", h)[0], 1)
        recent.update((block or {}).get("tx", []))
    confirmed = [t for t in s["mempool_txids"] if t not in kth and t in recent]
    missing = [t for t in s["mempool_txids"] if t not in kth and t not in confirmed]
    row(10, client, "BCHN's wallet transactions from right after the sync reached kth",
        not missing, {"kth mempool": sorted(kth), "confirmed in kth's chain": confirmed, "missing": missing})
    late = cli("sendtoaddress", s["others"][2], "0.1")
    reached = False
    for _ in range(30):
        if late in (call(KTH, "getrawmempool")[0] or []):
            reached = True
            break
        time.sleep(1)
    row(10, client, "a wallet transaction BCHN makes later reaches kth's mempool", reached,
        {"txid": late, "in kth's mempool": reached})
    # The same at a fee kth's own admission price is met by: 1.1 satoshis per
    # byte covers its per-byte and per-signature-operation price for a
    # payment of 200 bytes or more, so that what is checked is the relay
    # itself, whatever the node's fee policy.
    cli("settxfee", "0.000011")
    try:
        priced = cli("sendtoaddress", s["others"][2], "0.1")
    finally:
        cli("settxfee", "0")
    arrived = False
    for _ in range(30):
        if priced in (call(KTH, "getrawmempool")[0] or []):
            arrived = True
            break
        time.sleep(1)
    entry = (call(BCHN, "getmempoolentry", priced)[0] or {}) if priced else {}
    row(10, client, "a wallet transaction BCHN makes later, paying kth's price, reaches kth's mempool",
        arrived, {"txid": priced, "in kth's mempool": arrived, "size": entry.get("size"),
                  "fee": (entry.get("fees") or {}).get("base")})


def row11_relay_out(s: dict) -> None:
    """A transaction sent only to kth reaches BCHN's mempool."""
    txid, error = call(KTH, "sendrawtransaction", s["fresh_tx_row11"])
    reached = False
    for _ in range(30):
        if txid and txid in (call(BCHN, "getrawmempool")[0] or []):
            reached = True
            break
        time.sleep(1)
    row(11, "wallets, Fulcrum (broadcast)", "a transaction sent only to kth reaches BCHN's mempool",
        error is None and reached, {"kth sendrawtransaction": txid if error is None else error,
                                    "in BCHN's mempool": reached})


def pr795_fee_filter(s: dict) -> None:
    """kth tells its peer the floor its own mempool admits at (#873): BCHN's
    getpeerinfo shows a fee filter of 1000 satoshis per 1000 bytes, the relay
    floor kth and BCHN share since #868."""
    peers, error = call(BCHN, "getpeerinfo")
    kth_peers = [p for p in (peers or []) if "kth" in p.get("subver", "").lower()
                 or "knuth" in p.get("subver", "").lower()]
    seen = [{"subver": p.get("subver"), "minfeefilter": p.get("minfeefilter")} for p in kth_peers]
    ok = error is None and bool(kth_peers) and all(
        abs(float(p.get("minfeefilter", -1)) - 0.00001) < 1e-12 for p in kth_peers)
    row(873, "every peer (fee filter)", "BCHN sees kth's fee filter at 1000 sat/kB, the relay floor both use",
        ok, seen if error is None else error)


def row17_rawmempool_verbose(s: dict) -> None:
    """The Explorer's getrawmempool [true]: an object keyed by txid, each with
    fees.base, size and depends."""
    kth, error = call(KTH, "getrawmempool", True)
    bchn = call(BCHN, "getrawmempool", True)[0] or {}
    ok = error is None and isinstance(kth, dict) and kth and all(
        isinstance(v.get("fees", {}).get("base"), (int, float)) and isinstance(v.get("size"), int)
        and isinstance(v.get("depends"), list) for v in kth.values())
    shared = [t for t in kth if t in bchn] if isinstance(kth, dict) else []
    same = all(kth[t]["fees"]["base"] == bchn[t]["fees"]["base"] and kth[t]["size"] == bchn[t]["size"]
               for t in shared)
    row(17, "BCH Explorer", "getrawmempool [true] entries keyed by txid", ok and same,
        {"kth": kth if error is None else error, "shared with BCHN": len(shared)})


def row1_accept_found_block(s: dict) -> None:
    """The mining section's submitblocklight of a block built on kth's own light
    template, read right after the call: null, and kth's tip is that block
    before the same block is given to BCHN."""
    seen = s.get("row1")
    ok = bool(seen) and seen["accepted"] and seen["kth tip"] == seen["block"] \
        and seen["kth height"] == seen["template height"]
    row(1, "light-template miner", "submitblocklight answers null and kth's tip is the block", ok, seen)


def row2_found_block_reaches_peers(s: dict) -> None:
    """The block kth accepted through submitblocklight reaches BCHN from kth
    alone, before the harness gives it to BCHN itself."""
    seen = s.get("row2")
    row(2, "light-template miner (the network learns of its block)",
        "BCHN's tip becomes the block kth accepted", bool(seen) and seen["kth accepted it"]
        and seen["BCHN tip is the block"], seen)


def row3_submitblock(s: dict) -> None:
    """A pool on full templates submits a whole block with submitblock: null,
    and kth's tip is that block. A coinbase-only block on the current tip, so it
    does not depend on what kth's mempool holds. BCHN is then given it too, so
    the two stay on one chain."""
    client = "ASICseer pool, EloPool (submitblock)"
    template, error = call(KTH, "getblocktemplatelight")
    if error is not None or not template:
        return row(3, client, "getblocktemplatelight answers", False, error)
    block, block_hash, _ = solve_coinbase_only(template, b"knuth-rpc-e2e row 3")
    accepted, error = call(KTH, "submitblock", block)
    tip = call(KTH, "getbestblockhash")[0]
    row(3, client, "submitblock answers null and kth's tip is the block",
        error is None and accepted is None and tip == block_hash,
        {"submitblock": accepted if error is None else error, "kth tip": tip, "block": block_hash})
    call(BCHN, "submitblock", block)


def sv2_frame(msg_type: int, payload: bytes, extension: int = 0) -> bytes:
    """sv2-spec 3.2: extension_type U16, msg_type U8, msg_length U24, payload."""
    return struct.pack("<HB", extension, msg_type) + len(payload).to_bytes(3, "little") + payload


def sv2_str(value: bytes) -> bytes:
    return bytes([len(value)]) + value


def sv2_setup(protocol: int) -> bytes:
    """SetupConnection (sv2-spec 3.6.1) for version 2, no flags."""
    return sv2_frame(0x00, struct.pack("<BHHI", protocol, 2, 2, 0) + sv2_str(b"127.0.0.1")
                     + struct.pack("<H", SV2_PORT) + sv2_str(b"knuth-rpc-e2e") + sv2_str(b"")
                     + sv2_str(b"") + sv2_str(b""))


def sv2_read(reader: LineReader, timeout: float = 10) -> tuple[int, int, bytes] | None:
    header = reader.read_exact(6, timeout)
    if len(header) < 6:
        return None
    extension, msg_type = struct.unpack("<HB", header[:3])
    length = int.from_bytes(header[3:], "little")
    payload = reader.read_exact(length, timeout)
    return (extension, msg_type, payload) if len(payload) == length else None


def sv2_error_code(payload: bytes, offset: int) -> str:
    return payload[offset + 1:offset + 1 + payload[offset]].decode(errors="replace")


def row21_template_provider(s: dict) -> None:
    """A Stratum V2 Template Distribution client: SetupConnection gets Success
    for version 2, CoinbaseOutputConstraints is taken, and RequestTransactionData
    for an unknown template gets RequestTransactionData.Error. A client asking
    for the mining protocol gets SetupConnection.Error and the connection ends."""
    client = f"Stratum V2 Template Distribution client ({SV2_TRANSPORT})"
    if not SV2:
        return
    seen: dict = {}
    try:
        with socket.create_connection(("127.0.0.1", SV2_CLIENT_PORT), timeout=10) as sock:
            reader = LineReader(sock)
            sock.sendall(sv2_setup(2))
            reply = sv2_read(reader)
            seen["setup"] = reply and {"msg_type": reply[1],
                                       "used_version": struct.unpack("<H", reply[2][:2])[0]}
            sock.sendall(sv2_frame(0x70, struct.pack("<IH", 4000, 400)))
            sock.sendall(sv2_frame(0x73, struct.pack("<Q", 7)))
            reply = sv2_read(reader)
            # Where the provider already sends work (row 23), NewTemplate and
            # SetNewPrevHash come first; the answer to the request follows.
            while reply and reply[1] in (0x71, 0x72):
                reply = sv2_read(reader)
            seen["request"] = reply and {"msg_type": reply[1],
                                         "template_id": struct.unpack("<Q", reply[2][:8])[0],
                                         "error_code": sv2_error_code(reply[2], 8)}
        with socket.create_connection(("127.0.0.1", SV2_CLIENT_PORT), timeout=10) as sock:
            reader = LineReader(sock)
            sock.sendall(sv2_setup(0))
            reply = sv2_read(reader)
            seen["mining setup"] = reply and {"msg_type": reply[1], "error_code": sv2_error_code(reply[2], 4)}
            seen["closed after error"] = reader.read_exact(1, 10) == b""
    except OSError as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    ok = (seen.get("setup") == {"msg_type": 0x01, "used_version": 2}
          and seen.get("request") == {"msg_type": 0x75, "template_id": 7, "error_code": "template-id-not-found"}
          and seen.get("mining setup") == ({"msg_type": 0x01, "error_code": ""} if SV2_MINING
                                            else {"msg_type": 0x02, "error_code": "unsupported-protocol"})
          and seen.get("closed after error") is True)
    row(21, client, "setup succeeds on version 2, constraints are taken, unknown template answered, "
        + ("mining protocol accepted (sv2.mining.enabled)" if SV2_MINING else "mining protocol refused"),
        ok, seen)


def row23_template_flow(s: dict) -> None:
    """A Stratum V2 Template Distribution client mines on kth's work. After its
    constraints it gets NewTemplate and SetNewPrevHash without asking, fetches
    the template's transactions, builds its coinbase, solves the regtest header
    and submits it: kth's tip becomes that block, and BCHN accepts it too."""
    client = f"Stratum V2 Template Distribution client ({SV2_TRANSPORT}, mines a block)"
    if not SV2:
        return
    seen: dict = {}

    def fail(check: str) -> None:
        row(23, client, check, False, seen)

    try:
        with socket.create_connection(("127.0.0.1", SV2_CLIENT_PORT), timeout=20) as sock:
            reader = LineReader(sock)
            sock.sendall(sv2_setup(2))
            reply = sv2_read(reader)
            if not reply or reply[1] != 0x01:
                return fail("setup succeeds")
            sock.sendall(sv2_frame(0x70, struct.pack("<IH", 200, 10)))
            first, second = sv2_read(reader, 30), sv2_read(reader, 30)
            seen["frames after constraints"] = [first and first[1], second and second[1]]
            if not first or not second or first[1] != 0x71 or second[1] != 0x72:
                return fail("NewTemplate then SetNewPrevHash after the constraints")

            work, o = first[2], 0
            template_id, = struct.unpack_from("<Q", work, o); o += 8
            future = work[o]; o += 1
            version, coinbase_version = struct.unpack_from("<II", work, o); o += 8
            prefix = work[o + 1:o + 1 + work[o]]; o += 1 + work[o]
            sequence, value = struct.unpack_from("<IQ", work, o); o += 12
            output_count, = struct.unpack_from("<I", work, o); o += 4
            outputs_size, = struct.unpack_from("<H", work, o)
            outputs = work[o + 2:o + 2 + outputs_size]; o += 2 + outputs_size
            locktime, = struct.unpack_from("<I", work, o); o += 4
            path = [work[o + 1 + 32 * i:o + 33 + 32 * i] for i in range(work[o])]

            activation = second[2]
            previous = activation[8:40]
            ntime, bits = struct.unpack_from("<II", activation, 40)
            target = int.from_bytes(activation[48:80], "little")
            seen.update({"template_id": template_id, "future_template": future,
                         "SetNewPrevHash names it": struct.unpack_from("<Q", activation, 0)[0] == template_id,
                         "coinbase value": value, "merkle path": len(path)})

            sock.sendall(sv2_frame(0x73, struct.pack("<Q", template_id)))
            data = sv2_read(reader, 30)
            if not data or data[1] != 0x74:
                return fail("RequestTransactionData returns the template's transactions")
            body, o = data[2], 8
            o += 2 + struct.unpack_from("<H", body, o)[0]
            count, = struct.unpack_from("<H", body, o); o += 2
            transactions = []
            for _ in range(count):
                size = int.from_bytes(body[o:o + 3], "little"); o += 3
                transactions.append(body[o:o + size]); o += size
            seen["transactions"] = len(transactions)

            # The template's prefix, padded so the coinbase is at least the
            # 100 bytes BCH requires of a transaction, paying one OP_TRUE output.
            script_sig = prefix + b"knuth-rpc-e2e row 23".ljust(40, b".")
            coinbase = (struct.pack("<I", coinbase_version) + b"\x01" + bytes(32) + b"\xff\xff\xff\xff"
                        + compact_size(len(script_sig)) + script_sig + struct.pack("<I", sequence)
                        + compact_size(output_count + 1) + outputs
                        + struct.pack("<Q", value) + b"\x01\x51" + struct.pack("<I", locktime))
            root = sha256d(coinbase)
            for sibling in path:
                root = sha256d(root + sibling)
            for nonce in range(1 << 20):
                header = struct.pack("<I", version) + previous + root + struct.pack("<III", ntime, bits, nonce)
                if int.from_bytes(sha256d(header), "little") <= target:
                    break
            else:
                return fail("a regtest header solves within 2^20 nonces")
            block_hash = sha256d(header)[::-1].hex()
            seen["block"] = block_hash

            sock.sendall(sv2_frame(0x76, struct.pack("<QIII", template_id, version, ntime, nonce)
                                   + struct.pack("<H", len(coinbase)) + coinbase))
            tip = None
            for _ in range(30):
                tip = call(KTH, "getbestblockhash")[0]
                if tip == block_hash:
                    break
                time.sleep(1)
            seen["kth tip"] = tip

            block = header + compact_size(1 + len(transactions)) + coinbase + b"".join(transactions)
            accepted, error = call(BCHN, "submitblock", block.hex())
            seen["BCHN submitblock"] = accepted if error is None else error
    except OSError as error:
        seen["error"] = f"{type(error).__name__}: {error}"
        return fail("the client completes")
    row(23, client, "kth's tip becomes the block mined on its template; BCHN accepts the block",
        seen.get("kth tip") == seen.get("block") and seen.get("BCHN submitblock") in (None, "duplicate"), seen)


def read_until_closed(sock: socket.socket, seconds: float) -> dict:
    started, received, closed = time.monotonic(), b"", False
    while time.monotonic() - started < seconds:
        ready, _, _ = select.select([sock], [], [], seconds - (time.monotonic() - started))
        if not ready:
            break
        try:
            chunk = sock.recv(65536)
        except ConnectionResetError:
            chunk = b""
        if not chunk:
            closed = True
            break
        received += chunk
    return {"bytes": len(received), "closed": closed, "seconds": round(time.monotonic() - started, 1)}


def row22_noise(s: dict) -> None:
    """Stratum V2 over Noise (sv2-spec 4). Rows 21 and 23 reached kth only
    through the bridge, so every frame they exchanged went through the Stratum
    Reference Implementation's handshake and ciphers. Here: every bridge
    handshake succeeded and frames went both ways; the authority key kth prints
    is key-utils' encoding of its authority secret; a client pinned to another
    authority refuses kth's certificate; plaintext sent straight to kth's port
    never gets an SV2 answer, and the connection is closed."""
    client = "Stratum V2 pool / Job Declarator (Noise, SRI crates)"
    if not (SV2 and NOISE):
        return
    seen: dict = {"keys": NOISE_KEYS}
    time.sleep(2)  # the bridge logs a connection when both sides have closed
    events = []
    log = OUT / "noise-bridge.log"
    for line in (log.read_text(errors="replace").splitlines() if log.exists() else []):
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    handshakes = [e for e in events if e.get("event") == "handshake"]
    closed = [e for e in events if e.get("event") == "closed"]
    seen["bridge"] = {"handshakes ok": sum(1 for e in handshakes if e.get("ok")),
                      "handshakes failed": [e for e in handshakes if not e.get("ok")],
                      "frames to kth": sum(e["frames_to_kth"] for e in closed),
                      "frames from kth": sum(e["frames_from_kth"] for e in closed),
                      "largest payload from kth": max([e["largest_payload_from_kth"] for e in closed] or [0])}

    def probe(key: str) -> dict:
        out = subprocess.run([NOISE_BRIDGE, "--probe", "--connect", f"127.0.0.1:{SV2_PORT}",
                              "--authority", key], capture_output=True, text=True, timeout=30)
        return {"exit": out.returncode, "output": out.stdout.strip()}

    seen["probe pinned to kth's key"] = probe(NOISE_KEYS.get("printed by kth", ""))
    seen["probe pinned to another authority"] = probe(OTHER_AUTHORITY)
    try:
        # Shorter than the 64-byte first flight: kth waits for the rest until
        # its 10-second handshake deadline, then closes.
        with socket.create_connection(("127.0.0.1", SV2_PORT), timeout=10) as sock:
            sock.sendall(sv2_setup(2))
            seen["plaintext setup"] = read_until_closed(sock, 30)
        # Longer: kth takes the first 64 bytes as a key and answers with its
        # 234-byte handshake flight, then the rest fails authentication.
        with socket.create_connection(("127.0.0.1", SV2_PORT), timeout=10) as sock:
            sock.sendall(sv2_setup(2) + sv2_setup(2))
            seen["plaintext setup sent twice"] = read_until_closed(sock, 30)
    except OSError as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    bridge, other = seen["bridge"], seen["probe pinned to another authority"]
    short, long = seen.get("plaintext setup", {}), seen.get("plaintext setup sent twice", {})
    ok = (NOISE_KEYS.get("printed by kth") is not None
          and NOISE_KEYS.get("printed by kth") == NOISE_KEYS.get("key-utils, from the secret file")
          and bridge["handshakes ok"] >= 3 and not bridge["handshakes failed"]
          and bridge["frames to kth"] >= 6 and bridge["frames from kth"] >= 5
          and seen["probe pinned to kth's key"]["exit"] == 0
          and other["exit"] == 1 and "second flight" in other["output"]
          and short.get("bytes") == 0 and short.get("closed") and 8 <= short.get("seconds", 0) <= 15
          and long.get("bytes") == 234 and long.get("closed") and long.get("seconds", 99) < 5)
    row(22, client, "SRI's handshake and ciphers carry rows 21 and 23; another authority is refused; "
        "plaintext gets no answer and is closed", ok, seen)


def pr803_outbound_proxy(s: dict) -> None:
    """kth reaches BCHN only through the SOCKS5 proxy: the relay was asked for
    BCHN's address, each connection with fresh credentials, and every connection
    BCHN has from kth is one of the relay's own."""
    client = "node operators behind Tor (outbound SOCKS5)"
    if not PROXY or RELAY is None:
        return
    with RELAY.lock:
        requests = list(RELAY.requests)
    to_bchn = [r for r in requests if r.get("port") == 18444 and "error" not in r]
    relay_ports = {r.get("relay_port") for r in to_bchn}
    peers = call(BCHN, "getpeerinfo")[0] or []
    kth_peers = [p for p in peers if "knuth" in p.get("subver", "").lower()]
    via_relay = [p for p in kth_peers if int(p.get("addr", ":0").rsplit(":", 1)[1]) in relay_ports]
    users = [r.get("user") for r in requests]
    seen = {"requests": [{k: r.get(k) for k in ("host", "port", "address_type", "offered", "relay_port", "error")}
                         for r in requests],
            "fresh credentials each time": None not in users and len(set(users)) == len(users),
            "BCHN's kth peers": [p.get("addr") for p in kth_peers],
            "kth synced": RESULTS["setup"].get("kth_synced_bchn_chain")}
    ok = (bool(to_bchn) and bool(kth_peers) and len(via_relay) == len(kth_peers)
          and seen["fresh credentials each time"] and bool(seen["kth synced"]))
    row(803, client, "kth reaches BCHN only through the proxy, with fresh credentials each connection",
        ok, seen)


REGTEST_MAGIC = bytes.fromhex("dab5bffa")
NODE_SHV = 1 << 9
SHV_TO_ROOT = 1 << 0


def p2p_message(command: bytes, payload: bytes) -> bytes:
    return (REGTEST_MAGIC + command.ljust(12, b"\0") + struct.pack("<I", len(payload))
            + sha256d(payload)[:4] + payload)


def p2p_read(reader: LineReader, timeout: float = 15) -> tuple[bytes, bytes] | None:
    head = reader.read_exact(24, timeout)
    if len(head) < 24 or head[:4] != REGTEST_MAGIC:
        return None
    command = head[4:16].rstrip(b"\0")
    size, = struct.unpack("<I", head[16:20])
    payload = reader.read_exact(size, timeout)
    return (command, payload) if len(payload) == size else None


def read_compact(data: bytes, o: int) -> tuple[int, int]:
    first = data[o]
    if first < 0xfd:
        return first, o + 1
    width = {0xfd: 2, 0xfe: 4, 0xff: 8}[first]
    return int.from_bytes(data[o + 1:o + 1 + width], "little"), o + 1 + width


def merkle_root(hashes: list[bytes]) -> bytes:
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def walk_proof(leaf: bytes, index: int, siblings: list[bytes]) -> bytes:
    node = leaf
    for sibling in siblings:
        node = sha256d(sibling + node) if index & 1 else sha256d(node + sibling)
        index >>= 1
    return node


SHV_BY_HASH = 1 << 1


def shv_request(kind: int, commitment: int, block: int | bytes) -> bytes:
    """One getshv request: the type, the commitment height as a CompactSize,
    then the block's 32-byte hash (type bit 1 set) or its height as a
    CompactSize."""
    if kind & SHV_BY_HASH:
        return bytes([kind]) + compact_size(commitment) + block
    return bytes([kind]) + compact_size(commitment) + compact_size(block)


def parse_shv(payload: bytes) -> list[dict]:
    """The entries of an shv message: a status byte each, and for status 0
    (found) the type, both heights, the header, the proof and the target."""
    count, o = read_compact(payload, 0)
    entries = []
    for _ in range(count):
        status = payload[o]; o += 1
        if status != 0:
            entries.append({"status": status})
            continue
        kind = payload[o]; o += 1
        commitment, o = read_compact(payload, o)
        block_height, o = read_compact(payload, o)
        header = payload[o:o + 80]; o += 80
        size, o = read_compact(payload, o)
        proof = [payload[o + 32 * i:o + 32 * (i + 1)] for i in range(size)]; o += 32 * size
        target = payload[o:o + 32]; o += 32
        entries.append({"status": 0, "type": kind, "commitment": commitment, "height": block_height,
                        "header": header, "proof": proof, "target": target})
    if o != len(payload):
        raise ValueError(f"shv payload has {len(payload) - o} bytes after its entries")
    return entries


def shv_light_client(s: dict) -> None:
    """An SHV light client (CHIP-2026-02) over P2P, with the message points
    agreed in the CHIP review: one shv answers one getshv with a status per
    request, and heights in requests are CompactSize. The bytes are the ones
    another SHV server sends for the same requests, so one client works
    against either. kth
    advertises NODE_SHV and answers a proof to the root and a proof to a peak
    for a block; the header is the block at that height, and both proofs lead
    to roots computed here from BCHN's own block hashes. Every refused request
    gets its status byte, in order, an empty getshv gets an shv with no
    entries, and so does one of 101 requests."""
    client = "SHV light client (getshv over P2P)"
    if not SHV:
        return
    # kth's connected tip; the hashes up to it come from BCHN, on the same chain.
    tip = call(KTH, "getblockcount")[0]
    hashes = [bytes.fromhex(call(BCHN, "getblockhash", h)[0])[::-1] for h in range(tip + 1)]
    height = 5
    seen: dict = {"commitment height": tip, "height": height}
    statuses: dict = {}
    # Each refusal, and its status, in the order sent.
    refused = [
        (shv_request(4, tip, height), 4),                          # reserved type bit
        (shv_request(0, height - 1, height), 2),                   # block above the commitment
        (shv_request(0, tip + 1, height), 3),                      # commitment above the tip
        (shv_request(0, tip + 5, tip + 5), 1),                     # no such block, and the commitment too
        (shv_request(SHV_BY_HASH, tip, b"\x11" * 32), 1),          # unknown hash
        (shv_request(SHV_BY_HASH, height - 1, hashes[height]), 2),  # hash of a block above the commitment
    ]
    try:
        with socket.create_connection(("127.0.0.1", KTH_P2P_PORT), timeout=20) as sock:
            reader = LineReader(sock)
            address = struct.pack("<Q", 0) + bytes(10) + b"\xff\xff" + socket.inet_aton("127.0.0.1") + struct.pack(">H", KTH_P2P_PORT)
            version = (struct.pack("<iQq", 70016, 0, int(time.time())) + address + address
                       + struct.pack("<Q", 0x5348565f) + b"\x0dknuth-rpc-e2e" + struct.pack("<i", 0) + b"\x00")
            sock.sendall(p2p_message(b"version", version))
            services = None
            deadline = time.time() + 30
            while time.time() < deadline:
                message = p2p_read(reader)
                if message is None:
                    break
                command, payload = message
                if command == b"version":
                    services, = struct.unpack("<Q", payload[4:12])
                    sock.sendall(p2p_message(b"verack", b""))
                    break
                if command == b"ping":
                    sock.sendall(p2p_message(b"pong", payload))

            def exchange(payload: bytes) -> bytes | None:
                """Send one getshv and return the payload of the shv that answers it."""
                sock.sendall(p2p_message(b"getshv", payload))
                until = time.time() + 30
                while time.time() < until:
                    message = p2p_read(reader)
                    if message is None:
                        return None
                    command, body = message
                    if command == b"ping":
                        sock.sendall(p2p_message(b"pong", body))
                    elif command == b"shv":
                        return body
                return None

            seen["NODE_SHV advertised"] = services is not None and bool(services & NODE_SHV)
            proofs = exchange(b"\x02" + shv_request(SHV_TO_ROOT, tip, height) + shv_request(0, tip, height))
            replies = parse_shv(proofs) if proofs is not None else []
            leaf = hashes[height]
            root = merkle_root(hashes[:tip + 1])
            checks = []
            for entry in replies:
                if entry["status"] != 0:
                    checks.append({"status": entry["status"]})
                    continue
                check = {"type": entry["type"], "commitment": entry["commitment"], "height": entry["height"],
                         "header is the block": sha256d(entry["header"]) == leaf,
                         "proof leads to target": walk_proof(leaf, entry["height"], entry["proof"]) == entry["target"]}
                if entry["type"] & SHV_TO_ROOT:
                    check["target is the Merkle root of BCHN's hashes"] = entry["target"] == root
                checks.append(check)
            seen["replies"] = checks

            # The refusals, then a proof by hash, all in one getshv: one entry
            # each, in order, the refusals one status byte each.
            batch = (compact_size(len(refused) + 1) + b"".join(r for r, _ in refused)
                     + shv_request(SHV_BY_HASH | SHV_TO_ROOT, tip, hashes[height]))
            answer = exchange(batch)
            statuses["answered"] = answer is not None
            if answer is not None:
                expected = compact_size(len(refused) + 1) + bytes(status for _, status in refused)
                statuses["refusals byte for byte"] = answer.startswith(expected)
                entries = parse_shv(answer)
                statuses["statuses"] = [e["status"] for e in entries]
                last = entries[-1] if entries else {}
                statuses["proof by hash"] = (last.get("status") == 0 and last.get("height") == height
                                             and sha256d(last.get("header", b"")) == leaf
                                             and last.get("target") == root
                                             and walk_proof(leaf, height, last.get("proof", [])) == root)
            statuses["empty getshv, empty shv"] = exchange(b"\x00") == b"\x00"
            oversized = compact_size(101) + shv_request(0, tip, height) * 101
            statuses["101 requests, empty shv"] = exchange(oversized) == b"\x00"
    except (OSError, ValueError, IndexError) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    replies_ok = len(seen.get("replies", [])) == 2 and all(
        "status" not in e and all(v for k, v in e.items() if isinstance(v, bool)) for e in seen["replies"])
    to_root = [e for e in seen.get("replies", []) if e.get("type", 0) & SHV_TO_ROOT]
    row(1001, client, "kth advertises NODE_SHV; both proofs verify and the root equals the Merkle root "
        "of BCHN's block hashes", bool(seen.get("NODE_SHV advertised")) and replies_ok and len(to_root) == 1, seen)
    statuses_ok = (statuses.get("refusals byte for byte") is True and statuses.get("proof by hash") is True
                   and statuses.get("statuses") == [status for _, status in refused] + [0]
                   and statuses.get("empty getshv, empty shv") is True
                   and statuses.get("101 requests, empty shv") is True)
    row(1006, client, "one shv answers each getshv: refused requests get their status bytes in order, "
        "an empty or oversized getshv gets an shv with no entries", statuses_ok,
        statuses if "error" not in seen else {**statuses, "error": seen["error"]})


def kth_log_lines(container: str, pattern: str, limit: int = 12) -> list[str]:
    copy = Path(f"/tmp/{container}-debug.log")
    subprocess.run(["docker", "cp", f"{container}:/data/kth-debug.log", str(copy)], capture_output=True)
    text = copy.read_text(errors="replace") if copy.exists() else ""
    return [line[:300] for line in text.splitlines() if re.search(pattern, line)][:limit]


def i2p_second_node(s: dict) -> None:
    """kth and one i2pd router. BCHN is reachable only as an I2P destination
    that the harness publishes through the router's SAM bridge, and kth's only
    configured peer is that I2P address: kth syncs BCHN's chain through I2P,
    and BCHN's kth connections all come from the harness's I2P relay. A second
    kth (B) whose only peer is kth's own I2P address connects to it: kth accepts
    the stream, each side knows the other by its I2P address, and kth announces
    its own address to B in addrv2."""
    client = "node operators over I2P (SAM bridge of i2pd)"
    if not I2P:
        return
    seen: dict = {"BCHN's I2P address": BCHN_I2P.address if BCHN_I2P else None,
                  "kth synced BCHN's chain": RESULTS["setup"].get("kth_synced_bchn_chain"),
                  "kth height": call(KTH, "getblockcount")[0], "BCHN height": s["height"]}
    peers = json.loads(cli("getpeerinfo"))
    kth_peers = [p for p in peers if "knuth" in p.get("subver", "").lower()]
    relay_ports = set(BCHN_I2P.relay_ports if BCHN_I2P else [])
    seen["BCHN's kth peers"] = [p.get("addr") for p in kth_peers]
    seen["all through the I2P relay"] = bool(kth_peers) and all(
        int(p.get("addr", ":0").rsplit(":", 1)[1]) in relay_ports for p in kth_peers)
    seen["kth's peer events"] = kth_log_lines("kth", r"CONNECTED: .*b32\.i2p", 4)
    synced = bool(seen["kth synced BCHN's chain"] and seen["all through the I2P relay"]
                  and any(BCHN_I2P.address in line for line in seen["kth's peer events"]))
    row(1002, client, "kth's only peer is BCHN's I2P address; it syncs BCHN's chain through i2pd", synced, seen)

    check = "a second kth reaches kth over I2P; kth accepts it and announces its own I2P address"
    seen = {}
    address, deadline = None, time.monotonic() + 600
    while address is None and time.monotonic() < deadline:
        found = kth_log_lines("kth", r"\[sam\] I2P address: [a-z2-7]{52}\.b32\.i2p")
        address = found[-1].rsplit(" ", 1)[-1] if found else None
        if address is None:
            time.sleep(5)
    seen["kth's I2P address"] = address
    if address is None:
        seen["kth's SAM log"] = kth_log_lines("kth", r"\[sam\]")
        return row(1002, client, check, False, seen)
    start_kth_b(f"{address}:0", "--net.i2psam", f"127.0.0.1:{SAM_PORT}", "--net.i2p_accept_incoming", "false")
    handshake, started = [], time.monotonic()
    while not handshake and time.monotonic() - started < 600:
        time.sleep(5)
        handshake = kth_log_lines("kth-b", rf"Handshake complete with \[{address}\]", 1)
    b_address = kth_log_lines("kth-b", r"\[sam\] I2P address: ", 1)
    b_address = b_address[0].rsplit(" ", 1)[-1] if b_address else None
    seen.update({"B's handshake with kth": handshake, "seconds": round(time.monotonic() - started),
                 "B's I2P address": b_address,
                 "kth's event for B": kth_log_lines("kth", rf"CONNECTED: {b_address}", 1) if b_address else [],
                 "addrv2 from kth to B": kth_log_lines("kth-b", rf"Received addrv2 from \[{address}", 1)})
    row(1002, client, check, bool(handshake and seen["kth's event for B"] and seen["addrv2 from kth to B"]), seen)


def start_kth_b(peer: str, *extra: str) -> None:
    subprocess.run(["docker", "run", "--detach", "--name", "kth-b", "--network", "host",
                    "--platform", "linux/amd64", "--entrypoint", "/usr/local/bin/kth", "knuth-e2e",
                    "--init_run", "--network", "regtest", "--node.display", "daemon",
                    "--db.directory", "/data/e2e", "--db.db_mode", "full",
                    "--net.inbound_port", "18556", "--net.inbound_connections", "0",
                    "--net.outbound_connections", "1", "--net.peer", peer, *extra,
                    "--rpc.enabled", "true", "--rpc.bind", "127.0.0.1", "--rpc.port", str(KTH_B[1]),
                    "--rpc.user", "e2e", "--rpc.password", "e2e",
                    "--log.debug_file", "/data/kth-debug.log", "--log.error_file", "/data/kth-error.log"],
                   check=True)


def serve_second_node(s: dict) -> None:
    """A second kth (B) whose only peer is kth (A), over TCP, with no listener
    of its own: B must reach A's tip and hold the same best block. Every header
    and block B has, A served; BCHN never has B as a peer."""
    client = "node operators syncing from a kth node"
    if not SERVE:
        return
    tip = call(KTH, "getblockcount")[0]
    seen: dict = {"A height": tip}
    started = time.monotonic()
    start_kth_b(f"127.0.0.1:{KTH_P2P_PORT}")
    reached = False
    while time.monotonic() - started < 600:
        info, error = call(KTH_B, "getblockchaininfo")
        if error is None and info and info.get("blocks", -1) >= tip:
            reached = True
            break
        time.sleep(3)
    seen["B reached A's height"] = reached
    seen["seconds"] = round(time.monotonic() - started)
    seen["B height"] = (call(KTH_B, "getblockchaininfo")[0] or {}).get("blocks")
    seen["B's tip is A's"] = call(KTH_B, "getbestblockhash")[0] == call(KTH, "getbestblockhash")[0]
    peers = json.loads(cli("getpeerinfo"))
    seen["BCHN's kth peers"] = [p.get("addr") for p in peers if "knuth" in p.get("subver", "").lower()]
    # BCHN is A's peer at port 18444; B's connection comes from another port.
    seen["requests A had from B"] = {
        "getheaders": len(kth_log_lines("kth", r"Dispatching 'getheaders' from \[127\.0\.0\.1:(?!18444)", 10000)),
        "getdata": len(kth_log_lines("kth", r"Dispatching 'getdata' from \[127\.0\.0\.1:(?!18444)", 10000))}
    seen["B's peer events"] = kth_log_lines("kth-b", r"CONNECTED: ", 3)
    row(1003, client, "a second kth whose only peer is kth syncs kth's chain and holds the same tip",
        reached and seen["B's tip is A's"] and len(seen["BCHN's kth peers"]) == 1, seen)


def bchn_b_cli(*args) -> str:
    return subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_B_DIR}", "-rpcuser=e2e",
                           "-rpcpassword=e2e", f"-rpcport={BCHN_B[1]}", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def announce_second_node(s: dict) -> None:
    """A second BCHN (B) whose only peer is kth. Once B has kth's chain, each
    block BCHN mines must reach B within 20 seconds. BCHN asks a peer for new
    headers only when it is told of a block, or after its 30-minute stale-tip
    check, so B can only hear of these blocks from kth's announcements."""
    client = "nodes whose peer is kth"
    if not ANNOUNCE:
        return
    # B connects only now, after kth has synced: the height kth gives B in its
    # version message must be kth's tip, not the height kth started at.
    height_at_connect = call(KTH, "getblockcount")[0]
    BCHN_B_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bitcoind", "-regtest", f"-datadir={BCHN_B_DIR}", "-daemon", "-server",
                    "-rpcuser=e2e", "-rpcpassword=e2e", f"-rpcport={BCHN_B[1]}", "-port=18454",
                    "-listen=0", "-dnsseed=0", "-debug=net",
                    f"-connect=127.0.0.1:{KTH_P2P_PORT}"], check=True)
    wait_for(BCHN_B, "BCHN B")
    seen: dict = {}
    tip = call(KTH, "getbestblockhash")[0]
    started = time.monotonic()
    synced = False
    while time.monotonic() - started < 300:
        if call(BCHN_B, "getbestblockhash")[0] == tip:
            synced = True
            break
        time.sleep(2)
    seen["B synced kth's chain"] = synced
    seen["sync seconds"] = round(time.monotonic() - started)

    # From here on, a block reaches B only if kth announces it.
    log = BCHN_B_DIR / "regtest" / "debug.log"
    offset = log.stat().st_size if log.exists() else 0
    addr = wallet_address()
    delays: list = []
    for _ in range(5):
        new = json.loads(cli("generatetoaddress", "1", addr))[0]
        mined = time.monotonic()
        reached = None
        while time.monotonic() - mined < 20:
            if call(BCHN_B, "getbestblockhash")[0] == new:
                reached = round(time.monotonic() - mined, 1)
                break
            time.sleep(0.5)
        delays.append(reached)
    seen["seconds for each new block to reach B"] = delays
    text = log.read_text(errors="replace")[offset:] if log.exists() else ""
    seen["announcements B received"] = {
        "headers": len(re.findall(r"received: headers ", text)),
        "inv of a block": len(re.findall(r"got inv: block ", text)),
        # A high-bandwidth BIP152 peer is announced to with the block itself.
        "cmpctblock": len(re.findall(r"received: cmpctblock ", text))}
    peers = json.loads(bchn_b_cli("getpeerinfo"))
    seen["B's peers"] = [p.get("subver") for p in peers]
    ok = (synced and all(d is not None for d in delays) and len(peers) == 1
          and "knuth" in (peers[0].get("subver") or "").lower()
          and sum(seen["announcements B received"].values()) > 0)
    row(1004, client, "a BCHN whose only peer is kth hears of each new block from kth", ok, seen)

    start = {"kth's height when B connected": height_at_connect,
             "startingheight B has for kth": peers[0].get("startingheight") if peers else None,
             "kth's start heights": kth_log_lines("kth", r"Node start heights", 2)}
    row(1007, "nodes and light wallets that connect to kth later",
        "kth's version message gives its height at the time of connection",
        height_at_connect is not None and start["startingheight B has for kth"] == height_at_connect, start)


def start_bchn_b() -> None:
    """BCHN B: a second BCHN whose only peer is kth. Started once."""
    if call(BCHN_B, "getblockcount")[1] is None:
        return
    BCHN_B_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bitcoind", "-regtest", f"-datadir={BCHN_B_DIR}", "-daemon", "-server",
                    "-rpcuser=e2e", "-rpcpassword=e2e", f"-rpcport={BCHN_B[1]}", "-port=18454",
                    "-listen=0", "-dnsseed=0", "-debug=net", "-debug=cmpctblock",
                    f"-connect=127.0.0.1:{KTH_P2P_PORT}"], check=True)
    wait_for(BCHN_B, "BCHN B")


def per_msg(peers: list, field: str) -> dict:
    """bytessent_per_msg or bytesrecv_per_msg of the one knuth peer in a getpeerinfo."""
    for p in peers:
        if "knuth" in (p.get("subver") or "").lower():
            return dict(p.get(field) or {})
    return {}


def compact_blocks_both_ways(s: dict) -> None:
    """BIP152 between BCHN and kth, in both directions. BCHN mines blocks whose
    transactions kth already holds. BCHN must send kth each block as a
    cmpctblock and no full block; and BCHN B, whose only peer is kth, must get
    each block from kth as a cmpctblock and no full block. B has no mempool of
    its own here, so it asks kth for the transactions with getblocktxn."""
    client = "BCHN peers of kth (BIP152)"
    if not COMPACT:
        return
    start_bchn_b()
    seen: dict = {}
    tip = call(KTH, "getbestblockhash")[0]
    started = time.monotonic()
    synced = False
    while time.monotonic() - started < 300:
        if call(BCHN_B, "getbestblockhash")[0] == tip:
            synced = True
            break
        time.sleep(2)
    seen["B synced kth's chain"] = synced
    addr = wallet_address()
    keys = ("cmpctblock", "getblocktxn", "blocktxn", "block", "getdata")
    rounds: list = []
    # Paying kth's own admission price, so that the block's transactions are
    # in kth's mempool before it is mined, whatever kth's fee policy.
    cli("settxfee", "0.000011")
    for _ in range(3):
        txids = [cli("sendtoaddress", a, "0.01") for a in s["others"][:2]]
        waited = time.monotonic()
        in_kth = False
        while time.monotonic() - waited < 45:
            pool = call(KTH, "getrawmempool")[0] or []
            if all(t in pool for t in txids):
                in_kth = True
                break
            time.sleep(1)
        a0 = per_msg(json.loads(cli("getpeerinfo")), "bytessent_per_msg")
        b0 = per_msg(json.loads(bchn_b_cli("getpeerinfo")), "bytesrecv_per_msg")
        new = json.loads(cli("generatetoaddress", "1", addr))[0]
        mined = time.monotonic()
        kth_at = b_at = None
        while time.monotonic() - mined < 30 and (kth_at is None or b_at is None):
            if kth_at is None and call(KTH, "getbestblockhash")[0] == new:
                kth_at = round(time.monotonic() - mined, 1)
            if b_at is None and call(BCHN_B, "getbestblockhash")[0] == new:
                b_at = round(time.monotonic() - mined, 1)
            time.sleep(0.25)
        time.sleep(2)  # let late counters settle
        a1 = per_msg(json.loads(cli("getpeerinfo")), "bytessent_per_msg")
        b1 = per_msg(json.loads(bchn_b_cli("getpeerinfo")), "bytesrecv_per_msg")
        rounds.append({
            "transactions in kth's mempool before mining": in_kth,
            "seconds to kth": kth_at, "seconds to B": b_at,
            "bytes BCHN sent kth": {k: a1.get(k, 0) - a0.get(k, 0) for k in keys},
            "bytes B received from kth": {k: b1.get(k, 0) - b0.get(k, 0) for k in keys}})
    cli("settxfee", "0")
    seen["rounds"] = rounds
    ok = synced and all(
        r["transactions in kth's mempool before mining"] and r["seconds to kth"] is not None
        and r["seconds to B"] is not None
        and r["bytes BCHN sent kth"]["cmpctblock"] > 0 and r["bytes BCHN sent kth"]["block"] == 0
        and r["bytes B received from kth"]["cmpctblock"] > 0
        and r["bytes B received from kth"]["block"] == 0
        for r in rounds)
    row(1005, client, "blocks move between BCHN and kth as compact blocks both ways, with no full block",
        ok, seen)


def row24_mining_protocol(s: dict) -> None:
    """SRI's mining_device, an independent SV2 client, opens a Mining Protocol
    channel on kth over Noise, pinned to the authority key kth prints. kth
    builds jobs from its own template paying sv2.mining.address; the device
    mines them, and a share that meets regtest's target must become a block
    that BCHN accepts, with a coinbase paying that address."""
    client = "SRI mining_device"
    if not (SV2 and NOISE and SV2_MINING):
        return
    seen: dict = {"payout address": MINING_ADDRESS}
    key = NOISE_KEYS.get("printed by kth")
    seen["pinned authority"] = key
    before = int(cli("getblockcount"))
    log = OUT / "mining-device.log"
    with open(log, "w") as out:
        device = subprocess.Popen([MINING_DEVICE, "--address-pool", f"127.0.0.1:{SV2_PORT}",
                                   *(["--pubkey-pool", key] if key else []),
                                   "--id-user", "knuth-rpc-e2e", "--cores", "1"],
                                  stdout=out, stderr=subprocess.STDOUT)
        started = time.monotonic()
        while time.monotonic() - started < 240 and (call(KTH, "getblockcount")[0] or 0) < before + 2:
            if device.poll() is not None:
                break
            time.sleep(1)
        seen["device still running"] = device.poll() is None
        device.terminate()
        try:
            device.wait(10)
        except subprocess.TimeoutExpired:
            device.kill()
    seen["seconds"] = round(time.monotonic() - started)
    # Without block announcements in this stack, kth's blocks are handed to
    # BCHN with submitblock, as row 23 does; BCHN validates each one.
    kth_height = call(KTH, "getblockcount")[0] or before
    # With block announcements (#814) BCHN fetches kth's blocks itself.
    waited = time.monotonic()
    while time.monotonic() - waited < 30 and int(cli("getblockcount")) < kth_height:
        time.sleep(1)
    seen["BCHN fetched kth's blocks over P2P"] = int(cli("getblockcount")) >= kth_height
    submitted = []
    for height in range(before + 1, kth_height + 1):
        hash_ = call(KTH, "getblockhash", height)[0]
        raw = call(KTH, "getblock", hash_, 0)[0]
        if int(cli("getblockcount")) >= height and cli("getblockhash", str(height)) == hash_:
            submitted.append({"height": height, "BCHN submitblock": "already had it"})
            continue
        accepted, error = call(BCHN, "submitblock", raw)
        submitted.append({"height": height, "BCHN submitblock": accepted if error is None else error})
    seen["kth blocks handed to BCHN"] = submitted
    after = int(cli("getblockcount"))
    blocks = []
    for height in range(before + 1, after + 1):
        block = json.loads(cli("getblock", cli("getblockhash", str(height)), "2"))
        coinbase = block["tx"][0]
        paid = [a for o in coinbase["vout"]
                for a in (o["scriptPubKey"].get("addresses") or [o["scriptPubKey"].get("address")])
                if a]
        blocks.append({"height": height, "pays the configured address": MINING_ADDRESS in paid,
                       "coinbase value": sum(o["value"] for o in coinbase["vout"])})
    seen["new BCHN blocks"] = blocks
    seen["kth tip is BCHN's"] = call(KTH, "getbestblockhash")[0] == cli("getbestblockhash")
    text = log.read_text(errors="replace") if log.exists() else ""
    seen["device log lines"] = {
        "share accepted": len(re.findall(r"(?i)SubmitSharesSuccess|share.{0,20}accepted", text)),
        "new job": len(re.findall(r"(?i)NewMiningJob|new job", text)),
        "error": len(re.findall(r"(?i)error", text))}
    ok = (bool(blocks) and all(b["pays the configured address"] for b in blocks)
          and seen["kth tip is BCHN's"]
          and all(r["BCHN submitblock"] in (None, "already had it") for r in submitted))
    row(24, client, "an independent SV2 client mines on kth's Mining Protocol channel and finds blocks BCHN accepts",
        ok, seen)


# The fields BCH P2Pool's BlockTemplate reads without a serde default, and their
# JSON types (p2poolv2_lib/src/stratum/work/block_template.rs).
P2POOL_TEMPLATE = {"version": int, "previousblockhash": str, "transactions": list, "coinbaseaux": dict,
                   "coinbasevalue": int, "longpollid": str, "target": str, "mintime": int, "mutable": list,
                   "noncerange": str, "sigoplimit": int, "sizelimit": int, "curtime": int, "bits": str,
                   "height": int}
P2POOL_TEMPLATE_TX = ("data", "txid", "fee", "sigops")


def pr874_long_polling(s: dict) -> None:
    """Pools that wait on long polling (BIP22), and BCH P2Pool, whose template
    type requires longpollid: the template carries it, a request sending it
    back is held until BCHN mines a block and then answered with that block's
    template, and a request naming the replaced tip is answered at once."""
    if not LONGPOLL:
        return
    client = "pools on long polling, BCH P2Pool"
    call(KTH, "sendrawtransaction", s["fresh_tx_pr874"])  # a transaction whose fields are checked too
    time.sleep(7)  # past kth's template refresh window under mempool change
    template, error = call(KTH, "getblocktemplate", {})
    template = template if isinstance(template, dict) else {}
    wrong = [k for k, kind in P2POOL_TEMPLATE.items()
             if not isinstance(template.get(k), kind) or isinstance(template.get(k), bool)]
    tx_missing = sorted({k for t in template.get("transactions", []) for k in P2POOL_TEMPLATE_TX if k not in t})
    row(874, client, "getblocktemplate carries longpollid and every field P2Pool's template type requires",
        error is None and not wrong and not tx_missing,
        {"missing or mistyped": wrong, "transaction fields missing": tx_missing,
         "transactions": len(template.get("transactions", [])), "longpollid": template.get("longpollid"),
         "error": error})
    if error is not None or not isinstance(template.get("longpollid"), str):
        return row(874, client, "a request with the template's longpollid is held until a block, then answered",
                   False, "no longpollid to send back")

    answer: dict = {}

    def hold() -> None:
        started = time.monotonic()
        answer["template"], answer["error"] = call(KTH, "getblocktemplate", {"longpollid": template["longpollid"]})
        answer["seconds"] = round(time.monotonic() - started, 1)

    waiter = threading.Thread(target=hold, daemon=True)
    waiter.start()
    waiter.join(5)
    held = waiter.is_alive()  # kth's tip does not change in these 5 s
    mined = time.monotonic()
    new_tip = json.loads(cli("generatetoaddress", "1", wallet_address()))[0]
    waiter.join(60)
    reply = answer.get("template") if isinstance(answer.get("template"), dict) else {}
    after = round(time.monotonic() - mined, 1) if not waiter.is_alive() else None
    row(874, client, "a request with the template's longpollid is held until a block, then answered with its template",
        held and after is not None and answer.get("error") is None and reply.get("previousblockhash") == new_tip
        and reply.get("longpollid") != template["longpollid"],
        {"held for 5 s before the block": held, "seconds from BCHN's block to the answer": after,
         "answer's previousblockhash is the new block": reply.get("previousblockhash") == new_tip,
         "new longpollid": reply.get("longpollid"), "error": answer.get("error")})

    started = time.monotonic()
    stale, error = call(KTH, "getblocktemplate", {"longpollid": template["longpollid"]})
    seconds = round(time.monotonic() - started, 1)
    row(874, client, "a request naming the replaced tip is answered at once",
        error is None and isinstance(stale, dict) and seconds < 5,
        {"seconds": seconds, "previousblockhash": (stale or {}).get("previousblockhash") if error is None else error})


def rows(s: dict) -> None:
    for check in (row10_relay_in, row17_rawmempool_verbose, row11_relay_out, pr795_fee_filter,
                  row6_validateaddress, row7_decoderawtransaction, row8_blockchaininfo, row9_chain,
                  row12_block_reads, row13_gettxout, row14_getnetworkinfo, row15_help, row16_confirmations,
                  row18_estimatefee, row19_networkhashps, row20_getchaintips, row5_merkle, row4_getblocktemplate,
                  row1_accept_found_block, row2_found_block_reaches_peers, row3_submitblock,
                  row21_template_provider, row23_template_flow, row22_noise, pr803_outbound_proxy,
                  shv_light_client, compact_filter_light_client, neutrino_wallet_client,
                  script_filter_light_client, explorer_p2p_client, vault_wallet_filters, rpa_wallet_filters,
                  bloom_filter_light_client, i2p_second_node, serve_second_node, announce_second_node,
                  compact_blocks_both_ways, row24_mining_protocol, electrum_bridge_parity,
                  pr874_long_polling):
        try:
            check(s)
        except Exception as error:  # one broken check must not hide the others
            row(0, check.__name__, "crashed", False, f"{type(error).__name__}: {error}")



# ---- light wallets over P2P: compact filters and bloom filters ---------------------

NODE_BLOOM = 1 << 2
NODE_CF = 1 << 8
GCS_P = 19
GCS_M = 784931
MASK64 = (1 << 64) - 1


def siphash24(k0: int, k1: int, data: bytes) -> int:
    v0 = k0 ^ 0x736f6d6570736575
    v1 = k1 ^ 0x646f72616e646f6d
    v2 = k0 ^ 0x6c7967656e657261
    v3 = k1 ^ 0x7465646279746573

    def rotl(x: int, b: int) -> int:
        return ((x << b) | (x >> (64 - b))) & MASK64

    def rounds(n: int) -> None:
        nonlocal v0, v1, v2, v3
        for _ in range(n):
            v0 = (v0 + v1) & MASK64; v1 = rotl(v1, 13); v1 ^= v0; v0 = rotl(v0, 32)
            v2 = (v2 + v3) & MASK64; v3 = rotl(v3, 16); v3 ^= v2
            v0 = (v0 + v3) & MASK64; v3 = rotl(v3, 21); v3 ^= v0
            v2 = (v2 + v1) & MASK64; v1 = rotl(v1, 17); v1 ^= v2; v2 = rotl(v2, 32)

    whole = len(data) - len(data) % 8
    for o in range(0, whole, 8):
        m = int.from_bytes(data[o:o + 8], "little")
        v3 ^= m
        rounds(2)
        v0 ^= m
    last = int.from_bytes(data[whole:], "little") | ((len(data) & 0xff) << 56)
    v3 ^= last
    rounds(2)
    v0 ^= last
    v2 ^= 0xff
    rounds(4)
    return v0 ^ v1 ^ v2 ^ v3


def gcs_values(filter_bytes: bytes) -> tuple[int, list[int]]:
    """The item count of a served filter and its values, in order: each is the
    one before plus a Golomb-Rice coded difference, most significant bit first."""
    n, o = read_compact(filter_bytes, 0)
    bits = int.from_bytes(filter_bytes[o:], "big")
    left = (len(filter_bytes) - o) * 8
    values = []
    last = 0
    for _ in range(n):
        quotient = 0
        while True:
            if left == 0:
                raise ValueError("filter ends inside a value")
            left -= 1
            if not (bits >> left) & 1:
                break
            quotient += 1
        if left < GCS_P:
            raise ValueError("filter ends inside a remainder")
        left -= GCS_P
        remainder = (bits >> left) & ((1 << GCS_P) - 1)
        last += (quotient << GCS_P) | remainder
        values.append(last)
    return n, values


def gcs_expected(block_hash: bytes, items: set[bytes]) -> list[int]:
    """What a filter of these items holds under the block's key."""
    k0 = int.from_bytes(block_hash[:8], "little")
    k1 = int.from_bytes(block_hash[8:16], "little")
    modulus = len(items) * GCS_M
    return sorted({(siphash24(k0, k1, item) * modulus) >> 64 for item in items})


def regular_filter_items(block: dict) -> set[bytes]:
    """The items of the regular filter, from BCHN's own reading of the block:
    the outpoint of every input after the coinbase, and every output script
    that is not empty."""
    items: set[bytes] = set()
    for at, tx in enumerate(block["tx"]):
        if at != 0:
            for vin in tx["vin"]:
                items.add(bytes.fromhex(vin["txid"])[::-1] + struct.pack("<I", vin["vout"]))
        for vout in tx["vout"]:
            script = bytes.fromhex(vout["scriptPubKey"]["hex"])
            if script:
                items.add(script)
    return items


class LightPeer:
    """A P2P connection that has finished the version handshake as a wallet
    would: it answers pings and skips everything it did not ask for."""

    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=20)
        self.reader = LineReader(self.sock)
        address = (struct.pack("<Q", 0) + bytes(10) + b"\xff\xff" + socket.inet_aton("127.0.0.1")
                   + struct.pack(">H", port))
        version = (struct.pack("<iQq", 70016, 0, int(time.time())) + address + address
                   + struct.pack("<Q", 0x6c69676874) + b"\x0dknuth-rpc-e2e" + struct.pack("<i", 0) + b"\x00")
        self.sock.sendall(p2p_message(b"version", version))
        self.services = None
        deadline = time.time() + 30
        while time.time() < deadline:
            message = p2p_read(self.reader)
            if message is None:
                break
            command, payload = message
            if command == b"version":
                self.services, = struct.unpack("<Q", payload[4:12])
                self.sock.sendall(p2p_message(b"verack", b""))
                break
            if command == b"ping":
                self.sock.sendall(p2p_message(b"pong", payload))

    def send(self, command: bytes, payload: bytes) -> None:
        self.sock.sendall(p2p_message(command, payload))

    def collect(self, wanted: tuple[bytes, ...], count: int | None, quiet: float = 4.0, limit: float = 90.0):
        """Messages of the wanted commands, in order, until `count` of them
        arrived or nothing wanted came for `quiet` seconds."""
        out = []
        end = time.time() + limit
        until = time.time() + quiet
        while time.time() < min(end, until) and (count is None or len(out) < count):
            message = p2p_read(self.reader, max(0.2, min(end, until) - time.time()))
            if message is None:
                break
            command, payload = message
            if command == b"ping":
                self.sock.sendall(p2p_message(b"pong", payload))
            elif command in wanted:
                out.append((command, payload))
                until = time.time() + quiet
        return out

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def compact_filter_light_client(s: dict) -> None:
    """A wallet that syncs compact filters from kth over P2P, as a Neutrino
    wallet does: the filter header checkpoints, the filter hashes of the whole
    chain in ranges of 2,000, then filters. Every filter header is chained
    here from the hashes kth sends; every filter read is hashed against them;
    and filters are decoded and compared, value for value, with the filter
    built here from BCHN's own reading of the same blocks."""
    client = "compact filter light client (getcfcheckpt, getcfheaders, getcfilters over P2P)"
    if not FILTERS:
        return
    tip = call(KTH, "getblockcount")[0]
    hashes = [bytes.fromhex(call(BCHN, "getblockhash", h)[0])[::-1] for h in range(tip + 1)]
    seen: dict = {"tip": tip}
    ok = False
    peer = None
    try:
        peer = LightPeer(KTH_P2P_PORT)
        seen["NODE_CF advertised"] = peer.services is not None and bool(peer.services & NODE_CF)

        # The checkpoints first, as a wallet asks.
        peer.send(b"getcfcheckpt", b"\x00" + hashes[tip])
        reply = peer.collect((b"cfcheckpt",), 1, quiet=120.0, limit=600.0)
        checkpoints = []
        if reply:
            body = reply[0][1]
            count, o = read_compact(body, 33)
            checkpoints = [body[o + 32 * i:o + 32 * (i + 1)] for i in range(count)]
            seen["cfcheckpt"] = {"type": body[0], "stop is the tip": body[1:33] == hashes[tip], "headers": count,
                                 "whole": o + 32 * count == len(body)}

        # Every filter hash, 2,000 blocks a request, chained from all zeros.
        header = bytes(32)
        filter_hashes: list[bytes] = []
        headers: list[bytes] = []
        batches_ok = True
        start = 0
        while start <= tip:
            stop = min(tip, start + 1999)
            peer.send(b"getcfheaders", b"\x00" + struct.pack("<I", start) + hashes[stop])
            reply = peer.collect((b"cfheaders",), 1, quiet=60.0, limit=300.0)
            if not reply:
                batches_ok = False
                seen["cfheaders missing at"] = start
                break
            body = reply[0][1]
            count, o = read_compact(body, 65)
            if (body[0] != 0 or body[1:33] != hashes[stop] or body[33:65] != header
                    or count != stop - start + 1 or o + 32 * count != len(body)):
                batches_ok = False
                seen["cfheaders wrong at"] = start
                break
            for i in range(count):
                filter_hash = body[o + 32 * i:o + 32 * (i + 1)]
                header = sha256d(filter_hash + header)
                filter_hashes.append(filter_hash)
                headers.append(header)
            start = stop + 1
        seen["filter hashes"] = len(filter_hashes)
        seen["cfheaders batches chain"] = batches_ok and len(filter_hashes) == tip + 1
        checkpoints_ok = (batches_ok and len(checkpoints) == tip // 1000
                          and all(checkpoints[i] == headers[(i + 1) * 1000] for i in range(len(checkpoints))))
        seen["checkpoints are the chained headers at each 1000"] = checkpoints_ok

        # Filters: the first blocks, the blocks around the wallet transactions, the tip.
        ranges = [(0, min(tip, 9)), (max(0, s["tx_block_height"] - 2), min(tip, s["tx_block_height"] + 2)),
                  (tip, tip)]
        filters_ok = batches_ok
        compared = 0
        largest = 0
        for first, last in ranges:
            peer.send(b"getcfilters", b"\x00" + struct.pack("<I", first) + hashes[last])
            reply = peer.collect((b"cfilter",), last - first + 1, quiet=60.0, limit=300.0)
            if len(reply) != last - first + 1:
                filters_ok = False
                seen[f"cfilters {first}..{last}"] = len(reply)
                continue
            for i, (_, body) in enumerate(reply):
                height = first + i
                size, o = read_compact(body, 33)
                data = body[o:o + size]
                if (body[0] != 0 or body[1:33] != hashes[height] or o + size != len(body)
                        or not batches_ok or sha256d(data) != filter_hashes[height]):
                    filters_ok = False
                    seen["cfilter wrong at"] = height
                    continue
                block = call(BCHN, "getblock", hashes[height][::-1].hex(), 2)[0]
                items = regular_filter_items(block)
                n, values = gcs_values(data)
                largest = max(largest, n)
                if n != len(items) or values != gcs_expected(hashes[height], items):
                    filters_ok = False
                    seen["cfilter content differs at"] = height
                    continue
                compared += 1
        seen["filters equal to the ones built from BCHN's blocks"] = compared
        seen["most items in one filter"] = largest

        # What is not answered: another filter type, a start above the stop
        # block, a range past the limit, a stop block nobody has.
        silent = True
        # A branch answers the filter types it serves.
        other = UNSERVED_TYPE
        for command, payload in ((b"getcfilters", other + struct.pack("<I", 0) + hashes[tip]),
                                 (b"getcfilters", b"\x00" + struct.pack("<I", 5) + hashes[4]),
                                 (b"getcfheaders", b"\x00" + struct.pack("<I", 3) + b"\x11" * 32),
                                 (b"getcfcheckpt", other + hashes[tip])):
            peer.send(command, payload)
        if tip >= 1000:
            peer.send(b"getcfilters", b"\x00" + struct.pack("<I", 0) + hashes[1000])
        if tip >= 2000:
            peer.send(b"getcfheaders", b"\x00" + struct.pack("<I", 0) + hashes[2000])
        if peer.collect((b"cfilter", b"cfheaders", b"cfcheckpt"), None, quiet=4.0, limit=10.0):
            silent = False
        seen["refused requests get no answer"] = silent
        # And the connection still serves a good one.
        peer.send(b"getcfilters", b"\x00" + struct.pack("<I", tip) + hashes[tip])
        seen["answers after them"] = len(peer.collect((b"cfilter",), 1, quiet=30.0, limit=60.0)) == 1

        # The only thing stored: 64 bytes for every thousand blocks.
        stored = subprocess.run(["docker", "exec", "kth", "sh", "-c",
                                 "wc -c < /data/e2e/compact_filter_checkpoints"],
                                capture_output=True, text=True).stdout.strip()
        seen["checkpoint file bytes"] = stored
        file_ok = stored.isdigit() and int(stored) == 64 * (tip // 1000)

        ok = (bool(seen["NODE_CF advertised"]) and seen["cfheaders batches chain"] and checkpoints_ok
              and filters_ok and compared == sum(last - first + 1 for first, last in ranges)
              and silent and seen["answers after them"] and file_ok)
    except (OSError, ValueError, KeyError, IndexError) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    finally:
        if peer is not None:
            peer.close()
    row(1002, client, "kth advertises NODE_CF; the filter headers of the whole chain chain from zeros and "
        "match the checkpoints; filters hash to them and equal the ones built from BCHN's blocks; bad "
        "requests get no answer; 64 bytes are stored per thousand blocks", ok, seen)


def neutrino_wallet_client(s: dict) -> None:
    """The Neutrino library (gcash/neutrino) with kth as its only peer: it
    syncs block headers and filter headers to the tip, checking the filter
    headers against kth's checkpoints as it does on any network, fetches the
    filter of the block of wallet transactions, finds in it a script that
    block pays and not one it does not, and fetches the block."""
    client = "Neutrino wallet library (gcash/neutrino), kth its only peer"
    if not FILTERS or not NEUTRINO_BIN:
        return
    tip = call(KTH, "getblockcount")[0]
    paid = call(BCHN, "validateaddress", s["others"][0])[0]["scriptPubKey"]
    absent = "76a914" + "ab" * 20 + "88ac"
    seen: dict = {"tip": tip}
    ok = False
    try:
        run = subprocess.run([NEUTRINO_BIN, "-connect", f"127.0.0.1:{KTH_P2P_PORT}", "-height", str(tip),
                              "-block", s["tx_block"], "-script", paid, "-absent", absent,
                              "-timeout", "4m"], capture_output=True, text=True, timeout=420)
        lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
        seen["exit"] = run.returncode
        if lines:
            seen.update(json.loads(lines[-1]))
        else:
            seen["output"] = (run.stdout + run.stderr)[-300:]
        ok = (run.returncode == 0 and bool(seen.get("synced")) and seen.get("block_headers") == tip
              and seen.get("filter_headers") == tip and bool(seen.get("paid_script_found"))
              and not seen.get("other_script_found") and (seen.get("block_transactions") or 0) >= 2)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    row(1004, client, "it syncs block headers and filter headers to kth's tip, takes the filter of the "
        "wallet block, finds a script that block pays and not another, and fetches the block", ok, seen)


def sf_leaf(key: bytes, tx_index: int, index: int, kind: int) -> bytes:
    """A leaf of a script filter's entry tree: the key, the transaction index
    and the input or output index, and the kind (0 a created output's script,
    1 a spent output's script, 2 and 3 the same for a token category, 4 a
    transaction of the block, with index 0)."""
    return key + struct.pack("<II", tx_index, index) + bytes([kind])


def sf_leaf_order(leaf: bytes):
    return (leaf[:32], struct.unpack("<II", leaf[32:40]), leaf[40])


def sf_next_level(level: list[bytes]) -> list[bytes]:
    out = [hashlib.sha256(b"\x01" + level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)]
    if len(level) % 2:
        out.append(level[-1])
    return out


def sf_root(leaves: list[bytes]) -> bytes:
    """The root of the tree: a leaf hashes with 0x00 in front, two nodes with
    0x01, and a node with no partner moves up unchanged."""
    if not leaves:
        return bytes(32)
    level = [hashlib.sha256(b"\x00" + leaf).digest() for leaf in leaves]
    while len(level) > 1:
        level = sf_next_level(level)
    return level[0]


def sf_range_root(count: int, lo: int, run: list[bytes], hashes: list[bytes]) -> bytes:
    """The root a consecutive run of leaves and its edge hashes rebuild, for a
    tree of `count` leaves. Raises when the hashes are too few or too many."""
    if not run or lo + len(run) > count:
        raise ValueError("the run does not fit the tree")
    nodes = [hashlib.sha256(b"\x00" + leaf).digest() for leaf in run]
    left = list(hashes)
    a, b, width = lo, lo + len(run) - 1, count
    while width > 1:
        if a % 2 == 1:
            nodes.insert(0, left.pop(0))
            a -= 1
        if b % 2 == 0 and b + 1 < width:
            nodes.append(left.pop(0))
            b += 1
        nodes = sf_next_level(nodes)
        a //= 2
        b //= 2
        width = (width + 1) // 2
    if left:
        raise ValueError("hashes left over")
    return nodes[0]


def sf_fold(txid: bytes, index: int, branch: list[bytes]) -> bytes:
    """A transaction hash and its Merkle branch, folded to the block's root."""
    node = txid
    for sibling in branch:
        node = sha256d(sibling + node) if index % 2 else sha256d(node + sibling)
        index //= 2
    return node


def sf_read_tx(data: bytes, o: int) -> tuple[bytes, list[tuple[bytes, int]], list[bytes], int]:
    """A transaction in a message: its bytes, the outpoints its inputs spend,
    its output scripts as serialized, and where it ends."""
    start = o
    o += 4
    count, o = read_compact(data, o)
    inputs = []
    for _ in range(count):
        inputs.append((data[o:o + 32], struct.unpack("<I", data[o + 32:o + 36])[0]))
        size, o = read_compact(data, o + 36)
        o += size + 4
    count, o = read_compact(data, o)
    scripts = []
    for _ in range(count):
        size, o = read_compact(data, o + 8)
        scripts.append(data[o:o + size])
        o += size
    o += 4
    if o > len(data):
        raise ValueError("the transaction runs past the message")
    return data[start:o], inputs, scripts, o


def sf_read_hashes(data: bytes, o: int) -> tuple[list[bytes], int]:
    count, o = read_compact(data, o)
    if o + 32 * count > len(data):
        raise ValueError("the hashes run past the message")
    return [data[o + 32 * i:o + 32 * (i + 1)] for i in range(count)], o + 32 * count


_SF_PREVIOUS: dict = {}


def sf_previous_output(txid: str, n: int) -> dict:
    """An output a block spends, from BCHN's transaction index."""
    if txid not in _SF_PREVIOUS:
        _SF_PREVIOUS[txid] = call(BCHN, "getrawtransaction", txid, True)[0]
    return _SF_PREVIOUS[txid]["vout"][n]


def sf_txid_key(txid: str) -> bytes:
    """The key of a transaction's leaf: SHA256(0x02 || txid), the txid in the
    order it is stored, as in an outpoint (display order reversed)."""
    return hashlib.sha256(b"\x02" + bytes.fromhex(txid)[::-1]).digest()


def sf_category_key(category: str) -> bytes:
    """The key of a token category's leaf: SHA256(0x01 || category), stored order."""
    return hashlib.sha256(b"\x01" + bytes.fromhex(category)[::-1]).digest()


def sf_block_leaves(block: dict) -> list[bytes]:
    """The leaves of a block's script filter, from BCHN's own reading of the
    block and of the outputs it spends: one for each script that is not empty
    and does not start with OP_RETURN, one for each token category, and one
    for each transaction, the coinbase included."""
    leaves = []

    def add(vout: dict, tx_index: int, index: int, spent: bool) -> None:
        script = bytes.fromhex(vout["scriptPubKey"]["hex"])
        if script and script[0] != 0x6a:
            leaves.append(sf_leaf(hashlib.sha256(script).digest(), tx_index, index, 1 if spent else 0))
        token = vout.get("tokenData")
        if token:
            leaves.append(sf_leaf(sf_category_key(token["category"]), tx_index, index, 3 if spent else 2))

    for at, tx in enumerate(block["tx"]):
        leaves.append(sf_leaf(sf_txid_key(tx["txid"]), at, 0, 4))
        if at != 0:
            for j, vin in enumerate(tx["vin"]):
                add(sf_previous_output(vin["txid"], vin["vout"]), at, j, True)
        for j, vout in enumerate(tx["vout"]):
            add(vout, at, j, False)
    return sorted(leaves, key=sf_leaf_order)


def script_filter_light_client(s: dict) -> None:
    """A wallet that syncs script filters (filter type 1) from kth over P2P
    and settles what a filter holds without downloading the block: the filter
    header checkpoints and every filter hash, chained here; filters, hashed
    against them and compared, root and values, with the filter built here
    from BCHN's reading of the block and of the outputs it spends; the leaves
    under one key (getcfrange), rebuilt to the root in front of the filter;
    and the proof of a spent output's leaf (getcfproof), folded to block
    headers BCHN gives."""
    client = "script filter light client (filter type 1; getcfrange, getcfproof over P2P)"
    if not SCRIPT_FILTERS:
        return
    tip = call(KTH, "getblockcount")[0]
    hashes = [bytes.fromhex(call(BCHN, "getblockhash", h)[0])[::-1] for h in range(tip + 1)]
    seen: dict = {"tip": tip}
    ok = False
    peer = None
    try:
        peer = LightPeer(KTH_P2P_PORT)

        peer.send(b"getcfcheckpt", b"\x01" + hashes[tip])
        reply = peer.collect((b"cfcheckpt",), 1, quiet=180.0, limit=900.0)
        checkpoints = []
        if reply:
            body = reply[0][1]
            count, o = read_compact(body, 33)
            checkpoints = [body[o + 32 * i:o + 32 * (i + 1)] for i in range(count)]
            seen["cfcheckpt"] = {"type": body[0], "headers": count}

        header = bytes(32)
        filter_hashes: list[bytes] = []
        headers: list[bytes] = []
        chained = True
        start = 0
        while start <= tip:
            stop = min(tip, start + 1999)
            peer.send(b"getcfheaders", b"\x01" + struct.pack("<I", start) + hashes[stop])
            reply = peer.collect((b"cfheaders",), 1, quiet=120.0, limit=600.0)
            body = reply[0][1] if reply else b""
            count, o = read_compact(body, 65) if len(body) > 65 else (0, 0)
            if (not reply or body[0] != 1 or body[1:33] != hashes[stop] or body[33:65] != header
                    or count != stop - start + 1 or o + 32 * count != len(body)):
                chained = False
                seen["cfheaders wrong at"] = start
                break
            for i in range(count):
                filter_hash = body[o + 32 * i:o + 32 * (i + 1)]
                header = sha256d(filter_hash + header)
                filter_hashes.append(filter_hash)
                headers.append(header)
            start = stop + 1
        seen["filter hashes chained"] = len(filter_hashes)
        checkpoints_ok = (chained and len(filter_hashes) == tip + 1 and len(checkpoints) == tip // 1000
                          and all(checkpoints[i] == headers[(i + 1) * 1000] for i in range(len(checkpoints))))
        seen["checkpoints are the chained headers at each 1000"] = checkpoints_ok

        # Filters: the first blocks, the blocks around the wallet transactions
        # (which spend earlier outputs), the tip.
        wallet_height = s["tx_block_height"]
        ranges = [(0, min(tip, 3)), (max(0, wallet_height - 1), min(tip, wallet_height + 1)), (tip, tip)]
        compared = 0
        spending_leaves = 0
        roots: dict[int, bytes] = {}
        expected_leaves: dict[int, list[bytes]] = {}
        filters_ok = chained
        for first, last in ranges:
            peer.send(b"getcfilters", b"\x01" + struct.pack("<I", first) + hashes[last])
            reply = peer.collect((b"cfilter",), last - first + 1, quiet=60.0, limit=300.0)
            if len(reply) != last - first + 1:
                filters_ok = False
                seen[f"cfilters {first}..{last}"] = len(reply)
                continue
            for i, (_, body) in enumerate(reply):
                height = first + i
                size, o = read_compact(body, 33)
                data = body[o:o + size]
                if (body[0] != 1 or body[1:33] != hashes[height] or o + size != len(body) or size < 32
                        or not chained or sha256d(data) != filter_hashes[height]):
                    filters_ok = False
                    seen["cfilter wrong at"] = height
                    continue
                block = call(BCHN, "getblock", hashes[height][::-1].hex(), 2)[0]
                leaves = sf_block_leaves(block)
                keys = {leaf[:32] for leaf in leaves}
                n, values = gcs_values(data[32:])
                if data[:32] != sf_root(leaves) or n != len(keys) or values != gcs_expected(hashes[height], keys):
                    filters_ok = False
                    seen["cfilter content differs at"] = height
                    continue
                roots[height] = data[:32]
                expected_leaves[height] = leaves
                spending_leaves += sum(1 for leaf in leaves if leaf[40] == 1)
                compared += 1
        seen["filters equal to the ones built from BCHN's data, root and values"] = compared
        seen["leaves of spent outputs in them"] = spending_leaves

        # The wallet block: every leaf, the leaves under one key, a key it
        # does not have. Each answer rebuilds the root in front of the filter.
        block_hash = hashes[wallet_height]
        root = roots.get(wallet_height, b"")
        leaves = expected_leaves.get(wallet_height, [])
        paid = hashlib.sha256(bytes.fromhex(call(BCHN, "validateaddress", s["others"][0])[0]["scriptPubKey"])).digest()
        absent = hashlib.sha256(bytes.fromhex("76a914" + "ab" * 20 + "88ac")).digest()

        def ask_range(prefix: bytes, bits: int):
            peer.send(b"getcfrange", b"\x01" + block_hash + struct.pack("<H", bits) + prefix)
            reply = peer.collect((b"cfrange",), 1, quiet=30.0, limit=60.0)
            if not reply:
                return None
            body = reply[0][1]
            count, first = struct.unpack("<II", body[33:41])
            held, o = read_compact(body, 41)
            run = [body[o + 41 * i:o + 41 * (i + 1)] for i in range(held)]
            o += 41 * held
            before = after = None
            if first > 0:
                before, o = body[o:o + 41], o + 41
            if first + held < count:
                after, o = body[o:o + 41], o + 41
            edge, o = sf_read_hashes(body, o)
            whole = body[0] == 1 and body[1:33] == block_hash and o == len(body)
            proved = [leaf for leaf in (before, *run, after) if leaf is not None]
            rebuilt = sf_range_root(count, first - (1 if before else 0), proved, edge) if proved else bytes(32)
            return {"count": count, "first": first, "run": run, "before": before, "after": after,
                    "whole": whole, "root": rebuilt}

        ranges_ok = bool(leaves) and bool(root)
        every = ask_range(bytes(32), 0)
        seen["getcfrange, no prefix"] = every and {"leaves": len(every["run"]), "of": every["count"]}
        ranges_ok = ranges_ok and bool(every) and every["whole"] and every["run"] == leaves \
            and every["root"] == root and every["before"] is None and every["after"] is None
        one = ask_range(paid, 256)
        under = [leaf for leaf in leaves if leaf[:32] == paid]
        seen["getcfrange, the paid script's key"] = one and {"leaves": len(one["run"]), "first": one["first"]}
        ranges_ok = ranges_ok and bool(one) and bool(under) and one["whole"] and one["run"] == under \
            and one["root"] == root \
            and (one["before"] is None or one["before"][:32] < paid) \
            and (one["after"] is None or one["after"][:32] > paid)
        missing = ask_range(absent, 256)
        seen["getcfrange, a key the block does not have"] = missing and {
            "leaves": len(missing["run"]), "first": missing["first"]}
        ranges_ok = ranges_ok and bool(missing) and missing["whole"] and not missing["run"] \
            and missing["root"] == root \
            and (missing["before"] is None or missing["before"][:32] < absent) \
            and (missing["after"] is None or missing["after"][:32] > absent) \
            and (missing["before"] is not None or missing["after"] is not None)
        seen["ranges rebuild the root in front of the filter"] = ranges_ok

        # The proof of a spent output's leaf, checked against block headers
        # from BCHN alone.
        def merkle_root_of(block_hash_internal: bytes):
            block_header = call(BCHN, "getblockheader", block_hash_internal[::-1].hex(), True)[0] or {}
            if block_header.get("confirmations", 0) < 1:
                return None
            return bytes.fromhex(block_header["merkleroot"])[::-1]

        def ask_proof(index: int):
            peer.send(b"getcfproof", b"\x01" + block_hash + struct.pack("<I", index))
            reply = peer.collect((b"cfproof",), 1, quiet=30.0, limit=60.0)
            if not reply:
                return None
            body = reply[0][1]
            count, at = struct.unpack("<II", body[33:41])
            leaf = body[41:82]
            branch, o = sf_read_hashes(body, 82)
            tx, inputs, _, o = sf_read_tx(body, o)
            tx_branch, o = sf_read_hashes(body, o)
            out = {"count": count, "index": at, "leaf": leaf, "inputs": inputs, "origin": None,
                   "txid": sha256d(tx),
                   "leaf folds to the root": sf_range_root(count, at, [leaf], branch) == root,
                   "transaction folds to the header": sf_fold(
                       sha256d(tx), struct.unpack("<I", leaf[32:36])[0], tx_branch) == merkle_root_of(block_hash)}
            if leaf[40] in (1, 3):
                origin_block = body[o:o + 32]
                origin_index, = struct.unpack("<I", body[o + 32:o + 36])
                origin_tx, _, scripts, o = sf_read_tx(body, o + 36)
                origin_branch, o = sf_read_hashes(body, o)
                out["origin"] = {"block": origin_block, "txid": sha256d(origin_tx), "scripts": scripts,
                                 "folds to its header": sf_fold(sha256d(origin_tx), origin_index, origin_branch)
                                 == merkle_root_of(origin_block)}
            out["whole"] = body[0] == 1 and body[1:33] == block_hash and o == len(body)
            return out

        proofs_ok = bool(leaves) and bool(root)
        spent_at = next((i for i, leaf in enumerate(leaves) if leaf[40] == 1), None)
        created_at = next((i for i, leaf in enumerate(leaves) if leaf[40] == 0), None)
        spent = ask_proof(spent_at) if spent_at is not None else None
        if spent and spent["origin"]:
            input_index, = struct.unpack("<I", spent["leaf"][36:40])
            previous_txid, previous_n = spent["inputs"][input_index]
            origin = spent["origin"]
            key_matches = (previous_txid == origin["txid"] and previous_n < len(origin["scripts"])
                           and hashlib.sha256(origin["scripts"][previous_n]).digest() == spent["leaf"][:32])
            seen["getcfproof, a spent output's leaf"] = {
                "leaf": spent["index"], "of": spent["count"],
                "leaf folds to the root": spent["leaf folds to the root"],
                "transaction folds to the header": spent["transaction folds to the header"],
                "created in block": origin["block"][::-1].hex(),
                "creating transaction folds to its header": origin["folds to its header"],
                "the output it names has the leaf's key": key_matches}
            proofs_ok = proofs_ok and spent["whole"] and spent["leaf"] == leaves[spent_at] \
                and spent["leaf folds to the root"] and spent["transaction folds to the header"] \
                and origin["folds to its header"] and key_matches
        else:
            proofs_ok = False
        created = ask_proof(created_at) if created_at is not None else None
        seen["getcfproof, a created output's leaf"] = created and {
            "leaf": created["index"], "leaf folds to the root": created["leaf folds to the root"],
            "transaction folds to the header": created["transaction folds to the header"],
            "another transaction": created["origin"] is not None}
        proofs_ok = proofs_ok and bool(created) and created["whole"] and created["origin"] is None \
            and created["leaf"] == leaves[created_at] and created["leaf folds to the root"] \
            and created["transaction folds to the header"]
        seen["proofs check against BCHN's block headers"] = proofs_ok

        # Each transaction of the wallet block by its txid alone: the full key
        # SHA256(0x02 || txid) gives the one leaf of that transaction (kind 4,
        # its position, index 0), and the proof of that leaf is the
        # transaction itself, folded to BCHN's block header.
        txids = call(BCHN, "getblock", block_hash[::-1].hex(), 1)[0]["tx"]
        txids_ok = bool(txids) and bool(root)
        by_txid = 0
        for position, txid in enumerate(txids):
            key = sf_txid_key(txid)
            wanted = sf_leaf(key, position, 0, 4)
            answer = ask_range(key, 256)
            good = (bool(answer) and answer["whole"] and answer["run"] == [wanted] and answer["root"] == root
                    and (answer["before"] is None or answer["before"][:32] < key)
                    and (answer["after"] is None or answer["after"][:32] > key))
            proof = ask_proof(answer["first"]) if good else None
            good = (good and bool(proof) and proof["whole"] and proof["leaf"] == wanted and proof["origin"] is None
                    and proof["txid"] == bytes.fromhex(txid)[::-1] and proof["leaf folds to the root"]
                    and proof["transaction folds to the header"])
            by_txid += good
            txids_ok = txids_ok and good
        seen["getcfrange and getcfproof by txid alone"] = {"transactions": len(txids), "found and proved": by_txid}

        # What is not answered: the regular filter has no entry tree, a prefix
        # with a bit past its length, a leaf past the last, an unknown filter
        # type, a block nobody has.
        loose = bytes([0x00, 0x01]) + bytes(30)
        for command, payload in ((b"getcfrange", b"\x00" + block_hash + struct.pack("<H", 0) + bytes(32)),
                                 (b"getcfrange", b"\x01" + block_hash + struct.pack("<H", 8) + loose),
                                 (b"getcfrange", b"\x01" + b"\x11" * 32 + struct.pack("<H", 0) + bytes(32)),
                                 (b"getcfproof", b"\x01" + block_hash + struct.pack("<I", len(leaves))),
                                 (b"getcfproof", b"\x00" + block_hash + struct.pack("<I", 0)),
                                 (b"getcfilters", UNSERVED_TYPE + struct.pack("<I", 0) + hashes[tip]),
                                 (b"getcfcheckpt", UNSERVED_TYPE + hashes[tip])):
            peer.send(command, payload)
        silent = not peer.collect((b"cfrange", b"cfproof", b"cfilter", b"cfcheckpt"), None, quiet=4.0, limit=10.0)
        seen["refused requests get no answer"] = silent
        peer.send(b"getcfproof", b"\x01" + block_hash + struct.pack("<I", 0))
        seen["answers after them"] = len(peer.collect((b"cfproof",), 1, quiet=30.0, limit=60.0)) == 1

        # Stored for it: 64 bytes for every thousand blocks, in a file of its own.
        stored = subprocess.run(["docker", "exec", "kth", "sh", "-c",
                                 "wc -c < /data/e2e/script_filter_checkpoints"],
                                capture_output=True, text=True).stdout.strip()
        seen["checkpoint file bytes"] = stored
        file_ok = stored.isdigit() and int(stored) == 64 * (tip // 1000)

        ok = (checkpoints_ok and filters_ok and compared == sum(last - first + 1 for first, last in ranges)
              and spending_leaves > 0 and ranges_ok and proofs_ok and txids_ok and silent
              and seen["answers after them"] and file_ok)
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    finally:
        if peer is not None:
            peer.close()
    row(1005, client, "the script filter headers of the whole chain chain from zeros and match the checkpoints; "
        "filters hash to them and equal, root and values, the ones built from BCHN's blocks and the outputs "
        "they spend; a cfrange rebuilds the root in front of the filter; a cfproof folds to block headers "
        "BCHN gives and names the output whose script the leaf is of; a txid's key alone gives that "
        "transaction's one leaf and its proof; bad requests get no answer", ok, seen)


def explorer_p2p_client(s: dict) -> None:
    """The BCH Explorer's 'p2p' backend as a light client of kth
    (scripts/explorer-p2p-e2e.js): it syncs kth's script filters over P2P
    into its own filter store, then reads address histories from them, in
    block mode (matching blocks downloaded whole, no getcfrange or getcfproof)
    and in range mode (getcfrange and getcfproof), and compares each history,
    funded and spent sum and set of unspent outputs with BCHN's reading of
    every block. BCHN then mines one block to the wallet, which kth and the
    explorer must follow. Before that, the script asks kth the requests the
    filter store relies on one by one, so the log says what kth did."""
    client = "BCH Explorer p2p backend (script filters and blocks over P2P)"
    if not SCRIPT_FILTERS or not EXPLORER_BACKEND:
        return
    targets = [{"name": "the wallet's mining address", "address": s["addr"]}]
    targets += [{"name": f"wallet address {i + 1} (paid in block {s['tx_block_height']})", "address": a}
                for i, a in enumerate(s["others"])]
    targets.append({"name": "OP_TRUE, the coinbase script of the blocks the rows mine", "script": "51"})
    env = dict(os.environ, EXPLORER_BACKEND=EXPLORER_BACKEND, KTH_P2P=f"127.0.0.1:{KTH_P2P_PORT}",
               KTH_RPC=f"{KTH[0]}:{KTH[1]}", BCHN_RPC=f"{BCHN[0]}:{BCHN[1]}", RPC_AUTH="e2e:e2e",
               TARGETS=json.dumps(targets), MINE_TO=s["addr"], OUT_DIR=str(OUT / "explorer-p2p"))
    seen: dict = {}
    ok = False
    try:
        run = subprocess.run(["node", str(Path(__file__).with_name("explorer-p2p-e2e.js"))], env=env,
                             capture_output=True, text=True, timeout=1500)
        (OUT / "explorer-p2p.log").write_text(run.stdout + run.stderr)
        print(run.stdout[-12000:] + run.stderr[-3000:], flush=True)
        summary = next((json.loads(line[len("SUMMARY "):]) for line in reversed(run.stdout.splitlines())
                        if line.startswith("SUMMARY ")), None)
        if summary is None:
            seen = {"exit": run.returncode, "output": (run.stdout + run.stderr)[-300:]}
        else:
            seen = {"failed": summary["failed"], "passed": summary["passed"], "knuth": summary["knuth"]}
        ok = run.returncode == 0 and bool(summary and summary.get("ok"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    row(1008, client, "the explorer syncs kth's script filters over P2P; in block mode (no getcfrange or "
        "getcfproof) and in range mode the histories, funded and spent sums and unspent outputs of the wallet's "
        "addresses, OP_TRUE and an unused script equal BCHN's blocks, also after a new block", ok, seen)


def vault_wallet_filters(s: dict) -> None:
    """Wallets whose coins sit in each kind of locking script, served by kth's
    script filter (filter type 1) over P2P with no index: Quantumroot vaults
    (bitjson/quantumroot: the receive address and the quantum lock are P2SH32,
    the transaction-shape check is P2SH20, and a post-quantum spend moves an
    authorizing NFT), and P2PKH, P2SH and P2S wallets. BCHN makes one output
    of each kind in one transaction, an NFT of a new token category at a P2SH32
    output among them, and the next block spends them all, the NFT moving to
    another P2SH32 output. kth's filters of both blocks, from getcfilters, must
    equal the ones built here from BCHN's blocks, and match SHA256(script) of
    each of those outputs and SHA256(0x01 || category) of the NFT in the block
    that creates it and in the block that spends it, and each transaction's
    own key, but not an unrelated script."""
    client = "Quantumroot vaults, P2PKH, P2SH and P2S wallets (script filter over P2P, no index)"
    if not SCRIPT_FILTERS:
        return
    seen: dict = {}
    ok = False
    peer = None
    sats = 100_000_000
    fee = 10_000

    def script_of(address: str) -> bytes:
        return bytes.fromhex(call(BCHN, "validateaddress", address)[0]["scriptPubKey"])

    def p2sh20(redeem: bytes) -> bytes:
        # BCHN's own HASH160 of the redeem script, through its P2SH address.
        return script_of(json.loads(cli("decodescript", redeem.hex()))["p2sh"])

    def p2sh32(redeem: bytes) -> bytes:
        return b"\xaa\x20" + sha256d(redeem) + b"\x87"   # OP_HASH256 <32> OP_EQUAL

    def nft(category: bytes, commitment: bytes) -> bytes:
        # The CashTokens prefix of an immutable NFT with a commitment (HAS_NFT | HAS_COMMITMENT).
        return b"\xef" + category + b"\x60" + compact_size(len(commitment)) + commitment

    def raw_tx(inputs: list, outputs: list) -> str:
        out = struct.pack("<I", 2) + compact_size(len(inputs))
        for txid, n, script_sig in inputs:
            out += (bytes.fromhex(txid)[::-1] + struct.pack("<I", n) + compact_size(len(script_sig)) + script_sig
                    + b"\xff\xff\xff\xff")
        out += compact_size(len(outputs))
        for value, body in outputs:
            out += struct.pack("<Q", value) + compact_size(len(body)) + body
        return (out + struct.pack("<I", 0)).hex()

    def signed(hex_tx: str) -> str:
        result = json.loads(cli("signrawtransactionwithwallet", hex_tx))
        if not result.get("complete"):
            raise ValueError(f"the wallet did not sign: {json.dumps(result.get('errors'))[:200]}")
        return result["hex"]

    try:
        # Output 0 of a wallet transaction: the transaction that spends it as
        # its first input may make a new token category, whose id is that txid.
        utxo = max(json.loads(cli("listunspent", "1")), key=lambda u: u["amount"])
        in_sats = round(utxo["amount"] * sats)
        parent_txid = cli("sendrawtransaction", signed(cli(
            "createrawtransaction", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]),
            json.dumps({cli("getnewaddress"): round((in_sats - fee) / sats, 8)}))))
        parent_value = in_sats - fee

        category = bytes.fromhex(parent_txid)[::-1]
        commitment = b"quantumroot"
        token_value = 100_000
        p2pkh = script_of(cli("getnewaddress"))
        shape_check = p2sh20(b"\x51")              # Quantumroot's transaction-shape check: P2SH20
        vault_receive = p2sh32(b"\x52")            # Quantumroot's receive address: P2SH32
        vault_lock = p2sh32(b"\x53")               # the quantum lock that holds the authorizing NFT
        vault_lock_next = p2sh32(b"\x54")          # where a post-quantum spend moves the NFT
        bare = bytes.fromhex("52935487")           # P2S: OP_2 OP_ADD OP_4 OP_EQUAL, no template
        unrelated = bytes.fromhex("76a914" + "cd" * 20 + "88ac")
        change = script_of(cli("getnewaddress"))
        create_txid = cli("sendrawtransaction", signed(raw_tx(
            [(parent_txid, 0, b"")],
            [(sats, p2pkh), (sats, shape_check), (sats, vault_receive), (sats, bare),
             (token_value, nft(category, commitment) + vault_lock),
             (parent_value - 4 * sats - token_value - fee, change)])))
        create_hash = json.loads(cli("generatetoaddress", "1", s["addr"]))[0]

        # The next block spends every one of them: the P2SH20, P2SH32 and P2S
        # outputs by their scripts alone, the NFT on to the next lock, and the
        # P2PKH output by the wallet's signature.
        spend_txid = cli("sendrawtransaction", raw_tx(
            [(create_txid, 1, push(b"\x51")), (create_txid, 2, push(b"\x52")), (create_txid, 3, b"\x52"),
             (create_txid, 4, push(b"\x53"))],
            [(token_value, nft(category, commitment) + vault_lock_next),
             (3 * sats - fee, script_of(cli("getnewaddress")))]))
        p2pkh_spend_txid = cli("sendrawtransaction", signed(cli(
            "createrawtransaction", json.dumps([{"txid": create_txid, "vout": 0}]),
            json.dumps({cli("getnewaddress"): round((sats - fee) / sats, 8)}))))
        spend_hash = json.loads(cli("generatetoaddress", "1", s["addr"]))[0]
        create_height = call(BCHN, "getblockheader", create_hash, True)[0]["height"]
        spend_height = call(BCHN, "getblockheader", spend_hash, True)[0]["height"]
        in_blocks = (create_txid in call(BCHN, "getblock", create_hash, 1)[0]["tx"]
                     and {spend_txid, p2pkh_spend_txid} <= set(call(BCHN, "getblock", spend_hash, 1)[0]["tx"]))
        seen["BCHN mined both"] = in_blocks
        synced = wait_kth_height(spend_height, 300)
        seen["kth at the spending block"] = synced

        peer = LightPeer(KTH_P2P_PORT)
        peer.send(b"getcfilters", b"\x01" + struct.pack("<I", create_height) + bytes.fromhex(spend_hash)[::-1])
        reply = peer.collect((b"cfilter",), spend_height - create_height + 1, quiet=60.0, limit=300.0)
        filters = {}
        for _, body in reply:
            size, o = read_compact(body, 33)
            if body[0] == 1 and o + size == len(body):
                filters[body[1:33]] = body[o:o + size]

        sha = lambda script: hashlib.sha256(script).digest()
        wanted = {
            "P2PKH": sha(p2pkh),
            "P2SH20 shape check": sha(shape_check),
            "P2SH32 receive": sha(vault_receive),
            "P2S": sha(bare),
            "P2SH32 NFT lock": sha(vault_lock),
            "NFT category": sf_category_key(parent_txid),
        }
        checks_ok = in_blocks and synced
        for label, block_hash, extra in (
                ("creating", create_hash, {"its txid": sf_txid_key(create_txid)}),
                ("spending", spend_hash, {"its txid": sf_txid_key(spend_txid),
                                          "P2PKH spend txid": sf_txid_key(p2pkh_spend_txid),
                                          "next NFT lock": sha(vault_lock_next)})):
            internal = bytes.fromhex(block_hash)[::-1]
            data = filters.get(internal, b"")
            leaves = sf_block_leaves(call(BCHN, "getblock", block_hash, 2)[0])
            keys = {leaf[:32] for leaf in leaves}
            n, values = gcs_values(data[32:]) if len(data) > 32 else (0, [])
            equal = bool(data) and data[:32] == sf_root(leaves) and n == len(keys) \
                and values == gcs_expected(internal, keys)
            k0 = int.from_bytes(internal[:8], "little")
            k1 = int.from_bytes(internal[8:16], "little")

            def has(item: bytes) -> bool:
                return (siphash24(k0, k1, item) * n * GCS_M) >> 64 in values

            missing = [name for name, key in {**wanted, **extra}.items() if not has(key)]
            stray = has(sha(unrelated))
            seen[f"{label} block"] = {"equal to the reference": equal, "items": n, "missing": missing,
                                      "unrelated script matches": stray}
            checks_ok = checks_ok and equal and not missing and not stray
        ok = checks_ok
    except subprocess.CalledProcessError as error:
        method = error.cmd[6] if len(error.cmd) > 6 else error.cmd
        seen["error"] = f"{method}: {(error.stderr or '').strip()[:200]}"
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    finally:
        if peer is not None:
            peer.close()
    row(1009, client, "BCHN makes P2PKH, P2SH20, P2SH32 and P2S outputs and an NFT of a new category at a "
        "P2SH32 output, and spends them all in the next block, the NFT to another P2SH32 output; kth's type 1 "
        "filters of both blocks over P2P equal the reference built from BCHN's blocks and match each script, "
        "the category and each txid in the block that creates and in the block that spends, and not an "
        "unrelated script", ok, seen)


# ---- filter type 2: the input prefix filter, for reusable payment addresses -------
#
# A payment to a reusable payment address is found by one of its inputs: the
# sender grinds one of the first 30 inputs of the transaction until the first 4
# to 16 bits of SHA256d(the input as serialized) equal bits of the receiver's
# scan key. The filter of a block holds the first two bytes (big-endian) of that
# hash for the first 30 inputs of every transaction after the coinbase, sorted
# and distinct, Rice coded. Written here from the spec alone: it is the
# reference kth's filters are checked against, and scripts/rpa_filter_vectors.py
# makes the test vectors with it.

RPA_INPUTS = 30
RPA_RICE_SCALE = 45426  # round(65536 * ln 2)


def rpa_input_bytes(txid_internal: bytes, vout: int, script_sig: bytes, sequence: int) -> bytes:
    """An input as it stands in its transaction."""
    return (txid_internal + struct.pack("<I", vout) + compact_size(len(script_sig)) + script_sig
            + struct.pack("<I", sequence))


def rpa_value(serialized_input: bytes) -> int:
    """The first two bytes of SHA256d(input), most significant first."""
    digest = sha256d(serialized_input)
    return digest[0] * 256 + digest[1]


def rpa_values_of(transactions: list[list[bytes]]) -> list[int]:
    """S of a block, from the serialized inputs of each transaction after the
    coinbase: the values of the first 30 inputs of each, sorted, each once."""
    return sorted({rpa_value(serialized) for inputs in transactions for serialized in inputs[:RPA_INPUTS]})


def rpa_block_values(block: dict) -> list[int]:
    """S of a block, from BCHN's own reading of it (getblock verbosity 2)."""
    return rpa_values_of([[rpa_input_bytes(bytes.fromhex(vin["txid"])[::-1], vin["vout"],
                                           bytes.fromhex(vin["scriptSig"]["hex"]), vin["sequence"])
                           for vin in tx["vin"]] for tx in block["tx"][1:]])


def rpa_rice_parameter(n: int) -> int:
    """k = floor(log2(floor(45426 / N))), and 0 when that quotient is 0."""
    quotient = RPA_RICE_SCALE // n
    return quotient.bit_length() - 1 if quotient else 0


def rpa_rice_bits(k: int, gaps: list[int]) -> str:
    """Each gap: gap >> k one-bits, a zero-bit, the low k bits, most significant first."""
    return "".join("1" * (gap >> k) + "0" + (format(gap & ((1 << k) - 1), f"0{k}b") if k else "")
                   for gap in gaps)


def rpa_pack_bits(bits: str) -> bytes:
    """Most significant bit first, the last byte padded with zero-bits."""
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def rpa_encode(values: list[int]) -> bytes:
    """The one encoding of a set: CompactSize N, then, when N > 0, k in one
    byte and the Rice-coded gaps (the first from 0)."""
    values = sorted(set(values))
    out = compact_size(len(values))
    if not values:
        return out
    k = rpa_rice_parameter(len(values))
    gaps = [b - a for a, b in zip([0] + values[:-1], values)]
    return out + bytes([k]) + rpa_pack_bits(rpa_rice_bits(k, gaps))


def rpa_decode(data: bytes) -> list[int]:
    """The values of a served filter. Raises ValueError, naming the rule, for
    bytes that are not the one encoding of a set."""
    if not data:
        raise ValueError("truncated")
    width = {0xfd: 2, 0xfe: 4, 0xff: 8}.get(data[0], 0)
    if len(data) < 1 + width:
        raise ValueError("truncated")
    n = int.from_bytes(data[1:1 + width], "little") if width else data[0]
    if width and n < {2: 0xfd, 4: 0x10000, 8: 0x100000000}[width]:
        raise ValueError("count not in its shortest form")
    if n > 65536:
        raise ValueError("count above 65,536")
    o = 1 + width
    if n == 0:
        if o != len(data):
            raise ValueError("trailing bytes")
        return []
    if o >= len(data):
        raise ValueError("truncated")
    k = data[o]
    if k != rpa_rice_parameter(n):
        raise ValueError("k not the one its count gives")
    stream = data[o + 1:]
    total = len(stream) * 8
    pos = 0

    def bit() -> int:
        nonlocal pos
        if pos >= total:
            raise ValueError("truncated")
        one = (stream[pos // 8] >> (7 - pos % 8)) & 1
        pos += 1
        return one

    values: list[int] = []
    previous = 0
    for i in range(n):
        quotient = 0
        while bit():
            quotient += 1
            if previous + (quotient << k) > 65535:
                raise ValueError("value above 65,535")
        low = 0
        for _ in range(k):
            low = low << 1 | bit()
        gap = quotient << k | low
        if i and gap == 0:
            raise ValueError("gap of 0 after the first value")
        previous += gap
        if previous > 65535:
            raise ValueError("value above 65,535")
        values.append(previous)
    while pos % 8:
        if bit():
            raise ValueError("padding bit set")
    if pos != total:
        raise ValueError("trailing bytes")
    return values


def rpa_has_prefix(values: list[int], prefix: int, bits: int) -> bool:
    """Whether S has a value in [p << (16 - b), (p + 1) << (16 - b)): one test
    on the sorted values."""
    low, high = prefix << (16 - bits), (prefix + 1) << (16 - bits)
    at = bisect.bisect_left(values, low)
    return at < len(values) and values[at] < high


def rpa_read_block(data: bytes) -> list[tuple[str, list[bytes]]]:
    """A block as it comes on the wire: each transaction's txid (display
    order) and its inputs as serialized."""
    count, o = read_compact(data, 80)
    transactions = []
    for _ in range(count):
        start = o
        n_in, o = read_compact(data, o + 4)
        inputs = []
        for _ in range(n_in):
            begin = o
            size, o = read_compact(data, o + 36)
            o += size + 4
            inputs.append(data[begin:o])
        n_out, o = read_compact(data, o)
        for _ in range(n_out):
            size, o = read_compact(data, o + 8)
            o += size
        o += 4
        if o > len(data):
            raise ValueError("a transaction runs past the block")
        transactions.append((sha256d(data[start:o])[::-1].hex(), inputs))
    if o != len(data):
        raise ValueError("bytes after the last transaction")
    return transactions


def rpa_wallet_filters(s: dict) -> None:
    """Wallets of reusable payment addresses, served by kth's input prefix
    filter (filter type 2) over P2P in block mode: no request names a prefix.
    BCHN's wallet signs, again and again with another sequence, a payment
    whose input 0 is ground until SHA256d(input 0) starts with a chosen 16-bit
    prefix, then another to a chosen 8-bit prefix, and a transaction of 31
    inputs whose 31st value is not among the first 30's; each is mined in a
    block of its own. Every type 2 filter kth serves must equal the one built
    here from BCHN's reading of the same block, the filter headers must chain
    from zeros to kth's checkpoints, the 4, 8, 12 and 16-bit prefixes of each
    ground input must find exactly the blocks the reference finds, the
    payment's among them, prefixes absent from a block must not find it, and
    the block downloaded from kth must rebuild its filter and hold the
    payment."""
    client = "reusable payment address wallets (input prefix filter, type 2, over P2P, block mode)"
    if not RPA_FILTERS:
        return
    seen: dict = {}
    ok = False
    peer = None
    sats = 100_000_000
    fee = 10_000
    chosen16, chosen8 = 0x5250, 0x41

    def script_of(address: str) -> bytes:
        return bytes.fromhex(call(BCHN, "validateaddress", address)[0]["scriptPubKey"])

    def ground(utxo: dict, target: int, bits: int) -> tuple[str, bytes, int]:
        """Signs the payment with sequence 0x80000000 + n (no relative lock)
        for n = 0, 1, ... in batches until SHA256d(input 0) starts with the
        target bits: the signed transaction, input 0, and how many were signed."""
        value = round(utxo["amount"] * sats) - fee
        pay_to = script_of(cli("getnewaddress"))
        outpoint = bytes.fromhex(utxo["txid"])[::-1] + struct.pack("<I", utxo["vout"])
        signed = 0
        while signed < 1_500_000:
            batch = []
            for n in range(signed, signed + 2000):
                raw = (struct.pack("<I", 2) + b"\x01" + outpoint + b"\x00" + struct.pack("<I", 0x80000000 | n)
                       + b"\x01" + struct.pack("<Q", value) + compact_size(len(pay_to)) + pay_to
                       + struct.pack("<I", 0))
                batch.append({"jsonrpc": "1.0", "id": n, "method": "signrawtransactionwithwallet",
                              "params": [raw.hex()]})
            reply = post(BCHN, json.dumps(batch).encode(), timeout=900)
            if not isinstance(reply["json"], list):
                raise ValueError(f"the batch was not answered: {reply.get('transport') or reply['status']}")
            signed += len(batch)
            for item in sorted(reply["json"], key=lambda i: i["id"]):
                result = item.get("result") or {}
                if not result.get("complete"):
                    raise ValueError(f"the wallet did not sign: {json.dumps(item.get('error'))[:200]}")
                data = bytes.fromhex(result["hex"])
                size, o = read_compact(data, 41)
                first_input = data[5:o + size + 4]
                if rpa_value(first_input) >> (16 - bits) == target:
                    return result["hex"], first_input, item["id"] + 1
        raise ValueError(f"no signature gave the {bits}-bit prefix in {signed} tries")

    def mine_alone(hex_tx: str) -> tuple[str, str]:
        txid = cli("sendrawtransaction", hex_tx)
        block = json.loads(cli("generatetoaddress", "1", s["addr"]))[0]
        if call(BCHN, "getblock", block, 1)[0]["tx"][1:] != [txid]:
            raise ValueError(f"block {block} holds more than {txid}")
        return txid, block

    try:
        # Anything left in BCHN's mempool goes into a block first, so each
        # transaction below has a block of its own.
        cli("generatetoaddress", "1", s["addr"])
        coins = sorted((u for u in json.loads(cli("listunspent", "1"))
                        if u["scriptPubKey"].startswith("76a914") and u["amount"] >= 1),
                       key=lambda u: -u["amount"])
        started = time.monotonic()
        hex16, input16, tries16 = ground(coins[0], chosen16, 16)
        hex8, input8, tries8 = ground(coins[1], chosen8, 8)
        seen["grinding"] = {"16-bit signatures": tries16, "8-bit signatures": tries8,
                            "seconds": round(time.monotonic() - started)}
        value16, value8 = rpa_value(input16), rpa_value(input8)
        txid16, block16 = mine_alone(hex16)
        txid8, block8 = mine_alone(hex8)

        # 31 outputs of the wallet, then one transaction that spends them all,
        # its 31st input's sequence changed until that input's value is not
        # among the first 30's.
        payees = {cli("getnewaddress"): 0.1 for _ in range(31)}
        funding = cli("sendmany", "", json.dumps(payees))
        cli("generatetoaddress", "1", s["addr"])
        outs = [u for u in json.loads(cli("listunspent", "1")) if u["txid"] == funding and u["address"] in payees]
        if len(outs) != 31:
            raise ValueError(f"{len(outs)} of the 31 funding outputs found")
        pay_to = script_of(cli("getnewaddress"))
        for n in range(100):
            raw = struct.pack("<I", 2) + compact_size(31)
            for i, u in enumerate(outs):
                raw += (bytes.fromhex(u["txid"])[::-1] + struct.pack("<I", u["vout"]) + b"\x00"
                        + struct.pack("<I", (0xffffffff - n) if i == 30 else 0xffffffff))
            raw += b"\x01" + struct.pack("<Q", 31 * 10_000_000 - fee) + compact_size(len(pay_to)) + pay_to
            raw += struct.pack("<I", 0)
            hex31 = json.loads(cli("signrawtransactionwithwallet", raw.hex()))["hex"]
            data = bytes.fromhex(hex31)
            o = 5
            serialized = []
            for _ in range(31):
                size, at = read_compact(data, o + 36)
                serialized.append(data[o:at + size + 4])
                o = at + size + 4
            first30 = sorted({rpa_value(x) for x in serialized[:30]})
            value31 = rpa_value(serialized[30])
            if value31 not in first30:
                break
        txid31, block31 = mine_alone(hex31)
        tip = call(BCHN, "getblockheader", block31, True)[0]["height"]
        seen["kth at the tip"] = wait_kth_height(tip, 300)
        heights = {name: call(BCHN, "getblockheader", h, True)[0]["height"]
                   for name, h in (("16-bit payment", block16), ("8-bit payment", block8), ("31 inputs", block31))}
        seen["blocks"] = heights
        hashes = [bytes.fromhex(call(BCHN, "getblockhash", h)[0])[::-1] for h in range(tip + 1)]
        reference = [rpa_block_values(call(BCHN, "getblock", hashes[h][::-1].hex(), 2)[0]) for h in range(tip + 1)]
        seen["blocks with values"] = sum(1 for values in reference if values)

        peer = LightPeer(KTH_P2P_PORT)
        # The checkpoints, asked again while kth's pass has not reached the tip.
        checkpoints = None
        for _ in range(10):
            peer.send(b"getcfcheckpt", b"\x02" + hashes[tip])
            reply = peer.collect((b"cfcheckpt",), 1, quiet=30.0, limit=60.0)
            if reply:
                body = reply[0][1]
                count, o = read_compact(body, 33)
                if body[0] == 2 and body[1:33] == hashes[tip] and o + 32 * count == len(body):
                    checkpoints = [body[o + 32 * i:o + 32 * (i + 1)] for i in range(count)]
                break
        seen["cfcheckpt headers"] = None if checkpoints is None else len(checkpoints)

        header = bytes(32)
        filter_hashes: list[bytes] = []
        headers: list[bytes] = []
        chained = True
        start = 0
        while start <= tip:
            stop = min(tip, start + 1999)
            peer.send(b"getcfheaders", b"\x02" + struct.pack("<I", start) + hashes[stop])
            reply = peer.collect((b"cfheaders",), 1, quiet=120.0, limit=600.0)
            body = reply[0][1] if reply else b""
            count, o = read_compact(body, 65) if len(body) > 65 else (0, 0)
            if (not reply or body[0] != 2 or body[1:33] != hashes[stop] or body[33:65] != header
                    or count != stop - start + 1 or o + 32 * count != len(body)):
                chained = False
                seen["cfheaders wrong at"] = start
                break
            for i in range(count):
                filter_hash = body[o + 32 * i:o + 32 * (i + 1)]
                header = sha256d(filter_hash + header)
                filter_hashes.append(filter_hash)
                headers.append(header)
            start = stop + 1
        checkpoints_ok = (chained and checkpoints is not None and len(filter_hashes) == tip + 1
                          and len(checkpoints) == tip // 1000
                          and all(checkpoints[i] == headers[(i + 1) * 1000] for i in range(len(checkpoints))))
        seen["filter headers chain from zeros to the checkpoints"] = checkpoints_ok

        # Every filter of the chain, 1,000 a request.
        served: dict[int, list[int]] = {}
        filters_ok = chained
        start = 0
        while start <= tip:
            stop = min(tip, start + 999)
            peer.send(b"getcfilters", b"\x02" + struct.pack("<I", start) + hashes[stop])
            reply = peer.collect((b"cfilter",), stop - start + 1, quiet=60.0, limit=600.0)
            if len(reply) != stop - start + 1:
                filters_ok = False
                seen[f"cfilters {start}..{stop}"] = len(reply)
            for i, (_, body) in enumerate(reply):
                height = start + i
                size, o = read_compact(body, 33)
                data = body[o:o + size]
                good = (body[0] == 2 and body[1:33] == hashes[height] and o + size == len(body) and chained
                        and sha256d(data) == filter_hashes[height] and data == rpa_encode(reference[height]))
                try:
                    good = good and rpa_decode(data) == reference[height]
                except ValueError:
                    good = False
                if not good:
                    filters_ok = False
                    seen.setdefault("cfilter differs at", height)
                    continue
                served[height] = rpa_decode(data)
            start = stop + 1
        filters_ok = filters_ok and len(served) == tip + 1
        seen["filters equal to the reference, byte for byte"] = len(served)

        # A wallet's scan: each prefix finds the blocks the reference finds,
        # and the payment's block among them.
        scans_ok = filters_ok
        scans = {}
        for label, value, height in (("16-bit payment", value16, heights["16-bit payment"]),
                                     ("8-bit payment", value8, heights["8-bit payment"])):
            for bits in (4, 8, 12, 16):
                prefix = value >> (16 - bits)
                found = [h for h in range(tip + 1) if rpa_has_prefix(served.get(h, []), prefix, bits)]
                expected = [h for h in range(tip + 1) if rpa_has_prefix(reference[h], prefix, bits)]
                scans[f"{label}, {bits} bits"] = len(found)
                scans_ok = scans_ok and found == expected and height in found
        seen["blocks each prefix finds"] = scans
        seen["ground values"] = {"16-bit": f"{value16:04x}", "8-bit": f"{value8:04x}"}
        scans_ok = scans_ok and value16 == chosen16 and value8 >> 8 == chosen8

        # Prefixes a block does not have do not find it.
        absent = {}
        for label, height in heights.items():
            for bits in (4, 8, 12, 16):
                prefix = next((p for p in range(1 << bits) if not rpa_has_prefix(reference[height], p, bits)), None)
                miss = prefix is not None and not rpa_has_prefix(served.get(height, []), prefix, bits)
                absent[f"{label}, {bits} bits"] = None if prefix is None else f"{prefix:x}"
                scans_ok = scans_ok and miss
        seen["absent prefixes, not found"] = absent

        # The first 30 inputs only.
        thirty_ok = (served.get(heights["31 inputs"]) == first30 and value31 not in first30
                     and not rpa_has_prefix(served.get(heights["31 inputs"], []), value31, 16))
        seen["31 inputs: the first 30 values, not the 31st"] = {"31st": f"{value31:04x}", "ok": thirty_ok}

        # The wallet downloads the payment's block from kth, rebuilds the
        # values from it, and finds its payment.
        peer.send(b"getdata", compact_size(1) + struct.pack("<I", 2) + hashes[heights["16-bit payment"]])
        reply = peer.collect((b"block",), 1, quiet=30.0, limit=60.0)
        transactions = rpa_read_block(reply[0][1]) if reply else []
        rebuilt = rpa_values_of([inputs for _, inputs in transactions[1:]])
        paying = [txid for txid, inputs in transactions[1:]
                  if any(rpa_value(x) == chosen16 for x in inputs[:RPA_INPUTS])]
        download_ok = bool(transactions) and rebuilt == served.get(heights["16-bit payment"]) and txid16 in paying
        seen["downloaded block rebuilds its filter and holds the payment"] = download_ok

        # No entry tree for type 2, and no type 3.
        for command, payload in ((b"getcfrange", b"\x02" + hashes[tip] + struct.pack("<H", 0) + bytes(32)),
                                 (b"getcfproof", b"\x02" + hashes[tip] + struct.pack("<I", 0)),
                                 (b"getcfilters", b"\x03" + struct.pack("<I", 0) + hashes[tip]),
                                 (b"getcfcheckpt", b"\x03" + hashes[tip])):
            peer.send(command, payload)
        silent = not peer.collect((b"cfrange", b"cfproof", b"cfilter", b"cfcheckpt"), None, quiet=4.0, limit=10.0)
        seen["refused requests get no answer"] = silent
        peer.send(b"getcfilters", b"\x02" + struct.pack("<I", tip) + hashes[tip])
        answers = len(peer.collect((b"cfilter",), 1, quiet=30.0, limit=60.0)) == 1
        seen["answers after them"] = answers

        stored = subprocess.run(["docker", "exec", "kth", "sh", "-c",
                                 "wc -c < /data/e2e/input_prefix_filter_checkpoints"],
                                capture_output=True, text=True).stdout.strip()
        seen["checkpoint file bytes"] = stored
        file_ok = stored.isdigit() and int(stored) == 64 * (tip // 1000)

        ok = bool(seen["kth at the tip"] and checkpoints_ok and filters_ok and scans_ok and thirty_ok
                  and download_ok and silent and answers and file_ok)
    except subprocess.CalledProcessError as error:
        method = error.cmd[6] if len(error.cmd) > 6 else error.cmd
        seen["error"] = f"{method}: {(error.stderr or '').strip()[:200]}"
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    finally:
        if peer is not None:
            peer.close()
    row(1010, client, "BCHN signs payments whose input 0 is ground to a chosen 16-bit and 8-bit prefix, and a "
        "31-input transaction; every type 2 filter kth serves equals the reference built from BCHN's blocks, "
        "the headers chain from zeros to the checkpoints, each 4/8/12/16-bit prefix of a ground input finds "
        "the reference's blocks with the payment's among them, absent prefixes do not, the 31st input is left "
        "out, and the block downloaded from kth rebuilds its filter and holds the payment", ok, seen)


def bloom_bits(elements: list[bytes], size: int = 64, functions: int = 5, tweak: int = 0) -> bytes:
    bits = bytearray(size)

    def murmur3(seed: int, data: bytes) -> int:
        h = seed & 0xffffffff
        rot = lambda x, r: ((x << r) | (x >> (32 - r))) & 0xffffffff
        for o in range(0, len(data) - len(data) % 4, 4):
            k = int.from_bytes(data[o:o + 4], "little")
            k = (k * 0xcc9e2d51) & 0xffffffff
            k = rot(k, 15)
            k = (k * 0x1b873593) & 0xffffffff
            h ^= k
            h = rot(h, 13)
            h = (h * 5 + 0xe6546b64) & 0xffffffff
        tail = data[len(data) - len(data) % 4:]
        if tail:
            k = int.from_bytes(tail, "little")
            k = (k * 0xcc9e2d51) & 0xffffffff
            k = rot(k, 15)
            k = (k * 0x1b873593) & 0xffffffff
            h ^= k
        h ^= len(data)
        h ^= h >> 16
        h = (h * 0x85ebca6b) & 0xffffffff
        h ^= h >> 13
        h = (h * 0xc2b2ae35) & 0xffffffff
        return h ^ (h >> 16)

    for element in elements:
        for i in range(functions):
            bit = murmur3((i * 0xfba4c795 + tweak) & 0xffffffff, element) % (size * 8)
            bits[bit >> 3] |= 1 << (bit & 7)
    return bytes(bits)


def bloom_filter_light_client(s: dict) -> None:
    """A wallet that loads a bloom filter (BIP37) and asks for filtered blocks,
    against kth and against BCHN with the same bytes, step by step: before any
    filter is loaded, with a filter that holds the key hash one wallet
    transaction pays to, after an element is added, and after the filter is
    cleared. At every step kth's merkleblocks and the transactions after them
    must be the bytes BCHN sends."""
    client = "bloom filter light client (filterload, filteradd, filterclear, getdata of filtered blocks over P2P)"
    if not BLOOM:
        return
    seen: dict = {}
    ok = False
    peers: dict = {}
    try:
        script = bytes.fromhex(call(BCHN, "validateaddress", s["others"][0])[0]["scriptPubKey"])
        key_hash = script[3:23]
        seen["watched key hash"] = key_hash.hex()
        bits = bloom_bits([key_hash])
        load = bytes([len(bits)]) + bits + struct.pack("<II", 5, 0) + bytes([1])
        tx_block = bytes.fromhex(s["tx_block"])[::-1]
        other_block = bytes.fromhex(call(BCHN, "getblockhash", 5)[0])[::-1]
        coinbase = bytes.fromhex(call(BCHN, "getblock", other_block[::-1].hex(), 1)[0]["tx"][0])[::-1]
        wanted = (b"merkleblock", b"tx", b"notfound")

        def filtered(inventory: list[bytes]) -> bytes:
            return bytes([len(inventory)]) + b"".join(struct.pack("<I", 3) + h for h in inventory)

        steps = (("no filter loaded", None, [tx_block]),
                 ("filter loaded", (b"filterload", load), [tx_block, other_block]),
                 ("element added", (b"filteradd", bytes([32]) + coinbase), [other_block]),
                 ("filter cleared", (b"filterclear", b""), [other_block]))
        answers: dict = {}
        for name, port in (("kth", KTH_P2P_PORT), ("bchn", 18444)):
            peer = LightPeer(port)
            peers[name] = peer
            if name == "kth":
                seen["NODE_BLOOM advertised"] = peer.services is not None and bool(peer.services & NODE_BLOOM)
            answers[name] = []
            for _, message, inventory in steps:
                if message is not None:
                    peer.send(*message)
                peer.send(b"getdata", filtered(inventory))
                answers[name].append(peer.collect(wanted, None, quiet=5.0, limit=60.0))
        same = True
        for at, (label, _, _) in enumerate(steps):
            kth, bchn = answers["kth"][at], answers["bchn"][at]
            seen[label] = {"kth": [c.decode() for c, _ in kth], "bchn": [c.decode() for c, _ in bchn],
                           "same bytes": kth == bchn}
            same = same and kth == bchn and len(bchn) > 0
        # The loaded filter proves something: fewer transactions than the block
        # holds are sent for the block of wallet transactions.
        whole = sum(1 for c, _ in answers["bchn"][0] if c == b"tx")
        matched = sum(1 for c, _ in answers["kth"][1] if c == b"tx")
        seen["transactions in the block"] = whole
        seen["transactions the filter matched"] = matched
        ok = bool(seen["NODE_BLOOM advertised"]) and same and 1 <= matched < whole + 1
    except (OSError, ValueError, KeyError, IndexError) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    finally:
        for peer in peers.values():
            peer.close()
    row(1003, client, "kth advertises NODE_BLOOM; before a filter is loaded, with one, after filteradd and "
        "after filterclear, its merkleblocks and transactions are the bytes BCHN sends", ok, seen)


# ---- report -------------------------------------------------------------------

# ---- Electrum wallets: bch-electrum-bridge on kth against Fulcrum on BCHN ----

class ElectrumClient:
    """An Electrum protocol connection as a wallet makes it: newline-delimited
    JSON-RPC over TCP. It counts the bytes it receives and keeps the
    notifications that arrive, with the time each arrived."""

    def __init__(self, port: int, timeout: float = 300):
        self.timeout = timeout
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buffer = b""
        self.received = 0
        self.next_id = 0
        self.notifications: list = []

    def _lines(self):
        """The complete lines received so far; notifications are kept aside."""
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            message = json.loads(line)
            if "method" in message and "id" not in message:
                self.notifications.append((time.monotonic(), message))
            else:
                yield message

    def call(self, method: str, *params) -> tuple:
        """(result, error, milliseconds). One dict is sent as named parameters."""
        self.next_id += 1
        wanted = self.next_id
        started = time.monotonic()
        named = len(params) == 1 and isinstance(params[0], dict)
        self.sock.sendall(json.dumps({"jsonrpc": "2.0", "id": wanted, "method": method,
                                      "params": params[0] if named else list(params)}).encode() + b"\n")
        while True:
            for message in self._lines():
                if message.get("id") == wanted:
                    return (message.get("result"), message.get("error"),
                            round((time.monotonic() - started) * 1000, 1))
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise OSError(f"{method}: the server closed the connection")
            self.received += len(chunk)
            self.buffer += chunk

    def listen(self, seconds: float, done=lambda: False) -> None:
        """Takes notifications for up to `seconds`, or until `done()`."""
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline and not done():
                for _ in self._lines():
                    pass
                if done():
                    break
                self.sock.settimeout(max(0.05, min(0.5, deadline - time.monotonic())))
                try:
                    chunk = self.sock.recv(1 << 16)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                self.received += len(chunk)
                self.buffer += chunk
            for _ in self._lines():
                pass
        finally:
            self.sock.settimeout(self.timeout)

    def last_notification(self, method: str, key=None, after: float = 0.0) -> tuple:
        """(arrival time, params) of the last notification of `method` since
        `after` whose first parameter is `key` (any when None), or (None, None)."""
        for arrived, message in reversed(self.notifications):
            params = message.get("params") or [None]
            if arrived >= after and message.get("method") == method and (key is None or params[0] == key):
                return arrived, params
        return None, None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def electrum_connect(port: int, seconds: float) -> ElectrumClient:
    """A connection that has sent server.version, once the server listens."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            client = ElectrumClient(port)
            client.call("server.version", "knuth-rpc-e2e", "1.5")
            return client
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def electrum_at_tip(client: ElectrumClient, tip: int, seconds: float, bridge: bool) -> float | None:
    """Seconds until the server is at `tip`: Fulcrum's header tip, or the
    bridge's script filters, and its input prefix filters when it keeps
    them (by default it does not: an RPA search reads its whole range)."""
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if bridge:
            stats = client.call("light_client.stats")[0]
            if (isinstance(stats, dict) and stats.get("filter_height") == tip
                    and stats.get("rpa_height") in (None, tip)):
                return round(time.monotonic() - started, 2)
        else:
            header = client.call("blockchain.headers.subscribe")[0]
            if isinstance(header, dict) and header.get("height") == tip:
                return round(time.monotonic() - started, 2)
        time.sleep(0.2)
    return None


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def json_diff(a, b, path: str = "", out: list | None = None, limit: int = 40) -> list:
    """Where two answers differ: a path and both values (Fulcrum's first)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in b:
                out.append(f"{path}.{key}: Fulcrum only ({json.dumps(a[key])[:80]})")
            elif key not in a:
                out.append(f"{path}.{key}: bridge only ({json.dumps(b[key])[:80]})")
            else:
                json_diff(a[key], b[key], f"{path}.{key}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            json_diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b or type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        out.append(f"{path or 'answer'}: {json.dumps(a)[:100]} vs {json.dumps(b)[:100]}")
    return out


def by_outpoint(rows):
    if not isinstance(rows, list):
        return rows
    return sorted(rows, key=lambda r: (r.get("height", 0), r.get("tx_hash", ""), r.get("tx_pos", 0))
                  if isinstance(r, dict) else (0, "", 0))


# Where the bridge answers otherwise on purpose, and why. These are reported
# with the call, apart from the differences that fail the row.
DELIBERATE_DIFFERENCES = [
    (re.compile(r"^\[\d+\]\.fee: Fulcrum only \(\d+\)$"),
     "the fee of an unconfirmed transaction is left out unless the bridge already holds the coins it "
     "spends: getting them would mean downloading for it, which tells a peer what the wallet watches"),
]
DELIBERATE_CALLS = {
    "daemon.passthrough": "the bridge has no node RPC behind it",
    "server.donation_address": "Fulcrum's default is its author's address; the bridge has none",
    "server.banner": "the bridge's banner says it exists only for wallets that do not speak P2P",
}


def deliberate(method: str, differences: list) -> tuple[list, list]:
    """The differences that fail a row, and those made on purpose (with why)."""
    if differences and method in DELIBERATE_CALLS:
        return [], [f"{d} (on purpose: {DELIBERATE_CALLS[method]})" for d in differences]
    real, chosen = [], []
    for d in differences:
        reason = next((why for pattern, why in DELIBERATE_DIFFERENCES if pattern.match(d)), None)
        if reason is None:
            real.append(d)
        else:
            chosen.append(f"{d} (on purpose: {reason})")
    return real, chosen


def parity(f: ElectrumClient, b: ElectrumClient, group: str, data: bool, method: str, *params,
           repeat: bool = True, normal=None, on_purpose: str | None = None) -> tuple:
    """Fulcrum and the bridge get the same call, twice each when `repeat`
    (the first answer and the one from what the first left behind). A `data`
    call must give the same answer, apart from the differences made on
    purpose (DELIBERATE_DIFFERENCES); the others' differences are reported."""
    fr, fe, f_first = f.call(method, *params)
    br, be, b_first = b.call(method, *params)
    f_again = f.call(method, *params)[2] if repeat else None
    b_again = b.call(method, *params)[2] if repeat else None

    def code(error):
        return error.get("code") if isinstance(error, dict) else error

    if fe is not None or be is not None:
        same = fe is not None and be is not None and code(fe) == code(be)
        differences = [] if same else [f"error: Fulcrum {json.dumps(fe)[:160]}, bridge {json.dumps(be)[:160]}"]
    else:
        norm = normal or (lambda x: x)
        differences = json_diff(norm(fr), norm(br))
    if on_purpose is not None:
        differences, on_purpose = [], [f"{d} (on purpose: {on_purpose})" for d in differences]
    else:
        differences, on_purpose = deliberate(method, differences)
    RESULTS["parity"].append({
        "group": group, "method": method, "params": [p if not isinstance(p, str) or len(p) < 80 else p[:16] + "..."
                                                     for p in params],
        "data": data, "same": not differences, "differences": differences, "on_purpose": on_purpose,
        "fulcrum": json.dumps(fe if fe is not None else fr)[:600],
        "bridge": json.dumps(be if be is not None else br)[:600],
        "ms": {"fulcrum": [f_first, f_again], "bridge": [b_first, b_again]}})
    return (fr, fe), (br, be)


def electrum_bridge_parity(s: dict) -> None:
    """Row 1011. Electrum wallets get the same answers from bch-electrum-bridge,
    a P2P light client of kth (script filters with transaction keys, input
    prefix filters, whole blocks and relay) with BCHN as a relay peer (its
    mempool and double-spend proofs, which kth does not give), as from
    Fulcrum on BCHN, for the same chain and scripts: headers, every scripthash
    call, transactions by ID alone (a fully spent one too, and verbose),
    Merkle proofs, IDs by position, unspent outputs, reusable payment address
    searches, double-spend proofs, broadcasts, and the notifications of the
    script, transaction, header and double-spend proof subscriptions. Each
    call goes twice to each server, for the time of the first answer and of a
    repeated one. The row also measures what each side stores and downloads,
    and how long the bridge's filters take from genesis and from a wallet
    birthday. A second bridge, on kth alone, shows what kth by itself gives."""
    client = ("Electrum wallets (bch-electrum-bridge on kth over P2P, BCHN a relay peer, "
              "against Fulcrum 2.1.4 on BCHN)")
    if not (ELECTRUM_BRIDGE and FULCRUM and SCRIPT_FILTERS and RPA_FILTERS):
        return
    RESULTS["parity"] = []
    seen: dict = {}
    measure: dict = {}
    processes: list = []
    clients: list = []
    ok = False
    try:
        wallet = s["addr"]

        def new_coin() -> dict:
            utxo = next(u for u in json.loads(cli("listunspent", "1"))
                        if u["scriptPubKey"].startswith("76a914") and u["amount"] >= 1 and u["spendable"]
                        and "tokenData" not in u)
            cli("lockunspent", "false", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]))
            return utxo

        def spend(utxo: dict, to: str, fee: float = 0.001) -> str:
            raw = cli("createrawtransaction", json.dumps([{"txid": utxo["txid"], "vout": utxo["vout"]}]),
                      json.dumps({to: round(utxo["amount"] - fee, 8)}))
            return json.loads(cli("signrawtransactionwithwallet", raw))["hex"]

        # A transaction whose outputs are all spent, for a lookup by ID alone.
        spent_payee = cli("getnewaddress")
        fully_spent = cli("sendtoaddress", spent_payee, "1.0")
        cli("generatetoaddress", "1", wallet)
        outs = [u for u in json.loads(cli("listunspent", "1")) if u["txid"] == fully_spent]
        raw = cli("createrawtransaction", json.dumps([{"txid": u["txid"], "vout": u["vout"]} for u in outs]),
                  json.dumps({cli("getnewaddress"): round(sum(u["amount"] for u in outs) - 0.001, 8)}))
        cli("sendrawtransaction", json.loads(cli("signrawtransactionwithwallet", raw))["hex"])
        cli("generatetoaddress", "1", wallet)
        seen["fully spent"] = all(cli("gettxout", fully_spent, str(n)) in ("", "null")
                                  for n in range(len(call(BCHN, "getrawtransaction", fully_spent, True)[0]["vout"])))
        # One payment in the mempool before either server starts.
        early = cli("sendtoaddress", s["others"][2], "0.15")
        tip = int(cli("getblockcount"))
        seen["tip"] = tip
        seen["kth at the tip"] = wait_kth_height(tip, 300)

        # Fulcrum on BCHN's RPC, and the bridge on kth's P2P from genesis.
        home = Path("/tmp/fulcrum-regtest")
        home.mkdir(parents=True, exist_ok=True)
        (home / "fulcrum.conf").write_text("\n".join([
            f"datadir = {home / 'db'}", "bitcoind = 127.0.0.1:18443", "rpcuser = e2e", "rpcpassword = e2e",
            f"tcp = 127.0.0.1:{FULCRUM_PORT}", "peering = false", "announce = false", "polltime = 0.5", ""]))
        started = time.monotonic()
        processes.append(subprocess.Popen([FULCRUM, str(home / "fulcrum.conf")],
                                          stdout=open(OUT / "fulcrum.log", "w"), stderr=subprocess.STDOUT))
        f = electrum_connect(FULCRUM_PORT, 600)
        clients.append(f)
        at_tip = electrum_at_tip(f, tip, 600, bridge=False)
        measure_sync = {"Fulcrum, whole chain over BCHN's RPC":
                        None if at_tip is None else round(time.monotonic() - started, 2)}

        def start_bridge(name: str, port: int, peers: tuple, *extra: str) -> tuple[ElectrumClient, float | None]:
            started = time.monotonic()
            processes.append(subprocess.Popen(
                [ELECTRUM_BRIDGE, "--network", "regtest", "--datadir", f"/tmp/bridge-{name}",
                 *[a for peer in peers for a in ("--peer", peer)], "--listen", f"127.0.0.1:{port}", *extra],
                stdout=open(OUT / f"bridge-{name}.log", "w"), stderr=subprocess.STDOUT))
            connection = electrum_connect(port, 120)
            clients.append(connection)
            synced = electrum_at_tip(connection, tip, 900, bridge=True)
            return connection, None if synced is None else round(time.monotonic() - started, 2)

        # The bridge the calls go to has kth (filters, blocks, relay) and BCHN
        # (relay, its mempool and double-spend proofs; never asked for
        # filters) as peers, as a bridge on the network would have nodes of
        # both. The one from a birthday has kth alone.
        kth_peer, bchn_peer_addr = f"127.0.0.1:{KTH_P2P_PORT}", "127.0.0.1:18444"
        b, measure_sync["bridge filters from genesis"] = start_bridge(
            "genesis", BRIDGE_PORTS[0], (kth_peer, bchn_peer_addr))
        birthday = tip - 150
        late, measure_sync[f"bridge filters from birthday {birthday}, kth alone"] = start_bridge(
            "birthday", BRIDGE_PORTS[1], (kth_peer,), "--birthday", str(birthday))
        measure["filter sync seconds"] = measure_sync
        seen["synced"] = {k: v is not None for k, v in measure_sync.items()}

        # Payments relayed while both servers watch.
        m1 = cli("sendtoaddress", s["others"][0], "0.3")
        m2 = cli("sendtoaddress", s["others"][1], "0.2")
        payees = {"mining": wallet, "payee 0": s["others"][0], "payee 1": s["others"][1],
                  "payee 2 (paid before the servers started)": s["others"][2], "unused": cli("getnewaddress")}
        hashes = {name: hashlib.sha256(bytes.fromhex(call(BCHN, "validateaddress", a)[0]["scriptPubKey"]))
                  .digest()[::-1].hex() for name, a in payees.items()}

        def in_mempool(server: ElectrumClient, sh: str, txid: str) -> bool:
            rows = server.call("blockchain.scripthash.get_mempool", sh)[0]
            return isinstance(rows, list) and any(r.get("tx_hash") == txid for r in rows)

        early_hash = hashes["payee 2 (paid before the servers started)"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (all(
                in_mempool(server, hashes[name], txid) for server in (f, b)
                for name, txid in (("payee 0", m1), ("payee 1", m2))) and in_mempool(b, early_hash, early)):
            time.sleep(0.5)
        seen["relayed payments in both mempools"] = all(
            in_mempool(server, hashes[name], txid) for server in (f, b) for name, txid in (("payee 0", m1),
                                                                                           ("payee 1", m2)))
        seen["payment from before the start, in the bridge's mempool"] = in_mempool(b, early_hash, early)
        seen["kth alone: payment from before the start, in that bridge's mempool"] = in_mempool(
            late, early_hash, early)

        # Server and headers.
        parity(f, b, "server", False, "server.features", repeat=False,
               normal=lambda x: {k: x.get(k) for k in ("genesis_hash", "hash_function", "protocol_min",
                                                        "protocol_max", "dsproof", "rpa")}
               if isinstance(x, dict) else x)
        parity(f, b, "headers", True, "blockchain.headers.subscribe")
        parity(f, b, "headers", False, "blockchain.headers.get_tip")
        for height in (0, 1, s["tx_block_height"], tip):
            parity(f, b, "headers", True, "blockchain.block.header", height)
        parity(f, b, "headers", True, "blockchain.block.header", s["tx_block_height"], tip)
        parity(f, b, "headers", True, "blockchain.block.headers", 0, 2016)
        parity(f, b, "headers", True, "blockchain.block.headers", tip - 9, 20, tip)
        parity(f, b, "headers", False, "blockchain.header.get", tip)
        parity(f, b, "fees", False, "blockchain.estimatefee", 2)
        parity(f, b, "fees", False, "blockchain.relayfee")
        parity(f, b, "fees", False, "mempool.get_fee_histogram")

        # Every scripthash call for each script.
        for name, sh in hashes.items():
            for method in ("get_history", "get_balance", "get_mempool", "get_status", "get_first_use"):
                parity(f, b, f"scripthash: {name}", True, f"blockchain.scripthash.{method}", sh)
            parity(f, b, f"scripthash: {name}", True, "blockchain.scripthash.listunspent", sh, normal=by_outpoint)
            parity(f, b, f"scripthash: {name}", True, "blockchain.scripthash.subscribe", sh, repeat=False)
        parity(f, b, "address", True, "blockchain.address.get_balance", s["others"][0])
        parity(f, b, "address", True, "blockchain.address.get_history", s["others"][0])

        # Transactions: confirmed, coinbase, in the mempool, and fully spent.
        tx_block = call(BCHN, "getblock", s["tx_block"], 1)[0]
        position = tx_block["tx"].index(s["confirmed_txid"])
        spent_height = call(BCHN, "getrawtransaction", fully_spent, True)[0]
        spent_height = call(BCHN, "getblockheader", spent_height["blockhash"], True)[0]["height"]
        for label, txid in (("confirmed", s["confirmed_txid"]), ("coinbase", s["coinbase_txid"]),
                            ("mempool", m1), ("fully spent", fully_spent)):
            parity(f, b, f"transaction: {label}", True, "blockchain.transaction.get", txid)
            parity(f, b, f"transaction: {label}", False, "blockchain.transaction.get", txid, True)
            parity(f, b, f"transaction: {label}", True, "blockchain.transaction.get_height", txid)
        for label, txid, height in (("confirmed", s["confirmed_txid"], s["tx_block_height"]),
                                    ("coinbase", s["coinbase_txid"], s["tx_block_height"]),
                                    ("fully spent", fully_spent, spent_height)):
            parity(f, b, f"transaction: {label}", True, "blockchain.transaction.get_merkle", txid, height)
            parity(f, b, f"transaction: {label}", True, "blockchain.transaction.get_confirmed_blockhash", txid)
        parity(f, b, "transaction: by position", True, "blockchain.transaction.id_from_pos",
               s["tx_block_height"], position)
        parity(f, b, "transaction: by position", True, "blockchain.transaction.id_from_pos",
               s["tx_block_height"], position, True)
        parity(f, b, "unspent output", True, "blockchain.utxo.get_info", s["confirmed_txid"], s["unspent_n"])
        parity(f, b, "unspent output", True, "blockchain.utxo.get_info", fully_spent, 0)
        parity(f, b, "unspent output", True, "blockchain.utxo.get_info", m1, 0)

        # Reusable payment addresses: the payments row 1010 ground to 5250
        # and 41, every 60-block window of the chain; the input of a payment
        # in the mempool; a prefix nothing has.
        for prefix in ("5250", "41"):
            for start in range(0, tip + 1, 60):
                parity(f, b, f"rpa: {prefix}", True, "blockchain.rpa.get_history", prefix, start, start + 60,
                       repeat=start + 60 > tip)
        parity(f, b, "rpa: 5250", True, "blockchain.reusable.get_history", tip - 59, 60, "5250")
        parity(f, b, "rpa: 5250", True, "blockchain.rpa.get_history", "5250", tip - 59)
        data = bytes.fromhex(call(BCHN, "getrawtransaction", m1, False)[0])
        size, o = read_compact(data, 41)
        mempool_prefix = f"{rpa_value(data[5:o + size + 4]):04x}"
        seen["mempool payment prefix"] = mempool_prefix
        for prefix in (mempool_prefix, mempool_prefix[:2]):
            parity(f, b, "rpa: mempool", True, "blockchain.rpa.get_mempool", prefix)
            parity(f, b, "rpa: mempool", True, "blockchain.reusable.get_mempool", prefix)
        parity(f, b, "rpa: absent", True, "blockchain.rpa.get_history", "0000", tip - 59)
        parity(f, b, "rpa: limits", True, "blockchain.rpa.get_history", "5", tip - 59)

        # Subscriptions, on a connection of their own to each server: a
        # script with a payment in the mempool, that payment, the tip, and the
        # double-spend proof of a transaction not sent yet. Each notification
        # must be the same.
        sf, sb = electrum_connect(FULCRUM_PORT, 30), electrum_connect(BRIDGE_PORTS[0], 30)
        clients += [sf, sb]

        def subscription_parity(key: str, method: str, sub_key, since: float, seconds: float) -> None:
            """The last notification since `since` from each server, once both
            sent one and two more seconds brought no other."""
            def first_at(server):
                for arrived, message in server.notifications:
                    params = message.get("params") or [None]
                    if arrived >= since and message.get("method") == method and (
                            sub_key is None or params[0] == sub_key):
                        return arrived
                return None
            for server in (sf, sb):
                server.listen(seconds, lambda server=server: first_at(server) is not None)
                server.listen(2)
            (f_at, f_params), (b_at, b_params) = (server.last_notification(method, sub_key, since)
                                                  for server in (sf, sb))
            f_at, b_at = first_at(sf), first_at(sb)
            differences = json_diff(f_params, b_params)
            if f_params is None or b_params is None:
                differences = [f"no notification from {'Fulcrum' if f_params is None else 'the bridge'}"]
            RESULTS["parity"].append({
                "group": "subscription", "method": method, "params": [key], "data": True,
                "same": not differences, "differences": differences,
                "fulcrum": json.dumps(f_params)[:600], "bridge": json.dumps(b_params)[:600],
                "ms": {"fulcrum": [None if f_at is None else round((f_at - since) * 1000, 1), None],
                       "bridge": [None if b_at is None else round((b_at - since) * 1000, 1), None]}})

        # A double spend of a P2PKH coin: the first spend through BCHN's RPC,
        # the second to BCHN over P2P, where BCHN makes a proof of it. The
        # bridge checks a proof only against a coin it holds (a mempool
        # transaction, a block it read as it came, a watched script) and
        # fetches nothing for it, so this coin is an output of a mempool
        # transaction. No block is mined here: the block for the
        # subscriptions comes later, and a coin from an older block after it.
        parent = cli("sendtoaddress", cli("getnewaddress"), "1.0")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not all(
                server.call("blockchain.transaction.get", parent)[1] is None for server in (f, b)):
            time.sleep(0.5)
        coin = next(u for u in json.loads(cli("listunspent", "0"))
                    if u["txid"] == parent and abs(u["amount"] - 1.0) < 1e-9)
        cli("lockunspent", "false", json.dumps([{"txid": coin["txid"], "vout": coin["vout"]}]))
        first_hex, second_hex = spend(coin, cli("getnewaddress")), spend(coin, cli("getnewaddress"), 0.002)
        first_id = sha256d(bytes.fromhex(first_hex))[::-1].hex()
        for server in (sf, sb):
            server.call("blockchain.headers.subscribe")
        parity(sf, sb, "subscription", True, "blockchain.transaction.dsproof.subscribe", first_id, repeat=False)
        parity(sf, sb, "subscription", True, "blockchain.scripthash.subscribe", hashes["payee 0"], repeat=False)
        parity(sf, sb, "subscription", True, "blockchain.transaction.subscribe", m1, repeat=False)
        first = cli("sendrawtransaction", first_hex)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not all(
                server.call("blockchain.transaction.get", first)[1] is None for server in (f, b)):
            time.sleep(0.5)
        bchn_peer = LightPeer(18444)
        bchn_peer.send(b"tx", bytes.fromhex(second_hex))
        time.sleep(2)
        bchn_peer.close()
        proof_seconds = {}
        started = time.monotonic()
        while time.monotonic() - started < 60 and len(proof_seconds) < 2:
            for name, server in (("Fulcrum", f), ("bridge", b)):
                if name not in proof_seconds and server.call("blockchain.transaction.dsproof.get", first)[0]:
                    proof_seconds[name] = round(time.monotonic() - started, 1)
            time.sleep(0.5)
        seen["double-spend proof seen after seconds"] = proof_seconds
        seen["kth alone: double-spend proof at that bridge"] = bool(
            late.call("blockchain.transaction.dsproof.get", first)[0])
        seen["BCHN's proof"] = (call(BCHN, "getdsproof", first)[0] or {}).get("dspid")
        parity(f, b, "double-spend proof", True, "blockchain.transaction.dsproof.get", first)
        parity(f, b, "double-spend proof", True, "blockchain.transaction.dsproof.list")
        subscription_parity("the proof arrives", "blockchain.transaction.dsproof.subscribe", first_id, started, 30)

        stats = b.call("light_client.stats")[0]
        seen["bridge peers"] = stats.get("peers") if isinstance(stats, dict) else None

        # Broadcasts: one through each server, each seen by the other.
        one, two = spend(new_coin(), cli("getnewaddress")), spend(new_coin(), cli("getnewaddress"))
        one_id, two_id = (sha256d(bytes.fromhex(x))[::-1].hex() for x in (one, two))
        through_bridge = b.call("blockchain.transaction.broadcast", one)
        through_fulcrum = f.call("blockchain.transaction.broadcast", two)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (f.call("blockchain.transaction.get", one_id)[1] is None
                                                  and b.call("blockchain.transaction.get", two_id)[1] is None):
            time.sleep(0.5)
        crossed = (f.call("blockchain.transaction.get", one_id)[1] is None
                   and b.call("blockchain.transaction.get", two_id)[1] is None)
        RESULTS["parity"].append({
            "group": "broadcast", "method": "blockchain.transaction.broadcast", "params": ["a new transaction"],
            "data": True, "same": through_bridge[0] == one_id and through_fulcrum[0] == two_id and crossed,
            "differences": [] if crossed else ["the transaction one server took did not reach the other"],
            "fulcrum": json.dumps(through_fulcrum[0] if through_fulcrum[1] is None else through_fulcrum[1])[:600],
            "bridge": json.dumps(through_bridge[0] if through_bridge[1] is None else through_bridge[1])[:600],
            "ms": {"fulcrum": [through_fulcrum[2], None], "bridge": [through_bridge[2], None]}})
        parity(f, b, "broadcast", False, "blockchain.transaction.broadcast", one, repeat=False)
        parity(f, b, "broadcast", True, "blockchain.transaction.broadcast", "00", repeat=False)
        parity(f, b, "server", False, "daemon.passthrough", "getblockcount", [], repeat=False)
        parity(f, b, "server", False, "daemon.passthrough", {"method": "getblockcount", "params": []},
               repeat=False)
        parity(f, b, "server", False, "server.ping", repeat=False)
        parity(f, b, "server", False, "server.peers.subscribe", repeat=False)
        parity(f, b, "server", False, "server.donation_address", repeat=False)
        parity(f, b, "server", False, "server.banner", repeat=False)
        parity(f, b, "fees", False, "mempool.get_info", repeat=False)

        # A block: the tip, the payment's height, the script's status, and the
        # proof that the block settles, notified by each server.
        mined_at = time.monotonic()
        cli("generatetoaddress", "1", wallet)
        seen["block for the subscriptions"] = tip + 1
        subscription_parity("a block", "blockchain.headers.subscribe", None, mined_at, 60)
        subscription_parity("a block", "blockchain.transaction.subscribe", m1, mined_at, 30)
        subscription_parity("a block", "blockchain.scripthash.subscribe", hashes["payee 0"], mined_at, 30)
        subscription_parity("a block", "blockchain.transaction.dsproof.subscribe", first_id, mined_at, 30)

        # A double spend of a coin from an older block, of a script no wallet
        # asked about: the bridge holds neither, and fetches nothing to check
        # the proof, so Fulcrum shows BCHN's proof and the bridge none. The
        # coin is made here, then buried under more blocks than the bridge
        # keeps the outputs of (24).
        old_txid = cli("sendtoaddress", cli("getnewaddress"), "1.0")
        cli("generatetoaddress", "26", wallet)
        old_height = int(cli("getblockcount"))
        wait_kth_height(old_height, 180)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            stats = b.call("light_client.stats")[0]
            if (isinstance(stats, dict) and stats.get("filter_height") == old_height
                    and f.call("blockchain.block.header", old_height)[1] is None):
                break
            time.sleep(0.5)
        old = next(u for u in json.loads(cli("listunspent", "26"))
                   if u["txid"] == old_txid and abs(u["amount"] - 1.0) < 1e-9)
        cli("lockunspent", "false", json.dumps([{"txid": old["txid"], "vout": old["vout"]}]))
        old_first_hex = spend(old, cli("getnewaddress"))
        old_second_hex = spend(old, cli("getnewaddress"), 0.002)
        old_first = cli("sendrawtransaction", old_first_hex)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not all(
                server.call("blockchain.transaction.get", old_first)[1] is None for server in (f, b)):
            time.sleep(0.5)
        bchn_peer = LightPeer(18444)
        bchn_peer.send(b"tx", bytes.fromhex(old_second_hex))
        time.sleep(2)
        bchn_peer.close()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not f.call("blockchain.transaction.dsproof.get", old_first)[0]:
            time.sleep(0.5)
        time.sleep(5)
        seen["double-spend proof of an older coin"] = {
            "confirmations": old["confirmations"],
            "Fulcrum": bool(f.call("blockchain.transaction.dsproof.get", old_first)[0]),
            "bridge": bool(b.call("blockchain.transaction.dsproof.get", old_first)[0])}
        parity(f, b, "double-spend proof: an older coin", True, "blockchain.transaction.dsproof.get", old_first,
               on_purpose="the bridge checks a proof only against a coin it holds, and fetching the coin "
                          "would tell a peer which payment the wallet watches")

        # Row 1012: CashTokens and an identity (CHIP-BCMR), made with BCHN's
        # wallet and mined by BCHN, read through the bridge on kth: Fulcrum's
        # token answers must match, and the bridge's own light_client.*
        # methods must give what BCHN's chain holds.
        token_checks: list = []

        def token_check(name: str, ok: bool, detail) -> None:
            token_checks.append({"check": name, "ok": bool(ok), "detail": json.dumps(detail)[:400]})

        def send_raw(inputs: list, outputs: list) -> str:
            raw = cli("createrawtransaction", json.dumps(inputs), json.dumps(outputs))
            signed = json.loads(cli("signrawtransactionwithwallet", raw))
            return cli("sendrawtransaction", signed["hex"])

        def token(category: str, amount: int = 0, capability: str | None = None, commitment: str = "") -> dict:
            data: dict = {"category": category, "amount": str(amount)}
            if capability is not None:
                data["nft"] = {"capability": capability, "commitment": commitment}
            return data

        def to(address: str, data: dict | None = None, sats: int = 1000) -> dict:
            value: dict = {"amount": round(sats / 1e8, 8)}
            if data is not None:
                value["tokenData"] = data
            return {address: value}

        def bcmr(registry: bytes, uri: str) -> dict:
            return {"data": ["42434d52", hashlib.sha256(registry).hexdigest(), uri.encode().hex()]}

        def mine_and_wait() -> int:
            cli("generatetoaddress", "1", wallet)
            height = int(cli("getblockcount"))
            wait_kth_height(height, 120)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                stats = b.call("light_client.stats")[0]
                if isinstance(stats, dict) and stats.get("filter_height") == height:
                    break
                time.sleep(0.2)
            return height

        def height_of(txid: str) -> int:
            info = call(BCHN, "getrawtransaction", txid, True)[0]
            return call(BCHN, "getblockheader", info["blockhash"], True)[0]["height"]

        try:
            # The authbase: a transaction with one output; its ID is the category.
            coin = new_coin()
            first = cli("getnewaddress")
            authbase = send_raw([{"txid": coin["txid"], "vout": coin["vout"]}],
                                [{first: round(coin["amount"] - 0.001, 8)}])
            category = authbase
            mine_and_wait()
            base_value = int(round((coin["amount"] - 0.001) * 1e8))
            minter, holder_a, holder_b, change = (cli("getnewaddress") for _ in range(4))
            # Genesis: spends output 0 of the authbase: a minting NFT with all
            # the fungible tokens, part sent on, and an immutable NFT.
            genesis = send_raw([{"txid": authbase, "vout": 0}], [
                to(minter, token(category, 1_000_000, "minting")),
                to(holder_a, token(category, 250)),
                to(holder_b, token(category, 0, "none", "c0ffee")),
                {change: round((base_value - 3000 - 100_000) / 1e8, 8)},
            ])
            mine_and_wait()
            # Update 1: the minting NFT (the identity output) mints a mutable
            # NFT, keeps the identity at output 0, and publishes a registry.
            registry_1 = b'{"name":"one"}'
            minter_2, holder_c = cli("getnewaddress"), cli("getnewaddress")
            fee_coin = new_coin()
            update_1 = send_raw([{"txid": genesis, "vout": 0}, {"txid": fee_coin["txid"], "vout": fee_coin["vout"]}], [
                to(minter_2, token(category, 999_000, "minting")),
                bcmr(registry_1, "example.com"),
                to(holder_c, token(category, 1000, "mutable", "01")),
                {cli("getnewaddress"): round(fee_coin["amount"] - 0.001, 8)},
            ])
            mine_and_wait()
            # Fungible tokens move, and 50 are burned (left out of the outputs).
            holder_d = cli("getnewaddress")
            fee_coin = new_coin()
            move = send_raw([{"txid": genesis, "vout": 1}, {"txid": fee_coin["txid"], "vout": fee_coin["vout"]}], [
                to(holder_d, token(category, 120)),
                to(holder_a, token(category, 80)),
                {cli("getnewaddress"): round(fee_coin["amount"] - 0.001, 8)},
            ])
            # Update 2, in the same block: another registry.
            registry_2 = b'{"name":"two"}'
            minter_3 = cli("getnewaddress")
            fee_coin = new_coin()
            update_2 = send_raw([{"txid": update_1, "vout": 0}, {"txid": fee_coin["txid"], "vout": fee_coin["vout"]}], [
                to(minter_3, token(category, 999_000, "minting")),
                bcmr(registry_2, "ipfs://two"),
                {cli("getnewaddress"): round(fee_coin["amount"] - 0.001, 8)},
            ])
            tip_2 = mine_and_wait()

            # BCHN's view: the category's unspent outputs.
            outputs = {(genesis, 1): None, (genesis, 2): None, (update_1, 2): None, (move, 0): None,
                       (move, 1): None, (update_2, 0): None}
            unspent = {}
            for (txid, n) in outputs:
                out = cli("gettxout", txid, str(n))
                if out not in ("", "null"):
                    unspent[(txid, n)] = json.loads(out)
            def amount(o) -> int:
                return int(o.get("tokenData", {}).get("amount", "0"))
            def capability(o):
                return o.get("tokenData", {}).get("nft", {}).get("capability")
            total = sum(amount(o) for o in unspent.values())
            reserved = sum(amount(o) for o in unspent.values() if capability(o) in ("minting", "mutable"))
            truth = {"total": str(total), "reserved": str(reserved), "circulating": str(total - reserved),
                     "nfts": {"minting": sum(capability(o) == "minting" for o in unspent.values()),
                              "mutable": sum(capability(o) == "mutable" for o in unspent.values()),
                              "immutable": sum(capability(o) == "none" for o in unspent.values())}}
            supply = b.call("light_client.token.supply", category)
            got = supply[0] if supply[1] is None else supply[1]
            token_check("light_client.token.supply: total, reserved, circulating and NFTs as BCHN's unspent outputs",
                        isinstance(got, dict) and {k: got.get(k) for k in truth} == truth,
                        {"bridge": {k: (got or {}).get(k) for k in truth} if isinstance(got, dict) else got,
                         "BCHN": truth, "ms": supply[2]})
            holders = len({o["scriptPubKey"]["hex"] for o in unspent.values()})
            token_check("light_client.token.supply: one holder per locking script",
                        isinstance(got, dict) and len(got.get("holders", [])) == holders,
                        {"bridge": len(got.get("holders", [])) if isinstance(got, dict) else got, "BCHN": holders})
            found = b.call("light_client.token.genesis", category)
            token_check("light_client.token.genesis: the spender of the authbase's output 0",
                        isinstance(found[0], dict) and found[0].get("tx_hash") == genesis
                        and found[0].get("height") == height_of(genesis),
                        {"bridge": found[0] if found[1] is None else found[1], "BCHN": [genesis, height_of(genesis)]})
            # Fulcrum's token answers, for each holder: same as Fulcrum's.
            for name, address in (("minter", minter_3), ("holder a", holder_a), ("holder b", holder_b),
                                  ("holder d", holder_d)):
                sh = hashlib.sha256(bytes.fromhex(call(BCHN, "validateaddress", address)[0]["scriptPubKey"])).digest()[::-1].hex()
                parity(f, b, f"tokens: {name}", True, "blockchain.scripthash.listunspent", sh, "tokens_only",
                       normal=by_outpoint)
                parity(f, b, f"tokens: {name}", True, "blockchain.scripthash.get_balance", sh, "include_tokens")
                balances = b.call("light_client.token.balances", sh)[0]
                mine = [o for o in unspent.values() if o["scriptPubKey"]["hex"] ==
                        call(BCHN, "validateaddress", address)[0]["scriptPubKey"]]
                token_check(f"light_client.token.balances of {name}",
                            isinstance(balances, list) and sum(int(x["amount"]) for x in balances) == sum(amount(o) for o in mine)
                            and sum(len(x["nfts"]) for x in balances) == sum(1 for o in mine if capability(o)),
                            {"bridge": balances, "BCHN": [o.get("tokenData") for o in mine]})

            # The identity: authbase, genesis, update 1, update 2.
            resolved = b.call("light_client.bcmr.resolve", authbase, height_of(authbase), True)
            chain = resolved[0] if resolved[1] is None else resolved[1]
            token_check("light_client.bcmr.resolve: the authhead and its registry after two updates",
                        isinstance(chain, dict) and chain.get("authhead") == update_2 and chain.get("height") == tip_2
                        and chain.get("burned") is False
                        and chain.get("publication", {}).get("hash") == hashlib.sha256(registry_2).hexdigest()
                        and chain.get("publication", {}).get("uris") == ["ipfs://two"]
                        and [s_["tx_hash"] for s_ in chain.get("steps", [])][-3:] == [genesis, update_1, update_2],
                        {"bridge": chain, "BCHN": [genesis, update_1, update_2]})
            verified = b.call("light_client.bcmr.verify_registry", registry_2.hex(), hashlib.sha256(registry_2).hexdigest())[0]
            refused = b.call("light_client.bcmr.verify_registry", registry_1.hex(), hashlib.sha256(registry_2).hexdigest())[0]
            token_check("light_client.bcmr.verify_registry: the published hash, and not another registry's",
                        verified is True and refused is False, [verified, refused])
            extension = [call(BCHN, "getrawtransaction", t, False)[0] for t in (authbase, genesis)]
            extended = b.call("light_client.bcmr.resolve_extension", authbase, extension)
            token_check("light_client.bcmr.resolve_extension: from a registry's authchain to the same authhead",
                        isinstance(extended[0], dict) and extended[0].get("authhead") == update_2,
                        extended[0] if extended[1] is None else extended[1])
            # The identity burned: output 0 is a data output.
            fee_coin = new_coin()
            burn = send_raw([{"txid": update_2, "vout": 0}, {"txid": fee_coin["txid"], "vout": fee_coin["vout"]}], [
                {"data": ["6275726e"]},
                to(cli("getnewaddress"), token(category, 999_000, "minting")),
                {cli("getnewaddress"): round(fee_coin["amount"] - 0.001, 8)},
            ])
            mine_and_wait()
            burned = b.call("light_client.bcmr.resolve", authbase, height_of(authbase))
            chain = burned[0] if burned[1] is None else burned[1]
            token_check("light_client.bcmr.resolve: a burned identity",
                        isinstance(chain, dict) and chain.get("authhead") == burn and chain.get("burned") is True
                        and chain.get("publication") is None, chain)
        except (subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError, StopIteration) as error:
            token_check("tokens and identity made and read", False, f"{type(error).__name__}: {error}")
        RESULTS["tokens"] = token_checks

        # What it cost each side.
        latency: dict = {}
        for r in RESULTS["parity"]:
            for server in ("fulcrum", "bridge"):
                first_ms, again_ms = r["ms"][server]
                bucket = latency.setdefault(r["group"].split(":")[0], {}).setdefault(server, [[], []])
                if first_ms is not None:
                    bucket[0].append(first_ms)
                if again_ms is not None:
                    bucket[1].append(again_ms)
        measure["latency ms, median first / repeated / slowest first"] = {
            group: {server: [sorted(v[0])[len(v[0]) // 2] if v[0] else None,
                             sorted(v[1])[len(v[1]) // 2] if v[1] else None,
                             max(v[0]) if v[0] else None] for server, v in servers.items()}
            for group, servers in latency.items()}

        def kth_bytes(command: str) -> str:
            return subprocess.run(["docker", "exec", "kth", "sh", "-c", command],
                                  capture_output=True, text=True).stdout.strip()

        measure["stored bytes"] = {
            "Fulcrum database": tree_bytes(home / "db"),
            "bridge from genesis": tree_bytes(Path("/tmp/bridge-genesis")),
            f"bridge from birthday {birthday}": tree_bytes(Path("/tmp/bridge-birthday")),
            "kth basic filter checkpoints": kth_bytes("wc -c < /data/e2e/compact_filter_checkpoints"),
            "kth script filter checkpoints": kth_bytes("wc -c < /data/e2e/script_filter_checkpoints"),
            "kth input prefix filter checkpoints": kth_bytes("wc -c < /data/e2e/input_prefix_filter_checkpoints"),
            "kth whole database": kth_bytes("du -sb /data/e2e | cut -f1"),
        }
        late_stats = late.call("light_client.stats")[0]
        stats = b.call("light_client.stats")[0]
        # Fulcrum reads every block from BCHN's RPC as hex: at least twice
        # each block's size, before the JSON around it.
        block_sizes = sum(call(BCHN, "getblock", call(BCHN, "getblockhash", h)[0], 1)[0]["size"]
                          for h in range(int(cli("getblockcount")) + 1))

        def p2p_total(st) -> int | None:
            return (sum(st.get(k) or 0 for k in ("header_bytes", "filter_bytes", "block_bytes"))
                    if isinstance(st, dict) else None)

        measure["downloaded bytes"] = {
            "Fulcrum, every block from BCHN's RPC (hex, at least)": 2 * block_sizes,
            "bridge from genesis, headers + filters + blocks over P2P": p2p_total(stats),
            f"bridge from birthday {birthday}, headers + filters + blocks over P2P (no wallet calls)":
                p2p_total(late_stats),
            "bridge from genesis, over P2P": stats,
            f"bridge from birthday {birthday}, over P2P (no wallet calls)": late_stats,
            "the wallet, from Fulcrum": f.received,
            "the wallet, from the bridge": b.received,
        }
        RESULTS["parity_measure"] = measure
        data_differences = [r for r in RESULTS["parity"] if r["data"] and not r["same"]]
        seen["calls"] = len(RESULTS["parity"])
        seen["data calls that differ"] = len(data_differences)
        seen["other calls that differ"] = sum(1 for r in RESULTS["parity"] if not r["data"] and not r["same"])
        reasons: dict = {}
        for r in RESULTS["parity"]:
            for d in r.get("on_purpose") or []:
                why = d[d.index("(on purpose: ") + 13:-1]
                reasons[why] = reasons.get(why, 0) + 1
        seen["differences on purpose (calls, why)"] = reasons
        ok = bool(seen["kth at the tip"] and all(seen["synced"].values())
                  and seen["relayed payments in both mempools"] and not data_differences)
    except subprocess.CalledProcessError as error:
        seen["error"] = f"{error.cmd[6] if len(error.cmd) > 6 else error.cmd}: {(error.stderr or '').strip()[:200]}"
    except (OSError, ValueError, KeyError, IndexError, TypeError, StopIteration, struct.error) as error:
        seen["error"] = f"{type(error).__name__}: {error}"
    finally:
        for connection in clients:
            connection.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
        (OUT / "bridge-parity.json").write_text(json.dumps({"seen": seen, "measure": measure,
                                                            "calls": RESULTS.get("parity", [])}, indent=1))
    tokens = RESULTS.get("tokens") or []
    row(1012, "Exchanges and wallets with tokens (bch-electrum-bridge on kth, BCHN a relay peer)",
        "a category made, minted, moved, burned and published (BCMR) twice, then the identity burned, all by "
        "BCHN: Fulcrum's token answers match, and the bridge's light_client.token.* and light_client.bcmr.* "
        "give BCHN's supply, holders, genesis, authhead, registry and burn, with no index",
        bool(tokens) and all(t["ok"] for t in tokens),
        {t["check"]: t["ok"] if t["ok"] else t["detail"] for t in tokens})
    row(1011, client, "Fulcrum on BCHN and the bridge on kth (BCHN a relay peer), both synced to the same "
        "chain, give the same answer to every data call (headers, each scripthash call for five scripts, raw "
        "transactions by ID alone with a fully spent one, Merkle proofs, IDs by position, unspent outputs, RPA "
        "searches over every 60-block window, RPA mempool, double-spend proofs, broadcasts) and the same "
        "notifications (script, transaction, tip, double-spend proof), apart from what the bridge leaves out "
        "on purpose (an unconfirmed fee whose coins it does not hold, which would mean a download); shapes, "
        "times, storage and downloads are reported, and what a bridge on kth alone misses", ok, seen)


def report() -> str:
    lines = ["# Knuth RPC end-to-end (kth, no proxy) vs BCHN 29.2.0 on regtest", ""]
    for k, v in RESULTS["setup"].items():
        if k != "kth_log":
            lines.append(f"- {k}: `{v}`")
    lines += ["", "## Node calls", "", "| Call | Apps (! = fatal) | Verdict | kth | BCHN |", "|---|---|---|---|---|"]
    for r in RESULTS["methods"]:
        cell = lambda d: json.dumps(d)[:180].replace("|", "\\|")
        lines.append(f"| `{r['call']}` | {r['apps']} | **{r['verdict']}** | {cell(r['kth'])} | {cell(r['bchn'])} |")
    lines += ["", "## Client transports (each client's exact bytes and read logic)", "",
              "| Case | Apps | Verdict | kth | BCHN |", "|---|---|---|---|---|"]
    for r in RESULTS["transport"]:
        lines.append(f"| {r['case']} | {r['apps']} | **{r['verdict']}** | {r['kth']['note']} | {r['bchn']['note']} |")
    lines += ["", "## Mining", "", "| Step | Node | Result |", "|---|---|---|"]
    for r in RESULTS["mining"]:
        detail = r.get("error") or r.get("result")
        extra = f" (merkle branch: {r['merkle_branch_present']})" if "merkle_branch_present" in r else ""
        lines.append(f"| {r['step']} | {r['node']} | {json.dumps(detail)[:200].replace('|', '/')}{extra} |")
    lines += ["", "## #773 rows: each client's own acceptance of kth's reply", "",
              "| Row | Client | Check | Verdict | Detail |", "|---|---|---|---|---|"]
    for r in RESULTS["rows"]:
        lines.append(f"| {r['row']} | {r['client']} | {r['check']} | **{r['verdict']}** | "
                     f"{r['detail'][:400].replace('|', '/')} |")
    if RESULTS.get("parity"):
        lines += ["", "## Row 1011: Electrum calls, Fulcrum on BCHN and bch-electrum-bridge on kth", "",
                  "| Group | Call | Params | Must match | Verdict | ms Fulcrum first/again | ms bridge first/again "
                  "| Differences |", "|---|---|---|---|---|---|---|---|"]
        for r in RESULTS["parity"]:
            params = json.dumps(r["params"])[:60].replace("|", "/")
            times = lambda v: "/".join("-" if t is None else str(t) for t in v)
            lines.append(f"| {r['group']} | `{r['method']}` | {params} | {'yes' if r['data'] else 'no'} | "
                         f"**{'differs' if not r['same'] else 'on purpose' if r.get('on_purpose') else 'same'}** "
                         f"| {times(r['ms']['fulcrum'])} | {times(r['ms']['bridge'])} | "
                         f"{'; '.join(r['differences'] + (r.get('on_purpose') or []))[:600].replace('|', '/')} |")
        lines += ["", "### Row 1011 measurements", "", "```",
                  json.dumps(RESULTS.get("parity_measure"), indent=1), "```"]
    gaps = [r["call"] for r in RESULTS["methods"] if r["verdict"].startswith("GAP")]
    gaps += [r["case"] for r in RESULTS["transport"] if r["verdict"] == "GAP"]
    lines += ["", f"## {len(gaps)} gaps", ""] + [f"- {g}" for g in gaps]
    if "kth_log" in RESULTS["setup"]:
        lines += ["", "## kth log tail", "", "```", RESULTS["setup"]["kth_log"], "```"]
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        s = setup()
        RESULTS["setup"]["data"] = {k: v for k, v in s.items()
                                    if not k.startswith("fresh") and k not in ("others", "confirmed")}
        for name, phase in (("methods", lambda: methods(s)), ("transport", transport),
                            ("mining", lambda: mining(s)), ("rows", lambda: rows(s))):
            try:
                phase()
            except Exception as error:  # one broken phase must not hide the others
                RESULTS["setup"][f"{name}_crashed"] = f"{type(error).__name__}: {error}"
    finally:
        if BRIDGE is not None:
            BRIDGE.terminate()
        if I2P or SERVE:
            subprocess.run(["docker", "stop", "-t", "15", "kth-b"], capture_output=True)
            subprocess.run(["docker", "cp", "kth-b:/data/kth-debug.log", str(OUT / "kth-b-debug.log")],
                           capture_output=True)
            (OUT / "i2pd.log").write_text(subprocess.run(["docker", "logs", "i2pd"], capture_output=True,
                                                         text=True).stdout[-200000:]
                                       + subprocess.run(["docker", "logs", "i2pd"], capture_output=True,
                                                        text=True).stderr[-50000:])
        if ANNOUNCE or COMPACT:
            subprocess.run(["bitcoin-cli", "-regtest", f"-datadir={BCHN_B_DIR}", "-rpcuser=e2e",
                            "-rpcpassword=e2e", f"-rpcport={BCHN_B[1]}", "stop"], capture_output=True)
            b_log = BCHN_B_DIR / "regtest" / "debug.log"
            if b_log.exists():
                (OUT / "bchn-b-debug.log").write_text(b_log.read_text(errors="replace")[-400000:])
        RESULTS["setup"].setdefault("kth_log", kth_log(80))
        # kth logs to a file as a daemon; stopped first so what it buffered is written.
        subprocess.run(["docker", "stop", "-t", "15", "kth"], capture_output=True)
        subprocess.run(["docker", "cp", "kth:/data/kth-debug.log", str(OUT / "kth-debug.log")],
                       capture_output=True)
        (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1, default=str))
        text = report()
        (OUT / "results.md").write_text(text)
        print(text)


if __name__ == "__main__":
    main()
