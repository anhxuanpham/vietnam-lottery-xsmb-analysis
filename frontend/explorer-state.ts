import {
  isLotteryPrizeGroup,
  isLotteryPrizeMatch,
  lotteryPrizeGroupSupported,
  type LotteryDraw,
  type LotteryPrizeGroup,
  type LotteryPrizeMatch,
  type LotteryRegion,
} from "./lottery-contract.ts";

export type ExplorerQuery = {
  region: LotteryRegion;
  station: string;
  from: string | null;
  to: string | null;
  number: string | null;
  value?: string | null;
  match?: LotteryPrizeMatch | null;
  prizeGroup?: LotteryPrizeGroup | null;
};

export type NormalizedExplorerQuery = {
  region: LotteryRegion;
  station: string;
  from: string | null;
  to: string | null;
  number: string | null;
  value: string | null;
  match: LotteryPrizeMatch | null;
  prizeGroup: LotteryPrizeGroup | null;
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

export function normalizeExplorerQuery(query: ExplorerQuery): NormalizedExplorerQuery {
  const value = query.value ?? null;
  return {
    region: query.region,
    station: query.station,
    from: query.from,
    to: query.to,
    number: query.number,
    value,
    match: query.match ?? (value === null ? null : "exact"),
    prizeGroup: query.prizeGroup ?? null,
  };
}

export function sameExplorerQuery(left: ExplorerQuery, right: ExplorerQuery): boolean {
  const normalizedLeft = normalizeExplorerQuery(left);
  const normalizedRight = normalizeExplorerQuery(right);
  return normalizedLeft.region === normalizedRight.region &&
    normalizedLeft.station === normalizedRight.station &&
    normalizedLeft.from === normalizedRight.from &&
    normalizedLeft.to === normalizedRight.to &&
    normalizedLeft.number === normalizedRight.number &&
    normalizedLeft.value === normalizedRight.value &&
    normalizedLeft.match === normalizedRight.match &&
    normalizedLeft.prizeGroup === normalizedRight.prizeGroup;
}

function validIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

export function explorerQueryError(query: ExplorerQuery): string | null {
  const normalized = normalizeExplorerQuery(query);
  if ((normalized.from !== null && !validIsoDate(normalized.from)) ||
    (normalized.to !== null && !validIsoDate(normalized.to))) {
    return "Ngày tra cứu không hợp lệ.";
  }
  if (normalized.from !== null && normalized.to !== null && normalized.to < normalized.from) {
    return "Khoảng ngày không hợp lệ.";
  }
  if (normalized.number !== null && !/^[0-9]{2}$/.test(normalized.number)) {
    return "Đuôi loto phải gồm đúng hai chữ số từ 00 đến 99.";
  }
  if (normalized.value !== null && !/^[0-9]{1,6}$/.test(normalized.value)) {
    return "Giá trị giải phải gồm từ một đến sáu chữ số ASCII.";
  }
  if (normalized.match !== null && !isLotteryPrizeMatch(normalized.match)) {
    return "Kiểu so khớp giải không hợp lệ.";
  }
  if (normalized.prizeGroup !== null &&
    (!isLotteryPrizeGroup(normalized.prizeGroup) ||
      !lotteryPrizeGroupSupported(normalized.region, normalized.prizeGroup))) {
    return "Nhóm giải không hợp lệ cho miền đã chọn.";
  }
  if (normalized.number !== null &&
    (normalized.value !== null || normalized.match !== null || normalized.prizeGroup !== null)) {
    return "Không thể dùng đồng thời bộ lọc loto cũ và bộ lọc giải đầy đủ.";
  }
  if (normalized.value === null && (normalized.match !== null || normalized.prizeGroup !== null)) {
    return "Kiểu so khớp và nhóm giải chỉ dùng được khi có giá trị giải.";
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
