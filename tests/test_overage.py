"""Billable overage: the ceiling, the split, and what happens when it cannot be recorded.

``overage_limit`` was a field on the grant contract, faithfully carried by three
runtimes and consulted by none. This is what it does now, and every assertion
here is matched by a named test in ``laravel-fms`` and
``@particle-academy/fancy-features``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from fancy_features import (
    BillingPeriod,
    FeatureGrant,
    InMemoryUsageStore,
    OverageEvent,
    Subject,
    create_features,
)

PERIOD = BillingPeriod()


class _Source:
    """A one-grant catalog stand-in."""

    name = "catalog"

    def __init__(self, **grant: Any) -> None:
        self._grant = FeatureGrant(key="tokens", type="resource", enabled=True, **grant)

    def grants_for(self, subject: Subject, context: Any = None) -> Sequence[FeatureGrant]:
        return [self._grant]


class LegacyStore:
    """A store that predates overage: ``get_usage`` / ``add_usage`` and nothing else.

    Most hosts wrote one of these, which is the whole reason overage is opt-in
    here rather than switched on by a migration as it is in the PHP twin.
    """

    def __init__(self) -> None:
        self._cells: dict[tuple[str, str], int] = {}

    def get_usage(
        self, subject: Subject, feature_key: str, period: BillingPeriod | None = None
    ) -> int:
        return self._cells.get((str(subject), feature_key), 0)

    def add_usage(
        self,
        subject: Subject,
        feature_key: str,
        amount: int,
        period: BillingPeriod | None = None,
    ) -> None:
        key = (str(subject), feature_key)
        self._cells[key] = max(0, self._cells.get(key, 0) + amount)


def test_no_overage_is_configured_so_consumption_stops_at_the_included_quantity() -> None:
    features = create_features(sources=[_Source(included_quantity=100)])
    features.on_overage(lambda event: None)

    assert features.try_consume("tokens", "u", 100) is True
    # `overage_limit` unset means NO overage. Every configuration written before
    # this ruling has it unset, so this is what keeps the change opt-in.
    assert features.try_consume("tokens", "u", 1) is False
    assert features.overage_for("tokens", "u") == 0


def test_consumption_inside_the_band_is_permitted_and_recorded() -> None:
    events: list[OverageEvent] = []
    features = create_features(sources=[_Source(included_quantity=100, overage_limit=50)])
    features.on_overage(events.append)

    assert features.try_consume("tokens", "u", 90) is True
    assert features.overage_for("tokens", "u") == 0

    # Straddles the included line: 10 of these 30 are free, 20 are billable.
    assert features.try_consume("tokens", "u", 30) is True
    assert features.overage_for("tokens", "u") == 20

    # Already above the line: all 10 are billable, and the 20 already recorded
    # are NOT re-billed. A naive max(0, after - included) answers 30 here.
    assert features.try_consume("tokens", "u", 10) is True
    assert features.overage_for("tokens", "u") == 30

    assert [e.units for e in events] == [20, 10]
    assert events[-1].total_units == 30
    assert events[-1].included_quantity == 100


def test_the_overage_band_has_an_end_and_it_is_enforced() -> None:
    features = create_features(sources=[_Source(included_quantity=100, overage_limit=50)])
    features.on_overage(lambda event: None)

    assert features.try_consume("tokens", "u", 150) is True
    # A ceiling, not an alert. A field named *_limit that does not limit is the
    # same defect in a new costume.
    assert features.can_consume("tokens", "u", 1) is False
    assert features.try_consume("tokens", "u", 1) is False
    assert features.overage_for("tokens", "u") == 50


def test_overage_is_refused_when_it_cannot_be_recorded() -> None:
    # No `add_overage` on the store and no `on_overage` listener: nowhere to
    # write it down, so the ceiling stays at the included quantity. Unbilled
    # usage is the one failure that cannot be repaired after the fact, so the
    # default fails closed.
    features = create_features(
        usage=LegacyStore(),
        sources=[_Source(included_quantity=100, overage_limit=50)],
    )

    assert features.try_consume("tokens", "u", 100) is True
    assert features.try_consume("tokens", "u", 1) is False


def test_a_listener_alone_is_enough_to_take_responsibility() -> None:
    events: list[OverageEvent] = []
    features = create_features(
        usage=LegacyStore(),
        sources=[_Source(included_quantity=100, overage_limit=50)],
    )
    features.on_overage(events.append)

    assert features.try_consume("tokens", "u", 100) is True
    assert features.try_consume("tokens", "u", 10) is True
    assert [e.units for e in events] == [10]
    # The store cannot store it, so `total_units` falls back to this consumption
    # rather than reporting a total it has no way to know.
    assert events[0].total_units == 10


def test_a_refund_unwinds_only_the_billable_part() -> None:
    features = create_features(
        usage=InMemoryUsageStore(),
        sources=[_Source(included_quantity=100, overage_limit=50)],
    )
    features.on_overage(lambda event: None)

    features.try_consume("tokens", "u", 110)
    assert features.overage_for("tokens", "u") == 10

    # Refunding 30 units when only 10 of them were ever billable must credit 10,
    # not 30.
    features.decrement("tokens", "u", 30)
    assert features.usage_for("tokens", "u") == 80
    assert features.overage_for("tokens", "u") == 0


def test_a_refund_does_not_fire_the_listener() -> None:
    events: list[OverageEvent] = []
    features = create_features(sources=[_Source(included_quantity=100, overage_limit=50)])
    features.on_overage(events.append)

    features.try_consume("tokens", "u", 110)
    features.decrement("tokens", "u", 5)

    # A credit is a decision about money; inventing one from a usage correction
    # is not this package's call.
    assert len(events) == 1


def test_the_unenforced_increment_path_records_overage_too() -> None:
    features = create_features(sources=[_Source(included_quantity=100, overage_limit=50)])
    features.on_overage(lambda event: None)

    # `increment` does not ENFORCE the quota and never has. It must still RECORD
    # the billable share, or the invoice is built from a figure only some code
    # paths maintain.
    features.increment("tokens", "u", 130)
    assert features.overage_for("tokens", "u") == 30


def test_an_unlimited_allowance_never_accrues_overage() -> None:
    features = create_features(sources=[_Source(included_quantity=None, overage_limit=50)])
    features.on_overage(lambda event: None)

    assert features.try_consume("tokens", "u", 1_000_000) is True
    # Unlimited is not unmetered.
    assert features.usage_for("tokens", "u") == 1_000_000
    assert features.overage_for("tokens", "u") == 0


def test_a_config_feature_can_carry_an_overage_allowance_with_no_catalog() -> None:
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100, "overage_limit": 20}}
    )
    features.on_overage(lambda event: None)

    assert features.try_consume("tokens", "u", 115) is True
    assert features.overage_for("tokens", "u") == 15
    assert features.try_consume("tokens", "u", 6) is False


def test_overage_resets_with_the_billing_period() -> None:
    period = BillingPeriod()
    features = create_features(sources=[_Source(included_quantity=100, overage_limit=50)])
    features.on_overage(lambda event: None)

    features.try_consume("tokens", "u", 130, period=period)
    assert features.overage_for("tokens", "u", period) == 30

    features.reset_period("u", period)
    assert features.overage_for("tokens", "u", period) == 0
    assert features.usage_for("tokens", "u", period) == 0


def test_a_negative_consume_is_refused_rather_than_walking_past_the_ceiling() -> None:
    features = create_features(sources=[_Source(included_quantity=100)])

    with pytest.raises(ValueError, match="negative"):
        features.try_consume("tokens", "u", -50)


def test_the_included_line_is_the_limit_not_remaining_plus_used() -> None:
    """`remaining` is clamped at zero, so it cannot be inverted once past the line.

    Deriving the included quantity as `remaining + used` is right only while
    usage is below it. The moment a subject is in overage the derivation reports
    the limit as whatever they have already spent -- self-fulfilling, and every
    overage figure downstream then measures from the wrong line.
    """
    features = create_features(sources=[_Source(included_quantity=100, overage_limit=50)])
    features.on_overage(lambda event: None)

    features.increment("tokens", "u", 120)
    assert features.remaining("tokens", "u") == 0

    # If the line had moved to 120, this consumption would record 0 overage and
    # the ceiling would have slid to 170.
    features.increment("tokens", "u", 10)
    assert features.overage_for("tokens", "u") == 30
    assert features.try_consume("tokens", "u", 25) is False
