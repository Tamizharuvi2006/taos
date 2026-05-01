from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict, List
from urllib.parse import urlparse

from taos.core.research.source_quality import SourceQualityScorer

from .search_cache import SearchResultCache


WebSearchCallable = Callable[..., Awaitable[Dict[str, Any]]]
WebExtractCallable = Callable[..., Awaitable[Dict[str, Any]]]
PackageRegistryCallable = Callable[..., Awaitable[Dict[str, Any]]]

_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-z0-9][a-z0-9.\-]*)?\b", re.I)
_VERSION_HINT_RE = re.compile(
    r"\b(version|versions|versio|verison|versoin|release|latest\s+release|stable\s+release|latest\s+stable)\b",
    re.I,
)
_ROLE_HINT_RE = re.compile(r"\b(ceo|founder|co-?founder|chairman|president|governor)\b", re.I)
_VERSION_RESULT_HINT_RE = re.compile(
    r"\b(version|release|released|stable|changelog|announcement|announcing|package|download|install)\b",
    re.I,
)
_ROLE_RESULT_HINT_RE = re.compile(r"\b(ceo|founder|leadership|team|management|executive|about)\b", re.I)
_CURRENT_LOOKUP_STOPWORDS = {
    "current",
    "latest",
    "today",
    "now",
    "what",
    "is",
    "the",
    "of",
    "version",
    "versions",
    "versio",
    "verison",
    "versoin",
    "release",
    "stable",
    "who",
    "ceo",
    "founder",
    "cofounder",
    "company",
    "research",
    "deep",
    "comprehensive",
    "detailed",
    "full",
    "thorough",
    "verify",
    "verified",
    "verification",
    "with",
    "and",
    "for",
    "about",
    "source",
    "sources",
}
_LOW_QUALITY_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "quora.com",
    "reddit.com",
    "tiktok.com",
    "x.com",
}
_WEAK_TECH_DOMAINS = {
    "dev.to",
    "medium.com",
}
_TECHNICAL_PACKAGE_DOMAINS = {
    "jsdocs.io",
    "npmx.dev",
}
_TRUSTED_REPORTING_DOMAINS = {
    "apnews.com",
    "bbc.com",
    "bloomberg.com",
    "forbes.com",
    "reuters.com",
    "techcrunch.com",
    "theverge.com",
}
_SOURCE_OF_RECORD_DOMAINS = {
    "github.com",
    "npmjs.com",
    "pypi.org",
    "crates.io",
    "rubygems.org",
    "packagist.org",
}
_PACKAGE_ALIASES = {
    "nextjs": "next",
    "next.js": "next",
}


