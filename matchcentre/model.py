"""One match, and the arithmetic that turns it into points.

Two design choices here are worth stating up front because everything downstream
depends on them.

**Teams are slugs.** ``match.home`` is ``"rockets"``, not ``3``. The predecessor
keyed teams on their draw number, which appears on no scorecard and therefore had
to be invented — assigned alphabetically over whatever teams happened to have been
discovered. One misprinted banner then created a phantom team and renumbered
everyone below it, while every internal check still passed because the codes were
still ``1..n``. A slug is a pure function of the printed name, so it cannot shift.

**Each match carries its own format and ruleset.** They are not module constants.
The format is read off the card (how many set rows, which are doubles); the
ruleset is the club's choice. A match cannot answer "who won" or "how many points"
without both, so it holds both rather than letting callers supply mismatched ones.

Nothing in this module reads a file or knows what a PDF is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .format import MatchFormat, Slot, completed, set_winner
from .scoring import Ruleset

__all__ = ["NO_OPPONENT", "SetStatus", "MatchStatus", "Outcome", "Rating",
           "SetResult", "Match", "SIDES", "other_side", "forfeit_set_winner",
           "forfeit_match_winner", "stopped_short"]

# The opponent of a team with a bye. Deliberately unsluggable — `slugs.slugify`
# can never produce a string containing '*' — so it cannot collide with a real
# team however a club names its sides.
NO_OPPONENT = "*bye*"

SIDES = ("home", "away")


def other_side(side: str) -> str:
    if side == "home":
        return "away"
    if side == "away":
        return "home"
    raise ValueError(f"not a side: {side!r}")


def forfeit_set_winner(*, home_forfeit: bool, away_forfeit: bool,
                       home_games: int, away_games: int) -> str | None:
    """Who a forfeited rubber goes to, from the two things the row prints.

    The ticked box first, and whatever the games say. A rubber one side did not
    turn up for is 0-0 on the card — there were no games to print — so a winner
    taken from the games alone credits nobody, which loses the side that turned up
    a set it won, and then puts the set rows in contradiction with the card's own
    printed TOTAL row. That is the same rule :attr:`Match.winner` has always
    applied to a whole-match forfeit, where the ticked box decides however the
    games read, and for the same reason.

    The games are the fallback and not the answer. A row whose ``Forfeit`` boxes
    were ticked on both sides names nobody, and the printed scoreline is then the
    only evidence of a result left on the row — which on a card filled in as
    played is exactly where the answer is. A row with neither box ticked is not
    forfeited at all: the reader marks a row ``FORFEIT`` only when it reads a tick.

    Both ticks are passed rather than a side, so that turning two boxes into a
    forfeiter happens here and nowhere else. The model, the validator reading a
    card straight off the page, the reader's own cross-check of the TOTAL row, and
    the writer that prints the TOTAL row every fixture in the test suite is checked
    against all ask this function, and would otherwise each hold an opinion about a
    row that ticks both.
    """
    if home_forfeit != away_forfeit:
        return "away" if home_forfeit else "home"
    if home_games == away_games:
        return None
    return "home" if home_games > away_games else "away"


def forfeit_match_winner(forfeited_by: str | None) -> str | None:
    """Who wins a whole-match forfeit: the side the Match Forfeited box does not name.

    ``None`` when the box names no one side, left unticked or ticked for both. The
    games are not asked, whatever they say: a card nobody turned up for is 0-0 on
    every row, and the ticked box is the result. The model, the validator holding a
    forfeit's verb to the box, and the reader's cross-checks of the verb and the
    Match Winner tick all ask this function, so none of them keeps its own copy of
    who a forfeit went to.
    """
    return other_side(forfeited_by) if forfeited_by in SIDES else None


class SetStatus(Enum):
    """Why a set's score reads the way it does. Straight off the card's boxes."""

    PLAYED = "played"
    FORFEIT = "forfeit"
    RETIRED = "retired"
    INCOMPLETE = "incomplete"


def stopped_short(status: SetStatus) -> bool:
    """Whether a set's box says it stopped before it finished: retired or incomplete.

    Such a set is nobody's, however its printed games stand, since a lead is not a
    win. :attr:`SetResult.completed` asks this, and so do validate's check of the
    row straight off the page, the reader's bound on the sets each side can have
    won and the TOTAL row the card writer prints. The reader's bound credited the
    leader, and a card whose verb and tick gave the match on the finished sets was
    two F6 errors (4.217).
    """
    return status in (SetStatus.RETIRED, SetStatus.INCOMPLETE)


class MatchStatus(Enum):
    """The state of a whole match.

    ``UNPLAYED`` is a card with no usable scores on it; ``CANCELLED`` is a match
    called off (washout, closed courts); ``BYE`` is a team with no opponent. All
    three are unscored, and what happens to them is the ruleset's decision, not
    this module's.
    """

    PLAYED = "played"
    FORFEIT = "forfeit"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"
    UNPLAYED = "unplayed"
    BYE = "bye"


class Outcome(Enum):
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


@dataclass(frozen=True, slots=True)
class Rating:
    """A player's singles and doubles rating as printed on one card."""

    singles: float | None = None
    doubles: float | None = None


