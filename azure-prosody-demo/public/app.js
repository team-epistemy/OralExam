import { createAssessor } from "./speechAssess.js";
import { parsePhrase, aggregate, band, estimateCEFR, nextLevel, shouldStop, aggregatePlacement, CEFR_LADDER } from "./scoring.js";

const $ = (id) => document.getElementById(id);
const recBtn = $("rec");
let config = { azureConfigured: false, contentEvalEnabled: false };
let assessor = null;
let recording = false;
let current = null; // last aggregate result
let currentEstimate = null; // last CEFR estimate (logged during calibration)
let sampleContent = null;
let answerAudioURL = null; // object URL of the recorded response, for replay
let mediaRecorder = null, recChunks = [], recStream = null;

// Record the mic locally (in parallel with Azure) so the response can be replayed.
// Replay is best-effort: if capture fails, scoring still works, just no playback.
async function startLocalRecorder() {
  if (answerAudioURL) { URL.revokeObjectURL(answerAudioURL); answerAudioURL = null; }
  recChunks = [];
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(recStream);
    mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) recChunks.push(e.data); };
    mediaRecorder.start();
  } catch {
    mediaRecorder = null; // no MediaRecorder / mic: skip replay
  }
}

function stopTracks() { if (recStream) { recStream.getTracks().forEach((t) => t.stop()); recStream = null; } }

function stopLocalRecorder() {
  return new Promise((resolve) => {
    if (!mediaRecorder || mediaRecorder.state === "inactive") { stopTracks(); resolve(null); return; }
    mediaRecorder.onstop = () => {
      const blob = new Blob(recChunks, { type: mediaRecorder.mimeType || "audio/webm" });
      stopTracks();
      resolve(blob.size ? URL.createObjectURL(blob) : null);
    };
    mediaRecorder.stop();
  });
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text; // textContent only: transcripts are never parsed as HTML
  return n;
}

async function getToken() {
  const r = await fetch("/api/token");
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "Token request failed");
  return body;
}

function setStatus(msg) { $("status").textContent = msg; }

function renderScores(res) {
  const box = $("scores");
  box.replaceChildren();
  const items = [
    ["PronScore", res.scores.pron],
    ["Accuracy", res.scores.accuracy],
    ["Fluency", res.scores.fluency],
    ["Prosody", res.scores.prosody],
    ["Completeness", res.scores.completeness],
  ];
  for (const [label, val] of items) {
    const d = el("div", `score ${band(val)}`);
    d.append(el("div", "n", val == null ? "n/a" : String(Math.round(val))), el("div", "l", label));
    box.append(d);
  }
  $("prosodyNote").classList.toggle("hidden", res.prosodyAvailable);
}

function renderMetrics(m) {
  const box = $("metrics");
  box.replaceChildren();
  const rows = [
    ["Speech rate", m.speechRateWpm == null ? "n/a" : `${m.speechRateWpm} wpm`],
    ["Articulation rate", m.articulationRateWpm == null ? "n/a" : `${m.articulationRateWpm} wpm`],
    ["Pauses", `${m.pauseCount} (${m.longPauseCount} long)`],
    ["Mean pause", `${m.meanPauseSec}s`],
    ["Time not speaking", m.pauseRatio == null ? "n/a" : `${m.pauseRatio}%`],
    ["Duration", m.durationSec == null ? "n/a" : `${m.durationSec}s`],
  ];
  for (const [l, v] of rows) {
    const d = el("div", "metric");
    d.append(el("div", "v", v), el("div", "l", l));
    box.append(d);
  }
}

const CEFR_SET = ["A1", "A2", "B1", "B2", "C1", "C2"];
const isCefr = (b) => CEFR_SET.includes(b);

// A2/<A1 → weak, B1 → ok, B2 and up → good. Used only for tile colour.
function cefrColor(letter) {
  if (!letter) return "na";
  if (letter.startsWith("B2") || letter.startsWith("C")) return "good";
  if (letter.startsWith("B1")) return "ok";
  return "weak";
}

function cefrTile(label, letter, sub) {
  const d = el("div", `score ${cefrColor(letter)}`);
  d.append(el("div", "n", letter ?? "—"), el("div", "l", sub ? `${label} · ${sub}` : label));
  return d;
}

