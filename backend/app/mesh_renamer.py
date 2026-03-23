import asyncio
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "").strip()
API_KEY = os.environ.get("API_KEY", "").strip()
GLM_MODEL = os.environ.get("GLM_MODEL", "gpt-5-mini").strip()


def _create_client() -> OpenAI:
    if not GLM_BASE_URL or not API_KEY:
        raise RuntimeError("GLM_BASE_URL/API_KEY not configured in backend .env")
    return OpenAI(base_url=GLM_BASE_URL, api_key=API_KEY)


def parse_glb(data: bytes) -> Dict[str, Any]:
    if len(data) < 20 or data[0:4] != b"glTF":
        raise ValueError("Not a valid GLB file")

    total_length = int.from_bytes(data[8:12], "little")
    offset = 12
    json_chunk: Optional[bytes] = None

    while offset + 8 <= total_length and offset + 8 <= len(data):
        chunk_length = int.from_bytes(data[offset:offset + 4], "little")
        chunk_type = int.from_bytes(data[offset + 4:offset + 8], "little")
        start = offset + 8
        end = start + chunk_length
        if end > len(data):
            break
        chunk_data = data[start:end]
        # JSON chunk type is 0x4E4F534A
        if chunk_type == 0x4E4F534A:
            json_chunk = chunk_data
            break
        offset = end

    if json_chunk is None:
        raise ValueError("No JSON chunk found in GLB")

    return json.loads(json_chunk.decode("utf-8"))


def parse_gltf(data: bytes) -> Dict[str, Any]:
    return json.loads(data.decode("utf-8"))


def _get_texture_name(gltf: Dict[str, Any], texture_index: Optional[int]) -> Optional[str]:
    if texture_index is None:
        return None
    textures = gltf.get("textures") or []
    images = gltf.get("images") or []
    if texture_index < 0 or texture_index >= len(textures):
        return None
    texture = textures[texture_index] or {}
    if texture.get("name"):
        return str(texture["name"])
    source_index = texture.get("source")
    if isinstance(source_index, int) and 0 <= source_index < len(images):
        image = images[source_index] or {}
        return image.get("name") or image.get("uri")
    return None


def _get_material_info(gltf: Dict[str, Any], material_index: Optional[int]) -> Tuple[Optional[str], List[str]]:
    if material_index is None:
        return None, []
    materials = gltf.get("materials") or []
    if material_index < 0 or material_index >= len(materials):
        return None, []

    material = materials[material_index] or {}
    pbr = material.get("pbrMetallicRoughness") or {}

    texture_candidates = [
        ((pbr.get("baseColorTexture") or {}).get("index")),
        ((pbr.get("metallicRoughnessTexture") or {}).get("index")),
        ((material.get("normalTexture") or {}).get("index")),
        ((material.get("occlusionTexture") or {}).get("index")),
        ((material.get("emissiveTexture") or {}).get("index")),
    ]

    texture_names: List[str] = []
    for idx in texture_candidates:
        name = _get_texture_name(gltf, idx if isinstance(idx, int) else None)
        if name:
            texture_names.append(str(name))

    # de-dupe preserving order
    deduped = list(dict.fromkeys(texture_names))
    return material.get("name"), deduped


def _build_node_metadata(gltf: Dict[str, Any]) -> Dict[int, Dict[str, List[str]]]:
    nodes = gltf.get("nodes") or []
    parent_map: Dict[int, int] = {}

    for parent_idx, node in enumerate(nodes):
        for child in (node.get("children") or []):
            if isinstance(child, int):
                parent_map[child] = parent_idx

    def node_path(node_index: int) -> List[str]:
        path: List[str] = []
        current: Optional[int] = node_index
        while current is not None and 0 <= current < len(nodes):
            node = nodes[current] or {}
            path.insert(0, str(node.get("name") or f"node_{current}"))
            current = parent_map.get(current)
        return path

    mesh_to_meta: Dict[int, Dict[str, List[str]]] = {}
    for node_idx, node in enumerate(nodes):
        mesh_idx = node.get("mesh")
        if not isinstance(mesh_idx, int):
            continue
        info = mesh_to_meta.setdefault(mesh_idx, {"node_names": [], "node_paths": []})
        info["node_names"].append(str(node.get("name") or f"node_{node_idx}"))
        info["node_paths"].append(node_path(node_idx))

    return mesh_to_meta


