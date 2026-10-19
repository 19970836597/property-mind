#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
37% 法则 · 头脑风暴决策引擎

流程（严格对应五步）：
  第一步  init     用户定一个时间窗口
  第二步  log      每次头脑风暴产出的版本都记录进去（必须即时记录）
  第三步  reserve  37% 时间内，用户说"这是预定版本" → 记为预定版本
  第四步  choose   37% 时间后选一个版本
                   · 无预定版本 → 该版本即最终版本
                   · 有预定版本 → 输出与预定版本的优缺点对比表，交由用户定夺
           finalize 确认最终版本
  第五步  due      37% 后仍未选定：
                   · 有预定版本 → 预定版本自动成为最终版本
                   · 无预定版本 → 提醒重新设置时间（extend）

子命令：init / log / amend / reserve / choose / finalize / due / extend / status / report
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

DEFAULT_RATIO = 1.0 / math.e          # 0.367879...
DEFAULT_STATE = "decision-state.json"
FMT = "%Y-%m-%d %H:%M"


# ---------------------------------------------------------------- 工具

def now() -> datetime:
    return datetime.now()


def load(state_path: str) -> Dict[str, Any]:
    if not os.path.exists(state_path):
        sys.exit(f"[错误] 找不到状态文件 {state_path}，请先执行 init")
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def save(state_path: str, st: Dict[str, Any]) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        sys.exit(f"[错误] 时间格式无法解析：{s}（应为 YYYY-MM-DDTHH:MM）")


def fmt_delta(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total <= 0:
        return "已到"
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} 天 {h} 小时"
    if h:
        return f"{h} 小时 {m} 分钟"
    return f"{m} 分钟"


def split_items(raw: Optional[str]) -> List[str]:
    """把 "a;b" 或 "a、b" 拆成列表。"""
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("、", ";").replace("，", ";").replace(",", ";").split(";")]
    return [p for p in parts if p]


def bullets(items: List[str], placeholder: str = "（未记录，需与用户补全）") -> str:
    return "<br>".join(f"• {x}" for x in items) if items else placeholder


def phase(st: Dict[str, Any], at: Optional[datetime] = None) -> Dict[str, Any]:
    at = at or now()
    start = parse_dt(st["start"])
    deadline = parse_dt(st["deadline"])
    switch = start + (deadline - start) * st.get("ratio", DEFAULT_RATIO)
    span = (deadline - start).total_seconds()
    progress = max(0.0, min(1.0, (at - start).total_seconds() / span)) if span else 0.0

    if at >= deadline:
        name = "已过期"
    elif at >= switch:
        name = "拍板期"
    else:
        name = "观察期"

    return {
        "phase": name,
        "observing": at < switch,
        "expired": at >= deadline,
        "progress": progress,
        "start": start, "deadline": deadline, "switch": switch,
        "to_switch": switch - at,
        "to_deadline": deadline - at,
    }


def find_version(st: Dict[str, Any], seq: Optional[int] = None, title: Optional[str] = None):
    for v in st["versions"]:
        if seq is not None and v["seq"] == seq:
            return v
        if title and v["title"] == title:
            return v
    return None


def describe(v: Dict[str, Any]) -> str:
    return f"v{v['seq']}「{v['title']}」"


# ---------------------------------------------------------------- 命令

def cmd_init(args) -> None:
    start = parse_dt(args.start) if args.start else now()
    deadline = parse_dt(args.deadline) if args.deadline else start + timedelta(minutes=args.minutes)
    if deadline <= start:
        sys.exit("[错误] 截止时间必须晚于开始时间")

    st: Dict[str, Any] = {
        "question": args.question,
        "start": start.isoformat(timespec="seconds"),
        "deadline": deadline.isoformat(timespec="seconds"),
        "ratio": args.ratio,
        "versions": [],
        "reserved": None,
        "final": None,
    }
    ph = phase(st)
    span = deadline - start

    print(f"## 第一步完成 · 时间窗口已设定\n")
    print(f"- 议题：{args.question}")
    print(f"- 窗口：{start:{FMT}} → {deadline:{FMT}}（共 {fmt_delta(span)}）")
    print(f"- **观察期截止（{args.ratio:.1%}）：{ph['switch']:{FMT}}**")
    print(f"  - 此刻之前：只管头脑风暴、记录版本，可以指定「预定版本」，**但不能最终选定**")
    print(f"  - 此刻之后：可以选定版本\n")
    print("**接下来的规则：**")
    print("1. 第二步：每次头脑风暴产出一个版本，立刻 `log` 记录；")
    print("2. 第三步：观察期内你说「这是预定版本」，我就记下它作为锚点；")
    print("3. 第四步：观察期后选定版本——有预定版本就出优缺点对比表，没有就直接定为最终版；")
    print("4. 第五步：到期仍未选定——有预定版本则它为最终版，没有则提醒你重设时间。\n")
    print(f"状态文件：`{args.state}`")
    save(args.state, st)


