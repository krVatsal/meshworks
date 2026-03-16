"""
SAMPart3D-Inspired GLB Segmentation
=====================================
Based on: "SAMPart3D: Segment Any Part in 3D Objects" (Yang et al., 2024)
          https://arxiv.org/abs/2411.07184

Core pipeline from the paper:
  1. Multi-view rendering  (16 views, RGB + depth + camera matrices)
  2. 2D feature extraction — DINOv2 if available, else handcrafted image features
  3. Feature lifting  2D→3D  (unproject depth pixels, accumulate onto vertices)
  4. Scale-conditioned clustering  (multi-granularity k-means)
  5. Geodesic boundary smoothing

Strategy auto-selection:
  Multiple loose parts        ->  connectivity  (Blender loose-parts)
  Single hard-surface mesh    ->  sharp-edge split
  Single smooth/organic mesh  ->  SAMPart3D multi-view feature lifting

Usage:
  python segment_glb.py input.glb output.glb
  python segment_glb.py input.glb output.glb --granularity fine
  python segment_glb.py input.glb output.glb --granularity coarse
  python segment_glb.py input.glb output.glb -n 8
  python segment_glb.py input.glb output.glb --strategy multiview
  python segment_glb.py input.glb output.glb --views 16 --img-size 512
"""

import os, sys, warnings, argparse, platform

# ── Cross-platform OpenGL backend selection ───────────────────────────────────
# EGL   = Linux headless (servers, CI)
# osmesa= Windows / macOS / Linux fallback (pure software renderer)
# auto  = let pyrender pick (works when a display is available, e.g. desktop)
def _setup_opengl_platform():
    system = platform.system()
    if "PYOPENGL_PLATFORM" in os.environ:
        return  # already set by caller
    if system == "Linux":
        # Try EGL first (GPU-accelerated headless); fall back to osmesa
        try:
            os.environ["PYOPENGL_PLATFORM"] = "egl"
            import OpenGL.EGL  # probe
        except Exception:
            os.environ["PYOPENGL_PLATFORM"] = "osmesa"
    elif system == "Windows":
        # EGL not available on stock Windows — use osmesa (software)
        # osmesa must be installed: pip install pyopengl pyopengl-demo
        # or download Mesa3D DLL from https://fdossena.com/?p=mesa/index.frag
        # and place opengl32.dll / osmesa.dll next to the script.
        os.environ["PYOPENGL_PLATFORM"] = "osmesa"
    else:
        # macOS or other — let pyrender auto-detect
        pass

_setup_opengl_platform()

import numpy as np
import trimesh
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import KDTree
warnings.filterwarnings("ignore")

try:
    import torch
    from transformers import AutoImageProcessor, AutoModel
    from PIL import Image as PILImage
    DINO_AVAILABLE = True
    print("[dino]  DINOv2 available")
except ImportError:
    DINO_AVAILABLE = False
    print("[dino]  DINOv2 not available — using handcrafted image features")

try:
    import pyrender
    PYRENDER_AVAILABLE = True
except (ImportError, OSError, RuntimeError, TypeError) as e:
    PYRENDER_AVAILABLE = False
    print(f"[warn]  pyrender not available ({type(e).__name__}: {e})")
    print("[warn]  Multi-view strategy disabled — will use skeleton fallback")

PALETTE = np.array([
    [0.922,0.267,0.267],[0.267,0.675,0.922],[0.267,0.922,0.455],
    [0.922,0.816,0.267],[0.714,0.267,0.922],[0.922,0.545,0.267],
    [0.267,0.922,0.882],[0.922,0.267,0.675],[0.455,0.922,0.267],
    [0.267,0.384,0.922],[0.922,0.698,0.545],[0.545,0.922,0.698],
    [0.698,0.545,0.922],[0.922,0.922,0.455],[0.455,0.698,0.922],
    [0.922,0.455,0.545],[0.600,0.800,0.400],[0.800,0.400,0.600],
    [0.400,0.600,0.800],[0.800,0.700,0.300],
], dtype=np.float32)

GRANULARITY = {"coarse": 4, "medium": 7, "fine": 12}


# ─────────────────────────────────────────────────────────────────────────────
# Load
# ─────────────────────────────────────────────────────────────────────────────

