#!/usr/bin/env python3
"""TEST ONLY patch for SRI's mining_device.

mine() searches one 2^32 nonce space per job. It advances header.nonce and
never rolls ntime or version, so a target above one nonce space cannot be
met by waiting longer. The replacement adds 1 to header.time when nonce
wraps back to the worker's starting value or the addition overflows, then
keeps hashing.

kth accepts ntime up to two hours ahead (work.hpp line 38). P2Pool accepts
ntime up to two hours ahead (mining.rs lines 724-726).

The matched snippet is copied from the pinned sv2-apps tree. It must occur
once; a changed upstream fails here instead of mining the old loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

# TEST ONLY. handicap == 0 loop in mine(), the path the device runs.
# The similar loop above it sleeps when handicap != 0 and is not this text.
ORIGINAL = """\
        loop {
            // Prefer fast path with micro-batching when possible
            if kill.load(Ordering::Relaxed) {
                break;
            }
            let can_fast =
                miner.fast_hasher.is_some() && miner.target.is_some() && miner.header.is_some();
            if can_fast {
                let header = miner.header.as_mut().unwrap();
                let time = header.time;
                let start = header.nonce;
                let tgt_le = miner.target.unwrap().to_little_endian();
                let fast = miner.fast_hasher.as_mut().unwrap();
                let mut found = None;
                let batch = nonces_per_call();
                for i in 0..batch {
                    let nonce = start.wrapping_add(i);
                    let hash = fast.hash_with_nonce_time(nonce, time);
                    if hash_meets_target_le(&hash, &tgt_le) {
                        found = Some((nonce, hash));
                        break;
                    }
                }
                if let Some((nonce, hash)) = found {
                    header.nonce = nonce;
                    info!(
                        "Found share with nonce: {}, for target: {:?}, with hash: {:?}",
                        header.nonce, miner.target, hash,
                    );
                    let job_id = miner.job_id.unwrap();
                    let version = miner.version;
                    share_send
                        .try_send((nonce, job_id, version.unwrap(), time))
                        .unwrap();
                }
                // Advance nonce window
                header.nonce = start.wrapping_add(batch);
"""

REPLACEMENT = """\
        let nonce_origin = miner.header.as_ref().map(|header| header.nonce);
        loop {
            // Prefer fast path with micro-batching when possible
            if kill.load(Ordering::Relaxed) {
                break;
            }
            let can_fast =
                miner.fast_hasher.is_some() && miner.target.is_some() && miner.header.is_some();
            if can_fast {
                let header = miner.header.as_mut().unwrap();
                let time = header.time;
                let start = header.nonce;
                let tgt_le = miner.target.unwrap().to_little_endian();
                let fast = miner.fast_hasher.as_mut().unwrap();
                let mut found = None;
                let batch = nonces_per_call();
                for i in 0..batch {
                    let nonce = start.wrapping_add(i);
                    let hash = fast.hash_with_nonce_time(nonce, time);
                    if hash_meets_target_le(&hash, &tgt_le) {
                        found = Some((nonce, hash));
                        break;
                    }
                }
                if let Some((nonce, hash)) = found {
                    header.nonce = nonce;
                    info!(
                        "Found share with nonce: {}, for target: {:?}, with hash: {:?}",
                        header.nonce, miner.target, hash,
                    );
                    let job_id = miner.job_id.unwrap();
                    let version = miner.version;
                    share_send
                        .try_send((nonce, job_id, version.unwrap(), time))
                        .unwrap();
                }
                // Advance nonce window. TEST ONLY: one nonce space per job.
                // Roll ntime when nonce wraps to the worker start or overflows.
                let next = start.wrapping_add(batch);
                if Some(next) == nonce_origin || next < start {
                    header.time = header.time.wrapping_add(1);
                }
                header.nonce = next;
"""


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: python3 scripts/patch_mining_device.py <path-to-mod.rs>"
        )
    path = Path(sys.argv[1])
    data = path.read_bytes()
    original = ORIGINAL.encode("utf-8")
    found = data.count(original)
    assert found == 1, (
        "TEST ONLY mining_device patch: expected the mine() nonce loop once "
        f"in {path}, found {found}. The pinned sv2-apps source changed."
    )
    path.write_bytes(data.replace(original, REPLACEMENT.encode("utf-8"), 1))


if __name__ == "__main__":
    main()
