# Margin — Research Document Q&A Assistant

Margin answers natural-language questions over selected research PDFs, supports comparisons across
papers, and returns page citations, tables and original figure crops. It can report partial answers or
insufficient evidence. A React interface supports uploads, document selection, chat and source inspection.

This repository is the runnable submission for the M&S Consulting Research Document Q&A Assistant
assignment. Research documents are supplied separately; the recorded evaluation used ten public research
papers.

| Deliverable | Location |
| --- | --- |
| Source code | [Frontend](research-frontend/), [backend](research-backend/), [agent and worker](research-agent/) |
| Installation, configuration and execution | This README |
| Architecture and design decisions | [Technical explanation](TECHNICAL_README.md) |
| Methodology, actual answers, results and rerun commands | [Evaluation](docs/EVALUATION.md) |
| Known issues and prioritized improvements | [Limitations](docs/LIMITATIONS.md) |

## Assignment requirements

| Requirement | Implemented behavior and evidence |
| --- | --- |
| Ask natural-language questions about documents | A text chat accepts questions about one or more selected ready PDFs; the backend scopes retrieval to those documents. |
| Receive clear, relevant and accurate answers | Structured text, tables and figure descriptions are generated from retrieved evidence. The recorded 80-question run graded 57 fully correct, 11 partial and 12 incorrect; accuracy remains inconsistent for some tables, follow-ups and cross-paper questions. |
| Identify supporting sources | Clickable citations show document titles and physical page numbers and open the original PDF; extracted figures retain original crops. Source identity validation does not guarantee that every cited claim is correct. |
| Recognize insufficient information | Answers expose supported, partial or insufficient-evidence states with explanations. All ten missing-invoice cases declined to invent an answer; some answerable questions also triggered unnecessary abstention. |
| Provide a simple interface | A React interface combines paper upload/selection, question entry, answer display, conversation history and source inspection. |

The application provides the requested end-to-end workflow. The [evaluation report](docs/EVALUATION.md)
shows representative successes and failures rather than treating implemented features as a guarantee
of accuracy on every question.

## Prerequisites

- Git, or an extracted source-code archive.
- Docker Desktop running Linux containers with Compose v2, or Docker Engine with Compose v2.
- Node.js 22 with npm, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
  uv manages Python 3.13 and the shared virtual environment; manual activation is unnecessary.
