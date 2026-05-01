"""
TAOS Output Templates — Strict response templates per intent type.

UPGRADE #1: OUTPUT CONSISTENCY

Every response MUST follow a predictable, intent-specific format.
No more guessing — users always know where to look.

Templates:
- SIMPLE_LOOKUP: Direct answer only
- DEFINITION: Definition + context
- COMPARISON: Side-by-side structured output  
- TASK/DEBUG: Step-by-step with commands
- NEWS: Timestamped updates
- RESEARCH: Sections + sources
- TRANSFORM: Formatted transformation
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from taos.core.semantic.intent_classifier import IntentType


# ═══════════════════════════════════════════════════════════
# STRICT TEMPLATES
# ═══════════════════════════════════════════════════════════

_TEMPLATES: Dict[IntentType, str] = {
    IntentType.SIMPLE_LOOKUP: """👉 {answer}

Confidence: {confidence}""",

    IntentType.DEFINITION: """👉 {answer}

📖 Context:
{context}

Confidence: {confidence}""",

    IntentType.COMPARISON: """👉 {answer}

┌─────────────────────────────┐
│ Comparison                  │
├──────────┬──────────────────┤
{comparison_rows}
└──────────┴──────────────────┘

Key Differences:
{key_points}

Confidence: {confidence}""",

    IntentType.TASK: """👉 {answer}

Steps:
{steps}

{commands}

⚠️ Notes:
{notes}

Confidence: {confidence}""",

    IntentType.NEWS: """👉 {answer}

📰 Updates:
{updates}

Sources:
{sources}

Confidence: {confidence}""",

    IntentType.RESEARCH: """👉 {answer}

📋 Summary:
{summary}

Key Findings:
{key_points}

Sources:
{sources}

Confidence: {confidence}""",

    IntentType.TRANSFORM: """👉 {answer}

Confidence: {confidence}""",
}


class TemplateFormatter:
    """
    Strict template-based formatter (Upgrade #1).

    Ensures EVERY response follows a predictable, intent-specific structure.
    Users always know where to look for the information they need.
    """

    def format(
        self,
        intent: IntentType,
        answer: str,
        confidence: str = "",
        key_points: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        steps: Optional[List[str]] = None,
        commands: Optional[List[str]] = None,
        context: str = "",
        comparison_items: Optional[List[Dict[str, str]]] = None,
        notes: Optional[List[str]] = None,
    ) -> str:
        """
        Format a response using the strict template for the given intent.

        Args:
            intent: The classified intent type.
            answer: The direct answer.
            confidence: Confidence display string.
            key_points: Bullet points.
            sources: Source URLs.
            steps: Step-by-step instructions.
            commands: Code/CLI commands.
            context: Additional context.
            comparison_items: Items for comparison table.
            notes: Warning/notes.

        Returns:
            Formatted string following the strict template.
        """
        template = _TEMPLATES.get(intent, _TEMPLATES[IntentType.TASK])

        # Build field values
        fields = {
            "answer": answer.strip(),
            "confidence": confidence or "—",
            "context": context or "—",
            "summary": context or answer[:200],
        }

        # Key points
        if key_points:
            fields["key_points"] = "\n".join(f"• {p}" for p in key_points)
        else:
            fields["key_points"] = "• See details above"

        # Sources
        if sources:
            fields["sources"] = "\n".join(f"  📎 {s}" for s in sources[:5])
        else:
            fields["sources"] = "  📎 Based on available knowledge"

        # Steps (for task/debug)
        if steps:
            fields["steps"] = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
        else:
            fields["steps"] = self._extract_steps(answer)

        # Commands
        if commands:
            fields["commands"] = "Commands:\n" + "\n".join(f"  $ {c}" for c in commands)
        else:
            fields["commands"] = self._extract_commands(answer)

        # Notes
        if notes:
            fields["notes"] = "\n".join(f"  • {n}" for n in notes)
        else:
            fields["notes"] = "  • Verify in your environment before applying"

        # Comparison rows
        if comparison_items:
            rows = []
            for item in comparison_items:
                name = item.get("name", "")
                value = item.get("value", "")
                rows.append(f"│ {name:<8s} │ {value:<16s} │")
            fields["comparison_rows"] = "\n".join(rows)
        else:
            fields["comparison_rows"] = self._extract_comparison(answer)

        # News updates
        fields["updates"] = self._extract_updates(answer)

        # Apply template
        try:
            result = template.format(**fields)
        except KeyError:
            # Fallback: simple format
            result = f"👉 {answer}\n\nConfidence: {confidence}"

        # Clean up empty sections
        result = self._clean_empty_sections(result)

        return result

    def _extract_steps(self, text: str) -> str:
        """Extract numbered steps from text."""
        steps = re.findall(r"^\s*\d+[.\)]\s+(.+)$", text, re.MULTILINE)
        if steps:
            return "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))

        # Try bullet points
        bullets = re.findall(r"^[\s]*[•\-\*]\s+(.+)$", text, re.MULTILINE)
        if bullets:
            return "\n".join(f"  {i+1}. {s}" for i, s in enumerate(bullets))

        # Generate from sentences
        sentences = [s.strip() for s in re.split(r'[.!]', text) if len(s.strip()) > 15]
        if sentences:
            return "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sentences[:5]))

        return "  1. See answer above"

    def _extract_commands(self, text: str) -> str:
        """Extract code/CLI commands from text."""
        # Look for code blocks
        code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
        if code_blocks:
            commands = []
            for block in code_blocks:
                for line in block.strip().split("\n"):
                    if line.strip():
                        commands.append(f"  $ {line.strip()}")
            return "Commands:\n" + "\n".join(commands) if commands else ""

        # Look for inline commands
        inline = re.findall(r"`([^`]+)`", text)
        if inline and any(len(c) > 5 for c in inline):
            return "Commands:\n" + "\n".join(f"  $ {c}" for c in inline if len(c) > 5)

        return ""

    def _extract_comparison(self, text: str) -> str:
        """Build comparison rows from text (fallback)."""
        return "│ See     │ details above    │"

    def _extract_updates(self, text: str) -> str:
        """Extract news updates from text."""
        bullets = re.findall(r"^[\s]*[•\-\*]\s+(.+)$", text, re.MULTILINE)
        if bullets:
            return "\n".join(f"  📌 {b}" for b in bullets[:5])
        return f"  📌 {text[:200]}"

    def _clean_empty_sections(self, text: str) -> str:
        """Remove empty sections from formatted output."""
        lines = text.split("\n")
        cleaned = []
        skip_empty_section = False

        for line in lines:
            # Skip sections with only "—" content
            if line.strip() == "—":
                skip_empty_section = True
                continue
            if skip_empty_section and line.strip() == "":
                skip_empty_section = False
                continue
            skip_empty_section = False
            cleaned.append(line)

        return "\n".join(cleaned)
