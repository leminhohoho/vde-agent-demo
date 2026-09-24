# Local development. See README.md ("Makefile usage").

AGENTS := orchestrator data compare insight report
# Must match the addresses in backend/config.yaml.
PORT_orchestrator := 50051
PORT_data         := 50052
PORT_compare      := 50053
PORT_insight      := 50054
PORT_report       := 50055

BACKEND_DB   ?= var/backend.db
WAREHOUSE_DB ?= var/warehouse.db

.DEFAULT_GOAL := help
AGENT_TARGETS := $(addprefix agent-,$(AGENTS))
.PHONY: help backend agents reset-db $(AGENT_TARGETS)

help:
	@echo "make backend        start the backend on :8000"
	@echo "make agent-<name>   start one agent ($(AGENTS))"
	@echo "make agents         start all agents in this terminal"
	@echo "make reset-db       delete and reseed $(BACKEND_DB) and $(WAREHOUSE_DB) (stop the stack first)"

backend:
	uv run uvicorn vdagent_backend.app:app --port 8000

# Static pattern rule: works with .PHONY, unlike a plain `agent-%` rule.
$(AGENT_TARGETS): agent-%:
	GRPC_PORT=$(PORT_$*) uv run python -m vdagent_$*

agents:
	$(MAKE) -j $(words $(AGENTS)) $(AGENT_TARGETS)

reset-db:
	rm -f $(BACKEND_DB) $(BACKEND_DB)-wal $(BACKEND_DB)-shm $(WAREHOUSE_DB) $(WAREHOUSE_DB)-wal $(WAREHOUSE_DB)-shm
	uv run python data/seed_warehouse.py $(WAREHOUSE_DB)
	uv run python data/seed_users.py $(BACKEND_DB)
