"""
Prompt2Mesh — Sketchfab Download API Integration
-------------------------------------------------
Handles the full flow for fetching scorable 3D assets:

  1. Direct URL models (Poly Haven, Quaternius, CDN links)
     → Download directly → pass to scorer

  2. Sketchfab models
     → Check is_downloadable flag
     → Request temporary GLB URL via Sketchfab Download API
     → Download GLB → pass to scorer

  3. Undownloadable Sketchfab models
     → Fall back to metadata-only scoring (no CLIP, no geometry)
     → Score is capped at 0.59 → always routes to DISCARD/Blender

Decision gate output feeds directly into model_scorer.py
"""

import os
import json
import asyncio
import tempfile
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional
from enum import Enum

import httpx


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

SKETCHFAB_API_BASE   = "https://api.sketchfab.com/v3"
DOWNLOAD_TIMEOUT_SEC = httpx.Timeout(connect=10, read=120, write=30, pool=10)
MAX_FILE_SIZE_MB     = 50        # skip models larger than this
TEMP_DIR             = Path(tempfile.gettempdir()) / "prompt2mesh_models"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────

class ModelSource(str, Enum):
    DIRECT    = "direct"      # direct CDN / URL with .glb/.gltf extension
    SKETCHFAB = "sketchfab"   # sketchfab.com model page
    UNKNOWN   = "unknown"


class DownloadStatus(str, Enum):
    SUCCESS          = "success"
    NOT_DOWNLOADABLE = "not_downloadable"   # creator disabled downloads
    AUTH_REQUIRED    = "auth_required"      # needs Sketchfab API key
    RATE_LIMITED     = "rate_limited"       # hit Sketchfab API rate limit
    TOO_LARGE        = "too_large"          # file exceeds MAX_FILE_SIZE_MB
    FAILED           = "failed"             # generic failure


@dataclass
class SketchfabMetadata:
    model_id:        str
    title:           str           = ""
    description:     str           = ""
    tags:            list          = field(default_factory=list)
    face_count:      int           = 0
    is_downloadable: bool          = False
    license:         str           = ""
    thumbnail_url:   str           = ""
    author:          str           = ""


@dataclass
class FetchResult:
    source:          ModelSource
    status:          DownloadStatus
    local_path:      Optional[str]  = None   # path to downloaded .glb file
    metadata:        Optional[dict] = None   # title, description, tags
    sketchfab_id:    Optional[str]  = None
    original_url:    str            = ""
    file_size_mb:    float          = 0.0
    error:           Optional[str]  = None
    is_scorable:     bool           = False  # True only if local_path is set


# ─────────────────────────────────────────────
# URL Classification
# ─────────────────────────────────────────────

def classify_url(url: str) -> ModelSource:
    """Determine whether a URL is a direct model file or a Sketchfab page."""
    url_lower = url.lower()
    if "sketchfab.com" in url_lower:
        return ModelSource.SKETCHFAB
    if any(ext in url_lower for ext in [".glb", ".gltf", ".obj", ".ply", ".fbx"]):
        return ModelSource.DIRECT
    return ModelSource.UNKNOWN


def extract_sketchfab_id(url: str) -> Optional[str]:
    """
    Extract model ID from Sketchfab URLs.

    Handles formats:
      https://sketchfab.com/3d-models/human-heart-abc123def456
      https://sketchfab.com/models/abc123def456
      https://sketchfab.com/3d-models/title-{uid}
    """
    import re
    # Match the UID at the end of the URL path (32-char hex or shorter alphanumeric)
    patterns = [
        r"sketchfab\.com/3d-models/[^/]+-([a-f0-9]{32})",   # slug-uid format
        r"sketchfab\.com/models/([a-f0-9]{32})",              # direct model id
        r"sketchfab\.com/3d-models/([a-zA-Z0-9\-]+)$",       # fallback: use slug
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


# ─────────────────────────────────────────────
# Sketchfab API
# ─────────────────────────────────────────────

async def fetch_sketchfab_metadata(
    model_id: str,
    api_key: Optional[str] = None
) -> SketchfabMetadata:
    """
    Fetch public metadata for a Sketchfab model.
    Metadata is public — no API key required for basic fields.
    """
    url = f"{SKETCHFAB_API_BASE}/models/{model_id}"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Token {api_key}"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            return SketchfabMetadata(
                model_id        = model_id,
                title           = data.get("name", ""),
                description     = data.get("description", ""),
                tags            = [t["name"] for t in data.get("tags", [])],
                face_count      = data.get("faceCount", 0),
                is_downloadable = data.get("isDownloadable", False),
                license         = data.get("license", {}).get("label", "") if data.get("license") else "",
                thumbnail_url   = (
                    data.get("thumbnails", {})
                        .get("images", [{}])[0]
                        .get("url", "")
                    if data.get("thumbnails") else ""
                ),
                author          = data.get("user", {}).get("username", ""),
            )
        except Exception as e:
            print(f"[Sketchfab] Failed to fetch metadata for {model_id}: {e}")
            return SketchfabMetadata(model_id=model_id)


