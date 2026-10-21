import pytest
import hashlib
from sqlalchemy import select
from research_core.db import session_factory
from research_core.models import Collection, Content, Document, Job
from research_agent.worker import StaleAttempt, claim, fail, finalize, progress
from research_agent import worker
from research_agent.vision import VisionError
from research_core.storage import storage_path
from conftest import register, pdf_bytes


def upload(client, pages=1):
    response = client.post("/documents", files={"file": ("paper.pdf", pdf_bytes(pages), "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def test_progress_renews_lease_for_long_local_ocr_jobs(client, monkeypatch):
    register(client, admin=True)
    doc = upload(client)
    _, token, _ = claim(doc["job"]["id"])
    monkeypatch.setattr("research_agent.worker.now", lambda: 10000)
    progress(doc["job"]["id"], token, "OpenAI vision page 50")
    with session_factory()() as db:
        assert db.get(Job, doc["job"]["id"]).lease_until == 11560
    with pytest.raises(StaleAttempt):
        progress(doc["job"]["id"], "stale-token", "must not renew")


@pytest.mark.parametrize("error", [VisionError("truncated"), RuntimeError("PRIVATE provider text and credential")])
def test_failed_task_reports_safe_reason_and_is_not_a_celery_success(client, monkeypatch, caplog, error):
    register(client, admin=True)
    doc = upload(client)
    def broken(document, token, report):
        report("OpenAI vision vision page 3/16: embedded image")
        raise error
    monkeypatch.setattr(worker, "extract", broken)
    with pytest.raises(worker.IngestionFailed) as caught:
        worker.ingest(doc["job"]["id"])
    assert caught.value.__context__ is None
    with session_factory()() as db:
        job = db.get(Job, doc["job"]["id"])
        assert job.status == "failed"
        assert "page 3/16" in job.error
        assert "PRIVATE" not in job.error
        if isinstance(error, VisionError):
            assert "token limit" in job.error
            assert "OpenAI vision" in job.error and "{provider}" not in job.error
    assert "PRIVATE" not in str(caught.value) and "PRIVATE" not in caplog.text


def test_ineligible_delivery_is_reported_as_skipped(database):
    assert worker.ingest("missing-job")["status"] == "skipped"


def test_invalid_pdf_and_unlimited_page_counts(client):
    register(client, admin=True)
    assert client.post("/documents", files={"file": ("fake.pdf", b"not a pdf")}).status_code == 400
    first = upload(client, 30)
    upload(client, 25)
    collection = client.get("/documents").json()
    assert collection["used_pages"] == 55
    assert "page_cap" not in collection
    _, token, _ = claim(first["job"]["id"])
    finalize(first["job"]["id"], token, [], [])
    assert client.post(f"/documents/{first['id']}/reprocess").status_code == 200
    assert client.get("/documents").json()["used_pages"] == 85


def test_duplicates_do_not_consume_pages(client):
    register(client, admin=True)
    payload = pdf_bytes()
    for expected in (201, 409):
        assert client.post("/documents", files={"file": ("same.pdf", payload)}).status_code == expected
    assert client.get("/documents").json()["used_pages"] == 1


def test_pdf_over_previous_size_limit_is_stored_completely(client):
    register(client, admin=True)
    # A PDF comment before startxref preserves all object offsets and keeps the
    # final EOF marker near the end, while exceeding both former size ceilings.
    payload = pdf_bytes().replace(b"startxref", b"%" + b"x" * (27 * 1024 * 1024) + b"\nstartxref")
    response = client.post("/documents", files={"file": ("large.pdf", payload, "application/pdf")})
    assert response.status_code == 201, response.text
    with session_factory()() as db:
        doc = db.get(Document, response.json()["id"])
        path = storage_path(doc.storage_key)
        assert path.stat().st_size == len(payload)
        with path.open("rb") as stored:
            assert hashlib.file_digest(stored, "sha256").hexdigest() == hashlib.sha256(payload).hexdigest() == doc.sha256
    assert client.post("/documents", files={"file": ("duplicate.pdf", payload)}).status_code == 409
    assert client.get("/documents").json()["used_pages"] == 1


def test_upload_publishes_after_database_commit(client, monkeypatch):
    register(client, admin=True)
    published = []
    def publish(job_id):
        with session_factory()() as db:
            job = db.get(Job, job_id)
            assert job and db.get(Document, job.document_id)
        published.append(job_id)
    monkeypatch.setattr("research_backend.documents.publish_ingestion", publish)
    doc = upload(client)
    assert published == [doc["job"]["id"]]


def test_broker_failure_preserves_pdf_and_can_be_retried(client, monkeypatch):
    register(client, admin=True)
    def unavailable(job_id):
        raise ConnectionError("not included in UI")
    monkeypatch.setattr("research_backend.documents.publish_ingestion", unavailable)
    doc = upload(client, 2)
    assert doc["status"] == "failed" and doc["can_retry"]
    assert "Redis" in doc["job"]["error"]
    assert client.get("/documents").json()["used_pages"] == 0
    assert client.get(f"/documents/{doc['id']}/source").status_code == 200
    published = []
    monkeypatch.setattr("research_backend.documents.publish_ingestion", published.append)
    retried = client.post(f"/documents/{doc['id']}/retry")
    assert retried.status_code == 200
    assert published == [retried.json()["job"]["id"]]
    assert client.get("/documents").json()["used_pages"] == 2


def test_manual_recovery_cancels_old_delivery_and_fences_expired_attempt(client):
    register(client, admin=True)
    doc = upload(client)
    retried = client.post(f"/documents/{doc['id']}/retry").json()
    assert claim(doc["job"]["id"]) is None
    _, token, _ = claim(retried["job"]["id"])
    assert client.post(f"/documents/{doc['id']}/retry").status_code == 409
    with session_factory()() as db:
        db.get(Job, retried["job"]["id"]).lease_until = 0
        db.commit()
    assert client.get("/documents").json()["documents"][0]["can_retry"]
    assert client.post(f"/documents/{doc['id']}/retry").status_code == 200
    with pytest.raises(StaleAttempt):
        finalize(retried["job"]["id"], token, [], [])
    assert client.get("/documents").json()["used_pages"] == 1


def test_uncertain_publish_does_not_fail_already_claimed_job(client, monkeypatch):
    register(client, admin=True)
    def accepted_then_connection_lost(job_id):
        assert claim(job_id)
        raise ConnectionError("acknowledgement lost")
    monkeypatch.setattr("research_backend.documents.publish_ingestion", accepted_then_connection_lost)
    doc = upload(client, 2)
    assert doc["status"] == "processing"
    assert doc["job"]["error"] is None
    assert not doc["can_retry"]
    assert client.get("/documents").json()["used_pages"] == 2


def test_redelivery_failure_and_retry_reservations(client):
    register(client, admin=True)
    doc = upload(client, 3)
    job_id = doc["job"]["id"]
    _, token, _ = claim(job_id)
    assert claim(job_id) is None
    fail(job_id, token)
    assert client.get("/documents").json()["used_pages"] == 0
    fail(job_id, token)
    assert client.get("/documents").json()["used_pages"] == 0
    retried = client.post(f"/documents/{doc['id']}/retry")
    assert retried.status_code == 200
    assert client.get("/documents").json()["used_pages"] == 3
    assert claim(job_id) is None


def test_deleted_document_rejects_late_worker_completion(client):
    register(client, admin=True)
    doc = upload(client)
    _, token, _ = claim(doc["job"]["id"])
    assert client.delete(f"/documents/{doc['id']}").status_code == 204
    with pytest.raises(StaleAttempt):
        finalize(doc["job"]["id"], token, [], [])
    assert client.get("/documents").json()["used_pages"] == 0
    assert client.get(f"/documents/{doc['id']}/source").status_code == 404


def test_success_is_idempotent_and_new_version_publishes_atomically(client):
    register(client, admin=True)
    original = upload(client, 2)
    _, token, _ = claim(original["job"]["id"])
    finalize(original["job"]["id"], token, [], [])
    assert claim(original["job"]["id"]) is None
    replacement = client.post(f"/documents/{original['id']}/reprocess").json()
    assert replacement["version"] == 2
    assert client.get("/documents").json()["used_pages"] == 4
    with session_factory()() as db:
        assert db.get(Document, original["id"]).status == "ready"
    _, token, _ = claim(replacement["job"]["id"])
    finalize(replacement["job"]["id"], token, [], [])
    with session_factory()() as db:
        assert db.get(Document, original["id"]).status == "superseded"
        assert db.get(Document, replacement["id"]).status == "ready"
    assert client.get("/documents").json()["used_pages"] == 2


def test_reader_cannot_read_pending_sources(client):
    register(client, admin=True)
    doc = upload(client)
    register(client, "reader2@example.com")
    assert client.get(f"/documents/{doc['id']}/source").status_code == 404


def test_private_storage_rejects_path_traversal(database):
    with pytest.raises(ValueError):
        storage_path("../secrets.txt")


def test_failure_waits_for_manual_retry(client):
    register(client, admin=True)
    doc = upload(client)
    _, token, _ = claim(doc["job"]["id"])
    fail(doc["job"]["id"], token)
    assert claim(doc["job"]["id"]) is None
    assert client.get("/documents").json()["used_pages"] == 0


def test_repeated_worker_crashes_are_bounded(client):
    register(client, admin=True)
    doc = upload(client)
    for _ in range(3):
        assert claim(doc["job"]["id"]) is not None
        with session_factory()() as db:
            db.get(Job, doc["job"]["id"]).lease_until = 0
            db.commit()
    assert claim(doc["job"]["id"]) is None
    assert client.get("/documents").json()["used_pages"] == 0
