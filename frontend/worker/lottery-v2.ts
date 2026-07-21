/** Read-only station/year API for the versioned historical result explorer. */
import {
  isLotteryDashboardData,
  isLotteryRegion,
  isLotteryV2ReleaseMetadata,
  isLotteryV2Shard,
  isLotteryV2ShardPayload,
  LOTTERY_REGIONS,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryRegion,
  type LotteryV2ReleaseMetadata,
  type LotteryV2Shard,
} from "../lottery-contract.ts";

const DEFAULT_LIMIT = 25;
const MAX_LIMIT = 100;
const MAX_METADATA_BYTES = 100 * 1024;
const MAX_SHARD_BYTES = 2 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 250 * 1024;
const MAX_INGEST_BYTES = 2 * 1024 * 1024;
const MAX_PUBLISHED_BOUNDARY_BYTES = 8 * 1024 * 1024;
const MAX_METADATA_CAS_ATTEMPTS = 3;
const V2_HEALTH_ACTIVATION_KEY = "v2/health/required.json";
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const NUMBER_PATTERN = /^\d{2}$/;
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "x-content-type-options": "nosniff",
};

type Query = {
  region: LotteryRegion;
  station: string;
  from: string | null;
  to: string | null;
  number: string | null;
  limit: number;
  cursor: string | null;
};

type CursorPayload = {
  version: 2;
  releaseId: string;
  fingerprint: string;
  beforeDate: string;
};

type MetadataPointerState = {
  metadata: LotteryV2ReleaseMetadata | null;
  etag: string | null;
};

type MetadataPublishResult = {
  idempotent: boolean;
};

class ApiInputError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

class PayloadTooLargeError extends Error {}

class ReleasePublicationError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

function responseJson(body: unknown, status: number, headers?: HeadersInit): Response {
  return Response.json(body, {
    status,
    headers: { ...JSON_HEADERS, "cache-control": "no-store", ...headers },
  });
}

function singleParameter(url: URL, name: string, required = false): string | null {
  const values = url.searchParams.getAll(name);
  if (values.length > 1 || (required && values.length !== 1)) {
    throw new ApiInputError(`invalid_${name}`, `${name} must be supplied exactly once`);
  }
  const value = values[0] ?? null;
  if (value !== null && value.length === 0) {
    throw new ApiInputError(`invalid_${name}`, `${name} cannot be empty`);
  }
  return value;
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

async function authorized(request: Request, expectedToken: string): Promise<boolean> {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) return false;
  const supplied = authorization.slice("Bearer ".length);
  return supplied.length > 0 && tokensMatch(supplied, expectedToken);
}

