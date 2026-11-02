//! TEST ONLY. The client side of Stratum V2 Noise (sv2-spec 4) for kth's
//! Template Provider, built on the Stratum Reference Implementation's own
//! crates: `noise_sv2` runs the initiator handshake (BIP324 ECDH and the
//! certificate check against the pinned authority key) and the transport
//! ciphers, and `key-utils` decodes the authority key the way SRI clients read
//! it from their configuration. kth's Noise code shares nothing with them.
//!
//!   sv2-noise-bridge --listen 127.0.0.1:P --connect 127.0.0.1:Q --authority KEY
//!     Each plaintext client connection on P gets its own Noise connection to
//!     Q, and SV2 frames are relayed both ways, one Noise frame per SV2 frame
//!     (sv2-spec 4.6). One JSON line per event goes to stdout.
//!   sv2-noise-bridge --probe --connect 127.0.0.1:Q --authority KEY
//!     One handshake: prints its outcome, exits 0 when it succeeds.
//!   sv2-noise-bridge --public-key-of FILE
//!     Prints key-utils' encoding of the public key of a raw 32-byte secret.

use std::env;
use std::io::{self, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::process::ExitCode;
use std::str::FromStr;
use std::thread;
use std::time::Duration;

use key_utils::{Secp256k1PublicKey, Secp256k1SecretKey};
use noise_sv2::{Initiator, NoiseDecryptor, NoiseEncryptor, NoiseEngine};

const HEADER: usize = 6;
const MAC: usize = 16;
const MAX_BLOCK: usize = 65535 - MAC;

fn quoted(text: &str) -> String {
    format!("\"{}\"", text.replace('\\', "/").replace('"', "'"))
}

fn length(header: &[u8]) -> usize {
    header[3] as usize | (header[4] as usize) << 8 | (header[5] as usize) << 16
}

fn aead(error: noise_sv2::AeadError) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, format!("authentication failed: {error:?}"))
}

fn handshake(kth: &mut TcpStream, authority: &str) -> Result<NoiseEngine, String> {
    let key = Secp256k1PublicKey::from_str(authority)
        .map_err(|error| format!("authority key: {error:?}"))?;
    let mut initiator = Initiator::from_raw_k(key.into_bytes())
        .map_err(|error| format!("initiator: {error:?}"))?;
    let first = initiator.step_0().map_err(|error| format!("first flight: {error:?}"))?;
    kth.write_all(&first).map_err(|error| format!("send first flight: {error}"))?;
    let mut second = [0u8; 234];
    kth.read_exact(&mut second).map_err(|error| format!("read second flight: {error}"))?;
    initiator.step_2(second).map_err(|error| format!("second flight: {error:?}"))
}

/// Plaintext frames from the client become one Noise frame each: the header
/// alone, then the payload in blocks of at most 65519 bytes.
fn to_kth(client: &mut TcpStream, kth: &mut TcpStream, cipher: &mut NoiseEncryptor,
    frames: &mut u64) -> io::Result<()> {
    loop {
        let mut header = vec![0u8; HEADER];
        client.read_exact(&mut header)?;
        let mut payload = vec![0u8; length(&header)];
        client.read_exact(&mut payload)?;
        cipher.encrypt(&mut header).map_err(aead)?;
        let mut bytes = header;
        for block in payload.chunks(MAX_BLOCK) {
            let mut block = block.to_vec();
            cipher.encrypt(&mut block).map_err(aead)?;
            bytes.extend_from_slice(&block);
        }
        kth.write_all(&bytes)?;
        *frames += 1;
    }
}

fn from_kth(kth: &mut TcpStream, client: &mut TcpStream, cipher: &mut NoiseDecryptor,
    frames: &mut u64, largest: &mut usize) -> io::Result<()> {
    loop {
        let mut frame = vec![0u8; HEADER + MAC];
        kth.read_exact(&mut frame)?;
        cipher.decrypt(&mut frame).map_err(aead)?;
        let mut remaining = length(&frame);
        *largest = (*largest).max(remaining);
        while remaining > 0 {
            let size = remaining.min(MAX_BLOCK);
            let mut block = vec![0u8; size + MAC];
            kth.read_exact(&mut block)?;
            cipher.decrypt(&mut block).map_err(aead)?;
            frame.extend_from_slice(&block);
            remaining -= size;
        }
        client.write_all(&frame)?;
        *frames += 1;
    }
}

