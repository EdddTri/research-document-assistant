import base64
import json
import math
from functools import lru_cache
import httpx
from openai import OpenAI
from research_core.config import settings
from langsmith.wrappers import wrap_openai
from .usage import record_usage


PROVIDER_URLS = {"openai": "https://api.openai.com/v1"}
PROVIDER_LABELS = {"openai": "OpenAI"}


@lru_cache
def generation_client(provider: str = "openai"):
    if provider not in PROVIDER_URLS:
        raise ValueError("Only OpenAI is supported for chat and vision")
    key = settings().openai_api_key
    if not key.strip():
        raise RuntimeError(f"{provider.upper()}_API_KEY is required")
    return OpenAI(api_key=key, base_url=PROVIDER_URLS[provider],
                  timeout=60, max_retries=2)


@lru_cache
def embeddings_client():
    return httpx.Client(base_url=settings().ollama_base_url.rstrip("/") + "/",
                        timeout=httpx.Timeout(120, connect=10), trust_env=False,
                        transport=httpx.HTTPTransport(retries=2))


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    cfg = settings()
    result = []
    for start in range(0, len(texts), cfg.embedding_batch_size):
        batch = texts[start:start + cfg.embedding_batch_size]
        response = embeddings_client().post("api/embed", json={
            "input": batch, "model": cfg.embedding_model, "truncate": False,
            "keep_alive": "10m",
        })
        response.raise_for_status()
        vectors = response.json().get("embeddings")
        if (not isinstance(vectors, list) or len(vectors) != len(batch)
                or any(not isinstance(v, list) or len(v) != cfg.embedding_dimensions
                       or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)
                       or not any(v) for v in vectors)):
            raise ValueError("Ollama returned incompatible or invalid embedding vectors")
        result.extend(vectors)
    return result


class ResponseError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Invalid model response: {code}")


def structured(schema, system: str, prompt: str, image: bytes | None = None, model: str | None = None,
               max_tokens: int = 8000, timeout: float = 60, reasoning_effort: str | None = None,
               provider: str | None = None):
    cfg = settings()
    provider = provider or (cfg.vision_provider if image is not None else cfg.answer_provider)
    if provider != "openai":
        raise ValueError("Only OpenAI is supported for chat and vision")
    model = model or (cfg.vision_model if image is not None else cfg.answer_model)
    content = prompt
    if image is not None:
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {
                       "url": "data:image/png;base64," + base64.b64encode(image).decode(), "detail": "high"}}]
    effort = reasoning_effort if reasoning_effort is not None else (
        cfg.vision_reasoning_effort if image is not None else cfg.answer_reasoning_effort)
    options = {"max_completion_tokens": max_tokens}
    if effort is not None:
        options["reasoning_effort"] = effort
    # Omit sampling overrides for reasoning-model compatibility.
    client = generation_client(provider).with_options(timeout=timeout)
    if cfg.trace_document_content:
        client = wrap_openai(client)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system + "\nReturn only JSON matching this schema:\n" +
                       json.dumps(schema.model_json_schema())}, {"role": "user", "content": content}],
            response_format={"type": "json_object"}, **options)
    except Exception:
        record_usage(None, model)  # A failed request may have no observable billing usage.
        raise
    record_usage(response, model)  # Even truncated or invalid JSON responses consume tokens.
    if not response.choices:
        raise ResponseError("empty")
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise ResponseError("truncated")
    if choice.finish_reason != "stop":
        raise ResponseError("incomplete")
    if not choice.message.content or not choice.message.content.strip():
        raise ResponseError("empty")
    try:
        return schema.model_validate_json(choice.message.content)
    except ValueError:
        raise ResponseError("format") from None
