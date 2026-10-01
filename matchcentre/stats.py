"""Derived team and player statistics: the ladder, and everything under it.

Every function here is a pure function of ``list[Match]`` plus the competition, so
the dashboard's numbers can always be regenerated from the cards alone and nothing
cached in ``season.json`` can drift away from them.

Six rules drive the shape of this module. Two of them — averaging and
recomputation — were already true of the predecessor and are easy to break by
accident; the other four are new, and each closes a hole that produced wrong
numbers.

*Averaging.* When ``rules.average_unplayed`` is set, an unscored match (a bye, a
washout) is credited the team's own average over the matches it actually played.
That average must be computed from **decided matches alone, before any averaged
match exists**, or an averaged score feeds the next average and the whole ladder
drifts. :class:`PointsLedger` holds the two halves apart so the dashboard can say
which part of a total is real. When the setting is off, an unscored match leaves
the numerator and the divisor alike untouched — it is not a zero, it simply is not
there. :meth:`~matchcentre.model.Match.points_for` returns ``None`` for both
cases; this module is the only place that knows which of the two applies.

*Abandonment.* A match that did not happen contributes nothing to any count here
— no games, no sets, no appearance, no slot record — and
:attr:`~matchcentre.model.Match.counts_as_played` is the single test for it. Part
of a cancelled night's card is often filled in before play stops, and counting
those games would pay a team twice: once in the games column, once in whatever the
averaging rule above gives it for a round nobody played.

*One night, one result.* A fixture is counted once however many cards arrive for
it. Two identical exports of the same night are one night's tennis, and scoring
both hands two teams a second win and twenty more games. Two cards that disagree
are worse: picking one would be this module guessing at a *result*, which it never
does, so the fixture is counted nowhere until a human removes a file.
:func:`scoreable` is the single test, applied at every entry point below.

*Recomputation.* Ladder history is the ladder recomputed from scratch over the
matches up to each round, never a running total patched round by round. Because
averaging depends on how many matches a team has played, a patched ladder would
be wrong for exactly the teams that had a bye.

*Slug identity.* Teams are keyed by slug throughout, so ``head_to_head`` keys are
``"slug-a|slug-b"`` with the pair sorted lexically. The predecessor keyed on draw
numbers, which appear on no scorecard: it had to invent them, and one misprinted
banner renumbered the division.

*The order is the club's, not ours.* ``rules.ladder_by`` names the sort keys.
Hardcoding "points average, then points, then games" — as the predecessor did —
silently reorders the table of any club that decides on rubbers.
"""

from __future__ import annotations

import functools
from collections import defaultdict
from collections.abc import Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .competition import (DRAW_DECLARED, Competition, Player, Round,
                          round_sort_key)
from .format import MatchFormat
from .model import NO_OPPONENT, Match, MatchStatus, Outcome
from .scope import in_words
from .scoring import LADDER_KEYS
from .slugs import unslug

__all__ = ["PRECISION", "match_sort_key", "chronological", "rounds_present",
           "round_display", "round_half_up", "round_stored", "set_win_pct",
           "with_byes",
           "card_against_rest", "rests_standing",
           "MissingCard", "fixtures_without_cards", "later_round_with_cards",
           "RoundCards", "round_cards", "unlisted_words",
           "through_round",
           "regular_season", "same_result", "same_scoreline", "Duplicates",
           "duplicates", "disputed_fixtures",
           "scoreable", "priceable", "ladder_matches", "not_counted_reasons",
           "not_credited_reasons", "not_totalled_reasons", "other_cards",
           "NOT_COUNTED_CONFLICT", "NOT_COUNTED_DUPLICATE", "NOT_COUNTED_LEVEL",
           "NOT_COUNTED_FINALS", "NOT_COUNTED_NAMES",
           "PointsLedger", "points_ledger", "round_points", "head_to_head",
           "h2h_key", "ladder",
           "ladder_history", "ladder_complete", "team_stats", "player_stats",
           "player_names", "is_fill_in", "contribution_gaps", "ContributionGap",
           "PointsBreakdown", "SideRecord",
           "SlotRecord", "DisciplineRecord", "PlayerSlotRecord", "Streak",
           "LadderEntry", "RoundPoints", "RatingPoint", "PairRecord",
           "Contribution", "HeadToHead", "TeamStats", "PlayerStats"]

# Points are carried at full precision but summed from one-decimal values, so bare
# float arithmetic leaves 1e-16 crumbs that would churn `git diff` on season.json.
# Rounding at the output boundary keeps the file stable without throwing away
# meaningful precision (averages need ~6 places, display 1).
PRECISION = 6


def match_sort_key(match: Match, draw: Mapping[str, Round] | None = None
                   ) -> tuple[tuple, str, str, str]:
    """Chronological order, total and reproducible.

    ``round_sort_key`` comes from :mod:`~matchcentre.competition` so that "1, 2,
    10, F" sorts that way rather than as strings ("1, 10, 2") or as integers
    (which raises on ``F``). The trailing slugs make the key total, so two cards
    on the same date never swap places between runs. The *draw* is the key's: a
    competition's rounds, so that a final it declares under a name of its own is
    played where it put it (4.71).
    """
    return (round_sort_key(match.round, draw), match.date, match.home, match.away)


def chronological(matches: Iterable[Match],
                  draw: Mapping[str, Round] | None = None) -> list[Match]:
    return sorted(matches, key=lambda match: match_sort_key(match, draw))


def rounds_present(matches: Iterable[Match],
                   draw: Mapping[str, Round] | None = None) -> list[str]:
    """Round labels that have at least one match, in the order a season runs."""
    return sorted({m.round for m in matches},
                  key=lambda number: round_sort_key(number, draw))


def round_half_up(value: float | None, digits: int = 2) -> float | None:
    """*value* to *digits* places, rounding a half away from zero.

    Not what ``round()`` does, and the difference is visible on a real ladder. Two
    things are going on. Python rounds a stored half to *even*, so ``round(0.5)``
    is 0; and a decimal that ends in a half is usually not stored as one anyway —
    the nearest double to 6.225 is 6.22499999999999964, which every rounding rule
    in the world sends down to 6.22.

    Every association ladder this dashboard gets compared against lives in a
    spreadsheet, and a spreadsheet rounds the *printed* decimal half up: 6.23.
    Being a cent under the number a club reads off its own noticeboard is small and
    expensive — it is the kind of difference that costs the whole page its
    credibility, because nobody can tell it apart from a scoring bug.

    Quantising ``str(value)`` is what makes 6.225 a half again: ``str`` gives the
    shortest decimal string that round-trips to the double, which is the decimal the
    club would have typed. ``app.js`` does the same to the same values with the same
    reasoning, and the two have to agree — ``build.py`` prints this ladder to a
    terminal and the dashboard prints it to a page.
    """
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def round_display(value: float | None) -> float | None:
    return round_half_up(value, 1)


def round_stored(value: float | None) -> float | None:
    return None if value is None else round(value, PRECISION)


def set_win_pct(won: int, lost: int) -> float | None:
    """Sets won as a share of the sets decided, for display.

    None when no set was decided: no set decided is no win rate, not a win rate
    of nothing. The one rule for the figure, read by the season document and the
    careers page alike, so the two cannot print different digits for one record.
    """
    decided = won + lost
    return round_display(100.0 * won / decided) if decided else None


def _share_pct(games: float, total: int) -> float | None:
    """*games* as a share of a team's *total* games won, for display.

    None when there are no team games to share: a team that won none, or a name on
    no roster, holds no share, not a share of nothing, and a nought is a player who
    won none of a team's games. The one rule for a Share cell and for the shortfall
    under the column, so the two are in one unit.
    """
    return round_display(100.0 * games / total) if total else None


# --- small records mirrored 1:1 into season.json -------------------------
# Field names are the JSON keys: export.py walks these structurally, so renaming a
# field changes the public schema.


@dataclass(frozen=True, slots=True)
class PointsBreakdown:
    earned: float
    averaged: float
    averaged_matches: int


@dataclass(frozen=True, slots=True)
class SideRecord:
    played: int = 0
    won: int = 0
    lost: int = 0
    drawn: int = 0
    games_won: int = 0
    games_lost: int = 0


@dataclass(frozen=True, slots=True)
class SlotRecord:
    won: int = 0
    lost: int = 0
    games_won: int = 0
    games_lost: int = 0


@dataclass(frozen=True, slots=True)
class DisciplineRecord:
    """A player's singles or doubles record."""

    played: int = 0
    won: int = 0
    lost: int = 0
    games_won: int = 0
    games_lost: int = 0


@dataclass(frozen=True, slots=True)
class PlayerSlotRecord:
    played: int = 0
    won: int = 0
    # Sets won out of sets decided in the slot, by set_win_pct, the rule behind the
    # player's own win_pct: None for a slot with no set decided in it, as a player's
    # is when they have decided none. Published so the page prints a rate rather
    # than working one out.
    win_pct: float | None = None


@dataclass(frozen=True, slots=True)
class Streak:
    type: str | None
    length: int


@dataclass(frozen=True, slots=True)
class LadderEntry:
    round: str
    position: int
    points_average: float | None


@dataclass(frozen=True, slots=True)
class RoundPoints:
    round: str
    points: float


@dataclass(frozen=True, slots=True)
class RatingPoint:
    round: str
    singles: float | None
    doubles: float | None


@dataclass(frozen=True, slots=True)
class PairRecord:
    """A partner or opponent record, from the named player's point of view."""

    slug: str
    name: str
    sets: int = 0
    won: int = 0
    lost: int = 0
    games_won: int = 0
    games_lost: int = 0


