/** Cloudflare Worker entry point for the Loto Lab dashboard. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import {
  isLotteryDashboardData,
  isLotteryRegion,
  type LotteryRegion,
} from "../lottery-contract";
import { handleLotteryHealthRequest } from "./health.ts";
import {
  handleLotteryV2Ingest,
  handleLotteryV2Metadata,
  handleLotteryV2Results,
} from "./lottery-v2.ts";
import { handleLotteryWatchdogStatus } from "./ops-status.ts";
import { runLotteryWatchdog } from "./watchdog.ts";

const MAX_INGEST_BYTES = 8 * 1024 * 1024;
const IMAGE_OUTPUT_FORMATS = ["image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"] as const;
type ImageOutputFormat = (typeof IMAGE_OUTPUT_FORMATS)[number];
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "x-content-type-options": "nosniff",
};

class PayloadTooLargeError extends Error {}

function isImageOutputFormat(value: string): value is ImageOutputFormat {
  return IMAGE_OUTPUT_FORMATS.includes(value as ImageOutputFormat);
}

function jsonResponse(body: unknown, status: number, headers?: HeadersInit): Response {
  return Response.json(body, {
    status,
    headers: { ...JSON_HEADERS, "cache-control": "no-store", ...headers },
  });
}

function selectedRegion(url: URL): LotteryRegion | null {
  const values = url.searchParams.getAll("region");
  if (values.length !== 1 || !isLotteryRegion(values[0])) return null;
  return values[0];
}

function r2Key(region: LotteryRegion): string {
  return `regions/${region}.json`;
}

async function readBodyWithLimit(request: Request): Promise<Uint8Array> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const bytes = Number(declaredLength);
    if (!Number.isSafeInteger(bytes) || bytes < 0 || bytes > MAX_INGEST_BYTES) {
      throw new PayloadTooLargeError();
    }
  }

  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > MAX_INGEST_BYTES) {
        await reader.cancel("Payload exceeds dashboard ingest limit");
        throw new PayloadTooLargeError();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const body = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function tokensMatch(supplied: string, expected: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [suppliedHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(supplied)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const suppliedBytes = new Uint8Array(suppliedHash);
  const expectedBytes = new Uint8Array(expectedHash);
  let difference = 0;
  for (let index = 0; index < expectedBytes.length; index += 1) {
    difference |= suppliedBytes[index] ^ expectedBytes[index];
  }
  return difference === 0;
}

async function isAuthorized(request: Request, expectedToken: string): Promise<boolean> {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) return false;
  const suppliedToken = authorization.slice("Bearer ".length);
  return suppliedToken.length > 0 && tokensMatch(suppliedToken, expectedToken);
}

async function getLotteryData(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "GET") {
    return jsonResponse({ error: "method_not_allowed" }, 405, { allow: "GET" });
  }

  const region = selectedRegion(url);
  if (!region) {
    return jsonResponse({ error: "invalid_region", allowed: ["xsmb", "xsmn", "xsmt"] }, 400);
  }

  const object = await env.LOTTERY_DATA?.get(r2Key(region));
  if (object) {
    const headers = new Headers(JSON_HEADERS);
    object.writeHttpMetadata(headers);
    headers.set("content-type", "application/json; charset=utf-8");
    headers.set("cache-control", "public, max-age=300, stale-while-revalidate=3600");
    headers.set("etag", object.httpEtag);
    headers.set("x-lottery-source", "r2");
    return new Response(object.body, { headers });
  }

  const assetUrl = new URL(`/data/${region}-demo.json`, request.url);
  const fallback = await env.ASSETS.fetch(new Request(assetUrl, { headers: request.headers }));
  if (!fallback.ok) {
    return jsonResponse({ error: "dataset_unavailable", region }, 503);
  }

  const headers = new Headers(fallback.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "public, max-age=60, stale-while-revalidate=300");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-lottery-source", "bundled-demo");
  return new Response(fallback.body, { status: fallback.status, headers });
}

async function putLotteryData(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "PUT") {
    return jsonResponse({ error: "method_not_allowed" }, 405, { allow: "PUT" });
  }
  if (!env.LOTTERY_DATA || !env.DASHBOARD_INGEST_TOKEN) {
    return jsonResponse({ error: "ingest_not_configured" }, 503);
  }
  if (!(await isAuthorized(request, env.DASHBOARD_INGEST_TOKEN))) {
    return jsonResponse(
      { error: "unauthorized" },
      401,
      { "www-authenticate": 'Bearer realm="lottery-dashboard-ingest"' },
    );
  }

  const region = selectedRegion(url);
  if (!region) {
    return jsonResponse({ error: "invalid_region", allowed: ["xsmb", "xsmn", "xsmt"] }, 400);
  }
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    return jsonResponse({ error: "unsupported_media_type", expected: "application/json" }, 415);
  }

  let body: Uint8Array;
  try {
    body = await readBodyWithLimit(request);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) {
      return jsonResponse({ error: "payload_too_large", maxBytes: MAX_INGEST_BYTES }, 413);
    }
    throw error;
  }

  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return jsonResponse({ error: "invalid_json" }, 400);
  }
  if (!isLotteryDashboardData(payload, region)) {
    return jsonResponse({ error: "invalid_dashboard_payload", schemaVersion: 1 }, 422);
  }

  const canonicalBody = JSON.stringify(payload);
  const canonicalBytes = new TextEncoder().encode(canonicalBody);
  const object = await env.LOTTERY_DATA.put(r2Key(region), canonicalBytes, {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
      cacheControl: "public, max-age=300, stale-while-revalidate=3600",
    },
    customMetadata: {
      region,
      schemaVersion: String(payload.schemaVersion),
      generatedAt: payload.generatedAt,
    },
  });

  return jsonResponse(
    { ok: true, region, key: r2Key(region), etag: object.etag, bytes: canonicalBytes.byteLength },
    200,
  );
}

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/api/lottery") return getLotteryData(request, env, url);
    if (url.pathname === "/api/admin/lottery") return putLotteryData(request, env, url);
    if (url.pathname === "/api/v2/lottery") return handleLotteryV2Metadata(request, env, url);
    if (url.pathname === "/api/v2/results") return handleLotteryV2Results(request, env, url);
    if (url.pathname === "/api/admin/lottery-v2") return handleLotteryV2Ingest(request, env, url);
    if (url.pathname === "/api/health/lottery") return handleLotteryHealthRequest(request, env);
    if (url.pathname === "/api/ops/lottery") return handleLotteryWatchdogStatus(request, env);

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          if (!isImageOutputFormat(format)) throw new Error(`Unsupported image output format: ${format}`);
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    return handler.fetch(request, env, ctx);
  },
  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    await runLotteryWatchdog(controller, env);
  },
};

export default worker;
