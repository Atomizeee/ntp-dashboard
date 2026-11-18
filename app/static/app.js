"use strict";
const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isNum = (v) => typeof v === "number" && isFinite(v);

/* ---------- formatting ---------- */
const UNITS = [["s", 1], ["ms", 1e-3], ["µs", 1e-6], ["ns", 1e-9]];
function fmtTime(s, signed = false) {
  if (!isNum(s)) return "—";
  const a = Math.abs(s);
  const [name, f] = a >= 1 ? UNITS[0] : a >= 1e-3 ? UNITS[1] : a >= 1e-6 ? UNITS[2] : UNITS[3];
  const v = s / f, av = Math.abs(v);
  const txt = av >= 100 ? v.toFixed(0) : av >= 10 ? v.toFixed(1) : v.toFixed(2);
  return (signed && v > 0 ? "+" : "") + txt + " " + name;
}
const fmtNum = (v, d = 2, u = "") => (isNum(v) ? v.toFixed(d) + u : "—");
function fmtAge(s) {
  if (!isNum(s)) return "—";
  if (s < 90) return Math.round(s) + " s";
  if (s < 5400) return Math.round(s / 60) + " min";
  return (s / 3600).toFixed(1) + " h";
}
const p2 = (n) => String(n).padStart(2, "0");
function fmtClock(ms, utc) {
  const d = new Date(Math.floor(ms));
  const g = utc ? ["getUTCFullYear", "getUTCMonth", "getUTCDate", "getUTCHours", "getUTCMinutes", "getUTCSeconds", "getUTCMilliseconds"]
                : ["getFullYear", "getMonth", "getDate", "getHours", "getMinutes", "getSeconds", "getMilliseconds"];
  const [y, mo, da, h, mi, s, m] = g.map((k) => d[k]());
  return `${h > -1 ? p2(h) : ""}:${p2(mi)}:${p2(s)}.${String(m).padStart(3, "0")}`;
}
const fmtStamp = (ts) => { const d = new Date(ts * 1000); return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`; };
const kpi = (label, value, sub) => `<dt>${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</dt><dd>${esc(value)}</dd>`;

/* ---------- theme ---------- */
(function theme() {
  const modes = ["auto", "light", "dark"];
  let m = "auto";
  try { m = localStorage.getItem("theme") || "auto"; } catch (e) {}
  const apply = () => { document.documentElement.dataset.theme = m; $("theme-btn").textContent = "Theme: " + m; };
  $("theme-btn").onclick = () => { m = modes[(modes.indexOf(m) + 1) % 3]; try { localStorage.setItem("theme", m); } catch (e) {} apply(); setTimeout(redrawAll, 50); };
  apply();
})();

/* ---------- charts (canvas, no libraries) ---------- */
function niceTicks(lo, hi, n = 4) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const step0 = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(v);
  return out;
}
function drawChart(canvas, cfg, hoverX) {
  const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  canvas._cfg = cfg;
  const compact = cfg.compact, L = compact ? 4 : 52, R = 6, T = 6, B = compact ? 4 : 18;
  const all = cfg.series.flatMap((s) => s.data.flatMap((p) => (cfg.band && s.band ? [p[2], p[3]] : [p[1]]))).filter(isNum);
  if (!all.length) { g.fillStyle = css("--muted"); g.font = "12px " + css("--sans"); g.fillText(cfg.empty || "No data yet", L + 6, H / 2); canvas._lay = null; return; }
  let lo = Math.min(...all), hi = Math.max(...all);
  if (cfg.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.1; lo -= pad; hi += pad;
  const ts = cfg.series.flatMap((s) => s.data.map((p) => p[0]));
  const t0 = cfg.t0 ?? Math.min(...ts), t1 = cfg.t1 ?? Math.max(...ts, t0 + 1);
  const X = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R), Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let scale = 1, unit = cfg.unit || "";
  if (cfg.time) { const m = Math.max(Math.abs(lo), Math.abs(hi)); [unit, scale] = m >= 1 ? ["s", 1] : m >= 1e-3 ? ["ms", 1e3] : m >= 1e-6 ? ["µs", 1e6] : ["ns", 1e9]; }
  g.font = "10.5px " + css("--mono"); g.textBaseline = "middle";
  if (!compact) {
    g.strokeStyle = css("--grid"); g.fillStyle = css("--muted"); g.lineWidth = 1; g.textAlign = "right";
    for (const v of niceTicks(lo, hi)) { const y = Math.round(Y(v)) + .5; g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke(); const sv = v * scale; g.fillText((Math.abs(sv) >= 100 ? sv.toFixed(0) : Math.abs(sv) >= 10 ? sv.toFixed(1) : sv.toFixed(2)) + (v === niceTicks(lo, hi)[0] ? "" : ""), L - 5, y); }
    g.textAlign = "center"; g.textBaseline = "top";
    for (let i = 0; i <= 3; i++) { const t = t0 + ((t1 - t0) * i) / 3, d = new Date(t * 1000); g.fillText((t1 - t0 > 86400 ? `${d.getMonth() + 1}/${d.getDate()} ` : "") + `${p2(d.getHours())}:${p2(d.getMinutes())}`, Math.min(W - 18, Math.max(L + 12, X(t))), H - B + 4); }
    if (unit) { g.textAlign = "left"; g.textBaseline = "top"; g.fillText(unit, 2, H - B + 4); }
  }
  if (cfg.zero && lo < 0 && hi > 0) { g.strokeStyle = css("--muted"); g.setLineDash([3, 3]); g.beginPath(); g.moveTo(L, Y(0)); g.lineTo(W - R, Y(0)); g.stroke(); g.setLineDash([]); }
  cfg.series.forEach((s) => {
    const col = css(s.color);
    const pts = s.data.filter((p) => isNum(p[1]));
    if (cfg.band && s.band) { g.fillStyle = col; g.globalAlpha = 0.14; g.beginPath(); pts.forEach((p, i) => g[i ? "lineTo" : "moveTo"](X(p[0]), Y(p[3]))); [...pts].reverse().forEach((p) => g.lineTo(X(p[0]), Y(p[2]))); g.fill(); g.globalAlpha = 1; }
    g.strokeStyle = col; g.lineWidth = 2; g.lineJoin = "round"; g.beginPath();
    pts.forEach((p, i) => g[i ? "lineTo" : "moveTo"](X(p[0]), Y(p[1]))); g.stroke();
    if (pts.length <= 3) { g.fillStyle = col; pts.forEach((p) => { g.beginPath(); g.arc(X(p[0]), Y(p[1]), 3, 0, 7); g.fill(); }); }
  });
  canvas._lay = { L, R, W, t0, t1, X, scale, unit };
  if (hoverX != null && !compact) {
    const t = t0 + ((hoverX - L) / (W - L - R)) * (t1 - t0);
    g.strokeStyle = css("--ink2"); g.lineWidth = 1; g.beginPath(); g.moveTo(hoverX + .5, T); g.lineTo(hoverX + .5, H - B); g.stroke();
    return t;
  }
}
function attachHover(canvas, fmtVal) {
  const tip = document.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; document.body.appendChild(tip);
  canvas.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, cfg = canvas._cfg;
    if (!cfg || !canvas._lay) return;
    const t = drawChart(canvas, cfg, x); if (t == null) return;
    const rows = cfg.series.map((s) => {
      let best = null; for (const p of s.data) if (isNum(p[1]) && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p;
      return best ? `<div><span class="dot" style="background:${css(s.color)}"></span>${esc(s.name)}: ${fmtVal(cfg, best[1])}</div>` : "";
    }).join("");
    const d = new Date(t * 1000);
    tip.innerHTML = `<div style="color:${css("--muted")}">${fmtStamp(t)}</div>${rows}`; tip.hidden = false;
    tip.style.left = Math.min(window.innerWidth - tip.offsetWidth - 8, e.clientX + 14) + "px"; tip.style.top = e.clientY + 14 + "px";
  });
  canvas.addEventListener("mouseleave", () => { tip.hidden = true; if (canvas._cfg) drawChart(canvas, canvas._cfg); });
}
const tipFmt = (cfg, v) => (cfg.time ? fmtTime(v, true) : fmtNum(v, 2, cfg.unit ? " " + cfg.unit : ""));

const CHARTS = [
  { id: "offset", title: "Offset to reference", time: true, zero: true, band: true, series: [["offset.last", "Last offset", "--s1"], ["pps.offset", "Reference clock offset", "--s2"]] },
  { id: "jitter", title: "Jitter", time: true, series: [["pps.stddev", "Reference clock std dev", "--s1"], ["offset.rms", "RMS offset", "--s2"], ["offset.jitter", "System jitter", "--s3"]] },
  { id: "bound", title: "Maximum error bound", time: true, series: [["max_error", "Max error", "--s1"]] },
  { id: "rtt", title: "Round-trip time to server", time: true, band: true, series: [["probe.rtt", "RTT", "--s1"], ["probe.jitter", "Jitter", "--s2"]] },
  { id: "hostoff", title: "Dashboard host clock vs server", time: true, zero: true, series: [["probe.offset", "Offset", "--s1"]] },
  { id: "upstream", title: "Reachable upstream servers", unit: "", series: [["sources.usable", "Reachable", "--s1"]] },
  { id: "sats", title: "Satellites", unit: "", series: [["gps.seen", "Seen", "--s2"], ["gps.used", "Used", "--s1"]] },
  { id: "hdop", title: "HDOP (lower is better)", series: [["gps.hdop", "HDOP", "--s1"]] },
  { id: "freq", title: "Clock frequency error", unit: "ppm", series: [["freq.ppm", "Frequency", "--s1"]] },
  { id: "load", title: "NTP requests per second", unit: "pkt/s", series: [["ntp.rate", "Received", "--s1"], ["ntp.drop_rate", "Dropped", "--s2"]] },
];
const BAND_METRICS = new Set(["offset.last", "probe.rtt"]);
const chartEls = {};
let rangeSec = 3600, lastHistory = null;
function buildCharts() {
  $("charts").innerHTML = CHARTS.map((c) => `<div class="chart"><h3>${esc(c.title)}</h3><div class="lg" id="lg-${c.id}"></div><canvas id="c-${c.id}"></canvas></div>`).join("");
  CHARTS.forEach((c) => { chartEls[c.id] = $("c-" + c.id); attachHover(chartEls[c.id], tipFmt); });
}
const hasData = (s) => lastHistory && (lastHistory[s[0]] || []).length > 0;
function renderCharts() {
  if (!lastHistory) return;
  const now = Date.now() / 1000, anyData = CHARTS.some((c) => c.series.some(hasData));
  CHARTS.forEach((c) => {
    const live = c.series.filter(hasData), wrap = chartEls[c.id].parentElement;
    wrap.hidden = anyData && !live.length; // charts that can never have data here (no GPS, no PPS, ...) stay out of the way
    if (wrap.hidden) return;
    const shown = live.length ? live : c.series;
    $("lg-" + c.id).innerHTML = shown.length > 1 || c.id === "refs" ? shown.map((s) => `<span><span class="dot" style="background:var(${s[2]})"></span>${esc(s[1])}</span>`).join("") : "";
    drawChart(chartEls[c.id], {
      ...c, t0: now - rangeSec, t1: now, empty: "Collecting data…",
      series: shown.map(([m, name, color]) => ({ name, color, band: BAND_METRICS.has(m), data: lastHistory[m] || [] })),
    });
  });
}
async function loadHistory() {
  try {
    const metrics = [...new Set(CHARTS.flatMap((c) => c.series.map((s) => s[0])))];
    const r = await fetch(`/api/history?range=${rangeSec}&metrics=${encodeURIComponent(metrics.join(","))}`, { cache: "no-store" });
    lastHistory = await r.json(); renderCharts();
  } catch (e) { /* retried on next tick */ }
}
$("range").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  rangeSec = +b.dataset.s; [...$("range").children].forEach((x) => x.setAttribute("aria-pressed", x === b));
  loadHistory();
});

/* ---------- browser vs NTP clock ---------- */
const sync = { samples: [], offset: null, unc: null, minRtt: null, source: "host" };
const browserHist = [];
async function syncOnce() {
  const w0 = Date.now() + 0.5, p0 = performance.now();
  try {
    const j = await (await fetch("/api/time?" + Math.random(), { cache: "no-store" })).json();
    const rtt = performance.now() - p0;
    sync.samples.push({ rtt, off: j.ntp_ms - (w0 + rtt / 2) });
    if (sync.samples.length > 30) sync.samples.shift();
    const best = [...sync.samples].sort((a, b) => a.rtt - b.rtt).slice(0, 5);
    sync.offset = best.reduce((s, x) => s + x.off, 0) / best.length;
    sync.minRtt = best[0].rtt; sync.unc = best[0].rtt / 2; sync.source = j.source;
    browserHist.push([Date.now() / 1000, -sync.offset]); if (browserHist.length > 90) browserHist.shift();
    drawChart($("ch-browser"), { compact: true, time: true, zero: true, series: [{ name: "browser", color: "--accent", data: browserHist.map((p) => [p[0], p[1] / 1000]) }] });
  } catch (e) { /* keep last estimate */ }
}
function tickClocks() {
  const now = Date.now();
  if (sync.offset != null) {
    $("clk-ntp").textContent = fmtClock(now + sync.offset, true) + " UTC";
    $("clk-browser").textContent = fmtClock(now, true) + " UTC";
    const d = -sync.offset; // browser minus NTP, ms
    $("clk-diff").textContent = (Math.abs(d) < 0.05 ? "0" : (d > 0 ? "+" : "−") + Math.abs(d).toFixed(Math.abs(d) < 10 ? 2 : 1)) + " ms";
    $("clk-meta").textContent = `Browser is ${Math.abs(d) < 0.5 ? "in step with" : d > 0 ? "ahead of" : "behind"} the NTP server, ±${sync.unc.toFixed(1)} ms (limited by the ${sync.minRtt.toFixed(1)} ms page round trip).` +
      (sync.source === "host" ? " The dashboard host could not reach the NTP server, so this compares against the host's own clock." : "");
  }
  requestAnimationFrame(tickClocks);
}

/* ---------- sky plot ---------- */
const GNSS_COLOR = { GPS: "--c-gps", GLONASS: "--c-glonass", Galileo: "--c-galileo", BeiDou: "--c-beidou", SBAS: "--c-sbas", QZSS: "--c-qzss" };
const gcol = (n) => `var(${GNSS_COLOR[n] || "--c-other"})`;
function drawSky(sats) {
  const cx = 150, cy = 150, R = 124; let s = "";
  [0, 30, 60].forEach((el) => { const r = (R * (90 - el)) / 90; s += `<circle class="ring" cx="${cx}" cy="${cy}" r="${r}"/>` + (el ? `<text class="tick" x="${cx + 3}" y="${cy - r + 9}">${el}°</text>` : ""); });
  s += `<line class="ring" x1="${cx - R}" y1="${cy}" x2="${cx + R}" y2="${cy}"/><line class="ring" x1="${cx}" y1="${cy - R}" x2="${cx}" y2="${cy + R}"/>`;
  [["N", 0, -R - 8], ["E", R + 9, 0], ["S", 0, R + 10], ["W", -R - 9, 0]].forEach(([t, dx, dy]) => (s += `<text class="tick" x="${cx + dx}" y="${cy + dy + 3}" text-anchor="middle" style="font-size:10px">${t}</text>`));
  sats.filter((x) => isNum(x.az) && isNum(x.el) && x.el >= 0).forEach((x) => {
    const r = (R * (90 - x.el)) / 90, a = (x.az * Math.PI) / 180, px = cx + r * Math.sin(a), py = cy - r * Math.cos(a), rad = 4 + Math.min(Math.max(x.snr || 0, 0), 50) / 12;
    s += `<g><title>${esc(x.id)} ${esc(x.gnss)} · az ${x.az.toFixed(0)}° el ${x.el.toFixed(0)}° · ${x.snr ?? "?"} dB-Hz · ${x.used ? "used" : "not used"}</title>` +
      `<circle cx="${px}" cy="${py}" r="${rad}" style="fill:${gcol(x.gnss)};fill-opacity:${x.used ? 1 : 0.12};stroke:${gcol(x.gnss)};stroke-width:1.5"/>` +
      `<text class="sat-label" x="${px}" y="${py + rad + 8}">${esc(x.id)}</text></g>`;
  });
  $("sky").innerHTML = s;
  const by = {}; sats.forEach((x) => { const e = (by[x.gnss] ||= { u: 0, n: 0 }); e.n++; if (x.used) e.u++; });
  $("sky-legend").innerHTML = Object.entries(by).map(([n, e]) => `<span><span class="dot" style="background:${gcol(n)}"></span>${esc(n)} ${e.u}/${e.n}</span>`).join("") + `<span style="color:var(--muted)">filled = used in fix</span>`;
}

/* ---------- map ---------- */
let map = null, mapLayer = null, mapFitted = false, haveLeaflet = typeof L !== "undefined";
const unwrap = (lon, ref) => ref + ((((lon - ref + 540) % 360) + 360) % 360) - 180;
function initMap() {
  if (!haveLeaflet) { $("map").innerHTML = '<canvas class="fallback-map" id="fb"></canvas>'; $("map-note").textContent = "Map library not loaded (no internet?). Showing a plain latitude/longitude plot."; return; }
  map = L.map("map", { worldCopyJump: true, minZoom: 1, zoomControl: true }).setView([20, 0], 2);
  L.tileLayer(CONFIG.tile_url, { attribution: CONFIG.attribution, maxZoom: 8 }).addTo(map);
  mapLayer = L.layerGroup().addTo(map);
}
function drawMap(gps) {
  const showLines = $("map-lines").checked;
  if (!gps || gps.position_hidden || !isNum(gps.lat)) { $("map-note").textContent = !gps ? "gpsd data not available." : gps.position_hidden ? "Position is hidden (HIDE_POSITION=true); see the sky view instead." : "Waiting for a GPS position fix."; if (mapLayer) mapLayer.clearLayers(); return; }
  $("map-note").textContent = "Satellite points are computed from azimuth, elevation and a typical orbit height for each constellation. They show roughly where each satellite is, not its exact position.";
  const pts = gps.satellites.filter((s) => isNum(s.sub_lat)).map((s) => ({ ...s, lon: unwrap(s.sub_lon, gps.lon) }));
  if (!haveLeaflet) return drawFallback(gps, pts);
  mapLayer.clearLayers();
  pts.forEach((s) => {
    const c = css(GNSS_COLOR[s.gnss] || "--c-other");
    if (showLines && s.used) L.polyline([[gps.lat, gps.lon], [s.sub_lat, s.lon]], { color: c, weight: 1, opacity: 0.35, dashArray: "3 4" }).addTo(mapLayer);
    L.circleMarker([s.sub_lat, s.lon], { radius: 6, color: c, weight: 2, fillColor: c, fillOpacity: s.used ? 0.9 : 0.1 })
      .bindTooltip(`${s.id} (${s.gnss}) · el ${s.el.toFixed(0)}° · ${s.snr ?? "?"} dB-Hz · ${s.used ? "used" : "not used"}`).addTo(mapLayer);
  });
  L.circleMarker([gps.lat, gps.lon], { radius: 7, color: "#fff", weight: 2, fillColor: css("--accent"), fillOpacity: 1 }).bindTooltip("Your receiver").addTo(mapLayer);
  if (!mapFitted) fitMap(gps, pts);
}
function fitMap(gps, pts) { if (!map) return; map.fitBounds(L.latLngBounds([[gps.lat, gps.lon], ...pts.map((p) => [p.sub_lat, p.lon])]).pad(0.15), { maxZoom: 5 }); mapFitted = true; }
function drawFallback(gps, pts) {
  const cv = $("fb"), W = cv.clientWidth, H = cv.clientHeight, dpr = devicePixelRatio || 1; cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const all = [[gps.lat, gps.lon], ...pts.map((p) => [p.sub_lat, p.lon])];
  const la = all.map((p) => p[0]), lo = all.map((p) => p[1]), x0 = Math.min(...lo) - 10, x1 = Math.max(...lo) + 10, y0 = Math.min(...la) - 10, y1 = Math.max(...la) + 10;
  const X = (v) => ((v - x0) / (x1 - x0)) * W, Y = (v) => H - ((v - y0) / (y1 - y0)) * H;
  g.strokeStyle = css("--grid"); g.fillStyle = css("--muted"); g.font = "10px " + css("--mono");
  for (let v = Math.ceil(x0 / 20) * 20; v < x1; v += 20) { g.beginPath(); g.moveTo(X(v), 0); g.lineTo(X(v), H); g.stroke(); g.fillText(v + "°", X(v) + 2, H - 3); }
  for (let v = Math.ceil(y0 / 20) * 20; v < y1; v += 20) { g.beginPath(); g.moveTo(0, Y(v)); g.lineTo(W, Y(v)); g.stroke(); g.fillText(v + "°", 3, Y(v) - 2); }
  pts.forEach((s) => { const c = css(GNSS_COLOR[s.gnss] || "--c-other"); g.strokeStyle = c; g.fillStyle = c; g.globalAlpha = s.used ? 1 : 0.35; g.beginPath(); g.arc(X(s.lon), Y(s.sub_lat), 5, 0, 7); s.used ? g.fill() : g.stroke(); g.globalAlpha = 1; g.fillStyle = css("--ink"); g.fillText(s.id, X(s.lon) + 7, Y(s.sub_lat) + 3); });
  g.fillStyle = css("--accent"); g.beginPath(); g.arc(X(gps.lon), Y(gps.lat), 7, 0, 7); g.fill();
}
$("map-fit").onclick = () => { mapFitted = false; if (lastStatus) drawMap(lastStatus.gps); };
$("map-lines").onchange = () => lastStatus && drawMap(lastStatus.gps);

/* ---------- panels ---------- */
const ICON = { ok: "✓", warn: "!", bad: "✕", unknown: "?" };
const FIX = { 0: "unknown", 1: "no fix", 2: "2D", 3: "3D" };
const STATE = { "*": "selected", "+": "combined", "-": "not combined", x: "falseticker", "~": "too variable", "?": "unusable" };
const MODE = { "#": "ref clock", "^": "server", "=": "peer" };
function renderBanner(st) {
  const b = st.banner, a = st.accuracy;
  $("banner").dataset.level = b.level; $("banner-mark").textContent = { ok: "✓", warn: "!", bad: "✕" }[b.level] || "…";
  $("banner-title").textContent = b.title;
  const parts = [];
  if (a) {
    parts.push(`stratum ${a.stratum}`, `reference ${a.ref_name || "—"}`);
    if (isNum(a.rms_offset_s)) parts.push(`RMS offset ${fmtTime(a.rms_offset_s)}`);
    else if (isNum(a.last_offset_s)) parts.push(`offset ${fmtTime(a.last_offset_s, true)}`);
    if (isNum(a.max_error_s)) parts.push(`max error ${fmtTime(a.max_error_s)}`);
  }
  if (st.gps && st.gps.connected) parts.push(`${st.gps.sats_used} of ${st.gps.sats_seen} satellites used`);
  if (b.attention && b.level !== "bad") parts.push(`${b.attention} check${b.attention > 1 ? "s" : ""} need attention`);
  $("banner-sub").textContent = parts.join("  ·  ") || (st.server.error || "");
  $("checks").innerHTML = st.checks.map((c) => `<li data-s="${c.state}"><span class="ic" aria-hidden="true">${ICON[c.state]}</span><span><b>${esc(c.label)}</b> <span class="d">${esc(c.detail)}</span></span></li>`).join("");
}
function renderKpis(st) {
  const a = st.accuracy, p = st.probe, c = st.capabilities, rows = [];
  const add = (v, label, text, sub) => { if (isNum(v)) rows.push(kpi(label, text, sub)); };
  if (a) {
    add(a.last_offset_s, "Last offset", fmtTime(a.last_offset_s, true), "vs. selected source");
    add(a.rms_offset_s, "RMS offset", fmtTime(a.rms_offset_s));
    add(a.jitter_s, "System jitter", fmtTime(a.jitter_s));
    add(a.system_error_s, "System time error", fmtTime(a.system_error_s));
    add(a.max_error_s, "Maximum error", fmtTime(a.max_error_s), "worst-case bound sent to clients");
    add(a.root_delay_s, "Root delay", fmtTime(a.root_delay_s), "to the top of the hierarchy");
    add(a.pps_stddev_s, "Reference clock jitter", fmtTime(a.pps_stddev_s), a.pps_name ? `std dev of ${a.pps_name}` : "");
    add(a.skew_ppm, "Frequency skew", fmtNum(a.skew_ppm, 3, " ppm"));
    add(a.freq_ppm, "Frequency error", fmtNum(a.freq_ppm, 3, " ppm"));
    add(a.update_interval_s, "Update interval", fmtNum(a.update_interval_s, 0, " s"));
  }
  const note = !c.details && a ? `<dt class="span-all">Basic view: ${c.server_type === "probe" ? "only the NTP port is read (SERVER_TYPE=probe)." : "chrony and ntpd could not be read on this server, so this shows what the NTP port announces. See the README to enable the full view."}</dt>` : "";
  $("kpis").innerHTML = rows.join("") + note || kpi("Waiting for the server", st.server.error || "");
  $("probe-host").textContent = `${p.host}:${p.port}`;
  const sv = p.server;
  $("lat-kpis").innerHTML = [
    kpi("Round trip (last)", fmtTime(p.rtt_s)), kpi("Round trip (min / avg)", `${fmtTime(p.rtt_min_s)} / ${fmtTime(p.rtt_avg_s)}`),
    kpi("Jitter", fmtTime(p.jitter_s)), kpi("Packet loss", fmtNum(p.loss_pct, 0, " %"), `last ${p.samples} probes`),
    kpi("Dashboard host vs server", fmtTime(p.offset_s, true), "this machine's clock, not the server's"),
    kpi("Server answers as", sv ? `stratum ${sv.stratum} · ${sv.ref_id}` : "—"),
    kpi("Page → dashboard", sync.minRtt != null ? fmtTime(sync.minRtt / 1000) : "—", "browser round trip"),
  ].join("");
}
const reachHtml = (s) => `<span class="reach" title="${s.reach.toString(8)} (octal)">${[7, 6, 5, 4, 3, 2, 1, 0].map((i) => `<i class="${(s.reach >> i) & 1 ? "on" : ""}"></i>`).join("")}</span>`;
const SRC_COLS = [
  { h: "State", t: 1, v: (s) => `<b>${esc(s.state)}</b> ${STATE[s.state] || ""}` },
  { h: "Source", t: 1, v: (s) => `${esc(s.name)} <span style="color:var(--muted)">${MODE[s.mode] || ""}</span>` },
  { h: "Stratum", v: (s) => s.stratum },
  { h: "Poll", v: (s) => (isNum(s.poll) ? 2 ** s.poll + " s" : "—") },
  { h: "Reach", v: reachHtml },
  { h: "Last rx", k: "last_rx_s", v: (s) => fmtAge(s.last_rx_s) },
  { h: "Offset", k: "offset_s", v: (s) => fmtTime(s.offset_s, true) },
  { h: "Delay", k: "delay_s", v: (s) => fmtTime(s.delay_s) },
  { h: "± error", k: "error_s", v: (s) => fmtTime(s.error_s) },
  { h: "Jitter", k: "std_dev_s", v: (s) => fmtTime(s.std_dev_s) },
  { h: "Samples", k: "np", v: (s) => s.np ?? "—" },
];
function renderSources(st) {
  const rows = st.server.sources, cols = SRC_COLS.filter((c) => !c.k || rows.some((r) => r[c.k] != null));
  $("sources-hint").textContent = rows.length ? `${rows.length} configured · ${st.server.usable_sources || 0} usable upstream` : "";
  $("sources").innerHTML = `<tr>${cols.map((c) => `<th>${c.h}</th>`).join("")}</tr>` +
    (rows.map((s) => `<tr class="${s.state === "*" ? "sel" : ""}">${cols.map((c) => `<td${c.t ? ' class="txt"' : ""}>${c.v(s)}</td>`).join("")}</tr>`).join("") || `<tr><td>No sources</td></tr>`);
}
function renderRefs(st) {
  const rows = st.references;
  $("refs-table").innerHTML = `<tr><th>Server</th><th>Stratum</th><th>Round trip</th><th title="Positive: your server's clock is ahead">Your server vs it</th></tr>` +
    rows.map((r) => `<tr><td class="txt">${esc(r.host)}</td><td>${r.stratum ?? "—"}</td><td>${r.ok ? fmtTime(r.rtt_s) : '<span style="color:var(--muted)">no answer</span>'}</td><td>${fmtTime(r.diff_s, true)}</td></tr>`).join("");
  $("refs-note").textContent = "Positive means your server's clock is ahead of that public server. Internet paths add a few milliseconds of noise, so look for large or lasting differences, not small ones.";
}
function renderGps(st) {
  const g = st.gps;
  if (!g) { $("gps-kpis").innerHTML = kpi("gpsd", "disabled"); $("sat-table").innerHTML = ""; drawSky([]); return; }
  const dev = g.devices[0] || {};
  $("gps-kpis").innerHTML = [
    kpi("gpsd", g.connected ? "connected" : g.error || "not connected", g.version ? "version " + g.version : ""),
    kpi("Fix", FIX[g.fix_mode] ?? "—"), kpi("GPS time (UTC)", g.time ? g.time.replace("T", " ").replace(/\.\d+Z?$/, "") : "—"),
    kpi("Position", isNum(g.lat) ? `${g.lat}, ${g.lon}` : g.position_hidden ? "hidden" : "—"), kpi("Altitude", isNum(g.alt_m) ? g.alt_m.toFixed(0) + " m" : "—"),
    kpi("HDOP / VDOP / PDOP", ["hdop", "vdop", "pdop"].map((k) => fmtNum(g.dops[k], 1)).join(" / ")),
    kpi("Receiver", dev.driver || "—", dev.subtype ? String(dev.subtype).split(",")[0] + (dev.bps ? ` · ${dev.bps} baud` : "") : ""),
    g.pps ? kpi("PPS (gpsd)", fmtTime(g.pps.offset_s, true), isNum(g.pps.q_err_ps) ? `sawtooth error ${fmtTime(g.pps.q_err_ps * 1e-12, true)}` : "") : "",
  ].join("");
  $("sat-count").textContent = `${g.sats_used} used of ${g.sats_seen}`;
  $("sat-table").innerHTML = `<tr><th>ID</th><th>System</th><th>El</th><th>Az</th><th>dB-Hz</th><th>Used</th></tr>` +
    g.satellites.map((s) => `<tr><td><span class="dot" style="background:${gcol(s.gnss)}"></span>${esc(s.id)}</td><td class="txt">${esc(s.gnss)}</td><td>${fmtNum(s.el, 0, "°")}</td><td>${fmtNum(s.az, 0, "°")}</td><td>${isNum(s.snr) ? s.snr.toFixed(0) : "—"}</td><td>${s.used ? "yes" : "no"}</td></tr>`).join("");
  drawSky(g.satellites);
}
function renderServer(st) {
  const s = st.server.serverstats;
  $("srv-kpis").innerHTML = s ? [
    kpi("Requests per second", fmtNum(s.ntp_rate, 1)), kpi("Total requests", (s.ntp_rx ?? 0).toLocaleString()),
    kpi("Dropped", (s.ntp_dropped ?? 0).toLocaleString(), "rate limited or invalid"), kpi("Control requests", (s.cmd_rx ?? 0).toLocaleString()),
    s.nts_auth != null ? kpi("NTS authenticated", s.nts_auth.toLocaleString()) : "",
  ].join("") : `<dt style="grid-column:1/-1;color:var(--muted);font-weight:400">Not available remotely. chrony only gives client statistics to programs running on the server itself, so this box stays empty when the dashboard runs on another machine. It does not mean nobody is using the server.</dt>`;
}
function renderEvents(st) {
  $("events").innerHTML = st.events.map((e) => `<li data-l="${e.level}"><time>${fmtStamp(e.ts)}</time><span class="ic">${ICON[e.level] || "·"}</span><span>${esc(e.message)}</span></li>`).join("") || "<li>No events yet</li>";
}

/* ---------- what to show for this kind of server ---------- */
const MODE_LABEL = { chrony: "chrony", ntpd: "ntpd", probe: "NTP port only" };
let mapStarted = false;
function applyCaps(st) {
  const c = st.capabilities, show = (id, on) => { $(id).hidden = !on; };
  ["map-card", "sky-card", "sat-card", "gps-card"].forEach((id) => show(id, c.gps));
  show("sources-card", c.sources); show("refs-card", c.references);
  show("srv-card", c.serverstats || c.mode === "chrony");
  $("sources-card").classList.toggle("full", !c.references); $("sources-card").classList.toggle("wide", c.references);
  $("refs-card").classList.toggle("wide", !c.sources);
  $("server-host").textContent = `${CONFIG.server_host} · ${MODE_LABEL[c.mode] || c.mode}`;
  if (c.gps && !mapStarted) { mapStarted = true; initMap(); setTimeout(() => map && map.invalidateSize(), 50); }
}

/* ---------- main loop ---------- */
let CONFIG = null, lastStatus = null, failures = 0;
function redrawAll() { renderCharts(); if (lastStatus && lastStatus.gps) { drawSky(lastStatus.gps.satellites); drawMap(lastStatus.gps); } }
async function refresh() {
  try {
    const st = await (await fetch("/api/status", { cache: "no-store" })).json();
    lastStatus = st; failures = 0;
    $("conn").dataset.state = "ok"; $("conn").textContent = "live";
    applyCaps(st); renderBanner(st); renderKpis(st); renderSources(st); renderRefs(st); renderServer(st); renderEvents(st);
    if (st.capabilities.gps) { renderGps(st); drawMap(st.gps); }
    $("ver").textContent = "ntp-dashboard " + st.version;
  } catch (e) {
    if (++failures >= 2) { $("conn").dataset.state = "bad"; $("conn").textContent = "dashboard unreachable"; }
  }
}
async function main() {
  CONFIG = await (await fetch("/api/config")).json();
  $("server-name").textContent = CONFIG.server_name; document.title = CONFIG.server_name + " · NTP monitor";
  $("server-host").textContent = CONFIG.server_host;
  if (CONFIG.references.length) CHARTS.splice(5, 0, { id: "refs", title: "Your server vs public servers", time: true, zero: true,
    series: CONFIG.references.map((h, i) => ["ref.diff." + h, h, "--s" + ((i % 4) + 1)]) });
  buildCharts();
  window.addEventListener("resize", () => { renderCharts(); if (lastStatus && lastStatus.capabilities.gps) drawMap(lastStatus.gps); });
  await Promise.all([refresh(), syncOnce(), loadHistory()]);
  setInterval(refresh, 2000); setInterval(syncOnce, 2000); setInterval(loadHistory, 30000);
  requestAnimationFrame(tickClocks);
}
main().catch((e) => { $("banner-title").textContent = "Could not load the dashboard"; $("banner-sub").textContent = String(e); });
