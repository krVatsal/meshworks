import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Request, status

logger = logging.getLogger(__name__)

vr_router = APIRouter(prefix="/vr", tags=["VR"])

# ─── VR Plan Allocations ──────────────────────────────────────────────────────
# Free: 1 session/month, 10 minutes (600s)
# Pro Monthly: 25 sessions/month, 45 minutes (2700s)
# Pro Yearly: 300 sessions/year, unlimited (0s)
# Institutional: 1000 sessions/year, unlimited (0s)
# Master: Unlimited

VR_LIMITS = {
    "free": {"sessions_per_month": 1, "duration_seconds": 600},
    "pro_monthly": {"sessions_per_month": 25, "duration_seconds": 2700},
    "pro_yearly": {"sessions_per_month": 300, "duration_seconds": 0},
    "institutional_1yr": {"sessions_per_month": 1000, "duration_seconds": 0},
    "institutional_2yr": {"sessions_per_month": 3000, "duration_seconds": 0},
    "master": {"sessions_per_month": 999999, "duration_seconds": 0},
}


# ─── Schemas ──────────────────────────────────────────────────────────────────

class StartVRSessionRequest(BaseModel):
    model_id: str

class StartVRSessionResponse(BaseModel):
    session_id: str
    model_id: str
    duration_seconds: int
    unlimited_time: bool
    remaining_sessions: int
    plan_id: str
    is_master: bool

class VRChatRequest(BaseModel):
    session_id: Optional[str] = None
    model_id: str
    message: str
    highlighted_part: Optional[str] = None

class VRChatResponse(BaseModel):
    response: str
    part_focus: Optional[str] = None
    action: Optional[str] = None  # e.g., 'highlight', 'explode', 'reset'


# ─── Quota Management Helper ──────────────────────────────────────────────────

async def check_and_consume_vr_quota(db, user: Dict[str, Any]) -> Dict[str, Any]:
    """Check user's VR session quota and increment session count."""
    user_id = user["id"]
    email = (user.get("email") or "").lower().strip()
    role = user.get("role", "user")
    subscription = user.get("subscription", {})
    plan_id = subscription.get("plan_id", "free")

    # Check Master Account
    from app.auth import is_master_email
    is_master = is_master_email(email) or role == "master" or plan_id == "master"

    if is_master:
        return {
            "allowed": True,
            "duration_seconds": 0,
            "unlimited_time": True,
            "remaining_sessions": 999999,
            "plan_id": "master",
            "is_master": True,
        }

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    usage = user.get("usage", {})

    vr_count = usage.get("vr_sessions_count", 0)
    config = VR_LIMITS.get(plan_id, VR_LIMITS["free"])
    limit = config["sessions_per_month"]
    duration = config["duration_seconds"]

    # Monthly rollover check
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
                            "usage.vr_sessions_count": 0,
                        }
                    },
                )
                vr_count = 0
        except Exception:
            pass

    if vr_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "VR_QUOTA_EXCEEDED",
                "message": (
                    f"You have used your {limit} VR Holodeck session for this month. "
                    "Upgrade your plan for extended VR access."
                ),
                "used": vr_count,
                "limit": limit,
                "plan_id": plan_id,
            },
        )

    # Increment VR session counter
    await db.users.update_one(
        {"id": user_id},
        {"$inc": {"usage.vr_sessions_count": 1}}
    )
    new_count = vr_count + 1
    logger.info(f"[vr] Started VR session for {email}: {new_count}/{limit}")

    return {
        "allowed": True,
        "duration_seconds": duration,
        "unlimited_time": duration == 0,
        "remaining_sessions": max(0, limit - new_count),
        "plan_id": plan_id,
        "is_master": False,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@vr_router.post("/session/start", response_model=StartVRSessionResponse)
async def start_vr_session(req: StartVRSessionRequest, request: Request):
    """Start and authenticate a VR Holodeck session for a 3D model."""
    from app.auth import get_optional_current_user_from_request
    from app.server import db, REQUIRE_AUTH

    target_db = getattr(request.app.state, "db", None) or db
    user = await get_optional_current_user_from_request(request)

    if not user:
        if REQUIRE_AUTH:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to enter VR Holodeck.",
            )
        # Temporary guest session (3-minute teaser)
        session_id = f"vr_guest_{uuid.uuid4().hex[:10]}"
        return StartVRSessionResponse(
            session_id=session_id,
            model_id=req.model_id,
            duration_seconds=180,
            unlimited_time=False,
            remaining_sessions=0,
            plan_id="guest",
            is_master=False,
        )

    quota = await check_and_consume_vr_quota(target_db, user)
    session_id = f"vr_{uuid.uuid4().hex[:12]}"

    return StartVRSessionResponse(
        session_id=session_id,
        model_id=req.model_id,
        duration_seconds=quota["duration_seconds"],
        unlimited_time=quota["unlimited_time"],
        remaining_sessions=quota["remaining_sessions"],
        plan_id=quota["plan_id"],
        is_master=quota["is_master"],
    )


@vr_router.post("/chat", response_model=VRChatResponse)
async def vr_chat_endpoint(req: VRChatRequest, request: Request):
    """Contextual conversational response for in-VR floating HUD and spatial audio."""
    from app.server import db, groq_client, GROQ_MODEL

    target_db = getattr(request.app.state, "db", None) or db

    # Fetch model search record for context
    record = await target_db.searches.find_one({"id": req.model_id}, {"_id": 0})
    model_title = "3D Asset"
    keywords = []
    semantic_parts = []
    prompt = ""

    if record:
        model_title = record.get("primary_model", {}).get("title") or record.get("original_prompt", "")
        prompt = record.get("original_prompt", "")
        keywords = record.get("attributes", {}).get("keywords", [])
        semantic_parts = record.get("semantic_names", [])

    system_prompt = (
        "You are an interactive AI Curator inside a Virtual Reality 3D Holodeck classroom. "
        "The user is wearing a VR headset, standing directly in front of a 3D model. "
        "Answer questions clearly, concisely (max 2-3 sentences), engagingly, and enthusiastically. "
        f"Model: '{model_title}'. Original Prompt: '{prompt}'. "
        f"Available Parts/Segments: {', '.join(semantic_parts) if semantic_parts else ', '.join(keywords)}. "
        f"Currently focused part: '{req.highlighted_part or 'whole model'}'. "
        "If the user asks about a specific part, explain its function or visual role in the 3D model."
    )

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.message},
            ],
            temperature=0.6,
            max_tokens=150,
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"[vr_chat] Groq completion failed ({e}), using fallback")
        if req.highlighted_part:
            reply = f"You are inspecting the {req.highlighted_part}. This component forms a critical structural element of the {model_title}."
        else:
            reply = f"This is the {model_title}. You can point and pull any segment away with your VR controller grip."

    return VRChatResponse(
        response=reply,
        part_focus=req.highlighted_part,
        action="highlight" if req.highlighted_part else None,
    )
