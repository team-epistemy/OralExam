// Demo client: routes + header names come from the backend's /config endpoint,
// so the frontend never hardcodes paths (single source of truth = backend).
const USER = "operator";
let CFG = null;
let SELECTED = [];

const $ = (id) => document.getElementById(id);
const orgName = () => $("org").value.trim();
const courseName = () => $("course").value.trim();

const ICONS = { md: "📝", markdown: "📝", txt: "📄", pptx: "📊",
                docx: "📃", pdf: "📕" };
const iconFor = (name) => ICONS[(name.split(".").pop() || "").toLowerCase()] || "📁";

function headers(extra) {
  const h = { [CFG.headers.userId]: USER, [CFG.headers.role]: $("role").value };
  return { ...h, ...(extra || {}) };
}
function fill(t, p) { return t.replace(/\{(\w+)\}/g, (_, k) => encodeURIComponent(p[k])); }
function humanSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

async function loadConfig() {
  if (!CFG) CFG = await (await fetch("/config")).json();
  return CFG;
}
async function j(method, url, body, extra) {
  const opts = { method, headers: headers(extra) };
  if (body) { opts.headers["content-type"] = "application/json";
              opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// ── toast ────────────────────────────────────────────────────────────────────
function toast(title, detail, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind === "err" ? " err" : "");
  el.innerHTML = `<div class="tt">${kind === "err" ? "✕" : "✓"} ${title}</div>` +
                 (detail ? `<div class="td">${detail}</div>` : "");
  $("toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0";
                     setTimeout(() => el.remove(), 300); }, 4200);
}

// ── file selection (drag/drop + browse + chips) ──────────────────────────────
function setFiles(list) {
  SELECTED = Array.from(list);
  const chips = $("chips");
  chips.innerHTML = "";
  SELECTED.forEach((f) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.innerHTML = `${iconFor(f.name)} ${f.name} <span class="sz">${humanSize(f.size)}</span>`;
    chips.appendChild(c);
  });
}

function wireDropzone() {
  const drop = $("drop"), input = $("files");
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => setFiles(input.files));
  ["dragenter", "dragover"].forEach((e) =>
    drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) =>
    drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => setFiles(ev.dataTransfer.files));
}

// ── upload pipeline (per file) ───────────────────────────────────────────────
async function run() {
  if (!SELECTED.length) { toast("No files selected", "Drop or browse first.", "err"); return; }
  await loadConfig();
  $("go").disabled = true;
  $("uploadsCard").style.display = "block";
  $("uploads").innerHTML = "";
  let ok = 0;
  for (const file of SELECTED) { if (await ingestFile(file)) ok++; }
  $("go").disabled = false;
  toast(`${ok}/${SELECTED.length} file(s) ingested`,
        ok === SELECTED.length ? "All ready." : "Some failed — see rows.",
        ok === SELECTED.length ? "ok" : "err");
  await loadLibrary();
}

const STEPS = { presign: 25, uploading: 50, registering: 65, ingesting: 80,
                ready: 100, failed: 100 };

function uploadRow(file) {
  const row = document.createElement("div");
  row.className = "uprow";
  row.innerHTML =
    `<span class="ic">${iconFor(file.name)}</span>
     <div style="flex:1;min-width:0">
       <div style="display:flex;gap:10px;align-items:center">
         <span class="name">${file.name}</span>
         <span class="sz">${humanSize(file.size)}</span>
         <span class="badge" style="margin-left:auto">queued</span>
       </div>
       <div class="bar"><i></i></div>
     </div>`;
  $("uploads").appendChild(row);
  const badge = row.querySelector(".badge"), bar = row.querySelector(".bar > i");
  return (state, dot) => {
    badge.textContent = state;
    badge.className = "badge " + (dot === "ok" ? "ready" : dot === "err" ? "failed" : "");
    bar.style.width = (STEPS[state] || 35) + "%";
    if (dot === "err") bar.style.background = "var(--err)";
  };
}

async function ingestFile(file) {
  const set = uploadRow(file);
  try {
    const mime = file.type || "application/octet-stream";
    set("presign");
    const pre = await j("POST", CFG.routes.presign, {
      org_name: orgName(), course_name: courseName(),
      file_name: file.name, mime_type: mime, bytes: file.size });
    set("uploading");
    await putToS3(pre.upload_url, file, mime);
    set("registering");
    await j("POST", fill(CFG.routes.register, { version_id: pre.material_version_id }),
            null, { [CFG.headers.orgName]: orgName() });
    set("ingesting");
    return await poll(pre.material_id, pre.material_version_id, set);
  } catch (e) {
    set("error: " + e.message.slice(0, 70), "err");
    return false;
  }
}

