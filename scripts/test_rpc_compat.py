from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import tempfile
import time
import unittest

import struct

from rpc_compat import (
    BASE58_ALPHABET, EMPTY_SELECTION_JOB_ID, GENESIS_HEX, TEMPLATE_ATTEMPTS, Compat, TxStore,
    coinbase_height_prefix, current_peer_count, hex_rev, input_sigchecks, merkle_root,
    node_version, parse_transaction, selection_job_id, sha256d, txid_of, write_compact_size,
)


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
        # kth names an empty selection by the hash of no txids.
        template = {
            "previousblockhash": ACTIVE, "height": 11, "job_id": EMPTY_SELECTION_JOB_ID,
            "bits": "1d00ffff", "target": "native-target", "coinbasevalue": 50,
            "mintime": 100, "curtime": 200,
        }
        compat = self.compat({"getblocktemplatelight": template, "getrawmempool": []})
        result = compat.handle("getblocktemplate", [{}])["result"]
        for key in ("previousblockhash", "height", "bits", "target", "coinbasevalue"):
            self.assertEqual(result[key], template[key])
        self.assertEqual(result["transactions"], [])

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


def error(code, message):
    return {"result": None, "error": {"code": code, "message": message}, "id": 1}


SEQUENCE_FINAL = 0xFFFFFFFF
SIG = b"\x30" + bytes(70)  # a 71-byte stand-in for a DER signature
SCHNORR_SIG = bytes(range(64)) + b"\x41"
PUBKEY = b"\x02" + bytes(range(32))
HASH20 = bytes(range(20))
P2PKH = b"\x76\xa9\x14" + HASH20 + b"\x88\xac"
P2SH20 = b"\xa9\x14" + HASH20 + b"\x87"
P2SH32 = b"\xaa\x20" + bytes(range(32)) + b"\x87"
OP_IF, OP_ELSE, OP_ENDIF, OP_BEGIN, OP_UNTIL = b"\x63", b"\x67", b"\x68", b"\x65", b"\x66"
OP_CHECKSIG, OP_CHECKSIGVERIFY, OP_CHECKMULTISIG = b"\xac", b"\xad", b"\xae"
OP_CHECKDATASIGVERIFY, OP_NOT, OP_DUP, OP_DROP = b"\xbb", b"\x91", b"\x76", b"\x75"


def push(data: bytes) -> bytes:
    if len(data) < 0x4C:
        return bytes([len(data)]) + data
    if len(data) <= 0xFF:
        return b"\x4c" + bytes([len(data)]) + data
    return b"\x4d" + struct.pack("<H", len(data)) + data


def transaction(inputs, outputs, version=2, locktime=0) -> bytes:
    out = struct.pack("<i", version) + write_compact_size(len(inputs))
    for prev_txid, index, script_sig, sequence in inputs:
        out += bytes.fromhex(prev_txid)[::-1] + struct.pack("<I", index)
        out += write_compact_size(len(script_sig)) + script_sig + struct.pack("<I", sequence)
    out += write_compact_size(len(outputs))
    for value, script in outputs:
        out += struct.pack("<q", value) + write_compact_size(len(script)) + script
    return out + struct.pack("<I", locktime)


def spend(prev_txid, index=0, script_sig=None, locktime=0, sequence=SEQUENCE_FINAL) -> bytes:
    if script_sig is None:
        script_sig = push(SIG) + push(PUBKEY)
    return transaction([(prev_txid, index, script_sig, sequence)], [(1000, P2PKH)], locktime=locktime)


def confirmed_index(*txs):
    """A block-file index holding `txs` in one confirmed block."""
    block = bytes(80) + write_compact_size(len(txs)) + b"".join(txs)
    blockhash = "33" * 32
    return SimpleNamespace(
        tip_height=999, tip_hash=ORPHAN,
        txindex={txid_of(tx): (blockhash, n) for n, tx in enumerate(txs)},
        read_block=lambda h: block if h == blockhash else None,
        ingest_new=lambda: 0,
    )


def base58check(payload: bytes) -> str:
    data = payload + sha256d(payload)[:4]
    number, text = int.from_bytes(data, "big"), ""
    while number:
        number, digit = divmod(number, 58)
        text = BASE58_ALPHABET[digit] + text
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + text


