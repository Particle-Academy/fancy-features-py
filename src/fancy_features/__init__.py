"""fancy-features -- headless feature management and metered-resource gating.

The Python twin of ``particle-academy/laravel-fms`` (PHP) and
``@particle-academy/fancy-features`` (Node/TypeScript), and the **owner of the
shared feature contract** that ``fancy-catalog`` consumes.

Nothing here imports a web framework, an ORM, or a billing SDK. Persistence is
behind :class:`~fancy_features.contract.UsageStore` and
:class:`~fancy_features.contract.GroupStore`; entitlements arrive through
:class:`~fancy_features.contract.FeatureSource`; the subject is opaque.

    >>> from fancy_features import create_features
    >>> features = create_features(features={"tokens": {"type": "resource", "limit": 100}})
    >>> features.try_consume("tokens", user, 30)
    True
    >>> features.remaining("tokens", user)
    70

Every method has an ``a``-prefixed twin (``acan_access``, ``aremaining``, ...)
for hosts whose adapters are asynchronous. Both drive one copy of the
resolution rules.
"""

from __future__ import annotations

from .contract import (
    AccessResult,
    AtomicUsageStore,
    BillingPeriod,
    Feature,
    FeatureGrant,
    FeatureGroup,
    FeatureSource,
    FeatureType,
    GroupStore,
    OverageEvent,
    OverageListener,
    OverageStore,
    Subject,
    UsageStore,
)
from .errors import (
    FeatureAccessDeniedError,
    FeatureAsyncRequiredError,
    FeatureCallbackSignatureError,
    FeatureError,
    FeatureGroupCycleError,
)
from .groups import FeatureGroupRegistry, InMemoryGroupStore, default_subject_key, to_group
from .guard import acan_access_any, arequire_feature, can_access_any, require_feature
from .helpers import (
    can_access_feature,
    configure_features,
    enabled_features,
    feature,
    feature_remaining,
    get_default_features,
    has_feature,
    set_default_features,
)
from .manager import (
    FeatureManager,
    GateResolver,
    PreRemainingStrategy,
    PreStrategy,
    create_features,
)
from .quota import (
    allows_consumption,
    can_consume,
    consumption_ceiling,
    entitled,
    overage_delta,
)
from .registry import FeatureRegistry
from .usage import InMemoryUsageStore, whole_units

def _installed_version() -> str:
    """This package's version, read from the INSTALLED distribution metadata.

    Not a literal. A literal here is a second copy of a number that already
    lives in ``pyproject.toml``, and the two drift with nothing comparing them.
    That is not hypothetical in this estate: ``fancy-flow-py`` shipped
    ``__version__ = "0.1.0"`` against a 0.4.0 distribution for three releases,
    and the runtime's first outside consumer installed 0.4.0, read 0.1.0, and
    reported it.

    Reading from metadata removes the second copy rather than re-syncing it, so
    there is nothing left to drift. The fallback covers a source tree that was
    never installed — a case where ``pyproject.toml`` is the only truth and no
    distribution exists to disagree with it.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _distribution_version

    try:
        return _distribution_version("fancy-features")
    except PackageNotFoundError:  # pragma: no cover — an uninstalled source tree
        return "0.0.0+unknown"


__version__ = _installed_version()

__all__ = [
    # -- The shared contract (this package owns it; fancy-catalog imports it) --
    "AccessResult",
    "AtomicUsageStore",
    "BillingPeriod",
    "Feature",
    "FeatureGrant",
    "FeatureGroup",
    "FeatureSource",
    "FeatureType",
    "GroupStore",
    "OverageEvent",
    "OverageListener",
    "OverageStore",
    "Subject",
    "UsageStore",
    # -- Quota arithmetic (pinned by the shared/feature-entitlement suite) --
    "allows_consumption",
    "can_consume",
    "consumption_ceiling",
    "entitled",
    "overage_delta",
    # -- Engine --
    "FeatureManager",
    "GateResolver",
    "PreRemainingStrategy",
    "PreStrategy",
    "create_features",
    # -- Registries and adapters --
    "FeatureGroupRegistry",
    "FeatureRegistry",
    "InMemoryGroupStore",
    "InMemoryUsageStore",
    "default_subject_key",
    "to_group",
    "whole_units",
    # -- Guard --
    "acan_access_any",
    "arequire_feature",
    "can_access_any",
    "require_feature",
    # -- Bound helpers --
    "can_access_feature",
    "configure_features",
    "enabled_features",
    "feature",
    "feature_remaining",
    "get_default_features",
    "has_feature",
    "set_default_features",
    # -- Errors --
    "FeatureAccessDeniedError",
    "FeatureAsyncRequiredError",
    "FeatureCallbackSignatureError",
    "FeatureError",
    "FeatureGroupCycleError",
    "__version__",
]
