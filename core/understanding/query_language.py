from __future__ import annotations

from dataclasses import dataclass
import re


SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Tamil", re.compile(r"[\u0B80-\u0BFF]")),
    ("Devanagari", re.compile(r"[\u0900-\u097F]")),
    ("Arabic", re.compile(r"[\u0600-\u06FF]")),
    ("Malayalam", re.compile(r"[\u0D00-\u0D7F]")),
    ("Telugu", re.compile(r"[\u0C00-\u0C7F]")),
    ("Bengali", re.compile(r"[\u0980-\u09FF]")),
    ("Kannada", re.compile(r"[\u0C80-\u0CFF]")),
    ("Korean", re.compile(r"[\uAC00-\uD7AF]")),
    ("Japanese", re.compile(r"[\u3040-\u30FF]")),
    ("Chinese", re.compile(r"[\u4E00-\u9FFF]")),
    ("Cyrillic", re.compile(r"[\u0400-\u04FF]")),
)

_SPANISH_HINT = re.compile(r"[¿¡]|\b(qui[eé]n|fund[oó]|empresa)\b", re.I)
_FRENCH_HINT = re.compile(r"\b(qui|pdg|entreprise|vraie|fond[ée]|trouve)\b", re.I)


@dataclass(frozen=True)
class LanguageProfile:
    detected_language: str
    detected_script: str
    language_confidence: float
    mixed_language_flag: bool
    transliteration_detected: bool
    answer_language: str


def detect_language_profile(query: str) -> LanguageProfile:
    text = str(query or "")
    script_hits = [name for name, pat in SCRIPT_PATTERNS if pat.search(text)]
    detected_script = script_hits[0] if script_hits else "Latin"
    mixed_language_flag = len(script_hits) > 1

    if detected_script == "Tamil":
        language = "ta"
        confidence = 0.96
    elif detected_script == "Devanagari":
        language = "hi"
        confidence = 0.95
    elif detected_script == "Arabic":
        language = "ar"
        confidence = 0.92
    elif detected_script == "Malayalam":
        language = "ml"
        confidence = 0.92
    elif detected_script == "Telugu":
        language = "te"
        confidence = 0.92
    elif detected_script == "Bengali":
        language = "bn"
        confidence = 0.92
    elif detected_script == "Kannada":
        language = "kn"
        confidence = 0.92
    elif detected_script == "Korean":
        language = "ko"
        confidence = 0.9
    elif detected_script == "Japanese":
        language = "ja"
        confidence = 0.9
    elif detected_script == "Chinese":
        language = "zh"
        confidence = 0.9
    elif detected_script == "Cyrillic":
        language = "unknown"
        confidence = 0.6
    else:
        if _SPANISH_HINT.search(text):
            language = "es"
            confidence = 0.78
        elif _FRENCH_HINT.search(text):
            language = "fr"
            confidence = 0.76
        else:
            language = "en"
            confidence = 0.72

    transliteration_detected = bool(detected_script == "Latin" and language not in {"en", "unknown"})
    return LanguageProfile(
        detected_language=language,
        detected_script=detected_script,
        language_confidence=confidence,
        mixed_language_flag=mixed_language_flag,
        transliteration_detected=transliteration_detected,
        answer_language=language,
    )
