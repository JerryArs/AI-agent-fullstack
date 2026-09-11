"""Prompt 模板资产与渲染（§6.1 / §6.2）。"""

from __future__ import annotations

from string import Template

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_llm_gateway.protocol.errors import ErrorCode, GatewayError
from mini_llm_gateway.protocol.messages import Message


class PromptTemplate(BaseModel):
    """由 Gateway 发布并版本化管理的系统 Prompt 模板。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    system_template: str = Field(min_length=1)
    required_variables: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_placeholders(self) -> PromptTemplate:
        """声明的 required_variables 必须与模板占位符一致（加载期校验）。"""
        placeholders = self._placeholders()
        declared = set(self.required_variables)
        if declared and declared != placeholders:
            raise ValueError(
                f"required_variables {sorted(declared)} 与占位符 {sorted(placeholders)} 不一致"
            )
        return self

    def _placeholders(self) -> set[str]:
        names: set[str] = set()
        template = Template(self.system_template)
        for match in template.pattern.finditer(self.system_template):
            named = match.group("named") or match.group("braced")
            if named:
                names.add(named)
        return names

    def render(self, variables: dict[str, str]) -> Message:
        """渲染受控系统消息；缺变量抛稳定错误码（§6.2）。"""
        try:
            content = Template(self.system_template).substitute(variables)
        except KeyError as exc:
            raise GatewayError(
                ErrorCode.missing_prompt_variable,
                f"缺少 Prompt 变量: {exc.args[0]}",
                400,
            ) from exc
        return Message(role="system", content=content)