@dataclass(frozen=True, slots=True)
class SetResult:
    """One set: who played it, the games, and whether it finished.

    ``home_players``/``away_players`` are slugs, already alias-resolved. A
    doubles slot normally has two per side and a singles slot one, but the card
    is trusted over the format here: a doubles row with one name is a real thing
    that happens when a card is filled in badly, and it is reported rather than
    rejected.
    """

    slot: Slot
    home_games: int
    away_games: int
    home_players: tuple[str, ...] = ()
    away_players: tuple[str, ...] = ()
    status: SetStatus = SetStatus.PLAYED
    rule: object = None          # SetRule; set by the parser from the format
    # Which side's `Forfeit` box this row ticks, kept as the two booleans the card
    # prints rather than as one forfeiting side: a row that ticks both is a real
    # card, and reducing it here would throw away the only evidence that it did.
    home_forfeit: bool = False
    away_forfeit: bool = False

    @property
    def completed(self) -> bool:
        """Whether the score is a finished set under this set's own rule.

        A forfeited or retired set is *not* complete on the score even when the
        printed games look like a win, which is why status is checked first.
        """
        if stopped_short(self.status):
            return False
        if self.status is SetStatus.FORFEIT:
            return True
        if self.rule is None:
            raise ValueError(f"set {self.slot.key} has no set rule attached")
        return completed(self.home_games, self.away_games, self.rule)

    @property
    def unfinished_unmarked(self) -> bool:
        """Whether the score stops short of a finished set with no box saying why.

        The one set a wrong ``[rules] sets`` can explain: a set played to 4 and read
        as one to 6 looks exactly like this, where a set ticked as retired or
        incomplete says for itself why it stopped. validate warns about each one
        and the review list counts them, so the rule is here, once.
        """
        return self.status is SetStatus.PLAYED and not self.completed

    @property
    def winner(self) -> str | None:
        """``"home"``, ``"away"``, or ``None`` when level or unfinished.

        A forfeited set goes to whoever did not forfeit, which the card records by
        ticking the forfeiter's box beside the row. :func:`forfeit_set_winner` is
        what reads it.
        """
        if self.status is SetStatus.FORFEIT:
            return forfeit_set_winner(home_forfeit=self.home_forfeit,
                                      away_forfeit=self.away_forfeit,
                                      home_games=self.home_games,
                                      away_games=self.away_games)
        if not self.completed:
            return None
        return set_winner(self.home_games, self.away_games, self.rule)

    @property
    def margin(self) -> int:
        return abs(self.home_games - self.away_games)

    def games_for(self, side: str) -> int:
        return self.home_games if side == "home" else self.away_games

    def players_for(self, side: str) -> tuple[str, ...]:
        return self.home_players if side == "home" else self.away_players


