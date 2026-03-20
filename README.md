# Layers Summarize

Meeting capture + transcription + AI analysis prototype built on Vexa.

## What this stack includes

- `vexa` using `vexaai/vexa-lite:latest` — bot orchestration
- `postgres` for Vexa state
- `transcriber-proxy` — OpenAI-compatible `/v1/audio/transcriptions` endpoint (f32→wav conversion)
- `web` — FastAPI app: capture UI, admin panel, transcript viewer, AI summary, knowledge graph

## Features

- **Capture**: send bot to Google Meet / Zoom / Teams
- **Real-time transcript**: auto-polling with speaker colors, timestamps
- **Admin panel**: list all sessions, view status
- **AI Summary**: structured analysis (overview, topics, decisions, actions, participants)
- **Knowledge Graph**: entity/relation extraction + D3.js visualization
- **Layers integration**: send summary as a page to Layers API

## Pages

- `/` — capture page (send bot to meeting)
- `/admin` — sessions list
- `/admin/session/{platform}/{meeting_id}` — session detail (transcript, summary, graph, layers)

## Local run

1. Copy `.env.example` to `.env`
2. Fill the secrets
3. Start:

```bash
docker compose up --build
```

## Deployment

Git-backed Docker Compose via Coolify at `vexa.iron.md`.
