// S09 (Siva) + Video-Editor Motion Stage: run picker, verify-loop inspector, spline motion editor & keyframe tracks.
// Vanilla JS, no build step.
// Conventions (contracts.md): x → right, y → forward; yaw 0 = +y, positive = camera turned to ITS LEFT.
"use strict";

const $ = (id) => document.getElementById(id);
const S = {
  runId: null, run: null, orig: null, scene: null, waypoints: [],
  t: 0, playing: false, lastTick: 0, round: null,
  sel: { cam: null, actor: null }, drag: null, hover: null, view: null, offline: false, jobTimer: null,
  tool: "select", loop: true, curveMode: true, actorDragMode: "all", sceneLocked: false,
  orbitDir: 1, orbitSweep: 360, orbitRadius: null, lastOrbitAnchor: null,
  drawStroke: null, orbitDraft: null, undo: [], redo: [],
  kfDrag: null,
};
const COLORS = {
  bg: "#0b0e14", gridMinor: "#141925", gridMajor: "#1d2435", roomFill: "rgba(148,163,184,0.05)",
  room: "#94a3b8", cam: "#3ee88b", camDim: "rgba(62,232,139,0.7)", ghost: "#7c879e", aim: "#4f9eff",
};
const normColor = (c, fb) => (!c ? fb : (/^[0-9a-f]{3,8}$/i.test(c) ? `#${c}` : c));

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

// ------------------------------------------------------------------ Centripetal Catmull-Rom Spline Baker
function catmullRom2D(p0, p1, p2, p3, u) {
  // Centripetal parameterization (alpha = 0.5) avoids cusps and overshoot loops
  const dist = (a, b) => Math.max(1e-4, Math.hypot(b[0] - a[0], b[1] - a[1]) ** 0.5);
  const t0 = 0, t1 = t0 + dist(p0, p1), t2 = t1 + dist(p1, p2), t3 = t2 + dist(p2, p3);
  const t = lerp(t1, t2, u);
  const interp = (a, b, ta, tb) => {
    const f = (t - ta) / (tb - ta);
    return [lerp(a[0], b[0], f), lerp(a[1], b[1], f)];
  };
  const A1 = interp(p0, p1, t0, t1), A2 = interp(p1, p2, t1, t2), A3 = interp(p2, p3, t2, t3);
  const B1 = interp(A1, A2, t0, t2), B2 = interp(A2, A3, t1, t3);
  return interp(B1, B2, t1, t2);
}

function extractWaypointsFromTrack(track, maxCtrl = 9, markAuto = false) {
  const ks = sortedKeys(track);
  if (!ks.length) return [];
  // Collapse redundant stationary keys so tripod shots don't stack 10 hidden pins on top of (0,0)
  const pruned = [{ ...clone(ks[0]), auto: false }];
  for (let i = 1; i < ks.length; i++) {
    const k = ks[i], prev = pruned[pruned.length - 1];
    const moved = Math.hypot(k.x - prev.x, k.y - prev.y) > 0.12;
    const turned = Math.abs(wrap180(k.yaw_deg - prev.yaw_deg)) > 6;
    if (moved || turned) pruned.push({ ...clone(k), auto: markAuto });
  }
  if (pruned.length <= maxCtrl) return pruned;
  const out = [];
  for (let i = 0; i < maxCtrl; i++) {
    const idx = Math.round((i * (pruned.length - 1)) / (maxCtrl - 1));
    out.push(clone(pruned[idx]));
  }
  return out;
}

function clearFutureAutoWaypoints(afterIdx) {
  // When user moves the camera at t, drop unedited baseline future pins so camera stays where you put it
  // instead of snapping back to the original starting point!
  const tCut = S.waypoints[afterIdx]?.t ?? 0;
  S.waypoints = S.waypoints.filter((k, i) => i <= afterIdx || !k.auto || k.t <= tCut);
}

function bakeCameraCurve() {
  if (!S.scene) return;
  S.waypoints = sortedKeys(S.waypoints);
  const wps = S.waypoints;
  if (wps.length === 0) return;
  const dur = roundN(S.orig?.clip.duration_s || wps[wps.length - 1].t, 3);
  const autoAim = $("autoAim")?.checked;
  const tid = lockedTargetId();
  const aimAt = (x, y, t, fallbackYaw, customYaw = false) => {
    if (!customYaw && autoAim && tid) {
      const tgt = entityPosAt(S.scene, tid, t);
      if (tgt) return yawTowards(x, y, tgt.x, tgt.y);
    }
    return fallbackYaw;
  };

  let baked = [];
  if (!S.curveMode || wps.length < 3) {
    // Smoothly sample even 2-point moves so Look-At tracks cleanly along the line
    if (wps.length === 1) {
      const k = wps[0];
      const yaw0 = aimAt(k.x, k.y, k.t, k.yaw_deg, k.customYaw);
      baked.push({ t: roundN(k.t, 3), mmss: mmss(k.t), x: roundN(k.x, 4), y: roundN(k.y, 4), yaw_deg: roundN(wrap180(yaw0), 3) });
    } else {
      for (let i = 0; i < wps.length - 1; i++) {
        const a = wps[i], b = wps[i + 1];
        const useCustom = Boolean(a.customYaw || b.customYaw);
        const steps = Math.max(2, Math.min(8, Math.ceil((b.t - a.t) / 0.5)));
        for (let s = 0; s < steps; s++) {
          const u = s / steps, t = roundN(lerp(a.t, b.t, u), 3);
          const x = lerp(a.x, b.x, u), y = lerp(a.y, b.y, u);
          const yaw = aimAt(x, y, t, lerpAngle(a.yaw_deg, b.yaw_deg, u), useCustom);
          baked.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(wrap180(yaw), 3) });
        }
      }
      const last = wps[wps.length - 1];
      const lastYaw = aimAt(last.x, last.y, last.t, last.yaw_deg, last.customYaw);
      baked.push({ t: roundN(last.t, 3), mmss: mmss(last.t), x: roundN(last.x, 4), y: roundN(last.y, 4), yaw_deg: roundN(wrap180(lastYaw), 3) });
    }
  } else {
    for (let i = 0; i < wps.length - 1; i++) {
      const a = wps[i], b = wps[i + 1];
      const useCustom = Boolean(a.customYaw || b.customYaw);
      const p1 = [a.x, a.y], p2 = [b.x, b.y];
      const p0 = i > 0 ? [wps[i - 1].x, wps[i - 1].y] : [2 * p1[0] - p2[0], 2 * p1[1] - p2[1]];
      const p3 = i + 2 < wps.length ? [wps[i + 2].x, wps[i + 2].y] : [2 * p2[0] - p1[0], 2 * p2[1] - p1[1]];
      const dt = b.t - a.t;
      const segLen = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
      const steps = Math.max(4, Math.min(10, Math.ceil(Math.max(dt / 0.35, segLen * 2.2))));
      for (let s = 0; s < steps; s++) {
        const u = s / steps;
        const t = roundN(lerp(a.t, b.t, u), 3);
        const [x, y] = catmullRom2D(p0, p1, p2, p3, u);
        const yaw = aimAt(x, y, t, lerpAngle(a.yaw_deg, b.yaw_deg, u), useCustom);
        baked.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(wrap180(yaw), 3) });
      }
    }
    const last = wps[wps.length - 1];
    const lastYaw = aimAt(last.x, last.y, last.t, last.yaw_deg, last.customYaw);
    baked.push({
      t: roundN(last.t, 3), mmss: mmss(last.t), x: roundN(last.x, 4), y: roundN(last.y, 4),
      yaw_deg: roundN(wrap180(lastYaw), 3),
    });
  }

  // Hold final camera position through end of clip (never snap back to Start!)
  const tail = baked[baked.length - 1];
  const lastWp = wps[wps.length - 1];
  if (tail && tail.t < dur - 0.05) {
    const endYaw = aimAt(tail.x, tail.y, dur, tail.yaw_deg, lastWp?.customYaw);
    baked.push({ t: dur, mmss: mmss(dur), x: tail.x, y: tail.y, yaw_deg: roundN(wrap180(endYaw), 3) });
  }
  S.scene.camera.track = baked;
}

