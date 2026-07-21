import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
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