def load_glb(path):
    """
    Returns a list of (name, mesh) tuples — one per sub-mesh in the GLB.
    Sub-meshes are kept separate so their individual geometry / texture /
    UV islands are preserved and segmentation is run per-mesh.
    """
    print(f"[load]  {os.path.basename(path)}")
    obj = trimesh.load(path, force="scene", process=False)
    if isinstance(obj, trimesh.Scene):
        named = [(name, geom) for name, geom in obj.geometry.items()
                 if isinstance(geom, trimesh.Trimesh)]
        if not named:
            sys.exit("[error] No triangle meshes found.")
        print(f"[load]  {len(named)} sub-mesh(es):")
        result = []
        for name, m in named:
            m.process(validate=True)
            print(f"[load]    {name}: {len(m.vertices):,} verts, {len(m.faces):,} faces")
            result.append((name, m))
        return result
    else:
        obj.process(validate=True)
        print(f"[load]  1 mesh: {len(obj.vertices):,} verts, {len(obj.faces):,} faces")
        return [("Mesh", obj)]


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — Connectivity
# ─────────────────────────────────────────────────────────────────────────────

def connectivity_segments(mesh):
    n = len(mesh.faces)
    adj = mesh.face_adjacency
    row = np.concatenate([adj[:,0], adj[:,1]])
    col = np.concatenate([adj[:,1], adj[:,0]])
    G = csr_matrix((np.ones(len(row), np.int8),(row,col)), shape=(n,n))
    nc, labels = connected_components(G, directed=False)
    print(f"[conn]  {nc} connected component(s)")
    return labels, nc

def sharp_edge_segments(mesh, angle_deg=45.0):
    thr = np.radians(angle_deg)
    adj = mesh.face_adjacency
    ang = mesh.face_adjacency_angles
    sm = adj[ang < thr]
    n = len(mesh.faces)
    if len(sm) == 0:
        return np.arange(n), n
    row = np.concatenate([sm[:,0], sm[:,1]])
    col = np.concatenate([sm[:,1], sm[:,0]])
    G = csr_matrix((np.ones(len(row), np.int8),(row,col)), shape=(n,n))
    nc, labels = connected_components(G, directed=False)
    counts = np.bincount(labels, minlength=nc)
    large = np.where(counts >= max(3, int(n*0.001)))[0]
    if len(large):
        for s in np.where(counts < max(3, int(n*0.001)))[0]:
            labels[labels==s] = large[0]
        _, labels = np.unique(labels, return_inverse=True)
        nc = int(labels.max()) + 1
    print(f"[sharp] {nc} segment(s) at >{angle_deg} deg")
    return labels, nc


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — SAMPart3D multi-view feature lifting
# ─────────────────────────────────────────────────────────────────────────────

def get_camera_poses(n_views, radius):
    """Fibonacci sphere distribution — same idea as paper's 16-view setup."""
    poses = []
    golden = np.pi * (3 - np.sqrt(5))
    for i in range(n_views):
        y   = 1 - (i / max(n_views-1,1)) * 2
        r   = np.sqrt(max(0, 1-y*y))
        az  = golden * i
        eye = np.array([r*np.cos(az), y, r*np.sin(az)]) * radius
        fwd = -eye / (np.linalg.norm(eye)+1e-8)
        up  = np.array([0.,1.,0.]) if abs(fwd[1]) < 0.99 else np.array([1.,0.,0.])
        right = np.cross(fwd, up); right /= np.linalg.norm(right)+1e-8
        up    = np.cross(right, fwd)
        mat = np.eye(4)
        mat[:3,0] = right; mat[:3,1] = up; mat[:3,2] = -fwd; mat[:3,3] = eye
        poses.append(mat)
    return poses


class DINOv2Extractor:
    def __init__(self):
        print("[feat]  Loading DINOv2 ViT-B/14 ...")
        self.proc  = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
        self.model = AutoModel.from_pretrained("facebook/dinov2-base")
        self.model.eval()
        self.patch = 14
    def extract(self, rgb):
        H, W = rgb.shape[:2]
        with torch.no_grad():
          inp  = self.proc(images=PILImage.fromarray(rgb), return_tensors="pt")
          out  = self.model(**inp)
        toks = out.last_hidden_state[:,1:,:]
        ph, pw = H//self.patch, W//self.patch
        f = toks.squeeze(0).reshape(ph,pw,-1).numpy()
        f = np.repeat(np.repeat(f, self.patch, 0), self.patch, 1)
        return f[:H,:W].astype(np.float32)


