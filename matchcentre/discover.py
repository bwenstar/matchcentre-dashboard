"""Cards in, competitions out, with a written record of everything assumed.

This is where the project earns or loses its trustworthiness. Every one of the
five of the predecessor's measured routing failures happened at this step, where
predecessor, where the same work was spread between the parser's routing and
the same work was spread between the parser's routing and the competition
builder. So the rules that stop them are all here, in one
readable pass, and each is written down next to the code that keeps it:

* **Routing** is by ``(season key, weekday, division printed in the card's own
  header)``, the weekday being the night its competition line names, else that
  of the card's date, or the one printed beside it when the date does not read.
  Not by filename, not by directory. The one exception the contract allows is
  the season key in multi-season mode, which *is* the first path segment under
  ``data/``; that is recorded as path-derived and said out loud in
  :attr:`Discovery.notes`. Competition ids carry the season key, which is what
  stops two seasons of one day-and-division merging into a single ladder.
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

from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Collection, Iterable, Sequence

from . import careers
from . import format as fmt_mod
from .card import (Card, Rejection, date_forms, majority_letterhead, round_key,
                   weekday_of)
from .competition import (DRAW_DECLARED, DRAW_FROM_CARDS, Club, Competition,
                          CompetitionError, Player, Provenance, Round, Team,
                          competition_id, dedupe_fixtures, infer_byes,
                          is_finals_label, merge_provenance, round_sort_key,
                          rounds_played_words, season_order_key, year_of,
                          years_played)
from .config import (CompetitionConfig, Config, mode_among_labels,
                     teams_instruction, toml_key, toml_value)
from .model import Match, MatchStatus, Rating, SetResult, all_blank, rubber_award
from .scope import echoed, in_words
from .scoring import Ruleset
from .sheet import by_weeks, rounds_listed
from .slugs import (apply_alias, did_you_mean, display_name, label_key,
                    nearest, person_name, slugify, team_slug, unslug)
from .stats import (duplicates, later_round_with_cards, player_names,
                    round_cards, unlisted_words)
from .stats import other_cards

__all__ = ["Discovery", "DiscoveryError", "FiledElsewhere", "discover",
           "disputed_weekday", "filing_day", "season_key_for", "route"]


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


# How a season key was arrived at, for the notes. The distinction matters: one is
# read off a path, which the contract permits exactly here and nowhere else, and
# the other is a fallback that a club should probably fix by moving the file.
_FROM_FOLDER = "folder"
_FROM_DATE = "date"
_NO_SEASON = "none"


@dataclass(frozen=True, slots=True)
class FiledElsewhere:
    """A card of two of a competition's teams, filed under another of its season.

    Found for a round's gap once every competition is built (:func:`discover`): a
    card of round ``round`` between ``home`` and ``away``, both teams of the
    competition it is handed to, that routing filed under ``filed_under`` for the
    reason ``why`` (:func:`_why_filed_there`). ``source`` is its path, as every
    message names a card (:func:`where`), and ``beside`` says it sits in a folder
    holding that competition's own cards of the round.

    A candidate and no more: whether it is the card a gap is short of is asked gap
    by gap (:meth:`fits`), and what is said of it is what was read, never that it
    is the missing card, since two nights can field teams of one name.
    """

    round: str
    home: str
    away: str
    source: str
    filed_under: str
    why: str = ""
    beside: bool = False

    def fits(self, number: str, unaccounted: Collection[str]) -> bool:
        """Round *number*'s gap, of the teams *unaccounted*, is one this card fills.

        Both its teams are among them, sides either way round, as
        :func:`~matchcentre.stats.round_cards` pairs a card with a fixture.
        """
        return (self.round == number and self.home in unaccounted
                and self.away in unaccounted)

    def told(self, name: Callable[[str], str], *, path: bool = True) -> str:
        """What was read and where it went, in one sentence; *name* names a slug.

        Without *path* for a message that reaches ``season.json``, which names only
        its own competition's cards by path.
        """
        read = "read"
        if path:
            read += f" from {self.source}" + (
                ", in the folder of this round's other cards," if self.beside else "")
        return (f"A card of {name(self.home)} v {name(self.away)} for round "
                f"{echoed(self.round)} was {read} and filed under "
                f"{self.filed_under}" + (f", as {self.why}" if self.why else "")
                + ".")

    @staticmethod
    def uncounted(filed: Sequence[FiledElsewhere]) -> str:
        """What is said of *filed*, the cards told of one gap, once they are told.

        Without its full stop, which a note can carry on past. One sentence for every
        message that tells a card filed elsewhere, so that none says it otherwise.
        """
        return ("A card counts only where it is filed, so "
                + ("that one is" if len(filed) == 1 else "those are")
                + " not counted here")


@dataclass(frozen=True, slots=True)
class Discovery:
    """Everything one pass over ``data/`` produced.

    ``matches`` and ``cards`` are keyed by competition id and stay parallel: the
    cards a competition was built from are kept beside the matches so
    :mod:`~matchcentre.validate` can re-check a reading against its own source
    without re-parsing a PDF.

    ``notes`` is prose for a human, printed by ``build.py``. Each note says what
    was assumed and which file to edit to change it.

    ``config_warnings`` are about a line of the config that missed its aim, where only
    what the cards built can say so: the players, the teams, or the seasons.
    ``build.py`` prints them with the loader's own warnings, which a quiet run prints
    too.

    ``note_seasons`` says which season each of ``notes`` is about, in step with it:
    a season key, or ``None`` for a note about the whole run. Both come out of one
    list of pairs, so they cannot drift. Empty when a caller built this by hand, and
    ``build.py --only`` then shows every note rather than guess.

    ``strays`` are, by competition id, the cards filed under another competition of
    its season that are of two of its teams in one of its rounds
    (:class:`FiledElsewhere`), for ``card-missing`` to say where a card went. Only
    competitions that have one are keys.
    """

    club: Club
    matches: dict[str, list[Match]]
    cards: dict[str, list[Card]]
    rejected: list[Rejection]
    notes: list[str]
    club_name: str
    config_warnings: tuple[str, ...] = ()
    note_seasons: tuple[str | None, ...] = ()
    strays: dict[str, tuple[FiledElsewhere, ...]] = field(default_factory=dict)


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


def season_key_of_folder(name: str) -> str:
    """The season key a folder called *name* under ``data/`` files its cards as.

    ``""`` for a name with nothing in it to key by -- a folder called ``...`` --
    which is no season. Public because ``build.py --only`` takes a season by its
    folder's name, and a second spelling of this rule there would be a second
    answer to which season a folder is.
    """
    try:
        return slugify(name)
    except ValueError:
        return ""


def season_key_of_path(source: str, *, root: Path | None) -> str:
    """The season key *source*'s folder under *root* gives it, or ``""``.

    ``""`` for a card sitting directly in *root*, or one that cannot be placed
    under it: the router then keys that card by its date instead.
    """
    return season_key_of_folder(_season_segment(source, root))


def seasons_read(club: Club, rejected: Iterable[Rejection], *,
                 root: Path | None) -> set[str]:
    """Every season a card this run read is in: filed there, or refused in its folder.

    A season whose every card was refused is still one the run read, and the one a
    club most needs to look at. Public because ``build.py --only`` lists the
    seasons a run found, and a second list of them there would be a second answer.
    """
    read = set(club.season_keys)
    read.update(season_key_of_path(rejection.path, root=root)
                for rejection in rejected)
    read.discard("")
    return read


def _season_key(card: Card, *, root: Path | None,
                multi: bool) -> tuple[str, str]:
    """``(key, origin)``, where origin is one of the ``_FROM_*`` constants."""
    if not multi:
        return "", _NO_SEASON
    key = season_key_of_path(card.source, root=root)
    if key:
        return key, _FROM_FOLDER
    # No folder, or one named "..." that is no help: fall through to the date.
    year = year_of(card.date)
    if year:
        return year, _FROM_DATE
    return "", _NO_SEASON


def inside(source: str, root: Path) -> Path | None:
    """Where the card named *source* sits inside the folder *root*, or ``None``.

    Asked of the names first, which is how the walk spelt them and needs no disk,
    then of both resolved, for a card and a folder spelt two ways (``data/x.pdf``
    under the full path of ``data``). ``None`` when neither places it, rather than
    a guess. The card is read in *root*'s own kind of path, so a Windows folder is
    asked about in Windows' separators.
    """
    path = type(root)(source)
    try:
        return path.relative_to(root)
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None


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
        path = inside(source, Path(root)) or path
    if path.is_absolute():
        return ""
    parts = path.parts[:-1]         # drop the filename
    return parts[0] if parts else ""


def _route_id(card: Card, *, config: Config, season_key: str) -> str:
    day = filing_day(card)
    division = card.division.strip()
    generated = competition_id(day, division, season_key)
    return _declared_id(config, day, division, season_key) or generated


def _declared_id(config: Config, day: str, division: str,
                 season_key: str) -> str:
    """The club's own id for this day-and-division, if it named one.

    Matched on the fields, not on the section name, so a club may call the section
    whatever it likes. Both the day and the division must be given -- a section that
    names only one of them could claim cards from a competition it was not about --
    and the test for that is
    :attr:`~matchcentre.config.CompetitionConfig.can_claim_cards`, shared with the
    front page, which lists exactly the sections a card could land in. In
    multi-season mode the season key must match exactly, since that is the key
    whose absence merged two seasons in the predecessor.

    At most one section can match. Sorting only fixes the order in which they are
    tried; what makes the first match the only match is the config load, which refuses
    two sections claiming one day-and-division rather than leaving this loop to pick a
    winner and drop the other section's settings without a word.
    """
    for comp_id in sorted(config.competitions):
        declared = config.competitions[comp_id]
        if not declared.can_claim_cards:
            continue
        if _same(declared.day, day) and _same(declared.division, division) \
                and (declared.season_key or "") == season_key:
            return comp_id
    return ""


def _same(a: str, b: str) -> bool:
    """Two spellings of one label. ``"Div C"`` and ``"div c"`` are the same.

    The key is shared with the config load, which refuses two sections claiming one
    day-and-division: if these two disagreed about what "the same" means, a pair the
    load let through would still collide here, and one section's settings would go
    silently unused.
    """
    return label_key(a) == label_key(b)


def filing_day(card: Card) -> str:
    """The weekday a card is filed under: its night, its date's, or the one it prints.

    The night its competition line names decides whenever it names one
    (``Tuesday Night Fixtures``), because every card of a competition prints it
    whatever its date says. On a club's 201 cards, filing by the date and the
    printed weekday made 17 competitions where there are 10: two for the 2 cards
    dated two days after their night, and five for the 6 printing a range of days,
    each filed by the Sunday it closes on. Then the date, which is also the
    match's date and the year its season is taken from; then ``card.weekday`` --
    the word the card prints beside its date -- for a card whose date did not
    read. :func:`discover` notes a card on which the date disagrees with either.
    Public, as :func:`disputed_weekday` is, because ``tools/doctor.py`` says which
    of the three files a card, and a second statement of the rule is one that can
    drift. Rejected: the folder a card sits in, which is a label a human typed;
    and the config's ``day`` on its own, since a division letter does not pick the
    night when two nights run a ``B1``.
    """
    return card.night or weekday_of(card.date) or card.weekday.strip()


def disputed_weekday(card: Card) -> str:
    """The weekday a card prints that its own date contradicts, or ``""``.

    The printed weekday against the date, and never against the night: a card
    dated off its night is :func:`_off_night_dates`'s to note, in the same note.
    An undated card disputes nothing: the printed weekday and the night are all
    there is, and the reader's warning already says which files the card.
    """
    dated, printed = weekday_of(card.date), card.weekday.strip()
    return printed if dated and printed and not _same(printed, dated) else ""


def where(card: Card) -> str:
    """A card by name, for a message a human has to act on.

    Its match's :attr:`~matchcentre.model.Match.source`, so validate names the card
    by this too, and finds the one by the other.
    """
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


def _slug_player(name: str, config: Config, team: str = "") -> str:
    """One printed name to one player slug.

    ``team`` is the side of the card the name was printed on, and it only matters
    for a name the club has declared a namesake; every caller has it, so every
    caller passes it, and the one identity rule lives in
    :meth:`~matchcentre.config.Config.split_namesake` rather than in six places.
    """
    return config.split_namesake(apply_alias(slugify(name), config.aliases), team)


# --- entry point ------------------------------------------------------------


def discover(cards: Iterable[Card], rejected: Iterable[Rejection], *,
             config: Config, root: Path,
             config_text: str | None = None) -> Discovery:
    """Group cards into competitions and build their matches.

    The whole pass is deterministic: cards are handled in path order, teams and
    rounds come out in a fixed order, and nothing depends on dictionary insertion
    order. Two runs over the same tree produce the same ``season.json``, so a
    ``git diff`` of the output shows changed results and nothing else.

    *config_text* is the config file as it stands, which a note that sends the club
    to a line of it is worded from (:func:`~matchcentre.config.teams_instruction`),
    or None where this run writes the file, whose lines are the writer's.
    """
    multi = config.multi_season
    refused = list(rejected)
    # Each note beside the season it is about, from where it is made: None for the
    # run-wide ones, the card's key for a day dispute, the competition's for its own.
    notes: list[tuple[str | None, str]] = []

    grouped: dict[str, list[Card]] = {}
    season_keys: dict[str, str] = {}
    key_origins: dict[str, str] = {}
    day_disputes: list[tuple[str | None, str]] = []

    for card in sorted(cards, key=lambda c: (c.source, c.page)):
        key, origin = _season_key(card, root=root, multi=multi)
        try:
            comp_id = _route_id(card, config=config, season_key=key)
        except CompetitionError:
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="cannot be filed: the card prints neither a division nor "
                       "a date or a weekday to take the day from",
                detail="routing needs the division from the header and the "
                       "night its competition line names, else the weekday of "
                       "the card's date, or the one printed beside it when the "
                       "date does not read"))
            continue
        if not filing_day(card):
            # A card with neither a readable date nor a printed weekday cannot be
            # placed on a night, and a division alone is not a competition: the
            # same division runs on more than one night at plenty of clubs, so
            # guessing would merge two ladders. The predecessor skipped these too.
            # The detail names what would have been read, because a refused card's
            # own warnings are not printed and this is all the club is told.
            refused.append(Rejection(
                path=card.source, page=card.page,
                reason="no readable date and no weekday printed beside one, "
                       "so the night it was played is unknown",
                detail=f"a date is read when it is printed as {date_forms()}"))
            continue
        disputed = disputed_weekday(card)
        # A card its night files off its date is noted, with the weekday it
        # prints, by _off_night_dates; one dated on its night is noted here.
        if disputed and filing_day(card) == weekday_of(card.date):
            day_disputes.append((key,
                f"{where(card)} is dated {card.date}, a {weekday_of(card.date)}, "
                f"but the card prints {disputed}; the date is what it was filed "
                f"under"))
        grouped.setdefault(comp_id, []).append(card)
        season_keys.setdefault(comp_id, key)
        key_origins.setdefault(comp_id, origin)

    competitions: list[Competition] = []
    matches: dict[str, list[Match]] = {}
    kept: dict[str, list[Card]] = {}

    built_all = []
    for comp_id in sorted(grouped):
        built = _build_competition(
            comp_id, grouped[comp_id], config=config, root=root,
            season_key=season_keys[comp_id], key_origin=key_origins[comp_id],
            refused=refused, config_text=config_text)
        if built is None:
            continue        # every card in it was rejected; nothing to report on
        built_all.append(built)
        competition, comp_matches, comp_cards, _, _ = built
        competitions.append(competition)
        matches[comp_id] = comp_matches
        kept[comp_id] = comp_cards
    # The rounds' gaps wait for every competition, because a card a gap is short of
    # can have been read and filed under another of the season (4.444), and is
    # told in the gap's own note, where the competition's notes had it.
    strays = _filed_elsewhere(competitions, matches, kept)
    for competition, comp_matches, _, comp_notes, gaps_at in built_all:
        comp_notes[gaps_at:gaps_at] = _shortfall_notes(
            competition.id, competition.rounds, comp_matches, competition.teams,
            competition.teams_declared, strays.get(competition.id, ()))
        notes.extend((competition.season_key, note) for note in comp_notes)
    notes.extend((None, note) for note in _season_folder_notes(
        competitions, matches, config=config, root=root))

    club_name = _club_name(kept)
    club = Club(name=config.club_display(club_name),
                competitions=tuple(competitions),
                strip_team_prefix=config.strip_team_prefix or None)

    unreached, waiting, handed = _corrections_reaching_nobody(config, club, matches)
    split_away, split_handed = _corrections_split_away(config, club, matches)
    unlabelled, to_come = _season_label_warnings(
        config, seasons_read(club, refused, root=root), root=root)
    split_nobody, split_waits = _namesake_notes(config, club, matches)
    notes = ([(None, note) for note in _global_notes(
                 config, root, club, kept, multi, key_origins, season_keys)
              + split_waits + waiting]
             + to_come + day_disputes + notes)
    return Discovery(club=club, matches=matches, cards=kept, rejected=refused,
                     notes=[note for _, note in notes], club_name=club_name,
                     config_warnings=tuple(
                         unlabelled + _team_names_reaching_nobody(config, club, matches)
                         + split_away
                         + unreached + _names_shared(config, club, matches)
                         + _names_folded(config, club, matches, handed | split_handed)
                         + split_nobody),
                     note_seasons=tuple(key for key, _ in notes),
                     strays=strays)


def _club_name(cards: dict[str, list[Card]]) -> str:
    """The letterhead most cards printed, for the config stub.

    A guess about a *label*, never about a result, so a majority vote is honest
    enough. It is :func:`~matchcentre.card.majority_letterhead`'s, the rule the
    build's check takes one competition's letterhead for the club's by, asked here
    of every competition's cards in the order their paths are read, because a club
    has one name: its spelling most printed, and a tie to the one read first. So a
    competition most of whose cards print another club's letterhead is checked
    against that one, while the name is the season's.
    """
    read = sorted((card for page in cards.values() for card in page),
                  key=lambda card: (card.source, card.page))
    return majority_letterhead(read)[1]


# --- building one competition ----------------------------------------------


def _build_competition(comp_id: str, cards: list[Card], *, config: Config,
                       root: Path, season_key: str, key_origin: str,
                       refused: list[Rejection], config_text: str | None = None
                       ) -> tuple[Competition, list[Match], list[Card],
                                  list[str], int] | None:
    """One competition, its matches, the cards it kept, its notes, and a place.

    The place is where in the notes its rounds' gaps go, which :func:`discover`
    words once every competition is built (:func:`_shortfall_notes`).
    """
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
           else filing_day(usable[0]))
    division = (declared.division if declared and declared.division
                else usable[0].division.strip())
    season, season_source = season_name(config, declared, season_key, usable)

    # Only a card that counts registers anybody. A card that counts for nothing (see
    # Match.counts_as_played) -- a washout, a night not yet played -- is read by no
    # check, so a name on its roster alone would be registered unread and every other
    # card of the team told it had left that player out. The rule is the model's, so
    # each card is asked it as a match; and built again below, once the team list can
    # say which banners are strangers, because a match is whole only with that warning.
    # A night whose cards disagree registers only what they all print on a side
    # (_registered_rosters), for the same reason.
    built = [_build_match(entry, comp_id=comp_id, fmt=fmt, rules=rules,
                          config=config, unknown={}) for entry in sided]
    rostered = [_registering(entry, kept, config) for entry, match, kept
                in zip(sided, built, _registered_rosters(built))
                if match.counts_as_played]
    teams, teams_declared, unknown, team_notes, strangers = _build_teams(
        comp_id, sided, config=config, declared=declared, rostered=rostered)
    notes.extend(team_notes)

    matches = [_build_match(entry, comp_id=comp_id, fmt=fmt, rules=rules,
                            config=config, unknown=unknown)
               for entry in sided]
    shown, night_notes = _off_night_dates(comp_id, usable)
    notes.extend(night_notes)
    matches = [replace(m, date=shown[m.source]) if m.source in shown else m
               for m in matches]
    # In the declared draw's order, which places a final it names its own way.
    draw = {rnd.number: rnd for rnd in declared.rounds} if declared else {}
    matches.sort(key=lambda m: (round_sort_key(m.round, draw), m.date, m.home,
                                m.away, m.source))

    (rounds, draw_source, rounds_total, total_source, round_notes,
     outgrown) = _build_draw(
        comp_id, matches, fmt=fmt, rules=rules, teams=teams,
        teams_declared=teams_declared, declared=declared)

    # Named by the rule every registered player is named by, so that a
    # [player_names] line reaches a fill-in as it reaches anybody else.
    registered = {player.slug for team in teams.values() for player in team.players}
    printed = _set_row_spellings(sided, config)
    fill_in_names = {slug: _resolve_name(slug, printed.get(slug, ""),
                                         config.player_names, spell=person_name)[0]
                     for slug in sorted(_named_in_sets(matches) - registered)}

    provenance = {
        "teams": merge_provenance(t.name_source for t in teams.values()),
        "players": merge_provenance(p.name_source for t in teams.values()
                                    for p in t.players),
        # Over every team with a captain, a declared one on no card that counts
        # included: asked of the squads, those were left out, and a division whose
        # captains were all declared so read "derived".
        "captains": merge_provenance(
            t.captain_source for t in teams.values() if t.captain),
        "season": season_source,
        "rounds_total": total_source,
        "draw": (Provenance.DECLARED if draw_source == DRAW_DECLARED
                 else Provenance.CARD),
        "format": Provenance.DECLARED if format_declared else Provenance.CARD,
    }

    competition = Competition(
        id=comp_id, day=day, division=division, fmt=fmt, rules=rules,
        season=season, season_key=season_key,
        season_key_from=key_origin if season_key else "",
        label=declared.label if declared and declared.label else "",
        teams=teams, rounds=rounds, rounds_total=rounds_total,
        draw_outgrown_by=outgrown,
        draw_source=draw_source, teams_declared=teams_declared,
        format_declared=format_declared, provenance=provenance,
        source=f"{len(usable)} scorecard{'' if len(usable) == 1 else 's'} "
               f"under {root}",
        fill_in_names=fill_in_names, strangers=strangers,
    )

    notes.extend(round_notes)
    gaps_at = len(notes)
    notes.extend(_assumption_notes(competition, config, declared, key_origin,
                                   season_source, set_rule, matches,
                                   config_text=config_text))
    notes.extend(_merged_season_notes(competition, matches, config=config,
                                      root=root))
    return competition, matches, usable, notes, gaps_at


def _off_night_dates(comp_id: str, cards: list[Card]
                     ) -> tuple[dict[str, str], list[str]]:
    """The date to show each card dated off its night on, by card, and a note each.

    A card whose competition line names Tuesday nights and whose date is a
    Thursday is filed under Tuesday (:func:`filing_day`), and its match is shown
    on the date the round's other cards dated on a Tuesday print, when they agree
    on one; when none is, or they print two, on its own. Measured on a club's 201
    cards: each of the 2 dated two days after its night has 11 or 14 others in its
    round, every one dated on the night and on one date. Decided here, where a
    round's cards are together, and not by the reader, which sees one card; and a
    note rather than a warning, since nothing is wrong with the result. Rejected:
    the card's own date wherever it falls, which shows a Tuesday round's match on a
    Thursday it was not played; and siblings on any night, which lets one stray
    pick another's date. The card itself is never one of the others, since its
    date is off the night; and an undated card is left alone, since a date its
    round's other cards print is not one it printed.
    """
    shown: dict[str, str] = {}
    notes: list[str] = []
    for card in cards:
        dated, night = weekday_of(card.date), card.night
        if not (night and dated) or dated == night:
            continue
        rnd = round_key(card.round)
        dates = sorted({other.date for other in cards
                        if round_key(other.round) == rnd
                        and weekday_of(other.date) == night})
        disputed = disputed_weekday(card)
        prints = f" (the card prints {disputed})" if disputed else ""
        if len(dates) == 1:
            shown[where(card)] = dates[0]
            told = (f"shown on {dates[0]}, the date of every other card of round "
                    f"{echoed(rnd)} on a {night}")
        elif dates:
            told = (f"shown on its own date, since the other cards of round "
                    f"{echoed(rnd)} on a {night} are dated {in_words(dates)}")
        else:
            told = (f"shown on its own date, since no other card of round "
                    f"{echoed(rnd)} is dated on a {night}")
        notes.append(f"{comp_id}: {where(card)} is dated {card.date}, a {dated}"
                     f"{prints}, but its competition line names {night} nights; "
                     f"it is filed under {night} and {told}.")
    return shown, notes


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
    minimum = config.min_sets_for(declared)
    by_pattern: dict[str, list[str]] = {}
    formats: dict[str, fmt_mod.MatchFormat] = {}
    for entry in sided:
        shape = fmt_mod.from_rows(entry.card.doubles_flags, rule=set_rule)
        by_pattern.setdefault(shape.pattern, []).append(where(entry.card))
        formats.setdefault(shape.pattern, shape)

    if len(by_pattern) > 1:
        readings = "; ".join(
            f"{pattern} on {in_words(by_pattern[pattern])}"
            for pattern in sorted(by_pattern))
        # Counted once: of two readings one is wrong, of more it can be several.
        two = len(by_pattern) == 2
        raise DiscoveryError(
            f"{comp_id}: the cards do not agree on the shape of a match "
            f"({readings}). {'One of them is' if two else 'Some of them are'} "
            f"missing or {'has' if two else 'have'} gained a set row, or "
            f"the competition changed format and its cards need splitting into "
            f"{'two ' if two else ''}seasons. "
            f"{'Neither reading' if two else f'None of the {len(by_pattern)} readings'}"
            f" is safe to score, so nothing was scored.")

    detected = formats[next(iter(by_pattern))]
    if minimum is not None:
        # Only once the cards agree, so that "the cards have N set rows" is true of
        # every one of them. Put to each card as it was read, a card that lost two
        # rows was blamed on the number and a four-set grade was told to lower it.
        try:
            detected = replace(detected, min_sets_for_result=minimum)
        except fmt_mod.FormatError as exc:
            raise DiscoveryError(_minimum_too_large(
                comp_id, config, declared, minimum, len(detected.slots),
                exc)) from None
    pinned = config.declared_format(declared)
    if pinned is None:
        return detected, False
    if pinned.pattern != detected.pattern:
        sources = by_pattern[detected.pattern]
        one = len(sources) == 1
        raise DiscoveryError(
            f"{comp_id}: {config.path} declares a {pinned.pattern} match "
            f"({len(pinned.slots)} sets) but the "
            f"{'card describes' if one else 'cards describe'} "
            f"{detected.pattern} ({len(detected.slots)} sets): "
            f"{in_words(sources)}. A declared format is "
            f"used in preference to a detected one, but not while they disagree "
            f"— fix the config or find out what is wrong with the "
            f"{'card' if one else 'cards'}.")
    return pinned, True


def _minimum_too_large(comp_id: str, config: Config,
                       declared: CompetitionConfig | None, minimum: int,
                       rows: int, exc: Exception) -> str:
    """A declared ``min_sets_for_result`` these cards cannot reach, and where it is.

    The match format's own bound says only "min_sets_for_result=3 is outside
    1..2", which names neither the file, the line, nor the competition whose cards
    are shorter. A ``[rules]`` number reaches every competition without its own,
    so the way out may be to move it rather than lower it.
    """
    own = declared is not None and declared.min_sets_for_result is not None
    where = f"[competitions.{comp_id}]" if own else "[rules]"
    advice = ("" if own else f", or, if another competition needs {minimum}, "
              f"move the line into that competition's own section")
    return (f"{config.path}: {where} min_sets_for_result = {minimum}, but the "
            f"cards of {comp_id} have {rows} set row(s) ({exc}). Lower it to "
            f"{rows} or fewer{advice}.")


# --- teams and players ------------------------------------------------------


@dataclass(slots=True)
class _Squad:
    """A player as the cards of one competition print them, accumulated."""

    slug: str
    printed: str = ""
    captain_marks: int = 0
    cards: int = 0


def _build_teams(comp_id: str, sided: list[_Sided], *, config: Config,
                 declared: CompetitionConfig | None, rostered: list[_Sided]
                 ) -> tuple[dict[str, Team], bool, dict[str, str], list[str],
                            tuple[str, ...]]:
    """The competition's teams, and whether the list is closed.

    Returns the teams, ``teams_declared``, a message per unrecognised team slug
    for the matches that name it, notes, and the display name of each of those
    teams, for ``Competition.strangers``. ``teams_declared`` is the switch F1
    hangs off: it is true only when a human has written the team list down, either
    as ``teams`` or as a draw whose fixtures name everybody, and it is what allows
    a bye to be materialised at all.

    The teams are every card's banners; the squads are ``rostered``, the cards
    that count, alone, each cut to the names it registers. So a team every card of
    which counts for nothing has no squad yet, and is told so here, once, rather
    than by each note that would otherwise find its squad empty and guess why.

    A banner a declared list lacks gets its match's message and no note: F8
    reports it (``validate._check_team_list``), an error with the ``[aliases]``
    line that fixes it, which reaches season.json and the page and survives
    ``--quiet``. A note beside it said the one mistake twice.
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
    strangers: list[str] = []
    if teams_declared:
        for slug in sorted(set(printed) - set(closed)):
            hint = _did_you_mean(slug, closed)
            unknown[slug] = (
                f"{printed[slug]!r} is not one of the teams declared for "
                f"{comp_id}.{hint}")
            strangers.append(_resolve_name(slug, printed[slug], config.team_names)[0])
    else:
        notes.extend(_lookalike_notes(comp_id, printed, appearances))

    squads = _squads(rostered, config=config)
    on_rosters = _squads(sided, config=config)     # registered or not
    courts = _home_courts(sided)
    counted = {slug for entry in rostered for slug in (entry.home, entry.away)}
    waiting: list[str] = []
    teams: dict[str, Team] = {}
    for slug in order:
        team_name, name_source = _resolve_name(
            slug, printed.get(slug, ""), config.team_names,
            declared_name=closed.get(slug, ""))
        waits = slug in printed and slug not in counted
        if waits:
            waiting.append(team_name)
        players, captain, spelling, captain_notes, marked = _players_of(
            slug, squads.get(slug, {}), comp_id=comp_id, config=config,
            declared=declared, team_name=team_name, waiting=waits,
            printed_names=set(on_rosters.get(slug, {})))
        notes.extend(captain_notes)
        teams[slug] = Team(
            slug=slug, name=team_name, name_source=name_source,
            code=_int_for_team(declared.codes if declared else {}, slug,
                               team_name),
            home_court=_int_for_team(declared.courts if declared else {}, slug,
                                     team_name),
            captain=captain, players=players, captain_declared=spelling,
            courts_printed=courts.get(slug, ()), captain_marked=marked)
    if waiting:
        many = len(waiting) > 1
        notes.append(
            f"{comp_id}: every card naming {_listed(waiting)} counts for nothing so "
            f"far (a washout, or a night not yet played), so nobody is registered to "
            f"{'those teams' if many else 'that team'} and no (C) names "
            f"{'their captains' if many else 'its captain'}. A team's first card "
            f"that counts registers the players on its roster.")
    return teams, teams_declared, unknown, notes, tuple(strangers)


