// AC EVO Server Dashboard — frontend logic.
// Imports the vendored Material Web bundle (registers all md-* components), fetches metadata +
// saved config, renders the form, validates live, and drives the server via the API.

import "./vendor/material-web.js";
import {
  MOD_UPLOAD_PROXY_LIMIT_MESSAGE,
  formatLapDelta,
  formatLapTime,
  liveCarDisplayName,
  matchesCategoryFilters,
  matchesPiFilter,
  parseMobileSectionState,
  preferredTrack,
  resumableUploadError,
  selectedByCategoryFilters,
  sortCarsByDisplayName,
  uploadPercent,
} from "./dashboard_logic.mjs";

const api = {
  async get(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    return res.json();
  },
  async post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  },
};

let META = null;
let MODS = { mods: [], total_size: 0, running: false };
let modMutationActive = false;
const state = { server: {}, event: {}, sessions: {} };
const carState = new Map(); // internal_name -> { is_selected, ballast, restrictor }
const carFilters = {
  text: "",
  types: new Set(),
  eras: new Set(),
  engines: new Set(),
  classes: new Set(),
  mods: false,
  piMin: 0,
  piMax: 100,
  onlySelected: false,
};
let validateTimer = null;
let logsTimer = null;
let logTailTimer = null;
let liveTimer = null;
let liveRequestPending = false;
let activeView = "config";
let configSource = "env";
let configSourceWarning = "";
let configSourceSwitchAvailable = false;

const LOG_TAIL_DEFAULT = 200;
const LOG_TAIL_MAX = 50000;
const MOBILE_SECTION_STORAGE_KEY = "acevo-mobile-sections";
const mobileLayoutQuery = window.matchMedia("(max-width: 600px)");
const mobileSectionState = (() => {
  try {
    return parseMobileSectionState(localStorage.getItem(MOBILE_SECTION_STORAGE_KEY));
  } catch {
    return {};
  }
})();

// --- small helpers --------------------------------------------------------------------------

const byId = (id) => document.getElementById(id);
const isRace = () => /RACE_WEEKEND/i.test(state.event.type || "");
const trackList = () => (isRace() ? META.tracks.race_weekend : META.tracks.practice);
const allTracks = () => [...META.tracks.practice, ...META.tracks.race_weekend];
const lastTrackPerMode = new Map();

function trackPit(token) {
  const found = allTracks().find((t) => t.token === token);
  return found ? found.max_pit_slot : 50;
}

function trackDisplay(token) {
  const found = allTracks().find((t) => t.token === token);
  return found ? found.display : token;
}

function enumLabel(value) {
  if (!value) return "";
  for (const list of Object.values(META.enums)) {
    const hit = list.find((o) => o.value === value);
    if (hit) return hit.label;
  }
  const tail = String(value).split(/[_|]/).pop();
  return tail.charAt(0).toUpperCase() + tail.slice(1).toLowerCase();
}

function full(node) {
  node.classList.add("full");
  return node;
}

function textField({ label, value, type = "text", oninput, suffix, min, max, step }) {
  const field = document.createElement("md-outlined-text-field");
  field.label = label;
  field.value = value == null ? "" : String(value);
  field.type = type;
  if (suffix != null) field.setAttribute("suffix-text", suffix);
  if (min != null) field.min = min;
  if (max != null) field.max = max;
  if (step != null) field.step = step;
  field.addEventListener("input", () => oninput(field.value, field));
  return field;
}

function numberField(opts) {
  return textField({ ...opts, type: "number" });
}

function selectField({ label, value, options, onchange }) {
  const select = document.createElement("md-outlined-select");
  select.label = label;
  for (const opt of options) {
    const option = document.createElement("md-select-option");
    option.value = String(opt.value);
    if (String(opt.value) === String(value)) option.selected = true;
    option.textContent = opt.label;
    select.appendChild(option);
  }
  select.addEventListener("change", () => onchange(select.value));
  return select;
}

function switchRow(label, checked, onchange) {
  const row = document.createElement("div");
  row.className = "switch-row full";
  const span = document.createElement("span");
  span.textContent = label;
  const sw = document.createElement("md-switch");
  sw.selected = !!checked;
  sw.addEventListener("change", () => onchange(sw.selected));
  row.append(span, sw);
  return row;
}

function passwordField(label, value, oninput) {
  const row = document.createElement("div");
  row.className = "password-row full";
  const field = textField({ label, value, type: "password", oninput });
  const btn = document.createElement("md-icon-button");
  const icon = document.createElement("md-icon");
  icon.textContent = "visibility";
  btn.appendChild(icon);
  let shown = false;
  btn.addEventListener("click", () => {
    shown = !shown;
    field.type = shown ? "text" : "password";
    icon.textContent = shown ? "visibility_off" : "visibility";
  });
  row.append(field, btn);
  return row;
}

function toast(message) {
  const el = byId("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 3200);
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = bytes;
  let unit = "B";
  for (const next of units) {
    amount /= 1024;
    unit = next;
    if (amount < 1024) break;
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
}

function confirmDialog(message, headline = "Confirm", confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const dialog = byId("confirm-dialog");
    byId("confirm-message").textContent = message;
    byId("confirm-headline").textContent = headline;
    byId("confirm-ok").textContent = confirmLabel;
    const cleanup = (result) => {
      byId("confirm-ok").onclick = null;
      byId("confirm-cancel").onclick = null;
      dialog.close();
      resolve(result);
    };
    byId("confirm-ok").onclick = () => cleanup(true);
    byId("confirm-cancel").onclick = () => cleanup(false);
    dialog.show();
  });
}

function saveMobileSectionState() {
  try {
    localStorage.setItem(MOBILE_SECTION_STORAGE_KEY, JSON.stringify(mobileSectionState));
  } catch {
    /* Storage can be unavailable in private or locked-down browser contexts. */
  }
}

function syncMobileCard(card) {
  const key = card.dataset.mobileSection;
  const button = card.querySelector(":scope > h2 > .mobile-card-toggle");
  const content = card.querySelector(":scope > .mobile-card-content");
  if (!key || !button || !content) return;

  const mobile = mobileLayoutQuery.matches;
  const open = mobileSectionState[key] !== false;
  button.disabled = !mobile;
  button.tabIndex = mobile ? 0 : -1;
  button.setAttribute("aria-expanded", String(mobile ? open : true));
  content.hidden = mobile && !open;
  card.classList.toggle("mobile-collapsed", mobile && !open);
  const icon = button.querySelector(".mobile-card-toggle-icon");
  if (icon) icon.textContent = open ? "expand_less" : "expand_more";
}

function enhanceMobileCard(card) {
  if (card.dataset.mobileEnhanced === "true") {
    syncMobileCard(card);
    return;
  }
  const key = card.dataset.mobileSection;
  const heading = card.querySelector(":scope > h2");
  if (!key || !heading) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "mobile-card-toggle";
  button.setAttribute("aria-controls", `mobile-section-${key}`);

  const label = document.createElement("span");
  label.textContent = heading.textContent.trim();
  const icon = document.createElement("md-icon");
  icon.className = "mobile-card-toggle-icon";
  icon.setAttribute("aria-hidden", "true");
  button.append(label, icon);
  heading.replaceChildren(button);

  const content = document.createElement("div");
  content.id = `mobile-section-${key}`;
  content.className = "mobile-card-content";
  while (heading.nextSibling) content.append(heading.nextSibling);
  card.append(content);
  card.classList.add("mobile-collapsible");
  card.dataset.mobileEnhanced = "true";

  button.addEventListener("click", () => {
    if (!mobileLayoutQuery.matches) return;
    mobileSectionState[key] = content.hidden;
    saveMobileSectionState();
    syncMobileCard(card);
  });
  syncMobileCard(card);
}

