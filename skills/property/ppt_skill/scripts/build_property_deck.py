# -*- coding: utf-8 -*-
"""Fixed PropertyMind deck pipeline backed by the bundled ppt-master runtime.

Environment contract (injected by skills/loader.py):
    SKILL_REQUEST       natural-language request
    SKILL_INPUT_DATA    optional JSON payload with project/period/data_dir
    SKILL_RUN_DIR       run artifact directory
    SKILL_OUTPUT_PATH   target .pptx path
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from xml.sax.saxutils import unescape

from property_metrics import (
    DEFAULT_FILES,
    DEFAULT_PERIOD,
    DEFAULT_PROJECT,
    DataValidationError,
    compute_metrics,
)
from svg_render import estimate_width, render_deck, render_notes


SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR
for _ in range(8):
    if (PROJECT_ROOT / "tools" / "common").is_dir():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
else:
    raise RuntimeError(f"找不到项目根目录（从 {SKILL_DIR} 向上未发现 tools/common）")

RUNTIME_SCRIPTS = SKILL_DIR / "scripts"


class LayoutError(RuntimeError):
    """Raised when a generated text line would overflow its layout budget."""


_TEXT_BUDGET_RE = re.compile(
    r"<!--\s*text-budget:\s*([0-9.]+)\s*-->\s*<text\b[^>]*>(.*?)</text>",
    re.DOTALL,
)
_FONT_SIZE_RE = re.compile(r'font-size="([0-9.]+)"')


def preflight_text(svg_dir: Path) -> None:
    """Fail before export when a measured line exceeds its declared width."""
    for svg_path in sorted(svg_dir.glob("*.svg")):
        content = svg_path.read_text(encoding="utf-8")
        for match in _TEXT_BUDGET_RE.finditer(content):
            max_width = float(match.group(1))
            inner = re.sub(r"<tspan\b[^>]*>", "\n", match.group(2))
            inner = inner.replace("</tspan>", "\n")
            inner = re.sub(r"<[^>]+>", "", inner)
            lines = [unescape(line).strip() for line in inner.splitlines() if line.strip()]
            font_match = _FONT_SIZE_RE.search(match.group(0))
            font_size = int(float(font_match.group(1))) if font_match else 24
            for line in lines:
                width = estimate_width(line, font_size)
                if width > max_width * 0.9 + 0.5:
                    raise LayoutError(
                        f"{svg_path.name}: 文本约 {width:.0f}px，预算 {max_width:.0f}px：{line!r}"
                    )


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def _load_payload() -> dict:
    path = _env("SKILL_INPUT_DATA")
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _run(cmd: list[str], cwd: Path, env: dict[str, str], label: str) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} 失败（exit {completed.returncode}）\n"
            f"stdout:\n{completed.stdout[-3000:]}\n"
            f"stderr:\n{completed.stderr[-3000:]}"
        )
    return completed


def _script(name: str) -> str:
    return str(RUNTIME_SCRIPTS / name)


def _prepare_project(project: Path, metrics) -> None:
    (project / "svg_output").mkdir(parents=True, exist_ok=True)
    (project / "notes").mkdir(parents=True, exist_ok=True)
    (project / "validation").mkdir(parents=True, exist_ok=True)
    (project / "exports").mkdir(parents=True, exist_ok=True)
    (project / "icons" / "tabler-outline").mkdir(parents=True, exist_ok=True)

    for source in (SKILL_DIR / "assets" / "icons" / "tabler-outline").glob("*.svg"):
        shutil.copy2(source, project / "icons" / "tabler-outline" / source.name)

    for name in ("design_spec.md", "spec_lock.md"):
        text = (SKILL_DIR / "assets" / name).read_text(encoding="utf-8")
        text = text.replace("鸣X园", metrics.project_name).replace("2026年1-5月", metrics.period)
        (project / name).write_text(text, encoding="utf-8")

    render_deck(metrics, project / "svg_output")
    preflight_text(project / "svg_output")
    (project / "notes" / "total.md").write_text(render_notes(metrics), encoding="utf-8")


def _run_runtime(project: Path, env: dict[str, str]) -> None:
    _run(
        [sys.executable, _script("svg_quality_checker.py"), str(project),
         "--canonical-authoring", "--stage", "final", "--json"],
        cwd=project,
        env=env,
        label="最终 SVG 质检",
    )
    _run(
        [sys.executable, _script("total_md_split.py"), str(project)],
        cwd=project,
        env=env,
        label="拆分演讲备注",
    )
    _run(
        [sys.executable, _script("finalize_svg.py"), str(project)],
        cwd=project,
        env=env,
        label="生成 SVG 预览",
    )
    _run(
        [sys.executable, _script("svg_to_pptx.py"), str(project)],
        cwd=project,
        env=env,
        label="导出 PPTX",
    )


def _find_export(project: Path) -> Path:
    files = sorted((project / "exports").glob("*.pptx"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise RuntimeError(f"未找到导出的 PPTX：{project / 'exports'}")
    return files[-1]


def main() -> int:
    request = _env("SKILL_REQUEST")
    run_dir = Path(_env("SKILL_RUN_DIR") or (PROJECT_ROOT / "outputs" / "skills" / f"ppt_skill_{time.strftime('%Y%m%d_%H%M%S')}"))
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(_env("SKILL_OUTPUT_PATH") or (run_dir / "deck.pptx"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _load_payload()
    project_name = str(payload.get("project") or "").strip() or None
    period = str(payload.get("period") or "").strip() or None
    data_dir = Path(str(payload.get("data_dir") or (PROJECT_ROOT / "data" / "common")))

    try:
        metrics = compute_metrics(
            data_dir=data_dir,
            project_name=project_name,
            period=period or DEFAULT_PERIOD,
            request=request,
        )
    except DataValidationError as exc:
        print(f"[ppt_skill] 数据校验失败：{exc}", file=sys.stderr)
        return 2
    if not metrics.project_name:
        metrics.project_name = DEFAULT_PROJECT

    # Keep the loader's data-presence check quiet and leave a provenance record.
    (run_dir / "input_data.json").write_text(
        json.dumps(
            {
                "project": metrics.project_name,
                "period": metrics.period,
                "files": {key: str(data_dir / name) for key, name in DEFAULT_FILES.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "deck.deck.json").write_text(
        json.dumps({"项目": metrics.project_name}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temp_project = Path(tempfile.mkdtemp(prefix="ppt_skill_"))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(RUNTIME_SCRIPTS), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        _prepare_project(temp_project, metrics)
        _run_runtime(temp_project, env)
        exported = _find_export(temp_project)
        shutil.copy2(exported, output_path)
        speech_path = run_dir / "deck.speech.md"
        speech_path.write_text(
            (temp_project / "notes" / "total.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        summary = {
            "ok": True,
            "project": metrics.project_name,
            "period": metrics.period,
            "pages": 10,
            "pptx": str(output_path),
            "speech": str(speech_path),
            "metrics": {
                "comprehensive_rate": metrics.comprehensive_rate,
                "current_unpaid": metrics.current_unpaid,
                "zero_count": metrics.zero_count,
                "zero_share": metrics.zero_share,
            },
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except DataValidationError as exc:
        print(f"[ppt_skill] 数据校验失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        failed = run_dir / "_failed_project"
        if failed.exists():
            shutil.rmtree(failed, ignore_errors=True)
        shutil.copytree(temp_project, failed)
        print(f"[ppt_skill] 生成失败：{exc}", file=sys.stderr)
        print(f"[ppt_skill] 排查目录：{failed}", file=sys.stderr)
        return 3
    finally:
        shutil.rmtree(temp_project, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
