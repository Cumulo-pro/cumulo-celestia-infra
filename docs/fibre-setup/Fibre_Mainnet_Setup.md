# Celestia Fibre Server Setup (Mainnet Beta)

## Overview

This document records how Cumulo prepared Fibre for its **Mainnet Beta** (`celestia`) validator ahead of the v10 activation (block `15116658`). It builds on our [mocha-5 setup](./Fibre_Server_Setup.md), with one major difference: on mainnet the **Fibre server runs on a different host from the validator**, and the two are connected by a signing link protected with **mutual TLS (mTLS)**.

The goal of this write-up is to share the design, the checks and the lessons learned. Host names, addresses, providers, custom ports and other operational details are intentionally left out; every value that depends on your own setup appears as a `<placeholder>`.

Versions used: celestia-app and Fibre **v10.4.0** (mainnet release).

Official references:
- Release notes (mainnet): https://github.com/celestiaorg/celestia-app/releases/tag/v10.4.0
- v10 major release notes (KMS policy, privval gRPC, disks): https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md
- Fibre server README: https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md
- Fibre server spec: https://github.com/celestiaorg/celestia-app/blob/v10.4.0/specs/src/fibre_server.md
- `x/valaddr` registry: https://github.com/celestiaorg/celestia-app/blob/v10.4.0/x/valaddr/README.md
- Privval gRPC with mutual TLS: https://github.com/celestiaorg/celestia-core/blob/v0.42.3/docs/guides/privval-grpc-tls.md
- Setup guide: https://docs.celestia.org/operate/consensus-validators/fibre/
- Monitoring guide: https://docs.celestia.org/operate/consensus-validators/fibre/metrics/

## Why we split Fibre from the validator

On mocha-5 we co-located Fibre with the validator, which was the recommendation for the first phase because the signer link had no TLS. Since `v10.4.0` the signer link supports mTLS, and the Celestia team's guidance has shifted:

- Running Fibre on a **separate server** is recommended for security reasons, and will be required in the future to scale.
- The host registered in `x/valaddr` is **public**: it appears in the on-chain registry and in community explorers. With Fibre on the validator host, the validator's address becomes public too. With a separate Fibre host, it doesn't.
- Under load, Fibre competes with consensus for memory (up to ~27 GiB of gRPC receive buffers with default limits), CPU (upload verification), disk and bandwidth. Splitting removes that contention before it matters.

## Architecture

```
            Fibre clients (Internet)
                     │  TLS (automatic, endorsed by the consensus key)
                     ▼
┌──────────── Fibre host ────────────┐        ┌────────── Validator host ──────────┐
│  fibre ──── app gRPC (loopback) ──▶│        │  celestia-appd (validator)         │
│    │        standby celestia-appd  │        │  consensus key, local file         │
│    │        (full node, no key)    │        │                                    │
│    └──────── signer gRPC ──────────┼─mTLS──▶│  privval gRPC listener             │
│                                    │        │  (firewalled to the Fibre host)    │
└────────────────────────────────────┘        └────────────────────────────────────┘
```

Key points:

- **Only the signing link crosses between hosts**, and it is encrypted and mutually authenticated with mTLS.
- The **app gRPC link is never exposed**: it is plaintext by design, so Fibre reads chain state from a standby full node running **on the Fibre host itself**, over loopback. That standby node holds no consensus key and exposes no signer.
- The consensus key lives in a local key file on the validator (no remote KMS), so the 10 ms signing-latency requirement, which is measured on celestia-app for remote signers, is not a concern.
- Keep both hosts close to each other: Celestia warns that distance on this link can cost endorsements. Measure the round trip before committing to a design; ours is well under a millisecond, so the signing link adds negligible latency.

## Prerequisites checklist

