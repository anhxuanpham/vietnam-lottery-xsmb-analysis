"use client";

import { memo } from "react";

type SignalStackProps = {
  hot: Array<[string, number]>;
  cold: Array<[string, number]>;
  maxFrequency: number;
  momentum: Array<{ number: string; score: number }>;
};

export const SignalStack = memo(function SignalStack({
  hot,
  cold,
  maxFrequency,
  momentum,
}: SignalStackProps) {
  return (
    <aside className="signal-stack">
      <article className="panel signal-panel">
        <div className="panel-heading"><div><p className="kicker">SIGNALS</p><h2>Nóng / lạnh</h2></div></div>
        <div className="rank-columns">
          <div><h3>Tần suất cao</h3>{hot.map(([number, count], index) => <div className="rank-row" key={number}><span>{index + 1}</span><strong>{number}</strong><div><i style={{ width: `${(count / maxFrequency) * 100}%` }} /></div><small>{count}</small></div>)}</div>
          <div><h3>Tần suất thấp</h3>{cold.map(([number, count], index) => <div className="rank-row cold" key={number}><span>{index + 1}</span><strong>{number}</strong><div><i style={{ width: `${(count / maxFrequency) * 100}%` }} /></div><small>{count}</small></div>)}</div>
        </div>
      </article>

      <article className="panel momentum-panel">
        <div className="panel-heading"><div><p className="kicker">7D VS 30D</p><h2>Đà tăng ngắn hạn</h2></div></div>
        {momentum.map((item) => (
          <div className="momentum-row" key={item.number}>
            <strong>{item.number}</strong>
            <div><i style={{ width: `${Math.max(8, Math.min(100, 50 + item.score * 210))}%` }} /></div>
            <span>{item.score >= 0 ? "+" : ""}{item.score.toFixed(2)}/kỳ</span>
          </div>
        ))}
      </article>
    </aside>
  );
});
