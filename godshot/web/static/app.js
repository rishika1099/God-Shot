// S09 (Siva): run picker, verify-loop inspector, synced top-down map editor. Vanilla JS, no build step.
// Conventions (contracts.md): x → right, y → forward; yaw 0 = +y, positive = camera turned to ITS LEFT.
"use strict";

const $ = (id) => document.getElementById(id);
const S = {
  runId: null, run: null, orig: null, scene: null, t: 0, playing: false, lastTick: 0,
  round: null, sel: { cam: null }, drag: null, view: null, offline: false, jobTimer: null,
};
const COLORS = { bg: "#2b2b2b", grid: "#3a3a3a", room: "#bdbdbd", cam: "#5ad65a", ghost: "#8a8a8a" };

// ------------------------------------------------------------------ math (mirrors godshot/geometry.py)
const wrap180 = (a) => ((((a + 180) % 360) + 360) % 360) - 180;
const lerp = (a, b, f) => a + (b - a) * f;
const lerpAngle = (a, b, f) => wrap180(a + wrap180(b - a) * f);
const rad = (d) => (d * Math.PI) / 180;
const fwdVec = (yaw) => [-Math.sin(rad(yaw)), Math.cos(rad(yaw))];

function bracket(keys, t) {
  if (keys.length === 1 || t <= keys[0].t) return [keys[0], keys[0], 0];
  const last = keys[keys.length - 1];
  if (t >= last.t) return [last, last, 0];
  for (let j = 1; j < keys.length; j++) {
    if (keys[j].t >= t) {
      const a = keys[j - 1], b = keys[j], span = b.t - a.t;
      return [a, b, span <= 0 ? 0 : (t - a.t) / span];
    }
  }
  return [last, last, 0];
}
const sortedKeys = (track) => [...track].sort((a, b) => a.t - b.t);
function camAt(scene, t) {
  const [a, b, f] = bracket(sortedKeys(scene.camera.track), t);
  return { x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f), yaw: lerpAngle(a.yaw_deg, b.yaw_deg, f) };
}
function actorAt(actor, t) {
  const [a, b, f] = bracket(sortedKeys(actor.track), t);
  return { x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f), facing: lerpAngle(a.facing_deg, b.facing_deg, f),
           visible: f <= 0.5 ? a.visible : b.visible };
}
const mmss = (t) => {
  const ms = Math.max(0, Math.round(t * 1000));
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), r = ms % 1000;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(r).padStart(3, "0")}`;
};
const clone = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body?.detail || body || res.statusText);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify(data) });
function msg(text, err = false) { $("msg").textContent = text; $("msg").className = "msg" + (err ? " err" : ""); }

// ------------------------------------------------------------------ runs
async function loadRuns(select) {
  const [status, runs] = await Promise.all([api("/api/status"), api("/api/runs")]);
  S.offline = status.offline;
  const sel = $("runSelect");
  sel.innerHTML = runs.length ? "" : "<option>no runs yet</option>";
  for (const r of runs) {
    const o = document.createElement("option");
    const rates = r.pass_rates.filter((x) => x != null).map((x) => Math.round(x * 100) + "%").join(" → ");
    o.value = r.clip_id;
    o.textContent = `${r.clip_id}${r.mock ? " (mock)" : ""}${rates ? "  · " + rates : ""}`;
    sel.appendChild(o);
  }
  for (const id of ["uploadLabel", "rerender", "textEdit", "reshoot"]) $(id).classList.toggle("disabled", S.offline);
  ["rerender", "textEdit", "reshoot"].forEach((id) => ($(id).disabled = S.offline));
  $("upload").disabled = S.offline;
  const want = select || (runs.find((r) => r.clip_id === S.runId) ? S.runId : runs[0]?.clip_id);
  if (want) { sel.value = want; await openRun(want); }
  badges();
}

function badges(extra = "") {
  let html = "";
  if (S.offline) html += '<span class="badge offline">OFFLINE DEMO</span>';
  if (S.run?.mock) html += '<span class="badge mock">MOCK DATA</span>';
  $("badges").innerHTML = html + extra;
}

async function openRun(id) {
  S.runId = id;
  S.run = await api(`/api/run/${encodeURIComponent(id)}`);
  S.orig = S.run.scene;
  S.scene = clone(S.run.scene_edit || S.run.scene);
  S.t = 0; S.sel.cam = null;
  const dur = S.orig.clip.duration_s;
  $("time").max = dur; $("t1").value = Math.min(4, dur);
  $("fov").value = S.scene.camera.hfov_deg; $("fovVal").textContent = Math.round(S.scene.camera.hfov_deg);
  const tgt = $("target"); tgt.innerHTML = "";
  for (const e of [...S.scene.actors, ...S.scene.objects]) {
    const o = document.createElement("option"); o.value = e.id; o.textContent = `${e.id}: ${e.visual_description}`;
    tgt.appendChild(o);
  }
  setVideo(S.run.reel_edit_url || S.run.reel_url || S.run.clip_url);
  const rounds = Object.keys(S.run.checks).map(Number).sort((a, b) => a - b);
  S.round = rounds.length ? rounds[rounds.length - 1] : null;
  renderInspector();
  badges();
  msg(S.run.scene_edit ? "Loaded your latest saved edit (dashed grey = original path)." : "");
}

function setVideo(url) {
  const v = $("video");
  if (url) { v.src = url; v.style.display = "block"; $("noVideo").style.display = "none"; }
  else { v.removeAttribute("src"); v.load(); v.style.display = "none"; $("noVideo").style.display = "flex"; }
}

// ------------------------------------------------------------------ inspector
function renderInspector() {
  const log = S.run.loop_log || [];
  const tabs = $("rounds"); tabs.innerHTML = "";
  const rounds = Object.keys(S.run.checks).map(Number).sort((a, b) => a - b);
  for (const r of rounds) {
    const entry = log.find((e) => e.round === r) || {};
    const b = document.createElement("span");
    b.className = "tab" + (r === S.round ? " active" : "");
    const rate = entry.pass_rate != null ? Math.round(entry.pass_rate * 100) + "%" : "";
    b.innerHTML = `round ${r}${entry.accepted === false ? " ✗" : ""}<span class="rate">${rate}</span>`;
    b.onclick = () => { S.round = r; renderInspector(); };
    tabs.appendChild(b);
  }
  const checks = S.round != null ? S.run.checks[S.round] || [] : [];
  const n = (v) => checks.filter((c) => (c.result?.verdict || "none") === v).length;
  $("roundStats").textContent = checks.length
    ? `${checks.length} checks · ✔ ${n("pass")} · ✘ ${n("fail")} · ? ${n("uncertain") + n("none")}` +
      (log.find((e) => e.round === S.round)?.latency_s ? ` · ${log.find((e) => e.round === S.round).latency_s}s` : "")
    : "No checks yet for this run.";
  const ul = $("checks"); ul.innerHTML = "";
  const order = { fail: 0, uncertain: 1, none: 1, pass: 2 };
  for (const c of [...checks].sort((a, b) => order[a.result?.verdict || "none"] - order[b.result?.verdict || "none"])) {
    const v = c.result?.verdict || "none";
    const li = document.createElement("li");
    const p = c.result?.p_yes != null ? ` p=${c.result.p_yes.toFixed(2)}` : "";
    li.innerHTML = `<span class="v-${v}">${{ pass: "✔", fail: "✘" }[v] || "?"}</span>` +
      `<span class="tpl">${c.template} ${c.mmss}</span><span>${escapeHtml(c.question.instructions)}` +
      `<span class="tpl"> · expected ${c.expected}${p}</span></span>`;
    li.onmouseenter = (ev) => showThumb(c.input_ref.paths[0], ev);
    li.onmousemove = (ev) => moveThumb(ev);
    li.onmouseleave = hideThumb;
    li.onclick = () => seek(c.t);
    ul.appendChild(li);
  }
  const lc = $("lowconf"); lc.innerHTML = "";
  for (const l of S.scene.low_confidence || []) {
    const li = document.createElement("li");
    li.textContent = `${l.entity_id} @ ${mmss(l.t0)}–${mmss(l.t1)}  (${l.failed_checks.length} failed check(s))`;
    li.onclick = () => seek(l.t0);
    lc.appendChild(li);
  }
  if (!lc.children.length) lc.innerHTML = "<li style='color:#9a9a9a'>nothing flagged</li>";
}
const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
function showThumb(path, ev) { const im = $("thumb"); im.src = `/frame?path=${encodeURIComponent(path)}`;
  im.onerror = () => (im.style.display = "none"); im.style.display = "block"; moveThumb(ev); }
function moveThumb(ev) { const im = $("thumb"); im.style.left = ev.clientX - 340 + "px"; im.style.top = ev.clientY + 12 + "px"; }
function hideThumb() { $("thumb").style.display = "none"; }

// ------------------------------------------------------------------ time
function seek(t) {
  S.t = Math.max(0, Math.min(t, S.orig?.clip.duration_s || 0));
  const v = $("video");
  if (v.src && Math.abs(v.currentTime - S.t) > 0.05) v.currentTime = S.t;
}
$("time").oninput = (e) => seek(parseFloat(e.target.value));
$("play").onclick = () => {
  const v = $("video");
  if (v.src) { v.paused ? v.play() : v.pause(); return; }
  S.playing = !S.playing; S.lastTick = performance.now();
};
$("video").onplay = () => ($("play").textContent = "❚❚");
$("video").onpause = () => ($("play").textContent = "▶");

function tick(now) {
  const v = $("video");
  if (S.orig) {
    const dur = S.orig.clip.duration_s;
    if (v.src && !isNaN(v.duration)) S.t = Math.min(v.currentTime, dur);
    else if (S.playing) { S.t += (now - S.lastTick) / 1000; if (S.t > dur) S.t = 0; $("play").textContent = "❚❚"; }
    else if (!v.src) $("play").textContent = "▶";
    S.lastTick = now;
    $("time").value = S.t; $("timeLabel").textContent = mmss(S.t);
    const seg = (S.scene.camera.motion_segments || []).find((s) => S.t >= s.t0 && S.t <= s.t1);
    $("segLabel").textContent = seg
      ? [seg.translation, seg.rotation].filter((x) => x && x !== "none" && x !== "static").join(" · ").replaceAll("_", " ")
          .toUpperCase() || "STATIC" : "";
    draw();
  }
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------------ map drawing
const canvas = $("map"), ctx = canvas.getContext("2d");

function fitView(scene) {
  const pts = [...(scene.room.outline || [])];
  for (const o of scene.objects) pts.push(o.center);
  for (const a of scene.actors) for (const k of a.track) pts.push([k.x, k.y]);
  for (const k of scene.camera.track) pts.push([k.x, k.y]);
  if (S.orig) for (const k of S.orig.camera.track) pts.push([k.x, k.y]);
  let [x0, x1, y0, y1] = [Infinity, -Infinity, Infinity, -Infinity];
  for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  if (!isFinite(x0)) [x0, x1, y0, y1] = [-5, 5, 0, 10];
  const mx = Math.max(1, (x1 - x0) * 0.12), my = Math.max(1, (y1 - y0) * 0.12);
  x0 -= mx; x1 += mx; y0 -= my; y1 += my;
  const W = canvas.width, H = canvas.height, s = Math.min(W / (x1 - x0), H / (y1 - y0));
  const ox = (W - s * (x1 - x0)) / 2, oy = (H - s * (y1 - y0)) / 2;
  return { s, toPx: (x, y) => [ox + (x - x0) * s, H - oy - (y - y0) * s],
           toWorld: (px, py) => [x0 + (px - ox) / s, y0 + (H - oy - py) / s], x0, x1, y0, y1 };
}

function resize() {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
}
window.addEventListener("resize", resize);

const lowConfAt = (id, t) => (S.scene.low_confidence || []).some((l) => l.entity_id === id && t >= l.t0 && t <= l.t1);

function draw() {
  const sc = S.scene; if (!sc) return;
  if (!S.drag) S.view = fitView(sc);
  const V = S.view, P = V.toPx, dpr = window.devicePixelRatio || 1;
  ctx.fillStyle = COLORS.bg; ctx.fillRect(0, 0, canvas.width, canvas.height);
  // grid
  ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1;
  for (let x = Math.ceil(V.x0 * 2) / 2; x <= V.x1; x += 0.5) { line(P(x, V.y0), P(x, V.y1)); }
  for (let y = Math.ceil(V.y0 * 2) / 2; y <= V.y1; y += 0.5) { line(P(V.x0, y), P(V.x1, y)); }
  // room
  const room = sc.room.outline || [];
  if (room.length > 2) { ctx.strokeStyle = COLORS.room; ctx.lineWidth = 2 * dpr; poly(room.map((p) => P(...p)), true); }
  // objects
  for (const o of sc.objects) drawObject(o, P, V.s, dpr);
  // ghost (original) and current camera paths
  if (S.orig && JSON.stringify(S.orig.camera.track) !== JSON.stringify(sc.camera.track)) {
    ctx.setLineDash([6 * dpr, 6 * dpr]); ctx.strokeStyle = COLORS.ghost; ctx.lineWidth = 2 * dpr;
    poly(sortedKeys(S.orig.camera.track).map((k) => P(k.x, k.y))); ctx.setLineDash([]);
  }
  ctx.strokeStyle = "rgba(90,214,90,.55)"; ctx.lineWidth = 2 * dpr;
  poly(sortedKeys(sc.camera.track).map((k) => P(k.x, k.y)));
  // actors (trail + body + key dots)
  for (const a of sc.actors) drawActor(a, P, V.s, dpr);
  // camera keys
  sortedKeys(sc.camera.track).forEach((k, i) => {
    const [px, py] = P(k.x, k.y);
    ctx.fillStyle = i === S.sel.cam ? "#ffffff" : COLORS.cam;
    dot(px, py, (i === S.sel.cam ? 6 : 4) * dpr);
  });
  // camera now + FOV wedge
  const cam = camAt(sc, S.t), hf = sc.camera.hfov_deg, len = 4;
  const low = lowConfAt("camera", S.t);
  const [cx, cy] = P(cam.x, cam.y);
  const l = fwdVec(cam.yaw + hf / 2), r = fwdVec(cam.yaw - hf / 2);
  ctx.fillStyle = `rgba(90,214,90,${low ? 0.1 : 0.22})`;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(...P(cam.x + l[0] * len, cam.y + l[1] * len));
  ctx.lineTo(...P(cam.x + r[0] * len, cam.y + r[1] * len)); ctx.closePath(); ctx.fill();
  ctx.fillStyle = COLORS.cam; roundRect(cx - 8 * dpr, cy - 8 * dpr, 16 * dpr, 16 * dpr, 4 * dpr);
  // yaw handle for the selected key
  if (S.sel.cam != null) {
    const k = sortedKeys(sc.camera.track)[S.sel.cam];
    if (k) { const h = yawHandle(k); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 * dpr;
      line(P(k.x, k.y), h); ctx.beginPath(); ctx.arc(h[0], h[1], 7 * dpr, 0, 2 * Math.PI); ctx.stroke(); }
  }
}

function drawObject(o, P, s, dpr) {
  const [px, py] = P(...o.center), w = o.size[0] * s, h = o.size[1] * s;
  const low = lowConfAt(o.id, S.t);
  ctx.save(); ctx.translate(px, py); ctx.rotate(-rad(o.rotation_deg || 0));
  ctx.globalAlpha = low ? 0.45 : 0.85; ctx.fillStyle = o.color || "#888";
  if (o.shape === "circle") { ctx.beginPath(); ctx.arc(0, 0, w / 2, 0, 2 * Math.PI); ctx.fill(); }
  else ctx.fillRect(-w / 2, -h / 2, Math.max(w, 3), Math.max(h, 3));
  ctx.restore(); ctx.globalAlpha = 1;
  label(`${o.type}${low ? " ?" : ""}`, px, py - Math.max(h, w) / 2 - 6 * dpr, dpr, low);
}

function drawActor(a, P, s, dpr) {
  const keys = sortedKeys(a.track);
  ctx.strokeStyle = (a.color || "#e74c3c") + "55"; ctx.lineWidth = 2 * dpr;
  poly(keys.map((k) => P(k.x, k.y)));
  ctx.fillStyle = (a.color || "#e74c3c") + "99";
  for (const k of keys) { const [x, y] = P(k.x, k.y); dot(x, y, 2.5 * dpr); }
  const now = actorAt(a, S.t); if (!now.visible) return;
  const [px, py] = P(now.x, now.y), low = lowConfAt(a.id, S.t), f = fwdVec(now.facing);
  ctx.save(); ctx.translate(px, py); ctx.rotate(-rad(now.facing));
  ctx.globalAlpha = low ? 0.5 : 1; ctx.fillStyle = a.color || "#e74c3c";
  ctx.beginPath(); ctx.ellipse(0, 0, 0.32 * s, 0.16 * s, 0, 0, 2 * Math.PI); ctx.fill();
  ctx.fillStyle = "#f5d0b0"; ctx.beginPath(); ctx.arc(0, 0, 0.11 * s, 0, 2 * Math.PI); ctx.fill();
  ctx.restore(); ctx.globalAlpha = 1;
  ctx.strokeStyle = a.color || "#e74c3c"; line([px, py], P(now.x + f[0] * 0.5, now.y + f[1] * 0.5));
  label(`${a.id}${low ? " ?" : ""}`, px, py - 0.4 * s - 4 * dpr, dpr, low);
}

const yawHandle = (k) => { const f = fwdVec(k.yaw_deg); return S.view.toPx(k.x + f[0] * 1.2, k.y + f[1] * 1.2); };
function line(a, b) { ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); }
function poly(pts, close = false) { if (pts.length < 2) return; ctx.beginPath(); ctx.moveTo(...pts[0]);
  for (const p of pts.slice(1)) ctx.lineTo(...p); if (close) ctx.closePath(); ctx.stroke(); }
function dot(x, y, r) { ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); }
function roundRect(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); ctx.fill(); }
function label(text, x, y, dpr, warn) { ctx.font = `${11 * dpr}px system-ui`; ctx.textAlign = "center";
  ctx.fillStyle = warn ? "#f0c05a" : "#d8d8d8"; ctx.fillText(text, x, y); }

// ------------------------------------------------------------------ map editing (drag)
function hit(px, py) {
  const dpr = window.devicePixelRatio || 1, R = 9 * dpr, P = S.view.toPx, sc = S.scene;
  const near = (x, y) => Math.hypot(px - x, py - y) < R;
  if (S.sel.cam != null) { const k = sortedKeys(sc.camera.track)[S.sel.cam];
    if (k && near(...yawHandle(k))) return { kind: "yaw", index: S.sel.cam }; }
  const ck = sortedKeys(sc.camera.track);
  for (let i = 0; i < ck.length; i++) if (near(...P(ck[i].x, ck[i].y))) return { kind: "cam", index: i };
  for (const a of sc.actors) { const ks = sortedKeys(a.track);
    for (let i = 0; i < ks.length; i++) if (near(...P(ks[i].x, ks[i].y))) return { kind: "actor", id: a.id, index: i }; }
  for (const o of sc.objects) { const [x, y] = P(...o.center);
    if (Math.hypot(px - x, py - y) < Math.max(R, (Math.max(...o.size) * S.view.s) / 2)) return { kind: "obj", id: o.id }; }
  return null;
}
function evPx(ev) { const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  return [(ev.clientX - r.left) * dpr, (ev.clientY - r.top) * dpr]; }

canvas.addEventListener("pointerdown", (ev) => {
  if (!S.scene) return;
  const h = hit(...evPx(ev));
  if (h?.kind === "cam") { S.sel.cam = h.index; seek(sortedKeys(S.scene.camera.track)[h.index].t); }
  else if (!h) S.sel.cam = null;
  if (h) { S.drag = h; canvas.setPointerCapture(ev.pointerId); }
});
canvas.addEventListener("pointermove", (ev) => {
  if (!S.drag) return;
  const [wx, wy] = S.view.toWorld(...evPx(ev)), d = S.drag, sc = S.scene, r = (v) => Math.round(v * 100) / 100;
  if (d.kind === "cam") { sc.camera.track = sortedKeys(sc.camera.track); Object.assign(sc.camera.track[d.index], { x: r(wx), y: r(wy) }); }
  else if (d.kind === "yaw") { sc.camera.track = sortedKeys(sc.camera.track); const k = sc.camera.track[d.index];
    k.yaw_deg = r(wrap180((Math.atan2(-(wx - k.x), wy - k.y) * 180) / Math.PI)); }
  else if (d.kind === "actor") { const a = sc.actors.find((x) => x.id === d.id); a.track = sortedKeys(a.track);
    Object.assign(a.track[d.index], { x: r(wx), y: r(wy) }); }
  else if (d.kind === "obj") { sc.objects.find((o) => o.id === d.id).center = [r(wx), r(wy)]; }
  markDirty();
});
canvas.addEventListener("pointerup", () => (S.drag = null));

function markDirty() { msg("Edited (not saved). Press Re-render to save + render."); }

$("fov").oninput = (e) => { S.scene.camera.hfov_deg = parseFloat(e.target.value);
  $("fovVal").textContent = e.target.value; markDirty(); };

// ------------------------------------------------------------------ edit actions
$("applyPreset").onclick = async () => {
  const edit = { op: "preset", move: $("preset").value, t0: parseFloat($("t0").value), t1: parseFloat($("t1").value),
                 amount: $("amount").value === "" ? null : parseFloat($("amount").value),
                 target: $("preset").value.startsWith("arc") ? $("target").value || null : null };
  try { S.scene = (await post(`/api/run/${S.runId}/apply`, { scene: S.scene, edit })).scene; S.sel.cam = null;
    markDirty(); seek(edit.t0); }
  catch (e) { msg(e.message, true); }
};

async function saveAndRender(promise, label) {
  msg(`${label}…`);
  const t0 = performance.now();
  try {
    const res = await promise;
    S.scene = res.scene;
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    if (res.reel_url) { setVideo(res.reel_url); msg(`Saved ${res.saved}; new reel in ${secs}s.`); }
    else msg(`Saved ${res.saved} in ${secs}s. ${res.note || ""}`.trim());
  } catch (e) { msg(e.message, true); }
}
$("rerender").onclick = () => saveAndRender(post(`/api/run/${S.runId}/edit`, { scene: S.scene }), "Saving + rendering");
$("textEdit").onclick = () => {
  const instruction = $("instruction").value.trim(); if (!instruction) return;
  saveAndRender(post(`/api/run/${S.runId}/text-edit`, { instruction, scene: S.scene }), "Asking Hydrogen");
};
$("reset").onclick = () => { S.scene = clone(S.orig); S.sel.cam = null; $("fov").value = S.scene.camera.hfov_deg;
  $("fovVal").textContent = Math.round(S.scene.camera.hfov_deg); setVideo(S.run.reel_url || S.run.clip_url);
  msg("Back to the verified scene (edits on disk are kept)."); };
$("reshoot").onclick = async () => {
  try { const { job_id } = await post(`/api/run/${S.runId}/reshoot`, { scene: S.scene }); watchJob(job_id); }
  catch (e) { msg(e.message, true); }
};

// ------------------------------------------------------------------ upload + jobs
$("upload").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    msg(`Uploading ${f.name}…`);
    const { job_id } = await api(`/api/run?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
    watchJob(job_id);
  } catch (err) { msg(err.message, true); }
  e.target.value = "";
};