| Requirement | Status |
|---|---|
| v10 multiplexer `celestia-appd` installed before activation (not standalone, not Cosmovisor) | Confirmed on all our mainnet consensus nodes |
| Fibre binary from the same release, glibc ≥ 2.34 | Confirmed |
| CPU passes `tools/cpu_requirements`; **each host** must pass when Fibre and validator run separately | Both hosts use the same CPU model that passed the benchmark with 1.5x–4.7x margin |
| 32 GB RAM minimum | Confirmed, with large headroom |
| Validator bonded | Confirmed |
| Disk and bandwidth sized to stake (official calculator) | Launch needs are well under 100 GB and 100 Mbps for our stake |
| App gRPC enabled on the node Fibre reads from | Confirmed (standby node, loopback) |
| Privval gRPC enabled on the validator | Confirmed, with mTLS |
| Fibre client port `7980` reachable from the Internet | Confirmed from an external network |
| Separate disks for Fibre and celestia-app data | **Accepted deviation**: same volume, with ample free space and monitoring |

## Step 1 — A dedicated CA and certificates

Following the celestia-core privval TLS guide, we created a CA used **only** for this link, plus:

- a **server** certificate for the validator's privval listener, whose SAN matches the address Fibre dials;
- a **client** certificate for Fibre;
- an extra server certificate for the failover scenario (validator moved to the other host), kept offline.

```bash
mkdir -p ~/privval-pki && cd ~/privval-pki && umask 077

# CA
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout ca.key -out ca.crt -days 3650 -subj "/CN=<your-org>-privval-ca"

# Server certificate (validator). The SAN must match what Fibre dials.
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout server.key -out server.csr -subj "/CN=privval-server"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 \
  -extfile <(printf "subjectAltName=IP:<validator-address>,DNS:<internal-name>\nextendedKeyUsage=serverAuth")

# Client certificate (Fibre)
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout client.key -out client.csr -subj "/CN=fibre-client"
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt -days 825 -extfile <(printf "extendedKeyUsage=clientAuth")

openssl verify -CAfile ca.crt server.crt client.crt
```

Tips:

- Adding an internal DNS name to the server SAN, besides the address, lets you move the link to a private network later (e.g. a VLAN) by changing only name resolution on the Fibre host, without reissuing certificates.
- `ca.key` should not stay on any server. Each host only needs `ca.crt` plus its own certificate and key.
- To check a key matches its certificate: `openssl x509 -noout -pubkey -in X.crt | sha256sum` must equal `openssl pkey -pubout -in X.key | sha256sum`.
- Note the expiry date (825 days here) and add a renewal reminder. Certificates are loaded at startup, so rotation means issuing new ones from the same CA and restarting both services.

## Step 2 — Validator host: mTLS signer listener

Certificates go inside the node's home, owned by the node's user (our service runs with `ProtectSystem=full` under a dedicated user), with the private key at `0600`.

`config/config.toml`:

```toml
priv_validator_grpc_laddr = "<validator-address>:26669"
priv_validator_grpc_cert_file = "<node-home>/config/privval-tls/server.crt"
priv_validator_grpc_key_file = "<node-home>/config/privval-tls/server.key"
priv_validator_grpc_client_ca_file = "<node-home>/config/privval-tls/ca.crt"
priv_validator_grpc_allow_insecure = false
```

- All three TLS files must be set together; otherwise a non-loopback listener refuses to start. That is a feature: a misconfiguration fails loudly instead of exposing the signer.
- Bind to the specific address rather than `0.0.0.0`, and make sure that address is actually assigned to an interface on the host (`ip -4 addr`), otherwise the bind fails.
- **Firewall the port to the Fibre host only.** mTLS authenticates peers after the TCP connection is accepted, so the listener must not be reachable from anywhere else.
- Keep a backup of `config.toml` before editing and verify with `diff` that only these lines changed.

Applying it requires one node restart. We did it well before activation, at a quiet moment, with the rollback (restore the backup and restart) ready. After the restart:

- the multiplexer process logs `Started privval gRPC server addr=<validator-address>:26669`, while the embedded v9 child logs `Disabling privval gRPC server`, which is expected;
- we confirmed the validator was signing again by checking its signature in the last commits.

## Step 3 — Fibre host

### Standby node

The standby full node on the Fibre host serves app gRPC to Fibre:

