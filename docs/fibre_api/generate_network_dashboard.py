#!/usr/bin/env python3
"""Generate fibre-network-dashboard.md: a badges only status board for the whole
Fibre network, built on Cumulo's public Fibre Network Monitor API.

Every badge is a shields.io dynamic JSON badge. The layout is driven by the live
/summary (or a saved copy via --summary-file): a badge is only emitted when its
JSONPath exists in that JSON, and list rows are created from the real list
lengths. Fields that appear in /summary later are picked up automatically in the
"Additional fields" section.

Standard library only. Python 3.8+.

Usage:
  python3 generate_network_dashboard.py > fibre-network-dashboard.md
  python3 generate_network_dashboard.py --summary-file summary.json -o fibre-network-dashboard.md
"""
import argparse
import json
import re
import sys
import urllib.request
from urllib.parse import quote, urlencode

DEFAULT_API = "https://fibre-api.cumulo.com.es"
DEFAULT_CHAIN = "mocha-5"
SHIELDS = "https://img.shields.io/badge/dynamic/json"
CACHE_SECONDS = 600
MAX_ROWS = 10
MAX_COLS = 5
MAX_SCALARS = 15
LABEL_KEYS = ("region", "dimension", "name", "label", "country", "provider",
              "moniker", "asn", "host", "key", "id")
PERIODS = ("24h", "7d", "30d")
# Top-level /summary keys that the curated sections already cover (or skip on purpose).
COVERED = {"meta", "readiness", "missing", "quorum_by_region", "liveness_coefficient",
           "concentration", "network_uptime_24h", "retention_24h", "agents", "blobs_24h",
           "escrow", "params", "payment_formula", "history_since",
           "method",          # long explanatory text, not a metric
           "recent_events"}   # dominated by pay_for_fibre, see section 8 instead


class Ctx:
    def __init__(self, api, chain, summary):
        self.api = api.rstrip("/")
        self.chain = chain
        self.summary = summary
        self.count = 0
        self.skipped = []

    def url(self, path):
        return f"{self.api}/v1/{self.chain}{path}"


# ---------------------------------------------------------------- helpers

def jsonpath(tokens):
    out = "$"
    for t in tokens:
        if isinstance(t, int):
            out += f"[{t}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
            out += f".{t}"
        else:
            out += "['" + t.replace("'", "\\'") + "']"
    return out


def resolve(data, tokens):
    """Return (found, value) for a token path inside data."""
    cur = data
    for t in tokens:
        if isinstance(t, int):
            if not isinstance(cur, list) or t >= len(cur):
                return False, None
        elif not isinstance(cur, dict) or t not in cur:
            return False, None
        cur = cur[t]
    return True, cur


def suffix_for(name):
    n = str(name)
    if n.endswith("_utia") or n == "amount":
        return " utia"
    if n.endswith("_bytes") or n in ("bytes", "blob_size"):
        return " B"
    if n.endswith("_ms"):
        return " ms"
    if n.endswith("_pct") or n == "pct":
        return "%"
    if n.endswith("_s"):
        return " s"
    return ""


def color_for(value):
    if isinstance(value, bool):
        return "informational"
    if isinstance(value, str):
        return "lightgrey"
    return "blue"


def badge(ctx, endpoint, tokens, label, suffix=None, color="blue"):
    """Markdown image for one dynamic JSON badge. tokens is a token path."""
    if suffix is None:
        last = next((t for t in reversed(tokens) if isinstance(t, str)), "")
        suffix = suffix_for(last)
    params = {"url": ctx.url(endpoint), "query": jsonpath(tokens), "label": label}
    if suffix:
        params["suffix"] = suffix
    params.update({"color": color, "style": "flat", "cacheSeconds": str(CACHE_SECONDS)})
    ctx.count += 1
    return f"![{label}]({SHIELDS}?{urlencode(params, safe='', quote_via=quote)})"


def sbadge(ctx, tokens, label, suffix=None):
    """Badge on /summary, emitted only when the path exists in the loaded summary."""
    found, value = resolve(ctx.summary, tokens)
    if not found or value is None or isinstance(value, (dict, list)):
        ctx.skipped.append(jsonpath(tokens))
        return None
    return badge(ctx, "/summary", tokens, label, suffix, color_for(value))


def humanize(key):
    return str(key).replace("_", " ").replace(" pct", " %").strip().capitalize()


