"""放哪里"""
class HelloAgentsException(Exception):
    """HelloAgents 基础异常"""
    def __init__(self, problem: str):
        super().__init__(problem)
        self.problem = problem

    def __str__(self):
        # 固定前缀 + 具体异常消息
        return f"配置异常：\n{self.problem}"

class LLMException(HelloAgentsException):
    """LLM 调用相关异常"""
    def __init__(self, problem: str):
            super().__init__(problem)
            self.problem = problem
    
    def __str__(self):
           
            return f"LLM调用异常：\n{self.problem}"
    

class AgentException(HelloAgentsException):
    """Agent 运行相关异常"""
    def __init__(self, problem: str):
            super().__init__(problem)
            self.problem = problem
    
    def __str__(self):
       
        return f"Agent运行异常：\n{self.problem}"

class ConfigException(HelloAgentsException):
    """配置相关异常"""
    def __init__(self, problem: str):
        super().__init__(problem)
        self.problem = problem
    
    def __str__(self):
        return f"配置异常：\n{self.problem}"

class ToolException(HelloAgentsException):
    """工具执行相关异常"""
    def __init__(self, problem: str):
        super().__init__(problem)
        self.problem = problem

    def __str__(self):
        return f"工具执行异常：\n{self.problem}"
