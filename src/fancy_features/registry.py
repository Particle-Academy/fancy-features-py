"""The programmatic feature registry -- the port of ``FmsFeatureRegistry``.

Accepts the same four definition forms the PHP registry does, minus the
container-resolved class-string (Python has no service container to resolve
through, so the class object itself is passed instead).
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from typing import Any

from .contract import Feature

__all__ = ["FeatureRegistry"]

_FEATURE_FIELDS = frozenset(f.name for f in dataclasses.fields(Feature))


class FeatureRegistry:
    """Central registry for code-defined features, boolean and resource.

    A definition may be:

    * a :class:`~fancy_features.contract.Feature`;
    * a mapping, e.g. ``{"type": "resource", "limit": 1000}``;
    * a zero-argument factory returning either of the above;
    * a class or instance exposing ``definition()``.

    The **registration key always wins** over any ``key`` the value carried, so
    a definition copy-pasted between two features cannot silently answer for the
    wrong one.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, Any] = {}

    def register(self, key: str, definition: Any) -> FeatureRegistry:
        self._definitions[key] = definition
        return self

    def has(self, key: str) -> bool:
        return key in self._definitions

    def keys(self) -> list[str]:
        return list(self._definitions)

    def all(self) -> dict[str, Any]:
        """The raw definitions, as registered."""
        return dict(self._definitions)

    def definition(self, key: str) -> Feature | None:
        """Resolve one definition to a :class:`Feature`, or ``None`` if unregistered.

        Resolution is synchronous on purpose. A factory that hits a database to
        decide what a feature *is* would make every access check an I/O call,
        and the per-subject question is what ``check``/``limit``/``usage`` are
        for -- those may be async.
        """
        raw = self._definitions.get(key)
        if raw is None:
            return None
        return _normalise(key, _unwrap(raw))


def _unwrap(raw: Any) -> Any:
    """Reduce a registered value to a :class:`Feature` or a mapping."""
    if isinstance(raw, Feature | Mapping):
        return raw

    definition = getattr(raw, "definition", None)
    if callable(definition):
        # A CLASS exposing `definition()` is instantiated first, matching the
        # PHP registry's container resolution as closely as Python allows;
        # anything else already is an instance and `definition` is bound.
        if inspect.isclass(raw):
            instance: Any = raw()
            return _unwrap(instance.definition())
        return _unwrap(definition())

    if callable(raw):
        return _unwrap(raw())

    return raw


def _normalise(key: str, resolved: Any) -> Feature:
    if isinstance(resolved, Feature):
        return dataclasses.replace(resolved, key=key)

    if not isinstance(resolved, Mapping):
        raise TypeError(
            f"The `{key}` feature definition resolved to {type(resolved).__name__}, which is "
            "neither a Feature, a mapping, nor something with a definition() method."
        )

    fields = dict(resolved)
    fields.pop("key", None)

    unknown = sorted(set(fields) - _FEATURE_FIELDS)
    if unknown:
        # A typo in a config map is otherwise silent, and it fails the wrong
        # way: `{"limits": 10}` defines a resource feature with NO limit, which
        # resolves to unlimited.
        raise TypeError(
            f"The `{key}` feature definition has unknown field(s) {', '.join(unknown)}. "
            f"Valid fields: {', '.join(sorted(_FEATURE_FIELDS - {'key'}))}."
        )

    return Feature(key=key, **fields)
