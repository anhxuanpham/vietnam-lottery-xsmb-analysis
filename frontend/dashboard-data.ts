import {
  normalizeLotteryDashboardData,
  normalizeLotteryV2ReleaseMetadata,
  normalizeLotteryV2ResultsPage,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryRegion,
  type LotteryV2ReleaseMetadata,
} from "./lottery-contract.ts";

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
    do {
      const parameters = new URLSearchParams({
        region,
        station: requestedStation,
        limit: "100",
      });
      if (cursor) parameters.set("cursor", cursor);
      const response = await fetcher(`/api/v2/results?${parameters}`, { signal: options.signal });
      if (!response.ok) throw new Error(`V2 results API returned HTTP ${response.status}`);
      const payload: unknown = await response.json();
      const page = normalizeLotteryV2ResultsPage(payload, region);
      if (!page || page.releaseId !== dashboard.data.manifest.datasetVersion) {
        throw new Error("Invalid or stale v2 results page");
      }
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
