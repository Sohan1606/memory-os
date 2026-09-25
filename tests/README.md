# Integration tests

Backend unit and integration tests live in [`../backend/tests/`](../backend/tests).
This directory holds cross-stack browser suites that exercise the built frontend
against the real backend.

## `browser_qa.py`

End-to-end QA with Playwright/Chromium. It drives the rendered application and
its same-origin HTTP boundary; it does not mock the API or write directly to the
database.

Coverage includes:

- landing render, live health, canvases, retrieval, memory explorer, inspector,
  conflict resolution, architecture, mobile overflow, and reduced motion;
- a real memory-writing chat turn followed by the actual **+ New conversation**
  UI flow, capture of the generated `thread-<timestamp>` id, and cross-thread
  long-term recall (no hard-coded dynamic id);
- bounded V10.2 governance checks: an irrelevant turn emits no governance
  stages, genuine canonical reaction evidence causes only the stages for work
  that ran, the `/api/v10/governance` namespace is reachable and namespace
  scoped, summaries are concise and contain no hidden reasoning, and an
  unconfirmed adaptation is never presented as applied;
- honest console and page-error recording.

A clean browser run does **not** manufacture an ACTIVE governed adaptation.
ATTENTION adaptations require at least three independent episodes across at
least two days, and the public application intentionally has no test-only clock
or unsafe activation endpoint. If an ACTIVE adaptation already exists, browser
QA requires a later turn to record a real consultation and named consumer. If
none exists, that one check is explicitly reported as deferred; the deterministic
clock-controlled active/future-turn path is covered by
`backend/tests/test_v102_governance_runtime.py`.

### Running

Both servers must be up, and the frontend must be a production build.
The deterministic demo provider is appropriate for browser QA; unavailable
Ollama or semantic-embedding dependencies must be reported honestly rather than
simulated.

```bash
# terminal 1
cd backend
MODEL_PROVIDER=demo MEMORY_OS_DISABLE_EMBEDDINGS=1 \
  python -m uvicorn app.main:app --port 8000

# terminal 2
cd frontend && npm run build && npm start

# terminal 3
pip install playwright && python -m playwright install chromium
python tests/browser_qa.py
```

The runner continues independent sections after a selector or assertion failure,
then exits non-zero with the complete failure list. Screenshots are written to
the established, gitignored `docs/screenshots/` QA location.

### Not covered

Voice capture needs a real microphone and a Chromium user gesture, so the
SpeechRecognition path is not automated here. Real Ollama turns require the
configured model to be installed locally; DEMO / not-connected status is not a
failure when that prerequisite is unavailable.
