"""API 契约（§9）：鉴权、入口校验、探针与指标。手法 A。"""

from __future__ import annotations

from mini_llm_gateway.adapters.fake import FakeAdapter
from tests.conftest import fake_gateway_client, make_config


async def test_requires_auth() -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.get("/v1/traces")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_error"


async def test_unknown_field_rejected(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "x"}],
                "unexpected": True,
            },
        )
    assert response.status_code == 422


async def test_stream_with_schema_rejected_at_protocol(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "x"}],
                "stream": True,
                "response_schema": {"type": "object"},
            },
        )
    assert response.status_code == 422


async def test_stream_flag_on_nonstream_endpoint(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "x"}],
                "stream": True,
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "use_stream_endpoint"


async def test_healthz_and_metrics(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        health = await client.get("/healthz")
        metrics = await client.get("/metrics", headers=auth_headers)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert metrics.status_code == 200


async def test_non_stream_happy_path(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter(content="pong")) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={"model": "general-primary", "messages": [{"role": "user", "content": "ping"}]},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == "pong"
    assert body["model"] == "general-primary"
    assert body["attempts"] == 1
    assert body["request_id"].startswith("req_")
