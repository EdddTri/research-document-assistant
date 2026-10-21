import hashlib
import logging
import shutil
from pathlib import Path

import pypdfium2 as pdfium
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from research_core.db import get_db
from research_core.models import Chunk, Collection, Content, Document, Job, now, uid
from research_core.storage import storage_path
from .auth import Principal, admin, current
from .queue import publish_ingestion

router = APIRouter()
log = logging.getLogger(__name__)


def reserve(db, pages):
    result = db.execute(update(Collection).where(Collection.id == 1)
                        .values(used_pages=Collection.used_pages + pages))
    if result.rowcount != 1:
        raise HTTPException(503, "Collection is not initialized; run the database migrations")


def release(db, doc):
    if doc.reserved:
        db.execute(update(Collection).where(Collection.id == 1).values(
            used_pages=Collection.used_pages - doc.page_count))
        doc.reserved = False


def enqueue(db, doc):
    latest = db.scalar(select(func.max(Job.created_at)).where(Job.document_id == doc.id)) or 0
    job = Job(document_id=doc.id, created_at=max(now(), latest + 1))
    db.add(job)
    db.flush()
    return job


def submit(db, doc, job):
    # Call only after commit: a fast worker must be able to see the saved job.
    try:
        publish_ingestion(job.id)
    except Exception:
        log.warning("Could not enqueue ingestion %s; manual retry is available", job.id)
        db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
        db.refresh(doc, with_for_update=True)
        db.refresh(job, with_for_update=True)
        # Delivery may have succeeded before a broker connection error. Do not
        # overwrite work that a consumer has already claimed or completed.
        if job.status == "queued":
            doc.status = job.status = "failed"
            job.stage = "queue unavailable"
            job.error = "Could not send this job to Redis. Start Redis, then choose Retry."
            release(db, doc)
        db.commit()
    db.refresh(doc)
    db.refresh(job)
    return describe(doc, job)


def describe(doc, job=None):
    return {"id": doc.id, "title": doc.title, "page_count": doc.page_count,
            "version": doc.version, "status": doc.status,
            "can_retry": doc.status in {"failed", "queued"} or (
                doc.status == "processing" and job is not None and job.lease_until <= now()),
            "job": {"id": job.id, "status": job.status, "stage": job.stage, "error": job.error} if job else None}


@router.get("/documents")
def documents(principal: Principal = Depends(current), db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).where(Document.status.not_in(["deleted", "superseded"])).order_by(Document.created_at))
    items = []
    for doc in docs:
        job = db.scalar(select(Job).where(Job.document_id == doc.id).order_by(Job.created_at.desc(), Job.id.desc()))
        item = describe(doc, job)
        if principal.user.role != "admin" and item["job"]:
            item["job"]["error"] = None
        items.append(item)
    collection = db.get(Collection, 1)
    return {"documents": items, "used_pages": collection.used_pages}


@router.post("/documents", status_code=201)
def upload(file: UploadFile, replaces_id: str | None = None,
           principal: Principal = Depends(admin), db: Session = Depends(get_db)):
    # UploadFile spools larger requests to disk. Validate, hash and copy the stream
    # without materializing the complete PDF as a Python bytes object.
    if file.file.read(5) != b"%PDF-":
        raise HTTPException(400, "A valid PDF is required")
    file.file.seek(0)
    try:
        with pdfium.PdfDocument(file.file) as pdf:
            pages = len(pdf)
            if pages < 1:
                raise ValueError("page count")
    except Exception:
        raise HTTPException(400, "PDF is invalid, encrypted, or empty")
    file.file.seek(0)
    sha = hashlib.file_digest(file.file, "sha256").hexdigest()
    # Collection lock serializes duplicate checks and page-count updates across uploads.
    db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
    existing = db.scalar(select(Document).where(Document.sha256 == sha,
                         Document.status.not_in(["deleted", "superseded"])))
    if existing:
        raise HTTPException(409, "This PDF already exists; retry or reprocess that document")
    if replaces_id:
        old = db.get(Document, replaces_id)
        if not old or old.status != "ready":
            raise HTTPException(409, "Only a ready document can be replaced")
        if db.scalar(select(Document).where(Document.replaces_id == old.id, Document.status.in_(["queued", "processing"]))):
            raise HTTPException(409, "A replacement is already pending")
    reserve(db, pages)
    version = (db.scalar(select(func.max(Document.version)).where(Document.sha256 == sha)) or 0) + 1
    doc_id = uid()
    key = f"pdfs/{doc_id}.pdf"
    doc = Document(id=doc_id, title=Path(file.filename or "Research paper.pdf").name[:240],
                   sha256=sha, version=version, page_count=pages, storage_key=key, replaces_id=replaces_id)
    try:
        path = storage_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        file.file.seek(0)
        with path.open("wb") as output:
            shutil.copyfileobj(file.file, output, length=1024 * 1024)
        db.add(doc)
        db.flush()
        job = enqueue(db, doc)
        db.commit()
    except Exception:
        db.rollback()
        storage_path(key).unlink(missing_ok=True)
        raise
    return submit(db, doc, job)


