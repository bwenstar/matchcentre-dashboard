"""Cards in, competitions out, with a written record of everything assumed.

This is where the project earns or loses its trustworthiness. Every one of the
five of the predecessor's measured routing failures happened at this step, where
predecessor, where the same work was spread between the parser's routing and
the same work was spread between the parser's routing and the competition
builder. So the rules that stop them are all here, in one
readable pass, and each is written down next to the code that keeps it:

* **Routing** is by ``(season key, weekday of the card's own date, division
  printed in the card's own header)``. Not by filename, not by directory. The one
  exception the contract allows is the season key in multi-season mode, which *is*
  the first path segment under ``data/``; that is recorded as path-derived and
  said out loud in :attr:`Discovery.notes`. Competition ids carry the season key,
  which is what stops two seasons of one day-and-division merging into a single
  ladder.
* **Nothing absent is treated as evidence.** A round short of cards becomes a
  note for :mod:`~matchcentre.validate`, never a bye: byes need a closed team
  list, and only a human can close one (F1). A draw read off cards describes only
  rounds that were played (F2) and leaves ``rounds_total`` null (F3).
* **Every derived identity is labelled.** Team names, player names, captains, the
  season and the draw each arrive with a :class:`~matchcentre.competition.Provenance`
  so the dashboard can mark them (F7). Three values are used consistently here:
  ``DECLARED`` for anything a human wrote in ``config.toml``, ``CARD`` for
  anything a scorecard actually printed, and ``DERIVED`` for anything this program
  worked out on its own. Only the third needs a reviewer.
* **Two cards for one fixture are kept, both of them** (F9). The predecessor
  overwrote one with the other in a dict and then raised an uncaught ``ValueError``
  from the exporter; here they stay in the list, and the note names both files and
  both match ids.

The module refuses to guess in exactly two ways. A card it cannot place becomes a
:class:`~matchcentre.card.Rejection` — reported by path, counted, and fatal to the
run's exit status (F4) — and a genuine contradiction about the *shape* of a match
raises :class:`DiscoveryError`, because a competition whose cards disagree about
how many sets a match has cannot be scored either way round.
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import format as fmt_mod
from .card import Card, Rejection
from .competition import (DRAW_DECLARED, DRAW_FROM_CARDS, Club, Competition,
                          CompetitionError, Player, Provenance, Round, Team,
                          competition_id, is_finals_label, merge_provenance,
                          round_sort_key)
from .config import CompetitionConfig, Config
from .model import Match, MatchStatus, Rating, SetResult
from .scoring import Ruleset
from .slugs import (apply_alias, display_name, near_misses, slugify, team_slug,
                    unslug)

__all__ = ["Discovery", "DiscoveryError", "discover", "season_key_for", "route"]


class DiscoveryError(ValueError):
    """The cards contradict each other, or the config, about a match's shape.

    The only condition in this module worth stopping a run for. Everything else a
    club can get wrong produces a rejected card or a note, because a build that
    reports its problems is more useful than one that refuses to run. A format
    disagreement is different: neither reading wins, and scoring a card under the
    wrong shape is how a truncated card becomes a plausible wrong scoreline.

    Deliberately a ``ValueError``, like ``ConfigError`` and ``CompetitionError``,
    so a caller that already handles bad input handles this too.
    """


# Weekday names, indexed by ``date.weekday()``. Spelled out rather than taken
# from ``strftime("%A")`` because that is locale-dependent, and a competition id
# built from it would change with the machine's language settings — output has to
# be byte-identical across runs (CONTRACT §0).
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")

# How a season key was arrived at, for the notes. The distinction matters: one is
# read off a path, which the contract permits exactly here and nowhere else, and
# the other is a fallback that a club should probably fix by moving the file.
_FROM_FOLDER = "folder"
_FROM_DATE = "date"
_NO_SEASON = "none"


@dataclass(frozen=True, slots=True)
class Discovery:
    """Everything one pass over ``data/`` produced.

    ``matches`` and ``cards`` are keyed by competition id and stay parallel: the
    cards a competition was built from are kept beside the matches so
    :mod:`~matchcentre.validate` can re-check a reading against its own source
    without re-parsing a PDF.

    ``notes`` is prose for a human, printed by ``build.py``. Each note says what
    was assumed and which file to edit to change it.
    """

    club: Club
    matches: dict[str, list[Match]]
    cards: dict[str, list[Card]]
    rejected: list[Rejection]
    notes: list[str]
    club_name: str


# --- routing ----------------------------------------------------------------


def season_key_for(card: Card, *, root: Path, multi: bool) -> str:
    """Which season a card belongs to.

    In single-season mode there is no key, and the empty string keeps it out of
    competition ids. In multi-season mode the key is the first path segment under
    ``root`` — ``data/2026-winter/round-3/card.pdf`` is season ``2026-winter`` —
    which is the one fact in this program allowed to come from a path, because a
    club filing last season's cards in their own folder is the club telling us
    something no scorecard prints.

    A card sitting directly in ``root`` has no segment to read, so it falls back
    to the year on its own date. That is a guess, it is reported as one, and it is
    also why cards for two seasons should not share a folder.
    """
    return _season_key(card, root=root, multi=multi)[0]


def route(card: Card, *, config: Config, multi: bool) -> str:
    """The id of the competition this card belongs to.

    ``(season key, weekday, division)``, in that order, every part of it read from
    the card itself — with the season key's one path exception above. A club that
    has already named the competition in ``config.toml`` gets its own id back
    instead of the generated one, so a hand-written ``[competitions.tuesday-c]``
    section takes effect rather than sitting there doing nothing.

    Raises :class:`~matchcentre.competition.CompetitionError` for a card with
    neither a day nor a division, which cannot be filed anywhere;
    :func:`discover` catches that and rejects the card by path.

    Note that ``root`` is not a parameter here, so the season key comes from
    ``card.source`` read as a path relative to the data directory. An absolute
    source cannot be placed against a root that was not supplied, and rather than
    guess at one this falls back to the date. :func:`discover` always goes through
    :func:`season_key_for` with the root it actually walked, which is the
    authoritative form.
    """
    key = _season_key(card, root=None, multi=multi)[0]
    return _route_id(card, config=config, season_key=key)


def _season_key(card: Card, *, root: Path | None,
                multi: bool) -> tuple[str, str]:
    """``(key, origin)``, where origin is one of the ``_FROM_*`` constants."""
    if not multi:
        return "", _NO_SEASON
    segment = _season_segment(card.source, root)
    if segment:
        try:
            return slugify(segment), _FROM_FOLDER
        except ValueError:
            pass        # a folder named "..." is no help; fall through to the date
    year = _year_of(card.date)
    if year:
        return year, _FROM_DATE
    return "", _NO_SEASON


def _season_segment(source: str, root: Path | None) -> str:
    """The first directory of ``source`` under ``root``, or ``""``.

    Returns ``""`` rather than guessing whenever the source cannot be placed under
    the root — an absolute path from another tree has no first segment we are
    entitled to read, and inventing one would file a whole season under a folder
    name like ``home``.
    """
    if not source:
        return ""
    path = Path(source)
    if root is not None:
        base = Path(root)
        try:
            path = path.relative_to(base)
        except ValueError:
            try:
                path = path.resolve().relative_to(base.resolve())
            except (ValueError, OSError):
                if path.is_absolute():
                    return ""
    elif path.is_absolute():
        return ""
    parts = path.parts[:-1]         # drop the filename
    return parts[0] if parts else ""


def _route_id(card: Card, *, config: Config, season_key: str) -> str:
    day = _day_of(card)
    division = card.division.strip()
    generated = competition_id(day, division, season_key)
    return _declared_id(config, day, division, season_key) or generated


def _declared_id(config: Config, day: str, division: str,
                 season_key: str) -> str:
    """The club's own id for this day-and-division, if it named one.

    Matched on the fields, not on the section name, so a club may call the section
    whatever it likes. Both the day and the division must be given: a section that
    names only one of them could claim cards from a competition it was not about,
    and in multi-season mode the season key must match exactly, since that is the
    key whose absence merged two seasons in the predecessor.
    """
    for comp_id in sorted(config.competitions):
        declared = config.competitions[comp_id]
        if not (declared.day and declared.division):
            continue
        if _same(declared.day, day) and _same(declared.division, division) \
                and (declared.season_key or "") == season_key:
            return comp_id
    return ""


def _same(a: str, b: str) -> bool:
    """Two spellings of one label. ``"Div C"`` and ``"div c"`` are the same."""
    try:
        return slugify(a) == slugify(b)
    except ValueError:
        return a.strip().casefold() == b.strip().casefold()


def _day_of(card: Card) -> str:
    """The weekday a card was played, from the card's own date.

    The date is the only source of the weekday, because that is what was measured
    off real exported cards.
    ``card.weekday`` is used only when there is no readable date, and a
    disagreement between the two is reported by :func:`discover` because it means
    the parser and this module read the header differently.
    """
    return _weekday_of(card.date) or card.weekday.strip()


def _weekday_of(date: str) -> str:
    try:
        return WEEKDAYS[datetime.date.fromisoformat(date).weekday()]
    except ValueError:
        return ""


def _year_of(date: str) -> str:
    try:
        return str(datetime.date.fromisoformat(date).year)
    except ValueError:
        return ""


def _where(card: Card) -> str:
    """A card by name, for a message a human has to act on."""
    name = card.source or "<unnamed card>"
    return name if card.page == 0 else f"{name} page {card.page + 1}"


# --- one card's two team slugs ----------------------------------------------


@dataclass(frozen=True, slots=True)
class _Sided:
    """A card with its banner resolved to slugs, so it is only done once."""

    card: Card
    home: str
    away: str


def _slug_team(name: str, config: Config) -> str:
    return apply_alias(team_slug(name, strip_prefix=config.strip_team_prefix),
                       config.aliases)


def _slug_player(name: str, config: Config) -> str:
    return apply_alias(slugify(name), config.aliases)


# --- entry point ------------------------------------------------------------


def discover(cards: Iterable[Card], rejected: Iterable[Rejection], *,
             config: Config, root: Path) -> Discovery:
    """Group cards into competitions and build their matches.

    The whole pass is deterministic: cards are handled in path order, teams and
    rounds come out in a fixed order, and nothing depends on dictionary insertion
    order. Two runs over the same tree produce the same ``season.json``, so a
    ``git diff`` of the output shows changed results and nothing else.
    """
    multi = config.multi_season
    refused = list(rejected)
    notes: list[str] = []

    grouped: dict[str, list[Card]] = {}
    season_keys: dict[str, str] = {}
    key_origins: dict[str, str] = {}
    day_disputes: list[str] = []

    for card in sorted(cards, key=lambda c: (c.source, c.page)):
        key, origin = _season_key(card, root=root, multi=multi)
        try:
            comp_id = _route_id(card, config=config, season_key=key)
        except CompetitionError:
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="cannot be filed: the card prints neither a division nor "
                       "a date to take the day from",
                detail="routing needs the division from the header and the "
                       "weekday of the card's own date"))
            continue
        if not _weekday_of(card.date):
            # A card with no readable date cannot be placed on a night, and a
            # division alone is not a competition: the same division runs on more
            # than one night at plenty of clubs, so guessing would merge two
            # ladders. The predecessor skipped these too.
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="no readable date, so the night it was played is unknown",
                detail=f"header date was {card.date!r}"))
            continue
        if card.weekday.strip() and not _same(card.weekday, _weekday_of(card.date)):
            day_disputes.append(
                f"{_where(card)} is dated {card.date}, a "
                f"{_weekday_of(card.date)}, but the card was read as a "
                f"{card.weekday}; the date is what it was filed under")
        grouped.setdefault(comp_id, []).append(card)
        season_keys.setdefault(comp_id, key)
        key_origins.setdefault(comp_id, origin)

    competitions: list[Competition] = []
    matches: dict[str, list[Match]] = {}
    kept: dict[str, list[Card]] = {}

    for comp_id in sorted(grouped):
        built = _build_competition(
            comp_id, grouped[comp_id], config=config, root=root,
            season_key=season_keys[comp_id], key_origin=key_origins[comp_id],
            refused=refused)
        if built is None:
            continue        # every card in it was rejected; nothing to report on
        competition, comp_matches, comp_cards, comp_notes = built
        competitions.append(competition)
        matches[comp_id] = comp_matches
        kept[comp_id] = comp_cards
        notes.extend(comp_notes)

    club_name = _club_name(kept)
    club = Club(name=config.club_name or club_name,
                competitions=tuple(competitions),
                strip_team_prefix=config.strip_team_prefix or None)

    notes = (_global_notes(config, root, club, kept, multi, key_origins,
                           season_keys) + day_disputes + notes)
    return Discovery(club=club, matches=matches, cards=kept, rejected=refused,
                     notes=notes, club_name=club_name)


def _club_name(cards: dict[str, list[Card]]) -> str:
    """The letterhead most cards printed, for the config stub.

    A guess about a *label*, never about a result, so a majority vote is honest
    enough. Ties break on the spelling itself to keep the output reproducible.
    """
    tally = Counter(card.club_hint.strip()
                    for page in cards.values() for card in page
                    if card.club_hint.strip())
    if not tally:
        return ""
    best = max(tally.items(), key=lambda item: (item[1], item[0]))
    return best[0]


# --- building one competition ----------------------------------------------


def _build_competition(comp_id: str, cards: list[Card], *, config: Config,
                       root: Path, season_key: str, key_origin: str,
                       refused: list[Rejection]
                       ) -> tuple[Competition, list[Match], list[Card],
                                  list[str]] | None:
    """One competition, its matches, the cards it kept, and its notes."""
    declared = config.competition(comp_id)
    rules = config.ruleset_for(declared)
    set_rule = config.set_rule_for(declared)
    notes: list[str] = []

    usable, sided = _resolve_sides(cards, config=config, refused=refused)
    if not usable:
        return None

    fmt, format_declared = _match_format(comp_id, sided, config=config,
                                         declared=declared, set_rule=set_rule)

    day = (declared.day if declared and declared.day
           else _day_of(usable[0]))
    division = (declared.division if declared and declared.division
                else usable[0].division.strip())
    season, season_source = _season_label(config, declared, season_key, usable)

    teams, teams_declared, unknown, team_notes = _build_teams(
        comp_id, sided, config=config, declared=declared)
    notes.extend(team_notes)

    matches = [_build_match(entry, comp_id=comp_id, fmt=fmt, rules=rules,
                            config=config, unknown=unknown)
               for entry in sided]
    matches.sort(key=lambda m: (round_sort_key(m.round), m.date, m.home,
                                m.away, m.source))

    rounds, draw_source, rounds_total, total_source, round_notes = _build_draw(
        comp_id, matches, fmt=fmt, rules=rules, teams=teams,
        teams_declared=teams_declared, declared=declared)

    provenance = {
        "teams": merge_provenance(t.name_source for t in teams.values()),
        "players": merge_provenance(p.name_source for t in teams.values()
                                    for p in t.players),
        "captains": merge_provenance(
            t.player(t.captain).captain_source for t in teams.values()
            if t.captain and t.player(t.captain)),
        "season": season_source,
        "rounds_total": total_source,
        "draw": (Provenance.DECLARED if draw_source == DRAW_DECLARED
                 else Provenance.CARD),
        "format": Provenance.DECLARED if format_declared else Provenance.CARD,
    }

    competition = Competition(
        id=comp_id, day=day, division=division, fmt=fmt, rules=rules,
        season=season, season_key=season_key,
        label=declared.label if declared and declared.label else "",
        teams=teams, rounds=rounds, rounds_total=rounds_total,
        draw_source=draw_source, teams_declared=teams_declared,
        format_declared=format_declared, provenance=provenance,
        source=f"{len(usable)} scorecard{'' if len(usable) == 1 else 's'} "
               f"under {root}",
    )

    notes.extend(round_notes)
    notes.extend(_assumption_notes(competition, config, declared, key_origin,
                                   season_source, set_rule, matches))
    notes.extend(_duplicate_notes(competition, matches))
    return competition, matches, usable, notes


def _resolve_sides(cards: list[Card], *, config: Config,
                   refused: list[Rejection]) -> tuple[list[Card], list[_Sided]]:
    """Slug both banners on every card, refusing the cards that cannot be sided.

    Three refusals, all of them a card that cannot be turned into a fixture:

    * no set rows — nothing to score, and nothing for
      :func:`~matchcentre.format.from_rows` to read the shape off either, so
      letting it through raises ``FormatError`` out of :func:`discover` and takes
      the whole run with it;
    * a banner that produces no slug — the alternative, an empty slug, silently
      merges every such card into one phantom team;
    * both banners slugging to the same team — a side cannot play itself, so
      either a banner was misread or an alias has folded two real teams together.
    """
    usable: list[Card] = []
    sided: list[_Sided] = []
    for card in cards:
        if not card.sets:
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="no set rows, so there is nothing to score and no way to "
                       "tell what shape the match was",
                detail="a card's own row bands are what state its format"))
            continue
        try:
            home = _slug_team(card.home_name, config)
            away = _slug_team(card.away_name, config)
        except ValueError:
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="a team banner has no name in it",
                detail=f"home {card.home_name!r}, away {card.away_name!r}"))
            continue
        if home == away:
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="both banners name the same team, so this card has no "
                       "fixture in it",
                detail=f"home {card.home_name!r} and away {card.away_name!r} both "
                       f"become {home!r}; either a banner was misread or an "
                       f"[aliases] entry has folded two teams into one"))
            continue
        usable.append(card)
        sided.append(_Sided(card=card, home=home, away=away))
    return usable, sided


# --- match format -----------------------------------------------------------


def _match_format(comp_id: str, sided: list[_Sided], *, config: Config,
                  declared: CompetitionConfig | None,
                  set_rule: fmt_mod.SetRule) -> tuple[fmt_mod.MatchFormat, bool]:
    """The shape of a match here, cross-checked across every card.

    Each card states its own shape in its marker columns, so a competition has as
    many independent readings of the format as it has cards. They must agree.
    Cards in one competition describing different shapes means either a card lost
    a row or a club changed format mid-season, and those need opposite responses,
    so neither is chosen.
    """
    minimum = ((declared.min_sets_for_result if declared else None)
               or config.min_sets_for_result)
    by_pattern: dict[str, list[str]] = {}
    formats: dict[str, fmt_mod.MatchFormat] = {}
    for entry in sided:
        shape = fmt_mod.from_rows(entry.card.doubles_flags, rule=set_rule,
                                  min_sets_for_result=minimum)
        by_pattern.setdefault(shape.pattern, []).append(_where(entry.card))
        formats.setdefault(shape.pattern, shape)

    if len(by_pattern) > 1:
        readings = "; ".join(
            f"{pattern} on {', '.join(by_pattern[pattern])}"
            for pattern in sorted(by_pattern))
        raise DiscoveryError(
            f"{comp_id}: the cards do not agree on the shape of a match "
            f"({readings}). One of them is missing or has gained a set row, or "
            f"the competition changed format and its cards need splitting into "
            f"two seasons. Neither reading is safe to score, so nothing was "
            f"scored.")

    detected = formats[next(iter(by_pattern))]
    pinned = config.declared_format(declared)
    if pinned is None:
        return detected, False
    if pinned.pattern != detected.pattern:
        raise DiscoveryError(
            f"{comp_id}: {config.path} declares a {pinned.pattern} match "
            f"({len(pinned.slots)} sets) but the cards describe "
            f"{detected.pattern} ({len(detected.slots)} sets): "
            f"{', '.join(by_pattern[detected.pattern])}. A declared format is "
            f"used in preference to a detected one, but not while they disagree "
            f"— fix the config or find out what is wrong with the cards.")
    return pinned, True


# --- teams and players ------------------------------------------------------


@dataclass(slots=True)
class _Squad:
    """A player as the cards of one competition print them, accumulated."""

    slug: str
    printed: str = ""
    captain_marks: int = 0
    cards: int = 0


def _build_teams(comp_id: str, sided: list[_Sided], *, config: Config,
                 declared: CompetitionConfig | None
                 ) -> tuple[dict[str, Team], bool, dict[str, str], list[str]]:
    """The competition's teams, and whether the list is closed.

    Returns the teams, ``teams_declared``, a message per unrecognised team slug
    for the matches that name it, and notes. ``teams_declared`` is the switch F1
    hangs off: it is true only when a human has written the team list down, either
    as ``teams`` or as a draw whose fixtures name everybody, and it is what allows
    a bye to be materialised at all.
    """
    notes: list[str] = []
    printed: dict[str, str] = {}        # slug -> the banner as first seen
    appearances: Counter[str] = Counter()
    for entry in sided:
        for slug, name in ((entry.home, entry.card.home_name),
                           (entry.away, entry.card.away_name)):
            printed.setdefault(slug, name.strip())
            appearances[slug] += 1

    closed, closed_notes = _closed_list(config, declared)
    notes.extend(closed_notes)
    teams_declared = bool(closed)
    order = list(closed) if closed else sorted(printed)

    unknown: dict[str, str] = {}
    if teams_declared:
        for slug in sorted(set(printed) - set(closed)):
            hint = _did_you_mean(slug, closed)
            unknown[slug] = (
                f"{printed[slug]!r} is not one of the teams declared for "
                f"{comp_id}.{hint}")
            notes.append(
                f"{comp_id}: a card names {printed[slug]!r}, which is not in the "
                f"declared team list.{hint} Either the banner is wrong or the list "
                f"is: nothing was added, so this team's matches sit outside the "
                f"ladder until you fix one of them. An [aliases] entry "
                f"{slug} = <the right slug> is the one-line version.")
    else:
        notes.extend(_lookalike_notes(comp_id, printed, appearances))

    squads = _squads(sided, config=config)
    teams: dict[str, Team] = {}
    for slug in order:
        team_name, name_source = _resolve_name(
            slug, printed.get(slug, ""), config.team_names,
            declared_name=closed.get(slug, ""))
        players, captain, captain_notes = _players_of(
            slug, squads.get(slug, {}), comp_id=comp_id, config=config,
            declared=declared, team_name=team_name)
        notes.extend(captain_notes)
        teams[slug] = Team(
            slug=slug, name=team_name, name_source=name_source,
            code=_int_for_team(declared.codes if declared else {}, slug,
                               team_name),
            home_court=_int_for_team(declared.courts if declared else {}, slug,
                                     team_name),
            captain=captain, players=players)
    return teams, teams_declared, unknown, notes


def _closed_list(config: Config, declared: CompetitionConfig | None
                 ) -> tuple[dict[str, str], list[str]]:
    """The declared team list as ``slug -> declared spelling``, in club order.

    Empty when the club has not closed the list. Two sources count, per F1: a
    ``teams`` list, and a declared draw, whose fixtures name every team by
    definition of being a draw.
    """
    if declared is None:
        return {}, []
    notes: list[str] = []
    names: list[str] = list(declared.teams)
    from_draw = False
    if not names:
        for rnd in declared.rounds:
            for pair in rnd.fixtures:
                names.extend(pair)
            if rnd.bye:
                names.append(rnd.bye)
        from_draw = bool(names)

    closed: dict[str, str] = {}
    for name in names:
        try:
            slug = _slug_team(name, config)
        except ValueError:
            notes.append(f"[competitions.{declared.id}] lists a team with no "
                         f"name in it ({name!r}); it was skipped")
            continue
        if slug in closed and closed[slug] != name.strip():
            notes.append(
                f"[competitions.{declared.id}] lists {name!r} and "
                f"{closed[slug]!r}, which are the same team once slugged "
                f"({slug}); the first spelling is the one shown")
            continue
        closed.setdefault(slug, name.strip())
    if from_draw and closed:
        notes.append(
            f"[competitions.{declared.id}] has no teams list, so the team list "
            f"was closed from the declared draw instead ({len(closed)} teams). "
            f"That is what lets a bye be credited; if the draw is partial, add "
            f"teams = [...] as well.")
    return closed, notes


def _did_you_mean(slug: str, known: dict[str, str]) -> str:
    """`` Did you mean 'Riverbend Rockets' (rockets)?`` — or ``""`` if nothing is.

    Wraps :func:`~matchcentre.slugs.near_misses` and drops the suggestions that
    are not mistakes: see :func:`_siblings`. ``known`` maps each declared slug to
    the spelling the config declared it with, and both are printed: the spelling
    because it is what a reader recognises, the slug because it is what they have
    to type into ``[aliases]``.
    """
    candidates = [other for other in near_misses(slug, list(known))
                  if not _siblings(slug, other)]
    if not candidates:
        return ""
    listed = ", ".join(f"{known[other]!r} ({other})" for other in candidates)
    if len(candidates) == 1:
        return f" Did you mean {listed}?"
    return f" Did you mean one of {listed}?"


def _siblings(a: str, b: str) -> bool:
    """Whether two slugs are two real teams rather than one spelled twice.

    ``kenmore-3`` and ``kenmore-4`` are one edit apart and are two teams; so are
    ``st-lucia-a`` and ``st-lucia-b``. What they have in common is that they differ
    only in a number, or only in a trailing one-or-two-character tag — which is how
    every club distinguishes its own sides. ``rockets`` and ``rocketts`` share
    neither property, so they are treated as one team spelled twice, which is what
    a typo'd banner looks like.
    """
    if a == b:
        return False
    if _undigit(a) == _undigit(b):
        return True
    return _untagged(a) == _untagged(b) and _untagged(a) != ""


def _undigit(slug: str) -> str:
    return "".join(c for c in slug if not c.isdigit())


def _untagged(slug: str) -> str:
    """A slug without a trailing short segment: ``st-lucia-a`` -> ``st-lucia``."""
    head, _, tail = slug.rpartition("-")
    return head if head and len(tail) <= 2 else slug


def _lookalike_notes(comp_id: str, printed: dict[str, str],
                     appearances: Counter[str]) -> list[str]:
    """Pairs of team names on the cards that look like one name spelled twice.

    F8's derived half. With no declared team list there is nothing to check a
    banner against except the other banners, so the check is symmetric and the
    note names both spellings with the number of cards each appeared on — enough
    for a human to see instantly which one is the typo. It is a warning, never a
    correction: merging two teams that only look alike would be exactly the kind
    of guess this program refuses to make.
    """
    notes: list[str] = []
    slugs = sorted(printed)
    for index, slug in enumerate(slugs):
        # The default limit of three would hide a pair in a large division, and
        # every pair here becomes one line of prose, so ask for all of them.
        close = near_misses(slug, slugs, limit=len(slugs))
        for other in slugs[index + 1:]:
            if other not in close or _siblings(slug, other):
                continue
            notes.append(
                f"{comp_id}: {printed[slug]!r} ({appearances[slug]} card"
                f"{'' if appearances[slug] == 1 else 's'}) and "
                f"{printed[other]!r} ({appearances[other]} card"
                f"{'' if appearances[other] == 1 else 's'}) are within a "
                f"character or two of each other, so one of them may be a "
                f"misspelt banner that has become a team of its own. If it is, "
                f"add {slug} = {other} (or the other way round) under [aliases]; "
                f"if they are two real teams, declaring teams under "
                f"[competitions.{comp_id}] stops the question being asked.")
    return notes


def _squads(sided: list[_Sided], *, config: Config
            ) -> dict[str, dict[str, _Squad]]:
    """``team slug -> player slug -> accumulated printed evidence``.

    Only the roster block feeds this. The set rows print names too, and often a
    shortened form of them, so the roster is preferred for spelling; a name that
    appears only in the set rows belongs to a fill-in, who is not registered to
    the team and is identified as such by :mod:`~matchcentre.stats`.
    """
    out: dict[str, dict[str, _Squad]] = {}
    for entry in sided:
        for team, roster in ((entry.home, entry.card.home_roster),
                             (entry.away, entry.card.away_roster)):
            squad = out.setdefault(team, {})
            for player in roster:
                try:
                    slug = _slug_player(player.name, config)
                except ValueError:
                    continue    # nothing to key a player on; the match warns
                record = squad.setdefault(slug, _Squad(slug=slug))
                if not record.printed:
                    record.printed = player.name.strip()
                record.cards += 1
                if player.is_captain:
                    record.captain_marks += 1
    return out


def _players_of(team_slug_: str, squad: dict[str, _Squad], *, comp_id: str,
                config: Config, declared: CompetitionConfig | None,
                team_name: str) -> tuple[tuple[Player, ...], str | None,
                                         list[str]]:
    """A team's registered players, and its captain if the cards agree on one.

    The captain comes from the ``(C)`` marks the cards carry. The predecessor
    threw those away and then made "exactly one captain per team" a hard
    requirement of its configuration, which failed the whole build for a club that
    had not filled it in. Here nothing is required: no captain, or two, leaves
    ``Team.captain`` unset with both players still flagged, and
    :mod:`~matchcentre.validate` says so.
    """
    notes: list[str] = []
    declared_captain = (_str_for_team(declared.captains, team_slug_, team_name)
                        if declared else "")

    captain_slug: str | None = None
    captain_source = Provenance.CARD
    if declared_captain:
        try:
            captain_slug = _slug_player(declared_captain, config)
            captain_source = Provenance.DECLARED
        except ValueError:
            notes.append(f"[competitions.{comp_id}.captains] gives {team_name} a "
                         f"captain with no name in it ({declared_captain!r})")
    else:
        marked = sorted(slug for slug, record in squad.items()
                        if record.captain_marks)
        if len(marked) == 1:
            captain_slug = marked[0]
        elif len(marked) > 1:
            named = _listed([squad[slug].printed or slug for slug in marked])
            notes.append(
                f"{comp_id}: {team_name} has a (C) against {named} on its cards, "
                f"so no captain was taken from them. Settle it under "
                f"[competitions.{comp_id}.captains].")

    players: list[Player] = []
    for record in sorted(squad.values(), key=lambda r: r.slug):
        name, name_source = _resolve_name(record.slug, record.printed,
                                          config.player_names)
        # A player keeps the (C) their cards printed even when the config named
        # somebody else the captain: the mark is evidence, and validate.py is what
        # points out that the two disagree.
        players.append(Player(
            slug=record.slug, name=name, name_source=name_source,
            team=team_slug_,
            is_captain=record.slug == captain_slug or bool(record.captain_marks),
            captain_source=(captain_source if record.slug == captain_slug
                            else Provenance.CARD)))

    if captain_slug and captain_slug not in squad:
        notes.append(
            f"{comp_id}: {team_name}'s declared captain ({declared_captain}) is "
            f"not on any of that team's cards, so no player carries the mark. "
            f"Check the spelling against the roster printed on a scorecard.")
    return players, captain_slug, notes


def _listed(items: list[str]) -> str:
    """``"a, b and c"``. Notes are read as sentences, so they are punctuated."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _keyed_by_team(mapping: dict[str, object], slug: str,
                   name: str) -> object | None:
    """Look up a config table that a human keyed by team name, not by slug.

    ``captains``, ``courts`` and ``codes`` are written as ``"Riverbend Rockets" =``
    because that is what a club sees on its own cards, so every key is slugged
    before matching. Missing is not an error: these are all optional decoration.
    """
    for key in sorted(mapping):
        try:
            if slugify(key) in (slug, slugify(name)):
                return mapping[key]
        except ValueError:
            continue
    return None


