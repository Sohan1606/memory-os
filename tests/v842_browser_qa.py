"""Browser acceptance gate for V8.4.2 Advanced Explanation Engine.

Runs against real frontend/backend processes and seeds only through HTTP APIs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000"
SCREENSHOT = Path("/tmp/memory-os-v842-observatory.png")
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
    # 1. Seed memories
    mem1 = request_json(page.request.post(
        f"{BASE}/api/memories", data={
            "content": "Browser QA test memory for explainability",
            "category": "PROJECT", "importance": 0.9, "confidence": 0.85,
        }), "seed memory 1")
    m1_id = mem1["memory"]["id"]

    # 2. Query explanation via API
    exp = request_json(page.request.post(
        f"{BASE}/api/explanations/query", data={
            "subject_kind": "memory",
            "subject_id": m1_id,
            "query_intent": "why",
            "persist": True,
        }), "seed explanation snapshot")
    exp_id = exp["id"]
    return m1_id, exp_id


def run():
    print(f"Starting V8.4.2 Browser QA against {BASE}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error",) else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        # 1. Health check
        res = page.request.get(f"{BASE}/api/health")
        check("Backend /api/health reachable", res.ok, f"HTTP {res.status}")
        health_data = res.json()
        check("FastAPI runtime reporting", health_data.get("status") == "ok")

        # 2. Seed data
        m1_id, exp_id = seed(page)

        # 3. Visit Landing Page
        page.goto(f"{BASE}/")
        page.wait_for_load_state("networkidle")
        check("Landing page loaded", "MEMORY" in (page.title() or "") or len(page.content()) > 100)

        # 4. Visit Workspace
        page.goto(f"{BASE}/workspace")
        page.wait_for_load_state("networkidle")
        check("Workspace loaded", page.locator("body").is_visible())

        # 5. Visit Observatory
        page.goto(f"{BASE}/observatory")
        page.wait_for_load_state("networkidle")
        check("Observatory page loaded", page.locator("body").is_visible())

        # 6. Verify Explanations API directly
        exp_res = page.request.get(f"{BASE}/api/explanations/{exp_id}")
        check("Retrieve explanation snapshot", exp_res.ok and exp_res.json().get("id") == exp_id)
        exp_data = exp_res.json()
        check("Explanation graph has decisive factors", len(exp_data.get("decisive_factors", [])) > 0)
        check("Explanation graph has provenance", bool(exp_data.get("provenance", {}).get("schema_version")))

        # 7. Check console and page errors
        check("Zero page crash errors", len(page_errors) == 0, f"Errors: {page_errors}")
        check("Zero fatal console errors", len(console_errors) == 0, f"Errors: {console_errors}")

        browser.close()

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"\nV8.4.2 Browser QA Summary: {passed}/{total} checks passed.")
    if failed > 0:
        print(f"FAILED: {failed} check(s) failed.")
        sys.exit(1)
    else:
        print("ALL V8.4.2 BROWSER QA CHECKS PASSED.")


if __name__ == "__main__":
    run()
