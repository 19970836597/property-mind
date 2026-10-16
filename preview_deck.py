# -*- coding: utf-8 -*-
"""把 pptx 渲染成可看的 PNG（用于审美检查与排版验收）

为什么要这个文件
----------------
LibreOffice Kit **直接渲染 pptx 会放大 1.5 倍并裁掉右下角**
（实测：设计 0~1" 的红块渲染在 0~1.50"，设计在 6.5~7.5" 的黄块直接消失）。
走 `convert pptx → pdf` 再 `render pdf → png` 则与设计**逐像素吻合**。

所以本工具固定走 PDF 中转，避免拿到一张"看起来有问题其实没问题"的残图。

用法
----
    python preview_deck.py <文件.pptx> [--pages 1,3,5] [--dpi 150]

输出到 <pptx 同目录>/preview/。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

NODE = r"D:\dsh\resources\runtime\primary-runtime\dependencies\node\bin\node.exe"
CLI = (r"D:\dsh\resources\app.asar.unpacked\dsh\node_modules"
       r"\@deepseek-ai\libreoffice-kit\lib\cli.js")


def _run(args: list) -> str:
    proc = subprocess.run([NODE, CLI] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"LibreOffice Kit 失败 (exit {proc.returncode}):\n"
            f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
        )
    return proc.stdout


def preview(pptx: str, pages: str = "", dpi: int = 150) -> dict:
    """渲染 pptx 为 PNG，返回 manifest（含图片路径）。"""
    if not os.path.isfile(pptx):
        raise FileNotFoundError(pptx)
    if not os.path.isfile(NODE) or not os.path.isfile(CLI):
        raise RuntimeError("找不到 bundled LibreOffice Kit")

    base = os.path.splitext(os.path.abspath(pptx))[0]
    pdf = base + ".preview.pdf"
    out_dir = os.path.join(os.path.dirname(base), "preview",
                           os.path.basename(base))

    # CLI 要求输出必须是**新建**的（PDF 与目录同理，已存在会 EEXIST）
    import shutil

    if os.path.exists(pdf):
        os.remove(pdf)

    # ① pptx → pdf
    _run(["convert", "--input", pptx, "--output", pdf])

    # ② pdf → png
    # CLI 要求输出目录由它**自己创建**（预先存在会 EEXIST），
    # 所以这里只清掉旧目录、保证父目录在，不自己 mkdir。
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(os.path.dirname(out_dir), exist_ok=True)
    render_args = ["render", "--input", pdf, "--output-dir", out_dir,
                   "--dpi", str(dpi)]
    if pages:
        render_args += ["--pages", pages]
    manifest = json.loads(_run(render_args))

    manifest["outDir"] = out_dir
    manifest["pdf"] = pdf
    manifest["pptx"] = pptx
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 pptx 供肉眼检查")
    parser.add_argument("pptx")
    parser.add_argument("--pages", default="", help="如 1,3,5；默认全部")
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    manifest = preview(args.pptx, args.pages, args.dpi)
    print(f"页数: {manifest['pageCount']}  dpi: {manifest['dpi']}")
    if manifest.get("missingFonts"):
        print(f"缺字体: {manifest['missingFonts']}")
    for item in manifest["images"]:
        print(f"  第 {item['page']} 页  {item['width']}x{item['height']}px  "
              f"{item['path']}")
    print(f"目录: {manifest['outDir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
