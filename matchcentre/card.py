"""What one scorecard page says, before anything is decided about it.

This is the seam between reading PDFs and building a season. :mod:`scorecard`
produces a :class:`Card` — a faithful transcription of one page, in the page's own
vocabulary, with names exactly as printed and no slugs, no scoring and no
competition attached. :mod:`discover` turns cards into a :class:`~matchcentre.model.Match`.

Keeping the seam here has one concrete payoff: a card can be *refused*. The
predecessor's reader had no way to say "I do not believe this page", so when its
column grid failed it substituted hardcoded pixel positions and returned a
confidently reversed scoreline. :class:`Rejection` is that missing return value,
and :class:`CrossCheck` is the evidence a card has to produce before its numbers
are trusted.
"""

from __future__ import annotations

import datetime
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from .model import SetStatus
from .scope import in_words

__all__ = ["Card", "ParsedSet", "RosterEntry", "CrossCheck", "Rejection",
           "CardError", "Totals", "WEEKDAYS", "weekday_of", "DATE_FORMS",
           "date_forms", "letterhead_key", "letterhead_spellings",
           "majority_letterhead", "round_key", "states_no_result", "verb_key",
           "verb_side"]


# Weekday names, indexed by ``date.weekday()``. Spelled out rather than taken
# from ``strftime("%A")`` because that is locale-dependent, and a competition id
# built from it would change with the machine's language settings — output has to
# be byte-identical across runs (CONTRACT §0). Here, at the seam, because three
# things need the one spelling: the reader naming the weekday a card prints, the
# filing comparing it with the date's, and the doctor reporting both.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")

# The forms a card's date is read in: the notation a message names each by, and
# one date printed that way, all of them the same day. The notation is what the
# reader's warning and the filing's refusal quote to a club, rather than the
# example, because a message that lists dates reads as the dates it found, and
# because the doctor's redacted report shapes every date in it, which left the
# list naming none of them. A test reads every example back through the reader,
# so this cannot offer a club a form the reader then refuses.
DATE_FORMS = (
    ("DD/MM/YYYY", "18/08/2026"),
    ("DD-MM-YYYY", "18-08-2026"),
    ("DD.MM.YYYY", "18.08.2026"),
    ("DD/MM/YY", "18/08/26"),
    ("D Month YYYY", "18 August 2026"),
    ("D Mon YYYY", "18 Aug 2026"),
    ("YYYY-MM-DD", "2026-08-18"),
)


def date_forms() -> str:
    """:data:`DATE_FORMS` as one phrase, for a message that has to name them."""
    names = [name for name, _ in DATE_FORMS]
    return f"{in_words(names, 'or')}, with YY as 20YY"


def weekday_of(date: str) -> str:
    """The weekday of an ISO date, or ``""`` for anything that is not one."""
    try:
        return WEEKDAYS[datetime.date.fromisoformat(date).weekday()]
    except ValueError:
        return ""


class CardError(Exception):
    """This page cannot be read as a scorecard. Always carries a reason a human
    can act on: which structure was missing, and where it was looked for."""

    def __init__(self, reason: str, *, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason} ({detail})")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ParsedSet:
    """One set row, transcribed.

    ``doubles`` comes from the row's own marker column — ``1&2`` is a pairing,
    a bare ``1`` is singles — so the match format is read off the card rather than
    assumed. ``marker`` is kept verbatim for diagnostics.
    """

    index: int
    doubles: bool
    marker: str
    home_games: int
    away_games: int
    home_players: tuple[str, ...] = ()
    away_players: tuple[str, ...] = ()
    status: SetStatus = SetStatus.PLAYED
    home_forfeit: bool = False
    away_forfeit: bool = False
    # Which side's ``Retired`` box this row ticks: the side that retired, read off
    # the box since it cannot be read off the games (4.422).
    home_retired: bool = False
    away_retired: bool = False
    # Name cells on this row that print Match Centre's ``Unspecified player``
    # rather than a person, and so produced no name above. Counted rather than
    # discarded so that one warning per card can say how much of it was never
    # filled in; read as a name it would become a player with a playing record.
    # Kept per side, so a side is held to its own cells and the warning can say
    # which side to fix (4.448); ``unnamed`` is the row's.
    home_unnamed: int = 0
    away_unnamed: int = 0

    @property
    def unnamed(self) -> int:
        return self.home_unnamed + self.away_unnamed

    def unnamed_for(self, side: str) -> int:
        return self.home_unnamed if side == "home" else self.away_unnamed

    def players_for(self, side: str) -> tuple[str, ...]:
        return self.home_players if side == "home" else self.away_players


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """A registered player as the card's roster block prints them.

    ``name`` is the printed, properly-cased spelling. The predecessor slugged
    this and discarded the original, which is why names with internal capitals or
    hyphens could not be shown correctly anywhere in the dashboard. Keep it.
    """

    name: str
    singles: float | None = None
    doubles: float | None = None
    is_captain: bool = False


