"""Schemas for feedback memory ingestion endpoints."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class FeedbackRequest(BaseModel):
    """POST /feedback — store explicit correction feedback."""

    user_id: str = Field(default="default", min_length=1, max_length=128)
    query: str = Field(..., min_length=2, max_length=2000)
    bad_answer: str = Field(..., min_length=1, max_length=8000)
    corrected_answer: str = Field(..., min_length=1, max_length=8000)
    tags: Optional[List[str]] = Field(default=None, max_length=20)
    rating: int = Field(default=-1, ge=-1, le=1, description="-1 bad, 0 neutral, 1 good")

    @field_validator("query", "bad_answer", "corrected_answer")
    @classmethod
    def strip_required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be blank")
        return v.strip()


class FeedbackResponse(BaseModel):
    success: bool = True
    feedback_id: str


class FeedbackSignalRequest(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    query: str = Field(..., min_length=1, max_length=2000)
    feedback_type: str = Field(..., min_length=1, max_length=64)
    answer: str = Field(default="", max_length=8000)
    route: str = Field(default="", max_length=128)
    answer_mode: str = Field(default="", max_length=128)
    source_quality: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=2000)


class FeedbackSignalResponse(BaseModel):
    success: bool = True
    feedback_id: str
    stored_for_review: bool = True
