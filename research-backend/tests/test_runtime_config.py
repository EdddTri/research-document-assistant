from pathlib import Path

from dotenv import dotenv_values
import pytest
from sqlalchemy.engine import make_url

from research_core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_root_template_derives_restricted_database_roles():
    values = dotenv_values(ROOT / ".env.example")
    cfg = Settings(**{k.lower(): v for k, v in values.items()})
    for role, key in [("backend", "BACKEND_DB_PASSWORD"), ("agent", "AGENT_DB_PASSWORD"),
                      ("worker", "WORKER_DB_PASSWORD"), ("migration", "POSTGRES_PASSWORD")]:
        url = make_url(cfg.database_url_for(role))
        assert url.username == f"research_{role}" and url.host == "127.0.0.1"
        assert url.password == values[key]
    assert cfg.broker_url == "redis://127.0.0.1:6379/0"
    assert "DATABASE_URL" not in values
    assert set(dotenv_values(ROOT / "research-frontend/.env.example")) == {"VITE_API_BASE_URL"}


def test_database_passwords_are_encoded_and_ports_are_shared():
    password = "a@b:/?#% c"
    cfg = Settings(backend_db_password=password, db_port=5434, redis_port=6381)
    url = make_url(cfg.database_url_for("backend"))
    assert url.password == password and url.port == 5434
    assert cfg.broker_url == "redis://127.0.0.1:6381/0"


def test_explicit_database_url_remains_available_for_containers_and_tests():
    cfg = Settings(database_url="sqlite:///test.db")
    for role in ("backend", "agent", "worker", "migration"):
        assert cfg.database_url_for(role) == "sqlite:///test.db"
    with pytest.raises(ValueError, match="Unknown database role"):
        cfg.database_url_for("unknown")


def test_missing_role_password_has_actionable_error():
    with pytest.raises(ValueError, match="AGENT_DB_PASSWORD"):
        Settings().database_url_for("agent")


def test_settings_do_not_implicitly_read_unselected_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=must-not-be-loaded\n")
    assert Settings().openai_api_key == ""
