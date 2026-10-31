"""HelloAgents 统一 LLM 接口
基于 OpenAI Compatible API，支持：
- invoke()：非流式，返回完整 Assistant Message
- stream()：流式，yield 结构化 chunk（支持思考链 + 文本 + 工具调用）
- Tool Calling：invoke() / stream() 均正确传递 tools
- 配置优先级：用户参数 > provider 专属配置 > 默认配置
"""

import os
from typing import Any, Dict, Iterator, Literal, Optional

from openai import OpenAI

from .Exception import HelloAgentsException
from .config import  Config
#支持的 LLM 厂商
SUPPORTED_PROVIDERS = Literal[
    "openai",
    "deepseek",
    "qwen",
    "modelscope",
    "kimi",
    "zhipu",
    "ollama",
    "vllm",
    "local",
    "custom",
    "auto",
]

DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "openai": {
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "base_url": "https://api.openai.com/v1",
    },
    "deepseek": {
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url": "https://api.deepseek.com/v1",
    },
    "qwen": {
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",##
        "base_url_env": "DASHSCOPE_BASE_URL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "modelscope": {
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "api_key_env": "MODELSCOPE_API_KEY",
        "base_url_env": "MODELSCOPE_BASE_URL",
        "base_url": "https://api-inference.modelscope.cn/v1",
    },
    "kimi": {
        "model": "moonshot-v1-8k",
        "api_key_env": "KIMI_API_KEY",
        "base_url_env": "KIMI_BASE_URL",
        "base_url": "https://api.moonshot.cn/v1",
    },
    "zhipu": {
        "model": "glm-4",
        "api_key_env": "ZHIPU_API_KEY",
        "base_url_env": "ZHIPU_BASE_URL",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "ollama": {
        "model": "qwen3.5:2b",
        "api_key_env": "OLLAMA_API_KEY",
        "base_url_env": "OLLAMA_HOST",
        "api_key": "ollama",
        "base_url": "http://localhost:11434/v1",
    },
    "vllm": {
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "api_key_env": "VLLM_API_KEY",
        "base_url_env": "VLLM_HOST",
        "api_key": "vllm",
        "base_url": "http://localhost:8000/v1",
    },
    "custom": {
        "model": "custom-model",
        "api_key_env": "LLM_API_KEY",
        "base_url_env": "LLM_BASE_URL",
    },
}


def _env(name: Optional[str]) -> Optional[str]:
    """读取环境变量；空字符串按未配置处理。"""
    if not name:
        return None
    value = os.getenv(name)
    return value if value else None


#: 写了等于没写的占位值（多见于从 .env.example 复制后忘记改的项）。
_PLACEHOLDER_VALUES = frozenset({
    "xxx", "sk-xxx", "ms-xxx", "***", "none", "null",
    "placeholder", "changeme", "your-key-here",
})


def _real_env(name: Optional[str]) -> Optional[str]:
    """读取环境变量；空字符串与常见占位值按未配置处理。"""
    if not name:
        return None
    value = os.getenv(name)
    if not value:
        return None
    if value.strip().lower() in _PLACEHOLDER_VALUES:
        return None
    return value


def _infer_provider_from_model(model_name: Optional[str]) -> Optional[str]:
    """从模型名推断 provider；名字模棱两可时返回 None（不硬猜）。

    只认无歧义的写法：
    * ``xxx:tag``（冒号标签）→ 本地 ollama（如 qwen3.5:2b、deepseek-r1:8b）；
    * 名字里带 deepseek / gpt- / moonshot-kimi / glm / qwen-plus-max-turbo
      → 对应云端渠道。
    其他一律返回 None，走常规的环境变量检测。
    """
    if not model_name:
        return None
    m = model_name.strip().lower()
    if not m or "://" in m:
        return None
    if ":" in m:
        # 云端模型 id 不用冒号标签写法，这基本一定是 ollama（或兼容端）
        return "ollama"
    if "deepseek" in m:
        return "deepseek"
    if m.startswith(("gpt-", "chatgpt", "o1", "o3")) or "openai" in m:
        return "openai"
    if "moonshot" in m or "kimi" in m:
        return "kimi"
    if "glm" in m:
        return "zhipu"
    if any(tag in m for tag in ("qwen-plus", "qwen-max", "qwen-turbo", "qwen-flash")):
        return "qwen"
    return None


