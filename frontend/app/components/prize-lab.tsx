"use client";

import { memo, useMemo, type CSSProperties } from "react";
import {
  PRIZE_ANALYTICS_VERSION,
  type PrizeWindowAnalysis,
} from "@/prize-analytics";
import type { LotteryPrizeGroup, LotteryPrizeMatch } from "@/lottery-contract";
import {
  PRIZE_NAMES,
  formatDate,
  numberFormatter,
  percentFormatter,
} from "./format";

type PrizeLabProps = {
  prizeLab: PrizeWindowAnalysis | null;
  datasetVersion: string;
  openExplorerEvidence: (
    value: string,
    match?: LotteryPrizeMatch,
    prizeGroup?: LotteryPrizeGroup | "",
  ) => void;
};

export const PrizeLab = memo(function PrizeLab({
  prizeLab,
  datasetVersion,
  openExplorerEvidence,
}: PrizeLabProps) {
  const derived = useMemo(() => {
    if (prizeLab === null) return null;
    const topSpecialTails = [...prizeLab.specialPrize.tail3Frequency]
      .filter((item) => item.count > 1)
      .sort((left, right) => right.count - left.count || left.tail3.localeCompare(right.tail3))
      .slice(0, 8);
    const topSpecialHeads = [...prizeLab.specialPrize.head3Frequency]
      .filter((item) => item.count > 1)
      .sort((left, right) => right.count - left.count || left.head3.localeCompare(right.head3))
      .slice(0, 8);
    const specialTailRecency = new Map<string, number>(
      prizeLab.specialPrize.tail3Recency.map((item) => [item.tail3, item.drawsSinceLastSeen]),
    );
    const topSpecialDigitSums = [...prizeLab.specialPrize.digitSumDistribution]
      .sort((left, right) => right.count - left.count || left.digitSum - right.digitSum)
      .slice(0, 6);
    const specialPositionLeaders = prizeLab.specialPrize.positionalDigitDistributions.map((distribution) => {
      const maximum = Math.max(...distribution.digits.map((candidate) => candidate.count));
      return {
        position: distribution.positionFromLeft,
        leaders: distribution.digits.filter((candidate) => candidate.count === maximum),
      };
    });
    return {
      topSpecialTails,
      topSpecialHeads,
      specialTailRecency,
      topSpecialDigitSums,
      specialPositionLeaders,
    };
  }, [prizeLab]);

  if (prizeLab === null || derived === null) {
    return (
      <p className="prize-lab-empty" role="status">
        Dữ liệu giải trong cửa sổ này chưa vượt qua kiểm tra nhất quán (thiếu nhóm giải hoặc lệch
        định dạng) nên Prize Lab tạm ẩn. Các phân tích còn lại vẫn hiển thị từ lịch sử hiện có.
      </p>
    );
  }

  const {
    topSpecialTails,
    topSpecialHeads,
    specialTailRecency,
    topSpecialDigitSums,
    specialPositionLeaders,
  } = derived;

  return (<>
    <div className="prize-kpis" aria-label="Tổng quan giải đặc biệt">
      <article>
        <small>Mẫu giải đặc biệt</small>
        <strong>{numberFormatter.format(prizeLab.specialPrize.observations)}</strong>
        <p>{formatDate(prizeLab.dateRange.from)} — {formatDate(prizeLab.dateRange.to)}</p>
      </article>
      <article>
        <small>Giá trị phân biệt</small>
        <strong>{numberFormatter.format(prizeLab.specialPrize.distinctCount)}</strong>
        <p>{prizeLab.specialPrize.exactRepeats.length} giá trị có lặp</p>
      </article>
      <article>
        <small>Bắt đầu bằng 0</small>
        <strong>{percentFormatter.format(prizeLab.specialPrize.leadingZeroRate)}</strong>
        <p>{prizeLab.specialPrize.leadingZeroCount} / {prizeLab.specialPrize.observations} kỳ</p>
      </article>
      <article>
        <small>Đuôi chẵn / lẻ</small>
        <strong>
          {percentFormatter.format(prizeLab.specialPrize.parity.evenRate)}
          {" / "}
          {percentFormatter.format(prizeLab.specialPrize.parity.oddRate)}
        </strong>
        <p>Đếm theo chữ số cuối</p>
      </article>
    </div>

    <div className="prize-lab-layout">
      <article className="panel prize-anatomy">
        <div className="panel-heading">
          <div>
            <p className="kicker">SPECIAL PRIZE ANATOMY</p>
            <h2>Giải đặc biệt {prizeLab.specialPrize.officialWidth} số</h2>
          </div>
          <span>{PRIZE_ANALYTICS_VERSION}</span>
        </div>

        <h3>Chữ số nổi bật theo từng vị trí</h3>
        <div
          className="position-grid"
          style={{
            "--position-columns": prizeLab.specialPrize.officialWidth,
          } as CSSProperties}
        >
          {specialPositionLeaders.map(({ position, leaders }) => (
            <div key={position}>
              <small>Vị trí {position}</small>
              <strong className={leaders.length > 1 ? "has-tie" : undefined}>
                {leaders.map((leader) => leader.digit).join(" · ")}
              </strong>
              <span>
                {leaders[0].count} lần · {percentFormatter.format(leaders[0].rate)}
                {leaders.length > 1 ? ` · ${leaders.length} số đồng hạng` : ""}
              </span>
            </div>
          ))}
        </div>

        <div className="digit-presence">
          <h3>Chạm 0–9 trong giải đặc biệt</h3>
          <div
            className="digit-presence-grid"
            aria-label="Tỷ lệ kỳ mà giải đặc biệt chứa từng chữ số 0 đến 9"
          >
            {prizeLab.specialPrize.digitPresence.map((item) => (
              <div
                className="digit-presence-row"
                key={item.digit}
                title={`Chạm ${item.digit}: ${item.count}/${prizeLab.specialPrize.observations} kỳ`}
              >
                <strong>{item.digit}</strong>
                <div><i style={{ width: `${item.rate * 100}%` }} /></div>
                <small>{item.count} kỳ · {percentFormatter.format(item.rate)}</small>
              </div>
            ))}
          </div>
        </div>

        <div className="prize-pattern-columns">
          <div>
            <h3>Đuôi 3 số lặp lại</h3>
            {topSpecialTails.length > 0 ? (
              <div className="pattern-list">
                {topSpecialTails.map((item) => {
                  const drawsSince = specialTailRecency.get(item.tail3);
                  return (
                    <button
                      type="button"
                      key={item.tail3}
                      onClick={() => openExplorerEvidence(item.tail3, "suffix", "special")}
                      title={`Tra giải đặc biệt có đuôi ${item.tail3}`}
                    >
                      <strong>{item.tail3}</strong>
                      <span>
                        {item.count} lần · {percentFormatter.format(item.rate)}
                        {drawsSince !== undefined && (drawsSince === 0
                          ? " · vừa về kỳ mới nhất"
                          : ` · về cách đây ${drawsSince} kỳ`)}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="pattern-empty">Chưa có đuôi 3 số nào lặp trong cửa sổ này.</p>
            )}
          </div>
          <div>
            <h3>Đầu 3 số lặp lại</h3>
            {topSpecialHeads.length > 0 ? (
              <div className="pattern-list">
                {topSpecialHeads.map((item) => (
                  <div key={item.head3}>
                    <strong>{item.head3}</strong>
                    <span>{item.count} lần · {percentFormatter.format(item.rate)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="pattern-empty">Chưa có đầu 3 số nào lặp trong cửa sổ này.</p>
            )}
          </div>
          <div>
            <h3>Tổng chữ số phổ biến</h3>
            <div className="pattern-list digit-sums">
              {topSpecialDigitSums.map((item) => (
                <div key={item.digitSum}>
                  <strong>{item.digitSum}</strong>
                  <span>{item.count} lần · {percentFormatter.format(item.rate)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="repeat-strip">
          <h3>Giá trị đặc biệt đã lặp trong cửa sổ</h3>
          {prizeLab.specialPrize.exactRepeats.length > 0 ? (
            <div>
              {prizeLab.specialPrize.exactRepeats.slice(0, 8).map((item) => (
                <button
                  type="button"
                  key={item.formattedNumber}
                  onClick={() => openExplorerEvidence(item.formattedNumber, "exact", "special")}
                >
                  <strong>{item.formattedNumber}</strong>
                  <span>{item.count} lần</span>
                </button>
              ))}
            </div>
          ) : (
            <p>Không có giải đặc biệt trùng hoàn toàn trong cửa sổ này.</p>
          )}
        </div>
      </article>

      <article className="panel prize-groups-panel">
        <div className="panel-heading">
          <div>
            <p className="kicker">PRIZE GROUP SUMMARY</p>
            <h2>Không trộn nhóm giải</h2>
          </div>
          <span>{prizeLab.drawCount} kỳ</span>
        </div>
        <div className="prize-group-table" role="table" aria-label="Tóm tắt từng nhóm giải">
          <div className="prize-group-header" role="row">
            <span role="columnheader">Nhóm</span>
            <span role="columnheader">Rộng</span>
            <span role="columnheader">Mẫu</span>
            <span role="columnheader">Phân biệt</span>
            <span role="columnheader">Zero đầu</span>
            <span role="columnheader">Đuôi chẵn</span>
          </div>
          {prizeLab.prizeGroups.map((summary) => (
            <div className="prize-group-data" role="row" key={summary.prizeGroup}>
              <strong role="rowheader">{PRIZE_NAMES[summary.prizeGroup] ?? summary.prizeGroup}</strong>
              <span role="cell">{summary.officialWidth} số</span>
              <span role="cell">{numberFormatter.format(summary.observations)}</span>
              <span role="cell">{numberFormatter.format(summary.distinctCount)}</span>
              <span role="cell">{percentFormatter.format(summary.leadingZeroRate)}</span>
              <span role="cell">{percentFormatter.format(summary.parity.evenRate)}</span>
            </div>
          ))}
        </div>
      </article>
    </div>

    <p className="prize-lab-disclosure">
      {PRIZE_ANALYTICS_VERSION} · dataset {datasetVersion} · một đài, một cửa sổ, đúng độ dài chính
      thức. Mọi nút số mở Result Explorer để truy ngược kỳ quay gốc. Dữ liệu lịch sử không bảo đảm kết quả tương
      lai.
    </p>
  </>);
});
