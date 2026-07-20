import { useState } from "react";
import { api } from "../api";
import { useLanguage } from "../i18n";
import { Info } from "./primitives";
import type { ExecutionControl } from "../types";
import { textValue } from "../utils/format";

export function ExecutionControlPanel({
  control,
  onRefresh
}: {
  control: ExecutionControl;
  onRefresh: () => Promise<void>;
}) {
  const { language } = useLanguage();
  const zh = language === "zh-CN";
  const [suffix, setSuffix] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requirements = control.unlock_requirements ?? [];
  const liveReady = control.live_gateway_status === "unlocked";
  const readiness = control.live_readiness ?? {};
  const target = control.target_account;
  const targetLabel = target?.account_masked ? `${target.account_masked} (${target.account_kind})` : (zh ? "未读取到账户号" : "Account unavailable");
  return (
    <section className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{zh ? "执行控制中心" : "Execution control"}</h2>
          <p className="mt-1 text-sm text-slate-500">{zh ? control.message : "TigerOpen live execution remains locked until every requirement and session confirmation passes."}</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${liveReady ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-100 text-slate-700"}`}>
          {liveReady ? (zh ? "TigerOpen 已连接" : "TigerOpen connected") : "TigerOpen"}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-4">
        <ControlSwitchTile label={zh ? "执行引擎" : "Execution engine"} value="TigerOpen SDK" tone="danger" actionLabel={zh ? "固定" : "Fixed"} disabled />
        <ControlSwitchTile
          label={zh ? "执行模式" : "Execution mode"}
          value={control.effective_execution_mode}
          tone="danger"
          actionLabel={zh ? "固定" : "Fixed"}
          disabled
        />
        <ControlSwitchTile label={zh ? "目标账户" : "Target account"} value={targetLabel} tone="neutral" actionLabel={zh ? "本地配置" : "Local config"} disabled />
        <ControlSwitchTile label={zh ? "行情输入" : "Market input"} value={zh ? "Massive 实时行情" : "Massive live data"} tone="neutral" actionLabel={zh ? "固定" : "Fixed"} disabled />
      </div>
      <div className="mt-3 rounded border border-line bg-white p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold">{zh ? "Gateway 连接状态" : "Gateway readiness"}</span>
          <span className={`rounded border px-2 py-1 text-xs ${liveReady ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-100 text-slate-700"}`}>
            {textValue(readiness.status)}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-500">{zh ? textValue(readiness.message) : "All configured safeguards must pass before broker execution becomes available."}</p>
        <div className="mt-2 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
          <Info label={zh ? "检查进度" : "Checks passed"} value={`${textValue(readiness.requirements_satisfied, "0")} / ${textValue(readiness.requirements_total, "0")}`} />
          <Info label={zh ? "Broker 执行可用" : "Broker execution"} value={readiness.broker_execution_available === true ? (zh ? "是" : "Available") : (zh ? "否" : "Unavailable")} />
          <Info label={zh ? "缺失项" : "Missing"} value={Array.isArray(readiness.missing) && readiness.missing.length ? readiness.missing.join(", ") : (zh ? "无" : "None")} />
          <Info label={zh ? "账户类型" : "Account type"} value={textValue(target?.account_kind)} />
          <Info label={zh ? "账户号" : "Account"} value={textValue(target?.account_masked)} />
          <Info label="SDK" value={textValue(target?.sdk)} />
        </div>
      </div>
      {requirements.length ? (
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
          {requirements.map((item) => (
            <div key={textValue(item.key)} className="rounded border border-line bg-white p-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{zh ? textValue(item.label) : requirementLabel(textValue(item.key))}</span>
                <span className={item.satisfied ? "text-accent" : "text-danger"}>{item.satisfied ? (zh ? "满足" : "Passed") : (zh ? "未满足" : "Missing")}</span>
              </div>
              <p className="mt-1 text-slate-500">{zh ? textValue(item.detail) : requirementDetail(textValue(item.key), item.satisfied === true)}</p>
            </div>
          ))}
        </div>
      ) : null}
      <div className="mt-3 rounded border border-red-200 bg-red-50 p-3">
        <div className="font-semibold text-red-900">{zh ? "会话级实盘武装" : "Session live arming"}</div>
        <p className="mt-1 text-xs text-red-800">
          {zh ? "进程重启后会自动重新锁定。任何校验失败都不会调用券商下单接口。" : "The process relocks after every restart. Any failed check prevents broker submission."}
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <input className="focus-ring rounded border border-red-200 bg-white px-3 py-2 text-sm" value={suffix} onChange={(event) => setSuffix(event.target.value)} placeholder={zh ? "账户末四位或更多" : "Last 4+ account characters"} />
          <label className="flex items-center gap-2 text-xs text-red-900">
            <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
            {zh ? "我确认实盘风险与目标账户" : "I confirm live-trading risk and the target account"}
          </label>
          {control.live_gateway_armed ? (
            <button className="focus-ring rounded border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800" disabled={busy} onClick={async () => {
              setBusy(true); setError(null);
              try { await api.disarmExecution(); await onRefresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(false); }
            }}>{zh ? "立即锁定" : "Lock now"}</button>
          ) : (
            <button className="focus-ring rounded bg-red-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" disabled={busy || !accepted || suffix.length < 4} onClick={async () => {
              setBusy(true); setError(null);
              try { await api.armExecution(suffix, "OMEGA_LIVE_RISK_ACCEPTED"); await onRefresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(false); }
            }}>{zh ? "武装当前进程" : "Arm this process"}</button>
          )}
        </div>
        {error && <p className="mt-2 text-xs text-red-800">{error}</p>}
      </div>
    </section>
  );
}

function requirementLabel(key: string): string {
  return ({
    TIGEROPEN_ACCOUNT: "TigerOpen target account",
    OMEGA_EXPECTED_TIGER_ACCOUNT: "Expected account allowlist",
    OMEGA_LIVE_TRADING: "Live execution environment switch",
    OMEGA_API_TOKEN: "API write token",
    OMEGA_MAX_ORDER_NOTIONAL: "Per-order notional limit"
  } as Record<string, string>)[key] ?? key;
}

function requirementDetail(key: string, satisfied: boolean): string {
  if (satisfied) return "Configured and verified for this process.";
  return `Configure ${key} before live execution can be armed.`;
}

function ControlSwitchTile({
  label,
  value,
  tone,
  actionLabel,
  onClick,
  disabled = false
}: {
  label: string;
  value: string;
  tone: "success" | "danger" | "neutral";
  actionLabel: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  const toneClass = tone === "success" ? "text-accent" : tone === "danger" ? "text-danger" : "text-slate-700";
  return (
    <div className="rounded border border-line bg-white p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1 break-words font-semibold ${toneClass}`}>{value}</div>
      <button className="focus-ring mt-3 w-full rounded border border-line px-2 py-1.5 text-xs disabled:opacity-50" disabled={disabled || !onClick} onClick={onClick}>
        {actionLabel}
      </button>
    </div>
  );
}
