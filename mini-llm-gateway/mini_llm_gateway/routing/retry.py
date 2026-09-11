"""单次调用层的可重试判定（§5.3）。"""

from __future__ import annotations

from mini_llm_gateway.adapters.base import UpstreamError


def is_retryable(exc: BaseException) -> bool:
    """仅临时性故障可重试；供应商差异已在适配层归一化为 UpstreamError。"""
    if isinstance(exc, UpstreamError):
        return exc.retryable
    return isinstance(exc, (TimeoutError, ConnectionError))
