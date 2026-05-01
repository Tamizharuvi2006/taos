"""
TAOS Fast Path Engine — Bypass full pipeline for simple queries.

Handles simple_lookup, definition, and cached queries instantly
without tool calls, DAG execution, or reflection.

PRD §4: Fast Path Engine (NEW)

Behavior:
- Bypass DAG
- No tool calls
- Instant response from LLM or cache
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from taos.core.semantic.intent_classifier import IntentType, ClassificationResult
from taos.config.settings import get_settings


@dataclass
class FastPathResult:
    """Result from fast path execution."""

    was_handled: bool = False
    result: Optional[str] = None
    confidence: float = 0.0
    source: str = ""  # "cache" | "llm" | "skip"
    latency_ms: float = 0.0
    cost: float = 0.0


class FastPathCache:
    """
    Simple in-memory cache for fast path responses.

    Uses TTL-based expiration and LRU-like eviction.
    """

    def __init__(self, capacity: int = 256, ttl_seconds: int = 3600) -> None:
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[str]:
        """Get a cached response."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        # Check TTL
        if time.time() - entry["timestamp"] > self._ttl:
            del self._cache[key]
            self._misses += 1
            return None

        self._hits += 1
        entry["access_count"] = entry.get("access_count", 0) + 1
        return entry["value"]

    def put(self, key: str, value: str) -> None:
        """Cache a response."""
        if len(self._cache) >= self._capacity:
            self._evict_oldest()
        self._cache[key] = {
            "value": value,
            "timestamp": time.time(),
            "access_count": 1,
        }

    def _evict_oldest(self) -> None:
        """Evict the oldest entry."""
        if not self._cache:
            return
        oldest_key = min(self._cache, key=lambda k: self._cache[k]["timestamp"])
        del self._cache[oldest_key]

    @property
    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    def clear(self) -> None:
        self._cache.clear()


