"""
TAOS Semantic Layer — Query Rewriter.

Rewrites user queries for better tool/LLM results.
Handles ambiguity resolution, context injection, and
follow-up query expansion.

PRD §3: Query rewriting responsibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from taos.core.semantic.intent_classifier import IntentType, DomainType
from taos.core.semantic.query_normalizer import normalize_user_query


@dataclass
class RewriteResult:
    """Result of query rewriting."""

    original: str
    rewritten: str
    was_modified: bool = False
    context_injected: bool = False
    reason: str = ""


class QueryRewriter:
    """
    Production query rewriter (PRD §3).

    Responsibilities:
    1. Expand ambiguous pronouns using context
    2. Add search qualifiers for news/research queries
    3. Normalize query format for tools
    4. Merge follow-up with previous context
    """

    def rewrite(
        self,
        query: str,
        intent: IntentType,
        domain: DomainType,
        previous_context: Optional[str] = None,
        is_followup: bool = False,
    ) -> RewriteResult:
        """
        Rewrite a query for optimal tool/LLM performance.

        Args:
            query: Raw user query.
            intent: Classified intent type.
            domain: Classified domain.
            previous_context: Previous conversation context (for follow-ups).
            is_followup: Whether this is a follow-up query.

        Returns:
            RewriteResult with original and rewritten query.
        """
        rewritten = query.strip()
        was_modified = False
        reason_parts: List[str] = []

        # Step 0: Canonicalize noisy fragments before intent-specific rewrites.
        canonical = self._canonicalize_fragment(rewritten, previous_context=previous_context)
        if canonical != rewritten:
            rewritten = canonical
            was_modified = True
            reason_parts.append("canonicalized fragment")

        # Keep explicit context-anchor prompts stable; do not turn them into
        # research-style rewrites even if they contain words like "timeline".
        if rewritten.lower().startswith("previous turn requested"):
            return RewriteResult(
                original=query,
                rewritten=rewritten,
                was_modified=was_modified,
                context_injected=False,
                reason="; ".join(reason_parts + ["preserved context anchor"]).strip("; "),
            )

        # ─── Step 1: Follow-up expansion ───
        if is_followup and previous_context:
            expanded = self._expand_followup(rewritten, previous_context)
            if expanded != rewritten:
                rewritten = expanded
                was_modified = True
                reason_parts.append("follow-up expanded with context")

        # ─── Step 2: Intent-specific rewrites ───
        if intent == IntentType.NEWS:
            modified = self._add_recency_qualifier(rewritten)
            if modified != rewritten:
                rewritten = modified
                was_modified = True
                reason_parts.append("added recency qualifier")

        elif intent == IntentType.RESEARCH:
            modified = self._add_depth_qualifier(rewritten, domain)
            if modified != rewritten:
                rewritten = modified
                was_modified = True
                reason_parts.append("added depth qualifier")

        elif intent == IntentType.COMPARISON:
            modified = self._normalize_comparison(rewritten)
            if modified != rewritten:
                rewritten = modified
                was_modified = True
                reason_parts.append("normalized comparison format")

        # ─── Step 3: Domain-specific enrichment ───
        enriched = self._enrich_for_domain(rewritten, domain)
        if enriched != rewritten:
            rewritten = enriched
            was_modified = True
            reason_parts.append(f"enriched for {domain.value} domain")

        return RewriteResult(
            original=query,
            rewritten=rewritten,
            was_modified=was_modified,
            context_injected=is_followup and previous_context is not None,
            reason="; ".join(reason_parts) if reason_parts else "no changes needed",
        )

    # ─── Internal Methods ─────────────────────────────────

    def _expand_followup(self, query: str, context: str) -> str:
        """Expand follow-up queries by replacing pronouns with context."""
        # Replace "this", "that", "it" at the start with context reference
        pronoun_patterns = [
            (r"^(summarize|shorten|simplify|explain)\s+(this|that|it)\b",
             rf"\1 the following: {context[:200]}"),
            (r"^(this|that|it)\b",
             f"the following ({context[:100]})"),
        ]
        result = query
        for pattern, replacement in pronoun_patterns:
            new_result = re.sub(pattern, replacement, result, flags=re.I)
            if new_result != result:
                return new_result
        return result

    def _canonicalize_fragment(self, query: str, previous_context: Optional[str] = None) -> str:
        """Deterministic-first canonicalization for messy follow-up/doc/research fragments."""
        q = normalize_user_query(query)
        q_lower = q.lower()
        if not q:
            return q
        if self._is_profile_entity_lookup(q):
            return q

        if re.fullmatch(r"(next|continue|go on|keep going|go ahead)(\s+(please|pls|da|bro|macha|machi))?[.!?]*", q_lower):
            return "Continue with the previous answer."

        if re.search(
            r"\b(macha|machi|bro)?\s*(idha|itha|idhu|ithu|this)\b.*\b(explain|expain|sollu|solra|pannuda|pannu)\b.*\b(simple|short|brief|easy|ah)\b",
            q_lower,
        ):
            if previous_context:
                return (
                    "Explain the previously discussed topic in simple terms, "
                    "with key points and plain language."
                )
            return "Explain the requested topic in simple terms with key points."

        if q_lower.startswith("previous turn requested"):
            return q

        if re.search(r"\b(older one|same as before|that part|this part|do same|same thing|older one la)\b", q_lower):
            if previous_context:
                return (
                    "Summarize the previously discussed item in a concise way, based on this context: "
                    f"{previous_context[:220]}"
                )
            return "Summarize the previously discussed item in a concise way."

        if re.search(r"\b(explain|detail|detailed)\b.*\b(that part|this part)\b", q_lower):
            if previous_context:
                return (
                    "Explain the previously mentioned section in more detail, with clear step-by-step points. "
                    f"Context: {previous_context[:240]}"
                )
            return "Explain the previously mentioned section in more detail with clear step-by-step points."

        if re.fullmatch(r"what happened (there|here)\??", q_lower):
            if previous_context:
                return (
                    "Research what happened for the previously discussed event, summarize current reports, "
                    "and clearly mark uncertainty where reports conflict. "
                    f"Context: {previous_context[:220]}"
                )
            return "Research what happened for the referenced event and mark uncertainty where reports conflict."

        if re.search(r"\b(from this pdf|from this document|doc mode|important questions|uploaded notes?)\b", q_lower):
            return "Use the uploaded document context to answer this request with grounded evidence."

        if re.search(r"\b(latest|current|verify|official|status|timeline)\b", q_lower):
            if not re.search(r"\b(research|analy[sz]e|investigate)\b", q_lower):
                return f"Research and verify with current sources: {q}"
        return q

    def _add_recency_qualifier(self, query: str) -> str:
        """Add time qualifiers for news queries."""
        if self._is_profile_entity_lookup(query):
            return query
        if not re.search(r"\b(2025|2026|today|this\s+week|this\s+month|latest)\b", query, re.I):
            return f"{query} (latest 2026)"
        return query

    def _add_depth_qualifier(self, query: str, domain: DomainType) -> str:
        """Add depth qualifiers for research queries."""
        if self._is_profile_entity_lookup(query):
            return query
        if not re.search(r"\b(detailed|comprehensive|in-depth|thorough)\b", query, re.I):
            return f"comprehensive {query}"
        return query

    def _normalize_comparison(self, query: str) -> str:
        """Normalize comparison queries for better results."""
        # "X vs Y" → "comparison of X and Y: features, performance, pros/cons"
        match = re.match(r"^(.+?)\s+(?:vs\.?|versus)\s+(.+)$", query, re.I)
        if match:
            a, b = match.group(1).strip(), match.group(2).strip()
            return f"detailed comparison of {a} and {b}: features, performance, pros and cons"
        return query

    def _enrich_for_domain(self, query: str, domain: DomainType) -> str:
        """Add domain-specific context hints."""
        if domain == DomainType.AI and not re.search(r"\b(benchmark|model|paper)\b", query, re.I):
            # Don't modify short queries
            if len(query.split()) > 3:
                return query
        return query

    def _is_profile_entity_lookup(self, query: str) -> bool:
        q = str(query or "")
        return bool(
            re.search(
                r"\b("
                r"who\s+is|who\s+was|who\s+founded|who\s+started|ceo|founder|founded|linkedin|profile|biography|bio|leadership|board\s+member|"
                r"chairman|chairperson|director|coo|cto|cfo|official\s+website|official\s+site|official\s+page|"
                r"real\s+company|legit(?:imate)?|registered|company\s+details|about\s+company"
                r")\b",
                q,
                re.I,
            )
        )