- `priv_validator_laddr = ""` and `priv_validator_grpc_laddr = ""`: it is not a validator and exposes no signer.
- Confirm its local consensus key is **different** from the validator's (`celestia-appd comet show-validator`). This matters because a Fibre server pointed at a node with the wrong key fails TLS identity checks on clients, and because a copy of the real key on two hosts is a double-signing risk.
- `[grpc]` in `app.toml`: `enable = true` and a **loopback** address. Once the chain is on v10, `app.toml` is what governs this setting (the multiplexer enables it for the embedded v9 app regardless). In our case it had been left bound to all interfaces; we moved it to loopback after confirming no other service depended on it.

### Fibre

Dedicated system user, binary verified against the release checksums, home owned by that user, and the client certificate files (`ca.crt`, `client.crt`, `client.key` at `0600`) inside it.

`/etc/systemd/system/fibre.service`:

```ini
[Unit]
Description=Celestia Fibre server (mainnet)
Wants=network-online.target
After=network-online.target celestia-appd.service

[Service]
User=fibre
Group=fibre
ExecStart=/usr/local/bin/fibre start \
  --home <fibre-home> \
  --app-grpc-address 127.0.0.1:<standby-app-grpc-port> \
  --server-listen-address 0.0.0.0:7980 \
  --signer-grpc-address <validator-address>:26669 \
  --signer-grpc-ca-file <fibre-home>/tls/ca.crt \
  --signer-grpc-cert-file <fibre-home>/tls/client.crt \
  --signer-grpc-key-file <fibre-home>/tls/client.key \
  --otel-endpoint http://127.0.0.1:4318
Restart=always
RestartSec=3
LimitNOFILE=65535
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

Validated with `systemd-analyze verify` and left **not started** until activation: before v10, the `x/fibre` and `x/valaddr` modules don't exist.

Connection limits are not set as flags: `max_connections` and `max_concurrent_streams` live in `<fibre-home>/config/server_config.toml`, which Fibre creates on first start. A single upload uses 16 signers and fills all 16 default slots, blocking downloads, so we raise `max_connections` after the first start (worst-case memory ≈ `max_connections × max_concurrent_streams × 132 MiB`).

### Monitoring

Same pipeline as on Mocha: Fibre pushes OTLP to a local OpenTelemetry Collector, which exposes a Prometheus endpoint scraped by our central Prometheus (firewalled to it). Lessons carried over:

- Don't set a `namespace` on the collector's Prometheus exporter: Fibre metrics already carry the `fibre_` prefix, and the dashboards expect `fibre_server_*`, not `fibre_fibre_server_*`.
- Recent `otelcol-contrib` releases ship a `.sha256` file per archive instead of a single checksums file.
- `fibre.server.sign.duration` measures how long Fibre waits for endorsements. It is **not** the metric behind the 10 ms requirement; that one is `cometbft_privval_signing_latency_*` on celestia-app, and only applies to remote signers.

## Step 4 — Testing the link before activation

Fibre itself can't run before v10, but the signing link can be tested with `openssl s_client`, which performs the same TLS handshake.

**TLS 1.3 gotcha:** the client considers the handshake complete *before* the server has checked the client certificate. With `</dev/null`, `s_client` exits immediately and never sees the server's rejection, so a connection **without** a certificate looks just as successful as one with it. Keep the connection open for a few seconds:

```bash
T=<fibre-home>/tls

# Positive: with client certificate → "Verify return code: 0 (ok)" and no alert
(sleep 3) | sudo -u fibre openssl s_client -connect <validator-address>:26669 -alpn h2 \
  -CAfile $T/ca.crt -cert $T/client.crt -key $T/client.key 2>&1 \
  | grep -aiE 'alert|error|Verify return code'

# Negative: without client certificate → "tlsv13 alert certificate required"
(sleep 3) | sudo -u fibre openssl s_client -connect <validator-address>:26669 -alpn h2 \
  -CAfile $T/ca.crt 2>&1 | grep -aiE 'alert|error|Verify return code'
