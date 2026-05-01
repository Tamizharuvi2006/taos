"""Document upload initialization and processing orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any, Dict, Optional, Tuple

from taos.config.settings import get_settings
from taos.core.documents.chunking_service import ChunkingService
from taos.core.documents.embedding_service import EmbeddingService
from taos.core.documents.pdf_service import PdfService
from taos.core.documents.repository import DocumentRepository
from taos.core.documents.storage_service import DocumentStorageService
from taos.core.documents.utils import keyword_tokens, utc_now_iso
from taos.core.security import validate_upload_request
from taos.infra.logging.logger import TAOSLogger

_logger = TAOSLogger(name="taos.documents.process")


class DocumentProcessingService:
    def __init__(
        self,
        repository: Optional[DocumentRepository] = None,
        storage_service: Optional[DocumentStorageService] = None,
        pdf_service: Optional[PdfService] = None,
        chunking_service: Optional[ChunkingService] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ) -> None:
        self._settings = get_settings()
        self._repo = repository or DocumentRepository()
        self._storage = storage_service or DocumentStorageService()
        self._pdf = pdf_service or PdfService()
        self._chunker = chunking_service or ChunkingService()
        self._embedder = embedding_service or EmbeddingService()
        self._tasks: Dict[Tuple[str, str], asyncio.Task] = {}
        self._tasks_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, int(self._settings.document_processing_max_concurrency)))

    async def init_upload(self, user_id: str, file_name: str, file_size: int, mime_type: str) -> Dict[str, Any]:
        normalized_mime = str(mime_type or "").strip().lower()
        max_size_bytes = int(self._settings.max_upload_size_mb) * 1024 * 1024
        guard = validate_upload_request(
            file_name=file_name,
            file_size=file_size,
            mime_type=normalized_mime,
            max_size_bytes=max_size_bytes,
        )
        if not guard.allowed:
            if guard.code == "upload_too_large":
                raise ValueError(f"File is too large. Maximum allowed size is {self._settings.max_upload_size_mb}MB")
            raise ValueError(guard.raise_message())

        now = utc_now_iso()
        doc_id = f"doc_{uuid.uuid4().hex}"
        # Keep storage path compatible with current deployed Firebase Storage rules:
        # rules currently authorize /users/{uid}/** writes for owner accounts.
        storage_path = f"users/{user_id}/uploads/{doc_id}/original.pdf"
        document = {
            "doc_id": doc_id,
            "user_id": str(user_id),
            "file_name": str(file_name),
            "storage_path": storage_path,
            "file_size": int(file_size),
            "mime_type": normalized_mime,
            "sha256": "",
            "status": "uploaded",
            "processing_stage": "uploaded",
            "processing_progress": 0,
            "error_message": None,
            "page_count": None,
            "chunk_count": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.create_document(document)
        return document

    async def enqueue_processing(self, user_id: str, doc_id: str) -> Dict[str, Any]:
        doc = await self._repo.get_document(user_id=user_id, doc_id=doc_id)
        if not doc:
            raise KeyError("Document not found")

        key = (str(user_id), str(doc_id))
        async with self._tasks_lock:
            existing = self._tasks.get(key)
            if existing is not None and not existing.done():
                return {"doc_id": str(doc_id), "status": "processing"}

            await self._repo.update_document(
                user_id=user_id,
                doc_id=doc_id,
                updates={
                    "status": "processing",
                    "processing_stage": "queued",
                    "processing_progress": 5,
                    "error_message": None,
                    "updated_at": utc_now_iso(),
                },
            )
            task = asyncio.create_task(self._process_document_worker(user_id=user_id, doc_id=doc_id))
            self._tasks[key] = task
            task.add_done_callback(lambda _done: self._tasks.pop(key, None))
        return {"doc_id": str(doc_id), "status": "processing"}

    async def process_document_sync(self, user_id: str, doc_id: str) -> Dict[str, Any]:
        """Synchronous entry point useful for tests."""
        return await self._process_document_worker(user_id=user_id, doc_id=doc_id)

    async def get_status(self, user_id: str, doc_id: str) -> Optional[Dict[str, Any]]:
        return await self._repo.get_document(user_id=user_id, doc_id=doc_id)

    async def list_documents(self, user_id: str, limit: int = 100) -> list[Dict[str, Any]]:
        return await self._repo.list_documents(user_id=user_id, limit=limit)

    async def delete_document(self, user_id: str, doc_id: str) -> bool:
        key = (str(user_id), str(doc_id))
        async with self._tasks_lock:
            existing = self._tasks.get(key)
            if existing is not None and not existing.done():
                existing.cancel()
            self._tasks.pop(key, None)
        return await self._repo.delete_document(user_id=user_id, doc_id=doc_id)

    async def _process_document_worker(self, user_id: str, doc_id: str) -> Dict[str, Any]:
        async with self._semaphore:
            return await self._process_document_internal(user_id=user_id, doc_id=doc_id)

    async def _mark_progress(
        self,
        *,
        user_id: str,
        doc_id: str,
        stage: str,
        progress: int,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        updates: Dict[str, Any] = {
            "status": "processing",
            "processing_stage": stage,
            "processing_progress": max(0, min(100, int(progress))),
            "updated_at": utc_now_iso(),
        }
        if extra:
            updates.update(extra)
        await self._repo.update_document(user_id=user_id, doc_id=doc_id, updates=updates)

    async def _process_document_internal(self, user_id: str, doc_id: str) -> Dict[str, Any]:
        started_at = utc_now_iso()
        await self._mark_progress(
            user_id=user_id,
            doc_id=doc_id,
            stage="starting",
            progress=10,
            extra={"error_message": None, "processing_started_at": started_at},
        )

        try:
            await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="loading_metadata", progress=15)
            doc = await self._repo.get_document(user_id=user_id, doc_id=doc_id)
            if not doc:
                raise RuntimeError("Document metadata missing")
            storage_path = str(doc.get("storage_path") or "")
            if not storage_path:
                raise RuntimeError("Document storage_path is missing")

            await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="downloading_pdf", progress=25)
            payload = self._storage.download_bytes(storage_path)
            sha256 = hashlib.sha256(payload).hexdigest()

            await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="dedupe_check", progress=35)
            ready_match = await self._repo.find_ready_by_sha(
                user_id=user_id,
                sha256=sha256,
                exclude_doc_id=doc_id,
            )
            if ready_match:
                await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="cloning_cached_chunks", progress=55)
                cloned = await self._repo.clone_chunks(
                    user_id=user_id,
                    source_doc_id=str(ready_match.get("doc_id")),
                    target_doc_id=doc_id,
                    created_at=utc_now_iso(),
                )
                if cloned > 0:
                    updates = {
                        "status": "ready",
                        "processing_stage": "ready",
                        "processing_progress": 100,
                        "sha256": sha256,
                        "chunk_count": int(cloned),
                        "page_count": int(ready_match.get("page_count") or 0) or None,
                        "error_message": None,
                        "processing_completed_at": utc_now_iso(),
                        "updated_at": utc_now_iso(),
                        "dedupe_source_doc_id": str(ready_match.get("doc_id") or ""),
                    }
                    await self._repo.update_document(user_id=user_id, doc_id=doc_id, updates=updates)
                    _logger.info(
                        "documents.processed_dedupe_clone",
                        user_id=user_id,
                        doc_id=doc_id,
                        source_doc_id=ready_match.get("doc_id"),
                        chunk_count=cloned,
                    )
                    return {"doc_id": doc_id, "status": "ready", "chunk_count": int(cloned)}

            await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="extracting_text", progress=50)
            extracted = self._pdf.extract_pdf(payload)
            await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="chunking_text", progress=65)
            chunks = self._chunker.chunk_pages(extracted.page_texts)
            if not chunks and extracted.text:
                chunks = self._chunker.chunk_text(extracted.text)
            if not chunks:
                raise RuntimeError("No text chunks were extracted from PDF")

            chunk_docs = []
            now = utc_now_iso()
            total_chunks = max(1, len(chunks))
            for chunk in chunks:
                chunk_index = int(chunk.get("chunk_index") or 0)
                text = str(chunk.get("chunk_text") or "")
                embedding = self._embedder.embed_text(text)
                chunk_docs.append(
                    {
                        "chunk_id": f"{doc_id}_chunk_{chunk_index:04d}",
                        "doc_id": str(doc_id),
                        "user_id": str(user_id),
                        "chunk_index": chunk_index,
                        "chunk_text": text,
                        "page_start": chunk.get("page_start"),
                        "page_end": chunk.get("page_end"),
                        "embedding": embedding,
                        "keyword_tokens": keyword_tokens(text),
                        "token_count": int(chunk.get("token_count") or 0),
                        "created_at": now,
                    }
                )
                if chunk_index == 0 or chunk_index == total_chunks - 1 or chunk_index % 10 == 0:
                    dynamic_progress = 70 + int((chunk_index + 1) * 20 / total_chunks)
                    await self._mark_progress(
                        user_id=user_id,
                        doc_id=doc_id,
                        stage="embedding_chunks",
                        progress=dynamic_progress,
                    )

            await self._mark_progress(user_id=user_id, doc_id=doc_id, stage="persisting_chunks", progress=95)
            chunk_count = await self._repo.replace_chunks(user_id=user_id, doc_id=doc_id, chunks=chunk_docs)
            await self._repo.update_document(
                user_id=user_id,
                doc_id=doc_id,
                updates={
                    "status": "ready",
                    "processing_stage": "ready",
                    "processing_progress": 100,
                    "sha256": sha256,
                    "chunk_count": int(chunk_count),
                    "page_count": int(extracted.page_count),
                    "error_message": None,
                    "processing_completed_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                },
            )
            _logger.info(
                "documents.processed_ready",
                user_id=user_id,
                doc_id=doc_id,
                pages=extracted.page_count,
                chunks=chunk_count,
            )
            return {"doc_id": doc_id, "status": "ready", "chunk_count": int(chunk_count)}
        except Exception as exc:
            error_text = str(exc)[:500]
            await self._repo.update_document(
                user_id=user_id,
                doc_id=doc_id,
                updates={
                    "status": "failed",
                    "processing_stage": "failed",
                    "processing_progress": 100,
                    "error_message": error_text,
                    "updated_at": utc_now_iso(),
                },
            )
            _logger.error("documents.process_failed", user_id=user_id, doc_id=doc_id, error=error_text)
            return {"doc_id": doc_id, "status": "failed", "error_message": error_text}
