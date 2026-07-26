import assert from "node:assert/strict";
import test from "node:test";

import {
  INITIAL_EXPLORER_STATE,
  beginExplorerRequest,
  completeExplorerRequest,
  explorerQueryError,
  failExplorerRequest,
  normalizeExplorerQuery,
  sameExplorerQuery,
} from "../explorer-state.ts";

const query = {
  region: "xsmb",
  station: "xsmb",
  from: "2026-01-01",
  to: "2026-07-21",
  number: "07",
  value: null,
  match: null,
  prizeGroup: null,
};

function draw(date, specialPrize = "12345") {
  return {
    date,
    stationCode: "xsmb",
    stationName: "Miền Bắc",
    specialPrize,
    specialTail: specialPrize.slice(-2),
    numbers: [],
    prizes: { special: [specialPrize] },
  };
}

test("first Explorer page replaces state and a later page appends without duplicates", () => {
  const firstStarted = beginExplorerRequest(INITIAL_EXPLORER_STATE, query, false);
  const first = completeExplorerRequest(
    firstStarted,
    query,
    [draw("2026-07-21"), draw("2026-07-20")],
    "cursor-2",
    false,
  );
  assert.equal(first.status, "ready");
  assert.deepEqual(first.items.map((item) => item.date), ["2026-07-21", "2026-07-20"]);

  const appendStarted = beginExplorerRequest(first, query, true);
  assert.equal(appendStarted.appending, true);
  assert.equal(appendStarted.items.length, 2);
  const appended = completeExplorerRequest(
    appendStarted,
    query,
    [draw("2026-07-20"), draw("2026-07-19")],
    null,
    true,
  );
  assert.equal(appended.status, "ready");
  assert.equal(appended.cursor, null);
  assert.deepEqual(
    appended.items.map((item) => item.date),
    ["2026-07-21", "2026-07-20", "2026-07-19"],
  );
});

test("a changed query cannot append to or highlight results from the previous query", () => {
  const ready = completeExplorerRequest(
    beginExplorerRequest(INITIAL_EXPLORER_STATE, query, false),
    query,
    [draw("2026-07-21")],
    "old-cursor",
    false,
  );
  const changedQuery = { ...query, number: "08" };
  const changed = beginExplorerRequest(ready, changedQuery, true);
  assert.deepEqual(changed.items, []);
  assert.equal(changed.cursor, null);
  assert.equal(changed.appending, false);
  assert.deepEqual(changed.appliedQuery, changedQuery);
  const staleCompletion = completeExplorerRequest(
    changed,
    query,
    [draw("2026-07-18")],
    "stale-cursor",
    false,
  );
  assert.strictEqual(staleCompletion, changed);
});

test("idle, successful-empty, and error are distinct Explorer states", () => {
  assert.equal(INITIAL_EXPLORER_STATE.status, "idle");
  const started = beginExplorerRequest(INITIAL_EXPLORER_STATE, query, false);
  const empty = completeExplorerRequest(started, query, [], null, false);
  assert.equal(empty.status, "empty");
  assert.deepEqual(empty.items, []);

  const failed = failExplorerRequest(
    beginExplorerRequest(empty, query, false),
    query,
    "Dữ liệu vừa được cập nhật.",
  );
  assert.equal(failed.status, "error");
  assert.equal(failed.error, "Dữ liệu vừa được cập nhật.");
  assert.equal(failed.cursor, null);
});

test("Explorer query validation rejects reversed dates and partial numbers", () => {
  assert.equal(explorerQueryError(query), null);
  assert.match(
    explorerQueryError({ ...query, from: "2026-07-22", to: "2026-07-21" }),
    /ngày/i,
  );
  assert.match(explorerQueryError({ ...query, from: "2026-02-30" }), /ngày/i);
  assert.match(explorerQueryError({ ...query, number: "7" }), /hai chữ số/i);
});

test("Explorer state snapshots every full-prize filter and canonicalizes default exact matching", () => {
  const exact = {
    ...query,
    number: null,
    value: "005113",
    match: "exact",
    prizeGroup: "special",
  };
  const implicitExact = { ...exact };
  delete implicitExact.match;
  assert.equal(sameExplorerQuery(exact, implicitExact), true);
  assert.equal(normalizeExplorerQuery(implicitExact).value, "005113");
  assert.equal(normalizeExplorerQuery(implicitExact).match, "exact");
  assert.equal(sameExplorerQuery(exact, { ...exact, match: "suffix" }), false);
  assert.equal(sameExplorerQuery(exact, { ...exact, prizeGroup: "prize1" }), false);

  const ready = completeExplorerRequest(
    beginExplorerRequest(INITIAL_EXPLORER_STATE, exact, false),
    exact,
    [draw("2026-07-21", "005113")],
    "full-prize-cursor",
    false,
  );
  const changed = beginExplorerRequest(ready, { ...exact, value: "5113" }, true);
  assert.deepEqual(changed.items, []);
  assert.equal(changed.cursor, null);
  assert.equal(changed.appending, false);
});

test("Explorer validates full-prize filters without normalizing away leading zeros", () => {
  const fullPrize = {
    ...query,
    number: null,
    value: "005113",
    match: "exact",
    prizeGroup: "special",
  };
  assert.equal(explorerQueryError(fullPrize), null);
  assert.equal(normalizeExplorerQuery(fullPrize).value, "005113");
  assert.equal(explorerQueryError({ ...fullPrize, value: "7" }), null);
  assert.match(explorerQueryError({ ...fullPrize, value: "１２" }), /ASCII/i);
  assert.match(explorerQueryError({ ...fullPrize, value: "1234567" }), /sáu/i);
  assert.match(explorerQueryError({ ...fullPrize, match: "contains" }), /so khớp/i);
  assert.match(explorerQueryError({ ...fullPrize, prizeGroup: "prize8" }), /nhóm giải/i);
  assert.match(explorerQueryError({ ...fullPrize, number: "13" }), /đồng thời/i);
  assert.match(
    explorerQueryError({ ...query, number: null, value: null, match: "suffix" }),
    /khi có giá trị/i,
  );
});
