from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RuleDecision:
    route: str
    confidence: float
    reason: str
    matched_rules: List[str] = field(default_factory=list)
    high_stakes: bool = False
    signals: Dict[str, Any] = field(default_factory=dict)


GREETING_RE = re.compile(
    r"^\s*(?:hi+|hey+|hello+|heya|yo+|sup|thanks|thank\s+you|thx|ok(?:ay)?|bye)"
    r"(?:\s+(?:bro|da|macha|machi))?\s*[!?.,]*\s*$",
    re.I,
)
TAMIL_SMALL_TALK_RE = re.compile(
    r"^(?:(?:macha|machi|bro|buddy|dude)\s+)?"
    r"(?:(?:epadi|eppadi|epdi|eppdi)\s+(?:iruka|irukka|irukeenga|irukiya|irukkiya)|"
    r"saptiya|saptaya|saaptiya|saptingla|enna\s+(?:panra|pandra)|seri\s+da|sari\s+da)"
    r"\s*[!?]*$",
    re.I,
)
DEFINITION_RE = re.compile(
    r"^(?:what\s+(?:is|are|does)|define|meaning\s+of|explain)\b|"
    r"\b(?:na|naa)\s+enna\b|\benna\s+(?:meaning|artham)\b",
    re.I,
)
FRESHNESS_RE = re.compile(
    r"\b(latest|current|today|now|live|recent|newest|version|price|stock|quote|ceo|"
    r"release\s+date|as\s+of|this\s+week|this\s+month|202[0-9])\b",
    re.I,
)
NEWS_RE = re.compile(r"\b(news|headline|breaking|latest\s+news|current\s+status|updates?)\b", re.I)
RESEARCH_RE = re.compile(
    r"\b(research|analy[sz]e|analysis|market|trend|forecast|outlook|deep\s+dive|"
    r"investigate|comprehensive|thorough)\b",
    re.I,
)
COMPARISON_RE = re.compile(
    r"\b(compare|comparison|vs\.?|versus|best|better|pros\s*(?:and|&)\s*cons|difference\s+between)\b",
    re.I,
)
DOC_RE = re.compile(
    r"\b(pdf|document|doc|uploaded|upload|file|notes|chapter|unit|from\s+this|"
    r"from\s+uploaded|important\s+questions|(?:1|2|5|10|16)\s*mark)\b",
    re.I,
)
TASK_RE = re.compile(
    r"\b(run|execute|fix|debug|build|create|send|generate|write|implement|deploy|"
    r"install|test|refactor|email|task|schedule|python\s+code|code\s+error)\b",
    re.I,
)
ROLE_RE = re.compile(r"\b(ceo|founder|co-?founder|chairman|president|governor)\b", re.I)
ENTITY_PROFILE_RE = re.compile(
    r"\b(linkedin|official\s+website|official\s+site|official\s+page|real\s+company|legit(?:imate)?|registered|company\s+details|about\s+company)\b",
    re.I,
)
PERSONAL_SUPPORT_RE = re.compile(
    r"\b("
    r"break\s*up|breakup|broke\s+up|heart\s*break|heartbreak|"
    r"feeling\s+sad|feel\s+sad|feel\s+bad|feeling\s+low|"
    r"depressed|lonely|crying|anxious|stress(?:ed)?|"
    r"my\s+girlfriend|my\s+boyfriend|relationship|got\s+dumped|"
    r"she\s+left\s+me|he\s+left\s+me"
    r")\b",
    re.I,
)
HIGH_STAKES_RE = re.compile(
    r"\b(medical|health|doctor|diagnosis|treatment|medicine|dosage|legal|law|court|"
    r"lawyer|financial|finance|investment|stock|tax|immigration|visa|regulatory|"
    r"regulation|compliance|safety-critical|engineering\s+safety)\b",
    re.I,
)
ADVICE_RE = re.compile(r"\b(should\s+i|can\s+i|dosage|take|invest|buy|sell|file|sue|liable)\b", re.I)


def deterministic_route(normalized_query: str, context: Optional[Dict[str, Any]] = None) -> Optional[RuleDecision]:
    text = str(normalized_query or "").strip()
    if not text:
        return RuleDecision("clarification", 0.8, "empty_query", ["empty_query"])

    context = dict(context or {})
    if bool(context.get("has_active_doc")) and DOC_RE.search(text):
        return RuleDecision("doc_mode", 0.96, "active_document_context", ["active_doc", "doc_hint"])
    if DOC_RE.search(text):
        return RuleDecision("doc_mode", 0.9, "document_or_file_query", ["doc_hint"])
    if GREETING_RE.fullmatch(text) or TAMIL_SMALL_TALK_RE.fullmatch(text):
        return RuleDecision("fast_message", 0.96, "small_talk_or_greeting", ["greeting"])
    if PERSONAL_SUPPORT_RE.search(text):
        return RuleDecision("no_search", 0.93, "personal_support_chat", ["personal_support"])

    high_stakes = bool(HIGH_STAKES_RE.search(text))
    if high_stakes and ADVICE_RE.search(text) and not FRESHNESS_RE.search(text):
        return RuleDecision("clarification", 0.78, "high_stakes_advice_needs_clarification", ["high_stakes", "advice"], True)
    if high_stakes:
        return RuleDecision("official_search", 0.88, "high_stakes_official_source_required", ["high_stakes"], True)
    if ROLE_RE.search(text):
        return RuleDecision("entity_lookup", 0.9, "current_role_entity_lookup", ["role_lookup"])
    if ENTITY_PROFILE_RE.search(text):
        return RuleDecision("entity_lookup", 0.88, "entity_profile_lookup", ["entity_profile"])

    if NEWS_RE.search(text) and FRESHNESS_RE.search(text):
        return RuleDecision("news_search", 0.93, "fresh_news_query", ["news", "freshness"])
    if COMPARISON_RE.search(text):
        return RuleDecision("comparison_search", 0.86, "comparison_query", ["comparison"])
    if RESEARCH_RE.search(text):
        return RuleDecision("deep_search", 0.9, "explicit_research_query", ["research"])
    if DEFINITION_RE.search(text) and not FRESHNESS_RE.search(text):
        return RuleDecision("no_search", 0.88, "definition_without_freshness", ["definition"])
    if FRESHNESS_RE.search(text):
        return RuleDecision("fast_search", 0.84, "current_lookup_query", ["freshness"])
    if TASK_RE.search(text):
        return RuleDecision("task", 0.78, "action_or_code_task", ["task"])
    return None


def safe_default_route(normalized_query: str, context: Optional[Dict[str, Any]] = None) -> RuleDecision:
    text = str(normalized_query or "").strip()
    context = dict(context or {})
    if bool(context.get("has_active_doc")):
        return RuleDecision("doc_mode", 0.72, "safe_default_active_doc", ["safe_default", "active_doc"])
    if HIGH_STAKES_RE.search(text):
        return RuleDecision("clarification", 0.62, "safe_default_high_stakes_clarification", ["safe_default", "high_stakes"], True)
    if FRESHNESS_RE.search(text):
        return RuleDecision("fast_search", 0.66, "safe_default_freshness", ["safe_default", "freshness"])
    if RESEARCH_RE.search(text):
        return RuleDecision("deep_search", 0.66, "safe_default_research", ["safe_default", "research"])
    return RuleDecision("no_search", 0.6, "safe_default_general", ["safe_default"])
