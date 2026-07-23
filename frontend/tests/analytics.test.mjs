import assert from "node:assert/strict";
import test from "node:test";

import {
  ANALYTICS_MODEL_VERSION,
  BASELINE_COVERAGE,
  backtest,
  compareBacktests,
  frequencies,
  pickNumbers,
} from "../analytics.ts";

function isoDate(offset) {
  const value = new Date(Date.UTC(2026, 0, 1 + offset));
  return value.toISOString().slice(0, 10);
}

function draw(offset, number, stationCode = "xsmb", resultCount = 18) {
  const numbers = Array.from({ length: resultCount }, (_, index) =>
    index === 0 ? number : String(index % 100).padStart(2, "0")
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

function config(overrides = {}) {
  return {
    datasetVersion: "release-2026-07-22",
    region: "xsmn",
    stationCode: "TN",
    kind: "frequency",
    window: 30,
    topK: 10,
    evaluationLimit: 10,
    bootstrapSamples: 250,
    ...overrides,
  };
}

test("analytics counts leading-zero numbers and ranks deterministically", () => {
  const draws = [draw(0, "00"), draw(1, "00"), draw(2, "99")];
  const counts = frequencies(draws);
  assert.equal(counts["00"], 2);
  assert.equal(counts["99"], 1);
  assert.deepEqual(pickNumbers(draws, "frequency"), pickNumbers([...draws].reverse(), "frequency"));
  assert.equal(pickNumbers(draws, "frequency", 5).length, 5);
});

test("legacy walk-forward API remains supported and never trains on its evaluation draw", () => {
  const draws = Array.from({ length: 40 }, (_, index) => draw(index, index === 30 ? "99" : "01"));
  const result = backtest(draws, 30, "frequency", 10);

  assert.equal(result.modelVersion, ANALYTICS_MODEL_VERSION);
  assert.equal(result.datasetVersion, "unversioned");
  assert.equal(result.baseline, BASELINE_COVERAGE);
  assert.equal(result.evaluationCount, 10);
  assert.equal(result.series[0].evaluationDate, isoDate(30));
  assert.ok(!result.series[0].picks.includes("99"));
  assert.ok(result.series.every((point) => point.trainingTo < point.evaluationDate));
});

test("configured benchmark carries lineage, ranges, dynamic baseline and deterministic fingerprint", () => {
  const draws = Array.from({ length: 50 }, (_, index) =>
    draw(index, String(index % 100).padStart(2, "0"), "TN")
  );
  const benchmarkConfig = config({
    topK: 5,
    evaluationFrom: isoDate(35),
    evaluationTo: isoDate(44),
  });
  const result = backtest(draws, benchmarkConfig);
  const reversed = backtest([...draws].reverse(), benchmarkConfig);

  assert.equal(result.datasetVersion, benchmarkConfig.datasetVersion);
  assert.equal(result.region, "xsmn");
  assert.equal(result.stationCode, "TN");
  assert.equal(result.kind, "frequency");
  assert.equal(result.window, 30);
  assert.equal(result.topK, 5);
  assert.equal(result.baseline, 0.05);
  assert.deepEqual(result.requestedEvaluationRange, {
    from: isoDate(35),
    to: isoDate(44),
  });
  assert.deepEqual(result.evaluationRange, {
    from: isoDate(35),
    to: isoDate(44),
  });
  assert.equal(result.fingerprint, reversed.fingerprint);
  assert.deepEqual(result.coverageConfidenceInterval, reversed.coverageConfidenceInterval);
  assert.match(result.fingerprint, /^benchmark-v1-[0-9a-f]{16}$/);
  assert.equal(result.hitRate, result.hitCount / result.evaluationCount);
});

test("dataset lineage changes the fingerprint without changing deterministic scores", () => {
  const draws = Array.from({ length: 45 }, (_, index) => draw(index, "01", "TN"));
  const first = backtest(draws, config());
  const second = backtest(draws, config({ datasetVersion: "release-2026-07-23" }));

  assert.notEqual(first.fingerprint, second.fingerprint);
  assert.equal(first.coverage, second.coverage);
  assert.equal(first.hitRate, second.hitRate);
});

test("draw-block bootstrap is deterministic and supports both 18- and 27-result grains", () => {
  const southern = Array.from({ length: 45 }, (_, index) =>
    draw(index, String(index % 20).padStart(2, "0"), "TN", 18)
  );
  const northern = Array.from({ length: 45 }, (_, index) =>
    draw(index, String(index % 30).padStart(2, "0"), "XSMB", 27)
  );
  const first = backtest(southern, config());
  const repeated = backtest(southern, config());
  const xsmb = backtest(northern, config({
    region: "xsmb",
    stationCode: "XSMB",
  }));

  assert.deepEqual(first.coverageConfidenceInterval, repeated.coverageConfidenceInterval);
  assert.equal(first.totalResults, first.evaluationCount * 18);
  assert.equal(xsmb.totalResults, xsmb.evaluationCount * 27);
  assert.ok(first.coverageConfidenceInterval.lower <= first.coverage);
  assert.ok(first.coverageConfidenceInterval.upper >= first.coverage);
});

test("backtest output is stable for the same station history", () => {
  const draws = Array.from({ length: 125 }, (_, index) => draw(index, String(index % 100).padStart(2, "0")));
  assert.deepEqual(backtest(draws, 30, "balanced"), backtest(draws, 30, "balanced"));
});

test("analytics rejects cross-station leakage, station mismatch and duplicate dates", () => {
  assert.throws(
    () => backtest([draw(0, "01", "TN"), draw(1, "02", "AG")], 1, "frequency"),
    /cannot mix draws from different stations/,
  );
  const stationDraws = Array.from({ length: 35 }, (_, index) => draw(index, "01", "TN"));
  assert.throws(
    () => backtest(stationDraws, config({ stationCode: "AG" })),
    /station mismatch/,
  );
  assert.throws(
    () => backtest([...stationDraws, draw(34, "02", "TN")], config()),
    /duplicate station\/date/,
  );
});

test("analytics rejects insufficient history and invalid evaluation ranges", () => {
  const shortHistory = Array.from({ length: 30 }, (_, index) => draw(index, "01", "TN"));
  assert.throws(
    () => backtest(shortHistory, config()),
    /insufficient history/,
  );

  const history = Array.from({ length: 50 }, (_, index) => draw(index, "01", "TN"));
  assert.throws(
    () => backtest(history, config({ evaluationFrom: isoDate(45), evaluationTo: isoDate(40) })),
    /must not be after/,
  );
  assert.throws(
    () => backtest(history, config({ evaluationFrom: "2099-01-01" })),
    /requested evaluation range/,
  );
});

test("paired comparison uses identical evaluation dates and deterministic paired bootstrap", () => {
  const draws = Array.from({ length: 60 }, (_, index) =>
    draw(index, String((index * 7) % 100).padStart(2, "0"), "TN")
  );
  const left = backtest(draws, config({ kind: "frequency", evaluationLimit: 20 }));
  const right = backtest(draws, config({ kind: "gap", evaluationLimit: 20 }));
  const comparison = compareBacktests(left, right, 300);
  const repeated = compareBacktests(left, right, 300);
  const reversed = compareBacktests(right, left, 300);

  assert.deepEqual(comparison, repeated);
  assert.equal(comparison.evaluationCount, 20);
  assert.equal(
    comparison.leftWins + comparison.rightWins + comparison.ties,
    comparison.evaluationCount,
  );
  assert.ok(Math.abs(comparison.meanCoverageDelta - (left.coverage - right.coverage)) < 1e-12);
  assert.ok(Math.abs(comparison.meanCoverageDelta + reversed.meanCoverageDelta) < 1e-12);
  assert.ok(
    Math.abs(comparison.confidenceInterval.lower + reversed.confidenceInterval.upper) < 1e-12,
  );
  assert.ok(
    Math.abs(comparison.confidenceInterval.upper + reversed.confidenceInterval.lower) < 1e-12,
  );
});

test("paired comparison rejects incompatible lineage, topK and evaluation dates", () => {
  const draws = Array.from({ length: 60 }, (_, index) => draw(index, "01", "TN"));
  const base = backtest(draws, config({ kind: "frequency", evaluationLimit: 20 }));
  const otherRelease = backtest(draws, config({
    datasetVersion: "other-release",
    kind: "gap",
    evaluationLimit: 20,
  }));
  const otherTopK = backtest(draws, config({ kind: "gap", topK: 5, evaluationLimit: 20 }));
  const otherDates = backtest(draws, config({ kind: "gap", evaluationLimit: 10 }));

  assert.throws(() => compareBacktests(base, otherRelease), /same dataset and station lineage/);
  assert.throws(() => compareBacktests(base, otherTopK), /same topK/);
  assert.throws(() => compareBacktests(base, otherDates), /identical evaluation dates/);
});
