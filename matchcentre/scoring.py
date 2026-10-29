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
from .scope import choices
from .slugs import did_you_mean, nearest

__all__ = ["Ruleset", "RULESETS", "ruleset", "ScoringError",
           "DEFAULT_RULESET", "LADDER_KEYS", "UNPLAYED_MODES",
           "UNPLAYED_AVERAGE", "UNPLAYED_ZERO", "unplayed_worth",
           "unplayed_detail", "draws_detail"]


class ScoringError(ValueError):
    """An unknown or internally inconsistent scoring scheme."""


# Ladder ordering vocabulary. `head_to_head` is a pairwise comparison rather than
# a sort key, so stats.py handles it specially; the rest are plain numbers, all
# ordered highest-first.
LADDER_KEYS = ("points_average", "points", "wins", "games_diff", "games_won",
               "sets_diff", "sets_won", "head_to_head", "name")

# What a match forfeited whole is worth, set by `[rules] forfeit_award` and
# `forfeit_points` in config.toml (4.420). Never by the card: a forfeit's TOTAL
# row is a cross-check, and a card may leave it out.
#   full-games  the default: the side that turned up is paid a win with every
#               rubber of the format a love set (`forfeit_win_points`, 6.4 under
#               games-bonus over four first-to-6 rubbers), the side that gave
#               the match up nothing
#   fixed       the side that turned up is paid `forfeit_points`, the other
#               nothing, whatever the games
#   as-played   paid as a played match on the games the rows print, the side
#               that gave it up a loss; Match Centre prints a forfeit's love sets
#               in its TOTAL row only, so over rows left 0-0 that is a win and a
#               loss with no games
# A scheme whose points are the sets won pays nothing for the outcome, so under
# as-played and full-games alike its winner is paid every rubber of the format.
# The award says what a rubber not played out is scored, too: under full-games
# and fixed, a love set to the side that came, or a retirement raised to win it,
# a forfeit's rows included; under as-played, the games it prints
# (`model.rubber_award`, 4.421, 4.422).
FORFEIT_AWARDS = ("as-played", "full-games", "fixed")
DEFAULT_FORFEIT_AWARD = "full-games"

# What a round a team did not play is worth: a bye, a washout, a week called off.
# Every preset here ships with `zero`, and that is only a default — two
# associations running the same points scheme differ on this line all the time, and
# getting it wrong changes the points column of every team that has had a week off.
# So it is settable on its own, through `[rules] unplayed` in config.toml, which is
# what `Ruleset.average_unplayed` ends up holding. A club whose sheet pays for a bye
# sets `unplayed = "average"` and keeps its preset.
#
#   average  the team is credited its own average over the matches it did play,
#            and the round counts in that average but not in P, the matches
#            played. Common where a ladder is ordered on average rather than on
#            total points.
#   zero     the round scores nothing and is not counted. The team's average is
#            the same either way; its points total and its count differ, and so,
#            on a ladder ranked on points first, can its place (4.460).
UNPLAYED_AVERAGE = "average"
UNPLAYED_ZERO = "zero"
UNPLAYED_MODES = (UNPLAYED_AVERAGE, UNPLAYED_ZERO)

# What a season's finals are, through `[rules] finals` (4.537). No card says it,
# and no preset carries an opinion on it, so it is the club's to say or nobody's.
#
#   none      a finals round is left out of the ladder and places nobody, which
#             is every season's rule until a club says otherwise.
#   placings  one week after the regular rounds of 1st v 2nd, 3rd v 4th and on
#             down the final ladder. Each winner takes the higher of its two
#             places; a draw, a washout or a match called off leaves it with the
#             team placed higher; in a grade of an odd number of teams the last
#             plays nobody and keeps its place. The first round numbered past
#             the season's length is that week, as a round labelled a final is.
FINALS_NONE = "none"
FINALS_PLACINGS = "placings"
FINALS_FORMATS = (FINALS_NONE, FINALS_PLACINGS)


