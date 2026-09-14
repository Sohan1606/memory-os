# Integration tests

Backend unit and integration tests live in [`../backend/tests/`](../backend/tests)
(16 modules, run with `pytest`). This directory holds the cross-stack browser
suite that exercises the built frontend against the real backend.

## `browser_qa.py`

End-to-end QA with Playwright/Chromium. It drives the actual UI and performs
real mutations — it does not mock the API.

Checks: landing render · hero stats sourced from live `/api/health` · canvas
elements present · retrieval scores and reasons · memory explorer populated ·
inspector drawer · conflict resolution reaching v02 · architecture health
readout · agent chat over HTTP · cross-thread long-term recall · mobile overflow
at 390px (two routes) · reduced-motion rendering · zero console errors.

### Running

Both servers must be up, and the frontend must be a production build.

```bash
# terminal 1
cd backend && python -m uvicorn app.main:app --port 8000

# terminal 2
cd frontend && npm run build && npm start

# terminal 3
pip install playwright && python -m playwright install chromium
python tests/browser_qa.py
```

Exits non-zero if any check fails. Screenshots are written to the working
directory as `docs/screenshots/01-hero.png` … `docs/screenshots/15-reduced-motion.png`.

**Last verified run: 14/14 PASS, 0 console errors.**

### Not covered

Voice capture needs a real microphone and a Chromium user gesture, so the
SpeechRecognition path was verified manually rather than here.