def table(header, rows):
    rows = [r for r in rows if r and any(c for c in r[1:])]
    if not rows:
        return ""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        lines.append("| " + " | ".join(c if c else "n/a" for c in r) + " |")
    return "\n".join(lines) + "\n"


def scalar_table(ctx, base, keys=None, labels=None):
    """Two column Metric | Value table for scalar children of base (token path)."""
    found, obj = resolve(ctx.summary, base)
    if not found or not isinstance(obj, dict):
        ctx.skipped.append(jsonpath(base))
        return ""
    keys = keys or [k for k, v in obj.items() if not isinstance(v, (dict, list))]
    rows = []
    for k in keys[:MAX_SCALARS]:
        if isinstance(k, tuple):
            toks, name = list(base) + list(k), " ".join(map(str, k))
        else:
            toks, name = list(base) + [k], k
        label = (labels or {}).get(k) or humanize(name)
        b = sbadge(ctx, toks, label)
        if b:
            rows.append((label, b))
    return table(["Metric", "Value"], rows)


def list_table(ctx, base, columns, label_key=None, title_col="Item", max_rows=MAX_ROWS):
    """One row per list element. First column is a badge of the label field, so the
    row name always matches the live data even if the API reorders the list."""
    found, lst = resolve(ctx.summary, base)
    if not found or not isinstance(lst, list) or not lst or not isinstance(lst[0], dict):
        ctx.skipped.append(jsonpath(base))
        return ""
    if label_key is None:
        label_key = next((k for k in LABEL_KEYS if k in lst[0]), None)
    columns = [c for c in columns if c != label_key][:MAX_COLS]
    rows = []
    for i in range(min(len(lst), max_rows)):
        first = sbadge(ctx, list(base) + [i, label_key], title_col) if label_key else f"#{i}"
        cells = [sbadge(ctx, list(base) + [i, c], humanize(c)) for c in columns]
        rows.append([first or f"#{i}"] + cells)
    out = table([title_col] + [humanize(c) for c in columns], rows)
    if len(lst) > max_rows:
        out += f"\n_Showing the first {max_rows} of {len(lst)} entries (in API order)._\n"
    return out


def discover(ctx, key):
    """Generic rendering for /summary keys not covered by a curated section."""
    found, val = resolve(ctx.summary, [key])
    if not found or val is None:
        return ""
    if isinstance(val, dict):
        parts = [scalar_table(ctx, [key])]
        for sub, subval in val.items():
            if isinstance(subval, dict):
                t = scalar_table(ctx, [key, sub])
                if t:
                    parts.append(f"**{humanize(sub)}**\n\n{t}")
            elif isinstance(subval, list) and subval and isinstance(subval[0], dict):
                cols = [c for c, v in subval[0].items() if not isinstance(v, (dict, list))]
                parts.append(f"**{humanize(sub)}**\n\n" + list_table(ctx, [key, sub], cols))
        return "\n".join(p for p in parts if p)
    if isinstance(val, list) and val and isinstance(val[0], dict):
        cols = [c for c, v in val[0].items() if not isinstance(v, (dict, list))]
        return list_table(ctx, [key], cols)
    if not isinstance(val, list):
        return table(["Metric", "Value"], [(humanize(key), sbadge(ctx, [key], humanize(key)))])
    ctx.skipped.append(f"$.{key} (scalar list)")
    return ""


def slug(heading):
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


# ---------------------------------------------------------------- sections

def sec_readiness(ctx):
    out = ["Source: `/summary` (readiness, missing)\n",
           scalar_table(ctx, ["readiness"], keys=[
               "ready_meets_threshold", "registered_meets_threshold",
               "validators_needed_to_threshold", "ready_stake_pct", "registered_stake_pct",
               "threshold_pct", "bonded_validators", "registered_validators",
               "ready_validators", "responding_validators", "countries", "providers",
               ("status_counts", "online"), ("status_counts", "offline")],
               labels={"ready_meets_threshold": "Ready stake meets 2/3",
                       "registered_meets_threshold": "Registered stake meets 2/3",
                       "validators_needed_to_threshold": "Validators needed for 2/3",
                       "countries": "Countries", "providers": "Hosting providers"})]
    found, tiers = resolve(ctx.summary, ["readiness", "tiers"])
    if found and isinstance(tiers, dict):
        rows = []
        for tier in tiers:
            rows.append([f"`{tier}`",
                         sbadge(ctx, ["readiness", "tiers", tier, "validators"], "validators"),
                         sbadge(ctx, ["readiness", "tiers", tier, "stake_pct"], "stake"),
                         sbadge(ctx, ["readiness", "tiers", tier, "tokens"], "tokens", " utia")])
        out.append("**Validator tiers**\n\n" + table(["Tier", "Validators", "Stake", "Tokens"], rows))
    t = list_table(ctx, ["missing"], ["rank", "stake_pct", "registered_pct_if_added"],
                   label_key="moniker", title_col="Validator")
    if t:
        out.append("**Bonded validators without a Fibre host**\n\n" + t)
    out.append("> [!NOTE]\n> The 2/3 check is exact integer math on the server. "
               "Read the boolean badges, never compare a rounded percentage with 66.67.\n")
    return "\n".join(x for x in out if x)


