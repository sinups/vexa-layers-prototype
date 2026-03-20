import os

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()
backend_url = os.environ["BACKEND_URL"]
api_key = os.environ["TRANSCRIBER_PROXY_KEY"]


def check_auth(auth: str | None) -> None:
    expected = f"Bearer {api_key}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    check_auth(authorization)
    content = await file.read()
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            backend_url,
            params={
                "task": "transcribe",
                "language": language,
                "output": "json",
                "encode": "true",
                "vad_filter": "true",
            },
            files={
                "audio_file": (
                    file.filename,
                    content,
                    file.content_type or "application/octet-stream",
                )
            },
        )
    response.raise_for_status()
    payload = response.json()
    text = payload.get("text", "")
    return JSONResponse({"text": text, "model": model})
