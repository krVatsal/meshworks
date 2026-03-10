from fastapi import FastAPI, APIRouter, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from groq import Groq
from tavily import TavilyClient
import os
import logging
import re
import json
import asyncio
import uuid
from pathlib import Path
from dotenv import load_dotenv

from app.blender_mcp import blender_client, conversation_store

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo_url = os.environ['MONGO_URL']
db_client = AsyncIOMotorClient(mongo_url)
db = db_client[os.environ['DB_NAME']]

groq_client = Groq(api_key=os.environ['GROQ_API_KEY'])
tavily_client = TavilyClient(api_key=os.environ['TAVILY_API_KEY'])

app = FastAPI(title="3D Model Discovery API")
api_router = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Models ───────────────────────────────────────────────────────────────────

class SearchAttributes(BaseModel):
    object_type: str
    style: str
    keywords: List[str]
    refined_query: str
    confidence: float = 1.0

class ModelInfo(BaseModel):
    type: str  # "sketchfab" | "glb" | "gltf" | "none"
    url: Optional[str] = None
    embed_url: Optional[str] = None
    title: str
    source_url: str
    source_domain: str

class SearchRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original_prompt: str
    attributes: Optional[SearchAttributes] = None
    primary_model: Optional[ModelInfo] = None
    all_models: List[ModelInfo] = []
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SearchRequest(BaseModel):
    prompt: str

class SearchResponse(BaseModel):
    id: str
    original_prompt: str
    attributes: Optional[SearchAttributes]
    primary_model: Optional[ModelInfo]
    all_models: List[ModelInfo]
    status: str
    error_message: Optional[str]
    created_at: str

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return ""

def extract_sketchfab_id(url: str) -> Optional[str]:
    patterns = [
        r'sketchfab\.com/3d-models/[^/?#]+-([a-f0-9]{32})(?:[/?#]|$)',
        r'sketchfab\.com/models/([a-f0-9]{32})(?:[/?#]|$)',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def is_direct_model_url(url: str) -> bool:
    lo = url.lower().split('?')[0]
    return lo.endswith('.glb') or lo.endswith('.gltf')

def resolve_model_from_result(result: dict) -> Optional[ModelInfo]:
    url = result.get('url', '')
    title = result.get('title', 'Untitled')
    content = result.get('content', '')

    # 1. Sketchfab URL
    sf_id = extract_sketchfab_id(url)
    if sf_id:
        return ModelInfo(
            type="sketchfab",
            embed_url=f"https://sketchfab.com/models/{sf_id}/embed?autostart=1&ui_hint=0&ui_infos=0",
            title=title,
            source_url=url,
            source_domain="sketchfab.com",
        )

    # 2. Direct GLB/GLTF URL
    if is_direct_model_url(url):
        ext = "glb" if ".glb" in url.lower() else "gltf"
        return ModelInfo(type=ext, url=url, title=title, source_url=url, source_domain=_extract_domain(url))

    # 3. GLB/GLTF URL in page content
    matches = re.findall(r'https?://[^\s"\'<>]+\.(?:glb|gltf)(?:\?[^\s"\'<>]*)?', content, re.I)
    if matches:
        mu = matches[0]
        ext = "glb" if ".glb" in mu.lower() else "gltf"
        return ModelInfo(type=ext, url=mu, title=title, source_url=url, source_domain=_extract_domain(url))

    return None

def record_to_response(record: dict) -> SearchResponse:
    def _parse_attrs(a):
        return SearchAttributes(**a) if a else None

    def _parse_model(m):
        return ModelInfo(**m) if m else None

    created = record.get("created_at", datetime.now(timezone.utc))
    if isinstance(created, datetime):
        created_str = created.isoformat()
    else:
        created_str = str(created)

    return SearchResponse(
        id=record.get("id", ""),
        original_prompt=record.get("original_prompt", ""),
        attributes=_parse_attrs(record.get("attributes")),
        primary_model=_parse_model(record.get("primary_model")),
        all_models=[ModelInfo(**m) for m in record.get("all_models", [])],
        status=record.get("status", "unknown"),
        error_message=record.get("error_message"),
        created_at=created_str,
    )

# ─── Services ─────────────────────────────────────────────────────────────────

async def refine_prompt_with_groq(prompt: str) -> SearchAttributes:
    system_prompt = (
        "You are a 3D model search expert. Analyze the user's query and return JSON with:\n"
        "- object_type: main category (vehicle, character, building, creature, weapon, furniture, nature, aircraft, robot, etc.)\n"
        "- style: artistic style (realistic, sci-fi, cyberpunk, cartoon, low-poly, fantasy, medieval, futuristic, etc.)\n"
        "- keywords: array of 3-6 specific search terms (most distinctive first)\n"
        "- refined_query: concise effective search query, max 7 words\n"
        "- confidence: float 0.0-1.0\n"
        "Examples:\n"
        '- "a futuristic cyberpunk motorcycle" -> {"object_type":"vehicle","style":"cyberpunk","keywords":["motorcycle","cyberpunk","futuristic","neon"],"refined_query":"cyberpunk motorcycle futuristic neon","confidence":0.95}\n'
        '- "medieval knight in armor" -> {"object_type":"character","style":"medieval","keywords":["knight","armor","medieval","warrior"],"refined_query":"medieval knight armor warrior","confidence":0.92}'
    )

    def _call():
        return groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=512,
            temperature=0.2,
        )

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _call)
    data = json.loads(response.choices[0].message.content)

    return SearchAttributes(
        object_type=data.get("object_type", "object"),
        style=data.get("style", "realistic"),
        keywords=data.get("keywords", [prompt]),
        refined_query=data.get("refined_query", prompt),
        confidence=float(data.get("confidence", 0.8)),
    )


