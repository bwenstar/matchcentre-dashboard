"""How sets become ladder points.

Every association scores differently, and the differences are not cosmetic: they
change who is top of the table. So the scheme is a named preset the club chooses,
it is printed on every run, and it is shown on the dashboard. A club running the
wrong preset should be able to see that without reading any code.

Two things here were constants in the predecessor and are computed now, because
as constants they were quietly wrong outside one club:

*Forfeit value.* ``FORFEIT_WIN_POINTS = 6.4`` is exactly
``4 + 0.1 * (4 sets x 6 games)`` — right for that format, wrong for every other.
It is derived from the match format.

*Result basis.* That club decides a match on total games with sets as the
tiebreak. Most pennant competitions decide on sets (rubbers) with games as the
tiebreak. Getting this backwards silently reorders the ladder, so it is explicit
in :attr:`Ruleset.decide_by` and there is no default that suits everyone — only a
default that is *stated*.
"""

from __future__ import annotations

from dataclasses import dataclass

from .format import MatchFormat

__all__ = ["Ruleset", "RULESETS", "ruleset", "ScoringError",
           "DEFAULT_RULESET", "LADDER_KEYS"]


class ScoringError(ValueError):
    """An unknown or internally inconsistent scoring scheme."""


# Ladder ordering vocabulary. `head_to_head` is a pairwise comparison rather than
# a sort key, so stats.py handles it specially; the rest are plain numbers, all
# ordered highest-first.
LADDER_KEYS = ("points_average", "points", "wins", "games_diff", "games_won",
               "sets_diff", "sets_won", "head_to_head", "name")

# What a forfeit is worth.
#   as-played   the games printed on the card stand (the usual Match Centre case,
#               where the non-forfeiting side's scoreline is filled in)
#   full-games  the winner is credited a clean sweep, the forfeiter nothing
#   fixed       a flat number of points, ignoring games
FORFEIT_AWARDS = ("as-played", "full-games", "fixed")


