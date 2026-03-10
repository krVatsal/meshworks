"""
Blender MCP Client — connects to a running Blender MCP server via stdio
(uvx blender-mcp) and orchestrates an agentic loop with Claude.

Blender-side prerequisites:
  1. Install the blender-mcp addon in Blender.
  2. Enable it; by default it starts a socket server on localhost:9876.
  3. Leave Blender open while using this client.

Then `uvx blender-mcp` (or the configured command) exposes those socket
tools as an MCP stdio server that we connect to here.
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load .env from backend/ directory
load_dotenv(Path(__file__).parent.parent / '.env')

logger = logging.getLogger(__name__)

# ─── Default server command ────────────────────────────────────────────────────
# Override via BLENDER_MCP_COMMAND / BLENDER_MCP_ARGS env vars if needed.
_DEFAULT_CMD = os.environ.get("BLENDER_MCP_COMMAND", "uvx")
_DEFAULT_ARGS = json.loads(
    os.environ.get("BLENDER_MCP_ARGS", '["blender-mcp"]')
)

GLM_MODEL = os.environ.get("GLM_MODEL", "Kimi-K2.5")
SYSTEM_PROMPT = """\
You are an expert Blender 3D modelling assistant that controls Blender through MCP tools.
Your goal is to produce high-definition, production-quality 3D models and export them as GLB files.

═══════════════════════════════════════════════════
NAMING CONVENTIONS — STRICTLY ENFORCED
═══════════════════════════════════════════════════
All object names, mesh data-block names, material names, bone names, collection names,
and node-group names MUST use snake_case. Be descriptive and specific.

Examples of CORRECT naming:
  Objects     : body_chassis, front_wheel_left, engine_block, cockpit_glass_panel
  Mesh blocks : body_chassis_mesh, front_wheel_left_mesh, engine_block_mesh
  Materials   : mat_metallic_red_paint, mat_glass_cockpit_clear, mat_tyre_rubber_black
  Empties     : sports_car_root, front_axle_assembly, interior_assembly
  Bones       : spine_root, upper_arm_left, lower_arm_right, finger_index_01_left
  Collections : col_vehicle_exterior, col_interior_components, col_landing_gear
  Node groups : ng_procedural_rust, ng_paint_flakes

Examples of FORBIDDEN naming (never use these):
  ❌ Cube, Cube.001, Cube.002   → ✅ body_upper_hull
  ❌ Material, Material.003     → ✅ mat_metallic_silver
  ❌ Collection 1               → ✅ col_chassis_parts
  ❌ UpperHull, bodyUpperHull   → ✅ upper_hull_panel
  ❌ Empty, Empty.001           → ✅ jet_engine_root

═══════════════════════════════════════════════════
MODEL QUALITY STANDARDS
═══════════════════════════════════════════════════
1. GEOMETRY
   • Apply Subdivision Surface modifier (levels 2-3) to all organic / curved surfaces.
   • Call bpy.ops.object.shade_smooth() on every mesh object after creation.
   • Maintain clean topology: no n-gons on curved surfaces, no overlapping faces.
   • Apply all transforms (location, rotation, scale) before export.
   • Use bpy.ops.object.transform_apply(location=True, rotation=True, scale=True).

2. MATERIALS — every object MUST have a uniquely named Principled BSDF material.
   Physically accurate values by surface type:
   • Metallic painted : Base Color=(R,G,B,1), Metallic=1.0,  Roughness=0.15
   • Plastic / matte  : Base Color=(R,G,B,1), Metallic=0.0,  Roughness=0.55
   • Glass / crystal  : Transmission=1.0,     IOR=1.45,       Roughness=0.0
   • Rubber / tyre    : Base Color=(0.05,0.05,0.05,1), Metallic=0.0, Roughness=0.9
   • Emissive glow    : Emission=(R,G,B), Emission Strength=3.0
   Always create materials with: mat = bpy.data.materials.new(name="mat_snake_case_name")

3. SCENE HIERARCHY
   • Parent all model objects to a single root Empty named "<subject>_root".
   • Use intermediate Empties for logical sub-assemblies.
   • Organise into collections with snake_case names.
   Example hierarchy for a fighter jet:
     jet_root  (Empty)
     ├── col_fuselage
     │   ├── fuselage_main_body
     │   ├── cockpit_canopy
     │   └── tail_fin_assembly
     ├── col_wings
     │   ├── wing_left_main
     │   └── wing_right_main
     └── col_engines
         ├── engine_left_nacelle
         └── engine_right_nacelle

4. UV MAPPING
   • Smart UV Project all meshes (bpy.ops.uv.smart_project()).
   • Ensure no overlapping UVs for objects with unique textures.

