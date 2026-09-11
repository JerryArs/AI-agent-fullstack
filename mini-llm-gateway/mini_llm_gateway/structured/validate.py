"""出口结构化校验与错误归一化（§4.2）。"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import ValidationError as JsonSchemaError
from jsonschema import validate

from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError


def parse_and_validate(content: str, schema: dict[str, Any]) -> dict[str, Any] | list[Any]:
    """把模型返回文本解析为 JSON 并按 schema 校验；失败抛稳定错误码。"""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise GatewayError(ErrorCode.invalid_json, "模型没有返回合法 JSON") from exc
    try:
        validate(instance=parsed, schema=schema)
    except JsonSchemaError as exc:
        raise GatewayError(
            ErrorCode.schema_validation_failed,
            "模型结果不符合 response_schema",
        ) from exc
    if not isinstance(parsed, (dict, list)):
        raise GatewayError(ErrorCode.schema_validation_failed, "结构化结果必须是对象或数组")
    return parsed
