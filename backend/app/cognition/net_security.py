"""
SSRF-safe outbound HTTP(S) fetcher for Connected Research (V8.4.3).

MEMORY//OS previously had a real HTTP client for exactly zero external
connections — this build ships the first one. Because a research subsystem
lets the SYSTEM initiate outbound requests on the user's behalf, it is the
single highest-risk surface added in this milestone. Every design choice here
is defensive-by-default and fails closed.

Threat model covered (§ Phase 2/3 of the V8.4.3 spec):
  * unsafe schemes (file:, ftp:, javascript:, data:, blob:, chrome:, ...)
  * userinfo tricks (http://user:pass@host/)
  * alternate IP literal encodings (decimal / hex / octal / partial dotted)
  * loopback, private, link-local, multicast, reserved, unspecified addresses
  * IPv4-mapped IPv6 (::ffff:127.0.0.1) and other embedded-v4 forms
  * carrier-grade NAT (100.64.0.0/10)
  * cloud metadata endpoints (169.254.169.254 is already link-local; also
    blocks the well-known metadata hostnames as an explicit allowlist miss)
  * DNS rebinding: hostnames are resolved and validated, then the connection
    is PINNED to the validated IP — a hostname that resolves to a public IP
    at validation time and a private IP a second later cannot be exploited,
    because the actual socket connects to the address that was checked, not
    to whatever the resolver returns next.
  * redirects to unsafe targets: every redirect is independently validated
    and re-pinned before being followed; nothing is trusted merely because
    the original URL looked public. Cross-scheme redirects that leave
    http/https are refused.
  * unbounded resource consumption: connect/read/overall timeouts, a hard
    cap on redirects and on response bytes.

What this module deliberately does NOT attempt: full DNSSEC validation, IPv6
scoped-zone parsing beyond stripping the zone id, or defending against a
malicious *authorized* upstream server once connected to a validated public
IP (that is a content-safety concern, handled separately - see
`research.py`'s treatment of retrieved content as untrusted evidence, never
instructions).
"""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# --------------------------------------------------------------- policy
ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
DEFAULT_PORT = {"http": 80, "https": 443}

CONNECT_TIMEOUT_S = 6.0
READ_TIMEOUT_S = 10.0
OVERALL_TIMEOUT_S = 20.0
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2_000_000          # 2 MB hard cap on the raw body
MAX_EXTRACTED_CHARS = 20_000            # bound on text handed to extraction

ALLOWED_CONTENT_TYPES = (
    "text/html", "text/plain", "application/json", "application/xml",
    "text/xml", "application/xhtml+xml", "application/rss+xml",
    "application/atom+xml",
)

# Hostnames that are safe-looking but are known metadata/service endpoints.
# Their IPs are already link-local/reserved and would be blocked anyway; this
# is an explicit, honestly-documented belt-and-braces list, not the primary
# defense.
BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal", "metadata.goog",
})

CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")


