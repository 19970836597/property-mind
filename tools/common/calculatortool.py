import ast
import operator
from typing import List
from ..tools import *

class CalculatorTool(Tool):

    def __init__(self):
        super().__init__(
            name="calculator",
            description="用于执行数学计算，例如加减乘除、幂运算等"
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="expression",
                type="string",
                description="需要计算的数学表达式，例如 123 * 456",
                required=True
            )
        ]

    def run(self, parameters):
        expression = parameters["expression"]

        try:
            result = self._calculate(expression)
            return str(result)
        except Exception as e:
            return f"计算失败：{e}"


    def _calculate(self, expression):
        """
        安全地计算简单数学表达式。
        不直接使用 eval。
        """

        operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
            ast.Mod: operator.mod,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }

        def calc(node):

            if isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return node.value
                raise ValueError("不支持的值")

            if isinstance(node, ast.BinOp):
                op = operators.get(type(node.op))
                if op is None:
                    raise ValueError("不支持的运算符")

                return op(
                    calc(node.left),
                    calc(node.right)
                )

            if isinstance(node, ast.UnaryOp):
                op = operators.get(type(node.op))
                if op is None:
                    raise ValueError("不支持的运算符")

                return op(calc(node.operand))

            raise ValueError("不支持的表达式")

        tree = ast.parse(expression, mode="eval")

        return calc(tree.body)