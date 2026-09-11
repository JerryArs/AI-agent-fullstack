"""网关配置模型与加载（§10.1 / §2.3）。

配置可由 yaml 文件加载（支持 ``${VAR:default}`` 环境插值），也可在测试中直接
``GatewayConfig.model_validate({...})`` 构造。凭据只由网关进程读取。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mini_llm_gateway.adapters.capabilities import StructuredMode

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


class PriceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0, default=0.0)
    output: float = Field(ge=0, default=0.0)


class ModelSpec(BaseModel):
    """平台模型名 → 供应商模型、地址、密钥与能力（替代硬编码 MODEL_CONFIGS）。"""

    model_config = ConfigDict(extra="forbid")

    provider: str = "openai_compatible"
    provider_model: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str | None = None
    api_key: str | None = None
    supports_structured_output: bool = False
    structured_output_mode: StructuredMode = "json_schema"
    context_window: int = 65_536
    price_per_million: PriceSpec = Field(default_factory=PriceSpec)

    def resolve_api_key(self) -> str:
        """直连 api_key 优先，其次读环境变量；均无则返回空串（触发 misconfigured）。"""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env, "")
        return ""


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts_per_model: int = Field(default=2, ge=1)
    base_delay_seconds: float = Field(default=0.1, ge=0)


class RateLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    requests_per_minute: int = Field(default=60, ge=1)
    burst: int = Field(default=60, ge=1)


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_cost_usd: float = Field(default=0.0, ge=0)


class GatewayConfig(BaseModel):
    """网关运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    auth_token: str | None = None
    models: dict[str, ModelSpec]
    fallback_model: str = "general-backup"
    retry: RetryConfig = Field(default_factory=RetryConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    prompts_dir: str | None = None
    enable_metrics: bool = True

    def model_spec(self, name: str) -> ModelSpec | None:
        return self.models.get(name)


def _expand_env(value: Any) -> Any:
    """递归展开字符串中的 ``${VAR:default}``。"""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            var, default = match.group(1), match.group(2)
            return os.getenv(var, default if default is not None else "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | Path | None = None) -> GatewayConfig:
    """从 yaml 加载配置（默认 ``GATEWAY_CONFIG`` 或 config/models.yaml）。"""
    resolved = Path(path or os.getenv("GATEWAY_CONFIG") or "config/models.yaml")
    raw: dict[str, Any] = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    expanded = _expand_env(raw)
    # 顶层运行时开关允许从 env 覆盖。
    expanded.setdefault("auth_token", os.getenv("GATEWAY_AUTH_TOKEN"))
    if os.getenv("PROMPTS_DIR"):
        expanded["prompts_dir"] = os.getenv("PROMPTS_DIR")
    return GatewayConfig.model_validate(expanded)
