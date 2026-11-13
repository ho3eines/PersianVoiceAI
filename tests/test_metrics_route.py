"""HTTP surface of the in-process metrics: ``GET /metrics``.

The numbers existed only inside ``app.state.metrics`` before, so a running
server could not answer "how deep is the ASR queue right now?" without adding
temporary debug code.  These tests pin the shape of the new read-only route,
its authentication behaviour (it is NOT public: it may leak queue depths and
CPU usage) and the disabled-metrics contract.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app


class FakeLLM:
    """Minimal llama-server stand-in: the metrics route never reaches it."""

    def __init__(self) -> None:
        self.closed = False

    async def health(self) -> dict[str, Any]:
        return {"reachable": False, "model_available": False, "models": []}

    async def aclose(self) -> None:
        self.closed = True


def _client(**overrides: Any) -> TestClient:
    base: dict[str, Any] = {
        "model_path": "models/test-model.gguf",
        "history_persist": False,
    }
    base.update(overrides)
    return TestClient(create_app(settings=Settings(**base), llm=FakeLLM()))


def test_metrics_shape() -> None:
    with _client() as client:
        client.app.state.metrics.increment("api_auth_failures", 2)
        client.app.state.metrics.set("queue_depth", 3.0)
        client.app.state.metrics.observe("asr_ms", 120.0)

        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert isinstance(data["uptime_s"], (int, float))
        assert data["enabled"] is True
        assert data["counters"]["api_auth_failures"] == 2
        assert data["gauges"]["queue_depth"] == 3.0
        assert data["observations"]["asr_ms"]["count"] == 1
        assert data["observations"]["asr_ms"]["last"] == 120.0


def test_metrics_flat_format() -> None:
    with _client() as client:
        client.app.state.metrics.observe("llm_ttft_ms", 300.0)
        response = client.get("/metrics?format=flat")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["metrics"]["llm_ttft_ms"] == 300.0


def test_metrics_unknown_format_falls_back_to_full_shape() -> None:
    """A typo in ``format`` must not turn a monitoring call into an error."""
    with _client() as client:
        response = client.get("/metrics?format=prometheus")
        assert response.status_code == 200
        assert "counters" in response.json()


def test_metrics_disabled_returns_empty_but_ok() -> None:
    with _client(metrics_enabled=False) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["counters"] == {}
        assert data["gauges"] == {}
        assert data["observations"] == {}


def test_metrics_requires_token_when_set(tmp_path: Any) -> None:
    """/metrics is behind the same Bearer gate as the rest of REST.

    It may reveal load and queue internals, so it must not join PUBLIC_PATHS.
    """
    settings = Settings(
        model_path="models/test-model.gguf",
        history_persist=False,
        api_host="0.0.0.0",
        api_token="top-secret",
    )
    with TestClient(create_app(settings=settings, llm=FakeLLM())) as client:
        assert client.get("/metrics").status_code == 401
        ok = client.get("/metrics", headers={"Authorization": "Bearer top-secret"})
        assert ok.status_code == 200
        assert ok.json()["status"] == "ok"


def test_metrics_not_in_public_paths() -> None:
    """Guard the invariant the auth test above relies on, at the source."""
    from server.security import PUBLIC_PATHS

    assert "/metrics" not in PUBLIC_PATHS
