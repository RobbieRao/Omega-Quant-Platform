#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${OMEGA_VERIFY_PORT:-18770}"
BASE_URL="http://127.0.0.1:${PORT}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/omega-verify.XXXXXX")"
DB_PATH="${TMP_DIR}/omega_verify.sqlite"
SERVER_LOG="${TMP_DIR}/uvicorn.log"
FRONTEND_LOG="${TMP_DIR}/vite.log"
STREAM_PATH="${TMP_DIR}/live_stream.json"
SERVER_PID=""
FRONTEND_PID=""

cleanup() {
  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" >/dev/null 2>&1; then
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
    wait "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  if [[ -f "${SERVER_LOG}" ]]; then
    echo "--- uvicorn log ---" >&2
    tail -80 "${SERVER_LOG}" >&2 || true
  fi
  if [[ -f "${FRONTEND_LOG}" ]]; then
    echo "--- frontend log ---" >&2
    tail -80 "${FRONTEND_LOG}" >&2 || true
  fi
  exit 1
}

pass() {
  echo "PASS: $*"
}

warn() {
  echo "WARNING: $*" >&2
}

post_json() {
  local path="$1"
  local payload="$2"
  curl -fsS -X POST "${BASE_URL}${path}" \
    -H "Content-Type: application/json" \
    -d "${payload}"
}

get_json() {
  local path="$1"
  curl -fsS "${BASE_URL}${path}"
}

assert_json() {
  local json="$1"
  local code="$2"
  OMEGA_JSON="${json}" "${PYTHON_BIN}" - <<PY
import json
import os
import sys

data = json.loads(os.environ["OMEGA_JSON"])
${code}
PY
}

cd "${ROOT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"
export OMEGA_DATABASE_URL="sqlite:///${DB_PATH}"
export OMEGA_REAL_READONLY_OPTION_GREEKS="0"
export OMEGA_REAL_READONLY_STREAM_PATH="${STREAM_PATH}"
export OMEGA_STREAM_CACHE_PATH="${STREAM_PATH}"
export OMEGA_LIVE_READONLY_MAX_AGE_SECONDS="${OMEGA_LIVE_READONLY_MAX_AGE_SECONDS:-999999999}"
export TIGEROPEN_ACCOUNT="${OMEGA_VERIFY_TIGEROPEN_ACCOUNT:-TEST-SIM-ACCOUNT-0001}"
export OMEGA_TIGER_ACCOUNT_KIND="${OMEGA_TIGER_ACCOUNT_KIND:-simulated}"

cat >"${STREAM_PATH}" <<'JSON'
{
  "status": "live",
  "connected": true,
  "freshness": "live",
  "lastEventAt": "2026-05-24T00:00:00+00:00",
  "lastEventAtMs": 1779580800000,
  "sequence": 1,
  "subscriptions": {"quotes": ["AAPL"], "stocks": ["AAPL"], "options": []},
  "quotes": {
    "AAPL": {
      "symbol": "AAPL",
      "latest_price": 140.0,
      "price": 140.0,
      "volume": 5000000,
      "received_at": "2026-05-24T00:00:00+00:00",
      "received_at_ms": 1779580800000,
      "data_quality": "verify_live_readonly_cache"
    }
  },
  "bbo": {
    "AAPL": {
      "symbol": "AAPL",
      "bid_price": 139.95,
      "ask_price": 140.05,
      "mid_price": 140.0,
      "received_at": "2026-05-24T00:00:00+00:00",
      "received_at_ms": 1779580800000
    }
  },
  "ticks": {},
  "assets": {},
  "positions": {},
  "orders": {},
  "transactions": {},
  "events": [{"type": "massive_quote", "symbol": "AAPL", "at": "2026-05-24T00:00:00+00:00"}],
  "marketData": {
    "provider": "massive",
    "label": "Massive readonly",
    "status": "realtime",
    "connected": true,
    "permission": "readonly",
    "subscribeQuotes": true,
    "subscribeTrades": false,
    "viewerCount": 1,
    "pausedByNoViewers": false,
    "rateLimits": {"channels": {}}
  }
}
JSON

