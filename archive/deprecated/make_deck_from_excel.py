# -*- coding: utf-8 -*-
"""把业务原始表（xlsx）转成 PPT 技能能吃的 JSON，并生成 PPT。

这演示的是**最关键的一步**：

    xlsx（业务原始表）
        │  ① 读取 + 聚合 + 科目映射（本脚本）
        ▼
    契约 JSON（固定字段名）
        │  ② 作为 skill 的 data 参数
        ▼
    ppt_skill（script 模式）→ .pptx + checks.json + 话术

为什么必须转换：PPT 技能走 script 模式，脚本是独立子进程，
只读 loader 注入的 input_data.json —— **它不会读 Excel**。
任何 Excel/CSV/数据库数据，都要先落到契约字段上。

用法：
    python make_deck_from_excel.py
    python make_deck_from_excel.py --xlsx <路径> --out <json路径> --deck <pptx目录>
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import openpyxl

# PPT 技能的页面/图表/配色是按「物业经营」硬编码的，
# 所以收入/成本必须落到它认识的固定科目上，否则图表标签和语义色会对不上。
INCOME_SLOTS = ("物业费", "公共收益", "多种经营", "其他")
COST_SLOTS = ("人力成本", "公共能耗", "外包服务费", "维修维保",
              "物料耗材", "管理费及其他")

# ⚠ 单位口径（最容易错、也最难看出来的地方）：
#   PPT 技能全篇约定：**金额单位为万元**、**比率用比值**（0.952 表示 95.2%）。
#   原始表里的成交金额是「元」，直接传进去会得到 120606432.76 万 这种荒唐数字。
YUAN_PER_WAN = 10000.0


def _to_month(value) -> str:
    """把单元格日期统一成 'YYYY-MM'。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    text = str(value).strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return text[:7].replace("/", "-")


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _wan(value: float) -> float:
    """元 → 万元，并保留 2 位小数。契约要求万元。"""
    return round(value / YUAN_PER_WAN, 2)


def _fit_slots(items: dict, slots) -> dict:
    """把任意数量的科目压进固定槽位。

    多的合并进最后一个槽位（其他 / 管理费及其他），少的补 0。
    """
    ordered = sorted(items.items(), key=lambda kv: kv[1], reverse=True)
    result = {}
    for i, slot in enumerate(slots):
        if i < len(slots) - 1:
            result[slot] = round(ordered[i][1], 2) if i < len(ordered) else 0.0
        else:
            result[slot] = round(sum(v for _k, v in ordered[len(slots) - 1:]), 2)
    return result