class HandcraftedExtractor:
    """
    9-channel feature map: LAB color + Sobel XY + 4-orientation gradient mags.
    Smoothed at patch scale to mimic ViT pooling.
    Clusters by appearance — naturally groups face/torso/limbs by their
    surface color and texture profile.
    """
    def extract(self, rgb):
        from scipy.ndimage import uniform_filter, sobel
        img = rgb.astype(np.float32)/255.0
        H, W = img.shape[:2]
        # sRGB -> LAB
        lin = np.where(img>0.04045, ((img+0.055)/1.055)**2.4, img/12.92)
        M = np.array([[0.4124,0.3576,0.1805],[0.2126,0.7152,0.0722],[0.0193,0.1192,0.9505]])
        xyz = lin @ M.T / np.array([0.9505,1.0,1.0890])
        xyz = np.where(xyz>0.008856, xyz**(1/3), 7.787*xyz+16/116)
        lab = np.stack([116*xyz[...,1]-16, 500*(xyz[...,0]-xyz[...,1]),
                        200*(xyz[...,1]-xyz[...,2])], axis=-1)
        lab = lab / np.array([100,127,127])
        # Gradients
        gray = 0.299*img[...,0]+0.587*img[...,1]+0.114*img[...,2]
        sx = sobel(gray, axis=1); sy = sobel(gray, axis=0)
        mag = np.sqrt(sx**2+sy**2+1e-8)
        ang = np.arctan2(sy,sx)
        hog = np.stack([mag*np.cos(ang-t)**2 for t in [0,np.pi/4,np.pi/2,3*np.pi/4]], axis=-1)
        feats = np.concatenate([lab, sx[...,None], sy[...,None], hog], axis=-1)
        scale = max(H,W)//16
        for c in range(feats.shape[-1]):
            feats[...,c] = uniform_filter(feats[...,c], size=scale)
        return feats.astype(np.float32)


def render_multiview(mesh, n_views, img_size):
    """Render N views. Returns list of dicts with rgb, depth, K, E."""
    centre = mesh.bounding_box.centroid
    scale  = mesh.bounding_sphere.primitive.radius
    scale  = scale if scale > 1e-6 else 1.0
    vn     = (np.array(mesh.vertices)-centre)/scale
    try:
        vc = mesh.visual.to_color().vertex_colors
    except Exception:
        vc = None
    mn = trimesh.Trimesh(vertices=vn, faces=mesh.faces,
                          vertex_colors=vc, process=False)
    mn.fix_normals()

    yfov   = np.pi/3.0
    radius = 2.2
    fx     = img_size/(2*np.tan(yfov/2))
    K = np.array([[fx,0,img_size/2],[0,fx,img_size/2],[0,0,1]], dtype=np.float64)

    # Strip texture/UV data — pyrender crashes on ctypes if mesh has textures
    bare = trimesh.Trimesh(vertices=mn.vertices, faces=mn.faces, process=False)
    bare.fix_normals()
    mat     = pyrender.MetallicRoughnessMaterial(
                  baseColorFactor=[0.8, 0.7, 0.6, 1.0],
                  metallicFactor=0.0, roughnessFactor=0.8)
    py_mesh = pyrender.Mesh.from_trimesh(bare, material=mat, smooth=True)
    scene   = pyrender.Scene(ambient_light=[0.6,0.6,0.6], bg_color=[0,0,0,0])
    scene.add(py_mesh)
    # Point lights — avoid DirectionalLight which triggers shadow map textures
    for pos in [[2,2,2],[-2,1,0],[0,-2,1]]:
        pl = pyrender.PointLight(color=[1,1,1], intensity=8.0)
        lp = np.eye(4); lp[:3,3] = pos
        scene.add(pl, pose=lp)

    cam      = pyrender.PerspectiveCamera(yfov=yfov, aspectRatio=1.0, znear=0.01, zfar=100.)
    renderer = pyrender.OffscreenRenderer(img_size, img_size)
    poses    = get_camera_poses(n_views, radius)
    results  = []

    print(f"[render] {n_views} views at {img_size}x{img_size} ...")
    for i, pose in enumerate(poses):
        cn = scene.add(cam, pose=pose)
        color, depth = renderer.render(scene, flags=pyrender.RenderFlags.RGBA)
        scene.remove_node(cn)
        results.append({"rgb": color[:,:,:3], "depth": depth.astype(np.float32),
                         "K": K.copy(), "E": np.linalg.inv(pose)})
        if (i+1)%4==0:
            print(f"[render]   {i+1}/{n_views}")

    renderer.delete()
    return results, centre, scale


