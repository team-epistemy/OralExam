# Speech assessment demo (Azure Pronunciation Assessment)

Shows two things side by side: how a student spoke (accuracy, fluency, prosody, pause and rate measures) and what they said (transcript graded by an LLM).

## Run it

```bash
npm install
cp .env.example .env     # add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION
npm start                # http://localhost:3000
```

- No key needed to look around: click "Load sample result" (synthetic data shaped like Azure's JSON).
- Live recording needs the Azure key. The mic works on `localhost` or HTTPS only.
- `ANTHROPIC_API_KEY` is optional and enables live content grading. Without it the sample grading is shown for the sample only.

## How it works

```
Browser mic -> Azure Speech SDK (continuous recognition + pronunciation assessment, prosody on)
                    ^ short-lived token
Your server  -> /api/token  (holds the Azure key, never sent to the browser)
Browser      -> scoring.js  (parse phrases, weight scores, pauses and rates from word timings)
Your server  -> /api/content-eval (transcript only, optional)
```

Unscripted mode is the default (empty reference text, miscue off). For read-aloud tasks pass `referenceText` to `createAssessor` and completeness and miscue detection switch on.

## Tests

```bash
npm test                                            # 18 unit and server tests, offline
PORT=3111 node server.js &                          # then:
python3 test/e2e_ui.py http://localhost:3111        # 24 headless-browser checks (needs playwright)
```

What is verified: scoring math against hand-computed values, token caching and key secrecy, content-eval parsing and error paths, UI rendering at desktop and phone width, the full record/stop flow against a fake SDK, and that the real SDK bundle exposes every API used and puts `enableProsodyAssessment: true` into the request.

**Not verified: any call to the real Azure service.** No key or outbound access was available when this was built. Check these on the first live run:

1. Prosody is returned for your target locale (it has been limited to a few locales, en-US first).
2. Word `Offset` values are measured from the start of the stream across phrases in continuous mode. Pause calculation between phrases assumes this. If not, pause counts across phrase boundaries will be off.
3. Scores look sensible on a short unscripted answer. Very short answers can give unstable fluency and prosody.
4. Long answers: the token lasts 10 minutes. Sessions longer than that need a token refresh on the recognizer.

## Moving this into Epistemy's speech-test

Lift two files, they have no framework dependencies:

- `public/scoring.js`: pure functions, safe to reuse server side too.
- `public/speechAssess.js`: `createAssessor({ getToken, locale, referenceText, onInterim, onPhrase, onError })` returning `start()` and `stop()`. `stop()` resolves with scores, metrics, words and pauses.

Then:

1. Replace `/api/token` with an endpoint behind Epistemy's own auth. The demo's endpoint is open to anyone who can reach it. Add rate limiting.
2. Bundle or self-host the Speech SDK (`npm i microsoft-cognitiveservices-speech-sdk`) as this demo does.
3. Save the raw per-phrase JSON with each attempt. You will want it for audits, rescoring and validation.
4. Map `res.scores` and `res.metrics` into your existing result model. Keep the content grade separate from the speech-quality scores.
5. Check data handling (student audio goes to Azure) against Epistemy's consent, privacy and institutional commitments before any pilot.
6. Present scores as indicative until you have compared them with human raters on your own student population. Prosody scores are tuned toward native-like targets and can penalize legitimate accent variation.

## Files

```
server.js                  token, config, content-eval, static hosting
public/index.html, app.js  demo UI
public/speechAssess.js     Azure wrapper (reusable)
public/scoring.js          scoring and metrics (reusable)
public/fixtures/           synthetic sample
test/                      unit, server and headless UI tests
```
