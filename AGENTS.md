# AGENTS.md — fancy-features-py

Headless feature management and metered-resource gating. The Python twin of
`particle-academy/laravel-fms` and `@particle-academy/fancy-features`, and the
**owner of the shared feature contract** `fancy-catalog` consumes.
`CLAUDE.md` symlinks here.

This file describes **this package's code**. Process rules — publishing, kit
versioning, backports, the issue protocol — live in the envelope's `AGENTS.md`
and are deliberately not repeated.

## What this package is

A **port**, not a redesign. Behaviour questions are settled against the peers in
this order: `laravel-fms`'s `src/Services/FeatureManager.php` for the resolution
chain, `@particle-academy/fancy-features`'s `src/manager.ts` for the
`FeatureSource` link and the headless adapter shape.

Where this port deliberately differs from a peer, the difference is in a
docstring at the point of divergence AND in
[`.ai/plans/fancy-python-commerce-gating.md`](../../.ai/plans/fancy-python-commerce-gating.md).
There are four, listed below.

## Architecture

Pure core, `src/` layout, **zero runtime dependencies**.

- `contract.py` — **THE SHARED CONTRACT.** `FeatureType`, `FeatureGrant`,
  `FeatureSource`, `UsageStore`, `GroupStore`, `BillingPeriod`, `AccessResult`,
  `Feature`, `FeatureGroup`. This package owns them; `fancy_catalog.features`
  **imports** them. Do not add a second definition anywhere.
- `manager.py` — `FeatureManager`, the resolution chain.
- `registry.py` / `groups.py` — feature and group registries, `extends`
  resolution, `InMemoryGroupStore`.
- `usage.py` — `InMemoryUsageStore` and `whole_units`, the metered-amount guard.
- `guard.py` — `require_feature` / `can_access_any`, framework-free.
- `helpers.py` — the bound-default helpers (`feature()`, …).
- `_drive.py` — internal: the sync/async drivers and the callback-arity adapter.

### The chain is one generator, driven two ways

Every adapter may be sync or async — a `UsageStore` backed by a dict returns an
`int`, one backed by a database returns a coroutine. The Node twin made its
whole surface `async`, which Python cannot copy: a Django view cannot `await`.
Shipping two copies of the chain would mean two copies of the ordering rules, of
which only one is ever really tested.

So the chain is written **once, as a generator**. It `yield`s anything that might
be awaitable and is sent the resolved value back. `drive_sync` refuses
awaitables; `adrive` awaits them. `can_access()` and `acan_access()` drive the
same generator.

**Add behaviour to the generators in `manager.py`, never to a public wrapper.**
The wrappers are two lines each and must stay that way.

`can_access()` **refuses** an awaitable rather than storing it. A coroutine
object is truthy; treating it as a verdict would report a feature as enabled
with the check never having run.

## The invariants

**Every feature-definition callback receives `(subject, context)`.** `check`,
`enabled`, `limit`, `usage`, `remaining`, and a group's `enabled` gate. There is
no exception and there never was one in any documentation.

`laravel-fms` invoked `usage` and `remaining` as `($feature, $user, $context)`
before 0.8.0 — key first — so anyone following the docs bound the user to a
string and metered nothing. GuardCard.net reported it: the allowance never ran
out. **`fancy-features-js` still ships that order**, in its published types.

So a callback declaring exactly **three** positional parameters is **refused**
with `FeatureCallbackSignatureError`, not honoured-with-a-deprecation as PHP
does. PHP had users on the old order to carry; this package has none, and the
only way to arrive at three parameters here is by porting the twin's bug.
Fewer parameters is fine and adapted for — `lambda: 30` is the commonest form,
and Python, unlike PHP, raises on surplus arguments.

`tests/test_callback_signature.py` is the first test file in the package for
this reason. Do not weaken it.

**A metered amount is a whole number.** `whole_units()` refuses floats and
refuses `bool` (which is an `int` in Python and would silently meter 1). A quota
is counted, not measured.

**The billing period reaches the store on every quota path.** `remaining`,
`can_access`, `try_consume`, `usage_for` all take `period=` and all pass it
through. In `fancy-features-js` it does not: `resourceUsage` calls
`getUsage(subject, feature)` with no period, so `remaining()` reads a different
bucket from `increment()`, and `tryConsume` derives a limit from one bucket and
enforces it against the other. That is a live over-grant in the twin, not a
style choice this port declined to copy.

**`try_consume` refuses a negative amount.** A negative consume is a refund in
disguise and walks straight past the limit it is meant to enforce. `decrement`
exists for that.

**Registry outranks config; everything after the gate is additive.** A registry
or config feature with `enabled=False` does **not** block a group or a source
from turning it on. Pre-strategies and the gate are the only authoritative
links, in both directions.

## Deliberate divergences from the peers

1. **A three-parameter callback raises instead of deprecating.** Above.
2. **The billing period is threaded through resolution.** Above.
3. **`included_quantity=None` means unlimited.** The written contract
   (`.ai/plans/fancy-catalog-features-contract.md` §2) and `fancy-features-js`
   both say so. PHP's `Fms::can()` reads the same nullable column as **deny**.
   The spec settles it; the divergence is reported, not absorbed.
4. **No mirrored contract in `fancy-catalog`.** The TypeScript pair duplicates
   `FeatureGrant`/`FeatureSource` verbatim and lets structural typing police the
   copy. Python has no equivalent across distributions, so there is one
   definition and the bridge imports it.

## Known wart, faithfully reproduced

**`can_access` on a resource feature means different things depending on where
the feature came from.** A registry or config resource feature is on if its
`enabled`/`check` says so, *regardless of remaining quota*; a resource feature
arriving from a `FeatureSource` is on only while quota remains. Both twins do
this and this port matches them. It is inconsistent and it is a port, not a
redesign — flagged in the plan rather than fixed unilaterally.

## Testing

```bash
python -m pytest        # 110 tests, no install required
ruff check . && ruff format --check .
mypy
```

The suite runs on a bare checkout via `pythonpath = ["src"]`. **CI also installs
the wheel and runs against it** — only that catches an unshipped file or a
missing `py.typed`.

Every load-bearing behaviour here has been mutation-checked: reproducing the
callback bug, dropping the period, taking MIN instead of MAX for a group limit,
making the gate non-authoritative, and believing an awaitable each fail a named
test. If you change one of those, expect a specific test to go red — and if none
does, the test is the thing that is wrong.
