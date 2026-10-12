"""Assemble and write ``season.json``, the only data file the dashboard reads.

Two constraints shape everything here.

**Determinism.** The document is committed by the clubs that use this tool, so
re-running on unchanged cards must produce a byte-identical file and let ``git
diff`` show real result changes only. That rules out set iteration reaching the
output, dict insertion order standing in for a sort, and timestamps.
``meta.generated`` is the single wall-clock value and the caller may pin it. Keys
are not invented in this module either: the stats dataclasses' field names *are*
the JSON keys, so :func:`_plain` walks them mechanically and the schema stays in
one place.

**Nothing absent may look present.** The predecessor's document could not
distinguish "six teams" from "six teams that happened to have cards", or "a
ten-round season three rounds in" from "a three-round season". So this document
carries its own epistemics: ``meta.provenance`` says where each kind of identity
came from, ``meta.rounds_total`` is null unless a human declared it (F3),
``meta.ladder_state`` says whether the ladder has started and every team has
counted the same number, and ``meta.order_stands`` whether its order stands on
the counts it has (F10, 4.523), and
``meta.cards_seen``/``cards_scored``/``cards_rejected`` plus
``validation.rejected`` account for every PDF found (F4).

One further difference from the predecessor is a bug fix rather than an addition.
Its fixture index keyed matches on ``(round, home, away)`` and raised an uncaught
``ValueError`` when two cards collided, which is how a merged pair of seasons
took the whole run down. Duplicates are now :mod:`~matchcentre.validate`'s F9,
and this module's job is to *report* them in the document and still write a file
a club can use.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath

from . import __version__, discover, scoring, stats, validate
from .card import Card, Rejection
from .config import DEFAULT_PATH
from .competition import (DRAW_DECLARED, Club, Competition, Provenance,
                          fixture_sort_key, merge_provenance, round_number)
from .model import Match
from .scorecard import NO_VERB, thin_card_note
from .slugs import unslug

__all__ = ["build_season", "published", "season_json", "write_season",
           "SEASON_FORMAT"]

# Bumped when a key changes meaning, or when the page comes to need a key an
# older document lacks, so a dashboard opened against an older season.json can
# say so instead of rendering blanks. 2 is the first whose matches carry
# counts_as_played, 3 the first whose cards, fixtures and rounds carry the key
# the page finds a card by, 4 the first whose rounds say whether the build read
# their label as a number, and whose rounds and fixtures no longer list a card
# by the id it printed. A key a page of format 4 reads and an older build of it
# did not write, `meta.regular_fixtures`, and since 4.428 `meta.ladder_state`
# and `meta.leader`, 4.458's `meta.ladder_detail` and 4.523's
# `meta.ordered_on_average`, `meta.order_stands` and `meta.leader_withheld`,
# the page prints as unknown rather than bumping the format a second time, and
# so `players[].team_matches`, by printing no line under a player's Matches
# figure; and `rounds[].cards_played` it reads as that build's rounds were
# read, a round with a card played.
SEASON_FORMAT = 4

# The aspects of a competition whose provenance travels to the dashboard (F7).
# Fixed and sorted here rather than taken from whatever discovery happened to
# record, so a missing aspect shows up as a value to explain rather than as an
# absent key nobody notices.
PROVENANCE_ASPECTS = ("captains", "draw", "format", "players", "rounds_total",
                      "season", "teams")
# `meta.provenance` of an aspect that names nothing: players where the competition
# names nobody, fill-ins included, captains where no team has one, and the season
# where the pages show no name. Not a `Provenance` member, as anonymise.WITHHELD is
# not: an absence is not a value anybody worked out, and it has no trusted answer.
# The page flags only "derived" and "mixed", so it lists none of them as a guess.
EMPTY = "empty"


def build_season(matches: Sequence[Match], *, competition: Competition,
                 source_dir: str | Path, generated: str | None = None,
                 club: Club | None = None,
                 cards: Sequence[Card] = (),
                 rejected: Sequence[Rejection] = (),
                 seasons_mode: str = "single",
                 sheet=None, config_name: str = DEFAULT_PATH,
                 strays: Sequence[discover.FiledElsewhere] = ()) -> dict:
    """The full ``season.json`` document as a plain, JSON-ready dict.

    ``matches`` are the matches read from cards, without byes: byes are
    materialised inside :mod:`~matchcentre.stats`, and only when the team list is
    closed (F1), so they must not be pre-mixed into this list.

    ``generated`` is injectable so that a test, or a club that wants a stable
    diff, can pin the one non-reproducible value in the output.

    One document describes one competition. That is deliberate: two competitions
    in one file would put two ladders in one table, and it is exactly what the
    predecessor did when its competition key left the season out.

    ``sheet`` is the association's sheet held up against these matches, for
    validation to report on; it reaches no figure, and only a disagreement reaches
    the document at all, as a warning.

    ``config_name`` is the config file the build read, by its name, which a finding
    that sends the club to a line of it names (:func:`validate.validate`).

    ``strays`` are the cards of the competition's teams filed under another
    competition (:attr:`~matchcentre.discover.Discovery.strays`), for validation to
    say where a card went; like ``sheet`` they reach no figure.
    """
    ordered = stats.chronological(matches, competition.rounds)
    report = validate.validate(ordered, competition, club=club, cards=cards,
                              rejected=rejected, seasons_mode=seasons_mode,
                              sheet=sheet, config_name=config_name,
                              strays=strays)
    team_rows = stats.team_stats(ordered, competition)
    # The ladder's state and its leader, each asked once, for the report, the front
    # page and the overview alike (4.428, 4.438). The leader is the ruleset's
    # as well: an order on the average is led while counts are unequal,
    # unless its top row is level on it with a team on another count
    # (4.523).
    state = stats.ladder_state(ordered, competition)
    top = stats.ladder_leader(team_rows, state, competition.rules)
    # And why it is nobody, where the counts alone are not why.
    level_top = stats.level_at_the_top(team_rows, state, competition.rules)
    # And the words the state is said in, played or counted, by what F10 compared,
    # for the same three and F10 (4.458).
    detail = stats.ladder_detail(stats.ladder_counts(ordered, competition),
                                 stats.ladder_played(ordered, competition))
    player_rows = stats.player_stats(ordered, competition)
    # A name for each card that no other card has, for the page to find it by: the
    # printed id is missing from some cards and shared by others.
    keys = stats.card_keys(ordered)
    fixtures, duplicates = _fixtures(ordered, competition, keys)
    # Every match is in the document, including the ones no figure counted, each
    # carrying the reason. Worked out for the list as a whole because two cards for
    # one fixture are only a duplicate in each other's company.
    reasons = stats.not_counted_reasons(ordered, competition)
    # The other half of that answer, because on two kinds of card the two differ: a
    # final was played by people who are credited for it, and a night two cards agree
    # about and name differently is counted for the teams and credited to nobody.
    # One field cannot say both, and a page building a figure out of the names on a
    # card has to ask this one rather than the ladder's.
    credits = stats.not_credited_reasons(ordered)
    # And the third: which rows every aggregate in this document was computed from.
    # The front page counts its own sets, games and matches played off these rows,
    # and neither field above says what it needs -- a final and a level night are in
    # no ladder figure and were played, the kept card of a night named two ways
    # credited nobody and is still the night.
    totals = stats.not_totalled_reasons(ordered)
    # And the other cards that claim each night, by their places in this document's
    # `matches`, so a card counted for nothing because another disagrees can say which
    # file that is, and the page can put the cards of one night together.
    others = stats.other_cards(ordered)

    return {
        "format": SEASON_FORMAT,
        "meta": _meta(ordered, competition=competition, source_dir=source_dir,
                      generated=generated, club=club, cards=cards,
                      rejected=rejected, seasons_mode=seasons_mode,
                      length_refused=any(issue.rule == "F3"
                                         for issue in report.errors),
                      regular_fixtures=_regular_fixtures(ordered, fixtures, totals,
                                                         competition),
                      ladder_state=state, ladder_detail=detail,
                      leader=({"slug": top.slug, "value": top.points_average}
                              if top else None),
                      ordered_on_average=stats.ordered_on_the_average(
                          competition.rules),
                      order_stands=stats.order_stands(team_rows, state,
                                                      competition.rules),
                      leader_withheld=(stats.LEVEL_AT_THE_TOP if level_top
                                       else None),
                      matches_played=_matches_played(ordered, totals)),
        "teams": [_plain(team_rows[slug]) for slug in sorted(team_rows)],
        "players": _players(player_rows, competition, club),
        "matches": [_match(m, competition, key=key, not_counted=reason,
                           not_credited=credit, not_totalled=total,
                           other_cards=list(other), notes=_notes(m, cards))
                    for m, key, reason, credit, total, other
                    in zip(ordered, keys, reasons, credits, totals, others)],
        "fixtures": fixtures,
        "rounds": _rounds(ordered, competition, keys),
        "head_to_head": _plain(stats.head_to_head(ordered, competition)),
        "player_names": dict(sorted(
            stats.player_names(ordered, competition).items())),
        "validation": _validation(report, rejected=rejected,
                                 duplicates=duplicates,
                                 competition=competition),
    }


def published(season: dict) -> dict:
    """*season* as it may leave the machine that built it.

    The document :func:`build_season` returns names every card the way the walk
    over ``--data`` found it, and the run's report needs exactly that: it is read
    by the person who has to find the file. A published page does not. Built
    with ``--data /home/<account>/cards``, or ``C:\\Users\\<Full Name>\\cards``
    on Windows, the document carried the builder's account in every match's
    ``source``, every refused card's path and reason, both files of a card
    exported twice and the problem list's messages, and so into ``season.json``,
    the page's island and the one-file page a club emails round.

    So a card is published as the folder's own name and the path inside it,
    ``data/Round1/card.pdf``, with forward slashes, and ``source_dir`` as that
    name, resolved so that ``.`` and a symlink say what the folder is called.
    That is what ``--data data`` has always published: the layout this project
    documents publishes what it did, and every path on the page is still one
    ``tools/doctor.py`` finds from the project folder. Every spelling of one
    folder publishes the same bytes, and so does a build on Windows.

    Nothing changes but a card's name as the walk spelt it, wherever it is in
    any string of the document: as written, and as ``repr`` writes it, which is
    how the operating system's half of a refusal quotes it. Every string, not a
    list of fields, because a card's name reaches free text, and the next
    finding that quotes one would be a leak nobody listed. The names themselves
    rather than the folder's spelling, which a club's own words can hold: a
    season called ``Summer 2026/27`` whose cards are kept in ``2026``.
    """
    folder = Path(season["meta"]["source_dir"])
    name = folder.resolve().name
    names = {}
    for card in ([match.get("source") for match in season.get("matches", ())]
                 + [refused.get("path") for refused
                    in season.get("validation", {}).get("rejected", ())]):
        where = discover.inside(card, folder) if card else None
        if where is not None:
            said = str(PurePosixPath(name, *where.parts))
            names[card] = said
            names[repr(card)[1:-1]] = repr(said)[1:-1]
    cards = re.compile("|".join(map(re.escape, names)))

    def clean(value):
        if isinstance(value, str):
            return cards.sub(lambda found: names[found[0]], value) if names else value
        if isinstance(value, Mapping):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    document = clean(season)
    document["meta"]["source_dir"] = name
    return document


def season_json(season: dict) -> str:
    """The document exactly as it goes to disk: sorted, indented, newline-ended.

    Separate from :func:`write_season` because :mod:`matchcentre.site` renders the
    text *before* deciding whether to write it -- a document whose bytes the file
    already holds is left alone, and one that differs only in ``meta.generated``
    keeps the stamp it had. Both of those are comparisons against this text, and a
    caller that formatted the document any other way would find every file
    different every time and rewrite the lot.

    ``sort_keys`` is what makes the output independent of the order this module
    happened to build its dicts in; ``allow_nan=False`` makes a stray NaN an
    error here rather than a file no JSON parser will read.
    """
    return json.dumps(season, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False) + "\n"


def write_season(path: str | Path, season: dict) -> None:
    """Write the document, sorted and newline-terminated."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(season_json(season), encoding="utf-8")