function setupMobileCollapsibles() {
  document.querySelectorAll("#config-view .card[data-mobile-section]").forEach(enhanceMobileCard);
}

function syncMobileCollapsibles() {
  document.querySelectorAll("#config-view .card[data-mobile-section]").forEach(syncMobileCard);
}

// --- rendering ------------------------------------------------------------------------------

function renderServer() {
  const grid = byId("server-fields");
  grid.innerHTML = "";
  const s = state.server;
  const pit = trackPit(state.event.track);
  grid.append(
    full(textField({ label: "Server Name", value: s.server_name, oninput: (v) => set(s, "server_name", v) })),
    numberField({ label: "TCP Port", value: s.tcp_port, min: 1, max: 65535, oninput: (v) => set(s, "tcp_port", +v) }),
    numberField({ label: "UDP Port", value: s.udp_port, min: 1, max: 65535, oninput: (v) => set(s, "udp_port", +v) }),
    numberField({
      label: "HTTP Port",
      value: s.http_port,
      min: 1,
      max: 65535,
      oninput: (v) => set(s, "http_port", +v),
    }),
    numberField({
      label: `Max Players (≤ ${pit})`,
      value: s.max_players,
      min: 1,
      max: pit,
      oninput: (v) => set(s, "max_players", +v),
    }),
    selectField({
      label: "Server Type",
      value: s.server_type,
      options: META.enums.server_type,
      onchange: (v) => set(s, "server_type", v),
    }),
    selectField({
      label: "Tuning Type",
      value: s.tuning_type,
      options: META.enums.tuning_type,
      onchange: (v) => set(s, "tuning_type", v),
    }),
    switchRow("Cycle sessions", s.cycle_enabled, (v) => set(s, "cycle_enabled", v)),
    passwordField("Driver Password", s.driver_password, (v) => set(s, "driver_password", v)),
    passwordField("Admin Password", s.admin_password, (v) => set(s, "admin_password", v)),
    passwordField("Spectator Password", s.spectator_password, (v) => set(s, "spectator_password", v)),
  );
}

function renderAdvanced() {
  const grid = byId("advanced-fields");
  grid.innerHTML = "";
  const s = state.server;
  grid.append(
    full(
      textField({
        label: "Entry List URL",
        value: s.entry_list_url,
        oninput: (v) => set(s, "entry_list_url", v),
      }),
    ),
    full(
      textField({
        label: "Results POST URL",
        value: s.results_post_url,
        oninput: (v) => set(s, "results_post_url", v),
      }),
    ),
    full(
      textField({ label: "Entry List Path", value: s.entry_list_path, oninput: (v) => set(s, "entry_list_path", v) }),
    ),
    full(textField({ label: "Results Path", value: s.results_path, oninput: (v) => set(s, "results_path", v) })),
  );
}

function renderEvent() {
  const grid = byId("event-fields");
  grid.innerHTML = "";
  const e = state.event;
  grid.append(
    selectField({
      label: "Type",
      value: e.type,
      options: META.enums.event_type,
      onchange: (v) => {
        lastTrackPerMode.set(e.type, e.track);
        const previousTrack = e.track;
        e.type = v;
        const tracks = trackList();
        const remembered = lastTrackPerMode.get(v);
        e.track = preferredTrack(tracks, previousTrack, remembered);
        lastTrackPerMode.set(v, e.track);
        const pit = trackPit(e.track);
        if (state.server.max_players > pit) state.server.max_players = pit;
        renderEvent();
        renderServer();
        renderSessions();
        scheduleValidate();
      },
    }),
    selectField({
      label: "Initial Grip",
      value: e.initial_grip,
      options: META.enums.initial_grip,
      onchange: (v) => set(e, "initial_grip", v),
    }),
    selectField({
      label: "Weather Behaviour",
      value: e.weather_behaviour,
      options: META.enums.weather_behaviour,
      onchange: (v) => set(e, "weather_behaviour", v),
    }),
    selectField({
      label: "Weather",
      value: e.weather,
      options: META.enums.weather,
      onchange: (v) => set(e, "weather", v),
    }),
    full(
      selectField({
        label: "Track",
        value: e.track,
        options: trackList().map((t) => ({ value: t.token, label: t.display })),
        onchange: (v) => {
          e.track = v;
          lastTrackPerMode.set(e.type, v);
          // clamp max players to the new track's pit count
          const pit = trackPit(v);
          if (state.server.max_players > pit) state.server.max_players = pit;
          renderServer();
          scheduleValidate();
        },
      }),
    ),
  );
}

function categoryGroup() {
  const wrap = document.createElement("div");
  wrap.className = "category-group";
  const groups = [
    [META.categories.type, carFilters.types],
    [META.categories.era, carFilters.eras],
    [META.categories.engine, carFilters.engines],
    [META.categories.class, carFilters.classes],
  ];
  for (const [options, set_] of groups) {
    for (const opt of options) {
      const label = document.createElement("label");
      label.className = "cat";
      const cb = document.createElement("md-checkbox");
      cb.checked = set_.has(opt.value);
      cb.addEventListener("change", () => {
        if (cb.checked) set_.add(opt.value);
        else set_.delete(opt.value);
        applyCategorySelection();
        renderCarList();
        scheduleValidate();
      });
      const span = document.createElement("span");
      span.textContent = opt.label;
      label.append(cb, span);
      wrap.append(label);
    }
  }
  if (META.cars.some((car) => car.is_mod)) {
    const label = document.createElement("label");
    label.className = "cat";
    const cb = document.createElement("md-checkbox");
    cb.checked = carFilters.mods;
    cb.addEventListener("change", () => {
      carFilters.mods = cb.checked;
      applyCategorySelection();
      renderCarList();
      scheduleValidate();
    });
    const span = document.createElement("span");
    span.textContent = "Mod";
    label.append(cb, span);
    wrap.append(label);
  }
  return wrap;
}

function applyCategorySelection() {
  for (const car of META.cars) {
    carState.get(car.internal_name).is_selected = selectedByCategoryFilters(car, carFilters);
  }
  carFilters.onlySelected = false;
  const onlySelected = byId("cars-only-selected");
  if (onlySelected) onlySelected.checked = false;
}

function piRow() {
  const row = document.createElement("div");
  row.className = "pi-row";
  const label = document.createElement("span");
  const update = () => {
    label.textContent = `Pi ${carFilters.piMin.toFixed(1)} – ${carFilters.piMax.toFixed(1)}`;
  };
  const slider = document.createElement("md-slider");
  slider.range = true;
  slider.min = META.pi_min;
  slider.max = META.pi_max;
  slider.step = 0.1;
  slider.valueStart = carFilters.piMin;
  slider.valueEnd = carFilters.piMax;
  slider.labeled = true;
  slider.addEventListener("input", () => {
    carFilters.piMin = slider.valueStart;
    carFilters.piMax = slider.valueEnd;
    update();
    renderCarList();
  });
  update();
  row.append(label, slider);
  return row;
}

