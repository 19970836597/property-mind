# -*- coding: utf-8 -*-
"""PPTX 结构校验工具 —— 填补 ppt-skill 阶段 5.2 的空缺

背景
----
`skills/buttin/ppt_skill/SKILL.md` 的阶段 5.2 原本调用一个项目外部并不存在的
``check_office.py``，导致交付物 ②（``checks.json`` 校验报告）实际上无人生产。
本工具提供该能力，且**不引入任何新依赖**（只用 python-pptx + 标准库）。

它检查什么
----------
1. **可打开性 / 页数**   —— 文件是否合法、页数是否符合预期
2. **关键内容存在性**    —— 「异常科目归因」「当期收缴率」「行动项」等是否真的在页面上
3. **文字溢出（估算）**  —— 文本框字宽/行高是否超出形状边界（抓被裁切的文字）
4. **色彩语义一致性**    —— 只允许既定语义色，避免同色不同义
5. **图表数据对齐**      —— 图表分类数/系列数与数据源是否一致
6. **每页结论句**        —— 每页是否都有非空 so_what（技能纪律：不留白让听众猜）

说明：溢出检测是**基于字体度量的估算**，不是真实排版引擎。它会给出
``candidate``（疑似）与 ``overflow``（确定超出形状盒）两种级别，并报告
置信度，避免把估算当成定论 —— 这正是需要 LibreOffice 兜底的地方。

作为能力使用
------------
    from core.capability import CapabilityRouter
    from tool.common.pptx_check_tool import PptxCheckTool

    router = CapabilityRouter()
    router.add_tool(PptxCheckTool())
    router.execute("pptx_check", {"file_path": "deck.pptx", "out": "checks.json"})
"""

from __future__ import annotations

import json
import os
import unicodedata
import zipfile
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pptx import Presentation

from ..tools import Tool, ToolParameter

__all__ = ["PptxCheckTool", "check_pptx"]

# 允许的语义色（与 build_deck.py 的设计常量一致）
ALLOWED_THEME_COLORS = {
    "142A4C": "深蓝（标题）",
    "1E88E5": "强调蓝",
    "2EA06A": "达标绿",
    "E8A31D": "预警黄",
    "D9453D": "未达标红",
    "6B7786": "灰（脚注）",
    "F2F5F9": "浅底",
    "FFFFFF": "白",
    "5BC0DE": "浅蓝（图表第2色）",
    "000000": "黑",
    # --- 以下为 build_deck.py 内联使用、此前漏登记的合法色 ---
    "8FC4F0": "浅蓝（图表标签）",
    "C8D8EA": "浅灰蓝（分隔）",
    "9FB4CC": "灰蓝（辅助文字）",
    "4A6B93": "中蓝（次级文字）",
    "C3CEDB": "浅灰（表格线/底）",
    "333D4D": "深灰（正文）",
}

# 封面 / 路线图这类页面按设计就没有底部结论句，不参与 so_what 检查
SOWHAT_EXEMPT_SECTIONS = ("封面", "路线图", "附录", "专项深挖")

# 结果状态
PASS = "pass"
WARN = "warn"
FAIL = "fail"


def _char_width(ch: str, font_size: float) -> float:
    """估算单个字符的横向占宽（磅）。

    中文/全角按 1.0 em，西文按约 0.52 em，窄字符更小。
    这是估算，用于发现「明显塞不下」的文本，不做精确排版。
    """
    if ch == "\t":
        return font_size * 2.0
    if ch == " ":
        return font_size * 0.30
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return font_size * 1.0
    if ch in "iljI.,:;'|!()[]":
        return font_size * 0.30
    if ch.isupper():
        return font_size * 0.68
    return font_size * 0.55


def _estimate_text_block(text: str, font_size: float, box_width: float,
                         box_height: float) -> Dict[str, Any]:
    """估算一段文本在给定盒子里的排布。"""
    if box_width <= 0 or font_size <= 0:
        return {"lines": 0, "needed_height": 0.0, "widest_line": 0.0,
                "capacity": 0.0}

    # 逐段落估算折行
    total_lines = 0
    widest = 0.0
    for paragraph in text.split("\n"):
        if not paragraph:
            total_lines += 1
            continue
        line_w = 0.0
        lines = 1
        for ch in paragraph:
            w = _char_width(ch, font_size)
            if line_w + w > box_width:
                widest = max(widest, line_w)
                lines += 1
                line_w = w
            else:
                line_w += w
        widest = max(widest, line_w)
        total_lines += lines

    # 行距按 1.25 估算（build_deck.write 设置的就是 1.25）
    needed = total_lines * font_size * 1.25

    return {
        "lines": total_lines,
        "needed_height": round(needed, 1),
        "widest_line": round(widest, 1),
        "box_width": round(box_width, 1),
        "box_height": round(box_height, 1),
        "capacity": round(box_height / (font_size * 1.25), 1) if font_size else 0,
    }