# --- meta -------------------------------------------------------------------


def _meta(matches: Sequence[Match], *, competition: Competition,
          source_dir: str | Path, generated: str | None, club: Club | None,
          cards: Sequence[Card], rejected: Sequence[Rejection],
          seasons_mode: str, length_refused: bool = False,
          regular_fixtures: dict, ladder_state: str, ladder_detail: str,
          leader: dict | None, ordered_on_average: bool,
          order_stands: bool, leader_withheld: str | None,
          matches_played: int) -> dict:
    played = stats.rounds_present(matches, competition.rounds)
    unlisted = sum(1 for r in rejected if r.folder)
    return {
        "competition": competition.id,
        "day": competition.day,
        "division": competition.division,
        "label": competition.display_label,
        "club": club.name if club else "",
        "season": competition.season,
        "season_key": competition.season_key,
        "season_label": season_label(competition.season, competition.season_key),
        "season_label_from": season_label_from(competition),
        "seasons_mode": seasons_mode,
        # Which build wrote this document. `format` above says what the *shape*
        # means and is bumped only when a key changes meaning; this says which
        # release produced the numbers, which is the question a bug report about a
        # figure on the page actually needs answered. A constant, so it costs the
        # byte-identical rebuild nothing.
        "version": __version__,
        "generated": generated or datetime.now().replace(microsecond=0).isoformat(),
        # Null unless a human said how long the season is (F3). The predecessor
        # filled this with the number of rounds played, so a third of the way
        # into a ten-round season the progress meter read 100%. Null as well where
        # F3 stopped the run over the length declared, which the page would
        # otherwise read "Round 3 of 1" against: provenance.rounds_total still says
        # "declared" then, and declared with no length is how the report and the
        # page tell a length refused from one nobody gave.
        "rounds_total": None if length_refused else competition.rounds_total,
        # The regular rounds with cards, as the length counts them; the finals are
        # in `rounds_present`, and flagged in `rounds`.
        "rounds_played": len(competition.rounds_played(matches)),
        "rounds_present": played,
        # And those rounds' fixtures, played of listed, for the foot beneath that
        # count (4.194). The Matches played tile counts the whole season's, finals
        # in; this leaves the finals out, as the round count does.
        "regular_fixtures": regular_fixtures,
        # Every PDF found is one of these three (F4). A rejected card cannot be
        # attributed to a competition -- it was refused before its division could
        # be read -- so `cards_rejected` is the whole run's count, repeated in
        # every document. Naming it everywhere is the point: any of those files
        # might have belonged here. A folder the reader could not list is among
        # the rejected and is no card: it is counted apart, as a folder.
        "cards_seen": len(cards) + len(rejected) - unlisted,
        "cards_scored": len(cards),
        "cards_rejected": len(rejected) - unlisted,
        "folders_unlisted": unlisted,
        "matches_total": len(matches),
        # The nights of those played, each once, finals in: the Matches played tile's
        # count, and what makes a competition one "with results" on the front page,
        # where a document alone is also written for a folder of washouts (4.440).
        "matches_played": matches_played,
        "teams_total": len(competition.teams),
        # Whether the club declared this division's draw or it was reconstructed
        # from the cards. A derived draw can only describe rounds already played,
        # so the dashboard must not present its fixture list as a schedule (F2).
        "draw_source": competition.draw_source,
        "teams_declared": competition.teams_declared,
        # "empty" while no regular-season match has been counted, and nobody
        # is placed; "in-progress" while teams have counted unequal numbers;
        # "complete" otherwise (F10, 4.428). `ladder_complete` is the last of
        # the three, kept for the pages that read it. `leader` is the top of a
        # complete ladder, and of one in progress ordered on the average; null
        # on one not started, on one in progress ordered on a total, which is
        # a snapshot and not a standing until the counts are level, and on one
        # whose top row is level on the average with a team on another count,
        # which `leader_withheld` says (4.523).
        "ladder_state": ladder_state,
        # The state as a clause, "teams have played unequal numbers of matches"
        # where every team's counted is its P and counted matches where a round was
        # credited, so that no page says played of counts that are not (4.458).
        "ladder_detail": ladder_detail,
        "ladder_complete": ladder_state == "complete",
        "leader": leader,
        # Why `leader` is null where the counts are not why, as a clause the
        # front page and the overview's hero both give: "the teams on top are
        # level on the average with different numbers of matches counted",
        # `stats.LEVEL_AT_THE_TOP`, on a ladder in progress ordered on the
        # average whose top row is level on it with a team on another count
        # (`stats.level_at_the_top`). Null on every other ladder, whose
        # reason is its state and `ladder_detail`, as in a document from
        # before 4.523, where it is absent.
        "leader_withheld": leader_withheld,
        # Whether the ladder is ordered on the average, which divides each
        # team's points by its own counted matches, so that its order stands
        # while the counts are unequal wherever the average decides it.
        # Python's answer, `stats.ordered_on_the_average`, so that the page
        # does not read it off `rules.ladder_by` a second time.
        "ordered_on_average": ordered_on_average,
        # Whether the order stands on the counts it has, `stats.order_stands`:
        # true of a complete ladder, and of one in progress ordered on the
        # average where no two teams are level on it on unequal counts, whom
        # the next key, a total, puts in order for the matches the one has
        # counted more. Of a ladder in progress, the overview's note says the
        # order stands where this is true; where it is false on a ladder
        # ordered on the average it says the tie goes to the next key, so
        # there the order is a snapshot; and it calls the order a snapshot on
        # a ladder ordered on a total or, in a document from before 4.523,
        # where both keys are absent. A complete ladder's note gives its basis
        # alone, and an empty one's says nothing has been counted.
        "order_stands": order_stands,
        "rules": _rules(competition),
        "provenance": _provenance(competition),
        # Coerced because callers naturally hold this as a Path, and a Path in the
        # document is a TypeError from json.dumps at the moment of writing -- after
        # the whole run has already been done.
        "source_dir": str(source_dir),
    }