@dataclass(frozen=True, slots=True)
class Ruleset:
    """One competition's points scheme.

    ``draw = None`` means the competition cannot draw. That is not the same as
    ``draw = 0``: with ``None``, a level match is reported as a data problem
    (something was misread, or a tiebreak result was not recorded) instead of
    being silently scored. No preset sets it -- a club reaches it through
    ``[rules] draws = false``, the way it reaches ``average_unplayed`` through
    ``[rules] unplayed``.
    """

    name: str
    win: float
    draw: float | None
    loss: float
    game_bonus: float = 0.0
    set_bonus: float = 0.0
    points_are_sets_won: bool = False
    decide_by: tuple[str, ...] = ("games", "sets")
    forfeit_award: str = DEFAULT_FORFEIT_AWARD
    forfeit_points: float | None = None
    ladder_by: tuple[str, ...] = ("points_average", "points", "games_diff",
                                  "games_won", "head_to_head", "name")
    # False by default because it is the convention that needs no explaining: a
    # round nobody played adds nothing. Averaging is the more surprising of the
    # two — it puts points on the board for a night off — so a competition that
    # wants it says so, in its preset or in `[rules] unplayed`.
    average_unplayed: bool = False

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
                f"forfeit_award = \"{self.forfeit_award}\" is not "
                f"{choices(FORFEIT_AWARDS)}."
                f"{did_you_mean(nearest(str(self.forfeit_award), FORFEIT_AWARDS))}")
        if self.forfeit_award == "fixed" and self.forfeit_points is None:
            raise ScoringError(
                "forfeit_award='fixed' needs forfeit_points")
        for key in self.ladder_by:
            if key not in LADDER_KEYS:
                raise ScoringError(
                    f"ladder key \"{key}\" is not {choices(LADDER_KEYS)}."
                    f"{did_you_mean(nearest(str(key), LADDER_KEYS))}")
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

        Only used when ``forfeit_award`` is not ``as-played``, or when the points
        are the sets won; otherwise the card's own printed games flow through the
        normal path. Where the points are the sets won it is every rubber of the
        format, unless ``forfeit_award`` is ``fixed``, whose ``forfeit_points`` is
        paid whatever the scheme.
        """
        if self.forfeit_award == "fixed":
            return float(self.forfeit_points)
        sets, games = fmt.forfeit_worth
        if self.points_are_sets_won:
            return float(sets)
        return self.win + self.game_bonus * games + self.set_bonus * sets

    def forfeit_loss_points(self, fmt: MatchFormat) -> float:
        """What a side gets for giving a match up whole: nothing, but as played.

        Under ``full-games`` and ``fixed`` the side that gave the match up is paid
        0, as the FORFEIT_AWARDS comment says and a club's rules pay it, where
        this paid the scheme's loss value whatever the award (2.0 under
        games-bonus, 4.420). Under ``as-played`` the loss value, no bonus earned,
        which a scheme paying the sets won does not pay either.
        """
        if self.forfeit_award != "as-played" or self.points_are_sets_won:
            return 0.0
        return self.loss

    # --- results --------------------------------------------------------

    def deciding_basis(self, *, home_games: int, away_games: int,
                       home_sets: int, away_sets: int) -> str | None:
        """Which entry in ``decide_by`` separated the two sides, or ``None``.

        The dashboard needs this to explain a level scoreline: a match tied on
        games has a winner beside it only because something else broke the tie,
        and *which* something is the ruleset's decision. Under the default order it
        is sets; under ``sets-first`` a card level on games was never going to be
        decided on games at all, so a page that hardcoded "decided on sets" would
        be describing rules nobody played under.
        """
        pairs = {"games": (home_games, away_games), "sets": (home_sets, away_sets)}
        for basis in self.decide_by:
            mine, theirs = pairs[basis]
            if mine != theirs:
                return basis
        return None

    def outcome(self, *, home_games: int, away_games: int,
                home_sets: int, away_sets: int) -> str | None:
        """``"home"``, ``"away"``, ``None`` for a draw.

        Applies ``decide_by`` in order, through :meth:`deciding_basis` rather than
        beside it, so the winner and the reason given for it cannot disagree.
        Returning ``None`` is a genuine draw only when every basis is level;
        callers must check :attr:`allows_draw` before treating it as one.
        """
        basis = self.deciding_basis(home_games=home_games, away_games=away_games,
                                    home_sets=home_sets, away_sets=away_sets)
        if basis is None:
            return None
        mine, theirs = ({"games": (home_games, away_games),
                         "sets": (home_sets, away_sets)}[basis])
        return "home" if mine > theirs else "away"

    @property
    def allows_draw(self) -> bool:
        return self.draw is not None

    def describe(self) -> str:
        # "no draw" is said rather than left out. A scheme with `draw = None`
        # described itself as "win 4, loss 2" -- the same sentence as a scheme whose
        # draw is worth nothing, which is the opposite arrangement: there a level
        # night is a result, here it is a result nobody recorded. This string is the
        # only place a club that set `draws = false` can see the setting took
        # effect, since it is printed in the run report and in the page's banner.
        parts = [f"win {_num(self.win)}"]
        if self.draw is None:
            parts.append("no draw")
        else:
            parts.append(f"draw {_num(self.draw)}")
        parts.append(f"loss {_num(self.loss)}")
        if self.points_are_sets_won:
            parts = ["points = sets won"]
            if self.draw is None:
                parts.append("no draw")
        if self.game_bonus:
            parts.append(f"{_num(self.game_bonus)} per game")
        if self.set_bonus:
            parts.append(f"{_num(self.set_bonus)} per set")
        clauses = [", ".join(parts)]
        # Nobody wins a set on a forfeit's empty rows, so "points = sets won" alone
        # said the side that turned up was paid nothing. It is paid what
        # forfeit_win_points pays, and this says so from the same field. So does
        # any award but as-played, which a played match's sentence does not
        # describe: games-bonus said "loss 2" of a forfeiter it pays 0 (4.305).
        if self.points_are_sets_won or self.forfeit_award != "as-played":
            clauses.append("a match forfeited whole pays the side that turned up "
                           + self._forfeit_worth())
        clauses.append(f"decided on {' then '.join(self.decide_by)}")
        return "; ".join(clauses)

    def _forfeit_worth(self) -> str:
        """What :meth:`forfeit_win_points` and :meth:`forfeit_loss_points` pay, in
        the words of :meth:`describe`'s forfeit clause."""
        if self.forfeit_award == "fixed":
            points = self.forfeit_points
            paid = f"{_num(points)} point{'' if points == 1 else 's'}"
        elif self.points_are_sets_won:
            return "every rubber"
        else:
            paid = "a win" + (" with every rubber a love set"
                              if self.game_bonus or self.set_bonus else "")
        if self.points_are_sets_won or not self.loss:
            return paid
        return f"{paid}, and the side that gave it up 0"


