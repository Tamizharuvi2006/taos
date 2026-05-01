"""Firebase token verification utilities."""

from __future__ import annotations

from typing import Any, Dict, Optional

from taos.config.settings import get_settings
from taos.infra.firebase import init_firebase_admin


class FirebaseAuthError(Exception):
    pass


class FirebaseAuthVerifier:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._initialized = False
        self._auth = None
        self._init_once()

    def _init_once(self) -> None:
        if self._initialized:
            return
        try:
            import firebase_admin
            from firebase_admin import auth

            init_firebase_admin()

            self._auth = auth
            self._initialized = True
        except ImportError as e:
            raise FirebaseAuthError("firebase-admin not installed") from e
        except Exception as e:
            raise FirebaseAuthError(str(e)) from e

    def verify(self, token: str) -> Dict[str, Any]:
        if not token or not token.strip():
            raise FirebaseAuthError("Missing token")
        if not self._auth:
            raise FirebaseAuthError("Firebase auth not initialized")
        try:
            decoded = self._auth.verify_id_token(token.strip())
            if "uid" not in decoded:
                raise FirebaseAuthError("Token missing uid")
            return decoded
        except Exception as e:
            raise FirebaseAuthError("Invalid token") from e


def normalize_role(role_value: Optional[str]) -> str:
    if not role_value:
        return ""
    role = str(role_value).strip().lower()
    if role == "super_admin":
        return "superadmin"
    return role


def get_claim_role(claims: Dict[str, Any]) -> str:
    role = normalize_role(claims.get("role"))
    if role:
        return role
    if claims.get("superadmin") is True:
        return "superadmin"
    if claims.get("admin") is True:
        return "admin"
    return ""


def is_admin_claims(claims: Dict[str, Any]) -> bool:
    return get_claim_role(claims) in {"admin", "superadmin"}


def is_superadmin_claims(claims: Dict[str, Any]) -> bool:
    return get_claim_role(claims) == "superadmin"


def build_user_info(decoded_claims: Dict[str, Any]) -> Dict[str, Any]:
    role = get_claim_role(decoded_claims)
    return {
        "uid": str(decoded_claims.get("uid") or ""),
        "email": decoded_claims.get("email"),
        "name": decoded_claims.get("name"),
        "role": role,
        "admin": role in {"admin", "superadmin"},
        "superadmin": role == "superadmin",
        "claims": decoded_claims,
    }


_VERIFIER: Optional[FirebaseAuthVerifier] = None


def get_firebase_verifier() -> FirebaseAuthVerifier:
    global _VERIFIER
    if _VERIFIER is None:
        _VERIFIER = FirebaseAuthVerifier()
    return _VERIFIER
