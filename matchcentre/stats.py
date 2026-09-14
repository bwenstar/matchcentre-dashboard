"""Derived team and player statistics: the ladder, and everything under it.

Every function here is a pure function of ``list[Match]`` plus the competition, so
the dashboard's numbers can always be regenerated from the cards alone and nothing
cached in ``season.json`` can drift away from them.

Four rules drive the shape of this module. The first two were already true of the
predecessor and are easy to break by accident; the last two are new, and each
closes a hole that produced wrong numbers.

*Averaging.* When ``rules.average_unplayed`` is set, an unscored match (a bye, a
washout) is credited the team's own average over the matches it actually played.
That average must be computed from **decided matches alone, before any averaged
match exists**, or an averaged score feeds the next average and the whole ladder
drifts. :class:`PointsLedger` holds the two halves apart so the dashboard can say
which part of a total is real. When the setting is off, an unscored match leaves
the numerator and the divisor alike untouched — it is not a zero, it simply is not
there. :meth:`~matchcentre.model.Match.points_for` returns ``None`` for both
cases; this module is the only place that knows which of the two applies.

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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .competition import Competition, Player, round_sort_key
from .format import MatchFormat
from .model import NO_OPPONENT, Match, MatchStatus, Outcome
from .scoring import LADDER_KEYS
from .slugs import unslug

__all__ = ["PRECISION", "match_sort_key", "chronological", "rounds_present",
           "round_display", "round_stored", "with_byes", "PointsLedger",
           "points_ledger", "round_points", "head_to_head", "h2h_key", "ladder",
           "ladder_history", "ladder_complete", "team_stats", "player_stats",
           "player_names", "is_fill_in", "PointsBreakdown", "SideRecord",
           "SlotRecord", "DisciplineRecord", "PlayerSlotRecord", "Streak",
           "LadderEntry", "RoundPoints", "RatingPoint", "PairRecord",
           "Contribution", "HeadToHead", "TeamStats", "PlayerStats"]

# Points are carried at full precision but summed from one-decimal values, so bare
# float arithmetic leaves 1e-16 crumbs that would churn `git diff` on season.json.
# Rounding at the output boundary keeps the file stable without throwing away
# meaningful precision (averages need ~6 places, display 1).
PRECISION = 6


def match_sort_key(match: Match) -> tuple[tuple[int, int, str], str, str, str]:
    """Chronological order, total and reproducible.

    ``round_sort_key`` comes from :mod:`~matchcentre.competition` so that "1, 2,
    10, F" sorts that way rather than as strings ("1, 10, 2") or as integers
    (which raises on ``F``). The trailing slugs make the key total, so two cards
    on the same date never swap places between runs.
    """
    return (round_sort_key(match.round), match.date, match.home, match.away)


def chronological(matches: Iterable[Match]) -> list[Match]:
    return sorted(matches, key=match_sort_key)


def rounds_present(matches: Iterable[Match]) -> list[str]:
    """Round labels that have at least one match, in the order a season runs."""
    return sorted({m.round for m in matches}, key=round_sort_key)


def round_display(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def round_stored(value: float | None) -> float | None:
    return None if value is None else round(value, PRECISION)


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
    games_won: int
    team_games_won: int
    share_pct: float


@dataclass(frozen=True, slots=True)
class HeadToHead:
    """One pairing's record. ``low`` and ``high`` are the two slugs in the key.

    The key is ``"slug-a|slug-b"`` sorted lexically, so ``low`` is the first of
    the pair alphabetically and ``high`` the second. The names are the
    predecessor's, kept because they are the published JSON schema; only what
    orders them changed, from draw number to slug.
    """

    played: int = 0
    wins_low: int = 0
    wins_high: int = 0
    draws: int = 0
    games_low: int = 0
    games_high: int = 0


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
    counted: int
    home: SideRecord
    away: SideRecord
    form: list[str]
    streak: Streak
    by_slot: dict[str, SlotRecord]
    ladder_history: list[LadderEntry]
    best_round: RoundPoints | None
    worst_round: RoundPoints | None


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
    win_pct: float
    singles: DisciplineRecord
    doubles: DisciplineRecord
    streak: Streak
    longest_win_streak: int
    partners: list[PairRecord]
    opponents: list[PairRecord]
    contribution: Contribution
    fill_in_appearances: int
    by_slot: dict[str, PlayerSlotRecord]


# --- byes ---------------------------------------------------------------


def with_byes(matches: Sequence[Match], competition: Competition) -> list[Match]:
    """The matches, plus one scoreable ``BYE`` per bye the draw actually states.

    A bye has no card, and everything in this module counts a team's matches by
    :meth:`~matchcentre.model.Match.involves`, so a round that merely omitted the
    team would leave its ladder row a whole match short — which reads as a missing
    scorecard, not as a scheduled rest.

    Rule F1 lives here as a refusal to guess: this function adds a bye **only**
    where :attr:`~matchcentre.competition.Round.bye` already names one, and a
    ``Round`` only ever carries one when the team list was declared by a human or
    came from a declared draw. Nothing in this module compares a round's cards
    against the team list and concludes that whoever is missing must be resting.
    That inference is what paid a bye, in the predecessor, to teams that had
    played and won, off the back of one scorecard that failed to parse.

    Only for rounds that have been played. A bye is a scheduled rest, but it is
    not *worth* anything until the round happens; crediting a future one has a
    team scoring points for weeks that have not been played.

    Idempotent, because :func:`ladder_history` slices its output and feeds the
    pieces back to :func:`ladder`.
    """
    played = set(rounds_present(matches))
    already = {(m.round, m.home) for m in matches if m.status is MatchStatus.BYE}
    extra: list[Match] = []
    for number in competition.round_order:
        rnd = competition.rounds[number]
        team = rnd.bye
        if not team or number not in played or (number, team) in already:
            continue
        # A card for the team the draw has resting means the two sources disagree
        # -- a rescheduled fixture, most likely. Trust the card: it is evidence
        # that the match was played, and awarding an average on top of a real
        # result would pay the team twice. validate.py reports the disagreement.
        if any(m.round == number and m.involves(team) for m in matches):
            continue
        extra.append(Match(competition=competition.id, round=number,
                           home=team, away=NO_OPPONENT,
                           fmt=competition.fmt, rules=competition.rules,
                           date=rnd.date, sets=(), status=MatchStatus.BYE))
    return list(matches) + extra


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


def head_to_head(matches: Sequence[Match]) -> dict[str, HeadToHead]:
    """Pairwise records keyed ``"slug-a|slug-b"``, the pair sorted lexically."""
    tally: dict[tuple[str, str], HeadToHead] = {}
    for match in chronological(matches):
        if not match.decided or match.is_bye:
            continue
        low, high = sorted((match.home, match.away))
        current = tally.get((low, high), HeadToHead())
        winner = match.winner
        tally[(low, high)] = HeadToHead(
            played=current.played + 1,
            wins_low=current.wins_low + (1 if winner == match.side_of(low) else 0),
            wins_high=current.wins_high + (1 if winner == match.side_of(high) else 0),
            draws=current.draws + (1 if winner is None else 0),
            games_low=current.games_low + match.games_for(low),
            games_high=current.games_high + match.games_for(high),
        )
    return {f"{low}|{high}": rec for (low, high), rec in sorted(tally.items())}


def _h2h_compare(matches: Sequence[Match], a: str, b: str) -> int:
    """Head-to-head tiebreak: +1 if ``a`` ranks above ``b``, -1 below, 0 level."""
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
        if not match.involves(team):
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
                     f"{', '.join(LADDER_KEYS)}")


def ladder(matches: Sequence[Match], competition: Competition) -> list[str]:
    """Team slugs in ladder order, following ``competition.rules.ladder_by``.

    A team with nothing counted has no average and sorts last however the rest of
    its row reads: it has not played, so it is not on the table so much as under
    it.

    Head-to-head only means anything between the two teams being compared, so the
    ordering is a pairwise comparator rather than a sort key. That makes it
    formally non-transitive in a three-way tie; ``ladder_by`` is required to end
    with ``name``, which keeps the result deterministic even then.
    """
    teams = competition.teams
    matches = with_byes(matches, competition)
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
    matches = with_byes(matches, competition)
    counts = {points_ledger(matches, slug).counted for slug in competition.teams}
    return len(counts) == 1 and counts != {0}


def ladder_history(matches: Sequence[Match],
                   competition: Competition) -> dict[str, list[LadderEntry]]:
    """Position after each round, each round's ladder recomputed from scratch."""
    history: dict[str, list[LadderEntry]] = defaultdict(list)
    matches = with_byes(matches, competition)
    for number in rounds_present(matches):
        limit = round_sort_key(number)
        seen = [m for m in matches if round_sort_key(m.round) <= limit]
        for position, slug in enumerate(ladder(seen, competition), start=1):
            average = points_ledger(seen, slug).average
            history[slug].append(LadderEntry(round=number, position=position,
                                             points_average=round_stored(average)))
    return dict(history)


