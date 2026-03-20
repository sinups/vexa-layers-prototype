from __future__ import annotations

import os
import re
import asyncio
import base64
from typing import Any
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from analysis import generate_summary, extract_graph

APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://vexa.iron.md")
VEXA_API_BASE = os.environ.get("VEXA_API_BASE", "http://vexa:8056").rstrip("/")
VEXA_API_KEY = os.environ.get("VEXA_API_KEY", "")
VEXA_ADMIN_API_TOKEN = os.environ.get("VEXA_ADMIN_API_TOKEN", VEXA_API_KEY)
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "45"))
PROTOTYPE_USER_EMAIL = os.environ.get("PROTOTYPE_USER_EMAIL", "summary@layers.md")
PROTOTYPE_USER_NAME = os.environ.get("PROTOTYPE_USER_NAME", "Layers Prototype")
BOT_DISPLAY_NAME = os.environ.get("BOT_DISPLAY_NAME", "Layers Summarize")
BOT_AVATAR_URL = os.environ.get("BOT_AVATAR_URL", f"{APP_BASE_URL}/static/layers-logo.png")
BOT_AVATAR_PATH = Path(os.environ.get("BOT_AVATAR_PATH", "static/layers-logo.png"))
LAYERS_API_BASE = os.environ.get("LAYERS_API_BASE", "https://api.layers.md")
LAYERS_USER_EMAIL = os.environ.get("LAYERS_USER_EMAIL", "")
LAYERS_USER_PASSWORD = os.environ.get("LAYERS_USER_PASSWORD", "")

app = FastAPI(title="Layers Summarize")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
_prototype_user_api_key: str | None = None
_prototype_user_lock = asyncio.Lock()


def load_bot_avatar_data_uri() -> str | None:
    try:
        raw = BOT_AVATAR_PATH.read_bytes()
    except FileNotFoundError:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


BOT_AVATAR_DATA_URI = load_bot_avatar_data_uri()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_meeting_link(url: str) -> dict[str, str]:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    query = parse_qs(parsed.query)

    if host.endswith("meet.google.com") and path:
        return {
            "platform": "google_meet",
            "native_meeting_id": path,
            "meeting_url": url,
        }

    zoom_match = re.search(r"/j/(\d+)", parsed.path)
    if "zoom.us" in host and zoom_match:
        payload = {
            "platform": "zoom",
            "native_meeting_id": zoom_match.group(1),
            "meeting_url": url,
        }
        pwd = query.get("pwd", [None])[0]
        if pwd:
            payload["passcode"] = pwd
        return payload

    teams_id_match = re.search(r"/meet/(\d{9,15})", parsed.path)
    if ("teams.live.com" in host or "teams.microsoft.com" in host) and teams_id_match:
        payload = {
            "platform": "teams",
            "native_meeting_id": teams_id_match.group(1),
            "meeting_url": url,
        }
        passcode = query.get("p", [None])[0]
        if passcode:
            payload["passcode"] = passcode
        return payload

    raise HTTPException(
        status_code=400,
        detail="Unsupported meeting link. Prototype currently expects Google Meet, Zoom, or Teams links.",
    )


def is_existing_meeting_conflict(exc: HTTPException) -> bool:
    return exc.status_code == 409 and "active or requested meeting already exists" in str(exc.detail).lower()


def is_missing_meeting_error(exc: HTTPException) -> bool:
    return exc.status_code == 404 and "no active meeting found" in str(exc.detail).lower()


# ---------------------------------------------------------------------------
# Vexa API helpers
# ---------------------------------------------------------------------------

