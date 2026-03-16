import asyncio
import json
import re
import time
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel


class ChatRequest(BaseModel):
    search_id: str
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    explanation: str  # Detailed explanation for TTS narration
    conversation_id: str
    matched_label: Optional[str] = None


# Store (timestamp, messages) to enable TTL-based pruning
_chat_histories: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _pick_closest_label(candidate: Optional[str], labels: List[str]) -> Optional[str]:
    if not candidate or not labels:
        return None

    normalized_candidate = _normalize_label(candidate)
    if not normalized_candidate:
        return None

    for label in labels:
        normalized_label = _normalize_label(label)
        if not normalized_label:
            continue
        if normalized_candidate == normalized_label:
            return label
        if normalized_candidate in normalized_label or normalized_label in normalized_candidate:
            return label

    try:
        from rapidfuzz import fuzz, process

        match = process.extractOne(
            normalized_candidate,
            labels,
            scorer=fuzz.WRatio,
            score_cutoff=60,
        )
        if match:
            return str(match[0])
    except Exception:
        pass

    return None


async def handle_model_chat(request: ChatRequest, db, groq_client, logger) -> ChatResponse:
    global _chat_histories
    
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    record = await db.searches.find_one({"id": request.search_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Search not found")

    primary = record.get("primary_model") or {}
    title = primary.get("title") or "Unknown model"
    description = primary.get("description") or ""
    tags = primary.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]

    # Extract node names from primary model only, not all candidates
    primary_result = next(
        (r for r in record.get("scored_results", [])
         if r.get("model", {}).get("id") == primary.get("id")),
        None,
    )
    node_names = primary_result.get("node_names", []) if primary_result else []

    original_prompt = record.get("original_prompt", "")
    attributes = record.get("attributes") or {}
    attributes_str = json.dumps(attributes)

    label_text = ", ".join(node_names) if node_names else "none"
    conversation_id = request.conversation_id or str(uuid.uuid4())

    # Prune expired conversations (TTL: 1 hour) to prevent unbounded memory growth
    cutoff = time.time() - 3600
    _chat_histories = {
        k: v for k, v in _chat_histories.items() if v[0] > cutoff
    }

    # Initialize conversation history if not present
    if conversation_id not in _chat_histories:
        _chat_histories[conversation_id] = (time.time(), [])

    _, history = _chat_histories[conversation_id]

    system_prompt = (
        "You are an intelligent assistant for a 3D medical/anatomical model viewer. "
        "For each user question, you MUST provide: "
        "1) A brief answer, "
        "2) The exact name of the relevant anatomical structure/segment, "
        "3) A detailed, educational explanation suitable for text-to-speech narration.\n"
        f"Model title: {title}\n"
        f"Model description: {description}\n"
        f"Model tags: {', '.join(tags) if tags else 'none'}\n"
        f"Original search prompt: {original_prompt}\n"
        f"Extracted attributes: {attributes_str}\n"
        f"Available anatomical structures/segments: {label_text}\n\n"
        "Return ONLY valid JSON with this exact structure: "
        '{"answer":"<concise answer in 1-2 sentences>",'
        '"best_label":"<exact segment name from available segments or null>",'
        '"explanation":"<detailed 2-3 paragraph explanation suitable for text-to-speech narration. Explain the anatomical function, location, and relevance to the user question. Use clear, educational language.>"}. '
        "If no suitable segment exists, set best_label to null. "
        "The explanation should be thorough, educational, and engaging for audio narration."
    )

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": message}
    ]

    def _call_chat():
        return groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1000,  # Increased for detailed explanations
        )

    try:
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(None, _call_chat)
        content = completion.choices[0].message.content or "{}"
        payload = json.loads(content)
    except Exception as exc:
        logger.error("Model chat error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Chat failed")

    answer = str(payload.get("answer") or "I could not answer that.")
    explanation = str(payload.get("explanation") or answer)  # Fallback to answer if no explanation
    label_from_llm = payload.get("best_label")
    matched_label = _pick_closest_label(str(label_from_llm) if label_from_llm else None, node_names)

    # Append to stored history dict, not local reference
    _, history_list = _chat_histories[conversation_id]
    history_list.append({"role": "user", "content": message})
    history_list.append({"role": "assistant", "content": answer})
    if len(history_list) > 20:
        history_list = history_list[-20:]
        _chat_histories[conversation_id] = (time.time(), history_list)

    return ChatResponse(
        response=answer,
        explanation=explanation,
        conversation_id=conversation_id,
        matched_label=matched_label,
    )