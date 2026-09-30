"""ZORQ Phase 3F (3F-min) backend voice contract tests.

Covers, against the real HTTP surface and modules:

  * voice/status truthfulness (no Whisper => mode "browser", never faked);
  * /api/voice/transcribe fail-closed behavior: 503 unconfigured, 413 over
    the size cap (spec §8 TR6, acceptance tests 9-10);
  * /api/voice/transcribe sits behind the same authentication boundary as
    every other route in secured mode (spec GAP-3 closure);
  * Transcriber temporary-file cleanup, success AND failure paths
    (spec §15 / GAP-4 probe);
  * text/voice convergence at /api/chat: identical message, identical
    pipeline; interaction_mode may only add truthful voice session events
    (spec §6 C1/C2, acceptance tests 21-22);
  * voice input receives NO trust bonus in source reputation — the voice
    weight equals the conversation weight (spec §14 M1);
  * cognition event vocabulary contains the voice session events actually
    emitted, and nothing speculative.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, rt
from app.runtime import Runtime
from app.voice.transcription import Transcriber

from conftest_v85 import make_secure_runtime, register_and_login, secure_client, teardown


@pytest.fixture(scope="module")
def client():
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-3f-"))
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma")
    runtime = Runtime(cfg)
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c, runtime
    app.dependency_overrides.clear()
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ status truth
def test_voice_status_is_truthful_without_whisper(client):
    c, runtime = client
    body = c.get("/api/voice/status").json()
    # In this environment no Whisper model is configured: the API must say
    # "browser", never simulate a server capability.
    assert body["mode"] == runtime.transcriber.mode
    if not runtime.transcriber.available:
        assert body["mode"] == "browser"
    assert body["browser_fallback"] is True
    assert body["detail"]  # a reason is always given


# --------------------------------------------------- transcribe fail-closed
def test_transcribe_unconfigured_returns_503_not_fake_text(client):
    c, runtime = client
    if runtime.transcriber.available:  # pragma: no cover - env-dependent
        pytest.skip("local Whisper configured in this environment")
    r = c.post("/api/voice/transcribe",
               files={"file": ("a.webm", b"\x1a\x45\xdf\xa3fake", "audio/webm")})
    assert r.status_code == 503
    assert "browser" in r.json()["detail"].lower()


def test_transcribe_rejects_oversized_audio(client):
    c, runtime = client
    if runtime.transcriber.available:  # pragma: no cover - env-dependent
        pytest.skip("local Whisper configured; 503 path not applicable")
    # The size cap must be enforced regardless of transcriber availability
    # ordering — the endpoint checks availability first (fail-closed), so an
    # unconfigured runtime 503s before reading. Verify the documented cap by
    # exercising the available=True branch with a stub.
    runtime.transcriber._model = object()  # makes .available True
    try:
        big = b"0" * (25 * 1024 * 1024 + 1)
        r = c.post("/api/voice/transcribe",
                   files={"file": ("big.webm", big, "audio/webm")})
        assert r.status_code == 413
    finally:
        runtime.transcriber._model = None


# ------------------------------------------------------------- auth boundary
def test_transcribe_requires_authentication_in_secured_mode():
    """GAP-3 closure: the voice route sits behind the same middleware auth
    boundary as every other API route — no anonymous audio uploads."""
    # conftest_v85.teardown() clears ALL dependency overrides; preserve the
    # module fixture's override so later tests keep their isolated runtime.
    previous_overrides = dict(app.dependency_overrides)
    runtime, tmp = make_secure_runtime()
    client = secure_client(runtime)
    try:
        r = client.post("/api/voice/transcribe",
                        files={"file": ("a.webm", b"xx", "audio/webm")})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"

        r = client.get("/api/voice/status")
        assert r.status_code == 401

        # Authenticated: reachable, and honestly 503 without Whisper.
        _user, token, csrf = register_and_login(client, "voice3f@example.com")
        r = client.post("/api/voice/transcribe",
                        headers={"Authorization": f"Bearer {token}",
                                 "X-CSRF-Token": csrf},
                        files={"file": ("a.webm", b"xx", "audio/webm")})
        assert r.status_code == 503
    finally:
        teardown(runtime, tmp)
        app.dependency_overrides.update(previous_overrides)


# ------------------------------------------------------- temp-file lifecycle
class _FakeSegment:
    text = "hello from fake whisper"


class _FakeModel:
    """Stands in for faster_whisper.WhisperModel; records the temp path."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.seen_paths: list[Path] = []

    def transcribe(self, path: str, beam_size: int = 1):
        self.seen_paths.append(Path(path))
        if self.fail:
            raise RuntimeError("decode failed")
        return [_FakeSegment()], None


