"""Evaluate a trained collection policy against the current manual rules."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import DATA_COMMON_DIR, OUTPUTS_DIR
from rl.config import load_config
from rl.evaluator import CollectionPolicyEvaluator
from rl.heuristic import HeuristicPolicyService
from rl.loader import load_collection_dataset
from rl.policy import CollectionPolicyService
from rl.timeutils import business_today


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评测物业催缴 RL 策略")
    parser.add_argument(
        "--policy",
        default=str(OUTPUTS_DIR / "rl" / "collection_policy_v1.json"),
    )
    parser.add_argument("--data-dir", default=str(DATA_COMMON_DIR))
    parser.add_argument("--as-of", help="账龄参考日 YYYY-MM-DD，默认今天")
    parser.add_argument("--episodes", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--budget-units", type=float)
    parser.add_argument("--output-dir")
    return parser.parse_args()


def _markdown(report) -> str:
    lines = [
        "# 物业催缴 RL 策略评测",
        "",
        f"- 策略版本: `{report.policy_version}`",
        f"- 对照策略: `{report.baseline_version}`",
        f"- 模拟回合: {report.episodes}",
        f"- 累计回款周期: {report.horizon_days} 天",
        f"- 总体通过: {'是' if report.passed else '否'}",
        "",
        "| 场景 | 现行规则回款 | RL 回款 | 差值 | 95% CI | 安全违规 | 预算超支 | 通过 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for profile in report.profiles:
        lines.append(
            f"| {profile.profile} | {profile.baseline_mean_recovery:.2f} "
            f"| {profile.candidate_mean_recovery:.2f} "
            f"| {profile.mean_difference:.2f} "
            f"| [{profile.difference_ci_lower:.2f}, "
            f"{profile.difference_ci_upper:.2f}] "
            f"| {profile.unsafe_action_count} "
            f"| {profile.budget_breach_count} "
            f"| {'是' if profile.passed else '否'} |"
        )
    lines += ["", "## 限制", ""]
    lines.extend(f"- {item}" for item in report.limitations)
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else business_today()
    config = load_config()
    dataset = load_collection_dataset(
        args.data_dir,
        as_of=as_of,
        config=config,
    )
    candidate = CollectionPolicyService.from_path(args.policy, config=config)
    evaluator = CollectionPolicyEvaluator(dataset, config=config)
    report = evaluator.compare(
        candidate,
        baseline=HeuristicPolicyService(config),
        episodes=args.episodes,
        seed=args.seed,
        budget_units=args.budget_units,
    )
    output_dir = Path(args.output_dir or Path(args.policy).parent)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation.json").write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "evaluation.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    for profile in report.profiles:
        print(
            f"{profile.profile}: baseline={profile.baseline_mean_recovery:.2f}, "
            f"rl={profile.candidate_mean_recovery:.2f}, "
            f"difference={profile.mean_difference:.2f}, "
            f"passed={profile.passed}"
        )
    print(f"总体通过: {report.passed}")
    print(f"评测报告: {output_dir / 'evaluation.md'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
