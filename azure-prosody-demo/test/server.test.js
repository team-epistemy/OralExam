import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createApp } from "../server.js";

async function withServer(opts, fn) {
  const server = createApp(opts).listen(0);
  const base = `http://127.0.0.1:${server.address().port}`;
  try { await fn(base); } finally { server.close(); }
}
const post = (url, body) => fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

test("config reports which features are configured", async () => {
  await withServer({ env: {} }, async (base) => {
    const c = await (await fetch(`${base}/api/config`)).json();
    assert.equal(c.azureConfigured, false);
    assert.equal(c.contentEvalEnabled, false);
  });
  await withServer({ env: { AZURE_SPEECH_KEY: "k", ANTHROPIC_API_KEY: "a", AZURE_SPEECH_REGION: "eastus" } }, async (base) => {
    const c = await (await fetch(`${base}/api/config`)).json();
    assert.deepEqual(c, { region: "eastus", azureConfigured: true, contentEvalEnabled: true });
  });
});

test("token: 503 when no key is configured", async () => {
  await withServer({ env: {} }, async (base) => {
    const r = await fetch(`${base}/api/token`);
    assert.equal(r.status, 503);
  });
});

test("token: calls Azure with the key, never returns the key, and caches", async () => {
  const calls = [];
  let clock = 1_000_000;
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return new Response(`tok-${calls.length}`, { status: 200 }); };
  await withServer({ env: { AZURE_SPEECH_KEY: "SECRET", AZURE_SPEECH_REGION: "westus2" }, fetchImpl, now: () => clock }, async (base) => {
    const a = await (await fetch(`${base}/api/token`)).json();
    assert.equal(a.token, "tok-1");
    assert.equal(a.region, "westus2");
    assert.ok(!JSON.stringify(a).includes("SECRET"));
    assert.equal(calls[0].url, "https://westus2.api.cognitive.microsoft.com/sts/v1.0/issueToken");
    assert.equal(calls[0].init.headers["Ocp-Apim-Subscription-Key"], "SECRET");

    clock += 60_000; // within TTL: cached
    assert.equal((await (await fetch(`${base}/api/token`)).json()).token, "tok-1");
    assert.equal(calls.length, 1);

    clock += 9 * 60_000; // past TTL: refreshed
    assert.equal((await (await fetch(`${base}/api/token`)).json()).token, "tok-2");
    assert.equal(calls.length, 2);
  });
});

test("token: 502 with a clear message when Azure rejects the key", async () => {
  const fetchImpl = async () => new Response("denied", { status: 401 });
  await withServer({ env: { AZURE_SPEECH_KEY: "bad" }, fetchImpl }, async (base) => {
    const r = await fetch(`${base}/api/token`);
    assert.equal(r.status, 502);
    assert.match((await r.json()).error, /401/);
  });
});

test("content-eval: 501 without a key, 400 on bad input", async () => {
  await withServer({ env: {} }, async (base) => {
    assert.equal((await post(`${base}/api/content-eval`, { question: "q", transcript: "t" })).status, 501);
  });
  await withServer({ env: { ANTHROPIC_API_KEY: "a" } }, async (base) => {
    assert.equal((await post(`${base}/api/content-eval`, { question: "q" })).status, 400);
    assert.equal((await post(`${base}/api/content-eval`, { question: "q", transcript: "   " })).status, 400);
    assert.equal((await post(`${base}/api/content-eval`, { question: "q", transcript: "x".repeat(9000) })).status, 413);
  });
});

test("content-eval: parses JSON even when the model wraps it in prose, and sends the transcript as data", async () => {
  let sent;
  const fetchImpl = async (_url, init) => {
    sent = JSON.parse(init.body);
    const text = 'Here you go:\n{"relevance":80,"depth":60,"coherence":70,"summary":"ok","strengths":["a"],"gaps":["b"]}';
    return new Response(JSON.stringify({ content: [{ type: "text", text }] }), { status: 200 });
  };
  await withServer({ env: { ANTHROPIC_API_KEY: "a" }, fetchImpl }, async (base) => {
    const r = await post(`${base}/api/content-eval`, { question: "Why?", transcript: "Because reasons." });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.result.relevance, 80);
    assert.match(sent.messages[0].content, /<transcript>\nBecause reasons\.\n<\/transcript>/);
    assert.equal(sent.model, "claude-sonnet-5-5");
  });
});

test("content-eval: 502 when the model returns no JSON", async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ content: [{ type: "text", text: "sorry" }] }), { status: 200 });
  await withServer({ env: { ANTHROPIC_API_KEY: "a" }, fetchImpl }, async (base) => {
    assert.equal((await post(`${base}/api/content-eval`, { question: "q", transcript: "t" })).status, 502);
  });
});