async function readBodyWithLimit(request: Request, maximumBytes: number): Promise<Uint8Array> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const bytes = Number(declaredLength);
    if (!Number.isSafeInteger(bytes) || bytes < 0 || bytes > maximumBytes) throw new PayloadTooLargeError();
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
      if (totalBytes > maximumBytes) {
        await reader.cancel("Payload exceeds lottery v2 ingest limit");
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

function validDate(value: string): boolean {
  if (!DATE_PATTERN.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

export function lotteryV2MetadataKey(region: LotteryRegion): string {
  return `v2/regions/${region}/latest.json`;
}

export function lotteryV2ShardKey(
  releaseId: string,
  region: LotteryRegion,
  station: string,
  year: number,
): string {
  return `v2/releases/${releaseId}/regions/${region}/stations/${station}/years/${year}.json`;
}

async function readMetadata(env: Env, region: LotteryRegion): Promise<LotteryV2ReleaseMetadata | null> {
  const object = await env.LOTTERY_DATA.get(lotteryV2MetadataKey(region));
  if (!object) return null;
  if (object.size >= MAX_METADATA_BYTES) {
    throw new Error(`Lottery v2 metadata exceeds ${MAX_METADATA_BYTES} bytes`);
  }
  const payload: unknown = await object.json();
  if (!isLotteryV2ReleaseMetadata(payload, region)) {
    throw new Error("Lottery v2 metadata failed contract validation");
  }
  return payload;
}

function parseRegion(url: URL): LotteryRegion {
  const rawRegion = singleParameter(url, "region", true);
  if (!isLotteryRegion(rawRegion)) {
    throw new ApiInputError("invalid_region", "region must be one of xsmb, xsmn, or xsmt");
  }
  return rawRegion;
}

function parseQuery(url: URL): Query {
  const region = parseRegion(url);
  const station = singleParameter(url, "station", true);
  const from = singleParameter(url, "from");
  const to = singleParameter(url, "to");
  const number = singleParameter(url, "number");
  const rawLimit = singleParameter(url, "limit");
  const cursor = singleParameter(url, "cursor");
  if (station === null || !/^[A-Za-z0-9]{2,8}$/.test(station)) {
    throw new ApiInputError("invalid_station", "station has an invalid format");
  }
  if (from !== null && !validDate(from)) {
    throw new ApiInputError("invalid_from", "from must be a valid YYYY-MM-DD date");
  }
  if (to !== null && !validDate(to)) {
    throw new ApiInputError("invalid_to", "to must be a valid YYYY-MM-DD date");
  }
  if (from !== null && to !== null && to < from) {
    throw new ApiInputError("invalid_range", "to must be on or after from");
  }
  if (number !== null && !NUMBER_PATTERN.test(number)) {
    throw new ApiInputError("invalid_number", "number must contain exactly two digits from 00 to 99");
  }
  const limit = rawLimit === null ? DEFAULT_LIMIT : Number(rawLimit);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    throw new ApiInputError("invalid_limit", `limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  return { region, station, from, to, number, limit, cursor };
}

function queryFingerprint(query: Query): string {
  return [query.region, query.station, query.from ?? "", query.to ?? "", query.number ?? ""].join("|");
}

function encodeCursor(cursor: CursorPayload): string {
  return btoa(JSON.stringify(cursor)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeCursor(value: string): CursorPayload {
  try {
    const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
    const padding = "=".repeat((4 - (normalized.length % 4)) % 4);
    const parsed: unknown = JSON.parse(atob(normalized + padding));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    const candidate = parsed as Record<string, unknown>;
    if (candidate.version !== 2 || typeof candidate.releaseId !== "string" ||
      typeof candidate.fingerprint !== "string" || typeof candidate.beforeDate !== "string" ||
      !validDate(candidate.beforeDate)) throw new Error();
    return {
      version: 2,
      releaseId: candidate.releaseId,
      fingerprint: candidate.fingerprint,
      beforeDate: candidate.beforeDate,
    };
  } catch {
    throw new ApiInputError("invalid_cursor", "cursor is malformed or does not match this query");
  }
}

async function readShard(
  env: Env,
  metadata: LotteryV2ReleaseMetadata,
  stationCode: string,
  year: number,
): Promise<LotteryV2Shard> {
  const key = lotteryV2ShardKey(metadata.releaseId, metadata.region, stationCode, year);
  const object = await env.LOTTERY_DATA.get(key);
  if (!object) throw new Error(`Lottery v2 release is incomplete: ${key} is missing`);
  if (object.size > MAX_SHARD_BYTES) throw new Error(`Lottery v2 shard exceeds ${MAX_SHARD_BYTES} bytes`);
  const payload: unknown = await object.json();
  if (!isLotteryV2Shard(payload, metadata, stationCode, year)) {
    throw new Error(`Lottery v2 shard failed contract validation: ${key}`);
  }
  return payload;
}

async function readResultPage(
  env: Env,
  metadata: LotteryV2ReleaseMetadata,
  stationCode: string,
  years: number[],
  query: Query,
  effectiveFrom: string,
  effectiveTo: string,
  beforeDate: string | null,
): Promise<{ items: LotteryDraw[]; hasMore: boolean }> {
  const matches: LotteryDraw[] = [];
  const beforeYear = beforeDate === null ? null : Number(beforeDate.slice(0, 4));
  for (const year of [...years].sort((left, right) => right - left)) {
    if (beforeYear !== null && year > beforeYear) continue;
    const shard = await readShard(env, metadata, stationCode, year);
    matches.push(...shard.draws
      .filter((draw) => draw.date >= effectiveFrom && draw.date <= effectiveTo)
      .filter((draw) => beforeDate === null || draw.date < beforeDate)
      .filter((draw) => query.number === null || draw.numbers.includes(query.number))
      .sort((left, right) => right.date.localeCompare(left.date)));
    if (matches.length > query.limit) break;
  }
  return {
    items: matches.slice(0, query.limit),
    hasMore: matches.length > query.limit,
  };
}

function inputErrorResponse(error: ApiInputError): Response {
  return responseJson({ error: error.code, message: error.message }, 400);
}

async function readPublishedBoundary(env: Env, region: LotteryRegion): Promise<LotteryDashboardData> {
  const key = `regions/${region}.json`;
  const object = await env.LOTTERY_DATA.get(key);
  if (!object) {
    throw new ReleasePublicationError("published_boundary_unavailable", `${key} is missing`);
  }
  if (object.size > MAX_PUBLISHED_BOUNDARY_BYTES) {
    throw new ReleasePublicationError("published_boundary_invalid", `${key} exceeds the serving limit`);
  }
  let payload: unknown;
  try {
    payload = await object.json();
  } catch {
    throw new ReleasePublicationError("published_boundary_invalid", `${key} is not valid JSON`);
  }
  if (!isLotteryDashboardData(payload, region)) {
    throw new ReleasePublicationError("published_boundary_invalid", `${key} failed contract validation`);
  }
  return payload;
}

async function readCurrentMetadataForPublication(
  env: Env,
  region: LotteryRegion,
): Promise<MetadataPointerState> {
  const object = await env.LOTTERY_DATA.get(lotteryV2MetadataKey(region));
  if (!object) return { metadata: null, etag: null };
  if (object.size >= MAX_METADATA_BYTES) return { metadata: null, etag: object.etag };
  try {
    const payload: unknown = await object.json();
    return {
      metadata: isLotteryV2ReleaseMetadata(payload, region) ? payload : null,
      etag: object.etag,
    };
  } catch {
    return { metadata: null, etag: object.etag };
  }
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function sameMetadataPublication(
  current: LotteryV2ReleaseMetadata | null,
  candidate: LotteryV2ReleaseMetadata,
): boolean {
  if (!current) return false;
  return stableJson({ ...current, generatedAt: null }) === stableJson({ ...candidate, generatedAt: null });
}

function validatePublishedBoundary(
  metadata: LotteryV2ReleaseMetadata,
  published: LotteryDashboardData,
): void {
  const manifestFields = ["key", "runId", "datasetVersion", "targetDate", "publishedAt"] as const;
  if (manifestFields.some((field) => metadata.manifest[field] !== published.manifest[field]) ||
    metadata.releaseId !== published.manifest.datasetVersion ||
    metadata.range.from !== published.range.from || metadata.range.to !== published.range.to ||
    metadata.drawCount !== published.drawCount || metadata.resultCount !== published.resultCount ||
    metadata.freshness.latestDrawDate !== published.freshness.latestDrawDate ||
    metadata.freshness.manifestTargetDate !== published.freshness.manifestTargetDate ||
    metadata.freshness.matchesManifestTarget !== published.freshness.matchesManifestTarget) {
    throw new ReleasePublicationError(
      "release_not_published",
      "v2 metadata does not match the currently published compact boundary",
    );
  }
  const expectedTemplate = `v2/releases/${metadata.releaseId}/regions/${metadata.region}/stations/{stationCode}/years/{year}.json`;
  if (metadata.shardKeyTemplate !== expectedTemplate) {
    throw new ReleasePublicationError("invalid_shard_template", "v2 shardKeyTemplate is not canonical");
  }
}

function validateMonotonicRelease(
  metadata: LotteryV2ReleaseMetadata,
  current: LotteryV2ReleaseMetadata | null,
): void {
  if (!current) return;
  const targetComparison = metadata.manifest.targetDate.localeCompare(current.manifest.targetDate);
  const publicationComparison = Date.parse(metadata.manifest.publishedAt) - Date.parse(current.manifest.publishedAt);
  if (targetComparison < 0 || (targetComparison === 0 && publicationComparison < 0)) {
    throw new ReleasePublicationError("stale_release", "v2 latest metadata cannot move backwards");
  }
  if (targetComparison === 0 && publicationComparison === 0 && current.releaseId !== metadata.releaseId) {
    throw new ReleasePublicationError(
      "release_identity_conflict",
      "the same published boundary already points to another release",
    );
  }
}

async function validateDeclaredShards(
  env: Env,
  metadata: LotteryV2ReleaseMetadata,
): Promise<void> {
  for (const station of metadata.stations) {
    let shards: LotteryV2Shard[];
    try {
      shards = await Promise.all(
        station.years.map((year) => readShard(env, metadata, station.code, year)),
      );
    } catch (error) {
      throw new ReleasePublicationError(
        "incomplete_release",
        error instanceof Error ? error.message : "a declared v2 shard is unavailable",
      );
    }
    const drawCount = shards.reduce((total, shard) => total + shard.drawCount, 0);
    const resultCount = shards.reduce((total, shard) => total + shard.resultCount, 0);
    const rangeFrom = shards[0]?.range.from ?? "";
    const rangeTo = shards.at(-1)?.range.to ?? "";
    if (drawCount !== station.drawCount || resultCount !== station.resultCount ||
      rangeFrom !== station.range.from || rangeTo !== station.range.to ||
      shards.some((shard) => shard.station.name !== station.name)) {
      throw new ReleasePublicationError(
        "inconsistent_release",
        `declared shards do not match station metadata for ${station.code}`,
      );
    }
  }
}

async function validateMetadataPublication(
  env: Env,
  metadata: LotteryV2ReleaseMetadata,
): Promise<MetadataPointerState> {
  const [published, current] = await Promise.all([
    readPublishedBoundary(env, metadata.region),
    readCurrentMetadataForPublication(env, metadata.region),
  ]);
  validatePublishedBoundary(metadata, published);
  validateMonotonicRelease(metadata, current.metadata);
  await validateDeclaredShards(env, metadata);
  return current;
}

async function publishMetadataPointer(
  env: Env,
  metadata: LotteryV2ReleaseMetadata,
  canonicalBody: string,
  options: R2PutOptions,
  initialState: MetadataPointerState,
): Promise<MetadataPublishResult> {
  const key = lotteryV2MetadataKey(metadata.region);
  let state = initialState;

  for (let attempt = 0; attempt < MAX_METADATA_CAS_ATTEMPTS; attempt += 1) {
    const published = await readPublishedBoundary(env, metadata.region);
    validatePublishedBoundary(metadata, published);
    if (sameMetadataPublication(state.metadata, metadata)) return { idempotent: true };
    validateMonotonicRelease(metadata, state.metadata);

    const onlyIf: R2Conditional = state.etag === null
      ? { etagDoesNotMatch: "*" }
      : { etagMatches: state.etag };
    try {
      const stored = await env.LOTTERY_DATA.put(key, canonicalBody, { ...options, onlyIf });
      if (stored) return { idempotent: false };
    } catch (error) {
      try {
        const observed = await readCurrentMetadataForPublication(env, metadata.region);
        if (sameMetadataPublication(observed.metadata, metadata)) return { idempotent: true };
      } catch {
        // Preserve the original write error when reconciliation itself is unavailable.
      }
      throw error;
    }

    state = await readCurrentMetadataForPublication(env, metadata.region);
    if (sameMetadataPublication(state.metadata, metadata)) return { idempotent: true };
  }

  throw new ReleasePublicationError(
    "concurrent_release_update",
    "v2 latest metadata changed repeatedly during publication",
  );
}

async function activateV2HealthIfReady(env: Env, activatedAt: string): Promise<void> {
  const existing = await env.LOTTERY_DATA.get(V2_HEALTH_ACTIVATION_KEY);
  if (existing) return;
  const releases = await Promise.all(LOTTERY_REGIONS.map(async (region) => {
    try {
      return await readMetadata(env, region);
    } catch {
      return null;
    }
  }));
  if (releases.some((release) => release === null)) return;
  await env.LOTTERY_DATA.put(
    V2_HEALTH_ACTIVATION_KEY,
    JSON.stringify({ schemaVersion: 1, required: true, activatedAt, regions: LOTTERY_REGIONS }),
    {
      onlyIf: { etagDoesNotMatch: "*" },
      httpMetadata: { contentType: "application/json; charset=utf-8", cacheControl: "no-store" },
      customMetadata: { schemaVersion: "1", required: "true" },
    },
  );
}

export async function handleLotteryV2Metadata(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "GET") return responseJson({ error: "method_not_allowed" }, 405, { allow: "GET" });
  try {
    const region = parseRegion(url);
    const metadata = await readMetadata(env, region);
    if (!metadata) return responseJson({ error: "release_unavailable", region }, 503);
    return responseJson(metadata, 200, {
      "cache-control": "public, max-age=300, stale-while-revalidate=3600",
      "x-lottery-source": "r2",
    });
  } catch (error) {
    if (error instanceof ApiInputError) return inputErrorResponse(error);
    console.error(JSON.stringify({
      message: "lottery_v2_metadata_failed",
      error: error instanceof Error ? error.message : String(error),
    }));
    return responseJson({ error: "release_invalid" }, 503);
  }
}

export async function handleLotteryV2Results(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "GET") return responseJson({ error: "method_not_allowed" }, 405, { allow: "GET" });
  try {
    const query = parseQuery(url);
    const metadata = await readMetadata(env, query.region);
    if (!metadata) return responseJson({ error: "release_unavailable", region: query.region }, 503);
    const station = metadata.stations.find((candidate) => candidate.code === query.station);
    if (!station) {
      return responseJson(
        { error: "station_not_found", region: query.region, station: query.station },
        404,
      );
    }

    const effectiveFrom = query.from ?? station.range.from;
    const effectiveTo = query.to ?? station.range.to;
    const fromYear = Number(effectiveFrom.slice(0, 4));
    const toYear = Number(effectiveTo.slice(0, 4));
    const years = station.years.filter((year) => year >= fromYear && year <= toYear);

    const fingerprint = queryFingerprint(query);
    let beforeDate: string | null = null;
    if (query.cursor !== null) {
      const cursor = decodeCursor(query.cursor);
      if (cursor.releaseId !== metadata.releaseId || cursor.fingerprint !== fingerprint) {
        throw new ApiInputError("invalid_cursor", "cursor is stale or belongs to another query");
      }
      beforeDate = cursor.beforeDate;
    }

    const page = await readResultPage(
      env,
      metadata,
      station.code,
      years,
      query,
      effectiveFrom,
      effectiveTo,
      beforeDate,
    );
    const items = page.items;
    const nextCursor = page.hasMore && items.length > 0
      ? encodeCursor({
          version: 2,
          releaseId: metadata.releaseId,
          fingerprint,
          beforeDate: items.at(-1)?.date ?? effectiveFrom,
        })
      : null;
    const body = {
      schemaVersion: 2,
      source: "r2",
      region: metadata.region,
      releaseId: metadata.releaseId,
      datasetVersion: metadata.manifest.datasetVersion,
      generatedAt: metadata.generatedAt,
      query: {
        station: station.code,
        from: query.from,
        to: query.to,
        number: query.number,
      },
      page: { limit: query.limit, returned: items.length, nextCursor },
      items,
    } as const;
    const encoded = JSON.stringify(body);
    if (new TextEncoder().encode(encoded).byteLength >= MAX_RESPONSE_BYTES) {
      return responseJson({ error: "response_too_large", maxBytes: MAX_RESPONSE_BYTES }, 500);
    }
    return new Response(encoded, {
      status: 200,
      headers: {
        ...JSON_HEADERS,
        "cache-control": "public, max-age=300, stale-while-revalidate=3600",
        "x-lottery-source": "r2",
      },
    });
  } catch (error) {
    if (error instanceof ApiInputError) return inputErrorResponse(error);
    console.error(JSON.stringify({
      message: "lottery_v2_results_failed",
      error: error instanceof Error ? error.message : String(error),
    }));
    return responseJson({ error: "release_invalid" }, 503);
  }
}

export async function handleLotteryV2Ingest(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "PUT") return responseJson({ error: "method_not_allowed" }, 405, { allow: "PUT" });
  if (!env.DASHBOARD_INGEST_TOKEN) return responseJson({ error: "ingest_not_configured" }, 503);
  if (!(await authorized(request, env.DASHBOARD_INGEST_TOKEN))) {
    return responseJson(
      { error: "unauthorized" },
      401,
      { "www-authenticate": 'Bearer realm="lottery-v2-ingest"' },
    );
  }
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    return responseJson({ error: "unsupported_media_type", expected: "application/json" }, 415);
  }

  try {
    const kind = singleParameter(url, "kind", true);
    const region = parseRegion(url);
    if (kind !== "metadata" && kind !== "shard") {
      throw new ApiInputError("invalid_kind", "kind must be metadata or shard");
    }
    const body = await readBodyWithLimit(
      request,
      kind === "metadata" ? MAX_METADATA_BYTES - 1 : MAX_INGEST_BYTES,
    );
    let payload: unknown;
    try {
      payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
    } catch {
      return responseJson({ error: "invalid_json" }, 400);
    }

    let key: string;
    let immutable = false;
    let metadataPayload: LotteryV2ReleaseMetadata | null = null;
    let metadataPointerState: MetadataPointerState | null = null;
    if (kind === "metadata") {
      if (!isLotteryV2ReleaseMetadata(payload, region)) {
        return responseJson({ error: "invalid_v2_metadata", schemaVersion: 2 }, 422);
      }
      metadataPayload = payload;
      metadataPointerState = await validateMetadataPublication(env, payload);
      key = lotteryV2MetadataKey(region);
    } else {
      const releaseId = singleParameter(url, "release", true);
      const station = singleParameter(url, "station", true);
      const rawYear = singleParameter(url, "year", true);
      const year = Number(rawYear);
      if (releaseId === null || !/^[A-Za-z0-9._-]+$/.test(releaseId)) {
        throw new ApiInputError("invalid_release", "release has an invalid format");
      }
      if (station === null || !/^[A-Za-z0-9]{2,8}$/.test(station)) {
        throw new ApiInputError("invalid_station", "station has an invalid format");
      }
      if (!Number.isSafeInteger(year) || year < 1900 || year > 9999) {
        throw new ApiInputError("invalid_year", "year must be an integer from 1900 to 9999");
      }
      if (!isLotteryV2ShardPayload(payload) || payload.releaseId !== releaseId || payload.region !== region ||
        payload.station.code !== station || payload.year !== year) {
        return responseJson({ error: "invalid_v2_shard", schemaVersion: 2 }, 422);
      }
      key = lotteryV2ShardKey(releaseId, region, station, year);
      immutable = true;
    }

    const canonicalBody = JSON.stringify(payload);
    const options = {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=300, stale-while-revalidate=3600",
      },
      customMetadata: {
        schemaVersion: "2",
        region,
        kind,
      },
    };
    let idempotent = false;
    if (immutable) {
      const stored = await env.LOTTERY_DATA.put(key, canonicalBody, {
        ...options,
        onlyIf: { etagDoesNotMatch: "*" },
      });
      if (!stored) {
        const existing = await env.LOTTERY_DATA.get(key);
        if (!existing || existing.size > MAX_INGEST_BYTES || await existing.text() !== canonicalBody) {
          return responseJson({ error: "immutable_shard_conflict", key }, 409);
        }
        return responseJson({ ok: true, key, immutable: true, idempotent: true }, 200);
      }
    } else {
      if (!metadataPayload || !metadataPointerState) {
        throw new Error("v2 metadata publication state is unavailable");
      }
      const publication = await publishMetadataPointer(
        env,
        metadataPayload,
        canonicalBody,
        options,
        metadataPointerState,
      );
      idempotent = publication.idempotent;
      if (metadataPayload) await activateV2HealthIfReady(env, metadataPayload.generatedAt);
    }
    return responseJson({ ok: true, key, immutable, idempotent }, 200);
  } catch (error) {
    if (error instanceof ApiInputError) return inputErrorResponse(error);
    if (error instanceof ReleasePublicationError) {
      return responseJson({ error: error.code, message: error.message }, 409);
    }
    if (error instanceof PayloadTooLargeError) {
      return responseJson({ error: "payload_too_large", maxBytes: MAX_INGEST_BYTES }, 413);
    }
    console.error(JSON.stringify({
      message: "lottery_v2_ingest_failed",
      error: error instanceof Error ? error.message : String(error),
    }));
    return responseJson({ error: "ingest_failed" }, 500);
  }
}
