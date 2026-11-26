// TEST ONLY. The search of a wallet on reusable payment addresses, made with
// the Neutrino library itself (gcash/neutrino#56, SearchRPAPrefixes): it
// connects to the given peers only, syncs block headers to a height, and
// searches the chain from a start height for the inputs with each prefix. The
// library reads the peers' input prefix filters (filter type 2) in block mode,
// checks them against the filter hashes of RPAMinPeers peers, downloads the
// blocks whose filters match, rebuilds each one's filter and hands over the
// inputs with the prefix. No request names a prefix. It prints one JSON object
// and exits 0 when every search ran to the end.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/gcash/bchd/chaincfg"
	"github.com/gcash/bchwallet/walletdb"
	_ "github.com/gcash/bchwallet/walletdb/bdb"
	"github.com/gcash/neutrino"
)

type input struct {
	Tx      string `json:"tx"`
	TxIndex int    `json:"tx_index"`
	Index   int    `json:"index"`
	Value   string `json:"value"`
}

type match struct {
	Height int32   `json:"height"`
	Block  string  `json:"block"`
	Inputs []input `json:"inputs"`
	Peers  int     `json:"peers"`
}

type search struct {
	Prefix  string  `json:"prefix"`
	Reached int32   `json:"reached"`
	Matches []match `json:"matches"`
	Error   string  `json:"error,omitempty"`
}

type result struct {
	Peers        []string `json:"peers"`
	BlockHeaders int32    `json:"block_headers"`
	Synced       bool     `json:"synced"`
	Searches     []search `json:"searches"`
	Error        string   `json:"error,omitempty"`
}

func finish(r *result, err error) {
	if err != nil {
		r.Error = err.Error()
	}
	out, _ := json.Marshal(r)
	fmt.Println(string(out))
	ok := err == nil && r.Synced
	for _, s := range r.Searches {
		ok = ok && s.Error == ""
	}
	if !ok {
		os.Exit(1)
	}
	os.Exit(0)
}

// parsePrefix reads "value/bits", the value in hex holding the first bits.
func parsePrefix(text string) (neutrino.RPAPrefix, error) {
	value, bits, ok := strings.Cut(text, "/")
	if !ok {
		return neutrino.RPAPrefix{}, fmt.Errorf("prefix %q is not value/bits", text)
	}
	v, err := strconv.ParseUint(value, 16, 16)
	if err != nil {
		return neutrino.RPAPrefix{}, err
	}
	b, err := strconv.ParseUint(bits, 10, 8)
	if err != nil {
		return neutrino.RPAPrefix{}, err
	}
	return neutrino.RPAPrefix{Value: uint16(v), Bits: uint8(b)}, nil
}

func main() {
	connect := flag.String("connect", "127.0.0.1:18555", "the peers to use, comma separated")
	height := flag.Int("height", 0, "the height the block headers must reach, and the end of the searches")
	start := flag.Int("start", 1, "the first height searched")
	prefixes := flag.String("prefixes", "", "the prefixes searched, one search each: value/bits, comma separated")
	minPeers := flag.Int("min-peers", 1, "how many peers must give the filter hashes of each run")
	timeout := flag.Duration("timeout", 5*time.Minute, "how long the header sync may take")
	flag.Parse()

	r := &result{Peers: strings.Split(*connect, ",")}
	dir, err := os.MkdirTemp("", "neutrino-rpa")
	if err != nil {
		finish(r, err)
	}
	defer os.RemoveAll(dir)
	db, err := walletdb.Create("bdb", filepath.Join(dir, "wallet.db"), true)
	if err != nil {
		finish(r, err)
	}
	defer db.Close()

	service, err := neutrino.NewChainService(neutrino.Config{
		DataDir:      dir,
		Database:     db,
		ChainParams:  chaincfg.RegressionNetParams,
		ConnectPeers: r.Peers,
	})
	if err != nil {
		finish(r, err)
	}
	if err := service.Start(); err != nil {
		finish(r, err)
	}
	defer service.Stop()

	deadline := time.Now().Add(*timeout)
	for {
		if best, err := service.BestBlock(); err == nil && best != nil {
			r.BlockHeaders = best.Height
		}
		if int(r.BlockHeaders) >= *height && len(service.Peers()) >= len(r.Peers) {
			r.Synced = true
			break
		}
		if time.Now().After(deadline) {
			finish(r, fmt.Errorf("not synced to %d with %d peers in %s", *height, len(r.Peers), *timeout))
		}
		time.Sleep(500 * time.Millisecond)
	}

	for _, text := range strings.Split(*prefixes, ",") {
		s := search{Prefix: text, Matches: []match{}}
		prefix, err := parsePrefix(text)
		if err != nil {
			s.Error = err.Error()
			r.Searches = append(r.Searches, s)
			continue
		}
		reached, err := service.SearchRPAPrefixes([]neutrino.RPAPrefix{prefix}, int32(*start), int32(*height),
			func(m neutrino.RPAMatch) bool {
				found := match{Height: m.Height, Block: m.Block.Hash().String(), Peers: m.Peers}
				transactions := m.Block.MsgBlock().Transactions
				for _, in := range m.Inputs {
					found.Inputs = append(found.Inputs, input{
						Tx:      transactions[in.TxIndex].TxHash().String(),
						TxIndex: in.TxIndex,
						Index:   in.Index,
						Value:   fmt.Sprintf("%04x", in.Value),
					})
				}
				s.Matches = append(s.Matches, found)
				return true
			}, nil, nil, neutrino.RPAMinPeers(*minPeers))
		s.Reached = reached
		if err != nil {
			s.Error = err.Error()
		} else if reached != int32(*height) {
			s.Error = fmt.Sprintf("reached %d of %d", reached, *height)
		}
		r.Searches = append(r.Searches, s)
	}
	finish(r, nil)
}
