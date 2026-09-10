"""协议层校验（design_spec.md §1.2 / §4.3）。纯单元，无需 app。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mini_llm_gateway.protocol.messages import Message
from mini_llm_gateway.protocol.request import LLMRequest


def test_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LLMRequest.model_validate(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}], "unexpected": True}
        )


def test_request_rejects_stream_with_schema() -> None:
    with pytest.raises(ValidationError):
        LLMRequest.model_validate(
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
                "response_schema": {"type": "object"},
            }
        )


def test_message_content_length_bounds() -> None:
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "user", "content": ""})
