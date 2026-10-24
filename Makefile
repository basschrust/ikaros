
.PHONY: test test-verbose lint lint-check check

UV ?= uv

test:
	$(UV) run --no-sync python -m pytest -q tests

test-verbose:
	$(UV) run --no-sync python -m pytest -v tests

lint-check:
	$(UV) run --no-sync ruff check .
	$(UV) run --no-sync ruff format --check .

lint:
	$(UV) run --no-sync ruff check --fix .
	$(UV) run --no-sync ruff format .

check: lint-check test
