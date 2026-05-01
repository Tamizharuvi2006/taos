"""Intent-aware planner prompt templates and builder utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class PlannerTemplate:
    name: str
    system_role: str
    rules: List[str]
    constraints: List[str]


_BASE_OUTPUT_FORMAT = (
    "Return strict JSON with key `steps` as an array of objects. "
    "Each step must include: id, action, tool, tool_input, depends_on, description. "
    "Optional fields: step_type (`tool`|`reason`|`dag_exec`), dag_name, dag_input_template."
)


TEMPLATES: dict[str, PlannerTemplate] = {
    "research": PlannerTemplate(
        name="research",
        system_role="You are a research planner.",
        rules=[
            "Always include information gathering before synthesis.",
            "Cross-validate claims across multiple sources.",
            "Avoid single-source conclusions.",
        ],
        constraints=[
            "Prefer web_search for factual freshness.",
            "Include a synthesis step with citations-ready structure.",
            "For complex 3+ dependent operations, prefer one dag_exec step using dag_name='research_v2'.",
        ],
    ),
    "comparison": PlannerTemplate(
        name="comparison",
        system_role="You are a comparison planner.",
        rules=[
            "Collect evidence for each entity separately.",
            "Ensure equal coverage before concluding.",
            "Prefer metric-backed comparisons when possible.",
        ],
        constraints=[
            "Do not compare without first collecting data.",
            "Include explicit normalization/synthesis step.",
        ],
    ),
    "task": PlannerTemplate(
        name="task",
        system_role="You are a task execution planner.",
        rules=[
            "Break goals into actionable steps.",
            "Select tools only when required.",
            "Include validation steps for critical outputs.",
        ],
        constraints=[
            "Optimize for reliability and completion.",
            "Avoid unnecessary steps.",
        ],
    ),
    "debug": PlannerTemplate(
        name="debug",
        system_role="You are a debugging planner.",
        rules=[
            "Identify root cause before proposing fixes.",
            "Avoid blind trial-and-error.",
            "Validate the fix after applying it.",
        ],
        constraints=[
            "Prefer reasoning-first flow.",
            "Do not use external tools unless explicitly needed.",
        ],
    ),
    "transform": PlannerTemplate(
        name="transform",
        system_role="You are a transformation planner.",
        rules=[
            "Operate only on provided content and context.",
            "Do not fetch external data.",
            "Keep plan minimal.",
        ],
        constraints=[
            "Prefer a single-step plan.",
            "No external tool usage.",
        ],
    ),
}


def select_template(intent: Optional[str]) -> PlannerTemplate:
    key = (intent or "task").lower()
    if key in {"news", "research", "simple_lookup"}:
        return TEMPLATES["research"]
    if key in {"comparison"}:
        return TEMPLATES["comparison"]
    if key in {"debug"}:
        return TEMPLATES["debug"]
    if key in {"transform", "definition"}:
        return TEMPLATES["transform"]
    return TEMPLATES["task"]


def build_prompts(
    *,
    goal: str,
    context: str,
    available_tools: List[str],
    max_steps: int,
    intent: Optional[str],
    extra_hints: Optional[List[str]] = None,
) -> tuple[str, str, str]:
    """
    Build planner prompts from selected template.

    Returns:
        (template_name, system_prompt, user_prompt)
    """
    template = select_template(intent)
    hints = extra_hints or []

    rule_block = "\n".join([f"- {r}" for r in template.rules])
    constraint_block = "\n".join([f"- {c}" for c in template.constraints])
    hint_block = "\n".join([f"- {h}" for h in hints]) if hints else "- none"

    system_prompt = (
        f"{template.system_role}\n\n"
        f"Available tools: {', '.join(available_tools) if available_tools else 'none'}\n"
        "Available DAGs: research_v2\n"
        f"Maximum steps: {max_steps}\n\n"
        "Rules:\n"
        f"{rule_block}\n\n"
        "Constraints:\n"
        f"{constraint_block}\n\n"
        "DAG guidance:\n"
        "- Use step_type='dag_exec' only for multi-stage dependent workflows.\n"
        "- For dag_exec, set tool to null and provide dag_name + dag_input_template.\n\n"
        f"Output format:\n{_BASE_OUTPUT_FORMAT}"
    )

    user_prompt = (
        f"Goal:\n{goal}\n\n"
        f"Context:\n{context}\n\n"
        "Planner hints:\n"
        f"{hint_block}\n\n"
        "Respond with ONLY valid JSON."
    )
    return template.name, system_prompt, user_prompt
