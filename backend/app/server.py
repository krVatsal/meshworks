from fastapi import FastAPI, APIRouter, HTTPException, Query, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
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
import httpx
import tempfile
import aiofiles

from app.blender_mcp import blender_client, conversation_store
from app.mesh_renamer import rename_meshes_from_file
from app.model_scorer import (
    score_model, 
    score_candidates, 
    THRESHOLD_LABELLED, 
    THRESHOLD_UNLABELLED,
    SEMANTIC_WEIGHT,
    GEOMETRIC_WEIGHT
)
from app.sketchfab_fetcher import (
    fetch_model,
    classify_url,
    ModelSource,
    FetchResult,
    SketchfabMetadata
)

ROOT_DIR = Path(__file__).parent.parent.parent.parent  # Sankalp/
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
    # Sketchfab metadata
    description: Optional[str] = None
    tags: List[str] = []
    categories: List[str] = []
    vertex_count: Optional[int] = None
    face_count: Optional[int] = None
    animation_count: Optional[int] = None
    is_downloadable: Optional[bool] = None
    license: Optional[str] = None
    author: Optional[str] = None
    thumbnail_url: Optional[str] = None

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

class ModelScoreRequest(BaseModel):
    model_url: str
    prompt: str
    metadata: Optional[Dict[str, Any]] = None

class ModelScoreResponse(BaseModel):
    final_score: float
    is_labelled: bool
    decision: str  # USE / REFETCH / CACHE / DISCARD
    semantic_score: float
    geometric_score: float
    details: Dict[str, Any]

class BatchScoreRequest(BaseModel):
    search_id: str
    prompt: str

class BatchScoreResponse(BaseModel):
    search_id: str
    ranked_models: List[Dict[str, Any]]
    best_model: Optional[Dict[str, Any]]

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

