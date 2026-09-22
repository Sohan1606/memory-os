"""V9 desktop/mobile browser acceptance against live :3000/:8000 services."""
from playwright.sync_api import sync_playwright

URL = "http://localhost:3000"
checks: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def exercise(page, mobile=False):
    console: list[str] = []
    failed: list[str] = []
    page.on("console", lambda msg: console.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: console.append(str(err)))
    page.on("requestfailed", lambda req: failed.append(f"{req.method} {req.url}: {req.failure}"))
    page.goto(URL + "/workspace", wait_until="networkidle")
    body = page.inner_text("body")
    check(("mobile" if mobile else "desktop") + " workspace loads",
          "What are you thinking about?" in body)
    mic_honest = (page.get_by_text("MIC NOT AVAILABLE").count() > 0 or
                  page.get_by_role("button", name="Microphone").count() > 0)
    check(("mobile" if mobile else "desktop") + " microphone state is explicit", mic_honest)
    page.locator("#chat-input").fill("I think I should change roles next year.")
    page.locator("#chat-input").press("Enter")
    page.get_by_text("Live cognitive activity").wait_for(timeout=30000)
    check(("mobile" if mobile else "desktop") + " live activity appears before final surface",
          page.get_by_text("Live cognitive activity").count() > 0)
    page.get_by_text("Cognitive surface", exact=True).wait_for(timeout=30000)
    text = page.inner_text("body")
    check(("mobile" if mobile else "desktop") + " backend surface updates",
          "Understanding" in text and "Waiting for you" in text)
    check(("mobile" if mobile else "desktop") + " semantic object is real",
          "BELIEF" in text)
    check(("mobile" if mobile else "desktop") + " no console errors",
          not console, "; ".join(console)[:160])
    check(("mobile" if mobile else "desktop") + " no failed requests",
          not failed, "; ".join(failed)[:160])


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        exercise(browser.new_page(viewport={"width": 1440, "height": 900}))
        exercise(browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True), mobile=True)
        obs = browser.new_page(viewport={"width": 1200, "height": 900})
        errors: list[str] = []
        obs.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        obs.goto(URL + "/observatory", wait_until="networkidle")
        check("Observatory semantic state renders", "V9 SEMANTIC STATE" in obs.inner_text("body"))
        check("Observatory has no console errors", not errors, "; ".join(errors)[:160])
        browser.close()
    failures = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks)-len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
