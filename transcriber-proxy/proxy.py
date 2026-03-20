import os
import struct
import wave
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()
openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
openai_api_key = os.environ["OPENAI_API_KEY"]
openai_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
default_language = os.environ.get("OPENAI_DEFAULT_LANGUAGE")
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


def resolve_language(language: str | None, fallback_language: str | None) -> str | None:
    candidate = (language or "").strip()
    if candidate:
        return candidate
    fallback = (fallback_language or "").strip()
    return fallback or None


def float32le_to_wav(content: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    pcm16 = bytearray()
    for (sample,) in struct.iter_unpack("<f", content):
        clamped = max(-1.0, min(1.0, sample))
        pcm16.extend(struct.pack("<h", int(clamped * 32767)))

    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(pcm16))
    return buffer.getvalue()


def normalize_audio_upload(
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> tuple[str, bytes, str]:
    upload_filename = build_upload_filename(filename, content_type)
    suffix = Path(upload_filename).suffix.lower()
    if suffix == ".f32":
        wav_filename = f"{Path(upload_filename).stem}.wav"
        return wav_filename, float32le_to_wav(content), "audio/wav"
    return upload_filename, content, content_type or "application/octet-stream"


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
    upload_filename, upload_content, upload_content_type = normalize_audio_upload(
        file.filename,
        file.content_type,
        content,
    )
    request_language = resolve_language(language, default_language)
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{openai_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            data={
                "model": openai_model,
                "response_format": "json",
                **({"language": request_language} if request_language else {}),
            },
            files={
                "file": (
                    upload_filename,
                    upload_content,
                    upload_content_type,
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