def _home_courts(sided: list[_Sided]) -> dict[str, tuple[tuple[int, int], ...]]:
    """The courts each team's home cards print: ``(court, cards)``, most first.

    Every card prints the court it was played on, and a team's home cards print
    its home court: on a real season of 201 cards, the court printed most at home
    was the court its club had typed for each of the 54 teams with a home card,
    and 2 of them printed a second. Every card filed counts, a washout with the
    rest, since the night was drawn on that court whatever the rain did; and each
    card, not each night, since that is what the comment the writer offers it
    under says it counted. A tie is in order of the courts' numbers, and
    :attr:`Team.court_printed` takes neither.
    """
    counts: dict[str, Counter[int]] = {}
    for entry in sided:
        if entry.card.court is not None:
            counts.setdefault(entry.home, Counter())[entry.card.court] += 1
    return {slug: tuple(sorted(count.items(), key=lambda item: (-item[1], item[0])))
            for slug, count in counts.items()}


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
            names.extend(rnd.byes)
        # [competitions.<id>.byes] is keyed by team, so its keys are teams a club
        # has named as surely as a fixture names them.
        names.extend(declared.byes)
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

    The teams :func:`nearest_team` gives, asked about by
    :func:`~matchcentre.slugs.did_you_mean` without the advice to key a line,
    because a banner is no line the club typed. ``known`` maps each declared slug
    to the spelling the config declared it with, and both are printed: the spelling
    because it is what a reader recognises, the slug because it is what they have
    to type into ``[aliases]``.
    """
    return did_you_mean([f"{known[other]!r} ({other})"
                         for other in nearest_team(slug, known)], keyed=False)


def nearest_team(slug: str, known: object) -> list[str]:
    """The declared teams nearest *slug*, less its siblings: one, a tie, or none.

    The one rule for which teams a card's banner that is no team's is asked about,
    by F8, the page's warning and, with no list declared, discovery's note alike.
    A sibling is no question,
    see :func:`_siblings`: ``hilltop`` beside ``hilltop-a`` and ``hilltop-b`` may be
    a third side of the club, on a misfiled card, and asking about the two it has
    would have it aliased onto one of them. The nearest are found first and the
    siblings taken out after, so a banner nearest a side of its own club is asked
    nothing: ``kenmore-4`` beside ``kenmore-3`` is not asked about ``kenmure-3``,
    another club two edits off. Rejected: every team within two edits, as F8 and
    discovery's note each asked, which asked about ``hiltop-a`` whether Hilltop B
    was meant, two edits off, beside Hilltop A, one. A sheet line is asked about
    its siblings (:mod:`~matchcentre.sheet`): it is copied off this competition's
    sheet, under its heading, and is never a card of another side.
    """
    return [other for other in nearest(slug, list(known))
            if not _siblings(slug, other)]


def _siblings(a: str, b: str) -> bool:
    """Whether two slugs are two real teams rather than one spelled twice.

    Two sides of a club, in the club's own scheme. ``kenmore-3`` and ``kenmore-4``
    are one edit apart and are two teams, and so are ``kenmore-3`` and
    ``kenmore-33``, because numbered sides run past nine: they differ only in a
    number, where each has one. ``st-lucia-a`` and ``st-lucia-b`` differ only in a
    trailing tag of one shape, a letter beside a letter, and ``hilltop`` and
    ``hilltop-b`` in a tag beside a side printed with none. That is how every club
    distinguishes its own sides. A tag of another shape is outside the scheme:
    ``hilltop-aa`` or ``hilltop-1`` beside ``hilltop-a``, ``kenmore-e`` beside
    ``kenmore-3``, since a club that letters its sides adds no side AA and one that
    numbers them adds no side E; and so is a digit struck into a name,
    ``hill1top-a``. Those, and ``rocketts`` beside ``rockets``, are treated as one
    team spelled twice, which is what a typo'd banner looks like. Rejected: any
    trailing tag of one or two characters, and any digit anywhere, which took a
    doubled letter or a stray digit for a side nobody declared and silenced the one
    question that finds the slip.
    """
    if a == b:
        return False
    if _undigit(a) == _undigit(b):
        return True
    head = _untagged(a)
    if head != _untagged(b):
        return False
    tags = [_shape(slug[len(head) + 1:]) for slug in (a, b) if slug != head]
    return len(tags) < 2 or tags[0] == tags[1]


def _undigit(slug: str) -> str:
    """A slug with each number in it one mark: ``kenmore-33`` -> ``kenmore-#``."""
    marked: list[str] = []
    for c in slug:
        if not c.isdigit():
            marked.append(c)
        elif not marked or marked[-1] != "#":
            marked.append("#")
    return "".join(marked)


