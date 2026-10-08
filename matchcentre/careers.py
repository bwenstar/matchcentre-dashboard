"""Careers across seasons: each player's season documents, added up.

Nothing here reads a card or a file. The input is this run's season documents,
already built by the same code from the same cards, and the output is one page
body and three counts. Every figure on that page is either a figure a season
document states or the sum of a column of them, so any number a reader doubts
opens, one click away, on the season it came from.

In single-season mode the same page is the club's players this season: one row
per competition a player is named in, added up the same way, in words about
competitions where the page across seasons has words about seasons. Every
function that words the page is told which (*multi*), by site.py, which asks
the config.

Three decisions shape everything below.

**A person is a slug.** The key is the ``slug`` a season document carries, after
``[aliases]`` and ``[namesakes]`` have done their work, and nothing else: no case
folding, no near-miss matching, no second reading of the aliases. That joins one
name across seasons and across grades, and in single-season mode across the
competitions of the season, which the season pages never do, and the page says
so in its own words; two people who share a name are told apart by the club's
settings, the only evidence this tool has. It is also why a build that withholds
names writes no page at all: there the slug is a label counted per season
document, which is one competition's, and one label can be two people
(:func:`writes_careers`).

**Only counts are added.** A sum of counts is a count. A win rate is worked out
again from the added-up sets, never averaged, and is no figure at all where no set
was decided. A rating is the association's figure for one season, and so are the
things that only mean something inside one: streaks, partners, share of the
team's games. Every field of :class:`stats.PlayerStats` is named in exactly one of
the lists below, so a field added later is a decision somebody has to make.

**A missing figure stops the build.** A document that does not say how many sets
were won is not a document that says none, and a career built from a zero there
would be a total with a season behind it that says nothing of the kind.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from . import slugs
from .competition import season_order_key
from .stats import round_half_up, set_win_pct

__all__ = ["ANCHOR_PREFIX", "SUMMED", "RECOMPUTED", "NOT_SUMMED", "NOT_SHOWN",
           "IDENTITY", "CareersError", "SeasonRow", "Career", "Counts",
           "writes_careers", "build", "figure", "total", "name_of",
           "seasons_played", "competitions_played", "season_names", "counts",
           "win_rate_text", "rating_text", "body_html", "COLUMNS", "HEADING",
           "SINGLE_HEADING", "INITIALS", "initial", "heading", "part",
           "index_html", "initial_html"]

ANCHOR_PREFIX = "p-"
# Summed, and the only figures that are. Dotted keys read a nested record.
SUMMED = ("matches", "sets_won", "sets_lost", "games_won", "games_lost",
          "singles.won", "singles.lost", "doubles.won", "doubles.lost")
RECOMPUTED = ("win_pct",)                 # from the summed sets, never copied into a total
NOT_SUMMED = ("rating", "rating_history", "streak", "longest_win_streak",
              "partners", "opponents", "contribution", "available", "team_matches",
              "by_slot")
NOT_SHOWN = ("sets_played", "games_diff", "fill_in_appearances",
             "singles.played", "singles.games_won", "singles.games_lost",
             "doubles.played", "doubles.games_won", "doubles.games_lost")
IDENTITY = ("slug", "name", "team_code", "team", "team_name", "is_captain")

# Each figure's heading, as the season tables head theirs: the label its cell
# prints, and the words its title and its table's key spell the label out in. A
# label that is its own words has no title and no place in the key. One list, so
# a figure is headed alike in every table it is in, and a figure the season's
# players table heads too is headed as it heads it, label and words: ``M``,
# ``Won``, ``Win %``, ``GF`` and ``GA``, so a reader who follows a row back to
# its season finds the same letters over the same number. ``Win %`` and not
# ``Win rate``: a career's row is a players table's row, and both players tables
# head the column ``Win %``. ``Win rate`` is a tile's, and the slot table's,
# whose rate is of one slot's sets and not of all of them. And no label is one the
# season pages give another figure: their ``W`` and ``L`` are matches, so a set
# lost is ``Lost`` here.
COLUMNS = {"seasons_played": ("Seasons played", "Seasons played"),
           "competitions_played": ("Competitions played", "Competitions played"),
           "matches": ("M", "Matches played"),
           "sets_won": ("Won", "Sets won"), "sets_lost": ("Lost", "Sets lost"),
           "win_pct": ("Win %", "Sets won as a percentage of completed sets"),
           "games_won": ("GF", "Games for"), "games_lost": ("GA", "Games against"),
           "singles.won": ("SW", "Singles won"), "singles.lost": ("SL", "Singles lost"),
           "doubles.won": ("DW", "Doubles won"), "doubles.lost": ("DL", "Doubles lost"),
           "rating": ("Rating", "Singles / doubles rating")}
# The columns of a season row and of the total under them, in order. The Season,
# Competition and Team cells come before these and the rating after.
ROW_COLUMNS = ("matches", "sets_won", "sets_lost", "win_pct", "games_won",
               "games_lost", "singles.won", "singles.lost", "doubles.won",
               "doubles.lost")
INDEX_COLUMNS = ("seasons_played", "matches", "sets_won", "sets_lost", "win_pct",
                 "games_won", "games_lost")
# The same list in single-season mode, where every player has one season and
# "Seasons played" would be 1, or 0, against every name: it counts the
# competitions they played in instead, which is what the page adds up there.
SINGLE_INDEX_COLUMNS = ("competitions_played", *INDEX_COLUMNS[1:])

DASH = "—"
_MISSING = object()

_INTRO = (
    "Each player's seasons, added up. Every total is the sum of the rows above "
    "it, and every season opens the page its row was copied from, so any figure "
    "here can be checked against the season it came from. Win rate is worked out "
    "again from the added-up sets, not averaged, and is a dash where no set was "
    "decided. Ratings are shown for each season and never added up, and neither "
    "are the things that only make sense inside one season: streaks, partners, "
    "opponents and share of the team's games. Each season's page has those.",
    "People are matched by the name their season pages print, across seasons and "
    "across grades, so two people who share a name share a list here until the "
    "club's settings tell them apart. Every row names its team and grade. Only "
    "the seasons on the front page are counted.",
    "Listed by name. A season in one grade and a season in another are not the "
    "same tennis, so this is a record of how much somebody played, not a "
    "ranking.",
)
_ONE_SEASON = ("Only one season has results so far, so every total below is that "
               "season's. They start adding up when a second season's cards are "
               "filed.")
_NO_SEASON = "No season built so far has a result in it yet."
# A split index's own lead: it lists names and no figure, so it says where they are.
_INDEX_INTRO = (
    "Each player's seasons are added up on the page for their initial, where "
    "every season opens the page its row was copied from. This page lists the "
    "names only.",
)
_INDEX_CAPTION = "Everyone named in the seasons built so far, by name"

HEADING = "Careers across seasons"

# The page in single-season mode, where the one season is its whole subject and
# what it adds up is each player's competitions: the same three paragraphs, in
# the words that are true there. "This season" and not "at the club": the page
# lists everyone the cards name, fill-ins from other clubs included.
SINGLE_HEADING = "Players this season"
_SINGLE_INTRO = (
    "Each player's competitions this season, added up. Every total is the sum of "
    "the rows above it, and every competition opens the page its row was copied "
    "from, so any figure here can be checked against the competition it came "
    "from. Win rate is worked out again from the added-up sets, not averaged, "
    "and is a dash where no set was decided. Ratings are shown for each "
    "competition and never added up, and neither are the things that only make "
    "sense inside one competition: streaks, partners, opponents and share of the "
    "team's games. Each competition's page has those.",
    "People are matched by the name their competition pages print, across "
    "competitions, so two people who share a name share a list here until the "
    "club's settings tell them apart. Every row names its competition and team. "
    "Only the competitions on the front page are counted.",
    "Listed by name. Matches in one competition and matches in another are not "
    "the same tennis, so this is a record of how much somebody played, not a "
    "ranking.",
)
_ONE_COMPETITION = ("Only one competition has results so far, so every total "
                    "below is that competition's. They start adding up when a "
                    "second competition's cards are filed.")
_NO_COMPETITION = "No competition has a result in it yet."
_SINGLE_INDEX_INTRO = (
    "Each player's competitions are added up on the page for their initial, "
    "where every competition opens the page its row was copied from. This page "
    "lists the names only.",
)

# The initials a careers page too big for one page is split by, one page each,
# in the order the pages are linked. Fixed, and never made from the names a club
# has, so no part of a name reaches a file name; and every one is written, so a
# page for an initial nobody has any more is never left with old figures on it.
INITIALS = (*"abcdefghijklmnopqrstuvwxyz", "0-9", "other")
_LATIN = frozenset("abcdefghijklmnopqrstuvwxyz")
_DIGITS = frozenset("0123456789")
_SPLIT = ("There are too many careers here for one page, so each initial has a "
          "page of its own. An accent is set aside, so a name starting with É "
          "is under E; one starting with any other letter, such as Ø or "
          "Ł, is under Other. Every name below opens theirs.")
_SINGLE_SPLIT = _SPLIT.replace("too many careers", "too many players")
# On a split index only. A season page links careers.html#p-<slug>, which lands
# on that name's row here, and this follows the row's own link on to the career.
# Python decided which page the career is on and wrote it into the row; this only
# reads it back, so app.js never learns the rule. With scripts off the reader is
# on the row, one click from the career.
_FORWARD = ("<script>\n"
            "(function () {\n"
            "  var id = location.hash.slice(1);\n"
            f"  var row = id.indexOf('{ANCHOR_PREFIX}') === 0 ? "
            "document.getElementById(id) : null;\n"
            "  var link = row ? row.getElementsByTagName('a')[0] : null;\n"
            "  if (link) location.replace(link.href);\n"
            "}());\n"
            "</script>")


class _Words(NamedTuple):
    """Everything a careers page says that depends on the mode, in one mode."""
    heading: str
    intro: tuple[str, ...]
    one: str                  # under the intro, while one part has a result
    none: str                 # in place of the list, while no part has one
    index_intro: tuple[str, ...]
    split: str
    named_in: str             # what the list is the names of
    listed: str               # the list's caption
    columns: tuple[str, ...]  # the list's figures, keys of COLUMNS
    by: str                   # how a career's caption says its rows run
    part: str                 # what one row is, and opens
    total: str                # the row adding them up
    initials: str             # the list of a split's pages, as it is announced


# The two pages, keyed by ``multi``. One table, so no sentence of the page can be
# worded for one mode and left in the other's words. The report's lines about the
# page take its word for a row from here too (`part`).
_WORDS = {
    True: _Words(heading=HEADING, intro=_INTRO, one=_ONE_SEASON, none=_NO_SEASON,
                 index_intro=_INDEX_INTRO, split=_SPLIT,
                 named_in="the seasons built so far", listed=_INDEX_CAPTION,
                 columns=INDEX_COLUMNS,
                 by="season by season, oldest first", part="season",
                 total="All seasons", initials="Careers by initial"),
    False: _Words(heading=SINGLE_HEADING, intro=_SINGLE_INTRO, one=_ONE_COMPETITION,
                  none=_NO_COMPETITION, index_intro=_SINGLE_INDEX_INTRO,
                  split=_SINGLE_SPLIT, named_in="this season's competitions",
                  listed="Everyone named in this season's competitions, by name",
                  columns=SINGLE_INDEX_COLUMNS, by="competition by competition",
                  part="competition", total="All competitions",
                  initials="Players by initial"),
}


class CareersError(ValueError):
    """A season document without a figure the careers page adds up."""


@dataclass(frozen=True)
class SeasonRow:
    season_key: str
    season_name: str         # season_names(documents)[competition]
    competition: str         # meta.competition (the id, unique across a multi site)
    competition_label: str   # meta.label
    team_name: str | None
    name: str                # as that season's document prints it
    figures: Mapping[str, int]                 # every SUMMED key, read from the document
    rating: tuple[float | None, float | None]  # (rating["singles"], rating["doubles"])


@dataclass(frozen=True)
class Career:
    slug: str
    name: str                     # name_of(rows)
    rows: tuple[SeasonRow, ...]   # oldest first
    total: Mapping[str, int]      # total(rows)
    seasons_played: int           # seasons_played(rows)
    competitions_played: int      # competitions_played(rows)


class Counts(NamedTuple):
    """The report's numbers, over seasons or, in single-season mode, competitions."""
    players: int       # careers that played in one or more
    spanning: int      # careers that played in two or more
    with_results: int  # distinct seasons (or competitions) with a match in them


