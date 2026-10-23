.PHONY: setup setup-cloud test test-verbose lock sync check-env

UV ?= uv

# Create a local Python 3.12 virtual environment and install dependencies.
setup:
	$(UV) sync --python 3.12 --extra dev

setup-cloud:
	$(UV) sync --python 3.12 --extra dev --extra snowflake

# Resolve versions and write uv.lock. Commit uv.lock for exact reproducibility.
lock:
	$(UV) lock

# Use the committed lockfile and do not silently update dependencies.
sync:
	$(UV) sync --locked --extra dev --extra snowflake

test:
	$(UV) run --no-sync python -m pytest -q tests

test-verbose:
	$(UV) run --no-sync python -m pytest -v tests

check-env:
	$(UV) run --no-sync python -m run.check_environment