def season_label(season: str, season_key: str) -> str:
    """What to print for the season, with the key as the last resort.

    A season's name wins. Failing that the key is un-slugged, because a
    picker heading of ``2026-winter`` reads like a directory listing -- and
    ``meta.provenance.season`` is what tells the reader that the pretty version
    was worked out rather than declared. *season* is the name
    :func:`matchcentre.discover.season_name` gives, "" where nobody named it.
    """
    if season:
        return season
    if season_key:
        return unslug(season_key)
    return ""


def season_offer(competition: Competition, dates: Iterable[str] = ()) -> str:
    """The one season name a run offers a club to declare, wherever it is offered.

    Only what something supports: the name the pages show, printed on a card,
    declared, or the season's folder's name made readable, else the year or years
    the matches' own *dates* were played in, else "". A plausible invented name
    would be pasted into config.toml unread and become a declared fact: the file
    offered "2026 Season 2" where no card printed a season, beside a review that
    offered the year, and deleting its # declared the invention.
    """
    shown = season_label(competition.season, competition.season_key)
    if shown:
        return shown
    return " to ".join(sorted({date[:4] for date in dates if len(date or "") >= 4}))


# What the season's name the pages show was made from, where nobody gave one:
# `meta.season_label_from`, and the word each message about the name is chosen by.
SEASON_FROM_FOLDER = "folder"
SEASON_FROM_DATE = "date"