class FastPathEngine:
    """
    Production fast path engine (PRD §4).

    Determines if a query can be handled without the full
    PLAN → EXECUTE → REFLECT pipeline, and if so, handles it
    directly via cache or lightweight LLM call.

    Fast path is used for:
    - simple_lookup intents (version checks, basic facts)
    - definition intents ("What is X?")
    - transform intents with available context
    - cached queries (exact match from recent history)
    """

    # Intents eligible for fast path
    FAST_PATH_INTENTS = {
        IntentType.SIMPLE_LOOKUP,
        IntentType.DEFINITION,
        IntentType.TRANSFORM,
    }

    def __init__(self) -> None:
        self._cache = FastPathCache()
        self._settings = get_settings()

    def should_fast_path(self, classification: ClassificationResult, query: str = "") -> bool:
        """
        Determine if a query should use the fast path.

        Rules:
        1. Intent must be in FAST_PATH_INTENTS
        2. Confidence must be >= 0.8
        3. Transform intent requires existing context
        4. NEW: If query is "dynamic" (version, price, latest), do NOT fast-path via pure LLM.
        """
        # Always fast-path simple conversational small-talk.
        if self._small_talk_response(query):
            return True

        # Direct generation tasks (e.g., code/build/create artifacts) can use
        # a safe fast path when they do not require live tools.
        if classification.intent == IntentType.TASK and self._is_direct_generation_task(query):
            return True

        if classification.intent not in self.FAST_PATH_INTENTS:
            return False

        if classification.confidence < 0.8:
            return False

        # Transform requires context
        if classification.intent == IntentType.TRANSFORM:
            return classification.is_followup

        # 🚨 Dynamic Lookups (PRD Quality Fix) 🚨
        if classification.intent == IntentType.SIMPLE_LOOKUP:
            if self.requires_tools(query):
                return False  # Force standard pipeline with tools

        return True

    def requires_tools(self, query: str) -> bool:
        """Check if a query likely needs live search/tools (e.g. version, price)."""
        dynamic_keywords = [
            "version", "latest", "current", "price", "stock", "quote", "rate", "market",
            "update", "release", "announcement", "funding", "startup", "spot", "today",
            "silver", "gold", "diamond", "platinum", "oil", "bitcoin", "btc", "eth",
            "car price", "vehicle price", "on road price", "msrp",
            "ceo", "founder", "linkedin", "profile", "biography", "bio",
            "official profile", "company profile",
        ]
        q_lower = query.lower()
        return any(k in q_lower for k in dynamic_keywords)

    def get_fast_prompt(self, query: str, classification: ClassificationResult, context: Optional[str] = None) -> str:
        """Generate a direct prompt for the fast-path LLM."""
        q = query.strip().lower()
        minimal_progress = bool(re.fullmatch(r"(next|continue|go on|keep going|go ahead)(\s+(please|pls|da|bro|macha|machi))?[.!?]*", q))
        if q in {"hi", "hey", "hello", "yo", "how are you", "how r u"}:
            return (
                "Reply naturally in 1 short sentence to this casual greeting. "
                "Do not define the phrase. Keep it human, friendly, and slightly warm."
            )

        if "what can you do" in q or "tasks u can do" in q or "tasks you can do" in q:
            return (
                "Answer in plain English with a short capability list for an AI assistant. "
                "No jargon, no placeholders, no XML-like markers."
            )

        if (
            "what model" in q
            or "which model" in q
            or "model u r using" in q
            or "model you are using" in q
        ):
            return (
                "Answer in 1 sentence using this exact runtime config: "
                f"planner={self._settings.planner_model}, "
                f"executor={self._settings.executor_model}, "
                f"reflector={self._settings.reflection_model}. "
                "Do not mention GPT-3.5 unless it is explicitly in that config."
            )

        if classification.intent == IntentType.TRANSFORM or classification.is_followup:
            if context:
                if minimal_progress:
                    weak_context = bool(
                        re.search(
                            r"\b(no (specific )?(timeline|context)|could you (please )?share|no previous request|wasn.?t a previous request)\b",
                            context,
                            re.I,
                        )
                    )
                    if weak_context:
                        return (
                            "Continue with a concise timeline in 3 bullets using generic phases. "
                            "Include the word 'next' once in the final line."
                        )
                    return (
                        f"Context: Previous answer was:\n{context}\n\n"
                        "Task: Continue the same topic in 3 concise bullets. "
                        "Do not switch to a new topic. Keep it practical and useful."
                    )
                if classification.intent == IntentType.TRANSFORM:
                    return f"Context: Previous answer was:\n{context}\n\nTask: Perform the transformation '{query}' on that text. Return ONLY the transformed result. No preamble."
                else:
                    return f"Context: Previous answer was:\n{context}\n\nTask: Answer the user's new query '{query}' based on the previous context."
            else:
                if minimal_progress:
                    return (
                        "The user asked to continue ('next/continue') but no prior text is available. "
                        "Give a useful continuation template in 3 short bullets that the user can apply immediately. "
                        "Avoid apologies and avoid asking follow-up questions."
                    )
                return f"The user requested a transformation: '{query}', but no previous context was found. Apologize and ask for the text."
        
        if classification.intent == IntentType.DEFINITION:
            return (
                f"Provide a direct, natural 1-2 sentence definition for: '{query}'. "
                "Do not use placeholders like <X> and do not repeat the question. "
                "No extra explanation unless requested."
            )

        if classification.intent == IntentType.TASK and self._is_direct_generation_task(query):
            return (
                "Fulfill the user request directly and completely.\n"
                f"User request: {query}\n"
                "Rules:\n"
                "1) Do the task now, do not describe what should be done.\n"
                "2) If code is requested, return complete runnable code.\n"
                "3) Keep explanation minimal and only if needed.\n"
                "4) Do not ask unnecessary follow-up questions."
            )
        
        # Default for SIMPLE_LOOKUP
        return (
            f"Answer this query directly: '{query}'. "
            "Keep it short and natural. "
            "Only add extra explanation if the user explicitly asks for it."
        )

    async def try_fast_path(
        self,
        query: str,
        classification: ClassificationResult,
        context: Optional[str] = None,
    ) -> FastPathResult:
        """
        Attempt to handle a query via fast path.
        """
        start = time.time()
        q_norm = self._normalize_query(query)
        minimal_progress = bool(
            re.fullmatch(
                r"(next|continue|go on|keep going|go ahead)(\s+(please|pls|da|bro|macha|machi))?",
                q_norm,
            )
        )

        # Deterministic contextual follow-up handling for "next/continue" style prompts.
        # This avoids weak one-word LLM responses and keeps continuation useful.
        if minimal_progress:
            if context:
                context_snippet = " ".join(str(context).strip().split())
                if len(context_snippet) > 140:
                    context_snippet = context_snippet[:137].rstrip() + "..."
                return FastPathResult(
                    was_handled=True,
                    result=(
                        f"Continuing from earlier context: {context_snippet}\n\n"
                        "Next useful follow-ups\n"
                        "- Next: expand the same topic in 3 concise points.\n"
                        "- Next: separate what is clear vs what still needs verification.\n"
                        "- Next: convert this into a short summary or timeline."
                    ),
                    confidence=0.96,
                    source="small_talk_rule",
                    latency_ms=(time.time() - start) * 1000,
                    cost=0.0,
                )
            return FastPathResult(
                was_handled=True,
                result=(
                    "No prior context is available for 'next' yet.\n\n"
                    "Next useful follow-ups\n"
                    "- Share the topic/entity to continue from.\n"
                    "- Ask for a quick summary first, then say 'next'.\n"
                    "- Ask for timeline / key points / action items."
                ),
                confidence=0.92,
                source="small_talk_rule",
                latency_ms=(time.time() - start) * 1000,
                cost=0.0,
            )

        # Rule-based tiny-talk reply: zero tool/LLM latency path.
        small_talk = self._small_talk_response(query)
        if small_talk:
            return FastPathResult(
                was_handled=True,
                result=small_talk,
                confidence=0.99,
                source="small_talk_rule",
                latency_ms=(time.time() - start) * 1000,
                cost=0.0,
            )

        if not self.should_fast_path(classification, query):
            return FastPathResult(was_handled=False)

        # ─── Check cache ───
        cache_key = self._make_cache_key(query)
        cached = self._cache.get(cache_key)
        if cached:
            return FastPathResult(
                was_handled=True,
                result=cached,
                confidence=0.95,
                source="cache",
                latency_ms=(time.time() - start) * 1000,
                cost=0.0,
            )

        # We return was_handled=True but result=None to tell the engine 
        # to use its _run_fast_llm with our generated prompt.
        return FastPathResult(
            was_handled=True,
            result=None,
            confidence=0.90,
            source="llm_direct",
            latency_ms=(time.time() - start) * 1000,
        )

    def cache_result(self, query: str, result: str) -> None:
        """Cache a result for future fast path hits."""
        cache_key = self._make_cache_key(query)
        self._cache.put(cache_key, result)

    def _make_cache_key(self, query: str) -> str:
        """Generate a cache key from a query."""
        normalized = query.strip().lower()
        return hashlib.md5(normalized.encode()).hexdigest()

    @property
    def cache_stats(self) -> Dict[str, int]:
        return self._cache.stats

    def clear_cache(self) -> None:
        self._cache.clear()

    def micro_fast_response(self, query: str) -> Optional[str]:
        """
        Return an immediate zero-LLM response for obvious casual messages.

        This is intentionally rule-based and extremely cheap, and is safe to call
        from API edge routes before full orchestration startup.
        """
        return self._tiny_talk_response(query)

    def _tiny_talk_response(self, query: str) -> Optional[str]:
        """
        Strict tiny-talk responder for API-edge micro-fast mode.

        Intentionally excludes capability asks, factual asks, and profile/research cues.
        """
        q = self._normalize_query(query)
        if not q:
            return None

        # Allow direct "how are you" style greetings before the generic
        # question-word guard so casual chatter can stay on the API-edge path.
        if re.search(r"\bhow\s*(are|r)\s*(you|u)(\s*(doing|doin|today))?\b", q):
            return "I am doing great. Tell me what you need."

        # Guard: do not hijack non-trivial asks.
        if re.search(
            r"\b("
            r"what|why|when|where|who|which|how|can|could|would|please|"
            r"research|reserch|analyze|compare|build|create|code|fix|error|"
            r"latest|current|official|statement|news|price|version|"
            r"ceo|founder|linkedin|profile"
            r")\b",
            q,
        ):
            return None

        q_without_buddy_prefix = re.sub(r"^(macha|machi|bro|buddy|dude)\s+", "", q).strip()
        tamil_how_are_you = bool(
            re.search(
                r"\b(epadi|eppadi|epdi|eppdi)\s+"
                r"(ir+u?k+a|ir+u?k+inga|ir+u?keenga|ir+u?kiya|ir+u?kkiya)\b",
                q,
            )
        )
        tamil_casual_short = bool(
            re.fullmatch(
                r"(saptiya|saptaya|saaptiya|saptingla|enna (panra|pandra)|s(e|a)ri da|polaama|polama)",
                q_without_buddy_prefix or q,
            )
        )
        if tamil_how_are_you:
            return "Naan nalla iruken. Nee epadi iruka?"
        if tamil_casual_short:
            if re.fullmatch(r"(saptiya|saptaya|saaptiya|saptingla)", q_without_buddy_prefix or q):
                return "Sapten da. Nee saptiya?"
            if re.fullmatch(r"enna (panra|pandra)", q_without_buddy_prefix or q):
                return "Work mode da. Nee enna panra?"
            return "Seri da. Continue pannalama?"

        if re.fullmatch(r"(h[iey]+|hey+|hello+|heya|yo+|sup)( (bro|da|macha|machi))?", q):
            return "Hey. I am here and ready."
        if re.fullmatch(r"(thanks|thank you|thx)( so much)?", q):
            return "Anytime. Want to continue?"
        if re.fullmatch(r"(bye|goodbye|see you|cya|gn|good night)", q):
            return "See you. Ping me anytime."
        if re.fullmatch(
            r"(ok(?:ay)?(?: (bro|da|macha|machi))?|nice(?: da)?|cool|got it|understood|sounds good)",
            q,
        ):
            return "Perfect. We can continue whenever you are ready."
        if re.fullmatch(r"(next|continue|go on|keep going)", q):
            return None
        return None

    def _is_direct_generation_task(self, query: str) -> bool:
        q = self._normalize_query(query)
        if not q:
            return False
        if self.requires_tools(q):
            return False
        if re.search(
            r"\b(latest|current|today|news|official|statement|policy|price|market|stock|quote|version)\b",
            q,
        ):
            return False
        has_task_verb = bool(
            re.search(r"\b(code|build|create|make|write|generate|design|draft)\b", q)
        )
        has_artifact = bool(
            re.search(
                r"\b(html|css|javascript|js|react|component|page|website|landing page|"
                r"python|script|function|api|json|sql|query|prompt|email|message)\b",
                q,
            )
        )
        return has_task_verb and has_artifact

    def _normalize_query(self, query: str) -> str:
        # Unicode-friendly normalization to support non-Latin greetings too.
        return re.sub(r"\s+", " ", re.sub(r"[^\w'\s]", " ", (query or "").lower(), flags=re.UNICODE)).strip()

    def _small_talk_response(self, query: str) -> Optional[str]:
        q = self._normalize_query(query)
        if not q:
            return None
        q_without_buddy_prefix = re.sub(r"^(macha|machi|bro|buddy|dude)\s+", "", q).strip()

        if re.search(
            r"\b(latest|official|statement|election|legal|court|policy|regulation|press release|fix|bug|error|news)\b",
            q,
        ):
            return None

        tamil_how_are_you = bool(
            re.search(
                r"\b(epadi|eppadi|epdi|eppdi)\s+"
                r"(ir+u?k+a|ir+u?k+inga|ir+u?keenga|ir+u?kiya|ir+u?kkiya)\b",
                q,
            )
        )
        tamil_casual_short = bool(
            re.fullmatch(
                r"(saptiya|saptaya|saaptiya|saptingla|enna (panra|pandra)|s(e|a)ri da|polaama|polama)",
                q_without_buddy_prefix or q,
            )
        )
        greeting_match = re.match(r"^(h[iey]+|hey+|hello+|heya|yo+|sup)\b", q)
        has_buddy_word = bool(re.search(r"\b(macha|machi|bro|buddy|dude)\b", q))
        is_how_are_you = bool(
            re.search(
                r"\bhow\s*(are|r)\s*(you|u)(\s*(doing|doin|today))?\b|\bhow's it going\b|\bhow are things\b",
                q,
            )
        )
        is_user_status = bool(
            re.fullmatch(
                r"(i\s*(am|'?m)\s*(good|fine|ok|okay|great)|all good|doing good|i am fine)",
                q,
            )
        )
        is_capability_ask = bool(
            re.search(
                r"\b("
                r"how\s+(well|wek|good).*(you|u).*(code|react|nextjs|next js|research|reserch)|"
                r"can\s+(you|u)\s+(code|build|research|reserch|analy[sz]e)|"
                r"what\s+can\s+(you|u)\s+(do|code|build|research|reserch)|"
                r"(coding|research)\s+ability"
                r")\b",
                q,
            )
        )
        is_thanks = bool(re.fullmatch(r"(thanks|thank you|thx)( so much)?", q))
        is_bye = bool(re.fullmatch(r"(bye|goodbye|see you|cya|gn|good night)", q))
        is_ack = bool(
            re.fullmatch(
                r"(ok(?:ay)?(?:\s+(?:bro|da|macha|machi))?|nice(?:\s+da)?|cool|got it|understood|sounds good)",
                q,
            )
        )
        has_question_words = bool(
            re.search(
                r"\b(what|why|when|where|who|which|how|can|could|would|please|explain|tell|build|create|price|version|latest|research)\b",
                q,
            )
        )

        trailing_after_greeting = ""
        if greeting_match:
            trailing_after_greeting = q[greeting_match.end():].strip(" ,.!?")
            if trailing_after_greeting:
                # Avoid hijacking real questions that start with "hey ...".
                if has_question_words and not is_how_are_you:
                    return None
                if len(trailing_after_greeting.split()) > 3 and not is_how_are_you:
                    return None

        if tamil_how_are_you:
            return "Naan nalla iruken 🙂 Nee epadi iruka?"
        if tamil_casual_short:
            if re.fullmatch(r"(saptiya|saptaya|saaptiya|saptingla)", q_without_buddy_prefix or q):
                return "Sapten da 😄 Nee saptiya?"
            if re.fullmatch(r"enna (panra|pandra)", q_without_buddy_prefix or q):
                return "Work mode da 😎 Nee enna panra?"
            if re.fullmatch(r"s(e|a)ri da", q_without_buddy_prefix or q):
                return "Seri da 🙌 Continue pannalama?"
            return "Polaama da 🚀 Sollu next enna venum."
        if greeting_match and is_how_are_you:
            return "Hey! I am doing great and ready to help 🙂 What do you need?"
        if is_how_are_you:
            return "I am doing great 🙂 Tell me what you want to work on."
        if is_user_status:
            return "Nice \U0001F642 Want to continue with your next task?"
        if is_capability_ask:
            asks_rating = bool(
                re.search(
                    r"\b(1\s*(to|-|/)\s*10|out of 10|/10|rate|rating|basis of 1 10|basis of 1-10)\b",
                    q,
                )
            )
            asks_react = bool(re.search(r"\breact|next js|nextjs\b", q))
            asks_research = bool(re.search(r"\bresearch|reserch|analy[sz]e\b", q))
            if asks_react and asks_rating:
                return (
                    "React/Next.js: 9/10 for production features, debugging, and performance fixes. "
                    "Share your exact component/task and I will implement it now."
                )
            if asks_react:
                return (
                    "I am strong with React/Next.js end-to-end: components, state, APIs, bugs, and optimization. "
                    "Share your exact requirement and I will build it now."
                )
            if asks_research and asks_rating:
                return (
                    "Research quality: 8.5/10 when I can verify sources and cite clearly. "
                    "Give me a topic and I will show a source-grounded summary."
                )
            if asks_research:
                return (
                    "I can do structured research with source checks, conflict handling, and concise summaries. "
                    "Share a topic and I will start with a verified snapshot."
                )
            return (
                "I can code full web pages, APIs, backend logic, debugging fixes, and tests. "
                "If you want, I can also rate myself by stack (React/Next/Python/Node) out of 10."
            )
        if re.match(r"^previous turn requested\b", q):
            requested = re.sub(r"^previous turn requested\s*", "", q).strip(" .")
            if requested:
                return (
                    f"Context noted: {requested}. "
                    "Next step ready. Say 'next' to continue from this."
                )
            return "Context noted. Say 'next' to continue."
        if greeting_match and has_buddy_word:
            return "Hey macha, good to see you 😎 Tell me what you need."
        if greeting_match:
            return "Hey, I am here 👋 What do you need?"
        if is_thanks:
            return "Anytime 🙌 Want to continue?"
        if is_bye:
            return "See you 👋 Ping me anytime."

        if is_ack:
            return "Perfect. We can continue whenever you are ready."

        # Language-agnostic ultra-short casual fallback to avoid LLM latency.
        token_count = len(q.split())
        if q in {"next", "continue", "go on", "keep going"}:
            return None
        non_casual_short = bool(
            re.search(
                r"\b("
                r"react|python|java|javascript|typescript|node|api|sql|docker|kubernetes|"
                r"bug|fix|error|deploy|build|code|price|version|stock|gold|bitcoin|"
                r"research|analyze|compare|official|statement|policy|news|latest|current"
                r")\b",
                q,
            )
        )
        if token_count == 1 and not non_casual_short:
            return "Hey! I am here and ready to help 🙂 Tell me what you need."

        return None
