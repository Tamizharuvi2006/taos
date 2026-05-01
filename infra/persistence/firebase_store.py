"""
TAOS Firebase Storage - Firestore-backed persistence.

Uses Firebase Admin SDK to store:
- Tasks (with execution history)
- Memory entries
- User preferences

Collection structure:
  users/{user_id}/tasks/{task_id}
  users/{user_id}/executions/{task_id}/items/{exec_id}
  users/{user_id}/memory/{key}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from taos.config.settings import get_settings
from taos.infra.logging.logger import TAOSLogger
from taos.infra.persistence.store import StorageBackend

_logger = TAOSLogger(name="taos.firebase")


class FirestoreStore(StorageBackend):
    """
    Firebase Firestore storage backend.

    Requires firebase-admin package and valid credentials.
    Falls back gracefully if Firebase is not configured.
    """

    def __init__(self) -> None:
        self._db = None
        self._initialized = False
        self._firestore_missing_logged = False
        self._active_database_id = ""
        self._last_error = ""
        self._init_firebase()

    def _handle_runtime_error(self, e: Exception) -> None:
        """Handle runtime firestore errors without crashing API routes."""
        err = str(e)
        self._last_error = err
        if "database (default) does not exist" in err.lower():
            if not self._firestore_missing_logged:
                _logger.warning(
                    "firebase.database_missing",
                    msg=(
                        "Firestore database is not created for this project. "
                        "Create Firestore in Firebase console or switch STORAGE_BACKEND=memory."
                    ),
                )
                self._firestore_missing_logged = True
            self._initialized = False
            return
        _logger.error("firebase.runtime_error", error=err)

    def _init_firebase(self) -> None:
        """Initialize Firebase Admin SDK."""
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
            from google.cloud import firestore as google_firestore

            settings = get_settings()
            project_id = settings.firebase_project_id
            requested_database_id = (settings.firebase_database_id or "").strip()
            database_candidates: list[str] = []
            if requested_database_id and requested_database_id != "(default)":
                database_candidates = [requested_database_id]
            else:
                if requested_database_id:
                    database_candidates.append(requested_database_id)
                if "(default)" not in database_candidates:
                    database_candidates.append("(default)")
                if "relyceinfotech" not in database_candidates:
                    database_candidates.append("relyceinfotech")

            try:
                firebase_admin.get_app()
            except ValueError:
                private_key_id = settings.firebase_private_key_id
                private_key = (settings.firebase_private_key or "").replace("\\n", "\n")
                client_email = settings.firebase_client_email
                client_id = settings.firebase_client_id
                auth_uri = settings.firebase_auth_uri
                token_uri = settings.firebase_token_uri
                auth_provider_x509_cert_url = settings.firebase_auth_provider_x509_cert_url
                client_x509_cert_url = settings.firebase_client_x509_cert_url
                universe_domain = settings.firebase_universe_domain

                if project_id and private_key and client_email:
                    cred_dict = {
                        "type": "service_account",
                        "project_id": project_id,
                        "private_key_id": private_key_id,
                        "private_key": private_key,
                        "client_email": client_email,
                        "client_id": client_id,
                        "auth_uri": auth_uri,
                        "token_uri": token_uri,
                        "auth_provider_x509_cert_url": auth_provider_x509_cert_url,
                        "client_x509_cert_url": client_x509_cert_url,
                        "universe_domain": universe_domain,
                    }
                    cred_dict = {k: v for k, v in cred_dict.items() if v}
                    cred = credentials.Certificate(cred_dict)
                    firebase_admin.initialize_app(cred)
                else:
                    _logger.warning(
                    "firebase.no_credentials",
                    msg="Set FIREBASE_PROJECT_ID, FIREBASE_PRIVATE_KEY, and FIREBASE_CLIENT_EMAIL to initialize.",
                )
                    self._last_error = "firebase_credentials_missing"
                    return

            self._initialized = False
            probe_error: Exception | None = None
            for database_id in database_candidates:
                try:
                    if database_id == "(default)":
                        self._db = firestore.client()
                    else:
                        app = firebase_admin.get_app()
                        cred = None
                        try:
                            cred = app.credential.get_credential()
                        except Exception:
                            cred = None
                        if cred is not None:
                            self._db = google_firestore.Client(
                                credentials=cred,
                                project=project_id or None,
                                database=database_id,
                            )
                        else:
                            self._db = google_firestore.Client(project=project_id or None, database=database_id)
                    _ = list(
                        self._db.collection("taos_health")
                        .document("startup")
                        .collection("ping")
                        .limit(1)
                        .stream()
                    )
                    self._initialized = True
                    self._active_database_id = database_id
                    break
                except Exception as probe_err:
                    probe_error = probe_err
                    self._db = None
                    continue
            if not self._initialized:
                if probe_error is not None:
                    self._last_error = str(probe_error)
                    _logger.warning(
                        "firebase.candidate_probe_failed",
                        project_id=project_id,
                        candidates=database_candidates,
                        error=str(probe_error),
                    )
                    self._handle_runtime_error(probe_error)
                return

            _logger.info("firebase.initialized", database_id=self._active_database_id, project_id=project_id)

        except ImportError:
            self._last_error = "firebase_admin_not_installed"
            _logger.warning("firebase.not_installed", msg="pip install firebase-admin")
        except Exception as e:
            self._last_error = str(e)
            _logger.error("firebase.init_error", error=str(e))

    @property
    def is_available(self) -> bool:
        return self._initialized and self._db is not None

    @property
    def last_error(self) -> str:
        return str(self._last_error or "").strip()

    async def get(self, collection: str, doc_id: str, user_id: str = "default") -> Optional[Dict[str, Any]]:
        if not self.is_available:
            return None
        try:
            doc = self._db.collection("users").document(user_id).collection(collection).document(doc_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            self._handle_runtime_error(e)
            return None

    async def set(self, collection: str, doc_id: str, data: Dict[str, Any], user_id: str = "default") -> None:
        if not self.is_available:
            return
        try:
            self._db.collection("users").document(user_id).collection(collection).document(doc_id).set(data)
        except Exception as e:
            self._handle_runtime_error(e)

    async def delete(self, collection: str, doc_id: str, user_id: str = "default") -> bool:
        if not self.is_available:
            return False
        try:
            self._db.collection("users").document(user_id).collection(collection).document(doc_id).delete()
            return True
        except Exception as e:
            self._handle_runtime_error(e)
            return False

    async def list(
        self,
        collection: str,
        user_id: str = "default",
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not self.is_available:
            return []
        try:
            ref = self._db.collection("users").document(user_id).collection(collection)
            if filters:
                for key, value in filters.items():
                    ref = ref.where(key, "==", value)
            docs = ref.limit(limit).stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            self._handle_runtime_error(e)
            return []

    async def update(self, collection: str, doc_id: str, updates: Dict[str, Any], user_id: str = "default") -> None:
        if not self.is_available:
            return
        try:
            self._db.collection("users").document(user_id).collection(collection).document(doc_id).update(updates)
        except Exception as e:
            self._handle_runtime_error(e)

    async def append_to_list(
        self,
        collection: str,
        doc_id: str,
        field: str,
        value: Any,
        user_id: str = "default",
    ) -> None:
        if not self.is_available:
            return
        try:
            from google.cloud.firestore_v1 import ArrayUnion

            self._db.collection("users").document(user_id).collection(collection).document(doc_id).update(
                {field: ArrayUnion([value])}
            )
        except Exception as e:
            self._handle_runtime_error(e)
