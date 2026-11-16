"""Compatibility entry point for GPW. Shared implementation: run.seed_equities."""

from run.seed_equities import main as _main


def main() -> None:
    _main(default_venue="GPW")


if __name__ == "__main__":
    main()
