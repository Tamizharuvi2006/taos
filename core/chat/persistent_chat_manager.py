"""Persistent chat session manager."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from taos.infra.firebase import get_firestore_client
from taos.infra.logging.logger import TAOSLogger
from taos.infra.persistence.store import StorageBackend
from taos.infra.persistence.shared_store import get_shared_store


CHATS_COLLECTION = "chats"
_logger = TAOSLogger(name="taos.chat")


class PersistentChatManager:
    def __init__(self, user_id: str, store: Optional[StorageBackend] = None) -> None:
        self._user_id = user_id
        self._last_list_source = "unknown"
        self._chat_cache: Dict[str, Dict[str, Any]] = {}
        self._signature_cache: Dict[str, str] = {}
        if store is not None:
            self._store = store
        else:
            self._store = get_shared_store()

    @staticmethod
    def _content_signature(title: str, messages: List[Dict[str, Any]], doc_ids: List[str]) -> str:
        """Stable signature used to skip no-op chat writes."""
        canonical = {
            "title": str(title or "New chat"),
            "messages": messages or [],
            "doc_ids": [str(doc_id).strip() for doc_id in (doc_ids or []) if str(doc_id).strip()],
        }
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    async def upsert_chat(self, chat_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        existing = self._chat_cache.get(chat_id)
        if existing is None:
            existing = await self._store.get(CHATS_COLLECTION, chat_id, user_id=self._user_id) or {}
        has_messages = "messages" in payload
        has_doc_ids = "doc_ids" in payload
        title = str(payload.get("title") or existing.get("title") or "New chat")
        messages = payload.get("messages") if has_messages else (existing.get("messages") or [])
        doc_ids = payload.get("doc_ids") if has_doc_ids else (existing.get("doc_ids") or [])
        signature = self._content_signature(title=title, messages=messages or [], doc_ids=doc_ids or [])

        # No-op fast path: identical content already in local cache.
        cached_signature = self._signature_cache.get(chat_id)
        if cached_signature and cached_signature == signature and chat_id in self._chat_cache:
            return self._chat_cache[chat_id]

        # No-op persistence guard: content matches existing stored document.
        if existing:
            existing_signature = self._content_signature(
                title=str(existing.get("title") or "New chat"),
                messages=existing.get("messages") or [],
                doc_ids=existing.get("doc_ids") or [],
            )
            if existing_signature == signature:
                self._chat_cache[chat_id] = existing
                self._signature_cache[chat_id] = signature
                return existing

        doc = {
            "id": chat_id,
            "title": title,
            "messages": messages,
            "doc_ids": doc_ids,
            "created_at": float(existing.get("created_at") or now),
            "updated_at": float(now),
        }
        await self._store.set(CHATS_COLLECTION, chat_id, doc, user_id=self._user_id)
        self._chat_cache[chat_id] = doc
        self._signature_cache[chat_id] = signature
        return doc

    @staticmethod
    def _to_ts(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, datetime):
            return float(value.timestamp())
        if hasattr(value, "timestamp"):
            try:
                return float(value.timestamp())
            except Exception:
                return default
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _msg_text(data: Dict[str, Any]) -> str:
        return str(
            data.get("text")
            or data.get("content")
            or data.get("message")
            or data.get("body")
            or ""
        )

    @staticmethod
    def _msg_role(data: Dict[str, Any]) -> str:
        role = str(data.get("role") or data.get("sender") or "assistant").strip().lower()
        if role in {"user", "assistant", "system", "tool"}:
            return role
        return "assistant"

    async def _list_legacy_chats(self, limit: int = 100) -> List[Dict[str, Any]]:
        db = get_firestore_client()
        if db is None:
            return []
        try:
            sessions_ref = db.collection("users").document(self._user_id).collection("chatSessions")
            session_docs = list(sessions_ref.stream())
            if not session_docs:
                return []

            result: List[Dict[str, Any]] = []
            for sdoc in session_docs:
                sdata = sdoc.to_dict() or {}
                created_at = self._to_ts(
                    sdata.get("createdAt") or sdata.get("created_at"),
                    default=time.time(),
                )
                updated_at = self._to_ts(
                    sdata.get("updatedAt") or sdata.get("updated_at") or created_at,
                    default=created_at,
                )

                messages: List[Dict[str, Any]] = []
                try:
                    msg_docs = list(sdoc.reference.collection("messages").stream())
                    for mdoc in msg_docs:
                        mdata = mdoc.to_dict() or {}
                        ts = self._to_ts(
                            mdata.get("timestamp")
                            or mdata.get("ts")
                            or mdata.get("createdAt"),
                            default=updated_at,
                        )
                        messages.append(
                            {
                                "id": str(mdoc.id),
                                "role": self._msg_role(mdata),
                                "text": self._msg_text(mdata),
                                "status": str(mdata.get("status") or "done"),
                                "ts": ts,
                            }
                        )
                    messages.sort(key=lambda m: float(m.get("ts", 0)))
                except Exception as exc:
                    _logger.warning("chat.legacy_messages_failed", user_id=self._user_id, chat_id=sdoc.id, error=str(exc))

                result.append(
                    {
                        "id": str(sdoc.id),
                        "title": str(sdata.get("name") or sdata.get("title") or "New chat"),
                        "messages": messages,
                        "doc_ids": [],
                        "created_at": created_at,
                        "updated_at": updated_at,
                    }
                )

            result.sort(key=lambda d: d.get("updated_at", 0), reverse=True)
            result = result[: max(1, int(limit))]

            # Backfill into TAOS store so future reads are fast and editable.
            for doc in result:
                await self._store.set(CHATS_COLLECTION, str(doc.get("id")), doc, user_id=self._user_id)
            return result
        except Exception as exc:
            _logger.warning("chat.legacy_sessions_failed", user_id=self._user_id, error=str(exc))
            return []

    async def list_chats(self, limit: int = 100) -> List[Dict[str, Any]]:
        docs = await self._store.list(CHATS_COLLECTION, user_id=self._user_id, limit=limit)
        if docs:
            backend_name = type(self._store).__name__.lower()
            if "firestore" in backend_name:
                self._last_list_source = "store_firestore"
            elif "memory" in backend_name:
                self._last_list_source = "memory"
            else:
                self._last_list_source = "store"
        if not docs:
            docs = await self._list_legacy_chats(limit=limit)
            if docs:
                self._last_list_source = "legacy_firebase"
            else:
                backend_name = type(self._store).__name__.lower()
                if "memory" in backend_name:
                    self._last_list_source = "memory_empty"
                elif "firestore" in backend_name:
                    self._last_list_source = "store_firestore_empty"
                else:
                    self._last_list_source = "empty"
        docs = sorted(docs, key=lambda d: d.get("updated_at", 0), reverse=True)
        for doc in docs:
            chat_id = str(doc.get("id") or "")
            if not chat_id:
                continue
            self._chat_cache[chat_id] = doc
            self._signature_cache[chat_id] = self._content_signature(
                title=str(doc.get("title") or "New chat"),
                messages=doc.get("messages") or [],
                doc_ids=doc.get("doc_ids") or [],
            )
        return docs

    async def delete_chat(self, chat_id: str) -> bool:
        deleted = await self._store.delete(CHATS_COLLECTION, chat_id, user_id=self._user_id)
        self._chat_cache.pop(chat_id, None)
        self._signature_cache.pop(chat_id, None)
        return deleted

    async def get_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        chat = await self._store.get(CHATS_COLLECTION, chat_id, user_id=self._user_id)
        if chat:
            self._chat_cache[chat_id] = chat
            self._signature_cache[chat_id] = self._content_signature(
                title=str(chat.get("title") or "New chat"),
                messages=chat.get("messages") or [],
                doc_ids=chat.get("doc_ids") or [],
            )
            return chat
        legacy = await self._list_legacy_chats(limit=200)
        for row in legacy:
            if str(row.get("id") or "") == str(chat_id):
                self._chat_cache[chat_id] = row
                self._signature_cache[chat_id] = self._content_signature(
                    title=str(row.get("title") or "New chat"),
                    messages=row.get("messages") or [],
                    doc_ids=row.get("doc_ids") or [],
                )
                return row
        return None

    def last_list_source(self) -> str:
        return self._last_list_source
