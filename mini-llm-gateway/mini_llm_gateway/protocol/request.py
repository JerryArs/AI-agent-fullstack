"""统一 Gateway 请求协议（§1.2）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_llm_gateway.protocol.messages import Message


class PromptSelection(BaseModel):
    """调用方只能选择受控模板及变量，不能提交或覆盖模板正文（§6）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    variables: dict[str, str] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    """统一请求协议；``extra="forbid"`` 拦截未知字段。"""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=100)
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: bool = False
    response_schema: dict[str, Any] | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    prompt: PromptSelection | None = None

    @model_validator(mode="after")
    def check_supported_combination(self) -> LLMRequest:
        """禁止 stream 与 response_schema 混用（§4.3）。"""
        if self.stream and self.response_schema is not None:
            raise ValueError("stream 与 response_schema 不能同时使用")
        return self
