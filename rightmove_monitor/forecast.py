"""Baseline Edinburgh house-price forecast from the UK HPI average-price series.

A seasonal ARIMA on log(average price) gives a defensible 12-month projection
with uncertainty bands. Once you have accumulated several months of Rightmove
snapshots, asking-price momentum and inventory make natural leading-indicator
exogenous inputs - see the note at the bottom of this file.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .config import Config
from .ukhpi import update_ukhpi

FORECAST_CSV = "forecast_ukhpi.csv"
FORECAST_PNG = "forecast_ukhpi.png"


def run_forecast(cfg: Config, horizon: int = 12) -> dict:
    cfg.ensure_dirs()
    hpi = update_ukhpi(cfg)
    if hpi.empty or hpi["averagePrice"].notna().sum() < 48:
        print("  not enough UK HPI history to forecast")
        return {"ok": False}

    series = (
        hpi.dropna(subset=["averagePrice"])
        .assign(date=lambda d: pd.to_datetime(d["date"]))
        .set_index("date")["averagePrice"]
        .astype(float)
        .asfreq("MS")
        .interpolate()
    )
    log_series = np.log(series)

    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError:
        print("  install statsmodels to enable forecasting")
        return {"ok": False}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            log_series,
            order=(1, 1, 1),
            seasonal_order=(0, 1, 1, 12),
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
        fc = model.get_forecast(steps=horizon)
        mean = np.exp(fc.predicted_mean)
        ci80 = np.exp(fc.conf_int(alpha=0.20))
        ci95 = np.exp(fc.conf_int(alpha=0.05))

    hist = pd.DataFrame(
        {"date": series.index, "kind": "actual", "avg_price": series.values}
    )
    fut = pd.DataFrame(
        {
            "date": mean.index,
            "kind": "forecast",
            "avg_price": mean.values,
            "lo80": ci80.iloc[:, 0].values,
            "hi80": ci80.iloc[:, 1].values,
            "lo95": ci95.iloc[:, 0].values,
            "hi95": ci95.iloc[:, 1].values,
        }
    )
    out = pd.concat([hist, fut], ignore_index=True)
    out.to_csv(cfg.processed_dir / FORECAST_CSV, index=False)

    _plot(cfg, series, mean, ci95)

    last_actual = float(series.iloc[-1])
    proj = float(mean.iloc[-1])
    chg = 100 * (proj / last_actual - 1)
    print(
        f"  UK HPI avg price (City of Edinburgh): £{last_actual:,.0f} now -> "
        f"£{proj:,.0f} in {horizon} months ({chg:+.1f}%); "
        f"range £{float(ci95.iloc[-1, 0]):,.0f}-£{float(ci95.iloc[-1, 1]):,.0f}"
    )
    return {"ok": True, "projected_change_pct": chg, "horizon_months": horizon}


def _plot(cfg: Config, series, mean, ci95) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(series.index[-120:], series.values[-120:], label="UK HPI average price", lw=1.6)
    ax.plot(mean.index, mean.values, label="forecast", ls="--", color="C1")
    ax.fill_between(mean.index, ci95.iloc[:, 0], ci95.iloc[:, 1], alpha=0.2, color="C1", label="95% interval")
    ax.set_title("City of Edinburgh average house price - UK HPI + 12-month forecast")
    ax.set_ylabel("£")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(cfg.processed_dir / FORECAST_PNG, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Roadmap: leading-indicator model
# ---------------------------------------------------------------------------
# Once market_timeseries.parquet spans ~6+ months, resample it to month-start
# and feed these as `exog` to SARIMAX (lagged 1-3 months):
#   * median asking price (level and MoM change)
#   * active inventory count
#   * pct_reduced  (share of listings with a price cut)
#   * median_days_on_market
# Asking-price and inventory turns typically lead sold-price turns by a quarter,
# so this is where the Rightmove data starts to earn its keep predictively.
