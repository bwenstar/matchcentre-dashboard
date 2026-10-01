#!/usr/bin/env python3
"""Generate a fictional club's season of scorecards, so a fresh clone has data.

The onboarding promise is two steps: drop PDFs in ``data/``, run ``build.py``. A
new clone has no PDFs, so the promise cannot be demonstrated and the first thing a
club sees is an empty dashboard. This script fills that gap:

    python3 tools/demo.py
    python3 build.py

Everything it invents is invented. The club, the teams and every player name are
generated here; nothing is copied from any real club's cards, and the name lists
below deliberately share no first or last name with the real people whose cards
the predecessor project was written against.

The season is built to exercise the parts of the pipeline that a run of ordinary
6-2 results never reaches: a whitewash, a match decided by a single game, a
forfeit, a washout, a retirement mid-set, and a player filling in for a team they
are not rostered to. An odd ``--teams`` gives every round a bye, which is a
different path again (F1: a bye is only ever materialised from a *declared* team
list, so the demo prints the byes it intends rather than leaving them to be
guessed).

``--seasons multi`` writes two seasons of that club instead, one player changed
between them, for the careers page, which only a build of more than one season
writes (:func:`write_archive`).

Deterministic by construction: the only randomness comes from ``--seed``, and the
card writer stamps no date into the PDF, so two runs with the same arguments
produce byte-identical files and the demo dashboard is reproducible.
"""

from __future__ import annotations

import argparse
import datetime
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from matchcentre.cli import Parser
from matchcentre.cardwriter import (AUTO, NO_TICK, CardSpec, RosterRow, SetRow,
                                    write_card_file)
from matchcentre.config import (DEFAULT_PATH, DEMO_CONFIG, DEMO_MARKER,
                                SEASON_MODES)
from matchcentre.format import (DEFAULT_FORMAT, FORMATS, MatchFormat, SetRule,
                                completed, match_format, plausible)
from matchcentre.model import SetStatus
from matchcentre.scope import in_words
from matchcentre.slugs import team_slug
from matchcentre import pasteable, pasteable_path, scorecard, version_line
from matchcentre.card import letterhead_key

# --- the invented club ------------------------------------------------------

CLUB_NAME = "Brindle Creek Tennis Club"
CLUB_PREFIX = "BRINDLE"          # what a club would set as strip_team_prefix
CLUB_LINES = ("BRINDLE CREEK", "TENNIS CLUB INC")
# The letterhead as the card reader reports it, which is what `--clean` recognises
# its own cards by. Derived from the lines above rather than written out again, so
# renaming the invented club cannot leave the cleanup deleting somebody else's cards
# or refusing to delete its own.
LETTERHEAD = letterhead_key(" ".join(CLUB_LINES))
CLUB_WEB = "www.brindlecreek.invalid"
CLUB_PHONE = "0700000000"
CLUB_EMAIL = "fixtures@brindlecreek.invalid"
VENUE = "Brindle"                # printed with the court number, as the template does
SEASON_LABEL = "Season 1"

# Nicknames, not surnames, so a team name can never be mistaken for a person's.
TEAM_NICKNAMES = ("MAGPIES", "ROSELLAS", "QUOLLS", "BUNYIPS", "KOOKABURRAS",
                  "WOMBATS", "GALAHS", "NUMBATS", "BILBIES", "CURLEWS",
                  "DUNNARTS", "PLOVERS")

# Names are combined from these two lists so the roster reads like a club's rather
# than like a list of placeholders. Both lists are inventions.
FIRST_NAMES = ("Marguerite", "Desmond", "Priya", "Tobias", "Nadia", "Callum",
               "Imogen", "Rafael", "Sunniva", "Hamish", "Beatrix", "Yusuf",
               "Elke", "Lorenzo", "Fenella", "Dmitri", "Anwen", "Kofi",
               "Saoirse", "Bartholomew", "Thaddeus", "Rowena", "Mihail",
               "Clementine")
LAST_NAMES = ("Wrenfield", "Aldergate", "Thistlewood", "Brackenbury",
              "Halloran", "Vasquez-Poole", "Underhill", "Kettleborough",
              "Nascimento", "Fairweather", "Okonkwo", "Redgrave",
              "Sandringham", "Villalobos", "Quintrell", "Merriweather",
              "Larkspur", "Bramblecote", "Oyelaran", "Trentham", "Nakagawa",
              "Featherstone", "Cordwainer", "Pemberley")

PLAYERS_PER_TEAM = 3             # what the template's roster block prints
RATING_LOW, RATING_HIGH = 1.00, 5.50
FIRST_MATCH_ID = 4200000
DEFAULT_SEED = 20260818
SEASON_START = datetime.date(2026, 2, 2)      # a Monday; the first round is the
                                              # next occurrence of --day
DEFAULT_OUT = ROOT / "data" / "demo-season"
# `--seasons multi`: a folder of seasons, and the config that has the build read it
# as one. Beside build.py and not in data/, which is the club's
# (:func:`archive_refusal`).
DEFAULT_ARCHIVE = ROOT / "demo-seasons"
ARCHIVE_CONFIG = DEMO_CONFIG     # beside build.py, which the line runs from
# Begun with DEMO_MARKER: that and its name are how a build that reads it knows it
# is this one.
ARCHIVE_CONFIG_TEXT = (
    f"{DEMO_MARKER} --seasons multi for the invented seasons it\n"
    f"# writes, and written again each time it runs. It is not your {DEFAULT_PATH}:\n"
    "# build.py reads it only when --config names it.\n"
    "[build]\n"
    'seasons = "multi"\n')

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")

# --- what makes each fixture worth looking at -------------------------------

