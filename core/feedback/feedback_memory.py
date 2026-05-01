"""Feedback Memory Engine: stores and retrieves learning signals per user."""

from __future__ import annotations

import re
import time
import hashlib
from typing import Any, Dict, List, Optional
from uuid import uuid4

from taos.config.settings import get_settings
from taos.infra.persistence.firebase_store import FirestoreStore
from taos.infra.persistence.store import InMemoryStore, StorageBackend


class FeedbackMemoryEngine:
    """
    Persistent feedback memory for self-learning loops.

    Stores:
    - explicit user feedback (bad/corrected answers)
    - system failures (low confidence, critic failures)
    """

    COLLECTION = "feedback"

    def __init__(self, store: Optional[StorageBackend] = None) -> None:
        self._settings = get_settings()
        self._store = store or self._build_store()

    def _build_store(self) -> StorageBackend:
        preferred = getattr(self._settings, "storage_backend", "memory").lower()
        if preferred == "firebase":
            firebase = FirestoreStore()
            if firebase.is_available:
                return firebase
        return InMemoryStore()

    async def add_feedback(
        self,
        user_id: str,
        query: str,
        bad_answer: str,
        corrected_answer: str,
        tags: Optional[List[str]] = None,
        rating: int = -1,
        confidence: float = 0.9,
    ) -> str:
        """Store explicit user correction feedback."""
        await self._prune_old_feedback(user_id)
        existing_id = await self._find_duplicate_feedback(
            user_id=user_id,
            query=query,
            corrected_answer=corrected_answer,
            item_type="user_feedback",
        )
        now_ts = time.time()

        if existing_id:
            prev = await self._store.get(self.COLLECTION, existing_id, user_id=user_id) or {}
            occurrences = int(prev.get("occurrences", 1)) + 1
            await self._store.update(
                self.COLLECTION,
                existing_id,
                {
                    "bad_answer": bad_answer,
                    "corrected_answer": corrected_answer,
                    "tags": tags or prev.get("tags", []),
                    "rating": rating,
                    "confidence": max(float(prev.get("confidence", 0.0)), confidence),
                    "timestamp": now_ts,
                    "occurrences": occurrences,
                },
                user_id=user_id,
            )
            return existing_id

        feedback_id = f"fb_{uuid4().hex[:10]}"
        doc = {
            "id": feedback_id,
            "type": "user_feedback",
            "query": query,
            "query_hash": hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()[:16],
            "bad_answer": bad_answer,
            "corrected_answer": corrected_answer,
            "tags": tags or [],
            "rating": rating,
            "confidence": confidence,
            "timestamp": now_ts,
            "created_at": now_ts,
            "expires_at": now_ts + (int(self._settings.feedback_ttl_days) * 86400),
            "usage_count": 0,
            "occurrences": 1,
            "dedupe_key": self._build_dedupe_key(query, corrected_answer, "user_feedback"),
        }
        await self._store.set(self.COLLECTION, feedback_id, doc, user_id=user_id)
        await self._enforce_max_records(user_id)
        return feedback_id

    async def add_system_failure(
        self,
        user_id: str,
        query: str,
        failed_answer: str,
        reason: str,
        confidence: float,
    ) -> str:
        """Store automatic failure signal for future planning guards."""
        await self._prune_old_feedback(user_id)
        existing_id = await self._find_duplicate_feedback(
            user_id=user_id,
            query=query,
            corrected_answer=reason,
            item_type="system_failure",
        )
        now_ts = time.time()

        if existing_id:
            prev = await self._store.get(self.COLLECTION, existing_id, user_id=user_id) or {}
            occurrences = int(prev.get("occurrences", 1)) + 1
            await self._store.update(
                self.COLLECTION,
                existing_id,
                {
                    "bad_answer": failed_answer,
                    "confidence": min(float(prev.get("confidence", 1.0)), confidence),
                    "timestamp": now_ts,
                    "occurrences": occurrences,
                },
                user_id=user_id,
            )
            return existing_id

        feedback_id = f"sys_{uuid4().hex[:10]}"
        doc = {
            "id": feedback_id,
            "type": "system_failure",
            "query": query,
            "query_hash": hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()[:16],
            "bad_answer": failed_answer,
            "corrected_answer": "",
            "reason": reason,
            "confidence": confidence,
            "tags": ["system_failure"],
            "rating": -1,
            "timestamp": now_ts,
            "created_at": now_ts,
            "expires_at": now_ts + (int(self._settings.feedback_ttl_days) * 86400),
            "usage_count": 0,
            "occurrences": 1,
            "dedupe_key": self._build_dedupe_key(query, reason, "system_failure"),
        }
        await self._store.set(self.COLLECTION, feedback_id, doc, user_id=user_id)
        await self._enforce_max_records(user_id)
        return feedback_id

    async def retrieve_relevant_feedback(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant feedback using lexical similarity + recency score.
        """
        await self._prune_old_feedback(user_id)
        docs = await self._store.list(self.COLLECTION, user_id=user_id, limit=500)
        if not docs:
            return []

        q_tokens = self._tokenize(query)
        scored: List[tuple[float, Dict[str, Any]]] = []
        now = time.time()
        max_age_days = max(1, int(self._settings.feedback_ttl_days))
        min_similarity = float(self._settings.feedback_similarity_threshold)
        min_confidence = float(self._settings.feedback_min_confidence)

        for doc in docs:
            conf = float(doc.get("confidence", 0.0) or 0.0)
            if conf < min_confidence:
                continue

            age_days = max(0.0, (now - float(doc.get("timestamp", now))) / 86400.0)
            if age_days > max_age_days:
                continue

            source_text = " ".join(
                [
                    str(doc.get("query", "")),
                    str(doc.get("bad_answer", "")),
                    " ".join(doc.get("tags", []) or []),
                ]
            )
            d_tokens = self._tokenize(source_text)
            overlap = self._jaccard(q_tokens, d_tokens)

            # Recency weight: newer feedback gets higher score.
            recency = 1.0 / (1.0 + age_days)
            rating_boost = 0.15 if int(doc.get("rating", 0)) < 0 else 0.0
            score = (overlap * 0.75) + (recency * 0.2) + rating_boost
            if overlap < min_similarity:
                continue
            scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Lightweight clustering: keep one strongest feedback per dedupe key.
        clustered: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for score, doc in scored:
            dedupe_key = str(doc.get("dedupe_key", ""))
            if dedupe_key and dedupe_key in seen_keys:
                continue
            if dedupe_key:
                seen_keys.add(dedupe_key)
            clustered.append(doc)
            doc_id = str(doc.get("id", "")).strip()
            if doc_id:
                await self._store.update(
                    self.COLLECTION,
                    doc_id,
                    {"usage_count": int(doc.get("usage_count", 0) or 0) + 1},
                    user_id=user_id,
                )
            if len(clustered) >= max(1, min(top_k, int(self._settings.feedback_max_context_items))):
                break

        return clustered

    def build_context_hints(self, feedback_docs: List[Dict[str, Any]]) -> List[str]:
        """Convert feedback documents into compact planning hints."""
        hints: List[str] = []
        for idx, doc in enumerate(feedback_docs, start=1):
            query = str(doc.get("query", ""))[:180]
            correction = str(doc.get("corrected_answer", ""))[:220]
            reason = str(doc.get("reason", ""))[:120]
            if correction:
                hints.append(
                    f"[Feedback {idx}] Prior similar query: {query}. Corrective guidance: {correction}"
                )
            elif reason:
                hints.append(
                    f"[Feedback {idx}] Prior failure on similar query: {query}. Failure reason: {reason}"
                )
        return hints

    def _tokenize(self, text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
        stop = {"the", "is", "a", "an", "and", "or", "to", "of", "in", "for", "with", "on"}
        return {t for t in tokens if len(t) > 2 and t not in stop}

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    def _build_dedupe_key(self, query: str, payload: str, item_type: str) -> str:
        """Stable key for deduplication/clustering."""
        base = f"{item_type}|{query.strip().lower()}|{payload.strip().lower()}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    async def _find_duplicate_feedback(
        self,
        user_id: str,
        query: str,
        corrected_answer: str,
        item_type: str,
    ) -> Optional[str]:
        """Find existing document with same dedupe key."""
        dedupe_key = self._build_dedupe_key(query, corrected_answer, item_type)
        docs = await self._store.list(self.COLLECTION, user_id=user_id, limit=300)
        for doc in docs:
            if doc.get("dedupe_key") == dedupe_key:
                return str(doc.get("id", ""))
        return None

    async def _prune_old_feedback(self, user_id: str) -> None:
        """TTL cleanup to avoid unbounded growth."""
        ttl_days = max(1, int(self._settings.feedback_ttl_days))
        cutoff = time.time() - (ttl_days * 86400)
        docs = await self._store.list(self.COLLECTION, user_id=user_id, limit=2000)
        for doc in docs:
            ts = float(doc.get("timestamp", 0) or 0)
            if ts and ts < cutoff:
                doc_id = str(doc.get("id", "")).strip()
                if doc_id:
                    await self._store.delete(self.COLLECTION, doc_id, user_id=user_id)

    async def _enforce_max_records(self, user_id: str) -> None:
        """Keep newest N records only."""
        max_records = max(100, int(self._settings.feedback_max_records))
        docs = await self._store.list(self.COLLECTION, user_id=user_id, limit=max_records + 1000)
        if len(docs) <= max_records:
            return
        docs.sort(key=lambda d: float(d.get("timestamp", 0) or 0), reverse=True)
        for stale in docs[max_records:]:
            doc_id = str(stale.get("id", "")).strip()
            if doc_id:
                await self._store.delete(self.COLLECTION, doc_id, user_id=user_id)
