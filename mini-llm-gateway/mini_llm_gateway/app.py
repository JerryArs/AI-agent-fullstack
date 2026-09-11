"""FastAPI 装配与路由（design_spec.md §9）。

依赖注入（§11.1.2 可测性要求）：
- ``http_client``：注入上游 httpx 客户端，测试用 MockTransport 顶替网络传输（手法 B）。
- ``adapter_override``：直接注入 ProviderAdapter（如 FakeAdapter），用于协程级/协议单测（手法 A）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from mini_llm_gateway.adapters.base import ProviderAdapter
from mini_llm_gateway.adapters.fake import FakeAdapter
from mini_llm_gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from mini_llm_gateway.config.settings import GatewayConfig, ModelSpec
from mini_llm_gateway.governance.metrics import Metrics
from mini_llm_gateway.governance.quota import (
    BudgetGuard,
    QuotaError,
    QuotaManager,
    RateLimiter,
)
from mini_llm_gateway.governance.tracing import CallTrace, InMemoryTraceSink, TraceSink
from mini_llm_gateway.prompts.store import PromptStore
from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError
from mini_llm_gateway.protocol.request import LLMRequest
from mini_llm_gateway.protocol.response import LLMResponse
from mini_llm_gateway.routing.executor import AdapterResolver, Gateway

logger = logging.getLogger("mini_llm_gateway")


def _build_resolver(
    config: GatewayConfig,
    http_client: httpx.AsyncClient | None,
    adapter_override: ProviderAdapter | None,
) -> AdapterResolver:
    """按 ModelSpec.provider 解析适配器；override 时全部走同一适配器（手法 A）。"""
    if adapter_override is not None:
        return lambda _spec: adapter_override
    openai_adapter = OpenAICompatibleAdapter(http_client=http_client)
    fake_adapter = FakeAdapter()

    def resolve(spec: ModelSpec) -> ProviderAdapter:
        if spec.provider == "fake":
            return fake_adapter
        return openai_adapter

    return resolve


def create_app(
    config: GatewayConfig,
    *,
    http_client: httpx.AsyncClient | None = None,
    adapter_override: ProviderAdapter | None = None,
    prompt_store: PromptStore | None = None,
    trace_sink: TraceSink | None = None,
    metrics: Metrics | None = None,
) -> FastAPI:
    store = prompt_store or (
        PromptStore.from_directory(config.prompts_dir) if config.prompts_dir else PromptStore()
    )
    sink = trace_sink or InMemoryTraceSink()
    meter = metrics or Metrics()
    quota = QuotaManager(
        rate_limiter=RateLimiter(
            enabled=config.rate_limit.enabled,
            requests_per_minute=config.rate_limit.requests_per_minute,
            burst=config.rate_limit.burst,
        ),
        budget=BudgetGuard(
            enabled=config.budget.enabled,
            max_cost_usd=config.budget.max_cost_usd,
        ),
    )
    gateway = Gateway(
        config=config,
        resolve_adapter=_build_resolver(config, http_client, adapter_override),
        prompt_store=store,
        trace_sink=sink,
        quota=quota,
        metrics=meter,
    )

    if not config.auth_token:
        logger.warning("Gateway 运行在无鉴权模式：未配置 auth_token（§7.3 安全提示）")

    app = FastAPI(title="Mini LLM Gateway", version="0.1.0")
    app.state.gateway = gateway
    app.state.trace_sink = sink
    app.state.metrics = meter
    cancel_registry: dict[str, asyncio.Event] = {}
    app.state.cancel_registry = cancel_registry

    def require_auth(authorization: str | None = Header(default=None)) -> str:
        """鉴权依赖；返回调用方身份（用于配额）。未配置 token 则放行为 anonymous。"""
        if not config.auth_token:
            return "anonymous"
        expected = f"Bearer {config.auth_token}"
        if authorization != expected:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": ErrorCode.authentication_error.value,
                    "message": "无效或缺失的凭据",
                },
            )
        return config.auth_token

    @app.post("/v1/llm", response_model=LLMResponse)
    async def create_llm_response(
        request: LLMRequest,
        identity: str = Depends(require_auth),
    ) -> LLMResponse:
        if request.stream:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": ErrorCode.use_stream_endpoint.value,
                    "message": "流式请求请使用 /v1/llm/stream",
                },
            )
        _enforce_quota(gateway, identity)
        try:
            return await gateway.complete(request)
        except GatewayError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc

    @app.post("/v1/llm/stream")
    async def create_stream(
        request: LLMRequest,
        identity: str = Depends(require_auth),
    ) -> StreamingResponse:
        if request.response_schema is not None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": ErrorCode.unsupported_combination.value,
                    "message": "流式输出不支持 response_schema",
                },
            )
        _enforce_quota(gateway, identity)
        try:
            messages = gateway.prepare_stream(request)
        except GatewayError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc

        request_id = gateway.new_request_id()
        cancel_event = asyncio.Event()
        cancel_registry[request_id] = cancel_event

        async def event_source() -> AsyncIterator[str]:
            try:
                async for frame in gateway.stream_events(
                    request, messages, request_id=request_id, cancel_event=cancel_event
                ):
                    yield frame
            finally:
                cancel_registry.pop(request_id, None)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"X-Request-Id": request_id},
        )

    @app.post("/v1/llm/stream/{request_id}/cancel")
    async def cancel_stream(
        request_id: str,
        identity: str = Depends(require_auth),
    ) -> JSONResponse:
        event = cancel_registry.get(request_id)
        if event is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_request", "message": "流不存在或已结束"},
            )
        event.set()
        return JSONResponse(
            status_code=202,
            content={"request_id": request_id, "status": "cancelling"},
        )

    @app.get("/v1/traces", response_model=list[CallTrace])
    async def list_traces(identity: str = Depends(require_auth)) -> list[CallTrace]:
        return sink.list()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if config.enable_metrics:

        @app.get("/metrics")
        async def metrics_endpoint(identity: str = Depends(require_auth)) -> PlainTextResponse:
            return PlainTextResponse(meter.render())

    return app


def _enforce_quota(gateway: Gateway, identity: str) -> None:
    try:
        gateway.check_quota(identity)
    except QuotaError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def build_default_app() -> FastAPI:
    """从配置文件构建默认 app，供 ``uvicorn mini_llm_gateway.app:app`` 使用。"""
    from mini_llm_gateway.config.settings import load_config

    config = load_config()
    client = httpx.AsyncClient()
    app = create_app(config, http_client=client)

    @app.on_event("shutdown")
    async def _close_client() -> None:
        await client.aclose()

    return app