```

Our results:

| Test | Expected | Result |
|---|---|---|
| Handshake with client certificate | accepted | `Verify return code: 0 (ok)`, server then speaks HTTP/2 |
| Handshake without client certificate | rejected | `tlsv13 alert certificate required` (alert 116) |
| Signer port from the Fibre host | reachable | yes |
| Signer port from any other network | blocked | connection times out |
| `7980` from an external network (before start) | `Connection refused` (open, nothing listening) | yes |

What can't be tested on mainnet before v10: Fibre actually starting, a real `SignRawBytes` signature over the remote link, and Fibre's behaviour if the validator restarts while Fibre is running (not documented). Our fallback plan covers the first two; for the last one, our rule is to restart Fibre and check its log after any validator restart.

## Step 5 — Activation day

1. **Before the height:** both nodes synced, signer listener up, collector running, Fibre installed but stopped, operator key available on the machine that will sign `set-host`.
2. **After the height:** confirm `app_version` is `10` on every node (`curl -s localhost:26657/abci_info`) and that the validator is still signing. Don't continue until both are true.
3. **Start Fibre:** `sudo systemctl enable --now fibre`, then follow the log. Expected, in order: `connected to app node` with `chain_id=celestia`, `loaded fibre providers`, `connecting to privval gRPC signer` with `transport=mtls`, `signer ready`, `server started` on `:7980`. A first `connection refused` to app gRPC right after the version switch is fine; `Restart=always` retries.
4. **Tune and verify:** raise `max_connections` in `server_config.toml`, restart, re-check the log; confirm `7980` is reachable from an external network and that metrics flow.
5. **Register only now**, with the validator's operator account (it can be signed from any machine holding that key, pointing at any mainnet RPC):

   ```bash
   celestia-appd tx valaddr set-host <public-fibre-address>:7980 \
     --from <operator-account> --chain-id celestia <fee flags>
   celestia-appd query valaddr provider <celestiavalcons-address>   # found: true
   ```

   Never register a host that isn't serving yet: clients would dial it and time out.

   Note: `celestia-appd debug addr <valoper>` prints a `Bech32 Con` value that is just the operator bytes re-encoded. It is **not** your consensus address; take that from `query slashing signing-info` or `comet show-address` on the validator.

6. **Close:** record the UTC time and tx hash, check the provider appears as reachable on community dashboards, and watch `fibre_server_upload_shard_budget_bytes` (derived from stake) and the first successful uploads.

The Foundation Delegation Program expects Fibre to be running and registered within 24 hours of activation.

## Registering an IP or a DNS name

The registry accepts either; Celestia makes no recommendation. A DNS name lets you move Fibre without a new transaction, **but only if you control a low TTL**: resolvers may keep serving the old address for the whole TTL. Without that control, an IP plus a `set-host` transaction (confirmed within one block) gives a faster and more predictable switch. We registered by IP for launch.

## Fallback plan

- **Fibre host fails, or the remote link misbehaves on activation day:** Fibre moves to the validator host. Everything is pre-installed there (binary, user, client certificate) and the link to the local listener was tested. The unit file is kept **outside** systemd's search path, so it can't be started by accident, and the Fibre port stays closed on the validator host until needed. Activation is: copy the unit into place, start it, open the port, `set-host` to the new address. The trade-off: while active, the validator's address is public.
- **Validator host fails:** the validator moves to the Fibre host's standby node through our standard key-migration procedure (never with the key active on two hosts). Fibre then signs locally over loopback, and the registered address doesn't change.
- An accidentally started second Fibre server cannot cause double signing: Fibre's signatures use `SignRawBytes` with its own domain (`fibre/pp:v0`) and can't produce consensus votes.

## Lessons learned

- Do **all** configuration that needs a validator restart well before activation, with a backup and a rollback ready, and verify signing right after.
- Test negative paths too: a mTLS link that accepts the right certificate is only half the test; it must also reject everything else, and the firewall must block other networks.
- Check what's listening and on which interface (`ss -tlnp`) instead of trusting config defaults; we found an app gRPC endpoint bound to all interfaces on a former sentry node.
- Keep PKI material in as few places as possible and delete transfer copies once installed.
- Bring forward anything that can be verified without v10 (operator key, fees, binary support for `tx valaddr`), so the activation window is spent only on what truly depends on it.

## Status

As of 18 Oct 2026: everything that can be prepared and tested before activation is done. Fibre will be started and registered once mainnet crosses block `15116658`. This document will be updated with the outcome.

Planned follow-ups:
- Move the signing link to a private network between both hosts.
- Import the v10.4.0 Grafana dashboards (`fibre.json`, `fibre-runtime.json`).
