"""The quota arithmetic, as pure functions.

Every decision this package makes about a metered feature reduces to one of
these, and they are framework-free and side-effect-free so the shared
``shared/feature-entitlement`` conformance table can hold this runtime,
``laravel-fms`` and ``@particle-academy/fancy-features`` to identical answers.
Cross-runtime behaviour belongs in a fixture row, not in three sets of prose
that agree today.

## Entitlement is not quota

:func:`entitled` is deliberately blind to ``included_quantity`` and ``used``.
Both twins used to answer differently depending on where a feature happened to
be defined: a registry or config resource feature was on when its
``enabled``/``check`` said so, while a feature arriving from a
:class:`~fancy_features.contract.FeatureSource` was on only while quota
remained. One question, two answers. :func:`can_consume` is the quota-aware
read.

## Everything here is a WHOLE UNIT

A resource feature is counted, never measured. No fractional unit, no rate, no
proportional split. Money enters only when a host multiplies recorded overage
units by a unit amount in minor units, which this package never does.
"""

from __future__ import annotations

__all__ = [
    "allows_consumption",
    "can_consume",
    "consumption_ceiling",
    "entitled",
    "overage_delta",
]


def entitled(
    enabled: bool,
    type: str = "boolean",
    included_quantity: int | None = None,
    used: int = 0,
) -> bool:
    """Is the subject entitled to the feature at all?

    ``included_quantity`` and ``used`` are accepted and **ignored**. They are in
    the signature because the conformance table hands them over and requires the
    answer not to move -- an implementation that starts consulting them has
    re-merged two different questions, which is the defect this replaced.
    """
    return enabled


def consumption_ceiling(included_quantity: int | None, overage_limit: int | None) -> int | None:
    """The highest total usage a subject may reach.

    The included quantity plus whatever billable overage is permitted above it.
    ``None`` in means unlimited and ``None`` out means the same: there is no
    included line to exceed, so an overage allowance is meaningless and is
    ignored rather than added to something.

    **A ``None`` or ``0`` overage limit means NO overage.** Not an arbitrary
    reading: the field was carried by three runtimes and consulted by none until
    now, so every configuration in existence has it unset, and reading it as
    "unbounded" would turn each of them into an unlimited spending authority.
    """
    if included_quantity is None:
        return None
    return included_quantity + max(0, overage_limit or 0)


def allows_consumption(used: int, amount: int, ceiling: int | None) -> bool:
    """Does this request fit under the ceiling?

    All-or-nothing, on purpose. A request for 150 units against 100 remaining is
    refused rather than partly filled: the answer is a bool, so a caller that got
    100 has no way to learn that it did, and callers do not check quantities they
    were never told about.

    ``<=``, not ``<``. A plan that says 100 has to permit the hundredth unit.
    """
    if ceiling is None:
        return True
    return used + amount <= ceiling


def overage_delta(used: int, amount: int, included_quantity: int | None) -> int:
    """How many of the units in this consumption are BILLABLE OVERAGE.

    **Signed on purpose**: a refund passes a negative ``amount`` and gets a
    negative delta, so increment and decrement share one function and cannot
    drift apart. The caller clamps the stored total at zero.

    Subtracting the overage that existed BEFORE the call is what makes this
    composable over a period. The obvious ``max(0, after - included)`` is wrong
    for a subject already in overage -- it re-bills every unit already recorded,
    every time.
    """
    if included_quantity is None:
        return 0
    after = used + amount
    return max(0, after - included_quantity) - max(0, used - included_quantity)


def can_consume(
    enabled: bool,
    included_quantity: int | None,
    overage_limit: int | None,
    used: int,
    amount: int,
) -> bool:
    """Entitled AND it fits -- the quota-aware read.

    This is what ``can_access`` answered for a source grant before the ruling. It
    is a READ: between it and the write that follows, another request can spend
    the last unit. Use ``try_consume`` to gate an actual consumption; use this to
    decide what to show someone.
    """
    if not enabled:
        return False
    return allows_consumption(used, amount, consumption_ceiling(included_quantity, overage_limit))
