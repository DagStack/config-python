.PHONY: help install lint test test-cov conformance typecheck format clean

help:
	@echo "dagstack-config — Python binding for dagstack/config-spec"
	@echo ""
	@echo "Targets:"
	@echo "  install       uv sync (installs dev group)"
	@echo "  lint          ruff check + ruff format --check"
	@echo "  format        ruff format (autofix)"
	@echo "  typecheck     mypy --strict"
	@echo "  test          pytest"
	@echo "  test-cov      pytest с coverage report"
	@echo "  conformance   pytest -m conformance (требует spec/ submodule)"
	@echo "  clean         rm -rf build/ dist/ .pytest_cache/ .coverage"

install:
	uv sync --group dev

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy src tests

test:
	uv run pytest

test-cov:
	uv run pytest --cov-report=html --cov-report=term

conformance:
	uv run pytest -m conformance -v

clean:
	rm -rf build/ dist/ .pytest_cache/ .coverage htmlcov/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
