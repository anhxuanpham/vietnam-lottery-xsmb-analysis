import type { BacktestResult, ModelKind } from "./analytics.ts";
import type { LotteryRegion } from "./lottery-contract.ts";

export type BenchmarkReport = {
  schemaVersion: 1;
  reportType: "lottery-benchmark-integrity";
  reportId: string;
  datasetVersion: string;
  modelVersion: string;
  region: LotteryRegion;
  station: {
    code: string;
    name: string;
  };
  selectedWindow: number;
  benchmarkFingerprints: string[];
  exploratorySearchSpace: {
    modelKinds: ModelKind[];
    windows: number[];
    totalConfigurations: number;
    warning: string;
  };
  benchmarks: BacktestResult[];
};

type BuildBenchmarkReportOptions = {
  datasetVersion: string;
  region: LotteryRegion;
  stationCode: string;
  stationName: string;
  selectedWindow: number;
  modelKinds: readonly ModelKind[];
  windows: readonly number[];
  benchmarks: BacktestResult[];
};

function requireReportLineage(
  benchmark: BacktestResult,
  options: BuildBenchmarkReportOptions,
): void {
  if (
    benchmark.datasetVersion !== options.datasetVersion ||
    benchmark.region !== options.region ||
    benchmark.stationCode !== options.stationCode ||
    benchmark.window !== options.selectedWindow
  ) {
    throw new Error("benchmark report cannot mix dataset, region, station, or window lineage");
  }
}

function reportId(fingerprints: string[]): string {
  return fingerprints
    .map((fingerprint) => fingerprint.replace(/^benchmark-v1-/, "").slice(0, 8))
    .join("-");
}

export function buildBenchmarkReport(
  options: BuildBenchmarkReportOptions,
): BenchmarkReport {
  if (options.benchmarks.length === 0) {
    throw new Error("benchmark report requires at least one benchmark");
  }
  options.benchmarks.forEach((benchmark) => requireReportLineage(benchmark, options));
  const fingerprints = options.benchmarks.map((benchmark) => benchmark.fingerprint);

  return {
    schemaVersion: 1,
    reportType: "lottery-benchmark-integrity",
    reportId: reportId(fingerprints),
    datasetVersion: options.datasetVersion,
    modelVersion: options.benchmarks[0].modelVersion,
    region: options.region,
    station: {
      code: options.stationCode,
      name: options.stationName,
    },
    selectedWindow: options.selectedWindow,
    benchmarkFingerprints: fingerprints,
    exploratorySearchSpace: {
      modelKinds: [...options.modelKinds],
      windows: [...options.windows],
      totalConfigurations: options.modelKinds.length * options.windows.length,
      warning: "Exploratory comparison only; repeated model/window selection can overstate apparent performance.",
    },
    benchmarks: options.benchmarks,
  };
}

function safeFilenamePart(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

export function benchmarkReportFilename(report: BenchmarkReport): string {
  return [
    "lottery-benchmark",
    safeFilenamePart(report.region),
    safeFilenamePart(report.station.code),
    `${report.selectedWindow}d`,
    safeFilenamePart(report.datasetVersion),
    safeFilenamePart(report.reportId),
  ].join("-") + ".json";
}