// ------------------------------------------------------------------ undo / redo
function pushUndo() {
  if (!S.scene) return;
  S.undo.push({ scene: clone(S.scene), waypoints: clone(S.waypoints) });
  if (S.undo.length > 40) S.undo.shift();
  S.redo = [];
  syncHistoryButtons();
}
function syncHistoryButtons() {
  if ($("undoBtn")) $("undoBtn").disabled = S.undo.length === 0;
  if ($("redoBtn")) $("redoBtn").disabled = S.redo.length === 0;
  if ($("delKeyBtn")) {
    const canDel = S.sel.cam != null && S.waypoints.length > 1;
    $("delKeyBtn").disabled = !canDel;
  }
  updateKeyTelemetry();
  renderKeyframeTracks();
}
function doUndo() {
  if (!S.undo.length) return;
  S.redo.push({ scene: clone(S.scene), waypoints: clone(S.waypoints) });
  const prev = S.undo.pop();
  S.scene = prev.scene; S.waypoints = prev.waypoints;
  S.sel.cam = null;
  syncFovUI();
  syncHistoryButtons();
  msg("Undid last edit.");
}
function doRedo() {
  if (!S.redo.length) return;
  S.undo.push({ scene: clone(S.scene), waypoints: clone(S.waypoints) });
  const next = S.redo.pop();
  S.scene = next.scene; S.waypoints = next.waypoints;
  S.sel.cam = null;
  syncFovUI();
  syncHistoryButtons();
  msg("Redid edit.");
}
function syncFovUI() {
  if (!S.scene) return;
  $("fov").value = S.scene.camera.hfov_deg;
  $("fovVal").textContent = Math.round(S.scene.camera.hfov_deg);
}
function updateKeyTelemetry() {
  const el = $("keyTelemetry");
  if (!el) return;
  if (S.sel.cam == null || !S.waypoints[S.sel.cam]) { el.textContent = ""; return; }
  const k = S.waypoints[S.sel.cam];
  el.textContent = `Cam Key #${S.sel.cam + 1} · ${k.t.toFixed(2)}s · yaw ${Math.round(k.yaw_deg)}°`;
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
  S.waypoints = extractWaypointsFromTrack(S.scene.camera.track, 9, !S.run.scene_edit);
  S.t = 0; S.sel.cam = null; S.sel.actor = null; S.undo = []; S.redo = [];
  S.lastOrbitAnchor = null; S.orbitRadius = null;
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
      const shortDesc = e.visual_description.length > 26 ? e.visual_description.slice(0, 25) + "…" : e.visual_description;
      o.textContent = `${e.id} · ${shortDesc}`;
      el.appendChild(o);
    }
  }
  const tgt = entityPosAt(S.scene, lockedTargetId(), 0);
  const c0 = camAt(S.scene, 0);
  const initR = tgt ? Math.max(1.0, Math.min(8.0, Math.hypot(c0.x - tgt.x, c0.y - tgt.y))) : 3.0;
  if ($("orbitRadius")) {
    $("orbitRadius").value = initR.toFixed(1);
    $("orbitRadiusVal").textContent = initR.toFixed(1) + "u";
  }
  const beforeUrl = S.run.clip_url || S.run.reel_url;
  const afterUrl = S.run.reshoot_url || S.run.reel_edit_url;
  setBeforeVideo(beforeUrl, S.run.clip_url ? "BEFORE · Original Clip" : "BEFORE · Verified Reel");
  setAfterVideo(afterUrl, S.run.reshoot_url ? "AFTER · Re-shoot (Omni)" : "AFTER · Edited Reel");
  setCompareMode(Boolean(afterUrl));
  const rounds = Object.keys(S.run.checks).map(Number).sort((a, b) => a - b);
  S.round = rounds.length ? rounds[rounds.length - 1] : null;
  renderInspector();
  badges();
  setActivePov(null);
  if ($("modeBanner")) $("modeBanner").textContent = "";
  syncHistoryButtons();
  msg(S.run.scene_edit
    ? "Loaded your latest saved edit. Drag the camera or actors, or hit Play to compare Before | After."
    : "Hit Play to compare Before | After in sync, or drag the green camera / actors on the board.");
}

function setCompareMode(on) {
  const stage = $("videoStage");
  if (stage) stage.classList.toggle("compare", on);
  if ($("viewCompareBtn")) {
    $("viewCompareBtn").classList.toggle("active", on);
    $("viewCompareBtn").setAttribute("aria-pressed", String(on));
  }
  if ($("viewSingleBtn")) {
    $("viewSingleBtn").classList.toggle("active", !on);
    $("viewSingleBtn").setAttribute("aria-pressed", String(!on));
  }
}
if ($("viewCompareBtn")) $("viewCompareBtn").onclick = () => setCompareMode(true);
if ($("viewSingleBtn")) $("viewSingleBtn").onclick = () => setCompareMode(false);

function setBeforeVideo(url, label = "BEFORE · Original") {
  const v = $("video");
  if ($("beforeBadge")) $("beforeBadge").textContent = label;
  if (url) { v.src = url; v.style.display = "block"; $("noVideo").style.display = "none"; }
  else { v.removeAttribute("src"); v.load(); v.style.display = "none"; $("noVideo").style.display = "flex"; }
}

function setAfterVideo(url, label = "AFTER · Re-shoot") {
  const va = $("videoAfter");
  if (!va) return;
  if ($("afterBadge")) $("afterBadge").textContent = label;
  if (url) { va.src = url; va.style.display = "block"; $("noVideoAfter").style.display = "none"; }
  else { va.removeAttribute("src"); va.load(); va.style.display = "none"; $("noVideoAfter").style.display = "flex"; }
}

function setVideo(url) {
  if (!url) { setBeforeVideo(null); return; }
  const isAfter = /reshoot|edit|compare|map_/.test(url);
  if (isAfter && (S.run?.clip_url || S.run?.reel_url)) {
    const lbl = /reshoot|compare/.test(url) ? "AFTER · Re-shoot (Omni)" : "AFTER · Edited Render";
    setAfterVideo(url, lbl);
    setCompareMode(true);
    seek(0);
    return;
  }
  setBeforeVideo(url, "BEFORE · Original");
}

// ------------------------------------------------------------------ Video-Editor Multi-Track Keyframe Timeline
function renderKeyframeTracks() {
  const host = $("kfTracks");
  if (!host || !S.scene || !S.orig) return;
  const dur = Math.max(0.1, S.orig.clip.duration_s);
  const pct = (t) => `${Math.max(0, Math.min(100, (t / dur) * 100)).toFixed(2)}%`;

  host.innerHTML = "";

  // 1. Time Ruler Row
  const rulerRow = document.createElement("div");
  rulerRow.className = "kf-ruler-row";
  rulerRow.innerHTML = `<span class="kf-label mono" style="color:var(--muted)">Timeline</span>`;
  const ruler = document.createElement("div");
  ruler.className = "kf-ruler";
  const tickCount = Math.min(8, Math.max(3, Math.round(dur / 2)));
  for (let i = 0; i <= tickCount; i++) {
    const tt = (dur * i) / tickCount;
    const sp = document.createElement("span");
    sp.className = "kf-tick mono";
    sp.style.left = pct(tt);
    sp.textContent = `${tt.toFixed(dur < 5 ? 1 : 0)}s`;
    ruler.appendChild(sp);
  }
  const rulerPH = document.createElement("div");
  rulerPH.className = "kf-playhead";
  rulerPH.style.left = pct(S.t);
  ruler.appendChild(rulerPH);

  const scrubLane = (ev, laneEl) => {
    const r = laneEl.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width)));
    seek(frac * dur);
  };
  ruler.addEventListener("pointerdown", (ev) => {
    ruler.setPointerCapture(ev.pointerId);
    S.kfDrag = { kind: "scrub", el: ruler };
    scrubLane(ev, ruler);
  });
  ruler.addEventListener("pointermove", (ev) => { if (S.kfDrag?.kind === "scrub") scrubLane(ev, ruler); });
  ruler.addEventListener("pointerup", () => { if (S.kfDrag?.kind === "scrub") S.kfDrag = null; });
  rulerRow.appendChild(ruler);
  host.appendChild(rulerRow);

  // Helper to build a track lane
  function makeTrackRow(label, color, keys, onAdd, onSelect, onMoveTime) {
    const row = document.createElement("div");
    row.className = "kf-row";
    const lbl = document.createElement("div");
    lbl.className = "kf-label";
    lbl.innerHTML = `<span class="kf-label-name"><span class="kf-dot" style="background:${color}"></span><span>${escapeHtml(label)}</span></span>`;
    const addB = document.createElement("button");
    addB.type = "button";
    addB.className = "kf-add-mini";
    addB.textContent = "+◆";
    addB.title = `Add keyframe for ${label} at current time`;
    addB.onclick = (e) => { e.stopPropagation(); onAdd(); };
    lbl.appendChild(addB);
    row.appendChild(lbl);

    const lane = document.createElement("div");
    lane.className = "kf-lane";
    const ph = document.createElement("div");
    ph.className = "kf-playhead";
    ph.style.left = pct(S.t);
    lane.appendChild(ph);

    lane.addEventListener("pointerdown", (ev) => {
      if (ev.target.classList.contains("kf-diamond")) return;
      scrubLane(ev, lane);
    });

    keys.forEach((k, idx) => {
      const d = document.createElement("button");
      d.type = "button";
      d.className = "kf-diamond" + (k.selected ? " selected" : "");
      d.style.left = pct(k.t);
      d.style.background = color;
      d.title = `${label} @ ${k.t.toFixed(2)}s (click to jump, drag to re-time)`;
      d.setAttribute("aria-label", `${label} keyframe at ${k.t.toFixed(1)} seconds`);

      d.addEventListener("pointerdown", (ev) => {
        ev.stopPropagation();
        d.setPointerCapture(ev.pointerId);
        onSelect(idx);
        S.kfDrag = { kind: "key", lane, idx, pushed: false, onMoveTime };
      });
      d.addEventListener("pointermove", (ev) => {
        if (S.kfDrag?.kind !== "key" || S.kfDrag.idx !== idx) return;
        const r = lane.getBoundingClientRect();
        const frac = Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width)));
        const newT = roundN(frac * dur, 2);
        if (!S.kfDrag.pushed) { pushUndo(); S.kfDrag.pushed = true; }
        onMoveTime(idx, newT);
        d.style.left = pct(newT);
      });
      d.addEventListener("pointerup", () => {
        if (S.kfDrag?.kind === "key") {
          const changed = S.kfDrag.pushed;
          S.kfDrag = null;
          if (changed) syncHistoryButtons();
        }
      });
      lane.appendChild(d);
    });

    row.appendChild(lane);
    host.appendChild(row);
  }

  // 2. Camera Track
  makeTrackRow(
    "Camera",
    COLORS.cam,
    S.waypoints.map((k, i) => ({ t: k.t, selected: S.sel.cam === i })),
    () => {
      const c = camAt(S.scene, S.t);
      addKeyframeAt(c.x, c.y, S.t);
    },
    (idx) => {
      S.sel.cam = idx;
      seek(S.waypoints[idx].t);
      syncHistoryButtons();
    },
    (idx, newT) => {
      S.waypoints[idx].t = newT;
      S.waypoints[idx].mmss = mmss(newT);
      bakeCameraCurve();
      seek(newT);
      markDirty(`Re-timed camera keyframe to ${newT.toFixed(2)}s.`);
    },
  );

  // 3. Actor Tracks
  for (const a of S.scene.actors) {
    const aKeys = sortedKeys(a.track);
    makeTrackRow(
      a.id,
      normColor(a.color, "#f87171"),
      aKeys.map((k, i) => ({ t: k.t, selected: S.sel.actor?.id === a.id && S.sel.actor?.index === i })),
      () => {
        pushUndo();
        const p = actorAt(a, S.t);
        const ks = sortedKeys(a.track);
        const near = ks.findIndex((k) => Math.abs(k.t - S.t) < 0.12);
        const nk = { t: roundN(S.t, 3), mmss: mmss(S.t), x: roundN(p.x, 3), y: roundN(p.y, 3), facing_deg: roundN(p.facing, 2), visible: p.visible };
        if (near >= 0) ks[near] = nk; else ks.push(nk);
        a.track = sortedKeys(ks);
        S.sel.actor = { id: a.id, index: a.track.findIndex((k) => k.t === nk.t) };
        syncHistoryButtons();
        markDirty(`Added ${a.id} keyframe at ${nk.t.toFixed(2)}s.`);
      },
      (idx) => {
        S.sel.actor = { id: a.id, index: idx };
        seek(sortedKeys(a.track)[idx].t);
        syncHistoryButtons();
      },
      (idx, newT) => {
        a.track = sortedKeys(a.track);
        a.track[idx].t = newT;
        a.track[idx].mmss = mmss(newT);
        seek(newT);
        markDirty(`Re-timed ${a.id} keyframe to ${newT.toFixed(2)}s.`);
      },
    );
  }
}

