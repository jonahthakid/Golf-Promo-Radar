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

## Phase 3 — Klaviyo alerts

The server now exposes three alert endpoints and fires Klaviyo events at the
end of each scrape. All of it is **off** unless `KLAVIYO_PRIVATE_KEY` is set;
without that env var, the server logs a warning and skips Klaviyo calls
without affecting the rest of the scrape pipeline.

### Required Klaviyo setup

1. **Create a private API key** in Klaviyo: Account → Settings → API Keys →
   Create Private API Key. Give it scopes for Profiles, Lists, and Events
   (read + write). Copy the key.
2. **Create two lists** in Klaviyo: Lists & Segments → Create List.
   - `RADAR Watchlist` — the per-brand watch list. Profiles get a
     `watched_brands` array property that the flow filters on.
   - `RADAR Tactical Nukes` — separate list so the scarcity promise (only a
     few emails a month) is enforceable. Profiles signed up here get every
     `Tactical Nuke` event regardless of brand watching.
3. **Copy each list's ID** (the alphanumeric string in the URL, e.g. `Wp4xrK`).
4. **Set Railway env vars** (Service → Variables):
   - `KLAVIYO_PRIVATE_KEY=<the private key>`
   - `KLAVIYO_WATCHLIST_LIST_ID=<the watchlist list id>`
   - `KLAVIYO_NUKE_LIST_ID=<the nuke list id>`
   - Optional: `KLAVIYO_SYSTEM_EMAIL=alerts@radar.golf` (the from-address
     associated with events; defaults to `system@radar.golf`).
   - Optional knobs: `NUKE_MIN_PCT_OFF=40`, `NUKE_MAX_PER_24H=3`.

### Required Klaviyo flow setup

The server fires Klaviyo **events**; the actual email logic is wired in
Klaviyo's flow builder on top of those events.

- **Brand Deal Posted** event → flow "Watchlist Match" → filter:
  *profile property `watched_brands` contains event property `brand`* →
  send email using event's `brand`, `promo`, `code`, `url`, `pct_off`.
  Add Klaviyo's standard smart-sending throttle (e.g. once-per-24h-per-recipient).
- **Tactical Nuke** event → flow "Nuke Broadcast" → trigger filter:
  *profile is on `RADAR Tactical Nukes` list* → send immediately. The server
  enforces a cap of `NUKE_MAX_PER_24H` (default 3) before firing, so no
  flow-side throttle is required.

### Verification

After deploying with keys set:

1. Hit `POST /api/watch` with `{"email":"you@example.com","brands":["Greyson Clothiers"]}`.
   Check Klaviyo → Profiles → search the email. The profile should exist with
   `watched_brands: ["Greyson Clothiers"]` and be on the `RADAR Watchlist` list.
2. Hit `POST /api/watch-nuke` with `{"email":"you@example.com"}`. Confirm the
   profile is now also on `RADAR Tactical Nukes`.
3. Watch the scraper logs for `📣 Brand Deal Posted: X/Y delivered to Klaviyo`
   on the next cycle. Klaviyo → Analytics → Metrics → "Brand Deal Posted" should
   show those events landing.
4. `GET /api/digest` (admin auth) returns last-7-days material for assembling
   a weekly campaign by hand.