class SigChecksTest(unittest.TestCase):
    def test_p2pkh_and_p2pk_spends_check_one_signature(self):
        self.assertEqual(input_sigchecks(push(SIG) + push(PUBKEY), P2PKH), 1)
        self.assertEqual(input_sigchecks(push(SIG), push(PUBKEY) + OP_CHECKSIG), 1)

    def test_legacy_multisig_counts_its_keys_and_schnorr_multisig_its_signatures(self):
        redeem = b"\x52" + push(PUBKEY) * 3 + b"\x53" + OP_CHECKMULTISIG  # 2-of-3
        legacy = b"\x00" + push(SIG) + push(SIG)
        self.assertEqual(input_sigchecks(legacy, redeem), 3)
        schnorr = push(b"\x03") + push(SCHNORR_SIG) + push(SCHNORR_SIG)
        self.assertEqual(input_sigchecks(schnorr, redeem), 2)
        self.assertEqual(input_sigchecks(legacy + push(redeem), P2SH20), 3)
        self.assertEqual(input_sigchecks(schnorr + push(redeem), P2SH32), 2)

    def test_scripts_without_signature_checks_check_none(self):
        # A loop covenant like P2Pool's payout tree node: no signature check.
        covenant = b"\x00\x00" + OP_BEGIN + OP_DUP + b"\x8b" + OP_DUP + b"\x55\x9c" + OP_UNTIL + OP_DROP
        self.assertEqual(input_sigchecks(push(bytes(10)) + push(covenant), P2SH32), 0)

    def test_linear_scripts_count_verify_checks_and_a_final_checksig(self):
        redeem = push(PUBKEY) + OP_CHECKDATASIGVERIFY + push(PUBKEY) + OP_CHECKSIG
        unlock = push(SIG) + push(b"msg") + push(SIG) + push(redeem)
        self.assertEqual(input_sigchecks(unlock, P2SH20), 2)
        redeem = push(PUBKEY) + OP_CHECKSIGVERIFY + b"\x51"
        self.assertEqual(input_sigchecks(push(SIG) + push(redeem), P2SH32), 1)

    def test_checks_that_depend_on_the_run_are_unknown(self):
        branches = OP_IF + push(PUBKEY) + OP_CHECKSIG + OP_ELSE + push(PUBKEY) + OP_CHECKSIG + OP_ENDIF
        self.assertIsNone(input_sigchecks(push(SIG) + b"\x51" + push(branches), P2SH32))
        negated = push(PUBKEY) + OP_CHECKSIG + OP_NOT
        self.assertIsNone(input_sigchecks(b"\x00" + push(negated), P2SH20))
        self.assertIsNone(input_sigchecks(b"\x76", P2PKH))  # not push-only


class BlockchainInfoTest(unittest.TestCase):
    def compat(self, results):
        return Compat(Backend(results), SimpleNamespace(), Path("debug.log"), "1.5.0")

    def info(self, mining):
        results = {"getblockchaininfo": {"chain": "Chipnet", "blocks": 10, "headers": 10}}
        if mining is not None:
            results["getmininginfo"] = mining
        return self.compat(results).handle("getblockchaininfo", [])["result"]

    def test_initial_block_download_follows_kth_caught_up_and_fresh(self):
        self.assertIs(self.info({"caughtup": True, "fresh": True})["initialblockdownload"], False)
        self.assertIs(self.info({"caughtup": False, "fresh": True})["initialblockdownload"], True)
        self.assertIs(self.info({"caughtup": True, "fresh": False})["initialblockdownload"], True)

    def test_initial_block_download_is_left_out_when_kth_cannot_say(self):
        self.assertNotIn("initialblockdownload", self.info(None))
        self.assertNotIn("initialblockdownload", self.info({"blocks": 10}))


