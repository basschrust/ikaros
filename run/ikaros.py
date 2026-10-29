"""One supported operator entry point for Ikaros.

Use: uv run --extra kafka --extra snowflake python -m run.ikaros <command>

Existing ingestion modules remain the implementation layer.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROKER = "127.0.0.1:9092"


class CommandFailure(RuntimeError):
    pass


def command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def checked(argv: list[str], *, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(argv), flush=True)
    code = subprocess.call(argv, cwd=ROOT, env=env)
    if code:
        raise CommandFailure(f"Command failed with exit code {code}: {argv}")


def docker_compose(*args: str) -> list[str]:
    return ["docker", "compose", "-f", "compose.kafka.yaml", *args]


def runtime_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("IKAROS_KAFKA_BROKERS", BROKER)
    return env


def bootstrap() -> None:
    """Prepare Kafka infrastructure; never create or modify market reference data."""
    env = runtime_environment()
    checked(["docker", "info", "--format", "{{.ServerVersion}}"], env=env)
    checked(docker_compose("up", "-d"), env=env)
    checked(command("run.kafka_inbound_topics"), env=env)
    checked(command("run.kafka_discovery_topic"), env=env)


def job_commands(
    *, fx_symbols: list[str], gpw_limit: int | None, days: int
) -> dict[str, list[str]]:
    scheduler = command(
        "run.ikaros_scheduler", "--write", "--days", str(days), "--fx-symbols", *fx_symbols
    )
    if gpw_limit is not None:
        scheduler += ["--gpw-limit", str(gpw_limit)]
    return {
        "fx-sink": command("run.kafka_sink", "--dataset", "FX"),
        "gpw-sink": command("run.kafka_sink", "--dataset", "GPW"),
        "derivatives-sink": command("run.kafka_contract_sink"),
        "scheduler": scheduler,
    }


def supervise(jobs: dict[str, list[str]], *, env: dict[str, str]) -> None:
    """Single foreground supervisor. If any child dies, exit nonzero instead of silently running half a pipeline."""
    children: dict[str, subprocess.Popen] = {}
    stopping = False
    try:
        for name, argv in jobs.items():
            print(f"Starting {name}: {' '.join(argv)}", flush=True)
            children[name] = subprocess.Popen(argv, cwd=ROOT, env=env)
        while True:
            for name, process in children.items():
                code = process.poll()
                if code is not None:
                    raise CommandFailure(f"{name} exited (status={code}); stopping Ikaros")
            time.sleep(2)
    except KeyboardInterrupt:
        stopping = True
        print("Stopping Ikaros...", flush=True)
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for child in children.values():
            if child.poll() is None:
                try:
                    child.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    child.kill()
        if stopping:
            print("Ikaros stopped. Kafka data volume is preserved.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ikaros: one entry point for market data operations"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser(
        "setup", help="Start Docker Kafka and create topics (Python deps must already be installed)"
    )
    start = sub.add_parser(
        "start", help="Start Kafka, sinks and scheduler in one foreground process"
    )
    start.add_argument("--fx-symbols", nargs="+", default=["EURPLN=X"])
    start.add_argument(
        "--gpw-limit", type=int, default=None, help="Pilot only; default all registered stocks"
    )
    start.add_argument("--days", type=int, default=7)
    backfill = sub.add_parser(
        "backfill", help="GPW historical catch-up through IIC (idempotent insert-only)"
    )
    backfill.add_argument("--start", type=date.fromisoformat, required=True)
    backfill.add_argument("--end", type=date.fromisoformat, required=True)
    backfill.add_argument("--limit", type=int)
    sub.add_parser("status", help="Show GPW freshness and IIC failures from Snowflake")
    sub.add_parser("stop-broker", help="Stop local Kafka, preserving stored messages")
    args = parser.parse_args(argv)
    if args.action == "setup":
        bootstrap()
    elif args.action == "start":
        if args.days < 1 or (args.gpw_limit is not None and args.gpw_limit < 1):
            parser.error("--days and --gpw-limit must be positive")
        bootstrap()
        supervise(
            job_commands(fx_symbols=args.fx_symbols, gpw_limit=args.gpw_limit, days=args.days),
            env=runtime_environment(),
        )
    elif args.action == "backfill":
        if args.start > args.end:
            parser.error("--start must not follow --end")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be positive")
        cmd = command(
            "run.backfill_gpw_snowflake",
            "--start",
            args.start.isoformat(),
            "--end",
            args.end.isoformat(),
            "--write",
            "--continue-on-error",
        )
        if args.limit is not None:
            cmd.extend(["--limit", str(args.limit)])
        checked(cmd, env=runtime_environment())
    elif args.action == "status":
        checked(
            command("run.gpw_freshness", "--csv", "output/gpw_freshness.csv"),
            env=runtime_environment(),
        )
        checked(command("run.iic_status", "--failed-only"), env=runtime_environment())
    elif args.action == "stop-broker":
        checked(docker_compose("down"), env=runtime_environment())


if __name__ == "__main__":
    try:
        main()
    except (CommandFailure, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