function watchJob(id) {
  clearInterval(S.jobTimer);
  S.jobTimer = setInterval(async () => {
    try {
      const j = await api(`/api/job/${id}`);
      const rates = (j.loop_log || []).map((r) => (r.pass_rate != null ? Math.round(r.pass_rate * 100) + "%" : "–"));
      badges(`<span class="badge job">${j.kind}: ${j.stage}${rates.length ? " · " + rates.join(" → ") : ""}</span>`);
      if (j.status !== "running") {
        clearInterval(S.jobTimer);
        if (j.status === "error") msg(`${j.kind} failed: ${j.error}`, true);
        else if (j.kind === "reshoot") { setVideo(j.reshoot_url); msg(`Re-shoot ready. Conformance: ${
          j.conformance?.score != null ? Math.round(j.conformance.score * 100) + "%" : "n/a"} (AI-generated)`); }
        else { msg(`Run finished in ${j.elapsed_s}s.`); await loadRuns(j.clip_id); }
      }
    } catch (e) { clearInterval(S.jobTimer); msg(e.message, true); }
  }, 1000);
}

$("runSelect").onchange = (e) => openRun(e.target.value);

// ------------------------------------------------------------------ boot
resize();
loadRuns().catch((e) => msg(e.message, true));
requestAnimationFrame(tick);
