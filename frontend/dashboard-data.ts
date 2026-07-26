import {
  lotteryDrawMatchesPrizeFilter,
  normalizeLotteryDashboardData,
  normalizeLotteryV2ReleaseMetadata,
  normalizeLotteryV2ResultsPage,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryRegion,
  type LotteryV2ReleaseMetadata,
  type LotteryV2ResultsPage,
} from "./lottery-contract.ts";
import {
  normalizeExplorerQuery,
  type ExplorerQuery,
} from "./explorer-state.ts";

export type DashboardMetadata = LotteryV2ReleaseMetadata | LotteryDashboardData;
export type ServingMode = "v2" | "v1";

export type DashboardLoad = {
  data: DashboardMetadata;
  fallbackData: LotteryDashboardData | null;
  servingMode: ServingMode;
  dataSource: string;
};

export type StationHistoryLoad = {
  draws: LotteryDraw[];
  station: string;
  fallback: DashboardLoad | null;
};

type FetchOptions = {
  signal?: AbortSignal;
  fetcher?: typeof fetch;
};

type ExplorerFetchOptions = FetchOptions & {
  cursor?: string | null;
  limit?: number;
};

export class ExplorerPageError extends Error {
  readonly code: string;
  readonly status: number | null;

  constructor(code: string, message: string, status: number | null = null) {
    super(message);
    this.name = "ExplorerPageError";
    this.code = code;
    this.status = status;
  }
}