"${PYTHON_BIN}" -m uvicorn omega_quant.api:app --host 127.0.0.1 --port "${PORT}" >"${SERVER_LOG}" 2>&1 &
SERVER_PID="$!"

for _ in $(seq 1 80); do
  if curl -fsS "${BASE_URL}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "${BASE_URL}/api/health" >/dev/null 2>&1 || fail "Omega API did not start on ${BASE_URL}"

health="$(get_json /api/health)"
assert_json "${health}" '
assert data["mode"] == "live", data
assert data["live_trading"] == "unlocked", data
'
pass "/api/health returns live/unlocked"

execution_control="$(get_json /api/execution/control)"
assert_json "${execution_control}" '
assert data["control_plane"] == "omega_execution_control", data
assert data["effective_execution_mode"] == "live", data
assert data["paper_engine_enabled"] is False, data
assert data["live_gateway_status"] == "unlocked", data
assert data["real_account_mode"] == "tigeropen_configured_account", data
assert data["market_data_mode"] == "live_readonly", data
assert data["broker_execution_gateway"] == "tigeropen_sdk", data
assert data["live_readiness"]["status"] == "ready", data
assert data["live_readiness"]["broker_execution_available"] is True, data
assert data["centralized_switches"]["effective_execution_mode"] == "live", data
assert data["target_account"]["configured"] is True, data
assert data["target_account"]["account_kind"] == "simulated", data
assert data["target_account"]["account_masked"].startswith("TES"), data
assert data["target_account"]["account_masked"].endswith("0001"), data
assert "SIM-ACCOUNT" not in data["target_account"]["account_masked"], data
assert data["target_account"]["sdk"] == "tigerfintech/openapi-python-sdk", data
'
legacy_live="$(post_json /api/execution/control '{"requested_execution_mode":"live_gateway_armed"}')"
assert_json "${legacy_live}" '
assert data["effective_execution_mode"] == "live", data
assert data["live_gateway_status"] == "unlocked", data
'
paper_on="$(post_json /api/execution/control '{"paper_engine_enabled":true}')"
assert_json "${paper_on}" '
assert data["effective_execution_mode"] == "live", data
assert data["paper_engine_enabled"] is False, data
'
pass "execution control is live/unlocked and normalizes old paper/lock switches"

gateways="$(get_json /api/gateways)"
assert_json "${gateways}" '
by_name = {item["gateway_name"]: item for item in data}
assert set(by_name) >= {"TIGEROPEN", "MASSIVE"}, by_name
assert by_name["TIGEROPEN"]["capabilities"] == ["account", "contract", "market_data", "trading"], by_name
assert by_name["MASSIVE"]["capabilities"] == ["market_data"], by_name
'
pass "MainEngine exposes TigerOpen and Massive gateways with vn.py-style capabilities"

mode="$(get_json /api/account/mode)"
assert_json "${mode}" '
assert data["execution_mode"] == "live", data
assert data["live_trading"] == "enabled", data
assert data["real_broker_execution"] == "tigeropen_sdk", data
'
pass "account mode reports TigerOpen live execution context"