def _model_bounds(mesh_infos: List[Dict[str, Any]]) -> Optional[Dict[str, List[float]]]:
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    has = False

    for m in mesh_infos:
        center = m.get("center")
        size = m.get("size")
        if not center or not size:
            continue
        has = True
        cx, cy, cz = center
        sx, sy, sz = size
        hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
        min_x = min(min_x, cx - hx)
        min_y = min(min_y, cy - hy)
        min_z = min(min_z, cz - hz)
        max_x = max(max_x, cx + hx)
        max_y = max(max_y, cy + hy)
        max_z = max(max_z, cz + hz)

    if not has:
        return None

    size = [max(max_x - min_x, 1e-6), max(max_y - min_y, 1e-6), max(max_z - min_z, 1e-6)]
    center = [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0]
    return {"min": [min_x, min_y, min_z], "max": [max_x, max_y, max_z], "size": size, "center": center}


def _axis_label(value: float, low: str, high: str) -> str:
    if value < 0.33:
        return low
    if value > 0.66:
        return high
    return "center"


def _estimate_symmetry_count(mesh_infos: List[Dict[str, Any]], target: Dict[str, Any], bounds: Optional[Dict[str, Any]]) -> int:
    if not bounds:
        return 0
    center = target.get("center")
    size = target.get("size")
    if not center or not size:
        return 0

    tx, ty, tz = center
    tsx, tsy, tsz = size
    cx, cy, cz = bounds["center"]
    size_tolerance = 0.22
    position_tolerance = max(bounds["size"]) * 0.05

    def size_similar(a: float, b: float) -> bool:
        if abs(b) < 1e-6:
            return abs(a) < 1e-6
        return abs(a - b) / abs(b) <= size_tolerance

    mirrors = [
        ((2 * cx) - tx, ty, tz),
        (tx, (2 * cy) - ty, tz),
        (tx, ty, (2 * cz) - tz),
    ]

    count = 0
    for mirror in mirrors:
        found = False
        for m in mesh_infos:
            if m.get("index") == target.get("index"):
                continue
            mc = m.get("center")
            ms = m.get("size")
            if not mc or not ms:
                continue
            mx, my, mz = mc
            msx, msy, msz = ms
            pos_dist = math.sqrt((mx - mirror[0]) ** 2 + (my - mirror[1]) ** 2 + (mz - mirror[2]) ** 2)
            if (
                pos_dist <= position_tolerance
                and size_similar(msx, tsx)
                and size_similar(msy, tsy)
                and size_similar(msz, tsz)
            ):
                found = True
                break
        if found:
            count += 1

    return count


def _build_merge_groups(mesh_infos: List[Dict[str, Any]]) -> List[List[int]]:
    if len(mesh_infos) <= 20:
        return []

    candidates = [m for m in mesh_infos if m.get("center") and m.get("size")]
    if len(candidates) <= 20:
        return []

    bounds = _model_bounds(candidates)
    if not bounds:
        return []

    diag = math.sqrt(sum(v * v for v in bounds["size"]))
    center_threshold = max(diag * 0.025, 1e-6)
    size_threshold = 0.2

    parent: Dict[int, int] = {int(m["index"]): int(m["index"]) for m in candidates}

    def find(x: int) -> int:
        p = parent.get(x, x)
        if p == x:
            return x
        root = find(p)
        parent[x] = root
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def size_distance(a: List[float], b: List[float]) -> float:
        ratios = []
        for i in range(3):
            denom = max(abs(b[i]), 1e-6)
            ratios.append(abs(a[i] - b[i]) / denom)
        return max(ratios)

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a = candidates[i]
            b = candidates[j]
            ac, bc = a["center"], b["center"]
            asz, bsz = a["size"], b["size"]
            cdist = math.sqrt((ac[0] - bc[0]) ** 2 + (ac[1] - bc[1]) ** 2 + (ac[2] - bc[2]) ** 2)
            sdist = size_distance(asz, bsz)
            if cdist <= center_threshold and sdist <= size_threshold:
                union(int(a["index"]), int(b["index"]))

    groups: Dict[int, List[int]] = {}
    for m in candidates:
        idx = int(m["index"])
        root = find(idx)
        groups.setdefault(root, []).append(idx)

    return [group for group in groups.values() if len(group) > 1]


