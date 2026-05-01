"""Admin and superadmin management routes."""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from taos.apps.api.auth_context import current_user_info, require_admin, require_superadmin
from taos.apps.api.routes import tasks
from taos.infra.firebase import get_firestore_client, init_firebase_admin
from taos.infra.logging.logger import TAOSLogger
from taos.infra.persistence.shared_store import get_shared_store

router = APIRouter(prefix="/admin", tags=["admin"])
_store = get_shared_store()
_logger = TAOSLogger(name="taos.admin")


class ChangeRoleRequest(BaseModel):
    target_uid: str
    new_role: str


class UpdateMembershipRequest(BaseModel):
    target_uid: str
    plan: str
    billing_cycle: str = "monthly"
    payment: Dict[str, Any] | None = None


class BulkMembershipRequest(BaseModel):
    target_uids: List[str]
    plan: str
    billing_cycle: str = "monthly"


def _normalize_role(raw_role: str | None) -> str:
    role = str(raw_role or "").strip().lower()
    if role in {"superadmin", "super_admin"}:
        return "superadmin"
    if role in {"admin", "premium"}:
        return "admin" if role == "admin" else "user"
    return "user"


def _normalize_plan(plan: str | None) -> str:
    p = str(plan or "free").strip().lower()
    if p in {"starter", "plus", "pro", "business", "free"}:
        return p
    if p in {"premium", "student"}:
        return "plus"
    return "free"


def _monthly_allowance_for_plan(plan: str) -> int:
    if plan == "starter":
        return 1500
    if plan == "plus":
        return 5000
    if plan == "pro":
        return 12000
    if plan == "business":
        return 50000
    return 50


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    return str(value or "")


async def _write_audit(
    *,
    actor_uid: str,
    action: str,
    target_uid: str = "",
    details: Dict[str, Any] | None = None,
) -> None:
    now = time.time()
    entry = {
        "id": f"audit_{int(now * 1000)}",
        "action": action,
        "by": actor_uid,
        "target": target_uid,
        "details": dict(details or {}),
        "timestamp": now,
        "time": now,
    }
    await _store.set("audit_logs", entry["id"], entry, user_id=actor_uid)

    db = get_firestore_client()
    if db is None:
        return
    try:
        db.collection("auditLogs").add(
            {
                "action": action,
                "by": actor_uid,
                "target": target_uid,
                "details": dict(details or {}),
                "timestamp": datetime.now(timezone.utc),
                "time": now,
            }
        )
    except Exception as exc:
        _logger.warning("admin.audit_firestore_write_failed", error=str(exc))


def _legacy_user_doc(uid: str) -> Dict[str, Any]:
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
        _logger.warning("admin.legacy_user_lookup_failed", uid=uid, error=str(exc))
        return {}


async def _effective_user_profile(uid: str) -> Dict[str, Any]:
    profile = await _store.get("profiles", "me", user_id=uid) or {}
    legacy = _legacy_user_doc(uid)
    if not profile and legacy:
        profile = {
            "uid": uid,
            "email": legacy.get("email", ""),
            "displayName": legacy.get("displayName", ""),
            "uniqueUserId": legacy.get("uniqueUserId", ""),
            "role": _normalize_role(legacy.get("role")),
            "membership": dict(legacy.get("membership") or {}),
            "createdAt": legacy.get("createdAt") or time.time(),
            "updatedAt": time.time(),
        }
    return profile


@router.get("/whoami", summary="Admin identity check")
async def admin_whoami(raw_request: Request) -> Dict[str, Any]:
    user = require_admin(raw_request)
    return {
        "ok": True,
        "uid": user.get("uid"),
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role"),
        "admin": bool(user.get("admin")),
        "superadmin": bool(user.get("superadmin")),
    }


@router.get("/super/ping", summary="Superadmin gate check")
async def superadmin_ping(raw_request: Request) -> Dict[str, Any]:
    user = require_superadmin(raw_request)
    return {
        "ok": True,
        "message": "superadmin access granted",
        "uid": user.get("uid"),
    }


@router.get("/claims", summary="Raw claim debug")
async def admin_claims(raw_request: Request) -> Dict[str, Any]:
    user = require_admin(raw_request)
    current = current_user_info(raw_request)
    return {
        "ok": True,
        "user": {
            "uid": current.get("uid"),
            "role": current.get("role"),
            "admin": bool(current.get("admin")),
            "superadmin": bool(current.get("superadmin")),
        },
        "claims": user.get("claims", {}),
    }


@router.get("/scheduler/status", summary="Global scheduler status (superadmin)")
async def scheduler_status(raw_request: Request) -> Dict[str, Any]:
    _ = require_superadmin(raw_request)
    return {"ok": True, "scheduler": tasks.scheduler_stats()}


@router.post("/scheduler/pause", summary="Pause global scheduler (superadmin)")
async def scheduler_pause(raw_request: Request) -> Dict[str, Any]:
    user = require_superadmin(raw_request)
    before = tasks.scheduler_stats()
    await tasks.stop_scheduler()
    after = tasks.scheduler_stats()
    return {
        "ok": True,
        "action": "paused",
        "by_uid": user.get("uid"),
        "before": before,
        "after": after,
    }


