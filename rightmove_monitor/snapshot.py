"""Run one Rightmove snapshot and persist it."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from . import rightmove
from .config import Config
from .history import update_history

SNAPSHOT_INDEX = "snapshot_index.csv"


def run_snapshot(cfg: Config, *, update_hist: bool = True) -> dict:
    cfg.ensure_dirs()
    records, meta = rightmove.snapshot_listings(cfg.rightmove)
    df = pd.DataFrame(records)

    day = meta["snapshot_date"]
    slug = cfg.rightmove.location_slug
    out_path = cfg.raw_rightmove_dir / f"{slug}_{day}.parquet"
    df.to_parquet(out_path, index=False)
    meta["file"] = str(out_path)

    # Human-readable run log.
    (cfg.raw_rightmove_dir / f"{slug}_{day}.meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    summary = _summarise(df, meta)
    _append_index(cfg, summary)

    if update_hist:
        hist = update_history(df, cfg)
        summary["history_active"] = int((hist["status"] == "active").sum())
        summary["history_total"] = int(len(hist))

    for w in meta["warnings"]:
        print(f"  ! {w}")
    print(
        f"  {day}: collected {summary['n_listings']} listings "
        f"(Rightmove reported {summary['reported_total']}), "
        f"median asking £{summary['median_price']:,.0f}"
    )
    return summary


def _summarise(df: pd.DataFrame, meta: dict) -> dict:
    price = df["price"].dropna() if "price" in df else pd.Series(dtype=float)
    reduced = 0
    if "added_or_reduced_kind" in df:
        reduced = int((df["added_or_reduced_kind"] == "Reduced").sum())
    return {
        "snapshot_date": meta["snapshot_date"],
        "captured_at": meta["captured_at"],
        "n_listings": int(len(df)),
        "reported_total": int(meta["reported_total"]),
        "median_price": float(price.median()) if not price.empty else float("nan"),
        "mean_price": float(price.mean()) if not price.empty else float("nan"),
        "p25_price": float(price.quantile(0.25)) if not price.empty else float("nan"),
        "p75_price": float(price.quantile(0.75)) if not price.empty else float("nan"),
        "n_reduced_listings": reduced,
        "n_new_7d": _count_recent(df, 7),
        "n_new_30d": _count_recent(df, 30),
        "n_warnings": len(meta["warnings"]),
    }


def _count_recent(df: pd.DataFrame, days: int) -> int:
    if "first_visible_date" not in df or df.empty:
        return 0
    fvd = pd.to_datetime(df["first_visible_date"], errors="coerce", utc=True)
    snap = pd.Timestamp(df["snapshot_date"].iloc[0], tz="UTC")
    return int((snap - fvd <= pd.Timedelta(days=days)).sum())


def _append_index(cfg: Config, summary: dict) -> None:
    path = cfg.processed_dir / SNAPSHOT_INDEX
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    existing = existing[existing.get("snapshot_date") != summary["snapshot_date"]] if not existing.empty else existing
    out = pd.concat([existing, pd.DataFrame([summary])], ignore_index=True)
    out = out.sort_values("snapshot_date").reset_index(drop=True)
    out.to_csv(path, index=False)
