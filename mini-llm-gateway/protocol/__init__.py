"""内部协议层（design_spec.md §1.2）：与供应商、与传输无关的统一契约。"""

from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError
from mini_llm_gateway.protocol.events import StreamEvent, encode_sse
from mini_llm_gateway.protocol.messages import Message, Role
from mini_llm_gateway.protocol.request import LLMRequest, PromptSelection
from mini_llm_gateway.protocol.response import LLMResponse, Usage

__all__ = [
    "ErrorCode",
    "GatewayError",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "PromptSelection",
    "Role",
    "StreamEvent",
    "Usage",
    "encode_sse",
]
