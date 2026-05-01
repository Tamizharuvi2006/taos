from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, Optional


@dataclass
class FreshnessDecision:
    mode: str
    search_type: str
    recency_days: Optional[int]


class FreshnessPolicy:
    def decide(self, query: str) -> FreshnessDecision:
        text = str(query or "").lower()
        if re.search(r"\b(news|latest|today|live|breaking|current status)\b", text):
            return FreshnessDecision(mode="news_live", search_type="news", recency_days=2)
        if re.search(r"\b(current|latest|version|ceo|price|release date)\b", text):
            return FreshnessDecision(mode="current_lookup", search_type="search", recency_days=14)
        if re.search(r"\b(history|historical|timeline|origin|founded|old)\b", text):
            return FreshnessDecision(mode="historical", search_type="search", recency_days=None)
        return FreshnessDecision(mode="general_research", search_type="search", recency_days=365)

    def summarize(self, rows: Iterable[Dict[str, Any]], *, mode: str) -> Dict[str, Any]:
        dates = []
        for row in rows:
            raw = str(row.get("published_at") or row.get("date_hint") or "").strip()
            dt = self._parse_date(raw)
            if dt:
                dates.append(dt)
        newest = max(dates).date().isoformat() if dates else None
        oldest = min(dates).date().isoformat() if dates else None
        stale = self._is_stale(mode=mode, dates=dates)
        freshness_score = self._score(mode=mode, dates=dates)
        return {
            "freshness_mode": mode,
            "newest_source_date": newest,
            "oldest_used_source_date": oldest,
            "stale_detected": stale,
            "freshness_score": freshness_score,
        }

    def _is_stale(self, *, mode: str, dates: list[datetime]) -> bool:
        if not dates or mode == "historical":
            return False
        newest = self._ensure_aware(max(dates))
        age_days = (datetime.now(timezone.utc) - newest).days
        if mode == "news_live":
            return age_days > 2
        if mode == "current_lookup":
            return age_days > 45
        if mode == "general_research":
            return age_days > 365
        return False

    def _score(self, *, mode: str, dates: list[datetime]) -> float:
        if not dates:
            return 0.0
        newest = self._ensure_aware(max(dates))
        age_days = max(0, (datetime.now(timezone.utc) - newest).days)
        if mode == "historical":
            return 1.0
        if mode == "news_live":
            return 1.0 if age_days <= 2 else max(0.1, 1.0 - (age_days / 10.0))
        if mode == "current_lookup":
            return 1.0 if age_days <= 14 else max(0.15, 1.0 - (age_days / 90.0))
        return 1.0 if age_days <= 180 else max(0.15, 1.0 - (age_days / 540.0))

    def _parse_date(self, value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        iso = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso)
        except ValueError:
            pass
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if match:
            try:
                return datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _ensure_aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
