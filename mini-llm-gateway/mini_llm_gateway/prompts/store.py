"""Prompt Store：加载与校验 Bundle 资产（§6.1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mini_llm_gateway.prompts.bundle import PromptTemplate
from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError
from mini_llm_gateway.protocol.messages import Message
from mini_llm_gateway.protocol.request import PromptSelection


class PromptStore:
    """受控模板库；调用方只能按 name+version 选择，不能提交模板正文。"""

    def __init__(self, templates: list[PromptTemplate] | None = None) -> None:
        self._templates: dict[tuple[str, str], PromptTemplate] = {}
        for template in templates or []:
            self.add(template)

    def add(self, template: PromptTemplate) -> None:
        key = (template.name, template.version)
        if key in self._templates:
            raise ValueError(f"重复的 Prompt 模板: {key}")
        self._templates[key] = template

    @classmethod
    def from_directory(cls, directory: str | Path) -> PromptStore:
        """从 ``<dir>/**/*.yaml|*.yml`` 加载全部 bundle，加载期做校验。"""
        store = cls()
        root = Path(directory)
        if not root.exists():
            return store
        for path in sorted(root.rglob("*.y*ml")):
            raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            store.add(PromptTemplate.model_validate(raw))
        return store

    def get(self, name: str, version: str) -> PromptTemplate:
        template = self._templates.get((name, version))
        if template is None:
            raise GatewayError(ErrorCode.unknown_prompt_template, "Prompt 模板不存在", 400)
        return template

    def render(self, selection: PromptSelection) -> Message:
        return self.get(selection.name, selection.version).render(selection.variables)

    def build_messages(
        self,
        messages: list[Message],
        selection: PromptSelection | None,
    ) -> list[Message]:
        """把受控系统消息前置注入调用上下文（§6.2）。"""
        if selection is None:
            return messages
        return [self.render(selection), *messages]

    def __len__(self) -> int:
        return len(self._templates)
