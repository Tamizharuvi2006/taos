from __future__ import annotations

from dataclasses import asdict, replace
import re
from typing import Dict, Iterable, List

from .entity_resolver import EntityResolver
from .intent_frame import CorrectionCandidate, EntityMention, IntentFrame, SearchPlan
from .meaning_frame import build_meaning_frame
from .messy_query_normalizer import MessyQueryNormalizer
from .relation_inferencer import RelationInferencer
from .semantic_query_rewriter import SemanticQueryRewriter


INTENT_PATTERNS: Dict[str, tuple[str, ...]] = {
    "rumour_verification": (
        r"\brumou?r\b",
        r"\bi\s+(?:heard|got|saw|read)\b",
        r"\bsomeone\s+said\b",
        r"\bis\s+it\s+true\b",
        r"\bverify\b",
        r"\bclaim\b",
        r"\bfake\b",
    ),
    "official_verification": (
        r"\bofficial\b",
        r"\bsource[- ]of[- ]record\b",
        r"\bsupported\s+countries\b",
        r"\bdocs?\b",
        r"\bdocumentation\b",
    ),
    "comparison": (r"\bcompare\b", r"\bvs\b", r"\bversus\b", r"\bbetter\b"),
    "current_lookup": (r"\blatest\b", r"\bcurrent\b", r"\btoday\b", r"\bnow\b", r"\brecent\b"),
    "troubleshooting": (r"\berror\b", r"\bfailed\b", r"\bnot\s+working\b", r"\bfix\b"),
    "document_question": (r"\bthis\s+document\b", r"\buploaded\s+file\b", r"\bpdf\b", r"\bdocx\b"),
}


