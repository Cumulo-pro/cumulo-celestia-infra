#!/usr/bin/env bash
# Count event types across every closed daily export. Needs curl, jq and gunzip.
set -euo pipefail
API="${FIBRE_API_BASE:-https://fibre-api.cumulo.com.es}"
CHAIN="${FIBRE_CHAIN:-mocha-5}"

curl -fsS "$API/v1/$CHAIN/exports" \
  | jq -r '.files[] | select(.kind=="events" and .closed) | .url' \
  | while read -r url; do curl -fsS "$url" | gunzip; done \
  | jq -r '.type + (if .type=="status" then " (" + (.from // "?") + " -> " + (.to // "?") + ")" else "" end)' \
  | sort | uniq -c | sort -rn
