"""Streaming SSE（§3.1）。手法 B 端到端：真实 SSE 解析 + 转发。"""

from __future__ import annotations

import httpx

from tests.conftest import gateway_client, make_config


async def test_sse_forwarded_and_completed(auth_headers: dict[str, str]) -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"!"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    async with gateway_client(make_config(), handler) as client:
        async with client.stream(
            "POST",
            "/v1/llm/stream",
            headers=auth_headers,
            json={"model": "general-primary", "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            body = (await response.aread()).decode()
            request_id = response.headers["x-request-id"]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert request_id.startswith("req_")
    assert '"type": "content.delta"' in body
    assert '"delta": "Hi"' in body
    assert '"type": "response.completed"' in body


async def test_stream_rejects_response_schema(auth_headers: dict[str, str]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="", headers={"content-type": "text/event-stream"})

    async with gateway_client(make_config(), handler) as client:
        response = await client.post(
            "/v1/llm/stream",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "hi"}],
                "response_schema": {"type": "object"},
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_combination"
