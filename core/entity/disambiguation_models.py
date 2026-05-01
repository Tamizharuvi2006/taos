from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class EntityCandidate:
    name: str
    entity_type: str = "company"
    location: str = ""
    domain: str = ""
    industry: str = ""
    social_handle: str = ""
    source_count: int = 0
    evidence: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DisambiguationResult:
    mode: str
    selected: EntityCandidate | None = None
    candidates: tuple[EntityCandidate, ...] = ()
    confidence: float = 0.0
    reason: str = ""
