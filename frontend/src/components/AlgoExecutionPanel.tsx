import { Activity, Gauge, Pause, Play, Route, ShieldCheck, Square } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { IconButton, Info, Metric } from "./primitives";
import type { AlgoState, AlgoTemplate, IcebergStartPayload, TwapStartPayload } from "../types";
import { optionalDecimal } from "../utils/format";

export function AlgoExecutionPanel({
  templates,
  active,
  finished,
  busy,
  onStartTwap,
  onStartIceberg,
  onPauseAlgo,
  onResumeAlgo,
  onStopAlgo
}: {
  templates: AlgoTemplate[];
  active: AlgoState[];
  finished: AlgoState[];
  busy: boolean;
  onStartTwap: (payload: TwapStartPayload) => void;
  onStartIceberg: (payload: IcebergStartPayload) => void;
  onPauseAlgo: (algoid: string) => void;
  onResumeAlgo: (algoid: string) => void;
  onStopAlgo: (algoid: string) => void;
}) {
  const childOrderCount = active.reduce((sum, algo) => sum + algo.child_orderids.length, 0);
  const activeChildOrderCount = active.reduce((sum, algo) => sum + algo.active_orderids.length, 0);
  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">AlgoTrading</div>
            <h2 className="mt-1 text-xl font-semibold">算法执行工作台</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-500">
              用 TWAP / Iceberg 管理父单拆分。页面只暴露父单意图、拆单进度、子单状态和风险路径，避免把执行细节散落到订单表里。
            </p>
          </div>
          <div className="grid min-w-[260px] grid-cols-1 gap-2 text-sm sm:grid-cols-3">
            <ExecutionStep icon={<Route size={15} />} label="父单" value="TWAP / Iceberg" />
            <ExecutionStep icon={<ShieldCheck size={15} />} label="风控" value="RiskManager" />
            <ExecutionStep icon={<Gauge size={15} />} label="网关" value="TigerOpen" />
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Metric label="算法模板" value={String(templates.length)} />
          <Metric label="运行中" value={String(active.length)} />
          <Metric label="已结束" value={String(finished.length)} />
          <Metric label="活跃子单" value={`${activeChildOrderCount} / ${childOrderCount}`} />
          <Metric label="待发数量" value={optionalDecimal(active.reduce((sum, algo) => sum + algo.remaining_volume, 0), 2)} />
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
        <TwapLauncher busy={busy} onStart={onStartTwap} />
        <IcebergLauncher busy={busy} onStart={onStartIceberg} />
      </section>

      <AlgoStateTable active={active} finished={finished} busy={busy} onPause={onPauseAlgo} onResume={onResumeAlgo} onStop={onStopAlgo} />

      <section className="panel p-4">
        <h3 className="font-semibold">算法模板</h3>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          {templates.map((template) => (
            <div key={template.name} className="rounded border border-line bg-white p-3 text-sm">
              <div className="font-semibold">{template.display_name}</div>
              <div className="mt-1 text-slate-500">{template.description}</div>
              <div className="mt-2 text-xs text-slate-500">必填字段：{template.required_fields.join(", ") || "-"}</div>
            </div>
          ))}
          {!templates.length ? <p className="text-sm text-slate-500">暂无算法模板。</p> : null}
        </div>
      </section>
    </div>
  );
}

