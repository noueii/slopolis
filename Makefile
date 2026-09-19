SHELL := bash

.DEFAULT_GOAL := help

##@ Bootstrap

.PHONY: wt-config
wt-config: ## Bootstrap the current worktree from the primary checkout
	@set -euo pipefail; \
	git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "wt-config: not inside a git work tree" >&2; exit 1; }; \
	root="$$(git rev-parse --show-toplevel)"; \
	common="$$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"; \
	[ -n "$$common" ] || common="$$(cd "$$(git rev-parse --git-common-dir)" && pwd -P)"; \
	primary="$$(cd "$$(dirname "$$common")" && pwd -P)"; \
	root="$$(cd "$$root" && pwd -P)"; \
	if [ "$$root" = "$$primary" ]; then echo "wt-config: already in the primary checkout"; exit 0; fi; \
	echo "wt-config: bootstrapping $$root from $$primary"; \
	copied=0; \
	while IFS= read -r -d '' file; do \
		base="$${file##*/}"; \
		case "$$base" in *.example|*.sample|*.template|*.dist|*.default|*.example.*|*.sample.*|*.template.*) continue ;; esac; \
		dest="$$root/$${file#"$$primary"/}"; \
		if [ -e "$$dest" ] || [ -L "$$dest" ]; then continue; fi; \
		mkdir -p -- "$$(dirname "$$dest")"; \
		cp -p -- "$$file" "$$dest"; \
		copied=$$((copied + 1)); \
	done < <(find "$$primary" \
		\( -type d \( -name .git -o -name node_modules -o -name .venv -o -name venv -o -name dist -o -name build -o -name .next -o -name target -o -name vendor -o -name __pycache__ -o -name .cache \) -prune \) \
		-o -type f \( -name '.env' -o -name '.env.*' -o -name '.envrc' \) -print0 2>/dev/null); \
	if [ "$$copied" -gt 0 ]; then echo "wt-config: copied $$copied config file(s)"; fi; \
	if [ -f "$$root/apps/web/package.json" ]; then \
		if command -v bun >/dev/null 2>&1; then \
			echo "wt-config: bun install in apps/web"; \
			( cd "$$root/apps/web" && bun install ); \
		else \
			echo "wt-config: bun not found; run 'bun install' in apps/web" >&2; exit 1; \
		fi; \
	fi; \
	echo "wt-config: done"

##@ Development

MOCK_PORT ?= 8300
MOCK_API  ?= http://localhost:$(MOCK_PORT)
REAL_API  ?= http://localhost:8400
MOCK_MODE ?= server
API_TARGET ?= $(MOCK_API)

.PHONY: mock
mock: ## Run the standalone mock API server
	cd apps/web && MOCK_PORT=$(MOCK_PORT) bun run mock

.PHONY: api
api: ## Run the real API server (apps/server — not implemented yet)
	@if [ -f apps/server/pyproject.toml ]; then \
		cd apps/server && uv run uvicorn app.main:app --reload --port 8400; \
	else \
		echo "api: apps/server is not implemented yet" >&2; exit 1; \
	fi

.PHONY: worker
worker: ## Run the ARQ background worker (apps/worker)
	@if [ -f apps/worker/pyproject.toml ]; then \
		cd apps/worker && uv run arq worker.main.WorkerSettings; \
	else \
		echo "worker: apps/worker is not implemented yet" >&2; exit 1; \
	fi

.PHONY: migrate
migrate: ## Apply database migrations (alembic upgrade head)
	cd packages/db && uv run alembic upgrade head

.PHONY: test
test: ## Run the Python test suite
	uv run pytest

.PHONY: lint
lint: ## Lint the Python workspace
	uv run ruff check .

.PHONY: typecheck
typecheck: ## Type-check the Python workspace
	uv run basedpyright

.PHONY: e2e
e2e: ## Run the hermetic end-to-end review-flow test
	uv run pytest tests/e2e -q

.PHONY: contract
contract: ## Run the OpenAPI contract test
	uv run pytest tests/contract -q

.PHONY: verify
verify: ## Run the full local gate: lint, types, tests, e2e, contract
	uv run ruff check . && uv run basedpyright && uv run pytest -q && $(MAKE) e2e && $(MAKE) contract

.PHONY: dev
dev: ## Run the web dev server; MOCK_MODE=server|worker|off
	cd apps/web && VITE_API_PROXY_TARGET="$(API_TARGET)" VITE_MOCK="$(MOCK_MODE)" bun run dev

.PHONY: dev-mock
dev-mock: ## Run the standalone mock API + web dev server together
	$(MAKE) API_TARGET=$(MOCK_API) MOCK_MODE=server -j2 mock dev

.PHONY: dev-worker
dev-worker: ## Run the web dev server with the in-browser mock worker (no server needed)
	$(MAKE) MOCK_MODE=worker dev

.PHONY: dev-api
dev-api: ## Run the real API + web dev server together
	$(MAKE) API_TARGET=$(REAL_API) MOCK_MODE=off -j2 api dev

.PHONY: preview-worker
preview-worker: ## Build + preview the web app with the in-browser mock worker (no server)
	cd apps/web && VITE_MOCK=worker bun run build && bun run preview

##@ Help

.PHONY: help
help: ## Show available commands
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
