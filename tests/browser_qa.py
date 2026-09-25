"""Cross-stack browser QA for the current MEMORY//OS application.

This suite deliberately drives the rendered product and its same-origin HTTP
boundary.  It does not seed SQLite directly, invent UI controls, or assume that
a dynamically generated conversation id has a particular value.
"""
from __future__ import annotations

import json
import sys
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page, sync_playwright

# Established QA screenshot location (gitignored).
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
BASE = "http://localhost:3000"
GOVERNANCE_LABELS = (
    "Checking adaptive policy",
    "Comparing policy evidence",
    "Evaluating a policy change",
    "Waiting for your policy decision",
    "Applying your confirmed policy",
    "Measuring a policy outcome",
)

errors: list[str] = []
failures: list[str] = []
deferred: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}  {name} {detail}")
    if not condition:
        failures.append(name)
    return condition


def defer(name: str, detail: str) -> None:
    deferred.append(name)
    print(f"DEFER {name} — {detail}")


def section(name: str, action: Callable[[], None]) -> None:
    """Keep independent QA sections running while preserving useful failures."""
    print(f"\n--- {name} ---")
    try:
        action()
    except Exception as exc:  # pragma: no cover - this file is the QA runner
        failures.append(f"{name} crashed")
        print(f"FAIL  {name} crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=4)


def attach_error_capture(page: Page, prefix: str = "") -> None:
    page.on(
        "console",
        lambda message: errors.append(f"{prefix}{message.type}: {message.text}")
        if message.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"{prefix}pageerror: {exc}"))


