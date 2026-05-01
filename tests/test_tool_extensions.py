from __future__ import annotations

import pytest

from taos.core.documents.repository import DocumentRepository
from taos.core.tools.builtin import register_all_builtin_tools
from taos.core.tools.builtin.memory_kv import memory_get, memory_set
from taos.core.tools.builtin.retrieve_chunks import retrieve_chunks
from taos.core.tools.builtin.validate_answer import validate_answer
from taos.core.tools.registry import ToolRegistry


def test_registry_includes_new_abstraction_tools():
    registry = ToolRegistry()
    register_all_builtin_tools(registry)
    names = set(registry.list_names())
    assert "retrieve_chunks" in names
    assert "process_document" in names
    assert "memory_get" in names
    assert "memory_set" in names
    assert "validate_answer" in names


@pytest.mark.asyncio
async def test_memory_set_and_get_roundtrip():
    set_res = await memory_set(
        key="tone_pref",
        value={"style": "casual"},
        user_id="u_mem",
        session_id="chat_1",
    )
    assert set_res["success"] is True

    get_res = await memory_get(
        key="tone_pref",
        user_id="u_mem",
        session_id="chat_1",
    )
    assert get_res["success"] is True
    assert get_res["found"] is True
    assert get_res["value"] == {"style": "casual"}


@pytest.mark.asyncio
async def test_retrieve_chunks_returns_ranked_rows():
    repo = DocumentRepository(use_firestore=False)
    await repo.create_document(
        {
            "doc_id": "doc_tools_1",
            "user_id": "u_retrieve",
            "status": "ready",
        }
    )
    await repo.replace_chunks(
        user_id="u_retrieve",
        doc_id="doc_tools_1",
        chunks=[
            {
                "chunk_id": "doc_tools_1_chunk_0000",
                "doc_id": "doc_tools_1",
                "user_id": "u_retrieve",
                "chunk_index": 0,
                "chunk_text": "LinkedIn optimization uses small-world and scale-free models.",
                "page_start": 1,
                "page_end": 1,
                "embedding": [0.1, 0.2, 0.3, 0.4],
                "keyword_tokens": ["linkedin", "optimization", "small-world", "scale-free"],
            },
            {
                "chunk_id": "doc_tools_1_chunk_0001",
                "doc_id": "doc_tools_1",
                "user_id": "u_retrieve",
                "chunk_index": 1,
                "chunk_text": "Temporal sentiment analysis tracks customer feedback over time.",
                "page_start": 2,
                "page_end": 2,
                "embedding": [0.2, 0.2, 0.2, 0.2],
                "keyword_tokens": ["temporal", "sentiment", "customer", "feedback"],
            },
        ],
    )

    res = await retrieve_chunks(
        query="What platform is used for professional networking optimization?",
        doc_ids=["doc_tools_1"],
        user_id="u_retrieve",
        top_k=2,
    )
    assert res["success"] is True
    assert res["count"] >= 1
    assert "chunks" in res
    assert "score" in res["chunks"][0]
    assert "score_breakdown" in res["chunks"][0]
    assert "rank" in res["chunks"][0]
    assert "source_label" in res["chunks"][0]
    assert "summary" in res
    assert isinstance(res["summary"].get("candidate_count"), int)
    assert isinstance(res["summary"].get("selected_doc_ids"), list)
    assert res["summary"].get("retrieval_strength") in {"weak", "moderate", "strong"}


@pytest.mark.asyncio
async def test_validate_answer_flags_unsupported_sentences():
    res = await validate_answer(
        answer=(
            "The document says LinkedIn is used for networking optimization. "
            "It also says Mars has oceans."
        ),
        chunks=["LinkedIn optimization is discussed in the assignment document."],
        min_overlap=0.2,
    )
    assert res["success"] is True
    assert res["grounded"] is False
    assert res["unsupported_sentences"] >= 1
    assert res["issues"]