def _int_for_team(mapping: dict[str, int], slug: str,
                  name: str) -> int | None:
    found = _keyed_by_team(mapping, slug, name)
    return int(found) if isinstance(found, int) else None


def _str_for_team(mapping: dict[str, str], slug: str, name: str) -> str:
    found = _keyed_by_team(mapping, slug, name)
    return str(found) if isinstance(found, str) else ""


def _resolve_name(slug: str, printed: str, overrides: dict[str, str], *,
                  declared_name: str = "") -> tuple[str, Provenance]:
    """A display name and where it came from, in resolution order.

    ``[names]`` in the config wins, then the mixed-case spelling a card printed,
    then smart title case of the printed form, then the un-slugged slug. The last
    two are the ones a human should look at, so they come back ``DERIVED``. The
    test for "already mixed case" is whether title-casing changed anything: if it
    did not, the card's own spelling is what is being shown and nothing was
    invented.
    """
    if slug in overrides:
        return overrides[slug], Provenance.DECLARED
    if declared_name:
        return declared_name, Provenance.DECLARED
    if printed:
        shown = display_name(printed)
        return shown, (Provenance.CARD if shown == printed
                       else Provenance.DERIVED)
    return unslug(slug), Provenance.DERIVED


def _season_label(config: Config, declared: CompetitionConfig | None,
                  season_key: str, cards: list[Card]
                  ) -> tuple[str, Provenance]:
    """What this season is called, and how well we know it."""
    if declared and declared.season:
        return declared.season, Provenance.DECLARED
    label = config.season_label(season_key) if season_key else ""
    if label:
        return label, Provenance.DECLARED
    hint = next((c.season_hint.strip() for c in cards if c.season_hint.strip()), "")
    if hint:
        return hint, Provenance.CARD
    return "", Provenance.DERIVED