def _apply_merged_names(name_map: Dict[str, str], merge_groups: List[List[int]]) -> Dict[str, str]:
    if not merge_groups:
        return name_map

    out = dict(name_map)
    for group in merge_groups:
        existing = [out.get(str(i), "").strip() for i in group]
        existing = [n for n in existing if n]
        if not existing:
            continue

        counts: Dict[str, int] = {}
        for n in existing:
            counts[n] = counts.get(n, 0) + 1
        canonical = sorted(counts.items(), key=lambda x: x[1], reverse=True)[0][0]
        for idx in group:
            out[str(idx)] = canonical

    return out


def get_mesh_infos(gltf: Dict[str, Any]) -> List[Dict[str, Any]]:
    meshes = gltf.get("meshes") or []
    accessors = gltf.get("accessors") or []
    mesh_nodes = _build_node_metadata(gltf)

    mesh_infos: List[Dict[str, Any]] = []
    for idx, mesh in enumerate(meshes):
        primitives = mesh.get("primitives") or []
        center = None
        size = None
        vertex_count = None

        if primitives:
            prim = primitives[0] or {}
            attributes = prim.get("attributes") or {}
            position_accessor_idx = attributes.get("POSITION")
            if isinstance(position_accessor_idx, int) and 0 <= position_accessor_idx < len(accessors):
                acc = accessors[position_accessor_idx] or {}
                mn = acc.get("min")
                mx = acc.get("max")
                if isinstance(mn, list) and isinstance(mx, list) and len(mn) >= 3 and len(mx) >= 3:
                    center = [round((float(mn[i]) + float(mx[i])) / 2.0, 4) for i in range(3)]
                    size = [round(float(mx[i]) - float(mn[i]), 4) for i in range(3)]
                    vertex_count = acc.get("count")

        material_names: List[str] = []
        texture_names: List[str] = []
        for prim in primitives:
            mat_idx = prim.get("material")
            material_name, tx_names = _get_material_info(gltf, mat_idx if isinstance(mat_idx, int) else None)
            if material_name:
                material_names.append(str(material_name))
            texture_names.extend([str(t) for t in tx_names])

        material_names = list(dict.fromkeys(material_names))
        texture_names = list(dict.fromkeys(texture_names))

        node_meta = mesh_nodes.get(idx, {"node_names": [], "node_paths": []})

        mesh_infos.append(
            {
                "index": idx,
                "originalName": mesh.get("name") or f"mesh{idx:02d}",
                "center": center,
                "size": size,
                "vertexCount": vertex_count,
                "materialNames": material_names,
                "textureNames": texture_names,
                "nodeNames": node_meta.get("node_names") or [],
                "nodePaths": node_meta.get("node_paths") or [],
            }
        )

    return mesh_infos


def _compact_label(value: str, max_len: int = 32) -> str:
    cleaned = "".join(ch.lower() if (ch.isalnum() or ch in "_- ") else " " for ch in value)
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned[:max_len]


def _compact_free_text(value: str, max_len: int = 240) -> str:
    cleaned = " ".join(str(value or "").replace("\n", " ").replace("\r", " ").split()).strip()
    return cleaned[:max_len]


def _normalize_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(context, dict):
        return {}

    tags = context.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]

    keywords = context.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = [keywords]

    normalized = {
        "prompt": _compact_free_text(str(context.get("prompt") or ""), 200),
        "title": _compact_free_text(str(context.get("title") or ""), 120),
        "description": _compact_free_text(str(context.get("description") or ""), 320),
        "object_type": _compact_free_text(str(context.get("object_type") or ""), 80),
        "tags": [_compact_label(str(tag), 32) for tag in tags if str(tag).strip()],
        "keywords": [_compact_label(str(keyword), 32) for keyword in keywords if str(keyword).strip()],
    }
    normalized["tags"] = [t for t in normalized["tags"] if t]
    normalized["keywords"] = [k for k in normalized["keywords"] if k]
    return normalized


