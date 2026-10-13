SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help tools setup lint format typecheck test docs-check secrets-scan licenses check \
	up down logs ps doctor user-create user-list

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

tools: ## Install pinned developer tools into ~/.local (uv, node, pnpm, gitleaks, pre-commit)
	./scripts/install-dev-tools.sh

setup: ## Install project dependencies from lockfiles and enable git hooks
	uv sync --frozen
	pnpm install --frozen-lockfile
	pre-commit install

doctor: ## Check this machine (Docker, memory, Ollama, models, ports) with fix-it hints
	./scripts/doctor.sh

up: ## Start the local stack (Postgres, API, worker) and wait until healthy
	./scripts/ensure-env.sh
	docker compose up --detach --build --wait --wait-timeout 180
	@echo "API: http://127.0.0.1:$${API_PORT:-8000}/healthz  (docs: /docs)"

down: ## Stop the local stack (data volumes are kept)
	docker compose down

logs: ## Follow logs from all services
	docker compose logs --follow --tail 100

ps: ## Show service status and health
	docker compose ps

user-create: ## Create a user: make user-create EMAIL=you@example.com [ROLE=admin|member] (prompts for password)
	@test -n "$(EMAIL)" || { echo "Usage: make user-create EMAIL=you@example.com [ROLE=admin]"; exit 2; }
	docker compose run --rm api user create --email "$(EMAIL)" --role "$(or $(ROLE),member)"

user-list: ## List users in the default organization
	docker compose run --rm --no-deps api user list

lint: ## Lint Python (ruff), module boundaries (import-linter), formatting (ruff, prettier)
	uv run --frozen ruff check .
	uv run --frozen lint-imports
	uv run --frozen ruff format --check .
	pnpm exec prettier --check .

format: ## Apply formatting
	uv run --frozen ruff check --fix .
	uv run --frozen ruff format .
	pnpm exec prettier --write .

typecheck: ## Type-check Python (pyright, strict on the agentforge package)
	pnpm exec pyright

test: ## Run the test suite
	uv run --frozen pytest

docs-check: ## Validate every Mermaid diagram under docs/
	node scripts/docs-check.mjs docs

secrets-scan: ## Scan the working tree for secrets (local git-ignored .env files are skipped)
	cd .. && gitleaks dir agentforge --no-banner --redact --config .gitleaks.worktree.toml

licenses: ## Fail if any dependency's license is outside licenses/policy.toml
	uv run --frozen python -m tools.license_check

check: lint typecheck test docs-check secrets-scan licenses ## Run every check (same gates as CI)
	@echo "All checks passed."
