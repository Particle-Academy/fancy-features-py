"""The resolution chain: pre-strategies, gate, registry, groups, config, sources.

The order is the port of ``FeatureManager::canAccess``, extended with the
``FeatureSource`` link the Node twin introduced:

    pre-strategies -> gate -> registry -> groups (OR) -> config -> sources -> deny

Two properties matter as much as the order and are asserted separately:

* **Pre-strategies and the gate are AUTHORITATIVE.** They can deny something a
  later link would allow. Everything after them is OR.
* **Everything after the gate is ADDITIVE.** A registry feature with
  ``enabled=False`` does not block a group or a source from turning it on.
"""

from __future__ import annotations

from fancy_features import FeatureGrant, InMemoryGroupStore, create_features


def _source(name: str, *grants: FeatureGrant) -> object:
    class _S:
        def __init__(self) -> None:
            self.name = name

        def grants_for(self, subject: object, context: object = None) -> list[FeatureGrant]:
            return list(grants)

    return _S()


# --- Order ----------------------------------------------------------------


def test_a_pre_strategy_outranks_the_gate() -> None:
    # This is why pre-strategies exist: a subscription service must be able to
    # be authoritative even when a stray Gate would otherwise allow.
    features = create_features(gate=lambda f, s, c: True)
    features.register_pre_strategy("billing", lambda f, s, c: False)

    assert features.can_access("anything", "u") is False


def test_a_null_pre_strategy_falls_through_to_the_next() -> None:
    calls: list[str] = []
    features = create_features(features={"beta": {"enabled": True}})
    features.register_pre_strategy("first", lambda f, s, c: calls.append("first"))
    features.register_pre_strategy("second", lambda f, s, c: (calls.append("second"), None)[1])

    assert features.can_access("beta", "u") is True
    assert calls == ["first", "second"]


def test_pre_strategies_run_in_registration_order_and_first_non_null_wins() -> None:
    features = create_features()
    features.register_pre_strategy("a", lambda f, s, c: None)
    features.register_pre_strategy("b", lambda f, s, c: True)
    features.register_pre_strategy("c", lambda f, s, c: False)

    assert features.can_access("x", "u") is True
    assert features.pre_strategy_names() == ["a", "b", "c"]


def test_re_registering_a_pre_strategy_replaces_it_and_keeps_its_slot() -> None:
    features = create_features()
    features.register_pre_strategy("a", lambda f, s, c: True)
    features.register_pre_strategy("b", lambda f, s, c: False)
    features.register_pre_strategy("a", lambda f, s, c: None)

    assert features.pre_strategy_names() == ["a", "b"]
    assert features.can_access("x", "u") is False


def test_unregistering_a_pre_strategy_is_safe_for_an_unknown_name() -> None:
    features = create_features()
    features.unregister_pre_strategy("never-registered")
    assert features.pre_strategy_names() == []


def test_the_gate_can_deny_what_the_registry_allows() -> None:
    # Gate is the only other authoritative link. If it returns a boolean, that
    # verdict is final in BOTH directions.
    features = create_features(features={"beta": {"enabled": True}}, gate=lambda f, s, c: False)
    assert features.can_access("beta", "u") is False


def test_a_gate_returning_none_falls_through() -> None:
    features = create_features(features={"beta": {"enabled": True}}, gate=lambda f, s, c: None)
    assert features.can_access("beta", "u") is True


def test_the_registry_outranks_config_for_the_same_key() -> None:
    features = create_features(features={"beta": {"enabled": False}})
    features.register_feature("beta", {"enabled": True})
    assert features.can_access("beta", "u") is True


# --- Additive semantics ---------------------------------------------------


def test_a_disabled_registry_feature_does_not_block_a_group() -> None:
    # OR semantics. A registry/config feature with `enabled: false` does NOT
    # block a group from activating it -- groups are additive by design.
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(
        features={"beta": {"enabled": False}},
        groups=[{"key": "pro", "features": ["beta"]}],
        group_store=store,
    )
    assert features.can_access("beta", "u") is True


def test_a_disabled_config_feature_does_not_block_a_source() -> None:
    features = create_features(
        features={"beta": {"enabled": False}},
        sources=[_source("catalog", FeatureGrant(key="beta", enabled=True))],
    )
    assert features.can_access("beta", "u") is True


def test_a_bare_definition_with_no_gate_is_on() -> None:
    # Mirrors PHP `checkDefinition`: no `check`, no `enabled` -> true.
    features = create_features(features={"beta": {"name": "Beta"}})
    assert features.can_access("beta", "u") is True


def test_an_unknown_feature_is_denied() -> None:
    assert create_features().can_access("nope", "u") is False


def test_check_outranks_enabled_on_the_same_definition() -> None:
    features = create_features(features={"beta": {"enabled": True, "check": lambda s, c: False}})
    assert features.can_access("beta", "u") is False


# --- Sources --------------------------------------------------------------


