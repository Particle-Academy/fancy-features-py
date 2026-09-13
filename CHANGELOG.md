# Changelog

All notable changes to `fancy-features` (Python) are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

This package is pre-1.0, so **breaking changes land in MINOR releases**. The
version number is not a promise it can yet keep; the entries are.

## [Unreleased]

### Fixed

- **`__version__` reported 0.1.0 from a 0.2.0 package.** A literal with nothing comparing it to `pyproject.toml`. It now reads the installed distribution metadata, and `test_version_metadata.py` fails if a literal comes back.
- **`src/fancy_features/__init__.py` failed `ruff format --check`** (one blank line, not two, before `_installed_version`). Whitespace only, no behaviour change, nothing to do. Unseen because the Tests workflow had never got past Install: `fancy-conformance` is in the `test` group and not on PyPI, and CI never checked it out. It does now.


## [0.2.0] - unreleased

Two owner rulings, applied to all three runtimes together (`laravel-fms` 0.11.0,
`@particle-academy/fancy-features` 0.5.0). Argument:
`.ai/plans/fancy-commerce-gating-rulings.md`.

### Changed

- **BREAKING: `can_access` answers ENTITLEMENT, not quota.** A resource grant
  from a `FeatureSource` whose quota is exhausted is now `True`.

  This package shipped the wart deliberately — a port is not the place to
  redesign semantics — and recorded it in its `AGENTS.md`. The owner has now
  ruled it a contract defect: the answer used to depend on where the feature
  happened to be defined, so `can_access("ai-tokens", user)` asked a different
  question for the same subject depending on which layer the plan was modelled
  in.

  **What to do:** move a consumption guard to `can_consume` (a read) or
  `try_consume` (the gate). `is_entitled` is a new explicit alias for the
  entitlement question.

- **`try_consume` enforces the ceiling, not `remaining`.** With no
  `overage_limit` configured the two are identical, so nothing changes unless
  you opt in.

### Added

- **`overage_limit` does something.** It was carried by all three runtimes and
  read by none. `fancy-catalog`'s
  `test_the_overage_limit_is_carried_but_not_yet_enforced` pinned that fact so
  somebody would notice the day it changed; this is that day, and that test is
  now the enforcement test rather than the pin.

  It is a **ceiling** on billable consumption past `included_quantity`. `None`
  or `0` means no overage — every configuration in existence has it unset, so
  reading `None` as "unbounded" would have made each one an unlimited spending
  authority.

- **Overage is permitted only where it can be RECORDED.** `OverageStore` is a
  new optional protocol (`get_overage` / `add_overage`), duck-typed exactly as
  `AtomicUsageStore` is, and `FeatureManager.on_overage(listener)` returns an
  unsubscribe. With neither, the ceiling stays at `included_quantity`.

  It fails closed on purpose: unbilled usage is the one failure here that cannot
  be repaired after the fact.

- **`is_entitled` / `ais_entitled`, `can_consume` / `acan_consume`,
  `overage_for` / `aoverage_for`, `on_overage`.**

- **`Feature.overage_limit`**, so a host with no catalog can express "1,000
  included, 200 billable" in config. MAX across definition, group override and
  source grant, like `limit`.

- **`fancy_features.quota`** — `entitled`, `consumption_ceiling`,
  `allows_consumption`, `overage_delta`, `can_consume` as pure functions, held
  to the shared `shared/feature-entitlement` conformance table alongside both
  twins. Cross-runtime behaviour belongs in a fixture row, not in three sets of
  prose that agree today.

- **`OverageEvent` / `OverageListener`** on the contract.

### Fixed

- **`remaining + used` was being used as the included quantity.** `remaining` is
  clamped at zero, so the moment a subject reached their limit the derived
  "limit" became whatever they had already spent — self-fulfilling, and it would
  have made every overage figure measure from the wrong line. `_limit_for()`
  resolves the limit directly, and returns a sentinel rather than overloading
  `None`, which already means unlimited.

  *No consumer action:* before this release nothing consumed past the limit, so
  the two only ever met at exactly the limit, where they agree.