def cmd_log(args) -> None:
    st = load(args.state)
    if st.get("final"):
        print(f"⚠️ 已定最终版本 {describe(st['final'])}，本轮决策已结束。"
              f"（要继续发散请重新 init，或用 extend 开新议题）")
        return

    seq = len(st["versions"]) + 1
    v = {
        "seq": seq,
        "title": args.title,
        "summary": args.summary or "",
        "pros": split_items(args.pros),
        "cons": split_items(args.cons),
        "at": now().isoformat(timespec="seconds"),
    }
    st["versions"].append(v)
    ph = phase(st)

    print(f"### 已记录 {describe(v)}（共 {seq} 个版本）\n")
    if v["summary"]:
        print(f"> {v['summary']}\n")
    if ph["observing"]:
        print(f"📋 **观察期**（距 {ph['switch']:{FMT}} 还有 {fmt_delta(ph['to_switch'])}）\n")
        print("动作：**继续发散，不做最终决定。**")
        print("如果你觉得这个版本不错，可以说「这是预定版本」，我会记下它作为锚点。")
    elif not ph["expired"]:
        print(f"🎯 **拍板期**（距截止还有 {fmt_delta(ph['to_deadline'])}）\n")
        print("动作：**可以选定了。** 选定后我会与预定版本做优缺点对比（若有）。")
    else:
        print(f"⏰ **已过截止时间** —— 请执行 `due` 收口。\n")
    save(args.state, st)


def cmd_amend(args) -> None:
    """补全某个版本的优缺点（对比表需要这些素材）。"""
    st = load(args.state)
    v = find_version(st, args.seq, args.title)
    if v is None:
        sys.exit("[错误] 找不到该版本")
    if args.pros:
        v["pros"] = split_items(args.pros)
    if args.cons:
        v["cons"] = split_items(args.cons)
    if args.summary:
        v["summary"] = args.summary
    save(args.state, st)
    print(f"### 已补全 {describe(v)}\n")
    print(f"- 优点：{bullets(v['pros'], '（仍缺失）')}")
    print(f"- 缺点：{bullets(v['cons'], '（仍缺失）')}")


def cmd_reserve(args) -> None:
    st = load(args.state)
    ph = phase(st)
    v = find_version(st, args.seq, args.title)
    if v is None:
        sys.exit(f"[错误] 找不到该版本，已记录："
                 f"{', '.join(describe(x) for x in st['versions']) or '（无）'}")

    if not ph["observing"] and not args.force:
        print(f"⚠️ **预定版本只能在观察期内设定**（观察期已于 {ph['switch']:{FMT}} 结束）。\n"
              f"这条限制就是规则本身：拍板期再补一个锚点，等于给自己开后门。\n"
              f"若确有必要，加 `--force` 覆盖，或执行 `extend` 重设窗口。")
        return

    st["reserved"] = {"seq": v["seq"], "title": v["title"],
                      "at": now().isoformat(timespec="seconds"),
                      "forced": bool(args.force and not ph["observing"])}
    save(args.state, st)
    print(f"## 第三步完成 · 预定版本已记录：{describe(v)}\n")
    if v["summary"]:
        print(f"> {v['summary']}\n")
    print("它是观察期内的锚点。拍板期选定其他版本时，我会把它拉出来做优缺点对比；\n"
          "若到截止仍没选定，它会自动成为最终版本。\n")
    if st["reserved"].get("forced"):
        print("⚠️ 注意：这是在拍板期用 --force 设定的，规则效力打折。")


