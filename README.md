# Edinburgh housing market monitor

Track the **number of homes for sale and their asking prices on Rightmove over
time**, combine that with the official **UK House Price Index** for the City of
Edinburgh, and produce a rolling house-price forecast.

The Rightmove series is a *leading* indicator (asking prices, inventory,
discounting, time on market react first); the UK HPI is the *lagging* ground
truth (actual sold prices, ~2 months behind). Watching the gap between them is
the whole point.

---

## Why UK HPI and not "Land Registry Price Paid"

HM Land Registry's transaction-level **Price Paid Data covers England & Wales
only**. Scotland's sold prices sit with **Registers of Scotland**, which does not
publish a free record-level equivalent. The **UK House Price Index** *does* cover
the City of Edinburgh (monthly average prices, index, and sales volumes, from
2004) and already incorporates Registers of Scotland data — so that is the sold-
price benchmark used here. Record-level Scottish sold data would mean a paid
Registers of Scotland feed or scraping ESPC; both are noted in the roadmap.

---

## Setup

```powershell
cd "c:\Users\louis\OneDrive\Documents\OneDrive\Data Science Projects\DS Projects\rightmove house prices"
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Usage

```powershell
.venv\Scripts\python -m rightmove_monitor.cli snapshot    # scrape today's for-sale listings
.venv\Scripts\python -m rightmove_monitor.cli ukhpi       # download / refresh UK HPI
.venv\Scripts\python -m rightmove_monitor.cli analyse     # rebuild the tidy time series
.venv\Scripts\python -m rightmove_monitor.cli forecast    # 12-month UK HPI forecast + chart
.venv\Scripts\python -m rightmove_monitor.cli dashboard   # rebuild data\processed\dashboard.html
.venv\Scripts\python -m rightmove_monitor.cli all         # all of the above, in order
```

### Dashboard

`cli.py all` (and `cli.py dashboard`) writes a self-contained
`data/processed/dashboard.html` — open it in any browser. It shows the current
KPIs, the UK HPI series with the 12-month forecast, asking-price and inventory
trends (they fill in as daily snapshots accrue), a **by-area** section, and
today's stock broken down by property type, bedroom count, price and price
qualifier. `dashboard.fragment.html` is the same page without the outer HTML
skeleton, ready to publish as a Claude Artifact.

The **by-area** section answers "which parts of Edinburgh are cheaper / dearer
and how are they moving" with one interactive map (Leaflet, free Esri basemap
tiles, no API key) instead of separate price/trend charts:

- Each of the 17 core postcode districts is a marker at that district's real
  centroid (median lat/lon of its own listings).
- **Marker fill** = median asking price on a single-hue sequential scale
  (light → dark = cheap → dear), so price *level* is legible straight off the
  map.
- **Marker ring colour** = trend since the first snapshot (green = up, red =
  down, grey = flat) — price level and trend sit on the *same* map rather than
  two separate widgets.
- Every marker is permanently labelled with its district code and price (never
  colour alone), and clicking one opens a popup with the full stats plus a
  sparkline of its price history.
- *Area league table* below the map gives the same numbers in an accessible,
  sortable-by-eye table: listing count, median asking, % vs the whole-city
  median, % currently reduced, median days listed, and **Δ since the first
  snapshot** once ≥2 snapshots exist.

District → neighbourhood names live in [rightmove_monitor/areas.py](rightmove_monitor/areas.py)
(e.g. EH3 → "New Town, West End & Stockbridge"). The underlying per-area, per-day
series is `data/processed/market_timeseries_by_segment.csv`
(`segment_kind == "outcode"`).

Change the area or search options in [`config.yaml`](config.yaml). Find another
Rightmove location identifier with:

```powershell
.venv\Scripts\python -m rightmove_monitor.cli resolve "Leith"
```

### Finding a specific style: open-plan industrial lofts / warehouse conversions

[`rightmove_monitor/loft_finder.py`](rightmove_monitor/loft_finder.py) scans every
current listing's free-text `summary` and `key_features` (Rightmove's own
`keywords` search parameter doesn't actually filter results, so this is done
client-side) against a weighted keyword ladder:

- **strong** (3 pts) — terms basically unique to a real conversion: warehouse,
  foundry, printworks, bonded warehouse, whisky bond, mill/engine-works
  conversion, "loft apartment"/"loft-style"/"loft living", "warehouse loft"...
- **medium** (2 pts) — supporting decor cues: exposed brick, mezzanine,
  double-height, cast-iron columns, steel beams, industrial-style, exposed
  ductwork/pipework, exposed or polished concrete, concrete ceilings — the
  raw-brick-and-timber look and the sleeker exposed-concrete-and-services look
  both count.
- **light** (1 pt) — generic cues that also show up on ordinary flats: open
  plan, exposed beams, high ceilings, spiral staircases, and the bare word
  "loft" — deliberately *not* weighted as strong, since in Scottish listings
  "loft" usually just means an attic conversion in an ordinary house.

Listings scoring ≥3 show up as a **list** (not a card grid — denser and easier
to scan a lot of listings) in the **"Open-plan industrial lofts & warehouse
conversions"** section of the dashboard, ranked highest-score-first, with the
matched keywords, price and a link straight to the listing, plus an **"Open all
in new tabs"** button that opens every current match at once (some browsers
throttle multiple tabs from one click — allow pop-ups for the page if a few
don't open). Also runs from the CLI:

```powershell
.venv\Scripts\python -m rightmove_monitor.cli lofts --min-score 3 --limit 40
```

`summary`/`key_features` are only captured in snapshots from the date this was
added — it re-scans fresh every day the monitor runs, so this is a live watch,
not a one-off search. Edinburgh's own industrial-conversion stock clusters
mostly in **Leith** (EH6 — converted whisky bonds/warehouses around the Shore),
**Tanfield/Canonmills** (EH3 — the former print works), **Powderhall** (EH7 —
the old foundry site) and **Fountainbridge** (EH3/EH11 — former brewery land,
mostly new-build in an industrial idiom rather than true conversions) — worth
watching those areas directly as well as the scored list.

## Run it daily — in the cloud (GitHub Actions)

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs the full cycle
on GitHub's servers every morning (~06:20 UTC), commits the day's snapshot back
to the repo, and republishes the dashboard to **GitHub Pages**:

<https://lstanbrook.github.io/rightmove-house-prices/>

No laptop required. Trigger a run by hand from the repo's **Actions** tab
(*Daily Edinburgh housing snapshot → Run workflow*), or:

```bash
gh workflow run daily.yml
```

To pull the accumulated history down locally: `git pull`.

*Caveat:* Rightmove may rate-limit or block requests from datacenter IP ranges.
If a run fails on the scrape step, the local Task Scheduler route below is the
fallback.

## Run it daily — locally (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 07:15
Start-ScheduledTask -TaskName "RightmoveEdinburghMonitor"   # test run now
```

