import assert from "node:assert/strict";
import test from "node:test";

import {
  matchesSelectedClasses,
  parseMobileSectionState,
  preferredTrack,
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

test("class filters use OR within the class group", () => {
  const selected = new Set(["gt3", "gt4"]);
  assert.equal(matchesSelectedClasses({ classes: ["gt3"] }, selected), true);
  assert.equal(matchesSelectedClasses({ classes: ["cup"] }, selected), false);
  assert.equal(matchesSelectedClasses({ classes: [] }, new Set()), true);
});

test("mobile section preferences accept only boolean entries", () => {
  assert.deepEqual(parseMobileSectionState('{"server-info":false,"cars":true,"invalid":"yes"}'), {
    "server-info": false,
    cars: true,
  });
  assert.deepEqual(parseMobileSectionState("invalid json"), {});
  assert.deepEqual(parseMobileSectionState("[]"), {});
});
