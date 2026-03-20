# Vexa Layers Prototype

Minimal prototype for sending a bot into an external meeting from a simple web interface.

## What this stack includes

- `vexa` using `vexaai/vexa-lite:latest`
- `postgres` for Vexa state
- `whisper` using `onerahmet/openai-whisper-asr-webservice`
- `transcriber-proxy` exposing an OpenAI-compatible `/v1/audio/transcriptions` endpoint for Vexa
- `web` FastAPI app for entering a meeting link and polling transcript output

## Supported links in the prototype

- Google Meet
- Zoom (`/j/<id>` URLs)
- Microsoft Teams (`/meet/<id>?p=...` URLs)

## Local run

1. Copy `.env.example` to `.env`
2. Fill the secrets
3. Start:

```bash
docker compose up --build
```

The UI runs on the `web` service and talks to Vexa internally.

## Intended deployment

This repo is designed to be deployed as a git-backed Docker Compose application in Coolify.

Recommended domain:

- `vexa.iron.md`

## Notes

- This is intentionally a narrow prototype: join meeting, capture transcript, poll transcript.
- Summary and Layers import come next, after bot join/transcript is stable.
