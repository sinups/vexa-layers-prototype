import os
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()
openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
openai_api_key = os.environ["OPENAI_API_KEY"]
openai_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
api_key = os.environ["TRANSCRIBER_PROXY_KEY"]
extension_by_content_type = {
    "audio/mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/mpga": ".mpga",
    "audio/m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "video/webm": ".webm",
}


def check_auth(auth: str | None) -> None:
    expected = f"Bearer {api_key}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def build_upload_filename(filename: str | None, content_type: str | None) -> str:
    if filename:
        suffix = Path(filename).suffix
        if suffix:
            return filename
        inferred_suffix = extension_by_content_type.get((content_type or "").lower(), ".wav")
        return f"{filename}{inferred_suffix}"
    return f"audio{extension_by_content_type.get((content_type or '').lower(), '.wav')}"


def format_upstream_error(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return "OpenAI transcription request failed."


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
    upload_filename = build_upload_filename(file.filename, file.content_type)
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{openai_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            data={
                "model": openai_model,
                "response_format": "json",
                **({"language": language} if language else {}),
            },
            files={
                "file": (
                    upload_filename,
                    content,
                    file.content_type or "application/octet-stream",
                )
            },
        )
    if response.is_error:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = response.text
        raise HTTPException(
            status_code=502,
            detail=format_upstream_error(error_payload),
        )
    payload = response.json()
    text = payload.get("text", "")
    return JSONResponse({"text": text, "model": openai_model or model})
