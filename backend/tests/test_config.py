"""Backend configuration (§4.1 of the connect-direction spec): hub address, agent tokens, `.env`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vdagent_backend.config import load_config, tokens_from_env

YAML = """\
backend_db: ./var/backend.db
warehouse_db: ./var/warehouse.db
mcp_public_url: http://localhost:8000/mcp
frontend_dist: ./frontend/dist
agents:
  orchestrator: {description: "Plans."}
  data:         {description: "Queries."}
"""


@pytest.fixture
def cfg_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # setenv first so monkeypatch restores "unset" even when load_dotenv sets the variable later.
    for var in ("VDAGENT_AGENT_LISTEN", "VDAGENT_AGENT_TOKEN_ORCHESTRATOR", "VDAGENT_AGENT_TOKEN_DATA", "VDAGENT_MAX_STEPS"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "repo" / "backend" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(YAML)
    return path


def test_agents_need_only_a_description_and_the_hub_listens_on_localhost(cfg_file: Path) -> None:
    cfg = load_config(cfg_file)
    assert cfg.agent_listen == "127.0.0.1:50050"
    assert {n: s.description for n, s in cfg.agents.items()} == {"orchestrator": "Plans.", "data": "Queries."}


def test_agent_listen_is_overridable_from_the_environment(cfg_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VDAGENT_AGENT_LISTEN", "0.0.0.0:6000")
    assert load_config(cfg_file).agent_listen == "0.0.0.0:6000"


def test_repo_root_env_file_is_loaded_but_process_env_wins(cfg_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (cfg_file.parent.parent / ".env").write_text(
        "VDAGENT_AGENT_TOKEN_DATA=from-file\nVDAGENT_AGENT_TOKEN_ORCHESTRATOR=file-orch\nVDAGENT_MAX_STEPS=7\n"
    )
    monkeypatch.setenv("VDAGENT_AGENT_TOKEN_ORCHESTRATOR", "from-process")
    cfg = load_config(cfg_file)
    assert cfg.max_steps == 7
    assert tokens_from_env(cfg.agents) == {"orchestrator": "from-process", "data": "from-file"}


def test_agent_without_a_token_is_left_out_with_a_warning(
    cfg_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VDAGENT_AGENT_TOKEN_ORCHESTRATOR", "secret-orch")
    cfg = load_config(cfg_file)
    with caplog.at_level(logging.WARNING):
        tokens = tokens_from_env(cfg.agents)
    assert tokens == {"orchestrator": "secret-orch"}
    assert "VDAGENT_AGENT_TOKEN_DATA" in caplog.text
    assert "secret-orch" not in caplog.text
