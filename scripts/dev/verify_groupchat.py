# -*- coding: utf-8 -*-
"""离线验证：建群→切换→@跨群→解散，全流程走一遍（stub，不调 LLM）。

用法::

    .venv/Scripts/python.exe scripts/dev/verify_groupchat.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tests"))

from test_groupchat_registry import _registry  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  —— {detail}" if detail else ""))


def main():
    reg = _registry()
    check("默认群全员", len(reg.active.members) == 2,
          f"成员={[m.display for m in reg.active.members]}")

    reg.create("催收群", raw_mentions="@催费 @法务")
    check("建群自动切换", reg.active_name == "催收群")

    final = reg.active.run("@催费 欠款多少")
    check("@点名发布", "欠费100万" in final, f"回答={final[:40]}")
    check("raw不进群",
          [t.speaker for t in reg.active.history.turns] == ["user", "manager"])

    reg.create("法务群", raw_mentions="@法务")
    final = reg.active.run("@催费 欠款多少")
    check("@跨群提示", "没有找到 @催费" in final)

    # 群共享隔离：催收群的结论在法务群召回不到
    hits = reg.active.shared.recall("欠款多少")
    check("共享记忆按群隔离", not hits, f"召回{len(hits)}条")

    print(reg.describe())
    msg = reg.disband("法务群")
    check("解散回默认群", reg.active_name == "默认群", msg)

    print("=" * 60)
    print(f"结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
    for name in FAIL:
        print(f"  ✗ {name}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
