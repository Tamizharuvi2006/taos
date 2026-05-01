from __future__ import annotations

import asyncio
import pytest

from taos.core.documents.ask_service import DocumentAskService
from taos.core.documents.embedding_service import EmbeddingService
from taos.core.documents.pdf_service import PdfExtractionResult
from taos.core.documents.processing_service import DocumentProcessingService
from taos.core.documents.repository import DocumentRepository


class _FakeStorage:
    def __init__(self) -> None:
        self._map = {}

    def put(self, path: str, payload: bytes) -> None:
        self._map[path] = payload

    def download_bytes(self, path: str) -> bytes:
        return self._map[path]


class _FakePdf:
    def extract_pdf(self, _payload: bytes) -> PdfExtractionResult:
        return PdfExtractionResult(
            text=(
                "TAOS pipeline supports upload processing and retrieval. "
                "It stores chunk metadata and lets users ask document questions."
            ),
            page_texts=[
                "TAOS upload processing pipeline supports PDF extraction and chunking.",
                "Users can ask questions and get source-grounded responses from chunks.",
            ],
            page_count=2,
        )


@pytest.mark.asyncio
async def test_upload_init_builds_doc_metadata():
    repo = DocumentRepository(use_firestore=False)
    service = DocumentProcessingService(
        repository=repo,
        storage_service=_FakeStorage(),
        pdf_service=_FakePdf(),
    )

    doc = await service.init_upload(
        user_id="u_init",
        file_name="notes.pdf",
        file_size=1024,
        mime_type="application/pdf",
    )

    assert doc["status"] == "uploaded"
    assert doc["storage_path"].startswith("users/u_init/uploads/")
    assert doc["storage_path"].endswith("/original.pdf")


@pytest.mark.asyncio
async def test_process_document_marks_ready_and_stores_chunks():
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )

    doc = await service.init_upload(
        user_id="u_ready",
        file_name="report.pdf",
        file_size=2048,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-fake-1")

    result = await service.process_document_sync(user_id="u_ready", doc_id=doc["doc_id"])
    status = await service.get_status(user_id="u_ready", doc_id=doc["doc_id"])
    chunks = await repo.list_chunks(user_id="u_ready", doc_ids=[doc["doc_id"]])

    assert result["status"] == "ready"
    assert status is not None
    assert status["status"] == "ready"
    assert status.get("processing_stage") == "ready"
    assert int(status.get("processing_progress") or 0) == 100
    assert int(status["chunk_count"]) >= 1
    assert int(status["page_count"]) == 2
    assert len(chunks) >= 1
    assert all(chunk.get("embedding") for chunk in chunks)


@pytest.mark.asyncio
async def test_process_document_dedupes_by_sha_and_clones_chunks():
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )

    first = await service.init_upload(
        user_id="u_dedupe",
        file_name="a.pdf",
        file_size=1000,
        mime_type="application/pdf",
    )
    storage.put(first["storage_path"], b"%PDF-same-payload")
    await service.process_document_sync(user_id="u_dedupe", doc_id=first["doc_id"])

    second = await service.init_upload(
        user_id="u_dedupe",
        file_name="b.pdf",
        file_size=1200,
        mime_type="application/pdf",
    )
    storage.put(second["storage_path"], b"%PDF-same-payload")
    await service.process_document_sync(user_id="u_dedupe", doc_id=second["doc_id"])

    second_status = await service.get_status(user_id="u_dedupe", doc_id=second["doc_id"])
    assert second_status is not None
    assert second_status["status"] == "ready"
    assert second_status.get("processing_stage") == "ready"
    assert int(second_status.get("processing_progress") or 0) == 100
    assert second_status.get("dedupe_source_doc_id") == first["doc_id"]
    assert int(second_status.get("chunk_count") or 0) >= 1


