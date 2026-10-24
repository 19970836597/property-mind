"""Optional Agent capability for collection-policy recommendations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from core.paths import OUTPUTS_DIR, PROJECT_ROOT
from tools.tools import Tool, ToolParameter

from .config import load_config
from .loader import load_collection_dataset
from .policy import CollectionPolicyService
from .timeutils import business_today
from .types import ActionCode


class CollectionPolicyTool(Tool):
    """Read-only recommendation tool; never sends a collection message."""

    def __init__(self, policy_path: str | Path | None = None):
        super().__init__(
            name="collection_rl_policy",
            description=(
                "基于本地欠费数据和预算型 RL 策略，生成人工审核用的催缴动作建议。"
                "只排序与解释，不发送短信、不拨打电话、不生成或发送函件。"
            ),
        )
        self.tags = ["催缴", "欠费", "强化学习", "预算", "策略"]
        self.policy_path = Path(
            policy_path
            or OUTPUTS_DIR / "rl" / "collection_policy_v1.json"
        )

    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="budget_units",
                type="number",
                description="本周催缴努力点预算；默认 20。",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="as_of",
                type="string",
                description="账龄参考日 YYYY-MM-DD；默认今天。",
                required=False,
            ),
            ToolParameter(
                name="top_n",
                type="integer",
                description="最多显示的案例数；默认 20。",
                required=False,
                default=20,
            ),
        ]

    def run(self, params: Dict[str, Any]) -> str:
        if not self.policy_path.exists():
            return (
                f"❌ 未找到 RL 策略文件: {self.policy_path}\n"
                "请先运行 scripts/rl/train_collection_policy.py。"
            )
        as_of = (
            date.fromisoformat(str(params["as_of"]))
            if params.get("as_of")
            else business_today()
        )
        budget = float(params.get("budget_units") or 20)
        top_n = max(1, int(params.get("top_n") or 20))
        dataset = load_collection_dataset(as_of=as_of)
        service = CollectionPolicyService.from_path(str(self.policy_path))
        recommendations = service.recommend(
            dataset.cases, budget_units=budget, as_of=as_of
        )
        lines = [
            "## RL 催缴建议（需人工审核）",
            f"策略版本: `{service.artifact.version}`",
            f"预算: {budget:g} 努力点；案例: {len(recommendations)} 户",
            "",
            "| 案例ID | 动作 | 预测回款(元) | 成本 | 复核天数 | 安全说明 |",
            "|---|---|---:|---:|---:|---|",
        ]
        for item in recommendations[:top_n]:
            lines.append(
                f"| {item.case_id} | {item.action.value} "
                f"| {item.expected_recovered_amount:.2f} "
                f"| {item.effort_cost:g} | {item.review_after_days} "
                f"| {item.safety_reason} |"
            )
        spent = sum(
            item.effort_cost
            for item in recommendations
            if item.action != ActionCode.NO_ACTION
        )
        lines += [
            "",
            f"计划使用预算: {spent:g} / {budget:g}",
            "本次仅生成建议，未执行任何催缴动作。",
            f"项目根目录: {PROJECT_ROOT.name}",
        ]
        if service.last_fallback_reason:
            lines.append(f"回退说明: {service.last_fallback_reason}")
        return "\n".join(lines)