function renderCEFR(c) {
  currentEstimate = c;
  $("cefrBadge").textContent = c.overall ?? "—";
  $("cefrBadge").className = `cefr-badge ${cefrColor(c.overall)}`;
  const meta = $("cefrMeta");
  meta.replaceChildren();
  meta.append(
    el("div", null, `Overall = holistic mean of the CEFR Table-3 dimensions · ${c.mode}`),
    el("div", null, `Confidence: ${c.confidence}`),
  );

  // The five CEFR Table-3 columns + Azure pronunciation as supplementary context.
  const sub = $("cefrSub");
  sub.replaceChildren();
  const d = c.dimensions;
  sub.append(cefrTile("Fluency", d.fluency.band, d.fluency.score == null ? null : String(d.fluency.score)));
  sub.append(cefrTile("Range", d.range.band, d.range.band ? null : "grade"));
  sub.append(cefrTile("Accuracy", d.accuracy.band, d.accuracy.band ? null : "grade"));
  sub.append(cefrTile("Coherence", d.coherence.band, d.coherence.band ? null : "grade"));
  sub.append(cefrTile("Interaction", null, "n/a — monologue"));
  // Pronunciation is NOT a Table-3 column — show the Azure accuracy number, flagged.
  const pron = el("div", `score ${band(c.pronunciation.accuracy)}`);
  pron.append(
    el("div", "n", c.pronunciation.accuracy == null ? "n/a" : String(Math.round(c.pronunciation.accuracy))),
    el("div", "l", "Pronunciation (Azure, not CEFR)"),
  );
  sub.append(pron);

  const mBox = $("cefrMeasures");
  mBox.replaceChildren();
  for (const mm of d.fluency.measures) {
    if (mm.value == null) continue;
    const s = el("span");
    s.append(el("b", null, `${mm.label}: `), document.createTextNode(`${mm.value}${mm.unit === "%" ? "%" : " " + mm.unit} → ${mm.band}`));
    mBox.append(s);
  }

  const notes = $("cefrNotes");
  notes.replaceChildren();
  [...c.notes, c.pronunciation.note].forEach((t) => notes.append(el("li", null, t)));

  updateCalEst(c); // refresh the "(est: …)" reference on the calibration dropdowns
}

function renderWords(res) {
  const box = $("words");
  box.replaceChildren();
  const words = res.words;
  words.forEach((w, i) => {
    if (i > 0) {
      const gap = w.start - (words[i - 1].start + words[i - 1].duration);
      if (gap >= 0.25) box.append(el("span", `gap${gap >= 1 ? " long" : ""}`, `${gap.toFixed(1)}s`));
    }
    const chip = el("span", `w ${band(w.accuracy)}`, w.word);
    chip.title = w.accuracy == null ? "no score" : `accuracy ${Math.round(w.accuracy)}`;
    box.append(chip);
  });
}

function showResult(res) {
  current = res;
  $("results").classList.remove("hidden");
  renderScores(res);
  renderCEFR(estimateCEFR(res, null));
  renderMetrics(res.metrics);
  renderWords(res);
  $("transcript").textContent = res.transcript || "(no speech recognized)";
  setPlayerSrc(answerAudioURL);
  $("content").replaceChildren();
  $("gradeStatus").textContent = "";
}

// Point the audio player at a recorded blob. MediaRecorder's WebM has no
// duration header, so <audio>.duration is Infinity and the timeline looks
// truncated; force the browser to compute the real duration by seeking past the
// end once, then resetting. (The audio data itself is complete.)
function setPlayerSrc(url) {
  const player = $("player");
  if (!url) { player.removeAttribute("src"); player.load(); player.classList.add("hidden"); return; }
  player.preload = "auto";
  player.src = url;
  player.classList.remove("hidden");
  player.addEventListener("loadedmetadata", function onMeta() {
    player.removeEventListener("loadedmetadata", onMeta);
    if (player.duration === Infinity || Number.isNaN(player.duration)) {
      const onSeek = () => { player.removeEventListener("timeupdate", onSeek); player.currentTime = 0; };
      player.addEventListener("timeupdate", onSeek);
      player.currentTime = 1e7; // seek past the end; browser clamps and reports the true duration
    }
  });
}