@dataclass(frozen=True, slots=True)
class Totals:
    """The card's own printed TOTAL row, if it has one."""

    home_sets: int | None = None
    home_games: int | None = None
    away_games: int | None = None
    away_sets: int | None = None

    @property
    def present(self) -> bool:
        return any(v is not None for v in
                   (self.home_sets, self.home_games, self.away_games,
                    self.away_sets))

    def describe(self) -> str:
        """``sets 4-0 games 24-0``, a cell the card leaves blank written ``blank``.

        ``sets 3-0 games blank`` where both games cells are.
        """
        def pair(label: str, home: int | None, away: int | None) -> str:
            if home is None and away is None:
                return f"{label} blank"
            return (f"{label} {'blank' if home is None else home}-"
                    f"{'blank' if away is None else away}")
        return (f"{pair('sets', self.home_sets, self.away_sets)} "
                f"{pair('games', self.home_games, self.away_games)}")

    def differs_on(self, other: Totals) -> list[str]:
        """The cells printed here that *other* does not total, by name.

        In the row's order, home sets to away sets. A cell left blank here is
        compared with nothing: a template that totals games and not sets is
        common, and a missing cell read as a zero would be a disagreement the
        card never printed.
        """
        return [name for name, mine, theirs in (
                    ("home sets", self.home_sets, other.home_sets),
                    ("home games", self.home_games, other.home_games),
                    ("away games", self.away_games, other.away_games),
                    ("away sets", self.away_sets, other.away_sets))
                if mine is not None and mine != theirs]


@dataclass(frozen=True, slots=True)
class CrossCheck:
    """One independent confirmation that the set rows were read correctly.

    A scorecard states its result three times over: the per-set games, the printed
    ``TOTAL`` row, the result verb between the team names, and the ``Match Winner``
    tick. Any one of them disagreeing with the sets means something was misread
    or misprinted, or that the card counts its rows by a rule the check does not:
    build.py reports it as an error, which fails the run, and still records the
    match as its rows or its ``Match Forfeited`` box give it (4.426).

    ``available`` is false when the card simply does not print this check — a
    perfectly normal template variation, and not evidence of anything.

    ``reported_by`` names the check that reports this one's disagreement for it,
    when the two would be one mistake reported twice: on a whole-match forfeit, a
    verb the box contradicts is ``verb-mismatch``'s to report, which reads it
    through the same :func:`verb_side`, and a tick restating a verb is reported
    with the verb. It names only a check that reports on the card, or one whose
    disagreement ``forfeit-contradicted`` reports with this one's, where the
    club's rules read a forfeit's rows against its box (4.134). Such a check is
    printed and disagrees, so it is neither missing nor agreeing, but it is not a
    failure of its own either. Whether ``verb-mismatch`` reports a played card's
    verb, or ``totals-mismatch`` a ``TOTAL`` row, turns on the club's rules or on
    whether the card is called off, which the reader does not decide, so nothing
    is named there, and F6 asks the check.

    ``held_to`` is what the check held the card to, as a report names it: the set
    rows, or on a whole-match forfeit whose rows give no result, the Match
    Forfeited box the check was held to instead. ``unusable`` is set on a check
    the card prints something in the place of that confirms nothing, a ``VS`` or
    a verb no table holds: a clause saying what it prints, for a report to add to
    what the card does not. Such a check is not available, since it confirms
    nothing, but it is not missing either.
    """

    name: str
    available: bool
    agrees: bool = True
    detail: str = ""
    reported_by: str = ""
    held_to: str = "the set rows"
    unusable: str = ""

    def state(self, absent: str = "not printed") -> str:
        """What describe and the doctor call this check, *absent* if not printed.

        ``printed, not usable`` where the card prints something in its place that
        confirms nothing (4.133), so that neither report can call it missing.
        """
        return ("printed, not usable" if self.unusable
                else absent if not self.available
                else "agrees" if self.agrees
                else f"disagrees, reported by {self.reported_by}"
                if self.reported_by else "DISAGREES")


