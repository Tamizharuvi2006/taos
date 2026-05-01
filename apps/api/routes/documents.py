"""Document upload/process/status/ask routes."""

from __future__ import annotations

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from taos.apps.api.auth_context import require_uid
from taos.apps.api.errors import raise_api_error
from taos.apps.api.schemas.documents import (
    AskRequest,
    AskResponse,
    DeleteDocumentResponse,
    DocumentInfoResponse,
    DocumentStatusResponse,
    ProcessDocumentRequest,
    ProcessDocumentResponse,
    UploadInitRequest,
    UploadInitResponse,
)
from taos.core.chat.persistent_chat_manager import PersistentChatManager
from taos.core.documents.ask_service import DocumentAskService
from taos.core.documents.processing_service import DocumentProcessingService
from taos.infra.logging.logger import TAOSLogger

router = APIRouter(prefix="/api", tags=["documents"])
_logger = TAOSLogger(name="taos.api.documents")
_processing = DocumentProcessingService()
_ask = DocumentAskService()


def _to_document_info(data: dict) -> DocumentInfoResponse:
    return DocumentInfoResponse(
        doc_id=str(data.get("doc_id") or ""),
        file_name=str(data.get("file_name") or ""),
        storage_path=str(data.get("storage_path") or ""),
        mime_type=str(data.get("mime_type") or ""),
        file_size=int(data.get("file_size") or 0),
        status=str(data.get("status") or "uploaded"),
        processing_stage=data.get("processing_stage"),
        processing_progress=data.get("processing_progress"),
        sha256=data.get("sha256"),
        chunk_count=data.get("chunk_count"),
        page_count=data.get("page_count"),
        error_message=data.get("error_message"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


async def _enforce_chat_scope(user_id: str, payload: AskRequest, rid: str) -> None:
    if not payload.chat_id:
        return
    chat_mgr = PersistentChatManager(user_id=user_id)
    chat = await chat_mgr.get_chat(payload.chat_id)
    if not chat:
        raise_api_error(404, "CHAT_NOT_FOUND", "Chat not found", rid)
    allowed_doc_ids = {str(d).strip() for d in (chat.get("doc_ids") or []) if str(d).strip()}
    requested_doc_ids = {str(d).strip() for d in payload.doc_ids if str(d).strip()}
    if not requested_doc_ids.issubset(allowed_doc_ids):
        raise_api_error(
            403,
            "DOCUMENT_SCOPE_VIOLATION",
            "Requested document IDs are not attached to this chat",
            rid,
        )


@router.post("/upload/init", response_model=UploadInitResponse, summary="Initialize document upload metadata")
async def upload_init(payload: UploadInitRequest, raw_request: Request) -> UploadInitResponse:
    rid = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = require_uid(raw_request)
    try:
        doc = await _processing.init_upload(
            user_id=user_id,
            file_name=payload.file_name,
            file_size=payload.file_size,
            mime_type=payload.mime_type,
        )
        return UploadInitResponse(
            doc_id=str(doc.get("doc_id")),
            storage_path=str(doc.get("storage_path")),
            status=str(doc.get("status") or "uploaded"),
        )
    except ValueError as exc:
        raise_api_error(400, "UPLOAD_INVALID_REQUEST", str(exc), rid)
    except Exception as exc:
        _logger.error("documents.upload_init_failed", error=str(exc))
        raise_api_error(500, "UPLOAD_INIT_FAILED", "Could not initialize upload", rid)
    return UploadInitResponse(doc_id="", storage_path="", status="failed")


@router.post("/process-document", response_model=ProcessDocumentResponse, summary="Process uploaded PDF into chunks")
async def process_document(payload: ProcessDocumentRequest, raw_request: Request) -> ProcessDocumentResponse:
    rid = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = require_uid(raw_request)
    try:
        result = await _processing.enqueue_processing(user_id=user_id, doc_id=payload.doc_id)
        return ProcessDocumentResponse(
            doc_id=str(result.get("doc_id")),
            status=str(result.get("status") or "processing"),
        )
    except KeyError:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", "Document not found", rid)
    except Exception as exc:
        _logger.error("documents.process_enqueue_failed", error=str(exc), doc_id=payload.doc_id)
        raise_api_error(500, "PROCESS_ENQUEUE_FAILED", "Could not start document processing", rid)
    return ProcessDocumentResponse(doc_id=payload.doc_id, status="failed")


@router.get(
    "/document/{doc_id}/status",
    response_model=DocumentStatusResponse,
    summary="Get document processing status",
)
async def document_status(doc_id: str, raw_request: Request) -> DocumentStatusResponse:
    rid = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = require_uid(raw_request)
    status_doc = await _processing.get_status(user_id=user_id, doc_id=doc_id)
    if not status_doc:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", "Document not found", rid)
    return DocumentStatusResponse(
        doc_id=str(status_doc.get("doc_id") or doc_id),
        status=str(status_doc.get("status") or "uploaded"),
        processing_stage=status_doc.get("processing_stage"),
        processing_progress=status_doc.get("processing_progress"),
        chunk_count=status_doc.get("chunk_count"),
        page_count=status_doc.get("page_count"),
        error_message=status_doc.get("error_message"),
    )


@router.get("/documents", response_model=list[DocumentInfoResponse], summary="List uploaded documents")
async def list_documents(raw_request: Request) -> list[DocumentInfoResponse]:
    user_id = require_uid(raw_request)
    rows = await _processing.list_documents(user_id=user_id, limit=200)
    return [_to_document_info(row) for row in rows]


@router.get("/document/{doc_id}", response_model=DocumentInfoResponse, summary="Get document metadata")
async def get_document(doc_id: str, raw_request: Request) -> DocumentInfoResponse:
    rid = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = require_uid(raw_request)
    row = await _processing.get_status(user_id=user_id, doc_id=doc_id)
    if not row:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", "Document not found", rid)
    return _to_document_info(row)


@router.delete("/document/{doc_id}", response_model=DeleteDocumentResponse, summary="Delete document and chunks")
async def delete_document(doc_id: str, raw_request: Request) -> DeleteDocumentResponse:
    user_id = require_uid(raw_request)
    deleted = await _processing.delete_document(user_id=user_id, doc_id=doc_id)
    return DeleteDocumentResponse(doc_id=doc_id, deleted=bool(deleted))


@router.post("/ask", response_model=AskResponse, summary="Answer a question from uploaded documents")
async def ask_document(payload: AskRequest, raw_request: Request) -> AskResponse:
    rid = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = require_uid(raw_request)
    try:
        await _enforce_chat_scope(user_id=user_id, payload=payload, rid=rid)
        result = await _ask.ask(
            user_id=user_id,
            doc_ids=payload.doc_ids,
            question=payload.question,
            mode=payload.mode,
            mark_format=payload.mark_format,
            unit_hint=payload.unit_hint,
            output_format=payload.output_format,
        )
        return AskResponse.model_validate(result)
    except KeyError as exc:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", str(exc), rid)
    except RuntimeError as exc:
        raise_api_error(409, "DOCUMENT_NOT_READY", str(exc), rid)
    except ValueError as exc:
        raise_api_error(400, "ASK_INVALID_REQUEST", str(exc), rid)
    except Exception as exc:
        _logger.error("documents.ask_failed", error=str(exc))
        raise_api_error(500, "ASK_FAILED", "Could not answer from document context", rid)
    return AskResponse(answer="", sources=[], confidence=0.0, cached=False, warnings=[])


@router.post("/ask/stream", summary="Stream an answer from uploaded documents (SSE)")
async def ask_document_stream(payload: AskRequest, raw_request: Request):
    rid = raw_request.headers.get("X-Request-ID", "unknown")
    user_id = require_uid(raw_request)

    await _enforce_chat_scope(user_id=user_id, payload=payload, rid=rid)

    async def event_stream():
        start_payload = {"request_id": rid, "phase": "start"}
        yield f"event: START\ndata: {json.dumps(start_payload)}\n\n"
        try:
            async for event in _ask.stream(
                user_id=user_id,
                doc_ids=payload.doc_ids,
                question=payload.question,
                mode=payload.mode,
                mark_format=payload.mark_format,
                unit_hint=payload.unit_hint,
                output_format=payload.output_format,
            ):
                if event.get("type") == "partial":
                    body = {"request_id": rid, "partial_result": str(event.get('text') or "")}
                    yield f"event: PARTIAL\ndata: {json.dumps(body)}\n\n"
                    continue
                if event.get("type") == "final":
                    body = {"request_id": rid, "payload": event.get("payload") or {}}
                    yield f"event: FINAL\ndata: {json.dumps(body)}\n\n"
                    return
            yield f"event: FINAL\ndata: {json.dumps({'request_id': rid, 'payload': {}})}\n\n"
        except KeyError as exc:
            body = {"request_id": rid, "error_code": "DOCUMENT_NOT_FOUND", "message": str(exc)}
            yield f"event: ERROR\ndata: {json.dumps(body)}\n\n"
        except RuntimeError as exc:
            body = {"request_id": rid, "error_code": "DOCUMENT_NOT_READY", "message": str(exc)}
            yield f"event: ERROR\ndata: {json.dumps(body)}\n\n"
        except ValueError as exc:
            body = {"request_id": rid, "error_code": "ASK_INVALID_REQUEST", "message": str(exc)}
            yield f"event: ERROR\ndata: {json.dumps(body)}\n\n"
        except Exception as exc:
            _logger.error("documents.ask_stream_failed", error=str(exc))
            body = {"request_id": rid, "error_code": "ASK_STREAM_FAILED", "message": "Could not stream answer"}
            yield f"event: ERROR\ndata: {json.dumps(body)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