NORMAL = "normal"
WHITEWASH = "whitewash"
CLOSE = "close"
FORFEIT = "forfeit"
RETIRED = "retired set"
WASHOUT = "washout"
FILL_IN = "fill-in"

# In this order, one per fixture slot, spread across rounds before repeating a
# round. Every one of them is a state the rest of the program has a branch for.
SPECIALS = (WHITEWASH, CLOSE, FORFEIT, RETIRED, WASHOUT, FILL_IN)


@dataclass(frozen=True, slots=True)
class Player:
    name: str
    singles: float
    doubles: float


@dataclass(frozen=True, slots=True)
class Team:
    name: str                    # as a card would shout it: "BRINDLE MAGPIES"
    slug: str
    players: tuple[Player, ...]  # the captain first


@dataclass(frozen=True, slots=True)
class Fixture:
    round_index: int             # 0-based
    round_label: str
    date: datetime.date
    court: int
    home: Team
    away: Team
    flavour: str
    match_id: str
    season: str                  # the season line the card prints: its Season's


@dataclass(frozen=True, slots=True)
class Season:
    folder: str                  # its key: the folder it is written to
    label: str                   # as its cards print it
    start: datetime.date         # its first round is the next --day on or after
    first_id: int                # no two seasons share a match id


# The first is the plain demo's season, and the second is later in the same year
# under a label and match ids of its own. Between them one player leaves one team
# (`change_roster`), so the careers page shows a career of both seasons and a
# career of one.
SEASONS = (Season(folder="2026-summer", label=SEASON_LABEL, start=SEASON_START,
                  first_id=FIRST_MATCH_ID),
           Season(folder="2026-winter", label="Season 2",
                  start=datetime.date(2026, 7, 27), first_id=FIRST_MATCH_ID + 10000))


# --- the club ---------------------------------------------------------------


def player_names(count: int, rng: random.Random) -> list[str]:
    """``count`` distinct invented names, drawn without repeating while it can.

    Shuffled lists walked in step rather than two independent draws: drawing at
    random gave four Kettleboroughs and two Beatrixes in an 18-player club, which
    reads as a generator rather than as a roster. Past the length of a list the
    surnames are rotated by one so a second lap pairs differently, which keeps
    every full name distinct -- and full names must be distinct, because a slug is
    the player's primary key and two players sharing one would merge.
    """
    firsts = list(FIRST_NAMES)
    lasts = list(LAST_NAMES)
    rng.shuffle(firsts)
    rng.shuffle(lasts)
    names: list[str] = []
    taken: set[str] = set()
    index = 0
    while len(names) < count:
        lap = index // len(lasts)
        name = (f"{firsts[index % len(firsts)]} "
                f"{lasts[(index + lap) % len(lasts)]}")
        index += 1
        if name in taken:
            continue
        taken.add(name)
        names.append(name)
        if index > count * len(lasts):
            raise ValueError(f"cannot make {count} distinct names from these lists")
    return names


def make_teams(count: int, rng: random.Random) -> tuple[Team, ...]:
    """``count`` teams of :data:`PLAYERS_PER_TEAM`, with unique invented names."""
    if not 2 <= count <= len(TEAM_NICKNAMES):
        raise ValueError(f"--teams must be between 2 and {len(TEAM_NICKNAMES)}")
    pool = player_names(count * PLAYERS_PER_TEAM, rng)
    teams: list[Team] = []
    for number, nickname in enumerate(TEAM_NICKNAMES[:count]):
        start = number * PLAYERS_PER_TEAM
        players = [new_player(name, rng)
                   for name in pool[start:start + PLAYERS_PER_TEAM]]
        full = f"{CLUB_PREFIX} {nickname}"
        teams.append(Team(name=full,
                          slug=team_slug(full, strip_prefix=CLUB_PREFIX),
                          players=tuple(players)))
    return tuple(teams)


def new_player(name: str, rng: random.Random) -> Player:
    """A player called ``name``, with the two ratings a roster line prints."""
    return Player(name=name,
                  singles=round(rng.uniform(RATING_LOW, RATING_HIGH), 2),
                  doubles=round(rng.uniform(RATING_LOW, RATING_HIGH), 2))


def change_roster(teams: tuple[Team, ...], rng: random.Random
                  ) -> tuple[tuple[Team, ...], Player, Player]:
    """The club a season later: the teams, who left and who joined.

    The first team's last player leaves and a newcomer takes the place, so that the
    careers page has a career of both seasons and a career of each season alone.
    One change and no more, with every other player on the same ratings, so that
    the change is what the page shows. The newcomer's name is drawn as the club's
    were and is nobody's already: a name is a career's key, and a newcomer who
    shared one would be an old player's second season.
    """
    team = teams[0]
    leaver = team.players[-1]
    taken = {player.name for side in teams for player in side.players}
    name = next(name for name in player_names(len(taken) + 1, rng)
                if name not in taken)
    joiner = new_player(name, rng)
    changed = Team(name=team.name, slug=team.slug,
                   players=team.players[:-1] + (joiner,))
    return (changed, *teams[1:]), leaver, joiner


# --- the draw ---------------------------------------------------------------


