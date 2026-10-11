import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { parsePhrase, aggregate, band, estimateCEFR, cefrBand, nextLevel, shouldStop, aggregatePlacement } from "../public/scoring.js";

const fx = JSON.parse(fs.readFileSync(new URL("../public/fixtures/sample-result.json", import.meta.url)));
const close = (a, b, tol = 0.15) => assert.ok(Math.abs(a - b) <= tol, `${a} not within ${tol} of ${b}`);

test("parsePhrase converts ticks to seconds and keeps per-word scores", () => {
  const p = parsePhrase(fx.phrases[0]);
  assert.equal(p.words.length, 13);
  assert.equal(p.words[0].word, "I");
  close(p.words[0].start, 0.4, 1e-9);
  close(p.words[0].duration, 0.12, 1e-9);
  assert.equal(p.scores.prosody, 78.5);
  assert.equal(p.words[0].errorType, "None");
});

test("parsePhrase returns null when there is no NBest (silence, no match)", () => {
  assert.equal(parsePhrase({ RecognitionStatus: "NoMatch" }), null);
  assert.equal(parsePhrase(null), null);
});

test("aggregate weights phrase scores by word count", () => {
  const r = aggregate(fx.phrases.map(parsePhrase));
  assert.equal(r.scores.accuracy, 83.4);
  assert.equal(r.scores.fluency, 79.4);
  assert.equal(r.scores.prosody, 74.1);
  assert.equal(r.scores.pron, 81.4);
  assert.equal(r.prosodyAvailable, true);
});

test("aggregate derives rate and pause metrics from word timings", () => {
  const r = aggregate(fx.phrases.map(parsePhrase));
  const m = r.metrics;
  assert.equal(m.wordCount, 25);
  close(m.durationSec, 10.45);
  close(m.speechRateWpm, 143.5, 0.3);
  close(m.articulationRateWpm, 217.4, 0.3);
  assert.equal(m.pauseCount, 3); // 1.23s between phrases, 0.9s and 0.5s inside phrase 2
  assert.equal(m.longPauseCount, 1);
  close(m.pauseRatio, 34.0, 0.3);
  assert.equal(r.pauses[0].seconds, 1.2);
});

test("aggregate flags weak words", () => {
  const r = aggregate(fx.phrases.map(parsePhrase));
  assert.deepEqual(r.weakWords.map((w) => w.word), ["organize", "thoughts"]);
});

test("aggregate joins transcripts in order", () => {
  const r = aggregate(fx.phrases.map(parsePhrase));
  assert.match(r.transcript, /^I think the main reason.*writing\.$/);
});

test("missing prosody (unsupported locale) yields null and prosodyAvailable false", () => {
  const raw = structuredClone(fx.phrases);
  for (const p of raw) delete p.NBest[0].PronunciationAssessment.ProsodyScore;
  const r = aggregate(raw.map(parsePhrase));
  assert.equal(r.scores.prosody, null);
  assert.equal(r.prosodyAvailable, false);
  assert.equal(r.scores.accuracy, 83.4); // other scores unaffected
});

test("aggregate is safe on empty input and null phrases", () => {
  for (const input of [[], [null], undefined]) {
    const r = aggregate(input);
    assert.equal(r.metrics.wordCount, 0);
    assert.equal(r.metrics.speechRateWpm, null);
    assert.equal(r.scores.fluency, null);
    assert.equal(r.transcript, "");
  }
});

test("phrases supplied out of order still give correct pauses", () => {
  const r = aggregate(fx.phrases.map(parsePhrase).reverse());
  assert.equal(r.metrics.pauseCount, 3);
});

test("band thresholds", () => {
  assert.equal(band(80), "good");
  assert.equal(band(79.9), "ok");
  assert.equal(band(60), "ok");
  assert.equal(band(59.9), "weak");
  assert.equal(band(null), "na");
});

