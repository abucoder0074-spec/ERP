PYTHON ?= python

.PHONY: test lint typecheck translations

test:
	$(PYTHON) -m pytest --cov=apps --cov=config --cov=bot --cov-report=term-missing --cov-fail-under=70
	$(PYTHON) -m coverage report --include="*/services/*" --fail-under=85

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy

translations:
	$(PYTHON) -m babel.messages.frontend compile -D django -d locale
