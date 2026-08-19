# Agentic Contract Analyzer

A local, fully free, multi-agent AI system that analyzes legal contracts end-to-end: extracts key clauses, assesses risk, pauses for human review on high-risk findings, and produces a plain-language summary — with long-term memory and full observability.

Built as a hands-on exploration of agentic AI architecture: message queues, human-in-the-loop workflows, vector memory, and distributed tracing, all running locally with Docker.

## How it works

1. **Clause Extractor Agent** — pulls parties, duration, obligations, termination conditions, and payment terms out of the raw contract text (PDF or TXT).
2. **Risk Analyzer Agent** — rates each clause `low` / `medium` / `high` risk, using similar clauses from previously analyzed contracts (via vector search) as extra context for consistency.
3. **Human-in-the-Loop checkpoint** — if any clause is rated `high` risk, the pipeline pauses and waits for a human decision (approve/reject) before continuing.
4. **Summarizer Agent** — once approved, produces a plain-language summary of the contract and its key risks.
5. **Long-term memory** — every completed analysis is stored in a vector database, so future contracts benefit from precedent.

All three agents run on Groq's free LLM API. Everything else — the queue, the vector database, the tracing backend — runs locally in Docker, at zero cost.

## Architecture

- The user uploads a contract through the web UI, which sends it to the FastAPI `api` service (`POST /analyze`).
- `api` publishes a job to a RabbitMQ queue and writes the job status to Redis.
- The `worker` service consumes the queue and runs the 3-agent pipeline, updating job status in Redis as it goes.
- If a clause is high risk, the worker pauses and waits on a second RabbitMQ queue for a human decision, submitted via `POST /jobs/{id}/decision`.
- Completed analyses are stored in Qdrant (vector memory) for future reference.
- Every step in both `api` and `worker` is traced with OpenTelemetry and visualized in Jaeger.

## Tech stack

- **API**: FastAPI, served with a static HTML/JS frontend (no framework, no build step)
- **Messaging**: RabbitMQ (raw `pika`, two queues — one for new contracts, one for human decisions)
- **Shared state**: Redis (job status, since the API and worker are separate processes)
- **LLM**: Groq (`openai/gpt-oss-120b`) — free tier
- **Vector memory**: Qdrant + `fastembed` (local embeddings, no API key needed)
- **Observability**: OpenTelemetry + Jaeger (full distributed tracing across every pipeline stage)
- **Infra**: Docker Compose (6 services, all local)

## Features

- Upload a contract as PDF or TXT
- Automatic clause extraction and risk scoring
- Human-in-the-loop approval gate for high-risk contracts
- Long-term memory — the system gets more consistent over time as it sees more contracts
- Full request tracing (Jaeger) showing exactly how long each agent step took
- Simple web UI — no need to touch Swagger/API docs to use it

## Running locally

Requirements: Docker Desktop, a free Groq API key (console.groq.com).

```bash
git clone https://github.com/SSSA335/agentic-contract-analyzer.git
cd agentic-contract-analyzer

# create a .env file with:
# GROQ_API_KEY=your_key_here

docker compose up --build
```

Then open:

- **App UI**: http://localhost:8000
- **Jaeger tracing UI**: http://localhost:16686
- **RabbitMQ management**: http://localhost:15672 (guest/guest)

## Project structure

- `api/` — FastAPI app: upload endpoint, job status, decision endpoint, static UI
  - `api/static/` — Frontend (index.html)
- `worker/` — Consumes RabbitMQ queues, runs the 3-agent pipeline
  - `worker/agents.py` — Clause Extractor / Risk Analyzer / Summarizer prompts + Groq calls
  - `worker/memory.py` — Qdrant vector storage + similarity search
  - `worker/tasks.py` — Queue consumers, OpenTelemetry spans
- `docker-compose.yml` — defines all 6 services

## Notes

This is a personal learning project exploring agentic AI patterns — it is not production-hardened (no auth, no persistence beyond local volumes, in-memory job store via Redis without expiry). Contract samples used for testing are synthetic and for demonstration only.