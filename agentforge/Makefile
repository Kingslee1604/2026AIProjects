SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help tools setup lint format typecheck test docs-check secrets-scan licenses check

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

tools: ## Install pinned developer tools into ~/.local (uv, node, pnpm, gitleaks, pre-commit)
	./scripts/install-dev-tools.sh

setup: ## Install project dependencies from lockfiles and enable git hooks
	uv sync --frozen
	pnpm install --frozen-lockfile
	pre-commit install

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

secrets-scan: ## Scan the working tree for secrets
	gitleaks dir . --no-banner --redact --config ../.gitleaks.toml

licenses: ## Fail if any dependency's license is outside licenses/policy.toml
	uv run --frozen python -m tools.license_check

check: lint typecheck test docs-check secrets-scan licenses ## Run every check (same gates as CI)
	@echo "All checks passed."
