import secrets
from fastapi import Depends, FastAPI, Header, HTTPException
from langsmith import tracing_context
from research_core.config import settings
from research_core.contracts import Answer, AnswerRequest
from .answering import answer

app = FastAPI(title="Private Research Agent", version="1.0.0", docs_url=None, redoc_url=None)


def internal(x_internal_key: str = Header(default="")):
    if not secrets.compare_digest(x_internal_key, settings().internal_secret):
        raise HTTPException(401, "Invalid service credential")


@app.post("/internal/answers", response_model=Answer, dependencies=[Depends(internal)])
def answers(request: AnswerRequest):
    try:
        with tracing_context(enabled=settings().trace_document_content):
            return answer(request)
    except Exception:
        raise HTTPException(503, "Research provider unavailable or returned invalid data")


@app.get("/health")
def health():
    return {"status": "ok"}