def cmd_choose(args) -> None:
    st = load(args.state)
    ph = phase(st)
    v = find_version(st, args.seq, args.title)
    if v is None:
        sys.exit(f"[错误] 找不到该版本，已记录："
                 f"{', '.join(describe(x) for x in st['versions']) or '（无）'}")

    if ph["observing"]:
        print(f"⚠️ **观察期内不能最终选定**（观察期到 {ph['switch']:{FMT}} 结束，"
              f"还有 {fmt_delta(ph['to_switch'])}）。\n"
              f"这段时间的任务是摸清「什么样算好」。现在拍板，就等于前面全白看了。\n"
              f"如果确实很想留着它，可以说「这是预定版本」，我记为预定版本。")
        return

    st["chosen"] = {"seq": v["seq"], "title": v["title"],
                    "at": now().isoformat(timespec="seconds")}

    reserved = st.get("reserved")
    if not reserved:
        st["final"] = {"seq": v["seq"], "title": v["title"],
                       "at": now().isoformat(timespec="seconds"),
                       "reason": args.reason or "观察期后选定，且无预定版本",
                       "basis": "direct"}
        save(args.state, st)
        print(f"## 第四步完成 · **最终版本：{describe(v)}**\n")
        if v["summary"]:
            print(f"> {v['summary']}\n")
        print("没有预定版本，**此版本直接成为最终版本。**\n")
        print("**现在停止发散，开始动手。**\n")
        if args.reason:
            print(f"理由：{args.reason}\n")
        print(f"复盘：`python3 {os.path.basename(__file__)} report --state {args.state}`")
        return

    save(args.state, st)
    rv = find_version(st, reserved["seq"]) or reserved
    print(f"## 第四步 · 选定 {describe(v)} —— 但存在预定版本，需要对比后定夺\n")
    print("| | " + f"预定版本 {describe(rv)}（第 {reserved['seq']} 个） | "
          f"本次选定 {describe(v)}（第 {v['seq']} 个） |")
    print("|---|---|---|")
    print(f"| 记录时间 | {rv.get('at', '—')[:16].replace('T', ' ')} | "
          f"{v.get('at', '—')[:16].replace('T', ' ')} |")
    print(f"| 摘要 | {rv.get('summary') or '—'} | {v.get('summary') or '—'} |")
    print(f"| 优点 | {bullets(rv.get('pros', []))} | {bullets(v.get('pros', []))} |")
    print(f"| 缺点 | {bullets(rv.get('cons', []))} | {bullets(v.get('cons', []))} |")
    print("")
    if not (rv.get("pros") or rv.get("cons") or v.get("pros") or v.get("cons")):
        print("⚠️ 两版的优缺点都还没记录，这张表暂时是空的。用 `amend` 补全后再判断。\n")
    print("**请定夺：** 保留预定版本，还是改用它？\n")
    print(f"```\npython3 {os.path.basename(__file__)} finalize --seq {rv['seq']} "
          f"--state {args.state}   # 采用预定版本\n"
          f"python3 {os.path.basename(__file__)} finalize --seq {v['seq']} "
          f"--state {args.state}   # 改用本次选定\n```")


def cmd_finalize(args) -> None:
    st = load(args.state)
    v = find_version(st, args.seq, args.title)
    if v is None:
        sys.exit("[错误] 找不到该版本")
    ph = phase(st)
    if ph["observing"]:
        print(f"⚠️ 仍在观察期（到 {ph['switch']:{FMT}} 结束）。提前定版意味着放弃后面更好的可能，\n"
              f"也放弃了「预定版本」这个锚点的意义。确定不再发散就继续。\n")
    st["final"] = {"seq": v["seq"], "title": v["title"],
                   "at": now().isoformat(timespec="seconds"),
                   "reason": args.reason or "", "basis": "compare"}
    save(args.state, st)
    print(f"## ✅ 最终版本：{describe(v)}\n")
    if v["summary"]:
        print(f"> {v['summary']}\n")
    if args.reason:
        print(f"决定理由：{args.reason}\n")
    print("**到此为止，不再发散，开始动手。**\n")
    print(f"复盘：`python3 {os.path.basename(__file__)} report --state {args.state}`")


