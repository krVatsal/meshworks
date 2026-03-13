# Model Scoring Integration Guide

## Overview

The model scorer has been integrated into the Prompt2Mesh API. It evaluates 3D models using:

1. **Semantic Relevance** (60% weight)
   - CLIP score: Multi-view image-text similarity
   - Metadata score: Fuzzy matching (title, description, tags)

2. **Geometric Complexity** (40% weight)
   - Polygon density
   - Node hierarchy depth
   - UV coverage
   - Material diversity

## Decision Gates

Based on the final composite score **AND** label status:

### 1. **USE** (Labelled + score > 0.70)
- Model is **correct** AND has **named nodes**
- Use directly for rendering and spatial conversation
- **No Blender needed at all**

### 2. **REFETCH** (Labelled + score ≤ 0.70)
- Model has labels but it's the **wrong object**
- Retrieval fetched something irrelevant to the prompt
- **Refetch with a refined query**
- Don't generate because the problem is retrieval, not model availability

### 3. **CACHE** (Unlabelled + score > 0.85)
- Model is the **right object**, very high quality
- But has **no named nodes**
- Cache it for rendering
- **Trigger Blender MCP in background** to generate a labelled version
- The labelled version becomes the one used for spatial conversation

### 4. **DISCARD** (Unlabelled + score ≤ 0.85)
- Model is either the **wrong object** or **low quality**
- Has no labels anyway
- **Not worth caching**
- **Trigger Blender MCP for full procedural generation** from structured intent

## Installation

Install dependencies:

```bash
cd backend
pip install -e .
# Or with uv:
uv pip install -e .
```

## API Endpoints

### 1. Score a Single Model

**POST** `/api/score`

```json
{
  "model_url": "https://example.com/model.glb",
  "prompt": "futuristic motorcycle",
  "metadata": {
    "title": "Cyberpunk Bike",
    "description": "A futuristic motorcycle design",
    "tags": ["motorcycle", "cyberpunk", "vehicle"]
  }
}
```

**Response:**
```json
{
  "final_score": 0.87,
  "is_labelled": false,
  "decision": "CACHE",
  "semantic_score": 0.89,
  "geometric_score": 0.84,
  "details": {
    "semantic": {
      "clip_score": 0.91,
      "metadata_score": 0.85
    },
    "geometric": {
      "polygon_density": 0.92,
      "node_hierarchy_depth": 0.65,
      "uv_coverage": 0.88,
      "material_diversity": 0.75
    }
  }
}
```

### 2. Batch Score Search Results

**POST** `/api/score/batch`

```json
{
  "search_id": "550e8400-e29b-41d4-a716-446655440000",
  "prompt": "futuristic motorcycle"
}
```

**Response:**
```json
{
  "search_id": "550e8400-e29b-41d4-a716-446655440000",
  "ranked_models": [
    {
      "final_score": 0.87,
      "decision": "CACHE",
      "model_info": {
        "title": "Cyberpunk Bike",
        "url": "https://example.com/model.glb",
        "tags": ["motorcycle", "cyberpunk"]
      }
    }
  ],
  "best_model": { /* same structure as ranked_models[0] */ }
}
```

## Usage Flow

### Integrated Pipeline

1. **Search** → `POST /api/search` with prompt
2. **Get Search ID** from response
3. **Score Models** → `POST /api/score/batch` with search_id
4. **Get Best Model** from ranked results
5. **Decision Gate**:
   - **USE**: Model is perfect (labelled + good score) - use directly, no Blender needed
   - **REFETCH**: Model has labels but wrong object - refetch with refined query
   - **CACHE**: Good model but needs labels - cache and generate labels with Blender MCP
   - **DISCARD**: Poor quality or wrong object - generate from scratch with Blender MCP

### Python Example

```python
import httpx
import asyncio

async def search_and_score(prompt: str):
    async with httpx.AsyncClient() as client:
        # 1. Search for models
        search_response = await client.post(
            "http://localhost:8000/api/search",
            json={"prompt": prompt}
        )
        search_data = search_response.json()
        search_id = search_data["id"]
        
        # 2. Score all models from search
        score_response = await client.post(
            "http://localhost:8000/api/score/batch",
            json={"search_id": search_id, "prompt": prompt}
        )
        scores = score_response.json()
        
        # 3. Get best model
        best = scores["best_model"]
        print(f"Best model score: {best['final_score']}")
        print(f"Is labelled: {best['is_labelled']}")
        print(f"Decision: {best['decision']}")
        
        # 4. Act on decision
        if best['decision'] == 'USE':
            # Perfect! Use directly for rendering and conversation
            return best
        elif best['decision'] == 'REFETCH':
            # Wrong object, try refined search
            return await search_and_score(refine_query(prompt))
        elif best['decision'] == 'CACHE':
            # Good model, needs labels - trigger Blender MCP
            await generate_labels_with_blender(best['model_info'])
            return best
        else:  # DISCARD
            # Generate from scratch with Blender MCP
            await generate_with_blender(prompt)
        
        return best

# Run
result = asyncio.run(search_and_score("futuristic motorcycle"))
```

## Important Notes

### Current Limitations

1. **Only Direct URLs**: Sketchfab embed URLs cannot be scored yet (requires download API)
2. **GLB/GLTF Only**: Only models with direct `.glb` or `.gltf` URLs can be scored
3. **CPU Intensive**: CLIP scoring is computationally expensive
4. **Temporary Storage**: Models are downloaded to temp directory and deleted after scoring

### Metadata Integration

The scorer automatically uses metadata fetched by `fetch_sketchfab_metadata()`:

- ✅ `title` - Used in metadata scoring
- ✅ `description` - Used in metadata scoring  
- ✅ `tags` - Used in metadata scoring
- ℹ️ `vertex_count`, `face_count` - Already in metadata but scorer computes its own
- ℹ️ `categories`, `license`, `author` - Not used by scorer

### Performance Tips

1. **Use Batch Scoring**: More efficient than scoring models individually
2. **Cache Results**: Scores are automatically saved to MongoDB (`scored_models` field)
3. **Filter Before Scoring**: Only score downloadable models with `is_downloadable: true`
4. **GPU Acceleration**: Install PyTorch with CUDA for faster CLIP scoring

## Environment Setup

Ensure your `.env` has:

```env
SKETCHFAB_API_KEY=your_sketchfab_api_key  # For rich metadata
```

## Troubleshooting

### "Only direct GLB/GLTF URLs are supported"

- The scorer requires downloadable model files
- Sketchfab embeds need to use the download API (not yet implemented)
- Use models from other sources with direct URLs

### "Failed to download any models"

- Check model URLs are accessible
- Verify network connectivity
- Check model file formats (only GLB/GLTF supported)

### Slow scoring

- CLIP model loads on first use (~500MB download)
- Consider using GPU acceleration
- Batch scoring is more efficient than individual requests

## Next Steps

To add Sketchfab download support:

1. Implement Sketchfab download API integration
2. Handle authentication and permission checks  
3. Download GLB files from Sketchfab models
4. Enable scoring for all Sketchfab results