def _auto_detect_provider(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> str:
    """仅在用户没有指定 provider 时进行自动检测。

    注意：
    如果 provider 已经显式指定，就绝不调用这个函数覆盖用户选择。

    优先级：模型名线索 > base_url 线索 > 各家真实 API Key。
    OLLAMA_HOST 这类本地变量不再一票否决——它经常是历史残留，
    而模型名（deepseek-chat）才是用户当下真实意图。
    """
    # 0. 模型名无歧义时直接定（deepseek-chat 不可能是 ollama 本地模型；
    #    反过来 "model:tag" 冒号写法基本一定是 ollama）。
    hinted = _infer_provider_from_model(
        model_name or _env("LLM_MODEL_ID") or ""
    )
    if hinted:
        key_env = DEFAULT_CONFIG.get(hinted, {}).get("api_key_env")
        if hinted in ("ollama", "vllm") or _real_env(key_env):
            return hinted

    if base_url:
        url = base_url.lower()
        if "api.openai.com" in url:
            return "openai"
        if "deepseek.com" in url:
            return "deepseek"
        if "dashscope.aliyuncs.com" in url:
            return "qwen"
        if "modelscope" in url:
            return "modelscope"
        if "moonshot.cn" in url:
            return "kimi"
        if "bigmodel.cn" in url:
            return "zhipu"
        if ":11434" in url or "ollama" in url:
            return "ollama"
        if ":8000" in url or "vllm" in url:
            return "vllm"
       
    if api_key:
        key = api_key.lower()
        if key == "ollama":
            return "ollama"
        if key == "vllm":
            return "vllm"
       
        if api_key.startswith("ms-"):
            return "modelscope"

    # 各家云端 Key：占位值（sk-xxx / ms-xxx / xxx）不算数。
    # 注意排在 OLLAMA_HOST 之前——那个变量经常是历史残留，
    # 不能让它一票否决用户真实配置的云端 Key。
    if _real_env("DEEPSEEK_API_KEY"):
        return "deepseek"
    if _real_env("DASHSCOPE_API_KEY"):
        return "qwen"
    if _real_env("MODELSCOPE_API_KEY"):
        return "modelscope"
    if _real_env("KIMI_API_KEY") or _real_env("MOONSHOT_API_KEY"):
        return "kimi"
    if _real_env("ZHIPU_API_KEY") or _real_env("GLM_API_KEY"):
        return "zhipu"
    if _real_env("OPENAI_API_KEY"):
        return "openai"

    # 本地模型：只有走到这里（无模型线索、无云端 Key）才考虑。
    if _env("OLLAMA_HOST") or _real_env("OLLAMA_API_KEY"):
        return "ollama"
    if _env("VLLM_HOST") or _real_env("VLLM_API_KEY"):
        return "vllm"

    return "openai"




class LLM:
    """统一的 OpenAI-Compatible LLM 客户端。

    参数优先级：
        用户传参
            ↓
        provider 专属配置
            ↓
        默认配置

    例如：

        llm = HelloAgentsLLM(provider="ollama")

    等价于使用：
        provider = ollama
        model = DEFAULT_CONFIG["ollama"]["model"]
        api_key = OLLAMA_API_KEY / "ollama"
        base_url = OLLAMA_HOST / 默认 Ollama 地址

    如果用户显式传参：

        llm = HelloAgentsLLM(
            provider="ollama",
            model="qwen3.5:2b",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            temperature=0.2,
        )

    那么这些用户参数都会覆盖配置。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[SUPPORTED_PROVIDERS] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        config:Optional[Config]=None,
        **kwargs: Any,
    ):
        self.global_config = config or Config.from_env()
        if provider and provider.lower() != "auto":
            # 1. 用户明确指定 provider
            self.provider = provider.lower()
        else:
            # 2. 用户没有指定 provider：先看 LLM_PROVIDER / Config，
            #    仍为 auto 则按环境变量自动检测（有 deepseek key 就走 deepseek，
            #    而不是死守 ollama localhost 导致 Connection error）
            configured = (self.global_config.default_provider or "auto").lower()
            if configured and configured != "auto":
                self.provider = configured
            else:
                self.provider = _auto_detect_provider(
                    api_key or _env("LLM_API_KEY"),
                    base_url or _env("LLM_BASE_URL"),
                    model or _env("LLM_MODEL_ID"),
                )
        #用户指定的 provider 不在支持的列表中
        if self.provider not in DEFAULT_CONFIG:
            raise HelloAgentsException(
                f"不支持的 provider: {self.provider}"
            )

        config = DEFAULT_CONFIG[self.provider]

        # ----------------------------------------------------
        # 2. model：用户 > provider 配置 > 默认配置
        # ----------------------------------------------------
        provider_model = config.get("model")
        default_model = _env("LLM_MODEL_ID")

        # 注意：
        # 用户没有 model 时，优先使用 provider 模型。
        # 如果希望用统一的 LLM_MODEL_ID，可把它放进 provider 配置，
        # 或直接在初始化时传 model。
        self.model = (
            model
            or default_model
            or provider_model
        )

        # ----------------------------------------------------
        # 3. api_key：用户 > provider > 默认配置
        # ----------------------------------------------------
        provider_api_key = _real_env(config.get("api_key_env"))
        default_api_key = _real_env("LLM_API_KEY")

        self.api_key = (
            api_key
            or provider_api_key
            or config.get("api_key")
            or default_api_key
        )

        provider_base_url = _env(config.get("base_url_env"))
        default_base_url = _env("LLM_BASE_URL")

        self.base_url = (
            base_url
            or provider_base_url
            or config.get("base_url")
            or default_base_url
        )


        self.temperature = (
            temperature
            if temperature is not None
            else config.get("temperature", 0.7)
        )

        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else config.get("max_tokens")
        )

        self.timeout = (
            timeout
            if timeout is not None
            else int(_env("LLM_TIMEOUT") or config.get("timeout", 60))
        )

        # 初始化时额外保存的请求参数
        self.kwargs = kwargs

        # ----------------------------------------------------
        # 6. 参数最终检查
        # ----------------------------------------------------
        if not self.model:
            raise HelloAgentsException(
                "未找到模型。请传入 model，或配置 provider 默认模型。"
            )

        if not self.base_url:
            raise HelloAgentsException(
                f"provider={self.provider} 未配置 base_url。"
            )

        if not self.api_key:
            raise HelloAgentsException(
                f"provider={self.provider} 未配置 api_key"
                f"（请设置 {config.get('api_key_env')} 或 LLM_API_KEY）。"
            )

        self._client = self._create_client()
    def _create_client(self) -> OpenAI:
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    # ========================================================
    # 请求参数
    # ========================================================

    def _build_request_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """统一构造 OpenAI chat.completions.create 参数。

        用户本次调用的参数优先级最高。
        """
        request = dict(self.kwargs)

        request["temperature"] = self.temperature
        if self.max_tokens is not None:
            request["max_tokens"] = self.max_tokens

        request.update(kwargs)

        # 避免内部控制参数泄漏
        request.pop("stream", None)

        # None 参数尽量不发送
        return {
            key: value
            for key, value in request.items()
            if value is not None
        }

    # ========================================================
    # invoke：非流式
    # ========================================================

    def invoke(
        self,
        messages: list[dict],
        **kwargs: Any,
    ):
        """非流式调用。

        返回：
            ChatCompletionMessage

        重要：
            不再只返回 message.content，
            因此 Tool Calling 时可以读取：

                response.tool_calls
                response.content
        """
        try:
            request_kwargs = self._build_request_kwargs(kwargs)

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                **request_kwargs,
            )

            return response.choices[0].message

        except Exception as e:
            raise HelloAgentsException(
                f"LLM调用失败 [{self.provider}/{self.model}]: {e}"
            ) from e

    # ========================================================
    # stream：流式（结构化输出）
    # ========================================================

    def stream(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        """流式调用，yield 结构化 chunk。

        每个 chunk 是一个字典：
            {"type": "reasoning", "content": "..."}   — 思考链片段
            {"type": "text", "content": "..."}        — 正式回复片段
            {"type": "tool_calls", "content": [...]}  — 工具调用片段

        用法示例：

            # 只打印正式回复
            for chunk in llm.stream(messages):
                if chunk["type"] == "text":
                    print(chunk["content"], end="", flush=True)

            # 同时显示思考过程
            for chunk in llm.stream(messages):
                if chunk["type"] == "reasoning":
                    print(f"[思考] {chunk['content']}", end="", flush=True)
                elif chunk["type"] == "text":
                    print(chunk["content"], end="", flush=True)
        """
        try:
            request_kwargs = self._build_request_kwargs(kwargs)

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **request_kwargs,
            )

            for chunk in response:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                reasoning = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "reasoning", None)
                )
                if reasoning:
                    yield {"type": "reasoning", "content": reasoning}

                content = delta.content
                if content:
                    yield {"type": "text", "content": content}

                tool_calls = getattr(delta, "tool_calls", None)
                if tool_calls:
                    yield {"type": "tool_calls", "content": tool_calls}

        except Exception as e:
            raise HelloAgentsException(
                f"LLM流式调用失败 [{self.provider}/{self.model}]: {e}"
            ) from e

    # ========================================================
    # 配置查看
    # ========================================================

    def get_config(self) -> Dict[str, Any]:
        """返回当前最终生效配置。

        API Key 只显示是否存在，不返回真实密钥。
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": "***" if self.api_key else None,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
        }


HelloAgentsLLM = LLM