This calls [`scripts/run_monitor.cmd`](scripts/run_monitor.cmd), which runs the
full cycle and logs to `data/logs/`. The task only fires while the PC is on and
you are logged in; if it misses a day it catches up at the next opportunity
(`-StartWhenAvailable`). A missed day just means a gap in the daily series.

Run **one** of the two, not both — otherwise the local `data/` diverges from the
repo. The cloud workflow is the default; disable the local task with
`Disable-ScheduledTask -TaskName RightmoveEdinburghMonitor`.

---

## How the snapshot stays complete

Rightmove only pages through ~1000 results per search, but Edinburgh has ~1,800+
listings. `rightmove.py` splits the search into **price bands** along Rightmove's
own price ladder, recursively bisecting any band that still exceeds the cap, then
de-duplicates by listing id. If a single narrow band ever holds >1000 listings
you get a warning and the first ~1000 of that band.

Be a good citizen: this is for personal research. Keep `request_delay_seconds`
polite (default 1.5s), don't hammer it more than once a day, and don't
redistribute the raw scraped data — Rightmove's terms restrict bulk reuse.

---

## Data produced

```
data/
  raw/
    rightmove/edinburgh_YYYY-MM-DD.parquet   one row per live listing that day
    rightmove/edinburgh_YYYY-MM-DD.meta.json run log (reported total, warnings)
    ukhpi/ukhpi_city-of-edinburgh.parquet    monthly UK HPI series (cached)
  processed/
    snapshot_index.csv                       one row per snapshot run
    listings_history.parquet                 one row per listing, price path + time on market
    market_timeseries.csv / .parquet         daily market aggregates
    market_timeseries_by_segment.csv         daily aggregates by type / beds / outcode
    forecast_ukhpi.csv                       actual + 12-month forecast with intervals
    forecast_ukhpi.png                       chart
    dashboard.html                           self-contained visual dashboard
    dashboard.fragment.html                  same page, no skeleton (for Artifacts)
```

### `market_timeseries` columns

| column | meaning |
|---|---|
| `n_listings` | live for-sale listings that day (asking-price > 0) |
| `median_price`, `mean_price`, `p25_price`, `p75_price` | asking-price distribution |
| `median_price_per_sqft` | where floor area is published |
| `median_days_on_market` | from Rightmove's first-visible date |
| `pct_reduced` | share of listings currently showing a price cut |
| `n_new_7d`, `n_new_30d` | listings first seen within 7 / 30 days |

### `listings_history` columns

Per listing: `first_seen_date`, `last_seen_date`, `status` (`active` / `gone`),
`first_price`, `last_price`, `min_price`, `max_price`, `n_price_changes`,
`price_path` (JSON `[[date, price], ...]`), `days_on_market_observed`. This is the
table for studying discounting and true time on market once a few weeks of
snapshots have accrued.

---

## Roadmap

- **Leading-indicator forecast.** Once `market_timeseries` spans ~6 months, feed
  lagged asking-price momentum, inventory, and `pct_reduced` into the SARIMAX as
  exogenous regressors (stub + notes in [`forecast.py`](rightmove_monitor/forecast.py)).
- **ESPC.** Add the Edinburgh Solicitors Property Centre as a second listings
  source — it carries stock Rightmove misses and publishes sold prices.
- **Sub-area detail.** Sweep per-outcode (EH1–EH17) for cleaner neighbourhood
  series instead of relying on address parsing.
- **"Offers over" handling.** ~67% of the first Edinburgh snapshot was priced
  *Offers Over* and ~22% *Fixed Price* — so the asking figure is a floor, not a
  target, and sold prices run above it in a firm market. `price_qualifier` is
  stored per listing; model the typical asking→sold uplift per qualifier and
  outcode against the UK HPI to turn asking prices into a sold-price nowcast.
- **Floor area / £ per sqft.** `size_sqft` is empty in list-view data (0 of
  1,867 on the first run). Populating it means one extra request per listing to
  the detail page — worth adding as a weekly (not daily) enrichment pass.