def _num(value: float) -> str:
    return f"{value:g}"


def unplayed_worth(average_unplayed: bool) -> str:
    """What a round nobody played is worth, as a clause needing a subject.

    The sentence below is the form for a table of rules, where the subject has to
    be named because nothing around it says which rule is being described. This is
    the form for the places that have already named it: a bye's own row on the
    dashboard, and the card of a match that was washed out. Both of those are
    looking at one unplayed round and asking what it earned, and "a bye or a
    washout is credited…" said there tells a reader something they can see.

    So: no subject, no capital, no full stop, and no mention of byes or washouts.
    The callers supply "a bye or a washout is", "bye — " and "The round itself is".

    Split out rather than duplicated because the duplicate existed and had already
    drifted. ``byeRow`` in app.js used to carry its own pair of strings — "scored at
    the team's own average", which never says what the round counts *in*, and "no
    points, by this competition's rules", which never says it is not a match played
    — so the page's two accounts of one rule disagreed with each other and with
    this module, and the one a reader saw depended on which page they were on.
    """
    if average_unplayed:
        return "credited the team's own average, and counts in that average"
    return "scored as nothing, and not counted as a match played"


def unplayed_detail(average_unplayed: bool) -> str:
    """The averaging rule in a sentence, for the run report and the dashboard.

    One function so the terminal and the page say the same thing. It is worth
    saying at all because it changes the points column, and the presets that rank
    on points first — simple, sets-first and rubbers — rank on that column, so it
    can change their order too. Games-bonus ranks on the average, which crediting a
    team its own average cannot move, and points are its next key, so there it
    moves only teams level on average (4.460). A club comparing totals against its
    association's sheet needs to know which convention produced the number it is
    looking at.

    "Counts in that average" rather than "counts as a match played", which is what
    this said and is what associations call it: the ladder's ``P`` column is the
    matches a team's result was decided in, and ``W``, ``L`` and ``D`` add up to it.
    A credited round is in the average's divisor and not in that column, so the
    sentence has to name the thing it is really in — the dashboard prints this line
    a scroll above the column that would otherwise contradict it.

    Composed from :func:`unplayed_worth` rather than written out, so that the two
    forms of one rule cannot say different things.
    """
    return "a bye or a washout is " + unplayed_worth(average_unplayed)


