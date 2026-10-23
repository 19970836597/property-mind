"""Multiple-choice knapsack allocation under a collection effort budget."""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

from .types import ActionCode

BudgetOption = Tuple[ActionCode, int, float]


def allocate_budget(
    options_by_case: Sequence[Sequence[BudgetOption]],
    budget_units: float,
) -> Dict[int, ActionCode]:
    """Choose at most one action per case while maximizing total value.

    Costs are configured in whole effort points. A 100x integer scale is used
    so fractional CLI budgets remain deterministic.
    """

    units = max(0, int(round(budget_units * 100)))
    negative = -math.inf
    dp = [0.0] + [negative] * units
    decisions: List[List[int]] = []

    for options in options_by_case:
        next_dp = [negative] * (units + 1)
        choice = [-1] * (units + 1)
        for used, accumulated in enumerate(dp):
            if accumulated == negative:
                continue
            for option_index, (_action, cost, value) in enumerate(options):
                scaled_cost = int(round(cost * 100))
                next_used = used + scaled_cost
                if next_used > units:
                    continue
                candidate = accumulated + value
                if candidate > next_dp[next_used]:
                    next_dp[next_used] = candidate
                    choice[next_used] = option_index
        dp = next_dp
        decisions.append(choice)

    best_used = max(range(units + 1), key=lambda used: dp[used])
    selected: Dict[int, ActionCode] = {}
    used = best_used
    for index in range(len(options_by_case) - 1, -1, -1):
        option_index = decisions[index][used]
        if option_index < 0:
            continue
        action, cost, _value = options_by_case[index][option_index]
        selected[index] = action
        used -= int(round(cost * 100))
    return selected
