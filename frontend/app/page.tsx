"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ANALYTICS_MODEL_VERSION,
  BASELINE_COVERAGE,
  DEFAULT_EVALUATION_LIMIT,
  DEFAULT_TOP_K,
  MODEL_KINDS,
  backtest,
  frequencies,
  gaps,
  pickNumbers,
  type BacktestResult,
  type ModelKind,
} from "@/analytics";
import {
  benchmarkReportFilename,
  buildBenchmarkReport,
} from "@/benchmark-report";
import {
  ExplorerPageError,
  compatibilityExplorerItems,
  fetchExplorerPage,
  fetchPreferredDashboard,
  fetchStationHistory,
  type DashboardLoad,
  type DashboardMetadata,
  type ServingMode,
} from "@/dashboard-data";
import {
  INITIAL_EXPLORER_STATE,
  beginExplorerRequest,
  completeExplorerRequest,
  explorerQueryError,
  failExplorerRequest,
  type ExplorerQuery,
} from "@/explorer-state";
import {
  fetchLotteryOperations,
  type LotteryOperationsSnapshot,
} from "@/ops-data";
import {
  LOTTERY_REGIONS,
  isLotteryRegion,
  regionName,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryRegion,
} from "@/lottery-contract";

type ModelResult = {
  kind: ModelKind;
  name: string;
  eyebrow: string;
  description: string;
  picks: string[];
  benchmark: BacktestResult;
};

