import os

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()
openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
openai_api_key = os.environ["OPENAI_API_KEY"]
openai_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
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
            f"{openai_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            data={
                "model": openai_model,
                **({"language": language} if language else {}),
            },
            files={
                "file": (
                    file.filename,
                    content,
                    file.content_type or "application/octet-stream",
                )
            },
        )
    response.raise_for_status()
    payload = response.json()
    text = payload.get("text", "")
    return JSONResponse({"text": text, "model": openai_model or model})
