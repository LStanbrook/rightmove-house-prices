"""Configuration loading for the Rightmove monitor."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class RightmoveConfig:
    location_identifier: str = "REGION^475"
    location_name: str = "Edinburgh"
    channel: str = "BUY"
    include_sstc: bool = False
    max_results_per_query: int = 1000
    request_delay_seconds: float = 1.5
    max_retries: int = 4

    @property
    def location_slug(self) -> str:
        return self.location_name.strip().lower().replace(" ", "-")


@dataclass
class UKHPIConfig:
    region_slug: str = "city-of-edinburgh"
    start_month: str = "2004-01"


@dataclass
class Config:
    rightmove: RightmoveConfig = field(default_factory=RightmoveConfig)
    ukhpi: UKHPIConfig = field(default_factory=UKHPIConfig)
    data_dir: Path = PROJECT_ROOT / "data"
    root: Path = PROJECT_ROOT

    # --- derived paths -----------------------------------------------------
    @property
    def raw_rightmove_dir(self) -> Path:
        return self.data_dir / "raw" / "rightmove"

    @property
    def raw_ukhpi_dir(self) -> Path:
        return self.data_dir / "raw" / "ukhpi"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    def ensure_dirs(self) -> None:
        for p in (self.raw_rightmove_dir, self.raw_ukhpi_dir, self.processed_dir):
            p.mkdir(parents=True, exist_ok=True)


def load_config(path: str | Path | None = None) -> Config:
    """Read config.yaml, falling back to built-in defaults for missing keys."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    rm = raw.get("rightmove", {}) or {}
    hpi = raw.get("ukhpi", {}) or {}
    paths = raw.get("paths", {}) or {}

    data_dir = Path(paths.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    return Config(
        rightmove=RightmoveConfig(
            location_identifier=rm.get("location_identifier", "REGION^475"),
            location_name=rm.get("location_name", "Edinburgh"),
            channel=rm.get("channel", "BUY"),
            include_sstc=bool(rm.get("include_sstc", False)),
            max_results_per_query=int(rm.get("max_results_per_query", 1000)),
            request_delay_seconds=float(rm.get("request_delay_seconds", 1.5)),
            max_retries=int(rm.get("max_retries", 4)),
        ),
        ukhpi=UKHPIConfig(
            region_slug=hpi.get("region_slug", "city-of-edinburgh"),
            start_month=str(hpi.get("start_month", "2004-01")),
        ),
        data_dir=data_dir,
        root=PROJECT_ROOT,
    )