def test_transcriber_deletes_temp_file_on_success():
    t = Transcriber(model_name=None)
    fake = _FakeModel()
    t._model = fake
    out = t.transcribe(b"fake-audio-bytes")
    assert out == "hello from fake whisper"
    assert len(fake.seen_paths) == 1
    assert not fake.seen_paths[0].exists(), "temp audio must be deleted"


def test_transcriber_deletes_temp_file_on_failure():
    t = Transcriber(model_name=None)
    fake = _FakeModel(fail=True)
    t._model = fake
    with pytest.raises(RuntimeError):
        t.transcribe(b"fake-audio-bytes")
    assert len(fake.seen_paths) == 1
    assert not fake.seen_paths[0].exists(), "temp audio must be deleted even on failure"


def test_transcriber_unavailable_refuses_instead_of_pretending():
    t = Transcriber(model_name=None)
    assert not t.available
    assert t.mode == "browser"
    with pytest.raises(RuntimeError):
        t.transcribe(b"anything")


# ------------------------------------------------------------- convergence
def test_voice_and_text_chat_share_the_exact_pipeline(client):
    """Acceptance tests 21-22: identical message via voice mode and text mode
    traverses the same backend path. interaction_mode may only add the two
    truthful voice session events — provider, recall, and answer semantics
    are modality-blind."""
    c, runtime = client
    msg = "I prefer concise technical explanations."

    r_text = c.post("/api/chat", json={
        "message": msg, "thread_id": "3f-conv-text", "interaction_mode": "text"})
    r_voice = c.post("/api/chat", json={
        "message": msg, "thread_id": "3f-conv-voice", "interaction_mode": "voice"})
    assert r_text.status_code == 200 and r_voice.status_code == 200
    t_body, v_body = r_text.json(), r_voice.json()

    # Same provider and provider mode — no voice-specific provider routing.
    assert t_body["provider"] == v_body["provider"]
    # Both produced a real answer through the same agent path.
    assert t_body["answer"] and v_body["answer"]
    # Recall pipeline is modality-blind: same recalled memory ids.
    t_recalled = sorted(m["memory"]["id"] for m in t_body.get("recalled", []))
    v_recalled = sorted(m["memory"]["id"] for m in v_body.get("recalled", []))
    assert t_recalled == v_recalled

    # Voice session events exist ONLY for the voice-mode turn.
    user = runtime.settings.demo_user_id
    voice_events = runtime.cognition.bus.recent(
        user, limit=200,
        types=["voice.session_started", "voice.session_ended"])
    assert any(e.type == "voice.session_started" for e in voice_events)
    assert any(e.type == "voice.session_ended" for e in voice_events)
    for e in voice_events:
        assert e.subject_id == "3f-conv-voice", (
            "voice events must only ever be attached to voice-mode turns")


def test_interaction_mode_is_validated_and_cannot_smuggle_values(client):
    c, _ = client
    r = c.post("/api/chat", json={
        "message": "hello", "thread_id": "3f-mode", "interaction_mode": "root"})
    assert r.status_code == 422


# ------------------------------------------------------------ memory parity
def test_voice_source_gets_no_trust_bonus_over_conversation():
    """Spec §14 M1: modality never changes governance weight. The memory
    arbiter's source-authority prior for 'voice' must equal 'conversation'
    — no bonus, no penalty for having been spoken."""
    from app.cognition.reputation import MemoryArbiter
    assert MemoryArbiter.AUTHORITY["voice"] == MemoryArbiter.AUTHORITY["conversation"]
    # And spoken input never outranks an explicit user statement.
    assert MemoryArbiter.AUTHORITY["voice"] < MemoryArbiter.AUTHORITY["explicit"]


def test_arbitration_weights_voice_equals_conversation():
    from app.cognition import arbitration
    src = Path(arbitration.__file__).read_text(encoding="utf-8")
    # The literal table pins voice to the conversation weight.
    assert '"voice": 0.7' in src and '"conversation": 0.7' in src


# ------------------------------------------------------- event vocabulary
def test_voice_event_vocabulary_is_registered_and_minimal(client):
    from app.cognition.events import EVENT_TYPES, LABELS
    assert "voice.session_started" in EVENT_TYPES
    assert "voice.session_ended" in EVENT_TYPES
    # No speculative voice events that the runtime cannot evidence (3F-min
    # has no server-observable SPEAKING/LISTENING — those live client-side).
    speculative = [t for t in EVENT_TYPES
                   if t.startswith("voice.") and t not in
                   ("voice.session_started", "voice.session_ended")]
    assert speculative == [], f"unevidenced voice events registered: {speculative}"
    for t in ("voice.session_started", "voice.session_ended"):
        assert t in LABELS
