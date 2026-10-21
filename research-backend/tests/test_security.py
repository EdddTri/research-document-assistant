from fastapi.testclient import TestClient
from sqlalchemy import select
from research_backend.main import create_app
from research_core.db import session_factory
from research_core.models import LoginSession, User
from conftest import register, pdf_bytes


def test_reader_cannot_upload_and_anonymous_cannot_read(client):
    assert client.get("/documents").status_code == 401
    register(client)
    assert client.post("/documents", files={"file": ("paper.pdf", pdf_bytes(), "application/pdf")}).status_code == 403
    assert client.post("/auth/register", json={"email": "admin@example.com", "password": "test-password-123", "role": "admin"}).json()["user"]["role"] == "reader"


def test_logout_revokes_access_immediately(client):
    register(client)
    assert client.get("/users/me").status_code == 200
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/users/me").status_code == 401


def test_refresh_rotation_and_reuse_revokes_family(client):
    register(client)
    old = client.cookies["research_refresh"]
    assert client.post("/auth/refresh").status_code == 200
    assert client.cookies["research_refresh"] != old
    response = client.post("/auth/refresh", headers={"Cookie": f"research_refresh={old}"})
    assert response.status_code == 401
    assert "reuse" in response.text
    assert client.get("/users/me").status_code == 401


def test_csrf_and_origin_required(client):
    register(client)
    assert client.post("/auth/refresh", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/auth/refresh", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/auth/logout", headers={"X-CSRF-Token": ""}).status_code == 403


def test_switching_accounts_revokes_previous_browser_session(client):
    first = register(client)
    register(client, "second@example.com")
    assert client.get("/users/me", headers={"Authorization": f"Bearer {first['access_token']}"}).status_code == 401


def test_conversations_are_private_even_to_admin(client):
    register(client)
    chat = client.post("/conversations").json()
    register(client, "admin@example.com", admin=True)
    assert client.get(f"/conversations/{chat['id']}").status_code == 404
    assert client.delete(f"/conversations/{chat['id']}").status_code == 404
    assert client.get("/conversations").json() == []


def test_role_and_account_status_are_checked_on_every_request(client):
    user = register(client, admin=True)
    with session_factory()() as db:
        db.get(User, user["user"]["id"]).active = False
        db.commit()
    assert client.get("/documents").status_code == 401


def test_separate_devices_have_independent_sessions(client):
    register(client)
    with TestClient(create_app(), headers={"Origin": "http://localhost:5173"}) as device:
        response = device.post("/auth/login", json={"email": "reader@example.com", "password": "test-password-123"})
        assert response.status_code == 200
        device.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        assert len(client.get("/auth/sessions").json()) == 2
        assert client.post("/auth/logout").status_code == 204
        assert device.get("/users/me").status_code == 200


def test_foreign_session_cannot_be_revoked(client):
    first = register(client)
    session_id = client.get("/auth/sessions").json()[0]["id"]
    register(client, "other@example.com")
    assert client.delete(f"/auth/sessions/{session_id}").status_code == 404


def test_forged_jwt_rejected(client):
    register(client)
    assert client.get("/users/me", headers={"Authorization": "Bearer made.up.token"}).status_code == 401


def test_expired_access_token_rejected(client):
    import jwt
    from research_core.config import settings
    from research_core.models import now
    response = register(client)
    claims = jwt.decode(response["access_token"], settings().jwt_secret, algorithms=["HS256"],
                        audience=settings().jwt_audience, issuer=settings().jwt_issuer)
    claims["exp"] = now() - 1
    expired = jwt.encode(claims, settings().jwt_secret, algorithm="HS256")
    assert client.get("/users/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_only_password_authentication_is_exposed(client):
    for path in ("/auth/providers", "/auth/oidc/google/login", "/auth/oidc/google/callback",
                 "/auth/oidc/microsoft/login", "/auth/oidc/microsoft/callback"):
        assert client.get(path).status_code == 404
    paths = client.get("/openapi.json").json()["paths"]
    assert not any("oidc" in path or "providers" in path for path in paths)
    assert "/auth/login" in paths
    assert "/auth/register" in paths


def test_demo_password_registration_and_direct_sign_in(client):
    credentials = {"email": "demo@example.com", "password": "demo12"}
    assert client.post("/auth/register", json={**credentials, "password": "short"}).status_code == 422
    registered = client.post("/auth/register", json=credentials)
    assert registered.status_code == 201
    client.headers["X-CSRF-Token"] = client.cookies["research_csrf"]
    assert client.post("/auth/logout").status_code == 204
    signed_in = client.post("/auth/login", json=credentials)
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["id"] == registered.json()["user"]["id"]
    assert client.get("/users/me", headers={
        "Authorization": f"Bearer {signed_in.json()['access_token']}"
    }).status_code == 200
