# MEMORY//OS V9 / V9.0.1 / V9.0.2 verification

Verified 2026-09-22 in the release workspace.

## V9.0.2 semantic response hardening

Verification categories are intentionally separate:

1. **Deterministic semantics — PASS.** Nuanced interpretation and every unsafe
   model response fall back through the deterministic compiler.
2. **Simulated model boundary — PASS.** Tests exercise clean JSON, complete JSON
   fences, documented single content blocks, the `langchain-ollama`
   `include_raw` wrapper, strict schema failures and all semantic policy gates.
   These tests do not count as a real-model pass.
3. **Real Ollama — NOT VERIFIED in this release workspace.** Ollama 0.34.2 and
   the official `llama3.2:3b` model were installed and the API/model manifest
   were reachable. The sandbox has 1.9 GiB RAM; the 2.0 GiB model process was
   killed while loading. The real test therefore has no PASS claim here.

```bash
PYTHONPATH=. pytest -q tests/test_v902_semantic_hardening.py \
  tests/test_v901_hardening.py tests/test_v901_real_model.py tests/test_v9_*.py
# 66 passed, 1 skipped

PYTHONPATH=. pytest -q
# 964 passed, 23 skipped

npm ci && npm run typecheck && npm run lint && npm run build
# all exit 0; 0 npm vulnerabilities; 8/8 pages

python tests/v9_browser_qa.py
# 16 passed, 0 failed
python tests/v851_browser_qa.py
# 12 passed, 0 failed
```

The real gate remains:

```bash
cd backend
python -m pytest -m slow tests/test_v901_real_model.py -v
```

It must report `semantic_mode == "MODEL"`, `compiler == "model-assisted"`, a
`MODEL_HYPOTHESIS` candidate with confidence at most `0.85`, no `FACT`, and a
`FUTURE` temporal scope. A skip or deterministic fallback is **NOT VERIFIED**.

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
