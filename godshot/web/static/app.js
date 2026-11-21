// S09 (Siva) + Interactive Blocking Playground: run picker, verify-loop inspector, synced top-down map playground.
// Vanilla JS, no build step.
// Conventions (contracts.md): x → right, y → forward; yaw 0 = +y, positive = camera turned to ITS LEFT.
"use strict";

const $ = (id) => document.getElementById(id);
const S = {
  runId: null, run: null, orig: null, scene: null, t: 0, playing: false, lastTick: 0,
  round: null, sel: { cam: null }, drag: null, hover: null, view: null, offline: false, jobTimer: null,
  tool: "select", loop: true, orbitDir: 1, orbitSweep: 360, orbitRadius: null, lastOrbitAnchor: null,
  drawStroke: null, orbitDraft: null, undo: [], redo: [],
};
const COLORS = {
  bg: "#0b0e14", gridMinor: "#141925", gridMajor: "#1d2435", roomFill: "rgba(148,163,184,0.05)",
  room: "#94a3b8", cam: "#3ee88b", camDim: "rgba(62,232,139,0.55)", ghost: "#7c879e", aim: "#4f9eff",
};

// ------------------------------------------------------------------ math (mirrors godshot/geometry.py)
const wrap180 = (a) => ((((a + 180) % 360) + 360) % 360) - 180;
const lerp = (a, b, f) => a + (b - a) * f;
const lerpAngle = (a, b, f) => wrap180(a + wrap180(b - a) * f);
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
const fwdVec = (yaw) => [-Math.sin(rad(yaw)), Math.cos(rad(yaw))];
const yawTowards = (cx, cy, tx, ty) => wrap180(deg(Math.atan2(-(tx - cx), ty - cy)));
const roundN = (v, n = 3) => Math.round(v * 10 ** n) / 10 ** n;

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
function entityPosAt(scene, id, t) {
  if (!scene || !id) return null;
  const a = scene.actors.find((x) => x.id === id);
  if (a) { const p = actorAt(a, t); return { x: p.x, y: p.y, kind: "actor", id: a.id }; }
  const o = scene.objects.find((x) => x.id === id);
  if (o) return { x: o.center[0], y: o.center[1], kind: "obj", id: o.id };
  return null;
}
function lockedTargetId() {
  return $("aimTarget")?.value || S.scene?.actors[0]?.id || S.scene?.objects[0]?.id || null;
}
const mmss = (t) => {
  const ms = Math.max(0, Math.round(t * 1000));
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), r = ms % 1000;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(r).padStart(3, "0")}`;
};
const clone = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ undo / redo
function pushUndo() {
  if (!S.scene) return;
  S.undo.push(clone(S.scene));
  if (S.undo.length > 40) S.undo.shift();
  S.redo = [];
  syncHistoryButtons();
}
function syncHistoryButtons() {
  if ($("undoBtn")) $("undoBtn").disabled = S.undo.length === 0;
  if ($("redoBtn")) $("redoBtn").disabled = S.redo.length === 0;
  if ($("delKeyBtn")) {
    const canDel = S.sel.cam != null && (S.scene?.camera.track.length || 0) > 1;
    $("delKeyBtn").disabled = !canDel;
  }
  updateKeyTelemetry();
}
function doUndo() {
  if (!S.undo.length) return;
  S.redo.push(clone(S.scene));
  S.scene = S.undo.pop();
  S.sel.cam = null;
  syncFovUI();
  syncHistoryButtons();
  msg("Undid last blocking edit.");
}
function doRedo() {
  if (!S.redo.length) return;
  S.undo.push(clone(S.scene));
  S.scene = S.redo.pop();
  S.sel.cam = null;
  syncFovUI();
  syncHistoryButtons();
  msg("Redid blocking edit.");
}
function syncFovUI() {
  if (!S.scene) return;
  $("fov").value = S.scene.camera.hfov_deg;
  $("fovVal").textContent = Math.round(S.scene.camera.hfov_deg);
}
function updateKeyTelemetry() {
  const el = $("keyTelemetry");
  if (!el) return;
  if (S.sel.cam == null || !S.scene) { el.textContent = ""; return; }
  const k = sortedKeys(S.scene.camera.track)[S.sel.cam];
  el.textContent = k ? `Key #${S.sel.cam + 1} · ${k.t.toFixed(2)}s · (${k.x.toFixed(1)}, ${k.y.toFixed(1)}) · ${Math.round(k.yaw_deg)}°` : "";
}

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
  S.t = 0; S.sel.cam = null; S.undo = []; S.redo = []; S.lastOrbitAnchor = null; S.orbitRadius = null;
  const dur = S.orig.clip.duration_s;
  $("time").max = dur; $("t1").value = Math.min(4, dur);
  syncFovUI();
  const entities = [...S.scene.actors, ...S.scene.objects];
  for (const selId of ["target", "aimTarget"]) {
    const el = $(selId);
    if (!el) continue;
    el.innerHTML = "";
    for (const e of entities) {
      const o = document.createElement("option");
      o.value = e.id;
      const shortDesc = e.visual_description.length > 28 ? e.visual_description.slice(0, 27) + "…" : e.visual_description;
      o.textContent = `${e.id} · ${shortDesc}`;
      el.appendChild(o);
    }
  }
  // Default orbit radius from initial camera-to-subject distance
  const tgt = entityPosAt(S.scene, lockedTargetId(), 0);
  const c0 = camAt(S.scene, 0);
  const initR = tgt ? Math.max(1.0, Math.min(8.0, Math.hypot(c0.x - tgt.x, c0.y - tgt.y))) : 3.0;
  if ($("orbitRadius")) {
    $("orbitRadius").value = initR.toFixed(1);
    $("orbitRadiusVal").textContent = initR.toFixed(1) + "u";
  }
  setVideo(S.run.reel_edit_url || S.run.reel_url || S.run.clip_url);
  const rounds = Object.keys(S.run.checks).map(Number).sort((a, b) => a - b);
  S.round = rounds.length ? rounds[rounds.length - 1] : null;
  renderInspector();
  badges();
  syncHistoryButtons();
  msg(S.run.scene_edit ? "Loaded your latest saved edit (dashed grey = original path)." : "");
}

