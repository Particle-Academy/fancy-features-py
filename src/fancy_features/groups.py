"""Feature groups and the assignment adapter.

Port of ``FmsFeatureGroupRegistry`` plus the ``FeatureGroup`` value object's
``isEnabledByCallable`` / ``overrideFor`` helpers, and of the
``feature_group_assignments`` pivot behind a :class:`~fancy_features.contract.GroupStore`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from ._drive import Step, call_definition_callback
from .contract import FeatureGroup, Subject
from .errors import FeatureGroupCycleError

__all__ = ["FeatureGroupRegistry", "InMemoryGroupStore", "default_subject_key", "to_group"]

_GROUP_FIELDS = frozenset(f.name for f in dataclasses.fields(FeatureGroup))


def to_group(value: FeatureGroup | Mapping[str, Any]) -> FeatureGroup:
    """Coerce a mapping (the ``config/fms.php`` ``groups`` shape) to a :class:`FeatureGroup`."""
    if isinstance(value, FeatureGroup):
        return value

    fields = dict(value)
    unknown = sorted(set(fields) - _GROUP_FIELDS)
    if unknown:
        raise TypeError(
            f"Feature group `{fields.get('key', '?')}` has unknown field(s) "
            f"{', '.join(unknown)}. Valid fields: {', '.join(sorted(_GROUP_FIELDS))}."
        )
    if "key" not in fields:
        raise TypeError("A feature group needs a `key`.")

    for name in ("features", "extends"):
        if name in fields:
            fields[name] = tuple(fields[name])

    return FeatureGroup(**fields)


class FeatureGroupRegistry:
    """Holds :class:`FeatureGroup` objects and resolves the ``extends`` chain.

    ``extends`` is **one level deep**, with cycle detection, so a consumer sees a
    flat feature set and a flat override map per group. Both resolutions are
    cached, and ``register`` clears the caches -- a stale cache would serve the
    pre-registration answer forever, which is the kind of bug that only shows up
    in a long-lived process.
    """

    def __init__(self) -> None:
        self._groups: dict[str, FeatureGroup] = {}
        self._features_cache: dict[str, list[str]] = {}
        self._overrides_cache: dict[str, dict[str, Mapping[str, Any]]] = {}

    def register(self, group: FeatureGroup | Mapping[str, Any]) -> FeatureGroupRegistry:
        resolved = to_group(group)
        self._groups[resolved.key] = resolved
        self._features_cache.clear()
        self._overrides_cache.clear()
        return self

    def has(self, key: str) -> bool:
        return key in self._groups

    def get(self, key: str) -> FeatureGroup | None:
        return self._groups.get(key)

    def keys(self) -> list[str]:
        return list(self._groups)

    def all(self) -> dict[str, FeatureGroup]:
        return dict(self._groups)

    def resolved_features(self, key: str) -> list[str]:
        """Own features plus every extended group's features, deduplicated, order-stable."""
        cached = self._features_cache.get(key)
        if cached is not None:
            return cached

        group = self._groups.get(key)
        if group is None:
            return []

        features = list(group.features)
        for ext_key in group.extends:
            self._guard_cycle(key, ext_key)
            ext = self._groups.get(ext_key)
            if ext is None:
                continue
            # One level deep -- no transitive expansion.
            features.extend(ext.features)

        unique = list(dict.fromkeys(features))
        self._features_cache[key] = unique
        return unique

    def resolved_overrides(self, key: str) -> dict[str, Mapping[str, Any]]:
        """Overrides merged from ``extends``; own overrides win (closest to the leaf)."""
        cached = self._overrides_cache.get(key)
        if cached is not None:
            return cached

        group = self._groups.get(key)
        if group is None:
            return {}

        merged: dict[str, dict[str, Any]] = {}
        for ext_key in group.extends:
            self._guard_cycle(key, ext_key)
            ext = self._groups.get(ext_key)
            if ext is None:
                continue
            _merge_into(merged, ext.overrides)
        _merge_into(merged, group.overrides)

        result: dict[str, Mapping[str, Any]] = dict(merged)
        self._overrides_cache[key] = result
        return result

    def groups_containing(self, feature: str) -> list[str]:
        return [key for key in self._groups if feature in self.resolved_features(key)]

    def is_enabled_by_callable(self, key: str, subject: Subject, context: Any = None) -> Step[bool]:
        """Whether a group's ``enabled`` gate resolves truthy for the subject.

        A generator, because the gate may be a coroutine function. ``None``
        means "no gate" and the group is then enabled only by assignment.
        """
        group = self._groups.get(key)
        if group is None or group.enabled is None:
            return False
        if isinstance(group.enabled, bool):
            return group.enabled
        if callable(group.enabled):
            verdict = yield call_definition_callback(
                group.enabled, subject, context, feature=key, field="enabled"
            )
            return bool(verdict)
        return False

    def _guard_cycle(self, source_key: str, ext_key: str) -> None:
        if source_key == ext_key:
            raise FeatureGroupCycleError(
                f"[fancy-features] feature group `{source_key}` cannot extend itself"
            )
        ext = self._groups.get(ext_key)
        if ext is not None and source_key in ext.extends:
            raise FeatureGroupCycleError(
                f"[fancy-features] feature group cycle detected: `{source_key}` and "
                f"`{ext_key}` extend each other"
            )


def _merge_into(base: dict[str, dict[str, Any]], incoming: Mapping[str, Mapping[str, Any]]) -> None:
    """Per-feature shallow merge -- the ``array_replace_recursive`` analog."""
    for feature, override in incoming.items():
        base.setdefault(feature, {}).update(override)


def default_subject_key(subject: Subject) -> str:
    """Identify a subject by ``.id`` when it has one, else by its string form.

    Deliberately the same rule as the Node twin's ``defaultSubjectKey``, so a
    host that keys usage rows the same way in both runtimes gets the same
    buckets.
    """
    identifier = getattr(subject, "id", None)
    if identifier is not None:
        return str(identifier)
    if isinstance(subject, Mapping) and "id" in subject:
        return str(subject["id"])
    return str(subject)


class InMemoryGroupStore:
    """The default :class:`~fancy_features.contract.GroupStore`.

    Good enough for tests and single-process apps; swap in a database-backed
    store in production. Assignment is idempotent, matching the PHP trait's
    ``firstOrCreate``.
    """

    def __init__(self, key_of: Any = None) -> None:
        self._assignments: dict[str, set[str]] = {}
        self._key_of = key_of or default_subject_key

    def list(self, subject: Subject) -> list[str]:
        return sorted(self._assignments.get(self._key_of(subject), set()))

    def assign(self, subject: Subject, group_key: str) -> None:
        self._assignments.setdefault(self._key_of(subject), set()).add(group_key)

    def detach(self, subject: Subject, group_key: str) -> None:
        self._assignments.get(self._key_of(subject), set()).discard(group_key)

    def sync(self, subject: Subject, group_keys: Sequence[str]) -> None:
        self._assignments[self._key_of(subject)] = set(group_keys)

    def has(self, subject: Subject, group_key: str) -> bool:
        return group_key in self._assignments.get(self._key_of(subject), set())