def season_label_from(competition: Competition) -> str:
    """What the season's name the pages show was made from, or "".

    :data:`SEASON_FROM_FOLDER` where it is its folder's name made readable, and
    :data:`SEASON_FROM_DATE` where it is the year on cards that sit in no season
    folder, which multi-season mode keys them by; so only where no card printed a
    name and nobody declared one. "" where a name was given, and where there is no
    key, which shows no name at all. A message about the name says which, rather
    than that the dashboard leaves it out: said of both that the name was the
    folder's, a year beside the run's own note that its cards sit in no season
    folder sent the club to look for one.
    """
    if competition.season:
        return ""
    return competition.season_key_from


def _rules(competition: Competition) -> dict:
    """The active scoring scheme, set rule and match format, spelled out.

    Every one of these silently reorders the ladder when it is wrong, so the
    document carries the human-readable form beside the name: a club can read
    ``win 4, loss 2, 0.1 per game; decided on games then sets`` off its own
    dashboard and recognise its competition, which no preset name allows.
    """
    fmt = competition.fmt
    rules = competition.rules
    return {
        "scoring": rules.name,
        "scoring_detail": rules.describe(),
        "set_rule": fmt.set_rule.name,
        "set_rule_detail": fmt.set_rule.describe(),
        "format": fmt.name,
        "format_detail": fmt.describe(),
        "slots": [{"key": slot.key, "label": slot.label, "doubles": slot.doubles}
                  for slot in fmt.slots],
        "min_sets_for_result": fmt.min_sets_for_result,
        "ladder_by": list(rules.ladder_by),
        "average_unplayed": rules.average_unplayed,
        "unplayed": (scoring.UNPLAYED_AVERAGE if rules.average_unplayed
                     else scoring.UNPLAYED_ZERO),
        "unplayed_detail": scoring.unplayed_detail(rules.average_unplayed),
        # The same rule as a clause, for the two places on the page that have
        # already named the round they are talking about -- a bye's row, and the
        # card of a night that was washed out. Both forms are exported rather than
        # one being cut down in JavaScript: the page would have to strip a prefix
        # off a sentence Python wrote, which is a parser for a string that has a
        # function.
        "unplayed_worth": scoring.unplayed_worth(rules.average_unplayed),
        "allows_draw": rules.allows_draw,
        # Empty in the ordinary grade, and the banner prints no row for a pair with
        # nothing in it. `allows_draw` above is the same fact as a boolean, for code;
        # these two are for a reader, and both are needed -- the word alone ("no")
        # answers a question nobody asked, and the sentence alone has no label.
        "draws": "" if rules.allows_draw else "no",
        "draws_detail": scoring.draws_detail(rules.allows_draw),
    }


def _provenance(competition: Competition) -> dict:
    """Where each kind of identity came from (F7).

    Discovery's own record wins where it has one. The fallbacks below exist so
    that an aspect discovery forgot still reports something true rather than
    defaulting to ``derived`` and understating what we know -- a format read off
    a card is ``card``, not a guess. Ahead of both, an aspect that names nothing
    is :data:`EMPTY`, which no provenance describes.
    """
    out: dict[str, str] = {}
    for aspect in PROVENANCE_ASPECTS:
        if _names_nothing(aspect, competition):
            out[aspect] = EMPTY
        elif aspect in competition.provenance:
            out[aspect] = competition.provenance[aspect].value
        else:
            out[aspect] = _inferred_provenance(aspect, competition).value
    # One invariant this document enforces on itself. "teams: declared" next to
    # "teams_declared: false" would tell a reader the team list is settled while
    # byes are still uncredited and an unknown team is still only a suspicion --
    # the exact confusion F1 and F8 exist to prevent. Declaring team *names* is not
    # declaring the *list*, so it reports as mixed until the list itself is closed.
    if (out["teams"] == Provenance.DECLARED.value
            and not competition.teams_declared):
        out["teams"] = Provenance.MIXED.value
    return out