function renderContent(r) {
  const box = $("content");
  box.replaceChildren();
  const scores = el("div", "scores");
  // CEFR Table-3 rubric bands (Range / Accuracy / Coherence).
  for (const [l, v] of [["Range", r.range], ["Accuracy", r.accuracy], ["Coherence", r.coherence]]) {
    if (!isCefr(v)) continue; // skip numbers/absent (e.g. older sample fixtures)
    const d = el("div", `score ${cefrColor(v)}`);
    d.append(el("div", "n", v), el("div", "l", `${l} (CEFR)`));
    scores.append(d);
  }
  // Content task fulfilment (0-100), auxiliary to the rubric.
  for (const [l, v] of [["Relevance", r.relevance], ["Depth", r.depth]]) {
    if (v == null) continue;
    const d = el("div", `score ${band(v)}`);
    d.append(el("div", "n", String(Math.round(v))), el("div", "l", l));
    scores.append(d);
  }
  box.append(scores);
  // Per-dimension CEFR justifications.
  for (const [l, note] of [["Range", r.rangeNote], ["Accuracy", r.accuracyNote], ["Coherence", r.coherenceNote]]) {
    if (note) box.append(el("p", "note", `${l}: ${note}`));
  }
  if (r.summary) box.append(el("p", null, r.summary));
  for (const [title, list] of [["Strengths", r.strengths], ["Gaps", r.gaps]]) {
    if (!list?.length) continue;
    box.append(el("strong", null, title));
    const ul = el("ul");
    list.forEach((t) => ul.append(el("li", null, t)));
    box.append(ul);
  }
  // Fold the rubric bands into the CEFR estimate.
  if (current) renderCEFR(estimateCEFR(current, r));
}

async function toggleRecording() {
  if (!recording) {
    if (!config.azureConfigured) { setStatus("No Azure key on the server. Use the sample, or add a key to .env."); return; }
    try {
      assessor = createAssessor({
        getToken,
        onInterim: (t) => ($("live").textContent = t),
        onError: (e) => setStatus(`Error: ${e.message}`),
      });
      setStatus("Starting microphone...");
      await assessor.start();
      await startLocalRecorder(); // capture audio for replay, alongside Azure
      recording = true;
      recBtn.textContent = "Stop and score";
      recBtn.classList.add("recording");
      setStatus("Listening. Answer the question, then stop.");
    } catch (e) {
      setStatus(`Could not start: ${e.message}`);
    }
  } else {
    recBtn.disabled = true;
    setStatus("Scoring...");
    let res = null;
    try {
      res = await assessor.stop();
      answerAudioURL = await stopLocalRecorder();
      sampleContent = null; // a real recording must never show the canned sample grading
      showResult(res);
      setStatus(res.words.length ? "Done." : "No speech detected. Try again closer to the mic.");
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
    recording = false;
    $("live").textContent = "";
    recBtn.disabled = false;
    recBtn.textContent = "Start recording";
    recBtn.classList.remove("recording");
    // In an adaptive session, auto-grade this answer and advance to the next.
    if (adaptive && res && res.words.length) advanceAdaptive(res);
  }
}

async function loadSample() {
  const fx = await (await fetch("/fixtures/sample-result.json")).json();
  $("question").value = fx.question;
  sampleContent = fx.content;
  if (answerAudioURL) { URL.revokeObjectURL(answerAudioURL); answerAudioURL = null; } // sample has no audio
  showResult(aggregate(fx.phrases.map(parsePhrase)));
  setStatus("Showing a synthetic sample (not real Azure output).");
}

async function fetchContentEval(question, transcript) {
  const r = await fetch("/api/content-eval", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, transcript }),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || "Grading failed");
  return body.result;
}

async function gradeContent() {
  if (!current?.transcript) return;
  const status = $("gradeStatus");
  if (sampleContent && !config.contentEvalEnabled) {
    renderContent(sampleContent);
    status.textContent = "Sample grading shown. Set ANTHROPIC_API_KEY for live grading.";
    return;
  }
  status.textContent = "Grading...";
  try {
    renderContent(await fetchContentEval($("question").value, current.transcript));
    status.textContent = "";
    sampleContent = null;
  } catch (e) {
    status.textContent = e.message;
  }
}

// CEFR dimensions a rater calibrates; `est` reads the machine's band off the
// estimate. Keep keys in sync with CAL_DIMS in server.js.
// The CEFR Table-3 columns (+ Overall). Keys mirror CAL_DIMS in server.js.
const CAL_DIMS = [
  { key: "overall", label: "Overall", est: (c) => c?.overall },
  { key: "range", label: "Range", est: (c) => c?.dimensions?.range?.band },
  { key: "accuracy", label: "Accuracy", est: (c) => c?.dimensions?.accuracy?.band },
  { key: "fluency", label: "Fluency", est: (c) => c?.dimensions?.fluency?.band },
  { key: "coherence", label: "Coherence", est: (c) => c?.dimensions?.coherence?.band },
  { key: "interaction", label: "Interaction", est: () => null }, // monologue: human-only
];
const CEFR_OPTS = ["", "A1", "A2", "B1", "B2", "C1", "C2"];

