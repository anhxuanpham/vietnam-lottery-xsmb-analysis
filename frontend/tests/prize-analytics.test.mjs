import assert from "node:assert/strict";
import test from "node:test";

import {
  PRIZE_ANALYTICS_VERSION,
  analyzePrizeWindow,
  analyzeSpecialPrizeAnatomy,
  summarizePrizeGroups,
} from "../prize-analytics.ts";

function numbersFromPrizes(prizes) {
  return Object.values(prizes).flat().map((value) => value.slice(-2));
}

function lotteryDraw(date, stationCode, stationName, prizes) {
  const specialPrize = prizes.special[0];
  return {
    date,
    stationCode,
    stationName,
    specialPrize,
    specialTail: specialPrize.slice(-2),
    numbers: numbersFromPrizes(prizes),
    prizes,
  };
}

function xsmnDraw(date, specialPrize, prize8 = "20") {
  return lotteryDraw(date, "TN", "Tây Ninh", {
    special: [specialPrize],
    prize1: ["34175"],
    prize2: ["77274"],
    prize3: ["30384", "89567"],
    prize4: ["14773", "49466", "96992", "63553", "18749", "76798", "72637"],
    prize5: ["3614"],
    prize6: ["9697", "0371", "1937"],
    prize7: ["778"],
    prize8: [prize8],
  });
}

function xsmbDraw(date, specialPrize, prize1 = "01234") {
  return lotteryDraw(date, "xsmb", "Miền Bắc", {
    special: [specialPrize],
    prize1: [prize1],
    prize2: ["95698", "11630"],
    prize3: ["79516", "26391", "68013", "27471", "97978", "34710"],
    prize4: ["1339", "1663", "1679", "0296"],
    prize5: ["0481", "7361", "9785", "7077", "4530", "8255"],
    prize6: ["388", "553", "179"],
    prize7: ["89", "73", "76", "77"],
  });
}

function digitCount(distribution, positionFromLeft, digit) {
  return distribution
    .find((position) => position.positionFromLeft === positionFromLeft)
    .digits.find((bucket) => bucket.digit === digit).count;
}

test("special-prize anatomy preserves six-digit leading zeros and is deterministic", () => {
  const chronological = [
    xsmnDraw("2026-07-02", "005113", "07"),
    xsmnDraw("2026-07-09", "123114", "18"),
    xsmnDraw("2026-07-16", "005113", "07"),
  ];
  const anatomy = analyzeSpecialPrizeAnatomy([...chronological].reverse());

  assert.equal(anatomy.analyticsVersion, PRIZE_ANALYTICS_VERSION);
  assert.equal(anatomy.analysisType, "descriptive");
  assert.equal(anatomy.stationCode, "TN");
  assert.deepEqual(anatomy.dateRange, { from: "2026-07-02", to: "2026-07-16" });
  assert.equal(anatomy.drawCount, 3);
  assert.equal(anatomy.officialWidth, 6);
  assert.equal(anatomy.observations, 3);
  assert.equal(anatomy.distinctCount, 2);
  assert.equal(anatomy.leadingZeroCount, 2);
  assert.equal(anatomy.leadingZeroRate, 2 / 3);
  assert.deepEqual(anatomy.digitSumDistribution, [
    { digitSum: 10, count: 2, rate: 2 / 3 },
    { digitSum: 12, count: 1, rate: 1 / 3 },
  ]);
  assert.deepEqual(anatomy.parity, {
    evenCount: 1,
    oddCount: 2,
    evenRate: 1 / 3,
    oddRate: 2 / 3,
  });
  assert.deepEqual(anatomy.exactRepeats, [
    { formattedNumber: "005113", count: 2, rate: 2 / 3 },
  ]);
  assert.equal(digitCount(anatomy.positionalDigitDistributions, 1, "0"), 2);
  assert.equal(digitCount(anatomy.positionalDigitDistributions, 1, "1"), 1);
  assert.deepEqual(anatomy.tail3Frequency, [
    { tail3: "113", count: 2, rate: 2 / 3 },
    { tail3: "114", count: 1, rate: 1 / 3 },
  ]);
  assert.equal(
    anatomy.digitSumDistribution.reduce((total, bucket) => total + bucket.count, 0),
    anatomy.observations,
  );
  assert.equal(
    anatomy.tail3Frequency.reduce((total, bucket) => total + bucket.count, 0),
    anatomy.observations,
  );
  assert.ok(
    Math.abs(
      anatomy.digitSumDistribution.reduce((total, bucket) => total + bucket.rate, 0) - 1,
    ) < Number.EPSILON * 4,
  );
  assert.ok(
    anatomy.positionalDigitDistributions.every((position) =>
      position.digits.length === 10 &&
      position.digits.reduce((total, bucket) => total + bucket.count, 0) === anatomy.observations
    ),
  );
  assert.equal(anatomy.parity.evenRate + anatomy.parity.oddRate, 1);
  assert.deepEqual(
    anatomy,
    analyzeSpecialPrizeAnatomy(chronological),
    "input order must not change descriptive output",
  );
});

