from typing import Any, Optional


class ToolResult:
    """工具执行结果"""

    def __init__(self, success: bool = True, output: Any = None, error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error

    def to_string(self) -> str:
        if self.success:
            return str(self.output) if self.output is not None else ""
        return f"Error: {self.error}" if self.error else "Error"