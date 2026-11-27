"""Local WIG20 full-history backfill: current constituents and live derivative listings.

Dry-run discovers the inventory; --write registers and retrieves source observations.
Existing candidate stage and independent promotion paths remain authoritative.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from providers.derivatives.wig20_listings import BANKIER, discover


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--market-data-dir", type=Path)
    parser.add_argument("--write", action="store_true", help="Register and ingest local DuckDB")
    parser.add_argument(
        "--inventory-only",
        action="store_true",
        help="Register discovered contracts only with --write",
    )
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument(
        "--delay", type=float, default=0.25, help="Seconds between derivative series"
    )
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.delay < 0:
        parser.error("--batch-size must be positive and --delay nonnegative")

    import requests
    from config.config import PROJECT_ROOT, load_config
    from database.coverage_identity import (
        COVERAGE_FIELDS,
        derivative_identities,
        index_identity,
        stock_identities,
    )
    from database.daedalus_readiness import live_contracts, price_dates
    from database.daedalus_starter import seed
    from database.local_bootstrap import bootstrap
    from database.market_quote_seed import seed_mappings
    from database.repositories.factory import repository_from_config
    from database.wig20_history import (
        derivative_rejections,
        equity_jobs,
        extend_archive_mappings,
        gpw_calendar,
        sessions,
    )
    from ingestion.candidate_stage_job import run_stage
    from database.candidate_promotion import promote_dataset
    from run.backfill_local import execute_jobs, yesterday_warsaw
    from run.import_local_daedalus_inputs import store_contracts, validate_contracts
    from run.prepare_daedalus import configure

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        as_of = datetime.now(ZoneInfo("Europe/Warsaw")).date()
        through = args.through or yesterday_warsaw()
        if through > yesterday_warsaw():
            raise ValueError("--through must be a completed date (yesterday or earlier in Warsaw)")
        closures, years = gpw_calendar(root=PROJECT_ROOT)
        required = sessions(start=args.start, end=through, closures=closures, years=years)
        if not required:
            raise ValueError("No GPW trading sessions in requested period")
        target = max(required)
        directory = configure(
            root=PROJECT_ROOT, market_dir=args.market_data_dir, persist=args.write
        )
        print(
            f"Market data: {directory / 'ikaros.duckdb'}\nGPW sessions: {len(required)} from {min(required)} to {target}",
            flush=True,
        )
        with requests.Session() as session:
            contracts, listing_issues = discover(
                session=session, as_of=as_of, closures=closures, calendar_years=years
            )
        futures = [item for item in contracts if item.kind == "FUTURE"]
        options = [item for item in contracts if item.kind == "OPTION"]
        for issue in listing_issues:
            print(f"LISTING WARNING: {issue}")
        print(
            f"Discovered {len(futures)} WIG20 futures and {len(options)} options (listed, not necessarily traded)"
        )
        if not futures or not options:
            raise ValueError("No usable futures or options listing; refusing empty inventory")
        if len(futures) < 4:
            listing_issues.append(
                f"Only {len(futures)} listed futures; verify against exchange roster"
            )
        if len({item.expiry for item in options}) < 6:
            listing_issues.append("Fewer than six option expiry dates; verify exchange roster")
        if not args.write:
            print("DRY RUN: no database writes. Add --write to retrieve history.")
            return int(bool(listing_issues))

        report_dir = PROJECT_ROOT / "output" / "wig20_history"
        report_dir.mkdir(parents=True, exist_ok=True)
        manifest = [item.manifest_row() for item in contracts]
        with (report_dir / "discovered_contracts.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(manifest[0]) + ["reported_expiry", "discovered_as_of"]
            )
            writer.writeheader()
            for item, row in zip(contracts, manifest):
                writer.writerow(
                    {
                        **row,
                        "reported_expiry": item.reported_expiry or "",
                        "discovered_as_of": as_of,
                    }
                )
        settings = load_config("database.yaml")
        override = settings["database"].get("duckdb", {}).get("output_dir")
        if override and Path(override).expanduser().resolve() != directory:
            raise ValueError("database.duckdb.output_dir conflicts with configured path")
        with repository_from_config(settings) as repo:
            conn = repo.connection
            personal = PROJECT_ROOT / "data" / "derivative_contracts.csv"
            bootstrap(
                conn,
                project_root=PROJECT_ROOT,
                contracts_csv=personal if personal.is_file() else None,
                write=True,
            )
            symbols = seed(connection=conn, project_root=PROJECT_ROOT, target=as_of)
            if not args.inventory_only:
                alias_count = extend_archive_mappings(
                    connection=conn, root=PROJECT_ROOT, start=args.start
                )
                print(f"Earlier reviewed equity archive mappings added: {alias_count}")
            items = validate_contracts(rows=manifest)
            conn.execute("BEGIN")
            try:
                count = store_contracts(connection=conn, items=items)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            seed_mappings(conn, backend="duckdb", write=True)
            # Include already reviewed unexpired contracts absent from either current listing.
            # Do not silently drop them; identify them in the report.
            registry_all = {
                str(row[0]): row
                for row in live_contracts(connection=conn, target=as_of)
                if row[1] in {"FUTURE", "OPTION"}
            }
            # This runner is GPW-specific. Do not silently include foreign
            # derivatives merely because their underlying is also WIG20.
            all_metadata = derivative_identities(connection=conn, symbols=tuple(registry_all))
            foreign_venues = {
                symbol: identity.venue
                for symbol, identity in all_metadata.items()
                if identity.venue not in (None, "GPW")
            }
            if foreign_venues:
                print(f"Excluded non-GPW WIG20 contracts: {foreign_venues}")
            registry = {
                symbol: row for symbol, row in registry_all.items() if symbol not in foreign_venues
            }
            discovered = {item.symbol: item for item in contracts}
            live = sorted(set(registry) | set(discovered))
            print(f"Registry added/enriched: {count}; live inventory: {len(live)}")
            jobs = equity_jobs(connection=conn, symbols=symbols, required=required)
        if args.inventory_only:
            print(
                f"Inventory written: {report_dir / 'discovered_contracts.csv'}; no prices requested"
            )
            return int(bool(listing_issues))

        # Index and constituent gaps are regular GPW sessions; retain accepted observations.
        equity_status = execute_jobs(
            jobs=jobs, project_root=PROJECT_ROOT, write=True, fail_fast=False
        )
        # Derivative contracts can be illiquid. Fetch the entire requested window once
        # per series, rather than re-reading the same history pages for every 60-day job.
        # Each batch stages first, closes the writer, then independently promotes.
        errors: dict[str, str] = {}
        observations: dict[str, int] = {}
        promotion_issues = []
        for offset in range(0, len(live), args.batch_size):
            batch = live[offset : offset + args.batch_size]
            with repository_from_config(settings) as repo:
                for n, symbol in enumerate(batch, start=offset + 1):
                    print(f"DERIVATIVE [{n}/{len(live)}] {symbol}", flush=True)
                    staged = run_stage(
                        repo.connection,
                        dataset="DERIVATIVES",
                        symbols=[symbol],
                        start=args.start,
                        end=target,
                        backend="duckdb",
                        write=True,
                    )
                    observations[symbol] = staged.observations
                    if staged.errors or not staged.instruments:
                        errors[symbol] = "; ".join(staged.errors) or "No active mapping"
                    if args.delay:
                        time.sleep(args.delay)
            with repository_from_config(settings) as repo:
                promoted = promote_dataset(
                    repo.connection,
                    dataset="DERIVATIVES",
                    symbols=batch,
                    start=args.start,
                    end=target,
                    backend="duckdb",
                )
                print(
                    f"PROMOTED batch: inserted={promoted.promoted}, existing={promoted.identical}, invalid={promoted.invalid}, conflicts={promoted.conflicts}"
                )
                if promoted.conflicts:
                    promotion_issues.append(
                        f"{batch[0]}..{batch[-1]}: {promoted.conflicts} preserved conflicts"
                    )
        missing_equities = {}
        missing_futures = []
        no_history = []
        rejected_all = []
        bankier_without_history = []
        missing_derivative_venues: list[str] = []
        with repository_from_config(settings) as repo:
            conn = repo.connection
            stock_metadata = stock_identities(connection=conn, symbols=symbols)
            derivative_metadata = derivative_identities(connection=conn, symbols=live)
            coverage = []
            for dataset, symbol in [("WIG20", "WIG20"), *(("GPW", s) for s in symbols)]:
                identity = (
                    index_identity(symbol=symbol) if dataset == "WIG20" else stock_metadata[symbol]
                )
                dates = price_dates(connection=conn, dataset=dataset, symbol=symbol, target=target)
                missing = sorted(required - dates)
                if missing:
                    missing_equities[symbol] = [d.isoformat() for d in missing]
                coverage.append(
                    {
                        "venue": identity.venue or "",
                        "instrument_type": identity.instrument_type,
                        "symbol": symbol,
                        "accepted_sessions": len(dates & required),
                        "missing_sessions": len(missing),
                        "first": min(dates & required, default=""),
                        "last": max(dates & required, default=""),
                        "status": "GAPS" if missing else "OK",
                    }
                )
            for symbol in live:
                dates = {
                    d
                    for d in price_dates(
                        connection=conn, dataset="DERIVATIVES", symbol=symbol, target=target
                    )
                    if d >= args.start
                }
                identity = derivative_metadata[symbol]
                if identity.venue is None:
                    missing_derivative_venues.append(symbol)
                if identity.instrument_type == "FUTURE" and target not in dates:
                    missing_futures.append(symbol)
                if not dates:
                    no_history.append(symbol)
                    if observations.get(symbol, 0):
                        rejected_all.append(symbol)
                    if symbol in discovered and set(discovered[symbol].sources) & set(
                        BANKIER.values()
                    ):
                        bankier_without_history.append(symbol)
                status = (
                    "SOURCE_ERROR"
                    if symbol in errors
                    else "NO_SOURCE_ROWS"
                    if not observations.get(symbol)
                    else "NO_ACCEPTED_ROWS"
                    if not dates
                    else "SOURCE_WINDOW_PROCESSED"
                )
                coverage.append(
                    {
                        "venue": identity.venue or "",
                        "instrument_type": identity.instrument_type,
                        "symbol": symbol,
                        "accepted_sessions": len(dates),
                        "missing_sessions": "",
                        "first": min(dates, default=""),
                        "last": max(dates, default=""),
                        "status": status,
                        "listing": "DISCOVERED" if symbol in discovered else "REGISTRY_ONLY",
                        "source_observations": observations.get(symbol, 0),
                        "rejections": json.dumps(
                            derivative_rejections(
                                connection=conn, symbol=symbol, start=args.start, end=target
                            ),
                            sort_keys=True,
                        ),
                        "source_errors": errors.get(symbol, ""),
                    }
                )
        fields = list(COVERAGE_FIELDS)
        with (report_dir / "coverage.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(coverage)
        summary = {
            "start": args.start.isoformat(),
            "through": target.isoformat(),
            "discovered_as_of": as_of.isoformat(),
            "listed_futures": len(futures),
            "listed_options": len(options),
            "registry_only": sorted(set(live) - set(discovered)),
            "excluded_non_gpw_contracts": foreign_venues,
            "listing_issues": listing_issues,
            "missing_equity_sessions": missing_equities,
            "futures_without_target_close": missing_futures,
            "derivative_source_errors": errors,
            "derivatives_without_accepted_history": no_history,
            "derivatives_with_source_rows_but_no_accepted_history": rejected_all,
            "bankier_listed_without_accepted_history": bankier_without_history,
            "contracts_missing_venue": missing_derivative_venues,
            "promotion_conflicts": promotion_issues,
            "equity_worker_status": equity_status,
        }
        (report_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Coverage report: {report_dir / 'coverage.csv'}")
        print(
            f"Missing equity/index sessions: {sum(map(len, missing_equities.values()))}; derivative source errors: {len(errors)}; derivatives without accepted history: {len(no_history)}; contracts missing venue: {len(missing_derivative_venues)}"
        )
        incomplete = bool(
            listing_issues
            or missing_equities
            or missing_futures
            or errors
            or promotion_issues
            or equity_status
            or rejected_all
            or bankier_without_history
            or missing_derivative_venues
        )
        print(
            "INCOMPLETE: inspect summary.json"
            if incomplete
            else "SOURCE WINDOWS PROCESSED: see coverage.csv; no claim of exchange-wide completeness"
        )
        return int(incomplete)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"WIG20 BACKFILL INCOMPLETE: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
