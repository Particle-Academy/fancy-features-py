"""Module-level helpers bound to one default manager.

The analog of ``laravel-fms``'s ``helpers.php`` -- ``feature()``,
``can_access_feature()``, ``has_feature()``, ``feature_remaining()``,
``enabled_features()``.

Unlike the PHP globals, which resolve out of the service container, **the
default instance is explicit**: call :func:`configure_features` once at boot.
Python has no container to fall back on, and a helper that silently built an
empty manager would answer "denied" to everything -- a fail-closed default that
looks exactly like a correctly-configured deny.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, overload

from .contract import BillingPeriod, FeatureGroup, FeatureSource, GroupStore, Subject, UsageStore
from .manager import FeatureManager, GateResolver, create_features

__all__ = [
    "can_access_feature",
    "configure_features",
    "enabled_features",
    "feature",
    "feature_remaining",
    "get_default_features",
    "has_feature",
    "set_default_features",
]

_default: FeatureManager | None = None


def set_default_features(manager: FeatureManager | None) -> FeatureManager | None:
    """Install (or clear, with ``None``) the process-wide default manager."""
    global _default
    _default = manager
    return manager


def configure_features(
    *,
    features: Mapping[str, Any] | None = None,
    groups: Iterable[FeatureGroup | Mapping[str, Any]] | None = None,
    sources: Iterable[FeatureSource] | None = None,
    usage: UsageStore | None = None,
    group_store: GroupStore | None = None,
    gate: GateResolver | None = None,
) -> FeatureManager:
    """Create a manager and install it as the default, in one call."""
    manager = create_features(
        features=features,
        groups=groups,
        sources=sources,
        usage=usage,
        group_store=group_store,
        gate=gate,
    )
    set_default_features(manager)
    return manager


def get_default_features() -> FeatureManager:
    if _default is None:
        raise RuntimeError(
            "No default FeatureManager is configured. Call configure_features(...) or "
            "set_default_features(...) at boot. This raises rather than defaulting to an "
            "empty manager, because an empty manager denies everything and that is "
            "indistinguishable from a working configuration that says no."
        )
    return _default


@overload
def feature() -> FeatureManager: ...
@overload
def feature(
    key: str,
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> bool: ...


def feature(
    key: str | None = None,
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> FeatureManager | bool:
    """``feature()`` -> the default manager; ``feature(key, ...)`` -> the access check.

    Mirrors the PHP ``feature($key = null)`` overload.
    """
    manager = get_default_features()
    if key is None:
        return manager
    return manager.can_access(key, subject, context, period)


def can_access_feature(
    key: str,
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> bool:
    return get_default_features().can_access(key, subject, context, period)


def has_feature(
    key: str,
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> bool:
    return get_default_features().has_feature(key, subject, context, period)


def feature_remaining(
    key: str,
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> int | None:
    return get_default_features().remaining(key, subject, context, period)


def enabled_features(
    subject: Subject = None, context: Any = None, period: BillingPeriod | None = None
) -> list[str]:
    return get_default_features().enabled(subject, context, period)
