"""Browser acceptance gate for V8.4.1 Experience → Skill → Principle.

Runs against real frontend/backend processes and seeds only through HTTP APIs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000"
SCREENSHOT = Path("/tmp/memory-os-v841-observatory.png")
results: list[tuple[str, bool, str]] = []
console_errors: list[str] = []
page_errors: list[str] = []
http_errors: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def request_json(response, label: str):
    check(f"API seed: {label}", response.ok, f"HTTP {response.status}")
    return response.json()


def seed(page: Page) -> tuple[str, str]:
    skills: list[tuple[str, list[str]]] = []
    patterns = [
        ("browser_deploy_logs", "a deployment health check fails",
         "inspect deployment logs before retrying"),
        ("browser_migration_error", "a database migration fails",
         "inspect the database error before rerunning"),
    ]
    generalization = "Investigate the actual failure signal before repeating a failed operation."
    for pattern_index, (pattern, situation, action) in enumerate(patterns):
        experience_ids: list[str] = []
        for index in range(3):
            observation = request_json(page.request.post(
                f"{BASE}/api/observations", data={
                    "content": f"Browser QA observed success {pattern_index}-{index}",
                    "source": "outcome", "origin": f"browser:{pattern}:{index}",
                    "confidence": 0.95,
                }), f"observation {pattern_index}-{index}")
            oid = observation["observation"]["id"]
            experience = request_json(page.request.post(
                f"{BASE}/api/experiences", data={
                    "situation": situation, "evidence_ids": [oid],
                    "action": action,
                    "outcome": "the underlying failure signal was identified",
                    "success": True, "pattern_key": pattern,
                    "context": {"generalization_hint": generalization},
                    "source": "browser-qa",
                }), f"experience {pattern_index}-{index}")
            for lifecycle in ("enriched", "validated", "active"):
                experience = request_json(page.request.post(
                    f"{BASE}/api/experiences/{experience['id']}/lifecycle",
                    data={"lifecycle": lifecycle,
                          "reason": f"Browser QA {lifecycle}"}),
                    f"experience {pattern_index}-{index} {lifecycle}")
            experience_ids.append(experience["id"])
        candidate = request_json(page.request.post(
            f"{BASE}/api/skills/candidates", data={
                "name": f"Browser skill {pattern_index + 1}",
                "statement": f"When {situation}, {action}.",
                "trigger": situation,
                "procedure": [action, "identify the actual failure signal"],
                "expected_outcome": "the cause is known before another attempt",
                "supporting_experience_ids": experience_ids,
                "pattern_key": pattern,
                "generalization_hint": generalization,
                "source": "browser-qa",
            }), f"skill {pattern_index + 1} candidate")
        validation = request_json(page.request.post(
            f"{BASE}/api/skills/{candidate['id']}/validate"),
            f"skill {pattern_index + 1} validation")
        check(f"Skill {pattern_index + 1} validation passed",
              validation["decision"] == "PASS", validation["decision"])
        trusted = request_json(page.request.post(
            f"{BASE}/api/skills/{candidate['id']}/promote"),
            f"skill {pattern_index + 1} promotion")
        skills.append((trusted["id"], experience_ids))

    principle = request_json(page.request.post(
        f"{BASE}/api/principles/candidates", data={
            "name": "Investigate before repeating failures",
            "statement": generalization,
            "supporting_skill_ids": [skill[0] for skill in skills],
            "supporting_experience_ids": [eid for _, ids in skills for eid in ids],
            "application": ["inspect current evidence", "then decide whether to repeat"],
            "expected_outcome": "repeated work is informed by the actual cause",
            "pattern_key": "browser_investigate_before_repeat",
            "generality": 0.85, "source": "browser-qa",
        }), "principle candidate")
    validation = request_json(page.request.post(
        f"{BASE}/api/principles/{principle['id']}/validate"),
        "principle validation")
    check("Principle validation passed", validation["decision"] == "PASS",
          validation["decision"])
    principle = request_json(page.request.post(
        f"{BASE}/api/principles/{principle['id']}/promote"),
        "principle promotion")

    # One real supported Skill use proves reputation is not a confidence alias.
    usage = request_json(page.request.post(
        f"{BASE}/api/skills/{skills[0][0]}/use", data={
            "influenced_kind": "decision", "influenced_id": "browser-decision",
            "how": "Browser QA decision influence",
        }), "skill use")
    request_json(page.request.post(
        f"{BASE}/api/learning/usages/{usage['id']}/outcome", data={
            "verdict": "SUPPORTED", "detail": "The logs exposed the timeout",
            "evidence": ["browser incident report"],
        }), "skill outcome")
    return skills[0][0], principle["id"]


def attach(page: Page) -> None:
    page.on("console", lambda msg: console_errors.append(msg.text)
            if msg.type == "error" else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.on("response", lambda response: http_errors.append(
        f"{response.status} {response.url}") if response.status >= 400 else None)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 950})
        page = context.new_page()
        attach(page)

        reset = page.request.post(f"{BASE}/api/reset")
        check("Reset test namespace through real API", reset.ok,
              f"HTTP {reset.status}")
        page.goto(f"{BASE}/observatory", wait_until="networkidle")
        page.wait_for_timeout(1200)
        empty = page.inner_text("body")
        check("Learning panel renders empty state",
              "NO EVIDENCE-BACKED EXPERIENCES RECORDED YET" in empty.upper()
              and "NO SKILLS RECORDED YET" in empty.upper()
              and "NO PRINCIPLES RECORDED YET" in empty.upper())

        skill_id, principle_id = seed(page)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        upper = body.upper()
        check("V8.4.1 panel renders", "EXPERIENCE → SKILL → PRINCIPLE" in upper)
        check("Real API counts render", "6 EXPERIENCES" in upper
              and "2 SKILLS" in upper and "1 PRINCIPLE" in upper)
        check("Experience evidence renders", "1 EVIDENCE" in upper)
        check("Skill lifecycle renders", "REINFORCED" in upper)
        check("Principle lifecycle renders", "TRUSTED" in upper)
        check("Confidence is rendered", "CONFIDENCE" in upper and "%" in body)
        check("Reputation is rendered separately", "TRUSTED" in upper
              and "INSUFFICIENT EVIDENCE" in upper)
        check("Scope is rendered", "USER" in upper)
        check("Usage and evidence counts render", "3 SUPPORTING" in upper
              and "1 USES" in upper)

        skill_button = page.get_by_role("button", name="Browser skill 1")
        check("Skill is keyboard/button inspectable", skill_button.count() == 1)
        skill_button.click()
        page.wait_for_timeout(800)
        detail = page.inner_text("body")
        detail_upper = detail.upper()
        check("Evidence/provenance inspector opens",
              "EVIDENCE & PROVENANCE · BROWSER SKILL 1" in detail_upper)
        check("Validation detail is visible", "LATEST VALIDATION: PASS" in detail_upper)
        check("Evidence summary is visible", "3 SUPPORTING" in detail_upper)
        check("Usage summary is visible", "1 USES" in detail_upper)
        check("No private reasoning is exposed",
              "NO PRIVATE CHAIN-OF-THOUGHT" in detail_upper)
        focus = page.request.get(f"{BASE}/api/focus").json()
        focused = focus.get("focus", [])
        check("Inspecting sets canonical stable-id focus",
              bool(focused) and focused[0]["subject_id"] == skill_id,
              focused[0]["subject_id"] if focused else "none")

        page.screenshot(path=str(SCREENSHOT), full_page=True)

        # Every product route at both required viewport classes.
        routes = ["/", "/memory", "/architecture", "/workspace", "/observatory"]
        for label, viewport in (("desktop", {"width": 1440, "height": 900}),
                                ("mobile", {"width": 390, "height": 844})):
            route_page = context.new_page()
            route_page.set_viewport_size(viewport)
            attach(route_page)
            for route in routes:
                route_page.goto(f"{BASE}{route}", wait_until="networkidle")
                route_page.wait_for_timeout(400)
                overflow = route_page.evaluate(
                    "Math.max(0, document.documentElement.scrollWidth "
                    "- document.documentElement.clientWidth)")
                check(f"{label} {route} renders without overflow", overflow == 0,
                      f"{overflow}px")
                main = route_page.locator("main").first
                check(f"{label} {route} has main content",
                      main.count() == 1 and len(main.inner_text()) > 40)
            route_page.close()

        principle = page.request.get(
            f"{BASE}/api/principles/{principle_id}").json()
        check("Rendered Principle remains canonical backend state",
              principle["lifecycle"] == "trusted", principle["lifecycle"])

        real_console = [error for error in console_errors
                        if "favicon" not in error.lower()]
        check("No browser console errors", not real_console,
              "; ".join(real_console[:3]))
        check("No page errors", not page_errors, "; ".join(page_errors[:3]))
        check("No HTTP responses >= 400", not http_errors,
              "; ".join(http_errors[:3]))
        # Next.js 15.5 mounts a dev-tools portal even on a healthy page. Inspect
        # its shadow root for an actual error badge/dialog instead of treating
        # the portal host itself as an error overlay.
        next_error = page.locator("nextjs-portal").evaluate_all("""portals =>
          portals.some(portal => {
            const root = portal.shadowRoot;
            return root && root.querySelector(
              '[data-error="true"], [data-nextjs-dialog-overlay], ' +
              '[data-nextjs-error-overlay], #nextjs__container_errors');
          })
        """)
        check("No Next.js error overlay", not next_error)
        browser.close()

    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} browser assertions passed")
    print(f"Screenshot: {SCREENSHOT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