function setVideo(url) {
  const v = $("video");
  if (url) { v.src = url; v.style.display = "block"; $("noVideo").style.display = "none"; }
  else { v.removeAttribute("src"); v.load(); v.style.display = "none"; $("noVideo").style.display = "flex"; }
}

// ------------------------------------------------------------------ inspector
function roundRate(entry, checks) {
  if (entry?.pass_rate != null) return entry.pass_rate;
  if (entry?.score_cand != null) return entry.score_cand;
  if (!checks?.length) return null;
  const usable = checks.filter((c) => c.result?.verdict === "pass" || c.result?.verdict === "fail");
  if (!usable.length) return null;
  return usable.filter((c) => c.result?.verdict === "pass").length / usable.length;
}

function renderInspector() {
  const log = S.run.loop_log || [];
  const tabs = $("rounds"); tabs.innerHTML = "";
  const rounds = Object.keys(S.run.checks).map(Number).sort((a, b) => a - b);
  for (const r of rounds) {
    const entry = log.find((e) => e.round === r) || {};
    const b = document.createElement("button");
    b.type = "button";
    b.role = "tab";
    b.setAttribute("aria-selected", String(r === S.round));
    b.className = "tab" + (r === S.round ? " active" : "");
    const rateVal = roundRate(entry, S.run.checks[r]);
    const rate = rateVal != null ? Math.round(rateVal * 100) + "%" : "";
    b.innerHTML = `Round ${r}${entry.accepted === false ? " ✗" : ""}<span class="rate">${rate}</span>`;
    b.onclick = () => { S.round = r; renderInspector(); };
    tabs.appendChild(b);
  }
  const checks = S.round != null ? S.run.checks[S.round] || [] : [];
  const activeEntry = log.find((e) => e.round === S.round) || {};
  const n = (v) => checks.filter((c) => (c.result?.verdict || "none") === v).length;
  const nPass = n("pass"), nFail = n("fail"), nUnc = n("uncertain") + n("none");
  const curRate = roundRate(activeEntry, checks);

  const pill = $("passPill");
  if (pill) {
    if (curRate != null) {
      const pct = Math.round(curRate * 100);
      pill.textContent = `${pct}% pass`;
      pill.className = "pass-pill" + (pct >= 85 ? "" : pct >= 60 ? " warn" : " bad");
    } else {
      pill.textContent = "";
      pill.className = "pass-pill";
    }
  }

  const bar = $("roundBar");
  if (bar) {
    if (checks.length) {
      bar.innerHTML =
        (nPass ? `<span class="pb-pass" style="flex:${nPass}"></span>` : "") +
        (nUnc ? `<span class="pb-unc" style="flex:${nUnc}"></span>` : "") +
        (nFail ? `<span class="pb-fail" style="flex:${nFail}"></span>` : "");
    } else bar.innerHTML = "";
  }

  $("roundStats").textContent = checks.length
    ? `${checks.length} checks · ✔ ${nPass} pass · ✘ ${nFail} fail · ? ${nUnc} uncertain` +
      (activeEntry.latency_s ? ` · ${Number(activeEntry.latency_s).toFixed(1)}s` : "")
    : "No checks yet for this run.";

  const changesEl = $("roundChanges");
  if (changesEl) {
    if (activeEntry.changes) {
      changesEl.textContent = activeEntry.changes;
      changesEl.hidden = false;
    } else {
      changesEl.textContent = "";
      changesEl.hidden = true;
    }
  }

  const ul = $("checks"); ul.innerHTML = "";
  const order = { fail: 0, uncertain: 1, none: 1, pass: 2 };
  for (const c of [...checks].sort((a, b) => order[a.result?.verdict || "none"] - order[b.result?.verdict || "none"])) {
    const v = c.result?.verdict || "none";
    const li = document.createElement("li");
    const p = c.result?.p_yes != null ? ` p=${c.result.p_yes.toFixed(2)}` : "";
    li.innerHTML = `<span class="v-${v}" aria-label="${v}">${{ pass: "✔", fail: "✘" }[v] || "?"}</span>` +
      `<span class="tpl">${c.template} ${c.mmss}</span><span>${escapeHtml(c.question.instructions)}` +
      `<span class="tpl"> · expected ${c.expected}${p}</span></span>`;
    li.onmouseenter = (ev) => showThumb(c.input_ref.paths[0], ev);
    li.onmousemove = (ev) => moveThumb(ev);
    li.onmouseleave = hideThumb;
    li.onclick = () => seek(c.t);
    ul.appendChild(li);
  }
  const lc = $("lowconf"); lc.innerHTML = "";
  const flagged = S.scene.low_confidence || [];
  if ($("lowconfCount")) $("lowconfCount").textContent = flagged.length ? `${flagged.length} flagged` : "All clear";
  for (const l of flagged) {
    const li = document.createElement("li");
    li.textContent = `⚠ ${l.entity_id} @ ${mmss(l.t0)}–${mmss(l.t1)} (${l.failed_checks.length} failed check${l.failed_checks.length === 1 ? "" : "s"})`;
    li.onclick = () => seek(l.t0);
    lc.appendChild(li);
  }
  if (!lc.children.length) lc.innerHTML = '<li class="ok-item">✔ All camera &amp; subject tracks verified</li>';
}
const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
function showThumb(path, ev) {
  const im = $("thumb"); im.src = `/frame?path=${encodeURIComponent(path)}`;
  im.onerror = () => (im.style.display = "none"); im.style.display = "block"; moveThumb(ev);
}
function moveThumb(ev) {
  const im = $("thumb"), w = 320, h = 190;
  const x = Math.max(12, Math.min(ev.clientX - w - 18, window.innerWidth - w - 12));
  const y = Math.max(12, Math.min(ev.clientY + 14, window.innerHeight - h - 12));
  im.style.left = x + "px"; im.style.top = y + "px";
}
function hideThumb() { $("thumb").style.display = "none"; }

