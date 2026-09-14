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
``meta.ladder_complete`` says whether the positions mean anything yet (F10), and
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
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path

from . import stats, validate
from .card import Card, Rejection
from .competition import (DRAW_DECLARED, Club, Competition, Provenance,
                          merge_provenance, round_sort_key)
from .model import Match
from .slugs import unslug

__all__ = ["build_season", "write_season", "SEASON_FORMAT"]

# Bumped when a key changes meaning, so a dashboard opened against an older
# season.json can say so instead of rendering blanks.
SEASON_FORMAT = 1

# The aspects of a competition whose provenance travels to the dashboard (F7).
# Fixed and sorted here rather than taken from whatever discovery happened to
# record, so a missing aspect shows up as a value to explain rather than as an
# absent key nobody notices.
PROVENANCE_ASPECTS = ("captains", "draw", "format", "players", "rounds_total",
                      "season", "teams")


def build_season(matches: Sequence[Match], *, competition: Competition,
                 source_dir: str | Path, generated: str | None = None,
                 club: Club | None = None,
                 cards: Sequence[Card] = (),
                 rejected: Sequence[Rejection] = (),
                 seasons_mode: str = "single") -> dict:
    """The full ``season.json`` document as a plain, JSON-ready dict.

    ``matches`` are the matches read from cards, without byes: byes are
    materialised inside :mod:`~matchcentre.stats`, and only when the team list is
    closed (F1), so they must not be pre-mixed into this list.

    ``generated`` is injectable so that a test, or a club that wants a stable
    diff, can pin the one non-reproducible value in the output.

    One document describes one competition. That is deliberate: two competitions
    in one file would put two ladders in one table, and it is exactly what the
    predecessor did when its competition key left the season out.
    """
    ordered = stats.chronological(matches)
    report = validate.validate(ordered, competition, club=club, cards=cards,
                              rejected=rejected)
    team_rows = stats.team_stats(ordered, competition)
    player_rows = stats.player_stats(ordered, competition)
    fixtures, duplicates = _fixtures(ordered, competition)

    return {
        "format": SEASON_FORMAT,
        "meta": _meta(ordered, competition=competition, source_dir=source_dir,
                      generated=generated, club=club, cards=cards,
                      rejected=rejected, seasons_mode=seasons_mode),
        "teams": [_plain(team_rows[slug]) for slug in sorted(team_rows)],
        "players": _players(player_rows, competition),
        "matches": [_match(m, competition) for m in ordered],
        "fixtures": fixtures,
        "rounds": _rounds(ordered, competition),
        "head_to_head": _plain(stats.head_to_head(ordered)),
        "player_names": dict(sorted(
            stats.player_names(ordered, competition).items())),
        "validation": _validation(report, rejected=rejected,
                                 duplicates=duplicates,
                                 competition=competition),
    }


