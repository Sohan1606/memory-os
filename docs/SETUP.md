# Setup

Prerequisites: **Node 18+** (built on 20.20.2) and **Python 3.10+** (built on
3.13). No API keys. No paid services.

First run downloads the MiniLM ONNX embedding model (~80 MB) from Chroma's CDN
and caches it in `~/.cache/chroma/`. That is the only network access required,
and it is one-time.

## Windows (PowerShell)

```powershell
# 1. Backend
cd memory-os-showcase\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000

# 2. Frontend (second terminal)
cd memory-os-showcase\frontend
npm ci
npm run dev
```

Open <http://localhost:3000>.

If PowerShell blocks activation:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## macOS / Linux

```bash
cd memory-os-showcase/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload --port 8000
```

```bash
cd memory-os-showcase/frontend
npm ci
npm run dev
```

## Production build

```bash
cd frontend
npm run build
npm start
```

## Configuration

Everything is optional — see `.env.example`. Copy it to `backend/.env` only if
you want to change something.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MODEL_PROVIDER` | `demo` | `demo`, `ollama` or `openai` |
| `MEMORY_OS_DATA_DIR` | `./.data` | Chroma, SQLite and checkpoint location |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://127.0.0.1:11434` / `llama3.1` | Ollama |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | unset / `gpt-4o-mini` | OpenAI (paid) |
| `WHISPER_MODEL` | unset | Enables faster-whisper if installed |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Where Next.js proxies `/api/*`. **Read at build time** - set it before `npm run build`, not only before `npm start`. |

## Enabling real model-driven tool calling (free, local)

The demo provider has no LLM, so the agent uses a deterministic planner. For a
model that genuinely chooses its own tools:

```bash
# install Ollama from https://ollama.com, then:
ollama pull llama3.1
pip install langchain-ollama==1.1.1
MODEL_PROVIDER=ollama python3 -m uvicorn app.main:app --port 8000
```

`/api/health` will then report `provider.tool_calling: true`.

## Optional extras

See `backend/requirements-optional.txt`. None are needed for the demo.

- **LangMem** — `pip install langmem==0.0.30`. Also needs a tool-calling
  provider; until both are present health reports `NOT CONFIGURED`.
- **faster-whisper** — `pip install faster-whisper==1.2.0` and set
  `WHISPER_MODEL=base` for server-side transcription instead of the browser API.

## Tests

```bash
cd backend
python -m pytest -q
```

## Regenerating the scroll frames

The 60 WebP frames in `frontend/public/frames/` are committed. To rebuild:

```bash
cd frontend
npm run frames
```

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `/api/health` shows `vector.mode: "keyword"` | Embedding model failed to download; check `vector.error`. Retrieval still works, less precisely. |
| Frontend loads but data is empty | Backend is not running on port 8000, or `BACKEND_URL` is wrong. |
| `ModuleNotFoundError: python_multipart` | `pip install -r requirements.txt` — FastAPI needs it at import time. |
| Port 8000 in use | `uvicorn app.main:app --port 8010`, then **rebuild** the frontend with `BACKEND_URL=http://127.0.0.1:8010 npm run build`. Rewrites are compiled into the build, so setting the variable only at start time leaves the old port baked in and every `/api/*` call returns 500. |