@dataclass(frozen=True, slots=True)
class Contribution:
    """One player's share of the games their own team won.

    ``games_won`` is a share and so may be a half: a doubles rubber's games were
    won by two people jointly, and crediting them to both against a denominator
    that counts them once is what made this column add up to about 150%.
    """

    games_won: float
    team_games_won: int
    # None with no team games to share, by _share_pct.
    share_pct: float | None


@dataclass(frozen=True, slots=True)
class ContributionGap:
    """The part of a team's games won that no player row holds, and why.

    The Share column is a partition of one team's games won, and it is allowed to come
    to less than the whole rather than credit a team's games to somebody the table has
    no row for (F1). Honest, and indistinguishable from a broken sum unless something
    says so -- so this is what the dashboard says it with, and the reason it is written
    here is that the arithmetic it describes is decided here.

    ``games`` is ``team_games_won`` less everything credited, and so may be a half: half
    a doubles rubber belongs to nobody when a card names one player for it.
    ``share_pct`` is the same shortfall in the unit the cells are printed in, and so
    None, as theirs is, for a team that won no games.

    Both are signed, and negative is the other direction: a rubber with more names on it
    than the format has places pays each of them a whole place's share, so the rows can
    hold *more* than the team ever won and a reader's own addition overshoots 100%. One
    subtraction, so a season with faults both ways reports their net -- which is what
    the reader's addition shows -- while the counts below keep the faults apart.

    ``team_games_won`` is the Games column of the row this record sits on, not a total
    of this record's own: both are :func:`ladder_matches`, so a caption printing one
    beside the other cannot contradict the table it is under.

    The four counts are the four reasons the rows and the total disagree, counted as a
    reader would go and look at them -- per rubber, once, however many names in it are
    strangers, and per *night* for the last, because two cards describing one Tuesday
    are one night and the data health section names them as the pair of files they are.
    A gap with no reason behind it means something else caused it, which is why the page
    may promise the health section lists the rubbers only when a count is non-zero. They
    are counts of rubbers and not of games, so nothing nets: a season whose faults
    cancel to ``games == 0`` still reports every one of them.

    Zeroes rather than ``None`` for a team whose column adds up, the ``share_pct`` of a
    team that won no games excepted, as above: every field is written for every team,
    so nothing downstream has to tell "nothing wrong" from "nothing written".
    """

    games: float
    team_games_won: int
    share_pct: float | None
    rubbers_with_outsiders: int
    rubbers_short_a_name: int
    rubbers_overfull: int
    nights_not_credited: int


@dataclass(frozen=True, slots=True)
class HeadToHead:
    """One pairing's record. ``low`` and ``high`` are the two slugs in the key.

    The key is ``"slug-a|slug-b"`` sorted lexically, so ``low`` is the first of
    the pair alphabetically and ``high`` the second. The names are the
    predecessor's, kept because they are the published JSON schema; only what
    orders them changed, from draw number to slug.

    ``played`` counts the meetings, ``undecided`` how many of them reached no
    result, and so ``wins_low + wins_high + draws + undecided == played``.

    ``finals`` is how many of the meetings were in a finals round. It cuts across
    the other counts rather than partitioning them -- a final is in ``played``, in
    the games, and in a win column or in ``undecided`` like any other night -- and
    it is here because it is the one figure that explains a disagreement the page
    would otherwise just present: the ladder's head-to-head tiebreak counts the
    regular season, this record counts every meeting, and two teams can be level
    here and separated there. A club checking one against the other needs to know
    they are answers to different questions.

    New fields are added at the end rather than in the place they belong, because
    the order of the first six is a published schema.
    """

    played: int = 0
    wins_low: int = 0
    wins_high: int = 0
    draws: int = 0
    games_low: int = 0
    games_high: int = 0
    undecided: int = 0
    finals: int = 0


@dataclass(frozen=True, slots=True)
class TeamStats:
    """One ladder row and everything the team's own page shows.

    ``slug`` is the identity; ``code`` is the number a club's published draw gives
    the team and is display metadata only, ``None`` unless a human declared it.
    """

    code: int | None
    name: str
    short: str
    slug: str
    home_court: int | None
    captain: str | None
    players: list[str]
    played: int
    won: int
    lost: int
    drawn: int
    points: float
    points_average: float | None
    points_breakdown: PointsBreakdown
    position: int
    games_won: int
    games_lost: int
    games_diff: int
    sets_won: int
    sets_lost: int
    sets_diff: int
    # Sets won out of sets decided, by set_win_pct, the rule behind every player's
    # win_pct: None for a team that has decided none.
    win_pct: float | None
    counted: int
    home: SideRecord
    away: SideRecord
    form: list[str]
    streak: Streak
    by_slot: dict[str, SlotRecord]
    ladder_history: list[LadderEntry]
    best_round: RoundPoints | None
    worst_round: RoundPoints | None
    # The games in `games_won` that no player of this team has a share of, and why.
    # A team figure because it is one subtraction from a team figure; on the row
    # rather than beside the players so that a page reading the table has it without
    # walking the cards. See :class:`ContributionGap`.
    contribution_gap: ContributionGap


@dataclass(frozen=True, slots=True)
class PlayerStats:
    """One player's record. ``team`` is the team's slug, ``team_name`` its label."""

    slug: str
    name: str
    team_code: int | None
    team: str | None
    team_name: str | None
    is_captain: bool
    rating: dict[str, float | None]
    rating_history: list[RatingPoint]
    matches: int
    available: int
    sets_played: int
    sets_won: int
    sets_lost: int
    games_won: int
    games_lost: int
    games_diff: int
    # Sets won out of sets decided. None for a player who decided none, whether
    # they never took a court or their one set was retired: out of nothing is
    # not a nought, and a nought is a record somebody set.
    win_pct: float | None
    singles: DisciplineRecord
    doubles: DisciplineRecord
    streak: Streak
    longest_win_streak: int
    partners: list[PairRecord]
    opponents: list[PairRecord]
    contribution: Contribution
    fill_in_appearances: int
    by_slot: dict[str, PlayerSlotRecord]


# --- duplicate cards ----------------------------------------------------

# The five reasons a match can be on the page and in none of the figures above it.
# Named here, beside the code that decides them, and mirrored once in the browser.
NOT_COUNTED_CONFLICT = "conflict"
NOT_COUNTED_DUPLICATE = "duplicate"
NOT_COUNTED_LEVEL = "level"
NOT_COUNTED_FINALS = "finals"
# The one reason that is also the reason a *counted* match credits nobody, so it
# appears in two fields of a match record and means the same thing in both: two
# cards for this fixture agree about the result and disagree about who played.
NOT_COUNTED_NAMES = "names"


def _scoreline(match: Match) -> tuple:
    """What a card says about the result, with the names left out.

    Each row's two ``Forfeit`` ticks are in it because they decide who won a
    forfeited rubber, and two cards alike in every figure but which side gave up
    which singles do not agree about the night. Compared as ticked rather than as
    the winner they imply, as the row's status is.
    """
    return (match.status, match.forfeited_by, match.winner,
            match.home_games, match.away_games,
            match.home_sets, match.away_sets,
            tuple((s.slot.key, s.home_games, s.away_games, s.status,
                   s.home_forfeit, s.away_forfeit)
                  for s in match.sets))


def _named(match: Match) -> tuple:
    """Who a card says played each rubber."""
    return tuple((s.slot.key, tuple(s.home_players), tuple(s.away_players))
                 for s in match.sets)


def same_scoreline(a: Match, b: Match) -> bool:
    """Whether two cards for one fixture agree about the result.

    Everything a result is made of, and nothing else. Not the match id, not the
    court, not the file it came from, not the date: a club that fixes a typo and
    exports again has a second file whose result is the first one's, and treating
    the new id as a disagreement would refuse a night's tennis over a filename.
    Not the names either — :func:`same_result` is that question, and the two are
    asked separately because the answers have different consequences.
    """
    return _scoreline(a) == _scoreline(b)


def same_result(a: Match, b: Match) -> bool:
    """Whether two cards for one fixture say the same thing about it entirely.

    The scoreline *and* the names. Two cards agreeing on every score but naming
    different people do not agree: one of them is wrong about somebody's playing
    record, and keeping whichever was read first would put a real person in a
    rubber their card does not name.

    That is not the same as the fixture being unusable, which is why the weaker
    question next door exists. Cards that agree on the result and not on the names
    settle the result; see :func:`_repeats`.
    """
    return same_scoreline(a, b) and _named(a) == _named(b)


@dataclass(frozen=True, slots=True)
class Duplicates:
    """How a match list divides once repeated cards for one fixture are settled.

    ``kept`` is what anything aggregating should count: every fixture exactly once.
    The other three lists are what was left out and why, so a caller can say so
    rather than quietly dropping a card that exists.

    ``superseded`` is the odd one: those cards agree with a kept card about the
    result and disagree about who played, so the fixture is in the team figures and
    in nobody's playing record. :func:`disputed_fixtures` is how the second half of
    that is asked, because ``kept`` alone cannot show it.
    """

    kept: list[Match]
    copies: list[Match]
    conflicting: list[Match]
    superseded: list[Match] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.copies and not self.conflicting and not self.superseded


