"""
TAOS Semantic Layer — Intent Classification.

Classifies user queries into intent types using deterministic
overrides + LLM fallback. Critical PRD §3 feature.

Intent Types:
- simple_lookup: Quick fact retrieval
- definition: "What is X?" queries
- task: Complex multi-step tasks
- research: Deep investigation
- news: Latest information queries
- comparison: "X vs Y" queries
- transform: Follow-up transformations ("summarize this", "shorter version")
"""

from __future__ import annotations

import re
import httpx
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from taos.config.settings import get_settings
from taos.config.model_config import ModelOrchestration


class IntentType(str, Enum):
    """All recognized intent types (PRD §3)."""
    SIMPLE_LOOKUP = "simple_lookup"
    DEFINITION = "definition"
    TASK = "task"
    RESEARCH = "research"
    NEWS = "news"
    COMPARISON = "comparison"
    TRANSFORM = "transform"
    DEBUG = "debug"


class DomainType(str, Enum):
    """Domain types for query routing (PRD §3)."""
    AI = "ai"
    PROGRAMMING = "programming"
    STARTUP = "startup"
    GENERAL = "general"


@dataclass
class ClassificationResult:
    """Result of intent + domain classification."""

    intent: IntentType
    domain: DomainType
    confidence: float = 1.0
    is_deterministic: bool = False
    is_followup: bool = False
    rewritten_query: Optional[str] = None
    suggested_mode: str = "standard"  # "fast" | "standard" | "deep"
    metadata: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# DETERMINISTIC OVERRIDE RULES (PRD §3)
# ═══════════════════════════════════════════════════════════

_PERSONAL_SUPPORT_HINTS = re.compile(
    r"\b("
    r"break\s*up|breakup|broke\s+up|heart\s*break|heartbreak|"
    r"feeling\s+sad|feel\s+sad|feel\s+bad|feeling\s+low|"
    r"depressed|lonely|crying|anxious|stress(?:ed)?|"
    r"my\s+girlfriend|my\s+boyfriend|relationship|got\s+dumped|"
    r"she\s+left\s+me|he\s+left\s+me"
    r")\b",
    re.I,
)

_DETERMINISTIC_OVERRIDES: List[Tuple[re.Pattern, IntentType, DomainType]] = [
    # Small-talk / greetings (force fast path, no over-explaining)
    (
        re.compile(
            r"^\s*(?:hi+|hey+|hello+|heya|yo+|sup)(?:[\s,!.]+(?!(?:what|why|when|where|who|which|how|can|could|would|please)\b)[a-z']+){0,2}\s*$",
            re.I,
        ),
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),
    (
        re.compile(
            r"^\s*(?:hi+|hey+|hello+|heya|yo+)?[\s,!.]*(?:how\s*(?:are|r)\s*(?:you|u)(?:\s*(?:doing|doin|today))?|how'?s\s+it\s+going)\s*[!?]*\s*$",
            re.I,
        ),
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),
    (
        re.compile(
            r"^\s*(?:epadi|eppadi|epdi|eppdi)\s+"
            r"(?:ir+u?k+a|ir+u?k+inga|ir+u?keenga|ir+u?kiya|ir+u?kkiya)"
            r"(?:\s+(?:da|dei|bro|macha|machi))?\s*[!?]*\s*$",
            re.I,
        ),
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),
    # Conversational lifestyle advice should stay fast + direct (not full task orchestration).
    (
        re.compile(
            r"^\s*(?:i\s+need\s+to|i\s+want\s+to|what\s+should\s+i|which\s+\w+\s+should\s+i|can\s+i)\b"
            r".*\b(drink|eat|sleep|coffee|tea|night|morning|diet)\b.*\??\s*$",
            re.I,
        ),
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),
    (
        _PERSONAL_SUPPORT_HINTS,
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),
    (
        re.compile(r"^\s*(?:thanks|thank\s+you|thx)\s*[!.]*\s*$", re.I),
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),
    (
        re.compile(
            r"^\s*(?:(?:macha|machi|bro|buddy|dude)\s+)?"
            r"(?:saptiya|saptaya|saaptiya|saptingla|enna\s+(?:panra|pandra)|s(?:e|a)ri\s+da|polaama|polama)"
            r"\s*[!?]*\s*$",
            re.I,
        ),
        IntentType.SIMPLE_LOOKUP,
        DomainType.GENERAL,
    ),

    # News queries
    (re.compile(r"\b(official\s+statement|press\s+release|government\s+statement|court\s+order)\b", re.I),
     IntentType.NEWS, DomainType.GENERAL),
    (re.compile(r"\b(latest|recent|new|breaking|today'?s?|this week|announce)\b.*\b(ai|model|gpt|llm|release|update)\b", re.I),
     IntentType.NEWS, DomainType.AI),
    (re.compile(r"\b(latest|recent|new|breaking)\b.*\b(news|update|release|announcement)\b", re.I),
     IntentType.NEWS, DomainType.GENERAL),
    (re.compile(r"^\s*legal\s+ah\s*[!?]*\s*$", re.I),
     IntentType.TASK, DomainType.GENERAL),

    # Definitions
    (re.compile(r"^(what\s+is|what\s+are|define|explain|what\s+does)\b", re.I),
     IntentType.DEFINITION, DomainType.GENERAL),

    # Comparisons
    (re.compile(r"\b(vs\.?|versus|compared?\s+to|difference\s+between|better\s+than)\b", re.I),
     IntentType.COMPARISON, DomainType.GENERAL),

    # Transform/follow-up
    (re.compile(r"^(summarize|shorten|simplify|rewrite|translate|rephrase|give\s+timeline|short\s+version|make\s+it|give\s+only|key\s+points)\b", re.I),
     IntentType.TRANSFORM, DomainType.GENERAL),
    (re.compile(r"^(summarize\s+this|give\s+me\s+a\s+summary|shorter\s+version|bullet\s+points)\b", re.I),
     IntentType.TRANSFORM, DomainType.GENERAL),

    # Debug / Fix
    (re.compile(r"\b(fix|error|stacktrace|bug|fail|broken|troubleshoot)\b", re.I),
     IntentType.DEBUG, DomainType.PROGRAMMING),

    # Simple lookups
    (re.compile(r"^(what\s+version|current\s+version|how\s+old|how\s+many|when\s+was|who\s+is|who\s+was)\b", re.I),
     IntentType.SIMPLE_LOOKUP, DomainType.GENERAL),
    (re.compile(r"\b(version\s+of|release\s+date|price\s+of)\b", re.I),
     IntentType.SIMPLE_LOOKUP, DomainType.GENERAL),
]

