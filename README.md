# xbt-infra

Docker environment to develop and test software for Bitcoin BLAKE2b (XBT), the Bitcoin Knots hard fork with BLAKE2b proof of work and version 2 block headers.

It runs an XBT regtest network with a Knots node, an Electrum server and Lightning nodes, so that libraries and Lightning implementations can be tested against a live chain on a developer machine and in CI.

## What it runs

| Service | Software | Role |
|---|---|---|
| `knots` | [Bitcoin Knots](https://github.com/bitcoinknots/bitcoin) 29.4.3 | The XBT node, in regtest. BLAKE2b and the RDTS (BIP 110) rules activate at height 150. |
| `fulcrum` | [Fulcrum](https://github.com/privkeyio/Fulcrum) 2.1.4 with BLAKE2b | Electrum server for wallets and Lightning clients. |
| `cln` | [Core Lightning fork](https://github.com/privkeyio/lightning) 26.06.9-blake2b.10 | Lightning node, with `--interop`. |
| `lnd` | [Lightning Fork](https://github.com/paulscode/lightning-fork) 0.21.6-beta-blake2b.23 | Lightning node, with `--interop`. |

The chain starts with 200 blocks: 80-byte headers up to height 149, and 164-byte version 2 headers from height 150. Clients therefore see both formats, as they do when they sync mainnet.

The Knots image is built from the official release binaries, checked against their sha256. The other images are pulled from their publishers, pinned by digest.

## Requirements

- Docker with Compose v2: Docker Desktop on macOS or Docker Engine on Linux, on amd64 or arm64.
- Bash, `jq` and Python 3.10 or later, to run `bin/xbt` and the checks.

## Quick start

```bash
bin/xbt up
```

This builds the Knots image, starts Knots, mines 200 blocks, then starts Fulcrum. It takes about a minute the first time and a few seconds afterwards. Check that the chain crossed the fork and that Fulcrum serves it:

```bash
checks/activation.sh
```

```bash
python3 checks/electrum_headers.py
```

Stop the environment and delete its data:

```bash
bin/xbt down
```

## Commands

| Command | What it does |
|---|---|
| `bin/xbt up [--interop]` | Builds and starts the environment, then mines the initial blocks. `--interop` also starts the Lightning nodes. Running it again keeps the existing chain. |
| `bin/xbt down` | Stops the environment and deletes its data. |
| `bin/xbt status` | Shows the containers, the height and the state of the BLAKE2b and RDTS deployments. |
| `bin/xbt cli <args>` | Runs `bitcoin-cli` on the Knots node, for example `bin/xbt cli getblockchaininfo`. |
| `bin/xbt mine <blocks> [address]` | Mines blocks, to the `miner` wallet unless an address is given. |
| `bin/xbt fund <address> <amount>` | Sends an amount in BTC from the `miner` wallet and mines a block to confirm it. |
| `bin/xbt cln <args>` | Runs `lightning-cli` on the Core Lightning node. |
| `bin/xbt lnd <args>` | Runs `lncli` on the LND node. |
| `bin/xbt logs [service]` | Shows the logs of the containers. |

## Ports and credentials

Every port is published on `127.0.0.1` only. The values match the regtest setup of ACINQ's test suites, so their integration tests can connect without changes.

| Service | Port | Use |
|---|---|---|
| Knots | 18443 | RPC, user `foo`, password `bar` |
| Knots | 18444 | P2P |
| Knots | 29000 / 29001 / 29002 | ZMQ `rawblock` / `rawtx` / `hashblock` |
| Fulcrum | 51001 / 51002 | Electrum over TCP / TLS, with a self-signed certificate |
| Core Lightning | 19735 | P2P |
| LND | 19736 | P2P |

The RPC credentials are only safe because the node runs regtest and listens on `127.0.0.1`. Never use them on another network.

## Settings

`.env` holds the versions, image digests, credentials and chain parameters, and `compose.yaml` and `bin/xbt` read them from there. To change one locally, set it in `.env.local`, which is not committed. For example, to activate BLAKE2b at height 500 and start with 600 blocks:

```
XBT_BLAKE2B_HEIGHT=500
XBT_INITIAL_BLOCKS=600
```

Run `bin/xbt down` before changing the chain parameters: they only apply to a new chain.

## Lightning interoperability

`bin/xbt up --interop` also starts a Core Lightning node and an LND node, the two Lightning implementations that already run channels on XBT. Then:

```bash
python3 checks/interop.py
```

It funds both nodes, connects them, opens a channel from Core Lightning to LND, pays an invoice in each direction and closes the channel cooperatively. Along the way it checks the rules of the [XBT Lightning specification](https://github.com/lightning-blake2b/bolts):

- both nodes advertise `option_blake2b` (feature bit 512) and `option_unified_sigs` (514/515);
- the channel type is `static_remotekey`, `anchors` and `unified_sigs` (bits 12, 22 and 514);
- both signatures of the closing transaction use `SIGHASH_ALL_UNIFIED` (`0x21`).

It also reports whether each implementation's invoices carry `option_blake2b`.

These two nodes are the reference that other implementations are tested against. Run the check whenever one of their versions changes.

## Testnet4

`compose.testnet4.yaml` runs Knots and Fulcrum on testnet4, which forked to BLAKE2b at height 150,308. It is separate from the regtest environment: `bin/xbt` does not manage it, and the two can run at the same time.

```bash
docker compose -f compose.testnet4.yaml up --detach
```

The first run syncs the chain, which takes hours. Fulcrum then indexes it, and reports itself healthy once it serves Electrum on `127.0.0.1:52001` (TCP) and `127.0.0.1:52002` (TLS). Knots' RPC is on `127.0.0.1:48332`, with the cookie in its datadir:

```bash
docker compose -f compose.testnet4.yaml exec knots bitcoin-cli -conf=/etc/bitcoin/bitcoin.conf -datadir=/data getblockchaininfo
```

The data persists in Docker volumes. To delete it:

```bash
docker compose -f compose.testnet4.yaml down --volumes
```

## Updating the versions

Every version is pinned in `.env`.

- **Knots**:
  1. Set `KNOTS_VERSION` to the new release.
  2. Download its `SHA256SUMS` and `SHA256SUMS.asc` from the release, and verify the signatures against the builder keys in [guix.sigs](https://github.com/bitcoinknots/guix.sigs).
  3. Copy the sha256 of `bitcoin-<version>-x86_64-linux-gnu.tar.gz` to `KNOTS_SHA256_AMD64`, and of `bitcoin-<version>-aarch64-linux-gnu.tar.gz` to `KNOTS_SHA256_ARM64`.

  The build fails if a tarball does not match.
- **Fulcrum, Core Lightning and LND**: set the image's new tag, and its digest from `docker buildx imagetools inspect <image>:<tag>`.

Then run `bin/xbt down`, `bin/xbt up --interop` and every check.

## License

xbt-infra is released under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