function renderCars() {
  const container = byId("cars-container");
  container.innerHTML = "";
  const toolbar = document.createElement("div");
  toolbar.className = "cars-toolbar";

  toolbar.append(
    textField({
      label: "Filter by name",
      value: carFilters.text,
      oninput: (v) => {
        carFilters.text = v;
        renderCarList();
      },
    }),
  );
  toolbar.append(categoryGroup());
  toolbar.append(piRow());

  const filterRow = document.createElement("div");
  filterRow.className = "cars-filter-row";
  const onlyWrap = document.createElement("label");
  onlyWrap.className = "cat";
  const onlyCb = document.createElement("md-checkbox");
  onlyCb.id = "cars-only-selected";
  onlyCb.checked = carFilters.onlySelected;
  onlyCb.addEventListener("change", () => {
    carFilters.onlySelected = onlyCb.checked;
    renderCarList();
  });
  const onlySpan = document.createElement("span");
  onlySpan.textContent = "Show only selected";
  onlyWrap.append(onlyCb, onlySpan);
  filterRow.append(onlyWrap);
  toolbar.append(filterRow);

  container.append(toolbar);

  const header = document.createElement("div");
  header.className = "cars-list-header";
  const allVisibleWrap = document.createElement("label");
  allVisibleWrap.className = "cat cars-select-all";
  const allVisibleCb = document.createElement("md-checkbox");
  allVisibleCb.id = "cars-all-visible";
  allVisibleCb.addEventListener("change", () => setAllVisible(allVisibleCb.checked));
  const allVisibleSpan = document.createElement("span");
  allVisibleSpan.textContent = "All visible cars";
  allVisibleWrap.append(allVisibleCb, allVisibleSpan);
  const ballastHeader = document.createElement("span");
  ballastHeader.className = "cars-list-header-label";
  ballastHeader.textContent = "Ballast";
  const restrictorHeader = document.createElement("span");
  restrictorHeader.className = "cars-list-header-label";
  restrictorHeader.textContent = "Restr.";
  header.append(allVisibleWrap, ballastHeader, restrictorHeader);
  container.append(header);

  const list = document.createElement("div");
  list.className = "cars-list";
  list.id = "cars-list";
  container.append(list);

  const meta = document.createElement("div");
  meta.className = "cars-meta";
  meta.id = "cars-meta";
  container.append(meta);

  renderCarList();
}

function carMatches(car) {
  const searchText = `${car.display_name} ${car.internal_name}`.toLowerCase();
  if (carFilters.text && !searchText.includes(carFilters.text.toLowerCase())) return false;
  if (!matchesCategoryFilters(car, carFilters)) return false;
  if (!matchesPiFilter(car, carFilters.piMin, carFilters.piMax)) return false;
  if (carFilters.onlySelected && !carState.get(car.internal_name).is_selected) return false;
  return true;
}

function visibleCars() {
  return sortCarsByDisplayName(META.cars.filter(carMatches));
}

function renderCarList() {
  const list = byId("cars-list");
  if (!list) return;
  list.innerHTML = "";
  const shown = visibleCars();
  for (const car of shown) {
    const cs = carState.get(car.internal_name);
    const row = document.createElement("div");
    row.className = "car-row";

    const cb = document.createElement("md-checkbox");
    cb.checked = cs.is_selected;
    cb.dataset.name = car.internal_name;
    cb.addEventListener("change", () => {
      cs.is_selected = cb.checked;
      updateCarMeta();
      scheduleValidate();
    });

    const nameWrap = document.createElement("div");
    nameWrap.className = "car-info";
    const name = document.createElement("div");
    name.className = "car-name";
    const nameText = document.createElement("span");
    nameText.textContent = car.display_name;
    name.append(nameText);
    if (car.is_mod) {
      const badge = document.createElement("span");
      badge.className = "car-mod-badge";
      badge.textContent = "MOD";
      name.append(badge);
    }
    name.title = car.display_name;
    const pi = document.createElement("div");
    pi.className = "car-pi";
    pi.textContent = car.is_mod ? car.internal_name : `Pi ${car.pi} · ${car.type}/${car.era}/${car.engine}`;
    pi.title = pi.textContent;
    nameWrap.append(name, pi);

    const ballast = textField({
      label: "Ballast",
      value: cs.ballast,
      type: "number",
      min: 0,
      oninput: (v) => {
        cs.ballast = +v || 0;
        scheduleValidate();
      },
    });
    const restrictor = textField({
      label: "Restr.",
      value: cs.restrictor,
      type: "number",
      min: 0,
      step: 0.1,
      oninput: (v) => {
        cs.restrictor = +v || 0;
        scheduleValidate();
      },
    });

    row.append(cb, nameWrap, ballast, restrictor);
    list.append(row);
  }
  updateCarMeta(shown);
}

function updateCarMeta(shownCars) {
  const meta = byId("cars-meta");
  if (!meta) return;
  const shown = Array.isArray(shownCars) ? shownCars : visibleCars();
  const selected = [...carState.values()].filter((c) => c.is_selected).length;
  meta.textContent = `${selected} of ${META.cars.length} selected - ${shown.length} shown`;
  updateAllVisibleControl(shown);
}

function updateAllVisibleControl(shownCars) {
  const cb = byId("cars-all-visible");
  if (!cb) return;
  const shown = Array.isArray(shownCars) ? shownCars : visibleCars();
  const selectedShown = shown.filter((car) => carState.get(car.internal_name).is_selected).length;
  cb.disabled = shown.length === 0;
  cb.checked = shown.length > 0 && selectedShown === shown.length;
  cb.indeterminate = selectedShown > 0 && selectedShown < shown.length;
}

function setAllVisible(value) {
  for (const car of visibleCars()) {
    carState.get(car.internal_name).is_selected = value;
  }
  renderCarList();
  scheduleValidate();
}

