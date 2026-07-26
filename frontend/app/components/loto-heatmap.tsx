"use client";

import { memo } from "react";
import { heatCellColors } from "@/heat-color";

type LotoHeatmapProps = {
  counts: Record<string, number>;
  maxFrequency: number;
  activeWindow: number;
  openExplorerEvidence: (value: string) => void;
};

export const LotoHeatmap = memo(function LotoHeatmap({
  counts,
  maxFrequency,
  activeWindow,
  openExplorerEvidence,
}: LotoHeatmapProps) {
  return (
    <article className="panel heatmap-panel">
      <div className="panel-heading">
        <div><p className="kicker">DISTRIBUTION</p><h2>Heatmap 00–99</h2></div>
        <span>{activeWindow} kỳ</span>
      </div>
      <div className="heatmap" aria-label="Tần suất loto từ 00 đến 99">
        {Array.from({ length: 100 }, (_, index) => String(index).padStart(2, "0")).map((number) => {
          const intensity = counts[number] / maxFrequency;
          return (
            <button
              type="button"
              className="heat-cell"
              key={number}
              onClick={() => openExplorerEvidence(number)}
              style={heatCellColors(intensity)}
              title={`${number}: ${counts[number]} lần`}
              aria-label={`Tra các giải có đuôi ${number}, xuất hiện ${counts[number]} lần`}
            >
              <strong>{number}</strong><small>{counts[number]}</small>
            </button>
          );
        })}
      </div>
      <div className="heat-legend"><span>Ít</span><i /><i /><i /><i /><i /><span>Nhiều</span></div>
    </article>
  );
});