def lift_features(mesh, views, extractor, centre, scale):
    """
    Core feature-lifting step (SAMPart3D §3.2):
    Unproject each valid depth pixel -> 3D point -> nearest vertex.
    Accumulate 2D features weighted by proximity.
    """
    vn    = (np.array(mesh.vertices)-centre)/scale
    N     = len(vn)
    kd    = KDTree(vn)
    fdim  = None
    fsum  = None
    fcnt  = np.zeros(N, dtype=np.float32)

    print(f"[lift]  Projecting features onto {N:,} vertices ...")
    for vi, v in enumerate(views):
        f2d = extractor.extract(v["rgb"])
        if fdim is None:
            fdim = f2d.shape[-1]; fsum = np.zeros((N,fdim), dtype=np.float64)
        H, W = v["depth"].shape
        fx,fy,cx,cy = v["K"][0,0],v["K"][1,1],v["K"][0,2],v["K"][1,2]
        uu,vv = np.meshgrid(np.arange(W), np.arange(H))
        uu=uu.ravel().astype(np.float32); vv=vv.ravel().astype(np.float32)
        dd=v["depth"].ravel()
        ok=(dd>0)
        uu,vv,dd,ff = uu[ok],vv[ok],dd[ok],f2d.reshape(-1,fdim)[ok]
        if len(uu)==0: continue
        xc=(uu-cx)/fx*dd; yc=(vv-cy)/fy*dd
        dd_neg=-dd  # OpenGL -Z convention
        pts=np.stack([xc,yc,dd_neg,np.ones_like(dd)],1)
        C2W=np.linalg.inv(v["E"])
        pw=(C2W@pts.T).T[:,:3]
        dist,idx=kd.query(pw,k=1)
        idx=idx.ravel(); dist=dist.ravel()
        w=1.0/(dist+1e-4)
        np.add.at(fsum, idx, ff*w[:,None])
        np.add.at(fcnt, idx, w)
        if (vi+1)%4==0:
            print(f"[lift]    view {vi+1}/{len(views)}, pixels={ok.sum():,}")

    obs = fcnt>0
    out = np.zeros((N,fdim), dtype=np.float32)
    out[obs] = (fsum[obs]/fcnt[obs,None]).astype(np.float32)
    if not obs.all():
        obs_v = np.where(obs)[0]
        _, nn = KDTree(vn[obs]).query(vn[~obs], k=1)
        out[~obs] = out[obs_v[nn.ravel()]]
    print(f"[lift]  Done. Feature dim={fdim}, unobserved={100*(~obs).sum()/N:.1f}%")
    return out


def cluster_features(feats, n_segments):
    print(f"[cluster] k-means k={n_segments} ...")
    X = StandardScaler().fit_transform(feats)
    labels = KMeans(n_clusters=n_segments, n_init=15, max_iter=500,
                    random_state=42).fit_predict(X)
    print(f"[cluster] sizes={dict(enumerate(np.bincount(labels)))}")
    return labels.astype(np.int32)


def smooth_labels(mesh, labels, iters=4):
    adj = mesh.vertex_adjacency_graph
    ns  = int(labels.max())+1
    for _ in range(iters):
        new = labels.copy()
        for v in range(len(labels)):
            nb = list(adj.neighbors(v))
            if nb: new[v] = np.bincount(labels[nb], minlength=ns).argmax()
        labels = new
    return labels


def merge_small(mesh, labels, min_ratio=0.02):
    """
    Merge tiny segments (< min_ratio of total faces) into the neighbouring
    segment they share the MOST boundary edges with — i.e. the geometrically
    closest / most connected larger neighbour.

    Only tiny segments are affected. Large segments are never merged together.

    min_ratio: a segment is "tiny" if its face count < min_ratio * total_faces.
               Default 0.02 = any segment with < 2% of faces gets absorbed.
               Raise this value to merge more aggressively.
    """
    faces  = np.array(mesh.faces)
    ns     = int(labels.max()) + 1

    # Per-face label: majority vote of its 3 vertex labels
    fl     = np.array([np.bincount(labels[f], minlength=ns).argmax() for f in faces])
    counts = np.bincount(fl, minlength=ns)
    minf   = max(4, int(len(faces) * min_ratio))

    adj_pairs = mesh.face_adjacency   # (E, 2) — each row is a shared-edge face pair

    changed = True
    while changed:
        changed = False
        for s in range(int(fl.max()) + 1):
            if counts[s] == 0 or counts[s] >= minf:
                continue  # skip empty or large-enough segments

            # Find all shared edges between segment s and other segments
            s_faces   = set(np.where(fl == s)[0])
            # Edges that touch at least one face in s
            edge_mask = np.isin(adj_pairs[:, 0], list(s_faces)) |                         np.isin(adj_pairs[:, 1], list(s_faces))
            touching  = adj_pairs[edge_mask]

            # Count shared edges per neighbouring segment (only the other side)
            edge_tally = {}
            for fa, fb in touching:
                la, lb = fl[fa], fl[fb]
                neighbour = lb if la == s else la
                if neighbour != s:
                    edge_tally[neighbour] = edge_tally.get(neighbour, 0) + 1

            if not edge_tally:
                continue

            # Merge into the neighbour with the MOST shared edges (= closest)
            target = max(edge_tally, key=lambda n: edge_tally[n])

            labels[labels == s] = target
            fl[fl == s]         = target
            counts[target]     += counts[s]
            counts[s]           = 0
            changed             = True

    _, labels = np.unique(labels, return_inverse=True)
    n_final   = int(labels.max()) + 1
    print(f"[merge]  {n_final} segments after absorbing tiny fragments")
    return labels.astype(np.int32)


