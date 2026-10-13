SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help tools setup lint format typecheck test docs-check secrets-scan licenses check \
	up down logs ps doctor user-create user-list api-client api-client-check web-build e2e

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

up: ## Start the local stack (Postgres, API, worker, web) and wait until healthy
	./scripts/ensure-env.sh
	docker compose up --detach --build --wait --wait-timeout 300
	@echo "App: http://127.0.0.1:$${WEB_PORT:-3000}   API: http://127.0.0.1:$${API_PORT:-8000}/docs"

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

lint: ## Lint Python (ruff), module boundaries (import-linter), web (eslint), formatting
	uv run --frozen ruff check .
	uv run --frozen lint-imports
	uv run --frozen ruff format --check .
	pnpm exec prettier --check .
	pnpm --filter @agentforge/web lint

format: ## Apply formatting
	uv run --frozen ruff check --fix .
	uv run --frozen ruff format .
	pnpm exec prettier --write .

typecheck: ## Type-check Python (pyright, strict on the agentforge package) and the web app (tsc)
	pnpm exec pyright
	pnpm --filter @agentforge/web typecheck

test: ## Run the test suite (Python, then web unit tests)
	uv run --frozen pytest
	pnpm --filter @agentforge/web test

API_SCHEMA := apps/web/src/lib/api/openapi.json
API_TYPES := apps/web/src/lib/api/schema.d.ts

api-client: ## Regenerate the web app's typed API client from the backend's OpenAPI schema
	uv run --frozen agentforge openapi > $(API_SCHEMA)
	pnpm --filter @agentforge/web gen:api

api-client-check: ## Fail if the committed API client is out of date with the backend
	@tmp=$$(mktemp) && uv run --frozen agentforge openapi > $$tmp && \
	  diff -q $$tmp $(API_SCHEMA) >/dev/null || { rm -f $$tmp; \
	  echo "API client is stale: run 'make api-client' and commit the result"; exit 1; }; rm -f $$tmp

web-build: ## Production build of the web app
	pnpm --filter @agentforge/web build

docs-check: ## Validate every Mermaid diagram under docs/
	node scripts/docs-check.mjs docs

secrets-scan: ## Scan the working tree for secrets (local git-ignored .env files are skipped)
	cd .. && gitleaks dir agentforge --no-banner --redact --config .gitleaks.worktree.toml

licenses: ## Fail if any dependency's license is outside licenses/policy.toml
	uv run --frozen python -m tools.license_check

check: api-client-check lint typecheck test docs-check secrets-scan licenses ## Run every check (same gates as CI)
	@echo "All checks passed."

PLAYWRIGHT_IMAGE := mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27

e2e: ## Browser tests (Chromium in the Playwright image) against the running stack (make up first)
	@email="e2e-$$(date +%s)@example.test"; pw="$$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"; \
	printf '%s\n' "$$pw" | docker compose run --rm --no-deps -T api user create \
	  --email "$$email" --role member --password-stdin >/dev/null && \
	docker run --rm --network agentforge_default --user "$$(id -u):$$(id -g)" \
	  -v "$(CURDIR):/work" -w /work/apps/web -e HOME=/tmp \
	  -e E2E_BASE_URL=http://web:3000 -e E2E_EMAIL="$$email" -e E2E_PASSWORD="$$pw" \
	  $(PLAYWRIGHT_IMAGE) npx playwright test
