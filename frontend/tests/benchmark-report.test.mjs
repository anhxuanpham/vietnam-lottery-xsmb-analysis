import assert from "node:assert/strict";
import test from "node:test";

import { backtest } from "../analytics.ts";
import {
  benchmarkReportFilename,
  buildBenchmarkReport,
} from "../benchmark-report.ts";

function draw(offset) {
  const date = new Date(Date.UTC(2026, 0, 1 + offset)).toISOString().slice(0, 10);
  return {
    date,
    stationCode: "TN",
    stationName: "Tây Ninh",
    specialPrize: "123456",
    specialTail: "56",
    numbers: Array.from({ length: 18 }, (_, index) => String((index + offset) % 100).padStart(2, "0")),
    prizes: { special: ["123456"] },
  };
}

const draws = Array.from({ length: 45 }, (_, index) => draw(index));
const options = {
  datasetVersion: "release/2026-07-23",
  region: "xsmn",
  stationCode: "TN",
  stationName: "Tây Ninh",
  selectedWindow: 30,
  modelKinds: ["frequency", "gap", "balanced"],
  windows: [30, 90, 180, 365],
};

function benchmark(kind) {
  return backtest(draws, {
    datasetVersion: options.datasetVersion,
    region: options.region,
    stationCode: options.stationCode,
    kind,
    window: options.selectedWindow,
    evaluationLimit: 10,
    bootstrapSamples: 100,
  });
}

test("benchmark report preserves lineage, fingerprints, search-space disclosure and filename", () => {
  const benchmarks = options.modelKinds.map(benchmark);
  const report = buildBenchmarkReport({ ...options, benchmarks });

  assert.equal(report.schemaVersion, 1);
  assert.equal(report.datasetVersion, options.datasetVersion);
  assert.equal(report.modelVersion, benchmarks[0].modelVersion);
  assert.deepEqual(report.benchmarkFingerprints, benchmarks.map((item) => item.fingerprint));
  assert.equal(report.exploratorySearchSpace.totalConfigurations, 12);
  assert.match(report.exploratorySearchSpace.warning, /exploratory/i);
  assert.match(
    benchmarkReportFilename(report),
    /^lottery-benchmark-xsmn-tn-30d-release-2026-07-23-[a-f0-9-]+\.json$/,
  );
});

test("benchmark report rejects mixed lineage", () => {
  const first = benchmark("frequency");
  const otherDataset = backtest(draws, {
    datasetVersion: "other-release",
    region: options.region,
    stationCode: options.stationCode,
    kind: "gap",
    window: options.selectedWindow,
    evaluationLimit: 10,
    bootstrapSamples: 100,
  });

  assert.throws(
    () => buildBenchmarkReport({ ...options, benchmarks: [first, otherDataset] }),
    /cannot mix/,
  );
});