## [0.1.0] - unreleased

The first cut: the whole resolution chain, groups, quotas, and the shared
contract this package owns.

### Added

- **`FeatureManager`** — the port of `laravel-fms`'s `FeatureManager`, extended
  with the `FeatureSource` link the Node twin introduced.
  - Resolution order: pre-strategies → gate → registry → groups (OR) → config →
    sources → deny. The first two links are authoritative in **both**
    directions; everything after them is additive.
  - Resource quota: pre-remaining strategies → `MAX(group, source, feature)`
    limit − usage, clamped at zero, `None` meaning unlimited.
  - `explain()` returns an `AccessResult` naming which link answered, with
    `remaining` / `limit` / `used` filled in for resource features.
  - **Every method exists twice** — `can_access` / `acan_access`, `remaining` /
    `aremaining`, and so on — driving **one** generator, so the ordering rules
    exist in exactly one place. The synchronous form *refuses* an awaitable
    rather than storing a coroutine that would read as a verdict.
- **The shared feature contract** (`fancy_features.contract`): `FeatureType`,
  `FeatureGrant`, `FeatureSource`, `UsageStore`, `AtomicUsageStore`,
  `GroupStore`, `BillingPeriod`, `AccessResult`, `Feature`, `FeatureGroup`.
  `fancy-catalog` **imports** these rather than mirroring them.
- **Feature groups** — `extends` one level deep with cycle detection, `overrides`
  merged leaf-first, limits taken as MAX across every enabled group, and both
  assignment paths (a `GroupStore` and a callable `enabled` gate) composing by OR.
- **Metering** — `increment` / `decrement` / `try_consume` / `usage_for` /
  `reset_period`, bucketed by `BillingPeriod`, with an in-memory default store
  and an optional atomic `try_consume` a database-backed store implements with a
  row lock.
- **`require_feature` / `can_access_any`** — the `RequireFeature` middleware's
  core with no web framework imported, OR semantics, failing closed on an empty
  key list.
- **Bound helpers** — `feature()`, `can_access_feature()`, `has_feature()`,
  `feature_remaining()`, `enabled_features()`, over an explicitly configured
  default instance.
- **Zero runtime dependencies**, as a constraint rather than an accident: this
  package sits in the request path of everything it protects.

### Fixed relative to the twins

These are not changes to this package — it has no history — but they are
behaviours where a peer is wrong today, and a consumer moving between runtimes
needs to know which.

- **A `usage` / `remaining` callback receives `(subject, context)`**, like every
  other callback. `laravel-fms` fixed this in 0.8.0 with a deprecating shim;
  **`@particle-academy/fancy-features` still publishes the `(key, subject,
  context)` order.** This package *refuses* a three-parameter callback with
  `FeatureCallbackSignatureError` rather than guessing, because calling it with
  two arguments binds `subject` to the feature-key string and keeps returning
  plausible numbers — which is how a metered allowance stops running out.

  *What a consumer must do:* if you are porting a callback from the Node
  package, drop the first parameter. You will get a loud error naming the
  feature and the field until you do.

- **The billing period reaches the usage store on every path.** In
  `@particle-academy/fancy-features`, `remaining()` and `canAccess()` read the
  period-**less** bucket while `increment()` writes the period one, and
  `tryConsume` derives its limit from one and enforces it against the other —
  an over-grant of `limit + used` in the period.

  *What a consumer must do:* nothing here. Pass `period=` if you meter per
  cycle; omit it and everything shares one bucket, as before.

### Known divergence, unresolved

- **`included_quantity=None`.** The shared contract spec and
  `@particle-academy/fancy-features` read it as **unlimited**; PHP's `Fms::can()`
  reads it as **deny**. Same nullable column, opposite meanings, two shipped
  packages. This port follows the written spec. Reported in
  `.ai/plans/fancy-python-commerce-gating.md`; whoever resolves it should change
  one of the other two, not this one.
