"""供应商能力描述（§2.1）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StructuredMode = Literal["json_schema", "json_object"]


@dataclass(frozen=True)
class ProviderCapabilities:
    """描述适配器支持的能力，供路由层组装请求与做能力等价校验。"""

    streaming: bool = True
    structured_output: bool = True
