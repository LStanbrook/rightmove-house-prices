"""Build tidy time series from all accumulated Rightmove snapshots."""
from __future__ import annotations

import pandas as pd

from .config import Config

CORE_NAME = "market_timeseries.csv"
SEGMENT_NAME = "market_timeseries_by_segment.csv"


def load_all_snapshots(cfg: Config) -> pd.DataFrame:
    files = sorted(cfg.raw_rightmove_dir.glob(f"{cfg.rightmove.location_slug}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df["first_visible_date"] = pd.to_datetime(
        df["first_visible_date"], errors="coerce", utc=True
    )
    # Exclude sponsored cards bled in from neighbouring areas and pure land/
    # commercial entries from the price statistics.
    df = df[df["price"].notna() & (df["price"] > 0)]
    return df


def _days_on_market(group: pd.DataFrame) -> pd.Series:
    snap = group["snapshot_date"].iloc[0]
    fvd = group["first_visible_date"].dt.tz_convert(None)
    return (snap - fvd).dt.days


def build_core(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for snap, g in df.groupby("snapshot_date"):
        dom = _days_on_market(g).dropna()
        rows.append(
            {
                "snapshot_date": snap.date().isoformat(),
                "n_listings": len(g),
                "median_price": g["price"].median(),
                "mean_price": g["price"].mean(),
                "p25_price": g["price"].quantile(0.25),
                "p75_price": g["price"].quantile(0.75),
                "median_price_per_sqft": g["price_per_sqft"].median(),
                "median_days_on_market": dom.median() if not dom.empty else None,
                "pct_reduced": 100 * (g["added_or_reduced_kind"] == "Reduced").mean(),
                "n_new_7d": int((_days_on_market(g) <= 7).sum()),
                "n_new_30d": int((_days_on_market(g) <= 30).sum()),
                "median_beds": g["bedrooms"].median(),
                "n_development": int(g["is_development"].sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("snapshot_date").reset_index(drop=True)


def build_segments(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    specs = [
        ("property_sub_type", "property_sub_type"),
        ("bedrooms", "bedrooms"),
        ("outcode", "outcode"),
    ]
    for snap, g in df.groupby("snapshot_date"):
        for kind, col in specs:
            for seg, gg in g.groupby(col):
                if pd.isna(seg):
                    continue
                out.append(
                    {
                        "snapshot_date": snap.date().isoformat(),
                        "segment_kind": kind,
                        "segment": str(seg),
                        "n_listings": len(gg),
                        "median_price": gg["price"].median(),
                        "median_price_per_sqft": gg["price_per_sqft"].median(),
                        "pct_reduced": 100 * (gg["added_or_reduced_kind"] == "Reduced").mean(),
                    }
                )
    return pd.DataFrame(out).sort_values(
        ["snapshot_date", "segment_kind", "segment"]
    ).reset_index(drop=True)


def run_analysis(cfg: Config) -> dict:
    cfg.ensure_dirs()
    df = load_all_snapshots(cfg)
    if df.empty:
        print("  no snapshots yet - run `snapshot` first")
        return {"snapshots": 0}

    core = build_core(df)
    segs = build_segments(df)
    core.to_csv(cfg.processed_dir / CORE_NAME, index=False)
    core.to_parquet(cfg.processed_dir / CORE_NAME.replace(".csv", ".parquet"), index=False)
    segs.to_csv(cfg.processed_dir / SEGMENT_NAME, index=False)
    segs.to_parquet(cfg.processed_dir / SEGMENT_NAME.replace(".csv", ".parquet"), index=False)

    n = len(core)
    print(f"  {n} snapshot date(s) -> {CORE_NAME}, {SEGMENT_NAME}")
    if n >= 2:
        first, last = core.iloc[0], core.iloc[-1]
        chg = 100 * (last["median_price"] / first["median_price"] - 1)
        print(
            f"  median asking price {first['snapshot_date']} -> {last['snapshot_date']}: "
            f"£{first['median_price']:,.0f} -> £{last['median_price']:,.0f} ({chg:+.1f}%)"
        )
    return {"snapshots": n}
