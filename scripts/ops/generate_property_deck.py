# -*- coding: utf-8 -*-
"""用数据驱动 PPT 技能生成物业经营分析演示文稿。

从 data/common 自动取数，生成物业经营分析演示文稿。

用法：
    .venv\\Scripts\\python.exe scripts\\ops\\generate_property_deck.py
    .venv\\Scripts\\python.exe scripts\\ops\\generate_property_deck.py --project 鸣X园
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.loader import Skill  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成数据驱动的物业经营分析 PPT")
    parser.add_argument(
        "--skill-dir",
        default=str(ROOT / "skills" / "property" / "ppt_skill"),
        help="技能目录",
    )
    parser.add_argument("--project", default="鸣X园", help="封面项目名")
    parser.add_argument("--period", default="", help="期间；留空则使用数据实际覆盖范围")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs" / "skills" / "property_deck"),
        help="产物根目录",
    )
    parser.add_argument(
        "--request",
        default="用应收信息生成物业项目经营分析 PPT，覆盖数据实际期间",
        help="完整需求文本",
    )
    args = parser.parse_args()

    # 降低 BLAS 线程数，避免小内存环境取数时 OpenBLAS 申请失败。
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, "1")

    skill_path = Path(args.skill_dir)
    if not skill_path.is_absolute():
        skill_path = ROOT / skill_path
    output_path = Path(args.output_dir)
    if not output_path.is_absolute():
        output_path = ROOT / output_path

    skill = Skill(
        skill_dir=str(skill_path),
        skill_id="ppt_skill",
        llm=None,
    )
    payload = {
        "project": args.project,
        "data_dir": str(ROOT / "data" / "common"),
    }
    if args.period:
        payload["period"] = args.period
    params = {
        "request": args.request,
        "data": payload,
        "output_dir": str(output_path),
    }

    print(f"[生成] skill={skill.skill_dir}")
    print(f"[生成] mode={skill.effective_mode()}  script={skill.script}")
    result = skill.run_skill(params)
    print(result.to_text())
    print(f"SUCCESS={result.success}  ERROR={result.error or '-'}")
    run_dir = (result.meta or {}).get("run_dir")
    if run_dir:
        print(f"RUN_DIR={run_dir}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