// ------------------------------------------------------------------ time
function seek(t) {
  S.t = Math.max(0, Math.min(t, S.orig?.clip.duration_s || 0));
  const v = $("video");
  if (v.src && Math.abs(v.currentTime - S.t) > 0.05) v.currentTime = S.t;
}
function startMapPreview() {
  const v = $("video");
  if (v.src && !v.paused) v.pause();
  S.playing = true;
  S.lastTick = performance.now();
  $("play").textContent = "❚❚";
}
$("time").oninput = (e) => seek(parseFloat(e.target.value));
$("play").onclick = () => {
  const v = $("video");
  // If scene has unsaved interactive edits, play the live map preview independently of the old video!
  const edited = S.orig && S.scene && JSON.stringify(S.orig.camera.track) !== JSON.stringify(S.scene.camera.track);
  if (v.src && !edited) { v.paused ? v.play() : v.pause(); return; }
  S.playing = !S.playing; S.lastTick = performance.now();
  $("play").textContent = S.playing ? "❚❚" : "▶";
};
$("video").onplay = () => { S.playing = false; $("play").textContent = "❚❚"; };
$("video").onpause = () => { if (!S.playing) $("play").textContent = "▶"; };

if ($("loopToggle")) {
  $("loopToggle").onclick = () => {
    S.loop = !S.loop;
    $("loopToggle").setAttribute("aria-pressed", String(S.loop));
    $("loopToggle").classList.toggle("active-pill", S.loop);
  };
}

