<p align="center">
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Three.js-0.170-000?logo=three.js" alt="Three.js" />
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker" />
</p>

# Prompt2Mesh

**AI-powered 3D model discovery from natural language.** Describe any object, and Prompt2Mesh searches the web, scores candidates with CLIP + geometric analysis, segments meshes into semantically labeled parts, and lets you explore the result in an interactive 3D viewer with conversational AI.

<p align="center">
  <a href="https://drive.google.com/file/d/1r6hkBwANye73VCUGwIQxivr3zGQ3nmCM/view?usp=sharing">
    <img src="https://img.shields.io/badge/▶_Watch_Demo-4285F4?style=for-the-badge&logo=google-drive&logoColor=white" alt="Watch Demo" />
  </a>
</p>

---

## ✨ Features

- **Natural Language Search** — Describe a 3D model in plain English. An LLM refines your prompt, then Tavily searches across Sketchfab, Poly Haven, Quaternius, and more.
- **Image-to-3D** — Upload a reference image and generate a 3D mesh directly via TripoSR, then segment and label it automatically.
- **AI Model Scoring** — Every candidate model is scored on two axes using OpenAI CLIP and geometric analysis:
  - **Semantic Relevance (60%)** — Multi-view CLIP similarity + fuzzy metadata matching
  - **Geometric Complexity (40%)** — Polygon density, node hierarchy, UV coverage, material diversity
- **Decision Gates** — Scored models are routed through intelligent gates: `USE`, `RENAME`, `SEGMENT_MESH`, `REFETCH`, or `DISCARD` — ensuring only high-quality, well-labeled models reach the viewer.
- **Mesh Segmentation** — A SAMPart3D-inspired pipeline (multi-view rendering → DINOv2 feature lifting → scale-conditioned clustering → geodesic smoothing) segments unlabeled meshes into meaningful parts.
- **Semantic Renaming** — An LLM renames generic segments (`Segment_00`, `Segment_01`) into human-readable labels (`head`, `left_wing`, `engine_block`) based on model context.
- **Interactive 3D Viewer** — Three.js-powered viewer with segment highlighting, texture toggle, orbit controls, and model switching between ranked candidates.
- **Conversational AI Chat** — Ask questions about the model's parts. The AI highlights the relevant segment in the viewer and explains its function in natural, spoken-style language.
- **AI Narration** — Guided narration mode walks through each segment with domain-aware explanations (anatomy for characters, species-appropriate terms for animals, mechanical descriptions for vehicles).
- **Semantic Caching** — Fuzzy prompt matching against previous searches avoids redundant web lookups and re-scoring.
- **Blender MCP Integration** — Connect to a running Blender instance via Model Context Protocol for procedural 3D generation and manipulation.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (Next.js 16)                    │
│  ┌──────────┐  ┌──────────────┐  ┌───────────┐  ┌───────────┐  │
│  │  Search   │  │ Model Viewer │  │   Chat    │  │  History   │  │
│  │   Page    │  │  (Three.js)  │  │  Panel    │  │   Page     │  │
│  └────┬─────┘  └──────┬───────┘  └─────┬─────┘  └─────┬─────┘  │
└───────┼───────────────┼────────────────┼───────────────┼────────┘
        │               │                │               │
        ▼               ▼                ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI + Python)                   │
│                                                                 │
│  ┌─────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ Prompt       │  │ Model Scorer   │  │ Mesh Segmentation    │  │
│  │ Refinement   │  │ (CLIP +        │  │ (SAMPart3D-inspired) │  │
│  │ (Groq/Llama) │  │  Geometric)    │  │ + Semantic Renaming  │  │
│  └──────┬──────┘  └───────┬────────┘  └──────────┬───────────┘  │
│         │                 │                      │               │
│  ┌──────▼──────┐  ┌───────▼────────┐  ┌──────────▼───────────┐  │
│  │ Web Search   │  │ Sketchfab      │  │ Conversation         │  │
│  │ (Tavily)     │  │ Fetcher        │  │ Service + Narration  │  │
│  └─────────────┘  └────────────────┘  └──────────────────────┘  │
│                                                                 │
│  ┌─────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ Semantic     │  │ Blender MCP    │  │ Image-to-Mesh        │  │
│  │ Cache        │  │ Client         │  │ (TripoSR)            │  │
│  └─────────────┘  └────────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   MongoDB + GridFS │
                    └───────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (or pip)