def round_robin(count: int) -> list[list[tuple[int, int]]]:
    """A full single round robin as team indices; ``-1`` is the bye.

    The circle method: one team is pinned and the rest rotate, which produces
    ``count - 1`` rounds for an even field and ``count`` for an odd one, with
    every team meeting every other exactly once. Home and away alternate between
    rounds so no team is at home every week.
    """
    ids = list(range(count))
    if count % 2:
        ids.append(-1)           # the odd team out rests against this
    size = len(ids)
    schedule: list[list[tuple[int, int]]] = []
    for number in range(size - 1):
        pairs: list[tuple[int, int]] = []
        for slot in range(size // 2):
            first, second = ids[slot], ids[size - 1 - slot]
            if number % 2 and slot % 2 == 0:
                first, second = second, first
            pairs.append((first, second))
        schedule.append(pairs)
        ids = ids[:1] + ids[-1:] + ids[1:-1]
    return schedule


def first_playing_date(day: str, start: datetime.date = SEASON_START
                       ) -> datetime.date:
    """The first ``day`` on or after ``start``, :data:`SEASON_START` unless given.

    The card's date is the only source of the night a competition is played on,
    so the dates written here have to actually fall on the requested weekday.
    """
    wanted = WEEKDAYS.index(day)
    ahead = (wanted - start.weekday()) % 7
    return start + datetime.timedelta(days=ahead)


def build_fixtures(teams: tuple[Team, ...], rounds: int, day: str,
                   season: Season = SEASONS[0]
                   ) -> tuple[list[Fixture], list[tuple[int, Team]]]:
    """Every fixture to write, plus the (round index, team) pairs that have a bye."""
    schedule = round_robin(len(teams))
    start = first_playing_date(day, season.start)

    pairings: list[tuple[int, list[tuple[int, int]]]] = []
    byes: list[tuple[int, Team]] = []
    for index in range(rounds):
        base = schedule[index % len(schedule)]
        # A second pass through the draw reverses home and away, which is what
        # clubs actually publish for a double round robin.
        reversed_leg = index >= len(schedule)
        played = [(b, a) if reversed_leg else (a, b) for a, b in base]
        for a, b in played:
            if -1 in (a, b):
                resting = a if b == -1 else b
                byes.append((index, teams[resting]))
        pairings.append((index, [(a, b) for a, b in played if -1 not in (a, b)]))

    # Flavours are placed down the first fixture of each round before moving to
    # the second, so the specials land in different rounds where there is room.
    slots = [(round_index, position)
             for round_index, played in pairings
             for position in range(len(played))]
    slots.sort(key=lambda slot: (slot[1], slot[0]))
    flavours = {slot: SPECIALS[index] for index, slot in enumerate(slots)
                if index < len(SPECIALS)}

    fixtures: list[Fixture] = []
    match_id = season.first_id
    for round_index, played in pairings:
        for position, (home, away) in enumerate(played):
            fixtures.append(Fixture(
                round_index=round_index,
                round_label=str(round_index + 1),
                date=start + datetime.timedelta(days=7 * round_index),
                court=position + 1,
                home=teams[home],
                away=teams[away],
                flavour=flavours.get((round_index, position), NORMAL),
                match_id=str(match_id),
                season=season.label))
            match_id += 1
    return fixtures, byes


# --- legal scorelines -------------------------------------------------------


def finished_pairs(rule: SetRule) -> tuple[tuple[int, int], ...]:
    """Every ``(winner games, loser games)`` that is a finished set under ``rule``.

    Derived from the rule rather than listed, because "6-something" is one club's
    convention: under ``fast4`` the only legal wins are 4-0..4-3, and under
    ``advantage-6`` 8-6 is legal while 6-5 is not. A demo that wrote illegal
    scores would have the reader reporting defects in its own fixtures.
    """
    ceiling = (rule.max_games if rule.max_games is not None
               else rule.games_to_win + rule.win_by)
    return tuple((winner, loser)
                 for winner in range(rule.games_to_win, ceiling + 1)
                 for loser in range(winner)
                 if completed(winner, loser, rule)
                 and plausible(winner, loser, rule))


def unfinished_pair(rule: SetRule) -> tuple[int, int]:
    """A score a set can be abandoned at: unequal, legal, and not a finished set."""
    # Highest leader and closest trailer first, so the abandoned score reads like
    # a set that was in progress rather than one that had barely started.
    for leader in range(rule.games_to_win - 1, 0, -1):
        for trailer in range(leader - 1, -1, -1):
            if (not completed(leader, trailer, rule)
                    and plausible(leader, trailer, rule)):
                return leader, trailer
    return 0, 0                  # a rule where every score finishes the set


def washout_completed(fmt: MatchFormat) -> int:
    """How many rubbers finished before the night was called off.

    Two, where the format leaves room for two. That is the shape of a real washout
    and the shape the README describes: a couple of rubbers in, the rain arrives,
    and the card is left carrying a scoreline that reads like a result. An all-zero
    washout — which is what this demo used to write — demonstrates none of that, and
    the demo is the only washout most readers of this repo will ever see, so the
    dashboard's answer to the real case was invisible to them.

    Capped below ``min_sets_for_result`` rather than simply at two, and the cap is
    the point rather than tidiness: enough finished rubbers and the card would tick
    a winner (:func:`decided`), and a cancelled card that names a winner is a
    different fixture — the one this exists to show is the night nobody won. A
    format with no room returns 0, which is the all-zero card again.
    """
    return max(0, min(2, fmt.min_sets_for_result - 1, len(fmt.slots) - 1))


def score_rows(flavour: str, fmt: MatchFormat,
               rng: random.Random) -> list[tuple[int, int]]:
    """Per-row ``(home games, away games)`` for one match, all legal under the rule.

    Takes the whole format rather than a slot count and a set rule, because the
    washout needs a third thing from it and two arguments that always come from one
    object are two chances to pass a mismatched pair.
    """
    slots = len(fmt.slots)
    rule = fmt.set_rule
    pairs = finished_pairs(rule)
    widest = max(pairs, key=lambda pair: pair[0] - pair[1])
    narrowest = min(pairs, key=lambda pair: pair[0] - pair[1])

    if flavour == WHITEWASH:
        return [widest] * slots

    if flavour == CLOSE:
        # Minimal margins throughout, split as evenly as the row count allows. An
        # even split would be a genuine draw on both games and sets, so one row is
        # widened by a single game to leave the match decided by exactly that.
        home_wins = -(-slots // 2)
        rows = [narrowest] * home_wins
        rows += [(narrowest[1], narrowest[0])] * (slots - home_wins)
        if slots % 2 == 0:
            tighter = [p for p in pairs if p[0] == narrowest[0]
                       and p[1] == narrowest[1] - 1]
            if tighter:
                rows[0] = tighter[0]
        return rows

    if flavour == FORFEIT:
        # A forfeited set still carries games: the club's card is filled in as
        # played, awarding the set to whoever turned up. Which side that is comes
        # off the ticked Forfeit box (`model.forfeit_set_winner`), so 0-0 would
        # award the set just the same; the games are printed because the default
        # `forfeit_award = "as-played"` prices the night from them, and a card of
        # nil-alls would pay the side that turned up for a win and no games.
        return [(widest[1], widest[0])] * slots

    if flavour == WASHOUT:
        # The finished rubbers first, in the order the card prints them, then the
        # abandoned ones at 0-0. Both to the home side: a washout that left the two
        # sides level would be a night whose scoreline nobody could misread, which is
        # the opposite of the case worth shipping.
        done = washout_completed(fmt)
        return [widest, narrowest][:done] + [(0, 0)] * (slots - done)

    # An ordinary match: one side is stronger tonight, and takes most rows.
    favour_home = rng.random() < 0.5
    rows = []
    for _ in range(slots):
        pair = rng.choice(pairs)
        home_takes_it = rng.random() < (0.68 if favour_home else 0.32)
        rows.append(pair if home_takes_it else (pair[1], pair[0]))
    return rows


# --- one card ---------------------------------------------------------------


def line_up(team: Team, fmt: MatchFormat, *, without: str = ""
            ) -> list[tuple[str, ...]]:
    """Which of a team's players take each slot, in the order the card prints them.

    Singles rotate through the roster and doubles rotate through its pairings, so
    over a season every player has both singles and doubles numbers rather than
    the first two names owning the card.

    ``without`` leaves one registered player out, which is what a team looks like
    on the night it lends someone to another team: the remaining players cover the
    extra slot, and the roster block still lists everybody.
    """
    order = [player.name for player in team.players if player.name != without]
    if len(order) < 2:
        order = [player.name for player in team.players]
    pairings = [(a, b) for a in range(len(order)) for b in range(a + 1, len(order))]
    singles_taken = doubles_taken = 0
    out: list[tuple[str, ...]] = []
    for slot in fmt.slots:
        if slot.doubles:
            first, second = pairings[doubles_taken % len(pairings)]
            doubles_taken += 1
            out.append((order[first], order[second]))
        else:
            out.append((order[singles_taken % len(order)],))
            singles_taken += 1
    return out


def substitute(names: list[tuple[str, ...]], missing: str, stand_in: str
               ) -> list[tuple[str, ...]]:
    """``stand_in`` takes every slot ``missing`` was down for."""
    return [tuple(stand_in if name == missing else name for name in row)
            for row in names]


def decided(rows: tuple[SetRow, ...], fmt: MatchFormat) -> bool:
    """Whether enough sets finished for this card to claim a result.

    The same count :class:`~matchcentre.model.Match` uses. A card that printed a
    verb or ticked a winner without it would be stating a result its own rows
    cannot support, which the reader reports as ``verb-no-result``; the washout and
    the three-set retirement are both in that position, and both leave the verb and
    the tick off.
    """
    finished = sum(1 for row in rows
                   if row.status is SetStatus.FORFEIT
                   or (row.status is SetStatus.PLAYED
                       and completed(row.home_games, row.away_games, fmt.set_rule)))
    return finished >= fmt.min_sets_for_result


def build_card(fixture: Fixture, fmt: MatchFormat, division: str, day: str,
               rng: random.Random, *, stand_in: Player | None = None,
               lent_away: str = "") -> tuple[CardSpec, str]:
    """The spec for one fixture, and a one-line description of what it shows.

    ``lent_away`` is a registered player of one of these two teams who is playing
    elsewhere tonight, so this card must not name them.
    """
    scores = score_rows(fixture.flavour, fmt, rng)
    home_names = line_up(fixture.home, fmt, without=lent_away)
    away_names = line_up(fixture.away, fmt, without=lent_away)
    notes: list[str] = []
    if lent_away:
        notes.append(f"{lent_away} is filling in for another team tonight")

    if fixture.flavour == WHITEWASH:
        notes.append("every set to the home side, by the widest legal margin")
    elif fixture.flavour == CLOSE:
        margin = sum(home - away for home, away in scores)
        home_sets = sum(1 for home, away in scores if home > away)
        notes.append(f"sets {home_sets}-{len(scores) - home_sets}, "
                     f"{abs(margin)} game{'' if abs(margin) == 1 else 's'} "
                     f"between them")

    home_fill_ins: tuple[str, ...] = ()
    if fixture.flavour == FILL_IN:
        if stand_in is None:
            # Only reachable with two teams, where there is nobody to borrow from.
            notes.append("no other team to borrow a player from, so this card "
                         "is ordinary")
        else:
            absent = fixture.home.players[1].name
            home_names = substitute(home_names, absent, stand_in.name)
            home_fill_ins = (stand_in.name,)
            notes.append(f"{stand_in.name} fills in for {absent}, and is "
                         f"printed under Fill-ins rather than in the roster")

    status = SetStatus.PLAYED
    status_side = "home"
    # How many of this card's rows were finished before the night stopped. Only a
    # washout has any that the rows after them do not match, and the count comes from
    # `score_rows` through one function so the games and the ticks cannot disagree
    # about which rubbers were played.
    finished_rows = 0
    if fixture.flavour == FORFEIT:
        status, status_side = SetStatus.FORFEIT, "home"
        notes.append(f"{fixture.home.name} forfeited; every set is ticked "
                     f"Forfeit against them and awarded as played")
    elif fixture.flavour == WASHOUT:
        status, status_side = SetStatus.INCOMPLETE, "both"
        finished_rows = washout_completed(fmt)
        notes.append(f"washed out after {finished_rows} of {len(fmt.slots)} sets, "
                     f"so the card carries a scoreline and no result")

    rows = [SetRow(home_games=home_games, away_games=away_games,
                   doubles=slot.doubles, home_players=home, away_players=away,
                   status=SetStatus.PLAYED if index < finished_rows else status,
                   status_side="home" if index < finished_rows else status_side)
            for index, (slot, (home_games, away_games), home, away)
            in enumerate(zip(fmt.slots, scores, home_names, away_names))]

    if fixture.flavour == RETIRED:
        # One set abandoned partway. Which row matters: taking the last one keeps
        # the earlier sets complete, so the match still has a result.
        index = len(rows) - 1
        leader, trailer = unfinished_pair(fmt.set_rule)
        rows[index] = SetRow(home_games=leader, away_games=trailer,
                             doubles=rows[index].doubles,
                             home_players=rows[index].home_players,
                             away_players=rows[index].away_players,
                             status=SetStatus.RETIRED, status_side="away")
        notes.append(f"{fmt.slots[index].label} retired at {leader}-{trailer}")

    frozen_rows = tuple(rows)
    has_result = decided(frozen_rows, fmt)
    spec = CardSpec(
        club_lines=CLUB_LINES,
        club_web=CLUB_WEB,
        club_phone=CLUB_PHONE,
        club_email=CLUB_EMAIL,
        competition_line=f"{CLUB_NAME} {day} Night Fixtures {fixture.date.year}",
        season_line=fixture.season,
        date=fixture.date.isoformat(),
        venue=VENUE,
        court=f"{fixture.court:02d}",
        division=division,
        round_label=fixture.round_label,
        match_id=fixture.match_id,
        home_name=fixture.home.name,
        away_name=fixture.away.name,
        rows=frozen_rows,
        set_rule=fmt.set_rule.name,
        # An undecided card must not tick a winner or print a verb: the reader
        # cross-checks both against the sets it read, and a card that claimed a
        # result it cannot support would be reported as a defect in its own right.
        verb=AUTO if has_result else "",
        winner=AUTO if has_result else NO_TICK,
        forfeited_by="home" if fixture.flavour == FORFEIT else None,
        cancelled=fixture.flavour == WASHOUT,
        home_roster=roster_rows(fixture.home),
        away_roster=roster_rows(fixture.away),
        home_fill_ins=home_fill_ins,
        comments="Washed out - courts closed" if fixture.flavour == WASHOUT else "",
    )
    return spec, "; ".join(notes)


def roster_rows(team: Team) -> tuple[RosterRow, ...]:
    """The registered players, captain first and marked as the card marks them."""
    return tuple(RosterRow(name=player.name, singles=player.singles,
                           doubles=player.doubles, captain=index == 0)
                 for index, player in enumerate(team.players))


def score_summary(spec: CardSpec) -> str:
    return " ".join(f"{row.home_games}-{row.away_games}" for row in spec.rows)


# --- writing the season -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cleaned:
    """What :func:`clean` deleted, and what it would not.

    Three lists rather than a count, because the two kinds of survivor are owed
    different sentences. A club's own scorecard, recognised and left where it is, is
    reassurance about the flag it just typed; a file that is not a PDF at all is a
    note to go and look.
    """

    removed: tuple[Path, ...] = ()
    foreign: tuple[Path, ...] = ()      # PDFs this script did not write
    other: tuple[Path, ...] = ()        # everything that is not a PDF


def is_demo_card(path: Path) -> bool:
    """Whether this PDF is one this script wrote.

    Read off the letterhead printed on the page, with the same reader the build
    uses, so that "which club printed this card" has one definition in this project
    rather than a second one here. Not from the file name, which a club may change,
    and not from a manifest written beside the cards, which a club may delete:
    the file this must never remove is a real scorecard in the same directory, and
    neither of those two survives contact with an ordinary club.

    A file that does not read is **not** this script's. A PDF the reader refuses is
    a PDF this script knows nothing about, and half a download of a club's export is
    likelier than a damaged demo card -- the demo can always be written again, and a
    night's tennis cannot.
    """
    cards, _ = scorecard.read(path)
    return bool(cards) and all(
        letterhead_key(card.club_hint) == LETTERHEAD for card in cards)


def clean(out: Path) -> Cleaned:
    """Delete a previous demo under ``out`` -- the demo, and nothing else.

    Only cards this script wrote are removed. Everything else is reported and left
    exactly where it is, because ``--out`` can be pointed at ``data/``, which is
    where the README tells a club to keep its real exports, and a flag whose help
    reads "remove a previous demo" must not take a season with it. There is no undo
    for that: those files came out of Match Centre one night at a time.
    """
    if not out.exists():
        return Cleaned()
    found = sort_files(out)
    for path in found.removed:
        path.unlink()
    if not found.foreign and not found.other:
        for path in sorted(out.rglob("*"), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        if not any(out.iterdir()):
            out.rmdir()
    return found


def sort_files(out: Path) -> Cleaned:
    """Every file under ``out`` as :func:`clean` sorts them, before it deletes any:
    the cards this script wrote, the PDFs it did not, and everything else.

    Its own function because ``--seasons multi`` asks it too, of a folder it has not
    written yet, and a folder holding a PDF that is not the demo's is refused
    (:func:`archive_refusal`). One answer to "whose card is this", for both.
    """
    removed: list[Path] = []
    foreign: list[Path] = []
    other: list[Path] = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() != ".pdf":
            other.append(path)
        elif is_demo_card(path):
            removed.append(path)
        else:
            foreign.append(path)
    return Cleaned(removed=tuple(removed), foreign=tuple(foreign),
                   other=tuple(other))


def plan_loan(fixtures: list[Fixture], byes: list[tuple[int, Team]],
              teams: tuple[Team, ...]
              ) -> tuple[dict[str, Player], dict[tuple[int, str], str]]:
    """Arrange the fill-in: who is borrowed, and whose card must not name them.

    A player belongs to one team, so borrowing one has to be arranged across two
    fixtures at once. A player named on two cards in the same round is an *error*
    to the reader -- and rightly so, since it usually means a card names the wrong
    person -- so the lending team's card leaves them out and plays a teammate in
    their place. A team resting on a bye is the best lender of all, because it has
    no card that round to contradict.

    Returns the stand-in keyed by the borrowing card's match id, and the player to
    leave out keyed by ``(round index, lending team slug)``.
    """
    resting: dict[int, list[Team]] = {}
    for round_index, team in byes:
        resting.setdefault(round_index, []).append(team)

    stand_ins: dict[str, Player] = {}
    unavailable: dict[tuple[int, str], str] = {}
    for fixture in fixtures:
        if fixture.flavour != FILL_IN:
            continue
        lenders = resting.get(fixture.round_index) or [
            team for team in teams
            if team.slug not in (fixture.home.slug, fixture.away.slug)]
        if not lenders:
            continue                  # a two-team division has nobody spare
        lender = lenders[fixture.round_index % len(lenders)]
        borrowed = lender.players[-1]
        stand_ins[fixture.match_id] = borrowed
        if lender not in resting.get(fixture.round_index, ()):
            unavailable[(fixture.round_index, lender.slug)] = borrowed.name
    return stand_ins, unavailable


def write_season(fixtures: list[Fixture], fmt: MatchFormat, out: Path,
                 division: str, day: str, rng: random.Random,
                 stand_ins: dict[str, Player],
                 unavailable: dict[tuple[int, str], str]
                 ) -> list[tuple[Fixture, Path, CardSpec, str]]:
    written: list[tuple[Fixture, Path, CardSpec, str]] = []
    for fixture in fixtures:
        lent_away = ""
        for side in (fixture.home, fixture.away):
            lent_away = unavailable.get((fixture.round_index, side.slug), lent_away)
        spec, note = build_card(fixture, fmt, division, day, rng,
                                stand_in=stand_ins.get(fixture.match_id),
                                lent_away=lent_away)
        path = write_card_file(
            out / f"round-{fixture.round_index + 1:02d}"
            / f"{fixture.home.slug}-v-{fixture.away.slug}.pdf",
            spec)
        written.append((fixture, path, spec, note))
    return written


# --- what the run says -------------------------------------------------------


def print_rounds(written: list[tuple[Fixture, Path, CardSpec, str]],
                 byes: list[tuple[int, Team]], out: Path) -> None:
    """Each round's cards as the run lists them, under the round's date."""
    bye_by_round: dict[int, list[str]] = {}
    for round_index, team in byes:
        bye_by_round.setdefault(round_index, []).append(team.name)

    current = -1
    for fixture, path, spec, note in written:
        if fixture.round_index != current:
            current = fixture.round_index
            print(f"Round {fixture.round_label}  {fixture.date:%a %d/%m/%Y}")
            for name in bye_by_round.get(current, ()):
                print(f"  bye: {name} - no card is written for a bye, so the "
                      f"round is one fixture short on purpose")
        print(f"  court {fixture.court:02d}  {fixture.home.name} v "
              f"{fixture.away.name}  {score_summary(spec)}")
        print(f"            {path.relative_to(out)}")
        if note:
            label = fixture.flavour if fixture.flavour != NORMAL else "roster"
            print(f"            {label}: {note}")


def print_byes(teams: int, byes: int, config: str) -> None:
    """What a bye is to the build, where the draw has any.

    ``config`` is the file the line printed next has the build read, which is where
    a declared team list would have to go: ``config.toml`` for the plain demo, and
    :data:`ARCHIVE_CONFIG` for two seasons, whose line never reads ``config.toml``.
    That one the demo writes again each run, so a list typed into it would be gone
    the next time: it is named as declaring none, and not as where to put one.
    """
    if byes:
        told = (f"with no team list declared in {config} that is a warning, not a "
                f"scored bye (F1)" if config == ARCHIVE_CONFIG else
                f"with a derived team list that is a warning, and only a declared "
                f"team list in {config} turns it into a scored bye (F1)")
        print(f"{teams} teams is odd, so every round has a bye "
              f"({byes} in total). The team resting has no card: {told}.")


def print_replace(out: Path, written: list[tuple[Fixture, Path, CardSpec, str]]
                  ) -> None:
    """How to replace these cards, and how to be rid of them.

    Said every time, not only on request: a fresh clone ships data/ empty and is
    tested for it, so anyone who leaves these cards behind will see that test fail
    without knowing why. --clean is named as the way to *replace* them, because
    that is what it does -- it removes the old cards and writes new ones.

    "delete {out}" is only safe advice where {out} holds nothing else, and this
    script can be pointed straight at data/. So the directory is named only when
    emptying it would cost nothing; where a club's own files are in there, the
    sentence says so instead, since that is the more useful thing to be told.
    """
    mine = {path for _, path, _, _ in written}
    strangers = [p for p in sorted(out.rglob("*")) if p.is_file() and p not in mine]
    if strangers:
        print(f"to replace these cards, rerun with --clean: it removes only cards "
              f"this script wrote, and the {len(strangers)} other file(s) under "
              f"{out} are not its to touch")
    else:
        print(f"to replace these cards, rerun with --clean; to remove them, "
              f"delete {out}")


# --- two seasons --------------------------------------------------------------


def archive_refusal(out: Path) -> str | None:
    """Why ``--seasons multi`` may not write into ``out``, or None when it may.

    The line it prints next builds everything under ``out`` with
    :data:`ARCHIVE_CONFIG`, and not with the club's ``config.toml``, so ``out`` may
    hold nothing of the club's. Measured before this was asked: a club with cards
    in ``data/2025-winter`` and ``players = "numbered"`` ran ``--out data``, pasted
    the line, and published the names its own build withheld; a club keeping its
    cards outside ``data/`` did the same with ``--out`` pointed at them.

    So ``out`` may not be ``data/``, inside it or around it, because ``data/`` is
    where a club's cards go and what its own build reads: inside, the two seasons
    also built as one, and around it, ``data/`` was read as a third season. And
    anywhere else it may not hold a PDF this script did not write, told apart as
    :func:`clean` tells them apart. One reason is given, the first found. Whether
    ``out`` is ``data/`` or inside it is :func:`within_data`'s answer, the one
    :func:`next_command` gives its line by, so the two agree where it is.
    """
    data = (ROOT / "data").resolve()
    folder = out.resolve()
    inside = within_data(out)
    if inside is not None or data.is_relative_to(folder):
        where = ("is" if inside == Path(".") else
                 "is inside" if inside is not None else "holds")
        return (f"--out {out} {where} {data}, where a club's own cards go and "
                f"what its own build reads. The line printed next builds "
                f"everything under --out with {ARCHIVE_CONFIG} and not your "
                f"config, so the two seasons are not written there. Give --out a "
                f"folder of their own outside it, or leave --out off for "
                f"{DEFAULT_ARCHIVE}.")
    theirs = sort_files(folder).foreign
    if theirs:
        return (f"--out {out} holds {len(theirs)} scorecard(s) this script did "
                f"not write, the first {theirs[0]}. The line printed next builds "
                f"everything under --out with {ARCHIVE_CONFIG} and not your "
                f"config, which would publish any name your config withholds. "
                f"Give --out a folder of their own.")
    return None


def write_archive(args: argparse.Namespace, teams: tuple[Team, ...], rounds: int,
                  fmt: MatchFormat, rng: random.Random) -> int:
    """``--seasons multi``: :data:`SEASONS` under ``--out``, and a config for them.

    Each season is written as the plain demo writes its one, into a folder named
    by its key, and from the same generator in turn, so that the first is the plain
    demo's season byte for byte. Between the two, :func:`change_roster`. The config
    is :data:`ARCHIVE_CONFIG` beside ``build.py``, and not the club's
    ``config.toml``, which is not this script's to change: it says ``seasons =
    "multi"`` and nothing else, and it is written again each run, so the line
    printed next is right even after somebody edited it. The line is
    :func:`next_command`'s with the config after it.
    """
    out = args.out
    print(f"{CLUB_NAME} - {args.day} Division {args.division}, "
          f"{in_words(season.label for season in SEASONS)}")
    print(f"{len(teams)} teams, {len(teams) * PLAYERS_PER_TEAM} players a season, "
          f"format {fmt.name} ({fmt.set_rule.describe()})")
    print(f"seed {args.seed} - rerunning with it rewrites the same bytes")

    written: list[tuple[Fixture, Path, CardSpec, str]] = []
    byes = 0
    for number, season in enumerate(SEASONS):
        print()
        print(f"{season.label}, in {season.folder}/")
        if number:
            teams, leaver, joiner = change_roster(teams, rng)
            print(f"  {teams[0].name}: {leaver.name} has left and {joiner.name} "
                  f"has joined, so each has a career of one season")
        fixtures, rested = build_fixtures(teams, rounds, args.day, season)
        stand_ins, unavailable = plan_loan(fixtures, rested, teams)
        cards = write_season(fixtures, fmt, out / season.folder, args.division,
                             args.day, rng, stand_ins, unavailable)
        print_rounds(cards, rested, out)
        written += cards
        byes += len(rested)

    (ROOT / ARCHIVE_CONFIG).write_text(ARCHIVE_CONFIG_TEXT, encoding="utf-8")
    print()
    print(f"wrote {len(written)} scorecard(s) under {out}, a folder a season")
    print(f"wrote {ARCHIVE_CONFIG} beside build.py, which has the build read each "
          f"of those folders as a season; delete it with the cards")
    print_byes(len(teams), byes, ARCHIVE_CONFIG)
    print(f"next: {next_command(out)} --config {pasteable(ARCHIVE_CONFIG)}")
    print_replace(out, written)
    return 0


# --- command line -----------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(
        # `prog` spelled out, because argparse would take it from argv[0] and print
        # `usage: demo.py` -- which is not a command that works from anywhere the
        # README tells a club to stand.
        prog="tools/demo.py",
        description="Write a fictional club's season of scorecard PDFs into data/.")
    parser.add_argument("--teams", type=int, default=6,
                        help="teams in the division (odd gives every round a bye)")
    parser.add_argument("--rounds", type=int, default=0,
                        help="rounds to write (default: one full round robin)")
    parser.add_argument("--format", default=DEFAULT_FORMAT,
                        choices=sorted(FORMATS),
                        help=f"match format the cards describe: "
                             f"{in_words(sorted(FORMATS), 'or')}")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"directory to write into (default {DEFAULT_OUT}, "
                             f"or {DEFAULT_ARCHIVE} with --seasons multi)")
    parser.add_argument("--seasons", default="single", choices=SEASON_MODES,
                        help=f"{in_words(SEASON_MODES, 'or')}: multi writes two "
                             f"seasons, a folder each, and the config that builds "
                             f"them as two, for the careers page")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="RNG seed; the same seed writes byte-identical PDFs")
    parser.add_argument("--day", default="Wednesday", choices=WEEKDAYS,
                        help=f"the night the competition is played: "
                             f"{in_words(WEEKDAYS, 'or')}")
    parser.add_argument("--division", default="3",
                        help="division as it is printed on the card")
    parser.add_argument("--clean", action="store_true",
                        help="remove a previous demo from --out first")
    parser.add_argument("--version", action="version", version=version_line(),
                        help="print this build and the Python running it, then "
                             "stop")
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = DEFAULT_ARCHIVE if args.seasons == "multi" else DEFAULT_OUT
    return args


