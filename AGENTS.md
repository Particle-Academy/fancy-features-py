# Using fancy-features (Python) — guide for agents

How an agent installs and uses this package in a consumer app. What the package
is, is in [README.md](./README.md). Notes for working ON this repo's code are in
[CONTRIBUTING.md](./CONTRIBUTING.md).

## Install

```bash
pip install fancy-features
```

- Python 3.11+. No runtime dependencies, no web framework, no ORM.
- The repo is `fancy-features-py`; the distribution is `fancy-features` and the
  import is `fancy_features`. Twins: PHP `particle-academy/laravel-fms`, Node
  `@particle-academy/fancy-features`.

## Find it on the Fancy MCP server

The catalog server is `https://ui.particle.academy/mcp` (streamable HTTP, no API
key; MCP id `fancy-ui`). It is listed as the registry entry `fancy-features-py`
(a package entry, not vendorable source). Find it with `search-backend-packages`
(stack `python`); `install-instructions` with `fancy-features-py` gives the
command.

## API essentials

```python
from fancy_features import create_features

features = create_features(
    features={
        "use-mcp": {"enabled": True},
        "ai-tokens": {"type": "resource", "limit": 10_000},
    },
)

features.can_access("use-mcp", user)          # entitlement
features.try_consume("ai-tokens", user, 40)   # atomic check-and-increment
features.remaining("ai-tokens", user)         # None = unlimited
features.explain("ai-tokens", user)           # AccessResult(...)
```

- **Async hosts:** every method has an `a`-prefixed twin (`acan_access`,
  `aremaining`, `atry_consume`). Both drive the same resolution rules.
- **Three questions:** `can_access` / `is_entitled` (granted?), `can_consume`
  (granted and this amount fits; a read), `try_consume` (the gate).
- **Adapters, all optional, sync or async:** `UsageStore` (default
  `InMemoryUsageStore`), `GroupStore` (default `InMemoryGroupStore`),
  `FeatureSource`s (e.g. `fancy_catalog.features.create_catalog_feature_source`),
  and a gate.
- **Billing periods:** pass `period=BillingPeriod(start=..., end=...)` to
  `try_consume`, `remaining`, `can_access`, `usage_for`; `reset_period(user, period)`
  is the renewal reset.
- **Guard:** `require_feature(features, keys, user)` (OR logic) raises
  `FeatureAccessDeniedError` with `.features` and `.status`.
- The shared contract types live in `fancy_features.contract`; `fancy-catalog`
  imports them, so there is one definition.
- Resolution: pre-strategies, gate, registry, groups (OR), config, sources,
  deny. Pre-strategies and the gate are authoritative; everything after is
  additive. Limits merge with MAX.

## Traps

- **Every callback takes `(subject, context)`**: `check`, `enabled`, `limit`,
  `usage`, `remaining`, and a group's `enabled` gate. A callback with exactly
  three positional parameters raises `FeatureCallbackSignatureError`. Fewer is
  fine (`lambda: 30`). Do not copy the Node package's `(key, subject, context)`
  shape.
- **Do not call `can_access()` with async adapters.** It refuses an awaitable;
  use the `a`-prefixed twin.
- **`can_access` is entitlement, not quota.** An exhausted metered feature is
  still entitled; gate consumption with `try_consume`.
- **A metered amount is a whole number.** Floats and `bool` are refused.
- `try_consume` refuses a negative amount; use `decrement` for refunds.
- `remaining()` returning `None` means unlimited, not zero.
- **Overage needs a recorder.** `overage_limit` is a ceiling past the included
  quantity and `None` means no overage. Overage is allowed only with a store
  implementing `add_overage` (`OverageStore`) or an `on_overage` listener.
- A feature defined with `enabled=False` does not block a group or source from
  turning it on; only pre-strategies and the gate can deny.