function tick(now) {
  const v = $("video");
  if (S.orig) {
    const dur = S.orig.clip.duration_s;
    if (v.src && !v.paused && !isNaN(v.duration)) {
      S.t = Math.min(v.currentTime, dur);
    } else if (S.playing) {
      S.t += (now - S.lastTick) / 1000;
      if (S.t > dur) {
        if (S.loop) S.t = 0;
        else { S.t = dur; S.playing = false; $("play").textContent = "▶"; }
      }
      if (v.src && Math.abs(v.currentTime - S.t) > 0.25) v.currentTime = S.t;
    }
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

// ------------------------------------------------------------------ interactive playground choreography
function setTool(tool) {
  S.tool = tool;
  S.drawStroke = null;
  S.orbitDraft = null;
  document.querySelectorAll(".tool-btn").forEach((b) => {
    const on = b.dataset.tool === tool;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  });
  const banner = $("modeBanner");
  if (banner) {
    if (tool === "draw") banner.textContent = "Draw Mode: click & drag anywhere on the floor plan to sketch a custom camera path";
    else if (tool === "orbit") banner.textContent = "Orbit Mode: click on a character (or anywhere) and drag outward to sculpt an orbit ring";
    else banner.textContent = "";
  }
  canvas.style.cursor = tool === "select" ? "default" : "crosshair";
}

document.querySelectorAll(".tool-btn").forEach((b) => {
  b.onclick = () => setTool(b.dataset.tool);
});

function buildOrbitTrack(anchor, sweepDeg, dir, radiusOverride) {
  if (!S.scene) return;
  const dur = S.orig.clip.duration_s;
  const c0 = camAt(S.scene, 0);
  const center0 = anchor.entityId ? entityPosAt(S.scene, anchor.entityId, 0) : { x: anchor.x, y: anchor.y };
  if (!center0) return;

  const rx0 = c0.x - center0.x, ry0 = c0.y - center0.y;
  const baseDist = Math.hypot(rx0, ry0);
  const r = radiusOverride != null ? radiusOverride : (baseDist > 0.4 ? baseDist : 3.0);
  const startAngle = baseDist > 0.2 ? Math.atan2(ry0, rx0) : -Math.PI / 2;

  // At least 1 key every 15° (so lerpAngle never wraps the wrong way on 360° or 720°!)
  const steps = Math.max(12, Math.min(60, Math.ceil(Math.abs(sweepDeg) / 15)));
  const autoAim = $("autoAim") ? $("autoAim").checked : true;
  const keys = [];
  for (let i = 0; i <= steps; i++) {
    const f = i / steps;
    const t = roundN(dur * f, 3);
    const ctr = anchor.entityId ? (entityPosAt(S.scene, anchor.entityId, t) || center0) : center0;
    const phi = startAngle + rad(sweepDeg * f * dir);
    const x = ctr.x + r * Math.cos(phi);
    const y = ctr.y + r * Math.sin(phi);
    const yaw = autoAim ? yawTowards(x, y, ctr.x, ctr.y) : wrap180(c0.yaw + sweepDeg * f * dir);
    keys.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }
  S.scene.camera.track = keys;
  S.scene.camera.motion_segments = [
    { t0: 0, t1: roundN(dur, 3), translation: dir > 0 ? "arc_right" : "arc_left", rotation: "none" },
  ];
  S.sel.cam = null;
  S.lastOrbitAnchor = anchor;
  S.orbitRadius = r;
  if ($("orbitRadius")) {
    $("orbitRadius").value = Math.max(0.8, Math.min(8, r)).toFixed(1);
    $("orbitRadiusVal").textContent = r.toFixed(1) + "u";
  }
  syncHistoryButtons();
  markDirty(`${sweepDeg}° ${dir > 0 ? "CCW" : "CW"} orbit around ${anchor.entityId || "point"} (R=${r.toFixed(1)}u). Hit Re-render or Re-shoot!`);
  startMapPreview();
}

function buildPushInTrack() {
  if (!S.scene) return;
  const dur = S.orig.clip.duration_s;
  const tid = lockedTargetId();
  const c0 = camAt(S.scene, 0);
  const tgt0 = entityPosAt(S.scene, tid, 0);
  const f0 = fwdVec(c0.yaw);
  const tx = tgt0 ? tgt0.x : c0.x + f0[0] * 4;
  const ty = tgt0 ? tgt0.y : c0.y + f0[1] * 4;
  const dx = tx - c0.x, dy = ty - c0.y, dist = Math.hypot(dx, dy);
  const moveDist = Math.max(0.8, dist * 0.52);
  const ux = dist > 0.05 ? dx / dist : f0[0], uy = dist > 0.05 ? dy / dist : f0[1];
  pushUndo();
  const steps = Math.max(6, Math.ceil(dur / 0.5));
  const keys = [];
  for (let i = 0; i <= steps; i++) {
    const f = i / steps;
    const ease = f * f * (3 - 2 * f); // smoothstep cinema ramp
    const t = roundN(dur * f, 3);
    const x = c0.x + ux * moveDist * ease;
    const y = c0.y + uy * moveDist * ease;
    const tgt = entityPosAt(S.scene, tid, t) || { x: tx, y: ty };
    const yaw = ($("autoAim")?.checked ?? true) ? yawTowards(x, y, tgt.x, tgt.y) : c0.yaw;
    keys.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }
  S.scene.camera.track = keys;
  S.scene.camera.motion_segments = [{ t0: 0, t1: roundN(dur, 3), translation: "dolly_in", rotation: "none" }];
  S.lastOrbitAnchor = null;
  S.sel.cam = null;
  syncHistoryButtons();
  markDirty(`Applied smooth push-in toward ${tid || "frame center"}.`);
  startMapPreview();
}

function commitDrawnStroke(pts) {
  if (!S.scene || pts.length < 2) return;
  // Compute cumulative arc length
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  }
  const totalLen = cum[cum.length - 1];
  if (totalLen < 0.25) return;

  const dur = S.orig.clip.duration_s;
  const nKeys = Math.max(8, Math.min(28, Math.round(Math.max(dur / 0.4, totalLen * 1.6))));
  const sampled = [];
  for (let i = 0; i <= nKeys; i++) {
    const targetD = (totalLen * i) / nKeys;
    let j = 1;
    while (j < cum.length - 1 && cum[j] < targetD) j++;
    const segLen = cum[j] - cum[j - 1];
    const frac = segLen > 1e-6 ? (targetD - cum[j - 1]) / segLen : 0;
    sampled.push([lerp(pts[j - 1][0], pts[j][0], frac), lerp(pts[j - 1][1], pts[j][1], frac)]);
  }
  // Light 2-pass smoothing on interior points so hand-drawn curves feel like dolly tracks
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 1; i < sampled.length - 1; i++) {
      sampled[i] = [
        0.25 * sampled[i - 1][0] + 0.5 * sampled[i][0] + 0.25 * sampled[i + 1][0],
        0.25 * sampled[i - 1][1] + 0.5 * sampled[i][1] + 0.25 * sampled[i + 1][1],
      ];
    }
  }

  pushUndo();
  const autoAim = $("autoAim") ? $("autoAim").checked : true;
  const tid = lockedTargetId();
  const keys = [];
  for (let i = 0; i < sampled.length; i++) {
    const f = i / (sampled.length - 1);
    const t = roundN(dur * f, 3);
    const [x, y] = sampled[i];
    let yaw;
    const tgt = autoAim ? entityPosAt(S.scene, tid, t) : null;
    if (tgt) {
      yaw = yawTowards(x, y, tgt.x, tgt.y);
    } else {
      const next = sampled[Math.min(i + 1, sampled.length - 1)];
      const prev = sampled[Math.max(i - 1, 0)];
      yaw = yawTowards(prev[0], prev[1], next[0], next[1]);
    }
    keys.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }

  // Classify dominant motion for segment badge (net turn around target → arc, else dolly/truck)
  let segMove = "dolly_in";
  if (autoAim && tid) {
    const c = entityPosAt(S.scene, tid, dur / 2);
    if (c) {
      let sweep = 0;
      for (let i = 1; i < sampled.length; i++) {
        const a0 = Math.atan2(sampled[i - 1][1] - c.y, sampled[i - 1][0] - c.x);
        const a1 = Math.atan2(sampled[i][1] - c.y, sampled[i][0] - c.x);
        sweep += wrap180(deg(a1 - a0));
      }
      if (Math.abs(sweep) > 35) segMove = sweep > 0 ? "arc_right" : "arc_left";
    }
  }
  S.scene.camera.track = keys;
  S.scene.camera.motion_segments = [{ t0: 0, t1: roundN(dur, 3), translation: segMove, rotation: "none" }];
  S.lastOrbitAnchor = null;
  S.sel.cam = null;
  syncHistoryButtons();
  markDirty(`Sketched ${keys.length}-keyframe custom path (${totalLen.toFixed(1)}u)${autoAim && tid ? ` locked onto ${tid}` : ""}.`);
  startMapPreview();
}

function addKeyframeAt(wx, wy, t) {
  if (!S.scene) return;
  pushUndo();
  const track = sortedKeys(S.scene.camera.track);
  const tid = lockedTargetId();
  const tgt = ($("autoAim")?.checked ?? true) ? entityPosAt(S.scene, tid, t) : null;
  const yaw = tgt ? yawTowards(wx, wy, tgt.x, tgt.y) : camAt(S.scene, t).yaw;
  // Replace if within 0.08s of existing key, otherwise insert
  const nearIdx = track.findIndex((k) => Math.abs(k.t - t) < 0.08);
  const nk = { t: roundN(t, 3), mmss: mmss(t), x: roundN(wx, 4), y: roundN(wy, 4), yaw_deg: roundN(yaw, 3) };
  if (nearIdx >= 0) track[nearIdx] = nk;
  else track.push(nk);
  S.scene.camera.track = sortedKeys(track);
  S.sel.cam = S.scene.camera.track.findIndex((k) => k.t === nk.t);
  S.lastOrbitAnchor = null;
  syncHistoryButtons();
  markDirty(`Added camera keyframe at ${nk.t.toFixed(2)}s.`);
}

