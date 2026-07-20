# Launch Kit

These drafts are prepared for the maintainers. Publishing to any external
channel requires a separate confirmation after the public repository is live.

## Positioning

Omega Quant Platform is an original, event-driven platform for US-equity and
option research, risk, backtesting, and guarded TigerOpen execution. It combines
Massive market data, a React workbench, portfolio and option analytics, and an
optional LEAN sidecar behind one API and one product identity.

## Short post (X / Jike)

> Introducing Omega Quant Platform: an open-source US equities and options
> research + execution workbench. Event-driven OMS, CTA/backtesting, portfolio
> risk, option Greeks, Massive data, and session-armed TigerOpen execution.
> Credential-free demo included. Apache-2.0. Feedback welcome.

Chinese:

> Omega Quant Platform 开源首发：一个统一的美股/期权研究与执行工作台。
> 包含事件驱动 OMS、CTA/回测、组合风险、期权 Greeks、Massive 行情和
> 会话级武装的 TigerOpen 执行。无凭据 Demo 可直接体验，Apache-2.0。

## Hacker News

**Title:** Show HN: Omega Quant Platform – event-driven US stock/options research and guarded execution

**Body:**

Omega is a Python/FastAPI + React quant workbench focused on US equities and
options. The unusual part is that portfolio research, option scenarios,
event-driven OMS/CTA components, audit trails, and live-capable TigerOpen routing
share one domain model. Every process starts locked; broker submission requires
an API token, an exact expected-account match, a notional ceiling, account-suffix
confirmation, risk acknowledgement, and session arming. The demo is deterministic
and contacts no broker or data vendor. I would value feedback on the architecture,
safety model, and first contribution issues.

## Reddit

**Title:** I built an open-source US equities/options research and execution platform

Explain the problem first, link to the one-command demo, disclose the integration
boundaries, and ask for concrete feedback. Do not post portfolio performance,
investment claims, or growth targets.

## V2EX

**Title:** [开源] Omega Quant Platform：统一的美股/期权量化研究与执行平台

介绍一条命令 Demo、事件驱动架构、组合/期权风险能力和实盘安全门。
明确声明软件不构成投资建议，Demo 不访问券商，并邀请社区审查风险边界。

## Repository metadata

- Description: `Open-source event-driven US equities and options research, risk, backtesting, and guarded execution platform.`
- Topics: `quant-trading`, `algorithmic-trading`, `options`, `backtesting`,
  `portfolio-optimization`, `risk-management`, `fastapi`, `react`, `tigeropen`,
  `massive`
- Social preview: `docs/assets/omega-social-preview.png` (1280×640)

