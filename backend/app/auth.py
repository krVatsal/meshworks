import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()

import jwt
from fastapi import HTTPException, Security, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
JWT_SECRET = os.environ.get("JWT_SECRET", "meshworks-secret-key-prod-2026-auth").strip()
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 30
FREE_GENERATIONS_PER_MONTH = 10

# Master emails with zero restrictions and unlimited access
MASTER_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("MASTER_EMAILS", "prompt2mesh@gmail.com").split(",")
    if e.strip()
]


def is_master_email(email: Optional[str]) -> bool:
    """Check if an email is registered as an unrestricted master account."""
    if not email:
        return False
    return email.lower().strip() in MASTER_EMAILS


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class GoogleAuthRequest(BaseModel):
    id_token: str

class UserUsage(BaseModel):
    current_period_start: str
    current_period_end: str
    generations_count: int = 0
    generations_limit: int = FREE_GENERATIONS_PER_MONTH

class UserSubscription(BaseModel):
    plan_id: str = "free"
    status: str = "active"
    started_at: str
    expires_at: Optional[str] = None
    razorpay_customer_id: Optional[str] = None
    razorpay_subscription_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    picture: Optional[str] = None
    role: str = "user"
    institution_id: Optional[str] = None
    subscription: UserSubscription
    usage: UserUsage
    created_at: str
    last_login_at: str

class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ─── Token Utilities ──────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRATION_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please sign in again.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )


def verify_google_id_token(token_str: str) -> Dict[str, Any]:
    """Verify Google ID token and return claims dict."""
    client_id = (
        os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        or os.environ.get("NEXT_PUBLIC_GOOGLE_CLIENT_ID", "").strip()
        or GOOGLE_CLIENT_ID
    )
    audience = client_id if client_id else None

    # 1. Primary: Verify locally with google-auth library (with 120s clock skew tolerance)
    try:
        request = google_requests.Request()
        id_info = google_id_token.verify_oauth2_token(
            token_str, request, audience=audience, clock_skew_in_seconds=120
        )

        if id_info.get("iss") not in ["accounts.google.com", "https://accounts.google.com"]:
            raise ValueError(f"Wrong issuer: {id_info.get('iss')}")

        return {
            "google_id": id_info.get("sub"),
            "email": id_info.get("email"),
            "name": id_info.get("name") or id_info.get("email", "").split("@")[0],
            "picture": id_info.get("picture"),
        }
    except Exception as e1:
        logger.warning(f"[auth] Local google-auth verification failed ({e1}), checking tokeninfo fallback...")

    # 2. Secondary: Fallback to Google's official tokeninfo API
    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": token_str})
            if resp.status_code == 200:
                id_info = resp.json()
                if audience and id_info.get("aud") != audience:
                    raise ValueError(f"Audience mismatch: token aud '{id_info.get('aud')}' != expected '{audience}'")
                if id_info.get("iss") not in ["accounts.google.com", "https://accounts.google.com"]:
                    raise ValueError(f"Wrong issuer: {id_info.get('iss')}")

                return {
                    "google_id": id_info.get("sub"),
                    "email": id_info.get("email"),
                    "name": id_info.get("name") or id_info.get("email", "").split("@")[0],
                    "picture": id_info.get("picture"),
                }
            else:
                logger.error(f"[auth] Google tokeninfo API failed: {resp.text}")
                raise ValueError(f"Google tokeninfo response: {resp.text}")
    except Exception as e2:
        logger.error(f"[auth] Google token verification failed: {e2}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google authentication failed: {str(e2)}",
        )


# ─── Database User & Quota Management ─────────────────────────────────────────

