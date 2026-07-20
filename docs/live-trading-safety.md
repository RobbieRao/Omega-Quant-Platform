# Live trading safety

Omega can route real orders. It intentionally does not provide a persistent
"remember my choice" switch.

## Required gates

Before `place_order`, the same process must have a configured API token, exact
expected-account match, live environment switch, positive notional limit,
correct account suffix, explicit risk confirmation, an armed in-memory
controller, an approved risk result, and an approved TigerOpen preview.

The controller resets on restart. Preview mode, a rejected risk check, stale or
invalid market data, a mismatched account, an invalid token, or a notional above
the limit prevents broker submission.

## Operator checklist

1. Start with `omega-quant doctor` and verify only the masked target account.
2. Bind the API to localhost or a trusted authenticated network.
3. Set a conservative `OMEGA_MAX_ORDER_NOTIONAL`.
4. Start the service and verify it reports `locked`.
5. Enter the API token in the UI, compare the masked account, enter at least the
   last four account characters, read the warning, then arm the process.
6. Submit the smallest intended order only after independent broker-side review.
7. Disarm immediately after the execution window.

## Testing policy

CI and release verification use fake TigerOpen clients. They assert that an
unarmed request performs no broker method call, authentication/account/risk
failures do not place, a fully armed fake path previews and places exactly once,
and a new controller is locked. Real-order verification remains experimental
and requires separate explicit authorization outside the release process.