test("calibration: validates input, appends, and computes agreement", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cal-"));
  const CALIBRATION_FILE = path.join(dir, "cal.jsonl");
  try {
    await withServer({ env: { CALIBRATION_FILE } }, async (base) => {
      // Empty to start.
      let g = await (await fetch(`${base}/api/calibration`)).json();
      assert.equal(g.count, 0);
      assert.deepEqual(g.summary, { n: 0, perDimension: {} });

      // Bad input is rejected.
      assert.equal((await post(`${base}/api/calibration`, { human: "Z9", estimate: { overall: "B2" } })).status, 400);
      assert.equal((await post(`${base}/api/calibration`, { human: {}, estimate: { overall: "B2" } })).status, 400); // no valid dim
      assert.equal((await post(`${base}/api/calibration`, { human: "B2" })).status, 400); // no estimate

      // Overall: three pairs — B2/B2 (exact), B2/C1 (off by one), B2+/C1 (off by half → match).
      await post(`${base}/api/calibration`, { human: "B2", estimate: { overall: "B2", score: 67, mode: "fluency+content" } });
      await post(`${base}/api/calibration`, { human: "C1", estimate: { overall: "B2", score: 70 } });
      const last = await (await post(`${base}/api/calibration`, { human: "C1", estimate: { overall: "B2+", score: 74 } })).json();

      assert.equal(last.ok, true);
      assert.equal(last.count, 3);
      const ov = last.summary.perDimension.overall;
      assert.equal(ov.n, 3);
      assert.equal(ov.exactPct, 67);    // 2 of 3 within 0.75
      assert.equal(ov.within1Pct, 100);
      assert.equal(ov.meanAbsSublevels, 0.5);

      // GET returns most-recent-first; human is the per-dimension object.
      g = await (await fetch(`${base}/api/calibration`)).json();
      assert.equal(g.recent[0].estimate, "B2+");
      assert.equal(g.recent[0].human.overall, "C1");
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("calibration: scores each CEFR dimension independently", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cal-"));
  const CALIBRATION_FILE = path.join(dir, "cal.jsonl");
  try {
    await withServer({ env: { CALIBRATION_FILE } }, async (base) => {
      // A full per-dimension rating against a full estimate (Table-3 shape).
      await post(`${base}/api/calibration`, {
        human: { overall: "B2", fluency: "B2", range: "B1", interaction: "B2" },
        estimate: { overall: "B2", dimensions: { fluency: { band: "B2" }, range: { band: "B2" }, accuracy: { band: "C1" }, coherence: { band: "B2" } } },
      });
      const g = await (await fetch(`${base}/api/calibration`)).json();
      const pd = g.summary.perDimension;
      assert.equal(pd.overall.exactPct, 100);   // B2 vs B2
      assert.equal(pd.fluency.exactPct, 100);    // B2 vs B2
      assert.equal(pd.range.exactPct, 0);         // est B2 vs human B1 → off by one
      assert.equal(pd.range.within1Pct, 100);
      assert.equal(pd.interaction, undefined);    // no machine band → human-only, not scored
      assert.equal(pd.accuracy, undefined);       // machine rated it but no human rating → not scored
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("generate-question: 501 without a key; returns a question with one", async () => {
  await withServer({ env: {} }, async (base) => {
    assert.equal((await post(`${base}/api/generate-question`, { level: "B1" })).status, 501);
  });
  let sent;
  const fetchImpl = async (_url, init) => {
    sent = JSON.parse(init.body);
    return new Response(JSON.stringify({ content: [{ type: "text", text: 'Sure:\n{"question":"Describe a decision you regret."}' }] }), { status: 200 });
  };
  await withServer({ env: { ANTHROPIC_API_KEY: "a" }, fetchImpl }, async (base) => {
    const r = await post(`${base}/api/generate-question`, { level: "C1", avoid: ["old one"] });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.question, "Describe a decision you regret.");
    assert.equal(body.level, "C1");
    assert.match(sent.messages[0].content, /CEFR C1/); // level is injected into the prompt
  });
});

test("static: page, sample fixture and the SDK bundle are served", async () => {
  await withServer({ env: {} }, async (base) => {
    assert.equal((await fetch(`${base}/`)).status, 200);
    assert.equal((await fetch(`${base}/fixtures/sample-result.json`)).status, 200);
    const sdk = await fetch(`${base}/vendor/speech-sdk.js`);
    assert.equal(sdk.status, 200);
    assert.ok((await sdk.text()).length > 100_000);
  });
});