class BlockHeaderTest(unittest.TestCase):
    def setUp(self):
        self.blockhash, rawhex = next(iter(GENESIS_HEX.items()))
        self.raw = bytes.fromhex(rawhex)[:80]
        version, = struct.unpack_from("<I", self.raw, 0)
        timestamp, bits, nonce = struct.unpack_from("<III", self.raw, 68)
        self.fields = {
            "hash": self.blockhash, "height": 0, "version": version,
            "previousblockhash": hex_rev(self.raw[4:36]), "merkleroot": hex_rev(self.raw[36:68]),
            "time": timestamp, "bits": f"{bits:08x}", "nonce": nonce,
        }

    def compat(self, results):
        return Compat(Backend(results), SimpleNamespace(), Path("debug.log"), "1.5.0")

    def test_non_verbose_header_is_the_80_bytes_of_the_block(self):
        self.assertEqual(txid_of(self.raw), self.blockhash)
        compat = self.compat({"getblockheader": self.fields})
        self.assertEqual(compat.handle("getblockheader", [self.blockhash, False])["result"], self.raw.hex())

    def test_fields_that_do_not_hash_to_the_block_are_refused(self):
        compat = self.compat({"getblockheader": {**self.fields, "nonce": 1}})
        out = compat.handle("getblockheader", [self.blockhash, False])
        self.assertIsNone(out["result"])
        self.assertIsNotNone(out["error"])

    def test_unknown_block_answers_bchn_block_not_found(self):
        compat = self.compat({"getblockheader": lambda _: error(-32603, "object does not exist")})
        for verbose in (True, False):
            out = compat.handle("getblockheader", ["44" * 32, verbose])
            self.assertEqual(out["error"], {"code": -5, "message": "Block not found"})

    def test_verbose_header_reports_bchn_confirmations(self):
        compat = self.compat({
            "getblockheader": self.fields, "getblockcount": 9,
            "getblockhash": lambda params: ok(self.blockhash if params[0] == 0 else ACTIVE),
        })
        header = compat.handle("getblockheader", [self.blockhash])["result"]
        self.assertEqual(list(header)[:3], ["hash", "confirmations", "height"])
        self.assertEqual(header["confirmations"], 10)
        compat = self.compat({"getblockheader": self.fields, "getblockcount": 9, "getblockhash": ACTIVE})
        self.assertEqual(compat.handle("getblockheader", [self.blockhash, True])["result"]["confirmations"], -1)

    def test_verbose_flag_must_be_a_boolean(self):
        out = self.compat({"getblockheader": self.fields}).handle("getblockheader", [self.blockhash, 0])
        self.assertEqual(out["error"]["code"], -3)

    def test_height_past_the_tip_answers_bchn_out_of_range(self):
        compat = self.compat({"getblockhash": lambda _: error(-32602, "block height out of range")})
        self.assertEqual(
            compat.handle("getblockhash", [5000])["error"],
            {"code": -8, "message": "Block height out of range"},
        )


