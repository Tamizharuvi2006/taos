"""Firebase Storage access for document pipeline."""

from __future__ import annotations

from taos.config.settings import get_settings
from taos.infra.firebase import init_firebase_admin


class DocumentStorageService:
    """Read uploaded files from Firebase Storage."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def _bucket_candidates(self) -> list[str]:
        configured = (self._settings.firebase_storage_bucket or "").strip()
        if configured:
            return [configured]
        project_id = (self._settings.firebase_project_id or "").strip()
        if project_id:
            return [
                f"{project_id}.firebasestorage.app",
                f"{project_id}.appspot.com",
            ]
        raise RuntimeError("Firebase storage bucket is not configured")

    def download_bytes(self, storage_path: str) -> bytes:
        if not storage_path or not str(storage_path).strip():
            raise ValueError("storage_path is required")
        init_firebase_admin()
        from firebase_admin import storage

        normalized_path = str(storage_path).strip()
        last_error: Exception | None = None
        for bucket_name in self._bucket_candidates():
            try:
                bucket = storage.bucket(bucket_name)
                blob = bucket.blob(normalized_path)
                if blob.exists():
                    return blob.download_as_bytes()
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise FileNotFoundError(
                f"Uploaded file not found in configured Firebase buckets for path: {storage_path}"
            ) from last_error
        raise FileNotFoundError(f"Uploaded file not found in storage: {storage_path}")
