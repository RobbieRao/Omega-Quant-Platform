# Third-party notices

Omega Quant Platform is licensed under Apache-2.0. Runtime dependencies keep
their own licenses; consult the lockfiles and installed distributions for the
exact version-specific texts.

## Vendored source

| Component | Location | License | Use and modifications |
| --- | --- | --- | --- |
| vn.py | `src/omega_quant/vnpy_core/` | MIT | Headless event/trader core, pruned to the US equities/options scope; Qt UI and domestic gateways are not included. |

The complete vn.py MIT text is retained in
`src/omega_quant/vnpy_core/LICENSE`. Omega's adapters, APIs, risk controls,
research modules, and React UI are project code and are not represented as
upstream vn.py features.

## External integrations

TigerOpen, Massive, LEAN, vectorbt, PyPortfolioOpt, Riskfolio-Lib, FastAPI,
React, and other named projects are optional integrations or dependencies.
Their names and trademarks remain the property of their respective owners.
No vendor endorses Omega Quant Platform.

Notable optional transitive/runtime license conditions discovered by the
release audit:

| Dependency | Relationship | Reported upstream license |
| --- | --- | --- |
| vectorbt 1.x | Optional `research` dependency; not vendored | Apache-2.0 with Commons Clause; commercial resale restrictions apply to vectorbt itself |
| DEAP / moocore | Optional research/risk dependency chain; not vendored | LGPL |
| Peewee | Optional data dependency chain; not vendored | MIT |

These packages are installed from their upstream distributions and are not
relicensed by Omega's Apache-2.0 license. Consumers should review the precise
version's license before redistribution or commercial deployment.
