"""
TAOS Planner — LLM-powered plan generation via OpenRouter.

Takes a goal string and context, produces a structured PlanObject
with sequential steps, tool assignments, and retry policies.

Production features:
- Structured JSON output from LLM
- Fallback model support
- Cost tracking per planning call
- Plan validation before returning
- Prompt versioning support
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import httpx

from taos.config.model_config import ModelConfig, ModelOrchestration
from taos.config.settings import get_settings
from taos.core.planner.prompt_templates import build_prompts
from taos.core.state.state_schema import PlanObject, PlanStep, RetryPolicy, StepType


class PlannerError(Exception):
    """Raised when plan generation fails."""
    pass


# ═══════════════════════════════════════════════════════════
# PROMPT TEMPLATES (PRD §10 — Prompt Versioning)
# ═══════════════════════════════════════════════════════════

PLANNER_SYSTEM_PROMPT = """You are TAOS Planner — a production-grade AI task planner.

Your job: Given a goal, produce a structured execution plan as a JSON array of steps.

RULES:
1. Each step must have: id, action (description), tool (tool name or null), tool_input (dict or null), depends_on (list of step ids), description
2. Steps execute sequentially by default
3. Available tools: {available_tools}
4. Maximum steps allowed: {max_steps}
5. Keep plans minimal — fewest steps to achieve the goal
6. If no tool is needed for a step, set tool to null (reasoning/synthesis steps)
7. Be specific in action descriptions — no vague instructions

OUTPUT FORMAT (strict JSON):
{{
  "steps": [
    {{
      "id": "step_1",
      "action": "Search for current information about X",
      "tool": "web_search",
      "tool_input": {{"query": "X latest information"}},
      "depends_on": [],
      "description": "Find current data about X"
    }},
    {{
      "id": "step_2",
      "action": "Analyze the search results and synthesize answer",
      "tool": null,
      "tool_input": null,
      "depends_on": ["step_1"],
      "description": "Process and analyze gathered information"
    }}
  ],
  "reasoning": "Brief explanation of why this plan was chosen"
}}"""

PLANNER_USER_PROMPT = """GOAL: {goal}

CONTEXT:
{context}

