SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help setup lint format typecheck test up down logs ps e2e check bench-memory

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

up: ## Start Kafka, Postgres, gateway and ledger, and wait until healthy
	docker compose up --detach --build --wait --wait-timeout 300
	@echo "Gateway: http://127.0.0.1:8100/docs"

down: ## Stop the stack (data volumes are kept)
	docker compose down

logs: ## Follow logs from all services
	docker compose logs --follow --tail 100

ps: ## Show service status and health
	docker compose ps

e2e: ## End-to-end test against the running stack (make up first)
	uv run --frozen pytest -m e2e

bench-memory: ## Kafka RAM at idle and under load: make bench-memory NODES=1|3
	./bench/kafka-memory.sh $(or $(NODES),1)
