import assert from "node:assert/strict";
import test from "node:test";

import {
  INITIAL_EXPLORER_STATE,
  beginExplorerRequest,
  completeExplorerRequest,
  explorerQueryError,
  failExplorerRequest,
} from "../explorer-state.ts";

const query = {
  region: "xsmb",
  station: "xsmb",
  from: "2026-01-01",
  to: "2026-07-21",
  number: "07",
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