def writes_careers(config, players: str) -> bool:
    """Whether this build publishes a careers page: every build that prints names.

    The one place the question is answered. The site write, its refusal, the
    front-page link, the report line and the left-behind scan all ask it here, each
    handing over how this run prints players (``names``, ``numbered`` or
    ``initials``, as ``build._players_mode`` resolved it once), so no two of them
    can disagree about a given build. The namesakes note asks it too, about a build
    printing names, since it advises on a line of the config and not on this run.
    Every build that prints names, and not from the second season or the second
    competition, because every player page links its player's anchor here, and a
    link that sometimes lands on nothing would be the thing a page had to decide.
    In either mode: in single-season mode the page is the club's players this
    season, one row per competition, which no competition's page adds up.
    *config* is no longer read, and is still taken, so that if the answer ever
    turns on a setting again it is asked here and nowhere else.

    Never a build that withholds names. A person here is a slug, and a withheld
    slug is a label counted per season document, which is one competition's:
    Brindle Magpies #3 this season can be somebody else next, and Brindle Magpies
    #3 on Monday nights somebody else on Tuesdays, so a career joined on it adds
    up two people. Joining the real slugs under a label of this page's own is no
    better: it could match no competition's page, and it would publish a link
    between competitions that no withheld page makes.
    """
    return players == "names"