function sessionCard(key, title) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.session = key;
  card.dataset.mobileSection = `session-${key}`;
  const h = document.createElement("h2");
  h.textContent = title;
  const grid = document.createElement("div");
  grid.className = "field-grid";
  const s = state.sessions[key];

  if (key === "race") {
    grid.append(
      selectField({
        label: "Duration Type",
        value: s.duration_type,
        options: META.enums.duration_type,
        onchange: (v) => {
          s.duration_type = v;
          renderSessions();
          scheduleValidate();
        },
      }),
    );
    if (/LAPS/i.test(s.duration_type)) {
      grid.append(numberField({ label: "Laps", value: s.laps, min: 1, oninput: (v) => set(s, "laps", +v) }));
    } else {
      grid.append(
        numberField({ label: "Duration [sec]", value: s.length_sec, min: 0, oninput: (v) => set(s, "length_sec", +v) }),
      );
    }
  } else {
    grid.append(
      full(
        numberField({ label: "Duration [sec]", value: s.length_sec, min: 0, oninput: (v) => set(s, "length_sec", +v) }),
      ),
    );
  }

  grid.append(
    numberField({ label: "Hour", value: s.hour, min: 0, max: 23, oninput: (v) => set(s, "hour", +v) }),
    numberField({ label: "Minute", value: s.minute, min: 0, max: 59, oninput: (v) => set(s, "minute", +v) }),
    numberField({
      label: "Time Multiplier",
      value: s.time_multiplier,
      min: 1,
      oninput: (v) => set(s, "time_multiplier", +v),
    }),
    numberField({
      label: "Max wait to box [sec]",
      value: s.max_wait_to_box,
      min: 0,
      oninput: (v) => set(s, "max_wait_to_box", +v),
    }),
    full(
      numberField({
        label: "Overtime wait next session [sec]",
        value: s.overtime_waiting_next_session,
        min: 0,
        oninput: (v) => set(s, "overtime_waiting_next_session", +v),
      }),
    ),
  );

  if (key === "race") {
    grid.append(
      numberField({
        label: "Min waiting players [sec]",
        value: s.min_waiting_for_players,
        min: 0,
        oninput: (v) => set(s, "min_waiting_for_players", +v),
      }),
      numberField({
        label: "Max waiting players [sec]",
        value: s.max_waiting_for_players,
        min: 0,
        oninput: (v) => set(s, "max_waiting_for_players", +v),
      }),
    );
  }

  card.append(h, grid);
  return card;
}

function renderSessions() {
  const container = byId("sessions-container");
  container.innerHTML = "";
  container.append(sessionCard("practice", "Practice"));
  if (isRace()) {
    container.append(sessionCard("qualify", "Qualify"));
    container.append(sessionCard("warmup", "Warmup"));
    container.append(sessionCard("race", "Race"));
  }
  setupMobileCollapsibles();
}

function renderAll() {
  renderServer();
  renderAdvanced();
  renderEvent();
  renderCars();
  renderSessions();
}

function renderPreview(result) {
  const panel = byId("preview");
  panel.innerHTML = "";
  if (!result || result.error) {
    const err = document.createElement("div");
    err.className = "warning-item";
    err.textContent = result && result.error ? result.error : "Validation unavailable";
    panel.append(err);
    return;
  }
  const report = result.report || {};
  const ss = report.server_summary || {};
  const se = report.season_summary || {};
  const grid = document.createElement("div");
  grid.className = "summary-grid";
  const add = (k, v) => {
    if (v == null || v === "") return;
    const kEl = document.createElement("div");
    kEl.className = "k";
    kEl.textContent = k;
    const vEl = document.createElement("div");
    vEl.textContent = v;
    grid.append(kEl, vEl);
  };
  add("Name", ss.server_name);
  if (ss.ports) add("Ports", `${ss.ports.tcp}/${ss.ports.udp} · http ${ss.ports.http}`);
  add("Max players", ss.max_players);
  add("Cars selected", ss.car_count);
  add("Cycle", ss.cycle ? "on" : "off");
  add("Mode", enumLabel(se.game_type));
  add("Track", trackDisplay(se.track));
  add("Weather", enumLabel(se.weather));
  add("Grip", enumLabel(se.initial_grip));
  if (se.durations) {
    const d = se.durations;
    const parts = isRace()
      ? [`P ${d.practice}s`, `Q ${d.qualify}s`, `W ${d.warmup}s`, `R ${d.race}`]
      : [`P ${d.practice}s`];
    add("Durations", parts.join(" · "));
  }
  panel.append(grid);

  const warnings = result.warnings || [];
  const wrap = document.createElement("div");
  wrap.className = "warnings";
  if (!warnings.length) {
    const ok = document.createElement("div");
    ok.className = "ok-badge";
    ok.textContent = "✓ Configuration valid";
    wrap.append(ok);
  } else {
    for (const msg of warnings) {
      const item = document.createElement("div");
      item.className = "warning-item";
      item.textContent = msg;
      wrap.append(item);
    }
  }
  panel.append(wrap);
}

// --- data flow ------------------------------------------------------------------------------

function set(target, key, value) {
  target[key] = value;
  scheduleValidate();
}

function buildForm() {
  return {
    server: state.server,
    event: state.event,
    sessions: state.sessions,
    cars: [...carState.entries()].map(([name, v]) => ({
      name,
      is_selected: v.is_selected,
      ballast: v.ballast,
      restrictor: v.restrictor,
    })),
  };
}

function loadForm(saved) {
  const d = META.defaults;
  state.server = { ...d.server, ...(saved?.server || {}) };
  state.event = { show_only_selected: false, ...d.event, ...(saved?.event || {}) };
  state.sessions = {
    practice: { ...d.sessions.practice, ...(saved?.sessions?.practice || {}) },
    qualify: { ...d.sessions.qualify, ...(saved?.sessions?.qualify || {}) },
    warmup: { ...d.sessions.warmup, ...(saved?.sessions?.warmup || {}) },
    race: { ...d.sessions.race, ...(saved?.sessions?.race || {}) },
  };
  if (!state.event.track || !allTracks().some((t) => t.token === state.event.track)) {
    state.event.track = trackList()[0] ? trackList()[0].token : "";
  }
  lastTrackPerMode.clear();
  lastTrackPerMode.set(state.event.type, state.event.track);

  carState.clear();
  const savedCars = new Map((saved?.cars || []).map((c) => [c.name, c]));
  const hasSaved = !!(saved && saved.cars);
  for (const car of META.cars) {
    const sc = savedCars.get(car.internal_name);
    carState.set(car.internal_name, {
      is_selected: hasSaved ? !!(sc && sc.is_selected) : true,
      ballast: sc ? Number(sc.ballast) || 0 : 0,
      restrictor: sc ? Number(sc.restrictor) || 0 : 0,
    });
  }

  carFilters.piMin = META.pi_min;
  carFilters.piMax = META.pi_max;
  carFilters.types.clear();
  carFilters.eras.clear();
  carFilters.engines.clear();
  carFilters.classes.clear();
  carFilters.mods = false;
}

async function refreshCarCatalog() {
  const previous = new Map(carState);
  META = await api.get("/api/metadata");
  carState.clear();
  for (const car of META.cars) {
    carState.set(
      car.internal_name,
      previous.get(car.internal_name) || { is_selected: false, ballast: 0, restrictor: 0 },
    );
  }
  carFilters.piMin = META.pi_min;
  carFilters.piMax = META.pi_max;
  renderCars();
  runValidate();
}

// --- mods -----------------------------------------------------------------------------------

function updateModControls() {
  const input = byId("mod-file");
  const button = byId("btn-mod-upload");
  input.disabled = modMutationActive;
  button.disabled = modMutationActive || !input.files?.length;
}

