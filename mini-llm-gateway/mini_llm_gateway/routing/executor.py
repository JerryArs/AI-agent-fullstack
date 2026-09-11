"""Gateway 执行器：重试 + fallback 的非流式与流式主流程，含取消（§3 / §5）。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

from mini_llm_gateway.adapters.base import ProviderAdapter, ProviderCallSpec
from mini_llm_gateway.config.settings import GatewayConfig, ModelSpec
from mini_llm_gateway.governance.metrics import Metrics
from mini_llm_gateway.governance.quota import QuotaManager
from mini_llm_gateway.governance.tracing import CallTrace, TraceSink, calculate_cost, utcnow
from mini_llm_gateway.prompts.store import PromptStore
from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError
from mini_llm_gateway.protocol.events import StreamEvent, encode_sse
from mini_llm_gateway.protocol.messages import Message
from mini_llm_gateway.protocol.request import LLMRequest
from mini_llm_gateway.protocol.response import LLMResponse, Usage
from mini_llm_gateway.routing.policy import RoutePolicy
from mini_llm_gateway.routing.retry import is_retryable

AdapterResolver = Callable[[ModelSpec], ProviderAdapter]


class Gateway:
    """装配好依赖的网关核心；被 app 层路由调用。"""

    def __init__(
        self,
        config: GatewayConfig,
        resolve_adapter: AdapterResolver,
        prompt_store: PromptStore,
        trace_sink: TraceSink,
        quota: QuotaManager,
        metrics: Metrics,
    ) -> None:
        self.config = config
        self.resolve_adapter = resolve_adapter
        self.prompt_store = prompt_store
        self.trace_sink = trace_sink
        self.quota = quota
        self.metrics = metrics
        self.policy = RoutePolicy(config)

    # -- 治理前置 -------------------------------------------------------------
    def check_quota(self, identity: str) -> None:
        self.quota.check(identity)

    def new_request_id(self) -> str:
        return self._new_request_id()

    # -- 内部工具 -------------------------------------------------------------
    @staticmethod
    def _new_request_id() -> str:
        return f"req_{uuid4().hex}"

    def _call_spec(
        self,
        spec: ModelSpec,
        messages: list[Message],
        request: LLMRequest,
    ) -> ProviderCallSpec:
        return ProviderCallSpec(
            provider_model=spec.provider_model,
            base_url=spec.base_url,
            api_key=spec.resolve_api_key(),
            messages=messages,
            timeout_seconds=request.timeout_seconds,
            response_schema=request.response_schema,
            structured_mode=spec.structured_output_mode,
        )

    def _record(
        self,
        *,
        request_id: str,
        request: LLMRequest,
        actual_model: str | None,
        usage: Usage,
        latency_ms: int,
        attempts: int,
        status: str,
        error_code: str | None,
        cost_usd: float,
    ) -> None:
        trace = CallTrace(
            request_id=request_id,
            timestamp=utcnow(),
            requested_model=request.model,
            actual_model=actual_model,
            prompt_name=request.prompt.name if request.prompt else None,
            prompt_version=request.prompt.version if request.prompt else None,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            attempts=attempts,
            status=status,  # type: ignore[arg-type]
            error_code=error_code,
        )
        self.trace_sink.emit(trace)

    # -- 非流式 ---------------------------------------------------------------
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """有限重试 + 能力等价 fallback 的非流式主流程（§5）。"""
        request_id = self._new_request_id()
        started = time.perf_counter()
        attempts = 0
        needs_structured = request.response_schema is not None
        messages = self.prompt_store.build_messages(request.messages, request.prompt)

        for index, model_name in enumerate(self.policy.candidates(request.model)):
            try:
                spec = self.policy.validate_model(model_name, needs_structured=needs_structured)
            except GatewayError:
                if model_name == request.model:
                    raise
                continue
            adapter = self.resolve_adapter(spec)
            call_spec = self._call_spec(spec, messages, request)
            for attempt in range(self.config.retry.max_attempts_per_model):
                attempts += 1
                try:
                    content, usage = await adapter.complete(call_spec)
                except GatewayError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 归一化后按可重试性处理
                    if is_retryable(exc) and attempt < self.config.retry.max_attempts_per_model - 1:
                        await asyncio.sleep(self.config.retry.base_delay_seconds)
                        continue
                    break
                return self._finish_success(
                    request=request,
                    request_id=request_id,
                    model_name=model_name,
                    is_fallback=index > 0,
                    content=content,
                    usage=usage,
                    spec=spec,
                    attempts=attempts,
                    started=started,
                )

        latency_ms = int((time.perf_counter() - started) * 1000)
        self._record(
            request_id=request_id,
            request=request,
            actual_model=None,
            usage=Usage(input_tokens=0, output_tokens=0),
            latency_ms=latency_ms,
            attempts=attempts,
            status="failed",
            error_code=ErrorCode.model_unavailable.value,
            cost_usd=0.0,
        )
        self.metrics.record_request(request.model, "failed", latency_ms, 0.0)
        raise GatewayError(ErrorCode.model_unavailable, "主模型和备用模型均不可用", 502)

    def _finish_success(
        self,
        *,
        request: LLMRequest,
        request_id: str,
        model_name: str,
        is_fallback: bool,
        content: str,
        usage: Usage,
        spec: ModelSpec,
        attempts: int,
        started: float,
    ) -> LLMResponse:
        from mini_llm_gateway.structured.validate import parse_and_validate

        parsed: dict[str, object] | list[object] | None = None
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = calculate_cost(spec.price_per_million.input, spec.price_per_million.output, usage)
        if request.response_schema is not None:
            try:
                parsed = parse_and_validate(content, request.response_schema)
            except GatewayError as exc:
                self._record(
                    request_id=request_id,
                    request=request,
                    actual_model=model_name,
                    usage=usage,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    status="failed",
                    error_code=exc.code,
                    cost_usd=cost,
                )
                self.metrics.record_request(model_name, "failed", latency_ms, cost)
                raise
        self.quota.record_cost(cost)
        if is_fallback:
            self.metrics.record_fallback()
        self._record(
            request_id=request_id,
            request=request,
            actual_model=model_name,
            usage=usage,
            latency_ms=latency_ms,
            attempts=attempts,
            status="success",
            error_code=None,
            cost_usd=cost,
        )
        self.metrics.record_request(model_name, "success", latency_ms, cost)
        return LLMResponse(
            request_id=request_id,
            model=model_name,
            content=content,
            parsed=parsed,
            usage=usage,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    # -- 流式 -----------------------------------------------------------------
    def prepare_stream(self, request: LLMRequest) -> list[Message]:
        """流式前置校验：主模型白名单 + Prompt 渲染（§3.1）。"""
        self.policy.validate_model(request.model, needs_structured=False)
        return self.prompt_store.build_messages(request.messages, request.prompt)

    async def stream_events(
        self,
        request: LLMRequest,
        messages: list[Message],
        *,
        request_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """首块前可切备用；首块后只发流内错误；取消即停止拉取并记 cancelled（§3.2）。"""
        request_id = request_id or self._new_request_id()
        started = time.perf_counter()
        attempts = 0
        emitted = False
        finalized = False
        try:
            for model_name in self.policy.candidates(request.model):
                try:
                    spec = self.policy.validate_model(model_name, needs_structured=False)
                except GatewayError:
                    continue
                adapter = self.resolve_adapter(spec)
                call_spec = self._call_spec(spec, messages, request)
                attempts += 1
                try:
                    async for delta in adapter.stream(call_spec):
                        emitted = True
                        yield encode_sse(StreamEvent(type="content.delta", delta=delta))
                        if cancel_event is not None and cancel_event.is_set():
                            return  # 显式取消：停止拉取，finally 记 cancelled
                except Exception as exc:  # noqa: BLE001
                    if emitted or not is_retryable(exc):
                        break
                    continue
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._record(
                    request_id=request_id,
                    request=request,
                    actual_model=model_name,
                    usage=Usage(input_tokens=0, output_tokens=0),
                    latency_ms=latency_ms,
                    attempts=attempts,
                    status="success",
                    error_code=None,
                    cost_usd=0.0,
                )
                self.metrics.record_request(model_name, "success", latency_ms, 0.0)
                finalized = True
                yield encode_sse(StreamEvent(type="response.completed", model=model_name))
                return

            latency_ms = int((time.perf_counter() - started) * 1000)
            self._record(
                request_id=request_id,
                request=request,
                actual_model=None,
                usage=Usage(input_tokens=0, output_tokens=0),
                latency_ms=latency_ms,
                attempts=attempts,
                status="failed",
                error_code=ErrorCode.upstream_stream_failed.value,
                cost_usd=0.0,
            )
            self.metrics.record_request(request.model, "failed", latency_ms, 0.0)
            finalized = True
            failed_event = StreamEvent(
                type="response.failed",
                error=ErrorCode.upstream_stream_failed.value,
            )
            yield encode_sse(failed_event)
        finally:
            if not finalized:
                # 客户端断连 / 显式取消 / 超时：停止拉取上游并记 cancelled（§3.2）。
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._record(
                    request_id=request_id,
                    request=request,
                    actual_model=None,
                    usage=Usage(input_tokens=0, output_tokens=0),
                    latency_ms=latency_ms,
                    attempts=attempts,
                    status="cancelled",
                    error_code=None,
                    cost_usd=0.0,
                )
