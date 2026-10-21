"""Isolated browser-test backend: real routes/DB/auth/ingestion; deterministic model responses.

Never loads .env or connects to the user's database, broker, storage or model accounts.
Only Playwright starts this localhost test server; it is not part of the application.
"""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

temporary = TemporaryDirectory(prefix="margin-browser-tests-")
root = Path(temporary.name)
os.environ.update(
    DATABASE_URL=f"sqlite:///{root / 'test.db'}",
    STORAGE_ROOT=str(root / "storage"),
    JWT_SECRET="browser-tests-only-signing-secret-32-characters",
    INTERNAL_SECRET="browser-tests-only-service-secret-32-characters",
    COOKIE_SECURE="false", FRONTEND_ORIGIN="http://localhost:15173",
    LANGSMITH_TRACING="false", TRACE_DOCUMENT_CONTENT="false", OPENAI_API_KEY="",
    LANGCHAIN_TRACING_V2="false", LANGSMITH_API_KEY="", BROKER_URL="memory://",
    AGENT_URL="http://127.0.0.1:9", OLLAMA_BASE_URL="http://127.0.0.1:9",
)

import httpx
from fastapi.responses import Response
from PIL import Image
from sqlalchemy import select
import uvicorn
from research_backend import auth, chat, documents
from research_backend.main import create_app
from research_core.contracts import Answer, Block, Citation
from research_core.db import Base, engine, session_factory
from research_core.models import Collection, Content, Document, User, uid
from research_core.storage import storage_path
from research_agent import extraction
from research_agent.worker import ingest


def pdf_bytes(label="Browser integration study"):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = f"BT /F1 12 Tf 30 700 Td ({label}. The controlled trial included 42 participants.) Tj ET".encode("ascii")
    objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    data, offsets = b"%PDF-1.4\n", [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def reset(papers: int = 2):
    Base.metadata.drop_all(engine())
    Base.metadata.create_all(engine())
    with session_factory()() as db:
        db.add(Collection(id=1, used_pages=papers))
        for role in ("admin", "reader"):
            db.add(User(email=f"{role}@example.com", role=role, password_hash=auth.hasher.hash("test-password-123")))
        for index in range(papers):
            identifier = uid()
            data = pdf_bytes(f"Study {index + 1}")
            key = f"pdfs/{identifier}.pdf"
            path = storage_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            doc = Document(id=identifier, title=f"Study {index + 1}.pdf", sha256=hashlib.sha256(data).hexdigest(),
                page_count=1, status="ready", storage_key=key)
            db.add(doc)
            db.flush()
            crop = f"crops/{identifier}/figure.png"
            crop_path = storage_path(crop)
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (120, 80), "white").save(crop_path)
            db.add(Content(document_id=identifier, kind="figure", page=1,
                text="The controlled trial included 42 participants.", caption="Study flow",
                data={}, bbox=[.1, .1, .8, .8], page_width=612, page_height=792,
                crop_key=crop, method="browser-test-fixture", processing_version="browser-test"))
        db.commit()
    return {"status": "reset", "papers": papers}


def model_reply(request):
    import json
    payload = json.loads(request.content)
    if "unavailable" in payload["question"].lower():
        answer = Answer(answerability="none", citations=[], blocks=[Block(type="limitation", text="The selected papers do not provide that information.")])
    else:
        with session_factory()() as db:
            item = db.scalar(select(Content).where(Content.document_id.in_(payload["document_ids"])))
            doc = db.get(Document, item.document_id)
            blocks = [Block(type="text", text="The study included 42 participants.", evidence_ids=[item.id])]
            if item.crop_key:
                blocks.append(Block(type="image", text="Study flow", content_id=item.id, evidence_ids=[item.id]))
            answer = Answer(answerability="full", blocks=blocks, retrieved_ids=[item.id], citations=[
                Citation(evidence_id=item.id, document_id=doc.id, title=doc.title, version=doc.version,
                         page=1, bbox=item.bbox, source_url=f"/documents/{doc.id}/source#page=1")])
    return httpx.Response(200, json=answer.model_dump(), request=request)


real_client = httpx.Client
chat.httpx = SimpleNamespace(Client=lambda **kw: real_client(transport=httpx.MockTransport(model_reply), **kw),
                             Timeout=httpx.Timeout, HTTPError=httpx.HTTPError)
extraction.embed = lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]
# Native-text test uploads use the real extraction and worker state transitions, without Redis dispatch.
documents.publish_ingestion = lambda job_id: ingest.run(job_id)
app = create_app()
app.post("/__test__/reset")(reset)


@app.get("/__test__/pdf")
def test_pdf():
    return Response(pdf_bytes("Uploaded browser study"), media_type="application/pdf")


if __name__ == "__main__":
    try:
        reset()
        uvicorn.run(app, host="127.0.0.1", port=18010, log_level="warning")
    finally:
        for role in ("backend", "agent", "worker", "migration"):
            engine(role).dispose()
        temporary.cleanup()
