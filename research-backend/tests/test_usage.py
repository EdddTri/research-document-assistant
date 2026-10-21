import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
from openai import OpenAI
from openai.types.chat import ChatCompletion
import pytest

from research_agent import answering, providers
from research_agent.usage import collect_usage, record_usage
from research_core.contracts import Answer, AnswerRequest, Layout
from research_core.config import settings
from evaluation.run import metrics


def completion(model="gpt-5.4-mini-2026-03-17", **overrides):
    return ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"transcription":"hello"}'}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
                  "prompt_tokens_details": {"cached_tokens": 400},
                  "completion_tokens_details": {"reasoning_tokens": 100}},
        **overrides,
    })


def test_cached_input_discount_and_reasoning_not_double_counted():
    with collect_usage() as collector:
        record_usage(completion(), "gpt-5.4-mini")
        record_usage(completion(), "gpt-5.4-mini")
    result = collector.summary()
    assert result.estimated_cost_usd == pytest.approx(0.00276)
    assert result.input_tokens == 2000 and result.cached_input_tokens == 800
    assert result.output_tokens == 400 and result.reasoning_tokens == 200
    assert result.usage_complete and len(result.calls) == 2


@pytest.mark.parametrize("response", [completion(model="unknown"), completion(service_tier="priority"),
                                               completion(usage=None), None])
def test_unknown_pricing_or_missing_usage_never_reported_as_free(response):
    with collect_usage() as collector:
        record_usage(completion(), "gpt-5.4-mini")
        record_usage(response, "gpt-5.4-mini")
    result = collector.summary()
    assert result.estimated_cost_usd is None
    assert result.known_cost_usd == pytest.approx(0.00138)
    assert result.usage_complete == (response is not None and response.usage is not None)


def test_concurrent_requests_and_nested_collectors_are_isolated():
    barrier = Barrier(2)
    def run(model):
        with collect_usage() as collector:
            barrier.wait(timeout=5)
            record_usage(completion(model=model), model)
            with collect_usage() as nested:
                record_usage(completion(), "gpt-5.4-mini")
            assert len(nested.summary().calls) == 1
            record_usage(completion(model=model), model)
        record_usage(completion(), "gpt-5.4-mini")  # No longer inside any query.
        return collector.summary()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["model-a", "model-b"]))
    assert [[c.model for c in r.calls] for r in results] == [["model-a"] * 2, ["model-b"] * 2]


@pytest.mark.parametrize("finish,content,error", [("stop", "not JSON", "format"),
                                                  ("length", "{}", "truncated")])
def test_provider_records_usage_before_rejecting_paid_response(database, monkeypatch, finish, content, error):
    response = completion(choices=[{"index": 0, "finish_reason": finish,
                                   "message": {"role": "assistant", "content": content}}])
    with OpenAI(api_key="test-only", http_client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response.model_dump())))) as client:
        monkeypatch.setattr(providers, "generation_client", lambda _: client)
        with collect_usage() as collector, pytest.raises(providers.ResponseError, match=error):
            providers.structured(Layout, "system", "prompt")
    assert collector.summary().estimated_cost_usd == pytest.approx(0.00138)


def test_provider_failure_is_unknown_not_zero(database, monkeypatch):
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(429, json={"error": {"message": "test"}})))) as client:
        monkeypatch.setattr(providers, "generation_client", lambda _: client)
        with collect_usage() as collector, pytest.raises(Exception):
            providers.structured(Layout, "system", "prompt")
    assert collector.summary().estimated_cost_usd is None
    assert not collector.summary().usage_complete


@pytest.mark.parametrize("enabled", [False, True])
def test_openai_tracing_wrapper_requires_document_opt_in(database, monkeypatch, enabled):
    wrapped = []
    monkeypatch.setattr(settings(), "trace_document_content", enabled)
    monkeypatch.setattr(providers, "wrap_openai", lambda client: wrapped.append(client) or client)
    with OpenAI(api_key="test-only", http_client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=completion().model_dump())))) as client:
        monkeypatch.setattr(providers, "generation_client", lambda _: client)
        providers.structured(Layout, "system", "prompt")
        assert len(wrapped) == int(enabled)
        if enabled:
            assert wrapped[0] is not client  # Wrap after with_options, which creates a new client.


def test_real_graph_collects_resolve_and_both_compositions(database, monkeypatch):
    responses = iter([
        {"query": "Resolved question"},
        {"answerability": "none", "rewrite": "Try again", "limitations": ["Missing"]},
        {"answerability": "none", "limitations": ["Still missing"]},
    ])
    def handler(_):
        return httpx.Response(200, json=completion(choices=[{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(next(responses))}}]).model_dump())
    monkeypatch.setattr(answering, "retrieve", lambda *_: [{"id": "e1", "kind": "text", "text": "A study."}])
    with OpenAI(api_key="test-only", http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        monkeypatch.setattr(providers, "generation_client", lambda _: client)
        result = answering.answer(AnswerRequest(question="And why?", document_ids=["d1"],
                                               history=[{"role": "user", "content": {"text": "Explain"}}]))
    assert len(result.usage.calls) == 3
    assert result.usage.estimated_cost_usd == pytest.approx(0.00414)
    score = metrics(result.model_dump(), {"answerability": "none"})
    assert score["query_cost_usd"] == result.usage.estimated_cost_usd
    assert score["query_input_tokens"] == 3000


def test_old_answers_remain_readable_and_do_not_claim_zero_cost():
    result = Answer(blocks=[], citations=[], answerability="none")
    assert result.usage is None
    assert metrics(result.model_dump(), {"answerability": "none"})["query_cost_usd"] is None