// ── estimateCEFR ───────────────────────────────────────────────────────────
// Build a minimal result from metrics/scores (CEFR estimation reads only these).
const mkResult = (metrics, scores = {}) => ({ metrics, scores });

test("cefrBand maps the GSE axis onto Versant/GSE band boundaries", () => {
  assert.equal(cefrBand(40), "A2");
  assert.equal(cefrBand(50), "B1");
  assert.equal(cefrBand(67), "B2");
  assert.equal(cefrBand(80), "C1");
  assert.equal(cefrBand(null), null);
});

test("estimateCEFR: real sample 1 (slow but continuous) lands B2 on fluency alone", () => {
  const c = estimateCEFR(mkResult({
    wordCount: 60, durationSec: 38.8, speechRateWpm: 92.8, articulationRateWpm: 111.4,
    pauseCount: 11, longPauseCount: 0, meanPauseSec: 0.5, pauseRatio: 16.6,
  }));
  assert.equal(c.mode, "fluency-only");
  assert.equal(c.dimensions.fluency.band, "B2");
  assert.equal(c.overall, "B2");
  assert.equal(c.confidence, "low");
  assert.equal(c.dimensions.interaction.band, null); // not assessable from a monologue
});

test("estimateCEFR: real sample 2 (a notch faster) also B2, fluency score >= 70", () => {
  const c = estimateCEFR(mkResult({
    wordCount: 93, durationSec: 46.9, speechRateWpm: 118.9, articulationRateWpm: 147.8,
    pauseCount: 13, longPauseCount: 1, meanPauseSec: 0.6, pauseRatio: 19.6,
  }));
  assert.equal(c.overall, "B2");
  assert.ok(c.dimensions.fluency.score >= 70);
});

test("estimateCEFR: a slow, halting answer lands A1/A2/B1", () => {
  const c = estimateCEFR(mkResult({
    wordCount: 20, durationSec: 30, speechRateWpm: 45, articulationRateWpm: 90,
    pauseCount: 10, longPauseCount: 4, meanPauseSec: 1.4, pauseRatio: 55,
  }));
  assert.ok(["A1", "A2", "B1"].includes(c.overall), `got ${c.overall}`);
});

test("estimateCEFR: fluency alone is capped at B2+ (B2/C1 plateau), never a bare C1", () => {
  const c = estimateCEFR(mkResult({
    wordCount: 160, durationSec: 60, speechRateWpm: 170, articulationRateWpm: 200,
    pauseCount: 3, longPauseCount: 0, meanPauseSec: 0.35, pauseRatio: 8,
  }));
  assert.equal(c.mode, "fluency-only");
  assert.equal(c.overall, "B2+");
  assert.ok(c.notes.some((n) => /Capped at B2\+/.test(n)));
});

test("estimateCEFR: rubric bands switch to full-rubric mode and drive the overall", () => {
  const metrics = {
    wordCount: 160, durationSec: 60, speechRateWpm: 170, articulationRateWpm: 200,
    pauseCount: 3, longPauseCount: 0, meanPauseSec: 0.35, pauseRatio: 8,
  };
  const c = estimateCEFR(mkResult(metrics), { range: "C1", accuracy: "C1", coherence: "C1", relevance: 95, depth: 90 });
  assert.equal(c.mode, "full-rubric");
  assert.equal(c.dimensions.range.band, "C1");
  assert.equal(c.dimensions.accuracy.band, "C1");
  assert.ok(["B2", "C1", "C2"].includes(c.overall), `got ${c.overall}`);
});

test("estimateCEFR: non-CEFR rubric values are ignored (not treated as bands)", () => {
  const c = estimateCEFR(mkResult({ wordCount: 50, durationSec: 30, speechRateWpm: 100, pauseCount: 5, meanPauseSec: 0.6, pauseRatio: 20 }),
    { range: 80, accuracy: "nonsense", coherence: "B1" });
  assert.equal(c.dimensions.range.band, null);     // 80 is a number, not a CEFR level
  assert.equal(c.dimensions.accuracy.band, null);  // "nonsense" is not a CEFR level
  assert.equal(c.dimensions.coherence.band, "B1");
  assert.equal(c.mode, "full-rubric");             // at least one valid rubric band
});