def duplicates(matches: Sequence[Match]) -> Duplicates:
    """Split a match list into the countable fixtures and the repeats.

    A fixture is ``(round, home, away)`` — :attr:`~matchcentre.model.Match.key` —
    and two cards carrying it are one of two situations:

    *The same card twice.* The commonest thing that goes wrong in a club's ``data/``
    folder: a card is exported again, the browser names the second copy
    ``… (1).pdf``, and nothing is deleted. The first is kept and the rest are
    ``copies``. Which one is "first" does not matter, because :func:`same_result`
    has already established that they say the same thing.

    *Two cards that disagree about the result.* All of them go to ``conflicting``
    and none is counted. Choosing between them means deciding which result is true,
    and every tiebreak available here — the later file, the higher id, the longer
    scoreline — is a guess dressed as a rule. Better that the fixture is missing
    from the figures, loudly (``validate`` reports it as an error and the build
    exits 1), than that a plausible wrong ladder goes up.

    *Two cards that agree about the result and not about who played.* A corrected
    export, usually: a name that went in wrong, fixed in Match Centre and exported
    again beside the original. The result is not in doubt, so the first card is kept
    and the rest are ``superseded``; which one is kept is arbitrary and only harmless
    because nothing that differs between them is counted — see
    :func:`disputed_fixtures`, which is what keeps the names out of the player
    figures.

    Order is preserved, so a caller that sorted its input stays sorted.
    """
    kept: list[Match] = []
    copies: list[Match] = []
    conflicting: list[Match] = []
    superseded: list[Match] = []
    for match, reason in zip(matches, _repeats(matches)):
        if reason == NOT_COUNTED_DUPLICATE:
            copies.append(match)
        elif reason == NOT_COUNTED_CONFLICT:
            conflicting.append(match)
        elif reason == NOT_COUNTED_NAMES:
            superseded.append(match)
        else:
            kept.append(match)
    return Duplicates(kept=kept, copies=copies, conflicting=conflicting,
                      superseded=superseded)


def _places(matches: Sequence[Match]) -> dict[tuple[str, str, str], list[int]]:
    """Where in *matches* each fixture's cards are, by position, in the order given.

    The one grouping of cards into nights, shared by the reasons a card is left out
    and by the list of the other cards beside it, so the two cannot come to disagree
    about which cards claim one night.
    """
    where: dict[tuple[str, str, str], list[int]] = {}
    for index, match in enumerate(matches):
        where.setdefault(match.key, []).append(index)
    return where


def other_cards(matches: Sequence[Match]) -> list[tuple[int, ...]]:
    """Per position: the position of every other card for the same fixture, in order.

    Empty for a card alone on its night. For the rest it is what a reader needs to
    act on the reason :func:`not_counted_reasons` gives: two cards disagree, and
    these are the others. Every repeat and not only a clash, because the other card
    of a copy is there too, and a list that left it out would say it is not.

    Positions, as :func:`_repeats` works in, and not the cards: a document names a
    card by its place in ``matches``, the one thing about it no withholding rewrites.
    """
    out: list[tuple[int, ...]] = [()] * len(matches)
    for places in _places(matches).values():
        for index in places:
            out[index] = tuple(i for i in places if i != index)
    return out


def card_keys(matches: Sequence[Match]) -> list[str]:
    """Per position: a name for the card that no other card in *matches* has.

    The fixture and the card's place among that fixture's cards, counted from one in
    the order given: ``2/brindle-wombats/brindle-bunyips/1``. Slugs are
    ``[a-z0-9-]``, so the key reads back from the right whatever the round is
    called, and two cards never share one.

    Not the printed match id, which a card may not have and two cards may share, so
    a page finding cards by it showed one card twice and another nowhere. Not the
    card's place in *matches* either: a card arriving for an earlier fixture moves
    every place after it, and a fixture's own ordinal moves only when that fixture
    gains a card.
    """
    out = [""] * len(matches)
    for fixture, places in _places(matches).items():
        for number, index in enumerate(places, start=1):
            out[index] = "/".join((*fixture, str(number)))
    return out


def _repeats(matches: Sequence[Match]) -> list[str]:
    """Per position: ``""`` for a fixture's one countable card, else why not.

    Positional rather than by value, because two cards for one fixture can be
    equal in every field a :class:`~matchcentre.model.Match` has — that is what
    makes them a duplicate — so nothing but a place in the list tells them apart.

    Three answers, not two, and the middle one is the point: cards that agree about
    the result and disagree about who played still settle the result, so one of them
    is kept rather than the fixture being thrown away over a name.
    """
    out = [""] * len(matches)
    for places in _places(matches).values():
        if len(places) == 1:
            continue
        group = [matches[i] for i in places]
        if all(same_result(group[0], other) for other in group[1:]):
            for index in places[1:]:
                out[index] = NOT_COUNTED_DUPLICATE
        elif all(same_scoreline(group[0], other) for other in group[1:]):
            for index in places[1:]:
                out[index] = NOT_COUNTED_NAMES
        else:
            for index in places:
                out[index] = NOT_COUNTED_CONFLICT
    return out


def disputed_fixtures(matches: Sequence[Match]) -> set[tuple[str, str, str]]:
    """The fixtures whose cards agree about the result and not about who played.

    Keys, not matches, because the question is asked of the card that was *kept*:
    its figures are in the ladder and its names are one of two readings, so nothing
    it says about a person may be credited. A set of keys is also the shape a caller
    can ask about a match it is holding without re-deriving the grouping.

    Empty for every tidy folder, which is why both callers are a single guard rather
    than a second pass.
    """
    return {match.key for match, reason in zip(matches, _repeats(matches))
            if reason == NOT_COUNTED_NAMES}


def not_counted_reasons(matches: Sequence[Match],
                        competition: Competition) -> list[str]:
    """Why each match, in the order given, is in none of the ladder's figures.

    ``""`` for the ones that are in them. Otherwise ``"conflict"``, ``"duplicate"``,
    ``"names"``, ``"level"`` or ``"finals"``, which is the exporter's ``not_counted``
    field and the whole of what the page needs to explain a match it lists but no
    total above it includes. The page reads this rather than re-deriving any of the
    five, so it cannot reach a different answer from the ladder printed beside it.

    ``"names"`` is the reading that was not used, and the only one of the five whose
    fixture *is* in the figures: the card kept in its place carries the result. What
    that card does not carry is anybody's appearance, which is a different field —
    see :func:`disputed_fixtures`.

    Ordered by what somebody has to do about it, not by where the match leaves the
    pipeline. A repeated card's own reason wins over the rest, because which of two
    cards to believe has to be settled before anything said about the result means
    much; a level card in a grade that cannot draw wins over ``"finals"``, because a
    final left out of the ladder is the rules working and a result nobody recorded is
    somebody's afternoon.
    """
    out: list[str] = []
    for match, repeat in zip(matches, _repeats(matches)):
        if repeat:
            out.append(repeat)
        elif match.is_unscoreable_draw:
            out.append(NOT_COUNTED_LEVEL)
        elif competition.is_finals_round(match.round):
            out.append(NOT_COUNTED_FINALS)
        else:
            out.append("")
    return out


def not_credited_reasons(matches: Sequence[Match]) -> list[str]:
    """Why each match, in the order given, is in nobody's playing record.

    ``""`` for the ones that are in it. The other question :func:`not_counted_reasons`
    answers, asked separately because on two kinds of card the answers differ: a final
    and a level night are in none of the ladder's figures and were played by people,
    who are credited for them as usual; a night two cards agree about and name
    differently is counted for the teams and credited to nobody.

    It takes no ``competition`` for that reason -- whether a round is a final has
    nothing to do with it -- and the values it does return are the repeat reasons
    plus, on the kept card of a disputed night, ``"names"``.

    One field rather than a list of reasons for the page to check, because the page
    has to ask this of every row before it builds a figure out of the names on it:
    a set-by-set trail reading the copies as well as the originals lists somebody's
    night twice while their match count includes it once. A caller spelling out the
    reasons itself silently adopts each new one as it is added, which is how this
    project's uncounted-match sentences came to explain a level card as a final.
    """
    repeats = _repeats(matches)
    disputed = {match.key for match, reason in zip(matches, repeats)
                if reason == NOT_COUNTED_NAMES}
    return [reason if reason
            else (NOT_COUNTED_NAMES if match.key in disputed else "")
            for match, reason in zip(matches, repeats)]


def not_totalled_reasons(matches: Sequence[Match]) -> list[str]:
    """Why each match, in the order given, is in none of the season's own totals.

    The third question a card answers, and the front page's: sets completed, games
    played, matches played, the tightest night and the biggest margin. Those are
    about tennis that was played rather than about the ladder, so a final and a level
    night belong in them and :func:`not_counted_reasons` would take both out; the
    kept card of a night named two ways belongs in them too, and
    :func:`not_credited_reasons` would take that one out. Neither existing field
    answers it, and a page subtracting reasons from one of them -- everything except
    ``"finals"`` and ``"level"`` -- silently adopts each reason added afterwards.

    Which leaves the repeats: this is :func:`scoreable`'s membership, stated per
    position so a document can carry it on the row. ``""`` for the one card a
    fixture is counted from, else the reason the others were set aside. A row it sets
    aside is set aside by the ladder too, in the same word, so the sentence the page
    prints for it still comes from one field.
    """
    return _repeats(matches)


def scoreable(matches: Sequence[Match]) -> list[Match]:
    """The matches worth counting: one card per fixture, contradictions dropped.

    Idempotent, and the identity on a list with no repeats in it, which is every
    list this program builds from a tidy folder.
    """
    return duplicates(matches).kept