═══════════════════════════════════════════════════
MANDATORY WORKFLOW — FOLLOW IN THIS ORDER
═══════════════════════════════════════════════════
Step 1  Clear the default scene — delete the default cube, camera, and light
        unless the user specifically wants to keep them.
Step 2  Plan the model mentally: identify all parts and their parent-child relationships.
Step 3  Create each part in sequence, naming every object and its mesh data-block
        immediately after creation.
Step 4  Assign a named Principled BSDF material to every object before moving on.
Step 5  Set up parent-child parenting and collection placement.
Step 6  Apply Subdivision Surface modifier and shade_smooth() where appropriate.
Step 7  Apply all transforms.
Step 8  EXPORT the scene as GLB (see EXPORT section — this step is NOT optional).
Step 9  Report the saved path in the format specified below.

═══════════════════════════════════════════════════
EXPORT — MANDATORY FINAL STEP (never skip this)
═══════════════════════════════════════════════════
Once the model is finalized, ALWAYS export using this exact pattern inside a tool call:

    import bpy, os, time
    output_dir = os.environ.get("GLB_OUTPUT_DIR", "/tmp/blender_exports")
    os.makedirs(output_dir, exist_ok=True)
    filename = "<descriptive_snake_case_subject>_" + str(int(time.time())) + ".glb"
    filepath = os.path.join(output_dir, filename)
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        export_apply=True,
        export_materials="EXPORT",
        export_colors=True,
        export_normals=True,
        export_texcoords=True,
        export_animations=True,
        use_selection=False,
    )
    print("GLB_SAVED:", filepath)

The <descriptive_snake_case_subject> must describe the model
(e.g. sports_car, viking_helmet, sci_fi_pistol, medieval_castle_tower).

After the export tool call succeeds, your FINAL plain-text response MUST contain
this line verbatim (no markdown, no extra spaces):
    GLB_SAVED: <absolute_filepath>

