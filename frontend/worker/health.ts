import {
  isLotteryDashboardData,
  isLotteryV2ReleaseMetadata,
  isLotteryV2Shard,
  LOTTERY_REGIONS,
  type LotteryDashboardData,
  type LotteryRegion,
} from "../lottery-contract.ts";

export const VIETNAM_UTC_OFFSET_MINUTES = 7 * 60;
// The dashboard publisher is scheduled for 19:47 Vietnam time. Treat today's
// target as due from 20:00 so normal publication latency does not make the
// public health endpoint flap every evening.
export const DEFAULT_TARGET_ROLLOVER_MINUTE = 20 * 60;
export const MAX_HEALTH_OBJECT_BYTES = 8 * 1024 * 1024;
export const MAX_V2_METADATA_BYTES = 100 * 1024;
export const V2_HEALTH_ACTIVATION_KEY = "v2/health/required.json";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const JSON_HEADERS = {
  "cache-control": "no-store",
  "content-type": "application/json; charset=utf-8",
  "x-content-type-options": "nosniff",
};

export type HealthIssueCode =
  | "object_missing"
  | "object_read_failed"
  | "payload_too_large"
  | "invalid_json"
  | "invalid_contract"
  | "target_date_mismatch"
  | "manifest_freshness_mismatch"
  | "latest_draw_date_mismatch"
  | "latest_payload_date_mismatch"
  | "range_end_mismatch"
  | "freshness_flag_false"
  | "v2_activation_read_failed"
  | "v2_activation_invalid"
  | "v2_metadata_missing"
  | "v2_metadata_read_failed"
  | "v2_metadata_too_large"
  | "v2_metadata_invalid_contract"
  | "v2_target_date_mismatch"
  | "v2_published_boundary_mismatch"
  | "v2_shard_missing"
  | "v2_shard_read_failed"
  | "v2_shard_invalid_contract";

export type LotteryRegionHealth = {
  region: LotteryRegion;
  source: "r2" | "missing" | "error";
  healthy: boolean;
  issues: HealthIssueCode[];
  expectedTargetDate: string;
  observedTargetDate: string | null;
  latestDrawDate: string | null;
  latestPayloadDate: string | null;
  rangeEndDate: string | null;
  generatedAt: string | null;
  datasetVersion: string | null;
  drawCount: number | null;
  resultCount: number | null;
  objectBytes: number | null;
  v2Required: boolean;
  v2Source: "r2" | "not_required" | "missing" | "error";
  v2ReleaseId: string | null;
  v2GeneratedAt: string | null;
  v2ObjectBytes: number | null;
};

export type LotteryHealthReport = {
  schemaVersion: 1;
  service: "lottery-serving-data";
  checkedAt: string;
  expectedTargetDate: string;
  v2Required: boolean;
  healthy: boolean;
  regions: Record<LotteryRegion, LotteryRegionHealth>;
};

export type HealthEvaluationOptions = {
  now?: number;
  expectedTargetDate?: string;
  targetRolloverMinute?: number;
};

export type VietnamClock = {
  date: string;
  minuteOfDay: number;
};

type V2Requirement = {
  required: boolean;
  issue: "v2_activation_read_failed" | "v2_activation_invalid" | null;
};

function assertEpochMilliseconds(value: number): void {
  if (!Number.isFinite(value)) throw new RangeError("now must be finite epoch milliseconds");
}

function assertMinuteOfDay(value: number, label: string): void {
  if (!Number.isInteger(value) || value < 0 || value >= 24 * 60) {
    throw new RangeError(`${label} must be an integer from 0 through 1439`);
  }
}

function assertIsoDate(value: string, label: string): void {
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!DATE_PATTERN.test(value) || Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new RangeError(`${label} must be an ISO date`);
  }
}

export function vietnamClock(now: number): VietnamClock {
  assertEpochMilliseconds(now);
  const shifted = new Date(now + VIETNAM_UTC_OFFSET_MINUTES * 60_000);
  return {
    date: shifted.toISOString().slice(0, 10),
    minuteOfDay: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(),
  };
}