def build(documents: Sequence[dict]) -> list[Career]:
    """Who is one person, and which season rows are theirs.

    One row per document a slug appears in, roster-only rows included, so that
    every player a season page links here has an anchor to land on.
    """
    names = season_names(documents)
    found: dict[str, list[SeasonRow]] = {}
    for document in documents:
        meta = document["meta"]
        competition = meta["competition"]
        for player in document["players"]:
            slug = player.get("slug")
            if not isinstance(slug, str) or not slug:
                raise CareersError(f"{competition}: a player with no slug, so no "
                                   f"career to add their figures to")
            rating = player.get("rating") or {}
            found.setdefault(slug, []).append(SeasonRow(
                season_key=meta.get("season_key") or "",
                season_name=names[competition],
                competition=competition,
                competition_label=meta.get("label") or competition,
                team_name=player.get("team_name"),
                name=player.get("name") or slug,
                figures={key: figure(player, key, competition=competition)
                         for key in SUMMED},
                rating=(rating.get("singles"), rating.get("doubles"))))
    careers = []
    for slug, rows in found.items():
        rows.sort(key=lambda row: (season_order_key(row.season_key),
                                   row.competition))
        careers.append(Career(slug=slug, name=name_of(rows), rows=tuple(rows),
                              total=total(rows),
                              seasons_played=seasons_played(rows),
                              competitions_played=competitions_played(rows)))
    careers.sort(key=lambda career: (*_filed(career.name), career.slug))
    return careers


