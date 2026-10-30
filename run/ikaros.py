"""Ikaros operator CLI; run with python -m run.ikaros --help.

Compatibility exports are retained for existing tests and callers.
"""

from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import sys

from ikaros_app.operations import (
    CommandFailure,
    ProcessRunner,
    backfill_command,
    command,
    docker_compose,
    job_commands,
    load_environment,
)


def runtime_environment() -> dict[str, str]:
    return load_environment()


def checked(argv: list[str], *, env: dict[str, str] | None = None) -> None:
    ProcessRunner().checked(argv, env=env)


def bootstrap() -> None:
    ProcessRunner().bootstrap()


def supervise(jobs: dict[str, list[str]], *, env: dict[str, str] | None = None) -> None:
    # The runner loads .env and uses the resulting process environment.
    ProcessRunner().supervise(jobs)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ikaros local market-data ingestion")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("setup", help="Start Kafka and create topics")
    start = commands.add_parser("start", help="Start sinks and recurring ingestion")
    start.add_argument("--fx-symbols", nargs="+", default=["EURPLN=X"])
    start.add_argument("--gpw-limit", type=int)
    start.add_argument("--days", type=int, default=7)
    commands.add_parser("status", help="Report freshness and IIC failures")
    commands.add_parser("stop-broker", help="Stop Kafka without removing its volume")
    commands.add_parser("providers", help="List daily provider adapters")
    backfill = commands.add_parser("backfill", help="Historical catch-up, dry-run by default")
    backfill.add_argument(
        "--dataset", choices=["GPW", "FX", "FX-NBP", "WIG20", "FW20"], default="GPW"
    )
    backfill.add_argument("--start", type=date.fromisoformat)
    backfill.add_argument("--end", type=date.fromisoformat)
    backfill.add_argument("--symbols", nargs="+")
    backfill.add_argument("--limit", type=int)
    backfill.add_argument("--csv")
    backfill.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    runner = ProcessRunner()
    if args.action == "providers":
        from ikaros_app.providers import ProviderRegistry

        for dataset, provider in ProviderRegistry().describe().items():
            print(f"{dataset}: {provider}")
    elif args.action == "setup":
        runner.bootstrap()
    elif args.action == "start":
        if args.days < 1 or (args.gpw_limit is not None and args.gpw_limit < 1):
            parser.error("--days and --gpw-limit must be positive")
        runner.bootstrap()
        runner.supervise(
            job_commands(fx_symbols=args.fx_symbols, gpw_limit=args.gpw_limit, days=args.days)
        )
    elif args.action == "backfill":
        if args.start and args.end and args.start > args.end:
            parser.error("--start must not follow --end")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be positive")
        try:
            argv = backfill_command(
                args.dataset,
                start=args.start.isoformat() if args.start else None,
                end=args.end.isoformat() if args.end else None,
                symbols=args.symbols,
                limit=args.limit,
                csv=args.csv,
                write=args.write,
            )
        except ValueError as exc:
            parser.error(str(exc))
        module = argv[2]
        if importlib.util.find_spec(module) is None:
            parser.error(f"Optional module {module} not installed in this working tree")
        runner.checked(argv)
    elif args.action == "status":
        runner.checked(command("run.gpw_freshness", "--csv", "output/gpw_freshness.csv"))
        runner.checked(command("run.iic_status", "--failed-only"))
    elif args.action == "stop-broker":
        runner.checked(docker_compose("down"))


if __name__ == "__main__":
    try:
        main()
    except (CommandFailure, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
