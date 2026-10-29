"""The season document with every player's name swapped for a label, and checked.

``[build] players = "numbered"`` or ``"initials"``, or ``--players`` for one run,
promises that no file this program writes under the output folder holds a
player's name, the front page included. This module keeps that promise in two
halves, and the build writes no dashboard unless both agree.

The first half rewrites each finished season document. It is a pure function of
the named document, the mode and what the run learned, so the report -- which is
for the person holding the cards, and is composed from the named document -- sees
none of it, and two builds of the same cards write the same bytes. The document is
the stable contract here: the reader, the model and the checks behind it all
change, and the ``season.json`` schema is what the page reads. Every field of it
has a class in :data:`FIELDS`, which says whether it holds a player, free text, a
label the page must print anyway, or the program's own words.

The second half, :func:`refusals`, searches every key and string of every
rewritten document, and every string the front page prints, for every spelling of
every player the run met, a squad line a card left out and quoted included as it
printed it, ignoring case and accents. A hit is a refusal: the build writes no
dashboard, and changes at most the front page's status, to say it failed.
``SECURITY.md`` says what to do with one, and the lines the build prints about it
give each name's shape and never the name.

How a spelling is found in text is :mod:`matchcentre.redact`, shared with
``tools/doctor.py --redact``, so the two promises are kept by one rule.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import redact
from .competition import division_label
from .config import PLAYER_MODES, named as config_named, site_title
from .export import subject_said
from .scorecard import _NOT_IN_A_NAME, _could_be_a_name
from .slugs import apply_alias, slugify

__all__ = ["WITHHELD", "WITHHELD_TEXT", "REFUSED_DETAIL", "AMBIGUOUS",
           "FILE_WITHHELD", "WITHHOLDING", "FIELDS", "Pseudonym", "Secrets",
           "Anonymised", "Refusal", "detail", "classify", "secrets",
           "pseudonyms", "withhold", "anonymise", "places", "front_page",
           "refusals", "withhold_run"]

# `meta.provenance.players` on a withheld page. Not a `Provenance` member: it is not
# a claim about evidence, and the page's "derived" advice, which sends a club to
# [player_names], is wrong on a page that prints no name.
WITHHELD = "withheld"
# A message that named a player in a way no label could stand in for.
WITHHELD_TEXT = "(withheld: it named a player)"
# What a refused card's detail reads. The reader and the filing quote the card's
# own text there, from a card nobody was learned from.
REFUSED_DETAIL = "(withheld: the build's report has it)"
# A spelling that names more than one player, or a player of another competition.
AMBIGUOUS = "a player"
# What stands for a refused card's file name.
FILE_WITHHELD = "(file name withheld)"
# The modes that withhold. Names mode never reaches this module.
WITHHOLDING = tuple(mode for mode in PLAYER_MODES if mode != "names")

_DETAILS = {
    "numbered": "names withheld; each player is shown as their team and a number, "
                "in the order the cards first put them on court",
    "initials": "names withheld; each player is shown by their initials, numbered "
                "where two players share them",
}


def detail(mode: str) -> str:
    """What the page says a label means, in *mode*."""
    return _DETAILS[mode]


# --- what each field of the document is -------------------------------------

PLAYER_SLUG = "player-slug"        # one player's slug
PLAYER_SLUGS = "player-slugs"      # a list of them
PLAYER_NAME = "player-name"        # the name beside a slug in the same object
PLAYER_KEYED = "player-keyed"      # player_names: slug -> name
CAPTAIN = "captain"                # a team's captain, as a name
TEXT = "text"                      # free text: the rule in `withhold`
REFUSED_PATH = "refused-path"      # a refused card's path
REFUSED = "refused-detail"         # a refused card's detail
LABEL = "label"                    # printed anyway; kept, and checked
PROGRAM = "program"                # the program's own values; kept

# The dicts whose keys are data rather than the schema, and what each key is. A place
# in one prints the key's position, never the key.
KEYS = {
    "head_to_head": LABEL,
    "player_names": PLAYER_SLUG,
    "players[].by_slot": PROGRAM,
    "teams[].by_slot": PROGRAM,
}

# Path patterns, `[]` for any list index and `{}` for any key of a dict above.
_CLASSES = {
    PLAYER_SLUG: """
        players[].slug players[].{partners,opponents}[].slug
    """,
    PLAYER_SLUGS: """
        matches[].{home_roster,away_roster,fill_ins}[]
        matches[].sets[].{home_players,away_players}[]
        teams[].players[]
    """,
    PLAYER_NAME: """
        players[].name players[].{partners,opponents}[].name
    """,
    PLAYER_KEYED: "player_names{}",
    CAPTAIN: "teams[].captain",
    TEXT: """
        matches[].warnings[] matches[].source rounds[].note meta.source_dir
        validation.duplicates[].sources[]
        validation.{errors,warnings}[].{message,subject,detail}
        validation.rejected[].reason
        placings.waiting
    """,
    REFUSED_PATH: "validation.rejected[].path",
    REFUSED: "validation.rejected[].detail",
    LABEL: """
        teams[].{name,short,slug,code,home_court}
        matches[].{home,away,home_name,away_name,court,forfeited_by}
        fixtures[].{home,away,home_name,away_name}
        players[].{team,team_name,team_code}
        players[].registered_elsewhere
        meta.{label,club,day,division,competition,season,season_key,season_label}
        meta.leader.slug
        placings.matches[].{higher,lower,first,second} placings.unpaired.slug
        meta.rules.slots[].label matches[].sets[].label rounds[].byes[]
        validation.duplicates[].{home,away}
    """,
    PROGRAM: """
        format
        meta.{cards_rejected,cards_scored,cards_seen,draw_source,generated}
        meta.{ladder_complete,matches_total,rounds_played,rounds_total}
        meta.{ladder_detail,ladder_state,leader,matches_played} meta.leader.value
        meta.ordered_on_average meta.order_stands meta.leader_withheld
        meta.ladder_final
        placings placings.{format,set,after,unpaired,strays[]}
        placings.matches[].{places[],outcome,key,rounds[]}
        placings.unpaired.place
        meta.{seasons_mode,teams_declared,teams_total,version,players}
        meta.players_detail meta.rounds_present[]
        meta.folders_unlisted
        meta.regular_fixtures.{played,total}
        meta.provenance.{captains,draw,format,players,rounds_total,season,teams}
        meta.season_label_from
        meta.rules.{allows_draw,average_unplayed,draws,draws_detail,format}
        meta.rules.{format_detail,min_sets_for_result,scoring,scoring_detail}
        meta.rules.{set_rule,set_rule_detail,unplayed,unplayed_detail}
        meta.rules.unplayed_worth meta.rules.ladder_by[]
        meta.rules.slots[].{doubles,key}
        matches[].{away_games,away_points,away_sets,counts_as_played}
        matches[].{counts_for_ladder,date,decided,decided_by,disputed,home_games}
        matches[].{home_points,home_sets,id,key,margin,not_counted,not_credited}
        matches[].{not_totalled,result,round,status,tied_on_games}
        matches[].other_cards[]
        matches[].{no_rubber_played,printed_away_games,printed_home_games}
        matches[].sets[].{away_games,completed,doubles,home_games,margin,slot}
        matches[].sets[].{status,winner}
        matches[].sets[].{nobody_played,printed_away_games,printed_home_games}
        fixtures[].{card_missing,cards,date,played,round,washed_out} fixtures[].keys[]
        head_to_head{}.{draws,finals,games_high,games_low,played,undecided}
        head_to_head{}.{wins_high,wins_low}
        players[].{available,fill_in_appearances,games_diff,games_lost}
        players[].{games_won,is_captain,longest_win_streak,matches,sets_lost}
        players[].{sets_played,sets_won,team_matches,win_pct}
        players[].by_slot{}.{played,win_pct,won}
        players[].contribution.{games_won,share_pct,team_games_won}
        players[].{doubles,singles}.{games_lost,games_won,lost,played,won}
        players[].{partners,opponents}[].{games_lost,games_won,lost,sets,won}
        players[].rating.{doubles,singles}
        players[].rating_history[].{doubles,round,singles}
        players[].streak.{length,type}
        rounds[].{cards,cards_played,date,is_finals,number,numbered,played,source}
        rounds[].washed_out
        rounds[].keys[]
        teams[].{counted,drawn,games_diff,games_lost,games_won,lost,played}
        teams[].{points,points_average,position,sets_diff,sets_lost,sets_won}
        teams[].win_pct teams[].won teams[].form[]
        teams[].{home,away}.{drawn,games_lost,games_won,lost,played,won}
        teams[].{best_round,worst_round}.{points,round}
        teams[].by_slot{}.{games_lost,games_won,lost,won}
        teams[].contribution_gap.{games,nights_not_credited,rubbers_overfull}
        teams[].contribution_gap.{rubbers_short_a_name,rubbers_with_outsiders}
        teams[].contribution_gap.{share_pct,team_games_won}
        teams[].contribution_gap.rubbers_nobody_played
        teams[].ladder_history[].{points_average,position,round}
        teams[].points_breakdown.{averaged,averaged_matches,earned}
        teams[].streak.{length,type}
        validation.ok validation.own_errors
        validation.{errors,warnings}[].{rule,severity,subject_said}
        validation.rejected[].page validation.duplicates[].round
        validation.rejected[].folder
        validation.duplicates[].match_ids[]
    """,
}


def _expand(pattern: str) -> list[str]:
    """``a.{b,c}.d`` as ``a.b.d`` and ``a.c.d``: the table above, written short."""
    found = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if found is None:
        return [pattern]
    return [expanded for choice in found[1].split(",")
            for expanded in _expand(pattern[:found.start()] + choice
                                    + pattern[found.end():])]


FIELDS: dict[str, str] = {path: cls for cls, patterns in _CLASSES.items()
                          for pattern in patterns.split()
                          for path in _expand(pattern)}


def classify(pattern: str) -> str:
    """The class of the field at *pattern*. Free text for anything not in the table.

    Free text is the safe default: a field the export gains tomorrow goes through
    the rule that swaps or withholds a name, and the guard backs that up. The suite
    holds every field of a real document to the table, so the default is for a
    field nobody has classified yet and not a way to avoid classifying one.
    """
    return FIELDS.get(pattern, TEXT)


def _leaves(value: object, pattern: str = "", place: str = ""
            ) -> Iterator[tuple[str, str, object, bool]]:
    """``(pattern, place, value, is_key)`` for every leaf of *value*, in document order.

    A dynamic dict's keys are leaves too, and its entries are ``{}`` in the pattern
    and ``{index}`` in the place, so a place never prints the key.
    """
    if isinstance(value, Mapping):
        keyed = pattern in KEYS
        for index, (key, item) in enumerate(value.items()):
            if keyed:
                where = f"{place}{{{index}}}"
                yield f"{pattern}{{}}", f"the key of {where}", key, True
                yield from _leaves(item, f"{pattern}{{}}", where)
            else:
                yield from _leaves(item, f"{pattern}.{key}" if pattern else key,
                                   f"{place}.{key}" if place else key)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{pattern}[]", f"{place}[{index}]")
    else:
        yield pattern, place, value, False


def _key_class(pattern: str) -> str:
    return KEYS[pattern[:-2]]


@dataclass(frozen=True)
class _Walk:
    """What the run learns from one named document, found in one walk of it.

    The words the page prints anyway and the labels it prints whole are read off
    the document's labels and the program's own strings, and the spellings and the
    pseudonyms off every player slug in it wherever it sits, so one walk finds all
    three for all four, and not a walk each. Each string is kept once, in the order
    the document first holds it: a team's name is in every match it played.
    """

    season: Mapping
    labels: tuple[str, ...]
    program: tuple[str, ...]
    slugs: tuple[str, ...]


# A competition's id and its season's key: the places that name its folder.
_KEYS = ("meta.competition", "meta.season_key")
_FRONT_KEYS = ("the id of competition ", "the season key of competition ")


def _is_key(place: str, meta: Mapping | None = None) -> bool:
    """Whether *place* is an id or a season's key, in a document or the front page.

    Each names the dashboard's folder and is in its address and the front page's
    data (:func:`matchcentre.site.competition_dir`), and none of the page's words
    is one of them: a club typed the id, or named a folder. So a word of one is
    never a word the page prints anyway, and each is searched for the words of a
    name as well as the whole of one. So is a document's season label where its
    *meta* declares no season, since that is the key unslugged
    (``export.season_label``): ``2026 Winter`` of ``2026-winter``.
    """
    if place == "meta.season_label" and meta is not None:
        return not meta.get("season")
    return place in _KEYS or place.startswith(_FRONT_KEYS)


def _walk(season: Mapping) -> _Walk:
    """*season*'s labels, the program's own strings and every player slug in it."""
    labels: dict[str, None] = {}
    program: dict[str, None] = {}
    slugs: dict[str, None] = {}
    meta = season.get("meta", {})
    for pattern, _, value, is_key in _leaves(season):
        if not isinstance(value, str):
            continue
        cls = _key_class(pattern) if is_key else classify(pattern)
        if cls == LABEL and not _is_key(pattern, meta):
            labels[value] = None
        elif cls == PROGRAM:
            program[value] = None
        elif cls in (PLAYER_SLUG, PLAYER_SLUGS):
            slugs[value] = None
    return _Walk(season=season, labels=tuple(labels), program=tuple(program),
                 slugs=tuple(slugs))


# --- what the run learned ------------------------------------------------------


@dataclass(frozen=True)
class Secrets:
    """Every spelling of every player the run met, and what each one names.

    A player the run met includes a line a card left out of its squads, as a heading
    or as printed too close above one, and quoted in its warning: nobody can say it
    is not somebody.

    Learned once for the run and not per document. The refused cards are the club's
    list and sit in every competition's document, and the front page is shared, so
    a spelling learned in one competition has to be found in all of them.

    ``table`` maps a spelling, folded, to the player's real slug, or to
    :data:`AMBIGUOUS` when it names two, or nobody the documents hold, as such a
    line does. ``spellings`` is what the free-text rule looks for, as written and
    without its accents; ``folded`` is what the guard looks for in folded text.
    ``parts`` are the words of the spellings, two letters or more, that the page
    does not print anyway; ``ordinary`` is the words it does. ``lines`` are such
    lines, folded, as the cards printed them and not trimmed, and their pieces,
    that name no player: a message quoting one is withheld whole, whatever its
    words.
    """

    table: Mapping[str, str]
    spellings: tuple[str, ...]
    folded: tuple[str, ...]
    parts: frozenset[str]
    ordinary: frozenset[str]
    lines: tuple[str, ...]


def _unaccented(text: str) -> str:
    """*text* without its accents, its case kept: ``Benítez`` as ``Benitez``."""
    return unicodedata.normalize("NFC", "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)))


def _ordinary_words(walks: Sequence[_Walk], club, cfg) -> set[str]:
    """The words the pages print whoever played: none of them is ever a secret."""
    words: set[str] = {"player"}
    for constant in (WITHHELD_TEXT, REFUSED_DETAIL, AMBIGUOUS, FILE_WITHHELD,
                     *_DETAILS.values()):
        words.update(redact.words(constant))
    for walk in walks:
        for value in walk.labels:
            words.update(redact.words(value))
        for value in walk.program:
            said = redact.words(value)
            if len(said) == 1:
                words.update(said)
    for place, text in front_page(club, cfg):
        if not _is_key(place):
            words.update(redact.words(text))
    return words


def _printed_whole(walks: Sequence[_Walk], club, cfg) -> set[str]:
    """Every label and front-page string, folded: the page prints each as it is."""
    printed = {redact.fold(text) for _, text in front_page(club, cfg)}
    for walk in walks:
        printed.update(redact.fold(value) for value in walk.labels)
    return printed


def _spellings(walks: Sequence[_Walk], cfg) -> list[tuple[str, str]]:
    """``(spelling, slug)`` for all the documents and the config call a player."""
    pairs: list[tuple[str, str]] = []
    known: set[str] = set()
    for walk in walks:
        season = walk.season
        for slug in walk.slugs:
            known.add(slug)
            pairs.extend([(slug, slug), (slug.replace("-", " "), slug)])
        for slug, name in season.get("player_names", {}).items():
            pairs.append((str(name), slug))
        for player in season.get("players", ()):
            pairs.append((str(player.get("name") or ""), player["slug"]))
            for pair in (*player.get("partners", ()), *player.get("opponents", ())):
                pairs.append((str(pair.get("name") or ""), pair["slug"]))
        for team in season.get("teams", ()):
            captain = team.get("captain")
            if isinstance(captain, str) and captain:
                pairs.append((captain, _captain_of(team, captain, season) or
                              _slug_or(captain, team.get("slug", ""))))
    for slug, name in sorted(cfg.player_names.items()):
        pairs.extend([(slug, slug), (slug.replace("-", " "), slug), (name, slug)])
    # [aliases] folds teams as well as players, and a team's slug learned as a
    # player's would withhold the team. So only a pair whose target is a player some
    # document holds.
    for spelt, target in sorted(cfg.aliases.items()):
        if target in known:
            for slug in (spelt, target):
                pairs.extend([(slug, target), (slug.replace("-", " "), target)])
    # [namesakes] maps a player to the teams that split them; only the player is one.
    for slug in sorted(cfg.namesakes):
        pairs.extend([(slug, slug), (slug.replace("-", " "), slug)])
    for comp_id in sorted(cfg.competitions):
        section = cfg.competitions[comp_id]
        for team, name in sorted(section.captains.items()):
            pairs.append((name, _slug_or(name, team, cfg)))
    # Once each: a name is in the document as often as the player is on the page.
    return list(dict.fromkeys((spelling, slug) for spelling, slug in pairs
                              if spelling))


def _slug_or(name: str, team: str, cfg=None) -> str:
    """The slug a printed name would be given, or the name itself if it has none."""
    try:
        slug = slugify(name)
    except ValueError:
        return name
    if cfg is None:
        return slug
    return cfg.split_namesake(apply_alias(slug, cfg.aliases), team)


def _captain_of(team: Mapping, captain: str, season: Mapping) -> str | None:
    """The one player of *team* whose name its captain line prints, or ``None``.

    By the name and not by ``is_captain``: a captain the config declares outranks
    the (C) a card prints, and the line prints the declared name, while the player
    the card marked keeps the flag.
    """
    named = [player["slug"] for player in season.get("players", ())
             if player.get("team") == team.get("slug")
             and redact.fold(str(player.get("name") or "")) == redact.fold(captain)]
    return named[0] if len(named) == 1 else None


def secrets(seasons: Sequence[Mapping], cfg, club, *,
            dropped: Iterable[str] = ()) -> Secrets:
    """What this run must never write: every spelling, every slug, and their words.

    From the documents and the config, never from the reader's cards, which change
    shape with the reader. Every printed spelling that reaches a document passes
    through the model's slug or name, or is caught by the words rule, and refused
    cards are handled by structure. Except *dropped*: each roster line a card left
    out of its squads and quoted in a warning, which is in no document
    but that warning. Learned last, so a player's spelling keeps its slug, and for
    the guard and the words rule only, since no label can stand in for nobody: a
    message holding one, or a word of one the page does not print, is withheld
    whole. Learned as printed and never trimmed, and not at all when the page
    prints the whole line anyway, as it prints a word of a team's name. So is each
    piece of it (:func:`_pieces`), unless the page prints that whole or it is one
    word the page prints, so that ``Kit Vasse (sub)`` is found in a team called
    ``Kit Vasse Shield``: and then ``Example Alphas (emergencies)`` is found in
    ``Example Alphas Mixed`` too, since nothing in a line tells a team's name from
    a player's.
    """
    return _secrets([_walk(season) for season in seasons], cfg, club,
                    dropped=dropped)


# Where a line is cut into pieces: a bracket or a colon, the characters the reader
# reads no name across, and between them a word with no letter in it.
_CUT = re.compile("[%s]" % re.escape("".join(sorted(_NOT_IN_A_NAME))))


def _pieces(line: str) -> list[str]:
    """What is left of *line* between its cuts, or nothing when it has none.

    A card's reader takes a line holding one for a heading, by the rule this asks
    (:func:`matchcentre.scorecard._could_be_a_name`), so a name printed beside a
    heading's word is whole in one piece: ``Kit Vasse`` of ``Kit Vasse (sub)``,
    ``Sub: Kit Vasse``, ``Kit Vasse [sub]`` or ``Kit Vasse - sub``.
    """
    if _could_be_a_name(line):
        return []
    runs: list[list[str]] = []
    for between in _CUT.split(line):
        runs.append([])
        for word in between.split():
            if _could_be_a_name(word):
                runs[-1].append(word)
            else:
                runs.append([])
    return list(dict.fromkeys(" ".join(run) for run in runs if run))


def _secrets(walks: Sequence[_Walk], cfg, club, *,
             dropped: Iterable[str]) -> Secrets:
    """:func:`secrets`, from documents already walked."""
    ordinary = frozenset(_ordinary_words(walks, club, cfg))
    table: dict[str, str] = {}
    spellings: list[str] = []
    parts: set[str] = set()
    lines: list[str] = []
    # A line that is word for word a team's or a slot's name, say, is the page's own,
    # and so is a piece of one that is, or that is one word the page prints.
    printed = _printed_whole(walks, club, cfg) if dropped else set()
    dropped = [line for line in dropped if redact.fold(line.strip()) not in printed]
    pieces = [piece for line in dropped for piece in _pieces(line)
              if redact.fold(piece) not in printed
              and redact.fold(piece) not in ordinary]
    learned = [*_spellings(walks, cfg), *((line, None) for line in dropped),
               *((piece, None) for piece in pieces)]
    for spelling, slug in learned:
        for candidate in redact.candidates(spelling, ordinary):
            if slug is None:
                # As printed only: trimmed, "Kit Vasse (sub)" is "Kit Vasse (sub",
                # which is nobody's; its piece "Kit Vasse" is learned for itself.
                key = redact.fold(candidate)
                if candidate == spelling.strip() and key not in table:
                    table[key] = AMBIGUOUS
                    lines.append(key)
                continue
            for form in dict.fromkeys((candidate, _unaccented(candidate))):
                key = redact.fold(form)
                table[key] = slug if table.get(key, slug) == slug else AMBIGUOUS
                spellings.append(form)
        for word in redact.words(spelling):
            if sum(ch.isalpha() for ch in word) >= 2 and word not in ordinary:
                parts.add(word)
    return Secrets(table=table, spellings=tuple(sorted(set(spellings))),
                   folded=tuple(sorted(table)), parts=frozenset(parts),
                   ordinary=ordinary, lines=tuple(sorted(lines)))


# --- the labels ------------------------------------------------------------------


@dataclass(frozen=True)
class Pseudonym:
    """What one player is called on a withheld page."""

    slug: str
    label: str
    team: str       # "" for a player with no team in this competition
    number: int

    @property
    def order(self) -> tuple[int, str, int]:
        """Teams by slug, then number; players with no team here last."""
        return (0, self.team, self.number) if self.team else (1, "", self.number)


def _initials(name: str) -> str:
    """``Priya Halloran`` as ``P.H.``; ``""`` for a name with no letter in it.

    One to each word, and to each part of one that a character of Unicode's dash
    punctuation (``Pd``) parts, a hyphen or an en dash, so ``Tui-Mae O'Dowell`` is
    ``T.M.O.``: an apostrophe parts nothing.
    """
    letters = []
    for word in "".join(" " if unicodedata.category(ch) == "Pd" else ch
                        for ch in name).split():
        bare = [ch for ch in unicodedata.normalize("NFKD", word)
                if not unicodedata.combining(ch) and ch.isalpha()]
        if bare:
            letters.append(bare[0].upper())
    return ".".join(letters) + "." if letters else ""


def pseudonyms(season: Mapping, mode: str, found: Secrets) -> dict[str, Pseudonym]:
    """Real slug to pseudonym for every player in *season*, numbered in one walk.

    Each match in the document's order, each set row in slot order, home then away,
    and then the two roster blocks: a player takes the next number of their team
    the first time the walk meets them. Set rows first because slot order is the
    order a team was picked in; the roster block prints in an order nobody here has
    checked is not the alphabet. Then any player the walk did not meet, sorted, and
    then each team's captain, who may be on no card at all.
    """
    return _pseudonyms(_walk(season), mode, found)


def _pseudonyms(walk: _Walk, mode: str, found: Secrets) -> dict[str, Pseudonym]:
    """:func:`pseudonyms`, of a document already walked."""
    season = walk.season
    entries = {player["slug"]: player for player in season.get("players", ())}
    teams = {team["slug"]: team for team in season.get("teams", ())}
    counts: dict[str, int] = {}
    made: dict[str, tuple[str, str, int, str]] = {}

    def visit(slug: object, team: str | None = None, name: str = "") -> None:
        if not isinstance(slug, str) or slug in made:
            return
        entry = entries.get(slug, {})
        team = (entry.get("team") or "") if team is None else team
        side = teams.get(team, {})
        short = str(side.get("short") or side.get("name")
                    or entry.get("team_name") or "") if team else ""
        if not short:
            team = ""
        counts[team] = counts.get(team, 0) + 1
        said = (season.get("player_names", {}).get(slug) or entry.get("name")
                or name)
        made[slug] = (team, short, counts[team], str(said or ""))

    for match in season.get("matches", ()):
        for rubber in match.get("sets", ()):
            for side in ("home_players", "away_players"):
                for slug in rubber.get(side, ()):
                    visit(slug)
        for side in ("home_roster", "away_roster"):
            for slug in match.get(side, ()):
                visit(slug)
    for slug in sorted(set(walk.slugs) - made.keys()):
        visit(slug)
    for team in season.get("teams", ()):
        captain = team.get("captain")
        if isinstance(captain, str) and captain:
            slug = _captain_slug(team, captain, season, found)
            visit(slug, team=team.get("slug", ""), name=captain)

    def numbered(slug: str) -> str:
        team, short, number, _ = made[slug]
        return f"{short} #{number}" if team else f"Player #{number}"

    labels = {slug: numbered(slug) for slug in made}
    if mode == "initials":
        by_initials: dict[str, list[str]] = {}
        for slug, (_, _, _, name) in made.items():
            letters = _initials(name)
            if letters:
                by_initials.setdefault(letters, []).append(slug)
        for letters, holders in by_initials.items():
            for index, slug in enumerate(holders, start=1):
                labels[slug] = f"{letters} {index}" if len(holders) > 1 else letters
    return {slug: Pseudonym(slug=f"{team}-{number}" if team else f"player-{number}",
                            label=labels[slug], team=team, number=number)
            for slug, (team, _, number, _) in made.items()}


def _captain_slug(team: Mapping, captain: str, season: Mapping,
                  found: Secrets) -> str:
    """Whose label a team's captain line prints.

    The player of that team the line names; else the one player the captain's name
    is a spelling of, if they are in this document; else somebody new, a declared
    captain who is on no card, who takes the team's next number under the slug the
    run learned for them, so a message that names them prints the same label.
    """
    marked = _captain_of(team, captain, season)
    if marked is not None:
        return marked
    named = found.table.get(redact.fold(captain))
    if named is not None and named != AMBIGUOUS:
        return named
    return f"captain:{_slug_or(captain, '')}"


# --- the free-text rule ------------------------------------------------------


def _slug_form(text: str) -> bool:
    """Whether a matched spelling is written the way a slug is, and wants one back."""
    return text == text.lower() and "-" in text and not any(ch.isspace()
                                                            for ch in text)


def withhold(text: str, found: Secrets, names: Mapping[str, Pseudonym], *,
             refused: Mapping[str, str] | None = None) -> str:
    """*text* with each player it names named by their label, or withheld whole.

    First each refused card's path and detail, which a finding quotes and nobody
    was learned from, as their withheld forms. Then every spelling, ignoring case:
    one written as a slug becomes this document's pseudo slug, any other its label,
    and a player who is two people or not in this document becomes
    :data:`AMBIGUOUS`. Then, if a word of any spelling is still there -- a surname
    on its own, a name the other way round -- or a line of ``found.lines``, the
    message is withheld whole, because no label can stand in for a name nobody can
    pin to a player.
    """
    if refused:
        literals = sorted(refused, key=lambda s: (-len(s), s))
        text = re.sub("|".join(map(re.escape, literals)),
                      lambda m: refused[m.group()], text)

    def replace(matched: str) -> str:
        who = names.get(found.table.get(redact.fold(matched), AMBIGUOUS))
        if who is None:
            return AMBIGUOUS
        return who.slug if _slug_form(matched) else who.label

    swapped = redact.substitute(text, found.spellings, replace, ignore_case=True)
    if any(word in found.parts for word in redact.words(swapped)):
        return WITHHELD_TEXT
    if found.lines and _holds(swapped, found.lines):
        return WITHHELD_TEXT
    return swapped


# --- the document ----------------------------------------------------------------


@dataclass(frozen=True)
class Anonymised:
    """One withheld document, and what the report says about it.

    ``players`` is how many players were given a label, ``shared`` how many of
    those share their initials and are numbered, and ``withheld`` how many messages
    were withheld whole.
    """

    season: dict
    players: int
    shared: int
    withheld: int


def _withheld_path(path: str, found: Secrets, names: Mapping[str, Pseudonym]) -> str:
    """A refused card's path on the page: its folder, and never its file name."""
    folder = str(PurePosixPath(path).parent)
    if folder in ("", "."):
        return FILE_WITHHELD
    return f"{withhold(folder, found, names)}/{FILE_WITHHELD}"


