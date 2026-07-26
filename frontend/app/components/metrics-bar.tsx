"use client";

import { memo } from "react";
import { formatDate, numberFormatter } from "./format";

type MetricsBarProps = {
  drawCount: number;
  resultCount: number;
  rangeFrom: string;
  resultsPerDraw: number;
  activeWindow: number;
  evaluationCount: number;
};

export const MetricsBar = memo(function MetricsBar({
  drawCount,
  resultCount,
  rangeFrom,
  resultsPerDraw,
  activeWindow,
  evaluationCount,
}: MetricsBarProps) {
  return (
    <section className="metrics" aria-label="Chỉ số dữ liệu">
      <article><span>01</span><small>Tổng kỳ quay</small><strong>{numberFormatter.format(drawCount)}</strong><p>Từ {formatDate(rangeFrom)}</p></article>
      <article><span>02</span><small>Kết quả quan sát</small><strong>{numberFormatter.format(resultCount)}</strong><p>{resultsPerDraw} kết quả mỗi kỳ / đài</p></article>
      <article><span>03</span><small>Cửa sổ mô hình</small><strong>{activeWindow} kỳ</strong><p>Đang được áp dụng</p></article>
      <article><span>04</span><small>Backtest gần nhất</small><strong>{evaluationCount} kỳ</strong><p>Walk-forward · baseline 10%</p></article>
    </section>
  );
});