def ladder_matches(matches: Sequence[Match],
                   competition: Competition) -> list[Match]:
    """The exact set of matches every ladder figure is computed from.

    One fixture per pairing per round (:func:`scoreable`), regular rounds only
    (:func:`regular_season`), nothing level a grade with no draw cannot price
    (:func:`priceable`), plus the byes the draw states (:func:`with_byes`).
    Composed here once rather than at each of the five call sites, because a caller
    that assembled three of the four steps would produce a row that disagreed with
    the table it sits in — which is the whole class of bug this function exists to
    make impossible.

    The byes are judged against every card handed in, not only the ones kept: a
    card the ladder sets aside is still a night the team played. Which is why this
    is built once from the cards and never fed its own output. The cards it set
    aside are not in that output, so a second pass would pay a rest the first one
    refused. :func:`ladder_history` and :func:`team_stats` rank the list they built
    (:func:`_ranked`) instead of handing it back.
    """
    return with_byes(
        priceable(regular_season(scoreable(matches), competition)), competition,
        cards=matches)


def priceable(matches: Sequence[Match]) -> list[Match]:
    """The matches minus any the competition's scheme has no price for.

    Which today is one thing: a level card under ``[rules] draws = false``, where the
    association decides every match and a level one therefore means a result nobody
    wrote down. Dropped from the ladder rather than scored, because the two
    alternatives are both worse. Scoring it needs a draw value the grade does not
    have; keeping it and counting no points puts ``played 1, drawn 1, points 0`` on a
    ladder with no D in it, which reads as a team that drew rather than as a card to
    go and fix.

    Only the ladder loses it. The rubbers were played, so they stay in every player's
    record and in the head-to-head grid — the same division of labour as a final
    (:func:`regular_season`), and for the same reason.
    """
    return [m for m in matches if not m.is_unscoreable_draw]


# --- byes ---------------------------------------------------------------


def regular_season(matches: Sequence[Match],
                   competition: Competition) -> list[Match]:
    """The matches the ladder is made of: everything but the finals (F11).

    A final decides a premiership, not a ladder position, and a semi-final played
    after the last round would otherwise hand the team that won it four points and
    a place it did not earn over twenty-two weeks.

    Every figure the ladder rests on is filtered through here, which is what keeps
    a team row and the table it sits in describing the same set of matches. A
    player's own record is not: they played those rubbers, and a grand final is the
    match they would most like to see on it.
    """
    return [m for m in matches if not competition.is_finals_round(m.round)]


def card_against_rest(matches: Iterable[Match], number: str,
                      team: str) -> Match | None:
    """The first card, in the order given, that has *team* playing in round *number*.

    The card half of the evidence :func:`rests_standing` reads, and the card a
    finding about it names. A build hands the cards over in season order, and two
    cards for one fixture in the order they were read, so the file named is the
    same on every run.
    """
    return next((m for m in matches
                 if m.round == number and not m.is_bye and m.involves(team)),
                None)


def rests_standing(rnd: Round, matches: Iterable[Match]) -> tuple[str, ...]:
    """The teams *rnd* rests that nothing else about the round has playing.

    The one rule for whether a stated rest stands, read by everything that credits,
    draws or reports one. A rest does not stand where the same round lists the team
    in a fixture, or where a card has it playing. The card is evidence that the
    match was played, and an average paid on top of a real result pays the team
    twice. :mod:`~matchcentre.validate` reports both cases.

    *matches* are every card read, the ones the ladder sets aside included, and
    every caller hands this the same ones. Two cards that disagree count for
    nothing, and a level card is not counted in a grade that cannot draw, but either
    way the team played that night. Judged on the counted cards alone, a team whose
    only card that round was set aside looked like a team resting, and was paid
    for it. The fixtures are the half no card can supply: a round of the club's
    own draw that lists the team twice, once resting and once playing.
    """
    matches = tuple(matches)
    return tuple(team for team in rnd.byes
                 if team and not any(team in pair for pair in rnd.fixtures)
                 and card_against_rest(matches, rnd.number, team) is None)


def with_byes(matches: Sequence[Match], competition: Competition,
              cards: Sequence[Match] | None = None) -> list[Match]:
    """The matches, plus one scoreable ``BYE`` per bye the draw actually states.

    A bye has no card, and everything in this module counts a team's matches by
    :meth:`~matchcentre.model.Match.involves`, so a round that merely omitted the
    team would leave its ladder row a whole match short — which reads as a missing
    scorecard, not as a scheduled rest.

    Rule F1 lives here as a refusal to guess: this function adds a bye **only**
    where :attr:`~matchcentre.competition.Round.byes` already names one, and a
    ``Round`` only ever carries one when the team list was declared by a human or
    came from a declared draw. Nothing in this module compares a round's cards
    against the team list and concludes that whoever is missing must be resting.
    That inference is what paid a bye, in the predecessor, to teams that had
    played and won, off the back of one scorecard that failed to parse.

    And only a rest that stands (:func:`rests_standing`): none for a team the same
    round has playing, on a card or in a fixture. *cards* are what that is judged
    against, and are *matches* unless given. :func:`ladder_matches` gives every
    card read, because what it hands this as *matches* is only the ones it counts.

    Only for rounds that have been played. A bye is a scheduled rest, but it is
    not *worth* anything until the round happens; crediting a future one has a
    team scoring points for weeks that have not been played.

    Idempotent when *cards* is not given: its output then holds every card it
    judged by, and the byes it added are skipped the second time. Given *cards*,
    its output lacks the ones *matches* left out, which is why
    :func:`ladder_matches` is never fed its own.
    """
    played = set(rounds_present(matches))
    already = {(m.round, m.home) for m in matches if m.status is MatchStatus.BYE}
    extra: list[Match] = []
    for number in competition.round_order:
        rnd = competition.rounds[number]
        if number not in played:
            continue
        for team in rests_standing(rnd, matches if cards is None else cards):
            if (number, team) in already:
                continue
            extra.append(Match(competition=competition.id, round=number,
                               home=team, away=NO_OPPONENT,
                               fmt=competition.fmt, rules=competition.rules,
                               date=rnd.date, sets=(), status=MatchStatus.BYE))
    return list(matches) + extra


# --- fixtures with no card ----------------------------------------------


@dataclass(frozen=True, slots=True)
class MissingCard:
    """A declared fixture the season has gone past with no card read for it.

    The last four fields are the *evidence*, carried rather than resolved, so that
    whoever renders this shows a club why it is a gap instead of asserting that it
    is: how many of the round's declared fixtures do have a card, out of how many,
    the earliest later round that has one at all, and how many of the round's
    cards are of fixtures the draw does not list, as :func:`round_cards` counts
    them.
    """

    round: str
    home: str
    away: str
    cards: int
    fixtures: int
    later: str
    unlisted: int


@dataclass(frozen=True, slots=True)
class RoundCards:
    """A round's cards against the fixtures its draw lists, as every caller counts them.

    ``carded`` is the listed fixtures a card names, its sides either way round, and
    ``missing`` the rest, in draw order. ``unlisted`` are the round's cards of
    fixtures the draw does not list: they show the round was played, and they are
    no listed fixture's card. A rest is not a card and is in none of them.
    ``card-missing`` and discovery's round note both ask this (4.190). The warning
    counted every card of the round against the fixtures listed, and said "3 of
    the 1 fixtures the draw lists for that round do have one", while the note
    paired them by a rule of its own and said a round whose cards were all
    unlisted had "no cards at all".
    """

    carded: int
    missing: tuple[tuple[str, str], ...]
    unlisted: tuple[Match, ...]


def round_cards(rnd: Round, matches: Iterable[Match]) -> RoundCards:
    """*rnd*'s cards among *matches*, set against the fixtures the draw lists."""
    here = [m for m in matches if m.round == rnd.number and not m.is_bye]
    played = {frozenset((m.home, m.away)) for m in here}
    listed = {frozenset(pair) for pair in rnd.fixtures}
    missing = tuple(pair for pair in rnd.fixtures if frozenset(pair) not in played)
    return RoundCards(carded=len(rnd.fixtures) - len(missing), missing=missing,
                      unlisted=tuple(m for m in here
                                     if frozenset((m.home, m.away)) not in listed))


def unlisted_words(count: int) -> str:
    """The evidence a round was played when none of its cards is a fixture it lists."""
    if count == 1:
        return ("the one card read for that round is of a fixture the draw does "
                "not list")
    return (f"the {count} cards read for that round are all of fixtures the draw "
            f"does not list")


def later_round_with_cards(order: Iterable[str],
                           carded: Container[str]) -> dict[str, str]:
    """Per round, the earliest round after it that a card was read for.

    ``""`` where there is none, which is what a round the season has not reached
    looks like when nobody asks the clock. The rule two modules answer that question
    by, kept in one place: this module asks it per fixture and
    :mod:`~matchcentre.discover` per round, and a club reading the terminal and the
    dashboard must not be told two different things about one round.
    """
    out: dict[str, str] = {}
    later = ""
    for number in reversed(list(order)):
        out[number] = later
        if number in carded:
            later = number
    return out


