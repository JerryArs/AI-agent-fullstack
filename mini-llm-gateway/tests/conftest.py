"""测试夹具（design_spec.md §11.1）。

- ``gateway_client``：手法 B。外层 ASGITransport 打进 app，内层 MockTransport 冒充上游 HTTP。
- ``fake_gateway_client``：手法 A。直接注入 FakeAdapter（adapter_override）。
全离线：无网络、无真实密钥、无抖动（retry.base_delay_seconds=0）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from mini_llm_gateway.adapters.base import ProviderAdapter
from mini_llm_gateway.app import create_app
from mini_llm_gateway.config.settings import GatewayConfig
from mini_llm_gateway.governance.tracing import InMemoryTraceSink
from mini_llm_gateway.prompts.bundle import PromptTemplate
from mini_llm_gateway.prompts.store import PromptStore

Handler = Callable[[httpx.Request], httpx.Response]


def make_config(**overrides: Any) -> GatewayConfig:
    raw: dict[str, Any] = {
        "auth_token": "test-key",
        "fallback_model": "general-backup",
        "retry": {"max_attempts_per_model": 2, "base_delay_seconds": 0},
        "rate_limit": {"enabled": False, "requests_per_minute": 60, "burst": 60},
        "budget": {"enabled": False, "max_cost_usd": 0.0},
        "models": {
            "general-primary": {
                "provider": "openai_compatible",
                "provider_model": "primary-model",
                "base_url": "https://primary.test",
                "api_key": "upstream-a",
                "supports_structured_output": True,
                "structured_output_mode": "json_schema",
                "price_per_million": {"input": 1.0, "output": 4.0},
            },
            "general-backup": {
                "provider": "openai_compatible",
                "provider_model": "backup-model",
                "base_url": "https://fallback.test",
                "api_key": "upstream-b",
                "supports_structured_output": True,
                "structured_output_mode": "json_schema",
                "price_per_million": {"input": 0.8, "output": 3.2},
            },
        },
    }
    raw.update(overrides)
    return GatewayConfig.model_validate(raw)


def make_prompt_store() -> PromptStore:
    return PromptStore(
        [
            PromptTemplate(
                name="knowledge_decision",
                version="v1",
                system_template="你是${product_name}的知识库决策器。",
                required_variables=["product_name"],
            )
        ]
    )


@asynccontextmanager
async def gateway_client(
    config: GatewayConfig,
    handler: Handler,
    *,
    prompt_store: PromptStore | None = None,
    trace_sink: InMemoryTraceSink | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """手法 B：真实 Provider + MockTransport 伪上游。"""
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        config,
        http_client=upstream,
        prompt_store=prompt_store or make_prompt_store(),
        trace_sink=trace_sink,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        yield client
    await upstream.aclose()


@asynccontextmanager
async def fake_gateway_client(
    config: GatewayConfig,
    adapter: ProviderAdapter,
    *,
    prompt_store: PromptStore | None = None,
    trace_sink: InMemoryTraceSink | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """手法 A：直接注入 FakeAdapter。"""
    app = create_app(
        config,
        adapter_override=adapter,
        prompt_store=prompt_store or make_prompt_store(),
        trace_sink=trace_sink,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-key"}