def _names_nothing(aspect: str, competition: Competition) -> bool:
    """Whether *aspect* of *competition* names nothing at all, which is not a value
    discovery or anybody else worked out.

    Asked ahead of discovery's record and the fallback below, so the two agree:
    the provenance of an empty collection is ``derived``, and a washout that named
    nobody was told its players and captains were both guesses. Players are judged
    over the fill-ins too, so that it is never said where a name is shown.
    """
    if aspect == "players":
        return not competition.player_index() and not competition.fill_in_names
    if aspect == "captains":
        return not any(team.captain for team in competition.teams.values())
    if aspect == "season":
        return not season_label(competition.season, competition.season_key)
    return False


def _inferred_provenance(aspect: str, competition: Competition) -> Provenance:
    if aspect == "teams":
        return (Provenance.DECLARED if competition.teams_declared
                else Provenance.DERIVED)
    if aspect == "format":
        # Nobody guesses a format: it is either pinned in the config or read from
        # the card's own row bands and marker columns.
        return (Provenance.DECLARED if competition.format_declared
                else Provenance.CARD)
    if aspect == "draw":
        return (Provenance.DECLARED if competition.draw_source == DRAW_DECLARED
                else Provenance.CARD)
    if aspect == "rounds_total":
        # The four provenance values can all only describe a value that exists, so
        # when nobody declared a season length this reports ``derived`` for a field
        # whose value is null. Read the two together: it is ``meta.rounds_total``
        # being null that forbids a percentage under F3, never this string. A
        # consumer that switched on the provenance instead would show "2 of 2
        # rounds, derived" -- the invention F3 exists to stop. And ``declared``
        # describes a null too, where F3 refused the length declared and ``_meta``
        # gave none: the report and the page read the pair then only to say why
        # there is no percentage.
        return (Provenance.DECLARED if competition.rounds_total is not None
                else Provenance.DERIVED)
    if aspect == "season":
        return Provenance.DECLARED if competition.season else Provenance.DERIVED
    if aspect == "players":
        return merge_provenance(player.name_source for player
                                in competition.player_index().values())
    if aspect == "captains":
        # The team's captain, as discovery reads it: every player with a (C) mark,
        # the captain or not, counted a mark beside a declared captain, and a
        # declared captain on no card was in no player's record.
        return merge_provenance(team.captain_source
                                for team in competition.teams.values()
                                if team.captain)
    return Provenance.DERIVED


# --- sections ---------------------------------------------------------------


def _players(players: dict[str, stats.PlayerStats],
             competition: Competition,
             club: Club | None = None) -> list[dict]:
    """Players by team then squad order, with anyone unregistered last.

    Squad order comes from the roster block on the cards rather than the
    alphabet, because the roster prints a team's players in an order the club
    chose. Teams sort by slug: their draw codes are optional display metadata now
    and may be absent, so they cannot carry the sort.

    ``registered_elsewhere`` is the other competition of the club whose squads
    register a player no squad of this one does, found as fill-in-other-competition
    finds it, and null for everybody else (4.443). A player with no team here is
    either somebody another night registers or somebody no squad of the club holds,
    and the page's Fill-in notes say different things of the two: nothing else in
    a player's record tells them apart, and the page does not read it out of the
    report's findings.
    """
    index = competition.player_index()
    squad_order = {
        player.slug: (team.slug, position)
        for team in competition.teams.values()
        for position, player in enumerate(team.players)
    }

    def key(slug: str) -> tuple[int, str, int, str]:
        placed = squad_order.get(slug)
        if placed is None:
            # A fill-in from another grade, or someone who played before the
            # roster listed them. Sorted after every registered player, and by
            # slug among themselves so the order is total.
            return (1, "", 0, slug)
        return (0, placed[0], placed[1], slug)

    def record(slug: str) -> dict:
        plain = _plain(players[slug])
        elsewhere = (club.registered_elsewhere(slug, competition.id)
                     if club is not None and slug not in index else None)
        plain["registered_elsewhere"] = elsewhere.id if elsewhere else None
        return plain

    return [record(slug) for slug in sorted(players, key=key)]


def _notes(match: Match, cards: Sequence[Card]) -> list[str]:
    """The reader's warnings on *match*'s card, as its notes on the page.

    All of them, except on a card that records nothing
    (:func:`~matchcentre.validate.records_nothing`). That card is spared the
    two that say a cross-check is missing: :data:`~matchcentre.scorecard.NO_VERB`
    and :func:`~matchcentre.scorecard.thin_card_note`. With every row 0-0 and no
    result counted, there is nothing for a check to confirm. That is why F6's
    warning and ``totals-missing`` stand down on such a card in the report, and
    its ``no-result`` is the one finding about it (4.424). The page showed the
    two as parse notes and counted the match among those with parse notes, so a
    club was told on the page what the report no longer told it.

    Rejected: dropping them in the reader, which cannot know whether the match
    has a result, and whose words the doctor shows; dropping them in discover,
    which cannot ask validate, since validate imports it; and dropping every
    warning on such a card, which would take what its roster block or its Match
    Forfeited boxes say too.
    """
    spared = {note for card in cards
              if discover.where(card) == match.source
              and validate.records_nothing(match, card)
              for note in (NO_VERB, thin_card_note(card.checks))}
    return [warning for warning in match.warnings if warning not in spared]


