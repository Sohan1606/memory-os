"""V9.0.1 live surface lifecycle and local-model semantic hardening."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.cognition.events import EventBus
from app.cognition.focus import FocusTracker
from app.cognition.meaning import MeaningCompiler, MeaningKernel
from app.cognition.personal_state import PersonalStateService
from app.cognition.surface import SurfaceLifecycle
from app.config import Settings
from app.main import app, rt
from app.persistence.db import Database
from app.runtime import Runtime


class _Report:
    def __init__(self, supported=True): self.supported = supported
    def supports(self, _cap): return self.supported
    def reason(self, _cap): return "structured output unsupported"


class _Router:
    def __init__(self, supported=True): self.supported = supported
    def report(self): return _Report(self.supported)


class _Model:
    def __init__(self, output=None, error=None):
        self.output, self.error = output, error
    def invoke(self, _messages):
        if self.error: raise self.error
        return AIMessage(content=self.output)


class _Provider:
    def __init__(self, output=None, error=None, *, available=True, name="ollama"):
        self.model = _Model(output, error)
        self.available, self.name = available, name
    def status(self):
        return SimpleNamespace(name=self.name, available=self.available,
                               model="llama3.2:3b", detail="test provider")
    def chat_model(self): return self.model


def _proposal(text: str, *, kind="HYPOTHESIS", modality="POSSIBLE",
              confidence=.64, ambiguous=False):
    return json.dumps({
        "schema_version": "9.0", "input_text": text, "source": "local_model",
        "candidates": [{"type": kind, "content": text, "modality": modality,
            "confidence": confidence, "provenance": "MODEL_HYPOTHESIS",
            "source": "local_model", "temporal_scope": {
                "expression": "next year" if "next year" in text else None,
                "start": None, "end": None,
                "kind": "FUTURE" if "next year" in text else "UNSPECIFIED"},
            "status": "PROPOSED", "evidence": [], "relationships": [],
            "material": False}],
        "ambiguous": ambiguous,
        "ambiguity_reason": "Reference requires focus." if ambiguous else None,
        "compiler": "model-assisted"})


def _kernel(tmp_path: Path, provider):
    db = Database(tmp_path / f"{time.time_ns()}.db")
    bus = EventBus(db)
    state = PersonalStateService(db, bus)
    kernel = MeaningKernel(db, bus, state, FocusTracker(db, bus),
                           provider=provider, capability_router=_Router())
    return db, state, kernel


def test_valid_local_model_json_passes_schema_and_policy(tmp_path):
    text = "I might leave this job next year."
    db, _, kernel = _kernel(tmp_path, _Provider(_proposal(text)))
    try:
        result = kernel.process("u", text, persist=False)
        candidate = result["semantic"]["candidates"][0]
        assert result["semantic_mode"] == "MODEL"
        assert candidate["type"] == "HYPOTHESIS"
        assert candidate["provenance"] == "MODEL_HYPOTHESIS"
        assert candidate["temporal_scope"]["kind"] == "FUTURE"
        assert candidate["confidence"] == .64
    finally: db.close()


@pytest.mark.parametrize("output,error", [
    ("not json", None),
    (_proposal("wrong", kind="FACT"), None),
    (None, TimeoutError("model timed out")),
])
def test_invalid_timeout_or_ungrounded_model_falls_back(tmp_path, output, error):
    text = "I might leave this job next year."
    db, _, kernel = _kernel(tmp_path, _Provider(output, error))
    try:
        result = kernel.process("u", text, persist=False)
        assert result["semantic_mode"] == "DETERMINISTIC"
        assert result["semantic"]["candidates"][0]["provenance"] == "USER_STATED"
        assert result["model_fallback_reason"]
        row = db.query_one("SELECT error FROM meaning_compilations WHERE id=?",
                           (result["compilation_id"],))
        assert row["error"]
    finally: db.close()


def test_unavailable_or_paid_provider_never_runs_semantic_model(tmp_path):
    for provider in (_Provider(available=False), _Provider(name="openai")):
        db, _, kernel = _kernel(tmp_path, provider)
        try:
            result = kernel.process("u", "I prefer learning.", persist=False)
            assert result["semantic_mode"] == "DETERMINISTIC"
            assert result["model_fallback_reason"].startswith("NOT_")
        finally: db.close()


@pytest.mark.parametrize(("text", "kind", "modality", "ambiguous", "material"), [
    ("I might leave this job next year.", "HYPOTHESIS", "POSSIBLE", False, True),
    ("I don't think this plan makes sense anymore.", "BELIEF", "TENTATIVE", True, True),
    ("I'm seriously considering switching universities.", "INTENT", "TENTATIVE", False, True),
    ("I'd rather optimize for learning than salary.", "PREFERENCE", "ASSERTED", False, True),
    ("I haven't decided yet.", "CLAIM", "TENTATIVE", False, False),
    ("Keep Tuesday, not Monday.", "CORRECTION", "ASSERTED", False, True),
    ("What do you already know about this?", "QUESTION", "QUESTIONED", True, False),
    ("I'm not sure whether that is actually true.", "BELIEF", "TENTATIVE", True, True),
])
def test_nuanced_deterministic_semantics(text, kind, modality, ambiguous, material):
    result = MeaningCompiler().compile(text)
    candidate = result.candidates[0]
    assert candidate.type.value == kind
    assert candidate.modality.value == modality
    assert result.ambiguous is ambiguous
    assert candidate.material is material


def test_lifecycle_is_persisted_and_never_invents_stages(tmp_path):
    db = Database(tmp_path / "surface.db")
    try:
        lifecycle = SurfaceLifecycle(EventBus(db))
        initial = lifecycle.start_turn("u", "thread-a", "turn_live_a")
        assert initial["activities"][0]["stage"] == "LISTENING"
        lifecycle.transition("u", "thread-a", "turn_live_a", "UNDERSTANDING", "ACTIVE")
        active = lifecycle.snapshot("u", "turn_live_a")
        assert active["cognitive_stage"] == "UNDERSTANDING"
        assert active["activities"][-1]["status"] == "ACTIVE"
        assert "CHECKING_MEMORY" not in {a["stage"] for a in active["activities"]}
        lifecycle.transition("u", "thread-a", "turn_live_a", "UNDERSTANDING", "COMPLETED")
        lifecycle.transition("u", "thread-a", "turn_live_a", "CHECKING_MEMORY", "DEGRADED")
        done = lifecycle.snapshot("u", "turn_live_a")
        assert done["activities"][-1]["status"] == "DEGRADED"
        assert [h["status"] for h in done["history"] if h["stage"] == "UNDERSTANDING"] == [
            "ACTIVE", "COMPLETED"]
        lifecycle.start_turn("u", "thread-b", "turn_live_b")
        lifecycle.transition("u", "thread-b", "turn_live_b", "UNDERSTANDING", "ACTIVE")
        isolated = lifecycle.snapshot("u", "turn_live_b")
        assert isolated["conversation"]["thread_id"] == "thread-b"
        assert "CHECKING_MEMORY" not in {a["stage"] for a in isolated["activities"]}
        assert lifecycle.snapshot("other-user", "turn_live_a") is None
    finally: db.close()


@pytest.fixture(scope="module")
def live_client():
    root = Path(__file__).parent / ".v901-runtime"
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    runtime = Runtime(Settings(data_dir=root, sqlite_path=root / "m.db",
        checkpoint_path=root / "c.db", chroma_path=root / "chroma",
        disable_embeddings=True))
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as client:
        yield client, runtime
    app.dependency_overrides.clear(); runtime.close(); shutil.rmtree(root, ignore_errors=True)


def test_activity_is_observable_before_chat_response_completes(live_client, monkeypatch):
    client, runtime = live_client
    started = client.post("/api/v9/surface/turns", json={"thread_id": "live-thread"}).json()
    correlation = started["conversation"]["correlation_id"]
    original = runtime.agent.run
    entered = threading.Event()
    def slow_run(*args, **kwargs):
        entered.set(); time.sleep(.25); return original(*args, **kwargs)
    monkeypatch.setattr(runtime.agent, "run", slow_run)
    holder = {}
    thread = threading.Thread(target=lambda: holder.setdefault("response", client.post(
        "/api/chat", json={"message": "What am I missing?", "thread_id": "live-thread",
                            "correlation_id": correlation})))
    thread.start()
    assert entered.wait(10)
    live = client.get(f"/api/v9/surface/turns/{correlation}").json()
    assert live["status"] == "ACTIVE"
    assert live["cognitive_stage"] == "FORMING_RESPONSE"
    assert live["activities"][-1]["status"] == "ACTIVE"
    thread.join(30)
    assert holder["response"].status_code == 200
    final = holder["response"].json()["surface"]
    assert final["cognitive_stage"] == "WAITING_FOR_USER"
    assert final["activities"][-1]["status"] == "ACTIVE"
    assert "VERIFYING_EXTERNAL_INFORMATION" not in {a["stage"] for a in final["activities"]}
