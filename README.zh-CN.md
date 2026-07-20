# Omega Quant Platform

[![CI](https://github.com/RobbieRao/Omega-Quant-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/RobbieRao/Omega-Quant-Platform/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776ab.svg)](pyproject.toml)

Omega 是面向美股与美股期权的事件驱动开源量化研究和实盘执行平台。CTA
策略、回测、组合风险、期权 Greeks、TigerOpen 执行、Massive 行情与 React
操作台统一在一个代码库中。

**[English](README.md) · [架构](docs/architecture.md) · [实盘安全](docs/live-trading-safety.md) · [配置](docs/configuration.md)**

> [!WARNING]
> 显式配置并武装后，本软件可以向真实券商账户提交订单。它不是投资建议。
> 凭据保护、账户确认、策略验证与资金损失均由使用者负责。

## 一条命令体验

```bash
docker compose up --build
```

打开 <http://127.0.0.1:8770>。Demo 使用确定性合成数据，不需要任何 API
Key，不读取本地凭据，也不会触达券商或数据供应商，实盘执行保持锁定。

本地开发：

```bash
uv sync --no-editable --extra dev
uv run omega-quant demo
```

## 核心能力

- 同一事件内核承载 OMS、CTA、回测、风控、期权、价差、TWAP/Iceberg 与录制。
- 支持美股和 OCC 期权符号，提供 Greeks、IV 暴露与情景矩阵。
- TigerOpen 账户/交易网关、Massive REST/WebSocket 行情与限流、陈旧度处理。
- vectorbt、PyPortfolioOpt、Riskfolio-Lib 研究适配和 LEAN sidecar 集成。
- FastAPI、稳定错误码、SSE 后台任务、英文默认并可切换中文的 React UI。
- 无凭据、可复现且绝不触达券商的合成数据 Demo。

唯一 Python namespace 是 `omega_quant`，唯一 CLI 是 `omega-quant`。

## 实盘安全合同

服务启动时具备实盘姿态，但每次进程重启都会自动重新锁定。真正提交券商订单
必须同时满足：

1. 配置 `OMEGA_API_TOKEN`，请求头 `X-Omega-Token` 完全匹配。
2. `TIGEROPEN_ACCOUNT` 与 `OMEGA_EXPECTED_TIGER_ACCOUNT` 完全一致。
3. `OMEGA_LIVE_TRADING=1`。
4. `OMEGA_MAX_ORDER_NOTIONAL` 是正数。
5. 向当前进程提交正确账户后缀和明确的实盘风险确认。
6. 策略/订单风控全部通过，且 TigerOpen preview 批准。

任意一关失败都不会调用 `place_order`。API、日志和错误信息只展示脱敏账户
标识。自动化测试只使用模拟券商客户端，不以真实下单作为发布测试。

配置券商前请完整阅读[实盘安全手册](docs/live-trading-safety.md)。

## 示例

`examples/` 提供三套无需凭据且可复现的示例：

- `cta_ma_donchian.py`：MA/Donchian CTA。
- `portfolio_risk.py`：组合集中度与风险评估。
- `option_scenario_matrix.py`：期权价格与时间冲击情景矩阵。

## 验证与贡献

```bash
uv sync --extra dev
uv run pytest -q
cd frontend && npm ci && npm run build
```

欢迎提交可复现问题、文档、数据适配器和带测试的策略。参与前请阅读
[贡献指南](CONTRIBUTING.md)、[安全政策](SECURITY.md)和[行为准则](CODE_OF_CONDUCT.md)。

项目采用 Apache-2.0；修改后的 headless vn.py 子集保留 MIT 许可证和署名，
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