def figure(player: Mapping, key: str, *, competition: str = "") -> int:
    """One count off a season document's player, a dotted key reading a record."""
    value = player
    for part in key.split("."):
        value = value.get(part, _MISSING) if isinstance(value, Mapping) else _MISSING
    if type(value) is not int:
        said = "no such figure" if value is _MISSING else f"{value!r}, not a count"
        raise CareersError(f"{competition}: {player.get('slug')}'s {key} is "
                           f"{said}; the careers page adds only the counts every "
                           f"season document states")
    return value


def total(rows: Sequence[SeasonRow]) -> dict[str, int]:
    """The only place a career figure is made."""
    return {key: sum(row.figures[key] for row in rows) for key in SUMMED}


def name_of(rows: Sequence[SeasonRow]) -> str:
    """The newest season's spelling; within it, the last competition's."""
    return rows[-1].name


def seasons_played(rows: Sequence[SeasonRow]) -> int:
    """Seasons with a match in them. Being on a roster is not playing."""
    return len({row.season_key for row in rows if row.figures["matches"] > 0})


def competitions_played(rows: Sequence[SeasonRow]) -> int:
    """Competitions with a match in them: what single-season mode counts instead.

    By id, which is unique across a site, so one grade in two seasons is two.
    """
    return len({row.competition for row in rows if row.figures["matches"] > 0})