_DOMAIN_PATTERNS: List[Tuple[re.Pattern, DomainType]] = [
    (re.compile(r"\b(ai|artificial\s+intelligence|machine\s+learning|llm|gpt|neural\s+net|deep\s+learning|transformer|openai|anthropic|gemini|claude)\b", re.I),
     DomainType.AI),
    (re.compile(r"\b(python|javascript|rust|go|typescript|react|django|fastapi|code|programming|developer|api|github|docker|kubernetes|sql|database)\b", re.I),
     DomainType.PROGRAMMING),
    (re.compile(r"\b(startup|vc|venture\s+capital|funding|saas|b2b|market\s+fit|pitch|revenue|valuation|yc|y\s+combinator)\b", re.I),
     DomainType.STARTUP),
]

_RESEARCH_INDICATORS = re.compile(
    r"\b(research|reserch|reseach|reasearch|vresearch|investigate|analyze|deep\s+dive|comprehensive|detailed|thorough|explore|study)\b",
    re.I,
)
_RESEARCH_SOFT_HINTS = re.compile(
    r"\b("
    r"rumor|rumors|unreleased|verified\s+source|reliable\s+coverage|"
    r"ceasefire|conflict|reports?|coverage|timeline"
    r")\b",
    re.I,
)
_TASK_INDICATORS = re.compile(
    r"\b(create|build|write|generate|implement|develop|design|make|set\s+up|configure|deploy|run|execute|calculate|compute|install|setup|code|coding)\b", re.I
)
_DOC_MODE_HINTS = re.compile(
    r"\b("
    r"pdf|document|doc|notes|uploaded|upload|file|from this|"
    r"from uploaded notes|chapter|unit|"
    r"important questions|"
    r"(?:1|2|5|10|16)\s*mark"
    r")\b",
    re.I,
)
_FAST_MESSAGE_HINTS = re.compile(
    r"^\s*(?:h[iey]+|hey+|hello+|yo+|sup|thanks|thank\s+you|thx|ok(?:ay)?|k|nice|bye|good\s*night)(?:\s+(?:bro|da|macha|machi))?\s*[!?.,]*\s*$",
    re.I,
)
_CASUAL_STATUS_HINTS = re.compile(
    r"^(?:i\s*(?:am|'?m)\s*(?:good|fine|ok(?:ay)?|great)|all\s+good|doing\s+good|i\s+am\s+fine)\b",
    re.I,
)
_SERIOUS_SHORT_HINTS = re.compile(
    r"\b(latest|official|statement|election|legal|court|policy|regulation|press\s+release|fix|bug|error|news)\b",
    re.I,
)
_NON_CASUAL_SHORT_HINTS = re.compile(
    r"\b("
    r"react|python|java|javascript|typescript|node|api|sql|docker|kubernetes|"
    r"bug|fix|error|deploy|build|code|price|version|stock|gold|bitcoin|"
    r"research|analyze|compare|official|statement|policy|news|latest|current"
    r")\b",
    re.I,
)
_POLICY_DEEP_OVERRIDE_HINTS = re.compile(
    r"\b("
    r"latest|current|today|live|breaking|news|update|official\s+statement|"
    r"government\s+policy|company\s+announcement|press\s+release|policy\s+statement"
    r")\b",
    re.I,
)
_HIGH_STAKES_HINTS = re.compile(
    r"\b("
    r"medical|health|doctor|diagnosis|treatment|medicine|dosage|"
    r"legal|law|court|judgment|regulation|compliance|"
    r"financial|investment|stock|tax|policy|securities|incident|security"
    r")\b",
    re.I,
)
_DOC_FOLLOWUP_BOOST_HINTS = re.compile(
    r"^(?:next|continue|go on|go ahead|explain this|summarize this|this part|that part|continue this)\b",
    re.I,
)
_PROFILE_ENTITY_LOOKUP_HINTS = re.compile(
    r"\b("
    r"ceo|founder|linkedin|profile|biography|bio|leadership|board\s+member|"
    r"chairman|chairperson|director|coo|cto|cfo"
    r")\b",
    re.I,
)
_PROFILE_ENTITY_QUERY_SHAPE_HINTS = re.compile(
    r"\b("
    r"who\s+is|who\s+was|tell\s+me|details|background|career|history|"
    r"current|official|company|organization|org|of"
    r")\b",
    re.I,
)
_TAMIL_SMALL_TALK_HINTS = re.compile(
    r"^(?:"
    r"(?:(?:macha|machi|bro|buddy|dude)\s+)?(?:epadi|eppadi|epdi|eppdi)\s+(?:ir+u?k+a|ir+u?k+inga|ir+u?keenga|ir+u?kiya|ir+u?kkiya)(?:\s+(?:da|dei|bro|macha|machi))?"
    r"|(?:(?:macha|machi|bro|buddy|dude)\s+)?(?:saptiya|saptaya|saaptiya|saptingla)"
    r"|(?:(?:macha|machi|bro|buddy|dude)\s+)?enna\s+(?:panra|pandra)"
    r"|(?:(?:macha|machi|bro|buddy|dude)\s+)?s(?:e|a)ri\s+da"
    r"|(?:(?:macha|machi|bro|buddy|dude)\s+)?(?:polaama|polama)"
    r")\s*[!?]*$",
    re.I,
)
_TAMIL_TRANSLIT_HINTS = re.compile(
    r"\b("
    r"macha|machi|idha|idhu|pannuda|pannu|sollu|solra|venum|"
    r"apdi|ippo|inga|ingae|epadi|epdi|enna|iruka|irukka|irukeenga|"
    r"saptiya|saptaya|saaptiya|pannalama|pannunga|simple\s+ah"
    r")\b",
    re.I,
)
_DANGEROUS_FASTPATH_PATTERNS = (
    "confirm even if",
    "just tell me it's true",
    "just tell me its true",
    "just tell me it's confirmed",
    "just tell me its confirmed",
    "even if not",
    "force answer",
    "no uncertainty",
    "don't show uncertainty",
    "dont show uncertainty",
)
_ACK_WITH_TRAILING_CONTENT = re.compile(
    r"^\s*(?:ok(?:ay)?|k|nice|thanks|thank\s+you|thx)\b\s+.+$",
    re.I,
)
_MINIMAL_PROGRESS_FOLLOWUP = re.compile(
    r"^\s*(?:next|continue|go on|keep going|go ahead)"
    r"(?:\s+(?:please|pls|da|bro|macha|machi))?\s*[.!?]*\s*$",
    re.I,
)
_CONTEXT_ANCHOR_PROMPT = re.compile(r"^\s*previous\s+turn\s+requested\b", re.I)


