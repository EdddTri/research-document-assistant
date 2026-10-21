"""Export contracts without connecting to a database or loading real secrets."""
import json
import os
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "schema-export-only-not-a-runtime-signing-secret")
os.environ.setdefault("INTERNAL_SECRET", "schema-export-only-not-a-runtime-service-secret")

from research_backend.main import app

path = Path(__file__).resolve().parents[1] / "research-backend" / "openapi.json"
path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"Exported {len(app.openapi()['paths'])} public API paths.")
