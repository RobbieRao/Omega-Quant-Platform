import { Activity, Database, RefreshCw, ServerCog } from "lucide-react";
import type { ReactNode } from "react";
import { IconButton, Info, Metric } from "./primitives";
import type {
  CoreAccount,
  CoreContract,
  CoreOrder,
  CorePosition,
  CoreRuntimeStatus,
  CoreTrade,
  GatewayStatus
} from "../types";
import { optionalDecimal, textValue } from "../utils/format";

type CoreData = {
  status: CoreRuntimeStatus | null;
  coreOrders: CoreOrder[];
  coreTrades: CoreTrade[];
  coreAccounts: CoreAccount[];
  corePositions: CorePosition[];
  coreContracts: CoreContract[];
};

export function CoreEnginesPanel({
  data,
  busy,
  onRefresh
}: {
  data: CoreData;
  busy: boolean;
  onRefresh: () => void;
}) {
  const status = data.status;
  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">Runtime Diagnostics</div>
            <h2 className="mt-1 text-lg font-semibold">MainEngine / Gateway / OMS</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-500">
              这里只看 OMEGA 交易内核是否健康。策略、回测、风控、算法执行、价差和期权研究已经放到对应业务页面，避免同一个能力在两个入口重复操作。
            </p>
          </div>
          <IconButton label="刷新" icon={<RefreshCw size={15} />} onClick={onRefresh} disabled={busy} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Metric label="Gateways" value={String(status?.gateway_names.length ?? 0)} />
          <Metric label="Engines" value={String(status?.engine_names.length ?? 0)} />
          <Metric label="OMS Orders" value={String(data.coreOrders.length)} />
          <Metric label="OMS Positions" value={String(data.corePositions.length)} />
        </div>
        <div className="mt-3 rounded border border-line bg-white p-3 text-sm text-slate-600">
          {status?.scope ?? "等待后端 core runtime 状态。"}
        </div>
      </section>

      <GatewayHealthGrid gateways={status?.gateway_status ?? {}} />
      <Overview
        status={status}
        accounts={data.coreAccounts}
        positions={data.corePositions}
        orders={data.coreOrders}
        trades={data.coreTrades}
        contracts={data.coreContracts}
      />
    </div>
  );
}

