"""``__version__`` must be the version that was actually installed.

This is not a style point. Across this estate every package's version surface
had drifted from the number it ships as, and none of them reported it: PHP
constants stale against their own CHANGELOG, Node constants stale against their
own ``package.json``, and here a literal stale against ``pyproject.toml``.

``fancy-flow-py`` is the one that reached a user. It shipped
``__version__ = "0.1.0"`` against a 0.4.0 distribution for three releases; the
runtime's first outside consumer installed 0.4.0, read 0.1.0, and reported it.
Anything gating on the version at runtime would have branched on a release that
no longer existed.

The fix removes the second copy rather than re-syncing it: ``__version__`` is
read from the installed distribution metadata, so there is no longer a number
that CAN drift. These tests pin that PROPERTY, not the current value — asserting
the literal would recreate the very duplicate being deleted.

**The last test is the one that matters, and it runs everywhere.** The first two
compare values and therefore need an installed distribution; a bare source tree
has none, and CI installs one (``pip install -e .``) so they run there. The
shape check needs nothing installed, because what it guards is someone typing
the literal back in — which is how this happened the first time.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest

import fancy_features

DISTRIBUTION = "fancy-features"


def _installed() -> str | None:
    try:
        return distribution_version(DISTRIBUTION)
    except PackageNotFoundError:
        return None


def test_version_matches_the_installed_distribution() -> None:
    installed = _installed()
    if installed is None:
        pytest.skip(
            f"{DISTRIBUTION} is not installed in this environment, so there is no "
            "distribution metadata to compare against. CI installs the package "
            "(`pip install -e .`) before running these, so this assertion does run "
            "there — it is skipped here, not passing here."
        )

    assert fancy_features.__version__ == installed


def test_version_matches_pyproject() -> None:
    """The end-to-end claim, in the terms a release actually happens in.

    The test above compares the package to its own metadata, which agree by
    construction once the read is dynamic. This one compares it to the file a
    human edits when they cut a release — the only place the number is
    authored — so it fails if an editable install goes stale as well.
    """
    if _installed() is None:
        pytest.skip(
            f"{DISTRIBUTION} is not installed, so `__version__` is the deliberate "
            "uninstalled-tree fallback rather than a real version."
        )

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert fancy_features.__version__ == declared, (
        f"fancy_features.__version__ is {fancy_features.__version__!r} but pyproject.toml declares "
        f"{declared!r}. If these disagree, reinstall — and if they disagree after a "
        "reinstall, the dynamic read has been replaced by a literal again."
    )


def test_version_is_not_a_hardcoded_literal() -> None:
    """Guard the FIX, not just its result.

    Someone re-introducing ``VERSION = "1.2.3"`` would make both tests above
    pass on the day they wrote it, and drift again on the next release. That is
    exactly how this bug happened the first time, so the shape is asserted
    directly — and unlike them, this needs nothing installed, so it is the one
    that actually holds the line in a working tree.

    Deliberately not a regex: the pattern needs both quote characters inside a
    character class, which is three escaping layers deep and was written wrong
    twice while this file was being added. A string comparison has no escaping
    layer to get wrong.
    """
    for source in (Path(fancy_features.__file__).read_text(encoding="utf-8"),):
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("__version__ =", "VERSION =")):
                continue

            value = stripped.split("=", 1)[1].strip()
            assert not value.startswith(('"', "'")), (
                f"`{stripped}` assigns a string literal. Read the version from the "
                "installed distribution metadata instead — a literal is a second "
                "copy of pyproject.toml's number, and second copies drift silently."
            )
