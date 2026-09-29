#!/usr/bin/env python3
"""Z-UI.1 (refinement) browser QA.

Covers: desktop + mobile routes, console errors, truthful states, provider
truth regression (REAL AGENT only for mode === "REAL AGENT"; unreachable →
UNKNOWN, never defaulted), COMPLETED ≠ VERIFIED token regression, LOCAL
no-pulse, metadata badges vs state tokens, the intelligence rail, a real
end-to-end chat turn with a durable memory assertion, and accessibility
basics. Evidence → docs/zorq/qa/zui1-browser-evidence/.
"""
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
OUT = Path("/home/user/repo/docs/zorq/qa/zui1-browser-evidence")
OUT.mkdir(parents=True, exist_ok=True)

ROUTES = ["/", "/workspace", "/memory", "/actions", "/capabilities",
          "/audit", "/devices", "/system", "/observatory"]

MUST_CONTAIN = {
    "/": ["ZORQ", "SYSTEM INTELLIGENCE", "LOCAL", "MEMORY//OS", "OPEN THE WORKSPACE",
          "DETERMINISTIC FALLBACK", "INTELLIGENCE", "ACTION GATE"],
    "/workspace": ["Workspace", "ASK ZORQ", "OBSERVE", "UNDERSTAND", "ANALYZE", "DECIDE",
                   "PROPOSE", "AUTHORIZE", "VERIFY", "INTELLIGENCE", "ACTION GATE",
                   "MODEL CONFIDENCE ≠ AUTHORIZATION"],
    "/memory": ["MEMORY & CONTEXT", "CANONICAL", "MEMORY RETRIEVAL ≠ MEMORY GOVERNANCE"],
    "/actions": ["ACTIONS & VERIFICATION", "PROPOSED", "No action records", "EXECUTION ≠ VERIFIED OUTCOME"],
    "/capabilities": ["Capabilities", "VISIBLE", "AUTHORIZED", "EXECUTABLE", "Visibility is not permission"],
    "/audit": ["Audit trail", "VERIFIED", "chain"],
    "/devices": ["DEVICES & CONNECTIVITY", "NOT-CONFIGURED", "None enrolled", "DEVICE TRUST ≠ USER AUTHORIZATION"],
    "/system": ["System", "DETERMINISTIC FALLBACK", "Boundary", "Never faked"],
    "/observatory": ["ZORQ core", "Planes", "The system, inspectable"],
}

FORBIDDEN = ["restricted internal", "INTERNAL EXPANSION", "confidential expansion"]

results = {"console_errors": {}, "truth": {}, "forbidden": {}, "a11y": {},
           "mobile": {}, "regressions": {}, "functional": {}, "shots": []}

def shoot(page, name):
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    results["shots"].append(str(path))

