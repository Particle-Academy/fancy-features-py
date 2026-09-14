"""The `shared/feature-entitlement` table, run against THIS side.

`laravel-fms` and `@particle-academy/fancy-features` run the identical rows from
the identical file. That is the whole mechanism: three runtimes read one table,
so a divergence is a red build in whichever one drifted rather than a support
ticket months later.

## The two rows that carry the weight

**0002** -- an enabled resource grant with zero quota left is still ENTITLED.
Both twins used to answer this one way for a registry feature and the other way
for a catalog-sourced one. An implementation that puts the quota check back fails
this row and 0004, and nothing else.

**0018** -- consumption that starts *above* the included line bills the whole
amount, not the distance from the line. The obvious ``max(0, after - included)``
answers 50 where the truth is 10, re-billing every unit already recorded. That
one is an invoice, not a test failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fancy_conformance import cases, format_summary, run_table, version

from fancy_features.quota import (
    allows_consumption,
    can_consume,
    consumption_ceiling,
    entitled,
    overage_delta,
)

SUITE = "shared/feature-entitlement"

#: Moved deliberately, never automatically. A pin that follows whatever is on
#: disk asserts nothing.
# Moved to 0.22.0 on 2026-09-13, deliberately and not to get to green: the
# entitlement table was re-run against a 0.22.0 checkout FIRST -- 26 passed,
# 0 failed, 0 skipped -- and `suites/shared/feature-entitlement` has no diff
# between v0.20.0 and v0.22.0.
#
# Moved 0.22.0 -> 0.22.1 on 2026-09-13. That release changed no case and no
# golden (the Rust loader pins fancy-json by tag, plus docs); the entitlement
# table was re-run against a v0.22.1 checkout first all the same -- 26 passed,
# 0 failed, 0 skipped, the same counts CI printed at 0.22.0.
#
# Five ports had drifted to a pin this stale at once, which says the failure is
# structural rather than anyone forgetting: the pin only moves when a human
# re-runs the tables, and nothing prompts that when the fixture package ships.
#
# CI checks out `ref: v<this>` from .github/workflows/ci.yml, never `main`. It
# used to take `main`, on the theory that the red on the next fixture release
# would be the prompt; in practice a build sat red for weeks, for a reason no
# commit here caused, and nobody read it. Move the pin and the ref together;
# test_ci_checks_out_the_fixture_tag_this_suite_pins fails otherwise.
PINNED_SUITE_VERSION = "0.22.1"

_IMPL = {
    "entitled": lambda i: entitled(i["enabled"], i["type"], i["includedQuantity"], i["used"]),
    "consumptionCeiling": lambda i: consumption_ceiling(i["includedQuantity"], i["overageLimit"]),
    "allowsConsumption": lambda i: allows_consumption(i["used"], i["amount"], i["ceiling"]),
    "overageDelta": lambda i: overage_delta(i["used"], i["amount"], i["includedQuantity"]),
    "canConsume": lambda i: can_consume(
        i["enabled"], i["includedQuantity"], i["overageLimit"], i["used"], i["amount"]
    ),
}


def test_the_pinned_fixture_version_is_what_is_on_disk() -> None:
    assert version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {version()}, this suite is pinned to "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def _conformance_checkout_refs(workflow: str) -> list[str | None]:
    """The `ref:` of every workflow step that checks out fancy-conformance.

    Plain text on purpose: a YAML parser would be a dependency for one assertion.
    A step is a `- ` line plus everything indented deeper than it. `None` is a
    step with no `ref`, which checks out whatever `main` is at that moment.
    """
    lines = workflow.splitlines()
    refs: list[str | None] = []
    for index, line in enumerate(lines):
        start = re.match(r"(\s*)- ", line)
        if not start:
            continue
        step = [line]
        for following in lines[index + 1 :]:
            body = following.strip()
            indent = len(following) - len(following.lstrip())
            if body and not body.startswith("#") and indent <= len(start.group(1)):
                break
            step.append(following)
        text = "\n".join(step)
        if re.search(
            r"^\s*(- )?repository:\s*[\"']?Particle-Academy/fancy-conformance[\"']?\s*(#.*)?$",
            text,
            re.MULTILINE,
        ):
            ref = re.search(r"^\s*(- )?ref:\s*[\"']?([^\"'\s#]+)", text, re.MULTILINE)
            refs.append(ref.group(2) if ref else None)
    return refs


def test_the_checkout_ref_parser_sees_a_missing_ref() -> None:
    workflow = """
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/fancy-conformance
          path: .fancy-conformance
      - name: Pinned
        uses: actions/checkout@v4
        with:
          repository: "Particle-Academy/fancy-conformance"
          ref: 'v1.2.3'  # a comment
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/laravel-fms
          ref: v9.9.9
    """
    assert _conformance_checkout_refs(workflow) == [None, "v1.2.3"]


def test_ci_checks_out_the_fixture_tag_this_suite_pins() -> None:
    """The CI checkout `ref` and `PINNED_SUITE_VERSION` are one decision in two files.

    CI used to check fancy-conformance out with no `ref`, so every fixture release
    turned this build red at once for a reason no commit here caused, and it sat
    red for weeks. The pin is the contract: moving it is a deliberate commit in
    this repository, never a side effect of someone else's release.
    """
    here = Path(__file__).resolve()
    workflows = next(
        (p / ".github" / "workflows" for p in here.parents if (p / ".github/workflows").is_dir()),
        None,
    )
    assert workflows is not None, f"no .github/workflows above {here}"

    refs = {
        path.name: _conformance_checkout_refs(path.read_text(encoding="utf-8"))
        for path in sorted(workflows.glob("*.y*ml"))
    }
    refs = {name: found for name, found in refs.items() if found}
    # Vacuity guard: a parser that matched nothing would satisfy the loop below.
    assert refs, "no workflow checks out Particle-Academy/fancy-conformance"

    expected = f"v{PINNED_SUITE_VERSION}"
    for name, found in refs.items():
        assert found == [expected] * len(found), (
            f".github/workflows/{name} checks fancy-conformance out at {found}, but this "
            f"suite pins {PINNED_SUITE_VERSION}. Set `ref: {expected}` there, and move "
            "the pin and the ref together."
        )


def test_feature_entitlement_conformance(capsys: pytest.CaptureFixture[str]) -> None:
    summary = run_table(SUITE, lambda case: _IMPL[case["fn"]](case["input"]))
    with capsys.disabled():
        # Printed unconditionally, pass or fail. A summary only shown on failure
        # cannot tell anyone that the suite ran at all.
        print("\n" + format_summary(summary))
    assert summary["ok"], format_summary(summary)


def test_the_conformance_table_is_not_empty() -> None:
    # The vacuity guard, and the one that matters most: a loader that resolves,
    # returns nothing and reports "0 failed" reads exactly like full coverage.
    assert len(cases(SUITE)) >= 26


def test_the_table_cannot_express_a_refusal_so_this_asserts_it_here() -> None:
    """A negative consume walks past every ceiling, so the service refuses it.

    ``allows_consumption(100, -50, 100)`` is legitimately True -- the arithmetic
    is not where the refusal belongs. It belongs at the door, which is why
    ``FeatureManager.try_consume`` raises. See ``test_overage.py``.
    """
    assert allows_consumption(100, -50, 100) is True


def test_booleans_are_not_integers_in_the_grant_shape() -> None:
    """Python-specific, and the reason the loader guards it.

    ``True == 1`` here, so a row expecting ``False`` would be satisfied by an
    implementation returning ``0`` without the loader's guard. These functions
    return real bools; assert it, because a truthy int would pass the table.
    """
    for value in (
        entitled(True),
        allows_consumption(0, 1, None),
        can_consume(True, None, None, 0, 1),
    ):
        assert isinstance(value, bool)


def test_the_arithmetic_functions_return_ints_and_never_floats() -> None:
    """A quota is counted, never measured. A float here is a bug upstream."""
    assert isinstance(consumption_ceiling(100, 50), int)
    assert isinstance(overage_delta(90, 30, 100), int)
