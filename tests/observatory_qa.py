"""
Browser QA for the v8 cognitive Observatory.

Runs against a real running stack. Every assertion checks rendered output, and
screenshots are captured so layout regressions are visible, not just DOM-present.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []
console_errors: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # Seed one real conversational turn so the Observatory has evidence.
        # A real local model needs far longer than the 30s default to answer.
        page.request.post(f"{BASE}/api/chat", data={
            "message": "I need to ship the billing migration by Friday",
            "thread_id": "qa-observatory"},
            headers={"content-type": "application/json"}, timeout=300000)

        page.goto(f"{BASE}/observatory", wait_until="networkidle")
        page.wait_for_timeout(1500)

        body = page.inner_text("body")
        upper = body.upper()

        check("Observatory route renders", "OBSERVATORY" in upper)
        check("Headline present",
              page.locator("h1").first.inner_text().strip() != "")

        # Panels
        for panel in ("World", "Activity", "Autonomy", "Capabilities",
                      "Learning", "Sandbox", "Predictions", "Intent"):
            check(f"Panel: {panel}", panel.upper() in upper)

        # Honest state language must be visible, not hidden.
        check("Honest NOT CONFIGURED state shown", "NOT CONFIGURED" in upper)
        check("Honest INSUFFICIENT EVIDENCE shown", "INSUFFICIENT EVIDENCE" in upper)
        check("Integrations not overclaimed",
              "CALENDAR / EMAIL / FILES" in upper)

        # Real cognitive activity from the seeded turn.
        check("Activity feed shows real events",
              "Remembered" in body or "Recalled" in body
              or "conversation" in body.lower())
        check("World model shows tracked item",
              "billing migration" in body.lower())

        page.screenshot(path=str(SHOTS / "observatory-desktop.png"), full_page=True)

        # Developer mode reveals raw types.
        page.get_by_label("Developer mode").check()
        page.wait_for_timeout(900)
        dev_body = page.inner_text("body")
        check("Developer mode reveals raw event types",
              "conversation.message" in dev_body or "need.detected" in dev_body)
        page.screenshot(path=str(SHOTS / "observatory-developer.png"), full_page=True)
        page.get_by_label("Developer mode").uncheck()

        # Sandbox: run a simulation and confirm it is labelled and non-mutating.
        world_before = page.request.get(f"{BASE}/api/world").json()["summary"]
        page.fill("#sandbox-q", "What if I delay the migration by two weeks?")
        page.get_by_role("button", name="Run", exact=True).click()
        page.wait_for_timeout(1800)
        sandbox_body = page.inner_text("body")
        check("Sandbox labelled SIMULATION ONLY", "SIMULATION ONLY" in sandbox_body)
        check("Sandbox shows assumptions", "ASSUMPTIONS" in sandbox_body.upper())
        world_after = page.request.get(f"{BASE}/api/world").json()["summary"]
        check("Sandbox did not mutate world state", world_before == world_after,
              f"{world_before} == {world_after}")
        page.screenshot(path=str(SHOTS / "observatory-sandbox.png"), full_page=True)

        # Autonomy: switching level is real and persists to the backend.
        page.get_by_role("button", name="observe", exact=True).click()
        page.wait_for_timeout(1200)
        level = page.request.get(f"{BASE}/api/autonomy").json()["level"]
        check("Autonomy change persists to backend", level == "observe", level)
        page.get_by_role("button", name="assist", exact=True).click()
        page.wait_for_timeout(1000)

        # Why inspector: clicking an activity row explains the object.
        rows = page.locator("ul button")  # activity rows are the only list buttons
        if rows.count() > 0:
            rows.first.click()
            page.wait_for_timeout(1200)
            check("Why inspector opens", "WHY INSPECTOR" in page.inner_text("body").upper() or "WHY ·" in page.inner_text("body").upper())
        else:
            check("Why inspector opens", False, "no activity rows")

        # Chat surfaces the cognitive trace in the primary UI.
        page.goto(f"{BASE}/workspace", wait_until="networkidle")
        page.wait_for_timeout(1200)
        chat_input = page.locator("#chat-input")
        if chat_input.count() > 0:
            chat_input.fill("I am planning a launch in March")
            # Wait for the reply to actually land: a real local model takes far
            # longer than the deterministic planner.
            page.keyboard.press("Enter")
            # Wait for the disclosure itself rather than a DOM size delta: the
            # echoed user message alone changes length and would end the wait
            # before the assistant turn has rendered.
            try:
                page.wait_for_function(
                    "() => document.body.innerText.toUpperCase()"
                    ".includes('UNDERSTANDING')", timeout=240000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            work_body = page.inner_text("body")
            check("Chat shows Understanding disclosure",
                  "UNDERSTANDING" in work_body.upper())
            page.screenshot(path=str(SHOTS / "workspace-understanding.png"),
                            full_page=True)
        else:
            check("Chat shows Understanding disclosure", False, "no chat input")

        # Mobile.
        mobile = ctx.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.goto(f"{BASE}/observatory", wait_until="networkidle")
        mobile.wait_for_timeout(1200)
        overflow = mobile.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)")
        check("No horizontal overflow on mobile", overflow == 0, f"{overflow}px")
        mobile.screenshot(path=str(SHOTS / "observatory-mobile.png"), full_page=True)

        # Reduced motion still renders content.
        reduced = browser.new_context(reduced_motion="reduce",
                                      viewport={"width": 1280, "height": 900})
        rp = reduced.new_page()
        rp.goto(f"{BASE}/observatory", wait_until="networkidle")
        rp.wait_for_timeout(900)
        check("Reduced motion renders content",
              "OBSERVATORY" in rp.inner_text("body").upper())
        reduced.close()

        real_errors = [e for e in console_errors if "favicon" not in e.lower()]
        check("No console errors", len(real_errors) == 0,
              "; ".join(real_errors[:3]))

        browser.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    print(f"screenshots: {SHOTS}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
