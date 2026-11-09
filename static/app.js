"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { workouts: [], selected: new Set(), activeId: null, detail: null, view: "library", metric: "fc", limit: 50, importing: false, detailSeq: 0, previewSeq: 0 };
const sportNames = { running: "Corsa", cycling: "Ciclismo", swimming: "Nuoto", walking: "Camminata", hiking: "Escursione", training: "Allenamento", transition: "Transizione", fitness_equipment: "Palestra", unknown: "Altro" };
const nf = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
const distanceFormat = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const numeric = (value) => typeof value === "number" && Number.isFinite(value);
const sportName = (sport) => sportNames[sport] || sport.replaceAll("_", " ");
const iconName = (sport) => ({ running: "run", cycling: "bike", swimming: "swim", walking: "run" })[sport] || "grid";
const icon = (name, cls = "") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const sportIcon = (sport) => `<span class="sport-icon ${escape(sport)}">${icon(iconName(sport))}</span>`;
const formatDate = (timestamp, long = false) => new Intl.DateTimeFormat("it-IT", { day: "numeric", month: long ? "long" : "short", ...(long ? { year: "numeric" } : {}) }).format(new Date(timestamp));
const formatTime = (timestamp) => new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp));
const distance = (meters) => numeric(meters) ? `${distanceFormat.format(meters / 1000)} km` : "—";
const duration = (seconds) => { if (!numeric(seconds)) return "—"; const mins = Math.round(seconds / 60); return mins >= 60 ? `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m` : `${mins} min`; };
const metricValue = (value, unit = "") => numeric(value) ? `${(unit === "km" ? distanceFormat : nf).format(value)}${unit ? `<span>${escape(unit)}</span>` : ""}` : "—";
const size = (bytes) => bytes >= 1024 * 1024 ? `${nf.format(bytes / 1024 / 1024)} MB` : `${nf.format(bytes / 1024)} KB`;
const warnings = (workout) => Array.isArray(workout?.quality?.warnings) ? workout.quality.warnings : [];
const hasWarning = (workout) => warnings(workout).some((warning) => warning.level !== "info");
const isDemo = (workout) => workout.quality?.synthetic === true;
let toastTimer, previewTimer;

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").classList.toggle("error", error);
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, error ? 7000 : 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "X-Fit-Studio": "1", ...options.headers } });
  if (!response.ok) {
    let message = `Richiesta non riuscita (${response.status}).`;
    try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch (_) { /* Keep the HTTP error. */ }
    throw new Error(message);
  }
  return response;
}

function filteredWorkouts() {
  const query = $("#search-input").value.trim().toLocaleLowerCase();
  const sport = $("#sport-filter").value;
  const from = $("#date-from").value, to = $("#date-to").value;
  return state.workouts.filter((workout) => {
    const localDate = new Date(workout.ts);
    const day = `${localDate.getFullYear()}-${String(localDate.getMonth() + 1).padStart(2, "0")}-${String(localDate.getDate()).padStart(2, "0")}`;
    const haystack = `${sportName(workout.sport)} ${formatDate(workout.ts, true)} ${workout.provenance?.file || ""}`.toLocaleLowerCase();
    return (!sport || workout.sport === sport) && (!query || haystack.includes(query)) && (!from || day >= from) && (!to || day <= to);
  });
}

function dataBadge(workout) {
  if (hasWarning(workout)) return `<span class="data-badge warning">${icon("alert")}Da verificare</span>`;
  if (warnings(workout).length || !workout.sample_count) return `<span class="data-badge info">Riepilogo</span>`;
  return `<span class="data-badge">${icon("check")}Disponibili</span>`;
}

