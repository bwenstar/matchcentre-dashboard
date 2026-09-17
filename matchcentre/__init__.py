"""Turn Match Centre scorecard PDFs into an interactive dashboard.

The package is a library; the thing a club runs is ``build.py`` at the top of the
project. Nothing here reaches the network, writes outside the project, or needs
anything that is not in the Python standard library.

The modules, in the order data moves through them::

    pdf         read a PDF's text and drawn shapes, with no third-party reader
    scorecard   turn one page of shapes and text into a Card
    card        what a single scorecard says, and why each field is believed
    format      how many sets, and which are doubles -- read off the card
    slugs       stable identifiers, so two spellings of a name are one player
    model       a Match: two teams, their sets, and the result
    competition a season's teams, rounds and provenance
    config      the club's optional config.toml
    discover    route Cards into competitions and build Matches
    scoring     the ladder rulesets a club can choose between
    stats       ladders, form, head-to-head, per-player figures
    validate    refuse to publish figures that cannot be justified
    export      the season document the page reads
    site        write the dashboard files next to it
    cardwriter  write scorecard PDFs, for the demo and for the tests

Import a module, not a name from here: ``from matchcentre import stats``. Nothing
is re-exported at package level on purpose, so that importing one part of the
project does not drag in the rest.
"""

from __future__ import annotations

import sys

#: This release, and the number a club quotes when reporting a problem. It is
#: printed on the first line of every ``build.py`` report, recorded in each
#: ``season.json``, and shown in the dashboard's footer, so an issue can say which
#: build produced the output it is complaining about without anybody having to ask.
#:
#: It read ``1.0.0`` while ``CHANGELOG.md`` documented ``0.1.0`` and nothing
#: anywhere read the constant, so there was no run in which the disagreement could
#: show up. A test now requires this to equal the newest version the changelog
#: documents.
__version__ = "0.1.0"
__all__ = ["__version__", "REQUIRES_PYTHON"]

#: The oldest Python this code runs on. Set by ``tomllib``, which arrived in 3.11
#: and is how ``config.py`` reads a club's ``config.toml``; ``slots=True`` on the
#: dataclasses needs only 3.10.
#:
#: ``build.py`` carries the same number as ``MINIMUM_PYTHON`` and checks it before
#: importing this package. That duplication is deliberate and must stay: build.py
#: cannot import the constant from here without first importing the package, which
#: is the very thing that would fail. The two are kept honest by a test.
REQUIRES_PYTHON = (3, 11)

if sys.version_info < REQUIRES_PYTHON:
    # Checked here rather than left to fail later, because the failure it replaces
    # is `ModuleNotFoundError: No module named 'tomllib'` raised from inside
    # config.py -- which tells a club nothing about what to do. This is the first
    # code that runs on any import of the project, so the message arrives before
    # anything else can go wrong.
    raise RuntimeError(
        "matchcentre needs Python {}.{} or newer; this is Python {}. "
        "Install a newer Python and run the same command again -- there is "
        "nothing else to install.".format(
            *REQUIRES_PYTHON, ".".join(str(n) for n in sys.version_info[:3])))