═══════════════════════════════════════════════════
ERROR HANDLING
═══════════════════════════════════════════════════
• If a tool call returns an error, explain the cause, then retry with a corrected approach.
• Never leave the scene in a broken state — remove failed geometry before continuing.
• If export fails, report the exact error, then attempt export to the fallback path /tmp/export_fallback.glb.
"""


# ─── Conversation store (in-memory) ───────────────────────────────────────────

class ConversationStore:
    """Simple in-memory store for per-conversation message histories."""

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}

    def get(self, conversation_id: str) -> list[dict]:
        return self._store.setdefault(conversation_id, [])

    def append(self, conversation_id: str, message: dict) -> None:
        self._store.setdefault(conversation_id, []).append(message)

    def clear(self, conversation_id: str) -> None:
        self._store.pop(conversation_id, None)

    def list_ids(self) -> list[str]:
        return list(self._store.keys())


conversation_store = ConversationStore()


# ─── MCP Client ───────────────────────────────────────────────────────────────

class BlenderMCPClient:
    """
    Manages a single persistent stdio connection to the Blender MCP server
    and exposes a Claude-powered chat method.
    """

    def __init__(self) -> None:
        api_key = os.environ.get("API_KEY")
        base_url = os.environ.get("GLM_BASE_URL", "https://claudee.openai.azure.com/openai/v1")
        if not api_key:
            logger.error(
                "API_KEY not set. Set it in .env file."
            )

        self._client = AsyncOpenAI(
            api_key=api_key, 
            base_url=base_url,
            timeout=120.0,  # 2 minutes timeout
            max_retries=2
        )
        self._session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()
        self._tools: list[dict] = []
        self._lock = asyncio.Lock()  # serialise tool calls

    # ── Connection management ────────────────────────────────────────────────

    async def connect(
        self,
        command: str = _DEFAULT_CMD,
        args: list[str] = _DEFAULT_ARGS,
    ) -> list[dict]:
        """
        Start the Blender MCP server subprocess and initialise the session.
        Returns the list of available tools.
        Raises RuntimeError if already connected.
        """
        if self._session is not None:
            raise RuntimeError("Already connected.  Call disconnect() first.")

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=None,
        )

        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        stdio, write = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(stdio, write)
        )
        await self._session.initialize()

        raw = await self._session.list_tools()
        self._tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in raw.tools
        ]

        logger.info(
            "Connected to Blender MCP server.  Available tools: %s",
            [t["name"] for t in self._tools],
        )
        return self._tools

    async def disconnect(self) -> None:
        """Close the connection to the Blender MCP server."""
        await self._exit_stack.aclose()
        self._session = None
        self._tools = []
        self._exit_stack = AsyncExitStack()
        logger.info("Disconnected from Blender MCP server.")

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    # ── Chat (agentic loop) ──────────────────────────────────────────────────

    async def chat(
        self,
        user_message: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        """
        Run a single user turn through the Claude agentic loop.

        Claude is given the full conversation history plus the Blender tools.
        Tool calls are executed against the live MCP session and the results
        fed back to Claude until it produces a final text response.

        Returns:
            {
                "response": str,           # Claude's final text
                "tool_calls": [            # every tool call made this turn
                    {"tool": name, "input": {...}, "result": "..."},
                    ...
                ],
                "conversation_id": str,
            }
        """
        if not self._session:
            raise RuntimeError(
                "Not connected to Blender MCP server.  "
                "POST /api/blender/connect first."
            )

        # Append the new user message to history
        conversation_store.append(
            conversation_id, {"role": "user", "content": user_message}
        )
        messages = conversation_store.get(conversation_id)

        tool_calls_made: list[dict] = []

        # Convert MCP tools to OpenAI format
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in self._tools
        ]

        # Prepare messages for OpenAI (system message separate)
        openai_messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ] + messages

        async with self._lock:
            while True:
                response = await self._client.chat.completions.create(
                    model=GLM_MODEL,
                    messages=openai_messages,
                    tools=openai_tools if openai_tools else None,
                    tool_choice="auto" if openai_tools else None,
                    max_tokens=4096,
                    temperature=0.2,
                )

                choice = response.choices[0]
                message = choice.message

                # Check if finished
                if choice.finish_reason == "stop":
                    final_text = message.content or ""
                    # Persist assistant turn
                    conversation_store.append(
                        conversation_id,
                        {"role": "assistant", "content": final_text},
                    )
                    glb_path = _extract_glb_path(final_text, tool_calls_made)
                    return {
                        "response": final_text,
                        "tool_calls": tool_calls_made,
                        "conversation_id": conversation_id,
                        "glb_path": glb_path,
                    }

                # Handle tool calls
                if choice.finish_reason == "tool_calls" and message.tool_calls:
                    # Persist assistant turn
                    conversation_store.append(
                        conversation_id,
                        {
                            "role": "assistant",
                            "content": message.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in message.tool_calls
                            ],
                        },
                    )

                    # Execute each tool call
                    for tool_call in message.tool_calls:
                        func_name = tool_call.function.name
                        try:
                            func_args = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            func_args = {}

                        logger.info(
                            "Calling Blender tool '%s' with input: %s",
                            func_name,
                            func_args,
                        )

                        try:
                            mcp_result = await self._session.call_tool(
                                func_name, func_args
                            )
                            result_text = (
                                mcp_result.content[0].text
                                if mcp_result.content
                                else "(no output)"
                            )
                        except Exception as exc:
                            result_text = f"Error: {exc}"
                            logger.error(
                                "Tool '%s' raised an error: %s",
                                func_name,
                                exc,
                                exc_info=True,
                            )

                        tool_calls_made.append(
                            {
                                "tool": func_name,
                                "input": func_args,
                                "result": result_text,
                            }
                        )

                        # Append tool result for next turn
                        conversation_store.append(
                            conversation_id,
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": result_text,
                            },
                        )

                    # Rebuild messages for next iteration
                    messages = conversation_store.get(conversation_id)
                    openai_messages = [
                        {"role": "system", "content": SYSTEM_PROMPT}
                    ] + messages
                    continue  # next iteration

                # Unexpected finish reason
                logger.warning("Unexpected finish_reason: %s", choice.finish_reason)
                final_text = message.content or "(no response)"
                glb_path = _extract_glb_path(final_text, tool_calls_made)
                return {
                    "response": final_text,
                    "tool_calls": tool_calls_made,
                    "conversation_id": conversation_id,
                    "glb_path": glb_path,
                }


# ─── Helpers ─────────────────────────────────────────────────────────────────

import re as _re

def _extract_glb_path(
    response_text: str,
    tool_calls: list[dict],
) -> Optional[str]:
    """
    Try to extract the exported GLB file path from:
    1. A "GLB_SAVED: <path>" line in the final response text.
    2. A "GLB_SAVED: <path>" line printed to stdout inside any tool-call result.
    Returns None if not found.
    """
    pattern = _re.compile(r'GLB_SAVED:\s*(\S+\.glb)', _re.IGNORECASE)

    # 1. Check the assistant's final response text
    m = pattern.search(response_text)
    if m:
        return m.group(1)

    # 2. Check tool-call results (stdout from execute_blender_code)
    for tc in tool_calls:
        m = pattern.search(tc.get("result", ""))
        if m:
            return m.group(1)

    return None


# ─── Singleton ────────────────────────────────────────────────────────────────

blender_client = BlenderMCPClient()