def thin_card_prints(checks: tuple[CrossCheck, ...], nouns: dict[str, str],
                     conjunction: str = "and") -> str:
    """What a card none of whose cross-checks is available prints, as a clause.

    The checks it prints nothing for, each named by *nouns* and listed with
    *conjunction*, then, for each it prints something for that confirms nothing,
    the check's own clause saying what (4.133): ``no TOTAL row and no Match Winner
    tick, and it prints 'VS' between the team names instead of a result``. The
    reader's thin-card warning and F6's each word their own nouns around it, so
    neither joins what a card prints to what it does not by a rule of its own.
    """
    return (in_words((nouns[check.name] for check in checks if not check.unusable),
                     conjunction)
            + "".join(f", and {check.unusable}" for check in checks if check.unusable))


@dataclass(frozen=True, slots=True)
class Card:
    """One transcribed scorecard page. Names as printed; one thing interpreted.

    That one is ``walkover_by``: the side that gave the match up, where the card
    writes a walkover as Match Centre does, Match Cancelled ticked beside a
    winner and a TOTAL row awarding every rubber to the side that came
    (``scorecard.walkover_side``). It is kept beside the boxes as printed, which
    stay as printed, as the reader's cross-checks are kept, because discover
    reads cards and not the reader. :attr:`forfeit_side` is the side a match was
    given up by, however the card tells it.
    """

    source: str = ""
    page: int = 0
    match_id: str = ""
    date: str = ""              # ISO yyyy-mm-dd, "" if the card printed none
    weekday: str = ""           # as printed beside the date, not from the date
    night: str = ""             # the weekday its competition line names, in full
    division: str = ""
    round: str = ""
    court: int | None = None
    venue: str = ""
    season_hint: str = ""       # a season string if the header happened to print one
    club_hint: str = ""         # club name from the letterhead, for the config stub
    home_name: str = ""
    away_name: str = ""
    verb: str = ""              # DEFEATS / DRAWS WITH / LOSES TO, as printed
    sets: tuple[ParsedSet, ...] = ()
    home_roster: tuple[RosterEntry, ...] = ()
    away_roster: tuple[RosterEntry, ...] = ()
    totals: Totals = field(default_factory=Totals)
    winner_tick: str | None = None      # "home" | "away" | None
    forfeited_by: str | None = None     # whole-match forfeit
    walkover_by: str | None = None      # a walkover, Match Cancelled ticked (above)
    cancelled: bool = False
    disputed: bool = False
    checks: tuple[CrossCheck, ...] = ()
    warnings: tuple[str, ...] = ()
    # Each roster line left out of the squads, as a heading or as printed too close
    # above one, as a warning quotes it: in no squad, and so in no document but that
    # warning, and possibly a player.
    dropped: tuple[str, ...] = ()

    @property
    def forfeit_side(self) -> str | None:
        """The side this card gives the whole match up by: its Match Forfeited box,
        or the walkover it writes. Every card-level read of a whole-match forfeit
        asks this rather than the box."""
        return self.forfeited_by or self.walkover_by

    @property
    def doubles_flags(self) -> tuple[bool, ...]:
        """One flag per set row: the format this card describes."""
        return tuple(s.doubles for s in self.sets)

    @property
    def home_games(self) -> int:
        return sum(s.home_games for s in self.sets)

    @property
    def away_games(self) -> int:
        return sum(s.away_games for s in self.sets)

    @property
    def cross_checks_available(self) -> tuple[CrossCheck, ...]:
        return tuple(c for c in self.checks if c.available)

    @property
    def cross_checks_failed(self) -> tuple[CrossCheck, ...]:
        return tuple(c for c in self.checks
                     if c.available and not c.agrees and not c.reported_by)

    @property
    def confirmed(self) -> bool:
        """At least one available cross-check, and none of them disagreeing.

        What the reader alone can say of a card's numbers, knowing no club's
        rules. Zero available checks is a warning (some templates print none); a
        check that disagrees is an error, reported by F6, by the check it names in
        ``reported_by``, or, where it names none, by ``verb-mismatch`` or
        ``totals-mismatch`` where F6 finds that check reports the card. Agreeing
        here is not passing validate, which holds a card to the club's set rule: a
        forfeit's TOTAL row may total the award under a set rule the club does not
        play, and agree here.
        """
        available = self.cross_checks_available
        return bool(available) and all(c.agrees for c in available)

    def roster_names(self) -> tuple[str, ...]:
        return tuple(e.name for e in (*self.home_roster, *self.away_roster))