async def ensure_prototype_user_api_key() -> str:
    global _prototype_user_api_key
    if _prototype_user_api_key:
        return _prototype_user_api_key

    async with _prototype_user_lock:
        if _prototype_user_api_key:
            return _prototype_user_api_key

        headers = {"X-Admin-API-Key": VEXA_ADMIN_API_TOKEN, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            user_response = await client.post(
                f"{VEXA_API_BASE}/admin/users",
                headers=headers,
                json={
                    "email": PROTOTYPE_USER_EMAIL,
                    "name": PROTOTYPE_USER_NAME,
                    "max_concurrent_bots": 5,
                },
            )
            if user_response.status_code >= 400:
                raise HTTPException(status_code=user_response.status_code, detail=user_response.text)
            user = user_response.json()
            user_id = user["id"]

            token_response = await client.post(
                f"{VEXA_API_BASE}/admin/users/{user_id}/tokens",
                headers={"X-Admin-API-Key": VEXA_ADMIN_API_TOKEN},
            )
            if token_response.status_code >= 400:
                raise HTTPException(status_code=token_response.status_code, detail=token_response.text)
            token_payload = token_response.json()
            _prototype_user_api_key = token_payload["token"]
            return _prototype_user_api_key


async def vexa_request(method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
    api_key = await ensure_prototype_user_api_key()
    headers = {"X-API-Key": api_key}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.request(
            method,
            f"{VEXA_API_BASE}{path}",
            headers=headers,
            json=json_body,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    if not response.content:
        return {}
    return response.json()


def normalize_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    segments = payload.get("segments") or []
    result = []
    for seg in segments:
        speaker = seg.get("speaker") or seg.get("participant_name") or "Speaker"
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        result.append({
            "speaker": speaker,
            "text": text,
            "timestamp": seg.get("timestamp") or seg.get("start_time") or seg.get("created_at"),
        })
    return result


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_base_url": APP_BASE_URL,
        "default_link": "https://meet.google.com/abc-defg-hij",
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", {
        "app_base_url": APP_BASE_URL,
    })


@app.get("/admin/session/{platform}/{native_meeting_id}", response_class=HTMLResponse)
async def session_page(request: Request, platform: str, native_meeting_id: str):
    return templates.TemplateResponse(request, "session.html", {
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "app_base_url": APP_BASE_URL,
    })


# ---------------------------------------------------------------------------
# Capture API (existing)
# ---------------------------------------------------------------------------

@app.post("/api/captures")
async def create_capture(request: Request, meeting_url: str | None = Form(default=None)):
    if meeting_url is None:
        body = await request.json()
        meeting_url = body.get("meeting_url")
    if not meeting_url:
        raise HTTPException(status_code=422, detail="meeting_url is required")
    payload = parse_meeting_link(meeting_url)
    payload.setdefault("recording_enabled", True)
    payload.setdefault("transcribe_enabled", True)
    payload.setdefault("transcription_tier", "realtime")
    payload.setdefault("bot_name", BOT_DISPLAY_NAME)
    payload.setdefault("default_avatar_url", BOT_AVATAR_URL)
    reused = False
    transcript = None
    try:
        created = await vexa_request("POST", "/bots", payload)
    except HTTPException as exc:
        if not is_existing_meeting_conflict(exc):
            raise
        reused = True
        transcript = await vexa_request(
            "GET",
            f"/transcripts/{payload['platform']}/{payload['native_meeting_id']}",
        )
        created = {
            "status": transcript.get("status", "requested"),
            "message": "Reusing the existing bot capture for this meeting.",
        }
    if BOT_AVATAR_DATA_URI:
        try:
            await vexa_request(
                "PUT",
                f"/bots/{payload['platform']}/{payload['native_meeting_id']}/avatar",
                {"image_base64": BOT_AVATAR_DATA_URI},
            )
        except HTTPException:
            pass
    response_payload = {
        "platform": payload["platform"],
        "native_meeting_id": payload["native_meeting_id"],
        "meeting_url": meeting_url,
        "created": created,
        "reused": reused,
    }
    if transcript is not None:
        response_payload["transcript"] = transcript
    return JSONResponse(response_payload)


@app.get("/api/captures/{platform}/{native_meeting_id}")
async def capture_status(platform: str, native_meeting_id: str):
    try:
        transcript = await vexa_request("GET", f"/transcripts/{platform}/{native_meeting_id}")
    except HTTPException as exc:
        if is_missing_meeting_error(exc):
            raise HTTPException(
                status_code=404,
                detail="No capture found for this meeting yet. Send the bot to a real active meeting link first.",
            )
        raise
    segments = normalize_segments(transcript)
    return JSONResponse({
        "transcript": transcript,
        "segments": segments,
        "status": transcript.get("status"),
    })


# ---------------------------------------------------------------------------
# Sessions API (admin)
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def list_sessions():
    """List all meetings from Vexa."""
    try:
        meetings = await vexa_request("GET", "/meetings")
    except HTTPException:
        meetings = []
    # Vexa may return a list directly or wrapped in an object
    if isinstance(meetings, dict):
        meetings = meetings.get("meetings") or meetings.get("data") or []
    return JSONResponse({"sessions": meetings})


@app.get("/api/sessions/{platform}/{native_meeting_id}")
async def get_session(platform: str, native_meeting_id: str):
    """Get full session data including transcript."""
    try:
        transcript = await vexa_request("GET", f"/transcripts/{platform}/{native_meeting_id}")
    except HTTPException as exc:
        if is_missing_meeting_error(exc):
            raise HTTPException(status_code=404, detail="Session not found.")
        raise
    segments = normalize_segments(transcript)
    return JSONResponse({
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "transcript": transcript,
        "segments": segments,
        "status": transcript.get("status"),
    })


@app.post("/api/sessions/{platform}/{native_meeting_id}/summary")
async def session_summary(platform: str, native_meeting_id: str):
    """Generate AI summary for a session."""
    try:
        transcript = await vexa_request("GET", f"/transcripts/{platform}/{native_meeting_id}")
    except HTTPException as exc:
        if is_missing_meeting_error(exc):
            raise HTTPException(status_code=404, detail="Session not found.")
        raise
    segments = normalize_segments(transcript)
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments to summarize.")
    try:
        summary = await generate_summary(segments)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Summary generation failed: {exc}")
    return JSONResponse({"summary": summary})


@app.post("/api/sessions/{platform}/{native_meeting_id}/graph")
async def session_graph(platform: str, native_meeting_id: str):
    """Extract knowledge graph for a session."""
    try:
        transcript = await vexa_request("GET", f"/transcripts/{platform}/{native_meeting_id}")
    except HTTPException as exc:
        if is_missing_meeting_error(exc):
            raise HTTPException(status_code=404, detail="Session not found.")
        raise
    segments = normalize_segments(transcript)
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments for graph extraction.")
    try:
        graph = await extract_graph(segments)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Graph extraction failed: {exc}")
    return JSONResponse({"graph": graph})


# ---------------------------------------------------------------------------
# Layers API client
# ---------------------------------------------------------------------------

_layers_token: str | None = None
_layers_token_lock = asyncio.Lock()


async def layers_login() -> str:
    """Login to Layers API and return JWT token."""
    global _layers_token
    async with _layers_token_lock:
        if _layers_token:
            return _layers_token
        if not LAYERS_USER_EMAIL or not LAYERS_USER_PASSWORD:
            raise HTTPException(status_code=501, detail="Layers credentials not configured.")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LAYERS_API_BASE.rstrip('/')}/v1/login",
                json={"email": LAYERS_USER_EMAIL, "password": LAYERS_USER_PASSWORD},
            )
        if resp.is_error:
            raise HTTPException(status_code=502, detail=f"Layers login failed: {resp.text[:500]}")
        data = resp.json()
        # Token may be in different fields depending on API version
        _layers_token = data.get("token") or data.get("accessToken") or data.get("access_token")
        if not _layers_token:
            raise HTTPException(status_code=502, detail=f"Layers login: no token in response. Keys: {list(data.keys())}")
        return _layers_token


