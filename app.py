from __future__ import annotations

import os
import re
import asyncio
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import markdown
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://vexa.iron.md")
VEXA_API_BASE = os.environ.get("VEXA_API_BASE", "http://vexa:8056").rstrip("/")
VEXA_API_KEY = os.environ.get("VEXA_API_KEY", "")
VEXA_ADMIN_API_TOKEN = os.environ.get("VEXA_ADMIN_API_TOKEN", VEXA_API_KEY)
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "45"))
PROTOTYPE_USER_EMAIL = os.environ.get("PROTOTYPE_USER_EMAIL", "prototype@layers.local")
PROTOTYPE_USER_NAME = os.environ.get("PROTOTYPE_USER_NAME", "Layers Prototype")

app = FastAPI(title="Vexa Layers Prototype")
templates = Jinja2Templates(directory="templates")
_prototype_user_api_key: str | None = None
_prototype_user_lock = asyncio.Lock()


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


def transcript_to_html(payload: dict[str, Any]) -> str:
    segments = payload.get("segments") or []
    if not segments:
        return "<p>No transcript segments yet.</p>"
    lines = []
    for segment in segments:
        speaker = segment.get("speaker") or segment.get("participant_name") or "Speaker"
        text = segment.get("text") or ""
        if not text.strip():
            continue
        lines.append(f"**{speaker}:** {text.strip()}")
    if not lines:
        return "<p>No transcript segments yet.</p>"
    return markdown.markdown("\n\n".join(lines))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_base_url": APP_BASE_URL,
            "default_link": "https://meet.google.com/abc-defg-hij",
        },
    )


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
    created = await vexa_request("POST", "/bots", payload)
    return JSONResponse(
        {
            "platform": payload["platform"],
            "native_meeting_id": payload["native_meeting_id"],
            "meeting_url": meeting_url,
            "created": created,
        }
    )


@app.get("/api/captures/{platform}/{native_meeting_id}")
async def capture_status(platform: str, native_meeting_id: str):
    transcript = await vexa_request("GET", f"/transcripts/{platform}/{native_meeting_id}")
    rendered = transcript_to_html(transcript)
    return JSONResponse({"transcript": transcript, "transcript_html": rendered})
