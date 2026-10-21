import json

import httpx
from openai import OpenAI
from pydantic import ValidationError
import pytest

from research_agent import extraction, providers, vision
from research_core.config import Settings, settings
from research_core.contracts import Layout


def test_openai_defaults_and_task_specific_effort():
    cfg = Settings(answer_model="", judge_model="", vision_model="")
    assert cfg.answer_model == cfg.vision_model == cfg.judge_model == "gpt-5.4-mini"
    assert cfg.answer_provider == cfg.vision_provider == cfg.judge_provider == "openai"
    assert cfg.answer_reasoning_effort == "low" and cfg.vision_reasoning_effort == "none"
    assert Settings(answer_reasoning_effort="", vision_reasoning_effort="").answer_reasoning_effort is None
    assert "secret-test-key" not in repr(Settings(openai_api_key="secret-test-key"))


@pytest.mark.parametrize("provider", ["groq", "deepseek", "typo"])
@pytest.mark.parametrize("role", ["answer", "vision", "judge"])
def test_old_provider_overrides_are_rejected(provider, role):
    with pytest.raises(ValidationError):
        Settings(**{f"{role}_provider": provider})
    with pytest.raises(ValueError, match="Only OpenAI"):
        providers.generation_client(provider)


def test_client_requires_openai_key_and_ignores_external_endpoint_override(database, monkeypatch):
    providers.generation_client.cache_clear()
    try:
        monkeypatch.setattr(settings(), "openai_api_key", "")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY is required"):
            providers.generation_client()
        monkeypatch.setattr(settings(), "openai_api_key", "test-only")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://other-provider.invalid")
        client = providers.generation_client()
        assert str(client.base_url).rstrip("/") == "https://api.openai.com/v1"
        assert client.api_key == "test-only"
        client.close()
    finally:
        providers.generation_client.cache_clear()


@pytest.mark.parametrize("image", [None, b"test image"])
@pytest.mark.parametrize("effort", [None, "none", "low"])
def test_openai_wire_payload_routes_model_and_reasoning_by_task(database, monkeypatch, image, effort):
    cfg = settings()
    task = "vision" if image is not None else "answer"
    monkeypatch.setattr(cfg, f"{task}_model", f"openai-{task}-test-model")
    monkeypatch.setattr(cfg, f"{task}_reasoning_effort", effort)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"transcription":"42","regions":[]}'}}]})
    with OpenAI(api_key="test-only", base_url="https://api.openai.com/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        monkeypatch.setattr(providers, "generation_client", lambda name: client)
        result = providers.structured(Layout, "Return JSON", "Extract", image=image, max_tokens=512)
    assert result.transcription == "42" and requests[0].url.host == "api.openai.com"
    body = json.loads(requests[0].content)
    assert body["model"] == f"openai-{task}-test-model"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_completion_tokens"] == 512 and "max_tokens" not in body
    assert "thinking" not in body and "temperature" not in body
    assert body.get("reasoning_effort") == effort
    if effort is None:
        assert "reasoning_effort" not in body
    if image is not None:
        image_input = body["messages"][1]["content"][1]["image_url"]
        assert image_input["url"].startswith("data:image/png;base64,")
        assert image_input["detail"] == "high"
    else:
        assert body["messages"][1]["content"] == "Extract"


def test_structured_explicit_other_provider_cannot_bypass_client_guard(database, monkeypatch):
    monkeypatch.setattr(providers, "generation_client", lambda _: pytest.fail("Unexpected request"))
    with pytest.raises(ValueError, match="Only OpenAI"):
        providers.structured(Layout, "system", "prompt", provider="deepseek")


def test_cache_isolates_vision_reasoning_settings(database, monkeypatch):
    calls = []
    monkeypatch.setattr(extraction, "recognize", lambda _: calls.append(1) or Layout(transcription="42"))
    for effort in ("none", "low", "none"):
        monkeypatch.setattr(settings(), "vision_reasoning_effort", effort)
        extraction.layout_for(b"same-image", "")
    assert len(calls) == 2


def test_vision_errors_name_openai_and_key():
    error = str(vision.VisionError("authentication"))
    assert "OpenAI" in error and "OPENAI_API_KEY" in error


def test_chat_only_preflight_does_not_call_vision_or_embeddings(database, monkeypatch, capsys):
    import sys
    from research_agent import check_providers
    monkeypatch.setattr(sys, "argv", ["check_providers", "--chat-only"])
    monkeypatch.setattr(check_providers, "structured", lambda *a, **kw: check_providers.Check(ok=True))
    def unexpected(*args):
        raise AssertionError("Chat-only preflight called another provider")
    monkeypatch.setattr(check_providers, "recognize", unexpected)
    monkeypatch.setattr(check_providers, "embed", unexpected)
    check_providers.main()
    assert "OpenAI gpt-5.4-mini: answer JSON verified" in capsys.readouterr().out