async def search_models_with_tavily(attributes: SearchAttributes) -> List[ModelInfo]:
    loop = asyncio.get_event_loop()

    queries = [
        f"{attributes.refined_query} 3d model site:sketchfab.com",
        f"{attributes.refined_query} free 3d model gltf glb download",
        f"{attributes.object_type} {attributes.style} 3d model sketchfab",
    ]

    async def _search(query: str):
        def _call():
            return tavily_client.search(
                query=query,
                search_depth="basic",
                max_results=5,
                include_answer=False,
            )
        try:
            result = await loop.run_in_executor(None, _call)
            return result.get("results", [])
        except Exception as e:
            logger.error(f"Tavily error for '{query}': {e}")
            return []

    all_raw = await asyncio.gather(*[_search(q) for q in queries])

    models = []
    seen = set()
    for batch in all_raw:
        for result in batch:
            model = resolve_model_from_result(result)
            if model:
                key = model.embed_url or model.url or model.source_url
                if key and key not in seen:
                    seen.add(key)
                    models.append(model)

    return models

# ─── Routes ───────────────────────────────────────────────────────────────────

@api_router.post("/search", response_model=SearchResponse)
async def search_models(request: SearchRequest):
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    # Cache check: same prompt within 24 hours
    cache_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cached = await db.searches.find_one(
        {
            "original_prompt": {"$regex": f"^{re.escape(prompt)}$", "$options": "i"},
            "status": {"$in": ["completed", "no_model"]},
            "created_at": {"$gte": cache_cutoff},
        },
        {"_id": 0},
    )
    if cached:
        logger.info(f"Cache hit: {prompt}")
        return record_to_response(cached)

    record = SearchRecord(original_prompt=prompt)
    doc = record.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.searches.insert_one(doc)

    try:
        attributes = await refine_prompt_with_groq(prompt)
        models = await search_models_with_tavily(attributes)
        primary = models[0] if models else None
        status = "completed" if primary else "no_model"

        update = {
            "attributes": attributes.model_dump(),
            "primary_model": primary.model_dump() if primary else None,
            "all_models": [m.model_dump() for m in models],
            "status": status,
        }
        await db.searches.update_one({"id": record.id}, {"$set": update})

        return SearchResponse(
            id=record.id,
            original_prompt=prompt,
            attributes=attributes,
            primary_model=primary,
            all_models=models,
            status=status,
            error_message=None,
            created_at=record.created_at.isoformat(),
        )

    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        await db.searches.update_one(
            {"id": record.id},
            {"$set": {"status": "failed", "error_message": str(e)}},
        )
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@api_router.get("/history", response_model=List[SearchResponse])
async def get_history(limit: int = Query(default=20, le=50)):
    records = await (
        db.searches
        .find({"status": {"$in": ["completed", "no_model"]}}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return [record_to_response(r) for r in records]


@api_router.get("/history/{search_id}", response_model=SearchResponse)
async def get_history_item(search_id: str):
    record = await db.searches.find_one({"id": search_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Search not found")
    return record_to_response(record)


@api_router.delete("/history/{search_id}")
async def delete_history_item(search_id: str):
    result = await db.searches.delete_one({"id": search_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Search not found")
    return {"message": "Deleted"}


@api_router.get("/")
async def root():
    return {"message": "3D Model Discovery API", "version": "1.0"}


# ─── Blender MCP Models ───────────────────────────────────────────────────────

class BlenderConnectRequest(BaseModel):
    command: str = "uvx"
    args: List[str] = ["blender-mcp"]


class ToolCallInfo(BaseModel):
    tool: str
    input: Dict[str, Any]
    result: str


class BlenderChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class BlenderChatResponse(BaseModel):
    response: str
    tool_calls: List[ToolCallInfo]
    conversation_id: str
    glb_path: Optional[str] = None


# ─── Blender MCP Routes ───────────────────────────────────────────────────────

@api_router.post("/blender/connect")
async def blender_connect(request: BlenderConnectRequest = BlenderConnectRequest()):
    """
    Start the Blender MCP server subprocess and connect to it.
    Blender must already be running with the blender-mcp addon enabled.
    """
    if blender_client.is_connected:
        return {
            "status": "already_connected",
            "tools": blender_client.tools,
        }
    try:
        tools = await blender_client.connect(
            command=request.command, args=request.args
        )
        return {"status": "connected", "tools": tools}
    except Exception as exc:
        logger.error("Blender MCP connect error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@api_router.post("/blender/disconnect")
async def blender_disconnect():
    """Disconnect from the Blender MCP server."""
    if not blender_client.is_connected:
        return {"status": "not_connected"}
    try:
        await blender_client.disconnect()
        return {"status": "disconnected"}
    except Exception as exc:
        logger.error("Blender MCP disconnect error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@api_router.get("/blender/status")
async def blender_status():
    """Return connection status and available tools."""
    return {
        "connected": blender_client.is_connected,
        "tools": blender_client.tools if blender_client.is_connected else [],
        "conversations": conversation_store.list_ids(),
    }


@api_router.get("/blender/tools")
async def blender_tools():
    """List all tools exposed by the connected Blender MCP server."""
    if not blender_client.is_connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to Blender MCP server. POST /api/blender/connect first.",
        )
    return {"tools": blender_client.tools}


@api_router.post("/blender/chat", response_model=BlenderChatResponse)
async def blender_chat(request: BlenderChatRequest):
    """
    Send a natural-language message to Claude.  Claude has access to all
    Blender MCP tools and will call them automatically to fulfil the request.

    Pass `conversation_id` to continue an existing conversation; omit it (or
    pass null) to start a new one.  The returned `conversation_id` should be
    forwarded in subsequent requests.
    """
    if not blender_client.is_connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to Blender MCP server. POST /api/blender/connect first.",
        )

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    conversation_id = request.conversation_id or str(uuid.uuid4())

    try:
        result = await blender_client.chat(
            user_message=message,
            conversation_id=conversation_id,
        )
        return BlenderChatResponse(
            response=result["response"],
            tool_calls=[ToolCallInfo(**tc) for tc in result["tool_calls"]],
            conversation_id=result["conversation_id"],
            glb_path=result.get("glb_path"),
        )
    except Exception as exc:
        logger.error("Blender chat error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@api_router.delete("/blender/conversation/{conversation_id}")
async def blender_clear_conversation(conversation_id: str):
    """Clear a conversation's history."""
    conversation_store.clear(conversation_id)
    return {"status": "cleared", "conversation_id": conversation_id}


app.include_router(api_router)


@app.on_event("shutdown")
async def shutdown_db():
    db_client.close()
    if blender_client.is_connected:
        await blender_client.disconnect()