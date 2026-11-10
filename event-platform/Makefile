SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help env setup lint format typecheck test tools security up down logs ps e2e check bench-memory chaos-up chaos-down bench-budget bench-load \
	crash-stack-up crash-stack-down bench-crash

# The database password lives in .env (git-ignored), created by `make env`.
-include .env
export POSTGRES_PASSWORD

CRASH := docker compose -f compose.yaml -f compose.crash.yaml --profile chaos
CRASH_BOOTSTRAP := localhost:29092,localhost:29093,localhost:29094

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env: ## Create .env with a random database password (kept if it exists)
	./scripts/ensure-env.sh

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

tools: ## Install pinned, checksum-verified tools into ~/.local/bin (uv, gitleaks, scanners)
	./scripts/install-tools.sh

security: ## Dependency, image and history scans, plus an SBOM (make up builds the image)
	./scripts/security-scan.sh

up: env ## Start the stack (Kafka, Postgres and the services) and wait until healthy
	docker compose up --detach --build --wait --wait-timeout 300
	@echo "Gateway: http://127.0.0.1:8100/docs   Budget service: http://127.0.0.1:8110/docs"

down: env ## Stop the stack (data volumes are kept)
	docker compose --profile chaos down

chaos-up: env ## Start two extra gateways (ports 8101, 8102) for the crash tests
	docker compose --profile chaos up --detach --build --wait --wait-timeout 300

chaos-down: env ## Stop the extra gateways
	docker compose --profile chaos rm --stop --force gateway-2 gateway-3

logs: env ## Follow logs from all services
	docker compose logs --follow --tail 100

ps: env ## Show service status and health
	docker compose ps

e2e: ## End-to-end test against the running stack (make up first)
	uv run --frozen pytest -m e2e

bench-memory: ## Kafka RAM at idle and under load: make bench-memory NODES=1|3
	./bench/kafka-memory.sh $(or $(NODES),1)

bench-load: ## Throughput, latency and memory of the dev stack (needs make up)
	uv run --frozen python bench/load.py

bench-budget: ## Budget limits under gateway SIGKILLs (needs make chaos-up)
	uv run --frozen python bench/budget_chaos.py

crash-stack-up: env ## Crash-test stack: 3 Kafka nodes, 3 gateways (stops the dev stack first)
	docker compose --profile chaos stop
	$(CRASH) up --detach --build --wait --wait-timeout 300

crash-stack-down: env ## Remove the crash-test stack and its data (Kafka keeps none either)
	$(CRASH) down --volumes

bench-crash: ## Crash harness: broker, worker and gateway kills, frozen DB, replay (crash stack)
	KAFKA_BOOTSTRAP=$(CRASH_BOOTSTRAP) uv run --frozen python bench/crash.py $(SCENARIOS)
