"""The association's published sheet, held up against the cards.

Every club does this by eye every week: the ladder the association publishes on
one side, this dashboard on the other, a finger down both columns. A club that types
the sheet's points and matches played into ``[competitions.<id>.expected]`` has it
done on every build instead, and told which team is out, by how much, and what each
round gave that team.

The sheet is an **expectation**, never a result. A result only ever comes from a
card, so nothing here feeds a figure, a position or a row: the comparison is made
after the ladder, from the functions the ladder is made by, and what comes back is
a verdict for :mod:`~matchcentre.validate` to report and for the build to count.
Made once, here, and handed to both, so the sentence in the report and the
findings under it cannot disagree about how many teams were compared.

Nothing is restated. The ladder after a round is :func:`stats.through_round`,
whether a round has been reached is :func:`stats.later_round_with_cards`, what a
round gave a team is :func:`stats.round_points`, and the other ``unplayed``
convention is priced by :func:`stats.points_ledger` over the same matches with the
one rule flipped, so the what-if cannot drift from the ladder it is a what-if of.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from . import stats
from .competition import DRAW_DECLARED, Competition, round_sort_key
from .config import ExpectedLine, ExpectedSheet
from .model import Match
from .scope import in_words
from .scoring import UNPLAYED_AVERAGE, UNPLAYED_ZERO, ScoringError
from .slugs import nearest, slugify

__all__ = ["COMPARED", "AHEAD", "NO_LADDER", "CARD_MISSING", "NEARLY_NAMED",
           "NOT_TYPED", "Run", "TeamCheck", "Comparison", "compare", "places_of",
           "at_places", "agrees", "by_weeks", "rounds_listed"]

# What became of the table as a whole.
COMPARED = "compared"
AHEAD = "ahead"             # the sheet runs to a round no card has reached
NO_LADDER = "no-ladder"     # nothing to compare against: no teams, or no ladder

# Why a team was not compared. Said in the report, so a club is never left to
# think a team it cannot see a finding for was checked and agreed.
CARD_MISSING = "card missing"
NEARLY_NAMED = "nearly named -- see sheet-team below"   # a line is near it
NOT_TYPED = "not typed in"


def places_of(text: str) -> int:
    """The decimal places a typed figure claims: ``"10.90"`` two, ``"19"`` none."""
    return min(stats.PRECISION, max(0, -Decimal(text).as_tuple().exponent))


def at_places(value: float, places: int) -> str:
    """*value* as the ladder's points column would print it at *places*.

    The same two steps that column takes -- the stored figure, then half up on the
    printed decimal -- because the sheet is a spreadsheet and rounds the way it does.
    """
    return f"{stats.round_half_up(stats.round_stored(value), places):.{places}f}"


def agrees(value: float, typed: str) -> bool:
    """Whether *value*, printed to the places *typed* has, is the figure typed.

    Compared as decimals rather than as text, so ``"010.7"`` is 10.7 and agrees.
    """
    return Decimal(at_places(value, places_of(typed))) == Decimal(typed)


@dataclass(frozen=True, slots=True)
class Run:
    """Whole numbers *first* to *last*, three or more, that no card and no draw has.

    Found between the rounds a season knows, and said as one entry rather than
    one for each, so a card labelled round 1000000 costs a line and not a million.
    """

    first: int
    last: int

    def __str__(self) -> str:
        return f"{self.first} to {self.last}"


def by_weeks(rounds, one: str, many: str) -> str:
    """*one* for rounds that are a single week, *many* for more, a run by its weeks.

    The count behind every word the sheet check's advice and the cross-season note
    agree with their rounds in: ``round``, ``that week``, ``those cards``, ``is``.
    Discovery's notes, the config writer's comment and F3 count their lists of
    rounds by it too, where each once had a ``len()`` of its own. None of them is
    given a :class:`Run`, so there it counts what ``len()`` did, and the rule is
    one function whatever a list of rounds holds.
    """
    weeks = sum(r.last - r.first + 1 if isinstance(r, Run) else 1 for r in rounds)
    return one if weeks == 1 else many


def rounds_listed(numbers) -> str:
    """``round 2``, ``rounds 2 and 4``, ``rounds 1, 2 and 4``, ``rounds 3 and 5 to 9``."""
    return f"{by_weeks(numbers, 'round', 'rounds')} {in_words(numbers)}"


@dataclass(frozen=True, slots=True)
class TeamCheck:
    """One team's typed figures against what the cards give it after the round.

    ``counted_now`` is what the team's average divides by, bye included, and
    ``decided`` is the ladder's ``P``. Under ``unplayed = "zero"`` they are one
    number; under ``"average"`` a sheet's played agrees as either.

    ``evidence`` is one entry per round up to the sheet's, or per :class:`Run` of
    numbers nobody has, as the finding prints it; ``nothing_read`` the rounds and
    runs with no card at all for the team; ``weeks_off`` the rounds whose matches
    all earned nothing -- a bye, a washout -- and those with no card that the draw
    rests it in or gives it no fixture in; and ``set_aside`` the rounds whose every
    card for the team was read and not counted.
    """

    slug: str
    key: str
    typed_points: str
    typed_played: int | None
    points_now: float
    counted_now: int
    decided: int
    points_alt: float
    evidence: tuple[str, ...]
    nothing_read: tuple[str | Run, ...]
    weeks_off: tuple[str, ...]
    set_aside: tuple[str, ...] = ()

    def points_differ_now(self) -> bool:
        return bool(self.typed_points) and not agrees(self.points_now,
                                                      self.typed_points)

    def played_differs(self) -> bool:
        """The played typed is neither the divisor nor ``P``.

        Which of the two an averaging association prints has not been measured,
        and requiring one sent a club that set the ``unplayed`` the grade finding
        named round a loop, its played out under the setting that fixed its points.
        """
        return self.typed_played is not None and self.typed_played not in (
            self.counted_now, self.decided)

    def agrees_now(self) -> bool:
        """Every figure typed agrees. A figure left blank is checked by nothing."""
        return not self.points_differ_now() and not self.played_differs()

    def points_agree_alt(self) -> bool:
        """The points are typed, and the other ``unplayed`` would give them."""
        return bool(self.typed_points) and agrees(self.points_alt, self.typed_points)


@dataclass(frozen=True, slots=True)
class Comparison:
    """The whole table against the cards: who was compared, who was not, and why.

    ``held`` is every team with no check, with its reason, in slug order: a team
    a line names is ``CARD_MISSING``, one a line naming nobody is nearest is
    ``NEARLY_NAMED``, and any other ``NOT_TYPED``.
    ``unmatched`` is each key naming no team, with the names of the teams nearest
    it: none, one, or every one tied (:func:`~matchcentre.slugs.nearest`). And
    ``repeated`` is each key naming a team an earlier line already gave figures
    for, with that team's name and the earlier key.

    ``fixed`` and ``broken`` are the teams whose points the other ``unplayed`` would
    put right and would put out. Decided on points alone: the convention is a
    question about what a week off is worth, and what an averaging association
    prints as played has not been measured.
    """

    after_round: str
    status: str
    latest_read: str = "none"
    teams_total: int = 0
    checks: tuple[TeamCheck, ...] = ()
    held: tuple[tuple[str, str], ...] = ()
    unmatched: tuple[tuple[str, tuple[str, ...]], ...] = ()
    repeated: tuple[tuple[str, str, str], ...] = ()
    current_unplayed: str = UNPLAYED_ZERO
    alt_unplayed: str = UNPLAYED_AVERAGE
    fixed: tuple[str, ...] = ()
    broken: tuple[str, ...] = ()

    @property
    def agreeing(self) -> tuple[TeamCheck, ...]:
        return tuple(check for check in self.checks if check.agrees_now())

    @property
    def disagreeing(self) -> tuple[TeamCheck, ...]:
        return tuple(check for check in self.checks if not check.agrees_now())

    @property
    def convention_fits(self) -> bool:
        """The other ``unplayed`` puts at least one team right and none out."""
        return bool(self.fixed) and not self.broken

    @property
    def every_team_out(self) -> bool:
        """Two or more compared, none agreeing, and no setting that explains it."""
        return (len(self.checks) >= 2 and not self.agreeing
                and not self.convention_fits)


def compare(matches: list[Match] | tuple[Match, ...], competition: Competition,
            expected: ExpectedSheet | None) -> Comparison | None:
    """The table against *matches*, the cards read for *competition*.

    ``None`` when there is no table, which is the switch: no table, no sentence,
    no finding. A competition with no teams, or a ladder the scoring cannot price,
    is ``NO_LADDER`` -- both are already reported, and comparing against a ladder
    that is not there would be a second finding about the same thing. No folder
    reaches either, and both are defences. Discovery makes a competition only out
    of the cards filed for it, a ``teams`` list typed empty is filled from those
    cards, and the build passes over a config section nobody has filed a card for;
    but this function and :func:`~matchcentre.validate.validate` are public, and a
    caller can hand either a competition with no teams, which ``no-teams`` reports.
    And a level card under ``draws = false`` is dropped by
    :func:`stats.ladder_matches` before it is priced, :meth:`Match.points_for` fails
    closed on it besides, and a ruleset is checked whole when the config loads, so
    flipping ``average_unplayed`` below cannot make one invalid. The handler is a
    defence for a ledger that gains a second reason to refuse, and the suite
    reaches it by patching. A sheet that runs to a round no card has reached is
    ``AHEAD``, one verdict rather than a disagreement for every team.

    The evidence fills in the rounds a derived draw cannot see, but a typed draw
    says which weeks the season has, up to the last round the club typed fixtures
    for, and no number it passes over is filled in below that.

    A team is held rather than compared only where a card is *known* to be
    missing, which is a fixture of a declared draw with no card. Under a derived
    draw a missing card and a night off look the same, so the team is compared and
    its evidence says which round nothing was read for.

    Whether the sheet is ahead is asked before any team is priced, so a round typed
    as a date is one verdict and costs nothing. So a sheet that is ahead is
    ``AHEAD`` even where the scoring cannot price the ladder: that is the table's
    own mistake, apart from the ladder's.
    """
    if expected is None:
        return None
    number = expected.after_round
    average = competition.rules.average_unplayed
    base = Comparison(after_round=number, status=NO_LADDER,
                      teams_total=len(competition.teams),
                      current_unplayed=UNPLAYED_AVERAGE if average else UNPLAYED_ZERO,
                      alt_unplayed=UNPLAYED_ZERO if average else UNPLAYED_AVERAGE)
    if not competition.teams:
        return base
    targets, unmatched, repeated, nearly = _resolved(expected.lines, competition)
    base = replace(base, unmatched=unmatched, repeated=repeated)

    ordered = stats.chronological(matches)
    carded = {m.round for m in ordered if not m.is_bye}
    known = set(competition.round_order) | carded
    order = sorted(known | {number}, key=round_sort_key)
    if not (number in carded or stats.later_round_with_cards(order, carded)[number]):
        regular = [r for r in carded if not competition.is_finals_round(r)]
        return replace(base, status=AHEAD,
                       latest_read=max(regular, key=round_sort_key, default="none"))

    missing = {team for gap in stats.through_round(
                   stats.fixtures_without_cards(ordered, competition), number)
               for team in (gap.home, gap.away)}
    held = [(slug, CARD_MISSING if slug in targets
             else NEARLY_NAMED if slug in nearly else NOT_TYPED)
            for slug in sorted(competition.teams)
            if slug not in targets or slug in missing]
    compared = sorted(slug for slug in targets if slug not in missing)
    try:
        counted = stats.through_round(
            stats.chronological(stats.ladder_matches(ordered, competition)), number)
        flipped = [replace(m, rules=replace(
            m.rules, average_unplayed=not m.rules.average_unplayed)) for m in counted]
        drawn = max((key[1] for key in (
            round_sort_key(rnd.number) for rnd in competition.rounds.values()
            if rnd.source == DRAW_DECLARED and rnd.fixtures) if key[0] == 0),
            default=0)
        rounds = _evidence_rounds(number, order[:order.index(number) + 1], known,
                                  drawn)
        checks = tuple(_checked(counted, flipped, ordered, slug, targets[slug],
                                rounds, competition, drawn) for slug in compared)
    except ScoringError:
        # A defence, not a path: nothing a club can put in a folder gets here (the
        # docstring says why), and a table beside a ladder that cannot be priced is
        # no ladder rather than a crash or a comparison against half of one.
        return base

    fixed = tuple(c.slug for c in checks
                  if c.points_differ_now() and c.points_agree_alt())
    broken = tuple(c.slug for c in checks
                   if c.typed_points and not c.points_differ_now()
                   and not c.points_agree_alt())
    return replace(base, status=COMPARED, checks=checks, held=tuple(held),
                   fixed=fixed, broken=broken)


def _resolved(lines: tuple[ExpectedLine, ...], competition: Competition):
    """Each key to a team, by :meth:`Competition.team_by_name`, in typed order.

    The rule every table in the config keyed by team goes through, so a sheet line
    takes exactly the spellings the byes line beside it takes. Last, every team a
    key naming nobody is nearest, which the ``sheet-team`` finding suggests. A side
    of the key's own club is among them, where a banner is not asked about one
    (:func:`~matchcentre.discover.nearest_team`): a line is copied off this
    competition's sheet, so ``Hilltop`` is Hilltop A or Hilltop B, where a banner
    may be a third side's misfiled card.
    """
    targets: dict[str, ExpectedLine] = {}
    unmatched: list[tuple[str, tuple[str, ...]]] = []
    repeated: list[tuple[str, str, str]] = []
    nearly: set[str] = set()
    for line in lines:
        team = competition.team_by_name(line.key)
        if team is None:
            try:
                near = nearest(slugify(line.key), sorted(competition.teams))
            except ValueError:
                near = []
            nearly.update(near)
            unmatched.append((line.key, tuple(competition.team_name(slug)
                                              for slug in near)))
        elif team.slug in targets:
            repeated.append((line.key, team.name, targets[team.slug].key))
        else:
            targets[team.slug] = line
    return targets, tuple(unmatched), tuple(repeated), nearly


def _evidence_rounds(number: str, season: list[str], known: set[str],
                     drawn: int = 0) -> list[str | Run]:
    """Every round up to the sheet's, a round nobody has a card for included.

    *season* is the round order up to the sheet's round, and *known* every round
    that has a card or is in the draw. Under a derived draw a round nobody
    exported is neither, so it is found as a whole number below the last round
    known. *drawn* is the last numbered round a declared draw types fixtures for,
    a final typed with its pairing being in no numbered week, and no number up to
    it is found: a number the club's draw passes over is a week the season does
    not have, and was measured asking for a card nobody played.
    Above it the draw says nothing, so a round of cards nobody exported is still
    found there. A number past every round known is left out, the sheet's own round
    apart: a finals card reaches any round, and ``after_round = 20260915`` was
    measured building every number up to it.

    Found by walking the weeks *season* has, never every number up to the last:
    one or two numbers between two weeks are a round each, three or more are one
    :class:`Run`. So the cost is the rounds known, whatever a card is labelled.
    """
    numbered = [key[1] for key in map(round_sort_key, known) if key[0] == 0]
    last = min(int(number), max(numbered, default=0))
    present = set(season)
    weeks = sorted({key[1] for key in map(round_sort_key, season)
                    if key[0] == 0 and key[1] <= last})
    found: list[str | Run] = []
    after = 0
    for week in [*weeks, last + 1]:
        start = max(after, drawn)
        if week - start - 1 >= 3:
            found.append(Run(start + 1, week - 1))
        else:
            found.extend(str(n) for n in range(start + 1, week))
        if week <= last and str(week) not in present:
            found.append(str(week))
        after = week
    return sorted([*present, *found], key=lambda entry: round_sort_key(
        str(entry.first) if isinstance(entry, Run) else entry))


def _checked(counted: list[Match], flipped: list[Match], read: list[Match],
             slug: str, line: ExpectedLine, rounds: list[str | Run],
             competition: Competition, drawn: int) -> TeamCheck:
    """One team's check. *read* is every card, *counted* what the ladder kept.

    A round the ladder counted nothing from is "nothing read" only when no card at
    all names the team in it. A card the ladder set aside -- a level card a grade
    with no draws cannot price, two cards that disagree -- was read, has its own
    finding, and is said as that, so the advice never asks for a card that is here.
    Every card set aside is counted, and a :class:`Run` has no card by definition.

    Nor where the draw accounts for the team. A rest the club stated
    (:func:`stats.rests_standing`) is a ``bye``, though a round nobody has a card
    for credits it nothing. A round whose fixtures the club typed and which leave
    the team out is ``no fixture``, as is the sheet's own round where the draw
    passes over it, up to *drawn*. Both are weeks off. A round typed bare, whose
    fixtures were read off its cards, says nothing about who played, and nor does
    one whose fixtures name somebody the competition does not have: that name may
    be this team's, misspelt, and was measured giving the team the week off.
    """
    now = stats.points_ledger(counted, slug)
    carded: dict[str, int] = {}
    for m in read:
        if m.involves(slug):
            carded[m.round] = carded.get(m.round, 0) + 1
    figures: dict[str, list[str]] = {}
    for entry in stats.round_points(counted, slug):
        figures.setdefault(entry.round, []).append(
            f"{stats.round_display(entry.points):.1f}")
    evidence: list[str] = []
    nothing: list[str | Run] = []
    off: list[str] = []
    aside: list[str] = []
    for number in rounds:
        if isinstance(number, Run):
            evidence.append(f"R{number.first}-R{number.last} nothing read")
            nothing.append(number)
            continue
        here = [m for m in counted if m.round == number and m.involves(slug)]
        if not here:
            if number in carded:
                cards = carded[number]
                evidence.append(f"R{number} card set aside" if cards == 1
                                else f"R{number} {cards} cards set aside")
                aside.append(number)
                continue
            rnd = competition.rounds.get(number)
            if rnd is not None and slug in stats.rests_standing(rnd, read):
                evidence.append(f"R{number} bye")
                off.append(number)
                continue
            if ((rnd.source == DRAW_DECLARED and rnd.fixtures
                 and not any(slug in pair for pair in rnd.fixtures)
                 and all(team in competition.teams
                         for pair in rnd.fixtures for team in pair))
                    if rnd is not None else round_sort_key(number)[:2] <= (0, drawn)):
                evidence.append(f"R{number} no fixture")
                off.append(number)
                continue
            evidence.append(f"R{number} nothing read")
            nothing.append(number)
            continue
        paid = iter(figures.get(number, []))
        credited = len(figures.get(number, [])) == len(here)
        parts = []
        for match in here:
            if match.points_for(slug) is not None:
                parts.append(next(paid))
                continue
            kind = "bye" if match.is_bye else "not played"
            parts.append(f"{kind}, credited {next(paid)}" if credited else kind)
        evidence.append(f"R{number} " + " + ".join(parts))
        if all(m.points_for(slug) is None for m in here):
            off.append(number)
    return TeamCheck(
        slug=slug, key=line.key, typed_points=line.points,
        typed_played=line.played, points_now=now.total, counted_now=now.counted,
        decided=now.decided, points_alt=stats.points_ledger(flipped, slug).total,
        evidence=tuple(evidence), nothing_read=tuple(nothing), weeks_off=tuple(off),
        set_aside=tuple(aside))
