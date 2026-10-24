.PHONY: test test-verbose lint lint-fix format format-check check
UV ?= uv

test:
	$(UV) run --no-sync python -m pytest -q tests

test-verbose:
	$(UV) run --no-sync python -m pytest -v tests

lint:
	$(UV) run --no-sync ruff check .

lint-fix:
	$(UV) run --no-sync ruff check --fix .

format:
	$(UV) run --no-sync ruff format .

format-check:
	$(UV) run --no-sync ruff format --check .

check: lint format-check test
