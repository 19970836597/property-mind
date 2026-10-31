"""
职责配置llm 与系统
支持从环境变量中改变
"""
import os
from typing import Optional, Dict

from pydantic import BaseModel
from typing_extensions import Any


#llm配置  model temputure  max_token  provider
#系统配置  debug  log_level
#其他配置 max_history_lenth
class  Config(BaseModel):
     #llm
     
     default_provider:str="auto"
     default_token:str=""
     temperature:float=0.7
     max_tokens:Optional[int]= None
     timeout:int=60

     debug:bool =False
     log_level:str="INFO"


     max_history_length:int=100

     @classmethod   #省去实例化对象
     def from_env(cls)->"Config":
          return cls(default_provider=os.getenv("LLM_PROVIDER", "auto").strip().lower() or "auto",
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("MAX_TOKENS")) if os.getenv("MAX_TOKENS") else None,

          )
     def to_dict(self)->Dict[str,Any]:
         return self.model_dump()