def sec_quorum(ctx):
    a = list_table(ctx, ["quorum_by_region"],
                   ["reached", "quorum_rtt_ms", "validators_needed",
                    "ready_validators", "ready_stake_pct"], title_col="Region",
                   label_key="region")
    b = list_table(ctx, ["quorum_by_region"],
                   ["quorum_rtt_ms_excl_same_asn", "validators_needed_excl_same_asn",
                    "same_asn_hosts"], title_col="Region", label_key="region")
    out = ["Source: `/summary` (quorum_by_region)\n", a]
    if b:
        out.append("**Excluding hosts that share the probe's ASN**\n\n" + b)
    return "\n".join(x for x in out if x)


def sec_liveness(ctx):
    out = ["Source: `/summary` (liveness_coefficient, concentration)\n",
           "The liveness coefficient is the smallest number of validators, providers or "
           "countries whose loss would drop ready stake below 2/3.\n"]
    found, lc = resolve(ctx.summary, ["liveness_coefficient"])
    if found and isinstance(lc, dict):
        rows = []
        for dim in ("validators", "providers", "countries"):
            if dim in lc:
                rows.append([f"`{dim}`",
                             sbadge(ctx, ["liveness_coefficient", dim], "coefficient"),
                             sbadge(ctx, ["liveness_coefficient", "margin_pct_before_last", dim],
                                    "margin before last")])
        out.append(table(["Dimension", "Coefficient", "Margin before last"], rows))
        for dim, key in (("validators", "moniker"), ("providers", "label"), ("countries", "label")):
            t = list_table(ctx, ["liveness_coefficient", "sets", dim], ["ready_stake_pct"],
                           label_key=key, title_col=humanize(dim)[:-1])
            if t:
                out.append(f"**Smallest critical set: {dim}**\n\n" + t)
    cols = ["validators", "stake_pct", "ready_without_pct", "quorum_survives_loss"]
    for name, title in (("by_country", "Country"), ("by_provider", "Provider")):
        t = list_table(ctx, ["concentration", name], cols, label_key="label", title_col=title)
        if t:
            out.append(f"**Concentration {name.replace('_', ' ')}**\n\n" + t)
    return "\n".join(x for x in out if x)


def sec_uptime(ctx):
    out = ["Source: `/summary` (network_uptime_24h, retention_24h, agents)\n",
           "**Network uptime (24h)**\n\n" + scalar_table(
               ctx, ["network_uptime_24h"], keys=["pct", "ok", "total"],
               labels={"pct": "Online checks", "ok": "Checks OK", "total": "Checks total"}),
           "**Shard retention (24h)**\n\n" + scalar_table(
               ctx, ["retention_24h"], keys=[
                   "served_pct", "checks", "served", "unreachable",
                   ("obligations", "pct"), ("large", "pct"), ("verified", "pct"),
                   ("deleted_after_window", "pct"), ("availability", "pct"),
                   ("availability", "reconstructable"), ("availability", "blobs"),
                   ("availability", "host_failures"), "downloaded_bytes", "last_check_at"],
               labels={("obligations", "pct"): "Obligations served",
                       ("large", "pct"): "Large blobs served",
                       ("verified", "pct"): "Rows verified",
                       ("deleted_after_window", "pct"): "Deleted after retention window",
                       ("availability", "pct"): "Blobs reconstructable",
                       ("availability", "reconstructable"): "Reconstructable blobs",
                       ("availability", "blobs"): "Blobs sampled",
                       ("availability", "host_failures"): "Host failures"})]
    found, dl = resolve(ctx.summary, ["retention_24h", "download_ms_by_region"])
    if found and isinstance(dl, dict):
        rows = [[f"`{r}`", sbadge(ctx, ["retention_24h", "download_ms_by_region", r],
                                  "download", " ms")] for r in dl]
        out.append("**Shard download time by probe region**\n\n" + table(["Region", "Download"], rows))
    t = list_table(ctx, ["agents"], ["ok", "version", "last_cycle_at"],
                   label_key="region", title_col="Probe")
    if t:
        out.append("**Probe agents**\n\n" + t)
    return "\n".join(x for x in out if x)


