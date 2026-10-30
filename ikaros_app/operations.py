"""Operator commands and process supervision, independent of argparse."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BROKER = "127.0.0.1:9092"


class CommandFailure(RuntimeError):
    pass


def command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def docker_compose(*args: str) -> list[str]:
    return ["docker", "compose", "-f", "compose.kafka.yaml", *args]


def load_environment() -> dict[str, str]:
    """Read project .env without overriding an exported operating-system value."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Parent environment still works if the optional loader is absent.
        pass
    else:
        load_dotenv(ROOT / ".env", override=False)
    env = os.environ.copy()
    env.setdefault("IKAROS_KAFKA_BROKERS", BROKER)
    return env


@dataclass
class ProcessRunner:
    root: Path = ROOT

    def checked(self, argv: list[str], *, env: dict[str, str] | None = None) -> None:
        print("$", " ".join(argv), flush=True)
        code = subprocess.call(argv, cwd=self.root, env=env or load_environment())
        if code:
            raise CommandFailure(f"Command failed with exit code {code}: {argv}")

    def bootstrap(self) -> None:
        env = load_environment()
        self.checked(["docker", "info", "--format", "{{.ServerVersion}}"], env=env)
        self.checked(docker_compose("up", "-d"), env=env)
        self.checked(command("run.kafka_inbound_topics"), env=env)
        self.checked(command("run.kafka_discovery_topic"), env=env)

    def supervise(self, jobs: dict[str, list[str]]) -> None:
        env = load_environment()
        children: dict[str, subprocess.Popen] = {}
        interrupted = False
        try:
            for name, args in jobs.items():
                print(f"Starting {name}: {' '.join(args)}", flush=True)
                children[name] = subprocess.Popen(args, cwd=self.root, env=env)
            while True:
                for name, process in children.items():
                    code = process.poll()
                    if code is not None:
                        raise CommandFailure(f"{name} exited (status={code}); stopping Ikaros")
                time.sleep(2)
        except KeyboardInterrupt:
            interrupted = True
            print("Stopping Ikaros...", flush=True)
        finally:
            for process in children.values():
                if process.poll() is None:
                    process.terminate()
            for process in children.values():
                if process.poll() is None:
                    try:
                        process.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        process.kill()
            if interrupted:
                print("Ikaros stopped. Kafka data volume is preserved.")


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


BACKFILL_MODULES = {
    "GPW": "run.backfill_gpw_snowflake",
    "FX": "run.backfill_fx_snowflake",
    "FX-NBP": "run.backfill_fx_nbp",  # locally installed expansion, not always in GitHub
    "WIG20": "run.backfill_wig20_prices",  # locally installed expansion
    "FW20": "run.backfill_fw20_prices",  # locally installed expansion
}


def backfill_command(
    dataset: str,
    *,
    start: str | None,
    end: str | None,
    symbols: list[str] | None = None,
    limit: int | None = None,
    csv: str | None = None,
    write: bool = False,
) -> list[str]:
    name = dataset.upper()
    if name not in BACKFILL_MODULES:
        raise ValueError(f"Unsupported backfill dataset: {name}")
    if name in {"GPW", "FX", "FX-NBP", "WIG20"} and (not start or not end):
        raise ValueError(f"{name} requires --start and --end")
    if name == "FX" and not symbols:
        raise ValueError("FX requires --symbols; old backend defaults to EURPLN=X only")
    if symbols and name not in {"FX", "FX-NBP", "FW20"}:
        raise ValueError(f"--symbols unsupported for {name}")
    if limit is not None and name != "GPW":
        raise ValueError("--limit is only supported for GPW")
    if csv and name != "WIG20":
        raise ValueError("--csv is only supported for WIG20")
    args = command(BACKFILL_MODULES[name])
    if start:
        if name == "FW20":
            raise ValueError("FW20 supports --through (use --end), not --start")
        args += ["--start", start]
    if end:
        args += ["--through" if name == "FW20" else "--end", end]
    if symbols:
        args += ["--symbols", *symbols]
    if limit is not None:
        args += ["--limit", str(limit)]
    if csv:
        args += ["--csv", csv]
    if write:
        args += ["--write"]
    if name in {"GPW", "FX", "FX-NBP"}:
        args += ["--continue-on-error"]
    return args
