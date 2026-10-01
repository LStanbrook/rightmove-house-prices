"""OCR a Rightmove floorplan image for its stated size, when Rightmove has no
`size_sqft` on file.

This is a Python port of the extraction logic from the companion
`rightmove scraper` Chrome extension (`offscreen.js`), which was developed and
tuned against many real Rightmove floorplans - the regexes and heuristics below
(OCR's "S"/"5" and "²"/"?" confusions, the internal-vs-external-area
disambiguation, the dropped-decimal recovery, the room-dimension-sum fallback)
are carried over as-is rather than re-derived, with Tesseract (the same engine
Tesseract.js wraps) in place of the browser OCR. Only scoped to the handful of
loft/warehouse candidates each day, not the whole snapshot - OCR is slow
(~1-2s/image) and most ordinary listings don't need it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from io import BytesIO

import pandas as pd
import requests
from PIL import Image

from .config import Config
from .rightmove import _HEADERS as _RM_HEADERS

try:
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None

if pytesseract is not None:
    import shutil

    if shutil.which("tesseract") is None:
        # The GitHub Actions runner gets the `tesseract` binary on PATH via
        # apt (see .github/workflows/daily.yml) and needs nothing here. This
        # is only for local Windows dev, where it's a separate (winget)
        # install that doesn't add itself to PATH.
        _default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        import os

        if os.name == "nt" and os.path.exists(_default_win_path):
            pytesseract.pytesseract.tesseract_cmd = _default_win_path

SQM_TO_SQFT = 10.7639
MIN_PLAUSIBLE_SQFT = 50
MAX_PLAUSIBLE_SQFT = 20_000
NO_DECIMAL_MAX_SQM = 200
MIN_ROOMS_FOR_ESTIMATE = 3
MIN_ROOM_DIM_M = 0.5
MAX_ROOM_DIM_M = 15
MAX_ROOM_AREA_SQM = 100
MIN_PLAUSIBLE_WIDTH_PX = 100
TARGET_OCR_WIDTH_PX = 1500
MAX_UPSCALE = 4
LABEL_LOOKBACK_CHARS = 60

# Tesseract reliably misreads "²" as "?", and sometimes the "S" in "SQ" as "5"
# (verified in the extension against real floorplans: "806 SQ FT" -> "£08 5Q
# FT", "94 sq ft" -> "94 5q ft"). At most one line break between the number
# and its unit - a bare \s* was found to wrongly bridge across blocks.
SQFT_RE = re.compile(
    r"(\d[\d,]{0,6}(?:\.\d+)?)[ \t]*\n?[ \t]*(?:[s5][qo]\.?\s*ft\b|[s5][qo]\.?\s*feet\b|ft\.?\s*[²2?])",
    re.IGNORECASE,
)
SQM_UNIT_RE = re.compile(
    r"(\d[\d,]{0,4}(?:\.\d+)?)[ \t]*\n?[ \t]*(?:sq\.?\s*m\b|m2\b|m²|m\?)",
    re.IGNORECASE,
)
SQM_LABEL_RE = re.compile(r"sq\.?\s*m\b\D{0,8}?(\d[\d,]{0,4}(?:\.\d+)?)(?!['’])", re.IGNORECASE)
ROOM_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)(m)?[ \t/]*x[ \t/]*(\d+(?:\.\d+)?)(m)?\b", re.IGNORECASE)

EXTERNAL_AREA_RE = re.compile(r"external", re.IGNORECASE)
SEPARATE_STRUCTURE_RE = re.compile(
    r"summer\s*house|annex(?:e)?|outbuilding|garden\s*room|granny\s*flat|garage", re.IGNORECASE
)
EXCLUDED_SUBAREA_RE = re.compile(r"excluded|\bwalls?\b", re.IGNORECASE)
INTERNAL_AREA_RE = re.compile(r"internal", re.IGNORECASE)


@dataclass
class OcrResult:
    sqft: int | None
    source: str | None  # "stated" | "roomSum" | None
    text_length: int = 0
    text_preview: str = ""
    error: str | None = None


def _context_for(text: str, index: int) -> str:
    block_start = text.rfind("\n\n", 0, index)
    context_start = max(0, block_start + 2, index - LABEL_LOOKBACK_CHARS)
    return text[context_start:index]


def _pick_best_total(matches: list[tuple[float, str]]) -> float | None:
    candidates = [
        (
            value,
            bool(INTERNAL_AREA_RE.search(ctx)),
            bool(
                EXTERNAL_AREA_RE.search(ctx)
                or SEPARATE_STRUCTURE_RE.search(ctx)
                or EXCLUDED_SUBAREA_RE.search(ctx)
            ),
        )
        for value, ctx in matches
    ]
    internal_ones = [v for v, internal, _ in candidates if internal]
    if internal_ones:
        return max(internal_ones)
    eligible = [v for v, _, excluded in candidates if not excluded]
    return max(eligible) if eligible else None


def _extract_stated_sqft(text: str) -> float | None:
    matches = []
    for m in SQFT_RE.finditer(text):
        value = float(m.group(1).replace(",", ""))
        if not (MIN_PLAUSIBLE_SQFT <= value <= MAX_PLAUSIBLE_SQFT):
            continue
        matches.append((value, _context_for(text, m.start())))
    return _pick_best_total(matches)


def _recover_sqm(raw: str) -> float:
    value = float(raw)
    if "." in raw or len(raw) != 3 or value <= NO_DECIMAL_MAX_SQM:
        return value
    return value / 10


def _sqm_to_sqft(sqm: float) -> int | None:
    sqft = round(sqm * SQM_TO_SQFT)
    return sqft if MIN_PLAUSIBLE_SQFT <= sqft <= MAX_PLAUSIBLE_SQFT else None


def _extract_stated_sqm_total(text: str) -> int | None:
    matches = []
    for m in SQM_UNIT_RE.finditer(text):
        sqft = _sqm_to_sqft(_recover_sqm(m.group(1).replace(",", "")))
        if sqft is None:
            continue
        matches.append((sqft, _context_for(text, m.start())))
    best = _pick_best_total(matches)
    if best is not None:
        return int(best)
    label_match = SQM_LABEL_RE.search(text)
    if not label_match:
        return None
    return _sqm_to_sqft(_recover_sqm(label_match.group(1).replace(",", "")))


def _recover_room_dim(s: str) -> float:
    value = float(s)
    if "." in s or value <= MAX_ROOM_DIM_M or len(s) != 3:
        return value
    shifted = value / 100
    return shifted if shifted >= MIN_ROOM_DIM_M else value


def _estimate_sqft_from_rooms(text: str) -> int | None:
    total_sqm = 0.0
    room_count = 0
    for m in ROOM_DIM_RE.finditer(text):
        a_str, a_unit, b_str, b_unit = m.group(1), m.group(2), m.group(3), m.group(4)
        has_unit = a_unit is not None or b_unit is not None
        if not has_unit and not ("." in a_str and "." in b_str):
            continue
        a, b = _recover_room_dim(a_str), _recover_room_dim(b_str)
        if not (MIN_ROOM_DIM_M <= a <= MAX_ROOM_DIM_M and MIN_ROOM_DIM_M <= b <= MAX_ROOM_DIM_M):
            continue
        area = a * b
        if area > MAX_ROOM_AREA_SQM:
            continue
        total_sqm += area
        room_count += 1
    if room_count < MIN_ROOMS_FOR_ESTIMATE:
        return None
    sqft = round(total_sqm * SQM_TO_SQFT)
    return sqft if MIN_PLAUSIBLE_SQFT <= sqft <= MAX_PLAUSIBLE_SQFT else None


def _upscale_if_small(im: Image.Image) -> Image.Image | None:
    if im.width >= TARGET_OCR_WIDTH_PX:
        return None
    scale = min(MAX_UPSCALE, -(-TARGET_OCR_WIDTH_PX // im.width))  # ceil div
    return im.resize((im.width * scale, im.height * scale), Image.LANCZOS)


def _ocr_text(im: Image.Image) -> str:
    return pytesseract.image_to_string(im, config="--psm 11 --dpi 300") or ""


def ocr_floorplan_sqft(image_bytes: bytes) -> OcrResult:
    """Best-effort size (sq ft) read from a floorplan image's own pixels."""
    if pytesseract is None:
        return OcrResult(None, None, error="pytesseract not installed")
    try:
        im = Image.open(BytesIO(image_bytes))
        im.load()
    except Exception as exc:
        return OcrResult(None, None, error=f"could not open image: {exc}")
    if im.width < MIN_PLAUSIBLE_WIDTH_PX:
        return OcrResult(None, None, error=f"image too small ({im.width}x{im.height}px)")
    im = im.convert("L")  # greyscale - floorplans are line art, this is enough

    native_text = _ocr_text(im)
    upscaled_text = None
    if im.width < TARGET_OCR_WIDTH_PX:
        upscaled = _upscale_if_small(im)
        if upscaled is not None:
            upscaled_text = _ocr_text(upscaled)

    combined = native_text if upscaled_text is None else native_text + "\n\n" + upscaled_text
    stated = _extract_stated_sqft(combined)
    if stated is None:
        stated = _extract_stated_sqm_total(combined)
    if stated is not None:
        return OcrResult(int(stated), "stated", len(combined), combined[:200])

    room_sum = _estimate_sqft_from_rooms(native_text)
    if room_sum is None and upscaled_text is not None:
        room_sum = _estimate_sqft_from_rooms(upscaled_text)
    return OcrResult(room_sum, "roomSum" if room_sum is not None else None, len(combined), combined[:200])