def next_command(out: Path) -> str:
    """The exact command to run next, honouring a non-default --out.

    And honouring where this run was standing. The cards go next to ``build.py``
    whatever the working directory is, and the command that reads them names
    ``build.py`` with no path in front of it, so from anywhere else it was a
    command that cannot run: at best `can't open file 'build.py'`, and on a machine
    with a second checkout in it, a different project's build. The ``cd`` is added
    rather than the path spelled into the command, because a club that keeps the
    command will want the one it can also type again tomorrow.

    The folder is resolved here, against the working directory, and not left as it
    was typed. The command runs from the project, so a relative ``--out`` from
    anywhere else named a folder the build then looked for inside the project:
    `--out relseason` from another folder printed ``--data relseason`` and pasted to
    `error: no such directory: relseason`. A folder inside the project is named from
    the project, as the default is, and one outside it in full. Both paths are
    quoted by the build's own rule, because a space in either split the pasted
    line in two: `unrecognized arguments: season`, or `can't cd to .../my` for a
    project under ``my club``. The folder is named by ``pasteable_path``, as the
    build's hints name theirs, which also gives a name that begins with a dash
    its ``./``. ``tools/fetch.py`` prints its line from here too, so the folder
    is resolved in one place for both.
    """
    if within_data(out) is not None:
        command = "python3 build.py"
    else:
        command = f"python3 build.py --data {pasteable_path(out, project=ROOT)}"
    if Path.cwd().resolve() == ROOT:
        return command
    # Not only for the bare command above: `--data` may already be absolute and the
    # run still cannot find `build.py` from here.
    return f"cd {pasteable(str(ROOT))} && {command}"