@dataclass(frozen=True, slots=True)
class Rejection:
    """A PDF that was found but not turned into a card.

    Every one of these is reported by path and counted, and any rejection makes
    the run exit nonzero. A scorecard that vanishes quietly is the single most
    expensive failure this tool can have: the ladder stays plausible and a team is
    simply short a match.

    A folder under the cards that could not be listed is refused the same way,
    with ``folder`` set: what it cost is every card in it, a number nobody knows,
    so every count of refused cards counts it apart, as a folder.
    """

    path: str
    reason: str
    detail: str = ""
    page: int = 0
    folder: bool = False

    def describe(self) -> str:
        """The card and why it was refused. The detail is left out on purpose.

        This is the opening of F4's message, and the detail is printed from the
        refused entry alone. When the message carried it too, every reader printed
        the evidence twice.
        """
        where = f"{self.path}" if self.page == 0 else f"{self.path} page {self.page + 1}"
        return f"{where}: {self.reason}"


def letterhead_key(text: str) -> str:
    """A letterhead as two cards are compared on it: spacing and case ignored.

    Spelling is not. One definition for "which club printed this card", shared by
    the demo's own-card test and the fetcher's keep test, so that the two can never
    disagree about the same page, and by the build's letterhead check, so that it
    never takes a difference of case or spacing for another club.
    """
    return " ".join(text.split()).upper()


def letterhead_spellings(cards: Sequence[Card]) -> list[tuple[str, int]]:
    """Each spelling *cards* print, spacing folded, and how many print it.

    The most printed first, and a tie in the order read. Cards come in the order of
    their paths, so that is the folder's answer and never a hash seed's.
    """
    spellings = Counter(" ".join(card.club_hint.split()) for card in cards)
    return spellings.most_common()


def majority_letterhead(cards: Sequence[Card]) -> tuple[str, str]:
    """The letterhead most of *cards* print, and the spelling most of those print.

    As its :func:`letterhead_key` and that spelling, spacing folded, or two empty
    strings where no letterhead read; a card whose letterhead did not read counts
    towards none. A tie of letterheads goes to the one read first, and so does a
    tie of spellings (:func:`letterhead_spellings`), never to a code point: cards
    come in the order of their paths, so capitals against title case cannot decide
    it. The one rule for which letterhead is the club's, asked by
    ``discover._club_name`` of every competition's cards for the name the config
    stub offers, and by the build's letterhead check of one competition's.
    """
    printed: dict[str, list[Card]] = {}
    for card in cards:
        key = letterhead_key(card.club_hint)
        if key:
            printed.setdefault(key, []).append(card)
    if not printed:
        return "", ""
    key = max(printed, key=lambda key: len(printed[key]))
    return key, letterhead_spellings(printed[key])[0][0]


