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

from matchcentre.cardwriter import (AUTO, NO_TICK, CardSpec, RosterRow, SetRow,
                                    write_card_file)
from matchcentre.format import (DEFAULT_FORMAT, FORMATS, MatchFormat, SetRule,
                                completed, match_format, plausible)
from matchcentre.model import SetStatus
from matchcentre.slugs import team_slug

# --- the invented club ------------------------------------------------------

CLUB_NAME = "Brindle Creek Tennis Club"
CLUB_PREFIX = "BRINDLE"          # what a club would set as strip_team_prefix
CLUB_LINES = ("BRINDLE CREEK", "TENNIS CLUB INC")
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
        players = [Player(name=name,
                          singles=round(rng.uniform(RATING_LOW, RATING_HIGH), 2),
                          doubles=round(rng.uniform(RATING_LOW, RATING_HIGH), 2))
                   for name in pool[start:start + PLAYERS_PER_TEAM]]
        full = f"{CLUB_PREFIX} {nickname}"
        teams.append(Team(name=full,
                          slug=team_slug(full, strip_prefix=CLUB_PREFIX),
                          players=tuple(players)))
    return tuple(teams)


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


def first_playing_date(day: str) -> datetime.date:
    """The first ``day`` on or after :data:`SEASON_START`.

    The card's date is the only source of the night a competition is played on,
    so the dates written here have to actually fall on the requested weekday.
    """
    wanted = WEEKDAYS.index(day)
    ahead = (wanted - SEASON_START.weekday()) % 7
    return SEASON_START + datetime.timedelta(days=ahead)


def build_fixtures(teams: tuple[Team, ...], rounds: int, day: str
                   ) -> tuple[list[Fixture], list[tuple[int, Team]]]:
    """Every fixture to write, plus the (round index, team) pairs that have a bye."""
    schedule = round_robin(len(teams))
    start = first_playing_date(day)

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
    match_id = FIRST_MATCH_ID
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
                match_id=str(match_id)))
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


def score_rows(flavour: str, slots: int, rule: SetRule,
               rng: random.Random) -> list[tuple[int, int]]:
    """Per-row ``(home games, away games)`` for one match, all legal under ``rule``."""
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
        # played, awarding the set to whoever turned up, and both
        # `model.SetResult.winner` and the default `forfeit_award = "as-played"`
        # read the printed games to find that side. Writing 0-0 here would make
        # every forfeited set a draw and put the card's own result verb in
        # contradiction with its rows.
        return [(widest[1], widest[0])] * slots

    if flavour == WASHOUT:
        return [(0, 0)] * slots       # nothing was played, so nothing is credited

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
    scores = score_rows(fixture.flavour, len(fmt.slots), fmt.set_rule, rng)
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
    if fixture.flavour == FORFEIT:
        status, status_side = SetStatus.FORFEIT, "home"
        notes.append(f"{fixture.home.name} forfeited; every set is ticked "
                     f"Forfeit against them and awarded as played")
    elif fixture.flavour == WASHOUT:
        status, status_side = SetStatus.INCOMPLETE, "both"
        notes.append("washed out, no sets played")

    rows = [SetRow(home_games=home_games, away_games=away_games,
                   doubles=slot.doubles, home_players=home, away_players=away,
                   status=status, status_side=status_side)
            for slot, (home_games, away_games), home, away
            in zip(fmt.slots, scores, home_names, away_names)]

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
        season_line=SEASON_LABEL,
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


def clean(out: Path) -> tuple[int, list[Path]]:
    """Delete a previous demo under ``out``. Returns (cards removed, kept files).

    Only PDFs are removed, and anything else found is reported and left alone: a
    club that pointed ``--out`` at its real ``data/`` directory must not lose a
    scorecard to a demo flag.
    """
    if not out.exists():
        return 0, []
    removed = 0
    kept: list[Path] = []
    for path in sorted(out.rglob("*")):
        if path.is_file():
            if path.suffix.lower() == ".pdf":
                path.unlink()
                removed += 1
            else:
                kept.append(path)
    if not kept:
        for path in sorted(out.rglob("*"), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        if not any(out.iterdir()):
            out.rmdir()
    return removed, kept


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


# --- command line -----------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a fictional club's season of scorecard PDFs into data/.")
    parser.add_argument("--teams", type=int, default=6,
                        help="teams in the division (odd gives every round a bye)")
    parser.add_argument("--rounds", type=int, default=0,
                        help="rounds to write (default: one full round robin)")
    parser.add_argument("--format", default=DEFAULT_FORMAT,
                        choices=sorted(FORMATS),
                        help="match format the cards describe")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"directory to write into (default {DEFAULT_OUT})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="RNG seed; the same seed writes byte-identical PDFs")
    parser.add_argument("--day", default="Wednesday", choices=WEEKDAYS,
                        help="the night the competition is played")
    parser.add_argument("--division", default="3",
                        help="division as it is printed on the card")
    parser.add_argument("--clean", action="store_true",
                        help="remove a previous demo from --out first")
    return parser.parse_args(argv)


def next_command(out: Path) -> str:
    """The exact command to run next, honouring a non-default --out."""
    data = ROOT / "data"
    try:
        out.resolve().relative_to(data.resolve())
    except ValueError:
        return f"python3 build.py --data {out}"
    return "python3 build.py"


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
    if args.clean:
        removed, kept = clean(out)
        print(f"cleaned {removed} card(s) from {out}")
        for path in kept:
            print(f"  left alone (not a PDF): {path}")

    fixtures, byes = build_fixtures(teams, rounds, args.day)
    stand_ins, unavailable = plan_loan(fixtures, byes, teams)
    written = write_season(fixtures, fmt, out, args.division, args.day, rng,
                           stand_ins, unavailable)

    print(f"{CLUB_NAME} - {args.day} Division {args.division}, {SEASON_LABEL}")
    print(f"{len(teams)} teams, {len(teams) * PLAYERS_PER_TEAM} players, "
          f"format {fmt.name} ({fmt.set_rule.describe()})")
    print(f"seed {args.seed} - rerunning with it rewrites the same bytes")
    print()

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

    print()
    print(f"wrote {len(written)} scorecard(s) under {out}")
    if byes:
        print(f"{len(teams)} teams is odd, so every round has a bye "
              f"({len(byes)} in total). The team resting has no card: with a "
              f"derived team list that is a warning, and only a declared team "
              f"list in config.toml turns it into a scored bye (F1).")
    print(f"next: {next_command(out)}")
    # Said every time, not only on request: a fresh clone ships data/ empty and is
    # tested for it, so anyone who leaves these cards behind will see that test
    # fail without knowing why. --clean is named as the way to *replace* them,
    # because that is what it does -- it removes the old cards and writes new ones.
    print(f"to replace these cards, rerun with --clean; to remove them, "
          f"delete {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