# What each of FORFEIT_AWARDS pays, in the words config.toml's option table uses.
_FORFEIT_DETAIL = {
    "full-games": "the side that turned up is paid a win with every rubber a "
                  "love set, and the side that gave the match up 0",
    "fixed": "the side that turned up is paid forfeit_points, and the side "
             "that gave the match up 0",
    "as-played": "paid as a match played, on the games the rows print",
}


def forfeit_detail(award: str) -> str:
    """What *award* pays for a match forfeited whole, for the file a club edits.

    Beside :func:`unplayed_detail` for the same reason: the option table the
    first-run writer prints is the one place the file says what each award does
    (4.420), so it is said here once, with the comment over FORFEIT_AWARDS.
    """
    return _FORFEIT_DETAIL[award]


# What each of FINALS_FORMATS does, in the words config.toml's option table uses.
_FINALS_DETAIL = {
    "none": "a finals round is left out of the ladder and places nobody",
    "placings": "one week after the regular rounds of 1st v 2nd, 3rd v 4th and "
                "so on down the final ladder: each winner takes the higher "
                "place, and a draw, a washout or a match called off leaves it "
                "with the team placed higher",
}


def finals_detail(mode: str) -> str:
    """What *mode* makes of the finals, for the file a club edits (4.537).

    Beside :func:`forfeit_detail`, and for its reason: the option table is the one
    place the file says what each format does.
    """
    return _FINALS_DETAIL[mode]


def draws_detail(allows_draw: bool) -> str:
    """What becomes of a level card, or ``""`` where a draw is an ordinary result.

    Companion to :func:`unplayed_detail`, and here for the same reason: one sentence,
    beside the rule, printed by both the run report and the dashboard's rules banner
    so the terminal and the page cannot word it differently.

    The empty string is load-bearing rather than tidy. Both callers print the row
    only when there is something in it, so a club whose grade draws like every other
    grade is not shown a line telling them so — ``Ruleset.describe`` one row up
    already says what a draw is worth. Only the club that turned the setting on pays
    for the row, which is the club that has to act on it.

    Keyed on ``allows_draw`` rather than on ``Config.draws``: the fact, not one of
    the routes to it. A competition can override the setting, and the page has no
    config at all.
    """
    if allows_draw:
        return ""
    # Names the setting, because the sentence is otherwise a mystery to whoever
    # inherits the config file — and says the night is *reported*, not dropped,
    # since a figure quietly missing is the thing this project exists to avoid.
    return ("this competition decides every match, so a level card is reported "
            "as a result nobody recorded and left out of the ladder "
            "(draws = false)")


RULESETS: dict[str, Ruleset] = {
    # A win is worth four, and every game won adds a tenth, so a narrow loss
    # outscores a heavy one and a dead rubber still matters. Common in club
    # competitions that want every game played to count for something.
    "games-bonus": Ruleset(
        name="games-bonus", win=4.0, draw=3.0, loss=2.0, game_bonus=0.1,
        decide_by=("games", "sets"),
        ladder_by=("points_average", "points", "games_diff", "games_won",
                   "head_to_head", "name"),
        average_unplayed=False,
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
            f"scoring scheme \"{name}\" is not {choices(sorted(RULESETS))}."
            f"{did_you_mean(nearest(name, RULESETS))}") from None
