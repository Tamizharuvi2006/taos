"""Adaptive grounded document ask service."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx

from taos.config.settings import get_settings
from taos.core.documents.document_intent_router import (
    ALLOWED_MARK_FORMATS,
    document_intent_router,
    normalize_mark_format,
    normalize_mode,
    normalize_output_format,
)
from taos.core.documents.repository import DocumentRepository
from taos.core.documents.utils import build_cache_key, keyword_tokens, utc_now_iso
from taos.core.tools.builtin.retrieve_chunks import retrieve_chunks
from taos.core.tools.builtin.validate_answer import validate_answer
from taos.infra.logging.logger import TAOSLogger

_logger = TAOSLogger(name="taos.documents.ask")


class DocumentAskService:
    def __init__(
        self,
        repository: Optional[DocumentRepository] = None,
        embedding_service: Any = None,
    ) -> None:
        self._settings = get_settings()
        self._repo = repository or DocumentRepository()
        self._confidence_high_cutoff = 0.78
        self._confidence_medium_cutoff = 0.55

    async def ask(
        self,
        user_id: str,
        doc_ids: Sequence[str],
        question: str,
        *,
        mode: Optional[str] = None,
        mark_format: Optional[str] = None,
        unit_hint: Optional[str] = None,
        output_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        prep = await self._prepare_context(
            user_id=user_id,
            doc_ids=doc_ids,
            question=question,
            mode=mode,
            mark_format=mark_format,
            unit_hint=unit_hint,
            output_format=output_format,
        )
        if prep.get("cached_payload"):
            return dict(prep["cached_payload"])

        answer = await self._generate_answer(
            question=str(prep["question"]),
            retrieved_chunks=list(prep["retrieved_chunks"]),
            mode=str(prep["mode"]),
            mark_format=prep.get("mark_format"),
            output_format=prep.get("output_format"),
            unit_hint=prep.get("unit_hint"),
        )
        validation = await self._validate_by_mode(
            mode=str(prep["mode"]),
            answer=answer,
            retrieved_chunks=list(prep["retrieved_chunks"]),
            sources=list(prep["sources"]),
            output_format=prep.get("output_format"),
        )
        confidence, confidence_meta, warnings = self._apply_validation_policy(
            mode=str(prep["mode"]),
            base_confidence=float(prep["confidence"]),
            confidence_meta=dict(prep.get("confidence_meta") or {}),
            validation=validation,
        )
        merged_warnings = list(prep.get("warnings") or [])
        merged_warnings.extend([w for w in warnings if w not in merged_warnings])
        answer = self._append_grounding_notice(answer=answer, mode=str(prep["mode"]), validation=validation)

        payload = self._build_payload(
            answer=answer,
            mode=str(prep["mode"]),
            warnings=merged_warnings,
            sources=list(prep["sources"]),
            confidence=confidence,
            model_name=str(prep["model_name"]),
            retrieved_chunks=list(prep["retrieved_chunks"]),
            confidence_meta=confidence_meta,
            validation=validation,
            metadata_extras={
                "intent_detected": prep.get("intent_detected"),
                "mode_selected": prep.get("mode"),
                "mode_source": prep.get("mode_source"),
                "retrieval_profile": prep.get("retrieval_profile"),
                "retrieval_summary": prep.get("retrieval_summary"),
                "output_format": prep.get("output_format"),
                "scope_applied": prep.get("retrieval_scope"),
                "mark_format": prep.get("mark_format"),
                "unit_hint": prep.get("unit_hint"),
            },
            cached=False,
        )
        await self._persist_cache(
            user_id=str(prep["user_id"]),
            cache_key=str(prep["cache_key"]),
            normalized_doc_ids=list(prep["doc_ids"]),
            clean_question=str(prep["question"]),
            payload=payload,
            mode=str(prep["mode"]),
            mark_format=prep.get("mark_format"),
            output_format=prep.get("output_format"),
        )
        return payload

    async def stream(
        self,
        user_id: str,
        doc_ids: Sequence[str],
        question: str,
        *,
        mode: Optional[str] = None,
        mark_format: Optional[str] = None,
        unit_hint: Optional[str] = None,
        output_format: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        prep = await self._prepare_context(
            user_id=user_id,
            doc_ids=doc_ids,
            question=question,
            mode=mode,
            mark_format=mark_format,
            unit_hint=unit_hint,
            output_format=output_format,
        )
        if prep.get("cached_payload"):
            yield {"type": "final", "payload": dict(prep["cached_payload"])}
            return

        answer = ""
        streamed = False
        if self._settings.openrouter_api_key and prep["retrieved_chunks"]:
            try:
                async for chunk in self._stream_openrouter(
                    question=str(prep["question"]),
                    retrieved_chunks=list(prep["retrieved_chunks"]),
                    mode=str(prep["mode"]),
                    mark_format=prep.get("mark_format"),
                    output_format=prep.get("output_format"),
                    unit_hint=prep.get("unit_hint"),
                ):
                    if not chunk:
                        continue
                    answer += chunk
                    streamed = True
                    yield {"type": "partial", "text": answer}
            except Exception as exc:
                _logger.warning("documents.ask_stream_fallback", error=str(exc))
                streamed = False
                answer = ""

        if not streamed:
            answer = await self._generate_answer(
                question=str(prep["question"]),
                retrieved_chunks=list(prep["retrieved_chunks"]),
                mode=str(prep["mode"]),
                mark_format=prep.get("mark_format"),
                output_format=prep.get("output_format"),
                unit_hint=prep.get("unit_hint"),
            )
            if answer:
                yield {"type": "partial", "text": str(answer)}

        validation = await self._validate_by_mode(
            mode=str(prep["mode"]),
            answer=answer,
            retrieved_chunks=list(prep["retrieved_chunks"]),
            sources=list(prep["sources"]),
            output_format=prep.get("output_format"),
        )
        confidence, confidence_meta, warnings = self._apply_validation_policy(
            mode=str(prep["mode"]),
            base_confidence=float(prep["confidence"]),
            confidence_meta=dict(prep.get("confidence_meta") or {}),
            validation=validation,
        )
        merged_warnings = list(prep.get("warnings") or [])
        merged_warnings.extend([w for w in warnings if w not in merged_warnings])
        answer = self._append_grounding_notice(answer=answer, mode=str(prep["mode"]), validation=validation)
        payload = self._build_payload(
            answer=answer,
            mode=str(prep["mode"]),
            warnings=merged_warnings,
            sources=list(prep["sources"]),
            confidence=confidence,
            model_name=str(prep["model_name"]),
            retrieved_chunks=list(prep["retrieved_chunks"]),
            confidence_meta=confidence_meta,
            validation=validation,
            metadata_extras={
                "intent_detected": prep.get("intent_detected"),
                "mode_selected": prep.get("mode"),
                "mode_source": prep.get("mode_source"),
                "retrieval_profile": prep.get("retrieval_profile"),
                "retrieval_summary": prep.get("retrieval_summary"),
                "output_format": prep.get("output_format"),
                "scope_applied": prep.get("retrieval_scope"),
                "mark_format": prep.get("mark_format"),
                "unit_hint": prep.get("unit_hint"),
            },
            cached=False,
        )
        await self._persist_cache(
            user_id=str(prep["user_id"]),
            cache_key=str(prep["cache_key"]),
            normalized_doc_ids=list(prep["doc_ids"]),
            clean_question=str(prep["question"]),
            payload=payload,
            mode=str(prep["mode"]),
            mark_format=prep.get("mark_format"),
            output_format=prep.get("output_format"),
        )
        yield {"type": "final", "payload": payload}

    async def _prepare_context(
        self,
        *,
        user_id: str,
        doc_ids: Sequence[str],
        question: str,
        mode: Optional[str],
        mark_format: Optional[str],
        unit_hint: Optional[str],
        output_format: Optional[str],
    ) -> Dict[str, Any]:
        normalized_doc_ids = [str(d).strip() for d in doc_ids if str(d).strip()]
        if not normalized_doc_ids:
            raise ValueError("doc_ids cannot be empty")
        clean_question = str(question or "").strip()
        if not clean_question:
            raise ValueError("question cannot be empty")
        max_docs = max(1, int(self._settings.document_ask_max_docs))
        if len(normalized_doc_ids) > max_docs:
            raise ValueError(f"Maximum {max_docs} document IDs are allowed per request")

        missing_docs: List[str] = []
        not_ready_docs: List[str] = []
        for doc_id in normalized_doc_ids:
            doc = await self._repo.get_document(user_id=user_id, doc_id=doc_id)
            if not doc:
                missing_docs.append(doc_id)
                continue
            if str(doc.get("status") or "") != "ready":
                not_ready_docs.append(doc_id)
        if missing_docs:
            raise KeyError(f"Document not found: {', '.join(missing_docs)}")
        if not_ready_docs:
            raise RuntimeError(f"Document(s) not ready: {', '.join(not_ready_docs)}")

        warnings: List[str] = []
        requested_mode_raw = str(mode or "").strip().lower()
        requested_mode = normalize_mode(mode)
        if requested_mode_raw and not requested_mode:
            warnings.append("Requested mode is invalid. Falling back to automatic mode routing.")
        selected_mark = normalize_mark_format(mark_format)
        if str(mark_format or "").strip() and not selected_mark:
            warnings.append("Requested mark_format is invalid. Ignoring mark-based shaping.")
        selected_output = normalize_output_format(output_format)
        if str(output_format or "").strip() and not selected_output:
            warnings.append("Requested output_format is invalid. Using default presentation format.")
        selected_unit_hint = str(unit_hint or "").strip() or None

        routing = document_intent_router(
            clean_question,
            mode_override=requested_mode,
            hints={"mark_format": selected_mark, "unit_hint": selected_unit_hint, "output_format": selected_output},
        )
        selected_mode = str(routing.get("mode_selected") or "qa")
        mode_source = str(routing.get("mode_source") or "default")
        intent_detected = str(routing.get("intent_detected") or selected_mode)
        retrieval_profile, top_k = self._retrieval_plan_for_mode(selected_mode)
        retrieval_scope = {"unit_hint": selected_unit_hint}
        retrieval_query = clean_question if not selected_unit_hint else f"{clean_question} scoped to {selected_unit_hint}"

        model_name = self._settings.executor_model
        cache_key = build_cache_key(
            user_id=user_id,
            doc_ids=normalized_doc_ids,
            question=clean_question,
            mode=selected_mode,
            mark_format=selected_mark or "",
            output_format=selected_output or "",
            model=model_name,
        )
        cached = await self._repo.get_cache(user_id=user_id, cache_key=cache_key)
        if cached:
            cached_sources = list(cached.get("sources") or [])
            cached_confidence = float(cached.get("confidence") or 0.0)
            cached_tier, cached_reason = self._confidence_tier_and_reason(
                confidence=cached_confidence,
                score_meta={},
                retrieved_chunks=0,
                doc_coverage=0.0,
            )
            cached_warnings = list(cached.get("warnings") or [])
            merged_warnings = cached_warnings + [w for w in warnings if w not in cached_warnings]
            source_docs = sorted(
                {str(item.get("doc_id") or "").strip() for item in cached_sources if str(item.get("doc_id") or "").strip()}
            )
            return {
                "user_id": str(user_id),
                "doc_ids": normalized_doc_ids,
                "question": clean_question,
                "cache_key": cache_key,
                "model_name": model_name,
                "mode": str(cached.get("mode") or selected_mode),
                "mode_source": "cache",
                "intent_detected": intent_detected,
                "warnings": merged_warnings,
                "mark_format": selected_mark,
                "unit_hint": selected_unit_hint,
                "output_format": selected_output,
                "retrieval_profile": cached.get("retrieval_profile") or retrieval_profile,
                "retrieval_scope": retrieval_scope,
                "retrieved_chunks": [],
                "sources": [],
                "confidence": cached_confidence,
                "confidence_meta": {},
                "cached_payload": {
                    "answer": str(cached.get("answer") or ""),
                    "mode": str(cached.get("mode") or selected_mode),
                    "sources": cached_sources,
                    "confidence": cached_confidence,
                    "cached": True,
                    "warnings": merged_warnings,
                    "validation": dict(cached.get("validation") or {}),
                    "metadata": {
                        "cache_hit": True,
                        "cached_at": cached.get("created_at"),
                        "retrieval_mode": "hybrid_vector_keyword_rerank",
                        "retrieval_profile": cached.get("retrieval_profile") or retrieval_profile,
                        "retrieval_summary": dict(cached.get("retrieval_summary") or {}),
                        "retrieval_strength": str(cached.get("retrieval_strength") or "unknown"),
                        "confidence_tier": cached_tier,
                        "confidence_reason": cached_reason,
                        "source_count": len(cached_sources),
                        "source_docs": source_docs,
                        "grounding_level": "strong" if len(cached_sources) >= 3 else "moderate" if len(cached_sources) >= 1 else "weak",
                        "intent_detected": intent_detected,
                        "mode_selected": str(cached.get("mode") or selected_mode),
                        "mode_source": "cache",
                        "output_format": selected_output,
                        "scope_applied": retrieval_scope,
                        "mark_format": selected_mark,
                        "unit_hint": selected_unit_hint,
                        "validation_warning": bool(cached.get("validation_warning")),
                    },
                },
            }

        retrieval = await retrieve_chunks(
            query=retrieval_query,
            doc_ids=normalized_doc_ids,
            user_id=user_id,
            top_k=top_k,
            include_text=True,
        )
        if not bool(retrieval.get("success")):
            raise RuntimeError(str(retrieval.get("error") or "Chunk retrieval failed"))
        retrieved_chunks = list(retrieval.get("chunks") or [])
        retrieval_summary = dict(retrieval.get("summary") or {})
        if not retrieved_chunks:
            raise RuntimeError("No processed chunks are available for the selected document(s)")

        sources = [
            {
                "doc_id": str(row.get("doc_id") or ""),
                "chunk_id": str(row.get("chunk_id") or ""),
                "chunk_index": int(row.get("chunk_index") or 0),
                "page_start": row.get("page_start"),
                "page_end": row.get("page_end"),
                "score": round(float(row.get("score") or 0.0), 4),
            }
            for row in retrieved_chunks
        ]
        confidence, confidence_meta = self._compute_confidence(
            retrieved_chunks=retrieved_chunks,
            requested_doc_count=len(normalized_doc_ids),
        )
        confidence_meta["retrieval_summary"] = retrieval_summary
        return {
            "user_id": str(user_id),
            "doc_ids": normalized_doc_ids,
            "question": clean_question,
            "cache_key": cache_key,
            "model_name": model_name,
            "mode": selected_mode,
            "mode_source": mode_source,
            "intent_detected": intent_detected,
            "warnings": warnings,
            "mark_format": selected_mark,
            "unit_hint": selected_unit_hint,
            "output_format": selected_output,
            "retrieval_profile": retrieval_profile,
            "retrieval_scope": retrieval_scope,
            "retrieval_summary": retrieval_summary,
            "retrieved_chunks": retrieved_chunks,
            "sources": sources,
            "confidence": round(confidence, 3),
            "confidence_meta": confidence_meta,
            "cached_payload": None,
        }

    def _retrieval_plan_for_mode(self, mode: str) -> tuple[str, int]:
        if mode in {"qa", "mark_answers"}:
            return "focused", 5
        if mode in {"important_questions", "revision", "test_generation"}:
            return "broad", 6
        return "balanced", 8

    @staticmethod
    def _prompt_chunk_limit_for_mode(mode: str) -> int:
        if mode in {"important_questions", "mark_questions", "test_generation"}:
            return 6
        if mode == "mark_answers":
            return 5
        if mode == "revision":
            return 6
        return 8

    @staticmethod
    def _prompt_text_limit_for_mode(mode: str) -> int:
        if mode in {"important_questions", "mark_questions", "test_generation"}:
            return 650
        if mode == "mark_answers":
            return 780
        return 900

    @staticmethod
    def _is_exam_mode(mode: str) -> bool:
        return mode in {"important_questions", "mark_questions", "mark_answers", "test_generation"}

    def _build_payload(
        self,
        *,
        answer: str,
        mode: str,
        warnings: List[str],
        sources: List[Dict[str, Any]],
        confidence: float,
        model_name: str,
        retrieved_chunks: List[Dict[str, Any]],
        confidence_meta: Dict[str, Any],
        validation: Dict[str, Any],
        metadata_extras: Dict[str, Any],
        cached: bool,
    ) -> Dict[str, Any]:
        score_meta = self._summarize_score_meta(retrieved_chunks)
        confidence_tier = str(confidence_meta.get("confidence_tier") or self._confidence_tier(confidence))
        confidence_reason = str(confidence_meta.get("confidence_reason") or "Derived from retrieval score signals.")
        source_docs = sorted(
            {str(source.get("doc_id") or "").strip() for source in sources if str(source.get("doc_id") or "").strip()}
        )
        grounding_level = self._grounding_level(
            source_count=len(sources),
            avg_semantic=float(score_meta.get("avg_semantic") or 0.0),
            confidence_tier=confidence_tier,
        )
        return {
            "answer": str(answer or ""),
            "mode": mode,
            "sources": sources,
            "confidence": round(float(confidence), 3),
            "cached": bool(cached),
            "warnings": warnings,
            "validation": {
                "grounded": bool(validation.get("grounded")),
                "score": float(validation.get("score") or 0.0),
                "issues": list(validation.get("issues") or []),
                "supported_sentences": int(validation.get("supported_sentences") or 0),
                "unsupported_sentences": int(validation.get("unsupported_sentences") or 0),
            },
            "metadata": {
                "cache_hit": bool(cached),
                "retrieved_chunks": len(retrieved_chunks),
                "model": model_name,
                "retrieval_mode": "hybrid_vector_keyword_rerank",
                "retrieval_strength": str(confidence_meta.get("retrieval_strength") or "unknown"),
                "confidence_tier": confidence_tier,
                "confidence_reason": confidence_reason,
                "doc_coverage": round(float(confidence_meta.get("doc_coverage") or 0.0), 3),
                "source_count": len(sources),
                "source_docs": source_docs,
                "grounding_level": grounding_level,
                "validation_grounded": bool(validation.get("grounded")),
                "validation_score": round(float(validation.get("score") or 0.0), 3),
                "validation_issue_count": len(list(validation.get("issues") or [])),
                "validation_warning": not bool(validation.get("grounded")) or bool(validation.get("issues")),
                **score_meta,
                **metadata_extras,
            },
        }

    async def _persist_cache(
        self,
        *,
        user_id: str,
        cache_key: str,
        normalized_doc_ids: List[str],
        clean_question: str,
        payload: Dict[str, Any],
        mode: str,
        mark_format: Optional[str],
        output_format: Optional[str],
    ) -> None:
        await self._repo.set_cache(
            user_id=user_id,
            cache_key=cache_key,
            data={
                "doc_ids": normalized_doc_ids,
                "normalized_question": clean_question.lower(),
                "answer": payload["answer"],
                "mode": mode,
                "warnings": list(payload.get("warnings") or []),
                "sources": payload["sources"],
                "confidence": payload["confidence"],
                "validation": payload.get("validation") or {},
                "validation_warning": bool((payload.get("metadata") or {}).get("validation_warning")),
                "retrieval_profile": (payload.get("metadata") or {}).get("retrieval_profile"),
                "retrieval_summary": (payload.get("metadata") or {}).get("retrieval_summary") or {},
                "retrieval_strength": (payload.get("metadata") or {}).get("retrieval_strength") or "unknown",
                "mark_format": mark_format,
                "output_format": output_format,
                "model": payload.get("metadata", {}).get("model", self._settings.executor_model),
                "created_at": utc_now_iso(),
            },
        )

    @staticmethod
    def _summarize_score_meta(retrieved_chunks: List[Dict[str, Any]]) -> Dict[str, float]:
        if not retrieved_chunks:
            return {"avg_semantic": 0.0, "avg_lexical": 0.0, "avg_phrase": 0.0, "avg_fuzzy": 0.0}
        count = float(len(retrieved_chunks))
        return {
            "avg_semantic": round(sum(float((i.get("score_breakdown") or {}).get("semantic") or 0.0) for i in retrieved_chunks) / count, 3),
            "avg_lexical": round(sum(float((i.get("score_breakdown") or {}).get("lexical") or 0.0) for i in retrieved_chunks) / count, 3),
            "avg_phrase": round(sum(float((i.get("score_breakdown") or {}).get("phrase") or 0.0) for i in retrieved_chunks) / count, 3),
            "avg_fuzzy": round(sum(float((i.get("score_breakdown") or {}).get("fuzzy") or 0.0) for i in retrieved_chunks) / count, 3),
        }

    def _compute_confidence(self, *, retrieved_chunks: List[Dict[str, Any]], requested_doc_count: int) -> tuple[float, Dict[str, Any]]:
        if not retrieved_chunks:
            tier, reason = self._confidence_tier_and_reason(confidence=0.12, score_meta={}, retrieved_chunks=0, doc_coverage=0.0)
            return 0.12, {
                "confidence_tier": tier,
                "confidence_reason": reason,
                "doc_coverage": 0.0,
                "retrieved_chunks": 0,
                "retrieval_strength": "weak",
                "avg_score": 0.0,
                "top_score": 0.0,
            }
        score_meta = self._summarize_score_meta(retrieved_chunks)
        avg_score = sum(float(i.get("score") or 0.0) for i in retrieved_chunks) / float(len(retrieved_chunks))
        top_score = max(float(i.get("score") or 0.0) for i in retrieved_chunks)
        coverage = min(1.0, len(retrieved_chunks) / 5.0)
        docs_in_top = {str(i.get("doc_id") or "").strip() for i in retrieved_chunks if str(i.get("doc_id") or "").strip()}
        doc_coverage = min(1.0, len(docs_in_top) / float(max(1, int(requested_doc_count))))
        confidence = (
            (avg_score * 0.46)
            + (top_score * 0.20)
            + (float(score_meta.get("avg_semantic") or 0.0) * 0.16)
            + (float(score_meta.get("avg_lexical") or 0.0) * 0.07)
            + (coverage * 0.06)
            + (doc_coverage * 0.05)
        )
        if avg_score < 0.28 or float(score_meta.get("avg_semantic") or 0.0) < 0.20:
            confidence = min(confidence, 0.44)
        retrieval_strength = "weak"
        if avg_score >= 0.58 and float(score_meta.get("avg_semantic") or 0.0) >= 0.45 and doc_coverage >= 0.60:
            retrieval_strength = "strong"
        elif avg_score >= 0.35 and float(score_meta.get("avg_semantic") or 0.0) >= 0.25:
            retrieval_strength = "moderate"
        confidence = min(0.98, max(0.12, float(confidence)))
        tier, reason = self._confidence_tier_and_reason(
            confidence=confidence,
            score_meta=score_meta,
            retrieved_chunks=len(retrieved_chunks),
            doc_coverage=doc_coverage,
        )
        return confidence, {
            "confidence_tier": tier,
            "confidence_reason": reason,
            "doc_coverage": doc_coverage,
            "retrieved_chunks": len(retrieved_chunks),
            "retrieval_strength": retrieval_strength,
            "avg_score": round(avg_score, 4),
            "top_score": round(top_score, 4),
            "score_meta": score_meta,
        }

    def _confidence_tier(self, confidence: float) -> str:
        value = float(confidence or 0.0)
        if value >= self._confidence_high_cutoff:
            return "high"
        if value >= self._confidence_medium_cutoff:
            return "medium"
        return "low"

    def _confidence_tier_and_reason(
        self, *, confidence: float, score_meta: Dict[str, float], retrieved_chunks: int, doc_coverage: float
    ) -> tuple[str, str]:
        tier = self._confidence_tier(confidence)
        semantic = float(score_meta.get("avg_semantic") or 0.0)
        lexical = float(score_meta.get("avg_lexical") or 0.0)
        if tier == "high":
            return tier, f"Strong retrieval alignment across {retrieved_chunks} chunk(s) (semantic {semantic:.2f}, lexical {lexical:.2f}, coverage {doc_coverage:.2f})."
        if tier == "medium":
            return tier, f"Moderate retrieval alignment; answer is grounded but should be cross-checked (semantic {semantic:.2f}, lexical {lexical:.2f}, coverage {doc_coverage:.2f})."
        return tier, f"Weak retrieval signals; treat this as tentative (semantic {semantic:.2f}, lexical {lexical:.2f}, coverage {doc_coverage:.2f})."

    @staticmethod
    def _grounding_level(*, source_count: int, avg_semantic: float, confidence_tier: str) -> str:
        if source_count >= 3 and avg_semantic >= 0.45 and confidence_tier == "high":
            return "strong"
        if source_count >= 1 and avg_semantic >= 0.25:
            return "moderate"
        return "weak"

    async def _validate_by_mode(
        self,
        *,
        mode: str,
        answer: str,
        retrieved_chunks: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        output_format: Optional[str],
    ) -> Dict[str, Any]:
        min_overlap = 0.10 if mode in {"revision", "transformation"} else 0.12
        base = await validate_answer(
            answer=answer,
            chunks=[str(i.get("text") or "") for i in retrieved_chunks if str(i.get("text") or "").strip()],
            sources=sources,
            min_overlap=min_overlap,
        )
        issues = list(base.get("issues") or [])
        mode_issues: List[str] = []
        text = str(answer or "")
        text_lower = text.lower()
        if mode in {"important_questions", "mark_questions", "test_generation"}:
            if text.count("?") < 2:
                mode_issues.append("Question-generation mode output appears to contain too few question prompts.")
            chunk_vocab = set()
            for row in retrieved_chunks:
                chunk_vocab.update(keyword_tokens(str(row.get("text") or ""), limit=128))
            answer_vocab = set(keyword_tokens(text, limit=128))
            if answer_vocab and (len(answer_vocab.intersection(chunk_vocab)) / float(max(1, len(answer_vocab)))) < 0.08:
                mode_issues.append("Generated questions appear weakly traceable to retrieved concepts.")
        if mode == "revision" and len(text) > 5000:
            mode_issues.append("Revision output is too long; should stay concise and high-yield.")
        if mode == "transformation":
            fmt = str(output_format or "")
            if fmt == "flashcards" and ("q:" not in text_lower or "a:" not in text_lower):
                mode_issues.append("Flashcards format requested but structure was weak.")
            if fmt == "table" and "|" not in text:
                mode_issues.append("Table format requested but table structure was not detected.")
        if mode == "research_analysis" and re.search(r"\b(always|definitely|certainly|proves)\b", text_lower):
            if not re.search(r"\b(based on|according to|suggests|inferred)\b", text_lower):
                mode_issues.append("Analysis has strong claims without clear inference framing.")
        if mode == "general_doc_assist" and not bool(base.get("grounded")):
            mode_issues.append("Guidance is not sufficiently grounded in retrieved document context.")
        issues.extend(mode_issues)
        return {
            "success": bool(base.get("success", True)),
            "grounded": bool(base.get("grounded")) and not mode_issues,
            "score": float(base.get("score") or 0.0),
            "issues": issues,
            "source_count": int(base.get("source_count") or len(sources)),
            "supported_sentences": int(base.get("supported_sentences") or 0),
            "unsupported_sentences": int(base.get("unsupported_sentences") or 0),
            "threshold": float(base.get("threshold") or min_overlap),
            "mode": mode,
            "mode_issue_count": len(mode_issues),
        }

    def _apply_validation_policy(
        self,
        *,
        mode: str,
        base_confidence: float,
        confidence_meta: Dict[str, Any],
        validation: Dict[str, Any],
    ) -> tuple[float, Dict[str, Any], List[str]]:
        adjusted = float(base_confidence or 0.0)
        issues = list(validation.get("issues") or [])
        grounded = bool(validation.get("grounded"))
        validation_score = float(validation.get("score") or 0.0)
        retrieval_strength = str(confidence_meta.get("retrieval_strength") or "weak").lower()
        warnings: List[str] = []
        if not grounded:
            adjusted = min(adjusted, 0.34)
            warnings.append("Grounding is weak; response is based on limited retrieved context.")
        if issues:
            adjusted = max(0.12, adjusted - min(0.18, 0.06 * len(issues)))
            warnings.append("Validation detected quality issues; verify critical points with sources.")
        if grounded and retrieval_strength == "strong" and not issues and validation_score >= 0.45:
            adjusted = max(adjusted, 0.80)
        if grounded and retrieval_strength == "moderate" and adjusted > 0.86:
            adjusted = 0.86
        if grounded and retrieval_strength == "weak":
            adjusted = min(adjusted, 0.58)
        if grounded and validation_score >= 0.5:
            adjusted = min(0.98, adjusted + 0.03)
        if mode == "general_doc_assist" and not grounded:
            warnings.append("Guidance mode stayed constrained to document evidence and avoided unsupported advice.")
        if grounded and retrieval_strength == "weak":
            warnings.append("Retrieval signals were weak; treat this output as tentative and refine scope for higher precision.")
        tier, reason = self._confidence_tier_and_reason(
            confidence=adjusted,
            score_meta=dict(confidence_meta.get("score_meta") or {}),
            retrieved_chunks=int(confidence_meta.get("retrieved_chunks") or 0),
            doc_coverage=float(confidence_meta.get("doc_coverage") or 0.0),
        )
        if not grounded:
            reason = f"{reason} Validation flagged unsupported statements; confidence reduced."
        elif issues:
            reason = f"{reason} Minor validation issues detected."
        next_meta = dict(confidence_meta)
        next_meta["confidence_tier"] = tier
        next_meta["confidence_reason"] = reason
        return round(adjusted, 3), next_meta, warnings

    def _append_grounding_notice(self, *, answer: str, mode: str, validation: Dict[str, Any]) -> str:
        if bool(validation.get("grounded")) and not list(validation.get("issues") or []):
            return str(answer or "")
        base = str(answer or "").strip() or "I could not produce a well-grounded response from retrieved chunks."
        notice = (
            "\n\nGrounding note: this response is constrained to retrieved document context and may be incomplete. "
            "For better accuracy, try adding a unit/chapter/page hint."
        )
        if mode == "general_doc_assist":
            notice = (
                "\n\nGrounding note: guidance is limited to retrieved document context only. "
                "I avoided generic advice that was not supported by the notes."
            )
        return base if notice.strip() in base else f"{base}{notice}"

    async def _generate_answer(
        self,
        *,
        question: str,
        retrieved_chunks: List[Dict[str, Any]],
        mode: str,
        mark_format: Optional[str],
        output_format: Optional[str],
        unit_hint: Optional[str],
    ) -> str:
        llm_answer: Optional[str] = None
        try:
            llm_answer = await asyncio.wait_for(
                self._ask_openrouter(
                    question=question,
                    retrieved_chunks=retrieved_chunks,
                    mode=mode,
                    mark_format=mark_format,
                    output_format=output_format,
                    unit_hint=unit_hint,
                ),
                timeout=20.0 if self._is_exam_mode(mode) else 30.0,
            )
        except TimeoutError:
            _logger.warning("documents.ask_generation_timeout", mode=mode)
        if llm_answer:
            return llm_answer
        return self._fallback_answer(
            question=question,
            retrieved_chunks=retrieved_chunks,
            mode=mode,
            mark_format=mark_format,
        )

    def _fallback_answer(
        self, *, question: str, retrieved_chunks: List[Dict[str, Any]], mode: str, mark_format: Optional[str]
    ) -> str:
        if not retrieved_chunks:
            return "I could not find enough relevant context in the uploaded document."
        if mode in {"important_questions", "mark_questions", "test_generation"}:
            count = 6 if mode == "important_questions" else 5
            out = ["Important questions from your uploaded notes:"]
            for idx, row in enumerate(retrieved_chunks[:count], start=1):
                label = f"{mark_format}-mark " if mark_format in ALLOWED_MARK_FORMATS else ""
                out.append(f"{idx}. {label}Question on {self._topic_from_chunk(str(row.get('text') or ''))}? [C{idx}]")
            return "\n".join(out)
        if mode == "revision":
            out = ["Quick revision points (grounded in notes):"]
            for idx, row in enumerate(retrieved_chunks[:5], start=1):
                snippet = str(row.get("text") or "").strip()
                if len(snippet) > 220:
                    snippet = snippet[:220].rstrip() + "..."
                out.append(f"- [C{idx}] {snippet}")
            return "\n".join(out)
        if mode == "general_doc_assist":
            out = ["Based on your uploaded notes, focus in this order:"]
            for idx, row in enumerate(retrieved_chunks[:4], start=1):
                out.append(f"{idx}. {self._topic_from_chunk(str(row.get('text') or ''))} [C{idx}]")
            return "\n".join(out)
        if mode == "mark_answers":
            out = [f"Grounded {mark_format or 'requested'}-mark style answer from notes:"]
            for idx, row in enumerate(retrieved_chunks[:3], start=1):
                snippet = str(row.get("text") or "").strip()
                if len(snippet) > 260:
                    snippet = snippet[:260].rstrip() + "..."
                out.append(f"- [C{idx}] {snippet}")
            return "\n".join(out)
        out = ["I found these relevant points in the uploaded document:"]
        for idx, row in enumerate(retrieved_chunks[:3], start=1):
            snippet = str(row.get("text") or "").strip()
            if len(snippet) > 360:
                snippet = snippet[:360].rstrip() + "..."
            out.append(f"{idx}. {snippet} [C{idx}]")
        return "\n".join(out)

    @staticmethod
    def _topic_from_chunk(text: str) -> str:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", str(text or "")) if len(w) > 2]
        return " ".join(words[: min(7, len(words))]).strip() if words else "the key concept"

    def _mode_instruction(self, mode: str, mark_format: Optional[str]) -> str:
        if mode == "qa":
            return "Answer the user's question directly and concisely from context only."
        if mode == "important_questions":
            return "Generate important exam questions from context only. Group by importance when possible."
        if mode == "mark_questions":
            return f"Generate exam-style questions for {mark_format or 'requested'} mark pattern from context only."
        if mode == "mark_answers":
            return f"Answer in {mark_format or 'requested'}-mark depth using context only."
        if mode == "revision":
            return "Create concise high-yield revision notes from context only."
        if mode == "research_analysis":
            return "Provide grounded analysis from context and label inferences clearly."
        if mode == "extraction":
            return "Extract requested items strictly from context."
        if mode == "transformation":
            return "Transform structure while preserving grounded meaning."
        if mode == "test_generation":
            return "Generate a balanced mock test from context only."
        if mode == "general_doc_assist":
            return "Provide practical guidance derived strictly from context; avoid generic advice."
        return "Answer from context only."

    @staticmethod
    def _format_instruction(output_format: Optional[str]) -> str:
        fmt = str(output_format or "").strip().lower()
        if fmt == "bullet":
            return "Use compact bullet points."
        if fmt == "table":
            return "Use a Markdown table where suitable."
        if fmt == "flashcards":
            return "Use flashcards format with `Q:` and `A:` lines."
        if fmt == "outline":
            return "Use a concise hierarchical outline."
        return "Use clear paragraph form unless mode requires lists."

    def _build_prompt(
        self,
        *,
        question: str,
        retrieved_chunks: List[Dict[str, Any]],
        mode: str,
        mark_format: Optional[str],
        output_format: Optional[str],
        unit_hint: Optional[str],
    ) -> tuple[str, str]:
        chunk_limit = self._prompt_chunk_limit_for_mode(mode)
        text_limit = self._prompt_text_limit_for_mode(mode)
        context_parts = []
        for idx, chunk in enumerate(retrieved_chunks[:chunk_limit], start=1):
            raw_text = str(chunk.get("text") or "").strip()
            chunk_text = raw_text[:text_limit].rstrip()
            if len(raw_text) > text_limit:
                chunk_text += "..."
            context_parts.append(
                f"[C{idx}] doc={chunk.get('doc_id')} chunk={chunk.get('chunk_index')} pages={chunk.get('page_start')}-{chunk.get('page_end')}\n{chunk_text}"
            )
        context = "\n\n".join(context_parts)
        scope_line = f"Scope hint: {unit_hint}" if unit_hint else "Scope hint: none"
        mark_line = f"Mark format: {mark_format}" if mark_format else "Mark format: none"
        system_prompt = (
            "You are TAOS adaptive document assistant. Use ONLY provided context chunks. "
            "Do not invent facts or external claims. If context is insufficient, say so clearly. "
            f"Mode instruction: {self._mode_instruction(mode, mark_format)} "
            f"Presentation instruction: {self._format_instruction(output_format)} "
            "Formatting preference must never weaken grounding. Cite used chunks with [C#]."
        )
        user_prompt = (
            f"Question:\n{question}\n\n{scope_line}\n{mark_line}\nSelected mode: {mode}\n\n"
            f"Context Chunks:\n{context}\n\nReturn grounded response with [C#] citations."
        )
        return system_prompt, user_prompt

    async def _stream_openrouter(
        self,
        *,
        question: str,
        retrieved_chunks: List[Dict[str, Any]],
        mode: str,
        mark_format: Optional[str],
        output_format: Optional[str],
        unit_hint: Optional[str],
    ) -> AsyncIterator[str]:
        if not self._settings.openrouter_api_key or not retrieved_chunks:
            return
        system_prompt, user_prompt = self._build_prompt(
            question=question,
            retrieved_chunks=retrieved_chunks,
            mode=mode,
            mark_format=mark_format,
            output_format=output_format,
            unit_hint=unit_hint,
        )
        payload = {
            "model": self._settings.executor_model,
            "temperature": 0.2,
            "max_tokens": 460 if self._is_exam_mode(mode) else 700,
            "stream": True,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        }
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json",
        }
        stream_timeout = 35.0 if self._is_exam_mode(mode) else 65.0
        async with httpx.AsyncClient(timeout=stream_timeout) as client:
            async with client.stream("POST", f"{self._settings.openrouter_base_url}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    raw = str(line or "").strip()
                    if not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        parsed = json.loads(data)
                    except Exception:
                        continue
                    delta = (((parsed.get("choices") or [{}])[0].get("delta") or {}).get("content") or "")
                    if delta:
                        yield str(delta)

    async def _ask_openrouter(
        self,
        *,
        question: str,
        retrieved_chunks: List[Dict[str, Any]],
        mode: str,
        mark_format: Optional[str],
        output_format: Optional[str],
        unit_hint: Optional[str],
    ) -> Optional[str]:
        if not self._settings.openrouter_api_key or not retrieved_chunks:
            return None
        system_prompt, user_prompt = self._build_prompt(
            question=question,
            retrieved_chunks=retrieved_chunks,
            mode=mode,
            mark_format=mark_format,
            output_format=output_format,
            unit_hint=unit_hint,
        )
        payload = {
            "model": self._settings.executor_model,
            "temperature": 0.2,
            "max_tokens": 460 if self._is_exam_mode(mode) else 700,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        }
        headers = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "HTTP-Referer": self._settings.site_url,
            "X-Title": self._settings.site_name,
            "Content-Type": "application/json",
        }
        try:
            request_timeout = 24.0 if self._is_exam_mode(mode) else 45.0
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(f"{self._settings.openrouter_base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
            data = response.json()
            message = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return message or None
        except Exception as exc:
            _logger.warning("documents.ask_openrouter_fallback", error=str(exc))
            return None