@pytest.mark.asyncio
async def test_ask_uses_chunk_retrieval_and_cache(monkeypatch):
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )

    doc = await process_service.init_upload(
        user_id="u_ask",
        file_name="guide.pdf",
        file_size=3000,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-for-ask")
    await process_service.process_document_sync(user_id="u_ask", doc_id=doc["doc_id"])

    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    async def _no_llm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ask_service, "_ask_openrouter", _no_llm)

    first = await ask_service.ask(
        user_id="u_ask",
        doc_ids=[doc["doc_id"]],
        question="How does the upload processing pipeline work?",
    )
    second = await ask_service.ask(
        user_id="u_ask",
        doc_ids=[doc["doc_id"]],
        question="How does the upload processing pipeline work?",
    )

    assert first["cached"] is False
    assert first["sources"]
    assert isinstance(first.get("validation"), dict)
    assert "grounded" in first["validation"]
    assert first.get("mode") == "qa"
    assert isinstance(first.get("warnings"), list)
    assert first["confidence"] > 0
    assert first["metadata"].get("retrieval_mode") == "hybrid_vector_keyword_rerank"
    assert isinstance(first["metadata"].get("retrieval_summary"), dict)
    assert first["metadata"].get("retrieval_strength") in {"weak", "moderate", "strong"}
    assert first["metadata"].get("confidence_tier") in {"low", "medium", "high"}
    assert isinstance(first["metadata"].get("confidence_reason"), str)
    assert first["metadata"].get("confidence_reason")
    assert isinstance(first["metadata"].get("source_count"), int)
    assert first["metadata"].get("source_docs")
    assert first["metadata"].get("grounding_level") in {"weak", "moderate", "strong"}
    assert isinstance(first["sources"][0].get("score"), float)
    assert "uploaded document" in first["answer"] or "relevant points" in first["answer"]
    assert second["cached"] is True
    assert second["answer"] == first["answer"]
    assert isinstance(second.get("validation"), dict)
    assert second.get("mode") == "qa"
    assert second["metadata"].get("confidence_tier") in {"low", "medium", "high"}
    assert second["metadata"].get("cache_hit") is True
    assert second["metadata"].get("retrieval_strength") in {"weak", "moderate", "strong", "unknown"}
    assert second["metadata"].get("source_count") >= 0


@pytest.mark.asyncio
async def test_list_and_delete_document_lifecycle():
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )

    doc = await process_service.init_upload(
        user_id="u_delete",
        file_name="delete_me.pdf",
        file_size=3000,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-delete")
    await process_service.process_document_sync(user_id="u_delete", doc_id=doc["doc_id"])

    listed = await process_service.list_documents(user_id="u_delete")
    assert any(item.get("doc_id") == doc["doc_id"] for item in listed)

    deleted = await process_service.delete_document(user_id="u_delete", doc_id=doc["doc_id"])
    assert deleted is True

    status = await process_service.get_status(user_id="u_delete", doc_id=doc["doc_id"])
    chunks = await repo.list_chunks(user_id="u_delete", doc_ids=[doc["doc_id"]])
    assert status is None
    assert chunks == []


@pytest.mark.asyncio
async def test_ask_rejects_too_many_doc_ids():
    repo = DocumentRepository(use_firestore=False)
    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))
    with pytest.raises(ValueError):
        await ask_service.ask(
            user_id="u_limit",
            doc_ids=["d1", "d2", "d3", "d4", "d5", "d6"],
            question="hello",
        )


@pytest.mark.asyncio
async def test_ask_reports_not_ready_documents():
    repo = DocumentRepository(use_firestore=False)
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=_FakeStorage(),
        pdf_service=_FakePdf(),
    )
    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    doc = await process_service.init_upload(
        user_id="u_not_ready",
        file_name="pending.pdf",
        file_size=1024,
        mime_type="application/pdf",
    )
    # Document remains in uploaded status (not processed).
    with pytest.raises(RuntimeError) as exc:
        await ask_service.ask(
            user_id="u_not_ready",
            doc_ids=[doc["doc_id"]],
            question="What does this doc say?",
        )
    assert "Document(s) not ready" in str(exc.value)
    assert doc["doc_id"] in str(exc.value)