def sec_market(ctx):
    pub = [("blobs", "Blobs"), ("publishers", "Publishers"), ("fees_utia", "Fees"),
           ("bytes", "Bytes"), ("price_utia_per_mib", "Price per MiB"),
           ("escrow_held_utia", "Escrow held"), ("escrow_accounts", "Escrow accounts")]
    rows = []
    for field, name in pub:
        sfx = " utia/MiB" if field == "price_utia_per_mib" else None
        rows.append([name] + [badge(ctx, f"/publishers?period={p}", ["totals", field],
                                    p, sfx) for p in PERIODS])
    out = ["Source: `/publishers?period=24h|7d|30d` (totals)\n",
           table(["Metric"] + list(PERIODS), rows)]
    stats = [(["settlements"], "Settlements"), (["distinct_commitments"], "Distinct commitments"),
             (["fees_utia"], "Fees"), (["bytes"], "Bytes"),
             (["fee_check", "checked"], "Fee check: checked"),
             (["fee_check", "matching"], "Fee check: matching")]
    rows = [[name] + [badge(ctx, f"/blobs/stats?period={p}", toks, p) for p in PERIODS]
            for toks, name in stats]
    out += ["Source: `/blobs/stats?period=24h|7d|30d`\n", table(["Metric"] + list(PERIODS), rows)]
    t = scalar_table(ctx, ["blobs_24h"], keys=["avg_endorsed_stake_pct", "namespaces",
                                               "payers", "timeouts"])
    if t:
        out += ["Source: `/summary` (blobs_24h)\n", t]
    out.append("> [!NOTE]\n> Tracking started on 2026-09-25, so 30 day figures cover fewer real "
               "days until the window fills. Amounts are in utia (1 TIA = 1,000,000 utia) and "
               "sizes in bytes.\n")
    return "\n".join(out)


def sec_params(ctx):
    out = ["Source: `/summary` (escrow, params, payment_formula)\n"]
    found, bal = resolve(ctx.summary, ["escrow", "balance"])
    if found and isinstance(bal, list):
        rows = [(f"Module balance #{i}", sbadge(ctx, ["escrow", "balance", i, "text"], "escrow"))
                for i in range(len(bal))]
        rows.append(("Fetched at", sbadge(ctx, ["escrow", "fetched_at"], "fetched")))
        out.append("**Escrow**\n\n" + table(["Metric", "Value"], rows))
    out.append("**Module parameters**\n\n" + scalar_table(ctx, ["params"]))
    out.append("**Payment formula**\n\n" + scalar_table(
        ctx, ["payment_formula"], keys=["fixed_gas", "gas_per_chunk", "chunk_bytes",
                                        "utia_per_gas", "max_blob_bytes", "app_version",
                                        "app_commit", "checked_blobs", "matching_blobs"]))
    out.append("> [!NOTE]\n> Fee per blob = (fixed_gas + gas_per_chunk x chunks) x utia_per_gas, "
               "where chunks = ceil(blob_size / chunk_bytes).\n")
    return "\n".join(x for x in out if x)


def sec_latest_blob(ctx):
    blob = [(["at"], "Settled at"), (["namespace_text"], "Namespace"), (["blob_size"], "Size"),
            (["signatures"], "Signatures"), (["set_size"], "Set size"),
            (["endorsed_stake_pct"], "Endorsed stake"), (["meets_threshold"], "Meets 2/3"),
            (["availability", "state"], "Availability"),
            (["availability", "coverage_pct"], "Coverage")]
    rows = [(n, badge(ctx, "/blobs?limit=1", ["blobs", 0] + t, n)) for t, n in blob]
    totals = [("blobs", "Blobs"), ("bytes", "Bytes"), ("fees_utia", "Fees"),
              ("timeouts", "Timeouts"), ("tracking_since", "Tracking since"),
              ("first_blob_at", "First blob")]
    rows2 = [(n, badge(ctx, "/blobs?limit=1", ["totals", f], n,
                       color="lightgrey" if f.endswith(("since", "_at")) else "blue"))
             for f, n in totals]
    return "\n".join(["Source: `/blobs?limit=1`\n", "**Latest blob**\n",
                      table(["Field", "Value"], rows), "**Totals since tracking started**\n",
                      table(["Metric", "Value"], rows2)])