@dataclass(frozen=True, slots=True)
class Ruleset:
    """One competition's points scheme.

    ``draw = None`` means the competition cannot draw. That is not the same as
    ``draw = 0``: with ``None``, a level match is reported as a data problem
    (something was misread, or a tiebreak result was not recorded) instead of
    being silently scored.
    """

    name: str
    win: float
    draw: float | None
    loss: float
    game_bonus: float = 0.0
    set_bonus: float = 0.0
    points_are_sets_won: bool = False
    decide_by: tuple[str, ...] = ("games", "sets")
    forfeit_award: str = "as-played"
    forfeit_points: float | None = None
    ladder_by: tuple[str, ...] = ("points_average", "points", "games_diff",
                                  "games_won", "head_to_head", "name")
    average_unplayed: bool = True

    def __post_init__(self) -> None:
        for basis in self.decide_by:
            if basis not in ("games", "sets"):
                raise ScoringError(
                    f"decide_by may only contain 'games' and 'sets', got {basis!r}")
        if not self.decide_by:
            raise ScoringError("decide_by cannot be empty: nothing would decide a match")
        if len(set(self.decide_by)) != len(self.decide_by):
            raise ScoringError(f"decide_by repeats itself: {self.decide_by}")
        if self.forfeit_award not in FORFEIT_AWARDS:
            raise ScoringError(
                f"forfeit_award must be one of {FORFEIT_AWARDS}, "
                f"got {self.forfeit_award!r}")
        if self.forfeit_award == "fixed" and self.forfeit_points is None:
            raise ScoringError(
                "forfeit_award='fixed' needs forfeit_points")
        for key in self.ladder_by:
            if key not in LADDER_KEYS:
                raise ScoringError(
                    f"unknown ladder key {key!r}; known: {', '.join(LADDER_KEYS)}")
        if not self.ladder_by:
            raise ScoringError("ladder_by cannot be empty: the ladder needs an order")
        if "name" != self.ladder_by[-1]:
            # Without a total order the ladder is not reproducible, and an
            # unstable ladder makes every regenerated season.json churn.
            raise ScoringError(
                "ladder_by must end with 'name' so ties resolve deterministically")

    # --- points ---------------------------------------------------------

    def base_points(self, outcome: str) -> float:
        """Points for the result alone, before any bonus."""
        if outcome == "win":
            return self.win
        if outcome == "loss":
            return self.loss
        if outcome == "draw":
            if self.draw is None:
                raise ScoringError(
                    f"{self.name} does not allow draws, but a drawn match was scored")
            return self.draw
        raise ScoringError(f"unknown outcome {outcome!r}")

    def points(self, *, outcome: str, games_won: int, sets_won: int) -> float:
        """A side's points for one completed match."""
        if self.points_are_sets_won:
            return float(sets_won)
        return (self.base_points(outcome)
                + self.game_bonus * games_won
                + self.set_bonus * sets_won)

    def forfeit_win_points(self, fmt: MatchFormat) -> float:
        """What a side gets for a win by forfeit.

        Only used when ``forfeit_award`` is not ``as-played``; otherwise the card's
        own printed games flow through the normal path.
        """
        if self.forfeit_award == "fixed":
            return float(self.forfeit_points)
        games = fmt.max_games_per_match
        sets = len(fmt.slots)
        if self.points_are_sets_won:
            return float(sets)
        return self.win + self.game_bonus * games + self.set_bonus * sets

    def forfeit_loss_points(self, fmt: MatchFormat) -> float:
        """What a side gets for forfeiting: the loss value, no bonus earned."""
        if self.points_are_sets_won:
            return 0.0
        return self.loss

    # --- results --------------------------------------------------------

    def outcome(self, *, home_games: int, away_games: int,
                home_sets: int, away_sets: int) -> str | None:
        """``"home"``, ``"away"``, ``None`` for a draw.

        Applies ``decide_by`` in order. Returning ``None`` is a genuine draw only
        when every basis is level; callers must check :attr:`allows_draw` before
        treating it as one.
        """
        pairs = {"games": (home_games, away_games), "sets": (home_sets, away_sets)}
        for basis in self.decide_by:
            mine, theirs = pairs[basis]
            if mine != theirs:
                return "home" if mine > theirs else "away"
        return None

    @property
    def allows_draw(self) -> bool:
        return self.draw is not None

    def describe(self) -> str:
        parts = [f"win {_num(self.win)}"]
        if self.draw is not None:
            parts.append(f"draw {_num(self.draw)}")
        parts.append(f"loss {_num(self.loss)}")
        if self.points_are_sets_won:
            parts = ["points = sets won"]
        if self.game_bonus:
            parts.append(f"{_num(self.game_bonus)} per game")
        if self.set_bonus:
            parts.append(f"{_num(self.set_bonus)} per set")
        decided = " then ".join(self.decide_by)
        return f"{', '.join(parts)}; decided on {decided}"


def _num(value: float) -> str:
    return f"{value:g}"


RULESETS: dict[str, Ruleset] = {
    # A win is worth four, and every game won adds a tenth, so a narrow loss
    # outscores a heavy one and a dead rubber still matters. Common in club
    # competitions that want every game played to count for something.
    "games-bonus": Ruleset(
        name="games-bonus", win=4.0, draw=3.0, loss=2.0, game_bonus=0.1,
        decide_by=("games", "sets"),
        ladder_by=("points_average", "points", "games_diff", "games_won",
                   "head_to_head", "name"),
    ),
    # The common pennant scheme: the match goes to whoever won more rubbers.
    "sets-first": Ruleset(
        name="sets-first", win=2.0, draw=1.0, loss=0.0,
        decide_by=("sets", "games"),
        ladder_by=("points", "wins", "sets_diff", "games_diff", "head_to_head",
                   "name"),
        average_unplayed=False,
    ),
    # Every rubber is a point in its own right; the ladder is a rubber count.
    "rubbers": Ruleset(
        name="rubbers", win=1.0, draw=0.0, loss=0.0, points_are_sets_won=True,
        decide_by=("sets", "games"),
        ladder_by=("points", "wins", "games_diff", "head_to_head", "name"),
        average_unplayed=False,
    ),
    # Three points for a win, one for a draw, nothing for a loss.
    "simple": Ruleset(
        name="simple", win=3.0, draw=1.0, loss=0.0,
        decide_by=("sets", "games"),
        ladder_by=("points", "wins", "games_diff", "games_won", "head_to_head",
                   "name"),
        average_unplayed=False,
    ),
}

DEFAULT_RULESET = "games-bonus"


def ruleset(name: str) -> Ruleset:
    try:
        return RULESETS[name]
    except KeyError:
        raise ScoringError(
            f"unknown scoring scheme {name!r}; known: {', '.join(sorted(RULESETS))}"
        ) from None
