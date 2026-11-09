// SDK Release Kit Settings UI. Plain JS, no build step.
"use strict";

const TOKEN = document.querySelector('meta[name="srk-token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
let STATUS = null;
let REPOS = null;
let OUTPUTS = null;
let KEYS = null;

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-SRK-Token": TOKEN },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail; } catch (_) { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail, null, 2));
  }
  return res.json();
}

function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " err" : "");
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), isError ? 8000 : 2500);
}

function guard(fn) {
  return async (...args) => {
    try { await fn(...args); } catch (e) { toast(e.message, true); }
  };
}

const getPath = (obj, dotted) => dotted.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
function setPath(obj, dotted, value) {
  const parts = dotted.split(".");
  let o = obj;
  for (const p of parts.slice(0, -1)) o = o[p] ??= {};
  o[parts.at(-1)] = value;
}
const nullIfEmpty = (v) => (v === "" ? null : v);

// Tabs -------------------------------------------------------------------------------------
$$("#tabs button").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + btn.dataset.tab));
    try { localStorage.setItem("srk-tab", btn.dataset.tab); } catch (_) { /* storage blocked */ }
  })
);

// Repositories -----------------------------------------------------------------------------
function renderRepos() {
  const tbody = $("#repos-table tbody");
  tbody.innerHTML = "";
  const ids = [...new Set([...Object.keys(STATUS.recipes), ...Object.keys(REPOS.repos)])];
  for (const id of ids) {
    const entry = REPOS.repos[id] || { path: "", enabled: false };
    const recipe = STATUS.recipes[id];
    const tr = document.createElement("tr");
    tr.dataset.id = id;
    tr.innerHTML = `
      <td><input type="checkbox" class="enabled"></td>
      <td><strong></strong><div class="muted small"></div></td>
      <td class="path"><input class="path-input" placeholder="~/path/to/checkout"></td>
      <td class="check"></td>`;
    $(".enabled", tr).checked = !!entry.enabled;
    $("strong", tr).textContent = id;
    $(".small", tr).textContent = recipe ? `${recipe.platform}. ${recipe.label}` : "not in recipes (kept)";
    const input = $(".path-input", tr);
    input.value = entry.path || "";
    const check = guard(async () => {
      const cell = $(".check", tr);
      if (!input.value.trim()) { cell.textContent = ""; return; }
      const r = await api("POST", "/api/validate/path", { path: input.value });
      cell.textContent = r.ok ? "✓ " + r.detail : "✗ " + r.detail;
      cell.className = "check " + (r.ok ? "ok" : "bad");
    });
    input.addEventListener("change", check);
    check();
    tbody.appendChild(tr);
  }
  const t = REPOS.tooling || {};
  for (const input of $$("#tooling-form input")) {
    const v = t[input.name];
    input.value = Array.isArray(v) ? v.join(", ") : v || "";
  }
}

$("#save-repos").addEventListener("click", guard(async () => {
  const repos = { ...REPOS.repos };
  for (const tr of $$("#repos-table tbody tr")) {
    repos[tr.dataset.id] = {
      ...(repos[tr.dataset.id] || {}),
      path: $(".path-input", tr).value.trim(),
      enabled: $(".enabled", tr).checked,
    };
  }
  const tooling = { ...(REPOS.tooling || {}) };
  for (const input of $$("#tooling-form input")) {
    tooling[input.name] = input.name === "extra_path"
      ? input.value.split(",").map((s) => s.trim()).filter(Boolean)
      : nullIfEmpty(input.value.trim());
  }
  REPOS = await api("PUT", "/api/settings/repos", { repos, tooling });
  renderRepos();
  renderRunSdks();
  toast("Repositories saved");
}));

// Outputs ----------------------------------------------------------------------------------
function renderOutputs() {
  for (const el of $$("#tab-outputs [data-k]")) {
    const v = getPath(OUTPUTS, el.dataset.k);
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v ?? "";
  }
}

$("#save-outputs").addEventListener("click", guard(async () => {
  const body = structuredClone(OUTPUTS);
  for (const el of $$("#tab-outputs [data-k]")) {
    setPath(body, el.dataset.k, el.type === "checkbox" ? el.checked : nullIfEmpty(el.value.trim()));
  }
  OUTPUTS = await api("PUT", "/api/settings/outputs", body);
  renderOutputs();
  renderRunDestinations();
  toast("Outputs saved");
}));

// Keys -------------------------------------------------------------------------------------
const SECRET_LABELS = {
  "android.store_password": "Keystore password",
  "android.key_password": "Key password (empty uses keystore password)",
  "testflight.key_id": "ASC key id",
  "testflight.issuer_id": "ASC issuer id",
};