function renderMods() {
  const rows = byId("mods-rows");
  rows.replaceChildren();
  if (!MODS.mods.length) {
    const empty = document.createElement("div");
    empty.className = "mods-empty";
    empty.textContent = "No mods installed yet.";
    rows.append(empty);
  }

  for (const mod of MODS.mods) {
    const row = document.createElement("div");
    row.className = "mods-row";
    row.setAttribute("role", "row");

    const file = document.createElement("div");
    file.className = "mod-file";
    file.textContent = mod.filename;
    file.title = mod.filename;

    const cars = document.createElement("div");
    cars.className = "mod-cars";
    cars.textContent = mod.cars.map((car) => car.display_name).join(", ") || "—";
    cars.title = cars.textContent;

    const variants = document.createElement("div");
    variants.className = "mod-variants";
    variants.textContent = String(mod.variant_count || 0);
    variants.title = (mod.preset_ids || []).join("\n");

    const size = document.createElement("div");
    size.className = "mod-size";
    size.textContent = formatBytes(mod.size);

    const status = document.createElement("div");
    status.className = "mod-state";
    const statusBadge = document.createElement("span");
    statusBadge.className = `mod-status ${mod.status}`;
    statusBadge.textContent = mod.status === "ready" ? "Ready" : mod.status === "conflict" ? "Conflict" : "Invalid";
    statusBadge.title = mod.error || (mod.preset_ids || []).join("\n");
    status.append(statusBadge);

    const actions = document.createElement("div");
    actions.className = "mod-actions";
    const deleteButton = document.createElement("md-icon-button");
    deleteButton.disabled = modMutationActive;
    deleteButton.setAttribute("aria-label", `Delete ${mod.filename}`);
    deleteButton.title = `Delete ${mod.filename}`;
    const deleteIcon = document.createElement("md-icon");
    deleteIcon.textContent = "delete";
    deleteButton.append(deleteIcon);
    deleteButton.addEventListener("click", () => deleteMod(mod.filename));
    actions.append(deleteButton);

    row.append(file, cars, variants, size, status, actions);
    rows.append(row);
  }

  byId("mods-summary").textContent =
    `${MODS.mods.length} mod${MODS.mods.length === 1 ? "" : "s"} · ${formatBytes(MODS.total_size)}`;
  updateModControls();
}

async function refreshMods() {
  try {
    const result = await api.get("/api/mods");
    if (result.error) throw new Error(result.error);
    MODS = result;
    renderMods();
  } catch (error) {
    byId("mods-summary").textContent = `Could not load mods: ${error}`;
  }
}

class ModUploadError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
    this.uploadId = "";
    this.confirmedOffset = 0;
    this.totalSize = 0;
    this.sessionAvailable = false;
  }
}

function parseUploadResponse(responseText) {
  try {
    return JSON.parse(responseText || "{}");
  } catch {
    return {};
  }
}

function setModUploadProgress(offset, total, label) {
  const percent = uploadPercent(offset, total);
  byId("mod-upload-progress").classList.remove("hidden");
  byId("mod-upload-progress-bar").style.width = `${percent}%`;
  byId("mod-upload-label").classList.remove("error");
  byId("mod-upload-label").textContent = label || `Uploading… ${percent}%`;
  return percent;
}

function showModUploadError(message, keepProgress = false) {
  if (!keepProgress) byId("mod-upload-progress").classList.add("hidden");
  byId("mod-upload-label").classList.add("error");
  byId("mod-upload-label").textContent = message;
}

function clearModUploadStatus() {
  byId("mod-upload-progress").classList.add("hidden");
  byId("mod-upload-progress-bar").style.width = "0";
  byId("mod-upload-label").classList.remove("error");
  byId("mod-upload-label").textContent = "";
}

async function startModUpload(file) {
  let response;
  try {
    response = await fetch("/api/mods/upload/start", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ filename: file.name, size: file.size, last_modified: file.lastModified }),
    });
  } catch {
    throw new ModUploadError("network error");
  }
  const body = parseUploadResponse(await response.text());
  if (!response.ok) throw new ModUploadError(body.error || `HTTP ${response.status}`, response.status);
  return body;
}

function uploadModChunk(file, session, offset) {
  return new Promise((resolve, reject) => {
    const end = Math.min(offset + session.chunk_size, file.size);
    const chunk = file.slice(offset, end);
    const request = new XMLHttpRequest();
    request.open("POST", `/api/mods/upload/chunk?upload_id=${encodeURIComponent(session.upload_id)}&offset=${offset}`);
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      setModUploadProgress(offset + event.loaded, file.size);
    });
    request.upload.addEventListener("load", () => {
      if (end === file.size) setModUploadProgress(file.size, file.size, "Checking mod…");
    });
    request.addEventListener("load", () => {
      const body = parseUploadResponse(request.responseText);
      if (request.status >= 200 && request.status < 300) resolve(body);
      else if (request.status === 413) reject(new ModUploadError(MOD_UPLOAD_PROXY_LIMIT_MESSAGE, 413));
      else reject(new ModUploadError(body.error || `HTTP ${request.status}`, request.status));
    });
    request.addEventListener("error", () => reject(new ModUploadError("network error")));
    request.addEventListener("abort", () => reject(new ModUploadError("upload aborted")));
    request.send(chunk);
  });
}