function updateTimelinePlayheads() {
  if (!S.orig) return;
  const dur = Math.max(0.1, S.orig.clip.duration_s);
  const left = `${Math.max(0, Math.min(100, (S.t / dur) * 100)).toFixed(2)}%`;
  document.querySelectorAll(".kf-playhead").forEach((el) => { el.style.left = left; });
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
    } else { pill.textContent = ""; pill.className = "pass-pill"; }
  }
  const bar = $("roundBar");
  if (bar) {
    bar.innerHTML = checks.length
      ? (nPass ? `<span class="pb-pass" style="flex:${nPass}"></span>` : "") +
        (nUnc ? `<span class="pb-unc" style="flex:${nUnc}"></span>` : "") +
        (nFail ? `<span class="pb-fail" style="flex:${nFail}"></span>` : "")
      : "";
  }
  $("roundStats").textContent = checks.length
    ? `${checks.length} checks · ✔ ${nPass} pass · ✘ ${nFail} fail · ? ${nUnc} uncertain` +
      (activeEntry.latency_s ? ` · ${Number(activeEntry.latency_s).toFixed(1)}s` : "")
    : "No checks yet for this run.";

  const changesEl = $("roundChanges");
  if (changesEl) {
    if (activeEntry.changes) { changesEl.textContent = activeEntry.changes; changesEl.hidden = false; }
    else { changesEl.textContent = ""; changesEl.hidden = true; }
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
  if ($("lowconfCount")) $("lowconfCount").textContent = flagged.length ? `${flagged.length} low-conf` : "All tracks verified";
  for (const l of flagged) {
    const li = document.createElement("li");
    li.textContent = `⚠ ${l.entity_id} @ ${mmss(l.t0)}–${mmss(l.t1)} (${l.failed_checks.length} failed)`;
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
  const x = Math.max(12, Math.min(ev.clientX + 18, window.innerWidth - w - 12));
  const y = Math.max(12, Math.min(ev.clientY + 14, window.innerHeight - h - 12));
  im.style.left = x + "px"; im.style.top = y + "px";
}
function hideThumb() { $("thumb").style.display = "none"; }

// ------------------------------------------------------------------ time (synced Before + After + Map!)
function syncAfterTime(t) {
  const va = $("videoAfter");
  if (!va || !va.src || isNaN(va.duration) || va.duration <= 0) return;
  const clamped = Math.max(0, Math.min(t, Math.max(0, va.duration - 0.04)));
  if (Math.abs(va.currentTime - clamped) > 0.12) va.currentTime = clamped;
}

function seek(t) {
  S.t = Math.max(0, Math.min(t, S.orig?.clip.duration_s || 0));
  const v = $("video");
  if (v.src && Math.abs(v.currentTime - S.t) > 0.05) v.currentTime = S.t;
  syncAfterTime(S.t);
  updateTimelinePlayheads();
}
function startMapPreview() {
  const v = $("video"), va = $("videoAfter");
  if (v.src && !v.paused) v.pause();
  if (va && va.src && !va.paused) va.pause();
  S.playing = true;
  S.lastTick = performance.now();
  $("play").textContent = "❚❚";
}
$("time").oninput = (e) => seek(parseFloat(e.target.value));
$("play").onclick = () => {
  const v = $("video"), va = $("videoAfter");
  if (v.src) {
    if (v.paused) {
      if (v.duration && v.currentTime >= v.duration - 0.08) v.currentTime = 0;
      syncAfterTime(v.currentTime);
      v.play().catch(() => {});
      if (va && va.src && $("videoStage")?.classList.contains("compare")) va.play().catch(() => {});
    } else {
      v.pause();
      if (va && va.src) va.pause();
    }
    return;
  }
  S.playing = !S.playing; S.lastTick = performance.now();
  $("play").textContent = S.playing ? "❚❚" : "▶";
};
$("video").onplay = () => {
  S.playing = false;
  $("play").textContent = "❚❚";
  const va = $("videoAfter");
  if (va && va.src && $("videoStage")?.classList.contains("compare")) {
    syncAfterTime($("video").currentTime);
    if (va.paused) va.play().catch(() => {});
  }
};
$("video").onpause = () => {
  const va = $("videoAfter");
  if (va && va.src && !va.paused) va.pause();
  if (!S.playing) $("play").textContent = "▶";
};
$("video").onended = () => {
  if (S.loop) {
    seek(0);
    $("video").play().catch(() => {});
  }
};

if ($("loopToggle")) {
  $("loopToggle").onclick = () => {
    S.loop = !S.loop;
    $("loopToggle").setAttribute("aria-pressed", String(S.loop));
    $("loopToggle").classList.toggle("active-pill", S.loop);
  };
}
if ($("curveToggle")) {
  $("curveToggle").onclick = () => {
    S.curveMode = !S.curveMode;
    $("curveToggle").setAttribute("aria-pressed", String(S.curveMode));
    $("curveToggle").classList.toggle("active-pill", S.curveMode);
    if (S.scene) { pushUndo(); bakeCameraCurve(); syncHistoryButtons(); markDirty(S.curveMode ? "Switched to Smooth Curved motion paths." : "Switched to Linear (straight) segments."); }
  };
}
if ($("actorModeKey") && $("actorModeAll")) {
  const setActorMode = (mode) => {
    S.actorDragMode = mode;
    $("actorModeKey").classList.toggle("active", mode === "key");
    $("actorModeKey").setAttribute("aria-pressed", String(mode === "key"));
    $("actorModeAll").classList.toggle("active", mode === "all");
    $("actorModeAll").setAttribute("aria-pressed", String(mode === "all"));
  };
  $("actorModeKey").onclick = () => setActorMode("key");
  $("actorModeAll").onclick = () => setActorMode("all");
}

function tick(now) {
  const v = $("video");
  if (S.orig) {
    const dur = S.orig.clip.duration_s;
    if (v.src && !v.paused && !isNaN(v.duration)) {
      S.t = Math.min(v.currentTime, dur);
      syncAfterTime(S.t);
    } else if (S.playing) {
      S.t += (now - S.lastTick) / 1000;
      if (S.t > dur) {
        if (S.loop) S.t = 0;
        else { S.t = dur; S.playing = false; $("play").textContent = "▶"; }
      }
      if (v.src && Math.abs(v.currentTime - S.t) > 0.25) v.currentTime = S.t;
      syncAfterTime(S.t);
    }
    S.lastTick = now;
    $("time").value = S.t; $("timeLabel").textContent = mmss(S.t);
    updateTimelinePlayheads();
    const seg = (S.scene.camera.motion_segments || []).find((s) => S.t >= s.t0 && S.t <= s.t1);
    $("segLabel").textContent = seg
      ? [seg.translation, seg.rotation].filter((x) => x && x !== "none" && x !== "static").join(" · ").replaceAll("_", " ")
          .toUpperCase() || "STATIC" : "";
    draw();
  }
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------------ Camera POV & Angle Presets (Scene Locked)
function setActivePov(pov) {
  document.querySelectorAll(".pov-btn[data-pov]").forEach((b) => b.classList.toggle("active", b.dataset.pov === pov));
}

function applyPovPreset(pov) {
  if (!S.scene || !S.orig || !pov) return;
  pushUndo();
  setActivePov(pov);
  const dur = S.orig.clip.duration_s;
  const origCam0 = camAt(S.orig, 0);
  const actorsByX = [...S.scene.actors].sort((a, b) => actorAt(a, 0).x - actorAt(b, 0).x);
  const L0 = actorsByX[0] ? actorAt(actorsByX[0], 0) : { x: -0.8, y: 2.5 };
  const R0 = actorsByX[1] ? actorAt(actorsByX[1], 0) : { x: 0.8, y: 2.5 };
  const mid0 = { x: (L0.x + R0.x) / 2, y: (L0.y + R0.y) / 2 };

  const dx = R0.x - L0.x, dy = R0.y - L0.y, sep = Math.max(0.4, Math.hypot(dx, dy));
  const ux = dx / sep, uy = dy / sep;
  let nx = -uy, ny = ux;
  if ((origCam0.x - mid0.x) * nx + (origCam0.y - mid0.y) * ny < 0) { nx = -nx; ny = -ny; }

  const steps = Math.max(6, Math.ceil(dur / 1.0));
  const track = [];
  let newFov = S.orig.camera.hfov_deg;
  let bannerText = "";

  for (let i = 0; i <= steps; i++) {
    const f = i / steps, t = roundN(dur * f, 3);
    const L = actorsByX[0] ? actorAt(actorsByX[0], t) : L0;
    const R = actorsByX[1] ? actorAt(actorsByX[1], t) : R0;
    const mid = { x: (L.x + R.x) / 2, y: (L.y + R.y) / 2 };
    const oc = camAt(S.orig, t);
    let x = oc.x, y = oc.y, yaw = oc.yaw;

    if (pov === "ots_lr") {
      x = L.x - 0.85 * ux + 0.55 * nx;
      y = L.y - 0.85 * uy + 0.55 * ny;
      yaw = yawTowards(x, y, R.x, R.y);
      newFov = 48;
      bannerText = `Over-the-Shoulder · behind ${actorsByX[0]?.id || "left"} → ${actorsByX[1]?.id || "right"} (48° FOV)`;
    } else if (pov === "ots_rl") {
      x = R.x + 0.85 * ux + 0.55 * nx;
      y = R.y + 0.85 * uy + 0.55 * ny;
      yaw = yawTowards(x, y, L.x, L.y);
      newFov = 48;
      bannerText = `Over-the-Shoulder · behind ${actorsByX[1]?.id || "right"} → ${actorsByX[0]?.id || "left"} (48° FOV)`;
    } else if (pov === "reverse") {
      const rx = oc.x - mid.x, ry = oc.y - mid.y;
      x = mid.x - rx;
      y = mid.y - ry;
      yaw = yawTowards(x, y, mid.x, mid.y);
      newFov = Math.min(72, Math.max(48, S.orig.camera.hfov_deg));
      bannerText = `Reverse 180° Angle · opposite side of set (${Math.round(newFov)}° FOV)`;
    } else if (pov === "profile") {
      x = mid.x + (sep * 0.5 + 1.65) * ux + 0.35 * nx;
      y = mid.y + (sep * 0.5 + 1.65) * uy + 0.35 * ny;
      yaw = yawTowards(x, y, mid.x, mid.y);
      newFov = 54;
      bannerText = `Side Profile POV · lateral two-shot (54° FOV)`;
    } else if (pov === "wide") {
      const fv = fwdVec(oc.yaw);
      x = oc.x - fv[0] * 1.15;
      y = oc.y - fv[1] * 1.15;
      yaw = yawTowards(x, y, mid.x, mid.y);
      newFov = 95;
      bannerText = `Wide Establishing POV · pulled back on 95° ultra-wide lens`;
    } else if (pov === "closeup") {
      const tgt = entityPosAt(S.scene, lockedTargetId(), t) || R;
      x = tgt.x + 1.15 * nx;
      y = tgt.y + 1.15 * ny;
      yaw = yawTowards(x, y, tgt.x, tgt.y);
      newFov = 32;
      bannerText = `Telephoto Close-Up · ${lockedTargetId() || "subject"} on 32° lens`;
    }
    track.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }

  S.scene.camera.hfov_deg = newFov;
  S.scene.camera.track = track;
  S.scene.camera.motion_segments = [{ t0: 0, t1: roundN(dur, 3), translation: "static", rotation: "none" }];
  S.waypoints = extractWaypointsFromTrack(track, 5);
  syncFovUI();
  if ($("modeBanner")) $("modeBanner").textContent = bannerText;
  syncHistoryButtons();
  msg(`${bannerText}. Drag camera or adjust FOV, then hit Re-render or Re-shoot (Omni).`);
  startMapPreview();
}

document.querySelectorAll(".pov-btn[data-pov]").forEach((b) => {
  b.onclick = () => applyPovPreset(b.dataset.pov);
});

if ($("sceneLockBtn")) {
  $("sceneLockBtn").onclick = () => {
    S.sceneLocked = !S.sceneLocked;
    $("sceneLockBtn").classList.toggle("active-pill", S.sceneLocked);
    $("sceneLockBtn").setAttribute("aria-pressed", String(S.sceneLocked));
    $("sceneLockBtn").textContent = S.sceneLocked ? "🔒 Actors Locked" : "🔓 Actors Editable";
    msg(S.sceneLocked
      ? "Actors & props locked — drags only move the camera."
      : "Actors & props unlocked — drag any person to move them, or drag the dot in front of them to turn their facing.");
  };
}

// ------------------------------------------------------------------ interactive choreography
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
    if (tool === "draw") banner.textContent = "Draw Curve: click & drag anywhere to sketch a smooth curved camera path";
    else if (tool === "orbit") banner.textContent = "Orbit Tool: click a character and drag outward to sculpt an orbit circle";
    else banner.textContent = "";
  }
  canvas.style.cursor = tool === "select" ? "default" : "crosshair";
}
document.querySelectorAll(".tool-btn").forEach((b) => { b.onclick = () => setTool(b.dataset.tool); });

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

  // Clean editable control waypoints every 45° (e.g. 9 handles for 360°), then spline-baked into smooth 25+ keys!
  const ctrlSteps = Math.max(4, Math.min(16, Math.round(Math.abs(sweepDeg) / 45)));
  const autoAim = $("autoAim") ? $("autoAim").checked : true;
  const wps = [];
  for (let i = 0; i <= ctrlSteps; i++) {
    const f = i / ctrlSteps;
    const t = roundN(dur * f, 3);
    const ctr = anchor.entityId ? (entityPosAt(S.scene, anchor.entityId, t) || center0) : center0;
    const phi = startAngle + rad(sweepDeg * f * dir);
    const x = ctr.x + r * Math.cos(phi);
    const y = ctr.y + r * Math.sin(phi);
    const yaw = autoAim ? yawTowards(x, y, ctr.x, ctr.y) : wrap180(c0.yaw + sweepDeg * f * dir);
    wps.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }
  S.waypoints = wps;
  bakeCameraCurve();
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
  markDirty(`${sweepDeg}° ${dir > 0 ? "CCW" : "CW"} curved orbit around ${anchor.entityId || "point"} (R=${r.toFixed(1)}u). Drag any dot to reshape!`);
  startMapPreview();
}

