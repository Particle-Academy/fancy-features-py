"""The async driver, the registry's definition forms, the guard, and the helpers.

The async half is the load-bearing part. Every adapter in the contract may
return an awaitable, because a real ``UsageStore`` is a database. So the
resolution chain has to work both ways -- and it must do so **from one copy of
the rules**, or the two drivers drift and only one of them is ever tested
properly.
"""

from __future__ import annotations

import asyncio

import pytest

from fancy_features import (
    FeatureAccessDeniedError,
    FeatureAsyncRequiredError,
    FeatureRegistry,
    InMemoryGroupStore,
    arequire_feature,
    can_access_any,
    configure_features,
    create_features,
    enabled_features,
    feature,
    feature_remaining,
    require_feature,
)
from fancy_features.contract import FeatureGrant

# --- Sync and async resolve identically -----------------------------------


class AsyncUsageStore:
    """A store whose every method is a coroutine -- what a real one looks like."""

    def __init__(self) -> None:
        self._cells: dict[str, int] = {}

    async def get_usage(self, subject, feature_key, period=None):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return self._cells.get(f"{subject}:{feature_key}", 0)

    async def add_usage(self, subject, feature_key, amount, period=None):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        cell = f"{subject}:{feature_key}"
        self._cells[cell] = max(0, self._cells.get(cell, 0) + amount)


class AsyncSource:
    name = "async-catalog"

    async def grants_for(self, subject, context=None):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return [FeatureGrant(key="tokens", type="resource", enabled=True, included_quantity=10)]


class AsyncGroupStore:
    async def list(self, subject):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return ["pro"]

    async def assign(self, subject, group_key):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)

    async def detach(self, subject, group_key):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)

    async def sync(self, subject, group_keys):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)


def test_an_async_store_resolves_through_the_async_api() -> None:
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100}}, usage=AsyncUsageStore()
    )

    async def scenario() -> tuple[int | None, bool]:
        await features.aincrement("tokens", "u", 40)
        return await features.aremaining("tokens", "u"), await features.acan_access("tokens", "u")

    assert asyncio.run(scenario()) == (60, True)


def test_an_async_source_resolves_through_the_async_api() -> None:
    features = create_features(sources=[AsyncSource()], usage=AsyncUsageStore())

    async def scenario() -> tuple[bool, int | None, list[str]]:
        return (
            await features.acan_access("tokens", "u"),
            await features.aremaining("tokens", "u"),
            await features.aenabled("u"),
        )

    assert asyncio.run(scenario()) == (True, 10, ["tokens"])


def test_an_async_group_store_resolves_through_the_async_api() -> None:
    features = create_features(
        groups=[{"key": "pro", "features": ["mcp"]}], group_store=AsyncGroupStore()
    )
    assert asyncio.run(features.acan_access("mcp", "u")) is True


def test_an_async_callback_resolves_through_the_async_api() -> None:
    async def limit(subject, context):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return 12

    features = create_features(features={"tokens": {"type": "resource", "limit": limit}})
    assert asyncio.run(features.aremaining("tokens", "u")) == 12


def test_an_async_pre_strategy_and_gate_resolve() -> None:
    async def gate(f, s, c):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return False

    features = create_features(features={"beta": {"enabled": True}}, gate=gate)
    assert asyncio.run(features.acan_access("beta", "u")) is False


def test_the_sync_api_refuses_an_awaitable_rather_than_believing_it() -> None:
    # A coroutine object is truthy. Storing one and moving on would report a
    # feature as enabled with the check never having run -- success-shaped and
    # completely wrong. So the synchronous driver refuses it by name.
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100}}, usage=AsyncUsageStore()
    )

    with pytest.raises(FeatureAsyncRequiredError, match="aremaining"):
        features.remaining("tokens", "u")


def test_the_refusal_names_the_synchronous_method_that_failed() -> None:
    features = create_features(features={"beta": {"enabled": True}}, gate=_async_gate)
    with pytest.raises(FeatureAsyncRequiredError, match="acan_access"):
        features.can_access("beta", "u")


async def _async_gate(f, s, c):  # type: ignore[no-untyped-def]
    await asyncio.sleep(0)
    return True


def test_a_sync_store_still_works_through_the_async_api() -> None:
    # The async driver must not REQUIRE awaitables -- an in-memory store stays
    # usable from an async host without a second implementation.
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    assert asyncio.run(features.aremaining("tokens", "u")) == 10


def test_async_try_consume_enforces_the_quota() -> None:
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 3}}, usage=AsyncUsageStore()
    )

    async def scenario() -> list[bool]:
        return [
            await features.atry_consume("tokens", "u", 2),
            await features.atry_consume("tokens", "u", 2),
            await features.atry_consume("tokens", "u", 1),
        ]

    assert asyncio.run(scenario()) == [True, False, True]


def test_async_explain_matches_sync_explain() -> None:
    features = create_features(features={"tokens": {"type": "resource", "limit": 10}})
    features.increment("tokens", "u", 3)
    assert asyncio.run(features.aexplain("tokens", "u")) == features.explain("tokens", "u")