def _match(match: Match, competition: Competition, *, key: str,
           not_counted: str = "", not_credited: str = "",
           not_totalled: str = "", other_cards: Sequence[int] = (),
           notes: Sequence[str]) -> dict:
    """One match.

    ``home``/``away`` are slugs and ``home_name``/``away_name`` the display
    names. The predecessor put draw codes in the key positions and the names
    beside them; a code appears on no scorecard, so it had to be invented, and
    one misprinted banner renumbered every team below it.

    ``home_points``/``away_points`` are per-match and therefore null on a match
    that earned none: an averaged bye's value belongs to the team's season, not
    to a fixture, and printing it here would imply the teams played.

    ``not_counted`` comes from :func:`~matchcentre.stats.not_counted_reasons`,
    which needs the whole match list to work it out; the default is the honest one
    for a match considered on its own. ``not_credited`` is the same shape for the
    other question a card answers — who played — and the two are not the same
    question: a final and a level night are in none of the ladder's figures and are
    in everybody's record, and the kept card of a night named two ways is in the
    ladder and in nobody's.

    ``not_totalled`` is the third, for the figures that are about tennis played
    rather than about the ladder or about a person: a final is in that night's sets
    and games, and a card exported twice is not.

    ``other_cards`` is every other card for the same fixture, by its place in the
    document's ``matches``: :func:`~matchcentre.stats.other_cards`. Empty for a match
    considered on its own, which is again the honest default. A place and not its
    ``source``, because a page that withholds names withholds every source that
    named a player, and those all read the same: joined on them, every such card
    found every other.

    ``key`` is the name the page finds the card by, one no other card in the
    document has: :func:`~matchcentre.stats.card_keys`. ``id`` is the one printed
    on it, kept for the card's own page and for a reader to check against paper.
    """
    return {
        "id": match.match_id,
        "key": key,
        "round": match.round,
        "date": match.date,
        "court": match.court,
        "home": match.home,
        "away": match.away,
        "home_name": competition.team_name(match.home),
        "away_name": competition.team_name(match.away),
        "status": match.status.value,
        "result": _result(match),
        "decided": match.decided,
        "forfeited_by": match.forfeited_by,
        "disputed": match.disputed,
        "home_games": match.home_games,
        "away_games": match.away_games,
        "home_sets": match.home_sets,
        "away_sets": match.away_sets,
        # The games scored above, a forfeited rubber a love set and a retired one
        # raised to win it; and the games printed, which are the games played, the
        # overview's Games played tile (4.421). A night nobody played a rubber of
        # happened and counts, and has no games, sets or margin to set beside one
        # that was played, so the tiles leave it out by this field, the model's.
        "printed_home_games": match.printed_home_games,
        "printed_away_games": match.printed_away_games,
        "no_rubber_played": match.no_rubber_played,
        "home_points": stats.round_stored(match.points_for(match.home)),
        "away_points": stats.round_stored(match.points_for(match.away)),
        "margin": abs(match.home_games - match.away_games),
        # Both of these are about a contest, so both are false or null on a match
        # nobody played; the model holds the predicate so the page cannot re-derive
        # it from the two totals and reach a different answer.
        "tied_on_games": match.tied_on_games,
        "decided_by": match.decided_by,
        # Whether the ladder counted this one, and if it did not, why. The points
        # above are what the scoring scheme makes of the card either way; a finals
        # round earns none (F11), a card exported twice is counted once and two
        # cards that disagree are counted nowhere (F9). The page reads both fields
        # rather than deciding for itself, so it cannot chart a bar no total
        # includes, and cannot explain a missing match as finals when it is a
        # duplicate.
        "counts_for_ladder": not not_counted,
        "not_counted": not_counted,
        # Whether anybody's playing record was credited from this card, and if not,
        # why. Its own field because it is a different question from the one above:
        # a finals card is in no ladder figure and in everybody's record, and two
        # cards agreeing about a result while naming different players settle the
        # result, so that night is in the ladder above and in nobody's row below it.
        # Asked of every row, by a page that lists the sets a person played: reading
        # the names off a card the aggregates skipped shows somebody a night their
        # own match count does not include.
        "not_credited": not_credited,
        # And whether this card's own numbers are in the season's totals — the sets
        # completed, the games played, the tightest night, the biggest margin. A third
        # field rather than the page excusing two of the ladder's reasons, because
        # those totals are about tennis that was played: a final and a level night are
        # in them and in no ladder figure, and the kept card of a night named two ways
        # is in them and in nobody's record. Spelled out on the page as the reasons
        # that do not apply, the next reason added to the ladder's five would change
        # these totals without anybody deciding it should.
        "not_totalled": not_totalled,
        # And whether the night happened at all: Match.counts_as_played, the one test
        # every aggregate in this document was computed with. Written on the row so
        # the page asks it rather than keeping its own list of the statuses that count
        # for nothing -- a second place a new status has to go, and the first one to
        # miss it makes the tiles disagree with the ladder beside them.
        "counts_as_played": match.counts_as_played,
        "sets": [_set(s, match) for s in match.sets],
        "home_roster": list(match.home_roster),
        "away_roster": list(match.away_roster),
        "fill_ins": _fill_ins(match, competition),
        "source": match.source,
        # The other cards for this night, by their places in `matches`, each of whose
        # rows names itself above. A clash is counted nowhere until one of its files
        # goes, and the card that says so has to be able to say which files they are;
        # on a copy or a night named two ways it is the same fact, and it is written
        # there too rather than leaving a card with another beside it saying it has
        # none.
        "other_cards": list(other_cards),
        # The reader's warnings on its card, less those _notes leaves off (4.424).
        "warnings": list(notes),
    }


def _result(match: Match) -> str:
    if not match.decided:
        return "none"
    return match.winner or "draw"


