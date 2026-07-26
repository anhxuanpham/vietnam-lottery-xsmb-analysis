import type { LotteryDraw } from "./lottery-contract.ts";

export const PRIZE_ANALYTICS_VERSION = "prize-descriptive-v2";

const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const KNOWN_PRIZE_GROUPS = [
  "special",
  "prize1",
  "prize2",
  "prize3",
  "prize4",
  "prize5",
  "prize6",
  "prize7",
  "prize8",
] as const;
const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const FORMATTED_PRIZE_PATTERN = /^[0-9]{2,6}$/;

export type PrizeDateRange = {
  from: string;
  to: string;
};

export type FrequencyRate = {
  count: number;
  rate: number;
};

export type DigitFrequency = FrequencyRate & {
  digit: string;
};

export type DigitSumFrequency = FrequencyRate & {
  digitSum: number;
};

export type Tail3Frequency = FrequencyRate & {
  tail3: string;
};

export type Head3Frequency = FrequencyRate & {
  head3: string;
};

export type Tail3Recency = {
  tail3: string;
  count: number;
  lastSeenDate: string;
  /** Draws after the last occurrence inside the window; 0 means the latest draw. */
  drawsSinceLastSeen: number;
};

export type ExactPrizeRepeat = FrequencyRate & {
  formattedNumber: string;
};

export type PositionalDigitDistribution = {
  /** One-based position counted from the left edge of the official formatted value. */
  positionFromLeft: number;
  digits: DigitFrequency[];
};

export type PrizeParitySummary = {
  evenCount: number;
  oddCount: number;
  evenRate: number;
  oddRate: number;
};

export type PrizeNumberMetrics = {
  officialWidth: number;
  observations: number;
  distinctCount: number;
  leadingZeroCount: number;
  leadingZeroRate: number;
  digitSumDistribution: DigitSumFrequency[];
  parity: PrizeParitySummary;
  exactRepeats: ExactPrizeRepeat[];
  positionalDigitDistributions: PositionalDigitDistribution[];
  /** Per digit 0-9: observations containing the digit at least once ("chạm"), each counted once. */
  digitPresence: DigitFrequency[];
  tail3Frequency: Tail3Frequency[];
  /** First-three-digit frequency; empty below width 4 so it never mirrors tail3Frequency. */
  head3Frequency: Head3Frequency[];
};

export type PrizeWindowIdentity = {
  analyticsVersion: typeof PRIZE_ANALYTICS_VERSION;
  analysisType: "descriptive";
  stationCode: string;
  stationName: string;
  drawCount: number;
  dateRange: PrizeDateRange;
};

export type SpecialPrizeAnatomy = PrizeWindowIdentity & PrizeNumberMetrics & {
  prizeGroup: "special";
  tail3Recency: Tail3Recency[];
};

export type PrizeGroupSummary = PrizeNumberMetrics & {
  prizeGroup: string;
  drawsObserved: number;
  resultsPerDraw: number;
};

export type PrizeWindowAnalysis = PrizeWindowIdentity & {
  specialPrize: SpecialPrizeAnatomy;
  prizeGroups: PrizeGroupSummary[];
};

type PreparedPrizeWindow = {
  identity: PrizeWindowIdentity;
  draws: LotteryDraw[];
  prizeGroups: string[];
};

export class PrizeAnalyticsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PrizeAnalyticsError";
  }
}

