"""
TAOS Built-in Tool: Process Document.

Provides tool-level orchestration hook for document processing lifecycle.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from taos.config.constants import ToolRiskLevel
from taos.core.documents.processing_service import DocumentProcessingService
from taos.core.tools.registry import ToolDefinition, ToolPolicy


_processing = DocumentProcessingService()


async def process_document(
    user_id: str,
    doc_id: str,
    wait_for_ready: bool = False,
    max_wait_seconds: int = 90,
    poll_seconds: float = 2.0,
) -> Dict[str, Any]:
    """
    Trigger document processing and optionally wait for terminal status.
    """
    uid = str(user_id or "").strip()
    did = str(doc_id or "").strip()
    if not uid:
        return {"success": False, "error": "user_id is required"}
    if not did:
        return {"success": False, "error": "doc_id is required"}

    try:
        queued = await _processing.enqueue_processing(user_id=uid, doc_id=did)
    except KeyError:
        return {"success": False, "error": f"Document not found: {did}", "doc_id": did}
    except Exception as exc:
        return {"success": False, "error": f"Failed to enqueue processing: {str(exc)}", "doc_id": did}

    if not wait_for_ready:
        return {
            "success": True,
            "doc_id": did,
            "status": str(queued.get("status") or "processing"),
            "queued": True,
        }

    deadline = time.time() + max(5, int(max_wait_seconds or 90))
    interval = max(0.5, min(float(poll_seconds or 2.0), 10.0))
    last_status: Dict[str, Any] | None = None
    while time.time() < deadline:
        last_status = await _processing.get_status(user_id=uid, doc_id=did)
        status = str((last_status or {}).get("status") or "")
        if status in {"ready", "failed"}:
            return {
                "success": status == "ready",
                "doc_id": did,
                "status": status,
                "chunk_count": (last_status or {}).get("chunk_count"),
                "page_count": (last_status or {}).get("page_count"),
                "error_message": (last_status or {}).get("error_message"),
            }
        await asyncio.sleep(interval)

    return {
        "success": False,
        "doc_id": did,
        "status": str((last_status or {}).get("status") or "processing"),
        "error": f"Timed out waiting for document readiness after {int(max_wait_seconds)}s",
    }


def create_process_document_tool() -> ToolDefinition:
    return ToolDefinition(
        name="process_document",
        description=(
            "Trigger and monitor document processing for uploaded PDFs. "
            "Use wait_for_ready=true to block until ready/failed."
        ),
        input_schema={
            "user_id": "str",
            "doc_id": "str",
            "wait_for_ready": "bool",
            "max_wait_seconds": "int",
            "poll_seconds": "float",
        },
        handler=process_document,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=15,
            risk_level=ToolRiskLevel.MEDIUM,
            audit_required=True,
        ),
        rate_limit=30,
        cost_estimate=0.0,
        timeout=95,
        tags=["documents", "processing", "rag"],
    )