def sec_event(ctx):
    ep = "/events?type=registered,left,status,host_reconfirmed&limit=1"
    rows = [("Type", badge(ctx, ep, ["events", 0, "type"], "type", color="informational")),
            ("Time", badge(ctx, ep, ["events", 0, "at"], "at", color="lightgrey")),
            ("Title", badge(ctx, ep, ["events", 0, "title"], "event", color="lightgrey"))]
    return ("Source: `" + ep + "`\n\n" + table(["Field", "Value"], rows) +
            "\n> [!NOTE]\n> Payment events (`pay_for_fibre`) are excluded on purpose (thousands "
            "per day). If no registry event happened yet, these badges show `no result`.\n")


def sec_prometheus(ctx, guide):
    metrics = [
        ("fibre_ready_meets_threshold", "Ready stake meets 2/3, for alerting"),
        ("fibre_validators_needed_to_threshold", "Validators still needed for 2/3"),
        ("fibre_quorum_rtt_seconds{region}", "Quorum round trip per probe region, over time"),
        ("fibre_liveness_coefficient{dimension}", "Liveness coefficient history"),
        ("fibre_network_online_ratio_24h", "Network online ratio, as a time series"),
        ("fibre_retention_obligations_served_ratio_24h", "Retention obligations served"),
        ("fibre_rows_verified_ratio_24h", "Rows verified against commitments"),
        ("fibre_blobs_reconstructable_24h", "Blobs reconstructable from shards"),
        ("fibre_probe_up{region}", "Probe agent health per region"),
    ]
    rows = [(f"`{m}`", d) for m, d in metrics]
    return ("Badges show the current value only. History, thresholds and alerts need "
            "the Prometheus metrics of the API. See the [usage guide](" + guide + ").\n\n" +
            table(["Metric", "Use"], rows))


def sec_build(ctx, guide):
    example_tokens = ["readiness", "ready_stake_pct"]
    example = badge(ctx, "/summary", example_tokens, "ready stake")
    ctx.count -= 1  # example is not part of the board
    url = example[example.index("(") + 1:-1]
    return f"""Every badge is a [shields.io dynamic JSON badge](https://shields.io/badges/dynamic-json-badge):

```
{SHIELDS}?url=<API URL>&query=<JSONPath>&label=<text>&suffix=<text>&color=<color>&style=flat&cacheSeconds={CACHE_SECONDS}
```

All parameters are fully URL encoded (`$` is `%24`, `[` is `%5B`, `?` is `%3F`, `=` is `%3D`, `&` is `%26`).

**Add a metric**

1. Find the field with `jq`: `curl -s {ctx.url('/summary')} | jq '.readiness.ready_stake_pct'`
2. Convert the jq path to JSONPath: `.readiness.ready_stake_pct` becomes `$.readiness.ready_stake_pct`, and `.quorum_by_region[0].reached` stays `$.quorum_by_region[0].reached`. Prefer array indexes over filters like `[?(@.region=='US')]`.
3. Build the URL with `urllib.parse.urlencode(params, safe="", quote_via=quote)`.

**Embed one badge**

```markdown
![ready stake]({url})
```

{example}

**Regenerate this page**

```bash
python3 generate_network_dashboard.py --api {ctx.api} --chain {ctx.chain} -o fibre-network-dashboard.md
```

**Troubleshooting**

| Badge shows | Meaning | Fix |
|---|---|---|
| `invalid query` | The JSONPath is malformed | Check brackets and that it starts with `$` |
| `no result` | The path does not exist (yet), or the list is empty | Check the field with `jq`, then regenerate the page |
| `inaccessible` | shields.io could not reach or parse the API | Open the API URL in a browser; check it returns JSON |
| `429` or `inaccessible` in bursts | Rate limit (120 requests per minute per IP) | Keep `cacheSeconds={CACHE_SECONDS}`; avoid more badges per endpoint |
| Old value | GitHub and shields.io cache images | Wait a few minutes or reload without cache |
| Large raw numbers | Badges cannot do arithmetic | Read the suffix: amounts in utia, sizes in bytes, durations in ms |

More endpoints and conventions in the [usage guide]({guide}).
"""