function TwapLauncher({ busy, onStart }: { busy: boolean; onStart: (payload: TwapStartPayload) => void }) {
  const [draft, setDraft] = useState({
    symbol: "",
    direction: "long" as "long" | "short",
    total_volume: "",
    limit_price: "",
    slices: "3",
    interval_ticks: "1",
    clip_volume: "",
    reference: "OmegaAlgo:manual-twap"
  });
  const isValid = isPositiveNumber(draft.total_volume) && isPositiveNumber(draft.limit_price) && isPositiveNumber(draft.interval_ticks) && !!draft.symbol.trim();

  function start() {
    const clipVolume = draft.clip_volume.trim() ? Number(draft.clip_volume) : null;
    onStart({
      symbol: draft.symbol.trim().toUpperCase(),
      direction: draft.direction,
      total_volume: Number(draft.total_volume),
      limit_price: Number(draft.limit_price),
      slices: draft.slices.trim() ? Number(draft.slices) : null,
      interval_ticks: Number(draft.interval_ticks),
      clip_volume: clipVolume,
      product: inferProduct(draft.symbol),
      exchange: "SMART",
      order_type: "LMT",
      reference: draft.reference.trim() || "OmegaAlgo:manual-twap"
    });
  }

  return (
    <section className="panel p-4">
      <h3 className="font-semibold">TWAP 父单</h3>
      <p className="mt-1 text-sm text-slate-500">适合希望降低一次性冲击的限价父单。启动后由事件引擎按 tick 推进。</p>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <AlgoTextInput label="标的" value={draft.symbol} onChange={(value) => setDraft((next) => ({ ...next, symbol: value }))} placeholder="NVDA / O:..." />
        <DirectionSelect value={draft.direction} onChange={(value) => setDraft((next) => ({ ...next, direction: value }))} />
        <AlgoTextInput label="总数量" value={draft.total_volume} onChange={(value) => setDraft((next) => ({ ...next, total_volume: value }))} />
        <AlgoTextInput label="限价" value={draft.limit_price} onChange={(value) => setDraft((next) => ({ ...next, limit_price: value }))} />
        <AlgoTextInput label="切片数" value={draft.slices} onChange={(value) => setDraft((next) => ({ ...next, slices: value }))} />
        <AlgoTextInput label="间隔 tick" value={draft.interval_ticks} onChange={(value) => setDraft((next) => ({ ...next, interval_ticks: value }))} />
        <AlgoTextInput label="每片数量" value={draft.clip_volume} onChange={(value) => setDraft((next) => ({ ...next, clip_volume: value }))} placeholder="自动" />
        <AlgoTextInput label="审计备注" value={draft.reference} onChange={(value) => setDraft((next) => ({ ...next, reference: value }))} />
      </div>
      <ParentOrderPreview
        algoType="TWAP"
        symbol={draft.symbol}
        direction={draft.direction}
        totalVolume={draft.total_volume}
        limitPrice={draft.limit_price}
        note="按切片逐步生成子单；价格不满足限价条件时等待。"
      />
      <div className="mt-4 flex justify-end">
        <IconButton label="启动 TWAP" icon={<Activity size={15} />} onClick={start} disabled={busy || !isValid} />
      </div>
    </section>
  );
}

function IcebergLauncher({ busy, onStart }: { busy: boolean; onStart: (payload: IcebergStartPayload) => void }) {
  const [draft, setDraft] = useState({
    symbol: "",
    direction: "long" as "long" | "short",
    total_volume: "",
    display_volume: "",
    limit_price: "",
    interval_ticks: "1",
    reference: "OmegaAlgo:manual-iceberg"
  });
  const isValid = isPositiveNumber(draft.total_volume) && isPositiveNumber(draft.display_volume) && isPositiveNumber(draft.limit_price) && isPositiveNumber(draft.interval_ticks) && !!draft.symbol.trim();

  function start() {
    onStart({
      symbol: draft.symbol.trim().toUpperCase(),
      direction: draft.direction,
      total_volume: Number(draft.total_volume),
      display_volume: Number(draft.display_volume),
      limit_price: Number(draft.limit_price),
      interval_ticks: Number(draft.interval_ticks),
      product: inferProduct(draft.symbol),
      exchange: "SMART",
      order_type: "LMT",
      reference: draft.reference.trim() || "OmegaAlgo:manual-iceberg"
    });
  }

  return (
    <section className="panel p-4">
      <h3 className="font-semibold">Iceberg 父单</h3>
      <p className="mt-1 text-sm text-slate-500">适合大数量拆分：每次只暴露固定显示量，逐批生成限价子单。</p>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <AlgoTextInput label="标的" value={draft.symbol} onChange={(value) => setDraft((next) => ({ ...next, symbol: value }))} placeholder="NVDA / O:..." />
        <DirectionSelect value={draft.direction} onChange={(value) => setDraft((next) => ({ ...next, direction: value }))} />
        <AlgoTextInput label="总数量" value={draft.total_volume} onChange={(value) => setDraft((next) => ({ ...next, total_volume: value }))} />
        <AlgoTextInput label="显示数量" value={draft.display_volume} onChange={(value) => setDraft((next) => ({ ...next, display_volume: value }))} />
        <AlgoTextInput label="限价" value={draft.limit_price} onChange={(value) => setDraft((next) => ({ ...next, limit_price: value }))} />
        <AlgoTextInput label="间隔 tick" value={draft.interval_ticks} onChange={(value) => setDraft((next) => ({ ...next, interval_ticks: value }))} />
        <AlgoTextInput label="审计备注" value={draft.reference} onChange={(value) => setDraft((next) => ({ ...next, reference: value }))} className="md:col-span-2" />
      </div>
      <ParentOrderPreview
        algoType="Iceberg"
        symbol={draft.symbol}
        direction={draft.direction}
        totalVolume={draft.total_volume}
        limitPrice={draft.limit_price}
        note="每次只发送显示数量；上一批未完成时仍能在父单状态中追踪子单。"
      />
      <div className="mt-4 flex justify-end">
        <IconButton label="启动 Iceberg" icon={<Activity size={15} />} onClick={start} disabled={busy || !isValid} />
      </div>
    </section>
  );
}

