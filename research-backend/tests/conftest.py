import os

# Test-only credentials; no production defaults.
os.environ["JWT_SECRET"] = "unit-tests-only-secret-with-at-least-32-characters"
os.environ["INTERNAL_SECRET"] = "unit-tests-only-internal-key-with-32-characters"
os.environ["COOKIE_SECURE"] = "false"
os.environ["FRONTEND_ORIGIN"] = "http://localhost:5173"

import pytest
from fastapi.testclient import TestClient
from research_core.config import settings
from research_core.db import Base, engine, session_factory
from research_core.models import Collection, User
from research_backend.main import create_app


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "private"))
    settings.cache_clear()
    engine.cache_clear()
    Base.metadata.create_all(engine())
    with session_factory()() as db:
        db.add(Collection(id=1, used_pages=0))
        db.commit()
    yield
    engine().dispose()
    for role in ("backend", "agent", "worker", "migration"):
        engine(role).dispose()
    engine.cache_clear()
    settings.cache_clear()


@pytest.fixture
def client(database, monkeypatch):
    monkeypatch.setattr("research_backend.documents.publish_ingestion", lambda job_id: None)
    with TestClient(create_app(), headers={"Origin": "http://localhost:5173"}) as client:
        yield client


def register(client, email="reader@example.com", admin=False):
    response = client.post("/auth/register", json={"email": email, "password": "test-password-123"})
    assert response.status_code == 201, response.text
    data = response.json()
    client.headers["Authorization"] = f"Bearer {data['access_token']}"
    client.headers["X-CSRF-Token"] = client.cookies["research_csrf"]
    if admin:
        with session_factory()() as db:
            db.get(User, data["user"]["id"]).role = "admin"
            db.commit()
    return data


def pdf_bytes(pages=1):
    # Real PDFium-generated PDF, parsed by the same validation path as uploaded papers.
    import io
    import pypdfium2 as pdfium
    with pdfium.PdfDocument.new() as doc:
        for _ in range(pages):
            page = doc.new_page(612, 792)
            page.close()
        output = io.BytesIO()
        doc.save(output)
        return output.getvalue()