class SearchLite:
    def __init__(
        self,
        cache: SearchResultCache | None = None,
        scorer: SourceQualityScorer | None = None,
    ) -> None:
        self._cache = cache or SearchResultCache()
        self._scorer = scorer or SourceQualityScorer()

    async def run(
        self,
        *,
        query: str,
        web_search_fn: WebSearchCallable,
        web_extract_fn: WebExtractCallable | None = None,
        package_registry_fn: PackageRegistryCallable | None = None,
        search_type: str = "search",
        num_results: int = 5,
        recency_days: int | None = None,
        freshness_mode: str = "current_lookup",
    ) -> Dict[str, Any]:
        allow_stale = freshness_mode not in {"news_live", "current_lookup"}
        cached, cache_status = self._cache.get(
            query=query,
            mode=freshness_mode,
            search_type=search_type,
            allow_stale=allow_stale,
        )
        if cached:
            cached.setdefault("metadata", {})
            if isinstance(cached["metadata"], dict):
                cached["metadata"]["cache_status"] = cache_status
            return cached

        query_kind = self._query_kind(query)
        registry_payload = await self._try_package_registry_lookup(
            query=query,
            query_kind=query_kind,
            package_registry_fn=package_registry_fn,
            freshness_mode=freshness_mode,
            search_type=search_type,
        )
        if registry_payload is not None:
            self._cache.set(query=query, mode=freshness_mode, value=registry_payload, search_type=search_type)
            return registry_payload

        search_queries = self._build_query_plan(query, query_kind=query_kind)
        raw_rows: List[Dict[str, Any]] = []
        for search_query in search_queries:
            response = await web_search_fn(
                query=search_query,
                num_results=max(3, min(int(num_results or 5), 5)),
                search_type=search_type,
                recency_days=recency_days,
            )
            raw_rows = self._merge_rows(raw_rows, list((response or {}).get("results") or [])[:5])
            if self._has_confident_fast_answer(
                self._rank_rows(raw_rows, query=query),
                query_kind=query_kind,
            ):
                break

        if not raw_rows:
            return self._build_no_result(query=query, query_kind=query_kind)

        rows = self._rank_rows(raw_rows, query=query)
        if not rows:
            return self._build_no_result(query=query, query_kind=query_kind)

        extract_summary = {
            "attempted_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "source_reading_used": False,
        }
        if web_extract_fn is not None and self._should_read_sources_for_verification(rows, query_kind=query_kind):
            raw_rows, extract_summary = await self._read_source_pages(
                query=query,
                rows=rows,
                web_extract_fn=web_extract_fn,
                query_kind=query_kind,
            )
            rows = self._rank_rows(raw_rows, query=query)
            if not rows:
                return self._build_no_result(query=query, query_kind=query_kind)

        if query_kind == "version_lookup":
            payload = self._build_version_answer(query=query, rows=rows, freshness_mode=freshness_mode, search_type=search_type)
        elif query_kind == "role_lookup":
            payload = self._build_no_result(
                query=query,
                query_kind=query_kind,
                warnings=[
                    "Limited verification: current role lookups need official or company-controlled sources.",
                ],
            )
        else:
            payload = self._build_general_answer(
                query=query,
                rows=rows,
                freshness_mode=freshness_mode,
                search_type=search_type,
            )

        payload["raw_rows"] = rows
        metadata = dict(payload.get("metadata") or {})
        metadata["cache_status"] = "miss"
        metadata["search_lite"] = True
        metadata["query_kind"] = query_kind
        metadata["search_queries"] = list(search_queries)
        metadata["supplemental_query_used"] = len(search_queries) > 1
        metadata["extract_attempted_count"] = int(extract_summary.get("attempted_count") or 0)
        metadata["extract_success_count"] = int(extract_summary.get("success_count") or 0)
        metadata["extract_failed_count"] = int(extract_summary.get("failed_count") or 0)
        metadata["source_reading_used"] = bool(extract_summary.get("source_reading_used"))
        metadata["extraction_recovery_used"] = bool(extract_summary.get("extraction_recovery_used"))
        metadata["snippet_only"] = not bool(extract_summary.get("source_reading_used"))
        payload["metadata"] = metadata
        self._cache.set(query=query, mode=freshness_mode, value=payload, search_type=search_type)
        return payload

    async def _try_package_registry_lookup(
        self,
        *,
        query: str,
        query_kind: str,
        package_registry_fn: PackageRegistryCallable | None,
        freshness_mode: str,
        search_type: str,
    ) -> Dict[str, Any] | None:
        if query_kind != "version_lookup" or package_registry_fn is None:
            return None
        package_name = self._subject_package_name(query)
        if not package_name:
            return None
        try:
            registry = await package_registry_fn(package_name=package_name)
        except Exception:
            return None
        if not bool((registry or {}).get("success")):
            error = str((registry or {}).get("error") or "package_registry_unavailable")
            return self._build_no_result(
                query=query,
                query_kind=query_kind,
                warnings=[
                    "Package source-of-record lookup is unavailable right now; TAOS did not fall back to generic web results for this package-version query.",
                ],
                metadata={
                    "package_registry_used": True,
                    "package_name": package_name,
                    "source_type": "package_registry",
                    "source_domain": "npmjs.com",
                    "generic_web_used": False,
                    "registry_error": error,
                    "provider_health": dict((registry or {}).get("provider_health") or {}),
                },
            )
        version = str((registry or {}).get("version") or "").strip().lower().lstrip("v")
        if not version or not _VERSION_RE.fullmatch(version):
            return None

        display_name = str((registry or {}).get("name") or package_name).strip() or package_name
        source_url = str((registry or {}).get("source_url") or f"https://www.npmjs.com/package/{package_name}").strip()
        published_at = str((registry or {}).get("published_at") or "").strip()
        publish_note = f" Published: {published_at}." if published_at else ""
        row = {
            "title": f"{display_name} - npm",
            "link": source_url,
            "snippet": f"npm registry latest tag for {display_name} is {version}.{publish_note}".strip(),
            "provider": "npmjs.com",
            "domain": "npmjs.com",
            "source_domain": "npmjs.com",
            "source_type": "package_registry",
            "tier": "official",
            "freshness_score": 0.9,
            "source_of_record": True,
            "entity_exact_match": True,
            "query_match_score": 1.0,
            "version_candidate": version,
            "source_score": 1.0,
            "published_at": published_at,
        }
        subject = self._subject_label(query) or display_name
        answer = f"The latest version I could verify for {subject} is {version}."
        payload = {
            "mode": "fast_search",
            "answer": answer,
            "direct_answer": answer,
            "key_points": [row["snippet"]],
            "sources": [source_url],
            "confidence": 0.92,
            "freshness": freshness_mode,
            "warnings": [],
            "metadata": {
                "freshness_mode": freshness_mode,
                "search_type": search_type,
                "source_count": 1,
                "source_of_record_count": 1,
                "verification_state": "verified",
                "search_lite": True,
                "query_kind": query_kind,
                "verified_value": version,
                "package_registry_used": True,
                "package_name": package_name,
                "source_type": "package_registry",
                "source_domain": "npmjs.com",
                "generic_web_used": False,
                "fast_search_escalation_recommended": False,
                "confidence_reason": "Version confirmed from the npm registry latest tag.",
                "cache_status": str((registry or {}).get("cache_status") or "miss"),
                "registry_cache_status": str((registry or {}).get("cache_status") or "miss"),
                "cache_used": str((registry or {}).get("cache_status") or "") in {"hit", "stale_fallback", "circuit_open_cache_fallback"},
                "provider_health": dict((registry or {}).get("provider_health") or {}),
                "search_queries": [f"npm registry:{package_name}"],
                "supplemental_query_used": False,
                "extract_attempted_count": 0,
                "extract_success_count": 0,
                "extract_failed_count": 0,
                "source_reading_used": False,
                "snippet_only": False,
            },
            "raw_rows": [row],
        }
        return payload

    def _build_general_answer(
        self,
        *,
        query: str,
        rows: List[Dict[str, Any]],
        freshness_mode: str,
        search_type: str,
    ) -> Dict[str, Any]:
        top = rows[0]
        top_score = float(top.get("source_score") or 0.0)
        if top_score < 0.58:
            return self._build_no_result(
                query=query,
                query_kind="current_lookup",
                warnings=["Limited verification: quick search results were too weak to trust."],
                rows=rows,
            )

        answer = str(top.get("snippet") or top.get("title") or "").strip()
        if not answer:
            return self._build_no_result(query=query, query_kind="current_lookup", rows=rows)
        sources = [str(row.get("link") or "").strip() for row in rows[:5] if str(row.get("link") or "").strip()]
        key_points = [
            f"{str(row.get('title') or '').strip()}: {str(row.get('snippet') or '').strip()}".strip(": ")
            for row in rows[1:3]
            if str(row.get("snippet") or row.get("title") or "").strip()
        ]
        warnings: List[str] = []
        if top_score < 0.72:
            warnings.append("Limited verification: the top quick-search source is usable but not strong.")
        return {
            "mode": "fast_search",
            "answer": answer,
            "direct_answer": answer,
            "key_points": key_points,
            "sources": sources,
            "confidence": round(max(0.28, min(0.82, top_score)), 2),
            "freshness": freshness_mode,
            "warnings": warnings,
            "metadata": {
                "freshness_mode": freshness_mode,
                "search_type": search_type,
                "source_count": len(sources),
                "source_of_record_count": sum(1 for row in rows if bool(row.get("source_of_record"))),
                "verification_state": "verified" if top_score >= 0.7 else "partially_verified",
                "search_lite": True,
                "fast_search_escalation_recommended": top_score < 0.7,
                "confidence_reason": (
                    "Top quick-search source met the trust threshold."
                    if top_score >= 0.7
                    else "Confidence reduced because the top quick-search source was usable but not strong."
                ),
            },
        }

    def _build_version_answer(
        self,
        *,
        query: str,
        rows: List[Dict[str, Any]],
        freshness_mode: str,
        search_type: str,
    ) -> Dict[str, Any]:
        version_scores: Dict[str, float] = {}
        supporting_rows: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            version = str(row.get("version_candidate") or "").strip()
            if not version:
                continue
            weight = float(row.get("source_score") or 0.0)
            if row.get("source_of_record"):
                weight += 0.18
            if str(row.get("tier") or "").lower() == "trusted":
                weight += 0.05
            version_scores[version] = version_scores.get(version, 0.0) + weight
            supporting_rows.setdefault(version, []).append(row)

        if not version_scores:
            return self._build_no_result(
                query=query,
                query_kind="version_lookup",
                warnings=["Limited verification: no reliable version string appeared in strong source rows."],
                rows=rows,
            )

        best_version = max(version_scores, key=version_scores.get)
        support = sorted(
            supporting_rows.get(best_version, []),
            key=lambda item: float(item.get("source_score") or 0.0),
            reverse=True,
        )
        support_domains = {
            str(row.get("domain") or "").strip().lower()
            for row in support
            if str(row.get("domain") or "").strip()
        }
        source_of_record = [row for row in support if bool(row.get("source_of_record"))]
        technical_exact_support = [
            row
            for row in support
            if bool(row.get("entity_exact_match"))
            and str(row.get("domain") or "").strip().lower() in _TECHNICAL_PACKAGE_DOMAINS
        ]
        if not source_of_record and len(support_domains) < 2 and not technical_exact_support:
            return self._build_no_result(
                query=query,
                query_kind="version_lookup",
                warnings=[
                    "Limited verification: quick search did not find an official or source-of-record version result.",
                ],
                rows=rows,
            )

        ordered_rows = list(source_of_record) + [row for row in rows if row not in source_of_record]
        ordered_rows = ordered_rows[:5]
        subject = self._subject_label(query) or "this package"
        answer = f"The latest version I could verify for {subject} is {best_version}."
        key_points = [
            f"{str(row.get('title') or '').strip()}: {str(row.get('snippet') or '').strip()}".strip(": ")
            for row in ordered_rows[1:3]
            if str(row.get("snippet") or row.get("title") or "").strip()
        ]
        warnings: List[str] = []
        if not source_of_record:
            if technical_exact_support:
                warnings.append(
                    "Limited verification: this version is based on an exact package index entry, not a direct source-of-record page."
                )
            else:
                warnings.append("Limited verification: this version is based on corroborating results, not a direct source-of-record page.")
        confidence = 0.86 if source_of_record else (0.61 if technical_exact_support else 0.68)
        return {
            "mode": "fast_search",
            "answer": answer,
            "direct_answer": answer,
            "key_points": key_points,
            "sources": [str(row.get("link") or "").strip() for row in ordered_rows if str(row.get("link") or "").strip()],
            "confidence": confidence,
            "freshness": freshness_mode,
            "warnings": warnings,
            "metadata": {
                "freshness_mode": freshness_mode,
                "search_type": search_type,
                "source_count": len(ordered_rows),
                "source_of_record_count": len(source_of_record),
                "verification_state": "verified" if source_of_record else "partially_verified",
                "search_lite": True,
                "verified_value": best_version,
                "fast_search_escalation_recommended": not bool(source_of_record or technical_exact_support),
                "confidence_reason": (
                    "Version confirmed by a source-of-record result."
                    if source_of_record
                    else "Confidence reduced because the version came from exact package-index support, not the source-of-record page."
                    if technical_exact_support
                    else "Confidence reduced because the version was only corroborated by non-source-of-record results."
                ),
            },
        }

    def _query_kind(self, query: str) -> str:
        text = str(query or "").strip().lower()
        if self._is_version_lookup(text):
            return "version_lookup"
        if self._is_role_lookup(text):
            return "role_lookup"
        return "current_lookup"

    def _is_version_lookup(self, query: str) -> bool:
        text = str(query or "").strip().lower()
        return bool(_VERSION_HINT_RE.search(text) or re.search(r"\bwhat\s+version\b", text))

    def _is_role_lookup(self, query: str) -> bool:
        text = str(query or "").strip().lower()
        return bool(_ROLE_HINT_RE.search(text) or re.search(r"\bwho\s+is\b", text))

    def _build_query_plan(self, query: str, *, query_kind: str) -> List[str]:
        subject = self._subject_query_text(query)
        subject_slug = subject.replace(" ", "-")
        simple_subject = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,31}", subject))
        candidates = [str(query or "").strip()]
        if query_kind == "version_lookup" and subject:
            candidates.append(f"\"{subject}\" latest version")
            candidates.append(f"site:npmjs.com/package/{subject_slug} \"{subject}\"")
            if simple_subject:
                candidates.append(f"site:{subject_slug}.dev \"{subject}\" release")
            else:
                candidates.append(f"site:github.com \"{subject}\" releases")
        elif query_kind == "role_lookup" and subject:
            candidates.extend(
                [
                    f"\"{subject}\" official company leadership page",
                    f"\"{subject}\" official team page",
                ]
            )
        elif subject:
            candidates.append(f"\"{subject}\" official current update")

        ordered: List[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            cleaned = str(candidate or "").strip()
            key = cleaned.lower()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            ordered.append(cleaned)
        return ordered[:4]

    def _subject_label(self, query: str) -> str:
        parts = [
            token
            for token in re.findall(r"[a-z0-9][a-z0-9.+#-]*", str(query or "").lower())
            if token not in _CURRENT_LOOKUP_STOPWORDS and len(token) >= 2
        ]
        if not parts:
            return ""
        return " ".join(parts[:3]).title()

    def _subject_query_text(self, query: str) -> str:
        parts = [
            token
            for token in re.findall(r"[a-z0-9][a-z0-9.+#-]*", str(query or "").lower())
            if token not in _CURRENT_LOOKUP_STOPWORDS and len(token) >= 2
        ]
        if not parts:
            return ""
        return " ".join(parts[:3])

    def _subject_package_name(self, query: str) -> str:
        subject = self._subject_query_text(query).strip().lower()
        if not subject:
            return ""
        subject = _PACKAGE_ALIASES.get(subject, subject)
        if re.fullmatch(r"@[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,63}", subject):
            return subject
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", subject):
            return subject
        return ""

    def _subject_tokens(self, query: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]{2,}", str(query or "").lower())
            if token not in _CURRENT_LOOKUP_STOPWORDS
        }

    def _merge_rows(self, primary: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in list(primary or []) + list(extra or []):
            link = str((row or {}).get("link") or "").strip().lower()
            key = link or str((row or {}).get("title") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(dict(row or {}))
        return merged[:8]

    def _should_read_sources_for_verification(self, rows: List[Dict[str, Any]], *, query_kind: str) -> bool:
        if not rows:
            return False
        top = rows[0]
        top_score = float(top.get("source_score") or 0.0)
        verification_state = "verified" if top.get("source_of_record") and top_score >= 0.72 else "candidate"
        if query_kind in {"version_lookup", "role_lookup"}:
            return verification_state != "verified" or not bool(top.get("version_candidate"))
        return top_score < 0.72

    async def _read_source_pages(
        self,
        *,
        query: str,
        rows: List[Dict[str, Any]],
        web_extract_fn: WebExtractCallable,
        query_kind: str,
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        candidates = self._select_extract_candidates(rows, query_kind=query_kind)
        summary = {
            "attempted_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "source_reading_used": False,
            "extraction_recovery_used": False,
        }
        if not candidates:
            return list(rows or []), summary

        by_link = {
            str(row.get("link") or "").strip().lower(): dict(row or {})
            for row in rows or []
            if str(row.get("link") or "").strip()
        }
        for row in candidates:
            link = str(row.get("link") or "").strip()
            if not link:
                continue
            summary["attempted_count"] += 1
            try:
                extracted = await web_extract_fn(url=link, timeout=8, max_chars=5000)
            except Exception as exc:
                extracted = {"success": False, "error": str(exc)}
            if not bool((extracted or {}).get("success")):
                summary["failed_count"] += 1
                lower_link = link.lower()
                if lower_link in by_link:
                    by_link[lower_link]["extract_error"] = str((extracted or {}).get("error") or "extract_failed")
                    snippet = str(by_link[lower_link].get("snippet") or by_link[lower_link].get("search_snippet") or "").strip()
                    if len(snippet) >= 80:
                        by_link[lower_link]["extraction_failed"] = True
                        by_link[lower_link]["snippet_recovery_used"] = True
                        summary["extraction_recovery_used"] = True
                continue

            text = self._compact_extracted_text(str((extracted or {}).get("text") or ""))
            if not text:
                summary["failed_count"] += 1
                continue
            summary["success_count"] += 1
            summary["source_reading_used"] = True
            lower_link = link.lower()
            target = by_link.get(lower_link, dict(row or {}))
            search_snippet = str(target.get("search_snippet") or target.get("raw_snippet") or target.get("snippet") or "").strip()
            target["raw_snippet"] = search_snippet
            target["search_snippet"] = search_snippet
            target["extracted_text"] = text
            target["extracted_text_used"] = True
            target["extract_quality_score"] = float((extracted or {}).get("quality_score") or 0.0)
            target["published_at"] = str((extracted or {}).get("published_at") or target.get("published_at") or "").strip()
            if (extracted or {}).get("title") and not str(target.get("title") or "").strip():
                target["title"] = str((extracted or {}).get("title") or "").strip()
            merged = f"{search_snippet} Extract: {text}".strip()
            target["snippet"] = merged[:1800] if merged else text[:1800]
            by_link[lower_link] = target

        merged_rows: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows or []:
            link = str(row.get("link") or "").strip().lower()
            if link and link in by_link:
                out = by_link[link]
            else:
                out = dict(row or {})
            key = link or str(out.get("title") or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            merged_rows.append(out)
        return merged_rows, summary

    def _select_extract_candidates(self, rows: List[Dict[str, Any]], *, query_kind: str) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for row in rows or []:
            link = str(row.get("link") or "").strip()
            if not link.startswith(("http://", "https://")):
                continue
            domain = str(row.get("domain") or self._domain(link)).strip().lower()
            if domain in _LOW_QUALITY_DOMAINS:
                continue
            if query_kind == "version_lookup":
                if (
                    bool(row.get("source_of_record"))
                    or bool(row.get("entity_exact_match"))
                    or bool(row.get("version_candidate"))
                    or float(row.get("source_score") or 0.0) >= 0.55
                ):
                    selected.append(row)
            elif query_kind == "role_lookup":
                if bool(row.get("source_of_record")) or float(row.get("query_match_score") or 0.0) >= 0.28:
                    selected.append(row)
            elif float(row.get("source_score") or 0.0) >= 0.5:
                selected.append(row)
            if len(selected) >= 3:
                break
        return selected

    def _compact_extracted_text(self, text: str) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(compact) < 40:
            return ""
        return compact[:1400]

    def _has_source_of_record(self, rows: List[Dict[str, Any]], *, query: str) -> bool:
        return any(self._is_source_of_record(row, query=query) for row in rows or [])

    def _has_confident_fast_answer(self, rows: List[Dict[str, Any]], *, query_kind: str) -> bool:
        if not rows:
            return False
        top = rows[0]
        top_score = float(top.get("source_score") or 0.0)
        if query_kind == "version_lookup":
            return bool(top.get("source_of_record")) and bool(top.get("version_candidate")) and top_score >= 0.72
        if query_kind == "role_lookup":
            return bool(top.get("source_of_record")) and top_score >= 0.72
        return top_score >= 0.74 and float(top.get("query_match_score") or 0.0) >= 0.45

    def _rank_rows(self, rows: List[Dict[str, Any]], *, query: str) -> List[Dict[str, Any]]:
        subject_tokens = self._subject_tokens(query)
        version_lookup = self._is_version_lookup(query)
        role_lookup = self._is_role_lookup(query)
        enriched_rows: List[Dict[str, Any]] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            link = str(row.get("link") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            if not (title or snippet or link):
                continue
            domain = self._domain(link)
            path = urlparse(link).path.lower() if link else ""
            source_of_record = self._is_source_of_record(row, query=query)
            tier = self._infer_tier(
                row=row,
                domain=domain,
                query=query,
                source_of_record=source_of_record,
            )
            query_match = self._query_match_score(subject_tokens, f"{title} {snippet} {domain}")
            freshness_score = 0.45 if re.search(r"\b(latest|current|stable|today|now)\b", f"{title} {snippet}", re.I) else 0.12
            version_candidate = self._extract_version(f"{title} {snippet}") if version_lookup else ""
            source_entity_match = self._source_entity_matches_query(domain=domain, path=path, query=query)
            entity_exact_match = self._row_entity_exact_match(
                query=query,
                domain=domain,
                path=path,
                title=title,
            )
            if version_lookup and version_candidate and not self._supports_subject_version(
                query=query,
                subject_tokens=subject_tokens,
                title=title,
                snippet=snippet,
                version_candidate=version_candidate,
            ) and not (domain in _SOURCE_OF_RECORD_DOMAINS and source_entity_match):
                version_candidate = ""
            if not self._row_is_relevant(
                subject_tokens=subject_tokens,
                title=title,
                snippet=snippet,
                link=link,
                query_match=query_match,
                version_lookup=version_lookup,
                role_lookup=role_lookup,
                source_of_record=source_of_record,
                version_candidate=version_candidate,
            ):
                continue
            enriched = dict(row or {})
            enriched.update(
                {
                    "provider": domain,
                    "domain": domain,
                    "tier": tier,
                    "freshness_score": freshness_score,
                    "source_of_record": source_of_record,
                    "entity_exact_match": entity_exact_match,
                    "query_match_score": round(query_match, 3),
                    "version_candidate": version_candidate,
                }
            )
            enriched = self._scorer.score(enriched)
            score = float(enriched.get("source_score") or 0.0)
            if source_of_record:
                score += 0.22
            if version_lookup and version_candidate:
                score += 0.24
            if version_lookup and entity_exact_match and domain in _TECHNICAL_PACKAGE_DOMAINS:
                score += 0.12
            if role_lookup and tier == "official":
                score += 0.2
            if query_match < 0.16:
                score -= 0.28
            if domain in _LOW_QUALITY_DOMAINS:
                score -= 0.3
            if version_lookup and domain in _WEAK_TECH_DOMAINS:
                score -= 0.18
            enriched["source_score"] = round(max(0.0, min(1.0, score)), 3)
            enriched_rows.append(enriched)

        ranked = sorted(
            enriched_rows,
            key=lambda item: (
                bool(item.get("source_of_record")),
                float(item.get("source_score") or 0.0),
                float(item.get("query_match_score") or 0.0),
            ),
            reverse=True,
        )
        return self._scorer.diversify(ranked, max_per_domain=2)[:5]

    def _row_is_relevant(
        self,
        *,
        subject_tokens: set[str],
        title: str,
        snippet: str,
        link: str,
        query_match: float,
        version_lookup: bool,
        role_lookup: bool,
        source_of_record: bool,
        version_candidate: str,
    ) -> bool:
        if not subject_tokens:
            return True
        text = f"{title} {snippet} {link}".lower()
        if source_of_record and query_match >= 0.18:
            return True
        if version_lookup:
            if query_match < 0.34:
                return False
            return bool(version_candidate or _VERSION_RESULT_HINT_RE.search(text))
        if role_lookup:
            if query_match < 0.28:
                return False
            return bool(_ROLE_RESULT_HINT_RE.search(text))
        return query_match >= 0.22

    def _supports_subject_version(
        self,
        *,
        query: str,
        subject_tokens: set[str],
        title: str,
        snippet: str,
        version_candidate: str,
    ) -> bool:
        if not version_candidate:
            return False
        text = f"{title} {snippet}".lower()
        version_pattern = re.escape(version_candidate)
        subject_phrase = self._subject_query_text(query)
        if subject_phrase:
            subject_pattern = re.escape(subject_phrase)
            if re.search(rf"(?<![a-z0-9]){subject_pattern}(?![a-z0-9])[^.\n]{{0,32}}{version_pattern}", text):
                return True
            if re.search(rf"{version_pattern}[^.\n]{{0,32}}(?<![a-z0-9]){subject_pattern}(?![a-z0-9])", text):
                return True
        for token in subject_tokens:
            token_pattern = re.escape(token)
            if re.search(rf"(?<![a-z0-9]){token_pattern}(?![a-z0-9])[^.\n]{{0,20}}{version_pattern}", text):
                return True
            if re.search(rf"{version_pattern}[^.\n]{{0,20}}(?<![a-z0-9]){token_pattern}(?![a-z0-9])", text):
                return True
        return False

    def _infer_tier(
        self,
        *,
        row: Dict[str, Any],
        domain: str,
        query: str,
        source_of_record: bool,
    ) -> str:
        if source_of_record:
            return "official"
        if domain in _LOW_QUALITY_DOMAINS:
            return "other"
        text = f"{str(row.get('title') or '')} {str(row.get('snippet') or '')} {str(row.get('link') or '')}".lower()
        subject_tokens = self._subject_tokens(query)
        if any(self._token_present(token, domain) for token in subject_tokens) and re.search(r"\b(docs|documentation|release|releases|changelog|blog|about|leadership|team)\b", text):
            return "official"
        if domain in _TRUSTED_REPORTING_DOMAINS:
            return "trusted"
        if domain in _SOURCE_OF_RECORD_DOMAINS:
            return "trusted"
        if domain in _WEAK_TECH_DOMAINS:
            return "other"
        return "other"

    def _is_source_of_record(self, row: Dict[str, Any], *, query: str) -> bool:
        link = str(row.get("link") or "").strip().lower()
        title = str(row.get("title") or "").strip().lower()
        snippet = str(row.get("snippet") or "").strip().lower()
        domain = self._domain(link)
        path = urlparse(link).path.lower() if link else ""
        subject_tokens = list(self._subject_tokens(query))
        if not subject_tokens:
            return False
        text = f"{domain} {path} {title} {snippet}"
        if self._is_version_lookup(query):
            version_candidate = self._extract_version(f"{title} {snippet}")
            if domain in _SOURCE_OF_RECORD_DOMAINS and self._source_entity_matches_query(domain=domain, path=path, query=query):
                return bool(version_candidate or _VERSION_RESULT_HINT_RE.search(text))
            if any(self._token_present(token, domain) for token in subject_tokens):
                return bool(version_candidate or re.search(r"\b(docs|release|releases|changelog|version|blog|announce)\b", text))
            return False
        if self._is_role_lookup(query):
            if "linkedin.com/company/" in link and any(self._token_present(token, text) for token in subject_tokens):
                return True
            if any(self._token_present(token, domain) for token in subject_tokens):
                return bool(re.search(r"\b(leadership|management|team|about|founder|ceo|president|governor)\b", text))
        return False

    def _extract_version(self, text: str) -> str:
        for candidate in _VERSION_RE.findall(str(text or "")):
            cleaned = str(candidate or "").strip().lower().lstrip("v")
            if not cleaned or cleaned.count(".") < 1:
                continue
            head = cleaned.split(".", 1)[0]
            if head.isdigit() and int(head) >= 1900:
                continue
            return cleaned
        return ""

    def _query_match_score(self, subject_tokens: set[str], text: str) -> float:
        hay = str(text or "").lower()
        if not subject_tokens:
            return 0.4
        overlap = sum(1 for token in subject_tokens if self._token_present(token, hay))
        return min(1.0, overlap / max(1, len(subject_tokens)))

    def _token_present(self, token: str, text: str) -> bool:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(str(token or '').lower())}(?![a-z0-9])", str(text or "").lower()))

    def _source_entity_matches_query(self, *, domain: str, path: str, query: str) -> bool:
        entity = self._extract_source_entity(domain=domain, path=path)
        if not entity:
            return any(self._token_present(token, f"{domain} {path}") for token in self._subject_tokens(query))
        normalized_entity = self._normalize_entity_name(entity)
        if not normalized_entity:
            return False
        return normalized_entity in self._subject_entity_candidates(query)

    def _extract_source_entity(self, *, domain: str, path: str) -> str:
        parts = [segment for segment in str(path or "").strip("/").split("/") if segment]
        if domain == "npmjs.com" and len(parts) >= 2 and parts[0] == "package":
            return parts[1]
        if domain == "pypi.org" and len(parts) >= 2 and parts[0] == "project":
            return parts[1]
        if domain == "github.com" and len(parts) >= 2:
            return parts[1]
        if domain == "packagist.org" and len(parts) >= 2 and parts[0] == "packages":
            return parts[-1]
        if domain == "crates.io" and len(parts) >= 2 and parts[0] == "crates":
            return parts[1]
        return ""

    def _subject_entity_candidates(self, query: str) -> set[str]:
        subject = self._subject_query_text(query)
        tokens = list(self._subject_tokens(query))
        candidates = {self._normalize_entity_name(subject)}
        candidates.update(self._normalize_entity_name(token) for token in tokens)
        if subject:
            candidates.add(self._normalize_entity_name(subject.replace(" ", "-")))
            candidates.add(self._normalize_entity_name(subject.replace(" ", "")))
        return {candidate for candidate in candidates if candidate}

    def _row_entity_exact_match(self, *, query: str, domain: str, path: str, title: str) -> bool:
        if self._source_entity_matches_query(domain=domain, path=path, query=query):
            return True
        subject = self._subject_query_text(query)
        if not subject:
            return False
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(subject)}(?![a-z0-9])(?:\s*@|\s+v|\b)",
                str(title or "").lower(),
            )
        )

    def _normalize_entity_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def _domain(self, link: str) -> str:
        try:
            return str(urlparse(str(link or "")).netloc or "").lower().replace("www.", "")
        except Exception:
            return ""

    def _build_no_result(
        self,
        *,
        query: str,
        query_kind: str,
        warnings: List[str] | None = None,
        rows: List[Dict[str, Any]] | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        source_rows = list(rows or [])[:5]
        best = source_rows[0] if source_rows else {}
        best_title = str(best.get("title") or "").strip()
        best_snippet = str(best.get("snippet") or "").strip()
        best_link = str(best.get("link") or "").strip()
        best_candidate = ": ".join(part for part in [best_title, best_snippet] if part)
        key_points = [
            f"{str(row.get('title') or '').strip()}: {str(row.get('snippet') or '').strip()}".strip(": ")
            for row in source_rows[:3]
            if str(row.get("snippet") or row.get("title") or "").strip()
        ]
        if query_kind == "version_lookup":
            if best_candidate:
                answer = (
                    "I could not verify a reliable current version from official or source-of-record results right now. "
                    f"The strongest quick-search candidate I found was: {best_candidate}. "
                    "Treat this as unverified until an official package, release, or docs source confirms it."
                )
            else:
                answer = (
                    "I could not verify a reliable current version from official or source-of-record results right now. "
                    "Search Lite did not find a usable candidate source, so this needs a retry or official-source-only lookup."
                )
        elif query_kind == "role_lookup":
            if best_candidate:
                answer = (
                    "I could not verify the current role reliably from a quick search alone. "
                    f"The strongest candidate source I found was: {best_candidate}. "
                    "Treat this as unverified until an official company or company-controlled source confirms it."
                )
            else:
                answer = (
                    "I could not verify the current role reliably from a quick search alone. "
                    "Search Lite did not find a usable candidate source, so this needs an official-source lookup."
                )
        else:
            if best_candidate:
                answer = (
                    "I could not verify a reliable live result strongly enough to answer as fact. "
                    f"The strongest quick-search candidate I found was: {best_candidate}. "
                    "Treat this as uncertain unless stronger sources confirm it."
                )
            else:
                answer = (
                    "I could not verify a reliable live result right now. "
                    "Search Lite did not find a usable candidate source, so retry or narrow the entity/date."
                )
        meta = {
            "freshness_mode": "current_lookup",
            "search_lite": True,
            "query": str(query or "").strip(),
            "query_kind": query_kind,
            "verification_state": "not_verified",
            "source_count": len(source_rows),
            "source_of_record_count": sum(1 for row in source_rows if bool(row.get("source_of_record"))),
            "cache_status": "miss",
            "fast_search_escalation_recommended": query_kind in {"version_lookup", "role_lookup"},
            "confidence_reason": (
                "Confidence reduced because Search Lite found candidate source rows but no official or source-of-record confirmation."
                if source_rows
                else "Confidence reduced because Search Lite found no usable source rows."
            ),
            "best_candidate_link": best_link,
        }
        meta.update(dict(metadata or {}))
        return {
            "mode": "fast_search",
            "answer": answer,
            "direct_answer": answer,
            "key_points": key_points,
            "sources": [str(row.get("link") or "").strip() for row in source_rows if str(row.get("link") or "").strip()],
            "confidence": 0.22,
            "freshness": "current_lookup",
            "warnings": list(warnings or ["Limited verification: live search returned no reliable results."]),
            "metadata": meta,
            "raw_rows": source_rows,
        }
