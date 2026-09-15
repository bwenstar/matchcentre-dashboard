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
           "SetResult", "Match", "SIDES", "other_side"]

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


class SetStatus(Enum):
    """Why a set's score reads the way it does. Straight off the card's boxes."""

    PLAYED = "played"
    FORFEIT = "forfeit"
    RETIRED = "retired"
    INCOMPLETE = "incomplete"


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

    @property
    def completed(self) -> bool:
        """Whether the score is a finished set under this set's own rule.

        A forfeited or retired set is *not* complete on the score even when the
        printed games look like a win, which is why status is checked first.
        """
        if self.status in (SetStatus.RETIRED, SetStatus.INCOMPLETE):
            return False
        if self.status is SetStatus.FORFEIT:
            return True
        if self.rule is None:
            raise ValueError(f"set {self.slot.key} has no set rule attached")
        return completed(self.home_games, self.away_games, self.rule)

    @property
    def winner(self) -> str | None:
        """``"home"``, ``"away"``, or ``None`` when level or unfinished.

        A forfeited set goes to whoever did not forfeit, which the card records by
        ticking the forfeiter's box; the parser encodes that as the games, so the
        comparison below is still the right one.
        """
        if self.status is SetStatus.FORFEIT:
            if self.home_games == self.away_games:
                return None
            return "home" if self.home_games > self.away_games else "away"
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
        """
        if self.status is MatchStatus.FORFEIT:
            return self.forfeited_by in SIDES
        if self.status in (MatchStatus.BYE, MatchStatus.CANCELLED,
                           MatchStatus.UNPLAYED):
            return False
        return self.completed_sets >= self.fmt.min_sets_for_result

    @property
    def winner(self) -> str | None:
        """``"home"``, ``"away"``, or ``None`` for an undecided or drawn match."""
        if not self.decided:
            return None
        if self.status is MatchStatus.FORFEIT and self.forfeited_by in SIDES:
            return other_side(self.forfeited_by)
        return self.rules.outcome(home_games=self.home_games,
                                  away_games=self.away_games,
                                  home_sets=self.home_sets,
                                  away_sets=self.away_sets)

    @property
    def is_draw(self) -> bool:
        return self.decided and self.winner is None

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
        outcome = self.outcome_for(team)
        if (self.status is MatchStatus.FORFEIT
                and self.rules.forfeit_award != "as-played"):
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
