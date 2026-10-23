.PHONY: test test-verbose

PYTHON ?= python

test:
	$(PYTHON) -m pytest -q tests

test-verbose:
	$(PYTHON) -m pytest -v tests