def write_season(path: str | Path, season: dict) -> None:
    """Write the document, sorted and newline-terminated.

    ``sort_keys`` is what makes the output independent of the order this module
    happened to build its dicts in; ``allow_nan=False`` makes a stray NaN an
    error here rather than a file no JSON parser will read.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(season, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False)
    target.write_text(text + "\n", encoding="utf-8")


# --- meta -------------------------------------------------------------------


def _meta(matches: Sequence[Match], *, competition: Competition,
          source_dir: str | Path, generated: str | None, club: Club | None,
          cards: Sequence[Card], rejected: Sequence[Rejection],
          seasons_mode: str) -> dict:
    played = stats.rounds_present(matches)
    return {
        "competition": competition.id,
        "day": competition.day,
        "division": competition.division,
        "label": competition.display_label,
        "club": club.name if club else "",
        "season": competition.season,
        "season_key": competition.season_key,
        "season_label": _season_label(competition),
        "seasons_mode": seasons_mode,
        "generated": generated or datetime.now().replace(microsecond=0).isoformat(),
        # Null unless a human said how long the season is (F3). The predecessor
        # filled this with the number of rounds played, so a third of the way
        # into a ten-round season the progress meter read 100%.
        "rounds_total": competition.rounds_total,
        "rounds_played": len(played),
        "rounds_present": played,
        # Every PDF found is one of these three (F4). A rejected card cannot be
        # attributed to a competition -- it was refused before its division could
        # be read -- so `cards_rejected` is the whole run's count, repeated in
        # every document. Naming it everywhere is the point: any of those files
        # might have belonged here.
        "cards_seen": len(cards) + len(rejected),
        "cards_scored": len(cards),
        "cards_rejected": len(rejected),
        "matches_total": len(matches),
        "teams_total": len(competition.teams),
        # Whether the club declared this division's draw or it was reconstructed
        # from the cards. A derived draw can only describe rounds already played,
        # so the dashboard must not present its fixture list as a schedule (F2).
        "draw_source": competition.draw_source,
        "teams_declared": competition.teams_declared,
        # False when teams have played unequal numbers of counted matches, in
        # which case the order is a snapshot and not a standing (F10).
        "ladder_complete": stats.ladder_complete(matches, competition),
        "rules": _rules(competition),
        "provenance": _provenance(competition),
        # Coerced because callers naturally hold this as a Path, and a Path in the
        # document is a TypeError from json.dumps at the moment of writing -- after
        # the whole run has already been done.
        "source_dir": str(source_dir),
    }


def _season_label(competition: Competition) -> str:
    """What to print for the season, with the key as the last resort.

    A declared season name wins. Failing that the key is un-slugged, because a
    picker heading of ``2026-winter`` reads like a directory listing -- and
    ``meta.provenance.season`` is what tells the reader that the pretty version
    was worked out rather than declared.
    """
    if competition.season:
        return competition.season
    if competition.season_key:
        return unslug(competition.season_key)
    return ""


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
        "allows_draw": rules.allows_draw,
    }


def _provenance(competition: Competition) -> dict:
    """Where each kind of identity came from (F7).

    Discovery's own record wins where it has one. The fallbacks below exist so
    that an aspect discovery forgot still reports something true rather than
    defaulting to ``derived`` and understating what we know -- a format read off
    a card is ``card``, not a guess.
    """
    out: dict[str, str] = {}
    for aspect in PROVENANCE_ASPECTS:
        if aspect in competition.provenance:
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
        # rounds, derived" -- the invention F3 exists to stop.
        return (Provenance.DECLARED if competition.rounds_total is not None
                else Provenance.DERIVED)
    if aspect == "season":
        return Provenance.DECLARED if competition.season else Provenance.DERIVED
    if aspect == "players":
        return merge_provenance(player.name_source for player
                                in competition.player_index().values())
    if aspect == "captains":
        return merge_provenance(player.captain_source for player
                                in competition.player_index().values()
                                if player.is_captain)
    return Provenance.DERIVED


# --- sections ---------------------------------------------------------------


def _players(players: dict[str, stats.PlayerStats],
             competition: Competition) -> list[dict]:
    """Players by team then squad order, with anyone unregistered last.

    Squad order comes from the roster block on the cards rather than the
    alphabet, because the roster prints a team's players in an order the club
    chose. Teams sort by slug: their draw codes are optional display metadata now
    and may be absent, so they cannot carry the sort.
    """
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

    return [_plain(players[slug]) for slug in sorted(players, key=key)]


def _match(match: Match, competition: Competition) -> dict:
    """One match.

    ``home``/``away`` are slugs and ``home_name``/``away_name`` the display
    names. The predecessor put draw codes in the key positions and the names
    beside them; a code appears on no scorecard, so it had to be invented, and
    one misprinted banner renumbered every team below it.

    ``home_points``/``away_points`` are per-match and therefore null on a match
    that earned none: an averaged bye's value belongs to the team's season, not
    to a fixture, and printing it here would imply the teams played.
    """
    return {
        "id": match.match_id,
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
        "home_points": stats.round_stored(match.points_for(match.home)),
        "away_points": stats.round_stored(match.points_for(match.away)),
        "margin": abs(match.home_games - match.away_games),
        "tied_on_games": match.home_games == match.away_games,
        "sets": [_set(s) for s in match.sets],
        "home_roster": list(match.home_roster),
        "away_roster": list(match.away_roster),
        "fill_ins": _fill_ins(match, competition),
        "source": match.source,
        "warnings": list(match.warnings),
    }


def _result(match: Match) -> str:
    if not match.decided:
        return "none"
    return match.winner or "draw"


def _set(result) -> dict:
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


def _fixtures(matches: Sequence[Match],
              competition: Competition) -> tuple[list[dict], list[dict]]:
    """Every fixture the draw lists, plus every card the draw does not.

    Returns the fixture list and a description of any ``(round, home, away)``
    collision found. The predecessor raised here, which turned a duplicated card
    -- or two seasons sharing a competition key -- into a run that produced
    nothing at all. A duplicate is a real thing that happens to real clubs, so it
    is counted per fixture, listed for the validation section and reported as F9;
    the file still gets written.
    """
    by_pair: dict[tuple[str, str, str], Match] = {}
    counts: dict[tuple[str, str, str], list[Match]] = {}
    for match in matches:
        counts.setdefault(match.key, []).append(match)
        by_pair.setdefault(match.key, match)

    duplicates = [
        {
            "round": key[0],
            "home": key[1],
            "away": key[2],
            "match_ids": [m.match_id for m in clash],
            "sources": [m.source for m in clash],
        }
        for key, clash in sorted(counts.items()) if len(clash) > 1
    ]

    out: list[dict] = []
    for number in sorted(competition.rounds, key=round_sort_key):
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
                "match_id": match.match_id if match else None,
                "cards": len(counts.get((number, home, away), ())),
            })

    # Cards for fixtures the declared draw does not list are a validation error
    # (F8 territory), but dropping them from the document would hide the very
    # match that proves the draw wrong.
    scheduled = {(f["round"], f["home"], f["away"]) for f in out}
    for key, match in sorted(by_pair.items(),
                             key=lambda kv: stats.match_sort_key(kv[1])):
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
            "match_id": match.match_id,
            "cards": len(counts[key]),
        })
    return out, duplicates


def _rounds(matches: Sequence[Match], competition: Competition) -> list[dict]:
    out: list[dict] = []
    for number in sorted(competition.rounds, key=round_sort_key):
        rnd = competition.rounds[number]
        played = [m for m in matches if m.round == number]
        out.append({
            "number": number,
            "date": rnd.date,
            "played": bool(played),
            "matches": [m.match_id for m in played if m.match_id],
            "cards": len(played),
            "is_finals": rnd.is_finals,
            # Only ever set when the team list was declared (F1). Null here means
            # "nobody said", not "nobody rested", and the dashboard needs the
            # difference to explain a team's missing row.
            "bye": rnd.bye,
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
    errors = [_plain(issue) for issue in report.errors]
    # F9 duplicates come from validate.py in the normal case. This module sees
    # one case it cannot: a collision produced by two competitions being merged
    # under one key, where each half is internally consistent. Reported here if
    # it was not reported there, so the document never carries a silent overwrite.
    if duplicates and not any(e["rule"] == "F9" for e in errors):
        for clash in duplicates:
            errors.append(_plain(validate.Issue(
                rule="F9",
                severity="error",
                message=f"{len(clash['match_ids'])} cards share round "
                        f"{clash['round']} {clash['home']} v {clash['away']}; "
                        f"only the first is counted as a fixture",
                subject=f"{competition.id} round {clash['round']}",
                detail=", ".join(p for p in clash["sources"] if p),
            )))
    return {
        "ok": report.ok and not rejected and not duplicates,
        "errors": errors,
        "warnings": [_plain(issue) for issue in report.warnings],
        "rejected": [_plain(r) for r in rejected],
        "duplicates": list(duplicates),
    }


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