def fixtures_without_cards(matches: Sequence[Match],
                           competition: Competition) -> list[MissingCard]:
    """Declared fixtures the season has gone past with no card, in draw order.

    The one absence in this program that needs no guesswork. Every other one has a
    second reading -- a team missing from a round might be resting, a round with no
    cards might not have happened -- but a fixture the club itself declared is a
    scorecard that exists and has not been exported.

    Three refusals shape it.

    *No clock.* Whether a round has happened is answered by other cards, never by a
    date: ``--generated`` is an input and the draw's dates are a club's typing, so a
    date comparison would have one set of files produce a different report every
    week. A round counts as reached when it has a card, or when any later round does.

    *Only a declared draw.* A derived draw's fixtures are built out of these same
    cards, so none of them can be short of one, and reporting a fixture the club
    never published as missing is the fabrication F2 forbids. The gate is the
    competition's ``draw_source`` rather than each ``Round.source``: declaring one
    round leaves the rounds either side of it derived from the cards, and those are
    safe for exactly the same reason.

    *The pair unordered.* A card with home and away swapped is still that fixture's
    card. Which side was really at home is ``draw-reversed``'s finding in
    :mod:`~matchcentre.validate`, and calling the card missing as well would be two
    findings about one card that is sitting in the folder.

    Finals rounds need no special case. They pair by ladder position, so the fixture
    list is normally empty and nothing comes back; a club that did type the pairings
    in has declared them like any other fixture, and the missing card is as real.
    """
    if competition.draw_source != DRAW_DECLARED:
        return []
    carded: dict[str, list[Match]] = defaultdict(list)
    for match in matches:
        if match.is_bye:
            continue        # a scheduled rest is not a card, and needs none
        carded[match.round].append(match)
    order = competition.round_order
    later = later_round_with_cards(order, carded)
    out: list[MissingCard] = []
    for number in order:
        here = carded.get(number, ())
        if not here and not later[number]:
            continue        # the season has not reached this round
        rnd = competition.rounds[number]
        counted = round_cards(rnd, here)
        for home, away in counted.missing:
            out.append(MissingCard(round=number, home=home, away=away,
                                   cards=counted.carded, fixtures=len(rnd.fixtures),
                                   later=later[number],
                                   unlisted=len(counted.unlisted)))
    return out


# --- points -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PointsLedger:
    """Earned and averaged points held apart, per the averaging rule.

    ``base_average`` is the average over decided matches only. Because every
    averaged match is credited exactly that value, ``average`` equals
    ``base_average``: awarding the mean cannot move the mean. That identity is the
    invariant this split exists to protect.

    Under a ruleset with ``average_unplayed`` off, ``averaged_matches`` is zero —
    no match was credited an average — so ``counted`` is the decided matches alone
    and an unscored match is absent from numerator and divisor both.
    """

    earned: float
    decided: int
    averaged_matches: int
    base_average: float | None

    @property
    def averaged(self) -> float:
        if self.base_average is None:
            return 0.0
        return self.base_average * self.averaged_matches

    @property
    def counted(self) -> int:
        """Matches the average divides by — zero until something is decided."""
        if self.base_average is None:
            return 0
        return self.decided + self.averaged_matches

    @property
    def total(self) -> float:
        return self.earned + self.averaged

    @property
    def average(self) -> float | None:
        if not self.counted:
            return None
        return self.total / self.counted

    def breakdown(self) -> PointsBreakdown:
        return PointsBreakdown(
            earned=round_stored(self.earned),
            averaged=round_stored(self.averaged),
            averaged_matches=self.averaged_matches,
        )


def points_ledger(matches: Sequence[Match], team: str) -> PointsLedger:
    """A team's points, split into earned and averaged halves.

    Which half an unscored match lands in is the ruleset's decision, taken per
    match from the match's own ``rules`` so that no caller can pass a ruleset that
    disagrees with the one the match was scored under.
    """
    earned = 0.0
    decided = 0
    averaged = 0
    for match in matches:
        if not match.involves(team):
            continue
        points = match.points_for(team)
        if points is None:
            if match.rules.average_unplayed:
                averaged += 1
            # Otherwise the match is simply not counted: not a zero, absent.
        else:
            earned += points
            decided += 1
    base = earned / decided if decided else None
    return PointsLedger(earned=earned, decided=decided, averaged_matches=averaged,
                        base_average=base)


def round_points(matches: Sequence[Match], team: str) -> list[RoundPoints]:
    """Points per round, averaged rounds included at the team's own average.

    A round that earns nothing and is not averaged is left out rather than
    recorded as a zero, so a washed-out round cannot become the team's "worst
    round" under a ruleset that does not average.
    """
    ledger = points_ledger(matches, team)
    out: list[RoundPoints] = []
    for match in chronological(matches):
        if not match.involves(team):
            continue
        points = match.points_for(team)
        if points is None:
            if not match.rules.average_unplayed or ledger.base_average is None:
                continue
            points = ledger.base_average
        out.append(RoundPoints(round=match.round, points=round_stored(points)))
    return out


# --- ladder ------------------------------------------------------------


def h2h_key(a: str, b: str) -> str:
    """The ``head_to_head`` key for a pairing: both slugs, sorted lexically."""
    low, high = sorted((a, b))
    return f"{low}|{high}"


def head_to_head(matches: Sequence[Match],
                 competition: Competition) -> dict[str, HeadToHead]:
    """Pairwise records keyed ``"slug-a|slug-b"``, the pair sorted lexically.

    One card per fixture, so a re-export does not have two teams having met three
    times in a two-round season.

    A meeting is a night the two teams played each other, which is not the same as
    a night that reached a result: rain at 8pm with nobody ticking Match Cancelled,
    or a forfeit with neither side's box ticked, leaves a card that was played and
    decided nothing. The predecessor skipped those (``if not match.decided``), so
    the dashboard's grid printed a blank cell titled "not played yet" over a card
    listed on the page above it, and that night's games were in both teams' games
    columns -- which ask only :attr:`Match.counts_as_played` -- and in no pairing.

    So ``played`` and the games count every meeting, the wins and draws count the
    decided ones only, and ``undecided`` says how many are the difference. A
    cancelled or unplayed card is still nothing anywhere, on the same reasoning as
    everywhere else: it was not played.

    In a grade that cannot draw (``[rules] draws = false``) a level night is counted
    as undecided, which is what it is: the association decides every match, so this
    one reached no result anybody recorded. The grid is the only other place a D can
    appear, and a cell reading "1 draw" beneath a banner reading "no draw" is the
    page arguing with itself.

    Finals are counted here and not in the ladder (F11), which is the one place two
    figures on the same page are worked out over different sets of matches. It is
    the right answer twice -- a semi-final is a night two teams met, and is not a
    round of the season being ranked -- but it is not a difference a reader can be
    expected to guess, and the demo season is the proof: the aces and the rockets
    met twice, and over the pair the aces lead on games while the regular meeting
    the ladder counts was a 24-6 loss. So ``competition`` is asked for, rather than
    defaulted, and ``finals`` carries the count the page needs to say so.
    """
    tally: dict[tuple[str, str], HeadToHead] = {}
    for match in chronological(scoreable(matches)):
        if not match.counts_as_played or match.is_bye:
            continue
        low, high = sorted((match.home, match.away))
        current = tally.get((low, high), HeadToHead())
        winner = match.winner          # None for a draw and for no result alike
        settled = match.decided and not match.is_unscoreable_draw
        tally[(low, high)] = HeadToHead(
            played=current.played + 1,
            wins_low=current.wins_low + (1 if winner == match.side_of(low) else 0),
            wins_high=current.wins_high + (1 if winner == match.side_of(high) else 0),
            draws=current.draws + (1 if settled and winner is None else 0),
            games_low=current.games_low + match.games_for(low),
            games_high=current.games_high + match.games_for(high),
            undecided=current.undecided + (0 if settled else 1),
            finals=current.finals + (1 if competition.is_finals_round(match.round)
                                     else 0),
        )
    return {f"{low}|{high}": rec for (low, high), rec in sorted(tally.items())}


def _h2h_compare(matches: Sequence[Match], a: str, b: str) -> int:
    """Head-to-head tiebreak: +1 if ``a`` ranks above ``b``, -1 below, 0 level.

    Decided meetings only, unlike :func:`head_to_head`, which counts every meeting
    that was played. Deliberate, and the difference is the question: a tiebreak asks
    which of these two beat the other, and a night that reached no result has no
    answer to give. Level here falls through to the next key in the ruleset.

    The second difference is the caller's, not this function's, and matters more:
    :func:`ladder` hands it :func:`ladder_matches`, so a finals meeting is not in
    here either, while :func:`head_to_head` counts one. Both are right about their
    own question and the page has to say which it is answering -- see the ``finals``
    field on :class:`HeadToHead`.
    """
    wins_a = wins_b = games_a = games_b = 0
    for match in matches:
        if not (match.involves(a) and match.involves(b) and match.decided):
            continue
        games_a += match.games_for(a)
        games_b += match.games_for(b)
        winner = match.winner
        if winner == match.side_of(a):
            wins_a += 1
        elif winner == match.side_of(b):
            wins_b += 1
    if wins_a != wins_b:
        return 1 if wins_a > wins_b else -1
    if games_a != games_b:
        return 1 if games_a > games_b else -1
    return 0


@dataclass(frozen=True, slots=True)
class _Tally:
    """The countable half of a ladder row: everything but points."""

    wins: int = 0
    games_won: int = 0
    games_lost: int = 0
    sets_won: int = 0
    sets_lost: int = 0


def _tally(matches: Sequence[Match], team: str) -> _Tally:
    wins = games_won = games_lost = sets_won = sets_lost = 0
    for match in matches:
        if not match.involves(team) or not match.counts_as_played:
            continue
        games_won += match.games_for(team)
        games_lost += match.games_against(team)
        sets_won += match.sets_for(team)
        sets_lost += match.sets_against(team)
        if match.outcome_for(team) is Outcome.WIN:
            wins += 1
    return _Tally(wins=wins, games_won=games_won, games_lost=games_lost,
                  sets_won=sets_won, sets_lost=sets_lost)


