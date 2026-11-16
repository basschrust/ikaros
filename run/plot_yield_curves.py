"""Plot as-of published government yields from Snowflake (no interpolation).

Example:
  uv run --extra snowflake --with matplotlib python -m run.plot_yield_curves \\
      --date 2026-10-30 --currencies CHF GBP USD EUR
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ikaros_app.yield_curve_plot import fetch_points, grouped_points, plot_groups


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", type=date.fromisoformat, default=date.today(), help="As-of date")
    p.add_argument("--currencies", nargs="+", help="Currency list; default: all seven")
    p.add_argument("--output-dir", type=Path, default=Path("output/yield_curves"))
    p.add_argument("--max-age-days", type=int, default=7, help="Max daily observation age")
    p.add_argument("--monthly-max-age-days", type=int, default=75)
    p.add_argument("--show", action="store_true")
    args = p.parse_args()

    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from ikaros_app.operations import load_environment

    load_environment()
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("Select Snowflake in config/database.yaml")
    with repository_from_config(config) as repository:
        points = fetch_points(repository.connection, args.date, args.currencies)
    groups, warnings = grouped_points(
        points, args.date, args.max_age_days, args.monthly_max_age_days
    )
    for message in warnings:
        print(message)
    if not groups:
        p.error("No eligible government yield observations for requested date and currencies")
    outputs = plot_groups(groups, args.date, args.output_dir, show=args.show)
    for path in outputs:
        print(f"SAVED {path}")


if __name__ == "__main__":
    main()
