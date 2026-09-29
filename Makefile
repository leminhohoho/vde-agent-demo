# Local development. See README.md ("Makefile usage").

HOST         ?= 127.0.0.1
BACKEND_DB   ?= var/backend.db
WAREHOUSE_DB ?= var/warehouse.db

.DEFAULT_GOAL := help
.PHONY: help backend reset-db insight-test insight-lint

help:
	@echo "make backend        start the backend on $(HOST):8000 with the agent plugins listed in backend/config.yaml"
	@echo "                    (each configured by agents/<name>/.env); HOST=0.0.0.0 serves other machines"
	@echo "make reset-db       delete and reseed $(BACKEND_DB) and $(WAREHOUSE_DB) (stop the backend first)"
	@echo "make insight-test   run the insight agent's tests"
	@echo "make insight-lint   ruff + basedpyright on the insight agent"

backend:
	uv run uvicorn vdagent_backend.app:app --host $(HOST) --port 8000

reset-db:
	rm -f $(BACKEND_DB) $(BACKEND_DB)-wal $(BACKEND_DB)-shm $(WAREHOUSE_DB) $(WAREHOUSE_DB)-wal $(WAREHOUSE_DB)-shm
	uv run python data/seed_warehouse.py $(WAREHOUSE_DB)
	uv run python data/seed_users.py $(BACKEND_DB)

# Insight agent (agents/insight/CLAUDE.md). ruff runs through uvx (config: agents/insight/pyproject.toml);
# basedpyright through `python -m` (its launcher can be blocked by Windows Application Control).
RUFF ?= uvx ruff@0.16.9

insight-test:
	uv run pytest agents/insight

insight-lint:
	$(RUFF) check agents/insight
	$(RUFF) format --check agents/insight
	uv run --with basedpyright==1.40.1 python -m basedpyright agents/insight