function GatewayHealthGrid({ gateways }: { gateways: Record<string, GatewayStatus> }) {
  const rows = Object.values(gateways);
  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-center gap-2">
        <ServerCog size={16} />
        <h3 className="font-semibold">Gateway 状态</h3>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {rows.map((gateway) => (
          <div key={gateway.gateway_name} className="rounded border border-line bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-semibold">{gateway.gateway_name}</div>
              <span className={`rounded border px-2 py-1 text-xs ${gateway.connected === false ? "border-amber-200 bg-amber-50 text-amber-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                {gateway.connected === false ? "未连接" : "可用"}
              </span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
              <Info label="能力" value={(gateway.capabilities ?? []).join(", ") || "-"} />
              <Info label="订阅" value={(gateway.subscribed_symbols ?? []).join(", ") || "-"} />
              <Info label="Push" value={gateway.push_connected === true ? "已连接" : gateway.push_connected === false ? "未连接" : "-"} />
              <Info label="错误" value={gateway.push_error ?? "-"} />
            </div>
          </div>
        ))}
        {!rows.length ? <p className="text-sm text-slate-500">暂无 Gateway 状态。</p> : null}
      </div>
    </section>
  );
}

function Overview({
  status,
  accounts,
  positions,
  orders,
  trades,
  contracts
}: {
  status: CoreRuntimeStatus | null;
  accounts: CoreAccount[];
  positions: CorePosition[];
  orders: CoreOrder[];
  trades: CoreTrade[];
  contracts: CoreContract[];
}) {
  return (
    <section className="panel p-4">
      <div className="mb-3">
        <h3 className="flex items-center gap-2 font-semibold"><Activity size={16} /> OMS 缓存</h3>
        <p className="mt-1 text-sm text-slate-500">MainEngine 内存态账户、委托、成交、持仓和合约缓存。这里不提供业务操作按钮。</p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Info label="Gateway" value={(status?.gateway_names ?? []).join(", ") || "-"} />
        <Info label="Engines" value={(status?.engine_names ?? []).join(", ") || "-"} />
        <Info label="Option Portfolios" value={(status?.option_portfolios ?? []).join(", ") || "-"} />
        <Info label="Spreads" value={(status?.spreads ?? []).join(", ") || "-"} />
        <Info label="DataRecorder" value={`Tick ${status?.tick_recordings?.length ?? 0} / Bar ${status?.bar_recordings?.length ?? 0}`} />
        <Info label="历史数据集" value={String(status?.historical_datasets ?? 0)} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <RuntimeList title="OMS 账户" empty="尚未同步 TigerOpen 账户事件。">
          {accounts.map((account) => (
            <div key={account.vt_accountid} className="rounded border border-line p-2">
              <div className="font-medium">{account.vt_accountid}</div>
              <div className="mt-1 text-xs text-slate-600">
                权益 {optionalDecimal(account.balance, 2)} / 可用 {optionalDecimal(account.available, 2)} / 冻结 {optionalDecimal(account.frozen, 2)}
              </div>
            </div>
          ))}
        </RuntimeList>

        <RuntimeList title="OMS 持仓" empty="尚未同步 TigerOpen 持仓事件。">
          {positions.slice(0, 8).map((position) => (
            <div key={position.vt_positionid} className="rounded border border-line p-2">
              <div className="font-medium">{position.vt_symbol} / {position.direction}</div>
              <div className="mt-1 text-xs text-slate-600">
                数量 {optionalDecimal(position.volume, 2)} / 成本 {optionalDecimal(position.price, 3)} / PnL {optionalDecimal(position.pnl, 2)}
              </div>
            </div>
          ))}
        </RuntimeList>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <OrdersTable orders={orders} />
        <TradesTable trades={trades} />
      </div>

      <section className="mt-4 rounded border border-line bg-white p-3">
        <h4 className="flex items-center gap-2 font-semibold"><Database size={15} /> 合约缓存</h4>
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
          {contracts.slice(-9).reverse().map((contract) => (
            <div key={contract.vt_symbol} className="rounded border border-line bg-panel p-2 text-sm">
              <div className="font-medium">{contract.vt_symbol}</div>
              <div className="mt-1 text-xs text-slate-600">
                {contract.name || "-"} / {contract.product} / {contract.currency} / min tick {optionalDecimal(contract.pricetick, 4)}
              </div>
            </div>
          ))}
          {!contracts.length ? <p className="text-sm text-slate-500">尚未缓存 TigerOpen 合约信息。</p> : null}
        </div>
      </section>
    </section>
  );
}

function RuntimeList({ title, empty, children }: { title: string; empty: string; children: ReactNode }) {
  const hasChildren = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return (
    <section className="rounded border border-line bg-white p-3">
      <h4 className="font-semibold">{title}</h4>
      <div className="mt-2 space-y-2 text-sm">
        {hasChildren ? children : <p className="text-slate-500">{empty}</p>}
      </div>
    </section>
  );
}

function OrdersTable({ orders }: { orders: CoreOrder[] }) {
  return (
    <section className="rounded border border-line bg-white p-3">
      <h4 className="font-semibold">OMS 委托</h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="py-2">Order ID</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Status</th>
              <th>Broker ID</th>
            </tr>
          </thead>
          <tbody>
            {orders.slice(-8).reverse().map((order) => (
              <tr key={order.vt_orderid} className="border-t border-line">
                <td className="py-2 font-medium">{order.vt_orderid}</td>
                <td>{order.vt_symbol}</td>
                <td>{order.direction}</td>
                <td>{optionalDecimal(order.volume, 2)}</td>
                <td>{optionalDecimal(order.price, 3)}</td>
                <td>{order.status}</td>
                <td>{textValue(order.broker_order_id)}</td>
              </tr>
            ))}
            {!orders.length ? <tr><td colSpan={7} className="py-6 text-center text-slate-500">暂无 OMS 委托。</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TradesTable({ trades }: { trades: CoreTrade[] }) {
  return (
    <section className="rounded border border-line bg-white p-3">
      <h4 className="font-semibold">OMS 成交</h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="py-2">Trade ID</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Order ID</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(-8).reverse().map((trade) => (
              <tr key={trade.vt_tradeid} className="border-t border-line">
                <td className="py-2 font-medium">{trade.vt_tradeid}</td>
                <td>{trade.vt_symbol}</td>
                <td>{trade.direction}</td>
                <td>{optionalDecimal(trade.volume, 2)}</td>
                <td>{optionalDecimal(trade.price, 3)}</td>
                <td>{textValue(trade.orderid)}</td>
                <td>{new Date(trade.datetime).toLocaleString()}</td>
              </tr>
            ))}
            {!trades.length ? <tr><td colSpan={7} className="py-6 text-center text-slate-500">暂无 OMS 成交。</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