def _shape(tag: str) -> str:
    """A side's tag as its scheme: ``b`` is ``a``, ``2b`` ``9a``, ``ab`` ``aa``."""
    return "".join("9" if c.isdigit() else "a" for c in tag)


def _untagged(slug: str) -> str:
    """A slug without a trailing short segment: ``st-lucia-a`` -> ``st-lucia``."""
    head, _, tail = slug.rpartition("-")
    return head if head and len(tail) <= 2 else slug


def _lookalike_notes(comp_id: str, printed: dict[str, str],
                     appearances: Counter[str]) -> list[str]:
    """Pairs of team names on the cards that look like one name spelled twice.

    F8's derived half. With no declared team list there is nothing to check a
    banner against except the other banners, so the check is symmetric: two are
    paired when one is the other's :func:`nearest_team` among the rest, the rule
    F8 and the page ask of a banner a declared list lacks, so a slip is paired
    with the team nearest it and a side of the club with none. The note names both
    spellings with the number of cards each appeared on — enough for a human to see
    instantly which one is the typo. It is a warning, never a correction: merging
    two teams that only look alike would be exactly the kind of guess this program
    refuses to make. Rejected: every banner within two edits
    (:func:`~matchcentre.slugs.near_misses`), which paired HILTOP A with Hilltop B,
    two edits off, beside Hilltop A, one.
    """
    notes: list[str] = []
    slugs = sorted(printed)
    pairs = {tuple(sorted((slug, other))) for slug in slugs
             for other in nearest_team(slug, [s for s in slugs if s != slug])}
    for slug, other in sorted(pairs):
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

    Only the roster block feeds this, and only from the cards that count, which is
    what the caller passes, each cut to the names :func:`_registered_rosters` says
    it registers: from a night whose cards disagree, the names they all print. The
    set rows print names too, and often a
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
                    slug = _slug_player(player.name, config, team)
                except ValueError:
                    continue    # nothing to key a player on; the match warns
                record = squad.setdefault(slug, _Squad(slug=slug))
                record.printed = _spelling(record.printed, player.name, config)
                record.cards += 1
                if player.is_captain:
                    record.captain_marks += 1
    return out


def _registered_rosters(matches: list[Match]) -> list[dict[str, frozenset[str]]]:
    """Per match, in the order given, the names each side's roster registers.

    None from a card that counts for nothing (:attr:`Match.counts_as_played`), and
    every name from any other card, except on a night :func:`stats.duplicates`
    sets aside because its cards disagree. One of those cards is wrong and nothing
    says which, so a side registers only the names every card of the night prints
    there, a washout of the night among them: a name only one of them prints was a
    Magpie with no matches on the demo, and every other Magpies card was told its
    roster left him out. A card of the night printing no roster on that side takes
    nobody off, since a roster not read is not one naming nobody. A copy, and a
    card another supersedes, agree about the result, are not set aside, and
    register as any card does.

    The one rule, asked by the gate :func:`_squads` is fed through and by
    :func:`_players_named`, so the names a line is told are waiting are the names
    this left out.
    """
    disagree = {match.key for match in duplicates(matches).conflicting}
    agreed: dict[tuple[tuple[str, str, str], str], frozenset[str]] = {}
    for match in matches:
        for side in ("home", "away"):
            roster = frozenset(match.roster_for(side))
            if match.key in disagree and roster:
                agreed[match.key, side] = agreed.get((match.key, side),
                                                     roster) & roster
    return [{side: (frozenset() if not match.counts_as_played
                    else agreed.get((match.key, side), frozenset())
                    if match.key in disagree
                    else frozenset(match.roster_for(side)))
             for side in ("home", "away")} for match in matches]


def _registering(entry: _Sided, kept: dict[str, frozenset[str]],
                 config: Config) -> _Sided:
    """*entry* with each roster block cut to the names *kept* holds for that side.

    Cut on the card, and keyed as the match keys a roster name, so :func:`_squads`
    still reads the spelling and the ``(C)`` of each name that stays.
    """
    cut: dict[str, tuple] = {}
    for side, team in (("home", entry.home), ("away", entry.away)):
        rows = []
        for row in getattr(entry.card, f"{side}_roster"):
            try:
                slug = _slug_player(row.name, config, team)
            except ValueError:
                continue    # nothing to key a player on; the match warns
            if slug in kept[side]:
                rows.append(row)
        cut[f"{side}_roster"] = tuple(rows)
    return replace(entry, card=replace(entry.card, **cut))


def _set_row_spellings(sided: list[_Sided], config: Config) -> dict[str, str]:
    """Each name a set row prints, keyed as the match keys it, and its spelling.

    Spelt by :func:`_spelling`, the rule :func:`_squads` chooses a roster name's
    spelling by, so a fill-in and a registered player are named alike. Read off
    every card whose set rows are read, a washout's included, since a fill-in named
    only there is shown too; a roster name is read off the cards that count, since
    only those register anybody. Keyed on the side's team, so a fill-in
    ``[namesakes]`` split is spelt as the card prints the name, and never as the key
    the split made.
    """
    out: dict[str, str] = {}
    for entry in sided:
        for row in entry.card.sets:
            for team, names in ((entry.home, row.home_players),
                                (entry.away, row.away_players)):
                for name in names:
                    try:
                        slug = _slug_player(name, config, team)
                    except ValueError:
                        continue    # nothing to key a player on; the match warns
                    out[slug] = _spelling(out.get(slug, ""), name, config)
    return out


def _spelling(held: str, printed: str, config: Config) -> str:
    """The spelling a player is shown by: ``held`` so far, and ``printed`` next.

    The first card's in the order the cards are read, so a rebuild names everyone
    as the last one did, unless ``[aliases]`` folds that spelling into another name
    and a later card prints one it does not fold. An alias says the spelling is
    wrong. Kept for coming first, a roster block misspelling ``Bo Nakamura`` on the
    first card read, folded by an alias, named him ``Bo Nakamora``, and a fill-in's
    set row did the same. Where every card prints a folded spelling, the first of
    them is kept, as the only spelling the cards give. Rejected: the alias's target
    un-slugged, a name no card printed.
    """
    if not held or (slugify(held) in config.aliases
                    and slugify(printed) not in config.aliases):
        return printed.strip()
    return held


def _players_of(team_slug_: str, squad: dict[str, _Squad], *, comp_id: str,
                config: Config, declared: CompetitionConfig | None,
                team_name: str, waiting: bool, printed_names: set[str]
                ) -> tuple[tuple[Player, ...], str | None, str, list[str],
                           str | None]:
    """A team's registered players, and its captain if the cards agree on one.

    The captain comes from the ``(C)`` marks the cards carry. The predecessor
    threw those away and then made "exactly one captain per team" a hard
    requirement of its configuration, which failed the whole build for a club that
    had not filled it in. Here nothing is required: no captain, or two, leaves
    ``Team.captain`` unset with both players still flagged, and
    :mod:`~matchcentre.validate` says so.

    The third value is the spelling a declared captain was given, for
    ``Team.captain_declared``, and empty when the captain came from the cards or
    the declaration held no name to slug. The fifth is the one player the (C)
    marks name, for ``Team.captain_marked``, asked whether or not the club named a
    captain, so that the writer can offer the marks beside a declared captain they
    do not name.

    ``waiting`` is a team with cards and none of them counting. The caller's note
    says why its squad is empty, so a declared captain missing from it is not told
    to check the spelling as well.

    ``printed_names`` is every name the team's roster blocks print, whether a card
    registers it or not. A declared captain among them whom the squad does not hold
    is printed as the club spelt him, on cards :func:`_registered_rosters` registers
    nobody from, or not him; he is told he waits for a card that counts, as a
    ``[player_names]`` line under his name is, and not to check a spelling that is
    right. Measured on the demo with him on one of two disagreeing round-four cards
    alone, the note sent the club to check it.
    """
    notes: list[str] = []
    declared_captain = (_str_for_team(declared.captains, team_slug_, team_name)
                        if declared else "")

    captain_slug: str | None = None
    captain_source = Provenance.CARD
    spelling = ""
    marked = sorted(slug for slug, record in squad.items() if record.captain_marks)
    if declared_captain:
        try:
            captain_slug = _slug_player(declared_captain, config, team_slug_)
            captain_source = Provenance.DECLARED
            spelling = declared_captain
        except ValueError:
            notes.append(f"[competitions.{comp_id}.captains] gives {team_name} a "
                         f"captain with no name in it ({declared_captain!r})")
    else:
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
                                          config.player_names, spell=person_name)
        # A player keeps the (C) their cards printed even when the config named
        # somebody else the captain: the mark is evidence, and validate.py is what
        # points out that the two disagree.
        players.append(Player(
            slug=record.slug, name=name, name_source=name_source,
            team=team_slug_,
            is_captain=record.slug == captain_slug or bool(record.captain_marks),
            captain_source=(captain_source if record.slug == captain_slug
                            else Provenance.CARD)))

    if captain_slug and captain_slug not in squad and not waiting:
        why = "Check the spelling against the roster printed on a scorecard."
        if captain_slug in printed_names:
            why = ("The name is printed only on a card that counts for nothing so "
                   "far (a washout, or a night not yet played), or on some but not "
                   "all of the cards of a night that disagree. A card like that "
                   "registers nobody, and a night whose cards disagree registers "
                   "only the names they all print, so the mark waits until a card "
                   "that counts names that player.")
        notes.append(
            f"{comp_id}: {team_name}'s declared captain ({declared_captain}) is "
            f"not on any of that team's cards that count, so no player carries the "
            f"mark. {why}")
    return (players, captain_slug, spelling, notes,
            marked[0] if len(marked) == 1 else None)


def _listed(items: list[str]) -> str:
    """``"a, b and c"``. Notes are read as sentences, so they are punctuated."""
    return in_words(items)


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
                  declared_name: str = "",
                  spell: Callable[[str], str] = display_name
                  ) -> tuple[str, Provenance]:
    """A display name and where it came from, in resolution order.

    ``[names]`` in the config wins, then the mixed-case spelling a card printed,
    then smart title case of the printed form, then the un-slugged slug. The last
    two are the ones a human should look at, so they come back ``DERIVED``. The
    test for "already mixed case" is whether title-casing changed anything: if it
    did not, the card's own spelling is what is being shown and nothing was
    invented. *spell* is the title case: a team's ``display_name``, and a
    player's ``person_name``, which keeps no shouted abbreviation.
    """
    if slug in overrides:
        return overrides[slug], Provenance.DECLARED
    if declared_name:
        return declared_name, Provenance.DECLARED
    if printed:
        shown = spell(printed)
        return shown, (Provenance.CARD if shown == printed
                       else Provenance.DERIVED)
    return unslug(slug), Provenance.DERIVED


def season_name(config: Config, declared: CompetitionConfig | None,
                season_key: str, cards: Iterable[Card] = ()
                ) -> tuple[str, Provenance]:
    """What this season is called, and how well we know it.

    The competition's own ``season``, then its season's ``[seasons]`` line, then
    the season its cards print; "" where nobody named it, and
    :func:`matchcentre.export.season_label` then shows the key made readable. The
    front page asks it of a section with no card yet, so the order is written once.
    """
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
            home_players=_player_slugs(parsed.home_players, config, warnings,
                                       entry.home),
            away_players=_player_slugs(parsed.away_players, config, warnings,
                                       entry.away),
            status=parsed.status,
            rule=fmt.set_rule,
            # Carried, not derived: the row's own Forfeit ticks are the only thing
            # that says who won a rubber nobody came for, and a rubber nobody came
            # for is 0-0.
            home_forfeit=parsed.home_forfeit,
            away_forfeit=parsed.away_forfeit,
            # Each side's 'Unspecified player' cells, so validate tells a place
            # nobody filled from a name unread (4.448).
            home_unnamed=parsed.home_unnamed,
            away_unnamed=parsed.away_unnamed,
            # And the side that retired, which the games cannot say (4.422).
            home_retired=parsed.home_retired,
            away_retired=parsed.away_retired,
        ))

    for slug in (entry.home, entry.away):
        if slug in unknown:
            warnings.append(unknown[slug])

    return _awarded(Match(
        competition=comp_id,
        round=round_key(card.round),
        home=entry.home,
        away=entry.away,
        fmt=fmt,
        rules=rules,
        date=card.date,
        court=card.court,
        match_id=card.match_id,
        sets=tuple(sets),
        status=_match_status(card, sets),
        # The side the card gives the match up by, a walkover's too: a walkover
        # is the forfeit its TOTAL row awards (4.418).
        forfeited_by=card.forfeit_side,
        disputed=card.disputed,
        home_roster=_player_slugs([e.name for e in card.home_roster], config,
                                  warnings, entry.home),
        away_roster=_player_slugs([e.name for e in card.away_roster], config,
                                  warnings, entry.away),
        ratings=_ratings(card, config, entry.home, entry.away),
        source=where(card),
        warnings=tuple(warnings),
    ))