async def request_sketchfab_download_url(
    model_id: str,
    api_key: str
) -> Optional[str]:
    """
    Request a temporary signed download URL from Sketchfab Download API.

    POST /v3/models/{model_id}/download
    Returns a GLB URL that expires in ~1 hour.

    Requires:
      - Valid Sketchfab API key
      - Model must have is_downloadable = True
      - Authenticated user must have download permission
    """
    url = f"{SKETCHFAB_API_BASE}/models/{model_id}/download"
    headers = {"Authorization": f"Token {api_key}"}

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                # Prefer GLB, fall back to GLTF
                glb_url  = data.get("glb", {}).get("url")
                gltf_url = data.get("gltf", {}).get("url")
                download_url = glb_url or gltf_url
                if download_url:
                    print(f"[Sketchfab] Got download URL for {model_id} (expires in ~1 hour)")
                return download_url

            elif resp.status_code == 401:
                print(f"[Sketchfab] Auth failed for {model_id} — check API key")
                return None

            elif resp.status_code == 403:
                print(f"[Sketchfab] Download not permitted for {model_id} — creator restriction")
                return None

            elif resp.status_code == 429:
                print(f"[Sketchfab] Rate limited — back off before retrying")
                return None

            else:
                print(f"[Sketchfab] Unexpected status {resp.status_code} for {model_id}")
                return None

        except Exception as e:
            print(f"[Sketchfab] Download URL request failed for {model_id}: {e}")
            return None


# ─────────────────────────────────────────────
# File Downloader
# ─────────────────────────────────────────────

