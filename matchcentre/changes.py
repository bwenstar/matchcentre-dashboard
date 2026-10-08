"""What this build changes, against the document the build before it left.

Nothing here reads a card, works out a figure or decides a rule. Both documents
have already been built by the same code from the same cards; the whole job of
this module is to say, in the words a club uses, which fixtures and which places
on the ladder are not what they were.

Three decisions shape everything below.

**A fixture is a round and two teams**, not a match id. The id comes off the card,
a fixture nobody has played yet has none, and a night re-exported from Match
Centre can come back under a new one -- so keying on it would report a corrected
card as one fixture gone and another arrived. Two cards of one fixture collapse to
one entry here, which is what the dashboard does with them too (F9).

**The comparison is of the figures a reader sees**: sets, games, who won, and the
status. Not the file the card came from, which changes when a club tidies its
folders and means nothing about the tennis; not the derived aggregates, which all
move when one set score does and would turn a single correction into forty lines.
So an old document edited by hand to name no file, or another one, for a card
still in the folder is put right by the rebuild without a line: it is the same
kind of difference, and the document this run writes is built from the cards,
so what it shows is right whatever the old one said. Only a fixture that has
gone is told with the file it came from, because that name is all that is left.

**Absence of a team from the old document is not a movement.** A team that has
just arrived has no place to have come from, and its matches are already listed as
new. Saying "1st -> 1st" or inventing a place it never held would be a claim no
document supports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from .competition import Round, fixture_sort_key
from .discover import inside
from .stats import ladder_state_of

# The order the kinds are printed in, and the order they happened in as far as a
# club is concerned: what arrived, what moved, what went, and what that did to the
# table. Here rather than in the caller so that the report cannot group them one
# way while this module sorts them another.
KINDS = ("new", "changed", "gone", "ladder")

# The figures that decide whether a fixture is the same fixture it was. Sets and
# games as pairs, because "sets 4-0 -> 2-2" is one fact and two halves of it are
# two lines saying half of it each.
FIGURES = ("home_sets", "away_sets", "home_games", "away_games")


class Change(NamedTuple):
    """One line of the report: what kind of change, and what it says.

    A pair rather than a sentence, so the caller can group and cap by kind
    without parsing the words back out of its own output.
    """

    kind: str
    text: str


def since(old: object, new: Mapping) -> tuple[Change, ...]:
    """Every change from *old* to *new*, grouped in :data:`KINDS` order.

    ``()`` when there is no old document, or when what was found in its place is
    not one: a first build, a file half written by a run that died, a document
    edited into something that will not parse. A comparison against a guess is
    worse than no comparison, because the club would act on it.
    """
    if not isinstance(old, Mapping):
        return ()
    before, after = _by_fixture(old), _by_fixture(new)
    order = _fixture_order(new)
    arrived = [Change("new", f"{_subject(after[key])}, {_figures(after[key])}")
               for key in sorted(set(after) - set(before), key=order)]
    moved = []
    for key in sorted(set(before) & set(after), key=order):
        said = _differences(before[key], after[key])
        if said:
            moved.append(Change("changed", f"{_subject(after[key])}, {said}"))
    gone = [Change("gone", f"{_subject(before[key])}, "
                           f"{_provenance(before[key], old, new)}")
            for key in sorted(set(before) - set(after), key=order)]
    return tuple(arrived + moved + gone + _places(old, new))


def _fixture_order(document: Mapping) -> Callable[[tuple[str, ...]], tuple]:
    """The key :func:`since` lists fixtures by: *document*'s draw, in its order.

    The rounds the document lists, in the order the build that wrote it put them,
    which handed back to the key is that order again, so a final the club declared
    under a name of its own is listed where the dashboard lists it (4.71). A round
    only the old document holds is placed by its label.
    """
    draw = {row["number"]: Round(row["number"], is_finals=row["is_finals"])
            for row in document["rounds"]}
    return lambda key: fixture_sort_key(key, draw)


def _provenance(row: Mapping, old: Mapping, new: Mapping) -> str:
    """How a gone fixture's line ends: the file it was last read from, where the
    builder will find it, or that the last build recorded none.

    The document on disk names the card as it was published, ``data/Round2/x.pdf``,
    or, if a build older than that wrote it, as that build's walk spelt it. Either
    way the name is taken inside the folder the document says it was built from,
    and put under the folder this run was given: the one the person reading the
    report typed. As stored when it cannot be placed there, because the
    document's word for it is the only one left.

    And where the document names no file, a clause saying so, because there is
    no path to give. The last build, because that is what wrote the old document,
    under ``--check`` as much as in a plain run, whose heading calls it that.
    """
    source = row.get("source")
    if not _names_a_file(source):
        return "and the last build recorded no file for it"
    meta = old.get("meta")
    folder = meta.get("source_dir") if isinstance(meta, Mapping) else None
    where = inside(source, Path(folder)) if isinstance(folder, str) else None
    if where is None:
        return f"last read from {source}"
    return f"last read from {Path(new['meta']['source_dir']) / where}"


def _names_a_file(source: object) -> bool:
    """Whether an old document's ``source`` is a file's name: text that prints.

    A build writes the path of the card it read, so anything else came from a hand
    or another program. Asked as ``if not source`` it let everything truthy be a
    file: three spaces printed ``last read from`` over nothing, and a list, ``5``,
    ``true`` and a table printed as Python writes them. Nothing but text can be a
    path, and text names a file only with a character in it that prints and is
    not a space. Asked as ``source.strip()`` it let a zero-width space, a
    byte-order mark and a word joiner be a file, since Python does not count them
    as whitespace, and printed ``last read from`` over nothing that shows. So each
    character is asked by its kind and not looked up in a list of those three:
    ``str.isprintable`` is false for every control, format, surrogate,
    private-use and unassigned character and every separator but the space,
    which ``isspace`` takes. Anything else is the clause for a document that
    recorded none. A path with such a character inside it is printed as it is
    stored, because taking the character out would change what the path says.
    Hangul filler and braille blank are a letter and a symbol to Unicode, and a
    lone combining accent or a variation selector is a mark, so each still names
    a file here, though most faces draw nothing for them.
    """
    return isinstance(source, str) and any(
        char.isprintable() and not char.isspace() for char in source)


# --- fixtures ---------------------------------------------------------------


def fixture_of(row: Mapping) -> tuple[str, str, str]:
    """The three things that identify a document row's fixture: round, home, away.

    The teams by id and as the card printed them, home first. One rule for the
    comparison below and for both sides of the report's search for where the
    cards of a dashboard left behind were filed this run (4.457): the stale
    copy's matches and this run's.
    """
    return (str(row.get("round", "")), str(row.get("home", "")),
            str(row.get("away", "")))


def _by_fixture(document: Mapping) -> dict[tuple[str, str, str], Mapping]:
    """Every match in *document*, by the three things that identify a fixture.

    Tolerant of a document that is not one, because the old one comes off disk:
    anything that is not a list of rows is read as a season with no matches in it,
    which reports every fixture as new -- loud, wrong about nothing, and possible
    to act on -- rather than ending the build over a file it only wanted to read.
    """
    rows = document.get("matches")
    if not isinstance(rows, (list, tuple)):
        return {}
    return {fixture_of(row): row for row in rows if isinstance(row, Mapping)}


def _subject(row: Mapping) -> str:
    """``R4 Hilltop Aces v Parkview Wanderers``.

    The same shape the warnings use for a fixture, so a club greps one form for
    both -- but with the names the ladder prints rather than the slugs, because the
    lines this sits among name teams, and two spellings inside one section is
    worse than two across the report.
    """
    return (f"R{row.get('round', '?')} "
            f"{_side(row, 'home')} v {_side(row, 'away')}")


def _side(row: Mapping, side: str) -> str:
    return str(row.get(f"{side}_name") or row.get(side) or "?")


def _figures(row: Mapping) -> str:
    """``sets 4-0, games 24-6``, or the status of a fixture with no figures.

    A bye, a wet night and a fixture nobody has played yet all have nothing to
    print a score from, and the word on the row is what the dashboard shows for
    them. Told apart by the figures themselves rather than by a list of statuses
    kept here, which would be a second copy of the model's own answer to what
    counts as played.
    """
    if any(_is_number(row.get(name)) and row.get(name) for name in FIGURES):
        return f"sets {_pair(row, 'sets')}, games {_pair(row, 'games')}"
    return str(row.get("status") or "no figures recorded")


def _differences(before: Mapping, after: Mapping) -> str:
    """Each figure that moved, old beside new, or ``""`` if none did.

    Each one named, because which figure moved is the whole question: games alone
    means a set was corrected, the result alone means the scoring changed under an
    unchanged card, and the status alone means a played night became a forfeit.
    """
    said = []
    for label in ("sets", "games"):
        was, now = _pair(before, label), _pair(after, label)
        if was != now:
            said.append(f"{label} {was} -> {now}")
    for label, read in (("result", _winner), ("status", _status)):
        was, now = read(before), read(after)
        if was != now:
            said.append(f"{label} {was} -> {now}")
    return ", ".join(said)


def _pair(row: Mapping, figure: str) -> str:
    return f"{_number(row.get(f'home_{figure}'))}-{_number(row.get(f'away_{figure}'))}"


def _winner(row: Mapping) -> str:
    """Who won, by name -- because ``home`` is not an answer to "who won"."""
    result = row.get("result")
    if result in ("home", "away"):
        return _side(row, str(result))
    if result == "draw":
        return "drawn"
    return "no result"


def _status(row: Mapping) -> str:
    return str(row.get("status") or "?")


# --- the ladder -------------------------------------------------------------


def _places(old: Mapping, new: Mapping) -> list[Change]:
    """The teams that changed places, highest new place first.

    Highest first because that is the order of the table it describes, and because
    a club reading two lines wants the top of the ladder in the first one.

    None out of a ladder that had not started (4.428), whose places were no places:
    a document this release writes gives such a ladder none, and one release 0.1.0
    wrote placed its teams by name, so "Riverside Rockets 4th -> 1st" was the
    alphabet and not a move.
    """
    if _had_not_started(old):
        return []
    before, after = _positions(old), _positions(new)
    said = []
    for slug, (place, name) in sorted(after.items(), key=lambda item: item[1][0]):
        was = before.get(slug)
        if was is None or was[0] == place:
            continue
        said.append(Change("ladder",
                           f"{name} {_ordinal(was[0])} -> {_ordinal(place)}"))
    return said


def _had_not_started(document: Mapping) -> bool:
    """Whether *document*'s ladder had not started: its ``meta.ladder_state``, or
    without one, as 0.1.0 wrote none, every team's ``counted`` at nought.

    By :func:`stats.ladder_state_of`, the rule the state is written by. A document
    that says neither, a team row without a count among them, is taken at its places.
    """
    meta = document.get("meta")
    said = meta.get("ladder_state") if isinstance(meta, Mapping) else None
    if isinstance(said, str):
        return said == "empty"
    rows = document.get("teams")
    if not isinstance(rows, (list, tuple)) or not all(
            isinstance(row, Mapping) and isinstance(row.get("counted"), int)
            for row in rows):
        return False
    return ladder_state_of({str(row.get("slug")): row["counted"]
                            for row in rows}) == "empty"


def _positions(document: Mapping) -> dict[str, tuple[int, str]]:
    rows = document.get("teams")
    if not isinstance(rows, (list, tuple)):
        return {}
    return {str(row.get("slug")): (row["position"],
                                   str(row.get("name") or row.get("slug")))
            for row in rows
            if isinstance(row, Mapping) and _is_number(row.get("position"))}


def _ordinal(place: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th``, ``11th``, ``21st``.

    Written here because nothing else in this project prints a place in words: the
    dashboard's ladder is a table whose first column is a number, and a number in
    a column needs no suffix. A division of twenty-four teams is the case the
    ``11th`` line is for.
    """
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(place % 10, "th")
    return f"{place}{'th' if place % 100 in (11, 12, 13) else suffix}"


# --- figures off disk -------------------------------------------------------


def _is_number(value: object) -> bool:
    """A figure, and not a ``bool`` -- which is an ``int`` and is not a score."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value: object) -> str:
    """A figure as it is printed, or ``?`` for anything that is not one.

    ``sets ?-0`` is a line a club can read and report; a TypeError here is this
    program refusing to build tonight's dashboard over a field in last week's
    file, which is the one thing a comparison must never be able to do.
    """
    return f"{value:g}" if _is_number(value) else "?"
