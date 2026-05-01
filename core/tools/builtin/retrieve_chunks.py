"""
TAOS Built-in Tool: Retrieve Chunks.

Provides a first-class retrieval abstraction over document chunks
for RAG-style orchestration and traceable grounding.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Sequence

from taos.config.constants import ToolRiskLevel
from taos.core.documents.embedding_service import EmbeddingService
from taos.core.documents.repository import DocumentRepository
from taos.core.documents.utils import cosine_similarity, keyword_tokens, question_overlap_ratio
from taos.core.tools.registry import ToolDefinition, ToolPolicy


_repo = DocumentRepository()
_embedder = EmbeddingService()


def _normalize_text_signature(text: str) -> str:
    compact = " ".join(str(text or "").lower().split())
    return compact[:420]


def _score_chunk(query: str, query_embed: Sequence[float], chunk: Dict[str, Any]) -> tuple[float, Dict[str, float]]:
    text = str(chunk.get("chunk_text") or "")
    text_lower = text.lower()
    query_lower = str(query or "").lower()
    query_terms = keyword_tokens(query, limit=48)

    semantic = cosine_similarity(query_embed, chunk.get("embedding") or [])
    lexical = question_overlap_ratio(query_terms, chunk.get("keyword_tokens") or [])
    phrase = 1.0 if query_lower and query_lower in text_lower else 0.0
    fuzzy = SequenceMatcher(None, query_lower[:180], text_lower[:800]).ratio()

    score = (semantic * 0.55) + (lexical * 0.25) + (phrase * 0.15) + (fuzzy * 0.05)
    return score, {
        "semantic": round(float(semantic), 3),
        "lexical": round(float(lexical), 3),
        "phrase": round(float(phrase), 3),
        "fuzzy": round(float(fuzzy), 3),
    }


def _retrieval_strength_from_scores(*, avg_score: float, max_score: float) -> str:
    if avg_score >= 0.58 and max_score >= 0.70:
        return "strong"
    if avg_score >= 0.35:
        return "moderate"
    return "weak"


async def retrieve_chunks(
    query: str,
    doc_ids: List[str],
    user_id: str,
    top_k: int = 5,
    include_text: bool = True,
) -> Dict[str, Any]:
    """
    Retrieve top document chunks using hybrid ranking.

    Args:
        query: User query
        doc_ids: Document IDs to search
        user_id: Owner/user scope
        top_k: Number of chunks to return
        include_text: Whether chunk text should be included
    """
    q = str(query or "").strip()
    if not q:
        return {"success": False, "error": "query is required", "chunks": [], "count": 0}
    dids = [str(d).strip() for d in (doc_ids or []) if str(d).strip()]
    if not dids:
        return {"success": False, "error": "doc_ids cannot be empty", "chunks": [], "count": 0}

    chunks = await _repo.list_chunks(user_id=str(user_id or ""), doc_ids=dids)
    if not chunks:
        return {
            "success": True,
            "query": q,
            "doc_ids": dids,
            "chunks": [],
            "count": 0,
            "note": "No chunks found for requested documents.",
            "summary": {
                "candidate_count": 0,
                "ranked_count": 0,
                "deduped_count": 0,
                "selected_count": 0,
                "selected_doc_count": 0,
                "selected_doc_ids": [],
                "avg_score": 0.0,
                "max_score": 0.0,
                "min_score": 0.0,
                "retrieval_strength": "weak",
            },
        }

    query_embed = _embedder.embed_text(q)
    ranked: List[tuple[Dict[str, Any], float, Dict[str, float]]] = []
    for chunk in chunks:
        score, score_meta = _score_chunk(query=q, query_embed=query_embed, chunk=chunk)
        if score <= 0:
            continue
        ranked.append((chunk, score, score_meta))
    ranked.sort(key=lambda row: row[1], reverse=True)

    k = max(1, min(int(top_k or 5), 20))
    per_doc_cap = max(1, int((k + 1) // 2))
    seen_signatures: set[str] = set()
    seen_chunk_ids: set[str] = set()
    per_doc_counts: Dict[str, int] = {}
    selected: List[tuple[Dict[str, Any], float, Dict[str, float]]] = []
    deduped = 0

    for chunk, score, score_meta in ranked:
        if len(selected) >= k:
            break
        doc_id = str(chunk.get("doc_id") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        signature = _normalize_text_signature(chunk.get("chunk_text") or "")
        if (chunk_id and chunk_id in seen_chunk_ids) or (signature and signature in seen_signatures):
            deduped += 1
            continue
        if doc_id and per_doc_counts.get(doc_id, 0) >= per_doc_cap:
            continue
        if chunk_id:
            seen_chunk_ids.add(chunk_id)
        if signature:
            seen_signatures.add(signature)
        per_doc_counts[doc_id] = per_doc_counts.get(doc_id, 0) + 1
        selected.append((chunk, score, score_meta))

    # Fill any remaining slots without the per-doc cap while preserving dedupe.
    if len(selected) < k:
        for chunk, score, score_meta in ranked:
            if len(selected) >= k:
                break
            chunk_id = str(chunk.get("chunk_id") or "")
            signature = _normalize_text_signature(chunk.get("chunk_text") or "")
            if (chunk_id and chunk_id in seen_chunk_ids) or (signature and signature in seen_signatures):
                continue
            if chunk_id:
                seen_chunk_ids.add(chunk_id)
            if signature:
                seen_signatures.add(signature)
            selected.append((chunk, score, score_meta))

    out: List[Dict[str, Any]] = []
    for rank, (chunk, score, score_meta) in enumerate(selected, start=1):
        page_start = chunk.get("page_start")
        page_end = chunk.get("page_end")
        page_label = "page n/a"
        if page_start and page_end and page_start != page_end:
            page_label = f"pages {page_start}-{page_end}"
        elif page_start:
            page_label = f"page {page_start}"

        doc_id = str(chunk.get("doc_id") or "doc")
        chunk_index = int(chunk.get("chunk_index") or 0)
        row: Dict[str, Any] = {
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "rank": int(rank),
            "score": round(float(score), 4),
            "page_start": page_start,
            "page_end": page_end,
            "source_label": f"{doc_id} | chunk {chunk_index} | {page_label}",
            "score_breakdown": score_meta,
        }
        if include_text:
            row["text"] = str(chunk.get("chunk_text") or "")
        out.append(row)

    scores = [float(item[1]) for item in selected]
    selected_doc_ids = sorted(
        {
            str(item[0].get("doc_id") or "")
            for item in selected
            if str(item[0].get("doc_id") or "").strip()
        }
    )
    avg_score = round(sum(scores) / float(max(1, len(scores))), 4)
    max_score = round(max(scores), 4) if scores else 0.0
    retrieval_strength = _retrieval_strength_from_scores(avg_score=avg_score, max_score=max_score)
    summary = {
        "candidate_count": int(len(chunks)),
        "ranked_count": int(len(ranked)),
        "deduped_count": int(deduped),
        "selected_count": int(len(selected)),
        "selected_doc_count": int(len(selected_doc_ids)),
        "selected_doc_ids": selected_doc_ids,
        "avg_score": avg_score,
        "max_score": max_score,
        "min_score": round(min(scores), 4) if scores else 0.0,
        "retrieval_strength": retrieval_strength,
    }

    return {
        "success": True,
        "query": q,
        "doc_ids": dids,
        "chunks": out,
        "count": len(out),
        "summary": summary,
    }


def create_retrieve_chunks_tool() -> ToolDefinition:
    return ToolDefinition(
        name="retrieve_chunks",
        description=(
            "Retrieve top matching document chunks for a query using hybrid semantic+keyword ranking. "
            "Use for document-grounded answering and RAG flows."
        ),
        input_schema={
            "query": "str",
            "doc_ids": "list[str]",
            "user_id": "str",
            "top_k": "int",
            "include_text": "bool",
        },
        handler=retrieve_chunks,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=20,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=60,
        cost_estimate=0.0,
        timeout=15,
        tags=["rag", "retrieval", "documents"],
    )