def season_names(documents: Sequence[dict]) -> dict[str, str]:
    """The Season cell of each competition, keyed by its id.

    The season's label, with its key added only where one label is shown for two
    seasons -- two identical cells would be two rows nobody could tell apart.
    """
    shown: dict[str, tuple[str, str]] = {}
    keys_for: dict[str, set[str]] = {}
    for document in documents:
        meta = document["meta"]
        key = meta.get("season_key") or ""
        label = (meta.get("season_label") or "").strip() or key
        shown[meta["competition"]] = (key, label)
        if key:
            keys_for.setdefault(label, set()).add(key)
    names = {}
    for competition, (key, label) in shown.items():
        if not key:
            names[competition] = "(no season folder)"
        elif len(keys_for[label]) > 1:
            names[competition] = f"{label} ({key})"
        else:
            names[competition] = label
    return names


def counts(careers: Sequence[Career], *, multi: bool = True) -> Counts:
    """The report's numbers, and which paragraph the page's head adds.

    Over seasons, or in single-season mode over competitions, where every career
    is one season and "played in more than one" would be nobody.
    """
    played = [career.seasons_played if multi else career.competitions_played
              for career in careers]
    return Counts(
        players=sum(1 for each in played if each >= 1),
        spanning=sum(1 for each in played if each >= 2),
        with_results=len({row.season_key if multi else row.competition
                          for career in careers for row in career.rows
                          if row.figures["matches"] > 0}))


def initial(name: str) -> str:
    """Which initial's page a career is on: the first letter or digit its name prints.

    Folded as a slug is folded, case and accents set aside, so ``Émile`` is
    under E and a full-width ``Ｗ`` under W. A digit from 0 to 9 is under ``0-9``,
    and any other letter or digit -- one that is not Latin, or a Latin one with
    no accent to take off, such as Ø or Ł -- is under ``other``, as is a name
    with no letter at all. Punctuation in front of the name is passed over.

    The printed name and never the slug, although the slug is folded already:
    ``Łuka`` slugs to ``uka`` and ``李 Wren`` to ``wren``, so the slug would file
    both under a letter their names do not start with, on a page nobody would
    look for them on.
    """
    return INITIALS[_filed(name)[0]]


def _filed(name: str) -> tuple[int, str]:
    """Where a name goes in the careers list: its initial's place, then the rest.

    The place in :data:`INITIALS` of the page :func:`initial` files it on, then
    the name folded from the character that chose that page, so the list is in
    the order of the pages and, on each, alphabetical from its initial. Sorting
    on the folded name alone put ``'Ofa Renn``, filed under O, before ``Adam``,
    and ``4 Tien``, the last page but one, before every letter: a split index
    listed its names in an order its own links did not follow.
    """
    folded = slugs.fold(name)
    for at, char in enumerate(folded):
        if char in _LATIN:
            return INITIALS.index(char), folded[at:]
        if char in _DIGITS:
            return INITIALS.index("0-9"), folded[at:]
        if char.isalnum():
            return INITIALS.index("other"), folded[at:]
    return INITIALS.index("other"), folded