function deleteSelectedKey() {
  if (!S.scene || S.sel.cam == null) return;
  const track = sortedKeys(S.scene.camera.track);
  if (track.length <= 1) return;
  pushUndo();
  track.splice(S.sel.cam, 1);
  S.scene.camera.track = track;
  S.sel.cam = null;
  syncHistoryButtons();
  markDirty("Deleted camera keyframe.");
}

// Playground toolbar bindings
if ($("quickOrbit360")) $("quickOrbit360").onclick = () => {
  if (!S.scene) return;
  S.orbitSweep = 360;
  if ($("orbitSweep")) { $("orbitSweep").value = 360; $("orbitSweepVal").textContent = "360°"; }
  pushUndo();
  buildOrbitTrack({ entityId: lockedTargetId() }, 360, S.orbitDir, S.orbitRadius);
};
if ($("quickOrbit180")) $("quickOrbit180").onclick = () => {
  if (!S.scene) return;
  S.orbitSweep = 180;
  if ($("orbitSweep")) { $("orbitSweep").value = 180; $("orbitSweepVal").textContent = "180°"; }
  pushUndo();
  buildOrbitTrack({ entityId: lockedTargetId() }, 180, S.orbitDir, S.orbitRadius);
};
if ($("quickDollyIn")) $("quickDollyIn").onclick = () => buildPushInTrack();
if ($("quickFlipDir")) $("quickFlipDir").onclick = () => {
  S.orbitDir *= -1;
  $("quickFlipDir").textContent = S.orbitDir > 0 ? "CCW ↺" : "CW ↻";
  if (S.lastOrbitAnchor && S.scene) {
    pushUndo();
    buildOrbitTrack(S.lastOrbitAnchor, S.orbitSweep, S.orbitDir, S.orbitRadius);
  }
};
if ($("orbitSweep")) $("orbitSweep").oninput = (e) => {
  S.orbitSweep = parseInt(e.target.value, 10);
  $("orbitSweepVal").textContent = `${S.orbitSweep}°`;
  if (S.lastOrbitAnchor && S.scene) {
    buildOrbitTrack(S.lastOrbitAnchor, S.orbitSweep, S.orbitDir, S.orbitRadius);
  }
};
if ($("orbitRadius")) $("orbitRadius").oninput = (e) => {
  S.orbitRadius = parseFloat(e.target.value);
  $("orbitRadiusVal").textContent = `${S.orbitRadius.toFixed(1)}u`;
  if (S.lastOrbitAnchor && S.scene) {
    buildOrbitTrack(S.lastOrbitAnchor, S.orbitSweep, S.orbitDir, S.orbitRadius);
  }
};
if ($("aimTarget")) $("aimTarget").onchange = () => {
  if (S.lastOrbitAnchor && S.scene) {
    pushUndo();
    buildOrbitTrack({ entityId: lockedTargetId() }, S.orbitSweep, S.orbitDir, S.orbitRadius);
  }
};
if ($("addKeyBtn")) $("addKeyBtn").onclick = () => {
  if (!S.scene) return;
  const c = camAt(S.scene, S.t);
  addKeyframeAt(c.x, c.y, S.t);
};
if ($("delKeyBtn")) $("delKeyBtn").onclick = deleteSelectedKey;
if ($("undoBtn")) $("undoBtn").onclick = doUndo;
if ($("redoBtn")) $("redoBtn").onclick = doRedo;

