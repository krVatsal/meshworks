"""
Prompt2Mesh — 3D Model Composite Scorer
----------------------------------------
Scores a fetched 3D model across two dimensions:
  1. Semantic Relevance  — how well the model matches the original prompt
  2. Geometric Complexity — how detailed and well-structured the model is

Final composite score + label status drives the decision gate:

  Labelled + score > 0.70:
    → USE — Model is correct AND has named nodes. Use directly for rendering
            and spatial conversation. No Blender needed.

  Labelled + score ≤ 0.70:
    → REFETCH — Model has labels but is the wrong object. Retrieval fetched
                something irrelevant. Refetch with refined query.

  Unlabelled + score > 0.85:
    → CACHE — Model is the right object, very high quality, but has no named
              nodes. Cache it for rendering. Trigger Blender MCP in background
              to generate a labelled version for spatial conversation.

  Unlabelled + score ≤ 0.85:
    → DISCARD — Model is either wrong object or low quality and has no labels.
                Not worth caching. Trigger Blender MCP for full procedural
                generation from structured intent.
"""

import os
import json
import math
import logging
import numpy as np
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

import torch
import trimesh
import open_clip
from PIL import Image
from rapidfuzz import fuzz
from trimesh.scene import Scene

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config (from environment variables with defaults)
# ─────────────────────────────────────────────

# Polygon count reference for normalisation (default: 30k faces = score of 1.0)
POLYGON_REFERENCE = int(os.getenv('POLYGON_REFERENCE', '30000'))

# Material count reference (default: 10 materials = score of 1.0)
MATERIAL_REFERENCE = int(os.getenv('MATERIAL_REFERENCE', '10'))

# Node count reference for hierarchy scoring (default: 25 nodes = score of 1.0)
NODE_REFERENCE = int(os.getenv('NODE_REFERENCE', '25'))

# Mesh node count reference (default: 5 separate meshes = score of 1.0)
MESH_NODE_REFERENCE = int(os.getenv('MESH_NODE_REFERENCE', '5'))

# Decision thresholds
THRESHOLD_LABELLED = float(os.getenv('THRESHOLD_LABELLED', '0.60'))    # If labelled and score > this → USE
THRESHOLD_UNLABELLED = float(os.getenv('THRESHOLD_UNLABELLED', '0.70'))  # If unlabelled and score > this → CACHE

# Minimum node count to consider a model "labelled"
MIN_LABELLED_NODES = int(os.getenv('MIN_LABELLED_NODES', '3'))

# Final score weight distribution (must sum to 1.0)
SEMANTIC_WEIGHT = float(os.getenv('SEMANTIC_WEIGHT', '0.5'))    # Weight for semantic score
GEOMETRIC_WEIGHT = float(os.getenv('GEOMETRIC_WEIGHT', '0.5'))  # Weight for geometric score

# ─────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────

@dataclass
class SemanticScore:
    clip_score: float          # weighted average across 6 views via OpenCLIP
    metadata_score: float      # fuzzy match between prompt and model metadata
    combined: float            # 0.7 * clip + 0.3 * metadata


@dataclass
class GeometricScore:
    polygon_density: float     # normalised face count
    mesh_node_count: float     # normalised count of mesh geometries
    node_hierarchy_depth: float  # normalised scene graph depth + node count
    uv_coverage: float         # fraction of faces with UV maps
    material_diversity: float  # normalised material count
    combined: float            # weighted combination


@dataclass
class CompositeScore:
    semantic: SemanticScore
    geometric: GeometricScore
    final_score: float         # 0.7 * semantic + 0.3 * geometric
    is_labelled: bool          # whether model has meaningful named nodes
    node_names: list           # extracted scene-graph node names (empty if unlabelled)
    decision: str              # USE / REFETCH / CACHE / DISCARD
    prompt: str
    model_path: str


# Viewpoint weights for CLIP scoring [front, back, left, right, top, bottom]
VIEW_WEIGHTS = [0.25, 0.25, 0.15, 0.15, 0.10, 0.10]