- **Node.js 20+** and [pnpm](https://pnpm.io/)
- **MongoDB** running locally (or a remote connection string)
- API keys for: [Groq](https://console.groq.com/), [Tavily](https://tavily.com/), [Sketchfab](https://sketchfab.com/settings/password)

### 1. Clone the repository

```bash
git clone https://github.com/vanshika-0305/Prompt2Mesh.git
cd Prompt2Mesh
```

### 2. Configure environment variables

Copy the example `.env` at the project root and fill in your keys:

```env
# Required API Keys
TAVILY_API_KEY=your_tavily_api_key
GROQ_API_KEY=your_groq_api_key
SKETCHFAB_API_KEY=your_sketchfab_api_key

# Database
MONGO_URL=mongodb://localhost:27017
DB_NAME=prompt2mesh

# Mesh Generation API (optional — needed for image-to-3D and fallback generation)
MESH_GENERATOR_URL=http://98.70.40.74:8000/generate-mesh

# CORS
CORS_ORIGINS=*
```

Create a `frontend/.env` (or `.env.local`) with:

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

### 3. Start the backend

```bash
cd backend
uv venv && uv pip install -e .
# or: python -m venv .venv && pip install -e .

uvicorn app.server:app --reload --port 8000
```

### 4. Start the frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Open **http://localhost:3000** and search for a 3D model.

---

## 🐳 Docker

Both services include production-ready Dockerfiles.

```bash
# Backend
cd backend
docker build -t prompt2mesh-backend .
docker run -p 8000:8000 --env-file ../.env prompt2mesh-backend

# Frontend
cd frontend
docker build -t prompt2mesh-frontend .
docker run -p 3000:3000 -e NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 prompt2mesh-frontend
```

---

## 📂 Project Structure

```
Prompt2Mesh/
├── backend/
│   ├── app/
│   │   ├── server.py              # FastAPI app — routes, search, scoring pipeline
│   │   ├── model_scorer.py        # CLIP + geometric composite scorer
│   │   ├── segment_mesh.py        # SAMPart3D-inspired mesh segmentation
│   │   ├── mesh_renamer.py        # LLM-powered semantic segment renaming
│   │   ├── sketchfab_fetcher.py   # Sketchfab download API + direct URL fetcher
│   │   ├── conversation_service.py# Chat about model parts with label matching
│   │   ├── blender_mcp.py         # Blender MCP client for procedural generation
│   │   └── cleanup.py             # Temp file management
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── SCORING.md                 # Scoring & decision gate documentation
├── frontend/
│   ├── app/
│   │   ├── page.tsx               # Search page with prompt input + history
│   │   ├── view/[id]/page.tsx     # 3D viewer + chat + model details
│   │   ├── history/page.tsx       # Search history browser
│   │   └── components/
│   │       ├── ModelViewer.tsx     # Three.js GLB viewer with segment highlighting
│   │       ├── ChatPanel.tsx      # Conversational AI chat + narration controls
│   │       └── NavBar.tsx         # Navigation bar
│   ├── Dockerfile
│   └── package.json
├── .env                           # Environment variables (not committed)
└── README.md
```

---

## 🔌 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/search` | Search for 3D models from a text prompt |
| `POST` | `/api/search/image` | Generate a 3D model from an uploaded image |
| `GET` | `/api/history` | List recent searches |
| `GET` | `/api/history/:id` | Get a specific search result |
| `DELETE` | `/api/history/:id` | Delete a search record |
| `POST` | `/api/chat` | Chat about a model's parts (with segment highlighting) |
| `GET` | `/api/narration/:id` | Get AI narration for all segments of a model |
| `POST` | `/api/score` | Score a single model (CLIP + geometric) |
| `POST` | `/api/score/batch` | Score and rank all models from a search |
| `GET` | `/api/output/:filename` | Serve processed GLB files |
| `GET` | `/api/download` | Download a processed model |
| `POST` | `/api/mesh/rename` | Rename mesh segments with LLM |
| `POST` | `/api/generate-mesh-from-prompt` | Generate a mesh from a text prompt |
| `POST` | `/api/blender/connect` | Connect to Blender MCP server |
| `POST` | `/api/blender/chat` | Chat with Blender via MCP tools |

---

## ⚙️ How It Works

### Search Pipeline

1. **Prompt Refinement** — Groq (Llama 3.3 70B) extracts `object_type`, `style`, `keywords`, and a `refined_query` from the user's input.
2. **Semantic Cache Check** — A fuzzy matcher checks MongoDB for previous searches with similar prompts (Jaccard + sequence similarity, threshold 0.72).
3. **Web Search** — Tavily runs 4 parallel queries across free model repos (Poly Haven, Quaternius, Kenney) and Sketchfab.
4. **Model Fetching** — The Sketchfab Download API fetches GLBs; direct URLs are downloaded as-is.
5. **Scoring & Decision Gates** — Each candidate is scored and routed:
   - `USE` — Labeled model, good match → serve directly
   - `RENAME` — Labeled but names are generic → re-segment and rename
   - `SEGMENT_MESH` — Unlabeled, good match → segment + rename
   - `REFETCH` — Wrong object → try next candidate
   - `DISCARD` — Low quality → skip
6. **Background Processing** — After the first approved model is returned to the user, remaining candidates are scored in the background and progressively added.
7. **Fallback Generation** — If no candidate passes scoring, an image is fetched for the prompt and sent to a mesh generation API (TripoSR), then segmented and renamed.

### Segmentation Pipeline (SAMPart3D-inspired)

The system auto-selects a strategy based on mesh structure:

| Mesh Type | Strategy | Approach |
|-----------|----------|----------|
| Multiple loose parts | `connectivity` | Graph-based connected components |
| Single hard-surface | `sharp-edge` | Split on sharp dihedral angles |
| Single smooth/organic | `multiview` | 16-view rendering → DINOv2 features → 3D lifting → k-means clustering → geodesic smoothing |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, Three.js, Tailwind CSS 4, TypeScript |
| Backend | FastAPI, Python 3.11+, Pydantic, Motor (async MongoDB) |
| AI/ML | Groq (Llama 3.3 70B), OpenAI CLIP (ViT-B/32), DINOv2, Tavily |
| 3D Processing | trimesh, pyrender, pygltflib, scipy, scikit-learn |
| Database | MongoDB with GridFS for model storage |
| Infrastructure | Docker, uv, pnpm |

---

## 📄 License

This project is for educational and research purposes.

---

## 🙏 Acknowledgments

- [SAMPart3D](https://arxiv.org/abs/2411.07184) (Yang et al., 2024) — Inspiration for the multi-view segmentation pipeline
- [Sketchfab](https://sketchfab.com/) — 3D model marketplace and download API
- [Groq](https://groq.com/) — Ultra-fast LLM inference
- [Tavily](https://tavily.com/) — AI-optimized web search
- [OpenCLIP](https://github.com/mlfoundations/open_clip) — Open-source CLIP implementation
