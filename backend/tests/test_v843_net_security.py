"""
V8.4.3 — SSRF / resource-safety adversarial test suite for `net_security.py`.

These tests fail loudly if any protection is weakened or removed, per the
V8.4.3 spec's Phase 16 requirement. No test here is a "soft" assertion.
"""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.cognition import net_security as ns


# --------------------------------------------------------------- syntax-only
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.0.0.1:80/admin",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
])
def test_loopback_blocked(url):
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax(url)


@pytest.mark.parametrize("url", [
    "http://10.0.0.5/", "http://172.16.4.4/", "http://192.168.1.10/",
])
def test_private_ipv4_blocked(url):
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax(url)


def test_private_ipv6_blocked():
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax("http://[fc00::1]/")


def test_link_local_and_cloud_metadata_blocked():
    with pytest.raises(ns.SSRFBlocked) as exc:
        ns.validate_url_syntax("http://169.254.169.254/latest/meta-data/")
    assert "link-local" in exc.value.detail.lower() or "metadata" in exc.value.detail.lower()


def test_ipv4_mapped_ipv6_blocked():
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax("http://[::ffff:10.0.0.1]/")


@pytest.mark.parametrize("url", [
    "http://2130706433/",       # decimal for 127.0.0.1
    "http://0x7f000001/",       # hex for 127.0.0.1
    "http://0177.0.0.1/",       # octal first octet
    "http://127.1/",            # partial dotted shorthand
])
def test_alternate_ip_encodings_blocked(url):
    with pytest.raises(ns.SSRFBlocked) as exc:
        ns.validate_url_syntax(url)
    assert exc.value.reason == "ALT_IP_ENCODING"


@pytest.mark.parametrize("scheme_url", [
    "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)",
    "data:text/html,hi", "blob:http://example.com/uuid", "chrome://settings/",
])
def test_unsafe_schemes_blocked(scheme_url):
    with pytest.raises(ns.SSRFBlocked) as exc:
        ns.validate_url_syntax(scheme_url)
    assert exc.value.reason == "UNSAFE_SCHEME"


def test_userinfo_rejected():
    with pytest.raises(ns.SSRFBlocked) as exc:
        ns.validate_url_syntax("http://user:pass@example.com/")
    assert exc.value.reason == "USERINFO_REJECTED"


def test_invalid_url_rejected():
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax("")
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax("not a url at all")


def test_port_policy():
    with pytest.raises(ns.SSRFBlocked) as exc:
        ns.validate_url_syntax("http://example.com:9999/")
    assert exc.value.reason == "PORT_NOT_ALLOWED"
    # Allowed ports pass syntax validation (network layer validated separately).
    assert ns.validate_url_syntax("https://example.com:443/") == (
        "https", "example.com", 443, "/")


def test_multicast_and_reserved_blocked():
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax("http://224.0.0.1/")


def test_carrier_grade_nat_blocked():
    import ipaddress
    assert ns.classify_ip(ipaddress.ip_address("100.64.0.1")) is not None


def test_unspecified_address_blocked():
    with pytest.raises(ns.SSRFBlocked):
        ns.validate_url_syntax("http://0.0.0.0/")


# ------------------------------------------------------------ DNS rebinding
def test_dns_rebinding_localhost_blocked_end_to_end():
    """`localhost` passes syntax (it is a hostname) but must be blocked once
    resolved — this is the literal DNS-rebinding defense, exercised via the
    real resolver, not a mock."""
    result = ns.fetch("http://localhost/")
    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.error_code == "DNS_REBINDING_BLOCKED"


def test_resolve_and_pin_rejects_private_answers():
    with pytest.raises(ns.SSRFBlocked):
        ns.resolve_and_pin("localhost", 80)


# --------------------------------------------------------- malformed / misc
def test_malformed_url_variants():
    for bad in ["http://", "http:///path", "://missing-scheme.com", "http:// space.com/"]:
        with pytest.raises(ns.SSRFBlocked):
            ns.validate_url_syntax(bad)


def test_fetch_never_raises_on_bad_input():
    """`fetch()` must return a truthful FetchResult, never raise, even for
    completely invalid input — failure must stay first-class and inspectable."""
    result = ns.fetch("not-a-url")
    assert result.ok is False
    assert result.status == "BLOCKED"
    assert result.error_code in ("INVALID_URL", "UNSAFE_SCHEME")


