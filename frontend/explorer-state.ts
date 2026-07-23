import type { LotteryDraw, LotteryRegion } from "./lottery-contract.ts";

export type ExplorerQuery = {
  region: LotteryRegion;
  station: string;
  from: string | null;
  to: string | null;
  number: string | null;
};

export type ExplorerStatus = "idle" | "loading" | "ready" | "empty" | "error";

export type ExplorerState = {
  status: ExplorerStatus;
  appliedQuery: ExplorerQuery | null;
  items: LotteryDraw[];
  cursor: string | null;
  error: string | null;
  appending: boolean;
};

export const INITIAL_EXPLORER_STATE: ExplorerState = {
  status: "idle",
  appliedQuery: null,
  items: [],
  cursor: null,
  error: null,
  appending: false,
};

export function sameExplorerQuery(left: ExplorerQuery, right: ExplorerQuery): boolean {
  return left.region === right.region &&
    left.station === right.station &&
    left.from === right.from &&
    left.to === right.to &&
    left.number === right.number;
}

function validIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

export function explorerQueryError(query: ExplorerQuery): string | null {
  if ((query.from !== null && !validIsoDate(query.from)) ||
    (query.to !== null && !validIsoDate(query.to))) {
    return "Ngày tra cứu không hợp lệ.";
  }
  if (query.from !== null && query.to !== null && query.to < query.from) {
    return "Khoảng ngày không hợp lệ.";
  }
  if (query.number !== null && !/^\d{2}$/.test(query.number)) {
    return "Đuôi loto phải gồm đúng hai chữ số từ 00 đến 99.";
  }
  return null;
}

export function beginExplorerRequest(
  current: ExplorerState,
  query: ExplorerQuery,
  append: boolean,
): ExplorerState {
  const canAppend = append &&
    current.appliedQuery !== null &&
    sameExplorerQuery(current.appliedQuery, query);
  return {
    status: "loading",
    appliedQuery: query,
    items: canAppend ? current.items : [],
    cursor: canAppend ? current.cursor : null,
    error: null,
    appending: canAppend,
  };
}

function drawKey(draw: LotteryDraw): string {
  return `${draw.stationCode}|${draw.date}`;
}

export function completeExplorerRequest(
  current: ExplorerState,
  query: ExplorerQuery,
  pageItems: LotteryDraw[],
  nextCursor: string | null,
  append: boolean,
): ExplorerState {
  if (current.appliedQuery === null || !sameExplorerQuery(current.appliedQuery, query)) {
    return current;
  }
  const source = append ? [...current.items, ...pageItems] : pageItems;
  const items = [...new Map(source.map((draw) => [drawKey(draw), draw])).values()]
    .sort((left, right) =>
      right.date.localeCompare(left.date) || left.stationCode.localeCompare(right.stationCode)
    );
  return {
    status: items.length === 0 ? "empty" : "ready",
    appliedQuery: query,
    items,
    cursor: nextCursor,
    error: null,
    appending: false,
  };
}

export function failExplorerRequest(
  current: ExplorerState,
  query: ExplorerQuery,
  message: string,
): ExplorerState {
  if (current.appliedQuery === null || !sameExplorerQuery(current.appliedQuery, query)) {
    return current;
  }
  return {
    ...current,
    status: "error",
    cursor: null,
    error: message,
    appending: false,
  };
}
