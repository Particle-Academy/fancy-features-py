"""Every error this package raises, and why each one is an error rather than a
quieter outcome."""

from __future__ import annotations

__all__ = [
    "FeatureAccessDeniedError",
    "FeatureAsyncRequiredError",
    "FeatureCallbackSignatureError",
    "FeatureError",
    "FeatureGroupCycleError",
]


class FeatureError(Exception):
    """Base class for everything this package raises."""


class FeatureAccessDeniedError(FeatureError):
    """The subject has access to none of the required features.

    Carries ``status = 403`` so a web layer can map it without importing a
    framework-specific exception. The message names every key that was tried,
    because "access denied" alone is unactionable in a log.
    """

    status = 403

    def __init__(self, features: list[str]) -> None:
        super().__init__(f"Access denied: requires one of the feature(s) {', '.join(features)}")
        self.features = features


class FeatureCallbackSignatureError(FeatureError, TypeError):
    """A feature-definition callback declares the pre-0.8.0 parameter order.

    Every callback receives ``(subject, context)``. A callback declaring exactly
    three positional parameters was written against ``($feature, $user,
    $context)`` -- the order ``laravel-fms`` used before 0.8.0 and the order
    ``fancy-features-js`` still publishes today.

    Calling it with two arguments would not fail. It would bind ``subject`` to
    the feature-key string, shift every argument by one, and keep returning
    plausible numbers -- which is exactly how a metered allowance stops ever
    running out. So this raises instead of guessing.

    It subclasses :class:`TypeError` because that is what a wrong call signature
    is, and because a host already catching ``TypeError`` around user callbacks
    should keep catching this one.
    """


class FeatureAsyncRequiredError(FeatureError, RuntimeError):
    """A synchronous call reached an adapter or callback that returned an awaitable.

    A coroutine object is truthy and has a length, so storing one and carrying
    on would report a feature as enabled with the check never having run:
    success-shaped, and completely wrong. The message names the ``a``-prefixed
    method to call instead.
    """


class FeatureGroupCycleError(FeatureError, ValueError):
    """Two groups extend each other, or a group extends itself.

    ``extends`` is one level deep by design. A cycle would either recurse
    forever or resolve to a silently truncated feature list depending on where
    the traversal happened to start, and the second is worse.
    """
