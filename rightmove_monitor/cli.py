"""Command-line entry point.

    python -m rightmove_monitor.cli snapshot     # scrape today's listings
    python -m rightmove_monitor.cli ukhpi        # refresh UK HPI series
    python -m rightmove_monitor.cli analyse      # rebuild time series
    python -m rightmove_monitor.cli forecast     # 12-month HPI forecast
    python -m rightmove_monitor.cli all          # snapshot + ukhpi + analyse + forecast
    python -m rightmove_monitor.cli resolve "Leith"
"""
from __future__ import annotations

import argparse
import sys

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rightmove_monitor")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("snapshot", help="scrape a full snapshot of current listings")
    sub.add_parser("ukhpi", help="download / refresh the UK HPI series")
    sub.add_parser("analyse", help="rebuild tidy time series from snapshots")
    fc = sub.add_parser("forecast", help="baseline 12-month price forecast")
    fc.add_argument("--horizon", type=int, default=12)
    sub.add_parser("dashboard", help="rebuild the HTML dashboard")
    sub.add_parser("all", help="snapshot -> ukhpi -> analyse -> forecast -> dashboard")
    rs = sub.add_parser("resolve", help="look up a Rightmove location identifier")
    rs.add_argument("query")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.command == "resolve":
        from .rightmove import resolve_location

        for m in resolve_location(args.query):
            print(f"{m['identifier']:<18} {m['type']:<14} {m['display_name']}")
        return 0

    if args.command == "snapshot":
        from .snapshot import run_snapshot

        run_snapshot(cfg)
        return 0

    if args.command == "ukhpi":
        from .ukhpi import update_ukhpi

        df = update_ukhpi(cfg)
        print(f"  UK HPI: {len(df)} months, latest {df['month'].iloc[-1]}")
        return 0

    if args.command == "analyse":
        from .analyse import run_analysis

        run_analysis(cfg)
        return 0

    if args.command == "forecast":
        from .forecast import run_forecast

        run_forecast(cfg, horizon=args.horizon)
        return 0

    if args.command == "dashboard":
        from .dashboard import build_dashboard

        build_dashboard(cfg)
        return 0

    if args.command == "all":
        from .analyse import run_analysis
        from .dashboard import build_dashboard
        from .forecast import run_forecast
        from .snapshot import run_snapshot
        from .ukhpi import update_ukhpi

        print("[1/5] snapshot"); run_snapshot(cfg)
        print("[2/5] ukhpi"); update_ukhpi(cfg)
        print("[3/5] analyse"); run_analysis(cfg)
        print("[4/5] forecast"); run_forecast(cfg)
        print("[5/5] dashboard"); build_dashboard(cfg)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