@pytest.mark.asyncio
async def test_ask_routes_to_important_questions(monkeypatch):
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )
    doc = await process_service.init_upload(
        user_id="u_modes",
        file_name="exam.pdf",
        file_size=3000,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-exam")
    await process_service.process_document_sync(user_id="u_modes", doc_id=doc["doc_id"])

    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    async def _no_llm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ask_service, "_ask_openrouter", _no_llm)

    res = await ask_service.ask(
        user_id="u_modes",
        doc_ids=[doc["doc_id"]],
        question="Tomorrow is my exam, give me very important questions from this notes",
    )
    assert res["mode"] == "important_questions"
    assert "Important questions" in res["answer"]
    assert isinstance(res.get("warnings"), list)


@pytest.mark.asyncio
async def test_cache_key_separates_mode_and_output_format(monkeypatch):
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )
    doc = await process_service.init_upload(
        user_id="u_cache_modes",
        file_name="cache.pdf",
        file_size=2048,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-cache")
    await process_service.process_document_sync(user_id="u_cache_modes", doc_id=doc["doc_id"])

    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    async def _no_llm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ask_service, "_ask_openrouter", _no_llm)

    qa1 = await ask_service.ask(
        user_id="u_cache_modes",
        doc_ids=[doc["doc_id"]],
        question="Summarize the pipeline",
        mode="qa",
    )
    qa2 = await ask_service.ask(
        user_id="u_cache_modes",
        doc_ids=[doc["doc_id"]],
        question="Summarize the pipeline",
        mode="qa",
    )
    rev = await ask_service.ask(
        user_id="u_cache_modes",
        doc_ids=[doc["doc_id"]],
        question="Summarize the pipeline",
        mode="revision",
        output_format="bullet",
    )

    assert qa1["cached"] is False
    assert qa2["cached"] is True
    assert rev["cached"] is False
    assert rev["mode"] == "revision"
    assert rev["metadata"].get("output_format") == "bullet"


@pytest.mark.asyncio
async def test_general_doc_assist_adds_guardrail_warning(monkeypatch):
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )
    doc = await process_service.init_upload(
        user_id="u_guidance",
        file_name="guide.pdf",
        file_size=1800,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-guide")
    await process_service.process_document_sync(user_id="u_guidance", doc_id=doc["doc_id"])

    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    async def _no_llm(*_args, **_kwargs):
        return None

    async def _force_weak(*_args, **_kwargs):
        return {
            "success": True,
            "grounded": False,
            "score": 0.1,
            "issues": ["forced weak grounding"],
            "supported_sentences": 0,
            "unsupported_sentences": 1,
        }

    monkeypatch.setattr(ask_service, "_ask_openrouter", _no_llm)
    monkeypatch.setattr(ask_service, "_validate_by_mode", _force_weak)

    res = await ask_service.ask(
        user_id="u_guidance",
        doc_ids=[doc["doc_id"]],
        question="How should I study this in 2 days?",
        mode="general_doc_assist",
    )
    assert res["mode"] == "general_doc_assist"
    assert any("Guidance mode" in w for w in res.get("warnings", []))
    assert "Grounding note" in res["answer"]


@pytest.mark.asyncio
async def test_invalid_mode_falls_back_to_auto_with_warning(monkeypatch):
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )
    doc = await process_service.init_upload(
        user_id="u_invalid_mode",
        file_name="mode.pdf",
        file_size=1800,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-mode")
    await process_service.process_document_sync(user_id="u_invalid_mode", doc_id=doc["doc_id"])

    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    async def _no_llm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ask_service, "_ask_openrouter", _no_llm)

    res = await ask_service.ask(
        user_id="u_invalid_mode",
        doc_ids=[doc["doc_id"]],
        question="What are the key points?",
        mode="unknown_mode",
    )
    assert res["mode"] == "qa"
    assert any("invalid" in w.lower() for w in res.get("warnings", []))


