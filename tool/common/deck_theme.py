# -*- coding: utf-8 -*-
"""演示文稿主题（Theme）—— 样式知识的载体

分层原则
--------
一个 PPT 的样式分两种，来源不同：

    语义样式   达标=绿 / 预警=黄 / 未达标=红
              → 这是**业务约定**，应该由技能（知识）决定

    排版美学   字号 19pt / 边距 0.55" / 行距 1.25
              → 这是**工艺默认值**，工具给个好看默认，允许覆盖

两者都放进本模块的 theme 结构里，由技能通过参数传给 ``deck_build``。
渲染工具本身不再持有任何魔法数字。

主题结构
--------
::

    {
      "name": "物业经营",
      "canvas":  {"width_in": 13.333, "height_in": 7.5, "margin_in": 0.55,
                  "ratio": "16:9"},
      "fonts":   {"title": "微软雅黑", "body": "微软雅黑",
                  "title_size": 19, "body_size": 12, "kicker_size": 11,
                  "card_title_size": 12, "card_body_size": 10,
                  "footnote_size": 8.5, "cover_title_size": 30,
                  "kpi_value_size": 21, "kpi_label_size": 10.5,
                  "chart_size": 10, "line_spacing": 1.25},
      "colors":  {"title": "142A4C", "accent": "1E88E5", "muted": "6B7786",
                  "page_bg": "FFFFFF", "panel": "F2F5F9", "text": "333D4D",
                  "line": "E3EAF2", "on_dark": "FFFFFF", "on_dark_muted": "8FC4F0",
                  "ok": "2EA06A", "warn": "E8A31D", "bad": "D9453D",
                  "info": "1E88E5"},
      "palette": ["1E88E5", "5BC0DE", "142A4C", "E8A31D", "2EA06A", "6B7786"],
      "layout":  {"body_top_in": 1.55, "so_what_top_in": 6.24,
                  "cover_band_in": 2.5, "kpi_columns": 3, "cards_columns": 2}
    }

``colors`` 用 6 位十六进制字符串（不带 #），便于写进 JSON 由技能维护。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "DEFAULT_THEME",
    "SCENE_THEMES",
    "build_theme",
    "validate_theme",
    "list_scene_themes",
    "describe_theme",
]


#: 默认主题 —— 工具自带的"好看默认值"，不传 theme 时用它
DEFAULT_THEME: Dict[str, Any] = {
    "name": "默认",
    "canvas": {
        "width_in": 13.333,
        "height_in": 7.5,
        "margin_in": 0.55,
        "ratio": "16:9",
    },
    "fonts": {
        "title": "微软雅黑",
        "body": "微软雅黑",
        "title_size": 19,
        "body_size": 12,
        "kicker_size": 11,
        "card_title_size": 12,
        "card_body_size": 10,
        "footnote_size": 8.5,
        "cover_title_size": 30,
        "cover_subtitle_size": 15,
        "cover_project_size": 22,
        "kpi_value_size": 21,
        "kpi_label_size": 10.5,
        "chart_size": 10,
        "line_spacing": 1.25,
    },
    "colors": {
        "title": "142A4C",
        "accent": "1E88E5",
        "muted": "6B7786",
        "page_bg": "FFFFFF",
        "panel": "F2F5F9",
        "text": "333D4D",
        "line": "E3EAF2",
        "on_dark": "FFFFFF",
        "on_dark_muted": "8FC4F0",
        "ok": "2EA06A",
        "warn": "E8A31D",
        "bad": "D9453D",
        "info": "1E88E5",
    },
    "palette": ["1E88E5", "5BC0DE", "142A4C", "E8A31D", "2EA06A", "6B7786"],
    "layout": {
        "body_top_in": 1.55,
        "so_what_top_in": 6.24,
        "cover_band_in": 2.5,
        "kpi_columns": 3,
        "cards_columns": 2,
        "page_number": True,
    },
    # 装饰细节（决定"样式"而不只是"颜色"）。
    # 只登记**渲染器真正实现了**的开关 —— 没实现的宁可不写，
    # 免得业务方改了没效果的字段还以为生效了。
    "decor": {
        "card_shape": "rect",      # rect | rounded
        "card_radius": 0.12,       # rounded 时的圆角比例（0~0.5）
        "header_rule": True,       # 标题下方横线
        "kicker_bar": False,       # 阶段标签前的小色条
        "so_what_style": "panel",  # panel(整块底色) | rule(仅左侧色条) | plain
        "cover_band": True,        # 封面顶部色带
        "title_dark_bg": False,    # 深色主题时标题用浅色（由底色亮度自动判定）
    },
}


# ===================================================================
# 场景预设（示例知识，真实场景应由技能自带 spec）
# ===================================================================

#: 物业经营：稳重、深蓝、接近"公司年报"气质。
#: 说明：它刻意沿用 DEFAULT_THEME 的配色 —— 作为"参考外观"存在，
#: 便于业务方对比"默认长这样，动漫主题长那样"。
SCENE_THEMES: Dict[str, Dict[str, Any]] = {
    "property": {
        "name": "物业经营（稳重深蓝）",
        "canvas": {"ratio": "16:9"},
        "fonts": {"title": "微软雅黑", "body": "微软雅黑"},
        "colors": {
            "title": "142A4C",
            "accent": "1E88E5",
            "muted": "6B7786",
            "panel": "F2F5F9",
            "text": "333D4D",
            "ok": "2EA06A",
            "warn": "E8A31D",
            "bad": "D9453D",
            "on_dark": "FFFFFF",
            "on_dark_muted": "8FC4F0",
        },
        "palette": ["1E88E5", "5BC0DE", "142A4C", "E8A31D", "2EA06A", "6B7786"],
    },

    # 动漫主题：活泼、粉紫、高饱和 —— 与物业形成强烈视觉对比
    "anime": {
        "name": "动漫（粉紫活力）",
        "canvas": {"ratio": "16:9"},
        "fonts": {"title": "微软雅黑", "body": "微软雅黑",
                  "title_size": 21, "cover_title_size": 34},
        "colors": {
            "title": "3B1E5A",          # 深紫（标题）
            "accent": "FF5FA2",          # 樱粉（强调）
            "muted": "8A7BA8",           # 灰紫
            "panel": "FFF0F6",           # 淡粉底
            "text": "4A3B63",            # 紫灰正文
            "line": "F3D9E8",            # 粉线
            "on_dark": "FFFFFF",
            "on_dark_muted": "FFD6E7",
            "ok": "2ED8A7",              # 青绿（达标）
            "warn": "FFB13D",            # 橙黄（预警）
            "bad": "FF4D6D",             # 玫红（未达标）
            "info": "7C5CFF",            # 亮紫
        },
        "palette": ["FF5FA2", "7C5CFF", "2ED8A7", "FFB13D", "4AC7FF", "FF4D6D"],
        "decor": {"card_shape": "rounded", "card_radius": 0.22,
                  "kicker_bar": True},
    },

    # =================================================================
    # 以下按「汇报场景」分类的配色方案（用户提供的参考）
    # =================================================================

    # ---- 1. 商务汇报与工作总结：稳重、专业、秩序感 ----
    "business": {
        "name": "商务汇报（经典深蓝）",
        "fonts": {"title_size": 19, "cover_title_size": 30},
        "colors": {
            "title": "1B3A5C",           # 深蓝 / 藏青
            "accent": "2E6BA8",
            "muted": "6E7B8B",
            "page_bg": "FFFFFF",         # 纯白
            "panel": "F4F6F9",           # 浅灰
            "text": "2D3748",
            "line": "DCE3EC",
            "on_dark": "FFFFFF",
            "on_dark_muted": "A8C4E0",
            "ok": "3F8F6B",
            "warn": "C8862E",
            "bad": "C0392B",
            "info": "2E6BA8",
        },
        "palette": ["2E6BA8", "5B8FC7", "1B3A5C", "C8862E", "3F8F6B", "7A8794"],
        "decor": {"card_shape": "rect", "header_rule": True},
    },
    "business_vivid": {
        "name": "商务汇报（克莱因蓝 + 亮橙）",
        "fonts": {"title_size": 19, "cover_title_size": 31},
        "colors": {
            "title": "0B2A6B",
            "accent": "002FA7",          # 克莱因蓝
            "muted": "77828F",
            "page_bg": "FFFFFF",
            "panel": "F1F3F7",           # 灰白
            "text": "2A3138",
            "line": "D8DEE7",
            "on_dark": "FFFFFF",
            "on_dark_muted": "FFC49B",
            "ok": "1E8E5A",
            "warn": "FF6B1A",            # 亮橙
            "bad": "D62B1F",
            "info": "002FA7",
        },
        "palette": ["002FA7", "FF6B1A", "3D4A5C", "FFC44D", "4A90D9", "9AA5B1"],
        "decor": {"card_shape": "rect", "kicker_bar": True},
    },
    "business_mono": {
        "name": "商务汇报（黑白灰 + 单一亮色）",
        "fonts": {"title_size": 19, "cover_title_size": 32},
        "colors": {
            "title": "111111",
            "accent": "E8452C",          # 全篇唯一亮色
            "muted": "7A7A7A",
            "page_bg": "FFFFFF",
            "panel": "F5F5F5",
            "text": "333333",
            "line": "E2E2E2",
            "on_dark": "FFFFFF",
            "on_dark_muted": "FFB3A3",
            "ok": "2F7D5B",
            "warn": "B07C1E",
            "bad": "E8452C",
            "info": "4A4A4A",
        },
        "palette": ["111111", "4A4A4A", "8C8C8C", "E8452C", "C9C9C9", "6B6B6B"],
        "decor": {"card_shape": "rect", "kicker_bar": False,
                  "so_what_style": "rule"},
    },

    # ---- 2. 科技 / 互联网 / AI：未来感、清透 ----
    "tech": {
        "name": "科技（深空蓝 + 霓虹）",
        "fonts": {"title_size": 20, "cover_title_size": 33},
        "colors": {
            "title": "E6F1FF",           # 浅色标题（深底）
            "accent": "00E5FF",          # 青蓝
            "muted": "7A93AC",
            "page_bg": "0A1A2F",         # 深空蓝底
            "panel": "13263D",
            "text": "C7D7E8",
            "line": "1E3A5F",
            "on_dark": "FFFFFF",
            "on_dark_muted": "8FD4FF",
            "ok": "00E676",              # 荧光绿
            "warn": "FFAB00",
            "bad": "FF5252",
            "info": "7C4DFF",            # 电光紫
        },
        "palette": ["00E5FF", "7C4DFF", "00E676", "2979FF", "18FFFF", "B388FF"],
        "decor": {"card_shape": "rect", "header_rule": True,
                  "kicker_bar": True, "so_what_style": "rule",
                  "cover_band": True},
    },

    # ---- 3. 学术答辩 / 论文 / 研究报告：严谨、去装饰 ----
    "academic": {
        "name": "学术答辩（墨黑 + 单一辅助色）",
        "fonts": {"title_size": 18, "cover_title_size": 28},
        "colors": {
            "title": "1A1A1A",
            "accent": "1F4E79",          # 单一辅助色：深蓝
            "muted": "6E6E6E",
            "page_bg": "FFFFFF",         # 纯白 + 大留白
            "panel": "F7F7F7",
            "text": "262626",
            "line": "E0E0E0",
            "on_dark": "FFFFFF",
            "on_dark_muted": "B8CFE0",
            "ok": "2E7D5B",
            "warn": "9A7B1E",
            "bad": "A8322A",
            "info": "1F4E79",
        },
        "palette": ["1F4E79", "4A4A4A", "7F7F7F", "2E7D5B", "A6A6A6", "1A1A1A"],
        "decor": {"card_shape": "rect", "header_rule": True,
                  "kicker_bar": False, "so_what_style": "rule"},
    },
    "academic_morandi": {
        "name": "学术答辩（莫兰迪灰蓝灰紫）",
        "fonts": {"title_size": 18, "cover_title_size": 28},
        "colors": {
            "title": "3E4A59",
            "accent": "7E8C9B",          # 低饱和灰蓝
            "muted": "8B8579",
            "page_bg": "FFFFFF",
            "panel": "F2F0EE",
            "text": "45403A",
            "line": "DDD8D3",
            "on_dark": "FFFFFF",
            "on_dark_muted": "C9D3DC",
            "ok": "9CAF9F",
            "warn": "C4B7A6",
            "bad": "B08A88",
            "info": "A99BB5",            # 灰紫
        },
        "palette": ["8A9BAE", "A99BB5", "9CAF9F", "C4B7A6", "7E8C9B", "B8AEA4"],
        "decor": {"card_shape": "rect", "header_rule": False,
                  "so_what_style": "plain"},
    },

    # ---- 4. 党建 / 政务：庄严、规范、力量感 ----
    "gov": {
        "name": "党建政务（中国红 + 金）",
        "fonts": {"title_size": 20, "cover_title_size": 31},
        "colors": {
            "title": "8C1C13",           # 深红
            "accent": "C8102E",          # 中国红
            "muted": "8A7A6D",
            "page_bg": "FFFFFF",
            "panel": "FBF6EF",           # 爵士白（暖）
            "text": "3A2E28",
            "line": "EBDCC8",
            "on_dark": "FFFFFF",
            "on_dark_muted": "F0D9A8",
            "ok": "2E7D5B",
            "warn": "C9A227",            # 金
            "bad": "C8102E",
            "info": "2A4B7C",            # 深蓝（红蓝搭配）
        },
        "palette": ["C8102E", "C9A227", "8C1C13", "2A4B7C", "D4AF37", "8A8F98"],
        "decor": {"card_shape": "rect", "header_rule": True,
                  "kicker_bar": True, "so_what_style": "panel"},
    },

    # ---- 5. 文化 / 国风 / 艺术：诗意、典雅 ----
    "oriental": {
        "name": "国风水墨（黛蓝 + 朱砂）",
        "fonts": {"title_size": 19, "cover_title_size": 30},
        "colors": {
            "title": "2B3A4A",           # 黛蓝
            "accent": "9C3D2E",          # 朱砂红
            "muted": "7A756A",
            "page_bg": "FFFFFF",
            "panel": "F5F1E8",           # 宣纸白
            "text": "3C3A34",            # 墨色
            "line": "E0D8C8",
            "on_dark": "FFFFFF",
            "on_dark_muted": "E8D5B8",
            "ok": "6B7A6E",              # 黛绿
            "warn": "B8860B",
            "bad": "9C3D2E",
            "info": "5A5A52",            # 墨灰
        },
        "palette": ["2B3A4A", "9C3D2E", "5A5A52", "8A9BA8", "C8B89A", "6B7A6E"],
        "decor": {"card_shape": "rect", "header_rule": True,
                  "kicker_bar": False, "so_what_style": "rule"},
    },

    # 艺术 / 活动策划：马卡龙（轻快）
    "macaron": {
        "name": "艺术创意（马卡龙轻快）",
        "fonts": {"title_size": 20, "cover_title_size": 32},
        "colors": {
            "title": "4A3F5C",
            "accent": "F49AC1",          # 马卡龙粉
            "muted": "8E8299",
            "page_bg": "FFFFFF",
            "panel": "FDF3F8",
            "text": "4F4658",
            "line": "F0E2EC",
            "on_dark": "FFFFFF",
            "on_dark_muted": "FFD9EA",
            "ok": "7FD1AE",
            "warn": "F7C873",
            "bad": "F58A8A",
            "info": "9BB8F0",
        },
        "palette": ["F49AC1", "9BB8F0", "7FD1AE", "F7C873", "C3A6E0", "F58A8A"],
        "decor": {"card_shape": "rounded", "card_radius": 0.24,
                  "kicker_bar": True},
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深合并，override 优先。列表整体替换（不逐项合并）。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def build_theme(
    scene: Optional[str] = None,
    theme: Optional[Dict[str, Any]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """构造最终主题。

    优先级：``theme``（显式完整主题）> ``scene`` 预设 > 默认值，
    最后再叠加 ``overrides``（可用于单次渲染微调）。

    Args:
        scene: 场景预设名，见 :data:`SCENE_THEMES`。
        theme: 直接给一个主题字典（会与默认值深合并，保证字段齐全）。
        overrides: 零散覆盖，如 ``colors={"accent": "FF0000"}``。
    """
    base = copy.deepcopy(DEFAULT_THEME)

    if scene and scene in SCENE_THEMES:
        base = _deep_merge(base, SCENE_THEMES[scene])
        base.setdefault("name", scene)

    if theme:
        base = _deep_merge(base, theme)

    if overrides:
        base = _deep_merge(base, {k: v for k, v in overrides.items() if v is not None})

    return base


def validate_theme(theme: Dict[str, Any]) -> List[str]:
    """校验主题，返回问题列表。"""
    problems: List[str] = []
    if not isinstance(theme, dict):
        return ["theme 不是字典"]

    def need_color(where: str, value: Any) -> None:
        if not isinstance(value, str):
            problems.append(f"{where}: 颜色应为 6 位十六进制字符串，实际 {value!r}")
            return
        text = value.lstrip("#")
        if len(text) != 6:
            problems.append(f"{where}: 颜色 {value!r} 不是 6 位十六进制")
            return
        try:
            int(text, 16)
        except ValueError:
            problems.append(f"{where}: 颜色 {value!r} 含非十六进制字符")

    colors = theme.get("colors") or {}
    if not colors:
        problems.append("colors 为空（语义色缺失）")
    for key in ("ok", "warn", "bad", "title", "accent"):
        if key in colors:
            need_color(f"colors.{key}", colors[key])
        else:
            problems.append(f"colors 缺少 {key}")

    palette = theme.get("palette")
    if not isinstance(palette, list) or not palette:
        problems.append("palette 必须是非空数组")
    else:
        for i, c in enumerate(palette):
            need_color(f"palette[{i}]", c)

    canvas = theme.get("canvas") or {}
    for key, low, high in (("width_in", 1, 60), ("height_in", 1, 60)):
        value = canvas.get(key)
        if not isinstance(value, (int, float)):
            problems.append(f"canvas.{key} 应为数字")
        elif not (low <= value <= high):
            problems.append(f"canvas.{key}={value} 超出合理范围 {low}~{high}")

    fonts = theme.get("fonts") or {}
    if not fonts.get("title") or not fonts.get("body"):
        problems.append("fonts.title / fonts.body 不能为空")

    for key in ("body_top_in", "so_what_top_in"):
        value = (theme.get("layout") or {}).get(key)
        if value is not None and not isinstance(value, (int, float)):
            problems.append(f"layout.{key} 应为数字（英寸）")

    height = canvas.get("height_in") or 7.5
    so_top = (theme.get("layout") or {}).get("so_what_top_in")
    if isinstance(so_top, (int, float)) and so_top >= height:
        problems.append(
            f"layout.so_what_top_in={so_top} 超过画布高度 {height}，结论栏会跑到页面外"
        )

    return problems


def list_scene_themes() -> List[Dict[str, str]]:
    return [{"key": k, "name": v.get("name", k)} for k, v in SCENE_THEMES.items()]


def describe_theme(theme: Dict[str, Any]) -> str:
    """人类可读的主题摘要（便于验收时肉眼确认两份确实不同）。"""
    colors = theme.get("colors") or {}
    fonts = theme.get("fonts") or {}
    canvas = theme.get("canvas") or {}
    return "\n".join([
        f"主题: {theme.get('name', '(未命名)')}",
        f"  画布: {canvas.get('width_in')}×{canvas.get('height_in')} 英寸 "
        f"({canvas.get('ratio', '?')})，边距 {canvas.get('margin_in')}\"",
        f"  字体: 标题 {fonts.get('title')} / 正文 {fonts.get('body')}",
        f"  语义色: 达标 #{colors.get('ok')} · 预警 #{colors.get('warn')} "
        f"· 未达标 #{colors.get('bad')}",
        f"  标题色 #{colors.get('title')} · 强调色 #{colors.get('accent')}",
        f"  图表调色板: {', '.join('#' + str(c) for c in (theme.get('palette') or []))}",
    ])
