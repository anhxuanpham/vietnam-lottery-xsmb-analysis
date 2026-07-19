"use client";

import { useEffect, useMemo, useState } from "react";
import {
  LOTTERY_REGIONS,
  normalizeLotteryDashboardData,
  regionName,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryRegion,
} from "@/lottery-contract";

type ModelKind = "frequency" | "gap" | "balanced";

type ModelResult = {
  kind: ModelKind;
  name: string;
  eyebrow: string;
  description: string;
  picks: string[];
  coverage: number;
  lift: number;
};

const WINDOW_OPTIONS = [30, 90, 180, 365] as const;
const numberFormatter = new Intl.NumberFormat("vi-VN");
const percentFormatter = new Intl.NumberFormat("vi-VN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function frequencies(draws: LotteryDraw[]) {
  const counts = Object.fromEntries(
    Array.from({ length: 100 }, (_, index) => [String(index).padStart(2, "0"), 0]),
  ) as Record<string, number>;

  for (const draw of draws) {
    for (const number of draw.numbers) counts[number] += 1;
  }
  return counts;
}

function gaps(draws: LotteryDraw[]) {
  const latestIndex = draws.length - 1;
  const lastSeen = Object.fromEntries(
    Array.from({ length: 100 }, (_, index) => [String(index).padStart(2, "0"), -1]),
  ) as Record<string, number>;

  draws.forEach((draw, index) => {
    for (const number of new Set(draw.numbers)) lastSeen[number] = index;
  });

  return Object.fromEntries(
    Object.entries(lastSeen).map(([number, index]) => [
      number,
      index < 0 ? draws.length : latestIndex - index,
    ]),
  ) as Record<string, number>;
}

function pickNumbers(draws: LotteryDraw[], kind: ModelKind) {
  const counts = frequencies(draws);
  const drawGaps = gaps(draws);
  const maxFrequency = Math.max(...Object.values(counts), 1);
  const maxGap = Math.max(...Object.values(drawGaps), 1);

  return Object.keys(counts)
    .map((number) => {
      const frequencyScore = counts[number] / maxFrequency;
      const gapScore = drawGaps[number] / maxGap;
      const score =
        kind === "frequency"
          ? frequencyScore
          : kind === "gap"
            ? gapScore
            : frequencyScore * 0.6 + gapScore * 0.4;
      return { number, score, frequency: counts[number], gap: drawGaps[number] };
    })
    .sort((a, b) => b.score - a.score || b.frequency - a.frequency || a.number.localeCompare(b.number))
    .slice(0, 10)
    .map((item) => item.number);
}