const WINDOW_OPTIONS = [30, 90, 180, 365] as const;
const numberFormatter = new Intl.NumberFormat("vi-VN");
const percentFormatter = new Intl.NumberFormat("vi-VN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const PRIZE_NAMES: Record<string, string> = {
  special: "Đặc biệt",
  first: "Giải nhất",
  second: "Giải nhì",
  third: "Giải ba",
  fourth: "Giải tư",
  fifth: "Giải năm",
  sixth: "Giải sáu",
  seventh: "Giải bảy",
  eighth: "Giải tám",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00+07:00`));
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return "Chưa có bằng chứng chạy";
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function DashboardLoading() {
  return (
    <main className="loading-shell" role="status">
      <div className="loading-mark">LL</div>
      <p>Đang nạp dữ liệu mô hình…</p>
    </main>
  );
}

function initialSearchParameter(name: string): string {
  return typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get(name) ?? "";
}

export default function Home() {
  const [region, setRegion] = useState<LotteryRegion>(() => {
    const value = initialSearchParameter("region");
    return isLotteryRegion(value) ? value : "xsmb";
  });
  const [data, setData] = useState<DashboardMetadata | null>(null);
  const [fallbackData, setFallbackData] = useState<LotteryDashboardData | null>(null);
  const [servingMode, setServingMode] = useState<ServingMode>("v2");
  const [draws, setDraws] = useState<LotteryDraw[]>([]);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [dataSource, setDataSource] = useState("");
  const [selectedStation, setSelectedStation] = useState("");
  const requestedStation = useRef(initialSearchParameter("station"));
  const [selectedWindow, setSelectedWindow] = useState(90);
  const [activeWindow, setActiveWindow] = useState(90);
  const [lastRun, setLastRun] = useState("Chưa chạy");
  const [reloadToken, setReloadToken] = useState(0);
  const [operations, setOperations] = useState<LotteryOperationsSnapshot | null>(null);
  const [operationsError, setOperationsError] = useState("");
  const [explorerFrom, setExplorerFrom] = useState(() => initialSearchParameter("from"));
  const [explorerTo, setExplorerTo] = useState(() => initialSearchParameter("to"));
  const [explorerNumber, setExplorerNumber] = useState(() => initialSearchParameter("number"));
  const [explorerState, setExplorerState] = useState(INITIAL_EXPLORER_STATE);
  const explorerAbortController = useRef<AbortController | null>(null);
  const explorerDeepLinkPending = useRef(
    typeof window !== "undefined" && new URLSearchParams(window.location.search).has("station"),
  );

  useEffect(() => () => explorerAbortController.current?.abort(), []);

  useEffect(() => {
    const controller = new AbortController();
    fetchLotteryOperations({ signal: controller.signal })
      .then((snapshot) => {
        setOperations(snapshot);
        setOperationsError("");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setOperations(null);
        setOperationsError("Không đọc được health API");
      });
    return () => controller.abort();
  }, [reloadToken]);

  useEffect(() => {
    const controller = new AbortController();

    const load = async () => {
      const loaded = await fetchPreferredDashboard(region, { signal: controller.signal });
      setError("");
      setHistoryError("");
      setData(loaded.data);
      setFallbackData(loaded.fallbackData);
      setServingMode(loaded.servingMode);
      setDataSource(loaded.dataSource);
      const station = loaded.data.stations.some((item) => item.code === requestedStation.current)
        ? requestedStation.current
        : loaded.data.stations[0]?.code ?? "";
      setSelectedStation(station);
    };

    load().catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(`Không thể nạp dữ liệu ${region.toUpperCase()} từ API.`);
    });
    return () => controller.abort();
  }, [region, reloadToken]);

  useEffect(() => {
    if (!data || !selectedStation) return;
    const controller = new AbortController();

    const loadHistory = async () => {
      const dashboard: DashboardLoad = {
        data,
        fallbackData,
        servingMode,
        dataSource,
      };
      const loaded = await fetchStationHistory(dashboard, region, selectedStation, {
        signal: controller.signal,
      });
      if (loaded.fallback) {
        setData(loaded.fallback.data);
        setFallbackData(loaded.fallback.fallbackData);
        setServingMode(loaded.fallback.servingMode);
        setDataSource(loaded.fallback.dataSource);
      }
      if (loaded.station !== selectedStation) setSelectedStation(loaded.station);
      setDraws(loaded.draws);
      setHistoryError("");
    };
    loadHistory().catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setHistoryError(`Không thể nạp lịch sử của đài ${selectedStation}.`);
    });
    return () => controller.abort();
  }, [data, dataSource, fallbackData, region, selectedStation, servingMode]);

  useEffect(() => {
    if (!selectedStation || explorerDeepLinkPending.current) return;
    const parameters = new URLSearchParams();
    parameters.set("region", region);
    parameters.set("station", selectedStation);
    const applied = explorerState.appliedQuery;
    if (applied !== null && applied.region === region && applied.station === selectedStation &&
      explorerQueryError(applied) === null) {
      if (applied.from !== null) parameters.set("from", applied.from);
      if (applied.to !== null) parameters.set("to", applied.to);
      if (applied.number !== null) parameters.set("number", applied.number);
    }
    window.history.replaceState(null, "", `${window.location.pathname}?${parameters}${window.location.hash}`);
  }, [explorerState.appliedQuery, region, selectedStation]);

  const analysis = useMemo(() => {
    if (!data || draws.length === 0) return null;
    const station = data.stations.find((item) => item.code === selectedStation) ?? data.stations[0];
    if (!station) return null;
    const analysisDraws = draws.slice(-activeWindow);
    const counts = frequencies(analysisDraws);
    const drawGaps = gaps(draws);
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
      const benchmark = backtest(draws, {
        datasetVersion: data.manifest.datasetVersion,
        region,
        stationCode: station.code,
        kind: model.kind,
        window: activeWindow,
        topK: DEFAULT_TOP_K,
        evaluationLimit: DEFAULT_EVALUATION_LIMIT,
      });
      return {
        ...model,
        picks: pickNumbers(analysisDraws, model.kind, DEFAULT_TOP_K),
        benchmark,
      };
    });

    const recentSeven = frequencies(draws.slice(-7));
    const priorThirty = frequencies(draws.slice(-37, -7));
    const momentum = Object.keys(counts)
      .map((number) => ({
        number,
        score: recentSeven[number] / 7 - priorThirty[number] / 30,
      }))
      .sort((left, right) => right.score - left.score || left.number.localeCompare(right.number))
      .slice(0, 5);

    return {
      analysisDraws,
      filteredDraws: draws,
      station,
      evaluationCount: models[0]?.benchmark.evaluationCount ?? 0,
      counts,
      drawGaps,
      maxFrequency,
      hot: sortedFrequency.slice(0, 5),
      cold: [...sortedFrequency]
        .sort(([numberA, countA], [numberB, countB]) => countA - countB || numberA.localeCompare(numberB))
        .slice(0, 5),
      momentum,
      models,
    };
  }, [activeWindow, data, draws, region, selectedStation]);

  const resetExplorer = useCallback(() => {
    explorerAbortController.current?.abort();
    explorerAbortController.current = null;
    setExplorerState(INITIAL_EXPLORER_STATE);
  }, []);

  const runExplorer = useCallback(async (append = false) => {
    if (!data || !selectedStation) return;
    const query: ExplorerQuery | null = append
      ? explorerState.appliedQuery
      : {
          region,
          station: selectedStation,
          from: explorerFrom || null,
          to: explorerTo || null,
          number: explorerNumber || null,
        };
    const cursor = append ? explorerState.cursor : null;
    if (query === null || (append && cursor === null)) return;
    const validationError = explorerQueryError(query);
    if (validationError !== null) {
      const started = beginExplorerRequest(INITIAL_EXPLORER_STATE, query, false);
      setExplorerState(failExplorerRequest(started, query, validationError));
      return;
    }

    explorerAbortController.current?.abort();
    const controller = new AbortController();
    explorerAbortController.current = controller;
    setExplorerState((current) => beginExplorerRequest(current, query, append));
    try {
      if (servingMode === "v1") {
        const matches = fallbackData === null
          ? []
          : compatibilityExplorerItems(fallbackData, query, 25);
        if (explorerAbortController.current !== controller) return;
        setExplorerState((current) =>
          completeExplorerRequest(current, query, matches, null, false)
        );
        return;
      }
      const page = await fetchExplorerPage(query, data.manifest.datasetVersion, {
        cursor,
        limit: 25,
        signal: controller.signal,
      });
      if (explorerAbortController.current !== controller) return;
      setExplorerState((current) =>
        completeExplorerRequest(current, query, page.items, page.page.nextCursor, append)
      );
    } catch (reason: unknown) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (explorerAbortController.current !== controller) return;
      const message = reason instanceof ExplorerPageError &&
        (reason.code === "invalid_cursor" || reason.code === "stale_release")
        ? "Dữ liệu vừa được cập nhật. Bấm “Tra kết quả” để tải lại từ đầu."
        : "Không thể tra cứu. Kiểm tra khoảng ngày và thử lại.";
      setExplorerState((current) => failExplorerRequest(current, query, message));
    } finally {
      if (explorerAbortController.current === controller) {
        explorerAbortController.current = null;
      }
    }
  }, [
    data,
    explorerFrom,
    explorerNumber,
    explorerState.appliedQuery,
    explorerState.cursor,
    explorerTo,
    fallbackData,
    region,
    selectedStation,
    servingMode,
  ]);

  useEffect(() => {
    if (!data || !selectedStation || !explorerDeepLinkPending.current) return;
    explorerDeepLinkPending.current = false;
    void runExplorer();
  }, [data, runExplorer, selectedStation]);

  if (error) {
    return (
      <main className="loading-shell error-shell">
        <div className="loading-mark">!</div>
        <p>{error}</p>
        <button type="button" onClick={() => setReloadToken((value) => value + 1)}>Thử lại</button>
      </main>
    );
  }
  if (!data || (!analysis && !historyError)) return <DashboardLoading />;
  if (historyError || !analysis) {
    return (
      <main className="loading-shell error-shell">
        <div className="loading-mark">!</div>
        <p>{historyError || "Không đủ lịch sử để phân tích."}</p>
        <button type="button" onClick={() => setReloadToken((value) => value + 1)}>Thử lại</button>
      </main>
    );
  }

  const latestDraw = analysis.filteredDraws.at(-1);
  if (!latestDraw) return <DashboardLoading />;

  const regionalHealth = operations?.health.regions[region] ?? null;
  const unhealthyRegions = operations
    ? LOTTERY_REGIONS.filter((candidate) => !operations.health.regions[candidate].healthy)
    : [];
  const watchdogState = operations?.watchdog?.state ?? null;
  const watchdogLabel = watchdogState?.status === "healthy"
    ? "HEALTHY"
    : watchdogState?.status === "warning"
      ? "WARNING"
      : watchdogState?.status === "critical"
        ? "CRITICAL"
        : watchdogState?.status === "pending"
          ? "PENDING"
          : "NO EVIDENCE";
  const watchdogDot = watchdogState?.status === "healthy"
    ? "good"
    : watchdogState?.status === "warning" || watchdogState?.status === "critical"
      ? "bad"
      : "pending";
  const lineageHealthy = dataSource === "r2" &&
    data.freshness.matchesManifestTarget &&
    (regionalHealth?.datasetVersion === null ||
      regionalHealth?.datasetVersion === undefined ||
      regionalHealth.datasetVersion === data.manifest.datasetVersion);

  const runModels = () => {
    setActiveWindow(selectedWindow);
    setLastRun(
      new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(
        new Date(),
      ),
    );
  };

  const downloadBenchmarkReport = () => {
    const report = buildBenchmarkReport({
      datasetVersion: data.manifest.datasetVersion,
      region,
      stationCode: analysis.station.code,
      stationName: analysis.station.name,
      selectedWindow: activeWindow,
      modelKinds: MODEL_KINDS,
      windows: WINDOW_OPTIONS,
      benchmarks: analysis.models.map((model) => model.benchmark),
    });
    const url = URL.createObjectURL(
      new Blob([`${JSON.stringify(report, null, 2)}\n`], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = benchmarkReportFilename(report);
    anchor.hidden = true;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const chooseRegion = (nextRegion: LotteryRegion) => {
    if (nextRegion === region) return;
    resetExplorer();
    requestedStation.current = "";
    setExplorerFrom("");
    setExplorerTo("");
    setExplorerNumber("");
    setSelectedStation("");
    setData(null);
    setFallbackData(null);
    setDraws([]);
    setError("");
    setHistoryError("");
    setRegion(nextRegion);
  };

  const chooseStation = (station: string) => {
    resetExplorer();
    requestedStation.current = station;
    setSelectedStation(station);
    setDraws([]);
    setHistoryError("");
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
          <a href="#explorer">Tra cứu</a>
          <a href="#models">Mô hình</a>
          <a href="#heatmap">Heatmap</a>
          <a href="#health">Dữ liệu</a>
        </nav>
        <div className="live-badge"><span /> {dataSource === "r2" ? "R2 live" : "Demo local"}</div>
      </header>

      {(servingMode === "v1" || dataSource !== "r2") && (
        <aside className="degraded-banner" role="status">
          <strong>Chế độ tương thích</strong>
          <span>
            {dataSource === "bundled-demo"
              ? "R2 chưa sẵn sàng; dashboard đang dùng snapshot demo và phạm vi tra cứu bị giới hạn."
              : "Serving API v2 chưa sẵn sàng; dashboard đang đọc payload v1 gần nhất."}
          </span>
          <button type="button" onClick={() => setReloadToken((value) => value + 1)}>Thử lại v2</button>
        </aside>
      )}

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

      <section className="result-explorer" id="explorer">
        <div className="section-heading">
          <div><p className="kicker">RESULT EXPLORER</p><h2>Tra cứu từng kỳ quay</h2></div>
          <p>Lọc đúng đài, ngày và đuôi 00–99. Kết quả giữ nguyên từng nhóm giải và mọi số 0 ở đầu.</p>
        </div>
        <form
          className="explorer-controls"
          onSubmit={(event) => {
            event.preventDefault();
            void runExplorer();
          }}
        >
          <label>
            Đài
            <select value={selectedStation} onChange={(event) => chooseStation(event.target.value)}>
              {data.stations.map((station) => (
                <option key={station.code} value={station.code}>{station.name}</option>
              ))}
            </select>
          </label>
          <label>
            Từ ngày
            <input
              type="date"
              value={explorerFrom}
              onChange={(event) => {
                resetExplorer();
                setExplorerFrom(event.target.value);
              }}
            />
          </label>
          <label>
            Đến ngày
            <input
              type="date"
              value={explorerTo}
              onChange={(event) => {
                resetExplorer();
                setExplorerTo(event.target.value);
              }}
            />
          </label>
          <label>
            Đuôi loto
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]{2}"
              maxLength={2}
              placeholder="00–99"
              value={explorerNumber}
              onChange={(event) => {
                resetExplorer();
                setExplorerNumber(event.target.value.replace(/\D/g, "").slice(0, 2));
              }}
            />
          </label>
          <button type="submit" disabled={explorerState.status === "loading"}>
            {explorerState.status === "loading" && !explorerState.appending ? "Đang tra…" : "Tra kết quả"}
          </button>
        </form>
        {explorerState.status === "error" && explorerState.error && (
          <p className="explorer-message error" role="alert">{explorerState.error}</p>
        )}
        {explorerState.status === "idle" && (
          <p className="explorer-message">Chọn bộ lọc rồi bấm “Tra kết quả”. Đặt hai ngày giống nhau để tra đúng một kỳ.</p>
        )}
        {explorerState.status === "loading" && explorerState.items.length === 0 && (
          <p className="explorer-message" role="status">Đang tìm trong lịch sử đã publish…</p>
        )}
        {explorerState.status === "empty" && (
          <p className="explorer-message" role="status">Không tìm thấy kỳ quay phù hợp với bộ lọc đã áp dụng.</p>
        )}
        <div
          className="result-list"
          aria-busy={explorerState.status === "loading"}
          aria-live="polite"
        >
          {explorerState.items.map((draw) => (
            <article className="result-card" key={`${draw.stationCode}-${draw.date}`}>
              <header>
                <div><small>{draw.stationName}</small><h3>{formatDate(draw.date)}</h3></div>
                <div className="result-special"><small>Đặc biệt</small><strong>{draw.specialPrize}</strong></div>
              </header>
              <div className="prize-table">
                {Object.entries(draw.prizes).map(([group, prizes]) => (
                  <div className={group === "special" ? "prize-row special" : "prize-row"} key={group}>
                    <span>{PRIZE_NAMES[group] ?? group}</span>
                    <div>
                      {prizes.map((prize, index) => (
                        <strong
                          className={explorerState.appliedQuery?.number &&
                            prize.endsWith(explorerState.appliedQuery.number) ? "matched" : ""}
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
        {explorerState.cursor && explorerState.appliedQuery && (
          <button
            className="next-page"
            type="button"
            disabled={explorerState.status === "loading"}
            onClick={() => void runExplorer(true)}
          >
            {explorerState.appending ? "Đang tải thêm…" : "Tải thêm kết quả →"}
          </button>
        )}
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
              <select value={selectedStation} onChange={(event) => chooseStation(event.target.value)}>
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
                <div>
                  <small>Coverage</small>
                  <strong>{percentFormatter.format(model.benchmark.coverage)}</strong>
                </div>
                <div>
                  <small>95% CI</small>
                  <strong>
                    {percentFormatter.format(model.benchmark.coverageConfidenceInterval.lower)}
                    {" — "}
                    {percentFormatter.format(model.benchmark.coverageConfidenceInterval.upper)}
                  </strong>
                </div>
                <div>
                  <small>Hit rate</small>
                  <strong>{percentFormatter.format(model.benchmark.hitRate)}</strong>
                </div>
                <div>
                  <small>Lift / baseline</small>
                  <strong>{model.benchmark.lift.toFixed(2)}×</strong>
                </div>
              </div>
              <p className="model-sample">
                {model.benchmark.evaluationCount} kỳ · {formatDate(model.benchmark.evaluationRange.from)}
                {" — "}
                {formatDate(model.benchmark.evaluationRange.to)} · không nhìn trước
              </p>
              <code className="model-fingerprint">{model.benchmark.fingerprint}</code>
            </article>
          ))}
        </div>
        <div className="benchmark-actions">
          <p className="model-warning">
            <strong>{ANALYTICS_MODEL_VERSION}</strong> · baseline {percentFormatter.format(BASELINE_COVERAGE)}.
            {" "}12 lựa chọn model/cửa sổ (3 × 4) là phân tích khám phá; chọn lặp lại có thể làm kết quả
            trông tốt hơn thực tế. Đây là heuristic mô tả và backtest, không phải dự báo xác suất trúng
            hay khuyến nghị đặt cược.
          </p>
          <button className="benchmark-download" type="button" onClick={downloadBenchmarkReport}>
            Tải benchmark JSON
          </button>
        </div>
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
          <article>
            <span className={`health-dot ${operations?.health.healthy ? "good" : operations ? "bad" : "pending"}`} />
            <div>
              <small>Serving health</small>
              <strong>
                {operations?.health.healthy
                  ? "3/3 miền đạt chuẩn"
                  : operations
                    ? `Lỗi: ${unhealthyRegions.map((item) => item.toUpperCase()).join(", ")}`
                    : operationsError || "Đang chờ health API"}
              </strong>
            </div>
            <em>{operations ? `TARGET ${formatDate(operations.health.expectedTargetDate)}` : "UNAVAILABLE"}</em>
          </article>
          <article title={regionalHealth?.issues.join("; ") || undefined}>
            <span className={`health-dot ${regionalHealth?.healthy ? "good" : regionalHealth ? "bad" : "pending"}`} />
            <div>
              <small>{region.toUpperCase()} mới nhất</small>
              <strong>
                {formatDate(regionalHealth?.latestDrawDate ?? latestDraw.date)}
              </strong>
            </div>
            <em>{regionalHealth?.healthy ? "REGION OK" : regionalHealth ? "ISSUES" : "NO STATUS"}</em>
          </article>
          <article>
            <span className={`health-dot ${watchdogDot}`} />
            <div>
              <small>Watchdog gần nhất</small>
              <strong>{formatTimestamp(watchdogState?.lastObservedAt)}</strong>
            </div>
            <em>{watchdogLabel}</em>
          </article>
          <article>
            <span className={`health-dot ${lineageHealthy ? "good" : dataSource === "r2" ? "bad" : "pending"}`} />
            <div>
              <small>Dataset lineage · {analysis.station.code.toUpperCase()}</small>
              <strong>{data.manifest.datasetVersion}</strong>
            </div>
            <em>{dataSource === "r2" ? (lineageHealthy ? "R2 SYNCED" : "R2 MISMATCH") : "DEMO"}</em>
          </article>
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