async def layers_request(method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
    """Make authenticated request to Layers API. Retries once on 401."""
    global _layers_token
    token = await layers_login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(method, f"{LAYERS_API_BASE.rstrip('/')}{path}", headers=headers, json=json_body)
    # If 401, clear token and retry once
    if resp.status_code == 401:
        _layers_token = None
        token = await layers_login()
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(method, f"{LAYERS_API_BASE.rstrip('/')}{path}", headers=headers, json=json_body)
    if resp.is_error:
        raise HTTPException(status_code=502, detail=f"Layers API error ({resp.status_code}): {resp.text[:500]}")
    if not resp.content:
        return {}
    return resp.json()


@app.get("/api/layers/workspaces")
async def layers_workspaces():
    """Get available Layers workspaces."""
    data = await layers_request("GET", "/v1/workspaces")
    return JSONResponse({"workspaces": data if isinstance(data, list) else data.get("workspaces", data.get("data", []))})


@app.post("/api/sessions/{platform}/{native_meeting_id}/send-to-layers")
async def send_to_layers(platform: str, native_meeting_id: str, request: Request):
    """Send session summary to Layers as a page."""
    # Get optional parentId/parentType from request body
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    parent_id = body.get("parentId")
    parent_type = body.get("parentType")

    # Get transcript + summary
    try:
        transcript = await vexa_request("GET", f"/transcripts/{platform}/{native_meeting_id}")
    except HTTPException as exc:
        if is_missing_meeting_error(exc):
            raise HTTPException(status_code=404, detail="Session not found.")
        raise
    segments = normalize_segments(transcript)
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript to send.")

    try:
        summary = await generate_summary(segments)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Summary generation failed: {exc}")

    # Build page content
    page_title = f"Meeting Summary — {PLATFORM_NAMES.get(platform, platform)} / {native_meeting_id}"
    page_body = _build_layers_page_content(summary, segments)

    # Create page in Layers
    create_payload: dict[str, Any] = {"title": page_title}
    if parent_id:
        create_payload["parentId"] = parent_id
    if parent_type:
        create_payload["parentType"] = parent_type

    page = await layers_request("POST", "/v1/pages", create_payload)
    page_id = page.get("id") or page.get("pageId")

    result = {
        "sent": True,
        "page": page,
        "page_id": page_id,
        "title": page_title,
        "content_preview": page_body[:500],
        "summary": summary,
    }

    # Try to create tasks from action items
    tasks_created = []
    if summary.get("action_items"):
        for item in summary["action_items"]:
            try:
                task_payload: dict[str, Any] = {"title": item.get("task", "Action item")}
                if page_id:
                    task_payload["parentId"] = page_id
                    task_payload["parentType"] = "page"
                task = await layers_request("POST", "/v1/project/task", task_payload)
                tasks_created.append(task)
            except Exception:
                pass
    result["tasks_created"] = tasks_created

    return JSONResponse(result)


PLATFORM_NAMES = {
    "google_meet": "Google Meet",
    "zoom": "Zoom",
    "teams": "Microsoft Teams",
}


def _build_layers_page_content(summary: dict[str, Any], segments: list[dict[str, Any]]) -> str:
    """Format summary + transcript as markdown for Layers."""
    parts = []
    parts.append(f"## Overview\n{summary.get('overview', '')}\n")

    if summary.get("topics"):
        parts.append("## Topics")
        for t in summary["topics"]:
            parts.append(f"### {t.get('title', '')}\n{t.get('summary', '')}\n")

    if summary.get("decisions"):
        parts.append("## Decisions")
        for d in summary["decisions"]:
            parts.append(f"- **{d.get('decision', '')}** — {d.get('context', '')}")
        parts.append("")

    if summary.get("action_items"):
        parts.append("## Action Items")
        for a in summary["action_items"]:
            assignee = a.get("assignee") or "TBD"
            deadline = a.get("deadline") or ""
            dl = f" (by {deadline})" if deadline else ""
            parts.append(f"- [ ] {a.get('task', '')} — @{assignee}{dl}")
        parts.append("")

    if summary.get("by_participant"):
        parts.append("## By Participant")
        for p in summary["by_participant"]:
            points = ", ".join(p.get("key_points", []))
            parts.append(f"- **{p.get('name', '')}** ({p.get('role_in_meeting', '')}): {points}")
        parts.append("")

    parts.append("## Full Transcript")
    for seg in segments:
        parts.append(f"**{seg['speaker']}:** {seg['text']}")

    return "\n".join(parts)
