"""Compatibility entry point for GPW. Shared implementation: run.backfill_equities."""

from run.backfill_equities import main as _main


def main() -> None:
    _main(default_venue="GPW")


if __name__ == "__main__":
    main()