def _build_context_line(hint: str, context: Dict[str, Any]) -> str:
    parts: List[str] = []
    if hint.strip():
        parts.append(f'user_hint="{_compact_free_text(hint, 120)}"')
    if context.get("prompt"):
        parts.append(f'prompt="{context["prompt"]}"')
    if context.get("object_type"):
        parts.append(f'object_type="{context["object_type"]}"')
    if context.get("title"):
        parts.append(f'title="{context["title"]}"')
    if context.get("keywords"):
        parts.append(f'keywords={json.dumps(context["keywords"])}')
    if context.get("tags"):
        parts.append(f'tags={json.dumps(context["tags"][:12])}')
    if context.get("description"):
        parts.append(f'description="{context["description"]}"')
    return "Model context: " + ("; ".join(parts) if parts else "unknown object.")


def _looks_generic_name(name: str) -> bool:
    lowered = _normalize_name(name, "").lower()
    if not lowered:
        return True
    if len(lowered) <= 2:
        return True
    compact = lowered.replace("_", "")
    generic_whole_names = {
        "object",
        "mesh",
        "material",
        "defaultmaterial",
        "defaultshader",
        "lambert",
        "phong",
        "standardmaterial",
        "node",
        "part",
        "segment",
        "piece",
        "group",
        "item",
        "model",
    }
    if compact in generic_whole_names:
        return True
    if re.fullmatch(r"(object|mesh|material|default_material|defaultmaterial|node|part|segment|piece|group|item|model)_?\d*", lowered):
        return True
    if re.fullmatch(r"[a-z]+(?:_[a-z]+)*_\d+", lowered):
        tokens = lowered.split("_")
        generic_tokens = {
            "object",
            "mesh",
            "material",
            "default",
            "node",
            "part",
            "segment",
            "piece",
            "group",
            "item",
            "model",
        }
        if any(token in generic_tokens for token in tokens):
            return True
    generic_fragments = [
        "object_",
        "mesh_",
        "material_",
        "defaultmaterial",
        "default_material",
        "node_",
        "segment_",
        "part_",
    ]
    if any(fragment in lowered for fragment in generic_fragments):
        return True
    return False


def _fallback_name_from_item(item: Dict[str, Any]) -> str:
    first = ""
    for part in str(item.get("cue", "")).split("|"):
        part = part.strip()
        if part and not _looks_generic_name(part):
            first = part
            break
    if first:
        return _normalize_name(first, f"mesh_{item['i']}")

    quadrant = str(item.get("q") or "").replace("-", "_").strip("_")
    if quadrant:
        return f"part_{quadrant}"
    return f"mesh_{item['i']}"


def _fallback_prefix(hint: str, context: Dict[str, Any]) -> str:
    candidates: List[str] = []
    if context.get("object_type"):
        candidates.append(str(context["object_type"]))
    candidates.extend([str(k) for k in context.get("keywords") or []])
    candidates.extend([str(t) for t in context.get("tags") or []])
    if hint.strip():
        candidates.extend(hint.split(","))

    for value in candidates:
        normalized = _normalize_name(value, "")
        if normalized and not _looks_generic_name(normalized):
            return normalized
    return "model"


def _looks_like_positional_fallback(name: str) -> bool:
    normalized = _normalize_name(name, "")
    return normalized.startswith("part_") or normalized.startswith("mesh_")


def _replace_low_confidence_names(
    name_map: Dict[str, str],
    mesh_infos: List[Dict[str, Any]],
    hint: str,
    context: Dict[str, Any],
) -> Dict[str, str]:
    if not name_map:
        return name_map

    low_confidence = [name for name in name_map.values() if _looks_like_positional_fallback(name)]
    if len(low_confidence) < max(3, math.ceil(len(name_map) * 0.6)):
        return name_map

    prefix = _fallback_prefix(hint, context)
    replaced: Dict[str, str] = {}
    for order, mesh in enumerate(sorted(mesh_infos, key=lambda item: int(item["index"])), start=1):
        idx = str(mesh["index"])
        replaced[idx] = f"{prefix}_part_{order:02d}"
    return replaced


