"""Mini LLM Gateway：可配置、可部署、可治理的模型网关。

分层见 design_spec.md §1.1：protocol / adapters / routing / prompts / governance /
structured / config / app。协议层不依赖任何供应商 SDK，适配层不依赖 FastAPI。
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
