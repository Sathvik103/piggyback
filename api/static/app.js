// Piggyback dashboard: a thin client over the API in the same origin.
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const inr = (x) => "₹" + Math.round(x).toLocaleString("en-IN");
const hrs = (x) => (x == null ? "—" : `${Number(x).toFixed(1)} h`);

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

const KIND_INFO = {
  MISSED_CONNECTION: "Late truck arrived after the next leg left",
  LOST_SCAN: "Expected unload never scanned",
  MISROUTE: "Scanned on a leg outside its booking",
  HELD: "On hold at a hub",
  MISSORT: "Part of a pile-up at one hub",
  STALLED: "Next load overdue",
  AT_RISK: "On a late truck; will miss its connection",
};
const PRIORITY_RANK = { EXPRESS: 0, STANDARD: 1, ECONOMY: 2 };
const words = (code) => String(code ?? "").replace(/_/g, " ").toLowerCase();

const state = {
  sid: null, now: null, hubs: {}, dets: [], plan: null, planBy: {},
  disruptions: [], kindFilter: null, markers: {}, selectedHub: null,
};

// ------------------------------------------------------------------ tooltip

const tip = $("#tip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(x + 12, innerWidth - w - 8) + "px";
  tip.style.top = Math.min(y + 12, innerHeight - h - 8) + "px";
}
const hideTip = () => { tip.hidden = true; };

// ------------------------------------------------------------------ map

const EMPTY = { type: "FeatureCollection", features: [] };
const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster", tileSize: 256,
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": css("--surface") } },
      { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -1, "raster-opacity": 0.4 } },
    ],
  },
  center: [80.5, 21.5], zoom: 3.9, attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

// "style.load", not "load": "load" waits for every basemap tile, and the data
// layers shouldn't depend on a third-party tile server being quick (or up).
const mapReady = new Promise((resolve) => map.once("style.load", () => {
  for (const id of ["lanes", "delays", "routes", "dedicated", "sel-booked", "sel-route"]) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }
  map.addLayer({ id: "lanes", type: "line", source: "lanes", paint: { "line-width": 1 } });
  map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round" },
    paint: { "line-width": ["interpolate", ["linear"], ["get", "n"], 1, 1.5, 15, 7], "line-opacity": 0.85 } });
  map.addLayer({ id: "dedicated", type: "line", source: "dedicated",
    paint: { "line-width": 2, "line-dasharray": [3, 2] } });
  // Disruptions sit above routes so a route through a delayed lane can't hide it.
  map.addLayer({ id: "delays", type: "line", source: "delays",
    paint: { "line-width": 3, "line-dasharray": [2, 1.5] } });
  map.addLayer({ id: "sel-booked", type: "line", source: "sel-booked",
    paint: { "line-width": 2, "line-dasharray": [1, 1.5] } });
  map.addLayer({ id: "sel-route", type: "line", source: "sel-route", layout: { "line-cap": "round" },
    paint: { "line-width": 4 } });
  applyMapColors();

  map.on("mousemove", "routes", (e) => {
    const p = e.features[0].properties;
    showTip(`<b>${esc(p.a)} → ${esc(p.b)}</b><br>${p.n} recovered parcel(s) ride this hop`,
      e.originalEvent.clientX, e.originalEvent.clientY);
  });
  map.on("mousemove", "delays", (e) => {
    const p = e.features[0].properties;
    showTip(`⚠ <b>Lane delay ${esc(p.lane)}</b><br>+${hrs(p.delay)} from ${hrs(p.start)} to ${hrs(p.end)}`,
      e.originalEvent.clientX, e.originalEvent.clientY);
  });
  for (const l of ["routes", "delays"]) {
    map.on("mouseenter", l, () => { map.getCanvas().style.cursor = "default"; });
    map.on("mouseleave", l, hideTip);
  }
  resolve();
}));