def sampart3d_segment(mesh, n_segments, n_views=16, img_size=256):
    if not PYRENDER_AVAILABLE:
        print("[warn]  pyrender unavailable — falling back to skeleton segmentation")
        from skimage.morphology import skeletonize as sk3d
        return _skeleton_fallback(mesh, n_segments)
    ext = DINOv2Extractor() if DINO_AVAILABLE else HandcraftedExtractor()
    views, centre, scale = render_multiview(mesh, n_views, img_size)
    feats  = lift_features(mesh, views, ext, centre, scale)
    labels = cluster_features(feats, n_segments)
    labels = smooth_labels(mesh, labels)
    labels = merge_small(mesh, labels)
    return labels


def _skeleton_fallback(mesh, n_segments):
    """Skeleton-based fallback when pyrender is unavailable."""
    from skimage.morphology import skeletonize
    from scipy.sparse.csgraph import dijkstra
    import networkx as nx

    verts = np.array(mesh.vertices)
    bbox  = verts.max(0) - verts.min(0)
    voxel_size = bbox.max() / 70.0
    vox = mesh.voxelized(pitch=voxel_size).fill()
    skel = skeletonize(vox.matrix)
    coords = np.argwhere(skel)
    if len(coords) < 2:
        return np.zeros(len(verts), dtype=np.int32)
    coord_set = {tuple(c) for c in coords}
    offsets = [(di,dj,dk) for di in(-1,0,1) for dj in(-1,0,1) for dk in(-1,0,1)
               if not(di==0 and dj==0 and dk==0)]
    pos_to_idx = {tuple(c):i for i,c in enumerate(coords)}
    G = nx.Graph()
    for i,(ci,cj,ck) in enumerate(coords):
        for di,dj,dk in offsets:
            nb=(ci+di,cj+dj,ck+dk)
            if nb in coord_set and pos_to_idx[nb]>i:
                G.add_edge(i, pos_to_idx[nb], weight=np.sqrt(di**2+dj**2+dk**2))
    degrees = dict(G.degree())
    seeds_sk = [n for n,d in degrees.items() if d>=3] + [n for n,d in degrees.items() if d==1]
    if len(seeds_sk) < 2:
        seeds_sk = list(range(min(n_segments, len(coords))))
    seeds_sk = seeds_sk[:max(n_segments, len(seeds_sk))]
    world = (vox.transform @ np.hstack([coords[seeds_sk],
              np.ones((len(seeds_sk),1))]).T).T[:,:3]
    from sklearn.neighbors import KDTree as _KDT
    _, idx = _KDT(verts).query(world, k=1)
    seed_v = np.unique(idx.ravel()).tolist()
    edges = mesh.edges_unique
    w = np.linalg.norm(verts[edges[:,0]]-verts[edges[:,1]], axis=1)
    N = len(verts)
    from scipy.sparse import csr_matrix
    G2 = csr_matrix((np.concatenate([w,w]),
                     (np.concatenate([edges[:,0],edges[:,1]]),
                      np.concatenate([edges[:,1],edges[:,0]]))), shape=(N,N))
    dist = dijkstra(G2, indices=seed_v, directed=False)
    labels = np.argmin(dist, axis=0).astype(np.int32)
    labels = smooth_labels(mesh, labels, iters=4)
    labels = merge_small(mesh, labels)
    return labels





# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Sub-mesh grouping  (collapses 355 tiny pieces → N spatial groups)
# ─────────────────────────────────────────────────────────────────────────────