def _infer_semantic_category(hint: str, context: Dict[str, Any]) -> str:
    text_parts = [
        str(context.get("object_type") or ""),
        str(context.get("prompt") or ""),
        str(context.get("title") or ""),
        str(context.get("description") or ""),
        " ".join(str(v) for v in context.get("keywords") or []),
        " ".join(str(v) for v in context.get("tags") or []),
        hint,
    ]
    text = _normalize_name(" ".join(text_parts), "")

    if any(word in text for word in ["dog", "cat", "horse", "lion", "tiger", "wolf", "animal", "pet", "mammal", "canine", "feline"]):
        return "animal"
    if any(word in text for word in ["human", "person", "character", "humanoid", "man", "woman", "warrior", "knight", "soldier"]):
        return "humanoid"
    if any(word in text for word in ["car", "vehicle", "truck", "bus", "automobile", "van", "suv", "jeep"]):
        return "vehicle"
    return ""


def _mesh_norm_axes(mesh: Dict[str, Any], bounds: Optional[Dict[str, Any]]) -> Tuple[float, float, float]:
    if not bounds or not mesh.get("center"):
        return 0.5, 0.5, 0.5
    c = mesh["center"]
    return (
        max(0.0, min(1.0, (c[0] - bounds["min"][0]) / bounds["size"][0])),
        max(0.0, min(1.0, (c[1] - bounds["min"][1]) / bounds["size"][1])),
        max(0.0, min(1.0, (c[2] - bounds["min"][2]) / bounds["size"][2])),
    )


def _mesh_rel_volume(mesh: Dict[str, Any], bounds: Optional[Dict[str, Any]]) -> float:
    if not bounds or not mesh.get("size"):
        return 0.0
    size = mesh["size"]
    denom = max(bounds["size"][0] * bounds["size"][1] * bounds["size"][2], 1e-6)
    return max(0.0, min(1.0, (size[0] * size[1] * size[2]) / denom))


def _heuristic_name_for_mesh(mesh: Dict[str, Any], bounds: Optional[Dict[str, Any]], category: str) -> str:
    x, y, z = _mesh_norm_axes(mesh, bounds)
    volume = _mesh_rel_volume(mesh, bounds)
    side = "left" if x < 0.45 else "right" if x > 0.55 else "center"

    if category == "animal":
        if z > 0.72:
            if y > 0.72 and volume < 0.08 and side != "center":
                return f"{side}_ear"
            if y > 0.48:
                return "head"
            return "snout"
        if z < 0.22 and y > 0.38 and volume < 0.08:
            return "tail"
        if y < 0.38:
            if z > 0.52:
                return f"{side}_front_leg" if side != "center" else "front_leg"
            return f"{side}_hind_leg" if side != "center" else "hind_leg"
        if y > 0.72:
            return "back"
        if z > 0.55:
            return "chest"
        if z < 0.38:
            return "hindquarters"
        if y < 0.5:
            return "belly"
        return "torso"

    if category == "humanoid":
        if y > 0.8:
            return "head"
        if y < 0.25:
            return f"{side}_leg" if side != "center" else "legs"
        if side != "center" and y > 0.35 and y < 0.78:
            return f"{side}_arm"
        if y > 0.6:
            return "upper_torso"
        if y > 0.35:
            return "torso"
        return "pelvis"

    if category == "vehicle":
        if y < 0.3 and side != "center":
            if z > 0.55:
                return f"{side}_front_wheel"
            if z < 0.45:
                return f"{side}_rear_wheel"
        if y > 0.8:
            return "roof"
        if z > 0.78:
            if y > 0.55:
                return "windshield"
            return "front_bumper"
        if z > 0.6:
            if y > 0.45:
                return "hood"
            return "front_body"
        if z < 0.2:
            if y > 0.5:
                return "rear_window"
            return "rear_bumper"
        if z < 0.38:
            if y > 0.45:
                return "trunk"
            return "rear_body"
        return "body"

    return ""


