# Contributing

Thank you for helping improve Omega Quant Platform.

## Before opening a change

- Search existing issues and keep each pull request focused.
- Never include broker credentials, API keys, account numbers, private market
  data, personal paths, or real portfolio screenshots.
- Do not test with a real order. Use fake TigerOpen clients and synthetic data.
- Add or update tests for behavior changes and document public contracts.

## Development

```bash
uv sync --extra dev
uv run pytest -q
cd frontend && npm ci && npm run build
```

Run `scripts/check_public_tree.sh .` before submitting. Commit messages should
explain the outcome, and pull requests should include verification evidence and
any safety impact.

## Design boundaries

- Trading actions go through `MainEngine`, `RiskManagerEngine`, and a gateway.
- Frontend state is never the source of truth for orders or positions.
- Vendor SDKs stay behind adapters/gateways.
- Stable API error codes are language-neutral; UI copy belongs in translations.
- Live execution remains fail-closed and process-local arming is never persisted.

By contributing, you agree that your contribution is licensed under Apache-2.0.