def group_submeshes(named_meshes, max_groups=None, tiny_face_threshold=50):
    """
    Two-pass grouping strategy:

    Pass 1 — Trivial merge of micro-meshes:
        Sub-meshes with fewer than `tiny_face_threshold` faces are too small
        to segment meaningfully on their own. They get merged into the spatially
        nearest larger sub-mesh (by centroid distance).

    Pass 2 — Spatial clustering of remaining sub-meshes:
        If there are still more than max_groups sub-meshes, cluster them by
        centroid position using agglomerative (ward) clustering so spatially
        close parts (e.g. fingers, knuckles) are processed together.

    Returns: list of (group_name, merged_mesh) — never more than max_groups items.

    This does NOT destroy the sub-mesh geometry — it only concatenates meshes
    within each group so segmentation sees a coherent local region, not an
    isolated micro-piece.
    """
    if max_groups is None:
        # Sensible default: enough groups that each averages ~50-200 sub-meshes
        n = len(named_meshes)
        if n <= 10:
            max_groups = n          # small scene — keep all separate
        elif n <= 50:
            max_groups = min(n, 20)
        else:
            max_groups = min(n, 30)  # large scene — cap at 30 groups

    print(f"[group] {len(named_meshes)} sub-meshes -> target max {max_groups} groups")

    # Compute centroids + face counts
    centroids  = np.array([m.bounding_box.centroid for _, m in named_meshes])
    face_counts = np.array([len(m.faces) for _, m in named_meshes])

    # ── Pass 1: absorb micro-meshes into nearest larger neighbour ────────────
    large_mask = face_counts >= tiny_face_threshold
    small_mask = ~large_mask

    n_small = small_mask.sum()
    if n_small > 0 and large_mask.sum() > 0:
        print(f"[group] Absorbing {n_small} micro-meshes (< {tiny_face_threshold} faces) "
              f"into nearest larger sub-mesh ...")
        large_idx   = np.where(large_mask)[0]
        small_idx   = np.where(small_mask)[0]
        from sklearn.neighbors import KDTree as _KDT
        kd          = _KDT(centroids[large_idx])
        _, nn       = kd.query(centroids[small_idx], k=1)
        assignment  = np.arange(len(named_meshes))          # default: each mesh -> itself
        for si, li in zip(small_idx, large_idx[nn.ravel()]):
            assignment[si] = li                             # micro -> nearest large
    else:
        assignment = np.arange(len(named_meshes))

    # Remap assignments to contiguous group ids
    unique_leaders = sorted(set(assignment))
    leader_to_group = {l: g for g, l in enumerate(unique_leaders)}
    group_ids = np.array([leader_to_group[assignment[i]] for i in range(len(named_meshes))])
    n_after_pass1 = len(unique_leaders)
    print(f"[group] After micro-merge: {n_after_pass1} groups")

    # ── Pass 2: spatial clustering if still too many groups ──────────────────
    if n_after_pass1 > max_groups:
        from sklearn.cluster import AgglomerativeClustering
        # Compute per-group centroid (weighted by face count)
        group_centroids = np.zeros((n_after_pass1, 3))
        group_weights   = np.zeros(n_after_pass1)
        for i, (_, m) in enumerate(named_meshes):
            g = group_ids[i]
            w = len(m.faces)
            group_centroids[g] += m.bounding_box.centroid * w
            group_weights[g]   += w
        group_centroids /= group_weights[:, None].clip(min=1)

        clust = AgglomerativeClustering(n_clusters=max_groups, linkage="ward")
        cluster_of_group = clust.fit_predict(group_centroids)
        group_ids = np.array([cluster_of_group[group_ids[i]] for i in range(len(named_meshes))])
        print(f"[group] After spatial clustering: {max_groups} groups")

    # ── Build final groups: concatenate meshes within each group ─────────────
    n_groups = int(group_ids.max()) + 1
    groups   = [[] for _ in range(n_groups)]
    names    = [[] for _ in range(n_groups)]
    for i, (name, mesh) in enumerate(named_meshes):
        groups[group_ids[i]].append(mesh)
        names[group_ids[i]].append(name)

    result = []
    for g in range(n_groups):
        if not groups[g]:
            continue
        if len(groups[g]) == 1:
            merged = groups[g][0]
        else:
            merged = trimesh.util.concatenate(groups[g])
            merged.process(validate=True)
        group_name = f"Group_{g:03d}"
        face_total = sum(len(m.faces) for m in groups[g])
        print(f"[group]   {group_name}: {len(groups[g])} sub-mesh(es), "
              f"{face_total:,} faces  [{', '.join(names[g][:3])}"
              f"{'...' if len(names[g])>3 else ''}]")
        result.append((group_name, merged))

    return result


def segment_one_mesh(mesh_name, mesh, n_seg, strategy, n_views, img_size, angle):
    """Segment a single mesh and return (label_type, labels, n_actual)."""
    print(f"\n[seg]  === {mesh_name} ({len(mesh.vertices):,} verts) ===")
    if strategy == "connectivity":
        fl, n = connectivity_segments(mesh)
        return "face", fl, n
    if strategy == "multiview":
        vl = sampart3d_segment(mesh, n_seg, n_views, img_size)
        return "vertex", vl, int(vl.max())+1
    # Auto
    fl, n = connectivity_segments(mesh)
    if n > 1:
        print(f"[auto]  loose parts -> connectivity ({n})")
        return "face", fl, n
    if mesh.face_adjacency_angles.std() > np.radians(25):
        sl, n2 = sharp_edge_segments(mesh, angle)
        if n2 > 1:
            print(f"[auto]  hard-surface -> sharp-edge ({n2})")
            return "face", sl, n2
    print("[auto]  smooth/organic -> SAMPart3D multi-view feature lifting")
    vl = sampart3d_segment(mesh, n_seg, n_views, img_size)
    return "vertex", vl, int(vl.max())+1


