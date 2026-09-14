"""Optional local Whisper transcription (faster-whisper).

Never claims availability it does not have. When faster-whisper or the model is
absent the API reports voice.mode = "browser" and the frontend uses the browser
SpeechRecognition API instead.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, model_name: str | None) -> None:
        self.model_name = model_name
        self._model = None
        self._error: str | None = None
        if model_name:
            self._load()
        else:
            self._error = "WHISPER_MODEL not set."

    def _load(self) -> None:
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        except Exception as exc:
            self._error = str(exc)
            log.info("faster-whisper unavailable (%s); browser speech fallback active.", exc)

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def mode(self) -> str:
        return "whisper" if self.available else "browser"

    @property
    def detail(self) -> str:
        if self.available:
            return f"Local faster-whisper model '{self.model_name}'."
        return f"Browser SpeechRecognition fallback ({self._error})."

    def transcribe(self, audio_bytes: bytes, suffix: str = ".webm") -> str:
        if not self.available:
            raise RuntimeError("Local Whisper is not configured; use browser speech input.")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(audio_bytes)
            path = Path(fh.name)
        try:
            segments, _ = self._model.transcribe(str(path), beam_size=1)
            return " ".join(seg.text.strip() for seg in segments).strip()
        finally:
            path.unlink(missing_ok=True)
