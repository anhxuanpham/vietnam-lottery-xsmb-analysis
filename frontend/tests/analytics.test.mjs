import assert from "node:assert/strict";
import test from "node:test";

import {
  ANALYTICS_MODEL_VERSION,
  BASELINE_COVERAGE,
  backtest,
  frequencies,
  pickNumbers,
} from "../analytics.ts";

function isoDate(offset) {
  const value = new Date(Date.UTC(2026, 0, 1 + offset));
  return value.toISOString().slice(0, 10);
}

function draw(offset, number, stationCode = "xsmb") {
  const numbers = Array.from({ length: 18 }, (_, index) =>
    index === 0 ? number : String(index).padStart(2, "0")
  );
  return {
    date: isoDate(offset),
    stationCode,
    stationName: stationCode,
    specialPrize: `1234${number}`,
    specialTail: number,
    numbers,
    prizes: { special: [`1234${number}`] },
  };
}

test("analytics counts leading-zero numbers and ranks deterministically", () => {
  const draws = [draw(0, "00"), draw(1, "00"), draw(2, "99")];
  const counts = frequencies(draws);
  assert.equal(counts["00"], 2);
  assert.equal(counts["99"], 1);
  assert.deepEqual(pickNumbers(draws, "frequency"), pickNumbers([...draws].reverse(), "frequency"));
});

test("walk-forward backtest never trains on its evaluation draw", () => {
  const draws = Array.from({ length: 40 }, (_, index) => draw(index, index === 30 ? "99" : "01"));
  const result = backtest(draws, 30, "frequency", 10);

  assert.equal(result.modelVersion, ANALYTICS_MODEL_VERSION);
  assert.equal(result.baseline, BASELINE_COVERAGE);
  assert.equal(result.evaluationCount, 10);
  assert.equal(result.series[0].evaluationDate, isoDate(30));
  assert.ok(!result.series[0].picks.includes("99"));
  assert.ok(result.series.every((point) => point.trainingTo < point.evaluationDate));
});

test("backtest output is stable for the same station history", () => {
  const draws = Array.from({ length: 125 }, (_, index) => draw(index, String(index % 100).padStart(2, "0")));
  assert.deepEqual(backtest(draws, 30, "balanced"), backtest(draws, 30, "balanced"));
});

test("analytics rejects cross-station leakage", () => {
  assert.throws(
    () => backtest([draw(0, "01", "TN"), draw(1, "02", "AG")], 1, "frequency"),
    /cannot mix draws from different stations/,
  );
});
