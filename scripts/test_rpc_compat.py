from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import tempfile
import time
import unittest

from rpc_compat import Compat, GENESIS_HEX, current_peer_count, node_version


ACTIVE = "11" * 32
ORPHAN = "22" * 32


def ok(result):
    return {"result": result, "error": None, "id": 1}


class Backend:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def call(self, method, params=None):
        params = params or []
        self.calls.append((method, params))
        result = self.results.get(method)
        if callable(result):
            return result(params)
        if result is None:
            return {"result": None, "error": {"code": -32601}, "id": 1}
        return ok(result)


class RpcCompatTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "debug.log"
        self.index = SimpleNamespace(tip_height=999, tip_hash=ORPHAN)

    def compat(self, results):
        return Compat(Backend(results), self.index, self.log, "1.4.0")

    def peer_log(self, peers=0, age=0):
        stamp = datetime.fromtimestamp(time.time() - age).isoformat(sep=" ")
        self.log.write_text(
            f"[{stamp}] [info] [p2p_node:status] Peers: {peers}/16 (target), pending: 0\n"
        )

    def test_raw_orphan_tip_does_not_replace_validated_chain_state(self):
        original = {"chain": "Chipnet", "blocks": 10, "headers": 12, "bestblockhash": ACTIVE}
        compat = self.compat({
            "getblockchaininfo": original.copy(),
            "getblockcount": 10,
            "getbestblockhash": ACTIVE,
            "getblockhash": ACTIVE,
        })
        result = compat.handle("getblockchaininfo", [])["result"]
        self.assertEqual(result, {**original, "chain": "chip"})
        self.assertEqual(compat.handle("getblockcount", [])["result"], 10)
        self.assertEqual(compat.handle("getbestblockhash", [])["result"], ACTIVE)
        self.assertEqual(compat.handle("getblockhash", [10])["result"], ACTIVE)

    def test_backend_chain_failure_is_not_replaced_by_raw_file_height(self):
        compat = self.compat({})
        self.assertIsNotNone(compat.handle("getblockcount", [])["error"])
        self.assertIsNotNone(compat.handle("getbestblockhash", [])["error"])
        self.assertIsNotNone(compat.handle("getblockhash", [999])["error"])

    def test_mining_parent_height_and_difficulty_remain_native(self):
        template = {
            "previousblockhash": ACTIVE, "height": 11, "job_id": "job-1",
            "bits": "1d00ffff", "target": "native-target", "coinbasevalue": 50,
        }
        compat = self.compat({"getblocktemplatelight": template, "getrawmempool": []})
        result = compat.handle("getblocktemplate", [{}])["result"]
        for key in ("previousblockhash", "height", "bits", "target", "coinbasevalue"):
            self.assertEqual(result[key], template[key])
        self.assertEqual(compat.jobs, {ACTIVE: "job-1"})

    def test_orphan_block_is_not_reported_confirmed_or_given_an_active_successor(self):
        blockhash, rawhex = next(iter(GENESIS_HEX.items()))
        self.index.read_block = lambda _: bytes.fromhex(rawhex)
        self.index.height_of = lambda _: 0
        compat = self.compat({
            "getblockheader": {"height": 0}, "getblockhash": ACTIVE, "getblockcount": 10,
        })
        result = compat.handle("getblock", [blockhash])["result"]
        self.assertEqual(result["confirmations"], -1)
        self.assertNotIn("nextblockhash", result)

    def test_active_successor_is_queried_from_native_chain(self):
        blockhash, rawhex = next(iter(GENESIS_HEX.items()))
        self.index.read_block = lambda _: bytes.fromhex(rawhex)
        compat = self.compat({
            "getblockheader": {"height": 0}, "getblockcount": 10,
            "getblockhash": lambda params: ok(blockhash if params[0] == 0 else ACTIVE),
        })
        result = compat.handle("getblock", [blockhash])["result"]
        self.assertEqual(result["confirmations"], 11)
        self.assertEqual(result["nextblockhash"], ACTIVE)

    def test_network_info_reports_measured_zero_peers_and_installed_version(self):
        self.peer_log(0)
        result = self.compat({"getblockchaininfo": {"chain": "Chipnet"}}).handle("getnetworkinfo", [])
        self.assertIsNone(result["error"])
        self.assertEqual(result["result"]["connections"], 0)
        self.assertEqual(result["result"]["subversion"], "/Knuth:1.4.0/")
        self.assertNotIn("networkactive", result["result"])
        self.assertNotIn("connections_out", result["result"])

    def test_missing_or_stale_peer_status_is_explicitly_unavailable(self):
        compat = self.compat({})
        self.assertIsNotNone(compat.handle("getnetworkinfo", [])["error"])
        self.peer_log(8, age=121)
        self.assertIsNone(current_peer_count(self.log))
        self.assertIsNotNone(compat.handle("getnetworkinfo", [])["error"])
        self.log.write_text("[p2p_node:status] Peers: 8/16 (target)\n")
        self.assertIsNone(current_peer_count(self.log))

    def test_latest_peer_status_wins(self):
        self.peer_log(8)
        stamp = datetime.now().isoformat(sep=" ")
        with self.log.open("a") as log:
            log.write(f"[{stamp}] [info] [p2p_node:status] Peers: 0/16 (target)\n")
        self.assertEqual(current_peer_count(self.log), 0)

    def test_recent_status_from_previous_node_process_is_not_reused(self):
        self.peer_log(8)
        with self.log.open("a") as log:
            log.write("[info] [node] Knuth v1.4.0\n")
        self.assertIsNone(current_peer_count(self.log))

    def test_peer_details_are_unavailable_instead_of_a_fabricated_empty_list(self):
        result = self.compat({}).handle("getpeerinfo", [])
        self.assertEqual(result["error"]["code"], -32601)
        self.assertIsNone(result["result"])

    def test_native_peer_methods_pass_through_when_supported(self):
        compat = self.compat({"getnetworkinfo": {"connections": 3}, "getpeerinfo": [{"id": 4}]})
        self.assertEqual(compat.handle("getnetworkinfo", [])["result"], {"connections": 3})
        self.assertEqual(compat.handle("getpeerinfo", [])["result"], [{"id": 4}])

    def test_version_is_read_from_the_node_binary(self):
        result = subprocess.CompletedProcess(["kth", "--version"], 0, "Knuth Node\n  Knuth v1.4.0\n", "")
        with patch("rpc_compat.subprocess.run", return_value=result):
            self.assertEqual(node_version("kth"), "1.4.0")
        with patch("rpc_compat.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(node_version("missing"))


if __name__ == "__main__":
    unittest.main()