# Update resolve_model_from_result to be async and fetch metadata
async def resolve_model_from_result(result: dict) -> Optional[ModelInfo]:
    url = result.get('url', '')
    title = result.get('title', 'Untitled')
    content = result.get('content', '')

    # 1. Sketchfab URL
    sf_id = extract_sketchfab_id(url)
    if sf_id:
        # Fetch rich metadata from Sketchfab API using the fetcher
        sketchfab_api_key = os.environ.get('SKETCHFAB_API_KEY', '')
        
        try:
            from app.sketchfab_fetcher import fetch_sketchfab_metadata as fetch_sf_meta
            metadata_obj = await fetch_sf_meta(sf_id, sketchfab_api_key)
            
            if metadata_obj and metadata_obj.title:
                return ModelInfo(
                    type="sketchfab",
                    url=url,  # ✅ Add source URL for fetching/downloading
                    embed_url=f"https://sketchfab.com/models/{sf_id}/embed?autostart=1&ui_hint=0&ui_infos=0",
                    title=metadata_obj.title or title,
                    source_url=url,
                    source_domain="sketchfab.com",
                    description=metadata_obj.description,
                    tags=metadata_obj.tags,
                    face_count=metadata_obj.face_count,
                    is_downloadable=metadata_obj.is_downloadable,
                    license=metadata_obj.license,
                    author=metadata_obj.author,
                    thumbnail_url=metadata_obj.thumbnail_url,
                )
        except Exception as e:
            logger.warning(f"Failed to fetch Sketchfab metadata for {sf_id}: {e}")
        
        # Fallback if API fails
        return ModelInfo(
            type="sketchfab",
            url=url,  # ✅ Add source URL for fetching/downloading
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

    # Prioritize direct downloads from free model repositories
    queries = [
        f"{attributes.refined_query} 3d model glb gltf download site:polyhaven.com OR site:quaternius.com OR site:kenney.nl",
        f"{attributes.refined_query} free 3d model .glb .gltf direct download CC0",
        f"{attributes.object_type} {attributes.style} 3d asset glb gltf free download",
        f"{attributes.refined_query} 3d model site:sketchfab.com",  # Fallback to Sketchfab last
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
            model = await resolve_model_from_result(result)  # Add await
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
        
        if not models:
            update = {
                "attributes": attributes.model_dump(),
                "primary_model": None,
                "all_models": [],
                "status": "no_model",
            }
            await db.searches.update_one({"id": record.id}, {"$set": update})
            return SearchResponse(
                id=record.id,
                original_prompt=prompt,
                attributes=attributes,
                primary_model=None,
                all_models=[],
                status="no_model",
                error_message=None,
                created_at=record.created_at.isoformat(),
            )

        # ─── SCORING & DECISION GATES ───────────────────────────────────────
        logger.info(f"\n{'='*80}")
        logger.info(f"Found {len(models)} models, starting scoring pipeline...")
        logger.info(f"{'='*80}")
        for idx, m in enumerate(models[:5], 1):
            logger.info(f"  [{idx}] {m.title[:60]} | Type: {m.type} | URL: {m.url}")
        logger.info(f"{'='*80}\n")
        
        sketchfab_api_key = os.environ.get('SKETCHFAB_API_KEY', '')
        if not sketchfab_api_key:
            logger.warning("⚠️  SKETCHFAB_API_KEY not set - downloadable models will be skipped!")
        
        scored_models = []
        all_scoring_results = []  # Track all models for summary
        
        # Download and score each model
        for i, model in enumerate(models[:5], 1):  # Limit to top 5 to avoid long delays
            model_url = model.url
            if not model_url:
                continue
                
            try:
                logger.info(f"[{i}/{min(len(models), 5)}] Fetching {model_url}")
                
                # Download model
                fetch_result = await fetch_model(
                    url=model_url,
                    sketchfab_api_key=sketchfab_api_key
                )
                
                logger.info(f"[{i}/{min(len(models), 5)}] Fetch status: {fetch_result.status.value}, is_scorable: {fetch_result.is_scorable}, local_path: {fetch_result.local_path}")
                
                # Only score if successfully fetched
                if fetch_result.is_scorable and fetch_result.local_path:
                    # fetch_result.metadata is already a dict with title, description, tags
                    metadata = fetch_result.metadata if fetch_result.metadata else {
                        "title": model.title,
                        "description": model.description or "",
                        "tags": model.tags,
                    }
                    
                    logger.info(f"[{i}/{min(len(models), 5)}] Scoring model...")
                    
                    # Run scoring in thread pool
                    loop = asyncio.get_event_loop()
                    score_result = await loop.run_in_executor(
                        None,
                        score_model,
                        str(fetch_result.local_path),
                        prompt,
                        metadata
                    )
                    
                    # Log detailed scoring breakdown
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[{i}/{min(len(models), 5)}] SCORING RESULT: {model.title[:50]}")
                    logger.info(f"{'='*70}")
                    logger.info(f"  Semantic Score:  {score_result.semantic.combined:.4f} (CLIP: {score_result.semantic.clip_score:.4f}, Metadata: {score_result.semantic.metadata_score:.4f})")
                    logger.info(f"  Geometric Score: {score_result.geometric.combined:.4f}")
                    logger.info(f"  Final Score:     {score_result.final_score:.4f} = {SEMANTIC_WEIGHT:.1f}×{score_result.semantic.combined:.4f} + {GEOMETRIC_WEIGHT:.1f}×{score_result.geometric.combined:.4f}")
                    logger.info(f"  Is Labelled:     {score_result.is_labelled}")
                    logger.info(f"  Decision:        {score_result.decision}")
                    logger.info(f"-" * 70)
                    
                    # Store all results for summary
                    all_scoring_results.append({
                        "title": model.title[:40],
                        "score": score_result.final_score,
                        "semantic": score_result.semantic.combined,
                        "geometric": score_result.geometric.combined,
                        "labelled": score_result.is_labelled,
                        "decision": score_result.decision,
                    })
                    
                    # Apply decision gates - only USE and CACHE models are shown
                    if score_result.decision in ["USE", "CACHE"]:
                        if score_result.decision == "USE":
                            logger.info(f"  ✅ APPROVED (USE): Labelled + Score {score_result.final_score:.4f} > {THRESHOLD_LABELLED:.2f} threshold")
                            logger.info(f"     → Model has named nodes and good quality. Ready for immediate use!")
                        else:  # CACHE
                            logger.info(f"  ✅ APPROVED (CACHE): Unlabelled + Score {score_result.final_score:.4f} > {THRESHOLD_UNLABELLED:.2f} threshold")
                            logger.info(f"     → High quality but needs labeling. Will trigger Blender MCP for node naming.")
                        
                        scored_models.append({
                            "model": model,
                            "score": score_result.final_score,
                            "decision": score_result.decision,
                            "is_labelled": score_result.is_labelled,
                            "semantic_score": score_result.semantic.combined,
                            "geometric_score": score_result.geometric.combined,
                        })
                        
                        # TODO: If CACHE, trigger Blender MCP in background for labeling
                        if score_result.decision == "CACHE":
                            logger.info(f"     [TODO] Blender MCP labeling pipeline pending")
                    
                    elif score_result.decision == "REFETCH":
                        logger.info(f"  ❌ REJECTED (REFETCH): Labelled but Score {score_result.final_score:.4f} ≤ {THRESHOLD_LABELLED:.2f} threshold")
                        logger.info(f"     → Model has labels but is the WRONG OBJECT (semantic mismatch)")
                        logger.info(f"     → Search fetched something irrelevant. Need better query refinement.")
                    
                    elif score_result.decision == "DISCARD":
                        logger.info(f"  ❌ REJECTED (DISCARD): Unlabelled and Score {score_result.final_score:.4f} ≤ {THRESHOLD_UNLABELLED:.2f} threshold")
                        logger.info(f"     → Model is either wrong object OR too low quality")
                        logger.info(f"     → Not worth caching. Will generate from scratch with Blender.")
                    
                    logger.info(f"{'='*70}\n")
                
                else:
                    # Model fetched but not scorable
                    logger.warning(f"[{i}/{min(len(models), 5)}] ⚠️  Model NOT SCORABLE")
                    logger.warning(f"     Status: {fetch_result.status.value}")
                    logger.warning(f"     Reason: is_scorable={fetch_result.is_scorable}, has_local_path={bool(fetch_result.local_path)}")
                    if fetch_result.status.value == "NOT_DOWNLOADABLE":
                        logger.warning(f"     → Model is view-only (Sketchfab embed without download permission)")
                    elif fetch_result.status.value == "DOWNLOAD_FAILED":
                        logger.warning(f"     → Download failed - model may require authentication or be deleted")
                    
            except Exception as e:
                logger.error(f"[{i}/{min(len(models), 5)}] ❌ EXCEPTION during fetch/score: {type(e).__name__}: {e}", exc_info=True)
                continue
        
        # Sort by score (highest first)
        scored_models.sort(key=lambda x: x["score"], reverse=True)
        
        # Extract approved models
        approved_models = [item["model"] for item in scored_models]
        primary = approved_models[0] if approved_models else None
        
        # ─── SCORING SUMMARY TABLE ───
        logger.info(f"\n{'='*90}")
        logger.info(f"  SCORING SUMMARY - {len(all_scoring_results)} MODELS EVALUATED")
        logger.info(f"{'='*90}")
        logger.info(f"  {'Model':<40} {'Final':<8} {'Sem':<7} {'Geo':<7} {'Labelled':<10} {'Decision':<10}")
        logger.info(f"  {'-'*40} {'-'*8} {'-'*7} {'-'*7} {'-'*10} {'-'*10}")
        
        for result in all_scoring_results:
            decision_symbol = "✅" if result["decision"] in ["USE", "CACHE"] else "❌"
            labelled_str = "Yes" if result["labelled"] else "No"
            logger.info(
                f"  {result['title']:<40} {result['score']:.4f}   "
                f"{result['semantic']:.4f}  {result['geometric']:.4f}  "
                f"{labelled_str:<10} {decision_symbol} {result['decision']:<10}"
            )
        
        logger.info(f"{'='*90}")
        logger.info(f"  THRESHOLDS: Labelled models need >{THRESHOLD_LABELLED:.2f} | Unlabelled models need >{THRESHOLD_UNLABELLED:.2f}")
        logger.info(f"  RESULT: {len(scored_models)} APPROVED / {len(all_scoring_results)} EVALUATED")
        logger.info(f"{'='*90}\n")
        
        # ─── FALLBACK STRATEGIES: Return unscored models or generate procedural ───
        if not approved_models:
            logger.warning("⚠️  All models REJECTED by decision gates (REFETCH/DISCARD)")
            
            # Strategy 1: Return unscored Sketchfab models with embed URLs
            unscored_sketchfab = [m for m in models[:5] if m.type == "sketchfab" and m.embed_url]
            if unscored_sketchfab:
                logger.info(f"📋 Returning {len(unscored_sketchfab)} unscored Sketchfab models (embed-only, no scoring)")
                primary = unscored_sketchfab[0]
                approved_models = unscored_sketchfab
                status = "completed"
            else:
                # Strategy 2: Generate procedural model with Blender MCP
                logger.info("🔧 Triggering Blender MCP for procedural generation...")
                
                try:
                    # Connect to Blender MCP if not already connected
                    if not blender_client.is_connected:
                        logger.info("Connecting to Blender MCP server...")
                        await blender_client.connect()
                    
                    # Generate model using Blender MCP
                    blender_prompt = (
                        f"Create a 3D model of: {prompt}. "
                        f"Style: {attributes.style}. "
                        f"Make it detailed and export as GLB."
                    )
                    
                    conversation_id = str(uuid.uuid4())
                    logger.info(f"[Blender] Sending prompt: {blender_prompt}")
                    
                    result = await blender_client.chat(
                        user_message=blender_prompt,
                        conversation_id=conversation_id,
                    )
                    
                    glb_path = result.get("glb_path")
                    
                    if glb_path and Path(glb_path).exists():
                        logger.info(f"✅ Blender generated model: {glb_path}")
                        
                        # Create ModelInfo for the Blender-generated model
                        primary = ModelInfo(
                            type="glb",
                            url=f"file://{glb_path}",
                            title=f"Procedural: {prompt}",
                            source_url="blender://procedural",
                            source_domain="blender-mcp",
                            description=f"Procedurally generated by Blender MCP",
                            tags=attributes.keywords,
                        )
                        approved_models = [primary]
                        status = "completed"
                    else:
                        logger.error("❌ Blender MCP failed to generate model")
                        status = "no_model"
                        
                except Exception as e:
                    logger.error(f"❌ Blender fallback failed: {e}", exc_info=True)
                    status = "no_model"
        else:
            status = "completed"

        update = {
            "attributes": attributes.model_dump(),
            "primary_model": primary.model_dump() if primary else None,
            "all_models": [m.model_dump() for m in approved_models],
            "scored_results": [
                {
                    "model": item["model"].model_dump(),  # Convert ModelInfo to dict
                    "score": item["score"],
                    "decision": item["decision"],
                    "is_labelled": item["is_labelled"],
                    "semantic_score": item["semantic_score"],
                    "geometric_score": item["geometric_score"],
                }
                for item in scored_models
            ],
            "status": status,
        }
        await db.searches.update_one({"id": record.id}, {"$set": update})

        return SearchResponse(
            id=record.id,
            original_prompt=prompt,
            attributes=attributes,
            primary_model=primary,
            all_models=approved_models,
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

@api_router.get("/download")
async def download_model(url: str, background_tasks: BackgroundTasks):
    from app.sketchfab_fetcher import fetch_model
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / '.env', override=True)
    sketchfab_api_key = os.environ.get('SKETCHFAB_API_KEY', '')
    
    result = await fetch_model(url, sketchfab_api_key)
    
    if not result.local_path or not os.path.exists(result.local_path):
        raise HTTPException(status_code=400, detail=f"Could not download model: {result.error}")
        
    filename = os.path.basename(result.local_path)
    
    return FileResponse(
        path=result.local_path, 
        filename=filename, 
        media_type='application/octet-stream'
    )

@api_router.post("/mesh/rename")
async def mesh_rename(file: UploadFile = File(...), hint: str = Form(default="")):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    try:
        data = await file.read()
        result = await rename_meshes_from_file(file.filename, data, hint=hint or "")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Mesh rename error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
# ─── Model Scoring Routes ─────────────────────────────────────────────────────

@api_router.post("/score", response_model=ModelScoreResponse)
async def score_single_model(request: ModelScoreRequest):
    """
    Score a single 3D model using the composite scorer.
    Supports both direct GLB/GLTF URLs and Sketchfab URLs with download API.
    """
    try:
        # Get Sketchfab API key from environment
        sketchfab_api_key = os.environ.get('SKETCHFAB_API_KEY', '')
        
        # Fetch the model using the comprehensive fetcher
        fetch_result = await fetch_model(
            url=request.model_url,
            sketchfab_api_key=sketchfab_api_key
        )
        
        # Check if model was successfully fetched
        if not fetch_result.is_scorable or not fetch_result.local_path:
            raise HTTPException(
                status_code=400,
                detail=f"Model could not be downloaded: {fetch_result.status.value}"
            )
        
        # Prepare metadata for scorer
        metadata = request.metadata or {}
        if fetch_result.metadata:
            # Merge metadata from Sketchfab API
            metadata = {
                "title": fetch_result.metadata.title or metadata.get("title", ""),
                "description": fetch_result.metadata.description or metadata.get("description", ""),
                "tags": fetch_result.metadata.tags or metadata.get("tags", []),
            }
        else:
            # Use provided metadata
            metadata = {
                "title": metadata.get("title", ""),
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
            }
        
        # Run scorer in thread pool (CPU-intensive)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            score_model,
            str(fetch_result.local_path),
            request.prompt,
            metadata
        )
        
        # Clean up temp file (fetcher may have cached it, but respect our own cleanup)
        # Note: fetcher may cache files, so we don't always want to delete
        # For now, let the fetcher handle caching
        
        return ModelScoreResponse(
            final_score=result.final_score,
            is_labelled=result.is_labelled,
            decision=result.decision,
            semantic_score=result.semantic.combined,
            geometric_score=result.geometric.combined,
            details={
                "semantic": {
                    "clip_score": result.semantic.clip_score,
                    "metadata_score": result.semantic.metadata_score,
                },
                "geometric": {
                    "polygon_density": result.geometric.polygon_density,
                    "mesh_node_count": result.geometric.mesh_node_count,
                    "node_hierarchy_depth": result.geometric.node_hierarchy_depth,
                    "uv_coverage": result.geometric.uv_coverage,
                    "material_diversity": result.geometric.material_diversity,
                }
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scoring error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scoring failed: {str(e)}")


@api_router.post("/score/batch", response_model=BatchScoreResponse)
async def score_search_results(request: BatchScoreRequest):
    """
    Score all models from a search result and rank them.
    Supports direct GLB/GLTF URLs and Sketchfab download API.
    """
    # Get search record
    record = await db.searches.find_one({"id": request.search_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Search not found")
    
    all_models = record.get("all_models", [])
    if not all_models:
        raise HTTPException(status_code=404, detail="No models found in this search")
    
    # Get Sketchfab API key
    sketchfab_api_key = os.environ.get('SKETCHFAB_API_KEY', '')
    
    logger.info(f"Attempting to score {len(all_models)} models from search {request.search_id}")
    
    try:
        # Prepare candidates for batch scoring
        candidates = []
        
        for model in all_models:
            model_url = model.get("url")
            if not model_url:
                continue
                
            try:
                # Use fetcher to download model (supports both direct URLs and Sketchfab)
                fetch_result = await fetch_model(
                    url=model_url,
                    sketchfab_api_key=sketchfab_api_key
                )
                
                # Only add if successfully fetched
                if fetch_result.is_scorable and fetch_result.local_path:
                    metadata = {}
                    if fetch_result.metadata:
                        metadata = {
                            "title": fetch_result.metadata.title,
                            "description": fetch_result.metadata.description,
                            "tags": fetch_result.metadata.tags,
                        }
                    else:
                        metadata = {
                            "title": model.get("title", ""),
                            "description": model.get("description", ""),
                            "tags": model.get("tags", []),
                        }
                    
                    candidates.append({
                        "model_path": str(fetch_result.local_path),
                        "metadata": metadata,
                        "original_model_info": model
                    })
                else:
                    logger.warning(f"Skipped {model_url}: {fetch_result.status.value}")
                    
            except Exception as e:
                logger.warning(f"Failed to fetch {model_url}: {e}")
                continue
        
        if not candidates:
            raise HTTPException(
                status_code=400,
                detail="No models could be downloaded for scoring"
            )
        
        logger.info(f"Successfully fetched {len(candidates)} models, starting batch scoring")
        
        # Run batch scoring in thread pool
        loop = asyncio.get_event_loop()
        ranked = await loop.run_in_executor(
            None,
            score_candidates,
            candidates,
            request.prompt
        )
        
        # Merge scores with original model info
        for rank_result in ranked:
            original_info = next(
                (c["original_model_info"] for c in candidates 
                 if c["model_path"] == rank_result.get("model_path")),
                None
            )
            if original_info:
                rank_result["model_info"] = original_info
        
        best_model = ranked[0] if ranked else None
        
        # Update search record with scores
        await db.searches.update_one(
            {"id": request.search_id},
            {"$set": {"scored_models": ranked, "best_scored_model": best_model}}
        )
        
        return BatchScoreResponse(
            search_id=request.search_id,
            ranked_models=ranked,
            best_model=best_model
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch scoring error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch scoring failed: {str(e)}")


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