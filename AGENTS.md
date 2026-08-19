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
   both say so. PHP's `Fms::can()` read the same nullable column as **deny**
   until `laravel-fms` 0.10.0, which fixed it. No longer a divergence; kept
   here because the reasoning is what settles the next one.
4. **No mirrored contract in `fancy-catalog`.** The TypeScript pair duplicates
   `FeatureGrant`/`FeatureSource` verbatim and lets structural typing police the
   copy. Python has no equivalent across distributions, so there is one
   definition and the bridge imports it.

## Entitlement is not quota

**`can_access` answers ENTITLEMENT, on every branch.** Until 0.2.0 it did not:
a `FeatureSource` resource grant was on only while quota remained, while the
same feature defined in the registry was on regardless. Both twins did it, this
port reproduced it faithfully, and the owner ruled it a contract defect rather
than a divergence. All three runtimes changed together.

- `can_access` / `is_entitled` — is this granted.
- `can_consume` — granted AND this amount fits. A **read**; another request can
  take the last unit before the write.
- `try_consume` — the gate.

Do not re-merge them. `quota.entitled()` takes `included_quantity` and `used`
and is required to **ignore** them; conformance rows `0002` and `0004` fail if
it stops, and nothing else does.

## Billable overage

`overage_limit` is a **ceiling** on consumption past the included quantity, and
**`None` means no overage**. That reading is load-bearing: the field was carried
by three runtimes and read by none, so every configuration in existence has it
unset, and "unbounded" would make each one an unlimited spending authority.

**Overage is permitted only when it can be RECORDED** — a store implementing
`add_overage` (`OverageStore`), or an `on_overage` listener. A host with
neither keeps the old behaviour. That is the opt-in mechanism and it fails
closed on purpose: unbilled usage is the one failure that cannot be repaired
after the fact.

`quota.overage_delta()` is **signed**, so increment and decrement share it. Do
not split it into two functions — that is how the two directions drift.

**`remaining + used` is NOT the included quantity.** `remaining` is clamped at
zero, so once a subject is in overage that sum reports the limit as whatever
they have already spent, and every overage figure downstream then measures from
the wrong line. `_limit_for()` resolves it directly and returns `_UNRESOLVED`
rather than overloading `None`, which already means unlimited.

## Testing

```bash
python -m pytest        # 129 tests, no install required
ruff check . && ruff format --check .
mypy
```

The suite runs on a bare checkout via `pythonpath = ["src"]`. **CI also installs
the wheel and runs against it** — only that catches an unshipped file or a
missing `py.typed`.

The `shared/feature-entitlement` conformance rows run from the sibling checkout
through a path dependency; `PINNED_SUITE_VERSION` is moved deliberately, never
to match whatever is on disk.

Every load-bearing behaviour here has been mutation-checked: reproducing the
callback bug, dropping the period, taking MIN instead of MAX for a group limit,
making the gate non-authoritative, believing an awaitable, putting the quota
check back into `entitled` (fails exactly conformance rows 0002 and 0004),
writing `overage_delta` as `max(0, after - included)`, and ignoring
`overage_limit` each fail a named test. If you change one of those, expect a specific test to go red — and if none
does, the test is the thing that is wrong.