async def download_model_file(
    url: str,
    filename_hint: str = "model"
) -> Optional[str]:
    """
    Download a 3D model file from a URL to a local temp file.
    Returns local file path or None on failure.
    Skips files exceeding MAX_FILE_SIZE_MB.
    """
    # Generate a stable filename from the URL hash
    url_hash  = hashlib.md5(url.encode()).hexdigest()[:10]
    extension = Path(url.split("?")[0]).suffix or ".glb"
    local_path = TEMP_DIR / f"{filename_hint}_{url_hash}{extension}"

    # Return cached file if already downloaded
    if local_path.exists():
        print(f"[Download] Cache hit → {local_path}")
        return str(local_path)

    print(f"[Download] Fetching {url[:80]}...")

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
        try:
            # Stream the response to check size before full download
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()

                # Check content-length header before downloading
                content_length = resp.headers.get("content-length")
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    if size_mb > MAX_FILE_SIZE_MB:
                        print(f"[Download] Skipping — file too large ({size_mb:.1f} MB > {MAX_FILE_SIZE_MB} MB)")
                        return None

                # Stream to disk
                downloaded_bytes = 0
                with open(local_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        downloaded_bytes += len(chunk)
                        # Runtime size check in case no content-length header
                        if downloaded_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
                            print(f"[Download] Aborting — exceeded size limit during download")
                            local_path.unlink(missing_ok=True)
                            return None
                        f.write(chunk)

            size_mb = downloaded_bytes / (1024 * 1024)
            print(f"[Download] Saved to {local_path} ({size_mb:.1f} MB)")
            return str(local_path)

        except httpx.HTTPStatusError as e:
            print(f"[Download] HTTP error {e.response.status_code} for {url}")
            local_path.unlink(missing_ok=True)
            return None
        except Exception as e:
            print(f"[Download] Failed: {e}")
            local_path.unlink(missing_ok=True)
            return None


# ─────────────────────────────────────────────
# Main Fetch Orchestrator
# ─────────────────────────────────────────────

async def fetch_model(
    url: str,
    sketchfab_api_key: Optional[str] = None
) -> FetchResult:
    """
    Unified fetch handler for any model URL.
    Classifies the URL and routes to the appropriate download strategy.

    Returns FetchResult with:
      - local_path: path to downloaded file (if successful)
      - metadata:   dict with title, description, tags
      - is_scorable: True if local_path is set and file is ready for scorer
    """
    source = classify_url(url)
    print(f"\n[Fetch] URL={url[:80]}...")
    print(f"[Fetch] Source type: {source.value}")

    # ── Direct URL ──────────────────────────────
    if source == ModelSource.DIRECT:
        local_path = await download_model_file(url, filename_hint="direct")
        if local_path:
            return FetchResult(
                source       = source,
                status       = DownloadStatus.SUCCESS,
                local_path   = local_path,
                metadata     = {},
                original_url = url,
                file_size_mb = Path(local_path).stat().st_size / (1024 * 1024),
                is_scorable  = True
            )
        else:
            return FetchResult(
                source       = source,
                status       = DownloadStatus.FAILED,
                original_url = url,
                error        = "Download failed",
                is_scorable  = False
            )

    # ── Sketchfab URL ────────────────────────────
    elif source == ModelSource.SKETCHFAB:
        model_id = extract_sketchfab_id(url)
        if not model_id:
            return FetchResult(
                source       = source,
                status       = DownloadStatus.FAILED,
                original_url = url,
                error        = "Could not extract Sketchfab model ID from URL",
                is_scorable  = False
            )

        # Step 1 — Fetch metadata (always available, no key needed)
        print(f"[Sketchfab] Fetching metadata for model_id={model_id}")
        sf_meta = await fetch_sketchfab_metadata(model_id, sketchfab_api_key)
        metadata = {
            "title":       sf_meta.title,
            "description": sf_meta.description,
            "tags":        sf_meta.tags,
        }

        # Step 2 — Check if downloadable
        if not sf_meta.is_downloadable:
            print(f"[Sketchfab] Model {model_id} is not downloadable — metadata only")
            return FetchResult(
                source        = source,
                status        = DownloadStatus.NOT_DOWNLOADABLE,
                local_path    = None,
                metadata      = metadata,
                sketchfab_id  = model_id,
                original_url  = url,
                is_scorable   = False,
                error         = "Creator has disabled downloads for this model"
            )

        # Step 3 — Need API key to download
        api_key = sketchfab_api_key or os.environ.get("SKETCHFAB_API_KEY")
        if not api_key:
            print(f"[Sketchfab] Model is downloadable but no API key provided")
            return FetchResult(
                source       = source,
                status       = DownloadStatus.AUTH_REQUIRED,
                local_path   = None,
                metadata     = metadata,
                sketchfab_id = model_id,
                original_url = url,
                is_scorable  = False,
                error        = "SKETCHFAB_API_KEY not set — cannot request download URL"
            )

        # Step 4 — Request temporary download URL
        print(f"[Sketchfab] Requesting download URL for model_id={model_id}")
        download_url = await request_sketchfab_download_url(model_id, api_key)

        if not download_url:
            return FetchResult(
                source       = source,
                status       = DownloadStatus.FAILED,
                local_path   = None,
                metadata     = metadata,
                sketchfab_id = model_id,
                original_url = url,
                is_scorable  = False,
                error        = "Sketchfab did not return a download URL"
            )

        # Step 5 — Download the GLB file
        local_path = await download_model_file(
            download_url,
            filename_hint=f"sf_{model_id[:8]}"
        )

        if not local_path:
            return FetchResult(
                source       = source,
                status       = DownloadStatus.FAILED,
                local_path   = None,
                metadata     = metadata,
                sketchfab_id = model_id,
                original_url = url,
                is_scorable  = False,
                error        = "GLB file download failed after getting download URL"
            )

        file_size_mb = Path(local_path).stat().st_size / (1024 * 1024)
        return FetchResult(
            source       = source,
            status       = DownloadStatus.SUCCESS,
            local_path   = local_path,
            metadata     = metadata,
            sketchfab_id = model_id,
            original_url = url,
            file_size_mb = file_size_mb,
            is_scorable  = True
        )

    # ── Unknown URL ──────────────────────────────
    else:
        # Try downloading anyway — might be a direct file with no extension in URL
        local_path = await download_model_file(url, filename_hint="unknown")
        if local_path:
            return FetchResult(
                source       = ModelSource.UNKNOWN,
                status       = DownloadStatus.SUCCESS,
                local_path   = local_path,
                metadata     = {},
                original_url = url,
                file_size_mb = Path(local_path).stat().st_size / (1024 * 1024),
                is_scorable  = True
            )
        return FetchResult(
            source       = ModelSource.UNKNOWN,
            status       = DownloadStatus.FAILED,
            original_url = url,
            error        = "Unknown URL type and download failed",
            is_scorable  = False
        )


# ─────────────────────────────────────────────
# Pipeline Integration
# ─────────────────────────────────────────────

async def fetch_and_score(
    url: str,
    prompt: str,
    sketchfab_api_key: Optional[str] = None
) -> dict:
    """
    Full fetch + score pipeline for a single model URL.

    If model is downloadable → full composite score (semantic + geometric)
    If not downloadable       → metadata-only score (capped at 0.59 → always DISCARD)

    Returns a dict with fetch result + composite score merged.
    """
    # Import scorer here to avoid circular imports
    from app.model_scorer import score_model_to_json
    from rapidfuzz import fuzz

    fetch = await fetch_model(url, sketchfab_api_key)
    fetch_dict = asdict(fetch)

    if fetch.is_scorable and fetch.local_path:
        # Full scoring — both semantic and geometric
        print(f"\n[Pipeline] Model downloaded → running full composite score")
        try:
            score = score_model_to_json(
                model_path  = fetch.local_path,
                prompt      = prompt,
                metadata    = fetch.metadata or {},
            )
            return {**fetch_dict, "score": score, "scoring_mode": "full"}
        except Exception as e:
            print(f"[Pipeline] Scoring failed: {e}")
            return {**fetch_dict, "score": None, "scoring_mode": "failed", "error": str(e)}

    else:
        # Metadata-only fallback scoring
        # No geometry, no CLIP — score capped at 0.59 to always route to DISCARD
        print(f"\n[Pipeline] Model not downloadable → metadata-only scoring (capped at 0.59)")

        meta = fetch.metadata or {}
        prompt_lower = prompt.lower()

        title_score = fuzz.partial_ratio(prompt_lower, meta.get("title", "").lower()) / 100.0
        tags        = meta.get("tags", [])
        tag_score   = fuzz.partial_ratio(prompt_lower, " ".join(tags).lower()) / 100.0 if tags else 0.0
        desc_score  = fuzz.partial_ratio(prompt_lower, meta.get("description", "").lower()) / 100.0
        metadata_score = (0.50 * title_score) + (0.30 * tag_score) + (0.20 * desc_score)

        # Cap at 0.59 — metadata alone is insufficient for CACHE or USE
        capped_score = min(metadata_score * 0.59, 0.59)

        score = {
            "final_score": round(capped_score, 4),
            "decision":    "DISCARD",
            "semantic": {
                "clip_score":     None,
                "metadata_score": round(metadata_score, 4),
                "combined":       round(capped_score, 4),
            },
            "geometric": None,
            "note": "Metadata-only score — model was not downloadable. Capped at 0.59."
        }
        return {**fetch_dict, "score": score, "scoring_mode": "metadata_only"}


async def fetch_and_score_batch(
    candidates: list[dict],
    prompt: str,
    sketchfab_api_key: Optional[str] = None
) -> list[dict]:
    """
    Fetch and score multiple candidates concurrently.
    Each candidate dict must have a 'url' key.
    Returns list sorted by final_score descending.
    """
    tasks = [
        fetch_and_score(c["url"], prompt, sketchfab_api_key)
        for c in candidates
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    clean = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            clean.append({
                "url":         candidates[i].get("url", ""),
                "final_score": 0.0,
                "decision":    "DISCARD",
                "error":       str(r)
            })
        else:
            clean.append(r)

    clean.sort(key=lambda x: x.get("score", {}).get("final_score", 0.0) if x.get("score") else 0.0, reverse=True)
    return clean


# ─────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Prompt2Mesh — Sketchfab Fetch + Score")
    parser.add_argument("url",    type=str, help="Model URL (direct or Sketchfab page)")
    parser.add_argument("prompt", type=str, help="Original user prompt")
    parser.add_argument("--api-key", type=str, default=None, help="Sketchfab API key (or set SKETCHFAB_API_KEY env var)")
    parser.add_argument("--output",  type=str, default=None, help="Path to write JSON output")
    args = parser.parse_args()

    result = asyncio.run(fetch_and_score(
        url                = args.url,
        prompt             = args.prompt,
        sketchfab_api_key  = args.api_key
    ))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n[Output] Written to {args.output}")

    print("\nResult:")
    print(json.dumps(result, indent=2, default=str))
