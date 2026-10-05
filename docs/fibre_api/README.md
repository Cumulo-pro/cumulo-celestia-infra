# Fibre Network Monitor API

[![Fibre status](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx.svg)](https://cumulo.pro/services/celestia_mocha/fibre-api)
[![Fibre uptime 30d](https://fibre-api.cumulo.com.es/v1/mocha-5/badge/celestiavalcons14s4fvynqhq8u3rwmnef0r22hut6avrtylz0pfx.svg?metric=uptime&period=d30)](https://cumulo.pro/services/celestia_mocha/fibre-api)
[![network ready 2/3](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Ffibre-api.cumulo.com.es%2Fv1%2Fmocha-5%2Fsummary&query=%24.readiness.ready_meets_threshold&label=network%20ready%202%2F3&color=informational&style=flat&cacheSeconds=600)](./fibre-network-dashboard.md)

A public, read-only API by [Cumulo](https://cumulo.pro) that monitors **Fibre**, the validator-operated blob publishing protocol of Celestia (CIP-51, live on `mocha-5` since the v10 upgrade). It tells you, for every validator and for the network as a whole, whether Fibre hosts are registered and ready, how close the network is to the 2/3 threshold, how fast quorum is reached from several regions, whether shards are retained, and what publishers pay.

No key, no sign-up: `GET` requests, open CORS, JSON and Prometheus formats.

## Start here

| I want to... | Go to |
|---|---|
| See the state of the whole network right now | [Fibre Network Dashboard](./fibre-network-dashboard.md) |
| Check whether my validator's Fibre host is ready | [Usage guide, section 2](./fibre-api-guide.md#2-validator-status) |
| Add a status or uptime badge to my README | [Usage guide, section 12](./fibre-api-guide.md#12-badges) |
| Scrape metrics and set up alerts | [Usage guide, section 13](./fibre-api-guide.md#13-prometheus-and-alerts) |
| Plug an uptime monitor into my host's status | [Usage guide, section 14](./fibre-api-guide.md#14-uptime-monitors) |
| Explore blobs, publishers, escrow and fees | [Usage guide, sections 8 and 9](./fibre-api-guide.md#8-publishers-and-escrow) |
| Download the daily archive | [Usage guide, section 15](./fibre-api-guide.md#15-downloadable-archive-and-verification) |
| Find every endpoint in one table | [Usage guide, section 18](./fibre-api-guide.md#18-endpoint-reference) |
| Set up a Fibre server myself | [Fibre server setup](../fibre-setup/Fibre_Server_Setup.md) |

## Quick start

```bash
API=https://fibre-api.cumulo.com.es
CHAIN=mocha-5

# Is the network above 2/3 of ready stake?
curl -s "$API/v1/$CHAIN/summary" | jq '.readiness | {ready_meets_threshold, ready_stake_pct, validators_needed_to_threshold}'

# Find your validator by moniker, then check its Fibre host (HTTP 200 = ready, 503 = not ready)
VAL=$(curl -s "$API/v1/$CHAIN/search?q=YOUR_MONIKER" | jq -r '.validators[0].valcons')
curl -s "$API/v1/$CHAIN/validator/$VAL/status" | jq '{reason, ready}'

# Blobs and fees in the last 24 hours
curl -s "$API/v1/$CHAIN/blobs/stats?period=24h" | jq '{settlements, bytes, fees_utia}'
```

## At a glance

| | |
|---|---|
| **Base URL** | `https://fibre-api.cumulo.com.es` |
| **Network** | `mocha-5` (Celestia testnet) |
| **Access** | No key, `GET` only, open CORS |
| **Freshness** | Recomputed every minute, cached 30 to 60 seconds |
| **Limits** | 120 requests per minute per IP (search: 20) |
| **Tracking since** | 2026-09-25 |
| **Official page** | [cumulo.pro/services/celestia_mocha/fibre-api](https://cumulo.pro/services/celestia_mocha/fibre-api) (OpenAPI 3.1) |

> [!IMPORTANT]
> `null` means no data, never zero. Amounts are in `utia` (1 TIA = 1,000,000 utia). The 2/3 check is exact integer math on the server: use the boolean fields, never compare a rounded percentage with 66.67.

## Files in this folder

| File | What it is |
|---|---|
| [`fibre-api-guide.md`](./fibre-api-guide.md) | Full usage guide with real responses: every endpoint, conventions, Prometheus, alerts, archive and client examples. |
| [`fibre-network-dashboard.md`](./fibre-network-dashboard.md) | Network status board made only of badges. Copy any badge into your own page. |
| [`generate_network_dashboard.py`](./generate_network_dashboard.py) | Generator for the dashboard (Python 3, standard library only). |

The dashboard values update by themselves because every badge reads the API live. Run the generator again only when the API adds fields or the shape of its lists changes:

```bash
python3 generate_network_dashboard.py -o fibre-network-dashboard.md
```

## Feedback

Questions, bugs or ideas: open an issue in this repository or reach us at [cumulo.pro](https://cumulo.pro).

---

<sub>Maintained by [Cumulo](https://cumulo.pro) · Data from Celestia `mocha-5` testnet · API version: v1</sub>
