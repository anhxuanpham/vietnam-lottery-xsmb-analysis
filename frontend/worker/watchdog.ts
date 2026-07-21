import {
  DEFAULT_TARGET_ROLLOVER_MINUTE,
  evaluateLotteryHealth,
  previousIsoDate,
  vietnamClock,
  type LotteryHealthReport,
} from "./health.ts";
import {
  readWatchdogState,
  writeWatchdogIncident,
  writeWatchdogLedger,
  writeWatchdogState,
  type WatchdogIncidentRecord,
  type WatchdogIncidentSeverity,
  type WatchdogLedgerRecord,
  type WatchdogNotificationRecord,
  type WatchdogState,
} from "./ops-ledger.ts";

export const DEFAULT_WARNING_START_MINUTE = DEFAULT_TARGET_ROLLOVER_MINUTE;
export const DEFAULT_CRITICAL_START_MINUTE = 20 * 60 + 30;

export type WatchdogWindow = "pre_warning" | "warning" | "critical";
export type WatchdogObservedStatus = "healthy" | "pending" | WatchdogIncidentSeverity;

export type WatchdogPolicy = {
  warningStartMinute?: number;
  criticalStartMinute?: number;
};

export type WatchdogLogEntry = Record<string, unknown>;
export type WatchdogLogger = {
  info(entry: WatchdogLogEntry): void;
  warn(entry: WatchdogLogEntry): void;
  error(entry: WatchdogLogEntry): void;
};

export type WatchdogRunOptions = WatchdogPolicy & {
  now?: number;
  fetcher?: typeof fetch;
  incidentIdFactory?: () => string;
  logger?: WatchdogLogger;
};

export type WatchdogRunResult = {
  scheduledAt: string;
  checkedAt: string;
  queueDelaySeconds: number;
  expectedTargetDate: string;
  window: WatchdogWindow;
  observedStatus: WatchdogObservedStatus;
  health: LotteryHealthReport;
  notification: WatchdogNotificationRecord;
  stateKey: string;
  ledgerKey: string;
  incidentKey: string | null;
};

type NotificationPlan = {
  event: "alert" | "recovery";
  severity: WatchdogIncidentSeverity | "healthy";
  incidentId: string;
  dedupeKey: string;
} | null;

type StateTransition = {
  state: WatchdogState;
  notification: NotificationPlan;
};

type ActiveIncidentState = WatchdogState & {
  status: WatchdogIncidentSeverity;
  incidentId: string;
};

const DEFAULT_LOGGER: WatchdogLogger = {
  info(entry) {
    console.info(entry);
  },
  warn(entry) {
    console.warn(entry);
  },
  error(entry) {
    console.error(entry);
  },
};

export class AlertDeliveryError extends Error {
  constructor() {
    super("lottery watchdog alert delivery failed");
    this.name = "AlertDeliveryError";
  }
}

function assertMinute(value: number, label: string): void {
  if (!Number.isInteger(value) || value < 0 || value >= 24 * 60) {
    throw new RangeError(`${label} must be an integer from 0 through 1439`);
  }
}

export function watchdogSchedule(
  now: number,
  policy: WatchdogPolicy = {},
): { expectedTargetDate: string; window: WatchdogWindow } {
  const warningStartMinute = policy.warningStartMinute ?? DEFAULT_WARNING_START_MINUTE;
  const criticalStartMinute = policy.criticalStartMinute ?? DEFAULT_CRITICAL_START_MINUTE;
  assertMinute(warningStartMinute, "warningStartMinute");
  assertMinute(criticalStartMinute, "criticalStartMinute");
  if (criticalStartMinute <= warningStartMinute) {
    throw new RangeError("criticalStartMinute must be after warningStartMinute");
  }

  const clock = vietnamClock(now);
  if (clock.minuteOfDay < warningStartMinute) {
    return { expectedTargetDate: previousIsoDate(clock.date), window: "pre_warning" };
  }
  if (clock.minuteOfDay < criticalStartMinute) {
    return { expectedTargetDate: clock.date, window: "warning" };
  }
  return { expectedTargetDate: clock.date, window: "critical" };
}

function observedStatus(healthy: boolean, window: WatchdogWindow): WatchdogObservedStatus {
  if (healthy) return "healthy";
  return window === "pre_warning" ? "pending" : window;
}

function freshState(
  status: WatchdogState["status"],
  expectedTargetDate: string,
  checkedAt: string,
): WatchdogState {
  return {
    schemaVersion: 1,
    status,
    expectedTargetDate,
    incidentId: null,
    openedAt: null,
    lastObservedAt: checkedAt,
    notifiedSeverity: null,
  };
}

