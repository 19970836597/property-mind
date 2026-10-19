# -*- coding: utf-8 -*-
"""new-ppt-skill 入口。

数据 → deck IR → PPTX → checks → speech 的编排逻辑全部放在本目录的
``deck_pipeline.py``，技能不再依赖任何旧版目录。
"""

from __future__ import annotations

import json
import os
import re
import sys

# 小内存环境下 pandas/OpenBLAS 可能因默认线程数申请失败。
for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_key, "1")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


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
SPEC_PATH = os.path.join(SKILL_DIR, "deck_spec.json")
DEFAULT_DATA_FILE = os.path.join(
    PROJECT_ROOT, "data", "common", "2、应收信息.xlsx"
)

_PROJECT_RE = re.compile(
    r"([一-龥A-Za-z0-9]{2,12}(?:小区|花园|家园|公寓|广场|大厦|社区|项目|园|苑))"
)
_PROJECT_PREFIX_RE = re.compile(
    r"^(?:帮我|请|生成|制作|做|用|为|关于|一份|一个)+"
)


def _project_from_request(request: str) -> str:
    match = _PROJECT_RE.search(request or "")
    if not match:
        return "物业项目"
    project = _PROJECT_PREFIX_RE.sub("", match.group(1)).strip()
    return project or "物业项目"


def _ensure_default_input() -> str | None:
    """没收到数据线索时，默认使用项目的应收主表。

    skill loader 的自动取数会用整句 request 去匹配文件名；像“生成一份
    经营分析 PPT”这种请求没有文件名线索，会匹配不到。这里做一次兜底，
    让 new-ppt-skill 的直接调用也能走真实数据，而不是空模板。
    """
    if os.environ.get("SKILL_INPUT_DATA"):
        return None
    # loader 显式关闭自动取数时（测试/调用方要求“只用我传的数据”），
    # 不要再用项目默认表兜底，否则“无数据”告警永远触发不了。
    if os.environ.get("SKILL_AUTO_RESOLVE_DATA") == "0":
        return None
    if os.environ.get("NEW_PPT_ALLOW_EMPTY", "").lower() in ("1", "true", "yes"):
        return None
    if not os.path.isfile(DEFAULT_DATA_FILE):
        print(f"[new-ppt-skill] 默认数据文件不存在: {DEFAULT_DATA_FILE}")
        return None

    run_dir = (
        os.environ.get("SKILL_RUN_DIR")
        or os.path.dirname(os.environ.get("SKILL_OUTPUT_PATH", ""))
        or os.getcwd()
    )
    os.makedirs(run_dir, exist_ok=True)
    target = os.path.join(run_dir, "input_data.json")
    payload = {
        "_auto_file": DEFAULT_DATA_FILE,
        "_auto_resolved": True,
        "_source_name": os.path.basename(DEFAULT_DATA_FILE),
        "project": _project_from_request(os.environ.get("SKILL_REQUEST", "")),
        "extra_facts": {},
    }
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.environ["SKILL_INPUT_DATA"] = target
    print(
        "[new-ppt-skill] 未收到数据文件线索，"
        f"默认使用 {DEFAULT_DATA_FILE}"
    )
    return target


def main() -> int:
    # Windows 控制台默认 GBK，失败提示里的 ✗ 会让“报错”本身再抛异常。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if not os.path.isfile(SPEC_PATH):
        raise RuntimeError(f"new-ppt-skill 的 deck_spec.json 不存在: {SPEC_PATH}")
    _ensure_default_input()
    import deck_pipeline

    print(f"[new-ppt-skill] 使用技能知识: {SPEC_PATH}")
    return int(deck_pipeline.main())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 让失败原因明确可见
        print(f"[new-ppt-skill] ✗ 执行失败: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