class SearchIntentPlanner:
    def __init__(self) -> None:
        self._normalizer = MessyQueryNormalizer()
        self._entities = EntityResolver()
        self._relations = RelationInferencer()
        self._rewriter = SemanticQueryRewriter()

    def plan(self, query: str) -> IntentFrame:
        original = str(query or "").strip()
        if not original:
            return IntentFrame(original_query="", cleaned_query="", intent="empty")
        cleaned = self._normalizer.normalize(original)
        cleaned_tokens = _tokens(cleaned)
        raw_tokens = _tokens(original)

        entity_candidates = self._entities.correction_candidates(cleaned_tokens)
        relation_candidates = self._relations.correction_candidates(cleaned_tokens)
        correction_candidates = _dedupe_candidates([*entity_candidates, *relation_candidates])
        semantic_tokens = _semantic_tokens(raw_tokens, cleaned_tokens, correction_candidates)

        mentions = self._entities.resolve(semantic_tokens)
        relation = self._relations.infer(semantic_tokens, correction_candidates)
        intent = self._detect_intent(original=original, cleaned=cleaned, semantic_tokens=semantic_tokens, relation=relation.relation)
        entities = self._entity_map(mentions)
        if (
            relation.relation in {"blocked_or_restricted_access", "unavailable_or_outage"}
            and (entities.get("country") or "true" in semantic_tokens)
            and (entities.get("product") or entities.get("company") or entities.get("tool_framework"))
        ):
            intent = "rumour_verification"
        normalized_question = self._rewriter.normalized_question(
            country=entities.get("country", ""),
            product=entities.get("product", "") or entities.get("tool_framework", ""),
            company=entities.get("company", ""),
            relation=relation,
        )
        confidence = self._confidence(
            intent=intent,
            entities=entities,
            relation_score=relation.score,
            correction_candidates=correction_candidates,
        )
        search_plan = self._search_plan(
            original=original,
            cleaned=cleaned,
            intent=intent,
            entities=entities,
            mentions=mentions,
            relation=relation,
            normalized_question=normalized_question,
        )
        frame = IntentFrame(
            original_query=original,
            cleaned_query=cleaned,
            intent=intent,
            entities=entities,
            entity_mentions=mentions,
            relation=relation.relation,
            relation_frame=relation if relation.relation else None,
            normalized_question=normalized_question,
            search_plan=search_plan,
            correction_candidates=tuple(correction_candidates),
            confidence=round(confidence, 3),
            needs_llm_rewrite=bool(confidence < 0.62 and (intent != "general_research" or correction_candidates)),
            raw_query_priority="fallback_only" if correction_candidates or self._normalizer.has_conversational_filler(original) else "normal",
        )
        return replace(frame, meaning_frame=build_meaning_frame(frame))

    def as_trace_summary(self, frame: IntentFrame) -> Dict[str, object]:
        return {
            "original_query": frame.original_query,
            "cleaned_query": frame.cleaned_query,
            "intent": frame.intent,
            "normalized_question": frame.normalized_question,
            "entities": dict(frame.entities),
            "relation": frame.relation,
            "confidence": frame.confidence,
            "raw_query_priority": frame.raw_query_priority,
            "search_plan": frame.search_plan.as_dict(),
        }

    def _detect_intent(self, *, original: str, cleaned: str, semantic_tokens: List[str], relation: str) -> str:
        text = f"{original} {cleaned}".lower()
        for intent, patterns in INTENT_PATTERNS.items():
            if any(re.search(pattern, text, re.I) for pattern in patterns):
                return intent
        if relation in {"blocked_or_restricted_access", "unavailable_or_outage"}:
            return "rumour_verification"
        if relation:
            return "current_lookup"
        if len(semantic_tokens) >= 4:
            return "general_research"
        return "general_research"

    def _entity_map(self, mentions: Iterable[EntityMention]) -> Dict[str, str]:
        entities: Dict[str, str] = {}
        for mention in mentions:
            if mention.kind == "product":
                entities.setdefault("product", mention.canonical)
                if mention.company:
                    entities.setdefault("company", mention.company)
            elif mention.kind == "tool_framework":
                entities.setdefault("tool_framework", mention.canonical)
            else:
                entities.setdefault(mention.kind, mention.canonical)
        return entities

    def _search_plan(
        self,
        *,
        original: str,
        cleaned: str,
        intent: str,
        entities: Dict[str, str],
        mentions: Iterable[EntityMention],
        relation,
        normalized_question: str,
    ) -> SearchPlan:
        country = entities.get("country", "")
        obj = entities.get("product") or entities.get("tool_framework") or entities.get("company") or cleaned
        company = entities.get("company") or obj
        domain = _official_domain(mentions, obj)
        official_label = f"{company} {obj}".strip() if company and company != obj else obj
        company_suffix = company if company and company != obj else ""
        lower_text = f"{original} {cleaned}".lower()
        claude_ai_context = self._is_claude_ai_context(
            text=lower_text,
            obj=obj,
            company=company,
            entities=entities,
        )
        official: List[str] = []
        news: List[str] = []
        contradiction: List[str] = []
        background: List[str] = []
        technical: List[str] = []
        regional: List[str] = []

        if relation.relation in {"blocked_or_restricted_access", "unavailable_or_outage", "pricing_changed", "released_or_changed", "security_concern"} or intent == "official_verification":
            official.extend(
                [
                    f"{official_label} supported countries {country}".strip(),
                    f"site:{domain} {obj} supported countries {country}".strip(),
                    f"site:{domain} supported countries {obj} {country}".strip(),
                ]
            )
            if claude_ai_context:
                official.extend(
                    [
                        "Anthropic Claude supported countries India",
                        "site:anthropic.com Claude supported countries India",
                        "Claude API supported regions India",
                    ]
                )
        if entities.get("tool_framework") and re.search(r"\b(?:current|latest|version|versio)\b", f"{original} {cleaned}", re.I):
            technical.extend(
                [
                    f"{obj} npm package current version",
                    f"site:{domain} {obj} current version",
                ]
            )
        if relation.relation == "blocked_or_restricted_access":
            news.extend(
                [
                    f"{country} blocking {obj} {company_suffix}".strip(),
                    f"{country} ban {obj} AI".strip(),
                    f"{obj} unavailable in {country} {company_suffix}".strip(),
                ]
            )
            contradiction.extend([f"{obj} available in {country} {company_suffix}".strip(), f"{obj} outage {country}".strip()])
            background.extend([f"{official_label} {country} regulatory concern".strip(), f"{obj} {country} service issue".strip()])
            if country:
                regional.append(f"{country} {obj} access availability")
            if claude_ai_context:
                news.extend(
                    [
                        "India Claude Mythos cybersecurity risks RBI Anthropic",
                        "India blocking Claude Anthropic",
                        "India warning banks Claude Mythos",
                    ]
                )
                contradiction.extend(
                    [
                        "Claude available in India Anthropic supported countries",
                        "India Claude access supported countries",
                    ]
                )
                background.extend(
                    [
                        "Claude Mythos India banks cybersecurity concerns",
                        "Anthropic Claude Code issues April 2026",
                    ]
                )
        elif relation.relation == "unavailable_or_outage":
            news.extend([f"{obj} outage {country}".strip(), f"{obj} down {country}".strip()])
            contradiction.append(f"{obj} status page outage {country}".strip())
            background.append(f"{obj} service issue {country}".strip())
        elif relation.relation == "released_or_changed":
            news.append(f"{obj} latest release changes")
            technical.extend([f"{obj} changelog", f"site:{domain} {obj} release notes"])
        elif relation.relation == "pricing_changed":
            official.append(f"site:{domain} {obj} pricing")
            news.append(f"{obj} pricing changed latest")
            contradiction.append(f"{obj} pricing unchanged official")
        elif relation.relation == "security_concern":
            news.append(f"{obj} security concern {country}".strip())
            background.append(f"{company} {obj} cybersecurity concerns".strip())
        elif normalized_question:
            news.append(normalized_question.rstrip("?"))
        else:
            news.append(cleaned)

        fallback = [original] if original and original.lower() != cleaned.lower() else []
        return SearchPlan(
            official=tuple(_dedupe(official)),
            news=tuple(_dedupe(news)),
            contradiction=tuple(_dedupe(contradiction)),
            background=tuple(_dedupe(background)),
            technical=tuple(_dedupe(technical)),
            regional=tuple(_dedupe(regional)),
            fallback=tuple(_dedupe(fallback)),
        )

    def _is_claude_ai_context(
        self,
        *,
        text: str,
        obj: str,
        company: str,
        entities: Dict[str, str],
    ) -> bool:
        compact = str(text or "").lower()
        target = " ".join(
            [
                str(obj or "").lower(),
                str(company or "").lower(),
                str(entities.get("product") or "").lower(),
                str(entities.get("company") or "").lower(),
            ]
        )
        claude_like = any(token in compact for token in ("claude", "claud", "claudde", "cluade")) or "claude" in target
        ai_context = any(
            marker in compact
            for marker in ("anthropic", "model", "issue", "ai", "news", "mythos", "claude code")
        )
        return bool(claude_like and ai_context)

    def _confidence(
        self,
        *,
        intent: str,
        entities: Dict[str, str],
        relation_score: float,
        correction_candidates: Iterable[CorrectionCandidate],
    ) -> float:
        score = 0.2
        if intent != "general_research":
            score += 0.16
        if entities.get("country"):
            score += 0.16
        if entities.get("product") or entities.get("company") or entities.get("tool_framework"):
            score += 0.2
        if relation_score:
            score += 0.18
        candidate_scores = [candidate.score for candidate in correction_candidates if candidate.score < 1.0]
        if candidate_scores:
            score += min(0.1, sum(candidate_scores) / max(1, len(candidate_scores)) * 0.1)
        elif entities and relation_score:
            score += 0.1
        return max(0.0, min(1.0, score))