@router.post("/documents/{document_id}/retry")
def retry(document_id: str, principal: Principal = Depends(admin), db: Session = Depends(get_db)):
    db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
    doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if not doc or doc.status not in {"failed", "queued", "processing"}:
        raise HTTPException(409, "Only failed, queued or stalled documents can be retried")
    jobs = list(db.scalars(select(Job).where(Job.document_id == doc.id).with_for_update()))
    if any(job.status == "processing" and job.lease_until > now() for job in jobs):
        raise HTTPException(409, "This document is still being processed")
    for previous in jobs:
        if previous.status in {"queued", "processing"}:
            previous.status, previous.attempt_token = "cancelled", None
    if not doc.reserved:
        reserve(db, doc.page_count)
        doc.reserved = True
    doc.status = "queued"
    job = enqueue(db, doc)
    db.commit()
    return submit(db, doc, job)


@router.post("/documents/{document_id}/reprocess")
def reprocess(document_id: str, principal: Principal = Depends(admin), db: Session = Depends(get_db)):
    db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
    old = db.get(Document, document_id)
    if not old or old.status != "ready":
        raise HTTPException(409, "Only ready documents can be reprocessed")
    if db.scalar(select(Document).where(Document.replaces_id == old.id, Document.status.in_(["queued", "processing"]))):
        raise HTTPException(409, "A replacement is already pending")
    reserve(db, old.page_count)
    version = db.scalar(select(func.max(Document.version)).where(Document.sha256 == old.sha256)) + 1
    doc = Document(title=old.title, sha256=old.sha256, version=version, page_count=old.page_count,
                   storage_key=old.storage_key, replaces_id=old.id)
    db.add(doc)
    db.flush()
    job = enqueue(db, doc)
    db.commit()
    return submit(db, doc, job)


@router.delete("/documents/{document_id}", status_code=204)
def delete(document_id: str, principal: Principal = Depends(admin), db: Session = Depends(get_db)):
    db.scalar(select(Collection).where(Collection.id == 1).with_for_update())
    doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if not doc or doc.status == "deleted":
        raise HTTPException(404, "Document not found")
    if db.scalar(select(Document).where(Document.replaces_id == doc.id, Document.status.in_(["queued", "processing"]))):
        raise HTTPException(409, "Delete the pending replacement first")
    release(db, doc)
    doc.status = "deleted"
    for job in db.scalars(select(Job).where(Job.document_id == doc.id, Job.status.in_(["queued", "processing"]))):
        job.status, job.attempt_token = "cancelled", None
    # Tombstones retain citation provenance; protected routes deny all access.
    db.commit()


def authorized_document(db, doc_id, principal):
    doc = db.get(Document, doc_id)
    if not doc or doc.status == "deleted" or (doc.status not in {"ready", "superseded"} and principal.user.role != "admin"):
        raise HTTPException(404, "Document not available")
    return doc


@router.get("/documents/{document_id}/source")
def source(document_id: str, principal: Principal = Depends(current), db: Session = Depends(get_db)):
    doc = authorized_document(db, document_id, principal)
    return FileResponse(storage_path(doc.storage_key), media_type="application/pdf",
                        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"})


@router.get("/content/{content_id}/image")
def crop(content_id: str, principal: Principal = Depends(current), db: Session = Depends(get_db)):
    item = db.get(Content, content_id)
    if not item or not item.crop_key:
        raise HTTPException(404, "Image not available")
    authorized_document(db, item.document_id, principal)
    return FileResponse(storage_path(item.crop_key), media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/tables/{content_id}")
@router.get("/figures/{content_id}")
def content(content_id: str, principal: Principal = Depends(current), db: Session = Depends(get_db)):
    item = db.get(Content, content_id)
    if not item:
        raise HTTPException(404, "Content not found")
    authorized_document(db, item.document_id, principal)
    return {"id": item.id, "kind": item.kind, "caption": item.caption, "description": item.description,
            "data": item.data, "page": item.page, "bbox": item.bbox,
            "coordinate_system": "top-left PDF points", "page_width": item.page_width, "page_height": item.page_height,
            "image_url": f"/content/{item.id}/image" if item.crop_key else None}
