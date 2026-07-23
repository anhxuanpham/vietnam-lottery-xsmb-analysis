import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  ExplorerPageError,
  compatibilityExplorerItems,
  fetchExplorerPage,
  fetchPreferredDashboard,
  fetchStationHistory,
} from "../dashboard-data.ts";

const fallbackPayload = JSON.parse(
  await readFile(new URL("../public/data/xsmb-demo.json", import.meta.url), "utf8"),
);

function jsonResponse(payload, status = 200, source = "r2") {
  return Response.json(payload, {
    status,
    headers: { "x-lottery-source": source },
  });
}

const explorerSample = fallbackPayload.draws
  .filter((draw) => draw.stationCode === "xsmb")
  .at(-1);
const explorerQuery = {
  region: "xsmb",
  station: "xsmb",
  from: "2026-01-01",
  to: null,
  number: explorerSample.numbers[0],
};

function resultPage(query = explorerQuery) {
  const item = fallbackPayload.draws
    .filter((draw) => draw.stationCode === query.station)
    .at(-1);
  return {
    schemaVersion: 2,
    source: "r2",
    region: query.region,
    releaseId: fallbackPayload.manifest.datasetVersion,
    datasetVersion: fallbackPayload.manifest.datasetVersion,
    generatedAt: fallbackPayload.generatedAt,
    query,
    page: { limit: 25, returned: item ? 1 : 0, nextCursor: "next-cursor" },
    items: item ? [item] : [],
  };
}

test("Explorer client snapshots filters, cursor, and validates the matching response query", async () => {
  const calls = [];
  const controller = new AbortController();
  const page = await fetchExplorerPage(
    explorerQuery,
    fallbackPayload.manifest.datasetVersion,
    {
      cursor: "cursor-1",
      signal: controller.signal,
      fetcher: async (input, init) => {
        calls.push(String(input));
        assert.strictEqual(init.signal, controller.signal);
        return jsonResponse(resultPage());
      },
    },
  );

  assert.deepEqual(calls, [
    `/api/v2/results?region=xsmb&station=xsmb&limit=25&from=2026-01-01&number=${explorerQuery.number}&cursor=cursor-1`,
  ]);
  assert.equal(page.query.number, explorerQuery.number);
  assert.equal(page.items.length, 1);
});

test("Explorer client rejects a valid page belonging to another query", async () => {
  await assert.rejects(
    fetchExplorerPage(explorerQuery, fallbackPayload.manifest.datasetVersion, {
      fetcher: async () => jsonResponse(resultPage({ ...explorerQuery, from: null })),
    }),
    (error) => error instanceof ExplorerPageError && error.code === "response_query_mismatch",
  );
});

test("Explorer client preserves stale cursor error codes for actionable UI recovery", async () => {
  await assert.rejects(
    fetchExplorerPage(explorerQuery, fallbackPayload.manifest.datasetVersion, {
      cursor: "stale",
      fetcher: async () => jsonResponse({ error: "invalid_cursor" }, 400),
    }),
    (error) =>
      error instanceof ExplorerPageError &&
      error.code === "invalid_cursor" &&
      error.status === 400,
  );
});

test("compatibility Explorer stays bounded and applies the same query snapshot", () => {
  const items = compatibilityExplorerItems(
    fallbackPayload,
    { ...explorerQuery, from: null, number: null },
    25,
  );
  assert.equal(items.length, 25);
  assert.ok(items.every((item) => item.stationCode === explorerQuery.station));
  assert.ok(items.every((item, index) => index === 0 || items[index - 1].date > item.date));
});

test("preferred dashboard degrades to v1 when v2 returns malformed metadata", async () => {
  const calls = [];
  const loaded = await fetchPreferredDashboard("xsmb", {
    fetcher: async (input) => {
      calls.push(String(input));
      if (String(input).startsWith("/api/v2/lottery")) return jsonResponse({ schemaVersion: 2 });
      return jsonResponse(fallbackPayload, 200, "bundled-demo");
    },
  });

  assert.deepEqual(calls, ["/api/v2/lottery?region=xsmb", "/api/lottery?region=xsmb"]);
  assert.equal(loaded.servingMode, "v1");
  assert.equal(loaded.dataSource, "bundled-demo");
  assert.equal(loaded.fallbackData?.region, "xsmb");
});

test("station history degrades to the compact v1 payload when a v2 release is incomplete", async () => {
  const releaseId = "fallback-release";
  const metadata = {
    schemaVersion: 2,
    releaseId,
    region: "xsmb",
    source: "r2",
    generatedAt: "2026-07-21T12:00:00Z",
    manifest: { ...fallbackPayload.manifest, datasetVersion: releaseId },
    freshness: fallbackPayload.freshness,
    range: fallbackPayload.range,
    drawCount: fallbackPayload.drawCount,
    resultCount: fallbackPayload.resultCount,
    shardKeyTemplate: `v2/releases/${releaseId}/regions/xsmb/stations/{stationCode}/years/{year}.json`,
    stations: fallbackPayload.stations.map((station) => {
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
  const calls = [];
  const loaded = await fetchStationHistory(
    { data: metadata, fallbackData: null, servingMode: "v2", dataSource: "r2" },
    "xsmb",
    "xsmb",
    {
      fetcher: async (input) => {
        calls.push(String(input));
        if (String(input).startsWith("/api/v2/results")) {
          return jsonResponse({ error: "release_invalid" }, 503);
        }
        return jsonResponse(fallbackPayload, 200, "r2");
      },
    },
  );

  assert.ok(calls[0].startsWith("/api/v2/results?"));
  assert.equal(calls[1], "/api/lottery?region=xsmb");
  assert.equal(loaded.fallback?.servingMode, "v1");
  assert.equal(loaded.station, "xsmb");
  assert.ok(loaded.draws.length > 0);
  assert.ok(loaded.draws.every((draw) => draw.stationCode === "xsmb"));
});