function AlgoStateTable({
  active,
  finished,
  busy,
  onPause,
  onResume,
  onStop
}: {
  active: AlgoState[];
  finished: AlgoState[];
  busy: boolean;
  onPause: (algoid: string) => void;
  onResume: (algoid: string) => void;
  onStop: (algoid: string) => void;
}) {
  return (
    <section className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h3 className="font-semibold">算法父单状态</h3>
        <p className="text-sm text-slate-500">运行中算法可暂停、恢复、停止；已结束算法保留审计状态。</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1080px] text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">父单</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">方向</th>
              <th className="px-3 py-2">进度</th>
              <th className="px-3 py-2">显示量</th>
              <th className="px-3 py-2">剩余</th>
              <th className="px-3 py-2">限价</th>
              <th className="px-3 py-2">子单</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {active.map((algo) => (
              <AlgoRow key={algo.algoid} algo={algo} busy={busy} onPause={onPause} onResume={onResume} onStop={onStop} />
            ))}
            {!active.length ? <tr><td colSpan={10} className="px-3 py-8 text-center text-slate-500">暂无运行中的算法父单。</td></tr> : null}
          </tbody>
        </table>
      </div>
      {finished.length ? (
        <div className="border-t border-line bg-white px-4 py-3">
          <h4 className="font-semibold">最近结束</h4>
          <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
            {finished.slice(-6).reverse().map((algo) => (
              <div key={algo.algoid} className="rounded border border-line bg-panel p-2 text-sm">
                <div className="font-medium">{algo.algoid} / {algo.status}</div>
                <div className="mt-1 text-xs text-slate-600">{algo.vt_symbol} {directionText(algo.direction)} 已发 {optionalDecimal(algo.sent_volume, 2)} / {algo.message}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function AlgoRow({
  algo,
  busy,
  onPause,
  onResume,
  onStop
}: {
  algo: AlgoState;
  busy: boolean;
  onPause: (algoid: string) => void;
  onResume: (algoid: string) => void;
  onStop: (algoid: string) => void;
}) {
  const progress = algo.total_volume > 0 ? Math.min(100, Math.max(0, (algo.sent_volume / algo.total_volume) * 100)) : 0;
  return (
    <tr className="border-t border-line align-top">
      <td className="px-3 py-2 font-medium">{algo.algoid}</td>
      <td className="px-3 py-2">{algo.vt_symbol}</td>
      <td className="px-3 py-2">{directionText(algo.direction)}</td>
      <td className="px-3 py-2">
        <div className="font-medium">{optionalDecimal(algo.sent_volume, 2)} / {optionalDecimal(algo.total_volume, 2)}</div>
        <div className="mt-1 h-1.5 overflow-hidden rounded bg-slate-100">
          <div className="h-full rounded bg-accent" style={{ width: `${progress}%` }} />
        </div>
      </td>
      <td className="px-3 py-2">{algo.display_volume ? optionalDecimal(algo.display_volume, 2) : "-"}</td>
      <td className="px-3 py-2">{optionalDecimal(algo.remaining_volume, 2)}</td>
      <td className="px-3 py-2">{optionalDecimal(algo.limit_price, 3)}</td>
      <td className="px-3 py-2">
        <Info label="active" value={String(algo.active_orderids.length)} />
        <div className="mt-1 text-xs text-slate-500">child {algo.child_orderids.length}</div>
      </td>
      <td className="px-3 py-2">
        <span className="rounded bg-panel px-2 py-0.5 text-xs">{algo.status}</span>
        <div className="mt-1 max-w-[260px] text-xs text-slate-500">{algo.message}</div>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {algo.status === "paused" ? (
            <button className="focus-ring rounded border border-line px-2 py-1 text-xs" onClick={() => onResume(algo.algoid)} disabled={busy}>
              <Play size={13} className="inline" /> 恢复
            </button>
          ) : (
            <button className="focus-ring rounded border border-line px-2 py-1 text-xs" onClick={() => onPause(algo.algoid)} disabled={busy}>
              <Pause size={13} className="inline" /> 暂停
            </button>
          )}
          <button className="focus-ring rounded border border-rose-200 px-2 py-1 text-xs text-rose-700" onClick={() => onStop(algo.algoid)} disabled={busy}>
            <Square size={13} className="inline" /> 停止
          </button>
        </div>
      </td>
    </tr>
  );
}

function AlgoTextInput({
  label,
  value,
  onChange,
  placeholder,
  className = ""
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <label className={`min-w-0 text-xs font-medium text-slate-500 ${className}`}>
      {label}
      <input className="mt-1 w-full rounded border border-line px-2 py-1.5 text-sm text-ink" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

function DirectionSelect({ value, onChange }: { value: "long" | "short"; onChange: (value: "long" | "short") => void }) {
  return (
    <label className="min-w-0 text-xs font-medium text-slate-500">
      方向
      <select className="mt-1 w-full rounded border border-line px-2 py-1.5 text-sm text-ink" value={value} onChange={(event) => onChange(event.target.value as "long" | "short")}>
        <option value="long">买入</option>
        <option value="short">卖出</option>
      </select>
    </label>
  );
}

function inferProduct(symbol: string): "equity" | "option" {
  return symbol.trim().toUpperCase().startsWith("O:") ? "option" : "equity";
}

function ExecutionStep({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-white px-3 py-2">
      <div className="flex items-center gap-1 text-xs text-slate-500">{icon}{label}</div>
      <div className="mt-1 font-semibold">{value}</div>
    </div>
  );
}

function ParentOrderPreview({
  algoType,
  symbol,
  direction,
  totalVolume,
  limitPrice,
  note
}: {
  algoType: string;
  symbol: string;
  direction: "long" | "short";
  totalVolume: string;
  limitPrice: string;
  note: string;
}) {
  return (
    <div className="mt-4 rounded border border-line bg-panel p-3 text-sm">
      <div className="font-semibold">父单预览</div>
      <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Info label="算法" value={algoType} />
        <Info label="标的" value={symbol.trim().toUpperCase() || "-"} />
        <Info label="方向" value={directionText(direction)} />
        <Info label="总数量" value={totalVolume || "-"} />
        <Info label="限价" value={limitPrice || "-"} />
      </div>
      <div className="mt-2 text-xs text-slate-500">{note}</div>
    </div>
  );
}

function directionText(direction: string): string {
  return direction === "long" ? "买入" : direction === "short" ? "卖出" : direction;
}

function isPositiveNumber(value: string): boolean {
  const number = Number(value);
  return Number.isFinite(number) && number > 0;
}
