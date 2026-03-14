import asyncio
import json
import math
import os
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
            max_tokens=max_tokens,
        )

    response = await loop.run_in_executor(None, _call)

    choice = response.choices[0]
    content = choice.message.content or ""
    finish_reason = getattr(choice, "finish_reason", None)
    had_reasoning_only = (not content) and bool(getattr(choice.message, "reasoning_content", None))
    return content, finish_reason, had_reasoning_only


async def rename_meshes(mesh_infos: List[Dict[str, Any]], hint: str = "") -> Dict[str, Any]:
    if not mesh_infos:
        return {"names": {}, "mesh_count": 0, "logs": ["No meshes found."]}

    client = _create_client()
    bounds = _model_bounds(mesh_infos)
    merge_groups = _build_merge_groups(mesh_infos)

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

    max_chars = 9000 if len(mesh_infos) > 250 else 11000 if len(mesh_infos) > 120 else 14000
    max_items = 45 if len(mesh_infos) > 250 else 60
    chunks = _chunk_by_budget(payload, max_chars=max_chars, max_items=max_items)

    hint_line = f'User hint: "{hint.strip()}".' if hint.strip() else "User hint: unknown object type."
    merge_instruction = (
        "If multiple meshes are very close and size-similar, reuse the same semantic name."
        if len(mesh_infos) > 20
        else "Use semantic names and reuse only for truly equivalent parts."
    )

    logs: List[str] = [f"Prepared {len(mesh_infos)} meshes in {len(chunks)} chunks."]
    name_map: Dict[str, str] = {}

    queue: List[Tuple[List[Dict[str, Any]], int, str]] = [
        (chunk, 0, f"chunk {i + 1}/{len(chunks)}") for i, chunk in enumerate(chunks)
    ]

    while queue:
        chunk, depth, label = queue.pop(0)
        chunk_indices = {int(x["i"]) for x in chunk}
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
            f"{hint_line}\n"
            f"{merge_instruction}\n"
            f"{group_line}\n"
            "Field legend: i=index, q=quadrant, vr=relative volume ratio, sy=symmetry estimate, cue=name hint tokens.\n"
            "Rules: Use cue as primary naming signal and q/vr/sy for disambiguation.\n"
            "No prose. No markdown. Only JSON.\n"
            "Return format: { \"0\": \"name\", \"1\": \"name\" }\n"
            f"Mesh metadata: {json.dumps(chunk)}"
        )

        content, finish_reason, had_reasoning_only = await _chat_json(
            client=client,
            model=GLM_MODEL,
            prompt=prompt,
            max_tokens=900,
        )

        parsed = _try_parse_object(content)
        parsed_chunk: Optional[Dict[str, str]] = None
        if parsed:
            parsed_chunk = {}
            for k, v in parsed.items():
                if str(k) not in {str(i) for i in chunk_indices}:
                    continue
                if isinstance(v, str):
                    parsed_chunk[str(k)] = v

        if parsed_chunk:
            name_map.update(parsed_chunk)
            continue

        can_split = len(chunk) > 1 and depth < 3
        likely_budget_issue = finish_reason == "length" or had_reasoning_only
        if can_split and likely_budget_issue:
            mid = math.ceil(len(chunk) / 2)
            left = chunk[:mid]
            right = chunk[mid:]
            logs.append(f"{label} returned no JSON; splitting into {len(left)} + {len(right)}")
            queue.insert(0, (right, depth + 1, f"{label}.b"))
            queue.insert(0, (left, depth + 1, f"{label}.a"))
            continue

        logs.append(f"{label} fallback to cue-based naming")
        for item in chunk:
            first = ""
            for part in str(item.get("cue", "")).split("|"):
                if part.strip():
                    first = part.strip()
                    break
            fallback = _normalize_name(first or "mesh", f"mesh_{item['i']}")
            name_map[str(item["i"])] = fallback

    normalized: Dict[str, str] = {}
    for m in mesh_infos:
        idx = str(m["index"])
        raw = name_map.get(idx, f"mesh_{idx}")
        normalized[idx] = _normalize_name(raw, f"mesh_{idx}")

    merged = _apply_merged_names(normalized, merge_groups)
    return {
        "names": merged,
        "mesh_count": len(mesh_infos),
        "logs": logs,
    }


async def rename_meshes_from_file(filename: str, data: bytes, hint: str = "") -> Dict[str, Any]:
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if ext == "glb":
        gltf = parse_glb(data)
    elif ext == "gltf":
        gltf = parse_gltf(data)
    else:
        raise ValueError("Only .glb and .gltf files are supported")

    mesh_infos = get_mesh_infos(gltf)
    result = await rename_meshes(mesh_infos, hint=hint)
    result["mesh_infos"] = mesh_infos
    return result
