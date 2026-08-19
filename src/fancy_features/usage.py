"""Metering: the in-memory usage store and the whole-units guard.

A quota is a **count**, never a measurement. Everything here is an ``int``, and
the one function that turns a caller's argument into one refuses anything that
is not already a whole number rather than rounding it. `fancy-conformance`
exists because a money bug in `fancy-mlm` got through two implementations; the
cheapest place to stop the same class of bug is at the door.
"""

from __future__ import annotations

from typing import Any

from .contract import BillingPeriod, Subject
from .groups import default_subject_key

__all__ = ["InMemoryUsageStore", "whole_units"]


def whole_units(amount: Any, *, what: str = "amount") -> int:
    """Coerce a metered amount to an ``int``, refusing anything lossy.

    ``True`` is an ``int`` in Python and would silently meter 1, so it is
    refused explicitly rather than by type. ``2.0`` is refused too: a float
    reaching a quota path means somebody is measuring where they should be
    counting, and accepting the integral ones only defers the failure to the
    first ``0.1``.
    """
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError(
            f"A metered {what} is counted in whole units, so it must be an int; "
            f"got {amount!r} ({type(amount).__name__})."
        )
    return amount


class InMemoryUsageStore:
    """The default :class:`~fancy_features.contract.UsageStore` -- the ``feature_usages`` analog.

    Usage is bucketed by ``(subject, feature, period)``. Period-less usage
    shares one bucket, which is what a host that does not meter per cycle gets.

    ``try_consume`` is atomic *within one process*, which is all a dict can
    promise. A production store implements it with a row lock; the PHP
    ``Fms::tryIncrement`` is the reference, and the reason it exists is that
    ``can()`` followed by ``increment()`` lets two concurrent requests both pass
    the check before either writes.
    """

    def __init__(self, key_of: Any = None) -> None:
        self._cells: dict[tuple[str, str, str], int] = {}
        self._key_of = key_of or default_subject_key

    def _cell(
        self, subject: Subject, feature_key: str, period: BillingPeriod | None
    ) -> tuple[str, str, str]:
        return (self._key_of(subject), feature_key, (period or BillingPeriod()).cell)

    def get_usage(
        self, subject: Subject, feature_key: str, period: BillingPeriod | None = None
    ) -> int:
        return self._cells.get(self._cell(subject, feature_key, period), 0)

    def add_usage(
        self,
        subject: Subject,
        feature_key: str,
        amount: int,
        period: BillingPeriod | None = None,
    ) -> None:
        cell = self._cell(subject, feature_key, period)
        # Clamped at zero so a decrement never drives usage negative, which
        # would otherwise hand back quota that was never returned.
        self._cells[cell] = max(0, self._cells.get(cell, 0) + whole_units(amount))

    def try_consume(
        self,
        subject: Subject,
        feature_key: str,
        amount: int,
        limit: int,
        period: BillingPeriod | None = None,
    ) -> bool:
        cell = self._cell(subject, feature_key, period)
        used = self._cells.get(cell, 0)
        if limit - used < whole_units(amount):
            return False
        self._cells[cell] = used + amount
        return True

    def reset_period(self, subject: Subject, period: BillingPeriod) -> None:
        """Drop every usage row for a subject in one window -- the renewal reset."""
        subject_key = self._key_of(subject)
        period_key = period.cell
        for cell in [c for c in self._cells if c[0] == subject_key and c[2] == period_key]:
            del self._cells[cell]
