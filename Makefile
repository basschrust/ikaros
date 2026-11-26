.PHONY: test test-verbose lint lint-check check format

# Use the project environment directly: no activation or uv on PATH required.
ifeq ($(OS),Windows_NT)
PYTHON ?= .venv/Scripts/python.exe
else
PYTHON ?= .venv/bin/python
endif

test:
	"$(PYTHON)" -m pytest -q tests

test-verbose:
	"$(PYTHON)" -m pytest -v tests

lint-check:
	"$(PYTHON)" -m ruff check .
	"$(PYTHON)" -m ruff format --check .

lint:
	"$(PYTHON)" -m ruff check --fix .
	"$(PYTHON)" -m ruff format .

check: lint-check test

format:
	"$(PYTHON)" -m ruff format .
