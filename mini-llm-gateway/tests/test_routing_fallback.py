"""路由、重试与 Fallback（§5）。手法 B 端到端 + 单元能力校验。"""

from __future__ import annotations

import json

import httpx
import pytest

from mini_llm_gateway.adapters.fake import FakeAdapter
from mini_llm_gateway.config.settings import GatewayConfig
from mini_llm_gateway.governance.tracing import InMemoryTraceSink
from mini_llm_gateway.protocol.errors import GatewayError
from mini_llm_gateway.routing.policy import RoutePolicy
from tests.conftest import fake_gateway_client, gateway_client, make_config


async def test_retry_then_fallback_records_usage(auth_headers: dict[str, str]) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.host, body["model"]))
        if request.url.host == "primary.test":
            return httpx.Response(429, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    sink = InMemoryTraceSink()
    async with gateway_client(make_config(), handler, trace_sink=sink) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={"model": "general-primary", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "general-backup"
    assert body["attempts"] == 3
    assert body["content"] == "hello"
    assert calls == [
        ("primary.test", "primary-model"),
        ("primary.test", "primary-model"),
        ("fallback.test", "backup-model"),
    ]
    trace = sink.list()[-1]
    assert trace.status == "success"
    assert trace.actual_model == "general-backup"
    assert trace.input_tokens == 10
    assert trace.output_tokens == 5
    assert trace.cost_usd == pytest.approx((10 * 0.8 + 5 * 3.2) / 1_000_000)


async def test_unknown_model_rejected(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={"model": "does-not-exist", "messages": [{"role": "user", "content": "x"}]},
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_model"


def test_policy_rejects_structured_on_unsupported_model() -> None:
    config = GatewayConfig.model_validate(
        {
            "fallback_model": "basic",
            "models": {"basic": {"provider_model": "b", "supports_structured_output": False}},
        }
    )
    policy = RoutePolicy(config)
    with pytest.raises(GatewayError) as excinfo:
        policy.validate_model("basic", needs_structured=True)
    assert excinfo.value.code == "structured_output_unsupported"


def test_policy_candidates_dedupe() -> None:
    policy = RoutePolicy(make_config())
    assert policy.candidates("general-primary") == ["general-primary", "general-backup"]
    assert policy.candidates("general-backup") == ["general-backup"]
