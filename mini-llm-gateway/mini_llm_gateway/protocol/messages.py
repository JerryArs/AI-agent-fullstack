"""跨模型通用的单条对话消息，隔离供应商消息格式差异（§1.2）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """统一消息模型；``extra="forbid"`` 拒绝未知字段。"""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str = Field(min_length=1, max_length=20_000)
