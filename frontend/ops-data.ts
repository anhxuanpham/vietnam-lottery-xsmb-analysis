import {
  LOTTERY_REGIONS,
  normalizeLotteryWatchdogStatus,
  type LotteryRegion,
  type LotteryWatchdogStatus,
} from "./lottery-contract.ts";

export type LotteryRegionHealth = {
  healthy: boolean;
  issues: string[];
  observedTargetDate: string | null;
  latestDrawDate: string | null;
  datasetVersion: string | null;
};

export type LotteryServingHealth = {
  schemaVersion: 1;
  service: "lottery-serving-data";
  checkedAt: string;
  expectedTargetDate: string;
  v2Required: boolean;
  healthy: boolean;
  regions: Record<LotteryRegion, LotteryRegionHealth>;
};

export type LotteryOperationsSnapshot = {
  health: LotteryServingHealth;
  watchdog: LotteryWatchdogStatus | null;
};

type FetchOptions = {
  signal?: AbortSignal;
  fetcher?: typeof fetch;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function nullableDate(value: unknown): value is string | null {
  return value === null || isDate(value);
}

export function normalizeLotteryServingHealth(value: unknown): LotteryServingHealth | null {
  if (!isRecord(value) || value.schemaVersion !== 1 || value.service !== "lottery-serving-data" ||
    typeof value.checkedAt !== "string" || Number.isNaN(Date.parse(value.checkedAt)) ||
    !isDate(value.expectedTargetDate) || typeof value.v2Required !== "boolean" ||
    typeof value.healthy !== "boolean" || !isRecord(value.regions)) return null;

  for (const region of LOTTERY_REGIONS) {
    const report = value.regions[region];
    if (!isRecord(report) || typeof report.healthy !== "boolean" || !Array.isArray(report.issues) ||
      !report.issues.every((issue) => typeof issue === "string") ||
      !nullableDate(report.observedTargetDate) || !nullableDate(report.latestDrawDate) ||
      (report.datasetVersion !== null && typeof report.datasetVersion !== "string")) return null;
  }
  return value as LotteryServingHealth;
}

export async function fetchLotteryOperations(
  options: FetchOptions = {},
): Promise<LotteryOperationsSnapshot> {
  const fetcher = options.fetcher ?? fetch;
  const [healthResponse, watchdogResponse] = await Promise.all([
    fetcher("/api/health/lottery", { signal: options.signal }),
    fetcher("/api/ops/lottery", { signal: options.signal }).catch(() => null),
  ]);

  if (!healthResponse.ok && healthResponse.status !== 503) {
    throw new Error(`Lottery health API returned HTTP ${healthResponse.status}`);
  }
  const health = normalizeLotteryServingHealth(await healthResponse.json());
  if (!health) throw new Error("Lottery health API returned an invalid payload");

  let watchdog: LotteryWatchdogStatus | null = null;
  if (watchdogResponse?.ok) {
    watchdog = normalizeLotteryWatchdogStatus(await watchdogResponse.json());
  }
  return { health, watchdog };
}
