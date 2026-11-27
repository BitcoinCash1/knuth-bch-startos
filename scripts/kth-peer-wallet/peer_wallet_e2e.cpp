// TEST ONLY, never part of kth. Built inside a kth tree by the harness
// (kth-rows.yml appends it to src/node/CMakeLists.txt): kth's wallet support
// answered from peers (wallet::service::from_peers), driven once, its answers
// printed as one JSON line for scripts/knuth_rpc_e2e.py to compare.
//
//   peer_wallet_e2e --directory DIR --peer HOST:PORT [--peer ...]
//       [--input-prefix-filters] [--birthday N] [--sent-log FILE]
//       --tip-hash HASH [--timeout SECONDS]
//       [--script HEX]... [--script-hash HEX]... [--category HEX]...
//       [--rpa PREFIX/BITS/FROM/TO]... [--tx TXID[@HEIGHT]]... [--outpoint TXID:VOUT]...
//
// It waits until its headers end at HASH and its filters reach them, then
// asks every question and prints the answers.

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <future>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#include <asio/co_spawn.hpp>
#include <asio/use_future.hpp>

#include <kth/infrastructure/formats/base_16.hpp>
#include <kth/node/wallet/service.hpp>

using namespace kth;
namespace chain = kth::domain::chain;
namespace wallet = kth::node::wallet;

namespace {

std::string quoted(std::string const& text) {
    std::string out = "\"";
    for (auto const c : text) {
        if (c == '"' || c == '\\') out += '\\';
        if (static_cast<unsigned char>(c) < 0x20) {
            char buffer[8];
            std::snprintf(buffer, sizeof(buffer), "\\u%04x", unsigned(c));
            out += buffer;
            continue;
        }
        out += c;
    }
    return out + "\"";
}

template <typename Awaitable>
auto ask(wallet::service& service, Awaitable&& operation) {
    auto future = ::asio::co_spawn(service.executor(), std::forward<Awaitable>(operation), ::asio::use_future);
    if (future.wait_for(std::chrono::minutes(20)) != std::future_status::ready) {
        std::cerr << "a request did not finish in 20 minutes\n";
        std::exit(3);
    }
    return future.get();
}

std::string error_of(std::error_code const& ec) {
    return "{\"error\":" + quoted(ec.message()) + ",\"code\":" + std::to_string(ec.value()) + "}";
}

std::optional<hash_digest> hash_of(std::string const& text) {
    auto const decoded = decode_hash(text);
    if ( ! decoded) return std::nullopt;
    return *decoded;
}

std::string status_json(wallet::status const& now) {
    std::ostringstream out;
    auto const height = [](std::optional<uint32_t> value) {
        return value ? std::to_string(*value) : std::string("null");
    };
    out << "{\"source\":" << quoted(now.source) << ",\"header_height\":" << now.header_height
        << ",\"tip\":" << height(now.tip) << ",\"input_prefix_tip\":" << height(now.input_prefix_tip)
        << ",\"peers\":" << now.peers << ",\"synced\":" << (now.synced ? "true" : "false")
        << ",\"bytes_received\":" << now.bytes_received << ",\"bytes_sent\":" << now.bytes_sent
        << ",\"blocks_downloaded\":" << now.blocks_downloaded << ",\"filter_bytes\":" << now.filter_bytes
        << ",\"error\":" << quoted(now.error) << ",\"refused\":[";
    for (size_t i = 0; i < now.refused.size(); ++i) out << (i ? "," : "") << quoted(now.refused[i]);
    out << "]}";
    return out.str();
}

} // namespace

