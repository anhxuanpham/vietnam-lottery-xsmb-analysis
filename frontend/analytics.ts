import {
  LOTTERY_REGIONS,
  type LotteryDraw,
  type LotteryRegion,
} from "./lottery-contract.ts";

export const ANALYTICS_MODEL_VERSION = "heuristic-v2.1.0";
export const DEFAULT_TOP_K = 10;
export const DEFAULT_EVALUATION_LIMIT = 90;
export const DEFAULT_BOOTSTRAP_SAMPLES = 2_000;
export const BASELINE_COVERAGE = DEFAULT_TOP_K / 100;
export const MODEL_KINDS = ["frequency", "gap", "balanced"] as const;

export type ModelKind = (typeof MODEL_KINDS)[number];
export type FrequencyMap = Record<string, number>;
export type AnalyticsRegion = LotteryRegion | "unknown";

export type DateRange = {
  from: string;
  to: string;
};

export type RequestedDateRange = {
  from: string | null;
  to: string | null;
};

export type BootstrapConfidenceInterval = {
  confidenceLevel: 0.95;
  method: "deterministic-draw-bootstrap";
  samples: number;
  lower: number;
  upper: number;
};

export type BacktestConfig = {
  datasetVersion: string;
  region: LotteryRegion;
  stationCode: string;
  kind: ModelKind;
  window: number;
  topK?: number;
  evaluationLimit?: number;
  evaluationFrom?: string;
  evaluationTo?: string;
  bootstrapSamples?: number;
};

export type BacktestPoint = {
  evaluationDate: string;
  trainingFrom: string;
  trainingTo: string;
  picks: string[];
  coveredResults: number;
  totalResults: number;
  coverage: number;
};

export type BacktestResult = {
  modelVersion: string;
  fingerprint: string;
  datasetVersion: string;
  region: AnalyticsRegion;
  stationCode: string;
  kind: ModelKind;
  window: number;
  topK: number;
  evaluationLimit: number;
  requestedEvaluationRange: RequestedDateRange;
  trainingRange: DateRange;
  evaluationRange: DateRange;
  baseline: number;
  evaluationCount: number;
  coveredResults: number;
  totalResults: number;
  coverage: number;
  coverageConfidenceInterval: BootstrapConfidenceInterval;
  hitCount: number;
  hitRate: number;
  lift: number;
  series: BacktestPoint[];
};

export type PairedModelComparison = {
  fingerprint: string;
  leftFingerprint: string;
  rightFingerprint: string;
  datasetVersion: string;
  region: AnalyticsRegion;
  stationCode: string;
  evaluationRange: DateRange;
  evaluationCount: number;
  meanCoverageDelta: number;
  confidenceInterval: BootstrapConfidenceInterval;
  leftWins: number;
  rightWins: number;
  ties: number;
};

type NormalizedBacktestConfig = {
  datasetVersion: string;
  region: AnalyticsRegion;
  stationCode: string;
  kind: ModelKind;
  window: number;
  topK: number;
  evaluationLimit: number;
  evaluationFrom: string | null;
  evaluationTo: string | null;
  bootstrapSamples: number;
};

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function emptyFrequencyMap(): FrequencyMap {
  return Object.fromEntries(
    Array.from({ length: 100 }, (_, index) => [String(index).padStart(2, "0"), 0]),
  ) as FrequencyMap;
}

function chronological(draws: LotteryDraw[]): LotteryDraw[] {
  return [...draws].sort(
    (left, right) => left.date.localeCompare(right.date) || left.stationCode.localeCompare(right.stationCode),
  );
}

function requireOneStation(draws: LotteryDraw[]): string {
  const stations = new Set(draws.map((draw) => draw.stationCode));
  if (stations.size > 1) throw new Error("analytics cannot mix draws from different stations");
  return stations.values().next().value ?? "";
}

function requireUniqueDates(draws: LotteryDraw[]): void {
  const dates = new Set<string>();
  for (const draw of draws) {
    if (dates.has(draw.date)) {
      throw new Error(`analytics cannot contain duplicate station/date draws: ${draw.date}`);
    }
    dates.add(draw.date);
  }
}