function renderStats() {
  const list = filteredWorkouts();
  const knownDistance = list.filter((w) => numeric(w.session.dist_m));
  const knownDuration = list.filter((w) => numeric(w.session.dur_s));
  $("#stat-count").textContent = list.length ? integer.format(list.length) : "—";
  $("#stat-count-note").textContent = list.length ? `${list.filter(isDemo).length ? "Include dati di esempio · " : ""}${state.workouts.length} nell’archivio` : "Sessioni importate";
  $("#stat-distance").innerHTML = knownDistance.length ? `${nf.format(knownDistance.reduce((sum, w) => sum + w.session.dist_m, 0) / 1000)}<span class="stat-unit">km</span>` : "—";
  $("#stat-distance-note").textContent = knownDistance.length && knownDistance.length < list.length ? `${knownDistance.length} sessioni con distanza` : "Distanze registrate";
  $("#stat-time").textContent = knownDuration.length ? duration(knownDuration.reduce((sum, w) => sum + w.session.dur_s, 0)) : "—";
  $("#stat-time-note").textContent = knownDuration.length && knownDuration.length < list.length ? `${knownDuration.length} sessioni con durata` : "Durate attive dichiarate dalla fonte";
  const sports = [...new Set(list.map((w) => w.sport))];
  $("#stat-sports").textContent = sports.length || "—";
  $("#stat-sports-note").textContent = sports.length ? sports.map(sportName).join(" · ") : "Sport importati";
  $("#drop-zone").classList.toggle("compact", state.workouts.length > 0);
}

function renderLibrary() {
  renderStats();
  const list = filteredWorkouts();
  renderOverviewCharts(list);
  $("#nav-count").textContent = state.workouts.length;
  $("#list-count").textContent = list.length;
  $("#selection-count").textContent = state.selected.size;
  $("#export-selection").disabled = state.selected.size === 0;
  $("#empty-library").hidden = state.workouts.length > 0;
  $("#empty-filter").hidden = !state.workouts.length || list.length > 0;
  $("#workout-table-wrap").hidden = list.length === 0;
  $("#list-subtitle").textContent = state.workouts.length ? "Seleziona le sessioni da esportare." : "";
  $("#workout-rows").innerHTML = list.slice(0, state.limit).map((workout) => `<tr class="${workout.id === state.activeId ? "active" : ""}">
    <td class="check-cell"><input type="checkbox" data-select="${escape(workout.id)}" ${state.selected.has(workout.id) ? "checked" : ""} aria-label="Seleziona ${escape(sportName(workout.sport))} del ${escape(formatDate(workout.ts, true))}"></td>
    <td><button class="workout-open" data-open="${escape(workout.id)}">${sportIcon(workout.sport)}<span><strong>${escape(sportName(workout.sport))}${isDemo(workout) ? '<span class="example-tag">ESEMPIO</span>' : ""}</strong><small>${escape(formatDate(workout.ts))} · ${escape(formatTime(workout.ts))}</small></span></button></td>
    <td>${distance(workout.session.dist_m)}</td><td>${duration(workout.session.dur_s)}</td><td>${dataBadge(workout)}</td></tr>`).join("");
  const selectedVisible = list.filter((w) => state.selected.has(w.id)).length;
  $("#select-all").checked = list.length > 0 && selectedVisible === list.length;
  $("#select-all").indeterminate = selectedVisible > 0 && selectedVisible < list.length;
  $("#table-footer").textContent = state.workouts.length ? `${Math.min(list.length, state.limit)} di ${list.length} sessioni · ${state.selected.size} selezionate nell’archivio` : "In attesa del primo allenamento";
  $("#load-more").hidden = list.length <= state.limit;
}