def round_key(printed: str) -> str:
    """The round a card is filed under: the label it prints, less the spaces round it.

    Nothing more is done to it (4.32): ``03`` and ``3`` are two rounds, and so are
    ``SF`` and ``sf``, and ``round_sort_key`` orders them without equating them. One
    definition for "which round is this card", shared by discovery, which files the
    card under it; the fetcher, which counts the card for the index's round of that
    name; and the loader, which keys the declared draw's rounds and the byes table
    by it; so that neither of the other two asks for a round the build files
    elsewhere (4.124). Rejected: the fetcher calling discovery's filing, a tool
    reaching into the builder for a rule about a card.
    """
    return printed.strip()


# The result verbs a card prints between the team names, read whole, against a key
# with punctuation stripped, so "DEF. BY" and "DEF BY" are one entry. Real exported
# cards print DEFEATS and DEF. BY, and the template documentation says DEFEATS /
# DRAWS WITH / LOSES TO. One table, here at the seam, asked by the reader's
# cross-checks and by validate's ``verb-mismatch`` alike, so a verb is one reading
# wherever it is asked about. A word it does not hold is no verb, and is reported
# as one this program does not recognise rather than guessed at from the letters
# inside it. "beat", "drew", "level" and "halved" are the words the reader once
# looked for inside a verb, held here whole.
_HOME_VERBS = frozenset({"defeats", "def", "defeated", "wins", "won", "beats",
                         "beat"})
_AWAY_VERBS = frozenset({"def by", "defeated by", "loses to", "lost to",
                         "defeats by"})
_DRAW_VERBS = frozenset({"draws with", "draw", "draws", "drawn", "drew",
                         "ties with", "tie", "tied", "level", "halved"})

# Not verbs. `VS` is what Match Centre prints between the team names when it has
# no result to state, and it has to be told apart from a word this program does
# not know, because the two are opposite findings: an unknown word is a gap in
# this program, and a separator is a gap in the card. Measured across 22 real
# exported cards — every card printing VS also had no Match Winner tick, and every
# card printing DEFEATS or DEF. BY had one.
_NO_RESULT_VERBS = frozenset({"vs", "v", "versus"})


def verb_key(verb: str) -> str:
    """``DEF. BY`` -> ``def by``, so punctuation cannot make two of one verb."""
    return " ".join("".join(c if c.isalnum() else " " for c in verb.lower()).split())


def verb_side(verb: str) -> str | None:
    """The side a printed verb says won, ``"draw"``, or None for no verb it knows.

    The one reading of a verb, asked by the reader's verb and winner-tick checks
    and by validate's ``verb-mismatch``, so that no verb is a result to one and no
    verb to another, and a check that names ``verb-mismatch`` as reporting a verb
    names a check that reads it so.
    """
    # Exact match on a normalised key, deliberately: prefix matching would read the
    # away verb "DEF. BY" as the home verb "DEF".
    key = verb_key(verb)
    if key in _DRAW_VERBS:
        return "draw"
    if key in _AWAY_VERBS:
        return "away"
    if key in _HOME_VERBS:
        return "home"
    return None


def states_no_result(verb: str) -> bool:
    """Whether a printed verb is the neutral separator rather than an outcome.

    ``VS`` between two team names is a fixture, not a result. A card printing it is
    not a card this program failed to understand — it is a card with no result on
    it, which is a different thing to report and a different thing for a club to do
    about it. Read against the key a verb is, and asked by the reader's verb check
    and by validate alike, so no card is a separator to one and a word it does not
    know to the other.
    """
    return verb_key(verb) in _NO_RESULT_VERBS
