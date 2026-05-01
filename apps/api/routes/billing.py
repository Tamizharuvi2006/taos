"""Membership pricing and payment routes (Razorpay-compatible contract)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Request

from taos.apps.api.auth_context import current_user_info, require_admin, require_uid
from taos.config.settings import get_settings
from taos.core.notifications.models import NotificationChannel, NotificationConfig, NotificationEvent
from taos.core.notifications.notifier import NotificationManager
from taos.infra.persistence.shared_store import get_shared_store

router = APIRouter(prefix="/payment", tags=["payment"])
_store = get_shared_store()
_settings = get_settings()
_notifier = NotificationManager(store=_store)

try:  # optional dependency; backend keeps running even when package is missing
    import razorpay  # type: ignore
except Exception:  # pragma: no cover
    razorpay = None


DEFAULT_PRICING: Dict[str, Dict[str, int]] = {
    "starter": {"monthly": 199, "yearly": 1999},
    "plus": {"monthly": 999, "yearly": 9999},
    "pro": {"monthly": 1999, "yearly": 19999},
    "business": {"monthly": 2499, "yearly": 24999},
}

VALID_BILLING_CYCLES = {"monthly", "yearly"}

DEFAULT_COUPONS: Dict[str, Dict[str, Any]] = {
    "WELCOME10": {"type": "percent", "value": 10, "active": True},
    "RELYCE200": {"type": "flat", "value": 200, "active": True},
}


def _razorpay_client():
    key_id = (_settings.razorpay_key_id or "").strip()
    key_secret = (_settings.razorpay_key_secret or "").strip()
    if not key_id or not key_secret:
        return None
    if razorpay is None:
        return None
    return razorpay.Client(auth=(key_id, key_secret))


def _load_pricing() -> Dict[str, Dict[str, int]]:
    # TODO: wire to dynamic admin config store if needed.
    return DEFAULT_PRICING


def _get_plan_amount(pricing: Dict[str, Dict[str, int]], plan_id: str, billing_cycle: str) -> int | None:
    plan = pricing.get(plan_id)
    if not isinstance(plan, dict):
        return None
    return plan.get("yearly") if billing_cycle == "yearly" else plan.get("monthly")


def _normalize_coupon(code: str | None) -> str:
    return str(code or "").strip().upper()


def _compute_discount(amount_rupees: int, coupon_code: str | None) -> Dict[str, Any]:
    code = _normalize_coupon(coupon_code)
    if not code:
        return {"coupon_code": "", "discount_amount": 0, "final_amount": int(amount_rupees), "applied": False}

    coupon = DEFAULT_COUPONS.get(code)
    if not coupon or not coupon.get("active"):
        raise HTTPException(status_code=400, detail="Invalid or inactive coupon code")

    ctype = str(coupon.get("type") or "").lower()
    value = int(coupon.get("value") or 0)
    if value <= 0:
        raise HTTPException(status_code=400, detail="Invalid coupon configuration")

    if ctype == "percent":
        discount = int((int(amount_rupees) * value) / 100)
    elif ctype == "flat":
        discount = value
    else:
        raise HTTPException(status_code=400, detail="Unsupported coupon configuration")

    discount = max(0, min(int(amount_rupees), int(discount)))
    final_amount = int(amount_rupees) - discount
    return {
        "coupon_code": code,
        "discount_amount": discount,
        "final_amount": final_amount,
        "applied": discount > 0,
    }


def _normalize_plan_id(plan_id: str | None) -> str:
    return str(plan_id or "").strip().lower()


def _ensure_user_profile(payload: Dict[str, Any], uid: str) -> Dict[str, Any]:
    profile = dict(payload or {})
    profile.setdefault("uid", uid)
    profile.setdefault("membership", {"plan": "free", "planName": "Free", "status": "active", "billingCycle": "monthly"})
    profile.setdefault("monthly_credit_allowance", 50)
    profile.setdefault("monthly_credits_left", 0)
    profile.setdefault("credits_balance", 0)
    profile.setdefault("daily_credit_allowance", 50)
    profile.setdefault("daily_credits_left", 50)
    return profile


def _plan_allowance(plan_id: str) -> int:
    if plan_id == "starter":
        return 1500
    if plan_id == "plus":
        return 5000
    if plan_id == "pro":
        return 12000
    if plan_id == "business":
        return 50000
    return 50


def _verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    secret = (_settings.razorpay_webhook_secret or "").strip()
    sig = str(signature or "").strip()
    if not secret or not sig:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig)


def _extract_membership_context(payload: Dict[str, Any]) -> Dict[str, str]:
    item = dict(payload.get("payload") or {})
    payment_entity = dict(((item.get("payment") or {}).get("entity") or {}))
    order_entity = dict(((item.get("order") or {}).get("entity") or {}))
    notes = dict(order_entity.get("notes") or payment_entity.get("notes") or {})
    return {
        "uid": str(notes.get("user_id") or notes.get("uid") or "").strip(),
        "plan_id": _normalize_plan_id(notes.get("plan_id") or notes.get("plan")),
        "billing_cycle": str(notes.get("billing_cycle") or notes.get("billingCycle") or "monthly").strip().lower(),
        "payment_id": str(payment_entity.get("id") or "").strip(),
        "order_id": str(order_entity.get("id") or payment_entity.get("order_id") or "").strip(),
        "currency": str(payment_entity.get("currency") or "INR").strip() or "INR",
        "amount_paise": str(payment_entity.get("amount") or order_entity.get("amount") or "").strip(),
    }


async def _apply_membership_from_webhook(event_id: str, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = _extract_membership_context(payload)
    uid = meta["uid"]
    plan_id = _normalize_plan_id(meta["plan_id"])
    billing_cycle = meta["billing_cycle"] if meta["billing_cycle"] in VALID_BILLING_CYCLES else "monthly"
    if not uid or not plan_id:
        return {"processed": False, "reason": "missing_notes_metadata"}

    existing_event = await _store.get("payment_webhook_events", event_id, user_id=uid)
    if existing_event:
        return {"processed": True, "idempotent": True, "uid": uid}

    now = time.time()
    expiry_sec = now + (365 * 24 * 3600 if billing_cycle == "yearly" else 30 * 24 * 3600)
    profile = await _store.get("profiles", "me", user_id=uid) or {}
    profile = _ensure_user_profile(profile, uid)
    allowance = _plan_allowance(plan_id)
    profile["membership"] = {
        **(profile.get("membership") or {}),
        "plan": plan_id,
        "planName": plan_id.capitalize(),
        "status": "active",
        "billingCycle": billing_cycle,
        "paymentStatus": "paid",
        "startDate": now,
        "expiryDate": expiry_sec,
        "updatedAt": now,
    }
    profile["monthly_credit_allowance"] = allowance
    profile["monthly_credits_left"] = allowance
    profile["credits_balance"] = allowance
    profile["updatedAt"] = now
    await _store.set("profiles", "me", profile, user_id=uid)

    payment_id = meta["payment_id"] or f"webhook_{event_id}"
    amount_rupees = None
    try:
        amount_rupees = int(int(meta["amount_paise"]) / 100)
    except Exception:
        amount_rupees = _get_plan_amount(_load_pricing(), plan_id, billing_cycle) or 0

    await _store.set(
        "payments",
        payment_id,
        {
            "id": payment_id,
            "paymentId": payment_id,
            "orderId": meta["order_id"],
            "userId": uid,
            "planId": plan_id,
            "billingCycle": billing_cycle,
            "amount": amount_rupees,
            "currency": meta["currency"],
            "verified": True,
            "source": "webhook",
            "event": event,
            "eventId": event_id,
            "timestamp": now,
        },
        user_id=uid,
    )
    await _store.set(
        "payment_webhook_events",
        event_id,
        {
            "id": event_id,
            "event": event,
            "uid": uid,
            "paymentId": payment_id,
            "orderId": meta["order_id"],
            "processedAt": now,
        },
        user_id=uid,
    )
    email = str(profile.get("email") or "").strip()
    if email:
        await _send_membership_upgrade_email(
            uid=uid,
            email=email,
            plan_id=plan_id,
            billing_cycle=billing_cycle,
            expiry_sec=expiry_sec,
            source="webhook",
        )
    return {"processed": True, "uid": uid, "plan": plan_id, "billing_cycle": billing_cycle}


async def _send_membership_upgrade_email(
    uid: str,
    email: str,
    plan_id: str,
    billing_cycle: str,
    expiry_sec: float,
    source: str = "verify",
) -> None:
    try:
        task = SimpleNamespace(task_id=f"membership_{source}", name="Membership Update", goal="Membership updated")
        expiry_str = time.strftime("%d %b %Y", time.localtime(expiry_sec))
        execution = SimpleNamespace(
            execution_id=f"membership_exec_{int(time.time())}",
            success=True,
            result=(
                f"Your {plan_id.capitalize()} plan is active on {billing_cycle} billing. "
                f"Access valid through {expiry_str}."
            ),
            error="",
            email_subject=f"Relyce AI · {plan_id.capitalize()} Plan Activated",
            email_html=_notifier._build_relyce_email_template(
                title="Business Plan Activated" if plan_id == "business" else f"{plan_id.capitalize()} Plan Activated",
                subtitle=f"{billing_cycle.capitalize()} billing · Active",
                body_html=(
                    f"<p>Hi,</p>"
                    f"<p>Your <strong>{plan_id.capitalize()}</strong> plan is now active.</p>"
                    f"<p><strong>Billing cycle:</strong> {billing_cycle.capitalize()}<br/>"
                    f"<strong>Valid till:</strong> {expiry_str}</p>"
                    f"<p>You can now use advanced chat workflows, reminders, and automations.</p>"
                ),
                cta_text="Open Relyce AI",
                cta_url=(_settings.site_url or "").strip() or "https://relyce.com",
                footer_note="Thank you for choosing Relyce AI.",
            ),
        )
        cfg = NotificationConfig(
            channel=NotificationChannel.EMAIL,
            target=email,
            events=[NotificationEvent.ALWAYS],
        )
        await _notifier._send_email(cfg, task, execution, NotificationEvent.ALWAYS, user_id=uid)
    except Exception:
        # Email should never block billing flow.
        pass


@router.get("/plans")
async def list_plans(raw_request: Request) -> Dict[str, Any]:
    _ = require_uid(raw_request)
    pricing = _load_pricing()
    return {"success": True, "plans": pricing}


@router.post("/coupon/validate")
async def validate_coupon(
    raw_request: Request,
    plan_id: str = Body(...),
    billing_cycle: str = Body("monthly"),
    coupon_code: str = Body(""),
) -> Dict[str, Any]:
    _ = require_uid(raw_request)
    plan_id = _normalize_plan_id(plan_id)
    billing_cycle = str(billing_cycle or "monthly").strip().lower()
    if billing_cycle not in VALID_BILLING_CYCLES:
        raise HTTPException(status_code=400, detail="Invalid billing cycle")

    pricing = _load_pricing()
    amount_rupees = _get_plan_amount(pricing, plan_id, billing_cycle)
    if amount_rupees is None:
        raise HTTPException(status_code=400, detail="Invalid plan")

    pricing_info = _compute_discount(int(amount_rupees), coupon_code)
    return {
        "success": True,
        "plan_id": plan_id,
        "billing_cycle": billing_cycle,
        "base_amount": int(amount_rupees),
        "coupon_code": pricing_info["coupon_code"],
        "discount_amount": pricing_info["discount_amount"],
        "final_amount": pricing_info["final_amount"],
        "applied": pricing_info["applied"],
    }


@router.post("/create-order")
async def create_order(
    raw_request: Request,
    plan_id: str = Body(...),
    billing_cycle: str = Body("monthly"),
    currency: str = Body("INR"),
    receipt: str | None = Body(None),
    coupon_code: str = Body(""),
) -> Dict[str, Any]:
    uid = require_uid(raw_request)
    plan_id = _normalize_plan_id(plan_id)
    billing_cycle = str(billing_cycle or "monthly").strip().lower()
    if billing_cycle not in VALID_BILLING_CYCLES:
        raise HTTPException(status_code=400, detail="Invalid billing cycle")

    pricing = _load_pricing()
    amount_rupees = _get_plan_amount(pricing, plan_id, billing_cycle)
    if not amount_rupees:
        raise HTTPException(status_code=400, detail="Invalid plan")

    client = _razorpay_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Payment provider not configured")

    coupon_info = _compute_discount(int(amount_rupees), coupon_code)

    data = {
        "amount": int(coupon_info["final_amount"]) * 100,
        "currency": currency,
        "receipt": receipt or f"taos_{uid}_{int(time.time())}",
        "notes": {
            "user_id": uid,
            "plan_id": plan_id,
            "billing_cycle": billing_cycle,
            "coupon_code": coupon_info["coupon_code"],
            "base_amount": str(int(amount_rupees)),
            "discount_amount": str(int(coupon_info["discount_amount"])),
            "final_amount": str(int(coupon_info["final_amount"])),
        },
        "payment_capture": 1,
    }

    try:
        order = client.order.create(data=data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Order creation failed: {exc}") from exc

    order_id = str(order.get("id") or f"order_{int(time.time())}")
    await _store.set(
        "payment_orders",
        order_id,
        {
            "id": order_id,
            "userId": uid,
            "planId": plan_id,
            "billingCycle": billing_cycle,
            "amount": int(coupon_info["final_amount"]),
            "currency": currency,
            "couponCode": coupon_info["coupon_code"],
            "discountAmount": int(coupon_info["discount_amount"]),
            "baseAmount": int(amount_rupees),
            "createdAt": time.time(),
        },
        user_id=uid,
    )
    return {
        "success": True,
        "order": order,
        "key_id": _settings.razorpay_key_id,
        "amount": int(coupon_info["final_amount"]),
        "base_amount": int(amount_rupees),
        "discount_amount": int(coupon_info["discount_amount"]),
        "coupon_code": coupon_info["coupon_code"],
    }


@router.post("/verify")
async def verify_payment(
    raw_request: Request,
    razorpay_order_id: str = Body(...),
    razorpay_payment_id: str = Body(...),
    razorpay_signature: str = Body(...),
) -> Dict[str, Any]:
    uid = require_uid(raw_request)
    client = _razorpay_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Payment provider not configured")

    params_dict = {
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_signature": razorpay_signature,
    }
    try:
        client.utility.verify_payment_signature(params_dict)
    except Exception:
        raise HTTPException(status_code=400, detail="Signature verification failed")

    payment_details = {}
    order_details = {}
    try:
        payment_details = client.payment.fetch(razorpay_payment_id) or {}
    except Exception:
        payment_details = {}
    try:
        order_details = client.order.fetch(razorpay_order_id) or {}
    except Exception:
        order_details = {}

    notes = (order_details.get("notes") or payment_details.get("notes") or {}) if isinstance(order_details, dict) else {}
    notes_user = str(notes.get("user_id") or "").strip()
    if notes_user and notes_user != uid:
        raise HTTPException(status_code=403, detail="Payment user mismatch")

    order_meta = await _store.get("payment_orders", razorpay_order_id, user_id=uid) or {}
    plan_id = _normalize_plan_id(notes.get("plan_id") or notes.get("plan") or order_meta.get("planId"))
    billing_cycle = str(
        notes.get("billing_cycle") or notes.get("billingCycle") or order_meta.get("billingCycle") or "monthly"
    ).lower()
    if billing_cycle not in VALID_BILLING_CYCLES:
        raise HTTPException(status_code=400, detail="Invalid billing cycle")
    if not plan_id:
        raise HTTPException(status_code=400, detail="Missing plan metadata")

    pricing = _load_pricing()
    expected_amount = _get_plan_amount(pricing, plan_id, billing_cycle)
    if not expected_amount:
        raise HTTPException(status_code=400, detail="Invalid plan pricing")
    paid_amount = int(order_meta.get("amount") or expected_amount)

    now = time.time()
    expiry_sec = now + (365 * 24 * 3600 if billing_cycle == "yearly" else 30 * 24 * 3600)
    existing = await _store.get("profiles", "me", user_id=uid) or {}
    profile = _ensure_user_profile(existing, uid)
    auth_user = current_user_info(raw_request)
    if not profile.get("email") and auth_user.get("email"):
        profile["email"] = str(auth_user.get("email") or "").strip()
    allowance = _plan_allowance(plan_id)
    profile["membership"] = {
        **(profile.get("membership") or {}),
        "plan": plan_id,
        "planName": plan_id.capitalize(),
        "status": "active",
        "billingCycle": billing_cycle,
        "paymentStatus": "paid",
        "startDate": now,
        "expiryDate": expiry_sec,
        "updatedAt": now,
    }
    profile["monthly_credit_allowance"] = allowance
    profile["monthly_credits_left"] = allowance
    profile["credits_balance"] = allowance
    profile["updatedAt"] = now
    await _store.set("profiles", "me", profile, user_id=uid)

    amount_paid = paid_amount
    await _store.set(
        "payments",
        razorpay_payment_id,
        {
            "id": razorpay_payment_id,
            "paymentId": razorpay_payment_id,
            "orderId": razorpay_order_id,
            "userId": uid,
            "planId": plan_id,
            "billingCycle": billing_cycle,
            "amount": amount_paid,
            "currency": "INR",
            "verified": True,
            "timestamp": now,
        },
        user_id=uid,
    )
    email = str(profile.get("email") or "").strip()
    if email:
        await _send_membership_upgrade_email(
            uid=uid,
            email=email,
            plan_id=plan_id,
            billing_cycle=billing_cycle,
            expiry_sec=expiry_sec,
            source="verify",
        )
    return {
        "success": True,
        "message": "Payment verified and membership updated",
        "plan": plan_id,
        "billing_cycle": billing_cycle,
        "expected_amount": expected_amount,
    }


@router.post("/webhook")
async def webhook(raw_request: Request) -> Dict[str, Any]:
    body = await raw_request.body()
    signature = raw_request.headers.get("X-Razorpay-Signature")
    if not _verify_webhook_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {exc}") from exc

    event = str(payload.get("event") or "").strip()
    event_id = str(payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id") or f"evt_{int(time.time()*1000)}")

    if event in {"payment.captured", "order.paid"}:
        result = await _apply_membership_from_webhook(event_id=event_id, event=event, payload=payload)
        return {"status": "ok", "event": event, "result": result}

    await _store.set(
        "payment_webhook_events",
        event_id,
        {
            "id": event_id,
            "event": event,
            "processedAt": time.time(),
            "ignored": True,
        },
        user_id="system",
    )
    return {"status": "ignored", "event": event}


@router.get("/admin/check-payment/{payment_id}")
async def admin_check_payment(payment_id: str, raw_request: Request) -> Dict[str, Any]:
    _ = require_admin(raw_request)
    client = _razorpay_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Payment provider not configured")
    try:
        payment = client.payment.fetch(payment_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Payment not found: {exc}") from exc
    return {"success": True, "payment": payment}
