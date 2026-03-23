from fastapi import FastAPI, APIRouter, HTTPException, Query, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from groq import Groq
from tavily import TavilyClient
import sys
import os
import logging
import re
import json
import asyncio
import shutil
import uuid
import mimetypes
from pathlib import Path
from dotenv import load_dotenv
import httpx
import tempfile
import aiofiles
from difflib import SequenceMatcher

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
from app.conversation_service import (
    ChatRequest,
    ChatResponse,
    handle_model_chat,
    _select_model_result,
)

ROOT_DIR = Path(__file__).parent.parent.parent.parent  # Sankalp/
load_dotenv(ROOT_DIR / '.env')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo_url = os.environ['MONGO_URL']
db_client = AsyncIOMotorClient(mongo_url)
db = db_client[os.environ['DB_NAME']]
fs = AsyncIOMotorGridFSBucket(db)
ENABLE_GRIDFS_CACHE = os.environ.get("ENABLE_GRIDFS_CACHE", "").strip().lower() in {"1", "true", "yes", "on"}

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

# per segment narration helper

class SegmentNarrationResponse(BaseModel):
    segments: List[Dict[str, str]]  # [{"name": "...", "description": "..."}]

@api_router.get("/narration/{search_id}", response_model=SegmentNarrationResponse)
async def get_segment_narration(search_id: str, model_url: str = Query(default="")):
    record = await db.searches.find_one({"id": search_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Search not found")

    requested_model, selected_result = _select_model_result(record, (model_url or "").strip())
    node_names = selected_result.get("node_names", []) if selected_result else []

    if not node_names:
        raise HTTPException(status_code=404, detail="No segments found")

    # Filter out rig bones, numbered variants, and technical nodes
    def is_meaningful(name: str) -> bool:
        n = name.lower()
        # Skip Sketchfab boilerplate
        if any(skip in n for skip in ["sketchfab", "gltf", "rootnode", "root", "object_", "gltf_created"]):
            return False
        # Skip numbered bone variants like f_pinky.02.L, brow.T.R.001
        if re.search(r'\.\d+', name):
            return False
        # Skip pure index suffixes like spine_131, jaw_11
        if re.search(r'_\d+$', name):
            return False
        return True

    meaningful = [n for n in node_names if is_meaningful(n)]

    # If filtering too aggressive, fall back to first 30 original names
    if len(meaningful) < 3:
        meaningful = node_names[:30]

    # Cap at 30 to stay within token budget
    node_names = meaningful[:30]

    prompt_context = record.get("original_prompt", "3D model")
    attributes = record.get("attributes") or {}
    object_type = attributes.get("object_type", "object")
    model_title = requested_model.get("title") or "Unknown model"
    model_description = requested_model.get("description") or ""

    # Ask LLM for one description per segment
    def _call():
        return groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    f"You are an educational narrator for a 3D model viewer.\n"
                    f"The full model is: {prompt_context} (type: {object_type})\n"
                    f"Model title: {model_title}\n"
                    f"Model description: {model_description}\n"
                    f"For each segment name below, write a short spoken explanation suitable for narration.\n"
                    f"Adapt the explanation to the kind of model:\n"
                    f"- For living things, explain the part's role, structure, or biological function.\n"
                    f"- For machines, vehicles, tools, or devices, explain the component's purpose, placement, or how it contributes to operation.\n"
                    f"- For buildings, furniture, or crafted objects, explain the structural, functional, or decorative role of the part.\n"
                    f"- If the name is ambiguous, give the most likely plain-English explanation based on the full model context.\n"
                    f"Rules:\n"
                    f"- Focus on what the part is, what it does, where it is, or why it matters.\n"
                    f"- Use natural, listener-friendly language for a general audience.\n"
                    f"- Keep each description to 1-2 sentences.\n"
                    f"- Do NOT mention 3D models, segments, nodes, meshes, animations, rendering, geometry, or technical pipeline terms.\n"
                    f"- Do NOT say phrases like 'this segment', 'this part of the model', or 'in the 3D view'.\n"
                    f"- If a segment name is unclear, avoid inventing very specific facts; stay broadly accurate and modest.\n"
                    f"Return ONLY valid JSON: {{\"segments\": ["
                    f"{{\"name\": \"segment_name\", \"description\": \"...\"}}]}}\n\n"
                    f"Segments: {json.dumps(node_names)}"
                )
            }],
            response_format={"type": "json_object"},
            max_tokens=3000,
            temperature=0.4,
        )

    loop = asyncio.get_event_loop()
    try:
        completion = await loop.run_in_executor(None, _call)
        data = json.loads(completion.choices[0].message.content)
        segments = data.get("segments", [])
        # Fallback if LLM returns wrong shape
        if not segments:
            segments = [{"name": n, "description": f"This is the {n.replace('_', ' ')}."} 
                       for n in node_names]
    except Exception as e:
        logger.error(f"Narration generation failed: {e}")
        segments = [{"name": n, "description": f"This is the {n.replace('_', ' ')}."} 
                   for n in node_names]

    return SegmentNarrationResponse(segments=segments)


