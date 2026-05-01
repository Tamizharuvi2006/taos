"""Auth integrations."""

from taos.infra.auth.firebase_auth import (
    FirebaseAuthError,
    FirebaseAuthVerifier,
    build_user_info,
    get_claim_role,
    get_firebase_verifier,
    is_admin_claims,
    is_superadmin_claims,
    normalize_role,
)

__all__ = [
    "FirebaseAuthError",
    "FirebaseAuthVerifier",
    "build_user_info",
    "get_claim_role",
    "get_firebase_verifier",
    "is_admin_claims",
    "is_superadmin_claims",
    "normalize_role",
]
