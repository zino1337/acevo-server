export const trackIdentity = (token) => (token || "").split("|").slice(0, 2).join("|");
export const MOD_UPLOAD_PROXY_LIMIT_MESSAGE =
  "The web proxy rejected the upload chunk. Increase its maximum request body size to at least 8 MiB.";

export function uploadPercent(offset, total) {
  if (!(total > 0)) return 0;
  return Math.max(0, Math.min(100, Math.round((offset / total) * 100)));
}

export function resumableUploadError(status, offset, total, sessionAvailable = false) {
  if (status === 413) return MOD_UPLOAD_PROXY_LIMIT_MESSAGE;
  if (![0, 502, 503, 504].includes(status) && !(status === 409 && sessionAvailable)) return "";
  return `Upload paused at ${uploadPercent(offset, total)}%. Select the same file and click Install to resume.`;
}

export function preferredTrack(tracks, previous, remembered) {
  if (tracks.some((track) => track.token === remembered)) return remembered;
  if (tracks.some((track) => track.token === previous)) return previous;
  const sameTrack = tracks.find((track) => trackIdentity(track.token) === trackIdentity(previous));
  return sameTrack?.token || tracks[0]?.token || "";
}

export function categoryFilterDefaults(categories, cars) {
  const values = (options) => new Set((options || []).map((option) => option.value));
  return {
    types: values(categories.type),
    eras: values(categories.era),
    engines: values(categories.engine),
    classes: values(categories.class),
    mods: cars.some((car) => car.is_mod),
  };
}

export function matchesCarSearch(car, query) {
  const needle = String(query || "")
    .trim()
    .toLowerCase();
  if (!needle) return true;
  return [car.display_name, car.internal_name, car.runtime_name]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
}

export function matchesCategoryFilters(car, filters) {
  return (
    filters.types.has(car.type) ||
    filters.eras.has(car.era) ||
    filters.engines.has(car.engine) ||
    (car.classes || []).some((value) => filters.classes.has(value)) ||
    (!!filters.mods && !!car.is_mod)
  );
}

export function carHasCategory(car, kind, value) {
  if (kind === "mod") return !!car.is_mod;
  if (kind === "class") return (car.classes || []).includes(value);
  return car[kind] === value;
}

export function deselectCarsInCategory(cars, states, kind, value) {
  let changed = 0;
  for (const car of cars) {
    if (!carHasCategory(car, kind, value)) continue;
    const state = states.get(car.internal_name);
    if (!state?.is_selected) continue;
    state.is_selected = false;
    changed += 1;
  }
  return changed;
}

export function preferredCarCategory(car) {
  if (car.is_mod) return { filter: "mods", value: "mod" };
  const carClass = (car.classes || [])[0];
  if (carClass) return { filter: "classes", value: carClass };
  if (car.type) return { filter: "types", value: car.type };
  return null;
}

export function carCategoryLabel(car, categories = {}) {
  const carClass = (car.classes || [])[0];
  if (carClass) {
    return categories.class?.find((option) => option.value === carClass)?.label || carClass.toUpperCase();
  }
  if (!car.type) return "";
  return (
    categories.type?.find((option) => option.value === car.type)?.label ||
    `${car.type.charAt(0).toUpperCase()}${car.type.slice(1)}`
  );
}

export function carPerformanceLabel(car, categories = {}) {
  if (car.is_mod) return car.internal_name;
  const performance = Number(car.pi);
  const parts = [];
  if (Number.isFinite(performance)) parts.push(`Pi ${performance.toFixed(1)}`);
  const category = carCategoryLabel(car, categories);
  if (category) parts.push(category);
  return parts.join(" · ") || car.internal_name;
}

export function matchesPiFilter(car, minimum, maximum) {
  return car.is_mod || (car.pi >= minimum - 1e-6 && car.pi <= maximum + 1e-6);
}

export function setVisibleCarSelection(cars, states, selected) {
  for (const car of cars) {
    const state = states.get(car.internal_name);
    if (state) state.is_selected = !!selected;
  }
}

export function sortCarsByDisplayName(cars) {
  return [...cars].sort((left, right) => {
    const byName = String(left.display_name || left.internal_name || "").localeCompare(
      String(right.display_name || right.internal_name || ""),
      undefined,
      { sensitivity: "base", numeric: true },
    );
    if (byName) return byName;
    return String(left.internal_name || "").localeCompare(String(right.internal_name || ""));
  });
}

export function parseMobileSectionState(raw) {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return {};
    return Object.fromEntries(Object.entries(parsed).filter(([, value]) => typeof value === "boolean"));
  } catch {
    return {};
  }
}

export function formatLapTime(value) {
  if (value == null || value === "") return "—";
  const total = Math.round(Number(value));
  if (!Number.isFinite(total) || total < 0) return "—";
  const minutes = Math.floor(total / 60000);
  const seconds = Math.floor((total % 60000) / 1000);
  const millis = total % 1000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function formatLapDelta(value, fastest) {
  if (!Number.isFinite(value) || !Number.isFinite(fastest) || value <= fastest) return "";
  return `+${((value - fastest) / 1000).toFixed(3)}`;
}

function commonCarModelName(cars) {
  const bases = new Set(cars.map((car) => String(car.display_name || "").split(/\s+-\s+/)[0]).filter(Boolean));
  if (bases.size === 1) return [...bases][0];
  const names = cars.map((car) => String(car.display_name || "")).filter(Boolean);
  if (!names.length) return "";
  let prefix = names[0];
  for (const name of names.slice(1)) {
    while (prefix && !name.startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  return prefix.replace(/[\s\-–—:]+$/, "");
}

export function liveCarDisplayName(cars, internalName) {
  const raw = String(internalName || "").trim();
  if (!raw) return "Unknown car";
  const exact = cars.find((car) => car.internal_name === raw);
  if (exact) return exact.display_name || raw;
  const runtimeMatches = cars.filter((car) => car.runtime_name && car.runtime_name === raw);
  if (runtimeMatches.length === 1) return runtimeMatches[0].display_name || raw;
  if (runtimeMatches.length > 1) return commonCarModelName(runtimeMatches) || raw;
  return raw;
}