# Camera distance multiplier for rendering
CAMERA_DISTANCE = 2.5

# Image render resolution for CLIP
RENDER_SIZE = (224, 224)

# Log configuration on module load
logger.info("="*70)
logger.info("Model Scorer Configuration:")
logger.info(f"  POLYGON_REFERENCE:      {POLYGON_REFERENCE:,} faces")
logger.info(f"  MESH_NODE_REFERENCE:    {MESH_NODE_REFERENCE} meshes")
logger.info(f"  NODE_REFERENCE:         {NODE_REFERENCE} nodes")
logger.info(f"  MATERIAL_REFERENCE:     {MATERIAL_REFERENCE} materials")
logger.info(f"  SEMANTIC_WEIGHT:        {SEMANTIC_WEIGHT:.2f} ({int(SEMANTIC_WEIGHT*100)}%)")
logger.info(f"  GEOMETRIC_WEIGHT:       {GEOMETRIC_WEIGHT:.2f} ({int(GEOMETRIC_WEIGHT*100)}%)")
logger.info(f"  THRESHOLD_LABELLED:     {THRESHOLD_LABELLED:.2f}")
logger.info(f"  THRESHOLD_UNLABELLED:   {THRESHOLD_UNLABELLED:.2f}")
logger.info(f"  MIN_LABELLED_NODES:     {MIN_LABELLED_NODES}")
logger.info("="*70)


# ─────────────────────────────────────────────
# CLIP Model Loader (singleton)
# ─────────────────────────────────────────────

_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None

def load_clip():
    global _clip_model, _clip_preprocess, _clip_tokenizer
    if _clip_model is None:
        print("[CLIP] Loading OpenCLIP model (ViT-B-32)...")
        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai"
        )
        _clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")
        _clip_model.eval()
        print("[CLIP] Model loaded.")
    return _clip_model, _clip_preprocess, _clip_tokenizer


# ─────────────────────────────────────────────
# 1. Semantic Scoring
# ─────────────────────────────────────────────

def render_views(mesh_or_scene) -> list[Image.Image]:
    """
    Render 6 orthographic views of a trimesh object.
    Returns a list of PIL Images: [front, back, left, right, top, bottom]
    """
    # Normalise to scene
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        scene = mesh_or_scene.scene()
    elif isinstance(mesh_or_scene, Scene):
        scene = mesh_or_scene
    else:
        raise ValueError("Expected trimesh.Trimesh or trimesh.Scene")

    # Centre and scale the scene
    bounds = scene.bounds
    centre = (bounds[0] + bounds[1]) / 2
    scale  = np.linalg.norm(bounds[1] - bounds[0])
    if scale == 0:
        scale = 1.0

    d = scale * CAMERA_DISTANCE

    # Camera positions for each view
    camera_positions = {
        "front":  centre + np.array([0,  0,  d]),
        "back":   centre + np.array([0,  0, -d]),
        "left":   centre + np.array([-d, 0,  0]),
        "right":  centre + np.array([ d, 0,  0]),
        "top":    centre + np.array([0,  d,  0]),
        "bottom": centre + np.array([0, -d,  0]),
    }

    images = []
    for view_name, camera_pos in camera_positions.items():
        try:
            # Set camera to look at centre from this position
            scene.set_camera(angles=None, distance=None,
                             center=centre, resolution=RENDER_SIZE)
            scene.camera.look_at(
                points=[centre],
                rotation=trimesh.transformations.look_at(
                    camera_pos, centre, np.array([0, 1, 0])
                )
            )
            png = scene.save_image(resolution=RENDER_SIZE, visible=True)
            img = Image.open(__import__("io").BytesIO(png)).convert("RGB")
        except Exception:
            # Fallback: blank white image if render fails
            img = Image.new("RGB", RENDER_SIZE, (255, 255, 255))
        images.append(img)

    return images


