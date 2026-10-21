# API discovery note (2026-10-04)

Recorded against `https://fibre-api.cumulo.com.es` (chain `mocha-5`, aggregator version 1.24.3).
Fixtures live in `fixtures/`. Large arrays were trimmed to a few representative entries; values are
real. Exceptions, marked synthetic: the last point of `history_7d.json` (uptime and rtt set to `null`
to exercise the n/a path) and the `ready_meets` values of `timeseries_7d.json` (`null`, as recorded).

## Previously unverified structures, now confirmed

- `summary.readiness`: `bonded_validators, bonded_tokens, registered_validators, registered_tokens,
  ready_validators, ready_tokens, responding_validators, registered_stake_pct, ready_stake_pct,
  threshold_pct, registered_meets_threshold, ready_meets_threshold, validators_needed_to_threshold,
  tiers{ready, registered_not_ready, no_host: {validators, stake_pct, tokens}}, countries, providers,
  status_counts{online, offline, ...}`.
- `summary.liveness_coefficient`: `validators, providers, countries` (integers),
  `margin_pct_before_last{validators, providers, countries}`, `sets{validators[{valcons, moniker,
  ready_stake_pct}], providers[{key, label, ready_stake_pct}], countries[{key, label, ready_stake_pct}]}`.
- `summary.concentration.by_country[]` / `by_provider[]`: `key, label, validators, stake_pct,
  ready_stake_pct, ready_without_pct, quorum_survives_loss`.
- `summary.missing[]`: bonded validators without a host: `valcons, moniker, operator_address, identity,
  rank, stake_pct, registered_pct_if_added`.
- `summary.params`: `withdrawal_delay_s, payment_promise_timeout_s, payment_promise_height_window,
  shard_retention_s, full_stake_storage_budget_bytes, fetched_at`.
- `summary.escrow`: `balance[{denom, amount (string), text}], fetched_at`.
- Extra summary keys: `blobs_24h{blobs, bytes, fees_utia, timeouts, payers, namespaces,
  avg_endorsed_stake_pct}`, `network_uptime_24h{pct, ok, total}`, `retention_24h{checks, served,
  served_pct, obligations{served, checked, pct}, large, verified, availability, ...}`, `agents[]`.
- `/validator/{valcons}/history` points: `{t, uptime (pct or null), checks [ok, total], rtt_ms,
  rtt_by_region (null on older points, else {REGION: {p50_ms, p95_ms, samples}})}`.
- `/timeseries?range=7d`: `{chain, range, step_s, tracking_since, regions, points[]}`; point keys:
  `t, bonded, registered, ready, registered_pct, ready_pct, ready_meets (null on recorded points),
  identity_fail, unreachable, partial, countries, providers, liveness, liveness_providers,
  liveness_countries, rtt_by_region, ready_pct_by_region, quorum_rtt_by_region`.
- `/validators`: `{meta, validators[]}` with rich per-validator records (host, geo, status, regions,
  identity_check, uptime{h24,d7,d30}, endorsements, retention, flags, ...). Not used by the bot.
- `/validator/{id}/status` body: `reason, ready, status, registered, regions{R: {status, rtt_ms}},
  typical_rtt_ms, uptime, signed_pct, serve_pct, keeps_pct, warnings[], identity_expires_at, checked_at`.
- `/search?q=` also returns `index, kinds, totals, blobs, events` next to `validators` and `day`.
- `period=all` is accepted by `/blobs/stats` (returns `period: "all", since: null`).

## Event types

Seen: `pay_for_fibre`, `registered`, `host_reconfirmed`, `left`, `status`, `escrow_deposit`.

There is no separate "went offline" or "identity failure" type: both are `status` events with
`from`/`to` in `online, offline, partial, identity_fail` and an optional `reason`
(for example `signature_invalid`). Status events have no `height`/`txhash` (`source: "probe"`).

The list above comes from the recorded fixtures, not from a full scan of every closed
`events-*.ndjson.gz` export. Run `scripts/discover_event_types.sh` to count the types in the
exports yourself.

## Other notes

- Prometheus also exposes `fibre_validator_signed_blobs`, `fibre_validator_signable_blobs`,
  `fibre_validator_regions_online`, `fibre_validator_stake_ratio`, `fibre_validator_storage_budget_bytes`,
  `fibre_validator_last_ready_timestamp_seconds` and network `fibre_network_online_ratio_24h`.
- `fibre_validator_deleted_after_window_ratio` has no `24h` series (absent means no data).
- `/validators` returned 21 validators in the recorded response while 83 are bonded: it may be paginated
  or filtered by default. Unverified, not used yet.