export function previousIsoDate(date: string): string {
  assertIsoDate(date, "date");
  return new Date(Date.parse(`${date}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
}

export function expectedLotteryTargetDate(
  now: number,
  targetRolloverMinute = DEFAULT_TARGET_ROLLOVER_MINUTE,
): string {
  assertMinuteOfDay(targetRolloverMinute, "targetRolloverMinute");
  const clock = vietnamClock(now);
  return clock.minuteOfDay >= targetRolloverMinute ? clock.date : previousIsoDate(clock.date);
}

function servingObjectKey(region: LotteryRegion): string {
  return `regions/${region}.json`;
}

function unhealthyRegion(
  region: LotteryRegion,
  expectedTargetDate: string,
  source: LotteryRegionHealth["source"],
  issue: HealthIssueCode,
  objectBytes: number | null = null,
  v2Requirement: V2Requirement = { required: false, issue: null },
): LotteryRegionHealth {
  return {
    region,
    source,
    healthy: false,
    issues: v2Requirement.issue ? [issue, v2Requirement.issue] : [issue],
    expectedTargetDate,
    observedTargetDate: null,
    latestDrawDate: null,
    latestPayloadDate: null,
    rangeEndDate: null,
    generatedAt: null,
    datasetVersion: null,
    drawCount: null,
    resultCount: null,
    objectBytes,
    v2Required: v2Requirement.required,
    v2Source: v2Requirement.required ? "error" : "not_required",
    v2ReleaseId: null,
    v2GeneratedAt: null,
    v2ObjectBytes: null,
  };
}

async function v2Requirement(bucket: R2Bucket): Promise<V2Requirement> {
  let object: R2ObjectBody | null;
  try {
    object = await bucket.get(V2_HEALTH_ACTIVATION_KEY);
  } catch {
    return { required: true, issue: "v2_activation_read_failed" };
  }
  if (!object) return { required: false, issue: null };
  if (object.size > 16 * 1024) return { required: true, issue: "v2_activation_invalid" };
  try {
    const payload: unknown = await object.json();
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
      return { required: true, issue: "v2_activation_invalid" };
    }
    const record = payload as Record<string, unknown>;
    const regions = record.regions;
    if (record.schemaVersion !== 1 || record.required !== true || !Array.isArray(regions) ||
      regions.length !== LOTTERY_REGIONS.length ||
      !LOTTERY_REGIONS.every((region, index) => regions[index] === region)) {
      return { required: true, issue: "v2_activation_invalid" };
    }
    return { required: true, issue: null };
  } catch {
    return { required: true, issue: "v2_activation_invalid" };
  }
}

async function evaluateV2Metadata(
  bucket: R2Bucket,
  region: LotteryRegion,
  expectedTargetDate: string,
  maximumTargetDate: string,
  published: LotteryDashboardData,
  requirement: V2Requirement,
): Promise<Pick<LotteryRegionHealth,
  "v2Required" | "v2Source" | "v2ReleaseId" | "v2GeneratedAt" | "v2ObjectBytes"> & {
  issues: HealthIssueCode[];
}> {
  if (!requirement.required) {
    return {
      v2Required: false,
      v2Source: "not_required",
      v2ReleaseId: null,
      v2GeneratedAt: null,
      v2ObjectBytes: null,
      issues: [],
    };
  }
  const activationIssues: HealthIssueCode[] = requirement.issue ? [requirement.issue] : [];
  let object: R2ObjectBody | null;
  try {
    object = await bucket.get(`v2/regions/${region}/latest.json`);
  } catch {
    return {
      v2Required: true,
      v2Source: "error",
      v2ReleaseId: null,
      v2GeneratedAt: null,
      v2ObjectBytes: null,
      issues: [...activationIssues, "v2_metadata_read_failed"],
    };
  }
  if (!object) {
    return {
      v2Required: true,
      v2Source: "missing",
      v2ReleaseId: null,
      v2GeneratedAt: null,
      v2ObjectBytes: null,
      issues: [...activationIssues, "v2_metadata_missing"],
    };
  }
  if (object.size >= MAX_V2_METADATA_BYTES) {
    return {
      v2Required: true,
      v2Source: "r2",
      v2ReleaseId: null,
      v2GeneratedAt: null,
      v2ObjectBytes: object.size,
      issues: [...activationIssues, "v2_metadata_too_large"],
    };
  }
  let payload: unknown;
  try {
    payload = await object.json();
  } catch {
    return {
      v2Required: true,
      v2Source: "r2",
      v2ReleaseId: null,
      v2GeneratedAt: null,
      v2ObjectBytes: object.size,
      issues: [...activationIssues, "v2_metadata_read_failed"],
    };
  }
  if (!isLotteryV2ReleaseMetadata(payload, region)) {
    return {
      v2Required: true,
      v2Source: "r2",
      v2ReleaseId: null,
      v2GeneratedAt: null,
      v2ObjectBytes: object.size,
      issues: [...activationIssues, "v2_metadata_invalid_contract"],
    };
  }
  const issues = [...activationIssues];
  if (payload.manifest.targetDate < expectedTargetDate || payload.manifest.targetDate > maximumTargetDate ||
    payload.freshness.latestDrawDate !== published.manifest.targetDate ||
    payload.range.to !== published.manifest.targetDate) {
    issues.push("v2_target_date_mismatch");
  }
  if (payload.releaseId !== published.manifest.datasetVersion ||
    payload.manifest.runId !== published.manifest.runId ||
    payload.manifest.publishedAt !== published.manifest.publishedAt ||
    payload.range.from !== published.range.from || payload.range.to !== published.range.to ||
    payload.drawCount !== published.drawCount || payload.resultCount !== published.resultCount) {
    issues.push("v2_published_boundary_mismatch");
  }
  if (issues.length === 0) {
    for (const station of payload.stations) {
      const year = station.years.at(-1);
      if (year === undefined) {
        issues.push("v2_shard_invalid_contract");
        break;
      }
      const key = `v2/releases/${payload.releaseId}/regions/${region}/stations/${station.code}/years/${year}.json`;
      let shardObject: R2ObjectBody | null;
      try {
        shardObject = await bucket.get(key);
      } catch {
        issues.push("v2_shard_read_failed");
        break;
      }
      if (!shardObject) {
        issues.push("v2_shard_missing");
        break;
      }
      if (shardObject.size > 2 * 1024 * 1024) {
        issues.push("v2_shard_invalid_contract");
        break;
      }
      try {
        const shard: unknown = await shardObject.json();
        if (!isLotteryV2Shard(shard, payload, station.code, year)) {
          issues.push("v2_shard_invalid_contract");
          break;
        }
      } catch {
        issues.push("v2_shard_read_failed");
        break;
      }
    }
  }
  return {
    v2Required: true,
    v2Source: "r2",
    v2ReleaseId: payload.releaseId,
    v2GeneratedAt: payload.generatedAt,
    v2ObjectBytes: object.size,
    issues,
  };
}

function validateFreshness(
  payload: LotteryDashboardData,
  expectedTargetDate: string,
  maximumTargetDate: string,
): HealthIssueCode[] {
  const issues: HealthIssueCode[] = [];
  const observedTargetDate = payload.manifest.targetDate;
  if (observedTargetDate < expectedTargetDate || observedTargetDate > maximumTargetDate) {
    issues.push("target_date_mismatch");
  }
  if (payload.freshness.manifestTargetDate !== observedTargetDate) {
    issues.push("manifest_freshness_mismatch");
  }
  if (payload.freshness.latestDrawDate !== observedTargetDate) {
    issues.push("latest_draw_date_mismatch");
  }
  if (payload.latest.date !== observedTargetDate) issues.push("latest_payload_date_mismatch");
  if (payload.range.to !== observedTargetDate) issues.push("range_end_mismatch");
  if (!payload.freshness.matchesManifestTarget) issues.push("freshness_flag_false");
  return issues;
}

async function evaluateRegion(
  bucket: R2Bucket,
  region: LotteryRegion,
  expectedTargetDate: string,
  maximumTargetDate: string,
  requirement: V2Requirement,
): Promise<LotteryRegionHealth> {
  let object: R2ObjectBody | null;
  try {
    object = await bucket.get(servingObjectKey(region));
  } catch {
    return unhealthyRegion(region, expectedTargetDate, "error", "object_read_failed", null, requirement);
  }

  if (!object) return unhealthyRegion(region, expectedTargetDate, "missing", "object_missing", null, requirement);
  if (object.size > MAX_HEALTH_OBJECT_BYTES) {
    return unhealthyRegion(
      region,
      expectedTargetDate,
      "r2",
      "payload_too_large",
      object.size,
      requirement,
    );
  }

  let payload: unknown;
  try {
    payload = await object.json<unknown>();
  } catch (error) {
    const issue = error instanceof SyntaxError ? "invalid_json" : "object_read_failed";
    return unhealthyRegion(region, expectedTargetDate, "r2", issue, object.size, requirement);
  }

  if (!isLotteryDashboardData(payload, region)) {
    return unhealthyRegion(region, expectedTargetDate, "r2", "invalid_contract", object.size, requirement);
  }

  const v2 = await evaluateV2Metadata(
    bucket,
    region,
    expectedTargetDate,
    maximumTargetDate,
    payload,
    requirement,
  );
  const issues = [...validateFreshness(payload, expectedTargetDate, maximumTargetDate), ...v2.issues];
  return {
    region,
    source: "r2",
    healthy: issues.length === 0,
    issues,
    expectedTargetDate,
    observedTargetDate: payload.manifest.targetDate,
    latestDrawDate: payload.freshness.latestDrawDate,
    latestPayloadDate: payload.latest.date,
    rangeEndDate: payload.range.to,
    generatedAt: payload.generatedAt,
    datasetVersion: payload.manifest.datasetVersion,
    drawCount: payload.drawCount,
    resultCount: payload.resultCount,
    objectBytes: object.size,
    v2Required: v2.v2Required,
    v2Source: v2.v2Source,
    v2ReleaseId: v2.v2ReleaseId,
    v2GeneratedAt: v2.v2GeneratedAt,
    v2ObjectBytes: v2.v2ObjectBytes,
  };
}

export async function evaluateLotteryHealth(
  env: Env,
  options: HealthEvaluationOptions = {},
): Promise<LotteryHealthReport> {
  const now = options.now ?? Date.now();
  assertEpochMilliseconds(now);
  const expectedTargetDate = options.expectedTargetDate ?? expectedLotteryTargetDate(
    now,
    options.targetRolloverMinute,
  );
  assertIsoDate(expectedTargetDate, "expectedTargetDate");
  const maximumTargetDate = vietnamClock(now).date;
  const requirement = await v2Requirement(env.LOTTERY_DATA);

  // Evaluate sequentially so the largest regional payloads do not all remain
  // materialized in the Worker's memory at the same time.
  const entries: Array<[LotteryRegion, LotteryRegionHealth]> = [];
  for (const region of LOTTERY_REGIONS) {
    entries.push([
      region,
      await evaluateRegion(env.LOTTERY_DATA, region, expectedTargetDate, maximumTargetDate, requirement),
    ]);
  }
  const regions = Object.fromEntries(entries) as Record<LotteryRegion, LotteryRegionHealth>;

  return {
    schemaVersion: 1,
    service: "lottery-serving-data",
    checkedAt: new Date(now).toISOString(),
    expectedTargetDate,
    v2Required: requirement.required,
    healthy: LOTTERY_REGIONS.every((region) => regions[region].healthy),
    regions,
  };
}

export async function handleLotteryHealthRequest(
  request: Request,
  env: Env,
  options: HealthEvaluationOptions = {},
): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return Response.json(
      { error: "method_not_allowed" },
      { status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD" } },
    );
  }

  const report = await evaluateLotteryHealth(env, options);
  const response = Response.json(report, {
    status: report.healthy ? 200 : 503,
    headers: JSON_HEADERS,
  });
  return request.method === "HEAD" ? new Response(null, response) : response;
}
