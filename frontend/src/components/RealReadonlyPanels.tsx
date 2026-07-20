import { Eye } from "lucide-react";
import { Info, Metric } from "./primitives";
import type {
  RealAccountRisk,
  RealAccountSummary,
  RealOrderReadonly,
  RealPosition,
  RealPositionGroup,
  RealStreamDetail,
  RealStreamStatus
} from "../types";
import { booleanText, money, numberMaybe, optionalDecimal, optionalMoney, optionalPct, textValue } from "../utils/format";

export function RealAccountReadonlyDashboard({
  summary,
  positions,
  positionGroups,
  orders,
  streamStatus,
  streamDetail,
  risk,
  onOpenRealOrder
}: {
  summary: RealAccountSummary;
  positions: RealPosition[];
  positionGroups: RealPositionGroup[];
  orders: RealOrderReadonly[];
  streamStatus: RealStreamStatus;
  streamDetail: RealStreamDetail;
  risk: RealAccountRisk;
  onOpenRealOrder: (order: RealOrderReadonly) => void;
}) {
  return (
    <div className="space-y-5">
      {!summary.available ? (
        <section className="panel border-amber-200 bg-amber-50 p-4">
          <div className="flex items-start gap-3">
            <Eye className="mt-0.5 text-amber-800" size={20} />
            <div>
              <h2 className="font-semibold text-amber-950">TigerOpen 账户数据暂不可用</h2>
              <p className="mt-1 text-sm text-amber-900">{summary.message}</p>
            </div>
          </div>
        </section>
      ) : null}

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="账户权益" value={summary.available ? money(summary.real_account_equity) : "暂不可用"} />
        <Metric label="净清算价值" value={summary.available ? money(summary.net_liquidation) : "暂不可用"} />
        <Metric label="现金" value={summary.available ? money(summary.real_cash) : "暂不可用"} />
        <Metric label="购买力" value={summary.available ? money(summary.buying_power) : "暂不可用"} />
        <Metric label="持仓数" value={`${summary.positions_count}`} />
        <Metric label="订单数" value={`${summary.orders_count}`} />
        <Metric label="账户来源" value={summary.source} />
        <Metric label="更新时间" value={summary.queried_at ? new Date(summary.queried_at).toLocaleString() : "暂不可用"} />
      </section>

      {summary.errors.length ? (
        <section className="panel border-amber-200 bg-amber-50 p-4">
          <h2 className="mb-2 font-semibold text-amber-900">数据提示</h2>
          <ul className="space-y-1 text-sm text-amber-900">
            {summary.errors.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <RealStreamStatusPanel status={streamStatus} detail={streamDetail} />
        <RealRiskPanel risk={risk} />
      </section>

      <section className="space-y-4">
        <RealQuoteGroupsPanel detail={streamDetail} />
        <RealOrderFlowPanel detail={streamDetail} />
        <RealPositionGroupsPanel groups={positionGroups} fallbackPositions={positions} />
        <RealOrdersTable orders={orders} onOpenRealOrder={onOpenRealOrder} />
      </section>
    </div>
  );
}

function RealStreamStatusPanel({ status, detail }: { status: RealStreamStatus; detail: RealStreamDetail }) {
  const counts = status.counts ?? {};
  const market = detail.market_data ?? {};
  const connectedTone = status.connected ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800";
  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Tiger 推送流</h2>
          <p className="text-sm text-slate-500">读取当前 TigerOpen 账户资产、持仓、订单和成交事件；交易提交统一走 MainEngine / RiskManager / TigerOpenGateway。</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${connectedTone}`}>
          {status.connected ? "已连接" : "未连接"} / {status.freshness}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <Info label="资产事件" value={String(counts.assets ?? 0)} />
        <Info label="持仓事件" value={String(counts.positions ?? 0)} />
        <Info label="订单事件" value={String(counts.orders ?? 0)} />
        <Info label="成交事件" value={String(counts.transactions ?? 0)} />
        <Info label="报价" value={String(counts.quotes ?? 0)} />
        <Info label="盘口" value={String(counts.bbo ?? 0)} />
        <Info label="逐笔" value={String(counts.ticks ?? 0)} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
        <span className="rounded border border-line bg-panel px-2 py-1">状态：{status.status}</span>
        <span className="rounded border border-line bg-panel px-2 py-1">来源：{status.source}</span>
        <span className="rounded border border-line bg-panel px-2 py-1">更新时间：{status.updated_at ?? "暂不可用"}</span>
        <span className="rounded border border-line bg-panel px-2 py-1">序号：{String(status.sequence ?? "-")}</span>
        <span className="rounded border border-line bg-panel px-2 py-1">页面连接：{textValue(market.viewer_count)}</span>
        <span className="rounded border border-line bg-panel px-2 py-1">空闲暂停：{booleanText(market.paused_by_no_viewers)}</span>
      </div>
      <RecentStreamEvents events={detail.recent_events} />
    </section>
  );
}

function RealRiskPanel({ risk }: { risk: RealAccountRisk }) {
  const greeks = risk.option_greeks_exposure ?? {};
  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">真实账户风险观察</h2>
          <p className="text-sm text-slate-500">账户风险解释，用于监督当前 TigerOpen 账户交易。</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${risk.available ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          {risk.available ? "可用" : "暂不可用"}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <Info label="净清算" value={optionalMoney(risk.net_liquidation)} />
        <Info label="现金" value={optionalMoney(risk.cash_balance)} />
        <Info label="购买力" value={optionalMoney(risk.buying_power)} />
        <Info label="总持仓市值" value={optionalMoney(risk.gross_position_value)} />
        <Info label="今日盈亏" value={optionalMoney(risk.total_today_pl)} />
        <Info label="今日盈亏来源" value={risk.total_today_pl_source ?? "-"} />
        <Info label="期权暴露" value={optionalMoney(risk.option_market_value_abs)} />
        <Info label="期权占比" value={optionalPct(risk.option_weight)} />
      </div>
      <GreekExposureSummary exposure={greeks} />
      <p className="mt-3 text-sm text-slate-500">
        {risk.available ? `账户风险观察 ${risk.flags.length} 条；用于监督当前 TigerOpen 账户上下文。` : risk.message}
      </p>
    </section>
  );
}

function GreekExposureSummary({ exposure }: { exposure: Record<string, unknown> | undefined }) {
  const available = exposure?.available === true;
  return (
    <div className="mt-3 rounded border border-line bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold">期权 Greeks 合计</div>
        <span className={`rounded border px-2 py-1 text-xs ${available ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-100 text-slate-700"}`}>
          {available ? "已估算" : "暂无数据"}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <Info label="合约数" value={optionalDecimal(exposure?.total_contracts_abs, 0)} />
        <Info label="Delta 等效股数" value={optionalDecimal(exposure?.delta_shares, 2)} />
        <Info label="Gamma 等效" value={optionalDecimal(exposure?.gamma_shares, 2)} />
        <Info label="Theta" value={optionalDecimal(exposure?.theta, 2)} />
        <Info label="Vega" value={optionalDecimal(exposure?.vega, 2)} />
        <Info label="加权 IV" value={optionalPct(exposure?.weighted_iv)} />
        <Info label="来源" value={Array.isArray(exposure?.sources) && exposure.sources.length ? exposure.sources.join(", ") : "-"} />
        <Info label="用途" value="账户监督" />
      </div>
      <p className="mt-2 text-xs text-slate-500">{textValue(exposure?.message, "暂无可聚合的期权 Greeks。")}</p>
    </div>
  );
}

function RecentStreamEvents({ events }: { events: Array<Record<string, unknown>> }) {
  if (!events.length) {
    return <p className="mt-3 text-sm text-slate-500">暂无推送事件。</p>;
  }
  return (
    <div className="mt-3 rounded border border-line bg-white p-3">
      <div className="mb-2 text-sm font-semibold">最近推送事件</div>
      <div className="space-y-1">
        {events.slice(-5).reverse().map((event, index) => (
          <div key={`${textValue(event.type)}-${index}`} className="grid grid-cols-1 gap-1 rounded border border-line bg-panel px-2 py-1 text-xs sm:grid-cols-[100px_1fr_150px] sm:gap-2 sm:border-0 sm:bg-transparent sm:px-0 sm:py-0">
            <span className="font-medium">{textValue(event.type)}</span>
            <span className="break-all text-slate-600 sm:truncate">{textValue(event.symbol ?? event.key ?? "-")}</span>
            <span className="text-slate-500 sm:truncate sm:text-right">{textValue(event.at)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RealQuoteGroupsPanel({ detail }: { detail: RealStreamDetail }) {
  const groups = detail.quote_groups ?? [];
  if (!groups.length) {
    return (
      <section className="panel p-4">
        <h2 className="text-lg font-semibold">实时行情配对</h2>
        <p className="mt-1 text-sm text-slate-500">暂无股票/期权实时行情配对。开启实时行情流并订阅持仓标的后会显示。</p>
      </section>
    );
  }
  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">实时行情配对</h2>
          <p className="text-sm text-slate-500">同一 underlying 下展示股票 Q 与期权 Q/T/BBO。</p>
        </div>
        <span className="rounded border border-line bg-panel px-2 py-1 text-xs">{groups.length} 组</span>
      </div>
      <div className="space-y-3">
        {groups.map((group, index) => (
          <QuoteGroupCard key={`${textValue(group.underlying)}-${index}`} group={group} />
        ))}
      </div>
    </section>
  );
}

function QuoteGroupCard({ group }: { group: Record<string, unknown> }) {
  const stock = (group.stock ?? null) as Record<string, unknown> | null;
  const options = (Array.isArray(group.options) ? group.options : []) as Record<string, unknown>[];
  return (
    <article className="rounded border border-line bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{textValue(group.underlying)}</h3>
        <span className="rounded border border-line bg-panel px-2 py-1 text-xs">期权 {options.length}</span>
      </div>
      <div className="grid grid-cols-1 gap-2 xl:grid-cols-[260px_1fr]">
        <MarketQuoteTile title="股票" symbol={textValue(group.stock_symbol)} data={stock} />
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {options.length ? (
            options.map((option, index) => (
              <MarketQuoteTile
                key={`${textValue(option.symbol)}-${index}`}
                title="期权"
                symbol={textValue(option.symbol)}
                data={(option.data ?? {}) as Record<string, unknown>}
              />
            ))
          ) : (
            <div className="rounded border border-line bg-panel p-3 text-sm text-slate-500">没有对应期权行情。</div>
          )}
        </div>
      </div>
    </article>
  );
}

function MarketQuoteTile({ title, symbol, data }: { title: string; symbol: string; data: Record<string, unknown> | null }) {
  const book = (data?.bbo ?? data ?? {}) as Record<string, unknown>;
  return (
    <div className="rounded border border-line bg-panel p-3 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-semibold">{title}</span>
        <span className="break-all text-right text-slate-600">{symbol || textValue(data?.symbol)}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Info label="最新" value={optionalMoney(numberMaybe(data?.latest_price ?? data?.last_price ?? data?.price))} />
        <Info label="买一" value={optionalMoney(numberMaybe(book.bid_price ?? book.bid))} />
        <Info label="卖一" value={optionalMoney(numberMaybe(book.ask_price ?? book.ask))} />
        <Info label="成交量" value={textValue(data?.volume ?? data?.last_size)} />
      </div>
      <div className="mt-2 text-slate-500">收到：{textValue(data?.received_at)}</div>
    </div>
  );
}

function RealOrderFlowPanel({ detail }: { detail: RealStreamDetail }) {
  const orders = detail.orders ?? [];
  const transactions = detail.transactions ?? [];
  if (!orders.length && !transactions.length) {
    return (
      <section className="panel p-4">
        <h2 className="text-lg font-semibold">真实订单/成交推送</h2>
        <p className="mt-1 text-sm text-slate-500">暂无订单或成交推送。活动订单可在订单账本的 TigerOpen OMS 订单区管理。</p>
      </section>
    );
  }
  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">真实订单/成交推送</h2>
          <p className="text-sm text-slate-500">来自 Tiger 推送缓存的订单流；用于和 MainEngine OMS 状态对照。</p>
        </div>
        <span className="rounded border border-line bg-panel px-2 py-1 text-xs">
          订单 {orders.length} · 成交 {transactions.length}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <FlowEventRows title="订单推送" empty="暂无订单推送。" rows={orders} />
        <FlowEventRows title="成交推送" empty="暂无成交推送。" rows={transactions} />
      </div>
    </section>
  );
}

function FlowEventRows({ title, empty, rows }: { title: string; empty: string; rows: Array<Record<string, unknown>> }) {
  return (
    <div className="rounded border border-line bg-white p-3">
      <div className="mb-2 text-sm font-semibold">{title}</div>
      {rows.length ? (
        <div className="space-y-2">
          {rows.slice(-8).reverse().map((row, index) => (
            <details key={`${textValue(row.id ?? row.order_id ?? row.trade_id ?? row.symbol)}-${index}`} className="rounded border border-line bg-panel px-3 py-2 text-xs">
              <summary className="cursor-pointer">
                <span className="font-medium">{textValue(row.symbol ?? row.contract_symbol ?? row.identifier ?? row.display ?? "--")}</span>
                <span className="ml-2 text-slate-500">{textValue(row.action ?? row.side ?? row.status ?? row.order_status ?? row.trade_time ?? row.timestamp)}</span>
              </summary>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Info label="数量" value={textValue(row.quantity ?? row.filled_quantity ?? row.volume)} />
                <Info label="价格" value={optionalMoney(numberMaybe(row.price ?? row.avg_fill_price ?? row.filled_price ?? row.limit_price))} />
                <Info label="状态" value={textValue(row.status ?? row.order_status)} />
                <Info label="时间" value={textValue(row.updated_at ?? row.filled_at ?? row.timestamp ?? row.trade_time)} />
              </div>
              <pre className="mt-2 max-h-48 overflow-auto rounded bg-white p-2">{JSON.stringify(row, null, 2)}</pre>
            </details>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-500">{empty}</p>
      )}
    </div>
  );
}

export function RealPositionGroupsPanel({ groups, fallbackPositions }: { groups: RealPositionGroup[]; fallbackPositions: RealPosition[] }) {
  if (!groups.length) {
    return <RealPositionsTable positions={fallbackPositions} />;
  }
  return (
    <section className="panel p-4">
      <h2 className="mb-1 text-lg font-semibold">真实持仓</h2>
      <p className="mb-3 text-sm text-slate-500">股票和对应期权按同一标的归组；没有对应关系的持仓单独显示。</p>
      <div className="space-y-3">
        {groups.map((group) => (
          <article key={group.underlying} className="rounded border border-line bg-white p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="font-semibold">{group.underlying}</h3>
                <p className="text-xs text-slate-500">{group.position_count} 个持仓</p>
              </div>
            <div className="text-right text-xs">
                <div className="font-medium">{money(group.total_market_value)}</div>
                <div className={group.total_unrealized_pnl >= 0 ? "text-accent" : "text-danger"}>{money(group.total_unrealized_pnl)}</div>
              </div>
            </div>
            {group.option_positions.length ? <GroupGreekExposure exposure={group.option_greeks_exposure} /> : null}
            {group.stock_positions.length ? (
              <div className="mb-2">
                <div className="mb-1 text-xs font-medium text-slate-500">股票</div>
                <div className="space-y-1">
                  {group.stock_positions.map((position, index) => (
                    <RealPositionMiniRow key={`${group.underlying}-stock-${index}`} position={position} />
                  ))}
                </div>
              </div>
            ) : null}
            {group.option_positions.length ? (
              <div className="mb-2">
                <div className="mb-1 text-xs font-medium text-slate-500">期权</div>
                <div className="space-y-1">
                  {group.option_positions.map((position, index) => (
                    <RealPositionMiniRow key={`${group.underlying}-option-${index}`} position={position} />
                  ))}
                </div>
              </div>
            ) : null}
            {group.standalone_positions.length ? (
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">单独持仓</div>
                <div className="space-y-1">
                  {group.standalone_positions.map((position, index) => (
                    <RealPositionMiniRow key={`${group.underlying}-standalone-${index}`} position={position} />
                  ))}
                </div>
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function GroupGreekExposure({ exposure }: { exposure: Record<string, unknown> | undefined }) {
  if (!exposure?.available) {
    return null;
  }
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 rounded border border-line bg-panel p-2 text-xs md:grid-cols-5">
      <Info label="Delta 等效" value={optionalDecimal(exposure.delta_shares, 2)} />
      <Info label="Gamma" value={optionalDecimal(exposure.gamma_shares, 2)} />
      <Info label="Theta" value={optionalDecimal(exposure.theta, 2)} />
      <Info label="Vega" value={optionalDecimal(exposure.vega, 2)} />
      <Info label="加权 IV" value={optionalPct(exposure.weighted_iv)} />
    </div>
  );
}

function RealPositionMiniRow({ position }: { position: RealPosition }) {
  const canonical = textValue(position.canonical_option_symbol, "");
  const hasGreeks = [position.iv, position.delta, position.gamma, position.theta, position.vega].some((value) => value !== null && value !== undefined);
  return (
    <div className="grid grid-cols-1 gap-2 rounded border border-line bg-panel px-2 py-2 text-xs md:grid-cols-[minmax(0,1fr)_120px_150px_minmax(220px,0.8fr)]">
      <div className="min-w-0">
        <div className="break-all font-medium">{position.display ?? position.symbol ?? "-"}</div>
        {canonical ? <div className="text-slate-500">{canonical}</div> : null}
        {position.option_expiry ? (
          <div className="mt-1 text-slate-500">
            {position.option_put_call === "P" ? "Put" : "Call"} · 行权 {optionalDecimal(position.option_strike)} · 到期 {position.option_expiry}
            {position.days_to_expiry !== null && position.days_to_expiry !== undefined ? ` · DTE ${position.days_to_expiry}` : ""}
          </div>
        ) : null}
      </div>
      <div className="md:text-right">
        <div className="text-[11px] text-slate-500">数量 / 市价</div>
        <div>{position.quantity ?? "-"}</div>
        <div className="text-slate-500">{money(position.market_price)}</div>
      </div>
      <div className="md:text-right">
        <div className="text-[11px] text-slate-500">市值 / 盈亏</div>
        <div>{money(position.market_value)}</div>
        <div className={(position.unrealized_pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}>{money(position.unrealized_pnl)}</div>
      </div>
      <div className="md:text-right">
        <div className="text-[11px] text-slate-500">IV / 希腊值</div>
        {hasGreeks ? (
          <>
            <div>IV {optionalPct(position.iv)} · Δ {optionalDecimal(position.delta)}</div>
            <div className="text-slate-500">Γ {optionalDecimal(position.gamma)} · Θ {optionalDecimal(position.theta)} · V {optionalDecimal(position.vega)}</div>
            <div className="text-slate-500">买/卖 {optionalMoney(position.option_bid)} / {optionalMoney(position.option_ask)} · OI {optionalDecimal(position.open_interest, 0)}</div>
            <div className="text-slate-500">标记价 {optionalMoney(position.mark_price)} · {position.greeks_engine ?? position.greeks_source ?? "-"}</div>
            {position.option_moneyness !== null && position.option_moneyness !== undefined ? <div className="text-slate-500">价内/价外程度 {optionalPct(position.option_moneyness)}</div> : null}
          </>
        ) : (
          <div className="text-slate-500">{canonical ? "暂未取得 Greeks" : "-"}</div>
        )}
      </div>
    </div>
  );
}

export function RealPositionsTable({ positions }: { positions: RealPosition[] }) {
  return (
    <div className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h2 className="font-semibold">TigerOpen 持仓</h2>
        <p className="text-xs text-slate-500">来自当前 TigerOpen 配置账户。</p>
      </div>
      <div className="space-y-2 p-3 md:hidden">
        {positions.map((position, index) => (
          <RealPositionMiniRow key={`${position.display}-${index}-mobile`} position={position} />
        ))}
      </div>
      <div className="hidden overflow-x-auto md:block">
        <table className="min-w-[1220px] w-full text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">类型</th>
              <th className="px-3 py-2">数量</th>
              <th className="px-3 py-2">成本</th>
              <th className="px-3 py-2">市价</th>
              <th className="px-3 py-2">市值</th>
              <th className="px-3 py-2">未实现盈亏</th>
              <th className="px-3 py-2">IV</th>
              <th className="px-3 py-2">Delta</th>
              <th className="px-3 py-2">Gamma</th>
              <th className="px-3 py-2">Theta</th>
              <th className="px-3 py-2">Vega</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position, index) => (
              <tr key={`${position.display}-${index}`} className="border-t border-line">
                <td className="break-all px-3 py-2">{position.display ?? position.symbol ?? "-"}</td>
                <td className="px-3 py-2">{position.sec_type ?? "-"}</td>
                <td className="px-3 py-2">{position.quantity ?? "-"}</td>
                <td className="px-3 py-2">{position.average_cost !== null ? money(position.average_cost) : "-"}</td>
                <td className="px-3 py-2">{position.market_price !== null ? money(position.market_price) : "-"}</td>
                <td className="px-3 py-2">{position.market_value !== null ? money(position.market_value) : "-"}</td>
                <td className="px-3 py-2">{position.unrealized_pnl !== null ? money(position.unrealized_pnl) : "-"}</td>
                <td className="px-3 py-2">{optionalPct(position.iv)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.delta)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.gamma)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.theta)}</td>
                <td className="px-3 py-2">{optionalDecimal(position.vega)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!positions.length ? <p className="p-4 text-sm text-slate-500">持仓暂不可用。</p> : null}
    </div>
  );
}

const REAL_ORDER_ACTIVE_STATUSES = new Set([
  "submitting", "not_traded", "nottraded", "part_traded", "parttraded",
  "pending", "new", "accepted", "open", "working",
]);

function realOrderPrice(order: RealOrderReadonly): number | null {
  const ctx = order.raw_context ?? {};
  for (const key of ["price", "limit_price", "filled_price", "avg_fill_price"]) {
    const v = (ctx as Record<string, unknown>)[key];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
}

function isRealOrderActive(order: RealOrderReadonly): boolean {
  const s = (order.status ?? "").toLowerCase().replace(/[\s-]/g, "_");
  return REAL_ORDER_ACTIVE_STATUSES.has(s);
}

export function RealOrdersTable({
  orders,
  onOpenRealOrder,
  onCancelOrder,
  onModifyOrder,
  busy,
}: {
  orders: RealOrderReadonly[];
  onOpenRealOrder: (order: RealOrderReadonly) => void;
  onCancelOrder?: (order: RealOrderReadonly) => void;
  onModifyOrder?: (order: RealOrderReadonly) => void;
  busy?: boolean;
}) {
  const hasActions = Boolean(onCancelOrder || onModifyOrder);
  return (
    <div className="panel overflow-hidden p-0">
      <div className="border-b border-line px-4 py-3">
        <h2 className="font-semibold">TigerOpen 券商订单</h2>
        <p className="text-xs text-slate-500">来自 TigerOpen 账户/推送的券商订单上下文；和 MainEngine 本地风控拒单不是同一类记录。</p>
      </div>
      <div className="space-y-2 p-3 md:hidden">
        {orders.map((order, index) => {
          const price = realOrderPrice(order);
          const active = hasActions && isRealOrderActive(order);
          return (
            <div key={`${order.broker_order_id}-${index}-mobile`} className="rounded border border-line bg-white p-3 text-sm">
              <button className="focus-ring w-full text-left" onClick={() => onOpenRealOrder(order)}>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">{order.symbol ?? "-"}</span>
                  <span className="rounded border border-line bg-panel px-2 py-1 text-xs">{order.status ?? "-"}</span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <Info label="方向" value={order.side ?? "-"} />
                  <Info label="数量" value={String(order.quantity ?? "-")} />
                  <Info label="委托价" value={price !== null ? money(price) : "市价/-"} />
                  <Info label="订单号" value={order.broker_order_id || "-"} />
                  <Info label="提交时间" value={order.submitted_at ?? "-"} />
                </div>
              </button>
              {active && (
                <div className="mt-2 flex gap-3 border-t border-line pt-2">
                  {onModifyOrder && (
                    <button className="text-xs text-blue-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => onModifyOrder(order)}>修改</button>
                  )}
                  {onCancelOrder && (
                    <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => onCancelOrder(order)}>取消</button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="hidden overflow-x-auto md:block">
        <table className="min-w-[760px] w-full text-left text-sm">
          <thead className="bg-panel text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">券商订单号</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2">方向</th>
              <th className="px-3 py-2">数量</th>
              <th className="px-3 py-2">委托价</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">提交时间</th>
              {hasActions && <th className="px-3 py-2">操作</th>}
            </tr>
          </thead>
          <tbody>
            {orders.map((order, index) => {
              const price = realOrderPrice(order);
              const active = hasActions && isRealOrderActive(order);
              return (
                <tr key={`${order.broker_order_id}-${index}`} className="border-t border-line hover:bg-panel">
                  <td className="cursor-pointer break-all px-3 py-2" onClick={() => onOpenRealOrder(order)}>{order.broker_order_id || "-"}</td>
                  <td className="cursor-pointer px-3 py-2" onClick={() => onOpenRealOrder(order)}>{order.symbol ?? "-"}</td>
                  <td className="cursor-pointer px-3 py-2" onClick={() => onOpenRealOrder(order)}>{order.side ?? "-"}</td>
                  <td className="cursor-pointer px-3 py-2" onClick={() => onOpenRealOrder(order)}>{order.quantity ?? "-"}</td>
                  <td className="cursor-pointer px-3 py-2 font-mono" onClick={() => onOpenRealOrder(order)}>{price !== null ? money(price) : <span className="text-slate-400">市价/-</span>}</td>
                  <td className="cursor-pointer px-3 py-2" onClick={() => onOpenRealOrder(order)}>{order.status ?? "-"}</td>
                  <td className="cursor-pointer px-3 py-2" onClick={() => onOpenRealOrder(order)}>{order.submitted_at ?? "-"}</td>
                  {hasActions && (
                    <td className="px-3 py-2">
                      {active ? (
                        <div className="flex gap-3">
                          {onModifyOrder && (
                            <button className="text-xs text-blue-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => onModifyOrder(order)}>修改</button>
                          )}
                          {onCancelOrder && (
                            <button className="text-xs text-red-600 hover:underline disabled:opacity-50" disabled={busy} onClick={() => onCancelOrder(order)}>取消</button>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-slate-400">-</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!orders.length ? <p className="p-4 text-sm text-slate-500">订单暂不可用。</p> : null}
    </div>
  );
}

export function RealOrderReadonlyDetails({ order }: { order: RealOrderReadonly | null }) {
  return (
    <aside className="panel p-4">
      <div className="mb-3 flex items-center gap-2">
        <Eye size={17} />
        <h2 className="text-lg font-semibold">真实订单详情</h2>
      </div>
      <p className="mb-3 text-sm text-slate-500">外部上下文，没有 Omega lineage。</p>
      {order ? (
        <div className="space-y-3">
          <div className="rounded border border-blue-200 bg-blue-50 p-3 text-sm">
            <div className="font-semibold">{order.symbol ?? "unknown"} / {order.status ?? "unknown"}</div>
            <p className="mt-1 text-slate-600">来源标记：{order.readonly_badge}</p>
          </div>
          <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            <Info label="券商订单号" value={order.broker_order_id || "-"} />
            <Info label="方向" value={order.side ?? "-"} />
            <Info label="数量" value={String(order.quantity ?? "-")} />
            <Info label="委托价" value={(() => { const p = realOrderPrice(order); return p !== null ? money(p) : "市价/-"; })()} />
            <Info label="已成交" value={(() => { const t = (order.raw_context as Record<string, unknown>)?.traded; return typeof t === "number" ? String(t) : "-"; })()} />
            <Info label="成交均价" value={(() => { const ctx = order.raw_context as Record<string, unknown>; const v = ctx?.avg_fill_price ?? ctx?.filled_price; return typeof v === "number" ? money(v) : "-"; })()} />
            <Info label="提交时间" value={order.submitted_at ?? "-"} />
            <Info label="成交时间" value={order.filled_at ?? "-"} />
            <Info label="来源" value={order.source} />
          </div>
          <details>
            <summary className="cursor-pointer text-xs font-medium text-slate-600">查看原始上下文</summary>
            <pre className="mt-2 max-h-72 overflow-auto rounded bg-panel p-2 text-xs">{JSON.stringify(order.raw_context, null, 2)}</pre>
          </details>
        </div>
      ) : (
        <p className="text-sm text-slate-500">选择一条订单查看详情。</p>
      )}
    </aside>
  );
}
