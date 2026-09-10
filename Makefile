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

.PHONY: dev
dev: ## Run the web dev server
	cd apps/web && bun run dev

##@ Help

.PHONY: help
help: ## Show available commands
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
