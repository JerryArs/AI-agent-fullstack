"""适配器抽象、调用规格与上游错误归一化（§2.1）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from mini_llm_gateway.adapters.capabilities import ProviderCapabilities, StructuredMode
from mini_llm_gateway.protocol.messages import Message
from mini_llm_gateway.protocol.response import Usage


class UpstreamError(Exception):
    """上游调用错误的归一化表示：``retryable`` 决定路由层是否重试（§5.3）。

    适配层负责把各家 SDK/HTTP 的异常翻译成本类型，使路由层与供应商解耦。
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ProviderCallSpec:
    """一次上游调用所需的全部信息（由 ModelSpec + 请求组装，§2.1）。"""

    provider_model: str
    base_url: str
    api_key: str
    messages: list[Message]
    timeout_seconds: float
    response_schema: dict[str, Any] | None = None
    structured_mode: StructuredMode = "json_schema"
    extra_headers: dict[str, str] = field(default_factory=dict)


class ProviderAdapter(ABC):
    """协议归一化层：子类把供应商差异吸收在 complete/stream 内部。"""

    name: str = "provider"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    async def complete(self, spec: ProviderCallSpec) -> tuple[str, Usage]:
        """非流式调用，返回 (文本, 用量)。异常须归一化为 UpstreamError。"""
        raise NotImplementedError

    @abstractmethod
    def stream(self, spec: ProviderCallSpec) -> AsyncIterator[str]:
        """流式调用，逐块产出增量文本。异常须归一化为 UpstreamError。"""
        raise NotImplementedError