test("analytics version pins the v2 descriptive metrics", () => {
  assert.equal(PRIZE_ANALYTICS_VERSION, "prize-descriptive-v2");
});

test("digit presence counts each observation once per digit", () => {
  const chronological = [
    xsmnDraw("2026-07-02", "005113"),
    xsmnDraw("2026-07-09", "123114"),
    xsmnDraw("2026-07-16", "005113"),
  ];
  const anatomy = analyzeSpecialPrizeAnatomy(chronological);

  // "1" appears twice in 005113 and three times in 123114, yet each draw adds 1.
  assert.deepEqual(anatomy.digitPresence, [
    { digit: "0", count: 2, rate: 2 / 3 },
    { digit: "1", count: 3, rate: 1 },
    { digit: "2", count: 1, rate: 1 / 3 },
    { digit: "3", count: 3, rate: 1 },
    { digit: "4", count: 1, rate: 1 / 3 },
    { digit: "5", count: 2, rate: 2 / 3 },
    { digit: "6", count: 0, rate: 0 },
    { digit: "7", count: 0, rate: 0 },
    { digit: "8", count: 0, rate: 0 },
    { digit: "9", count: 0, rate: 0 },
  ]);
  assert.deepEqual(
    anatomy.digitPresence,
    analyzeSpecialPrizeAnatomy([...chronological].reverse()).digitPresence,
  );
});

test("head3 frequency covers widths 4-6 and stays empty at width 3 and below", () => {
  const sixDigit = analyzeSpecialPrizeAnatomy([
    xsmnDraw("2026-07-02", "005113"),
    xsmnDraw("2026-07-09", "123114"),
    xsmnDraw("2026-07-16", "005113"),
  ]);
  assert.deepEqual(sixDigit.head3Frequency, [
    { head3: "005", count: 2, rate: 2 / 3 },
    { head3: "123", count: 1, rate: 1 / 3 },
  ]);
  assert.equal(
    sixDigit.head3Frequency.reduce((total, bucket) => total + bucket.count, 0),
    sixDigit.observations,
  );

  const summaries = summarizePrizeGroups([
    xsmbDraw("2026-07-22", "09673"),
    xsmbDraw("2026-07-23", "19674"),
  ]);
  const special = summaries.find((summary) => summary.prizeGroup === "special");
  assert.equal(special.officialWidth, 5);
  assert.deepEqual(special.head3Frequency, [
    { head3: "096", count: 1, rate: 1 / 2 },
    { head3: "196", count: 1, rate: 1 / 2 },
  ]);
  const prize1 = summaries.find((summary) => summary.prizeGroup === "prize1");
  assert.deepEqual(prize1.head3Frequency, [{ head3: "012", count: 2, rate: 1 }]);

  const prize6 = summaries.find((summary) => summary.prizeGroup === "prize6");
  assert.equal(prize6.officialWidth, 3);
  assert.deepEqual(prize6.head3Frequency, [], "width 3 must not duplicate tail3Frequency");
  assert.ok(prize6.tail3Frequency.length > 0);
  const prize7 = summaries.find((summary) => summary.prizeGroup === "prize7");
  assert.deepEqual(prize7.head3Frequency, []);
});

test("special tail3 recency orders by count then tail and counts draws after last occurrence", () => {
  const chronological = [
    xsmnDraw("2026-07-02", "005113"),
    xsmnDraw("2026-07-09", "123114"),
    xsmnDraw("2026-07-16", "005113"),
  ];
  const anatomy = analyzeSpecialPrizeAnatomy(chronological);
  assert.deepEqual(anatomy.tail3Recency, [
    { tail3: "113", count: 2, lastSeenDate: "2026-07-16", drawsSinceLastSeen: 0 },
    { tail3: "114", count: 1, lastSeenDate: "2026-07-09", drawsSinceLastSeen: 1 },
  ]);
  assert.deepEqual(
    anatomy.tail3Recency,
    analyzeSpecialPrizeAnatomy([...chronological].reverse()).tail3Recency,
    "input order must not change recency output",
  );

  const tied = analyzeSpecialPrizeAnatomy([
    ...chronological,
    xsmnDraw("2026-07-23", "123114"),
  ]);
  assert.deepEqual(tied.tail3Recency, [
    { tail3: "113", count: 2, lastSeenDate: "2026-07-16", drawsSinceLastSeen: 1 },
    { tail3: "114", count: 2, lastSeenDate: "2026-07-23", drawsSinceLastSeen: 0 },
  ]);
});

