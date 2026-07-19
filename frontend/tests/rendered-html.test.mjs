import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the branded analytics shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Lôtô Lab — Vietnam Lottery Analytics<\/title>/i);
  assert.match(html, /Đang nạp dữ liệu mô hình/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/);
});

test("ships the local demo data and removes the starter preview", async () => {
  const [page, layout, dataset] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/data/xsmb-demo.json", import.meta.url), "utf8"),
  ]);
  assert.match(page, /MODEL LAB/);
  assert.match(page, /backtest/i);
  assert.match(page, /không phải dự báo xác suất trúng/i);
  assert.match(page, /XSMT/);
  assert.match(layout, /lang="vi"/);
  assert.match(dataset, /"drawCount":7493/);
  await assert.rejects(access(new URL("app/_sites-preview", root)));
});