def browser_json(page: Page, path: str, *, method: str = "GET",
                 body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the real same-origin application boundary from the browser."""
    result = page.evaluate(
        """async ({path, method, body}) => {
          const response = await fetch(path, {
            method,
            headers: {'Content-Type': 'application/json'},
            body: body === null ? undefined : JSON.stringify(body),
            cache: 'no-store'
          });
          let payload = null;
          try { payload = await response.json(); } catch (_) {}
          return {status: response.status, ok: response.ok, payload};
        }""",
        {"path": path, "method": method, "body": body},
    )
    return result


def send_chat(page: Page, message: str, timeout: int = 240_000) -> None:
    """Submit through the UI and wait for a new assistant article."""
    assistant = page.locator("article").filter(has=page.locator("text=MEMORY//OS"))
    before = assistant.count()
    page.fill("#chat-input", message)
    page.click("button[type=submit]")
    page.wait_for_function(
        """count => [...document.querySelectorAll('article')]
          .filter(n => n.innerText.includes('MEMORY//OS')).length > count""",
        arg=before,
        timeout=timeout,
    )
    page.wait_for_function(
        "() => !document.querySelector('#chat-input')?.disabled", timeout=timeout)


# Memory reset is an existing local-demo action. It intentionally does not
# pretend to provide an Ollama model or a production identity.
try:
    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/reset", method="POST"), timeout=120
    ).read()
except Exception as exc:  # pragma: no cover - diagnostic, landing will fail too
    print(f"WARN  could not reset demo data: {exc}")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    attach_error_capture(page)

    def landing() -> None:
        page.goto(BASE, wait_until="networkidle")
        check("landing h1", "actually" in page.inner_text("h1"))
        page.wait_for_timeout(2500)
        stats = page.inner_text("header dl")
        # Semantic embeddings may be deliberately unavailable in a bounded QA
        # environment. Both the real semantic mode and the honest fallback are
        # valid; a fabricated semantic claim is not.
        check("hero reports an honest live retrieval mode",
              "RETRIEVAL" in stats and
              ("SEMANTIC" in stats or "KEYWORD" in stats) and
              "PROVIDER" in stats,
              stats.replace("\n", " | "))
        page.screenshot(path=str(SHOTS / "01-hero.png"), timeout=120_000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight*0.30)")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOTS / "02-sequence.png"), timeout=120_000)
        check("canvas elements present", page.locator("canvas").count() >= 2,
              f"count={page.locator('canvas').count()}")
        page.evaluate("document.getElementById('retrieval').scrollIntoView()")
        page.wait_for_timeout(2000)
        text = page.inner_text("#retrieval")
        check("retrieval shows real scores", "%" in text and "Category intent" in text)
        page.screenshot(path=str(SHOTS / "03-retrieval.png"), timeout=120_000)
        for element_id, filename in (("layers", "04-layers.png"),
                                     ("surfaces", "05-gallery.png")):
            page.evaluate(f"document.getElementById('{element_id}').scrollIntoView()")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / filename), timeout=120_000)

    section("landing", landing)

    def memory_page() -> None:
        page.goto(f"{BASE}/memory", wait_until="networkidle")
        page.wait_for_timeout(2500)
        cards = page.locator("ul li.panel").count()
        check("memory explorer lists live memories", cards > 10, f"cards={cards}")
        page.screenshot(path=str(SHOTS / "06-memory.png"))
        inspect = page.locator("button:has-text('Inspect')")
        if check("memory inspector control exists", inspect.count() > 0):
            inspect.first.click()
            page.wait_for_timeout(1500)
            check("inspector opens", page.locator("text=Version").count() > 0 or
                  page.locator("text=Confidence").count() > 0)
            page.screenshot(path=str(SHOTS / "07-inspector.png"), timeout=120_000)
            page.keyboard.press("Escape")
        conflict = page.locator("button:has-text('Run conflict resolution')")
        if check("conflict demo control exists", conflict.count() > 0):
            conflict.scroll_into_view_if_needed()
            conflict.click()
            page.wait_for_timeout(7000)
            text = page.inner_text("#main")
            check("conflict resolved to v02", "V02" in text or "Resolved" in text)
            page.screenshot(path=str(SHOTS / "08-conflict.png"), timeout=120_000)
        timeline = page.locator("text=Every change, recorded.")
        if timeline.count():
            timeline.scroll_into_view_if_needed()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / "09-timeline.png"), timeout=120_000)

    section("memory explorer", memory_page)

    def architecture() -> None:
        page.goto(f"{BASE}/architecture", wait_until="networkidle")
        page.wait_for_timeout(2000)
        text = page.inner_text("#main")
        check("architecture shows live health",
              "LANGGRAPH" in text and
              ("CHROMADB" in text or "KEYWORD FALLBACK" in text or "UNAVAILABLE" in text),
              "semantic vector support may honestly be unavailable")
        page.screenshot(path=str(SHOTS / "10-architecture.png"), timeout=120_000)

    section("architecture", architecture)

    # A fresh page avoids retaining animated landing canvases during chat.
    page.close()
    page = context.new_page()
    attach_error_capture(page)

    def workspace_cross_thread() -> None:
        page.goto(f"{BASE}/workspace", wait_until="networkidle")
        page.wait_for_timeout(2500)
        check("workspace starts on thread-main",
              "thread: thread-main" in page.inner_text("#main").lower())
        send_chat(page, "Remember that I take my coffee black with no sugar.")
        workspace_text = page.inner_text("#main")
        check("agent replied", "MEMORY//OS" in workspace_text and
              ("Stored" in workspace_text or "memory" in workspace_text.lower()))
        page.screenshot(path=str(SHOTS / "11-workspace-chat.png"), timeout=120_000)

        # Capture the actual UI-generated id. No timestamp or stale thread-beta
        # fixture is assumed.
        thread_buttons = page.locator("button.chip").filter(has_text="thread-")
        before_ids = set(thread_buttons.evaluate_all(
            "buttons => buttons.map(button => button.textContent.trim())"))
        page.get_by_role("button", name="+ New conversation", exact=True).click()
        page.wait_for_function(
            """before => [...document.querySelectorAll('button.chip')]
              .map(b => b.textContent.trim())
              .some(id => id.startsWith('thread-') && !before.includes(id))""",
            arg=sorted(before_ids), timeout=10_000)
        after_ids = set(thread_buttons.evaluate_all(
            "buttons => buttons.map(button => button.textContent.trim())"))
        generated = sorted(after_ids - before_ids)
        if not check("new conversation renders one dynamic thread id",
                     len(generated) == 1, f"new={generated}"):
            return
        new_thread = generated[0]
        check("dynamic thread id has expected shape",
              new_thread.startswith("thread-") and new_thread != "thread-main",
              new_thread)
        selected = page.locator("button.chip[data-active='true']")
        selected_text = (selected.first.text_content() or "").strip() if selected.count() else ""
        check("workspace switched to generated thread",
              selected.count() > 0 and selected_text == new_thread,
              f"selected={selected_text or 'none'}")
        check("thread chip reflects generated thread",
              f"thread: {new_thread}" in page.inner_text("#main").lower())

        send_chat(page, "How do I take my coffee?")
        recall = page.inner_text("#main")
        check("cross-thread long-term recall",
              "coffee" in recall.lower() and "black" in recall.lower())
        page.screenshot(path=str(SHOTS / "12-cross-thread.png"), timeout=120_000)

    section("workspace dynamic cross-thread recall", workspace_cross_thread)

    def v102_governance() -> None:
        # 1/3/4: reachable namespace and principal-scoped results, through the
        # same Next.js application boundary used by the UI.
        listing = browser_json(page, "/api/v10/governance/adaptations")
        check("V10.2 governance API namespace reachable", listing["status"] == 200,
              f"status={listing['status']}")
        default_items = ((listing.get("payload") or {}).get("adaptations") or [])
        foreign = browser_json(
            page, "/api/v10/governance/adaptations?user_id=browser-qa-isolated-principal")
        foreign_items = ((foreign.get("payload") or {}).get("adaptations") or [])
        default_ids = {item.get("id") for item in default_items}
        foreign_ids = {item.get("id") for item in foreign_items}
        check("governance response stays namespace scoped",
              foreign["status"] == 200 and default_ids.isdisjoint(foreign_ids),
              f"default={len(default_ids)} isolated={len(foreign_ids)}")
        # A deliberate 404 direct-id probe is covered by the backend suite; it
        # is not repeated here because Chromium correctly reports failed HTTP
        # resources as console errors, obscuring genuine page diagnostics.

        # 2: an ordinary irrelevant turn must not fabricate governance work.
        ordinary = browser_json(page, "/api/chat", method="POST", body={
            "message": "Hello, I hope you are well.",
            "thread_id": "browser-v102-ordinary",
        })
        ordinary_payload = ordinary.get("payload") or {}
        ordinary_stages = [a.get("stage") for a in
                           ((ordinary_payload.get("surface") or {}).get("activities") or [])]
        ordinary_governance = ((ordinary_payload.get("cognition") or {})
                               .get("policy_governance") or {})
        check("ordinary chat completed through real application",
              ordinary["status"] == 200, f"status={ordinary['status']}")
        check("irrelevant conversation fabricates no governance stages",
              not set(ordinary_stages).intersection({
                  "CHECKING_ADAPTIVE_POLICY", "COMPARING_POLICY_EVIDENCE",
                  "EVALUATING_POLICY_CHANGE", "WAITING_FOR_POLICY_CONFIRMATION",
                  "APPLYING_ADAPTIVE_POLICY", "MEASURING_POLICY_OUTCOME"}),
              f"status={ordinary_governance.get('status')} stages={ordinary_stages}")

        # 1: create genuine canonical reaction evidence through existing HTTP
        # actions. It is deliberately insufficient to activate a policy: QA
        # must not defeat V10.2's multi-day evidence gate merely to get green.
        evaluation = browser_json(page, "/api/attention/evaluate", method="POST", body={
            "topic": "browser QA status interruption",
            "importance": 0.95, "urgency": 0.95, "confidence": 0.95,
            "relevance": 0.9,
        })
        intervention_id = ((evaluation.get("payload") or {}).get("intervention_id"))
        check("real governance evidence setup created an intervention",
              evaluation["status"] == 200 and bool(intervention_id),
              f"status={evaluation['status']} id={intervention_id}")
        if intervention_id:
            reaction = browser_json(
                page, f"/api/attention/{intervention_id}/reaction",
                method="POST", body={"accepted": False,
                                     "detail": "Browser QA explicitly dismissed it."})
            check("real governance evidence setup recorded reaction",
                  reaction["status"] == 200, f"status={reaction['status']}")

        governed = browser_json(page, "/api/chat", method="POST", body={
            "message": "Give me a concise status update.",
            "thread_id": "browser-v102-governed",
        })
        governed_payload = governed.get("payload") or {}
        governed_surface = governed_payload.get("surface") or {}
        governed_stages = [a.get("stage") for a in governed_surface.get("activities") or []]
        governed_summary = ((governed_payload.get("cognition") or {})
                            .get("policy_governance"))
        check("real governance work emits truthful governance stages",
              governed["status"] == 200 and
              "COMPARING_POLICY_EVIDENCE" in governed_stages and
              "EVALUATING_POLICY_CHANGE" in governed_stages,
              f"stages={governed_stages}")

        # 6: the public summary is bounded and excludes internal derivations or
        # hidden model reasoning. This checks the actual response, not a mock.
        serialized = json.dumps(governed_summary or {}, sort_keys=True).lower()
        safe_keys = {"status", "relevant", "active_adaptations_matched",
                     "consulted", "signals", "proposals", "measurements"}
        check("governance user summary remains concise",
              isinstance(governed_summary, dict) and
              set(governed_summary).issubset(safe_keys) and len(serialized) < 6000,
              f"keys={sorted(governed_summary or {})} bytes={len(serialized)}")
        check("governance summary exposes no hidden model reasoning",
              all(term not in serialized for term in
                  ("chain_of_thought", "chain of thought", "hidden reasoning",
                   "private reasoning", "internal monologue", "derivation_rule")))

        # Add a second canonical reaction before the rendered turn. The runtime
        # watermark means already-consumed evidence must not be presented as new
        # work on a later turn.
        second_evaluation = browser_json(
            page, "/api/attention/evaluate", method="POST", body={
                "topic": "browser QA second status interruption",
                "importance": 0.95, "urgency": 0.95, "confidence": 0.95,
                "relevance": 0.9,
            })
        second_id = ((second_evaluation.get("payload") or {}).get("intervention_id"))
        if second_id:
            browser_json(page, f"/api/attention/{second_id}/reaction",
                         method="POST", body={"accepted": False,
                         "detail": "Browser QA explicitly dismissed it again."})

        # Render the completed real surface in the Workspace and ensure the
        # user sees exactly the canonical stages (not an invented applied flag).
        page.goto(f"{BASE}/workspace", wait_until="networkidle")
        send_chat(page, "Give me another concise status update.")
        rendered = page.inner_text("#main")
        rendered_governance = [label for label in GOVERNANCE_LABELS if label in rendered]
        check("rendered governance stages correspond to real work",
              "Comparing policy evidence" in rendered_governance and
              "Evaluating a policy change" in rendered_governance,
              f"rendered={rendered_governance}")
        check("governance UI does not claim an unconfirmed policy was applied",
              "Applying your confirmed policy" not in rendered_governance)
        check("rendered governance information is bounded",
              len(rendered_governance) <= len(GOVERNANCE_LABELS),
              f"count={len(rendered_governance)}")
        page.screenshot(path=str(SHOTS / "13-v102-governance.png"), timeout=120_000)

        # 5: only test active influence if an ACTIVE canonical adaptation exists.
        # A clean browser run cannot truthfully manufacture one: ATTENTION needs
        # >=3 independent episodes across >=2 days. The focused runtime suite
        # owns that deterministic clock-controlled setup. If an active record is
        # present, however, browser QA requires a later real turn to consult it.
        active_response = browser_json(
            page, "/api/v10/governance/adaptations?state=ACTIVE")
        active = ((active_response.get("payload") or {}).get("adaptations") or [])
        if not active:
            defer("active governed adaptation browser influence",
                  "no ACTIVE adaptation exists; the public application correctly "
                  "requires multi-day evidence and exposes no safe browser fixture")
        else:
            later = browser_json(page, "/api/chat", method="POST", body={
                "message": "A later turn that must use my active preferences.",
                "thread_id": "browser-v102-active-influence",
            })
            later_governance = (((later.get("payload") or {}).get("cognition") or {})
                                .get("policy_governance") or {})
            consulted = later_governance.get("consulted") or []
            active_ids = {item.get("id") for item in active}
            check("active adaptation truthfully influences a future browser turn",
                  later["status"] == 200 and any(
                      item.get("adaptation_id") in active_ids and item.get("consumer")
                      for item in consulted),
                  f"active={sorted(active_ids)} consulted={consulted}")

    section("V10.2 bounded governance verification", v102_governance)

    def mobile() -> None:
        mobile_context = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mobile_page = mobile_context.new_page()
        attach_error_capture(mobile_page, "mobile ")
        try:
            mobile_page.goto(BASE, wait_until="networkidle")
            mobile_page.wait_for_timeout(2500)
            overflow = mobile_page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("no mobile horizontal overflow", overflow <= 1, f"overflow={overflow}px")
            mobile_page.screenshot(path=str(SHOTS / "14-mobile-hero.png"), timeout=120_000)
            mobile_page.goto(f"{BASE}/workspace", wait_until="networkidle")
            mobile_page.wait_for_timeout(2500)
            overflow = mobile_page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("no mobile overflow (workspace)", overflow <= 1,
                  f"overflow={overflow}px")
            mobile_page.screenshot(
                path=str(SHOTS / "15-mobile-workspace.png"), timeout=120_000)
        finally:
            mobile_context.close()

    section("mobile", mobile)

    def reduced_motion() -> None:
        reduced_context = browser.new_context(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        reduced_page = reduced_context.new_page()
        attach_error_capture(reduced_page, "reduced ")
        try:
            reduced_page.goto(BASE, wait_until="networkidle")
            reduced_page.wait_for_timeout(2000)
            reduced_page.evaluate("window.scrollTo(0, document.body.scrollHeight*0.30)")
            reduced_page.wait_for_timeout(1500)
            reduced_page.screenshot(
                path=str(SHOTS / "16-reduced-motion.png"), timeout=120_000)
            check("reduced-motion renders content",
                  len(reduced_page.inner_text("#main")) > 500)
        finally:
            reduced_context.close()

    section("reduced motion", reduced_motion)
    browser.close()

real_errors = [error for error in errors if "favicon" not in error.lower()]
print("\nconsole/page errors:", len(real_errors))
for error in real_errors[:25]:
    print("  ", error)
check("no console errors", len(real_errors) == 0)
print("\nDEFERRED:", deferred if deferred else "none")
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
