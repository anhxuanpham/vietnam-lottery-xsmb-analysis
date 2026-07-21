import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  handleLotteryV2Ingest,
  handleLotteryV2Metadata,
  handleLotteryV2Results,
  lotteryV2ShardKey,
} from "../worker/lottery-v2.ts";

const v1 = JSON.parse(await readFile(new URL("../public/data/xsmb-demo.json", import.meta.url), "utf8"));
const releaseId = "release-test-1";
const stationCode = "xsmb";
const sampleDraws = v1.draws.slice(-4);
const year = Number(sampleDraws[0].date.slice(0, 4));
const metadata = {
  schemaVersion: 2,
  releaseId,
  region: "xsmb",
  source: "r2",
  generatedAt: "2026-07-21T12:00:00Z",
  manifest: {
    ...v1.manifest,
    datasetVersion: releaseId,
  },
  freshness: v1.freshness,
  range: { from: sampleDraws[0].date, to: sampleDraws.at(-1).date },
  drawCount: sampleDraws.length,
  resultCount: sampleDraws.length * 27,
  shardKeyTemplate: `v2/releases/${releaseId}/regions/xsmb/stations/{stationCode}/years/{year}.json`,
  stations: [
    {
      code: stationCode,
      name: "Miền Bắc",
      url: null,
      range: { from: sampleDraws[0].date, to: sampleDraws.at(-1).date },
      drawCount: sampleDraws.length,
      resultCount: sampleDraws.length * 27,
      years: [year],
    },
  ],
};
const shard = {
  schemaVersion: 2,
  releaseId,
  region: "xsmb",
  station: { code: stationCode, name: "Miền Bắc" },
  year,
  range: metadata.range,
  drawCount: sampleDraws.length,
  resultCount: sampleDraws.length * 27,
  draws: sampleDraws,
};

function r2Object(value, etag = "etag-readonly") {
  const encoded = JSON.stringify(value);
  return {
    etag,
    size: new TextEncoder().encode(encoded).byteLength,
    json: async () => value,
    text: async () => encoded,
  };
}

function ingestEnvironment() {
  const objects = new Map();
  const etags = new Map();
  let revision = 0;
  let beforeMetadataPut = null;
  let throwAfterMetadataPut = false;
  const publishedBoundary = structuredClone(v1);
  publishedBoundary.generatedAt = metadata.generatedAt;
  publishedBoundary.manifest = metadata.manifest;
  publishedBoundary.freshness = metadata.freshness;
  publishedBoundary.range = metadata.range;
  publishedBoundary.drawCount = metadata.drawCount;
  publishedBoundary.resultCount = metadata.resultCount;
  const metadataKey = "v2/regions/xsmb/latest.json";
  const state = {
    objects,
    etags,
    publishedBoundary,
    pauseNextMetadataPut(hook) {
      beforeMetadataPut = hook;
    },
    throwAfterNextMetadataPut() {
      throwAfterMetadataPut = true;
    },
    seed(key, value) {
      objects.set(key, JSON.stringify(value));
      etags.set(key, `etag-${++revision}`);
    },
    env: {
      DASHBOARD_INGEST_TOKEN: "v2-test-token",
      LOTTERY_DATA: {
        get: async (key) => {
          if (key === "regions/xsmb.json") return r2Object(publishedBoundary);
          return objects.has(key)
            ? r2Object(JSON.parse(objects.get(key)), etags.get(key) ?? `etag-${++revision}`)
            : null;
        },
        put: async (key, value, options) => {
          if (key === metadataKey && beforeMetadataPut) {
            const hook = beforeMetadataPut;
            beforeMetadataPut = null;
            await hook();
          }
          if (options?.onlyIf?.etagDoesNotMatch === "*" && objects.has(key)) return null;
          if (options?.onlyIf?.etagMatches !== undefined &&
            etags.get(key) !== options.onlyIf.etagMatches) return null;
          objects.set(key, String(value));
          const etag = `etag-${++revision}`;
          etags.set(key, etag);
          if (key === metadataKey && throwAfterMetadataPut) {
            throwAfterMetadataPut = false;
            throw new Error("simulated lost R2 response");
          }
          return { etag };
        },
      },
    },
  };
  return state;
}

function environment({ includeShard = true } = {}) {
  const objects = new Map([
    ["v2/regions/xsmb/latest.json", metadata],
  ]);
  if (includeShard) objects.set(lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard);
  return {
    LOTTERY_DATA: {
      get: async (key) => objects.has(key) ? r2Object(objects.get(key)) : null,
    },
  };
}