function requirePositiveInteger(value: number, name: string): void {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer`);
  }
}

function requireIsoDate(value: string, name: string): void {
  if (!DATE_PATTERN.test(value)) throw new Error(`${name} must be an ISO date`);
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new Error(`${name} must be an ISO date`);
  }
}

function normalizeConfig(
  draws: LotteryDraw[],
  configOrWindow: BacktestConfig | number,
  legacyKind?: ModelKind,
  legacyEvaluationLimit = DEFAULT_EVALUATION_LIMIT,
): NormalizedBacktestConfig {
  const observedStation = requireOneStation(draws);
  if (typeof configOrWindow === "number") {
    if (!legacyKind || !MODEL_KINDS.includes(legacyKind)) {
      throw new Error("kind must be a supported analytics model");
    }
    return {
      datasetVersion: "unversioned",
      region: "unknown",
      stationCode: observedStation || "unknown",
      kind: legacyKind,
      window: configOrWindow,
      topK: DEFAULT_TOP_K,
      evaluationLimit: legacyEvaluationLimit,
      evaluationFrom: null,
      evaluationTo: null,
      bootstrapSamples: DEFAULT_BOOTSTRAP_SAMPLES,
    };
  }

  if (!configOrWindow.datasetVersion.trim()) throw new Error("datasetVersion must not be empty");
  if (!LOTTERY_REGIONS.includes(configOrWindow.region)) throw new Error("region must be a supported lottery region");
  if (!configOrWindow.stationCode.trim()) throw new Error("stationCode must not be empty");
  if (observedStation && observedStation !== configOrWindow.stationCode) {
    throw new Error(
      `analytics station mismatch: expected ${configOrWindow.stationCode}, received ${observedStation}`,
    );
  }
  if (!MODEL_KINDS.includes(configOrWindow.kind)) {
    throw new Error("kind must be a supported analytics model");
  }

  const evaluationFrom = configOrWindow.evaluationFrom ?? null;
  const evaluationTo = configOrWindow.evaluationTo ?? null;
  if (evaluationFrom) requireIsoDate(evaluationFrom, "evaluationFrom");
  if (evaluationTo) requireIsoDate(evaluationTo, "evaluationTo");
  if (evaluationFrom && evaluationTo && evaluationFrom > evaluationTo) {
    throw new Error("evaluationFrom must not be after evaluationTo");
  }

  return {
    datasetVersion: configOrWindow.datasetVersion,
    region: configOrWindow.region,
    stationCode: configOrWindow.stationCode,
    kind: configOrWindow.kind,
    window: configOrWindow.window,
    topK: configOrWindow.topK ?? DEFAULT_TOP_K,
    evaluationLimit: configOrWindow.evaluationLimit ?? DEFAULT_EVALUATION_LIMIT,
    evaluationFrom,
    evaluationTo,
    bootstrapSamples: configOrWindow.bootstrapSamples ?? DEFAULT_BOOTSTRAP_SAMPLES,
  };
}

function validateConfig(config: NormalizedBacktestConfig): void {
  requirePositiveInteger(config.window, "window");
  requirePositiveInteger(config.topK, "topK");
  if (config.topK > 100) throw new Error("topK must not exceed 100");
  requirePositiveInteger(config.evaluationLimit, "evaluationLimit");
  requirePositiveInteger(config.bootstrapSamples, "bootstrapSamples");
}

function hash32(value: string, seed: number): number {
  let hash = seed >>> 0;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function deterministicFingerprint(value: unknown): string {
  const serialized = JSON.stringify(value);
  const left = hash32(serialized, 0x811c9dc5).toString(16).padStart(8, "0");
  const right = hash32(serialized, 0x9e3779b9).toString(16).padStart(8, "0");
  return `benchmark-v1-${left}${right}`;
}

function seedFromFingerprint(fingerprint: string): number {
  return hash32(fingerprint, 0xa5a5a5a5) || 0x6d2b79f5;
}

function deterministicRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

function percentile(sorted: number[], probability: number): number {
  const index = probability * (sorted.length - 1);
  const lowerIndex = Math.floor(index);
  const upperIndex = Math.ceil(index);
  if (lowerIndex === upperIndex) return sorted[lowerIndex];
  const weight = index - lowerIndex;
  return sorted[lowerIndex] * (1 - weight) + sorted[upperIndex] * weight;
}

function bootstrapInterval(
  observations: ReadonlyArray<{ numerator: number; denominator: number }>,
  samples: number,
  fingerprint: string,
): BootstrapConfidenceInterval {
  if (observations.length === 0) throw new Error("cannot bootstrap an empty evaluation series");
  const random = deterministicRandom(seedFromFingerprint(fingerprint));
  const estimates: number[] = [];
  for (let sample = 0; sample < samples; sample += 1) {
    let numerator = 0;
    let denominator = 0;
    for (let draw = 0; draw < observations.length; draw += 1) {
      const selected = observations[Math.floor(random() * observations.length)];
      numerator += selected.numerator;
      denominator += selected.denominator;
    }
    estimates.push(denominator > 0 ? numerator / denominator : 0);
  }
  estimates.sort((left, right) => left - right);
  return {
    confidenceLevel: 0.95,
    method: "deterministic-draw-bootstrap",
    samples,
    lower: percentile(estimates, 0.025),
    upper: percentile(estimates, 0.975),
  };
}

export function frequencies(draws: LotteryDraw[]): FrequencyMap {
  const counts = emptyFrequencyMap();
  for (const draw of draws) {
    for (const number of draw.numbers) counts[number] += 1;
  }
  return counts;
}

export function gaps(draws: LotteryDraw[]): FrequencyMap {
  const ordered = chronological(draws);
  const latestIndex = ordered.length - 1;
  const lastSeen = Object.fromEntries(
    Array.from({ length: 100 }, (_, index) => [String(index).padStart(2, "0"), -1]),
  ) as FrequencyMap;

  ordered.forEach((draw, index) => {
    for (const number of new Set(draw.numbers)) lastSeen[number] = index;
  });
  return Object.fromEntries(
    Object.entries(lastSeen).map(([number, index]) => [
      number,
      index < 0 ? ordered.length : latestIndex - index,
    ]),
  ) as FrequencyMap;
}

export function pickNumbers(
  draws: LotteryDraw[],
  kind: ModelKind,
  topK = DEFAULT_TOP_K,
): string[] {
  requirePositiveInteger(topK, "topK");
  if (topK > 100) throw new Error("topK must not exceed 100");
  if (draws.length === 0) return [];
  requireOneStation(draws);
  const counts = frequencies(draws);
  const drawGaps = gaps(draws);
  const maxFrequency = Math.max(...Object.values(counts), 1);
  const maxGap = Math.max(...Object.values(drawGaps), 1);

  return Object.keys(counts)
    .map((number) => {
      const frequencyScore = counts[number] / maxFrequency;
      const gapScore = drawGaps[number] / maxGap;
      const score = kind === "frequency"
        ? frequencyScore
        : kind === "gap"
          ? gapScore
          : frequencyScore * 0.6 + gapScore * 0.4;
      return { number, score, frequency: counts[number] };
    })
    .sort((left, right) =>
      right.score - left.score || right.frequency - left.frequency || left.number.localeCompare(right.number)
    )
    .slice(0, topK)
    .map((item) => item.number);
}

export function backtest(
  draws: LotteryDraw[],
  config: BacktestConfig,
): BacktestResult;
export function backtest(
  draws: LotteryDraw[],
  window: number,
  kind: ModelKind,
  evaluationLimit?: number,
): BacktestResult;
export function backtest(
  draws: LotteryDraw[],
  configOrWindow: BacktestConfig | number,
  legacyKind?: ModelKind,
  legacyEvaluationLimit = DEFAULT_EVALUATION_LIMIT,
): BacktestResult {
  const config = normalizeConfig(draws, configOrWindow, legacyKind, legacyEvaluationLimit);
  validateConfig(config);
  requireUniqueDates(draws);
  const ordered = chronological(draws);
  if (ordered.length <= config.window) {
    throw new Error(
      `insufficient history: need more than ${config.window} draws, received ${ordered.length}`,
    );
  }

  const eligibleIndexes = ordered
    .map((draw, index) => ({ date: draw.date, index }))
    .filter(({ date, index }) =>
      index >= config.window &&
      (!config.evaluationFrom || date >= config.evaluationFrom) &&
      (!config.evaluationTo || date <= config.evaluationTo)
    )
    .slice(-config.evaluationLimit)
    .map(({ index }) => index);
  if (eligibleIndexes.length === 0) {
    throw new Error("insufficient history for the requested evaluation range");
  }

  const series: BacktestPoint[] = [];
  for (const index of eligibleIndexes) {
    const training = ordered.slice(index - config.window, index);
    const picks = pickNumbers(training, config.kind, config.topK);
    const pickSet = new Set(picks);
    const coveredResults = ordered[index].numbers.filter((number) => pickSet.has(number)).length;
    series.push({
      evaluationDate: ordered[index].date,
      trainingFrom: training[0].date,
      trainingTo: training.at(-1)?.date ?? training[0].date,
      picks,
      coveredResults,
      totalResults: ordered[index].numbers.length,
      coverage: coveredResults / ordered[index].numbers.length,
    });
  }

  const coveredResults = series.reduce((total, point) => total + point.coveredResults, 0);
  const totalResults = series.reduce((total, point) => total + point.totalResults, 0);
  const coverage = totalResults > 0 ? coveredResults / totalResults : 0;
  const hitCount = series.filter((point) => point.coveredResults > 0).length;
  const fingerprint = deterministicFingerprint({
    modelVersion: ANALYTICS_MODEL_VERSION,
    datasetVersion: config.datasetVersion,
    region: config.region,
    stationCode: config.stationCode,
    kind: config.kind,
    window: config.window,
    topK: config.topK,
    evaluationLimit: config.evaluationLimit,
    requestedEvaluationRange: {
      from: config.evaluationFrom,
      to: config.evaluationTo,
    },
    bootstrapSamples: config.bootstrapSamples,
    series,
  });

  return {
    modelVersion: ANALYTICS_MODEL_VERSION,
    fingerprint,
    datasetVersion: config.datasetVersion,
    region: config.region,
    stationCode: config.stationCode,
    kind: config.kind,
    window: config.window,
    topK: config.topK,
    evaluationLimit: config.evaluationLimit,
    requestedEvaluationRange: {
      from: config.evaluationFrom,
      to: config.evaluationTo,
    },
    trainingRange: {
      from: series[0].trainingFrom,
      to: series.at(-1)?.trainingTo ?? series[0].trainingTo,
    },
    evaluationRange: {
      from: series[0].evaluationDate,
      to: series.at(-1)?.evaluationDate ?? series[0].evaluationDate,
    },
    baseline: config.topK / 100,
    evaluationCount: series.length,
    coveredResults,
    totalResults,
    coverage,
    coverageConfidenceInterval: bootstrapInterval(
      series.map((point) => ({
        numerator: point.coveredResults,
        denominator: point.totalResults,
      })),
      config.bootstrapSamples,
      fingerprint,
    ),
    hitCount,
    hitRate: hitCount / series.length,
    lift: coverage / (config.topK / 100),
    series,
  };
}

function requireComparable(left: BacktestResult, right: BacktestResult): void {
  if (
    left.datasetVersion !== right.datasetVersion ||
    left.region !== right.region ||
    left.stationCode !== right.stationCode
  ) {
    throw new Error("paired backtests must use the same dataset and station lineage");
  }
  if (left.topK !== right.topK) {
    throw new Error("paired backtests must use the same topK");
  }
  if (
    left.series.length !== right.series.length ||
    left.series.some((point, index) => point.evaluationDate !== right.series[index]?.evaluationDate)
  ) {
    throw new Error("paired backtests must use identical evaluation dates");
  }
}

export function compareBacktests(
  left: BacktestResult,
  right: BacktestResult,
  bootstrapSamples = DEFAULT_BOOTSTRAP_SAMPLES,
): PairedModelComparison {
  requirePositiveInteger(bootstrapSamples, "bootstrapSamples");
  requireComparable(left, right);
  const deltas = left.series.map((point, index) => point.coverage - right.series[index].coverage);
  const meanCoverageDelta = deltas.reduce((total, delta) => total + delta, 0) / deltas.length;
  const seedFingerprint = deterministicFingerprint({
    comparison: [...[left.fingerprint, right.fingerprint]].sort(),
    evaluationDates: left.series.map((point) => point.evaluationDate),
    bootstrapSamples,
  });
  const confidenceInterval = bootstrapInterval(
    deltas.map((delta) => ({ numerator: delta, denominator: 1 })),
    bootstrapSamples,
    seedFingerprint,
  );

  return {
    fingerprint: deterministicFingerprint({
      left: left.fingerprint,
      right: right.fingerprint,
      bootstrapSamples,
    }),
    leftFingerprint: left.fingerprint,
    rightFingerprint: right.fingerprint,
    datasetVersion: left.datasetVersion,
    region: left.region,
    stationCode: left.stationCode,
    evaluationRange: left.evaluationRange,
    evaluationCount: deltas.length,
    meanCoverageDelta,
    confidenceInterval,
    leftWins: deltas.filter((delta) => delta > 0).length,
    rightWins: deltas.filter((delta) => delta < 0).length,
    ties: deltas.filter((delta) => delta === 0).length,
  };
}