int main(int argc, char** argv) {
    wallet::peer_settings settings;
    settings.network = domain::config::network::regtest;
    settings.request_timeout = std::chrono::seconds(30);
    std::string tip_hash;
    int timeout = 600;
    std::vector<std::string> scripts, script_hashes, categories, rpas, txs, outpoints;
    for (int i = 1; i < argc; ++i) {
        std::string const arg = argv[i];
        auto const next = [&]() -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "missing value after " << arg << "\n";
                std::exit(2);
            }
            return argv[++i];
        };
        if (arg == "--directory") settings.directory = next();
        else if (arg == "--peer") settings.peers.push_back(next());
        else if (arg == "--input-prefix-filters") settings.input_prefix_filters = true;
        else if (arg == "--birthday") settings.birthday = uint32_t(std::stoul(next()));
        else if (arg == "--sent-log") settings.sent_log = next();
        else if (arg == "--tip-hash") tip_hash = next();
        else if (arg == "--timeout") timeout = std::stoi(next());
        else if (arg == "--script") scripts.push_back(next());
        else if (arg == "--script-hash") script_hashes.push_back(next());
        else if (arg == "--category") categories.push_back(next());
        else if (arg == "--rpa") rpas.push_back(next());
        else if (arg == "--tx") txs.push_back(next());
        else if (arg == "--outpoint") outpoints.push_back(next());
        else {
            std::cerr << "unknown argument " << arg << "\n";
            return 2;
        }
    }
    auto const wanted = hash_of(tip_hash);
    if ( ! wanted) {
        std::cerr << "--tip-hash is not a hash\n";
        return 2;
    }

    auto made = wallet::service::from_peers(settings);
    if ( ! made) {
        std::cout << "{\"made\":" << error_of(made.error()) << "}\n";
        return 1;
    }
    auto& service = **made;
    auto running = ::asio::co_spawn(service.executor(), service.run(), ::asio::use_future);
    auto const started = std::chrono::steady_clock::now();

    // Until the headers end at the wanted block and the filters reach it.
    bool reached = false;
    uint64_t seen = 0;
    while (std::chrono::steady_clock::now() - started < std::chrono::seconds(timeout)) {
        auto const now = service.current();
        if (now.synced && now.tip == now.header_height) {
            auto const header = ask(service, service.header(now.header_height));
            if (header && chain::hash(*header) == *wanted) {
                reached = true;
                break;
            }
        }
        seen = ask(service, service.changed(seen, std::chrono::seconds(5)));
    }
    auto const sync_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();

    std::ostringstream out;
    out << "{\"reached\":" << (reached ? "true" : "false") << ",\"sync_seconds\":" << sync_seconds;

    auto const answers_started = std::chrono::steady_clock::now();
    out << ",\"scripts\":{";
    for (size_t i = 0; i < scripts.size(); ++i) {
        auto const bytes = decode_base16(scripts[i]);
        out << (i ? "," : "") << quoted(scripts[i]) << ":";
        if ( ! bytes) {
            out << "{\"error\":\"not hex\"}";
            continue;
        }
        auto const target = wallet::target::of_script(*bytes);
        auto const history = ask(service, service.history(target));
        if ( ! history) {
            out << error_of(history.error());
            continue;
        }
        out << "{\"synced\":" << (history->synced ? std::to_string(*history->synced) : std::string("null")) << ",\"history\":[";
        for (size_t at = 0; at < history->confirmed.size(); ++at) {
            auto const& entry = history->confirmed[at];
            out << (at ? "," : "") << "[" << quoted(encode_hash(entry.txid)) << "," << entry.height << "]";
        }
        out << "],\"unconfirmed\":[";
        for (size_t at = 0; at < history->unconfirmed.size(); ++at) {
            out << (at ? "," : "") << quoted(encode_hash(history->unconfirmed[at].txid));
        }
        out << "],\"unspent\":[";
        auto const coins = ask(service, service.unspent(target, wallet::token_filter::include));
        if (coins) {
            for (size_t at = 0; at < coins->size(); ++at) {
                auto const& coin = (*coins)[at];
                out << (at ? "," : "") << "[" << quoted(encode_hash(coin.outpoint.hash())) << "," << coin.outpoint.index()
                    << "," << coin.value << "," << (coin.height ? std::to_string(*coin.height) : std::string("null")) << "]";
            }
        }
        auto const balance = ask(service, service.get_balance(target, wallet::token_filter::include));
        out << "],\"balance\":" << (balance ? std::to_string(balance->confirmed) : std::string("null")) << "}";
    }
    out << "},\"script_hashes\":{";
    for (size_t i = 0; i < script_hashes.size(); ++i) {
        auto const hash = hash_of(script_hashes[i]);
        out << (i ? "," : "") << quoted(script_hashes[i]) << ":";
        if ( ! hash) {
            out << "{\"error\":\"not a hash\"}";
            continue;
        }
        auto const history = ask(service, service.history(wallet::target::of_script_hash(*hash)));
        out << (history ? std::string("{\"answered\":true}") : error_of(history.error()));
    }
    out << "},\"categories\":{";
    for (size_t i = 0; i < categories.size(); ++i) {
        auto const hash = hash_of(categories[i]);
        out << (i ? "," : "") << quoted(categories[i]) << ":";
        if ( ! hash) {
            out << "{\"error\":\"not a hash\"}";
            continue;
        }
        auto const coins = ask(service, service.unspent(wallet::target::of_category(*hash), wallet::token_filter::only));
        out << (coins ? std::string("{\"answered\":true}") : error_of(coins.error()));
    }
    out << "},\"rpa\":{";
    for (size_t i = 0; i < rpas.size(); ++i) {
        // PREFIX/BITS/FROM/TO, the prefix in hex.
        std::stringstream parts(rpas[i]);
        std::string prefix, bits, from, to;
        std::getline(parts, prefix, '/');
        std::getline(parts, bits, '/');
        std::getline(parts, from, '/');
        std::getline(parts, to, '/');
        out << (i ? "," : "") << quoted(rpas[i]) << ":";
        auto const matches = ask(service, service.rpa_history(uint16_t(std::stoul(prefix, nullptr, 16)),
            uint8_t(std::stoul(bits)), uint32_t(std::stoul(from)), uint32_t(std::stoul(to))));
        if ( ! matches) {
            out << error_of(matches.error());
            continue;
        }
        out << "[";
        for (size_t at = 0; at < matches->size(); ++at) {
            auto const& match = (*matches)[at];
            out << (at ? "," : "") << "[" << quoted(encode_hash(match.txid)) << ","
                << (match.height ? std::to_string(*match.height) : std::string("null")) << "]";
        }
        out << "]";
    }
    out << "},\"txs\":{";
    for (size_t i = 0; i < txs.size(); ++i) {
        auto const at = txs[i].find('@');
        auto const hash = hash_of(txs[i].substr(0, at));
        std::optional<uint32_t> hint;
        if (at != std::string::npos) hint = uint32_t(std::stoul(txs[i].substr(at + 1)));
        out << (i ? "," : "") << quoted(txs[i]) << ":";
        if ( ! hash) {
            out << "{\"error\":\"not a hash\"}";
            continue;
        }
        auto const found = ask(service, service.transaction(*hash, hint));
        if ( ! found) {
            out << error_of(found.error());
            continue;
        }
        out << "{\"height\":" << (found->confirmed ? std::to_string(found->confirmed->height) : std::string("null"))
            << ",\"txid\":" << quoted(encode_hash(found->tx.hash())) << "}";
    }
    out << "},\"outpoints\":{";
    for (size_t i = 0; i < outpoints.size(); ++i) {
        auto const colon = outpoints[i].find(':');
        auto const hash = hash_of(outpoints[i].substr(0, colon));
        out << (i ? "," : "") << quoted(outpoints[i]) << ":";
        if ( ! hash || colon == std::string::npos) {
            out << "{\"error\":\"not an outpoint\"}";
            continue;
        }
        auto const answer = ask(service, service.output(chain::point(*hash, uint32_t(std::stoul(outpoints[i].substr(colon + 1))))));
        if ( ! answer) {
            out << error_of(answer.error());
        } else if ( ! *answer) {
            out << "null";
        } else {
            out << "{\"value\":" << (*answer)->value << ",\"height\":"
                << ((*answer)->height ? std::to_string(*(*answer)->height) : std::string("null")) << "}";
        }
    }
    auto const answer_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - answers_started).count();
    out << "},\"answer_seconds\":" << answer_seconds << ",\"status\":" << status_json(service.current()) << "}";
    std::cout << out.str() << std::endl;

    service.stop();
    (void)running.wait_for(std::chrono::seconds(60));
    return reached ? 0 : 1;
}
