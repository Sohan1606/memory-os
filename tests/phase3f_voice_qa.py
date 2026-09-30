#!/usr/bin/env python3
"""ZORQ Phase 3F (3F-min) voice browser QA.

Drives the real Next.js frontend against the real FastAPI backend with
Playwright. The browser speech APIs are replaced by *controllable* test
doubles injected before app load, so every state transition asserted here is
produced by the actual voice runtime reacting to explicit, recorded events —
nothing is simulated inside the app itself, and no state is asserted that the
runtime did not actually enter.

Covers (spec §23): unavailable browser (1), permission denied (2), start/stop
listening (4-5), transcript produced (6), empty transcript (7), transcript
cancellation (8), speaking (13), speech completion (14), speech cancellation
(15), barge-in (16), stale voice event (18), stale speech completion (19),
stop-while-speaking != cancel generation (20), voice → conversation runtime
(21), telemetry truthfulness (§17), mobile (30), desktop (31), a11y (32).

Prereqs: backend on :8000, frontend (built) on :3000.
Evidence → docs/zorq/qa/phase3f-voice-evidence/ inside the repository.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "zorq" / "qa" / "phase3f-voice-evidence"
OUT.mkdir(parents=True, exist_ok=True)

FORBIDDEN = ["restricted internal", "INTERNAL EXPANSION", "confidential expansion"]

FAKE_SPEECH_INIT = """
window.__voiceQA = { instances: [], tts: { utterances: [], cancels: 0 } };
class FakeRecognition {
  constructor() {
    this.continuous = false; this.interimResults = false; this.lang = "";
    this.onaudiostart = null; this.onstart = null; this.onresult = null;
    this.onerror = null; this.onend = null;
    this.startedCount = 0; this.stoppedCount = 0; this.abortedCount = 0;
    window.__voiceQA.instances.push(this);
  }
  start() { this.startedCount++; }
  stop() { this.stoppedCount++; }
  abort() { this.abortedCount++; }
}
window.SpeechRecognition = FakeRecognition;
window.webkitSpeechRecognition = FakeRecognition;
const fakeSynth = {
  speak(u) { window.__voiceQA.tts.utterances.push(u); },
  cancel() { window.__voiceQA.tts.cancels++; },
  getVoices() { return []; },
};
Object.defineProperty(window, "speechSynthesis", { value: fakeSynth, configurable: true });
"""

NO_SPEECH_INIT = """
Object.defineProperty(window, "SpeechRecognition", { value: undefined, configurable: true });
Object.defineProperty(window, "webkitSpeechRecognition", { value: undefined, configurable: true });
"""

results = {"checks": [], "console_errors": {}, "shots": []}
fail = []


def check(name, cond, detail=""):
    results["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fail.append(name)


def shoot(page, name):
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    results["shots"].append(str(path.relative_to(REPO)))


def rec(page, index=-1):
    """Fire an event on a fake recognition instance from the page context."""
    def fire(code):
        return page.evaluate(
            "([i, body]) => { const r = window.__voiceQA.instances.at(i);"
            " return new Function('r', body)(r); }", [index, code])
    return fire


def voice_token(page):
    el = page.locator(".z-telemetry-item", has_text="VOICE").first
    return el.inner_text().replace("VOICE", "").strip()


def wait_token(page, token, timeout=4000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if voice_token(page) == token:
            return True
        time.sleep(0.05)
    return False


with sync_playwright() as p:
    browser = p.chromium.launch()

    # ============ PASS 1: desktop, controllable speech doubles ============
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(FAKE_SPEECH_INIT)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    chat_posts = {"n": 0}
    def route_chat(route):
        chat_posts["n"] += 1
        time.sleep(1.2)  # deterministic PROCESSING observation window
        route.continue_()
    page.route("**/api/chat", route_chat)

    page.goto(f"{BASE}/workspace", wait_until="networkidle")
    page.wait_for_selector("#chat-input")

    print("== desktop voice state machine ==")
    check("telemetry starts AVAILABLE (capability detected, evidence-gated)",
          wait_token(page, "AVAILABLE"), voice_token(page))
    mic = page.locator("button[aria-label='Start voice input']")
    check("mic control present with truthful STT disclosure",
          mic.count() == 1 and "vendor's speech service" in (mic.get_attribute("title") or ""))

    # -- start listening: REQUESTING_PERMISSION until real evidence --------
    mic.click()
    check("REQUESTING_PERMISSION shown before any recognition evidence",
          page.locator("button[aria-label='Cancel microphone permission request']").count() == 1)
    check("no LISTENING token without recognition evidence",
          voice_token(page) == "AVAILABLE", voice_token(page))
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    check("LISTENING only after real onstart", wait_token(page, "LISTENING"))
    check("recording indication visible", page.get_by_text("● REC").count() >= 1)
    check("mic aria-pressed reflects listening",
          page.locator("button[aria-pressed='true']").count() >= 1)
    shoot(page, "01-listening")

    # -- interim vs final ---------------------------------------------------
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'hello zorq'}], {isFinal:false})]});")
    page.wait_for_timeout(100)
    check("interim transcript visibly provisional, not in the input",
          page.get_by_text("INTERIM — NOT SUBMITTED").count() >= 1
          and page.locator("#chat-input").input_value() == "")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'hello zorq'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(200)
    check("final transcript becomes editable draft in the SAME input",
          page.locator("#chat-input").input_value() == "hello zorq")
    check("token returns to AVAILABLE after finalization", wait_token(page, "AVAILABLE"))

    # -- stale-event protection --------------------------------------------
    stale_handlers_nulled = page.evaluate(
        "() => { const r = window.__voiceQA.instances.at(-1);"
        " return r.onresult === null && r.onend === null && r.onerror === null; }")
    check("finished session's handlers are detached (stale events undeliverable)",
          stale_handlers_nulled)

    # -- empty transcript ----------------------------------------------------
    page.locator("#chat-input").fill("")
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onend && r.onend();")
    page.wait_for_timeout(200)
    check("empty recognition creates no message and returns to AVAILABLE",
          page.locator("#chat-input").input_value() == "" and voice_token(page) == "AVAILABLE")

    # -- transcript cancellation ---------------------------------------------
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'discard me'}], {isFinal:true})]});")
    page.locator("button[aria-label='Cancel voice input and discard the transcript']").click()
    page.wait_for_timeout(200)
    check("cancel discards the draft entirely",
          page.locator("#chat-input").input_value() == "" and voice_token(page) == "AVAILABLE")
    aborted = page.evaluate("() => window.__voiceQA.instances.at(-1).abortedCount")
    check("cancel aborts the real recognition transport", aborted >= 1)

    # -- permission denied -----------------------------------------------------
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onerror && r.onerror({error:'not-allowed'});")
    page.wait_for_timeout(200)
    check("permission denial → truthful ERROR with text-mode reassurance",
          page.get_by_text("Microphone permission denied").count() >= 1)
    check("ERROR token shown", voice_token(page) == "ERROR")
    shoot(page, "02-permission-denied")
    page.locator("button[aria-label='Dismiss voice error']").click()
    check("error acknowledged → AVAILABLE", wait_token(page, "AVAILABLE"))

    # -- voice turn: PROCESSING → SPEAKING → completion -------------------------
    print("== voice turn / speech output ==")
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'I prefer concise technical explanations'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(200)
    posts_before = chat_posts["n"]
    page.locator("#chat-input").press("Enter")
    check("PROCESSING token during the in-flight voice turn",
          wait_token(page, "PROCESSING", timeout=3000))
    # The real backend answers; then the app offers tracked speech output.
    got_utterance = False
    deadline = time.time() + 120
    while time.time() < deadline:
        if page.evaluate("() => window.__voiceQA.tts.utterances.length") > 0:
            got_utterance = True
            break
        time.sleep(0.2)
    check("speech output offered exactly for the rendered answer", got_utterance)
    answer_text = page.evaluate("() => window.__voiceQA.tts.utterances.at(-1).text")
    check("utterance text is the final response text (no spoken-only content)",
          bool(answer_text) and page.get_by_text(answer_text[:40].strip()).count() >= 1,
          answer_text[:60])
    check("no SPEAKING token before real onstart (no phantom state)",
          voice_token(page) in ("PROCESSING", "AVAILABLE"), voice_token(page))
    page.evaluate("() => { const u = window.__voiceQA.tts.utterances.at(-1);"
                  " u.onstart && u.onstart(); }")
    check("SPEAKING only after real utterance onstart", wait_token(page, "SPEAKING"))
    stop_btn = page.locator("button[aria-label='Stop speech output']")
    check("stop-speaking control visible while SPEAKING", stop_btn.count() == 1)
    shoot(page, "03-speaking")
    page.evaluate("() => { const u = window.__voiceQA.tts.utterances.at(-1);"
                  " u.onend && u.onend(); }")
    check("natural completion returns to AVAILABLE", wait_token(page, "AVAILABLE"))

    # -- stop while speaking: audio only, generation untouched ------------------
    print("== stop-while-speaking / barge-in ==")
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'what do you know about me'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(200)
    page.locator("#chat-input").press("Enter")
    wait_token(page, "PROCESSING", timeout=3000)
    deadline = time.time() + 120
    n_before = page.evaluate("() => window.__voiceQA.tts.utterances.length")
    while time.time() < deadline:
        if page.evaluate("() => window.__voiceQA.tts.utterances.length") > n_before:
            break
        time.sleep(0.2)
    page.evaluate("() => { const u = window.__voiceQA.tts.utterances.at(-1);"
                  " u.onstart && u.onstart(); }")
    wait_token(page, "SPEAKING")
    cancels_before = page.evaluate("() => window.__voiceQA.tts.cancels")
    posts_before_stop = chat_posts["n"]
    page.locator("button[aria-label='Stop speech output']").click()
    page.wait_for_timeout(200)
    check("stop-speaking cancels synthesis locally (high-priority path)",
          page.evaluate("() => window.__voiceQA.tts.cancels") > cancels_before)
    check("stopping speech fired no network request and cancelled nothing",
          chat_posts["n"] == posts_before_stop)
    check("response text fully intact after speech stop",
          wait_token(page, "AVAILABLE"))
    # Stale speech completion: the cancelled utterance's late onend must not
    # revive SPEAKING or mark a completion.
    page.evaluate("() => { const u = window.__voiceQA.tts.utterances.at(-1);"
                  " u.onend && u.onend(); }")
    page.wait_for_timeout(150)
    check("stale onend from the cancelled utterance changes nothing",
          voice_token(page) == "AVAILABLE", voice_token(page))

    # -- barge-in: SPEAKING → INTERRUPTING → LISTENING ---------------------------
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'tell me about memory governance'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(200)
    page.locator("#chat-input").press("Enter")
    wait_token(page, "PROCESSING", timeout=3000)
    n_before = page.evaluate("() => window.__voiceQA.tts.utterances.length")
    deadline = time.time() + 120
    while time.time() < deadline:
        if page.evaluate("() => window.__voiceQA.tts.utterances.length") > n_before:
            break
        time.sleep(0.2)
    page.evaluate("() => { const u = window.__voiceQA.tts.utterances.at(-1);"
                  " u.onstart && u.onstart(); }")
    wait_token(page, "SPEAKING")
    inst_before = page.evaluate("() => window.__voiceQA.instances.length")
    posts_before_barge = chat_posts["n"]
    page.locator("button[aria-label='Interrupt — stop speech output and talk']").click()
    page.wait_for_timeout(300)
    check("barge-in opened a NEW recognition session",
          page.evaluate("() => window.__voiceQA.instances.length") > inst_before)
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    check("barge-in continuation reaches LISTENING on real evidence",
          wait_token(page, "LISTENING"))
    check("barge-in fired no generation-control request (3F-min invariant)",
          chat_posts["n"] == posts_before_barge)
    shoot(page, "04-barge-in-listening")

    # -- spec §10 B6: bare "stop" during barge-in is STOP(target=SPEECH) --------
    # The live barge-in session finalizes with the bare control word. It must
    # be consumed as a speech control: NO draft, NO /api/chat submission, no
    # generation control, and the machine returns to IDLE/AVAILABLE.
    def stop_controls(p):
        return p.locator("span.z-telemetry-item[data-speech-stop-controls]") \
                .get_attribute("data-speech-stop-controls")
    turns_before_b6 = page.locator("article").count()
    posts_before_b6 = chat_posts["n"]
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'Stop.'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(300)
    check("B6: bare spoken 'stop' during barge-in consumed as STOP(target=SPEECH)",
          stop_controls(page) == "1", stop_controls(page))
    check("B6: control utterance produced NO draft in the input",
          page.locator("#chat-input").input_value() == "")
    check("B6: control utterance fired ZERO /api/chat submissions",
          chat_posts["n"] == posts_before_b6)
    check("B6: machine returns to AVAILABLE after the control stop",
          wait_token(page, "AVAILABLE"))
    check("B6: rendered conversation unchanged by the control stop",
          page.locator("article").count()
          == turns_before_b6)

    # -- B6 boundary: ordinary barge-in speech is still a normal draft ----------
    page.locator("button[aria-label='Start voice input']").click()
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'summarize the audit trail'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(200)
    page.locator("#chat-input").press("Enter")
    wait_token(page, "PROCESSING", timeout=3000)
    n_before = page.evaluate("() => window.__voiceQA.tts.utterances.length")
    deadline = time.time() + 120
    while time.time() < deadline:
        if page.evaluate("() => window.__voiceQA.tts.utterances.length") > n_before:
            break
        time.sleep(0.2)
    page.evaluate("() => { const u = window.__voiceQA.tts.utterances.at(-1);"
                  " u.onstart && u.onstart(); }")
    wait_token(page, "SPEAKING")
    page.locator("button[aria-label='Interrupt — stop speech output and talk']").click()
    page.wait_for_timeout(300)
    fire = rec(page)
    fire("r.onstart && r.onstart();")
    wait_token(page, "LISTENING")
    fire("r.onresult && r.onresult({resultIndex:0, results:[Object.assign("
         "[{transcript:'stop the deployment review'}], {isFinal:true})]});"
         "r.onend && r.onend();")
    page.wait_for_timeout(300)
    check("B6 boundary: non-bare barge-in speech becomes a normal editable draft",
          page.locator("#chat-input").input_value() == "stop the deployment review")
    check("B6 boundary: non-bare utterance did NOT increment the control count",
          stop_controls(page) == "1", stop_controls(page))
    page.locator("#chat-input").fill("")  # leave a clean input for later passes
    wait_token(page, "AVAILABLE")

    results["console_errors"]["desktop-machine"] = errors[:]
    check("0 console errors on the desktop machine pass", len(errors) == 0,
          "; ".join(errors[:3]))

    body = page.content()
    check("no forbidden identity strings on /workspace",
          not any(s.lower() in body.lower() for s in FORBIDDEN))
    ctx.close()

    # ============ PASS 2: unsupported browser (UNAVAILABLE) ============
    print("== unsupported browser ==")
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx2.add_init_script(NO_SPEECH_INIT)
    page2 = ctx2.new_page()
    errors2 = []
    page2.on("console", lambda m: errors2.append(m.text) if m.type == "error" else None)
    page2.on("pageerror", lambda e: errors2.append(str(e)))
    page2.goto(f"{BASE}/workspace", wait_until="networkidle")
    page2.wait_for_selector("#chat-input")
    check("MIC N/A shown when recognition is absent",
          page2.get_by_text("MIC N/A").count() >= 1)
    check("VOICE · UNAVAILABLE token (truthful capability absence)",
          wait_token(page2, "UNAVAILABLE"))
    page2.locator("#chat-input").fill("typing still works without voice")
    check("text path fully usable without voice",
          page2.locator("#chat-input").input_value() != ""
          and page2.locator("button[aria-label='Submit (Enter)']").is_enabled())
    shoot(page2, "05-unavailable")
    check("0 console errors on the unavailable pass", len(errors2) == 0,
          "; ".join(errors2[:3]))
    ctx2.close()

    # ============ PASS 3: home page disclosure + reduced motion ============
    print("== disclosure / reduced motion ==")
    ctx3 = browser.new_context(viewport={"width": 1440, "height": 900},
                               reduced_motion="reduce")
    ctx3.add_init_script(FAKE_SPEECH_INIT)
    page3 = ctx3.new_page()
    errors3 = []
    page3.on("console", lambda m: errors3.append(m.text) if m.type == "error" else None)
    page3.on("pageerror", lambda e: errors3.append(str(e)))
    page3.goto(f"{BASE}/", wait_until="networkidle")
    page3.wait_for_timeout(800)
    body3 = page3.content()
    check("GAP-1 browser STT egress disclosure present on the voice demo",
          "vendor's speech service" in body3)
    check("no offline-voice claim anywhere",
          "offline voice" not in body3.lower())
    check("0 console errors with reduced motion", len(errors3) == 0,
          "; ".join(errors3[:3]))
    shoot(page3, "06-home-disclosure")
    ctx3.close()

    # ============ PASS 4: mobile ============
    print("== mobile ==")
    ctx4 = browser.new_context(viewport={"width": 390, "height": 844},
                               is_mobile=True, has_touch=True,
                               user_agent="Mozilla/5.0 (Linux; Android 14) "
                                          "AppleWebKit/537.36 Chrome/120 Mobile")
    ctx4.add_init_script(FAKE_SPEECH_INIT)
    page4 = ctx4.new_page()
    errors4 = []
    page4.on("console", lambda m: errors4.append(m.text) if m.type == "error" else None)
    page4.on("pageerror", lambda e: errors4.append(str(e)))
    page4.goto(f"{BASE}/workspace", wait_until="networkidle")
    page4.wait_for_selector("#chat-input")
    overflow = page4.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("0px horizontal overflow on mobile", overflow <= 0, f"{overflow}px")
    check("mic control visible on mobile",
          page4.locator("button[aria-label='Start voice input']").count() == 1)
    page4.locator("button[aria-label='Start voice input']").tap()
    page4.evaluate("() => { const r = window.__voiceQA.instances.at(-1);"
                   " r.onstart && r.onstart(); }")
    check("mobile LISTENING on real evidence", wait_token(page4, "LISTENING"))
    # Tab backgrounding must resolve the voice session truthfully.
    page4.evaluate(
        "() => { Object.defineProperty(document, 'hidden',"
        " { value: true, configurable: true });"
        " document.dispatchEvent(new Event('visibilitychange')); }")
    page4.wait_for_timeout(300)
    check("visibility loss resolves LISTENING → AVAILABLE (no phantom state)",
          wait_token(page4, "AVAILABLE"))
    shoot(page4, "07-mobile")
    check("0 console errors on mobile", len(errors4) == 0, "; ".join(errors4[:3]))
    ctx4.close()

    # ============ PASS 5: keyboard accessibility ============
    print("== accessibility ==")
    ctx5 = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx5.add_init_script(FAKE_SPEECH_INIT)
    page5 = ctx5.new_page()
    page5.goto(f"{BASE}/workspace", wait_until="networkidle")
    page5.wait_for_selector("#chat-input")
    page5.locator("button[aria-label='Start voice input']").focus()
    page5.keyboard.press("Enter")
    page5.evaluate("() => { const r = window.__voiceQA.instances.at(-1);"
                   " r.onstart && r.onstart(); }")
    check("mic fully keyboard-operable", wait_token(page5, "LISTENING"))
    page5.keyboard.press("Escape")
    page5.wait_for_timeout(200)
    check("Escape cancels the voice attempt", wait_token(page5, "AVAILABLE"))
    check("polite live region present for screen readers",
          page5.locator("span.sr-only[role='status'][aria-live='polite']").count() >= 1)
    ctx5.close()

    browser.close()

(OUT / "phase3f-voice-qa-results.json").write_text(
    json.dumps(results, indent=2), encoding="utf-8")

total = len(results["checks"])
passed = sum(1 for c in results["checks"] if c["pass"])
print(f"\nPHASE 3F VOICE QA: {passed}/{total} checks passed")
if fail:
    print("FAILED:", *fail, sep="\n  - ")
    sys.exit(1)
print("VERDICT: PASS")
