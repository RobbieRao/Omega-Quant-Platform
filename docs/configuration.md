# Configuration reference

Copy `config/omega.local.secrets.env.example` to the ignored
`config/omega.local.secrets.env`, or use process environment variables. Never commit
the populated file.

## Omega settings

| Variable | Purpose | Safe default |
| --- | --- | --- |
| `OMEGA_DATABASE_URL` | SQLAlchemy database URL | Local SQLite |
| `OMEGA_API_HOST` / `OMEGA_API_PORT` | Bind address and port | `127.0.0.1:8770` |
| `OMEGA_API_TOKEN` | Required header token for mutations | unset / writes refused |
| `OMEGA_DEMO_MODE` | Synthetic, credential-free operation | `0` |
| `OMEGA_SERVE_UI` | Serve prebuilt `frontend/dist` | `0` |
| `OMEGA_LIVE_TRADING` | Enable live posture requirement | `0` |
| `OMEGA_EXPECTED_TIGER_ACCOUNT` | Exact account allowlist | unset |
| `OMEGA_MAX_ORDER_NOTIONAL` | Maximum estimated per-order notional | unset |
| `OMEGA_TIGER_PUSH_ENABLED` | Subscribe to TigerOpen account push | `0` |
| `OMEGA_MARKET_DATA_ENABLED` | Enable provider market data | `1` |
| `OMEGA_MASSIVE_REST_HOST` | Explicit REST override | `https://api.massive.com` |
| `OMEGA_MASSIVE_WS_HOST` | Explicit WebSocket override | `socket.massive.com` |
| `OMEGA_STREAM_CACHE_PATH` | Optional read-only stream cache | project report path |

## Vendor settings

`TIGEROPEN_ACCOUNT`, `TIGEROPEN_TIGER_ID`, `TIGEROPEN_PRIVATE_KEY` or
`TIGEROPEN_PRIVATE_KEY_PATH` are read by the TigerOpen integration.
`MASSIVE_API_KEY` is read by the Massive integration. Vendor names remain
unchanged so existing official SDK configuration is recognizable.

Run `omega-quant doctor` and `omega-quant data-check`; both reports mask
identifiers and reveal only whether keys are configured.
