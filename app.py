from __future__ import annotations

import os
import re
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
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "45"))

app = FastAPI(title="Vexa Layers Prototype")
templates = Jinja2Templates(directory="templates")


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


async def vexa_request(method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
    headers = {"X-API-Key": VEXA_API_KEY}
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
async def create_capture(meeting_url: str = Form(...)):
    payload = parse_meeting_link(meeting_url)
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