class SSRFBlocked(Exception):
    """Raised whenever a URL or a resolved address fails the safety policy."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason  # short machine-stable code
        self.detail = detail  # human-readable explanation


class FetchError(Exception):
    """Raised for genuine network/protocol failures (never fabricated)."""

    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


@dataclass
class FetchResult:
    """Everything about one real, or genuinely failed, HTTP(S) retrieval."""

    ok: bool
    requested_url: str
    final_url: str | None = None
    status: str = "FETCH_FAILED"          # COMPLETED | FETCH_FAILED | BLOCKED | TIMEOUT
    http_status: int | None = None
    content_type: str | None = None
    body_text: str | None = None
    bytes_read: int = 0
    redirect_chain: list[str] = field(default_factory=list)
    redirect_count: int = 0
    latency_ms: int = 0
    error_code: str | None = None
    error_detail: str | None = None
    content_hash: str | None = None


def _strip_zone(host: str) -> str:
    return host.split("%", 1)[0]


def _looks_like_alt_ip_encoding(host: str) -> bool:
    """
    Detects decimal / hex / octal / partial-dotted IPv4 literal tricks, e.g.
    ``2130706433``, ``0x7f000001``, ``0177.0.0.1``, ``127.1``. These are all
    rejected outright rather than decoded-then-allowed: a legitimate research
    source is referenced by domain name or standard dotted-decimal/bracketed
    IPv6, never by one of these encodings.
    """
    if re.fullmatch(r"\d+", host):
        return True
    if re.fullmatch(r"0x[0-9a-fA-F]+", host, re.I):
        return True
    parts = host.split(".")
    if 1 <= len(parts) <= 4:
        for p in parts:
            if not p:
                continue
            if re.fullmatch(r"0x[0-9a-fA-F]+", p, re.I):
                return True
            if re.fullmatch(r"0[0-7]+", p):
                return True
        # Standard dotted-decimal IPv4 always has exactly 4 parts; 2-3 parts
        # of plain digits ("127.1", "10.1") are the "partial" shorthand form.
        if len(parts) in (2, 3) and all(re.fullmatch(r"\d+", p) for p in parts):
            return True
    return False


def classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a block reason string, or None if the address is safe to reach."""
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            inner = classify_ip(mapped)
            if inner:
                return f"IPv4-mapped IPv6 embeds a blocked address ({inner})"
        sixfour = ip.sixtofour
        if sixfour is not None:
            inner = classify_ip(sixfour)
            if inner:
                return f"6to4 IPv6 embeds a blocked address ({inner})"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address (includes cloud metadata range)"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_reserved:
        return "reserved address"
    if ip.is_unspecified:
        return "unspecified address (0.0.0.0 / ::)"
    if ip.is_private:
        return "private address space"
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_V4:
        return "carrier-grade NAT range (100.64.0.0/10)"
    if not ip.is_global:
        return "non-global address"
    return None


def validate_url_syntax(url: str) -> tuple[str, str, int, str]:
    """
    Structural validation only — no network access yet. Returns
    (scheme, hostname, port, path_and_query). Raises SSRFBlocked.
    """
    if not url or not isinstance(url, str):
        raise SSRFBlocked("INVALID_URL", "Empty or non-string URL.")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise SSRFBlocked("INVALID_URL", f"URL could not be parsed: {exc}") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFBlocked(
            "UNSAFE_SCHEME",
            f"Scheme '{scheme or '(none)'}' is not allowed; only http/https "
            "may be fetched.")

    if parts.username is not None or parts.password is not None:
        raise SSRFBlocked(
            "USERINFO_REJECTED",
            "URLs containing embedded credentials (userinfo) are rejected.")

    host = parts.hostname
    if not host:
        raise SSRFBlocked("INVALID_URL", "URL has no hostname.")
    host = _strip_zone(host)
    if re.search(r"\s", host) or not re.fullmatch(
            r"[A-Za-z0-9.\-:\[\]%_]+", host):
        raise SSRFBlocked("INVALID_URL",
                          f"Hostname '{host}' contains invalid characters.")

    if host.lower() in BLOCKED_HOSTNAMES:
        raise SSRFBlocked("BLOCKED_HOSTNAME",
                          f"'{host}' is a known metadata/service hostname.")

    if _looks_like_alt_ip_encoding(host):
        raise SSRFBlocked(
            "ALT_IP_ENCODING",
            f"Hostname '{host}' looks like an alternate IP-literal encoding "
            "(decimal/hex/octal/partial); rejected outright.")

    # If it IS a literal IP (standard dotted-decimal or bracketed IPv6),
    # validate it immediately — no DNS step needed.
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        reason = classify_ip(literal_ip)
        if reason:
            raise SSRFBlocked("PRIVATE_ADDRESS",
                              f"'{host}' resolves to a blocked address: {reason}.")

    port = parts.port or DEFAULT_PORT[scheme]
    if port not in ALLOWED_PORTS:
        raise SSRFBlocked("PORT_NOT_ALLOWED",
                          f"Port {port} is not in the allowed set {sorted(ALLOWED_PORTS)}.")

    path_qs = parts.path or "/"
    if parts.query:
        path_qs += f"?{parts.query}"
    return scheme, host, port, path_qs


