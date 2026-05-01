"""Request-level abuse and upload guards."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PurePath, PureWindowsPath
import re
from typing import Iterable


ALLOWED_UPLOAD_EXTENSIONS = {".pdf"}
ALLOWED_UPLOAD_MIME_TYPES = {"application/pdf"}


@dataclass(frozen=True)
class RequestGuardResult:
    allowed: bool
    code: str = "allowed"
    reason: str = ""

    def raise_message(self) -> str:
        return self.reason or self.code


_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|developer|system)\s+instructions", re.I),
    re.compile(r"reveal\s+(the\s+)?(system|developer)\s+prompt", re.I),
    re.compile(r"show\s+me\s+your\s+(system|developer)\s+prompt", re.I),
    re.compile(r"pretend\s+i\s+am\s+(admin|superadmin)", re.I),
    re.compile(r"disable\s+(safety|guardrails|auth|authentication)", re.I),
]

_SECRET_FILE_PATTERNS = [
    re.compile(r"(?i)(read|open|show|print|cat|display)\s+.*(\.env|firebase.*credential|service[_-]?account|private[_-]?key)"),
    re.compile(r"(?i)(openrouter|serper|firebase|razorpay|telegram|zeptomail|whatsapp).*(api[_-]?key|secret|token)"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password).*(from|in)\s+.*(\.env|file|filesystem)"),
]


def _suffix(filename: str) -> str:
    return PurePath(filename).suffix.lower() or PureWindowsPath(filename).suffix.lower()


def _contains_path_traversal(filename: str) -> bool:
    text = str(filename or "").strip()
    if not text:
        return True
    path = PureWindowsPath(text)
    if path.is_absolute() or PurePath(text).is_absolute():
        return True
    normalized = text.replace("\\", "/")
    if "/" in normalized:
        return True
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    return any(part in {".", ".."} for part in parts) or ".." in text


def validate_upload_request(
    *,
    file_name: str,
    file_size: int,
    mime_type: str,
    max_size_bytes: int,
    allowed_extensions: Iterable[str] | None = None,
    allowed_mime_types: Iterable[str] | None = None,
) -> RequestGuardResult:
    clean_name = str(file_name or "").strip()
    if _contains_path_traversal(clean_name):
        return RequestGuardResult(False, "upload_path_traversal", "Upload filename is not allowed")

    allowed_exts = {str(ext).lower() for ext in (allowed_extensions or ALLOWED_UPLOAD_EXTENSIONS)}
    allowed_mimes = {str(mime).lower() for mime in (allowed_mime_types or ALLOWED_UPLOAD_MIME_TYPES)}
    if _suffix(clean_name) not in allowed_exts:
        return RequestGuardResult(False, "upload_type_blocked", "Only PDF uploads are allowed")

    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime not in allowed_mimes:
        return RequestGuardResult(False, "upload_mime_blocked", "Only PDF uploads are allowed")

    try:
        size = int(file_size)
    except Exception:
        return RequestGuardResult(False, "upload_size_invalid", "Upload size is invalid")
    if size <= 0:
        return RequestGuardResult(False, "upload_size_invalid", "Upload size is invalid")
    if int(max_size_bytes) > 0 and size > int(max_size_bytes):
        return RequestGuardResult(False, "upload_too_large", "File is too large")

    return RequestGuardResult(True)


def validate_user_prompt(text: str) -> RequestGuardResult:
    prompt = str(text or "")
    for pattern in _SECRET_FILE_PATTERNS:
        if pattern.search(prompt):
            return RequestGuardResult(
                False,
                "secret_or_private_file_request",
                "Requests for private files, secrets, or provider keys are not allowed",
            )
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(prompt):
            return RequestGuardResult(
                False,
                "prompt_injection_attempt",
                "Prompt-injection or privilege-escalation request blocked",
            )
    return RequestGuardResult(True)


def dev_bypass_allowed(*, taos_env: str, auth_allow_dev_bypass: bool) -> bool:
    return str(taos_env or "").strip().lower() == "development" and bool(auth_allow_dev_bypass)


def filename_from_path(value: str) -> str:
    return os.path.basename(str(value or "").replace("\\", "/"))