class ValidateAddressTest(unittest.TestCase):
    P2SH32_HASH = "3173ef6623c6b48ffd1a3dcc0cc6489b0a07bb47a37f47cfef4fe69de825c060"
    P2SH32_ADDRESS = "bchtest:pvch8mmxy0rtfrlarg7ucrxxfzds5pamg73h7370aa87d80gyhqxq7fqng6m6"
    P2PKH_HASH = "fc916f213a3d7f1369313d5fa30f6168f9446a2d"
    P2PKH_ADDRESS = "bchtest:qr7fzmep8g7h7ymfxy74lgc0v950j3r295pdnvy3hr"
    TOKEN_P2PKH_ADDRESS = "bchtest:zr7fzmep8g7h7ymfxy74lgc0v950j3r295x8qj2hgs"

    def validate(self, address, chain="Chipnet"):
        compat = Compat(
            Backend({"getblockchaininfo": {"chain": chain}}), SimpleNamespace(),
            Path("debug.log"), "1.5.0",
        )
        return compat.handle("validateaddress", [address])

    def test_p2sh32_pays_hash256_equal(self):
        self.assertEqual(self.validate(self.P2SH32_ADDRESS)["result"], {
            "isvalid": True,
            "address": self.P2SH32_ADDRESS,
            "scriptPubKey": "aa20" + self.P2SH32_HASH + "87",
            "isscript": True,
            "istokenaware": False,
        })

    def test_p2pkh_with_or_without_the_prefix_answers_the_prefixed_form(self):
        for address in (self.P2PKH_ADDRESS, self.P2PKH_ADDRESS.split(":")[1], self.P2PKH_ADDRESS.upper()):
            result = self.validate(address)["result"]
            self.assertEqual(result["address"], self.P2PKH_ADDRESS)
            self.assertEqual(result["scriptPubKey"], "76a914" + self.P2PKH_HASH + "88ac")
            self.assertIs(result["isscript"], False)

    def test_token_aware_address_keeps_its_form(self):
        result = self.validate(self.TOKEN_P2PKH_ADDRESS)["result"]
        self.assertEqual(result["address"], self.TOKEN_P2PKH_ADDRESS)
        self.assertIs(result["istokenaware"], True)
        self.assertEqual(result["scriptPubKey"], "76a914" + self.P2PKH_HASH + "88ac")

    def test_other_networks_and_broken_addresses_are_invalid(self):
        mainnet = "bitcoincash:qr6m7j9njldwwzlg9v7v53unlr4jkmx6eylep8ekg2"
        for address in (
            mainnet,  # a valid mainnet address is not a chipnet one
            "bchtest:qze95tc2dqrnltvxfa5yhunwx5952f34fv28vandn3",  # bad checksum
            "bchtest:Qr7fzmep8g7h7ymfxy74lgc0v950j3r295pdnvy3hr",  # mixed case
            "bchreg:qr7fzmep8g7h7ymfxy74lgc0v950j3r295pdnvy3hr",
            "",
        ):
            self.assertEqual(self.validate(address)["result"], {"isvalid": False}, address)
        self.assertEqual(self.validate(mainnet, chain="mainnet")["result"]["address"], mainnet)

    def test_legacy_addresses_use_the_network_version_bytes(self):
        hash_bytes = bytes.fromhex(self.P2PKH_HASH)
        result = self.validate(base58check(b"\x6f" + hash_bytes))["result"]
        self.assertEqual(result["address"], self.P2PKH_ADDRESS)
        self.assertEqual(self.validate(base58check(b"\x00" + hash_bytes))["result"], {"isvalid": False})
        script = self.validate(base58check(b"\xc4" + hash_bytes))["result"]
        self.assertEqual(script["scriptPubKey"], "a914" + self.P2PKH_HASH + "87")

    def test_unknown_network_is_an_error_not_a_verdict(self):
        compat = Compat(Backend({}), SimpleNamespace(), Path("debug.log"), "1.5.0")
        self.assertIsNotNone(compat.handle("validateaddress", [self.P2PKH_ADDRESS])["error"])
        self.assertEqual(self.validate(7)["error"]["code"], -3)


GBT_KEYS = [
    "capabilities", "version", "previousblockhash", "transactions", "coinbaseaux",
    "coinbasevalue", "longpollid", "target", "mintime", "mutable", "noncerange",
    "sigoplimit", "sizelimit", "curtime", "bits", "height",
]
SUBSIDY = 312_500_000
HEIGHT = 300