def compute_clip_score(prompt: str, images: list[Image.Image]) -> float:
    """
    Compute weighted average cosine similarity between prompt and 6 view images.
    """
    model, preprocess, tokenizer = load_clip()

    # Encode text
    text_tokens = tokenizer([prompt])
    with torch.no_grad():
        text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    # Encode each image and compute similarity
    similarities = []
    for img in images:
        img_tensor = preprocess(img).unsqueeze(0)
        with torch.no_grad():
            img_features = model.encode_image(img_tensor)
            img_features = img_features / img_features.norm(dim=-1, keepdim=True)
        sim = (text_features @ img_features.T).item()
        # Cosine similarity from CLIP is typically in [-1, 1], normalise to [0, 1]
        sim_normalised = (sim + 1) / 2
        similarities.append(sim_normalised)

    # Weighted average across views
    assert len(similarities) == len(VIEW_WEIGHTS), "View count mismatch"
    weighted_score = sum(s * w for s, w in zip(similarities, VIEW_WEIGHTS))
    return round(float(weighted_score), 4)


def compute_metadata_score(prompt: str, metadata: dict) -> float:
    """
    Fuzzy string match between prompt and model metadata fields.
    metadata dict expected keys: title, description, tags (list of str)
    """
    prompt_lower = prompt.lower()

    title_score = fuzz.partial_ratio(
        prompt_lower, metadata.get("title", "").lower()
    ) / 100.0

    desc_score = fuzz.partial_ratio(
        prompt_lower, metadata.get("description", "").lower()
    ) / 100.0

    tags = metadata.get("tags", [])
    if tags:
        tag_string = " ".join(str(t).lower() for t in tags)
        tag_score = fuzz.partial_ratio(prompt_lower, tag_string) / 100.0
    else:
        tag_score = 0.0

    # Weighted: title most important, then tags, then description
    combined = (0.50 * title_score) + (0.30 * tag_score) + (0.20 * desc_score)
    return round(float(combined), 4)


def score_semantic(
    prompt: str,
    mesh_or_scene,
    metadata: Optional[dict] = None
) -> SemanticScore:
    """
    Compute full semantic score for a model against a prompt.
    """
    print("[Semantic] Rendering views...")
    images = render_views(mesh_or_scene)

    print("[Semantic] Computing CLIP score...")
    clip_score = compute_clip_score(prompt, images)

    print("[Semantic] Computing metadata score...")
    meta = metadata or {}
    metadata_score = compute_metadata_score(prompt, meta)

    combined = round((0.7 * clip_score) + (0.3 * metadata_score), 4)

    logger.info(f"[Semantic] CLIP={clip_score:.4f} | Metadata={metadata_score:.4f} | Combined={combined:.4f}")
    return SemanticScore(
        clip_score=clip_score,
        metadata_score=metadata_score,
        combined=combined
    )


# ─────────────────────────────────────────────
# 2. Geometric Complexity Scoring
# ─────────────────────────────────────────────

def score_polygon_density(mesh_or_scene) -> float:
    """
    Normalised face count score. 100k faces = 1.0
    """
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        face_count = len(mesh_or_scene.faces)
    elif isinstance(mesh_or_scene, Scene):
        face_count = sum(
            len(g.faces) for g in mesh_or_scene.geometry.values()
            if isinstance(g, trimesh.Trimesh)
        )
    else:
        face_count = 0

    score = min(face_count / POLYGON_REFERENCE, 1.0)
    print(f"[Geometric] Polygon density: {face_count} faces → score={score:.4f}")
    return round(float(score), 4)


def score_mesh_node_count(mesh_or_scene) -> float:
    """
    Normalised count of separate mesh geometries.
    Measures model complexity by number of distinct mesh components.
    10 mesh nodes = 1.0
    """
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        # Single mesh
        mesh_count = 1
    elif isinstance(mesh_or_scene, Scene):
        # Count all mesh geometries in the scene
        mesh_count = sum(
            1 for g in mesh_or_scene.geometry.values()
            if isinstance(g, trimesh.Trimesh)
        )
    else:
        mesh_count = 0
    
    score = min(mesh_count / MESH_NODE_REFERENCE, 1.0)
    print(f"[Geometric] Mesh node count: {mesh_count} meshes → score={score:.4f}")
    return round(float(score), 4)


