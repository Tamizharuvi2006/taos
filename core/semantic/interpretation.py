"""
TAOS semantic interpretation envelope.

Builds a normalized + canonicalized request profile used by routing,
policy overrides, and planner handoff.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Dict, List, Optional

from taos.core.semantic.intent_classifier import ClassificationResult, IntentType
from taos.core.semantic.query_rewriter import RewriteResult


_PROTECT_BLOCKS_RE = re.compile(r"```[\s\S]*?```")
_PROTECT_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_PROTECT_URL_RE = re.compile(r"https?://\S+")
_PROTECT_QUOTED_RE = re.compile(r'"[^"\n]*"')
_PROTECT_SINGLE_QUOTED_RE = re.compile(r"'[^'\n]{2,}'")

_TAMIL_SCRIPT_RE = re.compile(r"[\u0B80-\u0BFF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CODE_HINT_RE = re.compile(
    r"(```|`[^`]+`|[{()}[\];]|=>|\b(def|class|function|import|from|const|let|var|return)\b)",
    re.I,
)
_FOLLOWUP_HINT_RE = re.compile(
    r"\b("
    r"older one|same as before|that part|this part|do same|same thing|same for|"
    r"what about that|why that|is it fixed now|still true|previous one|that one|older one la"
    r"|that part more|explain that part more"
    r")\b",
    re.I,
)
_DOC_HINT_RE = re.compile(
    r"\b("
    r"pdf|document|doc|uploaded|from this|from this pdf|important questions|chapter|unit|"
    r"doc mode|notes|"
    r"(?:1|2|5|10|16)\s*mark|"
    r"all units?|full answers?"
    r")\b",
    re.I,
)
_RESEARCH_HINT_RE = re.compile(
    r"\b("
    r"latest|current|today|live|breaking|news|verify|verified|official|research|analyze|"
    r"status|timeline|source|sources|citation|compare"
    r")\b",
    re.I,
)
_PROFILE_HINT_RE = re.compile(
    r"\b("
    r"who is|who was|who founded|who started|ceo|founder|founded|linkedin|profile|biography|bio|leadership|board member|director|"
    r"coo|cto|cfo|official website|official site|official page|real company|legit(?:imate)?|registered|company details|about company"
    r")\b",
    re.I,
)
_ENTITY_LOOKUP_HINT_RE = re.compile(
    r"\b("
    r"who\s+is|who\s+was|who\s+founded|who\s+started|"
    r"ceo|founder|founded|cto|cfo|coo|director|chairman|chairperson|"
    r"board\s+member|leadership|linkedin|official\s+website|official\s+site|official\s+page|"
    r"real\s+company|legit(?:imate)?|registered|company\s+details|about\s+company"
    r")\b",
    re.I,
)
_TRANSFORM_HINT_RE = re.compile(
    r"\b(summarize|summary|shorten|short|simplify|rewrite|rephrase|translate|key points)\b",
    re.I,
)
_COMPARE_HINT_RE = re.compile(r"\b(vs\.?|versus|compare|difference|better than)\b", re.I)
_DEBUG_HINT_RE = re.compile(r"\b(error|bug|fix|broken|stacktrace|not working|issue)\b", re.I)
_TASK_HINT_RE = re.compile(r"\b(build|create|write|generate|implement|make|develop|design|code)\b", re.I)
_HIGH_STAKES_RE = re.compile(
    r"\b("
    r"medical|health|doctor|diagnosis|treatment|medicine|dosage|"
    r"legal|law|court|judgment|regulation|compliance|"
    r"financial|investment|stock|tax|securities|security"
    r")\b",
    re.I,
)
_AUTHORITY_VERIFICATION_RE = re.compile(
    r"\b("
    r"is it true|"
    r"professor said|teacher said|expert said|authority said|"
    r"some sources say|sources say|"
    r"real or fake|leaked|fake|confirmed"
    r")\b",
    re.I,
)
_ADVERSARIAL_RE = re.compile(
    r"\b("
    r"confirm even if|just tell me it's true|even if not|force answer|no uncertainty|"
    r"don't show uncertainty|say it's confirmed"
    r")\b",
    re.I,
)
_TAMIL_TRANSLIT_RE = re.compile(
    r"\b("
    r"macha|machi|idha|idhu|pannuda|pannu|sollu|solra|venum|apdi|ippo|inga|ingae|"
    r"epadi|epdi|enna|iruka|irukka|irukeenga|saptiya|saptaya|saaptiya|simple\s+ah"
    r")\b",
    re.I,
)
_TAMIL_VAGUE_REF_RE = re.compile(r"\b(idha|itha|idhu|ithu|adha|athu)\b", re.I)
_TINY_TALK_RE = re.compile(
    r"^\s*("
    r"hi+|hey+|hello+|yo+|sup|"
    r"thanks|thank\s+you|thx|"
    r"bye|goodbye|see you|cya|good night|gn|"
    r"ok(?:ay)?|k|cool|got it|understood|sounds good|nice"
    r")(\s+(bro|da|macha|machi))?\s*[!?.,]*\s*$",
    re.I,
)
_MINIMAL_PROGRESS_RE = re.compile(
    r"^\s*(?:next|continue|go on|keep going|go ahead)"
    r"(?:\s+(?:please|pls|da|bro|macha|machi))?\s*[.!?]*\s*$",
    re.I,
)
_CONTEXT_ANCHOR_RE = re.compile(r"^\s*previous\s+turn\s+requested\b", re.I)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class RequestInterpreter:
    """Build request interpretation envelope for routing safety."""

    mixed_language_fastpath_confidence_threshold: float = 0.80

    def normalize_query(self, query: str) -> str:
        text = unicodedata.normalize("NFKC", str(query or ""))
        protected: Dict[str, str] = {}
        token_index = 0

        def _protect(pattern: re.Pattern[str], source: str) -> str:
            nonlocal token_index

            def repl(match: re.Match[str]) -> str:
                nonlocal token_index
                token = f"__P{token_index}__"
                token_index += 1
                protected[token] = match.group(0)
                return token

            return pattern.sub(repl, source)

        for patt in (
            _PROTECT_BLOCKS_RE,
            _PROTECT_INLINE_CODE_RE,
            _PROTECT_URL_RE,
            _PROTECT_QUOTED_RE,
            _PROTECT_SINGLE_QUOTED_RE,
        ):
            text = _protect(patt, text)

        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"([?!.,]){2,}", r"\1", text)
        text = text.strip()

        for token, original in protected.items():
            text = text.replace(token, original)

        return text

    def detect_language_profile(self, raw_query: str, normalized_query: str) -> Dict[str, Any]:
        raw = str(raw_query or "")
        normalized = str(normalized_query or "")
        latin_count = len(_LATIN_RE.findall(normalized))
        tamil_script_count = len(_TAMIL_SCRIPT_RE.findall(normalized))
        transliteration_detected = bool(_TAMIL_TRANSLIT_RE.search(normalized))
        code_present = bool(_CODE_HINT_RE.search(raw))
        typo_density = self._estimate_typo_density(normalized)
        token_count = len(normalized.split())

        mixed_language_flag = bool(
            (latin_count > 0 and tamil_script_count > 0)
            or (transliteration_detected and latin_count > 0)
        )

        primary_language = "unknown"
        secondary_language = None
        if tamil_script_count > 0 and latin_count == 0:
            primary_language = "tamil"
        elif transliteration_detected and latin_count > 0 and tamil_script_count == 0:
            primary_language = "tamil_transliteration"
            secondary_language = "english"
        elif mixed_language_flag:
            primary_language = "mixed"
            secondary_language = "english"
        elif latin_count > 0:
            primary_language = "english"

        confidence = 0.90
        if primary_language == "mixed":
            confidence = 0.72
        elif primary_language == "tamil_transliteration":
            confidence = 0.70
        elif primary_language == "unknown":
            confidence = 0.55
        if typo_density >= 0.25:
            confidence -= 0.15
        if token_count <= 3:
            confidence -= 0.08
        confidence = _clamp(confidence, 0.30, 0.95)

        return {
            "primary_language": primary_language,
            "secondary_language": secondary_language,
            "mixed_language_flag": mixed_language_flag,
            "transliteration_detected": transliteration_detected,
            "code_present": code_present,
            "typo_density": round(float(typo_density), 3),
            "language_confidence": round(float(confidence), 3),
            "token_count": token_count,
        }

    def build_routing_profile(
        self,
        *,
        raw_query: str,
        normalized_query: str,
        rewritten_query: str,
        classification: ClassificationResult,
        has_context: bool,
        has_active_doc: bool,
        language_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        q_raw = str(raw_query or "")
        q_norm = str(normalized_query or "").lower()
        q_rewritten = str(rewritten_query or "").lower()
        minimal_progress_followup = bool(_MINIMAL_PROGRESS_RE.fullmatch(q_norm))
        context_anchor_prompt = bool(_CONTEXT_ANCHOR_RE.search(q_norm))
        combined = q_norm if minimal_progress_followup else f"{q_norm} {q_rewritten}".strip()
        route_label = str((classification.metadata or {}).get("route_label") or "").strip().lower()

        scores: Dict[str, float] = {
            "small_talk": 0.02,
            "simple_lookup": 0.02,
            "definition": 0.02,
            "comparison": 0.02,
            "transform": 0.02,
            "summarize": 0.02,
            "debug": 0.02,
            "task_execution": 0.02,
            "deep_research": 0.02,
            "document_qa": 0.02,
            "follow_up": 0.02,
        }

        intent_to_key = {
            IntentType.SIMPLE_LOOKUP: "simple_lookup",
            IntentType.DEFINITION: "definition",
            IntentType.COMPARISON: "comparison",
            IntentType.TRANSFORM: "transform",
            IntentType.DEBUG: "debug",
            IntentType.TASK: "task_execution",
            IntentType.RESEARCH: "deep_research",
            IntentType.NEWS: "deep_research",
        }
        mapped = intent_to_key.get(classification.intent)
        if mapped:
            scores[mapped] = max(scores[mapped], 0.64)
        if route_label == "fast_message":
            scores["small_talk"] = max(scores["small_talk"], 0.72)
        elif route_label == "deep_research":
            scores["deep_research"] = max(scores["deep_research"], 0.74)
        elif route_label == "doc_mode":
            scores["document_qa"] = max(scores["document_qa"], 0.86)
        elif route_label == "standard_task":
            scores["task_execution"] = max(scores["task_execution"], 0.58)

        if _TINY_TALK_RE.search(q_norm):
            scores["small_talk"] = max(scores["small_talk"], 0.88)
        if _FOLLOWUP_HINT_RE.search(combined) or classification.is_followup:
            scores["follow_up"] = max(scores["follow_up"], 0.84)
            scores["summarize"] = max(scores["summarize"], 0.46)
        if _DOC_HINT_RE.search(combined) or has_active_doc:
            scores["document_qa"] = max(scores["document_qa"], 0.82 if _DOC_HINT_RE.search(combined) else 0.56)
        if (not context_anchor_prompt) and (_RESEARCH_HINT_RE.search(combined) or _PROFILE_HINT_RE.search(combined)):
            scores["deep_research"] = max(scores["deep_research"], 0.72)
        if _TRANSFORM_HINT_RE.search(combined):
            scores["transform"] = max(scores["transform"], 0.72)
            scores["summarize"] = max(scores["summarize"], 0.62)
        compare_hint = bool(_COMPARE_HINT_RE.search(combined))
        research_hint = bool(_RESEARCH_HINT_RE.search(combined))
        profile_hint = bool(_PROFILE_HINT_RE.search(combined))
        time_sensitive_hint = bool(re.search(r"\b(latest|current|today|now|breaking|update)\b", combined))
        comparative_decision_prompt = bool(compare_hint and re.search(r"\b(which is better|better)\b", combined))
        if comparative_decision_prompt and not time_sensitive_hint and not profile_hint:
            # Prevent rewrite-introduced research terms (e.g., "comprehensive") from forcing deep research.
            research_hint = False
        if compare_hint:
            scores["comparison"] = max(scores["comparison"], 0.74)
            # Keep normal product/tool comparisons on standard-task unless explicit web freshness is required.
            if not research_hint and not profile_hint and not time_sensitive_hint:
                scores["comparison"] = max(scores["comparison"], 0.9)
                scores["task_execution"] = max(scores["task_execution"], 0.78)
                scores["deep_research"] = min(scores["deep_research"], 0.48)
        if _DEBUG_HINT_RE.search(combined):
            scores["debug"] = max(scores["debug"], 0.78)
        if _TASK_HINT_RE.search(combined):
            scores["task_execution"] = max(scores["task_execution"], 0.66)
        if _AUTHORITY_VERIFICATION_RE.search(combined):
            scores["deep_research"] = max(scores["deep_research"], 0.74)

        query_kind = "general"
        if (not context_anchor_prompt) and _ENTITY_LOOKUP_HINT_RE.search(combined) and not (_DOC_HINT_RE.search(combined) or has_active_doc):
            query_kind = "entity_lookup"
        elif _DOC_HINT_RE.search(combined) or has_active_doc:
            query_kind = "document_qa"
        elif (not context_anchor_prompt) and research_hint:
            query_kind = "research"

        # Context dependence + ambiguity
        context_required = bool(classification.is_followup or _FOLLOWUP_HINT_RE.search(combined))
        if minimal_progress_followup:
            # Minimal continuation prompts should carry context and use contextual fast handling.
            context_required = bool(has_context)
            scores["follow_up"] = max(scores["follow_up"], 0.9)
            scores["small_talk"] = max(scores["small_talk"], 0.78)
            scores["simple_lookup"] = max(scores["simple_lookup"], 0.72)
        if re.search(r"\b(this|that|it|those|these|older one|that one|same)\b", combined) and not _TINY_TALK_RE.search(q_norm):
            context_required = True
            scores["follow_up"] = max(scores["follow_up"], 0.68)
        if _TAMIL_VAGUE_REF_RE.search(combined) and not has_context:
            context_required = True
            scores["follow_up"] = max(scores["follow_up"], 0.72)

        grounding_need = "none"
        if _DOC_HINT_RE.search(combined) or has_active_doc:
            grounding_need = "docs"
        elif (not context_anchor_prompt) and (
            research_hint or profile_hint or _AUTHORITY_VERIFICATION_RE.search(combined)
        ):
            grounding_need = "web"
        elif context_required and has_context:
            grounding_need = "memory"
        elif context_required and not has_context:
            grounding_need = "memory"

        risk_level = "low"
        adversarial_detected = bool(_ADVERSARIAL_RE.search(combined))
        if adversarial_detected or _HIGH_STAKES_RE.search(combined):
            risk_level = "high"
        elif "verify" in combined or "official" in combined or _AUTHORITY_VERIFICATION_RE.search(combined):
            risk_level = "medium"

        ambiguity_score = 0.06
        if context_required and not has_context:
            ambiguity_score += 0.45
        if len(q_norm.split()) <= 4 and not _TINY_TALK_RE.search(q_norm):
            ambiguity_score += 0.12
        if _TAMIL_VAGUE_REF_RE.search(combined) and not has_context:
            ambiguity_score += 0.18
        if language_profile.get("mixed_language_flag") and float(language_profile.get("language_confidence") or 0.0) < 0.8:
            ambiguity_score += 0.20
        if float(classification.confidence or 0.0) < 0.65:
            ambiguity_score += 0.12
        if _FOLLOWUP_HINT_RE.search(combined):
            ambiguity_score += 0.12
        if grounding_need == "web":
            ambiguity_score += 0.05
        ambiguity_score = _clamp(ambiguity_score, 0.0, 0.95)

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        primary_intent = ordered[0][0]
        secondary_intent = ordered[1][0]
        intent_confidence = _clamp(float(ordered[0][1]), 0.0, 0.99)

        return {
            "intent_scores": {k: round(v, 3) for k, v in scores.items()},
            "primary_intent": primary_intent,
            "secondary_intent": secondary_intent,
            "intent_confidence": round(intent_confidence, 3),
            "ambiguity_score": round(float(ambiguity_score), 3),
            "context_required": bool(context_required),
            "grounding_need": grounding_need,
            "risk_level": risk_level,
            "adversarial_detected": adversarial_detected,
            "query_kind": query_kind,
            "tiny_talk_match": bool(_TINY_TALK_RE.search(q_norm)),
            "minimal_progress_followup": minimal_progress_followup,
            "context_anchor_prompt": context_anchor_prompt,
            "has_context": bool(has_context),
        }

    def select_and_verify_route(
        self,
        *,
        routing_profile: Dict[str, Any],
        language_profile: Dict[str, Any],
        has_context: bool,
        has_active_doc: bool,
    ) -> Dict[str, Any]:
        primary_intent = str(routing_profile.get("primary_intent") or "task_execution")
        ambiguity_score = float(routing_profile.get("ambiguity_score") or 0.0)
        context_required = bool(routing_profile.get("context_required"))
        grounding_need = str(routing_profile.get("grounding_need") or "none")
        risk_level = str(routing_profile.get("risk_level") or "low")
        tiny_talk_match = bool(routing_profile.get("tiny_talk_match"))
        minimal_progress_followup = bool(routing_profile.get("minimal_progress_followup"))
        context_anchor_prompt = bool(routing_profile.get("context_anchor_prompt"))
        adversarial_detected = bool(routing_profile.get("adversarial_detected"))
        query_kind = str(routing_profile.get("query_kind") or "general")

        selected_route = "standard_answer"
        route_reason = "default_standard_answer"
        if context_anchor_prompt:
            selected_route = "micro_fast"
            route_reason = "context_anchor_priming"
        elif grounding_need == "docs" or has_active_doc:
            selected_route = "document_pipeline"
            route_reason = "doc_grounding_required"
        elif minimal_progress_followup and has_context and grounding_need in {"none", "memory"}:
            selected_route = "micro_fast"
            route_reason = "contextual_progress_followup_fast"
        elif query_kind == "entity_lookup":
            selected_route = "entity_lookup"
            route_reason = "entity_lookup_detected"
        elif adversarial_detected:
            selected_route = "deep_research"
            route_reason = "adversarial_integrity_guard"
        elif context_required and not has_context and ambiguity_score >= 0.45 and grounding_need == "none":
            selected_route = "clarification"
            route_reason = "ambiguous_followup_without_context"
        elif grounding_need == "web":
            selected_route = "deep_research"
            route_reason = "web_grounding_required"
        elif primary_intent in {"deep_research"}:
            selected_route = "deep_research"
            route_reason = "research_intent"
        elif primary_intent in {"transform", "summarize"}:
            selected_route = "transform_pipeline"
            route_reason = "transform_intent"
        elif primary_intent in {"debug"}:
            selected_route = "standard_fsm_task"
            route_reason = "debug_task_intent"
        elif primary_intent in {"task_execution", "comparison"}:
            selected_route = "standard_answer"
            route_reason = "task_like_standard_answer"
        elif primary_intent in {"small_talk"} and tiny_talk_match:
            selected_route = "micro_fast"
            route_reason = "tiny_talk_exact"

        policy_override_reasons: List[str] = []
        blocked_fast = False
        if selected_route == "micro_fast":
            allow_contextual_micro_fast = bool(
                context_required
                and has_context
                and (tiny_talk_match or minimal_progress_followup)
                and primary_intent in {"small_talk", "simple_lookup", "follow_up"}
            )
            if context_required and not allow_contextual_micro_fast:
                blocked_fast = True
                policy_override_reasons.append("context_required_fastpath_block")
            if ambiguity_score >= 0.35:
                blocked_fast = True
                policy_override_reasons.append("ambiguity_fastpath_block")
            if grounding_need != "none" and not (allow_contextual_micro_fast and grounding_need == "memory"):
                blocked_fast = True
                policy_override_reasons.append("grounding_fastpath_block")
            if (
                bool(language_profile.get("mixed_language_flag"))
                and float(language_profile.get("language_confidence") or 0.0)
                < self.mixed_language_fastpath_confidence_threshold
                and not tiny_talk_match
            ):
                blocked_fast = True
                policy_override_reasons.append("mixed_language_fastpath_block")
            if risk_level in {"medium", "high"}:
                blocked_fast = True
                policy_override_reasons.append("risk_fastpath_block")
            if blocked_fast:
                selected_route = "deep_research" if grounding_need == "web" else "document_pipeline" if grounding_need == "docs" else "standard_answer"
                route_reason = "policy_override_fastpath_blocked"

        candidate_routes = self._candidate_routes(
            primary_intent=primary_intent,
            context_required=context_required,
            grounding_need=grounding_need,
            query_kind=query_kind,
        )

        verification_status = "passed"
        rerouted = False
        if selected_route == "standard_answer" and grounding_need == "web":
            selected_route = "deep_research"
            rerouted = True
            verification_status = "rerouted"
            route_reason = "verification_grounding_upgrade"
        elif selected_route == "standard_answer" and grounding_need == "docs":
            selected_route = "document_pipeline"
            rerouted = True
            verification_status = "rerouted"
            route_reason = "verification_doc_upgrade"
        elif selected_route == "standard_fsm_task" and context_required and not has_context:
            selected_route = "clarification"
            rerouted = True
            verification_status = "rerouted"
            route_reason = "verification_missing_context"

        return {
            "candidate_routes": candidate_routes,
            "selected_route": selected_route,
            "route_reason": route_reason,
            "policy_reason": route_reason,
            "verification_status": verification_status,
            "rerouted": rerouted,
            "policy_override_reasons": policy_override_reasons,
            "blocked_fastpath": blocked_fast,
        }

    def build_envelope(
        self,
        *,
        raw_query: str,
        classification: ClassificationResult,
        rewrite: RewriteResult,
        has_context: bool,
        has_active_doc: bool,
    ) -> Dict[str, Any]:
        normalized_query = self.normalize_query(raw_query)
        rewritten_query = rewrite.rewritten if rewrite.was_modified else normalized_query
        language_profile = self.detect_language_profile(raw_query, normalized_query)
        routing_profile = self.build_routing_profile(
            raw_query=raw_query,
            normalized_query=normalized_query,
            rewritten_query=rewritten_query,
            classification=classification,
            has_context=has_context,
            has_active_doc=has_active_doc,
            language_profile=language_profile,
        )
        route_decision = self.select_and_verify_route(
            routing_profile=routing_profile,
            language_profile=language_profile,
            has_context=has_context,
            has_active_doc=has_active_doc,
        )
        return {
            "raw_query": str(raw_query or "").strip(),
            "normalized_query": normalized_query,
            "rewritten_query": rewritten_query,
            "rewrite_reason": str(rewrite.reason or ""),
            "language_profile": language_profile,
            "routing_profile": routing_profile,
            "route_decision": route_decision,
        }

    def _candidate_routes(
        self,
        *,
        primary_intent: str,
        context_required: bool,
        grounding_need: str,
        query_kind: str = "general",
    ) -> List[str]:
        routes: List[str] = []
        if str(query_kind or "").strip().lower() == "entity_lookup":
            routes.extend(["entity_lookup", "deep_research"])
        elif grounding_need == "docs":
            routes.extend(["document_pipeline", "standard_answer"])
        elif grounding_need == "web":
            routes.extend(["deep_research", "standard_answer"])
        elif primary_intent == "small_talk":
            routes.extend(["micro_fast", "standard_answer"])
        elif primary_intent in {"transform", "summarize"}:
            routes.extend(["transform_pipeline", "standard_answer"])
        elif primary_intent in {"debug"}:
            routes.extend(["standard_fsm_task", "standard_answer"])
        elif primary_intent in {"task_execution", "comparison"}:
            routes.extend(["standard_answer", "standard_fsm_task"])
        else:
            routes.extend(["standard_answer", "standard_fsm_task"])

        if context_required:
            routes.append("clarification")
        deduped: List[str] = []
        for route in routes:
            if route not in deduped:
                deduped.append(route)
        return deduped[:4]

    def _estimate_typo_density(self, text: str) -> float:
        words = re.findall(r"[a-zA-Z]+", str(text or "").lower())
        if not words:
            return 0.0
        suspicious = 0
        for word in words:
            if len(word) < 4:
                continue
            if re.search(r"(.)\1\1", word):
                suspicious += 1
                continue
            if not re.search(r"[aeiou]", word):
                suspicious += 1
                continue
            if re.search(r"[^a-z]", word):
                suspicious += 1
        return suspicious / max(1, len(words))