@pytest.mark.asyncio
async def test_stream_final_payload_parity_includes_mode_and_warnings(monkeypatch):
    repo = DocumentRepository(use_firestore=False)
    storage = _FakeStorage()
    process_service = DocumentProcessingService(
        repository=repo,
        storage_service=storage,
        pdf_service=_FakePdf(),
    )
    doc = await process_service.init_upload(
        user_id="u_stream_mode",
        file_name="stream.pdf",
        file_size=1600,
        mime_type="application/pdf",
    )
    storage.put(doc["storage_path"], b"%PDF-stream")
    await process_service.process_document_sync(user_id="u_stream_mode", doc_id=doc["doc_id"])

    ask_service = DocumentAskService(repository=repo, embedding_service=EmbeddingService(dimensions=64))

    async def _no_stream_llm(*_args, **_kwargs):
        if False:
            yield ""

    async def _no_llm(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ask_service, "_stream_openrouter", _no_stream_llm)
    monkeypatch.setattr(ask_service, "_ask_openrouter", _no_llm)

    events = []
    async for evt in ask_service.stream(
        user_id="u_stream_mode",
        doc_ids=[doc["doc_id"]],
        question="Give me quick revision points",
        mode="revision",
        output_format="bullet",
    ):
        events.append(evt)

    assert events
    assert events[-1]["type"] == "final"
    payload = events[-1]["payload"]
    assert payload["mode"] == "revision"
    assert isinstance(payload.get("warnings"), list)
    assert isinstance(payload.get("validation"), dict)


def test_exam_mode_retrieval_plan_and_prompt_limits():
    ask_service = DocumentAskService(repository=DocumentRepository(use_firestore=False), embedding_service=EmbeddingService(dimensions=64))
    profile, top_k = ask_service._retrieval_plan_for_mode("important_questions")
    assert profile == "broad"
    assert top_k == 6

    chunks = []
    for idx in range(1, 12):
        chunks.append(
            {
                "doc_id": "d1",
                "chunk_index": idx,
                "page_start": idx,
                "page_end": idx,
                "text": "A" * 1200,
            }
        )
    _system, user_prompt = ask_service._build_prompt(
        question="give important 16 mark questions",
        retrieved_chunks=chunks,
        mode="important_questions",
        mark_format="16",
        output_format="bullet",
        unit_hint="unit 2",
    )
    # Six chunk references plus one instruction token "[C#]" in the footer.
    assert user_prompt.count("[C") <= 7
    # Chunk body should be truncated in prompt for exam mode.
    assert "A" * 1100 not in user_prompt


@pytest.mark.asyncio
async def test_generate_answer_exam_mode_timeout_falls_back(monkeypatch):
    ask_service = DocumentAskService(repository=DocumentRepository(use_firestore=False), embedding_service=EmbeddingService(dimensions=64))

    async def _slow_openrouter(*_args, **_kwargs):
        await asyncio.sleep(21.0)
        return "too late"

    monkeypatch.setattr(ask_service, "_ask_openrouter", _slow_openrouter)

    chunks = [
        {"text": "Neural networks and optimization principles for exam preparation.", "chunk_index": 1, "doc_id": "d1"},
        {"text": "Backpropagation workflow and architecture trade-offs.", "chunk_index": 2, "doc_id": "d1"},
    ]
    answer = await ask_service._generate_answer(
        question="give important 16 mark questions from this",
        retrieved_chunks=chunks,
        mode="important_questions",
        mark_format="16",
        output_format="bullet",
        unit_hint=None,
    )
    lowered = answer.lower()
    assert "important questions" in lowered
    assert "16-mark question" in lowered