def build_contract_json(xlsx_path: str) -> dict:
    """读取成交流水，聚合成 PPT 技能的契约结构。"""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    def col(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else None

    # ---- 按月 / 按产品聚合 ----
    month_amount = defaultdict(float)
    month_orders = defaultdict(int)
    product_amount = defaultdict(float)

    for row in rows[1:]:
        if not row or row[0] is None:
            continue
        month = _to_month(row[idx.get("成交日期", 0)])
        amount = _num(col(row, "成交金额"))
        month_amount[month] += amount
        month_orders[month] += 1
        product_amount[str(col(row, "产品") or "未分类").strip()] += amount

    months = sorted(m for m in month_amount if m)
    if not months:
        raise SystemExit("没解析出任何月份，请检查「成交日期」列")

    current = months[-1]
    prev = months[-2] if len(months) > 1 else None
    cur_amount = month_amount[current]
    prev_amount = month_amount[prev] if prev else 0.0

    # ---- 收入：产品结构 → 4 个固定科目（元 → 万元）----
    income = _fit_slots({k: _wan(v) for k, v in product_amount.items()}, INCOME_SLOTS)

    # ---- 收入预算：演示用「环比上月」；真实场景请换成真预算表 ----
    if prev_amount:
        ratio = prev_amount / (sum(product_amount.values()) or 1.0)
        income_budget = {k: round(v * ratio, 2) for k, v in income.items()}
    else:
        income_budget = dict(income)

    # ---- 成本：当月按区域聚合 → 6 个固定科目（演示映射）----
    region_amount = defaultdict(float)
    for row in rows[1:]:
        if not row or row[0] is None:
            continue
        if _to_month(row[idx.get("成交日期", 0)]) != current:
            continue
        region = str(col(row, "区域") or "未分区").strip()
        region_amount[region] += _num(col(row, "成交金额"))

    cost = _fit_slots({k: _wan(v * 0.35) for k, v in region_amount.items()}, COST_SLOTS)
    cost_budget = {k: round(v * 1.1, 2) for k, v in cost.items()}

    cur_wan = _wan(cur_amount)
    prev_wan = _wan(prev_amount)

    return {
        "项目": f"销售成交分析（{current}）",
        "期间": current,
        "收入": income,
        "收入预算": income_budget,
        "成本": cost,
        "成本预算": cost_budget,
        "收缴": {
            "当月应收": cur_wan,
            "当月实收": round(cur_wan * 0.94, 2),
            "陈欠期初": prev_wan,
            "陈欠回收": round(prev_wan * 0.15, 2),
        },
        "账龄": {
            "0-30天": round(cur_wan * 0.10, 2),
            "31-90天": round(cur_wan * 0.06, 2),
            "91-180天": round(cur_wan * 0.04, 2),
            "180天以上": round(cur_wan * 0.02, 2),
        },
        "效能": {
            "人均管理面积": 3200,
            "人均产值": round(cur_wan / 18, 2),
            # ⚠ 比率用比值：0.415 会被渲染成 41.5%
            "人工成本率": 0.415,
            "人均工单量": 26.0,
        },
        # 原始口径留痕：技能会原样保留，便于回溯核对
        "_source": {
            "file": os.path.basename(xlsx_path),
            "rows": len(rows) - 1,
            "months": months,
            "product_amount": {k: round(v, 2) for k, v in product_amount.items()},
            "region_amount": {k: round(v, 2) for k, v in region_amount.items()},
            "month_amount": {k: round(v, 2) for k, v in month_amount.items()},
            "month_orders": dict(month_orders),
        },
    }


def generate_deck(payload: dict, output_dir: str) -> str:
    """把契约 JSON 交给 PPT 技能生成 PPT，返回 pptx 路径。"""
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    os.environ["SKILL_OUTPUT_DIR"] = os.path.abspath(output_dir)

    from skills.loader import load_skill  # noqa: E402  延迟导入，避免无谓依赖

    skill = load_skill(
        os.path.join(project_root, "skills", "buttin", "new-ppt-skill"),
        llm=None,
        execution="script",
    )
    result = skill.run_skill({
        "request": "把销售成交数据做成经营分析 PPT",
        "data": payload,
    })

    if not result.success:
        raise SystemExit(f"生成失败: {result.error}\n{result.output}")

    warnings = result.meta.get("warnings") or []
    if warnings:
        print("⚠ 数据警告:")
        for w in warnings:
            print(f"   - {w}")

    decks = [p for p in result.artifacts if p.lower().endswith(".pptx")]
    if not decks:
        raise SystemExit(f"没找到 pptx 产物: {result.artifacts}")
    return decks[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="xlsx → 契约 JSON → PPT")
    parser.add_argument("--xlsx",
                        default=r"C:\Users\12947\Downloads\4-7月销售成交汇总数据.xlsx")
    parser.add_argument("--out",
                        default=r"D:\PythonProject7\test_output\sales_data.json")
    parser.add_argument("--deck",
                        default=r"D:\PythonProject7\test_output\sales_deck")
    parser.add_argument("--no-deck", action="store_true", help="只产出 JSON，不生成 PPT")
    args = parser.parse_args()

    payload = build_contract_json(args.xlsx)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"① 契约 JSON 已写出: {args.out}")
    print(f"   项目: {payload['项目']}   期间: {payload['期间']}")
    print(f"   收入: {payload['收入']}")
    print(f"   来源行数: {payload['_source']['rows']}")

    if args.no_deck:
        return

    deck = generate_deck(payload, args.deck)
    print(f"② PPT 已生成: {deck}")


if __name__ == "__main__":
    main()
