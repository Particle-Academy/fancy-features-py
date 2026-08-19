"""``FeatureManager`` -- the resolution chain, written once and driven two ways.

Port of ``ParticleAcademy\\Fms\\Services\\FeatureManager``, extended with the
``FeatureSource`` link the Node twin introduced, and with the billing period
threaded through every quota path.

Resolution order for access::

    pre-strategies -> gate -> registry -> groups (OR) -> config -> sources -> deny

The first two links are **authoritative**: a non-``None`` verdict from either is
final in both directions, which is what lets a subscription service deny
something a stray gate would allow. Everything after them is **additive**: a
registry or config feature with ``enabled=False`` does not block a group or a
source from turning it on. Both properties are asserted in ``tests/test_resolution.py``.

Resolution for a resource quota::

    pre-remaining strategies -> MAX(group limit, source limit, feature limit) - usage

clamped at zero, with ``None`` meaning unlimited.

Every method exists twice: ``can_access`` / ``acan_access``, ``remaining`` /
``aremaining``, and so on. The pair drives the same generator -- see
``_drive.py``. **Add behaviour to the generators, never to a wrapper.**
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ._drive import Step, adrive, call_definition_callback, drive_sync
from .contract import (
    AccessResult,
    BillingPeriod,
    Feature,
    FeatureGrant,
    FeatureGroup,
    FeatureSource,
    GroupStore,
    Subject,
    UsageStore,
)
from .groups import FeatureGroupRegistry, InMemoryGroupStore
from .registry import FeatureRegistry
from .usage import InMemoryUsageStore, whole_units

__all__ = ["FeatureManager", "create_features"]

#: ``(feature, subject, context) -> bool | None``. ``None`` falls through.
PreStrategy = Callable[..., Any]
#: ``(feature, subject, context) -> int | None``. ``None`` falls through.
PreRemainingStrategy = Callable[..., Any]
#: ``(feature, subject, context) -> bool | None`` -- the Laravel ``Gate`` analog.
GateResolver = Callable[..., Any]


class FeatureManager:
    """The engine. Construct through :func:`create_features` unless you need the parts."""

    def __init__(
        self,
        *,
        features: Mapping[str, Any] | None = None,
        groups: Iterable[FeatureGroup | Mapping[str, Any]] | None = None,
        sources: Iterable[FeatureSource] | None = None,
        usage: UsageStore | None = None,
        group_store: GroupStore | None = None,
        gate: GateResolver | None = None,
        registry: FeatureRegistry | None = None,
        group_registry: FeatureGroupRegistry | None = None,
    ) -> None:
        self.registry = registry or FeatureRegistry()
        self.group_registry = group_registry or FeatureGroupRegistry()
        self.group_store: GroupStore = group_store or InMemoryGroupStore()
        self.usage: UsageStore = usage or InMemoryUsageStore()

        self._sources: list[FeatureSource] = list(sources or ())
        self._gate = gate
        self._pre_strategies: dict[str, PreStrategy] = {}
        self._pre_remaining: dict[str, PreRemainingStrategy] = {}

        # The lower-priority `config` map. Held in its own registry rather than
        # merged into the programmatic one, because the order between them is
        # part of the contract: registry outranks config for the same key.
        self._config = FeatureRegistry()
        for key, definition in (features or {}).items():
            self._config.register(key, definition)

        for group in groups or ():
            self.group_registry.register(group)

    # -- Registration ------------------------------------------------------

    def register_pre_strategy(self, name: str, strategy: PreStrategy) -> FeatureManager:
        """Register a boolean pre-strategy. Re-registering a name keeps its slot."""
        self._pre_strategies[name] = strategy
        return self

    def unregister_pre_strategy(self, name: str) -> FeatureManager:
        self._pre_strategies.pop(name, None)
        return self

    def pre_strategy_names(self) -> list[str]:
        return list(self._pre_strategies)

    def register_pre_remaining_strategy(
        self, name: str, strategy: PreRemainingStrategy
    ) -> FeatureManager:
        self._pre_remaining[name] = strategy
        return self

    def unregister_pre_remaining_strategy(self, name: str) -> FeatureManager:
        self._pre_remaining.pop(name, None)
        return self

    def pre_remaining_strategy_names(self) -> list[str]:
        return list(self._pre_remaining)

    def register_source(self, source: FeatureSource) -> FeatureManager:
        """Append a :class:`FeatureSource` -- the catalog plug-in point."""
        self._sources.append(source)
        return self

    def register_feature(self, key: str, definition: Any) -> FeatureManager:
        self.registry.register(key, definition)
        return self

    def register_group(self, group: FeatureGroup | Mapping[str, Any]) -> FeatureManager:
        self.group_registry.register(group)
        return self

    @property
    def sources(self) -> list[FeatureSource]:
        return list(self._sources)

    # -- Public API: access ------------------------------------------------

    def can_access(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return drive_sync(
            self._can_access(feature, subject, context, period),
            sync_name="can_access",
            async_name="acan_access",
        )

    async def acan_access(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return await adrive(self._can_access(feature, subject, context, period))

    #: Alias, matching ``isEnabled`` / ``hasFeature`` on both twins.
    def is_enabled(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return self.can_access(feature, subject, context, period)

    def has_feature(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return self.can_access(feature, subject, context, period)

    async def ais_enabled(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return await self.acan_access(feature, subject, context, period)

    async def ahas_feature(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return await self.acan_access(feature, subject, context, period)

    def remaining(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> int | None:
        return drive_sync(
            self._remaining(feature, subject, context, period),
            sync_name="remaining",
            async_name="aremaining",
        )

    async def aremaining(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> int | None:
        return await adrive(self._remaining(feature, subject, context, period))

    def enabled(
        self, subject: Subject = None, context: Any = None, period: BillingPeriod | None = None
    ) -> list[str]:
        return drive_sync(
            self._enabled(subject, context, period), sync_name="enabled", async_name="aenabled"
        )

    async def aenabled(
        self, subject: Subject = None, context: Any = None, period: BillingPeriod | None = None
    ) -> list[str]:
        return await adrive(self._enabled(subject, context, period))

    def explain(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> AccessResult:
        return drive_sync(
            self._explain(feature, subject, context, period),
            sync_name="explain",
            async_name="aexplain",
        )

    async def aexplain(
        self,
        feature: str,
        subject: Subject = None,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> AccessResult:
        return await adrive(self._explain(feature, subject, context, period))

    def enabled_groups_for(self, subject: Subject = None, context: Any = None) -> list[str]:
        return drive_sync(
            self._enabled_groups_for(subject, context),
            sync_name="enabled_groups_for",
            async_name="aenabled_groups_for",
        )

    async def aenabled_groups_for(self, subject: Subject = None, context: Any = None) -> list[str]:
        return await adrive(self._enabled_groups_for(subject, context))

    # -- Public API: metering ---------------------------------------------

    def usage_for(self, feature: str, subject: Subject, period: BillingPeriod | None = None) -> int:
        return drive_sync(
            self._usage_for(feature, subject, period),
            sync_name="usage_for",
            async_name="ausage_for",
        )

    async def ausage_for(
        self, feature: str, subject: Subject, period: BillingPeriod | None = None
    ) -> int:
        return await adrive(self._usage_for(feature, subject, period))

    def increment(
        self,
        feature: str,
        subject: Subject,
        amount: int = 1,
        period: BillingPeriod | None = None,
    ) -> None:
        """Record usage. Does **not** enforce the quota -- see :meth:`try_consume`."""
        drive_sync(
            self._add_usage(feature, subject, whole_units(amount), period),
            sync_name="increment",
            async_name="aincrement",
        )

    async def aincrement(
        self,
        feature: str,
        subject: Subject,
        amount: int = 1,
        period: BillingPeriod | None = None,
    ) -> None:
        await adrive(self._add_usage(feature, subject, whole_units(amount), period))

    def decrement(
        self,
        feature: str,
        subject: Subject,
        amount: int = 1,
        period: BillingPeriod | None = None,
    ) -> None:
        """Refund usage, clamped at zero by the store."""
        drive_sync(
            self._add_usage(feature, subject, -whole_units(amount), period),
            sync_name="decrement",
            async_name="adecrement",
        )

    async def adecrement(
        self,
        feature: str,
        subject: Subject,
        amount: int = 1,
        period: BillingPeriod | None = None,
    ) -> None:
        await adrive(self._add_usage(feature, subject, -whole_units(amount), period))

    def try_consume(
        self,
        feature: str,
        subject: Subject,
        amount: int = 1,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return drive_sync(
            self._try_consume(feature, subject, amount, context, period),
            sync_name="try_consume",
            async_name="atry_consume",
        )

    async def atry_consume(
        self,
        feature: str,
        subject: Subject,
        amount: int = 1,
        context: Any = None,
        period: BillingPeriod | None = None,
    ) -> bool:
        return await adrive(self._try_consume(feature, subject, amount, context, period))

    def reset_period(self, subject: Subject, period: BillingPeriod) -> None:
        drive_sync(
            self._reset_period(subject, period),
            sync_name="reset_period",
            async_name="areset_period",
        )

    async def areset_period(self, subject: Subject, period: BillingPeriod) -> None:
        await adrive(self._reset_period(subject, period))

    # -- The chain ---------------------------------------------------------

    def _can_access(
        self, feature: str, subject: Subject, context: Any, period: BillingPeriod | None
    ) -> Step[bool]:
        # 0. Pre-strategies, in registration order. First non-None wins and is
        #    authoritative -- above the gate, so a subscription service can deny
        #    what a stray gate would allow.
        for strategy in self._pre_strategies.values():
            verdict = yield strategy(feature, subject, context)
            if verdict is not None:
                return bool(verdict)

        # 1. Gate. Authoritative in BOTH directions when it answers.
        if self._gate is not None:
            verdict = yield self._gate(feature, subject, context)
            if verdict is not None:
                return bool(verdict)

        # 2. Registry.
        definition = self.registry.definition(feature)
        if definition is not None and (
            yield from self._check_definition(definition, subject, context)
        ):
            return True

        # 3. Groups, OR'd across every enabled group containing the feature.
        if (yield from self._matching_groups(feature, subject, context)):
            return True

        # 4. Config.
        config = self._config.definition(feature)
        if config is not None and (yield from self._check_definition(config, subject, context)):
            return True

        # 5. Sources. A resource grant only counts while quota remains; a
        #    boolean grant counts on `enabled` alone.
        found = yield from self._grant_for(feature, subject, context)
        if found is not None:
            grant, _ = found
            if grant.type == "resource":
                if not grant.enabled:
                    return False
                if grant.included_quantity is None:
                    return True  # unlimited -- see FeatureGrant's warning
                used = yield self.usage.get_usage(subject, feature, period)
                return max(0, grant.included_quantity - int(used)) > 0
            if grant.enabled:
                return True

        # 6. Default deny.
        return False

    def _remaining(
        self, feature: str, subject: Subject, context: Any, period: BillingPeriod | None
    ) -> Step[int | None]:
        for strategy in self._pre_remaining.values():
            verdict = yield strategy(feature, subject, context)
            if verdict is not None:
                return max(0, _to_int(verdict))

        group_limit = yield from self._group_limit(feature, subject, context)
        source_limit = yield from self._source_limit(feature, subject, context)
        external = _max_nullable(group_limit, source_limit)

        definition = self.registry.definition(feature)
        if definition is not None and definition.type == "resource":
            merged = _with_merged_limit(definition, external)
            return (yield from self._resource_remaining(merged, feature, subject, context, period))

        config = self._config.definition(feature)
        if config is not None and config.type == "resource":
            merged = _with_merged_limit(config, external)
            return (yield from self._resource_remaining(merged, feature, subject, context, period))

        # No definition anywhere, but a group or source supplies a limit --
        # treat it as a resource feature with that limit.
        if external is not None:
            synthetic = Feature(key=feature, type="resource", limit=external)
            return (
                yield from self._resource_remaining(synthetic, feature, subject, context, period)
            )

        return None

    def _enabled(
        self, subject: Subject, context: Any, period: BillingPeriod | None
    ) -> Step[list[str]]:
        out: list[str] = []
        seen: set[str] = set()

        def consider(key: str) -> Step[None]:
            if key in seen:
                return
            seen.add(key)
            if (yield from self._can_access(key, subject, context, period)):
                out.append(key)

        # SIM118 is a false positive: FeatureRegistry.keys() is our own method,
        # named to match the PHP and Node registries, and returns a list.
        for key in self.registry.keys():  # noqa: SIM118
            yield from consider(key)
        for key in self._config.keys():  # noqa: SIM118
            yield from consider(key)
        # Features exposed only through a group still count.
        for group_key in (yield from self._enabled_groups_for(subject, context)):
            for key in self.group_registry.resolved_features(group_key):
                yield from consider(key)
        # ...and features exposed only through a source.
        for source in self._sources:
            for grant in (yield source.grants_for(subject, context)) or ():
                yield from consider(grant.key)

        return out

    def _explain(
        self, feature: str, subject: Subject, context: Any, period: BillingPeriod | None
    ) -> Step[AccessResult]:
        for name, strategy in self._pre_strategies.items():
            verdict = yield strategy(feature, subject, context)
            if verdict is not None:
                return (
                    yield from self._fill(
                        AccessResult(allowed=bool(verdict), source="pre-strategy", reason=name),
                        feature,
                        subject,
                        context,
                        period,
                    )
                )

        if self._gate is not None:
            verdict = yield self._gate(feature, subject, context)
            if verdict is not None:
                return (
                    yield from self._fill(
                        AccessResult(allowed=bool(verdict), source="gate"),
                        feature,
                        subject,
                        context,
                        period,
                    )
                )

        definition = self.registry.definition(feature)
        if definition is not None and (
            yield from self._check_definition(definition, subject, context)
        ):
            return (
                yield from self._fill(
                    AccessResult(allowed=True, source="registry"), feature, subject, context, period
                )
            )

        matching = yield from self._matching_groups(feature, subject, context)
        if matching:
            return (
                yield from self._fill(
                    AccessResult(allowed=True, source="group", reason=",".join(matching)),
                    feature,
                    subject,
                    context,
                    period,
                )
            )

        config = self._config.definition(feature)
        if config is not None and (yield from self._check_definition(config, subject, context)):
            return (
                yield from self._fill(
                    AccessResult(allowed=True, source="config"), feature, subject, context, period
                )
            )

        found = yield from self._grant_for(feature, subject, context)
        if found is not None and found[0].enabled:
            grant, source_name = found
            allowed = yield from self._can_access(feature, subject, context, period)
            name = grant.source or source_name
            return (
                yield from self._fill(
                    AccessResult(allowed=allowed, source=f"source:{name}"),
                    feature,
                    subject,
                    context,
                    period,
                )
            )

        # Nothing enabled. Report the most specific source that DEFINED the
        # feature, even though it did not enable it -- "why is this off?" needs
        # a name, not "none".
        if definition is not None:
            return (
                yield from self._fill(
                    AccessResult(allowed=False, source="registry"),
                    feature,
                    subject,
                    context,
                    period,
                )
            )
        if config is not None:
            return (
                yield from self._fill(
                    AccessResult(allowed=False, source="config"), feature, subject, context, period
                )
            )
        return (
            yield from self._fill(
                AccessResult(allowed=False, source="none"), feature, subject, context, period
            )
        )

    # -- Metering internals ------------------------------------------------

    def _usage_for(self, feature: str, subject: Subject, period: BillingPeriod | None) -> Step[int]:
        used = yield self.usage.get_usage(subject, feature, period)
        return int(used)

    def _add_usage(
        self, feature: str, subject: Subject, amount: int, period: BillingPeriod | None
    ) -> Step[None]:
        yield self.usage.add_usage(subject, feature, amount, period)
        return None

    def _try_consume(
        self,
        feature: str,
        subject: Subject,
        amount: Any,
        context: Any,
        period: BillingPeriod | None,
    ) -> Step[bool]:
        amount = whole_units(amount)
        if amount < 0:
            # A negative "consume" is a refund wearing a disguise, and it would
            # walk straight past every quota check. `decrement` exists for it.
            raise ValueError(
                f"try_consume was given a negative amount ({amount}). Use decrement() to "
                "return quota; a negative consume bypasses the limit it is meant to enforce."
            )

        remaining = yield from self._remaining(feature, subject, context, period)

        if remaining is None:
            # Unlimited is not unmetered: a host that cannot bill what it cannot
            # count is the reason this records rather than short-circuits.
            yield self.usage.add_usage(subject, feature, amount, period)
            return True

        # `remaining` and `used` MUST come from the same period bucket, or the
        # derived limit belongs to neither. Mixing them is the live bug in
        # `fancy-features-js`; see BillingPeriod's docstring.
        used = int((yield self.usage.get_usage(subject, feature, period)))
        limit = remaining + used

        atomic = getattr(self.usage, "try_consume", None)
        if callable(atomic):
            return bool((yield atomic(subject, feature, amount, limit, period)))

        # Non-atomic fallback: correct in one process, TOCTOU-racy across
        # several. A production store implements `try_consume` with a row lock.
        if remaining < amount:
            return False
        yield self.usage.add_usage(subject, feature, amount, period)
        return True

    def _reset_period(self, subject: Subject, period: BillingPeriod) -> Step[None]:
        reset = getattr(self.usage, "reset_period", None)
        if callable(reset):
            yield reset(subject, period)
        return None

    # -- Definition + group + source internals -----------------------------

    def _check_definition(self, definition: Feature, subject: Subject, context: Any) -> Step[bool]:
        if callable(definition.check):
            verdict = yield call_definition_callback(
                definition.check, subject, context, feature=definition.key, field="check"
            )
            return bool(verdict)
        if definition.enabled is not None:
            return (
                yield from self._evaluate(
                    definition.enabled, subject, context, definition.key, "enabled"
                )
            )
        # A bare definition with no gate is on -- mirrors PHP `checkDefinition`.
        return True

    def _evaluate(
        self, value: Any, subject: Subject, context: Any, feature: str, field: str
    ) -> Step[bool]:
        if isinstance(value, bool):
            return value
        if callable(value):
            verdict = yield call_definition_callback(
                value, subject, context, feature=feature, field=field
            )
            return bool(verdict)
        return False

    def _enabled_groups_for(self, subject: Subject, context: Any) -> Step[list[str]]:
        keys: list[str] = []
        if subject is not None:
            for key in (yield self.group_store.list(subject)) or ():
                keys.append(key)
        for key in self.group_registry.keys():  # noqa: SIM118
            if (yield from self.group_registry.is_enabled_by_callable(key, subject, context)):
                keys.append(key)
        return list(dict.fromkeys(keys))

    def _matching_groups(self, feature: str, subject: Subject, context: Any) -> Step[list[str]]:
        matching: list[str] = []
        for group_key in (yield from self._enabled_groups_for(subject, context)):
            if feature in self.group_registry.resolved_features(group_key):
                matching.append(group_key)
        return matching

    def _group_limit(self, feature: str, subject: Subject, context: Any) -> Step[int | None]:
        """MAX ``limit`` override across enabled groups containing the feature."""
        best: int | None = None
        for group_key in (yield from self._matching_groups(feature, subject, context)):
            override = self.group_registry.resolved_overrides(group_key).get(feature)
            if not override or "limit" not in override:
                continue
            raw = override["limit"]
            if callable(raw):
                raw = yield call_definition_callback(
                    raw, subject, context, feature=feature, field="limit"
                )
            value = _to_int(raw)
            if best is None or value > best:
                best = value
        return best

    def _grant_for(
        self, feature: str, subject: Subject, context: Any
    ) -> Step[tuple[FeatureGrant, str] | None]:
        """The first grant for the feature across all sources, with the source's name.

        The name is carried out rather than looked up afterwards: a grant's own
        ``source`` field is optional, and falling back to "whichever source is
        first in the list" would attribute an entitlement to the wrong package
        in ``explain()`` -- the one place whose entire job is saying where a
        verdict came from.
        """
        for source in self._sources:
            for grant in (yield source.grants_for(subject, context)) or ():
                if grant.key == feature:
                    return (grant, source.name)
        return None

    def _source_limit(self, feature: str, subject: Subject, context: Any) -> Step[int | None]:
        """MAX ``included_quantity`` across enabled resource grants; ``None`` if unlimited."""
        best: int | None = None
        saw_unlimited = False
        saw_any = False
        for source in self._sources:
            for grant in (yield source.grants_for(subject, context)) or ():
                if grant.key != feature or not grant.enabled or grant.type != "resource":
                    continue
                saw_any = True
                if grant.included_quantity is None:
                    saw_unlimited = True
                    continue
                value = _to_int(grant.included_quantity)
                if best is None or value > best:
                    best = value
        # An unlimited grant beats any finite one; `None` is the signal, so it
        # can only be returned once something has actually granted.
        if saw_unlimited and saw_any:
            return None
        return best

    def _resource_remaining(
        self,
        definition: Feature,
        feature: str,
        subject: Subject,
        context: Any,
        period: BillingPeriod | None,
    ) -> Step[int | None]:
        if callable(definition.remaining):
            value = yield call_definition_callback(
                definition.remaining, subject, context, feature=feature, field="remaining"
            )
            return None if value is None else _to_int(value)

        raw_limit = definition.limit
        if callable(raw_limit):
            raw_limit = yield call_definition_callback(
                raw_limit, subject, context, feature=feature, field="limit"
            )
        if raw_limit is None:
            return None  # unlimited

        used = yield from self._resource_usage(definition, feature, subject, context, period)
        return max(0, _to_int(raw_limit) - used)

    def _resource_usage(
        self,
        definition: Feature,
        feature: str,
        subject: Subject,
        context: Any,
        period: BillingPeriod | None,
    ) -> Step[int]:
        if callable(definition.usage):
            value = yield call_definition_callback(
                definition.usage, subject, context, feature=feature, field="usage"
            )
            return _to_int(value)
        # The period reaches the store here. In `fancy-features-js` it does not,
        # which is why its `remaining()` reads a different bucket from its
        # `increment()`.
        return int((yield self.usage.get_usage(subject, feature, period)))

    def _fill(
        self,
        partial: AccessResult,
        feature: str,
        subject: Subject,
        context: Any,
        period: BillingPeriod | None,
    ) -> Step[AccessResult]:
        """Decorate a resource feature's result with remaining / limit / used."""
        definition = self.registry.definition(feature) or self._config.definition(feature)
        found = yield from self._grant_for(feature, subject, context)
        is_resource = (definition is not None and definition.type == "resource") or (
            found is not None and found[0].type == "resource"
        )
        if not is_resource:
            return partial

        remaining = yield from self._remaining(feature, subject, context, period)
        used = int((yield self.usage.get_usage(subject, feature, period)))
        return dataclasses.replace(
            partial,
            remaining=remaining,
            limit=None if remaining is None else remaining + used,
            used=used,
        )