class IntentClassifier:
    """
    Production intent classifier (PRD §3).

    Uses deterministic regex overrides first, then falls back
    to heuristic scoring. LLM fallback can be added later.
    """
    def __init__(self) -> None:
        self._settings = get_settings()
        # Phase 107 keeps first-pass routing deterministic. This config is now
        # only for legacy/explicit semantic fallback paths, not the default gate.
        self._model_config = ModelOrchestration().get_config("executor")

    def classify(
        self,
        query: str,
        has_context: bool = False,
        has_active_doc: bool = False,
    ) -> ClassificationResult:
        """
        Synchronous classifier for deterministic/heuristic flows.

        Kept sync for compatibility with existing tests and call sites that do
        not run in an async context.
        """
        query = query.strip()
        normalized_query = " ".join(query.lower().split())
        heuristic_route = self._heuristic_route_gate(normalized_query)
        route_label = heuristic_route or "standard_task"
        route_source = "heuristic" if heuristic_route else "fallback"
        route_confidence = 1.0 if heuristic_route else 0.5
        if has_context and _MINIMAL_PROGRESS_FOLLOWUP.fullmatch(normalized_query):
            route_label = "fast_message"
            route_source = "heuristic_followup_fast"
            route_confidence = 0.92

        route_label, overridden, policy_meta = self._apply_policy_overrides(
            normalized_query,
            route_label,
            has_active_doc=has_active_doc,
        )
        if overridden:
            route_source = "override"
            route_confidence = 1.0

        is_contextual_followup = has_context and self._is_followup(query, has_context)
        deterministic = self._try_deterministic(query)
        intent = self._intent_from_route_label(query=query, route_label=route_label, deterministic=deterministic)
        domain = self._refine_domain(query, deterministic.domain) if deterministic else self._detect_domain(query)
        base_confidence = float(deterministic.confidence) if deterministic else 0.5
        result = ClassificationResult(
            intent=intent,
            domain=domain,
            confidence=max(route_confidence, base_confidence),
            is_deterministic=bool(deterministic),
            is_followup=is_contextual_followup,
            suggested_mode=self._select_mode(intent),
        )
        if (result.intent == IntentType.TRANSFORM and has_context) or is_contextual_followup:
            result.is_followup = True
            result.suggested_mode = "fast"
        self._attach_route_metadata(
            result,
            query,
            route_source=route_source,
            route_label_override=route_label,
            route_confidence_override=route_confidence,
        )
        if policy_meta:
            result.metadata.update(policy_meta)
        result.metadata["doc_context_active"] = bool(has_active_doc)
        return result

    async def classify_async(
        self,
        query: str,
        has_context: bool = False,
        has_active_doc: bool = False,
    ) -> ClassificationResult:
        """
        Classify a user query into intent + domain.

        Args:
            query: The raw user query.
            has_context: Whether previous context exists (for follow-up detection).

        Returns:
            ClassificationResult with intent, domain, confidence, and metadata.
        """
        query = query.strip()
        normalized_query = " ".join(query.lower().split())
        is_contextual_followup = has_context and self._is_followup(query, has_context)
        deterministic = self._try_deterministic(query)

        # Step 1: Heuristic gate
        route_label = self._heuristic_route_gate(normalized_query)
        route_source = "heuristic" if route_label else "fallback"
        route_confidence = 1.0 if route_label else 0.0
        if has_context and _MINIMAL_PROGRESS_FOLLOWUP.fullmatch(normalized_query):
            route_label = "fast_message"
            route_source = "heuristic_followup_fast"
            route_confidence = 0.92

        # Step 2: Semantic router only when needed
        if route_label is None:
            llm_route_label, llm_route_confidence = await self._llm_route_label(query)
            if llm_route_label:
                if llm_route_label == "fast_message" and float(llm_route_confidence or 0.0) < 0.55:
                    llm_route_label = None
                elif llm_route_label == "fast_message" and self._should_guard_fast_message_route(normalized_query):
                    guarded_route = self._heuristic_route_gate(normalized_query)
                    if guarded_route is None:
                        if self._looks_like_doc_mode(normalized_query):
                            guarded_route = "doc_mode"
                        elif self._looks_like_deep_research(normalized_query) or _RESEARCH_SOFT_HINTS.search(normalized_query):
                            guarded_route = "deep_research"
                        else:
                            guarded_route = "standard_task"
                    route_label = guarded_route
                    route_source = "semantic_guard"
                    route_confidence = max(0.75, float(llm_route_confidence or 0.0))
                else:
                    route_label = llm_route_label
                    route_source = "semantic_router"
                    route_confidence = llm_route_confidence

        # Fallback route
        if route_label is None:
            route_label = "standard_task"
            route_source = "fallback"
            route_confidence = 0.5

        # Step 3: Policy overrides
        route_label, overridden, policy_meta = self._apply_policy_overrides(
            normalized_query,
            route_label,
            has_active_doc=has_active_doc,
        )
        if overridden:
            route_source = "override"
            route_confidence = 1.0

        # Step 4: Map route -> intent and execute path metadata
        intent = self._intent_from_route_label(query=query, route_label=route_label, deterministic=deterministic)
        domain = self._refine_domain(query, deterministic.domain) if deterministic else self._detect_domain(query)
        base_confidence = float(deterministic.confidence) if deterministic else 0.5
        result = ClassificationResult(
            intent=intent,
            domain=domain,
            confidence=max(base_confidence, max(0.0, min(1.0, float(route_confidence or 0.0)))),
            is_deterministic=bool(deterministic),
            is_followup=is_contextual_followup,
            suggested_mode=self._select_mode(intent),
        )
        if (result.intent == IntentType.TRANSFORM and has_context) or is_contextual_followup:
            result.is_followup = True
            result.suggested_mode = "fast"
        self._attach_route_metadata(
            result,
            query,
            route_source=route_source,
            route_label_override=route_label,
            route_confidence_override=route_confidence,
        )
        if policy_meta:
            result.metadata.update(policy_meta)
        result.metadata["doc_context_active"] = bool(has_active_doc)
        return result

    async def _llm_classify(self, query: str) -> tuple[Optional[IntentType], float, bool]:
        """Ask the LLM to cross-lingually identify the raw intent structure."""
        prompt = f"""You are the Intent Classification Engine.
Identify the user's intent purely based on meaning, ignoring the language (e.g. 'Docker na enna' is Tamil for 'What is Docker', which is a DEFINITION).
Also determine if the query relies on prior conversational context (e.g. uses pronouns like 'it', 'them', or says 'compare this with...').

Valid Intents:
- simple_lookup (fact retrieval, version checks)
- definition ("what is", "meaning of")
- debug ("fix", "error", "failed")
- comparison ("vs", "compare")
- research ("latest", "trends", "analysis")
- task ("build", "create", "suggest")
- transform ("summarize", "make shorter")

Query: "{query}"

Output valid JSON strictly matching: {{"intent": "definition", "confidence": 0.95, "is_followup": false}}"""

        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self._model_config.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            **self._model_config.to_api_params()
        }
        
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload
                )
                resp.raise_for_status()
                data = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(data)
                
                intent_str = parsed.get("intent", "unknown")
                conf = float(parsed.get("confidence", 0.0))
                followup = bool(parsed.get("is_followup", False))
                
                # Verify Enum membership
                try:
                    return IntentType(intent_str.lower()), conf, followup
                except ValueError:
                    return None, 0.0, False
                    
        except Exception:
            return None, 0.0, False

    async def _llm_route_label(self, query: str) -> tuple[Optional[str], float]:
        """
        Multilingual route classifier fallback.

        Returns one of:
        - fast_message
        - standard_task
        - deep_research
        - doc_mode
        """
        prompt = f"""Classify this user message into exactly one label:
- fast_message
- standard_task
- deep_research
- doc_mode

Rules:
- fast_message: greeting, small talk, thanks, acknowledgement, casual check-in in any language/transliteration.
- standard_task: general requests, coding, explanations, execution help.
- deep_research: latest/current/live/news/status/comparison needing source-grounded research.
- doc_mode: asks about uploaded files, notes, PDFs, chapters, units, document-grounded Q&A.

Return strict JSON only:
{{"route_label": "fast_message", "confidence": 0.91}}

Message: "{query}" """
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model_config.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            **self._model_config.to_api_params(),
        }
        valid = {"fast_message", "standard_task", "deep_research", "doc_mode"}
        try:
            async with httpx.AsyncClient(timeout=1.2) as client:
                resp = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(data)
                label = str(parsed.get("route_label", "")).strip().lower()
                conf = float(parsed.get("confidence", 0.0) or 0.0)
                min_conf = 0.55 if label == "fast_message" else 0.35
                if label in valid and conf >= min_conf:
                    return label, conf
        except Exception:
            pass
        return None, 0.0

    def _try_deterministic(self, query: str) -> Optional[ClassificationResult]:
        """Try deterministic regex overrides."""
        for pattern, intent, domain in _DETERMINISTIC_OVERRIDES:
            if pattern.search(query):
                return ClassificationResult(
                    intent=intent,
                    domain=domain,
                    confidence=1.0,
                    is_deterministic=True,
                )
        return None

    def _heuristic_classify(self, query: str, llm_intent: Optional[IntentType] = None) -> IntentType:
        """Heuristic intent classification acting as Hard Fallback Engine."""
        goal = query.lower()

        if "vs" in goal or "compare" in goal:
            return IntentType.COMPARISON
        elif "fix" in goal or "error" in goal or "not working" in goal:
            return IntentType.DEBUG
        elif _RESEARCH_INDICATORS.search(goal) or "latest" in goal or "trend" in goal:
            return IntentType.RESEARCH
        elif _TASK_INDICATORS.search(goal) or "find" in goal or "suggest" in goal or "analyze" in goal:
            return IntentType.TASK
        elif "concise" in goal or "summary" in goal:
            return IntentType.TRANSFORM
        else:
            return IntentType.DEFINITION

    def _detect_domain(self, query: str) -> DomainType:
        """Detect the query domain."""
        for pattern, domain in _DOMAIN_PATTERNS:
            if pattern.search(query):
                return domain
        return DomainType.GENERAL

    def _refine_domain(self, query: str, current: DomainType) -> DomainType:
        """Refine domain if generic was returned."""
        if current != DomainType.GENERAL:
            return current
        return self._detect_domain(query)

    def _is_followup(self, query: str, has_context: bool) -> bool:
        """Detect if query is a follow-up to previous context."""
        if not has_context:
            return False

        followup_patterns = [
            r"^(this|that|it|these|those)\b",
            r"^(also|and|but|however|additionally)\b",
            r"^(can you|could you|please)\s+(also|now|then)\b",
            r"\b(the\s+above|the\s+previous|the\s+same|mentioned)\b",
            r"^(yes|no|ok|sure|go ahead)\b",
            r"^(next|continue|go on|keep going)\b",
            r"^(make\s+it|give\s+only|give\s+me|shorten|simplify|summarize)\b",
            r"^(compare\s+it|explain\s+it|translate\s+it)\b",
        ]
        for pattern in followup_patterns:
            if re.search(pattern, query, re.I):
                return True
        return False

    def _select_mode(self, intent: IntentType) -> str:
        """Select execution mode based on intent."""
        mode_map = {
            IntentType.SIMPLE_LOOKUP: "fast",
            IntentType.DEFINITION: "fast",
            IntentType.TRANSFORM: "fast",
            IntentType.NEWS: "standard",
            IntentType.COMPARISON: "standard",
            IntentType.TASK: "standard",
            IntentType.RESEARCH: "deep",
        }
        return mode_map.get(intent, "standard")

    def _attach_route_metadata(
        self,
        result: ClassificationResult,
        query: str,
        route_source: str,
        route_label_override: Optional[str] = None,
        route_confidence_override: Optional[float] = None,
    ) -> None:
        route_label = route_label_override or self._route_label(query=query, intent=result.intent)
        result.metadata["route_label"] = route_label
        result.metadata["route_source"] = route_source
        route_conf = (
            float(route_confidence_override)
            if route_confidence_override is not None
            else float(result.confidence or 0.0)
        )
        result.metadata["route_confidence"] = round(route_conf, 3)
        result.metadata["router"] = "hybrid_v1"

    def _route_label(self, query: str, intent: IntentType) -> str:
        normalized = " ".join((query or "").strip().lower().split())
        if self._looks_like_doc_mode(normalized):
            return "doc_mode"
        if self._looks_like_fast_message(normalized):
            return "fast_message"
        if intent in {IntentType.RESEARCH, IntentType.NEWS}:
            return "deep_research"
        return "standard_task"

    def _looks_like_doc_mode(self, normalized_query: str) -> bool:
        if not normalized_query:
            return False
        return bool(_DOC_MODE_HINTS.search(normalized_query))

    def _looks_like_fast_message(self, normalized_query: str) -> bool:
        if not normalized_query:
            return False
        if _SERIOUS_SHORT_HINTS.search(normalized_query):
            return False
        if _ACK_WITH_TRAILING_CONTENT.search(normalized_query):
            return False
        token_count = len(normalized_query.split())
        # Explicit buddy-prefix casual phrasing, e.g. "macha epadi irruka"
        buddy_prefix = re.match(r"^(?:macha|machi|bro|buddy|dude)\b", normalized_query)
        if buddy_prefix and token_count <= 8:
            remainder = normalized_query[buddy_prefix.end():].strip(" ,.!?")
            if remainder and (
                _TAMIL_SMALL_TALK_HINTS.search(remainder)
                or re.search(r"\bhow\s*(are|r)\s*(you|u)(\s*(doing|doin|today))?\b", remainder)
            ):
                return True
        if token_count <= 8 and _FAST_MESSAGE_HINTS.search(normalized_query):
            return True
        if token_count <= 8 and _CASUAL_STATUS_HINTS.search(normalized_query):
            return True
        return bool(_TAMIL_SMALL_TALK_HINTS.search(normalized_query))

    def _looks_like_deep_research(self, normalized_query: str) -> bool:
        if not normalized_query:
            return False
        if _PERSONAL_SUPPORT_HINTS.search(normalized_query):
            return False
        if _RESEARCH_INDICATORS.search(normalized_query):
            return True
        if _PROFILE_ENTITY_LOOKUP_HINTS.search(normalized_query) and _PROFILE_ENTITY_QUERY_SHAPE_HINTS.search(normalized_query):
            return True
        if re.search(r"\b(version\s+of|what\s+version|price\s+of|how\s+many|what\s+is|who\s+is)\b", normalized_query):
            return False
        if "compare" in normalized_query and not re.search(
            r"\b(latest|current|today|live|breaking|status|news|official statement|press release|as of|timeline)\b",
            normalized_query,
        ):
            return False
        return bool(
            re.search(
                r"\b(latest|current|today|live|breaking|status|news|official statement|press release|as of|timeline)\b",
                normalized_query,
            )
        )

    def _heuristic_route_gate(self, normalized_query: str) -> Optional[str]:
        if not normalized_query:
            return None
        if _CONTEXT_ANCHOR_PROMPT.search(normalized_query):
            return "fast_message"
        if self._looks_like_doc_mode(normalized_query):
            return "doc_mode"
        if _PERSONAL_SUPPORT_HINTS.search(normalized_query):
            return "standard_task"
        if self._looks_like_deep_research(normalized_query):
            return "deep_research"
        if self._contains_tamil_transliteration(normalized_query):
            if self._looks_like_fast_message(normalized_query):
                return "fast_message"
            return "standard_task"
        if self._looks_like_fast_message(normalized_query):
            return "fast_message"
        if _TASK_INDICATORS.search(normalized_query):
            return "standard_task"
        if self._looks_like_ultra_short_casual_message(normalized_query):
            return "fast_message"
        return None

    def _contains_tamil_transliteration(self, normalized_query: str) -> bool:
        if not normalized_query:
            return False
        matches = list(_TAMIL_TRANSLIT_HINTS.finditer(normalized_query))
        if len(matches) >= 2:
            return True
        return bool(
            matches
            and re.search(r"\b(explain|summarize|short|simple|topic|key points|details|tell)\b", normalized_query)
        )

    def _looks_like_ultra_short_casual_message(self, normalized_query: str) -> bool:
        """Language-agnostic fallback for very short casual messages."""
        if not normalized_query:
            return False
        token_count = len(normalized_query.split())
        # Keep this very strict to avoid hijacking short imperative queries like
        # "explain this" / "summarize this".
        if token_count != 1:
            return False
        if any(ch.isdigit() for ch in normalized_query):
            return False
        if _DOC_MODE_HINTS.search(normalized_query):
            return False
        if _POLICY_DEEP_OVERRIDE_HINTS.search(normalized_query):
            return False
        if _SERIOUS_SHORT_HINTS.search(normalized_query):
            return False
        if _NON_CASUAL_SHORT_HINTS.search(normalized_query):
            return False
        return True

    def _should_guard_fast_message_route(self, normalized_query: str) -> bool:
        """
        Protect against LLM router false positives where non-casual prompts
        are mislabeled as fast_message.
        """
        if not normalized_query:
            return False
        if self._looks_like_fast_message(normalized_query):
            return False
        if self._looks_like_doc_mode(normalized_query):
            return True
        if self._looks_like_deep_research(normalized_query):
            return True
        if _RESEARCH_SOFT_HINTS.search(normalized_query):
            return True
        if _TASK_INDICATORS.search(normalized_query):
            return True
        if _NON_CASUAL_SHORT_HINTS.search(normalized_query):
            return True
        token_count = len(normalized_query.split())
        if token_count >= 4 and re.search(
            r"\b(write|code|build|create|generate|explain|summarize|compare|analyze|find|give)\b",
            normalized_query,
        ):
            return True
        return False

    def _apply_policy_overrides(
        self,
        normalized_query: str,
        route_label: str,
        *,
        has_active_doc: bool = False,
    ) -> tuple[str, bool, Dict[str, Any]]:
        label = str(route_label or "standard_task")
        override_reasons: List[str] = []
        simple_lookup_like = bool(
            re.search(r"\b(version\s+of|what\s+version|price\s+of|how\s+many|what\s+is|who\s+is)\b", normalized_query)
        )
        if self._has_dangerous_fastpath_pattern(normalized_query):
            label = "standard_task"
            override_reasons.append("adversarial_fastpath_guard")
            careful_mode = bool(_HIGH_STAKES_HINTS.search(normalized_query))
            return label, True, {
                "careful_mode": careful_mode,
                "policy_override_reasons": override_reasons,
            }
        if _PERSONAL_SUPPORT_HINTS.search(normalized_query):
            label = "standard_task"
            override_reasons.append("personal_support_no_research")
        if self._contains_tamil_transliteration(normalized_query) and label not in {"fast_message", "doc_mode", "deep_research"}:
            label = "standard_task"
            override_reasons.append("tamil_translit_boost")
        if (
            _PROFILE_ENTITY_LOOKUP_HINTS.search(normalized_query)
            and _PROFILE_ENTITY_QUERY_SHAPE_HINTS.search(normalized_query)
            and label != "doc_mode"
            and label != "entity_lookup"
        ):
            label = "entity_lookup"
            override_reasons.append("profile_entity_lookup")

        if self._looks_like_doc_mode(normalized_query) and label != "doc_mode":
            label = "doc_mode"
            override_reasons.append("doc_context")
        elif _CONTEXT_ANCHOR_PROMPT.search(normalized_query):
            # Prevent synthetic context-anchor text from escalating to deep research.
            label = "standard_task"
            override_reasons.append("context_anchor_standard")
        elif (
            (not simple_lookup_like)
            and not _PERSONAL_SUPPORT_HINTS.search(normalized_query)
            and _POLICY_DEEP_OVERRIDE_HINTS.search(normalized_query)
            and label not in {"deep_research", "entity_lookup"}
        ):
            label = "deep_research"
            override_reasons.append("freshness_or_official")
        elif has_active_doc and label in {"standard_task", "fast_message"} and _DOC_FOLLOWUP_BOOST_HINTS.search(normalized_query):
            label = "doc_mode"
            override_reasons.append("active_doc_followup_boost")
        elif has_active_doc and label == "standard_task":
            # Soft doc boost: only for ambiguous tasks when a chat has active docs.
            label = "doc_mode"
            override_reasons.append("active_doc_context_boost")

        careful_mode = bool(_HIGH_STAKES_HINTS.search(normalized_query))
        metadata: Dict[str, Any] = {
            "careful_mode": careful_mode,
            "policy_override_reasons": override_reasons,
        }
        return label, bool(override_reasons), metadata

    def _has_dangerous_fastpath_pattern(self, normalized_query: str) -> bool:
        lowered = str(normalized_query or "").lower()
        if not lowered:
            return False
        return any(pattern in lowered for pattern in _DANGEROUS_FASTPATH_PATTERNS)

    def _intent_from_route_label(
        self,
        *,
        query: str,
        route_label: str,
        deterministic: Optional[ClassificationResult] = None,
    ) -> IntentType:
        label = str(route_label or "").strip().lower()
        if label == "fast_message":
            return IntentType.SIMPLE_LOOKUP
        if label == "entity_lookup":
            return IntentType.RESEARCH
        if label == "no_search":
            if deterministic:
                return deterministic.intent
            return IntentType.DEFINITION
        if label == "deep_research":
            normalized = " ".join((query or "").lower().split())
            profile_lookup = bool(
                _PROFILE_ENTITY_LOOKUP_HINTS.search(normalized)
                and _PROFILE_ENTITY_QUERY_SHAPE_HINTS.search(normalized)
            )
            freshness_news = bool(_POLICY_DEEP_OVERRIDE_HINTS.search(normalized))
            if profile_lookup:
                return IntentType.RESEARCH
            if _RESEARCH_INDICATORS.search(normalized):
                return IntentType.RESEARCH
            if freshness_news:
                return IntentType.NEWS
            return IntentType.RESEARCH
        if label == "doc_mode":
            return IntentType.TASK
        if label == "standard_task":
            if deterministic and deterministic.intent in {
                IntentType.SIMPLE_LOOKUP,
                IntentType.DEFINITION,
                IntentType.TRANSFORM,
                IntentType.DEBUG,
                IntentType.COMPARISON,
                IntentType.TASK,
            }:
                return deterministic.intent
            return IntentType.TASK
        if deterministic:
            return deterministic.intent
        return self._heuristic_classify(query, None)
