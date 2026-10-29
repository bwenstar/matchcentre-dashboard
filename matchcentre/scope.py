"""Which seasons a run's report lists, when ``--only`` names some.

Nothing here reads a card or decides a figure. ``--only`` shortens the report and
nothing else: every season is still read, checked and written, because a season's
document depends on the whole run -- a refused card anywhere is in every season's
count of refused cards, and a fill-in is found by asking every season who a player
is registered to. So the one question this module answers is whether a line about
one season is printed, and the one question beside it is whether a new one-file
page is added.

**The two answers fail in opposite directions**, which is why they are two
functions and not one. :meth:`Scope.hides` is the report's rule, and a document or
a note that names no season is shown: a report that says too much costs a club a
scroll. :meth:`Scope.covers` is where ``--single-file`` may fold a dashboard, and a
competition that names no season gets no new fold: a fold is kept up to date by
every later run, so one nobody asked for is permanent.

Imports nothing from the rest of the package, so that both rules can be tested on
their own and neither build.py nor site.py grows a second copy of them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["Scope", "EVERY_SEASON", "in_words", "in_words_capped", "choices",
           "echoed", "ordinal"]


def in_words(items: Iterable[object], conjunction: str = "and") -> str:
    """*items* the way a sentence lists them: ``a``, ``a and b``, ``a, b and c``.

    *conjunction* is the last word between two of them, ``or`` where the sentence
    offers a choice (``a, b or c``). The empty string for none, so that a caller
    has to decide what an empty list means rather than printing whatever this made
    of it. Here because this module imports nothing, so the reader, the checks, the
    discovery notes, the report and the tools can each list things by it rather
    than by a copy of their own. Rejected: refusing an empty list, as one of the
    copies this replaced did with an IndexError. No caller could reach it, because
    each of that copy's callers tested for an empty list itself before asking.
    """
    items = [str(item) for item in items]
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def in_words_capped(items: Iterable[object], limit: int) -> str:
    """The first *limit* of *items* by :func:`in_words`, then how many more there are.

    ``a, b, c and 2 more``, or the whole list by :func:`in_words` when it is no
    longer than *limit*. For a sentence that names a few of a list and counts the
    rest, as the report's left-behind reason names a stale copy's fixtures
    (4.457), rather than a copy of the cut at each.
    """
    items = [str(item) for item in items]
    more = len(items) - limit
    return in_words([*items[:limit], *([f"{more} more"] if more > 0 else [])])


def choices(values: Iterable[object]) -> str:
    """The values a setting takes, as its refusal offers them: ``"a", "b" or "c"``.

    Quoted as config.toml writes a string, so one can be copied back into the line,
    and listed by :func:`in_words` with ``or``, as a sentence offering a choice is.
    One wording for ``[build] seasons``, ``[build] players``, ``unplayed`` and a
    ruleset's ``forfeit_award``, which each listed theirs their own way: a Python
    tuple, ``('single', 'multi')``, twice, ``one of`` a list typed out by hand, and
    each value in Python's quotes. Here because scoring cannot import the config,
    and so not quoted by ``config.toml_string``: the values are the program's own
    names, with no quote or backslash to escape. Rejected: ``one of "a" or "b"``,
    which offers the choice twice.
    """
    return in_words((f'"{value}"' for value in values), "or")


# A value is echoed whole up to _ECHOED characters, and past that as its first
# _HEAD and its length.
_ECHOED = 60
_HEAD = 12


def echoed(value: object, *, quoted: bool = False) -> str:
    """*value* as a warning, a note or a refusal repeats it back to the club.

    Whole when it is sixty characters or fewer, and past that its first dozen and its
    length: ``'111111111111...' (5000 characters)``. Every echo was whole, so a
    round labelled with 5000 ones, or an ``after_round`` typed so, was a line of
    5008 or 5017 columns however the sentence round it was wrapped. The start and
    the length find the value in the file, and fit any line. *quoted* gives it as
    ``repr`` does, as the checks on a table quote what was typed, so a line break in
    the start is still one line. What is not text is echoed as ``repr`` writes it.
    Here because this module imports nothing, so the config, discovery, the build
    and the fetcher each cap an echo by it. Rejected: refusing a value this long
    where it is read, which turns a file away for the length of a label. Rejected
    too: forty, which gave ``Round 5 (rescheduled from the washout of 12 Mar)`` as
    its first dozen, the same start as every other round so named. Sixty, quoted,
    still ends inside the width from the report's value column.
    """
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= _ECHOED:
        return repr(value) if quoted else text
    if quoted and isinstance(value, str):
        shown = repr(text[:_HEAD])
        return f"{shown[:-1]}...{shown[-1]} ({len(text)} characters)"
    return f"{text[:_HEAD]}... ({len(text)} characters)"


def ordinal(place: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ``11th``, ``21st``.

    A place in words, which the change list prints of a team that moved and the
    placings of a placing match (4.537); the dashboard's ladder is a table whose
    first column is a number, and a number in a column needs no suffix. A division
    of twenty-four teams is the case the ``11th`` line is for.
    """
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(place % 10, "th")
    return f"{place}{'th' if place % 100 in (11, 12, 13) else suffix}"


@dataclass(frozen=True, slots=True)
class Scope:
    """The season keys ``--only`` named, normalised, once each, in the order given.

    Empty means every season, and is what a run without ``--only`` has.
    """

    only: tuple[str, ...] = ()

    @property
    def narrowed(self) -> bool:
        return bool(self.only)

    def hides(self, key: str | None) -> bool:
        """Whether the report leaves out a line about season *key*.

        Only a non-empty key outside the scope is hidden. Something that cannot be
        placed in a season is listed, because leaving it out on no evidence would
        be a line of the report that is simply gone.
        """
        return bool(self.only) and bool(key) and key not in self.only

    def covers(self, key: str | None) -> bool:
        """Whether this run may add a one-file page to a competition in *key*.

        Under ``--only`` a key that is empty or outside the scope is not covered,
        because the page, once written, is kept by every run after this one.
        """
        return not self.only or (bool(key) and key in self.only)

    def words(self) -> str:
        return in_words(self.only)


# A run without --only. One value rather than ``Scope()`` spelt at each default, so
# that "no scope" is a thing the code can name.
EVERY_SEASON = Scope()