data_mode="$(get_json /api/data-mode)"
assert_json "${data_mode}" '
assert data["data_mode"] == "live_readonly", data
assert data["available_modes"] == ["live_readonly"], data
assert "mock" not in data["available_modes"], data
assert "replay" not in data["available_modes"], data
assert data["execution_mode"] == "live", data
assert data["live_trading"] == "enabled", data
assert "live_readonly_status" in data, data
'
live_status="$(get_json /api/market/live/status)"
assert_json "${live_status}" '
assert data["read_only"] is True, data
assert isinstance(data["subscribed_symbols"], list), data
assert data["subscribe_quotes"] is True, data
assert data["subscribe_trades"] is False, data
'
channel_status="$(post_json /api/market/live/channels '{"subscribe_quotes":true,"subscribe_trades":true}')"
assert_json "${channel_status}" '
assert data["read_only"] is True, data
assert data["subscribe_quotes"] is True, data
assert data["subscribe_trades"] is True, data
'
post_json /api/market/live/channels '{"subscribe_quotes":true,"subscribe_trades":false}' >/dev/null
post_json /api/market/live/subscribe '{"symbols":["AAPL"]}' >/dev/null
post_json /api/data-mode '{"data_mode":"live_readonly"}' >/dev/null
live_snapshot="$(get_json '/api/market/snapshot?symbol=AAPL')"
assert_json "${live_snapshot}" '
assert data["source"] == "live_readonly", data
assert data["symbol"] == "AAPL", data
assert data["price"] > 0, data
assert data["data_quality"], data
'
pass "data mode and live readonly market endpoints are live TigerOpen data controls"

rate_limits="$(get_json /api/market/rate-limits)"
assert_json "${rate_limits}" '
channels = data["channels"]
assert channels["rest"]["limitRpm"] == 500, data
assert channels["websocket_stocks"]["limitRpm"] == 1000, data
assert channels["websocket_options"]["limitRpm"] == 50, data
'
pass "data mode and rate limit endpoints are available"

real_summary="$(get_json /api/tiger/account/summary)"
real_positions="$(get_json /api/tiger/account/positions)"
real_position_groups="$(get_json /api/tiger/account/position-groups)"
real_orders="$(get_json /api/tiger/account/orders)"
real_stream_status="$(get_json /api/tiger/account/stream-status)"
real_stream_detail="$(get_json /api/tiger/account/stream-detail)"
real_risk="$(get_json /api/tiger/account/risk)"
assert_json "${real_summary}" '
assert data["account_view"] == "real_readonly", data
assert data["execution_mode"] == "live", data
assert data["live_trading"] == "enabled", data
assert data["read_only"] is True, data
assert "TigerOpen" in data["message"] or "暂不可用" in data["message"], data
'
assert_json "${real_positions}" 'assert isinstance(data, list), data'
assert_json "${real_position_groups}" 'assert isinstance(data, list), data'
assert_json "${real_orders}" 'assert isinstance(data, list), data'
assert_json "${real_stream_status}" '
assert data["read_only"] is True, data
assert isinstance(data["counts"], dict), data
assert "orders" in data["counts"], data
assert "transactions" in data["counts"], data
'
assert_json "${real_stream_detail}" '
assert data["read_only"] is True, data
assert isinstance(data["quote_groups"], list), data
assert isinstance(data["recent_events"], list), data
'
assert_json "${real_risk}" '
assert "read_only" in data, data
assert "flags" in data, data
assert "positions" in data, data
assert "option_greeks_exposure" in data, data
'
pass "tiger account readonly endpoints are available and read-only"

# Register a CTA strategy via monitor-groups endpoint and verify the vnpy lifecycle
mg_cta_group="$(
  post_json /api/monitor-groups '{
    "name": "verify CTA group",
    "description": "Created by verify script.",
    "allocated_capital": 8000
  }'
)"
mg_cta_group_id="$(assert_json "${mg_cta_group}" 'print(data["id"])')"
registered_cta="$(post_json "/api/monitor-groups/${mg_cta_group_id}/cta-strategy" '{"class_name":"TickMomentumStrategy","symbol":"AAPL","start":false}')"
assert_json "${registered_cta}" '
assert data["class_name"] == "TickMomentumStrategy", data
assert data["vt_symbol"] == "AAPL.SMART", data
assert data["gateway_name"] == "TIGEROPEN", data
assert data["trading"] is False, data
assert data["monitor_group_id"] is not None, data
'
pass "monitor group cta-strategy endpoint registers TickMomentumStrategy via CTA/MainEngine lifecycle"

if rg -n "execute_action|create_order|preview_order|place_order|submit_order|cancel_order|modify_order" src/omega_quant/adapters/live_market_readonly.py >/tmp/omega_verify_live_grep.txt 2>&1; then
  cat /tmp/omega_verify_live_grep.txt >&2
  fail "live readonly market adapter contains real execution call names"
