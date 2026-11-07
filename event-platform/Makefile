SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help setup lint format typecheck test up down logs ps e2e check bench-memory chaos-up chaos-down bench-budget \
	crash-stack-up crash-stack-down bench-crash

CRASH := docker compose -f compose.yaml -f compose.crash.yaml --profile chaos
CRASH_BOOTSTRAP := localhost:29092,localhost:29093,localhost:29094

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install project dependencies from the lockfile
	uv sync --frozen

lint: ## Lint and check formatting (ruff)
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .

format: ## Apply formatting
	uv run --frozen ruff check --fix .
	uv run --frozen ruff format .

typecheck: ## Type-check (pyright, strict on src/)
	uv run --frozen pyright

test: ## Unit tests (no services needed)
	uv run --frozen pytest

check: lint typecheck test ## Everything that runs without the stack

up: ## Start the stack (Kafka, Postgres and the services) and wait until healthy
	docker compose up --detach --build --wait --wait-timeout 300
	@echo "Gateway: http://127.0.0.1:8100/docs   Budget service: http://127.0.0.1:8110/docs"

down: ## Stop the stack (data volumes are kept)
	docker compose --profile chaos down

chaos-up: ## Start two extra gateways (ports 8101, 8102) for the crash tests
	docker compose --profile chaos up --detach --build --wait --wait-timeout 300

chaos-down: ## Stop the extra gateways
	docker compose --profile chaos rm --stop --force gateway-2 gateway-3

logs: ## Follow logs from all services
	docker compose logs --follow --tail 100

ps: ## Show service status and health
	docker compose ps

e2e: ## End-to-end test against the running stack (make up first)
	uv run --frozen pytest -m e2e

bench-memory: ## Kafka RAM at idle and under load: make bench-memory NODES=1|3
	./bench/kafka-memory.sh $(or $(NODES),1)

bench-budget: ## Budget limits under gateway SIGKILLs (needs make chaos-up)
	uv run --frozen python bench/budget_chaos.py

crash-stack-up: ## Crash-test stack: 3 Kafka nodes, 3 gateways (stops the dev stack first)
	docker compose --profile chaos stop
	$(CRASH) up --detach --build --wait --wait-timeout 300

crash-stack-down: ## Remove the crash-test stack and its data (Kafka keeps none either)
	$(CRASH) down --volumes

bench-crash: ## Crash harness: broker, worker and gateway kills, frozen DB, replay (crash stack)
	KAFKA_BOOTSTRAP=$(CRASH_BOOTSTRAP) uv run --frozen python bench/crash.py $(SCENARIOS)
