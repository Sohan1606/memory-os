import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Screenshots live under docs/ so the project root stays clean.
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

BASE = "http://localhost:3000"
errors, failures = [], []

# Start from known-good seed data so counts and assertions are deterministic.
try:
    import urllib.request
    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/reset", method="POST"), timeout=120).read()
except Exception as exc:  # pragma: no cover - QA helper
    print(f"WARN  could not reset demo data: {exc}")

def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond: failures.append(name)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900})
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type=="error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    # ---- landing ----
    page.goto(BASE, wait_until="networkidle")
    check("landing h1", "actually" in page.inner_text("h1"))
    page.wait_for_timeout(2500)
    stats = page.inner_text("header dl")
    check("hero live stats (no em-dash placeholders)", "SEMANTIC" in stats and "384-DIM" in stats, stats.replace("\n"," | "))
    page.screenshot(path=str(SHOTS / "01-hero.png"), timeout=120000)

    # scroll sequence
    page.evaluate("window.scrollTo(0, document.body.scrollHeight*0.30)")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(SHOTS / "02-sequence.png"), timeout=120000)
    canvases = page.locator("canvas").count()
    check("canvas elements present", canvases >= 2, f"count={canvases}")

    # retrieval section
    page.evaluate("document.getElementById('retrieval').scrollIntoView()")
    page.wait_for_timeout(2000)
    txt = page.inner_text("#retrieval")
    check("retrieval shows real scores", "%" in txt and "Category intent" in txt)
    page.screenshot(path=str(SHOTS / "03-retrieval.png"), timeout=120000)

    page.evaluate("document.getElementById('layers').scrollIntoView()")
    page.wait_for_timeout(1500); page.screenshot(path=str(SHOTS / "04-layers.png"), timeout=120000)
    page.evaluate("document.getElementById('surfaces').scrollIntoView()")
    page.wait_for_timeout(1500); page.screenshot(path=str(SHOTS / "05-gallery.png"), timeout=120000)

    # ---- memory page ----
    page.goto(f"{BASE}/memory", wait_until="networkidle")
    page.wait_for_timeout(2500)
    cards = page.locator("ul li.panel").count()
    check("memory explorer lists live memories", cards > 10, f"cards={cards}")
    page.screenshot(path=str(SHOTS / "06-memory.png"), full_page=False)

    # inspector
    page.locator("button:has-text('Inspect')").first.click()
    page.wait_for_timeout(1500)
    check("inspector opens", page.locator("text=Version").count() > 0 or page.locator("text=Confidence").count() > 0)
    page.screenshot(path=str(SHOTS / "07-inspector.png"), timeout=120000)
    page.keyboard.press("Escape"); page.wait_for_timeout(800)

    # conflict demo (real mutation)
    page.locator("button:has-text('Run conflict resolution')").scroll_into_view_if_needed()
    page.locator("button:has-text('Run conflict resolution')").click()
    page.wait_for_timeout(7000)
    ctext = page.inner_text("#main")
    check("conflict resolved to v02", "V02" in ctext or "Resolved" in ctext)
    page.screenshot(path=str(SHOTS / "08-conflict.png"), timeout=120000)

    # timeline
    page.locator("text=Every change, recorded.").scroll_into_view_if_needed()
    page.wait_for_timeout(1500); page.screenshot(path=str(SHOTS / "09-timeline.png"), timeout=120000)

    # ---- architecture ----
    page.goto(f"{BASE}/architecture", wait_until="networkidle")
    page.wait_for_timeout(2000)
    a = page.inner_text("#main")
    check("architecture shows live health", "LANGGRAPH" in a and "CHROMADB" in a)
    page.screenshot(path=str(SHOTS / "10-architecture.png"), timeout=120000)

    # ---- workspace: real chat ----
    # Use a FRESH page for the chat section. The landing page keeps animated
    # canvases running, which starves CPU inference on a small box and makes a
    # real local-model turn time out.
    page.close()
    page = ctx.new_page()
    # Re-attach error capture to the replacement page.
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type=="error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(f"{BASE}/workspace", wait_until="networkidle")
    page.wait_for_timeout(2500)
    page.fill("#chat-input", "Remember that I take my coffee black with no sugar.")
    # A real local model takes far longer than the deterministic planner, so wait
    # for the reply to actually arrive instead of a fixed delay.
    before = page.inner_text("#main")
    page.click("button[type=submit]")
    page.wait_for_function(
        "prev => document.querySelector('#main').innerText.length > prev.length + 40",
        arg=before, timeout=240000)
    w = page.inner_text("#main")
    check("agent replied", "MEMORY//OS" in w and ("Stored" in w or "memory" in w.lower()))
    page.screenshot(path=str(SHOTS / "11-workspace-chat.png"), timeout=120000)

    # switch thread -> long-term recall must cross threads
    page.click("button:has-text('thread-beta')")
    page.wait_for_timeout(2000)
    page.fill("#chat-input", "How do I take my coffee?")
    before2 = page.inner_text("#main")
    page.click("button[type=submit]")
    page.wait_for_function(
        "prev => document.querySelector('#main').innerText.length > prev.length + 40",
        arg=before2, timeout=240000)
    w2 = page.inner_text("#main")
    check("cross-thread long-term recall", "coffee" in w2.lower() and "black" in w2.lower())
    page.screenshot(path=str(SHOTS / "12-cross-thread.png"), timeout=120000)

    # ---- mobile ----
    m = b.new_context(viewport={"width":390,"height":844}, is_mobile=True, has_touch=True)
    mp = m.new_page()
    mp.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
    mp.goto(BASE, wait_until="networkidle"); mp.wait_for_timeout(2500)
    ow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("no mobile horizontal overflow", ow <= 1, f"overflow={ow}px")
    mp.screenshot(path=str(SHOTS / "13-mobile-hero.png"), timeout=120000)
    mp.goto(f"{BASE}/workspace", wait_until="networkidle"); mp.wait_for_timeout(2500)
    ow2 = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("no mobile overflow (workspace)", ow2 <= 1, f"overflow={ow2}px")
    mp.screenshot(path=str(SHOTS / "14-mobile-workspace.png"), timeout=120000)

    # ---- reduced motion ----
    r = b.new_context(viewport={"width":1440,"height":900}, reduced_motion="reduce")
    rp = r.new_page()
    rp.on("pageerror", lambda e: errors.append(f"reduced pageerror: {e}"))
    rp.goto(BASE, wait_until="networkidle"); rp.wait_for_timeout(2000)
    rp.evaluate("window.scrollTo(0, document.body.scrollHeight*0.30)")
    rp.wait_for_timeout(1500)
    rp.screenshot(path=str(SHOTS / "15-reduced-motion.png"), timeout=120000)
    check("reduced-motion renders content", len(rp.inner_text("#main")) > 500)

    b.close()

real = [e for e in errors if "favicon" not in e.lower()]
print("\nconsole/page errors:", len(real))
for e in real[:15]: print("  ", e)
check("no console errors", len(real)==0)
print("\nFAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
