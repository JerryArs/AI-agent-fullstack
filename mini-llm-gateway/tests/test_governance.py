"""资源治理与观测闭环（§7）。手法 A：限流 / 预算 / 审计不记文本。"""

from __future__ import annotations

from mini_llm_gateway.adapters.fake import FakeAdapter
from mini_llm_gateway.governance.tracing import InMemoryTraceSink
from tests.conftest import fake_gateway_client, make_config

_PAYLOAD = {"model": "general-primary", "messages": [{"role": "user", "content": "x"}]}


async def test_rate_limit_returns_429(auth_headers: dict[str, str]) -> None:
    config = make_config(rate_limit={"enabled": True, "requests_per_minute": 1, "burst": 1})
    async with fake_gateway_client(config, FakeAdapter()) as client:
        first = await client.post("/v1/llm", headers=auth_headers, json=_PAYLOAD)
        second = await client.post("/v1/llm", headers=auth_headers, json=_PAYLOAD)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "rate_limited"


async def test_budget_exceeded_returns_429(auth_headers: dict[str, str]) -> None:
    config = make_config(budget={"enabled": True, "max_cost_usd": 0.00001})
    async with fake_gateway_client(config, FakeAdapter(input_tokens=11, output_tokens=7)) as client:
        first = await client.post("/v1/llm", headers=auth_headers, json=_PAYLOAD)
        second = await client.post("/v1/llm", headers=auth_headers, json=_PAYLOAD)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "budget_exceeded"


async def test_traces_never_store_content(auth_headers: dict[str, str]) -> None:
    sink = InMemoryTraceSink()
    async with fake_gateway_client(make_config(), FakeAdapter(), trace_sink=sink) as client:
        ok = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "x"}],
                "prompt": {
                    "name": "knowledge_decision",
                    "version": "v1",
                    "variables": {"product_name": "测试"},
                },
            },
        )
        assert ok.status_code == 200
        traces = (await client.get("/v1/traces", headers=auth_headers)).json()

    assert traces
    for item in traces:
        assert "content" not in item
        assert "parsed" not in item
    last = traces[-1]
    assert last["status"] == "success"
    assert last["prompt_name"] == "knowledge_decision"
    assert last["prompt_version"] == "v1"
    assert last["cost_usd"] >= 0
    assert last["latency_ms"] >= 0
