# fibre-telegram-bot

**@CumuloFibreBot** ([open in Telegram](https://t.me/CumuloFibreBot)) is a Telegram bot with
on-demand status reports for [Celestia Fibre](https://docs.celestia.org/operate/consensus-validators/fibre/)
on mainnet and Mocha testnet.

It is also a working example of what you can build on the **Cumulo Fibre Network Monitor API**
(`https://fibre-api.cumulo.com.es`), a free public API with readiness, signing, retention,
concentration and blob market data for every Fibre server. Every figure the bot shows comes from
that API. Fork it, take the parts you need, or use it as a reference for your own bot, dashboard or
alerting tool.

- API usage guide: [`docs/fibre_api`](../../docs/fibre_api/README.md) in this repository, and the
  official page with the OpenAPI spec: https://cumulo.pro/services/celestia_mocha/fibre-api
- Dashboards built on the same API: [mainnet](https://cumulo.pro/services/celestia/fibre.php) ·
  [Mocha](https://cumulo.pro/services/celestia_mocha/fibre.php)

## What the bot does

`/fibre` opens a menu with inline buttons, editing the same message in place:

```
/fibre
  [Network] [A validator] [Market] [Report card (image)] [Switch to Mocha testnet]
      |
  period: [24h] [7d] [30d] (+ [Since start] for Network and Market)
      |
  report + [Refresh] [Back]

Report card → [This week] [Last week] [This month] [Last month] [This year]
  1200 x 630 PNG + share caption + [Refresh] [Details] [Back]
```

- **Network:** whether ready stake meets 2/3, ready / registered / no-host validators, quorum
  latency by probe region, the liveness coefficient (how many validators, providers or countries
  would have to go offline together to drop below 2/3), which single countries or providers would
  break 2/3, retention and network uptime.
- **A validator** (moniker or valcons): readiness per region, stake, uptime, signed blobs,
  retention of signed data, blob outcomes, RTT per region, certificate expiry and warnings.
- **Market:** blobs, data, fees, publishers, average price per MiB, a fee formula check, blob
  sizes, top publishers and escrow.
- **Report card:** a shareable image for the network or one validator, the same one the
  [cumulo.pro Fibre report](https://cumulo.pro/services/celestia_mocha/fibre-report) offers as
  "Download image". The font (Karla, SIL Open Font License) is bundled, so the image looks the
  same on every server.

| Command | What it does |
|---|---|
| `/fibre` | Report menu |
| `/status <moniker or valcons>` | One-line readiness check (defaults to the last validator used) |
| `/card [moniker or valcons]` | Report card image for last week |
| `/help` | Short help |
| `/id` | Your user id and the chat id (for the configuration) |
| `/weekly [preview\|post]` | Admins: weekly channel post status, preview or post now |

**Networks.** Mainnet is the default. Commands and inline queries take a network word:
`/status mocha cumulo`, `/card mainnet`, `@CumuloFibreBot mocha cumulo`. Every message names its
network (Mainnet / Mocha testnet).

**Inline mode.** Type `@CumuloFibreBot` in any chat (the bot does not need to be a member) and pick
a card. `@CumuloFibreBot cumulo` shows cards for one validator, matched locally by moniker. Some groups
block inline bots: there, open the card in the bot and forward it.

## Build your own with the Fibre API

The API needs no key. What this bot learned, in case it helps your client:

| Need | Endpoint | Notes |
|---|---|---|
| Chains and whether Fibre is live | `/v1/chains` | `key`, `chain_id`, `network`, `fibre_active`, `app_version` |
| Network snapshot | `/v1/<chain>/summary` | Readiness, quorum, liveness, concentration, retention. Poll it once a minute. |
| One validator's metrics | `/v1/<chain>/metrics?validator=<valcons>` | Prometheus text. The full `/metrics` is about 830 KB: fetch it rarely. |
| Validator status | `/v1/<chain>/validator/<id>/status` | Readiness, regions, RTT, uptime, warnings |
| Report card data | `/v1/<chain>/report?period=week&validator=<moniker>` | Calendar weeks, months and years in UTC |
| Market | `/v1/<chain>/blobs/stats`, `/v1/<chain>/publishers` | Fees, sizes, publishers, escrow |
| Search | `/v1/<chain>/search?q=` | Lower rate limit, match locally when you can |

- **Rate limits:** 120 requests per minute (burst 60) and 20 per minute for `/search`; this bot
  stays below them at 100 and 15. On `429` wait for `Retry-After`. This bot serves every user from one shared poller, so its API load does
  not grow with its users (500 simultaneous button presses produce 9 API calls).
- **A chain where Fibre is not active yet** answers `503` with `{"error": "fibre_not_active", ...}`,
  an `activation` object (blocks left, ETA) and `Retry-After: 600`. It is not an outage: ask again
  later and start using the chain when `/v1/chains` says `fibre_active: true`.
- **Freshness:** `summary.meta.generated_at` says when the data was computed.
- **Missing values** are missing, not zero: show `n/a`.
- **Shares of stake** in `concentration` and `liveness_coefficient` are percentages of total stake.
- **Be identifiable:** send a User-Agent with your project name and a contact URL.

The full endpoint reference is in the [usage guide](../../docs/fibre_api/fibre-api-guide.md#18-endpoint-reference).
Response structures confirmed against the live API are in [`DISCOVERY.md`](DISCOVERY.md), and the
recorded responses in `fixtures/`.

## Run your own copy

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token.
2. `git clone https://github.com/Cumulo-pro/cumulo-celestia-infra` and
   `cd cumulo-celestia-infra/tools/fibre-telegram-bot`.
3. `cp .env.example .env` and set `TELEGRAM_BOT_TOKEN`.
4. `python3 -m venv .venv && . .venv/bin/activate`
5. `pip install .`
6. `fibre-bot`
7. Open your bot in Telegram and send `/fibre`.

For inline mode, run `/setinline` in @BotFather. Inline images need a place to be uploaded once
(Telegram then reuses them by file_id): `STORAGE_CHAT_ID` (a private channel with the bot as admin)
or, if empty, the first `ADMIN_CHAT_IDS` entry. Without either, inline results are shared as text.

The bot uses long polling: no public domain, webhook or open port is needed.

### Docker

```bash
docker build -f deploy/Dockerfile -t fibre-telegram-bot .
docker run -d --name fibre-bot --env-file .env -v fibre-bot-data:/data --restart unless-stopped fibre-telegram-bot
```

### systemd

```bash
cp -r cumulo-celestia-infra/tools/fibre-telegram-bot ~/fibre-telegram-bot   # the unit expects this path
cd ~/fibre-telegram-bot
python3 -m venv .venv && .venv/bin/pip install .
cp .env.example .env   # then edit it
sed -e "s|__HOME__|$HOME|g" -e "s|__USER__|$USER|g" deploy/fibre-telegram-bot.service \
  | sudo tee /etc/systemd/system/fibre-telegram-bot.service
sudo systemctl daemon-reload && sudo systemctl enable --now fibre-telegram-bot
sudo journalctl -u fibre-telegram-bot -f
```

### Weekly post in a channel (optional)

Every Monday at 09:00 (`WEEKLY_DAY`, `WEEKLY_TIME`, `TZ`) the bot can post the card of the week
that just closed, one per network, to `REPORT_CHANNEL_ID`. It is off while that variable is empty.
Add the bot to the channel as an admin that can post, read the channel id from the log
(`bot_membership chat_id=-100...`), set it and your user id (`/id`) in `ADMIN_CHAT_IDS`, restart.
The last posted week is stored in `STATE_PATH`, so a restart never posts twice.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | (required) | Bot token from @BotFather. Never logged. |
| `FIBRE_API_BASE` | `https://fibre-api.cumulo.com.es` | API base URL |
| `DEFAULT_CHAIN` | `celestia` | Default network once Fibre is active on it |
| `FIBRE_CHAIN` | `mocha-5` | Fallback chain if `/v1/chains` fails at start |
| `FIBRE_CHAINS` | (all) | Limit to some chain keys |
| `CHAIN_PAGES` | cumulo.pro pages | General web page per chain (`key=url,key=url`) |
| `CHAIN_REPORT_PAGES` | cumulo.pro Mocha report | Report page per chain, takes `?period=` and `?validator=` |
| `DEFAULT_VALCONS`, `DEFAULT_VALIDATORS`, `DEFAULT_MONIKER` | Cumulo | Validator offered by default |
| `REPORT_CHANNEL_ID`, `WEEKLY_DAY`, `WEEKLY_TIME`, `WEEKLY_CHAINS`, `TZ` | off | Weekly channel post |
| `ADMIN_CHAT_IDS`, `STORAGE_CHAT_ID` | | Admin commands and inline images |
| `STATE_PATH` | `fibre_bot_state.json` | Last posted week per chain |
| `POLL_INTERVAL_SECONDS`, `STALE_AFTER_SECONDS` | `60`, `600` | Refresh interval, stale banner after |
| `LOG_LEVEL` | `INFO` | Logging level |

## Architecture

```
            every 60 s                          on demand (short TTL)
Fibre API ───────────► DataStore ◄──────────── /metrics?validator= /validator/{id}/status
 /v1/chains (10 min)   (one per chain,           /report /blobs/stats /publishers /search
 /summary              shared HTTP client
 /metrics (30 min)     and rate limiter)
                            │
                     ReportService ──► reports/*.py (pure functions: data in, HTML out)
                            │
                     aiogram router (handlers, keyboards, inline mode)
```

- Client-side token buckets stay under the API limits; the HTTP client and the limits are shared
  by every chain.
- If the monitor data is older than 10 minutes, reports show a stale banner.
- Report cards are rendered once per period and validator (about 70 ms), cached, and after the
  first upload resent by Telegram `file_id`.
- A per-user throttle (one action per second) protects against loops and Telegram flood limits.
- The bot keeps no user database: the last validator and network per chat live in memory only.

## Reading the figures

- The 2/3 status comes from the server flag computed exactly in integers, never from a rounded
  percentage.
- A signed ratio below 100% is not a failure: publishers stop collecting signatures at 2/3 of
  stake. Only blobs in the `not_ready` role are failures.
- Retention uses obligations (blobs the validator signed) and shows how many checks back it;
  fewer than 5 checks shows "not enough data".
- Periods longer than the tracked history say "Data since ...".

## Development

```bash
pip install -e ".[dev]"
pytest                 # fixtures only
pytest -m live         # also hits the real API
ruff check src tests && ruff format --check src tests && mypy
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Issues and pull requests are welcome.

## License

MIT. Data by the [Cumulo](https://cumulo.pro) Fibre Network Monitor.
