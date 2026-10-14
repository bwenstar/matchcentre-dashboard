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

import os
import sys
from pathlib import Path

#: This release, and the number a club quotes when reporting a problem. It is
#: printed on the first line of every ``build.py`` report, recorded in each
#: ``season.json``, and shown in the dashboard's footer, so an issue can say which
#: build produced the output it is complaining about without anybody having to ask.
#:
#: The printed forms of it all come from ``version_line()`` at the foot of this
#: module, so that the line a club is asked to paste reads the same whichever
#: command printed it. ``README.md`` says what moving this number promises.
#:
#: It read ``1.0.0`` while ``CHANGELOG.md`` documented ``0.1.0`` and nothing
#: anywhere read the constant, so there was no run in which the disagreement could
#: show up. A test now requires this to equal the newest version the changelog
#: documents.
__version__ = "0.2.0"
__all__ = ["__version__", "NAME", "REQUIRES_PYTHON", "version_line", "WINDOWS",
           "pasteable", "pasteable_path"]

#: The project's name, as the version line and a release's archive spell it.
#: ``tools/release.py`` names the zip it publishes ``NAME-X.Y.Z.zip`` and
#: finds every example of the version line by it, and an entry point may not
#: spell the line out for itself, so the name is read from here as the format
#: is.
NAME = "matchcentre-dashboard"

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


def version_line() -> str:
    """This build in one line: which release, which Python, which platform.

    The first line of every ``build.py`` report, and the whole of what each command
    prints for ``--version``. That second use is why it lives here and not in
    ``build.py``: the project has several programs and a parser in each of them,
    which is as many chances to describe the same build differently -- and "please
    paste the version" would then get a different answer depending on which command
    a club happened to run. One function owns the format and the parsers ask for it.

    The interpreter and the platform are on the line because *works here, not
    there* is answered by that pair and by nothing else in the report. Both are read
    when the line is built rather than when this module is imported, which is the
    same thing in every real run and not the same thing under a test.

    Deliberately a function and not a constant, although nothing in it changes
    during a run: a constant is built on every import of this package, including
    the imports of one module by another that have no interest in the line.
    """
    python = ".".join(str(part) for part in sys.version_info[:3])
    return f"{NAME} {__version__}  (python {python} on {sys.platform})"


#: Whether the lines this package quotes for a club to paste are for Windows, where
#: cmd reads double quotes and no single quote. Read by ``pasteable`` when it is
#: called, as ``version_line`` reads the platform, so that a test can ask what a
#: Windows machine prints. A constant and not ``os.name`` asked there, because
#: ``os.name`` cannot be set under a test: pathlib reads it to choose its class, and
#: set to ``nt`` on Linux, ``Path("x").resolve()`` raises ``NotImplementedError:
#: cannot instantiate 'WindowsPath' on your system``.
WINDOWS = os.name == "nt"


def pasteable(path: str, *, windows: bool | None = None) -> str:
    """A word for a command a club will paste, in single quotes when it needs them.

    Here rather than in ``build.py`` for the reason ``version_line`` is: more than
    one program prints a command to paste. The build's hints, the ``next`` line
    that ``tools/demo.py`` and ``tools/fetch.py`` print, the fetcher's
    ``--letterhead`` lines and the cron line ``tools/scheduled.py`` prints all name
    something a shell will read, and one folder should come out the same way
    whichever program printed it. Quoted only when it holds something besides
    letters, digits and ``./_-``, so that the ordinary line reads as a club would
    type it.

    Single quotes, because inside them a POSIX shell reads nothing at all. It was
    double quotes, which keep a space inside the one word and leave ``$``, a
    backtick, ``"`` and ``\\`` live, and ``!`` to an interactive bash: a folder
    named ``o$x`` was pasted as ``oEXPANDED``, and ``a"b`` as an unterminated
    string. A single quote inside is the one thing single quotes cannot hold, so
    it closes them, is given with a backslash, and opens them again: ``'\\''``. An
    empty word is ``''`` and not nothing, or the flag before it would take the
    next word as its own.

    On Windows the same word goes in the double quotes it always had, because
    neither cmd nor a Windows program splitting its own command line reads a single
    quote: ``--letterhead 'QUARRY QUOLLS TENNIS CLUB'`` reaches a Windows Python as
    four words, ``'QUARRY`` to ``CLUB'``. ``windows`` is for a line whose shell is
    known whatever machine prints it: ``tools/scheduled.py``'s cron line is POSIX,
    and asks for it.

    Nor is a path that begins with a dash given a ``./`` here, because the build
    also hands this a season's name to echo; ``pasteable_path`` sees to it.
    """
    if path and all(c.isalnum() or c in "./_-" for c in path):
        return path
    if WINDOWS if windows is None else windows:
        return f'"{path}"'
    return "'" + path.replace("'", "'\\''") + "'"


def pasteable_path(path: str | Path, *, project: str | Path | None = None) -> str:
    """A folder or a file handed to a flag, in a command a club will paste.

    One function for every such path, whichever program prints the command,
    because the two that printed one each had a rule of their own and each rule
    had a hole the other had closed. A name that begins with a dash is given its
    ``./``: ``Path("./-cards")`` prints as ``-cards``, and ``--data -cards`` is
    ``expected one argument``. ``tools/demo.py`` added it and the build's hints
    did not.

    With ``project``, the command runs there after a ``cd``, so the path is
    resolved against the working directory the club typed it in, and named from
    the project when it is inside it and in full when it is not. Without it, the
    command runs where the club is standing and the path is left as it was typed.
    Resolved and not only made absolute, so that ``..`` and a link both name the
    folder they reach. Quoted by ``pasteable``, looked up when it is called.
    """
    path = Path(path)
    if project is not None:
        project = Path(project).resolve()
        path = path.resolve()
        if path.is_relative_to(project):
            path = path.relative_to(project)
    name = str(path)
    if name.startswith("-"):
        name = f"./{name}"
    return pasteable(name)