function renderOverviewCharts(list) {
  $("#overview-charts").hidden = state.workouts.length === 0;
  const knownDistances = list.filter((w) => numeric(w.session.dist_m));
  const recent = knownDistances.slice(0, 8).reverse();
  const missingDistances = list.length - knownDistances.length;
  $("#distance-chart-note").textContent = `${knownDistances.length > 8 ? "Ultime 8 · " : ""}km${missingDistances ? ` · ${missingDistances} senza distanza` : ""}`;
  if (!recent.length) {
    $("#distance-chart").innerHTML = '<p class="overview-chart-empty">Nessuna distanza disponibile.</p>';
  } else {
    const maximum = Math.max(1, ...recent.map((w) => w.session.dist_m / 1000));
    const left = 34, plotWidth = 538, baseline = 125, plotHeight = 85;
    const slot = plotWidth / recent.length, barWidth = Math.min(46, slot * .6);
    const grid = [0, .5, 1].map((fraction) => {
      const y = baseline - fraction * plotHeight;
      return `<line class="overview-grid" x1="${left}" y1="${y}" x2="585" y2="${y}"/><text class="overview-axis" x="25" y="${y + 3}" text-anchor="end">${nf.format(maximum * fraction)}</text>`;
    }).join("");
    const bars = recent.map((w, index) => {
      const value = w.session.dist_m / 1000, height = value / maximum * plotHeight;
      const center = left + slot * (index + .5);
      const label = `${sportName(w.sport)}, ${formatDate(w.ts, true)}, ${formatTime(w.ts)}: ${distance(w.session.dist_m)}`;
      return `<g class="distance-column ${w.id === state.activeId ? "active" : ""}" data-chart-open="${escape(w.id)}" role="button" tabindex="0" aria-label="${escape(label)}"><title>${escape(label)}</title><rect class="chart-hitbox" x="${center - slot / 2}" y="12" width="${slot}" height="145"/><rect class="distance-bar" x="${center - barWidth / 2}" y="${baseline - height}" width="${barWidth}" height="${height}" rx="3"/><text class="overview-value" x="${center}" y="${baseline - height - 7}" text-anchor="middle">${nf.format(value)}</text><text class="overview-axis" x="${center}" y="148" text-anchor="middle">${escape(formatDate(w.ts))}</text></g>`;
    }).join("");
    $("#distance-chart").innerHTML = `<svg class="distance-overview-svg" viewBox="0 0 600 165" role="group" aria-label="Distanze in chilometri, dalla sessione meno recente alla più recente">${grid}${bars}</svg>`;
  }

  const times = new Map();
  let knownCount = 0;
  for (const w of list) {
    if (!numeric(w.session.dur_s)) continue;
    knownCount++;
    times.set(w.sport, (times.get(w.sport) || 0) + w.session.dur_s);
  }
  const total = [...times.values()].reduce((sum, value) => sum + value, 0);
  $("#sport-chart-total").textContent = knownCount ? `${duration(total)}${knownCount < list.length ? ` · ${list.length - knownCount} senza durata` : ""}` : "";
  $("#sport-chart").innerHTML = times.size ? `<div class="sport-time-bars">${[...times].sort((a, b) => b[1] - a[1]).map(([sport, seconds]) => {
    const share = total > 0 ? seconds / total * 100 : 0;
    return `<button class="sport-time-row" data-chart-sport="${escape(sport)}" aria-label="Filtra ${escape(sportName(sport))}: ${escape(duration(seconds))}"><span class="sport-time-label">${icon(iconName(sport))}${escape(sportName(sport))}</span><span class="sport-time-track"><svg viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true"><rect class="sport-track" width="100" height="8" rx="4"/><rect class="sport-fill" width="${share}" height="8" rx="4"/></svg></span><span class="sport-time-duration">${escape(duration(seconds))}</span></button>`;
  }).join("")}</div>` : '<p class="overview-chart-empty">Nessuna durata disponibile.</p>';
}

function renderSports() {
  const selected = $("#sport-filter").value;
  const sports = [...new Set(state.workouts.map((w) => w.sport))].sort();
  $("#sport-filter").innerHTML = '<option value="">Tutti gli sport</option>' + sports.map((sport) => `<option value="${escape(sport)}">${escape(sportName(sport))}</option>`).join("");
  $("#sport-filter").value = sports.includes(selected) ? selected : "";
}

async function refresh(added = []) {
  const response = await api("/api/workouts");
  state.workouts = (await response.json()).workouts;
  const available = new Set(state.workouts.map((w) => w.id));
  state.selected = new Set([...state.selected, ...added].filter((id) => available.has(id)));
  if (!state.activeId || !available.has(state.activeId)) state.activeId = state.workouts[0]?.id || null;
  renderSports();
  renderLibrary();
  if (state.activeId) await openWorkout(state.activeId, false);
  if (state.view === "export") schedulePreview();
}

async function openWorkout(id, rerender = true) {
  const sequence = ++state.detailSeq;
  state.activeId = id;
  if (rerender) renderLibrary();
  try {
    const detail = await (await api(`/api/workouts/${encodeURIComponent(id)}`)).json();
    if (sequence !== state.detailSeq) return;
    state.detail = detail;
    renderDetail();
  } catch (error) { toast(error.message, true); }
}

function renderDetail() {
  const w = state.detail;
  if (!w) return;
  const session = w.session;
  const labelPace = w.sport === "cycling" ? "Velocità" : "Passo";
  const listEntry = state.workouts.find((item) => item.id === w.id) || w;
  const issues = warnings(w);
  const qualityNotes = issues.length ? issues.map((item) => `<div class="quality-note ${item.level === "info" ? "" : "warning"}">${icon(item.level === "info" ? "file" : "alert")}<span>${escape(item.message)}</span></div>`).join("") : `<div class="quality-note">${icon("check")}<span>Nessuna anomalia segnalata.</span></div>`;
  $("#detail-card").innerHTML = `<div class="detail-header">${sportIcon(w.sport)}<div><h2>${escape(sportName(w.sport))}${isDemo(w) ? ' <span class="example-tag">ESEMPIO</span>' : ""}</h2><small>${escape(formatDate(w.ts, true))} · ${escape(formatTime(w.ts))}</small></div>${dataBadge(listEntry)}</div>
    <div class="detail-stats"><div class="detail-stat"><small>Distanza</small><strong>${metricValue(numeric(session.dist_m) ? session.dist_m / 1000 : null, "km")}</strong></div><div class="detail-stat"><small>Tempo attivo</small><strong>${escape(duration(session.dur_s))}</strong></div><div class="detail-stat"><small>FC media</small><strong>${metricValue(session.fc_avg, "bpm")}</strong></div></div>
    <section class="chart-section"><div class="chart-toolbar"><h3>Grafico</h3>${[["fc", "FC"], ["pace", labelPace], ["power", "Potenza"]].map(([value, label]) => `<button class="chart-tab ${state.metric === value ? "active" : ""}" data-metric="${value}" aria-pressed="${state.metric === value}">${label}</button>`).join("")}</div><p class="chart-note" id="chart-note"></p><div id="workout-chart"></div></section>
    <section class="quality-section"><h3 class="detail-section-title">Qualità dei dati</h3>${qualityNotes}</section>
    ${lapTable(w)}
    <div class="detail-source"><span title="${escape(w.provenance?.file || "")}">${escape(w.provenance?.file || "Fonte importata")}</span><button class="remove-button" id="remove-workout">Rimuovi dalla sessione</button></div>`;
  renderChart();
}

function lapTable(workout) {
  const swim = Array.isArray(workout.swim_sets);
  const fields = swim ? workout.swim_set_fields : workout.lap_fields;
  const rows = swim ? workout.swim_sets : workout.laps;
  if (!fields?.length || !rows?.length) return "";
  const kilometreSplits = !swim && workout.split_policy?.distance_m === 1000;
  const columns = swim ? [["i", "LAP"], ["dist_m", "METRI"], ["dur_s", "TEMPO"], ["pace_100m", "/100 M"], ["fc_avg", "BPM"]] : [["i", kilometreSplits ? "SPLIT" : "GIRO"], ["dist_m", "METRI"], ["dur_s", "TEMPO"], ["pace", "/KM"], ["fc_avg", "BPM"]];
  const present = columns.filter(([field]) => fields.includes(field));
  const cell = (row, field) => { const value = row[fields.indexOf(field)]; if (value === null || value === undefined) return "—"; if (field === "dur_s" && numeric(value)) { const seconds = Math.round(value); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; } return numeric(value) ? nf.format(value) : escape(value); };
  const renderRow = (row) => `<tr>${present.map(([field]) => `<td>${cell(row, field)}</td>`).join("")}</tr>`;
  const tableRows = rows.length <= 12 ? rows.map(renderRow).join("") : rows.slice(0, 10).map(renderRow).join("") + `<tr><td colspan="${present.length}" class="lap-omitted">… ${rows.length - 11} intermedi …</td></tr>` + renderRow(rows.at(-1));
  return `<section class="lap-section"><h3 class="detail-section-title">${swim ? "Lap / serie dalla fonte" : kilometreSplits ? "Split da 1 km" : "Giri dalla fonte"} · ${rows.length}</h3><div class="table-wrap"><table class="lap-table"><thead><tr>${present.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${tableRows}</tbody></table></div>${rows.length > 12 ? '<p class="chart-note">Primi 10 e ultimo. Export completo.</p>' : ""}</section>`;
}

function parsePace(value) {
  if (typeof value !== "string" || !/^\d+:\d{2}$/.test(value)) return null;
  const [minutes, seconds] = value.split(":").map(Number);
  return seconds < 60 ? minutes * 60 + seconds : null;
}

function chartData() {
  const w = state.detail, metric = state.metric;
  const cycling = w.sport === "cycling", swim = w.sport === "swimming";
  const metricField = { fc: "fc", power: "power_w", pace: "speed_mps" }[metric];
  const units = metric === "fc" ? "bpm" : metric === "power" ? "W" : cycling ? "km/h" : swim ? "min/100m" : "min/km";
  const sample = w.samples;
  if (sample?.fields?.includes(metricField) && sample.fields.includes("t_s")) {
    const yi = sample.fields.indexOf(metricField), xi = sample.fields.indexOf("t_s");
    const points = sample.rows.map((row) => {
      let y = row[yi];
      if (metric === "pace") y = numeric(y) && y > 0 ? (cycling ? y * 3.6 : (swim ? 100 : 1000) / y) : null;
      return [numeric(row[xi]) ? row[xi] / 60 : null, y];
    }).filter(([x]) => numeric(x));
    if (points.some(([, y]) => numeric(y))) return { points, units, basis: "Campioni della fonte · tempo in minuti", xUnit: "min" };
  }
  const fields = swim ? w.swim_set_fields : w.lap_fields;
  const rows = swim ? w.swim_sets : w.laps;
  const field = metric === "fc" ? "fc_avg" : metric === "power" ? "power" : swim ? "pace_100m" : "pace";
  if (fields?.includes(field) && rows?.length) {
    const idx = fields.indexOf(field);
    const points = rows.map((row, i) => {
      let y = metric === "pace" ? parsePace(row[idx]) : row[idx];
      if (metric === "pace" && cycling) y = y > 0 ? 3600 / y : null;
      return [i + 1, y];
    });
    return { points, units, basis: "Medie dei lap dalla fonte · non campioni istantanei", xUnit: "lap" };
  }
  return { points: [], units, basis: "", xUnit: "" };
}

function renderChart() {
  const { points, units, basis, xUnit } = chartData();
  const valid = points.filter(([, y]) => numeric(y));
  $("#chart-note").textContent = basis;
  if (!valid.length) {
    $("#workout-chart").innerHTML = `<div class="chart-empty">Serie non disponibile.<br>Questa misura non è presente nei campioni o nei lap.</div>`;
    return;
  }
  const width = 500, height = 195, left = 43, top = 19, right = 15, bottom = 30;
  const values = valid.map(([, y]) => y);
  let minY = values.reduce((a, b) => Math.min(a, b), Infinity), maxY = values.reduce((a, b) => Math.max(a, b), -Infinity);
  const observedMin = minY, observedMax = maxY;
  const padding = Math.max((maxY - minY) * .2, state.metric === "pace" ? 3 : 4);
  minY -= padding; maxY += padding;
  const minX = points[0][0], maxX = points.at(-1)[0] > minX ? points.at(-1)[0] : minX + 1;
  const x = (value) => left + (value - minX) / (maxX - minX) * (width - left - right);
  const y = (value) => height - bottom - (value - minY) / (maxY - minY) * (height - top - bottom);
  const tick = (value) => state.metric === "pace" && units !== "km/h" ? `${Math.floor(Math.round(value) / 60)}:${String(Math.round(value) % 60).padStart(2, "0")}` : integer.format(value);
  const grids = [0, .5, 1].map((t) => { const v = minY + t * (maxY - minY), sy = y(v); return `<line class="grid" x1="${left}" y1="${sy}" x2="${width - right}" y2="${sy}"/><text x="${left - 8}" y="${sy + 3}" text-anchor="end">${tick(v)}</text>`; }).join("");
  const segments = []; let current = [];
  for (const point of points) { if (numeric(point[1])) current.push(point); else if (current.length) { segments.push(current); current = []; } }
  if (current.length) segments.push(current);
  const lines = segments.map((segment) => {
    const stride = Math.max(1, Math.ceil(segment.length / 300));
    const reduced = segment.filter((_, index) => index % stride === 0 || index === segment.length - 1);
    const coords = reduced.map(([a, b]) => `${x(a).toFixed(2)},${y(b).toFixed(2)}`).join(" ");
    if (reduced.length === 1) return `<circle cx="${x(reduced[0][0])}" cy="${y(reduced[0][1])}" r="3" fill="#88a667" stroke="none"/>`;
    return `<polygon class="area" points="${x(reduced[0][0])},${height - bottom} ${coords} ${x(reduced.at(-1)[0])},${height - bottom}"/><polyline class="curve" points="${coords}"/>`;
  }).join("");
  const xLabels = [minX, (minX + maxX) / 2, maxX].map((v) => `<text x="${x(v)}" y="${height - 8}" text-anchor="middle">${nf.format(v)} ${xUnit}</text>`).join("");
  $("#workout-chart").innerHTML = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(units)}: da ${escape(tick(observedMin))} a ${escape(tick(observedMax))}. ${escape(basis)}"><title>${escape(units)} · ${valid.length} valori disponibili</title>${grids}${lines}${xLabels}</svg>`;
}