function buildPushInTrack() {
  if (!S.scene) return;
  const dur = S.orig.clip.duration_s;
  const tid = lockedTargetId();
  const c0 = camAt(S.scene, 0);
  const tgt0 = entityPosAt(S.scene, tid, 0);
  const f0 = fwdVec(c0.yaw);
  const tx = tgt0 ? tgt0.x : c0.x + f0[0] * 4, ty = tgt0 ? tgt0.y : c0.y + f0[1] * 4;
  const dx = tx - c0.x, dy = ty - c0.y, dist = Math.hypot(dx, dy);
  const moveDist = Math.max(0.8, dist * 0.52);
  const ux = dist > 0.05 ? dx / dist : f0[0], uy = dist > 0.05 ? dy / dist : f0[1];
  pushUndo();
  const wps = [];
  for (let i = 0; i <= 4; i++) {
    const f = i / 4, ease = f * f * (3 - 2 * f), t = roundN(dur * f, 3);
    const x = c0.x + ux * moveDist * ease, y = c0.y + uy * moveDist * ease;
    const tgt = entityPosAt(S.scene, tid, t) || { x: tx, y: ty };
    const yaw = ($("autoAim")?.checked ?? true) ? yawTowards(x, y, tgt.x, tgt.y) : c0.yaw;
    wps.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }
  S.waypoints = wps;
  bakeCameraCurve();
  S.scene.camera.motion_segments = [{ t0: 0, t1: roundN(dur, 3), translation: "dolly_in", rotation: "none" }];
  S.lastOrbitAnchor = null; S.sel.cam = null;
  syncHistoryButtons();
  markDirty(`Applied smooth push-in toward ${tid || "frame center"}.`);
  startMapPreview();
}

