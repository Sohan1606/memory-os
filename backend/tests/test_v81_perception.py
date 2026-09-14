"""Multimodal perception: one normalised stream, honest capability reporting."""

from app.cognition.perception import PerceptionEngine, MODALITIES

PNG_HEADER = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000001200000009008060000")


def engine(runtime):
    return runtime.cognition.perception


def test_capabilities_never_overclaim(runtime):
    caps = engine(runtime).capabilities()
    assert caps["text"]["state"] == "ACTIVE"
    # No vision model is bundled, so image content must not be claimed.
    assert caps["image"]["state"] == "METADATA ONLY"
    assert caps["video"]["state"] == "NOT CONFIGURED"


def test_text_ingestion_is_fully_understood(runtime):
    p = engine(runtime).ingest_text("u-percept", "I am planning a launch")
    assert p.modality == "text"
    assert p.understanding == "FULL"
    assert "planning a launch" in p.text


def test_plaintext_document_is_read(runtime):
    p = engine(runtime).ingest_file("u-percept", "notes.md",
                                    b"# Plan\nShip billing by Friday.")
    assert p.understanding == "FULL"
    assert "Ship billing" in p.text
    assert p.checksum


def test_image_is_metadata_only_and_reads_dimensions(runtime):
    p = engine(runtime).ingest_file("u-percept", "shot.png", PNG_HEADER)
    assert p.modality == "image"
    assert p.understanding == "METADATA ONLY"
    assert p.text == ""          # never fabricate image content
    assert "288" in p.detail     # real width parsed from the PNG header


def test_binary_document_is_rejected_honestly(runtime):
    p = engine(runtime).ingest_file("u-percept", "report.pdf", b"%PDF-1.4 binary")
    assert p.understanding == "NOT AVAILABLE"
    assert "NOT CONFIGURED" in p.detail


def test_oversize_file_is_refused(runtime):
    p = engine(runtime).ingest_file("u-percept", "big.txt", b"x" * (11 * 1024 * 1024))
    assert p.understanding == "NOT AVAILABLE"
    assert "limit" in p.detail.lower()


def test_audio_without_transcriber_is_not_available(runtime):
    p = engine(runtime).ingest_audio("u-percept", b"fake-audio-bytes")
    if not (runtime.transcriber and runtime.transcriber.available):
        assert p.understanding == "NOT AVAILABLE"
        assert p.text == ""


def test_every_modality_emits_a_real_event(runtime):
    user = "u-percept-events"
    eng = engine(runtime)
    eng.ingest_text(user, "hello there")
    eng.ingest_file(user, "a.txt", b"content here")
    eng.ingest_file(user, "b.png", PNG_HEADER)
    types = {e.type for e in runtime.cognition.bus.recent(user, 20)}
    assert types <= {"perception.received", "perception.processed",
                     "perception.rejected"}
    assert types


def test_context_summary_marks_unreadable_sources(runtime):
    eng = engine(runtime)
    readable = eng.ingest_file("u-percept", "a.txt", b"deploy on Friday")
    image = eng.ingest_file("u-percept", "b.png", PNG_HEADER)
    summary = eng.summarise_for_context([readable, image])
    assert "deploy on Friday" in summary
    assert "NOT AVAILABLE" in summary


def test_modalities_vocabulary_is_closed():
    assert MODALITIES == ("text", "voice", "image", "document")
