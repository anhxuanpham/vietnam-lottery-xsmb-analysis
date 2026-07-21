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

export type LotteryV2Station = Omit<LotteryStation, "fullFrequency"> & {
  years: number[];
};

export type LotteryV2ReleaseMetadata = {
  schemaVersion: 2;
  releaseId: string;
  region: LotteryRegion;
  source: "r2";
  generatedAt: string;
  manifest: LotteryDashboardData["manifest"];
  freshness: LotteryDashboardData["freshness"];
  range: { from: string; to: string };
  drawCount: number;
  resultCount: number;
  shardKeyTemplate: string;
  stations: LotteryV2Station[];
};

export type LotteryV2Shard = {
  schemaVersion: 2;
  releaseId: string;
  region: LotteryRegion;
  station: { code: string; name: string };
  year: number;
  range: { from: string; to: string };
  drawCount: number;
  resultCount: number;
  draws: LotteryDraw[];
};

export type LotteryV2ResultsPage = {
  schemaVersion: 2;
  source: "r2";
  region: LotteryRegion;
  releaseId: string;
  datasetVersion: string;
  generatedAt: string;
  query: {
    station: string;
    from: string | null;
    to: string | null;
    number: string | null;
  };
  page: {
    limit: number;
    returned: number;
    nextCursor: string | null;
  };
  items: LotteryDraw[];
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
const SAFE_KEY_PART_PATTERN = /^[A-Za-z0-9._-]+$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !DATE_PATTERN.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function isDateRange(value: unknown): value is { from: string; to: string } {
  return isRecord(value) &&
    isIsoDate(value.from) &&
    isIsoDate(value.to) &&
    value.from <= value.to;
}

function isFrequencyMap(value: unknown): value is FrequencyMap {
  if (!isRecord(value)) return false;
  return Array.from({ length: 100 }, (_, index) => String(index).padStart(2, "0"))
    .every((number) => isNonNegativeInteger(value[number]));
}

function isLotteryDraw(value: unknown, region: LotteryRegion, stationCodes: Set<string>): value is LotteryDraw {
  if (!isRecord(value) || !isIsoDate(value.date)) return false;
  if (typeof value.stationCode !== "string" || !stationCodes.has(value.stationCode)) return false;
  if (!isNonEmptyString(value.stationName) || typeof value.specialPrize !== "string" || !DIGITS_PATTERN.test(value.specialPrize)) return false;
  if (typeof value.specialTail !== "string" || !NUMBER_PATTERN.test(value.specialTail) || !value.specialPrize.endsWith(value.specialTail)) return false;
  const expectedResults = region === "xsmb" ? 27 : 18;
  if (!Array.isArray(value.numbers) || value.numbers.length !== expectedResults) return false;
  if (!value.numbers.every((number) => typeof number === "string" && NUMBER_PATTERN.test(number))) return false;
  const numbers = value.numbers as string[];
  if (!isRecord(value.prizes)) return false;
  const validPrizes = Object.values(value.prizes).every(
    (prizes) => Array.isArray(prizes) && prizes.every((prize) => typeof prize === "string" && /^\d{1,6}$/.test(prize)),
  );
  if (!validPrizes) return false;
  const prizeNumbers = Object.values(value.prizes)
    .flatMap((prizes) => prizes as string[])
    .map((prize) => prize.slice(-2).padStart(2, "0"));
  const sortedNumbers = [...numbers].sort();
  return prizeNumbers.length === expectedResults &&
    [...prizeNumbers].sort().every((number, index) => number === sortedNumbers[index]) &&
    Array.isArray(value.prizes.special) && value.prizes.special.length === 1 &&
    value.prizes.special[0] === value.specialPrize;
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
    !isNonEmptyString(manifest.datasetVersion) || !isIsoDate(manifest.targetDate) ||
    !isNonEmptyString(manifest.publishedAt) || Number.isNaN(Date.parse(manifest.publishedAt))) return false;

  const freshness = value.freshness;
  if (!isRecord(freshness) || !isIsoDate(freshness.latestDrawDate) ||
    !isIsoDate(freshness.manifestTargetDate) ||
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
  if (!isRecord(latest) || !isIsoDate(latest.date) || !Array.isArray(latest.results) ||
    latest.results.length === 0 || !latest.results.every((draw) => isLotteryDraw(draw, region, stationCodes))) return false;
  return true;
}

export function normalizeLotteryDashboardData(value: unknown, expectedRegion: LotteryRegion): LotteryDashboardData | null {
  return isLotteryDashboardData(value, expectedRegion) ? value : null;
}

export function isLotteryV2ReleaseMetadata(
  value: unknown,
  expectedRegion?: LotteryRegion,
): value is LotteryV2ReleaseMetadata {
  if (!isRecord(value) || value.schemaVersion !== 2 || value.source !== "r2") return false;
  const rawRegion = typeof value.region === "string" ? value.region : null;
  if (!isLotteryRegion(rawRegion) || (expectedRegion && rawRegion !== expectedRegion)) return false;
  if (!isNonEmptyString(value.releaseId) || !SAFE_KEY_PART_PATTERN.test(value.releaseId)) return false;
  if (!isNonEmptyString(value.generatedAt) || Number.isNaN(Date.parse(value.generatedAt))) return false;
  if (!isDateRange(value.range) || !isNonNegativeInteger(value.drawCount) || !isNonNegativeInteger(value.resultCount)) return false;
  if (!isNonEmptyString(value.shardKeyTemplate) || !value.shardKeyTemplate.includes("{stationCode}") || !value.shardKeyTemplate.includes("{year}")) return false;

  const manifest = value.manifest;
  if (!isRecord(manifest) || !isNonEmptyString(manifest.key) || !isNonEmptyString(manifest.runId) ||
    !isNonEmptyString(manifest.datasetVersion) || manifest.datasetVersion !== value.releaseId ||
    !isIsoDate(manifest.targetDate) ||
    !isNonEmptyString(manifest.publishedAt) || Number.isNaN(Date.parse(manifest.publishedAt))) return false;

  const freshness = value.freshness;
  if (!isRecord(freshness) || !isIsoDate(freshness.latestDrawDate) ||
    !isIsoDate(freshness.manifestTargetDate) ||
    typeof freshness.matchesManifestTarget !== "boolean") return false;

  if (!Array.isArray(value.stations) || value.stations.length === 0) return false;
  const stationCodes = new Set<string>();
  for (const station of value.stations) {
    if (!isRecord(station) || typeof station.code !== "string" || !STATION_CODE_PATTERN.test(station.code) || stationCodes.has(station.code) ||
      !isNonEmptyString(station.name) || (station.url !== null && !isNonEmptyString(station.url)) || !isDateRange(station.range) ||
      !isNonNegativeInteger(station.drawCount) || !isNonNegativeInteger(station.resultCount) || !Array.isArray(station.years) ||
      station.years.length === 0 || !station.years.every((year) => Number.isSafeInteger(year) && year >= 1900 && year <= 9999) ||
      new Set(station.years).size !== station.years.length) return false;
    const years = station.years as number[];
    if (!years.every((year, index) => index === 0 || years[index - 1] < year) ||
      years[0] !== Number(station.range.from.slice(0, 4)) ||
      years.at(-1) !== Number(station.range.to.slice(0, 4)) ||
      station.resultCount !== station.drawCount * (rawRegion === "xsmb" ? 27 : 18)) return false;
    stationCodes.add(station.code);
  }
  const stations = value.stations as LotteryV2Station[];
  return stations.reduce((total, station) => total + station.drawCount, 0) === value.drawCount &&
    stations.reduce((total, station) => total + station.resultCount, 0) === value.resultCount &&
    stations.reduce((minimum, station) => station.range.from < minimum ? station.range.from : minimum, stations[0].range.from) === value.range.from &&
    stations.reduce((maximum, station) => station.range.to > maximum ? station.range.to : maximum, stations[0].range.to) === value.range.to;
}

export function isLotteryV2Shard(
  value: unknown,
  metadata: LotteryV2ReleaseMetadata,
  stationCode: string,
  year: number,
): value is LotteryV2Shard {
  return isLotteryV2ShardPayload(value) && value.releaseId === metadata.releaseId &&
    value.region === metadata.region && value.station.code === stationCode && value.year === year;
}

export function isLotteryV2ShardPayload(value: unknown): value is LotteryV2Shard {
  if (!isRecord(value) || value.schemaVersion !== 2 || !isNonEmptyString(value.releaseId) ||
    !SAFE_KEY_PART_PATTERN.test(value.releaseId) || typeof value.region !== "string" || !isLotteryRegion(value.region) ||
    !Number.isSafeInteger(value.year) || (value.year as number) < 1900 || (value.year as number) > 9999 ||
    !isDateRange(value.range) || !isNonNegativeInteger(value.drawCount) ||
    !isNonNegativeInteger(value.resultCount)) return false;
  const station = value.station;
  if (!isRecord(station) || typeof station.code !== "string" || !STATION_CODE_PATTERN.test(station.code) ||
    !isNonEmptyString(station.name)) return false;
  if (!Array.isArray(value.draws) || value.draws.length === 0 || value.draws.length !== value.drawCount) return false;
  const region = value.region;
  const codes = new Set([station.code]);
  if (!value.draws.every((draw) => isLotteryDraw(draw, region, codes))) return false;
  const draws = value.draws as LotteryDraw[];
  const dates = draws.map((draw) => draw.date);
  return dates.every((date, index) => Number(date.slice(0, 4)) === value.year &&
      (index === 0 || dates[index - 1] < date)) &&
    new Set(dates).size === dates.length &&
    value.range.from === dates[0] && value.range.to === dates.at(-1) &&
    value.resultCount === draws.reduce((total, draw) => total + draw.numbers.length, 0) &&
    draws.every((draw) => draw.stationName === station.name);
}

export function isLotteryV2ResultsPage(
  value: unknown,
  expectedRegion: LotteryRegion,
): value is LotteryV2ResultsPage {
  if (!isRecord(value) || value.schemaVersion !== 2 || value.source !== "r2" || value.region !== expectedRegion ||
    !isNonEmptyString(value.releaseId) || !isNonEmptyString(value.datasetVersion) ||
    value.releaseId !== value.datasetVersion ||
    !isNonEmptyString(value.generatedAt) || Number.isNaN(Date.parse(value.generatedAt))) return false;
  const query = value.query;
  if (!isRecord(query) || typeof query.station !== "string" || !STATION_CODE_PATTERN.test(query.station) ||
    (query.from !== null && !isIsoDate(query.from)) ||
    (query.to !== null && !isIsoDate(query.to)) ||
    (query.number !== null && (typeof query.number !== "string" || !NUMBER_PATTERN.test(query.number))) ||
    (typeof query.from === "string" && typeof query.to === "string" && query.to < query.from)) return false;
  const page = value.page;
  if (!isRecord(page) || !isNonNegativeInteger(page.limit) || page.limit < 1 || page.limit > 100 ||
    !isNonNegativeInteger(page.returned) || page.returned > page.limit ||
    (page.nextCursor !== null && !isNonEmptyString(page.nextCursor))) return false;
  if (!Array.isArray(value.items) || value.items.length !== page.returned) return false;
  const normalizedQuery = query as LotteryV2ResultsPage["query"];
  const codes = new Set([normalizedQuery.station]);
  if (!value.items.every((draw) => isLotteryDraw(draw, expectedRegion, codes))) return false;
  const items = value.items as LotteryDraw[];
  return items.every((draw, index) =>
    (index === 0 || items[index - 1].date > draw.date) &&
    (normalizedQuery.from === null || draw.date >= normalizedQuery.from) &&
    (normalizedQuery.to === null || draw.date <= normalizedQuery.to) &&
    (normalizedQuery.number === null || draw.numbers.includes(normalizedQuery.number))
  );
}

export function normalizeLotteryV2ReleaseMetadata(
  value: unknown,
  expectedRegion: LotteryRegion,
): LotteryV2ReleaseMetadata | null {
  return isLotteryV2ReleaseMetadata(value, expectedRegion) ? value : null;
}

export function normalizeLotteryV2ResultsPage(
  value: unknown,
  expectedRegion: LotteryRegion,
): LotteryV2ResultsPage | null {
  return isLotteryV2ResultsPage(value, expectedRegion) ? value : null;
}
