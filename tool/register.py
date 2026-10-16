from typing import Dict, Any, List

from tool.tools import Tool


class ToolRegister:
    """
    工具注册中心

    职责：
        - 注册工具
        - 获取工具
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def add_tool(self, tool: Tool) -> bool:
        """注册工具。

        只接受真正的 Tool。技能（Skill）不再继承 Tool，因此**不能**注册到
        这里 —— 技能应该交给 `capability.CapabilityRouter`。
        返回是否注册成功。
        """
        if not isinstance(tool, Tool):
            kind = type(tool).__name__
            print(
                f"注册失败：'{getattr(tool, 'name', kind)}' 不是 Tool（实际类型 {kind}）。"
                f"技能请注册到 CapabilityRouter，例如 router.add_skill(skill)"
            )
            return False

        if tool.name not in self._tools:
            self._tools[tool.name] = tool
            print(f"工具 '{tool.name}' 注册成功")
            return True

        print(f"工具 '{tool.name}' 已存在")
        return False

    def get_tool(self, name: str) -> Tool | None:
        """根据工具名称获取工具"""
        return self._tools.get(name)


    def get_tools_schema(self) -> List[Dict]:
        """获取所有已注册工具的 Schema"""
        schemas = []
        for tool in self._tools.values():
            

            # 获取工具参数定义
            params = tool.get_params()

            properties = {}
            required = []

            for param in params:

                properties[param.name] = {
                    "type": param.type,
                    "description": param.description
                }

                if param.default is not None:
                    properties[param.name]["default"] = param.default

                if param.required:
                    required.append(param.name)

            # 构造当前工具 Schema
            tool_schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }
            }

            schemas.append(tool_schema)
        
        return schemas
    def execute_tool(self, name: str, input_text: str) -> str:
        """
        执行工具

        Args:
            name: 工具名称
            input_text: 输入参数

        Returns:
            工具执行结果
        """
        if name in self._tools:
            tool = self._tools[name]
            try:
                params = tool.get_params()
                if params:
                    first_param_name = params[0].name
                    return tool.run({first_param_name: input_text})
                else:
                    return tool.run({"input": input_text})
            except Exception as e:
                return f"错误：执行工具 '{name}' 时发生异常: {str(e)}"
        else:
            return f"错误：未找到名为 '{name}' 的工具"