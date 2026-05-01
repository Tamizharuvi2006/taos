from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from taos.core.governance.cost_policy import CostPolicy, RouteCostBudget, default_cost_policy
from taos.core.governance.usage_meter import UsageSnapshot


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    route: str
    reasons: List[str] = field(default_factory=list)
    budget: RouteCostBudget | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "route": self.route,
            "reasons": list(self.reasons),
            "budget": self.budget.__dict__.copy() if self.budget else None,
        }


class QuotaManager:
    def __init__(self, policy: CostPolicy | None = None) -> None:
        self._policy = policy or default_cost_policy()

    def check(self, usage: UsageSnapshot) -> QuotaDecision:
        budget = self._policy.budget_for(usage.route)
        reasons: List[str] = []
        if usage.llm_calls > budget.max_llm_calls:
            reasons.append("llm_call_budget_exceeded")
        if usage.search_calls > budget.max_search_calls:
            reasons.append("search_call_budget_exceeded")
        if usage.extract_calls > budget.max_extract_calls:
            reasons.append("extract_call_budget_exceeded")
        if usage.package_registry_calls > budget.max_package_registry_calls:
            reasons.append("package_registry_budget_exceeded")
        if usage.estimated_cost_usd > budget.max_estimated_cost_usd:
            reasons.append("estimated_cost_budget_exceeded")
        return QuotaDecision(allowed=not reasons, route=budget.route, reasons=reasons, budget=budget)

    def controlled_response(self, usage: UsageSnapshot) -> dict:
        decision = self.check(usage)
        return {
            "status": "allowed" if decision.allowed else "budget_exceeded",
            "message": (
                "Usage is within the configured route budget."
                if decision.allowed
                else "The request reached the configured route budget and was stopped safely."
            ),
            "usage": usage.to_dict(),
            "quota": decision.to_dict(),
        }

    def budget_matrix(self) -> dict:
        return self._policy.as_dict()