def _ladder_value(key: str, ledger: PointsLedger, tally: _Tally) -> float | None:
    """One ladder key's value for one team, highest-first in every case."""
    if key == "points_average":
        return ledger.average
    if key == "points":
        return ledger.total
    if key == "wins":
        return float(tally.wins)
    if key == "games_diff":
        return float(tally.games_won - tally.games_lost)
    if key == "games_won":
        return float(tally.games_won)
    if key == "sets_diff":
        return float(tally.sets_won - tally.sets_lost)
    if key == "sets_won":
        return float(tally.sets_won)
    # `head_to_head` and `name` are not values; the comparator handles them.
    raise ValueError(f"{key!r} is not a numeric ladder key; known: "
                     f"{in_words(LADDER_KEYS)}")


def ladder(matches: Sequence[Match], competition: Competition) -> list[str]:
    """Team slugs in ladder order, following ``competition.rules.ladder_by``.

    A team with nothing counted has no average and sorts last however the rest of
    its row reads: it has not played, so it is not on the table so much as under
    it.

    Finals are not in it (F11): see :func:`regular_season`. Neither is a repeated
    card, twice or at all: see :func:`duplicates`.

    Head-to-head only means anything between the two teams being compared, so the
    ordering is a pairwise comparator rather than a sort key. That makes it
    formally non-transitive in a three-way tie; ``ladder_by`` is required to end
    with ``name``, which keeps the result deterministic even then.
    """
    return _ranked(ladder_matches(matches, competition), competition)


def _ranked(matches: Sequence[Match], competition: Competition) -> list[str]:
    """:func:`ladder`, of a list :func:`ladder_matches` has already built.

    For the callers that hold that list, or a slice of it, and must not hand it
    back to be built again (:func:`ladder_matches` says why).
    """
    teams = competition.teams
    ledgers = {slug: points_ledger(matches, slug) for slug in teams}
    tallies = {slug: _tally(matches, slug) for slug in teams}
    keys = competition.rules.ladder_by

    def rank(a: str, b: str) -> int:
        la, lb = ledgers[a], ledgers[b]
        # No counted matches sorts last, and two such teams fall through to name.
        if (la.average is None) != (lb.average is None):
            return -1 if lb.average is None else 1
        for key in keys:
            if key == "name":
                break
            if key == "head_to_head":
                verdict = _h2h_compare(matches, a, b)
                if verdict:
                    return -verdict
                continue
            left = _ladder_value(key, la, tallies[a])
            right = _ladder_value(key, lb, tallies[b])
            if left is None or right is None:
                continue          # nothing to compare on this key
            # Compared at the stored precision, ordered on the raw value: 4.65 and
            # 4.6500000000000004 are one number, and treating them as two would
            # make the ladder depend on float noise.
            if round(left, PRECISION) != round(right, PRECISION):
                return -1 if left > right else 1
        # The name is the last resort, with the slug behind it so that two teams
        # sharing a display name still have a stable order.
        left_name = (competition.team_name(a), a)
        right_name = (competition.team_name(b), b)
        if left_name == right_name:
            return 0
        return -1 if left_name < right_name else 1

    return sorted(teams, key=functools.cmp_to_key(rank))


def ladder_complete(matches: Sequence[Match], competition: Competition) -> bool:
    """Rule F10: whether the ladder is a settled order or a snapshot.

    True only when every team has the same number of counted matches. Mid-round,
    with one card of three in, the leaders are simply the teams whose card arrived
    — and presenting that as a table of positions is how a dashboard tells a lie
    without a single wrong number in it.

    A competition where nothing has been counted yet is not complete either: an
    empty ladder is not a settled one.
    """
    if not competition.teams:
        return False
    # The same set of matches the ladder itself is built from, finals excluded, or
    # two teams meeting in a semi-final would report the table as unsettled for the
    # rest of time: they have played one more match than everybody else and always
    # will have. A card exported twice is the same trap in reverse -- it would read
    # as a team a match ahead of the field. Two cards that disagree about the result
    # leave the pair of them a match behind it instead, and two that agree about the
    # result and not about the names are counted once like anybody else -- which does
    # put that pair a match ahead, correctly: they played, and the teams either side
    # of them have not.
    matches = ladder_matches(matches, competition)
    counts = {points_ledger(matches, slug).counted for slug in competition.teams}
    return len(counts) == 1 and counts != {0}


def through_round(matches: Iterable[Match], number: str) -> list[Match]:
    """The matches of round *number* and every round before it: the ladder after it.

    Stated once, because two things ask it -- the ladder history, round by round,
    and the check against an association's sheet, which is a statement about one
    round -- and it is ordered by :func:`round_sort_key`, so round 10 comes after
    round 2 and not before it. A padded label is a round of its own just before
    the plain one: the slice after ``03`` stops short of ``3``, and the slice
    after ``3`` has ``03`` and ``３`` in it.
    """
    limit = round_sort_key(number)
    return [m for m in matches if round_sort_key(m.round) <= limit]


def ladder_history(matches: Sequence[Match],
                   competition: Competition) -> dict[str, list[LadderEntry]]:
    """Position after each round, each round's ladder recomputed from scratch.

    Regular rounds only, so the line ends where the ladder does (F11), and one card
    per fixture, so a round with a re-export in it does not put a step in the line.
    """
    history: dict[str, list[LadderEntry]] = defaultdict(list)
    matches = ladder_matches(matches, competition)
    for number in rounds_present(matches):
        seen = through_round(matches, number)
        for position, slug in enumerate(_ranked(seen, competition), start=1):
            average = points_ledger(seen, slug).average
            history[slug].append(LadderEntry(round=number, position=position,
                                             points_average=round_stored(average)))
    return dict(history)


# --- team statistics ---------------------------------------------------


def team_stats(matches: Sequence[Match],
               competition: Competition) -> dict[str, TeamStats]:
    """Full statistics for every team in the competition, position included.

    A team row is the regular season, wholesale (F11): its points, its games, its
    form, its home and away split. Every one of those figures is a column of the
    ladder or an explanation of one, and a row that counted a grand final would sit
    inside a table that did not.
    """
    teams = competition.teams
    # One list, built once, for every figure below -- the ladder, the per-round
    # points, the breakdown that says how much of a total was averaged. Assembling
    # the same three steps here by hand is how a row and its table come to describe
    # different sets of matches.
    ordered = chronological(ladder_matches(matches, competition))
    positions = {slug: i for i, slug in enumerate(_ranked(ordered, competition),
                                                 start=1)}
    # From the cards, as `ordered` is. Handed `ordered`, it would build that list
    # again out of one with none of the cards the first build set aside.
    history = ladder_history(matches, competition)
    # From the raw list, not from `ordered`: the Share column's scope is its own
    # (:func:`_share_walk`), and the disputed nights it cannot credit are a
    # disagreement between two cards, only one of which survives `scoreable`.
    gaps = contribution_gaps(matches, competition)

    out: dict[str, TeamStats] = {}
    for slug, team in teams.items():
        played = [m for m in ordered if m.involves(slug)]
        decided = [m for m in played if m.decided]
        outcomes = [m.outcome_for(slug) for m in decided]
        ledger = points_ledger(ordered, slug)
        per_round = round_points(ordered, slug)
        counts = _tally(ordered, slug)
        letters = [_outcome_letter(o) for o in outcomes]
        captain = team.player(team.captain) if team.captain else None

        out[slug] = TeamStats(
            code=team.code,
            name=team.name,
            short=team.short_name,
            slug=slug,
            home_court=team.home_court,
            # A declared captain on no card has no Player to take a name from,
            # and the name rebuilt from the slug loses what slugging drops: the
            # apostrophe in "Devi O'Raman" came back a space. The club's own
            # spelling first, then the slug as the last resort it always was.
            captain=(captain.name if captain
                     else (team.captain_declared or unslug(team.captain))
                     if team.captain else None),
            players=[p.slug for p in team.players],
            played=len(decided),
            won=outcomes.count(Outcome.WIN),
            lost=outcomes.count(Outcome.LOSS),
            drawn=outcomes.count(Outcome.DRAW),
            points=round_stored(ledger.total),
            points_average=round_stored(ledger.average),
            points_breakdown=ledger.breakdown(),
            position=positions[slug],
            games_won=counts.games_won,
            games_lost=counts.games_lost,
            games_diff=counts.games_won - counts.games_lost,
            sets_won=counts.sets_won,
            sets_lost=counts.sets_lost,
            sets_diff=counts.sets_won - counts.sets_lost,
            win_pct=set_win_pct(counts.sets_won, counts.sets_lost),
            # What the ladder's average divides by, so the dashboard can say *why*
            # the order is provisional when `ladder_complete` is false (F10).
            counted=ledger.counted,
            home=_side_record(played, slug, "home"),
            away=_side_record(played, slug, "away"),
            form=letters[-5:],
            streak=_streak(letters),
            by_slot=_team_by_slot(played, slug, competition.fmt),
            ladder_history=history.get(slug, []),
            best_round=max(per_round, key=lambda r: r.points, default=None),
            worst_round=min(per_round, key=lambda r: r.points, default=None),
            contribution_gap=gaps[slug],
        )
    return out


def _outcome_letter(outcome: Outcome) -> str:
    return {Outcome.WIN: "W", Outcome.LOSS: "L", Outcome.DRAW: "D"}[outcome]


def _side_record(played: Sequence[Match], team: str, side: str) -> SideRecord:
    subset = [m for m in played
              if m.side_of(team) == side and m.counts_as_played]
    outcomes = [m.outcome_for(team) for m in subset if m.decided]
    return SideRecord(
        played=len(outcomes),
        won=outcomes.count(Outcome.WIN),
        lost=outcomes.count(Outcome.LOSS),
        drawn=outcomes.count(Outcome.DRAW),
        games_won=sum(m.games_for(team) for m in subset),
        games_lost=sum(m.games_against(team) for m in subset),
    )


