"""稳定错误码与 GatewayError（design_spec.md §8）。

对外只暴露稳定 code + 安全 message，绝不透传上游堆栈或密钥。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """对外暴露的稳定错误码。"""

    unknown_model = "unknown_model"
    structured_output_unsupported = "structured_output_unsupported"
    unknown_prompt_template = "unknown_prompt_template"
    missing_prompt_variable = "missing_prompt_variable"
    unsupported_combination = "unsupported_combination"
    use_stream_endpoint = "use_stream_endpoint"
    invalid_json = "invalid_json"
    schema_validation_failed = "schema_validation_failed"
    gateway_misconfigured = "gateway_misconfigured"
    model_unavailable = "model_unavailable"
    upstream_stream_failed = "upstream_stream_failed"
    rate_limited = "rate_limited"
    budget_exceeded = "budget_exceeded"
    authentication_error = "authentication_error"


class GatewayError(Exception):
    """把内部错误标准化为可安全暴露的稳定 code + HTTP 状态。"""

    def __init__(self, code: ErrorCode | str, message: str, status_code: int = 502) -> None:
        self.code: str = code.value if isinstance(code, ErrorCode) else code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def as_detail(self) -> dict[str, str]:
        """FastAPI HTTPException detail 结构。"""
        return {"code": self.code, "message": self.message}
