"""OpenAI Compatible 适配器（§2.2）。

用 httpx 直接调用 ``/chat/completions``（而非 openai SDK），换取：
干净的 MockTransport 可测性（complete + stream）、明确的取消语义、更少的隐式重试。
密钥只由网关持有，缺失即 gateway_misconfigured。上游异常统一归一化为 UpstreamError。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from mini_llm_gateway.adapters.base import ProviderAdapter, ProviderCallSpec, UpstreamError
from mini_llm_gateway.adapters.capabilities import ProviderCapabilities
from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError
from mini_llm_gateway.protocol.response import Usage

_STRUCTURED_SYSTEM_HINT = (
    "只返回一个合法 JSON 对象，必须严格符合下列 JSON Schema，"
    "不要返回 Markdown 或额外文字："
)


class OpenAICompatibleAdapter(ProviderAdapter):
    """通过 OpenAI 兼容 HTTP API 调用上游；适配器无状态，靠 spec 承载模型信息。"""

    name = "openai_compatible"
    capabilities = ProviderCapabilities(streaming=True, structured_output=True)

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        # 注入 http_client 是可测性前提：测试用 MockTransport 顶替最底层网络传输（§11.1.2）。
        self._http_client = http_client

    def _endpoint(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/chat/completions"

    def _headers(self, spec: ProviderCallSpec) -> dict[str, str]:
        if not spec.api_key:
            raise GatewayError(ErrorCode.gateway_misconfigured, "Gateway 模型凭据未配置", 503)
        return {
            "Authorization": f"Bearer {spec.api_key}",
            "Content-Type": "application/json",
            **spec.extra_headers,
        }

    def _build_body(self, spec: ProviderCallSpec, *, stream: bool) -> dict[str, Any]:
        messages = [m.model_dump() for m in spec.messages]
        body: dict[str, Any] = {"model": spec.provider_model, "messages": messages}
        if stream:
            body["stream"] = True
        if spec.response_schema is not None:
            if spec.structured_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "agent_response",
                        "strict": True,
                        "schema": spec.response_schema,
                    },
                }
            else:
                body["response_format"] = {"type": "json_object"}
                schema_text = json.dumps(spec.response_schema, ensure_ascii=False)
                hint = _STRUCTURED_SYSTEM_HINT + schema_text
                body["messages"] = [{"role": "system", "content": hint}, *messages]
        return body

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        retryable = response.status_code == 429 or response.status_code >= 500
        raise UpstreamError(
            f"upstream returned {response.status_code}",
            retryable=retryable,
            status_code=response.status_code,
        )

    async def complete(self, spec: ProviderCallSpec) -> tuple[str, Usage]:
        client = self._require_client()
        body = self._build_body(spec, stream=False)
        try:
            response = await client.post(
                self._endpoint(spec.base_url),
                headers=self._headers(spec),
                json=body,
                timeout=spec.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError("upstream timeout", retryable=True) from exc
        except httpx.TransportError as exc:
            raise UpstreamError(f"upstream transport error: {exc}", retryable=True) from exc
        self._raise_for_status(response)
        data = response.json()
        content = self._extract_content(data)
        usage = self._extract_usage(data)
        return content, usage

    async def stream(self, spec: ProviderCallSpec) -> AsyncIterator[str]:
        # 设计取舍（非疏漏）：流式刻意只归一化为文本增量（§3.1 的 content.delta），
        # 丢弃供应商特定字段（finish_reason、tool_call delta、logprobs 等）。
        # 若将来把这些纳入统一事件契约或需要透传，改在此处解析并扩展 StreamEvent。
        client = self._require_client()
        body = self._build_body(spec, stream=True)
        try:
            async with client.stream(
                "POST",
                self._endpoint(spec.base_url),
                headers=self._headers(spec),
                json=body,
                timeout=spec.timeout_seconds,
            ) as response:
                self._raise_for_status(response)
                async for line in response.aiter_lines():
                    delta = self._parse_sse_delta(line)
                    if delta:
                        yield delta
        except httpx.TimeoutException as exc:
            raise UpstreamError("upstream timeout", retryable=True) from exc
        except httpx.TransportError as exc:
            raise UpstreamError(f"upstream transport error: {exc}", retryable=True) from exc

    def _require_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        raise GatewayError(
            ErrorCode.gateway_misconfigured,
            "Gateway HTTP client 未初始化",
            503,
        )

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> Usage:
        usage = data.get("usage") or {}
        return Usage(
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

    @staticmethod
    def _parse_sse_delta(line: str) -> str | None:
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            return None
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return None
        choices = chunk.get("choices") or []
        if not choices:
            return None
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        return str(content) if content else None