def _awarded(match: Match) -> Match:
    """*match* with every rubber not played out scored as Match Centre scores it.

    By :func:`~matchcentre.model.rubber_award` under the club's ``forfeit_award``
    and set rule (4.421, 4.422): a rubber ticked Forfeit against one side a love
    set, one a side retired from the other side's, raised to win it, and every
    rubber of a match given up whole a love set to the side that came. Each such
    set keeps the games its row prints as ``printed``, the record of what was
    played. Scored here, once, so every reader of a set's games, the ladder, the
    margins and the players' figures, reads the one scoring.
    """
    gave_up = match.given_up_by
    sets = []
    for result in match.sets:
        award = rubber_award(status=result.status, home_games=result.home_games,
                             away_games=result.away_games,
                             games_to_win=result.rule.games_to_win,
                             award=match.rules.forfeit_award,
                             home_forfeit=result.home_forfeit,
                             away_forfeit=result.away_forfeit,
                             home_retired=result.home_retired,
                             away_retired=result.away_retired,
                             given_up_by=gave_up)
        if award is not None:
            side, home, away = award
            result = replace(result, home_games=home, away_games=away,
                             printed=(result.home_games, result.away_games),
                             awarded_to=side)
        sets.append(result)
    return replace(match, sets=tuple(sets))


def _player_slugs(names: Iterable[str], config: Config, warnings: list[str],
                  team: str = "") -> tuple[str, ...]:
    out: list[str] = []
    for name in names:
        try:
            slug = _slug_player(name, config, team)
        except ValueError:
            warnings.append(f"a player name on this card ({name!r}) has no "
                            f"letters or digits in it and was left out")
            continue
        if slug not in out:
            out.append(slug)
    return tuple(out)


def _ratings(card: Card, config: Config, home: str,
             away: str) -> dict[str, Rating]:
    """Ratings as printed on this card, so a re-rating is captured at its round.

    Each roster block is read against its own team. Reading the two as one list
    would key both sides' rows off the same name, and a declared namesake -- two
    people graded differently under one printed name -- would come out holding
    whichever of the two ratings happened to be read second.
    """
    out: dict[str, Rating] = {}
    for team, roster in ((home, card.home_roster), (away, card.away_roster)):
        for player in roster:
            if player.singles is None and player.doubles is None:
                continue
            try:
                slug = _slug_player(player.name, config, team)
            except ValueError:
                continue
            out[slug] = Rating(singles=player.singles, doubles=player.doubles)
    return out


def _match_status(card: Card, sets: list[SetResult]) -> MatchStatus:
    """The whole-match state, from the card's own whole-match boxes.

    Order matters and follows the card: a forfeited match is a forfeit even if
    somebody also ticked Disputed, and a cancelled match was never played at all.
    ``UNPLAYED`` is reserved for a card with nothing on it a result could be made
    of — no games on any row, and no row that awards its rubber to anybody — which
    is a fixture that has not happened rather than a result of nil-all.

    A rubber the card awards counts as much as a game does. A side that sent nobody
    leaves every row 0-0 with its ``Forfeit`` box ticked, and read off the games
    alone that night was a fixture still to come: sets 4-0 on the page, "recorded as
    unplayed" beside them, and nobody paid for it. Both boxes ticked on a 0-0 row
    award it to nobody, so a card of 0-0 rows ticking both on every one is still
    unplayed rather than a draw paid to two teams that both stayed at home.

    A walkover is a forfeit too, though its card ticks Match Cancelled and no Match
    Forfeited box: :attr:`~matchcentre.card.Card.forfeit_side` asks the box and the
    walkover the reader read (``scorecard.walkover_side``), so a card Match Centre
    writes as a match one side gave up counts, and is not called off.
    """
    if card.forfeit_side:
        return MatchStatus.FORFEIT
    if card.cancelled:
        return MatchStatus.CANCELLED
    if card.disputed:
        return MatchStatus.DISPUTED
    if all_blank(sets):
        return MatchStatus.UNPLAYED
    return MatchStatus.PLAYED


def called_off(card: Card) -> bool:
    """Whether the card's own whole-match boxes call its match off.

    :func:`_match_status` asked of the boxes alone, before a row is read, so a card
    ticking Match Forfeited as well is the forfeit it is read as, and counts, as a
    walkover does (:attr:`~matchcentre.card.Card.forfeit_side`). Such a
    card counts for nothing whatever its rows say, so nothing it prints beside them
    is evidence of how they were read, and :mod:`~matchcentre.validate` holds no
    card called off to its rows. Public, as :func:`filing_day` is, because
    ``tools/doctor.py`` says what the build does with one card's cross-checks.
    """
    return _match_status(card, []) is MatchStatus.CANCELLED


# --- the draw ---------------------------------------------------------------


def _build_draw(comp_id: str, matches: list[Match], *,
                fmt: fmt_mod.MatchFormat, rules: Ruleset,
                teams: dict[str, Team], teams_declared: bool,
                declared: CompetitionConfig | None
                ) -> tuple[dict[str, Round], str, int | None, Provenance,
                           list[str], tuple[str, ...]]:
    """The rounds, where they came from, and how long the season is.

    A declared draw is a schedule: it may describe rounds nobody has played, it
    fixes ``rounds_total``, and its byes are the club's own. A draw read off the
    cards is a history — only rounds that have cards, no future fixtures (F2), and
    ``rounds_total`` stays ``None`` (F3). The predecessor set ``rounds_total`` to
    the number of rounds played, so a ten-round season read 100% complete at round
    three.

    Last, the rounds with cards a declared draw leaves out, when the length that
    draw gave is one its cards have played past: F3 reports those, so they are not
    noted here as well.
    """
    notes: list[str] = []
    played = {m.round for m in matches}
    stated = _stated_byes(declared, teams)

    if declared and declared.rounds:
        rounds: dict[str, Round] = {}
        for rnd in declared.rounds:
            fixtures, listed_twice = dedupe_fixtures(
                (_declared_slug(home, teams), _declared_slug(away, teams))
                for home, away in rnd.fixtures)
            notes.extend(_repeat_notes(comp_id, rnd.number, listed_twice, teams))
            here = [m for m in matches if m.round == rnd.number]
            source = DRAW_DECLARED
            if not fixtures and here:
                # The draw names this round but not its fixtures — a finals round
                # written down before anyone knew who would reach it is the usual
                # reason. The cards do know, so take the fixtures from them and
                # label the round as card-derived, rather than presenting a round
                # that has matches and claims no fixtures.
                #
                # Deduped, and silently: these are cards, and two cards for a
                # fixture is F9's to report, once.
                fixtures, _ = dedupe_fixtures((m.home, m.away) for m in here)
                source = DRAW_FROM_CARDS
            rounds[rnd.number] = Round(
                number=rnd.number,
                date=rnd.date or min((m.date for m in here if m.date),
                                     default=""),
                fixtures=fixtures,
                byes=_byes_for(rnd.number, stated, rnd.byes, fixtures,
                               teams, teams_declared),
                is_finals=rnd.is_finals, finals_typed=rnd.finals,
                note=rnd.note, source=source)
        extra = sorted(played - set(rounds), key=round_sort_key)
        for number in extra:
            # A round with cards that the draw does not mention still has to exist
            # or its matches would hang off nothing. It is marked as coming from
            # the cards, not from the draw.
            in_round = [m for m in matches if m.round == number]
            fixtures, _ = dedupe_fixtures((m.home, m.away) for m in in_round)
            rounds[number] = Round(
                number=number,
                date=min((m.date for m in in_round if m.date), default=""),
                fixtures=fixtures,
                byes=_byes_for(number, stated, (), fixtures,
                               teams, teams_declared),
                is_finals=is_finals_label(number), source=DRAW_FROM_CARDS)
        _add_bye_only_rounds(rounds, stated)
        outgrown: tuple[str, ...] = ()
        if extra and declared.rounds_total is None:
            # The length is the draw's, so the rounds it leaves out are what F3 has
            # to say about a season played past it. The scaffold follows the
            # no-draw branch below: the competition's own rule, before there is one.
            scaffold = Competition(id=comp_id, day="", division="", fmt=fmt,
                                   rules=rules, rounds=rounds,
                                   rounds_total=declared.season_length)
            if scaffold.rounds_past_length(matches):
                outgrown = tuple(extra)
        if extra and not outgrown:
            notes.append(
                f"{comp_id}: cards were found for {rounds_listed(extra)}, "
                f"which the declared draw does not list. They are "
                f"scored, and shown as coming from the cards. Add them under "
                f"[[competitions.{comp_id}.rounds]] if the draw grew.")
        # Declaring the draw declares the season's length, finals left out so the
        # dashboard's progress reads against the regular season. The rule is the
        # config's, shared with the writer that keeps the length when --force drops
        # the draw. Counted here off the rounds built above, a round the draw listed
        # with no fixtures stopped counting once its cards gave it some.
        total = declared.season_length
        return (rounds, DRAW_DECLARED, total,
                Provenance.DECLARED if total is not None else Provenance.DERIVED,
                notes, outgrown)

    # No declared draw. Build the rounds the cards describe, using the
    # competition's own rule about byes: they need a closed team list.
    scaffold = Competition(id=comp_id, day="", division="", fmt=fmt, rules=rules,
                           teams=teams, teams_declared=teams_declared)
    rounds = scaffold.rounds_from_matches(matches)
    for number, rnd in list(rounds.items()):
        byes = _byes_for(number, stated, (), rnd.fixtures, teams, teams_declared)
        if byes != rnd.byes:
            rounds[number] = replace(rnd, byes=byes)
    _add_bye_only_rounds(rounds, stated)
    total = declared.rounds_total if declared else None
    source = Provenance.DECLARED if total is not None else Provenance.DERIVED
    return (rounds, DRAW_FROM_CARDS, total, source, notes, ())


def _stated_byes(declared: CompetitionConfig | None,
                 teams: dict[str, Team]) -> dict[str, tuple[str, ...]]:
    """``[competitions.<id>.byes]`` turned round by round into slugs.

    The club writes it by team, because that is the shape of the sheet an
    association hands out — one row per team, a ``B`` in the weeks it rests. The
    draw needs it by round, so it is transposed here rather than asking anybody to
    transcribe a grid twice.
    """
    if declared is None:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for number, names in declared.byes_by_round().items():
        slugs = tuple(dict.fromkeys(
            slug for slug in (_declared_slug(name, teams) for name in names)
            if slug))
        if slugs:
            out[number] = slugs
    return out


def _byes_for(number: str, stated: dict[str, tuple[str, ...]],
              in_round: tuple[str, ...], fixtures: tuple[tuple[str, str], ...],
              teams: dict[str, Team], teams_declared: bool) -> tuple[str, ...]:
    """Who rests in one round: what the club said, or failing that, who is missing.

    A statement always wins, and a statement about *this* round switches inference
    off for it entirely — the club has answered the question, and adding a team it
    did not name would be second-guessing the answer rather than filling a gap.
    Inference then covers every round the club said nothing about, which is the
    point: a club that writes down the two weeks its draw has byes in should not
    thereby lose the byes this program could already see in the other eight.
    """
    said = tuple(dict.fromkeys(
        [slug for slug in (_declared_slug(name, teams) for name in in_round)
         if slug] + list(stated.get(number, ()))))
    if said:
        return said
    return infer_byes(fixtures, teams, teams_declared=teams_declared)


def _add_bye_only_rounds(rounds: dict[str, Round],
                         stated: dict[str, tuple[str, ...]]) -> None:
    """Rounds that exist because the byes table names them and nothing else does.

    A club transcribing its association's sheet will list the whole-competition
    bye week — the public holiday, the break before finals — and that week has no
    cards and no fixtures at all. It still belongs on the dashboard, so the round
    exists carrying only the rest. F2 is untouched: no fixture is invented for it,
    and :func:`~matchcentre.stats.with_byes` credits nothing for a round nobody has
    played.
    """
    for number in sorted(set(stated) - set(rounds), key=round_sort_key):
        rounds[number] = Round(number=number, byes=stated[number],
                               is_finals=is_finals_label(number),
                               source=DRAW_FROM_CARDS)


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


def _repeat_notes(comp_id: str, number: str,
                  repeats: tuple[tuple[str, str], ...],
                  teams: dict[str, Team]) -> list[str]:
    """A declared round with the same fixture written down twice.

    Said out loud rather than quietly corrected, because a declared draw is the
    club's own statement and this is the one cause of a repeated fixture that
    nothing else would ever mention: two cards for a fixture are already F9, and
    one mistake is reported once. Counted instead of dropped, the repeat is a
    fixture nobody can ever play, so a finished season never reads complete.

    The second cause is quieter than a pasted line and worth naming here, because
    the file looks right: two different spellings of one team that slug to the
    same team turn two fixtures in the draw into one fixture twice.
    """
    if not repeats:
        return []
    named = _listed([
        f"{teams[home].name if home in teams else home} v "
        f"{teams[away].name if away in teams else away}"
        for home, away in repeats])
    one = len(repeats) == 1
    return [f"{comp_id}: round {echoed(number)} of the declared draw lists {named} "
            f"more than once. A fixture is one fixture however many times it is "
            f"written down, so the {'repeat' if one else 'repeats'} "
            f"{'was' if one else 'were'} dropped — counted, "
            f"{'it' if one else 'they'} would be "
            f"{'a fixture' if one else 'fixtures'} nobody can play, and this "
            f"season could never read complete. Remove "
            f"{'it' if one else 'them'} from "
            f"[[competitions.{comp_id}.rounds]]; if you did not write "
            f"{'it' if one else 'them'} twice, two spellings of one team have "
            f"come out as the same team."]


def _shortfall_notes(comp_id: str, rounds: dict[str, Round],
                     matches: list[Match], teams: dict[str, Team],
                     teams_declared: bool,
                     strays: Sequence[FiledElsewhere] = ()) -> list[str]:
    """Rounds that look short of cards, said out loud instead of resolved.

    This is F1's whole point. A round missing a team is far more likely to be a
    scorecard that has not arrived than a team resting, and the predecessor read
    it as a rest: it credited a team that had played and won with its own average
    for a bye, and reported no errors at all.

    Which teams a round leaves off is worked out whether or not the list was
    declared (4.445): with it undeclared a round one card short of a full one leaves
    two teams off and a rest leaves one, and only the one is a bye a checked list
    would credit. A final is not held to the list, since it earns no ladder points
    and its rounds are short by design, and so the fullest round is not a final.

    A round after the last the cards reach, with none of its own, is a round still
    to come and no gap (4.433): its declared byes wait for the night it is played.
    Told apart by later cards, by the rule card-missing asks.

    *strays* are cards of this competition's teams filed under another competition
    (:class:`FiledElsewhere`); a gap one fits says what was read and where it went
    in place of sending the club to find it (4.444).
    """
    notes: list[str] = []
    counts = {number: sum(1 for m in matches if m.round == number)
              for number in rounds}
    if not counts:
        return notes
    busiest = max((count for number, count in counts.items()
                   if not rounds[number].is_finals), default=0)
    awaiting: list[str] = []            # declared rounds with no cards at all

    order = sorted(rounds, key=lambda number: round_sort_key(number, rounds))
    # Two unlike things were reported in one sentence: a round in November nobody
    # has reached, and one the season went past with nothing exported from it. Told
    # apart by other cards and never by the clock -- the same rule, from the same
    # function, that decides `card-missing` in validate.py, because a club reading
    # this note and the dashboard must not be told two different things.
    later = later_round_with_cards(order, {n for n, c in counts.items() if c})
    for number in order:
        rnd = rounds[number]
        absent = _unaccounted(rnd, teams, matches)
        if rnd.source == DRAW_DECLARED:
            notes.extend(_declared_round_notes(comp_id, rnd, matches, teams,
                                               awaiting, strays))
            # A draw typed in as its cards show the round lists no fixture of the
            # teams whose card is missing; with the list declared that is the same
            # gap, and the declared fixtures do not hide it.
            if (teams_declared and counts[number] and not rnd.is_finals
                    and len(absent) >= 2):
                notes.append(_absent_note(
                    comp_id, number, absent, teams, strays, teams_declared=True,
                    step=f"add the fixtures it leaves out to the round under "
                         f"[[competitions.{comp_id}.rounds]]"))
            continue
        if rnd.is_finals:
            continue
        if not (counts[number] or later[number]
                or any(stray.fits(number, absent) for stray in strays)):
            continue                    # a round still to come
        step = (f"declare the round's fixtures under "
                f"[[competitions.{comp_id}.rounds]]")
        if teams_declared:
            if len(absent) >= 2:
                notes.append(_absent_note(comp_id, number, absent, teams, strays,
                                          step=step, teams_declared=True))
        elif counts[number] < busiest and len(absent) >= 2:
            notes.append(_absent_note(
                comp_id, number, absent, teams, strays, step=step,
                teams_declared=False,
                of=f", of the {len(teams)} teams the cards name"))
        elif counts[number] < busiest and absent:
            off = teams[absent[0]].name
            # No file step: the same run's team-list note words that one from the
            # file (4.192), and a second wording of it here would be a remedy told
            # twice.
            notes.append(
                f"{comp_id}: round {echoed(number)} has no card of {off}, one of "
                f"the {len(teams)} teams the cards name. With the team list "
                f"undeclared that stays a gap; if {off} was resting, the bye is "
                f"credited once you have checked the team list and declared it.")

    for passed, wording in (
        (True, "in the declared draw with no cards at all, and there are cards for "
               "later rounds, so the season has moved past {pronoun}. Those are "
               "scorecards to find, not {a_round} nobody has played"),
        (False, "in the declared draw with no cards at all, which is what {a_round} "
                "nobody has played yet looks like. Nothing was scored for "
                "{pronoun}"),
    ):
        listed = [n for n in awaiting if bool(later[n]) is passed]
        if not listed:
            continue
        notes.append(
            f"{comp_id}: {rounds_listed([echoed(n) for n in listed])} "
            f"{by_weeks(listed, 'is', 'are')} "
            + wording.format(pronoun=by_weeks(listed, "it", "them"),
                             a_round=by_weeks(listed, "a round", "rounds")) + ".")
    return notes