with sync_playwright() as p:
    browser = p.chromium.launch()
    fail = []  # collected during passes; verdict computed at the end

    # ================= desktop pass =================
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    for route in ROUTES:
        errors.clear()
        page.goto(BASE + route, wait_until="networkidle")
        try:
            page.wait_for_selector(".z-readout, .z-condition, .z-state, .z-rail",
                                  state="attached", timeout=8000)
        except Exception:
            pass
        time.sleep(0.6)
        body = page.inner_text("body")

        results["console_errors"][route] = list(errors)
        results["truth"][route] = {
            req: (req in body or req in page.content()) for req in MUST_CONTAIN.get(route, [])
        }
        results["forbidden"][route] = [f for f in FORBIDDEN if f.lower() in body.lower()]
        shoot(page, "desktop" + route.replace("/", "-") or "desktop-home")
        print(f"desktop {route}: console_errors={len(errors)} "
              f"truth={sum(results['truth'][route].values())}/{len(results['truth'][route])}")

    # ---------- REGRESSION: provider truth (MUST-FIX 1) ----------
    # The verification backend runs MODEL_PROVIDER=demo → mode must be
    # DETERMINISTIC FALLBACK on every surface; REAL AGENT must never appear.
    page.goto(BASE + "/", wait_until="networkidle")
    time.sleep(1.2)
    for route in ["/", "/workspace", "/system", "/observatory"]:
        page.goto(BASE + route, wait_until="networkidle")
        time.sleep(0.8)
        body = page.inner_text("body")
        has_fallback = "DETERMINISTIC FALLBACK" in body
        has_real = bool(re.search(r"REAL AGENT", body))
        results["regressions"][f"provider_fallback_{route}"] = has_fallback
        results["regressions"][f"provider_never_real_{route}"] = not has_real
        if not has_fallback:
            fail.append(f"provider truth: DETERMINISTIC FALLBACK not shown on {route}")
        if has_real:
            fail.append(f"provider truth: REAL AGENT shown on {route} while backend is demo")

    # Unreachable provider → UNKNOWN, never REAL or DEMO (route interception)
    pg2 = ctx.new_page()
    pg2.route("**/api/health", lambda r: r.abort())
    pg2.route("**/api/zorq/status", lambda r: r.abort())
    pg2.goto(BASE + "/", wait_until="domcontentloaded")
    # Wait for the status strip to resolve past its loading placeholder
    # ("SYSTEM · · ·") — the assertion itself is unchanged: unreachable
    # provider must render UNKNOWN, never REAL or DEMO.
    try:
        pg2.wait_for_function(
            "() => [...document.querySelectorAll('header .z-meta')]"
            ".some(e => e.textContent && !e.textContent.includes('\u00b7 \u00b7 \u00b7'))",
            timeout=8000)
    except Exception:
        pass
    time.sleep(0.3)
    header_badges = pg2.eval_on_selector_all("header .z-meta", "els => els.map(e => e.textContent)")
    results["regressions"]["provider_unreachable_badges"] = header_badges
    joined = " ".join(header_badges).upper()
    if "UNKNOWN" not in joined:
        fail.append(f"provider truth: unreachable provider did not render UNKNOWN (got: {header_badges})")
    if "REAL" in joined.replace("PROVIDER · UNKNOWN", ""):
        fail.append(f"provider truth: unreachable provider rendered REAL (got: {header_badges})")
    if "DEMO" in joined:
        fail.append(f"provider truth: unreachable provider rendered DEMO (got: {header_badges})")
    pg2.close()

    # ---------- REGRESSION: COMPLETED ≠ VERIFIED (MUST-FIX 2) ----------
    page.goto(BASE + "/actions", wait_until="networkidle")
    try:
        page.wait_for_selector(".z-state", timeout=8000)
    except Exception:
        pass
    tones = page.evaluate("""() => {
      const out = {};
      for (const el of document.querySelectorAll('.z-state')) {
        const label = (el.textContent || '').trim().toUpperCase();
        if (label === 'COMPLETED' || label === 'VERIFIED') out[label] = el.getAttribute('data-tone');
      }
      return out;
    }""")
    results["regressions"]["completed_vs_verified_tones"] = tones
    if "COMPLETED" not in tones or "VERIFIED" not in tones:
        fail.append(f"COMPLETED/VERIFIED chips not found on /actions (got: {tones})")
    else:
        if tones["COMPLETED"] == tones["VERIFIED"]:
            fail.append(f"COMPLETED and VERIFIED share the same tone: {tones}")
        if tones["COMPLETED"] == "ok":
            fail.append("COMPLETED renders as affirmative ok tone (must not)")

    # ---------- REGRESSION: LOCAL must not pulse (MUST-FIX 5) ----------
    local_pulse = page.evaluate("""() => {
      return Array.from(document.querySelectorAll('.z-state'))
        .filter(el => (el.textContent || '').trim().toUpperCase().startsWith('LOCAL'))
        .map(el => el.getAttribute('data-pulse'));
    }""")
    results["regressions"]["local_pulse_attrs"] = local_pulse
    if any(v == "true" for v in local_pulse):
        fail.append("LOCAL token renders with pulse (must be steady)")

    # ---------- REGRESSION: metadata badges, not state tokens (MUST-FIX 4) ----------
    meta_checks = {}
    for route, label in [("/memory", "CANONICAL"), ("/actions", "READ-ONLY"), ("/capabilities", "SEALED")]:
        page.goto(BASE + route, wait_until="networkidle")
        time.sleep(0.6)
        found = page.evaluate("""(label) => {
          const asMeta = Array.from(document.querySelectorAll('.z-meta'))
            .some(e => (e.textContent || '').trim().toUpperCase().includes(label));
          const asState = Array.from(document.querySelectorAll('.z-state'))
            .some(e => (e.textContent || '').trim().toUpperCase() === label);
          return {asMeta, asState};
        }""", label)
        meta_checks[route] = found
        if not found["asMeta"] or found["asState"]:
            fail.append(f"metadata badge {label} on {route} not rendered as .z-meta: {found}")
    results["regressions"]["metadata_badges"] = meta_checks

    # ---------- REGRESSION: navigation IA groups (MUST-FIX 6) ----------
    page.goto(BASE + "/", wait_until="networkidle")
    groups = page.eval_on_selector_all(
        "nav[aria-label='Primary'] .z-nav-group", "els => els.map(e => e.textContent.trim())")
    results["regressions"]["nav_groups"] = groups
    if groups.count("System") > 1 or "Work" not in groups or "Control" not in groups:
        fail.append(f"navigation groups wrong or duplicated: {groups}")

    # ---------- a11y: focus, landmarks, headings ----------
    page.keyboard.press("Tab")
    results["a11y"]["first_tab_focus"] = page.evaluate(
        "document.activeElement && (document.activeElement.className + ' | ' + (document.activeElement.textContent||'').slice(0,40))")
    results["a11y"]["landmarks"] = page.evaluate(
        "['header','nav','main','footer'].map(t => document.querySelectorAll(t).length)")
    results["a11y"]["html_lang"] = page.evaluate("document.documentElement.lang")
    results["a11y"]["imgs_missing_alt"] = page.evaluate(
        "Array.from(document.images).filter(i => !i.alt).length")
    h1s = {}
    for route in ROUTES:
        page.goto(BASE + route, wait_until="networkidle")
        h1s[route] = page.evaluate("document.querySelectorAll('h1').length")
    results["a11y"]["h1_counts"] = h1s

    # desktop nav visibility
    page.goto(BASE + "/", wait_until="networkidle")
    results["a11y"]["desktop_nav_visible"] = page.is_visible("nav[aria-label='Primary'] .z-nav-link")
    menu_btn = page.query_selector("button[aria-label='Open menu']")
    results["a11y"]["desktop_menu_button_present"] = bool(menu_btn and menu_btn.is_visible())
    results["a11y"]["desktop_nav_links"] = page.eval_on_selector_all(
        "nav[aria-label='Primary'] a", "els => els.map(e => e.getAttribute('href'))")
    if not results["a11y"]["desktop_nav_visible"]:
        fail.append("desktop nav links not visible at 1440px")
    if results["a11y"]["desktop_menu_button_present"]:
        fail.append("mobile menu button visible on desktop")
    ctx.close()

    # ================= mobile pass =================
    mctx = browser.new_context(viewport={"width": 390, "height": 844},
                               is_mobile=True, has_touch=True,
                               user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    mpage = mctx.new_page()
    merrors = []
    mpage.on("console", lambda m: merrors.append(m.text) if m.type == "error" else None)
    mpage.on("pageerror", lambda e: merrors.append(str(e)))

    for route in ROUTES:
        merrors.clear()
        mpage.goto(BASE + route, wait_until="networkidle")
        try:
            mpage.wait_for_selector(".z-readout, .z-condition, .z-state, .z-rail",
                                    state="attached", timeout=8000)
        except Exception:
            pass
        time.sleep(0.4)
        overflow = mpage.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        results["mobile"][route] = {"h_overflow_px": overflow, "console_errors": list(merrors)}
        shoot(mpage, "mobile" + route.replace("/", "-") or "mobile-home")
        print(f"mobile {route}: overflow={overflow}px console_errors={len(merrors)}")

    # mobile: fixed header + content not hidden + touch targets
    mpage.goto(BASE + "/", wait_until="networkidle")
    time.sleep(0.8)
    header_fixed = mpage.evaluate(
        "getComputedStyle(document.querySelector('header')).position")
    content_clear = mpage.evaluate(
        """() => {
          const headerBottom = document.querySelector('header').getBoundingClientRect().bottom;
          const h1 = document.querySelector('main h1');
          return h1 ? h1.getBoundingClientRect().top >= headerBottom - 1 : false;
        }""")
    results["mobile"]["header_position"] = header_fixed
    results["mobile"]["content_below_header"] = content_clear
    if header_fixed != "fixed":
        fail.append(f"mobile header not fixed: {header_fixed}")
    if not content_clear:
        fail.append("mobile content starts under the fixed header")

    mpage.click("button[aria-label='Open menu']")
    time.sleep(0.4)
    results["mobile"]["nav_dialog_opens"] = mpage.is_visible("[role='dialog']")
    # touch targets: every dialog link ≥ 40px tall
    heights = mpage.eval_on_selector_all(
        "[role='dialog'] nav a", "els => els.map(e => Math.round(e.getBoundingClientRect().height))")
    results["mobile"]["nav_touch_target_heights"] = heights
    if any(h < 40 for h in heights):
        fail.append(f"mobile nav touch targets below 40px: {heights}")
    links = mpage.eval_on_selector_all("[role='dialog'] a", "els => els.map(e => e.getAttribute('href'))")
    results["mobile"]["nav_dialog_links"] = links
    shoot(mpage, "mobile-nav-dialog")
    mpage.click("button[aria-label='Close menu']")
    if not results["mobile"]["nav_dialog_opens"]:
        fail.append("mobile nav dialog did not open")

    # mobile: workspace input reachable + rail visible + keyboard opens
    mpage.goto(BASE + "/workspace", wait_until="networkidle")
    try:
        mpage.wait_for_selector("#chat-input", timeout=8000)
    except Exception:
        fail.append("mobile chat input not found")
    input_visible = mpage.is_visible("#chat-input")
    input_box = mpage.eval_on_selector("#chat-input", "e => { const r = e.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height)}; }")
    results["mobile"]["chat_input"] = {"visible": input_visible, **input_box}
    if not input_visible or input_box["h"] < 36:
        fail.append(f"mobile chat input problem: {results['mobile']['chat_input']}")
    rail_visible = mpage.is_visible(".z-rail")
    results["mobile"]["rail_visible"] = rail_visible
    if not rail_visible:
        fail.append("mobile intelligence rail not visible")

    resp = mpage.goto(BASE + "/architecture", wait_until="domcontentloaded")
    results["mobile"]["architecture_redirect"] = mpage.url
    if not mpage.url.endswith("/system"):
        fail.append(f"architecture redirect wrong: {mpage.url}")
    mctx.close()

    # ================= functional pass: real chat turn (MUST-FIX 8) =================
    fctx = browser.new_context(viewport={"width": 1440, "height": 900})
    fpage = fctx.new_page()
    ferrors = []
    fpage.on("console", lambda m: ferrors.append(m.text) if m.type == "error" else None)
    fpage.on("pageerror", lambda e: ferrors.append(str(e)))

    fpage.goto(BASE + "/workspace", wait_until="networkidle")
    # 2. locate the ACTUAL chat input
    fpage.wait_for_selector("#chat-input", state="visible", timeout=8000)
    # 3. enter a test message (intentionally durable: "I prefer …" → PREFERENCE)
    durable_msg = "I prefer ZORQ refinement-pass browser verification over assumptions."
    fpage.fill("#chat-input", durable_msg)
    # 4. activate the ACTUAL send control (submit button inside the chat form)
    send = fpage.locator("form button[type='submit']")
    if send.count() == 0:
        send = fpage.get_by_role("button", name="Send")
    send.first.click()
    # 5. wait for the assistant response (ZORQ-labeled turn with stored-memory reply)
    fpage.wait_for_selector("text=Stored that as a", timeout=20000)
    time.sleep(1.5)
    body = fpage.inner_text("body")
    # 6. assert the response is visible
    results["functional"]["response_visible"] = "Stored that as a" in body
    # 7. verify real backend interaction: the turn reported activity + rail evidence
    def rail_states_of(pg):
        return pg.evaluate("""() => {
          const out = {};
          for (const el of document.querySelectorAll('.z-rail-stage')) {
            out[el.querySelector('.z-rail-label').textContent.trim()] = el.getAttribute('data-state');
          }
          return out;
        }""")
    rail_states = rail_states_of(fpage)
    results["functional"]["rail_after_durable_turn"] = rail_states
    turn_activity_types = fpage.evaluate(
        "() => Array.from(document.querySelectorAll('details summary')).length > 0"
        " ? 'activity-details-present' : 'none'")
    # assistant identity is ZORQ, not MEMORY//OS
    zlabels = fpage.eval_on_selector_all(
        "article p.label", "els => els.map(e => e.textContent.trim())")
    results["functional"]["turn_labels"] = zlabels
    if "ZORQ" not in zlabels:
        fail.append(f"assistant turn not labeled ZORQ: {zlabels}")
    if "MEMORY//OS" in zlabels:
        fail.append(f"assistant turn still labeled MEMORY//OS: {zlabels}")

    # SEMANTIC REGRESSION (final refinement): a durable-memory turn contains
    # MODEL_CALL/DEMO_PLANNER + SAVE_MEMORY (+ MEMORY_MANAGER on some paths).
    # Cognition and memory writes must evidence intelligence stages only —
    # PROPOSE/AUTHORIZE/ACT/VERIFY must stay DORMANT (a memory save is a
    # memory operation, not an external action proposal).
    for stage in ["OBSERVE", "UNDERSTAND", "ANALYZE"]:
        if rail_states.get(stage) != "evidenced":
            fail.append(f"[semantics] {stage} not evidenced after durable-memory turn: {rail_states}")
    for stage in ["PROPOSE", "AUTHORIZE", "ACT", "VERIFY"]:
        if rail_states.get(stage) != "dormant":
            fail.append(f"[semantics] {stage} evidenced by a memory-save turn (must stay dormant): {rail_states}")
    results["functional"]["semantic_save_memory_no_propose"] = rail_states.get("PROPOSE") == "dormant"
    results["functional"]["semantic_model_call_no_propose"] = rail_states.get("PROPOSE") == "dormant"
    # rail group labels present
    group_labels = fpage.eval_on_selector_all(
        ".z-rail-group", "els => els.map(e => e.textContent.trim())")
    results["functional"]["rail_groups"] = group_labels
    if group_labels != ["INTELLIGENCE", "ACTION GATE"]:
        fail.append(f"[semantics] rail groups wrong: {group_labels}")
    if not results["functional"]["response_visible"]:
        fail.append("chat response not visible after send")
    shoot(fpage, "functional-workspace-chat")

    # ORDINARY TURN (non-durable): a plain response must not evidence DECIDE
    # (no explicit decision event) nor any action stage.
    articles_before = fpage.eval_on_selector_all("article", "els => els.length")
    fpage.fill("#chat-input", "hello there, plain ordinary turn")
    fpage.locator("form button[type='submit']").first.click()
    try:
        fpage.wait_for_function(
            "(n) => document.querySelectorAll('article').length >= n + 2",
            arg=articles_before, timeout=20000)
    except Exception:
        fail.append("ordinary turn: assistant response did not render")
    time.sleep(2.5)
    rail_plain = rail_states_of(fpage)
    results["functional"]["rail_after_ordinary_turn"] = rail_plain
    if rail_plain.get("PROPOSE") != "dormant":
        fail.append(f"[semantics] ordinary text response evidenced PROPOSE: {rail_plain}")
    if rail_plain.get("DECIDE") != "dormant":
        fail.append(f"[semantics] DECIDE inferred from ordinary response generation: {rail_plain}")
    for stage in ["AUTHORIZE", "ACT", "VERIFY"]:
        if rail_plain.get(stage) != "dormant":
            fail.append(f"[semantics] {stage} active on ordinary turn: {rail_plain}")

    # CONVERSATIONAL SURFACE (11th directive): a normal query must not narrate
    # recall/demo boilerplate, and without a provider it must say so honestly.
    ordinary_reply = (fpage.eval_on_selector_all(
        "article", "els => els[els.length - 1].innerText") or "")
    results["functional"]["ordinary_reply_excerpt"] = ordinary_reply[:220]
    for banned in ["Using what I remember", "LOCAL DEMO mode", "Applied preference"]:
        if banned.lower() in ordinary_reply.lower():
            fail.append(f"[surface] ordinary reply narrates boilerplate: {banned!r}")
    if "not guess" not in ordinary_reply.lower() and "will not guess" not in ordinary_reply.lower():
        fail.append("[surface] ordinary reply lacks truthful no-provider refusal")
    shoot(fpage, "functional-workspace-ordinary-turn")

    # EXPLICIT MEMORY QUERY: memory surfaces only when asked for.
    articles_before = fpage.eval_on_selector_all("article", "els => els.length")
    fpage.fill("#chat-input", "what do you remember about ZORQ verification")
    fpage.locator("form button[type='submit']").first.click()
    try:
        fpage.wait_for_function(
            "(n) => document.querySelectorAll('article').length >= n + 2",
            arg=articles_before, timeout=20000)
    except Exception:
        fail.append("memory query turn: assistant response did not render")
    time.sleep(2.0)
    memory_reply = (fpage.eval_on_selector_all(
        "article", "els => els[els.length - 1].innerText") or "")
    results["functional"]["memory_query_reply_excerpt"] = memory_reply[:220]
    if "Here is what I remember" not in memory_reply:
        fail.append("[surface] explicit memory query did not surface memory")
    shoot(fpage, "functional-workspace-memory-query")

    # ACTION-PROPOSAL FIXTURE (test-only route interception): if the backend
    # ever emits ACTION_PROPOSED, PROPOSE must evidence — and nothing else.
    ffix = fctx.new_page()
    ferr2 = []
    ffix.on("console", lambda m: ferr2.append(m.text) if m.type == "error" else None)
    ffix.on("pageerror", lambda e: ferr2.append(str(e)))
    fixture = {
        "answer": "FIXTURE: action proposal recorded.",
        "recalled": [], "activity": [
            {"type": "LOAD_CONTEXT"}, {"type": "MEMORY_PRELOAD"},
            {"type": "MODEL_CALL"}, {"type": "TOOL_DECISION", "tool": "x"},
            {"type": "ACTION_PROPOSED", "action_id": "fixture-1"},
        ], "cognition": None, "surface": None,
    }
    def serve_fixture(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(fixture))
    ffix.route("**/api/chat", serve_fixture)
    ffix.goto(BASE + "/workspace", wait_until="networkidle")
    ffix.wait_for_selector("#chat-input", timeout=8000)
    ffix.fill("#chat-input", "fixture turn")
    ffix.locator("form button[type='submit']").first.click()
    ffix.wait_for_selector("text=FIXTURE: action proposal recorded.", timeout=10000)
    time.sleep(1.0)
    rail_fix = rail_states_of(ffix)
    results["functional"]["rail_after_action_proposal_fixture"] = rail_fix
    if rail_fix.get("PROPOSE") != "evidenced":
        fail.append(f"[semantics] explicit ACTION_PROPOSED fixture did not evidence PROPOSE: {rail_fix}")
    for stage in ["AUTHORIZE", "ACT", "VERIFY"]:
        if rail_fix.get(stage) != "dormant":
            fail.append(f"[semantics] {stage} evidenced by proposal fixture (only PROPOSE may): {rail_fix}")
    results["functional"]["fixture_console_errors"] = list(ferr2)
    if ferr2:
        fail.append(f"console errors during fixture turn: {ferr2[:3]}")
    # COGNITION-ONLY FIXTURE: model call + memory save + memory manager —
    # none of these are action-proposal evidence. PROPOSE must stay dormant.
    fixture2 = {
        "answer": "FIXTURE 2: cognition and a memory write, no action proposal.",
        "recalled": [], "activity": [
            {"type": "LOAD_CONTEXT"}, {"type": "MEMORY_PRELOAD"},
            {"type": "MODEL_CALL"}, {"type": "MODEL_REVISION"},
            {"type": "SAVE_MEMORY"}, {"type": "MEMORY_MANAGER"},
        ], "cognition": None, "surface": None,
    }
    def serve_fixture2(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(fixture2))
    ffix.route("**/api/chat", serve_fixture2)
    ffix.reload(wait_until="networkidle")
    ffix.wait_for_selector("#chat-input", timeout=8000)
    ffix.fill("#chat-input", "fixture cognition-only turn")
    ffix.locator("form button[type='submit']").first.click()
    ffix.wait_for_selector("text=FIXTURE 2", timeout=10000)
    time.sleep(1.0)
    rail_fix2 = rail_states_of(ffix)
    results["functional"]["rail_after_cognition_only_fixture"] = rail_fix2
    if rail_fix2.get("PROPOSE") != "dormant":
        fail.append(f"[semantics] MODEL_CALL/SAVE_MEMORY/MEMORY_MANAGER evidenced PROPOSE (must stay dormant): {rail_fix2}")
    for stage in ["AUTHORIZE", "ACT", "VERIFY"]:
        if rail_fix2.get(stage) != "dormant":
            fail.append(f"[semantics] {stage} evidenced by cognition-only fixture: {rail_fix2}")
    shoot(ffix, "functional-workspace-cognition-only-fixture")
    ffix.close()

    # 9. zero console errors
    results["functional"]["chat_turn_console_errors"] = list(ferrors)
    if ferrors:
        fail.append(f"console errors during chat turn: {ferrors[:3]}")
    # 8. verify the memory result where the message is durable
    fpage.goto(BASE + "/memory", wait_until="networkidle")
    try:
        fpage.wait_for_selector("text=refinement-pass browser verification", timeout=8000)
        mem_ok = True
    except Exception:
        mem_ok = False
    results["functional"]["durable_memory_visible"] = mem_ok
    if not mem_ok:
        fail.append("durable message not visible on /memory after chat turn")
    shoot(fpage, "functional-memory-after-chat")
    fctx.close()
    browser.close()

(OUT / "qa-results.json").write_text(json.dumps(results, indent=2))

# ================= verdict =================
for route, errs in results["console_errors"].items():
    if errs:
        fail.append(f"console errors on {route}: {errs[:3]}")
for route, checks in results["truth"].items():
    for req, ok in checks.items():
        if not ok:
            fail.append(f"truth-missing on {route}: {req!r}")
for route, found in results["forbidden"].items():
    if found:
        fail.append(f"forbidden text on {route}: {found}")
for route, m in results["mobile"].items():
    if isinstance(m, dict) and m.get("h_overflow_px", 0) > 2:
        fail.append(f"mobile h-overflow on {route}: {m['h_overflow_px']}px")
    if isinstance(m, dict) and m.get("console_errors"):
        fail.append(f"mobile console errors on {route}: {m['console_errors'][:2]}")
for route, n in results["a11y"]["h1_counts"].items():
    if n != 1:
        fail.append(f"h1 count on {route}: {n} (expected 1)")
if results["a11y"]["html_lang"] != "en":
    fail.append("html lang missing")
if results["a11y"]["imgs_missing_alt"] > 0:
    fail.append(f"images missing alt: {results['a11y']['imgs_missing_alt']}")

print("\n=== VERDICT:", "PASS" if not fail else "FAIL", "===")
for f in fail:
    print("  FAIL:", f)
sys.exit(0 if not fail else 1)