Generate an execution plan. Respond with ONLY valid JSON, no markdown."""


class Planner:
    """
    Production LLM-powered planner.
    
    Generates structured execution plans via OpenRouter API.
    Supports fallback models, cost tracking, and retry on failure.
    """

    def __init__(
        self,
        model_orchestration: Optional[ModelOrchestration] = None,
        available_tools: Optional[List[str]] = None,
    ):
        self._settings = get_settings()
        self._orchestration = model_orchestration or ModelOrchestration()
        self._available_tools = available_tools or []
        self._client = httpx.Client(timeout=60)
        self._prompt_version = 1

    async def generate_plan(
        self,
        goal: str,
        context: Optional[List[str]] = None,
        max_steps: Optional[int] = None,
        cost_budget: Optional[float] = None,
        intent: Optional[str] = None,
        available_tools_override: Optional[List[str]] = None,
    ) -> tuple[PlanObject, float]:
        """
        Generate an execution plan for a goal.
        
        Returns:
            Tuple of (PlanObject, cost_of_planning_call)
        """
        max_steps = max_steps or self._settings.max_steps
        cost_budget = cost_budget or self._settings.cost_budget_per_task
        context_str = "\n".join(context) if context else "No prior context."

        # ─── Build prompts ───
        tools_list = available_tools_override if available_tools_override is not None else self._available_tools
        _, system_prompt, user_prompt = build_prompts(
            goal=goal,
            context=context_str,
            available_tools=tools_list,
            max_steps=max_steps,
            intent=intent,
            extra_hints=context or [],
        )

        # 🚨 DEBUG PIPELINE OVERRIDE (PRD Output Routing Fix) 🚨
        if intent == "debug":
            system_prompt += "\n\nCRITICAL SYSTEM OVERRIDE: The intent is DEBUG/ERROR_FIX. You MUST NOT use ANY tools (no search, no file_system). Generate ONLY pure reasoning steps (tool: null)."

        # ─── Call LLM ───
        model_config = self._orchestration.planner
        start_time = time.time()

        try:
            response_text, cost = await self._call_llm(model_config, system_prompt, user_prompt)
        except Exception as primary_error:
            # ─── Try fallback model ───
            fallback = self._orchestration.get_fallback("planner")
            if fallback:
                try:
                    response_text, cost = await self._call_llm(fallback, system_prompt, user_prompt)
                except Exception as fallback_error:
                    raise PlannerError(
                        f"Both primary and fallback models failed. "
                        f"Primary: {primary_error}, Fallback: {fallback_error}"
                    )
            else:
                raise PlannerError(f"Plan generation failed: {primary_error}")

        # ─── Parse response ───
        plan = self._parse_plan_response(response_text, max_steps, cost_budget)
        plan = self._promote_research_dag_if_needed(
            plan=plan,
            goal=goal,
            intent=intent,
        )

        latency = time.time() - start_time
        return plan, cost

    async def _call_llm(
        self,
        model_config: ModelConfig,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, float]:
        """Call OpenRouter API and return (response_text, estimated_cost)."""
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://taos-agent.local",
            "X-Title": "TAOS AgentOS",
        }

        payload = {
            **model_config.to_api_params(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=model_config.timeout) as client:
            response = await client.post(
                f"{self._settings.openrouter_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # ─── Extract cost from OpenRouter response ───
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        # Rough cost estimation (varies by model)
        cost = (prompt_tokens * 0.000005) + (completion_tokens * 0.000015)

        return content, cost

    def _parse_plan_response(
        self,
        response_text: str,
        max_steps: int,
        cost_budget: float,
    ) -> PlanObject:
        """Parse LLM response into a validated PlanObject."""
        try:
            # Clean response (strip markdown code blocks if present)
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise PlannerError(f"Failed to parse plan JSON: {e}\nResponse: {response_text[:500]}")

        steps_data = data.get("steps", [])
        if not steps_data:
            raise PlannerError("Plan contains no steps")

        if len(steps_data) > max_steps:
            raise PlannerError(f"Plan has {len(steps_data)} steps, exceeds max of {max_steps}")

        # ─── Build PlanStep objects (defensive normalization against noisy LLM JSON) ───
        steps: List[PlanStep] = []
        for i, step_data in enumerate(steps_data):
            raw_id = step_data.get("id", f"step_{i + 1}")
            step_id = str(raw_id)

            raw_tool = step_data.get("tool")
            tool = raw_tool if isinstance(raw_tool, str) and raw_tool.strip() else None

            raw_tool_input = step_data.get("tool_input")
            tool_input: Optional[Dict[str, Any]]
            if raw_tool_input is None:
                tool_input = None
            elif isinstance(raw_tool_input, dict):
                tool_input = raw_tool_input
            elif isinstance(raw_tool_input, str):
                # LLMs often return a plain query string for web_search.
                if tool == "web_search":
                    tool_input = {"query": raw_tool_input}
                else:
                    tool_input = {"input": raw_tool_input}
            else:
                # Coerce non-dict JSON (list/number/bool) into a predictable envelope.
                tool_input = {"input": raw_tool_input}

            raw_depends_on = step_data.get("depends_on", [])
            if isinstance(raw_depends_on, list):
                depends_on = [str(dep) for dep in raw_depends_on if str(dep).strip()]
            elif raw_depends_on is None:
                depends_on = []
            else:
                depends_on = [str(raw_depends_on)]

            raw_action = step_data.get("action", "")
            action = str(raw_action).strip()

            raw_desc = step_data.get("description", action)
            description = str(raw_desc).strip() if raw_desc is not None else action

            raw_step_type = step_data.get("step_type", step_data.get("type"))
            step_type: Optional[StepType] = None
            if isinstance(raw_step_type, str):
                normalized = raw_step_type.strip().lower()
                if normalized == StepType.DAG_EXEC.value:
                    step_type = StepType.DAG_EXEC
                elif normalized == StepType.REASON.value:
                    step_type = StepType.REASON
                elif normalized == StepType.TOOL.value:
                    step_type = StepType.TOOL

            raw_dag_name = step_data.get("dag_name")
            dag_name = str(raw_dag_name).strip() if isinstance(raw_dag_name, str) and raw_dag_name.strip() else None

            raw_dag_input = step_data.get(
                "dag_input_template",
                step_data.get("dag_input", step_data.get("input")),
            )
            if isinstance(raw_dag_input, dict):
                dag_input_template: Optional[Dict[str, Any]] = raw_dag_input
            elif isinstance(raw_dag_input, str) and raw_dag_input.strip():
                dag_input_template = {"query": raw_dag_input.strip()}
            else:
                dag_input_template = None

            step = PlanStep(
                id=step_id,
                action=action,
                step_type=step_type or (StepType.TOOL if tool else StepType.REASON),
                tool=tool,
                tool_input=tool_input,
                dag_name=dag_name,
                dag_input_template=dag_input_template,
                depends_on=depends_on,
                retry_policy=RetryPolicy(),
                description=description,
            )
            steps.append(step)

        return PlanObject(
            steps=steps,
            max_steps=max_steps,
            cost_budget=cost_budget,
        )

    def close(self) -> None:
        """Close HTTP client."""
        self._client.close()

    def _promote_research_dag_if_needed(
        self,
        plan: PlanObject,
        goal: str,
        intent: Optional[str],
    ) -> PlanObject:
        """
        Promote complex research/news plans to a single DAG step.

        DAG remains an execution primitive under FSM control.
        """
        intent_key = (intent or "").strip().lower()
        if intent_key not in {"research", "news"}:
            return plan
        if any(step.step_type == StepType.DAG_EXEC for step in plan.steps):
            return plan

        step_count = len(plan.steps)
        tool_steps = sum(1 for step in plan.steps if step.tool)
        has_dependencies = any(step.depends_on for step in plan.steps)
        is_complex = step_count >= 4 or (tool_steps >= 2 and has_dependencies)
        if not is_complex:
            return plan

        dag_input: Dict[str, Any] = {
            "query": goal,
            "search_type": "news" if intent_key == "news" else "search",
        }
        if intent_key == "news":
            dag_input["recency_days"] = 2

        plan.steps = [
            PlanStep(
                id="dag_research_1",
                step_type=StepType.DAG_EXEC,
                action=f"Run structured research DAG for: {goal}",
                tool=None,
                tool_input=None,
                dag_name="research_v2",
                dag_input_template=dag_input,
                depends_on=[],
                retry_policy=RetryPolicy(),
                description="FSM-controlled micro-DAG execution for complex research",
            )
        ]
        return plan
