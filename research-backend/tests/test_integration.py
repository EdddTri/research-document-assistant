import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from research_core.contracts import Claim, Draft, Layout, Region
from research_core.db import session_factory
from research_core.models import Chunk, Content
from research_agent import answering, extraction
from research_agent.worker import ingest
from research_agent.main import app as agent_app
from research_backend import chat
from conftest import register


def research_pdf(extra_stream=b"", text=b"The research study included 42 participants. The method uses a controlled trial.", image=False):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 50 700 Td (" + text + b") Tj ET\n" + extra_stream
    if image:
        objects[2] = objects[2].replace(b"/Resources <<", b"/Resources << /XObject << /Im1 6 0 R >>")
        stream += b"\nq 120 0 0 100 100 300 cm /Im1 Do Q"
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    if image:
        objects.append(b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 12 >>\nstream\n" + b"\x00" * 12 + b"\nendstream")
    data, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def test_real_pdf_upload_to_cited_answer_and_protected_image(client, monkeypatch):
    register(client, admin=True)
    layout = Layout(regions=[
        Region(kind="table", bbox=[.1, .3, .8, .5], caption="Participant count",
               headers=["Participants"], rows=[["42"]]),
        Region(kind="figure", bbox=[.1, .6, .8, .8], caption="Study flow", description="A controlled trial."),
    ])
    monkeypatch.setattr(extraction, "recognize", lambda *args, **kwargs: layout)
    monkeypatch.setattr(extraction, "embed", lambda texts: [[1.] + [0.] * 1023 for _ in texts])
    monkeypatch.setattr(answering, "embed", lambda texts: [[1.] + [0.] * 1023 for _ in texts])
    diagram = b"100 300 m 200 300 l 150 400 l h S"
    response = client.post("/documents", files={"file": ("study.pdf", research_pdf(extra_stream=diagram), "application/pdf")})
    assert response.status_code == 201, response.text
    document = response.json()
    ingest(document["job"]["id"])
    assert client.get("/documents").json()["documents"][0]["status"] == "ready"
    with session_factory()() as db:
        items = list(db.scalars(select(Content).where(Content.document_id == document["id"])))
        assert {item.kind for item in items} == {"text", "table", "figure"}
        assert all(item.page == 1 for item in items)
        assert len(list(db.scalars(select(Chunk)))) == 3
        # Old vectors survive migration but must never enter new-model retrieval.
        text_content = next(item for item in items if item.kind == "text")
        for identifier, model, dimensions in (("old-space", "text-embedding-3-large", 1536),
                                               ("bad-dimensions", "bge-m3", 1536)):
            db.add(Chunk(id=identifier, document_id=document["id"], content_id=text_content.id,
                         text="legacy sentinel", embedding=[1.] + [0.] * (dimensions - 1),
                         embedding_model=model, processing_version="legacy"))
        db.commit()
    def checked_cosine(a, b):
        assert len(a) == len(b) == 1024
        return 1.0
    monkeypatch.setattr(answering, "cosine", checked_cosine)
    def draft(schema, system, prompt, **kwargs):
        evidence = json.loads(prompt)["evidence"]
        text = next(item for item in evidence if item["kind"] == "text")
        table = next(item for item in evidence if item["kind"] == "table")
        return Draft(answerability="full",
            claims=[Claim(text="The study included 42 participants.", evidence_ids=[text["id"]])],
            display_ids=[table["id"]])
    monkeypatch.setattr(answering, "structured", draft)
    agent = TestClient(agent_app)

    class AgentConnection:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs): return agent.post("/internal/answers", **kwargs)

    monkeypatch.setattr(chat, "httpx", SimpleNamespace(Client=AgentConnection, Timeout=httpx.Timeout, HTTPError=httpx.HTTPError))
    conversation = client.post("/conversations").json()
    response = client.post("/chat", json={"conversation_id": conversation["id"], "question": "How many participants?",
                                          "document_ids": [document["id"]]})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["answerability"] == "full"
    assert answer["citations"][0]["title"] == "study.pdf"
    assert any(block["type"] == "table" and block["rows"] == [["42"]] for block in answer["blocks"])
    image = next(block["image_url"] for block in answer["blocks"] if block["type"] == "image")
    assert client.get(image).headers["content-type"] == "image/png"
    messages = client.get(f"/conversations/{conversation['id']}").json()["messages"]
    assert len(messages) == 2
    assert messages[-1]["content"]["usage"] == answer["usage"]
    assert answer["usage"]["estimated_cost_usd"] == 0  # This test mocks all paid model calls.
    client.post("/auth/logout")
    assert client.get(image).status_code == 401


def test_private_agent_requires_service_authentication(client):
    with TestClient(agent_app) as agent:
        response = agent.post("/internal/answers", json={"question": "Hello?", "document_ids": []})
        assert response.status_code == 401