def cmd_due(args) -> None:
    """第五步：到期检查与收口。"""
    st = load(args.state)
    ph = phase(st)

    if st.get("final"):
        print(f"## 已收口 · 最终版本：{describe(st['final'])}\n")
        print("无需再处理。")
        return

    if not ph["expired"] and not args.force:
        print(f"⏳ 还没到截止时间（{ph['deadline']:{FMT}}，还有 {fmt_delta(ph['to_deadline'])}）。\n"
              f"当前处于 **{ph['phase']}**，继续按流程走即可。"
              f"（确需提前收口请加 `--force`）")
        return

    chosen = st.get("chosen")
    reserved = st.get("reserved")

    if chosen:
        print(f"已选定 {describe(chosen)} 但尚未 finalize → 请执行 "
              f"`finalize --seq {chosen['seq']}`")
        return

    if reserved:
        rv = find_version(st, reserved["seq"]) or reserved
        st["final"] = {"seq": reserved["seq"], "title": reserved["title"],
                       "at": now().isoformat(timespec="seconds"),
                       "reason": "到期未选定，依规则由预定版本成为最终版本",
                       "basis": "reserved_fallback"}
        save(args.state, st)
        print(f"## 第五步 · 到期未选定，但有预定版本\n")
        print(f"**依规则，预定版本 {describe(rv)} 自动成为最终版本。**\n")
        if rv.get("summary"):
            print(f"> {rv['summary']}\n")
        print("**现在开始动手。**\n")
        print("这不是「认输」——预定版本是你在信息最充分的那段时间里亲手指定的锚点，\n"
              "它比「再想想」可靠得多。")
        return

    print("## 第五步 · 到期未选定，且没有预定版本\n")
    print("**这条决策无法收口，请重新设置时间。**\n")
    print("可能的原因：")
    print("- 时间窗口定得太短，还没发散够；")
    print("- 议题本身没想清楚，版本之间其实没有实质差异；")
    print("- 一直在发散，回避「停下来做决定」这件事（最常见）。\n")
    print(f"重设窗口（保留已有 {len(st['versions'])} 个版本）：\n")
    print(f"```\npython3 {os.path.basename(__file__)} extend --minutes 30 "
          f"--state {args.state}\n"
          f"python3 {os.path.basename(__file__)} extend "
          f"--deadline 2026-10-05T21:00:00 --state {args.state}\n```\n")
    if st["versions"]:
        best = max(st["versions"], key=lambda x: len(x.get("pros", [])) - len(x.get("cons", [])))
        print(f"（参考：目前记录里优点最突出的是 {describe(best)}，"
              f"如果你其实心里有数，可以直接 `finalize --seq {best['seq']}`。）")


def cmd_extend(args) -> None:
    st = load(args.state)
    deadline = parse_dt(args.deadline) if args.deadline else now() + timedelta(minutes=args.minutes)
    if args.reset:                      # 从现在起重新计时
        st["start"] = now().isoformat(timespec="seconds")
    st["deadline"] = deadline.isoformat(timespec="seconds")
    save(args.state, st)
    ph = phase(st)
    print(f"## 窗口已重设\n")
    print(f"- 起：{ph['start']:{FMT}} → 止：{ph['deadline']:{FMT}}")
    print(f"- 新的观察期截止：**{ph['switch']:{FMT}}**")
    print(f"- 已保留 {len(st['versions'])} 个版本"
          + (f"，预定版本 {describe(st['reserved'])} 仍有效" if st.get("reserved") else ""))
    print("\n提醒：延长窗口只能换来更多素材，换不来「更好的判断力」。\n"
          "如果这一轮结束时你还是不选，问题多半不在时间上。")


def cmd_status(args) -> None:
    st = load(args.state)
    ph = phase(st)
    print(f"## 状态：{st['question']}\n")
    print(f"- 阶段：**{ph['phase']}**（进度 {ph['progress']:.0%}）")
    print(f"- 观察期截止：{ph['switch']:{FMT}}"
          + (f"（还有 {fmt_delta(ph['to_switch'])}）" if ph["observing"] else "（已过）"))
    print(f"- 截止时间：{ph['deadline']:{FMT}}"
          + (f"（还有 {fmt_delta(ph['to_deadline'])}）" if not ph["expired"] else "（已过）"))
    print(f"- 已记录版本：{len(st['versions'])} 个")
    print(f"- 预定版本：{describe(st['reserved']) if st.get('reserved') else '无'}")
    print(f"- 最终版本：{describe(st['final']) if st.get('final') else '未定'}")
    if st.get("chosen") and not st.get("final"):
        print(f"- ⏳ 已选定 {describe(st['chosen'])}，等待 finalize")
    print()
    if st["versions"]:
        print("| # | 版本 | 摘要 | 优点 | 缺点 | 标记 |")
        print("|" + "---|" * 6)
        for v in st["versions"]:
            marks = []
            if st.get("reserved") and st["reserved"]["seq"] == v["seq"]:
                marks.append("📌预定")
            if st.get("chosen") and st["chosen"]["seq"] == v["seq"]:
                marks.append("🎯选定")
            if st.get("final") and st["final"]["seq"] == v["seq"]:
                marks.append("✅最终")
            print(f"| {v['seq']} | {v['title']} | {v.get('summary') or '—'} | "
                  f"{len(v.get('pros', []))} 条 | {len(v.get('cons', []))} 条 | "
                  f"{' '.join(marks)} |")