function notificationKey(
  incidentId: string,
  event: "alert" | "recovery",
  severity: WatchdogIncidentSeverity | "healthy",
): string {
  return `${incidentId}:${event}:${severity}`;
}

function activeIncident(state: WatchdogState | null): ActiveIncidentState | null {
  if (!state || state.incidentId === null) return null;
  if (state.status !== "warning" && state.status !== "critical") return null;
  return { ...state, status: state.status, incidentId: state.incidentId };
}

function transitionState(
  previous: WatchdogState | null,
  status: WatchdogObservedStatus,
  expectedTargetDate: string,
  checkedAt: string,
  incidentIdFactory: () => string,
): StateTransition {
  const previousIncident = activeIncident(previous);

  if (status === "healthy") {
    const notification = previousIncident && previousIncident.notifiedSeverity !== null
      ? {
          event: "recovery" as const,
          severity: "healthy" as const,
          incidentId: previousIncident.incidentId,
          dedupeKey: notificationKey(previousIncident.incidentId, "recovery", "healthy"),
        }
      : null;
    return { state: freshState("healthy", expectedTargetDate, checkedAt), notification };
  }

  if (status === "pending") {
    if (previousIncident) {
      return {
        state: { ...previousIncident, lastObservedAt: checkedAt },
        notification: null,
      };
    }
    return { state: freshState("pending", expectedTargetDate, checkedAt), notification: null };
  }

  const sameIncident = previousIncident?.expectedTargetDate === expectedTargetDate;
  const incidentId = sameIncident && previousIncident
    ? previousIncident.incidentId
    : incidentIdFactory();
  const openedAt = sameIncident && previousIncident ? previousIncident.openedAt : checkedAt;
  const notifiedSeverity = sameIncident && previousIncident
    ? previousIncident.notifiedSeverity
    : null;
  const mustNotify = notifiedSeverity === null ||
    (status === "critical" && notifiedSeverity === "warning");
  const state: WatchdogState = {
    schemaVersion: 1,
    status,
    expectedTargetDate,
    incidentId,
    openedAt,
    lastObservedAt: checkedAt,
    notifiedSeverity,
  };
  const notification = mustNotify
    ? {
        event: "alert" as const,
        severity: status,
        incidentId,
        dedupeKey: notificationKey(incidentId, "alert", status),
      }
    : null;
  return { state, notification };
}

function alertSummary(report: LotteryHealthReport): string {
  return Object.values(report.regions)
    .filter((region) => !region.healthy)
    .map((region) => `${region.region.toUpperCase()}: ${region.issues.join(",")}`)
    .join("; ");
}

function alertPayload(
  plan: Exclude<NotificationPlan, null>,
  report: LotteryHealthReport,
): Record<string, unknown> {
  const summary = plan.event === "recovery"
    ? `Lottery serving data recovered for ${report.expectedTargetDate}`
    : alertSummary(report);
  const text = plan.event === "recovery"
    ? `RECOVERY: ${summary}`
    : `${plan.severity.toUpperCase()}: lottery serving data unhealthy for ${report.expectedTargetDate}. ${summary}`;
  return {
    schemaVersion: 1,
    event: `lottery_watchdog_${plan.event}`,
    severity: plan.severity,
    incidentId: plan.incidentId,
    dedupeKey: plan.dedupeKey,
    checkedAt: report.checkedAt,
    expectedTargetDate: report.expectedTargetDate,
    summary,
    text,
    regions: report.regions,
  };
}

function webhookUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

async function deliverNotification(
  plan: NotificationPlan,
  report: LotteryHealthReport,
  webhook: string | undefined,
  fetcher: typeof fetch,
): Promise<WatchdogNotificationRecord> {
  if (!plan) {
    return {
      event: null,
      severity: null,
      dedupeKey: null,
      delivery: "not_required",
      httpStatus: null,
      failureCode: null,
    };
  }

  const base = {
    event: plan.event,
    severity: plan.severity,
    dedupeKey: plan.dedupeKey,
  };
  if (!webhook) {
    return { ...base, delivery: "disabled", httpStatus: null, failureCode: null };
  }

  const url = webhookUrl(webhook);
  if (!url) {
    return { ...base, delivery: "failed", httpStatus: null, failureCode: "invalid_url" };
  }

  try {
    const response = await fetcher(url, {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify(alertPayload(plan, report)),
    });
    if (response.body) await response.body.cancel();
    if (!response.ok) {
      return {
        ...base,
        delivery: "failed",
        httpStatus: response.status,
        failureCode: "http_error",
      };
    }
    return { ...base, delivery: "sent", httpStatus: response.status, failureCode: null };
  } catch {
    return { ...base, delivery: "failed", httpStatus: null, failureCode: "network_error" };
  }
}

