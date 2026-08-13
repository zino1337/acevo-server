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