class MiningTest(unittest.TestCase):
    def setUp(self):
        self.funding = transaction(
            [("aa" * 32, 0, push(SIG) + push(PUBKEY), SEQUENCE_FINAL)],
            [(5000, P2PKH), (5000, P2PKH), (5000, P2SH32), (5000, P2PKH)],
        )
        self.funding_id = txid_of(self.funding)
        self.a = spend(self.funding_id, 0)
        self.b = spend(self.funding_id, 1)
        self.store = TxStore()
        self.calls = None

    def template(self, selected, fees=0, **fields):
        template = {
            "version": 0x20000000, "previousblockhash": ACTIVE, "height": HEIGHT,
            "coinbasevalue": SUBSIDY + fees, "target": "00000000ffff" + "00" * 26,
            "bits": "1d00ffff", "mintime": 1000, "curtime": 2000, "sizelimit": 32_000_000,
            "sigchecklimit": 226_950, "noncerange": "00000000ffffffff",
            "mutable": ["time", "transactions", "prevblock"],
            "job_id": selection_job_id(sorted(txid_of(tx) for tx in selected)),
        }
        template.update(fields)
        return template

    def compat(self, pooled, template, extra=None):
        """kth with `pooled` {raw: (fee, first seen)} in its mempool."""
        ids = {txid_of(raw): raw for raw in pooled}

        def entry(params):
            raw = ids.get(params[0])
            if raw is None:
                return error(-32602, "transaction not in mempool")
            fee, seen = pooled[raw]
            parents = {hex_rev(txin.prev_hash) for txin in parse_transaction(raw).inputs}
            return ok({"fee": fee, "size": len(raw), "time": seen,
                       "depends": sorted(parents & ids.keys()), "spentby": []})

        results = {"getblocktemplatelight": template, "getrawmempool": list(ids),
                   "getmempoolentry": entry}
        results.update(extra or {})
        backend = Backend(results)
        self.calls = backend.calls
        return Compat(backend, confirmed_index(self.funding), Path("debug.log"), "1.5.0", self.store)

    def relay(self, *txs):
        for tx in txs:
            self.store.add(tx)

    def test_template_lists_kth_selection_with_bchn_fields(self):
        self.relay(self.a, self.b)
        template = self.template([self.a, self.b], fees=300)
        compat = self.compat({self.a: (100, 1500), self.b: (200, 1500)}, template)
        gbt = compat.handle("getblocktemplate", [{"capabilities": ["coinbasetxn"]}])["result"]
        self.assertEqual(list(gbt), GBT_KEYS)
        ids = sorted([txid_of(self.a), txid_of(self.b)])
        self.assertEqual([tx["txid"] for tx in gbt["transactions"]], ids)
        fees = {txid_of(self.a): 100, txid_of(self.b): 200}
        for tx in gbt["transactions"]:
            self.assertEqual(list(tx), ["data", "txid", "hash", "fee", "sigops"])
            self.assertEqual(txid_of(bytes.fromhex(tx["data"])), tx["txid"])
            self.assertEqual(tx["hash"], tx["txid"])
            self.assertEqual(tx["fee"], fees[tx["txid"]])
            self.assertEqual(tx["sigops"], 1)
        self.assertEqual(gbt["coinbasevalue"], SUBSIDY + 300)
        self.assertEqual(gbt["sigoplimit"], 226_950)
        self.assertEqual(gbt["coinbaseaux"], {"flags": ""})

    def test_template_built_before_a_transaction_arrived_leaves_it_out(self):
        self.relay(self.a, self.b)
        template = self.template([self.a], fees=100)
        compat = self.compat({self.a: (100, 1500), self.b: (200, 2500)}, template)
        gbt = compat.handle("getblocktemplate", [])["result"]
        self.assertEqual([tx["txid"] for tx in gbt["transactions"]], [txid_of(self.a)])
        self.assertEqual(gbt["coinbasevalue"], SUBSIDY + 100)

    def test_non_final_transactions_and_their_children_are_left_out(self):
        locked = spend(self.funding_id, 3, locktime=HEIGHT + 5, sequence=0)
        child = spend(txid_of(locked), 0)
        self.relay(self.a, locked, child)
        template = self.template([self.a], fees=100)
        compat = self.compat({self.a: (100, 1500), locked: (100, 1500), child: (100, 1500)}, template)
        gbt = compat.handle("getblocktemplate", [{}])["result"]
        self.assertEqual([tx["txid"] for tx in gbt["transactions"]], [txid_of(self.a)])

    def test_mempool_parents_supply_the_spent_scripts(self):
        child = spend(txid_of(self.a), 0)
        self.relay(self.a, child)
        template = self.template([self.a, child], fees=200)
        compat = self.compat({self.a: (100, 1500), child: (100, 1500)}, template)
        gbt = compat.handle("getblocktemplate", [{}])["result"]
        self.assertEqual([tx["sigops"] for tx in gbt["transactions"]], [1, 1])

    def test_transaction_without_bytes_is_an_error_not_a_template(self):
        self.relay(self.a)
        template = self.template([self.a, self.b], fees=300)
        out = self.compat({self.a: (100, 1500), self.b: (200, 1500)}, template).handle(
            "getblocktemplate", [{}])
        self.assertIsNone(out["result"])
        self.assertIn(txid_of(self.b), out["error"]["message"])

    def test_transaction_with_unreadable_sigchecks_is_an_error_not_a_template(self):
        branches = OP_IF + push(PUBKEY) + OP_CHECKSIG + OP_ELSE + push(PUBKEY) + OP_CHECKSIG + OP_ENDIF
        contract = spend(self.funding_id, 2, script_sig=push(SIG) + b"\x51" + push(branches))
        self.relay(contract)
        template = self.template([contract], fees=100)
        out = self.compat({contract: (100, 1500)}, template).handle("getblocktemplate", [{}])
        self.assertIsNone(out["result"])
        self.assertIn("SigChecks", out["error"]["message"])

    def test_unmatched_selection_is_an_error_after_rereading_the_template(self):
        self.relay(self.a)
        template = self.template([self.b], fees=200)
        out = self.compat({self.a: (100, 1500)}, template).handle("getblocktemplate", [{}])
        self.assertIsNone(out["result"])
        self.assertIsNotNone(out["error"])
        reads = [method for method, _ in self.calls if method == "getblocktemplatelight"]
        self.assertEqual(len(reads), TEMPLATE_ATTEMPTS)

    def test_fees_above_the_coinbase_value_are_an_error(self):
        self.relay(self.a)
        template = self.template([self.a], coinbasevalue=50)
        out = self.compat({self.a: (100, 1500)}, template).handle("getblocktemplate", [{}])
        self.assertIsNone(out["result"])

    def test_sendrawtransaction_keeps_what_kth_accepted(self):
        compat = self.compat({}, self.template([]), {
            "sendrawtransaction": lambda params: ok(txid_of(bytes.fromhex(params[0]))),
        })
        compat.handle("sendrawtransaction", [self.a.hex()])
        self.assertEqual(self.store.get(txid_of(self.a)), self.a)
        compat = self.compat({}, self.template([]), {
            "sendrawtransaction": lambda _: error(-1, "missing transaction parent"),
        })
        compat.handle("sendrawtransaction", [self.b.hex()])
        self.assertIsNone(self.store.get(txid_of(self.b)))
        # Sending a transaction kth already pools supplies its bytes.
        compat = self.compat({self.b: (100, 1500)}, self.template([]), {
            "sendrawtransaction": lambda _: error(-1, "duplicate transaction"),
        })
        self.assertIsNotNone(compat.handle("sendrawtransaction", [self.b.hex()])["error"])
        self.assertEqual(self.store.get(txid_of(self.b)), self.b)

    def test_relayed_transactions_survive_a_restart_until_they_leave_the_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rpc-compat-mempool.txt"
            store = TxStore(path)
            store.add(self.a)
            store.add(self.b)
            reloaded = TxStore(path)
            self.assertEqual(reloaded.get(txid_of(self.a)), self.a)
            later = time.time() + TxStore.RETAIN_SECONDS + 1
            reloaded.retain({txid_of(self.a)}, now=later)
            self.assertIsNone(reloaded.get(txid_of(self.b)))
            self.assertEqual(TxStore(path).get(txid_of(self.a)), self.a)
            self.assertIsNone(TxStore(path).get(txid_of(self.b)))

    def test_unknown_mode_is_refused(self):
        compat = self.compat({}, self.template([]))
        self.assertEqual(compat.handle("getblocktemplate", [{"mode": "other"}])["error"]["code"], -8)

    # ---- blocks ------------------------------------------------------------

    def coinbase(self, value=SUBSIDY + 300, height=HEIGHT):
        script_sig = coinbase_height_prefix(height) + b"/p2pool/"
        return transaction([("00" * 32, 0xFFFFFFFF, script_sig, SEQUENCE_FINAL)], [(value, P2PKH)])

    def block(self, txs, coinbase=None, prev=ACTIVE, timestamp=2000, bits=0x1D00FFFF, version=0x20000000):
        txs = [coinbase or self.coinbase()] + sorted(txs, key=txid_of)
        root = merkle_root([sha256d(tx) for tx in txs])
        header = (
            struct.pack("<i", version) + bytes.fromhex(prev)[::-1] + root
            + struct.pack("<III", timestamp, bits, 7)
        )
        return header, header + write_compact_size(len(txs)) + b"".join(txs)

    def propose(self, block, template, extra=None):
        compat = self.compat({}, template, extra)
        return compat.handle("getblocktemplate", [{"mode": "proposal", "data": block.hex()}])

    def test_proposal_of_kth_selection_is_accepted_with_null(self):
        _, block = self.block([self.a, self.b])
        out = self.propose(block, self.template([self.a, self.b], fees=300))
        self.assertEqual(out, {"result": None, "error": None, "id": 1})

    def test_proposal_rejects_with_bchn_reasons(self):
        template = self.template([self.a, self.b], fees=300)
        cases = {
            "bad-diffbits": self.block([self.a, self.b], bits=0x1D00FFFE)[1],
            "time-too-old": self.block([self.a, self.b], timestamp=999)[1],
            "bad-version(0x00000003)": self.block([self.a, self.b], version=3)[1],
            "bad-cb-amount": self.block([self.a, self.b], coinbase=self.coinbase(SUBSIDY + 301))[1],
            "bad-cb-height": self.block([self.a, self.b], coinbase=self.coinbase(height=HEIGHT + 1))[1],
            "inconclusive-txs-not-in-template": self.block([self.a])[1],
            "inconclusive-not-best-prevblk": self.block([self.a, self.b], prev=ORPHAN)[1],
        }
        header, block = self.block([self.a, self.b])
        cases["bad-txnmrklroot"] = header[:36] + bytes(32) + block[68:]
        for reason, data in cases.items():
            self.assertEqual(self.propose(data, template)["result"], reason, reason)

    def test_proposal_of_a_known_block_is_a_duplicate_and_never_a_template(self):
        header, block = self.block([self.a, self.b])
        out = self.propose(block, self.template([self.a, self.b], fees=300), {
            "getblockheader": {"height": HEIGHT}, "getblockhash": txid_of(header),
        })
        self.assertEqual(out["result"], "duplicate")
        bad = self.propose(b"\x00" * 10, self.template([]))
        self.assertEqual(bad["error"]["code"], -22)

    def submissions(self):
        return [params for method, params in self.calls if method == "submitblocklight"]

    def test_full_block_is_submitted_as_header_coinbase_and_its_own_job(self):
        header, block = self.block([self.a, self.b])
        compat = self.compat({}, self.template([]), {"submitblocklight": lambda _: ok(None)})
        self.assertEqual(compat.handle("submitblock", [block.hex()]), {"result": None, "error": None, "id": 1})
        [(light_block, job_id)] = self.submissions()
        self.assertEqual(light_block, (header + b"\x01" + self.coinbase()).hex())
        self.assertEqual(job_id, selection_job_id(sorted([txid_of(self.a), txid_of(self.b)])))

    def test_expired_job_is_registered_again_while_kth_still_selects_it(self):
        _, block = self.block([self.a, self.b])
        answers = iter([error(-32602, "job_id not found or expired"), ok(None)])
        compat = self.compat({}, self.template([self.a, self.b], fees=300), {
            "submitblocklight": lambda _: next(answers),
        })
        self.assertIsNone(compat.handle("submitblock", [block.hex()])["result"])
        self.assertEqual(len(self.submissions()), 2)

    def test_block_kth_cannot_take_is_an_error_not_a_reject_reason(self):
        _, block = self.block([self.a, self.b])
        compat = self.compat({}, self.template([self.a]), {
            "submitblocklight": lambda _: error(-32602, "job_id not found or expired"),
        })
        out = compat.handle("submitblock", [block.hex()])
        self.assertIsNone(out["result"])
        self.assertEqual(out["error"]["code"], -1)

    def test_known_block_is_a_duplicate_without_submitting(self):
        header, block = self.block([])
        compat = self.compat({}, self.template([]), {
            "getblockheader": {"height": HEIGHT}, "getblockhash": txid_of(header),
            "submitblocklight": lambda _: ok(None),
        })
        self.assertEqual(compat.handle("submitblock", [block.hex()])["result"], "duplicate")
        self.assertEqual(self.submissions(), [])

    def test_kth_verdicts_keep_bchn_shape(self):
        _, block = self.block([])
        compat = self.compat({}, self.template([]), {"submitblocklight": lambda _: ok("duplicate block")})
        self.assertEqual(compat.handle("submitblock", [block.hex()])["result"], "duplicate-inconclusive")
        compat = self.compat({}, self.template([]), {"submitblocklight": lambda _: ok("merkle root mismatch")})
        self.assertEqual(compat.handle("submitblock", [block.hex()])["result"], "merkle root mismatch")

    def test_block_must_start_with_a_coinbase(self):
        _, block = self.block([], coinbase=self.a)
        out = self.compat({}, self.template([])).handle("submitblock", [block.hex()])
        self.assertEqual(out["error"], {"code": -22, "message": "Block does not start with a coinbase"})


if __name__ == "__main__":
    unittest.main()