async function confirmedModUploadOffset(uploadId, fallback) {
  try {
    const response = await fetch(`/api/mods/upload/status?upload_id=${encodeURIComponent(uploadId)}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return { offset: fallback, available: false };
    const body = parseUploadResponse(await response.text());
    return {
      offset: Number.isSafeInteger(body.offset) ? body.offset : fallback,
      available: Number.isSafeInteger(body.offset),
    };
  } catch {
    return { offset: fallback, available: false };
  }
}

async function uploadModRequest(file) {
  const session = await startModUpload(file);
  if (session.complete) return session;
  let offset = Number(session.offset) || 0;
  const resumed = offset > 0;
  const percent = setModUploadProgress(offset, file.size);
  if (resumed) byId("mod-upload-label").textContent = `Resuming upload at ${percent}%…`;

  while (offset < file.size) {
    try {
      const result = await uploadModChunk(file, session, offset);
      const nextOffset = Number(result.offset);
      if (!Number.isSafeInteger(nextOffset) || nextOffset <= offset || nextOffset > file.size) {
        throw new ModUploadError("server returned an invalid upload offset");
      }
      offset = nextOffset;
      if (result.complete) return result;
      setModUploadProgress(offset, file.size);
    } catch (error) {
      const failure = error instanceof ModUploadError ? error : new ModUploadError(String(error));
      failure.uploadId = session.upload_id;
      const status = await confirmedModUploadOffset(session.upload_id, offset);
      failure.confirmedOffset = status.offset;
      failure.sessionAvailable = status.available;
      failure.totalSize = file.size;
      throw failure;
    }
  }
  throw new ModUploadError("upload ended before the mod was installed");
}

async function modServerIsRunning() {
  try {
    const status = await api.get("/api/server/status");
    MODS.running = !!status.running;
  } catch {
    // Keep the latest known status; the mutation endpoint still performs its own check.
  }
  return MODS.running;
}

async function stopServerForModChange() {
  if (!MODS.running) return true;
  let result;
  try {
    result = await api.post("/api/server/stop");
  } catch (error) {
    toast(`Could not stop server: ${error.message || error}`);
    return false;
  }
  if (!result.ok) {
    toast(`Could not stop server: ${result.error || result.stderr || "unknown error"}`);
    return false;
  }
  MODS.running = false;
  renderMods();
  toast("Server stopped.");
  refreshStatus();
  refreshLiveSoon();
  return true;
}

async function uploadMod() {
  const input = byId("mod-file");
  const file = input.files?.[0];
  if (!file || modMutationActive) return;
  modMutationActive = true;
  let keepUploadStatus = false;
  updateModControls();
  renderMods();
  try {
    const serverRunning = await modServerIsRunning();
    if (
      serverRunning &&
      !(await confirmDialog(
        `The game server is currently running and must be stopped to install ${file.name}. Stop it now and continue? Connected players will be disconnected.`,
        "Stop server and install mod",
      ))
    ) {
      return;
    }
    if (!(await stopServerForModChange())) return;
    clearModUploadStatus();
    setModUploadProgress(0, file.size);
    await uploadModRequest(file);
    toast(`${file.name} installed`);
    input.value = "";
    byId("mod-file-name").textContent = "Choose a .kspkg file…";
    await refreshMods();
    await refreshCarCatalog();
  } catch (error) {
    await refreshMods();
    const installed = MODS.mods.some((mod) => mod.filename.toLowerCase() === file.name.toLowerCase());
    if (installed) {
      toast(`${file.name} installed`);
      input.value = "";
      byId("mod-file-name").textContent = "Choose a .kspkg file…";
      await refreshCarCatalog();
    } else {
      const status = Number(error.status) || 0;
      const confirmed = Number(error.confirmedOffset) || 0;
      const total = Number(error.totalSize) || file.size;
      const resumableMessage = resumableUploadError(status, confirmed, total, error.sessionAvailable);
      if (status === 413) {
        setModUploadProgress(confirmed, total);
        showModUploadError(resumableMessage, true);
        keepUploadStatus = true;
      } else if (resumableMessage && error.uploadId) {
        setModUploadProgress(confirmed, total);
        showModUploadError(resumableMessage, true);
        keepUploadStatus = true;
      } else {
        showModUploadError(`Install failed: ${error.message || error}`);
        keepUploadStatus = true;
      }
      toast(`Install failed: ${error.message || error}`);
    }
  } finally {
    modMutationActive = false;
    if (!keepUploadStatus) clearModUploadStatus();
    updateModControls();
    renderMods();
  }
}

async function deleteMod(filename) {
  if (modMutationActive) return;
  modMutationActive = true;
  renderMods();
  const serverRunning = await modServerIsRunning();
  const message = serverRunning
    ? `The game server is currently running and must be stopped to delete ${filename}. Stop the server and delete the mod? Connected players will be disconnected.`
    : `Delete ${filename}? Its vehicle variants will be removed from the active configuration automatically.`;
  if (!(await confirmDialog(message, serverRunning ? "Stop server and delete mod" : "Delete mod"))) {
    modMutationActive = false;
    renderMods();
    return;
  }

  try {
    if (!(await stopServerForModChange())) return;
    const result = await api.post("/api/mods/delete", { filename });
    if (result.error) {
      toast(`Delete failed: ${result.error}`);
      return;
    }
    const removed = result.deselected?.length || 0;
    toast(
      removed
        ? `${filename} deleted · ${removed} variant${removed === 1 ? "" : "s"} removed from configuration`
        : `${filename} deleted`,
    );
    await refreshMods();
    await refreshCarCatalog();
  } finally {
    modMutationActive = false;
    renderMods();
  }
}

function scheduleValidate() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(runValidate, 350);
}

async function runValidate() {
  try {
    const result = await api.post("/api/validate", { form: buildForm() });
    renderPreview(result);
  } catch (err) {
    renderPreview({ error: String(err) });
  }
}

// --- configuration profiles (Profiles tab) --------------------------------------------------

let PROFILES = [];

async function loadProfiles() {
  try {
    const data = await api.get("/api/configs");
    PROFILES = data.profiles || [];
  } catch {
    PROFILES = [];
  }
  renderProfiles();
}

function renderProfiles() {
  const list = byId("profiles-list");
  if (!list) return;
  list.innerHTML = "";
  if (!PROFILES.length) {
    const empty = document.createElement("div");
    empty.className = "profiles-empty";
    empty.textContent = "No saved profiles yet.";
    list.append(empty);
    return;
  }
  for (const profile of PROFILES) {
    const row = document.createElement("div");
    row.className = "profile-row";

    const info = document.createElement("div");
    info.className = "profile-info";
    const name = document.createElement("div");
    name.className = "profile-name";
    name.textContent = profile.name;
    const meta = document.createElement("div");
    meta.className = "profile-meta";
    const parts = [];
    if (profile.server_name) parts.push(profile.server_name);
    if (profile.mode) parts.push(enumLabel(profile.mode));
    if (profile.track) parts.push(trackDisplay(profile.track));
    meta.textContent = parts.join(" · ");
    info.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "profile-actions";
    const loadBtn = document.createElement("md-text-button");
    loadBtn.textContent = "Load";
    loadBtn.addEventListener("click", () => applyProfile(profile.name));
    const delBtn = document.createElement("md-icon-button");
    delBtn.setAttribute("aria-label", `Delete ${profile.name}`);
    const delIcon = document.createElement("md-icon");
    delIcon.textContent = "delete";
    delBtn.append(delIcon);
    delBtn.addEventListener("click", () => deleteProfile(profile.name));
    actions.append(loadBtn, delBtn);

    row.append(info, actions);
    list.append(row);
  }
}

async function applyProfile(name) {
  let res;
  try {
    res = await api.get(`/api/configs/get?name=${encodeURIComponent(name)}`);
  } catch (err) {
    toast("Load failed: " + err);
    return;
  }
  if (!res || res.error || !res.form) {
    toast("Load failed: " + (res && res.error ? res.error : "unknown"));
    return;
  }
  loadForm(res.form);
  renderAll();
  runValidate();
  setActiveView("config");
  toast(`Loaded profile "${name}" — review, then Save & Apply`);
}

async function saveProfile() {
  const field = byId("profile-name");
  const name = (field.value || "").trim();
  if (!name) {
    toast("Enter a profile name first.");
    return;
  }
  const res = await api.post("/api/configs/save", { name, form: buildForm() });
  if (!res.ok) {
    toast("Save failed: " + (res.error || "unknown"));
    return;
  }
  const warned = res.warnings && res.warnings.length ? ` (${res.warnings.length} warning(s))` : "";
  toast(`Profile "${res.name}" saved` + warned);
  field.value = "";
  loadProfiles();
}

async function deleteProfile(name) {
  if (!name) return;
  if (!(await confirmDialog(`Delete profile "${name}"?`, "Delete profile"))) return;
  const res = await api.post("/api/configs/delete", { name });
  if (!res.ok) {
    toast("Delete failed: " + (res.error || "unknown"));
    return;
  }
  toast(`Profile "${name}" deleted`);
  loadProfiles();
}

// --- configuration source -------------------------------------------------------------------

function updateConfigSource(info = {}) {
  if (info.config_source) configSource = info.config_source;
  configSourceWarning = info.source_warning || "";
  configSourceSwitchAvailable = !!info.source_switch_available;
  const chip = byId("config-priority");
  const icon = byId("config-priority-icon");
  const value = byId("config-priority-value");
  const sourceLabel = configSource === "dashboard" ? "Dashboard" : "ENV";
  const targetLabel = configSource === "dashboard" ? "ENV" : "Dashboard";
  chip.classList.toggle("hidden", !configSourceWarning && !configSourceSwitchAvailable);
  chip.classList.toggle("warning", !!configSourceWarning);
  if (configSourceWarning) {
    icon.textContent = "!";
    value.textContent = "Warning";
    chip.title = configSourceWarning;
    chip.setAttribute("aria-label", `Configuration warning: ${configSourceWarning}`);
  } else {
    icon.textContent = "⇄";
    value.textContent = sourceLabel;
    chip.title = `Configuration priority: ${sourceLabel}. Click to use ${targetLabel}.`;
    chip.setAttribute("aria-label", chip.title);
  }
}

async function reloadEffectiveConfig() {
  const cfg = await api.get("/api/config");
  loadForm(cfg.form);
  byId("config-path").textContent = cfg.config_path || "—";
  updateConfigSource(cfg);
  renderAll();
  runValidate();
}

async function switchConfigSource() {
  const chip = byId("config-priority");
  if (!configSourceSwitchAvailable) {
    if (configSourceWarning) toast(configSourceWarning);
    return;
  }
  const target = configSource === "dashboard" ? "env" : "dashboard";
  const message =
    target === "env"
      ? "Use ENV priority? Your saved Dashboard configuration will be kept. A running server will restart."
      : "Use the saved Dashboard configuration? A running server will restart.";
  const action = target === "env" ? "Use ENV priority" : "Use Dashboard config";
  if (!(await confirmDialog(message, "Change configuration priority", action))) return;
  chip.disabled = true;
  try {
    const result = await api.post("/api/config/source", { source: target });
    if (result.error) {
      toast("Priority change failed: " + result.error);
      return;
    }
    await reloadEffectiveConfig();
    toast(result.restarted ? "Priority changed — server restarting." : "Configuration priority changed.");
    refreshStatus();
    refreshLogsSoon();
    refreshLiveSoon();
  } catch (error) {
    toast("Priority change failed: " + (error?.message || "network error"));
  } finally {
    chip.disabled = false;
  }
}

// --- server control + status ----------------------------------------------------------------

function statusLabel(s) {
  return (
    {
      running: "Running",
      stopped: "Stopped",
      missing: "Not created",
      "docker-missing": "Docker not found",
      error: "Error",
    }[s.state] || "Unknown"
  );
}

async function refreshStatus() {
  try {
    const s = await api.get("/api/server/status");
    const chip = byId("status-chip");
    chip.className = "status-chip " + (s.state || "unknown");
    byId("status-text").textContent = statusLabel(s);
    if (activeView === "mods" && MODS.running !== !!s.running) refreshMods();
  } catch {
    byId("status-text").textContent = "Unknown";
  }
}

async function doStart() {
  const r = await api.post("/api/server/start");
  toast(r.ok ? "Server starting…" : "Start failed: " + (r.error || r.stderr || ""));
  refreshStatus();
  refreshLogsSoon();
  refreshLiveSoon();
}

async function doStop() {
  if (!(await confirmDialog("Stop the server? Connected players will be disconnected.", "Stop server"))) return;
  const r = await api.post("/api/server/stop");
  toast(r.ok ? "Server stopped." : "Stop failed: " + (r.error || r.stderr || ""));
  refreshStatus();
  refreshLiveSoon();
}

async function doRestart() {
  if (!(await confirmDialog("Restart to apply the config? Players will briefly disconnect.", "Restart server"))) return;
  const r = await api.post("/api/server/restart");
  toast(r.ok ? "Restarting…" : "Restart failed: " + (r.error || r.stderr || ""));
  refreshStatus();
  refreshLogsSoon();
  refreshLiveSoon();
}

async function doSave() {
  const r = await api.post("/api/save", { form: buildForm() });
  if (r.error) {
    toast("Save failed: " + r.error);
  } else {
    toast("Saved to " + r.path);
    renderPreview(r);
    updateConfigSource(r);
  }
  return r;
}

async function doSaveApply() {
  const form = buildForm();
  const validation = await api.post("/api/validate", { form });
  if (validation.error) {
    toast("Validation failed: " + validation.error);
    return;
  }
  renderPreview(validation);
  const conflicts = validation.env_conflicts || [];
  const message = conflicts.length
    ? `Environment variables conflict with this configuration: ${conflicts.join(", ")}. Save & Apply will use the Dashboard values. We recommend removing these variables from your deployment.`
    : "Save this configuration and use Dashboard priority? A running server will restart.";
  if (!(await confirmDialog(message, "Save & Apply", "Save & Apply"))) return;
  const r = await api.post("/api/server/apply", { form });
  if (r.error) {
    toast("Apply failed: " + r.error);
  } else {
    toast(r.restarted ? "Applied — server restarting." : "Applied. Dashboard priority is active.");
    renderPreview(r);
    updateConfigSource(r);
  }
  refreshStatus();
  refreshLogsSoon();
  refreshLiveSoon();
}

// --- live session ---------------------------------------------------------------------------

function liveMetric(className, label, value) {
  const cell = document.createElement("span");
  cell.className = className;
  const mobileLabel = document.createElement("span");
  mobileLabel.className = "live-mobile-label";
  mobileLabel.textContent = label;
  const content = document.createElement("span");
  content.textContent = value;
  cell.append(mobileLabel, content);
  return { cell, content };
}

function renderLiveDriver(driver, fastest) {
  const row = document.createElement("div");
  row.className = "live-driver-row";
  row.setAttribute("role", "row");

  const number = document.createElement("span");
  number.className = "live-number";
  number.textContent = driver.number ?? "—";

  const name = document.createElement("span");
  name.className = "live-driver-name";
  name.textContent = driver.name || "Unknown driver";
  name.title = name.textContent;

  const car = document.createElement("span");
  car.className = "live-car";
  car.textContent = liveCarDisplayName(META.cars, driver.car);
  car.title = driver.car || car.textContent;

  const laps = liveMetric("live-laps", "Laps", String(driver.laps || 0));
  const best = liveMetric("live-best", "Best", formatLapTime(driver.best_lap_ms));
  const delta = formatLapDelta(driver.best_lap_ms, fastest);
  if (driver.best_lap_ms === fastest) best.cell.classList.add("live-fastest");
  if (delta) {
    const deltaLabel = document.createElement("small");
    deltaLabel.className = "live-delta";
    deltaLabel.textContent = delta;
    best.cell.append(deltaLabel);
  }
  const last = liveMetric("live-last", "Last", formatLapTime(driver.last_lap_ms));

  row.append(number, name, car, laps.cell, best.cell, last.cell);
  return row;
}

function showLiveMessage(message, error = false) {
  const panel = byId("live-message");
  panel.textContent = message;
  panel.classList.remove("hidden");
  panel.classList.toggle("error", error);
}

function renderLive(data) {
  const drivers = Array.isArray(data.drivers) ? data.drivers : [];
  byId("live-connected").textContent = String(Math.max(Number(data.players) || 0, drivers.length));
  byId("live-slots").textContent = String(state.server.max_players ?? "—");
  byId("live-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;

  const list = byId("live-drivers");
  const rows = byId("live-driver-rows");
  if (!data.running) {
    rows.replaceChildren();
    list.classList.add("hidden");
    showLiveMessage("Server is stopped.");
    return;
  }
  if (!drivers.length) {
    rows.replaceChildren();
    list.classList.add("hidden");
    showLiveMessage("No drivers connected.");
    return;
  }

  const fastest = drivers.reduce(
    (best, driver) =>
      Number.isFinite(driver.best_lap_ms) && (best == null || driver.best_lap_ms < best) ? driver.best_lap_ms : best,
    null,
  );
  rows.replaceChildren(...drivers.map((driver) => renderLiveDriver(driver, fastest)));
  byId("live-message").classList.add("hidden");
  list.classList.remove("hidden");
}

async function refreshLive() {
  if (liveRequestPending) return;
  liveRequestPending = true;
  try {
    const data = await api.get("/api/server/live");
    if (typeof data?.running !== "boolean" || !Array.isArray(data.drivers)) throw new Error("invalid live response");
    renderLive(data);
  } catch {
    showLiveMessage("Live data temporarily unavailable. Retrying…", true);
  } finally {
    liveRequestPending = false;
  }
}

function startLivePolling() {
  clearInterval(liveTimer);
  refreshLive();
  liveTimer = setInterval(refreshLive, 4000);
}

function stopLivePolling() {
  clearInterval(liveTimer);
  liveTimer = null;
}

function refreshLiveSoon() {
  setTimeout(() => {
    if (activeView === "live") refreshLive();
  }, 500);
}

async function refreshLogs() {
  try {
    const r = await api.get(`/api/server/logs?tail=${encodeURIComponent(readLogTail())}`);
    const lines = r.lines || "";
    byId("logs-output").textContent = lines || r.error || r.message || "(no output)";
  } catch (err) {
    byId("logs-output").textContent = String(err);
  }
}

function refreshLogsSoon() {
  setTimeout(() => {
    if (activeView === "logs") refreshLogs();
  }, 1500);
}

function startLogPolling() {
  clearInterval(logsTimer);
  refreshLogs();
  logsTimer = setInterval(refreshLogs, 4000);
}

function stopLogPolling() {
  clearInterval(logsTimer);
  logsTimer = null;
}

const VIEW_INDEX = { config: 0, mods: 1, live: 2, logs: 3, profiles: 4 };

function setActiveView(view) {
  activeView = view;
  byId("config-view").classList.toggle("hidden", view !== "config");
  byId("mods-view").classList.toggle("hidden", view !== "mods");
  byId("live-view").classList.toggle("hidden", view !== "live");
  byId("logs-view").classList.toggle("hidden", view !== "logs");
  byId("profiles-view").classList.toggle("hidden", view !== "profiles");
  byId("tab-config").active = view === "config";
  byId("tab-mods").active = view === "mods";
  byId("tab-live").active = view === "live";
  byId("tab-logs").active = view === "logs";
  byId("tab-profiles").active = view === "profiles";
  byId("main-tabs").activeTabIndex = VIEW_INDEX[view] ?? 0;
  if (view === "logs") startLogPolling();
  else stopLogPolling();
  if (view === "live") startLivePolling();
  else stopLivePolling();
  if (view === "profiles") loadProfiles();
  if (view === "mods") refreshMods();
}

function normalizeLogTail(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) return LOG_TAIL_DEFAULT;
  return Math.min(parsed, LOG_TAIL_MAX);
}

function readLogTail() {
  const preset = byId("log-tail-preset").value;
  if (preset === "custom") return normalizeLogTail(byId("log-tail-custom").value);
  return normalizeLogTail(preset);
}

function handleLogTailPreset() {
  const preset = byId("log-tail-preset").value;
  const custom = byId("log-tail-custom");
  if (preset !== "custom") custom.value = preset;
  custom.classList.toggle("hidden", preset !== "custom");
  scheduleLogRefresh();
}

function scheduleLogRefresh() {
  clearTimeout(logTailTimer);
  if (activeView !== "logs") return;
  logTailTimer = setTimeout(refreshLogs, 250);
}

async function copyLogs() {
  const text = byId("logs-output").textContent || "";
  if (!text.trim()) {
    toast("No logs to copy.");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast("Logs copied.");
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "clipboard-fallback";
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast("Logs copied.");
  }
}

function downloadLogs() {
  const text = byId("logs-output").textContent || "";
  if (!text.trim()) {
    toast("No logs to download.");
    return;
  }
  const blob = new Blob([text.endsWith("\n") ? text : `${text}\n`], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  link.href = url;
  link.download = `acevo-server-${stamp}.log`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  toast("Logs downloaded.");
}

// --- theme ----------------------------------------------------------------------------------

function setTheme(dark) {
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  localStorage.setItem("acevo-theme", dark ? "dark" : "light");
  const sw = byId("theme-switch");
  if (sw) sw.selected = dark;
}

function applyInitialTheme() {
  const saved = localStorage.getItem("acevo-theme");
  const dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  setTheme(dark);
}

// --- init -----------------------------------------------------------------------------------

function wireControls() {
  byId("btn-start").addEventListener("click", doStart);
  byId("btn-stop").addEventListener("click", doStop);
  byId("btn-restart").addEventListener("click", doRestart);
  byId("btn-save").addEventListener("click", doSave);
  byId("btn-save-apply").addEventListener("click", doSaveApply);
  byId("config-priority").addEventListener("click", switchConfigSource);
  byId("btn-profile-save").addEventListener("click", saveProfile);
  byId("tab-config").addEventListener("click", () => setActiveView("config"));
  byId("tab-mods").addEventListener("click", () => setActiveView("mods"));
  byId("tab-live").addEventListener("click", () => setActiveView("live"));
  byId("tab-logs").addEventListener("click", () => setActiveView("logs"));
  byId("tab-profiles").addEventListener("click", () => setActiveView("profiles"));
  byId("log-tail-preset").addEventListener("change", handleLogTailPreset);
  byId("log-tail-custom").addEventListener("input", scheduleLogRefresh);
  byId("btn-log-refresh").addEventListener("click", refreshLogs);
  byId("btn-copy-logs").addEventListener("click", copyLogs);
  byId("btn-download-logs").addEventListener("click", downloadLogs);
  byId("mod-file").addEventListener("change", () => {
    const file = byId("mod-file").files?.[0];
    byId("mod-file-name").textContent = file ? file.name : "Choose a .kspkg file…";
    clearModUploadStatus();
    updateModControls();
  });
  byId("btn-mod-upload").addEventListener("click", uploadMod);
  byId("theme-switch").addEventListener("change", (e) => setTheme(e.target.selected));
  mobileLayoutQuery.addEventListener("change", syncMobileCollapsibles);
}

async function init() {
  applyInitialTheme();
  META = await api.get("/api/metadata");
  let cfg = { form: null, config_path: "" };
  try {
    cfg = await api.get("/api/config");
  } catch {
    /* no saved config yet */
  }
  loadForm(cfg.form);
  byId("config-path").textContent = cfg.config_path || "—";
  updateConfigSource(cfg);

  renderAll();
  wireControls();
  setActiveView("config");

  runValidate();
  refreshStatus();
  setInterval(refreshStatus, 6000);
}

init();