def _unaccounted(rnd: Round, teams: dict[str, Team],
                 matches: list[Match]) -> list[str]:
    """The teams of *teams* on no card, fixture or rest of *rnd*, in slug order.

    The cards count as the fixtures do: a declared round's card of a fixture it does
    not list is that card's own finding, and its two teams are not missing.
    """
    present = {slug for pair in rnd.fixtures for slug in pair}
    present.update(rnd.byes)
    present.update(slug for m in matches if m.round == rnd.number
                   for slug in (m.home, m.away))
    return sorted(slug for slug in teams if slug not in present)


def _absent_note(comp_id: str, number: str, absent: list[str],
                 teams: dict[str, Team], strays: Sequence[FiledElsewhere], *,
                 step: str, teams_declared: bool, of: str = "") -> str:
    """Round *number* accounts for none of *absent*, two or more teams.

    A card filed elsewhere of two of them is told by what was read and where it
    went (4.444), and the teams it leaves are what is still to find; one team left
    is the one shape of a rest, which only a statement credits here, since the
    card that would make it the one team off counts where it is filed. A statement
    of a rest is read against a checked list alone: with the list read from the
    cards, a byes table closes it to the teams the table names (F1), and every
    other team on the cards is then an error, so the step asks for the list too.
    """
    head = (f"{comp_id}: round {echoed(number)} accounts for none of "
            f"{in_words(teams[slug].name for slug in absent)}{of}.")
    filed = [stray for stray in strays if stray.fits(number, absent)]
    if not filed:
        return (f"{head} Two or more teams unaccounted for is missing scorecards, "
                f"not a bye, so no bye was credited — find the cards, or {step}.")
    told = " ".join(stray.told(lambda slug: teams[slug].name) for stray in filed)
    left = [slug for slug in absent
            if not any(slug in (stray.home, stray.away) for stray in filed)]
    uncounted = FiledElsewhere.uncounted(filed)
    if not left:
        return f"{head} {told} {uncounted}, and no bye was credited."
    if len(left) == 1:
        off = teams[left[0]].name
        once = ("" if teams_declared
                else " once you have checked the team list and declared it")
        return (f"{head} {told} {uncounted}, and no bye was credited to {off}, the "
                f"one team it leaves on no card. If {off} was resting, its line "
                f"under [competitions.{comp_id}.byes] credits it{once}.")
    return (f"{head} {told} Two or more teams unaccounted for is missing "
            f"scorecards, not a bye, so no bye was credited — find the other "
            f"cards, or {step}.")


def _declared_round_notes(comp_id: str, rnd: Round, matches: list[Match],
                          teams: dict[str, Team], awaiting: list[str],
                          strays: Sequence[FiledElsewhere] = ()
                          ) -> list[str]:
    """What is missing from a round whose fixtures the club declared.

    A declared round needs no inference at all: it says who was meant to play, so
    a gap is a named card that has not arrived. That reads very differently from
    the derived case, where the same gap could be a team resting — and the two
    must not be reported in the same words. Rounds with no cards whatsoever are
    appended to ``awaiting`` for one collective note instead of one apiece, which
    matters for a club that declares ten rounds in August; the caller splits that
    list again, because a round the season went past and a round it has not reached
    are two more things that must not be reported in the same words.

    A missing fixture a card filed under another competition fits (*strays*) is told
    by what was read and where it went, and its round, read after all, is not one
    with no cards (4.444).
    """
    if not rnd.fixtures:
        return [f"{comp_id}: round {echoed(rnd.number)} is declared with no "
                f"fixtures, so nothing can be shown for it beyond its existence. "
                f"Fill in fixtures under [[competitions.{comp_id}.rounds]], or "
                f"leave it and let the cards describe the round when they arrive."]

    # Counted as card-missing counts it, by the one function both ask: sides either
    # way round, and a round whose every card is of a fixture it does not list is
    # a round with cards, not one with none at all.
    counted = round_cards(rnd, matches)
    missing = counted.missing
    if not missing:
        return []
    filed = [stray for stray in strays
             if any(stray.fits(rnd.number, pair) for pair in missing)]
    if not counted.carded and not counted.unlisted and not filed:
        awaiting.append(rnd.number)
        return []

    def name(slug: str) -> str:
        return teams[slug].name if slug in teams else slug

    listed = in_words([f"{name(home)} v {name(away)}" for home, away in missing])
    said = (f"{comp_id}: round {echoed(rnd.number)} has cards for "
            f"{counted.carded} of its {len(rnd.fixtures)} "
            f"declared fixture{'' if len(rnd.fixtures) == 1 else 's'} — {listed} "
            f"{'is' if len(missing) == 1 else 'are'} missing"
            + ("" if counted.carded or not counted.unlisted
               else f", and {unlisted_words(len(counted.unlisted))}")
            + ".")
    if not filed:
        return [said + " The draw says the fixture exists, so that is a card to "
                       "find, not a team resting."]
    told = " ".join(stray.told(name) for stray in filed)
    left = sum(1 for pair in missing
               if not any(stray.fits(rnd.number, pair) for stray in filed))
    if not left:
        return [f"{said} {told} {FiledElsewhere.uncounted(filed)}."]
    return [f"{said} {told} The draw says the "
            f"{'other fixture exists' if left == 1 else 'other fixtures exist'}, "
            f"so {'that is a card' if left == 1 else 'those are cards'} to find, "
            f"not a team resting."]


def _filed_elsewhere(competitions: Sequence[Competition],
                     matches: dict[str, list[Match]],
                     kept: dict[str, list[Card]]
                     ) -> dict[str, tuple[FiledElsewhere, ...]]:
    """By competition, the cards of two of its teams in its rounds filed elsewhere.

    Looked for among the other competitions of the same season key only, since a
    card of another season is not this season's card under any filing, and by the
    two teams and the round label together: the division alone would make every
    card of a genuine second night of one division fit every gap of the first. The
    folder a card sits in is said, as evidence, and never asked, since a folder is
    not a result. Nothing is moved: the card counts where routing filed it. In the
    order the competitions and their matches are built in, which is sorted, so a
    rebuild says them in the same order.
    """
    out: dict[str, tuple[FiledElsewhere, ...]] = {}
    for comp in competitions:
        folders: dict[str, set[Path]] = {}
        for card in kept[comp.id]:
            folders.setdefault(round_key(card.round), set()).add(
                Path(card.source).parent)
        found: list[FiledElsewhere] = []
        for other in competitions:
            if other.id == comp.id or other.season_key != comp.season_key:
                continue
            read = {where(card): card for card in kept[other.id]}
            for match in matches[other.id]:
                if (match.round not in comp.rounds or match.home not in comp.teams
                        or match.away not in comp.teams):
                    continue
                card = read[match.source]
                found.append(FiledElsewhere(
                    round=match.round, home=match.home, away=match.away,
                    source=match.source, filed_under=other.id,
                    why=_why_filed_there(card, comp),
                    beside=Path(card.source).parent in folders.get(match.round, ())))
        if found:
            out[comp.id] = tuple(found)
    return out


def _why_filed_there(card: Card, competition: Competition) -> str:
    """Why routing filed *card* away from *competition*, in the card's own terms.

    The weekday is :func:`filing_day`'s, asked once, as ``tools/doctor.py`` asks
    it, so that a change to which of the date and the printed day files a card
    reaches this sentence without a second edit.
    """
    dated, day = weekday_of(card.date), filing_day(card)
    division = card.division.strip()
    why: list[str] = []
    if not _same(day, competition.day):
        if dated and day == dated:
            why.append(f"it is dated {card.date}, a {day}")
        elif day:
            why.append(f"it is filed by the {day} it prints")
        else:
            why.append("no weekday reads on it")
    if not _same(division, competition.division):
        why.append(f"it prints division {division}" if division
                   else "it prints no division")
    return in_words(why)


def _merged_season_notes(competition: Competition, matches: list[Match], *,
                         config: Config, root: Path) -> list[str]:
    """A round built from cards played in more than one year — the merged archive.

    Single-season mode files every card under the data folder into one competition,
    which is what a club wants right up to the day it keeps last season as well. From
    then on last season's round 1 is filed onto this season's round 1, and what the
    club is shown is either a fixture two cards disagree about — counted for nobody —
    or a team down to play twice in a night, counted twice. Both were already
    reported, by :mod:`matchcentre.validate`, and neither said what a club could act
    on: the cause is one line that is not in ``config.toml``.

    Read off the dates, not the folder names, because the folder names cannot tell
    this apart from the layout every club starts with: ``data/Round1`` and
    ``data/Round2`` are two folders directly under the data folder too, and in
    multi-season mode they would be two seasons. What no single round can be is
    played in two different calendar years, and that is a fact on the cards.
    :func:`~matchcentre.competition.years_played` is that reading, shared with the
    finding that reports the same folder once a fixture in it actually collides: a
    note and an error disagreeing about whether there are two seasons here is worse
    than either being wrong, because one of them says there is nothing to do.

    The other reading of it is allowed for in a sentence, because it is real: a
    summer competition runs from one year into the next, so a club with one season
    can have a round either side of New Year and must not be told its own folder is
    a mistake. Which is also why this is a note and not a finding — nothing here is
    evidence of a wrong figure by itself.

    Every crossed round is named, in the words the sheet's findings name rounds in
    (:func:`~matchcentre.sheet.rounds_listed`). A ten-year archive crosses every
    round it has, so a first draft named three and counted the rest — but a count
    says the same thing about a two round overlap as about a twenty round one, and
    the rounds are what a club looks at its own folders with.

    What replaced what: this stands where a note about duplicate cards used to, one
    that told a club the ladder was "currently double-counting that fixture" and to
    "delete the card that is not the real result". Neither half was true. Two cards
    that agree are counted once and two that disagree are counted for neither
    (:func:`~matchcentre.stats.duplicates`), so nothing is ever counted twice from
    one fixture key; and the run printed that note one screen above the ladder that
    disproved it. F9 in :mod:`matchcentre.validate` already names both files, knows
    which of the three duplicate cases it is, and exits accordingly, so saying it
    here as well was the same mistake reported twice in two voices — with the wrong
    one offering to delete a season.
    """
    if config.multi_season:
        return []
    # Grouped in the order the cards arrive, which is draw order: the caller sorts
    # `matches` by `round_sort_key` thirty lines before handing them here. Sorting
    # again would be the same key applied twice, and the second one is a line no
    # fixture can tell from nothing.
    rounds: dict[str, list[Match]] = {}
    for match in matches:
        rounds.setdefault(match.round, []).append(match)
    years = {rnd: years_played(group) for rnd, group in rounds.items()}
    crossed = [rnd for rnd in years if len(years[rnd]) > 1]
    if not crossed:
        return []
    verb = by_weeks(crossed, "is", "are")
    every = sorted({year for rnd in crossed for year in years[rnd]})
    return [f"{competition.id}: {rounds_listed(crossed)} {verb} built from "
            f"cards played in more than one year ({in_words(every)}), and "
            f"single-season mode puts every card "
            f"under {root} on one ladder. If that is one season that ran across New "
            f"Year, this is right and there is nothing to do. If {root} holds more "
            f"than one season, set seasons = \"multi\" under [build] in {config.path} "
            f"and give each season a folder of its own under {root} — each then has "
            f"its own ladder, and no card has to be deleted to separate them."]


def _season_folder_notes(competitions: list[Competition],
                         matches: dict[str, list[Match]], *, config: Config,
                         root: Path) -> list[str]:
    """Every repeated fixture filed in folders the mode would make seasons — once.

    The other half of :func:`_merged_season_notes`, for the archive the dates cannot
    see: two seasons filed side by side whose cards give no fixture two years, a
    summer and a winter of one year, or last season's exports under this season's
    dates. F9 reports each repeat as a card to delete, and every one of them is a
    card the club needs, because single-season mode is what put the two folders on
    one ladder. Those repeats alone, the ones that ask for a file to go: two seasons
    with different draws also put a team down twice in a round, a finding that
    names no file, and this does not read it.

    A note and not a finding, and weaker than the one about years, because a folder
    is not a result: two folders directly under the data folder are what seasons
    look like and also what rounds look like, so F9 is left saying what it says and
    this only names the line that would separate them, in words a club filing
    rounds can dismiss. Said when every fixture more than one card claims has cards
    in more than one such folder, since then setting the mode separates every one
    of them; a fixture repeated inside one folder, or by a card directly under the
    data folder, is one the mode leaves where it is. Said once for the run, as one
    line of config separates every competition's repeats, and not at all when the
    note about years was said, the same line asked for twice. Multi-season mode
    never reaches it: there each folder is a competition of its own, and a fixture
    with cards in two of them is two fixtures.
    """
    if any(_merged_season_notes(competition, matches[competition.id],
                                config=config, root=root)
           for competition in competitions):
        return []
    repeated = 0
    folders: set[str] = set()
    for competition in competitions:
        these = matches[competition.id]
        for index, others in enumerate(other_cards(these)):
            if not others or others[0] < index:
                continue            # alone on its night, or not the night's first
            night = [these[i].source for i in (index, *others)]
            keys = {season_key_of_path(source, root=root) for source in night}
            if "" in keys or len(keys) < 2:
                return []
            repeated += 1
            folders.update(_season_segment(source, root) for source in night)
    if not repeated:
        return []
    if repeated == 1:
        opening = "The one fixture more than one card claims has"
        reported, stands = "it", "the fixture"
    else:
        opening = f"Every fixture more than one card claims — {repeated} of them — has"
        reported, stands = "each of them", "each fixture"
    return [f"{opening} cards in more than one folder directly under {root} "
            f"({in_words(sorted(folders))}), as seasons filed side by side would. "
            f"Single-season mode puts every card under {root} on one ladder, so F9 "
            f"reports {reported} as cards claiming one fixture. If those folders are "
            f"seasons, set seasons = \"multi\" under [build] in {config.path}: each "
            f"folder directly under {root} is then a season with its own ladder, and "
            f"no card has to be deleted to separate them. If they are not seasons, "
            f"what F9 says about {stands} stands."]


# --- notes ------------------------------------------------------------------