@router.post("/scheduler/resume", summary="Resume global scheduler (superadmin)")
async def scheduler_resume(raw_request: Request) -> Dict[str, Any]:
    user = require_superadmin(raw_request)
    before = tasks.scheduler_stats()
    await tasks.start_scheduler()
    after = tasks.scheduler_stats()
    return {
        "ok": True,
        "action": "resumed",
        "by_uid": user.get("uid"),
        "before": before,
        "after": after,
    }


@router.post("/change-role")
async def change_role(payload: ChangeRoleRequest, raw_request: Request) -> Dict[str, Any]:
    actor = require_superadmin(raw_request)
    target_uid = str(payload.target_uid or "").strip()
    if not target_uid:
        raise HTTPException(status_code=400, detail="target_uid is required")
    new_role = _normalize_role(payload.new_role)

    db = get_firestore_client()
    if db is not None:
        doc_ref = db.collection("users").document(target_uid)
        snap = doc_ref.get()
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Target user not found")
        doc_ref.set({"role": new_role, "updatedAt": datetime.now(timezone.utc)}, merge=True)

        try:
            init_firebase_admin()
            from firebase_admin import auth as firebase_auth

            user_record = firebase_auth.get_user(target_uid)
            claims = dict(user_record.custom_claims or {})
            claims.update(
                {
                    "role": new_role,
                    "admin": new_role in {"admin", "superadmin"},
                    "superadmin": new_role == "superadmin",
                }
            )
            firebase_auth.set_custom_user_claims(target_uid, claims)
        except Exception as exc:
            _logger.warning("admin.change_role_claims_update_failed", uid=target_uid, error=str(exc))

    profile = await _store.get("profiles", "me", user_id=target_uid) or {}
    profile["uid"] = target_uid
    profile["role"] = new_role
    profile["updatedAt"] = time.time()
    await _store.set("profiles", "me", profile, user_id=target_uid)

    await _write_audit(
        actor_uid=str(actor.get("uid") or ""),
        action="ROLE_CHANGED",
        target_uid=target_uid,
        details={"role": new_role},
    )
    return {"success": True, "message": "Role updated", "target_uid": target_uid, "role": new_role}


@router.post("/membership/update")
async def update_membership(payload: UpdateMembershipRequest, raw_request: Request) -> Dict[str, Any]:
    actor = require_superadmin(raw_request)
    target_uid = str(payload.target_uid or "").strip()
    if not target_uid:
        raise HTTPException(status_code=400, detail="target_uid is required")
    plan = _normalize_plan(payload.plan)
    billing_cycle = str(payload.billing_cycle or "monthly").strip().lower()
    if billing_cycle not in {"monthly", "yearly"}:
        raise HTTPException(status_code=400, detail="Invalid billing cycle")

    now_dt = datetime.now(timezone.utc)
    expiry_dt = None if plan == "free" else now_dt + (timedelta(days=365) if billing_cycle == "yearly" else timedelta(days=30))
    payment_status = "free" if plan == "free" else "paid"

    db = get_firestore_client()
    if db is not None:
        db.collection("users").document(target_uid).set(
            {
                "membership": {
                    "plan": plan,
                    "planName": plan.capitalize(),
                    "status": "active",
                    "billingCycle": billing_cycle,
                    "paymentStatus": payment_status,
                    "startDate": now_dt.isoformat(),
                    "expiryDate": expiry_dt.isoformat() if expiry_dt else None,
                    "updatedAt": now_dt,
                },
                "updatedAt": now_dt,
            },
            merge=True,
        )

    profile = await _store.get("profiles", "me", user_id=target_uid) or {}
    allowance = _monthly_allowance_for_plan(plan)
    profile["uid"] = target_uid
    profile["membership"] = {
        **dict(profile.get("membership") or {}),
        "plan": plan,
        "planName": plan.capitalize(),
        "status": "active",
        "billingCycle": billing_cycle,
        "paymentStatus": payment_status,
        "startDate": now_dt.isoformat(),
        "expiryDate": expiry_dt.isoformat() if expiry_dt else None,
        "updatedAt": now_dt.isoformat(),
    }
    profile["monthly_credit_allowance"] = allowance
    profile["monthly_credits_left"] = allowance if plan != "free" else 0
    profile["credits_balance"] = allowance if plan != "free" else 0
    profile["updatedAt"] = time.time()
    await _store.set("profiles", "me", profile, user_id=target_uid)

    await _write_audit(
        actor_uid=str(actor.get("uid") or ""),
        action="MEMBERSHIP_CHANGED",
        target_uid=target_uid,
        details={"plan": plan, "billingCycle": billing_cycle},
    )
    return {"success": True, "message": "Membership updated", "target_uid": target_uid, "plan": plan}


