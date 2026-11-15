import os
import uuid
import hmac
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import HTTPException, status
from pydantic import BaseModel
import razorpay

logger = logging.getLogger(__name__)

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()


def get_razorpay_key_id() -> str:
    """Dynamically get the Razorpay Key ID from env, checking multiple naming conventions."""
    for var in [
        "RAZORPAY_KEY_ID",
        "NEXT_PUBLIC_RAZORPAY_KEY_ID",
        "RAZORPAY_KEY",
        "RAZOR_PAY_KEY_ID",
        "razorpay_key_id",
    ]:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return RAZORPAY_KEY_ID


def get_razorpay_key_secret() -> str:
    """Dynamically get the Razorpay Key Secret from env, checking multiple naming conventions."""
    for var in [
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_SECRET",
        "RAZOR_PAY_KEY_SECRET",
        "RAZOR_PAY_SECRET",
        "razorpay_key_secret",
    ]:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return RAZORPAY_KEY_SECRET

PLANS: Dict[str, Dict[str, Any]] = {
    "free": {
        "name": "Free Tier",
        "amount_inr": 0,
        "period_days": 30,
        "generations_limit": 10,
        "description": "10 Free 3D model generations per month",
        "seats": 1,
    },
    "master": {
        "name": "Master Admin License",
        "amount_inr": 0,
        "period_days": 36500,
        "generations_limit": 999999999,
        "description": "Master account — unrestricted generations and admin privileges",
        "seats": 999,
    },
    "pro_monthly": {
        "name": "Pro Monthly",
        "amount_inr": 999,
        "period_days": 30,
        "generations_limit": 250,
        "description": "250 High-priority model generations / month + advanced segmentation",
        "seats": 1,
    },
    "pro_yearly": {
        "name": "Pro Yearly",
        "amount_inr": 9999,
        "period_days": 365,
        "generations_limit": 3000,
        "description": "3,000 High-priority model generations / year (17% off)",
        "seats": 1,
    },
    "institutional_1yr": {
        "name": "Institutional License (1 Year)",
        "amount_inr": 49999,
        "period_days": 365,
        "generations_limit": 10000,
        "description": "50 University/Lab seats with 10,000 pooled generations/year & domain access",
        "seats": 50,
    },
    "institutional_2yr": {
        "name": "Institutional License (2 Years)",
        "amount_inr": 89999,
        "period_days": 730,
        "generations_limit": 25000,
        "description": "100 University/Lab seats with 25,000 pooled generations/2yr & dedicated SLA",
        "seats": 100,
    },
}


def _get_razorpay_client() -> razorpay.Client:
    key_id = get_razorpay_key_id()
    key_secret = get_razorpay_key_secret()
    if not key_id or not key_secret:
        razor_vars = {k: v[:6] + "..." if v else "" for k, v in os.environ.items() if "RAZOR" in k.upper()}
        logger.warning(f"[payment] Razorpay check failed. Found vars: {razor_vars}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Razorpay credentials not loaded into container environment: "
                f"RAZORPAY_KEY_ID={'SET' if key_id else 'MISSING'}, "
                f"RAZORPAY_KEY_SECRET={'SET' if key_secret else 'MISSING'}."
            ),
        )
    return razorpay.Client(auth=(key_id, key_secret))


# ─── Request & Response Models ────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    plan_id: str

class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int  # in paise
    currency: str
    key_id: str
    plan_id: str
    plan_name: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    plan_id: str

class VerifyPaymentResponse(BaseModel):
    success: bool
    message: str
    plan_id: str
    generations_limit: int
    expires_at: Optional[str] = None


# ─── Payment Operations ───────────────────────────────────────────────────────

async def create_payment_order(plan_id: str, user: Dict[str, Any]) -> CreateOrderResponse:
    if plan_id not in PLANS or plan_id == "free":
        raise HTTPException(status_code=400, detail=f"Invalid or free plan: {plan_id}")

    plan = PLANS[plan_id]
    amount_in_paise = plan["amount_inr"] * 100
    receipt = f"rcpt_{uuid.uuid4().hex[:10]}"

    client = _get_razorpay_client()
    try:
        order_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "user_id": user["id"],
                "email": user["email"],
                "plan_id": plan_id,
                "plan_name": plan["name"],
            },
        }
        order = client.order.create(data=order_data)
        logger.info(f"[payment] Created Razorpay order {order['id']} for {user['email']} (Plan: {plan_id})")

        return CreateOrderResponse(
            order_id=order["id"],
            amount=amount_in_paise,
            currency="INR",
            key_id=get_razorpay_key_id(),
            plan_id=plan_id,
            plan_name=plan["name"],
        )
    except Exception as e:
        logger.error(f"[payment] Razorpay order creation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create Razorpay order: {str(e)}")


async def verify_and_activate_payment(
    db,
    req: VerifyPaymentRequest,
    user: Dict[str, Any],
) -> VerifyPaymentResponse:
    if req.plan_id not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan_id")

    client = _get_razorpay_client()
    
    # 1. Signature Verification
    signature_data = {
        "razorpay_order_id": req.razorpay_order_id,
        "razorpay_payment_id": req.razorpay_payment_id,
        "razorpay_signature": req.razorpay_signature,
    }

    try:
        client.utility.verify_payment_signature(signature_data)
    except razorpay.errors.SignatureVerificationError:
        logger.error(f"[payment] Invalid signature for order {req.razorpay_order_id} (user {user['email']})")
        raise HTTPException(status_code=400, detail="Razorpay signature verification failed. Payment cannot be verified.")

    # 2. Plan Details & Expiration Calculation
    plan = PLANS[req.plan_id]
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    period_end = now + timedelta(days=plan["period_days"])
    period_end_iso = period_end.isoformat()

    # 3. Update User Subscription & Quota in MongoDB Atlas
    update_data = {
        "subscription.plan_id": req.plan_id,
        "subscription.status": "active",
        "subscription.started_at": now_iso,
        "subscription.expires_at": period_end_iso,
        "subscription.razorpay_order_id": req.razorpay_order_id,
        "usage.current_period_start": now_iso,
        "usage.current_period_end": period_end_iso,
        "usage.generations_limit": plan["generations_limit"],
        "usage.generations_count": 0,  # Reset counter upon subscription upgrade
    }

    await db.users.update_one({"id": user["id"]}, {"$set": update_data})

    # 4. Record Transaction History in MongoDB
    transaction_doc = {
        "id": f"txn_{uuid.uuid4().hex[:12]}",
        "user_id": user["id"],
        "email": user["email"],
        "plan_id": req.plan_id,
        "amount_inr": plan["amount_inr"],
        "razorpay_order_id": req.razorpay_order_id,
        "razorpay_payment_id": req.razorpay_payment_id,
        "status": "success",
        "created_at": now_iso,
    }
    await db.transactions.insert_one(transaction_doc)

    logger.info(f"[payment] ✅ Activated {req.plan_id} for user {user['email']}. New limit: {plan['generations_limit']}")

    return VerifyPaymentResponse(
        success=True,
        message=f"Successfully subscribed to {plan['name']}!",
        plan_id=req.plan_id,
        generations_limit=plan["generations_limit"],
        expires_at=period_end_iso,
    )
