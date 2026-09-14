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

from dataclasses import dataclass, field, replace
from enum import Enum

from .format import MatchFormat
from .model import NO_OPPONENT, Match
from .scoring import Ruleset
from .slugs import slugify

__all__ = ["Provenance", "Player", "Team", "Round", "Competition", "Club",
           "competition_id", "round_sort_key", "CompetitionError",
           "DRAW_DECLARED", "DRAW_FROM_CARDS"]


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
    """

    slug: str
    name: str
    name_source: Provenance = Provenance.DERIVED
    code: int | None = None
    home_court: int | None = None
    captain: str | None = None
    players: tuple[Player, ...] = ()

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

    ``fixtures`` are ``(home_slug, away_slug)`` pairs. ``bye`` is only ever set
    when the team list was declared — see this module's docstring.
    """

    number: str
    date: str = ""
    fixtures: tuple[tuple[str, str], ...] = ()
    bye: str | None = None
    is_finals: bool = False
    note: str = ""
    source: str = DRAW_FROM_CARDS

    @property
    def teams(self) -> tuple[str, ...]:
        out: list[str] = []
        for home, away in self.fixtures:
            for slug in (home, away):
                if slug not in out:
                    out.append(slug)
        if self.bye and self.bye not in out:
            out.append(self.bye)
        return tuple(out)


@dataclass(frozen=True, slots=True)
class Competition:
    """One day-and-division, for one season.

    ``rounds_total`` is ``None`` when nobody has said how long the season is.
    That is not a defect to paper over: the predecessor filled it with the number
    of rounds played, so a third of the way through a ten-round season the
    dashboard's progress meter read 100%.
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
    draw_source: str = DRAW_FROM_CARDS
    teams_declared: bool = False
    format_declared: bool = False
    provenance: dict[str, Provenance] = field(default_factory=dict)
    source: str = ""

    # --- naming ---------------------------------------------------------

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        parts = [p for p in (self.day, _division_label(self.division)) if p]
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

    @property
    def regular_rounds(self) -> list[Round]:
        """Rounds that count towards the ladder: everything but the finals."""
        return [self.rounds[n] for n in self.round_order
                if not self.rounds[n].is_finals]

    @property
    def round_order(self) -> list[str]:
        return sorted(self.rounds, key=round_sort_key)

    def bye_for(self, number: str) -> str | None:
        rnd = self.rounds.get(number)
        return rnd.bye if rnd else None

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
            fixtures = tuple((m.home, m.away) for m in ordered
                             if m.away != NO_OPPONENT)
            date = next((m.date for m in ordered if m.date), "")
            bye = None
            if self.teams_declared:
                present = {slug for fixture in fixtures for slug in fixture}
                absent = [slug for slug in self.teams if slug not in present]
                # Exactly one team unaccounted for in a declared field is a bye.
                # Two or more is a missing card, and saying so is validate.py's
                # job -- not something to resolve by picking the first one.
                if len(absent) == 1:
                    bye = absent[0]
            existing = self.rounds.get(number)
            out[number] = Round(
                number=number,
                date=date or (existing.date if existing else ""),
                fixtures=fixtures,
                bye=bye,
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


def _division_label(division: str) -> str:
    """``"C"`` -> ``"Division C"``; anything already wordy is left alone."""
    if not division:
        return ""
    lowered = division.casefold()
    if any(word in lowered for word in ("division", "grade", "section", "div")):
        return division
    return f"Division {division}"


# Round labels a club uses for finals. Checked case-insensitively, whole-label.
_FINALS_LABELS = frozenset({"f", "final", "finals", "gf", "grand final",
                            "sf", "semi", "semi final", "semis",
                            "qf", "quarter final", "quarters",
                            "pf", "prelim final", "preliminary final",
                            "e", "elim", "elimination final"})


def _looks_final(number: str) -> bool:
    return number.strip().casefold() in _FINALS_LABELS


def round_sort_key(number: str) -> tuple[int, int, str]:
    """Order rounds the way a season runs: 1..n, then anything non-numeric.

    Sorting these as plain strings gives "1, 10, 2"; sorting them as integers
    fails on ``F``. The three-part key keeps numbered rounds in numeric order,
    puts finals after all of them, and stays total so the output is reproducible.
    """
    label = number.strip()
    if label.isdigit():
        return (0, int(label), "")
    return (1, 0, label.casefold())


def is_finals_label(number: str) -> bool:
    """Public form of the finals test, for the parser and validator."""
    return _looks_final(number)
