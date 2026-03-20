# Layers Summarize

Self-hosted meeting transcription via [Vexa](https://github.com/Vexa-ai/vexa) + [Vexa-Dashboard](https://github.com/Vexa-ai/Vexa-Dashboard) with OpenAI transcription.

## Architecture

- **dashboard** — official Vexa-Dashboard (Next.js), full meeting management UI
- **vexa** — `vexaai/vexa-lite:latest`, bot orchestration + API
- **postgres** — database
- **transcriber-proxy** — converts Vexa audio and proxies to OpenAI STT

## Setup

1. Copy `.env.example` to `.env`, fill secrets
2. Start:

```bash
git submodule update --init --recursive
docker compose up --build
```

Dashboard: `http://localhost:3001`

## Deployment

Git-backed Docker Compose via Coolify at `vexa.iron.md`.
