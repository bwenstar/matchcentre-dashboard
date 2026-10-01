"""Who is in a competition, what the draw is, and how much of that we actually know.

The third clause is the one that matters. Almost everything in here can arrive two
ways: declared by the club in ``config.toml``, or derived from the scorecards. The
two are not equally trustworthy, and the difference is invisible in the numbers, so
every field that can be derived carries a :class:`Provenance` alongside it and
that provenance travels all the way to the dashboard.

The rule this buys us is :class:`Competition.teams_declared`. A bye may only be
materialised when the team list is closed — declared by a human. Derive the team
list from cards instead and a single missing scorecard shrinks a six-team division
to five, at which point the predecessor's draw reconstruction concluded that the
one team without a card that round was *resting* and paid it its own average.
That team had played, and won. No error, no warning: a wrong ladder that looked
right.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from .format import MatchFormat
from .model import NO_OPPONENT, Match
from .scoring import Ruleset
from .slugs import slugify

__all__ = ["Provenance", "Player", "Team", "Round", "Competition", "Club",
           "competition_id", "decimal_figure", "dedupe_fixtures",
           "fixture_sort_key", "infer_byes",
           "round_number", "round_sort_key", "season_order_key", "year_of",
           "years_played",
           "CompetitionError", "DRAW_DECLARED", "DRAW_FROM_CARDS"]


class CompetitionError(ValueError):
    """A competition could not be assembled at all."""


class Provenance(Enum):
    """Where a value came from, and therefore how much to trust it."""

    DECLARED = "declared"       # a human wrote it in config.toml
    CARD = "card"               # printed on a scorecard
    DERIVED = "derived"         # inferred by this program
    MIXED = "mixed"             # some of each, across a collection

    @property
    def trusted(self) -> bool:
        return self in (Provenance.DECLARED, Provenance.CARD)


def merge_provenance(values: object) -> Provenance:
    """The provenance of a collection: ``MIXED`` unless everything agrees."""
    distinct = {v for v in values}
    if not distinct:
        return Provenance.DERIVED
    if len(distinct) == 1:
        return next(iter(distinct))
    return Provenance.MIXED


# How a draw came to exist. A declared draw can describe fixtures that have not
# happened yet; a derived one cannot, and the dashboard must not present it as a
# schedule.
DRAW_DECLARED = "declared"
DRAW_FROM_CARDS = "cards"


@dataclass(frozen=True, slots=True)
class Player:
    slug: str
    name: str
    name_source: Provenance = Provenance.DERIVED
    team: str | None = None
    is_captain: bool = False
    captain_source: Provenance = Provenance.DERIVED


@dataclass(frozen=True, slots=True)
class Team:
    """One side in a competition. ``slug`` is the key; ``code`` is decoration.

    ``code`` is the number a club's published draw gives a team. It is display
    metadata only, and it is ``None`` unless a human declared it, because it is
    printed on no scorecard and any value this program picked for it would be
    invention dressed as data.

    ``captain`` is a slug, and ``captain_declared`` is the name it was made from
    when the club named the captain in its config: empty when the captain came
    from a (C) mark. A declared captain need not be on any card, and then the
    slug is all that is left of the name unless the spelling is kept beside it --
    "Devi O'Raman" does not come back out of ``devi-o-raman``.
    """

    slug: str
    name: str
    name_source: Provenance = Provenance.DERIVED
    code: int | None = None
    home_court: int | None = None
    captain: str | None = None
    players: tuple[Player, ...] = ()
    captain_declared: str = ""

    @property
    def short_name(self) -> str:
        """The name with a leading club prefix already gone, for narrow columns.

        The prefix strip happens in :func:`~matchcentre.slugs.team_slug` at slug
        time, so by here the display name is whatever the club wants shown and
        this is simply it. Kept as a property because the dashboard asks for it by
        name.
        """
        return self.name

    def player(self, slug: str) -> Player | None:
        for player in self.players:
            if player.slug == slug:
                return player
        return None


@dataclass(frozen=True, slots=True)
class Round:
    """One round of the draw.

    ``fixtures`` are ``(home_slug, away_slug)`` pairs. ``byes`` are the teams
    resting, and are only ever filled in when the team list was declared or the
    club stated the bye itself — see this module's docstring.

    Plural because a competition can rest as a whole: a public holiday, a week
    called off before anybody travelled, a break between the regular season and
    the finals. One team resting is the common case and was for a while the only
    case this carried, which meant the week a club had to write down five byes it
    could write down one.

    ``finals_typed`` is what the club typed after ``finals =`` in the round's block,
    and ``None`` where nothing was, which ``is_finals`` cannot say: there the label
    decided. F11 and discovery's finals note both ask it, so the two cannot differ
    on whether a round's finals were typed or read off its label (4.163).
    """

    number: str
    date: str = ""
    fixtures: tuple[tuple[str, str], ...] = ()
    byes: tuple[str, ...] = ()
    is_finals: bool = False
    note: str = ""
    source: str = DRAW_FROM_CARDS
    finals_typed: bool | None = None

    @property
    def teams(self) -> tuple[str, ...]:
        out: list[str] = []
        for home, away in self.fixtures:
            for slug in (home, away):
                if slug not in out:
                    out.append(slug)
        for slug in self.byes:
            if slug and slug not in out:
                out.append(slug)
        return tuple(out)


@dataclass(frozen=True, slots=True)
class Competition:
    """One day-and-division, for one season.

    ``rounds_total`` is ``None`` when nobody has said how long the season is.
    That is not a defect to paper over: the predecessor filled it with the number
    of rounds played, so a third of the way through a ten-round season the
    dashboard's progress meter read 100%.

    ``fill_in_names`` is the display name of everyone a set row names and no roster
    block here registers, decided where every registered player's is and from the
    spelling the first card to print them gives, so that a ``[player_names]`` line
    reaches a fill-in the way it reaches anybody else.
    """

    id: str
    day: str
    division: str
    fmt: MatchFormat
    rules: Ruleset
    season: str = ""
    season_key: str = ""
    label: str = ""
    teams: dict[str, Team] = field(default_factory=dict)
    rounds: dict[str, Round] = field(default_factory=dict)
    rounds_total: int | None = None
    # The rounds with cards that a declared draw leaves out, kept only when that
    # draw gave the season its length and the cards have played past it. F3 names
    # them, where discovery's note would have said so as well; and the writer does
    # not write that length back live, where it would stop every run after.
    draw_outgrown_by: tuple[str, ...] = ()
    draw_source: str = DRAW_FROM_CARDS
    teams_declared: bool = False
    format_declared: bool = False
    provenance: dict[str, Provenance] = field(default_factory=dict)
    source: str = ""
    fill_in_names: dict[str, str] = field(default_factory=dict)

    # --- naming ---------------------------------------------------------

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        parts = [p for p in (self.day, division_label(self.division)) if p]
        return " ".join(parts) or self.id

    def provenance_of(self, aspect: str) -> Provenance:
        return self.provenance.get(aspect, Provenance.DERIVED)

    # --- teams ----------------------------------------------------------

    def team(self, slug: str) -> Team | None:
        return self.teams.get(slug)

    def team_name(self, slug: str) -> str:
        team = self.teams.get(slug)
        return team.name if team else slug

    def team_by_name(self, name: str) -> Team | None:
        """Look a team up by any spelling, via its slug."""
        try:
            wanted = slugify(name)
        except ValueError:
            return None
        for team in self.teams.values():
            if team.slug == wanted or slugify(team.name) == wanted:
                return team
        return None

    def player_index(self) -> dict[str, Player]:
        """Every registered player, by slug."""
        out: dict[str, Player] = {}
        for team in self.teams.values():
            for player in team.players:
                out[player.slug] = player
        return out

    # --- rounds ---------------------------------------------------------

    def is_finals_round(self, number: str | None) -> bool:
        """Whether a round label is a finals round, and so out of the ladder (F11).

        The one place that decides it. A round this competition knows carries the
        answer a human declared, or that its own label gave it when the draw was
        read from the cards; a label from a card that reached no round at all --
        every card carries a round, but a round only reaches ``rounds`` once the
        draw is built -- is judged by the label alone.

        Numeric means regular, always: promoting round 10 to a final because a
        club calls it one would silently drop a real round out of the ladder.
        """
        if not number:
            return False
        rnd = self.rounds.get(number)
        if rnd is not None:
            return rnd.is_finals
        return is_finals_label(number)

    def finals_advice(self, setting: str, purpose: str) -> str:
        """The sentence that sends a club to a round's ``finals``, for every sender.

        The setting lives in a ``[[...rounds]]`` block, and the blocks are a declared
        draw, as long as its regular rounds when no length is typed (4.18). Typed
        where nothing gave a length, into a draw off the cards or one of finals
        alone, one block was a draw one round long, which the next run found the
        season had outgrown (F3) and exited 1 on. So a season of no known length is
        told what else to type. A length typed, or counted off the regular rounds a
        draw declares, is one the setting does not shorten.
        """
        advice = f" Set {setting} under [[competitions.{self.id}.rounds]] {purpose}."
        if self.rounds_total is None:
            advice += (" The blocks are a declared draw, so give every round one or "
                       "set rounds_total too: a block alone is a draw one round "
                       "long.")
        return advice

    def rounds_played(self, matches: Iterable[Match], *,
                      finals: bool = False) -> list[str]:
        """The regular rounds with cards, in the order the season runs; with *finals*, the finals.

        The rounds a season's progress counts, and so the one count of them: the
        report's, season.json's ``rounds_played``, the front page's, discovery's note,
        the review's suggested length and the comment ``--force`` writes all read
        this. Regular only, because the season's length is: a declared draw is as
        long as its regular rounds and F3 counts the rounds played past it the same
        way, so a count with the finals in it read ``Round 3 of 2`` on a season of
        two rounds and a final. The finals with cards are asked for apart and said
        apart.
        """
        return sorted({m.round for m in matches
                       if self.is_finals_round(m.round) == finals},
                      key=self.round_key)

    def rounds_past_length(self, matches: Iterable[Match]) -> list[str]:
        """The regular rounds played, if there are more of them than ``rounds_total``.

        Empty when nobody has said how long the season is, or the cards fit what
        they said. The one rule for a season played past its length: F3 reports it,
        and discovery's notes leave it to F3 rather than saying it too. Finals are
        excluded: a ten-round season with a final has eleven round labels and is not
        thereby eleven rounds long.
        """
        if self.rounds_total is None:
            return []
        regular = self.rounds_played(matches)
        return regular if self.rounds_total < len(regular) else []

    @property
    def round_order(self) -> list[str]:
        return sorted(self.rounds, key=self.round_key)

    def round_key(self, number: str) -> tuple:
        """*number*'s place in the order this competition's draw runs (4.71)."""
        return round_sort_key(number, self.rounds)

    def byes_for(self, number: str) -> tuple[str, ...]:
        rnd = self.rounds.get(number)
        return rnd.byes if rnd else ()

    def with_rounds(self, rounds: dict[str, Round], *,
                    draw_source: str | None = None) -> Competition:
        return replace(self, rounds=rounds,
                       draw_source=draw_source or self.draw_source)

    def with_teams(self, teams: dict[str, Team]) -> Competition:
        return replace(self, teams=teams)

    # --- draw reconstruction -------------------------------------------

    def rounds_from_matches(self, matches: object) -> dict[str, Round]:
        """A draw described by the cards themselves.

        Only rounds that have cards appear, and only the fixtures those cards
        record: a derived draw is a history, never a schedule.

        A bye is inferred *only* when the team list was declared. With a derived
        team list, a round missing a team is far more likely to be a missing
        scorecard than a rest, and guessing wrong pays a team for a match it
        played. The shortfall is reported by :mod:`~matchcentre.validate`
        instead.
        """
        grouped: dict[str, list[Match]] = {}
        for match in matches:
            grouped.setdefault(match.round, []).append(match)

        out: dict[str, Round] = {}
        for number, played in grouped.items():
            ordered = sorted(played, key=lambda m: (m.date, m.home, m.away))
            # Deduped, because these are cards and a fixture can have two: see
            # `dedupe_fixtures`. The repeats need no note from here -- two cards
            # for a fixture is F9, and one mistake is reported once.
            fixtures, _ = dedupe_fixtures((m.home, m.away) for m in ordered
                                          if m.away != NO_OPPONENT)
            date = next((m.date for m in ordered if m.date), "")
            byes = infer_byes(fixtures, self.teams,
                              teams_declared=self.teams_declared)
            existing = self.rounds.get(number)
            out[number] = Round(
                number=number,
                date=date or (existing.date if existing else ""),
                fixtures=fixtures,
                byes=byes,
                is_finals=bool(existing and existing.is_finals) or _looks_final(number),
                note=existing.note if existing else "",
                source=DRAW_FROM_CARDS,
            )
        return out


@dataclass(frozen=True, slots=True)
class Club:
    """Every competition a club runs, across every season being reported."""

    name: str = ""
    competitions: tuple[Competition, ...] = ()
    strip_team_prefix: str | None = None

    def by_id(self, comp_id: str) -> Competition | None:
        for comp in self.competitions:
            if comp.id == comp_id:
                return comp
        return None

    def find(self, *, day: str = "", division: str = "",
             season_key: str = "") -> Competition | None:
        for comp in self.competitions:
            if day and comp.day.casefold() != day.casefold():
                continue
            if division and comp.division.casefold() != division.casefold():
                continue
            if season_key and comp.season_key != season_key:
                continue
            return comp
        return None

    @property
    def season_keys(self) -> tuple[str, ...]:
        seen: list[str] = []
        for comp in self.competitions:
            if comp.season_key and comp.season_key not in seen:
                seen.append(comp.season_key)
        return tuple(seen)

    def competition_of(self, player: str) -> Competition | None:
        """Which competition a player slug is registered in, if any."""
        for comp in self.competitions:
            if player in comp.player_index():
                return comp
        return None

    def registered_elsewhere(self, player: str,
                             comp_id: str) -> Competition | None:
        """A player registered in some *other* competition — a fill-in from within."""
        for comp in self.competitions:
            if comp.id != comp_id and player in comp.player_index():
                return comp
        return None


# --- identifiers ------------------------------------------------------------


def competition_id(day: str, division: str, season_key: str = "") -> str:
    """The directory name and JSON key for one competition.

    The season is part of the key whenever there is one. Leaving it out is what
    made the predecessor merge two seasons of the same day-and-division into a
    single ladder, then raise from the exporter when both seasons' round 1
    fixtures collided.
    """
    parts = []
    if season_key:
        parts.append(slugify(season_key))
    if day:
        parts.append(slugify(day))
    if division:
        parts.append(f"division-{slugify(division)}")
    if not parts:
        raise CompetitionError("a competition needs at least a day or a division")
    return "-".join(parts)


def division_label(division: str) -> str:
    """``"C"`` -> ``"Division C"``; anything already wordy is left alone.

    Public because the front page needs it for a competition that has no
    :class:`Competition` yet -- one a club has declared and nobody has played --
    and a second copy of this rule would put one spelling of a division on the
    picker and another on the dashboard it links to.

    Only the *leading* word decides. ``"Section 3 Grade B"`` says what it is and is
    printed as it stands, but ``"3 Grade B"`` -- which is what a card heading
    ``Division 3 Grade B`` reads as, the leading word having been consumed to find
    it -- does not, and reads as a number and a fragment without the prefix put
    back. Testing for the word anywhere in the string got that one wrong.
    """
    if not division:
        return ""
    first = division.split(maxsplit=1)[0].casefold().rstrip(".")
    if first in ("division", "grade", "section", "div"):
        return division
    return f"Division {division}"


def dedupe_fixtures(pairs: Iterable[tuple[str, str]]
                    ) -> tuple[tuple[tuple[str, str], ...],
                               tuple[tuple[str, str], ...]]:
    """One fixture per pairing, in the order it first appears, plus the repeats.

    A round's ``fixtures`` is a list of fixtures, and every place that
    reconstructs one from scorecards built it a card at a time — which is a
    fixture at a time only until a club exports a night twice. Then a seven-fixture
    season grew an eighth fixture that nobody could ever play, the draw page listed
    the night twice under its round, and the progress line read "8 of 7".

    Three paths do this, so the rule lives here rather than three times over, for
    the reason :func:`infer_byes` does: copies of a rule become separate rules.

    Compared as ``(home, away)`` in that order, not as an unordered pairing. Two
    cards with the sides swapped stay two fixtures, because merging them would
    mean choosing which side was really at home — a guess about a fact printed on
    a card, which this program refuses to make. A team down to play twice in one
    night is an error :mod:`~matchcentre.validate` raises by name.

    The repeats are handed back, distinct and in first-appearance order, because
    what a caller should say about one depends on where the list came from: two
    cards for a fixture are already reported as F9, while a club's own draw
    listing a fixture twice is a statement nothing else would ever mention.
    """
    kept: dict[tuple[str, str], None] = {}
    repeats: dict[tuple[str, str], None] = {}
    for pair in pairs:
        if pair in kept:
            repeats[pair] = None
        else:
            kept[pair] = None
    return tuple(kept), tuple(repeats)


def infer_byes(fixtures: Iterable[tuple[str, str]], teams: Iterable[str], *,
               teams_declared: bool) -> tuple[str, ...]:
    """The team resting in a round, worked out from who is missing from it.

    Rule F1 as one function, so that every place which reconstructs a draw obeys
    it identically. There are two such places — :meth:`Competition.rounds_from_matches`
    here and ``discover._build_draw`` — and while each carried its own copy, only
    one of them inferred anything: declaring a single round in ``config.toml``
    silently dropped the byes worked out for every other round.

    A bye is inferred **only** when the team list was declared. With a derived list
    a round missing a team is far more likely to be a scorecard that has not
    arrived than a rest, and guessing wrong pays a team for a match it played.

    Exactly one team unaccounted for in a closed field is a bye. Two or more is
    missing scorecards, and saying so is :mod:`~matchcentre.validate`'s job — not
    something to resolve by picking the first one. A club whose whole competition
    rested that week writes the byes down instead, which is a statement rather
    than a guess.
    """
    if not teams_declared:
        return ()
    present = {slug for fixture in fixtures for slug in fixture}
    absent = [slug for slug in teams if slug not in present]
    return (absent[0],) if len(absent) == 1 else ()


# Round labels a club uses for finals, each with the week of a finals series it is
# played in: a qualifying or quarter-final and an elimination final in the first,
# and the last night by whichever word a club uses for it. Whole-label, and looked
# up by _finals_word, so the keys are written as it leaves a label. The set of
# finals labels is this table's keys, so a label cannot be read as a final and
# have no place in the series.
_FINALS_STAGES = {"qf": 1, "quarterfinal": 1, "quarters": 1, "qualifyingfinal": 1,
                  "e": 1, "ef": 1, "elim": 1, "eliminationfinal": 1,
                  "sf": 2, "semi": 2, "semifinal": 2, "semis": 2,
                  "pf": 3, "prelimfinal": 3, "preliminaryfinal": 3,
                  "f": 4, "final": 4, "gf": 4, "grandfinal": 4}
_FINALS_LABELS = frozenset(_FINALS_STAGES)


def _finals_word(number: str) -> str:
    """*number* as the finals table is keyed: its letters, whatever the spelling.

    Case, spaces and hyphens aside, so ``Semi-Final``, ``semi final`` and
    ``SemiFinal`` are one label; a plural as its singular (``Grand Finals``); and
    a number after the word (``SF1``, ``Semi Final 2``) as one of that week's
    finals. Read as typed, ``Semi-Final`` was no final at all: scored as a
    regular round, and with no week of the series to be put in order by (4.37).
    The number's digits are the ones :func:`round_number` reads, so ``SF３`` is a
    final as ``SF3`` is, where stripping ASCII digits alone scored it into the
    ladder, and ``SF³`` is a name as ``³`` is (4.151).
    """
    word = "".join(c for c in number.casefold() if c.isalnum())
    while word and round_number(word[-1]) is not None:
        word = word[:-1]
    return word[:-1] if word.endswith("finals") else word


def _looks_final(number: str) -> bool:
    return _finals_word(number) in _FINALS_LABELS


def decimal_figure(text: str) -> int | None:
    """The number *text* prints, or None when it is not a figure ``int`` reads.

    A figure is decimal digits alone: ``3``, ``03``, a fullwidth ``３`` or an
    Arabic-Indic ``٣``, the characters ``int`` reads and ``re``'s ``\\d`` matches
    (680 in Unicode 15.0). ``str.isdigit`` is true of more, superscript, circled and
    dingbat digits (``³``, ``③``, ``❸``, 128 in 15.0), which ``int`` refuses, so
    asking it before ``int`` raised. More digits than ``int`` will read, 4300
    unless the interpreter is told otherwise, are not a figure either. Nothing
    round the digits is taken off; a caller that allows spaces strips them first.
    The one test for a round's number, the season order and every figure the
    reader takes off a card (4.151, 4.216).
    """
    if not text.isdecimal():
        return None
    try:
        return int(text)
    except ValueError:      # digits past int()'s limit (sys.get_int_max_str_digits)
        return None


def round_number(number: str) -> int | None:
    """The week a round's label numbers, or None for a label that is not a number.

    A number is decimal digits alone, less the spaces round them: ``3``, ``03``, a
    fullwidth ``３`` or an Arabic-Indic ``٣``, which ``int`` reads. A superscript or
    circled digit (``³``, ``③``, ``¹²``) is not one, and its label is a name like
    any other, a final or not by its letters (4.151). ``str.isdigit`` took those in
    and ``int`` then refused them, which stopped a build with a traceback. One
    test, shared by :func:`round_sort_key`, :func:`_finals_word`, F11 and the byes
    ``--discover`` writes back, so that a label the key orders as a name is never a
    number to the check that warns about names. More digits than ``int`` will read,
    4300 unless the interpreter is told otherwise, are a name too, where they raised
    the same way. The test is :func:`decimal_figure`'s, which the reader and the
    season order ask as well. Rejected: reading ``³`` as 3 by its digit value, which
    guesses what a club meant.
    """
    return decimal_figure(number.strip())


def round_sort_key(number: str, draw: Mapping[str, Round] | None = None) -> tuple:
    """Order rounds the way a season runs: 1..n, any other name, then the finals.

    Sorting these as plain strings gives "1, 10, 2"; sorting them as integers
    fails on ``F``; and sorting a name by its letters puts ``GF`` before ``QF``
    and ``SF``, and ``F`` before ``SF`` (4.37). So numbered rounds go in numeric
    order; a name that is not a final (``Catch-up``) after them, as text, since it
    is scored as a regular round; and a final last, in the week of the series its
    label gives it -- QF, SF, PF, then GF or F, in any spelling
    :func:`_finals_word` reads -- as text within a week.

    By the label alone, never the round's date. A declared round need not have
    one, and may give it as words (``6 & 7 Oct``); a card may print a wrong one;
    and a round moved behind the next one is still the round it was.

    The label itself comes last, so no two labels share a key. A round is the text
    its card prints, less the spaces round it (4.32), so ``3`` and ``03`` are two
    rounds and ``SF`` and ``sf`` are two, and a key that gave each pair one place
    left their order to whatever the rounds were collected in: a set, whose order
    moves with the hash seed. Within a place they run as text, ``SF`` before ``sf``,
    except that a number printed plainly comes after every other way of printing it,
    ``03`` or a fullwidth ``３``: a sheet's ``after_round`` is always a plain number,
    so the ladder after round 3 is the slice up to ``3`` and has every card of week
    3 in it. Rejected: a tiebreak at each caller, which is this rule again in every
    place that sorts rounds; the shorter label first, which would leave a card
    printing ``03`` out of a sheet's ladder after round 3; and text alone, which
    leaves ``３`` out of it, its digit sorting after ``3``.

    Handed the *draw*, the rounds by label in the order the club declared them (a
    competition's ``rounds``, or anything whose values say ``is_finals``), a name
    the draw types a final goes with the finals, where :func:`_draw_place` puts
    it. Asked of the label alone, as a caller with no competition asks it, it is a
    name: ``Major Semi`` names no week of a series, and went ahead of the QF the
    draw put it after (4.71).
    """
    label = number.strip()
    week = round_number(label)
    if week is not None:
        return (0, week, "", label == str(week), label)
    folded = label.casefold()
    stage = _FINALS_STAGES.get(_finals_word(label))
    if stage is not None:
        return (2, stage, 0, folded, label)
    placed = _draw_place(label, draw or {})
    if placed is None:
        return (1, 0, folded, label)
    return (2, *placed, label)


def _draw_place(label: str, draw: Mapping[str, Round]) -> tuple[int, int, int] | None:
    """Where a final the draw types under a name of its own is played, or None.

    After every final the draw lists above it, in the week of the latest, then by
    its place in the draw: typed ``QF``, ``Major Semi``, ``GF``, it is the QF's week
    and after the QF. The draw orders only these. A number keeps its week and a
    finals label its stage whatever is typed on them, because a label that says
    where it goes is not moved by the order the blocks were typed in. Rejected: the
    draw's order for every round, which would move every season whose rounds were
    typed out of turn, where the numbers already say the week; and the week of the
    nearest final above it rather than the latest, which puts it ahead of an SF
    typed above a QF.
    """
    rnd = draw.get(label)
    if rnd is None or not rnd.is_finals:
        return None
    week = 0
    for position, number in enumerate(draw):
        if number == label:
            return (week, 1, position)
        week = max(week, _FINALS_STAGES.get(_finals_word(number), 0))
    return None


def fixture_sort_key(key: tuple[str, ...],
                     draw: Mapping[str, Round] | None = None) -> tuple:
    """A fixture's ``(round, home, away)`` in the order the season runs.

    Earliest round first, by :func:`round_sort_key`, then by the two slugs; a
    ``(round, slug)`` pair sorts the same way. By the slugs rather than by the
    names on the row: the slug is the identity, and a list that reordered itself
    when a club corrected a team's spelling would look like a list that had
    changed. One key for the change list, the checks that list a fixture, a team or
    a player by round, and the document's list of duplicates, which sorted theirs
    as text and named round 10 before round 2 and GF before SF. The *draw* is
    :func:`round_sort_key`'s.
    """
    return (round_sort_key(key[0], draw), *key[1:])


def season_order_key(key: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    """A season key ordered oldest to newest.

    Digit runs compare as numbers and everything else case-folded, so a club that
    numbers its seasons instead of dating them gets 1, 2, 9, 10, 12 rather than
    the lexical 1, 10, 12, 2, 9. Only the key is looked at -- the folder name under
    ``data/`` -- because the season's *label* is whatever the club typed, is often
    the same word for two different seasons, and is not this module's business to
    interpret.

    This replaced a single composite key that negated each character's ordinal,
    which is reverse-lexical for two strings of equal length and inverts a pair
    where one key is a prefix of the other: the shorter tuple compares smaller, so
    ``2026`` came out above ``2026-finals`` -- oldest first, on the one page whose
    order says which season is current.

    Ordering is on the key a club chose, and that is all it can be: names with no
    order in them (``spring`` before ``autumn``) come out in the order they read
    in, which is the club's to fix by renaming a folder. The key itself comes last,
    so two folders are never one place: ``season-1`` and ``season-01`` had one, and
    came out in whatever order the hash seed left the set they were found in.

    Here rather than in site.py because two pages order seasons -- the front page
    newest first, the careers page oldest first -- and two copies of this rule
    would be two orders the day one of them is edited.

    A run is a number when :func:`decimal_figure` reads it, and text otherwise.
    ``isdigit`` then ``int`` raised on ``2026³``, ``¹²`` and ``❸``, whose
    superscript and dingbat digits ``isdigit`` takes in and ``int`` refuses, and on
    a run of more digits than ``int`` reads. A key reaches the pages slugified, so
    in ASCII digits, and it was the long run that stopped the front page (4.216).
    """
    parts = re.findall(r"\d+|\D+", key.strip().casefold())
    return (tuple((1, 0, part) if figure is None else (0, figure, "")
                  for part, figure in zip(parts, map(decimal_figure, parts))), key)


def is_finals_label(number: str) -> bool:
    """Public form of the finals test, for the parser and validator."""
    return _looks_final(number)


def rounds_played_words(count: int) -> str:
    """``"1 round played"``, ``"3 rounds played"``: what the dashboard says of *count*.

    Worded as the page words it (``plural`` in app.js), for every sentence that
    tells a club what the dashboard will say -- discovery's note, the review, the
    comment ``--force`` writes and the front page. Written out in each, three of
    the four said ``"1 rounds played"`` and the page said ``1 round played``.
    """
    return f"{count} round{'' if count == 1 else 's'} played"


def year_of(date: str) -> str:
    """The season a card's date belongs to when only the date is known, or ``""``.

    Here beside :func:`competition_id` because it is part of the same question:
    which competition a card is filed under is the day, the division and the season,
    and a card that is in no season folder has only its own date to say which season
    that is. Two callers ask it -- the season key, and :func:`years_played`, which is
    how the two checks that notice one round holding cards from two different years
    ask -- and they have to agree, because one of them decides where a card is filed
    and the others explain the filing to a club. A date this cannot read is no
    evidence of a year at all,
    which is why it answers with an empty string rather than a guess: a check that
    read ``"2026-13-45"`` as 2026 would report a season boundary that nothing but a
    typo put there.
    """
    try:
        return str(datetime.date.fromisoformat(date).year)
    except ValueError:
        return ""


def years_played(matches: Iterable[Match]) -> list[str]:
    """The calendar years a group of cards were actually played in, earliest first.

    The evidence for an archive filed as one season, asked the same way by both of
    the checks that report one: no round can have been played in two different
    calendar years, so two years here means two seasons under one data folder.

    Only cards that count as played. A bye or a washed-out night beside a played card
    is one club's own note about a round, not a second season's claim on it — and the
    one thing that would otherwise read as two seasons is the round washed out in
    December and replayed in January, which is a single season doing exactly what
    summer competitions do. Reading those in would tell a club to split a season in
    two because it rained.

    One function rather than one per caller, because a note and a finding
    disagreeing about whether a folder holds two seasons is worse than either being
    wrong: one of them says there is nothing to do.
    """
    return sorted({year_of(m.date) for m in matches
                   if m.counts_as_played and year_of(m.date)})