function buildCalInputs() {
  const box = $("calInputs");
  box.replaceChildren();
  for (const dim of CAL_DIMS) {
    const wrap = el("div");
    const lab = el("label", null, dim.label);
    lab.id = `calEst-${dim.key}`;
    lab.htmlFor = `calSel-${dim.key}`;
    const sel = document.createElement("select");
    sel.id = `calSel-${dim.key}`;
    for (const o of CEFR_OPTS) {
      const opt = document.createElement("option");
      opt.value = o; opt.textContent = o || "—";
      sel.append(opt);
    }
    wrap.append(lab, sel);
    box.append(wrap);
  }
}

// Show the machine's band next to each dimension so the rater has a reference.
function updateCalEst(c) {
  for (const dim of CAL_DIMS) {
    const b = c ? dim.est(c) : null;
    const lab = document.getElementById(`calEst-${dim.key}`);
    if (lab) lab.textContent = `${dim.label} (est: ${b ?? "n/a"})`;
  }
}

function renderCalibration(count, summary, recent) {
  const s = $("calSummary");
  s.replaceChildren();
  const per = summary?.perDimension || {};
  const dims = CAL_DIMS.filter((d) => per[d.key]);
  if (!dims.length) {
    s.append(el("div", null, `${count || 0} logged. No estimate↔human pairs yet — log ratings to start tracking agreement.`));
  } else {
    s.append(el("div", null, `${count} logged. Agreement by dimension:`));
    for (const d of dims) {
      const st = per[d.key];
      s.append(el("div", null, `${d.label}: n=${st.n} · exact ${st.exactPct}% · within one ${st.within1Pct}% · mean err ${st.meanAbsSublevels} sub-levels`));
    }
  }
  if (recent) {
    const box = $("calRecent");
    box.replaceChildren();
    for (const r of recent) {
      const h = r.human || {};
      const parts = CAL_DIMS.filter((d) => h[d.key]).map((d) => `${d.label[0]}:${h[d.key]}`).join(" ");
      const span = el("span");
      span.append(el("b", null, `est ${r.estimate ?? "—"}`), document.createTextNode(` · human ${parts || "—"}`));
      box.append(span);
    }
  }
}

async function loadCalibration() {
  try {
    const r = await fetch("/api/calibration");
    const b = await r.json();
    renderCalibration(b.count, b.summary, b.recent);
  } catch { /* calibration is best-effort; ignore fetch errors */ }
}

async function logRating() {
  const status = $("calStatus");
  if (!currentEstimate) { status.textContent = "Score an answer first."; return; }
  const human = {};
  for (const dim of CAL_DIMS) {
    const v = document.getElementById(`calSel-${dim.key}`)?.value;
    if (v) human[dim.key] = v;
  }
  if (!Object.keys(human).length) { status.textContent = "Pick at least one dimension's level."; return; }
  status.textContent = "Logging...";
  try {
    const r = await fetch("/api/calibration", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        human, estimate: currentEstimate, question: $("question").value,
        metrics: current?.metrics ?? null, scores: current?.scores ?? null,
        transcript: current?.transcript ?? "",
      }),
    });
    const b = await r.json();
    if (!r.ok) throw new Error(b.error || "Log failed");
    status.textContent = `Logged ${Object.keys(human).length} dimension(s).`;
    for (const dim of CAL_DIMS) { const sel = document.getElementById(`calSel-${dim.key}`); if (sel) sel.value = ""; }
    renderCalibration(b.count, b.summary);
    loadCalibration(); // refresh the recent list
  } catch (e) {
    status.textContent = e.message;
  }
}

// ── Adaptive multi-question flow ─────────────────────────────────────────
let bank = {};
const usedQuestions = new Set();
let adaptive = null; // { level, asked:[{level,question}], estimates:[], overalls:[] }

async function loadBank() {
  try { bank = await (await fetch("/fixtures/question-bank.json")).json(); } catch { bank = {}; }
}