function backtest(draws: LotteryDraw[], window: number, kind: ModelKind) {
  const evaluationCount = Math.min(90, draws.length - Math.max(window, 30));
  if (evaluationCount <= 0) return { coverage: 0, lift: 0 };

  const startIndex = draws.length - evaluationCount;
  let coveredResults = 0;
  let totalResults = 0;

  for (let index = startIndex; index < draws.length; index += 1) {
    const training = draws.slice(Math.max(0, index - window), index);
    const picks = new Set(pickNumbers(training, kind));
    for (const number of draws[index].numbers) {
      if (picks.has(number)) coveredResults += 1;
      totalResults += 1;
    }
  }

  const coverage = totalResults ? coveredResults / totalResults : 0;
  return { coverage, lift: coverage / 0.1 };
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00+07:00`));
}

function DashboardLoading() {
  return (
    <main className="loading-shell" role="status">
      <div className="loading-mark">LL</div>
      <p>Đang nạp dữ liệu mô hình…</p>
    </main>
  );
}

export default function Home() {
  const [region, setRegion] = useState<LotteryRegion>("xsmb");
  const [data, setData] = useState<LotteryDashboardData | null>(null);
  const [error, setError] = useState("");
  const [dataSource, setDataSource] = useState("");
  const [selectedStation, setSelectedStation] = useState("xsmb");
  const [selectedWindow, setSelectedWindow] = useState(90);
  const [activeWindow, setActiveWindow] = useState(90);
  const [lastRun, setLastRun] = useState("Chưa chạy");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/lottery?region=${region}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload: unknown = await response.json();
        const normalized = normalizeLotteryDashboardData(payload, region);
        if (!normalized) throw new Error("Invalid dashboard payload");
        setDataSource(response.headers.get("x-lottery-source") ?? "api");
        return normalized;
      })
      .then((nextData) => {
        setData(nextData);
        setSelectedStation(nextData.stations[0]?.code ?? "");
        setSelectedWindow(90);
        setActiveWindow(90);
        setLastRun("Chưa chạy");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(`Không thể nạp dữ liệu ${region.toUpperCase()} từ API.`);
      });
    return () => controller.abort();
  }, [region]);

  const analysis = useMemo(() => {
    if (!data) return null;
    const station = data.stations.find((item) => item.code === selectedStation) ?? data.stations[0];
    if (!station) return null;
    const filteredDraws = data.draws.filter((draw) => draw.stationCode === station.code);
    const analysisDraws = filteredDraws.slice(-activeWindow);
    const counts = frequencies(analysisDraws);
    const drawGaps = gaps(filteredDraws);
    const sortedFrequency = Object.entries(counts).sort(
      ([numberA, countA], [numberB, countB]) => countB - countA || numberA.localeCompare(numberB),
    );
    const maxFrequency = Math.max(sortedFrequency[0]?.[1] ?? 0, 1);
    const modelDefinitions: Array<Pick<ModelResult, "kind" | "name" | "eyebrow" | "description">> = [
      {
        kind: "frequency",
        name: "Tần suất",
        eyebrow: "Model 01 · Momentum",
        description: `Ưu tiên 10 số xuất hiện nhiều nhất trong ${activeWindow} kỳ gần đây.`,
      },
      {
        kind: "gap",
        name: "Khoảng vắng",
        eyebrow: "Model 02 · Recency gap",
        description: "Xếp hạng theo số kỳ chưa xuất hiện. Chỉ là mô tả độ trễ, không phải quy luật bù.",
      },
      {
        kind: "balanced",
        name: "Cân bằng",
        eyebrow: "Model 03 · 60/40 blend",
        description: "Kết hợp 60% tần suất và 40% khoảng vắng trên cùng cửa sổ dữ liệu.",
      },
    ];
    const models: ModelResult[] = modelDefinitions.map((model) => {
      const result = backtest(filteredDraws, activeWindow, model.kind);
      return {
        ...model,
        picks: pickNumbers(analysisDraws, model.kind),
        coverage: result.coverage,
        lift: result.lift,
      };
    });

    const recentSeven = frequencies(filteredDraws.slice(-7));
    const priorThirty = frequencies(filteredDraws.slice(-37, -7));
    const momentum = Object.keys(counts)
      .map((number) => ({
        number,
        score: recentSeven[number] / 7 - priorThirty[number] / 30,
      }))
      .sort((a, b) => b.score - a.score || a.number.localeCompare(b.number))
      .slice(0, 5);

    return {
      analysisDraws,
      filteredDraws,
      station,
      evaluationCount: Math.max(0, Math.min(90, filteredDraws.length - Math.max(activeWindow, 30))),
      counts,
      drawGaps,
      maxFrequency,
      hot: sortedFrequency.slice(0, 5),
      cold: [...sortedFrequency].sort(
        ([numberA, countA], [numberB, countB]) => countA - countB || numberA.localeCompare(numberB),
      ).slice(0, 5),
      momentum,
      models,
    };
  }, [activeWindow, data, selectedStation]);

  if (error) {
    return (
      <main className="loading-shell error-shell">
        <div className="loading-mark">!</div>
        <p>{error}</p>
      </main>
    );
  }
  if (!data || !analysis) return <DashboardLoading />;

  const latestDraw = analysis.filteredDraws.at(-1) ?? data.draws.at(-1);
  if (!latestDraw) return <DashboardLoading />;

  const runModels = () => {
    setActiveWindow(selectedWindow);
    setLastRun(
      new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(
        new Date(),
      ),
    );
  };

  const chooseRegion = (nextRegion: LotteryRegion) => {
    if (nextRegion === region) return;
    setData(null);
    setError("");
    setSelectedStation("");
    setRegion(nextRegion);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#overview" aria-label="Loto Lab - Tổng quan">
          <span className="brand-mark">LL</span>
          <span>
            <strong>LÔTÔ LAB</strong>
            <small>DATA WORKBENCH</small>
          </span>
        </a>
        <nav aria-label="Điều hướng chính">
          <a href="#overview">Tổng quan</a>
          <a href="#models">Mô hình</a>
          <a href="#heatmap">Heatmap</a>
          <a href="#health">Dữ liệu</a>
        </nav>
        <div className="live-badge"><span /> {dataSource === "r2" ? "R2 live" : "Demo local"}</div>
      </header>

      <section className="hero" id="overview">
        <div className="hero-copy">
          <p className="kicker">{region.toUpperCase()} · {regionName(region).toUpperCase()} · PHÂN TÍCH MÔ TẢ</p>
          <h1>Đọc nhịp dữ liệu.<br /><em>Không đoán tương lai.</em></h1>
          <p className="hero-description">
            Chạy nhanh ba heuristic trên dữ liệu lịch sử, nhìn ngay tần suất, khoảng vắng và kết quả backtest.
            Mọi con số đều có thể truy ngược về dataset gốc.
          </p>
          <div className="hero-actions">
            <a className="primary-action" href="#models">Mở Model Lab <span>→</span></a>
            <span className="data-period">{formatDate(data.range.from)} — {formatDate(data.range.to)}</span>
          </div>
        </div>
        <div className="latest-card">
          <div className="latest-card-head">
            <span>Kết quả gần nhất</span>
            <strong>{formatDate(latestDraw.date)}</strong>
          </div>
          <div className="special-result">
            <small>Đuôi giải đặc biệt</small>
            <strong>{latestDraw.specialTail}</strong>
          </div>
          <div className="latest-station">{latestDraw.stationName}</div>
          <div className="latest-grid" aria-label={`${latestDraw.numbers.length} kết quả loto gần nhất`}>
            {latestDraw.numbers.map((number, index) => (
              <span className={index === 0 ? "is-special" : ""} key={`${number}-${index}`}>{number}</span>
            ))}
          </div>
          <p>{latestDraw.numbers.length} kết quả · giữ nguyên số 0 ở đầu</p>
        </div>
      </section>

      <section className="metrics" aria-label="Chỉ số dữ liệu">
        <article><span>01</span><small>Tổng kỳ quay</small><strong>{numberFormatter.format(data.drawCount)}</strong><p>Từ {formatDate(data.range.from)}</p></article>
        <article><span>02</span><small>Kết quả quan sát</small><strong>{numberFormatter.format(data.resultCount)}</strong><p>{latestDraw.numbers.length} kết quả mỗi kỳ / đài</p></article>
        <article><span>03</span><small>Cửa sổ mô hình</small><strong>{activeWindow} kỳ</strong><p>Đang được áp dụng</p></article>
        <article><span>04</span><small>Backtest gần nhất</small><strong>{analysis.evaluationCount} kỳ</strong><p>Walk-forward · baseline 10%</p></article>
      </section>

      <section className="model-lab" id="models">
        <div className="section-heading">
          <div><p className="kicker">MODEL LAB</p><h2>Chạy thử các góc nhìn</h2></div>
          <p>Coverage đo tỷ lệ {latestDraw.numbers.length} kết quả thực tế nằm trong top 10 của model. Lift được so với baseline 10%.</p>
        </div>

        <div className="control-bar">
          <div className="region-switch" aria-label="Chọn miền">
            {LOTTERY_REGIONS.map((option) => (
              <button
                className={region === option ? "active" : ""}
                key={option}
                type="button"
                onClick={() => chooseRegion(option)}
                aria-pressed={region === option}
              >
                {option.toUpperCase()} <span>{region === option ? "Đang xem" : "Sẵn sàng"}</span>
              </button>
            ))}
          </div>
          {data.stations.length > 1 && (
            <label>
              Đài phân tích
              <select value={selectedStation} onChange={(event) => setSelectedStation(event.target.value)}>
                {data.stations.map((station) => (
                  <option key={station.code} value={station.code}>{station.name}</option>
                ))}
              </select>
            </label>
          )}
          <label>
            Cửa sổ phân tích
            <select value={selectedWindow} onChange={(event) => setSelectedWindow(Number(event.target.value))}>
              {WINDOW_OPTIONS.map((window) => <option key={window} value={window}>{window} kỳ gần nhất</option>)}
            </select>
          </label>
          <button className="run-button" type="button" onClick={runModels}>Chạy mô hình <span>↗</span></button>
          <small>Lần chạy: {lastRun}</small>
        </div>

        <div className="model-grid">
          {analysis.models.map((model, index) => (
            <article className="model-card" key={model.kind}>
              <div className="model-index">0{index + 1}</div>
              <p className="model-eyebrow">{model.eyebrow}</p>
              <h3>{model.name}</h3>
              <p className="model-description">{model.description}</p>
              <div className="pick-list" aria-label={`Top 10 ${model.name}`}>
                {model.picks.map((number, pickIndex) => (
                  <span key={number} className={pickIndex < 3 ? "top-pick" : ""}>{number}</span>
                ))}
              </div>
              <div className="model-stats">
                <div><small>Coverage</small><strong>{percentFormatter.format(model.coverage)}</strong></div>
                <div><small>Lift / baseline</small><strong>{model.lift.toFixed(2)}×</strong></div>
              </div>
            </article>
          ))}
        </div>
        <p className="model-warning"><strong>Lưu ý:</strong> Các model trên là heuristic mô tả và backtest, không phải dự báo xác suất trúng hay khuyến nghị đặt cược.</p>
      </section>

      <section className="analysis-grid" id="heatmap">
        <article className="panel heatmap-panel">
          <div className="panel-heading">
            <div><p className="kicker">DISTRIBUTION</p><h2>Heatmap 00–99</h2></div>
            <span>{activeWindow} kỳ</span>
          </div>
          <div className="heatmap" aria-label="Tần suất loto từ 00 đến 99">
            {Array.from({ length: 100 }, (_, index) => String(index).padStart(2, "0")).map((number) => {
              const intensity = analysis.counts[number] / analysis.maxFrequency;
              return (
                <div
                  className="heat-cell"
                  key={number}
                  style={{
                    backgroundColor: `rgba(224, 58, 36, ${0.12 + intensity * 0.88})`,
                    color: intensity > 0.55 ? "#fffdf7" : "#171714",
                  }}
                  title={`${number}: ${analysis.counts[number]} lần`}
                >
                  <strong>{number}</strong><small>{analysis.counts[number]}</small>
                </div>
              );
            })}
          </div>
          <div className="heat-legend"><span>Ít</span><i /><i /><i /><i /><i /><span>Nhiều</span></div>
        </article>

        <aside className="signal-stack">
          <article className="panel signal-panel">
            <div className="panel-heading"><div><p className="kicker">SIGNALS</p><h2>Nóng / lạnh</h2></div></div>
            <div className="rank-columns">
              <div><h3>Tần suất cao</h3>{analysis.hot.map(([number, count], index) => <div className="rank-row" key={number}><span>{index + 1}</span><strong>{number}</strong><div><i style={{ width: `${(count / analysis.maxFrequency) * 100}%` }} /></div><small>{count}</small></div>)}</div>
              <div><h3>Tần suất thấp</h3>{analysis.cold.map(([number, count], index) => <div className="rank-row cold" key={number}><span>{index + 1}</span><strong>{number}</strong><div><i style={{ width: `${(count / analysis.maxFrequency) * 100}%` }} /></div><small>{count}</small></div>)}</div>
            </div>
          </article>

          <article className="panel momentum-panel">
            <div className="panel-heading"><div><p className="kicker">7D VS 30D</p><h2>Đà tăng ngắn hạn</h2></div></div>
            {analysis.momentum.map((item) => (
              <div className="momentum-row" key={item.number}>
                <strong>{item.number}</strong>
                <div><i style={{ width: `${Math.max(8, Math.min(100, 50 + item.score * 210))}%` }} /></div>
                <span>{item.score >= 0 ? "+" : ""}{item.score.toFixed(2)}/kỳ</span>
              </div>
            ))}
          </article>
        </aside>
      </section>

      <section className="data-health" id="health">
        <div className="section-heading">
          <div><p className="kicker">DATA HEALTH</p><h2>Biết dashboard đang đọc gì</h2></div>
          <p>Dashboard chỉ đọc JSON gọn qua API Worker. Gold Parquet và credential không bao giờ được gửi xuống trình duyệt.</p>
        </div>
        <div className="health-grid">
          <article><span className="health-dot good" /><div><small>Dataset version</small><strong>{data.manifest.datasetVersion}</strong></div><em>{dataSource === "r2" ? "R2" : "DEMO"}</em></article>
          <article><span className={`health-dot ${data.freshness.matchesManifestTarget ? "good" : "pending"}`} /><div><small>Ngày mới nhất</small><strong>{formatDate(latestDraw.date)}</strong></div><em>{data.freshness.matchesManifestTarget ? "SYNCED" : "LAG"}</em></article>
          <article><span className="health-dot good" /><div><small>Miền dữ liệu</small><strong>{regionName(region)}</strong></div><em>{region.toUpperCase()}</em></article>
          <article><span className="health-dot good" /><div><small>Đài đang phân tích</small><strong>{analysis.station.name}</strong></div><em>{analysis.station.code.toUpperCase()}</em></article>
        </div>
      </section>

      <footer>
        <div className="brand footer-brand"><span className="brand-mark">LL</span><span><strong>LÔTÔ LAB</strong><small>DESCRIPTIVE ANALYTICS</small></span></div>
        <p>Dữ liệu lịch sử không bảo đảm kết quả tương lai.</p>
        <a href="#overview">Lên đầu trang ↑</a>
      </footer>
    </main>
  );
}
