# Celestia Fibre · My validator (Grafana dashboard)

Your Celestia Fibre server as the [Cumulo Fibre Network Monitor](https://cumulo.pro/services/celestia/fibre) sees it,
checked every minute from four regions (US, EU, CA, AS), in your own Grafana. The data comes from the free public
[Fibre API](https://cumulo.pro/services/celestia/fibre-api): no API key, nothing to run on your side.

Two versions of the same dashboard:

| | `fibre-validator-prometheus.json` (recommended) | `fibre-validator-infinity.json` |
|---|---|---|
| You need | The Prometheus you already run, plus one scrape job | The Infinity datasource plugin |
| History | From the moment your Prometheus starts scraping | 7 and 30 days at once (from the API) |
| Alerts | Native Prometheus / Grafana alerting on the metrics | No |
| Extra | | Latest blobs signed / not collected, rank and peer median |

## Prometheus version (recommended)

### 1. Scrape the Fibre API

Add one job per network to the `scrape_configs:` list of your `prometheus.yml` (only the network you validate on is
needed), with your validator's **valcons** (`celestiavalcons1...`). Indent the jobs like the ones already in your file
(YAML does not allow mixing), check the file with `promtool check config prometheus.yml`, then reload Prometheus:

```yaml
scrape_configs:
  - job_name: fibre-celestia
    scheme: https
    metrics_path: /v1/celestia/metrics
    params:
      validator: ["celestiavalcons1..."]   # yours; several separated by commas
    scrape_interval: 60s
    static_configs:
      - targets: ["fibre-api.cumulo.com.es"]

  - job_name: fibre-mocha-5
    scheme: https
    metrics_path: /v1/mocha-5/metrics
    params:
      validator: ["celestiavalcons1..."]
    scrape_interval: 60s
    static_configs:
      - targets: ["fibre-api.cumulo.com.es"]
```

`?validator=` keeps the scrape small: your validator's series plus the network figures. The API recomputes once a
minute, so 60 s is the useful interval.

### 2. Import the dashboard

Grafana › Dashboards › New › Import › upload `fibre-validator-prometheus.json`, and pick that Prometheus.
The **Network** and **Validator** dropdowns at the top list what your Prometheus scrapes (by moniker).

### 3. Alerts (optional)

Every panel is a plain metric, so alerting works as usual. Two useful rules:

```yaml
- alert: FibreServerNotReady
  expr: fibre_validator_ready == 0
  for: 5m
- alert: FibreCertificateExpiring
  expr: (fibre_validator_cert_expiry_timestamp_seconds - time()) / 86400 < 14
```

Without Prometheus, an uptime monitor pointed at
`https://fibre-api.cumulo.com.es/v1/<chain>/validator/<valcons>/status` alerts too (200 when ready, 503 otherwise).

## Infinity version

1. Install the [Infinity](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/) plugin (2.7.1 or newer;
   it comes with Grafana Cloud) and add an Infinity datasource with the defaults (no URL or authentication: the panels
   call `https://fibre-api.cumulo.com.es` by absolute URL; if you restrict allowed hosts, add that one).
2. Import `fibre-validator-infinity.json`, pick that datasource and type your validator's **valcons**.
3. One refresh is about 12 API requests; the API allows 120 per minute per address, so keep the refresh at 5 minutes or more.

## Good to know

- Your valcons: open your validator on [the Fibre monitor](https://cumulo.pro/services/celestia/fibre) (Validators tab),
  or `https://fibre-api.cumulo.com.es/v1/celestia/search?q=<your moniker>`.
- Less than 100% signed is normal: publishers stop collecting signatures once 2/3 of the stake has signed.
- Empty panels mean no blob or check in that window yet (never a zero made up).
- Every metric is documented in the API: `https://fibre-api.cumulo.com.es/v1/celestia/metrics` (`# HELP` lines).

MIT licence. Data and dashboard by [Cumulo](https://cumulo.pro).
