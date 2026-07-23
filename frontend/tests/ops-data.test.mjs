import assert from "node:assert/strict";
import test from "node:test";

import {
  fetchLotteryOperations,
  normalizeLotteryServingHealth,
} from "../ops-data.ts";

function healthPayload(healthy = true) {
  return {
    schemaVersion: 1,
    service: "lottery-serving-data",
    checkedAt: "2026-07-23T13:30:00.000Z",
    expectedTargetDate: "2026-07-23",
    v2Required: true,
    healthy,
    regions: Object.fromEntries(
      ["xsmb", "xsmn", "xsmt"].map((region) => [
        region,
        {
          healthy,
          issues: healthy ? [] : ["target_date_mismatch"],
          observedTargetDate: healthy ? "2026-07-23" : "2026-07-22",
          latestDrawDate: healthy ? "2026-07-23" : "2026-07-22",
          datasetVersion: `${region}-release`,
        },
      ]),
    ),
  };
}

function watchdogPayload() {
  return {
    schemaVersion: 1,
    service: "lottery-watchdog",
    available: true,
    state: {
      status: "healthy",
      expectedTargetDate: "2026-07-23",
      lastObservedAt: "2026-07-23T13:30:00.000Z",
      activeIncident: false,
      openedAt: null,
      notifiedSeverity: null,
    },
  };
}

test("normalizes a complete three-region health report", () => {
  const payload = healthPayload();
  assert.deepEqual(normalizeLotteryServingHealth(payload), payload);
  assert.equal(normalizeLotteryServingHealth({ ...payload, regions: { xsmb: payload.regions.xsmb } }), null);
});

test("accepts a structured unhealthy 503 and combines watchdog evidence", async () => {
  const fetcher = async (url) => {
    if (url === "/api/health/lottery") {
      return Response.json(healthPayload(false), { status: 503 });
    }
    return Response.json(watchdogPayload());
  };

  const result = await fetchLotteryOperations({ fetcher });
  assert.equal(result.health.healthy, false);
  assert.equal(result.watchdog.state.status, "healthy");
});

test("keeps serving health when watchdog evidence is unavailable", async () => {
  const fetcher = async (url) => {
    if (url === "/api/health/lottery") return Response.json(healthPayload());
    return new Response(null, { status: 503 });
  };

  const result = await fetchLotteryOperations({ fetcher });
  assert.equal(result.health.healthy, true);
  assert.equal(result.watchdog, null);
});

test("rejects invalid health responses", async () => {
  await assert.rejects(
    fetchLotteryOperations({
      fetcher: async () => Response.json({ healthy: true }),
    }),
    /invalid payload/,
  );
});
