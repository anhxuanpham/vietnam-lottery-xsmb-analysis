import type { LotteryHealthReport } from "./health.ts";

export const WATCHDOG_STATE_KEY = "ops/watchdog/state.json";

const JSON_HTTP_METADATA = {
  contentType: "application/json; charset=utf-8",
  cacheControl: "no-store",
};
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const INCIDENT_ID_PATTERN = /^[A-Za-z0-9._-]{1,128}$/;

export type WatchdogIncidentSeverity = "warning" | "critical";
export type WatchdogStateStatus = "healthy" | "pending" | WatchdogIncidentSeverity;

export type WatchdogState = {
  schemaVersion: 1;
  status: WatchdogStateStatus;
  expectedTargetDate: string;
  incidentId: string | null;
  openedAt: string | null;
  lastObservedAt: string;
  notifiedSeverity: WatchdogIncidentSeverity | null;
};

export type WatchdogNotificationRecord = {
  event: "alert" | "recovery" | null;
  severity: WatchdogIncidentSeverity | "healthy" | null;
  dedupeKey: string | null;
  delivery: "not_required" | "disabled" | "sent" | "failed";
  httpStatus: number | null;
  failureCode: "invalid_url" | "network_error" | "http_error" | null;
};

export type WatchdogLedgerRecord = {
  schemaVersion: 1;
  recordType: "watchdog_run";
  scheduledAt: string;
  checkedAt: string;
  queueDelaySeconds: number;
  expectedTargetDate: string;
  window: "pre_warning" | "warning" | "critical";
  observedStatus: "healthy" | "pending" | WatchdogIncidentSeverity;
  health: LotteryHealthReport;
  notification: WatchdogNotificationRecord;
};

export type WatchdogIncidentRecord = {
  schemaVersion: 1;
  recordType: "watchdog_incident_event";
  incidentId: string;
  event: "alert" | "recovery";
  severity: WatchdogIncidentSeverity | "healthy";
  expectedTargetDate: string;
  checkedAt: string;
  notification: WatchdogNotificationRecord;
  health: LotteryHealthReport;
};

function isIsoTimestamp(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function isWatchdogState(value: unknown): value is WatchdogState {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const state = value as Partial<WatchdogState>;
  const validStatus = state.status === "healthy" || state.status === "pending" ||
    state.status === "warning" || state.status === "critical";
  const validSeverity = state.notifiedSeverity === null || state.notifiedSeverity === "warning" ||
    state.notifiedSeverity === "critical";
  return state.schemaVersion === 1 && validStatus && validSeverity &&
    typeof state.expectedTargetDate === "string" && DATE_PATTERN.test(state.expectedTargetDate) &&
    (state.incidentId === null ||
      (typeof state.incidentId === "string" && INCIDENT_ID_PATTERN.test(state.incidentId))) &&
    (state.openedAt === null || isIsoTimestamp(state.openedAt)) && isIsoTimestamp(state.lastObservedAt);
}

async function putJson(
  bucket: R2Bucket,
  key: string,
  value: unknown,
  customMetadata: Record<string, string>,
): Promise<void> {
  await bucket.put(key, JSON.stringify(value), {
    httpMetadata: JSON_HTTP_METADATA,
    customMetadata,
  });
}

function timestampKey(timestamp: string): string {
  return timestamp.replaceAll(":", "-").replaceAll(".", "-");
}

export async function readWatchdogState(bucket: R2Bucket): Promise<WatchdogState | null> {
  try {
    const object = await bucket.get(WATCHDOG_STATE_KEY);
    if (!object) return null;
    const value = await object.json<unknown>();
    return isWatchdogState(value) ? value : null;
  } catch {
    return null;
  }
}

export async function writeWatchdogState(bucket: R2Bucket, state: WatchdogState): Promise<void> {
  await putJson(bucket, WATCHDOG_STATE_KEY, state, {
    recordType: "watchdog_state",
    schemaVersion: "1",
    status: state.status,
  });
}

export async function writeWatchdogLedger(
  bucket: R2Bucket,
  record: WatchdogLedgerRecord,
): Promise<string> {
  const date = record.checkedAt.slice(0, 10);
  const key = `ops/watchdog/ledger/date=${date}/scheduled=${timestampKey(record.scheduledAt)}.json`;
  await putJson(bucket, key, record, {
    recordType: record.recordType,
    schemaVersion: "1",
    observedStatus: record.observedStatus,
    expectedTargetDate: record.expectedTargetDate,
  });
  return key;
}

export async function writeWatchdogIncident(
  bucket: R2Bucket,
  record: WatchdogIncidentRecord,
): Promise<string> {
  const key = `ops/watchdog/incidents/${record.incidentId}/${timestampKey(record.checkedAt)}-${record.event}.json`;
  await putJson(bucket, key, record, {
    recordType: record.recordType,
    schemaVersion: "1",
    event: record.event,
    severity: record.severity,
  });
  return key;
}
