# Dawn — developer entry points. `make help` lists targets.
SHELL := /bin/bash
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
HOST ?= dawn.local
DEPLOY_USER ?= pi
DEPLOY_DIR ?= /opt/dawn

.PHONY: help setup web-build sim test e2e screenshots lint gen-config deploy clean

help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

setup: $(VENV)/bin/activate ## Install Python packages (editable) and web dependencies
	$(PIP) install -e ./core[dev] -e ./dawn-timed[dev] -e ./sim
	cd web && npm install --no-audit --no-fund

web-build: ## Build both web targets into web/dist
	cd web && npm run build

sim: ## Run simulators + core + dawn-timed on the laptop (http://localhost:8080)
	@test -f web/dist/index.html || $(MAKE) web-build
	DAWN_SIM=1 DAWN_CONFIG=$(CURDIR)/config/config.sim.yaml DAWN_DATA_DIR=$(CURDIR)/var/dawn $(PY) -m dawn_sim

sim-nocore: ## Run only the simulators (for developing core in another terminal)
	$(PY) -m dawn_sim --no-core --no-timed

test: ## Unit tests (core + dawn-timed)
	cd core && ../$(PY) -m pytest -q
	cd dawn-timed && ../$(PY) -m pytest -q

e2e: ## Playwright smoke tests against a running simulator (make sim in another terminal)
	cd web && npx playwright test

screenshots: ## Render face + control UI PNGs into docs/screenshots (needs make sim running)
	cd web && node scripts/screenshots.mjs

lint: ## Ruff + tsc
	$(VENV)/bin/ruff check core/dawn_core dawn-timed/dawn_timed sim/dawn_sim
	cd web && npm run typecheck

gen-config: ## Regenerate config/config.example.yaml from the schema
	$(PY) scripts/gen_example_config.py

deploy: web-build ## rsync the repo to the Pi and restart services (HOST=dawn.local)
	rsync -az --delete --exclude .git --exclude node_modules --exclude .venv --exclude var --exclude '__pycache__' ./ $(DEPLOY_USER)@$(HOST):$(DEPLOY_DIR)/
	ssh $(DEPLOY_USER)@$(HOST) 'cd $(DEPLOY_DIR) && sudo ./deploy/install.sh --update && sudo systemctl restart dawn-core dawn-timed dawn-face'

clean:
	rm -rf web/dist var core/build dawn-timed/build sim/build