function applyMapColors() {
  map.setPaintProperty("bg", "background-color", css("--surface"));
  map.setPaintProperty("lanes", "line-color", css("--lane"));
  map.setPaintProperty("delays", "line-color", css("--serious"));
  map.setPaintProperty("dedicated", "line-color", css("--s2"));
  map.setPaintProperty("routes", "line-color", css("--s1"));
  map.setPaintProperty("sel-booked", "line-color", css("--muted"));
  map.setPaintProperty("sel-route", "line-color", css("--ink"));
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => mapReady.then(applyMapColors));

const line = (hubIds, props = {}) => ({
  type: "Feature", properties: props,
  geometry: { type: "LineString", coordinates: hubIds.map((h) => state.hubs[h].coord) },
});
const fc = (features) => ({ type: "FeatureCollection", features });

// ------------------------------------------------------------------ hub markers

function closures(hubId) {
  return state.disruptions.filter((d) => d.kind === "HUB_CLOSURE" && d.hub_id === hubId);
}

function drawMarkers() {
  for (const m of Object.values(state.markers)) m.remove();
  state.markers = {};
  for (const [id, h] of Object.entries(state.hubs)) {
    const el = document.createElement("div");
    el.className = "hub-marker";
    el.setAttribute("role", "button");
    el.innerHTML = `<span class="hub-label">${esc(id)}</span>`;
    el.addEventListener("mouseenter", (e) => {
      const n = flaggedAt(id), c = closures(id);
      showTip(`<b>${esc(h.name)}</b> (${esc(id)})<br>${n} parcel(s) flagged here`
        + c.map((d) => `<br><span class="status-critical">⛔ Closed ${hrs(d.start_h)}–${hrs(d.end_h)}</span>`).join(""),
        e.clientX, e.clientY);
    });
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("click", () => { hideTip(); showHub(id); });
    state.markers[id] = new maplibregl.Marker({ element: el }).setLngLat(h.coord).addTo(map);
    el.setAttribute("aria-label", `${h.name} hub`);  // after Marker, which sets its own generic label
  }
  sizeMarkers();
}

const flaggedAt = (hubId) => state.dets.filter((d) => d.suspected_hub === hubId).length;

function sizeMarkers() {
  for (const [id, m] of Object.entries(state.markers)) {
    const size = 8 + 4 * Math.sqrt(flaggedAt(id));
    const el = m.getElement();
    el.style.width = el.style.height = `${size}px`;
    el.classList.toggle("closed", closures(id).length > 0);
    el.classList.toggle("selected", id === state.selectedHub);
  }
}

// ------------------------------------------------------------------ tabs

function showTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === name));
  }
  for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== `tab-${name}`;
}
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));

// ------------------------------------------------------------------ KPIs

function kpi(value, label, cls = "") {
  return `<div class="kpi"><div class="v ${cls}">${esc(value)}</div><div class="l">${esc(label)}</div></div>`;
}

function renderKpis() {
  const stranded = state.dets.filter((d) => d.kind !== "AT_RISK");
  const hubs = new Set(stranded.map((d) => d.suspected_hub));
  let html = kpi(stranded.length, "parcels flagged stranded")
    + kpi(state.dets.length - stranded.length, "at risk (late truck)")
    + kpi(hubs.size, "hubs with stranded parcels");
  if (state.plan) {
    const m = state.plan.by_mode;
    html += kpi(m.PIGGYBACK || 0, "piggyback on spare capacity")
      + kpi(m.DEDICATED || 0, "dedicated trucks")
      + kpi(m.NONE || 0, "left as they are")
      + kpi(inr(state.plan.cost_inr), `plan cost (${state.plan.solver})`);
  } else {
    html += kpi("—", "no plan yet: press Run plan");
  }
  $("#kpis").innerHTML = html;
}

// ------------------------------------------------------------------ detections

