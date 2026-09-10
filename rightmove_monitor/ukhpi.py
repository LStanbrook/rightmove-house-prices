"""Download the UK House Price Index series for a region (City of Edinburgh).

The UK HPI incorporates Registers of Scotland transactions, so it is the right
sold-price benchmark for Edinburgh. HM Land Registry's "Price Paid" record-level
data covers England & Wales only and is deliberately not used here.

Data source: HM Land Registry linked-data API,
https://landregistry.data.gov.uk/data/ukhpi/region/<slug>/month/<YYYY-MM>.json
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

from .config import Config

BASE = "https://landregistry.data.gov.uk/data/ukhpi/region"

# Flat numeric fields on each monthly observation worth keeping.
_FIELDS = [
    "averagePrice",
    "averagePriceDetached",
    "averagePriceSemiDetached",
    "averagePriceTerraced",
    "averagePriceFlatMaisonette",
    "averagePriceFirstTimeBuyer",
    "averagePriceFormerOwnerOccupier",
    "averagePriceNewBuild",
    "averagePriceExistingProperty",
    "averagePriceCash",
    "averagePriceMortgage",
    "housePriceIndex",
    "percentageChange",
    "percentageAnnualChange",
    "salesVolume",
]


def _month_range(start: str, end: date) -> list[str]:
    y, m = (int(x) for x in start.split("-"))
    months = []
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def _fetch_month(session: requests.Session, slug: str, month: str) -> dict | None:
    url = f"{BASE}/{slug}/month/{month}.json"
    for attempt in range(1, 5):
        try:
            resp = session.get(url, timeout=30, headers={"Accept": "application/json"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            topic = resp.json()["result"]["primaryTopic"]
            # Months not yet published return primaryTopic as a bare URI string.
            if not isinstance(topic, dict):
                return None
            row = {"month": month}
            for f in _FIELDS:
                row[f] = topic.get(f)
            return row
        except (requests.RequestException, KeyError):
            time.sleep(1.5 * attempt)
    return None


def update_ukhpi(cfg: Config, *, full_refresh: bool = False) -> pd.DataFrame:
    """Fetch any months missing from the local cache and return the full series."""
    cfg.ensure_dirs()
    cache = cfg.raw_ukhpi_dir / f"ukhpi_{cfg.ukhpi.region_slug}.parquet"

    existing = pd.DataFrame()
    if cache.exists() and not full_refresh:
        existing = pd.read_parquet(cache)

    have = set(existing["month"]) if not existing.empty else set()
    # Always re-pull the trailing 3 months: UK HPI revises recent figures.
    today = date.today()
    wanted = _month_range(cfg.ukhpi.start_month, today)
    trailing = set(wanted[-3:])
    todo = [m for m in wanted if m not in have or m in trailing]

    if not todo:
        return existing.sort_values("month").reset_index(drop=True)

    session = requests.Session()
    fetched = []
    for month in todo:
        row = _fetch_month(session, cfg.ukhpi.region_slug, month)
        time.sleep(0.4)
        if row:
            fetched.append(row)

    new = pd.DataFrame(fetched)
    combined = (
        pd.concat([existing, new], ignore_index=True)
        .drop_duplicates(subset="month", keep="last")
        .sort_values("month")
        .reset_index(drop=True)
    )
    for col in _FIELDS:
        combined[col] = pd.to_numeric(combined[col], errors="coerce")
    combined["date"] = pd.to_datetime(combined["month"] + "-01")

    combined.to_parquet(cache, index=False)
    combined.to_csv(cache.with_suffix(".csv"), index=False)
    return combined