function commitDrawnStroke(pts) {
  if (!S.scene || pts.length < 2) return;
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  const totalLen = cum[cum.length - 1];
  if (totalLen < 0.25) return;

  const dur = S.orig.clip.duration_s;
  // 8–12 clean editable spline control points along the sketched stroke
  const nCtrl = Math.max(6, Math.min(12, Math.round(totalLen * 1.1)));
  const sampled = [];
  for (let i = 0; i <= nCtrl; i++) {
    const targetD = (totalLen * i) / nCtrl;
    let j = 1; while (j < cum.length - 1 && cum[j] < targetD) j++;
    const segLen = cum[j] - cum[j - 1];
    const frac = segLen > 1e-6 ? (targetD - cum[j - 1]) / segLen : 0;
    sampled.push([lerp(pts[j - 1][0], pts[j][0], frac), lerp(pts[j - 1][1], pts[j][1], frac)]);
  }
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
  const wps = [];
  for (let i = 0; i < sampled.length; i++) {
    const f = i / (sampled.length - 1), t = roundN(dur * f, 3), [x, y] = sampled[i];
    const tgt = autoAim ? entityPosAt(S.scene, tid, t) : null;
    const yaw = tgt ? yawTowards(x, y, tgt.x, tgt.y)
      : yawTowards(sampled[Math.max(i - 1, 0)][0], sampled[Math.max(i - 1, 0)][1],
                   sampled[Math.min(i + 1, sampled.length - 1)][0], sampled[Math.min(i + 1, sampled.length - 1)][1]);
    wps.push({ t, mmss: mmss(t), x: roundN(x, 4), y: roundN(y, 4), yaw_deg: roundN(yaw, 3) });
  }
  S.waypoints = wps;
  bakeCameraCurve();
  let segMove = "dolly_in";
  if (autoAim && tid) {
    const c = entityPosAt(S.scene, tid, dur / 2);
    if (c) {
      let sweep = 0;
      for (let i = 1; i < sampled.length; i++) {
        sweep += wrap180(deg(Math.atan2(sampled[i][1] - c.y, sampled[i][0] - c.x) - Math.atan2(sampled[i - 1][1] - c.y, sampled[i - 1][0] - c.x)));
      }
      if (Math.abs(sweep) > 35) segMove = sweep > 0 ? "arc_right" : "arc_left";
    }
  }
  S.scene.camera.motion_segments = [{ t0: 0, t1: roundN(dur, 3), translation: segMove, rotation: "none" }];
  S.lastOrbitAnchor = null; S.sel.cam = null;
  syncHistoryButtons();
  markDirty(`Sketched smooth curved camera path (${wps.length} keyframes)${autoAim && tid ? ` locked onto ${tid}` : ""}.`);
  startMapPreview();
}

function addKeyframeAt(wx, wy, t) {
  if (!S.scene) return;
  pushUndo();
  const tid = lockedTargetId();
  const tgt = ($("autoAim")?.checked ?? true) ? entityPosAt(S.scene, tid, t) : null;
  const yaw = tgt ? yawTowards(wx, wy, tgt.x, tgt.y) : camAt(S.scene, t).yaw;
  const nearIdx = S.waypoints.findIndex((k) => Math.abs(k.t - t) < 0.2);
  const nk = { t: roundN(t, 3), mmss: mmss(t), x: roundN(wx, 4), y: roundN(wy, 4), yaw_deg: roundN(yaw, 3), auto: false };
  if (nearIdx >= 0) S.waypoints[nearIdx] = nk;
  else S.waypoints.push(nk);
  S.waypoints = sortedKeys(S.waypoints);
  S.sel.cam = S.waypoints.findIndex((k) => k.t === nk.t);
  clearFutureAutoWaypoints(S.sel.cam);
  bakeCameraCurve();
  S.lastOrbitAnchor = null;
  syncHistoryButtons();
  markDirty(`Added curved camera keyframe at ${nk.t.toFixed(2)}s.`);
}

function deleteSelectedKey() {
  if (!S.scene || S.sel.cam == null || S.waypoints.length <= 1) return;
  pushUndo();
  S.waypoints.splice(S.sel.cam, 1);
  S.sel.cam = null;
  bakeCameraCurve();
  syncHistoryButtons();
  markDirty("Deleted camera keyframe.");
}

// Toolbar bindings
if ($("quickOrbit360")) $("quickOrbit360").onclick = () => {
  if (!S.scene) return;
  S.orbitSweep = 360;
  if ($("orbitSweep")) { $("orbitSweep").value = 360; $("orbitSweepVal").textContent = "360°"; }
  pushUndo(); buildOrbitTrack({ entityId: lockedTargetId() }, 360, S.orbitDir, S.orbitRadius);
};
if ($("quickOrbit180")) $("quickOrbit180").onclick = () => {
  if (!S.scene) return;
  S.orbitSweep = 180;
  if ($("orbitSweep")) { $("orbitSweep").value = 180; $("orbitSweepVal").textContent = "180°"; }
  pushUndo(); buildOrbitTrack({ entityId: lockedTargetId() }, 180, S.orbitDir, S.orbitRadius);
};
if ($("quickDollyIn")) $("quickDollyIn").onclick = () => buildPushInTrack();
if ($("closeLoopBtn")) $("closeLoopBtn").onclick = () => {
  if (!S.scene || S.waypoints.length < 2) {
    msg("Place at least 2 camera positions at different times first, then click Close Loop.");
    return;
  }
  pushUndo();
  const dur = roundN(S.orig.clip.duration_s, 3);
  const first = S.waypoints[0];
  // Remove any existing waypoint right at dur, then close smoothly back to Start!
  S.waypoints = S.waypoints.filter((k) => k.t < dur - 0.15);
  S.waypoints.push({ t: dur, mmss: mmss(dur), x: first.x, y: first.y, yaw_deg: first.yaw_deg, auto: false });
  bakeCameraCurve();
  syncHistoryButtons();
  markDirty("Connected your camera waypoints smoothly back to Start (closed loop)!");
  startMapPreview();
};
if ($("quickFlipDir")) $("quickFlipDir").onclick = () => {
  S.orbitDir *= -1;
  $("quickFlipDir").textContent = S.orbitDir > 0 ? "CCW ↺" : "CW ↻";
  if (S.lastOrbitAnchor && S.scene) { pushUndo(); buildOrbitTrack(S.lastOrbitAnchor, S.orbitSweep, S.orbitDir, S.orbitRadius); }
};
if ($("orbitSweep")) $("orbitSweep").oninput = (e) => {
  S.orbitSweep = parseInt(e.target.value, 10);
  $("orbitSweepVal").textContent = `${S.orbitSweep}°`;
  if (S.lastOrbitAnchor && S.scene) buildOrbitTrack(S.lastOrbitAnchor, S.orbitSweep, S.orbitDir, S.orbitRadius);
};
if ($("orbitRadius")) $("orbitRadius").oninput = (e) => {
  S.orbitRadius = parseFloat(e.target.value);
  $("orbitRadiusVal").textContent = `${S.orbitRadius.toFixed(1)}u`;
  if (S.lastOrbitAnchor && S.scene) buildOrbitTrack(S.lastOrbitAnchor, S.orbitSweep, S.orbitDir, S.orbitRadius);
};
if ($("aimTarget")) $("aimTarget").onchange = () => {
  if (S.lastOrbitAnchor && S.scene) { pushUndo(); buildOrbitTrack({ entityId: lockedTargetId() }, S.orbitSweep, S.orbitDir, S.orbitRadius); }
  else if ($("autoAim")?.checked && S.scene) { bakeCameraCurve(); }
};
if ($("autoAim")) $("autoAim").onchange = () => {
  if (!S.scene) return;
  if ($("autoAim").checked) { for (const k of S.waypoints) k.customYaw = false; }
  bakeCameraCurve();
};
if ($("addKeyBtn")) $("addKeyBtn").onclick = () => { if (S.scene) { const c = camAt(S.scene, S.t); addKeyframeAt(c.x, c.y, S.t); } };
if ($("delKeyBtn")) $("delKeyBtn").onclick = deleteSelectedKey;
if ($("undoBtn")) $("undoBtn").onclick = doUndo;
if ($("redoBtn")) $("redoBtn").onclick = doRedo;

window.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); }
  else if ((e.key === "Delete" || e.key === "Backspace") && S.sel.cam != null) { e.preventDefault(); deleteSelectedKey(); }
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
  const mx = Math.max(1.5, (x1 - x0) * 0.17), my = Math.max(1.5, (y1 - y0) * 0.17);
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
const liveAimHandle = (cam) => { const f = fwdVec(cam.yaw); return S.view.toPx(cam.x + f[0] * 1.45, cam.y + f[1] * 1.45); };
const actorFacingHandle = (p) => { const f = fwdVec(p.facing); return S.view.toPx(p.x + f[0] * 0.85, p.y + f[1] * 0.85); };

