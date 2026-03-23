#!/usr/bin/env python3
"""
Test script for the fallback mesh generation mechanism.
Tests the following flow:
1. Image download from Tavily
2. Mesh generation API
3. Segmentation
4. Semantic renaming
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment
ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env', override=True)

# app.server imports blender_mcp at module load, which requires this env var.
# Fallback tests do not use Blender, so set a placeholder if absent.
os.environ.setdefault('OPENAI_API_KEY', 'test-key-for-fallback-script')

# Check required environment variables
REQUIRED_VARS = [
    'TAVILY_API_KEY',
    'GROQ_API_KEY',
    'MESH_GENERATOR_URL',
]

print("\n" + "="*70)
print("FALLBACK TEST - Environment Variables")
print("="*70)

missing = []
for var in REQUIRED_VARS:
    value = os.environ.get(var, '')
    status = "✅ SET" if value else "❌ MISSING"
    print(f"  {var:<30} {status}")
    if not value:
        missing.append(var)

if missing:
    print(f"\n⚠️  Missing variables: {', '.join(missing)}")
    print(f"Please set them in {ROOT_DIR / '.env'}")
    sys.exit(1)

print("\n✅ All required variables are set!\n")

# Import after env is loaded
from app.server import (
    _download_high_quality_image_for_prompt,
    _generate_mesh_file_from_prompt,
    _segment_and_rename_generated_model,
    OUTPUT_DIR,
)


async def test_fallback():
    """Test the complete fallback pipeline."""
    
    test_prompts = [
        "pkomon",
        "pokemon",
        "pikachu",
    ]
    
    for prompt in test_prompts:
        print("\n" + "="*70)
        print(f"TESTING: {prompt}")
        print("="*70)
        
        try:
            # Step 1: Download image
            print(f"\n[1/3] Downloading high-quality image for '{prompt}'...")
            image_bytes, image_url, image_mime = await _download_high_quality_image_for_prompt(prompt)
            print(f"  ✅ Downloaded: {len(image_bytes)} bytes")
            print(f"  ✅ MIME type: {image_mime}")
            print(f"  ✅ Image URL: {image_url[:80]}...")
            
            # Step 2: Generate mesh
            print(f"\n[2/3] Generating mesh from image...")
            generated_path, returned_url, img_bytes, mesh_api = await _generate_mesh_file_from_prompt(prompt)
            print(f"  ✅ Generated GLB: {generated_path}")
            print(f"     Size: {generated_path.stat().st_size} bytes")
            print(f"  ✅ Mesh Generator: {mesh_api}")
            
            # Step 3: Segment and rename
            print(f"\n[3/3] Segmenting and renaming mesh...")
            final_path, semantic_names = await _segment_and_rename_generated_model(
                generated_path, 
                hint=prompt
            )
            print(f"  ✅ Final GLB: {final_path}")
            print(f"     Size: {final_path.stat().st_size} bytes")
            print(f"  ✅ Semantic segments ({len(semantic_names)}):")
            for i, name in enumerate(semantic_names, 1):
                print(f"     [{i}] {name}")
            
            print(f"\n✅ FALLBACK COMPLETE for '{prompt}'")
            
        except Exception as e:
            print(f"\n❌ FALLBACK FAILED for '{prompt}'")
            print(f"   Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print("\n" + "="*70)
    print("TEST COMPLETE")
    print("="*70)
    print(f"\nGenerated files are in: {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(test_fallback())
