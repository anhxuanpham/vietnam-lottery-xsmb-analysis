import {
  normalizeLotteryDashboardData,
  normalizeLotteryV2ReleaseMetadata,
  normalizeLotteryV2ResultsPage,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryRegion,
  type LotteryV2ReleaseMetadata,
  type LotteryV2ResultsPage,
} from "./lottery-contract.ts";
import type { ExplorerQuery } from "./explorer-state.ts";

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
  return page.region === query.region &&
    page.query.station === query.station &&
    page.query.from === query.from &&
    page.query.to === query.to &&
    page.query.number === query.number &&
    page.page.limit === limit;
}

export async function fetchExplorerPage(
  query: ExplorerQuery,
  expectedReleaseId: string,
  options: ExplorerFetchOptions = {},
): Promise<LotteryV2ResultsPage> {
  const fetcher = options.fetcher ?? fetch;
  const limit = options.limit ?? 25;
  const parameters = new URLSearchParams({
    region: query.region,
    station: query.station,
    limit: String(limit),
  });
  if (query.from !== null) parameters.set("from", query.from);
  if (query.to !== null) parameters.set("to", query.to);
  if (query.number !== null) parameters.set("number", query.number);
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
  return data.draws
    .filter((draw) => draw.stationCode === query.station)
    .filter((draw) => query.from === null || draw.date >= query.from)
    .filter((draw) => query.to === null || draw.date <= query.to)
    .filter((draw) => query.number === null || draw.numbers.includes(query.number))
    .sort((left, right) => right.date.localeCompare(left.date))
    .slice(0, limit);
}

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
    } while (cursor && newest.length < 455);
    return {
      draws: newest.slice(0, 455).sort((left, right) => left.date.localeCompare(right.date)),
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
