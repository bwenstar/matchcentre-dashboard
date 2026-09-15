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

from dataclasses import dataclass, field

from .model import SetStatus

__all__ = ["Card", "ParsedSet", "RosterEntry", "CrossCheck", "Rejection",
           "CardError", "Totals"]


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
    # Name cells on this row that print Match Centre's ``Unspecified player``
    # rather than a person, and so produced no name above. Counted rather than
    # discarded so that one warning per card can say how much of it was never
    # filled in; read as a name it would become a player with a playing record.
    unnamed: int = 0


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


@dataclass(frozen=True, slots=True)
class CrossCheck:
    """One independent confirmation that the set rows were read correctly.

    A scorecard states its result three times over: the per-set games, the printed
    ``TOTAL`` row, the result verb between the team names, and the ``Match Winner``
    tick. Any one of them disagreeing with the sets means something was misread,
    and that is the difference between a wrong number and a refused card.

    ``available`` is false when the card simply does not print this check — a
    perfectly normal template variation, and not evidence of anything.
    """

    name: str
    available: bool
    agrees: bool = True
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Card:
    """One transcribed scorecard page. Names as printed; nothing interpreted."""

    source: str = ""
    page: int = 0
    match_id: str = ""
    date: str = ""              # ISO yyyy-mm-dd, "" if the card printed none
    weekday: str = ""           # from the date, not from a filename
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
    cancelled: bool = False
    disputed: bool = False
    checks: tuple[CrossCheck, ...] = ()
    warnings: tuple[str, ...] = ()

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
        return tuple(c for c in self.checks if c.available and not c.agrees)

    @property
    def confirmed(self) -> bool:
        """At least one available cross-check, and none of them disagreeing.

        The gate on trusting a card's numbers. Zero available checks is a warning
        (some templates print none); a check that disagrees is an error.
        """
        return bool(self.cross_checks_available) and not self.cross_checks_failed

    def roster_names(self) -> tuple[str, ...]:
        return tuple(e.name for e in (*self.home_roster, *self.away_roster))


@dataclass(frozen=True, slots=True)
class Rejection:
    """A PDF that was found but not turned into a card.

    Every one of these is reported by path and counted, and any rejection makes
    the run exit nonzero. A scorecard that vanishes quietly is the single most
    expensive failure this tool can have: the ladder stays plausible and a team is
    simply short a match.
    """

    path: str
    reason: str
    detail: str = ""
    page: int = 0

    def describe(self) -> str:
        where = f"{self.path}" if self.page == 0 else f"{self.path} page {self.page + 1}"
        return f"{where}: {self.reason}" + (f" ({self.detail})" if self.detail else "")
