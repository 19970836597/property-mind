# -*- coding: utf-8 -*-
"""Token 估算与分层预算核算。

为什么不用 tiktoken
-------------------
本项目主模型跑在 ollama（qwen3.5:2b），tiktoken 的 BPE 词表是 OpenAI 系的，
对中文 + qwen 分词器并不准，引进来还多一个重依赖。上下文管理只需要
"够不够、先裁谁"这种量级判断，粗估完全够用 —— 真正的保护来自预算留的
安全余量，不是来自小数点后两位的精确。

估算口径（经验值，宁可偏保守）
------------------------------
* CJK 字符：约 1 字 ≈ 1 token
* ASCII：约 4 字符 ≈ 1 token
偏保守的意思是：估出来**不小于**真实值，这样"没超预算"的判断是可信的。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "estimate_tokens",
    "estimate_messages_tokens",
    "ContextBudget",
    "DEFAULT_CONTEXT_WINDOW",
    "SAFETY_RATIO",
]

#: 默认上下文窗口。qwen3.5:2b 这类小模型常见是 32k，取一个保守的通用值。
#: 真实部署可通过 Config / 构造参数覆盖。
DEFAULT_CONTEXT_WINDOW = 32000

#: 安全余量：只敢用预算的这个比例。
#: 粗估会有误差，且模型回复本身也要占窗口（max_tokens），
#: 所以留 15% 给误差 + 回复，超过就开始压缩。
SAFETY_RATIO = 0.85

#: 每条消息的固定开销（role、分隔符等），OpenAI 系大致是 3~4 token
_PER_MESSAGE_OVERHEAD = 4

#: 按 1:1 计 token 的 Unicode 区间（用码点写，避免字面量范围难以核对）。
#: 覆盖：CJK 统一表意文字（含扩展 A）、CJK 标点、假名、全角字符、谚文。
_WIDE_RANGES = (
    (0x3000, 0x303F),   # CJK 标点：。，、「」等
    (0x3040, 0x30FF),   # 平假名 + 片假名
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 统一表意文字（绝大多数汉字）
    (0xAC00, 0xD7AF),   # 谚文音节
    (0xF900, 0xFAFF),   # CJK 兼容表意文字
    (0xFF00, 0xFFEF),   # 全角字母数字与半角片假名
)


def _is_wide(ch: str) -> bool:
    """这个字符是否按 1 字 ≈ 1 token 计。"""
    code = ord(ch)
    for low, high in _WIDE_RANGES:
        if low <= code <= high:
            return True
        # 区间已升序排列，越过就不必再找
        if code < low:
            break
    return False


def estimate_tokens(text: Any) -> int:
    """粗估一段文本的 token 数。

    对 CJK 按 1:1、其余按 4:1 折算。空值返回 0。
    """
    if not text:
        return 0
    s = text if isinstance(text, str) else str(text)

    cjk = 0
    other = 0
    for ch in s:
        if _is_wide(ch):
            cjk += 1
        else:
            other += 1
    # other 向上取整：半个 token 也要占一个
    return cjk + (other + 3) // 4


def estimate_messages_tokens(messages: Optional[List[Dict[str, Any]]]) -> int:
    """粗估一个 OpenAI 格式 messages 列表的总 token。"""
    total = 0
    for msg in messages or []:
        total += _PER_MESSAGE_OVERHEAD
        total += estimate_tokens(msg.get("content"))
        # 工具调用的 arguments 也占 token
        for call in (msg.get("tool_calls") or []):
            fn = (call or {}).get("function") or {}
            total += estimate_tokens(fn.get("name"))
            total += estimate_tokens(fn.get("arguments"))
    return total


class ContextBudget:
    """上下文预算：总窗口怎么分、各层实际占了多少。

    这个对象既是**约束**（还能放多少）也是**账本**（已经放了多少）——
    后者是 ``ContextManager.describe()`` 能打印出分层占用的原因。
    """

    def __init__(
        self,
        total: int = DEFAULT_CONTEXT_WINDOW,
        safety_ratio: float = SAFETY_RATIO,
        reserve_for_reply: int = 1024,
    ):
        self.total = max(1, int(total))
        self.safety_ratio = float(safety_ratio)
        self.reserve_for_reply = max(0, int(reserve_for_reply))
        #: 分层账本：层名 → token 占用（由 ContextManager 写入）
        self.layers: Dict[str, int] = {}

    @property
    def usable(self) -> int:
        """实际可用预算 = 窗口 × 安全比例 − 给回复留的额度。"""
        return max(1, int(self.total * self.safety_ratio) - self.reserve_for_reply)

    def record(self, layer: str, tokens: int) -> None:
        """记一层的占用（同名层累加，因为工具结果会分多次进来）。"""
        self.layers[layer] = self.layers.get(layer, 0) + max(0, int(tokens))

    def reset(self) -> None:
        self.layers.clear()

    @property
    def used(self) -> int:
        return sum(self.layers.values())

    @property
    def remaining(self) -> int:
        return self.usable - self.used

    def over_budget(self, extra: int = 0) -> bool:
        """加上 extra 之后是否超预算。"""
        return (self.used + max(0, int(extra))) > self.usable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "usable": self.usable,
            "used": self.used,
            "remaining": self.remaining,
            "layers": dict(self.layers),
        }

    def describe(self) -> str:
        """人读的预算分解 —— 对标 Claude Code 的 /context。"""
        lines = [
            f"上下文预算: {self.used:,} / {self.usable:,} token "
            f"(窗口 {self.total:,}，安全比例 {self.safety_ratio:.0%}，"
            f"留给回复 {self.reserve_for_reply:,})"
        ]
        if not self.layers:
            lines.append("  （尚未组装）")
            return "\n".join(lines)

        width = max(len(name) for name in self.layers)
        for name, tokens in self.layers.items():
            pct = tokens / self.usable * 100 if self.usable else 0
            bar = "█" * max(0, min(20, int(pct / 5)))
            lines.append(f"  {name:<{width}}  {tokens:>7,} tok  {pct:>5.1f}% {bar}")
        if self.remaining < 0:
            lines.append(f"  ⚠️ 超出预算 {-self.remaining:,} token，需要压缩")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"ContextBudget(used={self.used}, usable={self.usable})"