def _refused_literals(season: Mapping, found: Secrets,
                      names: Mapping[str, Pseudonym]) -> dict[str, str]:
    """Each refused card's path, as written and as ``repr`` writes it; its detail."""
    literals: dict[str, str] = {}
    for card in season.get("validation", {}).get("rejected", ()):
        path = card.get("path")
        if isinstance(path, str) and path:
            shown = _withheld_path(path, found, names)
            literals[path] = shown
            literals[repr(path)[1:-1]] = repr(shown)[1:-1]
        said = card.get("detail")
        if isinstance(said, str) and said:
            literals[said] = REFUSED_DETAIL
    return literals


def anonymise(season: Mapping, *, mode: str, secrets: Secrets) -> Anonymised:
    """*season* as a withheld page carries it. The input is never changed.

    Never changed because the report is composed from it afterwards, and it is for
    the person who has to fix the cards. Every set, dict and sort here is ordered,
    so the output does not depend on the hash seed.
    """
    return _anonymise(_walk(season), mode=mode, secrets=secrets)


def _anonymise(walk: _Walk, *, mode: str, secrets: Secrets) -> Anonymised:
    """:func:`anonymise`, of a document already walked."""
    if mode not in WITHHOLDING:
        raise ValueError(f"{mode!r} withholds nothing; the build does not call "
                         f"this module for it")
    found = secrets
    doc = walk.season
    names = _pseudonyms(walk, mode, found)
    refused = _refused_literals(doc, found, names)
    withheld = 0

    def text(value: str) -> str:
        nonlocal withheld
        out = withhold(value, found, names, refused=refused)
        if out == WITHHELD_TEXT and value != WITHHELD_TEXT:
            withheld += 1
        return out

    def slug_of(slug: object) -> object:
        who = names.get(slug) if isinstance(slug, str) else None
        return who.slug if who is not None else slug

    def order(slug: str) -> tuple:
        who = names.get(slug)
        return who.order if who is not None else (2, slug, 0)

    def rewrite(value, pattern: str):
        if isinstance(value, Mapping):
            if pattern == "player_names":
                return {names[slug].slug: names[slug].label for slug in value
                        if slug in names}
            out = {}
            for key, item in value.items():
                inner = (f"{pattern}{{}}" if pattern in KEYS
                         else f"{pattern}.{key}" if pattern else key)
                out[key] = rewrite(item, inner)
            if pattern in _NAMED and isinstance(value.get("slug"), str):
                who = names.get(value["slug"])
                if who is not None:
                    out["name"] = who.label
            # Asked again of the words the page will print: a message withheld
            # whole no longer begins with the subject it kept.
            if pattern in _FINDINGS and "subject_said" in value:
                out["subject_said"] = subject_said(out)
            return out
        if isinstance(value, (list, tuple)):
            items = list(value)
            if pattern in _PAIRS:
                items.sort(key=lambda pair: (-pair.get("sets", 0),
                                             -pair.get("games_won", 0),
                                             order(pair.get("slug", ""))))
            elif pattern in _ORDERED:
                items.sort(key=order)
            return [rewrite(item, f"{pattern}[]") for item in items]
        cls = classify(pattern)
        if cls in (PLAYER_SLUG, PLAYER_SLUGS):
            return slug_of(value)
        if cls == REFUSED_PATH and isinstance(value, str):
            return refused.get(value, _withheld_path(value, found, names))
        if cls == REFUSED and isinstance(value, str):
            return REFUSED_DETAIL if value else value
        if cls == TEXT and isinstance(value, str):
            return text(value)
        return value

    players = sorted(doc.get("players", ()), key=lambda p: order(p.get("slug", "")))
    teams = doc.get("teams", ())
    captains = {index: _captain_slug(team, team["captain"], doc, found)
                for index, team in enumerate(teams)
                if isinstance(team.get("captain"), str) and team.get("captain")}
    out = {key: rewrite(players if key == "players" else item, key)
           for key, item in doc.items()}
    for index, slug in captains.items():
        out["teams"][index]["captain"] = names[slug].label
    meta = out["meta"]
    out["meta"] = {"players": mode, "players_detail": detail(mode), **meta}
    out["meta"]["provenance"] = {**meta.get("provenance", {}), "players": WITHHELD}
    shared = sum(1 for who in names.values()
                 if mode == "initials" and re.search(r"\. \d+$", who.label))
    return Anonymised(season=out, players=len(names), shared=shared,
                      withheld=withheld)


