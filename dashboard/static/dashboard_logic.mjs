export const trackIdentity = (token) => (token || "").split("|").slice(0, 2).join("|");

export function preferredTrack(tracks, previous, remembered) {
  if (tracks.some((track) => track.token === remembered)) return remembered;
  if (tracks.some((track) => track.token === previous)) return previous;
  const sameTrack = tracks.find((track) => trackIdentity(track.token) === trackIdentity(previous));
  return sameTrack?.token || tracks[0]?.token || "";
}

export function matchesSelectedClasses(car, selectedClasses) {
  return !selectedClasses.size || (car.classes || []).some((value) => selectedClasses.has(value));
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
