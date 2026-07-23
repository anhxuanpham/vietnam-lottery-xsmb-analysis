/** Read-only, redacted status surface for the scheduled lottery watchdog. */
import type { LotteryWatchdogStatus } from "../lottery-contract.ts";
import { readWatchdogState } from "./ops-ledger.ts";

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "x-lottery-source": "r2",
};

function jsonResponse(body: LotteryWatchdogStatus | { error: string }, status: number, headers?: HeadersInit): Response {
  return Response.json(body, {
    status,
    headers: { ...JSON_HEADERS, ...headers },
  });
}

export async function handleLotteryWatchdogStatus(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return jsonResponse({ error: "method_not_allowed" }, 405, { allow: "GET" });
  }

  const state = await readWatchdogState(env.LOTTERY_DATA);
  const body: LotteryWatchdogStatus = {
    schemaVersion: 1,
    service: "lottery-watchdog",
    available: state !== null,
    state: state
      ? {
          status: state.status,
          expectedTargetDate: state.expectedTargetDate,
          lastObservedAt: state.lastObservedAt,
          activeIncident: state.incidentId !== null,
          openedAt: state.openedAt,
          notifiedSeverity: state.notifiedSeverity,
        }
      : null,
  };
  return jsonResponse(body, 200);
}
