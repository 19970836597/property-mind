# -*- coding: utf-8 -*-
"""new-ppt-skill 编排器 —— 技能通过调用工具完成整个流程

这个文件是**薄编排层**（约 200 行），不含任何渲染或专业知识。
所有知识在 ``../deck_spec.json``，所有机制在 ``tool/common/`` 的工具里。

    ┌─ 本文件（编排）──────────────────────────────────────┐
    │  读 deck_spec.json（技能知识）                        │
    │  ① data_analysis  → 读懂输入数据                     │
    │  ② deck_content   → 数据 + spec → 演示文稿 IR         │
    │  ③ deck_build     → IR + theme → .pptx               │
    │  ④ deck_speech    → IR → 逐页话术                     │
    │  ⑤ pptx_check     → 结构校验 → checks.json            │
    └──────────────────────────────────────────────────────┘

运行（通常由 skill loader 以子进程方式调用）：
    python scripts/run_skill.py
环境变量（由 loader 注入）：
    SKILL_INPUT_DATA   输入 JSON（含 _auto_file 或 data）
    SKILL_RUN_DIR      本次运行的产物目录
    SKILL_OUTPUT_PATH  .pptx 输出路径
    SKILL_SPEECH_PATH  话术 JSON 输出路径
    SKILL_CHECKS_PATH  checks.json 输出路径
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------
# 找到项目根（向上找 tool/common）
# ---------------------------------------------------------------------
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_project_root(start: str) -> str:
    current = start
    for _ in range(8):
        if os.path.isdir(os.path.join(current, "tool", "common")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise RuntimeError(f"找不到项目根（从 {start} 向上未发现 tool/common）")


PROJECT_ROOT = _find_project_root(SKILL_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tool.common.deck_build_tool import DeckBuildTool  # noqa: E402
from tool.common.deck_content_tool import DeckContentTool  # noqa: E402
from tool.common.deck_speech import build_speech, write_speech_files  # noqa: E402
from tool.common.deck_theme import build_theme, validate_theme  # noqa: E402
from tool.common.pptx_check_tool import check_pptx  # noqa: E402

SPEC_PATH = os.path.join(SKILL_DIR, "deck_spec.json")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK，告警里的 ✗/⚠ 会让脚本二次崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_spec() -> Dict[str, Any]:
    """① 读技能知识。"""
    if not os.path.isfile(SPEC_PATH):
        raise RuntimeError(f"技能知识文件缺失: {SPEC_PATH}")
    with open(SPEC_PATH, "r", encoding="utf-8-sig") as handle:
        spec = json.load(handle)
    print(f"[编排] 已载入技能知识: {os.path.basename(SPEC_PATH)}"
          f"（{spec.get('name')}）")
    return spec


def load_input() -> Dict[str, Any]:
    """读 loader 注入的输入。"""
    path = _env("SKILL_INPUT_DATA")
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        print(f"[编排] 已载入输入数据: {path}")
        return payload if isinstance(payload, dict) else {"data": payload}
    print("[编排] 未收到 input_data（将尝试用默认数据）")
    return {}


def resolve_source_file(payload: Dict[str, Any]) -> Optional[str]:
    """决定用哪个数据文件。

    loader 自动取数时写 ``_auto_file``；显式传参时写 ``file`` / ``data_path``。
    """
    for key in ("_auto_file", "file", "data_path", "source_file"):
        value = payload.get(key)
        if isinstance(value, str) and os.path.isfile(value):
            return value
    return None


def main() -> int:
    _force_utf8_stdio()
    print("=" * 68)
    print("new-ppt-skill 五阶段流水线（工具编排版）")
    print("=" * 68)

    spec = load_spec()
    payload = load_input()

    run_dir = _env("SKILL_RUN_DIR") or os.path.dirname(
        _env("SKILL_OUTPUT_PATH") or os.getcwd())
    os.makedirs(run_dir, exist_ok=True)

    source = resolve_source_file(payload)
    if source:
        print(f"[编排] 数据文件: {source}")

    # ---------------------------------------------------------------
    # ② 数据 + 知识 → 演示文稿 IR
    # ---------------------------------------------------------------
    ir_path = os.path.join(run_dir, "deck.json")
    content_params: Dict[str, Any] = {
        "action": "build",
        "spec": spec,                      # ← 知识来自技能
        "out": ir_path,
        "extra_facts": payload.get("extra_facts") or {},
    }
    if source:
        content_params["file"] = source
    for key in ("project", "period", "group_column", "value_column",
                "date_column", "overrides", "extra_facts"):
        if payload.get(key) is not None:
            content_params[key] = payload[key]

    print()
    print(DeckContentTool().run(content_params))

    if not os.path.isfile(ir_path):
        print("[编排] ✗ 未生成 IR，终止")
        return 1

    with open(ir_path, "r", encoding="utf-8") as handle:
        deck = json.load(handle)

    # ---------------------------------------------------------------
    # ③ IR + theme → .pptx
    # ---------------------------------------------------------------
    pptx_path = _env("SKILL_OUTPUT_PATH") or os.path.join(run_dir, "deck.pptx")

    # 主题优先级：spec.theme > IR.meta.theme_spec > 默认
    theme = build_theme(theme=spec.get("theme"))
    theme_problems = validate_theme(theme)
    if theme_problems:
        print(f"[编排] ⚠ 技能主题校验 {len(theme_problems)} 条:")
        for problem in theme_problems[:5]:
            print(f"    - {problem}")

    print()
    print(DeckBuildTool().run({
        "deck": deck, "output": pptx_path, "theme": theme,
    }))

    # ---------------------------------------------------------------
    # ④ IR → 逐页话术（交付物 ③）
    # ---------------------------------------------------------------
    speech_path = _env("SKILL_SPEECH_PATH")
    # loader 给的是完整路径（deck.speech.json）；write_speech_files 需要**基础名**。
    # 不处理会拼出 deck.speech.speech.json。
    if speech_path:
        base = speech_path
        for suffix in (".speech.json", ".speech.md", ".json", ".md"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
                break
    else:
        base = os.path.join(run_dir, "deck")
    speech = build_speech(deck)
    written = write_speech_files(speech, base)
    print()
    print(f"[编排] 逐页话术已生成: {written['json']}")
    print(f"  话术段数: {len(speech.get('scripts') or {})} 段")

    # ---------------------------------------------------------------
    # ⑤ 内容健康度门禁：结构 PASS ≠ 内容有意义
    # ---------------------------------------------------------------
    # 实测踩过：曾生成一份 11 页里 7 页写着"数据缺失，待补"的 PPT，
    # 而结构校验（页数/结论句/颜色）全部 PASS —— 因为"待补数据后生成"
    # 也是一句结论。只看结构会把空壳判成合格交付物。
    health = (deck.get("meta") or {}).get("content_health") or {}
    pages = int(health.get("pages") or 0)
    degraded = int(health.get("degraded_pages") or 0)
    content_problems: List[str] = []

    if health.get("derived_expected") and not health.get("derived_ok"):
        content_problems.append(
            "技能声明的派生指标**全部失败** —— 结论句里的关键指标算不出来"
            "（通常是数据文件不对，或列名与 spec 不符）")
    if pages and degraded:
        ratio = degraded / pages
        if ratio >= 0.5:
            content_problems.append(
                f"{degraded}/{pages} 页因**数据缺失**降级（{ratio:.0%}）—— "
                f"这份产物基本是空壳，不能当正式汇报交付")
        elif ratio > 0:
            content_problems.append(
                f"{degraded}/{pages} 页因数据缺失降级，交付前请人工确认")

    if content_problems:
        print()
        print("!" * 68)
        print("⚠ 内容健康度告警（结构校验无法发现这类问题）")
        for problem in content_problems:
            print(f"  · {problem}")
        print("!" * 68)

    # ---------------------------------------------------------------
    # ⑤ 结构校验 → checks.json（交付物 ③）
    # ---------------------------------------------------------------
    checks_path = _env("SKILL_CHECKS_PATH") or os.path.join(run_dir, "checks.json")
    check_cfg = spec.get("check") or {}
    allowed = (set(theme.get("colors", {}).values())
               | set(theme.get("palette", [])) | {"FFFFFF"})

    report = check_pptx(
        pptx_path,
        expected_count=len(deck.get("slides") or []),
        must_contain=check_cfg.get("must_contain"),
        out=checks_path,
        allowed_colors=allowed,
    )
    print()
    print(f"[编排] 结构校验: {report['verdict'].upper()} "
          f"(pass={report['summary']['pass']} "
          f"warn={report['summary']['warn']} "
          f"fail={report['summary']['fail']})")
    print(f"  报告: {checks_path}")

    # ---------------------------------------------------------------
    # 元数据 sidecar（供 loader 的告警判断与人工追溯）
    # ---------------------------------------------------------------
    meta = deck.get("meta") or {}
    meta_path = os.path.splitext(pptx_path)[0] + ".deck.json"
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump({
            "项目": meta.get("project", ""),
            "期间": meta.get("period", ""),
            "页数": len(deck.get("slides") or []),
            "profile": meta.get("profile", ""),
            "sections": [
                {"页码": i, "layout": s.get("layout"),
                 "章节": s.get("kicker"), "标题": s.get("title")}
                for i, s in enumerate(deck.get("slides") or [], start=1)
            ],
        }, handle, ensure_ascii=False, indent=2)

    print()
    print(f"完成！交付物:")
    print(f"  ① PPTX      {pptx_path}")
    print(f"  ② 校验报告  {checks_path}")
    print(f"  ③ 汇报话术  {written['md']}")

    if report["summary"]["fail"]:
        return 2
    # 内容空壳也视为失败 —— 让调用方知道这份产物不可交付。
    # 两种情况：
    #   ① 声明的派生指标全失败 → 关键指标算不出来，结论句没有依据
    #   ② 过半页面因数据缺失降级 → 基本是空壳
    if health.get("derived_expected") and not health.get("derived_ok"):
        return 3
    if content_problems and degraded / max(pages, 1) >= 0.5:
        return 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 让失败原因明确可见
        print(f"[编排] ✗ 执行失败: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise SystemExit(1)