def _global_notes(config: Config, root: Path, club: Club,
                  cards: dict[str, list[Card]], multi: bool,
                  key_origins: dict[str, str], season_keys: dict[str, str]
                  ) -> list[str]:
    notes: list[str] = []
    total = sum(len(page) for page in cards.values())
    if not config.exists and total:
        notes.append(
            f"There is no {config.path} yet, so every name, captain and season "
            f"label below was worked out from the {total} scorecard"
            f"{'' if total == 1 else 's'} themselves. Nothing there can change a "
            f"score — it supplies identity, the team list, the draw and the "
            f"scoring scheme.")
    elif not config.exists:
        # The same two facts for a run with nothing under it, said the other way
        # round because the sentence above is a claim about names, captains and
        # labels that are not there. A run that read no cards worked nothing out,
        # and the report that tells a club otherwise is the one line in it that
        # reads as though the program has lost track of what it just did. What a
        # club with an empty folder does need from this note is that the file it has
        # not written is not the thing standing in its way.
        notes.append(
            f"No scorecard was read, so nothing below was worked out from one. "
            f"{config.path} is where the club name, the team list, the draw and the "
            f"scoring scheme go, and none of it is needed to start: nothing there "
            f"can change a score, and the first card brings its own names with it.")
    keys = sorted({k for k in season_keys.values() if k})
    if multi:
        # Here and below, the demo's own config (config.written_by_demo) is sent no
        # line to type: the demo writes it again on every run, and the line is lost.
        if keys and not config.demo:
            notes.append(
                f"Seasons are the folder names directly under {root}: "
                f"{in_words(keys)}. That folder name is the only thing this "
                f"program reads off a path; give each one a readable label under "
                f"[seasons] in {config.path}.")
        by_date = sorted(comp for comp, origin in key_origins.items()
                         if origin == _FROM_DATE)
        if by_date:
            notes.append(
                f"{in_words(by_date)}: these cards sit directly in {root} rather "
                f"than in a season folder, so the season was taken from the year "
                f"on the card. Move them into a folder named for their season to "
                f"make that certain.")
        no_key = sorted(comp for comp, origin in key_origins.items()
                        if origin == _NO_SEASON)
        if no_key:
            notes.append(
                f"{in_words(no_key)}: no season folder and no readable date, so "
                f"these cards carry no season key at all. In multi-season mode "
                f"that means two seasons of the same day and division would share "
                f"one ladder — file them under a season folder.")
    for comp_id in sorted(config.competitions):
        if comp_id in cards:
            continue
        declared = config.competitions[comp_id]
        wanted = ""
        if declared.can_claim_cards:
            try:
                wanted = competition_id(declared.day, declared.division,
                                        declared.season_key)
            except CompetitionError:
                wanted = ""
        if wanted:
            # Listed, because a competition a club has declared and not played yet
            # is one its readers can wait for, and from outside the page that is
            # indistinguishable from one this tool has never heard of. The id is
            # printed because the other thing that produces this note is a division
            # spelt wrongly, and the spelling is where it shows.
            suffix = (f" It is on the front page with no dashboard of its own. The "
                      f"day and division it names spell out as {wanted!r}.")
        else:
            suffix = (" It is not listed anywhere, because it does not give both a "
                      "day and a division to match on.")
        if wanted and multi and not declared.season_key and keys:
            # Easy to get wrong and silent when you do: in multi-season mode the
            # season key is part of the id, so a section without one matches
            # nothing at all rather than matching every season.
            suffix += (f" In multi-season mode the season is part of the id too, "
                       f"so the section needs season_key — "
                       f"{in_words(keys, 'or')}.")
        notes.append(
            f"{config.path} describes [competitions.{comp_id}] but nothing was "
            f"filed under it, so none of its settings took effect.{suffix}")
    if club.name and not config.club_name and not config.demo:
        notes.append(
            f"The club name {club.name!r} came off the cards' letterhead. Set "
            f"name under [club] in {config.path} to write it the way you would, and "
            f"a line there counts once it has no # in front.")
    return notes


def _namesake_notes(config: Config, club: Club,
                    matches: dict[str, list[Match]]
                    ) -> tuple[list[str], list[str]]:
    """A ``[namesakes]`` entry that separated nobody, which is silent without it.

    The split is applied by rewriting a slug, so an entry that matches no card
    rewrites nothing — and a dashboard built from a line that did nothing looks
    exactly like one built from a line that worked. Two ways to write an entry that
    cannot match, a name no card prints and a team spelt differently from its
    banner, and both of them are one keystroke away from the line
    :mod:`matchcentre.validate` offers a club to paste.

    The third way matches every team and still separates nobody: one team each in
    two different competitions. A merge only ever happens inside one — each grade
    gets a dashboard of its own, with its own players — so one name across two of
    them is two rows already, and declaring it renames both without joining or
    parting anything. Judged on whether *some* competition holds two or more of the
    teams named, not on whether one holds them all: the same name merged in two
    grades needs all four teams in the one entry, since the entry is keyed on the
    name, and that entry does separate people in both.

    Except where the careers page is written, which is every build printing
    names, in either mode (4.430): it joins one name across competitions as well
    as seasons, so there an entry naming a team in each of two competitions gives
    each of two people an entry of their own, and the warning's advice to leave
    it off would merge them. The decider is asked about a build printing names,
    whatever this run prints. A run that withholds them writes no careers page,
    but the warning is advice about a line of the config, and the same config
    built with ``--players names`` writes one. Rejected: the warning for a run
    that withholds names, true of that run's pages and not of the line. Measured
    when it was given, a club that left the line off as told and then built with
    ``--players names`` got one career for two people.

    Whether the entry separated anybody is judged on the records it leaves on one
    page: the name, and the name with each of its teams, two or more of them on a
    competition's page, or on the careers page where one is written. The name alone
    is a record too, a player of a team the entry does not list, so an entry with
    one team left can still have parted two people, and one naming a team in each
    of two grades can have parted two in one of them, where leaving it off, as the
    note once advised, merged them. Missed teams are worded by their count.

    Everyone who reached a set row counts, not only the registered squads: a
    fill-in is never in a roster block, so judging the entry on rosters alone
    would report a split that had in fact taken effect.

    A team whose cards print the name only on one that counts for nothing, or on
    some but not all of the cards of a night that disagree, waits, as a
    ``[player_names]`` line under such a name does: that card registers nobody, and
    that night only the names its cards all print, so the entry has nobody there to
    separate until a card that counts names the player, and the club has nothing to
    check. So does a team a
    ``[competitions]`` section declares and no card this run read prints, as a
    ``[team_names]`` line for it does: there is no banner yet to check it against.
    Measured when it was a warning, ``quarry-quolls`` declared and on no card was
    told on every quiet run to check it against the banner it is printed under.

    Returned as the warnings and the notes. An entry with a team to check or that
    separated nobody is a mistake in the file, printed on every run, ``--quiet``
    included, with the loader's warnings: as a note, the run a club schedules
    dropped the only word about it. An entry whose every team left waits is a note,
    since nothing in it has to change, and a warning would be printed every week.
    An entry with both is one warning, so the one entry is said once.
    """
    if not config.namesakes:
        return [], []
    seen, waiting = _players_named(club, matches)
    uncarded = _teams_declared(config) - _teams_printed(matches)
    held = [set(comp.teams) for comp in club.competitions]
    pages = [_played_in(comp, matches) for comp in club.competitions]
    if careers.writes_careers(config, "names"):
        pages.append(seen)
    warnings: list[str] = []
    notes: list[str] = []
    for slug in sorted(config.namesakes):
        teams = config.namesakes[slug]
        missed = [team for team in teams if f"{slug}-{team}" not in seen]
        waits = [team for team in missed if f"{slug}-{team}" in waiting]
        ahead = [team for team in missed if team in uncarded]
        if waits or ahead:
            missed = [team for team in missed if team not in waits + ahead]
            said: list[str] = []
            if waits:
                said.append(
                    f"In {in_words(waits)}, that name is printed only on a card "
                    f"that counts for nothing so far (a washout, or a night not yet "
                    f"played), or on some but not all of the cards of a night that "
                    f"disagree. A card like that registers nobody, and a night "
                    f"whose cards disagree registers only the names they all print, "
                    f"so the entry waits there until a card that counts names "
                    f"{'that player' if len(waits) == 1 else 'those players'}.")
            if ahead:
                first = ("that team's first card" if len(ahead) == 1
                         else "those teams' first cards")
                said.append(
                    f"No card this run read prints {in_words(ahead)}, which "
                    f"[competitions] declares, so the entry waits there for {first}.")
            wait = " ".join(said)
            check = ""
            if missed:
                check = (f", and no card puts that name in {in_words(missed)}: "
                         f"check {'that team' if len(missed) == 1 else 'each of those'}"
                         f" against the banner it is printed under")
            (warnings if missed else notes).append(
                f"[namesakes] in {config.path} gives {slug} a player of "
                f"their own in {in_words(teams)}{check}. {wait}")
            continue
        made = {slug, *(f"{slug}-{team}" for team in teams)}
        parted = any(len(made & page) > 1 for page in pages)
        if not missed:
            if not parted and not any(len(set(teams) & squad) > 1 for squad in held):
                warnings.append(
                    f"[namesakes] in {config.path} gives {slug} a player of their "
                    f"own in {in_words(teams)}, and no one competition has two of "
                    f"those teams. Only teams of the same competition are ever one "
                    f"record, so this entry renamed that player and separated "
                    f"nobody. Leave it off, or name teams of one competition.")
            continue
        listed = in_words(missed)
        one = len(missed) == 1
        if len(missed) == len(teams):
            warnings.append(
                f"[namesakes] in {config.path} gives {slug} a player of "
                f"their own in {listed}, and no card puts that name in any of "
                f"them, so the entry separated nobody. Check the name against a "
                f"card and each team against the banner it is printed under.")
        elif parted:
            warnings.append(
                f"[namesakes] in {config.path} gives {slug} a player of "
                f"their own in {in_words(teams)}, and no card puts that name in "
                f"{listed}. The rest were still separated; "
                f"{'that one' if one else 'those'} added nothing, so check "
                f"{'it' if one else 'each of those'} against the banner it is "
                f"printed under.")
        else:
            warnings.append(
                f"[namesakes] in {config.path} gives {slug} a player of "
                f"their own in {in_words(teams)}, and no card puts that name in "
                f"{listed}, so the entry separated nobody. Check "
                f"{'that team' if one else 'each of those'} against the banner it "
                f"is printed under.")
    return warnings, notes


def _named_in_sets(matches: Iterable[Match]) -> set[str]:
    """Every player slug a set row names, on a roster or not."""
    return {slug for match in matches for st in match.sets
            for slug in (*st.home_players, *st.away_players)}


def _played_in(comp: Competition, matches: dict[str, list[Match]]) -> set[str]:
    """Every player one competition's page shows: its squads, and all who played."""
    return set(comp.player_index()) | _named_in_sets(matches.get(comp.id, ()))


def _players_named(club: Club, matches: dict[str, list[Match]]
                   ) -> tuple[set[str], set[str]]:
    """Every player the cards built, and every name waiting for a card that counts.

    The players are each registered squad and everyone who played. A fill-in is
    never in a roster block, so asking the rosters alone who the cards reached
    leaves out everybody who only ever appeared in a set row.

    The waiting names are the ones a roster block prints and does not register,
    by :func:`_registered_rosters`: on a card that counts for nothing, or on some
    but not all of the cards of a night that disagree. No player is built from
    them, and a check judging a line by the players alone said no card printed the
    name. A name one of those cards prints in a set row is among the players,
    because every card's set rows are, and a line under it does reach the name
    shown.
    """
    seen: set[str] = set()
    waiting: set[str] = set()
    for comp in club.competitions:
        seen.update(_played_in(comp, matches))
        held = matches.get(comp.id, [])
        waiting.update(slug for match, kept in zip(held, _registered_rosters(held))
                       for side in ("home", "away")
                       for slug in match.roster_for(side) if slug not in kept[side])
    return seen, waiting - seen


def _seasons_made(config: Config, root: Path | None) -> set[str]:
    """Every season the club has made room for: a folder directly under *root*, or
    a ``[competitions.…]`` section's ``season_key``, each read by the folder rule.

    A ``[seasons]`` line alone is not one, because a key a letter out is a line
    alone too: only something else the club made says that the line is not a typo.
    A *root* that cannot be listed makes none: reading the cards has said why.
    """
    made = {season_key_of_folder(comp.season_key)
            for comp in config.competitions.values()}
    if root is not None:
        try:
            made.update(season_key_of_folder(entry.name)
                        for entry in Path(root).iterdir() if entry.is_dir())
        except OSError:
            pass
    made.discard("")
    return made


def _season_label_warnings(config: Config, read: set[str], *, root: Path | None
                           ) -> tuple[list[str], list[tuple[str, str]]]:
    """A ``[seasons]`` season no card this run read is in, or one with two lines.

    Each key is read by the folder rule, as :meth:`Config.season_label` reads it,
    and said once for the season it names, with every key that names it. A key a
    letter out labelled nothing and the run said nothing, so the one season a club
    had named was the one left called by its folder. Only a season no key labels is
    asked about, by the rule ``[player_names]`` is answered by: rejected, every
    season read, which asks a club that has declared next winter whether it meant
    this one, a season that has its own line already. Two keys for one season are
    told which label the pages carry, which never depends on the order of the lines.

    ``(warnings, notes)``. A season still to come, one with its folder or its
    section and no card yet, is no mistake: its one line waits for the season's
    first card, a note tagged with that season, as a ``[player_names]`` line
    waiting for a card that counts is one. A warning would be printed under
    ``--quiet`` every week about a line nobody has to change. The front page heads
    the card of each section it lists by the line already, and the note says so.

    Not judged in single-season mode, where the loader says no label is used; nor
    for the mode written among the labels, which the loader says too; nor when no
    card was read, since a folder with nothing in it yet is no evidence about a key.
    """
    if not config.multi_season or not read:
        return [], []
    keys_of: dict[str, list[str]] = {}
    for key, label in config.season_labels.items():
        if not mode_among_labels(key, label):
            keys_of.setdefault(season_key_of_folder(key), []).append(key)
    made = _seasons_made(config, root)
    out: list[str] = []
    notes: list[tuple[str, str]] = []
    for season in sorted(keys_of):
        keys = sorted(keys_of[season])
        typed = _listed([toml_key(key) for key in keys])
        if season not in read and season not in made:
            advice = did_you_mean(nearest(season, (read | made) - set(keys_of))) or (
                " Check the key against the name of the season's folder.")
            if season:
                advice += (f" If the line is for a season still to come, make its "
                           f"folder, {Path(root or '') / season}/, and the run only "
                           f"notes that the label waits for the season's first card.")
            out.append(f"[seasons] labels {typed}, but no card this run read is filed "
                       f"in a season of that name, so no page shows the label.{advice}")
            continue
        if len(keys) == 1:
            if season not in read:
                # The front page heads a section it lists by the name its season's
                # first card would be given, so the label is on it already. The
                # loader keys a section's season_key the way its folder is.
                heads = [f"[competitions.{comp_id}]"
                         for comp_id, section in sorted(config.competitions.items())
                         if section.can_claim_cards and section.season_key == season
                         and season_name(config, section, section.season_key)[0]
                         == config.season_label(season)]
                waits = (f"for now the label heads only the front page's "
                         f"card{'s' if len(heads) > 1 else ''} for {_listed(heads)}, "
                         f"and the season's pages wait for its first card"
                         if heads else "the label waits for the season's first card")
                notes.append((season,
                    f"[seasons] labels {typed}, a season no card this run read is "
                    f"filed in yet, so {waits}."))
            continue
        if season in keys:
            others = _listed([toml_key(key) for key in keys if key != season])
            settled = (f"The one keyed {season}, as the season is, gives it its "
                       f"label, and {others} can go.")
        elif config.season_label(season):
            settled = (f"They agree on {toml_value(config.season_label(season))}, so "
                       f"that is its label, and one line is enough.")
        else:
            settled = (f"They give it different labels and none is keyed {season}, "
                       f"as the season is, so it has none: keep the one it is for, "
                       f"keyed {season}.")
        out.append(f"[seasons] keys {typed} are one season, {season}, because a key "
                   f"is read the way a folder's name is. {settled}")
    return out, notes


