
class HelloAgentsException(Exception):
    """HelloAgents 基础异常"""
    pass

class LLMException(HelloAgentsException):
    """LLM 调用相关异常"""
    pass

class AgentException(HelloAgentsException):
    """Agent 运行相关异常"""
    pass

class ConfigException(HelloAgentsException):
    """配置相关异常"""
    pass

class ToolException(HelloAgentsException):
    """工具执行相关异常"""
    pass