async function importFiles(allFiles) {
  if (state.importing) { toast("Attendi il completamento dell’importazione in corso."); return; }
  const files = [...allFiles].filter((file) => /\.(fit|json|jsonl|ndjson)$/i.test(file.name));
  const skipped = allFiles.length - files.length;
  if (!files.length) { toast("Nessun file FIT, JSON o JSONL nella selezione.", true); return; }
  state.importing = true;
  setImportBusy(true);
  let added = [], duplicates = 0; const errors = [];
  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      $("#import-progress-text").textContent = `Importazione ${i + 1} di ${files.length} · ${file.name}`;
      try {
        if (file.size > 32 * 1024 * 1024) throw new Error("Supera il limite di 32 MB per file.");
        const response = await api(`/api/import?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
        const result = await response.json();
        added.push(...result.added); duplicates += result.duplicates;
      } catch (error) { errors.push({ file: file.name, message: error.message }); }
    }
    await refresh(added);
    const message = `${added.length} ${added.length === 1 ? "allenamento importato" : "allenamenti importati"}${duplicates ? ` · ${duplicates} duplicati saltati` : ""}${skipped ? ` · ${skipped} file non supportati ignorati` : ""}${errors.length ? ` · ${errors.length} ${errors.length === 1 ? "file non importato" : "file non importati"}` : ""}`;
    $("#import-report").innerHTML = `<div class="report-line"><span>${escape(message)}</span><button class="dismiss-report" aria-label="Chiudi riepilogo importazione">${icon("close")}</button></div>${errors.length ? `<details open><summary>Controlla i file non importati</summary><ul>${errors.map((error) => `<li><strong>${escape(error.file)}</strong>: ${escape(error.message)}</li>`).join("")}</ul></details>` : ""}`;
    $("#import-report").hidden = false;
  } catch (error) { toast(error.message, true); }
  finally { state.importing = false; setImportBusy(false); $("#file-input").value = ""; $("#folder-input").value = ""; }
}

function setImportBusy(busy) {
  $("#import-progress").hidden = !busy;
  for (const selector of ["#import-button", "#folder-button", "#demo-button", "#empty-demo"]) $(selector).disabled = busy;
}

async function loadDemo() {
  if (state.importing) return;
  setImportBusy(true);
  state.importing = true;
  $("#import-progress-text").textContent = "Preparazione dell’archivio di esempio…";
  try {
    const result = await (await api("/api/demo", { method: "POST" })).json();
    await refresh(result.added);
    toast(result.added.length ? "6 sessioni sintetiche aggiunte. Nessun dato personale." : "Le sessioni di esempio sono già nell’archivio.");
  } catch (error) { toast(error.message, true); }
  finally { state.importing = false; setImportBusy(false); }
}

function showView(view) {
  state.view = view;
  $("#library-view").hidden = view !== "library";
  $("#export-view").hidden = view !== "export";
  $("#nav-library").classList.toggle("active", view === "library");
  $("#nav-export").classList.toggle("active", view === "export");
  for (const [selector, active] of [["#nav-library", view === "library"], ["#nav-export", view === "export"]]) {
    if (active) $(selector).setAttribute("aria-current", "page"); else $(selector).removeAttribute("aria-current");
  }
  $("#breadcrumb-page").textContent = view === "library" ? "Archivio" : "Esporta";
  if (view === "export") schedulePreview();
  window.scrollTo({ top: 0, behavior: "instant" });
}

function exportOptions() {
  return { ids: state.workouts.filter((w) => state.selected.has(w.id)).map((w) => w.id), profile: $('input[name="profile"]:checked').value,
    format: $('input[name="format"]:checked').value, include_gps: $("#include-gps").checked, include_identifiers: $("#include-identifiers").checked };
}

function schedulePreview() {
  clearTimeout(previewTimer);
  ++state.previewSeq;
  $("#download-button").disabled = true;
  previewTimer = setTimeout(updatePreview, 120);
}

async function updatePreview() {
  const sequence = ++state.previewSeq;
  const options = exportOptions();
  const selected = state.workouts.filter((w) => state.selected.has(w.id));
  $("#export-summary").textContent = selected.length ? `${selected.length} sessioni · ${distance(selected.filter((w) => numeric(w.session.dist_m)).reduce((sum, w) => sum + w.session.dist_m, 0))} registrati${selected.some(isDemo) ? " · include esempi sintetici" : ""}` : "Seleziona gli allenamenti nell’archivio.";
  $("#export-count").textContent = `${selected.length} ${selected.length === 1 ? "allenamento" : "allenamenti"}`;
  const sports = [...new Set(selected.map((w) => w.sport))];
  $("#export-sport-summary").innerHTML = sports.map((sport) => `<span class="sport-chip">${icon(iconName(sport))}${escape(sportName(sport))} · ${selected.filter((w) => w.sport === sport).length}</span>`).join("");
  $("#export-filename").textContent = `fit-studio-${options.profile}.${options.format}`;
  $("#preview-truncated").hidden = true;
  if (!selected.length) {
    $("#json-preview").textContent = "Seleziona almeno una sessione nell’archivio.";
    $("#preview-status").textContent = "Nessuna sessione selezionata";
    $("#export-size").textContent = "—";
    return;
  }
  $("#preview-status").textContent = "Preparazione dell’anteprima…";
  try {
    const result = await (await api("/api/export/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) })).json();
    if (sequence !== state.previewSeq) return;
    $("#json-preview").textContent = result.preview;
    $("#export-size").textContent = size(result.bytes);
    $("#preview-status").textContent = `${result.count} sessioni · ${options.profile === "compact" ? "riepilogo compatto" : "dati dettagliati"} · UTF-8`;
    $("#preview-truncated").hidden = !result.truncated;
    $("#download-button").disabled = false;
  } catch (error) { if (sequence === state.previewSeq) { $("#preview-status").textContent = error.message; $("#json-preview").textContent = "Anteprima non disponibile. Riprova o controlla la selezione."; toast(error.message, true); } }
}

async function downloadExport() {
  $("#download-button").disabled = true;
  try {
    const options = exportOptions();
    const response = await api("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) });
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url; link.download = `fit-studio-${options.profile}.${options.format}`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    toast("Export pronto. Il download è stato avviato.");
  } catch (error) { toast(error.message, true); }
  finally { $("#download-button").disabled = state.selected.size === 0; }
}

async function readEntry(entry) {
  if (entry.isFile) return [await new Promise((resolve, reject) => entry.file(resolve, reject))];
  if (!entry.isDirectory) return [];
  const reader = entry.createReader(), files = [];
  while (true) {
    const entries = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!entries.length) break;
    for (const child of entries) files.push(...await readEntry(child));
  }
  return files;
}

$("#import-button").addEventListener("click", () => $("#file-input").click());
$("#folder-button").addEventListener("click", () => $("#folder-input").click());
$("#file-input").addEventListener("change", (event) => importFiles([...event.target.files]));
$("#folder-input").addEventListener("change", (event) => importFiles([...event.target.files]));
$("#demo-button").addEventListener("click", loadDemo);
$("#empty-demo").addEventListener("click", loadDemo);
$("#nav-library").addEventListener("click", () => showView("library"));
$("#nav-export").addEventListener("click", () => showView("export"));
$("#back-library").addEventListener("click", () => showView("library"));
$("#change-selection").addEventListener("click", () => showView("library"));
$("#export-selection").addEventListener("click", () => showView("export"));
$("#download-button").addEventListener("click", downloadExport);
for (const selector of ["#search-input", "#sport-filter", "#date-from", "#date-to"]) $(selector).addEventListener("input", () => { state.limit = 50; renderLibrary(); });
$("#date-toggle").addEventListener("click", () => { const hidden = !$("#date-filters").hidden; $("#date-filters").hidden = hidden; $("#date-toggle").setAttribute("aria-expanded", String(!hidden)); });
$("#clear-filters").addEventListener("click", () => { for (const s of ["#search-input", "#sport-filter", "#date-from", "#date-to"]) $(s).value = ""; renderLibrary(); });
$("#load-more").addEventListener("click", () => { state.limit += 50; renderLibrary(); });
$("#select-all").addEventListener("change", (event) => { for (const w of filteredWorkouts()) { if (event.target.checked) state.selected.add(w.id); else state.selected.delete(w.id); } renderLibrary(); });
$("#workout-rows").addEventListener("click", (event) => { const button = event.target.closest("[data-open]"); if (button) openWorkout(button.dataset.open); });
$("#workout-rows").addEventListener("change", (event) => { if (!event.target.dataset.select) return; const id = event.target.dataset.select; if (event.target.checked) state.selected.add(id); else state.selected.delete(id); renderLibrary(); });
$("#overview-charts").addEventListener("click", (event) => {
  const session = event.target.closest("[data-chart-open]");
  if (session) { openWorkout(session.dataset.chartOpen); return; }
  const sport = event.target.closest("[data-chart-sport]");
  if (sport) {
    $("#sport-filter").value = $("#sport-filter").value === sport.dataset.chartSport ? "" : sport.dataset.chartSport;
    state.limit = 50;
    renderLibrary();
  }
});
$("#overview-charts").addEventListener("keydown", (event) => {
  const session = event.target.closest("[data-chart-open]");
  if (session && ["Enter", " "].includes(event.key)) { event.preventDefault(); openWorkout(session.dataset.chartOpen); }
});
$("#detail-card").addEventListener("click", async (event) => {
  const metric = event.target.closest("[data-metric]");
  if (metric) { state.metric = metric.dataset.metric; renderDetail(); return; }
  if (event.target.closest("#remove-workout")) {
    try {
      const id = state.activeId;
      await api(`/api/workouts/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.activeId = null; state.detail = null;
      await refresh();
      if (!state.workouts.length) $("#detail-card").innerHTML = '<div class="detail-placeholder"><h3>Sessione rimossa.</h3><p>Puoi importare altri file. Gli originali restano intatti.</p></div>';
      toast("Allenamento rimosso dalla sessione locale.");
    } catch (error) { toast(error.message, true); }
  }
});
$("#import-report").addEventListener("click", (event) => { if (event.target.closest(".dismiss-report")) $("#import-report").hidden = true; });
$("#export-view").addEventListener("change", schedulePreview);
for (const type of ["dragenter", "dragover"]) $("#drop-zone").addEventListener(type, (event) => { event.preventDefault(); $("#drop-zone").classList.add("drag-over"); });
$("#drop-zone").addEventListener("dragleave", (event) => { if (!$("#drop-zone").contains(event.relatedTarget)) $("#drop-zone").classList.remove("drag-over"); });
$("#drop-zone").addEventListener("drop", async (event) => {
  event.preventDefault(); $("#drop-zone").classList.remove("drag-over");
  try {
    // Entries must be captured before yielding; the browser clears the drag store.
    const entries = [...event.dataTransfer.items].map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
    const fallback = [...event.dataTransfer.files];
    const files = entries.length ? (await Promise.all(entries.map(readEntry))).flat() : fallback;
    await importFiles(files);
  } catch (error) { toast(`Cartella non leggibile: ${error.message}`, true); }
});
// Prevent a file dropped outside the import target from navigating away.
window.addEventListener("dragover", (event) => { if ([...event.dataTransfer.types].includes("Files")) event.preventDefault(); });
window.addEventListener("drop", (event) => event.preventDefault());

refresh().then(() => {
  // Reopening the page restores the running server’s in-memory library.
  if (!state.selected.size) state.selected = new Set(state.workouts.map((w) => w.id));
  renderLibrary();
}).catch(() => toast("Server locale non raggiungibile. Avvia FIT Studio e ricarica la pagina.", true));
