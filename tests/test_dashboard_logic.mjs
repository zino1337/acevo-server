import assert from "node:assert/strict";
import test from "node:test";

import {
  formatLapDelta,
  formatLapTime,
  hasActiveCategoryFilters,
  matchesCategoryFilters,
  parseMobileSectionState,
  preferredTrack,
  selectedByCategoryFilters,
  trackIdentity,
} from "../dashboard/static/dashboard_logic.mjs";

const practice = [
  { token: "Brands Hatch|GP|GP Time Attack|3916" },
  { token: "Nurburgring|Touristenfahrten|Touristenfahrten Time Attack|19300" },
];
const race = [{ token: "Brands Hatch|GP|GP Race|3916" }, { token: "Monza|GP|GP Race|5793" }];

test("track identity ignores mode-specific event names", () => {
  assert.equal(trackIdentity(practice[0].token), trackIdentity(race[0].token));
});

test("remembered track wins when returning to a mode", () => {
  assert.equal(preferredTrack(practice, race[1].token, practice[1].token), practice[1].token);
});

test("same track and layout are retained across modes", () => {
  assert.equal(preferredTrack(race, practice[0].token, ""), race[0].token);
});

test("first valid track is the final fallback", () => {
  assert.equal(preferredTrack(race, practice[1].token, "missing"), race[0].token);
});

test("category filters are additive across groups", () => {
  const filters = {
    types: new Set(),
    eras: new Set(),
    engines: new Set(["ev"]),
    classes: new Set(["gt3"]),
  };
  assert.equal(matchesCategoryFilters({ engine: "ice", classes: ["gt3"] }, filters), true);
  assert.equal(matchesCategoryFilters({ engine: "ev", classes: [] }, filters), true);
  assert.equal(matchesCategoryFilters({ engine: "ice", classes: ["cup"] }, filters), false);
  assert.equal(selectedByCategoryFilters({ engine: "ev", classes: [] }, filters), true);
});

test("empty category filters show all cars and select none", () => {
  const filters = {
    types: new Set(),
    eras: new Set(),
    engines: new Set(),
    classes: new Set(),
  };
  const car = { type: "race", era: "modern", engine: "ice", classes: ["gt3"] };
  assert.equal(hasActiveCategoryFilters(filters), false);
  assert.equal(matchesCategoryFilters(car, filters), true);
  assert.equal(selectedByCategoryFilters(car, filters), false);
});

test("mobile section preferences accept only boolean entries", () => {
  assert.deepEqual(parseMobileSectionState('{"server-info":false,"cars":true,"invalid":"yes"}'), {
    "server-info": false,
    cars: true,
  });
  assert.deepEqual(parseMobileSectionState("invalid json"), {});
  assert.deepEqual(parseMobileSectionState("[]"), {});
});

test("lap times use compact minute formatting", () => {
  assert.equal(formatLapTime(98321), "1:38.321");
  assert.equal(formatLapTime(0), "0:00.000");
  assert.equal(formatLapTime(null), "—");
  assert.equal(formatLapTime(undefined), "—");
  assert.equal(formatLapTime(-1), "—");
});

test("lap deltas are omitted for the fastest and invalid laps", () => {
  assert.equal(formatLapDelta(98321, 97210), "+1.111");
  assert.equal(formatLapDelta(97210, 97210), "");
  assert.equal(formatLapDelta(null, 97210), "");
});
