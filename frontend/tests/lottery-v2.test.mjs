import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  handleLotteryV2Ingest,
  handleLotteryV2Metadata,
  handleLotteryV2Results,
  lotteryV2ShardKey,
} from "../worker/lottery-v2.ts";
import { isLotteryV2ShardPayload } from "../lottery-contract.ts";

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

function countingEnvironment(options) {
  const base = environment(options);
  const gets = [];
  return {
    gets,
    env: {
      LOTTERY_DATA: {
        get: async (key) => {
          gets.push(key);
          return base.LOTTERY_DATA.get(key);
        },
      },
    },
  };
}

function fakeCache({ now = () => 0, failPuts = false, failMatches = false } = {}) {
  const entries = new Map();
  const cache = {
    matches: [],
    puts: [],
    async match(url) {
      cache.matches.push(url);
      if (failMatches) throw new Error("simulated cache read failure");
      const entry = entries.get(url);
      if (!entry || now() >= entry.expiresAt) return undefined;
      return new Response(entry.body, { headers: { "content-type": "application/json; charset=utf-8" } });
    },
    async put(url, response) {
      cache.puts.push(url);
      if (failPuts) throw new Error("simulated cache write failure");
      const maxAge = Number(/max-age=(\d+)/.exec(response.headers.get("cache-control") ?? "")?.[1] ?? "0");
      entries.set(url, { body: await response.text(), expiresAt: now() + maxAge * 1000 });
    },
  };
  return cache;
}

