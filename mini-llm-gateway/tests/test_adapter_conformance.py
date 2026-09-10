"""OpenAICompatibleAdapter 契约（§2 / §11.1 手法 B）：真实适配器 + MockTransport 伪上游。"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from mini_llm_gateway.adapters.base import ProviderCallSpec, UpstreamError
from mini_llm_gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from mini_llm_gateway.protocol.errors import GatewayError
from mini_llm_gateway.protocol.messages import Message

Handler = Callable[[httpx.Request], httpx.Response]


def _spec(**kw: object) -> ProviderCallSpec:
    base = {
        "provider_model": "m",
        "base_url": "https://up.test",
        "api_key": "k",
        "messages": [Message(role="user", content="hi")],
        "timeout_seconds": 5.0,
    }
    base.update(kw)
    return ProviderCallSpec(**base)  # type: ignore[arg-type]


def _adapter(handler: Handler) -> tuple[OpenAICompatibleAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleAdapter(http_client=client), client


async def test_complete_builds_body_and_parses_usage() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    adapter, client = _adapter(handler)
    content, usage = await adapter.complete(_spec())
    await client.aclose()

    assert content == "hello"
    assert (usage.input_tokens, usage.output_tokens) == (10, 5)
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "m"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["auth"] == "Bearer k"


async def test_structured_json_schema_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    adapter, client = _adapter(handler)
    await adapter.complete(_spec(response_schema={"type": "object"}, structured_mode="json_schema"))
    await client.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}


async def test_structured_json_object_injects_system() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    adapter, client = _adapter(handler)
    await adapter.complete(_spec(response_schema={"type": "object"}, structured_mode="json_object"))
    await client.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (400, False)])
async def test_error_status_normalized(status: int, retryable: bool) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "x"})

    adapter, client = _adapter(handler)
    with pytest.raises(UpstreamError) as excinfo:
        await adapter.complete(_spec())
    await client.aclose()
    assert excinfo.value.retryable is retryable


async def test_stream_parses_sse_deltas() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    adapter, client = _adapter(handler)
    deltas = [chunk async for chunk in adapter.stream(_spec())]
    await client.aclose()
    assert deltas == ["Hi", " there"]


async def test_missing_api_key_is_misconfigured() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter, client = _adapter(handler)
    with pytest.raises(GatewayError) as excinfo:
        await adapter.complete(_spec(api_key=""))
    await client.aclose()
    assert excinfo.value.code == "gateway_misconfigured"
