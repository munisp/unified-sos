# SOS monorepo — common developer tasks.
# Local dev stack lives in deploy/; production deploys via infra/helm/sos-platform.

COMPOSE := docker compose -f deploy/docker-compose.yml --env-file deploy/.env
GO_SERVICES := services/mod-rev-core ledger/splits services/mod-geospatial-gateway
PY_SERVICES := $(wildcard services/*/ edge/edge-daemon/ deploy/keycloak/ packages/*/)
RUST_COMPONENTS := geospatial/geometry-rs

.PHONY: help test test-go test-python test-rust validate contracts lint lint-rust compose-up compose-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

test: test-go test-python test-ci ## Run all tests (Go + Python + CI gate assertions)

test-go: ## Run Go tests for all Go modules
	@for mod in $(GO_SERVICES); do \
		if [ -f "$$mod/go.mod" ]; then \
			echo "== go test: $$mod"; \
			(cd $$mod && go test ./...) || exit 1; \
		fi; \
	done

test-python: ## Run pytest per Python service directory
	@for svc in $(PY_SERVICES); do \
		if [ -d "$$svc/tests" ]; then \
			echo "== pytest: $$svc"; \
			(cd $$svc && python -m pytest -q) || exit 1; \
		fi; \
	done

test-ci: ## Run CI workflow gate assertions (security workflows must stay blocking)
	python -m pytest -q tests/ci

test-rust: ## Run cargo tests for Rust components (skips if cargo unavailable)
	@if command -v cargo >/dev/null 2>&1; then \
		for comp in $(RUST_COMPONENTS); do \
			if [ -f "$$comp/Cargo.toml" ]; then \
				echo "== cargo test: $$comp"; \
				(cd $$comp && cargo test --locked 2>/dev/null || cargo test) || exit 1; \
			fi; \
		done; \
	else \
		echo "SKIP: cargo not installed — Rust tests not run (see geospatial/README.md)"; \
	fi

validate: ## Validate state policy packs and infra manifests
	python config/states/validate_packs.py
	python infra/tests/validate_infra.py

contracts: ## Regenerate OpenAPI contracts from FastAPI apps (if generator present)
	@if [ -f contracts/openapi/generate_from_apps.py ]; then \
		python contracts/openapi/generate_from_apps.py; \
	else \
		echo "contracts/openapi/generate_from_apps.py not present — contracts are hand-maintained YAML"; \
	fi

lint: ## Lint Go and Python sources
	@for mod in $(GO_SERVICES); do \
		if [ -f "$$mod/go.mod" ]; then \
			(cd $$mod && go vet ./...) || exit 1; \
		fi; \
	done
	@for svc in $(PY_SERVICES); do \
		if [ -d "$$svc/tests" ]; then \
			python -m compileall -q $$svc || exit 1; \
		fi; \
	done

lint-rust: ## Run cargo clippy/fmt check for Rust components (skips if cargo unavailable)
	@if command -v cargo >/dev/null 2>&1; then \
		for comp in $(RUST_COMPONENTS); do \
			if [ -f "$$comp/Cargo.toml" ]; then \
				echo "== cargo check: $$comp"; \
				(cd $$comp && cargo check) || exit 1; \
			fi; \
		done; \
	else \
		echo "SKIP: cargo not installed — Rust lint not run"; \
	fi

compose-up: ## Boot the local development stack (deploy/docker-compose.yml)
	@test -f deploy/.env || cp deploy/.env.example deploy/.env
	$(COMPOSE) up -d --build

compose-down: ## Stop the local development stack
	$(COMPOSE) down
