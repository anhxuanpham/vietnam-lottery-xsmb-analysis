"use client";

import { memo } from "react";
import {
  normalizeExplorerQuery,
  type ExplorerQuery,
} from "@/explorer-state";
import type { LotteryDraw } from "@/lottery-contract";
import { PRIZE_NAMES, formatDate, orderedPrizeEntries } from "./format";

function prizeMatchesExplorerQuery(
  prize: string,
  group: string,
  query: ExplorerQuery | null,
): boolean {
  if (query === null) return false;
  const normalized = normalizeExplorerQuery(query);
  if (normalized.number !== null) return prize.endsWith(normalized.number);
  if (normalized.value === null) return false;
  if (normalized.prizeGroup !== null && normalized.prizeGroup !== group) return false;
  return normalized.match === "suffix"
    ? prize.endsWith(normalized.value)
    : prize === normalized.value;
}

type ExplorerResultListProps = {
  items: LotteryDraw[];
  appliedQuery: ExplorerQuery | null;
  busy: boolean;
};

export const ExplorerResultList = memo(function ExplorerResultList({
  items,
  appliedQuery,
  busy,
}: ExplorerResultListProps) {
  const appliedExplorerQuery = appliedQuery === null
    ? null
    : normalizeExplorerQuery(appliedQuery);
  return (
    <div
      className="result-list"
      aria-busy={busy}
      aria-live="polite"
    >
      {items.map((draw) => (
        <article className="result-card" key={`${draw.stationCode}-${draw.date}`}>
          <header>
            <div><small>{draw.stationName}</small><h3>{formatDate(draw.date)}</h3></div>
            <div className="result-special"><small>Đặc biệt</small><strong>{draw.specialPrize}</strong></div>
          </header>
          <div className="prize-table">
            {orderedPrizeEntries(draw.prizes)
              .filter(([group]) =>
                appliedExplorerQuery?.prizeGroup === null ||
                appliedExplorerQuery?.prizeGroup === undefined ||
                appliedExplorerQuery.prizeGroup === group
              )
              .map(([group, prizes]) => (
                <div className={group === "special" ? "prize-row special" : "prize-row"} key={group}>
                  <span>{PRIZE_NAMES[group] ?? group}</span>
                  <div>
                    {prizes.map((prize, index) => (
                      <strong
                        className={prizeMatchesExplorerQuery(
                          prize,
                          group,
                          appliedQuery,
                        ) ? "matched" : ""}
                        key={`${prize}-${index}`}
                      >
                        {prize}
                      </strong>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </article>
      ))}
    </div>
  );
});