def heading(key: str | None = None, *, multi: bool = True) -> str:
    """A careers page's heading, and the start of its title: the index's or an initial's."""
    said = _WORDS[multi].heading
    return said if key is None else f"{said}: {_named(key, 'heading')}"


def part(multi: bool = True) -> str:
    """What one row of a career is: a season, or in single-season mode a competition.

    Asked by the report's careers field and by the reasons it gives for a page it
    left behind, so the word they count in is the word the page itself says, out
    of the one table.
    """
    return _WORDS[multi].part


def _named(key: str, form: str) -> str:
    """An initial as a page names it: in the list of pages, in a heading, in a sentence."""
    if key == "0-9":
        return "a digit from 0 to 9" if form == "phrase" else "0-9"
    if key == "other":
        return {"label": "Other", "heading": "other initials",
                "phrase": "anything but a letter from A to Z or a digit from "
                          "0 to 9"}[form]
    return key.upper()


def win_rate_text(won: int, lost: int) -> str | None:
    """A win rate as the season page prints it; None is a dash."""
    pct = set_win_pct(won, lost)
    return None if pct is None else f"{pct:.1f}%"


def rating_text(rating: tuple[float | None, float | None]) -> str:
    """A season's singles and doubles ratings, two places each, half up."""
    return " / ".join(DASH if value is None else f"{round_half_up(value, 2):.2f}"
                      for value in rating)


# --- the page body ----------------------------------------------------------


def _e(text) -> str:
    return html.escape(str(text))


def _figure_cells(figures: Mapping[str, int], columns) -> str:
    cells = []
    for key in columns:
        if key == "win_pct":
            text = win_rate_text(figures["sets_won"], figures["sets_lost"])
            if text is None:
                cells.append(f'<td class="num muted" data-field="win_pct">{DASH}</td>')
                continue
        else:
            text = figures[key]
        cells.append(f'<td class="num" data-field="{_e(key)}">{_e(text)}</td>')
    return "".join(cells)


def _head_cells(columns) -> str:
    cells = []
    for key in columns:
        label, words = COLUMNS[key]
        title = "" if words == label else f' title="{_e(words)}"'
        cells.append(f'<th scope="col" class="num"{title}>{_e(label)}</th>')
    return "".join(cells)


def _caption(sentence: str, columns) -> str:
    """A table's caption: its sentence, and under it the key to its figures' labels.

    Marked up as app.js's ``tableCaption`` marks a season table's, and the key
    worded as its ``columnKey`` words one, so it prints and reads as theirs does:
    a title is a hover, which a phone and a sheet of paper never show. *sentence*
    is markup already.
    """
    key = " · ".join(f"{label} — {words}" for label, words
                     in (COLUMNS[column] for column in columns) if words != label)
    return (f'<caption><span class="caption-text">{sentence}</span>'
            + (f'<span class="table-key">{_e(key)}</span>' if key else "")
            + "</caption>")


def _index_html(careers: Sequence[Career], *, caption: str | None = None,
                page: Callable[[str], str] | None = None,
                multi: bool = True) -> list[str]:
    """The list of names, each linking its career further down the page.

    Given *page*, it is a split index's list instead: each name links the page
    of its initial, and its row carries the career's anchor, so a link made for
    the one page lands on the name. And the name alone: its figures are on the
    page it links, and in every row they made the index the split's largest page.
    """
    words = _WORDS[multi]
    columns = words.columns if page is None else ()
    caption = caption or words.listed
    lines = ['<div class="table-wrap"><table>',
             f"  {_caption(_e(caption), columns)}",
             '  <thead><tr><th scope="col">Player</th>'
             + _head_cells(columns) + "</tr></thead>",
             "  <tbody>"]
    for career in careers:
        figures = dict(career.total, seasons_played=career.seasons_played,
                       competitions_played=career.competitions_played)
        anchor = f"{ANCHOR_PREFIX}{_e(career.slug)}"
        row, target = "<tr>", f"#{anchor}"
        if page is not None:
            row = f'<tr id="{anchor}">'
            target = f"{_e(page(initial(career.name)))}#{anchor}"
        lines.append(f'    {row}<th scope="row"><a href="{target}">'
                     f'{_e(career.name)}</a></th>'
                     + _figure_cells(figures, columns) + "</tr>")
    lines += ["  </tbody>", "</table></div>"]
    return lines


