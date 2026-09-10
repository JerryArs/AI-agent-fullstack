.PHONY: install test lint type check run docker

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

type:
	mypy mini_llm_gateway

check: lint type test

run:
	uvicorn mini_llm_gateway.app:build_default_app --factory --reload --port 8000

docker:
	docker build -t mini-llm-gateway:0.1.0 .