function renderKinds() {
  const counts = {};
  for (const d of state.dets) counts[d.kind] = (counts[d.kind] || 0) + 1;
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map((r) => r[1]));
  $("#kind-bars").innerHTML = rows.map(([k, n]) => `
    <button class="bar-row ${state.kindFilter === k ? "on" : ""}" data-kind="${esc(k)}"
            aria-pressed="${state.kindFilter === k}">
      <span class="k">${esc(words(k))}</span>
      <span class="track"><span class="fill" style="width:${(100 * n) / max}%; display:block"></span></span>
      <span class="n">${n}</span>
    </button>`).join("");
  for (const b of document.querySelectorAll(".bar-row")) {
    b.addEventListener("click", () => {
      state.kindFilter = state.kindFilter === b.dataset.kind ? null : b.dataset.kind;
      renderKinds();
      renderTable();
    });
    b.addEventListener("mousemove", (e) => showTip(
      `<b>${esc(b.dataset.kind)}</b>: ${b.querySelector(".n").textContent} parcel(s)<br>${esc(KIND_INFO[b.dataset.kind] || "")}<br><span class="muted">Click to filter the table</span>`,
      e.clientX, e.clientY));
    b.addEventListener("mouseleave", hideTip);
  }
  $("#kind-filter-note").textContent = state.kindFilter ? `· showing ${state.kindFilter}` : "· click a bar to filter";
}

