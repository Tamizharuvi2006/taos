"""
TAOS Judge System — Auto-Refinement Layer.

Evaluates AI-generated output using SelfEvaluator.
If the quality falls into the 60%-85% zone, it triggers a cheap, fast
LLM call to surgically fix missing sections, formatting, or noise
BEFORE returning it to the user.

PRD §14: Self-Evaluation + Refine Auto-Fix
"""

from __future__ import annotations

import httpx
from typing import Dict, Any
from taos.config.settings import get_settings
from taos.config.model_config import ModelOrchestration
from taos.core.evaluation.self_evaluator import SelfEvaluator, EvaluationResult
from taos.core.semantic.intent_classifier import IntentType

class JudgeSystem:
    """
    Final Output Quality Check + Auto-Refinement.
    Ensures responses are clean, correct, and complete.
    """

    def __init__(self):
        self._settings = get_settings()
        self._evaluator = SelfEvaluator()
        
        # We use a fast, deterministic model for the judge
        # to keep latency and costs low
        self._model_config = ModelOrchestration().get_config("executor")

    async def judge_and_refine(
        self,
        output: str,
        goal: str,
        intent: IntentType,
        complexity: str = "medium"
    ) -> Dict[str, Any]:
        """
        Evaluate generated output and auto-fix if necessary.
        """
        evaluation = self._evaluator.evaluate(output=output, goal=goal, intent=intent)
        
        score = evaluation.overall_score
        
        # DECISION RULE & CONSISTENCY CHECK
        word_count = len(output.split())
        needs_length_fix = False
        eval_note = ""
        
        if complexity == "low" and word_count > 40:
            needs_length_fix = True
            eval_note = "CRITICAL: The answer is too long for a simple query. Compress to 1-2 lines maximum."
        elif complexity == "medium" and (word_count > 150 or word_count < 15):
            needs_length_fix = True
            eval_note = f"CRITICAL: The answer length ({word_count} words) is inconsistent for a medium query. Format to 3-6 lines."
        elif complexity == "high":
            if "vs" in goal.lower() or intent == IntentType.COMPARISON:
                if "Verdict" not in output:
                    needs_length_fix = True
                    eval_note = "CRITICAL: Comparisons must be strictly 3-4 lines total. Format as extremely brief bullets. Drop all introduction and dive straight to the verdict."
            elif intent == IntentType.RESEARCH:
                if not any(char.isdigit() for char in output) or "months" in output.lower():
                    needs_length_fix = True
                    eval_note = "CRITICAL: Research must be quantitative. Drop generic vague language, add explicit months, numbers, or exact data points. DENSITY over brevity."
            
            # Catchall fluff check
            if "various industries" in output or "will continue to" in output:
                 needs_length_fix = True
                 eval_note = "CRITICAL: The answer contains generic fluff. MAKE IT SHARPER. Replace vague sentences with concrete facts."

        if score >= 0.85 and not needs_length_fix:
            # Output is excellent and right length.
            return {
                "refined_output": output,
                "evaluation": evaluation,
                "was_refined": False
            }
            
        elif score >= 0.60 or needs_length_fix:
            # Output is okay but has weakness (incomplete, messy, or wrong length). Auto-refine.
            if eval_note:
                evaluation.suggestions.append(eval_note)
                
            refined = await self._run_llm_fix(output, goal, evaluation, complexity)
            return {
                "refined_output": refined,
                "evaluation": evaluation,
                "was_refined": True
            }
                
        else:
            # Output is garbage (< 0.60).
            # The system will return it as a failure or severely truncated.
            # For now, we strip it down to a basic failure message so 
            # it doesn't give hallucinated content.
            return {
                "refined_output": f"⚠️ The system failed to generate a complete answer.\n\nPartial Result:\n{output[:300]}...",
                "evaluation": evaluation,
                "was_refined": False
            }

    async def _run_llm_fix(self, raw_output: str, goal: str, eval_res: EvaluationResult, complexity: str) -> str:
        """Call LLM to surgically fix the missing sections or format."""
        
        issues_text = "\n".join([f"- {i}" for i in eval_res.issues])
        suggestions_text = "\n".join([f"- {s}" for s in eval_res.suggestions])
        
        prompt = f"""You are the final Judge System.
Your job is to fix and finalize the assistant's answer based on evaluation feedback.
DO NOT rewrite the entire answer from scratch. Only fix the weak parts.

Original Goal: {goal}

Current Answer:
{raw_output}

Evaluator Issues Detected:
{issues_text}

Evaluator Suggestions:
{suggestions_text}

FIX RULES:
1. UNIVERSAL RULE (CORE ANSWER FIRST): Answer the question directly in the very first sentence. No preamble.
2. If intent is DEFINITION: The first line MUST strictly be "<Subject> is...".
3. If intent is RESEARCH (AI TOOLS): When listing top tools, you MUST include a mix of GenAI (ChatGPT/Claude/Gemini) and Automation (Zapier). Do not bias towards one.
4. If intent is TRANSFORM (Follow-up): Ensure comparisons like "compare it with X" actually use the subject from the previous text provided in Context.
5. If intent is COMPARISON: The result MUST conclude with a clear bolded **Verdict:** line.
6. NO Markdown headers (#). Use only text formatting and lists.
7. Output ONLY the perfectly fixed context. No conversational intro.
"""

        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self._model_config.model_id,
            "messages": [{"role": "user", "content": prompt}],
            **self._model_config.to_api_params()
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip()
        except Exception:
            # If refinement fails, gracefully fall back to original output
            return raw_output
