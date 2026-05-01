from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LegitimacyEvidence:
    title: str
    source_type: str
    url: str = ""
    supports_presence: bool = True
    supports_registration: bool = False
    requires_login: bool = False


@dataclass(frozen=True)
class LegitimacyResult:
    status: str
    confidence: str
    evidence_found: tuple[str, ...]
    missing: tuple[str, ...]
    caution: str = "This is not legal, financial, or investment due diligence."