def _set(result, match: Match) -> dict:
    return {
        "slot": result.slot.key,
        "label": result.slot.label,
        "doubles": result.slot.doubles,
        "home_games": result.home_games,
        "away_games": result.away_games,
        "home_players": list(result.home_players),
        "away_players": list(result.away_players),
        "winner": result.winner,
        "status": result.status.value,
        "completed": result.completed,
        "margin": result.margin,
        # The games the row prints, where an award scores it otherwise (4.421,
        # 4.422), for the pages to show beside it; and whether nobody played it,
        # the model's one rule, which a player's list of sets asks so that it
        # lists no rubber their figures leave out.
        "printed_home_games": result.printed_home_games,
        "printed_away_games": result.printed_away_games,
        "nobody_played": match.nobody_played(result),
    }


def _fill_ins(match: Match, competition: Competition) -> list[str]:
    """Slugs that played for a side they are neither registered nor rostered to."""
    index = competition.player_index()
    found: set[str] = set()
    for side in ("home", "away"):
        roster = match.roster_for(side)
        team = match.team_for(side)
        for result in match.sets:
            for slug in result.players_for(side):
                if stats.is_fill_in(slug, index.get(slug), roster, team):
                    found.add(slug)
    # Sorted on the way out: the set above is a membership test, and an
    # unordered container must never reach the document.
    return sorted(found)


def _regular_fixtures(matches: Sequence[Match], fixtures: Sequence[dict],
                      totals: Sequence[str], competition: Competition) -> dict:
    """The regular rounds' fixtures: how many were played, of how many listed.

    Counted as the front page counts the whole season's, each fixture once by the
    one card every total is built from (``totals`` is :func:`stats.not_totalled_reasons`
    of *matches*) and a night nobody played not at all; and over the rounds
    ``rounds_played`` counts, so a final is in neither number.
    """
    return {
        "played": sum(1 for match, total in zip(matches, totals)
                      if match.counts_as_played and not total
                      and not competition.is_finals_round(match.round)),
        "total": sum(1 for fixture in fixtures
                     if not competition.is_finals_round(fixture["round"])),
    }


def _matches_played(matches: Sequence[Match], totals: Sequence[str]) -> int:
    """The nights played, by the one card every total is built from, finals in.

    The overview's Matches played tile counts these rows the same way, and the
    front page asks it of a competition's document for whether it has results
    (4.440): a washout is a card and a match, and no night played.
    """
    return sum(1 for match, total in zip(matches, totals)
               if match.counts_as_played and not total)


def _fixtures(matches: Sequence[Match], competition: Competition,
              keys: Sequence[str]) -> tuple[list[dict], list[dict]]:
    """Every fixture the draw lists, plus every card the draw does not.

    Returns the fixture list and a description of any ``(round, home, away)``
    collision found. The predecessor raised here, which turned a duplicated card
    -- or two seasons sharing a competition key -- into a run that produced
    nothing at all. A duplicate is a real thing that happens to real clubs, so it
    is counted per fixture, listed for the validation section and reported as F9;
    the file still gets written.

    ``keys`` are the cards' own, :func:`~matchcentre.stats.card_keys`, and each
    fixture lists those of its cards in the document's order, so every card is
    under exactly one fixture and a page walking the fixtures meets all of them.
    """
    by_pair: dict[tuple[str, str, str], Match] = {}
    counts: dict[tuple[str, str, str], list[Match]] = {}
    named: dict[tuple[str, str, str], list[str]] = {}
    for match, key in zip(matches, keys):
        counts.setdefault(match.key, []).append(match)
        by_pair.setdefault(match.key, match)
        named.setdefault(match.key, []).append(key)

    duplicates = [
        {
            "round": key[0],
            "home": key[1],
            "away": key[2],
            "match_ids": [m.match_id for m in clash],
            "sources": [m.source for m in clash],
        }
        for key, clash in sorted(counts.items(),
                                 key=lambda item: fixture_sort_key(item[0],
                                                                   competition.rounds))
        if len(clash) > 1
    ]

    # Whether a fixture with no card is one the season has gone past. Worked out
    # here because the page cannot: it would need the whole draw and the same
    # clock-free rule, in a second language, and two answers to one question is how
    # a round heading came to say "not played yet" over rows for its own cards.
    # Not the same as `not played` below — a card the draw has the other way round
    # leaves the declared fixture unplayed and is not a missing card.
    gaps = {(gap.round, gap.home, gap.away)
            for gap in stats.fixtures_without_cards(matches, competition)}

    out: list[dict] = []
    for number in competition.round_order:
        rnd = competition.rounds[number]
        for home, away in rnd.fixtures:
            match = by_pair.get((number, home, away))
            out.append({
                "round": number,
                "date": match.date if match else rnd.date,
                "home": home,
                "away": away,
                "home_name": competition.team_name(home),
                "away_name": competition.team_name(away),
                "played": match is not None,
                "card_missing": (number, home, away) in gaps,
                "cards": len(counts.get((number, home, away), ())),
                "keys": list(named.get((number, home, away), ())),
            })

    # Cards for fixtures the declared draw does not list are a validation error
    # (F8 territory), but dropping them from the document would hide the very
    # match that proves the draw wrong.
    scheduled = {(f["round"], f["home"], f["away"]) for f in out}
    for key, match in sorted(by_pair.items(),
                             key=lambda kv: stats.match_sort_key(kv[1],
                                                                 competition.rounds)):
        if key in scheduled:
            continue
        out.append({
            "round": match.round,
            "date": match.date,
            "home": match.home,
            "away": match.away,
            "home_name": competition.team_name(match.home),
            "away_name": competition.team_name(match.away),
            "played": True,
            "card_missing": False,      # it is the card
            "cards": len(counts[key]),
            "keys": list(named[key]),
        })
    return out, duplicates


