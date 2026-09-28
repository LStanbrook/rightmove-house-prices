"""Score current listings for open-plan industrial loft / warehouse conversions.

Rightmove has no working keyword filter on its search API (a `keywords` param is
accepted but doesn't actually narrow results), so this scans the free-text
`summary` and `key_features` Rightmove gives per listing and scores it against a
small keyword ladder: strong terms that basically only appear in a genuine
conversion (warehouse, foundry, printworks, bonded warehouse) count for more
than supporting decor terms (exposed brick, mezzanine, open plan) that also show
up in ordinary flats.

`summary`/`key_features` are only captured from the date this module was added -
older snapshots won't have them and are skipped.
"""
from __future__ import annotations

import re

import pandas as pd

from .config import Config

# (compiled pattern, human label, weight)
#
# Note: bare "loft" is deliberately NOT a strong signal. In ordinary Scottish
# property listings "loft" almost always means an attic conversion in a
# terraced/semi house ("converted loft", "loft storage") - nothing to do with
# an industrial warehouse-loft apartment. Only the compound phrases below
# ("loft apartment", "warehouse loft"...) are specific enough to count as
# strong; the bare word is kept only as a light supporting signal.
STRONG = [
    (r"\bwarehouse\b", "warehouse", 3),
    (r"\bfoundry\b", "foundry", 3),
    (r"print\s?works", "printworks", 3),
    (r"bonded warehouse", "bonded warehouse", 3),
    (r"whisky bond", "whisky bond", 3),
    (r"\bmill\b.{0,15}conversion|conversion.{0,15}\bmill\b|converted mill\b", "mill conversion", 3),
    (r"engine works", "engine works", 3),
    (r"industrial conversion", "industrial conversion", 3),
    (r"converted warehouse", "converted warehouse", 3),
    (r"\bmaltings\b", "maltings", 3),
    (r"grain store", "grain store", 3),
    (r"loft[- ]style", "loft-style", 3),
    (r"loft apartment", "loft apartment", 3),
    (r"loft living", "loft living", 3),
    (r"warehouse (?:loft|apartment|conversion)", "warehouse loft/apartment", 3),
    (r"former (?:warehouse|mill|factory|foundry|print\s?works)", "former industrial building", 3),
]
MEDIUM = [
    (r"exposed brick", "exposed brick", 2),
    (r"\bmezzanine\b", "mezzanine", 2),
    (r"double[- ]height", "double-height", 2),
    (r"cast[- ]iron column", "cast-iron columns", 2),
    (r"steel beam", "steel beams", 2),
    (r"exposed (?:steel|truss(?:es)?)", "exposed steel/trusses", 2),
    (r"industrial[- ]style", "industrial-style", 2),
    (r"warehouse[- ]style", "warehouse-style", 2),
    (r"factory conversion", "factory conversion", 2),
    # Modern/finished industrial-style builds (concrete + services left on show),
    # not just raw Victorian brick - a different but equally valid loft look.
    (r"exposed (?:duct\s?work|pipe\s?work|services)", "exposed ductwork/pipework", 2),
    (r"(?:exposed|polished) concrete", "exposed/polished concrete", 2),
    (r"concrete ceiling", "concrete ceiling", 2),
]
LIGHT = [
    (r"open[- ]plan", "open plan", 1),
    (r"exposed beams?", "exposed beams", 1),
    (r"high ceiling", "high ceilings", 1),
    (r"vaulted ceiling", "vaulted ceiling", 1),
    (r"unique (?:living space|conversion|home)", "unique conversion", 1),
    (r"characterful conversion", "characterful conversion", 1),
    (r"spiral stair", "spiral staircase", 1),
    (r"split[- ]level", "split-level", 1),
    (r"\bloft\b(?!\s*(?:storage|hatch|ladder|insulation))", "loft (unspecified)", 1),
]
ALL_PATTERNS = [(re.compile(p, re.I), label, w) for p, label, w in STRONG + MEDIUM + LIGHT]