def resolve_and_pin(host: str, port: int) -> str:
    """
    Resolve `host` and validate EVERY returned address. Returns one validated
    IP to pin the connection to. Raises SSRFBlocked if any resolved address
    (or the whole resolution) is unsafe — a hostname with even one bad
    answer is treated as unsafe, since an attacker controlling DNS can return
    multiple records and only needs one accepted.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError("DNS_RESOLUTION_FAILED",
                         f"DNS resolution failed for '{host}': {exc}") from exc
    if not infos:
        raise FetchError("DNS_RESOLUTION_FAILED",
                         f"DNS resolution returned no addresses for '{host}'.")

    resolved: list[str] = []
    for family, _stype, _proto, _canon, sockaddr in infos:
        raw_ip = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(_strip_zone(raw_ip))
        except ValueError:
            raise SSRFBlocked("DNS_INVALID_ADDRESS",
                              f"DNS returned an unparsable address '{raw_ip}'.")
        reason = classify_ip(ip_obj)
        if reason:
            raise SSRFBlocked(
                "DNS_REBINDING_BLOCKED",
                f"'{host}' resolved to {raw_ip}, which is blocked: {reason}. "
                "The whole hostname is refused because DNS answers are not "
                "trusted individually.")
        resolved.append(raw_ip)

    # Pin to the first validated answer; connect-time uses exactly this
    # address so a subsequent re-resolution cannot rebind mid-request.
    return resolved[0]


class _PinnedHTTPConnection(HTTPConnection):
    """HTTP connection pinned to a pre-validated IP; Host header stays real."""

    def __init__(self, pinned_ip: str, sni_host: str, port: int, timeout: float):
        super().__init__(sni_host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised via fetch()
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), timeout=self.timeout)


class _PinnedHTTPSConnection(HTTPSConnection):
    """HTTPS connection pinned to a pre-validated IP, with correct SNI/host."""

    def __init__(self, pinned_ip: str, sni_host: str, port: int, timeout: float,
                context: ssl.SSLContext):
        super().__init__(sni_host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised via fetch()
        sock = socket.create_connection(
            (self._pinned_ip, self.port), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _one_request(scheme: str, host: str, port: int, path_qs: str,
                 pinned_ip: str, method: str = "GET",
                 deadline: float | None = None) -> tuple[int, dict[str, str], bytes, str]:
    """Perform exactly one HTTP request against the pinned IP. No redirects."""
    remaining = OVERALL_TIMEOUT_S
    if deadline is not None:
        remaining = max(0.5, deadline - time.monotonic())
    timeout = min(CONNECT_TIMEOUT_S, remaining)

    if scheme == "https":
        ctx = ssl.create_default_context()
        conn: HTTPConnection = _PinnedHTTPSConnection(pinned_ip, host, port, timeout, ctx)
    else:
        conn = _PinnedHTTPConnection(pinned_ip, host, port, timeout)

    try:
        conn.connect()
        conn.sock.settimeout(min(READ_TIMEOUT_S, remaining))
        headers = {
            "Host": host, "User-Agent": "MemoryOS-Research/8.4.3 (+evidence-fetch)",
            "Accept": "text/html,application/xhtml+xml,application/xml,"
                      "application/json;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",  # no compression -> simpler, bounded reads
            "Connection": "close",
        }
        conn.putrequest(method, path_qs, skip_host=True, skip_accept_encoding=True)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders()

        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        body = bytearray()
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise FetchError("TIMEOUT", "Overall research fetch timeout exceeded.")
            chunk = resp.read(65536)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise FetchError(
                    "RESPONSE_TOO_LARGE",
                    f"Response exceeded the {MAX_RESPONSE_BYTES} byte cap; aborted.")
        return resp.status, resp_headers, bytes(body), (resp.reason or "")
    except socket.timeout as exc:
        raise FetchError("TIMEOUT", f"Connection or read timed out: {exc}") from exc
    except (HTTPException, OSError) as exc:
        raise FetchError("CONNECTION_FAILED", f"Connection failed: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - best effort
            pass


def fetch(url: str, *, method: str = "GET") -> FetchResult:
    """
    Fetch one URL under the full SSRF/resource-safety policy, following
    redirects only when each hop independently validates. Never raises for
    ordinary network failure — always returns a truthful `FetchResult`.
    """
    started = time.monotonic()
    deadline = started + OVERALL_TIMEOUT_S
    chain: list[str] = []
    current = url

    for hop in range(MAX_REDIRECTS + 1):
        try:
            scheme, host, port, path_qs = validate_url_syntax(current)
        except SSRFBlocked as exc:
            return FetchResult(
                ok=False, requested_url=url, final_url=current, status="BLOCKED",
                redirect_chain=chain, redirect_count=len(chain),
                error_code=exc.reason, error_detail=exc.detail,
                latency_ms=int((time.monotonic() - started) * 1000))

        try:
            pinned_ip = resolve_and_pin(host, port)
        except SSRFBlocked as exc:
            return FetchResult(
                ok=False, requested_url=url, final_url=current, status="BLOCKED",
                redirect_chain=chain, redirect_count=len(chain),
                error_code=exc.reason, error_detail=exc.detail,
                latency_ms=int((time.monotonic() - started) * 1000))
        except FetchError as exc:
            return FetchResult(
                ok=False, requested_url=url, final_url=current, status="FETCH_FAILED",
                redirect_chain=chain, redirect_count=len(chain),
                error_code=exc.error_code, error_detail=exc.detail,
                latency_ms=int((time.monotonic() - started) * 1000))

        if time.monotonic() > deadline:
            return FetchResult(
                ok=False, requested_url=url, final_url=current, status="TIMEOUT",
                redirect_chain=chain, redirect_count=len(chain),
                error_code="TIMEOUT", error_detail="Overall fetch timeout exceeded.",
                latency_ms=int((time.monotonic() - started) * 1000))

        try:
            status, headers, body, reason = _one_request(
                scheme, host, port, path_qs, pinned_ip, method=method, deadline=deadline)
        except FetchError as exc:
            status_label = "TIMEOUT" if exc.error_code == "TIMEOUT" else "FETCH_FAILED"
            return FetchResult(
                ok=False, requested_url=url, final_url=current, status=status_label,
                redirect_chain=chain, redirect_count=len(chain),
                error_code=exc.error_code, error_detail=exc.detail,
                latency_ms=int((time.monotonic() - started) * 1000))

        if status in (301, 302, 303, 307, 308) and "location" in headers:
            chain.append(current)
            if len(chain) > MAX_REDIRECTS:
                return FetchResult(
                    ok=False, requested_url=url, final_url=current,
                    status="FETCH_FAILED", redirect_chain=chain,
                    redirect_count=len(chain), error_code="REDIRECT_LIMIT_EXCEEDED",
                    error_detail=f"More than {MAX_REDIRECTS} redirects; aborted.",
                    latency_ms=int((time.monotonic() - started) * 1000))
            location = headers["location"]
            # Resolve a relative Location against the current URL.
            base = urlsplit(current)
            if location.startswith("//"):
                location = f"{base.scheme}:{location}"
            elif location.startswith("/"):
                location = urlunsplit((base.scheme, base.netloc, location, "", ""))
            elif "://" not in location:
                dirpath = base.path.rsplit("/", 1)[0] or ""
                location = urlunsplit(
                    (base.scheme, base.netloc, f"{dirpath}/{location}", "", ""))
            current = location
            continue

        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if status < 400 and content_type and content_type not in ALLOWED_CONTENT_TYPES:
            return FetchResult(
                ok=False, requested_url=url, final_url=current,
                status="FETCH_FAILED", http_status=status, content_type=content_type,
                redirect_chain=chain, redirect_count=len(chain),
                error_code="UNSUPPORTED_CONTENT_TYPE",
                error_detail=(f"Content-Type '{content_type}' is not one of the "
                             f"supported text formats; refusing to parse it as "
                             f"research evidence."),
                latency_ms=int((time.monotonic() - started) * 1000))

        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - decode() with errors= doesn't raise
            text = ""

        import hashlib
        digest = hashlib.sha256(body).hexdigest()

        return FetchResult(
            ok=status < 400, requested_url=url, final_url=current,
            status="COMPLETED" if status < 400 else "FETCH_FAILED",
            http_status=status, content_type=content_type or None,
            body_text=text, bytes_read=len(body),
            redirect_chain=chain, redirect_count=len(chain),
            latency_ms=int((time.monotonic() - started) * 1000),
            content_hash=digest,
            error_code=None if status < 400 else f"HTTP_{status}",
            error_detail=None if status < 400 else f"Server responded {status} {reason}".strip())

    return FetchResult(  # pragma: no cover - loop always returns above
        ok=False, requested_url=url, final_url=current, status="FETCH_FAILED",
        redirect_chain=chain, redirect_count=len(chain),
        error_code="REDIRECT_LIMIT_EXCEEDED", error_detail="Too many redirects.")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
