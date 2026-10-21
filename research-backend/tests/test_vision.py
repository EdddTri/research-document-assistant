import base64
import json
from types import SimpleNamespace

import httpx
from openai import OpenAI, RateLimitError
import pytest

from research_agent import answering, extraction, providers, vision
from research_core.config import settings
from research_core.contracts import AnswerRequest, Claim, Draft, Layout
from research_core.db import session_factory
from research_core.models import ExtractionCache


def mock_openai(monkeypatch, content, finish="stop", status=200):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=(
            {"id": "test", "object": "chat.completion", "created": 0, "model": "test",
             "choices": [{"index": 0, "finish_reason": finish,
                          "message": {"role": "assistant", "content": content}}]}
            if status == 200 else {"error": {"message": "PRIVATE provider response and credential"}}))
    client = OpenAI(api_key="unit-test-only", base_url="https://api.openai.com/v1", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(providers, "generation_client", lambda provider: client)
    return requests


def test_vision_sends_image_to_openai_with_schema_and_configured_budget(database, monkeypatch):
    requests = mock_openai(monkeypatch, json.dumps({"transcription": "Participants: 42", "regions": []}))
    assert vision.recognize(b"image bytes").transcription == "Participants: 42"
    assert len(requests) == 1 and requests[0].url.host == "api.openai.com"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["max_completion_tokens"] == settings().vision_max_tokens
    assert payload["reasoning_effort"] == "none"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][1]["content"][1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"image bytes").decode())


@pytest.mark.parametrize("content,finish,code", [
    ("", "stop", "empty"), ("{}", "stop", "format"),
    ('{"transcription":"partial","regions":[]}', "length", "truncated"),
    ('{"transcription":"partial","regions":[]}', "content_filter", "incomplete"),
    ("PRIVATE invalid json", "stop", "format"),
    (json.dumps({"transcription": "Figure 1 shows the inputs and outputs of the proposed research method. " * 80,
                 "regions": []}), "stop", "repetition"),
])
def test_invalid_vision_never_becomes_evidence_or_triggers_alternate_prompt(database, monkeypatch, content, finish, code):
    requests = mock_openai(monkeypatch, content, finish)
    with pytest.raises(vision.VisionError) as caught:
        vision.recognize(b"image")
    assert caught.value.code == code
    assert caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("status,code", [(429, "rate_limit"), (401, "authentication"), (403, "provider"), (500, "provider")])
def test_provider_failures_are_sanitized(database, monkeypatch, status, code):
    mock_openai(monkeypatch, None, status=status)
    with pytest.raises(vision.VisionError) as caught:
        vision.recognize(b"image")
    assert caught.value.code == code and caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value)


def test_impossible_quota_request_is_distinguished_from_temporary_throttling(database, monkeypatch):
    def reject(*args, **kwargs):
        raise RateLimitError("PRIVATE", response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")),
                             body={"message": "Request too large for PRIVATE account"})
    monkeypatch.setattr(vision, "structured", reject)
    with pytest.raises(vision.VisionError) as caught:
        vision.recognize(b"image")
    assert caught.value.code == "quota_request_size"
    assert "Waiting alone will not fix" in str(caught.value)
    assert "PRIVATE" not in str(caught.value) and caught.value.__context__ is None


def test_insufficient_quota_reports_billing_without_splitting(database, monkeypatch):
    def reject(*args, **kwargs):
        raise RateLimitError("PRIVATE", response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")),
                             body={"code": "insufficient_quota", "message": "PRIVATE billing information"})
    monkeypatch.setattr(vision, "structured", reject)
    with pytest.raises(vision.VisionError) as caught:
        vision.recognize(b"image")
    assert caught.value.code == "quota" and caught.value.__context__ is None
    assert "billing" in str(caught.value) and "PRIVATE" not in str(caught.value)


