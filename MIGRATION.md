# Migration notes

## Phase 0.1 — Persistent `DATA_DIR`

Runtime JSON state (`promo_data.json`, `deal_history.json`, `drops_history.json`,
`clicks.json`, `searches.json`, `drops_url_cache.json`) now lives under
`DATA_DIR`. Default is the repo directory, so local dev keeps working with no
changes.

**Railway is ephemeral by default** — every deploy wipes the working directory,
which has been silently dropping `clicks.json` and `searches.json` (the
60-day soft-launch KPIs).

### Required Railway change

1. In the Railway service, **add a Volume** and mount it at `/data`.
2. Set the env var `DATA_DIR=/data`.
3. Redeploy. The startup log now prints `📂 DATA_DIR = /data` and shows which
   data files were found on the volume. On first boot the files will not exist
   (⬜); they will be created by the next scrape and persist from then on.

`priority_intel.json` and `tactical_nukes.json` stay in the repo dir — they are
editor-curated and ship with code.
