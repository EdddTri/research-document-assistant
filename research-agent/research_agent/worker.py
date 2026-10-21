import secrets
from time import perf_counter
from celery import Celery
from celery.utils.log import get_task_logger
from langsmith import traceable, tracing_context
from sqlalchemy import delete, select, update

from research_core.config import settings
from research_core.db import session_factory
from research_core.models import Chunk, Collection, Content, Document, Job, now
from .extraction import extract
from .vision import VisionError
from .usage import collect_usage

log = get_task_logger(__name__)
app = Celery("research", broker=settings().broker_url)
app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True,
                worker_prefetch_multiplier=1, task_ignore_result=True,
                broker_transport_options={"visibility_timeout": 3600},
                broker_connection_retry_on_startup=True)


class StaleAttempt(Exception):
    pass


class IngestionFailed(Exception):
    """Sanitized failure reported to Celery after updating durable job state."""
    pass


def claim(job_id):
    with session_factory("worker")() as db:
        db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
        job = db.get(Job, job_id)
        if not job:
            return None
        doc = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        db.refresh(job, with_for_update=True)
        if not job or job.status in {"ready", "failed", "cancelled"}:
            return None
        if job.lease_until > now():
            return None
        if not doc or doc.status not in {"queued", "processing"}:
            return None
        if job.attempts >= 3:
            job.status, job.stage, doc.status = "failed", "failed", "failed"
            job.error = "Worker attempts exhausted. Review provider availability and retry."
            release(db, doc)
            db.commit()
            return None
        token = secrets.token_hex(16)
        job.attempt_token, job.status, job.stage = token, "processing", "starting"
        job.attempts += 1
        job.lease_until = now() + 1560
        doc.status = "processing"
        db.commit()
        return doc, token, job.attempts


def progress(job_id, token, stage):
    with session_factory("worker")() as db:
        result = db.execute(update(Job).where(Job.id == job_id, Job.attempt_token == token,
                            Job.status == "processing").values(stage=stage, lease_until=now() + 1560))
        if result.rowcount != 1:
            raise StaleAttempt()
        db.commit()
    log.info("Ingestion %s: %s", job_id, stage)


def release(db, doc):
    if doc.reserved:
        db.execute(update(Collection).where(Collection.id == 1).values(used_pages=Collection.used_pages - doc.page_count))
        doc.reserved = False


def finalize(job_id, token, contents, chunks):
    with session_factory("worker")() as db:
        # Match public mutation lock order: collection, document, job.
        db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
        job = db.get(Job, job_id)
        doc = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        db.refresh(job, with_for_update=True)
        if job.attempt_token != token or job.status != "processing" or doc.status != "processing":
            raise StaleAttempt()
        db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        db.execute(delete(Content).where(Content.document_id == doc.id))
        db.add_all(contents)
        db.flush()
        db.add_all(chunks)
        doc.status, job.status, job.stage, job.error = "ready", "ready", "complete", None
        if doc.replaces_id:
            old = db.get(Document, doc.replaces_id)
            if old and old.status == "ready":
                old.status = "superseded"
                release(db, old)
        db.commit()


def fail(job_id, token, message="Ingestion failed. Check provider configuration, quota and PDF readability, then retry."):
    with session_factory("worker")() as db:
        db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
        job = db.get(Job, job_id)
        doc = db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        db.refresh(job, with_for_update=True)
        if job.attempt_token != token or job.status != "processing":
            return
        failed_stage = job.stage
        job.attempt_token = None
        job.status, job.stage, doc.status = "failed", "failed", "failed"
        # Provider exception bodies may contain source text, URLs or credentials.
        job.error = f"{failed_stage}: {message}"
        release(db, doc)
        db.commit()


@app.task(name="research.ingest")
def ingest(job_id):
    with tracing_context(enabled=settings().trace_document_content):
        return traced_ingest(job_id)


@traceable(name="ingest-document")
def traced_ingest(job_id):
    started = perf_counter()
    claimed = claim(job_id)
    if not claimed:
        return {"status": "skipped", "reason": "Job is not eligible for this delivery"}
    document, token, attempts = claimed
    log.info("Ingestion %s: started attempt %s (%s pages)", job_id, attempts, document.page_count)
    try:
        extraction_started = perf_counter()
        with collect_usage() as usage:
            contents, chunks = extract(document, token, lambda stage: progress(job_id, token, stage))
        extraction_seconds = perf_counter() - extraction_started
        indexing_started = perf_counter()
        finalize(job_id, token, contents, chunks)
        log.info("Ingestion %s: ready (%s chunks)", job_id, len(chunks))
        return {"status": "ready", "chunks": len(chunks), "contents": len(contents),
                "pages": document.page_count, "ingestion_seconds": perf_counter() - started,
                "extraction_and_embedding_seconds": extraction_seconds,
                "index_write_seconds": perf_counter() - indexing_started,
                "vector_payload_bytes": len(chunks) * settings().embedding_dimensions * 4,
                "usage": usage.summary().model_dump()}
    except StaleAttempt:
        log.info("Ingestion %s stopped: attempt was cancelled or replaced", job_id)
        return {"status": "skipped", "reason": "Attempt cancelled or replaced"}
    except Exception as exc:
        message = (str(VisionError(exc.code)) if isinstance(exc, VisionError)
                   else "Ingestion failed. Check provider configuration and PDF readability, then Retry.")
        log.warning("Ingestion %s failed (%s): %s", job_id, type(exc).__name__, message)
        fail(job_id, token, message)
        # Raising outside the handler below prevents provider exception chains from
        # appearing in Celery tracebacks (which may contain private response bodies).
    raise IngestionFailed(message) from None
