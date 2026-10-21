"""Static deployment/migration checks; does not replace a PostgreSQL/Compose smoke test."""
import io
import os
from pathlib import Path
import yaml
from alembic import command
from alembic.config import Config

os.environ.setdefault("JWT_SECRET", "deployment-validation-only-not-a-runtime-secret")
os.environ.setdefault("INTERNAL_SECRET", "deployment-validation-only-not-a-service-secret")
os.environ["DATABASE_URL"] = "postgresql+psycopg://migration:unused@localhost/research"
root = Path(__file__).resolve().parents[1]
manifest = yaml.safe_load((root / "research-backend/compose.yaml").read_text())
services = manifest["services"]
assert set(services) == {"frontend", "backend", "agent", "worker", "postgres", "redis", "migrate", "pgadmin"}
assert not services["agent"].get("ports")
assert not services["postgres"].get("ports")
assert "PAGE_CAP" not in services["backend"]["environment"]
assert "client_max_body_size 0;" in (root / "research-frontend/nginx.conf").read_text()
dev = yaml.safe_load((root / "research-backend/compose.dev.yaml").read_text())
assert set(dev["services"]) == {"postgres", "redis"}
assert dev["name"] != manifest["name"]
assert all(s["ports"][0].startswith("127.0.0.1:") for s in dev["services"].values())
assert "GROQ_API_KEY" not in services["backend"]["environment"]
assert "OPENAI_API_KEY" not in services["backend"]["environment"]
assert "DEEPSEEK_API_KEY" not in services["backend"]["environment"]
for service in ("agent", "worker"):
    environment = services[service]["environment"]
    assert environment["OPENAI_API_KEY"] == "${OPENAI_API_KEY:-}"
    assert not {"GROQ_API_KEY", "DEEPSEEK_API_KEY", "ANSWER_PROVIDER", "VISION_PROVIDER", "JUDGE_PROVIDER"} & environment.keys()
    assert environment["ANSWER_MODEL"] == "${ANSWER_MODEL:-gpt-5.4-mini}"
    assert environment["ANSWER_REASONING_EFFORT"] == "${ANSWER_REASONING_EFFORT-low}"
    assert environment["VISION_REASONING_EFFORT"] == "${VISION_REASONING_EFFORT-none}"
assert "host.docker.internal" in services["agent"]["environment"]["OLLAMA_BASE_URL"]
assert services["worker"]["environment"]["EMBEDDING_DIMENSIONS"] == "${EMBEDDING_DIMENSIONS:-1024}"
assert services["worker"]["environment"]["VISION_MODEL"] == "${VISION_MODEL:-gpt-5.4-mini}"
assert services["worker"]["environment"]["VISION_MAX_TOKENS"] == "${VISION_MAX_TOKENS:-4096}"
for key, default in (("VISION_SPLIT_MAX_DEPTH", "3"), ("VISION_SPLIT_MIN_SIDE", "192"), ("VISION_SPLIT_OVERLAP", "0.08")):
    assert services["worker"]["environment"][key] == "${" + key + ":-" + default + "}"
assert services["agent"]["environment"]["JWT_SECRET"].startswith("unused-")
buffer = io.StringIO()
config = Config(str(root / "research-backend/alembic.ini"), output_buffer=buffer)
command.upgrade(config, "head", sql=True)
sql = buffer.getvalue()
for required in ("CREATE SCHEMA IF NOT EXISTS app", "CREATE EXTENSION IF NOT EXISTS vector",
                 "VECTOR(1536)", "CREATE TABLE app.refresh_tokens", "CREATE TABLE knowledge.document_chunks",
                 "DROP TABLE app.external_identities",
                 "ALTER TABLE knowledge.document_chunks ALTER COLUMN embedding TYPE vector;",
                 "DROP CONSTRAINT collections_used_pages_check",
                 "ADD CONSTRAINT collections_used_pages_check CHECK (used_pages >= 0)"):
    assert required.lower() in sql.lower(), required
print(f"Compose structure and offline PostgreSQL migration SQL verified ({len(sql.splitlines())} SQL lines).")
