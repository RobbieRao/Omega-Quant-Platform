# Initial Community Issues

Create these only after the public repository exists.

1. **[good first issue] Add an RSI example strategy** — follow the deterministic
   example conventions and include a regression test.
2. **[good first issue] Improve CLI doctor diagnostics** — add actionable checks
   without printing credentials or full account identifiers.
3. **[documentation] Add a second architecture diagram** — document task/SSE and
   browser interactions in English and Chinese.
4. **[testing] Expand option scenario property tests** — cover monotonicity and
   expiry edge cases using a fixed clock.
5. **[performance] Split the React production bundle** — lazy-load the strategy
   editor and charting dependencies while preserving smoke coverage.
6. **[integration] Design a broker-neutral paper gateway** — research proposal
   only; it must preserve `MainEngine -> RiskManagerEngine -> gateway` routing.

Maintainers should triage weekly and target a small beta release every two weeks.
Do not label an issue `good first issue` unless it is bounded and has acceptance
criteria.