function draw() {
  const sc = S.scene; if (!sc) return;
  if (!S.drag && !S.drawStroke && !S.orbitDraft) S.view = fitView(sc);
  const V = S.view, P = V.toPx, dpr = window.devicePixelRatio || 1;
  ctx.fillStyle = COLORS.bg; ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Blueprint dual-density grid
  ctx.lineWidth = 1;
  for (let x = Math.ceil(V.x0 * 2) / 2; x <= V.x1; x += 0.5) {
    ctx.strokeStyle = Math.abs(x - Math.round(x)) < 1e-4 ? COLORS.gridMajor : COLORS.gridMinor;
    line(P(x, V.y0), P(x, V.y1));
  }
  for (let y = Math.ceil(V.y0 * 2) / 2; y <= V.y1; y += 0.5) {
    ctx.strokeStyle = Math.abs(y - Math.round(y)) < 1e-4 ? COLORS.gridMajor : COLORS.gridMinor;
    line(P(V.x0, y), P(V.x1, y));
  }

  // Room floor + walls
  const room = sc.room.outline || [];
  if (room.length > 2) {
    const rPts = room.map((p) => P(...p));
    ctx.fillStyle = COLORS.roomFill;
    ctx.beginPath(); ctx.moveTo(...rPts[0]);
    for (const p of rPts.slice(1)) ctx.lineTo(...p);
    ctx.closePath(); ctx.fill();
    ctx.strokeStyle = COLORS.room; ctx.lineWidth = 1.75 * dpr; ctx.stroke();
  }

  // Objects
  for (const o of sc.objects) drawObject(o, P, V.s, dpr);

  // Ghost (original) camera path
  if (S.orig && JSON.stringify(S.orig.camera.track) !== JSON.stringify(sc.camera.track)) {
    ctx.save();
    ctx.setLineDash([6 * dpr, 6 * dpr]); ctx.strokeStyle = COLORS.ghost; ctx.lineWidth = 1.6 * dpr;
    poly(sortedKeys(S.orig.camera.track).map((k) => P(k.x, k.y)));
    ctx.restore();
  }

  // Smooth curved camera path + directional chevrons
  const camPx = sortedKeys(sc.camera.track).map((k) => P(k.x, k.y));
  ctx.strokeStyle = COLORS.camDim; ctx.lineWidth = 2.6 * dpr;
  poly(camPx);
  drawPathArrows(camPx, dpr);

  // Look-At sightline & reticle
  const cam = camAt(sc, S.t);
  const [cx, cy] = P(cam.x, cam.y);
  const aimPos = $("autoAim")?.checked ? entityPosAt(sc, lockedTargetId(), S.t) : null;
  if (aimPos) {
    const [ax, ay] = P(aimPos.x, aimPos.y);
    ctx.save();
    ctx.setLineDash([4 * dpr, 5 * dpr]); ctx.strokeStyle = "rgba(79,158,255,0.45)"; ctx.lineWidth = 1.4 * dpr;
    line([cx, cy], [ax, ay]);
    ctx.setLineDash([]); ctx.strokeStyle = "rgba(79,158,255,0.85)"; ctx.lineWidth = 1.8 * dpr;
    ctx.beginPath(); ctx.arc(ax, ay, Math.max(17 * dpr, 0.46 * V.s), 0, 2 * Math.PI); ctx.stroke();
    ctx.restore();
  }

  // Actors
  for (const a of sc.actors) drawActor(a, P, V.s, dpr);

  // Editable camera control waypoints (clean diamonds + timestamp labels matching the timeline)
  S.waypoints.forEach((k, i) => {
    const [px, py] = P(k.x, k.y);
    const isSel = i === S.sel.cam;
    const isHov = S.hover?.kind === "cam_wp" && S.hover.index === i;
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(Math.PI / 4);
    const sz = (isSel || isHov ? 6.5 : 4.8) * dpr;
    if (isSel || isHov) {
      ctx.fillStyle = "rgba(62,232,139,0.28)";
      ctx.fillRect(-sz * 1.6, -sz * 1.6, sz * 3.2, sz * 3.2);
    }
    ctx.fillStyle = isSel ? "#ffffff" : COLORS.cam;
    ctx.strokeStyle = "#090b10"; ctx.lineWidth = 1.5 * dpr;
    ctx.fillRect(-sz, -sz, sz * 2, sz * 2);
    ctx.strokeRect(-sz, -sz, sz * 2, sz * 2);
    ctx.restore();
    if (S.waypoints.length > 1) {
      pillLabel(`${k.t.toFixed(k.t % 1 ? 1 : 0)}s`, px, py + 14 * dpr, dpr, isSel ? "#ffffff" : "#9beec1");
    }
  });

  // Camera FOV cone
  const hf = sc.camera.hfov_deg, len = 4.2, low = lowConfAt("camera", S.t);
  const l = fwdVec(cam.yaw + hf / 2), r = fwdVec(cam.yaw - hf / 2), f = fwdVec(cam.yaw);
  const pL = P(cam.x + l[0] * len, cam.y + l[1] * len), pR = P(cam.x + r[0] * len, cam.y + r[1] * len);
  const pMid = P(cam.x + f[0] * len, cam.y + f[1] * len);
  const grad = ctx.createLinearGradient(cx, cy, pMid[0], pMid[1]);
  grad.addColorStop(0, `rgba(62,232,139,${low ? 0.16 : 0.34})`);
  grad.addColorStop(1, "rgba(62,232,139,0.02)");
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(...pL); ctx.lineTo(...pR); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = `rgba(62,232,139,${low ? 0.25 : 0.45})`; ctx.lineWidth = 1.2 * dpr;
  line([cx, cy], pL); line([cx, cy], pR);

  // Always-visible Live Lens Aim Handle right in front of the camera
  const aimH = liveAimHandle(cam);
  const aimHov = S.hover?.kind === "cam_aim" || S.drag?.kind === "cam_aim";
  ctx.strokeStyle = aimHov ? "#ffffff" : "rgba(255,255,255,0.75)";
  ctx.lineWidth = (aimHov ? 2.2 : 1.5) * dpr;
  line([cx, cy], aimH);
  ctx.fillStyle = aimHov ? "#ffffff" : "rgba(11,14,20,0.85)";
  ctx.beginPath(); ctx.arc(aimH[0], aimH[1], (aimHov ? 7.5 : 6) * dpr, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();

  // Directly draggable Live Camera Body (with subtle halo on hover)
  const bodyHov = S.hover?.kind === "cam_live" || S.drag?.kind === "cam_live";
  if (bodyHov) {
    ctx.strokeStyle = "rgba(62,232,139,0.7)"; ctx.lineWidth = 2 * dpr;
    ctx.beginPath(); ctx.arc(cx, cy, 17 * dpr, 0, 2 * Math.PI); ctx.stroke();
  }
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-rad(cam.yaw));
  ctx.fillStyle = bodyHov ? "#6ef5a9" : COLORS.cam;
  roundRect(-9 * dpr, -7 * dpr, 18 * dpr, 14 * dpr, 4 * dpr);
  ctx.fillStyle = "#d4ffe7";
  ctx.beginPath();
  ctx.moveTo(-4.5 * dpr, -7 * dpr); ctx.lineTo(4.5 * dpr, -7 * dpr);
  ctx.lineTo(7 * dpr, -13 * dpr); ctx.lineTo(-7 * dpr, -13 * dpr);
  ctx.closePath(); ctx.fill();
  ctx.restore();

  // Freehand Draw preview
  if (S.drawStroke && S.drawStroke.length > 1) {
    const dPts = S.drawStroke.map((pt) => P(pt[0], pt[1]));
    ctx.save();
    ctx.strokeStyle = "#4f9eff"; ctx.lineWidth = 3.2 * dpr; ctx.lineCap = "round"; ctx.lineJoin = "round";
    poly(dPts); drawPathArrows(dPts, dpr, "#93c5fd");
    ctx.restore();
  }

  // Orbit Ring sculpting preview
  if (S.orbitDraft) {
    const { cx: ox, cy: oy, r } = S.orbitDraft;
    const [opx, opy] = P(ox, oy);
    const rPx = Math.max(12 * dpr, r * V.s);
    ctx.save();
    ctx.strokeStyle = "#3ee88b"; ctx.lineWidth = 2.2 * dpr; ctx.setLineDash([7 * dpr, 5 * dpr]);
    ctx.beginPath(); ctx.arc(opx, opy, rPx, 0, 2 * Math.PI); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = "rgba(62,232,139,0.07)";
    ctx.beginPath(); ctx.arc(opx, opy, rPx, 0, 2 * Math.PI); ctx.fill();
    pillLabel(`${S.orbitSweep}° ${S.orbitDir > 0 ? "CCW" : "CW"} · R=${r.toFixed(1)}u`, opx, opy - rPx - 12 * dpr, dpr, "#3ee88b");
    ctx.restore();
  }
}

function drawPathArrows(pts, dpr, color = "rgba(169,247,203,0.85)") {
  if (pts.length < 2) return;
  let accum = 0; const step = 62 * dpr; ctx.fillStyle = color;
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1], seg = Math.hypot(dx, dy);
    if (seg < 2) continue;
    accum += seg;
    if (accum >= step) {
      accum = 0;
      const mx = (pts[i][0] + pts[i - 1][0]) / 2, my = (pts[i][1] + pts[i - 1][1]) / 2, ang = Math.atan2(dy, dx);
      ctx.save(); ctx.translate(mx, my); ctx.rotate(ang);
      ctx.beginPath(); ctx.moveTo(5 * dpr, 0); ctx.lineTo(-4 * dpr, -3.5 * dpr); ctx.lineTo(-4 * dpr, 3.5 * dpr);
      ctx.closePath(); ctx.fill(); ctx.restore();
    }
  }
}