# --- matches ----------------------------------------------------------------


def _build_match(entry: _Sided, *, comp_id: str, fmt: fmt_mod.MatchFormat,
                 rules: Ruleset, config: Config,
                 unknown: dict[str, str]) -> Match:
    """One card as a scored match.

    The competition's format and ruleset are attached here, not looked up later,
    because no property of a match — who won, how many points — means anything
    without both. Each set gets the format's own set rule, which is what makes
    ``SetResult.completed`` something other than a hardcoded ``>= 6``.
    """
    card = entry.card
    warnings: list[str] = list(card.warnings)

    sets: list[SetResult] = []
    for index, parsed in enumerate(card.sets):
        slot = fmt.slots[index]
        if not fmt_mod.plausible(parsed.home_games, parsed.away_games,
                                 fmt.set_rule):
            # An impossible score is the signature of a misread card: under the
            # predecessor's fail-open column grid, a reversed or offset parse
            # looked exactly like this and was scored anyway.
            warnings.append(
                f"{slot.label} reads {parsed.home_games}-{parsed.away_games}, "
                f"which cannot happen under {fmt.set_rule.name} "
                f"({fmt.set_rule.describe()}); the card may have been misread")
        sets.append(SetResult(
            slot=slot,
            home_games=parsed.home_games,
            away_games=parsed.away_games,
            home_players=_player_slugs(parsed.home_players, config, warnings),
            away_players=_player_slugs(parsed.away_players, config, warnings),
            status=parsed.status,
            rule=fmt.set_rule,
        ))

    for slug in (entry.home, entry.away):
        if slug in unknown:
            warnings.append(unknown[slug])

    return Match(
        competition=comp_id,
        round=card.round.strip(),
        home=entry.home,
        away=entry.away,
        fmt=fmt,
        rules=rules,
        date=card.date,
        court=card.court,
        match_id=card.match_id,
        sets=tuple(sets),
        status=_match_status(card, sets),
        forfeited_by=card.forfeited_by,
        disputed=card.disputed,
        home_roster=_player_slugs([e.name for e in card.home_roster], config,
                                  warnings),
        away_roster=_player_slugs([e.name for e in card.away_roster], config,
                                  warnings),
        ratings=_ratings(card, config),
        source=_where(card),
        warnings=tuple(warnings),
    )


