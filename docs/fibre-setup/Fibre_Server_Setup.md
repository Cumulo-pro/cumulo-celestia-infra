# Celestia Fibre Server Setup — mocha-5 (velia6)

## Overview

[Fibre](https://github.com/celestiaorg/celestia-app/blob/main/fibre/cmd/README.md) is Celestia's validator-operated blob-publishing protocol introduced in the v10 network upgrade (CIP-51 / CIP-52). It runs as a standalone server that sits alongside a validator: clients upload and download blob data through it, while the validator's consensus key co-signs payment promises for uploads. Clients discover Fibre servers on-chain through the `x/valaddr` registry, and a validator's storage/bandwidth budget is derived from its bonded stake.

This document records how Cumulo set up Fibre for its **mocha-5** validator, running on **velia6** (`173.201.36.182`), ahead of and through the Mocha v10 activation (block `1082619`, 24 Sept 2026).

Official references:
- Fibre server README: https://github.com/celestiaorg/celestia-app/blob/v10.2.0-mocha/fibre/cmd/README.md
- Setup guide: https://docs.celestia.org/operate/consensus-validators/fibre/
- Metrics guide: https://docs.celestia.org/operate/consensus-validators/fibre/metrics/
- CPU benchmark tool: https://github.com/celestiaorg/celestia-app/blob/main/tools/cpu_requirements/README.md

## Architecture

Three processes are involved:

| Process | Role | Default address |
|---|---|---|
| `celestia-appd` | consensus + settlement app | app gRPC `127.0.0.1:9090` |
| `fibre` | shards, pruning, client-facing TLS server | `0.0.0.0:7980` |
| `celestia-appd` (privval) | signer gRPC — Fibre delegates payment-promise signing here | `127.0.0.1:26669` (default; see below for the port actually used) |

Fibre never talks to a KMS directly — it always signs through the validator node's own privval gRPC endpoint, whatever key backend that node uses (local file, tmkms, etc.).

**Important:** the app↔fibre link (`--app-grpc-address`) and the signer link (`--signer-grpc-address`) are **not TLS-protected**. They must stay on loopback or a trusted private network. Only the Fibre client port (`7980` by default) is meant to be publicly reachable — its transport is TLS 1.3, automatic and certificate-free (the server mints its own cert at each startup, endorsed by the validator's consensus key).

## Why co-located on velia6

Celestia's own guidance (confirmed by the Celestia team in the validators' Discord, 04/09/2026) is that for this first phase of the Fibre rollout, the Fibre server should run on the **same host** as the validator, specifically because the signer link has no TLS yet — running it remotely would mean exposing an unauthenticated path to the consensus key over the network.

velia6 is Cumulo's actual bonded mocha-5 validator (`BOND_STATUS_BONDED`, local `priv_validator_key.json`, no remote signer), so it was the natural (and, per the above, the only currently-recommended) choice — as opposed to e.g. a Bridge/RPC/Snapshot host, which doesn't hold the consensus key at all.

**Host specs (velia6):**

- AMD Ryzen 9 7950X3D — 16 cores / 32 threads @ 4.20GHz (5.70GHz turbo)
- 128GB DDR5 ECC RAM
- 2× 960GB NVMe (no RAID)

## Prerequisites checklist

| Requirement | Status on velia6 |
|---|---|
| CPU passes `tools/cpu_requirements` (128MB/6s upgrade) | ✅ — see benchmark below |
| 32GB RAM minimum | ✅ — 128GB available |
| Validator bonded (storage budget derives from stake) | ✅ |
| Disk/bandwidth sized to stake | ✅ — see sizing below |
| App gRPC enabled (`127.0.0.1:9090`) | ✅ |
| Privval gRPC enabled | ✅ |
| Fibre listen port (`7980`) reachable | ✅ |
| Separate disks for Fibre data and celestia-app data | ✅ — see disk layout below |

### CPU benchmark

The `128MB/6s` upgrade that Fibre requires is gated on execution time, not raw core count. The tool's hardware fallback recommendation (32+ cores, GFNI, SHA-NI) only applies if the benchmark itself comes out slower than reference — it's not a fixed floor.

velia6 has only 16 physical cores (32 via SMT), below that fallback recommendation, but the actual benchmark (`go run ./tools/cpu_requirements`, run from a `v10.2.0-mocha` checkout) passed comfortably:

```
=== CPU Specifications ===
Number of Cores:   16
Number of Threads: 32
GFNI Support:      true
SHA-NI Support:    true
```

| Operation | velia6 | Reference | Result |
|---|---|---|---|
| Prepare Proposal | 244 ms | 700 ms | 2.9x faster |
| Process Proposal | 510 ms | 700 ms | 1.4x faster |
| Finalize Block | 115 ms | 400 ms | 3.5x faster |
| Propose Block | 243 ms | 400 ms | 1.6x faster |
| Encode Block | 133 ms | 400 ms | 3.0x faster |
| Decode Block | 235 ms | 500 ms | 2.1x faster |

Verdict: *"CONGRATULATIONS! Your system is ready for the 128MB/6s upgrade."* — faster than reference across all six categories, confirming that per-core performance (plus GFNI/SHA-NI) matters more than core count for this specific gate.

### Disk sizing

Celestia's official calculator (78 active mocha-5 validators, snapshot 14 Sept 2026) gives Cumulo's real numbers, replacing an earlier rough estimate from a Discord-posted table:

| | Cumulo |
|---|---|
| Staking power | 1.34% |
| Launch bandwidth | 0.059 Gbps |
| Launch storage | 88.6 GB |

## Disk layout

velia6 has two physical NVMe disks, no RAID:

```
/dev/nvme1n1p4  862G   →  /              (root — celestia-appd home, $HOME/.celestia-app-mocha-5)
/dev/nvme0n1p1  880G   →  /home/node     (second physical disk — dedicated to Fibre)
```

`/home/node` previously hosted an unrelated **Dymension Mainnet Signer1** validator. It was migrated off to another host in Cumulo's fleet to isolate Fibre's I/O from anything non-Celestia-related, and the node data (down to a kept operator wallet) was cleaned up afterwards, leaving the disk effectively free.

One important correction made during this audit: a 159GB `avail` directory under `/home/node` was initially assumed to be a stale backup, but turned out — confirmed via matching device/inode between `/home/node/avail` and the symlink `/home/cumvelia6/node/avail` — to be the **live, in-production Avail mainnet RPC node** data directory. It was left untouched. It holds no validator keys (confirmed with a recursive search for keyring/priv_validator files), so it remains a low-risk candidate for future I/O isolation, but at 676GB free even with it in place — against Fibre's actual ~88.6GB need — there was no pressing need to move it.

Fibre's home was set up as its own subdirectory rather than the mount root, to avoid mixing with `lost+found` and other unrelated data:

```
/home/node/celestia-fibre
```

## Installation

```bash
fibre_version=v10.2.0-mocha
curl -fLO "https://github.com/celestiaorg/celestia-app/releases/download/$fibre_version/fibre_Linux_x86_64.tar.gz"
curl -fLO "https://github.com/celestiaorg/celestia-app/releases/download/$fibre_version/checksums.txt"
sha256sum --ignore-missing --check checksums.txt
tar -xzf fibre_Linux_x86_64.tar.gz
sudo mv fibre /usr/local/bin/fibre
fibre version
```

`v10.2.0-mocha` was pinned to match the version referenced by the current docs.celestia.org guide (the activation announcement itself links an older `v10.1.0-corto` tag).

## celestia-appd configuration

celestia-appd runs as a systemd service (`celestia-appd.service`) using celestia-app's built-in multiplexer pattern — the top-level process (already `v10.2.0-mocha`) dispatches to a pinned binary per historical app version (`bin/v9.0.7-corto/`, etc.) until app version 10 activates on-chain, at which point it runs the latest logic natively (no separate `bin/v10.x.x/` folder is needed for that).

Two gRPC endpoints needed to be confirmed/enabled on the validator, home `~/.celestia-app-mocha-5`:

**`config/app.toml`** — app gRPC (was disabled):
```toml
[grpc]
enable = true
address = "127.0.0.1:9090"
```

**`config/config.toml`** — privval gRPC. On velia6 this was **already configured**, just on a non-default port:
```toml
priv_validator_grpc_laddr = "127.0.0.1:26659"
```
(the celestia-app default from `celestia-appd init` is `26669` — confirm your own node's actual port with `grep priv_validator_grpc_laddr config.toml` rather than assuming the default.)

After editing `app.toml`, `celestia-appd` needs a restart to pick up the gRPC change:
```bash
sudo systemctl restart celestia-appd
```
A restart briefly interrupts signing but does not itself risk a slashing event — for a bonded validator this is a normal, low-risk operation. Verify both endpoints come back up:
```bash
ss -tlnp | grep -E ':(9090|26659)'
```

## Fibre systemd unit

`/etc/systemd/system/fibre.service`:

```ini
[Unit]
Description=Celestia Fibre server (mocha-5)
Wants=network-online.target
After=network-online.target celestia-appd.service
Requires=celestia-appd.service

[Service]
User=cumvelia6
ExecStart=/usr/local/bin/fibre start \
  --home /home/node/celestia-fibre \
  --app-grpc-address 127.0.0.1:9090 \
  --signer-grpc-address 127.0.0.1:26659 \
  --server-listen-address 0.0.0.0:7980 \
  --max-connections 32 \
  --otel-endpoint http://127.0.0.1:4318
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Notes on the non-default flags:

- `--signer-grpc-address 127.0.0.1:26659` — matches velia6's actual (non-default) privval port, see above.
- `--max-connections 32` (default is `16`). The Fibre README notes that a single upload uses 16 signers and therefore occupies all 16 connection slots by default, **blocking concurrent downloads** for its duration. Worst-case memory is `max_connections × max_concurrent_streams × 132 MiB`; at `32 × 13` that's ~53.5GiB, comfortably inside velia6's 128GB.
- `--otel-endpoint http://127.0.0.1:4318` — see [Monitoring](#monitoring) below.

```bash
sudo systemctl daemon-reload
sudo systemctl enable fibre       # enabled, not started
sudo systemd-analyze verify fibre.service
```

Fibre is left `enabled` but **not started** until the chain has actually activated v10 — starting it earlier against a v9 network serves no purpose and isn't supported.

> Editing tip: when adding a flag to an existing multi-line `ExecStart=... \` block, a one-off `sed` insert can easily land the new line outside the continuation if the preceding line didn't already end in `\`. Safer to rewrite the whole unit via a heredoc and check it with `systemd-analyze verify <unit>` before moving on.

## Firewall

Only `7980/tcp` needs to be opened publicly for Fibre; the app/signer gRPC ports stay on loopback and are never touched in the firewall.

```bash
sudo ufw allow 7980/tcp comment 'Celestia Fibre client port'
```

While reviewing the firewall for this, four allowed ports on velia6 (`26646`, `26660`, `26626`, `1235`) turned out to have no process listening behind them at all — checked against `ss -tlnp`/`ss -ulnp` and cross-referenced with everything actually running on the host (celestia-appd, an Avail mainnet RPC node, an XRPLEVM node, node_exporter, sshd). They were most likely leftovers from the now-migrated Dymension validator and were removed:

```bash
sudo ufw delete allow 26646
sudo ufw delete allow 26660
sudo ufw delete allow 26626
sudo ufw delete allow 1235
```

## Monitoring

Fibre exports metrics and traces via OTLP/HTTP — there's no native Prometheus scrape endpoint on Fibre itself. Cumulo's existing stack (Prometheus + Grafana, centralized on a separate host, scraped by public IP like `node_exporter`) is pull-based, so a small **OpenTelemetry Collector** bridges the two: it receives OTLP from Fibre on loopback and re-exposes a Prometheus-scrapeable endpoint.

**Collector config** (`/etc/otelcol-contrib/config.yaml`):
```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318

exporters:
  prometheus:
    endpoint: "0.0.0.0:9464"
    namespace: fibre
  debug:
    verbosity: basic

service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
    traces:
      receivers: [otlp]
      exporters: [debug]   # no tracing backend yet — traces are logged locally only
```

**systemd unit** (`/etc/systemd/system/otelcol-contrib.service`):
```ini
[Unit]
Description=OpenTelemetry Collector (Fibre metrics bridge)
After=network-online.target
Wants=network-online.target

[Service]
User=cumvelia6
ExecStart=/usr/local/bin/otelcol-contrib --config /etc/otelcol-contrib/config.yaml
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now otelcol-contrib
sudo ufw allow 9464/tcp comment 'OTel Collector - Prometheus exporter (Fibre metrics)'
```

Verified end-to-end with:
```bash
curl -sI http://127.0.0.1:9464/metrics   # HTTP/1.1 200 OK
```

**Prometheus scrape config** (added on the central Prometheus host):
```yaml
- job_name: 'fibre-velia6'
  static_configs:
    - targets: ['173.201.36.182:9464']
      labels:
        instance: 'velia6'
        chain: 'mocha-5'
```

**Grafana dashboard:** the pre-built dashboard shipped with the release is at [`observability/docker/grafana/dashboards/fibre.json`](https://github.com/celestiaorg/celestia-app/blob/v10.2.0-mocha/observability/docker/grafana/dashboards/fibre.json) (not at the path implied by the metrics doc's prose — the real path had to be tracked down directly from the page's link). Import it in Grafana via **Dashboards → New → Import → Upload dashboard JSON file** (the "paste a URL" box on that screen only accepts a grafana.com dashboard ID, not an arbitrary JSON URL), then map it to the Prometheus datasource.

## Registration (post-activation)

Once Mocha has crossed the v10 activation height and the validator is confirmed on app version 10:

```bash
sudo systemctl start fibre
sudo journalctl -u fibre -f          # confirm it connects to the app/signer and starts listening

celestia-appd tx valaddr set-host 173.201.36.182:7980 \
  --from <validator-account-key> --chain-id mocha-5

celestia-appd query valaddr providers
```

Start the server **before** registering — a registered-but-unreachable host causes clients to dial it and time out.

## Systemd quick reference

```bash
sudo systemctl start|stop|restart|status fibre
sudo systemctl enable|disable fibre
sudo journalctl -u fibre -f
sudo journalctl -u fibre -n 50 --no-pager
sudo systemctl daemon-reload            # after editing the unit file
sudo systemd-analyze verify fibre.service
```

## Troubleshooting reference (from the Fibre README)

| Symptom | Likely cause |
|---|---|
| `starting server: creating signer: ...` | Can't reach privval gRPC — check the node is up and `--signer-grpc-address` points at the right `priv_validator_grpc_laddr` |
| `derived storage budget is 0` | Validator not bonded, or `--app-grpc-address` points at a syncing/wrong node |
| Server runs, no uploads arrive | Check `query valaddr provider <consensus-addr>`, confirm the registered host:port actually routes to `--server-listen-address` through the firewall, confirm bonded status |
| TLS identity verification failures on clients | `--signer-grpc-address` points at a node holding a different consensus key than the one registered — e.g. a sentry |
| `payment promise verification failed` | Chain-ID mismatch on `--app-grpc-address`, or an issue on the submitter's side (escrow funding, stale promise height) |

## Status

As of 24 Sept 2026: all pre-activation prerequisites are complete on velia6 (binary installed, config staged, systemd units in place but not started, firewall opened, monitoring pipeline verified end-to-end). Fibre itself has not been started yet — that, along with `set-host` registration, happens once Mocha's v10 activation height is confirmed crossed.

Open items:
- Revisit whether to move the Avail RPC node off `/home/node` for I/O isolation (not urgent — disk headroom is ample either way).
- No RAID on velia6 — a disk failure would take Fibre's shard store with it (not consensus state, recoverable, but affects service continuity). Worth a decision on whether to mitigate.
- Repeat this setup for Cumulo's **mainnet** validator (Sentinel 1) once Fibre is required there.