function drawObject(o, P, s, dpr) {
  const [px, py] = P(...o.center), w = o.size[0] * s, h = o.size[1] * s, low = lowConfAt(o.id, S.t);
  const hov = S.hover?.kind === "obj" && S.hover.id === o.id;
  ctx.save(); ctx.translate(px, py); ctx.rotate(-rad(o.rotation_deg || 0));
  ctx.globalAlpha = low ? 0.45 : 0.85; ctx.fillStyle = normColor(o.color, "#64748b");
  if (o.shape === "circle") { ctx.beginPath(); ctx.arc(0, 0, w / 2, 0, 2 * Math.PI); ctx.fill(); }
  else {
    const rw = Math.max(w, 6), rh = Math.max(h, 6);
    ctx.fillRect(-rw / 2, -rh / 2, rw, rh);
    ctx.strokeStyle = hov ? "#ffffff" : "rgba(255,255,255,0.22)"; ctx.lineWidth = (hov ? 2 : 1) * dpr;
    ctx.strokeRect(-rw / 2, -rh / 2, rw, rh);
  }
  ctx.restore(); ctx.globalAlpha = 1;
  pillLabel(`${o.type}${low ? " ?" : ""}`, px, py - Math.max(h, w) / 2 - 10 * dpr, dpr, low ? "#f5bf4f" : "#cbd5e1");
}

function drawActor(a, P, s, dpr) {
  const col = normColor(a.color, "#f87171");
  const keys = sortedKeys(a.track);
  const aPts = keys.map((k) => P(k.x, k.y));
  ctx.strokeStyle = col + "66"; ctx.lineWidth = 2 * dpr;
  poly(aPts);
  drawPathArrows(aPts, dpr, col + "aa");
  ctx.fillStyle = col + "cc";
  for (const k of keys) { const [x, y] = P(k.x, k.y); dot(x, y, 3.2 * dpr); }
  const now = actorAt(a, S.t); if (!now.visible) return;
  const [px, py] = P(now.x, now.y), low = lowConfAt(a.id, S.t), f = fwdVec(now.facing);
  const hov = (S.hover?.kind === "actor_live" && S.hover.id === a.id) || (S.drag?.kind === "actor_live" && S.drag.id === a.id);
  if (hov) {
    ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2 * dpr;
    ctx.beginPath(); ctx.arc(px, py, Math.max(18 * dpr, 0.48 * s), 0, 2 * Math.PI); ctx.stroke();
  }
  ctx.save(); ctx.translate(px, py); ctx.rotate(-rad(now.facing));
  ctx.globalAlpha = low ? 0.5 : 1; ctx.fillStyle = col;
  ctx.beginPath(); ctx.ellipse(0, 0, Math.max(12 * dpr, 0.36 * s), Math.max(6 * dpr, 0.18 * s), 0, 0, 2 * Math.PI); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.65)"; ctx.lineWidth = 1.4 * dpr; ctx.stroke();
  ctx.fillStyle = "#f5d0b0"; ctx.beginPath(); ctx.arc(0, 0, Math.max(5 * dpr, 0.13 * s), 0, 2 * Math.PI); ctx.fill();
  ctx.restore(); ctx.globalAlpha = 1;
  ctx.strokeStyle = col; ctx.lineWidth = 2 * dpr;
  const fh = actorFacingHandle(now);
  line([px, py], fh);
  // Interactive Facing / Turn Handle at the tip of the actor's heading arrow!
  if (!S.sceneLocked) {
    const fHov = (S.hover?.kind === "actor_face" && S.hover.id === a.id) || (S.drag?.kind === "actor_face" && S.drag.id === a.id);
    ctx.fillStyle = fHov ? "#ffffff" : "#0b0e14";
    ctx.strokeStyle = fHov ? "#ffffff" : col;
    ctx.lineWidth = (fHov ? 2.4 : 1.8) * dpr;
    ctx.beginPath(); ctx.arc(fh[0], fh[1], (fHov ? 6.8 : 5.2) * dpr, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
  }
  pillLabel(`${a.id}${low ? " ?" : ""}`, px, py - Math.max(16 * dpr, 0.44 * s) - 9 * dpr, dpr, low ? "#f5bf4f" : "#f8fafc");
}

function pillLabel(text, x, y, dpr, color) {
  ctx.save();
  ctx.font = `600 ${10.5 * dpr}px -apple-system, system-ui, sans-serif`;
  const w = ctx.measureText(text).width + 10 * dpr, h = 16 * dpr;
  ctx.fillStyle = "rgba(9,11,16,0.8)";
  roundRect(x - w / 2, y - h / 2, w, h, 5 * dpr);
  ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, x, y + 0.5 * dpr);
  ctx.restore();
}

function line(a, b) { ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); }
function poly(pts, close = false) { if (pts.length < 2) return; ctx.beginPath(); ctx.moveTo(...pts[0]);
  for (const p of pts.slice(1)) ctx.lineTo(...p); if (close) ctx.closePath(); ctx.stroke(); }
function dot(x, y, r) { ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); }
function roundRect(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); ctx.fill(); }

// ------------------------------------------------------------------ direct video-editor hit testing & dragging
function hit(px, py) {
  if (!S.view || !S.scene) return null;
  const dpr = window.devicePixelRatio || 1, R = 13 * dpr, P = S.view.toPx, sc = S.scene;
  const near = (x, y, r = R) => Math.hypot(px - x, py - y) < r;

  // 1. Live Lens Aim Handle (always easy to grab!)
  const cam = camAt(sc, S.t);
  if (near(...liveAimHandle(cam), 13 * dpr)) return { kind: "cam_aim" };

  // 2. Live Camera Body (auto-keyframe at current time S.t!)
  if (near(...P(cam.x, cam.y), 18 * dpr)) return { kind: "cam_live" };

  // 3. Camera control waypoints (diamonds)
  for (let i = 0; i < S.waypoints.length; i++) {
    if (near(...P(S.waypoints[i].x, S.waypoints[i].y), 12 * dpr)) return { kind: "cam_wp", index: i };
  }

  // When Scene Locked is ON, actors & props stay fixed so scene action never changes
  if (S.sceneLocked) return null;

  // 4. Actor Facing / Turn Handles (grab the circle in front of any actor to spin them!)
  for (const a of sc.actors) {
    const p = actorAt(a, S.t);
    if (p.visible && near(...actorFacingHandle(p), 12 * dpr)) {
      return { kind: "actor_face", id: a.id };
    }
  }

  // 5. Live Actor Bodies (grab any actor directly!)
  for (const a of sc.actors) {
    const p = actorAt(a, S.t);
    if (p.visible && near(...P(p.x, p.y), Math.max(18 * dpr, 0.42 * S.view.s))) {
      return { kind: "actor_live", id: a.id };
    }
  }

  // 5. Actor key dots
  for (const a of sc.actors) {
    const ks = sortedKeys(a.track);
    for (let i = 0; i < ks.length; i++) if (near(...P(ks[i].x, ks[i].y), 10 * dpr)) return { kind: "actor_key", id: a.id, index: i };
  }

  // 6. Props / objects
  for (const o of sc.objects) {
    const [x, y] = P(...o.center);
    if (Math.hypot(px - x, py - y) < Math.max(R, (Math.max(...o.size) * S.view.s) / 2)) return { kind: "obj", id: o.id };
  }
  return null;
}

function hoverHelpText(h) {
  if (!h) return "";
  if (h.kind === "cam_live") return `Drag camera to move @ ${S.t.toFixed(1)}s (auto-keyframe + smooth curve)`;
  if (h.kind === "cam_aim") return `Drag lens handle to rotate camera aim @ ${S.t.toFixed(1)}s`;
  if (h.kind === "cam_wp") return `Drag diamond #${h.index + 1} (${S.waypoints[h.index]?.t.toFixed(1)}s) to bend camera curve`;
  if (h.kind === "actor_face") return `Drag facing circle to turn ${h.id} @ ${S.t.toFixed(1)}s`;
  if (h.kind === "actor_live") {
    return S.actorDragMode === "all"
      ? `Drag ${h.id} to reposition across the whole shot`
      : `Drag ${h.id} to animate position @ ${S.t.toFixed(1)}s`;
  }
  if (h.kind === "actor_key") return `Drag ${h.id} waypoint #${h.index + 1}`;
  if (h.kind === "obj") return `Drag prop ${h.id} to move placement`;
  return "";
}