test("v2 metadata endpoint returns a bounded live release contract", async () => {
  const request = new Request("https://example.test/api/v2/lottery?region=xsmb");
  const response = await handleLotteryV2Metadata(request, environment(), new URL(request.url));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-lottery-source"), "r2");
  assert.ok((await response.clone().arrayBuffer()).byteLength < 100 * 1024);
  assert.equal((await response.json()).releaseId, releaseId);
});

test("v2 results filters exactly and paginates with a stable cursor", async () => {
  const firstUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=1`,
  );
  const first = await handleLotteryV2Results(new Request(firstUrl), environment(), firstUrl);
  assert.equal(first.status, 200);
  assert.ok((await first.clone().arrayBuffer()).byteLength < 250 * 1024);
  const firstPage = await first.json();
  assert.equal(firstPage.items.length, 1);
  assert.ok(firstPage.page.nextCursor);

  const secondUrl = new URL(firstUrl);
  secondUrl.searchParams.set("cursor", firstPage.page.nextCursor);
  const second = await handleLotteryV2Results(new Request(secondUrl), environment(), secondUrl);
  const secondPage = await second.json();
  assert.equal(second.status, 200);
  assert.notEqual(secondPage.items[0].date, firstPage.items[0].date);

  const exact = sampleDraws[1];
  const exactUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&from=${exact.date}&to=${exact.date}&number=${exact.numbers[0]}`,
  );
  const exactResponse = await handleLotteryV2Results(new Request(exactUrl), environment(), exactUrl);
  const exactPage = await exactResponse.json();
  assert.equal(exactResponse.status, 200);
  assert.deepEqual(exactPage.items.map((item) => item.date), [exact.date]);
  assert.ok(exactPage.items[0].numbers.includes(exact.numbers[0]));
});

test("v2 cursor pages read only the newest shard needed for the requested window", async () => {
  const expanded = structuredClone(metadata);
  expanded.range.from = "2024-01-01";
  expanded.drawCount = 6;
  expanded.resultCount = 6 * 27;
  expanded.stations[0].range.from = "2024-01-01";
  expanded.stations[0].drawCount = 6;
  expanded.stations[0].resultCount = 6 * 27;
  expanded.stations[0].years = [2024, 2025, year];
  const gets = [];
  const objects = new Map([
    ["v2/regions/xsmb/latest.json", expanded],
    [lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard],
  ]);
  const env = {
    LOTTERY_DATA: {
      get: async (key) => {
        gets.push(key);
        return objects.has(key) ? r2Object(objects.get(key)) : null;
      },
    },
  };
  const url = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=1`,
  );
  const response = await handleLotteryV2Results(new Request(url), env, url);
  assert.equal(response.status, 200);
  assert.deepEqual(gets, [
    "v2/regions/xsmb/latest.json",
    lotteryV2ShardKey(releaseId, "xsmb", stationCode, year),
  ]);
});

test("v2 results validates query parameters and release completeness", async () => {
  const invalidUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=101`,
  );
  const invalid = await handleLotteryV2Results(new Request(invalidUrl), environment(), invalidUrl);
  assert.equal(invalid.status, 400);
  assert.equal((await invalid.json()).error, "invalid_limit");

  const missingUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}`,
  );
  const missing = await handleLotteryV2Results(
    new Request(missingUrl),
    environment({ includeShard: false }),
    missingUrl,
  );
  assert.equal(missing.status, 503);
  assert.equal((await missing.json()).error, "release_invalid");
});

test("v2 ingest authenticates, keeps shards immutable, and publishes metadata last", async () => {
  const state = ingestEnvironment();
  const shardUrl = new URL(
    `https://example.test/api/admin/lottery-v2?kind=shard&region=xsmb&release=${releaseId}&station=${stationCode}&year=${year}`,
  );
  const unauthorized = await handleLotteryV2Ingest(
    new Request(shardUrl, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(shard) }),
    state.env,
    shardUrl,
  );
  assert.equal(unauthorized.status, 401);
  assert.equal(state.objects.size, 0);

  const upload = () => handleLotteryV2Ingest(
    new Request(shardUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(shard),
    }),
    state.env,
    shardUrl,
  );
  const saved = await upload();
  assert.equal(saved.status, 200);
  assert.equal((await saved.json()).immutable, true);
  const repeated = await upload();
  assert.equal(repeated.status, 200);
  assert.equal((await repeated.json()).idempotent, true);

  const changedShard = structuredClone(shard);
  changedShard.draws[0].specialPrize = "00000";
  changedShard.draws[0].specialTail = "00";
  changedShard.draws[0].numbers[0] = "00";
  changedShard.draws[0].prizes.special = ["00000"];
  const conflict = await handleLotteryV2Ingest(
    new Request(shardUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(changedShard),
    }),
    state.env,
    shardUrl,
  );
  assert.equal(conflict.status, 409);

  const metadataUrl = new URL("https://example.test/api/admin/lottery-v2?kind=metadata&region=xsmb");
  const latest = await handleLotteryV2Ingest(
    new Request(metadataUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(metadata),
    }),
    state.env,
    metadataUrl,
  );
  assert.equal(latest.status, 200);
  assert.ok(state.objects.has("v2/regions/xsmb/latest.json"));
  const repeatedLatest = await handleLotteryV2Ingest(
    new Request(metadataUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(metadata),
    }),
    state.env,
    metadataUrl,
  );
  assert.equal(repeatedLatest.status, 200);
  assert.equal((await repeatedLatest.json()).idempotent, true);
});

