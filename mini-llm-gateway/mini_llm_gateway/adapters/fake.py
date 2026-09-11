"""FakeAdapter：离线测试基石（§1.3）。

支持脚本化控制：固定/结构化内容、前 N 次失败、分块流、时延与取消时序，
并记录 complete/stream 调用以断言路由与 fallback 行为。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from mini_llm_gateway.adapters.base import ProviderAdapter, ProviderCallSpec, UpstreamError
from mini_llm_gateway.adapters.capabilities import ProviderCapabilities
from mini_llm_gateway.protocol.response import Usage


class FakeAdapter(ProviderAdapter):
    """脚本化适配器；不触网、无抖动。"""

    name = "fake"
    capabilities = ProviderCapabilities(streaming=True, structured_output=True)

    def __init__(
        self,
        *,
        content: str = "正常结果",
        failures: int = 0,
        chunks: Sequence[str] | None = None,
        input_tokens: int = 11,
        output_tokens: int = 7,
        delay: float = 0.0,
        cancel_after: int | None = None,
    ) -> None:
        self.content = content
        self.failures = failures
        self.chunks = list(chunks) if chunks is not None else ["第", "一块"]
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.delay = delay
        self.cancel_after = cancel_after
        self.complete_calls: list[str] = []
        self.stream_calls: list[str] = []
        self.chunks_emitted = 0

    async def complete(self, spec: ProviderCallSpec) -> tuple[str, Usage]:
        self.complete_calls.append(spec.provider_model)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failures > 0:
            self.failures -= 1
            raise UpstreamError("temporary upstream timeout", retryable=True)
        return self.content, Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )

    async def stream(self, spec: ProviderCallSpec) -> AsyncIterator[str]:
        self.stream_calls.append(spec.provider_model)
        if self.failures > 0:
            self.failures -= 1
            raise UpstreamError("temporary upstream timeout", retryable=True)
        for index, chunk in enumerate(self.chunks):
            if self.delay:
                await asyncio.sleep(self.delay)
            self.chunks_emitted = index + 1
            yield chunk