# ------------------------------------------------------- local HTTP fixtures
class _RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/internal")
            self.end_headers()
        elif self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        elif self.path == "/huge":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            chunk = b"x" * 65536
            try:
                for _ in range(64):  # 4MB, exceeds the 2MB cap
                    self.wfile.write(chunk)
            except Exception:
                pass
        elif self.path == "/binary":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"\x00\x01\x02binarydata")
        elif self.path == "/slow":
            time.sleep(30)
            self.send_response(200)
            self.end_headers()
        else:
            body = b"<html><head><title>T</title></head><body>hello world, this is a test page with enough text.</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture(scope="module")
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


def test_redirect_to_private_target_blocked(local_server):
    """The server itself IS a private/loopback address, so even reaching it
    is blocked outright — this proves the SSRF layer refuses private targets
    unconditionally. Redirect-target re-validation against a PUBLIC starting
    point that then redirects to a private one is exercised for real in the
    live-network suite (test_v843_live_network.py) via httpbin.org."""
    result = ns.fetch(f"http://127.0.0.1:{local_server}/redirect-private")
    assert result.ok is False
    assert result.status == "BLOCKED"


# The remaining tests below exercise the FETCH MECHANICS (size caps, redirect
# exhaustion, content-type rejection, timeouts) in isolation from the SSRF
# layer, by allowlisting the loopback test server for exactly one call. This
# does not weaken the SSRF policy for real traffic — `resolve_and_pin` is
# monkeypatched back to itself everywhere else, and every SSRF test above
# runs against the real, unmodified function.
@pytest.fixture
def allow_loopback(monkeypatch):
    """Allow exactly 127.0.0.1 through the SSRF classifier for this test only,
    by wrapping (not disabling) the real `classify_ip`. Every other address
    is still classified normally — this proves fetch MECHANICS in isolation
    without weakening the SSRF policy under test elsewhere."""
    real_classify = ns.classify_ip

    def _classify(ip):
        if str(ip) == "127.0.0.1":
            return None
        return real_classify(ip)

    monkeypatch.setattr(ns, "classify_ip", _classify)
    monkeypatch.setattr(ns, "ALLOWED_PORTS", ns.ALLOWED_PORTS | {0})
    # Ports are dynamic in the test server; widen the allowed set for the
    # duration of this fixture only.
    import socket as _socket
    monkeypatch.setattr(ns, "ALLOWED_PORTS", frozenset(range(1, 65536)))
    yield


def test_oversized_response_blocked(local_server, allow_loopback):
    result = ns.fetch(f"http://127.0.0.1:{local_server}/huge")
    assert result.ok is False
    assert result.status == "FETCH_FAILED"
    assert result.error_code == "RESPONSE_TOO_LARGE"


def test_redirect_exhaustion_blocked(local_server, allow_loopback):
    result = ns.fetch(f"http://127.0.0.1:{local_server}/redirect-loop")
    assert result.ok is False
    assert result.error_code == "REDIRECT_LIMIT_EXCEEDED"
    assert result.redirect_count >= ns.MAX_REDIRECTS


def test_unsupported_content_type_rejected(local_server, allow_loopback):
    result = ns.fetch(f"http://127.0.0.1:{local_server}/binary")
    assert result.ok is False
    assert result.error_code == "UNSUPPORTED_CONTENT_TYPE"


def test_successful_fetch_extracts_bytes_and_hash(local_server, allow_loopback):
    result = ns.fetch(f"http://127.0.0.1:{local_server}/ok")
    assert result.ok is True
    assert result.status == "COMPLETED"
    assert result.bytes_read > 0
    assert result.content_hash is not None
    assert "hello world" in (result.body_text or "")


def test_request_timeout_reported_honestly(local_server, allow_loopback, monkeypatch):
    monkeypatch.setattr(ns, "OVERALL_TIMEOUT_S", 1.0)
    monkeypatch.setattr(ns, "CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(ns, "READ_TIMEOUT_S", 0.5)
    result = ns.fetch(f"http://127.0.0.1:{local_server}/slow")
    assert result.ok is False
    assert result.status == "TIMEOUT"