def _player_slugs(names: Iterable[str], config: Config,
                  warnings: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for name in names:
        try:
            slug = _slug_player(name, config)
        except ValueError:
            warnings.append(f"a player name on this card ({name!r}) has no "
                            f"letters or digits in it and was left out")
            continue
        if slug not in out:
            out.append(slug)
    return tuple(out)


def _ratings(card: Card, config: Config) -> dict[str, Rating]:
    """Ratings as printed on this card, so a re-rating is captured at its round."""
    out: dict[str, Rating] = {}
    for player in (*card.home_roster, *card.away_roster):
        if player.singles is None and player.doubles is None:
            continue
        try:
            slug = _slug_player(player.name, config)
        except ValueError:
            continue
        out[slug] = Rating(singles=player.singles, doubles=player.doubles)
    return out


def _match_status(card: Card, sets: list[SetResult]) -> MatchStatus:
    """The whole-match state, from the card's own whole-match boxes.

    Order matters and follows the card: a forfeited match is a forfeit even if
    somebody also ticked Disputed, and a cancelled match was never played at all.
    ``UNPLAYED`` is reserved for a card with no scores on it and nothing ticked,
    which is a fixture that has not happened rather than a result of nil-all.
    """
    if card.forfeited_by:
        return MatchStatus.FORFEIT
    if card.cancelled:
        return MatchStatus.CANCELLED
    if card.disputed:
        return MatchStatus.DISPUTED
    if sets and not any(s.home_games or s.away_games for s in sets):
        return MatchStatus.UNPLAYED
    return MatchStatus.PLAYED


# --- the draw ---------------------------------------------------------------


def _build_draw(comp_id: str, matches: list[Match], *,
                fmt: fmt_mod.MatchFormat, rules: Ruleset,
                teams: dict[str, Team], teams_declared: bool,
                declared: CompetitionConfig | None
                ) -> tuple[dict[str, Round], str, int | None, Provenance,
                           list[str]]:
    """The rounds, where they came from, and how long the season is.

    A declared draw is a schedule: it may describe rounds nobody has played, it
    fixes ``rounds_total``, and its byes are the club's own. A draw read off the
    cards is a history — only rounds that have cards, no future fixtures (F2), and
    ``rounds_total`` stays ``None`` (F3). The predecessor set ``rounds_total`` to
    the number of rounds played, so a ten-round season read 100% complete at round
    three.
    """
    notes: list[str] = []
    played = {m.round for m in matches}

    if declared and declared.rounds:
        rounds: dict[str, Round] = {}
        for rnd in declared.rounds:
            fixtures = tuple(
                (_declared_slug(home, teams), _declared_slug(away, teams))
                for home, away in rnd.fixtures)
            here = [m for m in matches if m.round == rnd.number]
            source = DRAW_DECLARED
            if not fixtures and here:
                # The draw names this round but not its fixtures — a finals round
                # written down before anyone knew who would reach it is the usual
                # reason. The cards do know, so take the fixtures from them and
                # label the round as card-derived, rather than presenting a round
                # that has matches and claims no fixtures.
                fixtures = tuple((m.home, m.away) for m in here)
                source = DRAW_FROM_CARDS
            rounds[rnd.number] = Round(
                number=rnd.number,
                date=rnd.date or min((m.date for m in here if m.date),
                                     default=""),
                fixtures=fixtures,
                bye=_declared_slug(rnd.bye, teams) if rnd.bye else None,
                is_finals=rnd.finals or is_finals_label(rnd.number),
                note=rnd.note, source=source)
        extra = sorted(played - set(rounds), key=round_sort_key)
        for number in extra:
            # A round with cards that the draw does not mention still has to exist
            # or its matches would hang off nothing. It is marked as coming from
            # the cards, not from the draw.
            in_round = [m for m in matches if m.round == number]
            rounds[number] = Round(
                number=number,
                date=min((m.date for m in in_round if m.date), default=""),
                fixtures=tuple((m.home, m.away) for m in in_round),
                is_finals=is_finals_label(number), source=DRAW_FROM_CARDS)
        if extra:
            listed = ", ".join(extra)
            notes.append(
                f"{comp_id}: cards were found for round{'' if len(extra) == 1 else 's'} "
                f"{listed}, which the declared draw does not list. They are "
                f"scored, and shown as coming from the cards. Add them under "
                f"[[competitions.{comp_id}.rounds]] if the draw grew.")
        # Declaring the draw declares the season's length: the rounds a club
        # published are the rounds it intends to play. Finals are left out of the
        # count so the dashboard's progress reads against the regular season.
        regular = sum(1 for r in rounds.values()
                      if not r.is_finals and r.source == DRAW_DECLARED)
        total = (declared.rounds_total if declared.rounds_total is not None
                 else regular or None)
        return (rounds, DRAW_DECLARED, total,
                Provenance.DECLARED if total is not None else Provenance.DERIVED,
                notes + _shortfall_notes(comp_id, rounds, matches, teams,
                                         teams_declared))

    # No declared draw. Build the rounds the cards describe, using the
    # competition's own rule about byes: they need a closed team list.
    scaffold = Competition(id=comp_id, day="", division="", fmt=fmt, rules=rules,
                           teams=teams, teams_declared=teams_declared)
    rounds = scaffold.rounds_from_matches(matches)
    total = declared.rounds_total if declared else None
    source = Provenance.DECLARED if total is not None else Provenance.DERIVED
    return (rounds, DRAW_FROM_CARDS, total, source,
            notes + _shortfall_notes(comp_id, rounds, matches, teams,
                                     teams_declared))


def _declared_slug(name: str, teams: dict[str, Team]) -> str:
    """A name from the declared draw, matched to a team already built.

    Matched against the teams rather than re-slugged from scratch so that a draw
    written with a different spelling than the ``teams`` list still lines up. An
    unmatched name is kept as its own slug: the draw is the club's statement, and
    :mod:`~matchcentre.validate` is where a fixture naming nobody is reported.
    """
    try:
        wanted = slugify(name)
    except ValueError:
        return ""
    for slug, team in teams.items():
        if slug == wanted or slugify(team.name) == wanted:
            return slug
    return wanted


def _shortfall_notes(comp_id: str, rounds: dict[str, Round],
                     matches: list[Match], teams: dict[str, Team],
                     teams_declared: bool) -> list[str]:
    """Rounds that look short of cards, said out loud instead of resolved.

    This is F1's whole point. A round missing a team is far more likely to be a
    scorecard that has not arrived than a team resting, and the predecessor read
    it as a rest: it credited a team that had played and won with its own average
    for a bye, and reported no errors at all.
    """
    notes: list[str] = []
    counts = {number: sum(1 for m in matches if m.round == number)
              for number in rounds}
    if not counts:
        return notes
    busiest = max(counts.values())
    awaiting: list[str] = []            # declared rounds with no cards at all

    for number in sorted(rounds, key=round_sort_key):
        rnd = rounds[number]
        if rnd.source == DRAW_DECLARED:
            notes.extend(_declared_round_notes(comp_id, rnd, matches, teams,
                                               awaiting))
            continue
        if teams_declared:
            present = {slug for pair in rnd.fixtures for slug in pair}
            if rnd.bye:
                present.add(rnd.bye)
            absent = sorted(slug for slug in teams if slug not in present)
            if len(absent) >= 2:
                named = ", ".join(teams[slug].name for slug in absent)
                notes.append(
                    f"{comp_id}: round {number} accounts for none of {named}. "
                    f"Two or more teams unaccounted for is missing scorecards, "
                    f"not a bye, so no bye was credited — find the cards, or "
                    f"declare the round's fixtures under "
                    f"[[competitions.{comp_id}.rounds]].")
        elif counts[number] < busiest:
            notes.append(
                f"{comp_id}: round {number} has {counts[number]} card"
                f"{'' if counts[number] == 1 else 's'} where the fullest round "
                f"has {busiest}. With the team list undeclared that stays a gap: "
                f"a bye is only ever credited from a team list you have checked, "
                f"so uncomment teams = [...] under [competitions.{comp_id}] if "
                f"the missing card is really a team resting.")

    if awaiting:
        listed = ", ".join(awaiting)
        one = len(awaiting) == 1
        notes.append(
            f"{comp_id}: round{'' if one else 's'} {listed} "
            f"{'is' if one else 'are'} in the declared draw with no cards at all, "
            f"which is what a round nobody has played yet looks like. Nothing was "
            f"scored for {'it' if one else 'them'}.")
    return notes


def _declared_round_notes(comp_id: str, rnd: Round, matches: list[Match],
                          teams: dict[str, Team], awaiting: list[str]
                          ) -> list[str]:
    """What is missing from a round whose fixtures the club declared.

    A declared round needs no inference at all: it says who was meant to play, so
    a gap is a named card that has not arrived. That reads very differently from
    the derived case, where the same gap could be a team resting — and the two
    must not be reported in the same words. Rounds with no cards whatsoever are
    appended to ``awaiting`` for one collective note instead of one apiece, which
    matters for a club that declares ten rounds in August.
    """
    if not rnd.fixtures:
        return [f"{comp_id}: round {rnd.number} is declared with no fixtures, so "
                f"nothing can be shown for it beyond its existence. Fill in "
                f"fixtures under [[competitions.{comp_id}.rounds]], or leave it "
                f"and let the cards describe the round when they arrive."]

    # Compared unordered: a card with the sides the other way round is still that
    # fixture's card. Which side was really at home is validate.py's question.
    played = {frozenset((m.home, m.away)) for m in matches
              if m.round == rnd.number}
    missing = [pair for pair in rnd.fixtures
               if frozenset(pair) not in played]
    if not missing:
        return []
    if len(missing) == len(rnd.fixtures):
        awaiting.append(rnd.number)
        return []

    listed = ", ".join(
        f"{teams[home].name if home in teams else home} v "
        f"{teams[away].name if away in teams else away}"
        for home, away in missing)
    return [f"{comp_id}: round {rnd.number} has cards for "
            f"{len(rnd.fixtures) - len(missing)} of its {len(rnd.fixtures)} "
            f"declared fixtures — {listed} "
            f"{'is' if len(missing) == 1 else 'are'} missing. The draw says the "
            f"fixture exists, so that is a card to find, not a team resting."]


def _duplicate_notes(competition: Competition,
                     matches: list[Match]) -> list[str]:
    """Two cards for one fixture, named by path and match id — F9.

    Both matches are kept. The predecessor kept fixtures in a dict keyed by the
    pairing, so the second card overwrote the first and the exporter later raised
    an uncaught ``ValueError`` on the collision; keeping both means the count of
    cards scored still adds up and the report can point at two real files.
    """
    seen: dict[tuple[str, str, str], list[Match]] = {}
    for match in matches:
        seen.setdefault(match.key, []).append(match)
    notes: list[str] = []
    for key in sorted(seen, key=lambda k: (round_sort_key(k[0]), k[1], k[2])):
        clashing = seen[key]
        if len(clashing) < 2:
            continue
        listed = "; ".join(
            f"{m.source} (match id {m.match_id or 'none printed'})"
            for m in clashing)
        notes.append(
            f"{competition.id}: {len(clashing)} cards claim round {key[0]} "
            f"{competition.team_name(key[1])} v {competition.team_name(key[2])} "
            f"— {listed}. Both were kept and both are counted, so the ladder is "
            f"currently double-counting that fixture: delete the card that is "
            f"not the real result.")
    return notes


# --- notes ------------------------------------------------------------------


def _global_notes(config: Config, root: Path, club: Club,
                  cards: dict[str, list[Card]], multi: bool,
                  key_origins: dict[str, str],
                  season_keys: dict[str, str]) -> list[str]:
    notes: list[str] = []
    total = sum(len(page) for page in cards.values())
    if not config.exists:
        notes.append(
            f"There is no {config.path} yet, so every name, captain and season "
            f"label below was worked out from the {total} scorecard"
            f"{'' if total == 1 else 's'} themselves. Nothing there can change a "
            f"score — it supplies identity, the team list, the draw and the "
            f"scoring scheme.")
    keys = sorted({k for k in season_keys.values() if k})
    if multi:
        if keys:
            notes.append(
                f"Seasons are the folder names directly under {root}: "
                f"{', '.join(keys)}. That folder name is the only thing this "
                f"program reads off a path; give each one a readable label under "
                f"[seasons] in {config.path}.")
        by_date = sorted(comp for comp, origin in key_origins.items()
                         if origin == _FROM_DATE)
        if by_date:
            notes.append(
                f"{', '.join(by_date)}: these cards sit directly in {root} rather "
                f"than in a season folder, so the season was taken from the year "
                f"on the card. Move them into a folder named for their season to "
                f"make that certain.")
        no_key = sorted(comp for comp, origin in key_origins.items()
                        if origin == _NO_SEASON)
        if no_key:
            notes.append(
                f"{', '.join(no_key)}: no season folder and no readable date, so "
                f"these cards carry no season key at all. In multi-season mode "
                f"that means two seasons of the same day and division would share "
                f"one ladder — file them under a season folder.")
    for comp_id in sorted(config.competitions):
        if comp_id in cards:
            continue
        declared = config.competitions[comp_id]
        wanted = ""
        if declared.day and declared.division:
            try:
                wanted = competition_id(declared.day, declared.division,
                                        declared.season_key)
            except CompetitionError:
                wanted = ""
        suffix = (f" No cards matched it; this program would call that "
                  f"competition {wanted!r}." if wanted
                  else " No cards matched it, and it does not give both a day and "
                       "a division to match on.")
        if wanted and multi and not declared.season_key and keys:
            # Easy to get wrong and silent when you do: in multi-season mode the
            # season key is part of the id, so a section without one matches
            # nothing at all rather than matching every season.
            suffix += (f" In multi-season mode the season is part of the id too, "
                       f"so the section needs season_key — one of "
                       f"{', '.join(keys)}.")
        notes.append(
            f"{config.path} describes [competitions.{comp_id}] but nothing was "
            f"filed under it, so none of its settings took effect.{suffix}")
    if club.name and not config.club_name:
        notes.append(
            f"The club name {club.name!r} came off the cards' letterhead. Set "
            f"name under [club] in {config.path} to write it the way you would.")
    return notes


def _assumption_notes(comp: Competition, config: Config,
                      declared: CompetitionConfig | None, key_origin: str,
                      season_source: Provenance, set_rule: fmt_mod.SetRule,
                      matches: list[Match]) -> list[str]:
    """What this competition took on trust, and the line to edit to change it."""
    notes: list[str] = []
    where = f"[competitions.{comp.id}]"

    if key_origin == _FROM_FOLDER:
        notes.append(
            f"{comp.id}: filed under season {comp.season_key!r} because that is "
            f"the folder its cards are in. Everything else about it — the day, "
            f"the division, the round, the scores — was read off the cards.")

    if not comp.division:
        notes.append(
            f"{comp.id}: none of these cards print a division, so every match "
            f"played on {comp.day or 'that night'} is in one competition here. If "
            f"the night really runs more than one division their ladders have run "
            f"together, and the only fix is cards that print it: a division is "
            f"read from the card's own header and from nowhere else, so no config "
            f"entry can separate them after the fact.")

    if comp.teams_declared:
        notes.append(
            f"{comp.id}: the team list is declared ({len(comp.teams)} teams), so "
            f"a round with exactly one team unaccounted for is credited as that "
            f"team's bye, and a name on a card that is not in the list is an "
            f"error rather than a new team.")
    else:
        listed = ", ".join(comp.teams[slug].name for slug in sorted(comp.teams))
        notes.append(
            f"{comp.id}: the team list was read from the cards — "
            f"{len(comp.teams)} teams: {listed}. Check it, then remove the # from "
            f"teams = [...] under {where} in {config.path}. Until you do, no bye "
            f"is ever credited (a missing card and a resting team look identical "
            f"from here) and a misspelt banner becomes a team of its own.")

    guessed = sorted(slug for slug, team in comp.teams.items()
                     if team.name_source is Provenance.DERIVED)
    if guessed:
        shown = ", ".join(comp.teams[slug].name for slug in guessed)
        notes.append(
            f"{comp.id}: cards shout team names, so {len(guessed)} of them were "
            f"un-shouted into {shown}. [team_names] in {config.path} is where to "
            f"correct any that read wrongly.")

    from_cards = sorted(
        team.name for team in comp.teams.values()
        if team.captain and team.player(team.captain)
        and team.player(team.captain).captain_source is Provenance.CARD)
    # A team whose cards carry more than one (C) already has its own note from
    # _players_of naming the players, so it is left out here rather than told
    # twice in less useful words.
    without = sorted(team.name for team in comp.teams.values()
                     if not team.captain and team.players
                     and not any(p.is_captain for p in team.players))
    if from_cards:
        notes.append(
            f"{comp.id}: captains for {', '.join(from_cards)} came from the (C) "
            f"marks printed on the scorecards. They are shown as derived until "
            f"they appear under [competitions.{comp.id}.captains].")
    if without:
        notes.append(
            f"{comp.id}: no (C) is printed on any card for {_listed(without)}, so "
            f"{'that team has' if len(without) == 1 else 'those teams have'} no "
            f"captain. Nothing was guessed; name "
            f"{'one' if len(without) == 1 else 'them'} under "
            f"[competitions.{comp.id}.captains] if you want "
            f"{'it' if len(without) == 1 else 'them'} shown.")

    if comp.format_declared:
        notes.append(
            f"{comp.id}: the match shape is declared as {comp.fmt.describe()}, "
            f"and every card agrees with it.")
    else:
        notes.append(
            f"{comp.id}: the match shape was read off the cards themselves — "
            f"{comp.fmt.describe()} — from the marker column against each set "
            f"row. Pin it with format or slots under [rules] if you would rather "
            f"a card that loses a row was refused than believed.")

    if not config.exists:
        notes.append(
            f"{comp.id}: sets are being scored as {set_rule.describe()} "
            f"({set_rule.name}) and the ladder as {comp.rules.describe()} "
            f"({comp.rules.name}). No scorecard can tell us either of those — "
            f"6-3 looks the same whether the set was played to 6 or to 8 — so "
            f"check them under [rules] in {config.path}.")

    if comp.rounds_total is None:
        notes.append(
            f"{comp.id}: nobody has said how many rounds the season runs, so the "
            f"dashboard will say \"{len(comp.rounds)} rounds played\" and show no "
            f"percentage. Set rounds_total under {where} once you know it — "
            f"guessing it from the rounds played is how the predecessor read 100% "
            f"complete at round three of ten.")
    else:
        # Rounds *with cards*, not rounds in the draw: a declared draw usually
        # describes rounds nobody has played yet, and counting those as played is
        # the predecessor's 100%-complete-at-round-three bug wearing a hat.
        with_cards = sorted({m.round for m in matches if m.round},
                            key=round_sort_key)
        listed = f" ({', '.join(with_cards)})" if with_cards else ""
        notes.append(
            f"{comp.id}: cards were found for {len(with_cards)} "
            f"round{'' if len(with_cards) == 1 else 's'}{listed}, and the season "
            f"is declared as {comp.rounds_total} rounds long.")

    # F11: a round is finals only if a human said so or the card's own round
    # label is not a number. Anything numeric counts towards the ladder.
    finals = sorted((number for number, rnd in comp.rounds.items()
                     if rnd.is_finals), key=round_sort_key)
    if finals:
        basis = ("declared as finals" if comp.draw_source == DRAW_DECLARED
                 else "read as finals because the round label on the cards is not "
                      "a number")
        one = len(finals) == 1
        notes.append(
            f"{comp.id}: round{'' if one else 's'} {', '.join(finals)} "
            f"{'is' if one else 'are'} {basis}, and "
            f"{'earns' if one else 'earn'} no ladder points. Set finals = true or "
            f"false under [[competitions.{comp.id}.rounds]] to decide it "
            f"yourself.")

    unplaceable = sorted({m.source for m in matches if not m.round})
    if unplaceable:
        one = len(unplaceable) == 1
        notes.append(
            f"{comp.id}: {', '.join(unplaceable)} "
            f"{'prints' if one else 'print'} no round number. "
            f"{'It is' if one else 'They are'} scored, but "
            f"{'it cannot' if one else 'they cannot'} be placed in a round, so "
            f"{'it sits' if one else 'they sit together'} at the end of the draw.")

    misread = sorted({m.source for m in matches
                      if any("cannot happen under" in w for w in m.warnings)})
    if misread:
        notes.append(
            f"{comp.id}: {', '.join(misread)} record a set score that is "
            f"impossible under {set_rule.name}. Either the set rule is wrong for "
            f"this competition or the card was misread; the match warnings name "
            f"the set.")
    return notes
