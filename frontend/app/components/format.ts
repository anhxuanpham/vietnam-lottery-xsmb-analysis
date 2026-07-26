import {
  LOTTERY_PRIZE_GROUPS,
  type LotteryPrizeGroup,
} from "@/lottery-contract";

export const numberFormatter = new Intl.NumberFormat("vi-VN");
export const percentFormatter = new Intl.NumberFormat("vi-VN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export const PRIZE_NAMES: Record<string, string> = {
  special: "Đặc biệt",
  prize1: "Giải nhất",
  prize2: "Giải nhì",
  prize3: "Giải ba",
  prize4: "Giải tư",
  prize5: "Giải năm",
  prize6: "Giải sáu",
  prize7: "Giải bảy",
  prize8: "Giải tám",
  // Preserve compatibility with older serving payloads.
  first: "Giải nhất",
  second: "Giải nhì",
  third: "Giải ba",
  fourth: "Giải tư",
  fifth: "Giải năm",
  sixth: "Giải sáu",
  seventh: "Giải bảy",
  eighth: "Giải tám",
};

const dateFormatter = new Intl.DateTimeFormat("vi-VN", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const timestampFormatter = new Intl.DateTimeFormat("vi-VN", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDate(value: string) {
  return dateFormatter.format(new Date(`${value}T00:00:00+07:00`));
}

export function formatTimestamp(value: string | null | undefined) {
  if (!value) return "Chưa có bằng chứng chạy";
  return timestampFormatter.format(new Date(value));
}

export function downloadJson(filename: string, payload: unknown) {
  const url = URL.createObjectURL(
    new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function orderedPrizeEntries(prizes: Record<string, string[]>): Array<[LotteryPrizeGroup, string[]]> {
  return LOTTERY_PRIZE_GROUPS.flatMap((group) =>
    Object.hasOwn(prizes, group) ? [[group, prizes[group]] as [LotteryPrizeGroup, string[]]] : []
  );
}