def enforce_segment_limit(mesh, face_labels, limit=20):
    """
    Keep only the `limit` largest segments by face count.
    Every smaller segment is merged into whichever kept segment shares
    the most boundary edges with it — i.e. the geometrically closest one.

    Only the over-limit segments are touched; the big ones are never merged
    with each other.
    """
    faces     = np.array(mesh.faces)
    adj_pairs = mesh.face_adjacency          # (E,2) shared-edge face pairs
    ns        = int(face_labels.max()) + 1
    counts    = np.bincount(face_labels, minlength=ns)

    # Rank segments by size — largest first
    ranked     = np.argsort(-counts)         # descending
    keep_set   = set(ranked[:limit].tolist())
    drop_set   = set(ranked[limit:].tolist())

    if not drop_set:
        return face_labels                   # already within limit

    print(f"[limit] Keeping top {limit} segments, merging {len(drop_set)} smaller ones ...")

    fl      = face_labels.copy()
    changed = True
    while changed:
        changed = False
        # Process smallest drop-segment first so the graph stays connected
        drop_by_size = sorted(drop_set, key=lambda s: counts[s])
        for s in drop_by_size:
            if counts[s] == 0:
                drop_set.discard(s)
                continue

            # Count shared boundary edges to every KEPT neighbour
            s_faces    = np.where(fl == s)[0]
            edge_mask  = np.isin(adj_pairs[:, 0], s_faces) |                          np.isin(adj_pairs[:, 1], s_faces)
            touching   = adj_pairs[edge_mask]

            tally = {}
            for fa, fb in touching:
                la, lb  = fl[fa], fl[fb]
                nb      = lb if la == s else la
                if nb in keep_set:
                    tally[nb] = tally.get(nb, 0) + 1

            if not tally:
                # No kept neighbour yet — skip this pass, will be reached later
                continue

            # Merge into the kept segment with most shared edges
            target         = max(tally, key=lambda n: tally[n])
            fl[fl == s]    = target
            counts[target] += counts[s]
            counts[s]       = 0
            drop_set.discard(s)
            changed = True

    # If any drop segments are still isolated (no shared edges with kept segments),
    # fall back to nearest centroid — assign to the spatially closest kept segment
    remaining = [s for s in drop_set if counts[s] > 0]
    if remaining:
        kept_list = sorted(keep_set)
        # Compute centroid of each kept segment
        kept_cents = np.array([
            mesh.vertices[np.unique(faces[fl == k])].mean(axis=0)
            for k in kept_list
        ])
        from sklearn.neighbors import KDTree as _KDT
        kd = _KDT(kept_cents)
        for s in remaining:
            s_verts = mesh.vertices[np.unique(faces[fl == s])]
            s_cent  = s_verts.mean(axis=0, keepdims=True)
            _, nn   = kd.query(s_cent, k=1)
            target  = kept_list[nn[0, 0]]
            fl[fl == s]      = target
            counts[target]  += counts[s]
            counts[s]        = 0
        print(f"[limit] {len(remaining)} isolated fragment(s) absorbed by nearest centroid")

    # Remap to contiguous 0..limit-1 ids
    _, fl = np.unique(fl, return_inverse=True)
    print(f"[limit] Final segment count: {int(fl.max()) + 1}")
    return fl.astype(np.int32)