# --------------------------------------------------------------------------- #
# Fetching a floorplan URL + daily cache, so OCR only ever runs once per
# listing (not once per day). Scoped to the loft-match shortlist, not the
# whole snapshot - OCR is slow (~1-2s/image incl. two passes) and most
# ordinary listings never need it.
# --------------------------------------------------------------------------- #
# The property page itself doesn't embed a search-page-style JSON blob (it's a
# client-rendered React app), but the server HTML does still contain plain
# <img> src URLs for SEO - including the floorplan, at a "_max_WxH" thumbnail
# size. Stripping that suffix (Rightmove's own thumbnail-sizing convention)
# gives the full-resolution original, exactly as the companion Chrome
# extension does by reading the rendered DOM instead.
_FLOORPLAN_IMG_RE = re.compile(
    r'(https://media\.rightmove\.co\.uk/dir/property-floorplan/[^\s"\'\\)]+?)_max_\d+x\d+(\.\w+)'
)
CACHE_NAME = "floorplan_cache.csv"
_CACHE_COLUMNS = ["id", "floor_sqft", "floor_source", "checked_date"]


def find_floorplan_url(session: requests.Session, property_id: str) -> str | None:
    resp = session.get(f"https://www.rightmove.co.uk/properties/{property_id}", timeout=30)
    resp.raise_for_status()
    m = _FLOORPLAN_IMG_RE.search(resp.text)
    return (m.group(1) + m.group(2)) if m else None


