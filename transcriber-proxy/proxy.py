import logging
import os
import struct
import wave
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("transcriber-proxy")

app = FastAPI()
openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
openai_api_key = os.environ["OPENAI_API_KEY"]
openai_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
default_language = os.environ.get("OPENAI_DEFAULT_LANGUAGE")
api_key = os.environ["TRANSCRIBER_PROXY_KEY"]


def check_auth(auth: str | None) -> None:
    expected = f"Bearer {api_key}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


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


# ---------------------------------------------------------------------------
# Audio format detection and conversion
# ---------------------------------------------------------------------------

WAV_HEADER = b"RIFF"
FLAC_HEADER = b"fLaC"
OGG_HEADER = b"OggS"
MP3_SYNC = b"\xff\xfb"
MP3_ID3 = b"ID3"


def is_valid_audio_container(content: bytes) -> bool:
    """Check if content starts with a known audio container header."""
    if len(content) < 12:
        return False
    return (
        content[:4] == WAV_HEADER
        or content[:4] == FLAC_HEADER
        or content[:4] == OGG_HEADER
        or content[:2] == MP3_SYNC
        or content[:3] == MP3_ID3
    )


def is_likely_float32_pcm(content: bytes) -> bool:
    """Heuristic: check if content looks like raw float32 PCM audio.

    Raw float32 PCM has values in [-1.0, 1.0] range. We sample a few
    values and check if they fall within a reasonable range.
    """
    if len(content) < 16 or len(content) % 4 != 0:
        return False
    # Sample up to 100 evenly-spaced float32 values
    num_samples = min(100, len(content) // 4)
    step = max(1, (len(content) // 4) // num_samples)
    in_range = 0
    total = 0
    for i in range(0, len(content) - 3, step * 4):
        (val,) = struct.unpack_from("<f", content, i)
        total += 1
        if -1.5 <= val <= 1.5:
            in_range += 1
        if total >= num_samples:
            break
    # If most samples are in float range, it's likely raw float32
    return total > 0 and (in_range / total) > 0.8


def float32le_to_wav(content: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Convert raw float32 little-endian PCM to WAV."""
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


def normalize_audio(
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> tuple[str, bytes, str]:
    """Detect actual audio format and convert if needed.

    Priority:
    1. If content has a valid audio container header → pass through
    2. If content looks like raw float32 PCM → convert to WAV
    3. If filename has .f32 extension → convert to WAV
    4. Otherwise → pass through and let OpenAI handle it
    """
    name = filename or "audio"
    suffix = Path(name).suffix.lower()

    # Already a valid audio file → pass through
    if is_valid_audio_container(content):
        ct = content_type or "audio/wav"
        if not Path(name).suffix:
            name = f"{name}.wav"
        logger.info("Audio pass-through: %s (%d bytes, content-type=%s)", name, len(content), ct)
        return name, content, ct

    # Raw float32 PCM detected by content analysis or file extension
    if is_likely_float32_pcm(content) or suffix == ".f32":
        wav_content = float32le_to_wav(content)
        wav_name = f"{Path(name).stem}.wav"
        logger.info(
            "Converted float32 PCM → WAV: %s → %s (%d → %d bytes)",
            name, wav_name, len(content), len(wav_content),
        )
        return wav_name, wav_content, "audio/wav"

    # Unknown format — pass through, OpenAI may handle it
    ct = content_type or "application/octet-stream"
    logger.warning(
        "Unknown audio format, passing through: %s (%d bytes, content-type=%s, first_bytes=%s)",
        name, len(content), ct, content[:8].hex() if content else "empty",
    )
    return name, content, ct


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

    logger.info(
        "Received audio: filename=%s, content_type=%s, size=%d bytes",
        file.filename, file.content_type, len(content),
    )

    if len(content) < 100:
        logger.warning("Audio too small (%d bytes), skipping", len(content))
        return JSONResponse({"text": "", "model": openai_model or model})

    upload_filename, upload_content, upload_content_type = normalize_audio(
        file.filename,
        file.content_type,
        content,
    )
    request_language = resolve_language(language, default_language)

    logger.info(
        "Sending to OpenAI: filename=%s, content_type=%s, size=%d, model=%s, language=%s",
        upload_filename, upload_content_type, len(upload_content), openai_model, request_language,
    )

    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{openai_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            data={
                "model": openai_model,
                "response_format": "verbose_json",
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
        logger.error("OpenAI error %d: %s", response.status_code, response.text[:500])
        raise HTTPException(
            status_code=502,
            detail=format_upstream_error(error_payload),
        )

    payload = response.json()
    text = payload.get("text", "")
    logger.info("Transcription result: %d chars, language=%s", len(text), payload.get("language", "?"))

    # Return in OpenAI-compatible format with segments if available
    result: dict = {"text": text, "model": openai_model or model}
    if "segments" in payload:
        result["segments"] = payload["segments"]
    if "language" in payload:
        result["language"] = payload["language"]
    if "duration" in payload:
        result["duration"] = payload["duration"]
    return JSONResponse(result)