def _team_names_reaching_nobody(config: Config, club: Club,
                                matches: dict[str, list[Match]]) -> list[str]:
    """A ``[team_names]`` line no team is looked up under, said once, with the key.

    A team's name is looked up by its slug, so a key no team has names nobody, and
    a dashboard built from a line that did nothing looks exactly like one built from
    a line that worked. Two ways to write one: a banner a letter out, and the
    left-hand spelling of an ``[aliases]`` line, which is never a team's key because
    a team is looked up after its alias. Each is told the key to use where the cards
    give one: the alias's target, or the nearest team, and only when one team is
    nearest; see :func:`nearest_team`, which never asks about another side of the
    club, since ``riverside-rockets-b`` is the seconds' key and not a typo.

    Judged against every team a card names and every team a ``[competitions]``
    section declares, carded or not, since a team declared and not yet on a card is
    a line waiting for its first one; and not at all when no card was read, which is
    no evidence about any key. A line is judged by every slug the loader filed it
    under (:attr:`Config.team_name_keys`), as typed and, where a prefix is stripped,
    as the team is filed, so it is said once, as typed, and only where neither
    reaches a team. Rejected: stripping the prefix again here, slugged, which judged
    ``north-end-rockets`` under ``strip_team_prefix = "North End"`` a line for
    ``rockets`` when the loader, taking the prefix off only as the setting spells
    it, had filed it under ``north-end-rockets`` and nothing else: the team kept its
    banner and the run said nothing. That team is named, with its line.

    A function of its own and not a table the ``[player_names]`` check is asked
    about: no team key has the branches a player's has, a split, a fill-in, a line
    waiting on a washout, and one function for both made each of those ask which
    table it was judging.
    """
    if not club.competitions:
        return []
    teams = {slug for comp in club.competitions for slug in comp.teams}
    teams |= _teams_printed(matches) | _teams_declared(config)
    try:
        prefix = slugify(config.strip_team_prefix or "")
    except ValueError:
        prefix = ""
    dead: dict[str, str] = {}
    for registered in config.team_name_keys.values():
        if teams.isdisjoint(registered):
            dead[registered[0]] = team_slug(registered[0], strip_prefix=prefix)
    out: list[str] = []
    for key, filed in sorted(dead.items()):
        if filed in teams:
            out.append(
                f"[team_names] names {key}, but strip_team_prefix files that team as "
                f"{filed}, and is taken off a key only where the key spells it as "
                f"the setting does, so the name reaches nobody."
                f"{_team_line(config, key, filed)}")
            continue
        target = apply_alias(filed, config.aliases)
        if target != filed:
            if target in teams:
                advice = _team_line(config, key, target)
            else:
                advice = (f" No card this run read prints {target} either, so there "
                          f"is no line to offer: check the alias against the banner "
                          f"a card prints, unless the team is on none of these cards.")
            out.append(
                f"[team_names] names {key}, but [aliases] folds that team into "
                f"{target}, and a team is looked up after its alias, so the name "
                f"reaches nobody.{advice}")
            continue
        near = nearest_team(filed, sorted(teams))
        if not near:
            advice = (" Check the key against the banner a card prints, unless the "
                      "team is on none of these cards.")
        elif len(near) > 1:
            advice = did_you_mean(near)
        else:
            advice = did_you_mean(near) + _team_line(config, key, near[0])
        out.append(
            f"[team_names] names {key}, but no card this run read prints a team "
            f"{key}, and no [competitions] section declares one, so the name reaches "
            f"nobody.{advice}")
    return out


def _teams_printed(matches: dict[str, list[Match]]) -> set[str]:
    """Every team a card this run read prints, a washout's included."""
    return {slug for page in matches.values() for match in page
            for slug in (match.home, match.away)}


def _teams_declared(config: Config) -> set[str]:
    """Every team a ``[competitions]`` section declares, carded or not."""
    return {slug for declared in config.competitions.values()
            for slug in _closed_list(config, declared)[0]}


def _team_line(config: Config, key: str, to: str) -> str:
    """Where the ``[team_names]`` line under ``key`` belongs: under ``to``, unless
    ``to`` has one. :func:`_rekeyed`'s answer, for the table of team names."""
    if to in config.team_names:
        return f" {to} has a line of its own, so this one can go."
    return (f" Keyed on {to}, the line is "
            f"{toml_key(to)} = {toml_value(config.team_names[key])}.")


def _corrections_reaching_nobody(config: Config, club: Club,
                                 matches: dict[str, list[Match]]
                                 ) -> tuple[list[str], list[str], set[str]]:
    """A ``[player_names]`` line no player is looked up under, said once, with the key.

    A correction is applied by looking a player's slug up in the table, so a key no
    player has does nothing, and a dashboard built from a line that did nothing looks
    exactly like one built from a line that worked. Three ways to write one: a name no
    card prints, a name a letter out, and the left-hand spelling of an ``[aliases]``
    line -- which is never anybody's key, because a name is looked up after its alias.
    Each is told the key to use where the cards give one: the alias's target, or the
    nearest key a player has. Only the nearest, and only when one is nearest; see
    :func:`~matchcentre.slugs.nearest`.

    A line to paste is offered only where the cards say who it is for. Not for an
    alias's target no card names, which may be a player not on these cards or an alias
    spelt wrong; and not for a key spelt from the very name it gives, which was typed
    from the name the club meant rather than copied off a card, so the key nearest it
    is as likely somebody else as the one meant. Pasted, either line would move the
    warning on or rename a stranger.

    Judged against every player the cards built, fill-ins included, since a line under
    a name only a set row prints now names that player. Not judged at all when no card
    was read: a folder with nothing in it yet is no evidence about any key. What
    ``[namesakes]`` builds is left to what reports on it, so the one mistake is not
    said twice: a key of a name and a team to the warning on a team the split missed,
    and a name the split made somebody of to :func:`_corrections_split_away`. A name
    whose split made nobody is only a name, and is judged as one.

    A key meant for a name the split made somebody of, a letter out from it or folded
    into it by an alias, is answered as a line under that name is, with a line for
    each player the split made: that name is printed on the cards and is nobody's key,
    so the key nearest it would be somebody else's, and the name alone reaches nobody.

    Returned as the warnings and the notes. A line under a name only a card that
    counts for nothing prints (:func:`_players_named`) is a note, with no line
    offered: it is not a mistake, and the player's first card that counts is the one
    it reaches, so a warning would be printed under ``--quiet`` every week about a
    line nobody has to change. An alias into such a name, or a key nearest it, is
    answered as for a printed name, so no line is offered a counted player's key
    in its place. Rejected: telling that line the nearest key a player has, which
    renamed somebody else, as the unfixed tree offered Parkview's ``kit-vasse`` for
    a washout's ``kit-vassey``; and registering such a card's roster after all,
    which undoes the rule that a card counting for nothing registers nobody. The
    notes are the run's and not a season's, as the table is: whether a line waits
    is judged over every season's cards, so ``--only`` shows them whatever season
    it names, as it shows the warnings.

    A line keyed right that would give the name the player is shown by already, on
    every page that shows them, is told to delete the line instead: pasted, the
    line offered would give no page a new name. Names are compared as a page shows
    them, whitespace run together, and case kept, since a line is shown as written;
    see :func:`_names_settled`. The third value is every player a warning hands a
    line to, so :func:`_names_folded` does not ask for the same line again.
    Nothing is judged where the config has no line, so a build with none reads
    no page and asks the careers decider nothing about a build printing names.
    """
    if not club.competitions or not config.player_names:
        return [], [], set()
    seen, waiting = _players_named(club, matches)
    settled = _names_settled(config, club, matches)
    made = {config.split_namesake(name, team): name
            for name, teams in config.namesakes.items() for team in teams}
    players_of: dict[str, list[str]] = {}
    for slug, name in sorted(made.items()):
        if slug in seen:
            players_of.setdefault(name, []).append(slug)
    split = set(made) | set(players_of)
    out: list[str] = []
    waits: list[str] = []
    handed: set[str] = set()
    for key in sorted(config.player_names):
        if key in seen or key in split:
            continue
        if key in waiting:
            waits.append(
                f"[player_names] corrects {key}, a name printed only on a card that "
                f"counts for nothing so far (a washout, or a night not yet played), "
                f"or on some but not all of the cards of a night that disagree. A "
                f"card like that registers nobody, and a night whose cards disagree "
                f"registers only the names they all print, so the correction has "
                f"nobody to reach until a card that counts names that player.")
            continue
        target = apply_alias(key, config.aliases)
        if target != key:
            if target in seen or target in waiting or target in config.player_names:
                advice = _rekeyed(config, key, target, seen, settled, handed)
            elif target in players_of:
                advice = _split_into(target, players_of, config.player_names[key])
                handed.update(players_of[target])
            else:
                advice = (f" No card this run read names {target} either, so there is "
                          f"no line to offer: check the alias against the name as a "
                          f"card prints it, unless the player is on none of these "
                          f"cards.")
            out.append(
                f"[player_names] corrects {key}, but [aliases] folds that name into "
                f"{target}, and a name is looked up after its alias, so the "
                f"correction reaches nobody.{advice}")
            continue
        near = nearest(key, sorted(seen | waiting | set(players_of)))
        if not near:
            advice = (" Check the key against the name as a card prints it, unless the "
                      "player is on none of these cards.")
        elif len(near) > 1:
            advice = did_you_mean(near)
        elif _spelt_from(config.player_names[key]) == key:
            advice = (f" Did you mean {near[0]}? The key is spelt from the name the "
                      f"line gives, so it may be for a player on none of these cards "
                      f"instead, and no line is offered.")
        elif near[0] in players_of and near[0] not in seen:
            advice = (f" Did you mean {near[0]}?"
                      f"{_split_into(near[0], players_of, config.player_names[key])}")
            handed.update(players_of[near[0]])
        else:
            advice = (f" Did you mean {near[0]}?"
                      f"{_rekeyed(config, key, near[0], seen, settled, handed)}")
        out.append(
            f"[player_names] corrects {key}, but no card this run read names a player "
            f"{key} in a roster block or a set row, so the correction reaches "
            f"nobody.{advice}")
    return out, waits, handed


def _spelt_from(name: str) -> str:
    """The key a name would be written under, or ``""`` for one with no letters."""
    try:
        return slugify(name)
    except ValueError:
        return ""


def _as_shown(name: str) -> str:
    """A name as the warnings compare one with a page's: whitespace run together,
    and case kept, since a page shows a name in the case it was written."""
    return " ".join(name.split())


def _split_into(name: str, players_of: dict[str, list[str]], value: str) -> str:
    """The lines for a correction meant for a name ``[namesakes]`` made players of."""
    return (f" [namesakes] splits {name} into one player per team. "
            f"{_per_team(players_of[name], value)}")


def _per_team(keys: list[str], value: str) -> str:
    """A correction under a split name, written as a line for each player it made.

    One wording for both warnings that hand those lines over, so a club told twice,
    for two different lines, is told the same thing.
    """
    offered = _listed([f"{toml_key(key)} = {toml_value(value)}" for key in keys])
    return (f"Write it against each player it is for, keyed on the name and the team: "
            f"{offered}. Anyone it is not for keeps the name their card prints.")


def _rekeyed(config: Config, key: str, to: str, seen: set[str],
             settled: dict[str, set[str]], handed: set[str]) -> str:
    """Where the line under ``key`` belongs: under ``to``, unless ``to`` has one,
    or every page showing ``to`` shows the name the line gives already, by
    ``settled``. ``to`` is added to ``handed`` where the line is offered, and only
    there."""
    if to in config.player_names:
        shown = ", which is the name shown" if to in seen else ""
        return f" {to} has a line of its own{shown}, so this one can go."
    value = config.player_names[key]
    name = _as_shown(value)
    if settled.get(to) == {name}:
        return (f" The dashboard already shows {to} as {name}, the name "
                f"the line gives, so delete the line.")
    handed.add(to)
    return (f" Keyed on {to}, the line is "
            f"{toml_key(to)} = {toml_value(value)}.")


def _names_settled(config: Config, club: Club,
                   matches: dict[str, list[Match]]) -> dict[str, set[str]]:
    """Every name each player is shown by, whitespace run together, leaving out a
    registered player any competition shows by a name this program worked out.

    A worked-out name is un-shouted from capitals, or un-slugged where no card
    printed one, and for a registered player ``build.py``'s review offers the very
    ``[player_names]`` line that gives it, to make the guess the club's word: a line
    giving that name is never one that gives no page a new name. A fill-in's is
    kept, since the review lists registered players alone, and the line keyed
    right changed not a byte of the site or the report.
    """
    guessed = {slug for comp in club.competitions
               for slug, player in comp.player_index().items()
               if player.name_source is Provenance.DERIVED}
    out: dict[str, set[str]] = {}
    for page in _pages_shown(config, club, matches):
        for slug, name in page.items():
            if slug not in guessed:
                out.setdefault(slug, set()).add(_as_shown(name))
    return out


def _corrections_split_away(config: Config, club: Club,
                            matches: dict[str, list[Match]]
                            ) -> tuple[list[str], set[str]]:
    """A ``[player_names]`` line under a name ``[namesakes]`` split, said once.

    Each player a split makes is looked up by :meth:`Config.split_namesake`'s key, the
    name and the team, and never by the name alone, so a line under the name is given
    to none of them. None rather than all: the split says they are two people, and a
    name written for one of them is no evidence about the other.

    Judged against the players the cards built and not against the file, because only
    the cards say which of the entry's teams printed the name and whether a team the
    entry leaves out printed it too. That player is still keyed on the name, so the
    line is still theirs, and the warning says so and says to leave it: a warning
    built from the file alone told a club with a misspelt team to replace a line that
    was working with one that matched nobody. A split that reached nobody is
    ``_namesake_notes``'s to report, and a team it missed too, so neither is said here.

    A fill-in is in no roster block, so the players the cards built are the
    registered ones and everyone a set row names, under the team whose side printed
    them. Judged by the registered alone, a split made only of fill-ins had its line
    do nothing, and said nothing.

    Quiet once any player the split made has a line of their own. The club has then
    said who it was for, and the rest keep the name their card prints, which is right
    for a correction meant for one of them. Waiting for all of them would leave the
    club two ways to quiet it: delete a line that may be naming somebody, or restate a
    printed name as a correction, which turns a name read off a card into one the
    club declared.

    The second value is every player the warning hands a line to, so
    :func:`_names_folded` does not ask for the same line again.
    """
    corrected = sorted(set(config.player_names) & set(config.namesakes))
    if not corrected:
        return [], set()
    teams_of: dict[str, set[str]] = {}
    for comp in club.competitions:
        for slug, player in comp.player_index().items():
            teams_of.setdefault(slug, set()).add(player.team)
        for match in matches.get(comp.id, ()):
            for st in match.sets:
                for team, slugs in ((match.home, st.home_players),
                                    (match.away, st.away_players)):
                    for slug in slugs:
                        teams_of.setdefault(slug, set()).add(team)
    out: list[str] = []
    handed: set[str] = set()
    for slug in corrected:
        made = sorted({config.split_namesake(slug, team)
                       for team in config.namesakes[slug]} & teams_of.keys())
        if not made or any(key in config.player_names for key in made):
            continue
        handed.update(made)
        if len(made) == 1:
            given = "is not given to the one player it made"
        else:
            given = (f"is given to {'neither' if len(made) == 2 else 'none of them'}: "
                     f"one line cannot say which of them it is for")
        still = ""
        if slug in teams_of:
            left = sorted(teams_of[slug])
            still = (f" It still names the {slug} of {_listed(left)}, "
                     f"{'a team' if len(left) == 1 else 'teams'} the entry does not "
                     f"list, so leave it where it is.")
        out.append(
            f"[player_names] corrects {slug}, but [namesakes] splits that name into "
            f"one player per team, so the correction {given}. "
            f"{_per_team(made, config.player_names[slug])}{still}")
    return out, handed


def _pages_shown(config: Config, club: Club,
                 matches: dict[str, list[Match]]) -> list[dict[str, str]]:
    """The names the dashboard shows, a page at a time: each competition's, and the
    careers page every build printing names writes, under each player's newest
    name."""
    shown = {comp.id: player_names(matches.get(comp.id, ()), comp)
             for comp in club.competitions}
    pages = list(shown.values())
    if careers.writes_careers(config, "names"):
        newest: dict[str, str] = {}
        for comp in sorted(club.competitions,
                           key=lambda c: (season_order_key(c.season_key), c.id)):
            newest.update(shown[comp.id])
        pages.append(newest)
    return pages


def _teams_printing(matches: dict[str, list[Match]]) -> dict[str, set[str]]:
    """Every player a card this run read names, and the teams whose side names them.

    A roster block's and a set row's alike, off every card, a washout's included:
    an alias is applied to every name a card prints, whether or not it registers
    anybody.
    """
    out: dict[str, set[str]] = {}
    for page in matches.values():
        for match in page:
            for side in ("home", "away"):
                for slug in (*match.roster_for(side), *match.players_on(side)):
                    out.setdefault(slug, set()).add(match.team_for(side))
    return out


