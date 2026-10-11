"""Headless-browser checks. Run with the server up:  PORT=3111 node server.js &  python3 test/e2e_ui.py http://localhost:3111

1. Sample mode renders scores, word chips, pauses, content grading.
2. The real Speech SDK bundle exposes every class/enum speechAssess.js uses, and the
   pronunciation config really carries the prosody flag.
3. The recording flow (speechAssess.js + app.js) works end to end against a FAKE SDK that
   emits Azure-shaped events. This verifies our wiring, not Azure itself.
"""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
FIX = json.loads((Path(__file__).parent.parent / "public/fixtures/sample-result.json").read_text())
failures = []

def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)

FAKE_SDK = """
(() => {
  const fixture = %s;
  window.__calls = {};
  const SDK = {
    OutputFormat: { Detailed: 1 },
    ResultReason: { RecognizedSpeech: 3, NoMatch: 0 },
    CancellationReason: { Error: 1 },
    PropertyId: { SpeechServiceResponse_JsonResult: 'json' },
    PronunciationAssessmentGradingSystem: { HundredMark: 'HundredMark' },
    PronunciationAssessmentGranularity: { Phoneme: 'Phoneme' },
    SpeechConfig: { fromAuthorizationToken(t, r) { window.__calls.token = t; window.__calls.region = r;
      return { set speechRecognitionLanguage(v) { window.__calls.locale = v; }, set outputFormat(v) { window.__calls.fmt = v; } }; } },
    AudioConfig: { fromDefaultMicrophoneInput() { return {}; } },
    PronunciationAssessmentConfig: class {
      constructor(ref, gs, gr, miscue) { window.__calls.ref = ref; window.__calls.miscue = miscue; window.__calls.gran = gr; }
      set enableProsodyAssessment(v) { window.__calls.prosody = v; }
      applyTo() { window.__calls.applied = true; }
    },
    SpeechRecognizer: class {
      startContinuousRecognitionAsync(ok) {
        ok();
        setTimeout(() => this.recognizing && this.recognizing(null, { result: { text: 'I think the main' } }), 30);
        fixture.phrases.forEach((p, i) => setTimeout(() => this.recognized(null, { result: { reason: 3,
          properties: { getProperty: () => JSON.stringify(p) } } }), 60 + i * 30));
      }
      stopContinuousRecognitionAsync(ok) { ok(); setTimeout(() => this.sessionStopped && this.sessionStopped(), 20); }
      close() { window.__calls.closed = true; }
    },
  };
  window.SpeechSDK = SDK;
})();
""" % json.dumps({"phrases": FIX["phrases"]})