function capScanFixture() {
  const years = Array.from({ length: 10 }, (_, index) => year - 9 + index);
  const capMetadata = structuredClone(metadata);
  capMetadata.range = { from: `${years[0]}-03-01`, to: `${years.at(-1)}-03-01` };
  capMetadata.drawCount = years.length;
  capMetadata.resultCount = years.length * 27;
  capMetadata.stations[0].range = { ...capMetadata.range };
  capMetadata.stations[0].drawCount = years.length;
  capMetadata.stations[0].resultCount = years.length * 27;
  capMetadata.stations[0].years = years;
  const objects = new Map([["v2/regions/xsmb/latest.json", capMetadata]]);
  for (const shardYear of years) {
    const draw = structuredClone(shardYear === years[0] ? sampleDraws[1] : sampleDraws[0]);
    draw.date = `${shardYear}-03-01`;
    objects.set(lotteryV2ShardKey(releaseId, "xsmb", stationCode, shardYear), {
      ...structuredClone(shard),
      year: shardYear,
      range: { from: draw.date, to: draw.date },
      drawCount: 1,
      resultCount: 27,
      draws: [draw],
    });
  }
  const gets = [];
  return {
    years,
    gets,
    env: {
      LOTTERY_DATA: {
        get: async (key) => {
          gets.push(key);
          return objects.has(key) ? r2Object(objects.get(key)) : null;
        },
      },
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
  assert.deepEqual(exactPage.query, {
    station: stationCode,
    from: exact.date,
    to: exact.date,
    number: exact.numbers[0],
    value: null,
    match: null,
    prizeGroup: null,
  });
});

test("v2 full-prize filters preserve leading zeros and support exact, suffix, and prize groups", async () => {
  const draw = sampleDraws.find((candidate) => candidate.prizes.prize4.includes("0844"));
  assert.ok(draw);
  const request = async (value, match, prizeGroup = "prize4") => {
    const url = new URL(
      `https://example.test/api/v2/results?region=xsmb&station=${stationCode}` +
      `&from=${draw.date}&to=${draw.date}&value=${value}&match=${match}&prizeGroup=${prizeGroup}`,
    );
    const response = await handleLotteryV2Results(new Request(url), environment(), url);
    return { response, page: await response.json() };
  };

  const exact = await request("0844", "exact");
  assert.equal(exact.response.status, 200);
  assert.deepEqual(exact.page.items.map((item) => item.date), [draw.date]);
  assert.deepEqual(exact.page.query, {
    station: stationCode,
    from: draw.date,
    to: draw.date,
    number: null,
    value: "0844",
    match: "exact",
    prizeGroup: "prize4",
  });

  const missingLeadingZero = await request("844", "exact");
  assert.equal(missingLeadingZero.response.status, 200);
  assert.deepEqual(missingLeadingZero.page.items, []);

  const suffix = await request("844", "suffix");
  assert.equal(suffix.response.status, 200);
  assert.deepEqual(suffix.page.items.map((item) => item.date), [draw.date]);

  const wrongGroup = await request("0844", "exact", "prize3");
  assert.equal(wrongGroup.response.status, 200);
  assert.deepEqual(wrongGroup.page.items, []);
});

test("v2 cursor fingerprint binds every full-prize filter", async () => {
  const firstUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&value=0&match=suffix&limit=1`,
  );
  const first = await handleLotteryV2Results(new Request(firstUrl), environment(), firstUrl);
  const page = await first.json();
  assert.equal(first.status, 200);
  assert.ok(page.page.nextCursor);

  for (const [name, value] of [["value", "1"], ["match", "exact"], ["prizeGroup", "prize4"]]) {
    const changedUrl = new URL(firstUrl);
    changedUrl.searchParams.set(name, value);
    changedUrl.searchParams.set("cursor", page.page.nextCursor);
    const changed = await handleLotteryV2Results(new Request(changedUrl), environment(), changedUrl);
    assert.equal(changed.status, 400);
    assert.equal((await changed.json()).error, "invalid_cursor");
  }
});

test("v2 cursor crosses a year boundary when the newest shard exactly fills a page", async () => {
  const olderYear = year - 1;
  const olderDraw = structuredClone(sampleDraws[0]);
  olderDraw.date = `${olderYear}-12-31`;
  const expanded = structuredClone(metadata);
  expanded.range.from = olderDraw.date;
  expanded.drawCount += 1;
  expanded.resultCount += 27;
  expanded.stations[0].range.from = olderDraw.date;
  expanded.stations[0].drawCount += 1;
  expanded.stations[0].resultCount += 27;
  expanded.stations[0].years = [olderYear, year];
  const olderShard = {
    ...structuredClone(shard),
    year: olderYear,
    range: { from: olderDraw.date, to: olderDraw.date },
    drawCount: 1,
    resultCount: 27,
    draws: [olderDraw],
  };
  const objects = new Map([
    ["v2/regions/xsmb/latest.json", expanded],
    [lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard],
    [lotteryV2ShardKey(releaseId, "xsmb", stationCode, olderYear), olderShard],
  ]);
  const env = {
    LOTTERY_DATA: {
      get: async (key) => objects.has(key) ? r2Object(objects.get(key)) : null,
    },
  };
  const firstUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=${sampleDraws.length}`,
  );
  const first = await handleLotteryV2Results(new Request(firstUrl), env, firstUrl);
  const firstPage = await first.json();
  assert.equal(first.status, 200);
  assert.equal(firstPage.items.length, sampleDraws.length);
  assert.ok(firstPage.page.nextCursor);
  assert.ok(firstPage.items.every((item) => Number(item.date.slice(0, 4)) === year));

  const secondUrl = new URL(firstUrl);
  secondUrl.searchParams.set("cursor", firstPage.page.nextCursor);
  const second = await handleLotteryV2Results(new Request(secondUrl), env, secondUrl);
  const secondPage = await second.json();
  assert.equal(second.status, 200);
  assert.deepEqual(secondPage.items.map((item) => item.date), [olderDraw.date]);
  assert.equal(secondPage.page.nextCursor, null);
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

test("v2 metadata cache serves repeat reads within the TTL and expires after it", async () => {
  const { gets, env } = countingEnvironment();
  const clock = { time: 0 };
  const cache = fakeCache({ now: () => clock.time });
  const request = new Request("https://example.test/api/v2/lottery?region=xsmb");

  const first = await handleLotteryV2Metadata(request, env, new URL(request.url), { cache });
  assert.equal(first.status, 200);
  assert.deepEqual(gets, ["v2/regions/xsmb/latest.json"]);

  const second = await handleLotteryV2Metadata(request, env, new URL(request.url), { cache });
  assert.equal(second.status, 200);
  assert.equal((await second.json()).releaseId, releaseId);
  assert.deepEqual(gets, ["v2/regions/xsmb/latest.json"]);

  clock.time = 61_000;
  const third = await handleLotteryV2Metadata(request, env, new URL(request.url), { cache });
  assert.equal(third.status, 200);
  assert.deepEqual(gets, ["v2/regions/xsmb/latest.json", "v2/regions/xsmb/latest.json"]);
});

test("v2 shard reads hit the cache on repeat requests for the same release", async () => {
  const { gets, env } = countingEnvironment();
  const cache = fakeCache();
  const url = new URL(`https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=2`);

  const first = await handleLotteryV2Results(new Request(url), env, url, { cache });
  assert.equal(first.status, 200);
  const firstPage = await first.json();
  const shardKey = lotteryV2ShardKey(releaseId, "xsmb", stationCode, year);
  assert.deepEqual(gets, ["v2/regions/xsmb/latest.json", shardKey]);

  const second = await handleLotteryV2Results(new Request(url), env, url, { cache });
  assert.equal(second.status, 200);
  assert.deepEqual(await second.json(), firstPage);
  assert.deepEqual(gets, ["v2/regions/xsmb/latest.json", shardKey]);
});

test("v2 cached shards are release-scoped so a new release bypasses them", async () => {
  const newerReleaseId = "release-test-2";
  const newerMetadata = structuredClone(metadata);
  newerMetadata.releaseId = newerReleaseId;
  newerMetadata.manifest.datasetVersion = newerReleaseId;
  newerMetadata.shardKeyTemplate =
    `v2/releases/${newerReleaseId}/regions/xsmb/stations/{stationCode}/years/{year}.json`;
  const newerShard = structuredClone(shard);
  newerShard.releaseId = newerReleaseId;
  const objects = new Map([
    ["v2/regions/xsmb/latest.json", metadata],
    [lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard],
  ]);
  const gets = [];
  const env = {
    LOTTERY_DATA: {
      get: async (key) => {
        gets.push(key);
        return objects.has(key) ? r2Object(objects.get(key)) : null;
      },
    },
  };
  const clock = { time: 0 };
  const cache = fakeCache({ now: () => clock.time });
  const url = new URL(`https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=2`);

  const first = await handleLotteryV2Results(new Request(url), env, url, { cache });
  assert.equal(first.status, 200);
  assert.equal((await first.json()).releaseId, releaseId);

  objects.set("v2/regions/xsmb/latest.json", newerMetadata);
  objects.set(lotteryV2ShardKey(newerReleaseId, "xsmb", stationCode, year), newerShard);
  clock.time = 61_000;
  const second = await handleLotteryV2Results(new Request(url), env, url, { cache });
  assert.equal(second.status, 200);
  assert.equal((await second.json()).releaseId, newerReleaseId);
  assert.ok(gets.includes(lotteryV2ShardKey(newerReleaseId, "xsmb", stationCode, year)));
  const shardMatches = cache.matches.filter((matched) => matched.includes("/stations/"));
  assert.ok(shardMatches.some((matched) => matched.includes(newerReleaseId)));
  assert.ok(shardMatches.every((matched) => matched.includes(releaseId) || matched.includes(newerReleaseId)));
});

test("v2 cursors from a fresh release survive a stale cached metadata pointer", async () => {
  const newerReleaseId = "release-test-3";
  const newerMetadata = structuredClone(metadata);
  newerMetadata.releaseId = newerReleaseId;
  newerMetadata.manifest.datasetVersion = newerReleaseId;
  newerMetadata.shardKeyTemplate =
    `v2/releases/${newerReleaseId}/regions/xsmb/stations/{stationCode}/years/{year}.json`;
  const newerShard = structuredClone(shard);
  newerShard.releaseId = newerReleaseId;
  const objects = new Map([
    ["v2/regions/xsmb/latest.json", metadata],
    [lotteryV2ShardKey(releaseId, "xsmb", stationCode, year), shard],
  ]);
  const gets = [];
  const env = {
    LOTTERY_DATA: {
      get: async (key) => {
        gets.push(key);
        return objects.has(key) ? r2Object(objects.get(key)) : null;
      },
    },
  };
  const cache = fakeCache();
  const url = new URL(`https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=1`);

  const warm = await handleLotteryV2Results(new Request(url), env, url, { cache });
  assert.equal(warm.status, 200);
  assert.equal((await warm.json()).releaseId, releaseId);

  objects.set("v2/regions/xsmb/latest.json", newerMetadata);
  objects.set(lotteryV2ShardKey(newerReleaseId, "xsmb", stationCode, year), newerShard);
  const uncachedUrl = new URL(url);
  const fresh = await handleLotteryV2Results(new Request(uncachedUrl), env, uncachedUrl, {});
  assert.equal(fresh.status, 200);
  const freshPage = await fresh.json();
  assert.equal(freshPage.releaseId, newerReleaseId);
  assert.ok(freshPage.page.nextCursor, "fixture must produce a next cursor for this test");

  const cursorUrl = new URL(url);
  cursorUrl.searchParams.set("cursor", freshPage.page.nextCursor);
  const metadataReadsBefore = gets.filter((key) => key === "v2/regions/xsmb/latest.json").length;
  const continued = await handleLotteryV2Results(new Request(cursorUrl), env, cursorUrl, { cache });
  assert.equal(continued.status, 200);
  assert.equal((await continued.json()).releaseId, newerReleaseId);
  const metadataReadsAfter = gets.filter((key) => key === "v2/regions/xsmb/latest.json").length;
  assert.equal(metadataReadsAfter, metadataReadsBefore + 1);
});

test("v2 requests succeed when the cache cannot be read or written", async () => {
  const url = new URL(`https://example.test/api/v2/results?region=xsmb&station=${stationCode}&limit=1`);

  const failingPuts = fakeCache({ failPuts: true });
  const withFailingPuts = await handleLotteryV2Results(new Request(url), environment(), url, {
    cache: failingPuts,
  });
  assert.equal(withFailingPuts.status, 200);
  assert.equal((await withFailingPuts.json()).items.length, 1);
  assert.ok(failingPuts.puts.length >= 1);

  const withFailingMatches = await handleLotteryV2Results(new Request(url), environment(), url, {
    cache: fakeCache({ failMatches: true }),
  });
  assert.equal(withFailingMatches.status, 200);
  assert.equal((await withFailingMatches.json()).items.length, 1);

  const waited = [];
  const deferred = await handleLotteryV2Results(new Request(url), environment(), url, {
    cache: fakeCache({ failPuts: true }),
    waitUntil: (promise) => waited.push(promise),
  });
  assert.equal(deferred.status, 200);
  assert.ok(waited.length >= 1);
  await Promise.all(waited);
});

test("v2 shard scans are capped per request and the cursor resumes at a year boundary", async () => {
  const fixture = capScanFixture();
  const uniqueValue = Object.values(sampleDraws[1].prizes).flat()
    .find((prize) => !Object.values(sampleDraws[0].prizes).flat().includes(prize));
  assert.ok(uniqueValue);
  const firstUrl = new URL(
    `https://example.test/api/v2/results?region=xsmb&station=${stationCode}` +
    `&value=${uniqueValue}&match=exact&limit=5`,
  );
  const first = await handleLotteryV2Results(new Request(firstUrl), fixture.env, firstUrl);
  const firstPage = await first.json();
  assert.equal(first.status, 200);
  assert.deepEqual(firstPage.items, []);
  assert.ok(firstPage.page.nextCursor);
  assert.equal(fixture.gets.length, 9);

  const normalized = firstPage.page.nextCursor.replaceAll("-", "+").replaceAll("_", "/");
  const decoded = JSON.parse(atob(normalized + "=".repeat((4 - (normalized.length % 4)) % 4)));
  assert.equal(decoded.beforeDate, `${fixture.years[2]}-01-01`);

  const secondUrl = new URL(firstUrl);
  secondUrl.searchParams.set("cursor", firstPage.page.nextCursor);
  const second = await handleLotteryV2Results(new Request(secondUrl), fixture.env, secondUrl);
  const secondPage = await second.json();
  assert.equal(second.status, 200);
  assert.deepEqual(secondPage.items.map((item) => item.date), [`${fixture.years[0]}-03-01`]);
  assert.equal(secondPage.page.nextCursor, null);
  assert.equal(fixture.gets.length, 13);
});

test("v2 never-matching filters page to completion without unbounded shard scans", async () => {
  const fixture = capScanFixture();
  let cursor = null;
  let requests = 0;
  do {
    const url = new URL(
      `https://example.test/api/v2/results?region=xsmb&station=${stationCode}` +
      "&value=999999&match=exact&limit=25",
    );
    if (cursor) url.searchParams.set("cursor", cursor);
    const before = fixture.gets.length;
    const response = await handleLotteryV2Results(new Request(url), fixture.env, url);
    assert.equal(response.status, 200);
    const page = await response.json();
    assert.deepEqual(page.items, []);
    assert.ok(fixture.gets.length - before <= 9);
    cursor = page.page.nextCursor;
    requests += 1;
  } while (cursor !== null && requests < 5);
  assert.equal(cursor, null);
  assert.equal(requests, 2);
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

test("v2 results rejects ambiguous or non-ASCII full-prize filters", async () => {
  const cases = [
    ["value=１２", "invalid_value"],
    ["value=1234567", "invalid_value"],
    ["value=12&match=contains", "invalid_match"],
    ["match=suffix", "incomplete_prize_filter"],
    ["value=12&prizeGroup=prize8", "invalid_prize_group"],
    ["number=12&value=12", "conflicting_filters"],
  ];
  for (const [parameters, expectedError] of cases) {
    const url = new URL(
      `https://example.test/api/v2/results?region=xsmb&station=${stationCode}&${parameters}`,
    );
    const response = await handleLotteryV2Results(new Request(url), environment(), url);
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error, expectedError);
  }
});

test("v2 shard contract enforces the exact regional prize layout and official widths", () => {
  assert.equal(isLotteryV2ShardPayload(shard), true);

  const shortened = structuredClone(shard);
  const prizeIndex = shortened.draws[0].prizes.prize4.indexOf("0844");
  assert.notEqual(prizeIndex, -1);
  shortened.draws[0].prizes.prize4[prizeIndex] = "844";
  assert.equal(isLotteryV2ShardPayload(shortened), false);

  const unknownGroup = structuredClone(shard);
  unknownGroup.draws[0].prizes.bonus = [];
  assert.equal(isLotteryV2ShardPayload(unknownGroup), false);

  const specialNotFirst = structuredClone(shard);
  const differentIndex = specialNotFirst.draws[0].numbers.findIndex(
    (number) => number !== specialNotFirst.draws[0].specialTail,
  );
  assert.ok(differentIndex > 0);
  [specialNotFirst.draws[0].numbers[0], specialNotFirst.draws[0].numbers[differentIndex]] = [
    specialNotFirst.draws[0].numbers[differentIndex],
    specialNotFirst.draws[0].numbers[0],
  ];
  assert.equal(isLotteryV2ShardPayload(specialNotFirst), false);
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