function renderTable() {
  const rows = state.dets
    .filter((d) => !state.kindFilter || d.kind === state.kindFilter)
    .sort((a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || b.overdue_h - a.overdue_h);
  const shown = rows.slice(0, 300);
  $("#det-table tbody").innerHTML = shown.map((d) => `
    <tr data-sid="${esc(d.shipment_id)}">
      <td>${esc(d.shipment_id)}</td><td>${esc(words(d.kind))}</td><td>${esc(d.suspected_hub)}</td>
      <td>${esc(words(d.priority))}</td><td class="num">${d.overdue_h.toFixed(1)}</td>
      <td>${esc(words(state.planBy[d.shipment_id]?.mode) || "—")}</td>
    </tr>`).join("");
  for (const tr of document.querySelectorAll("#det-table tbody tr")) {
    tr.addEventListener("click", () => showShipment(tr.dataset.sid));
  }
  $("#det-count").textContent = rows.length > shown.length
    ? `Showing ${shown.length} of ${rows.length}, most urgent first.` : `${rows.length} parcel(s), most urgent first.`;
}

async function loadDetections() {
  const g = Number($("#grace").value) || 0;
  const d = await api(`/scenarios/${state.sid}/detections?grace_h=${g}&limit=5000`);
  state.dets = d.items;
  renderKinds();
  renderTable();
  sizeMarkers();
  renderKpis();
  if ($("#truth").checked) loadTruth();
}

// ------------------------------------------------------------------ plans

async function loadPlans(selectId) {
  const plans = await api(`/scenarios/${state.sid}/plans`);
  $("#plan").innerHTML = plans.length
    ? plans.map((p) => `<option value="${esc(p.plan_id)}">${esc(p.plan_id)} · ${esc(p.solver)} · grace ${p.grace_h} h</option>`).join("")
    : `<option value="">none yet</option>`;
  const pick = selectId || plans.at(-1)?.plan_id;
  if (pick) {
    $("#plan").value = pick;
    await loadPlan(pick);
  } else {
    state.plan = null;
    state.planBy = {};
    await mapReady;
    map.getSource("routes").setData(EMPTY);
    map.getSource("dedicated").setData(EMPTY);
    renderKpis();
    renderTable();
  }
}

async function loadPlan(pid) {
  state.plan = await api(`/scenarios/${state.sid}/plans/${pid}`);
  state.planBy = Object.fromEntries(state.plan.choices.map((c) => [c.shipment_id, c]));
  const hops = {};
  const dedicated = [];
  for (const c of state.plan.choices) {
    if (c.mode === "PIGGYBACK") {
      for (let i = 0; i + 1 < c.route.length; i++) {
        const key = `${c.route[i]}>${c.route[i + 1]}`;
        hops[key] = (hops[key] || 0) + 1;
      }
    } else if (c.mode === "DEDICATED") {
      dedicated.push(line(c.route, { sid: c.shipment_id }));
    }
  }
  await mapReady;
  map.getSource("routes").setData(fc(Object.entries(hops).map(([k, n]) => {
    const [a, b] = k.split(">");
    return line([a, b], { a, b, n });
  })));
  map.getSource("dedicated").setData(fc(dedicated));
  renderKpis();
  renderTable();
  if ($("#truth").checked) loadTruth();
}

$("#plan").addEventListener("change", (e) => e.target.value && loadPlan(e.target.value));

$("#run").addEventListener("click", async () => {
  const btn = $("#run");
  btn.disabled = true;
  btn.textContent = "Planning…";
  try {
    const p = await api(`/scenarios/${state.sid}/plans`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ solver: $("#solver").value, grace_h: Number($("#grace").value) || 0 }),
    });
    await loadPlans(p.plan_id);
  } catch (err) {
    alert(`Planning failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run plan";
  }
});

// ------------------------------------------------------------------ truth (evaluation routes only)

async function loadTruth() {
  const box = $("#truth-kpis");
  const g = Number($("#grace").value) || 0;
  const det = await api(`/scenarios/${state.sid}/evaluation/detections?grace_h=${g}`);
  let html = kpi(`${Math.round(det.precision * 100)}%`, "flags that are real (precision)")
    + kpi(`${Math.round(det.recall * 100)}%`, "stranded parcels caught (recall)");
  if (state.plan) {
    const k = (await api(`/scenarios/${state.sid}/evaluation/plans/${state.plan.plan_id}`)).kpis;
    html += kpi(`${Math.round(k.on_time_rate * 100)}%`, `of ${k.stranded} stranded home on time`, "status-good")
      + kpi(k.late, "recovered but late")
      + kpi(`⛔ ${k.failed}`, "plans that failed in reality", "status-critical")
      + kpi(k.unrecovered, "not recovered")
      + kpi(inr(k.realized_penalty_inr), "realized penalty");
  }
  box.innerHTML = html;
  box.hidden = false;
}

$("#truth").addEventListener("change", (e) => {
  if (e.target.checked) loadTruth();
  else $("#truth-kpis").hidden = true;
});

// ------------------------------------------------------------------ detail panels

async function showHub(hubId) {
  state.selectedHub = hubId;
  sizeMarkers();
  const g = Number($("#grace").value) || 0;
  const d = await api(`/scenarios/${state.sid}/hubs/${hubId}?grace_h=${g}&window_h=24`);
  const c = closures(hubId);
  $("#tab-detail").innerHTML = `
    <button class="back" type="button" data-back>← Detections</button>
    <h2>${esc(d.hub.name)} (${esc(hubId)}), ${esc(d.hub.state)}</h2>
    <dl class="kv">
      <dt>Sort capacity</dt><dd>${d.hub.sort_capacity_per_h.toLocaleString("en-IN")} parcels/h</dd>
      <dt>Flagged here</dt><dd>${d.detections.length}</dd>
      ${c.map((x) => `<dt class="status-critical">⛔ Closed</dt><dd>${hrs(x.start_h)} – ${hrs(x.end_h)}</dd>`).join("")}
    </dl>
    <h2>Parcels flagged here</h2>
    <table><thead><tr><th>Shipment</th><th>Kind</th><th>Priority</th><th>Plan</th></tr></thead><tbody>
    ${d.detections.map((x) => `<tr data-sid="${esc(x.shipment_id)}"><td>${esc(x.shipment_id)}</td><td>${esc(words(x.kind))}</td>
      <td>${esc(words(x.priority))}</td><td>${esc(words(state.planBy[x.shipment_id]?.mode) || "—")}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">None</td></tr>`}
    </tbody></table>
    <h2>Departures in the next 24 h</h2>
    <table><thead><tr><th>Leg</th><th>To</th><th class="num">Departs</th><th class="num">Spare kg</th></tr></thead><tbody>
    ${d.departures.sort((a, b) => a.dep - b.dep).map((x) => `<tr><td>${esc(x.leg_id)}</td><td>${esc(x.to_hub)}</td>
      <td class="num">${hrs(x.dep)}</td><td class="num">${Math.round(x.spare_kg).toLocaleString("en-IN")}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">None</td></tr>`}
    </tbody></table>`;
  wireDetail();
  showTab("detail");
  await mapReady;
  map.getSource("sel-booked").setData(EMPTY);
  map.getSource("sel-route").setData(EMPTY);
}

async function showShipment(sid) {
  const d = await api(`/scenarios/${state.sid}/shipments/${sid}?grace_h=${Number($("#grace").value) || 0}`);
  const s = d.shipment, det = d.detection;
  const choice = state.planBy[sid];
  state.selectedHub = det?.suspected_hub ?? null;
  sizeMarkers();
  $("#tab-detail").innerHTML = `
    <button class="back" type="button" data-back>← Detections</button>
    <h2>${esc(sid)} · ${esc(s.priority)} · ${esc(s.origin_hub)} → ${esc(s.dest_hub)}</h2>
    <dl class="kv">
      <dt>Weight</dt><dd>${s.weight_kg} kg, ${s.volume_m3} m³</dd>
      <dt>Created</dt><dd>${hrs(s.created_h)}</dd>
      <dt>Deadline</dt><dd>${hrs(s.deadline_h)} (booked to arrive ${hrs(s.planned_arrival_h)})</dd>
    </dl>
    ${det ? `<h2>Detection</h2><dl class="kv">
      <dt>Kind</dt><dd><span class="pill">${esc(det.kind)}</span> ${esc(KIND_INFO[det.kind] || "")}</dd>
      <dt>Probably at</dt><dd>${esc(det.suspected_hub)}</dd>
      <dt>Overdue</dt><dd>${hrs(det.overdue_h)}</dd>
      <dt>Confidence</dt><dd>${Math.round(det.confidence * 100)}%</dd>
      <dt>Evidence</dt><dd>${esc(det.evidence)}</dd></dl>` : `<p class="muted">Not flagged.</p>`}
    ${choice ? `<h2>Plan ${esc(state.plan.plan_id)} (${esc(state.plan.solver)})</h2><dl class="kv">
      <dt>Action</dt><dd><span class="pill">${esc(choice.mode)}</span> ${esc(choice.note)}</dd>
      <dt>Route</dt><dd>${esc(choice.route.join(" → "))}</dd>
      <dt>Delivered</dt><dd>${hrs(choice.delivered_h)}${choice.lateness_h > 0
        ? ` <span class="status-serious">⚠ ${hrs(choice.lateness_h)} late</span>` : ` <span class="status-good">✓ on time</span>`}</dd>
      <dt>Cost</dt><dd>${inr(choice.cost_inr)}</dd></dl>` : ""}
    <h2>Visible scans</h2>
    <ol class="timeline">${d.scans.map((e) => `<li>${hrs(e.ts_h)} · ${esc(e.event_type)} at ${esc(e.hub_id)}${
      e.leg_id ? ` (${esc(e.leg_id)})` : ""}${e.note ? ` · ${esc(e.note)}` : ""}${
      e.recorded_h - e.ts_h > 0.5 ? ` <span class="muted">uploaded ${hrs(e.recorded_h - e.ts_h)} late</span>` : ""}</li>`).join("")}</ol>
    <h2>Booked legs</h2>
    <ol class="timeline">${d.booked_legs.map((l) => `<li>${esc(l.leg_id)} ${esc(l.from_hub)} → ${esc(l.to_hub)},
      ${hrs(l.departure_h)} – ${hrs(l.arrival_h)}</li>`).join("")}</ol>`;
  wireDetail();
  showTab("detail");
  await mapReady;
  const booked = [d.booked_legs[0].from_hub, ...d.booked_legs.map((l) => l.to_hub)];
  map.getSource("sel-booked").setData(fc([line(booked)]));
  map.getSource("sel-route").setData(choice && choice.route.length > 1 ? fc([line(choice.route)]) : EMPTY);
}

function wireDetail() {
  $("#tab-detail [data-back]").addEventListener("click", () => showTab("detections"));
  for (const tr of document.querySelectorAll("#tab-detail tbody tr[data-sid]")) {
    tr.addEventListener("click", () => showShipment(tr.dataset.sid));
  }
}

function renderDisruptions() {
  const icon = { HUB_CLOSURE: "⛔", LANE_DELAY: "⚠", TRIP_BREAKDOWN: "⛔" };
  const cls = { HUB_CLOSURE: "status-critical", LANE_DELAY: "status-serious", TRIP_BREAKDOWN: "status-critical" };
  $("#tab-disruptions").innerHTML = `<h2>Known disruptions (planning respects these)</h2>
    <table><thead><tr><th>Kind</th><th>Where</th><th class="num">From</th><th class="num">To</th><th>Effect</th></tr></thead><tbody>
    ${state.disruptions.map((d) => `<tr ${d.hub_id ? `data-hub="${esc(d.hub_id)}"` : ""}>
      <td class="${cls[d.kind]}">${icon[d.kind]} ${esc(words(d.kind))}</td>
      <td>${esc(d.hub_id || d.lane_id || d.trip_id)}</td><td class="num">${hrs(d.start_h)}</td><td class="num">${hrs(d.end_h)}</td>
      <td>${d.delay_h ? `+${hrs(d.delay_h)}` : d.kind === "HUB_CLOSURE" ? "no loading/unloading" : "trip stops running"}</td></tr>`).join("")}
    </tbody></table>`;
  for (const tr of document.querySelectorAll("#tab-disruptions tr[data-hub]")) {
    tr.addEventListener("click", () => showHub(tr.dataset.hub));
  }
}

// ------------------------------------------------------------------ scenario

async function loadScenario(sid) {
  state.sid = sid;
  state.kindFilter = null;
  state.selectedHub = null;
  const [sum, net, dis] = await Promise.all([
    api(`/scenarios/${sid}`), api(`/scenarios/${sid}/network`), api(`/scenarios/${sid}/disruptions`)]);
  state.now = sum.now_h;
  $("#now").textContent = `now = hour ${sum.now_h.toFixed(1)} · ${sum.shipments.toLocaleString("en-IN")} shipments`;
  state.hubs = {};
  const lanes = [];
  for (const f of net.features) {
    if (f.properties.kind === "hub") state.hubs[f.id] = { ...f.properties, coord: f.geometry.coordinates };
    else lanes.push(f);
  }
  state.disruptions = dis;
  await mapReady;
  map.getSource("lanes").setData(fc(lanes));
  map.getSource("delays").setData(fc(dis.filter((d) => d.kind === "LANE_DELAY").map((d) => {
    const [a, b] = d.lane_id.split("-");
    return line([a, b], { lane: d.lane_id, delay: d.delay_h, start: d.start_h, end: d.end_h });
  })));
  map.getSource("sel-booked").setData(EMPTY);
  map.getSource("sel-route").setData(EMPTY);
  drawMarkers();
  renderDisruptions();
  await loadDetections();
  await loadPlans();
  showTab("detections");
}

let graceTimer;
$("#grace").addEventListener("input", () => {
  clearTimeout(graceTimer);
  graceTimer = setTimeout(loadDetections, 300);
});
$("#scenario").addEventListener("change", (e) => loadScenario(e.target.value));

(async function init() {
  try {
    const list = await api("/scenarios");
    if (!list.length) {
      $("#kpis").innerHTML = kpi("—", "No scenarios yet: POST /scenarios or run the simulator CLI");
      return;
    }
    $("#scenario").innerHTML = list.map((s) => `<option value="${esc(s.scenario_id)}">${esc(s.scenario_id)}</option>`).join("");
    await loadScenario(list[0].scenario_id);
  } catch (err) {
    $("#kpis").innerHTML = kpi("!", `Could not reach the API: ${err.message}`);
  }
})();