async def get_or_create_user(db, google_info: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert user in MongoDB based on Google account info."""
    email = google_info["email"].lower().strip()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    is_master = is_master_email(email)

    user = await db.users.find_one({"email": email})

    if user:
        # Check if period rollover is needed
        usage = user.get("usage", {})
        period_end_str = usage.get("current_period_end")
        needs_rollover = False
        if period_end_str:
            try:
                period_end = datetime.fromisoformat(period_end_str)
                if now >= period_end:
                    needs_rollover = True
            except Exception:
                needs_rollover = True
        else:
            needs_rollover = True

        update_fields: Dict[str, Any] = {
            "name": google_info.get("name") or user.get("name"),
            "picture": google_info.get("picture") or user.get("picture"),
            "last_login_at": now_iso,
        }

        # If master email, ensure master privileges are active with infinite limits
        if is_master:
            update_fields["role"] = "master"
            update_fields["subscription.plan_id"] = "master"
            update_fields["subscription.status"] = "active"
            update_fields["usage.generations_limit"] = 999999999

        if needs_rollover and not is_master:
            rollover_end = (now + timedelta(days=30)).isoformat()
            update_fields["usage.current_period_start"] = now_iso
            update_fields["usage.current_period_end"] = rollover_end
            update_fields["usage.generations_count"] = 0
            logger.info(f"[auth] Rolled over monthly quota for user {email}")

        await db.users.update_one({"email": email}, {"$set": update_fields})
        user = await db.users.find_one({"email": email})
        return user

    # Create new user
    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    period_end_iso = (now + timedelta(days=36500)).isoformat() if is_master else (now + timedelta(days=30)).isoformat()

    new_user = {
        "id": user_id,
        "google_id": google_info.get("google_id"),
        "email": email,
        "name": google_info.get("name") or email.split("@")[0],
        "picture": google_info.get("picture"),
        "role": "master" if is_master else "user",
        "institution_id": None,
        "created_at": now_iso,
        "last_login_at": now_iso,
        "subscription": {
            "plan_id": "master" if is_master else "free",
            "status": "active",
            "started_at": now_iso,
            "expires_at": None,
            "razorpay_customer_id": None,
            "razorpay_subscription_id": None,
            "razorpay_order_id": None,
        },
        "usage": {
            "current_period_start": now_iso,
            "current_period_end": period_end_iso,
            "generations_count": 0,
            "generations_limit": 999999999 if is_master else FREE_GENERATIONS_PER_MONTH,
        },
    }

    await db.users.insert_one(new_user)
    logger.info(f"[auth] Created new user: {email} ({user_id}) [role={'master' if is_master else 'user'}]")
    return new_user


async def check_and_consume_quota(db, user_id: str) -> Dict[str, Any]:
    """
    Check if the user has available generations for the current period.
    Resets quota if period expired. Increments usage by 1 on success.
    Throws 403 Forbidden if quota exceeded.
    Master email / role is completely unrestricted with unlimited quota.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    email = (user.get("email") or "").lower().strip()
    is_master = (
        is_master_email(email)
        or user.get("role") == "master"
        or user.get("subscription", {}).get("plan_id") == "master"
    )

    # ── Master Account Bypass: ZERO restrictions ───────────────────────────────
    if is_master:
        await db.users.update_one(
            {"id": user_id},
            {"$inc": {"usage.generations_count": 1}}
        )
        count = user.get("usage", {}).get("generations_count", 0) + 1
        logger.info(f"[quota] Master account {email} generated model (unrestricted). Count={count}")
        return {
            "used": count,
            "limit": 999999999,
            "remaining": 999999999,
            "plan_id": "master",
            "is_master": True,
        }

    usage = user.get("usage", {})
    subscription = user.get("subscription", {})
    plan_id = subscription.get("plan_id", "free")
    count = usage.get("generations_count", 0)
    limit = usage.get("generations_limit", FREE_GENERATIONS_PER_MONTH)

    # Check monthly rollover
    period_end_str = usage.get("current_period_end")
    if period_end_str:
        try:
            period_end = datetime.fromisoformat(period_end_str)
            if now >= period_end:
                rollover_end = (now + timedelta(days=30)).isoformat()
                await db.users.update_one(
                    {"id": user_id},
                    {
                        "$set": {
                            "usage.current_period_start": now_iso,
                            "usage.current_period_end": rollover_end,
                            "usage.generations_count": 0,
                        }
                    },
                )
                count = 0
        except Exception:
            pass

    # Free plan quota gate
    if plan_id == "free" and count >= limit:
        logger.warning(f"[quota] User {user.get('email')} exceeded monthly quota ({count}/{limit})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "QUOTA_EXCEEDED",
                "message": (
                    f"You have used all {limit} free generations for this month. "
                    "Upgrade to Pro or an Institutional plan for more generations."
                ),
                "used": count,
                "limit": limit,
                "resets_at": usage.get("current_period_end"),
            },
        )

    # Atomically increment usage
    await db.users.update_one(
        {"id": user_id},
        {"$inc": {"usage.generations_count": 1}}
    )
    new_count = count + 1
    logger.info(f"[quota] Consumed 1 generation for {user.get('email')}: {new_count}/{limit}")

    return {
        "used": new_count,
        "limit": limit,
        "remaining": max(0, limit - new_count),
        "plan_id": plan_id,
    }


# ─── FastAPI Dependencies ─────────────────────────────────────────────────────

async def get_current_user_from_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Dict[str, Any]:
    """Require valid user JWT token."""
    token = None
    if credentials:
        token = credentials.credentials
    else:
        # Also check custom header or query for flexibility
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split("Bearer ")[1].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in with Google.",
        )

    payload = decode_access_token(token)
    user_id = payload.get("sub")

    # Access db from server module or request app state
    db = getattr(request.app.state, "db", None)
    if db is None:
        from app.server import db as server_db
        db = server_db

    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found. Please sign in again.",
        )

    if is_master_email(user.get("email")):
        user["role"] = "master"
        if "subscription" in user and isinstance(user["subscription"], dict):
            user["subscription"]["plan_id"] = "master"
        if "usage" in user and isinstance(user["usage"], dict):
            user["usage"]["generations_limit"] = 999999999

    return user


async def get_optional_current_user_from_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[Dict[str, Any]]:
    """Return user if authenticated, else None."""
    try:
        return await get_current_user_from_request(request, credentials)
    except HTTPException:
        return None
