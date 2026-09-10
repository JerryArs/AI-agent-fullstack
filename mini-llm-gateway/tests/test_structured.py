"""Structured Output（§4）。手法 B 端到端。"""

from __future__ import annotations

import httpx

from tests.conftest import gateway_client, make_config

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _payload(schema: dict[str, object]) -> dict[str, object]:
    return {
        "model": "general-primary",
        "messages": [{"role": "user", "content": "x"}],
        "response_schema": schema,
    }


async def test_invalid_json_maps_to_502(auth_headers: dict[str, str]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}], "usage": {}},
        )

    async with gateway_client(make_config(), handler) as client:
        response = await client.post("/v1/llm", headers=auth_headers, json=_payload(_SCHEMA))
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_json"


async def test_valid_structured_returns_parsed(auth_headers: dict[str, str]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer": "ok"}'}}], "usage": {}},
        )

    async with gateway_client(make_config(), handler) as client:
        response = await client.post("/v1/llm", headers=auth_headers, json=_payload(_SCHEMA))
    assert response.status_code == 200, response.text
    assert response.json()["parsed"] == {"answer": "ok"}


async def test_schema_violation_maps_to_502(auth_headers: dict[str, str]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer": 123}'}}], "usage": {}},
        )

    async with gateway_client(make_config(), handler) as client:
        response = await client.post("/v1/llm", headers=auth_headers, json=_payload(_SCHEMA))
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "schema_validation_failed"
