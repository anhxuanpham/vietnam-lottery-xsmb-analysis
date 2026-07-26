"use client";

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
  normalizeExplorerQuery,
  type ExplorerQuery,
  type ExplorerState,
} from "@/explorer-state";
import {
  fetchLotteryOperations,
  type LotteryOperationsSnapshot,
} from "@/ops-data";
import {
  PRIZE_ANALYTICS_VERSION,
  PrizeAnalyticsError,
  analyzePrizeWindow,
  type PrizeWindowAnalysis,
} from "@/prize-analytics";
import {
  LOTTERY_PRIZE_GROUPS,
  LOTTERY_REGIONS,
  isLotteryPrizeGroup,
  isLotteryPrizeMatch,
  isLotteryRegion,
  lotteryPrizeGroupSupported,
  regionName,
  type LotteryDashboardData,
  type LotteryDraw,
  type LotteryPrizeGroup,
  type LotteryPrizeMatch,
  type LotteryRegion,
} from "@/lottery-contract";
import { ExplorerResultList } from "./components/explorer-result-list";
import {
  PRIZE_NAMES,
  downloadJson,
  formatDate,
  formatTimestamp,
  orderedPrizeEntries,
  percentFormatter,
} from "./components/format";
import { LotoHeatmap } from "./components/loto-heatmap";
import { MetricsBar } from "./components/metrics-bar";
import { PrizeLab } from "./components/prize-lab";
import { SignalStack } from "./components/signal-stack";

type ModelResult = {
  kind: ModelKind;
  name: string;
  eyebrow: string;
  description: string;
  picks: string[];
  benchmark: BacktestResult;
};