def _team_by_slot(played: Sequence[Match], team: str,
                  fmt: MatchFormat) -> dict[str, SlotRecord]:
    """Per-slot record: whether a team's doubles carries it or its singles does.

    Keyed by the competition format's own slot keys. The predecessor's fixed
    ``D1, S1, S2, D2`` was one club's card; a six-set or all-singles competition
    had every row silently dropped.
    """
    tally = {slot.key: SlotRecord() for slot in fmt.slots}
    for match in played:
        if not match.counts_as_played:
            continue
        side = match.side_of(team)
        for result in match.sets:
            key = result.slot.key
            if key not in tally:
                # A card describing a different shape from the competition's. Its
                # games still count in the totals; attributing them to a slot that
                # does not exist here would invent a column. validate.py reports
                # the disagreement (F-format).
                continue
            current = tally[key]
            mine = result.games_for(side)
            theirs = result.games_for("away" if side == "home" else "home")
            winner = result.winner
            tally[key] = SlotRecord(
                won=current.won + (1 if winner == side else 0),
                lost=current.lost + (1 if winner is not None and winner != side else 0),
                games_won=current.games_won + mine,
                games_lost=current.games_lost + theirs,
            )
    return {slot.key: tally[slot.key] for slot in fmt.slots}


def _streak(letters: Sequence[str]) -> Streak:
    """The current run at the end of a chronological result sequence."""
    if not letters:
        return Streak(type=None, length=0)
    last = letters[-1]
    length = 0
    for letter in reversed(letters):
        if letter != last:
            break
        length += 1
    return Streak(type=last, length=length)


def _longest_run(letters: Sequence[str], target: str) -> int:
    best = current = 0
    for letter in letters:
        current = current + 1 if letter == target else 0
        best = max(best, current)
    return best


# --- the share of a team's games ---------------------------------------
# Serves both halves above: a player's Share cell, and the team record of what no
# cell holds. One walk, because those two are one subtraction.


def _earns_share(registered: Player | None, borrowed: bool) -> bool:
    """Whether the games this person won belong to a row in their side's own table.

    Two people on court do not have one. Somebody no card registers anywhere, and
    somebody registered to another squad (:func:`is_fill_in`) -- who has a row, under
    the team they belong to, and whose games tonight were won for a team whose table
    does not list them. Both are in their own playing record either way.

    So the column comes to less than the whole, which is what F1 asks for: the
    alternative is crediting a team's games to a row that is not there.
    """
    return registered is not None and not borrowed


@dataclass(slots=True)
class _GapTally:
    """Mutable scratch for one team; only the frozen :class:`ContributionGap` escapes."""

    credited: float = 0.0
    outsiders: int = 0
    short: int = 0
    crowded: int = 0
    nights: int = 0


@dataclass(frozen=True, slots=True)
class _Shares:
    """How one competition's games won divide among the people who won them.

    ``per_player`` is keyed by player slug and ``per_team`` by team slug, and the
    second is the first's remainder: a team's gap is its games won less every share
    this walk handed to a player of it.
    """

    per_player: dict[str, float]
    per_team: dict[str, ContributionGap]


def _share_walk(matches: Sequence[Match], competition: Competition) -> _Shares:
    """Hand each team's games won to the players who won them, and count what is left.

    The one place the partition is decided. Three separate readers used to decide it:
    :func:`player_stats` credited the shares, the exporter divided them by a total it
    fetched itself, and the dashboard re-walked the cards in JavaScript to work out why
    the column was short -- re-testing the night, the slot's shape, the roster, the
    finals and the disputed pair, in a second language. The shortfall is not a separate
    question from the credit; it is the credit's remainder, so it is counted here on the
    same branch that declines to award it.

    The scope is :func:`ladder_matches`, the list every ladder figure is computed from,
    because this column is a partition of one of those figures: the team's games won.
    A player's own record is wider -- every match they played, finals and all -- but a
    share is of a total, and the total is the one printed in the Games column. Composing
    three of the four steps here instead would put a denominator on a tile that the row
    two tables up contradicts: 37 against a row reading 18 on a grade whose level night
    the ladder cannot price, or 61 against 37 with a grand final credited. Both halves
    of the subtraction come from this one list, so a night dropped from the total is
    dropped from the credit too; dropping it from one of them is how a page reports a
    shortfall that is not there, or a column over a hundred.
    """
    counted = chronological(ladder_matches(matches, competition))
    # Asked of the whole list rather than of the one above, which is one card per
    # fixture: the disagreement is between two cards, and only one of them is here.
    disputed = disputed_fixtures(matches)
    index = competition.player_index()
    per_player: dict[str, float] = defaultdict(float)
    tally = {slug: _GapTally() for slug in competition.teams}

    for match in counted:
        if not match.counts_as_played:
            continue
        teams = {"home": match.home, "away": match.away}
        if match.key in disputed:
            # A night two cards agree the result of and name differently: in the
            # ladder, and in nobody's record. Counted once for each side, as a night
            # and not as its rubbers, because that is how the data health section
            # names it -- the pair of files it came from.
            for team in teams.values():
                if team in tally:
                    tally[team].nights += 1
            continue
        rosters = {"home": match.home_roster, "away": match.away_roster}
        for result in match.sets:
            for side, team in teams.items():
                players = result.players_for(side)
                earners = [slug for slug in players
                           if _earns_share(index.get(slug),
                                           is_fill_in(slug, index.get(slug),
                                                      rosters[side], team))]
                for slug in earners:
                    # A share, not a tally: the column has to add up to the team's
                    # games. A doubles pair won its games together, so half each.
                    #
                    # Divided by the slot's own shape rather than by the number of
                    # names the card had. A doubles rubber missing one name credits
                    # half and leaves the rest with nobody, which is what happened;
                    # handing the absent partner's half to whoever was printed would
                    # credit a person for a rubber the card does not say they were in.
                    share = result.games_for(side) / result.slot.players
                    per_player[slug] += share
                    if team in tally:
                        tally[team].credited += share
                if team not in tally:
                    # A card naming a side this competition has no team for. The
                    # validator reports it; there is no table here to be short.
                    continue
                if len(earners) < len(players):
                    tally[team].outsiders += 1
                if len(players) < result.slot.players:
                    tally[team].short += 1
                if len(players) > result.slot.players:
                    # The same division the other way: two names in a one-place slot
                    # are each credited the whole rubber, so the rows hold more than
                    # the team won. Dividing by the names instead would make the
                    # column add up over a card whose own marker contradicts it.
                    tally[team].crowded += 1

    gaps: dict[str, ContributionGap] = {}
    for slug, team_tally in tally.items():
        # The same total the cells are divided by, from the same list, so a cell and
        # the caption under it cannot be short of different denominators.
        total = _tally(counted, slug).games_won
        short = total - team_tally.credited
        gaps[slug] = ContributionGap(
            games=round_stored(short),
            team_games_won=total,
            share_pct=_share_pct(short, total),
            rubbers_with_outsiders=team_tally.outsiders,
            rubbers_short_a_name=team_tally.short,
            rubbers_overfull=team_tally.crowded,
            nights_not_credited=team_tally.nights,
        )
    return _Shares(per_player=dict(per_player), per_team=gaps)


def contribution_gaps(matches: Sequence[Match],
                      competition: Competition) -> dict[str, ContributionGap]:
    """Per team, the games won that no player row holds, and the reasons.

    One entry per team in the competition, zeroes included: see
    :class:`ContributionGap` for why nothing is left out.
    """
    return _share_walk(matches, competition).per_team


# --- player statistics -------------------------------------------------


@dataclass(slots=True)
class _PlayerAccumulator:
    """Mutable scratch space; only the frozen :class:`PlayerStats` escapes."""

    sets_played: int = 0
    sets_won: int = 0
    sets_lost: int = 0
    games_won: int = 0
    games_lost: int = 0
    match_ids: list[tuple[str, str, str]] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    fill_ins: set[tuple[str, str, str]] = field(default_factory=set)
    singles: list[tuple[str | None, int, int]] = field(default_factory=list)
    doubles: list[tuple[str | None, int, int]] = field(default_factory=list)
    slots: dict[str, list[str | None]] = field(default_factory=dict)
    partners: dict[str, list[tuple[str | None, int, int]]] = field(default_factory=dict)
    opponents: dict[str, list[tuple[str | None, int, int]]] = field(default_factory=dict)
    ratings: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)


