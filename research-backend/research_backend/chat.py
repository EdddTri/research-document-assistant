import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from research_core.config import settings
from research_core.contracts import Answer, AnswerRequest
from research_core.db import get_db
from research_core.models import Content, Conversation, Document, Message
from .auth import Principal, current

router = APIRouter()


def own(db, conversation_id, user_id):
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user_id:
        raise HTTPException(404, "Conversation not found")
    return conversation


@router.get("/conversations")
def conversations(p: Principal = Depends(current), db: Session = Depends(get_db)):
    return [{"id": c.id, "title": c.title} for c in db.scalars(select(Conversation).where(
        Conversation.user_id == p.user.id).order_by(Conversation.created_at.desc()))]


@router.post("/conversations", status_code=201)
def create(p: Principal = Depends(current), db: Session = Depends(get_db)):
    c = Conversation(user_id=p.user.id)
    db.add(c)
    db.commit()
    return {"id": c.id, "title": c.title}


@router.get("/conversations/{conversation_id}")
def messages(conversation_id: str, p: Principal = Depends(current), db: Session = Depends(get_db)):
    c = own(db, conversation_id, p.user.id)
    rows = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.sequence))
    return {"id": c.id, "title": c.title, "messages": [
        {"id": m.id, "role": m.role, "content": m.content} for m in rows]}


@router.delete("/conversations/{conversation_id}", status_code=204)
def remove(conversation_id: str, p: Principal = Depends(current), db: Session = Depends(get_db)):
    c = own(db, conversation_id, p.user.id)
    db.execute(delete(Message).where(Message.conversation_id == c.id))
    db.delete(c)
    db.commit()


class ChatInput(BaseModel):
    conversation_id: str
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(min_length=1, max_length=20)


@router.post("/chat", response_model=Answer)
def chat(body: ChatInput, p: Principal = Depends(current), db: Session = Depends(get_db)):
    c = own(db, body.conversation_id, p.user.id)
    ids = list(dict.fromkeys(body.document_ids))
    ready = list(db.scalars(select(Document.id).where(Document.id.in_(ids), Document.status == "ready")))
    if set(ready) != set(ids):
        raise HTTPException(409, "One or more selected papers are not ready")
    revision, conversation_id = c.revision, c.id
    history = list(db.scalars(select(Message).where(Message.conversation_id == c.id)
                             .order_by(Message.sequence.desc()).limit(12)))[::-1]
    payload = AnswerRequest(question=body.question, document_ids=ids,
                            history=[{"role": m.role, "content": {key: value for key, value in m.content.items()
                                                                 if key != "usage"}} for m in history])
    db.commit()  # No open transaction during remote model calls.
    try:
        with httpx.Client(timeout=httpx.Timeout(180, connect=5)) as client:
            response = client.post(f"{settings().agent_url}/internal/answers", json=payload.model_dump(),
                                   headers={"X-Internal-Key": settings().internal_secret})
            response.raise_for_status()
        answer = Answer.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "The research service could not answer. Please retry.")
    # Revalidate source scope and current document state before returning or saving.
    for citation in answer.citations:
        item = db.get(Content, citation.evidence_id)
        doc = db.get(Document, citation.document_id)
        if not item or not doc or doc.id not in ids or doc.status != "ready" or item.document_id != doc.id:
            raise HTTPException(409, "Source documents changed; ask again")
        citation.title, citation.version, citation.page = doc.title, doc.version, item.page
        citation.bbox = item.bbox
        citation.source_url = f"/documents/{doc.id}/source#page={item.page}"
    known = {c.evidence_id for c in answer.citations}
    for block in answer.blocks:
        if not set(block.evidence_ids) <= known or (block.content_id and block.content_id not in known):
            raise HTTPException(502, "Research service returned invalid evidence")
        if block.content_id:
            block.image_url = f"/content/{block.content_id}/image" if block.type == "image" else None
    result = db.execute(update(Conversation).where(Conversation.id == conversation_id,
                        Conversation.user_id == p.user.id, Conversation.revision == revision)
                        .values(revision=revision + 1, title=body.question[:80] if revision == 0 else c.title))
    if result.rowcount != 1:
        raise HTTPException(409, "Conversation changed while answering; please retry")
    db.add_all([
        Message(conversation_id=conversation_id, role="user", content={"text": body.question}, sequence=revision * 2),
        Message(conversation_id=conversation_id, role="assistant", content=answer.model_dump(), sequence=revision * 2 + 1),
    ])
    db.commit()
    return answer