fi
"${PYTHON_BIN}" - <<'PY'
from omega_quant.adapters.live_market_readonly import LiveMarketReadonlyAdapter

adapter = LiveMarketReadonlyAdapter()
status = adapter.subscribe([" aapl ", "MU 260618C01070000", "O:MU260618C01070000"])
assert status["subscribed_symbols"] == ["AAPL", "O:MU260618C01070000"], status
PY
pass "live readonly market adapter contains no real execution calls and canonicalizes option subscriptions"

if rg -n \
    --glob "!src/omega_quant/broker/**" \
    --glob "!src/omega_quant/gateways/**" \
    --glob "!src/omega_quant/core/**" \
    --glob "!src/omega_quant/vnpy_core/**" \
    --glob "!src/omega_quant/schemas.py" \
    "execute_action|create_order|preview_order|place_order|submit_order|cancel_order|modify_order" \
    src/omega_quant frontend >/tmp/omega_verify_grep.txt 2>&1; then
  cat /tmp/omega_verify_grep.txt >&2
  fail "Omega source outside broker/core/gateways contains real order submission call names"
fi
pass "Omega source outside broker/core/gateways contains no real order submission calls"

# === Section B：实盘路径冒烟（仅在 OMEGA_VERIFY_LIVE_PATH=1 时启用）===
if [[ "${OMEGA_VERIFY_LIVE_PATH:-0}" == "1" ]]; then
  live_group_status="$(
    curl -sS -o /dev/null -w "%{http_code}" -X POST "${BASE_URL}/api/monitor-groups" \
      -H "Content-Type: application/json" \
      -d '{"name":"live verify group","allocated_capital":1000}'
  )"
  [[ "${live_group_status}" == "200" ]] || fail "creating live group returned ${live_group_status}, expected 200"
  pass "live monitor group creation is enabled"

  broker_status="$(get_json /api/broker/stream/status)"
  assert_json "${broker_status}" '
assert data["armed"] is True, data
assert data["tiger_submission_unlocked"] is True, data
assert data["execution_mode"] == "live", data
'
  pass "broker stream status shows armed and tiger submission unlocked"
else
  echo "Section B (OMEGA_VERIFY_LIVE_PATH live path smoke) skipped; set OMEGA_VERIFY_LIVE_PATH=1 to enable."
fi

if [[ "${OMEGA_SKIP_FRONTEND_SMOKE:-0}" == "1" ]]; then
  warn "frontend smoke test skipped by OMEGA_SKIP_FRONTEND_SMOKE=1"
elif ! command -v node >/dev/null 2>&1; then
  warn "node is unavailable; skipping optional frontend smoke test"
elif ! node scripts/smoke_omega_frontend.mjs --check-playwright >/dev/null 2>&1; then
  warn "Playwright is unavailable; skipping optional frontend smoke test"
elif ! command -v npm >/dev/null 2>&1; then
  warn "npm is unavailable; skipping optional frontend smoke test"
elif [[ ! -d frontend/node_modules ]]; then
  warn "frontend/node_modules is missing; skipping optional frontend smoke test"
else
  FRONTEND_PORT="${OMEGA_VERIFY_FRONTEND_PORT:-15173}"
  FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
  (
    cd frontend
    OMEGA_API_PROXY_TARGET="${BASE_URL}" npm run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}" --strictPort
  ) >"${FRONTEND_LOG}" 2>&1 &
  FRONTEND_PID="$!"

  for _ in $(seq 1 100); do
    if curl -fsS "${FRONTEND_URL}/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
  curl -fsS "${FRONTEND_URL}/" >/dev/null 2>&1 || fail "Omega frontend did not start on ${FRONTEND_URL}"

  OMEGA_FRONTEND_URL="${FRONTEND_URL}" node scripts/smoke_omega_frontend.mjs
  pass "frontend smoke test completed"
fi

echo "Omega live TigerOpen verification passed."
