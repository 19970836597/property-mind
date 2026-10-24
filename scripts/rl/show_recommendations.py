"""Preview local collection recommendations without executing any action."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import DATA_COMMON_DIR, OUTPUTS_DIR
from rl.loader import load_collection_dataset
from rl.policy import CollectionPolicyService
from rl.timeutils import business_today


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预览物业催缴 RL 建议")
    parser.add_argument(
        "--policy",
        default=str(OUTPUTS_DIR / "rl" / "collection_policy_v1.json"),
    )
    parser.add_argument("--data-dir", default=str(DATA_COMMON_DIR))
    parser.add_argument("--as-of", help="账龄参考日 YYYY-MM-DD，默认今天")
    parser.add_argument("--budget-units", type=float, default=20)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--csv", help="可选 CSV 输出路径")
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else business_today()
    dataset = load_collection_dataset(args.data_dir, as_of=as_of)
    service = CollectionPolicyService.from_path(args.policy)
    recommendations = service.recommend(
        dataset.cases,
        budget_units=args.budget_units,
        as_of=as_of,
    )
    print(f"策略: {service.artifact.version}")
    print(
        "案例ID | 动作 | 预测回款(元) | 成本 | 复核天数 | 安全说明"
    )
    for item in recommendations[: max(1, args.top_n)]:
        print(
            f"{item.case_id} | {item.action.value} "
            f"| {item.expected_recovered_amount:.2f} "
            f"| {item.effort_cost:g} | {item.review_after_days} "
            f"| {item.safety_reason}"
        )
    if service.last_fallback_reason:
        print(f"回退说明: {service.last_fallback_reason}")

    if args.csv:
        target = Path(args.csv)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "recommendation_id",
                    "case_id",
                    "action",
                    "expected_recovered_amount",
                    "effort_cost",
                    "review_after_days",
                    "safety_reason",
                    "policy_version",
                ],
            )
            writer.writeheader()
            for item in recommendations:
                writer.writerow(
                    {
                        "recommendation_id": item.recommendation_id,
                        "case_id": item.case_id,
                        "action": item.action.value,
                        "expected_recovered_amount": item.expected_recovered_amount,
                        "effort_cost": item.effort_cost,
                        "review_after_days": item.review_after_days,
                        "safety_reason": item.safety_reason,
                        "policy_version": item.policy_version,
                    }
                )
        print(f"CSV: {target}")
    print("本次未执行任何真实催缴动作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