const WINDOW_OPTIONS = [30, 90, 180, 365] as const;
const runTimeFormatter = new Intl.DateTimeFormat("vi-VN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

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

function initialExplorerDeepLinkError(region: LotteryRegion): string {
  const number = initialSearchParameter("number");
  const value = initialSearchParameter("value");
  const match = initialSearchParameter("match");
  const prizeGroup = initialSearchParameter("prizeGroup");
  if (number && !/^[0-9]{2}$/.test(number)) {
    return "Deep link number phải gồm đúng hai chữ số.";
  }
  if (number && (value || match || prizeGroup)) {
    return "Deep link không thể trộn number với bộ lọc giải đầy đủ.";
  }
  if (match && !isLotteryPrizeMatch(match)) {
    return "Deep link có kiểu so khớp không hợp lệ.";
  }
  if (prizeGroup &&
    (!isLotteryPrizeGroup(prizeGroup) || !lotteryPrizeGroupSupported(region, prizeGroup))) {
    return "Deep link có nhóm giải không hợp lệ cho miền đã chọn.";
  }
  if (!value && (match || prizeGroup)) {
    return "Deep link phải có value khi dùng match hoặc prizeGroup.";
  }
  return "";
}

type LatestResultView = "tails" | "full";

type LatestResultCardProps = {
  latestDraw: LotteryDraw;
  latestResultView: LatestResultView;
  onViewChange: (view: LatestResultView) => void;
};

const LatestResultCard = memo(function LatestResultCard({
  latestDraw,
  latestResultView,
  onViewChange,
}: LatestResultCardProps) {
  return (
    <div className="latest-card">
      <div className="latest-card-head">
        <span>Kết quả gần nhất</span>
        <strong>{formatDate(latestDraw.date)}</strong>
      </div>
      <div className="latest-view-toggle" role="group" aria-label="Chế độ hiển thị kết quả gần nhất">
        <button
          type="button"
          aria-controls="latest-result-panel"
          aria-pressed={latestResultView === "tails"}
          onClick={() => onViewChange("tails")}
        >
          Lô tô 2 số
        </button>
        <button
          type="button"
          aria-controls="latest-result-panel"
          aria-pressed={latestResultView === "full"}
          onClick={() => onViewChange("full")}
        >
          Kết quả đầy đủ
        </button>
      </div>
      <div className="latest-station">{latestDraw.stationName}</div>
      <div className="latest-result-body" id="latest-result-panel">
        {latestResultView === "tails" ? (
          <>
            <div className="special-result">
              <small>Đuôi giải đặc biệt</small>
              <strong>{latestDraw.specialTail}</strong>
            </div>
            <div className="latest-grid" aria-label={`${latestDraw.numbers.length} kết quả loto gần nhất`}>
              {latestDraw.numbers.map((number, index) => (
                <span className={index === 0 ? "is-special" : ""} key={`${number}-${index}`}>
                  {number}
                </span>
              ))}
            </div>
            <p>{latestDraw.numbers.length} kết quả · giữ nguyên số 0 ở đầu</p>
          </>
        ) : (
          <>
            <div
              className="latest-prize-table"
              role="table"
              aria-label={`Kết quả đầy đủ ${latestDraw.stationName} ngày ${formatDate(latestDraw.date)}`}
            >
              {orderedPrizeEntries(latestDraw.prizes).map(([group, prizes]) => (
                <div
                  className={group === "special" ? "latest-prize-row special" : "latest-prize-row"}
                  role="row"
                  key={group}
                >
                  <span role="rowheader">{PRIZE_NAMES[group] ?? group}</span>
                  <div role="cell">
                    {prizes.map((prize, index) => (
                      <strong key={`${prize}-${index}`}>{prize}</strong>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <p>Số giải đầy đủ · giữ nguyên số 0 ở đầu</p>
          </>
        )}
      </div>
    </div>
  );
});

type ResultExplorerProps = {
  stations: DashboardMetadata["stations"];
  selectedStation: string;
  explorerFrom: string;
  explorerTo: string;
  explorerValue: string;
  explorerMatch: LotteryPrizeMatch;
  explorerPrizeGroup: LotteryPrizeGroup | "";
  explorerPrizeGroups: LotteryPrizeGroup[];
  explorerDeepLinkError: string;
  explorerState: ExplorerState;
  chooseStation: (station: string) => void;
  resetExplorer: () => void;
  runExplorer: (append?: boolean) => Promise<void>;
  setExplorerFrom: (value: string) => void;
  setExplorerTo: (value: string) => void;
  setExplorerValue: (value: string) => void;
  setExplorerMatch: (value: LotteryPrizeMatch) => void;
  setExplorerPrizeGroup: (value: LotteryPrizeGroup | "") => void;
};

function ResultExplorer({
  stations,
  selectedStation,
  explorerFrom,
  explorerTo,
  explorerValue,
  explorerMatch,
  explorerPrizeGroup,
  explorerPrizeGroups,
  explorerDeepLinkError,
  explorerState,
  chooseStation,
  resetExplorer,
  runExplorer,
  setExplorerFrom,
  setExplorerTo,
  setExplorerValue,
  setExplorerMatch,
  setExplorerPrizeGroup,
}: ResultExplorerProps) {
  return (
    <section className="result-explorer" id="explorer">
      <div className="section-heading">
        <div><p className="kicker">RESULT EXPLORER</p><h2>Tra cứu từng kỳ quay</h2></div>
        <p>
          Tìm theo số đầy đủ hoặc đuôi 1–6 chữ số, thu hẹp đúng nhóm giải và giữ nguyên mọi số 0 ở đầu.
        </p>
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
            {stations.map((station) => (
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
          Cách khớp
          <select
            value={explorerMatch}
            onChange={(event) => {
              resetExplorer();
              setExplorerMatch(event.target.value as LotteryPrizeMatch);
            }}
          >
            <option value="suffix">Khớp đuôi</option>
            <option value="exact">Số đầy đủ</option>
          </select>
        </label>
        <label>
          {explorerMatch === "suffix" ? "Đuôi cần tìm" : "Số cần tìm"}
          <input
            type="text"
            inputMode="numeric"
            pattern="[0-9]{1,6}"
            maxLength={6}
            placeholder={explorerMatch === "suffix" ? "VD: 13 hoặc 113" : "VD: 005113"}
            value={explorerValue}
            onChange={(event) => {
              resetExplorer();
              setExplorerValue(event.target.value.replace(/\D/g, "").slice(0, 6));
            }}
          />
        </label>
        <label>
          Nhóm giải
          <select
            value={explorerPrizeGroup}
            onChange={(event) => {
              resetExplorer();
              setExplorerPrizeGroup(event.target.value as LotteryPrizeGroup | "");
            }}
          >
            <option value="">Tất cả giải</option>
            {explorerPrizeGroups.map((group) => (
              <option key={group} value={group}>{PRIZE_NAMES[group] ?? group}</option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={explorerState.status === "loading"}>
          {explorerState.status === "loading" && !explorerState.appending ? "Đang tra…" : "Tra kết quả"}
        </button>
      </form>
      {(explorerDeepLinkError || (explorerState.status === "error" && explorerState.error)) && (
        <p className="explorer-message error" role="alert">
          {explorerDeepLinkError || explorerState.error}
        </p>
      )}
      {explorerState.status === "idle" && !explorerDeepLinkError && (
        <p className="explorer-message">Chọn bộ lọc rồi bấm “Tra kết quả”. Đặt hai ngày giống nhau để tra đúng một kỳ.</p>
      )}
      {explorerState.status === "loading" && explorerState.items.length === 0 && (
        <p className="explorer-message" role="status">Đang tìm trong lịch sử đã publish…</p>
      )}
      {explorerState.status === "empty" && (
        <p className="explorer-message" role="status">Không tìm thấy kỳ quay phù hợp với bộ lọc đã áp dụng.</p>
      )}
      <ExplorerResultList
        items={explorerState.items}
        appliedQuery={explorerState.appliedQuery}
        busy={explorerState.status === "loading"}
      />
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
  );
}

type ModelLabProps = {
  region: LotteryRegion;
  stations: DashboardMetadata["stations"];
  selectedStation: string;
  selectedWindow: number;
  activeWindow: number;
  lastRun: string;
  resultsPerDraw: number;
  benchmarkAvailable: boolean;
  requiredDraws: number;
  availableDraws: number;
  models: ModelResult[];
  chooseRegion: (region: LotteryRegion) => void;
  chooseStation: (station: string) => void;
  onWindowChange: (value: number) => void;
  runModels: () => void;
  openExplorerEvidence: (value: string) => void;
  downloadBenchmarkReport: () => void;
};

const ModelLab = memo(function ModelLab({
  region,
  stations,
  selectedStation,
  selectedWindow,
  activeWindow,
  lastRun,
  resultsPerDraw,
  benchmarkAvailable,
  requiredDraws,
  availableDraws,
  models,
  chooseRegion,
  chooseStation,
  onWindowChange,
  runModels,
  openExplorerEvidence,
  downloadBenchmarkReport,
}: ModelLabProps) {
  return (
    <section className="model-lab" id="models">
      <div className="section-heading">
        <div><p className="kicker">MODEL LAB</p><h2>Chạy thử các góc nhìn</h2></div>
        <p>Coverage đo tỷ lệ {resultsPerDraw} kết quả thực tế nằm trong top 10 của model. Lift được so với baseline 10%.</p>
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
        {stations.length > 1 && (
          <label>
            Đài phân tích
            <select value={selectedStation} onChange={(event) => chooseStation(event.target.value)}>
              {stations.map((station) => (
                <option key={station.code} value={station.code}>{station.name}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          Cửa sổ phân tích
          <select value={selectedWindow} onChange={(event) => onWindowChange(Number(event.target.value))}>
            {WINDOW_OPTIONS.map((window) => <option key={window} value={window}>{window} kỳ gần nhất</option>)}
          </select>
        </label>
        <button className="run-button" type="button" onClick={runModels}>Chạy mô hình <span>↗</span></button>
        <small>Lần chạy: {lastRun}</small>
      </div>

      {benchmarkAvailable ? (
        <div className="model-grid">
          {models.map((model, index) => (
            <article className="model-card" key={model.kind}>
              <div className="model-index">0{index + 1}</div>
              <p className="model-eyebrow">{model.eyebrow}</p>
              <h3>{model.name}</h3>
              <p className="model-description">{model.description}</p>
              <div className="pick-list" aria-label={`Top 10 ${model.name}`}>
                {model.picks.map((number, pickIndex) => (
                  <button
                    type="button"
                    key={number}
                    className={pickIndex < 3 ? "top-pick" : ""}
                    onClick={() => openExplorerEvidence(number)}
                    aria-label={`Tra các giải có đuôi ${number} trong ${activeWindow} kỳ`}
                    title={`Mở bằng chứng gốc cho đuôi ${number}`}
                  >
                    {number}
                  </button>
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
      ) : (
        <p className="model-empty-notice" role="status">
          Chưa đủ lịch sử để backtest cửa sổ {activeWindow} kỳ: cần ít nhất{" "}
          {requiredDraws} kỳ nhưng đài này mới có {availableDraws} kỳ.
          Chọn cửa sổ nhỏ hơn rồi bấm “Chạy mô hình”; heatmap, nóng/lạnh và Prize Lab
          vẫn dùng toàn bộ lịch sử hiện có.
        </p>
      )}
      <div className="benchmark-actions">
        <p className="model-warning">
          <strong>{ANALYTICS_MODEL_VERSION}</strong> · baseline {percentFormatter.format(BASELINE_COVERAGE)}.
          {" "}12 lựa chọn model/cửa sổ (3 × 4) là phân tích khám phá; chọn lặp lại có thể làm kết quả
          trông tốt hơn thực tế. Đây là heuristic mô tả và backtest, không phải dự báo xác suất trúng
          hay khuyến nghị đặt cược.
        </p>
        <button
          className="benchmark-download"
          type="button"
          onClick={downloadBenchmarkReport}
          disabled={!benchmarkAvailable}
        >
          Tải benchmark JSON
        </button>
      </div>
    </section>
  );
});

type DataHealthProps = {
  operations: LotteryOperationsSnapshot | null;
  operationsError: string;
  region: LotteryRegion;
  dataSource: string;
  datasetVersion: string;
  matchesManifestTarget: boolean;
  stationCode: string;
  latestDrawDate: string;
};

const DataHealth = memo(function DataHealth({
  operations,
  operationsError,
  region,
  dataSource,
  datasetVersion,
  matchesManifestTarget,
  stationCode,
  latestDrawDate,
}: DataHealthProps) {
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
    matchesManifestTarget &&
    (regionalHealth?.datasetVersion === null ||
      regionalHealth?.datasetVersion === undefined ||
      regionalHealth.datasetVersion === datasetVersion);

  return (
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
              {formatDate(regionalHealth?.latestDrawDate ?? latestDrawDate)}
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
            <small>Dataset lineage · {stationCode.toUpperCase()}</small>
            <strong>{datasetVersion}</strong>
          </div>
          <em>{dataSource === "r2" ? (lineageHealthy ? "R2 SYNCED" : "R2 MISMATCH") : "DEMO"}</em>
        </article>
      </div>
    </section>
  );
});

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
  const [latestResultView, setLatestResultView] = useState<LatestResultView>("tails");
  const [reloadToken, setReloadToken] = useState(0);
  const [operations, setOperations] = useState<LotteryOperationsSnapshot | null>(null);
  const [operationsError, setOperationsError] = useState("");
  const [explorerFrom, setExplorerFrom] = useState(() => initialSearchParameter("from"));
  const [explorerTo, setExplorerTo] = useState(() => initialSearchParameter("to"));
  const [explorerValue, setExplorerValue] = useState(
    () => initialSearchParameter("value") || initialSearchParameter("number"),
  );
  const [explorerMatch, setExplorerMatch] = useState<LotteryPrizeMatch>(() => {
    const value = initialSearchParameter("match");
    if (isLotteryPrizeMatch(value)) return value;
    return initialSearchParameter("value") ? "exact" : "suffix";
  });
  const [explorerPrizeGroup, setExplorerPrizeGroup] = useState<LotteryPrizeGroup | "">(() => {
    const value = initialSearchParameter("prizeGroup");
    return isLotteryPrizeGroup(value) && lotteryPrizeGroupSupported(region, value) ? value : "";
  });
  const [explorerDeepLinkError, setExplorerDeepLinkError] = useState(
    () => initialExplorerDeepLinkError(region),
  );
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
      const normalized = normalizeExplorerQuery(applied);
      if (normalized.from !== null) parameters.set("from", normalized.from);
      if (normalized.to !== null) parameters.set("to", normalized.to);
      if (normalized.value !== null) parameters.set("value", normalized.value);
      if (normalized.match !== null) parameters.set("match", normalized.match);
      if (normalized.prizeGroup !== null) parameters.set("prizeGroup", normalized.prizeGroup);
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
    // backtest throws unless it has a full training window plus at least one evaluation draw.
    const benchmarkAvailable = draws.length > activeWindow;
    const models: ModelResult[] = !benchmarkAvailable ? [] : modelDefinitions.map((model) => {
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

    let prizeLab: PrizeWindowAnalysis | null;
    try {
      prizeLab = analyzePrizeWindow(analysisDraws);
    } catch (reason: unknown) {
      if (!(reason instanceof PrizeAnalyticsError)) throw reason;
      prizeLab = null;
    }

    return {
      analysisDraws,
      filteredDraws: draws,
      station,
      benchmarkAvailable,
      requiredDraws: activeWindow + 1,
      availableDraws: draws.length,
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
      prizeLab,
    };
  }, [activeWindow, data, draws, region, selectedStation]);

  const resetExplorer = useCallback(() => {
    explorerAbortController.current?.abort();
    explorerAbortController.current = null;
    setExplorerDeepLinkError("");
    setExplorerState(INITIAL_EXPLORER_STATE);
  }, []);

  // Stable across explorer-input keystrokes so memoized sections that trigger
  // evidence lookups do not re-render while the user types.
  const executeExplorerQuery = useCallback(async (
    query: ExplorerQuery,
    append = false,
    cursor: string | null = null,
  ) => {
    if (!data || !selectedStation) return;
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
  }, [data, fallbackData, selectedStation, servingMode]);

  const runExplorer = useCallback(async (append = false) => {
    if (!data || !selectedStation) return;
    if (append) {
      const query = explorerState.appliedQuery;
      const cursor = explorerState.cursor;
      if (query === null || cursor === null) return;
      await executeExplorerQuery(query, true, cursor);
      return;
    }
    await executeExplorerQuery({
      region,
      station: selectedStation,
      from: explorerFrom || null,
      to: explorerTo || null,
      number: null,
      value: explorerValue || null,
      match: explorerValue ? explorerMatch : null,
      prizeGroup: explorerValue && explorerPrizeGroup ? explorerPrizeGroup : null,
    });
  }, [
    data,
    executeExplorerQuery,
    explorerFrom,
    explorerMatch,
    explorerPrizeGroup,
    explorerState.appliedQuery,
    explorerState.cursor,
    explorerTo,
    explorerValue,
    region,
    selectedStation,
  ]);

  const openExplorerEvidence = useCallback((
    value: string,
    match: LotteryPrizeMatch = "suffix",
    prizeGroup: LotteryPrizeGroup | "" = "",
  ) => {
    if (!analysis) return;
    const query: ExplorerQuery = {
      region,
      station: selectedStation,
      from: analysis.prizeLab?.dateRange.from ?? analysis.analysisDraws[0].date,
      to: analysis.prizeLab?.dateRange.to ?? analysis.analysisDraws[analysis.analysisDraws.length - 1].date,
      number: null,
      value,
      match,
      prizeGroup: prizeGroup || null,
    };
    resetExplorer();
    setExplorerFrom(query.from ?? "");
    setExplorerTo(query.to ?? "");
    setExplorerValue(value);
    setExplorerMatch(match);
    setExplorerPrizeGroup(prizeGroup);
    window.requestAnimationFrame(() => {
      document.getElementById("explorer")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    void executeExplorerQuery(query);
  }, [analysis, executeExplorerQuery, region, resetExplorer, selectedStation]);

  const chooseRegion = useCallback((nextRegion: LotteryRegion) => {
    if (nextRegion === region) return;
    resetExplorer();
    requestedStation.current = "";
    setExplorerFrom("");
    setExplorerTo("");
    setExplorerValue("");
    setExplorerMatch("suffix");
    setExplorerPrizeGroup("");
    setSelectedStation("");
    setData(null);
    setFallbackData(null);
    setDraws([]);
    setError("");
    setHistoryError("");
    setRegion(nextRegion);
  }, [region, resetExplorer]);

  const chooseStation = useCallback((station: string) => {
    resetExplorer();
    requestedStation.current = station;
    setSelectedStation(station);
    setDraws([]);
    setHistoryError("");
  }, [resetExplorer]);

  const runModels = useCallback(() => {
    setActiveWindow(selectedWindow);
    setLastRun(runTimeFormatter.format(new Date()));
  }, [selectedWindow]);

  const downloadBenchmarkReport = useCallback(() => {
    if (!data || !analysis || !analysis.benchmarkAvailable) return;
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
    downloadJson(benchmarkReportFilename(report), report);
  }, [activeWindow, analysis, data, region]);

  const downloadPrizeLabReport = useCallback(() => {
    const prizeLab = analysis?.prizeLab ?? null;
    if (!data || !analysis || prizeLab === null) return;
    downloadJson(
      `prize-lab-${region}-${analysis.station.code}-${prizeLab.dateRange.to}.json`,
      {
        schemaVersion: 1,
        reportType: "prize-lab",
        analyticsVersion: PRIZE_ANALYTICS_VERSION,
        reportGeneratedAt: new Date().toISOString(),
        datasetGeneratedAt: data.generatedAt,
        datasetVersion: data.manifest.datasetVersion,
        region,
        stationCode: analysis.station.code,
        stationName: analysis.station.name,
        requestedWindow: activeWindow,
        observedDrawCount: prizeLab.drawCount,
        disclosure: "Thống kê mô tả dữ liệu lịch sử; không dự báo xác suất trúng.",
        analysis: prizeLab,
      },
    );
  }, [activeWindow, analysis, data, region]);

  useEffect(() => {
    if (!data || !selectedStation || draws.length === 0 || !explorerDeepLinkPending.current) return;
    explorerDeepLinkPending.current = false;
    if (explorerDeepLinkError) return;
    const timeoutId = window.setTimeout(() => void runExplorer(), 0);
    return () => window.clearTimeout(timeoutId);
  }, [data, draws.length, explorerDeepLinkError, runExplorer, selectedStation]);

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
  const explorerPrizeGroups = LOTTERY_PRIZE_GROUPS.filter((group) =>
    lotteryPrizeGroupSupported(region, group)
  );
  const prizeLab = analysis.prizeLab;

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
          <a href="#prize-lab">Prize Lab</a>
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
        <LatestResultCard
          latestDraw={latestDraw}
          latestResultView={latestResultView}
          onViewChange={setLatestResultView}
        />
      </section>

      <MetricsBar
        drawCount={data.drawCount}
        resultCount={data.resultCount}
        rangeFrom={data.range.from}
        resultsPerDraw={latestDraw.numbers.length}
        activeWindow={activeWindow}
        evaluationCount={analysis.evaluationCount}
      />

      <ResultExplorer
        stations={data.stations}
        selectedStation={selectedStation}
        explorerFrom={explorerFrom}
        explorerTo={explorerTo}
        explorerValue={explorerValue}
        explorerMatch={explorerMatch}
        explorerPrizeGroup={explorerPrizeGroup}
        explorerPrizeGroups={explorerPrizeGroups}
        explorerDeepLinkError={explorerDeepLinkError}
        explorerState={explorerState}
        chooseStation={chooseStation}
        resetExplorer={resetExplorer}
        runExplorer={runExplorer}
        setExplorerFrom={setExplorerFrom}
        setExplorerTo={setExplorerTo}
        setExplorerValue={setExplorerValue}
        setExplorerMatch={setExplorerMatch}
        setExplorerPrizeGroup={setExplorerPrizeGroup}
      />

      <ModelLab
        region={region}
        stations={data.stations}
        selectedStation={selectedStation}
        selectedWindow={selectedWindow}
        activeWindow={activeWindow}
        lastRun={lastRun}
        resultsPerDraw={latestDraw.numbers.length}
        benchmarkAvailable={analysis.benchmarkAvailable}
        requiredDraws={analysis.requiredDraws}
        availableDraws={analysis.availableDraws}
        models={analysis.models}
        chooseRegion={chooseRegion}
        chooseStation={chooseStation}
        onWindowChange={setSelectedWindow}
        runModels={runModels}
        openExplorerEvidence={openExplorerEvidence}
        downloadBenchmarkReport={downloadBenchmarkReport}
      />

      <section className="analysis-grid" id="heatmap">
        <LotoHeatmap
          counts={analysis.counts}
          maxFrequency={analysis.maxFrequency}
          activeWindow={activeWindow}
          openExplorerEvidence={openExplorerEvidence}
        />
        <SignalStack
          hot={analysis.hot}
          cold={analysis.cold}
          maxFrequency={analysis.maxFrequency}
          momentum={analysis.momentum}
        />
      </section>

      <section className="prize-lab" id="prize-lab">
        <div className="section-heading prize-lab-heading">
          <div>
            <p className="kicker">PRIZE LAB · FULL NUMBER</p>
            <h2>Mổ xẻ từng chữ số</h2>
          </div>
          {prizeLab !== null && (
            <div className="prize-lab-heading-copy">
              <p>
                Phân tích riêng từng nhóm giải trong {prizeLab.drawCount} kỳ của{" "}
                {prizeLab.stationName}; không trộn độ dài và không biến thống kê thành dự báo.
              </p>
              <button type="button" onClick={downloadPrizeLabReport}>Tải Prize Lab JSON</button>
            </div>
          )}
        </div>
        <PrizeLab
          prizeLab={prizeLab}
          datasetVersion={data.manifest.datasetVersion}
          openExplorerEvidence={openExplorerEvidence}
        />
      </section>

      <DataHealth
        operations={operations}
        operationsError={operationsError}
        region={region}
        dataSource={dataSource}
        datasetVersion={data.manifest.datasetVersion}
        matchesManifestTarget={data.freshness.matchesManifestTarget}
        stationCode={analysis.station.code}
        latestDrawDate={latestDraw.date}
      />

      <footer>
        <div className="brand footer-brand"><span className="brand-mark">LL</span><span><strong>LÔTÔ LAB</strong><small>DESCRIPTIVE ANALYTICS</small></span></div>
        <p>Dữ liệu lịch sử không bảo đảm kết quả tương lai.</p>
        <a href="#overview">Lên đầu trang ↑</a>
      </footer>
    </main>
  );
}
