import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  evaluateLotteryHealth,
  expectedLotteryTargetDate,
  handleLotteryHealthRequest,
} from "../worker/health.ts";
import { WATCHDOG_STATE_KEY } from "../worker/ops-ledger.ts";
import {
  AlertDeliveryError,
  runLotteryWatchdog,
  watchdogSchedule,
} from "../worker/watchdog.ts";

const REGIONS = ["xsmb", "xsmn", "xsmt"];
const fixturePayloads = Object.fromEntries(
  await Promise.all(
    REGIONS.map(async (region) => [
      region,
      JSON.parse(await readFile(new URL(`../public/data/${region}-demo.json`, import.meta.url), "utf8")),
    ]),
  ),
);

function vietnamTime(date, hour, minute) {
  return Date.parse(`${date}T${String(hour - 7).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00Z`);
}

function servingPayload(region, targetDate) {
  const payload = structuredClone(fixturePayloads[region]);
  payload.generatedAt = `${targetDate}T13:00:00.000Z`;
  payload.manifest.targetDate = targetDate;
  payload.manifest.publishedAt = `${targetDate}T12:50:00.000Z`;
  payload.freshness.latestDrawDate = targetDate;
  payload.freshness.manifestTargetDate = targetDate;
  payload.freshness.matchesManifestTarget = true;
  payload.range.to = targetDate;
  payload.latest.date = targetDate;
  payload.latest.results.forEach((draw) => {
    draw.date = targetDate;
  });
  return payload;
}

function bodyFromText(key, text) {
  return {
    key,
    size: Buffer.byteLength(text),
    etag: "etag",
    httpEtag: '"etag"',
    uploaded: new Date("2026-07-21T13:00:00Z"),
    json: async () => JSON.parse(text),
  };
}

class MemoryBucket {
  constructor() {
    this.objects = new Map();
    this.gets = [];
    this.puts = [];
  }

  setJson(key, value) {
    this.objects.set(key, JSON.stringify(value));
  }

  setText(key, value) {
    this.objects.set(key, value);
  }

  async get(key) {
    this.gets.push(key);
    const value = this.objects.get(key);
    return value === undefined ? null : bodyFromText(key, value);
  }

  async put(key, value, options) {
    assert.equal(typeof value, "string");
    this.objects.set(key, value);
    this.puts.push({ key, value: JSON.parse(value), options });
    return { key, etag: `etag-${this.puts.length}` };
  }
}

function healthyBucket(targetDate) {
  const bucket = new MemoryBucket();
  for (const region of REGIONS) {
    bucket.setJson(`regions/${region}.json`, servingPayload(region, targetDate));
  }
  return bucket;
}

function scheduledController(scheduledTime) {
  return { scheduledTime, cron: "*/15 * * * *", noRetry() {} };
}

function collectingLogger(entries) {
  return {
    info(entry) {
      entries.push({ level: "info", entry });
    },
    warn(entry) {
      entries.push({ level: "warn", entry });
    },
    error(entry) {
      entries.push({ level: "error", entry });
    },
  };
}

test("uses yesterday before the warning window and today from the warning window onward", () => {
  const beforeWarning = vietnamTime("2026-07-21", 19, 59);
  const warning = vietnamTime("2026-07-21", 20, 0);
  const critical = vietnamTime("2026-07-21", 20, 30);

  assert.equal(expectedLotteryTargetDate(beforeWarning), "2026-07-20");
  assert.deepEqual(watchdogSchedule(beforeWarning), {
    expectedTargetDate: "2026-07-20",
    window: "pre_warning",
  });
  assert.deepEqual(watchdogSchedule(warning), {
    expectedTargetDate: "2026-07-21",
    window: "warning",
  });
  assert.deepEqual(watchdogSchedule(critical), {
    expectedTargetDate: "2026-07-21",
    window: "critical",
  });
});