window.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    e.shiftKey ? doRedo() : doUndo();
  } else if ((e.key === "Delete" || e.key === "Backspace") && S.sel.cam != null) {
    e.preventDefault();
    deleteSelectedKey();
  }
});

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
  const mx = Math.max(1.4, (x1 - x0) * 0.16), my = Math.max(1.4, (y1 - y0) * 0.16);
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
  if (!S.drag && !S.drawStroke && !S.orbitDraft) S.view = fitView(sc);
  const V = S.view, P = V.toPx, dpr = window.devicePixelRatio || 1;
  ctx.fillStyle = COLORS.bg; ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Blueprint dual-density grid (0.5u minor, 1.0u major)
  ctx.lineWidth = 1;
  for (let x = Math.ceil(V.x0 * 2) / 2; x <= V.x1; x += 0.5) {
    const major = Math.abs(x - Math.round(x)) < 1e-4;
    ctx.strokeStyle = major ? COLORS.gridMajor : COLORS.gridMinor;
    line(P(x, V.y0), P(x, V.y1));
  }
  for (let y = Math.ceil(V.y0 * 2) / 2; y <= V.y1; y += 0.5) {
    const major = Math.abs(y - Math.round(y)) < 1e-4;
    ctx.strokeStyle = major ? COLORS.gridMajor : COLORS.gridMinor;
    line(P(V.x0, y), P(V.x1, y));
  }

  // Room outline with translucent floor
  const room = sc.room.outline || [];
  if (room.length > 2) {
    const rPts = room.map((p) => P(...p));
    ctx.fillStyle = COLORS.roomFill;
    ctx.beginPath(); ctx.moveTo(...rPts[0]);
    for (const p of rPts.slice(1)) ctx.lineTo(...p);
    ctx.closePath(); ctx.fill();
    ctx.strokeStyle = COLORS.room; ctx.lineWidth = 1.75 * dpr;
    ctx.stroke();
  }

  // Objects
  for (const o of sc.objects) drawObject(o, P, V.s, dpr);

  // Ghost (original) camera path
  if (S.orig && JSON.stringify(S.orig.camera.track) !== JSON.stringify(sc.camera.track)) {
    ctx.save();
    ctx.setLineDash([6 * dpr, 6 * dpr]); ctx.strokeStyle = COLORS.ghost; ctx.lineWidth = 1.8 * dpr;
    poly(sortedKeys(S.orig.camera.track).map((k) => P(k.x, k.y)));
    ctx.restore();
  }

  // Active camera path + directional chevrons
  const camKeys = sortedKeys(sc.camera.track);
  const camPx = camKeys.map((k) => P(k.x, k.y));
  ctx.strokeStyle = COLORS.camDim; ctx.lineWidth = 2.4 * dpr;
  poly(camPx);
  drawPathArrows(camPx, dpr);

  // Look-At target reticle & sightline
  const autoAimOn = $("autoAim")?.checked;
  const aimPos = autoAimOn ? entityPosAt(sc, lockedTargetId(), S.t) : null;
  const cam = camAt(sc, S.t);
  const [cx, cy] = P(cam.x, cam.y);
  if (aimPos) {
    const [ax, ay] = P(aimPos.x, aimPos.y);
    ctx.save();
    ctx.setLineDash([4 * dpr, 5 * dpr]);
    ctx.strokeStyle = "rgba(79,158,255,0.45)";
    ctx.lineWidth = 1.3 * dpr;
    line([cx, cy], [ax, ay]);
    ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(79,158,255,0.85)";
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath(); ctx.arc(ax, ay, Math.max(15 * dpr, 0.45 * V.s), 0, 2 * Math.PI); ctx.stroke();
    ctx.restore();
  }

  // Actors
  for (const a of sc.actors) drawActor(a, P, V.s, dpr);

  // Camera keyframes
  camKeys.forEach((k, i) => {
    const [px, py] = P(k.x, k.y);
    const isSel = i === S.sel.cam;
    const isHov = S.hover?.kind === "cam" && S.hover.index === i;
    if (isSel || isHov) {
      ctx.fillStyle = "rgba(62,232,139,0.22)";
      dot(px, py, 10 * dpr);
    }
    ctx.fillStyle = isSel ? "#ffffff" : COLORS.cam;
    dot(px, py, (isSel ? 5.5 : (i === 0 || i === camKeys.length - 1 ? 4.8 : 3.5)) * dpr);
  });

  // Start badge on first keyframe if path moves
  if (camPx.length > 1) {
    pillLabel("START", camPx[0][0], camPx[0][1] + 15 * dpr, dpr, "#3ee88b");
  }

  // Camera now + gradient FOV cone + cinema camera icon
  const hf = sc.camera.hfov_deg, len = 4.2;
  const low = lowConfAt("camera", S.t);
  const l = fwdVec(cam.yaw + hf / 2), r = fwdVec(cam.yaw - hf / 2), f = fwdVec(cam.yaw);
  const pL = P(cam.x + l[0] * len, cam.y + l[1] * len);
  const pR = P(cam.x + r[0] * len, cam.y + r[1] * len);
  const pMid = P(cam.x + f[0] * len, cam.y + f[1] * len);
  const grad = ctx.createLinearGradient(cx, cy, pMid[0], pMid[1]);
  grad.addColorStop(0, `rgba(62,232,139,${low ? 0.16 : 0.34})`);
  grad.addColorStop(1, "rgba(62,232,139,0.02)");
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(...pL); ctx.lineTo(...pR); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = `rgba(62,232,139,${low ? 0.25 : 0.45})`; ctx.lineWidth = 1.1 * dpr;
  line([cx, cy], pL); line([cx, cy], pR);

  // Rotated camera body + lens barrel
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-rad(cam.yaw));
  ctx.fillStyle = COLORS.cam;
  roundRect(-8 * dpr, -6 * dpr, 16 * dpr, 13 * dpr, 3.5 * dpr);
  ctx.fillStyle = "#b8fbd4";
  ctx.beginPath();
  ctx.moveTo(-4 * dpr, -6 * dpr); ctx.lineTo(4 * dpr, -6 * dpr);
  ctx.lineTo(6.5 * dpr, -12 * dpr); ctx.lineTo(-6.5 * dpr, -12 * dpr);
  ctx.closePath(); ctx.fill();
  ctx.restore();

  // Yaw handle for the selected camera key
  if (S.sel.cam != null) {
    const k = camKeys[S.sel.cam];
    if (k) {
      const h = yawHandle(k);
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.6 * dpr;
      line(P(k.x, k.y), h);
      ctx.fillStyle = "rgba(255,255,255,0.18)";
      ctx.beginPath(); ctx.arc(h[0], h[1], 7.5 * dpr, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    }
  }

  // Live Freehand Draw preview ribbon
  if (S.drawStroke && S.drawStroke.length > 1) {
    const dPts = S.drawStroke.map((pt) => P(pt[0], pt[1]));
    ctx.save();
    ctx.strokeStyle = "#4f9eff"; ctx.lineWidth = 3.2 * dpr; ctx.lineCap = "round"; ctx.lineJoin = "round";
    poly(dPts);
    drawPathArrows(dPts, dpr, "#93c5fd");
    ctx.restore();
  }

  // Live Orbit Ring sculpting preview
  if (S.orbitDraft) {
    const { cx: ox, cy: oy, r } = S.orbitDraft;
    const [opx, opy] = P(ox, oy);
    const rPx = Math.max(12 * dpr, r * V.s);
    ctx.save();
    ctx.strokeStyle = "#3ee88b"; ctx.lineWidth = 2.2 * dpr;
    ctx.setLineDash([7 * dpr, 5 * dpr]);
    ctx.beginPath(); ctx.arc(opx, opy, rPx, 0, 2 * Math.PI); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(62,232,139,0.07)";
    ctx.beginPath(); ctx.arc(opx, opy, rPx, 0, 2 * Math.PI); ctx.fill();
    pillLabel(`${S.orbitSweep}° ${S.orbitDir > 0 ? "CCW" : "CW"} · R=${r.toFixed(1)}u`, opx, opy - rPx - 12 * dpr, dpr, "#3ee88b");
    ctx.restore();
  }
}

function drawPathArrows(pts, dpr, color = "rgba(169,247,203,0.8)") {
  if (pts.length < 2) return;
  let accum = 0;
  const step = 62 * dpr;
  ctx.fillStyle = color;
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
    const seg = Math.hypot(dx, dy);
    if (seg < 2) continue;
    accum += seg;
    if (accum >= step) {
      accum = 0;
      const mx = (pts[i][0] + pts[i - 1][0]) / 2, my = (pts[i][1] + pts[i - 1][1]) / 2;
      const ang = Math.atan2(dy, dx);
      ctx.save(); ctx.translate(mx, my); ctx.rotate(ang);
      ctx.beginPath();
      ctx.moveTo(5 * dpr, 0); ctx.lineTo(-4 * dpr, -3.5 * dpr); ctx.lineTo(-4 * dpr, 3.5 * dpr);
      ctx.closePath(); ctx.fill();
      ctx.restore();
    }
  }
}