def score_node_hierarchy(mesh_or_scene) -> float:
    """
    Scores the richness of the scene graph.
    Considers both number of named nodes and depth of hierarchy.
    """
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        # Single mesh — no hierarchy
        return 0.1

    if not isinstance(mesh_or_scene, Scene):
        return 0.0

    graph = mesh_or_scene.graph
    node_count = len(list(graph.nodes))

    # Compute max depth by traversing from world frame
    def get_depth(node, visited=None):
        if visited is None:
            visited = set()
        if node in visited:
            return 0
        visited.add(node)
        children = list(graph.transforms.successors(node)) \
            if hasattr(graph.transforms, "successors") else []
        if not children:
            return 1
        return 1 + max(get_depth(c, visited) for c in children)

    try:
        max_depth = get_depth(graph.base_frame)
    except Exception:
        max_depth = 1

    # Normalise: node count out of NODE_REFERENCE, depth out of 10
    node_score  = min(node_count / NODE_REFERENCE, 1.0)
    depth_score = min(max_depth / 10, 1.0)

    combined = (0.6 * node_score) + (0.4 * depth_score)
    print(f"[Geometric] Nodes={node_count}, Depth={max_depth} → score={combined:.4f}")
    return round(float(combined), 4)


def score_uv_coverage(mesh_or_scene) -> float:
    """
    Fraction of total faces that have UV mapping assigned.
    """
    total_faces = 0
    uv_faces    = 0

    meshes = []
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        meshes = [mesh_or_scene]
    elif isinstance(mesh_or_scene, Scene):
        meshes = [g for g in mesh_or_scene.geometry.values()
                  if isinstance(g, trimesh.Trimesh)]

    for mesh in meshes:
        f = len(mesh.faces)
        total_faces += f
        if hasattr(mesh.visual, "uv") and mesh.visual.uv is not None:
            if len(mesh.visual.uv) > 0:
                uv_faces += f

    score = (uv_faces / total_faces) if total_faces > 0 else 0.0
    print(f"[Geometric] UV coverage: {uv_faces}/{total_faces} faces → score={score:.4f}")
    return round(float(score), 4)


def score_material_diversity(mesh_or_scene) -> float:
    """
    Normalised count of distinct materials. 20 materials = 1.0
    """
    material_names = set()

    meshes = []
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        meshes = [mesh_or_scene]
    elif isinstance(mesh_or_scene, Scene):
        meshes = [g for g in mesh_or_scene.geometry.values()
                  if isinstance(g, trimesh.Trimesh)]

    for mesh in meshes:
        if hasattr(mesh.visual, "material"):
            mat = mesh.visual.material
            name = getattr(mat, "name", None) or str(id(mat))
            material_names.add(name)

    count = len(material_names)
    score = min(count / MATERIAL_REFERENCE, 1.0)
    print(f"[Geometric] Materials: {count} distinct → score={score:.4f}")
    return round(float(score), 4)


def score_geometric(mesh_or_scene) -> GeometricScore:
    """
    Compute full geometric complexity score.
    """
    polygon_density      = score_polygon_density(mesh_or_scene)
    mesh_node_count      = score_mesh_node_count(mesh_or_scene)
    node_hierarchy_depth = score_node_hierarchy(mesh_or_scene)
    uv_coverage          = score_uv_coverage(mesh_or_scene)
    material_diversity   = score_material_diversity(mesh_or_scene)

    combined = round(
        (0.35 * polygon_density) +
        (0.20 * mesh_node_count) +
        (0.20 * node_hierarchy_depth) +
        (0.15 * uv_coverage) +
        (0.10 * material_diversity),
        4
    )

    print(f"[Geometric] Combined={combined:.4f}")
    return GeometricScore(
        polygon_density=polygon_density,
        mesh_node_count=mesh_node_count,
        node_hierarchy_depth=node_hierarchy_depth,
        uv_coverage=uv_coverage,
        material_diversity=material_diversity,
        combined=combined
    )


