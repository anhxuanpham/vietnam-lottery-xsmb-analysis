import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker;
}

const executionContext = { waitUntil() {}, passThroughOnException() {} };

async function render() {
  const worker = await loadWorker();
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    executionContext,
  );
}

test("server-renders the branded analytics shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Lôtô Lab — Vietnam Lottery Analytics<\/title>/i);
  assert.match(
    html,
    /<meta property="og:image" content="https:\/\/loto-lab-vietnam\.nmt17092006\.chatgpt\.site\/og\.png"/i,
  );
  assert.match(
    html,
    /<meta name="twitter:image" content="https:\/\/loto-lab-vietnam\.nmt17092006\.chatgpt\.site\/og\.png"/i,
  );
  assert.match(html, /Đang nạp dữ liệu mô hình/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/);
});

test("ships all three serving-schema demo datasets and removes the starter preview", async () => {
  const [page, layout, dashboardData, ...datasets] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../dashboard-data.ts", import.meta.url), "utf8"),
    ...["xsmb", "xsmn", "xsmt"].map((region) =>
      readFile(new URL(`../public/data/${region}-demo.json`, import.meta.url), "utf8"),
    ),
  ]);
  assert.match(page, /MODEL LAB/);
  assert.match(page, /backtest/i);
  assert.match(page, /RESULT EXPLORER/);
  assert.match(dashboardData, /\/api\/v2\/results/);
  assert.match(page, /Tra kết quả/);
  assert.match(page, /Tải thêm kết quả/);
  assert.match(page, /Không tìm thấy kỳ quay phù hợp/);
  assert.match(page, /ANALYTICS_MODEL_VERSION/);
  assert.match(page, /95% CI/);
  assert.match(page, /Tải benchmark JSON/);
  assert.match(page, /12 lựa chọn model\/cửa sổ/);
  assert.match(page, /model\.benchmark\.fingerprint/);
  assert.match(page, /fetchLotteryOperations/);
  assert.match(page, /Watchdog gần nhất/);
  assert.match(page, /không phải dự báo xác suất trúng/i);
  assert.match(page, /LOTTERY_REGIONS/);
  assert.match(layout, /lang="vi"/);
  datasets.forEach((dataset, index) => {
    const parsed = JSON.parse(dataset);
    assert.equal(parsed.schemaVersion, 1);
    assert.equal(parsed.region, ["xsmb", "xsmn", "xsmt"][index]);
    assert.equal(parsed.manifest.key, "manifests/latest.json");
    assert.ok(parsed.latest.results.length > 0);
    assert.equal(Object.keys(parsed.fullFrequency).length, 100);
    assert.ok(parsed.stations.every((station) => Object.keys(station.fullFrequency).length === 100));
  });
  await assert.rejects(access(new URL("app/_sites-preview", root)));
});

test("serves R2 first and falls back to bundled regional assets", async () => {
  const worker = await loadWorker();
  const xsmt = await readFile(new URL("../public/data/xsmt-demo.json", import.meta.url), "utf8");
  const r2Response = await worker.fetch(
    new Request("http://localhost/api/lottery?region=xsmt"),
    {
      LOTTERY_DATA: {
        get: async (key) => ({
          body: xsmt,
          httpEtag: '"r2-etag"',
          writeHttpMetadata() {},
          key,
        }),
      },
      ASSETS: { fetch: async () => new Response("unexpected", { status: 500 }) },
    },
    executionContext,
  );
  assert.equal(r2Response.status, 200);
  assert.equal(r2Response.headers.get("x-lottery-source"), "r2");
  assert.equal((await r2Response.json()).region, "xsmt");

  const fallbackResponse = await worker.fetch(
    new Request("http://localhost/api/lottery?region=xsmn"),
    {
      LOTTERY_DATA: { get: async () => null },
      ASSETS: {
        fetch: async (request) => {
          assert.equal(new URL(request.url).pathname, "/data/xsmn-demo.json");
          return new Response(await readFile(new URL("../public/data/xsmn-demo.json", import.meta.url)));
        },
      },
    },
    executionContext,
  );
  assert.equal(fallbackResponse.status, 200);
  assert.equal(fallbackResponse.headers.get("x-lottery-source"), "bundled-demo");
  assert.equal((await fallbackResponse.json()).region, "xsmn");
});

test("routes the redacted watchdog status API through the production worker", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/api/ops/lottery"),
    {
      LOTTERY_DATA: { get: async () => null },
      ASSETS: { fetch: async () => new Response("unused") },
    },
    executionContext,
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), {
    schemaVersion: 1,
    service: "lottery-watchdog",
    available: false,
    state: null,
  });
});

test("validates region and protects the R2 ingest endpoint", async () => {
  const worker = await loadWorker();
  const invalidRegion = await worker.fetch(
    new Request("http://localhost/api/lottery?region=invalid"),
    { ASSETS: { fetch: async () => new Response("unused") } },
    executionContext,
  );
  assert.equal(invalidRegion.status, 400);

  const payload = await readFile(new URL("../public/data/xsmb-demo.json", import.meta.url), "utf8");
  const puts = [];
  const env = {
    DASHBOARD_INGEST_TOKEN: "test-token-with-enough-entropy",
    LOTTERY_DATA: {
      get: async () => null,
      put: async (key, body, options) => {
        puts.push({ key, body, options });
        return { etag: "saved-etag" };
      },
    },
    ASSETS: { fetch: async () => new Response("unused") },
  };
  const unauthorized = await worker.fetch(
    new Request("http://localhost/api/admin/lottery?region=xsmb", {
      method: "PUT",
      headers: { authorization: "Bearer wrong", "content-type": "application/json" },
      body: payload,
    }),
    env,
    executionContext,
  );
  assert.equal(unauthorized.status, 401);
  assert.equal(puts.length, 0);

  const saved = await worker.fetch(
    new Request("http://localhost/api/admin/lottery?region=xsmb", {
      method: "PUT",
      headers: { authorization: `Bearer ${env.DASHBOARD_INGEST_TOKEN}`, "content-type": "application/json" },
      body: payload,
    }),
    env,
    executionContext,
  );
  assert.equal(saved.status, 200);
  assert.equal(puts.length, 1);
  assert.equal(puts[0].key, "regions/xsmb.json");
  assert.equal(puts[0].options.httpMetadata.contentType, "application/json; charset=utf-8");
});