def within_data(folder: Path | str) -> Path | None:
    """Where *folder* is inside the project's ``data/``, or None when it is not.

    Asked of the folder each is resolved to, so that a link to ``data/`` and the
    folder ``data/`` links to are both ``data/``. ``next_command`` leaves out
    ``--data`` for any folder inside it, and ``tools/fetch.py`` gives its note for
    ``data/`` itself; the two answered with ``resolve`` and ``os.path.abspath``,
    and a link was data/ to one and not to the other, measured: ``--into`` a link
    to ``data/`` printed the line for ``data/`` without the note that goes with it.
    """
    folder, data = Path(folder).resolve(), (ROOT / "data").resolve()
    return folder.relative_to(data) if folder.is_relative_to(data) else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fmt = match_format(args.format)
    rng = random.Random(args.seed)

    try:
        teams = make_teams(args.teams, rng)
    except ValueError as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 2

    full_draw = len(round_robin(len(teams)))
    rounds = args.rounds if args.rounds > 0 else full_draw
    if rounds > 2 * full_draw:
        print(f"demo: --rounds {rounds} is more than two passes of a "
              f"{len(teams)}-team draw ({2 * full_draw})", file=sys.stderr)
        return 2

    out = args.out
    refused = archive_refusal(out) if args.seasons == "multi" else None
    if refused:
        print(f"demo: {refused}", file=sys.stderr)
        return 2
    if args.clean:
        cleaned = clean(out)
        print(f"cleaned {len(cleaned.removed)} card(s) from {out}")
        if cleaned.foreign:
            print(f"  kept {len(cleaned.foreign)} scorecard(s) not written by this "
                  f"script; a demo flag does not delete a club's own cards:")
            for path in cleaned.foreign:
                print(f"    {path}")
        for path in cleaned.other:
            print(f"  left alone (not a PDF): {path}")

    if args.seasons == "multi":
        return write_archive(args, teams, rounds, fmt, rng)

    fixtures, byes = build_fixtures(teams, rounds, args.day)
    stand_ins, unavailable = plan_loan(fixtures, byes, teams)
    written = write_season(fixtures, fmt, out, args.division, args.day, rng,
                           stand_ins, unavailable)

    print(f"{CLUB_NAME} - {args.day} Division {args.division}, {SEASON_LABEL}")
    print(f"{len(teams)} teams, {len(teams) * PLAYERS_PER_TEAM} players, "
          f"format {fmt.name} ({fmt.set_rule.describe()})")
    print(f"seed {args.seed} - rerunning with it rewrites the same bytes")
    print()
    print_rounds(written, byes, out)
    print()
    print(f"wrote {len(written)} scorecard(s) under {out}")
    print_byes(len(teams), len(byes), DEFAULT_PATH)
    print(f"next: {next_command(out)}")
    print_replace(out, written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
