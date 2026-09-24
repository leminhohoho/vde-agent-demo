# Local development. See README.md ("Makefile usage").

AGENTS := orchestrator data compare insight report

HOST         ?= 127.0.0.1
BACKEND_DB   ?= var/backend.db
WAREHOUSE_DB ?= var/warehouse.db

.DEFAULT_GOAL := help
AGENT_TARGETS := $(addprefix agent-,$(AGENTS))
.PHONY: help backend reset-db $(AGENT_TARGETS)

help:
	@echo "make backend        start the backend: HTTP on $(HOST):8000, agent hub per agent_listen (default 127.0.0.1:50050)"
	@echo "                    HOST=0.0.0.0 serves HTTP (UI, MCP) to other machines"
	@echo "make agent-<name>   start one agent from agents/<name>/ ($(AGENTS)); settings from agents/<name>/.env"
	@echo "make reset-db       delete and reseed $(BACKEND_DB) and $(WAREHOUSE_DB) (stop the stack first)"

backend:
	uv run uvicorn vdagent_backend.app:app --host $(HOST) --port 8000

# Static pattern rule: works with .PHONY, unlike a plain `agent-%` rule.
$(AGENT_TARGETS): agent-%:
	cd agents/$* && uv run python -m vdagent_$*

reset-db:
	rm -f $(BACKEND_DB) $(BACKEND_DB)-wal $(BACKEND_DB)-shm $(WAREHOUSE_DB) $(WAREHOUSE_DB)-wal $(WAREHOUSE_DB)-shm
	uv run python data/seed_warehouse.py $(WAREHOUSE_DB)
	uv run python data/seed_users.py $(BACKEND_DB)