# Objects whose `name` is the label of their `slug`.
_NAMED = {"players[]", "players[].partners[]", "players[].opponents[]"}
# Lists re-sorted in pseudonym order: the export sorts them by real slug, and an
# order by real slug is an order by the alphabet.
_ORDERED = {"teams[].players", "matches[].fill_ins"}
_PAIRS = {"players[].partners", "players[].opponents"}
_FINDINGS = {"validation.errors[]", "validation.warnings[]"}


# --- the guard -----------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """One player's name found where the build was about to write it.

    ``owner`` is the competition, or ``front page``; ``place`` the first place it
    was found and ``others`` how many more; ``shape`` the matched text's size;
    ``kind`` whether it is a ``label`` the club can rename, a ``front`` page string,
    the config ``file``'s name, which ``--config`` gives, a ``key`` that names a
    folder (:func:`_is_key`), or a ``fault`` in this program. ``card`` is the card
    whose squad line it is, when it is one of those, as a refusal prints it. The
    player is the grouping and never printed.
    """

    owner: str
    place: str
    others: int
    shape: str
    kind: str
    card: str = ""


_KINDS = ("label", "front", "file", "key", "fault")

# Where the front page prints the config file's name, a place of its own kind,
# since the file is renamed and not a line of it.
CONFIG_FILE = "the config file name"


def places(season: Mapping) -> Iterator[tuple[str, str, str]]:
    """``(place, text, class)`` for every string the guard reads in *season*.

    Every dynamic key and every string value, players, free text and labels alike.
    The schema's own keys and the program's own values are not read: they are the
    same whoever played, which the suite proves by building one season under two
    casts of players.
    """
    for pattern, place, value, is_key in _leaves(season):
        cls = _key_class(pattern) if is_key else classify(pattern)
        if cls != PROGRAM and isinstance(value, str):
            yield place, value, cls


def _shown(text: str, search: _Search) -> str:
    """An id as a refusal prints it: itself, or its shape if it holds a name.

    Or a word of one, as an id is searched (:func:`_is_key`), and a card's path too.
    """
    return redact.shape(text) if search.hits(text, words=True) else text


def front_page(club, cfg) -> Iterator[tuple[str, str]]:
    """``(place, text)`` for every string the front page prints from club and config.

    The picker's inputs rather than its output, so the guard does not depend on
    how the front page is drawn. Every competition the club built, every section
    the config declares that a card could be filed under, whether or not one has
    arrived, and the season labels. Not a section giving no day and division: the
    front page lists it nowhere, and its words are no words of the page's
    (:attr:`~matchcentre.config.CompetitionConfig.can_claim_cards`). A division as
    the program names it, ``Division C`` of ``C``, which is how the label it makes
    and the id it makes print it (:func:`matchcentre.competition.division_label`).
    """
    yield "the title", site_title(cfg, club.name)
    yield "the club name", club.name
    for comp in club.competitions:
        yield f"the id of competition {comp.id}", comp.id
        yield f"the label of competition {comp.id}", comp.display_label
        yield f"the day of competition {comp.id}", comp.day
        yield f"the division of competition {comp.id}", division_label(comp.division)
        yield f"the season of competition {comp.id}", comp.season
        yield f"the season key of competition {comp.id}", comp.season_key
    for comp_id in sorted(cfg.competitions):
        section = cfg.competitions[comp_id]
        if not section.can_claim_cards:
            continue
        yield f"the id of competition {comp_id}", comp_id
        yield f"the label of competition {comp_id}", section.label
        yield f"the day of competition {comp_id}", section.day
        yield (f"the division of competition {comp_id}",
               division_label(section.division))
        yield f"the season of competition {comp_id}", section.season
        yield f"the season key of competition {comp_id}", section.season_key
    for key in sorted(cfg.season_labels):
        yield f"the season label {key}", cfg.season_labels[key]
    yield CONFIG_FILE, config_named(cfg.path)


# What parts the words of an id or a folder's name, beyond what parts a sentence's:
# an underscore, which a section's name may hold, and a join of letters and digits,
# as in a folder called 2026Winter.
_JOINED = re.compile(r"_|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")


def _hits(text: str, found: Secrets, *, words: bool = False
          ) -> list[tuple[str, str]]:
    """``(matched, player)`` for every secret in *text*, folded, word-bounded.

    Through :func:`redact.substitute`, the rule the free-text rule swaps by, so the
    guard and the thing it guards cannot disagree about where a name starts. With
    *words*, each word of a name the page does not print anyway, in what is left,
    too, as ``(word, word)``, as the words rule finds it in free text; and *text*
    is read as an id is, its underscores and joins of letters and digits parting
    words, so ``vasse`` is found in ``vasse_cup`` and ``vasse2026``.
    """
    matched: list[tuple[str, str]] = []

    def record(hit: str) -> str:
        matched.append((hit, found.table.get(redact.fold(hit), AMBIGUOUS)))
        return " "

    folded = redact.fold(text)
    if words:
        folded = _JOINED.sub(" ", folded)
    left = redact.substitute(folded, found.folded, record, ignore_case=True)
    if words:
        matched.extend((word, word) for word in redact.words(left)
                       if word in found.parts)
    return matched


def _holds(text: str, lines: Sequence[str]) -> bool:
    """Whether *text* quotes one of *lines*, found as :func:`_hits` finds a name."""
    held: list[str] = []

    def record(hit: str) -> str:
        held.append(hit)
        return hit

    redact.substitute(redact.fold(text), lines, record, ignore_case=True)
    return bool(held)


@dataclass(frozen=True)
class _Search:
    """The guard's search of one run: :func:`_hits` of each string once, and kept.

    A string the run reads again, as a team's name is in every match it played,
    is answered as it was the first time. Kept under the string and whether it is
    read as an id, which is searched for more (:func:`_is_key`), and made for the
    run and dropped with it, so nothing keeps the names it withholds after it.
    """

    found: Secrets
    seen: dict = field(default_factory=dict)   # (text, words) -> its hits

    def hits(self, text: str, *, words: bool = False) -> list[tuple[str, str]]:
        """:func:`_hits` of *text*, searched the first time the run asks."""
        if (text, words) not in self.seen:
            self.seen[(text, words)] = _hits(text, self.found, words=words)
        return self.seen[(text, words)]


def refusals(published: Sequence[Mapping], secrets: Secrets, *, club,
             cfg, named: Sequence[Mapping] = ()) -> list[Refusal]:
    """Every player's name still in what the build was about to write, once each.

    One entry per player and kind for the whole run, at the first place found,
    because one mistake is reported once, and no fault for a player whose name a
    label prints. Labels first, then the front page, the config file's name, the
    keys, then the faults.

    The front page is read first, so that a competition's label, which its own
    document carries in ``meta`` as well, is reported where the club can find it.
    A name that is a squad line a card left out, or a word of one in a key, is given
    the card, the first of *named*, the documents as named, whose note quotes it.
    """
    search = _Search(secrets)
    groups: dict[tuple[str, str], list] = {}

    def add(owner: str, place: str, matched: str, who: str, kind: str) -> None:
        if (who, kind) not in groups:
            groups[(who, kind)] = [owner, place, set(), redact.shape(matched), kind,
                                   _card(matched, named, search)]
        groups[(who, kind)][2].add((owner, place))

    for place, value in front_page(club, cfg):
        key = _is_key(place)
        kind = "key" if key else "file" if place == CONFIG_FILE else "front"
        for matched, who in search.hits(value, words=key):
            add("front page", _shown_place(place, search), matched, who, kind)
    for season in published:
        meta = season.get("meta", {})
        owner = _shown(str(meta.get("competition", "")), search)
        for place, value, cls in places(season):
            key = _is_key(place, meta)
            for matched, who in search.hits(value, words=key):
                add(owner, place, matched, who, _kind(place, cls, key=key))
        for index, team in enumerate(season.get("teams", ())):
            # The pseudo slugs of players with no team are player-1, player-2, and a
            # team's are its slug and a number, so a team called that would share
            # them. Refused rather than renamed: it is not a name, and it is not safe.
            if team.get("slug") == "player":
                add(owner, f"teams[{index}].slug", "player", "teams[].slug", "fault")
    # A name in a team's label is in every player label made from it too, which
    # is the same mistake: renamed, the rerun shows whatever else is left.
    for who, kind in list(groups):
        if kind == "fault" and (who, "label") in groups:
            del groups[(who, kind)]
    ordered = sorted(enumerate(groups.values()),
                     key=lambda item: (_KINDS.index(item[1][4]), item[0]))
    return [Refusal(owner=owner, place=place, others=len(where) - 1, shape=shape,
                    kind=kind, card=card)
            for _, (owner, place, where, shape, kind, card) in ordered]


def _card(matched: str, named: Sequence[Mapping], search: _Search) -> str:
    """The card whose note quotes *matched*, if it is a squad line one left out.

    The first match of *named* with a note holding it, as a refusal prints its
    ``source``, or ``""`` for a name that is no such line, or that no note quotes.
    Or a word of one and of no player's name, as a key is searched (:func:`_hits`).
    """
    found = search.found
    line = redact.fold(matched)
    lines = (line,) if line in found.lines else ()
    if not lines and not any(line in redact.words(spelling)
                             for spelling in found.spellings):
        lines = tuple(held for held in found.lines if line in redact.words(held))
    for season in named if lines else ():
        for match in season.get("matches", ()):
            if any(_holds(str(note), lines) for note in match.get("warnings", ())):
                return _shown(str(match.get("source", "")), search)
    return ""


def _kind(place: str, cls: str, *, key: bool = False) -> str:
    """What a name found at *place* in a document is: a label, the front page's, a fault.

    A document's own ``meta`` labels -- its club, label, day, division, season --
    are the ones the front page prints of it, from the same lines of config.toml,
    so a name in one is the front page's finding and not a second one, and one at a
    *key* the key's (:func:`_is_key`). The one label deeper in ``meta``, a slot's,
    is this program's own, such as Doubles 1.
    """
    if cls != LABEL:
        return "fault"
    if key:
        return "key"
    return "front" if place.startswith("meta.") else "label"


def _shown_place(place: str, search: _Search) -> str:
    """A front-page place with any id in it that holds a name printed as its shape."""
    head, sep, tail = place.rpartition(" ")
    if sep and head.startswith(("the id", "the label", "the day", "the division",
                                "the season")):
        return f"{head} {_shown(tail, search)}"
    return place


# --- the run -------------------------------------------------------------------


def withhold_run(seasons: Sequence[Mapping], cfg, club, *, mode: str,
                 dropped: Iterable[str] = ()
                 ) -> tuple[list[Anonymised], list[Refusal]]:
    """Each of the run's documents withheld, in order, and every name still in them.

    :func:`secrets`, :func:`anonymise` of each document and :func:`refusals`, with
    each named document walked once for the first two, which read the same strings
    of it. The guard walks each rewritten document for itself: what it searches is
    what the build writes, and a walk shared with the rewrite would take the
    rewrite's word for that.
    """
    walks = [_walk(season) for season in seasons]
    found = _secrets(walks, cfg, club, dropped=dropped)
    done = [_anonymise(walk, mode=mode, secrets=found) for walk in walks]
    return done, refusals([one.season for one in done], found, club=club, cfg=cfg,
                          named=[walk.season for walk in walks])
