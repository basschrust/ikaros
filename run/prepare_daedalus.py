"""One-command local WIG20 reference setup, historical ingestion and readiness check."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path


FX = ("EURPLN=X", "USDPLN=X")
LOOKBACK = 252


def default_market_dir(*, root: Path) -> Path:
    siblings = [
        p
        for p in root.parent.iterdir()
        if p.is_dir()
        and p.name.lower()
        in {
            "daedalus",
            "pymarketsim",
            "daedalus-master",
            "daedalus-main",
            "pymarketsim-master",
            "pymarketsim-main",
        }
        and (p / "input" / "wig20.yaml").is_file()
    ]
    if len(siblings) == 1:
        return siblings[0] / "input" / "market_data"
    return root / "output" / "market_data"


def configure(*, root: Path, market_dir: Path | None, persist: bool = True) -> Path:
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)
    if os.getenv("IKAROS_DB_DRIVER", "duckdb").lower() != "duckdb":
        raise ValueError(
            "Local setup requires IKAROS_DB_DRIVER=duckdb; existing cloud settings untouched"
        )
    configured = os.getenv("IKAROS_MARKET_DATA_DIR")
    directory = market_dir or (Path(configured) if configured else default_market_dir(root=root))
    directory = directory.expanduser()
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    if market_dir is not None and configured:
        old = Path(configured).expanduser()
        if not old.is_absolute():
            old = root / old
        if old.resolve() != directory:
            raise ValueError("Existing market-data path differs; edit .env before changing it")
    os.environ["IKAROS_DB_DRIVER"] = "duckdb"
    os.environ["IKAROS_MARKET_DATA_DIR"] = str(directory)
    env = root / ".env"
    # Never overwrite existing settings or credentials.
    if not persist:
        return directory
    if not env.exists():
        with env.open("x", encoding="utf-8") as handle:
            handle.write(
                f'IKAROS_DB_DRIVER=duckdb\nIKAROS_MARKET_DATA_DIR="{directory.as_posix()}"\n'
            )
    elif not configured:
        with env.open("a", encoding="utf-8") as handle:
            handle.write(f'\nIKAROS_MARKET_DATA_DIR="{directory.as_posix()}"\n')
    return directory


def missing_ranges(*, missing: set[date]) -> list[tuple[date, date]]:
    """Repair short groups of missing sessions, not all subsequent history."""
    ranges: list[tuple[date, date]] = []
    for day in sorted(missing):
        if ranges and (day - ranges[-1][1]).days <= 4:
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    return ranges


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-data-dir", type=Path)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        help="Initial history start (default: 450 calendar days before target)",
    )
    parser.add_argument(
        "--reference-only",
        action="store_true",
        help="Seed reference data without downloading prices",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check existing file without downloading or seeding",
    )
    args = parser.parse_args(argv)
    if args.check_only and args.reference_only:
        parser.error("Choose only one of --check-only and --reference-only")
    from config.config import PROJECT_ROOT, load_config
    from database.daedalus_readiness import check_file, live_contracts, price_dates
    from database.repositories.factory import repository_from_config
    from ikaros_app.update import yesterday_warsaw
    from run.ingest_gpw_prices import last_gpw_sessions
    from run.backfill_local import BackfillJob, date_windows, execute_jobs

    try:
        through = args.through or yesterday_warsaw()
        if through > yesterday_warsaw():
            raise ValueError("Choose a completed date (through yesterday in Warsaw)")
        recent = last_gpw_sessions(through, 20)
        target = recent[-1]
        start = args.start or target - timedelta(days=450)
        if start > target:
            raise ValueError("History start is after the last completed GPW session")
        directory = configure(
            root=PROJECT_ROOT, market_dir=args.market_data_dir, persist=not args.check_only
        )
        db_path = directory / "ikaros.duckdb"
        print(f"Market data: {db_path}\nTarget GPW session: {target}", flush=True)
        if args.check_only:
            if not db_path.is_file():
                raise ValueError("Market database does not exist. Run setup-ikaros.cmd first.")
            problems = check_file(path=db_path, target=target, recent_sessions=recent)
            return report(problems=problems, status=0)
        from database.daedalus_starter import seed
        from database.local_bootstrap import bootstrap

        settings = load_config("database.yaml")
        override = settings["database"].get("duckdb", {}).get("output_dir")
        if override and Path(override).expanduser().resolve() != directory:
            raise ValueError(
                "database.duckdb.output_dir conflicts with .env; use one consistent path"
            )
        with repository_from_config(settings) as repo:
            conn = repo.connection
            personal_contracts = PROJECT_ROOT / "data" / "derivative_contracts.csv"
            bootstrap(
                conn,
                project_root=PROJECT_ROOT,
                contracts_csv=personal_contracts if personal_contracts.is_file() else None,
                write=True,
            )
            symbols = seed(connection=conn, project_root=PROJECT_ROOT, target=target)
            if args.reference_only:
                print("Reference setup complete. Prices were not downloaded.")
                return 0
            # Plan by instrument; do not download all registered markets or
            # repeatedly download a complete history on a routine refresh.
            index_dates = price_dates(
                connection=conn, dataset="WIG20", symbol="WIG20", target=target
            )
            required = set(sorted(index_dates)[-LOOKBACK:]) | set(recent)
            jobs: list[BackfillJob] = []
            for dataset, instruments in (("WIG20", ("WIG20",)), ("GPW", symbols), ("FX", FX)):
                grouped: dict[tuple[date, date], list[str]] = {}
                for symbol in instruments:
                    dates = (
                        index_dates
                        if dataset == "WIG20"
                        else price_dates(
                            connection=conn, dataset=dataset, symbol=symbol, target=target
                        )
                    )
                    if args.start is not None or len(dates) < LOOKBACK:
                        ranges = [(start, target)]
                    elif dataset == "FX":
                        if target in dates:
                            continue
                        ranges = [(max(start, max(dates) - timedelta(days=1)), target)]
                    else:
                        ranges = missing_ranges(missing=required - dates)
                    for begin, end in ranges:
                        for first, last in date_windows(start=begin, end=end, days=60):
                            grouped.setdefault((first, last), []).append(symbol)
                for (first, last), batch in sorted(grouped.items()):
                    jobs.append(BackfillJob(dataset, first, last, tuple(batch)))
            derivatives = live_contracts(connection=conn, target=target)
            grouped = {}
            for symbol, kind, *_metadata in derivatives:
                if kind not in {"FUTURE", "OPTION"}:
                    continue
                dates = price_dates(
                    connection=conn, dataset="DERIVATIVES", symbol=symbol, target=target
                )
                if kind == "FUTURE" and target in dates:
                    continue
                if kind == "OPTION" and len(dates) >= 4 and max(dates) >= recent[-5]:
                    continue
                begin = max(start, target - timedelta(days=59))
                if dates and (kind == "FUTURE" or len(dates) >= 4):
                    begin = max(begin, max(dates) - timedelta(days=1))
                grouped.setdefault(begin, []).append(symbol)
            for begin, batch in sorted(grouped.items()):
                for offset in range(0, len(batch), 30):
                    jobs.append(
                        BackfillJob(
                            "DERIVATIVES", begin, target, tuple(batch[offset : offset + 30])
                        )
                    )
        # Release the DuckDB writer before workers open the same file.
        status = execute_jobs(jobs=jobs, project_root=PROJECT_ROOT, write=True, fail_fast=False)
        problems = check_file(path=db_path, target=target, recent_sessions=recent)
        return report(problems=problems, status=status)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"SETUP INCOMPLETE: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Newcomer-facing command: keep diagnostics readable, preserve exit status.
        print(f"SETUP INCOMPLETE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Close any program using ikaros.duckdb, check the message above, then retry.",
            file=sys.stderr,
        )
        return 1


def report(*, problems: list[str], status: int) -> int:
    if problems or status:
        for problem in problems:
            print(f"MISSING: {problem}")
        if status:
            print("One or more source jobs failed. Existing accepted prices were kept.")
        print("NOT READY: rerun setup after resolving the reported issues.")
        return 1
    print("LOCAL WIG20 CHECK PASSED. Run Daedalus's own preparation/validation next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