def _career_html(career: Career, href: Callable[[SeasonRow, str], str], *,
                 multi: bool = True) -> list[str]:
    """One career's section: a row per season document, and their sum under them.

    In single-season mode the season is the page's, so each row is headed by its
    competition, linked as a Season cell is, and has no Season cell to repeat it.
    """
    words = _WORDS[multi]
    slug = _e(career.slug)
    name = _e(career.name)
    places = ('<th scope="col">Season</th><th scope="col">Competition</th>'
              if multi else '<th scope="col">Competition</th>')
    lines = [f'<section class="career" id="{ANCHOR_PREFIX}{slug}" '
             f'aria-labelledby="h-{slug}">',
             f'  <h2 class="section" id="h-{slug}">{name}</h2>',
             '  <div class="table-wrap"><table>',
             "    " + _caption(f"{name}, {words.by}. Each {words.part} opens that "
                          f"{words.part}'s page for them.",
                          (*ROW_COLUMNS, "rating")),
             f'    <thead><tr>{places}<th scope="col">Team</th>'
             + _head_cells((*ROW_COLUMNS, "rating")) + "</tr></thead>",
             "    <tbody>"]
    for row in career.rows:
        played = row.figures["matches"] > 0
        spelt = ("" if row.name == career.name
                 else f' <small class="muted">as {_e(row.name)}</small>')
        unplayed = "" if played else " (on the roster, no sets played)"
        team = row.team_name if row.team_name else "(not on a roster)"
        link = f'<a href="{_e(href(row, career.slug))}">'
        if multi:
            where = (f'<th scope="row">{link}{_e(row.season_name)}</a>{spelt}</th>'
                     f"<td>{_e(row.competition_label + unplayed)}</td>")
        else:
            where = (f'<th scope="row">{link}{_e(row.competition_label)}</a>'
                     f"{_e(unplayed)}{spelt}</th>")
        lines.append(("      <tr>" if played else '      <tr class="muted">')
                     + where + f"<td>{_e(team)}</td>"
                     + _figure_cells(row.figures, ROW_COLUMNS)
                     + f'<td class="num">{_e(rating_text(row.rating))}</td></tr>')
    lines += ["    </tbody>",
              f'    <tfoot><tr><th scope="row">{_e(words.total)}</th>'
              + "<td></td>" * (2 if multi else 1)
              + _figure_cells(career.total, ROW_COLUMNS)
              + '<td class="muted">not added</td></tr></tfoot>',
              "  </table></div>",
              "</section>"]
    return lines


def body_html(careers: Sequence[Career], *,
              href: Callable[[SeasonRow, str], str], multi: bool = True) -> str:
    """The page body: the head, the index, and one section per career.

    Given *href* rather than working it out, so this module never learns how the
    site is laid out; site.py, which decided where each dashboard went, says.
    """
    lines = _view_head(careers, multi=multi)
    if careers:
        lines += _index_html(careers, multi=multi)
        for career in careers:
            lines += _career_html(career, href, multi=multi)
    return "\n".join(lines)


