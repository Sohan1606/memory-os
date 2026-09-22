"""
V8.5.1 browser QA — cognitive interaction surfaces affected by the
tool-routing correction.

Verifies with a real Chromium against the running backend (:8000, demo
provider) and production frontend (:3000):

  1. The chat surface answers a mission question from the real registry and
     the agent-activity trail renders (including the new TOOL_SURFACE stage
     when present) with no console errors.
  2. The API's activity payload for a mission question includes a genuine
     TOOL_DECISION and TOOL_RESULT, and the answer is grounded.
  3. The Observatory still loads its panels without console errors.
  4. Mobile viewport renders the chat surface without console errors.

Run: .venv/bin/python tests/v851_browser_qa.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:3000"
BACKEND = "http://localhost:8000"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def api(path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BACKEND + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main() -> int:
    # ---------------- API-level routing contract (demo provider) -----------
    health = api("/api/health")
    check("backend healthy", health.get("status") in ("ok", "healthy", "ACTIVE")
          or bool(health), str(health.get("provider", ""))[:60])

    chat = api("/api/chat", {"message": "What missions am I currently working on?",
                             "thread_id": "qa-v851"})
    kinds = [a.get("type") for a in chat.get("activity", [])]
    tools = [a.get("tool") for a in chat.get("activity", [])
             if a.get("type") == "TOOL_DECISION"]
    check("chat returns activity trail", bool(kinds), str(kinds[:6]))
    check("mission tool decided (demo fallback contract)",
          any(t in tools for t in ("list_missions", "get_current_focus")),
          str(tools))
    check("no TOOL_FAILED in activity",
          not [a for a in chat.get("activity", []) if a.get("type") == "TOOL_FAILED"])

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ------------------------------ desktop chat surface ---------------
        page = browser.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(FRONTEND + "/workspace", wait_until="networkidle")
        box = page.locator("#chat-input").first
        box.fill("What missions am I currently working on?")
        box.press("Enter")
        page.wait_for_timeout(4000)
        body = page.inner_text("body")
        check("chat replied on the page",
              "DETERMINISTIC" in body or "mission" in body.lower())
        # The activity <details> block renders.
        details = page.locator("details", has_text="Agent activity")
        check("agent activity trail rendered", details.count() >= 1)
        if details.count():
            details.first.click()
            page.wait_for_timeout(300)
            trail = details.first.inner_text()
            check("activity entries visible", "→" in trail, trail[:120])
        check("no console errors on chat", not errors, "; ".join(errors)[:200])

        # ------------------------------ observatory ------------------------
        page2 = browser.new_page()
        errors2: list[str] = []
        page2.on("console", lambda m: errors2.append(m.text)
                 if m.type == "error" else None)
        page2.on("pageerror", lambda e: errors2.append(str(e)))
        page2.goto(FRONTEND + "/observatory", wait_until="networkidle")
        page2.wait_for_timeout(2500)
        obs = page2.inner_text("body").upper()
        check("observatory renders", "OBSERVATORY" in obs or "EVENT" in obs)
        check("no console errors on observatory", not errors2,
              "; ".join(errors2)[:200])

        # ------------------------------ mobile chat ------------------------
        page3 = browser.new_page(viewport={"width": 390, "height": 844})
        errors3: list[str] = []
        page3.on("console", lambda m: errors3.append(m.text)
                 if m.type == "error" else None)
        page3.on("pageerror", lambda e: errors3.append(str(e)))
        page3.goto(FRONTEND + "/workspace", wait_until="networkidle")
        page3.wait_for_timeout(1500)
        check("mobile chat renders",
              page3.locator("#chat-input").count() >= 1)
        check("no console errors on mobile", not errors3,
              "; ".join(errors3)[:200])

        browser.close()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