async function putToS3(url, file, mime) {
  const r = await fetch(url, { method: "PUT",
    headers: { "content-type": mime }, body: file });
  if (!r.ok) throw new Error(`S3 PUT ${r.status}`);
}

async function poll(materialId, versionId, set) {
  const url = fill(CFG.routes.listVersions, { material_id: materialId });
  for (let i = 0; i < 40; i++) {
    const versions = await j("GET", url, null, { [CFG.headers.orgName]: orgName() });
    const v = versions.find((x) => x.material_version_id === versionId);
    if (v) {
      set(v.status);
      if (v.status === "ready") { set("ready", "ok"); return true; }
      if (v.status === "failed") { set("failed", "err"); return false; }
    }
    await sleep(2000);
  }
  set("timeout", "err");
  return false;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── course hierarchy ─────────────────────────────────────────────────────────
async function loadLibrary() {
  await loadConfig();
  const tree = $("tree");
  tree.innerHTML = `<div class="empty">Loading…</div>`;
  const org = orgName(), course = courseName();
  try {
    const url = fill(CFG.routes.listMaterials, { org_name: org, course_name: course });
    const materials = await j("GET", url, null, { [CFG.headers.orgName]: org });
    tree.innerHTML = "";
    tree.appendChild(renderOrg(org, course, materials));
  } catch (e) {
    tree.innerHTML = `<pre style="color:#f43f5e">${e.message}</pre>`;
  }
}

function renderOrg(org, course, materials) {
  const orgEl = document.createElement("div");
  orgEl.className = "org";
  orgEl.innerHTML = `<div class="node">🏛️ <span>${org}</span></div>`;
  const courseEl = document.createElement("div");
  courseEl.className = "course";
  courseEl.innerHTML =
    `<div class="node">📚 <span>${course}</span>
       <span class="badge" style="margin-left:6px">${materials.length} material(s)</span></div>`;
  if (!materials.length) {
    const e = document.createElement("div");
    e.className = "empty"; e.style.marginLeft = "30px";
    e.textContent = "No materials yet — upload one above.";
    courseEl.appendChild(e);
  } else {
    materials.forEach((m) => courseEl.appendChild(renderMaterial(m)));
  }
  orgEl.appendChild(courseEl);
  return orgEl;
}

function renderMaterial(m) {
  const status = m.status || "—";
  const el = document.createElement("div");
  el.innerHTML =
    `<div class="mat">
       <span class="caret">▸</span>
       <span class="dot ${status === "ready" ? "ok" : status === "failed" ? "err" : ""}"></span>
       <span class="name">${iconFor(m.display_name)} ${m.display_name}</span>
       <span class="badge ${status}">${status}</span>
     </div>
     <div class="vers" style="display:none"></div>`;
  const vers = el.querySelector(".vers"), caret = el.querySelector(".caret");
  el.querySelector(".name").addEventListener("click", async () => {
    const open = vers.style.display !== "none";
    if (open) { vers.style.display = "none"; caret.classList.remove("open"); return; }
    caret.classList.add("open");
    vers.style.display = "block";
    vers.innerHTML = `<div class="empty">Loading versions…</div>`;
    await loadVersions(m.material_id, vers);
  });
  return el;
}

async function loadVersions(materialId, container) {
  try {
    const url = fill(CFG.routes.listVersions, { material_id: materialId });
    const versions = await j("GET", url, null, { [CFG.headers.orgName]: orgName() });
    container.innerHTML = "";
    if (!versions.length) { container.innerHTML = `<div class="empty">No versions.</div>`; return; }
    versions.forEach((v) => {
      const row = document.createElement("div");
      row.className = "ver";
      row.innerHTML =
        `<span class="dot ${v.status === "ready" ? "ok" : v.status === "failed" ? "err" : ""}"></span>
         <span>v${v.version_no} · ${v.file_name} · ${v.source_type}</span>
         <span class="badge ${v.status}" style="margin-left:6px">${v.status}</span>`;
      container.appendChild(row);
    });
  } catch (e) {
    container.innerHTML = `<pre style="color:#f43f5e">${e.message}</pre>`;
  }
}

$("go").addEventListener("click", run);
$("refresh").addEventListener("click", loadLibrary);
wireDropzone();
loadLibrary();