# --- team statistics ---------------------------------------------------


def team_stats(matches: Sequence[Match],
               competition: Competition) -> dict[str, TeamStats]:
    """Full statistics for every team in the competition, position included."""
    teams = competition.teams
    # Byes materialised once here, so every figure below -- the ladder, the
    # per-round points, the breakdown that says how much of a total was averaged --
    # is computed from the same set of matches.
    ordered = chronological(with_byes(matches, competition))
    positions = {slug: i for i, slug in enumerate(ladder(ordered, competition),
                                                 start=1)}
    history = ladder_history(ordered, competition)

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
            captain=(captain.name if captain
                     else (unslug(team.captain) if team.captain else None)),
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
        )
    return out


def _outcome_letter(outcome: Outcome) -> str:
    return {Outcome.WIN: "W", Outcome.LOSS: "L", Outcome.DRAW: "D"}[outcome]


def _side_record(played: Sequence[Match], team: str, side: str) -> SideRecord:
    subset = [m for m in played if m.side_of(team) == side]
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


# --- player statistics -------------------------------------------------


@dataclass(slots=True)
class _PlayerAccumulator:
    """Mutable scratch space; only the frozen :class:`PlayerStats` escapes."""

    sets_played: int = 0
    sets_won: int = 0
    sets_lost: int = 0
    games_won: int = 0
    games_lost: int = 0
    games_won_for_team: int = 0
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
    """
    ordered = chronological(matches)
    index = competition.player_index()
    teams = competition.teams
    team_games = {slug: _tally(ordered, slug).games_won for slug in teams}
    names = player_names(ordered, competition)
    slot_keys = [slot.key for slot in competition.fmt.slots]

    acc: dict[str, _PlayerAccumulator] = defaultdict(_PlayerAccumulator)
    for slug in index:
        acc[slug]  # noqa: B018 - materialise the zero rows

    for match in ordered:
        key = (match.round, match.home, match.away)
        rosters = {"home": match.home_roster, "away": match.away_roster}
        sides_team = {"home": match.home, "away": match.away}
        # Ratings come off the roster block, not the set rows: the card prints a
        # rating for every registered player, so a squad member who was named but
        # did not get on court still has one. Harvesting these inside the set loop
        # instead would leave an unused player showing no rating at all.
        for slug, rating in match.ratings.items():
            acc[slug].ratings[match.round] = (rating.singles, rating.doubles)
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
                    registered = index.get(slug)
                    if is_fill_in(slug, registered, rosters[side],
                                  sides_team[side]):
                        entry.fill_ins.add(key)
                    elif registered is not None:
                        entry.games_won_for_team += mine
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
        available = (sum(1 for m in ordered if m.involves(team_slug))
                     if team_slug else len(entry.match_ids))
        rating_history = [
            RatingPoint(round=number, singles=values[0], doubles=values[1])
            for number, values in sorted(entry.ratings.items(),
                                         key=lambda kv: round_sort_key(kv[0]))
        ]
        latest = _latest_rating(rating_history)
        decided_sets = entry.sets_won + entry.sets_lost
        team_total = team_games.get(team_slug, 0) if team_slug else 0
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
            win_pct=(round_display(100.0 * entry.sets_won / decided_sets)
                     if decided_sets else 0.0),
            singles=_discipline(entry.singles),
            doubles=_discipline(entry.doubles),
            streak=_streak(entry.results),
            longest_win_streak=_longest_run(entry.results, "W"),
            partners=_pair_records(entry.partners, names),
            opponents=_pair_records(entry.opponents, names),
            contribution=Contribution(
                games_won=entry.games_won_for_team,
                team_games_won=team_total,
                share_pct=(round_display(100.0 * entry.games_won_for_team
                                         / team_total) if team_total else 0.0),
            ),
            fill_in_appearances=len(entry.fill_ins),
            by_slot={
                key: PlayerSlotRecord(played=len(entry.slots.get(key, [])),
                                      won=entry.slots.get(key, []).count("W"))
                for key in slot_keys
            },
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
    2. the slug, un-slugged and title-cased.

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
                # A fill-in from outside the competition has no registered
                # spelling here, so the slug is all there is: un-slug it rather
                # than showing dashes in the UI, and let F7 mark it as derived.
                names.setdefault(slug, unslug(slug))
    return dict(sorted(names.items()))
