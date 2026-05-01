"""Document upload and QA API schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

DocumentStatus = Literal["uploaded", "processing", "ready", "failed"]


class UploadInitRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=512)
    file_size: int = Field(..., ge=1)
    mime_type: str = Field(..., min_length=1, max_length=128)

    @field_validator("file_name")
    @classmethod
    def _validate_file_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("file_name cannot be empty")
        return clean

    @field_validator("mime_type")
    @classmethod
    def _validate_mime_type(cls, value: str) -> str:
        return value.strip().lower()


class UploadInitResponse(BaseModel):
    doc_id: str
    storage_path: str
    status: DocumentStatus


class ProcessDocumentRequest(BaseModel):
    doc_id: str = Field(..., min_length=4, max_length=128)


class ProcessDocumentResponse(BaseModel):
    doc_id: str
    status: DocumentStatus


class DocumentStatusResponse(BaseModel):
    doc_id: str
    status: DocumentStatus
    processing_stage: Optional[str] = None
    processing_progress: Optional[int] = None
    chunk_count: Optional[int] = None
    page_count: Optional[int] = None
    error_message: Optional[str] = None


class DocumentInfoResponse(BaseModel):
    doc_id: str
    file_name: str
    storage_path: str
    mime_type: str
    file_size: int
    status: DocumentStatus
    processing_stage: Optional[str] = None
    processing_progress: Optional[int] = None
    sha256: Optional[str] = None
    chunk_count: Optional[int] = None
    page_count: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeleteDocumentResponse(BaseModel):
    doc_id: str
    deleted: bool


class AskRequest(BaseModel):
    doc_ids: List[str] = Field(..., min_length=1)
    question: str = Field(..., min_length=2, max_length=4000)
    chat_id: Optional[str] = Field(default=None, min_length=3, max_length=256)
    mode: Optional[str] = Field(default=None, min_length=2, max_length=64)
    mark_format: Optional[str] = Field(default=None, min_length=1, max_length=4)
    unit_hint: Optional[str] = Field(default=None, min_length=1, max_length=128)
    output_format: Optional[str] = Field(default=None, min_length=3, max_length=32)

    @field_validator("doc_ids")
    @classmethod
    def _validate_doc_ids(cls, value: List[str]) -> List[str]:
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        if not cleaned:
            raise ValueError("doc_ids cannot be empty")
        return cleaned

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("question cannot be empty")
        return clean

    @field_validator("chat_id")
    @classmethod
    def _validate_chat_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip()
        return clean or None

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip().lower()
        return clean or None

    @field_validator("mark_format")
    @classmethod
    def _validate_mark_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip()
        return clean or None

    @field_validator("unit_hint")
    @classmethod
    def _validate_unit_hint(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip()
        return clean or None

    @field_validator("output_format")
    @classmethod
    def _validate_output_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip().lower()
        return clean or None


class AskSource(BaseModel):
    doc_id: str
    chunk_id: str
    chunk_index: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    score: Optional[float] = None


class AskResponse(BaseModel):
    answer: str
    mode: Optional[str] = None
    sources: List[AskSource] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    cached: bool = False
    warnings: List[str] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
