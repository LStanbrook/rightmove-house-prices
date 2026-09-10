"""Scrape a complete snapshot of Rightmove for-sale listings for one location.

Rightmove serves its search results as a Next.js page with the data embedded in
a ``__NEXT_DATA__`` <script> tag (``props.pageProps.searchResults``). The site
only paginates ~1000 results per query, so :func:`snapshot_listings` splits the
search into price bands recursively until every band is under that cap, then
de-duplicates by listing id.

This is intended for personal research at a low request rate. Respect
Rightmove's terms of use, keep ``request_delay_seconds`` polite, and do not
redistribute the raw data.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import requests

from .config import RightmoveConfig

SEARCH_URL = "https://www.rightmove.co.uk/property-for-sale/find.html"
RENT_URL = "https://www.rightmove.co.uk/property-to-rent/find.html"
TYPEAHEAD_URL = "https://los.rightmove.co.uk/typeahead"
RESULTS_PER_PAGE = 24
PAGINATION_HARD_CAP = 1000  # Rightmove refuses index >= ~1000

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
_POSTCODE_RE = re.compile(r"\b(EH\d{1,2})\s*(\d[A-Z]{2})?\b", re.IGNORECASE)
_SIZE_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*sq\s*(ft|m)", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Rightmove's own sale-price ladder. Splitting only on these values keeps every
# band boundary something the site will accept, so no listings fall in a gap.
PRICE_LADDER: list[int | None] = [
    0, 50_000, 60_000, 70_000, 80_000, 90_000,
    100_000, 110_000, 120_000, 130_000, 140_000, 150_000, 160_000, 170_000,
    180_000, 190_000, 200_000, 210_000, 220_000, 230_000, 240_000, 250_000,
    260_000, 270_000, 280_000, 290_000, 300_000, 325_000, 350_000, 375_000,
    400_000, 425_000, 450_000, 475_000, 500_000, 550_000, 600_000, 650_000,
    700_000, 800_000, 900_000, 1_000_000, 1_250_000, 1_500_000, 1_750_000,
    2_000_000, 2_500_000, 3_000_000, 4_000_000, 5_000_000, 7_500_000,
    10_000_000, 15_000_000, 20_000_000,
    None,  # open-ended top of the market
]


class RightmoveError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Low-level fetching
# --------------------------------------------------------------------------- #
def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def _fetch_search_page(
    session: requests.Session,
    cfg: RightmoveConfig,
    *,
    index: int,
    min_price: int | None,
    max_price: int | None,
) -> dict:
    """Return the ``searchResults`` object for one result page."""
    params = {
        "locationIdentifier": cfg.location_identifier,
        "index": index,
        "numberOfPropertiesPerPage": RESULTS_PER_PAGE,
        "radius": "0.0",
        "sortType": "6",  # newest listed; order is irrelevant for a full sweep
        "viewType": "LIST",
        "channel": cfg.channel,
        "includeSSTC": "true" if cfg.include_sstc else "false",
    }
    if min_price:
        params["minPrice"] = min_price
    if max_price:
        params["maxPrice"] = max_price

    url = SEARCH_URL if cfg.channel.upper() == "BUY" else RENT_URL
    last_exc: Exception | None = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise RightmoveError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return _parse_search_results(resp.text)
        except (requests.RequestException, RightmoveError) as exc:
            last_exc = exc
            backoff = cfg.request_delay_seconds * (2 ** attempt)
            time.sleep(backoff)
    raise RightmoveError(
        f"giving up after {cfg.max_retries} attempts (index={index}, "
        f"min={min_price}, max={max_price}): {last_exc}"
    )


def _parse_search_results(html: str) -> dict:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        snippet = html[:200].replace("\n", " ")
        raise RightmoveError(f"no __NEXT_DATA__ in response; got: {snippet!r}")
    try:
        blob = json.loads(m.group(1))
        return blob["props"]["pageProps"]["searchResults"]
    except (json.JSONDecodeError, KeyError) as exc:  # pragma: no cover
        raise RightmoveError(f"unexpected __NEXT_DATA__ shape: {exc}") from exc


def _result_count(results: dict) -> int:
    raw = results.get("resultCount") or "0"
    return int(str(raw).replace(",", ""))


# --------------------------------------------------------------------------- #
# Price-band sweep
# --------------------------------------------------------------------------- #
def _iter_band(
    session: requests.Session,
    cfg: RightmoveConfig,
    lo: int | None,
    hi: int | None,
) -> Iterator[dict]:
    """Yield every raw property dict Rightmove will page through for a band."""
    index = 0
    while index < PAGINATION_HARD_CAP:
        results = _fetch_search_page(
            session, cfg, index=index, min_price=lo or None, max_price=hi
        )
        time.sleep(cfg.request_delay_seconds)
        props = results.get("properties") or []
        # Rightmove pads pages with sponsored "featured" cards from a wider
        # area; drop anything whose id we would also see organically elsewhere
        # is handled by the caller's de-dupe. Just yield here.
        for p in props:
            yield p
        pagination = results.get("pagination") or {}
        options = pagination.get("options") or []
        if not props or not options:
            break
        max_index = max(int(o["value"]) for o in options)
        if index >= max_index:
            break
        index += RESULTS_PER_PAGE


def _collect_bands(
    session: requests.Session,
    cfg: RightmoveConfig,
    lo_idx: int,
    hi_idx: int,
    out: list[dict],
    seen: set[str],
    warnings: list[str],
) -> None:
    lo, hi = PRICE_LADDER[lo_idx], PRICE_LADDER[hi_idx]
    probe = _fetch_search_page(
        session, cfg, index=0, min_price=lo or None, max_price=hi
    )
    time.sleep(cfg.request_delay_seconds)
    count = _result_count(probe)
    if count == 0:
        return

    if count <= cfg.max_results_per_query or hi_idx - lo_idx <= 1:
        if count > cfg.max_results_per_query:
            msg = (
                f"price band £{lo:,}–{('£%s' % f'{hi:,}') if hi else '∞'} has "
                f"{count} listings, above the {cfg.max_results_per_query} cap; "
                f"capturing the first ~{PAGINATION_HARD_CAP}"
            )
            warnings.append(msg)
        for raw in _iter_band(session, cfg, lo, hi):
            pid = str(raw.get("id"))
            if pid and pid not in seen:
                seen.add(pid)
                out.append(raw)
        return

    mid_idx = (lo_idx + hi_idx) // 2
    _collect_bands(session, cfg, lo_idx, mid_idx, out, seen, warnings)
    _collect_bands(session, cfg, mid_idx, hi_idx, out, seen, warnings)


def snapshot_listings(cfg: RightmoveConfig) -> tuple[list[dict], dict]:
    """Fetch every current listing for the configured location.

    Returns ``(records, meta)`` where ``records`` are flat dicts ready for a
    DataFrame and ``meta`` carries the run summary (reported total, warnings).
    """
    session = _new_session()
    captured_at = datetime.now(timezone.utc)
    snapshot_day = captured_at.date()

    headline = _fetch_search_page(session, cfg, index=0, min_price=None, max_price=None)
    time.sleep(cfg.request_delay_seconds)
    reported_total = _result_count(headline)

    raw_props: list[dict] = []
    seen: set[str] = set()
    warnings: list[str] = []
    _collect_bands(session, cfg, 0, len(PRICE_LADDER) - 1, raw_props, seen, warnings)

    records = [_flatten(p, captured_at, snapshot_day) for p in raw_props]
    meta = {
        "captured_at": captured_at.isoformat(),
        "snapshot_date": snapshot_day.isoformat(),
        "location_identifier": cfg.location_identifier,
        "location_name": cfg.location_name,
        "reported_total": reported_total,
        "collected": len(records),
        "warnings": warnings,
    }
    return records, meta


# --------------------------------------------------------------------------- #
# Flattening
# --------------------------------------------------------------------------- #
def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_added_reduced(text: str, snapshot_day: date) -> tuple[str | None, str | None]:
    """('Added'|'Reduced', ISO date) from strings like 'Reduced on 01/09/2026'."""
    if not text:
        return None, None
    kind = None
    low = text.lower()
    if "reduced" in low:
        kind = "Reduced"
    elif "added" in low:
        kind = "Added"
    m = _DATE_RE.search(text)
    if m:
        dd, mm, yyyy = map(int, m.groups())
        try:
            return kind, date(yyyy, mm, dd).isoformat()
        except ValueError:
            return kind, None
    if "yesterday" in low:
        return kind, (snapshot_day - timedelta(days=1)).isoformat()
    if "today" in low:
        return kind, snapshot_day.isoformat()
    return kind, None


def _parse_size(display_size: str) -> float | None:
    if not display_size:
        return None
    m = _SIZE_RE.search(display_size)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    if m.group(2).lower() == "m":  # sq m -> sq ft
        value *= 10.7639
    return round(value, 1)


def _flatten(p: dict, captured_at: datetime, snapshot_day: date) -> dict:
    price = p.get("price") or {}
    display_prices = price.get("displayPrices") or [{}]
    listing_update = p.get("listingUpdate") or {}
    customer = p.get("customer") or {}
    location = p.get("location") or {}
    address = p.get("displayAddress") or ""

    pc = _POSTCODE_RE.search(address)
    outcode = pc.group(1).upper() if pc else None
    postcode = None
    if pc and pc.group(2):
        postcode = f"{pc.group(1).upper()} {pc.group(2).upper()}"

    ar_kind, ar_date = _parse_added_reduced(p.get("addedOrReduced") or "", snapshot_day)
    size_sqft = _parse_size(p.get("displaySize") or "")
    amount = _to_int(price.get("amount"))
    ppsf = round(amount / size_sqft, 1) if amount and size_sqft else None

    url = p.get("propertyUrl") or ""
    if url and not url.startswith("http"):
        url = "https://www.rightmove.co.uk" + url

    return {
        "captured_at": captured_at.isoformat(),
        "snapshot_date": snapshot_day.isoformat(),
        "id": str(p.get("id")),
        "price": amount,
        "price_qualifier": (display_prices[0] or {}).get("displayPriceQualifier") or None,
        "bedrooms": _to_int(p.get("bedrooms")),
        "bathrooms": _to_int(p.get("bathrooms")),
        "property_sub_type": p.get("propertySubType") or None,
        "property_type_full_description": p.get("propertyTypeFullDescription") or None,
        "tenure": (p.get("tenure") or {}).get("tenureType") if isinstance(p.get("tenure"), dict) else p.get("tenure"),
        "size_sqft": size_sqft,
        "price_per_sqft": ppsf,
        "display_address": address.replace("\r", " ").replace("\n", " ").strip() or None,
        "postcode": postcode,
        "outcode": outcode,
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "first_visible_date": listing_update.get("listingUpdateDate")
        or p.get("firstVisibleDate"),
        "listing_update_reason": listing_update.get("listingUpdateReason") or None,
        "listing_update_date": listing_update.get("listingUpdateDate") or None,
        "added_or_reduced": p.get("addedOrReduced") or None,
        "added_or_reduced_kind": ar_kind,
        "added_or_reduced_date": ar_date,
        "display_status": p.get("displayStatus") or None,
        "transaction_type": p.get("transactionType") or None,
        "channel": p.get("channel") or None,
        "is_development": bool(p.get("development")),
        "is_commercial": bool(p.get("commercial")),
        "is_auction": bool(p.get("auction")),
        "is_students": bool(p.get("students")),
        "is_residential": bool(p.get("residential")),
        "featured": bool(p.get("featuredProperty")),
        "premium": bool(p.get("premiumListing")),
        "number_of_images": _to_int(p.get("numberOfImages")),
        "number_of_floorplans": _to_int(p.get("numberOfFloorplans")),
        "branch_id": customer.get("branchId"),
        "branch_name": customer.get("branchDisplayName") or None,
        "property_url": url or None,
    }


# --------------------------------------------------------------------------- #
# Location resolver
# --------------------------------------------------------------------------- #
def resolve_location(query: str) -> list[dict]:
    """Look up Rightmove location identifiers for a place name."""
    session = _new_session()
    resp = session.get(
        TYPEAHEAD_URL,
        params={"query": query, "limit": 10, "exclude": ""},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    matches = data.get("matches") or data.get("results") or []
    out = []
    for m in matches:
        loc_type = m.get("type") or m.get("locationType") or ""
        loc_id = m.get("id") or m.get("locationIdentifier") or ""
        ident = loc_id if "^" in str(loc_id) else f"{loc_type}^{loc_id}"
        out.append(
            {
                "identifier": ident,
                "display_name": m.get("displayName") or m.get("name") or "",
                "type": loc_type,
            }
        )
    return out
