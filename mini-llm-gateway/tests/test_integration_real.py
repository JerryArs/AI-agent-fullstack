"""可选真实集成（§2 / §11.1）：有 DEEPSEEK_API_KEY 时才跑，否则自动跳过。"""

from __future__ import annotations

import os

import httpx
import pytest

from mini_llm_gateway.app import create_app
from mini_llm_gateway.config.settings import load_config

pytestmark = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="未检测到 DEEPSEEK_API_KEY，跳过真实集成测试",
)


async def test_real_completion() -> None:
    config = load_config()
    upstream = httpx.AsyncClient()
    app = create_app(config, http_client=upstream)
    transport = httpx.ASGITransport(app=app)
    headers = {}
    if config.auth_token:
        headers["Authorization"] = f"Bearer {config.auth_token}"
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.test", timeout=90
    ) as client:
        response = await client.post(
            "/v1/llm",
            headers=headers,
            json={
                "model": "general-primary",
                "messages": [{"role": "user", "content": "只回复 PONG。"}],
            },
        )
    await upstream.aclose()
    assert response.status_code == 200, response.text
    assert response.json()["content"]
