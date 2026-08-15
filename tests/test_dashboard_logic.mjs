import assert from "node:assert/strict";
import test from "node:test";

import {
  MOD_UPLOAD_PROXY_LIMIT_MESSAGE,
  carHasCategory,
  carPerformanceLabel,
  categoryFilterDefaults,
  deselectCarsInCategory,
  formatLapDelta,
  formatLapTime,
  liveCarDisplayName,
  matchesCarSearch,
  matchesCategoryFilters,
  matchesPiFilter,
  parseMobileSectionState,
  preferredCarCategory,
  preferredTrack,
  resumableUploadError,
  setVisibleCarSelection,
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

test("all car categories are enabled by default", () => {
  const categories = {
    type: [{ value: "road" }, { value: "race" }],
    era: [{ value: "modern" }, { value: "vintage" }],
    engine: [{ value: "ice" }, { value: "ev" }],
    class: [{ value: "gt3" }, { value: "gt4" }],
  };
  const filters = categoryFilterDefaults(categories, [{ is_mod: false }, { is_mod: true }]);
  assert.deepEqual([...filters.types], ["road", "race"]);
  assert.deepEqual([...filters.eras], ["modern", "vintage"]);
  assert.deepEqual([...filters.engines], ["ice", "ev"]);
  assert.deepEqual([...filters.classes], ["gt3", "gt4"]);
  assert.equal(filters.mods, true);
});

test("car search matches readable, internal, and runtime names", () => {
  const car = {
    display_name: "Abarth 1000 TCR",
    internal_name: "preset_modded_car_mech_1",
    runtime_name: "abarth_1000_tcr",
  };
  assert.equal(matchesCarSearch(car, "  abarth  "), true);
  assert.equal(matchesCarSearch(car, "MODDED_CAR"), true);
  assert.equal(matchesCarSearch(car, "1000_tcr"), true);
  assert.equal(matchesCarSearch(car, "GT4"), false);
  assert.equal(matchesCarSearch(car, ""), true);
});

test("categories are additive across the original groups", () => {
  const filters = {
    types: new Set(["road"]),
    eras: new Set(),
    engines: new Set(),
    classes: new Set(["gt4"]),
    mods: true,
  };
  assert.equal(matchesCategoryFilters({ type: "road", classes: [] }, filters), true);
  assert.equal(matchesCategoryFilters({ type: "race", classes: ["gt4"] }, filters), true);
  assert.equal(matchesCategoryFilters({ type: "race", classes: ["gt3"] }, filters), false);
  assert.equal(matchesCategoryFilters({ is_mod: true, classes: [] }, filters), true);
});

test("categories recover immediately after all were unchecked", () => {
  const filters = {
    types: new Set(),
    eras: new Set(),
    engines: new Set(),
    classes: new Set(),
    mods: false,
  };
  const gt3 = { type: "race", era: "modern", engine: "ice", classes: ["gt3"] };
  const road = { type: "road", era: "modern", engine: "ice", classes: [] };
  assert.equal(matchesCategoryFilters(gt3, filters), false);
  assert.equal(matchesCategoryFilters(road, filters), false);

  filters.types.add("road");
  assert.equal(matchesCategoryFilters(gt3, filters), false);
  assert.equal(matchesCategoryFilters(road, filters), true);

  filters.classes.add("gt3");
  assert.equal(matchesCategoryFilters(gt3, filters), true);
});

test("mod visibility is controlled only by the mod category", () => {
  const filters = {
    types: new Set(),
    eras: new Set(),
    engines: new Set(),
    classes: new Set(),
    mods: true,
  };
  assert.equal(matchesCategoryFilters({ is_mod: true, classes: [] }, filters), true);
  filters.mods = false;
  assert.equal(matchesCategoryFilters({ is_mod: true, classes: [] }, filters), false);
});

test("hiding a category clears only its selected cars", () => {
  const cars = [
    { internal_name: "gt3", type: "race", classes: ["gt3"] },
    { internal_name: "gt4", type: "race", classes: ["gt4"] },
    { internal_name: "road", type: "road", classes: [] },
  ];
  const states = new Map(cars.map((car) => [car.internal_name, { is_selected: true }]));

  assert.equal(carHasCategory(cars[0], "class", "gt3"), true);
  assert.equal(carHasCategory(cars[1], "class", "gt3"), false);
  assert.equal(deselectCarsInCategory(cars, states, "class", "gt3"), 1);
  assert.equal(states.get("gt3").is_selected, false);
  assert.equal(states.get("gt4").is_selected, true);
  assert.equal(states.get("road").is_selected, true);

  assert.equal(deselectCarsInCategory(cars, states, "type", "race"), 1);
  assert.equal(states.get("gt4").is_selected, false);
  assert.equal(states.get("road").is_selected, true);
});

test("search selections prefer mod, class, then type categories", () => {
  assert.deepEqual(preferredCarCategory({ is_mod: true, classes: ["gt4"], type: "race" }), {
    filter: "mods",
    value: "mod",
  });
  assert.deepEqual(preferredCarCategory({ classes: ["gt4"], type: "race" }), {
    filter: "classes",
    value: "gt4",
  });
  assert.deepEqual(preferredCarCategory({ classes: [], type: "road" }), {
    filter: "types",
    value: "road",
  });
});

test("car list metadata rounds performance and shows one useful category", () => {
  const categories = {
    type: [
      { value: "road", label: "Road" },
      { value: "race", label: "Race" },
    ],
    class: [{ value: "gt2", label: "GT2" }],
  };
  assert.equal(carPerformanceLabel({ pi: 17.9641457, type: "race", classes: ["gt2"] }, categories), "Pi 18.0 · GT2");
  assert.equal(carPerformanceLabel({ pi: 10.178277, type: "road", classes: [] }, categories), "Pi 10.2 · Road");
  assert.equal(carPerformanceLabel({ pi: 20, type: "race", classes: ["gt1"] }, categories), "Pi 20.0 · GT1");
  assert.equal(
    carPerformanceLabel({ is_mod: true, internal_name: "preset_gt3rs_mech_1" }, categories),
    "preset_gt3rs_mech_1",
  );
});

test("bulk selection changes visible cars only", () => {
  const visible = [{ internal_name: "road" }, { internal_name: "f1" }];
  const states = new Map([
    ["road", { is_selected: true }],
    ["f1", { is_selected: false }],
    ["mod", { is_selected: false }],
  ]);

  setVisibleCarSelection(visible, states, true);
  assert.equal(states.get("road").is_selected, true);
  assert.equal(states.get("f1").is_selected, true);
  assert.equal(states.get("mod").is_selected, false);

  setVisibleCarSelection(visible, states, false);
  assert.equal(states.get("road").is_selected, false);
  assert.equal(states.get("f1").is_selected, false);
  assert.equal(states.get("mod").is_selected, false);
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
