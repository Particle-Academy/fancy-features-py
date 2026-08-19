"""THE SHARED FEATURE CONTRACT -- single source of truth, for Python.

``fancy-features`` **owns** these types. ``fancy-catalog``'s ``fancy_catalog.features``
bridge **imports them from here**; it does not mirror them.

That is a deliberate divergence from the TypeScript pair, where
``@particle-academy/fancy-catalog/features`` re-declares ``FeatureType``,
``FeatureGrant`` and ``FeatureSource`` verbatim and relies on TypeScript's
structural typing to keep the copy honest. Python's ``Protocol`` is structural
too, so the mirror would *work* -- but nothing would check the dataclass fields,
and ``fancy-conformance``'s own README names this pair as the case that
"survives only because TypeScript's structural typing does the checking -- a
mechanism that does not exist in Rust or Go". It does not meaningfully exist
across Python distributions either. One definition, imported, is cheaper than a
copy plus the test that would be needed to police it.

Spec: ``.ai/plans/fancy-catalog-features-contract.md`` section 2.
Divergences from the PHP and Node twins: ``.ai/plans/fancy-python-commerce-gating.md``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "AccessResult",
    "BillingPeriod",
    "Feature",
    "FeatureGrant",
    "FeatureGroup",
    "FeatureSource",
    "FeatureType",
    "GroupStore",
    "Subject",
    "UsageStore",
]

#: ``"boolean"`` is on/off; ``"resource"`` is metered against a quota.
FeatureType = Literal["boolean", "resource"]

#: Opaque, caller-defined subject: a user, org, team, subscription handle --
#: whatever the host means by "who". This package never inspects it beyond
#: handing it to the adapters and callbacks the host supplied.
Subject = Any

#: What a feature-definition callback may return. Anything awaitable is awaited
#: by the ``a``-prefixed API and REFUSED by the synchronous one.
_MaybeAwaitable = Any


@dataclass(frozen=True, slots=True)
class BillingPeriod:
    """A billing window for metered usage -- the PHP ``feature_usages`` period.

    Both bounds are optional. A period with neither is the "no period" bucket,
    which is what a host that does not meter per cycle gets.

    **The period is threaded through every quota path in this package**, which
    is the point of it existing: in ``fancy-features-js`` it is declared on the
    contract and reaches ``UsageStore`` from ``increment``/``decrement`` only,
    while ``remaining()`` and ``canAccess()`` read the period-LESS bucket. Its
    ``tryConsume`` then derives a limit from one bucket and enforces it against
    the other. See the plan; that is a live bug in the twin, not a style choice
    this port declined to copy.
    """

    start: datetime | None = None
    end: datetime | None = None

    @property
    def cell(self) -> str:
        """A stable key for this window. Period-less usage shares one bucket."""
        if self.start is None and self.end is None:
            return "_"
        start = self.start.isoformat() if self.start else ""
        end = self.end.isoformat() if self.end else ""
        return f"{start}-{end}"


@dataclass(frozen=True, slots=True)
class FeatureGrant:
    """A resolved entitlement for ONE feature, for ONE subject.

    What a :class:`FeatureSource` returns -- the analog of a
    ``product_feature_configs`` pivot row resolved for a subscription.

    ``included_quantity`` is a **quota in whole units** and never a float; a
    resource feature is counted, not measured. ``None`` means **unlimited**,
    which is what ``.ai/plans/fancy-catalog-features-contract.md`` section 2
    specifies and what ``fancy-features-js`` implements.

    .. warning::

       The PHP ``Fms`` service reads the same nullable ``included_quantity``
       column the OTHER way: ``Fms::remaining()`` returns ``null`` when it is
       null, and ``Fms::can()`` denies on ``null``. So one column means
       "unlimited" on Node and "denied" on PHP. This port follows the written
       contract; the divergence is reported rather than silently inherited.
    """

    key: str
    type: FeatureType = "boolean"
    enabled: bool = False
    included_quantity: int | None = None
    overage_limit: int | None = None
    source: str | None = None
    config: Mapping[str, Any] | None = None


@runtime_checkable
class FeatureSource(Protocol):
    """THE INTEGRATION EXTENSION POINT: a pluggable source of per-subject grants.

    ``fancy-catalog`` implements this (subject -> subscription -> product ->
    product features). ``fancy-features`` consumes any number of them as the last
    link in its resolution chain. Replaces the PHP "Database strategy / ``Fms``
    service" catalog bridge.

    ``grants_for`` may return an awaitable; the ``a``-prefixed manager API awaits
    it and the synchronous one refuses it with a message that says which.
    """

    @property
    def name(self) -> str:
        """For ``explain()`` and debugging, e.g. ``"catalog"``."""
        ...

    def grants_for(
        self, subject: Subject, context: Any = None
    ) -> Sequence[FeatureGrant] | Awaitable[Sequence[FeatureGrant]]: ...


@runtime_checkable
class UsageStore(Protocol):
    """Usage tracking for resource features -- the PHP ``feature_usages`` table.

    Amounts are whole units. An in-memory default ships with this package; a
    host plugs in its database.
    """

    def get_usage(
        self, subject: Subject, feature_key: str, period: BillingPeriod | None = None
    ) -> int | Awaitable[int]: ...

    def add_usage(
        self,
        subject: Subject,
        feature_key: str,
        amount: int,
        period: BillingPeriod | None = None,
    ) -> Awaitable[None] | None: ...


@runtime_checkable
class AtomicUsageStore(UsageStore, Protocol):
    """A :class:`UsageStore` that can check and increment in one operation.

    The PHP ``Fms::tryIncrement`` exists because ``can()`` followed by
    ``increment()`` is a TOCTOU race: two concurrent requests both pass the
    check before either increments, and the quota is exceeded. A store backed by
    a real database implements this with a row lock; a store that does not gets
    the non-atomic fallback and a documented caveat.
    """

    def try_consume(
        self,
        subject: Subject,
        feature_key: str,
        amount: int,
        limit: int,
        period: BillingPeriod | None = None,
    ) -> bool | Awaitable[bool]: ...


@runtime_checkable
class GroupStore(Protocol):
    """The polymorphic group-assignment adapter.

    The analog of the ``feature_group_assignments`` pivot plus the
    ``HasFeatureGroups`` trait. An in-memory default ships with this package.
    """

    def list(self, subject: Subject) -> Sequence[str] | Awaitable[Sequence[str]]: ...

    def assign(self, subject: Subject, group_key: str) -> Awaitable[None] | None: ...

    def detach(self, subject: Subject, group_key: str) -> Awaitable[None] | None: ...

    def sync(self, subject: Subject, group_keys: Sequence[str]) -> Awaitable[None] | None: ...


@dataclass(frozen=True, slots=True)
class AccessResult:
    """What ``explain()`` resolves to -- the trace behind a yes or a no.

    ``source`` is one of ``"pre-strategy"``, ``"gate"``, ``"registry"``,
    ``"group"``, ``"config"``, ``"source:<name>"`` or ``"none"``.
    """

    allowed: bool
    source: str
    remaining: int | None = None
    limit: int | None = None
    used: int = 0
    reason: str | None = None


# ---------------------------------------------------------------------------
# Feature and group DEFINITION shapes.
#
# These are `fancy-features` only -- `fancy-catalog` has no use for them, and
# the TypeScript pair does not mirror them either.
# ---------------------------------------------------------------------------

#: ``(subject, context) -> bool``. Fewer parameters are fine. **Exactly three
#: positional parameters is rejected**, because that is the pre-0.8.0 PHP order
#: ``($feature, $user, $context)`` -- still the live shape in
#: ``fancy-features-js`` -- and silently binding ``subject`` to the feature-key
#: string is the bug this package exists downstream of.
BoolCallback = Callable[..., _MaybeAwaitable]

#: ``(subject, context) -> int``.
IntCallback = Callable[..., _MaybeAwaitable]


@dataclass(frozen=True, slots=True)
class Feature:
    """A feature definition, as registered programmatically or in config.

    Every callback here receives ``(subject, context)`` -- ``check``,
    ``enabled``, ``limit``, ``usage`` and ``remaining`` alike. There is no
    exception and there never was one in the documentation; ``usage`` and
    ``remaining`` were invoked with the key first in ``laravel-fms`` before
    0.8.0, so anyone following the docs bound the user to a string and metered
    nothing. This package refuses that shape loudly rather than reproducing it.
    """

    key: str
    name: str | None = None
    description: str | None = None
    type: FeatureType = "boolean"
    #: ``bool`` or ``(subject, context) -> bool``. ``None`` means "not gated
    #: here", which for a bare definition resolves to enabled.
    enabled: bool | BoolCallback | None = None
    #: A custom access check, outranking ``enabled``.
    check: BoolCallback | None = None
    #: Resource quota: ``int`` or ``(subject, context) -> int``. ``None`` is
    #: unlimited.
    limit: int | IntCallback | None = None
    #: Resource usage override: ``(subject, context) -> int``. When absent the
    #: :class:`UsageStore` answers.
    usage: IntCallback | None = None
    #: Resource remaining override: ``(subject, context) -> int | None``.
    remaining: IntCallback | None = None


@dataclass(frozen=True, slots=True)
class FeatureGroup:
    """A bundle of features under one key.

    Subjects are assigned to groups through a :class:`GroupStore`, or a group
    carries its own ``enabled`` gate and needs no assignment at all. Both paths
    compose by OR, and resource limits supplied by groups take the **maximum**
    across every enabled group containing the feature -- a paid plan should be
    able to lift a base limit, never lower it.
    """

    key: str
    name: str | None = None
    description: str | None = None
    features: tuple[str, ...] = ()
    #: Other group keys whose features and overrides merge in. **One level
    #: only** -- no transitive expansion, and cycles are an error.
    extends: tuple[str, ...] = ()
    #: Per-feature overrides keyed by feature key. Today only ``limit``.
    overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: ``bool`` or ``(subject, context) -> bool``. When truthy the group is
    #: enabled for the subject regardless of assignment.
    enabled: bool | BoolCallback | None = None
