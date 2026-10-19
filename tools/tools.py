from abc import ABC, abstractmethod
from typing import Any, Dict, List



from pydantic import BaseModel
class ToolParameter(BaseModel):
    """工具每一个参数的定义"""
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None

class Tool(ABC):
    """
    name:str
    
    description:str
    

    """
    def __init__(self,name:str,description:str,expandable:bool=False):
        self.name=name
        self.description=description
        self.expandable=expandable
        
    @abstractmethod
    def run(self,params:Dict[str,Any]):
        """
        执行
        :param params: 参数
        :return:
        """
        pass
    @abstractmethod
    def get_params(self) -> List[ToolParameter]:
        """获取工具参数定义"""
        pass

    def validate_parameters(self, params: Dict[str, Any]) -> bool:
        """验证参数是否包含必需参数"""
        param_defs = self.get_params()
        for p in param_defs:
            if p.required and p.name not in params:
                return False
        return True


def tool_action(name: str, description: str = "", expandable: bool = False):
    """工具动作装饰器"""
    def decorator(func):
        func._action_name = name
        func._action_description = description
        func._action_expandable = expandable
        return func
    return decorator