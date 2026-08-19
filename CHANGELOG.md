# Changelog

All notable changes to `fancy-features` (Python) are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

This package is pre-1.0, so **breaking changes land in MINOR releases**. The
version number is not a promise it can yet keep; the entries are.

## [Unreleased]

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
