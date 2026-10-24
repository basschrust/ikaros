"""Print, but do not run, seeding and Snowflake backfill commands."""

import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="majors", choices=["majors", "pln", "usd", "eur", "all"])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    groups = yaml.safe_load(Path("config/fx_expansion.yaml").read_text(encoding="utf-8"))[
        "backfill_groups"
    ]
    selected = groups if args.group == "all" else {args.group: groups[args.group]}
    for group, symbols in selected.items():
        names = " ".join(f'"{name}"' for name in symbols)
        print(f"# {group}: {len(symbols)} pairs")
        print(f"uv run python -m run.seed_fx_reference --symbols {names} --write")
        cmd = (
            "uv run python -m run.backfill_fx_snowflake "
            f"--symbols {names} --start {args.start} --end {args.end} --continue-on-error"
        )
        print(cmd + (" --write" if args.write else ""))
        print()


if __name__ == "__main__":
    main()
