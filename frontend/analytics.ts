import type { LotteryDraw } from "./lottery-contract";

export const ANALYTICS_MODEL_VERSION = "heuristic-v2.0.0";
export const BASELINE_COVERAGE = 0.1;
export const MODEL_KINDS = ["frequency", "gap", "balanced"] as const;

export type ModelKind = (typeof MODEL_KINDS)[number];
export type FrequencyMap = Record<string, number>;

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
  baseline: number;
  evaluationCount: number;
  coveredResults: number;
  totalResults: number;
  coverage: number;
  lift: number;
  series: BacktestPoint[];
};

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

function requireOneStation(draws: LotteryDraw[]): void {
  const stations = new Set(draws.map((draw) => draw.stationCode));
  if (stations.size > 1) throw new Error("analytics cannot mix draws from different stations");
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

export function pickNumbers(draws: LotteryDraw[], kind: ModelKind): string[] {
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
    .slice(0, 10)
    .map((item) => item.number);
}

export function backtest(
  draws: LotteryDraw[],
  window: number,
  kind: ModelKind,
  evaluationLimit = 90,
): BacktestResult {
  if (!Number.isSafeInteger(window) || window < 1) throw new Error("window must be a positive integer");
  if (!Number.isSafeInteger(evaluationLimit) || evaluationLimit < 1) {
    throw new Error("evaluationLimit must be a positive integer");
  }
  requireOneStation(draws);
  const ordered = chronological(draws);
  const evaluationCount = Math.max(0, Math.min(evaluationLimit, ordered.length - window));
  const startIndex = ordered.length - evaluationCount;
  const series: BacktestPoint[] = [];

  for (let index = startIndex; index < ordered.length; index += 1) {
    const training = ordered.slice(index - window, index);
    const picks = pickNumbers(training, kind);
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
  return {
    modelVersion: ANALYTICS_MODEL_VERSION,
    baseline: BASELINE_COVERAGE,
    evaluationCount,
    coveredResults,
    totalResults,
    coverage,
    lift: coverage / BASELINE_COVERAGE,
    series,
  };
}
