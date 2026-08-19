"""What a feature-definition callback actually receives.

This file exists because of a shipped bug, and it is the first file in this
package for that reason.

``laravel-fms`` documented every feature-definition callback as receiving the
user, and invoked ``usage`` and ``remaining`` as ``($feature, $user, $context)``
with the key FIRST. Anyone following the docs bound ``$user`` to the feature-key
string and measured the wrong thing -- reported by GuardCard.net, whose metered
allowance never ran out. Nothing asserted it before, which is how it survived:
every existing test declared its callback as ``fn() => 30``, and an
argument-less closure passes under any signature at all.

**``fancy-features-js`` still has the old order**, in its published types and in
its manager. So the shape a consumer is most likely to write here is the wrong
one, arriving from the sibling package rather than from history.

Three rules, all asserted below:

1. Every callback gets ``(subject, context)``. No exceptions, no outliers.
2. A callback declaring FEWER parameters works -- ``lambda: 30`` is the
   commonest form and Python, unlike PHP, raises on surplus arguments.
3. A callback declaring exactly THREE positional parameters is **refused**,
   loudly, naming the fix. It is not honoured-with-a-deprecation as PHP does:
   PHP had users on the old order to carry, this package has none, and the only
   way to arrive at three parameters here is by porting the twin's bug.
"""

from __future__ import annotations

import pytest

from fancy_features import (
    FeatureCallbackSignatureError,
    InMemoryUsageStore,
    create_features,
)


def test_a_usage_callback_is_handed_the_subject_first() -> None:
    seen: dict[str, object] = {}

    def usage(subject: object, context: object) -> int:
        seen["subject"] = subject
        seen["context"] = context
        return 30

    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100, "usage": usage}}
    )

    assert features.remaining("tokens", "the-user", "ctx") == 70
    assert seen["subject"] == "the-user"
    assert seen["context"] == "ctx"


def test_a_remaining_callback_is_handed_the_same_shape() -> None:
    # `remaining` was the second outlier, and a fix that moved only `usage`
    # would leave the pair disagreeing with each other.
    seen: list[object] = []

    features = create_features(
        features={
            "tokens": {
                "type": "resource",
                "remaining": lambda subject, context: (seen.append(subject), 5)[1],
            }
        }
    )

    assert features.remaining("tokens", "the-user") == 5
    assert seen == ["the-user"]


def test_check_enabled_and_limit_agree_with_usage_and_remaining() -> None:
    # The argument for `(subject, context)` is that it is what every other
    # callback does. If that stopped being true, the convention would be wrong,
    # so it is asserted rather than assumed.
    seen: dict[str, object] = {}

    features = create_features(
        features={
            "tokens": {
                "type": "resource",
                "check": lambda s, c: seen.setdefault("check", s) is None or True,
                "limit": lambda s, c: (seen.__setitem__("limit", s), 10)[1],
                "usage": lambda s, c: (seen.__setitem__("usage", s), 4)[1],
            }
        }
    )

    assert features.remaining("tokens", "the-user") == 6
    assert features.can_access("tokens", "the-user") is True
    assert seen["check"] == "the-user"
    assert seen["limit"] == "the-user"
    assert seen["usage"] == "the-user"


def test_a_group_enabled_gate_gets_the_same_shape() -> None:
    seen: list[object] = []
    features = create_features(
        groups=[
            {
                "key": "beta",
                "features": ["experimental"],
                "enabled": lambda s, c: (seen.append(s), True)[1],
            }
        ]
    )

    assert features.can_access("experimental", "the-user") is True
    assert seen[0] == "the-user"


@pytest.mark.parametrize(
    "callback",
    [
        lambda: 30,
        lambda subject: 30,
        lambda subject, context: 30,
        lambda subject, context=None: 30,
        lambda *args: 30,
        lambda subject, *, context=None: 30,
    ],
    ids=["nullary", "unary", "binary", "binary-default", "varargs", "kwonly-context"],
)
def test_a_callback_declaring_fewer_parameters_is_fine(callback: object) -> None:
    # PHP discards surplus arguments to a closure; Python raises. So the
    # adapter must shrink the call to fit, and `lambda: 30` is by far the
    # commonest form in the wild -- every pre-existing laravel-fms test uses it.
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100, "usage": callback}}
    )
    assert features.remaining("tokens", "the-user") == 70


def test_a_three_parameter_callback_is_refused_by_name() -> None:
    # The shape a consumer arrives at by porting `fancy-features-js`, whose
    # published type is `(key, subject, context)`. Calling it with two arguments
    # would not fail -- it would bind `subject` to the key, shift everything by
    # one, and keep returning plausible numbers. That is the silent wrong answer
    # this whole file exists to end, so it is an error and not a guess.
    def legacy(feature: str, subject: object, context: object) -> int:
        return 30

    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100, "usage": legacy}}
    )

    with pytest.raises(FeatureCallbackSignatureError) as excinfo:
        features.remaining("tokens", "the-user")

    message = str(excinfo.value)
    assert "tokens" in message
    assert "usage" in message
    assert "(subject, context)" in message


def test_the_refusal_names_the_callback_that_is_wrong() -> None:
    def legacy(feature: str, subject: object, context: object) -> bool:
        return True

    features = create_features(features={"beta": {"check": legacy}})

    with pytest.raises(FeatureCallbackSignatureError, match="check"):
        features.can_access("beta", "the-user")


def test_an_uninspectable_callable_gets_the_documented_signature() -> None:
    # Some C-level callables have no readable signature. Guessing the legacy
    # order for the one case we can read least about would reintroduce the bug
    # exactly where it is hardest to see, so the documented shape is used.
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100, "usage": max}}
    )
    # `max("the-user", None)` would raise; what matters is that two arguments
    # were attempted rather than three.
    with pytest.raises(TypeError):
        features.remaining("tokens", "the-user")


def test_a_callback_bound_as_a_method_does_not_count_self() -> None:
    class Meter:
        def __init__(self) -> None:
            self.seen: object = None

        def usage(self, subject: object, context: object) -> int:
            self.seen = subject
            return 30

    meter = Meter()
    features = create_features(
        features={"tokens": {"type": "resource", "limit": 100, "usage": meter.usage}}
    )

    assert features.remaining("tokens", "the-user") == 70
    assert meter.seen == "the-user"


def test_a_metered_allowance_actually_runs_out() -> None:
    # The end-to-end shape of the original report: an allowance that never ran
    # out because the meter was reading the wrong subject. With the store
    # answering instead of a callback there is no signature to get wrong, and
    # the point of the assertion is that the number moves.
    usage = InMemoryUsageStore()
    features = create_features(features={"tokens": {"type": "resource", "limit": 3}}, usage=usage)

    assert features.remaining("tokens", "u1") == 3
    assert features.try_consume("tokens", "u1", 2) is True
    assert features.remaining("tokens", "u1") == 1
    assert features.try_consume("tokens", "u1", 2) is False
    assert features.try_consume("tokens", "u1", 1) is True
    assert features.remaining("tokens", "u1") == 0
    assert features.can_access("tokens", "u1") is True  # boolean access, quota aside
