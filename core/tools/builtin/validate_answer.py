"""
TAOS Built-in Tool: Validate Answer.

Performs lightweight grounding checks against provided context chunks/sources.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from taos.config.constants import ToolRiskLevel
from taos.core.tools.registry import ToolDefinition, ToolPolicy


_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "to", "of", "in", "on",
    "for", "with", "that", "this", "it", "as", "by", "at", "from", "be", "can", "will",
}


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-zA-Z0-9]+", str(text or "").lower())
    return {p for p in parts if len(p) >= 3 and p not in _STOPWORDS}


async def validate_answer(
    answer: str,
    chunks: List[str] | None = None,
    sources: List[Dict[str, Any]] | None = None,
    min_overlap: float = 0.12,
) -> Dict[str, Any]:
    """
    Validate whether an answer is sufficiently grounded in provided chunks.
    """
    text = str(answer or "").strip()
    if not text:
        return {"success": False, "grounded": False, "score": 0.0, "issues": ["Empty answer"]}

    chunk_texts = [str(c or "").strip() for c in (chunks or []) if str(c or "").strip()]
    source_count = len(sources or [])

    if not chunk_texts:
        issues = []
        if source_count == 0:
            issues.append("No chunks or sources provided for grounding validation.")
        return {
            "success": True,
            "grounded": source_count > 0,
            "score": 0.3 if source_count > 0 else 0.0,
            "issues": issues,
            "source_count": source_count,
            "supported_sentences": 0,
            "unsupported_sentences": 0,
        }

    sentences = [s.strip() for s in re.split(r"[.!?]\s+", text) if s.strip()]
    if not sentences:
        sentences = [text]

    chunk_tokens = [_tokens(c) for c in chunk_texts]
    supported = 0
    unsupported = 0
    overlaps: List[float] = []
    for sentence in sentences:
        st = _tokens(sentence)
        if not st:
            continue
        best = 0.0
        for ct in chunk_tokens:
            if not ct:
                continue
            overlap = len(st & ct) / float(max(1, len(st)))
            if overlap > best:
                best = overlap
        overlaps.append(best)
        if best >= float(min_overlap):
            supported += 1
        else:
            unsupported += 1

    avg_overlap = sum(overlaps) / float(max(1, len(overlaps)))
    # Conservative grounding rule: if any sentence is unsupported, mark as not grounded.
    grounded = unsupported == 0 and avg_overlap >= float(min_overlap)
    issues: List[str] = []
    if unsupported > 0:
        issues.append(f"{unsupported} sentence(s) appear weakly supported by provided chunks.")
    if avg_overlap < float(min_overlap):
        issues.append("Average overlap is below grounding threshold.")

    return {
        "success": True,
        "grounded": bool(grounded),
        "score": round(float(avg_overlap), 3),
        "issues": issues,
        "source_count": source_count,
        "supported_sentences": supported,
        "unsupported_sentences": unsupported,
        "threshold": float(min_overlap),
    }


def create_validate_answer_tool() -> ToolDefinition:
    return ToolDefinition(
        name="validate_answer",
        description=(
            "Validate answer grounding against provided chunks/sources and return support score/issues."
        ),
        input_schema={
            "answer": "str",
            "chunks": "list[str]",
            "sources": "list[dict]",
            "min_overlap": "float",
        },
        handler=validate_answer,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=30,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=60,
        cost_estimate=0.0,
        timeout=10,
        tags=["validation", "grounding", "safety"],
    )