@router.post("/membership/bulk-update")
async def bulk_update_membership(payload: BulkMembershipRequest, raw_request: Request) -> Dict[str, Any]:
    actor = require_superadmin(raw_request)
    plan = _normalize_plan(payload.plan)
    billing_cycle = str(payload.billing_cycle or "monthly").strip().lower()
    if billing_cycle not in {"monthly", "yearly"}:
        raise HTTPException(status_code=400, detail="Invalid billing cycle")

    results: List[Dict[str, Any]] = []
    success = 0
    failed = 0
    for raw_uid in payload.target_uids:
        target_uid = str(raw_uid or "").strip()
        if not target_uid:
            continue
        try:
            await update_membership(
                UpdateMembershipRequest(target_uid=target_uid, plan=plan, billing_cycle=billing_cycle),
                raw_request,
            )
            success += 1
            results.append({"uid": target_uid, "ok": True})
        except Exception as exc:
            failed += 1
            results.append({"uid": target_uid, "ok": False, "error": str(exc)})

    await _write_audit(
        actor_uid=str(actor.get("uid") or ""),
        action="BULK_MEMBERSHIP_CHANGED",
        details={"plan": plan, "billingCycle": billing_cycle, "success": success, "failed": failed},
    )
    return {
        "success": True,
        "summary": {"requested": len(payload.target_uids), "success": success, "failed": failed},
        "results": results[-200:],
    }


@router.get("/audit-logs")
async def get_audit_logs(
    raw_request: Request,
    limit: int = Query(default=50, ge=5, le=500),
    action: str = Query(default=""),
    actor_uid: str = Query(default=""),
    target_uid: str = Query(default=""),
) -> Dict[str, Any]:
    admin_user = require_admin(raw_request)
    rows: List[Dict[str, Any]] = []
    db = get_firestore_client()
    if db is not None:
        try:
            docs = db.collection("auditLogs").order_by("timestamp", direction="DESCENDING").limit(limit).stream()
            for doc in docs:
                data = doc.to_dict() or {}
                rows.append(
                    {
                        "id": doc.id,
                        "action": str(data.get("action") or ""),
                        "by": str(data.get("by") or ""),
                        "target": str(data.get("target") or ""),
                        "details": data.get("details") if isinstance(data.get("details"), dict) else {},
                        "timestamp": _to_iso(data.get("timestamp")) or str(data.get("time") or ""),
                    }
                )
        except Exception as exc:
            _logger.warning("admin.audit_logs_firestore_failed", error=str(exc))
            rows = []
    if not rows:
        rows = await _store.list("audit_logs", user_id=str(admin_user.get("uid") or "default"), limit=limit)

    if action:
        rows = [r for r in rows if str(r.get("action") or "").lower() == str(action).lower()]
    if actor_uid:
        rows = [r for r in rows if str(r.get("by") or "") == str(actor_uid)]
    if target_uid:
        rows = [r for r in rows if str(r.get("target") or "") == str(target_uid)]
    return {"success": True, "items": rows, "count": len(rows)}


@router.get("/export/users", response_model=None)
async def export_users(
    raw_request: Request,
    format: str = Query(default="json"),
) -> Any:
    _ = require_superadmin(raw_request)
    fmt = str(format or "json").strip().lower()
    if fmt not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="unsupported format")

    rows: List[Dict[str, Any]] = []
    db = get_firestore_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Global user export requires Firestore database")

    try:
        docs = db.collection("users").stream()
        for doc in docs:
            data = doc.to_dict() or {}
            membership = data.get("membership") if isinstance(data.get("membership"), dict) else {}
            rows.append(
                {
                    "uid": doc.id,
                    "email": str(data.get("email") or ""),
                    "displayName": str(data.get("displayName") or ""),
                    "role": str(data.get("role") or "user"),
                    "plan": str(membership.get("plan") or "free"),
                    "billingCycle": str(membership.get("billingCycle") or ""),
                    "status": str(membership.get("status") or ""),
                    "expiryDate": str(membership.get("expiryDate") or ""),
                    "createdAt": _to_iso(data.get("createdAt")),
                }
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to export users: {exc}") from exc

    if fmt == "json":
        return {"success": True, "count": len(rows), "items": rows}

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["uid", "email", "display_name", "role", "plan", "billing_cycle", "status", "expiry_date", "created_at"])
    for row in rows:
        writer.writerow(
            [
                row.get("uid", ""),
                row.get("email", ""),
                row.get("displayName", ""),
                row.get("role", ""),
                row.get("plan", ""),
                row.get("billingCycle", ""),
                row.get("status", ""),
                row.get("expiryDate", ""),
                row.get("createdAt", ""),
            ]
        )
    return Response(content=out.getvalue().encode("utf-8"), media_type="text/csv")


@router.get("/users/{target_uid}")
async def get_user(target_uid: str, raw_request: Request) -> Dict[str, Any]:
    _ = require_admin(raw_request)
    uid = str(target_uid or "").strip()
    if not uid:
        raise HTTPException(status_code=400, detail="target uid required")
    profile = await _effective_user_profile(uid)
    if not profile:
        raise HTTPException(status_code=404, detail="Target user not found")
    return {"success": True, "user": profile}