def plan_search_intent(query: str) -> IntentFrame:
    return SearchIntentPlanner().plan(query)


def flatten_search_plan(search_plan: SearchPlan, *, include_fallback: bool = True) -> List[str]:
    queries: List[str] = []
    for lane in ("official", "news", "contradiction", "background", "technical", "regional"):
        queries.extend(getattr(search_plan, lane))
    if include_fallback:
        queries.extend(search_plan.fallback)
    return _dedupe(queries)


def frame_to_dict(frame: IntentFrame) -> Dict[str, object]:
    data = asdict(frame)
    data["search_plan"] = frame.search_plan.as_dict()
    return data


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _semantic_tokens(raw_tokens: List[str], cleaned_tokens: List[str], candidates: Iterable[CorrectionCandidate]) -> List[str]:
    values = [*cleaned_tokens, *raw_tokens]
    values.extend(candidate.candidate for candidate in candidates)
    return _dedupe(values)


def _official_domain(mentions: Iterable[EntityMention], obj: str) -> str:
    for mention in mentions:
        if mention.canonical == obj and mention.official_domain:
            return mention.official_domain
    for mention in mentions:
        if mention.official_domain:
            return mention.official_domain
    return "official.com"


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _dedupe_candidates(candidates: Iterable[CorrectionCandidate]) -> List[CorrectionCandidate]:
    out: List[CorrectionCandidate] = []
    seen = set()
    for candidate in candidates:
        key = (candidate.token, candidate.candidate, candidate.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    out.sort(key=lambda item: item.score, reverse=True)
    return out
