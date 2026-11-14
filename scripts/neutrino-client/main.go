// A light wallet's node connection, made with the Neutrino library itself, to
// check that a node serves what such a wallet needs. It connects to one peer
// only, syncs block headers and filter headers to a height, fetches the
// filter of one block and tests it for two scripts, then fetches the block.
// It prints one JSON object and exits 0 when every step succeeded.
package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/gcash/bchd/chaincfg"
	"github.com/gcash/bchd/chaincfg/chainhash"
	"github.com/gcash/bchd/wire"
	"github.com/gcash/bchutil/gcs/builder"
	"github.com/gcash/bchwallet/walletdb"
	_ "github.com/gcash/bchwallet/walletdb/bdb"
	"github.com/gcash/neutrino"
)

type result struct {
	Peer            string `json:"peer"`
	BlockHeaders    int32  `json:"block_headers"`
	FilterHeaders   uint32 `json:"filter_headers"`
	Synced          bool   `json:"synced"`
	FilterItems     uint32 `json:"filter_items"`
	PaidScriptFound bool   `json:"paid_script_found"`
	OtherScriptHit  bool   `json:"other_script_found"`
	BlockTxs        int    `json:"block_transactions"`
	Error           string `json:"error,omitempty"`
}

func finish(r *result, err error) {
	if err != nil {
		r.Error = err.Error()
	}
	out, _ := json.Marshal(r)
	fmt.Println(string(out))
	if err != nil || !r.Synced {
		os.Exit(1)
	}
	os.Exit(0)
}

func main() {
	connect := flag.String("connect", "127.0.0.1:18555", "the one peer to use")
	height := flag.Int("height", 0, "the height both header chains must reach")
	blockHex := flag.String("block", "", "a block whose filter and body are fetched")
	paidHex := flag.String("script", "", "a script paid in that block, hex")
	otherHex := flag.String("absent", "", "a script that is not in that block, hex")
	timeout := flag.Duration("timeout", 5*time.Minute, "how long the sync may take")
	flag.Parse()

	r := &result{Peer: *connect}
	dir, err := os.MkdirTemp("", "neutrino-kth")
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
		ConnectPeers: []string{*connect},
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
		if _, tip, err := service.RegFilterHeaders.ChainTip(); err == nil {
			r.FilterHeaders = tip
		}
		if int(r.BlockHeaders) >= *height && int(r.FilterHeaders) >= *height {
			r.Synced = true
			break
		}
		if time.Now().After(deadline) {
			finish(r, fmt.Errorf("not synced to %d in %s", *height, *timeout))
		}
		time.Sleep(500 * time.Millisecond)
	}

	hash, err := chainhash.NewHashFromStr(*blockHex)
	if err != nil {
		finish(r, err)
	}
	// The library checks the filter it is given against the filter header it synced.
	filter, err := service.GetCFilter(*hash, wire.GCSFilterRegular)
	if err != nil {
		finish(r, err)
	}
	if filter == nil {
		finish(r, fmt.Errorf("no filter for %s", hash))
	}
	r.FilterItems = filter.N()
	key := builder.DeriveKey(hash)
	paid, err := hex.DecodeString(*paidHex)
	if err != nil {
		finish(r, err)
	}
	if r.PaidScriptFound, err = filter.Match(key, paid); err != nil {
		finish(r, err)
	}
	other, err := hex.DecodeString(*otherHex)
	if err != nil {
		finish(r, err)
	}
	if r.OtherScriptHit, err = filter.Match(key, other); err != nil {
		finish(r, err)
	}
	// And the block the filter pointed at, as a wallet would fetch it next.
	block, err := service.GetBlock(*hash)
	if err != nil {
		finish(r, err)
	}
	r.BlockTxs = len(block.Transactions())
	if !r.PaidScriptFound || r.OtherScriptHit {
		finish(r, fmt.Errorf("the filter does not hold what the block pays"))
	}
	finish(r, nil)
}
