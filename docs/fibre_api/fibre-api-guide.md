# Fibre Network Monitor API · Usage Guide

[![Fibre status](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx.svg)](https://cumulo.pro/services/celestia_mocha/fibre-api)
[![Fibre uptime 30d](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx.svg?metric=uptime&period=d30)](https://cumulo.pro/services/celestia_mocha/fibre-api)

A public, read-only API with no key required. It exposes everything the **Fibre Network Monitor** by [Cumulo](https://cumulo.pro) shows: readiness against the 2/3 threshold, identity, round trips from several regions, signatures, shard retention, blobs, publishers and escrow. It is also available in **Prometheus** format for your own dashboards and alerts.

| | |
|---|---|
| **Base URL** | `https://fibre-api.cumulo.com.es` |
| **Network** | `mocha-5` (Celestia testnet) |
| **Access** | No key, `GET` only, open CORS |
| **Freshness** | Recomputed every minute |
| **Limits** | 120 requests per minute per IP (search: 20) |
| **Official docs** | [cumulo.pro/services/celestia_mocha/fibre-api](https://cumulo.pro/services/celestia_mocha/fibre-api), OpenAPI 3.1 |

> [!NOTE]
> Every output in this guide is a **real response** (trimmed for readability), captured on 2026-10-03 with aggregator version `1.21.0`. The numbers will change; the structure is what matters.

## Contents

1. [Setup](#1-setup)
2. [Validator status](#2-validator-status)
3. [Network status (summary)](#3-network-status-summary)
4. [Hourly history](#4-hourly-history)
5. [Blobs of a validator: signed, not needed, not ready](#5-blobs-of-a-validator)
6. [Data retention](#6-data-retention)
7. [Events and Atom feed](#7-events-and-atom-feed)
8. [Publishers and escrow](#8-publishers-and-escrow)
9. [Blobs, statistics and the fee formula](#9-blobs-statistics-and-the-fee-formula)
10. [Hourly activity](#10-hourly-activity)
11. [Search](#11-search)
12. [Badges](#12-badges)
13. [Prometheus and alerts](#13-prometheus-and-alerts)
14. [Uptime monitors](#14-uptime-monitors)
15. [Downloadable archive and verification](#15-downloadable-archive-and-verification)
16. [Client examples (JavaScript and Python)](#16-client-examples)
17. [Errors, limits and caching](#17-errors-limits-and-caching)
18. [Endpoint reference](#18-endpoint-reference)
19. [Good to know](#19-good-to-know)

---

## 1. Setup

The examples use `curl` and [`jq`](https://jqlang.github.io/jq/). Define these variables once:

```bash
API=https://fibre-api.cumulo.com.es
CHAIN=mocha-5
VAL=celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx
```

A validator can be named by its **consensus address** (`valcons`), its **operator address** (`valoper`) or its **moniker** (exact match, case-insensitive). If a moniker is shared by several validators, the API answers `409` with `{"error":"ambiguous","candidates":[...]}`. Use the `valcons` in that case.

Don't know your `valcons`? Look it up by moniker:

```bash
VAL=$(curl -s "$API/v1/$CHAIN/search?q=Cumulo" | jq -r '.validators[0].valcons')
echo $VAL
```

### Conventions

| Topic | How it works |
|---|---|
| Identity | Every figure about a validator is keyed on its `valcons`. |
| Amounts | Integers in `utia` (1 TIA = 1,000,000 utia). |
| Percentages | 0 to 100 in JSON (`*_pct`), 0 to 1 in Prometheus (`*_ratio`). |
| Times | ISO 8601 in UTC in JSON, Unix seconds in Prometheus. Durations in ms in JSON, seconds in Prometheus. |
| Periods | `h24`, `d7`, `d30` in objects, `24h`, `7d`, `30d`, `all` in query parameters. |
| Counts | Pairs `[ok, total]`, so you can rebuild any percentage and know how many checks it rests on. |
| No data | `null` means there is nothing to count yet. It **never** means zero. Prometheus leaves those series out. |
| 2/3 threshold | Always compared exactly in integers (`tokens * 3 >= bonded * 2`). Rounded percentages are for reading only. |

---

## 2. Validator status

### Is my host ready?

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/status" | jq '{reason, ready}'
```

```json
{
  "reason": "ready",
  "ready": true
}
```

The endpoint answers **200** while the host is ready according to the majority of regions and **503** when it is not. The body says why:

| `reason` | HTTP | Meaning |
|---|---|---|
| `ready` | 200 | Host ready. |
| `not_ready` | 503 | Host registered but not ready. |
| `no_host` | 503 | No Fibre host registered. |
| `monitor_unknown` | 503 | None of our probe regions reported. This is our monitor, not your host. |
| `warming_up` | 503 | The aggregator has just started. |

Only the HTTP code:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$API/v1/$CHAIN/validator/$VAL/status"
```

The reason also travels in a header:

```bash
curl -sI "$API/v1/$CHAIN/validator/$VAL/status" | grep -i x-fibre-reason
```

> [!TIP]
> Add `?unknown=ok` so that `monitor_unknown` and `warming_up` answer 200. That way only a problem with **your host** alerts you, never one of our monitor. See [uptime monitors](#14-uptime-monitors).

---

## 3. Network status (summary)

`/summary` is the big endpoint (more than 200 fields). Start by looking at its structure.

### Top-level keys

```bash
curl -s "$API/v1/$CHAIN/summary" | jq 'keys'
```

```json
[
  "agents", "blobs_24h", "concentration", "escrow", "history_since",
  "liveness_coefficient", "meta", "method", "missing", "network_uptime_24h",
  "params", "payment_formula", "quorum_by_region", "readiness",
  "recent_events", "retention_24h"
]
```

| Section | What it contains |
|---|---|
| `meta` | Chain, height, aggregator version, probe regions and time windows. |
| `readiness` | Network readiness against 2/3. |
| `quorum_by_region` | Quorum latency and validators needed, per region. |
| `liveness_coefficient` | How many validators, providers or countries must be lost to drop below 2/3. |
| `concentration` | Split by country and by hosting company (`by_country`, `by_provider`). |
| `missing` | Validators still missing a ready host. |
| `params`, `payment_formula`, `escrow` | Module parameters, fee formula and escrow. |
| `blobs_24h`, `retention_24h`, `network_uptime_24h` | 24 hour figures. |
| `method`, `history_since`, `agents`, `recent_events` | Methodology, start of the history and monitor metadata (explore them with `jq`). |

### Metadata and windows

```bash
curl -s "$API/v1/$CHAIN/summary" | jq '.meta | {chain_id, network, height, version, generated_at, live_regions}'
```

```json
{
  "chain_id": "mocha-5",
  "network": "testnet",
  "height": 1346160,
  "version": "1.21.0",
  "generated_at": "2026-10-03T13:42:23.698Z",
  "live_regions": ["US", "EU", "CA", "AS"]
}
```

`meta.windows` gives the exact `from` and `to` of the `h24`, `d7` and `d30` windows:

```bash
curl -s "$API/v1/$CHAIN/summary" | jq '.meta.windows'
```

### Quorum latency per region

```bash
curl -s "$API/v1/$CHAIN/summary" | jq -r '
  ["REGION","REACHED","RTT_MS","VALIDATORS","READY_STAKE_%"],
  (.quorum_by_region[] | [.region, .reached, .quorum_rtt_ms, .validators_needed, .ready_stake_pct])
  | @tsv' | column -t
```

Each element of `quorum_by_region` has these fields:

| Field | Meaning |
|---|---|
| `region` | Probe region (`US`, `EU`, `CA`, `AS`). |
| `reached` | The ready stake seen from that region reaches 2/3. |
| `quorum_rtt_ms` | Round trip to the slowest validator needed for 2/3. |
| `validators_needed` | Fastest validators needed for 2/3 of the stake. |
| `ready_validators`, `ready_stake_pct` | Ready validators and share of ready stake seen from that region. |
| `quorum_rtt_ms_excl_same_asn`, `validators_needed_excl_same_asn`, `same_asn_hosts` | Same figures, leaving out hosts in the probe's own network. |

### Discover any field without guessing

```bash
# Paths of every field containing "quorum" (first 20)
curl -s "$API/v1/$CHAIN/summary" | jq '[paths(scalars) | map(tostring) | join(".")] | map(select(test("quorum"))) | .[:20]'

# Compact structure: array indexes are collapsed to []
curl -s "$API/v1/$CHAIN/summary" | jq '[paths(scalars) | map(tostring) | join(".") | gsub("\\.[0-9]+"; ".[]")] | unique'
```

### Concentration

```bash
curl -s "$API/v1/$CHAIN/summary" | jq '.concentration | {countries: (.by_country | length), providers: (.by_provider | length)}'
curl -s "$API/v1/$CHAIN/summary" | jq '.concentration.by_provider[0]'
```

Each entry of `by_country` and `by_provider` includes `quorum_survives_loss`: whether the network keeps 2/3 if that whole country or provider is lost.

### Exact 2/3 with Prometheus

To decide whether the network reaches the threshold, use the metrics, which compute it in integers:

```bash
curl -s "$API/v1/$CHAIN/metrics?validator=$VAL" \
  | grep -E '^fibre_(ready_meets_threshold|validators_needed_to_threshold|ready_stake_ratio|registered_stake_ratio|ready_validators|bonded_validators)\{'
```

| Metric | Reading |
|---|---|
| `fibre_ready_meets_threshold` | `1` if the ready stake reaches 2/3. |
| `fibre_validators_needed_to_threshold` | Ready validators still needed (0 once reached). |
| `fibre_ready_stake_ratio` | For reading only, not for deciding. |

---

## 4. Hourly history

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/history?days=30" | jq '{valcons, first_seen_at, points: (.points | length)}'
curl -s "$API/v1/$CHAIN/validator/$VAL/history?days=30" | jq '.points[-1]'
```

The response has `chain`, `valcons`, `first_seen_at` and `points` (one point per hour with online share and RTT).

> [!IMPORTANT]
> On this route the `{id}` must be the **`valcons`**, not the moniker.

If you ask for a `days` value out of range, there is no error: the closest accepted value is used and the response says so in `days_applied`, `days_requested` and the `X-Fibre-Param-Adjusted` header.

---

## 5. Blobs of a validator

For every blob whose validator set included you, the API says what you did:

| `role` | Meaning | Is it a failure? |
|---|---|---|
| `signed` | You signed the blob. | No. |
| `not_collected` | Your host was ready, but the publisher stopped collecting signatures on reaching 2/3. | **No.** |
| `not_ready` | You could not sign because your host was not ready. | **Yes, this is the one that matters.** |

### Counter summary

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/blobs?limit=1" | jq '{moniker, total, counts}'
```

```json
{
  "moniker": "Cumulo",
  "total": 8633,
  "counts": { "signed": 7792, "not_collected": 841, "not_ready": 0 }
}
```

`counts` covers the **whole tracked history**. The endorsement percentage is `signed / (signed + not_collected)`:

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/blobs?limit=1" | jq '.counts | (.signed / (.signed + .not_collected) * 100)'
```

> [!NOTE]
> This calculation uses the whole history. The official `endorsements.d30` figure uses only the last 30 days.

### Blobs where you were not needed

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/blobs?role=not_collected&limit=20" \
  | jq -r '.blobs[] | [.at, .namespace_text, .blob_size, "\(.signatures)/\(.set_size)", .endorsed_stake_pct, .meets_threshold] | @tsv'
```

You will see `meets_threshold: true` on all of them: the publisher already had its 2/3 without you.

### Blobs you could not sign

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/blobs?role=not_ready&limit=20" | jq '{total, blobs}'
```

```json
{ "total": 0, "blobs": [] }
```

`total: 0` is the answer you want: you were never not ready on a blob of your set.

### Paging

```bash
curl -s "$API/v1/$CHAIN/validator/$VAL/blobs?limit=50&offset=50" | jq '{offset, limit, total, returned: (.blobs | length)}'
```

Results go from newest to oldest.

---

## 6. Data retention

To know whether a validator **keeps the data** it signed, always use the **obligations** metrics (only blobs it signed), not `served_ratio` (which includes blobs that may never have reached it).

```bash
curl -s "$API/v1/$CHAIN/metrics?validator=$VAL" \
  | grep -E '^fibre_validator_(obligations_served_ratio|obligation_checks|rows_verified_ratio|unreachable_checks)\{'
```

Output (trimmed, `24h` period):

```text
fibre_validator_obligations_served_ratio{chain="mocha-5",valcons="celestiavalcons14s4…",moniker="Cumulo",period="24h"} 1
fibre_validator_obligation_checks{chain="mocha-5",valcons="celestiavalcons14s4…",moniker="Cumulo",period="24h"} 48
fibre_validator_rows_verified_ratio{chain="mocha-5",valcons="celestiavalcons14s4…",moniker="Cumulo",period="24h"} 1
fibre_validator_unreachable_checks{chain="mocha-5",valcons="celestiavalcons14s4…",moniker="Cumulo",period="24h"} 0
```

Every metric with a `period` label comes in `24h`, `7d` and `30d`. Only the last day:

```bash
curl -s "$API/v1/$CHAIN/metrics?validator=$VAL" | grep 'period="24h"'
```

| Metric | How to read it |
|---|---|
| `fibre_validator_obligations_served_ratio` | Rows served for blobs you signed. **This is the retention metric.** |
| `fibre_validator_obligation_checks` | Checks behind the previous ratio. With very few, do not draw conclusions. |
| `fibre_validator_rows_verified_ratio` | Downloaded rows that matched the blob commitment. |
| `fibre_validator_unreachable_checks` | Checks that could not reach the host (they do not count as a retention failure). |

An **absent** series means "no data", never 0. The latest 200 individual checks are available at `/validator/{id}/checks`.

Network-wide figures for 24 hours:

```bash
curl -s "$API/v1/$CHAIN/metrics?validator=$VAL" | grep -E '^fibre_(retention_obligations_served_ratio_24h|rows_verified_ratio_24h)\{'
```

---

## 7. Events and Atom feed

```bash
# Latest 20 events of a validator
curl -s "$API/v1/$CHAIN/events?validator=$VAL&limit=20" | jq '{tracking_since, events: (.events | length)}'
```

```json
{ "tracking_since": "2026-09-25T14:45:00.627Z", "events": 0 }
```

An empty `events` list is not an error: it means that validator has produced no events (registrations, drops, host changes and so on).

### Event types

Types seen in the real history: `pay_for_fibre`, `registered`, `status`, `escrow_deposit`, `left`, `host_reconfirmed`. Filter with `type`, one or several separated by commas:

```bash
# Validator registrations and departures across the network
curl -s "$API/v1/$CHAIN/events?type=registered,left&limit=20" \
  | jq -r '.events[] | "\(.at)  \(.type)  \(.title // "")"'
```

> [!WARNING]
> Do not leave placeholders such as `<type1>` in the command. They are sent literally and return an empty list.

The `validator` filter accepts `valcons`, `valoper` or, for escrow events, the publisher account.

### Blob payments (`pay_for_fibre`)

Each event of this type includes `signer`, `blob_size`, `namespace_text`, `fee_utia`, `fee_text`, `signatures`, `set_size`, `endorsed_stake_pct`, `best_region` and a readable `title`, for example: `celestia1las8…ee9snr paid 3.53 TIA for a 16.0 MiB blob in sov-niko-a · 51/82 signatures, 70.72% of stake`.

### Atom feed

The same content, with the same filters, for RSS readers, Slack, Discord, Zapier or n8n:

```bash
curl -s "$API/v1/$CHAIN/events.atom?validator=$VAL"
```

---

## 8. Publishers and escrow

```bash
# Totals for the last 7 days
curl -s "$API/v1/$CHAIN/publishers?period=7d" | jq '.totals | {
  blobs, publishers,
  fees_tia: (.fees_utia / 1e6),
  price_utia_per_mib,
  escrow_held_tia: (.escrow_held_utia / 1e6)
}'
```

```json
{
  "blobs": 8480,
  "publishers": 4,
  "fees_tia": 30016.84,
  "price_utia_per_mib": 220488.33,
  "escrow_held_tia": 20088.93
}
```

```bash
# Who published in the period
curl -s "$API/v1/$CHAIN/publishers?period=7d" | jq -r '
  .publishers[] | select(.blobs > 0)
  | [.account, .blobs, .share_bytes_pct, .price_utia_per_mib, (.namespaces | join(","))] | @tsv'
```

```bash
# Escrow balance of each account
curl -s "$API/v1/$CHAIN/publishers?period=7d" | jq -r '.publishers[] | [.account, (.escrow_balance_utia / 1e6)] | @tsv'
```

Fields of each publisher: `account`, `blobs`, `bytes`, `fees_utia`, `share_bytes_pct`, `share_fees_pct`, `price_utia_per_mib`, `namespaces`, `first_blob_at`, `last_blob_at`, `blobs_all_time`, `deposited_utia`, `withdrawal_requested_utia`, `escrow_balance_utia`, `escrow_available_utia`, `withdrawals` and `withdrawals_history`.

### A single account

```bash
curl -s "$API/v1/$CHAIN/publishers/celestia1las83d0dt9gew3faq2mxp2gtupq5drclee9snr?period=30d&limit=20"
```

If the account has no blobs and no escrow moves in the tracked history, the answer is `404`:

```json
{ "error": "No blobs or escrow moves for this account in the tracked history" }
```

### Escrow withdrawals

`withdrawals` lists only pending ones. `withdrawals_history` has every request. Once past `available_at` and no longer pending, a withdrawal shows as `paid_inferred` (the module pays it with no transaction of its own).

---

## 9. Blobs, statistics and the fee formula

### Latest blobs

Each blob carries long validator lists. For a compact view:

```bash
curl -s "$API/v1/$CHAIN/blobs?limit=10" | jq -r '
  .blobs[] | [.at, .namespace_text, .blob_size, "\(.signatures)/\(.set_size)",
              .endorsed_stake_pct, .meets_threshold, .availability.state, .availability.coverage_pct] | @tsv'
```

To see the structure of one blob without the validator lists:

```bash
curl -s "$API/v1/$CHAIN/blobs?limit=1" | jq '.blobs[0] | del(.endorsers, .not_collected, .ineligible)'
```

Main fields:

| Field | Meaning |
|---|---|
| `signatures`, `set_size` | Signatures collected and size of the validator set. |
| `endorsed_power`, `set_power`, `meets_threshold` | Signing power against total power, and whether it reaches 2/3 (exact integer comparison). |
| `endorsed_stake_pct` | Rounded percentage, for reading only. |
| `endorsers`, `not_collected`, `ineligible` | Who signed, who did not need to be collected and who was not eligible. |
| `signed_bits`, `ready_bits` | Bitmaps (hex) over the validator list of the `set_id`. |
| `region_match` | Share of stake that answered from each region. |
| `availability` | Result of the availability check (see below). |

Filters: `namespace` (hex or text), `payer` (`celestia1…`), `validator` (`valcons`) and `limit`.

### Availability

```json
"availability": {
  "verified_rows": 15348,
  "total_rows": 16384,
  "needed_rows": 4096,
  "reconstructable": true,
  "state": "proven",
  "coverage_pct": 100
}
```

`reconstructable: false` with `state: "not_proven"` means **our sample did not reach the 4,096 verified rows** needed to prove it, not that the blob is lost. `coverage_pct` says how far it got. `availability: null` means it was not checked.

### Statistics

```bash
curl -s "$API/v1/$CHAIN/blobs/stats?period=24h" | jq '{
  settlements, bytes, fees_utia, fee_check,
  sizes: [.size_buckets[] | select(.blobs > 0)]
}'
```

```json
{
  "settlements": 6,
  "bytes": 671350784,
  "fees_utia": 119145000,
  "fee_check": { "checked": 6, "matching": 6, "mismatches": [] },
  "sizes": [
    { "from_bytes": 0, "to_bytes": 262144, "blobs": 1, "bytes": 262144 },
    { "from_bytes": 67108864, "to_bytes": 134217728, "blobs": 5, "bytes": 671088640 }
  ]
}
```

`fee_check` compares the charge of **every blob** with the formula: `checked` is the number of blobs reviewed, `matching` the ones that agree and `mismatches` the ones that do not. Size buckets include the upper bound (up to 256 KiB, 1, 4, 16, 64 and 128 MiB). Periods: `24h`, `7d`, `30d`, `all` (default `all`).

### Fee formula

```text
charge (utia) = (fixed_gas + gas_per_chunk × ceil(blob_size / chunk_bytes)) × utia_per_gas
```

The constants travel in `payment_formula` together with the app version:

```bash
curl -s "$API/v1/$CHAIN/blobs/stats?period=24h" | jq '.payment_formula'
```

```json
{
  "fixed_gas": 650000,
  "gas_per_chunk": 45000,
  "chunk_bytes": 262144,
  "utia_per_gas": 1,
  "max_blob_bytes": 134217728,
  "app_version": "10.2.0-mocha"
}
```

Compute the expected charge for a given size:

```bash
curl -s "$API/v1/$CHAIN/blobs/stats?period=24h" | jq --argjson size 16777216 '
  .payment_formula | (.fixed_gas + .gas_per_chunk * (($size / .chunk_bytes) | ceil)) * .utia_per_gas'
```

Results that match real blobs on the network:

| Blob size | Charge (utia) | Charge (TIA) |
|---|---|---|
| 256 KiB | 695,000 | 0.695 |
| 16 MiB | 3,530,000 | 3.53 |
| 128 MiB | 23,690,000 | 23.69 |

> [!NOTE]
> For an app version whose constants we do not have, the charge check is reported as `null` instead of being guessed.

---

## 10. Hourly activity

```bash
# Only the hours with activity (last 7 days)
curl -s "$API/v1/$CHAIN/activity/series?range=7d" | jq -c '.points[] | select(.blobs > 0)'
```

```json
{"t":"2026-10-02T14:00:00.000Z","blobs":5,"bytes":671088640,"fees_utia":118450000,"timeouts":0,"payers":1}
{"t":"2026-10-02T18:00:00.000Z","blobs":1,"bytes":262144,"fees_utia":695000,"timeouts":0,"payers":1}
```

Each point has `blobs`, `bytes`, `fees_utia`, `timeouts` (payment promises settled by timeout) and `payers`. The step depends on the range: `24h` and `7d` hourly, `30d` every 6 hours and `all` daily (`step_s` tells you).

---

## 11. Search

`/search` accepts a transaction hash, blob id or commitment, height, account, namespace, validator (moniker, `valcons`, `valoper`) or a UTC day (`YYYY-MM-DD`). Specific limit: **20 requests per minute**.

### By moniker

```bash
curl -s "$API/v1/$CHAIN/search?q=Cumulo" | jq '.validators'
```

```json
[
  {
    "valcons": "celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx",
    "operator_address": "celestiavaloper16yv2p64nzps0lqcealnumrsqc2rcszkd0uzlp8",
    "moniker": "Cumulo",
    "registered": true,
    "status": "online"
  }
]
```

### By day

```bash
curl -s "$API/v1/$CHAIN/search?q=2026-09-28" | jq '.day | del(.files)'
```

```json
{
  "day": "2026-09-28",
  "blobs": 8472,
  "bytes": 142079164416,
  "fees_utia": 29896305000,
  "timeouts": 0,
  "payers": 2,
  "namespaces": 2,
  "avg_endorsed_stake_pct": 69.25,
  "events_by_type": {
    "registered": 6, "status": 2, "escrow_deposit": 4,
    "pay_for_fibre": 8472, "left": 1, "host_reconfirmed": 1
  }
}
```

The archive files for that day, with their hashes:

```bash
curl -s "$API/v1/$CHAIN/search?q=2026-09-28" | jq -r '.day.files[] | [.kind, .name, .lines, .sha256] | @tsv'
```

> [!WARNING]
> Replace placeholders such as `<txhash>` with a real value. A search with no match returns `"kinds": []` and empty lists, with no error.

---

## 12. Badges

Embeddable SVG badges for READMEs, websites or Discord:

```markdown
![status](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/VALCONS.svg)
![uptime](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/VALCONS.svg?metric=uptime&period=d30)
![signed](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/VALCONS.svg?metric=signed&period=d7)
![serve](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/VALCONS.svg?metric=serve&period=d7)
```

```html
<img alt="Fibre uptime" src="https://fibre-api.cumulo.com.es/v1/mocha-5/badge/VALCONS.svg?metric=uptime&period=d30">
```

| Parameter | Values | Default |
|---|---|---|
| `metric` | `status`, `uptime`, `signed`, `serve` | `status` |
| `period` | `h24`, `d7`, `d30` (applies to `uptime`, `signed` and `serve`) | `h24` |

---

## 13. Prometheus and alerts

`/v1/{chain}/metrics` serves the network figures and every bonded validator (about 3,000 series), or only the validators you pass in `validator` (repeatable or comma separated, up to 50). **Scraping only your own keeps it small.**

### Scrape configuration

```yaml
scrape_configs:
  - job_name: fibre
    scheme: https
    metrics_path: /v1/mocha-5/metrics
    params:
      validator: ['celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx']
    scrape_interval: 60s   # the data changes once per minute
    static_configs:
      - targets: ['fibre-api.cumulo.com.es']
```

With `validator`, the API answers `404` when nothing matched, `409` for an ambiguous moniker and `200` otherwise, with `fibre_validator_lookup_found{query="…"}` set to `1` or `0` for each query. A `0` means the query is wrong, not the validator.

### Useful queries (PromQL)

```promql
fibre_validator_ready                                          # 1 = host ready
fibre_validator_rtt_seconds                                    # smoothed RTT per region
fibre_validator_online_ratio{period="7d"}                      # uptime
fibre_validator_signed_ratio{period="7d"}                      # signed share of blobs made while ready
fibre_validator_obligations_served_ratio{period="24h"}         # retention
(fibre_validator_cert_expiry_timestamp_seconds - time()) / 86400   # days until the TLS certificate expires
fibre_validators_needed_to_threshold                           # validators still needed for 2/3
```

### Example alerts

```yaml
groups:
  - name: fibre
    rules:
      - alert: FibreHostNotReady
        # "and on(chain) ..." keeps it quiet while none of our probes report
        expr: fibre_validator_ready == 0 and on(chain) (sum by (chain) (fibre_probe_up) > 0)
        for: 5m
        annotations:
          summary: "{{ $labels.moniker }}: Fibre host not ready by majority of regions"

      - alert: FibreRegionDown
        expr: fibre_validator_region_up == 0
        for: 15m
        annotations:
          summary: "{{ $labels.moniker }}: not ready from {{ $labels.region }}"

      - alert: FibreCertificateExpiring
        expr: fibre_validator_cert_expiry_timestamp_seconds - time() < 14 * 86400
        annotations:
          summary: "{{ $labels.moniker }}: Fibre certificate expires in less than 14 days"

      - alert: FibreNotKeepingData
        # An absent series means no data: it never fires on that
        expr: fibre_validator_obligations_served_ratio{period="24h"} < 0.99 and on(chain, valcons) fibre_validator_obligation_checks{period="24h"} >= 5
        for: 15m
        annotations:
          summary: "{{ $labels.moniker }}: did not serve rows of blobs it signed"

      # Our data is stale: do not blame the validator
      - alert: FibreMonitorStale
        expr: time() - fibre_data_generated_timestamp_seconds > 600
```

### Metrics catalog

The full list (72 families) is always in the `/metrics` response itself. The most used:

| Metric | Extra labels | Meaning |
|---|---|---|
| `fibre_ready_meets_threshold` | | Ready stake reaches 2/3 (`1`) or not (`0`), computed exactly. |
| `fibre_validators_needed_to_threshold` | | Ready validators still needed for 2/3. |
| `fibre_quorum_rtt_seconds` | `region` | Round trip to the slowest validator needed for 2/3. |
| `fibre_liveness_coefficient` | `dimension` | Fewest validators, providers or countries whose loss drops ready stake below 2/3. |
| `fibre_validator_ready` | | Host ready by majority of regions (identity verified). |
| `fibre_validator_region_up` | `region` | Host ready from this region. |
| `fibre_validator_identity_ok` | | TLS identity matches the consensus key. |
| `fibre_validator_cert_expiry_timestamp_seconds` | | Expiry of the TLS certificate. |
| `fibre_validator_online_ratio` | `period` | Share of one-minute checks in which the host was ready. |
| `fibre_validator_signed_ratio` | `period` | Share of blobs signed. Below 1 is not a failure by itself. |
| `fibre_validator_obligations_served_ratio` | `period` | **Retention**: rows served for blobs it signed. |
| `fibre_data_generated_timestamp_seconds` | | When these figures were computed. |

---

## 14. Uptime monitors

`/validator/{id}/status` is designed for plain HTTP monitors (UptimeRobot, Uptime Kuma, Healthchecks and similar): **200 when the host is ready, 503 when it is not**, with nothing to parse.

```text
URL:     https://fibre-api.cumulo.com.es/v1/mocha-5/validator/<valcons>/status?unknown=ok
Method:  GET
Alert:   when the answer is not 200
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$API/v1/$CHAIN/validator/$VAL/status?unknown=ok"
```

With `?unknown=ok`, the reasons `monitor_unknown` and `warming_up` answer 200, so only a problem with your host can page you.

---

## 15. Downloadable archive and verification

Every event and blob is also kept as one **JSON Lines file per day and kind**. Closed days are gzip compressed, **never change**, and their `sha256` is listed in `manifest.json`, so any copy can be verified.

```bash
# Available files and the manifest with the sha256 values
curl -s "$API/v1/$CHAIN/exports" | jq -r '.files[] | select(.closed) | .url'
curl -s "$API/v1/$CHAIN/exports/manifest.json" | jq 'keys'
```

### Download and verify one day

```bash
DAY=2026-09-28
curl -s "$API/v1/$CHAIN/search?q=$DAY" \
  | jq -r '.day.files[] | select(.closed) | [.name, .sha256, .url] | @tsv' \
  | while IFS=$'\t' read -r name sha url; do
      curl -sO "$url"
      echo "$sha  $name" | sha256sum -c -
    done
```

### A whole month in one file

```bash
curl -sO "$API/v1/$CHAIN/exports/month/2026-09.tar"
tar xf 2026-09.tar    # the daily files plus manifest-2026-09.json with their sha256
```

A month that is over is immutable (its hash is under `bundles` in the manifest). The current month is built on request and is partial.

### File kinds

| Kind | Content |
|---|---|
| `blobs` | One line per blob. Signers are bitmaps (`signed_bits`, `ready_bits`) over the validator list of the `set_id`. |
| `valsets` | Each distinct validator set, the first time a blob uses it. |
| `valpowers` | Each distinct voting power vector (from 1.19). |
| `events` | One line per event. |
| `retention` | Download checks: `hourly` lines per hour, region and validator, plus a `check` line for every check that was not served (the evidence). |
| `blobs_power`, `blobs_eligibility`, `valsets_backfill`, `retention_class`, `blobs_valpowers` | Supplementary corrections to closed days, listed in the manifest. |

Today's files are plain `.ndjson` and still growing. Only the closed `.gz` days are final. To keep a complete copy up to date, the aggregator ships `deploy/mirror-archive.sh`, which downloads every closed file that is missing and checks its `sha256` (run it daily from cron).

---

## 16. Client examples

### JavaScript (browser or Node 18+)

CORS is open, so this works directly from a web page.

```javascript
const API = "https://fibre-api.cumulo.com.es";

async function fibre(path) {
  const res = await fetch(`${API}${path}`);

  if (res.status === 429) {                       // rate limited
    const wait = Number(res.headers.get("Retry-After") ?? 5);
    await new Promise(r => setTimeout(r, wait * 1000));
    return fibre(path);
  }

  const body = await res.json();
  if (res.status === 409) throw new Error(`Ambiguous moniker: ${JSON.stringify(body.candidates)}`);
  if (res.status === 503 && path.includes("/status")) return body; // on /status a 503 is data
  if (!res.ok) throw new Error(body.error ?? res.statusText);

  if (res.headers.get("X-Fibre-Param-Adjusted")) console.warn("The API adjusted a parameter");
  return body;
}

const VAL = "celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx";

const status = await fibre(`/v1/mocha-5/validator/${VAL}/status`);
console.log(status.reason, status.ready);          // "ready" true

const summary = await fibre("/v1/mocha-5/summary");
for (const r of summary.quorum_by_region) {
  console.log(r.region, r.reached, `${r.quorum_rtt_ms} ms`, `${r.validators_needed} validators`);
}
```

### Python

```python
import time
import requests

API = "https://fibre-api.cumulo.com.es/v1/mocha-5"
VAL = "celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx"


def get(path, **params):
    r = requests.get(f"{API}{path}", params=params, timeout=30)
    if r.status_code == 429:                       # rate limited
        time.sleep(int(r.headers.get("Retry-After", 5)))
        return get(path, **params)
    if r.status_code == 409:
        raise ValueError(f"Ambiguous moniker: {r.json()['candidates']}")
    r.raise_for_status()
    return r.json()


def status(valcons):
    """On /status a 503 is data (host not ready), not an error."""
    r = requests.get(f"{API}/validator/{valcons}/status", timeout=30)
    return r.json()


print("Status:", status(VAL)["reason"])

# Quorum latency per region
summary = get("/summary")
for q in summary["quorum_by_region"]:
    print(f'{q["region"]:>3}  reached={q["reached"]}  rtt={q["quorum_rtt_ms"]} ms  '
          f'validators={q["validators_needed"]}  ready_stake={q["ready_stake_pct"]}%')

# Endorsements over the whole tracked history
c = get(f"/validator/{VAL}/blobs", limit=1)["counts"]
print(f'Signed: {c["signed"]}  Not needed: {c["not_collected"]}  Not ready: {c["not_ready"]}')
print(f'Endorsements: {c["signed"] / (c["signed"] + c["not_collected"]) * 100:.2f}%')

# Publisher spend over 7 days
t = get("/publishers", period="7d")["totals"]
print(f'Blobs: {t["blobs"]}  Fees: {t["fees_utia"] / 1e6:.2f} TIA  Escrow: {t["escrow_held_utia"] / 1e6:.2f} TIA')
```

---

## 17. Errors, limits and caching

| Situation | Response |
|---|---|
| Too many requests | `429` with `{"error":"rate_limited"}` and a `Retry-After` header (seconds). Wait that long. |
| Ambiguous moniker | `409` with `{"error":"ambiguous","candidates":[...]}`. Use the `valcons`. |
| Missing resource | `404` with `{"error":"…"}`. |
| Chain configured but first cycle not finished (after a restart) | `503`. On `/status`, `reason` says why. |
| Parameter out of range (`range`, `period`, `days`) | **Not an error.** The closest accepted value is used and reported in `*_applied` / `*_requested` and the `X-Fibre-Param-Adjusted` header. |

Every error, 4xx or 5xx, is JSON with CORS. 4xx answers are cached for 5 seconds.

### Per-IP limits

| Scope | Limit | Burst |
|---|---|---|
| General | 120 requests per minute | 60 |
| `/search` | 20 requests per minute | 10 |
| `/status` | 300 requests per minute (its own budget) | 60 |

### Caching and freshness

Responses are cached for 30 to 60 seconds and the data is recomputed every minute, so **polling faster gives nothing new**. Closed archive days never change.

### Stability policy

On endpoints marked `stable`, the v1 policy is that fields are only **added**, never renamed, removed or changed in meaning. A breaking change would mean a new `/v2`, announced in advance, with `/v1` kept running for a transition period. `internal` endpoints feed the web page and may change without notice. Build on the documented fields.

---

## 18. Endpoint reference

All endpoints are `GET`. Replace `{chain}` with `mocha-5`.

### Network

| Endpoint | Status | Description |
|---|---|---|
| `/v1/chains` | stable | Chains served and whether data is available. |
| `/v1/{chain}/summary` | stable | Readiness against 2/3, quorum per region, liveness, concentration, missing validators, parameters, escrow and 24h figures. |
| `/v1/{chain}/timeseries?range=` | stable | Network series: registered and ready validators and stake, countries, hosting companies, liveness and RTT. `range`: `24h`, `7d`, `30d`, `all`. |
| `/health` | stable | Aggregator version, probe regions answering and history index size. |

### Validators

| Endpoint | Status | Description |
|---|---|---|
| `/v1/{chain}/validators` | stable | Every bonded validator with everything we measure. |
| `/v1/{chain}/providers` | stable | Same, only those with a Fibre host registered. |
| `/v1/{chain}/validator/{id}/checks` | stable | Its latest 200 download checks, a per-day summary (30 days) and throughput by region. |
| `/v1/{chain}/validator/{id}/history?days=` | stable | Hourly history (the `id` must be the `valcons`). |
| `/v1/{chain}/validator/{id}/blobs?role=&limit=&offset=` | stable | Blobs of its set and its role in each. |

### Monitoring

| Endpoint | Status | Description |
|---|---|---|
| `/v1/{chain}/validator/{id}/status?unknown=ok` | stable | 200 when ready, 503 otherwise. The body says why. |
| `/v1/{chain}/metrics?validator=` | stable | Prometheus format 0.0.4 (up to 50 validators per request). |
| `/v1/{chain}/badge/{id}.svg?metric=&period=` | stable | Embeddable SVG badge. |

### Blobs and market

| Endpoint | Status | Description |
|---|---|---|
| `/v1/{chain}/blobs?limit=&namespace=&payer=&validator=` | stable | Latest blobs (up to 500) with who signed. |
| `/v1/{chain}/blobs/stats?period=` | stable | Sizes, repeated commitments and the check of every blob's charge. |
| `/v1/{chain}/blobs/{id}` | stable | One blob, by tx hash, `txhash:msg_index` or blob id. |
| `/v1/{chain}/namespaces` | stable | Every namespace seen, with blobs, bytes, fees and payers. |
| `/v1/{chain}/publishers?period=` | stable | Per publisher: blobs, bytes, fees, price per MiB, deposits, withdrawals and escrow. |
| `/v1/{chain}/publishers/{account}?period=&limit=&offset=` | stable | One account: its row, namespaces, escrow moves and blobs. |
| `/v1/{chain}/activity/series?range=` | stable | Blobs, bytes, fees, payers and promise timeouts per step. |
| `/v1/{chain}/activity` | internal | Page data for the website. Use the endpoints above instead. |

### Events and search

| Endpoint | Status | Description |
|---|---|---|
| `/v1/{chain}/events?validator=&type=&limit=` | stable | Registrations, host changes, identity failures, going offline and back, escrow moves and timeouts. |
| `/v1/{chain}/events.atom` | stable | The same events as an Atom feed. |
| `/v1/{chain}/search?q=&limit=` | stable | Search the history (limit: 20 per minute). |

### Archive

| Endpoint | Status | Description |
|---|---|---|
| `/v1/{chain}/exports` | stable | List of archive files. |
| `/v1/{chain}/exports/manifest.json` | stable | `sha256` of every closed file. |
| `/v1/{chain}/exports/{file}` | stable | Download one daily or monthly file. |
| `/v1/{chain}/exports/month/{file}` | stable | One whole month in a single `.tar`. |

---

## 19. Good to know

- **`null` is not 0.** An absent Prometheus series is not 0 either: it means "no data".
- **Not signing a blob is not failing.** The publisher stops collecting signatures at 2/3. The real failure is `not_ready`.
- **To judge retention use `obligations_served_ratio`**, never `served_ratio`, and check how many checks back it (`obligation_checks`).
- **`not_proven` does not mean "lost".** It means the sample did not reach the 4,096 rows needed.
- **The 2/3 threshold is exact in integers.** Do not compare rounded percentages against 66.67.
- **Amounts are in `utia`**: divide by `1e6` for TIA.
- **Placeholders such as `<...>` must be replaced.** They are sent literally and return empty results or a `404`.
- **Polling faster than once a minute is pointless.** Caching and recomputation run every 30 to 60 seconds.
- **If you see `monitor_unknown`, the problem is on the monitor side**, not your host.

---

<sub>Maintained by [Cumulo](https://cumulo.pro) · Data from Celestia `mocha-5` testnet · API version: v1</sub>
