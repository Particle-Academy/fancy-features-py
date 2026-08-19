# fancy-features

**Headless feature management and metered-resource gating for Python.** Feature
flags, feature groups, per-plan quotas and billing-period usage — with no web
framework, no ORM and no runtime dependencies at all.

The Python twin of [`particle-academy/laravel-fms`](https://github.com/Particle-Academy/laravel-fms)
(PHP) and [`@particle-academy/fancy-features`](https://github.com/Particle-Academy/fancy-features-js)
(Node/TypeScript), and the owner of the shared feature contract
[`fancy-catalog`](https://github.com/Particle-Academy/fancy-catalog-py) consumes.

```bash
pip install fancy-features
```

## In one minute

```python
from fancy_features import create_features

features = create_features(
    features={
        "use-mcp": {"enabled": True},
        "ai-tokens": {"type": "resource", "limit": 10_000},
    },
    groups=[
        {"key": "pro", "features": ["sso"], "overrides": {"ai-tokens": {"limit": 250_000}}},
    ],
)

features.can_access("use-mcp", user)        # True
features.remaining("ai-tokens", user)       # 10000
features.try_consume("ai-tokens", user, 40) # True  -- atomic check-and-increment
features.remaining("ai-tokens", user)       # 9960
features.explain("ai-tokens", user)         # AccessResult(source="config", used=40, ...)
```

Every method has an `a`-prefixed twin for async hosts — `acan_access`,
`aremaining`, `atry_consume` — and both drive **one** copy of the resolution
rules, so they cannot disagree.

## How a verdict is reached

```
pre-strategies → gate → registry → groups (OR) → config → sources → deny
```

* **pre-strategies** and **gate** are *authoritative*: a non-`None` answer from
  either is final in both directions. That is what lets a billing service deny
  something a stray permission would allow.
* everything after them is *additive*: a feature defined with `enabled=False`
  does not block a group or a plan from turning it on.

For a resource feature:

```
pre-remaining strategies → MAX(group limit, source limit, feature limit) − usage
```

clamped at zero, with `None` meaning unlimited. **MAX**, because a plan should
be able to lift a base limit and never to lower one.

## Adapters, all optional

| Contract | Default | What a host plugs in |
|---|---|---|
| `UsageStore` | `InMemoryUsageStore` | its `feature_usages` table |
| `GroupStore` | `InMemoryGroupStore` | its group-assignment table |
| `FeatureSource` | none | `fancy-catalog`, or its own entitlement service |
| gate | none | its permission system |

Every one may be synchronous or asynchronous. A dict-backed store returns an
`int`; a database-backed one returns a coroutine; both work.

## Billing periods

```python
from datetime import datetime, UTC
from fancy_features import BillingPeriod

january = BillingPeriod(start=datetime(2026, 1, 1, tzinfo=UTC),
                        end=datetime(2026, 2, 1, tzinfo=UTC))

features.try_consume("ai-tokens", user, 500, period=january)
features.remaining("ai-tokens", user, period=january)
features.reset_period(user, january)      # the renewal reset
```

The period reaches the store on **every** quota path — reads and writes alike.

## Composing with a Stripe catalog

```python
from fancy_catalog import create_catalog
from fancy_catalog.features import create_catalog_feature_source
from fancy_features import create_features

catalog = create_catalog(stripe=stripe_client)
features = create_features(
    sources=[create_catalog_feature_source(catalog, resolve_subscription=lookup)],
)

features.can_access("use-mcp", user)     # resolved through the user's plan
features.remaining("ai-tokens", user)    # the plan's included quantity, minus usage
```

The two packages share **one** definition of the contract, in
`fancy_features.contract`. The catalog imports it; there is no mirrored copy.

## Guarding a route

```python
from fancy_features import FeatureAccessDeniedError, require_feature

try:
    require_feature(features, ["use-mcp", "use-agents"], user)   # OR
except FeatureAccessDeniedError as denied:
    return json_response({"features": denied.features}, status=denied.status)
```

No framework is imported and none is assumed.

## One thing that will bite a porter

**Every callback takes `(subject, context)`** — `check`, `enabled`, `limit`,
`usage`, `remaining`. The Node package still publishes `usage`/`remaining` as
`(key, subject, context)`. A three-parameter callback here **raises**, naming
the feature and the field, rather than binding `subject` to the key string and
quietly metering the wrong thing. Fewer parameters is fine: `lambda: 30` works.

## Requirements

Python 3.11+. No runtime dependencies.

## License

MIT © Particle Academy
