"""User profile, membership and credit routes."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict

from fastapi import APIRouter, Request

from taos.apps.api.auth_context import require_uid
from taos.infra.firebase import get_firestore_client
from taos.infra.logging.logger import TAOSLogger
from taos.infra.persistence.shared_store import get_shared_store

router = APIRouter(prefix="/users", tags=["users"])
_store = get_shared_store()
_logger = TAOSLogger(name="taos.users")
_id_lock = threading.Lock()


def _normalize_role(raw_role: str | None) -> str:
    role = (raw_role or "").strip().lower()
    if not role:
        return ""
    if role in {"super_admin", "superadmin"}:
        return "superadmin"
    if role == "admin":
        return "admin"
    if role == "user":
        return "user"
    return ""


def _stable_unique_id(uid: str) -> str:
    h = 0
    for ch in uid:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return f"RA{((h % 999) + 1):03d}"


def _counter_unique_id_or_empty() -> str:
    db = get_firestore_client()
    if db is None:
        return ""
    try:
        from firebase_admin import firestore

        counter_ref = db.collection("counters").document("userIds")

        @firestore.transactional
        def _increment(transaction: Any) -> int:
            snap = counter_ref.get(transaction=transaction)
            current = 0
            if snap.exists:
                data = snap.to_dict() or {}
                try:
                    current = int(data.get("currentId", 0) or 0)
                except Exception:
                    current = 0
            new_id = current + 1
            transaction.set(
                counter_ref,
                {
                    "currentId": new_id,
                    "lastUpdated": datetime.now(timezone.utc),
                },
                merge=True,
            )
            return new_id

        with _id_lock:
            next_id = _increment(db.transaction())
        return f"RA{next_id:03d}"
    except Exception as exc:
        _logger.warning("users.uid_counter_failed", error=str(exc))
        return ""


def _resolve_unique_user_id(existing: Dict[str, Any], legacy: Dict[str, Any], uid: str) -> str:
    from_legacy = str(
        legacy.get("uniqueUserId")
        or legacy.get("publicId")
        or legacy.get("legacyUniqueUserId")
        or ""
    ).strip()
    if from_legacy:
        return from_legacy

    from_existing = str(existing.get("uniqueUserId") or "").strip()
    if from_existing:
        return from_existing

    generated = _counter_unique_id_or_empty()
    if generated:
        return generated
    return _stable_unique_id(uid)


def _monthly_allowance_for_plan(plan: str) -> int:
    p = str(plan or "free").strip().lower()
    if p == "starter":
        return 1500
    if p == "plus":
        return 5000
    if p == "pro":
        return 12000
    if p == "business":
        return 50000
    return 50


def _default_membership() -> Dict[str, Any]:
    return {
        "plan": "free",
        "planName": "Free",
        "status": "active",
        "billingCycle": "monthly",
        "paymentStatus": "free",
    }


def _to_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _legacy_profile_for_uid(uid: str) -> Dict[str, Any]:
    """
    Read legacy top-level Firestore profile: users/{uid}.

    Returns empty dict when Firebase/Firestore is unavailable or no legacy record exists.
    """
    db = get_firestore_client()
    if db is None:
        return {}
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            return {}
        data = doc.to_dict() or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        _logger.warning("users.legacy_lookup_failed", uid=uid, error=str(exc))
        return {}


def _merge_legacy_profile(existing: Dict[str, Any], legacy: Dict[str, Any], uid: str) -> Dict[str, Any]:
    if not legacy:
        return existing

    merged = dict(existing or {})
    legacy_membership = dict(legacy.get("membership") or {})
    merged_membership = dict(merged.get("membership") or {})
    legacy_settings = dict(legacy.get("settings") or {})
    merged_settings = dict(merged.get("settings") or {})
    merged_plan = str(
        merged_membership.get("plan")
        or legacy_membership.get("plan")
        or legacy.get("plan")
        or "free"
    ).strip().lower() or "free"

    merged["uid"] = uid
    merged["email"] = merged.get("email") or legacy.get("email") or ""
    merged["displayName"] = merged.get("displayName") or legacy.get("displayName") or legacy.get("name") or ""
    merged["photoURL"] = merged.get("photoURL") or legacy.get("photoURL") or legacy.get("avatar") or ""
    merged["uniqueUserId"] = _resolve_unique_user_id(merged, legacy, uid)
    merged_role = _normalize_role(str(merged.get("role") or ""))
    legacy_role = _normalize_role(str(legacy.get("role") or ""))
    if merged_role in {"admin", "superadmin"}:
        role = merged_role
    elif legacy_role in {"admin", "superadmin"}:
        role = legacy_role
    else:
        role = merged_role or legacy_role or "user"
    merged["role"] = role
    merged["createdAt"] = merged.get("createdAt") or _to_float_or_none(legacy.get("createdAt")) or time.time()

    merged["membership"] = {
        **_default_membership(),
        **legacy_membership,
        **merged_membership,
        "plan": merged_plan,
        "planName": str(
            merged_membership.get("planName")
            or legacy_membership.get("planName")
            or merged_plan.capitalize()
        ),
    }
    merged["settings"] = {
        "notifications": bool(merged_settings.get("notifications", legacy_settings.get("notifications", False))),
        "emailUpdates": bool(merged_settings.get("emailUpdates", legacy_settings.get("emailUpdates", False))),
        "dataRetention": bool(merged_settings.get("dataRetention", legacy_settings.get("dataRetention", True))),
        "personalization": {
            **dict(legacy_settings.get("personalization") or {}),
            **dict(merged_settings.get("personalization") or {}),
        },
    }

    daily_allowance = _safe_int(
        merged.get("daily_credit_allowance")
        or legacy.get("daily_credit_allowance")
        or legacy.get("dailyCreditAllowance")
        or 50,
        50,
    )
    monthly_allowance = _safe_int(
        merged.get("monthly_credit_allowance")
        or legacy.get("monthly_credit_allowance")
        or legacy.get("monthlyCreditAllowance")
        or _monthly_allowance_for_plan(merged_plan),
        _monthly_allowance_for_plan(merged_plan),
    )
    merged["daily_credit_allowance"] = daily_allowance
    merged["monthly_credit_allowance"] = monthly_allowance
    merged["daily_credits_left"] = _safe_int(
        merged.get("daily_credits_left", legacy.get("daily_credits_left", daily_allowance)),
        daily_allowance,
    )
    merged["monthly_credits_left"] = _safe_int(
        merged.get("monthly_credits_left", legacy.get("monthly_credits_left", legacy.get("credits_balance", monthly_allowance))),
        monthly_allowance,
    )
    merged["credits_balance"] = _safe_int(merged.get("credits_balance", legacy.get("credits_balance", monthly_allowance)), monthly_allowance)
    merged["last_daily_reset_at"] = str(merged.get("last_daily_reset_at") or legacy.get("last_daily_reset_at") or "")
    merged["last_monthly_reset_at"] = str(merged.get("last_monthly_reset_at") or legacy.get("last_monthly_reset_at") or "")
    return merged


def _effective_credit_snapshot(user_data: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(user_data or {})
    membership = dict(data.get("membership") or {})
    plan = str(membership.get("plan") or "free").strip().lower() or "free"
    daily_allowance = int(data.get("daily_credit_allowance", 50) or 50)
    monthly_allowance = int(
        data.get("monthly_credit_allowance", _monthly_allowance_for_plan(plan)) or _monthly_allowance_for_plan(plan)
    )
    if plan == "free":
        available = data.get("daily_credits_left", daily_allowance)
        reset_at = str(data.get("last_daily_reset_at") or "")
    else:
        available = data.get("monthly_credits_left", data.get("credits_balance", monthly_allowance))
        reset_at = str(data.get("last_monthly_reset_at") or "")
    try:
        available_int = max(0, int(available))
    except Exception:
        available_int = daily_allowance if plan == "free" else monthly_allowance
    return {
        "plan": plan,
        "available_credits": available_int,
        "daily_credit_allowance": max(0, int(daily_allowance)),
        "monthly_credit_allowance": max(0, int(monthly_allowance)),
        "last_reset_at": reset_at,
    }


async def _build_or_update_profile(raw_request: Request, uid: str) -> Dict[str, Any]:
    now = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    existing = await _store.get("profiles", "me", user_id=uid) or {}
    existing = _merge_legacy_profile(existing, _legacy_profile_for_uid(uid), uid)
    claims = getattr(raw_request.state, "auth_claims", {}) or {}
    role = _normalize_role(getattr(raw_request.state, "auth_role", None))

    existing_membership = existing.get("membership") or _default_membership()
    existing_settings = dict(existing.get("settings") or {})
    plan = str(existing_membership.get("plan") or "free").strip().lower() or "free"
    daily_allowance = int(existing.get("daily_credit_allowance", 50) or 50)
    monthly_allowance = int(
        existing.get("monthly_credit_allowance", _monthly_allowance_for_plan(plan)) or _monthly_allowance_for_plan(plan)
    )
    monthly_left_default = monthly_allowance if plan != "free" else 0

    profile: Dict[str, Any] = {
        "uid": uid,
        "email": claims.get("email") or existing.get("email") or "",
        "displayName": claims.get("name") or existing.get("displayName") or "",
        "photoURL": claims.get("picture") or existing.get("photoURL") or "",
        "uniqueUserId": _resolve_unique_user_id(existing, {}, uid),
        "role": role or existing.get("role") or "user",
        "createdAt": existing.get("createdAt") or now,
        "updatedAt": now,
        "membership": {
            **_default_membership(),
            **(existing_membership or {}),
            "plan": plan,
            "planName": str((existing_membership or {}).get("planName") or plan.capitalize()),
            "updatedAt": now_iso,
        },
        "settings": {
            "notifications": bool(existing_settings.get("notifications", False)),
            "emailUpdates": bool(existing_settings.get("emailUpdates", False)),
            "dataRetention": bool(existing_settings.get("dataRetention", True)),
            "personalization": dict(existing_settings.get("personalization") or {}),
        },
        "daily_credits_left": int(existing.get("daily_credits_left", daily_allowance) or daily_allowance),
        "daily_credit_allowance": daily_allowance,
        "last_daily_reset_at": str(existing.get("last_daily_reset_at") or now_iso),
        "monthly_credits_left": int(existing.get("monthly_credits_left", monthly_left_default) or monthly_left_default),
        "monthly_credit_allowance": monthly_allowance,
        "credits_balance": int(existing.get("credits_balance", monthly_left_default) or monthly_left_default),
        "last_monthly_reset_at": str(existing.get("last_monthly_reset_at") or now_iso),
    }
    await _store.set("profiles", "me", profile, user_id=uid)
    return profile


@router.post("/init")
async def init_user(raw_request: Request) -> Dict[str, Any]:
    uid = require_uid(raw_request)
    profile = await _build_or_update_profile(raw_request, uid)
    return {
        "success": True,
        "uid": profile["uid"],
        "uniqueUserId": profile["uniqueUserId"],
        "role": profile["role"],
        "user": profile,
        "credits": _effective_credit_snapshot(profile),
    }


@router.get("/me")
async def get_user_profile(raw_request: Request) -> Dict[str, Any]:
    uid = require_uid(raw_request)
    profile = await _store.get("profiles", "me", user_id=uid)
    if not profile:
        profile = await _build_or_update_profile(raw_request, uid)
    else:
        legacy = _legacy_profile_for_uid(uid)
        if legacy:
            merged = _merge_legacy_profile(profile, legacy, uid)
            if merged != profile:
                profile = merged
                profile["updatedAt"] = time.time()
                await _store.set("profiles", "me", profile, user_id=uid)
    return {"success": True, "user": profile, "credits": _effective_credit_snapshot(profile)}


@router.get("/credits")
async def get_user_credits(raw_request: Request) -> Dict[str, Any]:
    uid = require_uid(raw_request)
    profile = await _store.get("profiles", "me", user_id=uid)
    if not profile:
        profile = await _build_or_update_profile(raw_request, uid)
    return {"success": True, "credits": _effective_credit_snapshot(profile)}


@router.post("/membership/downgrade")
async def downgrade_membership(raw_request: Request) -> Dict[str, Any]:
    uid = require_uid(raw_request)
    profile = await _store.get("profiles", "me", user_id=uid)
    if not profile:
        profile = await _build_or_update_profile(raw_request, uid)
    now = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    profile["membership"] = {
        **_default_membership(),
        "plan": "free",
        "planName": "Free",
        "status": "active",
        "updatedAt": now_iso,
    }
    profile["monthly_credits_left"] = 0
    profile["credits_balance"] = 0
    profile["monthly_credit_allowance"] = _monthly_allowance_for_plan("free")
    profile["updatedAt"] = now
    await _store.set("profiles", "me", profile, user_id=uid)

    audit_id = f"downgrade_{int(now)}"
    await _store.set(
        "audit_logs",
        audit_id,
        {
            "id": audit_id,
            "action": "MEMBERSHIP_CHANGED",
            "from": "paid",
            "to": "free",
            "by": uid,
            "target": uid,
            "timestamp": now,
            "time": now,
        },
        user_id=uid,
    )
    return {"success": True, "message": "Downgraded to Free plan", "user": profile}
