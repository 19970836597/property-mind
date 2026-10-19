from typing import Any, Dict

from .register import ToolRegister
from .result import ToolResult


class tool_executor:
    """工具执行器"""

    def __init__(self, registry: ToolRegister):
        self.registry = registry

    def execute_tool(self, name: str, params: Dict[str, Any]) -> ToolResult:
        tool = self.registry.get_tool(name)
        if tool is None:
            return ToolResult(success=False, error=f"未找到工具 '{name}'")
        try:
            result = tool.run(params)
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))