test("v2 metadata publication rejects a declared release until every shard exists", async () => {
  const state = ingestEnvironment();
  const metadataUrl = new URL("https://example.test/api/admin/lottery-v2?kind=metadata&region=xsmb");
  const response = await handleLotteryV2Ingest(
    new Request(metadataUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(metadata),
    }),
    state.env,
    metadataUrl,
  );
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "incomplete_release");
  assert.equal(state.objects.has("v2/regions/xsmb/latest.json"), false);
});

test("v2 metadata publication refuses to roll the latest pointer backwards", async () => {
  const state = ingestEnvironment();
  const newer = structuredClone(metadata);
  newer.releaseId = "release-test-newer";
  newer.manifest.datasetVersion = newer.releaseId;
  newer.manifest.publishedAt = "2026-07-21T13:00:00Z";
  newer.shardKeyTemplate = `v2/releases/${newer.releaseId}/regions/xsmb/stations/{stationCode}/years/{year}.json`;
  state.seed("v2/regions/xsmb/latest.json", newer);

  const metadataUrl = new URL("https://example.test/api/admin/lottery-v2?kind=metadata&region=xsmb");
  const response = await handleLotteryV2Ingest(
    new Request(metadataUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(metadata),
    }),
    state.env,
    metadataUrl,
  );
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, "stale_release");
  assert.equal(JSON.parse(state.objects.get("v2/regions/xsmb/latest.json")).releaseId, newer.releaseId);
});

test("v2 metadata CAS prevents an older validated request from overwriting a newer release", async () => {
  const state = ingestEnvironment();
  state.seed(lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard);

  let releasePaused;
  const paused = new Promise((resolve) => {
    state.pauseNextMetadataPut(() => new Promise((resume) => {
      releasePaused = resume;
      resolve();
    }));
  });
  const metadataUrl = new URL("https://example.test/api/admin/lottery-v2?kind=metadata&region=xsmb");
  const publish = (value) => handleLotteryV2Ingest(
    new Request(metadataUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(value),
    }),
    state.env,
    metadataUrl,
  );

  const olderRequest = publish(metadata);
  await paused;

  const newer = structuredClone(metadata);
  newer.releaseId = "release-test-newer";
  newer.generatedAt = "2026-07-21T13:00:00Z";
  newer.manifest.datasetVersion = newer.releaseId;
  newer.manifest.publishedAt = newer.generatedAt;
  newer.shardKeyTemplate = `v2/releases/${newer.releaseId}/regions/xsmb/stations/{stationCode}/years/{year}.json`;
  const newerShard = structuredClone(shard);
  newerShard.releaseId = newer.releaseId;
  state.seed(lotteryV2ShardKey(newer.releaseId, "xsmb", stationCode, year), newerShard);
  state.publishedBoundary.generatedAt = newer.generatedAt;
  state.publishedBoundary.manifest = newer.manifest;

  const newerResponse = await publish(newer);
  assert.equal(newerResponse.status, 200);
  releasePaused();
  const olderResponse = await olderRequest;

  assert.equal(olderResponse.status, 409);
  assert.equal((await olderResponse.json()).error, "release_not_published");
  assert.equal(JSON.parse(state.objects.get("v2/regions/xsmb/latest.json")).releaseId, newer.releaseId);
});

test("v2 metadata reconciles a committed write when the R2 response is lost", async () => {
  const state = ingestEnvironment();
  state.seed(lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard);
  state.throwAfterNextMetadataPut();
  const metadataUrl = new URL("https://example.test/api/admin/lottery-v2?kind=metadata&region=xsmb");
  const response = await handleLotteryV2Ingest(
    new Request(metadataUrl, {
      method: "PUT",
      headers: { authorization: "Bearer v2-test-token", "content-type": "application/json" },
      body: JSON.stringify(metadata),
    }),
    state.env,
    metadataUrl,
  );

  assert.equal(response.status, 200);
  assert.equal((await response.json()).idempotent, true);
  assert.equal(JSON.parse(state.objects.get("v2/regions/xsmb/latest.json")).releaseId, releaseId);
});
