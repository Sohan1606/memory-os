# ZORQ G-0 Canonical Gate Verification

**Gate:** G-0 — pre-phase integration/regression gate (Master Implementation Specification v1 Rev 2, §11.7)
**Date:** 2026-09-28
**Branch:** `zroq/canonical-migration`
**Commit under gate:** `262159d9a3b13dda499f0bf01aafa848aae6dea9`
**Environment:** Linux sandbox, Python 3.13.14, Node v20.20.2 / npm 10.8.2
**Result:** **PASS — all 8 gate items verified. No code changes required.**

## Gate matrix

| # | Gate item | Command | Result |
|---|---|---|---|
| 1 | MEMORY//OS backend regression suite | `cd backend && MODEL_PROVIDER=demo python -m pytest -m "not slow"` | **1080 passed / 10 skipped / 14 deselected / 0 failed** (286.6s) — exactly matches the V10.2.0 release evidence in `PROJECT_STATUS.md` |
| 2 | MEMORY//OS frontend production build | `cd frontend && npm ci && npm run build` | **PASS** — all routes built (`/`, `/architecture`, `/memory`, `/observatory`, `/workspace`, `/_not-found`) |
| 3 | Frontend typecheck + lint | `npm run typecheck` (`tsc --noEmit`); `npm run lint` | **PASS** — zero TypeScript errors; "No ESLint warnings or errors" |
| 4 | ZORQ suite | `python -m unittest discover -s tests` | **256 tests: 248 executed / 8 skipped (Windows-only class) / 0 failed** — matches Phase 3C.1 Linux baseline |
| 5 | Repository import/install integrity | `pip install -e .` (zroq-core 0.2.6); `import zroq`; `python -m compileall -q src tests backend/app`; backend `from app.main import app` | **PASS** — install OK; zroq imports; compileall clean over ZORQ `src`+`tests` and MEMORY//OS `backend/app`; backend app imports with 242 routes |
| 6 | API startup/health checks | `MODEL_PROVIDER=demo uvicorn app.main:app` → `GET /api/health`, `/api/health/live`, `/api/health/ready`, `/api/metrics`, `/api/voice/status`; functional smoke `POST /api/chat`, `GET /api/conversations` | **PASS** — status ok; semantic vector mode active (local ONNX MiniLM, 384-dim); ready endpoint reports truthful dependency states (model_provider NOT_CONFIGURED in demo mode, voice browser fallback, langmem optional); real chat turn created + recalled a memory and recorded the conversation |
| 7 | Migration did not break MEMORY//OS | Items 1–3 + 6 executed on the merged tree at `262159d` | **PASS** — full backend suite, frontend build/typecheck/lint, and live API all green on the migration branch |
| 8 | Frontend/backend structurally intact | `git diff --stat 2c73c37..262159d -- backend frontend` | **EMPTY (0 lines)** — migration commit `262159d` touched no `backend/` or `frontend/` file |

## Diagnostic note (transparency)

The first backend run used `MEMORY_OS_DISABLE_EMBEDDINGS=1` (the browser-QA environment convention) and produced 2 failures: `test_runtime_singleton.py::test_concurrent_startup_keeps_semantic_retrieval` and `test_v82_real_intelligence.py::test_context_is_actually_given_to_the_model`, plus 5 extra skips.

**Root cause:** environment configuration, not code. `app/memory/vector_store.py` uses chromadb's `ONNXMiniLM_L6_V2` embedding model; the disable flag degrades semantic retrieval, so retrieved memory does not reach the model prompt. The V10.2 release gate ran with embeddings enabled.

**Resolution:** re-ran with embeddings enabled (model downloaded once, locally). The 2 tests pass individually and the complete suite returns the release-identical result (1080/10/14). **No repository code was changed for this gate.**

## Conclusion

G-0 is satisfied. The canonical migration commit preserves MEMORY//OS functionality and structure exactly, and the merged tree is fully green across backend, frontend, and ZORQ suites. Phase 3B.2 (Production MEMORY//OS Adapter) is authorized to begin per the approved sequence.

**Windows owner re-run note:** the owner's Windows 11 verification (249 passed / 7 skipped / 0 failed) remains the canonical platform run for the ZORQ suite; G-0 items 1–3, 5–6 should be repeated by the owner on Windows at their convenience (backend deps: `pip install -r backend/requirements.txt`; frontend: `npm ci && npm run build`).