function nearestEntityAtWorld(wx, wy) {
  let best = null, bestD = 1.4;
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

function ensureCamWaypointAt(t) {
  const nearIdx = S.waypoints.findIndex((k) => Math.abs(k.t - t) < 0.35);
  if (nearIdx >= 0) {
    S.waypoints[nearIdx].auto = false;
    clearFutureAutoWaypoints(nearIdx);
    return nearIdx;
  }
  const c = camAt(S.scene, t);
  const nk = { t: roundN(t, 3), mmss: mmss(t), x: roundN(c.x, 4), y: roundN(c.y, 4), yaw_deg: roundN(c.yaw, 3), auto: false };
  S.waypoints.push(nk);
  S.waypoints = sortedKeys(S.waypoints);
  const idx = S.waypoints.findIndex((k) => k.t === nk.t);
  clearFutureAutoWaypoints(idx);
  return idx;
}

canvas.addEventListener("pointerdown", (ev) => {
  if (!S.scene || !S.view) return;
  const px = evPx(ev), [wx, wy] = S.view.toWorld(...px);

  if (S.tool === "draw") { S.drawStroke = [[wx, wy]]; canvas.setPointerCapture(ev.pointerId); return; }
  if (S.tool === "orbit") {
    const snapped = nearestEntityAtWorld(wx, wy);
    S.orbitDraft = snapped
      ? { entityId: snapped.entityId, cx: snapped.cx, cy: snapped.cy, r: 1.6 }
      : { entityId: null, cx: wx, cy: wy, r: 1.6 };
    canvas.setPointerCapture(ev.pointerId);
    return;
  }

  const h = hit(...px);
  if (!h) { S.sel.cam = null; S.sel.actor = null; syncHistoryButtons(); return; }

  pushUndo();
  S.lastOrbitAnchor = null;
  if (h.kind === "cam_live" || h.kind === "cam_aim" || h.kind === "cam_wp") { setActivePov(null); }
  if (h.kind === "cam_live") {
    const idx = ensureCamWaypointAt(S.t);
    S.sel.cam = idx;
    S.drag = { kind: "cam_wp", index: idx };
  } else if (h.kind === "cam_aim") {
    const idx = ensureCamWaypointAt(S.t);
    S.sel.cam = idx;
    S.drag = { kind: "cam_aim", index: idx };
  } else if (h.kind === "cam_wp") {
    S.sel.cam = h.index;
    seek(S.waypoints[h.index].t);
    S.drag = h;
  } else if (h.kind === "actor_face") {
    const a = S.scene.actors.find((x) => x.id === h.id);
    if (S.actorDragMode === "all") {
      S.drag = { kind: "actor_face_all", id: a.id };
    } else {
      a.track = sortedKeys(a.track);
      let idx = a.track.findIndex((k) => Math.abs(k.t - S.t) < 0.3);
      if (idx < 0) {
        const p = actorAt(a, S.t);
        const nk = { t: roundN(S.t, 3), mmss: mmss(S.t), x: roundN(p.x, 3), y: roundN(p.y, 3), facing_deg: roundN(p.facing, 2), visible: true, _pinned: true };
        a.track.push(nk); a.track = sortedKeys(a.track);
        idx = a.track.findIndex((k) => k.t === nk.t);
      } else {
        a.track[idx]._pinned = true;
      }
      S.sel.actor = { id: a.id, index: idx };
      S.drag = { kind: "actor_face", id: a.id, index: idx };
    }
  } else if (h.kind === "actor_live") {
    const a = S.scene.actors.find((x) => x.id === h.id);
    if (S.actorDragMode === "all") {
      const p = actorAt(a, S.t);
      S.drag = { kind: "actor_all", id: a.id, startX: wx, startY: wy, origTrack: clone(a.track), base: p };
    } else {
      a.track = sortedKeys(a.track);
      let idx = a.track.findIndex((k) => Math.abs(k.t - S.t) < 0.3);
      if (idx < 0) {
        const p = actorAt(a, S.t);
        const nk = { t: roundN(S.t, 3), mmss: mmss(S.t), x: roundN(p.x, 3), y: roundN(p.y, 3), facing_deg: roundN(p.facing, 2), visible: true, _pinned: true };
        a.track.push(nk); a.track = sortedKeys(a.track);
        idx = a.track.findIndex((k) => k.t === nk.t);
      } else {
        a.track[idx]._pinned = true;
      }
      S.sel.actor = { id: a.id, index: idx };
      S.drag = { kind: "actor_key", id: a.id, index: idx };
    }
  } else if (h.kind === "actor_key") {
    S.sel.actor = { id: h.id, index: h.index };
    S.drag = h;
  } else if (h.kind === "obj") {
    S.drag = h;
  }
  canvas.setPointerCapture(ev.pointerId);
  canvas.style.cursor = "grabbing";
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
      if ($("hoverTip")) $("hoverTip").textContent = hoverHelpText(S.hover);
    }
    return;
  }
  const d = S.drag, sc = S.scene, r = (v) => Math.round(v * 100) / 100;
  if (d.kind === "cam_wp") {
    const k = S.waypoints[d.index];
    k.auto = false;
    k.x = r(wx); k.y = r(wy);
    if ($("autoAim")?.checked) {
      const tgt = entityPosAt(sc, lockedTargetId(), k.t);
      if (tgt) k.yaw_deg = r(yawTowards(k.x, k.y, tgt.x, tgt.y));
    }
    clearFutureAutoWaypoints(d.index);
    bakeCameraCurve();
  } else if (d.kind === "cam_aim") {
    const k = S.waypoints[d.index];
    k.auto = false;
    k.customYaw = true;
    k.yaw_deg = r(yawTowards(k.x, k.y, wx, wy));
    clearFutureAutoWaypoints(d.index);
    bakeCameraCurve();
  } else if (d.kind === "actor_key") {
    const a = sc.actors.find((x) => x.id === d.id);
    a.track = sortedKeys(a.track);
    const k = a.track[d.index];
    k._pinned = true;
    const dx = r(wx) - k.x, dy = r(wy) - k.y;
    k.x = r(wx); k.y = r(wy);
    // Carry unpinned future keys forward so the actor stays where you move them instead of snapping back!
    for (let j = d.index + 1; j < a.track.length; j++) {
      if (a.track[j]._pinned) break;
      a.track[j].x = r(a.track[j].x + dx);
      a.track[j].y = r(a.track[j].y + dy);
    }
    if ($("autoAim")?.checked) bakeCameraCurve();
  } else if (d.kind === "actor_face") {
    const a = sc.actors.find((x) => x.id === d.id);
    a.track = sortedKeys(a.track);
    const k = a.track[d.index];
    k._pinned = true;
    const newFace = r(yawTowards(k.x, k.y, wx, wy));
    k.facing_deg = newFace;
    for (let j = d.index + 1; j < a.track.length; j++) {
      if (a.track[j]._pinned) break;
      a.track[j].facing_deg = newFace;
    }
  } else if (d.kind === "actor_face_all") {
    const a = sc.actors.find((x) => x.id === d.id);
    const p = actorAt(a, S.t);
    const newFace = r(yawTowards(p.x, p.y, wx, wy));
    for (const k of a.track) k.facing_deg = newFace;
  } else if (d.kind === "actor_all") {
    const a = sc.actors.find((x) => x.id === d.id);
    const dx = wx - d.startX, dy = wy - d.startY;
    a.track = d.origTrack.map((k) => ({ ...k, x: r(k.x + dx), y: r(k.y + dy) }));
    if ($("autoAim")?.checked) bakeCameraCurve();
  } else if (d.kind === "obj") {
    sc.objects.find((o) => o.id === d.id).center = [r(wx), r(wy)];
  }
  updateKeyTelemetry();
  markDirty();
});

canvas.addEventListener("pointerup", () => {
  if (S.drawStroke) { const stroke = S.drawStroke; S.drawStroke = null; commitDrawnStroke(stroke); return; }
  if (S.orbitDraft) {
    const draft = S.orbitDraft; S.orbitDraft = null; pushUndo();
    if (draft.entityId && $("aimTarget")) $("aimTarget").value = draft.entityId;
    buildOrbitTrack(draft.entityId ? { entityId: draft.entityId } : { x: draft.cx, y: draft.cy }, S.orbitSweep, S.orbitDir, draft.r);
    return;
  }
  if (S.drag) { S.drag = null; syncHistoryButtons(); }
  if (S.tool === "select") canvas.style.cursor = S.hover ? "grab" : "default";
});

canvas.addEventListener("pointerleave", () => {
  if (!S.drag && $("hoverTip")) $("hoverTip").textContent = "";
});

canvas.addEventListener("dblclick", (ev) => {
  if (!S.scene || !S.view || S.tool !== "select") return;
  const [wx, wy] = S.view.toWorld(...evPx(ev));
  addKeyframeAt(wx, wy, S.t);
});

function markDirty(note) {
  msg(note || "Edited on motion stage (not saved). Press Re-render Map & Reel or Re-shoot (Omni).");
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
    S.scene = (await post(`/api/run/${S.runId}/apply`, { scene: S.scene, edit })).scene;
    S.waypoints = extractWaypointsFromTrack(S.scene.camera.track, 9);
    S.sel.cam = null; S.lastOrbitAnchor = null; syncHistoryButtons(); markDirty(); seek(edit.t0); startMapPreview();
  } catch (e) { S.undo.pop(); syncHistoryButtons(); msg(e.message, true); }
};

async function saveAndRender(promise, label) {
  msg(`${label}…`);
  const t0 = performance.now();
  try {
    const res = await promise;
    S.scene = res.scene;
    S.waypoints = extractWaypointsFromTrack(S.scene.camera.track, 9);
    S.playing = false;
    syncHistoryButtons();
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
  S.scene = clone(S.orig);
  S.waypoints = extractWaypointsFromTrack(S.scene.camera.track, 9, true);
  S.sel.cam = null; S.sel.actor = null; S.lastOrbitAnchor = null; S.playing = false;
  setActivePov(null);
  if ($("modeBanner")) $("modeBanner").textContent = "";
  syncFovUI(); setVideo(S.run.reel_url || S.run.clip_url); syncHistoryButtons();
  msg("Reset to the original verified camera angle & FOV.");
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