def index_html(careers: Sequence[Career], *, page: Callable[[str], str],
               index: str, multi: bool = True) -> str:
    """A split careers page's index: every name, each linking their initial's page.

    *page* names an initial's page and *index* this one, both given by site.py,
    which decided the names. No career is printed here, only the list, the
    pages, and the lines that take a link to a career on to where it is.
    """
    lines = _view_head(careers, names_only=True, multi=multi,
                       after=[f"  <p>{_e(_WORDS[multi].split)}</p>"])
    lines += _initials_nav(careers, key=None, page=page, index=index, multi=multi)
    if careers:
        lines += _index_html(careers, page=page, multi=multi)
    lines.append(_FORWARD)
    return "\n".join(lines)


def initial_html(key: str, careers: Sequence[Career], *,
                 href: Callable[[SeasonRow, str], str],
                 page: Callable[[str], str], index: str,
                 multi: bool = True) -> str:
    """One initial's page of a split careers page: its careers, as one page prints them.

    Handed every career and picking its own by :func:`initial`, so the page a
    career is printed on and the page the index links for it are one rule's
    answer. Written whether anybody's name starts with *key* or not.
    """
    named_in = _WORDS[multi].named_in
    mine = [career for career in careers if initial(career.name) == key]
    starts = _named(key, "phrase")
    lead = (f'  <p>Names starting with {_e(starts)}. <a href="{_e(index)}">The '
            f"index</a> lists everyone, each with the page their name is on.</p>")
    lines = _view_head(careers, key=key, lead=[lead], multi=multi)
    lines += _initials_nav(careers, key=key, page=page, index=index, multi=multi)
    if not mine:
        lines.append(f'<p class="empty">No name in {_e(named_in)} starts '
                     f"with {_e(starts)}.</p>")
        return "\n".join(lines)
    lines += _index_html(mine, multi=multi,
                         caption=f"Everyone named in {named_in} whose name "
                                 f"starts with {starts}, by name")
    for career in mine:
        lines += _career_html(career, href, multi=multi)
    return "\n".join(lines)


def _view_head(careers: Sequence[Career], *, key: str | None = None,
               lead: Sequence[str] = (), after: Sequence[str] = (),
               names_only: bool = False, multi: bool = True) -> list[str]:
    """The heading and the paragraphs every careers page opens with.

    Counted over every career on the site, whichever page this is, so an
    initial's page says what the index says when no season has a result yet.
    A view of *names_only*, the split index, has a lead of its own and no
    one-season line, both of which are about totals it does not print.
    """
    words = _WORDS[multi]
    tally = counts(careers, multi=multi)
    intro = words.index_intro if names_only else words.intro
    lines = ['<div class="view-head">',
             f"  <h1>{_e(heading(key, multi=multi))}</h1>", *lead]
    lines += [f"  <p>{_e(text)}</p>" for text in intro]
    if tally.with_results == 1 and not names_only:
        lines.append(f'  <p class="muted">{_e(words.one)}</p>')
    lines += after
    lines.append("</div>")
    if tally.with_results == 0:
        lines.append(f'<p class="empty">{_e(words.none)}</p>')
    return lines


def _initials_nav(careers: Sequence[Career], *, key: str | None,
                  page: Callable[[str], str], index: str,
                  multi: bool = True) -> list[str]:
    """Every page of a split careers page, the open one marked as current.

    An initial nobody's name starts with is its letter and no link, except on
    its own page, which a link from an earlier build can still open.
    """
    having = {initial(career.name) for career in careers}
    current = ' aria-current="page"'
    lines = [f'<nav class="initials" aria-label="{_e(_WORDS[multi].initials)}"><ul>',
             f'  <li><a href="{_e(index)}"{current if key is None else ""}>'
             f"Everyone</a></li>"]
    for each in INITIALS:
        label = _e(_named(each, "label"))
        if each in having or each == key:
            lines.append(f'  <li><a href="{_e(page(each))}"'
                         f'{current if each == key else ""}>{label}</a></li>')
        else:
            lines.append(f'  <li><span class="muted">{label}</span></li>')
    lines.append("</ul></nav>")
    return lines
