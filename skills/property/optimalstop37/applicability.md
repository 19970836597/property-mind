#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
决策评分器 —— 加权决策矩阵计算 + 稳健性检验

用途：把「选项 × 维度 × 权重 × 打分」算成加权总分、排名，并检验结论是否稳健。
设计原则：**禁止虚假精确**。当分差很小时，工具会明确提示"数据无法区分"，
而不是假装第一名真的更好。

用法：
    python3 score.py --input decision.json              # 完整报告
    python3 score.py --input decision.json --json       # 机器可读输出
    python3 score.py --template > decision.json         # 生成一个输入模板
    cat decision.json | python3 score.py                # 从 stdin 读取
"""

import argparse
import json
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 常量与默认值

DEFAULT_SCALE = (1, 10)      # 打分默认区间
CLOSE_GAP_RATIO = 0.10       # 分差 < 满分的 10% 判定为"过于接近"
MC_TRIALS = 4000             # 蒙特卡洛次数
MC_WEIGHT_SIGMA = 0.35       # 权重相对不确定度（对数正态）
MC_SCORE_SIGMA = 0.8         # 打分的绝对不确定度（正态，1-10 尺度下约 ±0.8）
SEED = 42                    # 固定随机种子，保证结果可复现

TEMPLATE = {
    "question": "（一句话描述你要做的决定）",
    "options": ["选项A", "选项B", "维持现状"],
    "criteria": [
        {"name": "维度1", "weight": 40},
        {"name": "维度2", "weight": 35},
        {"name": "维度3", "weight": 25},
    ],
    "scores": {
        "选项A": {"维度1": 8, "维度2": 6, "维度3": 7},
        "选项B": {"维度1": 6, "维度2": 9, "维度3": 5},
        "维持现状": {"维度1": 5, "维度2": 5, "维度3": 6},
    },
    "confidence": {"选项A": "中", "选项B": "低", "维持现状": "高"},
    "veto": {},
}


# ---------------------------------------------------------------- 输入处理

def load_input(args) -> Dict[str, Any]:
    if args.template:
        return None
    raw = sys.stdin.read() if args.input in (None, "-") else open(args.input, encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"[错误] 输入不是合法 JSON：{exc}")
    return data


def validate(data: Dict[str, Any], scale: Tuple[int, int]) -> None:
    """校验输入并就地修正：权重归一化、缺分补空、维度对齐。"""
    if not data.get("options"):
        sys.exit("[错误] 缺少 options（选项列表）")
    if not data.get("criteria"):
        sys.exit("[错误] 缺少 criteria（决策维度与权重）")

    opts: List[str] = list(data["options"])
    crits: List[Dict[str, Any]] = list(data["criteria"])
    for c in crits:
        if "name" not in c:
            sys.exit("[错误] 每个 criterion 必须有 name")
        c.setdefault("weight", 0)

    # 权重归一化到总和 100
    total_w = sum(float(c["weight"]) for c in crits)
    if total_w <= 0:
        sys.exit("[错误] 权重总和必须大于 0")
    if abs(total_w - 100.0) > 1e-6:
        data["_weight_raw_sum"] = total_w
        for c in crits:
            c["weight"] = float(c["weight"]) / total_w * 100.0

    scores = data.setdefault("scores", {})
    lo, hi = scale
    problems: List[str] = []
    for o in opts:
        row = scores.setdefault(o, {})
        for c in crits:
            v = row.get(c["name"])
            if v is None:
                problems.append(f"选项「{o}」在维度「{c['name']}」上缺分")
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                sys.exit(f"[错误] 选项「{o}」维度「{c['name']}」的分数不是数字：{v!r}")
            if not (lo - 0.001 <= fv <= hi + 0.001):
                problems.append(f"选项「{o}」维度「{c['name']}」分数 {fv} 超出 {lo}-{hi} 区间")
            row[c["name"]] = fv
        # 剔除未声明的多余维度
        for k in list(row.keys()):
            if k not in {c["name"] for c in crits}:
                row.pop(k)
    data["_problems"] = problems

    # 否决项（一票否决）：{"选项": "原因"}
    data.setdefault("veto", {})


# ---------------------------------------------------------------- 核心计算

def weighted_totals(data, weights: Optional[List[float]] = None) -> Dict[str, float]:
    opts = data["options"]
    crits = data["criteria"]
    ws = weights if weights is not None else [c["weight"] / 100.0 for c in crits]
    out = {}
    for o in opts:
        row = data["scores"].get(o, {})
        acc, wsum = 0.0, 0.0
        for c, w in zip(crits, ws):
            v = row.get(c["name"])
            if v is None:
                continue
            acc += float(v) * w
            wsum += w
        out[o] = acc / wsum if wsum > 0 else 0.0
    return out


def rank(totals: Dict[str, float]) -> List[Tuple[str, float]]:
    return sorted(totals.items(), key=lambda kv: -kv[1])


def single_criterion_winners(data) -> List[Tuple[str, str, float]]:
    """单维度独裁测试：如果只看某一个维度，谁最优。"""
    crits = data["criteria"]
    out = []
    for c in crits:
        best, bv = None, -1e9
        for o in data["options"]:
            v = data["scores"].get(o, {}).get(c["name"])
            if v is not None and v > bv:
                best, bv = o, v
        out.append((c["name"], best, bv))
    return out


def flip_thresholds(data, steps: int = 200) -> List[Dict[str, Any]]:
    """
    翻转阈值：某个维度的权重需要提升到多少（%），第一名才会改变。
    None 表示即使该维度权重拉满也无法翻盘 —— 说明结论不依赖这个维度。
    """
    crits = data["criteria"]
    opts = data["options"]
    base_w = [c["weight"] / 100.0 for c in crits]
    base_top = rank(weighted_totals(data))[0][0]

    results = []
    for idx, c in enumerate(crits):
        cur = base_w[idx]
        other_sum = sum(base_w) - cur
        if other_sum <= 1e-9:            # 该维度已占满权重
            results.append({"name": c["name"], "current": cur * 100,
                            "threshold": None, "flip_to": None})
            continue
        # 其余维度归一化后的加权基准分
        rest = {}
        for o in opts:
            row = data["scores"].get(o, {})
            acc = 0.0
            for j, cj in enumerate(crits):
                if j == idx:
                    continue
                v = row.get(cj["name"])
                if v is not None:
                    acc += (base_w[j] / other_sum) * v
            rest[o] = acc

        threshold, flip_to = None, None
        for k in range(1, steps + 1):
            t = cur + (1.0 - cur) * k / steps
            totals = {o: t * (data["scores"].get(o, {}).get(c["name"]) or 0.0)
                         + (1.0 - t) * rest[o] for o in opts}
            top = max(totals.items(), key=lambda kv: kv[1])[0]
            if top != base_top:
                threshold, flip_to = t * 100.0, top
                break
        results.append({"name": c["name"], "current": cur * 100,
                        "threshold": threshold, "flip_to": flip_to})
    return results


def monte_carlo(data, trials: int = MC_TRIALS, w_sigma: float = MC_WEIGHT_SIGMA,
                s_sigma: float = MC_SCORE_SIGMA) -> Dict[str, Any]:
    """同时扰动权重与打分，统计每个选项夺冠的概率（结论稳健度）。"""
    rng = random.Random(SEED)
    crits = data["criteria"]
    opts = data["options"]
    base_w = [max(c["weight"], 1e-6) / 100.0 for c in crits]
    base_s = {o: [data["scores"].get(o, {}).get(c["name"]) for c in crits] for o in opts}

    wins = {o: 0 for o in opts}
    margins = []
    for _ in range(trials):
        w = [x * _lognormal(rng, w_sigma) for x in base_w]
        tw = sum(w)
        w = [x / tw for x in w]
        totals = {}
        for o in opts:
            acc, wsum = 0.0, 0.0
            for j, c in enumerate(crits):
                v = base_s[o][j]
                if v is None:
                    continue
                vv = min(max(v + rng.gauss(0, s_sigma), 0.0), 10.0)
                acc += vv * w[j]
                wsum += w[j]
            totals[o] = acc / wsum if wsum else 0.0
        r = rank(totals)
        wins[r[0][0]] += 1
        if len(r) > 1:
            margins.append(r[0][1] - r[1][1])
    return {
        "trials": trials,
        "win_rate": {o: wins[o] / trials for o in opts},
        "mean_margin": sum(margins) / len(margins) if margins else 0.0,
    }


def _lognormal(rng, sigma: float) -> float:
    import math
    return math.exp(rng.gauss(0, sigma))


# ---------------------------------------------------------------- 报告渲染

def render_markdown(data, scale, mc: Dict[str, Any]) -> str:
    lo, hi = scale
    crits = data["criteria"]
    opts = data["options"]
    totals = weighted_totals(data)
    ranked = rank(totals)
    L: List[str] = []

    L.append(f"## 决策分析结果：{data.get('question', '（未命名决策）')}\n")

    if data.get("_weight_raw_sum") and abs(data["_weight_raw_sum"] - 100) > 1e-6:
        L.append(f"> 注：权重原始总和为 {data['_weight_raw_sum']:g}，已自动归一化到 100。\n")

    # 1) 加权矩阵
    head = "| 维度（权重） | " + " | ".join(opts) + " |"
    sep = "|" + "---|" * (len(opts) + 1)
    L.append("### 1. 加权决策矩阵\n")
    L.append(head)
    L.append(sep)
    for c in crits:
        cells = []
        for o in opts:
            v = data["scores"].get(o, {}).get(c["name"])
            cells.append("—" if v is None else f"{v:g}")
        L.append(f"| {c['name']}（{c['weight']:.0f}%） | " + " | ".join(cells) + " |")
    L.append("| **加权总分** | " + " | ".join(f"**{totals[o]:.2f}**" for o in opts) + " |")
    L.append("")

    # 2) 排名
    L.append("### 2. 排名与分差\n")
    L.append("| 排名 | 选项 | 加权总分 | 与第一名分差 | 置信度 |")
    L.append("|" + "---|" * 5)
    top_score = ranked[0][1]
    for i, (o, s) in enumerate(ranked, 1):
        conf = data.get("confidence", {}).get(o, "未评估")
        L.append(f"| {i} | {o} | {s:.2f} | {top_score - s:.2f} | {conf} |")
    L.append("")

    # 3) 结论稳健性判定
    span = hi - lo
    gap = top_score - ranked[1][1] if len(ranked) > 1 else 0.0
    L.append("### 3. 结论稳健性\n")
    if data.get("veto"):
        L.append("**一票否决项（命中即出局）：**")
        for o, why in data["veto"].items():
            L.append(f"- {o}：{why}")
        L.append("")
    if len(ranked) > 1 and gap < span * CLOSE_GAP_RATIO:
        L.append(f"⚠️ **分差过小（{gap:.2f} 分，低于满分区间 {span:g} 的 {CLOSE_GAP_RATIO:.0%}）** —— "
                 "矩阵无法可靠区分前两名，不要假装第一名真的更好。\n")
        L.append("应改用「决胜问题」来定夺，例如：\n"
                 "- 哪个选项在最重要的那一两个维度上明显更强？\n"
                 "- 哪个选项的最坏情况你更能承受？\n"
                 "- 哪个选项更可逆（错了容易撤回）？\n")
    elif len(ranked) > 1:
        L.append(f"分差 {gap:.2f} 分（占满分区间 {gap / span:.0%}），矩阵给出明确排序。\n")

    # 4) 单维度独裁测试
    L.append("### 4. 单维度独裁测试（只看一个维度时谁赢）\n")
    L.append("| 维度 | 该维度最优 | 得分 |")
    L.append("|" + "---|" * 3)
    for name, best, bv in single_criterion_winners(data):
        mark = " ⭐" if best == ranked[0][0] else ""
        L.append(f"| {name} | {best} | {bv:g}{mark} |")
    L.append("")

    # 5) 翻转阈值
    L.append("### 5. 权重敏感度（该维度权重涨到多少会翻盘）\n")
    L.append("| 维度 | 当前权重 | 翻盘所需权重 | 翻盘后领先者 |")
    L.append("|" + "---|" * 4)
    for r in flip_thresholds(data):
        if r["threshold"] is None:
            L.append(f"| {r['name']} | {r['current']:.0f}% | 不可能 | — |")
        else:
            L.append(f"| {r['name']} | {r['current']:.0f}% | {r['threshold']:.0f}% | {r['flip_to']} |")
    L.append("")
    L.append("「不可能」= 即使该维度权重拉到 100% 也改变不了第一名，说明结论不依赖它。\n")

    # 6) 蒙特卡洛
    if mc:
        L.append("### 6. 蒙特卡洛稳健度\n")
        L.append(f"对权重和打分同时施加随机扰动，重复 {mc['trials']} 次：\n")
        L.append("| 选项 | 夺冠概率 |")
        L.append("|" + "---|" * 2)
        for o, p in sorted(mc["win_rate"].items(), key=lambda kv: -kv[1]):
            L.append(f"| {o} | {p:.0%} |")
        L.append("")
        top_p = max(mc["win_rate"].values())
        if top_p < 0.60:
            L.append(f"⚠️ 领先者夺冠概率仅 {top_p:.0%}，结论对权重/打分假设高度敏感，"
                     "建议先补信息或收紧维度定义，而不是急着下结论。\n")
        else:
            L.append(f"领先者夺冠概率 {top_p:.0%}，结论在合理扰动下基本稳定。\n")

    if data.get("_problems"):
        L.append("### ⚠️ 数据问题\n")
        for p in data["_problems"]:
            L.append(f"- {p}")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- CLI

def main() -> None:
    ap = argparse.ArgumentParser(description="加权决策矩阵评分与稳健性检验")
    ap.add_argument("--input", "-i", nargs="?", const="-", default=None,
                    help="输入 JSON 文件路径，省略或 '-' 表示从 stdin 读取")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--template", action="store_true", help="输出输入模板")
    ap.add_argument("--no-mc", action="store_true", help="跳过蒙特卡洛模拟")
    ap.add_argument("--trials", type=int, default=MC_TRIALS, help=f"蒙特卡洛次数（默认 {MC_TRIALS}）")
    ap.add_argument("--min", type=float, default=DEFAULT_SCALE[0], help="打分下限（默认 1）")
    ap.add_argument("--max", type=float, default=DEFAULT_SCALE[1], help="打分上限（默认 10）")
    args = ap.parse_args()

    if args.template:
        print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
        return

    data = load_input(args)
    validate(data, (args.min, args.max))

    totals = weighted_totals(data)
    ranked = rank(totals)
    mc = None if args.no_mc else monte_carlo(data, trials=args.trials)

    if args.json:
        print(json.dumps({
            "question": data.get("question"),
            "ranking": [{"option": o, "score": round(s, 4)} for o, s in ranked],
            "gap": round(ranked[0][1] - ranked[1][1], 4) if len(ranked) > 1 else 0.0,
            "close": len(ranked) > 1 and (ranked[0][1] - ranked[1][1]) < (args.max - args.min) * CLOSE_GAP_RATIO,
            "single_criterion": [{"criterion": n, "winner": b, "score": v}
                                 for n, b, v in single_criterion_winners(data)],
            "flip_thresholds": flip_thresholds(data),
            "monte_carlo": mc,
            "problems": data.get("_problems", []),
        }, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(data, (args.min, args.max), mc))


if __name__ == "__main__":
    main()