@dataclass(frozen=True, slots=True)
class Match:
    """One scorecard, or one materialised bye.

    ``fmt`` is the format this particular card describes; ``rules`` is the
    competition's scoring scheme. Both are required because none of the derived
    properties mean anything without them.
    """

    competition: str
    round: str
    home: str
    away: str
    fmt: MatchFormat
    rules: Ruleset
    date: str = ""
    court: int | None = None
    match_id: str = ""
    sets: tuple[SetResult, ...] = ()
    status: MatchStatus = MatchStatus.PLAYED
    forfeited_by: str | None = None
    disputed: bool = False
    home_roster: tuple[str, ...] = ()
    away_roster: tuple[str, ...] = ()
    ratings: dict[str, Rating] = field(default_factory=dict)
    source: str = ""
    warnings: tuple[str, ...] = ()

    # --- sides ----------------------------------------------------------

    def involves(self, team: str) -> bool:
        return team in (self.home, self.away)

    def side_of(self, team: str) -> str | None:
        if team == self.home:
            return "home"
        if team == self.away:
            return "away"
        return None

    def opponent_of(self, team: str) -> str | None:
        side = self.side_of(team)
        if side is None:
            return None
        return self.away if side == "home" else self.home

    @property
    def is_bye(self) -> bool:
        return self.status is MatchStatus.BYE or self.away == NO_OPPONENT

    @property
    def counts_as_played(self) -> bool:
        """Whether this match happened, and so has numbers worth counting.

        Weaker than :attr:`decided` and deliberately so: a card whose last rubber
        was never finished was still played, and its games belong in the ladder's
        games column even though the match itself decides nothing.

        A cancelled or unplayed match is the other way round. It contributes
        nothing anywhere — not a game, not a set, not an appearance — *including*
        when part of its card was filled in before play stopped. That happens: a
        washed-out night can leave two and a half rubbers on the card, and
        counting them would credit a team both the games it won before the rain and
        whatever the ruleset pays for a round nobody played. It also decides which
        way the association's sheet is reproduced, since a round marked as
        unplayed there adds nothing to a team's games, points or matches played.
        """
        return self.status not in (MatchStatus.BYE, MatchStatus.CANCELLED,
                                   MatchStatus.UNPLAYED)

    # --- tallies --------------------------------------------------------

    @property
    def home_games(self) -> int:
        return sum(s.home_games for s in self.sets)

    @property
    def away_games(self) -> int:
        return sum(s.away_games for s in self.sets)

    @property
    def home_sets(self) -> int:
        return sum(1 for s in self.sets if s.winner == "home")

    @property
    def away_sets(self) -> int:
        return sum(1 for s in self.sets if s.winner == "away")

    @property
    def completed_sets(self) -> int:
        return sum(1 for s in self.sets if s.completed)

    def games_for(self, team: str) -> int:
        side = self.side_of(team)
        if side is None:
            return 0
        return self.home_games if side == "home" else self.away_games

    def games_against(self, team: str) -> int:
        side = self.side_of(team)
        if side is None:
            return 0
        return self.away_games if side == "home" else self.home_games

    def sets_for(self, team: str) -> int:
        side = self.side_of(team)
        if side is None:
            return 0
        return self.home_sets if side == "home" else self.away_sets

    def sets_against(self, team: str) -> int:
        side = self.side_of(team)
        if side is None:
            return 0
        return self.away_sets if side == "home" else self.home_sets

    # --- result ---------------------------------------------------------

    @property
    def decided(self) -> bool:
        """Whether this match contributes a result to the ladder.

        A forfeit counts only when the card says *who* forfeited. The
        predecessor scored a match flagged as a forfeit with neither side ticked
        by paying the forfeit-win value to *both* sides; refusing to decide such a
        match, and warning, is the fix.

        Nothing :attr:`counts_as_played` leaves out decides anything, a forfeit
        included: the rule is asked before a forfeit's own branch, so the ticked
        box of a match that does not count is never read as its result.
        """
        if not self.counts_as_played:
            return False
        if self.status is MatchStatus.FORFEIT:
            return self.forfeited_by in SIDES
        return self.completed_sets >= self.fmt.min_sets_for_result

    @property
    def short_of_result(self) -> bool:
        """Whether the match was played and has no result only for want of sets.

        The one kind of undecided match ``min_sets_for_result`` could change. A
        washout, a fixture never played and a bye count as nothing whatever the
        number, and a forfeit is decided by its ticked box, or by nothing when no
        side is ticked: none of them is short of sets.
        """
        return (self.counts_as_played and self.status is not MatchStatus.FORFEIT
                and not self.decided)

    @property
    def winner(self) -> str | None:
        """``"home"``, ``"away"``, or ``None`` for an undecided or drawn match."""
        if not self.decided:
            return None
        if self.status is MatchStatus.FORFEIT:
            return forfeit_match_winner(self.forfeited_by)
        return self.rules.outcome(home_games=self.home_games,
                                  away_games=self.away_games,
                                  home_sets=self.home_sets,
                                  away_sets=self.away_sets)

    @property
    def decided_by(self) -> str | None:
        """Which basis separated the two sides: ``"games"``, ``"sets"``, ``None``.

        ``None`` covers everything that no basis decided: a draw, a match with no
        result, and a forfeit -- where the winner comes from the ticked box rather
        than from anything either side did on court.

        This exists so the dashboard can say why a level scoreline has a winner
        beside it without assuming which basis comes first. It is not a second
        opinion about the result: :attr:`winner` is computed from the same call.
        """
        if not self.decided or self.status is MatchStatus.FORFEIT:
            return None
        return self.rules.deciding_basis(home_games=self.home_games,
                                         away_games=self.away_games,
                                         home_sets=self.home_sets,
                                         away_sets=self.away_sets)

    @property
    def tied_on_games(self) -> bool:
        """Whether a match that was played finished level on games.

        The predicate is ``counts_as_played`` and not ``decided``: a night that ran
        to the end with one rubber unfinished was still level or not level, and
        withholding that would lose a real fact about a real match. What it keeps
        out is the empty card. A match nobody played is level at nothing, and 0-0
        satisfies any bare comparison of the two totals -- which is how a washout
        came to be described on the dashboard as a contest decided on sets.

        A night of rubbers one side never came for is played, and counts, and is
        still level at nothing: not a game was played on it. So the games have to
        be more than none, or a walkover is "level on games, decided on sets".
        """
        return (self.counts_as_played and self.home_games > 0
                and self.home_games == self.away_games)

    @property
    def is_draw(self) -> bool:
        return self.decided and self.winner is None

    @property
    def is_unscoreable_draw(self) -> bool:
        """A level match in a competition whose scheme has no value for a draw.

        Not a draw and not a result: under ``[rules] draws = false`` the association
        decides every match, so a level card means a tiebreak nobody wrote down or a
        row read wrong. Both halves matter -- a card with a winner is fine however
        strict the grade is, and a level card is an ordinary draw wherever the grade
        allows one.

        Defined here, once, so that the report that names the card, the reason the
        page prints beside it and the ladder that leaves it out all ask the same
        question. It is deliberately not ``not self.decided``: the card was played
        to the end and its games are real, which is why they stay in the player
        figures and only the ladder declines it.
        """
        return self.is_draw and not self.rules.allows_draw

    def outcome_for(self, team: str) -> Outcome | None:
        side = self.side_of(team)
        if side is None or not self.decided:
            return None
        winner = self.winner
        if winner is None:
            return Outcome.DRAW
        return Outcome.WIN if winner == side else Outcome.LOSS

    def points_for(self, team: str) -> float | None:
        """Points this team earned, or ``None`` when the match earned none.

        ``None`` does not mean zero. It means *this match produced no points of
        its own*, and what happens next is the ruleset's decision:
        ``average_unplayed`` credits the team its own average over the matches it
        did play, and otherwise the match is left out of the ladder entirely.
        Keeping that decision in one place (``stats.PointsLedger``) is what stops
        an averaged score feeding the next average.
        """
        side = self.side_of(team)
        if side is None or not self.decided:
            return None
        if self.is_unscoreable_draw:
            # Fail closed (F5) rather than let `Ruleset.base_points` raise: one card
            # a grade's rules cannot price is a card to report, not a reason for the
            # whole dashboard to fail to build. `stats.ladder_matches` drops the
            # match outright, which is what stops this `None` being read as a week
            # off and paid the team's own average.
            return None
        outcome = self.outcome_for(team)
        # Under as-played the box decides the outcome and the outcome is paid, but
        # a scheme whose points are the sets won pays nothing for an outcome, and
        # nobody wins a set on a forfeit's empty rows. So such a scheme prices
        # every whole-match forfeit here, whatever `forfeit_award` says.
        if (self.status is MatchStatus.FORFEIT
                and (self.rules.forfeit_award != "as-played"
                     or self.rules.points_are_sets_won)):
            if outcome is Outcome.WIN:
                return self.rules.forfeit_win_points(self.fmt)
            return self.rules.forfeit_loss_points(self.fmt)
        return self.rules.points(
            outcome=outcome.value,
            games_won=self.games_for(team),
            sets_won=self.sets_for(team),
        )

    # --- rosters --------------------------------------------------------

    def roster_for(self, side: str) -> tuple[str, ...]:
        return self.home_roster if side == "home" else self.away_roster

    def team_for(self, side: str) -> str:
        return self.home if side == "home" else self.away

    def players_on(self, side: str) -> tuple[str, ...]:
        """Every slug that took a court for this side, in slot order."""
        seen: list[str] = []
        for result in self.sets:
            for slug in result.players_for(side):
                if slug not in seen:
                    seen.append(slug)
        return tuple(seen)

    @property
    def key(self) -> tuple[str, str, str]:
        """The identity a fixture has: one match per round per pairing."""
        return (self.round, self.home, self.away)
