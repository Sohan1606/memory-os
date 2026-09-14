"""
Multimodal perception (v8.1).

Every input modality - text, voice transcript, image, document - is normalised
into ONE cognitive perception record that then flows through the same
memory / world / intent pipeline. There are no parallel pipelines.

    TEXT ┐
   VOICE ┤
   IMAGE ├─> Perception ─> cognitive event ─> memory / world / intent
DOCUMENT ┘

Honesty rules:
  * We only claim to have "read" what we actually parsed.
  * Image pixels are NOT interpreted unless a vision model is configured; an
    uploaded image without a vision model is recorded with
    `understanding = "NOT AVAILABLE"` and only its metadata is used.
  * Nothing perceived is stored as long-term memory automatically - candidates
    still pass the existing importance/retention policy.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

MODALITIES = ("text", "voice", "image", "document")

# Conservative caps - this runs on modest hardware.
MAX_TEXT_CHARS = 20_000
MAX_BYTES = 10 * 1024 * 1024

_TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yml",
             ".yaml", ".rst", ".ini", ".toml"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Perception:
    """One normalised observation, whatever modality it arrived in."""

    id: str
    modality: str
    text: str                      # extracted text ("" when none available)
    understanding: str             # FULL | PARTIAL | METADATA ONLY | NOT AVAILABLE
    source: str                    # filename / "microphone" / "keyboard"
    detail: str
    bytes_len: int = 0
    checksum: str | None = None
    created_at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "modality": self.modality, "text": self.text,
                "understanding": self.understanding, "source": self.source,
                "detail": self.detail, "bytes": self.bytes_len,
                "checksum": self.checksum, "created_at": self.created_at}


def _decode_text(data: bytes) -> str | None:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read PNG/GIF dimensions from the header without any image library."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return (int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"))
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return (int.from_bytes(data[6:8], "little"),
                    int.from_bytes(data[8:10], "little"))
    except Exception:  # pragma: no cover - malformed header
        return None
    return None


class PerceptionEngine:
    """Normalises every modality into one cognitive stream."""

    def __init__(self, bus, transcriber=None, provider=None) -> None:
        self.bus = bus
        self.transcriber = transcriber
        self.provider = provider

    # ------------------------------------------------------------ capability
    def capabilities(self) -> dict[str, Any]:
        """Truthful statement of what can actually be perceived right now."""
        voice_active = bool(self.transcriber and self.transcriber.available)
        return {
            "text": {"state": "ACTIVE", "detail": "Typed text is always accepted."},
            "voice": {
                "state": "ACTIVE" if voice_active else "DEGRADED",
                "detail": (self.transcriber.detail if self.transcriber
                           else "No transcriber configured.")},
            "document": {
                "state": "ACTIVE",
                "detail": "Plain-text documents are read directly. "
                          "PDF and Office parsing are NOT CONFIGURED."},
            "image": {
                "state": "METADATA ONLY",
                "detail": "No vision model is configured, so image CONTENT is "
                          "NOT AVAILABLE. Only format and dimensions are read."},
            "video": {"state": "NOT CONFIGURED",
                      "detail": "Video ingestion is not implemented."},
        }

    # --------------------------------------------------------------- ingest
    def ingest_text(self, user_id: str, text: str, *, source: str = "keyboard",
                    modality: str = "text",
                    correlation_id: str | None = None) -> Perception:
        clean = (text or "").strip()[:MAX_TEXT_CHARS]
        p = Perception(
            id=f"pcp_{uuid.uuid4().hex[:10]}", modality=modality, text=clean,
            understanding="FULL" if clean else "NOT AVAILABLE",
            source=source, detail=f"{len(clean)} characters read.",
            bytes_len=len(clean.encode("utf-8")))
        self._emit(user_id, p, correlation_id)
        return p

    def ingest_file(self, user_id: str, filename: str, data: bytes, *,
                    correlation_id: str | None = None) -> Perception:
        """
        Ingest an uploaded file.

        Text-like files are read in full. Images are recorded with metadata only
        and clearly marked NOT AVAILABLE for content, because no vision model is
        configured - we never pretend to have seen an image.
        """
        if len(data) > MAX_BYTES:
            p = Perception(
                id=f"pcp_{uuid.uuid4().hex[:10]}", modality="document", text="",
                understanding="NOT AVAILABLE", source=filename,
                detail=f"File exceeds the {MAX_BYTES // (1024 * 1024)}MB limit.",
                bytes_len=len(data))
            self._emit(user_id, p, correlation_id, rejected=True)
            return p

        checksum = hashlib.sha256(data).hexdigest()[:16]
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

        if ext in _IMAGE_EXT:
            dims = _image_dimensions(data)
            size = f"{dims[0]}×{dims[1]} " if dims else ""
            p = Perception(
                id=f"pcp_{uuid.uuid4().hex[:10]}", modality="image", text="",
                understanding="METADATA ONLY", source=filename,
                detail=(f"{size}{ext.lstrip('.').upper()} image received. "
                        "No vision model is configured, so the image content is "
                        "NOT AVAILABLE to me."),
                bytes_len=len(data), checksum=checksum)
            self._emit(user_id, p, correlation_id)
            return p

        decoded = _decode_text(data) if (ext in _TEXT_EXT or not ext) else None
        if decoded is None:
            p = Perception(
                id=f"pcp_{uuid.uuid4().hex[:10]}", modality="document", text="",
                understanding="NOT AVAILABLE", source=filename,
                detail=(f"'{ext or 'unknown'}' files are NOT CONFIGURED for "
                        "parsing. Only plain-text formats can be read."),
                bytes_len=len(data), checksum=checksum)
            self._emit(user_id, p, correlation_id, rejected=True)
            return p

        text = decoded[:MAX_TEXT_CHARS]
        truncated = len(decoded) > MAX_TEXT_CHARS
        p = Perception(
            id=f"pcp_{uuid.uuid4().hex[:10]}", modality="document", text=text,
            understanding="PARTIAL" if truncated else "FULL", source=filename,
            detail=(f"Read {len(text)} of {len(decoded)} characters."
                    if truncated else f"Read {len(text)} characters."),
            bytes_len=len(data), checksum=checksum)
        self._emit(user_id, p, correlation_id)
        return p

    def ingest_audio(self, user_id: str, data: bytes, *, suffix: str = ".webm",
                     correlation_id: str | None = None) -> Perception:
        """Transcribe audio server-side, or say plainly that we cannot."""
        if not (self.transcriber and self.transcriber.available):
            p = Perception(
                id=f"pcp_{uuid.uuid4().hex[:10]}", modality="voice", text="",
                understanding="NOT AVAILABLE", source="microphone",
                detail="Server-side transcription is NOT CONFIGURED. "
                       "Use browser speech input instead.",
                bytes_len=len(data))
            self._emit(user_id, p, correlation_id, rejected=True)
            return p
        try:
            text = self.transcriber.transcribe(data, suffix=suffix)
        except Exception as exc:
            p = Perception(
                id=f"pcp_{uuid.uuid4().hex[:10]}", modality="voice", text="",
                understanding="NOT AVAILABLE", source="microphone",
                detail=f"Transcription failed: {exc}"[:200], bytes_len=len(data))
            self._emit(user_id, p, correlation_id, rejected=True)
            return p
        p = Perception(
            id=f"pcp_{uuid.uuid4().hex[:10]}", modality="voice", text=text,
            understanding="FULL" if text else "NOT AVAILABLE", source="microphone",
            detail=f"Transcribed {len(text)} characters.", bytes_len=len(data))
        self._emit(user_id, p, correlation_id)
        return p

    # ---------------------------------------------------------------- helper
    def summarise_for_context(self, perceptions: list[Perception],
                              limit: int = 4000) -> str:
        """Build a context block the model can actually use."""
        if not perceptions:
            return ""
        parts: list[str] = []
        for p in perceptions:
            if p.text:
                body = re.sub(r"\n{3,}", "\n\n", p.text)[:limit]
                parts.append(f"[{p.modality}: {p.source}]\n{body}")
            else:
                parts.append(f"[{p.modality}: {p.source}] {p.detail}")
        return "\n\n".join(parts)[:limit]

    def _emit(self, user_id: str, p: Perception, correlation_id: str | None,
              rejected: bool = False) -> None:
        event = "perception.rejected" if rejected else (
            "perception.processed" if p.text else "perception.received")
        self.bus.emit(user_id, event, f"{p.modality}: {p.source}",
                      subject_kind="perception", subject_id=p.id,
                      correlation_id=correlation_id, payload=p.as_dict())