# ─────────────────────────────────────────────
# 3. Label Detection
# ─────────────────────────────────────────────

def check_if_labelled(mesh_or_scene) -> tuple:
    """
    Determine if a model has meaningful labels (named nodes).
    Returns (is_labelled: bool, node_names: list[str]).
    """
    if isinstance(mesh_or_scene, trimesh.Trimesh):
        # Single mesh with no hierarchy
        return False, []
    
    if not isinstance(mesh_or_scene, Scene):
        return False, []
    
    graph = mesh_or_scene.graph
    
    # Get all node names (excluding auto-generated ones)
    node_names = []
    for node in graph.nodes:
        # Filter out generic/auto-generated names
        if node and not node.startswith('world'):
            # Check if name looks meaningful (not just numbers or generic patterns)
            if not node.isdigit() and len(node) > 1:
                node_names.append(node)
    
    # Consider labeled if we have at least MIN_LABELLED_NODES distinct meaningful names
    is_labelled = len(node_names) >= MIN_LABELLED_NODES
    
    print(f"[Label Check] Found {len(node_names)} meaningful node names: {node_names[:5]}")
    print(f"[Label Check] Model is {'LABELLED' if is_labelled else 'UNLABELLED'}")
    
    return is_labelled, node_names


# ─────────────────────────────────────────────
# 4. Decision Gate
# ─────────────────────────────────────────────

def make_decision(final_score: float, is_labelled: bool) -> str:
    """
    Returns decision string based on composite score and label status.

    Decision logic (thresholds configurable via environment):
    1. Labelled + score > THRESHOLD_LABELLED (default 0.60)  → USE
       Model is correct AND has named nodes. Use directly for rendering
       and spatial conversation. No Blender needed.

    2. Labelled + score ≤ THRESHOLD_LABELLED → REFETCH
       Model has labels but is the wrong object — retrieval fetched
       something irrelevant. Refetch with refined query. Do not generate
       because the problem is retrieval, not model availability.

    3. Unlabelled + score > THRESHOLD_UNLABELLED (default 0.70) → CACHE
       Model is the right object, very high quality, but has no named nodes.
       Cache it for rendering. Trigger Blender MCP in background to generate
       a labelled version for spatial conversation.

    4. Unlabelled + score ≤ THRESHOLD_UNLABELLED → DISCARD
       Model is either wrong object or low quality and has no labels.
       Not worth caching. Discard and trigger Blender MCP for full
       procedural generation from structured intent.
    """
    if is_labelled:
        if final_score > THRESHOLD_LABELLED:
            return "USE"     # Perfect: good model with labels
        else:
            return "REFETCH"  # Wrong object, try better search
    else:
        if final_score > THRESHOLD_UNLABELLED:
            return "CACHE"    # Good model, needs labeling
        else:
            return "DISCARD"  # Poor quality, generate from scratch


# ─────────────────────────────────────────────
# 5. Main Scorer
# ─────────────────────────────────────────────