function renderKeys() {
  const { settings, status } = KEYS;
  const chips = $("#keys-status");
  chips.innerHTML = "";
  for (const [name, st] of Object.entries(status)) {
    const c = document.createElement("span");
    c.className = "chip " + (st.configured ? "ok" : "bad");
    c.textContent = `${name}: ${st.configured ? "configured" : "missing"}`;
    c.title = st.detail;
    chips.appendChild(c);
  }
  for (const el of $$("#tab-keys [data-key]")) {
    const v = getPath(settings, el.dataset.key);
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v ?? "";
  }
  for (const box of $$("#tab-keys [data-secret]")) {
    const field = box.dataset.secret;
    const ref = getPath(settings, field) || { source: "none", name: "" };
    const st = status[field] || {};
    box.innerHTML = `
      <div class="muted">${SECRET_LABELS[field]} <span class="${st.configured ? "ok" : "bad"}">(${st.configured ? "configured" : "missing"})</span></div>
      <div class="row">
        <select class="src"><option value="keychain">OS keychain</option><option value="env">Environment variable</option><option value="none">Not set</option></select>
        <input class="ref-name" placeholder="entry or variable name">
      </div>
      <div class="row keychain-row">
        <input class="value" type="password" autocomplete="new-password" placeholder="new value (stored in keychain, never shown)">
        <button class="store">Store in keychain</button>
      </div>`;
    $(".src", box).value = ref.source;
    $(".ref-name", box).value = ref.name;
    const syncRow = () => ($(".keychain-row", box).hidden = $(".src", box).value !== "keychain");
    $(".src", box).addEventListener("change", syncRow);
    syncRow();
    $(".store", box).addEventListener("click", guard(async () => {
      const value = $(".value", box).value;
      if (!value) { toast("Enter a value first", true); return; }
      KEYS = await api("POST", "/api/keys/secret", { field, value });
      renderKeys();
      toast("Stored in keychain");
    }));
  }
}

$$("#tab-keys [data-upload]").forEach((input) =>
  input.addEventListener("change", guard(async () => {
    const file = input.files[0];
    if (!file) return;
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    KEYS = await api("POST", "/api/keys/upload", {
      field: input.dataset.upload, filename: file.name, content_b64: btoa(bin),
    });
    input.value = "";
    renderKeys();
    toast(`${file.name} saved to the private keys folder`);
  }))
);

$("#save-keys").addEventListener("click", guard(async () => {
  const body = structuredClone(KEYS.settings);
  for (const el of $$("#tab-keys [data-key]")) {
    setPath(body, el.dataset.key, el.type === "checkbox" ? el.checked : nullIfEmpty(el.value.trim()));
  }
  for (const box of $$("#tab-keys [data-secret]")) {
    setPath(body, box.dataset.secret, {
      source: $(".src", box).value, name: $(".ref-name", box).value.trim(),
    });
  }
  KEYS = await api("PUT", "/api/settings/keys", body);
  renderKeys();
  toast("Keys saved");
}));

// Custom -----------------------------------------------------------------------------------
$("#save-custom").addEventListener("click", guard(async () => {
  const r = await api("PUT", "/api/settings/custom", { text: $("#custom-text").value });
  $("#custom-text").value = r.text;
  toast(`Custom data saved (${r.keys.length} top-level keys)`);
}));

// Run --------------------------------------------------------------------------------------
function renderRunSdks() {
  const box = $("#run-sdks");
  box.innerHTML = "";
  for (const id of Object.keys(STATUS.recipes)) {
    const entry = REPOS.repos[id];
    const ready = entry && entry.enabled && entry.path;
    const label = document.createElement("label");
    label.className = "inline";
    label.innerHTML = `<input type="checkbox" name="sdk"> <span></span>`;
    $("input", label).value = id;
    $("input", label).checked = !!ready;
    $("span", label).textContent = id + (ready ? "" : " (no repo)");
    box.appendChild(label);
  }
}

function renderRunDestinations() {
  for (const el of $$('input[name="dest"]')) el.checked = !!(OUTPUTS[el.value] || {}).enabled;
}

async function startRun(dryRun) {
  const body = {
    sdks: $$('input[name="sdk"]:checked').map((e) => e.value),
    artifacts: $('input[name="artifacts"]:checked').value,
    destinations: $$('input[name="dest"]:checked').map((e) => e.value),
    dry_run: dryRun,
    skip_upload: $("#skip-upload").checked,
  };
  if (!body.sdks.length) { toast("Select at least one SDK", true); return; }
  const { id } = await api("POST", "/api/runs", body);
  const logEl = $("#run-log");
  logEl.textContent = "";
  $("#run-state").textContent = dryRun ? "planning..." : "running...";
  $$("#dry-run, #start-run").forEach((b) => (b.disabled = true));
  try {
    const res = await fetch(`/api/runs/${id}/log`, { headers: { "X-SRK-Token": TOKEN } });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 8;
      logEl.textContent += dec.decode(value, { stream: true });
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
    }
    const st = await api("GET", `/api/runs/${id}`);
    const ok = st.result && (st.result.dry_run || st.result.ok);
    $("#run-state").textContent = st.error ? "error" : dryRun ? "plan ready" : ok ? "finished" : "finished with problems";
  } finally {
    $$("#dry-run, #start-run").forEach((b) => (b.disabled = false));
  }
}
$("#dry-run").addEventListener("click", guard(() => startRun(true)));
$("#start-run").addEventListener("click", guard(() => startRun(false)));

// Boot -------------------------------------------------------------------------------------
guard(async () => {
  [STATUS, REPOS, OUTPUTS, KEYS] = await Promise.all([
    api("GET", "/api/status"),
    api("GET", "/api/settings/repos"),
    api("GET", "/api/settings/outputs"),
    api("GET", "/api/settings/keys"),
  ]);
  $("#version").textContent = "v" + STATUS.version;
  $("#repos-file").textContent = STATUS.files.repos;
  if (STATUS.recipe_error) toast(STATUS.recipe_error, true);
  $("#custom-text").value = (await api("GET", "/api/settings/custom")).text;
  renderRepos();
  renderOutputs();
  renderKeys();
  renderRunSdks();
  renderRunDestinations();
  let tab = null;
  try { tab = localStorage.getItem("srk-tab"); } catch (_) { /* storage blocked */ }
  if (tab) $(`#tabs button[data-tab="${tab}"]`)?.click();
})();
