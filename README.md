# Layers Summarize

Self-hosted meeting transcription: [Vexa](https://github.com/Vexa-ai/vexa) + [Vexa-Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard) + OpenAI transcription.

## Services

- **nginx** — reverse proxy, routes `/` → dashboard, `/ws` → vexa (WebSocket)
- **dashboard** — official Vexa-Dashboard (Next.js)
- **vexa** — `vexaai/vexa-lite`, bot orchestration + API
- **postgres** — database
- **transcriber-proxy** — OpenAI STT proxy (handles audio format conversion)

## Setup

```bash
cp .env.example .env
# fill secrets
docker compose up --build
```

Open `http://localhost` (or your domain).

## Deployment

Docker Compose via Coolify → `vexa.iron.md`. Nginx is the entry point service.
