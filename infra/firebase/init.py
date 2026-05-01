"""Firebase Admin SDK bootstrap utilities."""

from __future__ import annotations

from typing import Any, Optional

from taos.config.settings import get_settings

_APP: Optional[Any] = None
_DB: Optional[Any] = None


class FirebaseInitError(Exception):
    pass


def init_firebase_admin() -> Any:
    """
    Initialize Firebase Admin SDK once and return the app handle.

    Required env:
    - FIREBASE_PROJECT_ID
    - FIREBASE_PRIVATE_KEY
    - FIREBASE_CLIENT_EMAIL
    """
    global _APP
    if _APP is not None:
        return _APP

    settings = get_settings()
    try:
        import firebase_admin
        from firebase_admin import credentials

        try:
            _APP = firebase_admin.get_app()
            return _APP
        except ValueError:
            pass

        project_id = settings.firebase_project_id
        private_key = settings.firebase_private_key.replace("\\n", "\n")
        client_email = settings.firebase_client_email
        if not (project_id and private_key and client_email):
            raise FirebaseInitError("Missing Firebase Admin environment variables")

        cred_dict = {
            "type": "service_account",
            "project_id": project_id,
            "private_key_id": settings.firebase_private_key_id,
            "private_key": private_key,
            "client_email": client_email,
            "client_id": settings.firebase_client_id,
            "auth_uri": settings.firebase_auth_uri,
            "token_uri": settings.firebase_token_uri,
            "auth_provider_x509_cert_url": settings.firebase_auth_provider_x509_cert_url,
            "client_x509_cert_url": settings.firebase_client_x509_cert_url,
            "universe_domain": settings.firebase_universe_domain,
        }
        cred_dict = {k: v for k, v in cred_dict.items() if v}
        options = {}
        storage_bucket = (settings.firebase_storage_bucket or "").strip()
        if not storage_bucket and project_id:
            storage_bucket = f"{project_id}.appspot.com"
        if storage_bucket:
            options["storageBucket"] = storage_bucket

        _APP = firebase_admin.initialize_app(
            credentials.Certificate(cred_dict),
            options=options or None,
        )
        return _APP
    except ImportError as e:
        raise FirebaseInitError("firebase-admin not installed") from e
    except Exception as e:
        raise FirebaseInitError(str(e)) from e


def get_firestore_client() -> Optional[Any]:
    """
    Return a firebase-admin Firestore client when available.

    Returns None when Firebase is not configured or Firestore is unavailable.
    """
    global _DB
    if _DB is not None:
        return _DB
    try:
        app = init_firebase_admin()
        from firebase_admin import firestore
        from google.cloud import firestore as google_firestore

        settings = get_settings()
        project_id = (settings.firebase_project_id or "").strip()
        requested_db = (settings.firebase_database_id or "").strip()
        db_candidates = []
        if requested_db and requested_db != "(default)":
            db_candidates = [requested_db]
        else:
            if requested_db:
                db_candidates.append(requested_db)
            if "(default)" not in db_candidates:
                db_candidates.append("(default)")
            if "relyceinfotech" not in db_candidates:
                db_candidates.append("relyceinfotech")

        last_error: Exception | None = None
        for database_id in db_candidates:
            try:
                if database_id == "(default)":
                    client = firestore.client()
                else:
                    cred = None
                    try:
                        cred = app.credential.get_credential()
                    except Exception:
                        cred = None
                    if cred is not None:
                        client = google_firestore.Client(
                            credentials=cred,
                            project=project_id or None,
                            database=database_id,
                        )
                    else:
                        client = google_firestore.Client(project=project_id or None, database=database_id)
                # Probe to ensure database exists and is reachable.
                _ = list(client.collection("taos_health").document("probe").collection("ping").limit(1).stream())
                _DB = client
                return _DB
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        return None
    except Exception:
        return None
