"""Meeting transcript analysis: summary generation and knowledge graph extraction."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

SUMMARY_SYSTEM_PROMPT = """\
You are a meeting analyst. Given a meeting transcript, produce a structured JSON summary.
Always respond in the SAME LANGUAGE as the transcript.
Return ONLY valid JSON with this exact structure:
{
  "overview": "2-3 sentence meeting summary",
  "topics": [{"title": "topic name", "summary": "what was discussed"}],
  "decisions": [{"decision": "what was decided", "context": "why/how it was decided"}],
  "action_items": [{"task": "what needs to be done", "assignee": "who (or null)", "deadline": "when (or null)"}],
  "key_quotes": [{"speaker": "name", "quote": "notable quote"}],
  "by_participant": [{"name": "participant name", "role_in_meeting": "brief role description", "key_points": ["point 1", "point 2"]}],
  "sentiment": "overall meeting sentiment: constructive/tense/neutral/productive/etc"
}
If there are no decisions or action items, use empty arrays. Extract what you can."""

GRAPH_SYSTEM_PROMPT = """\
You are a knowledge graph extractor. Given a meeting transcript, extract entities and relationships.
Return ONLY valid JSON with this exact structure:
{
  "nodes": [
    {"id": "unique_id", "label": "display name", "type": "person|topic|decision|action|organization|product"}
  ],
  "edges": [
    {"source": "node_id", "target": "node_id", "label": "relationship description", "type": "discussed|decided|assigned|mentioned|agreed|disagreed|proposed|blocked"}
  ]
}
Rules:
- Every speaker must be a node with type "person"
- Extract key topics, decisions, action items, organizations, products as nodes
- Create edges showing who discussed/proposed/decided what
- Use short, clear labels
- Keep IDs lowercase with underscores (e.g. "john_doe", "topic_budget")
- Ensure every edge references existing node IDs
- Aim for 10-30 nodes and 15-50 edges for a typical meeting"""


def _format_transcript_text(segments: list[dict[str, Any]]) -> str:
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "Speaker")
        text = seg.get("text", "")
        ts = seg.get("timestamp", "")
        prefix = f"[{ts}] " if ts else ""
        lines.append(f"{prefix}{speaker}: {text}")
    return "\n".join(lines)


async def _call_openai_chat(
    system_prompt: str,
    user_content: str,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    key = api_key or OPENAI_API_KEY
    mdl = model or OPENAI_CHAT_MODEL
    url = (base_url or OPENAI_BASE_URL).rstrip("/")

    if not key:
        raise ValueError("OPENAI_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": mdl,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.3,
            },
        )

    if resp.is_error:
        raise RuntimeError(f"OpenAI API error {resp.status_code}: {resp.text[:500]}")

    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


async def generate_summary(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a structured meeting summary from transcript segments."""
    if not segments:
        return {
            "overview": "No transcript data available.",
            "topics": [],
            "decisions": [],
            "action_items": [],
            "key_quotes": [],
            "by_participant": [],
            "sentiment": "unknown",
        }

    transcript_text = _format_transcript_text(segments)
    user_msg = f"Meeting transcript ({len(segments)} segments):\n\n{transcript_text}"
    return await _call_openai_chat(SUMMARY_SYSTEM_PROMPT, user_msg)


async def extract_graph(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract a knowledge graph (nodes + edges) from transcript segments."""
    if not segments:
        return {"nodes": [], "edges": []}

    transcript_text = _format_transcript_text(segments)
    user_msg = f"Meeting transcript ({len(segments)} segments):\n\n{transcript_text}"
    result = await _call_openai_chat(GRAPH_SYSTEM_PROMPT, user_msg)

    # Validate: ensure all edge source/target exist in nodes
    node_ids = {n["id"] for n in result.get("nodes", [])}
    result["edges"] = [
        e for e in result.get("edges", [])
        if e.get("source") in node_ids and e.get("target") in node_ids
    ]
    return result