# --- The registry's definition forms --------------------------------------


def test_the_registry_accepts_a_mapping() -> None:
    registry = FeatureRegistry()
    registry.register("beta", {"name": "Beta", "enabled": True})
    definition = registry.definition("beta")
    assert definition is not None and definition.key == "beta" and definition.name == "Beta"


def test_the_registry_accepts_a_feature_instance() -> None:
    from fancy_features import Feature

    registry = FeatureRegistry()
    registry.register("beta", Feature(key="ignored", name="Beta"))
    definition = registry.definition("beta")
    # The registration key wins over whatever the value carried, so a copied
    # definition cannot silently answer for the wrong feature.
    assert definition is not None and definition.key == "beta"


def test_the_registry_accepts_a_factory_function() -> None:
    registry = FeatureRegistry()
    registry.register("beta", lambda: {"name": "Made lazily"})
    definition = registry.definition("beta")
    assert definition is not None and definition.name == "Made lazily"


def test_the_registry_accepts_a_class_with_a_definition_method() -> None:
    class BetaFeature:
        def definition(self) -> dict[str, object]:
            return {"name": "From a class", "type": "resource", "limit": 5}

    registry = FeatureRegistry()
    registry.register("beta", BetaFeature)
    definition = registry.definition("beta")
    assert definition is not None and definition.limit == 5


def test_the_registry_accepts_an_instance_with_a_definition_method() -> None:
    class BetaFeature:
        def definition(self) -> dict[str, object]:
            return {"name": "From an instance"}

    registry = FeatureRegistry()
    registry.register("beta", BetaFeature())
    definition = registry.definition("beta")
    assert definition is not None and definition.name == "From an instance"


def test_an_unknown_key_resolves_to_none() -> None:
    assert FeatureRegistry().definition("nope") is None


def test_an_unknown_definition_field_is_refused() -> None:
    # A typo in a config map is otherwise silent: `{"limits": 10}` defines a
    # resource feature with no limit, i.e. unlimited, which is the wrong way for
    # a typo to fail.
    registry = FeatureRegistry()
    registry.register("beta", {"limits": 10})
    with pytest.raises(TypeError, match="limits"):
        registry.definition("beta")


def test_registry_keys_and_has() -> None:
    registry = FeatureRegistry()
    registry.register("a", {})
    assert registry.keys() == ["a"]
    assert registry.has("a") and not registry.has("b")


# --- The guard ------------------------------------------------------------


def test_require_feature_passes_when_any_key_is_allowed() -> None:
    features = create_features(features={"b": {"enabled": True}})
    require_feature(features, ["a", "b"], "u")  # does not raise


def test_require_feature_raises_when_none_is_allowed() -> None:
    features = create_features()
    with pytest.raises(FeatureAccessDeniedError) as excinfo:
        require_feature(features, ["a", "b"], "u")
    assert excinfo.value.features == ["a", "b"]
    assert excinfo.value.status == 403


def test_require_feature_accepts_a_single_key() -> None:
    features = create_features(features={"a": {"enabled": True}})
    require_feature(features, "a", "u")


def test_an_empty_guard_fails_closed() -> None:
    # Mirrors the PHP middleware's "requires at least one feature argument".
    # Silently allowing traffic past a `require feature` gate is how a typo in a
    # route definition becomes an open door.
    features = create_features()
    with pytest.raises(ValueError, match="at least one"):
        require_feature(features, [], "u")


def test_can_access_any_is_the_boolean_variant() -> None:
    features = create_features(features={"b": {"enabled": True}})
    assert can_access_any(features, ["a", "b"], "u") is True
    assert can_access_any(features, ["a"], "u") is False


def test_the_guard_has_an_async_form() -> None:
    features = create_features(features={"b": {"enabled": True}}, usage=AsyncUsageStore())
    asyncio.run(arequire_feature(features, ["a", "b"], "u"))


# --- The bound helpers ----------------------------------------------------


def test_the_helpers_route_through_the_configured_default() -> None:
    configure_features(
        features={"beta": {"enabled": True}, "tokens": {"type": "resource", "limit": 4}}
    )

    assert feature("beta", "u") is True
    assert feature_remaining("tokens", "u") == 4
    assert sorted(enabled_features("u")) == ["beta", "tokens"]
    assert feature() is not None


def test_the_helpers_raise_before_a_default_is_configured() -> None:
    import fancy_features.helpers as helpers

    helpers.set_default_features(None)
    with pytest.raises(RuntimeError, match="configure_features"):
        feature("beta", "u")


def test_a_group_store_round_trips() -> None:
    store = InMemoryGroupStore()
    store.assign("u", "a")
    store.assign("u", "a")
    store.assign("u", "b")
    assert sorted(store.list("u")) == ["a", "b"]
    store.detach("u", "a")
    assert store.list("u") == ["b"]
    store.sync("u", ["x", "y"])
    assert sorted(store.list("u")) == ["x", "y"]
    assert store.has("u", "x") is True
