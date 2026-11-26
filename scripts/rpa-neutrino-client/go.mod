module knuth-rpa-neutrino-client

go 1.26.0

require (
	github.com/gcash/bchd v0.22.5-0.20261105020735-bb13df8168a3
	github.com/gcash/bchwallet/walletdb v1.0.0
	github.com/gcash/neutrino v0.0.0-20261109031510-ebe693fb45bd
)

require (
	github.com/aead/siphash v1.0.1 // indirect
	github.com/btcsuite/go-socks v0.0.0-20170105172521-4720035b7bfd // indirect
	github.com/btcsuite/golangcrypto v0.0.0-20150304025918-53f62d9b43e8 // indirect
	github.com/davecgh/go-spew v1.1.1 // indirect
	github.com/dchest/siphash v1.2.3 // indirect
	github.com/decred/dcrd/dcrec/secp256k1/v4 v4.4.1 // indirect
	github.com/gcash/bchlog v0.0.0-20261027020323-7b5704c0a60f // indirect
	github.com/gcash/bchutil v0.0.0-20261028060706-e320ade87427 // indirect
	github.com/gcash/bchwallet v0.13.2-0.20261028013728-d29e5627b006 // indirect
	github.com/gorilla/websocket v1.5.3 // indirect
	github.com/kkdai/bstream v1.0.0 // indirect
	github.com/zquestz/grab v0.0.0-20190224022517-abcee96e61b1 // indirect
	go.etcd.io/bbolt v1.5.0 // indirect
	golang.org/x/crypto v0.58.0 // indirect
	golang.org/x/net v0.62.0 // indirect
	golang.org/x/sys v0.49.0 // indirect
	golang.org/x/text v0.43.0 // indirect
	lukechampine.com/uint128 v1.3.0 // indirect
)

// gcash/neutrino#56 at its head, and the gcash/bchd and gcash/bchutil
// commits it names in its own go.mod, whose replacements do not reach here.
replace github.com/gcash/neutrino => github.com/CyberAshven/neutrino v0.0.0-20261126072534-9dcb407fc97c

replace github.com/gcash/bchd => github.com/CyberAshven/bchd v0.22.1-0.20261116202941-60816369d828

replace github.com/gcash/bchutil => github.com/CyberAshven/bchutil v0.0.0-20261126051837-bcad7d10b2c3