function abortError(signal: AbortSignal | undefined): unknown {
  if (!signal?.aborted) return null;
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

export async function fetchCompatibilityDashboard(
  region: LotteryRegion,
  options: FetchOptions = {},
): Promise<DashboardLoad> {
  const fetcher = options.fetcher ?? fetch;
  const response = await fetcher(`/api/lottery?region=${region}`, { signal: options.signal });
  if (!response.ok) throw new Error(`Compatibility API returned HTTP ${response.status}`);
  const payload: unknown = await response.json();
  const fallback = normalizeLotteryDashboardData(payload, region);
  if (!fallback) throw new Error("Invalid compatibility payload");
  return {
    data: fallback,
    fallbackData: fallback,
    servingMode: "v1",
    dataSource: response.headers.get("x-lottery-source") ?? "api",
  };
}

export async function fetchPreferredDashboard(
  region: LotteryRegion,
  options: FetchOptions = {},
): Promise<DashboardLoad> {
  const fetcher = options.fetcher ?? fetch;
  try {
    const response = await fetcher(`/api/v2/lottery?region=${region}`, { signal: options.signal });
    if (!response.ok) throw new Error(`V2 metadata API returned HTTP ${response.status}`);
    const payload: unknown = await response.json();
    const metadata = normalizeLotteryV2ReleaseMetadata(payload, region);
    if (!metadata) throw new Error("Invalid v2 release metadata");
    return {
      data: metadata,
      fallbackData: null,
      servingMode: "v2",
      dataSource: response.headers.get("x-lottery-source") ?? metadata.source,
    };
  } catch {
    const aborted = abortError(options.signal);
    if (aborted) throw aborted;
    return fetchCompatibilityDashboard(region, options);
  }
}

function errorCode(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return null;
  const value = (payload as Record<string, unknown>).error;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function responseMatchesQuery(
  page: LotteryV2ResultsPage,
  query: ExplorerQuery,
  limit: number,
): boolean {
  const normalized = normalizeExplorerQuery(query);
  return page.region === normalized.region &&
    page.query.station === normalized.station &&
    page.query.from === normalized.from &&
    page.query.to === normalized.to &&
    page.query.number === normalized.number &&
    page.query.value === normalized.value &&
    page.query.match === normalized.match &&
    page.query.prizeGroup === normalized.prizeGroup &&
    page.page.limit === limit;
}

// The worker bounds each request to a fixed number of year shards, so a page can
// legitimately be empty while its cursor still points at unscanned older years.
const MAX_EMPTY_PAGE_SKIPS = 25;

export async function fetchExplorerPage(
  query: ExplorerQuery,
  expectedReleaseId: string,
  options: ExplorerFetchOptions = {},
): Promise<LotteryV2ResultsPage> {
  let page = await fetchExplorerPageOnce(query, expectedReleaseId, options);
  for (
    let skips = 0;
    page.items.length === 0 && page.page.nextCursor !== null && skips < MAX_EMPTY_PAGE_SKIPS;
    skips += 1
  ) {
    page = await fetchExplorerPageOnce(query, expectedReleaseId, {
      ...options,
      cursor: page.page.nextCursor,
    });
  }
  return page;
}

async function fetchExplorerPageOnce(
  query: ExplorerQuery,
  expectedReleaseId: string,
  options: ExplorerFetchOptions = {},
): Promise<LotteryV2ResultsPage> {
  const fetcher = options.fetcher ?? fetch;
  const limit = options.limit ?? 25;
  const normalized = normalizeExplorerQuery(query);
  const parameters = new URLSearchParams({
    region: normalized.region,
    station: normalized.station,
    limit: String(limit),
  });
  if (normalized.from !== null) parameters.set("from", normalized.from);
  if (normalized.to !== null) parameters.set("to", normalized.to);
  if (normalized.number !== null) parameters.set("number", normalized.number);
  if (normalized.value !== null) parameters.set("value", normalized.value);
  if (normalized.match !== null) parameters.set("match", normalized.match);
  if (normalized.prizeGroup !== null) parameters.set("prizeGroup", normalized.prizeGroup);
  if (options.cursor) parameters.set("cursor", options.cursor);

  const response = await fetcher(`/api/v2/results?${parameters}`, { signal: options.signal });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ExplorerPageError(
      "invalid_response",
      `V2 results API returned a non-JSON response with HTTP ${response.status}`,
      response.status,
    );
  }
  if (!response.ok) {
    throw new ExplorerPageError(
      errorCode(payload) ?? "request_failed",
      `V2 results API returned HTTP ${response.status}`,
      response.status,
    );
  }
  const page = normalizeLotteryV2ResultsPage(payload, query.region);
  if (!page) {
    throw new ExplorerPageError("invalid_response", "V2 results API returned an invalid result page");
  }
  if (page.releaseId !== expectedReleaseId) {
    throw new ExplorerPageError("stale_release", "V2 results page belongs to another release");
  }
  if (!responseMatchesQuery(page, query, limit)) {
    throw new ExplorerPageError("response_query_mismatch", "V2 results page does not match the requested query");
  }
  return page;
}

export function compatibilityExplorerItems(
  data: LotteryDashboardData,
  query: ExplorerQuery,
  limit = 25,
): LotteryDraw[] {
  const normalized = normalizeExplorerQuery(query);
  return data.draws
    .filter((draw) => draw.stationCode === normalized.station)
    .filter((draw) => normalized.from === null || draw.date >= normalized.from)
    .filter((draw) => normalized.to === null || draw.date <= normalized.to)
    .filter((draw) => lotteryDrawMatchesPrizeFilter(draw, normalized))
    .sort((left, right) => right.date.localeCompare(left.date))
    .slice(0, limit);
}

// 365-draw max analysis window plus the 90-draw evaluation limit; must match
// DEFAULT_RECENT_DRAWS_PER_STATION in scripts/export_serving_data.py.
const STATION_HISTORY_TARGET_DRAWS = 455;

function stationDraws(data: LotteryDashboardData, station: string): LotteryDraw[] {
  return data.draws
    .filter((draw) => draw.stationCode === station)
    .sort((left, right) => left.date.localeCompare(right.date));
}

export async function fetchStationHistory(
  dashboard: DashboardLoad,
  region: LotteryRegion,
  requestedStation: string,
  options: FetchOptions = {},
): Promise<StationHistoryLoad> {
  const fetcher = options.fetcher ?? fetch;
  if (dashboard.servingMode === "v1") {
    const fallback = dashboard.fallbackData;
    if (!fallback) throw new Error("Compatibility dashboard has no compatibility payload");
    const station = fallback.stations.some((item) => item.code === requestedStation)
      ? requestedStation
      : fallback.stations[0]?.code ?? "";
    return { draws: stationDraws(fallback, station), station, fallback: null };
  }

  try {
    const newest: LotteryDraw[] = [];
    let cursor: string | null = null;
    const query: ExplorerQuery = {
      region,
      station: requestedStation,
      from: null,
      to: null,
      number: null,
    };
    do {
      const page = await fetchExplorerPage(query, dashboard.data.manifest.datasetVersion, {
        cursor,
        limit: 100,
        signal: options.signal,
        fetcher,
      });
      newest.push(...page.items);
      cursor = page.page.nextCursor;
    } while (cursor && newest.length < STATION_HISTORY_TARGET_DRAWS);
    return {
      draws: newest
        .slice(0, STATION_HISTORY_TARGET_DRAWS)
        .sort((left, right) => left.date.localeCompare(right.date)),
      station: requestedStation,
      fallback: null,
    };
  } catch (error) {
    const aborted = abortError(options.signal);
    if (aborted) throw aborted;
    const fallback = await fetchCompatibilityDashboard(region, options);
    const fallbackPayload = fallback.fallbackData;
    if (!fallbackPayload) throw error;
    const station = fallbackPayload.stations.some((item) => item.code === requestedStation)
      ? requestedStation
      : fallbackPayload.stations[0]?.code ?? "";
    return {
      draws: stationDraws(fallbackPayload, station),
      station,
      fallback,
    };
  }
}
