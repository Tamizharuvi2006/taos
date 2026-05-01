"""Firestore-oriented memory schema service for TAOS learning artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from typing import Any, Dict, List, Optional

from taos.config.settings import get_settings
from taos.core.documents.embedding_service import EmbeddingService
from taos.core.persistence.runtime_status import build_persistence_status
from taos.infra.persistence.firebase_store import FirestoreStore
from taos.infra.persistence.store import InMemoryStore, StorageBackend


class FirestoreMemorySchema:
    """
    User-scoped schema facade for:
    feedback, plans, agent memory, tool stats, executions.

    Falls back to in-memory store when Firestore is unavailable.
    """

    def __init__(self, store: Optional[StorageBackend] = None) -> None:
        self._settings = get_settings()
        self._store = store or self._build_store()
        self._embedder = EmbeddingService(dimensions=96)

    @property
    def store(self) -> StorageBackend:
        return self._store

    def _build_store(self) -> StorageBackend:
        status = build_persistence_status(settings=self._settings, check_runtime=True)
        if status.get("production_blocking"):
            raise RuntimeError(f"Persistence is not production-ready: {status.get('fallback_reason') or 'unknown_reason'}")
        preferred = getattr(self._settings, "storage_backend", "memory").lower()
        if preferred in {"firebase", "firestore"} and status.get("persistence_mode") == "firestore":
            firestore = FirestoreStore()
            if firestore.is_available:
                return firestore
        return InMemoryStore()

    async def upsert_feedback(
        self,
        user_id: str,
        query: str,
        bad_answer: str,
        corrected_answer: str,
        confidence: float,
        tags: Optional[List[str]] = None,
        ttl_days: Optional[int] = None,
    ) -> str:
        now = time.time()
        max_days = int(ttl_days or getattr(self._settings, "feedback_ttl_days", 45))
        query_hash = self._sha1(query)
        feedback_id = f"fb_{query_hash}"
        doc = {
            "id": feedback_id,
            "query": query,
            "query_hash": query_hash,
            "bad_answer": bad_answer,
            "corrected_answer": corrected_answer,
            "confidence": max(0.0, min(1.0, confidence)),
            "tags": tags or [],
            "created_at": now,
            "expires_at": now + (max_days * 86400),
            "usage_count": 0,
        }
        await self._store.set("feedback", feedback_id, doc, user_id=user_id)
        return feedback_id

    async def store_plan(
        self,
        user_id: str,
        goal: str,
        plan_steps: List[Dict[str, Any]],
        outcome: str,
        confidence: float,
        cost: float,
        latency: float,
        failure_reason: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        now = time.time()
        goal_hash = self._sha1(goal)
        plan_id = f"plan_{int(now)}_{goal_hash[:8]}"
        score = self._plan_score(outcome, confidence, cost, latency)
        doc = {
            "id": plan_id,
            "goal": goal,
            "goal_hash": goal_hash,
            "plan_steps": plan_steps,
            "outcome": outcome,
            "confidence": max(0.0, min(1.0, confidence)),
            "cost": max(0.0, cost),
            "latency": max(0.0, latency),
            "score": score,
            "failure_reason": failure_reason,
            "tags": tags or [],
            "created_at": now,
        }
        await self._store.set("plans", plan_id, doc, user_id=user_id)
        await self._prune_collection(
            collection="plans",
            user_id=user_id,
            max_records=int(self._settings.max_plan_history_records),
        )
        return plan_id

    async def store_agent_memory(
        self,
        user_id: str,
        agent_name: str,
        observation: str,
        outcome: str,
        confidence: float,
        tags: Optional[List[str]] = None,
    ) -> str:
        now = time.time()
        memory_id = f"am_{agent_name}_{int(now)}_{self._sha1(observation)[:6]}"
        doc = {
            "id": memory_id,
            "agent_name": agent_name,
            "observation": observation,
            "outcome": outcome,
            "confidence": max(0.0, min(1.0, confidence)),
            "tags": tags or [],
            "created_at": now,
        }
        await self._store.set("agent_memory", memory_id, doc, user_id=user_id)
        return memory_id

    async def update_tool_stats(
        self,
        user_id: str,
        tool_name: str,
        success: bool,
        latency: float,
        cost: float,
    ) -> None:
        now = time.time()
        key = f"tool_{tool_name}"
        existing = await self._store.get("tool_stats", key, user_id=user_id) or {}
        success_count = int(existing.get("success_count", 0))
        failure_count = int(existing.get("failure_count", 0))
        total_latency = float(existing.get("total_latency", 0.0))
        total_cost = float(existing.get("total_cost", 0.0))
        calls = max(0, success_count + failure_count)

        if success:
            success_count += 1
        else:
            failure_count += 1
        total_latency += max(0.0, latency)
        total_cost += max(0.0, cost)
        calls = success_count + failure_count

        avg_latency = total_latency / calls if calls else 0.0
        avg_cost = total_cost / calls if calls else 0.0
        success_rate = success_count / calls if calls else 0.0
        score = success_rate - min(1.0, avg_latency / 5000.0) * 0.2

        await self._store.set(
            "tool_stats",
            key,
            {
                "tool_name": tool_name,
                "success_count": success_count,
                "failure_count": failure_count,
                "avg_latency": avg_latency,
                "avg_cost": avg_cost,
                "success_rate": success_rate,
                "score": score,
                "last_used": now,
                "total_latency": total_latency,
                "total_cost": total_cost,
            },
            user_id=user_id,
        )

    async def log_execution(
        self,
        user_id: str,
        execution_id: str,
        goal: str,
        steps: List[Dict[str, Any]],
        agents_used: List[str],
        latency: float,
        cost: float,
        success: bool,
        critic_flagged: bool = False,
        debate_triggered: bool = False,
    ) -> None:
        should_store = self._should_store_execution(
            success=success,
            latency=latency,
            critic_flagged=critic_flagged,
            debate_triggered=debate_triggered,
        )
        if not should_store:
            return
        await self._store.set(
            "executions",
            execution_id,
            {
                "id": execution_id,
                "goal": goal,
                "steps": self._truncate_obj(steps),
                "agents_used": agents_used,
                "latency": max(0.0, latency),
                "cost": max(0.0, cost),
                "success": bool(success),
                "created_at": time.time(),
                "critic_flagged": bool(critic_flagged),
                "debate_triggered": bool(debate_triggered),
            },
            user_id=user_id,
        )
        await self._prune_collection(
            collection="executions",
            user_id=user_id,
            max_records=int(self._settings.max_execution_history_records),
        )

    async def store_research_profile(
        self,
        *,
        user_id: str,
        query: str,
        answer: str,
        source_rows: Optional[List[Dict[str, Any]]] = None,
        agreement: Optional[Dict[str, Any]] = None,
        related_questions: Optional[List[str]] = None,
        entity_hints: Optional[List[str]] = None,
    ) -> str:
        now = time.time()
        clean_query = str(query or "").strip()
        clean_answer = str(answer or "").strip()
        if not clean_query or not clean_answer:
            return ""
        ttl_hours = max(6, int(getattr(self._settings, "research_profile_ttl_hours", 72)))
        source_rows = list(source_rows or [])[:8]
        agreement = dict(agreement or {})
        related = [str(q).strip() for q in (related_questions or []) if str(q).strip()][:6]
        entity = [str(v).strip() for v in (entity_hints or []) if str(v).strip()][:8]
        text_for_embedding = f"{clean_query}\n{clean_answer[:2200]}"
        embedding = self._embedder.embed_text(text_for_embedding)
        profile_id = f"rp_{self._sha1(clean_query)[:10]}_{int(now)}"
        doc = {
            "id": profile_id,
            "query": clean_query,
            "query_hash": self._sha1(clean_query),
            "answer": clean_answer,
            "source_rows": source_rows,
            "agreement": agreement,
            "related_questions": related,
            "entity_hints": entity,
            "embedding": embedding,
            "created_at": now,
            "expires_at": now + (ttl_hours * 3600),
        }
        await self._store.set("research_profiles", profile_id, doc, user_id=user_id)
        await self._prune_collection(
            collection="research_profiles",
            user_id=user_id,
            max_records=int(getattr(self._settings, "research_profile_max_records", 500)),
        )
        return profile_id

    async def retrieve_research_profiles(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 3,
        min_similarity: Optional[float] = None,
        max_age_hours: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clean_query = str(query or "").strip()
        if not clean_query:
            return []
        docs = await self._store.list(
            "research_profiles",
            user_id=user_id,
            limit=max(50, int(limit) * 20),
        )
        if not docs:
            return []
        query_embedding = self._embedder.embed_text(clean_query)
        now = time.time()
        threshold = float(
            min_similarity
            if min_similarity is not None
            else getattr(self._settings, "research_cache_min_similarity", 0.58)
        )
        age_limit_hours = int(
            max_age_hours if max_age_hours is not None else getattr(self._settings, "research_cache_max_age_hours", 120)
        )
        age_limit_seconds = max(1, age_limit_hours) * 3600
        ranked: List[Dict[str, Any]] = []
        for row in docs:
            if not isinstance(row, dict):
                continue
            expires_at = float(row.get("expires_at") or 0.0)
            created_at = float(row.get("created_at") or 0.0)
            if expires_at and expires_at < now:
                continue
            if created_at and (now - created_at) > age_limit_seconds:
                continue
            emb = row.get("embedding")
            if not isinstance(emb, list) or not emb:
                continue
            sim = self._cosine_similarity(query_embedding, emb)
            if sim < threshold:
                continue
            scored = dict(row)
            scored["retrieval_score"] = round(sim, 4)
            ranked.append(scored)
        ranked.sort(
            key=lambda item: (
                float(item.get("retrieval_score") or 0.0),
                float(item.get("created_at") or 0.0),
            ),
            reverse=True,
        )
        return ranked[: max(1, int(limit))]

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        dot = sum(float(a[i]) * float(b[i]) for i in range(n))
        norm_a = math.sqrt(sum(float(v) * float(v) for v in a[:n]))
        norm_b = math.sqrt(sum(float(v) * float(v) for v in b[:n]))
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))

    def _sha1(self, text: str) -> str:
        return hashlib.sha1((text or "").strip().lower().encode("utf-8")).hexdigest()

    def _plan_score(self, outcome: str, confidence: float, cost: float, latency: float) -> float:
        base = 1.0 if outcome == "success" else -0.3
        return base + (confidence * 0.6) - (cost * 0.2) - (latency / 20000.0)

    def _should_store_execution(
        self,
        *,
        success: bool,
        latency: float,
        critic_flagged: bool,
        debate_triggered: bool,
    ) -> bool:
        if not success:
            return True
        if latency > float(self._settings.execution_store_latency_threshold_ms):
            return True
        if critic_flagged or debate_triggered:
            return True
        return random.random() < float(self._settings.execution_sampling_rate)

    def _truncate_obj(self, value: Any) -> Any:
        max_bytes = max(1024, int(self._settings.max_stored_field_bytes))
        try:
            raw = json.dumps(value, ensure_ascii=False)
        except Exception:
            raw = str(value)
        encoded = raw.encode("utf-8")
        if len(encoded) <= max_bytes:
            return value
        truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return {
            "truncated": True,
            "preview": truncated,
            "original_size_bytes": len(encoded),
        }

    async def _prune_collection(self, collection: str, user_id: str, max_records: int) -> None:
        if max_records <= 0:
            return
        docs = await self._store.list(collection, user_id=user_id, limit=max_records + 2000)
        if len(docs) <= max_records:
            return
        docs.sort(key=lambda d: float(d.get("created_at", 0) or 0), reverse=True)
        for stale in docs[max_records:]:
            stale_id = str(stale.get("id", "")).strip()
            if stale_id:
                await self._store.delete(collection, stale_id, user_id=user_id)
