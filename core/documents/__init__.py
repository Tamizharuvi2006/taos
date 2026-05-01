"""Document upload and retrieval services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from taos.core.documents.ask_service import DocumentAskService
    from taos.core.documents.processing_service import DocumentProcessingService
    from taos.core.documents.repository import DocumentRepository

__all__ = [
    "DocumentAskService",
    "DocumentProcessingService",
    "DocumentRepository",
]


def __getattr__(name: str) -> Any:
    # Keep package-level exports while preventing eager import cycles.
    if name == "DocumentAskService":
        from taos.core.documents.ask_service import DocumentAskService

        return DocumentAskService
    if name == "DocumentProcessingService":
        from taos.core.documents.processing_service import DocumentProcessingService

        return DocumentProcessingService
    if name == "DocumentRepository":
        from taos.core.documents.repository import DocumentRepository

        return DocumentRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

