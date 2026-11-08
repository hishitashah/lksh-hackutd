# Avon Nightwatch: one-command tasks for the whole repo (task T0.1).
# `make help` lists every target. Recipes must be indented with a TAB, not spaces.

.PHONY: help setup dev test test-backend test-frontend lint types check-nvidia branding

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

setup:  ## Install backend (uv) and frontend (npm) dependencies
	cd backend && uv sync
	cd frontend && npm install

dev:  ## Run backend (:8000) + frontend (:5173) together; Ctrl-C stops both
	./scripts/dev.sh

test: test-backend test-frontend  ## Run every check that CI runs (except lint/branding)

test-backend:  ## pytest: contract + API tests (scorer checks join in T1.3)
	cd backend && uv run pytest -q

test-frontend:  ## Typecheck + production build
	cd frontend && npm run build

lint:  ## ruff (lint + format check) for the backend, eslint for the frontend
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

types:  ## Regenerate frontend/src/api/{openapi.json,types.ts} from the backend models
	cd backend && uv run python scripts/export_openapi.py
	cd frontend && npm run gen:types

check-nvidia:  ## List Nemotron models for NVIDIA_API_KEY in .env (ARGS=--ping to test a call)
	cd backend && uv run python scripts/check_nvidia.py $(ARGS)

branding:  ## Fail if banned branding appears in app code (see CLAUDE.md)
	./scripts/check_branding.sh
