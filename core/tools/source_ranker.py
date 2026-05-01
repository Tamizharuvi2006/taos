"""
TAOS Source Ranking System: domain-aware filtering and ranking.

Ranks and filters search results based on source quality,
domain relevance, provider diversity, and optional freshness weighting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from taos.core.semantic.intent_classifier import DomainType


@dataclass
class RankedSource:
    """A ranked search result source."""

    url: str
    title: str = ""
    snippet: str = ""
    rank_score: float = 0.0
    tier: str = "other"  # "official" | "trusted" | "other"
    domain_match: bool = False
    provider: str = ""
    published_at: str = ""
    freshness_score: float = 0.0
    source_score: float = 0.0
    reason: str = ""


# Tier 1: Official/authoritative sources (highest trust)
OFFICIAL_DOMAINS: Dict[DomainType, Set[str]] = {
    DomainType.AI: {
        "openai.com",
        "anthropic.com",
        "deepmind.google",
        "huggingface.co",
        "arxiv.org",
        "ai.google",
        "pytorch.org",
        "tensorflow.org",
        "keras.io",
        "scikit-learn.org",
        "mlflow.org",
        "wandb.ai",
        "paperswithcode.com",
        "distill.pub",
    },
    DomainType.PROGRAMMING: {
        "docs.python.org",
        "developer.mozilla.org",
        "doc.rust-lang.org",
        "go.dev",
        "learn.microsoft.com",
        "nodejs.org",
        "typescriptlang.org",
        "react.dev",
        "vuejs.org",
        "angular.dev",
        "nextjs.org",
        "fastapi.tiangolo.com",
        "docs.djangoproject.com",
        "docs.docker.com",
        "kubernetes.io",
        "docs.aws.amazon.com",
        "cloud.google.com",
        "docs.github.com",
        "vitejs.dev",
        "packaging.python.org",
        "pypi.org",
        "npmjs.com",
    },
    DomainType.STARTUP: {
        "ycombinator.com",
        "crunchbase.com",
        "techcrunch.com",
        "producthunt.com",
        "a16z.com",
        "sequoiacap.com",
        "firstround.com",
        "saastr.com",
    },
    DomainType.GENERAL: set(),
}

# Tier 2: Trusted community sources
TRUSTED_DOMAINS: Set[str] = {
    "stackoverflow.com",
    "github.com",
    "medium.com",
    "dev.to",
    "hackernews.ycombinator.com",
    "reddit.com",
    "wikipedia.org",
    "towardsdatascience.com",
    "realpython.com",
    "blog.google",
    "engineering.fb.com",
    "css-tricks.com",
    "smashingmagazine.com",
    "web.dev",
    "infoq.com",
    "martinfowler.com",
    "research.google",
    "engineering.atspotify.com",
    "netflixtechblog.com",
    "uber.com/blog",
}

# Public authority / institutional domains treated as official.
OFFICIAL_PUBLIC_DOMAINS: Set[str] = {
    "sec.gov",
    "federalreserve.gov",
    "rbi.org.in",
    "europa.eu",
    "who.int",
    "imf.org",
    "worldbank.org",
    "un.org",
}

# Domains to deprioritize
LOW_QUALITY_DOMAINS: Set[str] = {
    "pinterest.com",
    "quora.com",
    "w3schools.com",
    "geeksforgeeks.org",
    "tutorialspoint.com",
    "javatpoint.com",
    "guru99.com",
    "educba.com",
    "simplilearn.com",
    "intellipaat.com",
}

# Domains to block entirely
BLOCKED_DOMAINS: Set[str] = {
    "spam-example.com",
    "clickbait-tech.com",
    "fake-reviews.com",
    "content-farm.xyz",
}


class SourceRanker:
    """
    Production source ranking system.

    Ranks search results by:
    1. Source tier (official > trusted > other)
    2. Domain relevance
    3. Snippet quality
    4. Provider diversity
    5. Freshness weighting (when enabled)
    """

    TIER_SCORES = {
        "official": 1.0,
        "trusted": 0.7,
        "other": 0.3,
    }

    MONTHS = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    def rank(
        self,
        results: List[Dict[str, Any]],
        domain: DomainType = DomainType.GENERAL,
        max_results: int = 10,
        *,
        max_per_provider: int = 2,
        freshness_sensitive: bool = False,
        official_source_required: bool = False,
        reference_time: Optional[datetime] = None,
    ) -> List[RankedSource]:
        """
        Rank and filter search results.

        Args:
            results: Raw search results (list of dicts with url/title/snippet).
            domain: Detected domain for relevance scoring.
            max_results: Maximum number of results to return.
            max_per_provider: Domain cap for diversity.
            freshness_sensitive: Whether recency should heavily influence ranking.
            reference_time: UTC reference for recency scoring.
        """
        ranked: List[RankedSource] = []
        now = reference_time or datetime.now(timezone.utc)

        for result in results:
            url = str(result.get("url", result.get("link", "")) or "").strip()
            if not url:
                continue

            source_domain = self._extract_domain(url)
            if source_domain in BLOCKED_DOMAINS:
                continue

            title = str(result.get("title", "") or "")
            snippet = str(result.get("snippet", "") or "")
            published_at = str(result.get("published_at", result.get("date_hint", "")) or "")
            freshness_bonus = self._freshness_bonus(
                published_at=published_at,
                title=title,
                snippet=snippet,
                freshness_sensitive=freshness_sensitive,
                now=now,
            )

            tier = self._classify_tier(source_domain, domain)
            score = self._compute_score(
                url=url,
                title=title,
                snippet=snippet,
                tier=tier,
                domain=domain,
                source_domain=source_domain,
                freshness_bonus=freshness_bonus,
                official_source_required=official_source_required,
            )

            ranked.append(
                RankedSource(
                    url=url,
                    title=title,
                    snippet=snippet,
                    rank_score=score,
                    tier=tier,
                    domain_match=self._is_domain_relevant(source_domain, domain),
                    provider=source_domain,
                    published_at=published_at,
                    freshness_score=round(float(freshness_bonus), 4),
                    source_score=round(float(score), 4),
                    reason=self._reason_for_source(
                        tier=tier,
                        source_domain=source_domain,
                        freshness_bonus=freshness_bonus,
                    ),
                )
            )

        ranked.sort(key=lambda r: r.rank_score, reverse=True)
        diversified = self._enforce_diversity(ranked, max_per_provider=max(1, int(max_per_provider)))
        return diversified[:max_results]

    def _reason_for_source(self, *, tier: str, source_domain: str, freshness_bonus: float) -> str:
        freshness_text = "recent" if freshness_bonus >= 0.6 else "usable"
        if tier == "official":
            return f"Official source, {freshness_text}, directly relevant"
        if tier == "trusted":
            return f"Trusted source from {source_domain or 'trusted domain'}"
        return f"General source from {source_domain or 'web'}"

    def filter_irrelevant(
        self,
        results: List[RankedSource],
        min_score: float = 0.2,
    ) -> List[RankedSource]:
        """Remove results below minimum score threshold."""
        return [r for r in results if r.rank_score >= min_score]

    def _classify_tier(self, source_domain: str, query_domain: DomainType) -> str:
        official = OFFICIAL_DOMAINS.get(query_domain, set())
        if self._domain_matches_set(source_domain, official):
            return "official"

        all_official: Set[str] = set()
        for domains in OFFICIAL_DOMAINS.values():
            all_official |= domains
        if self._domain_matches_set(source_domain, all_official):
            return "official"
        if self._is_public_authority_domain(source_domain):
            return "official"

        if self._domain_matches_set(source_domain, TRUSTED_DOMAINS):
            return "trusted"
        return "other"

    def _compute_score(
        self,
        url: str,
        title: str,
        snippet: str,
        tier: str,
        domain: DomainType,
        source_domain: str,
        freshness_bonus: float = 0.0,
        official_source_required: bool = False,
    ) -> float:
        score = self.TIER_SCORES.get(tier, 0.3)

        if self._is_domain_relevant(source_domain, domain):
            score += 0.2
        if source_domain in LOW_QUALITY_DOMAINS:
            score -= 0.3
        if snippet and len(snippet) > 50:
            score += 0.1
        if snippet and len(snippet) > 150:
            score += 0.05
        if title and len(title) > 10:
            score += 0.05
        if url.startswith("https://"):
            score += 0.05
        if re.search(r"\b(docs?|documentation|guide|tutorial|reference)\b", url, re.I):
            score += 0.15

        if official_source_required:
            if tier == "official":
                score += 0.30
            elif tier == "trusted":
                score -= 0.12
            else:
                score -= 0.24
            if re.search(r"\b(official|statement|policy|press release|gazette|regulation)\b", f"{title} {snippet}", re.I):
                score += 0.08

        score += float(freshness_bonus)
        return max(0.0, min(1.8, score))

    def _is_domain_relevant(self, source_domain: str, query_domain: DomainType) -> bool:
        official = OFFICIAL_DOMAINS.get(query_domain, set())
        return self._domain_matches_set(source_domain, official | TRUSTED_DOMAINS)

    def _domain_matches_set(self, domain: str, domain_set: Set[str]) -> bool:
        for d in domain_set:
            if domain == d or domain.endswith("." + d):
                return True
        return False

    def _extract_domain(self, url: str) -> str:
        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        return match.group(1).lower() if match else ""

    def _is_public_authority_domain(self, source_domain: str) -> bool:
        domain = str(source_domain or "").lower().strip()
        if not domain:
            return False
        if domain.endswith(".gov") or ".gov." in domain:
            return True
        if domain.endswith(".mil") or ".mil." in domain:
            return True
        return self._domain_matches_set(domain, OFFICIAL_PUBLIC_DOMAINS)

    def _enforce_diversity(
        self,
        results: List[RankedSource],
        max_per_provider: int = 2,
    ) -> List[RankedSource]:
        provider_counts: Dict[str, int] = {}
        diversified: List[RankedSource] = []
        for result in results:
            count = provider_counts.get(result.provider, 0)
            if count < max_per_provider:
                diversified.append(result)
                provider_counts[result.provider] = count + 1
        return diversified

    def _freshness_bonus(
        self,
        *,
        published_at: str,
        title: str,
        snippet: str,
        freshness_sensitive: bool,
        now: datetime,
    ) -> float:
        candidate = published_at or self._extract_date_candidate(f"{title}. {snippet}")
        dt = self._parse_datetime(candidate)
        if dt is None:
            return -0.08 if freshness_sensitive else 0.0

        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        if freshness_sensitive:
            if age_days <= 2:
                return 0.35
            if age_days <= 7:
                return 0.22
            if age_days <= 30:
                return 0.08
            return -0.12

        if age_days <= 30:
            return 0.08
        if age_days <= 90:
            return 0.03
        return 0.0

    @classmethod
    def _extract_date_candidate(cls, text: str) -> str:
        blob = str(text or "")

        m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", blob)
        if m:
            return m.group(0)

        m = re.search(
            r"\b("
            r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
            r")\s+(\d{1,2}),\s*(20\d{2})\b",
            blob,
            flags=re.I,
        )
        if m:
            return f"{m.group(1)} {m.group(2)}, {m.group(3)}"

        m = re.search(
            r"\b(\d{1,2})\s+("
            r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
            r")\s+(20\d{2})\b",
            blob,
            flags=re.I,
        )
        if m:
            return f"{m.group(1)} {m.group(2)} {m.group(3)}"
        return ""

    @classmethod
    def _parse_datetime(cls, value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None

        try:
            iso = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

        m = re.match(r"^(20\d{2})-(\d{2})-(\d{2})$", text)
        if m:
            try:
                return datetime(
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                    tzinfo=timezone.utc,
                )
            except Exception:
                return None

        m = re.match(
            r"^("
            r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
            r")\s+(\d{1,2}),\s*(20\d{2})$",
            text,
            flags=re.I,
        )
        if m:
            month_key = m.group(1).lower()
            month = cls.MONTHS.get(month_key)
            if month:
                try:
                    return datetime(int(m.group(3)), month, int(m.group(2)), tzinfo=timezone.utc)
                except Exception:
                    return None

        m = re.match(
            r"^(\d{1,2})\s+("
            r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
            r")\s+(20\d{2})$",
            text,
            flags=re.I,
        )
        if m:
            month_key = m.group(2).lower()
            month = cls.MONTHS.get(month_key)
            if month:
                try:
                    return datetime(int(m.group(3)), month, int(m.group(1)), tzinfo=timezone.utc)
                except Exception:
                    return None
        return None
