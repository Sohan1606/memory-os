# MEMORY//OS V9 / V9.0.1 verification

Verified 2026-09-22 in the release workspace.

## V9.0.1 hardening results

```bash
PYTHONPATH=. pytest -q tests/test_v901_hardening.py \
  tests/test_v901_real_model.py tests/test_v9_*.py
# 46 passed, 1 skipped

PYTHONPATH=. pytest -q
# 944 passed, 23 skipped

npm ci
npm run typecheck
npm run lint
npm run build
# all exit 0; production build generated 8/8 pages

python tests/v9_browser_qa.py
# 16 passed, 0 failed
python tests/v851_browser_qa.py
# 12 passed, 0 failed
```

Live-surface tests observed `FORMING_RESPONSE: ACTIVE` from the persisted
EventBus before `/api/chat` completed, then verified the final
`WAITING_FOR_USER` surface. Lifecycle, correlation isolation, namespace
isolation, absent-stage behavior and honest degradation passed.

Model-semantic tests executed valid local-model JSON through strict Pydantic and
semantic policy validation, plus invalid JSON, ungrounded output, timeout,
unavailable provider, paid-provider exclusion and deterministic fallback.
Confidence, temporal scope, ambiguity and `MODEL_HYPOTHESIS` provenance were
verified.

The real Ollama gate (`test_v901_real_model.py`) was **NOT CONNECTED / NOT
VERIFIED** because no Ollama server was reachable. It skipped loudly; no
deterministic test is reported as real-model evidence.

## Commands

```bash
cd backend
PYTHONPATH=. pytest -q tests/test_v9_*.py
# 30 passed

PYTHONPATH=. pytest -q
# 928 passed, 22 skipped

cd ../frontend
npm ci
npm run typecheck
npm run lint
npm run build
# all exit 0; build generated 8/8 pages
```

The 22 backend skips are environment-dependent real-model/Ollama tests. No
Ollama service was connected, so V9 real-model behavior is **NOT CONNECTED /
NOT VERIFIED** in this environment. Deterministic tests are not used as a
substitute for that gate.

## Covered V9 behavior

- all 26 initial cognitive types and all six provenance categories;
- factual, uncertain, hypothetical, preference, goal, intent, commitment,
  question, correction, contradiction, prediction and temporal compilation;
- invalid model semantic output fails closed;
- ambiguous corrections and ordinary questions are not silently persisted;
- object create/update/retire/supersede and semantic relationships;
- immutable state snapshots, reconstruction and deterministic diff;
- canonical EventBus events (no shadow stream);
- `/api/chat` Meaning Kernel and Cognitive Surface integration;
- backend-sourced activity and uncertainty;
- voice/text semantic pipeline parity and voice lifecycle events;
- direct-id, cross-user write and caller-supplied namespace isolation;
- V9 portability export, validation, dry run and restore with provenance.

## Browser status

`python tests/v9_browser_qa.py` passed **16/16** at desktop 1440×900 and mobile
390×844: workspace load, real conversation response, backend surface update,
persisted BELIEF display, explicit microphone capability/fallback, Observatory
semantic state, zero console errors and zero failed HTTP requests.

The V8.5.1 browser regression also passed **12/12**. Browser speech support is
capability-detected at runtime; unsupported browsers visibly fall back to text.