def test_a_source_grant_with_enabled_false_does_not_turn_it_on() -> None:
    features = create_features(
        sources=[_source("catalog", FeatureGrant(key="beta", enabled=False))]
    )
    assert features.can_access("beta", "u") is False


def test_a_resource_grant_stays_entitled_when_the_quota_is_exhausted() -> None:
    """The ruling, and this test used to assert the opposite.

    A grant-sourced resource feature was on only while quota remained, while the
    same feature defined in the registry was on regardless -- one question with
    two answers, decided by which layer the plan happened to be modelled in.
    `can_access` answers ENTITLEMENT in both now; `can_consume` is the quota
    question.
    """
    features = create_features(
        sources=[
            _source(
                "catalog",
                FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=2),
            )
        ]
    )
    assert features.can_access("tokens", "u") is True
    features.increment("tokens", "u", 2)

    assert features.remaining("tokens", "u") == 0
    # Still entitled -- the customer is still paying for it.
    assert features.can_access("tokens", "u") is True
    assert features.is_entitled("tokens", "u") is True

    # The quota question moved here, and to try_consume for an actual write.
    assert features.can_consume("tokens", "u", 1) is False
    assert features.try_consume("tokens", "u", 1) is False


def test_a_resource_grant_with_no_quantity_is_unlimited() -> None:
    # THE cross-runtime disagreement. `.ai/plans/fancy-catalog-features-contract.md`
    # section 2 says "null = unlimited" and `fancy-features-js` implements that;
    # PHP's `Fms::can()` DENIES on a null `included_quantity`. The written
    # contract settles it, which is why this is not a coin flip -- but the
    # divergence is real and is reported, because the same nullable column means
    # opposite things in two shipped packages.
    features = create_features(
        sources=[
            _source(
                "catalog",
                FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=None),
            )
        ]
    )
    assert features.can_access("tokens", "u") is True
    features.increment("tokens", "u", 10_000)
    assert features.can_access("tokens", "u") is True
    assert features.remaining("tokens", "u") is None


def test_sources_are_consulted_in_registration_order() -> None:
    features = create_features(
        sources=[
            _source("first", FeatureGrant(key="beta", enabled=True)),
            _source("second", FeatureGrant(key="beta", enabled=False)),
        ]
    )
    assert features.can_access("beta", "u") is True
    assert features.explain("beta", "u").source == "source:first"


def test_register_source_appends() -> None:
    features = create_features()
    assert features.can_access("beta", "u") is False
    features.register_source(_source("late", FeatureGrant(key="beta", enabled=True)))
    assert features.can_access("beta", "u") is True


# --- enabled() ------------------------------------------------------------


def test_enabled_lists_registry_config_group_and_source_features() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(
        features={"from-config": {"enabled": True}, "off": {"enabled": False}},
        groups=[{"key": "pro", "features": ["from-group"]}],
        group_store=store,
        sources=[_source("catalog", FeatureGrant(key="from-source", enabled=True))],
    )
    features.register_feature("from-registry", {"enabled": True})

    assert sorted(features.enabled("u")) == [
        "from-config",
        "from-group",
        "from-registry",
        "from-source",
    ]


def test_enabled_is_deduplicated() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(
        features={"beta": {"enabled": True}},
        groups=[{"key": "pro", "features": ["beta"]}],
        group_store=store,
    )
    assert features.enabled("u") == ["beta"]


# --- explain() ------------------------------------------------------------


def test_explain_names_the_pre_strategy_that_answered() -> None:
    features = create_features()
    features.register_pre_strategy("billing", lambda f, s, c: False)

    result = features.explain("beta", "u")
    assert result.allowed is False
    assert result.source == "pre-strategy"
    assert result.reason == "billing"


def test_explain_reports_the_gate_verdict_in_both_directions() -> None:
    allow = create_features(gate=lambda f, s, c: True).explain("beta", "u")
    deny = create_features(gate=lambda f, s, c: False).explain("beta", "u")
    assert (allow.source, allow.allowed) == ("gate", True)
    assert (deny.source, deny.allowed) == ("gate", False)


def test_explain_reports_the_defining_source_even_when_off() -> None:
    # "Why is this OFF?" needs the most specific source that DEFINED the
    # feature, not just "none".
    features = create_features(features={"beta": {"enabled": False}})
    result = features.explain("beta", "u")
    assert result.source == "config"
    assert result.allowed is False


def test_explain_falls_back_to_none() -> None:
    assert create_features().explain("beta", "u").source == "none"


def test_explain_fills_in_resource_numbers() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u", 4)

    result = features.explain("tokens", "u")
    assert (result.remaining, result.limit, result.used) == (6, 10, 4)


def test_explain_names_the_group_that_matched() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "pro")
    features = create_features(groups=[{"key": "pro", "features": ["beta"]}], group_store=store)
    result = features.explain("beta", "u")
    assert result.source == "group"
    assert result.reason == "pro"