def _read_deck_sections(prs, file_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取 build_deck.py 写入的「页 ↔ 章节」元数据。

    三处来源，按可靠性排序：
      1. 内存属性 ``_deck_sections``（同进程直接传 Presentation 时）
      2. sidecar ``<deck>.deck.json``（完整元数据，含关键内容/so_what）
      3. ``core_properties.comments``（紧凑摘要，只有章节序列）
    """
    # 1) 内存属性（同进程）
    sections = getattr(prs, "_deck_sections", None)
    if sections:
        return list(sections)

    # 2) sidecar 完整元数据
    if file_path:
        meta_path = os.path.splitext(file_path)[0] + ".deck.json"
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and payload.get("sections"):
                    return list(payload["sections"])
            except (json.JSONDecodeError, OSError):
                pass

    # 3) core_properties.comments 紧凑摘要
    try:
        raw = prs.core_properties.comments
    except Exception:
        return []
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    if isinstance(payload, dict):
        if payload.get("sections"):
            return list(payload["sections"])
        # 紧凑格式 {"v":1,"pages":11,"secs":["1:封面", ...]}
        secs = payload.get("secs") or []
        restored: List[Dict[str, Any]] = []
        for entry in secs:
            text = str(entry)
            if ":" not in text:
                continue
            page_no, _, section = text.partition(":")
            if page_no.strip().isdigit():
                restored.append({"页码": int(page_no), "章节": section})
        return restored

    return list(payload) if isinstance(payload, list) else []


def _iter_text_shapes(slide):
    """遍历一页里的所有文本框（含表格单元格）。"""
    for shape in slide.shapes:
        if shape.shape_type is not None and str(shape.shape_type).startswith("GROUP"):
            try:
                for sub in shape.shapes:
                    yield sub
            except Exception:
                continue
        if shape.has_text_frame:
            yield shape
        # 表格
        if getattr(shape, "has_table", False) and shape.has_table:
            yield shape


def _shape_text(shape) -> str:
    if getattr(shape, "has_table", False) and shape.has_table:
        parts = []
        for row in shape.table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text.strip())
        return "\n".join(parts)
    if shape.has_text_frame:
        return shape.text_frame.text
    return ""


def _dominant_font_size(shape) -> Optional[float]:
    """取形状里最大的字号（磅）；取不到返回 None。"""
    sizes: List[float] = []
    try:
        tf = shape.text_frame
    except Exception:
        return None
    for paragraph in tf.paragraphs:
        for run in paragraph.runs:
            if run.font.size is not None:
                sizes.append(run.font.size.pt)
        if paragraph.font.size is not None:
            sizes.append(paragraph.font.size.pt)
    if not sizes:
        return None
    return max(sizes)


def _emphasize_size(shape) -> Optional[float]:
    """取形状里最大的字号（磅）；取不到返回 None。"""
    return _dominant_font_size(shape)


def _shape_rgb(shape) -> Optional[str]:
    """尽力取出形状的文字颜色（用于色彩语义检查）。"""
    try:
        tf = shape.text_frame
    except Exception:
        return None
    for paragraph in tf.paragraphs:
        for run in paragraph.runs:
            color = run.font.color
            if color is not None and color.type is not None:
                try:
                    return str(color.rgb).upper()
                except Exception:
                    continue
    return None


def check_pptx(
    file_path: str,
    expected_count: Optional[int] = None,
    must_contain: Optional[Iterable[str]] = None,
    out: Optional[str] = None,
    require_so_what: bool = True,
    allowed_colors: Optional[Iterable[str]] = None,
    sowhat_exempt: Sequence[str] = ("cover", "agenda", "appendix", "封面", "路线图", "附录"),
) -> Dict[str, Any]:
    """校验一个 .pptx，返回 checks 报告字典。"""
    checks: List[Dict[str, Any]] = []
    started = datetime.now().isoformat(timespec="seconds")

    def add(check_id: str, status: str, name: str = "", **extra: Any) -> None:
        entry: Dict[str, Any] = {"id": check_id, "status": status}
        if name:
            entry["name"] = name
        entry.update(extra)
        checks.append(entry)

    # ---------- 1. 文件与包结构 ----------
    if not os.path.isfile(file_path):
        add("package", FAIL, "文件存在", error=f"文件不存在: {file_path}")
        return _finalize(file_path, checks, out, started)

    add("package", PASS, "文件存在",
        size_kb=round(os.path.getsize(file_path) / 1024, 1))

    if not zipfile.is_zipfile(file_path):
        add("ooxml", FAIL, "OOXML 包结构", error="不是合法的 zip/OOXML 文件")
        return _finalize(file_path, checks, out, started)
    add("ooxml", PASS, "OOXML 包结构")

    # ---------- 2. 打开 ----------
    try:
        prs = Presentation(file_path)
    except Exception as exc:
        add("open", FAIL, "python-pptx 可打开", error=f"{type(exc).__name__}: {exc}")
        return _finalize(file_path, checks, out, started)

    slides = list(prs.slides)
    slide_count = len(slides)
    add("open", PASS, "python-pptx 可打开", slides=slide_count)

    # 16:9
    ratio = prs.slide_width / prs.slide_height if prs.slide_height else 0
    if abs(ratio - 16 / 9) < 0.05:
        add("aspect", PASS, "16:9 比例", ratio=round(ratio, 3))
    else:
        add("aspect", WARN, "16:9 比例", ratio=round(ratio, 3),
            detail="非 16:9，需确认是否符合要求")

    # ---------- 3. 页数 ----------
    if expected_count is not None:
        if slide_count == expected_count:
            add("count", PASS, "页数符合预期", expected=expected_count, actual=slide_count)
        else:
            add("count", FAIL, "页数符合预期", expected=expected_count, actual=slide_count)

    # ---------- 4. 关键内容 ----------
    per_slide_text: List[str] = []
    for slide in slides:
        texts = [_shape_text(sh) for sh in _iter_text_shapes(slide)]
        per_slide_text.append("\n".join(t for t in texts if t))
    all_text = "\n".join(per_slide_text)

    for keyword in must_contain or []:
        if keyword in all_text:
            add("contains", PASS, f"包含「{keyword}」", text=keyword)
        else:
            add("contains", FAIL, f"包含「{keyword}」", text=keyword)

    # ---------- 4b. 按「页 ↔ 章节」逐页核对关键内容 ----------
    deck_sections = _read_deck_sections(prs, file_path)
    if deck_sections:
        per_page_issues: List[Dict[str, Any]] = []
        for item in deck_sections:
            page_no = int(item.get("页码", 0))
            section = str(item.get("章节", ""))
            keywords = [str(k) for k in (item.get("关键内容") or [])]
            span = int(item.get("幻灯片数", 1) or 1)
            if not keywords or page_no < 1:
                continue
            # 统计该章节覆盖的页面文本
            covered = "\n".join(
                per_slide_text[i - 1]
                for i in range(page_no, min(page_no + span, slide_count + 1))
            )
            if not any(k in covered for k in keywords):
                per_page_issues.append({
                    "页码": page_no,
                    "章节": section,
                    "期望关键词": keywords,
                })
        if per_page_issues:
            add("section_content", WARN, "各页含对应章节关键内容",
                issues=per_page_issues)
        else:
            add("section_content", PASS, "各页含对应章节关键内容",
                sections=len(deck_sections))
    else:
        add("section_content", WARN, "各页含对应章节关键内容",
            detail="未找到页/章节元数据（该 pptx 可能不是 build_deck.py 生成的）")

    # ---------- 5. 每页结论句 so_what ----------
    if require_so_what:
        # 豁免规则**按 layout 判定**，不按业务章节名。
        # 早先用中文章节名（"封面"/"路线图"/"附录"）豁免 —— 那是物业专用知识，
        # 而通用渲染器写入的是 layout 名（cover/agenda/appendix），导致
        # 这些页被误判为"缺少结论句"。
        section_of: Dict[int, str] = {}
        for item in deck_sections:
            page = int(item.get("页码", item.get("page", 0)) or 0)
            section_of[page] = str(
                item.get("layout") or item.get("章节") or ""
            ).strip().lower()

        exempt = {s.lower() for s in sowhat_exempt}
        silent_pages: List[int] = []
        checked_pages = 0
        for idx, slide in enumerate(slides, 1):
            section = section_of.get(idx, "")
            if section in exempt:
                continue
            checked_pages += 1
            bottom_texts = []
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                top = getattr(shape, "top", None)
                if top is None:
                    continue
                if top >= prs.slide_height * 0.72:
                    txt = shape.text_frame.text.strip()
                    if txt:
                        bottom_texts.append(txt)
            if not any(len(t) >= 8 for t in bottom_texts):
                silent_pages.append(idx)

        if silent_pages:
            add("so_what", WARN, "每页都有结论句",
                pages=len(silent_pages),
                detail=f"这些页底部未见结论句: {silent_pages}")
        else:
            add("so_what", PASS, "每页都有结论句", pages=checked_pages)

    # ---------- 6. 文字溢出估算 ----------
    overflow_findings: List[Dict[str, Any]] = []
    for idx, slide in enumerate(slides, 1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text
            if not text.strip():
                continue
            size = _dominant_font_size(shape)
            if size is None:
                size = 12.0
            try:
                box_w = shape.width.pt
                box_h = shape.height.pt
            except Exception:
                continue
            if box_w <= 0 or box_h <= 0:
                continue

            est = _estimate_text_block(text, size, box_w, box_h)
            # 宽溢出：最长行超过盒宽
            width_over = est["widest_line"] > est["box_width"] * 1.02
            # 高溢出：所需高度超过盒高
            height_over = est["needed_height"] > est["box_height"] * 1.05
            # 行数容量
            lines_over = est["capacity"] and est["lines"] > est["capacity"] + 0.5

            if width_over or height_over or lines_over:
                overflow_findings.append({
                    "slide": idx,
                    "text": text.strip().replace("\n", " / ")[:60],
                    "font_pt": round(size, 1),
                    "box": f"{est['box_width']}x{est['box_height']}pt",
                    "widest_line_pt": est["widest_line"],
                    "needed_height_pt": est["needed_height"],
                    "lines": est["lines"],
                    "capacity_lines": est["capacity"],
                    "reasons": [r for r, flag in (
                        ("宽度超出", width_over),
                        ("高度超出", height_over),
                        ("行数超容量", lines_over),
                    ) if flag],
                })

    if overflow_findings:
        # 估算有误差，报 warn 而不是 fail
        add("overflow", WARN, "文字未溢出（估算）",
            findings=len(overflow_findings),
            detail=overflow_findings[:10])
    else:
        add("overflow", PASS, "文字未溢出（估算）")

    # ---------- 7. 色彩语义一致性 ----------
    # 允许色来自**传入的主题**（知识在技能里），不再硬编码物业配色。
    # 传入 allowed_colors 时严格按它校验；不传则跳过该检查，
    # 因为不知道用什么主题就没法判断"颜色是否越界"。
    if allowed_colors:
        allowed = {
            str(c).lstrip("#").upper()
            for c in allowed_colors
            if str(c).strip()
        }
    else:
        allowed = {
            str(c).lstrip("#").upper() for c in ALLOWED_THEME_COLORS
        }

    used_colors: Dict[str, int] = {}
    for slide in slides:
        for shape in slide.shapes:
            rgb = _shape_rgb(shape)
            if rgb:
                used_colors[rgb] = used_colors.get(rgb, 0) + 1
    unknown = {c: n for c, n in used_colors.items() if c not in allowed}
    if unknown:
        add("color_semantics", WARN, "色彩语义一致",
            unknown=unknown, allowed=sorted(allowed))
    else:
        add("color_semantics", PASS, "色彩语义一致",
            used=sorted(used_colors))

    # ---------- 8. 图表数据对齐 ----------
    chart_issues: List[Dict[str, Any]] = []
    chart_total = 0
    for idx, slide in enumerate(slides, 1):
        for shape in slide.shapes:
            if not getattr(shape, "has_chart", False) or not shape.has_chart:
                continue
            chart_total += 1
            chart = shape.chart
            try:
                plot = chart.plots[0]
                categories = list(plot.categories)
                series = list(chart.series)
                n_points = [len(s.values) for s in series]

                if len(series) == 0:
                    chart_issues.append({"slide": idx, "issue": "无数据系列"})
                    continue
                if any(n != len(categories) for n in n_points):
                    chart_issues.append({
                        "slide": idx,
                        "issue": "系列点数与分类数不一致",
                        "categories": len(categories),
                        "series_points": n_points,
                    })
                if any(v is None for s in series for v in s.values):
                    chart_issues.append({"slide": idx, "issue": "存在空数据点"})
            except Exception as exc:
                chart_issues.append({
                    "slide": idx,
                    "issue": f"读取图表失败: {type(exc).__name__}",
                })

    if chart_issues:
        add("chart_data", WARN, "图表数据对齐",
            charts=chart_total, issues=chart_issues[:10])
    else:
        add("chart_data", PASS, "图表数据对齐", charts=chart_total)

    # ---------- 9. 空页检查 ----------
    empty_pages = [i for i, t in enumerate(per_slide_text, 1) if not t.strip()]
    if empty_pages:
        add("no_empty_slide", FAIL, "无空白页", pages=empty_pages)
    else:
        add("no_empty_slide", PASS, "无空白页")

    return _finalize(file_path, checks, out, started)


def _finalize(
    file_path: str,
    checks: List[Dict[str, Any]],
    out: Optional[str],
    started: str,
) -> Dict[str, Any]:
    """汇总判定，并按需写出 checks.json。"""
    statuses = [c["status"] for c in checks]
    if FAIL in statuses:
        verdict = FAIL
    elif WARN in statuses:
        verdict = WARN
    else:
        verdict = PASS

    report: Dict[str, Any] = {
        "format": "pptx",
        "file": file_path,
        "verdict": verdict,
        "checked_at": started,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "pass": statuses.count(PASS),
            "warn": statuses.count(WARN),
            "fail": statuses.count(FAIL),
        },
    }

    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        report["report_path"] = out

    return report


def format_report(report: Dict[str, Any]) -> str:
    """把报告渲染成便于 Agent 阅读/贴进正文的文本。"""
    s = report.get("summary", {})
    lines = [
        f"[PPTX 校验] 判定: {report.get('verdict', '?').upper()}  "
        f"(pass={s.get('pass',0)} warn={s.get('warn',0)} fail={s.get('fail',0)})",
        f"文件: {report.get('file', '')}",
    ]
    if report.get("report_path"):
        lines.append(f"报告: {report['report_path']}")

    for check in report.get("checks", []):
        status = check.get("status", "?")
        if status == PASS:
            continue  # 只报有问题的项，保持简短
        icon = "WARN" if status == WARN else "FAIL"
        lines.append(f"  [{icon}] {check.get('id')}: {check.get('name', '')}")
        for key in ("error", "detail", "pages", "expected", "actual", "unknown"):
            if key in check:
                value = check[key]
                text = json.dumps(value, ensure_ascii=False)
                if len(text) > 400:
                    text = text[:400] + "…"
                lines.append(f"        {key}: {text}")

    if report.get("verdict") == PASS:
        lines.append("  全部检查通过 ✅")
    return "\n".join(lines)


# ===================================================================
# Tool 封装：注册进 CapabilityRouter 即可被 Agent 调用
# ===================================================================

class PptxCheckTool(Tool):
    """PPTX 结构校验工具（能力名 ``pptx_check``）。"""

    def __init__(self, expandable: bool = False):
        super().__init__(
            name="pptx_check",
            description=(
                "校验 .pptx 演示文稿的结构完整性：页数、关键内容是否存在、"
                "文字是否溢出（估算）、色彩语义是否一致、图表数据是否与源一致，"
                "并输出 checks.json 校验报告。用于 PPT 交付前的自检。"
            ),
            expandable=expandable,
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="file_path",
                type="string",
                description="要校验的 .pptx 文件路径",
                required=True,
            ),
            ToolParameter(
                name="expected_count",
                type="integer",
                description="预期页数；提供则校验页数是否一致",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="must_contain",
                type="string",
                description="必须出现的关键词，多个用英文逗号分隔，例如 "
                            "'异常科目归因,当期收缴率,行动项'",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="out",
                type="string",
                description="checks.json 输出路径；提供则落盘校验报告",
                required=False,
                default=None,
            ),
        ]

    def run(self, params: Dict[str, Any]) -> str:
        file_path = params.get("file_path") or params.get("path") or ""
        if not file_path:
            return "校验失败：缺少 file_path 参数"

        expected = params.get("expected_count")
        if isinstance(expected, str):
            expected = int(expected) if expected.strip().isdigit() else None

        raw_keywords = params.get("must_contain")
        if not raw_keywords:
            keywords: List[str] = []
        elif isinstance(raw_keywords, (list, tuple)):
            keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
        else:
            keywords = [k.strip() for k in str(raw_keywords).split(",") if k.strip()]

        out = params.get("out")

        report = check_pptx(
            file_path=file_path,
            expected_count=expected,
            must_contain=keywords,
            out=out,
        )
        return format_report(report)