test("health accepts an early publication for today before the 20:00 rollover", async () => {
  const now = vietnamTime("2026-07-21", 19, 50);
  const report = await evaluateLotteryHealth(
    { LOTTERY_DATA: healthyBucket("2026-07-21") },
    { now },
  );
  assert.equal(report.expectedTargetDate, "2026-07-20");
  assert.equal(report.healthy, true);
  assert.ok(REGIONS.every((region) => report.regions[region].observedTargetDate === "2026-07-21"));
});

test("health validates all three R2 objects and returns 200 only when every region is current", async () => {
  const now = vietnamTime("2026-07-21", 20, 45);
  const bucket = healthyBucket("2026-07-21");
  const env = { LOTTERY_DATA: bucket };

  const report = await evaluateLotteryHealth(env, { now });
  assert.equal(report.healthy, true);
  assert.equal(report.expectedTargetDate, "2026-07-21");
  assert.deepEqual(bucket.gets, [
    "v2/health/required.json",
    ...REGIONS.map((region) => `regions/${region}.json`),
  ]);
  for (const region of REGIONS) {
    assert.equal(report.regions[region].source, "r2");
    assert.equal(report.regions[region].healthy, true);
    assert.deepEqual(report.regions[region].issues, []);
  }

  const response = await handleLotteryHealthRequest(
    new Request("https://lottery.example/api/health/lottery"),
    env,
    { now },
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal((await response.json()).healthy, true);
});

test("v2 health activates only after the sentinel and then requires every regional release", async () => {
  const targetDate = "2026-07-19";
  const now = vietnamTime(targetDate, 20, 45);
  const bucket = healthyBucket(targetDate);
  bucket.setJson("v2/health/required.json", {
    schemaVersion: 1,
    required: true,
    activatedAt: `${targetDate}T13:00:00.000Z`,
    regions: REGIONS,
  });

  const missing = await evaluateLotteryHealth({ LOTTERY_DATA: bucket }, { now });
  assert.equal(missing.v2Required, true);
  assert.equal(missing.healthy, false);
  assert.deepEqual(missing.regions.xsmb.issues, ["v2_metadata_missing"]);

  for (const region of REGIONS) {
    const compact = JSON.parse(bucket.objects.get(`regions/${region}.json`));
    const releaseMetadata = {
      schemaVersion: 2,
      releaseId: compact.manifest.datasetVersion,
      region,
      source: "r2",
      generatedAt: compact.generatedAt,
      manifest: compact.manifest,
      freshness: compact.freshness,
      range: compact.range,
      drawCount: compact.drawCount,
      resultCount: compact.resultCount,
      shardKeyTemplate: `v2/releases/${compact.manifest.datasetVersion}/regions/${region}/stations/{stationCode}/years/{year}.json`,
      stations: compact.stations.map((station) => {
        const stationMetadata = { ...station };
        delete stationMetadata.fullFrequency;
        return {
          ...stationMetadata,
          years: Array.from(
            { length: Number(station.range.to.slice(0, 4)) - Number(station.range.from.slice(0, 4)) + 1 },
            (_, index) => Number(station.range.from.slice(0, 4)) + index,
          ),
        };
      }),
    };
    bucket.setJson(`v2/regions/${region}/latest.json`, releaseMetadata);
    for (const station of releaseMetadata.stations) {
      const year = station.years.at(-1);
      const draws = compact.draws.filter(
        (draw) => draw.stationCode === station.code && Number(draw.date.slice(0, 4)) === year,
      );
      bucket.setJson(
        `v2/releases/${releaseMetadata.releaseId}/regions/${region}/stations/${station.code}/years/${year}.json`,
        {
          schemaVersion: 2,
          releaseId: releaseMetadata.releaseId,
          region,
          station: { code: station.code, name: station.name },
          year,
          range: { from: draws[0].date, to: draws.at(-1).date },
          drawCount: draws.length,
          resultCount: draws.reduce((total, draw) => total + draw.numbers.length, 0),
          draws,
        },
      );
    }
  }

  const healthy = await evaluateLotteryHealth({ LOTTERY_DATA: bucket }, { now });
  assert.equal(healthy.healthy, true);
  assert.ok(REGIONS.every((region) => healthy.regions[region].v2Source === "r2"));
  assert.ok(REGIONS.every((region) => healthy.regions[region].v2ReleaseId !== null));

  const southernMetadata = JSON.parse(bucket.objects.get("v2/regions/xsmn/latest.json"));
  const southernStation = southernMetadata.stations[0];
  bucket.objects.delete(
    `v2/releases/${southernMetadata.releaseId}/regions/xsmn/stations/${southernStation.code}/years/${southernStation.years.at(-1)}.json`,
  );
  const missingShard = await evaluateLotteryHealth({ LOTTERY_DATA: bucket }, { now });
  assert.equal(missingShard.healthy, false);
  assert.ok(missingShard.regions.xsmn.issues.includes("v2_shard_missing"));
});

test("health returns structured 503 for stale, missing, and malformed regional data", async () => {
  const now = vietnamTime("2026-07-21", 20, 45);
  const bucket = healthyBucket("2026-07-21");
  bucket.setJson("regions/xsmn.json", servingPayload("xsmn", "2026-07-20"));
  bucket.objects.delete("regions/xsmt.json");
  bucket.setText("regions/xsmb.json", "{");

  const response = await handleLotteryHealthRequest(
    new Request("https://lottery.example/api/health/lottery"),
    { LOTTERY_DATA: bucket },
    { now },
  );
  const report = await response.json();

  assert.equal(response.status, 503);
  assert.equal(report.healthy, false);
  assert.deepEqual(report.regions.xsmb.issues, ["invalid_json"]);
  assert.equal(report.regions.xsmn.source, "r2");
  assert.ok(report.regions.xsmn.issues.includes("target_date_mismatch"));
  assert.deepEqual(report.regions.xsmt.issues, ["object_missing"]);
  assert.equal(Object.hasOwn(report.regions.xsmb, "error"), false);
});

test("watchdog deduplicates warnings, escalates critical, records evidence, and sends recovery", async () => {
  const targetDate = "2026-07-21";
  const bucket = healthyBucket(targetDate);
  bucket.setJson("regions/xsmt.json", servingPayload("xsmt", "2026-07-20"));
  const webhook = "https://alerts.example/secret-path-value";
  const calls = [];
  const logs = [];
  const fetcher = async (url, init) => {
    calls.push({ url: url.toString(), body: JSON.parse(init.body) });
    return new Response(null, { status: 204 });
  };
  const env = { LOTTERY_DATA: bucket, ALERT_WEBHOOK_URL: webhook };
  const common = {
    fetcher,
    logger: collectingLogger(logs),
    incidentIdFactory: () => "incident-fixed",
  };

  const warningNow = vietnamTime(targetDate, 20, 15);
  const warning = await runLotteryWatchdog(
    scheduledController(warningNow - 70 * 60_000),
    env,
    { ...common, now: warningNow },
  );
  assert.equal(warning.observedStatus, "warning");
  assert.equal(warning.queueDelaySeconds, 4_200);
  assert.equal(warning.notification.delivery, "sent");
  assert.equal(warning.notification.dedupeKey, "incident-fixed:alert:warning");

  const duplicate = await runLotteryWatchdog(
    scheduledController(warningNow + 10 * 60_000),
    env,
    { ...common, now: warningNow + 10 * 60_000 },
  );
  assert.equal(duplicate.notification.delivery, "not_required");

  const criticalNow = vietnamTime(targetDate, 20, 45);
  const critical = await runLotteryWatchdog(
    scheduledController(criticalNow),
    env,
    { ...common, now: criticalNow },
  );
  assert.equal(critical.observedStatus, "critical");
  assert.equal(critical.notification.dedupeKey, "incident-fixed:alert:critical");

  bucket.setJson("regions/xsmt.json", servingPayload("xsmt", targetDate));
  const recovered = await runLotteryWatchdog(
    scheduledController(criticalNow + 15 * 60_000),
    env,
    { ...common, now: criticalNow + 15 * 60_000 },
  );
  assert.equal(recovered.observedStatus, "healthy");
  assert.equal(recovered.notification.dedupeKey, "incident-fixed:recovery:healthy");

  assert.equal(calls.length, 3);
  assert.deepEqual(calls.map((call) => call.body.event), [
    "lottery_watchdog_alert",
    "lottery_watchdog_alert",
    "lottery_watchdog_recovery",
  ]);
  assert.equal(bucket.puts.filter((put) => put.key.includes("/ledger/")).length, 4);
  assert.equal(bucket.puts.filter((put) => put.key.includes("/incidents/")).length, 3);
  const stateWrites = bucket.puts.filter((put) => put.key === WATCHDOG_STATE_KEY);
  assert.equal(stateWrites.at(-1).value.status, "healthy");
  assert.equal(JSON.stringify(logs).includes(webhook), false);
  assert.equal(JSON.stringify(logs).includes("secret-path-value"), false);
  assert.equal(JSON.stringify(bucket.puts).includes(webhook), false);
});

test("watchdog keeps failed recovery state retryable and never persists the webhook secret", async () => {
  const targetDate = "2026-07-21";
  const bucket = healthyBucket(targetDate);
  bucket.setJson("regions/xsmb.json", servingPayload("xsmb", "2026-07-20"));
  const webhook = "https://alerts.example/top-secret";
  const warningNow = vietnamTime(targetDate, 20, 15);
  const env = { LOTTERY_DATA: bucket, ALERT_WEBHOOK_URL: webhook };

  await runLotteryWatchdog(scheduledController(warningNow), env, {
    now: warningNow,
    incidentIdFactory: () => "recovery-retry",
    fetcher: async () => new Response(null, { status: 204 }),
    logger: collectingLogger([]),
  });

  bucket.setJson("regions/xsmb.json", servingPayload("xsmb", targetDate));
  const recoveryNow = warningNow + 15 * 60_000;
  await assert.rejects(
    runLotteryWatchdog(scheduledController(recoveryNow), env, {
      now: recoveryNow,
      fetcher: async () => new Response(null, { status: 503 }),
      logger: collectingLogger([]),
    }),
    AlertDeliveryError,
  );

  const storedState = JSON.parse(bucket.objects.get(WATCHDOG_STATE_KEY));
  assert.equal(storedState.incidentId, "recovery-retry");
  assert.equal(storedState.status, "warning");
  assert.equal(JSON.stringify([...bucket.objects]).includes(webhook), false);
  assert.equal(bucket.puts.filter((put) => put.key.includes("/ledger/")).length, 2);
});

test("watchdog stores pending evidence without sending before warning time", async () => {
  const now = vietnamTime("2026-07-21", 12, 0);
  const bucket = healthyBucket("2026-07-19");
  let fetchCount = 0;
  const result = await runLotteryWatchdog(
    scheduledController(now),
    { LOTTERY_DATA: bucket, ALERT_WEBHOOK_URL: "https://alerts.example/not-called" },
    {
      now,
      fetcher: async () => {
        fetchCount += 1;
        return new Response(null, { status: 204 });
      },
      incidentIdFactory: () => "not-created",
      logger: collectingLogger([]),
    },
  );

  assert.equal(result.expectedTargetDate, "2026-07-20");
  assert.equal(result.observedStatus, "pending");
  assert.equal(result.notification.delivery, "not_required");
  assert.equal(fetchCount, 0);
  assert.ok(result.ledgerKey.includes("ops/watchdog/ledger/"));
  assert.equal(result.incidentKey, null);
});
