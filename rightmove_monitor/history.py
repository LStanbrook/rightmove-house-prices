"""Maintain a listing-level history across snapshots.

One row per Rightmove listing id, updated in place on every snapshot:

* first / last date the listing was seen
* first / last / min / max asking price and the full price path
* number of price changes
* status: ``active`` while it keeps appearing, ``gone`` once it drops out
* observed days on market

The price path is what lets you measure discounting behaviour and true time on
market rather than trusting Rightmove's own "Added on" label, which resets.
"""
from __future__ import annotations

import json

import pandas as pd

from .config import Config

HISTORY_NAME = "listings_history.parquet"


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "id", "first_seen_date", "last_seen_date", "status", "gone_date",
            "first_price", "last_price", "min_price", "max_price",
            "n_price_changes", "price_path", "days_on_market_observed",
            "postcode", "outcode", "bedrooms", "property_sub_type",
            "latitude", "longitude", "branch_name", "property_url",
        ]
    )


def update_history(snapshot_df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Fold one snapshot into the running history table and persist it."""
    cfg.ensure_dirs()
    path = cfg.processed_dir / HISTORY_NAME
    hist = pd.read_parquet(path) if path.exists() else _empty()
    hist = hist.set_index("id", drop=False) if not hist.empty else hist

    if snapshot_df.empty:
        return hist.reset_index(drop=True)

    snap_day = str(snapshot_df["snapshot_date"].iloc[0])
    seen_ids = set(snapshot_df["id"].astype(str))
    rows = {r["id"]: r for r in hist.to_dict("records")} if not hist.empty else {}

    for rec in snapshot_df.to_dict("records"):
        pid = str(rec["id"])
        price = rec.get("price")
        price = int(price) if pd.notna(price) else None

        if pid not in rows:
            path_list = [[snap_day, price]] if price is not None else []
            rows[pid] = {
                "id": pid,
                "first_seen_date": snap_day,
                "last_seen_date": snap_day,
                "status": "active",
                "gone_date": None,
                "first_price": price,
                "last_price": price,
                "min_price": price,
                "max_price": price,
                "n_price_changes": 0,
                "price_path": json.dumps(path_list),
                "days_on_market_observed": 0,
                "postcode": rec.get("postcode"),
                "outcode": rec.get("outcode"),
                "bedrooms": rec.get("bedrooms"),
                "property_sub_type": rec.get("property_sub_type"),
                "latitude": rec.get("latitude"),
                "longitude": rec.get("longitude"),
                "branch_name": rec.get("branch_name"),
                "property_url": rec.get("property_url"),
            }
            continue

        row = rows[pid]
        row["status"] = "active"
        row["gone_date"] = None
        row["last_seen_date"] = snap_day
        row["days_on_market_observed"] = (
            pd.Timestamp(snap_day) - pd.Timestamp(row["first_seen_date"])
        ).days
        if price is not None:
            try:
                path_list = json.loads(row.get("price_path") or "[]")
            except (TypeError, json.JSONDecodeError):
                path_list = []
            prev = path_list[-1][1] if path_list else None
            if prev != price:
                path_list.append([snap_day, price])
                row["price_path"] = json.dumps(path_list)
                row["n_price_changes"] = int(row.get("n_price_changes") or 0) + (
                    1 if prev is not None else 0
                )
            row["last_price"] = price
            row["min_price"] = min(x for x in (row.get("min_price"), price) if x is not None)
            row["max_price"] = max(x for x in (row.get("max_price"), price) if x is not None)
            if row.get("first_price") is None:
                row["first_price"] = price

    # Listings that were active but are missing from this snapshot have left the
    # market (sold, withdrawn, or let). Mark them once.
    for pid, row in rows.items():
        if pid not in seen_ids and row.get("status") == "active":
            row["status"] = "gone"
            row["gone_date"] = snap_day

    out = pd.DataFrame(list(rows.values()))
    out.to_parquet(path, index=False)
    return out