test("special tail3 recency is empty below official width 3", () => {
  const anatomy = analyzeSpecialPrizeAnatomy([
    lotteryDraw("2026-07-16", "XX", "Đài hai số", { special: ["13"] }),
  ]);
  assert.equal(anatomy.officialWidth, 2);
  assert.deepEqual(anatomy.tail3Recency, []);
  assert.deepEqual(anatomy.tail3Frequency, []);
});

test("prize-group summaries keep XSMB groups and official widths separate", () => {
  const summaries = summarizePrizeGroups([
    xsmbDraw("2026-07-22", "09673"),
    xsmbDraw("2026-07-23", "19674"),
  ]);

  assert.deepEqual(
    summaries.map((summary) => summary.prizeGroup),
    ["special", "prize1", "prize2", "prize3", "prize4", "prize5", "prize6", "prize7"],
  );
  const prize1 = summaries.find((summary) => summary.prizeGroup === "prize1");
  assert.equal(prize1.officialWidth, 5);
  assert.equal(prize1.observations, 2);
  assert.equal(prize1.resultsPerDraw, 1);
  assert.equal(prize1.leadingZeroCount, 2);
  assert.deepEqual(prize1.exactRepeats, [
    { formattedNumber: "01234", count: 2, rate: 1 },
  ]);

  const prize3 = summaries.find((summary) => summary.prizeGroup === "prize3");
  assert.equal(prize3.officialWidth, 5);
  assert.equal(prize3.observations, 12);
  assert.equal(prize3.resultsPerDraw, 6);
  assert.equal(prize3.positionalDigitDistributions.length, 5);

  const prize7 = summaries.find((summary) => summary.prizeGroup === "prize7");
  assert.equal(prize7.officialWidth, 2);
  assert.equal(prize7.observations, 8);
  assert.deepEqual(prize7.tail3Frequency, []);
  assert.equal(prize7.parity.evenCount + prize7.parity.oddCount, prize7.observations);
});

test("combined Prize Lab analysis is pure and reuses one station window", () => {
  const draws = [
    xsmnDraw("2026-07-09", "123114", "18"),
    xsmnDraw("2026-07-16", "005113", "07"),
  ];
  const untouched = structuredClone(draws);
  const analysis = analyzePrizeWindow(draws);

  assert.deepEqual(draws, untouched);
  assert.equal(analysis.analyticsVersion, PRIZE_ANALYTICS_VERSION);
  assert.equal(analysis.analysisType, "descriptive");
  assert.equal(analysis.stationName, "Tây Ninh");
  assert.equal(analysis.specialPrize.prizeGroup, "special");
  assert.equal(analysis.specialPrize.observations, 2);
  assert.equal(analysis.prizeGroups.at(-1).prizeGroup, "prize8");
  assert.equal(analysis.prizeGroups.at(-1).officialWidth, 2);
});

test("Prize Lab rejects cross-station and duplicate-date windows", () => {
  const first = xsmnDraw("2026-07-16", "005113");
  const otherStation = {
    ...xsmnDraw("2026-07-23", "123114"),
    stationCode: "AG",
    stationName: "An Giang",
  };
  assert.throws(
    () => analyzePrizeWindow([first, otherStation]),
    /cannot mix draws from different stations/,
  );
  assert.throws(
    () => analyzePrizeWindow([first, structuredClone(first)]),
    /duplicate station\/date/,
  );
});

test("Prize Lab rejects mixed official widths and special-prize contract drift", () => {
  const first = xsmnDraw("2026-07-16", "005113");
  const mixedWidth = xsmnDraw("2026-07-23", "95113");
  assert.throws(
    () => summarizePrizeGroups([first, mixedWidth]),
    /special mixes official widths across the selected window/,
  );

  const mismatch = xsmnDraw("2026-07-23", "123114");
  mismatch.specialPrize = "999999";
  mismatch.specialTail = "99";
  assert.throws(
    () => analyzeSpecialPrizeAnatomy([mismatch]),
    /special prize contract mismatch/,
  );
});