def score_model(
    model_path: str,
    prompt: str,
    metadata: Optional[dict] = None
) -> CompositeScore:
    """
    Full scoring pipeline for a fetched 3D model.

    Args:
        model_path: Path to the 3D model file (.glb, .obj, .ply, etc.)
        prompt:     Original user prompt string
        metadata:   Optional dict with keys: title, description, tags

    Returns:
        CompositeScore dataclass with all sub-scores and final decision
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"[Scorer] Model : {model_path}")
    logger.info(f"[Scorer] Prompt: {prompt}")
    logger.info(f"{'='*60}\n")

    # Load model
    logger.info("[Loader] Loading 3D model...")
    try:
        loaded = trimesh.load(model_path, force="scene")
    except Exception as e:
        raise RuntimeError(f"Failed to load model at {model_path}: {e}")
    logger.info(f"[Loader] Loaded successfully → type={type(loaded).__name__}")

    # Check if model has labels — returns (bool, list[str])
    is_labelled, node_names = check_if_labelled(loaded)
    
    # Score
    semantic  = score_semantic(prompt, loaded, metadata)
    geometric = score_geometric(loaded)

    # Composite (using configurable weights)
    final_score = round(
        (SEMANTIC_WEIGHT * semantic.combined) + (GEOMETRIC_WEIGHT * geometric.combined),
        4
    )
    decision = make_decision(final_score, is_labelled)

    logger.info(f"\n{'='*60}")
    logger.info(f"[Result] Semantic  : {semantic.combined:.4f} (weight: {SEMANTIC_WEIGHT:.2f})")
    logger.info(f"[Result] Geometric : {geometric.combined:.4f} (weight: {GEOMETRIC_WEIGHT:.2f})")
    logger.info(f"[Result] Labelled  : {is_labelled} ({len(node_names)} nodes)")
    if node_names:
        logger.info(f"[Result] Node names: {node_names}")
    logger.info(f"[Result] FINAL     : {final_score:.4f} = {SEMANTIC_WEIGHT:.1f}×{semantic.combined:.4f} + {GEOMETRIC_WEIGHT:.1f}×{geometric.combined:.4f}")
    logger.info(f"[Result] DECISION  : {decision}")
    logger.info(f"{'='*60}\n")

    return CompositeScore(
        semantic=semantic,
        geometric=geometric,
        final_score=final_score,
        is_labelled=is_labelled,
        node_names=node_names,
        decision=decision,
        prompt=prompt,
        model_path=model_path
    )


def score_model_to_json(
    model_path: str,
    prompt: str,
    metadata: Optional[dict] = None,
    output_path: Optional[str] = None
) -> dict:
    """
    Wrapper that returns the composite score as a JSON-serialisable dict
    and optionally writes it to a file.
    """
    result = score_model(model_path, prompt, metadata)
    result_dict = asdict(result)

    if output_path:
        with open(output_path, "w") as f:
            json.dump(result_dict, f, indent=2)
        print(f"[Output] Score written to {output_path}")

    return result_dict


# ─────────────────────────────────────────────
# 6. Batch Scorer (for ranking multiple candidates)
# ─────────────────────────────────────────────

def score_candidates(
    candidates: list[dict],
    prompt: str
) -> list[dict]:
    """
    Score multiple candidate models and return them ranked by final score.

    Each candidate dict must have:
        model_path (str): path to the 3D model file
        metadata   (dict, optional): title, description, tags

    Returns list of result dicts sorted by final_score descending.
    """
    results = []
    for i, candidate in enumerate(candidates):
        print(f"\n[Batch] Scoring candidate {i+1}/{len(candidates)}")
        try:
            result = score_model(
                model_path=candidate["model_path"],
                prompt=prompt,
                metadata=candidate.get("metadata", {})
            )
            results.append(asdict(result))
        except Exception as e:
            print(f"[Batch] Failed to score {candidate['model_path']}: {e}")
            results.append({
                "model_path": candidate["model_path"],
                "final_score": 0.0,
                "decision": "DISCARD",
                "error": str(e)
            })

    results.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
    print(f"\n[Batch] Ranked {len(results)} candidates. Top score: {results[0]['final_score']:.4f}")
    return results


# ─────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Prompt2Mesh — 3D Model Composite Scorer")
    parser.add_argument("model_path", type=str, help="Path to the 3D model file")
    parser.add_argument("prompt",     type=str, help="Original user prompt")
    parser.add_argument("--title",    type=str, default="", help="Model title metadata")
    parser.add_argument("--description", type=str, default="", help="Model description metadata")
    parser.add_argument("--tags",     type=str, default="", help="Comma-separated tags")
    parser.add_argument("--output",   type=str, default=None, help="Path to write JSON output")
    args = parser.parse_args()

    metadata = {
        "title":       args.title,
        "description": args.description,
        "tags":        [t.strip() for t in args.tags.split(",") if t.strip()]
    }

    result = score_model_to_json(
        model_path=args.model_path,
        prompt=args.prompt,
        metadata=metadata,
        output_path=args.output
    )

    print("\nFinal Score JSON:")
    print(json.dumps(result, indent=2))
