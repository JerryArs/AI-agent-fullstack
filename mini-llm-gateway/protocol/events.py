"""流式事件与 SSE 编码（§3.1）。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EventType = Literal["content.delta", "response.completed", "response.failed"]


class StreamEvent(BaseModel):
    """统一流式事件；``delta`` / ``model`` / ``error`` 按事件类型选填。"""

    model_config = ConfigDict(extra="forbid")

    type: EventType
    delta: str | None = None
    model: str | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """仅保留非 None 字段，保持 SSE 负载紧凑。"""
        return self.model_dump(exclude_none=True)


def encode_sse(event: StreamEvent | dict[str, Any]) -> str:
    """把事件编码为浏览器与 Agent 都能消费的 SSE 帧。"""
    payload = event.to_payload() if isinstance(event, StreamEvent) else event
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