def cmd_report(args) -> None:
    st = load(args.state)
    ph = phase(st)
    print(f"## 决策报告：{st['question']}\n")
    print(f"- 窗口：{ph['start']:{FMT}} → {ph['deadline']:{FMT}}，"
          f"观察期截止 {ph['switch']:{FMT}}（{st.get('ratio', DEFAULT_RATIO):.1%}）")
    print(f"- 共记录 {len(st['versions'])} 个版本\n")

    print("| # | 版本 | 记录时间 | 摘要 | 优点 | 缺点 |")
    print("|" + "---|" * 6)
    for v in st["versions"]:
        print(f"| {v['seq']} | {v['title']} | {v['at'][:16].replace('T', ' ')} | "
              f"{v.get('summary') or '—'} | {bullets(v.get('pros', []), '—')} | "
              f"{bullets(v.get('cons', []), '—')} |")

    print()
    if st.get("reserved"):
        print(f"- 预定版本：{describe(st['reserved'])}")
    if st.get("final"):
        f_ = st["final"]
        print(f"- ✅ **最终版本：{describe(f_)}**（{f_.get('basis', '')}）")
        if f_.get("reason"):
            print(f"- 理由：{f_['reason']}")
    else:
        print("- 最终版本：**未定**（执行 `due` 收口）")

    print("\n**复盘要点：**")
    print("1. 观察期内产出的版本够不够多样？如果前几个都差不多，说明发散不足；")
    print("2. 预定版本定得准吗？它是不是最后真的赢了的那个？")
    print("3. 时间窗口合适吗？如果到点还很纠结，下次把窗口缩短三分之一；")
    print("4. 从定版到动手隔了多久？这个间隔越长，这套方法的收益流失越多。")


# ---------------------------------------------------------------- CLI

def main() -> None:
    ap = argparse.ArgumentParser(description="37% 法则 · 头脑风暴决策引擎")
    ap.add_argument("--state", default=DEFAULT_STATE, help=f"状态文件（默认 {DEFAULT_STATE}）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="第一步：设定时间窗口")
    p.add_argument("--question", required=True)
    p.add_argument("--deadline", help="截止时间 ISO，如 2026-10-05T21:00")
    p.add_argument("--minutes", type=int, default=30, help="从现在起多少分钟（无 --deadline 时生效）")
    p.add_argument("--start", help="开始时间 ISO（默认现在）")
    p.add_argument("--ratio", type=float, default=DEFAULT_RATIO)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("log", help="第二步：记录一个版本")
    p.add_argument("--title", required=True)
    p.add_argument("--summary", default="")
    p.add_argument("--pros", help='优点，用 ; 分隔')
    p.add_argument("--cons", help='缺点，用 ; 分隔')
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("amend", help="补全版本的优缺点")
    p.add_argument("--seq", type=int)
    p.add_argument("--title")
    p.add_argument("--summary")
    p.add_argument("--pros")
    p.add_argument("--cons")
    p.set_defaults(func=cmd_amend)

    p = sub.add_parser("reserve", help="第三步：指定预定版本（限观察期）")
    p.add_argument("--seq", type=int)
    p.add_argument("--title")
    p.add_argument("--force", action="store_true", help="拍板期强制设定")
    p.set_defaults(func=cmd_reserve)

    p = sub.add_parser("choose", help="第四步：观察期后选定版本")
    p.add_argument("--seq", type=int)
    p.add_argument("--title")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_choose)

    p = sub.add_parser("finalize", help="确认最终版本")
    p.add_argument("--seq", type=int)
    p.add_argument("--title")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("due", help="第五步：到期收口检查")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_due)

    p = sub.add_parser("extend", help="重设时间窗口")
    p.add_argument("--deadline")
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--reset", action="store_true", help="从现在起重算 37% 节点")
    p.set_defaults(func=cmd_extend)

    p = sub.add_parser("status", help="查看状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("report", help="决策报告")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