def player_stats(matches: Sequence[Match],
                 competition: Competition) -> dict[str, PlayerStats]:
    """Statistics for every registered player, plus anyone who filled in.

    Registered players who have never played come back with zeros rather than
    being absent, so the dashboard can list a full squad and show "played 0 of 2".

    Finals are in a player's record -- they played them -- but a card exported twice
    is not two nights out, and two cards that disagree about a night cannot say who
    was on court for it. So this counts the same one-card-per-fixture list the
    ladder does, and differs from it over the finals and over one more thing.

    That one more thing is :func:`disputed_fixtures`: a night two cards agree about
    and name differently is in the ladder and in nobody's record here. The ladder is
    answering "what was the result", which both cards settle; this is answering "who
    played", which they do not, and the card kept for the first question is not
    evidence for the second.
    """
    ordered = chronological(scoreable(matches), competition.rounds)
    # Asked of the whole list rather than of `ordered`, which is one card per
    # fixture: the disagreement is between two cards, and only one of them is here.
    disputed = disputed_fixtures(matches)
    index = competition.player_index()
    teams = competition.teams
    # The Share column, numerator and denominator both, from the one walk that
    # decides it (:func:`_share_walk`). Not accumulated in the loop below, which is
    # answering "who played" over a different set of matches: a player's own record
    # is every match they played, finals included, and their share is a partition of
    # the team row's Games column, which is the list the ladder prices.
    shares = _share_walk(matches, competition)
    names = player_names(ordered, competition)
    slot_keys = [slot.key for slot in competition.fmt.slots]

    acc: dict[str, _PlayerAccumulator] = defaultdict(_PlayerAccumulator)
    for slug in index:
        acc[slug]  # noqa: B018 - materialise the zero rows

    for match in ordered:
        key = (match.round, match.home, match.away)
        rosters = {"home": match.home_roster, "away": match.away_roster}
        sides_team = {"home": match.home, "away": match.away}
        if not match.counts_as_played:
            # A cancelled night leaves nothing behind, whatever reached the card
            # before play stopped: a rubber nobody finished playing is not a rubber
            # anybody played. Nor are its ratings a round of anybody's history. Its
            # roster block registers nobody (discover.py builds the squads from the
            # cards that count) and no check reads it, so a rating kept from it would
            # be the one figure on the page no finding could ever be about.
            continue
        # Ratings come off the roster block, not the set rows: the card prints a
        # rating for every registered player, so a squad member who was named but
        # did not get on court still has one. Harvesting these inside the set loop
        # instead would leave an unused player showing no rating at all.
        for slug, rating in match.ratings.items():
            acc[slug].ratings[match.round] = (rating.singles, rating.doubles)
        if match.key in disputed:
            # Played, counted, and credited to nobody. Whole-match rather than the
            # disputed rubber alone: crediting the rubbers both cards agree on would
            # mean building a card out of the two, and printing that on a page under
            # a `Source:` line naming one PDF whose own rows read differently. The
            # ratings above are kept: the card counts, and its squad block is not a
            # claim about who took a court.
            continue
        for result in match.sets:
            sides = {"home": result.home_players, "away": result.away_players}
            for side, players in sides.items():
                other = "away" if side == "home" else "home"
                mine = result.games_for(side)
                theirs = result.games_for(other)
                winner = result.winner
                verdict = (None if winner is None
                           else ("W" if winner == side else "L"))
                for slug in players:
                    entry = acc[slug]
                    entry.sets_played += 1
                    entry.games_won += mine
                    entry.games_lost += theirs
                    if verdict == "W":
                        entry.sets_won += 1
                    elif verdict == "L":
                        entry.sets_lost += 1
                    if verdict:
                        entry.results.append(verdict)
                    if key not in entry.match_ids:
                        entry.match_ids.append(key)
                    if is_fill_in(slug, index.get(slug), rosters[side],
                                  sides_team[side]):
                        entry.fill_ins.add(key)
                    bucket = (entry.doubles if result.slot.doubles
                              else entry.singles)
                    bucket.append((verdict, mine, theirs))
                    entry.slots.setdefault(result.slot.key, []).append(verdict)
                    if result.slot.doubles:
                        for partner in players:
                            if partner != slug:
                                entry.partners.setdefault(partner, []).append(
                                    (verdict, mine, theirs))
                    # In a doubles set every player on one side faces every player
                    # on the other, so the head-to-head grid is the cross product
                    # of the two rows.
                    for opponent in sides[other]:
                        entry.opponents.setdefault(opponent, []).append(
                            (verdict, mine, theirs))

    out: dict[str, PlayerStats] = {}
    for slug, entry in acc.items():
        registered = index.get(slug)
        team_slug = registered.team if registered else None
        team = teams.get(team_slug) if team_slug else None
        # Matches the player could have been picked for: a cancelled round is not
        # one, so "played 2 of 3" counts the same three nights the association's
        # sheet counts.
        available = (sum(1 for m in ordered
                         if m.involves(team_slug) and m.counts_as_played)
                     if team_slug else len(entry.match_ids))
        rating_history = [
            RatingPoint(round=number, singles=values[0], doubles=values[1])
            for number, values in sorted(entry.ratings.items(),
                                         key=lambda kv: competition.round_key(kv[0]))
        ]
        latest = _latest_rating(rating_history)
        win_pct = set_win_pct(entry.sets_won, entry.sets_lost)
        gap = shares.per_team.get(team_slug) if team_slug else None
        team_total = gap.team_games_won if gap else 0
        credit = shares.per_player.get(slug, 0.0)
        out[slug] = PlayerStats(
            slug=slug,
            name=names.get(slug, unslug(slug)),
            team_code=team.code if team else None,
            team=team_slug,
            team_name=team.name if team else None,
            is_captain=bool(registered and registered.is_captain),
            rating={"singles": latest[0], "doubles": latest[1]},
            rating_history=rating_history,
            matches=len(entry.match_ids),
            available=available,
            sets_played=entry.sets_played,
            sets_won=entry.sets_won,
            sets_lost=entry.sets_lost,
            games_won=entry.games_won,
            games_lost=entry.games_lost,
            games_diff=entry.games_won - entry.games_lost,
            win_pct=win_pct,
            singles=_discipline(entry.singles),
            doubles=_discipline(entry.doubles),
            streak=_streak(entry.results),
            longest_win_streak=_longest_run(entry.results, "W"),
            partners=_pair_records(entry.partners, names),
            opponents=_pair_records(entry.opponents, names),
            contribution=Contribution(
                games_won=round_stored(credit),
                team_games_won=team_total,
                share_pct=_share_pct(credit, team_total),
            ),
            fill_in_appearances=len(entry.fill_ins),
            by_slot={key: _slot_record(entry.slots.get(key, []))
                     for key in slot_keys},
        )
    return out


def is_fill_in(slug: str, registered: Player | None, roster: Sequence[str],
               side_team: str) -> bool:
    """A fill-in is someone playing for a side they do not belong to.

    Two independent signals, because either source can be missing: the card's own
    roster block, and the team a player is registered to. Either one flagging is
    enough.
    """
    if registered is not None and registered.team != side_team:
        return True
    return bool(roster) and slug not in roster


def _slot_record(verdicts: Sequence[str | None]) -> PlayerSlotRecord:
    """One player's sets in one slot: "W", "L", or None for a set nobody won."""
    won = verdicts.count("W")
    return PlayerSlotRecord(played=len(verdicts), won=won,
                            win_pct=set_win_pct(won, verdicts.count("L")))


def _discipline(entries: Sequence[tuple[str | None, int, int]]) -> DisciplineRecord:
    return DisciplineRecord(
        played=len(entries),
        won=sum(1 for verdict, _, _ in entries if verdict == "W"),
        lost=sum(1 for verdict, _, _ in entries if verdict == "L"),
        games_won=sum(mine for _, mine, _ in entries),
        games_lost=sum(theirs for _, _, theirs in entries),
    )


def _pair_records(grouped: dict[str, list[tuple[str | None, int, int]]],
                  names: dict[str, str]) -> list[PairRecord]:
    """Partner/opponent rows, most-played first for a stable, useful order."""
    records = [
        PairRecord(
            slug=slug,
            name=names.get(slug, unslug(slug)),
            sets=len(entries),
            won=sum(1 for verdict, _, _ in entries if verdict == "W"),
            lost=sum(1 for verdict, _, _ in entries if verdict == "L"),
            games_won=sum(mine for _, mine, _ in entries),
            games_lost=sum(theirs for _, _, theirs in entries),
        )
        for slug, entries in grouped.items()
    ]
    return sorted(records, key=lambda r: (-r.sets, -r.games_won, r.slug))


def _latest_rating(history: Sequence[RatingPoint]) -> tuple[float | None, float | None]:
    """Most recent non-null value of each rating, tracked independently.

    Cards occasionally print one rating and not the other, so carrying the two
    forward separately avoids a blank doubles rating erasing a known one.
    """
    singles = doubles = None
    for point in history:
        if point.singles is not None:
            singles = point.singles
        if point.doubles is not None:
            doubles = point.doubles
    return singles, doubles


def player_names(matches: Sequence[Match],
                 competition: Competition) -> dict[str, str]:
    """Slug -> display name, for every registered player and every name seen.

    Resolution order, first hit wins:

    1. the name on the :class:`~matchcentre.competition.Player` record, which is
       where a config-declared spelling lands, and failing that the properly-cased
       spelling the card's roster block printed;
    2. for anyone no roster block registers, the name discovery gave them in
       ``Competition.fill_in_names``, from the set row's own spelling, which is
       where a ``[player_names]`` line for a fill-in lands;
    3. the slug, un-slugged and title-cased.

    A registered spelling therefore beats anything a set row implies, which is the
    point: the roster block prints a full name like ``Alexandra Fitzsimmons`` while
    a set row may be filled in as ``A Fitzsimmons``, and the two slug differently
    unless someone has written an alias. The predecessor discarded the printed
    spelling entirely and then required it back from configuration.
    """
    names = {slug: player.name
             for slug, player in competition.player_index().items()
             if player.name}
    for match in chronological(matches):
        for result in match.sets:
            for slug in (*result.home_players, *result.away_players):
                # Discovery names every fill-in from the set row's own spelling,
                # so only a set row it never read has the slug alone: un-slug
                # that rather than showing dashes in the UI, and let F7 mark it
                # as derived.
                names.setdefault(slug, competition.fill_in_names.get(slug)
                                 or unslug(slug))
    return dict(sorted(names.items()))
