# Project instructions

## Positioning

Omega is a single open-source US-stock/option research and execution platform.
The active application is `src/omega_quant` plus `frontend`; active code must
not import retired packages.

## Run and verify

- Python 3.12; install with `uv pip install ".[all]"`.
- Backend: `.venv/bin/uvicorn omega_quant.api:app --host 127.0.0.1 --port 8770`.
- Frontend: `cd frontend && npm run dev` (port 5173).
- Tests: `.venv/bin/python -m pytest -q`.
- Frontend gate: `cd frontend && npm run build`.
- Follow the user-level RTK rule for shell commands.

## Safety

- Current Omega execution is live-capable, but every process starts locked and
  requires API token, expected-account, notional, suffix and risk confirmation.
- Never call order, cancel, CTA-start, algo-start, spread-order, or live-smoke
  endpoints unless the user explicitly authorizes broker-side actions.
- The configured TigerOpen account is the execution target. Never print or
  commit full account IDs, private keys, API keys, or tokens.
- Bind locally and set `OMEGA_API_TOKEN` when running the API for interactive use.
- All execution must remain `MainEngine -> RiskManagerEngine -> TigerOpenGateway`.

## Structure and conventions

- `src/omega_quant/core`: event-driven engines and domain objects.
- `src/omega_quant/gateways`: external trading/market capabilities.
- `src/omega_quant/adapters`: readonly integration seams.
- `src/omega_quant/broker`: Tiger execution plumbing only.
- `frontend/src`: React pages/components; the backend/OMS is state authority.
- `data/omega_quant` and most of `reports/` are local runtime state and ignored.
- Preserve the vendored vn.py license and do not import Qt/domestic gateways.
- Use `apply_patch` for edits and preserve unrelated dirty-worktree changes.

## Knowledge entry points

README, `docs/architecture.md`, `docs/configuration.md`, and
`docs/live-trading-safety.md` define the public contracts.