# ─── Segmentation helper ──────────────────────────────────────────────────────

APP_DIR    = Path(__file__).parent          # …/app/
INPUT_DIR  = APP_DIR / "input"
OUTPUT_DIR = APP_DIR / "output"

# copy to ouput helper
def _copy_to_output(local_path: str) -> Optional[str]:
    """Copy a downloaded model to app/output/ and return the output path."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    src = Path(local_path)
    dst = OUTPUT_DIR / src.name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return str(dst)


def _rewrite_glb_with_names(data: bytes, names_dict: dict) -> bytes:
    """Rewrite GLB JSON chunk to rename mesh nodes and their referencing nodes."""
    if len(data) < 20 or data[0:4] != b"glTF":
        raise ValueError("Not a valid GLB")

    offset = 12
    json_start = json_end = json_chunk_start = None
    while offset + 8 <= len(data):
        chunk_length = int.from_bytes(data[offset:offset+4], "little")
        chunk_type   = int.from_bytes(data[offset+4:offset+8], "little")
        data_start   = offset + 8
        data_end     = data_start + chunk_length
        if chunk_type == 0x4E4F534A:  # JSON chunk
            json_chunk_start = offset
            json_start = data_start
            json_end   = data_end
            break
        offset = data_end

    if json_start is None:
        raise ValueError("No JSON chunk found")

    gltf = json.loads(data[json_start:json_end].decode("utf-8"))

    for idx_str, new_name in names_dict.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        meshes = gltf.get("meshes") or []
        if 0 <= idx < len(meshes):
            meshes[idx]["name"] = new_name

    for node in (gltf.get("nodes") or []):
        mesh_idx = node.get("mesh")
        if isinstance(mesh_idx, int) and str(mesh_idx) in names_dict:
            node["name"] = names_dict[str(mesh_idx)]

    new_json = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(new_json) % 4) % 4
    new_json += b" " * pad

    new_chunk  = len(new_json).to_bytes(4, "little") + (0x4E4F534A).to_bytes(4, "little") + new_json
    rest       = data[json_chunk_start + 8 + (json_end - json_start):]
    new_total  = 12 + len(new_chunk) + len(rest)
    header     = b"glTF" + (2).to_bytes(4, "little") + new_total.to_bytes(4, "little")
    return header + new_chunk + rest


def _build_rename_context(
    prompt: str = "",
    attributes: Optional["SearchAttributes"] = None,
    model: Optional["ModelInfo"] = None,
) -> Dict[str, Any]:
    return {
        "prompt": prompt or "",
        "object_type": attributes.object_type if attributes else "",
        "keywords": list(attributes.keywords) if attributes and attributes.keywords else [],
        "title": model.title if model else "",
        "description": model.description if model else "",
        "tags": list(model.tags) if model and model.tags else [],
    }


async def rename_segmented_model(
    segmented_path: str,
    hint: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[str], list[str]]:
    """
    Rename segments in a segmented GLB to semantic names using LLM.
    
    Takes a segmented GLB (with Segment_00, Segment_01, etc.) and renames
    the mesh nodes to meaningful semantic names (head, left_arm, right_leg, etc.).
    
    Returns:
      (renamed_path: str or None, semantic_names: list[str])
        - renamed_path: Path to the output GLB (currently same as input, updates pending)
        - semantic_names: List of semantic names generated by the renamer
    """
    try:
        segmented_file = Path(segmented_path)
        if not segmented_file.exists():
            logger.error(f"[rename] Segmented file not found: {segmented_path}")
            return None, []
        
        # Read the segmented GLB
        with open(segmented_file, "rb") as f:
            glb_data = f.read()
        
        logger.info(f"[rename] Renaming segments in {segmented_file.name} with hint: '{hint}'")
        
        # Call mesh_renamer to get semantic names
        rename_result = await rename_meshes_from_file(
            filename=segmented_file.name,
            data=glb_data,
            hint=hint or "object",
            context=context,
        )
        
        if not rename_result.get("names"):
            logger.warning(f"[rename] No names returned from renamer")
            return None, []
        
        # Extract semantic names in order of mesh index
        names_dict = rename_result.get("names", {})
        semantic_names = [names_dict.get(str(i), f"Segment_{i:02d}") 
                         for i in range(len(names_dict))]
        
        logger.info(f"[rename] Generated semantic names: {semantic_names}")
        
        # Rewrite the GLB file with new node names
        try:
            rewritten = _rewrite_glb_with_names(glb_data, names_dict)
            out_path = OUTPUT_DIR / segmented_file.name
            with open(out_path, "wb") as f:
                f.write(rewritten)
            logger.info(f"[rename] ✅ GLB rewritten with semantic names -> {out_path}")
            return str(out_path), semantic_names
        except Exception as e:
            logger.error(f"[rename] GLB rewrite failed: {e}", exc_info=True)
            return segmented_path, semantic_names  # partial success — names generated but file unchanged
        
    except Exception as e:
        logger.error(f"[rename] Failed to rename segmented model: {e}", exc_info=True)
        return None, []


async def segment_model(local_path: str) -> Optional[str]:
    """
    Run segment_glb.py on a GLB that has no named mesh nodes
    (single-mesh / unlabelled model).

    Steps:
      1. Copy the file into app/input/<filename>
      2. Run:  python segment_glb.py <filename>
         The script reads from app/input/ and writes to app/output/
      3. Return the path to the segmented GLB, or None on failure.
    """
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    src      = Path(local_path)
    filename = src.name
    dst      = INPUT_DIR / filename

    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    logger.info(f"[segment] Copied {filename} -> {dst}")

    script = APP_DIR / "segment_mesh.py"
    if not script.exists():
        logger.error(f"[segment] segment_glb.py not found at {script}")
        return None

    try:
        import subprocess
        def _run():
            return subprocess.run(
                [sys.executable, str(script), filename,
                 "--max-segments", "20",
                 "--strategy", "auto"],
                cwd=str(APP_DIR),
                capture_output=True,
                text=True
            )
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(None, _run)
        log_output = proc.stdout + proc.stderr
        logger.info(f"[segment] Output:\n{log_output}")

        if proc.returncode != 0:
            logger.error(f"[segment] segment_mesh.py exited with code {proc.returncode}")
            return None

        out_path = OUTPUT_DIR / filename
        if out_path.exists():
            logger.info(f"[segment] Segmented model saved -> {out_path}")
            return str(out_path)

        logger.error(f"[segment] Expected output not found: {out_path}")
        return None

    except Exception as e:
        logger.error(f"[segment] Exception: {e}", exc_info=True)
        return None

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


class MeshFromPromptRequest(BaseModel):
    prompt: str


class MeshFromPromptResponse(BaseModel):
    prompt: str
    selected_image_url: str
    selected_image_bytes: int
    output_filename: str
    output_url: str
    generator_endpoint: str

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


def _normalize_search_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _tokenize_search_text(value: str) -> set[str]:
    return set(_normalize_search_text(value).split())


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _search_signature(prompt: str, attributes: Optional[SearchAttributes]) -> Dict[str, Any]:
    attributes = attributes or SearchAttributes(
        object_type="object",
        style="realistic",
        keywords=[],
        refined_query=prompt,
        confidence=1.0,
    )
    keywords = list(attributes.keywords or [])
    combined = " ".join(
        part for part in [
            prompt,
            attributes.object_type,
            attributes.style,
            attributes.refined_query,
            " ".join(keywords),
        ] if part
    )
    return {
        "prompt_norm": _normalize_search_text(prompt),
        "refined_norm": _normalize_search_text(attributes.refined_query),
        "object_type_norm": _normalize_search_text(attributes.object_type),
        "style_norm": _normalize_search_text(attributes.style),
        "keywords_norm": [_normalize_search_text(k) for k in keywords if _normalize_search_text(k)],
        "token_set": _tokenize_search_text(combined),
    }


def _record_attributes(record: dict) -> Optional[SearchAttributes]:
    raw = record.get("attributes")
    if not raw:
        return None
    try:
        return SearchAttributes(**raw)
    except Exception:
        return None


async def _cached_record_is_servable(record: dict) -> bool:
    if not record:
        return False

    if record.get("status") == "no_model":
        return True

    primary = record.get("primary_model") or {}
    model_url = str(primary.get("url") or primary.get("embed_url") or "").strip()
    if not model_url:
        return False

    if model_url.startswith("/api/output/"):
        filename = model_url.replace("/api/output/", "", 1)
        if (OUTPUT_DIR / filename).exists():
            return True
        if ENABLE_GRIDFS_CACHE:
            try:
                cursor = fs.find({"filename": filename})
                docs = await cursor.to_list(1)
                if docs:
                    return True
            except Exception as exc:
                logger.warning("[cache] GridFS lookup failed for %s: %s", filename, exc)
        logger.warning("[cache] Skipping stale cached record; missing local model file: %s", filename)
        return False

    return True


def _semantic_cache_score(
    prompt: str,
    attributes: SearchAttributes,
    record: dict,
) -> float:
    candidate_prompt = str(record.get("original_prompt") or "")
    candidate_attrs = _record_attributes(record)
    candidate_sig = _search_signature(candidate_prompt, candidate_attrs)
    query_sig = _search_signature(prompt, attributes)

    exact_prompt = 1.0 if query_sig["prompt_norm"] and query_sig["prompt_norm"] == candidate_sig["prompt_norm"] else 0.0
    exact_refined = 1.0 if query_sig["refined_norm"] and query_sig["refined_norm"] == candidate_sig["refined_norm"] else 0.0
    object_match = 1.0 if query_sig["object_type_norm"] and query_sig["object_type_norm"] == candidate_sig["object_type_norm"] else 0.0
    style_match = 1.0 if query_sig["style_norm"] and query_sig["style_norm"] == candidate_sig["style_norm"] else 0.0

    keyword_overlap = _jaccard_similarity(set(query_sig["keywords_norm"]), set(candidate_sig["keywords_norm"]))
    token_overlap = _jaccard_similarity(query_sig["token_set"], candidate_sig["token_set"])
    refined_ratio = SequenceMatcher(None, query_sig["refined_norm"], candidate_sig["refined_norm"]).ratio() if query_sig["refined_norm"] and candidate_sig["refined_norm"] else 0.0
    prompt_ratio = SequenceMatcher(None, query_sig["prompt_norm"], candidate_sig["prompt_norm"]).ratio() if query_sig["prompt_norm"] and candidate_sig["prompt_norm"] else 0.0

    return (
        0.30 * exact_prompt +
        0.20 * exact_refined +
        0.15 * object_match +
        0.05 * style_match +
        0.15 * keyword_overlap +
        0.10 * token_overlap +
        0.03 * refined_ratio +
        0.02 * prompt_ratio
    )


async def _find_semantic_cached_search(prompt: str, attributes: SearchAttributes) -> Optional[dict]:
    cursor = (
        db.searches
        .find(
            {"status": {"$in": ["completed", "no_model"]}},
            {"_id": 0},
        )
        .sort("created_at", -1)
        .limit(200)
    )
    candidates = await cursor.to_list(length=200)
    if not candidates:
        return None

    best_record = None
    best_score = 0.0
    for candidate in candidates:
        score = _semantic_cache_score(prompt, attributes, candidate)
        if score > best_score:
            best_score = score
            best_record = candidate

    if not best_record:
        return None

    threshold = 0.72
    if best_score >= threshold and await _cached_record_is_servable(best_record):
        logger.info(
            "[cache] Semantic cache hit for '%s' -> '%s' (score=%.3f)",
            prompt,
            best_record.get("original_prompt", ""),
            best_score,
        )
        return best_record

    logger.info("[cache] No semantic cache hit for '%s' (best score=%.3f)", prompt, best_score)
    return None

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
                max_results=25,
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


def _extract_image_urls(search_result: Dict[str, Any]) -> List[str]:
    urls: List[str] = []

    images = search_result.get("images") or []
    for image_entry in images:
        if isinstance(image_entry, str) and image_entry.startswith("http"):
            urls.append(image_entry)
        elif isinstance(image_entry, dict):
            candidate = image_entry.get("url") or image_entry.get("image_url")
            if isinstance(candidate, str) and candidate.startswith("http"):
                urls.append(candidate)

    for item in search_result.get("results", []):
        if not isinstance(item, dict):
            continue
        for key in ("image", "image_url", "thumbnail"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.startswith("http"):
                urls.append(candidate)

    deduped: List[str] = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


async def _download_high_quality_image_for_prompt(prompt: str) -> tuple[bytes, str, str]:
    loop = asyncio.get_event_loop()

    search_queries = [
        f"{prompt} high quality 3d render 8k",
        f"{prompt} high quality 3d model reference image",
        f"{prompt} high resolution image",
    ]

    def _tavily_search(query: str) -> Dict[str, Any]:
        return tavily_client.search(
            query=query,
            search_depth="advanced",
            max_results=8,
            include_answer=False,
            include_images=True,
        )

    candidate_urls: List[str] = []
    for query in search_queries:
        try:
            result = await loop.run_in_executor(None, _tavily_search, query)
            extracted = _extract_image_urls(result)
            candidate_urls.extend(extracted)
            if extracted:
                # Prefer 3D-biased queries first; stop early once candidates are found.
                break
        except Exception as exc:
            logger.warning("Tavily image search failed for query '%s': %s", query, exc)

    if not candidate_urls:
        raise HTTPException(status_code=502, detail="No candidate images found via Tavily")

    best_bytes: Optional[bytes] = None
    best_url: Optional[str] = None
    best_mime: str = "application/octet-stream"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for image_url in candidate_urls[:12]:
            try:
                resp = await client.get(image_url)
                if resp.status_code != 200:
                    continue

                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    continue

                payload = resp.content
                if len(payload) < 100_000:
                    continue

                if best_bytes is None or len(payload) > len(best_bytes):
                    best_bytes = payload
                    best_url = image_url
                    best_mime = content_type
            except Exception:
                continue

    if not best_bytes or not best_url:
        raise HTTPException(status_code=502, detail="Could not download a suitable high-quality image")

    return best_bytes, best_url, best_mime


async def _generate_mesh_file_from_prompt(prompt: str) -> tuple[Path, str, int, str]:
    """
    Generate a GLB from prompt via the mesh generation API and persist it in OUTPUT_DIR.

    Returns:
        (output_path, selected_image_url, selected_image_bytes, generator_endpoint)
    """
    image_bytes, image_url, image_mime = await _download_high_quality_image_for_prompt(prompt)

    mesh_api_url = os.environ.get("MESH_GENERATOR_URL", "http://98.70.40.74:8000/generate-mesh")
    extension = mimetypes.guess_extension(image_mime) or ".jpg"
    upload_name = f"source_{uuid.uuid4().hex}{extension}"

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        try:
            response = await client.post(
                mesh_api_url,
                files={"file": (upload_name, image_bytes, image_mime)},
            )
        except Exception as exc:
            logger.error("Mesh generation API request failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail="Mesh generation API is unreachable") from exc

    if response.status_code != 200:
        detail = response.text[:500]
        raise HTTPException(
            status_code=502,
            detail=f"Mesh generation API failed with {response.status_code}: {detail}",
        )

    output_filename = f"model_{uuid.uuid4().hex}.glb"
    output_path = OUTPUT_DIR / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(output_path, "wb") as f:
        await f.write(response.content)

    return output_path, image_url, len(image_bytes), mesh_api_url


async def _segment_and_rename_generated_model(
    glb_path: Path,
    hint: str,
    context: Optional[Dict[str, Any]] = None,
) -> tuple[Path, list[str]]:
    """Apply the same segment -> rename flow used for approved unlabelled models."""
    segmented_path = await segment_model(str(glb_path))
    if not segmented_path:
        logger.warning("[fallback] Segmentation failed for generated mesh; using raw generated model")
        return glb_path, []

    renamed_path, semantic_names = await rename_segmented_model(segmented_path, hint=hint, context=context)
    if renamed_path:
        return Path(renamed_path), semantic_names

    logger.warning("[fallback] Renaming failed for generated mesh; using segmented output")
    return Path(segmented_path), []

# ─── Model Evaluation Helper ──────────────────────────────────────────────────

async def _evaluate_single_candidate(
    model: ModelInfo,
    idx: int,
    total: int,
    prompt: str,
    attributes: "SearchAttributes",
    sketchfab_api_key: str,
) -> tuple:
    """
    Fetch, score, and post-process a single candidate model.
    Returns (updated_model, scored_dict, summary_dict) on success,
    or (None, None, None) on failure / PENDING.
    """
    model_url = model.url
    if not model_url:
        return None, None, None

    try:
        logger.info(f"[{idx}/{total}] Fetching {model_url}")
        fetch_result = await fetch_model(url=model_url, sketchfab_api_key=sketchfab_api_key)
        logger.info(
            f"[{idx}/{total}] Fetch status: {fetch_result.status.value}, "
            f"is_scorable: {fetch_result.is_scorable}, local_path: {fetch_result.local_path}"
        )

        if not (fetch_result.is_scorable and fetch_result.local_path):
            logger.warning(f"[{idx}/{total}] ⚠️  Model NOT SCORABLE ({fetch_result.status.value})")
            return None, None, None

        metadata = fetch_result.metadata if fetch_result.metadata else {
            "title": model.title,
            "description": model.description or "",
            "tags": model.tags,
        }
        logger.info(f"[{idx}/{total}] Scoring model...")
        loop = asyncio.get_event_loop()
        score_result = await loop.run_in_executor(
            None, score_model, str(fetch_result.local_path), prompt, metadata
        )

        logger.info(f"\n{'='*70}")
        logger.info(f"[{idx}/{total}] SCORING RESULT: {model.title[:50]}")
        logger.info(f"{'='*70}")
        logger.info(f"  Semantic Score:  {score_result.semantic.combined:.4f}")
        logger.info(f"  Geometric Score: {score_result.geometric.combined:.4f}")
        logger.info(f"  Final Score:     {score_result.final_score:.4f}")
        logger.info(f"  Decision:        {score_result.decision}")
        logger.info(f"-" * 70)

        summary_dict = {
            "title": model.title[:40],
            "score": score_result.final_score,
            "semantic": score_result.semantic.combined,
            "geometric": score_result.geometric.combined,
            "labelled": score_result.is_labelled,
            "decision": score_result.decision,
        }

        scored_dict = None

        if score_result.decision == "SEGMENT_MESH":
            segmented_path = await segment_model(str(fetch_result.local_path))
            if segmented_path:
                rename_context = _build_rename_context(prompt=prompt, attributes=attributes, model=model)
                renamed_path, semantic_names = await rename_segmented_model(
                    segmented_path,
                    hint=", ".join(attributes.keywords) if attributes.keywords else prompt,
                    context=rename_context,
                )
                final_path = renamed_path if renamed_path else segmented_path
                semantic_names = semantic_names if renamed_path else []
                model = ModelInfo(
                    **{**model.model_dump(),
                       "url": f"/api/output/{Path(final_path).name}",
                       "description": (model.description or "") + " [segmented + renamed]"}
                )
                scored_dict = {
                    "model": model, "score": score_result.final_score,
                    "decision": score_result.decision, "is_labelled": True,
                    "node_names": semantic_names,
                    "semantic_score": score_result.semantic.combined,
                    "geometric_score": score_result.geometric.combined,
                }

        elif score_result.decision == "USE":
            final_path = _copy_to_output(str(fetch_result.local_path))
            node_names = getattr(score_result, "node_names", []) or []
            model = ModelInfo(
                **{**model.model_dump(), "type": "glb",
                   "url": f"/api/output/{Path(final_path).name}",
                   "description": (model.description or "") + " [used directly]"}
            )
            scored_dict = {
                "model": model, "score": score_result.final_score,
                "decision": score_result.decision,
                "is_labelled": score_result.is_labelled,
                "node_names": node_names,
                "semantic_score": score_result.semantic.combined,
                "geometric_score": score_result.geometric.combined,
            }

        elif score_result.decision == "RENAME":
            rename_context = _build_rename_context(prompt=prompt, attributes=attributes, model=model)
            renamed_path, semantic_names = await rename_segmented_model(
                str(fetch_result.local_path),
                hint=", ".join(attributes.keywords) if attributes.keywords else prompt,
                context=rename_context,
            )
            if renamed_path:
                final_path = renamed_path
            else:
                final_path = _copy_to_output(str(fetch_result.local_path))
                semantic_names = getattr(score_result, "node_names", []) or []
            model = ModelInfo(
                **{**model.model_dump(), "type": "glb",
                   "url": f"/api/output/{Path(final_path).name}",
                   "description": (model.description or "") + " [labels refined]"}
            )
            scored_dict = {
                "model": model, "score": score_result.final_score,
                "decision": score_result.decision, "is_labelled": True,
                "node_names": semantic_names,
                "semantic_score": score_result.semantic.combined,
                "geometric_score": score_result.geometric.combined,
            }

        # PENDING or unsupported decision → scored_dict stays None
        return model, scored_dict, summary_dict

    except Exception as e:
        logger.error(f"[{idx}/{total}] ❌ EXCEPTION: {type(e).__name__}: {e}", exc_info=True)
        return None, None, None


async def _upload_to_gridfs(approved_models):
    """Upload approved model GLBs to GridFS."""
    if not ENABLE_GRIDFS_CACHE:
        return
    for item in approved_models:
        model_url = item.url
        if model_url and model_url.startswith("/api/output/"):
            filename = model_url.replace("/api/output/", "")
            local_path = OUTPUT_DIR / filename
            if local_path.exists():
                try:
                    cursor = fs.find({"filename": filename})
                    docs = await cursor.to_list(1)
                    if not docs:
                        with open(local_path, "rb") as file_data:
                            await fs.upload_from_stream(filename, file_data)
                            logger.info(f"Uploaded {filename} to GridFS")
                except Exception as e:
                    logger.error(f"Failed to upload {filename} to GridFS: {e}")


async def _process_remaining_models_bg(
    search_id: str,
    remaining_models: list,
    prompt: str,
    attributes,
    sketchfab_api_key: str,
    start_attempt: int,
    existing_scored: list,
):
    """Background task: score remaining models and update DB record."""
    MAX_ATTEMPTS = 15
    TARGET_SCORED = 25
    scored_models = list(existing_scored)
    scored_count = len(scored_models)
    attempts = start_attempt

    for model in remaining_models:
        if scored_count >= TARGET_SCORED or attempts >= MAX_ATTEMPTS:
            break
        attempts += 1
        _, scored_dict, _ = await _evaluate_single_candidate(
            model, attempts, min(len(remaining_models) + start_attempt, MAX_ATTEMPTS),
            prompt, attributes, sketchfab_api_key,
        )
        if scored_dict:
            scored_models.append(scored_dict)
            scored_count += 1

    scored_models.sort(key=lambda x: x["score"], reverse=True)
    approved = [item["model"] for item in scored_models]
    primary = approved[0] if approved else None

    await _upload_to_gridfs(approved)

    update = {
        "attributes": attributes.model_dump(),
        "primary_model": primary.model_dump() if primary else None,
        "all_models": [m.model_dump() for m in approved],
        "scored_results": [
            {
                "model": item["model"].model_dump(),
                "score": item["score"],
                "decision": item["decision"],
                "is_labelled": item["is_labelled"],
                "node_names": item.get("node_names", []),
                "semantic_score": item["semantic_score"],
                "geometric_score": item["geometric_score"],
            }
            for item in scored_models
        ],
        "status": "completed",
    }
    await db.searches.update_one({"id": search_id}, {"$set": update})
    logger.info(f"[BG] Finished processing remaining models for search {search_id}: {scored_count} total approved")


# ─── Routes ───────────────────────────────────────────────────────────────────

@api_router.post("/search", response_model=SearchResponse)
async def search_models(request: SearchRequest, background_tasks: BackgroundTasks, http_request: Request):
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    attributes = await refine_prompt_with_groq(prompt)

    # DB-first reuse: exact prompt/refined-query match first, then semantic reuse.
    cached = await db.searches.find_one(
        {
            "$or": [
                {"original_prompt": {"$regex": f"^{re.escape(prompt)}$", "$options": "i"}},
                {"attributes.refined_query": attributes.refined_query},
            ],
            "status": {"$in": ["completed", "no_model"]},
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if cached and await _cached_record_is_servable(cached):
        logger.info("[cache] Exact cache hit: %s or %s", prompt, attributes.refined_query)
        return record_to_response(cached)

    semantic_cached = await _find_semantic_cached_search(prompt, attributes)
    if semantic_cached:
        return record_to_response(semantic_cached)

    record = SearchRecord(original_prompt=prompt, attributes=attributes)
    doc = record.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.searches.insert_one(doc)

    try:
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
        MAX_ATTEMPTS  = 15
        attempts      = 0

        # ─── FOREGROUND: Find the FIRST approved model and return immediately ───
        for model in models[:MAX_ATTEMPTS]:
            attempts += 1
            _, scored_dict, _ = await _evaluate_single_candidate(
                model, attempts, min(len(models), MAX_ATTEMPTS),
                prompt, attributes, sketchfab_api_key,
            )
            if scored_dict:
                scored_models.append(scored_dict)
                break  # Got one → return it now, process rest in background

        approved_models = [item["model"] for item in scored_models]
        primary = approved_models[0] if approved_models else None

        # ─── FALLBACK: Generate model if none approved ───
        if not approved_models:
            logger.warning("⚠️  No approved model available. Triggering mesh-generation fallback.")
            try:
                generated_path, image_url, image_bytes, generator_endpoint = await _generate_mesh_file_from_prompt(prompt)
                logger.info(f"[fallback] Generated base mesh: {generated_path}")

                rename_hint = ", ".join(attributes.keywords) if attributes.keywords else prompt
                rename_context = _build_rename_context(
                    prompt=prompt,
                    attributes=attributes,
                    model=ModelInfo(
                        type="glb",
                        title=f"Generated: {prompt}",
                        source_url="",
                        source_domain="mesh-generator",
                        description=f"Generated from prompt: {prompt}",
                        tags=attributes.keywords,
                    ),
                )
                final_path, semantic_names = await _segment_and_rename_generated_model(
                    generated_path, hint=rename_hint, context=rename_context,
                )

                output_url = str(http_request.url_for("serve_output_file", filename=final_path.name))
                primary = ModelInfo(
                    type="glb", url=output_url,
                    title=f"Generated: {prompt}",
                    source_url=generator_endpoint,
                    source_domain="mesh-generator",
                    description=(
                        f"Generated via fallback mesh API from image: {image_url} "
                        f"({image_bytes} bytes), then segmented/renamed"
                    ),
                    tags=attributes.keywords,
                )
                approved_models = [primary]
                scored_models.append({
                    "model": primary, "score": 0.0,
                    "decision": "GENERATED_FALLBACK",
                    "is_labelled": bool(semantic_names),
                    "node_names": semantic_names,
                    "semantic_score": 0.0, "geometric_score": 0.0,
                })
                status = "completed"
            except Exception as e:
                logger.error(f"❌ Mesh-generation fallback failed: {e}", exc_info=True)
                status = "no_model"
        else:
            status = "completed"

        # Upload first model to GridFS
        await _upload_to_gridfs(approved_models)

        # Save first-model result to DB immediately
        update = {
            "attributes": attributes.model_dump(),
            "primary_model": primary.model_dump() if primary else None,
            "all_models": [m.model_dump() for m in approved_models],
            "scored_results": [
                {
                    "model": item["model"].model_dump(),
                    "score": item["score"],
                    "decision": item["decision"],
                    "is_labelled": item["is_labelled"],
                    "node_names": item.get("node_names", []),
                    "semantic_score": item["semantic_score"],
                    "geometric_score": item["geometric_score"],
                }
                for item in scored_models
            ],
            "status": status,
        }
        await db.searches.update_one({"id": record.id}, {"$set": update})

        # ─── BACKGROUND: Schedule remaining models for async processing ───
        remaining = models[attempts:MAX_ATTEMPTS]
        if remaining and status == "completed":
            logger.info(f"[BG] Scheduling {len(remaining)} remaining models for background processing")
            background_tasks.add_task(
                _process_remaining_models_bg,
                record.id,
                remaining,
                prompt,
                attributes,
                sketchfab_api_key,
                attempts,
                scored_models,
            )

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


@api_router.post("/chat", response_model=ChatResponse)
async def model_chat(request: ChatRequest):
    return await handle_model_chat(
        request=request,
        db=db,
        groq_client=groq_client,
        logger=logger,
    )


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

@api_router.get("/output/{filename}")
async def serve_output_file(filename: str):
    """Serve segmented GLB files from GridFS or local app/output/ over HTTP."""
    # Try GridFS first
    try:
        grid_out = await fs.open_download_stream_by_name(filename)
        file_data = await grid_out.read()
        
        return StreamingResponse(
            iter([file_data]),
            media_type="model/gltf-binary",
            headers={"Content-Length": str(len(file_data))}
        )
    except Exception as e:
        logger.warning(f"GridFS not found for {filename}, falling back to local: {e}")

    # Fallback to local file
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="model/gltf-binary"
    )

@api_router.post("/mesh/rename")
async def mesh_rename(
    file: UploadFile = File(...),
    hint: str = Form(default=""),
    prompt: str = Form(default=""),
    object_type: str = Form(default=""),
    title: str = Form(default=""),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    keywords: str = Form(default=""),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    try:
        data = await file.read()
        rename_context = {
            "prompt": prompt,
            "object_type": object_type,
            "title": title,
            "description": description,
            "tags": [part.strip() for part in tags.split(",") if part.strip()],
            "keywords": [part.strip() for part in keywords.split(",") if part.strip()],
        }
        result = await rename_meshes_from_file(
            file.filename,
            data,
            hint=hint or prompt or "",
            context=rename_context,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Mesh rename error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@api_router.post("/generate-mesh-from-prompt", response_model=MeshFromPromptResponse)
async def generate_mesh_from_prompt(request: MeshFromPromptRequest, http_request: Request):
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    generated_path, image_url, image_bytes, mesh_api_url = await _generate_mesh_file_from_prompt(prompt)
    rename_context = {
        "prompt": prompt,
        "object_type": "",
        "title": f"Generated: {prompt}",
        "description": f"Generated from prompt: {prompt}",
        "tags": [],
        "keywords": [],
    }
    final_path, _ = await _segment_and_rename_generated_model(
        generated_path,
        hint=prompt,
        context=rename_context,
    )

    output_url = str(http_request.url_for("serve_output_file", filename=final_path.name))

    return MeshFromPromptResponse(
        prompt=prompt,
        selected_image_url=image_url,
        selected_image_bytes=image_bytes,
        output_filename=final_path.name,
        output_url=output_url,
        generator_endpoint=mesh_api_url,
    )
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