def test_optional_pacing_spaces_real_requests_without_delaying_the_first(database, monkeypatch):
    clock, starts, sleeps = [100.], [], []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    def recognize(*args, **kwargs):
        starts.append(clock[0])
        return Layout(transcription="42")
    monkeypatch.setattr(vision, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(vision, "_next_request_at", 0.)
    monkeypatch.setattr(settings(), "vision_min_interval_seconds", 60)
    monkeypatch.setattr(vision, "structured", recognize)
    vision.recognize(b"one")
    clock[0] += 5
    vision.recognize(b"two")
    assert starts == [100., 160.] and sleeps == [55.]


def test_decorative_crop_can_explicitly_return_no_evidence(database, monkeypatch):
    mock_openai(monkeypatch, '{"transcription":"","regions":[]}')
    assert vision.recognize(b"blank image").regions == []


def test_text_free_image_retains_supported_description(database, monkeypatch):
    mock_openai(monkeypatch, json.dumps({"transcription": "", "regions": [
        {"kind": "figure", "bbox": [0, 0, 1, 1], "description": "Two boxes connected by an arrow."}]}))
    assert "arrow" in vision.recognize(b"diagram").regions[0].description


def test_text_answers_do_not_attach_images(database, monkeypatch):
    requests = mock_openai(monkeypatch, '{"transcription":"42","regions":[]}')
    providers.structured(Layout, "test", "test")
    payload = json.loads(requests[0].content)
    assert payload["model"] == settings().answer_model
    assert payload["messages"][1]["content"] == "test"
    assert payload["reasoning_effort"] == "low"


def test_openai_vision_cache_separates_models_budgets_and_legacy_ollama(database, monkeypatch):
    import hashlib
    calls = []
    cfg = settings()
    legacy = f"ollama/{cfg.vision_model}/unlimited-ocr-3-empty-response-retry/layout-5-region-ocr/chunks-2/8192/6144"
    with session_factory()() as db:
        db.add(ExtractionCache(key=hashlib.sha256(b"page" + legacy.encode()).hexdigest(),
                               data=Layout(transcription="old OCR").model_dump()))
        db.commit()
    monkeypatch.setattr(extraction, "recognize", lambda image: calls.append(image) or Layout(transcription="OpenAI 42"))
    assert extraction.layout_for(b"page", "native").transcription == "OpenAI 42"
    extraction.layout_for(b"page", "native")
    assert len(calls) == 1
    monkeypatch.setattr(cfg, "vision_max_tokens", 8192)
    extraction.layout_for(b"page", "native")
    monkeypatch.setattr(cfg, "vision_model", "another-vision-model")
    extraction.layout_for(b"page", "native")
    assert len(calls) == 3


def test_failed_vision_is_not_cached(database, monkeypatch):
    def fail(image):
        raise vision.VisionError("rate_limit")
    monkeypatch.setattr(extraction, "recognize", fail)
    with pytest.raises(vision.VisionError):
        extraction.layout_for(b"page", "")
    with session_factory()() as db:
        assert db.query(ExtractionCache).count() == 0


def test_preflight_reports_vision_error_without_claiming_success(database, monkeypatch, capsys):
    import sys
    from research_agent import check_providers
    monkeypatch.setattr(sys, "argv", ["check_providers", "--vision-only"])
    def fail(_):
        raise vision.VisionError("rate_limit")
    monkeypatch.setattr(check_providers, "recognize", fail)
    with pytest.raises(SystemExit, match="OpenAI vision preflight failed"):
        check_providers.main()
    assert "verified" not in capsys.readouterr().out


def test_visual_question_uses_indexed_openai_descriptions_without_new_image_call():
    item = {"kind": "figure", "crop_key": "private.png", "text": "Two boxes connected by an arrow."}
    state = {"query": "Explain the diagram", "request": AnswerRequest(question="Explain the diagram", document_ids=["d"]),
             "evidence": [item]}
    result = answering.inspect(state)
    assert result["evidence"][0]["visual_observation"]["observation"] == item["text"]
    assert "Unlimited" not in result["evidence"][0]["visual_observation"]["limitations"][0]