def export_all_segments(named_meshes, all_results, output_path, seg_limit=20):
    """
    named_meshes : list of (name, mesh)
    all_results  : list of (label_type, labels, n_seg) — one per mesh

    After collecting all per-mesh segmentation results, applies a global
    segment limit: across the entire model, only the `seg_limit` largest
    segments survive. All smaller ones are merged into their closest
    (most-shared-edge) neighbour that is in the kept set.

    Node naming: Segment_00 … Segment_19  (globally consistent palette).
    """
    # ── Step 1: collect all (face_labels, faces, vertices) globally ──────────
    all_faces_list  = []
    all_verts_list  = []
    all_fl_list     = []
    vert_offsets    = []
    face_offsets    = []
    v_off = f_off = 0

    for (mesh_name, mesh), (label_type, labels, ns) in zip(named_meshes, all_results):
        verts = np.array(mesh.vertices)
        faces = np.array(mesh.faces)

        if label_type == "vertex":
            fl = np.array([np.bincount(labels[f], minlength=ns).argmax()
                           for f in faces])
        else:
            fl = labels.copy()

        all_verts_list.append(verts)
        all_faces_list.append(faces + v_off)   # offset face indices
        all_fl_list.append(fl)
        vert_offsets.append(v_off)
        face_offsets.append(f_off)
        v_off += len(verts)
        f_off += len(faces)

    # ── Step 2: make labels globally unique across all meshes ─────────────────
    offset = 0
    global_fl = []
    for fl in all_fl_list:
        global_fl.append(fl + offset)
        offset += int(fl.max()) + 1
    global_fl    = np.concatenate(global_fl)   # (total_faces,)
    global_faces = np.concatenate(all_faces_list, axis=0)
    global_verts = np.concatenate(all_verts_list, axis=0)

    total_raw = int(global_fl.max()) + 1
    print(f"[export] Raw segments across all meshes: {total_raw}")

    # ── Step 3: build a combined mesh just for adjacency computation ──────────
    combined = trimesh.Trimesh(vertices=global_verts,
                               faces=global_faces, process=False)

    # ── Step 4: enforce the global segment limit ──────────────────────────────
    final_fl = enforce_segment_limit(combined, global_fl, limit=seg_limit)
    n_final  = int(final_fl.max()) + 1

    # ── Step 5: export each final segment as a named GLB node ─────────────────
    pal   = np.tile(PALETTE, (n_final // len(PALETTE) + 1, 1))[:n_final]
    scene = trimesh.Scene()
    for sid in range(n_final):
        sf = global_faces[final_fl == sid]
        if len(sf) == 0:
            continue
        uv, inv = np.unique(sf, return_inverse=True)
        nv  = global_verts[uv]
        nf  = inv.reshape(-1, 3)
        col = (np.array([*pal[sid], 1.0]) * 255).astype(np.uint8)
        sub = trimesh.Trimesh(vertices=nv, faces=nf,
                              vertex_colors=np.tile(col, (len(nv), 1)),
                              process=False)
        name = f"Segment_{sid:02d}"
        scene.add_geometry(sub, node_name=name, geom_name=name)
        print(f"[out]   {name} -> {len(sf):,} faces")

    scene.export(output_path)
    print(f"\nSaved {n_final} segments -> {output_path}")
    return n_final


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="SAMPart3D-inspired GLB segmentation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input",
                   help="GLB filename (e.g. model.glb) — loaded from the "
                        "input/ folder next to this script")
    p.add_argument("-n","--segments",     type=int, default=None,
                   help="Per-mesh segment target before global limit is applied")
    p.add_argument("--granularity",       choices=["coarse","medium","fine"], default="medium")
    p.add_argument("--strategy",          choices=["auto","connectivity","multiview"], default="auto")
    p.add_argument("--max-segments",      type=int, default=20,
                   help="Global segment limit across the whole model. "
                        "The N largest segments are kept; all smaller ones are "
                        "merged into their closest (most-shared-edge) neighbour. "
                        "Default: 20")
    p.add_argument("--views",             type=int, default=16)
    p.add_argument("--img-size",          type=int, default=256)
    p.add_argument("--angle",             type=float, default=45.0)
    a = p.parse_args()
    n_seg = a.segments if a.segments else GRANULARITY[a.granularity]

    # ── Resolve paths relative to the app/ folder this script lives in ───────
    app_dir    = os.path.dirname(os.path.abspath(__file__))
    input_dir  = os.path.join(app_dir, "input")
    output_dir = os.path.join(app_dir, "output")
    os.makedirs(input_dir,  exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Accept bare filename ("model.glb") or full path — always resolve to input/
    input_filename = os.path.basename(a.input)
    input_path     = os.path.join(input_dir, input_filename)

    # Output file: same name, stored in output/
    output_path    = os.path.join(output_dir, input_filename)

    if not os.path.exists(input_path):
        sys.exit(f"[error] Input file not found: {input_path}")

    print(f"[config] segments={n_seg}, max_segments={a.max_segments}, "
          f"strategy={a.strategy}, views={a.views}, img_size={a.img_size}")
    print(f"[config] input  : {input_path}")
    print(f"[config] output : {output_path}")

    # Load all sub-meshes independently — NO merging
    named_meshes = load_glb(input_path)

    # Segment each sub-mesh independently
    all_results = []
    for mesh_name, mesh in named_meshes:
        lt, labels, n_actual = segment_one_mesh(
            mesh_name, mesh, n_seg, a.strategy, a.views, a.img_size, a.angle)
        all_results.append((lt, labels, n_actual))

    # Export — enforces global segment limit of --max-segments
    total = export_all_segments(named_meshes, all_results, output_path,
                                seg_limit=a.max_segments)
    print(f"\n   Sub-meshes      : {len(named_meshes)}")
    print(f"   Total segments  : {total}  (limit={a.max_segments})")
    print(f"   Backbone        : {'DINOv2' if DINO_AVAILABLE else 'Handcrafted image features'}")

if __name__ == "__main__":
    main()