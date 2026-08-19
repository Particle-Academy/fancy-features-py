"""One copy of the rules, driven two ways -- plus the callback-arity adapter.

Internal. Nothing here is part of the public API.

## Why a generator

Every adapter in the contract may be synchronous or asynchronous: a
``UsageStore`` backed by a dict returns an ``int``, one backed by a database
returns a coroutine. The Node twin solved this by making the whole surface
``async``, which Python cannot copy -- a Django view or a Celery task calling
``await features.can_access(...)`` is not an option, and shipping a synchronous
and an asynchronous copy of the resolution chain means two copies of the rules,
of which only one is ever really tested.

So the resolution chain is written **once**, as a generator. It ``yield``s any
value that might be awaitable and is sent the resolved value back.
:func:`drive_sync` refuses awaitables; :func:`adrive` awaits them. Both drive
the same generator, so behaviour lives in exactly one place.

This is the pattern ``fancy-flow-py`` established for its engine walk
(``FlowRunner._walk`` with ``run()`` / ``arun()`` drivers). Add behaviour to the
generators, never to a driver.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator
from typing import Any, TypeVar

from .errors import FeatureAsyncRequiredError, FeatureCallbackSignatureError

_T = TypeVar("_T")

#: A resolution step: yields a maybe-awaitable, is sent the resolved value.
Step = Generator[Any, Any, _T]


def drive_sync(gen: Step[_T], *, sync_name: str, async_name: str) -> _T:
    """Run a resolution generator synchronously, refusing any awaitable.

    ``sync_name`` / ``async_name`` are only used to build the error message. The
    caller knows which method the user actually invoked; the generator does not.
    """
    sent: Any = None
    try:
        while True:
            value = gen.send(sent)
            if inspect.isawaitable(value):
                _close_awaitable(value)
                gen.close()
                raise FeatureAsyncRequiredError(
                    f"{sync_name}() reached an adapter or callback that returned an awaitable. "
                    f"Call {async_name}() instead, or supply a synchronous implementation. "
                    "A coroutine object is truthy, so treating it as the answer would report a "
                    "verdict the check never produced."
                )
            sent = value
    except StopIteration as stop:
        return stop.value  # type: ignore[no-any-return]


async def adrive(gen: Step[_T]) -> _T:
    """Run a resolution generator, awaiting anything awaitable.

    Deliberately does not REQUIRE awaitables: a synchronous in-memory store must
    stay usable from an async host without a second implementation.
    """
    sent: Any = None
    try:
        while True:
            value = gen.send(sent)
            if inspect.isawaitable(value):
                value = await value
            sent = value
    except StopIteration as stop:
        return stop.value  # type: ignore[no-any-return]


def _close_awaitable(value: Any) -> None:
    """Close a coroutine we are about to abandon, so Python does not warn about it.

    The warning would be correct but would arrive detached from the error that
    caused it, which turns one clear failure into two confusing ones.
    """
    close = getattr(value, "close", None)
    if callable(close):
        close()


# ---------------------------------------------------------------------------
# The callback-arity adapter
# ---------------------------------------------------------------------------


def positional_arity(fn: Callable[..., Any]) -> int | None:
    """How many positional parameters ``fn`` declares, or ``None`` if unknowable.

    ``None`` means either that the signature cannot be read (some C-level
    callables) or that the callable takes ``*args`` and will accept whatever it
    is handed.

    Counts optional positional parameters too, matching PHP's
    ``ReflectionFunction::getNumberOfParameters()`` -- so ``lambda s, c=None,
    x=None: ...`` counts as three and is refused. That is a false positive in
    principle; in practice a third positional parameter on a feature callback is
    the legacy order far more often than it is a genuine default, and the peer
    that this is matched against draws the line in the same place.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return None

    total = 0
    for parameter in signature.parameters.values():
        if parameter.kind is parameter.VAR_POSITIONAL:
            return None
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            total += 1
    return total


def call_definition_callback(
    fn: Callable[..., Any],
    subject: Any,
    context: Any,
    *,
    feature: str,
    field: str,
) -> Any:
    """Invoke a feature-definition callback as ``(subject, context)``.

    That is the convention for **every** callback in this package -- ``check``,
    ``enabled``, ``limit``, ``usage``, ``remaining`` and a group's ``enabled``
    gate alike. The feature key is redundant in the first place: the callback is
    written inside ``features[<key>]``, so the key is already known where it is
    defined.

    Two adaptations, each with a reason:

    **Fewer parameters is fine.** PHP silently discards surplus arguments to a
    closure, so ``fn() => 30`` is idiomatic there and appears in every existing
    ``laravel-fms`` test. Python raises instead, so the call is shrunk to fit.

    **Exactly three positional parameters is refused.** See
    :class:`~fancy_features.errors.FeatureCallbackSignatureError`.
    """
    arity = positional_arity(fn)

    if arity == 3:
        raise FeatureCallbackSignatureError(
            f"The `{feature}` feature's `{field}` callback declares three positional "
            "parameters, which is the pre-0.8.0 `(feature, subject, context)` order -- still "
            "what `@particle-academy/fancy-features` publishes. Change it to "
            "`(subject, context)`: the feature key is already known where the callback is "
            "defined. This is refused rather than deprecated because calling it with two "
            "arguments would bind `subject` to the key string and keep returning plausible "
            "numbers, which is how a metered allowance stops running out."
        )

    if arity is None or arity >= 2:
        return fn(subject, context)
    if arity == 1:
        return fn(subject)
    return fn()