# ---------------------------------------------------------------------------


def _to_int(value: Any) -> int:
    """Truncate toward zero -- what PHP's ``(int)`` and JS's ``Math.trunc`` both do.

    A quota that arrived as a float from JSON must land on the same integer in
    all three runtimes; ``round()`` would not, and the disagreement would only
    show at the halves.
    """
    if isinstance(value, bool):
        raise TypeError(f"A quota limit must be a number, not a bool; got {value!r}.")
    return int(value)


def _max_nullable(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _with_merged_limit(definition: Feature, external: int | None) -> Feature:
    """Replace the definition's limit with an external one only if it is higher."""
    if external is None:
        return definition
    current = definition.limit
    if callable(current):
        # A callable limit cannot be compared without invoking it, and invoking
        # it here would run a user callback for a comparison the caller may not
        # need. The external limit wins, which is the generous direction.
        return dataclasses.replace(definition, limit=external)
    resolved = 0 if current is None else _to_int(current)
    if resolved >= external:
        return definition
    return dataclasses.replace(definition, limit=external)


def create_features(
    *,
    features: Mapping[str, Any] | None = None,
    groups: Iterable[FeatureGroup | Mapping[str, Any]] | None = None,
    sources: Iterable[FeatureSource] | None = None,
    usage: UsageStore | None = None,
    group_store: GroupStore | None = None,
    gate: GateResolver | None = None,
    registry: FeatureRegistry | None = None,
    group_registry: FeatureGroupRegistry | None = None,
) -> FeatureManager:
    """Build a :class:`FeatureManager`. The factory mirror of ``createFeatures()``."""
    return FeatureManager(
        features=features,
        groups=groups,
        sources=sources,
        usage=usage,
        group_store=group_store,
        gate=gate,
        registry=registry,
        group_registry=group_registry,
    )
