"""Prompt Bundle（§6）。手法 B 注入断言 + 手法 A 错误码 + 单元加载/校验。"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from mini_llm_gateway.adapters.fake import FakeAdapter
from mini_llm_gateway.prompts.bundle import PromptTemplate
from mini_llm_gateway.prompts.store import PromptStore
from tests.conftest import fake_gateway_client, gateway_client, make_config


async def test_prompt_injected_as_system_message(auth_headers: dict[str, str]) -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    async with gateway_client(make_config(), handler) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "hi"}],
                "prompt": {
                    "name": "knowledge_decision",
                    "version": "v1",
                    "variables": {"product_name": "差旅助手"},
                },
            },
        )
    assert response.status_code == 200, response.text
    assert bodies[-1]["messages"][0] == {
        "role": "system",
        "content": "你是差旅助手的知识库决策器。",
    }


async def test_missing_variable_rejected(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "x"}],
                "prompt": {"name": "knowledge_decision", "version": "v1", "variables": {}},
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_prompt_variable"


async def test_unknown_template_rejected(auth_headers: dict[str, str]) -> None:
    async with fake_gateway_client(make_config(), FakeAdapter()) as client:
        response = await client.post(
            "/v1/llm",
            headers=auth_headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "x"}],
                "prompt": {"name": "nope", "version": "v9", "variables": {}},
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_prompt_template"


def test_template_placeholder_consistency_enforced() -> None:
    with pytest.raises(ValidationError):
        PromptTemplate(
            name="x",
            version="v1",
            system_template="hi ${a}",
            required_variables=["b"],
        )


def test_store_loads_bundle_directory() -> None:
    store = PromptStore.from_directory("prompts/bundles")
    assert len(store) >= 1
    template = store.get("knowledge_decision", "v1")
    assert template.name == "knowledge_decision"


def test_duplicate_template_rejected() -> None:
    template = PromptTemplate(name="a", version="v1", system_template="hi")
    with pytest.raises(ValueError, match="重复"):
        PromptStore([template, template])
