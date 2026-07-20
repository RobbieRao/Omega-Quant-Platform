import { useCallback, useEffect, useState } from "react";
import { Modal } from "../components/Modal";
import { RealOrderReadonlyDetails, RealOrdersTable, RealPositionGroupsPanel } from "../components/RealReadonlyPanels";
import { api } from "../api";
import type { CoreOrder, CorePosition, CoreTrade, RealOrderReadonly, RealPosition, RealPositionGroup } from "../types";
import { optionalDecimal, optionalMoney, textValue } from "../utils/format";

export default function TradingPage() {
  const [positionGroups, setPositionGroups] = useState<RealPositionGroup[]>([]);
  const [fallbackPositions, setFallbackPositions] = useState<RealPosition[]>([]);
  const [realOrders, setRealOrders] = useState<RealOrderReadonly[]>([]);
  const [coreOrders, setCoreOrders] = useState<CoreOrder[]>([]);
  const [corePositions, setCorePositions] = useState<CorePosition[]>([]);
  const [coreTrades, setCoreTrades] = useState<CoreTrade[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<RealOrderReadonly | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orderForm, setOrderForm] = useState({
    symbol: "AAPL",
    exchange: "SMART",
    direction: "buy" as "buy" | "sell",
    order_type: "limit" as "limit" | "market" | "stop",
    volume: "1",
    price: "",
    offset: "NONE",
    outside_rth: true,
    time_in_force: "DAY" as "DAY" | "GTC",
  });
  const [hideCancelled, setHideCancelled] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitInfo, setSubmitInfo] = useState<string | null>(null);
  const [sizingPct, setSizingPct] = useState("");  // 输入百分比 → 自动算 volume

  // 估算 volume = floor(buying_power * pct / price) 的 helper
  const estimateVolumeFromPct = useCallback(async (pctStr: string) => {
    const pct = Number(pctStr);
    if (!Number.isFinite(pct) || pct <= 0 || pct > 1) { setError("仓位比必须在 0-1 之间"); return; }
    if (!orderForm.symbol) { setError("先填标的"); return; }
    setError(null); setBusy(true);
    try {
      const [snap, accountSummary] = await Promise.all([
        api.marketSnapshot(orderForm.symbol).catch(() => null),
        api.realAccountSummary().catch(() => null),
      ]);
      const refPrice = (snap?.ask ?? snap?.last ?? snap?.price ?? 0) || Number(orderForm.price);
      const buyingPower = Number((accountSummary as { buying_power?: number } | null)?.buying_power ?? 0);
      if (refPrice <= 0) { setError("无法获取参考价（先填限价或选有行情的标的）"); return; }
      if (buyingPower <= 0) { setError("Buying power 为 0"); return; }
      const target = buyingPower * pct;
      const vol = Math.floor(target / refPrice);
      if (vol <= 0) { setError(`资金不足：${pct * 100}% × $${buyingPower.toFixed(0)} = $${target.toFixed(0)}, 不够买 1 股 @ $${refPrice}`); return; }
      setOrderForm((f) => ({ ...f, volume: String(vol) }));
      setSubmitInfo(`✓ 已按 ${(pct * 100).toFixed(0)}% × buying_power $${buyingPower.toFixed(0)} ÷ ref $${refPrice.toFixed(2)} = ${vol} 股 填入`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [orderForm.symbol, orderForm.price]);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const results = await Promise.allSettled([
        api.realPositionGroups(),
        api.realPositions(),
        api.realOrdersReadonly(),
        api.coreOrders(),
        api.corePositions(),
        api.coreTrades(),
      ]);
      const [groups, pos, orders, co, cp, ct] = results;
      if (groups.status === "fulfilled") setPositionGroups(groups.value);
      if (pos.status === "fulfilled") setFallbackPositions(pos.value);
      if (orders.status === "fulfilled") setRealOrders(orders.value);
      if (co.status === "fulfilled") setCoreOrders(co.value);
      if (cp.status === "fulfilled") setCorePositions(cp.value);
      if (ct.status === "fulfilled") setCoreTrades(ct.value);
      const fail = results.find((r) => r.status === "rejected");
      setError(fail ? String((fail as PromiseRejectedResult).reason) : null);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 10_000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleCancelCoreOrder = useCallback(async (orderid: string) => {
    if (!confirm(`确认取消订单 ${orderid}？`)) return;
    setBusy(true); setError(null);
    try {
      await api.cancelCoreOrder(orderid);
      setSubmitInfo(`✓ 已发送取消请求 ${orderid}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleCancelRealOrder = useCallback(async (order: RealOrderReadonly) => {
    const id = order.broker_order_id;
    if (!id) { setError("缺少券商订单号，无法取消"); return; }
    await handleCancelCoreOrder(id);
  }, [handleCancelCoreOrder]);

  const [modifyTarget, setModifyTarget] = useState<CoreOrder | null>(null);
  const [modifyDraft, setModifyDraft] = useState({ price: "", volume: "" });

  const openModifyModal = useCallback((order: CoreOrder) => {
    const remaining = order.volume - order.traded;
    setModifyTarget(order);
    setModifyDraft({ price: String(order.price ?? ""), volume: String(remaining) });
  }, []);

  const openModifyModalFromReal = useCallback((order: RealOrderReadonly) => {
    const ctx = (order.raw_context ?? {}) as Record<string, unknown>;
    const num = (k: string): number => {
      const v = ctx[k];
      if (typeof v === "number") return v;
      if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
      return 0;
    };
    const str = (k: string, fallback = ""): string => {
      const v = ctx[k];
      return typeof v === "string" ? v : fallback;
    };
    const synthetic: CoreOrder = {
      vt_orderid: str("vt_orderid", `TIGEROPEN.${order.broker_order_id}`),
      orderid: order.broker_order_id,
      symbol: order.symbol ?? str("symbol"),
      vt_symbol: str("vt_symbol", `${order.symbol ?? ""}.${str("exchange", "SMART")}`),
      exchange: str("exchange", "SMART"),
      direction: str("direction", (order.side ?? "buy").toLowerCase().includes("sell") ? "short" : "long"),
      volume: order.quantity ?? num("volume"),
      price: num("price"),
      traded: num("traded"),
      status: order.status ?? str("status"),
      datetime: order.submitted_at ?? str("datetime"),
      gateway_name: str("gateway_name", "TIGEROPEN"),
      broker_order_id: order.broker_order_id,
      reference: str("reference"),
      risk_result: null,
    };
    openModifyModal(synthetic);
  }, [openModifyModal]);

  const submitModify = useCallback(async () => {
    if (!modifyTarget) return;
    const newPrice = Number(modifyDraft.price);
    const newVolume = Number(modifyDraft.volume);
    if (!Number.isFinite(newPrice) || newPrice <= 0) { setError("无效价格"); return; }
    if (!Number.isFinite(newVolume) || newVolume <= 0) { setError("无效数量"); return; }
    setBusy(true); setError(null);
    try {
      await api.cancelCoreOrder(modifyTarget.orderid);
      const dir = modifyTarget.direction.toLowerCase().includes("long") ? "buy" : "sell";
      await api.submitManualOrder({
        symbol: modifyTarget.symbol,
        exchange: modifyTarget.exchange,
        direction: dir as "buy" | "sell",
        order_type: "limit",
        volume: newVolume,
        price: newPrice,
        reference: `modify_of_${modifyTarget.orderid}`,
      });
      setSubmitInfo(`✓ 已取消 ${modifyTarget.orderid} 并以 ${newPrice} 重新下单 ${newVolume}`);
      setModifyTarget(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [modifyTarget, modifyDraft, refresh]);

  const handleSubmit = useCallback(async () => {
    setError(null); setSubmitInfo(null);
    const volume = Number(orderForm.volume);
    const price = orderForm.order_type === "market" ? 0 : Number(orderForm.price);
    if (!orderForm.symbol || !Number.isFinite(volume) || volume <= 0) {
      setError("数量必须 > 0");
      return;
    }
    if (orderForm.order_type !== "market" && (!Number.isFinite(price) || price <= 0)) {
      setError("限价/止损单需要填写有效价格");
      return;
    }
    const side = orderForm.direction === "buy" ? "买入" : "卖出";
    const action = orderForm.order_type === "market" ? "市价" : `${price}`;
    const tifLabel = orderForm.time_in_force === "GTC" ? "GTC 长期" : "当日 DAY";
    const sessionLabel = orderForm.outside_rth ? "✓ 允许盘前/盘后/夜盘 (outside RTH)" : "仅常规交易时段";
    if (!confirm(`【真实下单确认】\n${side} ${orderForm.symbol} ${volume} 股 @ ${action}\n时段: ${sessionLabel}\nTIF: ${tifLabel}\n\n确认提交到 TigerOpen？`)) return;
    setSubmitting(true);
    try {
      const r = await api.submitManualOrder({
        symbol: orderForm.symbol,
        exchange: orderForm.exchange,
        direction: orderForm.direction,
        order_type: orderForm.order_type,
        volume,
        price,
        offset: orderForm.offset,
        reference: "manual_ui",
        outside_rth: orderForm.outside_rth,
        time_in_force: orderForm.time_in_force,
      });
      const rthTag = orderForm.outside_rth ? " · 夜盘" : "";
      setSubmitInfo(`✓ 已提交 vt_orderid=${r.vt_orderid} · ${orderForm.time_in_force}${rthTag}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }, [orderForm, refresh]);

  const activeStatuses = new Set(["SUBMITTING", "NOTTRADED", "PARTTRADED"]);

  return (
    <div className="space-y-5">
      {error && <p className="rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger">{error}</p>}
      {submitInfo && <p className="rounded border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{submitInfo}</p>}

      <section className="panel border-amber-300 bg-amber-50/30 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">手动下单 · MainEngine</div>
            <h2 className="mt-1 text-lg font-semibold">真实下单（直接发送到 TigerOpen 实盘）</h2>
            <p className="mt-1 text-xs text-amber-900">⚠ 真钱交易。订单走 RiskManager 预检 → MainEngine → TigerOpenGateway。</p>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <label className="block text-xs text-slate-500">
            标的
            <input className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={orderForm.symbol}
              onChange={(e) => setOrderForm((f) => ({ ...f, symbol: e.target.value.trim().toUpperCase() }))} placeholder="AAPL" />
          </label>
          <label className="block text-xs text-slate-500">
            交易所
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={orderForm.exchange}
              onChange={(e) => setOrderForm((f) => ({ ...f, exchange: e.target.value }))}>
              <option value="SMART">SMART</option>
              <option value="NASDAQ">NASDAQ</option>
              <option value="NYSE">NYSE</option>
              <option value="ARCA">ARCA</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            方向
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={orderForm.direction}
              onChange={(e) => setOrderForm((f) => ({ ...f, direction: e.target.value as "buy" | "sell" }))}>
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            订单类型
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={orderForm.order_type}
              onChange={(e) => setOrderForm((f) => ({ ...f, order_type: e.target.value as "limit" | "market" | "stop" }))}>
              <option value="limit">限价</option>
              <option value="market">市价</option>
              <option value="stop">止损</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            开平仓
            <select className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={orderForm.offset}
              onChange={(e) => setOrderForm((f) => ({ ...f, offset: e.target.value }))}>
              <option value="NONE">默认</option>
              <option value="OPEN">开仓</option>
              <option value="CLOSE">平仓</option>
            </select>
          </label>
          <label className="block text-xs text-slate-500">
            数量
            <input type="number" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={orderForm.volume}
              onChange={(e) => setOrderForm((f) => ({ ...f, volume: e.target.value }))} placeholder="1" />
          </label>
          <label className="block text-xs text-slate-500">
            价格 {orderForm.order_type === "market" && <span className="text-slate-400">(市价单忽略)</span>}
            <input type="number" step="0.01" disabled={orderForm.order_type === "market"} className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm disabled:bg-slate-100" value={orderForm.price}
              onChange={(e) => setOrderForm((f) => ({ ...f, price: e.target.value }))} placeholder="150.00" />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded border border-line bg-panel/40 p-2">
          <span className="text-xs font-medium text-slate-700">💰 仓位 helper</span>
          <span className="text-xs text-slate-500">按 % 资金算股数</span>
          {[0.1, 0.25, 0.5, 1.0].map((p) => (
            <button key={p} className="focus-ring rounded border border-line bg-white px-2 py-1 text-xs hover:bg-blue-50"
              disabled={busy}
              onClick={() => { setSizingPct(String(p)); estimateVolumeFromPct(String(p)); }}
            >{(p * 100).toFixed(0)}%</button>
          ))}
          <label className="flex items-center gap-1 text-xs text-slate-700">
            自定义:
            <input type="number" step="0.01" min="0.01" max="1" className="focus-ring w-16 rounded border border-line px-2 py-1 text-xs"
              value={sizingPct} onChange={(e) => setSizingPct(e.target.value)} placeholder="0.50" />
            <button className="focus-ring rounded border border-line bg-white px-2 py-1 text-xs hover:bg-blue-50"
              disabled={busy || !sizingPct}
              onClick={() => estimateVolumeFromPct(sizingPct)}>应用</button>
          </label>
          <span className="text-[11px] text-slate-500">(读 Tiger buying_power + 行情 ask 价，floor 到整股)</span>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={orderForm.outside_rth}
              onChange={(e) => setOrderForm((f) => ({ ...f, outside_rth: e.target.checked }))}
            />
            <span>允许盘前/盘后/夜盘 (outside RTH)</span>
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-700">
            <span>TIF</span>
            <select
              className="focus-ring rounded border border-line px-2 py-1 text-xs"
              value={orderForm.time_in_force}
              onChange={(e) => setOrderForm((f) => ({ ...f, time_in_force: e.target.value as "DAY" | "GTC" }))}
            >
              <option value="DAY">DAY · 当日有效</option>
              <option value="GTC">GTC · 长期有效</option>
            </select>
          </label>
          <p className="text-xs text-slate-500">
            夜盘 = 盘前 (04:00 ET) / 盘后 (16:00–20:00 ET)。仅限价单、Tiger 支持的标的；不能与市价单组合。
          </p>
        </div>
        <div className="mt-3">
          <button
            className={`focus-ring rounded px-5 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50 ${orderForm.direction === "buy" ? "bg-emerald-600" : "bg-red-600"}`}
            onClick={handleSubmit} disabled={submitting || !orderForm.symbol || !orderForm.volume}
          >
            {submitting ? "提交中…" : `${orderForm.direction === "buy" ? "买入" : "卖出"} ${orderForm.symbol || "?"} ${orderForm.volume || "?"}${orderForm.outside_rth ? " · 夜盘" : ""}`}
          </button>
        </div>
      </section>

      <RealPositionGroupsPanel groups={positionGroups} fallbackPositions={fallbackPositions} />

      <section className="panel p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">Tiger 订单</div>
            <h2 className="mt-1 text-lg font-semibold">
              券商订单 ·{" "}
              <span className="text-sm font-normal text-slate-500">
                {(() => {
                  const total = realOrders.length;
                  const active = realOrders.filter((o) => {
                    const s = (o.status ?? "").toLowerCase().replace(/[\s-]/g, "_");
                    return ["submitting", "not_traded", "nottraded", "part_traded", "parttraded", "pending", "new", "accepted", "open", "working"].includes(s);
                  }).length;
                  return `${active} 活跃 / ${total} 总数`;
                })()}
              </span>
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input type="checkbox" checked={hideCancelled} onChange={(e) => setHideCancelled(e.target.checked)} />
              <span>隐藏已撤销 / 已成交 / 已拒单</span>
            </label>
            <button
              className="focus-ring rounded border border-red-300 bg-red-50 px-3 py-1.5 text-sm text-red-700 hover:bg-red-100 disabled:opacity-50"
              disabled={busy}
              onClick={async () => {
                if (!confirm("一键撤掉全部活跃订单？")) return;
                setBusy(true); setError(null);
                try {
                  const r = await api.cancelAllCoreOrders();
                  setSubmitInfo(`✓ 撤单 ${r.cancelled.length}/${r.requested}${r.errors.length ? ` · 失败 ${r.errors.length}` : ""}`);
                  await refresh();
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setBusy(false);
                }
              }}
            >一键撤销所有活跃单</button>
            <button className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50" onClick={refresh} disabled={busy}>刷新</button>
          </div>
        </div>
        <RealOrdersTable
          orders={hideCancelled
            ? realOrders.filter((o) => {
                const s = (o.status ?? "").toLowerCase().replace(/[\s-]/g, "_");
                return !["cancelled", "canceled", "rejected", "expired", "filled", "all_traded", "executed"].includes(s);
              })
            : realOrders}
          onOpenRealOrder={setSelectedOrder}
          onCancelOrder={handleCancelRealOrder}
          onModifyOrder={openModifyModalFromReal}
          busy={busy}
        />
      </section>

      {selectedOrder && <RealOrderReadonlyDetails order={selectedOrder} />}

      <section className="panel p-4">
        <div className="mb-3">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">MainEngine OMS</div>
          <h2 className="mt-1 text-lg font-semibold">内部订单账本</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-slate-500">
                <th className="pb-2 pr-4">标的</th>
                <th className="pb-2 pr-4">方向</th>
                <th className="pb-2 pr-4">价格</th>
                <th className="pb-2 pr-4">数量</th>
                <th className="pb-2 pr-4">已成交</th>
                <th className="pb-2 pr-4">状态</th>
                <th className="pb-2 pr-4">来源</th>
                <th className="pb-2 pr-4">时间</th>
                <th className="pb-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {coreOrders.length === 0 ? (
                <tr><td colSpan={9} className="py-4 text-center text-slate-500">暂无内部订单</td></tr>
              ) : (
                coreOrders.map((o) => (
                  <tr key={o.vt_orderid} className="border-b border-line/50">
                    <td className="py-2 pr-4 font-medium">{o.symbol}</td>
                    <td className="py-2 pr-4">{o.direction}</td>
                    <td className="py-2 pr-4">{optionalDecimal(o.price, 2)}</td>
                    <td className="py-2 pr-4">{o.volume}</td>
                    <td className="py-2 pr-4">{o.traded}</td>
                    <td className="py-2 pr-4">{textValue(o.status)}</td>
                    <td className="py-2 pr-4 text-xs text-slate-500">{textValue(o.reference)}</td>
                    <td className="py-2 pr-4 text-xs text-slate-500">{o.datetime ? new Date(o.datetime).toLocaleTimeString() : "-"}</td>
                    <td className="py-2">
                      {activeStatuses.has(o.status) && (
                        <div className="flex gap-2">
                          <button className="text-xs text-blue-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => openModifyModal(o)}>修改</button>
                          <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => handleCancelCoreOrder(o.orderid)}>取消</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel p-4">
        <div className="mb-3">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">MainEngine OMS</div>
          <h2 className="mt-1 text-lg font-semibold">持仓</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-slate-500">
                <th className="pb-2 pr-4">标的</th>
                <th className="pb-2 pr-4">方向</th>
                <th className="pb-2 pr-4">数量</th>
                <th className="pb-2 pr-4">均价</th>
                <th className="pb-2 pr-4">浮盈</th>
                <th className="pb-2">网关</th>
              </tr>
            </thead>
            <tbody>
              {corePositions.length === 0 ? (
                <tr><td colSpan={6} className="py-4 text-center text-slate-500">暂无持仓</td></tr>
              ) : (
                corePositions.map((p) => (
                  <tr key={p.vt_positionid} className="border-b border-line/50">
                    <td className="py-2 pr-4 font-medium">{p.symbol}</td>
                    <td className="py-2 pr-4">{p.direction}</td>
                    <td className="py-2 pr-4">{p.volume}</td>
                    <td className="py-2 pr-4">{optionalDecimal(p.price, 2)}</td>
                    <td className="py-2 pr-4">{optionalMoney(p.pnl)}</td>
                    <td className="py-2 text-xs text-slate-500">{p.gateway_name}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel p-4">
        <div className="mb-3">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">MainEngine OMS</div>
          <h2 className="mt-1 text-lg font-semibold">成交记录</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-slate-500">
                <th className="pb-2 pr-4">标的</th>
                <th className="pb-2 pr-4">方向</th>
                <th className="pb-2 pr-4">价格</th>
                <th className="pb-2 pr-4">数量</th>
                <th className="pb-2">时间</th>
              </tr>
            </thead>
            <tbody>
              {coreTrades.length === 0 ? (
                <tr><td colSpan={5} className="py-4 text-center text-slate-500">暂无成交记录</td></tr>
              ) : (
                coreTrades.map((t) => (
                  <tr key={t.vt_tradeid} className="border-b border-line/50">
                    <td className="py-2 pr-4 font-medium">{t.symbol}</td>
                    <td className="py-2 pr-4">{t.direction}</td>
                    <td className="py-2 pr-4">{optionalDecimal(t.price, 2)}</td>
                    <td className="py-2 pr-4">{t.volume}</td>
                    <td className="py-2 text-xs text-slate-500">{t.datetime ? new Date(t.datetime).toLocaleTimeString() : "-"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <Modal
        open={!!modifyTarget}
        onClose={() => setModifyTarget(null)}
        title={modifyTarget ? `修改委托 ${modifyTarget.symbol} (${modifyTarget.direction})` : ""}
        footer={
          <>
            <button className="rounded border border-line bg-white px-4 py-1.5 text-sm hover:bg-panel" onClick={() => setModifyTarget(null)}>取消</button>
            <button className="rounded border border-accent bg-accent px-4 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50" disabled={busy} onClick={submitModify}>取消老单并重新提交</button>
          </>
        }
      >
        <p className="mb-3 text-xs text-amber-700">⚠ 操作链路：cancel 老订单 → 用新 price/volume 走 RiskManager → 重新 send。</p>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-xs text-slate-500">
            新价格 ($)
            <input type="number" step="0.01" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={modifyDraft.price}
              onChange={(e) => setModifyDraft((d) => ({ ...d, price: e.target.value }))} />
          </label>
          <label className="block text-xs text-slate-500">
            新数量
            <input type="number" className="focus-ring mt-1 w-full rounded border border-line px-3 py-1.5 text-sm" value={modifyDraft.volume}
              onChange={(e) => setModifyDraft((d) => ({ ...d, volume: e.target.value }))} />
          </label>
        </div>
        {modifyTarget && (
          <div className="mt-3 rounded border border-line bg-panel p-2 text-xs text-slate-600">
            原订单：价格 {modifyTarget.price} · 总量 {modifyTarget.volume} · 已成交 {modifyTarget.traded} · 剩余 {modifyTarget.volume - modifyTarget.traded}
          </div>
        )}
      </Modal>
    </div>
  );
}
