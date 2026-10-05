.PHONY: install dev test lint format typecheck build publish clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest -q --cov=pyarattai --cov-report=term-missing

lint:
	ruff check pyarattai tests examples

format:
	ruff format pyarattai tests examples

typecheck:
	mypy pyarattai

build:
	python -m build

publish: build
	twine check dist/*
	twine upload dist/*

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