test("estimateCEFR: low pronunciation accuracy raises an intelligibility caveat", () => {
  const c = estimateCEFR(
    mkResult({ wordCount: 40, durationSec: 30, speechRateWpm: 90, articulationRateWpm: 120, pauseCount: 6, meanPauseSec: 0.6, pauseRatio: 25 },
      { accuracy: 42, prosody: 55 }));
  assert.ok(c.notes.some((n) => /intelligibility/i.test(n)));
  assert.equal(c.pronunciation.accuracy, 42);
});

test("estimateCEFR is safe on empty metrics", () => {
  const c = estimateCEFR(aggregate([]));
  assert.equal(c.dimensions.fluency.score, null);
  assert.equal(c.overall, null);
});

// ── adaptive flow ────────────────────────────────────────────────────────
test("nextLevel steps up when the answer meets/beats the level, down when below", () => {
  assert.equal(nextLevel("B1", "B2"), "B2");   // beat → harder
  assert.equal(nextLevel("B1", "B1"), "B2");   // meet → harder
  assert.equal(nextLevel("B1", "A2"), "A2");   // below → easier
  assert.equal(nextLevel("B2", "B2+"), "C1");  // B2+ counts as ≥ B2 → up
  assert.equal(nextLevel("C2", "C2"), "C2");   // clamped at the top
  assert.equal(nextLevel("A1", "A1"), "A2");   // clamped at the bottom then up
  assert.equal(nextLevel("B1", null), "B1");   // no usable band → hold
});

test("shouldStop: caps at max, early-stops on a stable band after >=3 answers", () => {
  assert.equal(shouldStop(["B1", "B2"], 5), false);              // too few
  assert.equal(shouldStop(["B2", "B2"], 5), false);              // equal but only 2
  assert.equal(shouldStop(["B1", "B1", "B1"], 5), true);          // stable at 3
  assert.equal(shouldStop(["B1", "B2", "B1"], 5), false);         // not stable
  assert.equal(shouldStop(["A2", "B1", "B2", "B2"], 5), true);    // last two equal
  assert.equal(shouldStop(["A", "B", "C", "D", "E"], 5), true);   // hit max
});

test("aggregatePlacement averages overall and each dimension across answers", () => {
  const mk = (overall, f, r, a, c) => ({ overall, dimensions: { fluency: { band: f }, range: { band: r }, accuracy: { band: a }, coherence: { band: c } } });
  const p = aggregatePlacement([mk("B1", "B2", "B1", "B1", "B1"), mk("B2", "B2", "B2", "B2", "B2")]);
  assert.equal(p.overall, "B2");        // mean(B1=2, B2=3) = 2.5 → B2
  assert.equal(p.dimensions.fluency, "B2");
  assert.equal(p.dimensions.range, "B2"); // mean(B1, B2) = 2.5 → B2
  assert.equal(p.n, 2);
  assert.equal(p.confidence, "low");    // < 3 answers
});

test("aggregatePlacement confidence rises with n and is safe when empty", () => {
  const e = { overall: "B1", dimensions: { fluency: { band: "B1" }, range: { band: "B1" }, accuracy: { band: "B1" }, coherence: { band: "B1" } } };
  assert.equal(aggregatePlacement([e, e, e]).confidence, "low-moderate");
  assert.equal(aggregatePlacement([e, e, e, e, e]).confidence, "moderate");
  const empty = aggregatePlacement([]);
  assert.equal(empty.overall, null);
  assert.equal(empty.dimensions.fluency, null);
  assert.equal(empty.n, 0);
});
