import assert from "node:assert/strict";
import test from "node:test";

import {
  MOD_UPLOAD_PROXY_LIMIT_MESSAGE,
  formatLapDelta,
  formatLapTime,
  hasActiveCategoryFilters,
  liveCarDisplayName,
  matchesCategoryFilters,
  matchesPiFilter,
  parseMobileSectionState,
  preferredTrack,
  resumableUploadError,
  selectedByCategoryFilters,
  sortCarsByDisplayName,
  trackIdentity,
  uploadPercent,
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
    mods: false,
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
    mods: false,
  };
  const car = { type: "race", era: "modern", engine: "ice", classes: ["gt3"] };
  assert.equal(hasActiveCategoryFilters(filters), false);
  assert.equal(matchesCategoryFilters(car, filters), true);
  assert.equal(selectedByCategoryFilters(car, filters), false);
});

test("mod category shows only mod cars when used alone", () => {
  const filters = {
    types: new Set(),
    eras: new Set(),
    engines: new Set(),
    classes: new Set(),
    mods: true,
  };
  assert.equal(matchesCategoryFilters({ is_mod: true, classes: [] }, filters), true);
  assert.equal(matchesCategoryFilters({ is_mod: false, classes: [] }, filters), false);
});

test("cars stay alphabetic by readable display name", () => {
  const cars = [
    { display_name: "Volkswagen Golf", internal_name: "vw" },
    { display_name: "Abarth 1000 TCR", internal_name: "abarth" },
    { display_name: "BMW M2", internal_name: "bmw" },
  ];
  assert.deepEqual(
    sortCarsByDisplayName(cars).map((car) => car.internal_name),
    ["abarth", "bmw", "vw"],
  );
});

test("PI filters never hide mods with unknown performance", () => {
  assert.equal(matchesPiFilter({ is_mod: true, pi: null }, 20, 30), true);
  assert.equal(matchesPiFilter({ is_mod: false, pi: 25 }, 20, 30), true);
  assert.equal(matchesPiFilter({ is_mod: false, pi: 40 }, 20, 30), false);
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

test("live cars resolve presets, runtime models, variants, and unknown IDs", () => {
  const cars = [
    { internal_name: "preset_gt2_1", display_name: "KTM X-Bow GT2 - Standard", runtime_name: "ks_ktm_x_bow_gt2" },
    { internal_name: "preset_gt2_2", display_name: "KTM X-Bow GT2 - Endurance", runtime_name: "ks_ktm_x_bow_gt2" },
    { internal_name: "preset_mod_1", display_name: "Abarth 1000 TCR", runtime_name: "tc1000" },
  ];
  assert.equal(liveCarDisplayName(cars, "preset_gt2_1"), "KTM X-Bow GT2 - Standard");
  assert.equal(liveCarDisplayName(cars, "ks_ktm_x_bow_gt2"), "KTM X-Bow GT2");
  assert.equal(liveCarDisplayName(cars, "tc1000"), "Abarth 1000 TCR");
  assert.equal(liveCarDisplayName(cars, "unknown_runtime"), "unknown_runtime");
  assert.equal(liveCarDisplayName(cars, ""), "Unknown car");
});

test("mod upload progress is bounded and proxy errors are actionable", () => {
  assert.equal(uploadPercent(3, 8), 38);
  assert.equal(uploadPercent(9, 8), 100);
  assert.equal(uploadPercent(1, 0), 0);
  assert.equal(resumableUploadError(413, 0, 100), MOD_UPLOAD_PROXY_LIMIT_MESSAGE);
  assert.equal(
    resumableUploadError(504, 30, 100),
    "Upload paused at 30%. Select the same file and click Install to resume.",
  );
  assert.equal(
    resumableUploadError(409, 30, 100, true),
    "Upload paused at 30%. Select the same file and click Install to resume.",
  );
  assert.equal(resumableUploadError(409, 30, 100, false), "");
  assert.equal(resumableUploadError(400, 30, 100), "");
});
