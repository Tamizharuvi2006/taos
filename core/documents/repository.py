"""Firestore-backed repository for document upload pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from google.cloud.firestore_v1.base_query import FieldFilter
except Exception:  # pragma: no cover - runtime compatibility fallback
    FieldFilter = None

from taos.infra.firebase import get_firestore_client
from taos.infra.logging.logger import TAOSLogger

_logger = TAOSLogger(name="taos.documents.repo")

_MEM_DOCUMENTS: Dict[Tuple[str, str], Dict[str, Any]] = {}
_MEM_CHUNKS: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
_MEM_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


class DocumentRepository:
    def __init__(self, use_firestore: bool = True) -> None:
        self._db = get_firestore_client() if use_firestore else None

    @property
    def uses_firestore(self) -> bool:
        return self._db is not None

    @staticmethod
    def _where_eq(query: Any, field: str, value: Any) -> Any:
        """Use keyword filter form when available to avoid Firestore positional where warnings."""
        if FieldFilter is not None:
            try:
                return query.where(filter=FieldFilter(field, "==", value))
            except Exception:
                pass
        return query.where(field, "==", value)

    async def create_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        user_id = str(document.get("user_id") or "")
        doc_id = str(document.get("doc_id") or "")
        if not user_id or not doc_id:
            raise ValueError("document requires user_id and doc_id")
        if self._db is not None:
            try:
                self._db.collection("documents").document(doc_id).set(document)
                return document
            except Exception as exc:
                _logger.warning("documents.create_firestore_fallback", error=str(exc))
        _MEM_DOCUMENTS[(user_id, doc_id)] = dict(document)
        return document

    async def get_document(self, user_id: str, doc_id: str) -> Optional[Dict[str, Any]]:
        if self._db is not None:
            try:
                snap = self._db.collection("documents").document(doc_id).get()
                if not snap.exists:
                    return None
                data = snap.to_dict() or {}
                if str(data.get("user_id") or "") != str(user_id):
                    return None
                data.setdefault("doc_id", str(doc_id))
                return data
            except Exception as exc:
                _logger.warning("documents.get_firestore_fallback", error=str(exc))
        data = _MEM_DOCUMENTS.get((str(user_id), str(doc_id)))
        return dict(data) if data else None

    async def update_document(self, user_id: str, doc_id: str, updates: Dict[str, Any]) -> None:
        if self._db is not None:
            try:
                self._db.collection("documents").document(doc_id).set(dict(updates), merge=True)
            except Exception as exc:
                _logger.warning("documents.update_firestore_fallback", error=str(exc))
        key = (str(user_id), str(doc_id))
        existing = _MEM_DOCUMENTS.get(key, {"user_id": str(user_id), "doc_id": str(doc_id)})
        existing.update(updates)
        _MEM_DOCUMENTS[key] = existing

    async def find_ready_by_sha(self, user_id: str, sha256: str, exclude_doc_id: str = "") -> Optional[Dict[str, Any]]:
        target_uid = str(user_id)
        target_sha = str(sha256)
        exclude = str(exclude_doc_id or "")
        if self._db is not None:
            try:
                query = self._db.collection("documents")
                query = self._where_eq(query, "user_id", target_uid)
                query = self._where_eq(query, "sha256", target_sha)
                query = self._where_eq(query, "status", "ready").limit(5)
                for snap in query.stream():
                    data = snap.to_dict() or {}
                    if str(data.get("doc_id") or snap.id) == exclude:
                        continue
                    return data
            except Exception as exc:
                _logger.warning("documents.find_sha_firestore_fallback", error=str(exc))
        for (uid, _doc_id), data in _MEM_DOCUMENTS.items():
            if uid != target_uid:
                continue
            if str(data.get("sha256") or "") != target_sha:
                continue
            if str(data.get("status") or "") != "ready":
                continue
            if str(data.get("doc_id") or "") == exclude:
                continue
            return dict(data)
        return None

    async def replace_chunks(self, user_id: str, doc_id: str, chunks: List[Dict[str, Any]]) -> int:
        uid = str(user_id)
        did = str(doc_id)
        if self._db is not None:
            try:
                existing_q = self._db.collection("chunks")
                existing_q = self._where_eq(existing_q, "user_id", uid)
                existing_q = self._where_eq(existing_q, "doc_id", did)
                existing = existing_q.stream()
                batch = self._db.batch()
                pending = 0
                for snap in existing:
                    batch.delete(snap.reference)
                    pending += 1
                    if pending >= 350:
                        batch.commit()
                        batch = self._db.batch()
                        pending = 0
                if pending:
                    batch.commit()

                batch = self._db.batch()
                pending = 0
                for chunk in chunks:
                    chunk_id = str(chunk.get("chunk_id"))
                    ref = self._db.collection("chunks").document(chunk_id)
                    batch.set(ref, chunk)
                    pending += 1
                    if pending >= 350:
                        batch.commit()
                        batch = self._db.batch()
                        pending = 0
                if pending:
                    batch.commit()
            except Exception as exc:
                _logger.warning("documents.replace_chunks_firestore_fallback", error=str(exc))
        _MEM_CHUNKS[(uid, did)] = [dict(chunk) for chunk in chunks]
        return len(chunks)

    async def list_documents(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        uid = str(user_id)
        rows: List[Dict[str, Any]] = []
        if self._db is not None:
            try:
                query = self._db.collection("documents")
                query = self._where_eq(query, "user_id", uid).limit(max(1, int(limit)))
                for snap in query.stream():
                    data = snap.to_dict() or {}
                    data.setdefault("doc_id", str(snap.id))
                    rows.append(data)
                if rows:
                    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                    return rows
            except Exception as exc:
                _logger.warning("documents.list_firestore_fallback", error=str(exc))
        for (mem_uid, _doc_id), data in _MEM_DOCUMENTS.items():
            if mem_uid != uid:
                continue
            rows.append(dict(data))
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return rows[: max(1, int(limit))]

    async def clone_chunks(self, user_id: str, source_doc_id: str, target_doc_id: str, created_at: str) -> int:
        src = await self.list_chunks(user_id=user_id, doc_ids=[source_doc_id])
        cloned: List[Dict[str, Any]] = []
        for i, chunk in enumerate(src):
            copied = dict(chunk)
            copied["doc_id"] = str(target_doc_id)
            copied["chunk_index"] = i
            copied["chunk_id"] = f"{target_doc_id}_chunk_{i:04d}"
            copied["created_at"] = created_at
            cloned.append(copied)
        return await self.replace_chunks(user_id=user_id, doc_id=target_doc_id, chunks=cloned)

    async def list_chunks(self, user_id: str, doc_ids: List[str]) -> List[Dict[str, Any]]:
        uid = str(user_id)
        normalized = [str(d) for d in doc_ids if str(d).strip()]
        if not normalized:
            return []

        combined: List[Dict[str, Any]] = []
        if self._db is not None:
            try:
                for doc_id in normalized:
                    query = self._db.collection("chunks")
                    query = self._where_eq(query, "user_id", uid)
                    query = self._where_eq(query, "doc_id", doc_id)
                    for snap in query.stream():
                        combined.append(snap.to_dict() or {})
                if combined:
                    combined.sort(key=lambda item: (str(item.get("doc_id") or ""), int(item.get("chunk_index") or 0)))
                    return combined
            except Exception as exc:
                _logger.warning("documents.list_chunks_firestore_fallback", error=str(exc))

        for doc_id in normalized:
            combined.extend(_MEM_CHUNKS.get((uid, doc_id), []))
        combined.sort(key=lambda item: (str(item.get("doc_id") or ""), int(item.get("chunk_index") or 0)))
        return [dict(item) for item in combined]

    async def get_cache(self, user_id: str, cache_key: str) -> Optional[Dict[str, Any]]:
        uid = str(user_id)
        key = str(cache_key)
        if self._db is not None:
            try:
                snap = self._db.collection("query_cache").document(key).get()
                if snap.exists:
                    data = snap.to_dict() or {}
                    if str(data.get("user_id") or "") == uid:
                        return data
            except Exception as exc:
                _logger.warning("documents.get_cache_firestore_fallback", error=str(exc))
        data = _MEM_CACHE.get((uid, key))
        return dict(data) if data else None

    async def set_cache(self, user_id: str, cache_key: str, data: Dict[str, Any]) -> None:
        uid = str(user_id)
        key = str(cache_key)
        payload = dict(data)
        payload["user_id"] = uid
        payload["cache_key"] = key
        if self._db is not None:
            try:
                self._db.collection("query_cache").document(key).set(payload)
            except Exception as exc:
                _logger.warning("documents.set_cache_firestore_fallback", error=str(exc))
        _MEM_CACHE[(uid, key)] = payload

    async def delete_document(self, user_id: str, doc_id: str) -> bool:
        uid = str(user_id)
        did = str(doc_id)
        deleted_any = False

        if self._db is not None:
            try:
                snap = self._db.collection("documents").document(did).get()
                if snap.exists:
                    data = snap.to_dict() or {}
                    if str(data.get("user_id") or "") == uid:
                        snap.reference.delete()
                        deleted_any = True

                chunk_query = self._db.collection("chunks")
                chunk_query = self._where_eq(chunk_query, "user_id", uid)
                chunk_query = self._where_eq(chunk_query, "doc_id", did)
                chunk_query = chunk_query.stream()
                batch = self._db.batch()
                pending = 0
                for chunk_snap in chunk_query:
                    batch.delete(chunk_snap.reference)
                    pending += 1
                    if pending >= 350:
                        batch.commit()
                        batch = self._db.batch()
                        pending = 0
                if pending:
                    batch.commit()
            except Exception as exc:
                _logger.warning("documents.delete_firestore_fallback", error=str(exc))

        if _MEM_DOCUMENTS.pop((uid, did), None) is not None:
            deleted_any = True
        if _MEM_CHUNKS.pop((uid, did), None) is not None:
            deleted_any = True

        keys_to_remove = []
        for (cache_uid, cache_key), payload in _MEM_CACHE.items():
            if cache_uid != uid:
                continue
            doc_ids = payload.get("doc_ids") or []
            if did in [str(x) for x in doc_ids]:
                keys_to_remove.append((cache_uid, cache_key))
        for key in keys_to_remove:
            _MEM_CACHE.pop(key, None)

        return deleted_any