def _load_cache(cfg: Config) -> pd.DataFrame:
    path = cfg.processed_dir / CACHE_NAME
    if path.exists():
        df = pd.read_csv(path, dtype={"id": str})
        for col in _CACHE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df
    return pd.DataFrame(columns=_CACHE_COLUMNS)


def _save_cache(cfg: Config, df: pd.DataFrame) -> None:
    cfg.ensure_dirs()
    df.to_csv(cfg.processed_dir / CACHE_NAME, index=False)


def enrich_floor_sizes(
    cfg: Config, candidates: pd.DataFrame, *, delay: float = 1.0, max_new: int = 30
) -> pd.DataFrame:
    """Add `floor_sqft`/`floor_source` columns, OCR-ing only ids not already cached.

    A listing's floorplan image doesn't change, so a cached result (including a
    confirmed "nothing found") is never re-attempted - this keeps the daily
    cost down to whatever's genuinely new since the cache was last written.
    """
    out = candidates.copy()
    out["id"] = out["id"].astype(str)
    if out.empty:
        out["floor_sqft"] = None
        out["floor_source"] = None
        return out

    cache = _load_cache(cfg)
    known_ids = set(cache["id"].astype(str)) if not cache.empty else set()
    # Only OCR candidates with no size on file at all - a listing Rightmove
    # already reports a size for doesn't need the floorplan read as well.
    needs_size = out["size_sqft"].isna() if "size_sqft" in out.columns else pd.Series(True, index=out.index)
    to_fetch = [i for i in out.loc[needs_size, "id"].tolist() if i not in known_ids][:max_new]

    if to_fetch and pytesseract is not None:
        session = requests.Session()
        session.headers.update(_RM_HEADERS)
        today = date.today().isoformat()
        new_rows = []
        for pid in to_fetch:
            sqft, source = None, None
            try:
                fp_url = find_floorplan_url(session, pid)
                time.sleep(delay)
                if fp_url:
                    img_resp = session.get(fp_url, timeout=30)
                    img_resp.raise_for_status()
                    time.sleep(delay)
                    result = ocr_floorplan_sqft(img_resp.content)
                    sqft, source = result.sqft, result.source
            except Exception:
                source = "error"
            new_rows.append({"id": pid, "floor_sqft": sqft, "floor_source": source, "checked_date": today})
        cache = pd.concat([cache, pd.DataFrame(new_rows)], ignore_index=True)
        _save_cache(cfg, cache)

    if cache.empty:
        out["floor_sqft"] = None
        out["floor_source"] = None
        return out
    return out.merge(cache[["id", "floor_sqft", "floor_source"]], on="id", how="left")