fn serve(id: u64, mut client: TcpStream, target: &str, authority: &str) {
    let mut kth = match TcpStream::connect(target) {
        Ok(stream) => stream,
        Err(error) => {
            println!("{{\"event\":\"handshake\",\"connection\":{id},\"ok\":false,\"error\":{}}}",
                quoted(&format!("connect: {error}")));
            return;
        }
    };
    let _ = kth.set_read_timeout(Some(Duration::from_secs(10)));
    let engine = match handshake(&mut kth, authority) {
        Ok(engine) => engine,
        Err(error) => {
            println!("{{\"event\":\"handshake\",\"connection\":{id},\"ok\":false,\"error\":{}}}",
                quoted(&error));
            let _ = client.shutdown(Shutdown::Both);
            return;
        }
    };
    println!("{{\"event\":\"handshake\",\"connection\":{id},\"ok\":true}}");
    let _ = kth.set_read_timeout(None);

    let (mut encryptor, mut decryptor) = engine.into_split();
    let (mut client_in, mut kth_out) = match (client.try_clone(), kth.try_clone()) {
        (Ok(a), Ok(b)) => (a, b),
        _ => return,
    };
    let upstream = thread::spawn(move || {
        let mut frames = 0;
        let end = to_kth(&mut client_in, &mut kth_out, &mut encryptor, &mut frames);
        let _ = kth_out.shutdown(Shutdown::Write);
        (frames, end)
    });
    let (mut frames, mut largest) = (0, 0);
    let end = from_kth(&mut kth, &mut client, &mut decryptor, &mut frames, &mut largest);
    let _ = client.shutdown(Shutdown::Both);
    let _ = kth.shutdown(Shutdown::Both);
    let (sent, sent_end) = upstream.join().unwrap_or((0, Ok(())));
    println!("{{\"event\":\"closed\",\"connection\":{id},\"frames_to_kth\":{sent},\
\"frames_from_kth\":{frames},\"largest_payload_from_kth\":{largest},\
\"kth_side_end\":{},\"client_side_end\":{}}}",
        quoted(&end.err().map(|e| e.to_string()).unwrap_or_default()),
        quoted(&sent_end.err().map(|e| e.to_string()).unwrap_or_default()));
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    let option = |name: &str| {
        args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
    };
    if let Some(path) = option("--public-key-of") {
        let Some(secret) = std::fs::read(&path).ok()
            .and_then(|bytes| secp256k1::SecretKey::from_slice(&bytes).ok()) else {
            eprintln!("{path} is not a raw 32-byte secret key");
            return ExitCode::FAILURE;
        };
        println!("{}", Secp256k1PublicKey::from(Secp256k1SecretKey(secret)));
        return ExitCode::SUCCESS;
    }
    let (Some(target), Some(authority)) = (option("--connect"), option("--authority")) else {
        eprintln!("usage: sv2-noise-bridge (--listen ADDR | --probe) --connect ADDR --authority KEY");
        return ExitCode::from(2);
    };

    if args.iter().any(|a| a == "--probe") {
        let outcome = TcpStream::connect(&target).map_err(|e| format!("connect: {e}"))
            .and_then(|mut kth| {
                let _ = kth.set_read_timeout(Some(Duration::from_secs(10)));
                handshake(&mut kth, &authority).map(|_| ())
            });
        return match outcome {
            Ok(()) => {
                println!("{{\"event\":\"probe\",\"ok\":true}}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                println!("{{\"event\":\"probe\",\"ok\":false,\"error\":{}}}", quoted(&error));
                ExitCode::FAILURE
            }
        };
    }

    let Some(listen) = option("--listen") else {
        eprintln!("--listen or --probe is required");
        return ExitCode::from(2);
    };
    let listener = match TcpListener::bind(&listen) {
        Ok(listener) => listener,
        Err(error) => {
            eprintln!("bind {listen}: {error}");
            return ExitCode::FAILURE;
        }
    };
    println!("{{\"event\":\"listening\",\"address\":{}}}", quoted(&listen));
    for (id, stream) in (1u64..).zip(listener.incoming()) {
        let Ok(client) = stream else { continue };
        let (target, authority) = (target.clone(), authority.clone());
        thread::spawn(move || serve(id, client, &target, &authority));
    }
    ExitCode::SUCCESS
}