function drawObject(o, P, s, dpr) {
  const [px, py] = P(...o.center), w = o.size[0] * s, h = o.size[1] * s;
  const low = lowConfAt(o.id, S.t);
  ctx.save(); ctx.translate(px, py); ctx.rotate(-rad(o.rotation_deg || 0));
  ctx.globalAlpha = low ? 0.45 : 0.85; ctx.fillStyle = o.color || "#64748b";
  if (o.shape === "circle") { ctx.beginPath(); ctx.arc(0, 0, w / 2, 0, 2 * Math.PI); ctx.fill(); }
  else {
    const rw = Math.max(w, 4), rh = Math.max(h, 4);
    ctx.fillRect(-rw / 2, -rh / 2, rw, rh);
    ctx.strokeStyle = "rgba(255,255,255,0.2)"; ctx.lineWidth = 1 * dpr;
    ctx.strokeRect(-rw / 2, -rh / 2, rw, rh);
  }
  ctx.restore(); ctx.globalAlpha = 1;
  pillLabel(`${o.type}${low ? " ?" : ""}`, px, py - Math.max(h, w) / 2 - 10 * dpr, dpr, low ? "#f5bf4f" : "#cbd5e1");
}

function drawActor(a, P, s, dpr) {
  const keys = sortedKeys(a.track);
  ctx.strokeStyle = (a.color || "#f87171") + "55"; ctx.lineWidth = 2 * dpr;
  poly(keys.map((k) => P(k.x, k.y)));
  ctx.fillStyle = (a.color || "#f87171") + "aa";
  for (const k of keys) { const [x, y] = P(k.x, k.y); dot(x, y, 2.8 * dpr); }
  const now = actorAt(a, S.t); if (!now.visible) return;
  const [px, py] = P(now.x, now.y), low = lowConfAt(a.id, S.t), f = fwdVec(now.facing);
  ctx.save(); ctx.translate(px, py); ctx.rotate(-rad(now.facing));
  ctx.globalAlpha = low ? 0.5 : 1; ctx.fillStyle = a.color || "#f87171";
  ctx.beginPath(); ctx.ellipse(0, 0, 0.34 * s, 0.17 * s, 0, 0, 2 * Math.PI); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.45)"; ctx.lineWidth = 1.2 * dpr; ctx.stroke();
  ctx.fillStyle = "#f5d0b0"; ctx.beginPath(); ctx.arc(0, 0, 0.12 * s, 0, 2 * Math.PI); ctx.fill();
  ctx.restore(); ctx.globalAlpha = 1;
  ctx.strokeStyle = a.color || "#f87171"; ctx.lineWidth = 1.8 * dpr;
  line([px, py], P(now.x + f[0] * 0.55, now.y + f[1] * 0.55));
  pillLabel(`${a.id}${low ? " ?" : ""}`, px, py - 0.42 * s - 10 * dpr, dpr, low ? "#f5bf4f" : "#f8fafc");
}

function pillLabel(text, x, y, dpr, color) {
  ctx.save();
  ctx.font = `600 ${10.5 * dpr}px -apple-system, system-ui, sans-serif`;
  const w = ctx.measureText(text).width + 10 * dpr, h = 16 * dpr;
  ctx.fillStyle = "rgba(9,11,16,0.78)";
  roundRect(x - w / 2, y - h / 2, w, h, 5 * dpr);
  ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, x, y + 0.5 * dpr);
  ctx.restore();
}

const yawHandle = (k) => { const f = fwdVec(k.yaw_deg); return S.view.toPx(k.x + f[0] * 1.2, k.y + f[1] * 1.2); };
function line(a, b) { ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); }
function poly(pts, close = false) { if (pts.length < 2) return; ctx.beginPath(); ctx.moveTo(...pts[0]);
  for (const p of pts.slice(1)) ctx.lineTo(...p); if (close) ctx.closePath(); ctx.stroke(); }
function dot(x, y, r) { ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); }
function roundRect(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); ctx.fill(); }