// Hybrid question selection: an unused bank question at the target level, else
// generate one via the LLM, else fall back to any bank question.
async function pickQuestion(level) {
  const pool = (bank[level] || []).filter((q) => typeof q === "string" && !usedQuestions.has(q));
  if (pool.length) { usedQuestions.add(pool[0]); return pool[0]; }
  if (config.contentEvalEnabled) {
    try {
      const r = await fetch("/api/generate-question", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ level, avoid: [...usedQuestions].slice(-12) }),
      });
      const b = await r.json();
      if (r.ok && b.question) { usedQuestions.add(b.question); return b.question; }
    } catch { /* fall through to bank reuse */ }
  }
  for (const lvl of [level, ...CEFR_LADDER]) { const q = (bank[lvl] || [])[0]; if (q) return q; }
  return "Tell me about yourself.";
}

const setAdaptiveStatus = (msg) => { $("adaptiveStatus").textContent = msg; };

async function startAdaptive() {
  if (recording) { setAdaptiveStatus("Finish the current recording first."); return; }
  adaptive = { level: "B1", asked: [], estimates: [], overalls: [] };
  usedQuestions.clear();
  $("placementCard").classList.add("hidden");
  const q = await pickQuestion(adaptive.level);
  adaptive.asked.push({ level: adaptive.level, question: q });
  $("question").value = q;
  setAdaptiveStatus(`Adaptive test started · question 1 of up to 5 · target ${adaptive.level}. Press “Start recording”, answer, then stop.`);
}

async function advanceAdaptive(res) {
  // Auto-grade the rubric so the overall band can drive adaptation.
  let contentResult = null;
  if (config.contentEvalEnabled && res?.transcript) {
    setAdaptiveStatus("Scoring your answer…");
    try { contentResult = await fetchContentEval($("question").value, res.transcript); renderContent(contentResult); }
    catch { /* fall back to fluency-only estimate */ }
  }
  const est = estimateCEFR(res, contentResult);
  renderCEFR(est);
  adaptive.estimates.push(est);
  adaptive.overalls.push(est.overall);
  const i = adaptive.estimates.length;

  if (shouldStop(adaptive.overalls, 5)) { finishAdaptive(); return; }
  adaptive.level = nextLevel(adaptive.level, est.overall);
  const q = await pickQuestion(adaptive.level);
  adaptive.asked.push({ level: adaptive.level, question: q });
  $("question").value = q;
  setAdaptiveStatus(`Answer ${i} scored (${est.overall ?? "n/a"}). Question ${i + 1} of up to 5 · target ${adaptive.level}. Record your next answer.`);
}

function finishAdaptive() {
  const placement = aggregatePlacement(adaptive.estimates);
  renderPlacement(placement, adaptive);
  setAdaptiveStatus(`Adaptive test complete — ${placement.n} answer(s). Placement: ${placement.overall ?? "n/a"}.`);
  adaptive = null;
}

function renderPlacement(p, session) {
  $("placementCard").classList.remove("hidden");
  $("placementBadge").textContent = p.overall ?? "—";
  $("placementBadge").className = `cefr-badge ${cefrColor(p.overall)}`;
  const meta = $("placementMeta");
  meta.replaceChildren();
  meta.append(
    el("div", null, `Across ${p.n} answer(s) · confidence ${p.confidence}`),
    el("div", null, "Aggregating responses raises reliability (single sample R≈.56 → ~.77 at 6)."),
  );
  const sub = $("placementSub");
  sub.replaceChildren();
  for (const [label, key] of [["Fluency", "fluency"], ["Range", "range"], ["Accuracy", "accuracy"], ["Coherence", "coherence"]]) {
    sub.append(cefrTile(label, p.dimensions[key], null));
  }
  const hist = $("placementHistory");
  hist.replaceChildren();
  session.asked.slice(0, session.estimates.length).forEach((a, k) => {
    const span = el("span");
    span.append(el("b", null, `Q${k + 1} (${a.level}): `), document.createTextNode(session.estimates[k]?.overall ?? "—"));
    hist.append(span);
  });
}

recBtn.addEventListener("click", toggleRecording);
$("startAdaptive").addEventListener("click", startAdaptive);
$("sample").addEventListener("click", loadSample);
$("grade").addEventListener("click", gradeContent);
$("logRating").addEventListener("click", logRating);
buildCalInputs();
loadBank();
loadCalibration();

fetch("/api/config").then((r) => r.json()).then((c) => {
  config = c;
  if (!c.azureConfigured) setStatus("No Azure key configured: sample mode only.");
});