function validIsoDate(value: string): boolean {
  if (!ISO_DATE_PATTERN.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function prizeGroupRank(group: string): number {
  const index = KNOWN_PRIZE_GROUPS.indexOf(group as (typeof KNOWN_PRIZE_GROUPS)[number]);
  return index === -1 ? KNOWN_PRIZE_GROUPS.length : index;
}

function sortPrizeGroups(groups: Iterable<string>): string[] {
  return [...groups].sort(
    (left, right) =>
      prizeGroupRank(left) - prizeGroupRank(right) || left.localeCompare(right),
  );
}

function sameStrings(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function requireFormattedPrize(value: unknown, prizeGroup: string, drawDate: string): asserts value is string {
  if (typeof value !== "string" || !FORMATTED_PRIZE_PATTERN.test(value)) {
    throw new PrizeAnalyticsError(
      `${prizeGroup} contains an invalid formatted prize on ${drawDate}`,
    );
  }
}

function preparePrizeWindow(input: readonly LotteryDraw[]): PreparedPrizeWindow {
  if (input.length === 0) {
    throw new PrizeAnalyticsError("prize analytics requires at least one draw");
  }
  const draws = [...input].sort((left, right) => left.date.localeCompare(right.date));
  const stationCode = draws[0].stationCode;
  if (!stationCode || draws.some((draw) => draw.stationCode !== stationCode)) {
    throw new PrizeAnalyticsError("prize analytics cannot mix draws from different stations");
  }

  const seenDates = new Set<string>();
  let expectedGroups: string[] | null = null;
  const expectedCountByGroup = new Map<string, number>();
  const expectedWidthByGroup = new Map<string, number>();

  for (const draw of draws) {
    if (!validIsoDate(draw.date)) {
      throw new PrizeAnalyticsError(`prize analytics received an invalid draw date: ${draw.date}`);
    }
    if (seenDates.has(draw.date)) {
      throw new PrizeAnalyticsError(
        `prize analytics cannot contain duplicate station/date draws: ${draw.date}`,
      );
    }
    seenDates.add(draw.date);

    const groups = sortPrizeGroups(Object.keys(draw.prizes));
    if (groups.length === 0 || !groups.includes("special")) {
      throw new PrizeAnalyticsError(`draw ${draw.date} has no special prize group`);
    }
    if (expectedGroups === null) {
      expectedGroups = groups;
    } else if (!sameStrings(groups, expectedGroups)) {
      throw new PrizeAnalyticsError("prize analytics requires the same prize groups in every draw");
    }

    for (const group of groups) {
      const values: unknown = draw.prizes[group];
      if (!Array.isArray(values) || values.length === 0) {
        throw new PrizeAnalyticsError(`${group} has no observations on ${draw.date}`);
      }
      for (const value of values) requireFormattedPrize(value, group, draw.date);

      const width = values[0].length;
      if (values.some((value) => value.length !== width)) {
        throw new PrizeAnalyticsError(`${group} mixes official widths on ${draw.date}`);
      }
      const expectedCount = expectedCountByGroup.get(group);
      const expectedWidth = expectedWidthByGroup.get(group);
      if (expectedCount === undefined) {
        expectedCountByGroup.set(group, values.length);
        expectedWidthByGroup.set(group, width);
      } else if (values.length !== expectedCount) {
        throw new PrizeAnalyticsError(`${group} changes result count across the selected window`);
      } else if (width !== expectedWidth) {
        throw new PrizeAnalyticsError(`${group} mixes official widths across the selected window`);
      }
    }

    const specialValues = draw.prizes.special;
    if (specialValues.length !== 1 || specialValues[0] !== draw.specialPrize) {
      throw new PrizeAnalyticsError(`special prize contract mismatch on ${draw.date}`);
    }
    if (draw.specialTail !== draw.specialPrize.slice(-2)) {
      throw new PrizeAnalyticsError(`special prize tail contract mismatch on ${draw.date}`);
    }
  }

  const latest = draws.at(-1) ?? draws[0];
  return {
    identity: {
      analyticsVersion: PRIZE_ANALYTICS_VERSION,
      analysisType: "descriptive",
      stationCode,
      stationName: latest.stationName,
      drawCount: draws.length,
      dateRange: {
        from: draws[0].date,
        to: latest.date,
      },
    },
    draws,
    prizeGroups: expectedGroups ?? [],
  };
}

function frequencyRate(count: number, observations: number): FrequencyRate {
  return { count, rate: count / observations };
}

function prizeNumberMetrics(values: readonly string[]): PrizeNumberMetrics {
  const observations = values.length;
  const officialWidth = values[0].length;
  const exactCounts = new Map<string, number>();
  const digitSumCounts = new Map<number, number>();
  const tail3Counts = new Map<string, number>();
  const head3Counts = new Map<string, number>();
  const digitPresenceCounts = new Map<string, number>(DIGITS.map((digit) => [digit, 0]));
  const positionCounts = Array.from(
    { length: officialWidth },
    () => new Map<string, number>(DIGITS.map((digit) => [digit, 0])),
  );
  let leadingZeroCount = 0;
  let evenCount = 0;

  for (const formattedNumber of values) {
    exactCounts.set(formattedNumber, (exactCounts.get(formattedNumber) ?? 0) + 1);
    if (formattedNumber.startsWith("0")) leadingZeroCount += 1;
    if (Number(formattedNumber.at(-1)) % 2 === 0) evenCount += 1;

    let digitSum = 0;
    for (let position = 0; position < officialWidth; position += 1) {
      const digit = formattedNumber[position];
      digitSum += Number(digit);
      const counts = positionCounts[position];
      counts.set(digit, (counts.get(digit) ?? 0) + 1);
    }
    digitSumCounts.set(digitSum, (digitSumCounts.get(digitSum) ?? 0) + 1);
    for (const digit of new Set(formattedNumber)) {
      digitPresenceCounts.set(digit, (digitPresenceCounts.get(digit) ?? 0) + 1);
    }
    if (officialWidth >= 3) {
      const tail3 = formattedNumber.slice(-3);
      tail3Counts.set(tail3, (tail3Counts.get(tail3) ?? 0) + 1);
    }
    // Below width 4 the first three digits equal the tail, so head3 stays empty.
    if (officialWidth >= 4) {
      const head3 = formattedNumber.slice(0, 3);
      head3Counts.set(head3, (head3Counts.get(head3) ?? 0) + 1);
    }
  }

  const oddCount = observations - evenCount;
  return {
    officialWidth,
    observations,
    distinctCount: exactCounts.size,
    leadingZeroCount,
    leadingZeroRate: leadingZeroCount / observations,
    digitSumDistribution: [...digitSumCounts.entries()]
      .sort(([left], [right]) => left - right)
      .map(([digitSum, count]) => ({ digitSum, ...frequencyRate(count, observations) })),
    parity: {
      evenCount,
      oddCount,
      evenRate: evenCount / observations,
      oddRate: oddCount / observations,
    },
    exactRepeats: [...exactCounts.entries()]
      .filter(([, count]) => count > 1)
      .sort(([leftValue, leftCount], [rightValue, rightCount]) =>
        rightCount - leftCount || leftValue.localeCompare(rightValue)
      )
      .map(([formattedNumber, count]) => ({
        formattedNumber,
        ...frequencyRate(count, observations),
      })),
    positionalDigitDistributions: positionCounts.map((counts, index) => ({
      positionFromLeft: index + 1,
      digits: DIGITS.map((digit) => ({
        digit,
        ...frequencyRate(counts.get(digit) ?? 0, observations),
      })),
    })),
    digitPresence: DIGITS.map((digit) => ({
      digit,
      ...frequencyRate(digitPresenceCounts.get(digit) ?? 0, observations),
    })),
    tail3Frequency: [...tail3Counts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([tail3, count]) => ({ tail3, ...frequencyRate(count, observations) })),
    head3Frequency: [...head3Counts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([head3, count]) => ({ head3, ...frequencyRate(count, observations) })),
  };
}

// Relies on prepared draws being sorted ascending with one special value per draw.
function specialTail3Recency(draws: readonly LotteryDraw[]): Tail3Recency[] {
  if (draws[0].specialPrize.length < 3) return [];
  const occurrences = new Map<string, { count: number; lastIndex: number }>();
  draws.forEach((draw, index) => {
    const tail3 = draw.specialPrize.slice(-3);
    const entry = occurrences.get(tail3);
    if (entry) {
      entry.count += 1;
      entry.lastIndex = index;
    } else {
      occurrences.set(tail3, { count: 1, lastIndex: index });
    }
  });
  return [...occurrences.entries()]
    .sort(([leftTail, left], [rightTail, right]) =>
      right.count - left.count || leftTail.localeCompare(rightTail))
    .map(([tail3, { count, lastIndex }]) => ({
      tail3,
      count,
      lastSeenDate: draws[lastIndex].date,
      drawsSinceLastSeen: draws.length - 1 - lastIndex,
    }));
}

function specialPrizeAnatomy(prepared: PreparedPrizeWindow): SpecialPrizeAnatomy {
  return {
    ...prepared.identity,
    prizeGroup: "special",
    ...prizeNumberMetrics(prepared.draws.map((draw) => draw.specialPrize)),
    tail3Recency: specialTail3Recency(prepared.draws),
  };
}

function prizeGroupSummaries(prepared: PreparedPrizeWindow): PrizeGroupSummary[] {
  return prepared.prizeGroups.map((prizeGroup) => {
    const values = prepared.draws.flatMap((draw) => draw.prizes[prizeGroup]);
    return {
      prizeGroup,
      drawsObserved: prepared.draws.length,
      resultsPerDraw: prepared.draws[0].prizes[prizeGroup].length,
      ...prizeNumberMetrics(values),
    };
  });
}

/**
 * Describes full special-prize values in one station/window. It does not score
 * or forecast future outcomes.
 */
export function analyzeSpecialPrizeAnatomy(
  draws: readonly LotteryDraw[],
): SpecialPrizeAnatomy {
  return specialPrizeAnatomy(preparePrizeWindow(draws));
}

/**
 * Describes each official prize group independently so values with different
 * widths are never combined into the same positional distribution.
 */
export function summarizePrizeGroups(
  draws: readonly LotteryDraw[],
): PrizeGroupSummary[] {
  return prizeGroupSummaries(preparePrizeWindow(draws));
}

/** Builds both descriptive Prize Lab surfaces from the same validated window. */
export function analyzePrizeWindow(
  draws: readonly LotteryDraw[],
): PrizeWindowAnalysis {
  const prepared = preparePrizeWindow(draws);
  return {
    ...prepared.identity,
    specialPrize: specialPrizeAnatomy(prepared),
    prizeGroups: prizeGroupSummaries(prepared),
  };
}