# ---------------------------------------------------------------- page

def header(ctx):
    items = [
        sbadge(ctx, ["meta", "network"], "network"),
        sbadge(ctx, ["meta", "height"], "height"),
        sbadge(ctx, ["payment_formula", "app_version"], "fibre app"),
        sbadge(ctx, ["blobs_24h", "blobs"], "blobs 24h"),
        sbadge(ctx, ["blobs_24h", "fees_utia"], "fees 24h"),
        sbadge(ctx, ["readiness", "ready_meets_threshold"], "ready 2/3"),
    ]
    return " ".join(i for i in items if i)


def build(ctx, title, guide):
    sections = [
        ("Network readiness", lambda: sec_readiness(ctx)),
        ("Quorum by region", lambda: sec_quorum(ctx)),
        ("Liveness and concentration", lambda: sec_liveness(ctx)),
        ("Network uptime and shard retention", lambda: sec_uptime(ctx)),
        ("Blobs and market", lambda: sec_market(ctx)),
        ("Escrow and module parameters", lambda: sec_params(ctx)),
        ("Latest blob and totals", lambda: sec_latest_blob(ctx)),
        ("Latest network event", lambda: sec_event(ctx)),
    ]
    extra = [k for k in ctx.summary if k not in COVERED]
    if extra:
        sections.append(("Additional fields",
                         lambda: "Source: `/summary` (fields discovered at generation time)\n\n" +
                         "\n".join(f"**{humanize(k)}**\n\n{discover(ctx, k)}" for k in extra)))
    sections += [("What needs Prometheus", lambda: sec_prometheus(ctx, guide)),
                 ("Build your own", lambda: sec_build(ctx, guide))]

    headings = [f"{i}. {name}" for i, (name, _) in enumerate(sections, 1)]
    body = []
    for h, (_, fn) in zip(headings, sections):
        body.append(f"## {h}\n\n{fn().strip()}\n")
    toc = "\n".join(f"{i}. [{name}](#{slug(h)})"
                    for i, ((name, _), h) in enumerate(zip(sections, headings), 1))
    chain = ctx.chain
    return f"""# {title}

{header(ctx)}

Live status of the Fibre data availability network on Celestia `{chain}`, built only from badges that read Cumulo's public Fibre Network Monitor API (`{ctx.api}`). No backend, no JavaScript, no tokens: copy any badge into your own README. See the [usage guide]({guide}) for endpoints and conventions.

> [!NOTE]
> Badges are cached by shields.io ({CACHE_SECONDS // 60} minutes) and by GitHub, so this page is a status board delayed by several minutes, not a real time feed. `null` in the API means no data, never zero; shields.io then shows `no result`.

## Contents

{toc}

{chr(10).join(body)}
---

Generated with generate_network_dashboard.py · Maintained by [Cumulo](https://cumulo.pro)
"""


def load_summary(args):
    if args.summary_file:
        try:
            with open(args.summary_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(f"error: cannot load summary file {args.summary_file}: {e}")
    url = f"{args.api.rstrip('/')}/v1/{args.chain}/summary"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fibre-dashboard-generator"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"error: cannot load {url}: {e}\n"
                 "Save it with curl and pass --summary-file instead.")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--api", default=DEFAULT_API)
    p.add_argument("--chain", default=DEFAULT_CHAIN)
    p.add_argument("--summary-file", help="use a saved /summary JSON instead of calling the API")
    p.add_argument("--guide-link", default="./fibre-api-guide.md")
    p.add_argument("--title", default="Fibre Network Dashboard")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    args = p.parse_args()

    summary = load_summary(args)
    if not isinstance(summary, dict):
        sys.exit("error: /summary is not a JSON object")
    ctx = Ctx(args.api, args.chain, summary)
    page = build(ctx, args.title, args.guide_link)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(page)
    else:
        sys.stdout.write(page)
    print(f"badges generated: {ctx.count}", file=sys.stderr)
    if ctx.skipped:
        print(f"skipped (not present or not scalar in /summary): {len(ctx.skipped)}", file=sys.stderr)
        for s in ctx.skipped:
            print(f"  {s}", file=sys.stderr)


if __name__ == "__main__":
    main()
