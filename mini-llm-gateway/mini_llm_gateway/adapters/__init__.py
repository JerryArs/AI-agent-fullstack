"""供应商适配层（design_spec.md §1.3 / §2）：吸收 SDK/HTTP 差异，不依赖 FastAPI。"""

from mini_llm_gateway.adapters.base import ProviderAdapter, ProviderCallSpec, UpstreamError
from mini_llm_gateway.adapters.capabilities import ProviderCapabilities
from mini_llm_gateway.adapters.fake import FakeAdapter
from mini_llm_gateway.adapters.openai_compatible import OpenAICompatibleAdapter

__all__ = [
    "FakeAdapter",
    "OpenAICompatibleAdapter",
    "ProviderAdapter",
    "ProviderCallSpec",
    "ProviderCapabilities",
    "UpstreamError",
]
