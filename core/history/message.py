import enum
from datetime import datetime
from typing import Dict, Any

from pydantic import BaseModel, Field


class MessageRole(enum.Enum):
    """LLM 消息角色  ["system", "user", "assistant"] """
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """
    消息类
    role:MessageRole
    content:str
    timestamp:datetime=None
    metadata；Optional[Dict[str,Any]]=None

    """

    
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
            """转为Openai格式"""
            role_str=self.role.value if isinstance(self.role, MessageRole) else str(self.role)
            #格式不准确问题    isinstance
            return {
                "role": role_str,
                "content": self.content
            }

    def to_str(self) -> str:
            """为agent之间通讯"""
            return f"{self.role}:{self.content}"
