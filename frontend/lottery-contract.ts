export const LOTTERY_REGIONS = ["xsmb", "xsmn", "xsmt"] as const;

export type LotteryRegion = (typeof LOTTERY_REGIONS)[number];
export type FrequencyMap = Record<string, number>;

export type LotteryStation = {
  code: string;
  name: string;
  url: string | null;
  range: { from: string; to: string };
  drawCount: number;
  resultCount: number;
  fullFrequency: FrequencyMap;
};

export type LotteryDraw = {
  date: string;
  stationCode: string;
  stationName: string;
  specialPrize: string;
  specialTail: string;
  numbers: string[];
  prizes: Record<string, string[]>;
};

export type LotteryDashboardData = {
  schemaVersion: 1;
  region: LotteryRegion;
  generatedAt: string;
  manifest: {
    key: string;
    runId: string;
    datasetVersion: string;
    targetDate: string;
    publishedAt: string;
  };
  freshness: {
    latestDrawDate: string;
    manifestTargetDate: string;
    matchesManifestTarget: boolean;
  };
  range: { from: string; to: string };
  drawCount: number;
  resultCount: number;
  latest: { date: string; results: LotteryDraw[] };
  fullFrequency: FrequencyMap;
  draws: LotteryDraw[];
  stations: LotteryStation[];
};

const REGION_NAMES: Record<LotteryRegion, string> = {
  xsmb: "Miền Bắc",
  xsmn: "Miền Nam",
  xsmt: "Miền Trung",
};

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const NUMBER_PATTERN = /^\d{2}$/;
const DIGITS_PATTERN = /^\d{2,6}$/;
const STATION_CODE_PATTERN = /^[A-Za-z0-9]{2,8}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isDateRange(value: unknown): value is { from: string; to: string } {
  return isRecord(value) &&
    typeof value.from === "string" && DATE_PATTERN.test(value.from) &&
    typeof value.to === "string" && DATE_PATTERN.test(value.to) &&
    value.from <= value.to;
}

function isFrequencyMap(value: unknown): value is FrequencyMap {
  if (!isRecord(value)) return false;
  return Array.from({ length: 100 }, (_, index) => String(index).padStart(2, "0"))
    .every((number) => isNonNegativeInteger(value[number]));
}

function isLotteryDraw(value: unknown, region: LotteryRegion, stationCodes: Set<string>): value is LotteryDraw {
  if (!isRecord(value) || typeof value.date !== "string" || !DATE_PATTERN.test(value.date)) return false;
  if (typeof value.stationCode !== "string" || !stationCodes.has(value.stationCode)) return false;
  if (!isNonEmptyString(value.stationName) || typeof value.specialPrize !== "string" || !DIGITS_PATTERN.test(value.specialPrize)) return false;
  if (typeof value.specialTail !== "string" || !NUMBER_PATTERN.test(value.specialTail) || !value.specialPrize.endsWith(value.specialTail)) return false;
  const expectedResults = region === "xsmb" ? 27 : 18;
  if (!Array.isArray(value.numbers) || value.numbers.length !== expectedResults) return false;
  if (!value.numbers.every((number) => typeof number === "string" && NUMBER_PATTERN.test(number))) return false;
  if (!isRecord(value.prizes)) return false;
  return Object.values(value.prizes).every(
    (prizes) => Array.isArray(prizes) && prizes.every((prize) => typeof prize === "string" && /^\d{1,6}$/.test(prize)),
  );
}

export function isLotteryRegion(value: string | null): value is LotteryRegion {
  return value !== null && LOTTERY_REGIONS.includes(value as LotteryRegion);
}

export function regionName(region: LotteryRegion): string {
  return REGION_NAMES[region];
}

export function isLotteryDashboardData(value: unknown, expectedRegion?: LotteryRegion): value is LotteryDashboardData {
  if (!isRecord(value) || value.schemaVersion !== 1) return false;
  const rawRegion = typeof value.region === "string" ? value.region : null;
  if (!isLotteryRegion(rawRegion)) return false;
  const region = rawRegion;
  if (expectedRegion && region !== expectedRegion) return false;
  if (!isNonEmptyString(value.generatedAt) || Number.isNaN(Date.parse(value.generatedAt))) return false;
  if (!isDateRange(value.range) || !isNonNegativeInteger(value.drawCount) || !isNonNegativeInteger(value.resultCount)) return false;
  if (!isFrequencyMap(value.fullFrequency)) return false;

  const manifest = value.manifest;
  if (!isRecord(manifest) || !isNonEmptyString(manifest.key) || !isNonEmptyString(manifest.runId) ||
    !isNonEmptyString(manifest.datasetVersion) || typeof manifest.targetDate !== "string" || !DATE_PATTERN.test(manifest.targetDate) ||
    !isNonEmptyString(manifest.publishedAt) || Number.isNaN(Date.parse(manifest.publishedAt))) return false;

  const freshness = value.freshness;
  if (!isRecord(freshness) || typeof freshness.latestDrawDate !== "string" || !DATE_PATTERN.test(freshness.latestDrawDate) ||
    typeof freshness.manifestTargetDate !== "string" || !DATE_PATTERN.test(freshness.manifestTargetDate) ||
    typeof freshness.matchesManifestTarget !== "boolean") return false;

  if (!Array.isArray(value.stations) || value.stations.length === 0) return false;
  const stationCodes = new Set<string>();
  for (const station of value.stations) {
    if (!isRecord(station) || typeof station.code !== "string" || !STATION_CODE_PATTERN.test(station.code) || stationCodes.has(station.code) ||
      !isNonEmptyString(station.name) || (station.url !== null && !isNonEmptyString(station.url)) || !isDateRange(station.range) ||
      !isNonNegativeInteger(station.drawCount) || !isNonNegativeInteger(station.resultCount) || !isFrequencyMap(station.fullFrequency)) return false;
    stationCodes.add(station.code);
  }

  if (!Array.isArray(value.draws) || value.draws.length === 0 || !value.draws.every((draw) => isLotteryDraw(draw, region, stationCodes))) return false;
  const latest = value.latest;
  if (!isRecord(latest) || typeof latest.date !== "string" || !DATE_PATTERN.test(latest.date) || !Array.isArray(latest.results) ||
    latest.results.length === 0 || !latest.results.every((draw) => isLotteryDraw(draw, region, stationCodes))) return false;
  return true;
}

export function normalizeLotteryDashboardData(value: unknown, expectedRegion: LotteryRegion): LotteryDashboardData | null {
  return isLotteryDashboardData(value, expectedRegion) ? value : null;
}
