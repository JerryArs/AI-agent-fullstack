"""路由策略与能力等价校验（§5.1 / §5.2）。"""

from __future__ import annotations

from mini_llm_gateway.config.settings import GatewayConfig, ModelSpec
from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError


class RoutePolicy:
    """默认策略：候选序列 = 去重保序的 [requested, fallback_model]。"""

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config

    def candidates(self, requested_model: str) -> list[str]:
        return list(dict.fromkeys([requested_model, self._config.fallback_model]))

    def validate_model(self, model_name: str, *, needs_structured: bool) -> ModelSpec:
        """校验白名单与结构化能力，阻止不等价的 fallback（§5.2）。"""
        spec = self._config.model_spec(model_name)
        if spec is None:
            raise GatewayError(ErrorCode.unknown_model, "模型不在 Gateway 允许列表中", 400)
        if needs_structured and not spec.supports_structured_output:
            raise GatewayError(
                ErrorCode.structured_output_unsupported,
                "模型不支持 Structured Output",
                400,
            )
        return spec