// ------------------------------------------------------------------ map interaction (select / draw / orbit)
function hit(px, py) {
  if (!S.view || !S.scene) return null;
  const dpr = window.devicePixelRatio || 1, R = 11 * dpr, P = S.view.toPx, sc = S.scene;
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
function nearestEntityAtWorld(wx, wy) {
  let best = null, bestD = 1.2;
  for (const a of S.scene.actors) {
    const p = actorAt(a, S.t), d = Math.hypot(wx - p.x, wy - p.y);
    if (d < bestD) { bestD = d; best = { entityId: a.id, cx: p.x, cy: p.y }; }
  }
  for (const o of S.scene.objects) {
    const d = Math.hypot(wx - o.center[0], wy - o.center[1]);
    if (d < bestD) { bestD = d; best = { entityId: o.id, cx: o.center[0], cy: o.center[1] }; }
  }
  return best;
}
function evPx(ev) { const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  return [(ev.clientX - r.left) * dpr, (ev.clientY - r.top) * dpr]; }

canvas.addEventListener("pointerdown", (ev) => {
  if (!S.scene || !S.view) return;
  const px = evPx(ev), [wx, wy] = S.view.toWorld(...px);

  if (S.tool === "draw") {
    S.drawStroke = [[wx, wy]];
    canvas.setPointerCapture(ev.pointerId);
    return;
  }
  if (S.tool === "orbit") {
    const snapped = nearestEntityAtWorld(wx, wy);
    S.orbitDraft = snapped
      ? { entityId: snapped.entityId, cx: snapped.cx, cy: snapped.cy, r: 1.5 }
      : { entityId: null, cx: wx, cy: wy, r: 1.5 };
    canvas.setPointerCapture(ev.pointerId);
    return;
  }

  const h = hit(...px);
  if (h?.kind === "cam") { S.sel.cam = h.index; seek(sortedKeys(S.scene.camera.track)[h.index].t); }
  else if (!h) S.sel.cam = null;
  if (h) {
    pushUndo();
    S.drag = h;
    S.lastOrbitAnchor = null;
    canvas.setPointerCapture(ev.pointerId);
    canvas.style.cursor = "grabbing";
  }
  syncHistoryButtons();
});

canvas.addEventListener("pointermove", (ev) => {
  if (!S.scene || !S.view) return;
  const px = evPx(ev), [wx, wy] = S.view.toWorld(...px);

  if (S.drawStroke) {
    const last = S.drawStroke[S.drawStroke.length - 1];
    if (Math.hypot(wx - last[0], wy - last[1]) > 0.08) S.drawStroke.push([wx, wy]);
    return;
  }
  if (S.orbitDraft) {
    S.orbitDraft.r = Math.max(0.8, Math.min(10, Math.hypot(wx - S.orbitDraft.cx, wy - S.orbitDraft.cy)));
    return;
  }
  if (!S.drag) {
    if (S.tool === "select") {
      S.hover = hit(...px);
      canvas.style.cursor = S.hover ? "grab" : "default";
    }
    return;
  }
  const d = S.drag, sc = S.scene, r = (v) => Math.round(v * 100) / 100;
  if (d.kind === "cam") {
    sc.camera.track = sortedKeys(sc.camera.track);
    const k = sc.camera.track[d.index];
    k.x = r(wx); k.y = r(wy);
    if ($("autoAim")?.checked) {
      const tgt = entityPosAt(sc, lockedTargetId(), k.t);
      if (tgt) k.yaw_deg = r(yawTowards(k.x, k.y, tgt.x, tgt.y));
    }
  } else if (d.kind === "yaw") {
    sc.camera.track = sortedKeys(sc.camera.track);
    const k = sc.camera.track[d.index];
    k.yaw_deg = r(yawTowards(k.x, k.y, wx, wy));
  } else if (d.kind === "actor") {
    const a = sc.actors.find((x) => x.id === d.id);
    a.track = sortedKeys(a.track);
    Object.assign(a.track[d.index], { x: r(wx), y: r(wy) });
  } else if (d.kind === "obj") {
    sc.objects.find((o) => o.id === d.id).center = [r(wx), r(wy)];
  }
  updateKeyTelemetry();
  markDirty();
});

canvas.addEventListener("pointerup", () => {
  if (S.drawStroke) {
    const stroke = S.drawStroke;
    S.drawStroke = null;
    commitDrawnStroke(stroke);
    return;
  }
  if (S.orbitDraft) {
    const draft = S.orbitDraft;
    S.orbitDraft = null;
    pushUndo();
    if (draft.entityId && $("aimTarget")) $("aimTarget").value = draft.entityId;
    buildOrbitTrack(
      draft.entityId ? { entityId: draft.entityId } : { x: draft.cx, y: draft.cy },
      S.orbitSweep, S.orbitDir, draft.r,
    );
    return;
  }
  S.drag = null;
  if (S.tool === "select") canvas.style.cursor = S.hover ? "grab" : "default";
});

canvas.addEventListener("dblclick", (ev) => {
  if (!S.scene || !S.view || S.tool !== "select") return;
  const [wx, wy] = S.view.toWorld(...evPx(ev));
  addKeyframeAt(wx, wy, S.t);
});

function markDirty(note) {
  msg(note || "Edited on playground (not saved). Press Re-render Map & Reel or Re-shoot (Omni).");
}

$("fov").oninput = (e) => {
  if (!S.scene) return;
  S.scene.camera.hfov_deg = parseFloat(e.target.value);
  $("fovVal").textContent = e.target.value; markDirty();
};

// ------------------------------------------------------------------ edit actions
$("applyPreset").onclick = async () => {
  const edit = { op: "preset", move: $("preset").value, t0: parseFloat($("t0").value), t1: parseFloat($("t1").value),
                 amount: $("amount").value === "" ? null : parseFloat($("amount").value),
                 target: $("preset").value.startsWith("arc") ? $("target").value || null : null };
  try {
    pushUndo();
    S.scene = (await post(`/api/run/${S.runId}/apply`, { scene: S.scene, edit })).scene; S.sel.cam = null;
    S.lastOrbitAnchor = null; syncHistoryButtons(); markDirty(); seek(edit.t0); startMapPreview();
  } catch (e) { S.undo.pop(); syncHistoryButtons(); msg(e.message, true); }
};

async function saveAndRender(promise, label) {
  msg(`${label}…`);
  const t0 = performance.now();
  try {
    const res = await promise;
    S.scene = res.scene;
    S.playing = false;
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    if (res.reel_url) { setVideo(res.reel_url); msg(`Saved ${res.saved}; new reel in ${secs}s.`); }
    else if (res.map_url) { setVideo(res.map_url); msg(`Saved ${res.saved}; new map video in ${secs}s (no clip to stack).`); }
    else msg(`Saved ${res.saved} in ${secs}s. ${res.note || ""}`.trim());
  } catch (e) { msg(e.message, true); }
}
$("rerender").onclick = () => saveAndRender(post(`/api/run/${S.runId}/edit`, { scene: S.scene }), "Saving + rendering");
$("textEdit").onclick = () => {
  const instruction = $("instruction").value.trim(); if (!instruction) return;
  pushUndo();
  saveAndRender(post(`/api/run/${S.runId}/text-edit`, { instruction, scene: S.scene }), "Asking Hydrogen");
};
$("reset").onclick = () => {
  pushUndo();
  S.scene = clone(S.orig); S.sel.cam = null; S.lastOrbitAnchor = null; S.playing = false;
  syncFovUI(); setVideo(S.run.reel_url || S.run.clip_url); syncHistoryButtons();
  msg("Back to the verified scene (edits on disk are kept).");
};
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
