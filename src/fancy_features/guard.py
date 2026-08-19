"""The framework-agnostic guard -- the core of the ``RequireFeature`` middleware.

No web framework is imported and none is assumed. A guard that reached for
Flask, Django or Starlette would make this package impossible to use in the
other two, and the actual logic is four lines.

**OR semantics**, matching the PHP middleware: the guard passes if the subject
can access ANY of the keys. For AND, call it once per key -- which is what
"use multiple middleware instances" means on the PHP side.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .contract import BillingPeriod, Subject
from .errors import FeatureAccessDeniedError
from .manager import FeatureManager

__all__ = ["acan_access_any", "arequire_feature", "can_access_any", "require_feature"]


def _keys(keys: str | Sequence[str]) -> list[str]:
    features = [keys] if isinstance(keys, str) else list(keys)
    if not features:
        # Fail closed. Silently allowing traffic past a "require feature" gate
        # is how a typo in a route definition becomes an open door, and the
        # misconfiguration would then never surface.
        raise ValueError("require_feature needs at least one feature key.")
    return features


def require_feature(
    manager: FeatureManager,
    keys: str | Sequence[str],
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> None:
    """Pass if the subject can access any of ``keys``; raise otherwise."""
    features = _keys(keys)
    for key in features:
        if manager.can_access(key, subject, context, period):
            return
    raise FeatureAccessDeniedError(features)


async def arequire_feature(
    manager: FeatureManager,
    keys: str | Sequence[str],
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> None:
    features = _keys(keys)
    for key in features:
        if await manager.acan_access(key, subject, context, period):
            return
    raise FeatureAccessDeniedError(features)


def can_access_any(
    manager: FeatureManager,
    keys: str | Sequence[str],
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> bool:
    """Boolean variant of :func:`require_feature` -- branch instead of aborting.

    An empty key list is still an error here. It is the same misconfiguration,
    and returning ``False`` for it would be a *quiet* denial that looks like a
    working gate.
    """
    return any(manager.can_access(key, subject, context, period) for key in _keys(keys))


async def acan_access_any(
    manager: FeatureManager,
    keys: str | Sequence[str],
    subject: Subject = None,
    context: Any = None,
    period: BillingPeriod | None = None,
) -> bool:
    for key in _keys(keys):
        if await manager.acan_access(key, subject, context, period):
            return True
    return False