TEXT_COLUMNS = [
    "summary", "key_features", "property_sub_type",
    "property_type_full_description", "display_address",
]


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return df[name].fillna("")
    return pd.Series([""] * len(df), index=df.index)


def _score_text(text: str) -> tuple[int, list[str]]:
    hits = []
    score = 0
    for pattern, label, weight in ALL_PATTERNS:
        if pattern.search(text):
            hits.append(label)
            score += weight
    return score, hits


def score_listings(df: pd.DataFrame) -> pd.DataFrame:
    """Add loft_score/loft_matches columns to a snapshot dataframe."""
    text = _col(df, TEXT_COLUMNS[0])
    for col in TEXT_COLUMNS[1:]:
        text = text.str.cat(_col(df, col), sep=" | ")
    scored = text.map(_score_text)
    out = df.copy()
    out["loft_score"] = scored.map(lambda t: t[0])
    out["loft_matches"] = scored.map(lambda t: ", ".join(t[1]))
    return out


def find_loft_candidates(cfg: Config, *, min_score: int = 3, limit: int = 40) -> pd.DataFrame:
    """Rank today's live listings by how "industrial loft / warehouse" they read."""
    files = sorted(cfg.raw_rightmove_dir.glob(f"{cfg.rightmove.location_slug}_*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.read_parquet(files[-1])
    df = df[df["price"].notna() & (df["price"] > 0)].copy()
    if "summary" not in df.columns and "key_features" not in df.columns:
        return pd.DataFrame()  # snapshot predates text capture

    scored = score_listings(df)
    out = scored[scored["loft_score"] >= min_score].sort_values(
        ["loft_score", "price"], ascending=[False, True]
    )
    cols = [
        "id", "loft_score", "loft_matches", "price", "price_qualifier", "bedrooms",
        "property_sub_type", "display_address", "outcode", "size_sqft",
        "added_or_reduced", "branch_name", "property_url",
    ]
    return out[[c for c in cols if c in out.columns]].head(limit).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Day-over-day history, so the dashboard can show what's NEW rather than the
# same top-scoring listing every single day it stays on the market.
# --------------------------------------------------------------------------- #
LOFT_HISTORY_NAME = "loft_history.csv"
_HISTORY_COLUMNS = [
    "snapshot_date", "id", "score", "price", "display_address", "outcode", "property_url",
]


def update_loft_history(cfg: Config, candidates: pd.DataFrame, snapshot_date: str) -> pd.DataFrame:
    """Append today's matches (as scored for the dashboard) to the running log.

    Re-running for the same date replaces that date's rows rather than
    duplicating them.
    """
    path = cfg.processed_dir / LOFT_HISTORY_NAME
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=_HISTORY_COLUMNS)

    today = pd.DataFrame({
        "snapshot_date": snapshot_date,
        "id": candidates["id"].astype(str),
        "score": candidates["loft_score"].astype(int),
        "price": candidates["price"],
        "display_address": candidates.get("display_address", ""),
        "outcode": candidates.get("outcode", ""),
        "property_url": candidates.get("property_url", ""),
    })
    combined = pd.concat(
        [existing[existing["snapshot_date"] != snapshot_date], today], ignore_index=True
    )
    combined = combined.sort_values(["snapshot_date", "score"], ascending=[True, False])
    combined.to_csv(path, index=False)
    return combined


def new_since_previous_snapshot(history: pd.DataFrame, snapshot_date: str) -> tuple[set[str], int]:
    """(ids new to the match list today, count no longer matching since the day before).

    On the first day of history there is nothing to compare against, so
    nothing is flagged "new" (everything would trivially be new).
    """
    if history.empty:
        return set(), 0
    dates = sorted(history["snapshot_date"].unique())
    if snapshot_date not in dates:
        return set(), 0
    idx = dates.index(snapshot_date)
    if idx == 0:
        return set(), 0
    prev_date = dates[idx - 1]
    today_ids = set(history.loc[history["snapshot_date"] == snapshot_date, "id"].astype(str))
    prev_ids = set(history.loc[history["snapshot_date"] == prev_date, "id"].astype(str))
    return today_ids - prev_ids, len(prev_ids - today_ids)
