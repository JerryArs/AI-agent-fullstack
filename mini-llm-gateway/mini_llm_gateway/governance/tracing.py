"""审计追踪（§7.1 / §7.2）。默认不记录 messages / content（隐私红线）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mini_llm_gateway.protocol.response import Usage

TraceStatus = Literal["success", "failed", "cancelled"]


class CallTrace(BaseModel):
    """单次调用的元数据；不含模型回答文本。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    timestamp: datetime
    requested_model: str
    actual_model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)
    attempts: int = Field(ge=0)
    status: TraceStatus
    error_code: str | None = None


def calculate_cost(
    price_input_per_million: float,
    price_output_per_million: float,
    usage: Usage,
) -> float:
    """按实际模型单价与输入输出 Token 计算成本。"""
    return (
        usage.input_tokens * price_input_per_million
        + usage.output_tokens * price_output_per_million
    ) / 1_000_000


def utcnow() -> datetime:
    return datetime.now(UTC)


class TraceSink(ABC):
    """可插拔审计后端（§7.2）。"""

    @abstractmethod
    def emit(self, trace: CallTrace) -> None: ...

    @abstractmethod
    def list(self) -> list[CallTrace]: ...


class InMemoryTraceSink(TraceSink):
    """默认内存实现，支撑 GET /v1/traces。"""

    def __init__(self) -> None:
        self._traces: list[CallTrace] = []

    def emit(self, trace: CallTrace) -> None:
        self._traces.append(trace)

    def list(self) -> list[CallTrace]:
        return list(self._traces)

    def clear(self) -> None:
        self._traces.clear()
