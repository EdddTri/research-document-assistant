import json

import httpx
import pytest

from research_agent import providers
from research_core.config import Settings


def mock_ollama(monkeypatch, handler):
    client = httpx.Client(base_url="http://ollama.test/", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(providers, "embeddings_client", lambda: client)
    return client


def test_batches_preserve_order_without_cloud_credentials(monkeypatch):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/api/embed"
        assert "authorization" not in request.headers
        assert body["model"] == "bge-m3" and body["truncate"] is False
        return httpx.Response(200, json={"embeddings": [[float(t)] + [0.] * 1023 for t in body["input"]]})
    with mock_ollama(monkeypatch, handler):
        assert providers.embed([]) == []
        vectors = providers.embed([str(i) for i in range(1, 10)])
    assert [v[0] for v in vectors] == list(range(1, 10))
    assert [len(r["input"]) for r in requests] == [4, 4, 1]


@pytest.mark.parametrize("vectors", [None, [], [[1.] * 1536], [[0.] * 1024],
                                       [["bad"] * 1024], [[True] * 1024],
                                       [[1.] * 1024, [1.] * 1024]])
def test_rejects_invalid_vectors(monkeypatch, vectors):
    with mock_ollama(monkeypatch, lambda r: httpx.Response(200, json={"embeddings": vectors})):
        with pytest.raises(ValueError, match="invalid embedding"):
            providers.embed(["question"])


@pytest.mark.parametrize("status", [400, 404, 503])
def test_ollama_errors_do_not_fall_back_or_return_fake_vectors(monkeypatch, status):
    with mock_ollama(monkeypatch, lambda r: httpx.Response(status, json={"error": "unavailable"})):
        with pytest.raises(httpx.HTTPStatusError):
            providers.embed(["question"])


def test_model_alias_and_dimension_configuration():
    assert Settings(_env_file=None, embedding_model="bge-m3:latest").embedding_model == "bge-m3"
    with pytest.raises(ValueError, match="1024"):
        Settings(_env_file=None, embedding_dimensions=1536)