def _names_shared(config: Config, club: Club,
                  matches: dict[str, list[Match]]) -> list[str]:
    """A ``[player_names]`` line that gives a player another player's name, said once.

    The line reaches its player, so nothing else says a word about it, and the page
    it makes looks right: two rows under one name. It is what a club writes for a
    misspelling on a card, correcting the record that is spelt wrong rather than
    joining it to the one that is right, and it leaves one person as two records.

    Judged on the names the dashboard shows, a page at a time: each competition's,
    and the careers page, which puts every season and competition on one page under
    each player's newest name. Asked of a build printing names, as
    the namesakes note asks, since it is advice about a line of the config. Two
    records under one name on no one page are shown apart, so the club whole at
    once would call a line wrong that no page shows. A line under any of the
    records that share a name is said, and not a set of records ``[namesakes]``
    made of one name, which share it on purpose. What the same lines did on two
    pages is one sentence, the records of both, so the same pair in two seasons, or
    a player lent to another grade, is said once, and never offered a line on one
    page and told on another that the cards give none.

    The ``[aliases]`` line is offered only when one of them is on a roster anywhere
    in the club, since that one is the record a misspelling is joined to; otherwise
    the cards do not say which spelling is wrong. Nor where any of them is a name
    ``[namesakes]`` splits, or a player it made: an alias is applied before the
    split, so one keyed on a player the split made reaches nobody, and one keyed on
    the name moves every player of it. Names are compared as a page shows them,
    whitespace run together.

    Except where one name is split and no lined record is a player of it: an alias
    keyed on a misspelling of the name is split by each side's team, as the name
    is, so it is offered where every card printing the misspelling prints it for a
    team the entry lists, and the split made each of those teams a player among
    the records that share the name. An alias applies to every card, so a team the
    entry leaves out would get the bare name, the one the split leaves every team
    it does not list, and where one prints it that team is named and no line is
    offered. Where the split made one of those teams no player among them, as where
    that team's every card prints the misspelling, the alias would make the
    misspelling a player of that team's own, still apart from the rest, so no line
    is offered and the player it would make is named.
    """
    if not config.player_names:
        return []
    families = {name: {name, *(config.split_namesake(name, team) for team in teams)}
                for name, teams in config.namesakes.items()}
    pages = _pages_shown(config, club, matches)
    registered = {slug for comp in club.competitions for slug in comp.player_index()}
    printing = _teams_printing(matches)
    groups: dict[frozenset[str], tuple[str, set[str]]] = {}
    for page in pages:
        called: dict[str, list[str]] = {}
        for slug, name in page.items():
            called.setdefault(_as_shown(name), []).append(slug)
        for name, slugs in sorted(called.items()):
            lined = frozenset(slug for slug in slugs if slug in config.player_names)
            if (len(slugs) < 2 or not lined
                    or any(set(slugs) <= family for family in families.values())):
                continue
            groups.setdefault(lined, (name, set()))[1].update(slugs)
    out: list[str] = []
    for lined, (name, together) in groups.items():
        slugs = sorted(together)
        others = [slug for slug in slugs if slug not in lined]
        if others:
            given = (f"gives {_listed(sorted(lined))} the name {name}, which "
                     f"{_listed(others)} already "
                     f"{'has' if len(others) == 1 else 'have'}")
        else:
            given = f"gives {_listed(sorted(lined))} one name, {name}"
        split = sorted(bare for bare, family in families.items() if family & together)
        kept = [slug for slug in slugs if slug in registered]
        # One name split, and none of the lined records a player of it: an alias
        # keyed on the rest is split by each side's team, as the name is.
        bare = split[0] if len(split) == 1 and not lined & families[split[0]] else ""
        wrong = [slug for slug in slugs if bare and slug not in families[bare]]
        teams = sorted({team for slug in wrong for team in printing.get(slug, ())})
        left = [team for team in teams if team not in config.namesakes.get(bare, ())]
        unmade = [team for team in teams
                  if config.split_namesake(bare, team) not in together]
        if teams and not left and not unmade:
            joins = _listed([f"{toml_key(slug)} = {toml_value(bare)}"
                             for slug in wrong])
            join = (f"join them under [aliases] with {joins}, and delete the "
                    f"[player_names] line{'' if len(lined) == 1 else 's'} for "
                    f"{_listed(sorted(lined))}. [namesakes] splits {bare} after "
                    f"[aliases] is applied, and the cards print {_listed(wrong)} "
                    f"only for {_listed(teams)}, "
                    f"{'a team' if len(teams) == 1 else 'teams'} the entry lists, "
                    f"so the alias joins the "
                    f"{'player' if len(teams) == 1 else 'players'} the split made "
                    f"there")
        elif left:
            whose = ("that team's player" if len(left) == 1
                     else "those teams' players")
            join = (f"join them under [aliases], keyed on the wrong spelling as a "
                    f"card prints it. No line is offered: [namesakes] splits "
                    f"{bare} after [aliases] is applied, and a card prints "
                    f"{_listed(wrong)} for {_listed(left)}, "
                    f"{'a team' if len(left) == 1 else 'teams'} the entry does not "
                    f"list, so an alias would make {whose} the {bare} of every team "
                    f"the entry leaves out")
        elif unmade:
            whose = ("that team's player" if len(unmade) == 1
                     else "those teams' players")
            made = _listed([config.split_namesake(bare, team) for team in unmade])
            rest = _listed([slug for slug in slugs if slug in families[bare]])
            join = (f"join them under [aliases], keyed on the wrong spelling as a "
                    f"card prints it. No line is offered: [namesakes] splits "
                    f"{bare} after [aliases] is applied, and a card prints "
                    f"{_listed(wrong)} for {_listed(unmade)}, so an alias would make "
                    f"{whose} {made}, still apart from {rest}")
        elif split:
            join = (f"join them under [aliases], keyed on the wrong spelling as a "
                    f"card prints it. No line is offered: [namesakes] splits "
                    f"{_listed(split)} after [aliases] is applied, so an alias keyed "
                    f"on a player the split made reaches nobody, and one keyed on "
                    f"{_listed(split)} moves every player of "
                    f"{'that name' if len(split) == 1 else 'those names'}")
        elif len(kept) == 1:
            joins = _listed([f"{toml_key(slug)} = {toml_value(kept[0])}"
                             for slug in slugs if slug != kept[0]])
            gone = [slug for slug in sorted(lined) if slug != kept[0]]
            join = f"join them under [aliases] with {joins}"
            if gone:
                join += (f", and delete the [player_names] "
                         f"line{'' if len(gone) == 1 else 's'} for {_listed(gone)}")
        else:
            join = ("join them under [aliases], keyed on the spelling that is "
                    "wrong: the cards do not say which that is, so no line is "
                    "offered")
        out.append(
            f"[player_names] {given}, so the dashboard shows them as separate "
            f"players under that one name. If they are one person, {join}. If "
            f"they are different people, give each a name of their own.")
    return out


def _names_folded(config: Config, club: Club, matches: dict[str, list[Match]],
                  handed: set[str]) -> list[str]:
    """A player shown by a spelling ``[aliases]`` folds away, said once, with the key.

    An alias says who a spelling is, and :func:`_spelling` shows a player by one it
    does not fold wherever a card they are spelt from gives one. Where none does,
    the first folded spelling is kept, since the alias's target un-slugged is a
    name no card printed, and the page shows the spelling the club said was wrong
    with nothing to say so. The line that names them is keyed on the player, and
    its name is left to the club: no card they are spelt from gives it, so a value
    offered would be a guess.

    Judged on the names each page shows (:func:`_pages_shown`), and said once a
    player however many pages show them. A player with a ``[player_names]`` line is
    shown by it and is not judged, and nor is one of ``handed``, the players another
    warning already hands a line to, so the one line is asked for once. Nor is an
    alias keyed on the slug of the very name it is printed by, which folds that
    spelling into itself. A config warning, and not a note, because ``--quiet``
    drops a note, and the weekly run is the one that would never say it.
    """
    if not config.aliases:
        return []
    said: dict[str, tuple[str, str]] = {}
    for page in _pages_shown(config, club, matches):
        for slug, name in sorted(page.items()):
            folded = _spelt_from(name)
            if (folded in config.aliases and slug not in config.player_names
                    and slug not in handed
                    and apply_alias(folded, config.aliases) != folded):
                said.setdefault(slug, (folded, name))
    return [f"[aliases] folds {folded} into {apply_alias(folded, config.aliases)}, "
            f"but the dashboard still shows {slug} as {name}, the spelling the "
            f"alias says is wrong: an alias says who a spelling is, and not how to "
            f"spell them. To show the name as it should read, add a line for "
            f"{slug} under [player_names], giving the name as the club spells it."
            for slug, (folded, name) in sorted(said.items())]


def _assumption_notes(comp: Competition, config: Config,
                      declared: CompetitionConfig | None, key_origin: str,
                      season_source: Provenance, set_rule: fmt_mod.SetRule,
                      matches: list[Match], *,
                      config_text: str | None = None) -> list[str]:
    """What this competition took on trust, and the line to edit to change it.

    *config_text* as :func:`discover` is given it.
    """
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
    elif not config.demo:
        # This note, and each below that is the line to edit, is left out for the
        # demo's own config (config.written_by_demo), which the demo writes again
        # on every run.
        names = [comp.teams[slug].name for slug in sorted(comp.teams)]
        listed = in_words(names)
        # Asked of the file rather than worded here: a file written before any card
        # holds no teams line to remove a # from, and a team list too wide for one
        # line is written commented on all of them, where "remove the #" sent a
        # club to delete one of four and a file that will not load.
        step = teams_instruction(comp.id, names, config.path, config_text)
        notes.append(
            f"{comp.id}: the team list was read from the cards — "
            f"{len(comp.teams)} teams: {listed}. Check it, then {step}. Until "
            f"you do, no bye is ever credited (a team on no card of a round is "
            f"resting only if it is one of the division's, and only a list you "
            f"have checked says so) and a misspelt banner becomes a team of its "
            f"own.")

    guessed = sorted(slug for slug, team in comp.teams.items()
                     if team.name_source is Provenance.DERIVED)
    if guessed and not config.demo:
        shown = _listed([comp.teams[slug].name for slug in guessed])
        notes.append(
            f"{comp.id}: cards shout team names, so {len(guessed)} of them were "
            f"un-shouted into {shown}. [team_names] in {config.path} is where to "
            f"correct any that read wrongly, and a line there counts once it has no "
            f"# in front.")

    from_cards = sorted(
        team.name for team in comp.teams.values()
        if team.captain and team.captain_source is Provenance.CARD)
    # A team whose cards carry more than one (C) already has its own note from
    # _players_of naming the players, so it is left out here rather than told
    # twice in less useful words.
    without = sorted(team.name for team in comp.teams.values()
                     if not team.captain and team.players
                     and not any(p.is_captain for p in team.players))
    # Where to name one, and not "shown as derived": the page takes a (C) mark as a
    # card's word and marks nothing, and a first run writes the marks under that
    # table already, behind a # that is what keeps them the cards'.
    if from_cards and not config.demo:
        notes.append(
            f"{comp.id}: captains for {_listed(from_cards)} came from the (C) "
            f"marks printed on the scorecards. [competitions.{comp.id}.captains] in "
            f"{config.path} is where to name one yourself, and a line there counts "
            f"once it has no # in front.")
    if without:
        notes.append(
            f"{comp.id}: no card that counts prints a (C) for {_listed(without)}, so "
            f"{'that team has' if len(without) == 1 else 'those teams have'} no "
            f"captain; a (C) on a card that counts for nothing, such as a washout, "
            f"names nobody. Nothing was guessed; name "
            f"{'one' if len(without) == 1 else 'them'} under "
            f"[competitions.{comp.id}.captains] if you want "
            f"{'it' if len(without) == 1 else 'them'} shown.")

    if comp.format_declared:
        notes.append(
            f"{comp.id}: the match shape is declared as "
            f"{comp.fmt.describe(sentence=True)}, and every card agrees with it.")
    elif not config.demo:
        notes.append(
            f"{comp.id}: the match shape was read off the cards themselves — "
            f"{comp.fmt.describe(sentence=True)} — from the marker column against "
            f"each set row. Pin it with format or slots under [rules] if you would "
            f"rather a card that loses a row was refused than believed.")

    left = config.rules_left_to_default(declared)
    if left and not config.demo:
        notes.append(
            f"{comp.id}: sets are being scored as {set_rule.describe()} "
            f"({set_rule.name}) and the ladder as {comp.rules.describe()} "
            f"({comp.rules.name}). No scorecard can tell us either of those — "
            f"6-3 looks the same whether the set was played to 6 or to 8 — so "
            f"check them under [rules] in {config.path}. This is asked on every "
            f"run until {in_words(left)} {'is' if len(left) == 1 else 'are'} "
            f"declared there, with no # in front.")

    if comp.rounds_total is None and not config.demo:
        # Counted as the page counts them, where it had been the draw's rounds, and
        # a round the draw lists or the byes table rests everyone in was "played".
        played = rounds_played_words(len(comp.rounds_played(matches)))
        notes.append(
            f"{comp.id}: nobody has said how many rounds the season runs, so the "
            f"dashboard will say \"{played}\" and show no "
            f"percentage. Set rounds_total under {where} once you know it — "
            f"guessing it from the rounds played is how the predecessor read 100% "
            f"complete at round three of ten.")
    elif comp.rounds_total is not None and not comp.rounds_past_length(matches):
        # Past its length, the season is F3's to report, and it says both numbers.
        #
        # Rounds *with cards*, not rounds in the draw: a declared draw usually
        # describes rounds nobody has played yet, and counting those as played is
        # the predecessor's 100%-complete-at-round-three bug wearing a hat.
        #
        # Regular rounds against the length, as the length counts them, and the
        # finals with cards named after them: counted in, a final read as a round
        # past the length, "3 rounds" of a season "declared as 2 rounds long".
        with_cards = comp.rounds_played(matches)
        finals_played = comp.rounds_played(matches, finals=True)
        listed = f" ({in_words(with_cards)})" if with_cards else ""
        also = (f" and {len(finals_played)} finals "
                f"{by_weeks(finals_played, 'round', 'rounds')} "
                f"({in_words(finals_played)})" if finals_played else "")
        notes.append(
            f"{comp.id}: cards were found for {len(with_cards)} regular "
            f"{by_weeks(with_cards, 'round', 'rounds')}{listed}{also}, and the "
            f"season is declared as {comp.rounds_total} "
            f"round{'' if comp.rounds_total == 1 else 's'} long.")

    # F11: a round is finals only if a human said so or the card's own round
    # label names a final. Anything numeric counts towards the ladder. The rounds
    # the club typed finals = true on are said apart from those read off a label,
    # and only the second are offered the setting: the first have used it. Asked
    # of the round, as F11 asks it, and not of the config: typed at all, which
    # among the finals is typed true, since a round typed false is never one.
    typed = {n for n, rnd in comp.rounds.items() if rnd.finals_typed is not None}
    finals = sorted((number for number, rnd in comp.rounds.items()
                     if rnd.is_finals), key=comp.round_key)
    label = ("the round label names a final" if comp.draw_source == DRAW_DECLARED
             else "the round label on the cards names a final")
    for chosen, basis, advice in (
            ([n for n in finals if n in typed], "declared as finals", ""),
            ([n for n in finals if n not in typed], f"read as finals because {label}",
             comp.finals_advice("finals = true or false", "to decide it yourself"))):
        if chosen:
            notes.append(
                f"{comp.id}: {rounds_listed(chosen)} "
                f"{by_weeks(chosen, 'is', 'are')} {basis}, and "
                f"{by_weeks(chosen, 'earns', 'earn')} no ladder points.{advice}")

    misread = sorted({m.source for m in matches
                      if any("cannot happen under" in w for w in m.warnings)})
    if misread:
        one = len(misread) == 1
        notes.append(
            f"{comp.id}: {in_words(misread)} {'records' if one else 'record'} a "
            f"set score that is "
            f"impossible under {set_rule.name}. Either the set rule is wrong for "
            f"this competition or the {'card was' if one else 'cards were'} "
            f"misread; the match warnings name "
            f"the set.")
    return notes