with sync_playwright() as p:
    browser = p.chromium.launch()

    # ---- 1. sample mode + 2. real SDK surface -------------------------------------------
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE)
    page.click("#sample")
    page.wait_for_selector("#results:not(.hidden)")
    scores = page.locator("#scores .score")
    labels = [scores.nth(i).inner_text().replace("\n", " ") for i in range(scores.count())]
    check(labels == ["81 Overall", "83 Accuracy", "79 Fluency", "74 Prosody"], f"score cards {labels}")
    check(page.locator("#words .w").count() == 25, "25 word chips")
    check(page.locator("#words .w.weak").count() == 2, "2 weak words flagged")
    check(page.locator("#words .gap").count() == 3, "3 pause markers")
    check(page.locator("#words .gap.long").count() == 1, "1 long pause marker")
    check("143.5 wpm" in page.locator("#metrics").inner_text(), "speech rate shown")
    page.click("#grade")
    page.wait_for_selector("#content .score")
    check(page.locator("#content .score").count() == 3, "content grading cards shown (sample)")
    check("prosodyNote" and page.locator("#prosodyNote").is_hidden(), "prosody note hidden when prosody present")

    surface = page.evaluate("""() => {
      const S = window.SpeechSDK; const out = {};
      out.loaded = !!S;
      for (const [k, v] of Object.entries({
        PronunciationAssessmentConfig: S.PronunciationAssessmentConfig, SpeechRecognizer: S.SpeechRecognizer,
        'AudioConfig.fromDefaultMicrophoneInput': S.AudioConfig.fromDefaultMicrophoneInput,
        'SpeechConfig.fromAuthorizationToken': S.SpeechConfig.fromAuthorizationToken,
        'GradingSystem.HundredMark': S.PronunciationAssessmentGradingSystem.HundredMark,
        'Granularity.Phoneme': S.PronunciationAssessmentGranularity.Phoneme,
        'OutputFormat.Detailed': S.OutputFormat.Detailed, 'ResultReason.RecognizedSpeech': S.ResultReason.RecognizedSpeech,
        'CancellationReason.Error': S.CancellationReason.Error, 'PropertyId.JsonResult': S.PropertyId.SpeechServiceResponse_JsonResult,
      })) out[k] = v !== undefined;
      // build a real config + recognizer on a push stream (no mic, no network at construction)
      const stream = S.AudioInputStream.createPushStream();
      const sc = S.SpeechConfig.fromAuthorizationToken('x', 'westus2');
      const rec = new S.SpeechRecognizer(sc, S.AudioConfig.fromStreamInput(stream));
      const pc = new S.PronunciationAssessmentConfig('', S.PronunciationAssessmentGradingSystem.HundredMark, S.PronunciationAssessmentGranularity.Phoneme, false);
      pc.enableProsodyAssessment = true;
      pc.applyTo(rec);
      out.params = pc.toJSON();            // what applyTo pushes into the request's speech context
      out.ctx = JSON.stringify(rec.internalData.speechContext.privContext || {});
      rec.close();
      return out;
    }""")
    missing = [k for k, v in surface.items() if v is False]
    check(surface.get("loaded") and not missing, f"real SDK exposes everything we use (missing: {missing})")
    params = json.loads(surface["params"]) if surface.get("params") else {}
    print("     real SDK assessment params:", params)
    check(bool(params), "SDK produced assessment params")
    check(params.get("enableProsodyAssessment") in (True, "true", "True"), "prosody flag is in the SDK params")
    check(params.get("enableMiscue") in (False, "false", "False", None), "miscue off for unscripted")
    ctx = json.loads(surface.get("ctx") or "{}")
    sent = ctx.get("phraseDetection", {}).get("enrichment", {}).get("pronunciationAssessment", {})
    check(sent.get("enableProsodyAssessment") in (True, "true", "True"), "prosody flag is in the request context after applyTo")
    check(not errors, f"no console/page errors ({errors})")
    page.close()

    # ---- 3. recording flow against a fake SDK -------------------------------------------
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/config", lambda r: r.fulfill(json={"region": "westus2", "azureConfigured": True, "contentEvalEnabled": False}))
    page.route("**/api/token", lambda r: r.fulfill(json={"token": "TOKEN123", "region": "westus2", "expiresInSec": 540}))
    page.route("**/vendor/speech-sdk.js", lambda r: r.fulfill(body=FAKE_SDK, content_type="application/javascript"))
    page.goto(BASE)
    page.wait_for_timeout(200)
    page.click("#rec")
    page.wait_for_function("document.getElementById('rec').textContent.includes('Stop')")
    page.wait_for_function("document.getElementById('live').textContent.includes('main')")
    page.wait_for_timeout(250)
    page.click("#rec")
    page.wait_for_selector("#results:not(.hidden)")
    calls = page.evaluate("window.__calls")
    check(calls.get("token") == "TOKEN123" and calls.get("region") == "westus2", "token and region passed to SDK")
    check(calls.get("locale") == "en-US" and calls.get("prosody") is True and calls.get("applied") is True, "locale, prosody flag, applyTo")
    check(calls.get("ref") == "" and calls.get("miscue") is False, "unscripted: empty reference, miscue off")
    check(calls.get("closed") is True, "recognizer closed after stop")
    check(page.locator("#scores .score").first.inner_text().startswith("81"), "overall score computed from recognized phrases")
    check(page.locator("#words .w").count() == 25, "all 25 words rendered from live events")
    check(page.locator("#rec").inner_text() == "Start recording" and page.locator("#rec").is_enabled(), "button reset after scoring")
    # a second take must not accumulate the first take's phrases
    page.click("#rec"); page.wait_for_function("document.getElementById('rec').textContent.includes('Stop')")
    page.wait_for_timeout(250); page.click("#rec"); page.wait_for_timeout(300)
    check(page.locator("#words .w").count() == 25, "second take starts clean (still 25 words)")
    check(not errors, f"no page errors in recording flow ({errors})")

    # real recording must not show canned sample grading
    page.click("#sample"); page.wait_for_timeout(100)
    page.click("#rec"); page.wait_for_function("document.getElementById('rec').textContent.includes('Stop')")
    page.wait_for_timeout(250); page.click("#rec"); page.wait_for_timeout(300)
    page.click("#grade"); page.wait_for_timeout(300)
    check(page.locator("#content .score").count() == 0, "no sample grading leaks onto a real recording")
    browser.close()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