function applyDelivery(
  state: WatchdogState,
  previous: WatchdogState | null,
  plan: NotificationPlan,
  notification: WatchdogNotificationRecord,
  checkedAt: string,
): WatchdogState {
  if (!plan) return state;
  if (notification.delivery !== "sent") {
    const previousIncident = activeIncident(previous);
    if (plan.event === "recovery" && previousIncident) {
      return { ...previousIncident, lastObservedAt: checkedAt };
    }
    return state;
  }
  if (plan.event === "alert" && plan.severity !== "healthy") {
    return { ...state, notifiedSeverity: plan.severity };
  }
  return state;
}

function logRun(
  logger: WatchdogLogger,
  ledger: WatchdogLedgerRecord,
  incidentId: string | null,
): void {
  const entry: WatchdogLogEntry = {
    event: "lottery_watchdog_run",
    scheduledAt: ledger.scheduledAt,
    checkedAt: ledger.checkedAt,
    queueDelaySeconds: ledger.queueDelaySeconds,
    expectedTargetDate: ledger.expectedTargetDate,
    window: ledger.window,
    observedStatus: ledger.observedStatus,
    healthy: ledger.health.healthy,
    incidentId,
    notificationEvent: ledger.notification.event,
    notificationDelivery: ledger.notification.delivery,
    notificationFailureCode: ledger.notification.failureCode,
  };
  if (ledger.notification.delivery === "failed") logger.error(entry);
  else if (ledger.observedStatus === "warning" || ledger.observedStatus === "critical") {
    logger.warn(entry);
  } else logger.info(entry);
}

export async function runLotteryWatchdog(
  controller: ScheduledController,
  env: Env,
  options: WatchdogRunOptions = {},
): Promise<WatchdogRunResult> {
  const now = options.now ?? Date.now();
  if (!Number.isFinite(controller.scheduledTime)) {
    throw new RangeError("scheduledTime must be finite epoch milliseconds");
  }
  const logger = options.logger ?? DEFAULT_LOGGER;
  const schedule = watchdogSchedule(now, options);
  const health = await evaluateLotteryHealth(env, {
    now,
    expectedTargetDate: schedule.expectedTargetDate,
  });
  const checkedAt = health.checkedAt;
  const scheduledAt = new Date(controller.scheduledTime).toISOString();
  const queueDelaySeconds = Math.max(0, Math.floor((now - controller.scheduledTime) / 1_000));
  const status = observedStatus(health.healthy, schedule.window);
  const previous = await readWatchdogState(env.LOTTERY_DATA);
  const transition = transitionState(
    previous,
    status,
    schedule.expectedTargetDate,
    checkedAt,
    options.incidentIdFactory ?? (() => crypto.randomUUID()),
  );
  const notification = await deliverNotification(
    transition.notification,
    health,
    env.ALERT_WEBHOOK_URL,
    options.fetcher ?? fetch,
  );
  const state = applyDelivery(
    transition.state,
    previous,
    transition.notification,
    notification,
    checkedAt,
  );
  const ledger: WatchdogLedgerRecord = {
    schemaVersion: 1,
    recordType: "watchdog_run",
    scheduledAt,
    checkedAt,
    queueDelaySeconds,
    expectedTargetDate: schedule.expectedTargetDate,
    window: schedule.window,
    observedStatus: status,
    health,
    notification,
  };

  await writeWatchdogState(env.LOTTERY_DATA, state);
  let incidentKey: string | null = null;
  if (transition.notification) {
    const incident: WatchdogIncidentRecord = {
      schemaVersion: 1,
      recordType: "watchdog_incident_event",
      incidentId: transition.notification.incidentId,
      event: transition.notification.event,
      severity: transition.notification.severity,
      expectedTargetDate: schedule.expectedTargetDate,
      checkedAt,
      notification,
      health,
    };
    incidentKey = await writeWatchdogIncident(env.LOTTERY_DATA, incident);
  }
  const ledgerKey = await writeWatchdogLedger(env.LOTTERY_DATA, ledger);
  logRun(logger, ledger, state.incidentId);

  const result: WatchdogRunResult = {
    scheduledAt,
    checkedAt,
    queueDelaySeconds,
    expectedTargetDate: schedule.expectedTargetDate,
    window: schedule.window,
    observedStatus: status,
    health,
    notification,
    stateKey: "ops/watchdog/state.json",
    ledgerKey,
    incidentKey,
  };
  if (notification.delivery === "failed") throw new AlertDeliveryError();
  return result;
}