def _rounds(matches: Sequence[Match], competition: Competition,
            keys: Sequence[str]) -> list[dict]:
    out: list[dict] = []
    for number in competition.round_order:
        rnd = competition.rounds[number]
        played = [m for m in matches if m.round == number]
        out.append({
            "number": number,
            # Whether the build read the label as a number, by the rule it orders
            # the rounds and warns by (F11), so the page titles `Round 3` by this
            # and keeps no test of its own (4.215). A bool and not the week: the
            # page asks yes or no, and round `0`'s week is falsy.
            "numbered": round_number(number) is not None,
            "date": rnd.date,
            "played": bool(played),
            # Every card of the round by its key, a card that printed no id too.
            "keys": [key for m, key in zip(matches, keys) if m.round == number],
            "cards": len(played),
            # How many of them count as played, by Match.counts_as_played, so the page
            # heads a round whose every card was called off or left blank "not played"
            # and not "played" over cards that each say they count for nothing, and
            # keeps no copy of the rule (4.452). `played` keeps its meaning, a round
            # with a card, which Latest round and rounds_played go by.
            "cards_played": sum(1 for m in played if m.counts_as_played),
            "is_finals": rnd.is_finals,
            # Only ever filled in when the team list was declared or the club
            # stated the bye itself (F1). Empty here means "nobody said", not
            # "nobody rested", and the dashboard needs the difference to explain a
            # team's missing row. A list because a whole competition can rest.
            # Only the rests that stand, by the rule the ladder pays them by, so
            # the page never draws a rest beside a card or a fixture of the team it
            # names. Every rest left out that way is one the validation section
            # names, as bye-contradicted or bye-and-fixture.
            "byes": list(stats.rests_standing(rnd, matches)),
            "note": rnd.note,
            "source": rnd.source,
        })
    return out


def _validation(report: validate.Report, *, rejected: Sequence[Rejection],
                duplicates: Sequence[dict], competition: Competition) -> dict:
    """The validation section, including anything this module found itself.

    ``ok`` accounts for rejected cards as well as errors: a document whose
    validation says ``ok`` while three PDFs went unread would be exactly the
    silence F4 exists to forbid.
    """
    errors = [_finding(issue) for issue in report.errors]
    # F9 duplicates come from validate.py in the normal case, where it decides which
    # of the two they are: the same card twice, counted once, a warning; or two cards
    # that disagree, counted nowhere, an error. This module sees one case it cannot —
    # a collision produced by two competitions being merged under one key, where each
    # half is internally consistent — and reports it here, as an error, only when
    # nothing was said about F9 at all. Anything else would either double the finding
    # or contradict it.
    unreported = ([] if any(issue.rule == "F9" for issue in report.issues)
                  else list(duplicates))
    for clash in unreported:
        errors.append(_finding(validate.Issue(
            rule="F9",
            severity="error",
            message=f"{len(clash['match_ids'])} cards share round "
                    f"{clash['round']} {clash['home']} v {clash['away']}; "
                    f"only the first is counted as a fixture",
            # The round and the pair, without the competition: this finding is written
            # into that competition's own document, and both the report and the page
            # label it with the id themselves.
            subject=f"round {clash['round']}",
            detail=", ".join(p for p in clash["sources"] if p),
        )))
    return {
        "ok": report.ok and not rejected and not unreported,
        "errors": errors,
        # The errors that are this competition's own, counted by the function the
        # report's own line and the front page call, so the page shows their
        # number and not one that counts each refused card a second time.
        "own_errors": len(own_findings(errors)),
        "warnings": [_finding(issue) for issue in report.warnings],
        "rejected": [_plain(r) for r in rejected],
        "duplicates": list(duplicates),
    }


def own_findings(findings: Sequence[Mapping]) -> list:
    """The findings of one document that are its own: every one but F4.

    A refused card might belong to any competition, so every document carries
    every refusal as an F4 error, and ``validation.rejected`` lists it as well.
    Counted as one of a competition's errors too, one card read as two mistakes
    wherever that count was printed beside the count of cards not scored. So this
    is the one rule for what a competition's errors are: the report's own
    ``problems`` line, the errors section's labels, the front page's flag and,
    through ``validation.own_errors``, the page's pill all use it. Rejected:
    counting ``len(errors) - len(rejected)`` where each is printed, the same
    number today and a second rule to keep in step with this one.
    """
    return [finding for finding in findings if finding["rule"] != "F4"]


def subject_said(finding: Mapping) -> bool:
    """Whether a finding's message already begins with its subject, as a word.

    What a subject is for: the key a program finds a finding by, always written
    -- which card a ``verb-no-result`` is about, which path a refusal names -- and
    printed by a reader only when the message has not already said it. Measured
    over the suite's findings, 1,060 of 1,171 with a subject began with it,
    followed by ``: `` or by `` page``, and the report and the page printed each
    of them a second time. Rejected: a subject only where the message does not
    name the thing, which rewrites a thousand messages or drops the keys that are
    read. Followed by nothing, a space or a colon, so ``round 1`` is not said by
    a message about ``round 12``.
    """
    subject = str(finding.get("subject") or "")
    message = str(finding.get("message") or "")
    return (bool(subject) and message.startswith(subject)
            and message[len(subject):len(subject) + 1] in ("", " ", ":"))


def _finding(issue: validate.Issue) -> dict:
    """One finding as the document carries it, with ``subject_said`` for the page."""
    plain = _plain(issue)
    plain["subject_said"] = subject_said(plain)
    return plain


# --- plain-data conversion --------------------------------------------------


def _plain(value):
    """Recursively convert dataclasses, enums and mappings to JSON-safe data.

    Dataclass field order is preserved on the way in and ``json.dumps`` sorts
    keys on the way out, so nothing downstream depends on declaration order.
    Sets raise: an unordered container in the output would make the file churn
    between runs for no reason a reader could see.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name))
                for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(),
                                                     key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("sets are unordered and would make the output unstable")
    return value