- [Ollama](https://ollama.com/) running locally for `bge-m3` embeddings.
- An OpenAI API key with model access and API quota. Chat and vision default to `gpt-5.4-mini`.
  OpenAI API usage is billed separately from ChatGPT subscriptions.
- PDFs to upload; they are not included in the repository.

Commands below use Windows PowerShell. If uv is missing on Windows, install it with
`winget install --id astral-sh.uv -e` and reopen the terminal. On other systems, follow the linked uv
installation guide. Application commands also work in a POSIX shell; use `cp` instead of `Copy-Item`.

## First-time local setup

Docker runs **PostgreSQL/pgvector and Redis**. Frontend, backend, agent API and Celery worker run in
four terminals. Ollama runs on the host. Keep all three service directories together.

### 1. Open the repository and install dependencies

For a fresh clone with repository access:

```powershell
git clone https://github.com/EdddTri/research-document-assistant.git
cd research-document-assistant
```

For an existing checkout or archive, open its root instead: the directory containing this README,
`pyproject.toml` and all three service folders. Run:

```powershell
docker compose version
uv --version
node --version
npm --version
uv sync --locked
npm --prefix research-frontend ci
ollama pull bge-m3
```

Keep Ollama running. If the desktop application is not already running its server, run `ollama serve`
in another terminal. Do not start a second server when port 11434 is already in use.

### 2. Configure the environment

For a new checkout only, copy the templates from the root. Preserve existing filled files:

```powershell
Copy-Item .env.example .env
Copy-Item research-frontend/.env.example research-frontend/.env
```

| File | Purpose |
| --- | --- |
| Root `.env` | Backend, agent, worker, migrations and Compose settings |
| `research-frontend/.env` | Public Vite setting: `VITE_API_BASE_URL=http://localhost:8000` |

Replace these placeholders in the root `.env`:

| Setting | What to supply |
| --- | --- |
| `POSTGRES_PASSWORD` | Database owner/migration password |
| `BACKEND_DB_PASSWORD`, `WORKER_DB_PASSWORD`, `AGENT_DB_PASSWORD` | Three distinct restricted database-role passwords |
| `JWT_SECRET` | Random signing secret, at least 32 characters; shared across the application's user tokens |
| `INTERNAL_SECRET` | Different random secret, at least 32 characters, for backend-to-agent requests |
| `OPENAI_API_KEY` | Your OpenAI API key for answers, vision and optional judging |

Generate each password/secret separately, then paste the output into its field. This does not generate
an API key:

```powershell
uv run --locked python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep URL-safe database passwords for Compose connection URLs. Local processes select their database
role automatically; do not add duplicate `DATABASE_URL` or `BROKER_URL` settings. Keep the template's
addresses, `COOKIE_SECURE=false`, `FRONTEND_ORIGIN=http://localhost:5173` and `STORAGE_ROOT=../storage-dev`.
That storage path assumes Python commands run from their service directories, as shown below.

| Model setting | Default |
| --- | --- |
| `ANSWER_MODEL` / `VISION_MODEL` | `gpt-5.4-mini` / `gpt-5.4-mini` |
| `ANSWER_REASONING_EFFORT` / `VISION_REASONING_EFFORT` | `low` / `none` |
| `VISION_MAX_TOKENS` / `VISION_TIMEOUT_SECONDS` | `4096` / `120` |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` / `EMBEDDING_BATCH_SIZE` | `bge-m3` / `1024` / `4` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` for terminal processes |
| `DOCKER_OLLAMA_BASE_URL` | `http://host.docker.internal:11434` for packaged containers |
| `RETRIEVAL_MODE` | `hybrid`; `vector` is available for comparison |

OpenAI is the only chat/vision provider in this version. Model changes require matching account access
and supported request settings. Embeddings remain BGE-M3. LangSmith is optional and disabled in the
example; see [evaluation configuration](docs/EVALUATION.md#rerun-the-evaluation). Never put server
secrets in the frontend environment or commit filled `.env` files.

### 3. Start PostgreSQL and Redis

From the **repository root**:

```powershell
docker compose --env-file .env -f research-backend/compose.dev.yaml up -d --wait
docker compose --env-file .env -f research-backend/compose.dev.yaml ps
```

The development stack publishes PostgreSQL on `127.0.0.1:5432` and Redis on `127.0.0.1:6379` by default.
It does not start Celery or application servers. `DB_PORT` and `REDIS_PORT` can change these mappings.

### 4. Migrate the database and create the administrator

Starting from the root:

```powershell
cd research-backend
uv run --locked --env-file ../.env alembic upgrade head
uv run --locked --env-file ../.env python -m research_backend.provision_roles
uv run --locked --env-file ../.env python -m research_backend.cli create-admin --email admin@example.com
cd ..
```

Choose your own email if preferred. The CLI prompts for a password of at least six characters. There
is no pre-created admin/default application password. Skip creation when reusing an existing account.
Self-registration in the interface creates readers; administrators manage documents.

### 5. Verify model connectivity

Starting from the root:

```powershell
cd research-agent
uv run --locked --env-file ../.env python -m research_agent.check_providers --embeddings-only
uv run --locked --env-file ../.env python -m research_agent.check_providers --chat-only
uv run --locked --env-file ../.env python -m research_agent.check_providers --vision-only
cd ..
```

Expect two 1,024-dimensional vectors, valid chat JSON and a recovered number from a generated image.
Chat/vision checks make small paid API calls. They verify connectivity/format, not full-paper accuracy.

### 6. Start four application terminals

Each terminal starts at the **repository root**. Run one block per terminal.

Frontend:

```powershell
cd research-frontend
npm run dev
```

Backend:

```powershell
cd research-backend
uv run --locked --env-file ../.env uvicorn research_backend.main:app --reload --port 8000
```

Agent API:

```powershell
cd research-agent
uv run --locked --env-file ../.env uvicorn research_agent.main:app --reload --port 8001
```

Ingestion worker:

```powershell
cd research-agent
uv run --locked --env-file ../.env celery -A research_agent.worker:app worker --pool=solo --concurrency=1 --loglevel=INFO
```

Each process prints its logs. Windows uses Celery's single-process `solo` pool. Wait for the worker to
report ready. Restart it after code changes, and restart affected processes after environment changes.
There is no separate dispatcher or custom application launcher.

### 7. Upload and ask a question

1. Open **http://localhost:5173** and sign in with the administrator account.
2. Use **Upload papers** and wait for **Ready**. HTTP upload completion precedes worker extraction/indexing.
3. Check up to 20 ready papers per question. Retrieval is restricted to that selection; the collection
   itself has no application-imposed paper/page limit.
4. Ask a question, such as “How do the methods in these two papers differ?”
5. Open citations to verify physical PDF pages and inspect returned tables or original crops.

API documentation is at **http://localhost:8000/docs**; health is at **http://localhost:8000/health**.
Paper/page totals describe the collection, not a quota. No PDF size/page-count ceiling is configured;
memory, disk and provider limits still apply. Papers are shared; conversations are private per user.

## Stop, restart and recover

Stop application processes with Ctrl+C. From the root, stop infrastructure while preserving data:

```powershell
docker compose --env-file .env -f research-backend/compose.dev.yaml stop
```

To restart, repeat step 3 and the four application commands. Named database/Redis volumes and
`storage-dev/` persist. Removing containers does not erase volumes. Avoid `down -v` unless you intend
to delete infrastructure data. Keep existing passwords when reusing volumes; editing `.env` alone
does not rotate database credentials.

Use **Retry** for failed/undelivered jobs and **Reprocess** to rebuild ready evidence. Processing jobs
can be retried after their 26-minute progress lease expires. Replacing/reprocessing a ready version
preserves it until the new one succeeds. Deletion blocks access but does not physically purge files.

To deliberately discard waiting Celery messages, stop backend and worker, then run from `research-agent`:

```powershell
uv run --locked --env-file ../.env celery -A research_agent.worker:app purge --force
```

This removes queued messages, not database jobs or currently executing tasks. Use Retry for documents
still needing indexing. Purging is not part of normal startup.

| Symptom | Action |
| --- | --- |
| Compose file not found | Run root commands from the root; do not repeat `research-backend/` while inside it. |
| Database/migration failure | Check Docker health, ports and retained-volume credentials. |
| Upload remains queued | Check worker and Redis; Retry resends undelivered jobs. |
| OpenAI/vision failure | Check account access/quota and worker diagnostics; Retry after resolving the cause. Invalid output can still fail a page. |
| Ollama connection/model error | Keep Ollama running, pull `bge-m3`, and check the host/container URL. |
| Sign-in cookie fails | Use `localhost` consistently and `COOKIE_SECURE=false` for local HTTP. |
| Missing/incorrect answer | Check selected ready papers and citations/extraction; see [known failures](docs/LIMITATIONS.md). |

## Optional: package the application with Docker

Use this instead of the terminal application processes. Configure root `.env` and host Ollama first.
Stop the other application mode to avoid port conflicts. Packaged/development Compose use separate
volumes, so create an admin and upload papers separately for each mode.

From the root:

```powershell
docker compose --env-file .env -f research-backend/compose.yaml up --build -d
docker compose --env-file .env -f research-backend/compose.yaml ps -a
docker compose --env-file .env -f research-backend/compose.yaml exec backend python -m research_backend.cli create-admin --email admin@example.com
docker compose --env-file .env -f research-backend/compose.yaml exec agent python -m research_agent.check_providers
```

The one-shot `migrate` service applies migrations/role grants before application startup; its successful
exit is expected. The frontend remains at `http://localhost:5173`. Only frontend/backend ports are
published, bound to loopback. PostgreSQL, Redis and the agent use the private container network.

```powershell
docker compose --env-file .env -f research-backend/compose.yaml logs --tail=100 backend agent worker migrate
docker compose --env-file .env -f research-backend/compose.yaml stop
```

Containers reach host Ollama through `DOCKER_OLLAMA_BASE_URL`. If it binds only to loopback, configure
it to accept Docker connections. On Windows, quit the existing tray server, then run
`$env:OLLAMA_HOST = "0.0.0.0:11434"` and `ollama serve`. Restrict firewall access to the local Docker
network because the endpoint is unauthenticated.

Optional pgAdmin: from the root run
`docker compose --env-file .env -f research-backend/compose.yaml --profile admin up -d pgadmin`.
Fill `PGADMIN_DEFAULT_EMAIL`/`PGADMIN_DEFAULT_PASSWORD` first and open `http://localhost:5050`.
These are pgAdmin credentials, separate from the application administrator.

Full packaged Compose startup has not been verified end to end. Recorded live experiments used the
terminal workflow; deployment also needs the measures in [limitations](docs/LIMITATIONS.md).

## Verification

From the root:

```powershell
uv sync --locked
uv run --locked pytest research-backend/tests -q
uv run --locked python scripts/check_deployment.py
npm --prefix research-frontend ci
npm --prefix research-frontend run build
```

Run the browser regression suite after installing its browser once:

```powershell
cd research-frontend
npx playwright install chromium
npm run test:e2e
cd ..
```

To use an installed Google Chrome instead of downloading Chromium, set
`$env:PLAYWRIGHT_CHANNEL = "chrome"` before `npm run test:e2e`. The recorded Windows run used Chrome.
Keep `uv` and Node/npm on PATH. The suite starts its own frontend on port 15173 and a test backend on
18010 with temporary SQLite/storage and deterministic model responses. It exercises real API routes,
authentication and native PDF ingestion without using your `.env`, documents, Redis or API credits.
Failures retain screenshots/traces under the ignored `research-frontend/test-results/` directory.

Recorded checks passed **172 Python tests and 14 browser tests**, the TypeScript/Vite build, migration/Compose
structure checks and the Python lockfile check. Mocked-provider tests verify software behavior, not
answer accuracy. The live development experiment asked 80 questions across ten papers, plus ten API
chat checks. Automatic grades were **57 fully correct, 11 partial and 12 incorrect**. See
[actual examples, methodology and results](docs/EVALUATION.md) before interpreting these scores.

For API contract changes, run `uv run --locked python scripts/export_openapi.py` from the root, then
`npx openapi-typescript ../research-backend/openapi.json -o src/generated-api.d.ts` from
`research-frontend`. Alternatively, `npm run generate:types` reads a running backend on port 8000.

Local secrets, PDFs, storage, dependencies, builds and private experiment reports are excluded from
Git and Docker contexts. Submit source/documentation; provide research documents separately.