def _replace_low_confidence_with_heuristics(
    mesh_infos: List[Dict[str, Any]],
    hint: str,
    context: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    category = _infer_semantic_category(hint, context)
    if not category:
        return None

    bounds = _model_bounds(mesh_infos)
    if not bounds:
        return None

    out: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    for mesh in sorted(mesh_infos, key=lambda item: int(item["index"])):
        base = _heuristic_name_for_mesh(mesh, bounds, category)
        if not base:
            return None
        seen[base] = seen.get(base, 0) + 1
        final_name = base if seen[base] == 1 else f"{base}_{seen[base]}"
        out[str(mesh["index"])] = final_name
    return out


def _count_category_term_hits(names: Dict[str, str], terms: List[str]) -> int:
    if not names or not terms:
        return 0

    total = 0
    term_set = set(terms)
    for raw_name in names.values():
        normalized = _normalize_name(raw_name, "")
        if not normalized:
            continue
        tokens = normalized.split("_")
        if any(token in term_set for token in tokens):
            total += 1
    return total


def _needs_category_override(
    names: Dict[str, str],
    expected_category: str,
) -> bool:
    if not names or not expected_category:
        return False

    category_terms = {
        "animal": [
            "head", "snout", "ear", "tail", "front", "hind", "leg",
            "back", "chest", "belly", "torso", "hindquarters",
        ],
        "humanoid": [
            "head", "arm", "leg", "torso", "upper", "pelvis",
            "hand", "foot", "shoulder", "neck",
        ],
        "vehicle": [
            "wheel", "tire", "roof", "hood", "body", "bumper",
            "trunk", "window", "windshield", "door", "fender",
            "chassis", "grille", "mirror",
        ],
    }

    expected_hits = _count_category_term_hits(names, category_terms.get(expected_category, []))
    strongest_other_hits = 0
    for category, terms in category_terms.items():
        if category == expected_category:
            continue
        strongest_other_hits = max(strongest_other_hits, _count_category_term_hits(names, terms))

    total = len(names)
    if total == 0:
        return False

    mismatch_threshold = max(3, math.ceil(total * 0.25))
    return expected_hits == 0 and strongest_other_hits >= mismatch_threshold


def _disambiguate_duplicate_names(
    name_map: Dict[str, str],
    mesh_infos: List[Dict[str, Any]],
    merge_groups: List[List[int]],
) -> Dict[str, str]:
    protected_pairs = set()
    for group in merge_groups:
        for idx in group:
            protected_pairs.add(int(idx))

    mesh_by_index = {int(m["index"]): m for m in mesh_infos}
    groups_by_name: Dict[str, List[int]] = {}
    for idx_str, name in name_map.items():
        groups_by_name.setdefault(name, []).append(int(idx_str))

    out = dict(name_map)
    for name, indices in groups_by_name.items():
        if len(indices) <= 1:
            continue

        unprotected = [idx for idx in indices if idx not in protected_pairs]
        if len(unprotected) <= 1:
            continue

        seen: Dict[str, int] = {}
        for idx in sorted(unprotected):
            mesh = mesh_by_index.get(idx, {})
            center = mesh.get("center") or []
            axis_parts: List[str] = []
            if len(center) >= 3:
                axis_parts = [
                    "left" if center[0] < -1e-6 else "right" if center[0] > 1e-6 else "center",
                    "low" if center[1] < -1e-6 else "high" if center[1] > 1e-6 else "mid",
                    "back" if center[2] < -1e-6 else "front" if center[2] > 1e-6 else "center",
                ]
            suffix = "_".join(axis_parts) if axis_parts else f"idx_{idx}"
            candidate = _normalize_name(f"{name}_{suffix}", f"{name}_{idx}")
            seen[candidate] = seen.get(candidate, 0) + 1
            if seen[candidate] > 1:
                candidate = f"{candidate}_{seen[candidate]}"
            out[str(idx)] = candidate

    return out


def _chunk_by_budget(items: List[Dict[str, Any]], max_chars: int, max_items: int) -> List[List[Dict[str, Any]]]:
    out: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_chars = 2

    for item in items:
        chars = len(json.dumps(item)) + 1
        if cur and (cur_chars + chars > max_chars or len(cur) >= max_items):
            out.append(cur)
            cur = [item]
            cur_chars = 2 + chars
        else:
            cur.append(item)
            cur_chars += chars

    if cur:
        out.append(cur)
    return out


def _try_parse_object(text: str) -> Optional[Dict[str, Any]]:
    t = text.strip()
    if not t:
        return None
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(t[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            return None
    return None


def _normalize_name(name: str, fallback: str) -> str:
    n = name.strip().lower()
    out = []
    last_underscore = False
    for ch in n:
        if ch.isalnum() or ch == "_":
            out.append(ch)
            last_underscore = (ch == "_")
        else:
            if not last_underscore:
                out.append("_")
                last_underscore = True
    normalized = "".join(out).strip("_")
    return normalized or fallback


async def _chat_json(
    client: OpenAI,
    model: str,
    prompt: str,
    max_tokens: int,
) -> Tuple[str, Optional[str], bool]:
    loop = asyncio.get_running_loop()

    def _call() -> Any:
        return client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_completion_tokens=max_tokens,
        )

    response = await loop.run_in_executor(None, _call)

    choice = response.choices[0]
    content = choice.message.content or ""
    finish_reason = getattr(choice, "finish_reason", None)
    had_reasoning_only = (not content) and bool(getattr(choice.message, "reasoning_content", None))
    return content, finish_reason, had_reasoning_only


async def rename_meshes(
    mesh_infos: List[Dict[str, Any]],
    hint: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not mesh_infos:
        return {"names": {}, "mesh_count": 0, "logs": ["No meshes found."]}

    client = _create_client()
    bounds = _model_bounds(mesh_infos)
    merge_groups = _build_merge_groups(mesh_infos)
    normalized_context = _normalize_context(context)

    payload: List[Dict[str, Any]] = []
    for m in mesh_infos:
        ratio = None
        normalized = None
        quadrant = None

        if bounds and m.get("size"):
            size = m["size"]
            ratio = {
                "x": round(size[0] / bounds["size"][0], 3),
                "y": round(size[1] / bounds["size"][1], 3),
                "z": round(size[2] / bounds["size"][2], 3),
            }

        if bounds and m.get("center"):
            c = m["center"]
            normalized = {
                "x": round((c[0] - bounds["min"][0]) / bounds["size"][0], 3),
                "y": round((c[1] - bounds["min"][1]) / bounds["size"][1], 3),
                "z": round((c[2] - bounds["min"][2]) / bounds["size"][2], 3),
            }
            quadrant = (
                f"{_axis_label(normalized['x'], 'left', 'right')}-"
                f"{_axis_label(normalized['y'], 'bottom', 'top')}-"
                f"{_axis_label(normalized['z'], 'back', 'front')}"
            )

        parts = [
            _compact_label(str(m.get("originalName", "")), 18),
            _compact_label(str((m.get("materialNames") or [""])[0] or ""), 18),
            _compact_label(str((m.get("textureNames") or [""])[0] or ""), 18),
            _compact_label(str((m.get("nodeNames") or [""])[0] or ""), 18),
        ]
        cue = "|".join([p for p in dict.fromkeys(parts) if p])[:72]

        vr = None
        if ratio:
            vr = round(ratio["x"] * ratio["y"] * ratio["z"], 4)

        payload.append(
            {
                "i": int(m["index"]),
                "q": quadrant,
                "vr": vr,
                "sy": _estimate_symmetry_count(mesh_infos, m, bounds),
                "cue": cue,
            }
        )

    payload_json = json.dumps(payload, separators=(",", ":"))
    use_single_request = len(mesh_infos) <= 400 and len(payload_json) <= 45000
    if use_single_request:
        chunks = [payload]
    else:
        chunks = _chunk_by_budget(payload, max_chars=45000, max_items=200)

    context_line = _build_context_line(hint, normalized_context)
    merge_instruction = (
        "If multiple meshes are very close and size-similar, reuse the same semantic name."
        if len(mesh_infos) > 20
        else "Use semantic names and reuse only for truly equivalent parts."
    )

    logs: List[str] = [f"Prepared {len(mesh_infos)} meshes in {len(chunks)} AI request(s)."]
    name_map: Dict[str, str] = {}
    for i, chunk in enumerate(chunks):
        label = f"chunk {i + 1}/{len(chunks)}"
        chunk_indices = {int(x["i"]) for x in chunk}
        chunk_index_strings = {str(idx) for idx in chunk_indices}
        relevant_groups = [
            [idx for idx in group if idx in chunk_indices]
            for group in merge_groups
        ]
        relevant_groups = [g for g in relevant_groups if len(g) > 1]

        group_line = (
            f"Close groups in this chunk: {json.dumps(relevant_groups)}."
            if relevant_groups
            else "No close groups in this chunk."
        )

        prompt = (
            "Return ONLY JSON object mapping mesh index string to lowercase_snake_case name.\n"
            f"{context_line}\n"
            f"{merge_instruction}\n"
            f"{group_line}\n"
            "Field legend: i=index, q=quadrant, vr=relative volume ratio, sy=symmetry estimate, cue=name hint tokens.\n"
            "Rules: Use the model context as the primary semantic guide and use cue/q/vr/sy only for disambiguation.\n"
            "Prefer specific semantic names such as head, wheel, handle, roof, wing, torso, base, blade, column, screen, or ornament when supported by context.\n"
            "Never return placeholder-like names such as object_*, mesh_*, material_*, node_*, part_* unless there is truly no semantic evidence.\n"
            "Cover every mesh index exactly once.\n"
            "No prose. No markdown. Only JSON.\n"
            "Return format: { \"0\": \"name\", \"1\": \"name\" }\n"
            f"Mesh metadata: {json.dumps(chunk, separators=(',', ':'))}"
        )

        max_tokens = min(3500, max(900, len(chunk) * 18))
        content, finish_reason, had_reasoning_only = await _chat_json(
            client=client,
            model=GLM_MODEL,
            prompt=prompt,
            max_tokens=max_tokens,
        )

        parsed = _try_parse_object(content)
        parsed_chunk: Dict[str, str] = {}
        if parsed:
            for k, v in parsed.items():
                if str(k) not in chunk_index_strings:
                    continue
                if isinstance(v, str) and not _looks_generic_name(v):
                    parsed_chunk[str(k)] = v

        if len(parsed_chunk) != len(chunk):
            reason_bits = []
            if finish_reason:
                reason_bits.append(f"finish_reason={finish_reason}")
            if had_reasoning_only:
                reason_bits.append("reasoning_only")
            missing_count = len(chunk) - len(parsed_chunk)
            if missing_count > 0:
                reason_bits.append(f"fallback_count={missing_count}")
            reason_text = f" ({', '.join(reason_bits)})" if reason_bits else ""
            logs.append(f"{label} returned incomplete JSON{reason_text}; using cue fallback for missing names")

        for item in chunk:
            idx = str(item["i"])
            if idx in parsed_chunk:
                name_map[idx] = parsed_chunk[idx]
                continue
            name_map[idx] = _fallback_name_from_item(item)

    normalized: Dict[str, str] = {}
    for m in mesh_infos:
        idx = str(m["index"])
        raw = name_map.get(idx, f"mesh_{idx}")
        normalized[idx] = _normalize_name(raw, f"mesh_{idx}")

    low_confidence_count = sum(1 for name in normalized.values() if _looks_like_positional_fallback(name))
    if low_confidence_count >= max(3, math.ceil(len(normalized) * 0.6)):
        heuristic_names = _replace_low_confidence_with_heuristics(mesh_infos, hint, normalized_context)
        if heuristic_names:
            logs.append(
                f"Semantic naming confidence was low ({low_confidence_count}/{len(normalized)} fallback-style names); "
                "using category-aware positional heuristics instead."
            )
            normalized = heuristic_names
        else:
            logs.append(
                f"Semantic naming confidence was low ({low_confidence_count}/{len(normalized)} fallback-style names); "
                "using ordered object-part labels instead."
            )
            normalized = _replace_low_confidence_names(normalized, mesh_infos, hint, normalized_context)

    inferred_category = _infer_semantic_category(hint, normalized_context)
    if _needs_category_override(normalized, inferred_category):
        heuristic_names = _replace_low_confidence_with_heuristics(mesh_infos, hint, normalized_context)
        if heuristic_names:
            logs.append(
                f"Generated names conflicted with inferred '{inferred_category}' context; "
                "overriding with category-aware heuristics."
            )
            normalized = heuristic_names

    merged = _apply_merged_names(normalized, merge_groups)
    merged = _disambiguate_duplicate_names(merged, mesh_infos, merge_groups)
    return {
        "names": merged,
        "mesh_count": len(mesh_infos),
        "logs": logs,
    }


async def rename_meshes_from_file(
    filename: str,
    data: bytes,
    hint: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if ext == "glb":
        gltf = parse_glb(data)
    elif ext == "gltf":
        gltf = parse_gltf(data)
    else:
        raise ValueError("Only .glb and .gltf files are supported")

    mesh_infos = get_mesh_infos(gltf)
    result = await rename_meshes(mesh_infos, hint=hint, context=context)
    result["mesh_infos"] = mesh_infos
    return result
