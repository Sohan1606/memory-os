"""
Content safety and bounded text extraction for Connected Research (V8.4.3).

Everything retrieved from the open web is untrusted input (§ Phase 4 of the
V8.4.3 spec). This module has exactly two jobs:

  1. Turn raw HTML/text/JSON bytes into a bounded, readable text excerpt —
     deterministic parsing, not "understanding". No LLM is used here; nothing
     in this file claims semantic comprehension.
  2. Detect and FLAG language that reads like an attempt to redirect
     MEMORY//OS's own behaviour (prompt injection). Flagging never grants the
     page any authority — it only labels the evidence so a human, and the
     Explanation Engine, can see that an instruction-like pattern was present
     in SOURCE MATERIAL, not in a system or user instruction. The research
     pipeline never executes anything found here as a tool call or as a
     directive to change cognition.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

MAX_EXCERPT_CHARS = 20_000
_SCRIPT_STYLE_TAGS = {"script", "style", "noscript", "template"}

# Patterns that read as an attempt to command an AI system reading this page.
# Detection is intentionally lexical/pattern-based (documented honestly, not
# claimed as semantic understanding) and is deliberately over-inclusive: a
# false positive here only adds a label to evidence, it never blocks anything
# outright, so erring toward flagging is the safe direction.
_INJECTION_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.I) for p in (
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"disregard (all |any )?(previous|prior|above) (instructions|rules)",
    r"reveal (your |the )?system prompt",
    r"you are now (in )?(dan|jailbreak|developer mode)",
    r"act as (if you (are|were)|an?) (unrestricted|unfiltered|jailbroken)",
    r"\bnew instructions?:",
    r"\bsystem\s*:\s*",
    r"call (this|the) tool",
    r"execute (this|the following) (command|code|tool)",
    r"delete (all )?(your |the )?memory",
    r"update your beliefs? (to|that)",
    r"trust me,? (and|then)? (ignore|override|forget)",
    r"from now on,? you (must|will|should) (ignore|forget|disregard)",
    r"do not (tell|inform) the user",
    r"override your (guidelines|instructions|programming)",
))


@dataclass
class ExtractedContent:
    """Bounded, deterministic text extraction result for one fetched page."""

    text: str
    truncated: bool
    original_chars: int
    injection_flags: list[str] = field(default_factory=list)
    title: str | None = None

    @property
    def has_injection_attempt(self) -> bool:
        return bool(self.injection_flags)


class _TextExtractor(HTMLParser):
    """Minimal, dependency-free HTML-to-text extractor.

    Deliberately simple: strips tags, drops script/style content, collapses
    whitespace. This is NOT a rendering engine and NOT a semantic parser —
    it is documented as deterministic text extraction, matching the honesty
    requirement that V8.4.3 not claim comprehension it does not have.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._title_chunks: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SCRIPT_STYLE_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SCRIPT_STYLE_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_chunks.append(data)
            return
        self._chunks.append(data)

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    @property
    def title(self) -> str | None:
        t = "".join(self._title_chunks).strip()
        return t or None


def extract_text(raw: str, content_type: str | None) -> ExtractedContent:
    """
    Deterministically extract bounded plain text from fetched content.

    JSON and plain text pass through with only whitespace normalisation and
    truncation. HTML is stripped of tags/scripts/styles. Nothing here infers
    meaning; it only reduces bytes to a readable excerpt.
    """
    original_chars = len(raw)
    content_type = (content_type or "").lower()

    title = None
    if "html" in content_type or (raw.lstrip()[:15].lower().startswith("<!doctype html")
                                  or raw.lstrip()[:5].lower() == "<html"):
        parser = _TextExtractor()
        try:
            parser.feed(raw)
        except Exception:
            pass
        text = parser.text
        title = parser.title
        text = html.unescape(text)
    else:
        text = raw

    # Collapse excessive whitespace deterministically.
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = text.strip()

    truncated = len(text) > MAX_EXCERPT_CHARS
    if truncated:
        text = text[:MAX_EXCERPT_CHARS]

    flags = scan_for_injection(text)

    return ExtractedContent(text=text, truncated=truncated,
                            original_chars=original_chars,
                            injection_flags=flags, title=title)


def scan_for_injection(text: str) -> list[str]:
    """
    Return the list of matched injection-style patterns found in `text`.

    This labels evidence; it never changes control flow. Retrieved content is
    ALWAYS treated as page content to be recorded and cited, never as an
    instruction to MEMORY//OS, regardless of whether this scan finds anything.
    """
    found: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(text)
        if m:
            found.append(m.group(0)[:80])
    return found


def quote_excerpt(text: str, max_len: int = 400) -> str:
    """Bounded quote/excerpt for an Evidence record — never the full page."""
    snippet = " ".join(text.split())
    if len(snippet) <= max_len:
        return snippet
    return snippet[:max_len].rsplit(" ", 1)[0] + "…"
