#!/usr/bin/env python3
"""V8.5 browser QA — production trust surfaces.

Drives the REAL built frontend against the REAL backend (no mocks):

1.  Observatory renders the Security panel with the honest local-mode state.
2.  Observatory renders the System health panel with per-dependency truth.
3.  Dependency states use the honest vocabulary (no fake health).
4.  Mobile layout (390px) does not overflow on the Observatory.
5.  Zero console errors across the exercised routes.
6.  No sensitive material (password hashes, tokens) in any rendered panel.

Auth-flow browser checks (login/logout/unauthorized) run against a second
backend started with AUTH_MODE=required — see `--auth-base` below. When that
server is up the script additionally verifies:

7.  Unauthenticated API access is rejected (401) in required mode.
8.  Register → login → authenticated /api/auth/session round-trip.
9.  Logout invalidates the session.

Usage:
    python tests/v85_browser_qa.py [--auth-base http://127.0.0.1:8001]
"""
from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
AUTH_BASE = None
for i, arg in enumerate(sys.argv):
    if arg == "--auth-base" and i + 1 < len(sys.argv):
        AUTH_BASE = sys.argv[i + 1]

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def api_json(base: str, path: str, method: str = "GET", body: dict | None = None,
             headers: dict | None = None):
    req = urllib.request.Request(base + path, method=method,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def main() -> int:
    console_errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text)
                if msg.type == "error" else None)

        # ---- Observatory: security + health panels ----
        page.goto(f"{BASE}/observatory", wait_until="networkidle")
        page.wait_for_timeout(2500)
        body = page.inner_text("body")

        upper = body.upper()
        check("Security panel renders", "SECURITY" in upper)
        check("Security panel is honest about local mode",
              "DISABLED" in body or "No authentication is enforced" in body
              or "NOT CONFIGURED" in body)
        check("System health panel renders", "SYSTEM HEALTH" in upper)
        check("Dependency truth vocabulary shown",
              any(s in body for s in ("NOT CONFIGURED", "ACTIVE", "DEGRADED")))
        check("Database dependency reported", "database" in body.lower())
        check("No password hashes leak into UI", "pbkdf2" not in body)
        check("No bearer tokens leak into UI", "Bearer " not in body)

        page.screenshot(path="docs/screenshots/v85-observatory.png", full_page=False)

        # ---- Mobile layout ----
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("console", lambda msg: console_errors.append(msg.text)
                  if msg.type == "error" else None)
        mobile.goto(f"{BASE}/observatory", wait_until="networkidle")
        mobile.wait_for_timeout(1500)
        overflow = mobile.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("Mobile 390px: no horizontal overflow", overflow <= 1,
              f"overflow={overflow}px")
        mobile.screenshot(path="docs/screenshots/v85-observatory-mobile.png")

        check("Zero console errors", not console_errors,
              "; ".join(console_errors[:3]))
        browser.close()

    # ---- Auth flows against the AUTH_MODE=required backend ----
    if AUTH_BASE:
        status, _ = api_json(AUTH_BASE, "/api/memories")
        check("required mode: unauthenticated API is 401", status == 401)

        status, reg = api_json(AUTH_BASE, "/api/auth/register", "POST", {
            "email": "qa@browser.test", "password": "browser-qa-password-1",
            "display_name": "Browser QA"})
        check("required mode: registration works", status in (201, 400))

        status, login = api_json(AUTH_BASE, "/api/auth/login", "POST", {
            "email": "qa@browser.test", "password": "browser-qa-password-1"})
        check("required mode: login works", status == 200)
        token = login.get("token", "")
        check("required mode: login returns no password material",
              "browser-qa-password-1" not in json.dumps(login)
              and "pbkdf2" not in json.dumps(login))

        status, session = api_json(AUTH_BASE, "/api/auth/session",
                                   headers={"Authorization": f"Bearer {token}"})
        check("required mode: session resolves principal",
              status == 200 and session.get("user", {}).get("email") == "qa@browser.test")

        status, _ = api_json(AUTH_BASE, "/api/auth/logout", "POST", {},
                             headers={"Authorization": f"Bearer {token}"})
        check("required mode: logout succeeds", status == 200)
        status, _ = api_json(AUTH_BASE, "/api/memories",
                             headers={"Authorization": f"Bearer {token}"})
        check("required mode: token dead after logout", status == 401)
    else:
        print("NOTE  --auth-base not supplied; auth-flow browser checks skipped.")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
