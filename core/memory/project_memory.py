from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Protocol


class MessageLike(Protocol):
    id: str
    role: str
    content: str


@dataclass(frozen=True)
class ProjectFact:
    key: str
    value: str
    category: str
    source_message_ids: tuple[str, ...]
    confidence: float = 0.85


class ProjectMemory:
    def __init__(self, max_facts: int = 40) -> None:
        self._facts: Dict[str, ProjectFact] = {}
        self._max_facts = max(5, int(max_facts or 40))

    def update_from_messages(self, messages: Iterable[MessageLike]) -> None:
        for message in messages:
            for fact in extract_project_facts(message.content, source_message_id=message.id):
                self.put(fact)

    def put(self, fact: ProjectFact) -> None:
        if not fact.key or not fact.value:
            return
        self._facts[fact.key] = fact
        if len(self._facts) > self._max_facts:
            oldest = next(iter(self._facts))
            self._facts.pop(oldest, None)

    def facts(self, *, category: str = "") -> List[ProjectFact]:
        values = list(self._facts.values())
        if category:
            values = [fact for fact in values if fact.category == category]
        return values

    def as_trace(self) -> Dict[str, object]:
        return {
            "fact_count": len(self._facts),
            "facts": [
                {
                    "key": fact.key,
                    "value": fact.value,
                    "category": fact.category,
                    "source_message_ids": list(fact.source_message_ids),
                    "confidence": fact.confidence,
                }
                for fact in self.facts()
            ],
        }


def extract_project_facts(content: str, *, source_message_id: str) -> List[ProjectFact]:
    text = str(content or "")
    lower = text.lower()
    facts: List[ProjectFact] = []
    for match in re.finditer(r"\bphase\s+([0-9]{2,3}[a-z]?)\b[^.\n]{0,120}\b(done|complete|completed|passed|green)\b", text, re.I):
        phase = match.group(1).upper()
        facts.append(ProjectFact(
            key=f"phase:{phase}:status",
            value=f"Phase {phase} {match.group(2).lower()}",
            category="phase_status",
            source_message_ids=(source_message_id,),
        ))
    if "package-version" in lower and ("locked" in lower or "source-of-record" in lower):
        facts.append(ProjectFact(
            key="decision:package_version_source_of_record_locked",
            value="Package-version source-of-record behavior is locked.",
            category="architecture_decision",
            source_message_ids=(source_message_id,),
            confidence=0.95,
        ))
    if "development_log.md" in lower or "dev log" in lower:
        facts.append(ProjectFact(
            key="preference:update_development_log",
            value="Update DEVELOPMENT_LOG.md for phase-based work.",
            category="user_preference",
            source_message_ids=(source_message_id,),
        ))
    if "best-supported" in lower and "generic" in lower:
        facts.append(ProjectFact(
            key="decision:best_supported_research_answers",
            value="Use best-supported research answers instead of generic no-result fallback.",
            category="architecture_decision",
            source_message_ids=(source_message_id,),
        ))
    if "public-only" in lower or "do not bypass login" in lower:
        facts.append(ProjectFact(
            key="safety:public_entity_intelligence_only",
            value="Entity intelligence must use public evidence only and must not bypass login.",
            category="safety_boundary",
            source_message_ids=(source_message_id,),
        ))
    return facts
