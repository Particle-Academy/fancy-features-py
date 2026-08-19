"""Feature groups, resource limits, metered usage, and the billing period.

The two halves that decide what a paid plan actually grants:

* **Groups** bundle features, extend one another exactly one level deep, and
  raise limits by MAX -- a plan lifts a base limit, it never lowers one.
* **Quota** is whole units, threaded through a :class:`BillingPeriod` on every
  path. The period threading is the part the Node twin gets wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fancy_features import (
    BillingPeriod,
    FeatureGrant,
    FeatureGroupCycleError,
    InMemoryGroupStore,
    InMemoryUsageStore,
    create_features,
)


def _source(name: str, *grants: FeatureGrant) -> object:
    class _S:
        def __init__(self) -> None:
            self.name = name

        def grants_for(self, subject: object, context: object = None) -> list[FeatureGrant]:
            return list(grants)

    return _S()


# --- Groups ---------------------------------------------------------------


def test_an_assigned_group_turns_its_features_on() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(groups=[{"key": "pro", "features": ["a", "b"]}], group_store=store)
    assert features.can_access("a", "u") is True
    assert features.can_access("b", "u") is True
    assert features.can_access("c", "u") is False


def test_an_unassigned_subject_gets_nothing_from_the_group() -> None:
    features = create_features(groups=[{"key": "pro", "features": ["a"]}])
    assert features.can_access("a", "u") is False


def test_a_callable_gated_group_needs_no_assignment() -> None:
    features = create_features(
        groups=[
            {
                "key": "beta",
                "features": ["experimental"],
                "enabled": lambda s, c: s == "insider",
            }
        ]
    )
    assert features.can_access("experimental", "insider") is True
    assert features.can_access("experimental", "outsider") is False


def test_a_boolean_gated_group_is_on_for_everyone() -> None:
    features = create_features(groups=[{"key": "all", "features": ["a"], "enabled": True}])
    assert features.can_access("a", "anyone") is True


def test_extends_merges_one_level_of_features() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "enterprise")
    features = create_features(
        groups=[
            {"key": "pro", "features": ["mcp"]},
            {"key": "enterprise", "extends": ["pro"], "features": ["sso"]},
        ],
        group_store=store,
    )
    assert sorted(features.group_registry.resolved_features("enterprise")) == ["mcp", "sso"]
    assert features.can_access("mcp", "u") is True


def test_extends_does_not_expand_transitively() -> None:
    # One level only, and it is a documented limit rather than an accident, so
    # it is asserted: `top` must NOT inherit `base`'s features through `mid`.
    features = create_features(
        groups=[
            {"key": "base", "features": ["deep"]},
            {"key": "mid", "extends": ["base"], "features": ["middling"]},
            {"key": "top", "extends": ["mid"], "features": ["shallow"]},
        ]
    )
    assert sorted(features.group_registry.resolved_features("top")) == ["middling", "shallow"]


def test_a_group_extending_itself_is_an_error() -> None:
    features = create_features(groups=[{"key": "loop", "extends": ["loop"], "features": ["a"]}])
    with pytest.raises(FeatureGroupCycleError, match="cannot extend itself"):
        features.group_registry.resolved_features("loop")


def test_two_groups_extending_each_other_is_an_error() -> None:
    features = create_features(
        groups=[
            {"key": "a", "extends": ["b"], "features": ["x"]},
            {"key": "b", "extends": ["a"], "features": ["y"]},
        ]
    )
    with pytest.raises(FeatureGroupCycleError, match="extend each other"):
        features.group_registry.resolved_features("a")


def test_registering_a_group_invalidates_the_resolution_cache() -> None:
    # The cache is what makes repeated checks cheap; a stale one silently
    # serves the pre-registration answer forever.
    features = create_features(groups=[{"key": "pro", "features": ["a"]}])
    assert features.group_registry.resolved_features("pro") == ["a"]
    features.register_group({"key": "pro", "features": ["a", "b"]})
    assert features.group_registry.resolved_features("pro") == ["a", "b"]


def test_own_overrides_win_over_extended_ones() -> None:
    features = create_features(
        groups=[
            {"key": "pro", "features": ["tokens"], "overrides": {"tokens": {"limit": 50}}},
            {
                "key": "enterprise",
                "extends": ["pro"],
                "overrides": {"tokens": {"limit": 250}},
            },
        ]
    )
    assert features.group_registry.resolved_overrides("enterprise")["tokens"]["limit"] == 250


def test_groups_containing_finds_every_group() -> None:
    features = create_features(
        groups=[
            {"key": "a", "features": ["x"]},
            {"key": "b", "features": ["x", "y"]},
            {"key": "c", "features": ["y"]},
        ]
    )
    assert sorted(features.group_registry.groups_containing("x")) == ["a", "b"]


def test_enabled_groups_for_merges_assigned_and_callable_gated() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "assigned")
    features = create_features(
        groups=[
            {"key": "assigned", "features": ["a"]},
            {"key": "gated", "features": ["b"], "enabled": True},
            {"key": "neither", "features": ["c"]},
        ],
        group_store=store,
    )
    assert sorted(features.enabled_groups_for("u")) == ["assigned", "gated"]


# --- Limits ---------------------------------------------------------------


def test_a_group_override_raises_the_base_limit() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 10}},
        groups=[{"key": "pro", "features": ["tokens"], "overrides": {"tokens": {"limit": 500}}}],
        group_store=store,
    )
    assert features.remaining("tokens", "u") == 500


def test_a_group_override_never_lowers_the_base_limit() -> None:
    # MAX wins. A plan that granted FEWER tokens than the free tier would be a
    # downgrade nobody asked for, and the merge is the only place that can
    # happen by accident.
    store = InMemoryGroupStore()
    store.assign("u", "cheap")
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 1000}},
        groups=[{"key": "cheap", "features": ["tokens"], "overrides": {"tokens": {"limit": 5}}}],
        group_store=store,
    )
    assert features.remaining("tokens", "u") == 1000


def test_the_maximum_is_taken_across_several_enabled_groups() -> None:
    store = InMemoryGroupStore()
    store.sync("u", ["a", "b"])
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 1}},
        groups=[
            {"key": "a", "features": ["tokens"], "overrides": {"tokens": {"limit": 40}}},
            {"key": "b", "features": ["tokens"], "overrides": {"tokens": {"limit": 90}}},
        ],
        group_store=store,
    )
    assert features.remaining("tokens", "u") == 90


def test_a_group_limit_alone_makes_it_a_resource_feature() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(
        groups=[{"key": "pro", "features": ["seats"], "overrides": {"seats": {"limit": 7}}}],
        group_store=store,
    )
    assert features.remaining("seats", "u") == 7


def test_a_callable_group_override_is_resolved() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(
        groups=[
            {
                "key": "pro",
                "features": ["tokens"],
                "overrides": {"tokens": {"limit": lambda s, c: 42}},
            }
        ],
        group_store=store,
    )
    assert features.remaining("tokens", "u") == 42


def test_a_source_limit_raises_the_base_limit_too() -> None:
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 10}},
        sources=[
            _source(
                "catalog",
                FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=900),
            )
        ],
    )
    assert features.remaining("tokens", "u") == 900


def test_an_unlimited_source_grant_beats_a_finite_one() -> None:
    features = create_features(
        sources=[
            _source(
                "a",
                FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=5),
            ),
            _source(
                "b",
                FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=None),
            ),
        ]
    )
    assert features.remaining("tokens", "u") is None


def test_a_non_resource_feature_has_no_remaining() -> None:
    features = create_features(features={"beta": {"enabled": True}})
    assert features.remaining("beta", "u") is None


def test_a_resource_feature_with_no_limit_is_unlimited() -> None:
    features = create_features(features={"tokens": {"type": "resource"}})
    assert features.remaining("tokens", "u") is None


def test_remaining_clamps_at_zero() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 3}})
    features.increment("tokens", "u", 99)
    assert features.remaining("tokens", "u") == 0


def test_a_pre_remaining_strategy_is_authoritative_and_clamped() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.register_pre_remaining_strategy("billing", lambda f, s, c: -5)
    assert features.remaining("tokens", "u") == 0
    assert features.pre_remaining_strategy_names() == ["billing"]


def test_a_null_pre_remaining_strategy_falls_through() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.register_pre_remaining_strategy("billing", lambda f, s, c: None)
    assert features.remaining("tokens", "u") == 10


def test_a_float_limit_is_truncated_not_rounded() -> None:
    # PHP `(int)`, JS `Math.trunc` and Python `int()` all truncate toward zero.
    # A quota is a count of whole units, so a limit that arrived as a float from
    # JSON must land on the same integer in all three runtimes.
    features = create_features(features={"tokens": {"type": "resource", "limit": lambda s, c: 9.9}})
    assert features.remaining("tokens", "u") == 9


def test_a_limit_is_never_a_float() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    remaining = features.remaining("tokens", "u")
    assert isinstance(remaining, int) and not isinstance(remaining, bool)


# --- Usage and the billing period ----------------------------------------


def test_increment_and_decrement_move_usage() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u", 4)
    assert features.usage_for("tokens", "u") == 4
    features.decrement("tokens", "u", 3)
    assert features.usage_for("tokens", "u") == 1


def test_usage_never_goes_negative() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.decrement("tokens", "u", 5)
    assert features.usage_for("tokens", "u") == 0


def test_usage_is_kept_per_subject() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u1", 4)
    assert features.usage_for("tokens", "u2") == 0


def test_a_subject_with_an_id_attribute_is_keyed_by_it() -> None:
    class User:
        id = 7

    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", User(), 3)
    assert features.usage_for("tokens", User()) == 3


PERIOD_ONE = BillingPeriod(
    start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC)
)
PERIOD_TWO = BillingPeriod(
    start=datetime(2026, 2, 1, tzinfo=UTC), end=datetime(2026, 3, 1, tzinfo=UTC)
)


def test_usage_is_bucketed_by_billing_period() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u", 4, period=PERIOD_ONE)
    assert features.usage_for("tokens", "u", period=PERIOD_ONE) == 4
    assert features.usage_for("tokens", "u", period=PERIOD_TWO) == 0


def test_remaining_reads_the_period_it_was_given() -> None:
    # THE bug in `fancy-features-js`: `remaining()` reads the period-LESS
    # bucket because `resourceUsage` calls `getUsage(subject, feature)` with no
    # period at all. So a subject who spent their whole January allowance shows
    # a full allowance in January and a spent one in the no-period bucket.
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u", 10, period=PERIOD_ONE)

    assert features.remaining("tokens", "u", period=PERIOD_ONE) == 0
    assert features.remaining("tokens", "u", period=PERIOD_TWO) == 10
    assert features.remaining("tokens", "u") == 10


def test_try_consume_enforces_the_limit_in_the_period_it_was_given() -> None:
    # The same bug's sharp end. The twin derives `limit = remaining + used`
    # where `remaining` comes from the period-less bucket and `used` from the
    # period one, so a subject with 90 units spent in the period is granted
    # `100 + 90 = 190`. Mixing the two buckets is an over-grant, silently.
    features = create_features(features={"tokens": {"type": "resource", "limit": 100}})
    features.increment("tokens", "u", 90, period=PERIOD_ONE)

    assert features.try_consume("tokens", "u", 10, period=PERIOD_ONE) is True
    assert features.try_consume("tokens", "u", 1, period=PERIOD_ONE) is False
    assert features.usage_for("tokens", "u", period=PERIOD_ONE) == 100


def test_can_consume_reads_the_period_for_a_resource_grant() -> None:
    """The period reaches the quota READ, not just the write.

    This used to be asserted through `can_access`, which is now quota-blind by
    ruling. The property it was really guarding -- that a spent period and a
    fresh one give different answers -- lives on `can_consume`, and it is the one
    that costs money: reading a period-less bucket while writing a period one
    means the enforced limit is not the configured one.
    """
    features = create_features(
        sources=[
            _source(
                "catalog",
                FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=2),
            )
        ]
    )
    features.increment("tokens", "u", 2, period=PERIOD_ONE)

    assert features.can_consume("tokens", "u", 1, period=PERIOD_ONE) is False
    assert features.can_consume("tokens", "u", 1, period=PERIOD_TWO) is True

    # Entitlement is period-blind, because entitlement is not quota.
    assert features.can_access("tokens", "u", period=PERIOD_ONE) is True


def test_reset_period_clears_that_window_only() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u", 4, period=PERIOD_ONE)
    features.increment("tokens", "u", 4, period=PERIOD_TWO)

    features.reset_period("u", PERIOD_ONE)
    assert features.usage_for("tokens", "u", period=PERIOD_ONE) == 0
    assert features.usage_for("tokens", "u", period=PERIOD_TWO) == 4


# --- try_consume ----------------------------------------------------------


def test_try_consume_refuses_to_exceed_the_limit() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 5}})
    assert features.try_consume("tokens", "u", 5) is True
    assert features.try_consume("tokens", "u", 1) is False
    assert features.usage_for("tokens", "u") == 5


def test_try_consume_records_usage_for_an_unlimited_feature() -> None:
    # Unlimited is not unmetered. A host that cannot bill what it cannot count
    # is the reason this records rather than short-circuits.
    features = create_features(features={"tokens": {"type": "resource"}})
    assert features.try_consume("tokens", "u", 7) is True
    assert features.usage_for("tokens", "u") == 7


def test_try_consume_uses_the_stores_atomic_path_when_it_has_one() -> None:
    calls: list[tuple[int, int]] = []

    class AtomicStore(InMemoryUsageStore):
        def try_consume(self, subject, feature_key, amount, limit, period=None):  # type: ignore[no-untyped-def]
            calls.append((amount, limit))
            return super().try_consume(subject, feature_key, amount, limit, period)

    features = create_features(
        features={"tokens": {"type": "resource", "limit": 5}}, usage=AtomicStore()
    )
    assert features.try_consume("tokens", "u", 2) is True
    assert calls == [(2, 5)]


def test_try_consume_rejects_a_negative_amount() -> None:
    # A negative "consume" is a refund wearing a disguise, and it would slip
    # past every quota check. `decrement` is the operation that exists for it.
    features = create_features(features={"tokens": {"type": "resource", "limit": 5}})
    with pytest.raises(ValueError, match="negative"):
        features.try_consume("tokens", "u", -3)


def test_try_consume_rejects_a_fractional_amount() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 5}})
    with pytest.raises(TypeError, match="whole units"):
        features.try_consume("tokens", "u", 1.5)


def test_increment_rejects_a_fractional_amount() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 5}})
    with pytest.raises(TypeError, match="whole units"):
        features.increment("tokens", "u", 0.1)


def test_a_bool_is_not_an_amount() -> None:
    # `True` is an `int` in Python and would silently meter 1.
    features = create_features(features={"tokens": {"type": "resource", "limit": 5}})
    with pytest.raises(TypeError, match="whole units"):
        features.increment("tokens", "u", True)
