"""A whole competition, from PDFs on disk to the season document.

The other suites test modules. This one tests the *seams*, which is where the
predecessor's expensive bugs lived: not inside the parser or inside the ladder, but
in the handover — a card routed to the wrong competition, a team list closed when it
should have been open, a bye materialised from a gap in the evidence.

The competition below is small enough to check by hand and is checked by hand: six
cards, four teams, three complete rounds, every figure worked out on paper from the
``games-bonus`` definition (win 4, draw 3, loss 2, plus a tenth per game won) and
written into :data:`EXPECTED_POINTS` rather than recomputed from the code under
test. A test that derives its expectation from the thing it is testing proves only
that the code agrees with itself.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import NamedTuple
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matchcentre import (cardwriter as cw, config as config_mod,      # noqa: E402
                         discover as discover_mod, export, format as fmt_mod,
                         scorecard, scoring, slugs, stats, validate)
from matchcentre.competition import (DRAW_DECLARED, DRAW_FROM_CARDS,  # noqa: E402
                                     Competition, Provenance, Round, Team)
from matchcentre.model import (NO_OPPONENT, Match, MatchStatus,       # noqa: E402
                               SetResult, SetStatus, forfeit_match_winner)
from matchcentre import sheet as sheet_mod                      # noqa: E402
from matchcentre import card as card_mod                        # noqa: E402
from matchcentre.card import Card                               # noqa: E402

# Four invented clubs. No prefix in common, so team slugs need no stripping and
# the identity path under test is the plain one.
TEAMS = {
    "riverside-rockets": ("RIVERSIDE ROCKETS",
                          ("Ana Fernandez", "Bo Nakamura", "Cass Ellery")),
    "hilltop-aces": ("HILLTOP ACES",
                     ("Dev Raman", "Elin Sotto", "Fay Bramwell")),
    "lakeside-spinners": ("LAKESIDE SPINNERS",
                          ("Gil Ahearn", "Hana Wex", "Ilya Modric")),
    "parkview-wanderers": ("PARKVIEW WANDERERS",
                           ("Jo Calder", "Kit Vasse", "Lena Ordoy")),
}

# Round -> date. All Tuesdays, because routing is on the weekday of the card's own
# date and every card here belongs to the same competition.
DATES = {"1": "2026-08-18", "2": "2026-08-25", "3": "2026-09-01"}

# (round, home, away, per-set (home, away) games). Chosen so that no two teams
# finish level, one match is a genuine draw, and every team plays three matches --
# which is what makes the ladder complete under F10.
FIXTURES = (
    ("1", "riverside-rockets", "hilltop-aces",
     ((6, 1), (6, 2), (6, 0), (6, 3))),                  # 24-6,  sets 4-0
    ("1", "lakeside-spinners", "parkview-wanderers",
     ((6, 4), (6, 3), (3, 6), (6, 2))),                  # 21-15, sets 3-1
    ("2", "riverside-rockets", "lakeside-spinners",
     ((6, 3), (6, 4), (4, 6), (6, 2))),                  # 22-15, sets 3-1
    ("2", "hilltop-aces", "parkview-wanderers",
     ((6, 3), (3, 6), (6, 4), (4, 6))),                  # 19-19, sets 2-2, a draw
    ("3", "riverside-rockets", "parkview-wanderers",
     ((6, 2), (6, 1), (6, 0), (6, 4))),                  # 24-7,  sets 4-0
    ("3", "hilltop-aces", "lakeside-spinners",
     ((2, 6), (6, 4), (1, 6), (3, 6))),                  # 12-22, sets 1-3
)

# Worked out on paper, per match, then summed:
#
#   rockets    win 24 -> 4+2.4=6.4 | win 22 -> 6.2 | win 24 -> 6.4  = 19.0
#   spinners   win 21 -> 4+2.1=6.1 | loss 15 -> 2+1.5=3.5 | win 22 -> 6.2 = 15.8
#   wanderers  loss 15 -> 3.5 | draw 19 -> 3+1.9=4.9 | loss 7 -> 2+0.7=2.7 = 11.1
#   aces       loss 6 -> 2+0.6=2.6 | draw 19 -> 4.9 | loss 12 -> 2+1.2=3.2 = 10.7
EXPECTED_POINTS = {
    "riverside-rockets": 19.0,
    "lakeside-spinners": 15.8,
    "parkview-wanderers": 11.1,
    "hilltop-aces": 10.7,
}
EXPECTED_LADDER = ["riverside-rockets", "lakeside-spinners",
                   "parkview-wanderers", "hilltop-aces"]
EXPECTED_GAMES_FOR = {"riverside-rockets": 70, "lakeside-spinners": 58,
                      "parkview-wanderers": 41, "hilltop-aces": 37}


def rows_for(scores, home_players, away_players):
    """Four rows, doubles at the ends, in the shape a club card prints."""
    pattern = (True, False, False, True)
    built = []
    singles = 0
    for (home, away), pair in zip(scores, pattern):
        if pair:
            home_names, away_names = home_players[:2], away_players[:2]
        else:
            home_names = (home_players[singles],)
            away_names = (away_players[singles],)
            singles += 1
        built.append(cw.SetRow(home_games=home, away_games=away, doubles=pair,
                               home_players=home_names,
                               away_players=away_names))
    return tuple(built)


class Swap(NamedTuple):
    """One roster row printed under a name another team's roster block carries.

    ``position`` picks which of the three players it is, and the choice is the
    whole point: position 1 is on court every week in this generator, so a swap
    there produces a merge with four rounds of results behind it, while position 2
    is rostered and never plays, which is a merge no set row can betray.

    ``rating`` is the other half of the evidence. Two people of different
    standards printed under one name leave two ratings on the cards, and only one
    of them can survive a merge.
    """

    position: int
    name: str
    rating: float = 2.5


def squad_for(team: str, players: tuple[str, ...],
              rename: dict[str, Swap] | None = None
              ) -> tuple[tuple[cw.RosterRow, ...], tuple[str, ...]]:
    """A team's roster block and its on-court names, as one card prints them."""
    swap = (rename or {}).get(team)
    rows, names = [], []
    for index, printed in enumerate(players):
        renamed = swap is not None and index == swap.position
        name = swap.name if renamed else printed
        rating = swap.rating if renamed else 2.5
        names.append(name)
        rows.append(cw.RosterRow(name=name, singles=rating, doubles=rating,
                                 captain=(index == 0)))
    return tuple(rows), tuple(names)


def write_season_cards(data_dir: Path, *,
                       rename: dict[str, Swap] | None = None,
                       banners: dict[str, str] | None = None) -> list[Path]:
    """One PDF per fixture, in per-round folders, as a club would export them.

    ``banners`` prints a team under another name, by its key in ``TEAMS``.
    """
    written = []
    for index, (rnd, home, away, scores) in enumerate(FIXTURES, start=1):
        folder = data_dir / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_name = (banners or {}).get(home, home_name)
        away_name = (banners or {}).get(away, away_name)
        home_rows, home_on_court = squad_for(home, home_players, rename)
        away_rows, away_on_court = squad_for(away, away_players, rename)
        spec = cw.CardSpec(
            date=DATES[rnd], round_label=rnd, division="B",
            court=f"{index:02d}", match_id=f"200{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_on_court, away_on_court),
            home_roster=home_rows,
            away_roster=away_rows,
        )
        written.append(cw.write_card_file(
            folder / f"{home_name.title().replace(' ', '')}-"
                     f"{away_name.title().replace(' ', '')}.pdf", spec))
    return written


class SeasonCase(unittest.TestCase):
    """Builds the season once for the whole class; nothing here mutates it."""

    DECLARE_TEAMS = False

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / "data"
        write_season_cards(cls.data)
        cls.extra_cards()

        cls.cards, cls.rejected = scorecard.read_tree(cls.data)
        cls.config = cls.make_config()
        cls.discovery = discover_mod.discover(cls.cards, cls.rejected,
                                              config=cls.config, root=cls.data)
        cls.comp_id, = sorted(cls.discovery.matches)
        cls.matches = cls.discovery.matches[cls.comp_id]
        cls.competition = next(c for c in cls.discovery.club.competitions
                               if c.id == cls.comp_id)
        # The mode handed on as build.py hands it on, so a class that sets it is
        # told what a club in that mode is told.
        cls.report = validate.validate(cls.matches, cls.competition,
                                       club=cls.discovery.club,
                                       cards=cls.cards, rejected=cls.rejected,
                                       seasons_mode=cls.config.seasons_mode)
        cls.season = export.build_season(
            cls.matches, competition=cls.competition, source_dir=cls.data,
            generated="2026-09-10T12:00:00", club=cls.discovery.club,
            cards=cls.cards, rejected=cls.rejected,
            seasons_mode=cls.config.seasons_mode)

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml")

    @classmethod
    def extra_cards(cls):
        """Cards beyond the three-round season, for a subclass that needs them."""

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestEveryCardIsReadAndRouted(SeasonCase):

    def test_all_six_cards_are_read_and_none_rejected(self):
        self.assertEqual(len(self.cards), len(FIXTURES))
        self.assertEqual(self.rejected, [])

    def test_they_all_land_in_one_competition(self):
        # Same weekday, same division, same season: one ladder. The predecessor
        # split or merged competitions by filename and got both wrong.
        self.assertEqual(len(self.discovery.matches), 1)
        self.assertEqual(len(self.matches), len(FIXTURES))

    def test_the_competition_id_says_what_it_is(self):
        self.assertIn("tuesday", self.comp_id)
        self.assertIn("b", self.comp_id)

    def test_the_rounds_are_the_three_the_cards_printed(self):
        self.assertEqual(stats.rounds_present(self.matches), ["1", "2", "3"])

    def test_the_four_teams_are_discovered_by_slug(self):
        self.assertEqual(sorted(self.competition.teams), sorted(TEAMS))


class TestTheLadderIsWhatHandArithmeticSays(SeasonCase):

    def test_each_team_scores_the_points_worked_out_on_paper(self):
        for slug, expected in EXPECTED_POINTS.items():
            with self.subTest(team=slug):
                ledger = stats.points_ledger(self.matches, slug)
                self.assertAlmostEqual(ledger.total, expected, places=6)

    def test_nothing_was_averaged_because_every_match_was_played(self):
        # `average_unplayed` exists for genuinely unplayed matches. If it fired
        # here, a played match had been read as unplayed -- the FAST4 failure.
        for slug in EXPECTED_POINTS:
            with self.subTest(team=slug):
                self.assertEqual(stats.points_ledger(self.matches,
                                                     slug).averaged_matches, 0)

    def test_the_ladder_order_is_the_expected_one(self):
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)

    def test_games_won_match_the_sum_of_the_printed_sets(self):
        table = stats.team_stats(self.matches, self.competition)
        for slug, expected in EXPECTED_GAMES_FOR.items():
            with self.subTest(team=slug):
                self.assertEqual(table[slug].games_won, expected)

    def test_games_won_and_lost_balance_across_the_competition(self):
        # Every game won by one side was lost by the other, so the two columns
        # must total the same. They will not if a set was counted once for the
        # home team and twice, or not at all, for the away team.
        table = stats.team_stats(self.matches, self.competition)
        self.assertEqual(sum(t.games_won for t in table.values()),
                         sum(t.games_lost for t in table.values()))

    def test_the_draw_is_recorded_as_a_draw_for_both_sides(self):
        drawn = [m for m in self.matches if m.is_draw]
        self.assertEqual(len(drawn), 1)
        self.assertEqual(sorted((drawn[0].home, drawn[0].away)),
                         ["hilltop-aces", "parkview-wanderers"])

    def test_the_ladder_is_complete_because_all_teams_played_three(self):
        # F10: only claimable when every team has the same number of counted
        # matches. Here they do, so the order is a real order.
        self.assertTrue(stats.ladder_complete(self.matches, self.competition))
        table = stats.team_stats(self.matches, self.competition)
        self.assertEqual({t.played for t in table.values()}, {3})


class TestNothingIsInventedFromAbsence(SeasonCase):

    def test_no_bye_appears_anywhere(self):
        # F1. Four teams, two matches a round: there is no bye to find, and a
        # derived team list may not invent one even when there is a gap.
        self.assertEqual([m for m in self.matches
                          if m.status is MatchStatus.BYE], [])

    def test_rounds_total_is_null_because_no_draw_was_declared(self):
        # F3. Three rounds have been played; whether the season has ten is not
        # something a scorecard knows, so the dashboard must show no percentage.
        self.assertIsNone(self.season["meta"]["rounds_total"])
        self.assertEqual(self.season["meta"]["rounds_played"], 3)

    def test_the_draw_is_marked_as_coming_from_the_cards(self):
        # F7: provenance travels into the document, so the page can say so.
        self.assertEqual(self.season["meta"]["draw_source"], "cards")

    def test_the_team_list_is_marked_derived_not_declared(self):
        # F7. Nobody declared these four teams; they were found by reading six
        # cards. The document has to say so, because a derived list cannot be
        # known to be complete and the page must not claim otherwise.
        self.assertEqual(self.season["meta"]["provenance"]["teams"], "derived")
        self.assertFalse(self.season["meta"]["teams_declared"])

    def test_what_was_printed_on_the_cards_is_marked_as_coming_from_them(self):
        provenance = self.season["meta"]["provenance"]
        for aspect in ("draw", "format", "players", "season"):
            with self.subTest(aspect=aspect):
                self.assertEqual(provenance[aspect], "card")

    def test_no_finals_round_is_invented_from_a_numeric_label(self):
        # F11. Rounds 1-3 are numeric, so none of them is a final, and all three
        # count towards the ladder.
        self.assertFalse(any(r.is_finals
                             for r in self.competition.rounds.values()))
        self.assertEqual([n for n in self.competition.round_order
                          if not self.competition.is_finals_round(n)],
                         ["1", "2", "3"])


class TestADeclaredDrawIsAsLongAsTheRoundsItLists(SeasonCase):
    """Nine regular rounds declared are nine, whichever of them have cards yet.

    Declaring the draw declares the season's length, and the length was counted off
    the rounds built from it that were still marked as declared. A round the draw
    lists with no fixtures takes its fixtures from its cards once they arrive, and is
    marked as coming from them -- so it stopped counting. Measured on
    /tmp/t4/scratch/i417/fix2 with the six cards of rounds 1 to 3: rounds_total 6,
    and the page read three rounds into a nine-round season as half of it. The count
    is now the draw's own, finals left out, and it is the one ``--discover`` writes
    back when it drops the draw. Rejected: counting the rounds whose fixtures the
    draw lists, which is what the old count came to once each played round had its
    cards, and which asks a club for every fixture to be told how long its season is.
    """

    ROUNDS = 9

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        draw = "".join(f'\n[[competitions.tuesday-b.rounds]]\nnumber = "{n}"\n'
                       for n in range(1, cls.ROUNDS + 1))
        path.write_text(
            '[competitions.tuesday-b]\nday = "Tuesday"\ndivision = "B"\n' + draw
            + '\n[[competitions.tuesday-b.rounds]]\nnumber = "SF"\nfinals = true\n',
            encoding="utf-8")
        return config_mod.load(path)

    def test_three_rounds_played_are_three_of_the_nine_declared(self):
        self.assertEqual(self.comp_id, "tuesday-b")
        self.assertEqual(self.season["meta"]["rounds_played"], 3)
        self.assertEqual(self.season["meta"]["rounds_total"], self.ROUNDS)


def draw_with_a_final(listed: dict[str, list[tuple[str, str]]] | None = None) -> str:
    """Rounds 1, 2 and 3 dated, 3 a final, with each round in *listed* and its fixtures."""
    text = '[competitions.tuesday-b]\nday = "Tuesday"\ndivision = "B"\n'
    for number, date in (*DATES.items(), ("4", "2026-09-08")):
        if number == "4" and listed is None:
            break
        text += (f'\n[[competitions.tuesday-b.rounds]]\nnumber = "{number}"\n'
                 f'date = "{date}"\n' + ("finals = true\n" if number == "3" else ""))
        if listed is not None:
            pairs = [[TEAMS[home][0].title(), TEAMS[away][0].title()]
                     for home, away in listed[number]]
            text += f"fixtures = {json.dumps(pairs)}\n"
    return text


def write_semi_final(data: Path) -> None:
    """A card for a round labelled SF, the Rockets over the Spinners 4-0."""
    home_name, home_players = TEAMS["riverside-rockets"]
    away_name, away_players = TEAMS["lakeside-spinners"]
    (data / "Finals").mkdir(parents=True, exist_ok=True)
    cw.write_card_file(data / "Finals" / "sf.pdf", cw.CardSpec(
        date="2026-09-22", round_label="SF", division="B", court="09",
        match_id="2009999", home_name=home_name, away_name=away_name,
        rows=rows_for(((6, 1), (6, 2), (6, 0), (6, 3)), home_players, away_players),
        home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(home_players)),
        away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(away_players))))


class TestTheProgressBoxsFixturesAreTheRegularRoundsItsRoundCountIs(SeasonCase):
    """The document counts the regular rounds' fixtures for the progress box's foot.

    The box's round count and percentage are of the regular rounds (4.69), and its
    foot counted what the page counted, every card and every fixture, finals and all.
    Measured through ``build.py`` on these cards, a draw dating rounds 1, 2 and 3
    with 3 a final, and this semi-final's card (/tmp/t7/scratch/W1/before.txt):
    exit 0, and the box read ``Round 2 of 2`` over ``7 of 7 fixtures played · 100%
    of the declared rounds``, where the two regular rounds hold 4 of the 7. Now
    ``meta.regular_fixtures`` is those rounds' fixtures and how many were played, 4
    of 4, and the page prints it. The season's own 7 of 7 are still the document's
    ``fixtures``, which the Matches played tile counts.

    Rejected: keeping the season's count in the foot and saying the finals are in
    it. The box names the finals played on a line of their own and counts none of
    them, and a line counting one set of fixtures beside a round count of another is
    two answers in one sentence.
    """

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        path.write_text(draw_with_a_final(), encoding="utf-8")
        return config_mod.load(path)

    @classmethod
    def extra_cards(cls):
        write_semi_final(cls.data)

    def test_the_final_and_the_semi_final_are_in_neither_count(self):
        meta = self.season["meta"]
        # The pre-flight: the draw is declared and dates only, so every fixture row
        # is a card's, and the final and the semi-final are rows of their own.
        self.assertEqual((meta["draw_source"], meta["rounds_played"]), ("declared", 2))
        self.assertEqual(sorted(f["round"] for f in self.season["fixtures"]),
                         ["1", "1", "2", "2", "3", "3", "SF"])
        self.assertEqual(meta.get("regular_fixtures"), {"played": 4, "total": 4})


class TestARegularFixtureIsCountedOnceAndOnlyWhereItWasPlayed(SeasonCase):
    """Of the draw's regular fixtures, the ones a card the season's totals keep was played.

    Played as the page's Matches played tile counts it (4.52's ``counts_as_played``
    and ``not_totalled``), so a night exported twice is one fixture and a washout is
    none, and out of the regular rounds' rows of ``fixtures``, so a fixture the draw
    lists with no card yet is still one of them. Here the draw lists rounds 1 to 4,
    round 3 a final, with round 4's Rockets v Aces washed out and its Spinners v
    Wanderers not yet carded, and round 2's Rockets v Spinners is on two cards: 4 of
    6. The season as a whole is 7 of 9, the finals in. Round 1 lists the Rockets v
    the Aces alone, so the Spinners v the Wanderers' card is a row of its own.

    Rejected: counting the rows ``fixtures`` marks ``played``. A washout's row is
    played, since a card came in for it, and the tile beside the box counts no night
    that counts for nothing.
    """

    DRAW = {"1": [("riverside-rockets", "hilltop-aces")],
            "2": [("riverside-rockets", "lakeside-spinners"),
                  ("hilltop-aces", "parkview-wanderers")],
            "3": [("riverside-rockets", "parkview-wanderers"),
                  ("hilltop-aces", "lakeside-spinners")],
            "4": [("riverside-rockets", "hilltop-aces"),
                  ("lakeside-spinners", "parkview-wanderers")]}

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        path.write_text(draw_with_a_final(cls.DRAW), encoding="utf-8")
        return config_mod.load(path)

    @classmethod
    def extra_cards(cls):
        write_semi_final(cls.data)
        card = cls.data / "Round2" / "RiversideRockets-LakesideSpinners.pdf"
        card.with_name(f"{card.stem} (1).pdf").write_bytes(card.read_bytes())
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        (cls.data / "Round4").mkdir(parents=True, exist_ok=True)
        cw.write_card_file(cls.data / "Round4" / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B", court="09",
            match_id="4000001", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)), home_players,
                          away_players),
            cancelled=True, verb="VS", winner=cw.NO_TICK,
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def build(self) -> dict:
        return export.build_season(
            self.matches, competition=self.competition, source_dir=self.data,
            generated="2026-09-10T12:00:00", club=self.discovery.club,
            cards=self.cards, rejected=self.rejected,
            seasons_mode=self.config.seasons_mode)

    def test_a_copy_is_one_fixture_a_washout_none_and_a_fixture_with_no_card_one_to_play(
            self):
        # The pre-flight: each card arrived as what it is, or the count below cannot
        # tell a copy, a washout or a missing card from a night played.
        self.assertEqual(sorted((m["round"], m["not_totalled"], m["counts_as_played"])
                                for m in self.season["matches"]
                                if m["round"] in {"2", "4", "SF"}),
                         [("2", "", True), ("2", "", True), ("2", "duplicate", True),
                          ("4", "", False), ("SF", "", True)])
        self.assertEqual(sorted((f["round"], f["played"]) for f in self.season["fixtures"]
                                if f["round"] == "4"), [("4", False), ("4", True)])
        self.assertEqual(len(self.season["fixtures"]), 9)
        self.assertEqual(self.season["meta"].get("regular_fixtures"),
                         {"played": 4, "total": 6})

    def test_a_card_the_draw_does_not_list_is_one_of_the_fixtures_it_counts(self):
        """The total is the rows of ``fixtures``, and a card off the draw has one.

        The draw lists 5 regular fixtures, and the Spinners v the Wanderers' round 1
        card, which it does not list, is a sixth row after them, played, as the page's
        tile counts it. A total of the draw's own list is 5 here, and equal to the rows
        on a draw every card is on: so it passed every other test of this item
        (/tmp/mutate293.py's #6 survived them), and it counts that night as played
        and not as a fixture, so a season of such nights reads more played than
        there are.
        """
        self.assertEqual(self.competition.rounds["1"].fixtures,
                         (("riverside-rockets", "hilltop-aces"),))
        self.assertEqual([(f["home"], f["away"], f["played"])
                          for f in self.season["fixtures"] if f["round"] == "1"],
                         [("riverside-rockets", "hilltop-aces", True),
                          ("lakeside-spinners", "parkview-wanderers", True)])
        self.assertEqual((self.season["meta"].get("regular_fixtures") or {}).get("total"),
                         6)

    def test_which_rounds_are_regular_is_the_one_rule_s_answer(self):
        # One rule, one function: asked of `is_finals_round`, the count follows it
        # wherever it says the finals are. Told round 1 is the only final, the
        # regular rows are rounds 2, 3 and 4 and the semi-final's, 7, and 5 of them
        # were played.
        with mock.patch.object(Competition, "is_finals_round", autospec=True,
                               side_effect=lambda self, number: number == "1"):
            season = self.build()
        self.assertEqual(season["meta"].get("regular_fixtures"),
                         {"played": 5, "total": 7})


class TestARoundWithNoCardYetIsStillToPlayAndALevelNightWasPlayed(SeasonCase):
    """A listed round nobody has carded is in the total, and a level night in the played.

    Two other counts passed every test above, and each is wrong beside the meter or
    the tile (/tmp/t7/scratch/W1-review/mymut.log, /tmp/t7/scratch/W1/fix/probe2.txt).
    A total of only the rounds with a card: on a draw listing rounds 1, 2 and 3, with
    no card yet for round 3, the box read ``4 of 4 fixtures played · 67% of the
    declared rounds``, every fixture played under a meter a third short, where it
    reads ``4 of 6``. A played count of the nights the ladder counts,
    ``not_counted``, in place of the nights the season's totals keep: under
    ``draws = false`` round 2's Aces v Wanderers is level, out of the ladder and
    played to the end, and the box read ``5 of 6`` beside a Matches played tile of
    6. Here the draw lists rounds 1 to 4, round 3 a final, and round 4 has no card,
    under ``draws = false``: 4 of 6.

    Rejected: a level night a grade cannot draw counted as not played, as the ladder
    counts it. Its games are in the sets and games tiles below the box, and the tile
    beside them counts it as a match played.
    """

    DRAW = {"1": [("riverside-rockets", "hilltop-aces"),
                  ("lakeside-spinners", "parkview-wanderers")],
            "2": [("riverside-rockets", "lakeside-spinners"),
                  ("hilltop-aces", "parkview-wanderers")],
            "3": [("riverside-rockets", "parkview-wanderers"),
                  ("hilltop-aces", "lakeside-spinners")],
            "4": [("riverside-rockets", "hilltop-aces"),
                  ("lakeside-spinners", "parkview-wanderers")]}

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        path.write_text("[rules]\ndraws = false\n\n" + draw_with_a_final(cls.DRAW),
                        encoding="utf-8")
        return config_mod.load(path)

    def test_the_round_nobody_has_carded_is_in_the_total(self):
        # The pre-flight: round 4 is listed, with two fixtures and no card, so it is
        # not one of the rounds played, and it is one of the season's length.
        meta = self.season["meta"]
        self.assertEqual((meta["draw_source"], meta["rounds_played"],
                          meta["rounds_total"]), ("declared", 2, 3))
        self.assertEqual([m["key"] for m in self.season["matches"]
                          if m["round"] == "4"], [])
        self.assertEqual([(f["round"], f["played"]) for f in self.season["fixtures"]
                          if f["round"] == "4"], [("4", False), ("4", False)])
        self.assertEqual((meta.get("regular_fixtures") or {}).get("total"), 6)

    def test_the_level_night_the_ladder_leaves_out_is_one_of_the_played(self):
        # The pre-flight: the level night is out of the ladder and in the totals.
        self.assertEqual([(m["not_counted"], m["not_totalled"], m["counts_as_played"])
                          for m in self.season["matches"]
                          if (m["round"], m["home"]) == ("2", "hilltop-aces")],
                         [("level", "", True)])
        self.assertEqual(
            (self.season["meta"].get("regular_fixtures") or {}).get("played"), 4)


class TestTheRegularFixturesAreCountedWhateverOrderTheCardsArriveIn(SeasonCase):
    """Each card is counted beside its own reason, not the one at its place in the list.

    ``build_season`` puts what it is handed in the season's order and works out
    every reason for that list, so the count has to be of that list too. Of the
    reading order it pairs each card with another card's reason: a copy is counted
    because its reason sits against a final, which is counted out anyway. Every
    season the item's tests build hands the exporter the season's order, since
    discovery sorts each competition's cards by round, date and teams however the
    folder names them, and so that count passed all of them (/tmp/mutate325.py's
    #1). Here round 1's Rockets v Aces is on two cards, round 3 is a final and the
    semi-final is played, and the cards arrive newest first, as the tests above
    that hand the exporter the reverse of the read order do: 4 of 4, where that
    count reads 5 of 4.

    Rejected: sorting the list in ``_regular_fixtures`` as well, a second sort of
    the same cards that could fall out of step with the first.
    """

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        path.write_text(draw_with_a_final(), encoding="utf-8")
        return config_mod.load(path)

    @classmethod
    def extra_cards(cls):
        write_semi_final(cls.data)
        card = cls.data / "Round1" / "RiversideRockets-HilltopAces.pdf"
        card.with_name(f"{card.stem} (1).pdf").write_bytes(card.read_bytes())

    def test_newest_first_the_count_is_the_one_in_the_season_s_order(self):
        newest_first = list(reversed(self.matches))
        # The pre-flight: the copy's reason is at the third place of the season's
        # order, and handed newest first a final's card is at the third place, so a
        # reason read by place would count the copy and set the final aside.
        totals = stats.not_totalled_reasons(self.matches)
        self.assertEqual(totals.index("duplicate"), 2)
        self.assertEqual(newest_first[2].round, "3")
        self.assertEqual(self.season["meta"].get("regular_fixtures"),
                         {"played": 4, "total": 4})
        season = export.build_season(
            newest_first, competition=self.competition, source_dir=self.data,
            generated="2026-09-10T12:00:00", club=self.discovery.club,
            cards=self.cards, rejected=self.rejected,
            seasons_mode=self.config.seasons_mode)
        self.assertEqual(season["meta"].get("regular_fixtures"),
                         {"played": 4, "total": 4})


class TestValidationPassesOnACleanSeason(SeasonCase):

    def test_there_are_no_errors(self):
        self.assertEqual([i.message for i in self.report.errors], [])
        self.assertTrue(self.report.ok)

    def test_every_card_is_accounted_for(self):
        # F4 as a number: cards read, cards scored, and no rejections.
        meta = self.season["meta"]
        self.assertEqual(meta["cards_scored"], len(FIXTURES))
        self.assertEqual(meta["cards_rejected"], 0)

    def test_a_warning_about_a_derived_team_list_is_expected_and_specific(self):
        # Warnings are not failures here -- a derived team list *should* say so.
        # What matters is that each one names its rule, so it can be acted on.
        for issue in self.report.warnings:
            with self.subTest(rule=issue.rule):
                self.assertTrue(issue.rule)
                self.assertTrue(issue.message.strip())


class TestACardThatPrintsVSInsteadOfAResult(SeasonCase):
    """The card holds no result. It is still read, still scored, still counted.

    Match Centre prints ``VS`` between the team names for a fixture it has no
    result for, and 3 of 22 real exported cards did. The result is on those cards
    anyway — in the set rows, which are what this program scores from — so all
    three were counted, and the run nonetheless reported "result verb 'VS' is not
    one this program recognises". That is a true sentence about a check and a
    misleading one about a card: a club reading it looks for a match it believes
    went missing.

    Three cards are added here for the three ways a card can leave the verb check
    unrunnable, because the value is in the *separation*: a fixture with no result
    printed, a word this program genuinely does not know, and a match nobody
    played, whose cancellation is already reported once and must not be reported
    twice.
    """

    EXTRA = (
        # round, date, home, away, per-set games, printed verb, cancelled
        ("4", "2026-09-08", "riverside-rockets", "lakeside-spinners",
         ((6, 2), (6, 3), (4, 6), (6, 1)), "VS", False),
        ("5", "2026-09-15", "hilltop-aces", "parkview-wanderers",
         ((6, 4), (6, 2), (3, 6), (6, 3)), "TROUNCES", False),
        ("6", "2026-09-22", "lakeside-spinners", "parkview-wanderers",
         ((0, 0), (0, 0), (0, 0), (0, 0)), "VS", True),
        # The away verb exactly as the real exports print it, punctuation and all.
        ("7", "2026-09-29", "hilltop-aces", "lakeside-spinners",
         ((2, 6), (3, 6), (6, 4), (1, 6)), "DEF. BY", False),
    )

    @classmethod
    def extra_cards(cls):
        cls.paths = {}
        for index, (rnd, date, home, away, scores, verb, cancelled) in enumerate(
                cls.EXTRA, start=1):
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            folder = cls.data / f"Round{rnd}"
            folder.mkdir(parents=True, exist_ok=True)
            spec = cw.CardSpec(
                date=date, round_label=rnd, division="B",
                court=f"1{index}", match_id=f"300{index:04d}",
                home_name=home_name, away_name=away_name,
                rows=rows_for(scores, home_players, away_players),
                verb=verb, cancelled=cancelled,
                # A real card printing VS carries no Match Winner tick either: the
                # two absences were measured together on every one of them.
                winner=cw.NO_TICK if verb == "VS" else cw.AUTO,
                home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(home_players)),
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players)))
            cls.paths[rnd] = cw.write_card_file(folder / f"round{rnd}.pdf", spec)

    def match_in(self, round_label):
        found = [m for m in self.matches if m.round == round_label]
        self.assertEqual(len(found), 1)
        return found[0]

    def verb_issues_for(self, round_label):
        """Every finding whose rule is about the verb, for one round's card."""
        path = str(self.paths[round_label])
        return [i for i in self.report.issues
                if i.rule.startswith("verb") and path in i.subject]

    # --- the card is scored ------------------------------------------------

    def test_the_result_is_worked_out_from_the_set_rows(self):
        # Games 22-12, sets 3-1: a home win, and nothing about the banner changes
        # that. This is the question a club actually asks about these cards.
        match = self.match_in("4")
        self.assertTrue(match.decided)
        self.assertEqual(match.winner, "home")
        self.assertEqual(match.home_games, 22)
        self.assertEqual(match.away_games, 12)

    def test_the_points_are_paid_and_land_on_the_ladder(self):
        # win 4 + a tenth per game won: 4 + 2.2 = 6.2 home, 2 + 1.2 = 3.2 away.
        match = self.match_in("4")
        self.assertAlmostEqual(match.points_for("riverside-rockets"), 6.2, places=6)
        self.assertAlmostEqual(match.points_for("lakeside-spinners"), 3.2, places=6)
        ledger = stats.points_ledger(self.matches, "riverside-rockets")
        self.assertAlmostEqual(ledger.total,
                               EXPECTED_POINTS["riverside-rockets"] + 6.2,
                               places=6)

    def test_the_card_is_counted_not_rejected(self):
        self.assertEqual(self.rejected, [])
        self.assertEqual(self.season["meta"]["cards_scored"],
                         len(FIXTURES) + len(self.EXTRA))

    # --- and what it cost is named, in the right words ---------------------

    def test_the_finding_says_the_card_states_no_result_not_that_it_is_unknown(self):
        issue, = self.verb_issues_for("4")
        self.assertEqual(issue.rule, "verb-absent")
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertIn("VS", issue.message)

    def test_the_finding_says_the_sets_were_read_and_scored_anyway(self):
        # Without this sentence the warning is still misleading, however it is
        # named: the reader has to be told the card counted.
        issue, = self.verb_issues_for("4")
        lowered = issue.message.casefold()
        self.assertIn("still", lowered)
        self.assertIn("scored", lowered)
        self.assertIn("cross-check", lowered)

    def test_a_word_this_program_does_not_know_is_still_reported_as_unknown(self):
        # The other half of the separation. Collapsing the two findings into one
        # kind message would hide a verb worth adding to the vocabulary.
        issue, = self.verb_issues_for("5")
        self.assertEqual(issue.rule, "verb-unknown")
        self.assertIn("TROUNCES", issue.message)

    def test_a_cancelled_match_is_reported_once_not_twice(self):
        # It has no result for a verb to state, and `no-result` has already said
        # the match was called off. A list where every entry is two entries is a
        # list a club learns to skim.
        self.assertEqual(self.verb_issues_for("6"), [])
        cancelled = [i for i in self.report.issues if i.rule == "no-result"
                     and str(self.paths["6"]) in i.detail]
        self.assertEqual(len(cancelled), 1, "the cancellation went unreported")

    def test_the_verbs_a_real_card_prints_are_all_understood(self):
        # Ten cards, and only the two that were meant to be reported are. That
        # covers DEFEATS, LOSES TO and DRAWS WITH on the ordinary cards, and on
        # round 7 the away verb the real exports print, "DEF. BY" -- which is why
        # the vocabulary is matched on a key with the punctuation taken out, and
        # matched exactly rather than by prefix, since "DEF. BY" begins with the
        # home verb "DEF". Read by prefix it would say the loser won.
        self.assertEqual(sorted(i.rule for i in self.report.issues
                                if i.rule.startswith("verb")),
                         ["verb-absent", "verb-unknown"])
        self.assertEqual(self.match_in("7").winner, "away")

    def test_no_verb_finding_is_an_error(self):
        # Severity is a claim about the numbers, and the numbers are fine: the
        # sets are on the card and were added up. A missing confirmation is a
        # warning by definition.
        for issue in self.report.errors:
            with self.subTest(rule=issue.rule):
                self.assertFalse(issue.rule.startswith("verb"), issue.message)


class TestTheSeasonDocumentIsCompleteAndStable(SeasonCase):

    def test_it_serialises_to_json_without_nan_or_infinity(self):
        # allow_nan=False: a NaN would be written as bare `NaN`, which is not
        # JSON and which every browser refuses to parse -- a blank dashboard.
        text = json.dumps(self.season, allow_nan=False, sort_keys=True)
        self.assertGreater(len(text), 1000)

    def test_the_same_cards_produce_a_byte_identical_document(self):
        again = export.build_season(
            self.matches, competition=self.competition, source_dir=self.data,
            generated="2026-09-10T12:00:00", club=self.discovery.club,
            cards=self.cards, rejected=self.rejected)
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(self.season, sort_keys=True))

    def test_reading_the_cards_again_yields_the_same_ladder(self):
        # Determinism across a whole second pass, including directory walking.
        cards, rejected = scorecard.read_tree(self.data)
        discovery = discover_mod.discover(cards, rejected, config=self.config,
                                          root=self.data)
        comp_id, = sorted(discovery.matches)
        comp = next(c for c in discovery.club.competitions if c.id == comp_id)
        self.assertEqual(stats.ladder(discovery.matches[comp_id], comp),
                         EXPECTED_LADDER)

    def test_every_team_appears_with_a_position(self):
        positions = sorted(t["position"] for t in self.season["teams"])
        self.assertEqual(positions, [1, 2, 3, 4])

    def test_the_players_who_took_a_court_are_all_present(self):
        # Three per team, all of whom played: two in the doubles rows and the
        # singles rows between them.
        slugs = {p["slug"] for p in self.season["players"]}
        self.assertEqual(len(slugs), 12)

    def test_the_generated_stamp_is_the_one_that_was_asked_for(self):
        # The only wall-clock value in the document, and injectable precisely so
        # that two runs of unchanged cards differ in nothing at all.
        self.assertEqual(self.season["meta"]["generated"],
                         "2026-09-10T12:00:00")


class TestAMatchKeepsTheAddressItWasFirstGiven(unittest.TestCase):
    """A link to one night, sent to somebody, and then the next round lands.

    The dashboard gives every match a page of its own at ``#/match/<id>``, and that
    only holds together if the id belongs to the match rather than to the build. It
    does: the id is the one Match Centre printed on the scorecard, the reader
    transcribes it, and the exporter writes it through untouched.

    The alternative was a counter over the cards, which looks identical on a first
    build and renumbers on the second -- the tree is walked in directory order, so a
    round that arrives in a folder sorting earlier than the others moves every id
    after it, and a link a club sent last week opens a different night. So the two
    tests here are the two halves of one promise: the address is what the card says,
    and it still names the same fixture after more cards arrive.
    """

    @staticmethod
    def season_for(root: Path) -> tuple[dict, set[str]]:
        """The season document, and the ids printed on the cards it was built from."""
        data = root / "data"
        cards, rejected = scorecard.read_tree(data)
        config = config_mod.Config(path=root / "config.toml")
        found = discover_mod.discover(cards, rejected, config=config, root=data)
        comp_id, = sorted(found.matches)
        competition = next(c for c in found.club.competitions if c.id == comp_id)
        season = export.build_season(
            found.matches[comp_id], competition=competition, source_dir=data,
            generated="2026-09-10T12:00:00", club=found.club, cards=cards,
            rejected=rejected)
        return season, {card.match_id for card in cards}

    @staticmethod
    def fixtures_by_id(season: dict) -> dict[str, tuple]:
        return {m["id"]: (m["round"], m["home"], m["away"])
                for m in season["matches"]}

    def test_the_address_is_the_id_printed_on_the_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_season_cards(root / "data")
            season, printed = self.season_for(root)
        addresses = {m["id"] for m in season["matches"]}
        self.assertEqual(addresses, printed,
                         "the ids in the season document are not the ids on the "
                         "cards, so a link to a match is this build's own numbering")

    def test_a_new_round_landing_moves_nobodys_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_season_cards(root / "data")
            before = self.fixtures_by_id(self.season_for(root)[0])
        with tempfile.TemporaryDirectory() as tmp:
            # The same six cards plus a fourth round, built from scratch.
            after = self.fixtures_by_id(build_season(Path(tmp)))
        self.assertLess(set(before), set(after),
                        "the fourth round added no addresses, so this proves nothing "
                        "about what a new round does to the old ones")
        for match_id, fixture in sorted(before.items()):
            with self.subTest(match=match_id):
                self.assertEqual(after[match_id], fixture,
                                 f"match {match_id} was round {fixture[0]}, "
                                 f"{fixture[1]} v {fixture[2]}, and after another "
                                 f"round landed that address opens "
                                 f"{after[match_id]}: every link a club sent is now "
                                 f"pointing at somebody else's night")


class TestADeclaredTeamListChangesWhatMayBeInferred(SeasonCase):
    """The same six cards, with the four teams declared in the config.

    Declaring the list is what licenses inference: with a closed roster, a round
    short of a card is a bye rather than a mystery. That is the *only* difference,
    and it must not move a single point of the ladder.
    """

    @classmethod
    def make_config(cls):
        # Matched on day and division rather than on the section name, so the id
        # here is the club's own choice; `_declared_id` finds it by its fields.
        return config_mod.Config(
            path=cls.root / "config.toml",
            competitions={"tuesday-b": config_mod.CompetitionConfig(
                id="tuesday-b", day="Tuesday", division="B",
                teams=tuple(name for name, _ in TEAMS.values()))})

    def test_the_declaration_was_actually_picked_up(self):
        # Without this the class below would pass by testing the derived path
        # twice, which is the quiet way an integration test stops testing.
        self.assertTrue(self.season["meta"]["teams_declared"])
        self.assertEqual(self.season["meta"]["provenance"]["teams"], "declared")

    def test_declaring_the_teams_does_not_change_the_ladder(self):
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)

    def test_the_points_are_unchanged(self):
        for slug, expected in EXPECTED_POINTS.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(
                    stats.points_ledger(self.matches, slug).total, expected,
                    places=6)

    def test_still_no_bye_because_no_round_is_short(self):
        self.assertEqual([m for m in self.matches
                          if m.status is MatchStatus.BYE], [])


class TestAWashoutStoppedHalfwayLeavesNothingBehind(SeasonCase):
    """Rain at 8pm: two rubbers on the card, the night called off, nothing counts.

    Measured on real exports. An association's sheet marks such a night ``W`` and
    adds nothing to either team -- not a game, not a match played -- and the tool
    counted the games that had reached the card before the rain. Two teams' games
    columns were wrong by twenty-odd games each, which in this preset is a tiebreak,
    and both teams were *also* paid for the round by the averaging rule. Paid twice
    and counted once.

    So the whole of round four here must be invisible to every aggregate, while
    remaining perfectly visible as a match on the dashboard and as a finding in the
    report. The expectations are the clean three-round season's, unchanged: the
    point is that adding this card changes nothing.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    # Two rubbers finished before the rain, two never started. A real washed-out
    # card looks exactly like this.
    SCORES = ((6, 2), (6, 1), (0, 0), (0, 0))

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.washout = cw.write_card_file(folder / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="09", match_id="4000001",
            home_name=home_name, away_name=away_name,
            rows=rows_for(cls.SCORES, home_players, away_players),
            cancelled=True, verb="VS", winner=cw.NO_TICK,
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def washed_out(self):
        found = [m for m in self.matches if m.round == self.ROUND]
        self.assertEqual(len(found), 1)
        return found[0]

    def on_court(self):
        """The slugs `rows_for` actually puts on court: the first two per side."""
        return [name.lower().replace(" ", "-")
                for team in (self.HOME, self.AWAY)
                for name in TEAMS[team][1][:2]]

    # --- the card is read, and says what it says ---------------------------

    def test_the_card_is_read_and_the_games_on_it_are_transcribed(self):
        # Nothing is being hidden at the parser: the card really does print 12-3,
        # and refusing to read it would lose the evidence that it was played at all.
        match = self.washed_out()
        self.assertIs(match.status, MatchStatus.CANCELLED)
        self.assertEqual((match.home_games, match.away_games), (12, 3))
        self.assertFalse(match.counts_as_played)

    def test_the_cancellation_is_reported_rather_than_absorbed(self):
        named = [i for i in self.report.issues
                 if i.rule == "no-result" and str(self.washout) in i.detail]
        self.assertEqual(len(named), 1)

    # --- and counts for nothing anywhere -----------------------------------

    def test_the_ladder_is_the_one_the_three_clean_rounds_produced(self):
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)

    def test_no_team_gains_a_point(self):
        for slug, expected in EXPECTED_POINTS.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(
                    stats.points_ledger(self.matches, slug).total, expected,
                    places=6)

    def test_no_team_gains_a_game(self):
        # The failure that mattered most: games difference is this preset's
        # tiebreak, so twenty-odd phantom games reorder a ladder silently.
        rows = {t["slug"]: t for t in self.season["teams"]}
        for slug, expected in EXPECTED_GAMES_FOR.items():
            with self.subTest(team=slug):
                self.assertEqual(rows[slug]["games_won"], expected)

    def test_no_team_gains_a_match_played_or_a_set(self):
        rows = {t["slug"]: t for t in self.season["teams"]}
        for slug in (self.HOME, self.AWAY):
            with self.subTest(team=slug):
                self.assertEqual(rows[slug]["played"], 3)
                self.assertEqual(rows[slug]["counted"], 3)
        # Rounds 1, 2, 3: sets 4-0, 3-1, 4-0. Not the two the rain left behind.
        self.assertEqual(rows[self.HOME]["sets_won"], 11)

    def test_nobody_who_was_on_the_card_gains_an_appearance(self):
        # A rubber nobody finished playing is not a rubber anybody played, and a
        # player's own page is where a club member notices the difference first.
        players = {p["slug"]: p for p in self.season["players"]}
        for slug in self.on_court():
            with self.subTest(player=slug):
                self.assertEqual(players[slug]["matches"], 3)
                # Three rubbers a night -- both doubles and one singles -- over
                # three nights. The washout's first two rubbers are not a fourth.
                self.assertEqual(players[slug]["sets_played"], 9)

    def test_nobody_takes_a_share_of_the_games_that_stopped_counting(self):
        """The Share column is a share of the games column, so the rain has to leave
        both of them alone or neither.

        A fifth aggregation over the same matches, and the one that would have told on
        itself loudest: the team's total comes from the list the ladder prices, which
        this night is not in, and the credit is walked card by card. Walked without
        asking whether the night happened, the rockets' two finished rubbers hand 12
        games to players whose team is credited 70 -- a column adding to 117% and a
        shortfall of minus twelve games printed as a percentage of a hundred.

        Ana played both doubles and the first singles on all three counted nights: half
        of 6 twice and 6 outright, so 12 a night and 36 of the 70. The rain would have
        made it 45.
        """
        rows = {t["slug"]: t for t in self.season["teams"]}
        for slug in (self.HOME, self.AWAY):
            with self.subTest(team=slug):
                gap = rows[slug]["contribution_gap"]
                self.assertEqual(gap["team_games_won"], EXPECTED_GAMES_FOR[slug])
                self.assertEqual(gap["games"], 0.0)
        players = {p["slug"]: p for p in self.season["players"]}
        self.assertEqual(players["ana-fernandez"]["contribution"],
                         {"games_won": 36.0, "team_games_won": 70, "share_pct": 51.4})

    def test_a_round_nobody_played_is_not_a_round_anybody_was_available_for(self):
        # "played 3 of 4" would read as a player who missed a week, when the week
        # is the one that did not happen -- and the dashboard's "players to check"
        # list is built from exactly that ratio.
        players = {p["slug"]: p for p in self.season["players"]}
        for slug in self.on_court():
            with self.subTest(player=slug):
                self.assertEqual(players[slug]["available"], 3)

    def test_the_side_records_do_not_count_it_either(self):
        # home/away splits are their own aggregation, so they are their own bug.
        # Every rockets fixture here is at home, washout included.
        home = {t["slug"]: t for t in self.season["teams"]}[self.HOME]["home"]
        self.assertEqual(home["played"], 3)
        self.assertEqual(home["games_won"], EXPECTED_GAMES_FOR[self.HOME])

    def test_the_pairing_does_not_count_it_as_a_meeting_either(self):
        # A fourth aggregation, and the one the dashboard's head-to-head grid is
        # drawn from. It counts every meeting that was *played*, which is what lets
        # a card that reached no result into it -- so the line between that card and
        # this one is the whole of what keeps the rain out. "hilltop-aces" sorts
        # before "riverside-rockets", so the aces are `low`.
        record = self.season["head_to_head"][stats.h2h_key(self.HOME, self.AWAY)]
        self.assertEqual(record["played"], 1)       # round one, and only that
        self.assertEqual(record["undecided"], 0)
        self.assertEqual(record["games_low"], 6)    # not the 3 before the rain
        self.assertEqual(record["games_high"], 24)  # not the 12 before the rain

    def test_the_slot_records_do_not_count_it_either(self):
        # The per-slot table is a third aggregation over the same matches, and the
        # washout's two finished rubbers are d1 and s1 -- so a slot table that
        # counted them would show two slots played four times and two played three.
        by_slot = {t["slug"]: t
                   for t in self.season["teams"]}[self.HOME]["by_slot"]
        self.assertEqual(sorted(by_slot), ["d1", "d2", "s1", "s2"])
        for key, record in by_slot.items():
            with self.subTest(slot=key):
                self.assertEqual(record["won"] + record["lost"], 3)
        self.assertEqual(sum(r["games_won"] for r in by_slot.values()),
                         EXPECTED_GAMES_FOR[self.HOME])

    # --- while still being on the dashboard --------------------------------

    def test_the_match_is_still_in_the_document_so_the_round_is_visible(self):
        rounds = {r["number"]: r for r in self.season["rounds"]}
        self.assertIn(self.ROUND, rounds)
        exported = [m for m in self.season["matches"] if m["round"] == self.ROUND]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["status"], "cancelled")


class TestARowIsReadForMistakesExactlyWhereItsNumbersCount(SeasonCase):
    """The checks that read a card row by row read exactly the matches that count.

    Five checks go through a match a row at a time: its scores against the set rule,
    its names against the rosters, one player on two courts, its ratings and its
    roster block. Which matches they read was a list of three statuses kept in the
    validator, a copy of the list :attr:`Match.counts_as_played` keeps for every
    figure on the page. Measured, the two gave the same answer on all 216
    combinations of status, scoreline, forfeit tick, Disputed box and opponent, so
    nothing was wrong yet. Parted by one status in a copy of the tree, they went wrong
    in opposite directions. The validator's copy without ``cancelled`` read a
    washout's rows and failed the run at exit 1 on ``player-two-teams``, with six more
    warnings about a night counted nowhere, two of them saying no box was ticked on a
    card whose ``Match Cancelled`` box is. The model's copy without it put the
    washout's 12-3 into the ladder's games column at exit 0, and no finding about the
    rows those games came from.

    So the validator asks the model. Rejected: keeping the list here and pinning it
    equal to the model's with a test, which is still two answers, only held level.

    Nothing a report says changed, so only the last test here failed first, on the
    validator's own list: ``['roster-mismatch', 'set-implausible'] != []``. The other
    two pass on the tree before the change as well as after. They pin what the report
    says today, and each is the only test to catch at least one mutant
    (``/tmp/mutate94.py``): the stranger, three checks sent round the gate; every
    status by hand, the gate narrowed to ``decided``.

    The same stranger is written onto a finished rubber of all three cards the
    question turns on: a washout stopped after two rubbers, a disputed card and a
    forfeited one. He is on neither roster, so wherever a card is read he is reported.
    """

    STRANGER = "Zed Quill"

    @classmethod
    def extra_cards(cls):
        cls.washout = cls.card("washout", "4", "2026-09-08", "riverside-rockets",
                               "hilltop-aces", ((6, 2), (6, 1), (0, 0), (0, 0)), "09",
                               cancelled=True, verb="VS", winner=cw.NO_TICK)
        cls.disputed = cls.card("disputed", "4", "2026-09-08", "lakeside-spinners",
                                "parkview-wanderers", ((6, 3), (6, 2), (2, 6), (6, 4)),
                                "10", disputed=True)
        cls.forfeit = cls.card("forfeit", "5", "2026-09-15", "riverside-rockets",
                               "lakeside-spinners", ((6, 0), (6, 0), (6, 0), (6, 0)),
                               "11", forfeited_by="away")

    @classmethod
    def card(cls, name, rnd, date, home, away, scores, court, **flags):
        """A card with the stranger in the home side's second place on court.

        `rows_for` puts the second place in both doubles and the second singles, so he
        is on the washout's first rubber, which finished 6-2, and on its two empty
        ones.
        """
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        folder = cls.data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        on_court = (home_players[0], cls.STRANGER, home_players[2])
        return cw.write_card_file(folder / f"{name}.pdf", cw.CardSpec(
            date=date, round_label=rnd, division="B", court=court,
            match_id=f"4{court}0001", home_name=home_name, away_name=away_name,
            rows=rows_for(scores, on_court, away_players),
            home_roster=squad_for(home, home_players)[0],
            away_roster=squad_for(away, away_players)[0], **flags))

    # The stranger reaches three of the five checks: names against rosters, ratings,
    # and one player on two courts. The matches below are made by hand to reach the
    # other two, one mark each: a 9-2, which cannot happen under first-to-6, and a
    # roster leaving out every player the cards registered to its team.
    MARKS = ("roster-incomplete", "set-implausible")

    def row_by_row(self, match) -> list[str]:
        """Which of the two marks the validator found on one match on its own."""
        report = validate.validate([match], self.competition)
        return sorted({i.rule for i in report.issues if i.rule in self.MARKS})

    def made(self, status, *, rows=((6, 2), (9, 2), (6, 3), (6, 1)), **kw):
        fmt = self.competition.fmt
        sets = tuple(SetResult(slot=fmt.slots[i], home_games=home, away_games=away,
                               rule=fmt.set_rule)
                     for i, (home, away) in enumerate(rows))
        away = NO_OPPONENT if status is MatchStatus.BYE else "hilltop-aces"
        return Match(competition=self.comp_id, round="9", home="riverside-rockets",
                     away=away, fmt=fmt, rules=self.competition.rules, sets=sets,
                     status=status, home_roster=("zed-quill",), **kw)

    def test_the_stranger_is_reported_on_each_card_that_counts_and_not_the_washout(self):
        # Worked out on paper: three places on court on each card, so three findings
        # on the disputed card, three on the forfeit and none on the washout, whose
        # only finding is the one that names it. And not the stranger "on two courts"
        # in round four, once on the disputed card and once on a night that did not
        # happen, which is the error this season has when the washout is read.
        found = [i for i in self.report.issues if i.rule == "unknown-player"]
        self.assertEqual(sorted(Path(i.detail).name for i in found),
                         ["disputed.pdf"] * 3 + ["forfeit.pdf"] * 3)
        about_the_washout = [i.rule for i in self.report.issues
                             if str(self.washout) in i.detail]
        self.assertEqual(about_the_washout, ["no-result"])
        self.assertNotIn("player-two-teams", [i.rule for i in self.report.errors])

    def test_every_status_is_read_row_by_row_exactly_when_the_model_counts_it(self):
        # Two of these are the cases a hand-kept list gets wrong by reaching for
        # `decided` instead: a night that stopped two rubbers short of a result, and
        # a forfeit nobody ticked. Both happened, so both are counted and read.
        cases = {
            "played": self.made(MatchStatus.PLAYED),
            "played, two rubbers short of a result":
                self.made(MatchStatus.PLAYED, rows=((6, 2), (9, 2), (4, 4), (3, 2))),
            "forfeit, the away side ticked":
                self.made(MatchStatus.FORFEIT, forfeited_by="away"),
            "forfeit, neither side ticked": self.made(MatchStatus.FORFEIT),
            "disputed": self.made(MatchStatus.DISPUTED, disputed=True),
            "cancelled": self.made(MatchStatus.CANCELLED),
            "unplayed": self.made(MatchStatus.UNPLAYED),
            "bye": self.made(MatchStatus.BYE),
        }
        self.assertEqual({m.status for m in cases.values()}, set(MatchStatus),
                         "a status nobody has decided about")
        found = {name: self.row_by_row(match) for name, match in cases.items()}
        self.assertEqual(found, {name: list(self.MARKS) if match.counts_as_played
                                 else [] for name, match in cases.items()})
        self.assertEqual({name for name, marks in found.items() if marks},
                         {"played", "played, two rubbers short of a result",
                          "forfeit, the away side ticked",
                          "forfeit, neither side ticked", "disputed"})

    def test_the_validator_asks_the_model_rather_than_keeping_a_list_of_its_own(self):
        # A list that agrees today is indistinguishable from the rule until the day
        # the two part, so the model's answer is turned round both ways and the
        # validator has to follow it.
        played, washout = (self.made(MatchStatus.PLAYED),
                           self.made(MatchStatus.CANCELLED))
        with mock.patch.object(Match, "counts_as_played",
                               new_callable=mock.PropertyMock, return_value=False):
            self.assertEqual(self.row_by_row(played), [])
        with mock.patch.object(Match, "counts_as_played",
                               new_callable=mock.PropertyMock, return_value=True):
            self.assertEqual(self.row_by_row(washout), list(self.MARKS))


class TestACardThatCountsForNothingRegistersNobodyAndRatesNobody(SeasonCase):
    """A card the model counts for nothing feeds neither a squad nor a rating.

    Its games, sets and appearances were already nobody's. Its roster block was not:
    the squads were gathered from every card's roster and the rating history from
    every card's ratings, while the two checks that read a roster read only the
    matches that count. Measured on the demo with ``Yan Stray`` added to the Magpies'
    roster on their R5 washout: exit 0, 6 warnings became 10, the four new ones
    ``roster-incomplete`` on the Magpies' other four cards and none on the washout,
    and ``yan-stray`` a registered Magpie with 0 matches. The other way round, with
    the washout printing no ratings the report was byte-identical and round 5 left
    six players' rating histories. On the season below, 17 warnings where 11 are
    now, six of them about two names nobody plays under, and Ana shown at the 3.0 her
    washout printed rather than the 2.5 of every card that counted.

    So discovery registers a squad from the cards :attr:`Match.counts_as_played`
    counts and from nothing else, and the rating history reads the same cards, which
    are the ones the two checks already read. Rejected, both measured in a copy of
    the tree: a name an uncounted card prints registered only when a counted card
    names it too, which registered the same players, left the six histories moving
    on a card nothing checks and, with its records read from every card, still
    took a washout's (C) (the washout's-(C) test says what that costs); and the two
    checks given a gate of their own that reads a washout's roster, which put one
    or two findings on the washout beside its ``no-result`` and is a second answer
    to whether a card counts. The cost is a folder nobody has played a card of yet,
    which now registers no squad and so no captain: the demo's round one alone,
    every card printed as Match Centre prints a fixture it holds no result for
    (/tmp/t4/scratch/y444/fix/preseason.py), registered 18 players and 6 captains
    from its (C) marks and registers none, exit 0 and 8 warnings either way. The
    note naming those captains goes, one saying why the squads are empty comes, and
    the config it writes lists the six teams' captains commented and blank. Its
    teams are still every card's banners, which is what the night-alone test is for.

    Three cards beyond the season. A washout, rockets v aces, stopped after two
    rubbers, printing the rockets at 3.0 with a stranger beside them and the aces
    with no ratings at all. A night not yet played, spinners v wanderers, every row
    0-0 and nobody named, with a stranger on the spinners' roster at 3.5 like the
    rest of it. And a night that counts with no result, wanderers v aces stopped two
    rubbers short with no box ticked, and a newcomer on the wanderers' roster who
    takes no court, the player an answer of "a decided card" would lose.

    Eight of these failed first, on the tree before the change: the name, the night
    alone, the rating, the model turned round (``counts=False``), the captain's
    note, which said nothing at all because the washout had registered him, the
    washout's (C), the captain declared for a team no card of which counts, whose
    declaration took his team out of a note naming the captains an unplayed night's
    (C) marks had given, and the config written for such a folder. Four failed on
    the item's first version as well, which took the washout's (C) away and then
    said none was printed, told the night alone's teams nothing, told their declared
    captain to check his spelling, and wrote that no (C) marks were found. The other
    three pass before and after and pin what the change must leave alone, and each
    is the only test to catch at least one mutant (``/tmp/mutate132.py``): a night
    not yet played going unnamed, ratings kept only for whoever took a court, and a
    disputed card or a forfeit registering nobody.
    """

    STRANGER = "Yan Stray"      # the washout's rockets roster, and nowhere else
    NOT_YET = "Tam Pellow"      # the unplayed night's spinners roster, and nowhere else
    NEWCOMER = "Uma Latchford"  # the wanderers' roster on a night that counts

    @classmethod
    def extra_cards(cls):
        cls.washout = cls.card("washout", "4", "2026-09-08", "riverside-rockets",
                               "hilltop-aces", ((6, 2), (6, 1), (0, 0), (0, 0)), "09",
                               extra=cls.STRANGER, rating=3.0, away_rating=None,
                               cancelled=True, verb="VS", winner=cw.NO_TICK)
        cls.not_yet = cls.card("not-yet", "4", "2026-09-08", "lakeside-spinners",
                               "parkview-wanderers", ((0, 0),) * 4, "10",
                               extra=cls.NOT_YET, rating=3.5, named=False,
                               verb="VS", winner=cw.NO_TICK)
        cls.short = cls.card("short", "5", "2026-09-15", "parkview-wanderers",
                             "hilltop-aces", ((6, 3), (6, 4), (0, 0), (0, 0)), "11",
                             extra=cls.NEWCOMER, rating=4.0,
                             verb="VS", winner=cw.NO_TICK)

    @classmethod
    def card(cls, name, rnd, date, home, away, scores, court, *, extra, rating,
             away_rating=2.5, named=True, **flags):
        """A card whose home roster prints ``extra`` under the home squad.

        The whole home roster at ``rating`` and the away one at ``away_rating``,
        where None prints no ratings at all; and nobody named on a set row when
        ``named`` is false, which is how a night still to come is printed.
        """
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        folder = cls.data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        rows = rows_for(scores, home_players, away_players)
        if not named:
            rows = tuple(dataclasses.replace(row, home_players=(), away_players=())
                         for row in rows)
        return cw.write_card_file(folder / f"{name}.pdf", cw.CardSpec(
            date=date, round_label=rnd, division="B", court=court,
            match_id=f"4{court}0001", home_name=home_name, away_name=away_name,
            rows=rows,
            home_roster=tuple(cw.RosterRow(n, rating, rating, captain=(i == 0))
                              for i, n in enumerate(home_players + (extra,))),
            away_roster=tuple(cw.RosterRow(n, away_rating, away_rating,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)),
            **flags))

    def players(self):
        return {p["slug"]: p for p in self.season["players"]}

    def test_a_name_only_a_card_that_counts_for_nothing_prints_is_nobody(self):
        # On no squad, so no player of the season, and so no finding on any card of
        # the team saying its roster left them out.
        squads = {slug: sorted(p.slug for p in team.players)
                  for slug, team in self.competition.teams.items()}
        self.assertEqual(squads["riverside-rockets"],
                         ["ana-fernandez", "bo-nakamura", "cass-ellery"])
        self.assertEqual(squads["lakeside-spinners"],
                         ["gil-ahearn", "hana-wex", "ilya-modric"])
        for slug in ("yan-stray", "tam-pellow"):
            with self.subTest(player=slug):
                self.assertNotIn(slug, self.players())
                self.assertNotIn(slug, self.season["player_names"])
                self.assertEqual([i.rule for i in self.report.issues
                                  if slug in i.message], [])

    def test_a_night_not_yet_played_alone_has_teams_nobody_on_them_and_says_so(self):
        # A folder of round one before anyone has played it. Its banners are the
        # fixture, and the team list is read from every card; its roster blocks are
        # a claim about who played, and nobody has. One note says so for both teams,
        # where the demo's round one alone had said nothing about six empty squads.
        alone = [c for c in self.cards if Path(c.source).name == "not-yet.pdf"]
        found = discover_mod.discover(alone, [], config=self.config, root=self.data)
        competition, = found.club.competitions
        self.assertEqual({slug: [p.slug for p in team.players]
                          for slug, team in competition.teams.items()},
                         {"lakeside-spinners": [], "parkview-wanderers": []})
        said = [n for n in found.notes if "nobody is registered" in n]
        self.assertEqual(len(said), 1, found.notes)
        for team in ("Lakeside Spinners", "Parkview Wanderers"):
            self.assertIn(team, said[0])

    def test_each_match_that_counts_for_nothing_is_named_once_and_read_no_further(self):
        # Its only finding is the one that names it. The washout's aces print no
        # ratings, and a rating check sent round the gate says so. The checks that
        # read a card rather than a match are not this question, and name it by path.
        for label in ("R4 riverside-rockets v hilltop-aces",
                      "R4 lakeside-spinners v parkview-wanderers"):
            with self.subTest(match=label):
                self.assertEqual([i.rule for i in self.report.issues
                                  if i.subject.startswith(label)], ["no-result"])

    def test_the_night_not_yet_played_is_named_once_and_its_verb_goes_unread(self):
        """Every finding about ``not-yet.pdf``, whole: one, and it is ``no-result``.

        Measured on the tree before this (/tmp/t6/scratch/m22/probe/probe.py, the
        season and this card through build.py): exit 0, and beside ``recorded as
        unplayed`` a ``verb-absent`` warning ending "The set rows were still read and
        scored", which is false of a card none of whose rows is read. ``verb-absent``
        passed by only a card whose Match Cancelled box is ticked, and this one's is
        not: it counts for nothing because nothing is on it. So the verb checks now
        ask :attr:`Match.counts_as_played`, the question every check of a row asks.
        Rejected: ``verb-absent`` kept, reworded for a card that counts for nothing,
        which is the one fact said twice in two sets of words.
        """
        path = str(self.not_yet)
        label = "R4 lakeside-spinners v parkview-wanderers"
        self.assertEqual(
            [i for i in self.report.issues
             if path in (i.subject, i.detail) or path in i.message
             or i.subject.startswith(label)],
            [validate.Issue(
                rule="no-result", severity=validate.WARNING,
                message=f"{label}: recorded as unplayed, so it is not counted in "
                        "the ladder.",
                subject=label, detail=path)])

    def test_a_card_that_counts_registers_and_rates_with_no_result_on_it(self):
        # Stopped two rubbers short with no box ticked: counted, and not decided. So
        # the newcomer on its roster is a wanderer, rated though she took no court,
        # and the ratings it prints are the latest.
        short, = [m for m in self.matches if m.round == "5"]
        self.assertTrue(short.counts_as_played)
        self.assertFalse(short.decided)
        wanderers = self.competition.teams["parkview-wanderers"]
        self.assertIn("uma-latchford", [p.slug for p in wanderers.players])
        players = self.players()
        self.assertEqual(players["uma-latchford"]["rating_history"],
                         [{"round": "5", "singles": 4.0, "doubles": 4.0}])
        self.assertEqual(players["jo-calder"]["rating"],
                         {"singles": 4.0, "doubles": 4.0})

    def test_the_rating_a_player_shows_is_the_last_a_card_that_counts_printed(self):
        # Ana's washout printed 3.0 and Gil's unplayed night 3.5, each later than
        # every card of theirs that counts. Neither is a round of anybody's history,
        # registered player or not, so each shows the 2.5 of round three.
        players = self.players()
        for slug in ("ana-fernandez", "gil-ahearn"):
            with self.subTest(player=slug):
                self.assertEqual([h["round"] for h in players[slug]["rating_history"]],
                                 ["1", "2", "3"])
                self.assertEqual(players[slug]["rating"],
                                 {"singles": 2.5, "doubles": 2.5})

    def test_discovery_and_the_rating_history_ask_the_model_which_cards_count(self):
        # Turned round both ways, as the validator's gate is. Every card counting
        # registers both strangers and gives Ana the washout's round; none counting
        # registers nobody and rates nobody.
        for counts in (True, False):
            with self.subTest(counts=counts), mock.patch.object(
                    Match, "counts_as_played", new_callable=mock.PropertyMock,
                    return_value=counts):
                found = discover_mod.discover(self.cards, self.rejected,
                                              config=self.config, root=self.data)
                competition, = found.club.competitions
                registered = {p.slug for team in competition.teams.values()
                              for p in team.players}
                history = {slug: [point.round for point in record.rating_history]
                           for slug, record in stats.player_stats(
                               found.matches[competition.id], competition).items()}
                if counts:
                    self.assertLessEqual({"yan-stray", "tam-pellow"}, registered)
                    self.assertEqual(history["ana-fernandez"], ["1", "2", "3", "4"])
                else:
                    self.assertEqual(registered, set())
                    self.assertEqual({s: h for s, h in history.items() if h}, {})

    def test_a_captain_declared_from_a_card_that_counts_for_nothing_is_told_why(self):
        # He is on a card, so "not on any of that team's cards" would be false; and
        # printed as the club spelt him, so he is told he waits, where the note once
        # told the club to check the spelling of a name that is right.
        config = config_mod.Config(
            path=self.root / "config.toml",
            competitions={"tuesday-b": config_mod.CompetitionConfig(
                id="tuesday-b", day="Tuesday", division="B",
                captains={"Riverside Rockets": self.STRANGER})})
        found = discover_mod.discover(self.cards, self.rejected, config=config,
                                      root=self.data)
        said = [n for n in found.notes if "declared captain" in n]
        self.assertEqual(len(said), 1, found.notes)
        self.assertIn(f"({self.STRANGER})", said[0])
        self.assertIn("any of that team's cards that count", said[0])
        self.assertIn("the mark waits until a card that counts names that player",
                      said[0])
        self.assertNotIn("spelling", said[0])

    def test_a_captain_marked_only_on_a_card_that_counts_for_nothing_is_nobodys(self):
        """The rockets' (C) left on their washout alone: no captain, and said so.

        A card that counts for nothing is read for nothing, and its (C) is on it.
        Measured on the demo with the Magpies' marks taken off their four played
        cards and left on the R5 washout (/tmp/t4/scratch/y444/fix/capmark.py): the
        tree before the item took Clementine Nakagawa from the washout and the tree
        after dropped her, and told the club "no (C) is printed on any card", which
        the washout contradicts. Rejected: the squads' members from the cards that
        count and their evidence, the (C) and the spelling, from every card. That
        kept Clementine here, and with the washout's (C) moved to another Magpie
        (twoc.py) it put a second (C) on the squad and took the captain every played
        card agreed on away, with a note about a clash no card that counts has.
        """
        rockets = TEAMS["riverside-rockets"][0]
        cards = [card if card.home_name != rockets
                 or Path(card.source).name == "washout.pdf"
                 else dataclasses.replace(card, home_roster=tuple(
                     dataclasses.replace(entry, is_captain=False)
                     for entry in card.home_roster))
                 for card in self.cards]
        found = discover_mod.discover(cards, self.rejected, config=self.config,
                                      root=self.data)
        competition, = found.club.competitions
        team = competition.teams["riverside-rockets"]
        self.assertIsNone(team.captain)
        self.assertEqual([p.slug for p in team.players if p.is_captain], [])
        self.assertTrue(any("no card that counts prints a (C) for Riverside Rockets"
                            in note for note in found.notes), found.notes)

    def test_a_disputed_card_and_a_forfeit_count_so_each_registers_and_rates(self):
        # The washout again with its box unticked and Disputed ticked, then as a
        # forfeit: both count, so the stranger is a rocket, and Ana, on court in
        # two rubbers of it, has round four. A rule of "a card with a result the
        # club is sure of" would lose both.
        for status, flags in (
                (MatchStatus.DISPUTED, {"cancelled": False, "disputed": True}),
                (MatchStatus.FORFEIT, {"cancelled": False, "forfeited_by": "away"})):
            cards = [dataclasses.replace(card, **flags)
                     if Path(card.source).name == "washout.pdf" else card
                     for card in self.cards]
            with self.subTest(card=status.value):
                found = discover_mod.discover(cards, self.rejected,
                                              config=self.config, root=self.data)
                competition, = found.club.competitions
                self.assertEqual([m.status for m in found.matches[competition.id]
                                  if Path(m.source).name == "washout.pdf"], [status])
                rockets = competition.teams["riverside-rockets"]
                self.assertIn("yan-stray", [p.slug for p in rockets.players])
                history = stats.player_stats(
                    found.matches[competition.id],
                    competition)["ana-fernandez"].rating_history
                self.assertEqual([point.round for point in history],
                                 ["1", "2", "3", "4"])

    def test_a_captain_declared_for_a_team_with_no_card_that_counts_adds_no_note(self):
        """The night not yet played alone, with a captain declared for one side.

        The team is told once why nobody is on it, and the declaration is still its
        captain. Measured on the item's first version: the declared captain was
        told to check his spelling against a roster that registers nobody, a
        second note about the same empty squad. Rejected: registering such a team
        from the cards that count for nothing until one counts, which is two
        sources for one squad and a squad that changes on the night nothing about
        it did.
        """
        alone = [c for c in self.cards if Path(c.source).name == "not-yet.pdf"]

        def discovered(captains):
            config = config_mod.Config(
                path=self.root / "config.toml",
                competitions={"tuesday-b": config_mod.CompetitionConfig(
                    id="tuesday-b", day="Tuesday", division="B", captains=captains)})
            return discover_mod.discover(alone, [], config=config, root=self.data)

        found = discovered({"Lakeside Spinners": "Gil Ahearn"})
        self.assertEqual(found.notes, discovered({}).notes)
        competition, = found.club.competitions
        self.assertEqual(competition.teams["lakeside-spinners"].captain, "gil-ahearn")

    def test_the_config_written_from_such_a_folder_says_whose_marks_name_nobody(self):
        # Every card prints a (C), and a stub saying none were found sent a club
        # looking for marks that are there. The claim is the one the squads keep.
        alone = [c for c in self.cards if Path(c.source).name == "not-yet.pdf"]
        found = discover_mod.discover(alone, [], config=self.config, root=self.data)
        competition, = found.club.competitions
        text = config_mod.render(self.config, competitions=[competition],
                                 cards=len(alone))
        block = text.split(f"[competitions.{competition.id}.captains]\n", 1)[1]
        stub = block.split("\n\n", 1)[0]
        self.assertIn("on a card that counts", " ".join(stub.split()))


class TestTheCardsOfANightThatDisagreeRegisterOnlyTheNamesTheyAllPrint(SeasonCase):
    """A night the conflict rule sets aside registers the names its cards agree on.

    Two cards that disagree about a night both count (:attr:`Match.counts_as_played`),
    so 4.44's gate let each register its whole roster block, and a name only one of
    them prints became a player of the team that nothing else of the season knew.
    Measured on the demo with a second R4 Quolls v Magpies card beside the first,
    its first row's games swapped and ``Yan Stray`` added to its Magpies roster
    (/tmp/t7/scratch/SQ/p76/probe.py): exit 1 on F9, and 10 warnings where the
    plain demo has 6. The four more were ``roster-incomplete`` on the Magpies' R1 to
    R4 cards, the first R4 card among them, each saying it ``omits registered
    player(s) yan-stray``, and ``yan-stray`` was a Magpie with 0 matches and no
    rating. Without the stranger the same pair gave the plain demo's 6.

    So from a night :func:`stats.duplicates` sets aside for disagreeing, a roster
    registers only the names every card of the night prints on that side, and a
    card of it printing no roster there takes nobody off, since a roster not read
    is not a roster naming nobody. A washout of the night is one of its cards: the
    duplicate rule groups it with the card it disagrees with, and its roster block
    is a claim about who was on the squad that night as the other card's is. A copy
    and a card superseded by another agree about the result, are not set aside, and
    are left as they were. A captain declared under a name the night leaves out is
    told he waits, as a line under the name is: with the stranger no ace, the note
    about him told the club to check the spelling of a name spelt as printed
    (/tmp/t7/scratch/SQ/fix/capw-pre.txt).

    Rejected: registering nobody from such a night, which loses the names every card
    of it agrees on, when the cards are the only source of a squad; keeping the
    union, which is the defect; the intersection with an unread roster in it, which
    registers nobody from a card that lost its roster block beside one that did not;
    and comparing only the cards that count, which leaves a washout's night
    registering its played card whole, the defect again on the one card the rule
    sets aside against it.

    Four nights beyond the season. Two R4 rockets v aces cards that disagree about
    the first rubber, both printing a newcomer on the rockets' roster and one
    printing a stranger on the aces'. Two R4 spinners v wanderers cards that
    disagree, one with a newcomer on the wanderers' roster and one printing no
    wanderers roster at all. An R5 rockets v spinners washout, stopped after two
    rubbers, beside a played card for the same night whose rockets roster adds a
    name the washout's does not. And two R5 aces v wanderers cards that disagree,
    one printing the aces' squad and the other only a name the first does not, so
    that no name is on both.
    """

    AGREED = "Vic Harrow"       # both R4 rockets v aces cards, the rockets' roster
    STRANGER = "Yan Stray"      # one of those two's aces roster, and nowhere else
    BESIDE_UNREAD = "Uma Latchford"   # R4 wanderers, beside a card with no roster
    NOT_ON_WASHOUT = "Wes Tolland"    # R5's played card, not its washout
    APART = "Zoe Quint"         # R5 aces v wanderers, the aces' roster of one card
    WIN = ((6, 1), (6, 2), (6, 0), (6, 3))
    OTHER = ((1, 6), (6, 2), (6, 0), (6, 3))

    @classmethod
    def extra_cards(cls):
        _, rockets = TEAMS["riverside-rockets"]
        _, aces = TEAMS["hilltop-aces"]
        _, wanderers = TEAMS["parkview-wanderers"]
        on = ("4", "2026-09-08")
        cls.card("first", *on, "riverside-rockets", "hilltop-aces", cls.WIN, "07",
                 home_roster=(*rockets, cls.AGREED), away_roster=(*aces, cls.STRANGER))
        cls.card("second", *on, "riverside-rockets", "hilltop-aces", cls.OTHER,
                 "08", home_roster=(*rockets, cls.AGREED))
        cls.card("spinners", *on, "lakeside-spinners", "parkview-wanderers",
                 cls.WIN, "09", away_roster=(*wanderers, cls.BESIDE_UNREAD))
        cls.card("spinners-again", *on, "lakeside-spinners", "parkview-wanderers",
                 cls.OTHER, "10", away_roster=())
        cls.card("washout", "5", "2026-09-15", "riverside-rockets",
                 "lakeside-spinners", ((6, 2), (6, 1), (0, 0), (0, 0)), "11",
                 cancelled=True, verb="VS", winner=cw.NO_TICK)
        cls.card("replayed", "5", "2026-09-15", "riverside-rockets",
                 "lakeside-spinners", cls.WIN, "12",
                 home_roster=(*rockets, cls.NOT_ON_WASHOUT))
        cls.card("aces", "5", "2026-09-15", "hilltop-aces", "parkview-wanderers",
                 cls.WIN, "13")
        cls.card("aces-again", "5", "2026-09-15", "hilltop-aces",
                 "parkview-wanderers", cls.OTHER, "14", home_roster=(cls.APART,))

    @classmethod
    def card(cls, name, rnd, date, home, away, scores, court, *, home_roster=None,
             away_roster=None, **flags):
        """A card whose roster blocks are the two squads, unless given otherwise."""
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        folder = cls.data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        return cw.write_card_file(folder / f"{name}.pdf", cw.CardSpec(
            date=date, round_label=rnd, division="B", court=court,
            match_id=f"4{court}0001", home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_players, away_players),
            home_roster=squad_for(home, home_players if home_roster is None
                                  else home_roster)[0],
            away_roster=squad_for(away, away_players if away_roster is None
                                  else away_roster)[0],
            **flags))

    def squad(self, team: str, competition=None) -> list[str]:
        teams = (competition or self.competition).teams
        return sorted(p.slug for p in teams[team].players)

    def said_of(self, slug: str) -> list[str]:
        # The sentence, not the detail: a roster-incomplete's detail lists the
        # card's own roster, which is what the card printed.
        return [i.rule for i in self.report.issues if slug in i.message]

    def test_a_name_only_one_card_of_the_night_prints_is_nobody(self):
        # No ace, so no player of the season, and no card of the aces told that its
        # roster left him out.
        self.assertNotIn("yan-stray", self.squad("hilltop-aces"))
        self.assertNotIn("yan-stray", {p["slug"] for p in self.season["players"]})
        self.assertNotIn("yan-stray", self.season["player_names"])
        self.assertEqual(self.said_of("yan-stray"), [])

    def test_a_name_every_card_of_the_night_prints_is_registered(self):
        self.assertIn("vic-harrow", self.squad("riverside-rockets"))

    def test_a_card_of_the_night_whose_roster_was_not_read_takes_nobody_off(self):
        self.assertIn("uma-latchford", self.squad("parkview-wanderers"))

    def test_a_washout_is_one_of_the_cards_of_the_night_it_disagrees_with(self):
        self.assertEqual(self.squad("riverside-rockets"),
                         ["ana-fernandez", "bo-nakamura", "cass-ellery",
                          "vic-harrow"])
        self.assertNotIn("wes-tolland", {p["slug"] for p in self.season["players"]})
        self.assertEqual(self.said_of("wes-tolland"), [])

    def test_a_night_whose_cards_share_no_name_on_a_side_registers_nobody_there(self):
        # Nothing both cards print is nothing agreed, not a night with nothing to
        # take off: the name only the second prints is no ace, and waits.
        self.assertNotIn("zoe-quint", self.squad("hilltop-aces"))
        self.assertEqual(self.said_of("zoe-quint"), [])
        found = discover_mod.discover(self.cards, self.rejected, config=self.config,
                                      root=self.data)
        self.assertIn("zoe-quint", discover_mod._players_named(found.club,
                                                               found.matches)[1])

    def test_a_captain_declared_from_one_card_of_the_night_is_told_he_waits(self):
        # Printed as the club spelt him, so the note that sent the club to check the
        # spelling, as it did once the stranger was no ace, sent it to a name that
        # is right. A captain on no card at all is still told to check it.
        config = config_mod.Config(
            path=self.root / "config.toml",
            competitions={self.comp_id: config_mod.CompetitionConfig(
                id=self.comp_id, day="Tuesday", division="B",
                captains={"Hilltop Aces": self.STRANGER,
                          "Parkview Wanderers": "Zed Nobody"})})
        found = discover_mod.discover(self.cards, self.rejected, config=config,
                                      root=self.data)
        said = [[n for n in found.notes if f"{team}'s declared captain" in n]
                for team in ("Hilltop Aces", "Parkview Wanderers")]
        self.assertEqual([len(notes) for notes in said], [1, 1], found.notes)
        (aces,), (wanderers,) = said
        self.assertIn(f"({self.STRANGER})", aces)
        self.assertIn("or on some but not all of the cards of a night that disagree",
                      aces)
        self.assertIn("the mark waits until a card that counts names that player",
                      aces)
        self.assertNotIn("spelling", aces)
        self.assertIn("Check the spelling against the roster printed on a scorecard.",
                      wanderers)

    def test_discovery_asks_the_repeat_rule_which_nights_disagree(self):
        # Told no night disagrees, every roster registers whole, the stranger and
        # the name the washout leaves off among them.
        nobody = stats.Duplicates(kept=[], copies=[], conflicting=[], superseded=[])
        with mock.patch.object(discover_mod, "duplicates", return_value=nobody):
            found = discover_mod.discover(self.cards, self.rejected,
                                          config=self.config, root=self.data)
        competition, = found.club.competitions
        self.assertIn("yan-stray", self.squad("hilltop-aces", competition))
        self.assertIn("wes-tolland", self.squad("riverside-rockets", competition))

    def test_the_names_that_wait_are_the_ones_the_rule_leaves_out(self):
        # Asked of the rule, not worked out as every roster name no squad holds,
        # which is the same set on these cards and a second copy of the rule that
        # stops agreeing the day the rule does. Told every roster registers whole,
        # nothing waits.
        found = discover_mod.discover(self.cards, self.rejected, config=self.config,
                                      root=self.data)
        _, waiting = discover_mod._players_named(found.club, found.matches)
        self.assertLessEqual({"yan-stray", "wes-tolland"}, waiting)

        def whole(matches):
            return [{side: frozenset(match.roster_for(side))
                     for side in ("home", "away")} for match in matches]
        with mock.patch.object(discover_mod, "_registered_rosters", whole):
            _, waiting = discover_mod._players_named(found.club, found.matches)
        self.assertEqual(waiting, set())

    def test_a_line_under_the_stranger_is_told_it_waits_for_a_card_that_counts(self):
        # Keyed on a name a card prints, so neither "no card this run read names"
        # nor "no card puts that name in any of them", which is what the checks
        # said once the stranger was no ace and they still asked 4.44's gate alone.
        waits = ("some but not all of the cards of a night that disagree",
                 "a night whose cards disagree registers only the names they all "
                 "print")
        lines = {"[player_names]": '[player_names]\n"yan-stray" = "Yan Stray-Smith"\n',
                 "[namesakes]": '[namesakes]\n"yan-stray" = ["Hilltop Aces", '
                                '"Riverside Rockets"]\n'}
        for section, toml in lines.items():
            with self.subTest(section):
                path = self.root / f"config-{section.strip('[]')}.toml"
                path.write_text(toml, encoding="utf-8")
                config = config_mod.load(path)
                found = discover_mod.discover(self.cards, self.rejected,
                                              config=config, root=self.data)
                said = [" ".join(line.split()) for line in
                        (*config.warnings, *found.config_warnings, *found.notes)
                        if "yan-stray" in line]
                self.assertEqual(len(said), 1, said)
                for needle in waits:
                    self.assertIn(needle, said[0])
                self.assertNotIn("no card this run read", said[0].lower())
                self.assertNotIn("any of them", said[0])


class TestEveryCaptainDiscoveryGivesATeamWasNamedByTheClubOrPrinted(SeasonCase):
    """A team's captain is declared, or is the one player its counted cards mark.

    Which is why the review list no longer has an item for a captain "inferred"
    when no card marked one: nothing infers one. Measured on the base
    (/tmp/t6/scratch/m15/p126/probe.py) through ``build.py`` over eighteen ways in:
    a (C) on every card, none for a team, two for one, one on a washout alone, a
    captain declared by name and by slug, on the squad, on no card, on a washout
    alone, on another team, blank, with no letters in it, beside another player's
    (C), a captain on two teams, ``[aliases]`` either way round, ``[namesakes]``,
    ``[player_names]`` and a night not yet played, and the demo with a second
    competition beside it. Every captain answered ``DECLARED`` or ``CARD`` and the
    item printed 0 times. So the item and ``build._captain_source`` went, and this
    holds what made them dead: were discovery to guess a captain, this fails
    rather than a club being shown a guess nothing reports.

    Rejected: keeping the item for a hand-built model with a test that builds one,
    a review line no folder can produce, and a live-looking guarantee (2.13).
    """

    STRANGER = "Yan Stray"      # on the rockets' washout roster and nowhere else

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        folder = cls.data / "Round4"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B", court="09",
            match_id="4090001", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)), home_players,
                          away_players),
            home_roster=tuple(cw.RosterRow(n, 2.5, 2.5, captain=(i == 0)) for i, n
                              in enumerate(home_players + (cls.STRANGER,))),
            away_roster=tuple(cw.RosterRow(n, 2.5, 2.5, captain=(i == 0))
                              for i, n in enumerate(away_players)),
            cancelled=True, verb="VS", winner=cw.NO_TICK))

    def rockets_marked(self, marked, *, washout=True):
        """The cards with the rockets' (C) against ``marked``, by round."""
        rockets = TEAMS["riverside-rockets"][0]
        out = []
        for card in self.cards:
            if card.home_name == rockets and (washout or card.round != "4"):
                who = marked.get(card.round, "")
                card = dataclasses.replace(card, home_roster=tuple(
                    dataclasses.replace(entry, is_captain=entry.name == who)
                    for entry in card.home_roster))
            out.append(card)
        return out

    def discovered(self, cards=None, *, captains=None, **settings):
        config = config_mod.Config(
            path=self.root / "config.toml", competitions={} if captains is None
            else {"tuesday-b": config_mod.CompetitionConfig(
                id="tuesday-b", day="Tuesday", division="B", captains=captains)},
            **settings)
        found = discover_mod.discover(self.cards if cards is None else cards,
                                      self.rejected, config=config, root=self.data)
        competition, = found.club.competitions
        return competition

    def test_each_way_in_gives_the_captain_it_should_and_none_a_guess(self):
        ana, bo = "Ana Fernandez", "Bo Nakamura"
        every = {"1": ana, "2": ana, "3": ana, "4": ana}
        cases = {
            "a (C) on every card": (self.discovered(),
                                    ("ana-fernandez", "", Provenance.CARD)),
            "declared on the squad": (
                self.discovered(captains={"Riverside Rockets": bo}),
                ("bo-nakamura", bo, Provenance.DECLARED)),
            "declared by slug": (
                self.discovered(captains={"riverside-rockets": bo}),
                ("bo-nakamura", bo, Provenance.DECLARED)),
            "declared on a washout alone": (
                self.discovered(captains={"Riverside Rockets": self.STRANGER}),
                ("yan-stray", self.STRANGER, None)),
            "declared on no card": (
                self.discovered(captains={"Riverside Rockets": "Zed Nowhere"}),
                ("zed-nowhere", "Zed Nowhere", None)),
            "two (C) marks": (
                self.discovered(self.rockets_marked(dict(every, **{"3": bo}))),
                (None, "", None)),
            "a (C) on the washout alone": (
                self.discovered(self.rockets_marked({"4": ana})),
                (None, "", None)),
            "no (C) at all": (self.discovered(self.rockets_marked({})),
                              (None, "", None)),
            "[aliases] folds the captain into another": (
                self.discovered(aliases={"ana-fernandez": "bo-nakamura"}),
                ("bo-nakamura", "", Provenance.CARD)),
        }
        for label, (competition, rockets) in cases.items():
            with self.subTest(label):
                team = competition.teams["riverside-rockets"]
                player = team.player(team.captain) if team.captain else None
                self.assertEqual((team.captain, team.captain_declared,
                                  player.captain_source if player else None), rockets)
                for slug, other in competition.teams.items():
                    if other.captain and not other.captain_declared:
                        self.assertIs(other.player(other.captain).captain_source,
                                      Provenance.CARD, slug)
                    self.assertEqual({p.slug for p in other.players
                                      if p.captain_source not in
                                      (Provenance.CARD, Provenance.DECLARED)},
                                     set(), slug)


class TestARubberWithNoGamesIsNotReportedAsATie(SeasonCase):
    """The same washed-out night, with nobody having ticked Match Cancelled.

    Without the tick there is nothing on the page to read a cancellation off, so the
    card is an ordinary match and its empty rows go through the set checks. That is
    the case a club is actually in when a round is rained off and the box is missed,
    and every one of those rows was reported as "level at 0-0, so nobody won the set
    and nothing on the card says why it stopped" -- a sentence about a contest. A row
    with no games on it is not a contest that finished level, it is a rubber nobody
    played, and a club reading it as a tie is left to work that out unaided. Three of
    these came off one real exported card.

    A rubber that genuinely stopped level is the other case and keeps the word. It is
    on the same card here so that the two readings cannot be confused, and so that
    fixing one by deleting the other fails.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    # Two whitewashes, one each way, then a rubber abandoned level, then one that
    # never started. The whitewashes are the point of the shape: they are the rows
    # that separate "no games on the row" from "no games for one side", and a check
    # written against either side alone reports the two most decisive rubbers on
    # the card as rubbers nobody played. One each way, because one of them only
    # catches half of that.
    SCORES = ((6, 0), (0, 6), (3, 3), (0, 0))
    EMPTY_SLOTS = ("d2",)
    TIED_SLOT = "s2"
    WHITEWASH_SLOTS = ("d1", "s1")

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.card = cw.write_card_file(folder / "unticked.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="09", match_id="4000002",
            home_name=home_name, away_name=away_name,
            rows=rows_for(cls.SCORES, home_players, away_players),
            verb="VS", winner=cw.NO_TICK,
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def issues(self, rule):
        return [i for i in self.report.issues
                if i.rule == rule and str(self.card) in i.detail]

    def test_the_card_reads_as_a_match_that_happened(self):
        # The premise of everything below. With no tick there is no evidence of a
        # cancellation, so this is a played match with rows left empty -- not the
        # cancelled card two classes up, which never reaches the set checks at all.
        match, = [m for m in self.matches if m.round == self.ROUND]
        self.assertIs(match.status, MatchStatus.PLAYED)
        self.assertTrue(match.counts_as_played)

    def test_each_rubber_with_no_games_on_it_is_named(self):
        found = self.issues("set-no-games")
        self.assertEqual(sorted(i.subject.split()[-1] for i in found),
                         sorted(self.EMPTY_SLOTS))
        for issue in found:
            with self.subTest(subject=issue.subject):
                self.assertEqual(issue.severity, validate.WARNING)
                self.assertTrue(issue.detail.endswith(".pdf"),
                                f"the warning does not name the card it came "
                                f"from: {issue.detail!r}")

    def test_it_says_there_are_no_games_rather_than_quoting_a_score(self):
        # The whole of the fix is the wording, so the wording is what is asserted.
        # "level at 0-0" reads as a match that was played out to a dead heat.
        for issue in self.issues("set-no-games"):
            with self.subTest(subject=issue.subject):
                self.assertIn("no games", issue.message)
                self.assertNotIn("level at", issue.message)
                self.assertNotIn("0-0", issue.message)

    def test_a_rubber_won_without_conceding_a_game_is_not_an_empty_one(self):
        # Both sides have to be at zero. Measured as two surviving mutations:
        # reading the condition off one side reported every whitewash in the
        # competition as a rubber nobody played, and nothing else in the suite
        # noticed. Scanned over the whole report rather than this card alone,
        # because the clean rounds have whitewashes on them too.
        named = " ".join(i.subject for i in self.report.issues
                         if i.rule == "set-no-games")
        for slot in self.WHITEWASH_SLOTS:
            with self.subTest(slot=slot):
                self.assertNotIn(slot, named)

    def test_a_rubber_that_really_stopped_level_is_still_a_tie(self):
        tied = self.issues("set-tied")
        self.assertEqual([i.subject.split()[-1] for i in tied], [self.TIED_SLOT])
        self.assertIn("level at 3-3", tied[0].message)

    def test_the_empty_rubbers_are_not_also_reported_as_ties(self):
        # Both branches firing would double-report, and the pair of messages
        # disagree with each other about what happened.
        subjects = " ".join(i.subject for i in self.issues("set-tied"))
        for slot in self.EMPTY_SLOTS:
            with self.subTest(slot=slot):
                self.assertNotIn(slot, subjects)

    def test_none_of_this_stops_the_run(self):
        # An empty row is a fact about the night, not a misread card: the games
        # that are on the card are still transcribed and still count.
        for rule in ("set-no-games", "set-tied"):
            with self.subTest(rule=rule):
                self.assertEqual([i for i in self.report.errors
                                  if i.rule == rule], [])

    # --- and the two teams met, whatever the card decided ------------------
    #
    # Nine games each, two sets short of a result. The pairing record is what the
    # dashboard's head-to-head grid and every team's opponent table are drawn from,
    # and it counted the decided meetings only -- so a card that is on the fixture
    # list, in the folder, and in both teams' games columns was a blank cell whose
    # hover read "not played yet".
    #
    # "hilltop-aces" sorts before "riverside-rockets", so the aces are `low` in the
    # key and in each two-ended figure below.

    def record(self) -> dict:
        return self.season["head_to_head"][stats.h2h_key(self.HOME, self.AWAY)]

    def test_the_pairing_counts_the_night_they_played(self):
        record = self.record()
        self.assertEqual(record["played"], 2)      # round 1, and this card
        self.assertEqual(record["undecided"], 1)

    def test_counting_the_night_does_not_invent_a_result_for_it(self):
        # Round one, 24-6 to the rockets, is the only result these two have. A
        # meeting in the played count and in neither win column is exactly what
        # `undecided` is for; a draw is a result, and this is not one.
        record = self.record()
        self.assertEqual(record["wins_low"], 0)           # aces
        self.assertEqual(record["wins_high"], 1)          # rockets
        self.assertEqual(record["draws"], 0)

    def test_a_pairing_whose_meetings_all_reached_a_result_counts_none(self):
        # The other end of the count. One decided meeting and one undecided is
        # indistinguishable from the count taken of the wrong ones, so a pairing
        # with a result and nothing else is asserted as well.
        record = self.season["head_to_head"][
            stats.h2h_key("riverside-rockets", "lakeside-spinners")]
        self.assertEqual(record["played"], 1)
        self.assertEqual(record["undecided"], 0)

    def test_the_games_of_that_night_are_in_the_pairing_and_on_the_ladder(self):
        # 24-6 in round one and 9-9 here. The ladder's games column asks only
        # whether a card was played, so a pairing that counted the decided
        # meetings alone disagreed with the row above it: the rockets' 79 games
        # for included these nine and their 24 against the aces did not.
        record = self.record()
        self.assertEqual(record["games_low"], 6 + 9)      # aces
        self.assertEqual(record["games_high"], 24 + 9)    # rockets
        rockets, = [t for t in self.season["teams"] if t["slug"] == self.HOME]
        self.assertEqual(rockets["games_won"], EXPECTED_GAMES_FOR[self.HOME] + 9)


# --- round four, a side short -----------------------------------------------
#
# Cards on which a side did not have the players for every rubber. At module level
# because test_cli runs build.py on the same two cards whose figures the class
# below works out, and the report and the figures have to be about one pair of
# cards.

SHORT_ROUND, SHORT_DATE = "4", "2026-09-08"
# The TOTAL row of the rockets' card, as the site prints it: the side that turned
# up is credited the rubbers the other side forfeited. Stated rather than derived
# so that a reader which credits them to nobody is contradicted by the card in
# front of it, which is what a club's run would show.
SHORT_TOTALS = (3, 8, 8, 1)


def short_spec(home: str, away: str, *, date: str = SHORT_DATE,
               round_label: str = SHORT_ROUND, **fields) -> cw.CardSpec:
    """A card between two of the season's teams, both rosters printed."""
    home_name, home_players = TEAMS[home]
    away_name, away_players = TEAMS[away]
    return cw.CardSpec(
        date=date, round_label=round_label, division="B",
        home_name=home_name, away_name=away_name,
        home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(home_players)),
        away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(away_players)),
        **fields)


def two_short_rows() -> tuple[cw.SetRow, ...]:
    """Rockets v aces: both doubles played and level, both singles forfeited."""
    _, home_players = TEAMS["riverside-rockets"]
    _, away_players = TEAMS["hilltop-aces"]
    pair_home, pair_away = home_players[:2], away_players[:2]
    return (
        cw.SetRow(6, 2, True, pair_home, pair_away),
        # No away name on either: nobody came, so there was nobody to print.
        cw.SetRow(0, 0, False, (home_players[0],), (),
                  status=SetStatus.FORFEIT, status_side="away"),
        cw.SetRow(0, 0, False, (home_players[1],), (),
                  status=SetStatus.FORFEIT, status_side="away"),
        cw.SetRow(2, 6, True, pair_home, pair_away),
    )


def one_short_rows() -> tuple[cw.SetRow, ...]:
    """Spinners v wanderers: three rubbers played, the second doubles forfeited."""
    _, home_players = TEAMS["lakeside-spinners"]
    _, away_players = TEAMS["parkview-wanderers"]
    pair_home, pair_away = home_players[:2], away_players[:2]
    return (
        cw.SetRow(6, 3, True, pair_home, pair_away),
        cw.SetRow(3, 6, False, (home_players[0],), (away_players[0],)),
        cw.SetRow(6, 4, False, (home_players[1],), (away_players[1],)),
        cw.SetRow(0, 0, True, pair_home, (),
                  status=SetStatus.FORFEIT, status_side="away"),
    )


def write_short_round(data_dir: Path) -> tuple[Path, Path]:
    """Both round-four cards; the second one's TOTAL row derived by the writer."""
    folder = data_dir / f"Round{SHORT_ROUND}"
    folder.mkdir(parents=True, exist_ok=True)
    stated = cw.write_card_file(folder / "two-short.pdf", short_spec(
        "riverside-rockets", "hilltop-aces", court="10", match_id="4000010",
        rows=two_short_rows(), totals=SHORT_TOTALS, verb="DEFEATS",
        winner="home"))
    derived = cw.write_card_file(folder / "one-short.pdf", short_spec(
        "lakeside-spinners", "parkview-wanderers", court="11",
        match_id="4000011", rows=one_short_rows()))
    return stated, derived


# --- a match played that decides nothing --------------------------------------
#
# Cards on which fewer sets finished than a result needs. At module level because
# test_cli runs build.py on the same cards the class below reads with validate,
# and the reminder and the warnings have to be about one set of cards.

SHORT_OF_RESULT_DATES = {"4": "2026-09-08", "5": "2026-09-15", "6": "2026-09-22"}
# Finished sets, all won at home, then sets that stopped short under first-to-6.
FINISHED_ROWS = ((6, 2), (6, 3), (6, 4), (6, 1))
UNFINISHED_ROWS = ((4, 3), (3, 2), (2, 1), (1, 0))


def write_short_card(data_dir: Path, round_label: str, date: str, home: str,
                     away: str, *, finished: int = 2, marked: bool = True,
                     verb: str = cw.AUTO, winner: str = cw.AUTO,
                     disputed: bool = False, **fields) -> Path:
    """One card on which only ``finished`` of the four sets finished.

    With ``marked`` the sets that stopped are ticked as retired, which is a card
    saying why; without it they are left as played at 4-3 and 3-2, which is a set
    nobody said was stopped and may be a set length read wrongly.
    """
    _, home_players = TEAMS[home]
    _, away_players = TEAMS[away]
    scores = FINISHED_ROWS[:finished] + UNFINISHED_ROWS[:4 - finished]
    rows = list(rows_for(scores, home_players, away_players))
    if marked:
        for index in range(finished, 4):
            rows[index] = dataclasses.replace(rows[index], status=SetStatus.RETIRED,
                                              status_side="away")
    teams = list(TEAMS)
    fields.setdefault("match_id", f"5{int(round_label):03d}0"
                                  f"{teams.index(home)}{teams.index(away)}")
    fields.setdefault("court", f"3{teams.index(home)}")
    folder = data_dir / f"Round{round_label}"
    folder.mkdir(parents=True, exist_ok=True)
    home_name, away_name = TEAMS[home][0], TEAMS[away][0]
    return cw.write_card_file(
        folder / f"{home_name.title().replace(' ', '')}-"
                 f"{away_name.title().replace(' ', '')}.pdf",
        short_spec(home, away, date=date, round_label=round_label,
                   rows=tuple(rows), verb=verb, winner=winner, disputed=disputed,
                   **fields))


class TestADisputedCardShortOfItsSetsSaysWhyItHasNoResult(SeasonCase):
    """A disputed card that falls short of a result, and its two neighbours.

    Measured on the tree before this: a short card with Match Disputed ticked drew
    "The result is counted as printed until the club says otherwise." and then,
    for the same match, "recorded as disputed, so it is not counted in the
    ladder." Neither is true. There is no result to count, and the match has none
    because 2 of its 3 sets finished, which is the one thing never said.

    Rejected: mending only the no-result line. The disputed line would still claim
    a result is counted, and saying "see no-result" instead would be false for an
    unattributed forfeit, which draws no no-result warning at all.
    """

    @classmethod
    def extra_cards(cls):
        dates = SHORT_OF_RESULT_DATES
        write_short_card(cls.data, "4", dates["4"], "riverside-rockets",
                         "hilltop-aces", disputed=True)
        write_short_card(cls.data, "4", dates["4"], "lakeside-spinners",
                         "parkview-wanderers", finished=3, disputed=True)
        write_short_card(cls.data, "5", dates["5"], "riverside-rockets",
                         "lakeside-spinners", verb="VS", winner=cw.NO_TICK,
                         cancelled=True, disputed=True)

    def said(self, rule: str, label: str) -> str:
        found = [i.message for i in self.report.issues
                 if i.rule == rule and i.subject == label]
        self.assertEqual(len(found), 1, (rule, label, found))
        return found[0]

    def test_the_short_match_says_how_many_sets_it_was_short(self):
        self.assertIn("only 2 of the 3 sets needed for a result are complete",
                      self.said("no-result", "R4 riverside-rockets v hilltop-aces"))

    def test_the_short_match_is_not_said_to_have_a_result_to_count(self):
        message = self.said("disputed", "R4 riverside-rockets v hilltop-aces")
        self.assertNotIn("counted as printed", message)
        self.assertIn("has no result for the ladder to count", message)

    def test_a_disputed_match_that_has_a_result_still_counts_it_as_printed(self):
        self.assertIn("counted as printed",
                      self.said("disputed", "R4 lakeside-spinners v parkview-wanderers"))
        self.assertFalse([i for i in self.report.issues if i.rule == "no-result"
                          and i.subject == "R4 lakeside-spinners v parkview-wanderers"])

    def test_a_disputed_washout_is_still_said_to_be_cancelled(self):
        label = "R5 riverside-rockets v lakeside-spinners"
        self.assertIn("recorded as cancelled", self.said("no-result", label))
        self.assertNotIn("counted as printed", self.said("disputed", label))


class TestTheSetsAResultNeedsAreReadFromOnePlace(unittest.TestCase):
    """``Config.min_sets_for``: a competition's own number, else ``[rules]``, else none.

    The pair was resolved twice, in ``declared_format`` and in discovery's own
    reading of the shape, each as ``own or rules``, and the review list needed a
    third answer to know whether anybody had said. ``or`` also read a 0 as unset.

    Rejected: a flag on the built competition saying the number was declared,
    which is a fourth copy of the same question.
    """

    def test_a_competitions_own_number_wins_and_nothing_said_is_none(self):
        own = config_mod.CompetitionConfig(id="tuesday-division-b",
                                           min_sets_for_result=2)
        silent = config_mod.CompetitionConfig(id="wednesday-division-b")
        rules = config_mod.Config(path=Path("config.toml"), min_sets_for_result=3)
        unset = config_mod.Config(path=Path("config.toml"))
        self.assertEqual([rules.min_sets_for(c) for c in (own, silent, None)],
                         [2, 3, 3])
        self.assertEqual([unset.min_sets_for(c) for c in (own, silent, None)],
                         [2, None, None])

    def test_a_pinned_shape_takes_its_number_from_the_same_place(self):
        # declared_format asked the pair its own way, so it is asked here too.
        own = config_mod.CompetitionConfig(id="tuesday-division-b",
                                           format="doubles-singles-doubles",
                                           min_sets_for_result=2)
        rules = config_mod.Config(path=Path("config.toml"), min_sets_for_result=3)
        self.assertEqual(rules.declared_format(own).min_sets_for_result, 2)

    def test_discovery_gives_one_competition_its_number_and_leaves_the_other_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            write_season_cards(data)
            write_short_card(data, "4", SHORT_OF_RESULT_DATES["4"],
                             "riverside-rockets", "hilltop-aces")
            # A Wednesday, so a competition of its own that declares nothing.
            write_short_card(data, "4", "2026-09-09", "lakeside-spinners",
                             "parkview-wanderers")
            path = root / "config.toml"
            path.write_text('[competitions.tuesday-division-b]\nday = "Tuesday"\n'
                            'division = "B"\nmin_sets_for_result = 2\n',
                            encoding="utf-8")
            config = config_mod.load(path)
            cards, rejected = scorecard.read_tree(data)
            found = discover_mod.discover(cards, rejected, config=config, root=data)
        tuesday, = [m for m in found.matches["tuesday-division-b"] if m.round == "4"]
        wednesday, = found.matches["wednesday-division-b"]
        self.assertEqual((tuesday.fmt.min_sets_for_result, tuesday.decided), (2, True))
        self.assertEqual((wednesday.fmt.min_sets_for_result, wednesday.decided,
                          wednesday.short_of_result), (3, False, True))


class TestARubberNobodyCameForGoesToTheSideThatDid(SeasonCase):
    """A team two players short, and the two rubbers it forfeited.

    The reader has always read the ``Forfeit`` box beside each row, and the row it
    built kept which side's box was ticked. Turning that row into a set threw the
    ticks away, so who won a forfeited rubber came down to which side had more games
    printed on it -- and a rubber nobody came for has no games to print. Measured on
    this card before the fix: 0-0, and the rubber went to nobody.

    What that costs is not one column. The rockets are credited sets 1-1 on a card
    whose four rubbers all have a winner; the night is a **draw** rather than a win,
    so both teams take 3.8 points off it instead of 4.8 and 2.8; the printed TOTAL
    row says home sets 3 and the set rows add to 1, which is reported as
    ``totals-mismatch``, an ERROR; and the card's own ``DEFEATS`` is reported as
    ``verb-mismatch``, another one. Two errors and a non-zero exit on a card that is
    filled in correctly, and the fix a club would reach for is to delete a rubber
    somebody won.

    It is also the one thing the fixtures of this suite could not say. ``tools/demo.py``
    writes its forfeited rubbers with the games filled in as played, and the comment
    beside them used to give the reason: "Writing 0-0 here would make every
    forfeited set a draw". That was the defect, written down as a constraint.

    The shape is chosen so that nothing else can carry the result. Games finish level
    at 8-8, so the two forfeited rubbers are the only thing separating the two sides:
    a fixture whose winner was ahead on games anyway passes whether the rubbers are
    credited or not. Nobody is named on the forfeiting side of them, which is what a
    card looks like when nobody came, and it is why a rubber awarded to a team can be
    awarded to no person.

    The second card is the writer's half. Its TOTAL row is derived rather than stated,
    so a writer that counts a forfeited rubber differently from the reader is caught
    here -- rather than by every fixture in the suite that has one on it, all at once.
    """

    ROUND = SHORT_ROUND
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    OTHER_HOME, OTHER_AWAY = "lakeside-spinners", "parkview-wanderers"
    FORFEITED = ("s1", "s2")

    @classmethod
    def extra_cards(cls):
        cls.card, cls.derived = write_short_round(cls.data)

    def match(self, home=None):
        home = home or self.HOME
        found, = [m for m in self.matches
                  if m.round == self.ROUND and m.home == home]
        return found

    def sets_of(self, match):
        return {result.slot.key: result for result in match.sets}

    def errors_about(self, match, rule=None):
        # Card-level findings name the file, match- and set-level ones name the
        # fixture. Both spellings, so a check that moved between the two is still
        # counted here.
        label = f"R{match.round} {match.home} v {match.away}"
        return sorted(i.rule for i in self.report.errors
                      if i.subject == match.source or i.subject == label
                      or i.subject.startswith(f"{label} ")
                      if rule is None or i.rule == rule)

    # --- the row ------------------------------------------------------------

    def test_the_rubbers_nobody_came_for_are_forfeited_against_one_side(self):
        # The premise. The ticks were always read; what changed is that they now
        # survive the trip from the page to the match.
        sets = self.sets_of(self.match())
        for key in self.FORFEITED:
            with self.subTest(slot=key):
                result = sets[key]
                self.assertIs(result.status, SetStatus.FORFEIT)
                self.assertTrue(result.away_forfeit)
                self.assertFalse(result.home_forfeit)
                self.assertEqual((result.home_games, result.away_games), (0, 0))

    def test_each_forfeited_rubber_goes_to_the_side_that_turned_up(self):
        # 0-0 on the row, so the winner cannot have come from the games.
        sets = self.sets_of(self.match())
        for key in self.FORFEITED:
            with self.subTest(slot=key):
                self.assertEqual(sets[key].winner, "home")

    # --- the night ----------------------------------------------------------

    def test_the_sets_column_counts_all_four_rubbers(self):
        # The doubles went one each way, so a change that credited every row to the
        # side that turned up would read 4-0 here rather than 3-1.
        match = self.match()
        self.assertEqual((match.home_sets, match.away_sets), (3, 1))
        self.assertEqual(match.completed_sets, 4)

    def test_the_night_is_a_win_and_not_a_draw(self):
        # Level on games at 8-8, so the sets are what separate them, and two of the
        # three the rockets are credited are rubbers the aces did not come for.
        match = self.match()
        self.assertEqual(match.home_games, match.away_games)
        self.assertEqual(match.winner, "home")
        self.assertFalse(match.is_draw)
        self.assertEqual(match.decided_by, "sets")

    def test_the_points_are_a_win_and_a_loss_rather_than_two_draws(self):
        # games-bonus, worked out on paper: win 4 + 0.8 for eight games, loss 2 +
        # 0.8. A draw would have paid 3.8 to each of them, which is the figure the
        # ladder carried before the ticks reached the set.
        match = self.match()
        self.assertAlmostEqual(match.points_for(self.HOME), 4.8)
        self.assertAlmostEqual(match.points_for(self.AWAY), 2.8)

    # --- the card's own cross-checks ----------------------------------------

    def test_the_printed_total_row_and_the_set_rows_agree(self):
        self.assertEqual(self.errors_about(self.match(), "totals-mismatch"), [])

    def test_the_card_says_who_won_and_is_not_contradicted(self):
        self.assertEqual(self.errors_about(self.match(), "verb-mismatch"), [])

    def test_nothing_about_this_card_stops_the_run(self):
        # build.py's exit status is decided from this report, so the report is what
        # is asserted here: nothing at all about the card, and nothing anywhere in
        # the season. test_cli runs build.py on the same cards and reads the status.
        self.assertEqual(self.errors_about(self.match()), [])
        self.assertTrue(self.report.ok)

    # --- who is credited ----------------------------------------------------

    def test_the_player_who_turned_up_is_credited_the_rubber(self):
        # Ana plays both doubles and the first singles every week: nine rubbers over
        # the three counted rounds, all won, and three more here -- the doubles the
        # rockets took, the doubles they lost, and the singles nobody came for. Eleven
        # won of twelve, and the twelfth is the one she lost on court. Ten of twelve
        # with one in neither column is what her record read before the fix: a rubber
        # in her played count that she is shown neither winning nor losing.
        players = {p["slug"]: p for p in self.season["players"]}
        ana = players["ana-fernandez"]
        self.assertEqual(ana["sets_played"], 12)
        self.assertEqual(ana["sets_won"], 11)
        self.assertEqual(ana["sets_lost"], 1)

    def test_a_rubber_credited_to_a_team_is_credited_to_no_absent_person(self):
        # Nobody was printed on the away side of either forfeited row, so the two
        # sets the aces lost are in the team's column and in nobody's record. The
        # aces' own captain played the two doubles and only those.
        players = {p["slug"]: p for p in self.season["players"]}
        dev = players["dev-raman"]
        self.assertEqual(dev["sets_played"], 11)     # 9 + both doubles here
        self.assertEqual(dev["sets_won"], 3)         # 2 + the second doubles
        self.assertEqual(dev["sets_lost"], 8)        # 7 + the first
        self.assertEqual(self.match().sets_against(self.AWAY), 3)

    # --- the writer's half --------------------------------------------------

    def test_a_card_this_tool_writes_totals_its_own_forfeited_rubber(self):
        # The TOTAL row on the second card is derived, not stated. Read back, it has
        # to say sets 3-1: three rubbers the spinners are credited, one of them
        # forfeited. A writer that counts it the old way prints 2-1 and every
        # fixture in this suite carrying a forfeited row starts disagreeing with the
        # reader about the card it was handed.
        card, = [c for c in self.cards if c.source == str(self.derived)]
        self.assertEqual((card.totals.home_sets, card.totals.away_sets), (3, 1))
        match = self.match(self.OTHER_HOME)
        self.assertEqual((match.home_sets, match.away_sets), (3, 1))
        self.assertEqual(self.errors_about(match), [])


def conceded_rows(players: tuple[str, ...], *, by: str) -> tuple[cw.SetRow, ...]:
    """Four rubbers at 0-0, every one ticked ``Forfeit`` against ``by``.

    ``players`` is the side that turned up, printed on the home side of each row;
    nobody is printed opposite, because nobody came. Empty, and nobody is printed
    anywhere.
    """
    return tuple(
        cw.SetRow(0, 0, doubles,
                  (players[:2] if doubles else (players[index],)) if players
                  else (), (),
                  status=SetStatus.FORFEIT, status_side=by)
        for index, doubles in enumerate((True, False, False, True)))


class TestACardOfRubbersNobodyCameForIsANightThatHappened(SeasonCase):
    """Every rubber forfeited by one side, and the ``Match Forfeited`` box left alone.

    The limiting case of the class above, and the one its fix did not reach. The
    aces sent nobody: all four rows are 0-0 with the aces' ``Forfeit`` box ticked
    and nobody printed on their side, and the card's own TOTAL row, verb and
    ``Match Winner`` box say the rockets won 4-0.

    Whether a card is a night that happened was decided from the games alone, and a
    card with no games on it was ``UNPLAYED``: a fixture still to come. Measured
    before this item: exit 1 on a ``totals-mismatch`` and a ``verb-mismatch``, and
    the match recorded as unplayed. Measured with the rubbers credited and the status
    still read off the games: exit 0 and no error at all, sets 4-0 on the page beside
    "recorded as unplayed, so it is not counted in the ladder", the rockets and the
    aces a match behind the other two (F10), and nothing paid to anybody for the
    night. The two errors had been the only loud sign that anything was wrong, and
    crediting the rubbers took them away.

    So a rubber the card awards is evidence that the night happened, as a game is.
    What is still ``UNPLAYED`` is a card with nothing on it: no games, and no row
    that names a winner. That includes a card ticking both ``Forfeit`` boxes on every
    row, which is the round-five card here, and which stays a night nobody played
    rather than becoming a draw paid to two teams that both stayed at home.

    Rejected: reading four conceded rubbers as a whole-match forfeit. The card has a
    box for that, it was not ticked, and ``forfeit_award`` prices that box; ticking
    it on the club's behalf would price the night by a rule the card never invoked.

    And 0-0 is not a contest, which :class:`TestALevelScorelineSaysWhatDecidedTheMatch`
    already says of a washout. Measured with only the status fixed: ``tied_on_games``
    true and ``decided_by`` ``"sets"``, so the page printed "level on games, decided
    on sets" beside a night on which nobody played a game.
    """

    ROUND = SHORT_ROUND
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    # The other two teams' round-four night, played, so that every team has four
    # matches and the ladder is complete once the walkover counts.
    OTHER_HOME, OTHER_AWAY = "lakeside-spinners", "parkview-wanderers"
    OTHER_SCORES = ((6, 3), (3, 6), (6, 4), (6, 2))      # 21-15, sets 3-1
    NOBODY_ROUND = "5"

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        _, rockets = TEAMS[cls.HOME]
        cw.write_card_file(folder / "walkover.pdf", short_spec(
            cls.HOME, cls.AWAY, court="10", match_id="4000010",
            rows=conceded_rows(rockets, by="away"), totals=(4, 0, 0, 0),
            verb="DEFEATS", winner="home"))
        _, spinners = TEAMS[cls.OTHER_HOME]
        _, wanderers = TEAMS[cls.OTHER_AWAY]
        cw.write_card_file(folder / "played.pdf", short_spec(
            cls.OTHER_HOME, cls.OTHER_AWAY, court="11", match_id="4000011",
            rows=rows_for(cls.OTHER_SCORES, spinners, wanderers)))
        # Both boxes ticked on every row: a card with no winner anywhere on it.
        folder = cls.data / f"Round{cls.NOBODY_ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "nobody.pdf", short_spec(
            cls.HOME, cls.OTHER_HOME, date="2026-09-15",
            round_label=cls.NOBODY_ROUND, court="12", match_id="5000012",
            rows=conceded_rows((), by="both"), totals=(0, 0, 0, 0),
            verb="DRAWS WITH", winner=cw.NO_TICK))

    def match(self, rnd=None):
        found, = [m for m in self.matches
                  if m.round == (rnd or self.ROUND) and m.home == self.HOME]
        return found

    def exported(self):
        found, = [m for m in self.season["matches"]
                  if m["round"] == self.ROUND and m["home"] == self.HOME]
        return found

    def test_every_rubber_is_awarded_to_the_side_that_turned_up(self):
        # The premise, and the class above's fix: four rubbers, four winners.
        self.assertEqual([(s.status, s.home_games, s.away_games, s.winner)
                          for s in self.match().sets],
                         [(SetStatus.FORFEIT, 0, 0, "home")] * 4)

    def test_the_night_happened(self):
        self.assertIs(self.match().status, MatchStatus.PLAYED)

    def test_it_is_a_win_and_a_loss(self):
        # games-bonus on paper: a win is 4 and a loss 2, plus a tenth per game won,
        # and nobody won a game.
        match = self.match()
        self.assertEqual(match.winner, "home")
        self.assertEqual(match.decided_by, "sets")
        self.assertAlmostEqual(match.points_for(self.HOME), 4.0)
        self.assertAlmostEqual(match.points_for(self.AWAY), 2.0)

    def test_the_ladder_counts_it_and_every_team_has_played_four(self):
        # Worked out on paper from EXPECTED_POINTS: rockets 19.0 + 4.0; spinners
        # 15.8 + 6.1 for a 21-game win; wanderers 11.1 + 3.5 for a 15-game loss;
        # aces 10.7 + 2.0. And every team four matches, so the order is settled.
        teams = {t["slug"]: t for t in self.season["teams"]}
        for slug, points in (("riverside-rockets", 23.0),
                             ("lakeside-spinners", 21.9),
                             ("parkview-wanderers", 14.6),
                             ("hilltop-aces", 12.7)):
            with self.subTest(team=slug):
                self.assertEqual(teams[slug]["played"], 4)
                self.assertAlmostEqual(teams[slug]["points"], points, places=6)
        self.assertTrue(self.season["meta"]["ladder_complete"])

    def test_nothing_is_reported_about_the_card(self):
        # Not an error and not "recorded as unplayed": the card is filled in
        # correctly, and it says what happened.
        match = self.match()
        label = f"R{match.round} {match.home} v {match.away}"
        self.assertEqual([(i.severity, i.rule) for i in self.report.issues
                          if i.subject in (match.source, label)
                          or i.subject.startswith(f"{label} ")], [])

    def test_it_is_not_described_as_level_on_games(self):
        row = self.exported()
        self.assertEqual((row["home_games"], row["away_games"]), (0, 0))
        self.assertEqual(row["decided_by"], "sets")
        self.assertFalse(row["tied_on_games"],
                         "nobody played a game, so nobody was level on games")

    def test_a_card_that_names_no_winner_anywhere_is_a_night_nobody_played(self):
        # The boundary. Both boxes ticked on every row name nobody, so there is
        # nothing on the card a result could be made of, and counting it would pay a
        # draw to two teams that both stayed at home.
        match = self.match(self.NOBODY_ROUND)
        self.assertIs(match.status, MatchStatus.UNPLAYED)
        self.assertIsNone(match.points_for(self.HOME))


def cards_in_one_file(*cards: bytes) -> bytes:
    """One PDF of the cards ``cardwriter`` wrote, a page each, in the order given.

    The writer makes one page, its page object 3 and its content stream object 6.
    The first card's objects are kept; each card after it adds a copy of that page
    object pointing at the card's own content stream, the way ``with_blank_pages``
    in ``test_cli`` adds empty ones, and the cross-reference table is written again
    the way ``cardwriter._assemble`` writes it.
    """
    def objects_of(card: bytes) -> dict[int, bytes]:
        found = {int(number): body for number, body in
                 re.findall(rb"(?s)(\d+) 0 obj\n(.*?)\nendobj\n", card)}
        if b"/Contents 6 0 R" not in found.get(3, b""):
            raise AssertionError("the card writer no longer puts its page in object "
                                 "3 with its content in object 6; say where it does "
                                 "now")
        return found
    objects = objects_of(cards[0])
    kids = [3]
    for card in cards[1:]:
        page = max(objects) + 1
        objects[page] = objects[3].replace(b"/Contents 6 0 R",
                                           b"/Contents %d 0 R" % (page + 1))
        objects[page + 1] = objects_of(card)[6]
        kids.append(page)
    objects[2] = (b"<< /Type /Pages /Kids [ " + b" ".join(b"%d 0 R" % kid for kid in kids)
                  + b" ] /Count %d >>" % len(kids))
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref_at, size = len(out), max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        out += b"%010d 00000 %s \n" % (offsets.get(number, 0),
                                       b"n" if number in offsets else b"f")
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (size, xref_at))
    return bytes(out)


class CardsBesideTheSeasonCase(SeasonCase):
    """The season and cards between its teams beside it, each card's findings whole."""

    @classmethod
    def spec(cls, rnd, home, away, scores, **flags):
        _, home_players = TEAMS[home]
        _, away_players = TEAMS[away]
        teams = list(TEAMS)
        rows = flags.pop("rows", None) or rows_for(scores, home_players, away_players)
        return short_spec(
            home, away, date=SHORT_OF_RESULT_DATES[rnd], round_label=rnd,
            court=f"2{teams.index(home)}", match_id=f"6{rnd}0000{teams.index(home)}",
            rows=rows, **flags)

    @classmethod
    def card(cls, name, rnd, home, away, scores, **flags):
        folder = cls.data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        return cw.write_card_file(folder / f"{name}.pdf",
                                  cls.spec(rnd, home, away, scores, **flags))

    def about(self, where, issues=None):
        """Every finding naming the card read at *where*, or its match, whole.

        *where* is the card's path, with `` page 2`` after it for a second page, and
        is matched whole, since the first page's path begins the second's.
        """
        where = str(where)
        named = re.compile(re.escape(where) + r"(?! page \d)")
        labels = {f"R{m.round} {m.home} v {m.away}" for m in self.matches
                  if m.source == where}
        return [i for i in (self.report.issues if issues is None else issues)
                if where in (i.subject, i.detail) or named.search(i.message)
                or any(i.subject == label or i.subject.startswith(f"{label} ")
                       for label in labels)]

    def f6(self, where, check, detail, held_to="the set rows"):
        return validate.Issue(
            rule="F6", severity=validate.ERROR,
            message=f"{where}: the {check} cross-check disagrees with {held_to}, "
                    "so this card's numbers are not trustworthy.",
            subject=str(where), detail=detail)

    def no_result(self, where, label, recorded):
        return validate.Issue(
            rule="no-result", severity=validate.WARNING,
            message=f"{label}: recorded as {recorded}, so it is not counted in the "
                    "ladder.",
            subject=label, detail=str(where))


class TestACardThatCountsOrWasReadEmptyIsStillCrossChecked(CardsBesideTheSeasonCase):
    """The checks a washout is spared, still run on every card whose reading matters.

    A card its own Match Cancelled box calls off is held to nothing it prints beside
    its rows. Every other card is still held to them by F6 and by its TOTAL row, and
    the verb checks pass by one only to leave a verb unheld to a result its match
    does not have. Six cards beyond the season, each a way those gates could be
    drawn wider or narrower than that.

    A card that counts, printing ``DRAWS WITH`` over a 3-1 home win. A card read 0-0
    on every row, which is how a night not yet played is read, printing ``DEFEATS``
    and ticking home, which says the zeros were misread; it is the second page of
    the first card's file, so it is found by its page. Two forfeits with Match
    Cancelled ticked too, which the model reads as forfeits, so they count: one
    ticking as winner the side that forfeited, and one printing ``VS``. A card read
    0-0 printing ``TROUNCES``, a word this program does not know. And a card of four
    rubbers each ticked Forfeit against both sides, which is read as nobody winning
    any and so as a night nobody played, printing ``DEFEATS``: rows finished by
    their boxes carry its verb past ``verb-no-result`` to ``verb-mismatch``.

    Measured on the tree before this (/tmp/t6/scratch/m22/fix/probe.py, each card
    beside the season through build.py, cases s, t, p, m, k and o): the first gives
    F6 and ``verb-mismatch``, exit 1; the second F6 for its verb and for its tick,
    ``verb-no-result`` and ``no-result``, exit 1; the third F6 for its tick, exit 1;
    the fourth nothing, exit 0; the fifth ``no-result`` and ``verb-unknown``, exit
    0; the sixth F6, ``verb-mismatch`` and ``no-result``, exit 1. Only the second's
    ``verb-no-result``, the fourth's silence and the sixth's ``verb-mismatch``
    change. Rejected: F6 passing by every card that counts for nothing, as the row
    was filed. Its rows are what made the second card count for nothing, and with
    its TOTAL row printed 0-0 that gate took the run from exit 1 to exit 0 with only
    ``recorded as unplayed`` left. And rejected, measured on a first version of this
    change: every verb check passing by a card that counts for nothing, which took
    the fifth card's ``verb-unknown``, the one finding naming the word its zeros may
    have been misread under.
    """

    @classmethod
    def extra_cards(cls):
        decided = cls.spec("4", "riverside-rockets", "hilltop-aces",
                           ((6, 2), (6, 3), (4, 6), (6, 1)),
                           verb="DRAWS WITH", winner=cw.NO_TICK)
        zeros = cls.spec("4", "lakeside-spinners", "parkview-wanderers",
                         ((0, 0),) * 4, verb="DEFEATS", winner="home")
        path = cls.data / "Round4" / "both.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cards_in_one_file(cw.write_card(decided),
                                           cw.write_card(zeros)))
        cls.decided, cls.zeros = str(path), f"{path} page 2"
        cls.ticked = cls.card("ticked", "5", "riverside-rockets", "lakeside-spinners",
                              ((0, 0),) * 4, cancelled=True, forfeited_by="away",
                              verb="DEFEATS", winner="away")
        cls.vs = cls.card("vs", "5", "hilltop-aces", "parkview-wanderers",
                          ((0, 0),) * 4, cancelled=True, forfeited_by="away",
                          verb="VS", winner=cw.NO_TICK)
        cls.unknown = cls.card("unknown", "6", "riverside-rockets",
                               "parkview-wanderers", ((0, 0),) * 4,
                               verb="TROUNCES", winner=cw.NO_TICK)
        cls.conceded = cls.card("conceded", "6", "lakeside-spinners", "hilltop-aces",
                                None, rows=conceded_rows((), by="both"),
                                totals=(0, 0, 0, 0), verb="DEFEATS",
                                winner=cw.NO_TICK)

    def test_a_card_that_counts_and_prints_a_draw_over_a_win_is_one_verb_mismatch(self):
        # Pinned whole, for the report a gate must not take away. It was F6 from
        # the reader's verb check and verb-mismatch from validate's own reading of
        # the same rows, one mistake reported twice on a card that counts. Now F6
        # asks verb-mismatch where it reports, and passes the verb by there, so
        # it is one report, the one that holds the verb to the result under the
        # club's rules (4.131). The reader still says its verb check disagrees.
        path = self.decided
        card, = [c for c in self.cards if c.source == path and c.page == 0]
        check, = [c for c in card.checks if c.name == "verb"]
        self.assertEqual((check.agrees, check.reported_by), (False, ""))
        self.assertEqual(self.about(path), [
            validate.Issue(
                rule="verb-mismatch", severity=validate.ERROR,
                message=f"{path}: the verb 'DRAWS WITH' says the match was drawn, "
                        "but the set rows give home.",
                subject=str(path),
                detail="verb 'DRAWS WITH' says draw; sets 3-1 and games 22-12 "
                       "decided on games then sets say home")])
        self.assertFalse(self.report.ok)

    def test_a_card_read_empty_that_prints_a_winner_still_fails_f6(self):
        # Its verb is not held to a result, as a night not yet played has none for
        # it to state; F6 still holds its verb and its tick to the zeros they
        # contradict. The second page of a file whose first page counts: asked of
        # the file alone, the question whether it counts finds that match.
        path = self.zeros
        self.assertEqual(self.about(path), [
            self.f6(path, "verb", "the card prints 'DEFEATS', making home the winner, "
                    "but the set rows give games 0-0 (level) and at most 0-0 sets "
                    "(level)"),
            self.f6(path, "winner-tick", "the card ticks home as Match Winner, but "
                    "the set rows give games 0-0 (level) and at most 0-0 sets "
                    "(level)"),
            self.no_result(path, "R4 lakeside-spinners v parkview-wanderers",
                           "unplayed")])

    def test_a_forfeit_with_its_cancelled_box_ticked_too_counts_and_is_checked(self):
        # The model reads the Match Forfeited box first, so both are forfeits, paid
        # and counted. A gate asked of the Cancelled box passes both by: the tick
        # naming the side that forfeited goes unreported, and the VS where the
        # forfeit's result should be went unsaid before this for exactly that.
        self.assertEqual({m.status for m in self.matches
                          if m.source in (str(self.ticked), str(self.vs))},
                         {MatchStatus.FORFEIT})
        self.assertEqual(self.about(self.ticked), [
            self.f6(self.ticked, "winner-tick", "the card ticks away as Match Winner, "
                    "but the away Match Forfeited box is ticked, giving the match to "
                    "home, and the set rows give no result",
                    held_to="the away Match Forfeited box")])
        self.assertEqual(self.about(self.vs), [validate.Issue(
            rule="verb-absent", severity=validate.WARNING,
            message=f"{self.vs}: the card prints 'VS' between the team names instead "
                    "of a result, which is what Match Centre prints for a fixture it "
                    "holds no result for. The set rows were still read and scored; "
                    "what could not run is this one cross-check against them.",
            subject=str(self.vs), detail="VS")])

    def test_a_card_read_empty_still_names_a_verb_nobody_can_read(self):
        # Whether its verb can be read is a question of how the card was read, so a
        # card read empty is asked it as every card in play is; whether the verb
        # states a result is not asked of it.
        path = self.unknown
        self.assertEqual(self.about(path), [
            self.no_result(path, "R6 riverside-rockets v parkview-wanderers",
                           "unplayed"),
            validate.Issue(
                rule="verb-unknown", severity=validate.WARNING,
                message=f"{path}: result verb 'TROUNCES' is not one this program "
                        "recognises, so the computed result was not cross-checked "
                        "against it.",
                subject=str(path), detail="TROUNCES")])

    def test_a_card_of_rubbers_both_sides_forfeited_states_its_one_mistake_once(self):
        # Four rows finished by their boxes and won by nobody: a night not played,
        # whose DEFEATS F6 holds to its level rows. verb-mismatch said the same of
        # the same verb, about a result its match does not have.
        path = self.conceded
        self.assertEqual({m.status for m in self.matches if m.source == str(path)},
                         {MatchStatus.UNPLAYED})
        self.assertEqual(self.about(path), [
            self.f6(path, "verb", "the card prints 'DEFEATS', making home the winner, "
                    "but the set rows give games 0-0 (level) and at most 0-0 sets "
                    "(level)"),
            self.no_result(path, "R6 lakeside-spinners v hilltop-aces", "unplayed")])

    def test_a_card_no_match_was_made_from_is_checked_as_one_that_counts(self):
        # Handed its cards and no matches, validate has nothing saying a card counts
        # for nothing, and that is no evidence that it does: both cards are checked
        # as they are in the build.
        cards = [c for c in self.cards
                 if c.page == 0 and c.source in (self.decided, str(self.ticked))]
        report = validate.validate([], self.competition, cards=cards)
        for path in (self.decided, self.ticked):
            with self.subTest(card=Path(path).name):
                self.assertEqual(self.about(path, report.issues), self.about(path))


class TestF6WarnsACardNothingConfirmsOfTheVerbItPrints(CardsBesideTheSeasonCase):
    """F6's warning names a printed verb nobody can use, and not as a verb unprinted.

    Row 4.133. Measured by /tmp/t7/scratch/V3/probe/probe.py through build.py on
    the tree before this, cards of four rubbers to home with no TOTAL row and no
    Match Winner tick: printing ``VS``, F6 warned that "the card prints no TOTAL
    row, result verb or winner tick", beside ``verb-absent`` saying it prints
    ``'VS'``; printing ``TROUNCES``, the same, beside ``verb-unknown`` naming the
    word. Over rows read 0-0, which count for nothing and so draw no
    ``verb-absent``, F6's was the one finding about the ``VS`` it printed, and said
    there was none. Four cards beside the season: those three, and one printing no
    verb, which is warned in the words it always was.

    Rejected: counting a printed verb nobody can use as available, which would
    silence F6 on a card nothing confirms.
    """

    BARE = dict(omit_totals=True, winner=cw.NO_TICK)
    HOME = ((6, 2), (6, 3), (6, 4), (6, 1))

    @classmethod
    def extra_cards(cls):
        cls.vs = cls.card("vs", "4", "riverside-rockets", "hilltop-aces", cls.HOME,
                          verb="VS", **cls.BARE)
        cls.unknown = cls.card("unknown", "5", "lakeside-spinners",
                               "parkview-wanderers", cls.HOME, verb="TROUNCES",
                               **cls.BARE)
        cls.none = cls.card("none", "6", "riverside-rockets", "parkview-wanderers",
                            cls.HOME, omit_verb=True, **cls.BARE)
        cls.empty = cls.card("empty", "6", "lakeside-spinners", "hilltop-aces",
                             ((0, 0),) * 4, verb="VS", **cls.BARE)

    def warned(self, where, prints):
        return [validate.Issue(
            rule="F6", severity=validate.WARNING,
            message=f"{where}: the card prints no {prints}, so nothing on it confirms "
                    "the set rows were read correctly.",
            subject=str(where), detail="4 set row(s) read")]

    def rules(self, where) -> list[str]:
        return [issue.rule for issue in self.about(where)]

    def f6_of(self, where):
        return [issue for issue in self.about(where) if issue.rule == "F6"]

    def test_a_card_printing_vs_is_warned_it_prints_a_separator(self):
        self.assertEqual(self.f6_of(self.vs), self.warned(
            self.vs, "TOTAL row or winner tick, and it prints 'VS' between the team "
                     "names instead of a result"))
        self.assertIn("verb-absent", self.rules(self.vs))

    def test_a_card_printing_a_word_nobody_reads_is_warned_which_word(self):
        self.assertEqual(self.f6_of(self.unknown), self.warned(
            self.unknown, "TOTAL row or winner tick, and its result verb "
                          "'TROUNCES' is not one this program recognises"))
        self.assertIn("verb-unknown", self.rules(self.unknown))

    def test_a_card_printing_no_verb_is_warned_as_it_always_was(self):
        self.assertEqual(self.f6_of(self.none), self.warned(
            self.none, "TOTAL row, result verb or winner tick"))

    def test_a_card_read_empty_is_told_the_vs_nothing_else_names(self):
        self.assertEqual(self.f6_of(self.empty), self.warned(
            self.empty, "TOTAL row or winner tick, and it prints 'VS' between the "
                        "team names instead of a result"))
        self.assertNotIn("verb-absent", self.rules(self.empty))


class TestAnF6ErrorOnAForfeitSaysTheTickDisagreesWithTheBox(CardsBesideTheSeasonCase):
    """A tick held to the Match Forfeited box is reported as disagreeing with it.

    Row 4.152. Measured by /tmp/t7/scratch/V3/probe/probe.py through build.py on
    the tree before this, a whole-match forfeit with its box ticked against away,
    four 0-0 rows, no verb and the away tick: exit 1 on one F6 error, "the
    winner-tick cross-check disagrees with the set rows", rows which give no
    result to disagree with, over a detail saying what it does disagree with, the
    box. So with ``BOWS TO``, a word nobody reads, beside the tick, and with the
    home side's box and the home tick. Four cards beside the season: those three,
    and a played card whose away tick the rows contradict, reported as it was.

    Rejected: one wording for both, "disagrees with the card", which says less and
    changes every played card's report.
    """

    EMPTY = ((0, 0),) * 4

    @classmethod
    def extra_cards(cls):
        cls.away = cls.card("away", "4", "riverside-rockets", "hilltop-aces",
                            cls.EMPTY, forfeited_by="away", omit_verb=True,
                            winner="away")
        cls.home = cls.card("home", "5", "lakeside-spinners", "parkview-wanderers",
                            cls.EMPTY, forfeited_by="home", omit_verb=True,
                            winner="home")
        cls.bows = cls.card("bows", "6", "riverside-rockets", "parkview-wanderers",
                            cls.EMPTY, forfeited_by="away", verb="BOWS TO",
                            winner="away")
        cls.played = cls.card("played", "6", "lakeside-spinners", "hilltop-aces",
                              ((6, 2), (6, 3), (6, 4), (6, 1)), omit_verb=True,
                              winner="away")

    def f6_of(self, where):
        return [issue for issue in self.about(where) if issue.rule == "F6"]

    def test_a_tick_for_the_side_that_forfeited_disagrees_with_the_box(self):
        self.assertEqual(self.f6_of(self.away), [self.f6(
            self.away, "winner-tick", "the card ticks away as Match Winner, but the "
            "away Match Forfeited box is ticked, giving the match to home, and the "
            "set rows give no result", held_to="the away Match Forfeited box")])

    def test_the_box_named_is_the_one_the_card_ticks(self):
        self.assertEqual(self.f6_of(self.home), [self.f6(
            self.home, "winner-tick", "the card ticks home as Match Winner, but the "
            "home Match Forfeited box is ticked, giving the match to away, and the "
            "set rows give no result", held_to="the home Match Forfeited box")])

    def test_beside_a_word_nobody_reads_the_tick_still_names_the_box(self):
        self.assertEqual([issue.message for issue in self.f6_of(self.bows)], [
            f"{self.bows}: the winner-tick cross-check disagrees with the away Match "
            "Forfeited box, so this card's numbers are not trustworthy."])
        self.assertIn("verb-unknown", [issue.rule for issue in self.about(self.bows)])

    def test_a_played_card_still_disagrees_with_its_set_rows(self):
        self.assertEqual(self.f6_of(self.played), [self.f6(
            self.played, "winner-tick", "the card ticks away as Match Winner, but the "
            "set rows give games 24-10 (home) and at most 4-0 sets (home)")])


class TestAWashoutIsHeldToNothingItPrintsBesideItsRows(CardsBesideTheSeasonCase):
    """A card its own Match Cancelled box calls off is named once, by ``no-result``.

    Six washouts beside the season, none ticking Match Forfeited. One stopped after
    two rubbers, printing ``VS``, no TOTAL row and no tick. One stopped after two,
    printing ``TROUNCES``. One read 0-0 on every row, printing ``DEFEATS`` and
    ticking home. One of four finished rubbers won at home, printing ``LOSES
    TO`` over a TOTAL row of 30-10 games where its rows add to 24-10. And one file
    of two: a card stopped after two, printing ``DEFEATS`` and ticking away, and
    on its second page a card read 0-0, printing ``VS`` and ticking away.

    Measured on the tree before this (/tmp/t6/scratch/m22/fix/probe.py, cases g2, h,
    v and f2): beside ``recorded as cancelled``, the first drew F6's warning that
    nothing confirms its rows and ``totals-missing``; the second ``verb-unknown``;
    the third failed the run on 3 errors, F6 for its verb and for its tick and
    ``verb-no-result``; and the fourth on 4, F6 for its TOTAL row and for its verb,
    ``totals-mismatch`` and ``verb-mismatch``. Every one is about rows no figure
    reads, and the run failed over two nights nobody counts. On a first version of
    this change, which passed by a washout in F6 and in the verb checks but not in
    ``_check_totals``, the first kept ``totals-missing`` and the fourth failed the
    run on ``totals-mismatch`` alone. So F6, the TOTAL row and the verb all read the
    cards :func:`~matchcentre.discover.called_off` does not call off.

    The last four each say both that the match was called off and that a side won
    it: by tick and verb, by verb alone, by tick and verb naming different sides,
    and by tick alone. A night called off has no winner, so each card contradicts
    itself, between two of its own boxes rather than between its boxes and its
    rows, and a ladder that leaves the match out may be missing a result. Measured
    on the tree before this (/tmp/t7/scratch/VW/probes/p233.py, cases v, f2, d and
    l): exit 0, ``recorded as cancelled`` alone, for every one. Now that one
    finding is an error that names what the card claims, and the rows are still
    held to nothing. Rejected: a second finding beside ``no-result``, which is one
    card's mistake reported twice; and holding the winner to the rows, which is F6
    over a night nobody counts.
    """

    @classmethod
    def extra_cards(cls):
        cls.bare = cls.card("bare", "6", "riverside-rockets", "parkview-wanderers",
                            ((6, 2), (6, 3), (0, 0), (0, 0)), cancelled=True,
                            verb="VS", winner=cw.NO_TICK, omit_totals=True)
        cls.unknown = cls.card("unknown", "6", "lakeside-spinners", "hilltop-aces",
                               ((6, 2), (6, 3), (0, 0), (0, 0)), cancelled=True,
                               verb="TROUNCES", winner=cw.NO_TICK)
        cls.winner = cls.card("winner", "5", "riverside-rockets", "lakeside-spinners",
                              ((0, 0),) * 4, cancelled=True, verb="DEFEATS",
                              winner="home")
        cls.reversed = cls.card("reversed", "5", "hilltop-aces",
                                "parkview-wanderers", FINISHED_ROWS, cancelled=True,
                                totals=(4, 30, 10, 0), verb="LOSES TO",
                                winner=cw.NO_TICK)
        split = cls.spec("4", "riverside-rockets", "hilltop-aces",
                         ((6, 2), (6, 3), (0, 0), (0, 0)), cancelled=True,
                         verb="DEFEATS", winner="away")
        ticked = cls.spec("4", "lakeside-spinners", "parkview-wanderers",
                          ((0, 0),) * 4, cancelled=True, verb="VS", winner="away")
        path = cls.data / "Round4" / "both.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cards_in_one_file(cw.write_card(split),
                                           cw.write_card(ticked)))
        cls.split, cls.ticked = str(path), f"{path} page 2"

    def washout(self, path, claims=""):
        """The one finding about a washout, an error naming *claims* if any."""
        match, = [m for m in self.matches if m.source == str(path)]
        self.assertIs(match.status, MatchStatus.CANCELLED)
        found = self.no_result(path, f"R{match.round} {match.home} v {match.away}",
                               "cancelled")
        if not claims:
            return [found]
        return [dataclasses.replace(
            found, severity=validate.ERROR,
            message=found.message.replace(
                " ladder.", f" ladder, but {claims}. A match called off has no "
                            "winner, so the card contradicts itself."))]

    def test_a_washout_with_nothing_to_check_its_rows_by_is_not_warned_of_it(self):
        self.assertEqual(self.about(self.bare), self.washout(self.bare))

    def test_a_washout_printing_a_word_nobody_knows_is_not_warned_of_it(self):
        self.assertEqual(self.about(self.unknown), self.washout(self.unknown))

    def test_a_washout_naming_a_winner_over_blank_rows_is_one_error_naming_both(self):
        self.assertEqual(self.about(self.winner), self.washout(
            self.winner, "the card ticks home as Match Winner and the verb "
                         "'DEFEATS' means home won"))

    def test_a_washout_whose_total_and_verb_contradict_its_rows_is_held_to_neither(self):
        # Its verb names a winner, and that is the error; that the verb and the
        # TOTAL row disagree with the rows is still said by nothing.
        self.assertEqual(self.about(self.reversed), self.washout(
            self.reversed, "the verb 'LOSES TO' means away won"))

    def test_a_tick_and_a_verb_naming_different_winners_are_both_named(self):
        self.assertEqual(self.about(self.split), self.washout(
            self.split, "the card ticks away as Match Winner and the verb "
                        "'DEFEATS' means home won"))

    def test_a_tick_alone_names_a_winner_and_is_found_by_its_page(self):
        self.assertEqual(self.about(self.ticked), self.washout(
            self.ticked, "the card ticks away as Match Winner"))

    def test_the_washouts_naming_a_winner_are_the_run_s_only_errors(self):
        self.assertEqual(
            sorted((i.rule, i.detail) for i in self.report.errors),
            sorted(("no-result", str(path)) for path in (
                self.winner, self.reversed, self.split, self.ticked)))

    def test_the_cards_held_to_nothing_are_the_ones_discover_calls_off(self):
        # Asked, not restated: validate reads the boxes by the same function that
        # made the match. Told nothing is called off, the fourth card's TOTAL row and
        # verb are held to its rows again; its verb is still not held to a result,
        # since its match still counts for nothing.
        self.assertIs(validate.called_off, discover_mod.called_off)
        with mock.patch.object(validate, "called_off", return_value=False):
            report = validate.validate(self.matches, self.competition,
                                       cards=self.cards)
        self.assertEqual(sorted({i.rule for i in self.about(self.reversed,
                                                              report.issues)}),
                         ["F6", "no-result", "totals-mismatch"])


class TestACardWithNoNameIsNamedByTheOneFunctionItsMatchIsNamedBy(
        CardsBesideTheSeasonCase):
    """A card whose source is empty is found by its match and reported as if named.

    No card read from a file has an empty source; one made in memory can. Discover
    named its match ``<unnamed card>`` and validate named the card ``''``, and
    whether a card counts is asked by that name, so the card found no match and
    counted. Two cards beside the season, each blanked in turn and the season found
    and checked again: one read 0-0 on every row, printing ``DEFEATS`` and ticking
    home, as a night not yet played is read, and a washout printing the same.

    Measured on the tree before this (/tmp/t7/scratch/VW/probes/p234.py, cases t
    and washout): the first, named, gets F6 for its verb and for its tick and
    ``no-result``; blanked, the same two F6 under the subject ``''`` with messages
    beginning ``: the verb cross-check``, and ``verb-no-result`` besides, a verb
    held to a result its match does not have. Rejected: keeping validate's copy and
    giving it discover's fallback. The two copies agreed once and drifted apart,
    and a third caller would be a third copy.
    """

    @classmethod
    def extra_cards(cls):
        cls.zeros = cls.card("zeros", "4", "riverside-rockets", "lakeside-spinners",
                             ((0, 0),) * 4, verb="DEFEATS", winner="home")
        cls.washout = cls.card("washout", "4", "hilltop-aces", "parkview-wanderers",
                               ((0, 0),) * 4, cancelled=True, verb="DEFEATS",
                               winner="home")

    def unnamed(self, path):
        """Every finding with the card at *path* made in memory with no name.

        The season is found and checked again, as a build would, and every finding
        is returned with ``<unnamed card>`` where it says *path*.
        """
        cards = [dataclasses.replace(c, source="") if c.source == str(path) else c
                 for c in self.cards]
        found = discover_mod.discover(cards, self.rejected, config=self.config,
                                      root=self.data)
        competition = next(c for c in found.club.competitions
                           if c.id == self.comp_id)
        report = validate.validate(found.matches[self.comp_id], competition,
                                   club=found.club, cards=cards,
                                   rejected=self.rejected)
        return report.issues

    def named(self, path):
        """The same findings with the card named, and ``<unnamed card>`` for it."""
        def unname(text):
            return text.replace(str(path), "<unnamed card>")
        return tuple(dataclasses.replace(i, message=unname(i.message),
                                         subject=unname(i.subject),
                                         detail=unname(i.detail))
                     for i in self.report.issues)

    def test_validate_names_a_card_by_the_function_discover_names_its_match_by(self):
        self.assertIs(validate._where, discover_mod.where)

    def test_a_card_read_empty_is_reported_as_it_is_when_named(self):
        # F6 twice and no-result, under the name its match has; no verb-no-result.
        self.assertEqual(self.unnamed(self.zeros), self.named(self.zeros))

    def test_a_washout_naming_a_winner_is_found_by_its_match_when_unnamed(self):
        # The one finding about it is an error only if the card is found by the
        # name its match has.
        self.assertEqual(self.unnamed(self.washout), self.named(self.washout))
        found, = [i for i in self.named(self.washout)
                  if i.detail == "<unnamed card>"]
        self.assertEqual((found.rule, found.severity), ("no-result", validate.ERROR))


class TestTwoCardsThatSwapWhoForfeitedWhichRubberDisagree(SeasonCase):
    """Two cards for one night, alike in every figure but which rubber each side gave up.

    Duplicate cards are compared on everything a result is made of, and while the
    ``Forfeit`` ticks decided nothing they did not need to be part of it. Now the
    tick decides who won the rubber, and the comparison had not been told. Measured
    on the pair below with the ticks crediting the rubbers and not compared: "2 cards
    claim round 4 riverside-rockets v hilltop-aces and say the same thing about it,
    so it has been counted once and every figure on the page is right" -- a warning,
    exit 0 -- while one card gave the first singles to the rockets and the second to
    the aces, the other card the reverse, and the rockets' first-singles record read
    4-0 off whichever card was read first where the other gives 3-1.

    Everybody is named on both cards, as on a card where the forfeit was a withdrawal
    after the team sheet went in. With the forfeiting side left blank the pair was
    already a naming dispute, and that finding claimed the two agreed about the
    result as well.

    Rejected: comparing each rubber's winner rather than its ticks. The ticks sit in
    the comparison beside the row's status, which is compared as printed rather than
    as what it implies, and for the same reason: two cards that record different
    things about a rubber are two different cards, whether or not the set rule
    happens to give both the same winner.
    """

    ROUND = SHORT_ROUND
    HOME, AWAY = "riverside-rockets", "hilltop-aces"

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        _, home_players = TEAMS[cls.HOME]
        _, away_players = TEAMS[cls.AWAY]

        def rows(first, second):
            pair_home, pair_away = home_players[:2], away_players[:2]
            return (cw.SetRow(6, 2, True, pair_home, pair_away),
                    cw.SetRow(0, 0, False, home_players[:1], away_players[:1],
                              status=SetStatus.FORFEIT, status_side=first),
                    cw.SetRow(0, 0, False, home_players[1:2], away_players[1:2],
                              status=SetStatus.FORFEIT, status_side=second),
                    cw.SetRow(2, 6, True, pair_home, pair_away))

        for name, court, ticks in (("a.pdf", "10", ("away", "home")),
                                   ("b.pdf", "11", ("home", "away"))):
            cw.write_card_file(folder / name, short_spec(
                cls.HOME, cls.AWAY, court=court, match_id="4000010",
                rows=rows(*ticks), totals=(2, 8, 8, 2), verb="DRAWS WITH",
                winner=cw.NO_TICK))

    def pair(self):
        return sorted((m for m in self.matches if m.round == self.ROUND),
                      key=lambda m: Path(m.source).name)

    def test_the_two_cards_agree_on_every_total_and_not_on_the_singles(self):
        # The pre-flight. Without it the two below could pass on a pair that also
        # differed in a figure the comparison always read.
        a, b = self.pair()
        for figure in ("status", "winner", "home_games", "away_games",
                       "home_sets", "away_sets"):
            with self.subTest(figure=figure):
                self.assertEqual(getattr(a, figure), getattr(b, figure))
        self.assertEqual([s.winner for s in a.sets],
                         ["home", "home", "away", "away"])
        self.assertEqual([s.winner for s in b.sets],
                         ["home", "away", "home", "away"])

    def test_the_pair_is_an_error_and_not_a_harmless_repeat(self):
        issue, = [i for i in self.report.issues if i.rule == "F9"]
        self.assertEqual(issue.severity, validate.ERROR)
        self.assertNotIn("every figure on the page is right", issue.message)
        self.assertIn("neither is counted", issue.message)

    def test_neither_card_is_in_any_figure(self):
        for row in [m for m in self.season["matches"] if m["round"] == self.ROUND]:
            with self.subTest(source=row["source"]):
                self.assertEqual(row["not_counted"], "conflict")
        rockets, = [t for t in self.season["teams"] if t["slug"] == self.HOME]
        self.assertEqual(rockets["played"], 3)
        self.assertAlmostEqual(rockets["points"], EXPECTED_POINTS[self.HOME],
                               places=6)

    def test_a_rubber_ticked_against_both_sides_is_not_one_ticked_against_one(self):
        # The rejected alternative, on the one row where it answers differently: the
        # first singles at 6-0, ticked against the aces on one card and against both
        # sides on the other. The games give it to the rockets either way, so the
        # winners agree and so does every figure the page prints. The two cards
        # still record different things about who turned up, which is what the
        # match's own `forfeited_by` is compared for one level up. And the mirror,
        # 0-6 ticked against the rockets alone or against both, because each tick
        # is compared on its own: with the aces' tick left out, the first pair
        # still differs on the rockets' tick and only this one is a repeat.
        a, _ = self.pair()
        cases = (((6, 0), "away", "home", (2, 2, 14, 8)),
                 ((0, 6), "home", "away", (1, 3, 8, 14)))
        for (home, away), alone, winner, figures in cases:
            with self.subTest(ticked_against=alone):
                rubber = dataclasses.replace(a.sets[1], home_games=home,
                                             away_games=away)
                rows = (dataclasses.replace(rubber, home_forfeit=alone == "home",
                                            away_forfeit=alone == "away"),
                        dataclasses.replace(rubber, home_forfeit=True,
                                            away_forfeit=True))
                cards = [dataclasses.replace(a, sets=a.sets[:1] + (row,)
                                             + a.sets[2:]) for row in rows]
                self.assertEqual([card.sets[1].winner for card in cards],
                                 [winner, winner])
                self.assertEqual({(card.home_sets, card.away_sets, card.home_games,
                                   card.away_games) for card in cards}, {figures})
                self.assertFalse(stats.same_scoreline(*cards))


class TestARubberTickedAgainstBothSidesGoesByItsGamesEverywhere(SeasonCase):
    """Both ``Forfeit`` boxes ticked on a rubber that was played, on two cards.

    Both boxes ticked names nobody as the forfeiter, so the shared rule falls back
    on the games printed on the row, and four places ask it: the model, the
    validator reading the row straight off the page, the reader's own check of the
    TOTAL row, and the writer that derives the TOTAL row every fixture here is
    printed with. The foundation test asked the model about a row built by hand.
    Nothing asked the other three about a card: measured by the review, the games
    passed the other way round at any one of those three calls left the whole suite
    passing, so the writer could print a both-ticked 6-0 as an away set, the reader
    check it that way and the validator report it that way.

    Two cards, so that the answer is not the same side twice: the rockets' first
    singles at 6-0 and the spinners' second singles at 1-6, both ticked against both
    sides, every TOTAL row derived. Each night is two sets each and decided on
    games, so the forfeited rubber is the set that makes it two-all rather than
    three-one, and a call with the games turned round moves the TOTAL row, the
    reader's check or the validator's tally off the others by one set. Measured on
    the tree 4.14 left and on this one: the same answer, sets 2-2, games 17-14 and
    16-17, and nothing reported. These cards pin it rather than change it.

    Rejected: a both-ticked row that is level. It is the case the rule credits to
    nobody, so turning its games round changes nothing, and a test of it could not
    tell the four places apart.
    """

    ROUND = SHORT_ROUND

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        _, rockets = TEAMS["riverside-rockets"]
        _, aces = TEAMS["hilltop-aces"]
        _, spinners = TEAMS["lakeside-spinners"]
        _, wanderers = TEAMS["parkview-wanderers"]
        both = {"status": SetStatus.FORFEIT, "status_side": "both"}
        cw.write_card_file(folder / "both-home.pdf", short_spec(
            "riverside-rockets", "hilltop-aces", court="10", match_id="4000010",
            rows=(cw.SetRow(6, 2, True, rockets[:2], aces[:2]),
                  cw.SetRow(6, 0, False, rockets[:1], aces[:1], **both),
                  cw.SetRow(3, 6, False, rockets[1:2], aces[1:2]),
                  cw.SetRow(2, 6, True, rockets[:2], aces[:2]))))
        cw.write_card_file(folder / "both-away.pdf", short_spec(
            "lakeside-spinners", "parkview-wanderers", court="11",
            match_id="4000011",
            rows=(cw.SetRow(6, 3, True, spinners[:2], wanderers[:2]),
                  cw.SetRow(3, 6, False, spinners[:1], wanderers[:1]),
                  cw.SetRow(1, 6, False, spinners[1:2], wanderers[1:2], **both),
                  cw.SetRow(6, 2, True, spinners[:2], wanderers[:2]))))

    # name -> (the slot ticked against both sides, the side its games favour)
    FORFEITED = {"both-home.pdf": ("s1", "home"), "both-away.pdf": ("s2", "away")}

    def card(self, name):
        found, = [c for c in self.cards if Path(c.source).name == name]
        return found

    def match(self, name):
        found, = [m for m in self.matches if Path(m.source).name == name]
        return found

    def test_the_rubber_goes_to_the_side_its_games_favour(self):
        # The model's half, and the premise of the three below: each night two-all
        # on sets and won by the side ahead on games.
        for name, (key, side) in self.FORFEITED.items():
            with self.subTest(card=name):
                match = self.match(name)
                rubber, = [s for s in match.sets if s.slot.key == key]
                self.assertEqual((rubber.status, rubber.home_forfeit,
                                  rubber.away_forfeit),
                                 (SetStatus.FORFEIT, True, True))
                self.assertEqual(rubber.winner, side)
                self.assertEqual((match.home_sets, match.away_sets), (2, 2))
                self.assertEqual((match.winner, match.decided_by), (side, "games"))

    def test_the_writer_totals_it_as_the_side_its_games_favour(self):
        # The TOTAL rows were derived, so what the reader read back is what the
        # writer counted: two sets each, not three-one either way.
        for name in self.FORFEITED:
            with self.subTest(card=name):
                totals = self.card(name).totals
                self.assertEqual((totals.home_sets, totals.away_sets), (2, 2))

    def test_the_readers_own_check_of_the_total_row_agrees(self):
        for name in self.FORFEITED:
            with self.subTest(card=name):
                check, = [c for c in self.card(name).checks if c.name == "totals"]
                self.assertTrue(check.available)
                self.assertTrue(check.agrees, check.detail)

    def test_the_validator_finds_nothing_to_report_about_either_card(self):
        for name in self.FORFEITED:
            with self.subTest(card=name):
                match = self.match(name)
                label = f"R{match.round} {match.home} v {match.away}"
                self.assertEqual([(i.severity, i.rule) for i in self.report.issues
                                  if i.subject in (match.source, label)
                                  or i.subject.startswith(f"{label} ")], [])


class TestARowThatForfeitsTheSideItShowsWinningIsReported(SeasonCase):
    """A ``Forfeit`` box ticked against the side the row's own games say won.

    The rockets' first singles reads 6-0 with the rockets' box ticked. The tick
    decides, so the set is the aces', and a TOTAL row counted that way agrees with
    the set rows, the verb and the winner tick. Measured on this card: sets 1-3,
    and nothing in the report about it at all. Before this item every rubber went by
    its games, so the same card was a ``totals-mismatch`` error; crediting the tick
    took the only sign away. The foundation test of the rule said the printed TOTAL
    row reports this, which holds only for a TOTAL row counted off the games.

    One half of the row is wrong and nothing on the card says which. If the box is
    on the wrong side, a set the rockets won is on the aces' side of the ladder; if
    the scoreline was written the wrong way round, the rockets are paid six games
    they did not win. So the row is reported, once, by name.

    A warning and not an error, because the card can mean it: a rubber awarded
    against the side that won it on court, for an ineligible player say, is
    recorded exactly like this, and the numbers then stand. Rejected, an error:
    it would stop the run on a card that is right. And it is asked of the set as
    scored rather than of the ticks: the row is reported when the games are a
    finished set and the rubber is credited to the other side, which is only ever
    the side that did not tick. Measured on the second card, which forfeits four
    ways that do not contradict: a side that ticked while ahead in a set nobody
    finished (4-2, the spinners' pair), a 6-0 ticked against the side it shows
    losing, a 6-0 ticked against both, and a 0-0 nobody came for. None is reported,
    and a check that compared the games instead of the finished set reported the
    first.
    """

    ROUND = SHORT_ROUND

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        _, rockets = TEAMS["riverside-rockets"]
        _, aces = TEAMS["hilltop-aces"]
        _, spinners = TEAMS["lakeside-spinners"]
        _, wanderers = TEAMS["parkview-wanderers"]
        forfeit = SetStatus.FORFEIT
        cls.contradicts = cw.write_card_file(folder / "contradicts.pdf", short_spec(
            "riverside-rockets", "hilltop-aces", court="10", match_id="4000010",
            rows=(cw.SetRow(6, 2, True, rockets[:2], aces[:2]),
                  cw.SetRow(6, 0, False, rockets[:1], aces[:1], status=forfeit,
                            status_side="home"),
                  cw.SetRow(3, 6, False, rockets[1:2], aces[1:2]),
                  cw.SetRow(2, 6, True, rockets[:2], aces[:2]))))
        cls.consistent = cw.write_card_file(folder / "consistent.pdf", short_spec(
            "lakeside-spinners", "parkview-wanderers", court="11",
            match_id="4000011",
            rows=(cw.SetRow(4, 2, True, spinners[:2], wanderers[:2],
                            status=forfeit, status_side="home"),
                  cw.SetRow(6, 0, False, spinners[:1], (), status=forfeit,
                            status_side="away"),
                  cw.SetRow(6, 0, False, spinners[1:2], wanderers[1:2],
                            status=forfeit, status_side="both"),
                  cw.SetRow(0, 0, True, spinners[:2], (), status=forfeit,
                            status_side="away"))))

    def match(self, path):
        found, = [m for m in self.matches if m.source == str(path)]
        return found

    def issues_about(self, match, key=None):
        label = f"R{match.round} {match.home} v {match.away}"
        return [i for i in self.report.issues
                if i.subject in (match.source, label)
                or i.subject.startswith(f"{label} ")
                if key is None or i.subject == f"{label} {key}"]

    def test_the_tick_decides_and_every_total_on_the_card_agrees(self):
        # The premise: the tick, not the 6-0, gives the aces the set, and the card
        # the writer printed says so in its TOTAL row, so nothing else reports it.
        match = self.match(self.contradicts)
        rubber = match.sets[1]
        self.assertEqual((rubber.home_games, rubber.away_games, rubber.home_forfeit,
                          rubber.away_forfeit), (6, 0, True, False))
        self.assertEqual(rubber.winner, "away")
        self.assertEqual((match.home_sets, match.away_sets), (1, 3))
        card, = [c for c in self.cards if c.source == str(self.contradicts)]
        self.assertTrue(card.confirmed)
        self.assertEqual(card.cross_checks_failed, ())

    def test_the_row_is_reported_once_by_name(self):
        match = self.match(self.contradicts)
        self.assertEqual([(i.severity, i.rule, i.subject)
                          for i in self.issues_about(match)],
                         [(validate.WARNING, "set-forfeit-contradicted",
                           "R4 riverside-rockets v hilltop-aces s1")])

    def test_it_says_what_the_row_prints_and_who_was_credited(self):
        issue, = self.issues_about(self.match(self.contradicts))
        self.assertIn("6-0", issue.message)
        self.assertIn("ticks Forfeit against riverside-rockets", issue.message)
        self.assertIn("credited to hilltop-aces", issue.message)
        self.assertEqual(issue.detail, str(self.contradicts))

    def test_it_stops_nothing(self):
        # Every finding in the season is a warning, so build.py exits 0.
        self.assertTrue(self.report.ok, [i.rule for i in self.report.errors])

    def test_a_forfeit_the_games_agree_with_is_not_reported(self):
        # 6-0 ticked against the side it shows losing, 6-0 ticked against both,
        # 0-0 nobody came for.
        match = self.match(self.consistent)
        self.assertEqual([s.winner for s in match.sets[1:]], ["home"] * 3)
        for key in ("s1", "s2", "d2"):
            with self.subTest(slot=key):
                self.assertEqual(self.issues_about(match, key), [])

    def test_a_side_that_forfeited_while_ahead_is_not_reported(self):
        # 4-2 is not a finished set under first-to-6, so nobody won it on court and
        # the tick is the only thing that says who the rubber goes to.
        match = self.match(self.consistent)
        rubber = match.sets[0]
        self.assertEqual((rubber.home_games, rubber.away_games, rubber.winner),
                         (4, 2, "away"))
        self.assertEqual(self.issues_about(match), [])


class TestAWholeMatchForfeitIsReportedOnlyWhereItContradictsItself(unittest.TestCase):
    """A whole-match forfeit is reported for what contradicts it, once.

    Each night below is one card, the rockets against the aces, with the Match
    Forfeited box ticked against the aces unless it says otherwise. Measured by
    /tmp/t4/scratch/d433/matrix.py and /tmp/t4/scratch/d433/fix/probe.py on the
    tree before this:

    * The filed card, every row 0-0 with nothing ticked on it, gave two F6 errors,
      from the verb and winner-tick cross-checks. It also gave four
      ``set-no-games`` warnings, each saying "a rubber that was never played reads
      like this when the card does not say so". This card does say so, with its
      box.
    * The same card with the aces' names left blank, as a card for a side that
      never came would be, gave those and a ``set-players-missing`` warning
      naming every row.
    * A forfeit given up after one rubber, 6-2 and then three empty rows, gave
      three ``set-no-games`` warnings, and one given up at a rubber each gave two.
    * Rows giving every set to the aces gave the one contradiction twice.
    * A verb for the aces on empty rows gave it three times: two F6 errors and a
      ``verb-mismatch``; with a tick for the aces as well, three times again.
    * A tick for the aces on empty rows gave two F6 errors.
    * The filed card printing no TOTAL row gave the two F6 errors and the four
      warnings beside its ``totals-missing``.

    Now each of those is one error or none, and the card without its TOTAL row is
    told only that. Its verb and tick restate the box, so they are two checks the
    card prints and agree, not two it lacks: marked as not printed, they had the
    card warned that it "prints no TOTAL row, result verb or winner tick". So is a
    card printing only a verb for the aces, which ``verb-mismatch`` reports: it
    had that warning too while its verb was marked not printed. ``set-no-games``
    and ``set-players-missing`` ask whether a row is a whole-match forfeit's empty
    one through the status ``discover._match_status`` gives the match, which is the
    reader's one reading of the box, and not by testing the box again. A played
    card, or a disputed one, with a row left empty is still told about it, and a
    forfeit is still held to the names on a rubber it played.

    Rejected: keeping the warnings for a forfeit given up part way through. Its
    empty rows are the rubbers nobody stayed to play, and the box says so as
    plainly as it does for a night nobody came to.
    """

    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    EMPTY = ((0, 0),) * 4
    TO_ACES = ((2, 6), (3, 6), (4, 6), (1, 6))
    NIGHTS = {
        "filed": dict(scores=EMPTY),
        "aces named nowhere": dict(scores=EMPTY, nameless=(0, 1, 2, 3)),
        "given up": dict(scores=((6, 2), (0, 0), (0, 0), (0, 0))),
        "given up, its rubber unnamed": dict(scores=((6, 2), (0, 0), (0, 0), (0, 0)),
                                             nameless=(0, 1, 2, 3)),
        "given up at a rubber each": dict(scores=((6, 4), (4, 6), (0, 0), (0, 0))),
        "rows to the aces": dict(scores=TO_ACES),
        "rows and verb to the aces": dict(scores=TO_ACES, verb="LOSES TO"),
        "rows forfeited by the rockets": dict(scores=EMPTY, rows_forfeited_by="home"),
        "verb for the aces": dict(scores=EMPTY, verb="LOSES TO"),
        "verb and tick for the aces": dict(scores=EMPTY, verb="LOSES TO",
                                           winner="away"),
        "only a verb, for the aces": dict(scores=EMPTY, verb="LOSES TO",
                                          winner=cw.NO_TICK, omit_totals=True),
        "tick for the aces": dict(scores=EMPTY, winner="away"),
        "box unticked": dict(scores=((6, 2), (6, 3), (6, 4), (0, 0)),
                             forfeited_by=None),
        "box unticked, empty row unnamed": dict(scores=((6, 2), (6, 3), (6, 4), (0, 0)),
                                                forfeited_by=None, nameless=(3,)),
        "disputed, a row empty": dict(scores=((6, 2), (6, 3), (6, 4), (0, 0)),
                                      forfeited_by=None, disputed=True),
        "no TOTAL row": dict(scores=EMPTY, omit_totals=True),
    }

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _, rockets = TEAMS[cls.HOME]
        _, aces = TEAMS[cls.AWAY]
        cls.found, cls.read, cls.validated = {}, {}, {}
        for index, (name, night) in enumerate(cls.NIGHTS.items()):
            fields = dict(night)
            nameless = fields.pop("nameless", ())
            forfeited_rows = fields.pop("rows_forfeited_by", None)
            built = []
            for at, row in enumerate(rows_for(fields.pop("scores"), rockets, aces)):
                if at in nameless:
                    row = dataclasses.replace(row, away_players=())
                if forfeited_rows:
                    row = dataclasses.replace(row, status=SetStatus.FORFEIT,
                                              status_side=forfeited_rows)
                built.append(row)
            fields.setdefault("forfeited_by", "away")
            data = root / f"night-{index}" / "data"
            folder = data / f"Round{SHORT_ROUND}"
            folder.mkdir(parents=True)
            cw.write_card_file(folder / "card.pdf", short_spec(
                cls.HOME, cls.AWAY, court="10", match_id="4000010",
                rows=tuple(built), **fields))
            cards, rejected = scorecard.read_tree(data)
            found = discover_mod.discover(
                cards, rejected, config=config_mod.Config(path=root / "config.toml"),
                root=data)
            comp_id, = sorted(found.matches)
            competition = next(c for c in found.club.competitions if c.id == comp_id)
            report = validate.validate(found.matches[comp_id], competition,
                                       club=found.club, cards=cards,
                                       rejected=rejected)
            match, = found.matches[comp_id]
            # F8 is the team list being read from one card, which is every night.
            cls.found[name] = (match, [i for i in report.issues if i.rule != "F8"])
            cls.read[name], = cards
            cls.validated[name] = dict(matches=found.matches[comp_id],
                                       comp=competition, club=found.club,
                                       cards=cards, rejected=rejected)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def said(self, name: str) -> list[tuple[str, str]]:
        _, issues = self.found[name]
        return [(i.rule, i.message.split(" cross-check ")[0].rsplit(" ", 1)[-1]
                 if i.rule == "F6" else i.severity) for i in issues]

    def test_the_validator_asks_the_model_who_the_forfeit_went_to(self):
        # A spy rather than a card: validate's copy of who a forfeit goes to agreed
        # with the model's on every card, so no card could tell the two apart.
        with mock.patch.object(validate, "forfeit_match_winner",
                               wraps=forfeit_match_winner) as rule:
            given = self.validated["verb for the aces"]
            validate.validate(given["matches"], given["comp"], club=given["club"],
                              cards=given["cards"], rejected=given["rejected"])
        self.assertIn(mock.call("away"), rule.call_args_list)

    def test_the_filed_card_is_reported_as_nothing_at_all(self):
        match, _ = self.found["filed"]
        self.assertEqual((match.status, match.winner),
                         (MatchStatus.FORFEIT, "home"))
        self.assertEqual(self.said("filed"), [])

    def test_a_forfeit_naming_nobody_for_the_side_that_never_came_is_not_told_so(self):
        match, _ = self.found["aces named nowhere"]
        self.assertEqual([s.away_players for s in match.sets], [()] * 4)
        self.assertEqual(self.said("aces named nowhere"), [])

    def test_a_forfeit_given_up_part_way_is_not_told_its_rows_were_unplayed(self):
        self.assertEqual(self.said("given up"), [])
        self.assertEqual(self.said("given up at a rubber each"), [])

    def test_a_forfeit_given_up_part_way_is_held_to_the_names_it_played(self):
        _, issues = self.found["given up, its rubber unnamed"]
        self.assertEqual(self.said("given up, its rubber unnamed"),
                         [("set-players-missing", validate.WARNING)])
        self.assertIn("no away player was read for d1, so", issues[0].message)

    def test_rows_giving_the_match_to_the_forfeiter_are_one_error(self):
        self.assertEqual(self.said("rows to the aces"),
                         [("forfeit-contradicted", validate.ERROR)])

    def test_a_verb_agreeing_with_such_rows_and_the_tick_beside_it_are_that_error(self):
        # The verb agrees with the rows, and the tick restates the box: one
        # contradiction, the rows and the verb against the box (4.134).
        self.assertEqual(self.said("rows and verb to the aces"),
                         [("forfeit-contradicted", validate.ERROR)])

    def test_rows_forfeited_against_the_side_that_came_are_one_error(self):
        self.assertEqual(self.said("rows forfeited by the rockets"),
                         [("forfeit-contradicted", validate.ERROR)])

    def test_a_verb_for_the_forfeiter_is_one_error_against_the_box(self):
        self.assertEqual(self.said("verb for the aces"),
                         [("verb-mismatch", validate.ERROR)])

    def test_a_tick_restating_a_verb_for_the_forfeiter_is_the_same_one_error(self):
        self.assertEqual(self.said("verb and tick for the aces"),
                         [("verb-mismatch", validate.ERROR)])

    def test_a_card_printing_only_a_verb_for_the_forfeiter_is_not_called_thin(self):
        self.assertEqual(self.said("only a verb, for the aces"),
                         [("verb-mismatch", validate.ERROR),
                          ("totals-missing", validate.WARNING)])

    def test_a_tick_for_the_forfeiter_is_one_error_against_the_box(self):
        _, issues = self.found["tick for the aces"]
        self.assertEqual(self.said("tick for the aces"), [("F6", "winner-tick")])
        self.assertIn("Match Forfeited box is ticked, giving the match to home",
                      issues[0].detail)

    def test_a_forfeit_printing_no_total_row_is_told_only_that(self):
        self.assertEqual(self.said("no TOTAL row"),
                         [("totals-missing", validate.WARNING)])
        card = self.read["no TOTAL row"]
        self.assertEqual([c.name for c in card.cross_checks_available],
                         ["verb", "winner-tick"])
        self.assertFalse([w for w in card.warnings if "confirms" in w], card.warnings)

    def test_a_played_card_with_a_row_left_empty_is_still_told_so(self):
        match, issues = self.found["box unticked"]
        self.assertIs(match.status, MatchStatus.PLAYED)
        self.assertEqual(self.said("box unticked"),
                         [("set-no-games", validate.WARNING)])
        self.assertTrue(issues[0].subject.endswith(" d2"), issues[0].subject)
        self.assertEqual(self.said("box unticked, empty row unnamed"),
                         [("set-no-games", validate.WARNING),
                          ("set-players-missing", validate.WARNING)])

    def test_a_disputed_card_with_a_row_left_empty_is_still_told_so(self):
        match, _ = self.found["disputed, a row empty"]
        self.assertIs(match.status, MatchStatus.DISPUTED)
        self.assertIn(("set-no-games", validate.WARNING),
                      self.said("disputed, a row empty"))


class TestAWholeMatchForfeitAsksNoRosterOfTheSideThatNeverCame(unittest.TestCase):
    """A side that forfeited the whole match, and played no row of it, owes no roster.

    The card of a night the aces never came to ticks Match Forfeited against them,
    leaves every row 0-0, and has nobody's names to print in the aces' roster
    block. Each such card was told ``roster-missing``: measured through
    ``build.py --check`` on one rockets v aces card each
    (/tmp/t7/scratch/SQ/p138/probe.py), the filed card gave 2 warnings, F8 and
    ``no away roster was read from the card, so fill-ins on that side cannot be
    detected and nobody who played away has a rating for this round.``, which is
    advice about a fill-in check and a rating nobody could have, on a card whose
    box says nobody played. The same card ticking every rubber's Forfeit box as
    well gave the same two, one against the rockets gave the same for the home
    side, and one reading neither roster said ``neither roster was read`` though
    the rockets came.

    Now a side the box forfeits the match against is asked for no roster where
    :func:`validate._nobody_played` holds for every row, the rule that already
    passes such a row by for ``set-no-games`` and ``set-players-missing``. The side
    that came still owes one, as does a forfeit given up after a rubber, since
    somebody played that rubber, and a played card.

    Rejected: keeping the warning, which says a false thing (``nobody who played
    away``) about a card whose box says nobody did, and asks a club for a roster of
    a side that sent nobody to be on one.
    """

    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    EMPTY = ((0, 0),) * 4
    EVERY_ROW = (0, 1, 2, 3)
    NIGHTS = {
        "filed": dict(scores=EMPTY, away_blank=EVERY_ROW, away_roster=()),
        "aces named on its rows": dict(scores=EMPTY, away_roster=()),
        "every rubber ticked too": dict(scores=EMPTY, away_blank=EVERY_ROW,
                                        away_roster=(), rows_forfeited_by="away"),
        "forfeited by the rockets": dict(scores=EMPTY, forfeited_by="home",
                                         home_blank=EVERY_ROW, home_roster=()),
        "the rockets' roster unread": dict(scores=EMPTY, away_blank=EVERY_ROW,
                                           home_roster=()),
        "neither roster read": dict(scores=EMPTY, away_blank=EVERY_ROW,
                                    home_roster=(), away_roster=()),
        "given up after a rubber": dict(scores=((6, 2), (0, 0), (0, 0), (0, 0)),
                                        away_blank=(1, 2, 3), away_roster=()),
        "played": dict(scores=((6, 2), (6, 3), (4, 6), (6, 1)), forfeited_by=None,
                       away_roster=()),
    }

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _, rockets = TEAMS[cls.HOME]
        _, aces = TEAMS[cls.AWAY]
        cls.found, cls.validated = {}, {}
        for index, (name, night) in enumerate(cls.NIGHTS.items()):
            fields = dict(night)
            blank = {"home_players": fields.pop("home_blank", ()),
                     "away_players": fields.pop("away_blank", ())}
            forfeited_rows = fields.pop("rows_forfeited_by", None)
            rosters = {side: fields.pop(side) for side in ("home_roster",
                                                           "away_roster")
                       if side in fields}
            built = []
            for at, row in enumerate(rows_for(fields.pop("scores"), rockets, aces)):
                row = dataclasses.replace(row, **{names: () for names, rows
                                                  in blank.items() if at in rows})
                if forfeited_rows:
                    row = dataclasses.replace(row, status=SetStatus.FORFEIT,
                                              status_side=forfeited_rows)
                built.append(row)
            fields.setdefault("forfeited_by", "away")
            data = root / f"night-{index}" / "data"
            folder = data / f"Round{SHORT_ROUND}"
            folder.mkdir(parents=True)
            cw.write_card_file(folder / "card.pdf", dataclasses.replace(short_spec(
                cls.HOME, cls.AWAY, court="10", match_id="4000010",
                rows=tuple(built), **fields), **rosters))
            cards, rejected = scorecard.read_tree(data)
            found = discover_mod.discover(
                cards, rejected, config=config_mod.Config(path=root / "config.toml"),
                root=data)
            comp_id, = sorted(found.matches)
            competition = next(c for c in found.club.competitions if c.id == comp_id)
            cls.validated[name] = dict(matches=found.matches[comp_id],
                                       comp=competition, club=found.club,
                                       cards=cards, rejected=rejected)
            cls.found[name] = cls.issues(name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def issues(cls, name: str) -> list:
        given = cls.validated[name]
        report = validate.validate(given["matches"], given["comp"], club=given["club"],
                                   cards=given["cards"], rejected=given["rejected"])
        # F8 is the team list being read from one card, which is every night.
        return [i for i in report.issues if i.rule != "F8"]

    def rules(self, name: str) -> list[str]:
        return [i.rule for i in self.found[name]]

    def missing(self, name: str) -> list[str]:
        return [i.message for i in self.found[name] if i.rule == "roster-missing"]

    def test_the_filed_card_is_asked_for_no_roster_of_the_side_that_never_came(self):
        match, = self.validated["filed"]["matches"]
        self.assertEqual((match.status, match.away_roster),
                         (MatchStatus.FORFEIT, ()))
        self.assertEqual(self.rules("filed"), [])

    def test_nor_is_one_whose_rows_name_that_side_or_forfeit_each_rubber_too(self):
        self.assertEqual(self.missing("aces named on its rows"), [])
        self.assertEqual(self.rules("every rubber ticked too"), [])

    def test_a_home_side_that_never_came_is_asked_for_none_either(self):
        self.assertEqual(self.rules("forfeited by the rockets"), [])

    def test_the_side_that_came_still_owes_its_roster(self):
        said = ("R4 riverside-rockets v hilltop-aces: no home roster was read from "
                "the card, so fill-ins on that side cannot be detected and nobody "
                "who played home has a rating for this round.")
        self.assertEqual(self.missing("the rockets' roster unread"), [said])
        self.assertEqual(self.missing("neither roster read"), [said])

    def test_a_forfeit_given_up_after_a_rubber_still_owes_the_roster(self):
        said = ("R4 riverside-rockets v hilltop-aces: no away roster was read from "
                "the card, so fill-ins on that side cannot be detected and nobody "
                "who played away has a rating for this round.")
        self.assertEqual(self.missing("given up after a rubber"), [said])
        self.assertEqual(self.missing("played"), [said])

    def test_the_rule_is_the_one_that_passes_a_forfeits_empty_rows_by(self):
        # Told no row of it went unplayed, the filed card owes the aces' roster
        # again, so the check asks the rule set-no-games asks and not a copy of it.
        with mock.patch.object(validate, "_nobody_played", return_value=False):
            said = [i.rule for i in self.issues("filed")]
        self.assertIn("roster-missing", said)


class TestEveryHandOffIsOneReportByTheCheckNamedOrByForfeitContradicted(
        unittest.TestCase):
    """Each hand-off is one report, by the check it names or by forfeit-contradicted.

    Row 4.33 let a check stand down for another, and the verb check stands down
    for ``verb-mismatch`` whenever it reads a forfeit's verb as naming the side
    that forfeited. The reader read a verb by the marks inside it and validate by
    the whole verb, so the hand-off was made to depend on the two agreeing, and
    a tick went with whichever of them reported its verb. Row 4.137 made the two
    one table, ``card.verb_side``, which both ask. Measured by
    /tmp/t7/scratch/V1/probe/probe3520.py and count.py on the tree before it,
    over 3,520 cards: counting the results a forfeit with rows that give none
    states against the box by that table, 96 were reported more times, 8 for
    each of twelve words the marks read otherwise than the table, such as
    ``BOWS TO``, ``BEAT`` and ``DEF.BY``; counting by either reading, 72 were
    reported fewer, 24 for each of ``DREW``, ``LEVEL`` and ``HALVED``. Now each
    result such a card states against the box is reported exactly once.

    Each card below is the rockets against the aces, one per verb, box, tick and
    rows. The verbs are the table's, as printed and with stops for spaces, which
    its key reads as the same verb, and each word the reader's marks were written
    for, printed as a verb of its own. The rows are left blank, level at a
    rubber each, or given to the side that forfeited, and the tick is for the
    side that turned up, for the side that forfeited, or not there.

    Rejected: keeping two readings with a bridge between them, which is two
    rules for one question; dropping ``reported_by``, which makes 4.33's one
    report two again; and a copy of the table in the reader, which is how the
    two came to disagree.

    Row 4.134 then had F6 pass by what ``forfeit-contradicted`` names, on rows
    the club's rules read as the forfeiter's. A tick the reader hands to the
    verb cross-check is reported, where the club's rules read the rows so, by
    that finding with the verb, and by F6 on the verb only where they do not, as
    under a set rule leaving the rows unfinished; under the default config the
    finding takes 18 of these cards' ticks. So this holds not that the check a
    hand-off names reports on the card, but that one report is made, by it or
    by the finding that takes it on, and ``tools/doctor.py`` says which under
    each set rule. Rejected: keeping the old invariant by having the reader name
    the check that reports, which needs the club's rules, which the reader is
    not given; and a sweep under a set rule finishing none of these rows, which
    leaves the 18 untested.
    """

    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    BUILD = ROOT / "build.py"
    VERBS = (
        # the table's whole verbs, as a card prints them, then with a stop for the
        # space, which its key reads as the same verb
        "DEFEATS", "DEF", "DEFEATED", "WINS", "WON", "BEATS",
        "DEF BY", "DEF. BY", "DEFEATED BY", "LOSES TO", "LOST TO", "DEFEATS BY",
        "DRAWS WITH", "DRAW", "DRAWS", "DRAWN", "TIES WITH", "TIE", "TIED",
        "DEF.BY", "DEFEATED.BY", "LOSES.TO", "LOST.TO", "DEFEATS.BY",
        "DRAWS.WITH", "TIES.WITH",
        # each word the reader's marks were written for, printed as a verb of its
        # own: the table holds the first four, and the rest are words to learn
        "DREW", "LEVEL", "HALVED", "BEAT", "WIN", "D.", "BOWS TO", "FELL BY",
        # no reading's, and the separator
        "TROUNCES", "VS")
    ROWS = {
        "left blank": {"away": ((0, 0),) * 4, "home": ((0, 0),) * 4},
        "a rubber each": {"away": ((6, 4), (4, 6), (0, 0), (0, 0)),
                          "home": ((6, 4), (4, 6), (0, 0), (0, 0))},
        "to the forfeiter": {"away": ((2, 6), (3, 6), (4, 6), (1, 6)),
                             "home": ((6, 2), (6, 3), (6, 4), (6, 1))},
    }
    TICKS = ("turned up", "forfeited", "none")
    # Who reports on a card, by the name a check hands its report to.
    REPORTS = {"verb-mismatch": ("verb-mismatch", validate.ERROR),
               "the verb cross-check": ("F6", "verb")}
    # Where a report is handed on again, by a rule the reader cannot see: F6
    # passes the verb it names on a card forfeit-contradicted reports (4.134).
    ONWARD = {"the verb cross-check": ("forfeit-contradicted", validate.ERROR)}

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _, rockets = TEAMS[cls.HOME]
        _, aces = TEAMS[cls.AWAY]
        cls.cards, cls.validated = {}, {}
        for verb in cls.VERBS:
            for box in ("away", "home"):
                turned_up = forfeit_match_winner(box)
                for tick in cls.TICKS:
                    winner = {"turned up": turned_up, "forfeited": box,
                              "none": cw.NO_TICK}[tick]
                    for shape, scores in cls.ROWS.items():
                        data = root / f"night-{len(cls.cards)}" / "data"
                        folder = data / f"Round{SHORT_ROUND}"
                        folder.mkdir(parents=True)
                        cw.write_card_file(folder / "card.pdf", short_spec(
                            cls.HOME, cls.AWAY, court="10", match_id="4000010",
                            rows=rows_for(scores[box], rockets, aces),
                            forfeited_by=box, verb=verb, winner=winner))
                        cards, rejected = scorecard.read_tree(data)
                        found = discover_mod.discover(
                            cards, rejected,
                            config=config_mod.Config(path=root / "config.toml"),
                            root=data)
                        comp_id, = sorted(found.matches)
                        competition = next(c for c in found.club.competitions
                                           if c.id == comp_id)
                        given = dict(matches=found.matches[comp_id],
                                     comp=competition, club=found.club,
                                     cards=cards, rejected=rejected)
                        report = validate.validate(
                            given["matches"], given["comp"], club=given["club"],
                            cards=cards, rejected=rejected)
                        card, = cards
                        # F8 is the team list being read from one card, every night.
                        said = [(i.rule, i.message.split(" cross-check ")[0]
                                 .rsplit(" ", 1)[-1] if i.rule == "F6"
                                 else i.severity)
                                for i in report.issues if i.rule != "F8"]
                        cls.cards[verb, box, tick, shape] = (card, said)
                        cls.validated[verb, box, tick, shape] = given

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def check(self, key, name: str):
        card, _ = self.cards[key]
        check, = [c for c in card.checks if c.name == name]
        return check

    def build(self, verb: str) -> subprocess.CompletedProcess:
        """``build.py --check`` on the filed card, printing *verb*."""
        _, rockets = TEAMS[self.HOME]
        _, aces = TEAMS[self.AWAY]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "data" / f"Round{SHORT_ROUND}"
            folder.mkdir(parents=True)
            cw.write_card_file(folder / "card.pdf", short_spec(
                self.HOME, self.AWAY, court="10", match_id="4000010",
                rows=rows_for(self.ROWS["left blank"]["away"], rockets, aces),
                forfeited_by="away", verb=verb))
            return subprocess.run((sys.executable, str(self.BUILD), "--check"),
                                  cwd=tmp, capture_output=True, text=True,
                                  timeout=600)

    def test_bows_to_on_the_filed_card_is_one_warning_that_the_word_is_unknown(self):
        key = ("BOWS TO", "away", "turned up", "left blank")
        self.assertEqual(self.cards[key][1], [("verb-unknown", validate.WARNING)])
        self.assertFalse(self.check(key, "verb").available)
        # The home tick restates the box, and agrees with it as on the filed card.
        tick = self.check(key, "winner-tick")
        self.assertEqual((tick.available, tick.agrees), (True, True))

    def test_bows_to_beside_a_tick_for_the_forfeiter_is_one_error_on_the_tick(self):
        key = ("BOWS TO", "away", "forfeited", "left blank")
        self.assertEqual(self.cards[key][1], [("F6", "winner-tick"),
                                              ("verb-unknown", validate.WARNING)])
        self.assertEqual(self.check(key, "winner-tick").reported_by, "")

    def test_def_by_run_together_on_the_rockets_forfeit_restates_the_box(self):
        # A loss for the rockets, who forfeited: only a tick for them is against
        # the box, and it is reported once, by itself.
        for tick, said in (("turned up", []), ("none", []),
                           ("forfeited", [("F6", "winner-tick")])):
            with self.subTest(tick=tick):
                key = ("DEF.BY", "home", tick, "left blank")
                self.assertEqual(self.cards[key][1], said)

    def test_the_build_exits_0_on_bows_to_and_says_the_word_is_unknown(self):
        done = self.build("BOWS TO")
        words = " ".join(done.stdout.split())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("0 error(s)", words)
        self.assertIn("result verb 'BOWS TO' is not one this program recognises",
                      words)
        self.assertNotIn("cross-check disagrees", words)

    def test_the_build_exits_1_on_drew_with_one_verb_mismatch(self):
        done = self.build("DREW")
        words = " ".join(done.stdout.split())
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("1 error(s)", words)
        self.assertIn("verb-mismatch", words)
        # Pinned whole: a draw is no side, and "means draw won" was no sentence.
        self.assertIn("the verb 'DREW' says the match was drawn, but the card says "
                      "away forfeited the match.", words)
        self.assertNotIn("verb-unknown", words)
        self.assertNotIn("cross-check disagrees", words)

    def test_every_hand_off_is_one_report_by_the_check_named_or_forfeit_contradicted(
            self):
        handed, by_ticks, onward = set(), set(), 0
        for key, (card, said) in self.cards.items():
            for check in card.checks:
                if check.reported_by:
                    handed.add(check.reported_by)
                    if check.name == "winner-tick":
                        by_ticks.add((key[2], check.reported_by))
                    reports = (self.REPORTS[check.reported_by],
                               self.ONWARD.get(check.reported_by))
                    onward += reports[1] in said
                    with self.subTest(card=key, check=check.name):
                        self.assertIn(check.reported_by, self.REPORTS)
                        # One report, the one it names or the one that takes it.
                        self.assertEqual([r in said for r in reports].count(True), 1)
        # The premise: the cards hand off to both, so the property is not vacuous.
        # A tick for the side that forfeited goes with its verb to verb-mismatch,
        # and one for the side that turned up to the verb check, on rows the verb
        # restating the box disagrees with, which hands it on where the club's
        # rules read those rows as the forfeiter's (4.134).
        self.assertEqual(handed, set(self.REPORTS))
        self.assertEqual(by_ticks, {("forfeited", "verb-mismatch"),
                                    ("turned up", "the verb cross-check")})
        # Every tick the verb check takes, 18, is over rows to the forfeiter,
        # which first to 6 finishes. F6 keeping one where the club's rule does not
        # finish the rows is pinned below, at pro-8 and on rows given up.
        self.assertEqual(onward, 18)

    def test_rows_given_to_the_forfeiter_are_one_error_whatever_the_card_prints(self):
        # 4.134: one forfeit-contradicted on every such card, and no F6. A verb the
        # table reads as a draw is against the rows and the box both, and is
        # verb-mismatch's as well; any other verb or tick restates one or the other.
        # A word the table does not hold is still a warning of its own.
        mine = ("forfeit-contradicted", validate.ERROR)
        for key, (card, said) in self.cards.items():
            verb, box, _, shape = key
            if shape != "to the forfeiter":
                continue
            draw = card_mod.verb_side(verb) == "draw"
            with self.subTest(card=key):
                self.assertEqual([s for s in said if s[1] != validate.WARNING],
                                 [("verb-mismatch", validate.ERROR), mine] if draw
                                 else [mine])

    def test_a_verb_run_together_with_a_stop_and_its_tick_are_one_error(self):
        # Read whole, with the stop for a space, each is a win for away, the side
        # that forfeited, and verb-mismatch reports the verb and the tick with it.
        for verb in ("DEF.BY", "DEFEATED.BY", "DEFEATS.BY", "LOSES.TO", "LOST.TO"):
            for shape in ("left blank", "a rubber each"):
                key = (verb, "away", "forfeited", shape)
                with self.subTest(card=key):
                    self.assertEqual(self.cards[key][1],
                                     [("verb-mismatch", validate.ERROR)])
                    self.assertEqual(self.check(key, "verb").reported_by,
                                     "verb-mismatch")
                    self.assertEqual(self.check(key, "winner-tick").reported_by,
                                     "verb-mismatch")

    def test_each_result_stated_against_the_box_on_rows_giving_none_is_one_report(self):
        # One report for each result the card states against the box, by the one
        # table, so a verb and a tick for one side are one, and a draw and a tick
        # for the side that forfeited are two. Rows given to the forfeiter are left
        # out: they state a result as well, and the checks hold the verb and the
        # tick to them.
        reports = {("F6", "verb"), ("F6", "winner-tick"),
                   ("verb-mismatch", validate.ERROR)}
        for key, (card, said) in self.cards.items():
            verb, box, _, shape = key
            if shape == "to the forfeiter":
                continue
            stated = ({card_mod.verb_side(verb), card.winner_tick}
                      - {None, forfeit_match_winner(box)})
            with self.subTest(card=key):
                self.assertEqual(len([s for s in said if s in reports]),
                                 len(stated), said)

    def test_a_verb_the_box_contradicts_on_rows_giving_none_is_verb_mismatchs(self):
        # Every verb the table reads as other than the box's result, a draw as
        # well as the side that forfeited, is handed to verb-mismatch, which
        # reports it; one that restates the box agrees; and no other word is read.
        for key, (card, said) in self.cards.items():
            verb, box, _, shape = key
            if shape == "to the forfeiter":
                continue
            reading = card_mod.verb_side(verb)
            check = self.check(key, "verb")
            with self.subTest(card=key):
                if reading is None:
                    self.assertFalse(check.available)
                elif reading == forfeit_match_winner(box):
                    self.assertEqual((check.agrees, check.reported_by), (True, ""))
                else:
                    self.assertEqual((check.agrees, check.reported_by),
                                     (False, "verb-mismatch"))
                    self.assertIn(("verb-mismatch", validate.ERROR), said)

    def test_a_verb_for_the_forfeiter_is_reported_once_by_verb_mismatch(self):
        for box, verbs in (("away", ("DEF BY", "DEF. BY", "DEFEATED BY", "LOSES TO",
                                     "LOST TO", "DEFEATS BY")),
                           ("home", ("DEFEATS", "DEF", "DEFEATED", "WINS", "WON",
                                     "BEATS", "BEAT"))):
            for verb in verbs:
                for tick in self.TICKS:
                    for shape in ("left blank", "a rubber each"):
                        key = (verb, box, tick, shape)
                        with self.subTest(card=key):
                            self.assertEqual(self.cards[key][1],
                                             [("verb-mismatch", validate.ERROR)])
                            self.assertEqual(self.check(key, "verb").reported_by,
                                             "verb-mismatch")

    def test_the_verbs_are_every_verb_the_table_knows_and_words_it_does_not(self):
        keys = {card_mod.verb_key(verb) for verb in self.VERBS}
        for verb in (card_mod._HOME_VERBS | card_mod._AWAY_VERBS
                     | card_mod._DRAW_VERBS):
            with self.subTest(verb=verb):
                self.assertIn(verb, keys)
                if " " in verb:
                    self.assertIn(verb.replace(" ", ".").upper(), self.VERBS)
        # And words the marks once read that the table does not hold, so the
        # properties above are asked of a verb no reading knows.
        for verb in ("WIN", "D.", "BOWS TO", "FELL BY", "TROUNCES"):
            with self.subTest(verb=verb):
                self.assertIn(verb, self.VERBS)
                self.assertIsNone(card_mod.verb_side(verb))

    def test_validate_reads_a_verb_through_the_one_whole_verb_reading(self):
        # A spy rather than a card: a second copy of the reading that agrees today
        # cannot be told apart from it by any card.
        self.assertIs(validate.verb_side, card_mod.verb_side)
        given = self.validated["BOWS TO", "away", "turned up", "left blank"]
        with mock.patch.object(validate, "verb_side",
                               wraps=card_mod.verb_side) as reading:
            validate.validate(given["matches"], given["comp"], club=given["club"],
                              cards=given["cards"], rejected=given["rejected"])
        self.assertIn(mock.call("BOWS TO"), reading.call_args_list)

    def test_validate_asks_the_one_separator_rule(self):
        self.assertIs(validate.states_no_result, card_mod.states_no_result)
        given = self.validated["VS", "away", "none", "left blank"]
        with mock.patch.object(validate, "states_no_result",
                               wraps=card_mod.states_no_result) as rule:
            validate.validate(given["matches"], given["comp"], club=given["club"],
                              cards=given["cards"], rejected=given["rejected"])
        self.assertIn(mock.call("VS"), rule.call_args_list)


class OneNightCase(unittest.TestCase):
    """One card a night, the rockets at home to the aces, read, found and validated.

    Each night has a folder of its own, so ``build.py`` can be run on any of them,
    and ``said`` is what validate reported, F8 left out: it is the team list
    being read from one card, every night. An F6 error is named by its check.
    """

    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    BUILD = ROOT / "build.py"

    @classmethod
    def cases(cls):
        return {}

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.nights = {}
        for key, fields in cls.cases().items():
            cls.nights[key] = cls.night(len(cls.nights), **fields)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def night(cls, number: int, *, scores, config: str = "", retired=(),
              **fields) -> dict:
        _, rockets = TEAMS[cls.HOME]
        _, aces = TEAMS[cls.AWAY]
        root = Path(cls._tmp.name) / f"night-{number}"
        data = root / "data"
        folder = data / f"Round{SHORT_ROUND}"
        folder.mkdir(parents=True)
        if config:
            (root / "config.toml").write_text(config)
        rows = list(rows_for(scores, rockets, aces))
        for index, side in retired:     # (row, side that retired)
            rows[index] = dataclasses.replace(rows[index], status=SetStatus.RETIRED,
                                              status_side=side)
        path = cw.write_card_file(folder / "card.pdf", short_spec(
            cls.HOME, cls.AWAY, court="10", match_id="4000010",
            rows=tuple(rows), **fields))
        cards, rejected = scorecard.read_tree(data)
        found = discover_mod.discover(
            cards, rejected,
            config=(config_mod.load(root / "config.toml") if config
                    else config_mod.Config(path=root / "config.toml")),
            root=data)
        comp_id, = sorted(found.matches)
        competition = next(c for c in found.club.competitions if c.id == comp_id)
        given = dict(matches=found.matches[comp_id], comp=competition,
                     club=found.club, cards=cards, rejected=rejected)
        report = cls.validate(given)
        card, = cards
        return dict(root=root, path=path, card=card, given=given, report=report,
                    said=cls.said_of(report))

    @staticmethod
    def validate(given: dict) -> validate.Report:
        return validate.validate(given["matches"], given["comp"], club=given["club"],
                                 cards=given["cards"], rejected=given["rejected"])

    @staticmethod
    def said_of(report) -> list[tuple[str, str]]:
        return [(i.rule, i.message.split(" cross-check ")[0].rsplit(" ", 1)[-1]
                 if i.rule == "F6" and i.severity == validate.ERROR
                 else i.severity)
                for i in report.issues if i.rule != "F8"]

    def errors(self, key) -> list[tuple[str, str]]:
        return [said for said in self.nights[key]["said"]
                if said[1] != validate.WARNING]

    def check(self, key, name: str):
        check, = [c for c in self.nights[key]["card"].checks if c.name == name]
        return check

    def build(self, key) -> tuple[int, str]:
        """``build.py --check`` on the night's own folder, its output as words."""
        done = subprocess.run((sys.executable, str(self.BUILD), "--check"),
                              cwd=self.nights[key]["root"], capture_output=True,
                              text=True, timeout=600)
        return done.returncode, " ".join((done.stdout + done.stderr).split())


class TestAVerbTheRowsContradictIsReportedOnceByVerbMismatch(OneNightCase):
    """A misprinted verb is one ``verb-mismatch``, and a tick restating it goes with it.

    Row 4.131. The reader's verb check holds the verb to the set rows and F6
    makes its disagreement an error; ``verb-mismatch`` holds the same verb to
    the result under the club's rules, or on a forfeit to the box, and reports
    it again. Measured with /tmp/t7/scratch/V2/probe/verbs.py and more.py on the
    tree before this: a played card with rows 6-2, 6-3, 6-4, 6-1, ``LOSES TO``
    and the away Match Winner tick got F6 on its verb, F6 on its tick and
    ``verb-mismatch``, three errors for one misprint; so did the same rows the
    other way round under ``DEFEATS`` and the home tick, and with Match Forfeited
    ticked against the away side, rows for home under ``LOSES TO`` and the away
    tick. With no tick, or one the rows agree with, it was two. Over the 90
    nights below (/tmp/t7/scratch/V2/probe/sweep.py), 30 carried F6 on the verb
    beside ``verb-mismatch``, 5 of them F6 on a tick for the verb's side as well,
    137 errors in all.

    ``verb-mismatch`` stays, as 4.33 made it the owner of a forfeit's verb: it
    names what the verb says and what the card is scored on. Whether it reports
    a card is a question of whether the card counts, how many of its sets
    finished under the club's set rule, and which side the rules decide it for,
    and the reader, which sets ``reported_by``, sees none of them. So F6 asks
    ``_check_verb`` where it reports, and there passes by the verb and a tick
    naming the side the verb names. A tick for another side is a second thing
    the card says, and keeps its own F6. Where ``verb-mismatch`` does not fire, on
    a card read empty that counts for nothing or one with too few finished sets,
    F6 is still the report and keeps both.

    Rejected: keeping F6 and dropping ``verb-mismatch``, since F6 cannot see
    the rules or the box; and ``reported_by="verb-mismatch"`` set by the reader
    on every verb the rows contradict, which on the card read empty and the
    card short of sets below would name a report that never comes, and leave
    the misprint reported by nothing.
    """

    ROWS = {"to home": ((6, 2), (6, 3), (6, 4), (6, 1)),
            "to away": ((2, 6), (3, 6), (4, 6), (1, 6)),
            "level": ((6, 4), (4, 6), (6, 4), (4, 6)),
            "read empty": ((0, 0),) * 4,
            "short of sets": ((6, 4), (0, 5), (0, 5), (0, 5))}
    VERBS = ("DEFEATS", "LOSES TO", "DRAWS WITH")
    TICKS = ("home", "away", "none")

    @classmethod
    def cases(cls):
        return {(verb, tick, shape, box): dict(
                    scores=scores, verb=verb, forfeited_by=box,
                    winner=cw.NO_TICK if tick == "none" else tick)
                for verb in cls.VERBS for tick in cls.TICKS
                for shape, scores in cls.ROWS.items() for box in (None, "away")}

    def test_a_played_verb_and_the_tick_restating_it_are_one_verb_mismatch(self):
        for key in (("LOSES TO", "away", "to home", None),
                    ("DEFEATS", "home", "to away", None)):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [("verb-mismatch", validate.ERROR)])
                # The reader still says what it read: it cannot know the report is
                # made, so it names none.
                for name in ("verb", "winner-tick"):
                    check = self.check(key, name)
                    self.assertEqual((check.agrees, check.reported_by), (False, ""))

    def test_a_verb_beside_a_tick_the_rows_agree_with_is_one_verb_mismatch(self):
        for key in (("LOSES TO", "home", "to home", None),
                    ("DRAWS WITH", "home", "to home", None),
                    ("LOSES TO", "none", "to home", None),
                    ("DRAWS WITH", "none", "short of sets", "away")):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [("verb-mismatch", validate.ERROR)])

    def test_a_forfeits_verb_for_the_forfeiter_over_rows_for_the_other_is_one(self):
        key = ("LOSES TO", "away", "to home", "away")
        self.assertEqual(self.errors(key), [("verb-mismatch", validate.ERROR)])
        self.assertIn("but the card says away forfeited the match.",
                      self.nights[key]["report"].errors[0].message)

    def test_a_tick_for_a_side_the_verb_does_not_name_keeps_its_own_f6(self):
        for key in (("DRAWS WITH", "away", "to home", None),
                    ("DRAWS WITH", "away", "to home", "away")):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [("F6", "winner-tick"),
                                                    ("verb-mismatch", validate.ERROR)])

    def test_a_verb_verb_mismatch_passes_by_is_still_f6s_to_report(self):
        # Read empty, the card counts for nothing and no verb check holds its verb
        # to a result; short of sets, it is verb-no-result's. Neither is the report
        # F6 stands down for, so the verb and the tick keep theirs.
        for key, also in ((("DEFEATS", "home", "read empty", None), []),
                          (("DEFEATS", "home", "short of sets", None),
                           [("verb-no-result", validate.ERROR)])):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [("F6", "verb"),
                                                    ("F6", "winner-tick"), *also])

    def test_on_every_night_a_verb_is_reported_once_and_its_tick_with_it(self):
        both = alone = handed = 0
        for key, night in self.nights.items():
            card, said = night["card"], night["said"]
            failed = [check.name for check in card.cross_checks_failed]
            expected = failed
            if ("verb-mismatch", validate.ERROR) in said:
                both += "verb" in failed
                expected = [name for name in failed if name != "verb"
                            and not (name == "winner-tick" and card.winner_tick
                                     == card_mod.verb_side(card.verb))]
            elif ("forfeit-contradicted", validate.ERROR) in said:
                handed += "verb" in failed
            else:
                alone += "verb" in failed
            if ("forfeit-contradicted", validate.ERROR) in said:
                # What restates the box is that finding's, which names it (4.134).
                came = forfeit_match_winner(card.forfeited_by)
                expected = [name for name in expected if not (
                    name == "verb" and card_mod.verb_side(card.verb) == came
                    or name == "winner-tick" and card.winner_tick == came)]
            with self.subTest(night=key):
                self.assertEqual([what for rule, what in said if rule == "F6"],
                                 expected)
        # The premise: the reader disagreed on verbs verb-mismatch reports, and on
        # verbs it does not, so neither half of the property is vacuous. The 15 are
        # the 12 read empty or short of sets with no box, and 3 that restate the
        # box over rows short of sets, which the club's rule does not finish; the
        # 3 that restate it over rows to away are forfeit-contradicted's.
        self.assertEqual((both, alone, handed), (30, 15, 3))

    def test_f6_asks_the_verb_check_where_it_reports(self):
        # A spy rather than a card: F6 keeping its own copy of the verb check's
        # gates would agree with it on every card until the day one changed.
        key = ("LOSES TO", "away", "to home", None)
        with mock.patch.object(validate, "_check_verb", return_value=[]):
            report = self.validate(self.nights[key]["given"])
        self.assertEqual(self.said_of(report), [("F6", "verb"), ("F6", "winner-tick")])

    def test_the_build_reports_a_misprinted_verb_and_its_tick_once(self):
        code, words = self.build(("LOSES TO", "away", "to home", None))
        self.assertEqual(code, 1, words)
        self.assertIn("1 error(s)", words)
        self.assertIn("the verb 'LOSES TO' means away won, but the set rows give "
                      "home.", words)
        self.assertNotIn("cross-check disagrees", words)


class TestAForfeitWhoseRowsGiveTheMatchToTheSideThatForfeitedIsOneError(OneNightCase):
    """Set rows giving a forfeit to the side its box says forfeited are one error.

    Row 4.134. Measured by /tmp/t7/scratch/V4/probe/probe.py on the base, the
    aces' Match Forfeited box ticked and the rows 2-6, 3-6, 4-6, 1-6: printing
    no verb and the aces' Match Winner tick, the card built with no error, its
    tick check saying it "agrees with the set rows", and was paid as a forfeit
    to the rockets, 5.0 points to 4.4 under ``games-bonus``. So was the card
    printing no tick, and one printing ``LOSES TO`` and the aces' tick was one
    ``verb-mismatch``, about the verb. What the card got hung on what else it
    printed: ``DEFEATS`` and the rockets' tick was F6 on the verb; the rockets'
    tick alone, F6 on the tick; ``LOSES TO`` beside it, that and
    ``verb-mismatch``. The contradiction is one, the rows against the box, and
    nothing named it.

    Now ``forfeit-contradicted`` reports it, once, an error. The rows are
    evidence against the box only as the club's rules read them, which
    :func:`~matchcentre.validate.rows_against_box` asks: every row a set the
    club's set rule finishes, more sets to the side that forfeited than to the
    side that came, and the match to it by the club's ``decide_by``. A set the
    club's rule does not finish is absence of evidence, so a forfeit given up
    part way, or rows another rule finishes, are not asked, and rows giving the
    side that came as many sets or more give the box nothing to answer. Its
    detail names what else the card prints on either side: on a card it
    reports, F6 passes by a verb, tick or TOTAL row restating the box,
    ``verb-mismatch`` a verb for the side that forfeited, and
    ``totals-mismatch`` a TOTAL row totalling the box's award under the club's
    set rule. A draw verb says what neither does, and stays ``verb-mismatch``'s.

    Then the tree before this was found failing cards the base builds clean,
    since it asked whether any set rule finished the rows. Measured by
    /tmp/t7/scratch/V4/fix3/probe/p4.py, 33 cards under the default config,
    sets-first and pro-8: 4-0, 3-4, 3-4, 2-4 at first to 6, which only fast4
    finishes, and 1-4, 2-4, 0-4, 3-4 each gained this error; so did the filed
    card at pro-8 printing no TOTAL row or the rows' sums, and 6-0, 4-6, 4-6,
    4-6 there, none of whose sets pro-8 finishes. And a mutant asking that each
    row be finished under some rule, not every row under one, survived its
    tests; here it fails 5-7, 5-6, 2-6, 1-6, which no one rule finishes.

    Rejected: reporting it beside the F6 errors and ``verb-mismatch``, up to
    three reports of one fact; a warning, as ``set-forfeit-contradicted`` is,
    which would make the F6 errors it takes over a warning; the rows under any
    set rule, which fails the cards above; and rows giving the side that came
    the match on neither games nor sets, the tree before this's reading, which
    fails a card whose sets are level and passes a sets-first club's card
    giving the side that forfeited 3-1 in sets.
    """

    TO_AWAY = ((2, 6), (3, 6), (4, 6), (1, 6))
    TO_HOME = ((6, 2), (6, 3), (6, 4), (6, 1))
    GAMES_AWAY = ((6, 4), (4, 6), (0, 6), (6, 5))    # games 16-21, sets 2-2
    SETS_AWAY = ((6, 0), (4, 6), (4, 6), (5, 6))     # games 19-18, sets 1-3
    EVEN_GAMES = ((6, 0), (4, 6), (4, 6), (4, 6))    # games 18-18, sets 1-3
    GIVEN_UP = ((2, 6), (3, 3), (0, 0), (0, 0))
    RETIRED = ((2, 6), (3, 1), (0, 0), (0, 0))       # the second, by the aces
    SHORT = ((4, 0), (3, 4), (3, 4), (2, 4))         # sets 1-3 at fast4 alone
    MIXED_RULES = ((5, 7), (5, 6), (2, 6), (1, 6))   # no one rule finishes both
    LEVEL = ((6, 4), (4, 6), (6, 4), (4, 6))         # games 20-20, sets 2-2
    SETS_FIRST = '[rules]\nscoring = "sets-first"\n'
    PRO8 = '[rules]\nscoring = "games-bonus"\nsets = "pro-8"\n'
    FAST4 = '[rules]\nsets = "fast4"\n'
    ADVANTAGE6 = '[rules]\nsets = "advantage-6"\n'
    MINE = ("forfeit-contradicted", validate.ERROR)
    MESSAGE = ("the card says away forfeited the match, but its set rows, every "
               "set finished, give away more sets and the match.")
    READ = ("sets 0-4 and games 10-24 decided on games then sets say away; the "
            "away Match Forfeited box gives the match to home")

    @classmethod
    def cases(cls):
        away = dict(scores=cls.TO_AWAY, forfeited_by="away")
        bare = dict(verb="", winner=cw.NO_TICK)
        return {
            "filed": dict(away, verb="", winner="away"),
            "no verb, no tick": dict(away, **bare),
            "for the forfeiter": dict(away, verb="LOSES TO", winner="away"),
            "restating the box": dict(away),
            "verb for the forfeiter": dict(away, verb="LOSES TO", winner="home"),
            "a draw": dict(away, verb="DRAWS WITH", winner=cw.NO_TICK),
            "the rockets' forfeit": dict(scores=cls.TO_HOME, forfeited_by="home",
                                         verb="DEFEATS", winner="home"),
            "left blank": dict(scores=((0, 0),) * 4, forfeited_by="away"),
            "level": dict(scores=cls.LEVEL, forfeited_by="away"),
            "rows to the side that came": dict(scores=cls.TO_HOME, forfeited_by="away",
                                               verb="", winner="home"),
            "no box": dict(scores=cls.TO_AWAY, verb="", winner="away"),
            "games to away": dict(away, scores=cls.GAMES_AWAY, **bare),
            "sets to away": dict(away, scores=cls.SETS_AWAY, **bare),
            "sets to away, sets-first": dict(away, scores=cls.SETS_AWAY, **bare,
                                             config=cls.SETS_FIRST),
            "even games": dict(away, scores=cls.EVEN_GAMES, **bare),
            "given up part way": dict(away, scores=cls.GIVEN_UP, **bare),
            "retired, then forfeited": dict(away, scores=cls.RETIRED, **bare,
                                            retired=((1, "away"),)),
            "given up, the box restated": dict(away, scores=cls.GIVEN_UP),
            "short": dict(away, scores=cls.SHORT, **bare),
            "short, fast4": dict(away, scores=cls.SHORT, **bare, omit_totals=True,
                                 config=cls.FAST4),
            "short, verb and award": dict(away, scores=cls.SHORT, verb="LOSES TO",
                                          winner=cw.NO_TICK, totals=(4, 24, 0, 0)),
            "short, fast4, verb and award": dict(away, scores=cls.SHORT,
                                                 verb="LOSES TO", winner=cw.NO_TICK,
                                                 totals=(4, 16, 0, 0),
                                                 config=cls.FAST4),
            "mixed rules": dict(away, scores=cls.MIXED_RULES, **bare),
            "mixed rules, advantage-6": dict(away, scores=cls.MIXED_RULES, **bare,
                                             omit_totals=True, config=cls.ADVANTAGE6),
            "filed, pro-8": dict(away, verb="", winner="away", omit_totals=True,
                                 config=cls.PRO8),
            "filed, pro-8, the rows' sums": dict(away, verb="", winner="away",
                                                 totals=(0, 10, 24, 0),
                                                 config=cls.PRO8),
            "restating the box, pro-8": dict(away, omit_totals=True, config=cls.PRO8),
            "the award": dict(away, **bare, totals=(4, 24, 0, 0)),
            "another rule's award": dict(away, **bare, totals=(4, 32, 0, 0)),
            "no award": dict(away, **bare, totals=(4, 9, 0, 0)),
            "no TOTAL row": dict(away, **bare, omit_totals=True),
        }

    def finding(self, key) -> validate.Issue:
        found, = [i for i in self.nights[key]["report"].errors
                  if i.rule == "forfeit-contradicted"]
        return found

    def test_the_filed_card_is_one_error_naming_the_rows_against_the_box(self):
        self.assertEqual(self.errors("filed"), [self.MINE])
        issue = self.finding("filed")
        self.assertEqual(issue.message, f"{issue.subject}: {self.MESSAGE}")
        self.assertEqual(issue.detail, f"{self.READ}, and the away Match Winner tick "
                                       "gives it to away")
        # The tick is for the side the rows give the match to, and agrees with them.
        self.assertTrue(self.check("filed", "winner-tick").agrees)

    def test_a_card_printing_only_its_rows_and_its_box_is_that_error(self):
        self.assertEqual(self.errors("no verb, no tick"), [self.MINE])
        self.assertEqual(self.finding("no verb, no tick").detail, self.READ)

    def test_a_verb_and_tick_for_the_forfeiter_are_named_in_it_and_not_again(self):
        # verb-mismatch passes by the verb the rows agree with: the one fact is the
        # rows, the verb and the tick against the box.
        self.assertEqual(self.errors("for the forfeiter"), [self.MINE])
        self.assertEqual(self.finding("for the forfeiter").detail,
                         f"{self.READ}, and the verb 'LOSES TO' and the away Match "
                         "Winner tick give it to away")

    def test_a_verb_and_tick_restating_the_box_are_named_in_it_and_not_again(self):
        # The reader holds the verb to the rows and hands the tick to it, as on the
        # base; F6 passes the verb by, since this reports the card and names it.
        self.assertEqual(self.errors("restating the box"), [self.MINE])
        self.assertEqual(self.finding("restating the box").detail,
                         f"{self.READ}, as the verb 'DEFEATS' and the home Match "
                         "Winner tick do")
        self.assertFalse(self.check("restating the box", "verb").agrees)
        self.assertEqual(self.check("restating the box", "winner-tick").reported_by,
                         "the verb cross-check")

    def test_a_verb_for_the_forfeiter_beside_the_boxs_tick_is_one_error(self):
        self.assertEqual(self.errors("verb for the forfeiter"), [self.MINE])
        self.assertEqual(self.finding("verb for the forfeiter").detail,
                         f"{self.READ}, as the home Match Winner tick does, and the "
                         "verb 'LOSES TO' gives it to away")

    def test_a_draw_is_still_verb_mismatchs_beside_it(self):
        self.assertEqual(self.errors("a draw"),
                         [("verb-mismatch", validate.ERROR), self.MINE])
        # A draw gives the match to neither side, so is named on neither.
        self.assertEqual(self.finding("a draw").detail, self.READ)

    def test_the_rockets_forfeit_is_read_the_same_way(self):
        self.assertEqual(self.errors("the rockets' forfeit"), [self.MINE])
        issue = self.finding("the rockets' forfeit")
        self.assertEqual(issue.message,
                         f"{issue.subject}: the card says home forfeited the match, "
                         "but its set rows, every set finished, give home more sets "
                         "and the match.")
        self.assertEqual(issue.detail,
                         "sets 4-0 and games 24-10 decided on games then sets say "
                         "home; the home Match Forfeited box gives the match to "
                         "away, and the verb 'DEFEATS' and the home Match Winner "
                         "tick give it to home")

    def test_rows_giving_the_side_that_came_as_many_sets_or_the_match_are_not_it(self):
        # Level sets give the side that forfeited nothing the box must answer,
        # whatever the games; and games-bonus gives the rockets 19-18 the match.
        for key in ("left blank", "level", "rows to the side that came", "no box",
                    "games to away", "sets to away"):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [])

    def test_rows_a_sets_first_club_gives_the_forfeiter_are_it(self):
        self.assertEqual(self.errors("sets to away, sets-first"), [self.MINE])
        self.assertEqual(self.finding("sets to away, sets-first").detail,
                         "sets 1-3 and games 19-18 decided on sets then games say "
                         "away; the away Match Forfeited box gives the match to home")

    def test_level_games_are_decided_on_the_next_basis(self):
        self.assertEqual(self.errors("even games"), [self.MINE])
        self.assertEqual(self.finding("even games").detail,
                         "sets 1-3 and games 18-18 decided on games then sets say "
                         "away; the away Match Forfeited box gives the match to home")

    def test_rows_the_clubs_decide_by_gives_nobody_are_not_it(self):
        # No preset decides on games alone, so the competition's rules are
        # replaced: level games are then a draw, which is not the side that
        # forfeited, and sets alone give it the match.
        given = self.nights["even games"]["given"]
        for decide_by, said in ((("games",), []), (("sets",), [self.MINE])):
            comp = dataclasses.replace(given["comp"], rules=dataclasses.replace(
                given["comp"].rules, decide_by=decide_by))
            with self.subTest(decide_by=decide_by):
                report = self.validate(dict(given, comp=comp))
                self.assertEqual([s for s in self.said_of(report)
                                  if s[1] != validate.WARNING], said)

    def test_a_forfeit_given_up_part_way_is_not_it(self):
        # A correct card: its rows are the lead when the aces gave up, and the
        # club's rule finishes neither 3-3 nor a set retired.
        for key in ("given up part way", "retired, then forfeited"):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [])
                comp = self.nights[key]["given"]["comp"]
                self.assertEqual(validate.rows_against_box(
                    self.nights[key]["card"], comp.fmt.set_rule, comp.rules), "")

    def test_a_verb_and_tick_restating_the_box_over_rows_given_up_are_one_report(self):
        # As on the base: the verb is held to the rows, and the tick restating it
        # with it is handed to the verb's F6, one report, not two.
        self.assertEqual(self.errors("given up, the box restated"), [("F6", "verb")])
        self.assertEqual(self.check("given up, the box restated",
                                    "winner-tick").reported_by, "the verb cross-check")

    def test_rows_only_another_set_rule_finishes_are_not_it(self):
        # 4-0 is no set at first to 6; at fast4, which the club plays there, the
        # rows give the aces 3-1 in sets and the match.
        self.assertEqual(self.errors("short"), [])
        self.assertEqual(self.errors("short, fast4"), [self.MINE])
        self.assertTrue(self.finding("short, fast4").detail.startswith(
            "sets 1-3 and games 12-12 decided on games then sets say away; "))

    def test_a_verb_and_award_on_rows_another_rule_finishes_are_as_they_were(self):
        # At first to 6 the rows give nothing, so the verb for the aces is held to
        # the box and the award to the rows, as on the base; at fast4 the rows give
        # the aces the match, and both are the finding's, which names them.
        self.assertEqual(self.errors("short, verb and award"),
                         [("totals-mismatch", validate.ERROR),
                          ("verb-mismatch", validate.ERROR)])
        self.assertEqual(self.errors("short, fast4, verb and award"), [self.MINE])
        self.assertEqual(self.finding("short, fast4, verb and award").detail,
                         "sets 1-3 and games 12-12 decided on games then sets say "
                         "away; the away Match Forfeited box gives the match to home, "
                         "as the printed TOTAL row does, and the verb 'LOSES TO' "
                         "gives it to away")

    def test_rows_no_one_set_rule_finishes_are_not_it(self):
        # 5-7 is a set at advantage 6 and 5-6 at first to 6, and neither at the
        # other: under the club's rule, one row is always unfinished.
        for key in ("mixed rules", "mixed rules, advantage-6"):
            with self.subTest(night=key):
                self.assertNotIn(self.MINE, self.errors(key))

    def test_at_pro_8_six_game_rows_are_not_it_and_f6_reports_as_it_did(self):
        for key, said in (("filed, pro-8", []), ("filed, pro-8, the rows' sums", []),
                          ("restating the box, pro-8", [("F6", "verb")])):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), said)

    def test_the_build_passes_rows_the_clubs_rule_does_not_finish(self):
        for key in ("given up part way", "retired, then forfeited", "short",
                    "filed, pro-8"):
            with self.subTest(night=key):
                code, words = self.build(key)
                self.assertEqual(code, 0, words)
                self.assertIn("0 error(s)", words)
                self.assertNotIn("forfeit-contradicted", words)

    def test_f6_passes_by_only_what_names_the_side_that_came(self):
        # A tick naming nobody, both boxes ticked, restates nothing: it keeps the
        # F6 it had on the base beside the finding.
        night = self.nights["no verb, no tick"]
        card = night["card"]
        tick = dataclasses.replace(
            self.check("no verb, no tick", "winner-tick"), available=True,
            agrees=False, detail="both Match Winner boxes are ticked, so the card "
                                 "does not say who won")
        both = dataclasses.replace(card, winner_tick=None, checks=tuple(
            tick if check.name == "winner-tick" else check for check in card.checks))
        report = self.validate(dict(night["given"], cards=[both]))
        self.assertEqual([s for s in self.said_of(report) if s[1] != validate.WARNING],
                         [("F6", "winner-tick"), self.MINE])

    def test_a_total_row_totalling_the_boxs_award_is_named_in_it_and_not_again(self):
        # The row sides with the box, as a verb restating it does: one contradiction.
        self.assertEqual(self.errors("the award"), [self.MINE])
        self.assertEqual(self.finding("the award").detail,
                         f"{self.READ}, as the printed TOTAL row does")
        # A card printing no TOTAL row prints none that restates the box.
        self.assertEqual(self.errors("no TOTAL row"), [self.MINE])
        self.assertEqual(self.finding("no TOTAL row").detail, self.READ)

    def test_a_total_row_totalling_another_rules_award_or_none_is_reported_too(self):
        # 32-0 is the award at pro-8, not at first to 6, which the club plays; 9-0
        # is no award. Each is totals-mismatch's, held to the rows.
        for key in ("another rule's award", "no award"):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key),
                                 [("totals-mismatch", validate.ERROR), self.MINE])
                self.assertEqual(self.finding(key).detail, self.READ)

    def test_a_played_cards_total_row_is_checked_on_the_set_rule_alone(self):
        # As on the base, so a caller holding only the set rule may check one, as
        # test_cli's list test does. The pass-by asking the club's scoring of every
        # wrong row raised there, 3 errors in the full suite. Rejected: leaving it
        # to that test, which raises rather than fails; here the competition counts
        # what it is asked.
        from types import SimpleNamespace
        night = self.nights["no box"]
        comp, asked = night["given"]["comp"], []

        class Competition:
            def __getattr__(self, name):
                asked.append(name)
                return getattr(comp, name)

        card = dataclasses.replace(night["card"], totals=card_mod.Totals(9, 10, 24, 0))
        found = validate._check_totals(SimpleNamespace(
            rule=comp.fmt.set_rule, in_play=[card], competition=Competition()))
        self.assertEqual([issue.rule for issue in found], ["totals-mismatch"])
        self.assertEqual(asked, [])

    def test_validate_asks_the_rule_under_the_clubs_set_rule_and_scoring(self):
        # A spy rather than a card: a copy of the rule, or the rule asked under
        # another set rule, agrees on every card until the day one differs. The
        # night's verb and TOTAL row are asked it of too, by the passes-by.
        given = self.nights["short, fast4, verb and award"]["given"]
        comp = given["comp"]
        with mock.patch.object(validate, "rows_against_box",
                               wraps=validate.rows_against_box) as rule:
            self.validate(given)
        self.assertIn(mock.call(given["cards"][0], comp.fmt.set_rule, comp.rules),
                      rule.call_args_list)
        self.assertEqual({(call.args[1], call.args[2]) for call in rule.call_args_list},
                         {(comp.fmt.set_rule, comp.rules)})
        # Told the rows are not against the box, validate reports nothing of them,
        # and verb-mismatch and totals-mismatch what they passed by.
        with mock.patch.object(validate, "rows_against_box", return_value=""):
            for key, said in (("for the forfeiter", "verb-mismatch"),
                              ("the award", "totals-mismatch")):
                with self.subTest(night=key):
                    report = self.validate(self.nights[key]["given"])
                    self.assertEqual([s for s in self.said_of(report)
                                      if s[1] != validate.WARNING],
                                     [(said, validate.ERROR)])

    def test_validate_prices_the_box_as_the_reader_does(self):
        # A spy, as above: told the box awards something else, validate holds the
        # row to the rows and names it in nothing.
        self.assertIs(validate.box_award, scorecard.box_award)
        given = self.nights["the award"]["given"]
        other = dataclasses.replace(scorecard.box_award("away", given["comp"].fmt),
                                    home_sets=3)
        with mock.patch.object(validate, "box_award", return_value=other):
            report = self.validate(given)
        self.assertEqual([i.rule for i in report.errors],
                         ["totals-mismatch", "forfeit-contradicted"])
        found, = [i for i in report.errors if i.rule == "forfeit-contradicted"]
        self.assertEqual(found.detail, self.READ)

    def test_the_box_is_named_in_the_words_the_readers_checks_use(self):
        # A spy for the same reason: the box worded a second way reads the same.
        with mock.patch.object(validate, "_box", return_value="THE BOX") as named:
            report = self.validate(self.nights["filed"]["given"])
        self.assertIn(mock.call("away"), named.call_args_list)
        found, = [i for i in report.errors if i.rule == "forfeit-contradicted"]
        self.assertEqual(found.detail, self.READ.replace(
            "the away Match Forfeited box", "THE BOX") + ", and the away Match Winner "
            "tick gives it to away")

    def test_the_build_fails_on_the_filed_card_within_the_reports_width(self):
        done = subprocess.run((sys.executable, str(self.BUILD), "--check"),
                              cwd=self.nights["filed"]["root"], capture_output=True,
                              text=True, timeout=600)
        words = " ".join(done.stdout.split())
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("1 error(s)", words)
        self.assertIn("forfeit-contradicted", words)
        self.assertIn(self.MESSAGE, words)
        self.assertNotIn("cross-check disagrees", words)
        self.assertLessEqual(max(map(len, done.stdout.splitlines())), 78)


class TestAForfeitsVerbMismatchSaysWhatTheBoxGivesTheMatchTo(OneNightCase):
    """``verb-mismatch`` on a forfeit says which side the box is ticked against.

    Row 4.132. Measured by /tmp/t7/scratch/V4/probe/probe.py on the tree before
    this, the aces' Match Forfeited box ticked, four 0-0 rows and ``LOSES TO``:
    the message said "the verb 'LOSES TO' means away won, but the card says away
    forfeited the match", and the detail under it "verb 'LOSES TO' says away;
    the Match Forfeited tick says home". The box ticked is the away side's, and a
    tick that "says home" reads as one for home, so the detail seemed to
    contradict the line above it. It now names the box by the side it is ticked
    against, in the words every check held to it uses,
    :func:`~matchcentre.scorecard._box`, and says whom that gives the match to:
    "the away Match Forfeited box gives the match to home". The message still
    names only the side that forfeited.

    Rejected: the winner in the message as well, one fact on two lines, where the
    detail is what the card was read as; and "the Match Forfeited box, ticked
    against away", a second way of naming the one box.
    """

    EMPTY = ((0, 0),) * 4

    @classmethod
    def cases(cls):
        return {
            "LOSES TO": dict(scores=cls.EMPTY, forfeited_by="away",
                             verb="LOSES TO", winner=cw.NO_TICK),
            "DRAWS WITH": dict(scores=cls.EMPTY, forfeited_by="away",
                               verb="DRAWS WITH", winner=cw.NO_TICK),
            "DEFEATS": dict(scores=cls.EMPTY, forfeited_by="home",
                            verb="DEFEATS", winner=cw.NO_TICK),
        }

    def issue(self, key) -> validate.Issue:
        found, = self.nights[key]["report"].errors
        self.assertEqual(found.rule, "verb-mismatch")
        return found

    def test_the_detail_names_the_box_by_its_side_and_whom_it_gives_the_match(self):
        for key, detail in (
                ("LOSES TO", "verb 'LOSES TO' says away; the away Match Forfeited "
                             "box gives the match to home"),
                ("DRAWS WITH", "verb 'DRAWS WITH' says draw; the away Match "
                               "Forfeited box gives the match to home"),
                ("DEFEATS", "verb 'DEFEATS' says home; the home Match Forfeited "
                            "box gives the match to away")):
            with self.subTest(night=key):
                self.assertEqual(self.issue(key).detail, detail)

    def test_the_message_still_names_only_the_side_that_forfeited(self):
        issue = self.issue("LOSES TO")
        self.assertEqual(issue.message, f"{issue.subject}: the verb 'LOSES TO' means "
                                        "away won, but the card says away forfeited "
                                        "the match.")

    def test_the_box_is_named_in_the_words_every_check_held_to_it_uses(self):
        # A spy rather than a card: a second wording that agrees today cannot be
        # told apart from the one by any card.
        self.assertIs(validate._box, scorecard._box)
        with mock.patch.object(validate, "_box", return_value="THE BOX") as named:
            report = self.validate(self.nights["LOSES TO"]["given"])
        self.assertIn(mock.call("away"), named.call_args_list)
        self.assertEqual(report.errors[0].detail,
                         "verb 'LOSES TO' says away; THE BOX gives the match to home")

    def test_the_build_prints_the_detail_within_the_reports_width(self):
        done = subprocess.run((sys.executable, str(self.BUILD), "--check"),
                              cwd=self.nights["LOSES TO"]["root"], capture_output=True,
                              text=True, timeout=600)
        words = " ".join(done.stdout.split())
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("verb 'LOSES TO' says away; the away Match Forfeited box gives "
                      "the match to home", words)
        self.assertNotIn("tick says", words)
        self.assertLessEqual(max(map(len, done.stdout.splitlines())), 78)


class TestATotalRowTheRowsDoNotAddToIsReportedOnceByTotalsMismatch(OneNightCase):
    """A wrong TOTAL row is one ``totals-mismatch``, and a forfeit may total its award.

    Row 4.135. The reader's totals check holds the printed TOTAL row to the set
    rows and F6 makes its disagreement an error; ``totals-mismatch`` holds the
    same row to the same rows, cell by printed cell, and reports it again.
    Measured with /tmp/t7/scratch/V2/probe/verbs.py and more.py on the tree
    before this: a played card with rows 6-2, 6-3, 6-4, 6-1 and a TOTAL row
    printed 4-0 20-10 got F6 on its totals and ``totals-mismatch``, and so did a
    whole-match forfeit with Match Forfeited ticked against the away side, four
    0-0 rows and a TOTAL row of 4-0 24-0, which totals the forfeit as a love set
    in every rubber to the side that came. So did that forfeit totalling its sets
    alone, 4-0, or its games alone, 24-0.

    ``totals-mismatch`` stays: it names the cells and holds them to the rows
    under the club's set rule, which the reader cannot, and every TOTAL row the
    reader's check faults on a card in play it faults as well. So F6 asks
    ``_check_totals`` where it reports, and there passes by the totals check, as
    it asks ``_check_verb`` (4.131). The reader names no report. Validate holds
    no card its Match Cancelled box calls off to its rows, and on a washout with
    a wrong TOTAL row (/tmp/t7/scratch/V2/washout/probe.py) a reader's
    ``reported_by="totals-mismatch"`` named a report that never came, which the
    doctor printed beside "it reports the match once, as cancelled".

    A forfeit whose rows give no result is scored on its box, as its verb and
    tick are held to it (4.33), so its TOTAL row agrees if it totals the rows or
    totals what the box awards: every rubber of the format to the side that
    came, each a love set, as ``MatchFormat.forfeit_worth`` prices it, 4-0 and
    24-0 over four rubbers first to 6. A love set is 6-0 with a set tiebreak or
    without, since the tiebreak is only played at 6-all. Those sets are what
    ``rubbers`` pays such a forfeit; the games are paid by no scheme a config can
    name, the ladder taking a forfeit's games from its rows. validate holds the
    row to the award under the club's set rule. The reader cannot see that rule,
    so its check agrees with a row totalling the award under any set rule and
    says which, and leaves a row totalling another rule's award, 24-0 under
    fast4, to validate; a row totalling neither the rows nor any award
    disagrees, and keeps what the rows alone fault it for. So on a card of the
    format's shape, a forfeit's row the reader faults ``totals-mismatch`` faults
    under whatever set rule the club plays, and a TOTAL row that is neither is
    one ``totals-mismatch``, which names the cells it differs from the box on.

    Rejected: holding a forfeit's TOTAL row to its rows alone, which reports
    every card that totals the award as an error; comparing each printed cell
    with either reading, which would pass a row of sets 4-0 and games 0-0, one
    account of the night in one cell and another in the next; the reader holding
    the row to what it gives the side that forfeited alone, which agreed with
    9-0 99-0, 4-0 20-0 and 4-0 0-0 and threw away the rows' own faults, so the
    doctor counted nothing disagreeing on cards build.py refused; pricing a set
    under six-game-tiebreak at the 7 games a side can win in one, 28-0 over
    four rubbers, where a forfeit's set is 6-0; and the reader naming
    ``totals-mismatch``, which on a washout names a report never made.
    The reader could only tell a washout by the rule
    :func:`~matchcentre.discover.called_off` keeps, which asks a whole card,
    and the doctor's own reading builds none.
    """

    TO_HOME = ((6, 2), (6, 3), (6, 4), (6, 1))
    EMPTY = ((0, 0),) * 4
    A_RUBBER_EACH = ((6, 4), (4, 6), (0, 0), (0, 0))
    TIEBREAK = '[rules]\nsets = "six-game-tiebreak"\n'
    FAST4 = '[rules]\nsets = "fast4"\n'
    PRO8 = '[rules]\nsets = "pro-8"\n'
    RUBBERS = '[rules]\nscoring = "rubbers"\n'

    @classmethod
    def cases(cls):
        played = dict(scores=cls.TO_HOME)
        away = dict(scores=cls.EMPTY, forfeited_by="away")
        home = dict(scores=cls.EMPTY, forfeited_by="home", verb="LOSES TO",
                    winner="away")
        return {
            "played, games wrong": dict(played, totals=(4, 20, 10, 0)),
            "washout, games wrong": dict(played, totals=(4, 20, 10, 0),
                                         cancelled=True),
            "read empty, the award": dict(scores=cls.EMPTY, totals=(4, 24, 0, 0)),
            "read empty, the award, a verb for home": dict(
                scores=cls.EMPTY, totals=(4, 24, 0, 0), verb="DEFEATS",
                winner="home"),
            "played, games alone wrong": dict(played, totals=("", 20, 10, "")),
            "played, sets alone wrong": dict(played, totals=(3, "", "", 0)),
            "forfeit, the award": dict(away, totals=(4, 24, 0, 0)),
            "forfeit, the award's sets": dict(away, totals=(4, "", "", 0)),
            "forfeit, the award's games": dict(away, totals=("", 24, 0, "")),
            "forfeit, the rows": dict(away, totals=(0, 0, 0, 0)),
            "home forfeit, the award": dict(home, totals=(0, 0, 24, 4)),
            "forfeit a rubber each, the award": dict(
                away, scores=cls.A_RUBBER_EACH, totals=(4, 24, 0, 0)),
            "forfeit, games short": dict(away, totals=(4, 20, 0, 0)),
            "forfeit, the award's sets and the rows' games": dict(
                away, totals=(4, 0, 0, 0)),
            "forfeit, the award to the forfeiter": dict(away, totals=(0, 0, 24, 4)),
            "forfeit with rows, the award": dict(
                away, scores=cls.TO_HOME, totals=(4, 24, 0, 0)),
            "tiebreak forfeit, the award": dict(
                away, totals=(4, 24, 0, 0), config=cls.TIEBREAK),
            "tiebreak forfeit, four 7-0 sets": dict(
                away, totals=(4, 28, 0, 0), config=cls.TIEBREAK),
            "fast4 forfeit, first-to-6's award": dict(
                away, totals=(4, 24, 0, 0), config=cls.FAST4),
            "pro-8 forfeit, the award": dict(
                away, totals=(4, 32, 0, 0), config=cls.PRO8),
            "forfeit, the award and games to the forfeiter": dict(
                away, totals=(4, 24, 3, 0)),
            "forfeit, the award, the forfeiter ticked": dict(
                away, totals=(4, 24, 0, 0), winner="away"),
            "forfeit, sets over the rows": dict(away, totals=(9, 99, 0, 0)),
        }

    def message(self, key) -> tuple[str, str]:
        issue, = self.nights[key]["report"].errors
        return issue.message.split(": ", 1)[1], issue.detail

    def test_a_played_cards_wrong_total_is_one_totals_mismatch(self):
        for key, cells in (("played, games wrong", "home games"),
                           ("played, games alone wrong", "home games"),
                           ("played, sets alone wrong", "home sets"),
                           ("read empty, the award", "home sets and home games")):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key),
                                 [("totals-mismatch", validate.ERROR)])
                self.assertEqual(self.message(key)[0],
                                 f"the printed TOTAL row and the set rows disagree "
                                 f"on {cells}.")
        # A cell the card leaves blank is said to be, not printed as a value.
        self.assertEqual(self.message("played, sets alone wrong")[1],
                         "printed sets 3-0 games blank; set rows add to sets 4-0 "
                         "games 24-10")
        for key in ("played, games wrong", "played, games alone wrong",
                    "read empty, the award"):
            with self.subTest(night=key):
                # The reader still says what it read, and names no report.
                check = self.check(key, "totals")
                self.assertEqual((check.agrees, check.reported_by), (False, ""))

    def test_a_forfeit_totalling_its_award_or_its_rows_is_reported_by_nothing(self):
        for key in ("forfeit, the award", "forfeit, the award's sets",
                    "forfeit, the award's games", "forfeit, the rows",
                    "home forfeit, the award", "forfeit a rubber each, the award",
                    "tiebreak forfeit, the award", "pro-8 forfeit, the award"):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key), [])
                check = self.check(key, "totals")
                self.assertEqual((check.available, check.agrees, check.reported_by),
                                 (True, True, ""))

    def test_the_reader_holds_a_forfeits_total_to_its_award_under_any_set_rule(self):
        for key, side, under in (
                ("forfeit, the award", "home",
                 "first-to-6, advantage-6 or six-game-tiebreak"),
                ("home forfeit, the award", "away",
                 "first-to-6, advantage-6 or six-game-tiebreak"),
                ("pro-8 forfeit, the award", "home", "pro-8 or pro-8-tiebreak"),
                ("forfeit, the award's sets", "home", "any set rule"),
                ("fast4 forfeit, first-to-6's award", "home",
                 "first-to-6, advantage-6 or six-game-tiebreak")):
            with self.subTest(night=key):
                check = self.check(key, "totals")
                self.assertTrue(check.agrees)
                self.assertTrue(check.detail.endswith(
                    "so the printed TOTAL row is held to the box rather than to "
                    f"the rows, and totals its award under {under}: every rubber "
                    f"to {side}, each a love set"), check.detail)

    def test_the_reader_keeps_the_rows_faults_of_a_forfeit_total_that_is_no_award(self):
        for key in ("forfeit, games short",
                    "forfeit, the award's sets and the rows' games",
                    "forfeit, the award to the forfeiter",
                    "forfeit, the award and games to the forfeiter",
                    "tiebreak forfeit, four 7-0 sets",
                    "forfeit, sets over the rows"):
            with self.subTest(night=key):
                check = self.check(key, "totals")
                self.assertEqual((check.agrees, check.reported_by), (False, ""))
                self.assertTrue(check.detail.endswith(
                    "; nor does it total what the away Match Forfeited box awards "
                    "under any set rule, every rubber to home as a love set"),
                    check.detail)
        self.assertIn("; 9 + 0 sets printed over 4 rows; ",
                      self.check("forfeit, sets over the rows", "totals").detail)
        self.assertIn("away games 3 printed against 0 in the rows; ",
                      self.check("forfeit, the award and games to the forfeiter",
                                 "totals").detail)

    def test_a_forfeit_total_that_is_neither_is_one_totals_mismatch(self):
        for key, cells in (
                ("forfeit, games short", "home games"),
                ("forfeit, the award's sets and the rows' games", "home games"),
                ("forfeit, the award to the forfeiter",
                 "home sets, home games, away games and away sets"),
                ("forfeit, the award and games to the forfeiter", "away games"),
                ("forfeit, sets over the rows", "home sets and home games"),
                ("tiebreak forfeit, four 7-0 sets", "home games"),
                ("fast4 forfeit, first-to-6's award", "home games")):
            with self.subTest(night=key):
                self.assertEqual(self.errors(key),
                                 [("totals-mismatch", validate.ERROR)])
                self.assertEqual(self.message(key)[0],
                                 f"the printed TOTAL row and the away Match Forfeited "
                                 f"box disagree on {cells}.")
        self.assertEqual(self.message("forfeit, games short")[1],
                         "printed sets 4-0 games 20-0; a TOTAL row may total the "
                         "forfeit, every rubber to home as a love set, sets 4-0 "
                         "games 24-0, or the set rows, sets 0-0 games 0-0")
        for key, award in (("tiebreak forfeit, four 7-0 sets", "sets 4-0 games 24-0"),
                           ("fast4 forfeit, first-to-6's award",
                            "sets 4-0 games 16-0")):
            with self.subTest(night=key):
                self.assertIn(f"as a love set, {award}, or the set rows",
                              self.message(key)[1])

    def test_a_forfeit_ticked_for_the_side_that_forfeited_is_its_ticks_to_report(self):
        # The award goes to the side the box says came, not to the tick.
        self.assertEqual(self.errors("forfeit, the award, the forfeiter ticked"),
                         [("F6", "winner-tick")])

    def test_a_total_row_says_which_cells_it_leaves_blank(self):
        # What the reports print a TOTAL row as, and which cells they compare.
        printed = card_mod.Totals(home_sets=3, home_games=20, away_sets=0)
        self.assertEqual(printed.describe(), "sets 3-0 games 20-blank")
        self.assertEqual(card_mod.Totals(away_games=5).describe(),
                         "sets blank games blank-5")
        self.assertEqual(printed.differs_on(card_mod.Totals(3, 24, 0, 0)),
                         ["home games"])

    def test_a_forfeit_whose_rows_give_a_result_is_held_to_its_rows(self):
        key = "forfeit with rows, the award"
        self.assertEqual(self.errors(key), [("totals-mismatch", validate.ERROR)])
        self.assertEqual(self.message(key)[0], "the printed TOTAL row and the set "
                                               "rows disagree on away games.")
        self.assertFalse(self.check(key, "totals").agrees)

    def test_a_wrong_total_stands_down_the_totals_check_alone(self):
        # Read empty, the card counts for nothing, so its verb and its tick are
        # F6's to report (4.131); its TOTAL row is still totals-mismatch's.
        self.assertEqual(self.errors("read empty, the award, a verb for home"),
                         [("F6", "verb"), ("F6", "winner-tick"),
                          ("totals-mismatch", validate.ERROR)])

    def test_the_award_is_the_one_the_ladder_pays_a_forfeit_from(self):
        # A spy rather than a card: a second pricing of the award agrees with the
        # first on every format until the day one of them changes. Three sets over
        # four rubbers is no format's, so a pricing that counts the rubbers
        # itself fails on the sets as one pricing its own games fails on them.
        with mock.patch.object(fmt_mod.MatchFormat, "forfeit_worth",
                               new_callable=mock.PropertyMock,
                               return_value=(3, 20)):
            night = self.night(100, scores=self.EMPTY, forfeited_by="away",
                               totals=(3, 20, 0, 0), config=self.RUBBERS)
            match, = night["given"]["matches"]
            fmt = night["given"]["comp"].fmt
            rubbers = match.points_for(self.HOME)
            full = dataclasses.replace(scoring.ruleset("games-bonus"),
                                       forfeit_award="full-games", set_bonus=0.5)
            priced = full.forfeit_win_points(fmt)
        self.assertEqual(night["said"], [])
        self.assertTrue(next(c for c in night["card"].checks
                             if c.name == "totals").agrees)
        self.assertEqual(rubbers, 3.0)
        self.assertAlmostEqual(priced, 4.0 + 0.1 * 20 + 0.5 * 3)

    def test_on_every_night_a_wrong_total_is_reported_once(self):
        stood_down = called_off = 0
        for key, night in self.nights.items():
            check, said = self.check(key, "totals"), night["said"]
            with self.subTest(night=key):
                self.assertEqual(check.reported_by, "")
                self.assertNotIn(("F6", "totals"), said)
                if check.agrees:
                    continue
                if discover_mod.called_off(night["card"]):
                    # Held to nothing it prints, by F6 or by totals-mismatch.
                    called_off += 1
                    self.assertEqual(self.errors(key), [])
                else:
                    stood_down += 1
                    self.assertIn(("totals-mismatch", validate.ERROR), said)
        # The premise: the reader disagreed on TOTAL rows totals-mismatch reports,
        # and on one it holds to nothing, so neither half is vacuous.
        self.assertEqual((stood_down, called_off), (11, 1))

    def test_f6_asks_the_totals_check_where_it_reports(self):
        # A spy rather than a card: F6 keeping its own account of which cards
        # totals-mismatch reports would agree with it until the day one changed.
        key = "played, games wrong"
        with mock.patch.object(validate, "_check_totals", return_value=[]):
            report = self.validate(self.nights[key]["given"])
        self.assertEqual(self.said_of(report), [("F6", "totals")])

    def test_the_build_passes_a_forfeit_totalling_its_award(self):
        code, words = self.build("forfeit, the award")
        self.assertEqual(code, 0, words)
        self.assertIn("0 error(s)", words)

    def test_the_build_reports_a_wrong_total_once(self):
        code, words = self.build("played, games wrong")
        self.assertEqual(code, 1, words)
        self.assertIn("1 error(s)", words)
        self.assertIn("the printed TOTAL row and the set rows disagree on home "
                      "games.", words)
        self.assertNotIn("cross-check disagrees", words)

    def test_the_doctor_names_no_report_on_a_washouts_wrong_total(self):
        done = subprocess.run(
            (sys.executable, str(ROOT / "tools" / "doctor.py"),
             str(self.nights["washout, games wrong"]["path"])),
            capture_output=True, text=True, timeout=120)
        self.assertEqual(done.stderr, "")
        self.assertIn("cross-check totals: DISAGREES - ", done.stdout)
        self.assertIn("it reports the match once, as cancelled.",
                      " ".join(done.stdout.split()))
        self.assertNotIn("reported by", done.stdout)

    def test_the_doctor_says_a_forfeits_total_of_its_award_agrees(self):
        done = subprocess.run(
            (sys.executable, str(ROOT / "tools" / "doctor.py"),
             str(self.nights["forfeit, the award"]["path"])),
            capture_output=True, text=True, timeout=120)
        self.assertEqual(done.stderr, "")
        self.assertIn("cross-check totals: agrees - ", done.stdout)
        self.assertIn("cross-checks  3 available, 0 disagreeing\n", done.stdout)

    def test_the_doctor_says_a_forfeits_wrong_total_disagrees(self):
        for key in ("forfeit, sets over the rows", "forfeit, games short"):
            with self.subTest(night=key):
                done = subprocess.run(
                    (sys.executable, str(ROOT / "tools" / "doctor.py"),
                     str(self.nights[key]["path"])),
                    capture_output=True, text=True, timeout=120)
                self.assertEqual(done.stderr, "")
                self.assertIn("cross-check totals: DISAGREES - ", done.stdout)
                self.assertIn("cross-checks  3 available, 1 disagreeing\n",
                              done.stdout)
                self.assertIn("build.py reports this as an error rather than "
                              "scoring it.", " ".join(done.stdout.split()))


class TestTheVerbsRealCardsPrintAreReadAsTheyWere(unittest.TestCase):
    """``DEFEATS``, ``DEF. BY`` and ``VS`` read as they did before one verb table.

    One club's real season of 22 cards, counted by the maintainer, prints three
    verbs between the team names and no others: ``DEFEATS`` on 9, ``DEF. BY`` on
    10 and ``VS`` on 3. Row 4.137 made one table of the reader's marks and
    validate's whole verbs, and the words it dropped or added are words no real
    card was measured to print. These three are the ones that must not move, so
    each is built here on a played card and on a whole-match forfeit whose rows
    give no result, and what the reader's verb check and validate say about it is
    pinned: ``DEFEATS`` a win for home, ``DEF. BY`` for away, and ``VS`` no result
    at all, which both ask one separator rule of. A card printing ``VS:`` was
    measured, by /tmp/t7/scratch/V1/probe/sepval.py on the tree before, to be
    ``verb-absent`` to validate and a word the reader did not recognise.

    The tests of the three verbs pass on the tree before 4.137, as they must, and
    are earned by breaking the table they are read through; only the separator's,
    of ``VS:`` and ``(VS)``, fails there.
    """

    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    TO_HOME = ((6, 2), (6, 3), (6, 4), (6, 1))
    TO_AWAY = ((2, 6), (3, 6), (4, 6), (1, 6))
    EMPTY = ((0, 0),) * 4

    def said(self, verb: str, scores, **fields):
        """The reader's verb check, and validate's findings about the verb."""
        _, rockets = TEAMS[self.HOME]
        _, aces = TEAMS[self.AWAY]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            folder = data / f"Round{SHORT_ROUND}"
            folder.mkdir(parents=True)
            cw.write_card_file(folder / "card.pdf", short_spec(
                self.HOME, self.AWAY, court="10", match_id="4000010",
                rows=rows_for(scores, rockets, aces), verb=verb, **fields))
            cards, rejected = scorecard.read_tree(data)
            found = discover_mod.discover(
                cards, rejected, config=config_mod.Config(path=root / "config.toml"),
                root=data)
            comp_id, = sorted(found.matches)
            competition = next(c for c in found.club.competitions if c.id == comp_id)
            report = validate.validate(found.matches[comp_id], competition,
                                       club=found.club, cards=cards,
                                       rejected=rejected)
        card, = cards
        self.assertEqual(card.verb, verb)
        check, = [c for c in card.checks if c.name == "verb"]
        state = ("n/a" if not check.available else "agrees" if check.agrees
                 else f"reported by {check.reported_by}" if check.reported_by
                 else "DISAGREES")
        return state, check.detail, [
            (i.rule, i.message.split(".pdf: ", 1)[1]) for i in report.issues
            if i.rule.startswith("verb")
            or (i.rule == "F6" and "verb cross-check" in i.message)]

    def test_defeats_is_a_win_for_home(self):
        self.assertEqual(self.said("DEFEATS", self.TO_HOME),
                         ("agrees", "'DEFEATS' agrees with the set rows", []))
        state, detail, issues = self.said("DEFEATS", self.TO_AWAY)
        self.assertEqual(state, "DISAGREES")
        self.assertIn("'DEFEATS', making home the winner, but", detail)
        self.assertIn(("verb-mismatch", "the verb 'DEFEATS' means home won, but "
                       "the set rows give away."), issues)

    def test_def_by_is_a_win_for_away(self):
        self.assertEqual(self.said("DEF. BY", self.TO_AWAY),
                         ("agrees", "'DEF. BY' agrees with the set rows", []))
        state, detail, issues = self.said("DEF. BY", self.TO_HOME)
        self.assertEqual(state, "DISAGREES")
        self.assertIn("'DEF. BY', making away the winner, but", detail)
        self.assertIn(("verb-mismatch", "the verb 'DEF. BY' means away won, but "
                       "the set rows give home."), issues)

    def test_vs_is_no_result_at_all(self):
        state, detail, issues = self.said("VS", self.TO_HOME, winner=cw.NO_TICK)
        self.assertEqual((state, detail), (
            "n/a", "the card prints 'VS' between the team names, which states no "
                   "result to check against"))
        self.assertEqual([rule for rule, _ in issues], ["verb-absent"])

    def test_on_a_forfeit_each_restates_the_box_or_is_verb_mismatchs(self):
        for verb, box, state in (("DEFEATS", "away", "agrees"),
                                 ("DEF. BY", "home", "agrees"),
                                 ("DEF. BY", "away", "reported by verb-mismatch"),
                                 ("DEFEATS", "home", "reported by verb-mismatch")):
            with self.subTest(verb=verb, box=box):
                got, _, issues = self.said(verb, self.EMPTY, forfeited_by=box)
                self.assertEqual(got, state)
                side = "home" if verb == "DEFEATS" else "away"
                self.assertEqual(issues, [] if state == "agrees" else [
                    ("verb-mismatch", f"the verb {verb!r} means {side} won, but "
                                      f"the card says {box} forfeited the match.")])
        got, _, issues = self.said("VS", self.EMPTY, forfeited_by="away",
                                   winner=cw.NO_TICK)
        self.assertEqual((got, [rule for rule, _ in issues]), ("n/a", ["verb-absent"]))

    def test_a_separator_is_the_same_separator_to_the_reader_and_to_validate(self):
        for verb in ("VS:", "(VS)", "-V-"):
            with self.subTest(verb=verb):
                state, detail, issues = self.said(verb, self.TO_HOME,
                                                  winner=cw.NO_TICK)
                self.assertEqual(state, "n/a")
                self.assertIn("which states no result to check against", detail)
                self.assertEqual([rule for rule, _ in issues], ["verb-absent"])


class TestTheSeasonSaysWhoWonEachRubberSoNoPageHasToWorkItOut(SeasonCase):
    """Who won each rubber is in the season document, for every rubber on every card.

    The matches page now puts every rubber of the season in one table, with a Winner
    column, and the page reads that column off the season rather than off the games.
    That is only safe if the document answers for every set, and answers where the
    games do not. Measured before the table was built: over the demo season's 60 sets
    and a seven-team season's 84, every set carried a `winner`, and comparing games
    would have disagreed with it once in each -- on a 5-4 rubber that was retired and
    went to nobody. The other disagreement the games cannot see is the rubber nobody
    came for, 0-0 and won.

    So one card with all of it, and both sides of it: a rubber forfeited by the away
    side and one forfeited by the home side, both 0-0, and a retired rubber the home
    side was ahead in. Working the winner out in the browser was the alternative and
    is the one this pins against -- a second copy of the forfeit rule, in a language
    this suite cannot run, deciding a result the cards already decided.

    These pass on the tree the table was built on: the document was already writing
    `winner` for every set, and nothing here changed it. What they add is the check at
    the document's level. The rule is decided in the model and pinned there, by
    TestSetResult and TestARubberNobodyCameForGoesToTheSideThatDid; but measured,
    the whole suite as it stood passed with `"winner": None` written for every set,
    so the one field the new column reads was a field nothing read back. They are
    earned by breaking the line that writes it, in the ways a page's answer would go
    wrong, rather than by failing first.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        pair_home, pair_away = home_players[:2], away_players[:2]
        rows = (
            cw.SetRow(6, 2, True, pair_home, pair_away),
            # Nobody came on the away side, then nobody on the home side.
            cw.SetRow(0, 0, False, (home_players[0],), (),
                      status=SetStatus.FORFEIT, status_side="away"),
            cw.SetRow(0, 0, False, (), (away_players[0],),
                      status=SetStatus.FORFEIT, status_side="home"),
            # Ahead when it stopped, which is not the same as having won it.
            cw.SetRow(5, 4, True, pair_home, pair_away,
                      status=SetStatus.RETIRED, status_side="away"),
        )
        cw.write_card_file(folder / "every-kind.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            home_name=home_name, away_name=away_name,
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)),
            court="10", match_id="4000012", rows=rows))

    def sets(self):
        match, = [m for m in self.season["matches"] if m["id"] == "4000012"]
        return {st["slot"]: st for st in match["sets"]}

    def test_every_rubber_in_the_document_says_who_won_it(self):
        # Every set of every match, because the page reads the field on every row and
        # a set without it would be a rubber the table gives to nobody by accident.
        for match in self.season["matches"]:
            for st in match["sets"]:
                with self.subTest(match=match["id"], slot=st["slot"]):
                    self.assertIn("winner", st,
                                  "a set in the season document does not say who "
                                  "won it, so the page has to guess")
                    self.assertIn(st["winner"], ("home", "away", None))

    def test_a_rubber_nobody_came_for_goes_to_the_side_that_did_on_either_side(self):
        sets = self.sets()
        self.assertEqual((sets["s1"]["home_games"], sets["s1"]["away_games"]), (0, 0))
        self.assertEqual(sets["s1"]["winner"], "home",
                         "a rubber the away side did not come for is not the home "
                         "side's in the document, and 0-0 gives it to nobody")
        self.assertEqual(sets["s2"]["winner"], "away",
                         "a rubber the home side did not come for is not the away "
                         "side's in the document")

    def test_a_rubber_stopped_before_it_finished_goes_to_nobody_whoever_was_ahead(self):
        d2 = self.sets()["d2"]
        self.assertEqual((d2["home_games"], d2["away_games"]), (5, 4))
        self.assertEqual(d2["status"], "retired")
        self.assertIsNone(d2["winner"],
                          "a retired rubber goes to the side that was ahead when it "
                          "stopped, which is a result the card does not give")
        # And the played rubber beside them is untouched, so the three above are not
        # passing because every rubber on the card went one way.
        self.assertEqual(self.sets()["d1"]["winner"], "home")


class TestALevelScorelineSaysWhatDecidedTheMatch(SeasonCase):
    """"Tied on games" is a fact about a contest, and 0-0 is not a contest.

    The page prints "tied on games, decided on sets" beside any match whose two
    games figures are equal, and the flag it reads was a bare ``home_games ==
    away_games``. Every match nobody played satisfies that, because an empty card
    is level at nothing: the demo season ships one such round, and the screenshot
    in the README shows a cancelled night described as a match that went to a
    tiebreak on sets.

    The sentence is also wrong on a preset that does not decide on games first.
    Under ``sets-first`` a card level on games was never *going* to be decided on
    games, so naming sets as the tiebreak describes a contest under rules nobody
    played it under. So the document now records which basis in the ruleset's own
    order separated the two sides, and the page says only what that says.

    Three cards, three cases: one played and level with a winner, one played and
    level with no winner, one nobody played at all.
    """

    # 18-18 on games, sets 3-1: level on the first basis, separated on the second.
    LEVEL_ROUND = "4"
    LEVEL_SCORES = ((6, 4), (6, 4), (6, 4), (0, 6))
    # Nothing entered at all, `Match Cancelled` ticked. The commonest washout.
    OFF_ROUND = "5"
    OFF_SCORES = ((0, 0), (0, 0), (0, 0), (0, 0))

    @classmethod
    def extra_cards(cls):
        cls.write("level.pdf", cls.LEVEL_ROUND, "2026-09-08", "riverside-rockets",
                  "hilltop-aces", cls.LEVEL_SCORES, cancelled=False)
        cls.write("rained-off.pdf", cls.OFF_ROUND, "2026-09-15",
                  "lakeside-spinners", "parkview-wanderers", cls.OFF_SCORES,
                  cancelled=True)

    @classmethod
    def write(cls, name, rnd, date, home, away, scores, *, cancelled):
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        folder = cls.data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        extra = ({"cancelled": True, "verb": "VS", "winner": cw.NO_TICK}
                 if cancelled else {})
        cw.write_card_file(folder / name, cw.CardSpec(
            date=date, round_label=rnd, division="B", court="09",
            match_id=f"500{rnd}001", home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)),
            **extra))

    def exported(self, rnd):
        found = [m for m in self.season["matches"] if m["round"] == rnd]
        self.assertEqual(len(found), 1)
        return found[0]

    # --- the card that really was level ------------------------------------

    def test_the_level_card_was_read_as_level_and_has_a_winner(self):
        # The pre-flight. If the generator's arithmetic were wrong the two
        # assertions below would pass by describing a match that never happened.
        row = self.exported(self.LEVEL_ROUND)
        self.assertEqual((row["home_games"], row["away_games"]), (18, 18))
        self.assertEqual((row["home_sets"], row["away_sets"]), (3, 1))
        self.assertEqual(row["result"], "home")

    def test_it_is_marked_tied_on_games_and_decided_on_sets(self):
        row = self.exported(self.LEVEL_ROUND)
        self.assertTrue(row["tied_on_games"])
        self.assertEqual(row["decided_by"], "sets")

    # --- the card nobody played --------------------------------------------

    def test_the_rained_off_card_is_not_described_as_level(self):
        # The whole bug in one assertion. 0-0 is not a tie; it is a night that did
        # not happen, and the row already says `cancelled`.
        row = self.exported(self.OFF_ROUND)
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual((row["home_games"], row["away_games"]), (0, 0))
        self.assertFalse(row["tied_on_games"],
                         "a match nobody played is not tied on games")

    def test_nothing_decided_the_match_nobody_played(self):
        self.assertIsNone(self.exported(self.OFF_ROUND)["decided_by"])

    # --- and the genuine draw already in the season -------------------------

    def test_a_real_draw_is_level_with_nothing_deciding_it(self):
        # Round 2, aces v wanderers: 19-19 and sets 2-2. Level on every basis the
        # ruleset has, which is what a draw *is* -- so "decided on sets" must not
        # appear beside it either.
        drawn = [m for m in self.season["matches"] if m["result"] == "draw"]
        self.assertEqual(len(drawn), 1)
        self.assertTrue(drawn[0]["tied_on_games"])
        self.assertIsNone(drawn[0]["decided_by"])

    def test_every_match_that_names_a_basis_really_was_separated_by_it(self):
        # The invariant behind the sentence, over the whole season: whichever basis
        # is named, the two figures for it differ, and every basis the ruleset
        # would have consulted first is level.
        order = self.competition.rules.decide_by
        for row in self.season["matches"]:
            basis = row["decided_by"]
            if basis is None:
                continue
            with self.subTest(match=row["id"]):
                for earlier in order[:order.index(basis)]:
                    self.assertEqual(row[f"home_{earlier}"],
                                     row[f"away_{earlier}"])
                self.assertNotEqual(row[f"home_{basis}"], row[f"away_{basis}"])


class TestAGradeWhoseNightsCannotEndLevel(SeasonCase):
    """The same six cards, read under ``[rules] draws = false``.

    Round 2's aces v wanderers is 19-19 and sets 2-2 -- a genuine draw under the
    preset, and under this setting a result nobody recorded: the association decides
    every match, so either a tiebreak went unwritten or a row was misread. F5 says
    report it and count nothing, and that is what is checked here, figure by figure.

    Before the setting existed, this combination was unreachable, and reaching it by
    hand did not report anything: ``Ruleset.base_points`` raised, ``build_season``
    let the exception out, and one level card took the whole dashboard down. With a
    fail-closed ``points_for`` and no exclusion, the alternative was worse than the
    crash -- the ladder read ``played 1, drawn 1, points 0`` for a competition with
    no D in it.
    """

    LEVEL_ROUND = "2"
    HOME, AWAY = "hilltop-aces", "parkview-wanderers"

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml", draws=False)

    def row(self):
        found = [m for m in self.season["matches"]
                 if m["round"] == self.LEVEL_ROUND and m["home"] == self.HOME]
        self.assertEqual(len(found), 1)
        return found[0]

    def team(self, slug):
        return next(t for t in self.season["teams"] if t["slug"] == slug)

    def test_one_level_card_does_not_take_the_dashboard_down_with_it(self):
        # The whole document, not just this fixture's row: the failure this replaces
        # was a ScoringError out of build_season, so nothing was written at all.
        self.assertEqual(len(self.season["teams"]), len(TEAMS))
        self.assertEqual(len(self.season["matches"]), len(FIXTURES))

    def test_the_setting_reached_the_scheme_the_matches_were_scored_under(self):
        self.assertFalse(self.competition.rules.allows_draw)
        self.assertFalse(self.season["meta"]["rules"]["allows_draw"])

    def test_the_night_is_on_the_page_and_in_no_figure_above_it(self):
        row = self.row()
        self.assertFalse(row["counts_for_ladder"])
        self.assertEqual(row["not_counted"], "level")

    def test_the_card_earns_neither_side_points_of_its_own(self):
        row = self.row()
        self.assertIsNone(row["home_points"])
        self.assertIsNone(row["away_points"])

    def test_neither_side_is_credited_a_draw_this_grade_cannot_have(self):
        for slug in (self.HOME, self.AWAY):
            with self.subTest(team=slug):
                self.assertEqual(self.team(slug)["drawn"], 0)
                self.assertEqual(self.team(slug)["played"], 2)

    def test_the_two_teams_score_what_their_other_two_nights_paid(self):
        # On paper, games-bonus, the level night excluded:
        #   aces       loss 6 -> 2+0.6=2.6 | loss 12 -> 2+1.2=3.2  = 5.8
        #   wanderers  loss 15 -> 2+1.5=3.5 | loss 7 -> 2+0.7=2.7  = 6.2
        self.assertAlmostEqual(self.team(self.HOME)["points"], 5.8, places=6)
        self.assertAlmostEqual(self.team(self.AWAY)["points"], 6.2, places=6)

    def test_the_games_from_that_night_are_out_of_the_ladder_as_well(self):
        # 19 each, and a ladder that kept the games while dropping the points would
        # have a games column no match list on the page adds up to.
        self.assertEqual(self.team(self.HOME)["games_won"],
                         EXPECTED_GAMES_FOR[self.HOME] - 19)
        self.assertEqual(self.team(self.AWAY)["games_won"],
                         EXPECTED_GAMES_FOR[self.AWAY] - 19)

    def test_the_share_column_divides_by_the_total_the_ladder_kept(self):
        """The Share column is a share of the Games column beside it, not of a third
        figure only this column has.

        Item 3.19 put the shortfall on the team's row and printed it in the caption of
        the table it is short in -- "9 of the 256 games the team won belong to no row
        above" -- which makes the total in that sentence a claim about the Games column
        two tables up the same page. The walk's scope was the regular season, and the
        ladder's is the regular season minus what it cannot price, so on this season the
        caption would have said "of the 37" under a row reading 18: a column announced
        as 205% of the team's games.

        So the walk takes the ladder's list, and both halves of the subtraction move
        together -- the level night is out of the numerator as well, or the column
        would be over a hundred with nothing wrong. The night stays in every player's
        own record, which is the same division of labour a final has (F11) and the one
        `priceable` was written for: Dev Raman keeps the 13 games of it, and no share
        of the team's total is worked out from them.
        """
        for slug in TEAMS:
            with self.subTest(team=slug):
                team = self.team(slug)
                self.assertEqual(team["contribution_gap"]["team_games_won"],
                                 team["games_won"])
        # And the figure itself, not only the agreement: 37 games across three cards,
        # 18 across the two the ladder kept.
        self.assertEqual(self.team(self.HOME)["contribution_gap"]["team_games_won"], 18)
        # Dev played every rubber of the aces' three cards: 6 of their games in round 1
        # and 12 in round 3, shared with a doubles partner in the two doubles rubbers
        # of each -- 0.5 + 2 + 1.5 and 1 + 6 + 1.5, so 12.5 of the 18. His own record
        # keeps all three nights, and 13 of the 30 games it counts are the level one's.
        dev, = [p for p in self.season["players"] if p["slug"] == "dev-raman"]
        self.assertEqual(dev["games_won"], 30)
        self.assertEqual(dev["contribution"], {"games_won": 12.5,
                                               "team_games_won": 18,
                                               "share_pct": 69.4})

    def test_and_nothing_of_that_night_is_left_over_for_nobody_to_hold(self):
        # The shortfall is what the column does not reach, and dropping a night from
        # one side of the subtraction is exactly how a page comes to report one that
        # is not there. Every name on these cards is registered to the team it played
        # for, so every team's column is whole.
        for slug in TEAMS:
            with self.subTest(team=slug):
                gap = self.team(slug)["contribution_gap"]
                self.assertEqual(gap["games"], 0.0)
                self.assertEqual(gap["share_pct"], 0.0)
                self.assertEqual(gap["nights_not_credited"], 0)

    def test_the_teams_that_were_not_in_it_are_untouched(self):
        for slug in ("riverside-rockets", "lakeside-spinners"):
            with self.subTest(team=slug):
                self.assertEqual(self.team(slug)["played"], 3)
                self.assertAlmostEqual(self.team(slug)["points"],
                                       EXPECTED_POINTS[slug], places=6)

    def test_the_report_names_the_card_and_says_what_is_wrong_with_it(self):
        issue, = [i for i in self.report.issues if i.rule == "draw-not-allowed"]
        self.assertEqual(issue.severity, validate.ERROR)
        self.assertIn("level", issue.message)
        self.assertIn(self.competition.rules.name, issue.message)
        self.assertTrue(issue.detail.endswith(".pdf"), issue.detail)

    def test_the_report_also_says_the_ladder_is_a_night_short(self):
        # Two teams on two counted matches and two on three. Said out loud, because
        # a ladder ordered on points average is comparable across unequal matches
        # and a club reading the points column is not comparing averages.
        short = [i for i in self.report.issues
                 if i.rule == "F10" and "counted" in i.message]
        self.assertTrue(short, [i.rule for i in self.report.issues])

    def test_the_players_keep_the_rubbers_they_turned_up_for(self):
        # The night is out of the ladder, not out of anybody's record: three cards,
        # three rubbers a card. The reasoning is F11's, which keeps a grand final on
        # a player's page and off the ladder.
        jo, = [p for p in self.season["players"] if p["slug"] == "jo-calder"]
        self.assertEqual(jo["matches"], 3)
        self.assertEqual(jo["sets_played"], 9)

    def test_the_grid_records_the_meeting_as_no_result_rather_than_a_draw(self):
        # The head-to-head grid is the one other place a D can appear. A cell
        # reading "1 draw" under a banner reading "no draw" is the page arguing
        # with itself, and `undecided` is the word the grid already has for a night
        # that reached no result.
        record = self.season["head_to_head"][stats.h2h_key(self.HOME, self.AWAY)]
        self.assertEqual(record["played"], 1)
        self.assertEqual(record["draws"], 0)
        self.assertEqual(record["undecided"], 1)
        self.assertEqual(record["wins_low"] + record["wins_high"], 0)

    def test_the_banner_says_the_grade_has_no_draw(self):
        # Two fields, because they answer different questions. `scoring_detail` says
        # the scheme has no value for a draw; `draws_detail` says what then becomes
        # of the card, which is the part a club has to act on.
        rules = self.season["meta"]["rules"]
        self.assertIn("no draw", rules["scoring_detail"])
        self.assertEqual(rules["draws"], "no")
        self.assertIn("ladder", rules["draws_detail"])


class TestALevelMatchTheLadderCannotUse(unittest.TestCase):
    """The same rule as the class above, at the seam where it is decided.

    Built by hand rather than from cards, because the cases that matter most are
    the ones a club's PDFs will not produce on demand: a scheme that pays for a week
    off *and* has no draw, a level card in a final, and a level card exported twice.
    """

    def competition(self, *, rules, finals=()):
        return Competition(
            id="tuesday-division-c", day="Tuesday", division="C",
            fmt=fmt_mod.match_format("doubles-singles-doubles"), rules=rules,
            teams={"rockets": Team(slug="rockets", name="ROCKETS"),
                   "aces": Team(slug="aces", name="ACES")},
            # Four rounds, two of which no test before the totals needed: the
            # invariant between the ladder's reasons and the totals' takes a list
            # holding every kind of row at once, and a duplicate, a conflict, a level
            # night and a final are four fixtures.
            rounds={"1": Round(number="1"),
                    "2": Round(number="2", is_finals="2" in finals),
                    "3": Round(number="3"),
                    "4": Round(number="4", is_finals="4" in finals)})

    def match(self, scores, *, rules, rnd="1", source="card.pdf"):
        fmt = fmt_mod.match_format("doubles-singles-doubles")
        sets = tuple(
            SetResult(slot=fmt.slots[i], home_games=home, away_games=away,
                      home_players=(f"h{i}",), away_players=(f"a{i}",),
                      rule=fmt.set_rule)
            for i, (home, away) in enumerate(scores))
        return Match(competition="tuesday-division-c", round=rnd, home="rockets",
                     away="aces", fmt=fmt, rules=rules, sets=sets,
                     source=Path(source))

    # 17-17 on games and 2-2 on sets: level on every basis the scheme has.
    LEVEL = ((6, 2), (2, 6), (6, 3), (3, 6))
    WON = ((6, 2), (6, 1), (2, 6), (6, 3))

    def no_draws(self, **kw):
        return dataclasses.replace(scoring.ruleset("games-bonus"), draw=None, **kw)

    def renamed(self, match, name, *, source="second.pdf"):
        """The same card with one rubber given to somebody else."""
        sets = tuple(dataclasses.replace(s, home_players=(name,)) if i == 1 else s
                     for i, s in enumerate(match.sets))
        return dataclasses.replace(match, sets=sets, source=Path(source))

    def test_the_predicate_is_about_the_grade_and_not_about_levelness(self):
        allowed = scoring.ruleset("games-bonus")
        self.assertFalse(self.match(self.LEVEL, rules=allowed).is_unscoreable_draw)
        self.assertTrue(self.match(self.LEVEL,
                                   rules=self.no_draws()).is_unscoreable_draw)
        # A match with a winner is not one, whatever the grade allows.
        self.assertFalse(self.match(self.WON, rules=self.no_draws())
                         .is_unscoreable_draw)

    def test_an_empty_card_is_not_a_level_result(self):
        # 0-0 is level on any comparison of two totals, and a washout is not a
        # result that went unrecorded -- it is a night nobody played. Calling it one
        # would put a data-problem sentence on every rained-off round.
        blank = self.match(((0, 0), (0, 0), (0, 0), (0, 0)), rules=self.no_draws())
        self.assertFalse(blank.decided)
        self.assertFalse(blank.is_unscoreable_draw)

    def test_the_match_earns_no_points_instead_of_stopping_the_run(self):
        level = self.match(self.LEVEL, rules=self.no_draws())
        self.assertIsNone(level.points_for("rockets"))
        self.assertIsNone(level.points_for("aces"))

    def test_the_scheme_still_refuses_to_price_a_draw_it_has_no_price_for(self):
        # The fail-closed core stays: `points_for` declines to ask, and a caller
        # that asks anyway is a bug rather than a club's data problem.
        with self.assertRaises(scoring.ScoringError):
            self.no_draws().base_points("draw")

    def test_the_ladder_set_drops_it_and_every_other_set_keeps_it(self):
        rules = self.no_draws()
        comp = self.competition(rules=rules)
        matches = [self.match(self.LEVEL, rules=rules),
                   self.match(self.WON, rules=rules, rnd="2")]
        self.assertEqual(len(stats.scoreable(matches)), 2)
        self.assertEqual(len(stats.regular_season(matches, comp)), 2)
        kept = stats.ladder_matches(matches, comp)
        self.assertEqual([m.round for m in kept], ["2"])

    def test_it_is_not_paid_for_as_a_week_off_under_a_scheme_that_pays(self):
        # The sharp edge of a fail-closed `points_for`: `None` already means "this
        # match produced no points of its own", and under `unplayed = "average"`
        # that earns the team its own average. A level night credited the team's
        # average is worse than the crash it replaced -- points for a match the
        # ladder says was never counted.
        rules = self.no_draws(average_unplayed=True)
        comp = self.competition(rules=rules)
        matches = [self.match(self.LEVEL, rules=rules),
                   self.match(self.WON, rules=rules, rnd="2")]
        ledger = stats.points_ledger(stats.ladder_matches(matches, comp), "rockets")
        self.assertEqual(ledger.averaged_matches, 0)
        self.assertEqual(ledger.decided, 1)

    def test_the_page_is_told_which_of_the_reasons_this_one_is(self):
        rules = self.no_draws()
        comp = self.competition(rules=rules)
        matches = [self.match(self.LEVEL, rules=rules),
                   self.match(self.WON, rules=rules, rnd="2")]
        self.assertEqual(stats.not_counted_reasons(matches, comp),
                         [stats.NOT_COUNTED_LEVEL, ""])

    def test_a_level_card_in_a_final_is_named_as_the_thing_to_go_and_fix(self):
        # Precedence, on the one match where both apply. A final left out of the
        # ladder is the rules working and needs no action; a result nobody recorded
        # is somebody's afternoon, and it is the sentence worth printing.
        rules = self.no_draws()
        comp = self.competition(rules=rules, finals=("2",))
        matches = [self.match(self.LEVEL, rules=rules, rnd="2")]
        self.assertEqual(stats.not_counted_reasons(matches, comp),
                         [stats.NOT_COUNTED_LEVEL])

    def test_a_repeated_card_is_a_repeat_first_and_a_level_card_second(self):
        # The other way round, for the same reason the duplicate reason wins over
        # finals: which of two cards to believe has to be settled before anything
        # said about the result means much.
        rules = self.no_draws()
        comp = self.competition(rules=rules)
        matches = [self.match(self.LEVEL, rules=rules, source="a.pdf"),
                   self.match(self.LEVEL, rules=rules, source="b.pdf")]
        self.assertEqual(stats.not_counted_reasons(matches, comp),
                         [stats.NOT_COUNTED_LEVEL, stats.NOT_COUNTED_DUPLICATE])

    def test_the_ladder_and_the_players_are_asked_separately(self):
        # Two questions with two answers, and a card where they differ: a level
        # night in a grade with no draw is in none of the ladder's figures and was
        # played by people, who are credited for it as usual.
        rules = self.no_draws()
        comp = self.competition(rules=rules)
        matches = [self.match(self.LEVEL, rules=rules),
                   self.match(self.WON, rules=rules, rnd="2")]
        self.assertEqual(stats.not_counted_reasons(matches, comp),
                         [stats.NOT_COUNTED_LEVEL, ""])
        self.assertEqual(stats.not_credited_reasons(matches), ["", ""])

    def test_a_card_exported_twice_credits_nobody_the_second_time(self):
        # The question a page has to ask before listing somebody's sets. Both cards
        # name the same people, so reading the pair of them credits every one of
        # them twice for a night they played once.
        rules = self.no_draws()
        matches = [self.match(self.WON, rules=rules, source="a.pdf"),
                   self.match(self.WON, rules=rules, source="b.pdf")]
        self.assertEqual(stats.not_credited_reasons(matches),
                         ["", stats.NOT_COUNTED_DUPLICATE])

    def test_two_cards_that_swap_two_rubbers_do_not_agree_about_the_result(self):
        # The edge of "agree about the result", which is not "agree about the two
        # totals": these two cards both read 17-17 and 2-2, from different rubbers.
        # A pair of cards that disagree about who won the first singles disagree
        # about the night, and calling that a naming dispute would count a result
        # from the card read first while telling the club the numbers were settled.
        rules = self.no_draws()
        comp = self.competition(rules=rules)
        swapped = ((2, 6), (6, 2), (6, 3), (3, 6))       # rubbers 1 and 2 exchanged
        pair = [self.match(self.LEVEL, rules=rules, source="a.pdf"),
                self.match(swapped, rules=rules, source="b.pdf")]
        self.assertEqual(pair[0].home_games, pair[1].home_games)
        self.assertEqual(pair[0].home_sets, pair[1].home_sets)
        self.assertFalse(stats.same_scoreline(*pair))
        self.assertEqual(stats.not_counted_reasons(pair, comp),
                         [stats.NOT_COUNTED_CONFLICT, stats.NOT_COUNTED_CONFLICT])

    def test_and_a_night_named_two_ways_credits_nobody_at_all(self):
        # The case the second field exists for. The ladder keeps one of the two
        # readings -- they agree about the result -- and neither of them is anybody's
        # appearance, so the row the ladder counted still credited nobody.
        rules = self.no_draws()
        comp = self.competition(rules=rules)
        first = self.match(self.WON, rules=rules)
        pair = [first, self.renamed(first, "somebody-else")]
        self.assertEqual(stats.not_counted_reasons(pair, comp),
                         ["", stats.NOT_COUNTED_NAMES])
        self.assertEqual(stats.not_credited_reasons(pair),
                         [stats.NOT_COUNTED_NAMES, stats.NOT_COUNTED_NAMES])

    def test_and_the_seasons_own_totals_ask_a_third_question(self):
        # The front page's sets completed, games played, matches played, tightest
        # and widest night. A final and a level night are tennis that was played, so
        # they belong in every one of those figures -- and the ladder's field says to
        # leave both out. That is why this is a field and not the page excusing two
        # of the ladder's five reasons: the next reason added to that list would
        # change these totals without anybody deciding it should.
        rules = self.no_draws()
        comp = self.competition(rules=rules, finals=("2",))
        matches = [self.match(self.LEVEL, rules=rules),
                   self.match(self.WON, rules=rules, rnd="2")]
        self.assertEqual(stats.not_counted_reasons(matches, comp),
                         [stats.NOT_COUNTED_LEVEL, stats.NOT_COUNTED_FINALS])
        self.assertEqual(stats.not_totalled_reasons(matches), ["", ""])

    def test_a_card_exported_twice_is_one_night_in_those_totals(self):
        rules = self.no_draws()
        matches = [self.match(self.WON, rules=rules, source="a.pdf"),
                   self.match(self.WON, rules=rules, source="b.pdf")]
        self.assertEqual(stats.not_totalled_reasons(matches),
                         ["", stats.NOT_COUNTED_DUPLICATE])

    def test_and_two_cards_that_disagree_are_no_nights_at_all(self):
        # Neither reading is in the ladder, so neither is in a total above it. A
        # page that counted both would print more sets than were played from cards
        # it also says settled nothing.
        rules = self.no_draws()
        swapped = ((2, 6), (6, 2), (6, 3), (3, 6))
        pair = [self.match(self.LEVEL, rules=rules, source="a.pdf"),
                self.match(swapped, rules=rules, source="b.pdf")]
        self.assertEqual(stats.not_totalled_reasons(pair),
                         [stats.NOT_COUNTED_CONFLICT, stats.NOT_COUNTED_CONFLICT])

    def test_while_a_night_named_two_ways_is_totalled_from_the_card_kept(self):
        # The row where this field and the players' field disagree, which is the
        # reason there are two of them: the kept card's scoreline settled the night,
        # so its sets and games are in the season's totals, and its names are in
        # nobody's record.
        rules = self.no_draws()
        first = self.match(self.WON, rules=rules)
        pair = [first, self.renamed(first, "somebody-else")]
        self.assertEqual(stats.not_totalled_reasons(pair),
                         ["", stats.NOT_COUNTED_NAMES])
        self.assertEqual(stats.not_credited_reasons(pair),
                         [stats.NOT_COUNTED_NAMES, stats.NOT_COUNTED_NAMES])

    def test_the_rows_it_keeps_are_the_ones_scoreable_keeps(self):
        # Said as an assertion rather than left as a resemblance: `scoreable` is
        # what every aggregate in the document is computed from, and this field is
        # how the page is told which rows those were.
        rules = self.no_draws()
        first = self.match(self.WON, rules=rules)
        matches = [first, self.match(self.WON, rules=rules, source="b.pdf"),
                   self.match(self.LEVEL, rules=rules, rnd="2"),
                   self.renamed(self.match(self.WON, rules=rules, rnd="3"),
                                "somebody-else"),
                   self.match(self.WON, rules=rules, rnd="3")]
        reasons = stats.not_totalled_reasons(matches)
        self.assertEqual([m for m, reason in zip(matches, reasons) if not reason],
                         stats.scoreable(matches))

    def test_and_every_reason_it_gives_is_the_ladders_word_for_the_same_row(self):
        # The invariant that lets the page explain a row it left out of a total: the
        # sentence it prints comes from the ladder's field, so a row this one sets
        # aside has to be set aside there too, in the same word. The other direction
        # does not hold, and that is the point of the field.
        rules = self.no_draws()
        comp = self.competition(rules=rules, finals=("4",))
        swapped = ((2, 6), (6, 2), (6, 3), (3, 6))
        first = self.match(self.WON, rules=rules)
        matches = [first, self.match(self.WON, rules=rules, source="b.pdf"),
                   self.match(self.LEVEL, rules=rules, rnd="2", source="c.pdf"),
                   self.match(swapped, rules=rules, rnd="2", source="d.pdf"),
                   self.match(self.LEVEL, rules=rules, rnd="3", source="e.pdf"),
                   self.match(self.WON, rules=rules, rnd="4", source="f.pdf")]
        ladder = stats.not_counted_reasons(matches, comp)
        for match, mine, theirs in zip(matches, stats.not_totalled_reasons(matches),
                                       ladder):
            with self.subTest(source=match.source, round=match.round):
                self.assertTrue(mine == "" or mine == theirs,
                                f"{mine!r} is not the ladder's {theirs!r}")
        # And the list is worth something: it holds a row of every kind, including
        # the two the ladder alone would have hidden from these totals.
        self.assertEqual(sorted(set(ladder)),
                         ["", "conflict", "duplicate", "finals", "level"])


class TestNoFindingNamesTheCompetitionItIsListedUnder(unittest.TestCase):
    """The report labels every finding with the competition; the finding must not.

    `_problems_section` prints each message under `<competition id>: `, and the page's
    data health list sits inside that competition's own dashboard. So a message that
    opens with the id reads `tuesday-division-c: tuesday-division-c: the ladder is in
    progress`, and a `subject` that *is* the id says it a third time on a line of its
    own. Ten findings did one or both. It went unseen because those lines ran off the
    side of an eighty-column terminal until the report learnt to wrap.

    Built by hand and swept, because the ten belong to six rules and no fixture in this
    suite fires more than two of them at once: a competition with no rounds has had its
    cards routed somewhere, and one whose ladder cannot be computed needs a scoring
    scheme with no value for a draw.
    """

    ID = "tuesday-division-c"
    FMT = "doubles-singles-doubles"
    # The six rules that named their own competition, in either field. Swept rather
    # than listed one test each, and asserted complete below, so that a rule added to
    # this family is not quietly left out of the sweep.
    RULES = {"no-matches", "no-cards", "no-teams", "no-rounds", "F3", "F8", "F10"}

    def competition(self, **kw):
        base = dict(
            id=self.ID, day="Tuesday", division="C",
            fmt=fmt_mod.match_format(self.FMT), rules=scoring.ruleset("games-bonus"),
            teams={slug: Team(slug=slug, name=slug.upper())
                   for slug in ("rockets", "aces", "quolls")},
            rounds={"1": Round(number="1", fixtures=(("rockets", "aces"),)),
                    "2": Round(number="2", fixtures=(("rockets", "quolls"),))})
        base.update(kw)
        return Competition(**base)

    def match(self, scores, *, rules, rnd="1", away="aces"):
        fmt = fmt_mod.match_format(self.FMT)
        sets = tuple(
            SetResult(slot=fmt.slots[i], home_games=home, away_games=away_games,
                      home_players=(f"h{i}",), away_players=(f"a{i}",),
                      rule=fmt.set_rule)
            for i, (home, away_games) in enumerate(scores))
        return Match(competition=self.ID, round=rnd, home="rockets", away=away,
                     fmt=fmt, rules=rules, sets=sets, source=Path("card.pdf"))

    LEVEL = ((6, 2), (2, 6), (6, 3), (3, 6))
    WON = ((6, 2), (6, 1), (2, 6), (6, 3))

    def cases(self) -> list[tuple[str, validate.Report]]:
        """One report per way of reaching this family of rules, named for the trigger."""
        plain = scoring.ruleset("games-bonus")
        no_draws = dataclasses.replace(plain, draw=None)
        won = self.match(self.WON, rules=plain)
        # F10's `except ScoringError` guard, reached the only way it can be: by making
        # the ledger refuse. `ladder_matches` drops a level card the grade cannot price
        # (`priceable`) before the ledger is ever asked about it, so nothing a club can
        # put in a folder reaches that handler today -- it is there because a ledger is
        # free to grow a second reason to refuse, and because the alternative is the
        # report dying on one competition of three. Its sentence is shown to a club like
        # any other sentence, so it is swept like any other.
        with mock.patch("matchcentre.validate.points_ledger",
                        side_effect=scoring.ScoringError(
                            "games-bonus has no value for a draw")):
            refused = validate.validate([won], self.competition())
        return [
            ("a ledger that refuses to price the season", refused),
            ("nothing was read", validate.validate([], self.competition())),
            ("no team list", validate.validate([], self.competition(teams={}))),
            ("no draw at all", validate.validate([won], self.competition(rounds={}))),
            ("a team list off the cards", validate.validate([won], self.competition())),
            ("a season length nobody declared",
             validate.validate([won], self.competition(rounds_total=10))),
            ("more rounds played than declared", validate.validate(
                [won, self.match(self.WON, rules=plain, rnd="2", away="quolls")],
                self.competition(
                    rounds_total=1,
                    provenance={"rounds_total": Provenance.DECLARED}))),
            ("more rounds played than the draw that gave the length lists",
             validate.validate(
                 [won, self.match(self.WON, rules=plain, rnd="2", away="quolls")],
                 self.competition(
                     rounds_total=1, draw_outgrown_by=("2",),
                     provenance={"rounds_total": Provenance.DECLARED}))),
            ("a level card the grade cannot price", validate.validate(
                [self.match(self.LEVEL, rules=no_draws)],
                self.competition(rules=no_draws))),
            ("no counted match yet", validate.validate(
                [self.match(self.WON, rules=plain, rnd="F")],
                self.competition(rounds={"F": Round(number="F", is_finals=True)}))),
            ("an unequal number of matches", validate.validate([won],
                                                               self.competition())),
        ]

    def test_the_sweep_reaches_every_rule_that_used_to_repeat_itself(self):
        # The guard on the two tests below: they prove nothing about a rule no case
        # reaches, and each case is one line away from reaching nothing at all.
        found = {issue.rule for _, report in self.cases()
                 for issue in report.issues}
        self.assertEqual(self.RULES - found, set(),
                         "a rule in this family is not exercised by any case")

    def places(self, text: str) -> list[int]:
        """Where the competition's id appears in a message, left to right."""
        out, at = [], text.find(self.ID)
        while at != -1:
            out.append(at)
            at = text.find(self.ID, at + 1)
        return out

    def test_no_message_names_the_competition_the_report_has_named(self):
        # One use of the id is not a repetition and stays: `competitions.<id>` inside a
        # TOML path, which is a key the club has to find in a file of its own. F11 tells
        # a club where to say a round is not a final, and the path is the answer.
        for trigger, report in self.cases():
            for issue in report.issues:
                with self.subTest(trigger=trigger, rule=issue.rule):
                    for at in self.places(issue.message):
                        self.assertTrue(
                            issue.message[:at].endswith("competitions."),
                            f"{issue.rule} says it twice: {issue.message}")

    def test_nor_is_the_competition_what_a_finding_is_about(self):
        # `subject` is a match, a team or a path -- the thing inside the season this
        # finding points at. The season itself is the report's own label, and printed
        # here it is a line saying `subject: tuesday-division-c` under a line that
        # already opened with it.
        for trigger, report in self.cases():
            for issue in report.issues:
                with self.subTest(trigger=trigger, rule=issue.rule):
                    self.assertNotEqual(issue.subject, self.ID,
                                        f"{issue.rule} is about its own competition")

    def test_the_one_finding_made_outside_this_module_follows_the_same_rule(self):
        # `export._validation` raises an F9 of its own for a collision validate cannot
        # see -- two competitions merged under one key, each half internally consistent
        # -- and only when nothing was said about F9 at all. It lands in the same
        # section of the same report and the same list on the page, so it is labelled
        # the same way. Nothing else in this suite reaches that branch.
        section = export._validation(
            validate.Report(errors=(), warnings=()), rejected=(),
            duplicates=[{"round": "1", "home": "rockets", "away": "aces",
                         "match_ids": ["4600001", "4600001"],
                         "sources": ["first.pdf", "second.pdf"]}],
            competition=self.competition())
        error, = section["errors"]
        self.assertEqual(error["rule"], "F9")
        self.assertEqual(error["subject"], "round 1")
        self.assertNotIn(self.ID, error["message"])
        self.assertIn("rockets v aces", error["message"])
        for path in ("first.pdf", "second.pdf"):
            self.assertIn(path, error["detail"])

    def test_and_every_message_still_stands_up_as_a_sentence_without_it(self):
        # What deleting a prefix can cost: a message that read "{id} has no matches"
        # becomes "has no matches", which is not a sentence and reads as a fragment of
        # the label above it. Each one has to begin with a word of its own.
        for trigger, report in self.cases():
            for issue in report.issues:
                with self.subTest(trigger=trigger, rule=issue.rule):
                    first = issue.message.split()[0]
                    self.assertNotIn(first, {"has", "have", "is", "was", "were",
                                             "declares", "reports"},
                                     f"{issue.rule} opens mid-sentence: "
                                     f"{issue.message}")


class TestARefusedCardGivesItsReasonInTheMessageAndItsDetailOnce(unittest.TestCase):
    """F4's message is the card and why it was refused. The evidence is its detail.

    The message used to be built from the refusal's one-line description, which
    already had the detail in brackets, and then the detail was set beside it as
    well. Measured through ``build.py`` on the demo season with one PDF cut to its
    first 1,024 bytes and one with permissions 000
    (``/tmp/t4/scratch/y445/measure.py``).
    The locked card's message read ``data/locked.pdf: not a readable PDF
    (data/locked.pdf: cannot read ([Errno 13] Permission denied: 'data/locked.pdf'))
    — found under data/ and not scored.``, with the bracketed text again as its
    detail. Its entry in the terminal named the path six times. The page's data
    health list, which prints a finding's message and detail side by side, printed
    the detail twice. The cut card's 234-character detail came out three times in a
    one-division report.

    These refusals are made by hand because no card measured gives a build a refusal
    with no detail. The reader raises one refusal without a detail, for a page with
    nothing printed above its table. Such a page is refused first for having no
    identity line, and that refusal says why. A refusal with no detail used to be
    given its reason as its detail, so the reason was said twice. Now it gets no
    detail at all.

    Rejected: keeping the description whole and leaving the detail field empty. The
    terminal prints a detail on lines of its own under the message, and the page
    prints it in muted type after it, so that a 234-character sentence is kept out of
    the message.

    The detail was then told twice, since the refused entry, which the report and
    the page both print, carries it too. Measured on the demo season with a
    cut card, a text file and a locked card (``/tmp/t7/scratch/RF/measure.py``): each
    detail twice in the report and twice on the page. So F4 carries no detail, and
    where the refusal has one its message says where it is. Rejected: keeping the
    detail in both places, 4.45's choice; one mistake is reported once.
    """

    COMPETITION = Competition(id="tuesday-division-c", day="Tuesday", division="C",
                              fmt=fmt_mod.match_format("doubles-singles-doubles"),
                              rules=scoring.ruleset("games-bonus"))
    AFTER = " — found under data/ and not scored."
    POINTER = " The list of cards not scored gives its detail."

    def refused(self, *rejected) -> list[tuple[str, str, str]]:
        report = validate.validate([], self.COMPETITION, rejected=rejected)
        return [(issue.message, issue.subject, issue.detail)
                for issue in report.issues if issue.rule == "F4"]

    def test_the_message_is_the_card_and_the_reason_and_says_where_the_detail_is(self):
        self.assertEqual(self.refused(
            scorecard.Rejection(path="data/cut.pdf",
                                reason="no table rules could be found on the page",
                                detail="looked for filled rects under 2.0pt wide"),
            scorecard.Rejection(path="data/two.pdf", reason="no set rows were read",
                                detail="the second page has a banner and no table",
                                page=1)), [
            ("data/cut.pdf: no table rules could be found on the page" + self.AFTER
             + self.POINTER, "data/cut.pdf", ""),
            ("data/two.pdf page 2: no set rows were read" + self.AFTER + self.POINTER,
             "data/two.pdf", "")])

    def test_a_refusal_that_carries_no_detail_is_given_none(self):
        self.assertEqual(self.refused(scorecard.Rejection(
            path="data/cut.pdf", reason="not a readable PDF")),
            [("data/cut.pdf: not a readable PDF" + self.AFTER, "data/cut.pdf", "")])


class TestACompetitionsOwnErrorsAreEveryErrorButTheRunsRefusals(unittest.TestCase):
    """``export.own_findings`` and ``validation.own_errors``, as a unit.

    A refused card might be any competition's, so every document carries every
    refusal as an F4 error. The report's own ``problems`` line, the front page's
    flag and the page's pill each counted those among the competition's own errors
    and printed that count beside the count of cards not scored. Measured through
    ``build.py`` (``/tmp/t6/scratch/m11/measure.py``) on the demo with two refused
    PDFs: ``2 error(s)`` on the own line and ``2 validation errors`` on the front
    page, for a season with nothing else wrong. The CLI class in test_cli.py checks
    the three readers. This one checks the rule they share, on the one error no card
    run reaches: export's own F9, beside two refusals.

    Rejected: ``len(errors) - len(rejected)`` at each reader, which gives the same
    number today and is a second rule to keep in step with the first.
    """

    COMPETITION = TestARefusedCardGivesItsReasonInTheMessageAndItsDetailOnce.COMPETITION
    REJECTED = (scorecard.Rejection(path="data/cut.pdf", reason="not a readable PDF"),
                scorecard.Rejection(path="data/two.pdf", reason="no set rows were read",
                                    detail="the second page has no table", page=1))
    CLASH = {"round": "12", "home": "rockets", "away": "aces",
             "match_ids": ["4600001", "4600001"],
             "sources": ["first.pdf", "second.pdf"]}

    @classmethod
    def section(cls) -> dict:
        report = validate.validate([], cls.COMPETITION, rejected=cls.REJECTED)
        return export._validation(report, rejected=cls.REJECTED,
                                  duplicates=[cls.CLASH], competition=cls.COMPETITION)

    def test_a_competitions_own_findings_are_every_one_but_f4_in_their_order(self):
        findings = [{"rule": rule} for rule in ("F4", "F9", "F4", "draw-not-allowed")]
        self.assertEqual(export.own_findings(findings),
                         [{"rule": "F9"}, {"rule": "draw-not-allowed"}])
        self.assertEqual(export.own_findings([{"rule": "F4"}]), [])

    def test_the_document_counts_its_own_error_and_not_the_two_refusals(self):
        section = self.section()
        self.assertEqual([error["rule"] for error in section["errors"]],
                         ["F4", "F4", "F9"])
        self.assertEqual(section["own_errors"], 1)
        # The refusals are still errors, so the document is still not ok.
        self.assertFalse(section["ok"])

    def test_a_document_whose_only_errors_are_refusals_has_none_of_its_own(self):
        report = validate.validate([], self.COMPETITION, rejected=self.REJECTED)
        section = export._validation(report, rejected=self.REJECTED, duplicates=[],
                                     competition=self.COMPETITION)
        self.assertEqual((len(section["errors"]), section["own_errors"]), (2, 0))
        self.assertFalse(section["ok"])


class TestAFindingSaysWhetherItsMessageHasAlreadySaidItsSubject(unittest.TestCase):
    """``export.subject_said``: the one rule for when a reader prints a subject.

    A finding's ``subject`` names the thing it is about, such as a card's path or a
    match, and both the report and the page printed it under the message. Measured
    over every distinct finding the in-process suites make
    (``/tmp/t6/scratch/m11/sweep.py``): 1,171 of 1,216 carry a subject, and 1,060 of
    those messages begin with it, 1,059 followed by ``: `` and one by `` page``. The
    report printed ``subject: data/minutes.pdf`` under a line that opened with
    ``data/minutes.pdf:``. The page printed ``[data/minutes.pdf]`` after it.

    A subject is a key, always written, for a program to find a finding by. A reader
    prints it only where the message has not already begun with it, and the build
    decides which, once, into ``subject_said``. Rejected: setting a subject only
    where the message does not name the thing. That rewrites 1,060 findings in
    validate.py, and it empties the key build.py finds a ``verb-no-result``'s card
    by, which about 95 reads in the tests use as well.
    """

    def said(self, subject: str, message: str) -> bool:
        return export.subject_said({"subject": subject, "message": message})

    def test_a_message_that_opens_with_the_subject_as_a_word_has_said_it(self):
        for subject, message in (
                ("data/cut.pdf", "data/cut.pdf: not a readable PDF"),
                ("data/two.pdf", "data/two.pdf page 2: no set rows were read"),
                ("R2 aces v rockets", "R2 aces v rockets"),
                ("round 1", "round 1: two cards")):
            with self.subTest(subject=subject):
                self.assertTrue(self.said(subject, message))

    def test_one_that_does_not_or_only_opens_with_its_letters_has_not(self):
        for subject, message in (
                ("round 1", "round 12: two cards"),
                ("bo-nakamura", "Bo Nakamura played for two teams"),
                ("data/cut.pdf", "a card, data/cut.pdf: refused"),
                # Another file, wherever paths are told apart by case.
                ("data/Round1/cut.pdf", "data/round1/cut.pdf: not a readable PDF"),
                ("", ""),
                ("", "no matches were read")):
            with self.subTest(subject=subject, message=message):
                self.assertFalse(self.said(subject, message))

    def test_every_finding_in_the_document_carries_the_answer(self):
        section = TestACompetitionsOwnErrorsAreEveryErrorButTheRunsRefusals.section()
        found = section["errors"] + section["warnings"]
        self.assertEqual([(finding["rule"], finding["subject_said"])
                          for finding in found],
                         [("F4", True), ("F4", True), ("F9", False),
                          ("no-matches", False), ("no-teams", False),
                          ("no-rounds", False)])
        # Export's own F9 is about round 12, and its message says so in a sentence.
        self.assertEqual(section["errors"][2]["subject"], "round 12")


class TestTheGridCountsEveryMeeting(unittest.TestCase):
    """:func:`stats.head_to_head` by hand, for the meetings cards will not produce.

    The season fixture has one final between one pair, which is the ordinary case and
    is checked where it is exported. What it cannot show is a pair who met in the
    finals *twice* -- a preliminary final and a grand final, which every association
    running a top-four plays every year -- or a finals night that reached no result.
    """

    FMT = fmt_mod.match_format("doubles-singles-doubles")
    LOW, HIGH = "aces", "rockets"

    # 24-6 to the aces, and 17-17 on games with the sets 2-2.
    WIN = ((6, 1), (6, 2), (6, 0), (6, 3))
    LEVEL = ((6, 2), (2, 6), (6, 3), (3, 6))

    def rules(self, *, draws=True):
        base = scoring.ruleset("games-bonus")
        return base if draws else dataclasses.replace(base, draw=None)

    def competition(self, rules):
        return Competition(
            id="tuesday-division-c", day="Tuesday", division="C",
            fmt=self.FMT, rules=rules,
            teams={self.LOW: Team(slug=self.LOW, name="ACES"),
                   self.HIGH: Team(slug=self.HIGH, name="ROCKETS")},
            rounds={"1": Round(number="1"),
                    "PF": Round(number="PF", is_finals=True),
                    "GF": Round(number="GF", is_finals=True)})

    def match(self, scores, *, rnd, rules):
        sets = tuple(
            SetResult(slot=self.FMT.slots[i], home_games=home, away_games=away,
                      home_players=(f"h{i}",), away_players=(f"a{i}",),
                      rule=self.FMT.set_rule)
            for i, (home, away) in enumerate(scores))
        return Match(competition="tuesday-division-c", round=rnd, home=self.LOW,
                     away=self.HIGH, fmt=self.FMT, rules=rules, sets=sets,
                     source=Path(f"round-{rnd}/card.pdf"))

    def record(self, matches, rules):
        found = stats.head_to_head(matches, self.competition(rules))
        return found[stats.h2h_key(self.LOW, self.HIGH)]

    def test_two_finals_between_one_pair_are_counted_as_two(self):
        # The count accumulates rather than being set. A top-four final series has
        # the same two teams meeting twice inside a fortnight, and a record saying
        # one of those was a final understates by exactly the night the ladder's
        # tiebreak is most likely to be asked about.
        rules = self.rules()
        rec = self.record([self.match(self.WIN, rnd="1", rules=rules),
                           self.match(self.WIN, rnd="PF", rules=rules),
                           self.match(self.WIN, rnd="GF", rules=rules)], rules)
        self.assertEqual(rec.played, 3)
        self.assertEqual(rec.finals, 2)

    def test_the_regular_meeting_is_not_one_of_them(self):
        rules = self.rules()
        rec = self.record([self.match(self.WIN, rnd="1", rules=rules)], rules)
        self.assertEqual((rec.played, rec.finals), (1, 0))

    def test_a_finals_night_that_reached_no_result_is_both(self):
        # The two counts cut across each other by design: `finals` says which round
        # the night belonged to, `undecided` says what it produced. A grand final
        # left level in a grade that decides every match is both, and a record
        # forcing them to be exclusive would have to drop one of the two facts.
        rules = self.rules(draws=False)
        rec = self.record([self.match(self.WIN, rnd="1", rules=rules),
                           self.match(self.LEVEL, rnd="GF", rules=rules)], rules)
        self.assertEqual(rec.played, 2)
        self.assertEqual(rec.finals, 1)
        self.assertEqual(rec.undecided, 1)
        self.assertEqual(rec.draws, 0)


class TestAFixtureTheSeasonWentPastWithNoCardIsReported(unittest.TestCase):
    """``card-missing``: the draw says the match exists and no card names it.

    The one gap this program cannot infer its way out of. Every other absence has a
    reading -- a team missing from a round might be resting, a round with no cards
    might not have happened -- but a *declared* fixture with no card is a scorecard
    somewhere that nobody exported, and saying so costs no guesswork at all.

    Measured, before this existed: the only thing that mentioned it was a line on
    the terminal from :func:`~matchcentre.discover._shortfall_notes`, printed once
    at build time. A club building with ``-q`` never saw it, a club scrolling never
    saw it, and nobody opening the dashboard a week later saw it -- ``season.json``
    said nothing, so the Data health section said nothing, and the fixture's own row
    read "scheduled" for the rest of the season. The round above it read "not played
    yet" over rows for the cards that *had* arrived.

    Three refusals shape what is checked here.

    *No clock.* Whether a round has happened is answered by other cards and never
    by a date. ``--generated`` is an input and the draw's dates are a club's typing;
    a build replayed next year has to find the faults it found on the night, and a
    date comparison would have one set of files produce a different report every
    week. ``later`` is the evidence instead: the earliest round after this one that
    a card was read for.

    *Only a declared draw.* A derived draw's fixtures are built out of the cards, so
    none of them can be short of one, and reporting a fixture the club never
    published as missing is precisely the fabrication F2 forbids. Rejected: gating
    per round instead, on ``Round.source`` -- a club that declares one round gets
    cards-derived rounds either side of it, and those are safe for the very same
    reason, so the gate belongs on the competition.

    *The pair unordered.* A card with home and away swapped is still that fixture's
    card. Which side was really at home is ``draw-reversed``'s finding, and calling
    the card missing as well would be two findings about one card that is sitting
    right there.

    A warning, not an error, because ``Report.ok`` gates the build's exit status and
    a club that has three of this week's four cards must not have the run fail on
    the fourth.
    """

    RULES = scoring.ruleset("games-bonus")
    FMT = fmt_mod.match_format("doubles-singles-doubles")
    SLUGS = ("rockets", "aces", "bolts", "comets")
    # Four teams, because the case this is about is a round with some of its cards:
    # two fixtures a week is the smallest draw where one can be missing and the
    # other present.
    DRAW = {"1": (("rockets", "aces"), ("bolts", "comets")),
            "2": (("rockets", "bolts"), ("aces", "comets"))}

    def competition(self, rounds=None, *, declared=True, finals=(), dates=(),
                    byes=()):
        return Competition(
            id="tuesday-division-c", day="Tuesday", division="C",
            fmt=self.FMT, rules=self.RULES,
            draw_source=DRAW_DECLARED if declared else DRAW_FROM_CARDS,
            teams={slug: Team(slug=slug, name=slug.upper())
                   for slug in self.SLUGS},
            rounds={number: Round(number=number, fixtures=tuple(fixtures),
                                  date=dict(dates).get(number, ""),
                                  byes=tuple(dict(byes).get(number, ())),
                                  is_finals=number in finals,
                                  source=DRAW_DECLARED)
                    for number, fixtures in (rounds or self.DRAW).items()})

    def card(self, number, home, away):
        sets = tuple(
            SetResult(slot=self.FMT.slots[i], home_games=6, away_games=i,
                      home_players=(f"h{i}",), away_players=(f"a{i}",),
                      rule=self.FMT.set_rule)
            for i in range(4))
        return Match(competition="tuesday-division-c", round=number, home=home,
                     away=away, fmt=self.FMT, rules=self.RULES, sets=sets,
                     date=f"2026-0{number}-01" if number.isdigit() else "",
                     source=Path(f"round-{number}/{home}-v-{away}.pdf"))

    def bye(self, number, team):
        return Match(competition="tuesday-division-c", round=number, home=team,
                     away=NO_OPPONENT, fmt=self.FMT, rules=self.RULES,
                     status=MatchStatus.BYE)

    def gaps(self, matches, comp=None):
        return stats.fixtures_without_cards(matches, comp or self.competition())

    def findings(self, matches, comp=None):
        report = validate.validate(matches, comp or self.competition())
        return [i for i in report.warnings if i.rule == "card-missing"]

    def test_a_fixture_no_card_names_is_reported_once_its_round_has_others(self):
        # The everyday case: one of the night's cards was exported and the other
        # was not. Nothing about the round is in doubt -- it happened, there is a
        # card from it -- so the missing one is a file to find.
        gap, = self.gaps([self.card("1", "rockets", "aces")])
        self.assertEqual((gap.round, gap.home, gap.away),
                         ("1", "bolts", "comets"))
        self.assertEqual(gap.later, "", "the round this fixture is in is carried as "
                                       "the evidence that the season got past it, "
                                       "which reads as cards existing for a round "
                                       "whose cards are what is missing")

    def test_a_round_the_season_has_not_reached_is_not_a_missing_card(self):
        # Round 2 is declared and has no cards, and nothing after it has any
        # either. That is a round nobody has played, and a club that types its
        # whole draw in during August must not open a dashboard full of faults.
        cards = [self.card("1", "rockets", "aces"),
                 self.card("1", "bolts", "comets")]
        self.assertEqual(self.gaps(cards), [])

    def test_a_whole_round_the_season_went_past_is_named_fixture_by_fixture(self):
        # Round 2 has nothing at all and round 3 has a card, so the season moved on
        # without it. Two fixtures, two findings: each is a separate card to find,
        # and one finding naming the round would leave a club counting.
        comp = self.competition({**self.DRAW, "3": (("rockets", "comets"),)})
        cards = [self.card("1", "rockets", "aces"), self.card("1", "bolts", "comets"),
                 self.card("3", "rockets", "comets")]
        found = self.gaps(cards, comp)
        self.assertEqual([(g.round, g.home, g.away) for g in found],
                         [("2", "rockets", "bolts"), ("2", "aces", "comets")])
        self.assertEqual({g.later for g in found}, {"3"},
                         "the finding does not carry the round that proves the "
                         "season went past this one, so whoever renders it has to "
                         "assert the conclusion instead of showing the evidence")

    def test_a_round_is_judged_by_other_cards_and_never_by_its_date(self):
        # Round 1 is dated years ago and round 2 next century; both are reported,
        # because a card exists for round 3 and that is the whole of the evidence.
        # A date comparison would make this report a function of the day it was run.
        comp = self.competition(
            {**self.DRAW, "3": (("rockets", "comets"),)},
            dates=(("1", "2019-02-05"), ("2", "2119-02-12"), ("3", "2026-03-01")))
        found = self.gaps([self.card("3", "rockets", "comets")], comp)
        self.assertEqual({g.round for g in found}, {"1", "2"})

    def test_a_draw_derived_from_the_cards_can_have_nothing_missing(self):
        # Its fixtures *are* the cards, so a gap is impossible by construction and
        # a finding here would be invented (F2). The same rounds and the same one
        # card as the first test, which reports a gap on a declared draw.
        comp = self.competition(declared=False)
        self.assertEqual(self.gaps([self.card("1", "rockets", "aces")], comp), [])

    def test_a_card_the_draw_has_the_other_way_round_is_not_also_missing(self):
        # `draw-reversed` already reports this card, by name and by path. Reporting
        # the fixture as having no card would be a second finding about a card the
        # club is looking at, and the wrong one to act on.
        cards = [self.card("1", "aces", "rockets"), self.card("1", "bolts", "comets"),
                 self.card("2", "rockets", "bolts")]
        found = self.gaps(cards)
        self.assertEqual([(g.round, g.home, g.away) for g in found],
                         [("2", "aces", "comets")])

    def test_a_bye_the_draw_states_is_not_one_of_the_rounds_cards(self):
        # A bye is a scheduled rest: it has no card and none is owed. Counted as one,
        # it would both prove a round was played and inflate the evidence -- "2 of the
        # 1 fixtures the draw lists for that round do have one", over a fixture named
        # as having none, which is a line a club checks against its draw and finds
        # says two impossible things.
        comp = self.competition({"1": (("rockets", "aces"),),
                                 "2": (("bolts", "comets"),)},
                                byes=(("1", ("bolts", "comets")),))
        cards = [self.card("2", "bolts", "comets"),
                 self.bye("1", "bolts"), self.bye("1", "comets")]
        gap, = self.gaps(cards, comp)
        self.assertEqual((gap.round, gap.home, gap.away), ("1", "rockets", "aces"))
        self.assertEqual((gap.cards, gap.fixtures, gap.later), (0, 1, "2"),
                         "a rest was counted as a card, so the round's evidence "
                         "credits it with scorecards nobody exported")

    def test_a_declared_finals_fixture_with_no_card_is_still_a_card_to_find(self):
        # Finals pair by ladder position, so the fixture list is normally empty and
        # this finds nothing in a finals round without being told to skip one. A
        # club that did type the pairings in has declared them like any other
        # fixture, and the missing card is as real.
        comp = self.competition(
            {"1": (("rockets", "aces"), ("bolts", "comets")),
             "GF": (("rockets", "aces"), ("bolts", "comets"))}, finals=("GF",))
        cards = [self.card("1", "rockets", "aces"), self.card("1", "bolts", "comets"),
                 self.card("GF", "rockets", "aces")]
        gap, = self.gaps(cards, comp)
        self.assertEqual((gap.round, gap.home, gap.away),
                         ("GF", "bolts", "comets"))

    def test_the_finding_is_a_warning_because_a_club_builds_mid_round(self):
        # An error would put this in `Report.errors`, which is what `Report.ok` and
        # build.py's exit status are made of: the club with three of four cards has
        # a failing run over a file that is not wrong, only not in yet.
        found, = self.findings([self.card("1", "rockets", "aces")])
        self.assertEqual(found.severity, validate.WARNING)
        report = validate.validate([self.card("1", "rockets", "aces")],
                                   self.competition())
        self.assertEqual([i for i in report.errors if i.rule == "card-missing"], [])

    def test_the_finding_names_the_round_and_both_teams(self):
        # The line a club checks against its own draw, so it carries what the draw
        # carries: the round, and both teams under the names the club registered.
        found, = self.findings([self.card("1", "rockets", "aces")])
        self.assertEqual(found.subject, "R1 bolts v comets")
        self.assertIn("round 1", found.message)
        self.assertIn("BOLTS", found.message)
        self.assertIn("COMETS", found.message)

    def test_the_finding_shows_its_evidence_rather_than_asserting_the_gap(self):
        # The two ways of reaching this conclusion read differently to a club and
        # must not be worded the same. Within the round: how many of its fixtures do
        # have a card. Beyond it: which later round has one.
        same, = self.findings([self.card("1", "rockets", "aces")])
        self.assertRegex(same.message, r"\b1\b.*\b2\b")
        comp = self.competition({**self.DRAW, "3": (("rockets", "comets"),)})
        cards = [self.card("1", "rockets", "aces"), self.card("1", "bolts", "comets"),
                 self.card("3", "rockets", "comets")]
        later = self.findings(cards, comp)
        self.assertTrue(later)
        for issue in later:
            with self.subTest(subject=issue.subject):
                self.assertIn("round 3", issue.message)

    def test_the_finding_says_nothing_about_whether_the_night_was_played(self):
        # A forfeit, a washout and a card nobody exported are identical from here,
        # and which one it was is written on the scorecard this is asking for. The
        # predecessor's habit was to decide; F1 is the refusal.
        found, = self.findings([self.card("1", "rockets", "aces")])
        for word in ("forfeit", "washed", "cancelled", "not played"):
            with self.subTest(word=word):
                self.assertNotIn(word, found.message.lower())

    def test_the_finding_says_where_to_put_the_card_it_is_asking_for(self):
        # A fault with no next step is a fault a club cannot clear. Both routes:
        # export it, or run the reader's own diagnosis if the file is already in.
        found, = self.findings([self.card("1", "rockets", "aces")])
        self.assertIn("Match Centre", found.message)
        self.assertIn("doctor", found.message)

    def test_the_evidence_is_carried_in_the_detail_line_too(self):
        # The detail line is what a reader sees under the message, and it is the
        # only place the counts survive a reworded message.
        found, = self.findings([self.card("1", "rockets", "aces")])
        self.assertIn("1", found.detail)
        self.assertIn("2", found.detail)

    # Row 4.190. The count was the round's cards, listed or not, against the
    # fixtures the draw lists: measured on the demo (/tmp/t7/scratch/DR/probe.py),
    # "3 of the 1 fixtures the draw lists for that round do have one", and "3 of
    # the 3" beside a fixture named as having none. Now the fixtures that have a
    # card, out of those the draw lists, and the cards of fixtures it does not list
    # counted apart, by one function discovery's note asks as well.
    UNLISTED = ("the 2 cards read for that round are all of fixtures the draw does "
                "not list")

    def test_a_card_of_a_fixture_the_draw_does_not_list_is_not_counted_as_one_it_does(self):
        # Two teams more, so the unlisted card puts no team in the round twice.
        self.SLUGS = type(self).SLUGS + ("hawks", "owls")
        comp = self.competition()
        cards = [self.card("1", "rockets", "aces"), self.card("1", "hawks", "owls")]
        gap, = self.gaps(cards, comp)
        self.assertEqual((gap.round, gap.home, gap.away), ("1", "bolts", "comets"))
        self.assertEqual((gap.cards, gap.fixtures), (1, 2))
        self.assertEqual(gap.unlisted, 1)
        found, = self.findings(cards, comp)
        self.assertIn("which the declared draw lists, and 1 of the 2 fixtures the draw "
                      "lists for that round do have one. Export it", found.message)
        self.assertEqual(found.detail, "round 1: 1 of 2 declared fixtures have a card")
        notes = discover_mod._shortfall_notes(comp.id, dict(comp.rounds), cards,
                                              dict(comp.teams), True)
        self.assertEqual([note for note in notes if ": round 1 " in note], [
            f"{comp.id}: round 1 has cards for 1 of its 2 declared fixtures — "
            f"BOLTS v COMETS is missing. The draw says the fixture exists, so that "
            f"is a card to find, not a team resting."])

    def test_a_fixture_with_two_cards_is_one_fixture_with_a_card(self):
        # Two cards of one fixture are F9's to report; here they are one fixture.
        cards = [self.card("1", "rockets", "aces"), self.card("1", "rockets", "aces")]
        gap, = self.gaps(cards)
        self.assertEqual((gap.cards, gap.fixtures), (1, 2))
        self.assertEqual(gap.unlisted, 0)

    def test_a_round_whose_every_card_is_of_an_unlisted_fixture_is_said_to_be(self):
        # Round 1 typed with round 2's pairings: it has cards, so the season is
        # past it, and none of them is a fixture it lists.
        cards = [self.card("1", "rockets", "bolts"), self.card("1", "aces", "comets")]
        issues = self.findings(cards)
        self.assertEqual(len(issues), 2)
        for issue in issues:
            with self.subTest(subject=issue.subject):
                self.assertIn(f"which the declared draw lists, and {self.UNLISTED}.",
                              issue.message)
                self.assertNotIn("do have one", issue.message)
                self.assertNotIn("nothing at all was read", issue.message)
                self.assertEqual(issue.detail, "round 1: 0 of 2 declared fixtures "
                                               "have a card")
        found = self.gaps(cards)
        self.assertEqual([(g.home, g.away, g.cards, g.unlisted) for g in found],
                         [("rockets", "aces", 0, 2), ("bolts", "comets", 0, 2)])

    def test_two_cards_of_one_unlisted_fixture_are_two_cards_read(self):
        # Counted by the card, as the sentence says: two exports of one fixture the
        # draw does not list are two cards read for the round, and not one.
        cards = [self.card("1", "rockets", "bolts"), self.card("1", "rockets", "bolts")]
        issues = self.findings(cards)
        self.assertEqual(len(issues), 2)
        for issue in issues:
            with self.subTest(subject=issue.subject):
                self.assertIn(f"which the declared draw lists, and {self.UNLISTED}.",
                              issue.message)
        self.assertEqual([(g.home, g.away, g.cards, g.unlisted)
                          for g in self.gaps(cards)],
                         [("rockets", "aces", 0, 2), ("bolts", "comets", 0, 2)])

    def test_one_such_card_is_said_as_one(self):
        found = self.findings([self.card("1", "rockets", "bolts")])
        self.assertEqual(len(found), 2)
        for issue in found:
            with self.subTest(subject=issue.subject):
                self.assertIn("which the declared draw lists, and the one card read "
                              "for that round is of a fixture the draw does not "
                              "list. Export it", issue.message)

    def test_a_rest_is_not_a_card_of_a_fixture_the_draw_does_not_list(self):
        rnd = self.competition().rounds["1"]
        counted = stats.round_cards(rnd, [self.card("1", "rockets", "aces"),
                                          self.bye("1", "bolts"),
                                          self.card("2", "rockets", "bolts")])
        self.assertEqual((counted.carded, counted.missing, counted.unlisted),
                         (1, (("bolts", "comets"),), ()))

    def test_discovery_s_note_says_it_in_the_warning_s_words(self):
        # The note was "in the declared draw with no cards at all" of a round with
        # two, because it paired the cards with the fixtures by its own rule.
        comp = self.competition()
        cards = [self.card("1", "rockets", "bolts"), self.card("1", "aces", "comets")]
        notes = discover_mod._shortfall_notes(comp.id, dict(comp.rounds), cards,
                                              dict(comp.teams), True)
        self.assertEqual([note for note in notes if ": round 1 " in note], [
            f"{comp.id}: round 1 has cards for 0 of its 2 declared fixtures — "
            f"ROCKETS v ACES and BOLTS v COMETS are missing, and {self.UNLISTED}. The "
            f"draw says the fixture exists, so that is a card to find, not a team "
            f"resting."])

    def test_a_round_of_one_unlisted_card_and_one_fixture_is_worded_for_one(self):
        comp = self.competition({"1": (("rockets", "aces"),),
                                 "2": (("bolts", "comets"),)})
        notes = discover_mod._shortfall_notes(
            comp.id, dict(comp.rounds), [self.card("1", "rockets", "bolts")],
            dict(comp.teams), True)
        self.assertEqual(notes, [
            f"{comp.id}: round 1 has cards for 0 of its 1 declared fixture — "
            f"ROCKETS v ACES is missing, and the one card read for that round is of "
            f"a fixture the draw does not list. The draw says the fixture exists, so "
            f"that is a card to find, not a team resting.",
            f"{comp.id}: round 2 is in the declared draw with no cards at all, which "
            f"is what a round nobody has played yet looks like. Nothing was scored "
            f"for it."])

    def test_a_round_labelled_at_length_is_named_by_its_start_and_its_length(self):
        # 4.168. Each note on a declared round named it by its label whole, so a
        # label typed 5000 characters long was a note 5008 columns wide
        # (/tmp/t7/scratch/WID/p168-before.txt). The width class runs the note on a
        # round with no fixtures and the one on a round with no card; the one on a
        # round with some of its cards is held here, beside them.
        long = {digit: digit * 5000 for digit in "123"}
        comp = self.competition({long["1"]: self.DRAW["1"], long["2"]: (),
                                 long["3"]: self.DRAW["2"]})
        cards = [dataclasses.replace(self.card("1", "rockets", "aces"),
                                     round=long["1"])]
        notes = discover_mod._shortfall_notes(comp.id, dict(comp.rounds), cards,
                                              dict(comp.teams), True)
        said = {"1": "has cards for 1 of its 2 declared fixtures — BOLTS v COMETS",
                "2": "is declared with no fixtures",
                "3": "is in the declared draw with no cards at all"}
        self.assertEqual(len(notes), len(said), notes)
        for note, (digit, words) in zip(notes, said.items()):
            with self.subTest(round=digit):
                self.assertTrue(note.startswith(
                    f"{comp.id}: round {digit * 12}... (5000 characters) {words}"),
                    note[:200])

    def test_a_round_built_from_cards_is_named_the_same_way(self):
        # 4.168, the review's: the two notes on a round the cards drew named it
        # whole, 5000 characters of it (/tmp/t7/scratch/WID/p168-review.txt), after
        # the declared round's three were capped.
        long = {digit: digit * 5000 for digit in "12"}
        rounds = {long["1"]: Round(number=long["1"], fixtures=(("rockets", "aces"),
                                                               ("bolts", "comets")),
                                   source=DRAW_FROM_CARDS),
                  long["2"]: Round(number=long["2"], fixtures=(("rockets", "aces"),),
                                   source=DRAW_FROM_CARDS)}
        cards = [dataclasses.replace(self.card("1", home, away), round=long[digit])
                 for digit, pairs in (("1", self.DRAW["1"]),
                                      ("2", (("rockets", "aces"),)))
                 for home, away in pairs]
        comp = self.competition()
        for declared, digit, words in (
                (True, "2", "accounts for none of BOLTS and COMETS."),
                (False, "2", "has 1 card where the fullest round has 2.")):
            with self.subTest(teams_declared=declared):
                note, = discover_mod._shortfall_notes(
                    comp.id, rounds, cards, dict(comp.teams), declared)
                self.assertTrue(note.startswith(
                    f"{comp.id}: round {digit * 12}... (5000 characters) {words}"),
                    note[:200])

    def test_rounds_awaiting_their_cards_are_each_named_whole_or_by_their_start(self):
        # One by one and never the list: three labels of 38 characters, 118 when
        # joined, are named whole, and one of 5000 beside them by its start alone.
        labels = [f"Round {digit} (rescheduled from the washout)" for digit in "123"]
        comp = self.competition({label: self.DRAW["1"]
                                 for label in ["4" * 5000, *labels]})
        note, = discover_mod._shortfall_notes(comp.id, dict(comp.rounds), [],
                                              dict(comp.teams), True)
        self.assertTrue(note.startswith(
            f"{comp.id}: rounds 444444444444... (5000 characters), "
            f"{', '.join(labels[:-1])} and {labels[-1]} are in the declared draw with "
            f"no cards at all"),
            note[:300])


class TestF11NamesARoundLabelledAtLengthByItsStartAndItsLength(unittest.TestCase):
    """F11's three warnings name a round as every other echo of a label does.

    4.168, the review's. Each named the label whole, after discovery's notes and the
    fetcher had been capped: measured on rounds labelled with 5000 F's, 200 digits
    and 5000 b's (/tmp/t7/scratch/WID/p168-review.txt), warnings of 5428, 409 and
    5117 columns, and 461, 241 and 150 now. The subject and the detail are left
    whole, being what a finding is found by. Rejected: a cap of F11's own, a second
    rule for one echo.
    """

    FMT, RULES = (TestAFixtureTheSeasonWentPastWithNoCardIsReported.FMT,
                  TestAFixtureTheSeasonWentPastWithNoCardIsReported.RULES)
    card = TestAFixtureTheSeasonWentPastWithNoCardIsReported.card

    def warned(self, label, *, finals, source):
        comp = dataclasses.replace(
            TestAFixtureTheSeasonWentPastWithNoCardIsReported().competition(),
            draw_source=source,
            rounds={label: Round(number=label, fixtures=(("rockets", "aces"),),
                                 is_finals=finals, source=source)})
        found = validate.validate([self.card(label, "rockets", "aces")], comp)
        return [(issue.message, issue.subject) for issue in found.issues
                if issue.rule == "F11"]

    def test_each_warning_names_the_label_by_its_start_and_its_length(self):
        final, digits, name = "F" * 5000, "1" * 200, "b" * 5000
        for label, finals, source, said in (
                (final, True, DRAW_DECLARED,
                 ["round FFFFFFFFFFFF... (5000 characters) is a finals round, so "
                  "its 1 result is left out"]),
                (digits, True, DRAW_FROM_CARDS,
                 ["round 111111111111... (200 characters) is marked finals, but its "
                  "label is a number",
                  "round 111111111111... (200 characters) is a finals round, so its "
                  "1 result is left out"]),
                (name, False, DRAW_FROM_CARDS,
                 ["round label 'bbbbbbbbbbbb...' (5000 characters) is neither a "
                  "number nor a finals label"])):
            with self.subTest(label=label[:12]):
                warned = self.warned(label, finals=finals, source=source)
                self.assertEqual([(message[:len(start)], subject)
                                  for (message, subject), start in zip(warned, said)],
                                 [(start, f"R{label}") for start in said])
                self.assertEqual(len(warned), len(said))


class TestTheFinalsSettingTheClubTypedIsCarriedOnItsRound(SeasonCase):
    """Discovery keeps what was typed after ``finals =`` on the round it builds.

    A declared round was built with ``is_finals`` alone: what was typed or, where
    nothing was, what the label gives. So what the club typed was lost where the
    draw is built, F11, which is handed the competition and never the config,
    could not tell a final the club declared from one read off its label, and the
    finals note asked the config for itself. Measured on the demo season
    (/tmp/t7/scratch/DR/probe.py): the note said ``round SF is declared as
    finals`` and F11, of the same round, ``Set finals = false ... if this round is
    really a regular one``. ``Round.finals_typed`` is ``True`` or ``False`` where
    a value was typed and ``None`` where none was, and both ask it, so they cannot
    say two things of one round. Rejected: handing the config to validate(), a
    second road to a value discovery has already read.
    """

    TYPED = {"1": None, "2": False, "3": True, "SF": True, "GF": None}

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        path.write_text(
            '[competitions.tuesday-b]\nday = "Tuesday"\ndivision = "B"\n'
            + "".join(f'\n[[competitions.tuesday-b.rounds]]\nnumber = "{number}"\n'
                      + ("" if typed is None
                         else f"finals = {'true' if typed else 'false'}\n")
                      for number, typed in cls.TYPED.items()),
            encoding="utf-8")
        return config_mod.load(path)

    def test_each_round_carries_what_was_typed_on_it_and_none_where_nothing_was(self):
        self.assertEqual({number: rnd.finals_typed
                          for number, rnd in self.competition.rounds.items()},
                         self.TYPED)

    def test_f11_offers_the_setting_to_no_round_the_club_typed(self):
        # Round 3 is typed a final and has two results; nothing else here does.
        found, = [issue for issue in self.report.warnings if issue.rule == "F11"]
        self.assertEqual(found.subject, "R3")
        self.assertTrue(found.message.endswith("records of the players in it."),
                        found.message)

    def test_the_note_asks_the_round_and_not_the_config(self):
        # Handed no declared section at all, it reads the same off the rounds.
        notes = discover_mod._assumption_notes(
            self.competition, self.config, None, discover_mod._FROM_DATE,
            Provenance.DECLARED, self.competition.fmt.set_rule, self.matches)
        flat = " ".join(" ".join(notes).split())
        self.assertIn("tuesday-b: rounds 3 and SF are declared as finals, and earn no "
                      "ladder points.", flat)
        self.assertIn("tuesday-b: round GF is read as finals because the round "
                      "label names a final", flat)


class TestADiscoveredMatchAlwaysHasARound(SeasonCase):
    """A card that prints no round is refused, so no match is ever without one.

    Discovery had a note for cards that print no round number: scored, it said,
    and sat together at the end of the draw. Nothing reached it. The reader finds
    a division and a round on the card or refuses the page, and a round is a word
    with a letter or a digit in it. Measured on the demo season with round 2's first
    card rewritten with a round label of "", " ", "-", "." and "_"
    (/tmp/t7/scratch/DR/probe.py): the card refused each time, 14 matches of the 15
    built, none of them with an empty round, and the note never printed. The note is
    gone and this is the premise its removal rests on. Rejected: keeping it, tested
    with a match built by hand, which would have to decide how a round of "" is
    counted for a card the reader cannot return.
    """

    @classmethod
    def extra_cards(cls):
        (home, home_players), (away, away_players) = (TEAMS["riverside-rockets"],
                                                      TEAMS["hilltop-aces"])
        cw.write_card_file(cls.data / "Round4" / "no-round.pdf", cw.CardSpec(
            date="2026-09-08", round_label="", division="B", court="09",
            match_id="2009999", home_name=home, away_name=away,
            rows=rows_for(((6, 2), (6, 3), (4, 6), (6, 1)), home_players,
                          away_players),
            home_roster=squad_for("riverside-rockets", home_players)[0],
            away_roster=squad_for("hilltop-aces", away_players)[0]))

    def test_the_card_is_refused_by_the_reader(self):
        refused, = self.rejected
        self.assertTrue(refused.path.endswith("no-round.pdf"), refused.path)
        self.assertIn("no line above the set table names both a division and a "
                      "round", refused.reason)

    def test_no_match_discovered_is_without_a_round(self):
        matches = [m for found in self.discovery.matches.values() for m in found]
        self.assertEqual(len(matches), len(FIXTURES))
        self.assertEqual([str(m.source) for m in matches if not m.round.strip()], [])


class TestAPlayerWhoDecidedNoSetHasNoWinRateRatherThanANought(SeasonCase):
    """A win rate is sets won out of sets decided, and out of none it is not a number.

    ``win_pct`` was ``0.0`` whenever a player's won and lost sets added up to nothing,
    which is the arithmetic's guard written down as though it were a result. Measured
    on a two-card season from the card writer (/tmp/t4/scratch/y443/measure.py): a name
    printed on both rosters and never on court, a player whose one rubber was ticked
    Retired at 4-3, and a player who lost all six of theirs were published as the same
    ``0.0``, printed as the same 0.0% on the player page, the team page and the Players
    tab, exported as the same ``0.0%`` by Copy and CSV, and sorted among each other by
    name. Only the third is a record anybody set. It is ``None`` now, which the page
    already prints as a muted dash and which leaves those two as an empty cell, as
    ``points_average`` is for a team with nothing counted.

    The fourth round below puts the two kinds of player the base season does not have
    on court, on a card that still has its three decided rubbers and so still counts:
    Cass Ellery's only rubber is the doubles retired at 4-3, and Fay Bramwell loses a
    singles and is in that same retired doubles. The third registered player of the
    other two teams never plays at all.

    Rejected: keeping ``0.0`` and letting the page tell the cases apart. It would have
    to decide from ``sets_played``, which is 1 for Cass, and deciding is the Python's.
    Guarding on ``sets_played`` instead was rejected too: a retired set is played and
    not decided, so Cass would divide by nothing, and dividing by sets played gives Bo
    Nakamura 83.3 for ten won and one lost.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        ana, bo, cass = home_players
        dev, elin, fay = away_players
        home_rows, _ = squad_for(cls.HOME, home_players)
        away_rows, _ = squad_for(cls.AWAY, away_players)
        rows = (
            cw.SetRow(home_games=6, away_games=2, doubles=True,
                      home_players=(ana, bo), away_players=(dev, elin)),
            cw.SetRow(home_games=6, away_games=3, doubles=False,
                      home_players=(ana,), away_players=(dev,)),
            cw.SetRow(home_games=6, away_games=1, doubles=False,
                      home_players=(bo,), away_players=(fay,)),
            cw.SetRow(home_games=4, away_games=3, doubles=True,
                      home_players=(bo, cass), away_players=(elin, fay),
                      status=SetStatus.RETIRED, status_side="away"),
        )
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "retired.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="07", match_id="4000043",
            home_name=home_name, away_name=away_name, rows=rows,
            home_roster=home_rows, away_roster=away_rows))

    def player(self, slug: str) -> dict:
        return {p["slug"]: p for p in self.season["players"]}[slug]

    def test_a_player_who_decided_no_set_has_no_rate_whether_or_not_they_played(self):
        # Ilya and Lena are on a roster every week and never on court; Cass played
        # one set and it was retired. All three are published as having decided none.
        for slug, played in (("ilya-modric", 0), ("lena-ordoy", 0),
                             ("cass-ellery", 1)):
            with self.subTest(player=slug):
                player = self.player(slug)
                self.assertEqual((player["sets_played"], player["sets_won"],
                                  player["sets_lost"]), (played, 0, 0))
                self.assertIsNone(player["win_pct"])

    def test_a_player_who_lost_every_set_they_decided_has_a_real_nought(self):
        # Fay lost the singles and was in the retired doubles: one decided, none won.
        # The nought is a record, so it stays a number and not a dash.
        fay = self.player("fay-bramwell")
        self.assertEqual((fay["sets_played"], fay["sets_won"], fay["sets_lost"]),
                         (2, 0, 1))
        self.assertEqual(fay["win_pct"], 0.0)

    def test_the_rate_is_out_of_the_sets_decided_and_not_the_sets_played(self):
        # Bo: 8-1 over rounds 1-3, then won d1 and s2 and was in the retired doubles.
        # Ten of eleven is 90.9; ten of the twelve played would be 83.3.
        bo = self.player("bo-nakamura")
        self.assertEqual((bo["sets_played"], bo["sets_won"], bo["sets_lost"]),
                         (12, 10, 1))
        self.assertEqual(bo["win_pct"], 90.9)


class TestASlotsWinRateIsOutOfTheSetsDecidedInItAsTheTilesIs(SeasonCase):
    """Each slot's rate is published, by the rule that gives the Win rate tile its own.

    The player page's slot table worked its rate out for itself, as sets won out of
    sets played, so a retired set counted against the player in that table and in no
    other figure on the page. Measured on the demo season with headless Chrome
    (/tmp/t6/scratch/m13/before.txt): the four players in round 4's retired doubles
    were shown 50.0%, 40.0%, 33.3% and 40.0% at d2 for records that are 66.7, 50.0,
    50.0 and 50.0 out of the sets decided, and on 4.43's two-card season Foxtrot
    Fenwick's page printed a dash on the tile over a 0.0% at s2, whose one set was the
    retired one. Each slot record now carries ``win_pct`` from ``set_win_pct``, the
    function behind the tile's figure, and the page prints it.

    Round 4 below is 4.43's card: Bo Nakamura and Cass Ellery in the retired doubles,
    Fay Bramwell losing a singles and in the same retired doubles. Every expected
    figure is worked out from the card rows by hand.

    Rejected: leaving the page to work the rate out of ``won`` and a new ``lost``. That
    is the rule written a second time, in a second language, and the one it replaced
    was the rule written wrong.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    # 4.43's round-4 card, written by the same hand.
    extra_cards = TestAPlayerWhoDecidedNoSetHasNoWinRateRatherThanANought.__dict__[
        "extra_cards"]

    def slot(self, slug: str, key: str) -> tuple:
        record = {p["slug"]: p for p in self.season["players"]}[slug]["by_slot"][key]
        return record["played"], record["won"], record.get("win_pct", "missing")

    def test_a_retired_set_among_decided_ones_is_left_out_of_the_slots_rate(self):
        # Bo at d2: won it in rounds 1-3 and was in round 4's retired doubles. Three of
        # three decided is 100.0; three of the four played, which the page printed,
        # would be 75.0.
        self.assertEqual(self.slot("bo-nakamura", "d2"), (4, 3, 100.0))

    def test_a_slot_whose_only_set_was_retired_has_no_rate_as_the_tile_has_none(self):
        # Cass's one set and Fay's one doubles were the retired one: played, and not
        # decided. The page printed 0.0% for both, and Cass's tile's null is 4.43's.
        for slug in ("cass-ellery", "fay-bramwell"):
            with self.subTest(player=slug):
                self.assertEqual(self.slot(slug, "d2"), (1, 0, None))

    def test_a_slot_lost_every_time_it_was_decided_has_a_real_nought(self):
        # Fay's singles, lost 6-1 to Bo. A nought somebody set, so a number.
        self.assertEqual(self.slot("fay-bramwell", "s2"), (1, 0, 0.0))

    def test_a_slot_the_player_never_played_has_no_rate(self):
        # Bo never plays s1; Ilya is on the Spinners' roster every week and never on
        # court at all.
        self.assertEqual(self.slot("bo-nakamura", "s1"), (0, 0, None))
        for key in ("d1", "s1", "s2", "d2"):
            with self.subTest(slot=key):
                self.assertEqual(self.slot("ilya-modric", key), (0, 0, None))

    def test_every_slots_rate_comes_from_the_rule_the_tile_uses(self):
        # One rule, one function: a slot rated by an expression of its own agrees
        # with the tile only until one of the two is changed.
        with mock.patch.object(stats, "set_win_pct", return_value=12.3):
            computed = stats.player_stats(self.matches, self.competition)
        rates = [record.win_pct for player in computed.values()
                 for record in player.by_slot.values()]
        self.assertEqual(len(rates), 4 * len(computed))
        self.assertEqual(set(rates), {12.3})


class TestATeamsRateOfSetsWonIsPublishedByTheRuleAPlayersIs(SeasonCase):
    """A team's rate of sets won is the Python's figure, as every player's rate is.

    The team page's Sets tile worked the rate out in the page, as ``num(100 *
    t.sets_won / (t.sets_won + t.sets_lost), 0)``: the one rate on the site the
    season document did not carry, and the one printed to no decimal place where
    ``pct()`` prints every other to one. Measured on the demo season with headless
    Chrome (/tmp/t7/scratch/W2/before.txt): the Quolls' tile read ``53% of sets
    won`` for 10-9, where ``set_win_pct(10, 9)`` is 52.6, and the other five teams
    give whole numbers either way. Each team row now carries ``win_pct`` from
    ``set_win_pct``, the function behind every player's and every slot's rate.

    By hand from FIXTURES: the Rockets won their sets 4-0, 3-1 and 4-0, the Spinners
    3-1, 1-3 and 3-1, the Wanderers 1-3, 2-2 and 0-4, and the Aces 0-4, 2-2 and 1-3.
    Three cards more, one for each way a card can stand outside a row or inside it
    without deciding anything: a semi-final the Aces win 4-0 over the Rockets, out of
    the row as out of the ladder (F11); a second copy of round 3's Aces v Spinners,
    counted once; and a round 4 card on which the Wanderers win the only two sets the
    Spinners and they finished, in the row's sets as in its games, though it decides
    no result. The rate is out of the row's own sets, so the Wanderers are 5-9 and
    the Spinners 7-7. Worked out over every card instead, the Aces' rate was measured
    at 40.0 beside a row of 3-9; over decided matches only, the Wanderers' at 25.0
    beside 5-9 (/tmp/t7/scratch/W2/fixup/probe_scope.py).

    Rejected: leaving the arithmetic in the page and printing it to one place there.
    That is the rule a second time, in the language the suite cannot run, and
    ``set_win_pct`` already says what a team that decided no set has.
    """

    # slug -> (sets won, sets lost, win_pct). Eleven of twelve is 91.666..., which
    # the tile printed as 92%.
    EXPECTED = {"riverside-rockets": (11, 1, 91.7),
                "lakeside-spinners": (7, 7, 50.0),
                "parkview-wanderers": (5, 9, 35.7),
                "hilltop-aces": (3, 9, 25.0)}

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS["hilltop-aces"]
        away_name, away_players = TEAMS["riverside-rockets"]
        folder = cls.data / "Finals"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "semi-final.pdf", cw.CardSpec(
            date="2026-09-08", round_label="SF", division="B",
            court="11", match_id="6000001",
            home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 0),) * 4, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))
        card = cls.data / "Round3" / "HilltopAces-LakesideSpinners.pdf"
        card.with_name(f"{card.stem} (1).pdf").write_bytes(card.read_bytes())
        write_short_card(cls.data, "4", SHORT_OF_RESULT_DATES["4"],
                         "parkview-wanderers", "lakeside-spinners")

    def test_each_team_s_rate_is_its_sets_won_out_of_those_it_decided(self):
        # The pre-flight: the three cards arrived, each as what it is, or the table
        # below cannot tell the row's scope from any other.
        self.assertEqual(sorted((m["round"], m["not_counted"], m["result"])
                                for m in self.season["matches"]
                                if m["round"] in {"4", "SF"} or m["not_counted"]),
                         [("3", "duplicate", "away"), ("4", "", "none"),
                          ("SF", "finals", "home")])
        rows = {t["slug"]: t for t in self.season["teams"]}
        for slug, expected in self.EXPECTED.items():
            with self.subTest(team=slug):
                row = rows[slug]
                self.assertEqual((row["sets_won"], row["sets_lost"],
                                  row.get("win_pct", "missing")), expected)

    def test_a_team_that_has_decided_no_set_has_no_rate_rather_than_a_nought(self):
        # The season as it stood after round 1's first card: the Rockets 4-0 up on
        # the Aces, whose nought is a record, and two teams that have decided nothing.
        first, = [m for m in self.matches
                  if m.round == "1" and m.involves("riverside-rockets")]
        computed = stats.team_stats([first], self.competition)
        self.assertEqual({slug: getattr(team, "win_pct", "missing")
                          for slug, team in computed.items()},
                         {"riverside-rockets": 100.0, "hilltop-aces": 0.0,
                          "lakeside-spinners": None, "parkview-wanderers": None})

    def test_every_team_s_rate_comes_from_the_rule_a_player_s_comes_from(self):
        # One rule, one function: a rate by an expression of its own agrees with the
        # player's only until one of the two is changed.
        with mock.patch.object(stats, "set_win_pct", return_value=12.3):
            computed = stats.team_stats(self.matches, self.competition)
        self.assertEqual(len(computed), len(TEAMS))
        self.assertEqual({getattr(team, "win_pct", "missing")
                          for team in computed.values()}, {12.3})


class TestAFillInsAppearancesAreCountedInMatchesNotSets(SeasonCase):
    """A borrowed player used in two rubbers of one night is one appearance, not two.

    ``fill_in_appearances`` is the size of ``fill_ins``, a set of the same
    ``(round, home, away)`` keys the accumulator counts as ``match_ids``, so the unit
    is matches. The dashboard printed it as sets -- "has played 1 set outside their
    registered roster" on a night they played two -- which is a number a club can
    check against a scorecard and find wrong. Asserted from the data rather than from
    the source, because the source is what was misread.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    BORROWED = "Jo Calder"          # registered to parkview-wanderers
    BORROWED_SLUG = "jo-calder"
    # Two of the four rubbers, on one night. Both doubles and one singles is what a
    # club does with a borrowed player, so d1 and s1 it is.
    RUBBERS_FILLED_IN = 2

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        # The set rows name the borrowed player; the roster block does not. That
        # gap is what makes them a fill-in, and it is what a real card looks like.
        rows = (
            cw.SetRow(home_games=6, away_games=2, doubles=True,
                      home_players=(cls.BORROWED, home_players[0]),
                      away_players=away_players[:2]),
            cw.SetRow(home_games=6, away_games=3, doubles=False,
                      home_players=(cls.BORROWED,),
                      away_players=(away_players[0],)),
            cw.SetRow(home_games=6, away_games=4, doubles=False,
                      home_players=(home_players[1],),
                      away_players=(away_players[1],)),
            cw.SetRow(home_games=6, away_games=1, doubles=True,
                      home_players=home_players[:2],
                      away_players=away_players[:2]),
        )
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.card = cw.write_card_file(folder / "borrowed.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="09", match_id="4000003",
            home_name=home_name, away_name=away_name, rows=rows,
            home_fill_ins=(cls.BORROWED,),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def borrowed(self):
        return {p["slug"]: p for p in self.season["players"]}[self.BORROWED_SLUG]

    def test_the_night_is_one_appearance_however_many_rubbers_it_was(self):
        self.assertEqual(
            self.borrowed()["fill_in_appearances"], 1,
            "a borrowed player used twice in one night counts as one night away "
            "from their own team, so the figure the page prints is matches")

    def test_the_rubbers_themselves_are_still_counted(self):
        # Otherwise the 1 above could be a fill-in whose second rubber was dropped,
        # which is the same number arrived at by a much worse route. Three rubbers a
        # night for their own team across rounds 1-3, plus two on loan.
        borrowed = self.borrowed()
        self.assertEqual(borrowed["sets_played"], 9 + self.RUBBERS_FILLED_IN)
        self.assertEqual(borrowed["matches"], 4)

    def test_the_borrowed_players_games_go_to_neither_team(self):
        # The share column is a partition of a team's games among *its own* players,
        # so a borrowed player's games are the one legitimate reason it adds to less
        # than a hundred per cent -- and the shortfall has to be exactly them.
        # Nine games: three of the six in d1, which they split, and all six of s1.
        borrowed_games = 3.0 + 6.0
        rockets = {t["slug"]: t for t in self.season["teams"]}[self.HOME]
        credited = sum(p["contribution"]["games_won"]
                       for p in self.season["players"] if p["team"] == self.HOME)
        self.assertAlmostEqual(credited,
                               rockets["games_won"] - borrowed_games, places=6)

    def test_their_own_team_is_not_credited_the_games_either(self):
        # The other half of the same rule: the games were won for the rockets, so
        # crediting them to the wanderers' total -- the team this player is
        # registered to -- would put games on a page for a match that team did not
        # play. Their own three rounds for the wanderers are what remains.
        borrowed = self.borrowed()
        self.assertEqual(borrowed["team"], "parkview-wanderers")
        for_own_team = borrowed["contribution"]["games_won"]
        self.assertLess(for_own_team, borrowed["games_won"])
        self.assertLessEqual(for_own_team,
                             borrowed["contribution"]["team_games_won"])

    def test_and_the_team_row_says_how_many_of_its_games_no_row_of_its_table_holds(self):
        """The same nine games, on the team whose table has no row for them.

        The test above measures the shortfall by summing what the rows were credited,
        which is how the dashboard used to work it out. Item 3.19 has the exporter
        write it down instead -- so the number the page prints is measured here too,
        against the same nine games, and the reason with it: two rubbers, because both
        of the borrowed player's are rubbers this table cannot fully account for, and
        a rubber is what a reader would go and look at. Counted per rubber and not per
        name, and not per night, which is the count the disputed pair uses.

        The rockets won 24 games on this card, all four rubbers 6-something, so their
        94 games are short by nine: 9.6% at the one place the document rounds
        percentages to.
        """
        rockets = {t["slug"]: t for t in self.season["teams"]}[self.HOME]
        self.assertEqual(rockets["games_won"], 94)
        self.assertEqual(rockets["contribution_gap"],
                         {"games": 9.0, "team_games_won": 94, "share_pct": 9.6,
                          "rubbers_with_outsiders": 2, "rubbers_short_a_name": 0,
                          "rubbers_overfull": 0, "nights_not_credited": 0})
        # And their opponents' column is whole: a loan is one side's shortfall, and a
        # figure that appeared on both would be a walk crediting by the card rather
        # than by the side of it.
        aces = {t["slug"]: t for t in self.season["teams"]}[self.AWAY]
        self.assertEqual(aces["contribution_gap"]["games"], 0.0)
        self.assertEqual(aces["contribution_gap"]["rubbers_with_outsiders"], 0)

    def test_the_report_names_the_loan_once_per_rubber_not_once_per_night(self):
        # The report and the player page count different things on purpose, and
        # this pins the difference: the report is a list of rows for a human to
        # look at, so a row is its unit, while the page is describing a person's
        # season. Two counts of the same event, both correct, differently worded.
        named = [i for i in self.report.issues
                 if i.rule == "fill-in" and self.BORROWED in i.message]
        self.assertEqual(len(named), self.RUBBERS_FILLED_IN)
        for issue in named:
            with self.subTest(subject=issue.subject):
                self.assertTrue(issue.subject.startswith(f"R{self.ROUND} "),
                                f"a fill-in warning that does not name its "
                                f"rubber: {issue.subject!r}")


class TestTheShareOfATeamsGamesIsAPartitionOfThem(SeasonCase):
    """The Share column adds up to the team, because that is what a share is.

    "Share of the team's games won" was computed by crediting a doubles rubber's
    games to *both* players who won them, against a denominator that counted those
    games once. Two of four rubbers on a club card are doubles, so the column ran
    to about 150%: on the demo season that ships with the project, the six teams
    summed to 147.8, 144.2, 148.9, 151.6, 149.9 and 150.0 per cent. Each figure is
    beside the words "of the team's games won", and the first person to add the
    column up is the first person to find it.

    A doubles pair wins its games jointly, so half each is the only split that
    makes the column a partition -- and the halves are split by the *slot*, two for
    doubles, rather than by how many names the card had, so a rubber missing a
    name credits half and leaves the rest with nobody rather than handing an absent
    partner's share to whoever was printed.

    Worked out on paper below from the same six cards the rest of this file uses,
    for the two teams whose figures include a half.
    """

    # slug -> credited games, by hand from FIXTURES. Doubles rows are halved; a
    # singles row goes whole to the one player in it. The third registered player
    # of each team never takes a court, so their share is a real zero.
    #
    #   rockets  R1 d1 6 -> 3/3, s1 6 -> Ana, s2 6 -> Bo, d2 6 -> 3/3
    #            R2 d1 6 -> 3/3, s1 6 -> Ana, s2 4 -> Bo, d2 6 -> 3/3
    #            R3 d1 6 -> 3/3, s1 6 -> Ana, s2 6 -> Bo, d2 6 -> 3/3
    #   aces     R1 d1 1 -> .5/.5, s1 2 -> Dev, s2 0 -> Elin, d2 3 -> 1.5/1.5
    #            R2 d1 6 -> 3/3,   s1 3 -> Dev, s2 6 -> Elin, d2 4 -> 2/2
    #            R3 d1 2 -> 1/1,   s1 6 -> Dev, s2 1 -> Elin, d2 3 -> 1.5/1.5
    EXPECTED_CREDIT = {
        "riverside-rockets": {"ana-fernandez": 36.0, "bo-nakamura": 34.0,
                              "cass-ellery": 0.0},
        "hilltop-aces": {"dev-raman": 20.5, "elin-sotto": 16.5,
                         "fay-bramwell": 0.0},
    }

    def players_of(self, team: str) -> list[dict]:
        return [p for p in self.season["players"] if p["team"] == team]

    def test_each_players_credit_is_the_one_worked_out_on_paper(self):
        players = {p["slug"]: p for p in self.season["players"]}
        for team, expected in self.EXPECTED_CREDIT.items():
            for slug, credit in expected.items():
                with self.subTest(team=team, player=slug):
                    self.assertAlmostEqual(
                        players[slug]["contribution"]["games_won"], credit,
                        places=6)

    def test_the_credited_games_add_up_to_the_teams_own_games_won(self):
        # The invariant, over every team rather than the two worked out above. No
        # fill-in played in this season, so nothing is legitimately uncredited.
        rows = {t["slug"]: t for t in self.season["teams"]}
        for team in sorted(rows):
            with self.subTest(team=team):
                credited = sum(p["contribution"]["games_won"]
                               for p in self.players_of(team))
                self.assertAlmostEqual(credited, rows[team]["games_won"],
                                       places=6)

    def test_the_share_column_sums_to_a_hundred_per_cent(self):
        # What a reader actually does with the column. Half a point of slack for
        # the rounding of each row, and no more.
        for team in sorted(t["slug"] for t in self.season["teams"]):
            with self.subTest(team=team):
                total = sum(p["contribution"]["share_pct"]
                            for p in self.players_of(team))
                self.assertAlmostEqual(total, 100.0, delta=0.5)

    def test_the_denominator_is_the_teams_games_and_says_so(self):
        # The tile prints "36 of 70 team games won", so the second figure has to be
        # the team's games won and not some derived total of its own.
        rows = {t["slug"]: t for t in self.season["teams"]}
        for team, expected in self.EXPECTED_CREDIT.items():
            for player in self.players_of(team):
                with self.subTest(player=player["slug"]):
                    self.assertEqual(player["contribution"]["team_games_won"],
                                     rows[team]["games_won"])

    def test_nobody_is_credited_more_games_than_they_won(self):
        # The old numerator could exceed a player's own games won, which is the
        # arithmetic saying out loud that it had counted a rubber twice.
        for player in self.season["players"]:
            with self.subTest(player=player["slug"]):
                self.assertLessEqual(player["contribution"]["games_won"],
                                     player["games_won"])


class TestTheSameCardExportedTwiceIsCountedOnce(SeasonCase):
    """`Riverside-Rockets.pdf` and `Riverside-Rockets (1).pdf`, the same night twice.

    The commonest way a club's `data/` folder goes wrong: a card is exported again,
    the browser adds a suffix, and nothing is deleted. Both files are the same
    evidence about the same fixture, so the fixture is counted once -- twice would
    hand two teams a second win, twenty-four more games and a ladder nobody can
    reconcile against the association's sheet.

    Nothing is guessed and nothing is thrown away: both cards are still read, still
    listed, and named in a finding that says which files they are. It is a warning
    rather than an error, because after this the numbers on the page are right; what
    is left is a tidy-up in a folder.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    SCORES = ((6, 2), (6, 3), (6, 4), (6, 1))            # 24-10, sets 4-0
    MATCH_ID = "4600001"
    # Counted once: rockets 19.0 + (4 + 2.4), aces 10.7 + (2 + 1.0). Counted twice
    # they would read 31.8 and 16.7.
    POINTS = {"riverside-rockets": 25.4, "hilltop-aces": 13.7}
    GAMES = {"riverside-rockets": 94, "hilltop-aces": 47}

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        spec = cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="14", match_id=cls.MATCH_ID,
            home_name=home_name, away_name=away_name,
            rows=rows_for(cls.SCORES, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.first = cw.write_card_file(folder / "Rockets-Aces.pdf", spec)
        cls.second = cw.write_card_file(folder / "Rockets-Aces (1).pdf", spec)

    def team(self, slug: str) -> dict:
        found, = [t for t in self.season["teams"] if t["slug"] == slug]
        return found

    def rows(self) -> list[dict]:
        return [m for m in self.season["matches"] if m["round"] == self.ROUND]

    def test_both_cards_were_read_and_both_reach_the_document(self):
        # The pre-flight, and the promise: a duplicate is not quietly deleted.
        self.assertEqual(self.season["meta"]["cards_scored"], len(FIXTURES) + 2)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual({m["home"] for m in self.rows()}, {self.HOME})

    def test_the_fixture_is_counted_once(self):
        for slug, points in self.POINTS.items():
            with self.subTest(team=slug):
                row = self.team(slug)
                self.assertAlmostEqual(row["points"], points, places=6)
                self.assertEqual(row["games_won"], self.GAMES[slug])
                self.assertEqual(row["played"], 4)

    def test_the_teams_that_were_not_in_it_are_untouched(self):
        for slug, games in EXPECTED_GAMES_FOR.items():
            if slug in self.GAMES:
                continue
            with self.subTest(team=slug):
                self.assertEqual(self.team(slug)["games_won"], games)
                self.assertAlmostEqual(self.team(slug)["points"],
                                       EXPECTED_POINTS[slug], places=6)

    def test_a_player_is_not_credited_with_the_night_twice(self):
        ana, = [p for p in self.season["players"]
                if p["slug"] == "ana-fernandez"]
        self.assertEqual(ana["matches"], 4)
        self.assertEqual(ana["sets_played"], 12)        # three rubbers a card

    def test_the_two_teams_have_not_met_three_times(self):
        # They played in round 1 and again in round 4. A grid saying three is a
        # figure a reader can check against the draw in a second.
        record = self.season["head_to_head"][stats.h2h_key(self.HOME, self.AWAY)]
        self.assertEqual(record["played"], 2)
        self.assertEqual(record["games_low"] + record["games_high"], 24 + 6 + 34)

    def test_a_repeated_card_is_a_repeat_first_and_a_final_second(self):
        # Precedence, on the one match list where both could apply: a final left
        # out of the ladder is the rules working, and two cards for it is a thing
        # somebody has to go and fix, so the page is told the second.
        rounds = dict(self.competition.rounds)
        rounds[self.ROUND] = dataclasses.replace(rounds[self.ROUND],
                                                 is_finals=True)
        finals = dataclasses.replace(self.competition, rounds=rounds)
        ordered = stats.chronological(self.matches)
        reasons = [reason for match, reason
                   in zip(ordered, stats.not_counted_reasons(ordered, finals))
                   if match.round == self.ROUND]
        self.assertEqual(sorted(reasons), ["duplicate", "finals"])

    def test_one_of_the_two_rows_says_it_is_the_copy(self):
        counted = [m for m in self.rows() if m["counts_for_ladder"]]
        copies = [m for m in self.rows() if not m["counts_for_ladder"]]
        self.assertEqual(len(counted), 1)
        self.assertEqual(len(copies), 1)
        self.assertEqual(counted[0]["not_counted"], "")
        self.assertEqual(copies[0]["not_counted"], "duplicate")
        # And the copy is the second of the two, in the order the page lists them:
        # its explanation reads "a second copy of a night already above", which is
        # only true of the one that is not first.
        listed = self.season["matches"]
        self.assertLess(listed.index(counted[0]), listed.index(copies[0]))

    def test_and_which_of_the_two_credited_the_players(self):
        # The same answer twice over here, and the field is not redundant: a page
        # listing every set a person played reads the match rows, and the copy is
        # where a second round 4 in that list would come from. It says of itself
        # that nobody's record was built from it; the counted card says the
        # opposite, because its names are the ones in every per-player figure.
        counted = [m for m in self.rows() if m["counts_for_ladder"]]
        copies = [m for m in self.rows() if not m["counts_for_ladder"]]
        self.assertEqual(counted[0]["not_credited"], "")
        self.assertEqual(copies[0]["not_credited"], "duplicate")

    def test_the_front_pages_own_count_is_one_night_and_not_two(self):
        # The consequence, checked the way the page arrives at it: it counts its
        # matches, sets and games off these rows itself, so filtering them as it does,
        # by each row's own `counts_as_played` and `not_totalled`, has to leave one row
        # per night. Unfiltered, this season read 8 matches played where 7 were played,
        # 32 sets completed for 28 and 274 games for 240 -- and, where the draw is
        # declared, "8 of 7 fixtures played".
        played = [m for m in self.season["matches"]
                  if m["counts_as_played"] and not m["not_totalled"]]
        self.assertEqual(len(played), len(FIXTURES) + 1)
        keys = [(m["round"], m["home"], m["away"]) for m in played]
        self.assertEqual(sorted(keys), sorted(set(keys)),
                         "a fixture is in the season's totals twice")
        night, = [m for m in played if m["round"] == self.ROUND]
        self.assertEqual(night["home_games"] + night["away_games"], 34)

    def test_and_both_rows_say_the_night_was_played(self):
        # `counts_as_played` is the rule's answer about the night, not a fourth word
        # for which card was counted: the copy is the same tennis, and `not_totalled`
        # is what keeps it out of the count above. Folded into it, the copy's row would
        # tell the page its night counted for nothing -- the washout's words, on a
        # night that was played and is counted, once, off the other card.
        self.assertEqual([m["counts_as_played"] for m in self.rows()], [True, True])

    def test_and_which_of_the_two_the_seasons_totals_were_built_from(self):
        # The third question, for the tiles on the front page: one night per night
        # played, and that night's sets and games counted once.
        counted = [m for m in self.rows() if m["counts_for_ladder"]]
        copies = [m for m in self.rows() if not m["counts_for_ladder"]]
        self.assertEqual(counted[0]["not_totalled"], "")
        self.assertEqual(copies[0]["not_totalled"], "duplicate")

    def test_and_every_answer_sits_on_the_row_it_is_about(self):
        # All three fields are lists worked out for the season as a whole and then
        # zipped on to the rows position by position, so each of the three is one
        # variable name away from being printed against the wrong night: "duplicate"
        # on a round nobody exported twice, and the copy below it claiming it was
        # counted. Nothing in the numbers moves when that happens, which is what
        # makes it worth a test.
        #
        # Built here from the reverse of the read order, because the zip is with the
        # chronological list and read order is not chronological in general -- a club
        # that files by court or renames a folder hands the exporter any order at all.
        season = export.build_season(
            list(reversed(self.matches)), competition=self.competition,
            source_dir=self.data, generated="2026-09-10T12:00:00",
            club=self.discovery.club, cards=self.cards, rejected=self.rejected)
        rows = season["matches"]
        keys = [(m["round"], m["home"], m["away"]) for m in rows]
        for field in ("not_counted", "not_credited", "not_totalled"):
            for key, row in zip(keys, rows):
                # Only the three words about repeats: "level" and "finals" are about
                # a fixture with one card and say nothing about where a row sits.
                if row[field] in {"duplicate", "conflict", "names"}:
                    with self.subTest(field=field, round=row["round"]):
                        self.assertGreater(
                            keys.count(key), 1,
                            f"{field} reads {row[field]!r} of a fixture with one card")
        for field in ("not_counted", "not_credited", "not_totalled"):
            with self.subTest(field=field):
                blank = [m for m in rows
                         if m["round"] == self.ROUND and not m[field]]
                self.assertEqual(len(blank), 1, f"{field} of the pair is not one of two")

    def test_the_finding_names_both_files_and_says_it_counted_once(self):
        issue, = [i for i in self.report.issues if i.rule == "F9"]
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertIn(str(self.first), issue.detail)
        self.assertIn(str(self.second), issue.detail)
        self.assertIn("once", issue.message)

    def test_nothing_here_is_worth_failing_a_build_over(self):
        self.assertTrue(self.report.ok)
        self.assertEqual({i.rule for i in self.report.warnings} & {"F9"}, {"F9"})
        # Not reported a second time as an id clash or as a team playing twice: it
        # is one mistake, and it is the same two files.
        self.assertEqual([i.rule for i in self.report.issues
                          if i.rule == "team-twice-in-round"], [])

    def test_the_document_agrees_with_the_report_about_the_severity(self):
        # The exporter has a duplicate check of its own, for a collision only it can
        # see. It must not fire here and contradict the report -- an error in the
        # document beside a warning in the terminal is one of them being wrong.
        section = self.season["validation"]
        self.assertEqual([e["rule"] for e in section["errors"]], [])
        self.assertEqual([w["rule"] for w in section["warnings"]].count("F9"), 1)
        self.assertEqual(len(section["duplicates"]), 1)
        self.assertTrue(section["ok"])

    def test_the_night_is_one_fixture_in_the_draw(self):
        # The draw is a list of fixtures and was built one per card, so the spare
        # file put this night in the draw twice: a seven-fixture season with an
        # eighth fixture in it, listed twice under its round on the Draw page.
        self.assertEqual(self.competition.rounds[self.ROUND].fixtures,
                         ((self.HOME, self.AWAY),))

    def test_and_one_row_in_the_document_that_says_two_cards_reached_it(self):
        # Where the draw page reads it, and where "of N in the draw" is counted.
        # The row is not thrown away, it just stops being two rows: its own `cards`
        # count is how a reader gets from one fixture back to two files.
        fixtures = self.season["fixtures"]
        keys = [(f["round"], f["home"], f["away"]) for f in fixtures]
        self.assertEqual(sorted(keys), sorted(set(keys)),
                         "a fixture is in the draw twice")
        self.assertEqual(len(fixtures), len(FIXTURES) + 1)
        night, = [f for f in fixtures if f["round"] == self.ROUND]
        self.assertEqual(night["cards"], 2)
        self.assertTrue(night["played"])


class TestTheWarningAboutACopyNamesTheFileThatIsNotCounted(SeasonCase):
    """``Delete the spare file from data/`` named no file, beside two it listed.

    F9's warning for a night exported twice listed both files and told the club to
    delete "the spare file", when one of them is the card every figure on the page
    was counted from. The season decides which, in
    :func:`~matchcentre.stats.duplicates`: the first card of the night in the order
    the season runs, and that is not the file the club exported first, because
    ``Rockets-Aces (1).pdf`` reads before ``Rockets-Aces.pdf``. Measured before on
    the demo with the round 5 Brindle Bunyips v Brindle Kookaburras card again as
    ``bunyips-v-kookaburras (1).pdf`` under id 42000149
    (``/tmp/t7/scratch/DUP/runs/before/copy-id``): exit 0, the warning ending
    ``Delete the spare file from data/ to keep the folder honest.``, and the document
    counting the ``(1).pdf`` and marking ``bunyips-v-kookaburras.pdf`` the copy. With
    a ``(2).pdf`` as well it said ``the spare file`` of two.

    So the sentence names the copies, asked of the function that chose the card
    counted, in words for how many there are. Rejected: naming both and leaving the
    choice to the club, which the detail line under it already did; and choosing by
    this check's own walk of the cards, a second rule for which card counts.
    """

    PAIR = ("4", "2026-09-08", ("riverside-rockets", "hilltop-aces"),
            ("Rockets-Aces.pdf", "Rockets-Aces (1).pdf"))
    THREE = ("5", "2026-09-15", ("lakeside-spinners", "parkview-wanderers"),
             ("Spinners-Wanderers.pdf", "Spinners-Wanderers (1).pdf",
              "Spinners-Wanderers (2).pdf"))

    @classmethod
    def extra_cards(cls):
        for index, (rnd, when, (home, away), files) in enumerate((cls.PAIR,
                                                                  cls.THREE)):
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            spec = cw.CardSpec(
                date=when, round_label=rnd, division="B", court=f"6{index}",
                match_id=f"460100{index}", home_name=home_name, away_name=away_name,
                rows=rows_for(((6, 2), (6, 3), (6, 4), (6, 1)), home_players,
                              away_players),
                home_roster=squad_for(home, home_players)[0],
                away_roster=squad_for(away, away_players)[0])
            folder = cls.data / f"Round{rnd}"
            folder.mkdir(parents=True, exist_ok=True)
            for name in files:
                cw.write_card_file(folder / name, spec)

    def f9(self, number: str) -> validate.Issue:
        found = [i for i in self.report.issues
                 if i.rule == "F9" and i.subject.startswith(f"R{number} ")]
        self.assertEqual(len(found), 1, [i.message for i in found])
        return found[0]

    def sources(self, number: str, reason: str) -> list[str]:
        """The files of round *number* the document gives *reason*, in its order."""
        return [m["source"] for m in self.season["matches"]
                if m["round"] == number and m["not_totalled"] == reason]

    def test_the_warning_names_the_copy_the_season_left_out(self):
        kept, = self.sources(self.PAIR[0], "")
        spare, = self.sources(self.PAIR[0], "duplicate")
        # The pre-flight, and why a sentence naming no file was no help: the card
        # counted is the (1).pdf, the one a club would take for the spare.
        self.assertEqual(Path(kept).name, "Rockets-Aces (1).pdf")
        issue = self.f9(self.PAIR[0])
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertTrue(issue.message.endswith(
            f" Delete the spare file {spare}, the one not counted, to keep the "
            f"folder honest."), issue.message)
        self.assertNotIn(kept, issue.message,
                         "the warning tells the club to delete the card every figure "
                         "on the page was counted from")

    def test_two_copies_are_both_named_and_spoken_of_as_two(self):
        spares = self.sources(self.THREE[0], "duplicate")
        self.assertEqual(len(spares), 2)
        message = self.f9(self.THREE[0]).message
        self.assertTrue(message.endswith(
            f" Delete the spare files {spares[0]} and {spares[1]}, the ones not "
            f"counted, to keep the folder honest."), message)


class TestSpareCardsAreNamedInTheOrderTheSeasonRuns(SeasonCase):
    """A spare card in rounds 2, 10, SF and GF is named in that order, both places.

    The duplicate check and the document's list of duplicates sorted their
    fixtures as text, so round 10 was named before round 2 and the grand final
    before the semi-final, an order no season is played in. Measured before on a
    ten-round demo season with a spare card in rounds 2 and 10
    (``/tmp/t4/scratch/f3/d2/r10-before``): the report named ``R10`` first and
    ``duplicates`` read ``10``, ``2``; with spare cards in rounds 2, SF and GF
    (``d2/tdup-before``) they read ``2``, ``GF``, ``SF``. Every other list of
    rounds in the report and the document runs in the season's order, and these
    now do too, by the fixture key the change list already sorted by.

    Rejected: a key of the round's number where it has one and its text where it
    has not, which is the finals half of ``round_sort_key`` written again.
    """

    EXTRA = (("10", "2026-10-20"), ("SF", "2026-10-27"), ("GF", "2026-11-03"))

    @classmethod
    def extra_cards(cls):
        spare = cls.data / "Round2" / "RiversideRockets-LakesideSpinners.pdf"
        shutil.copyfile(spare, spare.with_name(f"{spare.stem} (1).pdf"))
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        for index, (label, when) in enumerate(cls.EXTRA):
            spec = cw.CardSpec(
                date=when, round_label=label, division="B", court=f"3{index}",
                match_id=f"480000{index}", home_name=home_name,
                away_name=away_name,
                rows=rows_for(((6, 2), (6, 3), (6, 4), (6, 1)), home_players,
                              away_players),
                home_roster=squad_for("riverside-rockets", home_players)[0],
                away_roster=squad_for("hilltop-aces", away_players)[0])
            folder = cls.data / f"Round{label}"
            folder.mkdir(parents=True, exist_ok=True)
            cw.write_card_file(folder / "Rockets-Aces.pdf", spec)
            cw.write_card_file(folder / "Rockets-Aces (1).pdf", spec)

    def test_the_report_names_them_round_by_round(self):
        self.assertEqual([i.subject.split()[0] for i in self.report.issues
                          if i.rule == "F9"], ["R2", "R10", "RSF", "RGF"])

    def test_the_document_lists_them_round_by_round(self):
        self.assertEqual([d["round"] for d in self.season["validation"]["duplicates"]],
                         ["2", "10", "SF", "GF"])


class TestATeamOrAPlayerTwiceInARoundIsNamedInTheOrderTheSeasonRuns(SeasonCase):
    """Rounds 2, 10, SF and GF, each with a team twice and a player for two teams.

    ``team-twice-in-round`` and ``player-two-teams`` sorted their (round, slug)
    pairs as text, so round 10 came before round 2 and GF before SF. Measured
    before on a ten-round demo season with a team twice in rounds 2 and 10
    (``/tmp/t4/scratch/f3/d2/ttwice-before``): ``R10 brindle-magpies``,
    ``R10 brindle-quolls``, ``R2 brindle-bunyips``, ``R2 brindle-magpies``. Now by
    round, in the season's order, and by slug within a round.

    Rejected: sorting the finished issue list by round, which would move every
    other check's findings too, when each check already lists its own in order.
    """

    EXTRA = (("10", "2026-10-20"), ("SF", "2026-10-27"), ("GF", "2026-11-03"))

    @classmethod
    def card(cls, label, when, index, home, away, away_players=None):
        (home_name, home_on), (away_name, away_on) = TEAMS[home], TEAMS[away]
        folder = cls.data / f"Round{label}"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / f"{home}-{away}.pdf", cw.CardSpec(
            date=when, round_label=label, division="B", court=f"5{index}",
            match_id=f"490{index:04d}", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 3), (4, 6), (6, 1)), home_on,
                          away_players or away_on),
            home_roster=squad_for(home, home_on)[0],
            away_roster=squad_for(away, away_on)[0]))

    @classmethod
    def extra_cards(cls):
        # Each round: the aces meet the wanderers and then the rockets, and field a
        # wanderer, Jo Calder, against the rockets.
        borrowed = ("Jo Calder", *TEAMS["hilltop-aces"][1][1:])
        index = 0
        for label, when in (("2", DATES["2"]), *cls.EXTRA):
            if label != "2":
                for home, away in (("riverside-rockets", "lakeside-spinners"),
                                   ("hilltop-aces", "parkview-wanderers")):
                    index += 1
                    cls.card(label, when, index, home, away)
            index += 1
            cls.card(label, when, index, "riverside-rockets", "hilltop-aces",
                     borrowed)

    def subjects(self, rule):
        return [i.subject for i in self.report.issues if i.rule == rule]

    def test_a_team_twice_is_named_round_by_round(self):
        self.assertEqual(self.subjects("team-twice-in-round"), [
            f"R{label} {slug}" for label in ("2", "10", "SF", "GF")
            for slug in ("hilltop-aces", "riverside-rockets")])

    def test_a_player_for_two_teams_is_named_round_by_round(self):
        self.assertEqual(self.subjects("player-two-teams"), [
            f"R{label} jo-calder" for label in ("2", "10", "SF", "GF")])


class TestTwoCardsThatDisagreeAboutAFixtureCountForNothing(SeasonCase):
    """A corrected re-export whose scores differ from the card it replaces.

    Two cards, one fixture, two different results. Which one is right is a question
    about a *result*, and this program does not answer those: it does not pick the
    later file, the longer scoreline or the one that sorts first. So the fixture is
    in no figure at all until a human removes one of the two files -- an error, exit
    1, both paths named -- and both cards stay on the Matches page saying so.

    The alternative, scoring both, is what happened before: two teams were paid
    twice for one night and the ladder could not be reconciled against anything.
    """

    ROUND = "4"
    HOME, AWAY = "lakeside-spinners", "parkview-wanderers"
    FIRST = ((6, 2), (6, 3), (6, 4), (6, 1))             # 24-10
    SECOND = ((6, 2), (6, 3), (2, 6), (6, 4))            # 20-15
    MATCH_ID = "4700001"

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        for name, scores in (("first.pdf", cls.FIRST), ("second.pdf", cls.SECOND)):
            written.append(cw.write_card_file(folder / name, cw.CardSpec(
                date="2026-09-08", round_label=cls.ROUND, division="B",
                court="15", match_id=cls.MATCH_ID,
                home_name=home_name, away_name=away_name,
                rows=rows_for(scores, home_players, away_players),
                home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(home_players)),
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players)))))
        cls.first, cls.second = written

    def rows(self) -> list[dict]:
        return [m for m in self.season["matches"] if m["round"] == self.ROUND]

    def test_the_two_cards_disagree_and_both_were_read(self):
        # The pre-flight: same fixture, two results, both in the document.
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual({m["home_games"] for m in self.rows()}, {24, 20})

    def test_neither_is_in_any_figure(self):
        for slug in EXPECTED_GAMES_FOR:
            with self.subTest(team=slug):
                row, = [t for t in self.season["teams"] if t["slug"] == slug]
                self.assertEqual(row["games_won"], EXPECTED_GAMES_FOR[slug])
                self.assertAlmostEqual(row["points"], EXPECTED_POINTS[slug],
                                       places=6)
                self.assertEqual(row["played"], 3)

    def test_the_ladder_is_the_one_the_first_three_rounds_made(self):
        by_position = sorted(self.season["teams"], key=lambda t: t["position"])
        self.assertEqual([t["slug"] for t in by_position], EXPECTED_LADDER)
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)
        # And it is a settled order, not a snapshot: the round the two cards
        # cannot settle is counted for nobody, so nobody is a match ahead (F10).
        self.assertTrue(self.season["meta"]["ladder_complete"])

    def test_the_players_are_not_credited_with_a_night_nobody_can_settle(self):
        gil, = [p for p in self.season["players"] if p["slug"] == "gil-ahearn"]
        self.assertEqual(gil["matches"], 3)

    def test_both_rows_say_why_they_count_for_nothing(self):
        for row in self.rows():
            with self.subTest(source=row["source"]):
                self.assertFalse(row["counts_for_ladder"])
                self.assertEqual(row["not_counted"], "conflict")

    def test_and_that_neither_of_them_credited_anybody_either(self):
        # Both questions, answered separately on every row, because on one kind of
        # card they differ: a night two cards agree about and name differently is
        # counted and credited to nobody. Here the answers agree, and a page asking
        # only the ladder's question would list both readings of the night in
        # somebody's set-by-set trail while their match count excluded it.
        for row in self.rows():
            with self.subTest(source=row["source"]):
                self.assertEqual(row["not_credited"], "conflict")

    def test_nor_are_they_in_the_sets_and_games_the_season_says_were_played(self):
        # The third field, and here all three agree: two readings of one night, in
        # the ladder from neither, in nobody's record from either, and in no total
        # above them. A page counting them would print more sets than were played,
        # from cards it says settled nothing.
        for row in self.rows():
            with self.subTest(source=row["source"]):
                self.assertEqual(row["not_totalled"], "conflict")

    def test_the_error_names_both_files_and_says_neither_was_counted(self):
        issue, = [i for i in self.report.issues if i.rule == "F9"]
        self.assertEqual(issue.severity, validate.ERROR)
        self.assertIn(str(self.first), issue.detail)
        self.assertIn(str(self.second), issue.detail)
        self.assertIn("neither", issue.message)
        self.assertIn("remove", issue.message)

    def test_the_build_stops_for_this_and_only_this(self):
        self.assertFalse(self.report.ok)
        self.assertEqual({i.rule for i in self.report.errors}, {"F9"})
        # Once in the document too, not once from the report and once more from the
        # exporter's own duplicate check.
        section = self.season["validation"]
        self.assertEqual([e["rule"] for e in section["errors"]], ["F9"])
        self.assertFalse(section["ok"])

    def test_the_night_is_still_a_fixture_in_the_draw_and_is_one_fixture(self):
        # The question this fix had to settle. Two cards disagree about a *result*;
        # they agree about who played whom, which is the only thing the draw claims.
        # So the fixture stays -- dropping it would take the night off the one page
        # a club looks at when a night is missing, and shrink the "of N" a finished
        # season is measured against -- and it is one fixture, not two.
        self.assertEqual(self.competition.rounds[self.ROUND].fixtures,
                         ((self.HOME, self.AWAY),))
        night, = [f for f in self.season["fixtures"]
                  if f["round"] == self.ROUND]
        self.assertEqual(len(self.season["fixtures"]), len(FIXTURES) + 1)
        self.assertTrue(night["played"])
        self.assertEqual(night["cards"], 2)


class TestEachCardOfARepeatedNightNamesTheOtherFiles(SeasonCase):
    """Every row of a night more than one card claims says which files the others are.

    Two cards that disagree about a night are counted nowhere, and the page went on
    showing the night as played. Measured on the demo season with a second round 2
    card whose last rubber reads 6-2 for 6-0 (``/tmp/t4/scratch/y436/measure``):
    exit 1 and an F9 error naming both files, and on ``#/matches`` one card when the
    two printed the same match id, silent about the clash. So the page owes the
    clash on the card, and a card that says "another card disagrees" and cannot say
    which one leaves the reader to find it among a season of files. The page cannot
    work that out for itself: it has no way to group cards but the exporter's key,
    and a second copy of that rule in JavaScript is how two answers start.

    So ``other_cards``: the place in the document's ``matches`` of every other card
    for the same ``(round, home, away)``, in the order the document lists them,
    which is the order the build read them and the order the finding names them.
    Places and not each card's ``source``, because a withheld build withholds a
    file name that holds a word of a player's name whole, and then every such card
    reads the same: measured in test_anonymise, the page showed a card the night
    never had. On every row, ``[]`` for a card alone. Not only on a clash, which
    was weighed: a field written only there would say "no other card" of a copy
    that has an original, and that is absence recorded as a fact. Here a clash of
    three cards, two of them agreeing, and a card exported twice, in one round. The
    clash's card read first printed no match id, which a card can: measured on the
    demo, a clash whose first card printed none had a fixture that found no card,
    and the page showed the other card alone, so the cards that did print one are
    the page's way to it.
    """

    ROUND = "4"
    CLASH = ("lakeside-spinners", "parkview-wanderers")
    COPIED = ("riverside-rockets", "hilltop-aces")
    AGREED = ((6, 2), (6, 3), (6, 4), (6, 1))            # 24-10
    OTHER = ((6, 2), (6, 3), (2, 6), (6, 4))             # 20-15

    @classmethod
    def spec(cls, pair, scores, *, match_id, court):
        (home_name, home_players), (away_name, away_players) = (TEAMS[pair[0]],
                                                                TEAMS[pair[1]])
        return cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B", court=court,
            match_id=match_id, home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)))

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        # One id for two of the three, as a re-export gives it, which is the case
        # where the page used to show one card of the night and nothing else. And none
        # on the card read first, `(1).pdf` sorting ahead of the original, so the
        # fixture takes no id and finds no card.
        for name, scores, match_id in (
                ("Spinners-Wanderers.pdf", cls.AGREED, "4800001"),
                ("Spinners-Wanderers (1).pdf", cls.OTHER, ""),
                ("Spinners-Wanderers (2).pdf", cls.AGREED, "4800001")):
            cw.write_card_file(folder / name, cls.spec(
                cls.CLASH, scores, match_id=match_id, court="15"))
        copied = cls.spec(cls.COPIED, cls.AGREED, match_id="4800002", court="14")
        for name in ("Rockets-Aces.pdf", "Rockets-Aces (1).pdf"):
            cw.write_card_file(folder / name, copied)

    def night(self, pair, season=None) -> list[dict]:
        return [m for m in (season or self.season)["matches"]
                if (m["round"], m["home"], m["away"]) == (self.ROUND, *pair)]

    def place(self, row, season=None) -> int:
        # Where this very row is in the document's matches.
        at, = [i for i, m in enumerate((season or self.season)["matches"])
               if m is row]
        return at

    def test_a_card_alone_on_its_night_names_no_other_card(self):
        alone = [m for m in self.season["matches"] if m["round"] != self.ROUND]
        self.assertEqual(len(alone), len(FIXTURES))
        for row in alone:
            with self.subTest(source=row["source"]):
                self.assertEqual(row["other_cards"], [])

    def test_each_card_of_a_clash_names_the_others_in_the_order_the_finding_does(self):
        rows = self.night(self.CLASH)
        self.assertEqual([m["not_counted"] for m in rows], ["conflict"] * 3)
        # The pre-flight: the finding lists the three files in the document's order,
        # which is the order a reader meets them in the data health section too.
        issue, = [i for i in self.report.issues
                  if i.rule == "F9" and i.severity == validate.ERROR]
        at = [issue.detail.find(m["source"]) for m in rows]
        self.assertEqual(at, sorted(at))
        self.assertGreaterEqual(min(at), 0)
        for row in rows:
            with self.subTest(source=row["source"]):
                self.assertEqual(row["other_cards"],
                                 [self.place(m) for m in rows if m is not row])

    def test_a_card_that_printed_no_id_is_named_by_the_cards_that_did(self):
        # The page found a card by its match id alone, the last one read with it, as
        # `IDX.matchById` did, from the fixture or from the round's own list. A card
        # that printed none was in neither, and the other cards' `other_cards` the
        # one way to it. Left out of them, a clash whose first card printed no id was
        # one card on the page where the build read three. The page now finds every
        # card by its key (TestEveryCardIsFoundByAKeyNoOtherCardHas), no round or
        # fixture lists a card by id (TestNoRoundOrFixtureListsACardByTheIdItPrinted),
        # and a night's own page, `#/match/<id>`, still opens on the cards that
        # printed its id, whose `other_cards` still name this one.
        rows = self.night(self.CLASH)
        # The pre-flight: the card read first printed no id, and is the fixture's first.
        self.assertEqual([m["id"] for m in rows], ["", "4800001", "4800001"])
        fixture, = [f for f in self.season["fixtures"]
                    if (f["round"], f["home"], f["away"]) == (self.ROUND, *self.CLASH)]
        self.assertEqual(fixture["keys"], [m["key"] for m in rows])
        reached = set()
        for match_id in sorted({m["id"] for m in rows if m["id"]}):
            # Every card under that id, as `#/match/<id>` finds them.
            for card in [m for m in self.season["matches"] if m["id"] == match_id]:
                reached.update([card["source"],
                                *(self.season["matches"][at]["source"]
                                  for at in card["other_cards"])])
        self.assertEqual(reached, {m["source"] for m in rows},
                         "a card of the night is on no part of the page")

    def test_a_copy_and_its_original_name_each_other_as_well(self):
        # Not a clash, and the field says the same kind of thing: the folder holds
        # another card for this night. What the page does with it is the page's
        # business; what the document must not do is say there is none.
        first, second = self.night(self.COPIED)
        self.assertEqual([first["not_counted"], second["not_counted"]],
                         ["", "duplicate"])
        self.assertEqual(first["other_cards"], [self.place(second)])
        self.assertEqual(second["other_cards"], [self.place(first)])

    def test_each_row_is_answered_about_itself_whatever_order_the_cards_arrive_in(self):
        # Worked out for the list as a whole and zipped on to the rows, as the three
        # reasons are, so it is one list away from naming another night's files.
        # Built from the reverse of the read order for the reason the class above
        # gives: read order is anything a club's folder hands the exporter.
        season = export.build_season(
            list(reversed(self.matches)), competition=self.competition,
            source_dir=self.data, generated="2026-09-10T12:00:00",
            club=self.discovery.club, cards=self.cards, rejected=self.rejected)
        for pair in (self.CLASH, self.COPIED):
            rows = self.night(pair, season)
            for row in rows:
                with self.subTest(source=row["source"]):
                    self.assertEqual(row["other_cards"],
                                     [self.place(m, season) for m in rows
                                      if m is not row])
        for row in season["matches"]:
            if row["round"] != self.ROUND:
                self.assertEqual(row["other_cards"], [], row["source"])


class TestEveryCardIsFoundByAKeyNoOtherCardHas(SeasonCase):
    """The page found a card by the match id it printed, and not every card prints one.

    ``fixtures[].match_id`` was the first card's id, ``rounds[].matches`` the ids of
    the cards that had one, and the page looked each up in an index by id, which
    holds one card per id, the last written. Measured on the demo season, headless
    Chrome at 1280 and 360px (``/tmp/t6/scratch/m1/probe``). The round 2 Brindle
    Wombats v Brindle Bunyips card printing no id: exit 0, ``all 15 card(s)
    scored``, and on ``#/matches`` 14 cards, 56 rubber rows where the demo has 60,
    and the fixture reading ``scheduled``. A second card for it, neither printing an
    id: 14 cards, the clash on no part of the page. A second round 5 card under the
    first one's id: the front page's Latest round showed one card twice and the
    other nowhere. And a round 2 card printing another fixture's id: that fixture's
    card shown twice, its own nowhere, and no scheduled row.

    So every card gets a key in the document, ``matches[].key``, and the fixture and
    the round list theirs, ``fixtures[].keys`` and ``rounds[].keys``, in the
    document's order. The key is the fixture and the card's place among that
    fixture's cards, ``4/lakeside-spinners/parkview-wanderers/2``: slugs are
    ``[a-z0-9-]``, so it reads back from the right whatever the round is called.
    The printed id stays where it was, because ``#/match/<id>`` is the address a
    club can check against the card in its hand. Rejected: the card's place in
    ``matches``, which moves every later card's key when a card for an earlier
    fixture arrives (measured, 13 of the demo's 14 when a round 1 card is added);
    the fixture alone, which is the same for both cards of a clash; the file's
    name, which a withheld build withholds and a key would publish.
    """

    NOID = ("4", "lakeside-spinners", "parkview-wanderers")
    COPIED = ("4", "riverside-rockets", "hilltop-aces")
    ALONE = ("5", "hilltop-aces", "lakeside-spinners")
    BORROWED = ("5", "riverside-rockets", "parkview-wanderers")
    DATES = {"4": "2026-09-08", "5": "2026-09-15"}
    # Played a week late, so round 5's cards in the order a season runs, the date
    # before the teams, are not its keys sorted, the teams before the place.
    LATE = {ALONE: "2026-09-22"}
    AGREED = ((6, 2), (6, 3), (6, 4), (6, 1))            # 24-10
    OTHER = ((6, 2), (6, 3), (2, 6), (6, 4))             # 20-15

    @classmethod
    def spec(cls, fixture, scores, *, match_id, court):
        rnd, home, away = fixture
        (home_name, home_players), (away_name, away_players) = TEAMS[home], TEAMS[away]
        return cw.CardSpec(
            date=cls.LATE.get(fixture, cls.DATES[rnd]), round_label=rnd, division="B",
            court=court,
            match_id=match_id, home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)))

    @classmethod
    def extra_cards(cls):
        for fixture, name, scores, match_id in (
                # A clash neither of whose cards printed an id.
                (cls.NOID, "Spinners-Wanderers.pdf", cls.AGREED, ""),
                (cls.NOID, "Spinners-Wanderers (1).pdf", cls.OTHER, ""),
                # A night exported twice under one id.
                (cls.COPIED, "Rockets-Aces.pdf", cls.AGREED, "4800002"),
                (cls.COPIED, "Rockets-Aces (1).pdf", cls.AGREED, "4800002"),
                # A card alone on its night that printed no id.
                (cls.ALONE, "Aces-Spinners.pdf", cls.OTHER, ""),
                # And one printing the id of another fixture's cards.
                (cls.BORROWED, "Rockets-Wanderers.pdf", cls.AGREED, "4800002")):
            folder = cls.data / f"Round{fixture[0]}"
            folder.mkdir(parents=True, exist_ok=True)
            cw.write_card_file(folder / name, cls.spec(fixture, scores,
                                                       match_id=match_id, court="16"))

    def cards_of(self, fixture, season=None) -> list[dict]:
        return [m for m in (season or self.season)["matches"]
                if (m["round"], m["home"], m["away"]) == fixture]

    def test_every_card_has_a_key_no_other_card_has(self):
        rows = self.season["matches"]
        self.assertEqual(len(rows), len(FIXTURES) + 6)
        # The pre-flight: the printed ids cannot tell these cards apart, which is
        # the index by id the page had, holding one card for each.
        self.assertLess(len({m["id"] for m in rows}), len(rows) - 3)
        keys = [m["key"] for m in rows]
        self.assertEqual(len(set(keys)), len(rows), keys)
        for key in keys:
            with self.subTest(key=key):
                self.assertIsInstance(key, str)
                self.assertTrue(key)

    def test_the_key_is_the_fixture_and_the_cards_place_among_its_cards(self):
        seen: dict[tuple, int] = {}
        for row in self.season["matches"]:
            fixture = (row["round"], row["home"], row["away"])
            seen[fixture] = seen.get(fixture, 0) + 1
            with self.subTest(source=row["source"]):
                self.assertEqual(row["key"], "/".join((*fixture, str(seen[fixture]))))
        # Both cards of the clash, which the fixture alone cannot tell apart.
        self.assertEqual([m["key"] for m in self.cards_of(self.NOID)],
                         ["4/lakeside-spinners/parkview-wanderers/1",
                          "4/lakeside-spinners/parkview-wanderers/2"])

    def test_each_fixture_lists_every_card_it_has_and_no_other(self):
        listed = []
        for fixture in self.season["fixtures"]:
            key = (fixture["round"], fixture["home"], fixture["away"])
            with self.subTest(fixture=key):
                self.assertEqual(fixture["keys"],
                                 [m["key"] for m in self.cards_of(key)])
                self.assertEqual(len(fixture["keys"]), fixture["cards"])
            listed.extend(fixture["keys"])
        # Every card under exactly one fixture, so a walk of the fixtures is a walk
        # of every card, the no-id card's and the clash's among them.
        self.assertEqual(sorted(listed), sorted(m["key"] for m in self.season["matches"]))

    def test_each_round_lists_every_card_it_has_in_the_documents_order(self):
        for rnd in self.season["rounds"]:
            with self.subTest(round=rnd["number"]):
                self.assertEqual(rnd["keys"],
                                 [m["key"] for m in self.season["matches"]
                                  if m["round"] == rnd["number"]])
                self.assertEqual(len(rnd["keys"]), rnd["cards"])
        # The late night last, where sorting the keys would put it first, and the
        # front page's latest round draws them in this order.
        five, = [r for r in self.season["rounds"] if r["number"] == "5"]
        self.assertEqual(five["keys"], ["5/riverside-rockets/parkview-wanderers/1",
                                        "5/hilltop-aces/lakeside-spinners/1"])
        self.assertNotEqual(five["keys"], sorted(five["keys"]))

    def test_the_printed_id_is_kept_as_the_card_printed_it(self):
        # The address of a night is its id, and a card that printed none has none.
        self.assertEqual([m["id"] for m in self.cards_of(self.NOID)], ["", ""])
        self.assertEqual([m["id"] for m in self.cards_of(self.ALONE)], [""])
        self.assertEqual([m["id"] for m in self.cards_of(self.BORROWED)], ["4800002"])

    def test_a_card_for_another_fixture_moves_no_key(self):
        """The same season a card short, as a folder is before a late card lands.

        Keyed by place, every card after the one missing would move, and a key is
        what the page and anybody reading two builds side by side find a card by.
        Here the round 1 card is the earliest of all, so a place moves every other.
        """
        late = stats.chronological(self.matches)[0]
        rest = [m for m in self.matches if m is not late]
        before = export.build_season(
            rest, competition=self.competition, source_dir=self.data,
            generated="2026-09-10T12:00:00", club=self.discovery.club,
            cards=self.cards, rejected=self.rejected)
        after = {m["source"]: m for m in self.season["matches"]}
        # The pre-flight: a place is not the same card's in the two.
        places = {m["source"]: at for at, m in enumerate(self.season["matches"])}
        moved = [m["source"] for at, m in enumerate(before["matches"])
                 if places[m["source"]] != at]
        self.assertEqual(len(moved), len(before["matches"]))
        for row in before["matches"]:
            with self.subTest(source=row["source"]):
                self.assertEqual(row["key"], after[row["source"]]["key"])

    def test_each_key_is_its_own_cards_whatever_order_the_cards_arrive_in(self):
        # Worked out for the list as a whole and zipped on to the rows, as the
        # reasons and the other cards are, so it is one list away from giving a
        # card another's key.
        season = export.build_season(
            list(reversed(self.matches)), competition=self.competition,
            source_dir=self.data, generated="2026-09-10T12:00:00",
            club=self.discovery.club, cards=self.cards, rejected=self.rejected)
        for fixture in (self.NOID, self.COPIED, self.ALONE):
            rows = self.cards_of(fixture, season)
            with self.subTest(fixture=fixture):
                self.assertEqual([m["key"] for m in rows],
                                 ["/".join((*fixture, str(n)))
                                  for n in range(1, len(rows) + 1)])
                listed, = [f["keys"] for f in season["fixtures"]
                           if (f["round"], f["home"], f["away"]) == fixture]
                self.assertEqual(listed, [m["key"] for m in rows])


class TestARoundSaysWhetherTheBuildReadItsLabelAsANumber(unittest.TestCase):
    """``rounds[].numbered``: the build's own answer, so the page keeps no rule of its own.

    The page titled a round ``Round 3`` and ``R3`` when its own test, ``/^[0-9]+$/``,
    took the label for a number, and the build reads it by
    :func:`~matchcentre.competition.round_number`, which takes a fullwidth ``５``, an
    Arabic-Indic ``٥`` and a mathematical ``𝟓`` for numbers too and more than 4300
    digits for a name. Measured before on the demo season with every round 5 card
    printing one of the first three (``/tmp/t7/scratch/E1/probe.py``): exit 0 and
    no F11 warning, the build scoring it as a numbered round, and in headless Chrome
    the front page's ``Latest round ５ · Wed 4 Mar 2026`` and a ``#/matches``
    heading ``５`` below ``Round 1`` to ``Round 4``. The page titled 4301 digits
    ``Round 111...``, which F11 calls a name. So each round says whether the build
    read its label as a number, and the page reads that. ``true`` or ``false``, never
    the week: the page asks yes or no, and round ``0``'s week is falsy. A final is
    numbered by its label, as any other round is. Rejected: widening the page's test
    to every decimal digit (``\\p{Nd}``), a second copy of the rule in a second
    language, which is how the two came apart.
    """

    NUMBERS = ("3", "03", "0", "５", "٥", "\U0001d7d3", "19")
    NAMES = ("³", "③", "¹²", "SF", "Catch-up", "1" * 4301)
    # A final the club declared by number, which a declared draw may have (F11),
    # and one whose label names a final.
    FINALS = ("19", "SF")

    @classmethod
    def setUpClass(cls):
        competition = Competition(
            id="x", day="Monday", division="C",
            fmt=fmt_mod.match_format("doubles-singles-doubles"),
            rules=scoring.ruleset("games-bonus"),
            rounds={label: Round(number=label, is_finals=label in cls.FINALS)
                    for label in cls.NUMBERS + cls.NAMES})
        season = export.build_season([], competition=competition,
                                     source_dir=Path("."), generated="")
        cls.rounds = {r["number"]: r for r in season["rounds"]}

    def test_a_label_of_decimal_digits_is_a_number(self):
        from matchcentre.competition import round_number
        # Zero among them, which is a number and is falsy, so a flag written as
        # the week's truth would call it a name. And round 19, a final, which the
        # page titled `Round 19` before the flag, as it titles any numbered round.
        self.assertIs(self.rounds["19"]["is_finals"], True)
        for label in self.NUMBERS:
            with self.subTest(label=label):
                # The pre-flight: the build reads it as one.
                self.assertIsNotNone(round_number(label))
                self.assertIs(self.rounds[label]["numbered"], True)

    def test_any_other_label_is_not_one(self):
        from matchcentre.competition import round_number
        # A superscript and a circled digit, which str.isdigit takes in, and digits
        # past what int will read, which str.isdecimal takes in.
        for label in self.NAMES:
            with self.subTest(label=label[:12]):
                self.assertIsNone(round_number(label))
                self.assertIs(self.rounds[label]["numbered"], False)


class TestEveryWayARoundCanEndUpWithAFixtureInItTwice(SeasonCase):
    """Three code paths build a round's fixtures, and this season goes through all.

    Rounds 1 to 3 are declared, and round 1 is declared with one of its fixtures
    written down twice -- a pasted line, or two spellings of one team that come out
    as the same team. The final is declared with no fixtures at all, which is what a
    club writes before it knows who will reach it, so its fixtures come off the
    cards; and there are two cards for it. Round 4 is not in the draw at all, and
    there are two cards for its one night as well.

    All three used to produce a fixture twice, which is an extra fixture nobody can
    ever play: the Draw page listed the night twice under its round, and "of N in the
    draw" -- the denominator a club reads its season's progress against -- counted 10
    where 8 were ever going to be played, so a finished season could never read
    finished.

    The club's own draw is the only one of the three whose repeat nothing else would
    ever mention, so it gets a note. The other two are two cards for a fixture, which
    is F9's to report, once.
    """

    FINAL, EXTRA = "F", "4"

    @classmethod
    def make_config(cls):
        # Names written the way a club writes them rather than the way the cards
        # shout them, because that is what a club types and it is the declared
        # spelling a note has to quote back.
        declared = config_mod.CompetitionConfig(
            id="tuesday-division-b", day="Tuesday", division="B",
            teams=("Hilltop Aces", "Lakeside Spinners", "Parkview Wanderers",
                   "Riverside Rockets"),
            rounds=(
                # Written the way a club writes it, with one line in twice.
                config_mod.RoundConfig(
                    number="1", date=DATES["1"],
                    fixtures=(("Lakeside Spinners", "Parkview Wanderers"),
                              ("Riverside Rockets", "Hilltop Aces"),
                              ("Riverside Rockets", "Hilltop Aces"))),
                config_mod.RoundConfig(
                    number="2", date=DATES["2"],
                    fixtures=(("Hilltop Aces", "Parkview Wanderers"),
                              ("Riverside Rockets", "Lakeside Spinners"))),
                config_mod.RoundConfig(
                    number="3", date=DATES["3"],
                    fixtures=(("Hilltop Aces", "Lakeside Spinners"),
                              ("Riverside Rockets", "Parkview Wanderers"))),
                config_mod.RoundConfig(number=cls.FINAL, date="2026-09-15",
                                       finals=True),
            ))
        return config_mod.Config(path=cls.root / "config.toml",
                                 competitions={declared.id: declared})

    @classmethod
    def extra_cards(cls):
        # Two identical copies of one night per round, which is the folder mistake
        # every club makes: exported again, suffixed by the browser, never deleted.
        for rnd, date, home, away, match_id in (
                (cls.FINAL, "2026-09-15", "riverside-rockets",
                 "lakeside-spinners", "4800001"),
                (cls.EXTRA, "2026-09-08", "lakeside-spinners",
                 "parkview-wanderers", "4900001")):
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            spec = cw.CardSpec(
                date=date, round_label=rnd, division="B", court="20",
                match_id=match_id, home_name=home_name, away_name=away_name,
                rows=rows_for(((6, 2), (6, 3), (6, 4), (6, 1)),
                              home_players, away_players),
                home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(home_players)),
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players)))
            folder = cls.data / f"Round{rnd}"
            folder.mkdir(parents=True, exist_ok=True)
            cw.write_card_file(folder / "card.pdf", spec)
            cw.write_card_file(folder / "card (1).pdf", spec)

    def notes_about_repeats(self) -> list[str]:
        return [n for n in self.discovery.notes if "more than once" in n]

    def test_the_draw_was_taken_up_and_all_three_paths_were_walked(self):
        # The pre-flight. Without this, every assertion below could be passing
        # about a draw that was never read.
        self.assertEqual(self.season["meta"]["draw_source"], DRAW_DECLARED)
        source = {n: r.source for n, r in self.competition.rounds.items()}
        self.assertEqual(source, {"1": DRAW_DECLARED, "2": DRAW_DECLARED,
                                  "3": DRAW_DECLARED, self.FINAL: "cards",
                                  self.EXTRA: "cards"})

    def test_the_line_the_club_wrote_twice_is_one_fixture(self):
        self.assertEqual(
            self.competition.rounds["1"].fixtures,
            (("lakeside-spinners", "parkview-wanderers"),
             ("riverside-rockets", "hilltop-aces")))

    def test_and_the_club_is_told_which_line_to_delete(self):
        # A declared draw is the club's own statement, so a repeat in it is not
        # quietly corrected: nothing else in the program would ever mention it, and
        # the file looks right when the cause is two spellings of one team.
        note, = self.notes_about_repeats()
        self.assertIn("round 1", note)
        self.assertIn("Riverside Rockets v Hilltop Aces", note)
        self.assertIn("[[competitions.tuesday-division-b.rounds]]", note)
        self.assertIn("two spellings of one team", note)

    def test_a_final_whose_pairings_came_off_the_cards_is_one_fixture(self):
        self.assertEqual(self.competition.rounds[self.FINAL].fixtures,
                         (("riverside-rockets", "lakeside-spinners"),))

    def test_a_round_the_draw_never_mentioned_is_one_fixture(self):
        self.assertEqual(self.competition.rounds[self.EXTRA].fixtures,
                         (("lakeside-spinners", "parkview-wanderers"),))

    def test_the_repeats_the_cards_caused_are_not_mentioned_twice(self):
        # One mistake reported once: the two duplicated nights are F9 warnings
        # naming both files each, and a note here would have a club hunting two
        # problems per night.
        self.assertEqual(len(self.notes_about_repeats()), 1)
        self.assertEqual([i.rule for i in self.report.issues].count("F9"), 2)

    def test_the_denominator_the_page_counts_progress_against_is_the_draw(self):
        fixtures = self.season["fixtures"]
        keys = [(f["round"], f["home"], f["away"]) for f in fixtures]
        self.assertEqual(sorted(keys), sorted(set(keys)),
                         "a fixture is in the document twice")
        self.assertEqual(len(fixtures), 8)
        # And the two nights with two cards each say so on their own row, which is
        # how a reader gets from one fixture back to two files.
        doubled = [f for f in fixtures if f["cards"] == 2]
        self.assertEqual(sorted(f["round"] for f in doubled),
                         [self.EXTRA, self.FINAL])


class TestLastSeasonsCardsAreNotThisSeasonsDuplicates(SeasonCase):
    """A club's own archive under ``data/`` in single-season mode, and what it hears.

    The tidiest thing a club can do with two years of exports is what this season
    does: last year's cards moved into ``data/2025/``, this year's left where they
    were. In single-season mode every one of them is filed onto one ladder, so last
    year's round 1 lands on this year's round 1 -- and the two things that then go
    wrong are the two shapes a repeated draw takes. A fixture played in both years is
    two cards for one fixture, which disagree, so neither is counted. A fixture that
    was in last year's draw and not in this one is a team down to play twice in a
    night, which is counted twice.

    Both were reported before this, and neither was explained. F9 named the two files
    and said to remove the one that is wrong; the round check said a team's points
    would be counted twice and stopped there. Every card here is a real Match Centre
    export of a real night, so "remove the file that is wrong" is advice to delete a
    season, and the club that follows it loses last year to fix this year.

    What all of it comes from is one line that is not in ``config.toml``, and the
    evidence for that is on the cards: one round cannot have been played in two
    different years. So the years are named -- by the note, before any of it is an
    error, and by both errors, in place of the sentence about deleting a file.

    The note allows for the other reading in one sentence, because a summer
    competition really does run from one year into the next, and a club whose season
    crosses New Year must not be told its own layout is a mistake.
    """

    YEAR = "2025"
    WHEN = "2025-08-19"                                  # a Tuesday, as all are
    # Last year's round 1: one fixture this year's draw repeats, and one it does not.
    REPEAT = ("lakeside-spinners", "parkview-wanderers")
    REPEAT_SCORES = ((6, 1), (6, 2), (6, 1), (6, 3))     # 24-7, not this year's
    FRESH = ("riverside-rockets", "lakeside-spinners")
    FRESH_SCORES = ((6, 3), (6, 2), (4, 6), (6, 1))

    @classmethod
    def extra_cards(cls):
        folder = cls.data / cls.YEAR / "Round1"
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        for index, ((home, away), scores) in enumerate((
                (cls.REPEAT, cls.REPEAT_SCORES), (cls.FRESH, cls.FRESH_SCORES))):
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            home_rows, home_on_court = squad_for(home, home_players, None)
            away_rows, away_on_court = squad_for(away, away_players, None)
            written.append(cw.write_card_file(folder / f"{home}-{away}.pdf", cw.CardSpec(
                date=cls.WHEN, round_label="1", division="B",
                court=f"{20 + index}", match_id=f"{cls.YEAR}000{index}",
                home_name=home_name, away_name=away_name,
                rows=rows_for(scores, home_on_court, away_on_court),
                home_roster=home_rows, away_roster=away_rows)))
        cls.repeated, cls.fresh = written

    def note(self) -> str:
        """The one note that names the line to write.

        Found by that line rather than by position, because the whole complaint is
        that what a club was told named no line at all -- so a note that does name
        it cannot be confused with the six this season already prints.
        """
        found = [n for n in self.discovery.notes if 'seasons = "multi"' in n]
        self.assertEqual(len(found), 1, f"expected one note, got {found}")
        return found[0]

    def issue(self, rule: str):
        found = [i for i in self.report.issues if i.rule == rule]
        self.assertEqual(len(found), 1, [i.message for i in found])
        return found[0]

    def test_both_years_were_read_and_filed_onto_one_ladder(self):
        # The pre-flight. Nothing is rejected and nothing is split: one competition,
        # and a round 1 holding twice what the draw has in it.
        self.assertEqual(len(self.cards), len(FIXTURES) + 2)
        self.assertEqual(self.rejected, [])
        self.assertEqual(len(self.discovery.matches), 1)
        first = [m for m in self.matches if m.round == "1"]
        self.assertEqual(len(first), 4)
        self.assertEqual({m.date[:4] for m in first}, {"2025", "2026"})

    def test_a_note_names_the_round_with_two_years_in_it(self):
        note = self.note()
        self.assertIn("round 1", note)
        self.assertIn("2025", note)
        self.assertIn("2026", note)

    def test_it_names_the_file_and_the_line_that_separate_them(self):
        note = self.note()
        self.assertIn(str(self.config.path), note)
        self.assertIn("[build]", note)

    def test_it_leaves_room_for_a_season_that_ran_across_new_year(self):
        # A summer competition is not a mistake, and the note is a note: it says
        # which reading makes this right before it says what to change.
        self.assertIn("New Year", self.note())

    def test_nothing_says_the_ladder_is_double_counting_the_repeat(self):
        # It is not: two cards that disagree are counted for nobody. The note that
        # said otherwise sat one screen above a ladder that proved it wrong, and
        # said it about the one pair of cards that are two seasons rather than two
        # exports of one night.
        for note in self.discovery.notes:
            self.assertNotIn("double-counting", note)
        spinners, = [t for t in self.season["teams"]
                     if t["slug"] == "lakeside-spinners"]
        self.assertEqual(spinners["played"], 3)

    def test_the_repeated_fixture_is_still_an_error_naming_both_files(self):
        issue = self.issue("F9")
        self.assertEqual(issue.severity, validate.ERROR)
        self.assertIn(str(self.repeated), issue.detail)
        self.assertIn("neither is counted", issue.message)

    def test_that_error_says_the_two_cards_were_played_in_different_years(self):
        message = self.issue("F9").message
        self.assertIn("2025", message)
        self.assertIn("2026", message)

    def test_it_does_not_ask_for_one_of_the_two_seasons_to_be_deleted(self):
        message = self.issue("F9").message
        for word in ("delete", "remove", "leaves data/"):
            with self.subTest(word=word):
                self.assertNotIn(word, message)

    def test_it_names_the_setting_that_separates_them_instead(self):
        self.assertIn('seasons = "multi"', self.issue("F9").message)

    def test_a_team_down_to_play_twice_in_a_night_is_told_the_same_thing(self):
        # The other shape, and the same cause. Two fixtures, so F9 cannot see it.
        issues = [i for i in self.report.issues if i.rule == "team-twice-in-round"]
        self.assertEqual(len(issues), 2)
        for issue in issues:
            with self.subTest(subject=issue.subject):
                self.assertIn("2025", issue.message)
                self.assertIn('seasons = "multi"', issue.message)

    def test_one_season_of_the_same_cards_is_told_none_of_it(self):
        # The measurement that keeps the note honest for every other club: it is the
        # second year that produces it, not the folder, the round numbers or the
        # fixture list.
        cards = [c for c in self.cards if self.YEAR not in c.source]
        found = discover_mod.discover(cards, [], config=self.config, root=self.data)
        self.assertEqual([n for n in found.notes if 'seasons = "multi"' in n], [])
        comp_id, = sorted(found.matches)
        competition, = [c for c in found.club.competitions if c.id == comp_id]
        report = validate.validate(found.matches[comp_id], competition,
                                   club=found.club, cards=cards)
        self.assertEqual([i.rule for i in report.issues if i.rule in
                          ("F9", "team-twice-in-round")], [])

    def test_and_neither_is_the_run_that_has_the_line_already(self):
        config = dataclasses.replace(self.config, seasons_mode="multi")
        found = discover_mod.discover(self.cards, [], config=config, root=self.data)
        self.assertEqual([n for n in found.notes if 'seasons = "multi"' in n], [])
        # Four, and this is the measurement that settles how the note reads its
        # evidence. With the mode set, a season is the folder directly under data/ --
        # so this layout becomes 2025 plus one competition per round folder, which is
        # not what the club wants either and is a different note's business. A note
        # that had counted folders instead of years would have called this season's
        # own three round folders three seasons.
        self.assertEqual(len(found.matches), 4)


class TestAnArchiveOverlappingTwoRoundsNamesBothOfThem(SeasonCase):
    """One note for the folder, listing every round of it, in draw order.

    The class above is the small case: one night of last season kept beside this
    one. A club that keeps a season keeps all of it, so the note holds a list rather
    than a round -- and a list has two ways to come out wrong that one round cannot
    show. A note per crossed round would say the same sentence over and over about a
    folder with one thing wrong with it, which is the rule this project holds itself
    to (one mistake, reported once) said about its own advice. And a list in the
    order the cards happened to be read says "rounds 3 and 1" to a club that thinks
    in draw order, so these two are written in exactly that order.
    """

    YEAR = "2025"
    # Both repeat a fixture this season plays in the same round, so each is a pair of
    # cards for one fixture rather than a team down to play twice.
    ARCHIVED = (("3", "2025-09-02", ("hilltop-aces", "lakeside-spinners")),
                ("1", "2025-08-19", ("lakeside-spinners", "parkview-wanderers")))
    SCORES = ((6, 1), (6, 2), (6, 1), (6, 3))    # 24-7, neither round's real result

    @classmethod
    def extra_cards(cls):
        for index, (rnd, when, (home, away)) in enumerate(cls.ARCHIVED):
            folder = cls.data / cls.YEAR / f"Round{rnd}"
            folder.mkdir(parents=True, exist_ok=True)
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            home_rows, home_on_court = squad_for(home, home_players, None)
            away_rows, away_on_court = squad_for(away, away_players, None)
            cw.write_card_file(folder / f"{home}-{away}.pdf", cw.CardSpec(
                date=when, round_label=rnd, division="B",
                court=f"{30 + index}", match_id=f"{cls.YEAR}100{index}",
                home_name=home_name, away_name=away_name,
                rows=rows_for(cls.SCORES, home_on_court, away_on_court),
                home_roster=home_rows, away_roster=away_rows))

    def notes(self) -> list[str]:
        return [n for n in self.discovery.notes if "more than one year" in n]

    def test_both_rounds_are_named_in_draw_order_as_one_sentence(self):
        self.assertIn("rounds 1 and 3 are built from cards played in more than one "
                      "year (2025 and 2026)", self.notes()[0])

    def test_one_folder_gets_one_note_and_not_one_per_round(self):
        self.assertEqual(len(self.notes()), 1, self.notes())


class TestTheArchiveAdviceReplacesEveryVersionOfGetRidOfAFile(SeasonCase):
    """The other two shapes of ``F9``, with a year between the two cards.

    F9 has three sentences of advice, and the ordinary archive -- last season's
    result for a fixture the draw runs again -- only ever reaches one of them, the
    one where the two cards disagree. The other two are just as reachable from a
    folder holding two seasons, and both of them said to get rid of a file:
    "Delete the spare file from data/" where the cards say the same thing, and
    "Everybody's record for that night comes back when the card that is wrong leaves
    data/" where they agree about the result and not about who played. Last season's
    card is not spare and is not wrong, so each is advice to throw away a night that
    was played, sitting under an id (``F9``) a club is told to quote.

    Two archived cards, because each lands in a different branch and one build can
    hold both: round 1's repeated exactly, scores and names, which is what a club
    gets when last season's export was taken twice; and round 2's repeated with this
    season's scoreline under a name that is not on this season's card, which is last
    season's card beside a corrected copy of itself.
    """

    YEAR = "2025"
    SAME = ("1", "2025-08-19", ("lakeside-spinners", "parkview-wanderers"))
    NAMED = ("2", "2025-08-26", ("hilltop-aces", "parkview-wanderers"))
    # A name on last season's card that is on nothing else, which is the whole
    # difference between the two branches: the scoreline is this season's either way.
    INSTEAD = Swap(1, "Robin Quaife")

    @classmethod
    def extra_cards(cls):
        for index, (rnd, when, sides) in enumerate((cls.SAME, cls.NAMED)):
            scores, = [f[3] for f in FIXTURES if (f[0], f[1], f[2]) == (rnd, *sides)]
            rename = {sides[0]: cls.INSTEAD} if (rnd, when, sides) == cls.NAMED \
                else None
            folder = cls.data / cls.YEAR / f"Round{rnd}"
            folder.mkdir(parents=True, exist_ok=True)
            home, away = sides
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            home_rows, home_on_court = squad_for(home, home_players, rename)
            away_rows, away_on_court = squad_for(away, away_players, rename)
            cw.write_card_file(folder / f"{home}-{away}.pdf", cw.CardSpec(
                date=when, round_label=rnd, division="B",
                court=f"{40 + index}", match_id=f"{cls.YEAR}200{index}",
                home_name=home_name, away_name=away_name,
                rows=rows_for(scores, home_on_court, away_on_court),
                home_roster=home_rows, away_roster=away_rows))

    def f9(self, round_number: str) -> validate.Issue:
        found = [i for i in self.report.issues
                 if i.rule == "F9" and i.subject.startswith(f"R{round_number} ")]
        self.assertEqual(len(found), 1, [i.message for i in found])
        return found[0]

    def test_the_pair_that_says_the_same_thing_twice_is_still_only_a_warning(self):
        # The severity is the part that must not move: every figure on the page is
        # right, so a club has nothing to fix tonight and is not stopped.
        self.assertEqual(self.f9(self.SAME[0]).severity, validate.WARNING)

    def test_and_that_warning_names_the_years_instead_of_a_file_to_delete(self):
        message = self.f9(self.SAME[0]).message
        self.assertIn("2025 and 2026", message)
        self.assertNotIn("spare", message)

    def test_the_pair_that_agrees_about_the_result_names_the_years_too(self):
        message = self.f9(self.NAMED[0]).message
        self.assertIn("disagree about who played", message)
        self.assertIn("2025 and 2026", message)

    def test_and_does_not_say_a_record_comes_back_when_a_card_leaves(self):
        # The sentence it replaced, quoted the way a club would read it: the night
        # comes back by filing the seasons apart, not by a file leaving data/.
        self.assertNotIn("leaves data/", self.f9(self.NAMED[0]).message)

    def test_the_years_say_it_so_the_folders_do_not_say_it_again(self):
        # Both repeats have a card in data/2025 and one in data/Round1 or Round2,
        # folders a season could be, so the note about folders would be said here
        # too, beside the one about years, and for the same line of config.
        self.assertEqual(len([n for n in self.discovery.notes
                              if "more than one year" in n]), 1)
        self.assertEqual([n for n in self.discovery.notes
                          if "as seasons filed side by side would" in n], [])


EVERY = tuple(range(len(FIXTURES)))
# Each of DATES 52 weeks earlier: the same weekday, and the year before.
A_YEAR_EARLIER = {"1": "2025-08-19", "2": "2025-08-26", "3": "2025-09-02"}


class SeasonFoldersCase(unittest.TestCase):
    """Cards filed where ``LAYOUT`` says, discovered in single-season mode.

    Each entry is ``(folder under data/, division, which FIXTURES, name suffix)``,
    the fixtures written into ``Round<n>`` folders under the folder, or directly
    under data/ when the folder is ``""``. Every card of a fixture prints the same
    date, result and id wherever it is filed, as the same export filed twice does,
    except that a ``(folder, division)`` in ``EARLIER`` is dated a year before.
    """

    LAYOUT: tuple[tuple[str, str, tuple[int, ...], str], ...] = ()
    EARLIER: tuple[tuple[str, str], ...] = ()

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / "data"
        for folder, division, which, suffix in cls.LAYOUT:
            for index in which:
                rnd, home, away, scores = FIXTURES[index]
                home_rows, home_on_court = squad_for(home, TEAMS[home][1])
                away_rows, away_on_court = squad_for(away, TEAMS[away][1])
                place = cls.data / folder / f"Round{rnd}" if folder else cls.data
                place.mkdir(parents=True, exist_ok=True)
                dates = A_YEAR_EARLIER if (folder, division) in cls.EARLIER else DATES
                cw.write_card_file(place / f"{home}-{away}{suffix}.pdf", cw.CardSpec(
                    date=dates[rnd], round_label=rnd, division=division,
                    court=f"{index + 1:02d}",
                    match_id=f"47{ord(division)}{index:03d}",
                    home_name=TEAMS[home][0], away_name=TEAMS[away][0],
                    rows=rows_for(scores, home_on_court, away_on_court),
                    home_roster=home_rows, away_roster=away_rows))
        cls.cards, cls.rejected = scorecard.read_tree(cls.data)
        cls.config = config_mod.Config(path=cls.root / "config.toml")
        cls.discovery = discover_mod.discover(cls.cards, cls.rejected,
                                              config=cls.config, root=cls.data)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def folder_notes(self) -> list[str]:
        return [n for n in self.discovery.notes
                if "as seasons filed side by side would" in n]

    def repeats(self) -> list[validate.Issue]:
        """F9's findings about fixtures, from every competition of the run."""
        found = []
        for competition in self.discovery.club.competitions:
            report = validate.validate(self.discovery.matches[competition.id],
                                       competition, club=self.discovery.club,
                                       cards=self.cards, rejected=self.rejected)
            found += [i for i in report.issues
                      if i.rule == "F9" and "cards claim round" in i.message]
        return found

    def the_note(self, count: int,
                 folders: str = "2025-winter and 2026-winter") -> str:
        """The note, as it reads for *count* fixtures in *folders*."""
        data, config = self.data, self.root / "config.toml"
        if count == 1:
            opening = "The one fixture more than one card claims has"
            reported, stands = "it", "the fixture"
        else:
            opening = (f"Every fixture more than one card claims — {count} of "
                       f"them — has")
            reported, stands = "each of them", "each fixture"
        return (f"{opening} cards in more than one folder directly under {data} "
                f"({folders}), as seasons filed side by side would. Single-season "
                f"mode puts every card under {data} on one ladder, so F9 reports "
                f"{reported} as cards claiming one fixture. If those folders are "
                f"seasons, set seasons = \"multi\" under [build] in {config}: each "
                f"folder directly under {data} is then a season with its own "
                f"ladder, and no card has to be deleted to separate them. If they "
                f"are not seasons, what F9 says about {stands} stands.")


class TestTwoSeasonFoldersBuiltAsOneAreToldTheSettingOnce(SeasonFoldersCase):
    """Two seasons filed side by side under single-season mode, told only to delete.

    A club that files each season in a folder of its own and never sets the mode
    has every repeated fixture of the two seasons on one ladder. When the two
    seasons' cards give their fixtures different years, the note about years says
    what to set (:class:`TestTheArchiveAdviceReplacesEveryVersionOfGetRidOfAFile`).
    When they do not, nothing did: measured before on the demo written twice, into
    data/2025-winter and data/2026-winter (``/tmp/t7/scratch/DUP/runs/before/
    two-folders``), the run exited 0 with 15 F9 warnings, each ending "Delete the
    spare file from data/ to keep the folder honest.", and ``seasons = "multi"``
    nowhere in its 345 lines. The same held for the demo's own two-season folder
    built without its config (``demo-multi``): both seasons are 2026, and the
    14 F9 errors each said to remove a file from data/.

    So discover says it once, in a note, when every fixture more than one card
    claims has cards in more than one folder directly under the data folder: the
    folders that setting would make seasons. F9 does not read the folders, because a
    folder is not a result and the club may have filed a card twice. Rejected:
    letting F9 read the folders as the years are read, which would call data/Round1
    against data/2025 two seasons and tell a club with one to set the mode; and a
    note per fixture, fifteen notes for one line of config.
    """

    LAYOUT = (("2025-winter", "B", EVERY, ""), ("2026-winter", "B", EVERY, ""))

    def test_every_repeat_is_still_reported_as_it_was(self):
        # The pre-flight, and the half of the defect this leaves alone: nothing but
        # the cards says a result, so each repeat is still a copy to F9.
        self.assertEqual(self.rejected, [])
        repeats = self.repeats()
        self.assertEqual(len(repeats), len(FIXTURES))
        self.assertEqual({i.severity for i in repeats}, {validate.WARNING})
        for issue in repeats:
            self.assertIn(" Delete the spare file ", issue.message)
        self.assertEqual([n for n in self.discovery.notes
                          if "more than one year" in n], [])

    def test_one_note_names_the_folders_and_the_line_that_separates_them(self):
        self.assertEqual(self.folder_notes(), [self.the_note(len(FIXTURES))])


class TestOneFixtureFiledInTwoFoldersIsSpokenOfAsOne(SeasonFoldersCase):
    """The same note when the second folder holds one night so far.

    The day a club starts a new season folder is the day it has one repeat, and
    "every fixture more than one card claims, 1 of them" is a sentence about a list
    that is not there. Rejected: leaving the one alone to a later season with more
    repeats, which is the season the club has already been told to delete from.
    """

    LAYOUT = (("2025-winter", "B", (0,), ""), ("2026-winter", "B", EVERY, ""))

    def test_the_note_speaks_of_one_fixture(self):
        self.assertEqual(len(self.repeats()), 1)
        self.assertEqual(self.folder_notes(), [self.the_note(1)])


class TestARepeatInsideOneFolderIsNoSignOfTwoSeasons(SeasonFoldersCase):
    """Five fixtures across the two folders and one filed twice inside one of them.

    A copy in one folder is a copy whatever the mode, so setting it would leave
    that finding standing, and a note saying the folders explain the repeats would
    be wrong about one of them. The note is said only when every repeat is one the
    setting would separate. Rejected: saying it when most of them are, which puts a
    count to a guess.
    """

    LAYOUT = (("2025-winter", "B", EVERY[:-1], ""), ("2026-winter", "B", EVERY, ""),
              ("2026-winter", "B", EVERY[-1:], " (1)"))

    def test_no_note_says_the_folders_are_seasons(self):
        self.assertEqual(len(self.repeats()), len(FIXTURES))
        self.assertEqual(self.folder_notes(), [])


class TestAThirdCardOfAFixtureInOneOfTheFoldersLeavesTheNoteStanding(SeasonFoldersCase):
    """Two seasons, and one of this season's cards exported twice as well.

    The setting still separates the fixture's cards into two seasons, and the one
    left with two cards is F9's to report afterwards, as a copy. Measured before
    (``two-folders-plus-copy``): 15 F9 warnings, one of them for three cards, and
    no word of seasons. Rejected: asking every two cards of a fixture to sit in
    different folders, which says nothing to the club with the most to separate.
    """

    LAYOUT = (("2025-winter", "B", EVERY, ""), ("2026-winter", "B", EVERY, ""),
              ("2026-winter", "B", (0,), " (1)"))

    def test_the_note_is_said_for_every_fixture(self):
        self.assertEqual(sorted(len(i.detail.split("; ")) for i in self.repeats()),
                         [2] * (len(FIXTURES) - 1) + [3])
        self.assertEqual(self.folder_notes(), [self.the_note(len(FIXTURES))])


class TestACardDirectlyInTheDataFolderIsInNoFolderASeasonCouldBe(SeasonFoldersCase):
    """One season in a folder, and a card of it again directly under data/.

    In multi-season mode a card with no folder is filed by its date, which puts it
    in this season again, beside its copy, so the setting would change nothing.
    Rejected: counting data/ itself as a folder, which is how this layout comes
    to be told it holds two seasons.
    """

    LAYOUT = (("2026-winter", "B", EVERY, ""), ("", "B", (0,), ""))

    def test_no_note_says_the_folders_are_seasons(self):
        self.assertEqual(len(self.repeats()), 1)
        self.assertEqual(self.folder_notes(), [])


class TestSeasonFoldersOneLevelDownAreNotWhatTheSettingReads(SeasonFoldersCase):
    """Two seasons filed in data/archive/2025-winter and data/archive/2026-winter.

    The setting makes seasons of the folders directly under data/, so this layout
    is one season, archive, either way. Telling the club to set it is advice that
    changes nothing. Measured before (``two-folders-nested``): the same 15 F9
    warnings as two folders side by side.
    """

    LAYOUT = (("archive/2025-winter", "B", EVERY, ""),
              ("archive/2026-winter", "B", EVERY, ""))

    def test_no_note_says_the_folders_are_seasons(self):
        self.assertEqual(len(self.repeats()), len(FIXTURES))
        self.assertEqual(self.folder_notes(), [])


class TestTwoDivisionsInTwoSeasonFoldersAreToldOnceBetweenThem(SeasonFoldersCase):
    """The two seasons hold two divisions each, so two competitions have repeats.

    One line of config separates both, so it is said once for the run and counts
    every repeat in it. Measured before (``two-folders-two-divisions``): 30 F9
    warnings across two competitions. Rejected: the note from each competition,
    where the note about years is made, which says one mistake twice.
    """

    LAYOUT = (("2025-winter", "B", EVERY, ""), ("2025-winter", "C", EVERY, "-c"),
              ("2026-winter", "B", EVERY, ""), ("2026-winter", "C", EVERY, "-c"))

    def test_one_note_counts_the_repeats_of_both(self):
        self.assertEqual(len(self.discovery.club.competitions), 2)
        self.assertEqual(len(self.repeats()), 2 * len(FIXTURES))
        self.assertEqual(self.folder_notes(), [self.the_note(2 * len(FIXTURES))])


class TestTwoFoldersTheSettingReadsAsOneSeasonAreNotToldToSetIt(SeasonFoldersCase):
    """``2026 Winter`` and ``2026-winter``: two folders, and one season to the mode.

    The setting makes a season of each folder directly under data/ by the key its
    name gives, and both of these names give ``2026-winter``. Measured on the demo
    written into both (``/tmp/t7/scratch/DUP/runs/after/two-slugs``): exit 0, 15 F9
    warnings and no note, and with ``seasons = "multi"`` written, exit 0, one
    competition, 2026-winter-wednesday-division-3, and the same 15 warnings. So the
    folders are told apart by the season each gives, as the mode tells them apart.
    Rejected: telling them apart by name, which asks the club for a line that
    separates nothing.
    """

    LAYOUT = (("2026 Winter", "B", EVERY, ""), ("2026-winter", "B", EVERY, ""))

    def test_no_note_says_the_folders_are_seasons(self):
        # The pre-flight: the line the note would name, written, leaves every
        # repeat where it was.
        multi = discover_mod.discover(
            self.cards, self.rejected, root=self.data,
            config=config_mod.Config(path=self.root / "config.toml",
                                     seasons_mode="multi"))
        competition, = multi.club.competitions
        self.assertEqual(competition.season_key, "2026-winter")
        self.assertEqual(len(stats.duplicates(
            multi.matches[competition.id]).copies), len(FIXTURES))
        self.assertEqual(len(self.repeats()), len(FIXTURES))
        self.assertEqual(self.folder_notes(), [])


class TestThreeSeasonFoldersAreAllNamedThoughNoFixtureIsInAllThree(SeasonFoldersCase):
    """Round 1 in 2024-winter and 2025-winter, and rounds 2 and 3 in 2025 and 2026.

    Every repeat has its cards in two folders, and not every repeat in the same
    two, so the folders the note names are those of every repeat between them.
    Measured on the demo (``/tmp/t7/scratch/DUP/runs/after/three-folders-
    staggered``, rounds 1 and 2 in 2024-winter and 2025-winter, rounds 3 to 5 in
    2025-winter and 2026-winter): one note, ``— 15 of them —``, naming
    ``2024-winter, 2025-winter and 2026-winter``. Rejected: the folders of the
    first repeat the note reads, which are every folder only when every repeat is
    in the same ones, and here leave 2026-winter out.
    """

    LAYOUT = (("2024-winter", "B", (0, 1), ""), ("2025-winter", "B", EVERY, ""),
              ("2026-winter", "B", EVERY[2:], ""))

    def test_the_note_names_every_folder_a_repeat_is_in(self):
        self.assertEqual(len(self.repeats()), len(FIXTURES))
        self.assertEqual(self.folder_notes(), [self.the_note(
            len(FIXTURES), folders="2024-winter, 2025-winter and 2026-winter")])


class TestAYearsNoteForEitherDivisionKeepsTheFoldersQuiet(SeasonFoldersCase):
    """Two divisions in two season folders, and only the second's cards give years.

    Division C's 2025-winter cards are dated a year before its 2026-winter ones, so
    its rounds cross two years and the note about years is said for it, asking for
    ``seasons = "multi"``. Division B's repeats give no years, and every one of them
    is in the two folders, which that line separates too. Measured on the demo
    (``/tmp/t7/scratch/DUP/runs/after/two-divisions-years-second``, division 4's
    2025-winter cards 52 weeks early): exit 0, 745 lines, the note about years once,
    for wednesday-division-4, and no note about folders. Rejected: asking the
    first competition alone whether the years were said, which here asks for the
    same line a second time, in the folders' words.
    """

    LAYOUT = (("2025-winter", "B", EVERY, ""), ("2025-winter", "C", EVERY, "-c"),
              ("2026-winter", "B", EVERY, ""), ("2026-winter", "C", EVERY, "-c"))
    EARLIER = (("2025-winter", "C"),)

    def test_the_second_divisions_years_are_the_one_note_said(self):
        first, second = self.discovery.club.competitions
        years = [n for n in self.discovery.notes if "more than one year" in n]
        self.assertEqual(len(years), 1, years)
        self.assertTrue(years[0].startswith(f"{second.id}: "), years[0])
        self.assertEqual(len(self.repeats()), 2 * len(FIXTURES))
        self.assertEqual(self.folder_notes(), [])


class TestARoundWashedOutInDecemberAndReplayedInJanuaryIsOneSeason(SeasonCase):
    """The false positive the archive advice would otherwise hand a summer club.

    Two cards for round 4: the night it was washed out, in December, and the night it
    was replayed, in January. Two calendar years, one season, and one of the two
    cards really is the one to get rid of -- so this is the case where "remove the
    file that is wrong from data/" is right and where being told to split the season
    in two instead would be a tool telling a club to reorganise its whole folder
    because it rained in December.

    It is also the shape a summer competition has every year, which is why the check
    reads only cards that count as played
    (:func:`~matchcentre.competition.years_played`). A washout is a club's own note
    about a round and is evidence of nothing else. Both the note and the finding ask
    the same way, so this fixture holds both of them to it: neither may say a word
    about seasons here.
    """

    ROUND = "4"
    WASHED = "2026-12-15"                 # a Tuesday, as every date here is
    REPLAYED = "2027-01-12"               # four Tuesdays later, the year after
    SIDES = ("riverside-rockets", "hilltop-aces")
    SCORES = ((6, 2), (6, 4), (3, 6), (6, 1))

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        home, away = cls.SIDES
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on_court = squad_for(home, home_players, None)
        away_rows, away_on_court = squad_for(away, away_players, None)
        for index, when in enumerate((cls.WASHED, cls.REPLAYED)):
            washout = when == cls.WASHED
            extra = ({"cancelled": True, "verb": "VS", "winner": cw.NO_TICK}
                     if washout else {})
            cw.write_card_file(
                folder / ("washed-out.pdf" if washout else "replayed.pdf"),
                cw.CardSpec(
                    date=when, round_label=cls.ROUND, division="B",
                    court=f"{50 + index}", match_id=f"205000{index}",
                    home_name=home_name, away_name=away_name,
                    rows=rows_for(cls.SCORES, home_on_court, away_on_court),
                    home_roster=home_rows, away_roster=away_rows, **extra))

    def test_the_two_cards_are_read_as_two_cards_for_one_fixture(self):
        # The pre-flight: without this the class proves nothing, because a fixture
        # whose cards were filed apart is never asked the question at all.
        fourth = [m for m in self.matches if m.round == self.ROUND]
        self.assertEqual(len(fourth), 2)
        self.assertEqual({m.date for m in fourth}, {self.WASHED, self.REPLAYED})
        self.assertEqual({m.status for m in fourth},
                         {MatchStatus.CANCELLED, MatchStatus.PLAYED})

    def test_no_note_says_the_round_holds_two_seasons(self):
        self.assertEqual([n for n in self.discovery.notes
                          if "more than one year" in n], [])

    def test_the_finding_still_names_the_file_to_get_rid_of(self):
        # And says it the way it always did. The advice this item added replaces that
        # sentence, so a fixture that must not get the advice is also the fixture
        # that proves the sentence it replaces is still there to be had.
        message, = [i.message for i in self.report.issues
                    if i.rule == "F9" and i.subject.startswith(f"R{self.ROUND} ")]
        self.assertIn("remove the file that is wrong from data/", message)
        self.assertNotIn("more than one season is filed", message)


class TestAClubThatHasAlreadySetTheModeIsNotToldToSetIt(SeasonCase):
    """Two years in one season folder, with the mode on: the finding, and no note.

    The note exists because single-season mode files every card under ``data/`` onto
    one ladder, and its sentence is about the one line that changes that. Setting the
    line does not make this layout impossible: the mode makes each *folder* a season,
    not each year, so a club can write ``seasons = "multi"`` and still export last
    season's cards into this season's folder. Then every word of the note is wrong
    twice over -- it says single-season mode is in force, and it asks for a setting
    the club is already using. A run that tells a club to do what it has done reads
    as a run that has not noticed.

    The class above measures the same guard with a layout that cannot hold it to
    anything: with the mode set, last season's folder becomes a season of its own, so
    no competition there has two years in it and the note stays quiet for that reason
    instead of this one. One folder holding both years is the case that can tell the
    difference.

    What the club gets instead is the finding: two cards claim round 1, neither is
    counted, and both files are named. One mistake, reported once, by the check that
    can see the fixture rather than by the note that can only see a folder. The
    finding used to read no config at all, and so it said the note's sentence in the
    note's place -- "single-season mode puts every card under data/ on one ladder
    ... Set seasons = "multi"" -- on the F9 and on each team down twice in round 1:
    measured on /tmp/t4/scratch/i417/m417b, three findings asking for the line the
    club had written. It is told the mode now, and what it asks for in this one is the
    half still undone, a folder per season.

    Rejected: a second note worded for multi-season mode -- "this folder holds two
    years, so split it" -- which would repeat what the finding already says about the
    only round that collides, for a folder whose other rounds are nobody's problem. A
    club reading two voices about one folder goes looking for two mistakes.
    """

    SEASON = "2026"
    ARCHIVED = ("1", "2025-08-19", ("lakeside-spinners", "parkview-wanderers"))
    SCORES = ((6, 1), (6, 2), (6, 1), (6, 3))    # not round 1's real result
    # Last season's other round 1 card, a pairing this season's round 1 does not
    # have: no second F9, and the Rockets down to play twice in the night.
    PAIRED = ("riverside-rockets", "lakeside-spinners")

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml",
                                 seasons_mode="multi")

    @classmethod
    def extra_cards(cls):
        season = cls.data / cls.SEASON
        season.mkdir()
        for folder in sorted(cls.data.glob("Round*")):
            shutil.move(str(folder), str(season / folder.name))
        rnd, when, (home, away) = cls.ARCHIVED
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on_court = squad_for(home, home_players, None)
        away_rows, away_on_court = squad_for(away, away_players, None)
        cls.archived = season / f"Round{rnd}" / "last-season.pdf"
        cw.write_card_file(cls.archived, cw.CardSpec(
            date=when, round_label=rnd, division="B", court="40",
            match_id="20251040", home_name=home_name, away_name=away_name,
            rows=rows_for(cls.SCORES, home_on_court, away_on_court),
            home_roster=home_rows, away_roster=away_rows))
        home, away = cls.PAIRED
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on_court = squad_for(home, home_players, None)
        away_rows, away_on_court = squad_for(away, away_players, None)
        cw.write_card_file(season / f"Round{rnd}" / "last-season-2.pdf", cw.CardSpec(
            date=when, round_label=rnd, division="B", court="41",
            match_id="20251041", home_name=home_name, away_name=away_name,
            rows=rows_for(cls.SCORES, home_on_court, away_on_court),
            home_roster=home_rows, away_roster=away_rows))

    def test_the_one_folder_is_one_season_holding_two_years(self):
        # The pre-flight. Without this the class proves nothing: a folder per year is
        # the layout the note is quiet about for a reason that is not the mode.
        self.assertTrue(self.comp_id.startswith(f"{self.SEASON}-"), self.comp_id)
        archived_round = [m for m in self.matches if m.round == self.ARCHIVED[0]]
        self.assertEqual({m.date[:4] for m in archived_round}, {"2025", "2026"})

    def test_no_note_asks_for_the_line_the_club_has_written(self):
        self.assertEqual([n for n in self.discovery.notes
                          if "more than one year" in n], [])

    def test_the_fixture_the_two_years_collide_over_is_still_reported(self):
        # The silence above is about one note, not about the run. This is the mistake
        # a club can act on, and it is named without the config being read at all.
        issue, = [i for i in self.report.issues if i.rule == "F9"]
        self.assertIn("neither is counted", issue.message)
        self.assertIn(str(self.archived), issue.detail)
        self.assertEqual(issue.detail.count(".pdf"), 2, issue.detail)

    def advice(self, rule: str, subject: str) -> str:
        """One finding's message as the season document carries it.

        The document's copy and not the report's, because it is the one a club reads:
        build.py prints the findings from it and the page shows them from it, and it
        is written by a second caller that has to hand the mode on as well.
        """
        message, = [i["message"] for i in self.season["validation"]["errors"]
                    if i["rule"] == rule and i["subject"] == subject]
        return message

    def assert_asks_for_a_folder_and_not_the_mode(self, message: str) -> None:
        self.assertIn("2025 and 2026", message)
        self.assertIn("a folder of its own under data/", message)
        self.assertNotIn('seasons = "multi"', message)
        self.assertNotIn("single-season mode", message)

    def test_the_finding_asks_for_the_folder_and_not_the_line_it_has(self):
        home, away = self.ARCHIVED[2]
        self.assert_asks_for_a_folder_and_not_the_mode(
            self.advice("F9", f"R1 {home} v {away}"))

    def test_so_does_a_team_down_to_play_twice_in_the_round(self):
        # A second caller of the same sentence, and so a second place to hand the
        # mode to: the Rockets play this season's round 1 and last season's.
        self.assert_asks_for_a_folder_and_not_the_mode(
            self.advice("team-twice-in-round", f"R1 {self.PAIRED[0]}"))


class TestADisputedNameCostsThePlayersTheNightAndNotTheTeams(SeasonCase):
    """The same scoreline twice, with a different name in one rubber.

    The realistic version of this is a club fixing a card in Match Centre -- a name
    that went in as `Unspecified Player`, or the wrong Modric -- and exporting it
    again beside the original. Every total agrees; who was on court does not.

    Two questions, and the cards answer one of them twice over. *What was the
    result?* Both cards say 24-10 to the spinners, so the night is counted once and
    the ladder is the ladder. *Who played?* The cards disagree, so no appearance is
    credited for it: every figure a person owns is short one night, the Share column
    no longer adds to a hundred, and the page says why.

    Two alternatives were rejected. Counting nothing at all -- which is what this did
    until now -- throws away a result neither card disputes, and a club that corrected
    a spelling loses a night from the ladder as the price. Keeping whichever card was
    read first credits a real person with a rubber the other card says somebody else
    played, which is the guess the Share column was changed to stop making.

    A third was rejected more reluctantly: credit the appearances *both* cards agree
    on, and drop only the rubber they differ over. That is the finest-grained true
    answer -- eleven of twelve rubbers here are not in dispute -- but it needs a
    merged card that exists in no file, printed on the page under a `Source:` line
    naming one PDF whose own second singles reads differently. Every row on that page
    would have to be trusted a little less to save one night's appearances.
    """

    ROUND = "4"
    HOME, AWAY = "lakeside-spinners", "hilltop-aces"
    SCORES = ((6, 2), (6, 3), (6, 4), (6, 1))            # 24-10, sets 4-0
    SWAPPED = "Ilya Modric"                  # for Hana Wex, in the second singles
    MATCH_ID = "4800001"

    # The night counted once, on top of the three rounds worked out at the top of
    # this file: spinners 15.8 + (4 + 2.4), aces 10.7 + (2 + 1.0).
    POINTS = dict(EXPECTED_POINTS, **{"lakeside-spinners": 22.2,
                                      "hilltop-aces": 13.7})
    GAMES = dict(EXPECTED_GAMES_FOR, **{"lakeside-spinners": 82,
                                        "hilltop-aces": 47})
    # Credited to people: the three rounds only, for both sides of the night.
    CREDITED = {"lakeside-spinners": 58, "hilltop-aces": 37}

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = TEAMS[cls.AWAY]
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        first_rows = rows_for(cls.SCORES, home_players, away_players)
        second_rows = tuple(
            dataclasses.replace(row, home_players=(cls.SWAPPED,))
            if index == 2 else row
            for index, row in enumerate(first_rows))
        written = []
        for name, rows in (("as-entered.pdf", first_rows),
                           ("corrected.pdf", second_rows)):
            written.append(cw.write_card_file(folder / name, cw.CardSpec(
                date="2026-09-08", round_label=cls.ROUND, division="B",
                court="16", match_id=cls.MATCH_ID,
                home_name=home_name, away_name=away_name, rows=rows,
                home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(home_players)),
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players)))))
        cls.first, cls.second = written

    def rows(self) -> list[dict]:
        return [m for m in self.season["matches"] if m["round"] == self.ROUND]

    def test_the_two_cards_agree_about_every_number(self):
        # The pre-flight. If this fails the case under test is not the one set up.
        totals = {(m["home_games"], m["away_games"], m["home_sets"],
                   m["away_sets"], m["result"]) for m in self.rows()}
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(totals), 1)

    def test_and_disagree_about_who_played_the_second_singles(self):
        named = [{p for s in m["sets"] if s["slot"] == "s2"
                  for p in s["home_players"]} for m in self.rows()]
        self.assertEqual(sorted(named, key=sorted),
                         [{"hana-wex"}, {"ilya-modric"}])

    def test_the_result_neither_card_disputes_is_counted_once(self):
        for slug, points in self.POINTS.items():
            with self.subTest(team=slug):
                row, = [t for t in self.season["teams"] if t["slug"] == slug]
                self.assertEqual(row["games_won"], self.GAMES[slug])
                self.assertAlmostEqual(row["points"], points, places=6)
        # The order does not move, which is the ladder rule working rather than the
        # night being ignored: positions are ranked on points *average*, and the
        # spinners' extra 6.4 points came with an extra match to divide them by.
        # The figures above are where the night shows up.
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)

    def test_and_the_ladder_now_says_it_is_a_snapshot(self):
        # The honest consequence of counting it: two teams have played four matches
        # and two have played three, so the table is a mid-round picture and F10
        # says so. Counting nothing would have kept the flag true by keeping the
        # field level -- tidier, and a round further from what happened.
        self.assertFalse(self.season["meta"]["ladder_complete"])
        self.assertTrue(stats.ladder_complete(
            [m for m in self.matches if m.key[0] != self.ROUND],
            self.competition))

    def test_it_is_counted_once_and_not_twice(self):
        # The other way to get the result wrong. Both cards say 24-10, so scoring
        # both would read as a fourth win and 24 more games for a night that
        # happened once.
        spinners, = [t for t in self.season["teams"]
                     if t["slug"] == self.HOME]
        self.assertEqual(spinners["played"], 4)
        self.assertEqual(spinners["won"], 3)

    def test_the_two_teams_have_met_twice_and_not_three_times(self):
        # Round 3 and round 4, in the pairwise record as in the ladder.
        record = self.season["head_to_head"][stats.h2h_key(self.HOME, self.AWAY)]
        self.assertEqual(record["played"], 2)
        self.assertEqual((record["wins_low"], record["wins_high"]), (0, 2))

    def test_neither_player_is_credited_with_the_rubber(self):
        # Hana Wex played the first three rounds and Ilya Modric has not played at
        # all, so a fourth match on either record could only have come from a card
        # the other card contradicts.
        for slug, before in (("hana-wex", 3), ("ilya-modric", 0)):
            with self.subTest(player=slug):
                player, = [p for p in self.season["players"]
                           if p["slug"] == slug]
                self.assertEqual(player["matches"], before)
                self.assertEqual(player["sets_played"], before * 3)

    def test_nor_is_anybody_else_on_either_side_of_it(self):
        # Not only the rubber in dispute and not only the home side: the cards
        # disagree about the night, and a card is the unit a name is read from.
        # Gil Ahearn's doubles and Dev Raman's singles are in neither card's
        # dispute, and both lose the night with everybody else.
        for slug in ("gil-ahearn", "hana-wex", "dev-raman", "elin-sotto"):
            with self.subTest(player=slug):
                player, = [p for p in self.season["players"]
                           if p["slug"] == slug]
                self.assertEqual(player["matches"], 3)

    def test_the_share_column_says_so_by_not_adding_up(self):
        # The same signal as a rubber with a name cell empty: the team's games are
        # all there, and the column that divides them among people is short a
        # night. A club reading down it can see that something is unattributed.
        for slug, credited in self.CREDITED.items():
            with self.subTest(team=slug):
                total = sum(p["contribution"]["games_won"]
                            for p in self.season["players"]
                            if p["team"] == slug)
                self.assertAlmostEqual(total, credited, places=6)
                self.assertLess(total, self.GAMES[slug])

    def test_one_row_is_the_night_and_the_other_is_the_reading_not_used(self):
        counted = [m for m in self.rows() if m["counts_for_ladder"]]
        spare = [m for m in self.rows() if not m["counts_for_ladder"]]
        self.assertEqual(len(counted), 1)
        self.assertEqual(len(spare), 1)
        self.assertEqual(spare[0]["not_counted"], "names")
        # And the counted row carries the other half of the answer: its figures are
        # in the ladder and nobody was credited from it. One field per question,
        # because "counted" and "credited" now have different answers on one card.
        self.assertEqual(counted[0]["not_counted"], "")
        self.assertEqual(counted[0]["not_credited"], "names")
        # The spare answers both questions the same way, which is the only row in
        # the document where the two fields repeat each other. Left repeating rather
        # than left blank: a page asking "did this card credit anybody" of every row
        # would read a blank as yes and list a person's night twice.
        self.assertEqual(spare[0]["not_credited"], "names")

    def test_and_the_seasons_totals_are_built_from_the_night_they_settled(self):
        # The third question, and the one row in the document where it and the
        # players' question differ: the kept card's scoreline is the night, so its
        # sets and games are in the front page's totals, and its names are in
        # nobody's record. A page that filtered those totals by the players' field
        # would drop a night both teams played out of the sets it says were played.
        counted, = [m for m in self.rows() if m["counts_for_ladder"]]
        spare, = [m for m in self.rows() if not m["counts_for_ladder"]]
        self.assertEqual(counted["not_totalled"], "")
        self.assertEqual(counted["not_credited"], "names")
        self.assertEqual(spare["not_totalled"], "names")

    def test_the_counted_card_still_prints_the_names_it_read(self):
        # Which of the two is kept is arbitrary, and that is only acceptable
        # because nothing that differs between them is counted. The row keeps its
        # own reading so the page never shows a name the PDF under `Source:` does
        # not print -- a merged card would have been the fabrication.
        counted, = [m for m in self.rows() if m["counts_for_ladder"]]
        named = {p for s in counted["sets"] if s["slot"] == "s2"
                 for p in s["home_players"]}
        self.assertIn(named, [{"hana-wex"}, {"ilya-modric"}])
        self.assertTrue(counted["source"].endswith(".pdf"))

    def test_the_error_says_which_half_was_counted_and_which_was_not(self):
        issue, = [i for i in self.report.issues if i.rule == "F9"]
        self.assertEqual(issue.severity, validate.ERROR)
        self.assertIn("who played", issue.message)
        self.assertNotIn("the games", issue.message)
        self.assertIn(str(self.first), issue.detail)
        self.assertIn(str(self.second), issue.detail)
        # Both halves, because a club told only that a night was refused will go
        # looking for a missing result, and a club told only that it was counted
        # will not know why the players' rows are short.
        self.assertIn("counted", issue.message)
        self.assertIn("no appearance", issue.message)
        self.assertNotIn("neither is counted", issue.message)

    def test_it_is_still_an_error_with_an_exit_code_behind_it(self):
        # The ladder is right and somebody's season is not, which only the club can
        # fix. A warning here would be the dashboard settling for it.
        self.assertFalse(self.report.ok)
        self.assertEqual({i.rule for i in self.report.errors}, {"F9"})
        self.assertEqual([e["rule"] for e in self.season["validation"]["errors"]],
                         ["F9"])


class TestACardWithAnotherClubsLetterheadIsScoredAndNamed(SeasonCase):
    """A card from somebody else's competition, in the same division on the same night.

    Routing is by what the card prints -- its division and the weekday of its own
    date -- so a PDF exported from another club's Tuesday B division lands in this
    one, names two teams nobody here has heard of, and takes a ladder position. The
    letterhead is the only thing on the card that says otherwise, and it was being
    settled by a majority vote whose losers were thrown away.

    It is a warning and not an error, and the card is still scored. A letterhead is
    a *label*: the same club can print two spellings of its own name, and a season
    that stopped building over one of those would be a season a club cannot build.
    What it is owed is the path, both spellings, and the fact that the result is in
    the ladder below.
    """

    ROUND = "4"
    OURS = "EXAMPLE DISTRICT TENNIS CLUB INC"        # what the season's cards print
    THEIRS = ("NORTHGATE LAWN TENNIS", "ASSOCIATION")
    FOREIGN = (("NORTHGATE NIGHTHAWKS", ("Wynn Talbot", "Xan Ruiz")),
               ("SANDHILL SWIFTS", ("Yves Marchetti", "Zia Kovac")))

    @classmethod
    def extra_cards(cls):
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        (home_name, home_players), (away_name, away_players) = cls.FOREIGN
        cls.foreign = cw.write_card_file(folder / "northgate.pdf", cw.CardSpec(
            club_lines=cls.THEIRS,
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="12", match_id="4500001",
            home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 3), (6, 2), (6, 4), (6, 1)),
                          home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))
        # And one of this club's own cards whose letterhead did not read at all: a
        # blank is not a second club, and must not be reported as one.
        home_name, home_players = TEAMS["lakeside-spinners"]
        away_name, away_players = TEAMS["hilltop-aces"]
        cls.headless = cw.write_card_file(folder / "no-letterhead.pdf", cw.CardSpec(
            club_lines=(),
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="13", match_id="4500002",
            home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (4, 6), (6, 3), (6, 4)),
                          home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def findings(self) -> list:
        return [i for i in self.report.issues if i.rule == "foreign-card"]

    def test_the_cards_say_what_the_test_needs_them_to_say(self):
        # The pre-flight: one letterhead of its own, one that did not read, and the
        # rest this club's.
        hints = {Path(c.source).name: c.club_hint for c in self.cards}
        self.assertEqual(hints["northgate.pdf"], " ".join(self.THEIRS))
        self.assertEqual(hints["no-letterhead.pdf"], "")
        self.assertEqual({h for name, h in hints.items()
                          if name not in ("northgate.pdf", "no-letterhead.pdf")},
                         {self.OURS})

    def test_the_card_is_scored_and_its_teams_reach_the_ladder(self):
        # Which is the cost being reported. Refusing it would be the other fault:
        # a card thrown away over a label nobody declared.
        self.assertEqual(self.season["meta"]["cards_scored"], len(FIXTURES) + 2)
        self.assertIn("northgate-nighthawks",
                      {t["slug"] for t in self.season["teams"]})

    def test_one_finding_names_the_card_and_both_letterheads(self):
        issue, = self.findings()
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertIn(str(self.foreign), issue.subject)
        self.assertIn(" ".join(self.THEIRS), issue.message)
        self.assertIn(self.OURS, issue.message)

    def test_it_says_the_result_is_in_the_ladder_and_how_to_take_it_out(self):
        issue, = self.findings()
        self.assertIn("ladder", issue.message)
        self.assertIn("data", issue.message)

    def test_a_letterhead_that_did_not_read_is_not_a_second_club(self):
        for issue in self.findings():
            with self.subTest(subject=issue.subject):
                self.assertNotIn(str(self.headless), issue.subject)

    def test_this_clubs_own_cards_are_not_named(self):
        issue, = self.findings()
        for card in self.cards:
            if card.source not in (str(self.foreign), str(self.headless)):
                with self.subTest(card=card.source):
                    self.assertNotIn(card.source, issue.subject)

    def test_a_label_never_fails_a_build(self):
        self.assertTrue(self.report.ok)


class TestALetterheadDifferingOnlyInCaseIsTheClubsOwnSpelling(unittest.TestCase):
    """Capitals and spacing are how a club printed its name, not which club it is.

    The letterhead check compared cards on the letterhead with its spacing folded
    and its case kept. The demo's own-card test and the fetcher's keep test compare
    through ``card.letterhead_key``, which folds both. Measured on the demo season
    with some of its cards' letterheads changed, each built at exit 0
    (``/tmp/t4/scratch/d447/measure.py``):

      one card of 15 in title case, or in lower case   a seventh warning,
                                                       foreign-card, "the other 14"
      7 of the 15 in title case                        those 7 named
      another club's 2 cards, one in title case        two findings for one club
      the club's 9 in three cases, another club's 6    all 9 of the club's named,
                                                       under the other's name

    Now cards are grouped by ``letterhead_key``, the rule the other two ask. A
    letterhead named is quoted in every spelling its cards print, most printed
    first, and the detail counts each. The rest is quoted in the spelling most of
    its cards print, and counted by the cards that print it that way, out of all of
    its cards where some print it otherwise. A tie of spellings goes to the one read
    first, and cards are read in the order of their paths. So does a tie of
    letterheads, in ``card.majority_letterhead``, which ``discover._club_name`` asks
    for the name it offers the config stub as the club's.

    Rejected: a fold of this check's own with case added. It would be a second
    definition of "which club printed this card", and the fetcher's tests hold that
    the check asks the shared one. Rejected too, each measured on the demo split 7
    to 7 while ``_club_name`` still counted spellings by a rule of its own: a tie
    of letterheads settled by read order, and one settled on the spelling alone.
    The first named the cards whose letterhead the same report offered as the
    club's name, with the demo's own read first. The second named them in either
    read order, once the other club printed its name in capitals on four cards and
    in title case on three. Now the two ask one function, and the first is its
    tie-break. Rejected as well: a letterhead named
    quoted in its most-printed spelling alone, which put a spelling in quotes that
    one of the cards it named did not print.
    """

    COMPETITION = Competition(id="tuesday-division-c", day="Tuesday", division="C",
                              fmt=fmt_mod.match_format("doubles-singles-doubles"),
                              rules=scoring.ruleset("games-bonus"))
    OURS = "EXAMPLE DISTRICT TENNIS CLUB INC"
    TITLE = "Example District Tennis Club Inc"
    LOWER = "example district tennis club inc"
    THEIRS = "NORTHGATE LAWN TENNIS ASSOCIATION"

    @staticmethod
    def cards(*hints: str) -> list[Card]:
        """One card per letterhead, in the order given, which is the order read."""
        return [Card(source=f"data/Round1/card-{n:02}.pdf", club_hint=hint)
                for n, hint in enumerate(hints)]

    def findings(self, cards: list[Card]) -> list:
        report = validate.validate([], self.COMPETITION, cards=cards)
        return [i for i in report.issues if i.rule == "foreign-card"]

    @staticmethod
    def named(*cards: Card) -> str:
        """The cards a finding names, listed as a sentence lists them."""
        sources = [card.source for card in cards]
        return (f"{', '.join(sources[:-1])} and {sources[-1]}" if len(sources) > 1
                else sources[0])

    def test_a_card_printing_the_name_in_other_capitals_is_not_reported(self):
        for name, hints in (
                ("one card of 15 in title case", (self.TITLE,) + (self.OURS,) * 14),
                ("one card of 15 in lower case",
                 (self.OURS,) * 5 + (self.LOWER,) + (self.OURS,) * 9),
                ("7 of 15 in title case", (self.TITLE,) * 7 + (self.OURS,) * 8),
                ("other spacing and other capitals",
                 (self.OURS,) * 14 + (" example  District tennis\tCLUB inc ",)),
                ("three ways, five each",
                 (self.OURS, self.TITLE, self.LOWER) * 5)):
            with self.subTest(name):
                self.assertEqual(self.findings(self.cards(*hints)), [])

    def test_another_club_printing_its_name_two_ways_is_one_finding(self):
        # One card to each spelling, so the title case, read first, is quoted first.
        theirs = self.THEIRS.title()
        cards = self.cards(*(self.OURS,) * 13, theirs, self.THEIRS)
        issue, = self.findings(cards)
        self.assertEqual(issue.subject, self.named(*cards[13:]))
        self.assertTrue(issue.message.startswith(
            f"{issue.subject}: these cards' letterheads read {theirs!r} or "
            f"{self.THEIRS!r}, where the other 13 card(s) scored as "
            f"tuesday-division-c read {self.OURS!r}. "), issue.message)
        self.assertEqual(issue.detail, f"1 card(s) print {theirs!r}, "
                                       f"1 card(s) print {self.THEIRS!r}")

    def test_the_clubs_cards_are_counted_by_letterhead_and_not_by_spelling(self):
        # Nine of this club's, three to a spelling, against six of another's: the
        # six are the ones named, and the rest is this club.
        cards = self.cards(*(self.OURS,) * 3, *(self.TITLE,) * 3, *(self.LOWER,) * 3,
                           *(self.THEIRS,) * 6)
        issue, = self.findings(cards)
        self.assertEqual(issue.subject, self.named(*cards[9:]))
        self.assertIn(f"where 3 of the other 9 card(s) scored as tuesday-division-c "
                      f"read {self.OURS!r}.", issue.message)

    def test_the_rest_is_quoted_as_most_of_it_prints_and_counted_by_that(self):
        # Four in title case, read first, and ten in capitals: the capitals are the
        # ones quoted, and the ten that print them are the count, of fourteen.
        cards = self.cards(*(self.TITLE,) * 4, *(self.OURS,) * 10, self.THEIRS)
        issue, = self.findings(cards)
        self.assertIn(f"where 10 of the other 14 card(s) scored as tuesday-division-c "
                      f"read {self.OURS!r}.", issue.message)

    def test_two_spellings_printed_as_often_go_to_the_one_read_first(self):
        for first, second in ((self.OURS, self.TITLE), (self.TITLE, self.OURS)):
            with self.subTest(first=first):
                cards = self.cards(*(first,) * 7, *(second,) * 7, self.THEIRS)
                issue, = self.findings(cards)
                self.assertIn(f"where 7 of the other 14 card(s) scored as "
                              f"tuesday-division-c read {first!r}.", issue.message)

    def test_every_spelling_another_club_prints_is_quoted_most_printed_first(self):
        # Its title case is read first and its capitals printed twice, once with
        # spacing of their own, which is not a spelling.
        theirs = self.THEIRS.title()
        spaced = " NORTHGATE  LAWN TENNIS\tASSOCIATION "
        cards = self.cards(*(self.OURS,) * 12, theirs, self.THEIRS, spaced)
        issue, = self.findings(cards)
        self.assertEqual(issue.subject, self.named(*cards[12:]))
        self.assertTrue(issue.message.startswith(
            f"{issue.subject}: these cards' letterheads read {self.THEIRS!r} or "
            f"{theirs!r}, where the other 12 card(s) "), issue.message)
        self.assertEqual(issue.detail, f"2 card(s) print {self.THEIRS!r}, "
                                       f"1 card(s) print {theirs!r}")

    def test_two_letterheads_on_as_many_cards_are_settled_as_the_club_name_is(self):
        # Seven to each, read in either order. The rest is the letterhead read
        # first, however it is spelt, and another club's four in capitals and
        # three in title case, read first, are quoted in the capitals four print.
        split = (self.THEIRS,) * 4 + (self.THEIRS.title(),) * 3
        for name, hints, rest, others in (
                ("ours then theirs", (self.OURS,) * 7 + (self.THEIRS,) * 7,
                 self.OURS, "the other 7"),
                ("theirs then ours", (self.THEIRS,) * 7 + (self.OURS,) * 7,
                 self.THEIRS, "the other 7"),
                ("lower then theirs", (self.LOWER,) * 7 + (self.THEIRS,) * 7,
                 self.LOWER, "the other 7"),
                ("theirs then lower", (self.THEIRS,) * 7 + (self.LOWER,) * 7,
                 self.THEIRS, "the other 7"),
                ("ours then theirs four and three", (self.OURS,) * 7 + split,
                 self.OURS, "the other 7"),
                ("theirs four and three then ours", split + (self.OURS,) * 7,
                 self.THEIRS, "4 of the other 7")):
            with self.subTest(name):
                cards = self.cards(*hints)
                self.assertEqual(discover_mod._club_name({"": cards}), rest)
                issue, = self.findings(cards)
                self.assertEqual(issue.subject, self.named(
                    *(card for card in cards
                      if card.club_hint.upper() != rest.upper())))
                self.assertIn(f"where {others} card(s) scored as "
                              f"tuesday-division-c read {rest!r}.", issue.message)


class TestTheClubNameAndTheCheckTakeTheClubsLetterheadByOneRule(unittest.TestCase):
    """The name offered as the club's is chosen by the rule the check keeps it by.

    ``discover._club_name`` counted exact spellings over every competition and broke
    a tie on the spelling's code points. The letterhead check counted cards by
    ``card.letterhead_key`` within one competition and broke a tie on the count of
    each letterhead's most-printed spelling, and then on that spelling. Measured on
    the demo season with some of its cards' letterheads changed, each built at exit
    0 (``/tmp/t7/scratch/LH/measure.py``), and in process
    (``/tmp/t7/scratch/LH/inproc.py``):

      the club's 9 in three cases and     the six named, and the report's
      another club's 6                    note, ``meta.club``, ``<title>`` and
                                          the stub the other club's
      7 in capitals read first, 7 in      the check quoting the capitals, and
      title case, another club's 1        the name the title case
      two divisions, 15 in capitals       the name the title case, on its code
      read first, 15 in title case        points
      3 in capitals and 3 in title case   the check naming those six in two of
      against 3, 2 and 1 of another       three read orders, while the name
      club's                              was their title case in all three

    Now both ask ``card.majority_letterhead``: the letterhead the most cards print,
    by ``letterhead_key``, and the spelling the most of those cards print, spacing
    folded, a tie of either going to the one read first. The check asks it of one
    competition's cards, as before, and ``_club_name`` of every competition's, in
    the order their paths and then their pages are read, because a club has one
    ``[club] name``.

    Rejected: a tie broken on code points, which makes capitals against title case
    decide the club's name. Rejected too: the name counted per competition, which
    leaves the one line of the stub several answers. A season can still hold a
    competition most of whose cards are another club's, whose check names the
    club's own: it is the one rule, asked of other cards.
    """

    COMPETITION = TestALetterheadDifferingOnlyInCaseIsTheClubsOwnSpelling.COMPETITION
    OURS = "EXAMPLE DISTRICT TENNIS CLUB INC"
    TITLE = "Example District Tennis Club Inc"
    LOWER = "example district tennis club inc"
    THEIRS = "NORTHGATE LAWN TENNIS ASSOCIATION"
    # Chosen so that its capitals sort below THEIRS's and its title case above.
    NOMINAL = "NOMINAL EXAMPLE TENNIS CLUB"

    @staticmethod
    def cards(*hints: str, folder: str = "data/Round1") -> list[Card]:
        """One card per letterhead, in the order given, which is the order read."""
        return [Card(source=f"{folder}/card-{n:02}.pdf", club_hint=hint)
                for n, hint in enumerate(hints)]

    def kept(self, cards: list[Card]) -> tuple[str, list[str]]:
        """The letterhead the check quotes as the club's, and the cards it names."""
        report = validate.validate([], self.COMPETITION, cards=cards)
        issue, = [i for i in report.issues if i.rule == "foreign-card"]
        quoted = re.search(r"scored as tuesday-division-c read '([^']*)'\.",
                           issue.message)
        self.assertIsNotNone(quoted, issue.message)
        return quoted.group(1), [card.source for card in cards
                                 if card.source in issue.subject]

    def test_the_clubs_cards_counted_by_letterhead_give_the_name(self):
        # The row's nine in three cases and six of another club's: the name is the
        # club's, in the capitals read first, and the six are the ones named.
        cards = self.cards(*(self.OURS,) * 3, *(self.TITLE,) * 3, *(self.LOWER,) * 3,
                           *(self.THEIRS,) * 6)
        self.assertEqual(discover_mod._club_name({"": cards}), self.OURS)
        self.assertEqual(self.kept(cards),
                         (self.OURS, [card.source for card in cards[9:]]))

    def test_a_tie_of_spellings_goes_to_the_one_read_first_in_the_name_too(self):
        for first, second in ((self.OURS, self.TITLE), (self.TITLE, self.OURS)):
            with self.subTest(first=first):
                cards = self.cards(*(first,) * 7, *(second,) * 7, self.THEIRS)
                self.assertEqual(discover_mod._club_name({"": cards}), first)
                self.assertEqual(self.kept(cards)[0], first)

    def test_the_spelling_most_printed_is_the_name_however_late_it_is_read(self):
        # Two in title case read first and five in capitals, against another
        # club's six; and two in capitals, two in title case and three in lower
        # case, against another club's seven read after them, as a season of the
        # demo measured: the name and the rest the check quotes are the spelling
        # the most of the club's cards print, not the first of them read.
        for name, hints, club in (
                ("title case read first",
                 (self.TITLE,) * 2 + (self.OURS,) * 5 + (self.THEIRS,) * 6, self.OURS),
                ("lower case read last",
                 (self.OURS,) * 2 + (self.TITLE,) * 2 + (self.LOWER,) * 3
                 + (self.THEIRS,) * 7, self.LOWER)):
            with self.subTest(name):
                cards = self.cards(*hints)
                self.assertEqual(discover_mod._club_name({"": cards}), club)
                self.assertEqual(self.kept(cards), (club, [
                    card.source for card in cards if card.club_hint == self.THEIRS]))

    def test_the_name_and_the_check_agree_however_the_spellings_sort(self):
        # Six to each letterhead, read in three orders: the first read is the name
        # and the rest, in the spelling it prints most, and the other is named.
        theirs = ((self.THEIRS,) * 3 + (self.THEIRS.title(),) * 2
                  + (self.THEIRS.lower(),))
        nominal = (self.NOMINAL,) * 3 + (self.NOMINAL.title(),) * 3
        for name, hints, club in (
                ("its capitals read first", nominal + theirs, self.NOMINAL),
                ("the other club read first", theirs + nominal, self.THEIRS),
                ("its title case read first",
                 nominal[3:] + nominal[:3] + theirs, self.NOMINAL.title())):
            with self.subTest(name):
                cards = self.cards(*hints)
                self.assertEqual(discover_mod._club_name({"": cards}), club)
                self.assertEqual(self.kept(cards), (club, [
                    card.source for card in cards
                    if card_mod.letterhead_key(card.club_hint)
                    != card_mod.letterhead_key(club)]))

    def test_the_name_is_counted_over_every_competition(self):
        # Wednesday's eight of another club's are its majority, and not the club's:
        # the club's seven there and fifteen on Thursday are.
        wednesday = self.cards(*(self.THEIRS,) * 8, *(self.OURS,) * 7,
                               folder="data/a-wed")
        thursday = self.cards(*(self.OURS,) * 15, folder="data/b-thu")
        self.assertEqual(discover_mod._club_name(
            {"wednesday-division-3": wednesday, "thursday-division-b": thursday}),
            self.OURS)

    def test_the_competitions_are_read_in_the_order_of_their_paths(self):
        # Thursday's id sorts first, and discovery hands its cards over first, but
        # Wednesday's paths are read first: a tie over the two goes to Wednesday's.
        wednesday = self.cards(*(self.OURS,) * 15, folder="data/a-wed")
        thursday = self.cards(*(self.TITLE,) * 15, folder="data/b-thu")
        self.assertEqual(discover_mod._club_name(
            {"thursday-division-b": thursday, "wednesday-division-3": wednesday}),
            self.OURS)

    def test_a_files_pages_are_read_before_the_next_files(self):
        # One PDF whose first page's letterhead did not read and whose second
        # prints the club's name in capitals, and a second PDF printing it in
        # title case, handed over last first: the capitals are read first.
        cards = [Card(source="data/Round1/a.pdf", page=0, club_hint=""),
                 Card(source="data/Round1/a.pdf", page=1, club_hint=self.OURS),
                 Card(source="data/Round1/b.pdf", page=0, club_hint=self.TITLE)]
        self.assertEqual(discover_mod._club_name({"": cards[::-1]}), self.OURS)

    def test_a_letterhead_that_did_not_read_is_no_club(self):
        cards = self.cards("", " ", "", self.TITLE, self.OURS)
        self.assertEqual(discover_mod._club_name({"": cards}), self.TITLE)
        self.assertEqual(card_mod.majority_letterhead(cards), (self.OURS, self.TITLE))
        self.assertEqual(card_mod.majority_letterhead(self.cards("", " ")), ("", ""))
        self.assertEqual(discover_mod._club_name({"": self.cards("", " ")}), "")

    def test_one_function_answers_for_the_name_and_for_the_check(self):
        self.assertIs(discover_mod.majority_letterhead, card_mod.majority_letterhead)
        self.assertIs(validate.majority_letterhead, card_mod.majority_letterhead)
        cards = self.cards(*(self.OURS,) * 14, self.THEIRS)
        for module, ask in (
                (discover_mod, lambda: discover_mod._club_name({"": cards})),
                (validate, lambda: validate.validate([], self.COMPETITION,
                                                     cards=cards))):
            with (self.subTest(module.__name__),
                  mock.patch.object(module, "majority_letterhead",
                                    wraps=card_mod.majority_letterhead) as spy):
                ask()
                self.assertEqual([list(call.args[0]) for call in spy.call_args_list],
                                 [cards], f"{module.__name__} settles the club's "
                                          f"letterhead its own way")


class TestAForeignCardFindingListsItsCardsAndSpellingsAsASentence(unittest.TestCase):
    """Three cards and three spellings are listed as every other list is.

    ``_check_letterhead`` joined the spellings with `` or `` alone and the cards with
    commas, and said "this card's letterhead" however many cards it named. Measured
    in process (``/tmp/t7/scratch/LH/inproc.py``) on twelve of the club's cards and
    three of another club's, in capitals, title case and lower case: "... card-13.pdf,
    data/Round1/card-14.pdf: this card's letterhead reads 'NORTHGATE LAWN TENNIS
    ASSOCIATION' or 'Northgate Lawn Tennis Association' or 'northgate lawn tennis
    association', where the other 12 card(s) ...". It went on "It has been scored
    and its result is in the ladder" and "take it out of data/", of three cards.

    Now both lists are joined by ``scope.in_words``, the spellings with ``or``, and
    the message is worded for one card or for several from the count. The subject is
    the list the message opens with, so no reader prints it twice. The detail is a
    list of counts, not a sentence, and keeps its commas.

    Rejected: the hand joins left as they were, a second rule for a list. And "these
    cards'" alone, which left "It has been scored" after it.
    """

    COMPETITION = TestALetterheadDifferingOnlyInCaseIsTheClubsOwnSpelling.COMPETITION
    OURS = "EXAMPLE DISTRICT TENNIS CLUB INC"
    THEIRS = "NORTHGATE LAWN TENNIS ASSOCIATION"
    cards = staticmethod(TestALetterheadDifferingOnlyInCaseIsTheClubsOwnSpelling.cards)
    named = staticmethod(TestALetterheadDifferingOnlyInCaseIsTheClubsOwnSpelling.named)

    def finding(self, *theirs: str):
        cards = self.cards(*(self.OURS,) * 12, *theirs)
        report = validate.validate([], self.COMPETITION, cards=cards)
        issue, = [i for i in report.issues if i.rule == "foreign-card"]
        return issue

    def test_three_cards_in_three_spellings_are_listed_and_spoken_of_as_three(self):
        issue = self.finding(self.THEIRS, self.THEIRS.title(), self.THEIRS.lower())
        self.assertEqual(issue.message, (
            "data/Round1/card-12.pdf, data/Round1/card-13.pdf and "
            "data/Round1/card-14.pdf: these cards' letterheads read "
            "'NORTHGATE LAWN TENNIS ASSOCIATION', 'Northgate Lawn Tennis "
            "Association' or 'northgate lawn tennis association', where the other "
            "12 card(s) scored as tuesday-division-c read 'EXAMPLE DISTRICT TENNIS "
            "CLUB INC'. They have been scored and their results are in the ladder, "
            "with their teams and players. If they belong to another club's "
            "competition, take them out of data/ and build again; if they are this "
            "club's cards with the name printed differently, nothing needs doing."))

    def test_one_card_is_spoken_of_as_one(self):
        issue = self.finding(self.THEIRS)
        self.assertEqual(issue.message, (
            "data/Round1/card-12.pdf: this card's letterhead reads 'NORTHGATE LAWN "
            "TENNIS ASSOCIATION', where the other 12 card(s) scored as "
            "tuesday-division-c read 'EXAMPLE DISTRICT TENNIS CLUB INC'. It has "
            "been scored and its result is in the ladder, with its teams and "
            "players. If it belongs to another club's competition, take it out of "
            "data/ and build again; if it is this club's card with the name printed "
            "differently, nothing needs doing."))

    def test_the_subject_is_the_list_the_message_opens_with(self):
        for theirs in ((self.THEIRS,), (self.THEIRS,) * 2, (self.THEIRS,) * 3):
            with self.subTest(cards=len(theirs)):
                issue = self.finding(*theirs)
                self.assertEqual(issue.subject, self.named(
                    *self.cards(*(self.OURS,) * 12, *theirs)[12:]))
                self.assertTrue(export.subject_said(
                    {"subject": issue.subject, "message": issue.message}))

    def test_the_detail_is_a_list_of_counts_and_keeps_its_commas(self):
        issue = self.finding(self.THEIRS, self.THEIRS, self.THEIRS.title(),
                             self.THEIRS.lower())
        self.assertEqual(issue.detail, (
            "2 card(s) print 'NORTHGATE LAWN TENNIS ASSOCIATION', "
            "1 card(s) print 'Northgate Lawn Tennis Association', "
            "1 card(s) print 'northgate lawn tennis association'"))


class TestADoublesRubberMissingANameCreditsHalfAndKeepsTheRest(SeasonCase):
    """Half the rubber went to the player who was named. The other half went nowhere.

    The Share column splits a doubles rubber by the shape of the slot -- two players
    won it, so half each -- and not by how many names the card happens to carry. The
    difference only shows on a card with a name cell empty, and it is the difference
    between crediting a person for a rubber the card says they played in and
    crediting them for one it does not.

    A club's fix for this is upstream in Match Centre, so what the dashboard owes
    them is the half it can stand behind, a column that no longer adds to 100%, and
    a finding that says which rubber to look at.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "parkview-wanderers"
    SCORES = ((6, 4), (6, 2), (6, 1), (6, 3))            # 24-10, sets 4-0

    # Round four by hand, on top of the three worked out in the class above.
    # Rockets' d1 names Ana alone: she takes half of its 6, and the other half has
    # nobody to go to.
    #
    #   Ana   d1 3 + s1 6 + d2 3 = 12, on 36  -> 48
    #   Bo             s2 6 + d2 3 =  9, on 34 -> 43
    #   Cass                                   ->  0
    EXPECTED_CREDIT = {"ana-fernandez": 48.0, "bo-nakamura": 43.0,
                       "cass-ellery": 0.0}
    TEAM_GAMES = 94                                      # 70 + 24
    UNCREDITED = 3.0                                     # the half nobody claimed

    @classmethod
    def extra_cards(cls):
        home_players, away_players = TEAMS[cls.HOME][1], TEAMS[cls.AWAY][1]
        rows = []
        for index, (home, away) in enumerate(cls.SCORES):
            doubles = index in (0, 3)
            if index == 0:                       # d1: one name of two, home side
                home_names, away_names = home_players[:1], away_players[:2]
            elif doubles:
                home_names, away_names = home_players[:2], away_players[:2]
            else:
                singles = index - 1              # s1 takes the first player, s2 the
                home_names = (home_players[singles],)    # second, as rows_for does
                away_names = (away_players[singles],)
            rows.append(cw.SetRow(home_games=home, away_games=away,
                                  doubles=doubles, home_players=home_names,
                                  away_players=away_names))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.half_card = cw.write_card_file(folder / "one-name.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="10", match_id="4300001",
            home_name=TEAMS[cls.HOME][0], away_name=TEAMS[cls.AWAY][0],
            rows=tuple(rows),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def credits(self, team: str) -> float:
        return sum(p["contribution"]["games_won"] for p in self.season["players"]
                   if p["team"] == team)

    def test_the_rubber_was_read_with_one_name_and_its_games_all_count(self):
        # The pre-flight: one name where a doubles rubber has two, and a scoreline
        # that is still evidence of six games won.
        match, = [m for m in self.matches if m.round == self.ROUND]
        first = match.sets[0]
        self.assertTrue(first.slot.doubles)
        self.assertEqual(len(first.home_players), 1)
        self.assertEqual(len(first.away_players), 2)
        self.assertEqual((first.home_games, first.away_games), (6, 4))
        self.assertEqual(match.home_games, 24)

    def test_the_named_player_is_credited_half_of_it(self):
        players = {p["slug"]: p for p in self.season["players"]}
        for slug, credit in self.EXPECTED_CREDIT.items():
            with self.subTest(player=slug):
                self.assertAlmostEqual(players[slug]["contribution"]["games_won"],
                                       credit, places=6)

    def test_the_other_half_is_credited_to_nobody(self):
        rows = {t["slug"]: t for t in self.season["teams"]}
        self.assertEqual(rows[self.HOME]["games_won"], self.TEAM_GAMES)
        self.assertAlmostEqual(self.credits(self.HOME),
                               self.TEAM_GAMES - self.UNCREDITED, places=6)

    def test_no_teammate_absorbed_it(self):
        # The alternative arithmetic -- splitting by the names printed rather than
        # by the slot -- hands the absent partner's half to whoever was named, and
        # the column adds up again while crediting a rubber to one person.
        players = {p["slug"]: p for p in self.season["players"]}
        self.assertLess(players["ana-fernandez"]["contribution"]["games_won"],
                        self.EXPECTED_CREDIT["ana-fernandez"] + self.UNCREDITED)
        self.assertAlmostEqual(
            sum(self.EXPECTED_CREDIT.values()),
            self.TEAM_GAMES - self.UNCREDITED, places=6)

    def test_the_column_says_so_by_not_adding_up_to_a_hundred(self):
        # 91 of 94 is 96.8%, and the three per cent missing is the point: a club
        # reading down the column can see that a rubber is unattributed.
        total = sum(p["contribution"]["share_pct"] for p in self.season["players"]
                    if p["team"] == self.HOME)
        self.assertAlmostEqual(total, 100.0 * (self.TEAM_GAMES - self.UNCREDITED)
                               / self.TEAM_GAMES, delta=0.5)
        self.assertLess(total, 100.0)

    def test_every_other_team_still_adds_up_exactly(self):
        rows = {t["slug"]: t for t in self.season["teams"]}
        for slug in sorted(rows):
            if slug == self.HOME:
                continue
            with self.subTest(team=slug):
                self.assertAlmostEqual(self.credits(slug), rows[slug]["games_won"],
                                       places=6)

    def issue(self):
        found, = [i for i in self.report.issues if i.rule == "set-player-count"]
        return found

    def test_the_report_names_the_rubber_with_the_empty_cell(self):
        # A club cannot fix this in the dashboard, so what it is owed is the rubber
        # to go and look at. It is a warning: the games are real either way.
        issue = self.issue()
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertIn("d1", issue.subject)
        self.assertIn(f"R{self.ROUND}", issue.subject)
        self.assertIn("1 player", issue.message)
        self.assertTrue(self.report.ok)

    def test_it_names_the_card_to_open_and_not_only_the_rubber(self):
        # The one finding here whose fix is upstream, in Match Centre, and it was the
        # one that did not say which file to open: its `detail` carried the name that
        # *was* read, which is the half of the problem a club already has. The rubber
        # is in the subject and the missing partner is not on the card at all, so the
        # path is the only thing the detail can usefully hold.
        self.assertIn(".pdf", self.issue().detail)

    def test_and_the_name_it_did_read_is_in_the_sentence(self):
        # Moved rather than dropped. Two cards in a round both short a name are two
        # findings a club has to tell apart, and the name printed in the cell that
        # did read is what tells them apart -- it is also the person to ask.
        message = self.issue().message
        self.assertIn("1 player", message)
        self.assertIn("ana-fernandez", message)
        # And it is who was read, not who was available: printing the roster here
        # would name the absent partner among two others and say nothing about
        # which cell is empty.
        self.assertNotIn("bo-nakamura", message)


class TestARubberWithMoreNamesThanPlacesPaysOutTwiceOver(SeasonCase):
    """The mirror of the class above, and the half of it nothing said out loud.

    A rubber can arrive with *more* names than the format has places for it. The one
    measured here is the plainest: a row whose own marker reads ``1`` -- a singles row
    -- with two names in its home cell, which is what a scorer entering a substitute
    beside the person they replaced leaves behind, and what a doubles pairing typed
    into a singles row looks like to the reader. The parser says so ("read 2 home
    player name(s) from a row its own marker '1' says is singles") and `validate.py`
    raises it as a finding, so the case is known at both ends.

    The Share column divides a rubber by the shape of the slot, which is the decision
    the class above rests on, and it follows from that decision that two names in a
    one-place slot are each credited the whole rubber: 6 games paid out twice. The
    column then adds to **106.4%** on a 94-game team, and before item 3.20 the caption
    above it said nothing whatever -- its sentence is guarded on a *positive*
    shortfall, so the one direction it could not describe was the one where a reader's
    own addition overshoots.

    Normalising instead -- dividing by the names printed, so the two share 3 apiece --
    is the alternative this rejects for the same reason the missing-name case rejects
    it: it credits a person with a share of a rubber the card does not say they played
    that way, and it makes a column add up over a card that is wrong.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "parkview-wanderers"
    SCORES = ((6, 4), (6, 2), (6, 1), (6, 3))            # 24-10, sets 4-0

    # Round four by hand, on top of the three worked out earlier in this file. s1
    # names Bo and then Ana where there is one place, so each takes its 6 outright.
    #
    #   Ana   d1 3 + s1 6 + d2 3 = 12, on 36 -> 48
    #   Bo    d1 3 + s1 6 + s2 6 + d2 3 = 18, on 34 -> 52
    #   Cass                                 ->  0
    EXPECTED_CREDIT = {"ana-fernandez": 48.0, "bo-nakamura": 52.0,
                       "cass-ellery": 0.0}
    TEAM_GAMES = 94                                      # 70 + 24
    OVERPAID = 6.0                                       # the rubber paid twice
    SHARE_PCT = -6.4                                     # 100 * -6 / 94, one place

    @classmethod
    def extra_cards(cls):
        home_players, away_players = TEAMS[cls.HOME][1], TEAMS[cls.AWAY][1]
        rows = []
        for index, (home, away) in enumerate(cls.SCORES):
            doubles = index in (0, 3)
            if index == 1:                   # s1: two names where there is one place
                home_names = (home_players[1], home_players[0])
                away_names = (away_players[0],)
                rows.append(cw.SetRow(home_games=home, away_games=away,
                                      doubles=False, home_players=home_names,
                                      away_players=away_names))
                continue
            if doubles:
                home_names, away_names = home_players[:2], away_players[:2]
            else:
                singles = index - 1
                home_names = (home_players[singles],)
                away_names = (away_players[singles],)
            rows.append(cw.SetRow(home_games=home, away_games=away,
                                  doubles=doubles, home_players=home_names,
                                  away_players=away_names))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.crowded_card = cw.write_card_file(
            folder / "two-names.pdf", cw.CardSpec(
                date="2026-09-08", round_label=cls.ROUND, division="B",
                court="10", match_id="4500001",
                home_name=TEAMS[cls.HOME][0], away_name=TEAMS[cls.AWAY][0],
                rows=tuple(rows),
                home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(home_players)),
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players))))

    def credits(self, team: str) -> float:
        return sum(p["contribution"]["games_won"] for p in self.season["players"]
                   if p["team"] == team)

    def row(self, team: str) -> dict:
        return {t["slug"]: t for t in self.season["teams"]}[team]

    def test_the_rubber_was_read_with_two_names_where_there_is_one_place(self):
        # The pre-flight, and the shape of the defect: the slot is a singles slot --
        # the marker column is where the format comes from, and it said so -- while
        # the name cell carries a pair.
        match, = [m for m in self.matches if m.round == self.ROUND]
        second = match.sets[1]
        self.assertFalse(second.slot.doubles)
        self.assertEqual(second.slot.players, 1)
        self.assertEqual(len(second.home_players), 2)
        self.assertEqual(len(second.away_players), 1)
        self.assertEqual((second.home_games, second.away_games), (6, 2))
        self.assertEqual(match.home_games, 24)

    def test_each_of_the_two_names_is_credited_the_whole_rubber(self):
        players = {p["slug"]: p for p in self.season["players"]}
        for slug, credit in self.EXPECTED_CREDIT.items():
            with self.subTest(player=slug):
                self.assertAlmostEqual(players[slug]["contribution"]["games_won"],
                                       credit, places=6)

    def test_so_the_column_adds_up_to_more_than_a_hundred(self):
        # 106.4%, which is the reader's side of it: adding the column up gives a
        # number over a hundred, and until 3.20 nothing on the page said why.
        total = sum(p["contribution"]["share_pct"] for p in self.season["players"]
                    if p["team"] == self.HOME)
        self.assertGreater(total, 100.0)
        self.assertAlmostEqual(total, 100.0 * (self.TEAM_GAMES + self.OVERPAID)
                               / self.TEAM_GAMES, delta=0.5)

    def test_the_team_row_says_by_how_much_and_over_how_many_rubbers(self):
        """The record the caption prints, in the direction it could not describe.

        Signed, one subtraction: the games the rows above hold, taken from the games
        the team won. Negative is the rows holding more than the team ever won, which
        is a statement about this table and not about the ladder -- the Games column
        beside it is 94 either way.

        The cause is counted per rubber, like the other two, because a rubber is what
        a reader would go and look at, and it is counted on the side it happened on:
        the away side of this card is one name in one place throughout.
        """
        self.assertEqual(self.row(self.HOME)["games_won"], self.TEAM_GAMES)
        self.assertEqual(self.row(self.HOME)["contribution_gap"],
                         {"games": -self.OVERPAID, "team_games_won": self.TEAM_GAMES,
                          "share_pct": self.SHARE_PCT, "rubbers_with_outsiders": 0,
                          "rubbers_short_a_name": 0, "rubbers_overfull": 1,
                          "nights_not_credited": 0})

    def test_the_opponents_column_is_whole(self):
        gap = self.row(self.AWAY)["contribution_gap"]
        self.assertEqual(gap["games"], 0.0)
        self.assertEqual(gap["rubbers_overfull"], 0)
        self.assertAlmostEqual(self.credits(self.AWAY),
                               self.row(self.AWAY)["games_won"], places=6)

    def test_the_rubber_is_not_quietly_shared_out_to_make_the_column_add_up(self):
        # The rejected arithmetic, stated as a number: dividing by the names printed
        # credits 3 apiece, the column adds to exactly 100%, and the page then says
        # nothing about a card whose own marker contradicts its name cell. What the
        # slot's shape gives instead is 6 apiece and a column that declares itself.
        self.assertAlmostEqual(self.credits(self.HOME),
                               self.TEAM_GAMES + self.OVERPAID, places=6)
        self.assertGreater(self.credits(self.HOME), self.row(self.HOME)["games_won"])

    def test_the_report_names_the_rubber_and_both_names_in_it(self):
        """The whole warning, with the names in the order the card prints them.

        A warning, not an error: the games are real, the night counted, and the fix
        is upstream in Match Centre. What the club is owed is which rubber, and the
        names as they read on the card they will go and look at. The card prints Bo
        and then Ana, the reverse of alphabetical, so a list sorted on its way into
        the sentence reads differently. This test asked for each name and the count
        on their own, and passed with the names sorted, and with them taken from a
        set under each of seeds 0 to 99 (/tmp/t6/scratch/m20/meas). Rejected:
        holding them against a set's order here as well, which comes right or wrong
        with this process's seed; the same-bytes builds hold it under seeds they
        pin.
        """
        found, = [i for i in self.report.issues if i.rule == "set-player-count"]
        self.assertEqual(found.severity, validate.WARNING)
        where = f"R{self.ROUND} {self.HOME} v {self.AWAY} s1"
        self.assertEqual((found.message, found.subject),
                         (f"{where}: the home side has 2 player(s) named "
                          f"(bo-nakamura and ana-fernandez), and Singles 1 is a "
                          f"singles set.", where))
        self.assertTrue(self.report.ok)


class TestTwoFaultsInOppositeDirectionsStillHaveToBeNamed(SeasonCase):
    """The column adds to exactly 100% and three of its rubbers are wrong.

    One card, three faults on the home side: both doubles rubbers a name short (half
    of each belonging to nobody, +3 and +3) and the first singles rubber holding two
    names where there is one place (its 6 games paid out twice, -6). They cancel. The
    team's rows sum to 94 of 94 and the column reads 51.1%, 48.9%, 0.0% -- measured,
    exactly 100.0%.

    Which is why the cause clauses cannot be nested inside the sentence about the sum,
    the way item 3.11 wrote them: on this season that sentence has nothing to say and
    it took both reasons down with it, leaving a caption of 39 characters over three
    rubbers a club can act on. The sum sentence is about the sum and is silent when the
    sum is whole; each cause clause is about a rubber and speaks whenever there is one.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "parkview-wanderers"
    SCORES = ((6, 4), (6, 2), (6, 1), (6, 3))            # 24-10, sets 4-0

    #   Ana   d1 3 + s1 6 + d2 3 = 12, on 36 -> 48      (half of each doubles rubber)
    #   Bo    s1 6 + s2 6        = 12, on 34 -> 46      (the singles paid twice)
    #   Cass                                 ->  0
    EXPECTED_CREDIT = {"ana-fernandez": 48.0, "bo-nakamura": 46.0,
                       "cass-ellery": 0.0}
    TEAM_GAMES = 94                                      # 70 + 24

    @classmethod
    def extra_cards(cls):
        home_players, away_players = TEAMS[cls.HOME][1], TEAMS[cls.AWAY][1]
        rows = []
        for index, (home, away) in enumerate(cls.SCORES):
            doubles = index in (0, 3)
            if index == 1:                   # s1: two names where there is one place
                home_names, away_names = home_players[:2], (away_players[0],)
                doubles = False
            elif doubles:                    # d1 and d2: one name of the two
                home_names, away_names = home_players[:1], away_players[:2]
            else:
                home_names = (home_players[1],)
                away_names = (away_players[1],)
            rows.append(cw.SetRow(home_games=home, away_games=away,
                                  doubles=doubles, home_players=home_names,
                                  away_players=away_names))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.mixed_card = cw.write_card_file(
            folder / "both-ways.pdf", cw.CardSpec(
                date="2026-09-08", round_label=cls.ROUND, division="B",
                court="10", match_id="4600001",
                home_name=TEAMS[cls.HOME][0], away_name=TEAMS[cls.AWAY][0],
                rows=tuple(rows),
                home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(home_players)),
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players))))

    def row(self, team: str) -> dict:
        return {t["slug"]: t for t in self.season["teams"]}[team]

    def test_the_card_was_read_as_written(self):
        match, = [m for m in self.matches if m.round == self.ROUND]
        shape = [(r.slot.key, r.slot.players, len(r.home_players))
                 for r in match.sets]
        self.assertEqual(shape, [("d1", 2, 1), ("s1", 1, 2), ("s2", 1, 1),
                                 ("d2", 2, 1)])

    def test_the_credits_are_what_hand_arithmetic_says(self):
        players = {p["slug"]: p for p in self.season["players"]}
        for slug, credit in self.EXPECTED_CREDIT.items():
            with self.subTest(player=slug):
                self.assertAlmostEqual(players[slug]["contribution"]["games_won"],
                                       credit, places=6)

    def test_the_column_adds_to_exactly_a_hundred_anyway(self):
        total = sum(p["contribution"]["share_pct"] for p in self.season["players"]
                    if p["team"] == self.HOME)
        self.assertEqual(total, 100.0)
        gap = self.row(self.HOME)["contribution_gap"]
        self.assertEqual((gap["games"], gap["share_pct"]), (0.0, 0.0))

    def test_and_both_causes_are_counted_all_the_same(self):
        # The two that cancelled, still countable separately, because they are counts
        # of rubbers and not of games: the netting happens in one field and cannot
        # reach these.
        gap = self.row(self.HOME)["contribution_gap"]
        self.assertEqual(gap["rubbers_short_a_name"], 2)
        self.assertEqual(gap["rubbers_overfull"], 1)
        self.assertEqual(gap["rubbers_with_outsiders"], 0)

    def test_the_report_names_all_three_rubbers(self):
        found = [i.subject for i in self.report.issues
                 if i.rule == "set-player-count"]
        self.assertEqual(len(found), 3)
        for slot in ("d1", "s1", "d2"):
            with self.subTest(rubber=slot):
                self.assertTrue(any(s.endswith(slot) for s in found),
                                f"{slot} is not named in the report, so the caption's "
                                f"pointer sends a reader to a section missing it")
        self.assertTrue(self.report.ok)


class TestANameWithNoSquadBehindItTakesNoShareOfTheTeam(SeasonCase):
    """A card with no roster block, naming somebody no other card has ever named.

    Their games are real and go into the team's total -- somebody won them for that
    side -- but nothing on any card says which team they belong to, so no share of
    that team's total is credited to them. The Share column is the one place where
    guessing would put a stranger's name against a team's season; their own record
    keeps every game, because that part needs no team to be true.

    The alternative -- crediting them because they were printed on that side -- is
    how a visitor who played one night ends up holding a percentage of a squad they
    are not in.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "hilltop-aces"
    STRANGER = "Rue Fenwick"
    SCORES = ((6, 4), (6, 2), (6, 1), (6, 3))            # 24-10, sets 4-0

    # Round four, with the stranger alone in s1 and no home roster printed:
    #
    #   Ana   d1 3 + d2 3        =  6, on 36 -> 42
    #   Bo    d1 3 + s2 6 + d2 3 = 12, on 34 -> 46
    #   Rue   s1 6, credited to nobody       ->  0
    EXPECTED_CREDIT = {"ana-fernandez": 42.0, "bo-nakamura": 46.0,
                       "cass-ellery": 0.0, "rue-fenwick": 0.0}
    TEAM_GAMES = 94                                      # 70 + 24
    STRANGERS_GAMES = 6

    @classmethod
    def extra_cards(cls):
        home_players, away_players = TEAMS[cls.HOME][1], TEAMS[cls.AWAY][1]
        pairs = (home_players[:2], (cls.STRANGER,), home_players[1:2],
                 home_players[:2])
        rows = []
        for index, (home, away) in enumerate(cls.SCORES):
            doubles = index in (0, 3)
            away_names = (away_players[:2] if doubles
                          else (away_players[index - 1],))
            rows.append(cw.SetRow(home_games=home, away_games=away,
                                  doubles=doubles, home_players=pairs[index],
                                  away_players=away_names))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.rosterless = cw.write_card_file(folder / "no-roster.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="11", match_id="4400001",
            home_name=TEAMS[cls.HOME][0], away_name=TEAMS[cls.AWAY][0],
            rows=tuple(rows),
            home_roster=(),                  # the block that failed to read
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def player(self, slug: str) -> dict:
        found, = [p for p in self.season["players"] if p["slug"] == slug]
        return found

    def credits(self, team: str) -> float:
        return sum(p["contribution"]["games_won"] for p in self.season["players"]
                   if p["team"] == team)

    def test_the_card_named_them_and_printed_no_home_roster(self):
        match, = [m for m in self.matches if m.round == self.ROUND]
        self.assertEqual(match.home_roster, ())
        self.assertEqual(match.sets[1].home_players, ("rue-fenwick",))
        self.assertEqual(match.home_games, 24)

    def test_nothing_registers_them_to_the_team_they_played_for(self):
        self.assertIsNone(self.player("rue-fenwick")["team"])

    def test_their_own_record_keeps_every_game_they_won(self):
        stranger = self.player("rue-fenwick")
        self.assertEqual(stranger["games_won"], self.STRANGERS_GAMES)
        self.assertEqual(stranger["matches"], 1)
        self.assertEqual(stranger["sets_played"], 1)
        # And the rate out of the one set they won, which needs no team either: a
        # missing rate is None, and only for a player who decided no set.
        self.assertEqual(stranger["win_pct"], 100.0)

    def test_they_hold_no_share_of_a_team_they_may_not_be_in(self):
        # No share at all rather than a share of nought: 0.0 is what Cass, who is on
        # the roster and never played, holds of the Rockets' 94, and Rue holds no
        # part of any team's games (TestAShareOfATeamThatWonNoGameIsNoFigure...).
        contribution = self.player("rue-fenwick")["contribution"]
        self.assertEqual(contribution["games_won"], 0)
        self.assertIsNone(contribution["share_pct"])
        self.assertEqual(contribution["team_games_won"], 0)

    def test_the_registered_players_are_credited_as_usual(self):
        for slug, credit in self.EXPECTED_CREDIT.items():
            with self.subTest(player=slug):
                self.assertAlmostEqual(
                    self.player(slug)["contribution"]["games_won"], credit,
                    places=6)

    def test_the_teams_column_is_short_by_exactly_their_games(self):
        rows = {t["slug"]: t for t in self.season["teams"]}
        self.assertEqual(rows[self.HOME]["games_won"], self.TEAM_GAMES)
        self.assertAlmostEqual(self.credits(self.HOME),
                               self.TEAM_GAMES - self.STRANGERS_GAMES, places=6)

    def test_the_report_says_the_roster_block_is_missing(self):
        rules = {i.rule for i in self.report.issues}
        self.assertIn("roster-missing", rules)
        self.assertIn("unknown-player", rules)
        self.assertTrue(self.report.ok)

    def test_it_says_once_that_the_home_squad_went_and_its_ratings_with_it(self):
        """The side that was not read, named in one finding that says what it cost.

        Measured before: three findings for this card, a ``roster-missing`` for the
        home side, the ``unknown-player`` above, and a ``ratings-missing`` naming
        the three who played home. The ratings are printed in the roster block, so
        the second of those said the first again. Rejected: keeping the
        ``ratings-missing`` and dropping the words about ratings from
        ``roster-missing``, which leaves the club two findings to connect.
        """
        found = sorted((i.rule, i.message) for i in self.report.issues
                       if Path(i.detail) == self.rosterless)
        self.assertEqual([rule for rule, _ in found],
                         ["roster-missing", "unknown-player"], found)
        said = found[0][1]
        self.assertIn("no home roster", said)
        self.assertIn("rating", said)


class TestAShareOfATeamThatWonNoGameIsNoFigureRatherThanANought(SeasonCase):
    """With no team games to share out, a player's share is not a number.

    ``share_pct`` was ``0.0`` whenever the games it is out of came to nothing, which
    is the division's guard written down as though it were a result. Measured on a
    one-card season from the card writer (/tmp/t7/scratch/W2/before.txt), in which
    the Example Alphas win every rubber 6-0 and a name on neither roster plays their
    second singles: the three Bravos and the stranger were published as
    ``{games_won: 0.0, share_pct: 0.0, team_games_won: 0}``, the same 0.0 as Charlie
    Cotterill, an Alpha who never took a court. In headless Chrome all five read
    0.0% on the Players tab, exported ``0.0%`` by Copy and CSV, and sorted among
    each other by name. The Bravos' ``contribution_gap`` gave ``share_pct`` 0.0 of 0
    as well. Only Charlie's is a figure, nought of 24. The others are ``None`` now,
    as 4.43 made a win rate out of no decided set, and the stranger holds no share
    of the side they played for (TestANameWithNoSquadBehindItTakesNoShareOfTheTeam).

    Round 4 below brings a fifth team for one card, at the Rockets, and it loses
    every rubber 6-0. Mo Quinlan and Nell Pardoe play all of it; Oli Strand is on
    the roster and never does. Cass Ellery never plays for the Rockets either, and
    keeps a real nought.

    Rejected: keeping ``0.0`` and having the page tell the two apart by reading
    ``team_games_won``. That is the page deciding, and Copy and CSV would still
    carry the nought out of it.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "northgate-owls"
    OWLS = ("NORTHGATE OWLS", ("Mo Quinlan", "Nell Pardoe", "Oli Strand"))

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.HOME]
        away_name, away_players = cls.OWLS
        home_rows, _ = squad_for(cls.HOME, home_players)
        away_rows, _ = squad_for(cls.AWAY, away_players)
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "whitewash.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="12", match_id="4780001",
            home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 0),) * 4, home_players, away_players),
            home_roster=home_rows, away_roster=away_rows))

    def player(self, slug: str) -> dict:
        return {p["slug"]: p for p in self.season["players"]}[slug]

    def team(self, slug: str) -> dict:
        return {t["slug"]: t for t in self.season["teams"]}[slug]

    def test_its_players_have_no_share_whether_or_not_they_played(self):
        owls = self.team(self.AWAY)
        self.assertEqual((owls["played"], owls["games_won"], owls["games_lost"]),
                         (1, 0, 24))
        self.assertEqual(owls["players"], ["mo-quinlan", "nell-pardoe", "oli-strand"])
        for slug, sets in (("mo-quinlan", 3), ("nell-pardoe", 3), ("oli-strand", 0)):
            with self.subTest(player=slug):
                player = self.player(slug)
                self.assertEqual(player["sets_played"], sets)
                self.assertEqual(player["contribution"],
                                 {"games_won": 0.0, "team_games_won": 0,
                                  "share_pct": None})

    def test_its_shortfall_has_no_share_of_nothing_either(self):
        # No games short, of no games: nothing to put in the unit the cells are in.
        gap = self.team(self.AWAY)["contribution_gap"]
        self.assertEqual((gap["games"], gap["team_games_won"], gap["share_pct"]),
                         (0.0, 0, None))

    def test_a_player_who_won_none_of_a_team_s_games_keeps_a_real_nought(self):
        # Cass is on the Rockets' roster every week and never on court: nought of
        # the 94 they won, 70 over rounds 1-3 and 24 here. A nought somebody set.
        self.assertEqual(self.player("cass-ellery")["contribution"],
                         {"games_won": 0.0, "team_games_won": 94, "share_pct": 0.0})
        gap = self.team(self.HOME)["contribution_gap"]
        self.assertEqual((gap["games"], gap["team_games_won"], gap["share_pct"]),
                         (0.0, 94, 0.0))

    def test_the_share_of_a_team_that_won_games_is_the_figure_it_was(self):
        # Ana: d1 3 + s1 6 + d2 3 on her 36, Bo: 3 + 6 + 3 on his 34, of 94.
        for slug, games, share in (("ana-fernandez", 48.0, 51.1),
                                   ("bo-nakamura", 46.0, 48.9)):
            with self.subTest(player=slug):
                self.assertEqual(self.player(slug)["contribution"],
                                 {"games_won": games, "team_games_won": 94,
                                  "share_pct": share})


class TestTheShortfallInTheShareColumnIsCountedWhereItIsDecided(SeasonCase):
    """What the Share column leaves out, and why, written by the program that left it.

    The column is a partition of one team's games won and item 1.2 chose to leave it
    short rather than credit a team's games to somebody the table has no row for. Item
    3.11 then had the *browser* work out how short and why, by walking the cards and
    re-deciding, in JavaScript, which rubbers pay a share: it re-tested the night, the
    slot's shape, the roster, the finals and the disputed pair. Two implementations of
    one question, and the arithmetic they were implementing lives here.

    So the exporter writes it. ``contribution_gap`` is a per-team record of the games no
    row accounts for, the total they are short of, and a count of the three things that
    cause it -- and it is accumulated in the same walk that credits the shares, on the
    same branch, so the figure and its reasons cannot describe different rubbers from
    the credit they are the remainder of.

    Three causes, and this season has one of each:

    ``rubbers_with_outsiders``
        somebody on court with no row in the team's table: registered to another squad,
        or on no squad any card names.
    ``rubbers_short_a_name``
        a doubles rubber with one name on it, so half of it belongs to nobody. Item
        1.2 declines to hand the absent partner's half to whoever was printed.
    ``nights_not_credited``
        two cards that agree about the result and disagree about who played. The night
        is in the ladder and in nobody's record, so the whole of it is missing here.

    Worked out on paper. Round four, rockets at home, 24 games won:

    ======  ==============================  ===========================
    rubber  the card                        who is credited
    ======  ==============================  ===========================
    d1 6    Ana alone in a doubles rubber   Ana 3, and 3 to nobody
    s1 6    a stranger no card registers    nobody
    s2 6    Bo                              Bo 6
    d2 6    Ana and Bo                      3 each
    ======  ==============================  ===========================

    So 15 of that night's 24 are credited, and round five's 24 are credited to nobody at
    all, which puts the rockets on 118 games won with 85 of them held by a row: a
    shortfall of 33, or 28.0% at the one decimal place every percentage in the
    document is printed to.

    Round five is a scoreline the away side wins games in, because a disputed night is
    missing from *both* columns and a 24-0 would have left the spinners a night they
    cannot name short of nothing at all.
    """

    TEAM = "riverside-rockets"
    STRANGER = "Rue Fenwick"
    BLANK_ROUND, DISPUTED_ROUND = "4", "5"
    BLANK_SCORES = ((6, 0), (6, 0), (6, 0), (6, 0))       # 24-0,  sets 4-0
    DISPUTED_SCORES = ((6, 1), (6, 2), (6, 3), (6, 4))    # 24-10, sets 4-0, twice

    TEAM_GAMES = 118                                     # 70 + 24 + 24
    CREDITED = 85.0                                      # 70 + 15 + 0
    SHORTFALL = 33.0
    SHARE_PCT = 28.0                                     # 33 / 118, one place

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS[cls.TEAM]
        roster = tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                    captain=(i == 0))
                       for i, n in enumerate(home_players))

        # The night with two holes in it, against the wanderers.
        away_name, away_players = TEAMS["parkview-wanderers"]
        rows = rows_for(cls.BLANK_SCORES, home_players, away_players)
        holed = (
            dataclasses.replace(rows[0], home_players=home_players[:1]),
            dataclasses.replace(rows[1], home_players=(cls.STRANGER,)),
            rows[2],
            rows[3],
        )
        folder = cls.data / f"Round{cls.BLANK_ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "holed.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.BLANK_ROUND, division="B",
            court="21", match_id="4900001",
            home_name=home_name, away_name=away_name, rows=holed,
            home_roster=roster,
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

        # And the night two cards name differently, against the spinners.
        away_name, away_players = TEAMS["lakeside-spinners"]
        first = rows_for(cls.DISPUTED_SCORES, home_players, away_players)
        second = tuple(
            dataclasses.replace(row, home_players=home_players[2:3])
            if index == 2 else row
            for index, row in enumerate(first))
        folder = cls.data / f"Round{cls.DISPUTED_ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        for name, rows in (("as-entered.pdf", first), ("corrected.pdf", second)):
            cw.write_card_file(folder / name, cw.CardSpec(
                date="2026-09-15", round_label=cls.DISPUTED_ROUND, division="B",
                court="22", match_id="4900002",
                home_name=home_name, away_name=away_name, rows=rows,
                home_roster=roster,
                away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                               captain=(i == 0))
                                  for i, n in enumerate(away_players))))

    def team(self, slug: str | None = None) -> dict:
        found, = [t for t in self.season["teams"]
                  if t["slug"] == (slug or self.TEAM)]
        return found

    def gap(self, slug: str | None = None) -> dict:
        return self.team(slug)["contribution_gap"]

    def credited(self, slug: str | None = None) -> float:
        return sum(p["contribution"]["games_won"] for p in self.season["players"]
                   if p["team"] == (slug or self.TEAM))

    def test_the_season_is_the_one_this_class_describes(self):
        # The pre-flight: three holes, one per cause, or the figures below are about
        # a season nobody set up.
        rounds = {m["round"]: m for m in self.season["matches"]}
        holed = rounds[self.BLANK_ROUND]
        self.assertEqual([len(s["home_players"]) for s in holed["sets"]],
                         [1, 1, 1, 2])
        self.assertEqual(holed["sets"][0]["doubles"], True)
        self.assertEqual(holed["sets"][1]["home_players"], ["rue-fenwick"])
        disputed = [m for m in self.season["matches"]
                    if m["round"] == self.DISPUTED_ROUND]
        # Both readings carry it: the kept card's figures are in the ladder and its
        # names are one of two accounts, so neither may be credited to a person.
        self.assertEqual([m["not_credited"] for m in disputed], ["names", "names"])
        self.assertEqual(self.team()["games_won"], self.TEAM_GAMES)
        self.assertAlmostEqual(self.credited(), self.CREDITED, places=6)

    def test_the_shortfall_is_the_games_no_row_holds(self):
        self.assertAlmostEqual(self.gap()["games"], self.SHORTFALL, places=6)
        self.assertEqual(self.gap()["team_games_won"], self.TEAM_GAMES)
        self.assertAlmostEqual(self.gap()["games"],
                               self.gap()["team_games_won"] - self.credited(),
                               places=6)

    def test_the_shortfall_is_also_given_as_the_percentage_a_reader_sees(self):
        # The Share column prints percentages, so the gap is offered in the same unit
        # as the cells it is missing from -- rounded the way every other percentage in
        # the document is, and not left to a page to divide for itself.
        self.assertEqual(self.gap()["share_pct"], self.SHARE_PCT)

    def test_a_doubles_rubber_with_one_name_on_it_is_counted(self):
        self.assertEqual(self.gap()["rubbers_short_a_name"], 1)

    def test_a_rubber_played_by_somebody_with_no_row_is_counted(self):
        self.assertEqual(self.gap()["rubbers_with_outsiders"], 1)

    def test_a_night_two_cards_name_differently_is_counted_as_a_night(self):
        # Not as four rubbers: nothing on that night is credited, and the data health
        # section names it as the pair of files it came from. Counted per fixture and
        # not per card, so one Tuesday read twice is one night.
        self.assertEqual(self.gap()["nights_not_credited"], 1)

    def test_the_opponents_of_that_night_are_short_of_it_too(self):
        # The disputed night belongs to both sides of it, and the spinners' own column
        # is missing the same Tuesday -- the ten games they won in it. Their other two
        # causes are the rockets' alone.
        spinners = self.gap("lakeside-spinners")
        self.assertEqual(spinners["nights_not_credited"], 1)
        self.assertEqual(spinners["rubbers_with_outsiders"], 0)
        self.assertEqual(spinners["rubbers_short_a_name"], 0)
        self.assertEqual(spinners["games"], sum(a for _, a in self.DISPUTED_SCORES))

    def test_a_team_whose_column_adds_up_has_a_gap_of_nothing(self):
        # Every field zero rather than the record left off, so a page reading it does
        # not have to ask whether a missing record means nothing wrong or nothing
        # written. The aces play in neither of the two nights above.
        aces = self.gap("hilltop-aces")
        self.assertEqual(aces["games"], 0)
        self.assertEqual(aces["share_pct"], 0)
        for cause in ("rubbers_with_outsiders", "rubbers_short_a_name",
                      "nights_not_credited"):
            with self.subTest(cause=cause):
                self.assertEqual(aces[cause], 0)

    def test_the_causes_are_counted_over_the_rubbers_the_credit_was_decided_on(self):
        # The shortfall and its reasons are one walk, so a season with a shortfall it
        # cannot name is a season where something else caused it -- which is exactly
        # what the caption on the page needs to know before it promises a reader the
        # data health section lists the rubbers.
        for team in sorted(t["slug"] for t in self.season["teams"]):
            with self.subTest(team=team):
                gap = self.gap(team)
                named = (gap["rubbers_with_outsiders"] + gap["rubbers_short_a_name"]
                         + gap["nights_not_credited"])
                self.assertEqual(gap["games"] > 0, named > 0)


class TestFinalsResultsStayOutOfTheLadder(SeasonCase):
    """A grand final decides a premiership, not a ladder position (F11).

    Three places already tell the reader that finals earn no ladder points: the
    Matches page prints it under every finals round, the discovery notes say it of
    each finals round they found, and the validator has a rule named for it. The
    ladder counted them anyway. ``Competition.regular_rounds`` was the filter meant
    to stop it, and nothing in the program ever called it.

    So this class adds a semi-final in which the bottom team thrashes the top one
    24-0, and asserts that not one figure the ladder rests on moved. Counted, that
    card would take the aces from last to second on 17.1 points off a match played
    after the season had finished.

    The split is by scope rather than by hiding the match: figures the ladder rests
    on are the regular season, a player's own record is every match they played, and
    the Share column -- a partition of one team's games -- follows the team row, so
    the two halves of a player tile cannot disagree.
    """

    FINALS_ROUND = "SF"
    FINALS_SCORES = ((6, 0), (6, 0), (6, 0), (6, 0))      # 24-0, sets 4-0

    # Dev Raman's own games, by hand from FIXTURES: R1 d1 1 + s1 2 + d2 3 = 6,
    # R2 6 + 3 + 4 = 13, R3 2 + 6 + 3 = 11, and 6 + 6 + 6 = 18 in the semi-final.
    DEV_REGULAR = 30
    DEV_FINALS = 18
    DEV_CREDIT = 20.5                                    # unchanged by the finals

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS["hilltop-aces"]
        away_name, away_players = TEAMS["riverside-rockets"]
        folder = cls.data / "Finals"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(folder / "semi-final.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.FINALS_ROUND, division="B",
            court="11", match_id="6000001",
            home_name=home_name, away_name=away_name,
            rows=rows_for(cls.FINALS_SCORES, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def teams(self) -> dict[str, dict]:
        return {t["slug"]: t for t in self.season["teams"]}

    def players(self) -> dict[str, dict]:
        return {p["slug"]: p for p in self.season["players"]}

    def finals_match(self) -> dict:
        found = [m for m in self.season["matches"]
                 if m["round"] == self.FINALS_ROUND]
        self.assertEqual(len(found), 1)
        return found[0]

    # --- the card is read, and is a result worth excluding -------------------

    def test_the_finals_card_was_read_and_the_aces_thrashed_the_rockets(self):
        # The pre-flight. Every assertion below is worthless if this card did not
        # arrive, or arrived as something less than a lopsided win for the team the
        # regular season put last.
        row = self.finals_match()
        self.assertEqual((row["home"], row["away"]),
                         ("hilltop-aces", "riverside-rockets"))
        self.assertEqual((row["home_games"], row["away_games"]), (24, 0))
        self.assertEqual(row["result"], "home")

    def test_the_round_is_read_as_finals_from_its_own_label(self):
        self.assertTrue(self.competition.is_finals_round(self.FINALS_ROUND))
        rnd = [r for r in self.season["rounds"]
               if r["number"] == self.FINALS_ROUND]
        self.assertEqual(len(rnd), 1)
        self.assertTrue(rnd[0]["is_finals"])

    def test_the_match_itself_says_it_is_not_in_the_ladder(self):
        # One predicate in the document rather than a round label the page has to
        # re-read: the page draws a points-by-round chart from these rows and must
        # not plot a bar the total beside it never counted.
        self.assertFalse(self.finals_match()["counts_for_ladder"])
        for row in self.season["matches"]:
            if row["round"] != self.FINALS_ROUND:
                with self.subTest(match=row["id"]):
                    self.assertTrue(row["counts_for_ladder"])
                    self.assertEqual(row["not_counted"], "")

    def test_it_says_the_finals_are_why_and_not_one_of_the_other_reasons(self):
        # A match can be out of the figures for three reasons and they call for
        # three different sentences: this one is the rules working, and a club that
        # read "a second copy of a night already above" would go looking for a file
        # to delete that does not exist.
        self.assertEqual(self.finals_match()["not_counted"], "finals")

    def test_and_that_the_people_who_played_it_were_credited_with_it(self):
        # The other field, and the card that shows why they are two: a final is in
        # none of the ladder's figures and is in every player's. Answered "no" here,
        # every squad table would be short a match and the set-by-set trail of a
        # grand final would be missing from the pages of the people who played it.
        for row in self.season["matches"]:
            with self.subTest(match=row["id"]):
                self.assertEqual(row["not_credited"], "")

    def test_and_that_the_seasons_totals_include_the_night_it_was_played(self):
        # The third field, on the card that shows why it is not the ladder's: a
        # grand final is four rubbers of tennis, and the front page's sets completed,
        # games played and biggest margin are about tennis played rather than about
        # the ladder. Filtered by `counts_for_ladder`, the season's own summary would
        # leave out its last night.
        for row in self.season["matches"]:
            with self.subTest(match=row["id"]):
                self.assertEqual(row["not_totalled"], "")

    # --- nothing the ladder rests on moved ----------------------------------

    def test_the_points_are_still_the_ones_worked_out_for_three_rounds(self):
        rows = self.teams()
        for slug, expected in EXPECTED_POINTS.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(rows[slug]["points"], expected, places=6)

    def test_the_ladder_order_is_the_regular_seasons(self):
        rows = self.teams()
        self.assertEqual(
            [slug for slug in sorted(rows, key=lambda s: rows[s]["position"])],
            EXPECTED_LADDER)

    def test_the_ladder_drops_the_finals_itself_whoever_calls_it(self):
        # Handed the raw match list, finals included, rather than the filtered one
        # `team_stats` passes it. The filter belongs to the ladder, not to its
        # callers, or the next caller is the one that counts a grand final.
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)
        self.assertTrue(stats.ladder_complete(self.matches, self.competition))
        for slug, expected in EXPECTED_POINTS.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(
                    stats.points_ledger(
                        stats.regular_season(self.matches, self.competition),
                        slug).total, expected, places=6)

    def test_the_team_row_counts_the_regular_season_and_nothing_else(self):
        rows = self.teams()
        for slug, expected in EXPECTED_GAMES_FOR.items():
            with self.subTest(team=slug):
                self.assertEqual(rows[slug]["games_won"], expected)
        # The two teams in the semi-final, in full: three matches each, and the
        # aces' one win of the season is still not on their record.
        aces, rockets = rows["hilltop-aces"], rows["riverside-rockets"]
        self.assertEqual((aces["played"], aces["won"], aces["lost"],
                          aces["drawn"]), (3, 0, 2, 1))
        self.assertEqual((rockets["played"], rockets["won"], rockets["lost"]),
                         (3, 3, 0))
        # 0 + 2 + 1 from FIXTURES. The four they won in the semi-final are not here.
        self.assertEqual(aces["sets_won"], 3)

    def test_the_ladder_is_still_a_settled_order(self):
        # F10. Every team has played the same three rounds, and one pair playing a
        # fourth match in the finals does not make the table provisional.
        self.assertTrue(self.season["meta"]["ladder_complete"])

    # --- and the reader is told, rather than left to notice -----------------

    def test_the_validator_does_not_call_a_scored_final_an_error(self):
        # It used to, and it was right to: the finals result *was* feeding the
        # ladder. Now that it cannot, the same situation is not a defect.
        self.assertEqual([i.message for i in self.report.errors], [])
        self.assertTrue(self.report.ok)

    def test_the_report_does_not_call_the_table_provisional_either(self):
        # F10 has to count the same matches the ladder does. Counting the semi-final
        # here leaves two teams a round ahead of everybody else for good, and the
        # dashboard carries a banner calling its own order unsettled for ever.
        unsettled = [i.message for i in self.report.warnings
                     if i.rule == "F10" and "unequal" in i.message]
        self.assertEqual(unsettled, [])

    def test_the_validator_says_the_result_was_left_out(self):
        # Absence of evidence is never silence. A card that read cleanly and then
        # moved no number at all has to be accounted for somewhere a human looks.
        said = [i for i in self.report.warnings
                if i.rule == "F11" and self.FINALS_ROUND in (i.subject or "")]
        self.assertEqual(len(said), 1, [i.message for i in self.report.warnings])
        self.assertIn("ladder", said[0].message)

    # --- the grid counts every meeting, and now says how many -----------------

    # Sorted lexically, so the aces are `low` and the rockets `high`.
    H2H_LOW, H2H_HIGH = "hilltop-aces", "riverside-rockets"

    def pairing(self) -> dict:
        return self.season["head_to_head"][
            stats.h2h_key(self.H2H_LOW, self.H2H_HIGH)]

    def test_the_grid_counts_the_final_because_the_two_teams_did_meet(self):
        # Not a second answer to the ladder's question -- a different question. The
        # grid is the record between two clubs and a semi-final is a night they
        # played each other, so hiding it would leave the Matches list on the same
        # page showing a fixture the grid says never happened.
        rec = self.pairing()
        self.assertEqual(rec["played"], 2)
        self.assertEqual((rec["wins_low"], rec["wins_high"]), (1, 1))
        # 6 + 24 against 24 + 0: the aces are ahead on games over the two meetings
        # and behind on the one the ladder counts. This is the whole problem.
        self.assertEqual((rec["games_low"], rec["games_high"]), (30, 24))

    def test_and_says_how_many_of_them_the_ladder_could_not_use(self):
        # The field the page's caption is drawn from. Without it the grid cannot
        # tell a club why a row reading 1-1 and +6 sits under a ladder that has
        # these two teams first and last, and the number is the whole explanation.
        self.assertEqual(self.pairing()["finals"], 1)

    def test_the_ladder_counts_one_of_those_two_meetings(self):
        # The disagreement, stated at the seam rather than inferred from the
        # ordering: the ladder's head-to-head tiebreak is handed the regular season,
        # where these two met once and the rockets won it 24-6.
        counted = [m for m in stats.ladder_matches(self.matches, self.competition)
                   if m.involves(self.H2H_LOW) and m.involves(self.H2H_HIGH)]
        self.assertEqual([m.round for m in counted], ["1"])
        self.assertEqual(counted[0].games_for(self.H2H_HIGH), 24)
        self.assertEqual(counted[0].games_for(self.H2H_LOW), 6)

    def test_an_ordinary_pairing_counts_no_finals_at_all(self):
        # So the page's sentence is drawn from the finals two teams actually played
        # and not from the season having a finals round somewhere in it. Every other
        # pairing here met once, in the regular season, and owes no explanation.
        for key, rec in sorted(self.season["head_to_head"].items()):
            if key == stats.h2h_key(self.H2H_LOW, self.H2H_HIGH):
                continue
            with self.subTest(pairing=key):
                self.assertEqual(rec["finals"], 0)
                self.assertEqual(rec["played"], 1)

    # --- the player's own record is every match they played -----------------

    def test_the_players_own_games_count_the_finals(self):
        dev = self.players()["dev-raman"]
        self.assertEqual(dev["games_won"], self.DEV_REGULAR + self.DEV_FINALS)
        self.assertEqual(dev["matches"], 4)

    def test_the_share_column_still_adds_up_to_the_team_row(self):
        # The invariant from the Share fix, now with a finals card in the season:
        # both halves of the fraction are the regular season, so the column is
        # still a partition of the games the team row prints.
        rows = self.teams()
        dev = self.players()["dev-raman"]
        self.assertAlmostEqual(dev["contribution"]["games_won"], self.DEV_CREDIT,
                               places=6)
        for team in sorted(rows):
            mine = [p for p in self.season["players"] if p["team"] == team]
            with self.subTest(team=team):
                self.assertAlmostEqual(
                    sum(p["contribution"]["games_won"] for p in mine),
                    rows[team]["games_won"], places=6)
                for player in mine:
                    self.assertEqual(player["contribution"]["team_games_won"],
                                     rows[team]["games_won"])


class TestABlankCardInventsNobody(SeasonCase):
    """A fixture called off before its players were entered, which is a real export.

    Every name cell prints Match Centre's own ``Unspecified player``, and read as a
    name that placeholder became a player: one appearance, eight rubbers -- more than
    any real member of the club had -- no team, and itself as its only opponent. It
    reached the dashboard's list of players worth checking, where a club would look
    for a person who does not exist. Nothing in the report mentioned it, because a
    cancelled card skips the roster checks that would have.

    So: no phantom, and one line in the report per card saying how much of it was
    never filled in.
    """

    ROUND = "4"
    HOME, AWAY = "lakeside-spinners", "parkview-wanderers"
    PLACEHOLDER = "Unspecified player"

    @classmethod
    def extra_cards(cls):
        blank = tuple(
            cw.SetRow(home_games=0, away_games=0, doubles=index in (0, 3),
                      home_players=(cls.PLACEHOLDER,) * (2 if index in (0, 3) else 1),
                      away_players=(cls.PLACEHOLDER,) * (2 if index in (0, 3) else 1))
            for index in range(4))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.blank_card = cw.write_card_file(folder / "blank.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="09", match_id="4100001",
            home_name=TEAMS[cls.HOME][0], away_name=TEAMS[cls.AWAY][0],
            rows=blank, cancelled=True, verb="VS", winner=cw.NO_TICK,
            omit_totals=True,
            # The roster block is empty on such a card too, so nothing here
            # registers the placeholder onto a team either.
            home_roster=(cw.RosterRow(name=cls.PLACEHOLDER),),
            away_roster=(cw.RosterRow(name=cls.PLACEHOLDER),)))

    def unrecorded(self):
        return [i for i in self.report.issues
                if i.rule == "players-unrecorded"]

    # --- nobody was invented -----------------------------------------------

    def test_no_player_is_registered_from_the_placeholder(self):
        slugs = {p["slug"] for p in self.season["players"]}
        self.assertEqual(len(slugs), 12)
        for slug in slugs:
            with self.subTest(player=slug):
                self.assertNotIn("unspecified", slug)

    def test_no_display_name_is_invented_for_it_either(self):
        # player_names is where a slug becomes a label, and the label would have
        # been "Unspecified Player" -- title-cased, which is how it reached the
        # dashboard looking like a person.
        for slug, name in self.season["player_names"].items():
            with self.subTest(player=slug):
                self.assertNotIn("unspecified", name.casefold())

    def test_the_match_has_no_players_on_either_side(self):
        match, = [m for m in self.matches if m.round == self.ROUND]
        self.assertEqual(match.players_on("home"), ())
        self.assertEqual(match.players_on("away"), ())
        self.assertEqual(match.home_roster, ())

    def test_no_team_gains_a_squad_member(self):
        rows = {t["slug"]: t for t in self.season["teams"]}
        for slug in (self.HOME, self.AWAY):
            with self.subTest(team=slug):
                self.assertEqual(len(rows[slug]["players"]), 3)

    # --- and the loss is reported ------------------------------------------

    def test_one_finding_per_card_says_how_much_was_never_filled_in(self):
        issue, = self.unrecorded()
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertIn(str(self.blank_card), issue.subject)
        self.assertIn("12 name cell", issue.message)
        self.assertIn("4 of 4 rubber", issue.message)
        self.assertIn("Unspecified player", issue.message)

    def test_it_says_the_match_was_called_off_so_nothing_needs_doing(self):
        # The distinction that decides whether a club acts: a card called off
        # before the names went in needs nothing, a played card printing the
        # placeholder needs re-exporting.
        issue, = self.unrecorded()
        self.assertIn("called off", issue.message)

    def test_the_clean_cards_produce_no_such_finding(self):
        # Six cards with real names on them. Without this the check could fire on
        # everything and still pass above.
        for issue in self.unrecorded():
            with self.subTest(subject=issue.subject):
                self.assertIn(str(self.blank_card), issue.subject)

    def test_the_card_is_still_read_and_counted_as_a_card(self):
        self.assertEqual(self.rejected, [])
        self.assertEqual(self.season["meta"]["cards_scored"], len(FIXTURES) + 1)

    def test_the_ladder_is_untouched_by_it(self):
        self.assertEqual(stats.ladder(self.matches, self.competition),
                         EXPECTED_LADDER)


class TestAPlayedCardMissingOneRowsNamesSaysWhatToDo(SeasonCase):
    """The version a club has to act on: the match was played, a rubber has no names.

    Those games are real and are counted -- the scoreline is evidence, and refusing
    it would lose a night's tennis -- but there is nobody to credit them to, so the
    two players who did play that rubber have a week missing from their records. The
    fix is upstream, in Match Centre, and the report has to say so differently from
    the way it describes a card called off before anybody was entered.
    """

    ROUND = "4"
    HOME, AWAY = "riverside-rockets", "parkview-wanderers"
    PLACEHOLDER = "Unspecified player"

    @classmethod
    def extra_cards(cls):
        home_players, away_players = TEAMS[cls.HOME][1], TEAMS[cls.AWAY][1]
        scores = ((6, 2), (6, 3), (6, 1), (6, 4))
        rows = []
        for index, (home, away) in enumerate(scores):
            doubles = index in (0, 3)
            if index == 2:                       # s2: played, nobody named
                home_names = away_names = (cls.PLACEHOLDER,)
            elif doubles:
                home_names, away_names = home_players[:2], away_players[:2]
            else:
                home_names, away_names = home_players[:1], away_players[:1]
            rows.append(cw.SetRow(home_games=home, away_games=away,
                                  doubles=doubles, home_players=home_names,
                                  away_players=away_names))
        folder = cls.data / f"Round{cls.ROUND}"
        folder.mkdir(parents=True, exist_ok=True)
        cls.thin_card = cw.write_card_file(folder / "thin.pdf", cw.CardSpec(
            date="2026-09-08", round_label=cls.ROUND, division="B",
            court="09", match_id="4200001",
            home_name=TEAMS[cls.HOME][0], away_name=TEAMS[cls.AWAY][0],
            rows=tuple(rows),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def test_the_match_is_played_and_its_games_all_count(self):
        match, = [m for m in self.matches if m.round == self.ROUND]
        self.assertTrue(match.counts_as_played)
        self.assertTrue(match.decided)
        self.assertEqual(match.home_games, 24)      # the nameless rubber included

    def test_the_nameless_rubber_has_its_score_and_no_players(self):
        match, = [m for m in self.matches if m.round == self.ROUND]
        nameless = match.sets[2]
        self.assertEqual((nameless.home_games, nameless.away_games), (6, 1))
        self.assertEqual(nameless.home_players, ())
        self.assertEqual(nameless.away_players, ())

    def test_the_finding_says_the_games_have_nobody_to_credit_them_to(self):
        issue, = [i for i in self.report.issues
                  if i.rule == "players-unrecorded"]
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertIn(str(self.thin_card), issue.subject)
        self.assertIn("2 name cell", issue.message)
        self.assertIn("nobody to credit", issue.message)
        self.assertNotIn("called off", issue.message)

    def test_it_names_the_rubber_so_the_card_can_be_compared_against_it(self):
        issue, = [i for i in self.report.issues
                  if i.rule == "players-unrecorded"]
        self.assertIn("3", issue.detail)            # row three of four

    def test_no_phantom_reaches_the_players_list(self):
        slugs = {p["slug"] for p in self.season["players"]}
        for slug in slugs:
            with self.subTest(player=slug):
                self.assertNotIn("unspecified", slug)


# --- one player, two spellings ------------------------------------------------

# The fourth round below exists so that "has played once or twice" means
# something: after three rounds every player has played at most three, and a
# player with one appearance is unremarkable.
ROUND_FOUR = (("riverside-rockets", "hilltop-aces",
               ((6, 2), (6, 3), (6, 4), (6, 1))),
              ("lakeside-spinners", "parkview-wanderers",
               ((6, 2), (6, 3), (2, 6), (6, 4))))


def build_season(root: Path, *, misspell: str = "",
                 aliases: dict[str, str] | None = None,
                 rename: dict[str, Swap] | None = None,
                 namesakes: dict[str, tuple[str, ...]] | None = None) -> dict:
    """The season document for the four-round competition.

    ``misspell`` replaces the second Rockets player's name **in the set rows of
    round four only**, leaving every roster block spelling it correctly -- which
    is what happens when whoever fills in a card writes a name from memory.
    ``aliases`` is the config remedy for it.

    ``rename`` is the opposite defect: it prints one team's second player under a
    name another team's roster block already carries, so that two people arrive
    under one name. ``namesakes`` is the config remedy for that one.
    """
    data = root / "data"
    write_season_cards(data, rename=rename)
    folder = data / "Round4"
    folder.mkdir(parents=True, exist_ok=True)
    for index, (home, away, scores) in enumerate(ROUND_FOUR, start=7):
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on_court = squad_for(home, home_players, rename)
        away_rows, away_on_court = squad_for(away, away_players, rename)
        on_court = list(home_on_court)
        if misspell and home == "riverside-rockets":
            on_court[1] = misspell
        spec = cw.CardSpec(
            date="2026-09-08", round_label="4", division="B",
            court=f"{index:02d}", match_id=f"200{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, tuple(on_court), away_on_court),
            home_roster=home_rows,
            away_roster=away_rows)
        cw.write_card_file(folder / f"r4-{index}.pdf", spec)

    cards, rejected = scorecard.read_tree(data)
    config = config_mod.Config(path=root / "config.toml",
                               aliases=dict(aliases or {}),
                               namesakes=dict(namesakes or {}))
    found = discover_mod.discover(cards, rejected, config=config, root=data)
    comp_id, = sorted(found.matches)
    competition = next(c for c in found.club.competitions if c.id == comp_id)
    season = export.build_season(
        found.matches[comp_id], competition=competition, source_dir=data,
        generated="2026-09-10T12:00:00", club=found.club, cards=cards,
        rejected=rejected)
    season["_report"] = validate.validate(
        found.matches[comp_id], competition, club=found.club, cards=cards,
        rejected=rejected)
    season["_notes"] = tuple(found.notes)
    season["_config_warnings"] = tuple(found.config_warnings)
    return season


class TestOnePlayerRecordedTwice(unittest.TestCase):
    """A name spelled two ways, which splits one player's record in half.

    Both halves are internally consistent, every set was still played and
    counted, and the team's points, games and ladder position are all exactly
    right -- so there is nothing here that can be called an error, and nothing
    that changes a figure a club looks at.

    Three things say so anyway, for three different readers. The build report warns
    whoever ran ``build.py``; the dashboard's "players to check" list is for
    whoever only ever opens the page, which at most clubs is nearly everybody; and
    the team's own ``contribution_gap`` is where the split stops being a warning and
    becomes a number, because twelve of the rockets' games are held by a name their
    table has no row for. These tests cover the report, the facts the list rests on,
    that number, and the remedy all three recommend.

    Three seasons are built: one spelled correctly throughout, one with the
    misspelling, and one with the misspelling plus the alias that declares the
    two spellings to be the same person.
    """

    MISSPELT = "Bo Nakamora"        # every roster block says Bo Nakamura

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.control = build_season(root / "control")
        cls.split = build_season(root / "split", misspell=cls.MISSPELT)
        cls.fixed = build_season(root / "fixed", misspell=cls.MISSPELT,
                                 aliases={"bo-nakamora": "bo-nakamura"})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def player(season: dict, slug: str) -> dict | None:
        return next((p for p in season["players"] if p["slug"] == slug), None)

    def test_the_misspelling_becomes_a_player_of_its_own(self):
        self.assertEqual(len(self.control["players"]), 12)
        self.assertEqual(len(self.split["players"]), 13)
        stray = self.player(self.split, "bo-nakamora")
        self.assertIsNotNone(stray, "the misspelt name did not produce a "
                                    "separate player, so there is nothing for "
                                    "the list to show")
        self.assertEqual(stray["matches"], 1)

    def test_the_stray_half_belongs_to_no_team(self):
        # Why it cannot be found by looking at a team page: it is on no roster,
        # so it appears in no squad. This is also the strongest hint the list can
        # print, and the reason that column exists.
        self.assertIsNone(self.player(self.split, "bo-nakamora")["team"])

    def test_the_real_player_is_left_one_match_short(self):
        self.assertEqual(self.player(self.control, "bo-nakamura")["matches"], 4)
        self.assertEqual(self.player(self.split, "bo-nakamura")["matches"], 3)

    def test_the_ladder_does_not_move_at_all(self):
        # The whole reason a page needs to say something: every figure a club
        # actually looks at is identical, because the sets were played and
        # counted either way.
        #
        # Every figure but one. `contribution_gap` is the record that exists to
        # notice precisely this, and the test below is that it did; leaving it in the
        # comparison would assert the opposite of what that one asserts.
        self.assertEqual(self.rows(self.split), self.rows(self.control))

    def test_the_split_leaves_games_on_the_team_row_with_nobody_to_hold_them(self):
        # The one figure that does move, and why it is worth a column on the row:
        # the rockets' Share column is twelve games short over the three rubbers the
        # stray half played, because those games went to a name no roster carries.
        # Declaring the alias closes the hole exactly, which is the remedy the
        # warning recommends, measured rather than asserted.
        self.assertEqual(self.gap(self.control)["games"], 0)
        self.assertEqual(self.gap(self.split)["games"], 12.0)
        self.assertEqual(self.gap(self.split)["rubbers_with_outsiders"], 3)
        self.assertEqual(self.gap(self.fixed), self.gap(self.control))

    @staticmethod
    def rows(season: dict) -> list[dict]:
        return [{k: v for k, v in t.items() if k != "contribution_gap"}
                for t in season["teams"]]

    @staticmethod
    def gap(season: dict) -> dict:
        return next(t["contribution_gap"] for t in season["teams"]
                    if t["slug"] == "riverside-rockets")

    def test_the_report_warns_about_it_and_does_not_fail(self):
        # A warning rather than an error, because whether two names are one
        # person is not something this program can find out: the same evidence
        # describes a genuine fill-in from another grade.
        self.assertEqual(list(self.split["_report"].errors), [])
        flagged = [i for i in self.split["_report"].warnings
                   if i.rule == "unknown-player" and "nakamora" in i.subject +
                   i.message]
        self.assertTrue(flagged, "the build report says nothing about a name "
                                 "that is on no roster")
        for issue in flagged:
            with self.subTest(subject=issue.subject):
                # Both readings named, and the remedy for the one the club can
                # act on. "Unknown player" alone leaves nothing to do.
                self.assertIn("fill-in", issue.message)
                self.assertIn("alias", issue.message)
                self.assertTrue(issue.detail.endswith(".pdf"),
                                f"the warning does not name the card it came "
                                f"from: {issue.detail!r}")

    def test_the_correctly_spelled_season_produces_no_such_warning(self):
        # Otherwise the warning above is noise every club sees on every run, and
        # a warning everybody ignores is worse than silence.
        self.assertEqual([i for i in self.control["_report"].warnings
                          if i.rule == "unknown-player"], [])

    def test_the_document_carries_what_the_list_needs_to_single_it_out(self):
        # The two facts the dashboard's rule rests on: somebody has had enough
        # matches available for one appearance to stand out, and exactly one
        # player is down at one or two.
        scale = max(max(p["available"], p["matches"])
                    for p in self.split["players"])
        self.assertGreaterEqual(scale, 4)
        sparse = sorted(p["slug"] for p in self.split["players"]
                        if 1 <= p["matches"] <= 2)
        self.assertEqual(sparse, ["bo-nakamora"])

    def test_a_player_yet_to_play_is_not_in_that_group(self):
        # Each team's third player never takes a court in this season -- four
        # rosters, four idle names. They are the reason the rule starts at one
        # appearance rather than at zero: a split name has at least one on each
        # side, and a reserve who has played nothing can never be half of one.
        idle = sorted(p["slug"] for p in self.split["players"]
                      if p["matches"] == 0)
        self.assertEqual(idle, ["cass-ellery", "fay-bramwell", "ilya-modric",
                                "lena-ordoy"])

    def test_declaring_the_alias_restores_the_record_exactly(self):
        # The remedy the list tells a club to apply. If this did not hold, the
        # advice would be worse than saying nothing.
        self.assertIsNone(self.player(self.fixed, "bo-nakamora"))
        self.assertEqual(self.player(self.fixed, "bo-nakamura")["matches"], 4)
        self.assertEqual(self.fixed["players"], self.control["players"])

    def test_and_still_does_not_move_the_ladder(self):
        self.assertEqual(self.fixed["teams"], self.control["teams"])


class TestTwoPlayersRecordedOnce(unittest.TestCase):
    """Two people whose cards print one name, which arrive as one player.

    The mirror image of the class above, and the more expensive of the two. A name
    spelled two ways splits one record in half and leaves both halves internally
    right. One name covering two people produces a single row that is wrong for
    both of them and impossible on its face: twenty-four rubbers in a competition
    that has sixteen, a rating history alternating between two standards, a player
    who is their own opponent and their own team's fill-in, and a team allocation
    settled by whichever roster the index happened to write last.

    Nothing on the cards can settle whether it is two people. Two teams' roster
    blocks carrying one name is equally one person who changed teams mid-season,
    and splitting that would invent somebody who does not exist -- so this program
    reports it and a human decides, exactly as it treats two team banners that look
    alike. ``[namesakes]`` is where the human says so.

    Four seasons: the competition as written; the merge with four rounds of results
    behind it; that merge declared; and a merge between two players who never take
    a court, which is the case no set row can betray and which nothing in the
    program noticed at all.
    """

    # Lakeside's second player is printed under the name Riverside's second player
    # already carries, at a rating that says plainly they are not the same person.
    SHARED = "Bo Nakamura"
    MERGED = "bo-nakamura"
    OURS = "bo-nakamura-riverside-rockets"
    THEIRS = "bo-nakamura-lakeside-spinners"
    # Position 2 is rostered every week and never plays, so the second merge leaves
    # no trace in any set row, any total or any ladder.
    IDLE = "cass-ellery"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        swap = {"lakeside-spinners": Swap(1, cls.SHARED, 4.0)}
        cls.control = build_season(root / "control")
        cls.merged = build_season(root / "merged", rename=swap)
        cls.split = build_season(
            root / "split", rename=swap,
            namesakes={cls.MERGED: ("riverside-rockets", "lakeside-spinners")})
        cls.quiet = build_season(
            root / "quiet",
            rename={"lakeside-spinners": Swap(2, "Cass Ellery")})
        cls.quiet_split = build_season(
            root / "quiet-split",
            rename={"lakeside-spinners": Swap(2, "Cass Ellery")},
            namesakes={cls.IDLE: ("riverside-rockets", "lakeside-spinners")})
        # Three declarations that cannot take effect as written, each a keystroke
        # from the line the report offers: one names a team that has nobody of the
        # name, one names a person no card prints at all, and one names somebody who
        # reached a set row without ever reaching a roster block -- which is a real
        # player, found, and the reason this check cannot read rosters alone.
        cls.mistyped = build_season(
            root / "mistyped", rename=swap, misspell="Bo Nakamora",
            namesakes={cls.MERGED: ("riverside-rockets", "parkview-wanderers"),
                       "bo-nakamora": ("riverside-rockets", "lakeside-spinners"),
                       "no-such-person": ("riverside-rockets", "hilltop-aces")})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def player(season: dict, slug: str) -> dict | None:
        return next((p for p in season["players"] if p["slug"] == slug), None)

    @staticmethod
    def rostered(season: dict) -> int:
        """Roster rows across every team, which is how many people were printed."""
        return sum(len(t["players"]) for t in season["teams"])

    @staticmethod
    def flagged(season: dict, rule: str) -> list:
        report = season["_report"]
        return [i for i in report.issues if i.rule == rule]

    # --- what the merge does ------------------------------------------------

    def test_two_rostered_people_arrive_as_one_player(self):
        # Twelve roster rows either way; one fewer player to hold them.
        self.assertEqual(self.rostered(self.control), 12)
        self.assertEqual(self.rostered(self.merged), 12)
        self.assertEqual(len(self.control["players"]), 12)
        self.assertEqual(len(self.merged["players"]), 11)

    def test_the_merged_row_holds_more_rubbers_than_the_competition_has(self):
        # Worked out from the fixtures, not from the code: this generator puts the
        # second player of each team on court three times a week (both doubles and
        # the second singles). Riverside play four matches and Lakeside four, so
        # the merged row claims 4x3 + 4x3 = 24 rubbers -- in a four-round
        # competition where the most anybody can play is 4x4 = 16.
        self.assertEqual(self.player(self.control, self.MERGED)["sets_played"], 12)
        self.assertEqual(self.player(self.merged, self.MERGED)["sets_played"], 24)
        rounds = len(self.merged["rounds"])
        self.assertEqual(rounds, 4)
        self.assertGreater(self.player(self.merged, self.MERGED)["sets_played"],
                           rounds * 4)

    def test_the_merged_row_makes_them_their_own_opponent(self):
        # Riverside meet Lakeside in round two, so both people are on the same
        # card. One person cannot play themselves, and the row says they did --
        # which is the merge stating a thing that cannot be true, in a figure a
        # club reads straight off the player page.
        against = [o["slug"] for o in
                   self.player(self.merged, self.MERGED)["opponents"]]
        self.assertIn(self.MERGED, against)
        self.assertNotIn(self.MERGED, [o["slug"] for o in
                                       self.player(self.control,
                                                   self.MERGED)["opponents"]])

    def test_the_merged_row_is_its_own_team_s_fill_in(self):
        # `is_fill_in` reads the team the one surviving record claims, so every
        # night the other team played is branded as filling in: all four of
        # Lakeside's matches, credited to somebody who never left their club.
        self.assertEqual(
            self.player(self.control, self.MERGED)["fill_in_appearances"], 0)
        self.assertEqual(
            self.player(self.merged, self.MERGED)["fill_in_appearances"], 4)

    def test_only_one_of_the_two_ratings_survives_each_round(self):
        # The two people are graded 2.5 and 4.0 and the cards print both. The
        # merged history keeps whichever card was read last for each round, so the
        # dashboard draws a line that jumps between two people's standards.
        history = {row["round"]: row["singles"] for row in
                   self.player(self.merged, self.MERGED)["rating_history"]}
        self.assertEqual(set(history.values()), {2.5, 4.0})

    def test_one_team_loses_the_player_from_its_own_index(self):
        # The team pages still print both roster rows, so both teams show the name;
        # only one row exists to click through to, and it names the other team.
        for season, teams in ((self.control, 1), (self.merged, 2)):
            with self.subTest(rostered_by=teams):
                holding = [t["slug"] for t in season["teams"]
                           if self.MERGED in t["players"]]
                self.assertEqual(len(holding), teams)
        self.assertEqual(self.player(self.merged, self.MERGED)["team"],
                         "riverside-rockets")

    # --- what is said about it ----------------------------------------------

    def test_the_report_names_both_teams_and_what_to_do(self):
        found = self.flagged(self.merged, "player-two-rosters")
        self.assertTrue(found, "the build report says nothing about one name "
                               "registered to two teams")
        issue, = found
        for expected in ("riverside-rockets", "lakeside-spinners",
                         "namesakes", self.SHARED):
            with self.subTest(expected=expected):
                self.assertIn(expected, issue.message)
        # Both readings, because the cards cannot choose between them, and a club
        # told only one of them would correct data that was never wrong.
        self.assertIn("two different people", issue.message)
        self.assertIn("changed teams", issue.message)
        self.assertEqual(issue.severity, "warning")
        # And the line itself, not only the name of the section it goes in: a club
        # that has decided which of the two readings is right should be able to
        # paste the answer rather than go and learn the syntax for it.
        self.assertIn(
            f'{self.MERGED} = ["lakeside-spinners", "riverside-rockets"]',
            issue.message)

    def test_the_merge_nobody_plays_is_reported_just_as_loudly(self):
        # The case that mattered most and was seen least. Neither of these two ever
        # reaches a set row, so every set-level check in the program is blind to
        # them: before this check the whole report was silent, and the dashboard
        # quietly showed eleven players where twelve were printed.
        self.assertEqual(len(self.quiet["players"]), 11)
        self.assertEqual(self.rostered(self.quiet), 12)
        found = self.flagged(self.quiet, "player-two-rosters")
        self.assertTrue(found, "a merge with no results behind it is invisible")
        self.assertIn(self.IDLE, found[0].subject)
        # And nothing else in the report knew: no set row, no round, no total.
        self.assertEqual([i.rule for i in self.quiet["_report"].issues
                          if self.IDLE in i.message + i.subject],
                         ["player-two-rosters"])

    def test_the_round_error_no_longer_calls_one_of_the_cards_wrong(self):
        # It used to end "one of those cards names the wrong player", which is
        # false whenever the true answer is two people with one name -- and that is
        # exactly the case here, where both cards are right. The error stands,
        # because one person still cannot be in two places on one night; the
        # explanation now offers the reading that fits.
        found = self.flagged(self.merged, "player-two-teams")
        self.assertTrue(found)
        for issue in found:
            with self.subTest(subject=issue.subject):
                self.assertEqual(issue.severity, "error")
                self.assertIn("two different people", issue.message)
                self.assertIn("namesakes", issue.message)

    def test_the_season_as_written_is_warned_about_neither_way(self):
        # Or the warnings above are noise on every run at every club.
        self.assertEqual(self.flagged(self.control, "player-two-rosters"), [])
        self.assertEqual(self.flagged(self.control, "player-two-teams"), [])

    # --- the remedy ---------------------------------------------------------

    def test_declaring_the_namesake_gives_each_team_its_own_player(self):
        self.assertEqual(len(self.split["players"]), 12)
        self.assertIsNone(self.player(self.split, self.MERGED))
        for slug, team in ((self.OURS, "riverside-rockets"),
                           (self.THEIRS, "lakeside-spinners")):
            with self.subTest(slug=slug):
                self.assertEqual(self.player(self.split, slug)["team"], team)
                # The name a club reads is still the name the card printed. The
                # team column is what tells the two of them apart, and it is on
                # every table the name appears in.
                self.assertEqual(self.player(self.split, slug)["name"],
                                 self.SHARED)

    def test_each_split_player_holds_exactly_their_own_half(self):
        # Riverside's player comes out figure for figure identical to the season
        # where the collision never happened -- the strongest statement available
        # that the split takes nothing away and adds nothing.
        #
        # Their own slug is different, and so are the slugs of the people they
        # played against, because the other team's second player is a different
        # person in the two seasons. The numbers against each of those names are
        # compared; the names themselves are the thing that changed.
        self.assertEqual(self.figures(self.split, self.OURS),
                         self.figures(self.control, self.MERGED))
        self.assertEqual(self.player(self.split, self.THEIRS)["sets_played"], 12)

    @classmethod
    def figures(cls, season: dict, slug: str) -> dict:
        """One player's record with every player's name taken out of it."""
        row = {k: v for k, v in cls.player(season, slug).items() if k != "slug"}
        for key in ("partners", "opponents"):
            row[key] = [{k: v for k, v in pair.items()
                         if k not in ("slug", "name")} for pair in row[key]]
        return row

    def test_the_split_gives_each_of_them_the_rating_their_cards_printed(self):
        self.assertEqual(self.player(self.split, self.OURS)["rating"]["singles"],
                         2.5)
        self.assertEqual(self.player(self.split, self.THEIRS)["rating"]["singles"],
                         4.0)

    def test_the_split_ends_every_complaint_the_merge_caused(self):
        self.assertEqual(self.flagged(self.split, "player-two-rosters"), [])
        self.assertEqual(self.flagged(self.split, "player-two-teams"), [])
        self.assertEqual(list(self.split["_report"].errors), [])
        for slug in (self.OURS, self.THEIRS):
            with self.subTest(slug=slug):
                row = self.player(self.split, slug)
                self.assertEqual(row["fill_in_appearances"], 0)
                self.assertNotIn(slug, [o["slug"] for o in row["opponents"]])

    def test_the_split_does_not_move_a_single_team_figure(self):
        # Every set was played and counted whichever way the names were read, so
        # the ladder had better be untouched -- by the merge and by the split
        # alike. If it moved, one of the two would be rewriting results.
        #
        # The roster lists are excluded because they are the one thing that must
        # differ: Lakeside's second row is a different name in each season.
        self.assertEqual(self.rows(self.split), self.rows(self.control))

    def test_the_merge_moves_a_third_of_one_team_s_games_out_of_its_table(self):
        # The merge does move one figure, and this is the one place a club could
        # have caught it before now: `contribution_gap` counts games held by a name
        # the team's own table has no row for, and the merged row claims Riverside,
        # so 37.5 of Lakeside's 78 games belong to a stranger. It is the symptom
        # furthest from the cause, which is why the check above exists -- and it is
        # also why the split has to close the hole exactly.
        self.assertEqual(self.gap(self.control)["games"], 0)
        self.assertEqual(self.gap(self.merged)["games"], 37.5)
        self.assertEqual(self.gap(self.merged)["team_games_won"], 78)
        self.assertEqual(self.gap(self.split), self.gap(self.control))
        # And nothing else on any row moved: it is one hole, not a rescored season.
        self.assertEqual(
            [{k: v for k, v in t.items() if k != "contribution_gap"}
             for t in self.rows(self.merged)],
            [{k: v for k, v in t.items() if k != "contribution_gap"}
             for t in self.rows(self.control)])

    # --- a declaration that cannot take effect ------------------------------

    def test_a_namesake_entry_naming_nobody_says_so_instead_of_nothing(self):
        # A split is applied by rewriting a slug, so an entry that matches no card
        # rewrites nothing -- and a dashboard built from a line that did nothing is
        # indistinguishable from one built from a line that worked. The misspelt
        # name is the whole of the defect here: it is the only thing between this
        # run and the season above it. A warning, which --quiet prints, and not a
        # note as well: one mistake is said once.
        said = [n for n in self.mistyped["_config_warnings"] if "no-such-person" in n]
        self.assertEqual(len(said), 1, self.mistyped["_config_warnings"])
        self.assertIn("[namesakes]", said[0])
        self.assertIn("separated nobody", said[0])
        self.assertEqual([n for n in self.mistyped["_notes"] if "[namesakes]" in n],
                         [])

    def test_and_one_named_team_without_the_name_is_named_on_its_own(self):
        # Half a declaration still splits the teams it did match, so this is a
        # warning and not a failure -- but the team it could not find is the one
        # thing the club has to look at, and nothing else in the run mentions it.
        said = [n for n in self.mistyped["_config_warnings"]
                if "[namesakes]" in n and "parkview-wanderers" in n]
        self.assertEqual(len(said), 1, self.mistyped["_config_warnings"])
        self.assertNotIn("separated nobody", said[0])
        self.assertIn("riverside-rockets", said[0])

    def test_a_name_that_only_ever_filled_in_counts_as_a_team_that_has_it(self):
        # Round four spells Riverside's second player wrongly in the set rows and
        # correctly in the roster block, so that spelling belongs to a player with
        # four rubbers and no registration -- exactly what a fill-in looks like from
        # here. Judging the declaration on roster blocks alone would tell a club
        # that Riverside has nobody of the name while the dashboard shows them
        # playing for Riverside. Nobody else is called that, so the one record
        # was renamed and nobody was parted from it, and the warning says that.
        said = [n for n in self.mistyped["_config_warnings"] if "bo-nakamora" in n]
        self.assertEqual(len(said), 1, self.mistyped["_config_warnings"])
        self.assertNotIn("any of them", said[0])
        self.assertIn("no card puts that name in lakeside-spinners, so the entry "
                      "separated nobody.", " ".join(said[0].split()))
        self.assertIsNotNone(
            self.player(self.mistyped, "bo-nakamora-riverside-rockets"))

    def test_a_declaration_that_works_is_not_complained_about(self):
        # Otherwise the warning above is just something every run prints, which
        # tells a club nothing.
        for season in (self.split, self.control):
            said = (*season["_notes"], *season["_config_warnings"])
            self.assertEqual([n for n in said if "[namesakes]" in n], [])

    def test_declaring_two_people_who_never_play_works_and_is_not_queried(self):
        # The case with no set rows behind it at all, which is both the one nothing
        # else in the program can see and the one that would read as a mistyped
        # entry if the check only looked at who took a court. Two roster rows, two
        # players, and nothing said about either of them.
        for slug in (f"{self.IDLE}-riverside-rockets",
                     f"{self.IDLE}-lakeside-spinners"):
            with self.subTest(slug=slug):
                self.assertIsNotNone(self.player(self.quiet_split, slug))
        self.assertIsNone(self.player(self.quiet_split, self.IDLE))
        self.assertEqual(len(self.quiet_split["players"]), 12)
        self.assertEqual(
            [n for n in (*self.quiet_split["_notes"],
                         *self.quiet_split["_config_warnings"])
             if "[namesakes]" in n], [])
        self.assertEqual(self.flagged(self.quiet_split, "player-two-rosters"), [])

    @staticmethod
    def rows(season: dict) -> list[dict]:
        return [{k: v for k, v in t.items() if k != "players"}
                for t in season["teams"]]

    @staticmethod
    def gap(season: dict) -> dict:
        return next(t["contribution_gap"] for t in season["teams"]
                    if t["slug"] == "lakeside-spinners")


# --- byes: what is inferred, what is stated, and what a bye is worth ----------

# Three teams and three rounds is the smallest competition that has a bye at all:
# one team rests each week. Mondays, so the routing that files a card by the
# weekday of its own date cannot confuse this with the Tuesday season above, and no
# two team names share a first word, so no prefix is stripped from any of them.
BYE_TEAMS = {
    "sundown-larks": ("SUNDOWN LARKS",
                      ("Mira Okonkwo", "Nils Perrault", "Oona Devlin")),
    "tollgate-moths": ("TOLLGATE MOTHS",
                       ("Pia Halloran", "Quen Adeyemi", "Rafe Sotiris")),
    "verge-newts": ("VERGE NEWTS",
                    ("Suki Ferreira", "Tam Brogan", "Uli Naismith")),
}
BYE_DATES = {"1": "2026-08-17", "2": "2026-08-24", "3": "2026-08-31"}
BYE_FIXTURES = (
    ("1", "sundown-larks", "tollgate-moths",
     ((6, 1), (6, 2), (6, 0), (6, 3))),                  # 24-6,  larks
    ("2", "sundown-larks", "verge-newts",
     ((6, 3), (6, 4), (4, 6), (6, 2))),                  # 22-15, larks
    ("3", "tollgate-moths", "verge-newts",
     ((6, 4), (6, 3), (3, 6), (6, 2))),                  # 21-15, moths
)
# Who is missing from each round, which is who rests in it.
BYE_RESTING = {"1": ["verge-newts"], "2": ["tollgate-moths"],
               "3": ["sundown-larks"]}

# Worked out on paper from games-bonus (win 4, draw 3, loss 2, plus a tenth per
# game won), decided matches only -- two each:
#
#   larks  win 24 -> 6.4 | win 22 -> 6.2          = 12.6 over 2 -> 6.30
#   moths  loss 6 -> 2.6 | win 21 -> 6.1          =  8.7 over 2 -> 4.35
#   newts  loss 15 -> 3.5 | loss 15 -> 3.5        =  7.0 over 2 -> 3.50
BYE_EARNED = {"sundown-larks": 12.6, "tollgate-moths": 8.7, "verge-newts": 7.0}
BYE_AVERAGES = {"sundown-larks": 6.30, "tollgate-moths": 4.35, "verge-newts": 3.50}
# The bye credited at the team's own average: three counted instead of two.
BYE_AVERAGED_TOTALS = {slug: round(avg * 3, 10)
                       for slug, avg in BYE_AVERAGES.items()}
BYE_LADDER = ["sundown-larks", "tollgate-moths", "verge-newts"]


def write_bye_season_cards(data_dir: Path) -> None:
    for index, (rnd, home, away, scores) in enumerate(BYE_FIXTURES, start=1):
        write_bye_card(data_dir, index, rnd, home, away, scores)


def write_bye_card(data_dir: Path, index: int, rnd: str, home: str, away: str,
                   scores, *, name: str = "") -> None:
    """One card of the three-round season, into its round's folder."""
    folder = data_dir / f"Round{rnd}"
    folder.mkdir(parents=True, exist_ok=True)
    home_name, home_players = BYE_TEAMS[home]
    away_name, away_players = BYE_TEAMS[away]
    cw.write_card_file(folder / (name or f"r{rnd}.pdf"), cw.CardSpec(
        date=BYE_DATES[rnd], round_label=rnd, division="C",
        court=f"{index:02d}", match_id=f"300{index:04d}",
        home_name=home_name, away_name=away_name,
        rows=rows_for(scores, home_players, away_players),
        home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(home_players)),
        away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(away_players))))


class ByeSeasonCase(unittest.TestCase):
    """The three-round season, with whatever the subclass declares about it."""

    UNPLAYED = ""
    DECLARE_ROUND_THREE = False
    STATED_BYES: dict[str, tuple[str, ...]] = {}
    EXTRA_TEAMS: tuple[str, ...] = ()
    ROUNDS_TOTAL = None

    @classmethod
    def declared(cls):
        rounds = ()
        if cls.DECLARE_ROUND_THREE:
            # One round out of three, with its fixtures, exactly as a club would
            # type it after copying a line off the draw.
            rounds = (config_mod.RoundConfig(
                number="3", date=BYE_DATES["3"],
                fixtures=(("TOLLGATE MOTHS", "VERGE NEWTS"),)),)
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()) + cls.EXTRA_TEAMS,
            rounds=rounds, byes=dict(cls.STATED_BYES),
            rounds_total=cls.ROUNDS_TOTAL)

    @classmethod
    def write_cards(cls, data: Path) -> None:
        write_bye_season_cards(data)

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / "data"
        cls.write_cards(cls.data)
        cls.cards, cls.rejected = scorecard.read_tree(cls.data)
        declared = cls.declared()
        cls.config = config_mod.Config(path=cls.root / "config.toml",
                                       unplayed=cls.UNPLAYED,
                                       competitions={declared.id: declared})
        cls.discovery = discover_mod.discover(cls.cards, cls.rejected,
                                              config=cls.config, root=cls.data)
        cls.comp_id, = sorted(cls.discovery.matches)
        cls.competition = next(c for c in cls.discovery.club.competitions
                               if c.id == cls.comp_id)
        cls.matches = stats.with_byes(cls.discovery.matches[cls.comp_id],
                                      cls.competition)
        cls.season = export.build_season(
            cls.discovery.matches[cls.comp_id], competition=cls.competition,
            source_dir=cls.data, generated="2026-09-10T12:00:00",
            club=cls.discovery.club, cards=cls.cards, rejected=cls.rejected)
        cls.team = {t["slug"]: t for t in cls.season["teams"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def resting(self):
        return {number: sorted(self.competition.byes_for(number))
                for number in self.competition.round_order}


class TestEveryRoundsByeIsWorkedOutFromTheClosedList(ByeSeasonCase):
    """Nothing declared but the team list, which is all rule F1 asks for."""

    def test_the_team_that_is_missing_from_each_round_is_the_one_resting(self):
        self.assertEqual(self.resting(),
                         {n: sorted(v) for n, v in BYE_RESTING.items()})

    def test_the_bye_is_a_match_of_its_own_rather_than_a_gap(self):
        byes = sorted((m.round, m.home) for m in self.matches
                      if m.status is MatchStatus.BYE)
        self.assertEqual(byes, [("1", "verge-newts"), ("2", "tollgate-moths"),
                                ("3", "sundown-larks")])

    def test_no_bye_becomes_a_fixture(self):
        for number, rnd in self.competition.rounds.items():
            with self.subTest(round=number):
                self.assertEqual(len(rnd.fixtures), 1)


class TestDeclaringOneRoundKeepsTheByesInTheOthers(ByeSeasonCase):
    """Measured bug: declaring any round silently discarded every inferred bye.

    Two code paths reconstruct a draw -- one for a declared draw, one for the cards
    -- and only the second inferred anything. So a club that wrote down a single
    round, which is exactly what a club does when it wants to record one date or
    one note, lost the byes this program could already see in every other round.
    Nothing warned: three teams simply stopped being paid for resting.
    """

    DECLARE_ROUND_THREE = True
    ROUNDS_TOTAL = 3

    def test_the_declaration_was_actually_picked_up(self):
        # Without this the class would pass by testing the undeclared path twice.
        self.assertEqual(self.competition.rounds["3"].source,
                         "declared")
        self.assertEqual(self.competition.rounds["1"].source, "cards")

    def test_the_rounds_nobody_declared_still_know_who_rested(self):
        self.assertEqual(self.resting(),
                         {n: sorted(v) for n, v in BYE_RESTING.items()})

    def test_and_the_declared_round_knows_too(self):
        self.assertEqual(self.competition.byes_for("3"), ("sundown-larks",))

    def test_the_ladder_is_the_same_as_if_nothing_had_been_declared(self):
        self.assertEqual(stats.ladder(self.matches, self.competition), BYE_LADDER)


class TestAStatedByeCoversWhatTheCardsCannotShow(ByeSeasonCase):
    """The byes table: the weeks no card can account for.

    Round 4 here has no cards at all -- a public holiday, or a week called off
    before anybody travelled. Inference cannot see it, because there is nothing to
    be missing from, so the club states it and the round exists carrying only the
    rest.
    """

    STATED_BYES = {"SUNDOWN LARKS": ("4",), "TOLLGATE MOTHS": ("4",),
                   "VERGE NEWTS": ("4",)}
    ROUNDS_TOTAL = 4
    # The convention under which a bye is worth anything at all, which is the only
    # one where "paid for resting, not paid for a week nobody played" is a
    # distinction to be drawn.
    UNPLAYED = "average"

    def test_the_week_nobody_played_is_a_round_of_its_own(self):
        self.assertIn("4", self.competition.rounds)
        self.assertEqual(sorted(self.competition.byes_for("4")),
                         sorted(BYE_TEAMS))

    def test_it_invents_no_fixture_for_a_week_that_has_no_cards(self):
        self.assertEqual(self.competition.rounds["4"].fixtures, ())

    def test_it_pays_nobody_for_a_round_nobody_has_played(self):
        # F2's other half. The round is on the dashboard; it is not on the ladder.
        for slug in BYE_TEAMS:
            with self.subTest(team=slug):
                self.assertEqual(self.team[slug]["counted"], 3)

    def test_the_rounds_that_do_have_cards_are_unaffected(self):
        for number, expected in BYE_RESTING.items():
            with self.subTest(round=number):
                self.assertEqual(sorted(self.competition.byes_for(number)),
                                 sorted(expected))

    def test_the_stated_round_is_carried_into_the_season_document(self):
        rounds = {r["number"]: r for r in self.season["rounds"]}
        self.assertEqual(sorted(rounds["4"]["byes"]), sorted(BYE_TEAMS))


class TestAStatedByeCarriesRoundsInferenceWontDecide(ByeSeasonCase):
    """A grade of five whose cards are still coming in.

    Three fixtures are in and the rest of the draw is not, so every round is short
    of more than one team and inference refuses to pick a bye out of the gap --
    rule F1's other half, and the predecessor's most expensive bug: two teams
    missing is a missing card, not two byes. The club has the association's sheet
    in front of it and writes the byes down, and the statement has to be honoured
    for the rounds that already have cards, not only for the rounds that have none.
    """

    EXTRA_TEAMS = ("WOLDS PIPITS", "YARROW SHRIKES")
    STATED_BYES = {"VERGE NEWTS": ("1",), "TOLLGATE MOTHS": ("2",)}
    # As above: a stated bye and an unexplained gap only score differently where a
    # bye scores at all.
    UNPLAYED = "average"

    def test_all_five_teams_are_on_the_ladder_though_two_have_no_card(self):
        self.assertEqual(len(self.competition.teams), 5)

    def test_the_statement_is_honoured_in_a_round_that_has_cards(self):
        self.assertEqual(self.competition.byes_for("1"), ("verge-newts",))
        self.assertEqual(self.competition.byes_for("2"), ("tollgate-moths",))

    def test_a_round_nobody_wrote_down_still_infers_nothing(self):
        # Round three is short of three teams. Silence is the right answer.
        self.assertEqual(self.competition.byes_for("3"), ())

    def test_a_stated_bye_is_paid_and_an_unexplained_gap_is_not(self):
        # Newts rest in round one and are paid for it; larks are missing from
        # round three and are paid nothing, because nobody has said why.
        self.assertEqual(self.team["verge-newts"]["counted"], 3)
        self.assertEqual(self.team["sundown-larks"]["counted"], 2)

    def test_the_missing_cards_are_reported_rather_than_absorbed(self):
        report = validate.validate(self.discovery.matches[self.comp_id],
                                   self.competition, club=self.discovery.club,
                                   cards=self.cards, rejected=self.rejected)
        text = " ".join(f"{i.subject} {i.message} {i.detail}"
                        for i in report.warnings + report.errors)
        self.assertIn("wolds-pipits", text)


class TestAByeCreditedTheTeamsOwnAverage(ByeSeasonCase):
    """``unplayed = "average"``: the games-bonus preset's own convention."""

    UNPLAYED = "average"

    def test_the_convention_in_force_is_the_one_asked_for(self):
        self.assertTrue(self.competition.rules.average_unplayed)
        self.assertEqual(self.season["meta"]["rules"]["unplayed"], "average")

    def test_a_resting_team_is_paid_its_average_over_the_matches_it_decided(self):
        for slug, expected in BYE_AVERAGED_TOTALS.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(self.team[slug]["points"], expected,
                                       places=6)

    def test_the_bye_counts_in_the_average_and_not_in_the_played_column(self):
        # Two figures, both right, and neither is the other. `counted` is what the
        # average divides by; `played` is the matches whose result was decided, and
        # W, L and D add up to it -- a bye is none of the three. Points ÷ played is
        # not the average under this convention, which is why the dashboard prints
        # the divisor as its own column wherever the two differ.
        for slug in BYE_TEAMS:
            with self.subTest(team=slug):
                self.assertEqual(self.team[slug]["counted"], 3)
                self.assertEqual(self.team[slug]["played"], 2)

    def test_awarding_the_mean_does_not_move_the_mean(self):
        for slug, expected in BYE_AVERAGES.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(self.team[slug]["points_average"],
                                       expected, places=6)


class TestAByeWorthNothing(ByeSeasonCase):
    """``unplayed = "zero"``: the same points system, the other convention.

    Both are real grades' rules and no preset name distinguishes them, which is why
    this is a setting rather than a fifth preset. What it changes is the whole
    points column; what it must not change is who is top, because the average that
    orders the ladder is the average over decided matches either way.
    """

    UNPLAYED = "zero"

    def test_the_convention_in_force_is_the_one_asked_for(self):
        self.assertFalse(self.competition.rules.average_unplayed)
        self.assertEqual(self.season["meta"]["rules"]["unplayed"], "zero")

    def test_a_resting_team_is_paid_nothing_for_it(self):
        for slug, expected in BYE_EARNED.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(self.team[slug]["points"], expected,
                                       places=6)

    def test_the_bye_is_absent_rather_than_a_zero(self):
        # Counted as played it would halve nobody's average by a third; recorded
        # as a nil score it would. It is neither: it is not counted.
        for slug in BYE_TEAMS:
            with self.subTest(team=slug):
                self.assertEqual(self.team[slug]["counted"], 2)
                # The same number as the played column, so the dashboard's extra
                # column stays away: repeating P under a second heading teaches
                # nobody anything and is one more thing to read.
                self.assertEqual(self.team[slug]["played"], 2)

    def test_the_bye_is_still_shown_in_the_draw(self):
        # The club's dashboard should say why a team played two matches in three
        # weeks, whatever the round was worth.
        self.assertEqual(self.resting(),
                         {n: sorted(v) for n, v in BYE_RESTING.items()})

    def test_the_ladder_order_is_the_same_under_either_convention(self):
        self.assertEqual(stats.ladder(self.matches, self.competition), BYE_LADDER)

    def test_and_so_is_every_average(self):
        for slug, expected in BYE_AVERAGES.items():
            with self.subTest(team=slug):
                self.assertAlmostEqual(self.team[slug]["points_average"],
                                       expected, places=6)


class TestTheFixtureRowSaysACardIsMissingRatherThanScheduled(ByeSeasonCase):
    """The flag the dashboard's fixture row is drawn from, in the document.

    Python decides and JavaScript renders, which is the rule everywhere in this
    project: the row cannot work out for itself whether the season went past its
    round, because it would need the whole draw and the same clock-free rule, in a
    second language. So the exporter puts the answer on the fixture.

    Round 2 here declares two fixtures. One has the card the season really holds;
    the other names a team invented for this class and no card mentions it. Round 3
    has a card, so the season is past round 2 either way.

    Round 4 is declared as well and the season has no cards for it or after it, which
    is what pins the flag as something other than ``not played``: a page drawn off
    ``played`` would print "no card yet" over every week of a draw typed in during
    August.
    """

    EXTRA_TEAMS = ("HOLLOW WRENS",)
    AHEAD = "4"             # declared, no cards, nothing after it either
    # Declaring rounds without saying how long the season is declares a season that
    # long, and F3 would report the rest as rounds nobody said would be played.
    ROUNDS_TOTAL = 4

    @classmethod
    def declared(cls):
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()) + cls.EXTRA_TEAMS,
            rounds_total=cls.ROUNDS_TOTAL,
            rounds=(config_mod.RoundConfig(
                number="2", date=BYE_DATES["2"],
                fixtures=(("SUNDOWN LARKS", "VERGE NEWTS"),
                          ("TOLLGATE MOTHS", "HOLLOW WRENS"))),
                    config_mod.RoundConfig(
                number=cls.AHEAD, date="2026-09-07",
                fixtures=(("SUNDOWN LARKS", "TOLLGATE MOTHS"),))))

    def fixture(self, number, home, away):
        found = [f for f in self.season["fixtures"]
                 if (f["round"], f["home"], f["away"]) == (number, home, away)]
        self.assertEqual(len(found), 1, f"no one fixture row for R{number} "
                                       f"{home} v {away}")
        return found[0]

    def test_the_declaration_was_actually_picked_up(self):
        # Without this the class passes by testing a derived draw, where nothing
        # can be missing and every assertion below is vacuous.
        self.assertEqual(self.competition.draw_source, DRAW_DECLARED)
        self.assertEqual(self.competition.rounds["2"].source, DRAW_DECLARED)

    def test_the_declared_fixture_no_card_names_is_flagged(self):
        self.assertIs(
            self.fixture("2", "tollgate-moths", "hollow-wrens")["card_missing"],
            True)

    def test_the_fixture_that_has_its_card_is_not(self):
        self.assertIs(
            self.fixture("2", "sundown-larks", "verge-newts")["card_missing"],
            False)

    def test_a_fixture_in_a_round_the_season_has_not_reached_is_not_flagged(self):
        # The flag is not `played` spelled another way, and the row is the only place
        # the difference shows: this fixture has no card and none is owed, because
        # nothing after it has one either.
        row = self.fixture(self.AHEAD, "sundown-larks", "tollgate-moths")
        self.assertIs(row["played"], False)
        self.assertIs(row["card_missing"], False)

    def test_the_document_tells_the_dashboard_about_it_as_well(self):
        # The row says it where the reader is looking; the validation block is what
        # the Data health section lists, and it is the half that keeps.
        subjects = [w["subject"] for w in self.season["validation"]["warnings"]
                    if w["rule"] == "card-missing"]
        self.assertEqual(subjects, ["R2 tollgate-moths v hollow-wrens"])

    def test_a_missing_card_does_not_fail_the_build(self):
        self.assertIs(self.season["validation"]["ok"], True)


class TestACardTheDrawHasReversedLeavesNoRowSayingItIsMissing(ByeSeasonCase):
    """Two rows about one card, and neither of them a fixture short of one.

    A club that typed a round's fixture with the sides the other way round has a card
    matching no declared pair, so the document carries both: the declared fixture,
    unplayed, and the card itself in a row of its own. ``draw-reversed`` is the error
    that says so and the one thing to act on. A second finding calling that fixture's
    card missing would be about a file the club is looking at.

    It is also the only shape of season that reaches the exporter's second branch, so
    this is where the rows are checked for being one shape. A reader that has to know
    which branch produced a row before it can ask about a missing card gets
    ``undefined``, which is falsy -- an answer to a question nobody asked.
    """

    ROUNDS_TOTAL = 3
    DRAW_ORDER = ("verge-newts", "tollgate-moths")      # what the club typed
    CARD_ORDER = ("tollgate-moths", "verge-newts")      # what the scorecard says

    @classmethod
    def declared(cls):
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()),
            rounds_total=cls.ROUNDS_TOTAL,
            rounds=(config_mod.RoundConfig(
                number="3", date=BYE_DATES["3"],
                fixtures=(("VERGE NEWTS", "TOLLGATE MOTHS"),)),))

    def row(self, home, away):
        found = [f for f in self.season["fixtures"]
                 if (f["round"], f["home"], f["away"]) == ("3", home, away)]
        self.assertEqual(len(found), 1, f"not one row for R3 {home} v {away}: "
                                       f"{self.season['fixtures']}")
        return found[0]

    def test_the_draw_really_is_the_other_way_round(self):
        # Without this the class could pass with the draw and the card agreeing, and
        # the branch it exists to reach would never run.
        rules = [e["rule"] for e in self.season["validation"]["errors"]]
        self.assertIn("draw-reversed", rules)

    def test_the_card_keeps_a_row_of_its_own(self):
        # Dropping it would hide the very match that proves the draw wrong.
        self.assertIs(self.row(*self.CARD_ORDER)["played"], True)

    def test_neither_row_says_a_card_is_missing(self):
        for home, away in (self.DRAW_ORDER, self.CARD_ORDER):
            with self.subTest(fixture=f"{home} v {away}"):
                self.assertIs(self.row(home, away)["card_missing"], False)

    def test_every_row_answers_the_question(self):
        kinds = {tuple(sorted(row)) for row in self.season["fixtures"]}
        self.assertEqual(len(kinds), 1, "the fixture rows are not one shape: "
                                       f"{sorted(sorted(k) for k in kinds)}")
        self.assertIn("card_missing", kinds.pop())


class TestACardTheDrawDoesNotListIsUnderItsOwnRowWithEveryCardOfItsNight(ByeSeasonCase):
    """The exporter's second branch, the row for a card the declared draw has no line for.

    A page that walks the fixtures meets a card only through the row it is listed
    under, so this row has to list every card of its night, as the draw's own rows
    do. Nothing else reaches the branch: a season whose draw comes off the cards has
    every card's fixture in it. Here the club typed round 3 the other way round, as
    in the class above, and exported the card twice. Rejected: the first card's key
    alone, the shape the row's ``match_id`` had, which leaves the copy under no row
    and so on no part of ``#/matches``.
    """

    CARD_ORDER = ("tollgate-moths", "verge-newts")      # what the scorecard says

    @classmethod
    def declared(cls):
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()),
            rounds_total=3,
            rounds=(config_mod.RoundConfig(
                number="3", date=BYE_DATES["3"],
                fixtures=(("VERGE NEWTS", "TOLLGATE MOTHS"),)),))

    @classmethod
    def write_cards(cls, data: Path) -> None:
        write_bye_season_cards(data)
        # The same night exported again, as a browser names a second download.
        index, (rnd, home, away, scores) = next(
            (i, f) for i, f in enumerate(BYE_FIXTURES, start=1) if f[0] == "3")
        write_bye_card(data, index, rnd, home, away, scores, name="r3 (1).pdf")

    def test_its_row_lists_every_card_of_its_night(self):
        fixture = ("3", *self.CARD_ORDER)
        # The pre-flight: the card's pair is not the draw's, so its row is the
        # second branch's, and there are two cards for it.
        self.assertIn("draw-reversed",
                      [e["rule"] for e in self.season["validation"]["errors"]])
        cards = [m for m in self.season["matches"]
                 if (m["round"], m["home"], m["away"]) == fixture]
        self.assertEqual(len(cards), 2)
        row, = [f for f in self.season["fixtures"]
                if (f["round"], f["home"], f["away"]) == fixture]
        self.assertEqual(row["keys"], ["3/tollgate-moths/verge-newts/1",
                                       "3/tollgate-moths/verge-newts/2"])
        self.assertEqual(row["keys"], [m["key"] for m in cards])
        listed = [key for f in self.season["fixtures"] for key in f["keys"]]
        self.assertEqual(sorted(listed),
                         sorted(m["key"] for m in self.season["matches"]))


class TestNoRoundOrFixtureListsACardByTheIdItPrinted(ByeSeasonCase):
    """``rounds[].matches`` and ``fixtures[].match_id``: a second way to a card, and lossy.

    Written beside ``keys`` since those came in, and read by nothing: the page finds
    a card by its key (TestEveryCardIsFoundByAKeyNoOtherCardHas) and a night's own
    page by the card's ``id``. Measured before on the demo season
    (``/tmp/t7/scratch/E1/probe.py``): every round listed its 3 cards twice, by id
    under ``matches`` and by key under ``keys``, and every fixture carried its first
    card's id. With the round 2 Brindle Wombats v Brindle Bunyips card printing no id,
    round 2's ``matches`` held 2 ids for its 3 cards and 3 keys, and that fixture's
    ``match_id`` was ``""``: a reader taking either at its word, as the page did
    before the keys, misses that card. So both go. Both rows of a fixture had the id,
    the draw's and the one for a card the draw has no line for, so the club here typed
    round 3 the other way round, as in the class above, and the document has each.
    Rejected: keeping them, documented as informational, which is a list a reader can
    take for every card of the round and is not.
    """

    @classmethod
    def declared(cls):
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()),
            rounds=(config_mod.RoundConfig(
                number="3", date=BYE_DATES["3"],
                fixtures=(("VERGE NEWTS", "TOLLGATE MOTHS"),)),))

    def test_no_round_lists_its_cards_by_id(self):
        rounds = self.season["rounds"]
        self.assertEqual([r["number"] for r in rounds], ["1", "2", "3"])
        for rnd in rounds:
            with self.subTest(round=rnd["number"]):
                self.assertNotIn("matches", rnd)
                # Every card of it is still listed, by the key the page finds it by.
                self.assertEqual(rnd["keys"], [m["key"] for m in self.season["matches"]
                                               if m["round"] == rnd["number"]])
                self.assertTrue(rnd["keys"])

    def test_no_fixture_names_a_card_by_id_on_either_of_its_rows(self):
        # The pre-flight: the draw's row for round 3 has no card, and the card has
        # a row of its own, the exporter's second branch.
        self.assertIn("draw-reversed",
                      [e["rule"] for e in self.season["validation"]["errors"]])
        rows = {(f["round"], f["home"], f["away"]): f for f in self.season["fixtures"]}
        self.assertFalse(rows["3", "verge-newts", "tollgate-moths"]["played"])
        self.assertTrue(rows["3", "tollgate-moths", "verge-newts"]["played"])
        self.assertEqual(len(rows), len(BYE_FIXTURES) + 1)
        for key, row in rows.items():
            with self.subTest(fixture=key):
                self.assertNotIn("match_id", row)
                self.assertEqual(len(row["keys"]), row["cards"])


class TestARoundTheSeasonWentPastIsNotARoundNobodyHasReached(unittest.TestCase):
    """The build's own note about a declared round with no cards, which said both.

    Measured: every wholly uncarded round in a declared draw was collected into one
    sentence ending "which is what a round nobody has played yet looks like". For a
    club that types its whole draw in during August that is true of round 18 and
    flatly wrong about round 2 -- and round 2 is the one with a scorecard sitting
    unexported in somebody's downloads.

    Told apart by :func:`~matchcentre.stats.later_round_with_cards`, which is the
    same function ``card-missing`` is decided by, so the sentence on the terminal and
    the findings in the dashboard cannot come to disagree about one round.

    Round 2's cards are deleted here rather than never written, because that is the
    shape of the real fault: the round happened, the PDFs exist, and this program is
    looking at a folder that does not have them.
    """

    MISSING = "2"           # the round whose folder is removed
    AHEAD = "4"             # declared, no cards, nothing after it either

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / "data"
        write_bye_season_cards(cls.data)
        shutil.rmtree(cls.data / f"Round{cls.MISSING}")
        cls.cards, cls.rejected = scorecard.read_tree(cls.data)
        declared = config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()),
            rounds_total=4,
            rounds=(
                config_mod.RoundConfig(
                    number=cls.MISSING, date=BYE_DATES["2"],
                    fixtures=(("SUNDOWN LARKS", "VERGE NEWTS"),)),
                config_mod.RoundConfig(
                    number=cls.AHEAD, date="2026-09-07",
                    fixtures=(("SUNDOWN LARKS", "TOLLGATE MOTHS"),)),
            ))
        cls.config = config_mod.Config(path=cls.root / "config.toml",
                                       competitions={declared.id: declared})
        cls.discovery = discover_mod.discover(cls.cards, cls.rejected,
                                              config=cls.config, root=cls.data)
        cls.comp_id, = sorted(cls.discovery.matches)
        cls.competition = next(c for c in cls.discovery.club.competitions
                               if c.id == cls.comp_id)
        cls.matches = cls.discovery.matches[cls.comp_id]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def note_for(self, number):
        # Located by the note's own prefix and the round it names, not by a phrase
        # out of its prose: the tests below are about what the two notes say, and a
        # locator that spelt one of them would fail a rewording twice over.
        found = [n for n in self.discovery.notes
                 if n.startswith(f"{self.comp_id}: round {number} ")]
        self.assertEqual(len(found), 1, f"not one note about round {number}: "
                                       f"{self.discovery.notes}")
        return found[0]

    def test_the_two_rounds_really_are_the_two_cases(self):
        # Without this the class could pass with both rounds in one state, and the
        # distinction it exists for would go untested.
        self.assertEqual(self.competition.draw_source, DRAW_DECLARED)
        present = stats.rounds_present(self.matches)
        self.assertNotIn(self.MISSING, present)
        self.assertNotIn(self.AHEAD, present)
        self.assertIn("3", present, "nothing is later than the deleted round, so "
                                    "there is no evidence the season went past it")

    # Both notes are matched on what they say rather than on how they spell it: the
    # claim is that one names the season as having gone past the round and the cards
    # as something to go and find, and the other does neither. Reworded prose that
    # still draws the distinction has to keep passing, or this pins an author's
    # sentence instead of a club's reading of it.
    def test_the_round_the_season_went_past_is_named_as_cards_to_find(self):
        said = self.note_for(self.MISSING)
        self.assertRegex(said, r"\bpast\b", "the note does not say the season has "
                                            "gone past this round")
        self.assertRegex(said, r"\bfind\b", "the note does not say the cards are "
                                            "there to be found")

    def test_the_round_ahead_is_still_a_round_nobody_has_played(self):
        said = self.note_for(self.AHEAD)
        self.assertRegex(said, r"(?i)nobody has", "the note does not say this is a "
                                                  "round nobody has played")
        self.assertNotRegex(said, r"\bpast\b|\bfind\b",
                            "a round the season has not reached is described as one "
                            "it went past, with scorecards to go and find")

    def test_the_two_are_not_run_together_into_one_sentence(self):
        self.assertNotEqual(self.note_for(self.MISSING), self.note_for(self.AHEAD))

    def test_only_the_round_the_season_went_past_is_a_finding(self):
        # The note is printed once, at build time. This is the half that keeps, and
        # the round nobody has reached must stay out of it: a club that declares its
        # season in August would open a dashboard listing faults for every week of it.
        report = validate.validate(self.matches, self.competition)
        subjects = [i.subject for i in report.warnings if i.rule == "card-missing"]
        self.assertEqual(subjects,
                         [f"R{self.MISSING} sundown-larks v verge-newts"])


class TestARestTheCardsOverruleIsSaidOnceAndDrawnNowhere(ByeSeasonCase):
    """A bye stated for a team with a card that round: one warning, and no rest drawn.

    Filed as a dead error. The validator had an error for a bye match lying beside a
    card, and :func:`validate.validate` is handed the cards and nothing else -- the
    byes are credited downstream, by :func:`stats.with_byes` -- so it could never find
    one. It was kept as a guard for a draw-PDF reader, and no such reader is planned.

    What it was for does happen, one step further on. Measured on the demo season
    before this change, a byes table resting Brindle Quolls in round one, the week
    Quolls played Bunyips, drew "Brindle Quolls bye" on the Matches page directly
    under Quolls' own card. The warning that did fire said "the cards draw rests
    brindle-quolls", which blames the cards for a line the club typed. What the
    ladder paid for it is the class below, because this season has one fixture a
    round and cannot show it.

    So that error is deleted, and ``bye-contradicted`` is the one finding: once per
    rest however many cards contradict it, and a warning, because the team played
    and no bye is paid. An error was rejected. The table's line is stale, and
    failing the run would hold up a ladder that is right about it.

    Larks rest in round one here by statement, and play in it twice. Their two
    cards disagree, so F9 counts neither, and the warning used to say "The card
    wins" of them all the same. It names no winner now where the ladder counts no
    card, and it names the first card read, so it points at the same file on every
    run.
    """

    STATED_BYES = {"SUNDOWN LARKS": ("1",)}
    UNPLAYED = "average"
    # The same fixture and the same people, with larks winning 24-16 rather than
    # 24-6, so F9 has two results and refuses to choose between them.
    DISAGREEING = ((6, 4), (6, 4), (6, 4), (6, 4))

    @classmethod
    def write_cards(cls, data):
        super().write_cards(data)
        write_bye_card(data, 1, "1", "sundown-larks", "tollgate-moths",
                       cls.DISAGREEING, name="r1 again.pdf")

    def findings(self):
        found = self.season["validation"]
        return found["errors"] + found["warnings"]

    def test_it_is_said_once_as_a_warning_that_counts_neither_card_nor_rest(self):
        about = [i for i in self.findings() if i["subject"] == "R1 sundown-larks"]
        self.assertEqual([(i["rule"], i["severity"]) for i in about],
                         [("bye-contradicted", validate.WARNING)])
        message = about[0]["message"]
        # "the cards draw rests" was the wording whenever the draw came off the
        # cards, which is the only way a stated rest ever meets a card in one. The
        # club's own spelling, because the line to change is in config.toml.
        self.assertTrue(message.startswith(
            "round 1: the draw rests SUNDOWN LARKS, but there is a card for it "
            "that round. "), message)
        self.assertIn("No bye was credited", message)
        self.assertIn("the ladder does not count the card either", message)
        self.assertNotIn("The card wins", message)
        # The first of the two in the order they were read. Either is a true
        # detail, and only one of them is the same file on every run.
        self.assertRegex(about[0]["detail"], r"^card .*/Round1/r1 again\.pdf$")

    def test_the_page_draws_no_rest_beside_the_cards(self):
        rounds = {r["number"]: r for r in self.season["rounds"]}
        self.assertEqual(rounds["1"]["byes"], [])
        # The rest nobody contradicts is still drawn, so this is not a page that
        # has stopped drawing rests.
        self.assertEqual(rounds["3"]["byes"], ["sundown-larks"])

    def test_the_validator_is_handed_the_cards_and_never_a_bye(self):
        # Why no check there can be about a bye match: none arrives. It is why
        # the two bye errors this replaces never fired, so it is pinned where the
        # next check like them would be written.
        credited = [(m.round, m.home) for m in self.matches if m.is_bye]
        self.assertIn(("3", "sundown-larks"), credited)
        with mock.patch.object(validate, "validate",
                               wraps=validate.validate) as seen:
            export.build_season(
                self.discovery.matches[self.comp_id],
                competition=self.competition, source_dir=self.data,
                generated="2026-09-10T12:00:00", club=self.discovery.club,
                cards=self.cards, rejected=self.rejected)
        handed = seen.call_args.args[0]
        self.assertTrue(handed, "the validator was handed nothing at all")
        self.assertEqual([m for m in handed if m.is_bye], [])


class TestANightTheLadderRefusedIsNotPaidAsARestInstead(SeasonCase):
    """A rest stated for a team whose own card the ladder refused: not paid.

    This is the state the deleted error for a bye beside a card was written for, and
    it occurs, just not where that check looked. Measured on the demo season before
    this change: take a byes table that rests Brindle Quolls in round one, and put a
    second card for Quolls v Bunyips beside the first that disagrees with it. F9
    drops both from the ladder, and ``with_byes`` was handed what was left. The
    round's other card still counts, so the round was played, and nothing left in it
    named Quolls. So they were paid their average for the week: counted 5, 26.0
    points, against 4 and 20.8 once the rest is refused. All the while
    ``bye-contradicted`` said "The card wins and no bye was credited". A level card
    under ``draws = false`` is dropped by :func:`stats.priceable` instead, and was
    paid for the same way, at 5 and 26.0 again.

    Fixed where the bye is made, rather than by bringing the error back to life.
    :func:`stats.rests_standing` is handed every card read, the ones the ladder set
    aside included, and reads the round's fixtures, which a draw read off the cards
    builds from every card too. Here both refuse the rest. Two classes on, a
    declared round leaves only the cards to do it. The rejected alternative was an
    error printed beside the payment: a finding about a wrong ladder that goes on
    publishing it.

    Rockets rest in round one here by statement, and their card against aces is
    there twice with two results. Spinners v wanderers still counts.
    """

    # The same night and the same people, rockets winning 22-9 rather than 24-6.
    SECOND = ((6, 1), (6, 2), (6, 0), (4, 6))

    @classmethod
    def make_config(cls):
        return config_mod.Config(
            path=cls.root / "config.toml", unplayed="average",
            competitions={"tuesday-b": config_mod.CompetitionConfig(
                id="tuesday-b", day="Tuesday", division="B",
                teams=tuple(name for name, _ in TEAMS.values()),
                byes={"RIVERSIDE ROCKETS": ("1",)})})

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        cw.write_card_file(cls.data / "Round1" / "again.pdf", cw.CardSpec(
            date=DATES["1"], round_label="1", division="B",
            court="01", match_id="2000001",
            home_name=home_name, away_name=away_name,
            rows=rows_for(cls.SECOND, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    def test_the_team_is_paid_for_neither_the_night_nor_a_rest(self):
        self.assertEqual([i.subject for i in self.report.errors if i.rule == "F9"],
                         ["R1 riverside-rockets v hilltop-aces"],
                         "the two cards were not refused, so this is a counted "
                         "card and proves nothing about a refused one")
        self.assertIn("R1 riverside-rockets",
                      [i.subject for i in self.report.warnings
                       if i.rule == "bye-contradicted"])
        # Rounds two and three, 6.2 and 6.4. Paid round one's rest at that average
        # as well, it was 3 and 18.9.
        rockets, = [t for t in self.season["teams"]
                    if t["slug"] == "riverside-rockets"]
        self.assertEqual(rockets["counted"], 2)
        self.assertAlmostEqual(rockets["points"], 12.6, places=6)
        self.assertEqual([m.round for m in stats.ladder_matches(
                              self.matches, self.competition)
                          if m.is_bye], [])


class TestARoundThatRestsATeamItAlsoFixturesIsAnError(ByeSeasonCase):
    """``bye-and-fixture``: one round's draw has a team resting and playing.

    The deleted error for a bye the declared draw does not rest could not happen.
    It compared a bye match with the draw's rests, nothing hands the validator a
    bye match, and the one thing that makes one, :func:`stats.with_byes`, makes it
    *from* the draw's rests. So the only question that check could ask had its
    answer built in.

    A draw can contradict itself in a way that costs points, though, and this was
    measured. The run's own review block offered a card-contradicted rest back as
    `bye =`, in a round whose fixtures have that team playing. With the round pasted
    in and Quolls' card present, the card settles it. With the card missing, the
    ladder paid Quolls a bye for the week, counted 5 and 26.0 points, off the half of
    the draw nobody could check. The only finding was the missing card, and the run
    passed.

    Now refused, and reported. No bye is credited for a rest its own round also
    fixtures. Once the round has a card the run fails, because the ladder is right
    only if the fixture is and nothing read here can say which. Crediting it, as
    before, was rejected: it pays points on one of two statements that contradict
    each other. Before the round has a card it is a warning. No figure depends on it
    yet, and as an error a draw typed in whole before the season would fail every
    build over a week months off. It is said all the same, because the page draws
    the fixture and not the rest from the first build, and a rest left off the page
    with nothing saying why is a rest that vanished.

    The fixture's missing card is asked for inside the error, as the other half of
    the fix, and not again as ``card-missing``. Said twice, the one typo read as two
    things to do, and doing the second alone left the first in place.

    The typo here is the fixture. Newts really do rest in round one, and the club
    has also typed Newts v Wrens, with Newts at home. Round four has the same
    contradiction for a week nobody has played, with the resting team, Larks, away.
    Owls v Quails is a real round-one fixture whose card never came.
    """

    EXTRA_TEAMS = ("HOLLOW WRENS", "IRONBARK OWLS", "JUNIPER QUAILS")
    UNPLAYED = "average"
    ROUNDS_TOTAL = 4

    @classmethod
    def declared(cls):
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()) + cls.EXTRA_TEAMS,
            rounds_total=cls.ROUNDS_TOTAL,
            rounds=(config_mod.RoundConfig(
                number="1", date=BYE_DATES["1"],
                fixtures=(("SUNDOWN LARKS", "TOLLGATE MOTHS"),
                          ("VERGE NEWTS", "HOLLOW WRENS"),
                          ("IRONBARK OWLS", "JUNIPER QUAILS")),
                byes=("VERGE NEWTS",)),
                    config_mod.RoundConfig(
                number="4", date="2026-09-07",
                fixtures=(("HOLLOW WRENS", "SUNDOWN LARKS"),),
                byes=("SUNDOWN LARKS",))))

    def findings(self, severity, rule):
        return [i for i in self.season["validation"][severity] if i["rule"] == rule]

    def test_it_is_an_error_naming_the_fixture(self):
        found = self.findings("errors", "bye-and-fixture")
        # Round one only. Round four says the same two things of a week nobody has
        # played, which is the warning below.
        self.assertEqual([(i["subject"], i["detail"]) for i in found],
                         [("R1 verge-newts", "declared: verge-newts v hollow-wrens")])
        # In the club's own spelling, because the fix is a line of config.toml.
        self.assertIn("rests VERGE NEWTS and also has it playing HOLLOW WRENS",
                      found[0]["message"])
        self.assertIs(self.season["validation"]["ok"], False)

    def test_it_says_what_to_do_whichever_line_is_wrong(self):
        message, = [i["message"] for i in self.findings("errors", "bye-and-fixture")]
        for step in ("If they played, export that card from Match Centre into this "
                     "round's folder and take the rest out of config.toml.",
                     "If they rested, take the fixture out."):
            with self.subTest(step=step):
                self.assertIn(step, message)

    def test_and_takes_the_rest_out_of_the_config_the_build_read(self):
        # build.py hands the season the name of the file it read, and the season
        # hands it to the checks, so the step that takes the rest out names it.
        season = export.build_season(
            self.discovery.matches[self.comp_id], competition=self.competition,
            source_dir=self.data, generated="2026-09-10T12:00:00",
            club=self.discovery.club, cards=self.cards, rejected=self.rejected,
            config_name="other.toml")
        message, = [i["message"] for i in season["validation"]["errors"]
                    if i["rule"] == "bye-and-fixture"]
        self.assertIn("round's folder and take the rest out of other.toml. If they "
                      "rested", message)

    def test_the_contradicting_fixtures_card_is_asked_for_once_inside_the_error(self):
        # Owls v Quails is asked for as ever, so this is not a round whose missing
        # cards have stopped being reported.
        self.assertEqual([i["subject"] for i in self.findings("warnings",
                                                                "card-missing")],
                         ["R1 ironbark-owls v juniper-quails"])

    def test_a_round_nobody_has_played_is_a_warning_naming_the_fixture(self):
        found = self.findings("warnings", "bye-and-fixture")
        self.assertEqual([(i["subject"], i["detail"]) for i in found],
                         [("R4 sundown-larks",
                           "declared: hollow-wrens v sundown-larks")])
        message = found[0]["message"]
        self.assertIn("rests SUNDOWN LARKS and also has it playing HOLLOW WRENS",
                      message)
        self.assertIn("the dashboard shows the fixture and not the rest", message)
        # No card to export for a week not yet played.
        self.assertIn("Take whichever line is wrong out of config.toml.", message)
        self.assertNotIn("export", message)

    def test_the_ladder_pays_no_rest_the_round_also_has_it_playing(self):
        # Two losses of 15 games, 3.5 each. Paid the rest, it was 3 and 10.5.
        newts = self.team["verge-newts"]
        self.assertEqual(newts["counted"], 2)
        self.assertAlmostEqual(newts["points"], BYE_EARNED["verge-newts"], places=6)

    def test_the_page_draws_the_fixture_and_not_a_rest(self):
        rounds = {r["number"]: r for r in self.season["rounds"]}
        # Newts at home in round one, Larks away in round four.
        for number in ("1", "4"):
            with self.subTest(round=number):
                self.assertEqual(rounds[number]["byes"], [])


class TestARestTheDrawAlsoFixturesThreeTimesNamesTheThreeAsASentence(ByeSeasonCase):
    """``bye-and-fixture`` lists three fixtures of a resting team as ``a, b and c``.

    ``_check_byes`` joined the teams the draw also has the resting team playing with
    `` and `` alone. Measured before, with Newts resting in round one and fixtured
    against Wrens, Owls and Quails (/tmp/t6/scratch/m23/probe-before.txt): ``the
    draw rests VERGE NEWTS and also has it playing HOLLOW WRENS and IRONBARK OWLS
    and JUNIPER QUAILS``. Two read ``HOLLOW WRENS and IRONBARK OWLS`` either way.
    So the list is ``scope.in_words``'. The detail lists the fixtures as it did.

    Rejected: one finding a fixture. The draw says one thing of the team, that it
    rests, and every fixture beside it contradicts that one line, which is one
    mistake.
    """

    EXTRA_TEAMS = ("HOLLOW WRENS", "IRONBARK OWLS", "JUNIPER QUAILS")
    UNPLAYED = "average"
    ROUNDS_TOTAL = 4

    @classmethod
    def declared(cls):
        return config_mod.CompetitionConfig(
            id="monday-c", day="Monday", division="C",
            teams=tuple(name for name, _ in BYE_TEAMS.values()) + cls.EXTRA_TEAMS,
            rounds_total=cls.ROUNDS_TOTAL,
            rounds=(config_mod.RoundConfig(
                number="1", date=BYE_DATES["1"],
                fixtures=(("SUNDOWN LARKS", "TOLLGATE MOTHS"),
                          ("VERGE NEWTS", "HOLLOW WRENS"),
                          ("IRONBARK OWLS", "VERGE NEWTS"),
                          ("VERGE NEWTS", "JUNIPER QUAILS")),
                byes=("VERGE NEWTS",)),))

    def test_the_three_fixtures_are_listed_as_a_sentence(self):
        found = [i for i in self.season["validation"]["errors"]
                 if i["rule"] == "bye-and-fixture"]
        self.assertEqual(len(found), 1, found)
        self.assertIn("round 1: the draw rests VERGE NEWTS and also has it playing "
                      "HOLLOW WRENS, IRONBARK OWLS and JUNIPER QUAILS, and no card "
                      "says which is true.", found[0]["message"])


class TestACardOverrulesARestTheDrawDoesNotListItFor(unittest.TestCase):
    """The card half of the rule, where the fixtures say nothing.

    A declared round lists what the club typed. Suppose a night is rescheduled:
    cods rest on the sheet, and then play aces that week after all. The card for
    that match is in no fixture the round lists, so only the card says cods played.
    Leave the cards out of :func:`stats.rests_standing` and the ladder pays cods a
    rest on top of the counted result while the report says nothing about the rest.
    The card is a ``draw-mismatch`` as well, which is the draw's own finding, under
    the match's subject rather than the team's.

    Built by hand, since the state is one round and one card, and built both ways
    round, because a card is the team's whether it was at home or away.

    The ladder and the report already had this half, off the cards they are
    handed. The page did not: it drew every rest the round states, and measured on
    this state before this change it drew cods resting in round one above cods' own
    card. It reads the same rule now, handed the same cards.
    """

    FMT = fmt_mod.match_format("doubles-singles-doubles")
    RULES = scoring.ruleset("games-bonus")
    SIDES = (("cods", "aces"), ("aces", "cods"))

    def setUp(self):
        self.comp = Competition(
            id="monday-c", day="Monday", division="C", fmt=self.FMT,
            rules=self.RULES, teams_declared=True, draw_source=DRAW_DECLARED,
            teams={slug: Team(slug=slug, name=slug.title())
                   for slug in ("aces", "bats", "cods")},
            rounds={"1": Round(number="1", date="2026-08-17",
                               fixtures=(("aces", "bats"),), byes=("cods",),
                               source=DRAW_DECLARED)})

    def card(self, home: str, away: str) -> Match:
        sets = tuple(SetResult(slot=slot, home_games=6, away_games=i,
                               home_players=(f"{home}-{i}",),
                               away_players=(f"{away}-{i}",), rule=self.FMT.set_rule)
                     for i, slot in enumerate(self.FMT.slots))
        return Match(competition="monday-c", round="1", home=home, away=away,
                     fmt=self.FMT, rules=self.RULES, sets=sets,
                     source="data/Round1/r1.pdf")

    def test_the_resting_team_is_paid_no_bye_beside_its_card(self):
        for home, away in self.SIDES:
            with self.subTest(card=f"{home} v {away}"):
                self.assertEqual(
                    [m for m in stats.ladder_matches([self.card(home, away)],
                                                     self.comp) if m.is_bye], [])

    def test_the_report_says_the_card_wins(self):
        for home, away in self.SIDES:
            with self.subTest(card=f"{home} v {away}"):
                about = [i for i in validate.validate([self.card(home, away)],
                                                      self.comp).issues
                         if i.subject == "R1 cods"]
                self.assertEqual([i.rule for i in about], ["bye-contradicted"])
                # The ladder counts this card, so it is the result.
                self.assertTrue(about[0].message.endswith(
                    "The card wins and no bye was credited."), about[0].message)

    def test_a_fixture_its_card_does_not_answer_is_still_asked_for(self):
        """A card-missing is left to the bye finding only where that one asks for it.

        ``bye-and-fixture`` asks for the card of the fixture it names, so the
        fixture's ``card-missing`` is folded into it. ``bye-contradicted`` asks for
        nothing. Here the draw has cods resting and playing bats, and the card has
        them playing aces, so the bats fixture has no card and nothing else says so.
        This passes on the tree before this change, which folded nothing. It pins
        the fold to the finding that asks: widened to every rest a round refuses,
        the missing card went unsaid.
        """
        comp = dataclasses.replace(self.comp, rounds={"1": Round(
            number="1", date="2026-08-17", fixtures=(("cods", "bats"),),
            byes=("cods",), source=DRAW_DECLARED)})
        report = validate.validate([self.card("cods", "aces")], comp)
        self.assertEqual(
            sorted((i.rule, i.subject) for i in report.issues
                   if i.rule in ("bye-contradicted", "bye-and-fixture",
                                 "card-missing")),
            [("bye-contradicted", "R1 cods"), ("card-missing", "R1 cods v bats")])

    def test_the_page_draws_no_rest_beside_its_card(self):
        for home, away in self.SIDES:
            with self.subTest(card=f"{home} v {away}"):
                with tempfile.TemporaryDirectory() as tmp:
                    season = export.build_season(
                        [self.card(home, away)], competition=self.comp,
                        source_dir=tmp, generated="2026-09-10T12:00:00")
                self.assertEqual([(r["number"], r["byes"])
                                  for r in season["rounds"]], [("1", [])])


class TestARescheduledNightTheLadderSetAsideIsNotPaidAsARest(unittest.TestCase):
    """The card half again, where the cards are ones the ladder refused.

    Cods rest in round one on the sheet, then play aces that week after all, and
    the two cards of that night disagree, so F9 counts neither. The round's declared
    fixtures never had the match, so the only evidence cods played is the two cards
    the ladder set aside. Measured on the demo season in the same shape, before this
    change (Quolls' round-one fixture taken out of a pasted draw, and a second,
    disagreeing card put beside theirs): the ladder paid Quolls the rest, counted 5
    and 26.0 against 4 and 20.8, and the page drew it beside their cards. A version
    that judged the page on every card and the ladder on the counted ones was
    measured too, and paid the same 26.0 under a page that drew no rest and a
    warning that said none was credited. So every caller hands the rule every card
    read. Judging all of them on the counted cards was rejected: the page, the
    report and the ladder would agree, on paying a rest for a night the team
    played.

    Handing the rule every card means the ladder's own list, which lacks the refused
    ones, must never be built into a ladder again, and :func:`stats.team_stats` and
    :func:`stats.ladder_history` both used to do that. Built again, the rest
    stands. So the teams here are made to tie: aces and cods each won one night by
    the same scoreline, the name puts aces first, and only a paid rest, 6.2 more
    points for cods, puts cods above them.
    """

    FMT = fmt_mod.match_format("doubles-singles-doubles")
    RULES = dataclasses.replace(scoring.ruleset("games-bonus"), average_unplayed=True)
    # Both round-two winners, 22 games to 17: 4 for the win and 2.2 for the games.
    WON = ((6, 4), (6, 4), (4, 6), (6, 3))

    def setUp(self):
        self.comp = Competition(
            id="monday-c", day="Monday", division="C", fmt=self.FMT,
            rules=self.RULES, teams_declared=True, draw_source=DRAW_DECLARED,
            teams={slug: Team(slug=slug, name=slug.title())
                   for slug in ("aces", "bats", "cods", "dogs")},
            rounds={"1": Round(number="1", date="2026-08-17",
                               fixtures=(("bats", "dogs"),), byes=("cods",),
                               source=DRAW_DECLARED),
                    "2": Round(number="2", date="2026-08-24",
                               fixtures=(("cods", "bats"), ("aces", "dogs")),
                               source=DRAW_DECLARED)})
        self.cards = [
            self.card("1", "bats", "dogs", ((6, 2), (6, 3), (6, 4), (6, 1)),
                      "bats-v-dogs"),
            self.card("1", "cods", "aces", ((6, 1), (6, 2), (6, 3), (6, 0)),
                      "cods-v-aces"),
            self.card("1", "cods", "aces", ((1, 6), (2, 6), (3, 6), (0, 6)),
                      "cods-v-aces again"),
            self.card("2", "cods", "bats", self.WON, "cods-v-bats"),
            self.card("2", "aces", "dogs", self.WON, "aces-v-dogs"),
        ]

    def card(self, rnd: str, home: str, away: str, games, name: str) -> Match:
        sets = tuple(SetResult(slot=slot, home_games=h, away_games=a,
                               home_players=(f"{home}-{i}",),
                               away_players=(f"{away}-{i}",), rule=self.FMT.set_rule)
                     for i, (slot, (h, a)) in enumerate(zip(self.FMT.slots, games)))
        return Match(competition="monday-c", round=rnd, home=home, away=away,
                     fmt=self.FMT, rules=self.RULES, sets=sets,
                     date=self.comp.rounds[rnd].date,
                     source=f"data/Round{rnd}/{name}.pdf")

    def test_the_ladder_pays_no_rest_for_it(self):
        self.assertEqual([(m.round, m.home) for m in stats.ladder_matches(
                              self.cards, self.comp) if m.is_bye], [])
        cods = stats.team_stats(self.cards, self.comp)["cods"]
        # Paid the rest, it was 2 and 12.4.
        self.assertEqual(cods.counted, 1)
        self.assertAlmostEqual(cods.points, 6.2, places=6)

    def test_every_position_is_the_ladder_the_cards_make(self):
        self.assertEqual(stats.ladder(self.cards, self.comp),
                         ["aces", "cods", "bats", "dogs"])
        rows = stats.team_stats(self.cards, self.comp)
        self.assertEqual({slug: row.position for slug, row in rows.items()},
                         {"aces": 1, "cods": 2, "bats": 3, "dogs": 4})
        self.assertEqual({slug: [(e.round, e.position) for e in row.ladder_history]
                          for slug, row in rows.items()},
                         {"aces": [("1", 3), ("2", 1)], "bats": [("1", 1), ("2", 3)],
                          "cods": [("1", 4), ("2", 2)], "dogs": [("1", 2), ("2", 4)]})

    def test_the_page_and_the_report_say_no_rest_was_paid(self):
        with tempfile.TemporaryDirectory() as tmp:
            season = export.build_season(self.cards, competition=self.comp,
                                         source_dir=tmp,
                                         generated="2026-09-10T12:00:00")
        self.assertEqual({r["number"]: r["byes"] for r in season["rounds"]},
                         {"1": [], "2": []})
        about = [i for i in season["validation"]["warnings"]
                 if i["subject"] == "R1 cods"]
        self.assertEqual([i["rule"] for i in about], ["bye-contradicted"])
        self.assertIn("the ladder does not count the card either",
                      about[0]["message"])
        self.assertEqual(about[0]["detail"], "card data/Round1/cods-v-aces.pdf")


# --- two grades of one section ------------------------------------------------

# Two teams a grade, four teams in the building, no prefix in common. Both grades
# play on the same Tuesday night and both cards head themselves `Division 3
# Grade ...`, so the only thing that tells the two competitions apart is the
# second half of that designation.
GRADE_TEAMS = {
    "A": (("MARINA MAGPIES", ("Ada Renn", "Bex Toloa", "Cai Ferris")),
          ("QUARRY QUOLLS", ("Dee Halvard", "Eli Prass", "Fay Ngo"))),
    "B": (("STATION STARLINGS", ("Gus Emmett", "Hux Bardo", "Ivy Salter")),
          ("TERRACE TEALS", ("Jem Okonkwo", "Kai Bright", "Lux Marris"))),
}

# (grade, round, home index, away index, per-set (home, away) games). Two rounds
# so that each two-team grade has a ladder every team played to the end of.
GRADE_FIXTURES = (
    ("A", "1", 0, 1, ((6, 2), (6, 3), (6, 1), (6, 4))),
    ("A", "2", 1, 0, ((6, 4), (3, 6), (6, 2), (6, 3))),
    ("B", "1", 0, 1, ((6, 0), (6, 1), (6, 2), (6, 3))),
    ("B", "2", 1, 0, ((2, 6), (6, 4), (1, 6), (3, 6))),
)

GRADE_DATES = {"1": "2026-08-18", "2": "2026-08-25"}


def write_two_grade_cards(data_dir: Path, *, shared: str | None = None,
                          shared_in: tuple[tuple[str, int], ...] = (("B", 0),)
                          ) -> None:
    """One card per fixture, the grade written where a real card writes it.

    ``shared`` reprints one name as the opening player of every ``(grade, team)``
    in ``shared_in``. Grade A's team 0 opens with ``Ada Renn`` already, so the
    default puts that one name in one team of each grade -- one printed name across
    two competitions, merged in neither.
    """
    for index, (grade, rnd, home_i, away_i, scores) in enumerate(GRADE_FIXTURES,
                                                                 start=1):
        folder = data_dir / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        home_name, home_players = GRADE_TEAMS[grade][home_i]
        away_name, away_players = GRADE_TEAMS[grade][away_i]
        if shared is not None:
            # Whichever end of this round's fixture each named team is at.
            if (grade, home_i) in shared_in:
                home_players = (shared,) + tuple(home_players[1:])
            if (grade, away_i) in shared_in:
                away_players = (shared,) + tuple(away_players[1:])
        cw.write_card_file(folder / f"grade{grade}-r{rnd}.pdf", cw.CardSpec(
            date=GRADE_DATES[rnd], round_label=rnd, division=f"3 Grade {grade}",
            court=f"{index:02d}", match_id=f"400{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))


class TestTwoGradesOfOneSectionAreTwoCompetitions(unittest.TestCase):
    """`Division 3 Grade A` and `Division 3 Grade B`, one club, one night.

    A grade letter behind a section number is how a good many associations name
    their divisions, and the two are separate competitions: different teams,
    different ladders, different champions. Reading one word after the first
    keyword makes both of them `3`, and then four teams that never played each
    other share a ladder.

    Nothing about that looks wrong from the outside. The rounds are present, the
    games balance, every card is accounted for, and the ladder is even complete
    under F10 -- so no warning fires and no figure is obviously out. It is only
    wrong in the one way this project cares most about: the page states a standing
    that no match on it was played for.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / "data"
        write_two_grade_cards(cls.data)
        cls.cards, cls.rejected = scorecard.read_tree(cls.data)
        cls.config = config_mod.Config(path=cls.root / "config.toml")
        cls.discovery = discover_mod.discover(cls.cards, cls.rejected,
                                              config=cls.config, root=cls.data)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def competition(self, grade: str):
        """The one discovered competition whose id carries this grade."""
        wanted = f"grade-{grade.lower()}"
        found = [c for c in self.discovery.club.competitions if wanted in c.id]
        self.assertEqual(len(found), 1,
                         f"expected one competition id containing {wanted}; the "
                         f"ids are {sorted(self.discovery.matches)}")
        return found[0]

    def test_all_four_cards_are_read_and_none_rejected(self):
        self.assertEqual(len(self.cards), len(GRADE_FIXTURES))
        self.assertEqual(self.rejected, [])

    def test_the_whole_designation_reaches_the_card(self):
        self.assertEqual(sorted({card.division for card in self.cards}),
                         ["3 Grade A", "3 Grade B"])

    def test_the_four_cards_make_two_competitions(self):
        self.assertEqual(len(self.discovery.matches), 2,
                         "two grades were routed as one competition")

    def test_each_competition_holds_only_its_own_two_teams(self):
        for grade, teams in GRADE_TEAMS.items():
            with self.subTest(grade=grade):
                expected = sorted(slugs.team_slug(name) for name, _ in teams)
                self.assertEqual(sorted(self.competition(grade).teams), expected)

    def test_each_grade_has_a_ladder_of_its_own(self):
        for grade in GRADE_TEAMS:
            with self.subTest(grade=grade):
                comp = self.competition(grade)
                matches = self.discovery.matches[comp.id]
                self.assertEqual(len(matches), 2)
                self.assertEqual(len(stats.ladder(matches, comp)), 2)
                self.assertTrue(stats.ladder_complete(matches, comp))

    def test_the_label_on_the_page_names_the_grade(self):
        self.assertEqual(self.competition("A").display_label,
                         "Tuesday Division 3 Grade A")
        self.assertEqual(self.competition("B").display_label,
                         "Tuesday Division 3 Grade B")


class TestOneNameInTwoGradesIsAlreadyTwoPlayers(unittest.TestCase):
    """The scope of ``[namesakes]``, which its own documentation had wrong.

    ``config.example.toml`` offered its worked entry as "two unrelated M Kellys,
    one in each of two grades' squads" -- and that is the one case the section can
    do nothing for. Every competition gets a dashboard of its own, with its own
    ladder, its own teams and its own players, so a name printed in two grades is
    two rows already and there is nothing to separate. A club taking the example at
    its word would have declared an entry, seen no change, and had no way to tell
    whether the section worked.

    So the claim is pinned here rather than left to the comment: nothing is
    merged, nothing is reported, and an entry declared anyway leaves each grade
    with the one player it already had -- under a new slug, which is the whole of
    what it achieved. The mirror claim, that two teams of one grade *are* merged
    and *are* reported, is :class:`TestTwoPlayersRecordedOnce`.

    Two builds of the same cards, one plain and one with the cross-grade entry
    declared, because "the declaration did nothing" is a statement about the
    difference between them and not about either alone. And the run says so, which
    is the part a club can act on: an entry no one competition holds all the teams
    of is a note of its own.
    """

    SHARED = "Ada Renn"          # grade A's opening player, reprinted in grade B
    MERGED = "ada-renn"
    # All four teams, which is the case the check above must not catch: the name is
    # merged inside grade A and inside grade B, and one entry has to name all four
    # because the entry is keyed on the name.
    ALL_FOUR = (("A", 1), ("B", 0), ("B", 1))
    BESIDE = (("A", 1), ("B", 0))
    TEAMS_OF = {"A": ("marina-magpies", "quarry-quolls"),
                "B": ("station-starlings", "terrace-teals")}

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.plain = cls.build(root / "plain")
        cls.declared = cls.build(root / "declared", namesakes={
            cls.MERGED: ("marina-magpies", "station-starlings")})
        cls.both = cls.build(
            root / "both", shared_in=cls.ALL_FOUR,
            namesakes={cls.MERGED: cls.TEAMS_OF["A"] + cls.TEAMS_OF["B"]})
        # Grade A's Quolls print the name too, so grade A merges two people, and an
        # entry naming a team of each grade parts them there.
        cls.beside_plain = cls.build(root / "beside-plain", shared_in=cls.BESIDE)
        cls.beside = cls.build(root / "beside", shared_in=cls.BESIDE, namesakes={
            cls.MERGED: ("marina-magpies", "station-starlings")})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def build(cls, root: Path, namesakes=None, shared_in=(("B", 0),)) -> dict:
        """Both grades built from one tree, keyed by grade letter, plus the notes."""
        data = root / "data"
        write_two_grade_cards(data, shared=cls.SHARED, shared_in=shared_in)
        config = config_mod.Config(path=root / "config.toml",
                                   namesakes=dict(namesakes or {}))
        cards, rejected = scorecard.read_tree(data)
        found = discover_mod.discover(cards, rejected, config=config, root=data)
        built = {"_notes": tuple(found.notes),
                 "_said": tuple(found.config_warnings)}
        for comp in found.club.competitions:
            grade = comp.id.rsplit("-", 1)[-1].upper()
            season = export.build_season(
                found.matches[comp.id], competition=comp, source_dir=data,
                generated="2026-09-10T12:00:00", club=found.club,
                cards=cards, rejected=rejected)
            season["_report"] = validate.validate(
                found.matches[comp.id], comp, club=found.club, cards=cards,
                rejected=rejected)
            built[grade] = season
        return built

    @staticmethod
    def slugs_of(season: dict) -> set[str]:
        return {player["slug"] for player in season["players"]}

    @staticmethod
    def standing(season: dict) -> list[tuple]:
        """The table a club reads, which is what must not move."""
        return [(t["slug"], t["position"], t["points"], t["games_won"])
                for t in season["teams"]]

    def test_the_name_really_is_printed_in_both_grades(self):
        # Otherwise everything below passes by describing a season nobody wrote.
        for grade in ("A", "B"):
            with self.subTest(grade=grade):
                self.assertIn(self.MERGED, self.slugs_of(self.plain[grade]))

    def test_each_grade_holds_its_own_player_of_that_name(self):
        # Two documents, one row each. The slug is the same in both because it is
        # the same printed name; what keeps them apart is that no page ever puts
        # the two documents together.
        for grade, team in (("A", "marina-magpies"), ("B", "station-starlings")):
            with self.subTest(grade=grade):
                held = [p for p in self.plain[grade]["players"]
                        if p["slug"] == self.MERGED]
                self.assertEqual(len(held), 1)
                self.assertEqual(held[0]["team"], team)
                self.assertEqual(held[0]["matches"], 2)

    def test_nothing_is_reported_because_nothing_was_merged(self):
        for grade in ("A", "B"):
            with self.subTest(grade=grade):
                report = self.plain[grade]["_report"]
                self.assertEqual(
                    [i.rule for i in report.issues
                     if i.rule in ("player-two-rosters", "player-two-teams")], [])
        self.assertEqual([n for n in self.plain["_notes"] if "namesakes" in n], [])

    def test_declaring_them_anyway_leaves_each_grade_the_player_it_had(self):
        # The whole finding. An entry across two grades is not refused -- the loader
        # never sees which teams share a competition -- and it is not a no-op
        # either: it renames the one row each grade already had. Nothing is joined,
        # nothing is parted, and every figure is the figure it was.
        for grade, team in (("A", "marina-magpies"), ("B", "station-starlings")):
            with self.subTest(grade=grade):
                before = self.slugs_of(self.plain[grade])
                after = self.slugs_of(self.declared[grade])
                self.assertEqual(after - before, {f"{self.MERGED}-{team}"})
                self.assertEqual(before - after, {self.MERGED})
                self.assertEqual(self.standing(self.declared[grade]),
                                 self.standing(self.plain[grade]))

    def test_the_run_says_no_one_competition_has_all_those_teams(self):
        # Without this the club's only evidence is a slug in a URL. The warning
        # names both teams, because which pair cannot be acted on is the whole
        # message, and it is a warning, which --quiet prints, and not a note too.
        found = [n for n in self.declared["_said"] if "[namesakes]" in n]
        self.assertEqual(len(found), 1, self.declared["_said"])
        self.assertEqual([n for n in self.declared["_notes"] if "[namesakes]" in n],
                         [])
        for team in ("marina-magpies", "station-starlings"):
            self.assertIn(team, found[0])
        self.assertIn(self.MERGED, found[0])

    def test_the_same_name_merged_in_both_grades_is_not_that_mistake(self):
        # The boundary of the note, and the reason it asks whether any one
        # competition holds *two* of the named teams rather than all of them. A name
        # merged in grade A and again in grade B needs all four teams in the one
        # entry -- [namesakes] is keyed on the name -- and that entry separates
        # people in both. Complaining about it would tell a club their only
        # available fix was wrong.
        self.assertEqual([n for n in (*self.both["_notes"], *self.both["_said"])
                          if "[namesakes]" in n], [])
        for grade in ("A", "B"):
            with self.subTest(grade=grade):
                slugs_seen = self.slugs_of(self.both[grade])
                self.assertNotIn(self.MERGED, slugs_seen)
                for team in self.TEAMS_OF[grade]:
                    self.assertIn(f"{self.MERGED}-{team}", slugs_seen)

    def test_an_entry_that_parts_two_in_one_grade_is_not_told_to_go(self):
        """Two teams in two grades, and the entry still parts two people in one.

        The cross-grade check asked whether one competition holds two of the teams
        named, and the entry is not judged by its teams: the record it leaves
        behind, the name alone, is a person too. Measured through ``build.py`` on
        the unfixed tree (/tmp/t7/scratch/DW1/probe.py, g-cross-bare): grade A's
        Magpies and Quolls both print ``Ada Renn``, and ``ada-renn =
        ["marina-magpies", "station-starlings"]`` gave grade A two records,
        ``ada-renn`` and ``ada-renn-marina-magpies``, while the note said the entry
        "separated nobody. Leave it off"; left off, grade A merged the two and the
        run exited 1 with two ``player-two-teams`` errors, one a round, beside a
        ``player-two-rosters`` warning. Judged instead on the records the entry
        leaves on one page, two here, so nothing is said. Rejected: taking one team
        left, or no competition shared, for an entry that separated nobody, which is
        this same false sentence.
        """
        self.assertEqual([n for n in (*self.beside["_notes"], *self.beside["_said"])
                          if "[namesakes]" in n], [])
        self.assertEqual(self.slugs_of(self.beside["A"]) & {
            self.MERGED, f"{self.MERGED}-marina-magpies"},
            {self.MERGED, f"{self.MERGED}-marina-magpies"})
        # And the advice it used to give, followed, is the merge: one player on two
        # courts in each round, which is what fails the run.
        issues = self.beside_plain["A"]["_report"].issues
        self.assertEqual(
            [(i.rule, i.severity) for i in issues if i.rule == "player-two-teams"],
            [("player-two-teams", validate.ERROR)] * 2)
        self.assertEqual([i.rule for i in issues if i.rule == "player-two-rosters"],
                         ["player-two-rosters"])


# A line a warning offers a club to paste: a bare or quoted TOML key, then a basic
# string. Bare keys are what a slug always is, and quoted ones are what toml_key
# writes for anything else, so the pattern takes both.
OFFERED_LINE = re.compile(r'(?:"(?:[^"\\]|\\.)*"|[A-Za-z0-9_-]+) = "(?:[^"\\]|\\.)*"')


def offered_lines(text: str) -> list[str]:
    """Every ``key = "value"`` line a printed warning offers, as a club would paste it.

    Unwrapped first, because the report folds a long warning across lines and the fold
    can fall anywhere in one, even between a key and its ``=``.
    """
    return OFFERED_LINE.findall(" ".join(text.split()))


def offered_corrections(text: str) -> list[tuple[str, str]]:
    """The same lines as TOML reads them back, key and value, in the order offered.

    Read back by :mod:`tomllib` and not by the pattern that found them, because TOML is
    what the pasted line goes through. A value quoted by hand around a quote of its own
    matches the pattern and comes back as some other value, which is the line a club
    would paste and then wonder at.
    """
    out = []
    for line in offered_lines(text):
        ((key, value),) = tomllib.loads(line).items()
        out.append((key, value))
    return out


class TestACorrectionUnderANameSplitInTwoIsGivenToNeitherAndSaidOnce(unittest.TestCase):
    """A ``[player_names]`` line keyed on a name ``[namesakes]`` has split.

    The order a club meets the two sections in. The merged row is one player called
    ``Bo Nakamura``, so that is the row they correct; then the run tells them it is two
    people, they paste the ``[namesakes]`` line it offers, and each team has a player of
    its own, looked up by ``split_namesake``'s key -- the name and the team -- and never
    by the name alone. Measured through this program on the unfixed tree
    (``/tmp/t4/scratch/w427/probe.py``): exit 0, both players ``Bo Nakamura`` in
    ``season.json`` and on the page, and not one line of the report about a line that
    did nothing.

    Given to neither, and said once. Neither and not both: the split is a statement that
    they are two people, and a name written for one of them is no evidence about the
    other. Once, because it is one line of the file and one mistake.

    Judged against the players the cards built, not against the file alone, which is
    what a first fix did. The file says which teams the entry names; only the cards say
    which of those teams printed the name, and whether a team the entry leaves out
    printed it too. Measured on that fix (``/tmp/t4/scratch/w427/fix/probe_r1.py``): with
    a team misspelt in the entry, it said the line was given to neither and offered a key
    for the misspelling, while the Lakeside player -- unsplit, because the entry did not
    reach them -- was taking the line all along; pasting what it offered put them back to
    ``Bo Nakamura`` with nothing said. With a third team printing the name, it said
    "given to neither" of a line that was naming Hilltop's player.
    """

    SAID = "[player_names] corrects bo-nakamura"
    SHARED = "Bo Nakamura"
    CORRECTED = "Boris Nakamura"
    PAIR = ("lakeside-spinners", "riverside-rockets")
    SPLIT = '[namesakes]\nbo-nakamura = ["Riverside Rockets", "Lakeside Spinners"]\n\n'
    SHARED_LINE = '[player_names]\nbo-nakamura = "Boris Nakamura"\n'
    # Each a season in which Riverside's Bo Nakamura has namesakes: the one card set per
    # way the name can be spread, written and read once for the whole class.
    CARDS = {
        "pair": {"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)},
        "three": {"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0),
                  "hilltop-aces": Swap(1, "Bo Nakamura", 3.0)},
        "four": {"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0),
                 "hilltop-aces": Swap(1, "Bo Nakamura", 3.0),
                 "parkview-wanderers": Swap(1, "Bo Nakamura", 2.0)},
        "two names": {"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0),
                      "hilltop-aces": Swap(1, "Ana Fernandez", 3.0)},
    }

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.read = {}
        for label, rename in cls.CARDS.items():
            data = cls.root / label.replace(" ", "-") / "data"
            write_season_cards(data, rename=rename)
            cls.read[label] = (data, *scorecard.read_tree(data))
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_with(self, cards: str, toml: str) -> dict:
        """One config over one card set: what the run says about it, and who is who.

        ``said`` is every config warning the run prints, the loader's and discovery's
        alike, so a warning in the wrong place and a warning twice are both visible.
        ``players`` is each registered player whose slug starts with a name under test,
        by team, as ``(slug, name, where the name came from)``.
        """
        data, cards_read, rejected = self.read[cards]
        type(self).configs += 1
        path = self.root / f"config-{self.configs}.toml"
        path.write_text(toml, encoding="utf-8")
        config = config_mod.load(path)
        found = discover_mod.discover(cards_read, rejected, config=config, root=data)
        players: dict[str, dict[str, tuple]] = {}
        for comp in found.club.competitions:
            for slug, player in comp.player_index().items():
                for stem in ("bo-nakamura", "ana-fernandez"):
                    if slug.startswith(stem):
                        players.setdefault(stem, {})[player.team] = (
                            slug, player.name, player.name_source)
        return {"said": [*config.warnings, *found.config_warnings],
                "notes": found.notes, "players": players}

    def split_keys(self, *teams: str) -> list[str]:
        return [f"bo-nakamura-{team}" for team in sorted(teams)]

    def test_a_line_under_the_shared_name_is_one_warning_offering_a_line_for_each(self):
        # Keyed on the slug or on the name as printed, which the loader slugs: the
        # same line either way, and the same one warning. The lines come in the order
        # the file is written in, so the pasted file reads as the next --discover
        # writes it.
        for label, line in (("the slug", self.SHARED_LINE),
                            ("the printed name",
                             '[player_names]\n"Bo Nakamura" = "Boris Nakamura"\n')):
            with self.subTest(keyed_on=label):
                run = self.run_with("pair", self.SPLIT + line)
                self.assertEqual(len(run["said"]), 1, run["said"])
                (warning,) = run["said"]
                self.assertIn(self.SAID, warning)
                self.assertIn("given to neither", " ".join(warning.split()))
                self.assertEqual(offered_corrections(warning),
                                 [(key, self.CORRECTED)
                                  for key in self.split_keys(*self.PAIR)])
                self.assertEqual(run["players"]["bo-nakamura"], {
                    team: (key, self.SHARED, Provenance.CARD)
                    for team, key in zip(self.PAIR, self.split_keys(*self.PAIR))})

    def test_what_it_offers_pastes_back_as_a_correction_for_each_of_them(self):
        # Pasted as printed, below the line it is about. A name with quotes of its own
        # is the value a line built by hand gets wrong, and TOML is what reads it.
        written = 'Bo "BJ" Nakamura'
        shared = f"[player_names]\nbo-nakamura = {json.dumps(written)}\n"
        first = self.run_with("pair", self.SPLIT + shared)
        self.assertEqual(len(first["said"]), 1, first["said"])
        pasted = "".join(f"{line}\n" for line in offered_lines(first["said"][0]))
        again = self.run_with("pair", self.SPLIT + shared + pasted)
        self.assertEqual(again["said"], [])
        self.assertEqual(again["players"]["bo-nakamura"], {
            team: (key, written, Provenance.DECLARED)
            for team, key in zip(self.PAIR, self.split_keys(*self.PAIR))})

    def test_a_line_for_one_of_them_ends_it_and_the_other_keeps_their_card_s_name(self):
        # The likelier case: the correction was for one of them. Once the club has
        # written it against the one it is for, there is nothing left to say -- the
        # other keeps the spelling their roster block printed, read off the card.
        # Measured on the first fix (/tmp/t4/scratch/w427/fix/probe_r5.py and
        # probe_restate2.py): it warned on every build after this, offering the name
        # to the player it was not for, and the only ways to quiet it were to take the
        # shared line away or to restate the printed name as a correction, which turns
        # that player's name from one the card printed into one the club declared.
        run = self.run_with("pair", self.SPLIT + self.SHARED_LINE
                            + 'bo-nakamura-riverside-rockets = "Boris Nakamura"\n')
        self.assertEqual(run["said"], [])
        self.assertEqual(run["players"]["bo-nakamura"], {
            "lakeside-spinners": ("bo-nakamura-lakeside-spinners", self.SHARED,
                                  Provenance.CARD),
            "riverside-rockets": ("bo-nakamura-riverside-rockets", self.CORRECTED,
                                  Provenance.DECLARED)})

    def test_a_player_of_that_name_the_split_did_not_reach_still_takes_the_line(self):
        # The two cases the file alone cannot see. The warning offers lines only for
        # the players the split made, names the ones still taking the line, and says to
        # leave it; the misspelt team is the [namesakes] warning's to report, and
        # only that warning's.
        cases = {
            "a team misspelt in the entry": (
                "pair", '[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                        '"Lakeside Spiners"]\n\n',
                "not given to the one player it made", ("riverside-rockets",),
                "lakeside-spinners", 1),
            "a third team the entry leaves out": (
                "three", self.SPLIT, "given to neither", self.PAIR, "hilltop-aces", 0),
        }
        for label, (cards, split, given, made, left, noted) in cases.items():
            with self.subTest(label):
                run = self.run_with(cards, split + self.SHARED_LINE)
                self.assertEqual(len(run["said"]), 1 + noted, run["said"])
                flat = " ".join(run["said"][0].split())
                self.assertIn(given, flat)
                self.assertEqual(offered_corrections(flat),
                                 [(key, self.CORRECTED)
                                  for key in self.split_keys(*made)])
                self.assertIn(f"It still names the bo-nakamura of {left}, a team the "
                              f"entry does not list, so leave it where it is.", flat)
                self.assertEqual(run["players"]["bo-nakamura"][left],
                                 ("bo-nakamura", self.CORRECTED, Provenance.DECLARED))
                # The misspelling, said by its own warning and not by this one too.
                self.assertNotIn("spiners", flat)
                self.assertEqual(len([s for s in run["said"] if "spiners" in s]),
                                 noted, run["said"])
                self.assertEqual([n for n in run["notes"] if "spiners" in n], [])

    def test_a_split_that_reached_nobody_is_left_to_the_warning_that_says_so(self):
        # Every team in the entry misspelt: nobody is split, the line names the one
        # player it always named, and what went wrong is the entry -- which the
        # [namesakes] warning already says. A second one would be the same mistake
        # twice.
        run = self.run_with("pair", '[namesakes]\nbo-nakamura = ["Riverside Rocket", '
                                    '"Lakeside Spiners"]\n\n' + self.SHARED_LINE)
        self.assertEqual(len(run["said"]), 1, run["said"])
        self.assertIn("gives bo-nakamura a player of their own", run["said"][0])
        self.assertEqual([n for n in run["notes"] if "[namesakes]" in n], [])
        self.assertEqual({name for _, name, _ in run["players"]["bo-nakamura"].values()},
                         {self.CORRECTED})

    def test_two_names_split_and_corrected_are_a_warning_each(self):
        run = self.run_with(
            "two names",
            '[namesakes]\nbo-nakamura = ["Riverside Rockets", "Lakeside Spinners"]\n'
            'ana-fernandez = ["Riverside Rockets", "Hilltop Aces"]\n\n'
            '[player_names]\nbo-nakamura = "Boris Nakamura"\n'
            'ana-fernandez = "Anna Fernandez"\n')
        self.assertEqual([warning.split(",")[0] for warning in run["said"]],
                         ["[player_names] corrects ana-fernandez", self.SAID])
        self.assertEqual([key for key, _ in offered_corrections(run["said"][0])],
                         ["ana-fernandez-hilltop-aces",
                          "ana-fernandez-riverside-rockets"])

    def test_a_name_split_four_ways_is_given_to_none_and_offered_four_lines(self):
        # One name merged in two grades is split with all four teams in the one entry,
        # the form config.example.toml documents, so a pair is not the only shape.
        teams = ("hilltop-aces", "lakeside-spinners", "parkview-wanderers",
                 "riverside-rockets")
        run = self.run_with("four", '[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                                    '"Lakeside Spinners", "Hilltop Aces", '
                                    '"Parkview Wanderers"]\n\n' + self.SHARED_LINE)
        self.assertEqual(len(run["said"]), 1, run["said"])
        flat = " ".join(run["said"][0].split())
        self.assertIn("given to none of them", flat)
        self.assertNotIn("neither", flat)
        self.assertEqual(offered_corrections(flat),
                         [(key, self.CORRECTED) for key in self.split_keys(*teams)])

    def test_a_split_nobody_corrected_and_a_correction_nobody_split_are_both_quiet(self):
        cases = {
            "a split nobody corrected": (self.SPLIT + '[player_names]\n'
                                         'ana-fernandez = "Anna Fernandez"\n',
                                         "ana-fernandez", "Anna Fernandez"),
            "a correction nobody split": (self.SHARED_LINE, "bo-nakamura",
                                          self.CORRECTED),
        }
        for label, (toml, stem, name) in cases.items():
            with self.subTest(label):
                run = self.run_with("pair", toml)
                self.assertEqual(run["said"], [])
                self.assertEqual({held for _, held, _ in run["players"][stem].values()},
                                 {name})


class TestALineUnderASplitNameStillNamesThePlayerTheSplitDidNotReach(unittest.TestCase):
    """Why a ``[player_names]`` line under a split name is reported and never dropped.

    A split applies by team, and only to the teams its entry names. So one name
    merged in grade A and printed once more in grade B, by a team the entry does not
    name, leaves grade B's player keyed on the name itself -- and the line under that
    name is the only line that can correct them. Measured on the unfixed tree
    (``/tmp/t4/scratch/w427/probe4.py``): grade B's ``ada-renn`` takes ``Ada Renner``
    from it, declared, while grade A's two players keep the spelling printed for them.

    That is what rules out the loader's usual answer to a line it cannot use, which
    is to ignore it and say so. Ignoring this one would take a correction away from a
    player it was doing its job for, to report that it did nothing for two others. It
    is why the warning names the player still taking the line and says to leave it, and
    why it has nothing more to say once one of grade A's two has a line of their own.
    """

    SHARED = "Ada Renn"
    SPLIT = '[namesakes]\nada-renn = ["Marina Magpies", "Quarry Quolls"]\n\n'
    WRITTEN = "Ada Renner"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.shared_only = cls.build(root / "shared", 'ada-renn = "Ada Renner"\n')
        cls.one_keyed = cls.build(
            root / "one", 'ada-renn = "Ada Renner"\n'
                          'ada-renn-marina-magpies = "Ada Renner"\n')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def build(cls, root: Path, player_names: str) -> dict:
        """Every player's name, keyed ``(grade, slug)``, and every config warning."""
        data = root / "data"
        write_two_grade_cards(data, shared=cls.SHARED,
                              shared_in=(("A", 1), ("B", 0)))
        path = root / "config.toml"
        path.write_text(f"{cls.SPLIT}[player_names]\n{player_names}", encoding="utf-8")
        config = config_mod.load(path)
        cards, rejected = scorecard.read_tree(data)
        found = discover_mod.discover(cards, rejected, config=config, root=data)
        names = {(comp.id.rsplit("-", 1)[-1].upper(), slug): player.name
                 for comp in found.club.competitions
                 for slug, player in comp.player_index().items()
                 if slug.startswith("ada-renn")}
        return {"names": names,
                "warnings": [*config.warnings, *found.config_warnings]}

    def test_the_line_still_names_grade_b_s_player_while_it_is_warned_about(self):
        self.assertEqual(self.shared_only["names"], {
            ("A", "ada-renn-marina-magpies"): self.SHARED,
            ("A", "ada-renn-quarry-quolls"): self.SHARED,
            ("B", "ada-renn"): self.WRITTEN,
        })
        self.assertEqual(len(self.shared_only["warnings"]), 1,
                         self.shared_only["warnings"])
        flat = " ".join(self.shared_only["warnings"][0].split())
        self.assertEqual([key for key, _ in offered_corrections(flat)],
                         ["ada-renn-marina-magpies", "ada-renn-quarry-quolls"])
        self.assertIn("It still names the ada-renn of station-starlings, a team the "
                      "entry does not list, so leave it where it is.", flat)

    def test_and_once_one_of_grade_a_has_a_line_nothing_is_left_to_say(self):
        self.assertEqual(self.one_keyed["warnings"], [])
        self.assertEqual(self.one_keyed["names"], {
            ("A", "ada-renn-marina-magpies"): self.WRITTEN,
            ("A", "ada-renn-quarry-quolls"): self.SHARED,
            ("B", "ada-renn"): self.WRITTEN,
        })


class TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse(unittest.TestCase):
    """A ``[player_names]`` line no player is looked up under, said once.

    A correction is applied by looking a player's slug up in the table, so a key no
    player has is a line that does nothing -- and a dashboard built from a line that
    did nothing looks exactly like one built from a line that worked. Measured through
    ``build.py`` on the unfixed tree, one config a line, on the four-round season whose
    last round spells ``bo-nakamura`` as ``Bo Nakamora`` in its set rows: a key nobody
    has (``no-such-person``), a key a letter out (``bo-nakamuru``) and a key on a
    spelling ``[aliases]`` folds away (``bo-nakamora``) each exited 0 with not one line
    of the report about them, and the first two left the report line for line as a
    build with no ``[player_names]`` writes it.

    Reported once each, with the key to use where the cards give one: the nearest key
    a player has for a typo, and the alias's target for a folded spelling. The nearest
    and not every key within two edits, which is the loader's answer for a setting:
    ``bo-nakamuru`` is one edit from ``bo-nakamura`` and two from the misspelling
    ``bo-nakamora``, and offering both would put the misspelling up as a key to use.
    Two keys equally near are both named and neither is offered as a line, because
    choosing would be guessing which person the club meant. Nor is a line offered where
    the cards cannot say who it is for: an alias's target no card names, or a key spelt
    from the very name it gives, which is how a line for a player who is not on this
    run's cards looks. Offered there, ``jo-calden = "Jo Calden"`` renamed Parkview's
    ``Jo Calder``. And a key meant for a name ``[namesakes]`` split, a letter out from
    it or folded into it, is given a line for each player the split made, as a line
    under that name is: the key nearest it was somebody else's.

    And one case that was not a typo at all. A fill-in named only in a set row was
    spelt from the slug and never looked up, so ``Tui-Mae O'Dowell``, on no roster
    anywhere, showed as ``Tui Mae O Dowell`` with a correction written for exactly
    her sitting in the file. The correction now reaches her, and the page's own
    advice -- "Correct any in [player_names]" -- is true of every name on it. The
    other answer, telling the club to alias that name to the player it was meant for,
    was rejected: a genuine fill-in has nobody to alias to, and a misspelling of a
    registered player is already said, set row by set row, by ``unknown-player``.
    """

    SAID = "[player_names] corrects"
    ALIAS = '[aliases]\n"bo-nakamora" = "bo-nakamura"\n\n'
    # Grade A's third player, rostered and never on court there, filling in for
    # grade B's first card: registered in one competition, a fill-in in the other.
    VISITOR = "Cai Ferris"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        build_season(cls.root / "four", misspell="Bo Nakamora")
        build_season(cls.root / "outside", misspell="Tui-Mae O'Dowell")
        cls.visitors_fill_in(cls.root / "visiting" / "data", "Pim Arkwright-Sole")
        write_season_cards(cls.root / "split" / "data",
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        cls.grade_b_fill_in(cls.root / "grades" / "data", cls.VISITOR)
        (cls.root / "none" / "data").mkdir(parents=True)
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("four", "outside", "visiting", "split", "grades",
                                  "none")}
        cls.configs = 0

    @staticmethod
    def visitors_fill_in(data: Path, name: str) -> None:
        """The four rounds, the Aces' second player replaced in round four's set rows
        by ``name``, on no roster anywhere: ``build_season``'s fill-in, for the away
        side.
        """
        write_season_cards(data)
        (data / "Round4").mkdir(parents=True)
        for index, (home, away, scores) in enumerate(ROUND_FOUR, start=7):
            home_rows, home_on_court = squad_for(home, TEAMS[home][1])
            away_rows, away_on_court = squad_for(away, TEAMS[away][1])
            if away == "hilltop-aces":
                away_on_court = (away_on_court[0], name, *away_on_court[2:])
            cw.write_card_file(data / "Round4" / f"r4-{index}.pdf", cw.CardSpec(
                date="2026-09-08", round_label="4", division="B",
                court=f"{index:02d}", match_id=f"200{index:04d}",
                home_name=TEAMS[home][0], away_name=TEAMS[away][0],
                rows=rows_for(scores, home_on_court, away_on_court),
                home_roster=home_rows, away_roster=away_rows))

    @staticmethod
    def grade_b_fill_in(data: Path, name: str) -> None:
        """The two grades, grade B's first home side playing ``name`` in its second
        seat in the set rows while its roster block prints its own three players.
        """
        write_two_grade_cards(data)
        grade, rnd, home_i, away_i, scores = GRADE_FIXTURES[2]
        home_name, home_players = GRADE_TEAMS[grade][home_i]
        away_name, away_players = GRADE_TEAMS[grade][away_i]
        on_court = (home_players[0], name, *home_players[2:])
        cw.write_card_file(data / f"Round{rnd}" / f"grade{grade}-r{rnd}.pdf",
                           cw.CardSpec(
            date=GRADE_DATES[rnd], round_label=rnd, division=f"3 Grade {grade}",
            court="03", match_id="4000003",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, on_court, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players))))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_with(self, cards: str, toml: str) -> dict:
        """One config over one card set: what the run says, and what everyone is called.

        ``said`` is every config warning, the loader's and discovery's alike, so a
        warning twice or in the wrong place is visible. ``names`` is every player's
        display name, as :func:`stats.player_names` gives it to the season document,
        and ``shown`` the same by competition, for a player in two of them.
        ``unknown`` is the set rows ``unknown-player`` names ``bo-nakamora`` in.
        """
        data, cards_read, rejected = self.read[cards]
        type(self).configs += 1
        path = self.root / f"config-{self.configs}.toml"
        path.write_text(toml, encoding="utf-8")
        config = config_mod.load(path)
        found = discover_mod.discover(cards_read, rejected, config=config, root=data)
        names: dict[str, str] = {}
        shown: dict[str, dict[str, str]] = {}
        unknown = 0
        for comp in found.club.competitions:
            shown[comp.id] = stats.player_names(found.matches[comp.id], comp)
            names.update(shown[comp.id])
            report = validate.validate(found.matches[comp.id], comp, club=found.club,
                                       cards=cards_read, rejected=rejected)
            unknown += sum(1 for issue in report.warnings
                           if issue.rule == "unknown-player"
                           and "bo-nakamora" in issue.message)
        return {"said": [*config.warnings, *found.config_warnings],
                "names": names, "shown": shown, "notes": found.notes,
                "unknown": unknown}

    def one(self, run: dict) -> str:
        """The one warning a run gave, unwrapped."""
        self.assertEqual(len(run["said"]), 1, run["said"])
        return " ".join(run["said"][0].split())

    def test_a_key_no_card_prints_is_one_warning_that_offers_no_line(self):
        run = self.run_with("four",
                            '[player_names]\n"no-such-person" = "Nobody Here"\n')
        flat = self.one(run)
        self.assertIn("[player_names] corrects no-such-person, but no card this run "
                      "read names a player no-such-person in a roster block or a set "
                      "row, so the correction reaches nobody.", flat)
        self.assertIn("Check the key against the name as a card prints it, unless "
                      "the player is on none of these cards.", flat)
        self.assertNotIn("Did you mean", flat)
        self.assertEqual(offered_lines(flat), [])
        self.assertEqual(run["names"], self.run_with("four", "")["names"])

    def test_a_key_within_two_edits_is_offered_the_one_real_key_nearest_it(self):
        # Keyed on the slug or on the name as printed, which the loader slugs: one
        # line either way. The misspelling in round four is a key too, two edits
        # away, and is not offered: the nearest is the one the club meant. Two
        # letters swapped are two edits, and still a typo.
        cases = {"the slug": ("bo-nakamuru", "bo-nakamuru"),
                 "as printed": ('"Bo Nakamuru"', "bo-nakamuru"),
                 "two letters swapped": ("bo-nakmaura", "bo-nakmaura")}
        for label, (written, key) in cases.items():
            with self.subTest(keyed_on=label):
                run = self.run_with("four",
                                    f'[player_names]\n{written} = "Boris Nakamura"\n')
                flat = self.one(run)
                self.assertIn(f"{self.SAID} {key},", flat)
                self.assertIn("Did you mean bo-nakamura?", flat)
                self.assertNotIn("bo-nakamora", flat)
                self.assertEqual(offered_corrections(flat),
                                 [("bo-nakamura", "Boris Nakamura")])

    def test_what_it_offers_pastes_back_in_place_and_the_name_arrives(self):
        # The end the warning is for: its line pasted in place of the one it is
        # about, and the player carrying the name with nothing left to say. A value
        # with quotes of its own, because TOML is what reads the pasted line back.
        written = 'Bo "BJ" Nakamura'
        cases = {"a letter out": ("", "bo-nakamuru"),
                 "folded by an alias": (self.ALIAS, "bo-nakamora")}
        for label, (before, key) in cases.items():
            with self.subTest(label):
                first = self.run_with(
                    "four", f"{before}[player_names]\n{key} = {json.dumps(written)}\n")
                pasted = "".join(f"{line}\n" for line in offered_lines(self.one(first)))
                again = self.run_with("four", f"{before}[player_names]\n{pasted}")
                self.assertEqual(again["said"], [])
                self.assertEqual(again["names"]["bo-nakamura"], written)

    def test_two_keys_equally_near_are_both_named_and_neither_is_offered(self):
        # bo-nakamara is one edit from bo-nakamura and one from bo-nakamora. Either
        # could be meant, and a line for one would be a guess about which person.
        flat = self.one(self.run_with(
            "four", '[player_names]\n"bo-nakamara" = "Boris Nakamura"\n'))
        self.assertIn("Did you mean bo-nakamora or bo-nakamura?", flat)
        self.assertEqual(offered_lines(flat), [])

    def test_a_key_spelt_from_the_name_it_gives_is_asked_about_and_offered_no_line(self):
        # jo-calden is one edit from Parkview's jo-calder, and its line gives the
        # name the key was spelt from: a line for somebody on none of these cards
        # looks exactly like this. Offered, the line renamed Jo Calder "Jo Calden".
        run = self.run_with("four", '[player_names]\n"jo-calden" = "Jo Calden"\n')
        flat = self.one(run)
        self.assertIn(f"{self.SAID} jo-calden,", flat)
        self.assertIn("Did you mean jo-calder? The key is spelt from the name the line "
                      "gives, so it may be for a player on none of these cards "
                      "instead, and no line is offered.", flat)
        self.assertEqual(offered_lines(flat), [])
        self.assertEqual(run["names"]["jo-calder"], "Jo Calder")
        # A value with no letters spells no key, so it is spelt from nothing and is
        # offered as any other line is. The loader takes such a value, and asking
        # slugify for its key stopped the whole build with a ValueError.
        flat = self.one(self.run_with("four", '[player_names]\n"bo-nakamuru" = "?"\n'))
        self.assertEqual(offered_corrections(flat), [("bo-nakamura", "?")])

    def test_a_key_whose_real_key_has_a_line_already_is_told_it_can_go(self):
        # The club has already said what that player is called, so a second line
        # for them would be two answers to one question.
        cases = {
            "a letter out": ('[player_names]\n"bo-nakamuru" = "Boris Nakamura"\n'
                             '"bo-nakamura" = "Bo N"\n'),
            "folded by an alias": (self.ALIAS + '[player_names]\n'
                                   '"bo-nakamora" = "Via Alias Source"\n'
                                   '"bo-nakamura" = "Bo N"\n'),
        }
        for label, toml in cases.items():
            with self.subTest(label):
                run = self.run_with("four", toml)
                flat = self.one(run)
                self.assertIn("bo-nakamura has a line of its own, which is the name "
                              "shown, so this one can go.", flat)
                self.assertEqual(offered_lines(flat), [])
                self.assertEqual(run["names"]["bo-nakamura"], "Bo N")

    def test_a_key_on_a_spelling_an_alias_folds_away_is_offered_the_alias_s_target(self):
        # A name is looked up after its alias, so the spelling on the left of an
        # alias is never anybody's key. Through a chain, the target is the end of it.
        cases = {
            "one alias": self.ALIAS,
            "a chain": ('[aliases]\n"bo-nakamora" = "bo-nakamurra"\n'
                        '"bo-nakamurra" = "bo-nakamura"\n\n'),
        }
        for label, aliases in cases.items():
            with self.subTest(label):
                run = self.run_with("four", aliases + '[player_names]\n'
                                                     '"bo-nakamora" = "Via Source"\n')
                flat = self.one(run)
                self.assertIn("[player_names] corrects bo-nakamora, but [aliases] "
                              "folds that name into bo-nakamura, and a name is looked "
                              "up after its alias, so the correction reaches nobody.",
                              flat)
                self.assertNotIn("Did you mean", flat)
                self.assertEqual(offered_corrections(flat),
                                 [("bo-nakamura", "Via Source")])
                self.assertEqual(run["names"]["bo-nakamura"], "Bo Nakamura")

    def test_an_alias_into_a_name_no_card_prints_is_offered_no_line(self):
        # The key to use is still the alias's target, but no card says who that is:
        # an alias spelt wrong, or a player not on these cards. Offered, the line
        # moved the warning on to itself. And a target with a line of its own is
        # shown on no page, so the line under the source can go, and is not told
        # that the target's is the name shown.
        aliases = '[aliases]\n"old-name" = "new-name"\n\n[player_names]\n'
        flat = self.one(self.run_with("four", aliases + '"old-name" = "Old Name"\n'))
        self.assertIn("[aliases] folds that name into new-name", flat)
        self.assertIn("No card this run read names new-name either, so there is no "
                      "line to offer: check the alias against the name as a card "
                      "prints it, unless the player is on none of these cards.", flat)
        self.assertEqual(offered_lines(flat), [])
        both = self.run_with("four", aliases + '"old-name" = "Old Name"\n'
                                               '"new-name" = "New Name"\n')
        self.assertEqual([" ".join(w.split()).split(",")[0] for w in both["said"]],
                         [f"{self.SAID} new-name", f"{self.SAID} old-name"])
        source = " ".join(both["said"][1].split())
        self.assertIn("new-name has a line of its own, so this one can go.", source)
        self.assertNotIn("the name shown", source)

    def test_a_fill_in_no_roster_prints_takes_the_correction_written_for_them(self):
        # The decision. Before: the name the set row gave her, with this line in
        # the file. The misspelling of a registered player is corrected the same way
        # and is still said by unknown-player, once a set row, as it was before. A
        # fill-in for the visitors too, since a set row names both sides, and one
        # from another grade, who takes the line in both.
        cases = {
            "a fill-in": ("outside", "tui-mae-o-dowell", "Tui-Mae O'Dowell-Hart",
                          "Tui-Mae O'Dowell", 1),
            "a fill-in for the visitors": ("visiting", "pim-arkwright-sole",
                                           "Pim Arkwright-Sole Jr", "Pim Arkwright-Sole",
                                           1),
            "a misspelling": ("four", "bo-nakamora", "Bo Nakamora Fixed",
                              "Bo Nakamora", 1),
            "a fill-in from another grade": ("grades", "cai-ferris", "Cai Ferris-Vale",
                                             self.VISITOR, 2),
        }
        for label, (cards, slug, written, spelt, grades) in cases.items():
            with self.subTest(label):
                before = self.run_with(cards, "")
                self.assertEqual(before["names"][slug], spelt)
                run = self.run_with(cards, f"[player_names]\n{slug} = "
                                           f"{json.dumps(written)}\n")
                self.assertEqual(run["said"], [])
                self.assertEqual([names[slug] for names in run["shown"].values()
                                  if slug in names], [written] * grades)
                self.assertEqual(run["unknown"], before["unknown"])
        self.assertEqual(self.run_with("four", "")["unknown"], 3)

    def test_a_line_that_reaches_somebody_is_quiet(self):
        # Somebody in a roster block who never takes a court is still a player the
        # cards built, and so is everyone in either grade of a two-grade night.
        cases = {
            "a registered player": ("four", '[player_names]\n'
                                            'bo-nakamura = "Boris Nakamura"\n'),
            "a player who never plays": ("four", '[player_names]\n'
                                                 'cass-ellery = "Cass Ellery-Voss"\n'),
            "an alias's target": ("four", self.ALIAS + '[player_names]\n'
                                                       'bo-nakamura = "Boris N"\n'),
            "one player a split made": (
                "split", '[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                         '"Lakeside Spinners"]\n\n[player_names]\n'
                         'bo-nakamura-riverside-rockets = "Boris Nakamura"\n'),
            "a player of each grade": ("grades", '[player_names]\n'
                                                 'ada-renn = "Ada Renn-Holt"\n'
                                                 'gus-emmett = "Gus Emmett-Ray"\n'),
        }
        for label, (cards, toml) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.run_with(cards, toml)["said"], [])

    def test_a_line_under_what_a_split_builds_is_left_to_what_already_says_so(self):
        # A name [namesakes] split is never a player's key, and the warning about a
        # correction under a split name already says so. A key of the name and a
        # team the split never reached -- a team that does not print the name, or
        # one misspelt -- is the namesakes warning's, which names the team; no card
        # prints a key like that, so this warning's advice would not fit it.
        split = self.run_with(
            "split", '[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                     '"Lakeside Spinners"]\n\n[player_names]\n'
                     'bo-nakamura = "Boris Nakamura"\n')
        self.assertIn("[namesakes] splits that name", self.one(split))
        missed = {
            "a team that never prints it": (
                '"Riverside Rockets", "Lakeside Spinners", "Parkview Wanderers"',
                "parkview-wanderers"),
            "a team misspelt": ('"Riverside Rockets", "Lakeside Spinerz"',
                                "lakeside-spinerz"),
        }
        for label, (teams, team) in missed.items():
            with self.subTest(label):
                run = self.run_with(
                    "split", f'[namesakes]\nbo-nakamura = [{teams}]\n\n'
                             f'[player_names]\nbo-nakamura-{team} = "Bo N"\n')
                self.assertIn(f"[namesakes] in {self.root}", run["said"][0])
                self.assertIn(f"no card puts that name in {team}.", self.one(run))
                self.assertEqual([n for n in run["notes"] if "[namesakes]" in n], [])

    def test_a_line_under_a_namesake_that_split_nobody_is_judged_as_any_line_is(self):
        # A split that separated nobody leaves its name an ordinary name, and the
        # line under it reaches nobody the way any line can. The namesakes warning
        # says the entry did nothing; left to it, the line was not said at all in
        # the run a club schedules. Two lines of the file, each wrong, so two
        # warnings, in the order the file's sections come in.
        run = self.run_with(
            "split", '[namesakes]\nbo-nakamuru = ["Riverside Rockets", '
                     '"Lakeside Spinners"]\n\n[player_names]\n'
                     'bo-nakamuru = "Boris Nakamura"\n')
        self.assertEqual(len(run["said"]), 2, run["said"])
        flat, split = (" ".join(said.split()) for said in run["said"])
        self.assertIn(f"{self.SAID} bo-nakamuru, but no card this run read", flat)
        self.assertEqual(offered_corrections(flat), [("bo-nakamura", "Boris Nakamura")])
        self.assertIn("gives bo-nakamuru a player of their own", split)
        self.assertIn("separated nobody", split)
        self.assertEqual([n for n in run["notes"] if "bo-nakamuru" in n], [])

    def test_a_key_meant_for_a_name_a_split_made_two_of_is_given_a_line_for_each(self):
        # Split, the name is printed on the cards and is nobody's key. So the key
        # nearest a typo of it was somebody else's, and an alias into it was told
        # that no card names it, which sends a club looking for a misspelling that
        # is not there. Both are answered as a line under the split name is.
        namesakes = ('[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                     '"Lakeside Spinners"]\n\n')
        each = [("bo-nakamura-lakeside-spinners", "Boris Nakamura"),
                ("bo-nakamura-riverside-rockets", "Boris Nakamura")]
        cases = {
            "a letter out": (namesakes, "bo-nakamuru", "Did you mean bo-nakamura? "),
            "folded by an alias": (namesakes + self.ALIAS, "bo-nakamora",
                                   "[aliases] folds that name into bo-nakamura,"),
        }
        for label, (head, key, why) in cases.items():
            with self.subTest(label):
                flat = self.one(self.run_with(
                    "split", head + f'[player_names]\n"{key}" = "Boris Nakamura"\n'))
                self.assertIn(why, flat)
                self.assertIn("[namesakes] splits bo-nakamura into one player per "
                              "team. Write it against each player it is for, keyed "
                              "on the name and the team:", flat)
                self.assertEqual(offered_corrections(flat), each)
                pasted = self.run_with("split", head + "[player_names]\n" + "".join(
                    f'"{slug}" = "{name}"\n' for slug, name in each))
                self.assertEqual(pasted["said"], [])
                self.assertEqual(pasted["names"]["bo-nakamura-lakeside-spinners"],
                                 "Boris Nakamura")

    def test_two_lines_that_reach_nobody_are_a_warning_each_in_key_order(self):
        run = self.run_with("four", '[player_names]\n"zed-nobody" = "Zed"\n'
                                    '"no-such-person" = "Nobody Here"\n')
        self.assertEqual([warning.split(",")[0] for warning in run["said"]],
                         [f"{self.SAID} no-such-person", f"{self.SAID} zed-nobody"])

    def test_a_folder_with_no_cards_in_it_says_nothing_about_any_line(self):
        # No card read is no evidence about a key, and the first run of a new season
        # is exactly this: last season's corrections, and nothing yet to check them by.
        run = self.run_with("none", '[player_names]\n"no-such-person" = "Nobody"\n')
        self.assertEqual(run["said"], [])


class TestATeamNameThatReachesNobodyIsSaidOnceWithTheKeyToUse(unittest.TestCase):
    """A ``[team_names]`` line no team is looked up under, said once, with the key.

    A team's name is looked up by its slug, so a key no team has names nobody, and
    the dashboard looks exactly as it would had the line never been typed. Measured
    through ``build.py`` on the unfixed tree (/tmp/t7/scratch/DW1/probe.py): the
    four-round season with ``"riverside-rocket" = "The Rockets"``, a letter out,
    exited 0 with 183 lines, 8 under ``--quiet``, not one of them about the key, and
    the team still shown as ``Riverside Rockets``; the demo's ``brindle-bunyip``
    likewise. A key under a banner ``[aliases]`` folds away, ``riverside-rokets``,
    left the team shown as ``Riverside Rokets``.

    Said the way ``[player_names]`` is said, by a function of its own, since no team
    key has the branches a player's has (a split, a fill-in, a line waiting on a
    washout): the nearest team, the tie named whole, and a line to paste only where
    one team is nearest. Judged against every team the cards built and every team a
    ``[competitions]`` section declares, since a team declared and not yet carded
    is a line waiting, not a line wrong; and not at all when no card was read. A
    club that strips a prefix has each key registered twice, as typed and as filed,
    so a line is judged by every slug the loader filed it under, and said once.
    Rejected: one function judging both tables, which makes every player branch ask
    which table it is in.
    """

    SAID = "[team_names] names"
    DECLARED = ('[competitions.tuesday-division-b]\nteams = ["Riverside Rockets", '
                '"Hilltop Aces", "Lakeside Spinners", "Parkview Wanderers"{}]\n\n')

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        write_season_cards(cls.root / "four" / "data")
        write_season_cards(cls.root / "rokets" / "data",
                           banners={"riverside-rockets": "RIVERSIDE ROKETS"})
        write_season_cards(cls.root / "north" / "data", banners={
            slug: f"NORTH END {slug.split('-')[1].upper()}" for slug in TEAMS})
        (cls.root / "none" / "data").mkdir(parents=True)
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("four", "rokets", "north", "none")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_with(self, cards: str, toml: str) -> dict:
        """What the run says about the config, and what each team is called."""
        data, cards_read, rejected = self.read[cards]
        type(self).configs += 1
        path = self.root / f"config-{self.configs}.toml"
        path.write_text(toml, encoding="utf-8")
        config = config_mod.load(path)
        found = discover_mod.discover(cards_read, rejected, config=config, root=data)
        return {"said": [" ".join(said.split())
                         for said in (*config.warnings, *found.config_warnings)],
                "teams": {slug: team.name for comp in found.club.competitions
                          for slug, team in comp.teams.items()},
                "notes": found.notes}

    def one(self, run: dict) -> str:
        self.assertEqual(len(run["said"]), 1, run["said"])
        return run["said"][0]

    def test_a_key_a_letter_out_is_one_warning_with_the_line_to_paste(self):
        run = self.run_with("four",
                            '[team_names]\n"riverside-rocket" = "The Rockets"\n')
        flat = self.one(run)
        self.assertEqual(
            flat, f"{self.SAID} riverside-rocket, but no card this run read prints a "
                  f"team riverside-rocket, and no [competitions] section declares "
                  f"one, so the name reaches nobody. Did you mean riverside-rockets? "
                  f'Keyed on riverside-rockets, the line is riverside-rockets = '
                  f'"The Rockets".')
        self.assertEqual(run["teams"]["riverside-rockets"], "Riverside Rockets")
        pasted = self.run_with("four", "[team_names]\n"
                               + "".join(f"{line}\n" for line in offered_lines(flat)))
        self.assertEqual(pasted["said"], [])
        self.assertEqual(pasted["teams"]["riverside-rockets"], "The Rockets")

    def test_a_key_nothing_is_near_is_told_to_check_it_and_offered_no_line(self):
        flat = self.one(self.run_with("four",
                                      '[team_names]\n"no-such-team" = "Nobody"\n'))
        self.assertTrue(flat.endswith(
            "so the name reaches nobody. Check the key against the banner a card "
            "prints, unless the team is on none of these cards."), flat)
        self.assertNotIn("Did you mean", flat)
        self.assertEqual(offered_lines(flat), [])

    def test_two_teams_equally_near_are_both_named_and_neither_offered(self):
        flat = self.one(self.run_with(
            "four", self.DECLARED.format(', "Hilltop Acer"')
            + '[team_names]\n"hilltop-acex" = "The Aces"\n'))
        self.assertTrue(flat.endswith(
            "Did you mean hilltop-acer or hilltop-aces? Key the line on the one it is "
            "for."), flat)
        self.assertEqual(offered_lines(flat), [])

    def test_a_key_for_a_side_no_card_prints_is_never_given_its_sibling_s_key(self):
        """A club's second side, on no card this run, is not keyed on its first.

        Measured through ``build.py`` on this row's first fix
        (/tmp/t7/scratch/DW1/sib/probe_sib.py): ``"Riverside Rockets B" = "The
        Rockets Seconds"`` was told ``Did you mean riverside-rockets? Keyed on
        riverside-rockets, the line is riverside-rockets = "The Rockets
        Seconds".``, which names the firsts as the seconds; and, beside the firsts'
        own line, that it "can go", which drops the line the seconds' first card
        needs. Two sides of a club are two teams, the rule ``nearest_team`` keeps for
        a banner no team has. Rejected: ``slugs.nearest`` alone, which asks about a
        sibling as though it were a typo.
        """
        for label, lines in {
            "a lettered side": '"Riverside Rockets B" = "The Rockets Seconds"\n',
            "a numbered side": '"Hilltop Aces 2" = "The Aces Seconds"\n',
            "beside the first side's own line": (
                '"Riverside Rockets" = "The Rockets"\n'
                '"Riverside Rockets B" = "The Rockets Seconds"\n'),
        }.items():
            with self.subTest(label):
                flat = self.one(self.run_with("four", "[team_names]\n" + lines))
                self.assertTrue(flat.endswith(
                    "so the name reaches nobody. Check the key against the banner a "
                    "card prints, unless the team is on none of these cards."), flat)
                self.assertEqual(offered_lines(flat), [])

    def test_a_key_near_a_team_with_a_line_already_is_told_it_can_go(self):
        run = self.run_with("four", '[team_names]\n'
                                    '"riverside-rockets" = "The Rockets"\n'
                                    '"riverside-rocket" = "Rockets"\n')
        self.assertTrue(self.one(run).endswith(
            "Did you mean riverside-rockets? riverside-rockets has a line of its own, "
            "so this one can go."), run["said"])
        self.assertEqual(run["teams"]["riverside-rockets"], "The Rockets")

    def test_a_line_that_reaches_a_team_is_quiet(self):
        cases = {
            "keyed as the slug": ('[team_names]\n"hilltop-aces" = "The Aces"\n',
                                  "hilltop-aces"),
            "keyed as the card prints it": (
                '[team_names]\n"Hilltop Aces" = "The Aces"\n', "hilltop-aces"),
            "a declared team no card has yet": (
                self.DECLARED.format(', "Quarry Quolls"')
                + '[team_names]\n"quarry-quolls" = "The Aces"\n', "quarry-quolls"),
            "a team of a section no card is filed under": (
                '[competitions.thursday-division-c]\nday = "Thursday"\n'
                'division = "C"\nteams = ["Marina Magpies", "Terrace Teals"]\n\n'
                '[team_names]\n"marina-magpies" = "The Aces"\n', None),
            # Not a team of the page, and its note says so; but a card prints it.
            "a banner the declared list leaves out": (
                '[competitions.tuesday-division-b]\nteams = ["Riverside Rockets", '
                '"Hilltop Aces", "Lakeside Spinners"]\n\n'
                '[team_names]\n"parkview-wanderers" = "The Aces"\n', None),
            "a prefix stripped, keyed as printed": (
                '[club]\nstrip_team_prefix = "Riverside"\n\n'
                '[team_names]\n"Riverside Rockets" = "The Aces"\n', "rockets"),
            "a prefix stripped, keyed as filed": (
                '[club]\nstrip_team_prefix = "Riverside"\n\n'
                '[team_names]\nrockets = "The Aces"\n', "rockets"),
            "an alias's target": (
                '[aliases]\n"riverside-rokets" = "riverside-rockets"\n\n'
                '[team_names]\n"riverside-rockets" = "The Aces"\n',
                "riverside-rockets"),
        }
        for label, (toml, team) in cases.items():
            with self.subTest(label):
                run = self.run_with("rokets" if "[aliases]" in toml else "four", toml)
                self.assertEqual(run["said"], [])
                if team:
                    self.assertEqual(run["teams"][team], "The Aces")

    def test_a_prefix_stripped_key_a_letter_out_is_said_once(self):
        # Registered twice, as typed and as filed, and neither reaches a team.
        prefix = '[club]\nstrip_team_prefix = "Riverside"\n\n'
        flat = self.one(self.run_with(
            "four", prefix + '[team_names]\n"Riverside Rocket" = "The Rockets"\n'))
        self.assertTrue(flat.startswith(f"{self.SAID} riverside-rocket, but"), flat)
        self.assertIn("Did you mean rockets?", flat)
        self.assertEqual(offered_lines(flat), ['rockets = "The Rockets"'])
        pasted = self.run_with("four",
                               prefix + '[team_names]\nrockets = "The Rockets"\n')
        self.assertEqual(pasted["said"], [])
        self.assertEqual(pasted["teams"]["rockets"], "The Rockets")

    def test_a_two_word_prefix_is_judged_by_the_slugs_the_loader_filed(self):
        """Under ``strip_team_prefix = "North End"``, a slug key keeps the prefix.

        The loader takes a prefix off a key only where the key spells it as the
        setting does, so ``"North End Rockets"`` is filed under ``rockets`` as well
        and ``north-end-rockets`` under nothing else. Measured through ``build.py``
        on this row's first fix (/tmp/t7/scratch/DW1/fix/prefix.py): the check
        stripped the prefix again, slugged, so ``north-end-rockets = "The
        Rockets"`` was 6 lines under ``--quiet``, none about the key, with the team
        still ``North END Rockets``; and with the raw prefix it warned about the
        working ``"North End Rockets"`` line. Judged instead by what the loader
        filed (``Config.team_name_keys``), and told the team the prefix files it as.
        """
        prefix = '[club]\nstrip_team_prefix = "North End"\n\n[team_names]\n'
        typed = self.run_with("north", prefix + '"North End Rockets" = "The Rockets"\n')
        self.assertEqual(typed["said"], [])
        self.assertEqual(typed["teams"]["rockets"], "The Rockets")
        run = self.run_with("north", prefix + 'north-end-rockets = "The Rockets"\n')
        self.assertEqual(
            self.one(run), f"{self.SAID} north-end-rockets, but strip_team_prefix "
                           f"files that team as rockets, and is taken off a key only "
                           f"where the key spells it as the setting does, so the name "
                           f"reaches nobody. Keyed on rockets, the line is rockets = "
                           f'"The Rockets".')
        self.assertNotEqual(run["teams"]["rockets"], "The Rockets")
        pasted = self.run_with("north", prefix + "".join(
            f"{line}\n" for line in offered_lines(run["said"][0])))
        self.assertEqual(pasted["said"], [])
        self.assertEqual(pasted["teams"]["rockets"], "The Rockets")

    def test_it_comes_before_the_warnings_on_later_sections_of_the_file(self):
        # In the order the file's sections come: [team_names], [player_names],
        # [namesakes]. The Spinners' banner is spelt a letter out in the entry.
        run = self.run_with("four", '[team_names]\n"riverside-rocket" = "The Rockets"\n'
                                    '\n[player_names]\n"no-such-person" = "Nobody"\n'
                                    '\n[namesakes]\nbo-nakamura = ["riverside-rockets",'
                                    ' "lakeside-spinerz"]\n')
        self.assertEqual([said.split(" ", 1)[0] for said in run["said"]],
                         ["[team_names]", "[player_names]", "[namesakes]"])

    def test_a_key_on_a_banner_an_alias_folds_is_given_the_alias_s_target(self):
        alias = '[aliases]\n"riverside-rokets" = "riverside-rockets"\n\n'
        run = self.run_with("rokets", alias + '[team_names]\n'
                                              '"riverside-rokets" = "The Rockets"\n')
        self.assertEqual(
            self.one(run), f"{self.SAID} riverside-rokets, but [aliases] folds that "
                           f"team into riverside-rockets, and a team is looked up "
                           f"after its alias, so the name reaches nobody. Keyed on "
                           f'riverside-rockets, the line is riverside-rockets = '
                           f'"The Rockets".')
        self.assertEqual(run["teams"]["riverside-rockets"], "Riverside Rokets")
        pasted = self.run_with("rokets", alias + "[team_names]\n" + "".join(
            f"{line}\n" for line in offered_lines(run["said"][0])))
        self.assertEqual(pasted["said"], [])
        self.assertEqual(pasted["teams"]["riverside-rockets"], "The Rockets")

    def test_an_alias_into_a_team_no_card_prints_offers_no_line(self):
        flat = self.one(self.run_with(
            "four", '[aliases]\n"quarry-quols" = "quarry-quolls"\n\n'
                    '[team_names]\n"quarry-quols" = "The Quolls"\n'))
        self.assertTrue(flat.endswith(
            "so the name reaches nobody. No card this run read prints quarry-quolls "
            "either, so there is no line to offer: check the alias against the "
            "banner a card prints, unless the team is on none of these cards."), flat)
        self.assertEqual(offered_lines(flat), [])

    def test_two_keys_that_reach_nobody_are_a_warning_each_in_key_order(self):
        run = self.run_with("four", '[team_names]\n"zed-nobody" = "Zed"\n'
                                    '"no-such-team" = "Nobody"\n')
        self.assertEqual([said.split(",")[0] for said in run["said"]],
                         [f"{self.SAID} no-such-team", f"{self.SAID} zed-nobody"])

    def test_a_folder_with_no_cards_in_it_says_nothing_about_any_key(self):
        run = self.run_with("none", '[team_names]\n"no-such-team" = "Nobody"\n')
        self.assertEqual(run["said"], [])


class TestACorrectionForANameOnlyAWashoutPrintsWaitsAndRenamesNobodyElse(
        unittest.TestCase):
    """A ``[player_names]`` line under a name printed only on a card that counts for
    nothing is told it waits, and is never handed somebody else's key.

    Since 4.44 such a card registers nobody, so the check that judges a key against
    the players the cards built no longer knew the name, and said that no card read
    names it, which the card's roster block contradicts. Measured through
    ``build.py`` on the unfixed tree, on the three-round season with a round-four
    washout whose Rockets roster prints ``Kit Vassey``, a letter from Parkview's
    ``Kit Vasse`` (/tmp/t4/scratch/f4/probes/p_washout_near.py and p_more.py):
    ``kit-vassey = "Kit Vassey-Brown"`` exited 0 with that warning, printed under
    ``--quiet`` as well, and the line ``kit-vasse = "Kit Vassey-Brown"``. Pasted, the
    line exited 0 with nothing said and renamed Parkview's Kit Vasse. An alias onto
    the washout's name was told that no card names it and offered no line, and a
    key one edit from both names was offered Kit Vasse's.

    So the names such a card's roster block prints are known to the check as names
    waiting for a card that counts, and a line under one is a note, in 4.44's terms,
    with no line offered: nothing is wrong with the line, and the player's first card
    that counts is the one it reaches. A note and not a config warning, because a
    warning is printed under ``--quiet``, every week until that player plays, about a
    line nobody has to change. The waiting names are candidates for the nearest key
    too, so an alias onto one is offered the line under it, and a key as near one as
    a counted name is asked about both. Rejected: offering the nearest counted key,
    which renames somebody else; and registering such a card's roster again, which
    undoes 4.44. A name one of these cards prints in a set row is left as it was,
    because the set rows of every card are in the season's names and a correction
    does reach it; a test pins that. The note is the run's and not a season's, as
    the warning it replaces was: with the washout in one of two seasons, ``--only``
    on the other printed the warning, 186 lines, and prints the note
    (/tmp/t4/scratch/f4/fixup/probes/p_modes.py).
    """

    WAITS = ("a name printed only on a card that counts for nothing so far (a "
             "washout, or a night not yet played), or on some but not all of the "
             "cards of a night that disagree. A card like that registers nobody, "
             "and a night whose cards disagree registers only the names they all "
             "print, so the correction has nobody to reach until a card that counts "
             "names that player.")
    ON_ROSTER = "Kit Vassey"        # the washout's Rockets roster, and nowhere else
    NOT_YET = "Tam Pellow"          # the unplayed night's Spinners roster, alone
    IN_A_SET_ROW = "Yan Stray"      # the washout's second Rockets seat, on no roster

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        data = cls.root / "washout" / "data"
        write_season_cards(data)
        (data / "Round4").mkdir()
        nights = (("washout", "riverside-rockets", "hilltop-aces", cls.ON_ROSTER,
                   ((6, 2), (6, 1), (0, 0), (0, 0)), {"cancelled": True}),
                  ("not-yet", "lakeside-spinners", "parkview-wanderers", cls.NOT_YET,
                   ((0, 0),) * 4, {}))
        for index, (name, home, away, extra, scores, flags) in enumerate(nights, 7):
            home_name, home_players = TEAMS[home]
            away_name, away_players = TEAMS[away]
            on_court = home_players
            if name == "washout":
                on_court = (home_players[0], cls.IN_A_SET_ROW, *home_players[2:])
            rows = rows_for(scores, on_court, away_players)
            if name == "not-yet":
                rows = tuple(dataclasses.replace(row, home_players=(),
                                                 away_players=()) for row in rows)
            cw.write_card_file(data / "Round4" / f"{name}.pdf", cw.CardSpec(
                date="2026-09-08", round_label="4", division="B",
                court=f"{index:02d}", match_id=f"200{index:04d}",
                home_name=home_name, away_name=away_name, rows=rows,
                home_roster=squad_for(home, (*home_players, extra))[0],
                away_roster=squad_for(away, away_players)[0],
                verb="VS", winner=cw.NO_TICK, **flags))
        cls.read = {"washout": (data, *scorecard.read_tree(data))}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    @staticmethod
    def notes_on(run: dict, key: str) -> list[str]:
        return [" ".join(note.split()) for note in run["notes"]
                if f"corrects {key}," in note]

    def test_a_line_under_a_name_only_such_a_card_prints_is_a_note_that_it_waits(self):
        plain = self.run_with("washout", "")["names"]
        for key, card in (("kit-vassey", "a washout"),
                          ("tam-pellow", "a night not yet played")):
            with self.subTest(card):
                run = self.run_with("washout",
                                    f'[player_names]\n"{key}" = "Kit Vassey-Brown"\n')
                self.assertEqual(run["said"], [])
                notes = self.notes_on(run, key)
                self.assertEqual(notes,
                                 [f"[player_names] corrects {key}, {self.WAITS}"])
                self.assertEqual(offered_lines(notes[0]), [])
                self.assertEqual(run["names"], plain)
                self.assertEqual(run["names"]["kit-vasse"], "Kit Vasse")

    def test_an_alias_onto_such_a_name_is_offered_the_line_under_it_which_waits(self):
        aliases = '[aliases]\n"kit-vasey" = "kit-vassey"\n\n'
        flat = self.one(self.run_with(
            "washout", aliases + '[player_names]\n"kit-vasey" = "Kit Vassey-Brown"\n'))
        self.assertIn("[player_names] corrects kit-vasey, but [aliases] folds that "
                      "name into kit-vassey, and a name is looked up after its alias, "
                      "so the correction reaches nobody.", flat)
        self.assertNotIn("No card this run read names", flat)
        self.assertEqual(offered_corrections(flat),
                         [("kit-vassey", "Kit Vassey-Brown")])
        pasted = self.run_with("washout", aliases + "[player_names]\n" + "".join(
            f"{line}\n" for line in offered_lines(flat)))
        self.assertEqual(pasted["said"], [])
        self.assertEqual(self.notes_on(pasted, "kit-vassey"),
                         [f"[player_names] corrects kit-vassey, {self.WAITS}"])

    def test_a_key_near_such_a_name_is_never_offered_a_counted_player_s_key(self):
        # kit-vassy is one edit from each; kit-vasseyy is one from the washout's
        # name and two from Kit Vasse's, which the unfixed tree offered for both.
        cases = {"as near both": ("kit-vassy", "Did you mean kit-vasse or "
                                  "kit-vassey? Key the line on the one it is for.", []),
                 "nearer the washout's": ("kit-vasseyy", "Did you mean kit-vassey?",
                                          [("kit-vassey", "Kit Vassey-Brown")])}
        for label, (key, asked, offered) in cases.items():
            with self.subTest(label):
                run = self.run_with("washout",
                                    f'[player_names]\n"{key}" = "Kit Vassey-Brown"\n')
                flat = self.one(run)
                self.assertIn(asked, flat)
                self.assertEqual(offered_corrections(flat), offered)
                self.assertEqual(run["names"]["kit-vasse"], "Kit Vasse")

    def test_a_name_such_a_card_prints_in_a_set_row_is_reached_and_goes_unsaid(self):
        run = self.run_with("washout",
                            '[player_names]\n"yan-stray" = "Yan Stray-Jones"\n')
        self.assertEqual(run["said"], [])
        self.assertEqual(self.notes_on(run, "yan-stray"), [])
        self.assertEqual(run["names"]["yan-stray"], "Yan Stray-Jones")

    def test_the_note_is_the_run_s_so_a_report_on_either_season_prints_it(self):
        # The washout is 2026-winter's. The table is the club's, and whether a line
        # waits is judged over every season's cards, so --only shows the note
        # whichever season it names, as it shows the warnings about the same table.
        from matchcentre.scope import Scope
        data = self.root / "two" / "data"
        shutil.copytree(self.read["washout"][0], data / "2026-winter")
        write_season_cards(data / "2025-winter")
        path = self.root / "two" / "config.toml"
        path.write_text('[build]\nseasons = "multi"\n\n[player_names]\n'
                        '"kit-vassey" = "Kit Vassey-Brown"\n', encoding="utf-8")
        found = discover_mod.discover(*scorecard.read_tree(data),
                                      config=config_mod.load(path), root=data)
        tags = [tag for note, tag in zip(found.notes, found.note_seasons)
                if " ".join(note.split()).endswith(f"kit-vassey, {self.WAITS}")]
        self.assertEqual(len(tags), 1, found.notes)
        self.assertEqual(len(found.club.season_keys), 2)
        for season in found.club.season_keys:
            with self.subTest(only=season):
                self.assertFalse(Scope((season,)).hides(tags[0]))


def write_round_four(data: Path, seconds: dict[str, str]) -> None:
    """Round four of the four-round season, each team ``seconds`` names playing that
    name in its second seat in the set rows, every roster block as it was.

    ``build_season``'s ``misspell`` and the 4.51 class's ``visitors_fill_in``, for any
    side of either card: a fill-in on no roster, or a name written from memory.
    """
    (data / "Round4").mkdir(parents=True, exist_ok=True)
    for index, (home, away, scores) in enumerate(ROUND_FOUR, start=7):
        home_rows, home_on_court = squad_for(home, TEAMS[home][1])
        away_rows, away_on_court = squad_for(away, TEAMS[away][1])
        if home in seconds:
            home_on_court = (home_on_court[0], seconds[home], *home_on_court[2:])
        if away in seconds:
            away_on_court = (away_on_court[0], seconds[away], *away_on_court[2:])
        cw.write_card_file(data / "Round4" / f"r4-{index}.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B",
            court=f"{index:02d}", match_id=f"200{index:04d}",
            home_name=TEAMS[home][0], away_name=TEAMS[away][0],
            rows=rows_for(scores, home_on_court, away_on_court),
            home_roster=home_rows, away_roster=away_rows))


def write_grade_card(data: Path, index: int, *, opener: str | None = None,
                     seat: tuple[str, int, str] | None = None) -> None:
    """``GRADE_FIXTURES[index]`` written again over ``write_two_grade_cards``' card.

    ``opener`` opens grade A's first team, roster block and set rows, as ``shared``
    does there; ``seat`` is ``(side, position, name)``, one set-row seat of the
    ``"home"`` or ``"away"`` side printing ``name``, its roster block as it was.
    """
    grade, rnd, home_i, away_i, scores = GRADE_FIXTURES[index]
    sides = []
    for side, team_i in (("home", home_i), ("away", away_i)):
        name, players = GRADE_TEAMS[grade][team_i]
        if opener is not None and (grade, team_i) == ("A", 0):
            players = (opener, *players[1:])
        on_court = list(players)
        if seat is not None and seat[0] == side:
            on_court[seat[1]] = seat[2]
        sides.append((name, players, tuple(on_court)))
    (home_name, home_players, home_on), (away_name, away_players, away_on) = sides
    cw.write_card_file(data / f"Round{rnd}" / f"grade{grade}-r{rnd}.pdf", cw.CardSpec(
        date=GRADE_DATES[rnd], round_label=rnd, division=f"3 Grade {grade}",
        court=f"{index + 1:02d}", match_id=f"400{index + 1:04d}",
        home_name=home_name, away_name=away_name,
        rows=rows_for(scores, home_on, away_on),
        home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(home_players)),
        away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                       captain=(i == 0))
                          for i, n in enumerate(away_players))))


class TestACorrectionThatGivesOnePlayerAnothersNameIsSaidWithTheAliasThatJoinsThem(
        unittest.TestCase):
    """A ``[player_names]`` line that gives one record the name another already has.

    The remedy a club reaches for first. Round four spells the Rockets' Bo Nakamura
    ``Bo Nakamora`` in its set rows, the dashboard lists ``Bo Nakamora`` beside him,
    and the club corrects the one it can see is wrong. Measured through ``build.py``
    on the unfixed tree (/tmp/t6/scratch/m7/probe.py, p84-empty and p84-line):
    ``"bo-nakamora" = "Bo Nakamura"`` exited 0 with two records called Bo Nakamura
    in ``season.json``, a report of 180 lines that differs from the 181 of a
    rebuild with no line only in how many files it wrote, and under ``--quiet`` 8
    lines, the same 8. The one person was two records under one name, and nothing
    said so.

    Said once, as a config warning, so ``--quiet`` prints it: what the line did is a
    fact about the club's own file, and the page it made looks right. It offers the
    ``[aliases]`` line that joins them when the cards say which record is the real
    one, the one of them a roster block registers anywhere in the club, and tells
    the club to delete the line under the other, which the alias would leave
    reaching nobody. Where the cards cannot say -- two records a roster block
    registers, or none -- no line is offered, because choosing would be guessing
    which spelling the club keeps; nor where one of them is a player ``[namesakes]``
    made, since an alias is applied before the split and one keyed on that player
    reaches nobody (the review's probe: the line offered for the Aces' split Pim,
    pasted, left two records and said nothing). ``unknown-player`` still names each
    set row the misspelling is in, as a fill-in or a spelling that needs an alias,
    and offers no line: it is about the card, and this is about the line.

    Judged a page at a time, each competition's and a multi-season build's careers
    page, and said once for what the lines did on all of them. Measured with the
    review's probes on the tree before this pass: one pair on the pages of two
    grades was said twice, offered the alias once and told once that the cards give
    none; two seasons' fill-ins spelt two ways and given one name made two careers
    under it, and nothing was said (/tmp/t6/scratch/m7/fix/rprobe.py, careers:
    360 lines, 26 under ``--quiet``). Rejected: silencing ``unknown-player`` for a
    slug a line has given a registered name, which would leave the misspelling
    reported only while the line that hides it stays; a warning for every record
    that shares a name, which would tell a club its ``[namesakes]`` entry did what
    it was written to do; and the club judged whole, all its names at once, which
    says a line is wrong of two records no page shows together.
    """

    ALIAS = '[aliases]\n"bo-nakamora" = "bo-nakamura"\n\n'
    SAID = ("so the dashboard shows them as separate players under that one name. "
            "If they are one person, ")
    NO_LINE = ("join them under [aliases], keyed on the spelling that is wrong: the "
               "cards do not say which that is, so no line is offered.")
    ROS = "Ros D'Arcy"
    ROS_LINE = '[player_names]\n"ros-darcy" = "Ros D\'Arcy"\n'
    ROS_SAID = ("[player_names] gives ros-darcy the name Ros D'Arcy, which "
                "ros-d-arcy already has, so the dashboard shows them as separate "
                "players under that one name. If they are one person, join them "
                "under [aliases] with ros-darcy = \"ros-d-arcy\", and delete the "
                "[player_names] line for ros-darcy. If they are different people, "
                "give each a name of their own.")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        four = cls.root / "four" / "data"
        write_season_cards(four)
        write_round_four(four, {"riverside-rockets": "Bo Nakamora"})
        split = cls.root / "split" / "data"
        write_season_cards(split,
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        for season in ("2025-winter", "2026-winter"):
            shutil.copytree(four, cls.root / "two" / "data" / season)
        pim = cls.root / "pim" / "data"
        write_season_cards(pim)
        write_round_four(pim, {"riverside-rockets": "Pim Arkwright-Sole",
                               "hilltop-aces": "Pim Arkwright-Sole"})
        # Grade A registers Ros D'Arcy and lends her to grade B, and a set row of
        # each grade spells her Ros DArcy: one pair of records on two pages.
        grades = cls.root / "grades" / "data"
        write_two_grade_cards(grades, shared=cls.ROS, shared_in=(("A", 0),))
        write_grade_card(grades, 1, opener=cls.ROS, seat=("away", 0, "Ros DArcy"))
        write_grade_card(grades, 2, seat=("home", 1, cls.ROS))
        write_grade_card(grades, 3, seat=("away", 1, "Ros DArcy"))
        # Grade B alone spells her Ros DArcy, filling in: two records on no one
        # competition's page, and on the one careers page.
        lent = cls.root / "lent" / "data" / "2026-winter"
        write_two_grade_cards(lent, shared=cls.ROS, shared_in=(("A", 0),))
        write_grade_card(lent, 2, seat=("home", 1, "Ros DArcy"))
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("four", "split", "two", "pim", "grades", "lent")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    def test_the_misspelling_given_the_registered_name_is_offered_the_alias(self):
        # Spaced another way, the name is the one the page shows twice.
        for label, name in (("as printed", "Bo Nakamura"),
                            ("spaced", "Bo  Nakamura ")):
            with self.subTest(label):
                run = self.run_with("four",
                                    f'[player_names]\n"bo-nakamora" = "{name}"\n')
                self.assertEqual(
                    self.one(run),
                    '[player_names] gives bo-nakamora the name Bo Nakamura, which '
                    'bo-nakamura already has, ' + self.SAID + 'join them under '
                    '[aliases] with bo-nakamora = "bo-nakamura", and delete the '
                    '[player_names] line for bo-nakamora. If they are different '
                    'people, give each a name of their own.')
                self.assertEqual(offered_corrections(self.one(run)),
                                 [("bo-nakamora", "bo-nakamura")])
                # The card's misspelling is still said where it is printed, set row
                # by set row, as a fill-in or a spelling wanting an alias, and with
                # no line: that warning is about the card.
                self.assertEqual(run["unknown"], 3)
        self.assertEqual(self.run_with("four", "")["said"], [])

    def test_the_alias_pasted_and_the_line_deleted_is_one_record_and_nothing_said(self):
        pasted = self.run_with("four", self.ALIAS)
        self.assertEqual(pasted["said"], [])
        self.assertNotIn("bo-nakamora", pasted["names"])
        self.assertEqual(pasted["names"]["bo-nakamura"], "Bo Nakamura")
        self.assertEqual(pasted["unknown"], 0)
        # Kept, the line reaches nobody, and 4.51's warning is the only one said.
        kept = self.run_with("four", self.ALIAS + '[player_names]\n'
                                                  '"bo-nakamora" = "Bo Nakamura"\n')
        self.assertIn("[aliases] folds that name into bo-nakamura", self.one(kept))

    def test_the_registered_player_given_the_misspelling_is_offered_one_alias(self):
        # The other way round: nothing to delete, and the name the club wrote is
        # the one the joined record keeps.
        line = '[player_names]\n"bo-nakamura" = "Bo Nakamora"\n'
        flat = self.one(self.run_with("four", line))
        self.assertTrue(flat.startswith(
            "[player_names] gives bo-nakamura the name Bo Nakamora, which "
            "bo-nakamora already has, " + self.SAID), flat)
        self.assertNotIn("delete", flat)
        self.assertEqual(offered_corrections(flat), [("bo-nakamora", "bo-nakamura")])
        pasted = self.run_with("four", self.ALIAS + line)
        self.assertEqual(pasted["said"], [])
        self.assertEqual(pasted["names"]["bo-nakamura"], "Bo Nakamora")

    def test_both_given_one_name_are_offered_the_alias_and_lose_one_line(self):
        lines = ('[player_names]\n"bo-nakamora" = "Boris Nakamura"\n'
                 '"bo-nakamura" = "Boris Nakamura"\n')
        flat = self.one(self.run_with("four", lines))
        self.assertTrue(flat.startswith(
            "[player_names] gives bo-nakamora and bo-nakamura one name, Boris "
            "Nakamura, " + self.SAID), flat)
        self.assertIn("and delete the [player_names] line for bo-nakamora.", flat)
        self.assertEqual(offered_corrections(flat), [("bo-nakamora", "bo-nakamura")])
        pasted = self.run_with("four", self.ALIAS + '[player_names]\n'
                                                    'bo-nakamura = "Boris Nakamura"\n')
        self.assertEqual(pasted["said"], [])
        self.assertEqual(pasted["names"]["bo-nakamura"], "Boris Nakamura")

    def test_two_players_a_roster_registers_given_one_name_are_offered_no_line(self):
        # Jo Calder and Kit Vasse are both Parkview's: either could be the spelling
        # to keep, or they are two people and the line is the mistake.
        for label, lines, given in (
                ("one line", '"jo-calder" = "Kit Vasse"\n',
                 "gives jo-calder the name Kit Vasse, which kit-vasse already has, "),
                ("a line each", 'jo-calder = "Jo Vasse"\nkit-vasse = "Jo Vasse"\n',
                 "gives jo-calder and kit-vasse one name, Jo Vasse, ")):
            with self.subTest(label):
                flat = self.one(self.run_with("four", "[player_names]\n" + lines))
                self.assertEqual(flat, f"[player_names] {given}{self.SAID}"
                                       f"{self.NO_LINE} If they are different people, "
                                       f"give each a name of their own.")
                self.assertEqual(offered_lines(flat), [])

    def test_a_name_of_its_own_a_name_restated_and_a_split_s_one_name_are_quiet(self):
        cases = {
            "a name of its own": ("four", '[player_names]\n'
                                          '"bo-nakamora" = "Bo Nakamora-Hill"\n'),
            "a name restated": ("four", '[player_names]\n'
                                        '"bo-nakamura" = "Bo Nakamura"\n'),
            "one player a split made": (
                "split", '[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                         '"Lakeside Spinners"]\n\n[player_names]\n'
                         'bo-nakamura-lakeside-spinners = "Bo Nakamura"\n'),
            # The Spinners' Bo keeps the name alone, and is the Rockets' namesake.
            "a split that leaves a team out": (
                "split", '[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                         '"Hilltop Aces"]\n\n[player_names]\n'
                         'bo-nakamura-riverside-rockets = "Bo Nakamura"\n'),
        }
        for label, (cards, toml) in cases.items():
            with self.subTest(label):
                said = self.run_with(cards, toml)["said"]
                if label == "a split that leaves a team out":
                    # No Aces card prints the name: the entry's own warning, and
                    # nothing about the line.
                    self.assertEqual(len(said), 1, said)
                    self.assertIn("no card puts that name in hilltop-aces. The rest "
                                  "were still separated;", " ".join(said.pop().split()))
                self.assertEqual(said, [])

    def test_a_player_a_split_made_is_told_no_alias_can_reach_them(self):
        # The Spinners' Bo, registered, and the Aces' Pim, a fill-in: either way
        # the key is one the split made, and an alias is applied before the split.
        for cards, bare, teams, key, name, other in (
                ("split", "bo-nakamura", '"Riverside Rockets", "Lakeside Spinners"',
                 "bo-nakamura-lakeside-spinners", "Cass Ellery", "cass-ellery"),
                ("pim", "pim-arkwright-sole", '"riverside-rockets", "hilltop-aces"',
                 "pim-arkwright-sole-hilltop-aces", "Dev Raman", "dev-raman")):
            with self.subTest(key):
                split = f"[namesakes]\n{bare} = [{teams}]\n\n"
                flat = self.one(self.run_with(
                    cards, split + f'[player_names]\n{key} = "{name}"\n'))
                self.assertEqual(
                    flat,
                    f"[player_names] gives {key} the name {name}, which {other} "
                    f"already has, {self.SAID}join them under [aliases], keyed on "
                    f"the wrong spelling as a card prints it. No line is offered: "
                    f"[namesakes] splits {bare} after [aliases] is applied, so an "
                    f"alias keyed on a player the split made reaches nobody, and one "
                    f"keyed on {bare} moves every player of that name. If they are "
                    f"different people, give each a name of their own.")
                self.assertEqual(offered_lines(flat), [])
                # The line the family was offered before, pasted: still two.
                pasted = self.run_with(cards,
                                       split + f'[aliases]\n{key} = "{other}"\n')
                self.assertIn(key, pasted["names"])
                self.assertIn(other, pasted["names"])

    def test_one_pair_on_the_pages_of_two_grades_is_said_once_with_the_alias(self):
        # Grade B shows the pair with neither on its rosters, grade A with Ros
        # D'Arcy on one: one sentence, and grade A's roster says which is right.
        run = self.run_with("grades", self.ROS_LINE)
        self.assertEqual(len(run["shown"]), 2)
        self.assertEqual(self.one(run), self.ROS_SAID)
        pasted = self.run_with("grades", '[aliases]\n"ros-darcy" = "ros-d-arcy"\n')
        self.assertEqual(pasted["said"], [])
        self.assertNotIn("ros-darcy", pasted["names"])

    def test_a_pair_no_one_page_shows_is_said_once_the_careers_page_shows_it(self):
        # Grade B alone prints Ros DArcy: each grade's page shows one of them, and
        # a multi-season build's careers page puts both on one.
        self.assertEqual(self.run_with("lent", self.ROS_LINE)["said"], [])
        multi = '[build]\nseasons = "multi"\n\n'
        run = self.run_with("lent", multi + self.ROS_LINE)
        self.assertEqual(len(run["shown"]), 2)
        self.assertEqual(self.one(run), self.ROS_SAID)
        self.assertEqual(self.run_with("lent", multi)["said"], [])

    def test_two_seasons_holding_the_same_pair_are_told_once(self):
        path = self.root / "two" / "config.toml"
        path.write_text('[build]\nseasons = "multi"\n\n[player_names]\n'
                        '"bo-nakamora" = "Bo Nakamura"\n', encoding="utf-8")
        data = self.read["two"][0]
        found = discover_mod.discover(*self.read["two"][1:],
                                      config=config_mod.load(path), root=data)
        self.assertEqual(len(found.club.competitions), 2)
        self.assertEqual([" ".join(w.split())[:44] for w in found.config_warnings],
                         ["[player_names] gives bo-nakamora the name Bo"])


class TestAFillInIsShownAsTheirSetRowPrintsThem(unittest.TestCase):
    """A fill-in's name is the set row's spelling of it, and not their slug's.

    ``fill_in_names`` gave everyone no roster block registers the name of their slug,
    since the slug was all it was handed, so every apostrophe and hyphen a set row
    printed was gone. Measured through ``build.py`` on the unfixed tree
    (/tmp/t6/scratch/m7/probe.py): ``Tui-Mae O'Dowell`` filling in for the Rockets
    showed as ``Tui Mae O Dowell``, ``Pim Arkwright-Sole`` for the Aces as ``Pim
    Arkwright Sole``, grade A's ``Ros D'Arcy`` lent to grade B as ``Ros D Arcy`` there
    and ``Ros D'Arcy`` at home, and a fill-in ``[namesakes]`` split as ``Pim
    Arkwright Sole Hilltop Aces``, the team spelt into the name from the key.

    Named by the rule a roster name is named by, one function for both: the
    spelling of the first card to print it, in the order the cards are read, unless
    ``[aliases]`` folds that spelling and a later card prints one it does not; then
    through the same display rule. The alias half is new to both. The review's
    probe (alias-fillin) showed a fill-in misspelt first, and aliased, under the
    misspelling, ``Tui-Mae ODowel``, and a roster block misspelt on the first card
    read showed ``Bo Nakamora`` on the base tree for all its alias
    (/tmp/t6/scratch/m7/fix/gen.py, roster-alias). Set rows are read off every card
    whose set rows are shown, a washout's included; a roster name off the cards
    that count, the only ones that register anybody. A shouted set row is shown as
    a shouted roster block is, ``BO NAKAMA`` as ``Bo Nakama``: that is
    ``person_name``'s rule for both, and un-slugging the one would name a player
    lent between grades two ways. A split fill-in's spelling is keyed on the player
    the split made, so the team is in the key and never in the name. Rejected: the
    spelling most set rows print, which a single card more changes, and the earliest
    night's, which ``_squads`` does not use for a roster name either, so one person's
    name would follow two rules depending on which block printed it.
    """

    TUI = "Tui-Mae O'Dowell"
    PIM = "Pim Arkwright-Sole"
    ROS = "Ros D'Arcy"
    SPLIT = ('[namesakes]\npim-arkwright-sole = ["Riverside Rockets", '
             '"Hilltop Aces"]\n')

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        seasons = {"outside": {"riverside-rockets": cls.TUI},
                   "visiting": {"hilltop-aces": cls.PIM},
                   "split": {"riverside-rockets": cls.PIM, "hilltop-aces": cls.PIM},
                   "first": {"riverside-rockets": "Tui Mae O'Dowell"},
                   "later": {"riverside-rockets": cls.TUI},
                   "aliased": {"riverside-rockets": cls.TUI},
                   "roster": {}}
        for label, seconds in seasons.items():
            write_season_cards(cls.root / label / "data")
            write_round_four(cls.root / label / "data", seconds)
        # Round three's Rockets card, read before round four's, spells her the other
        # way: hyphenated where round four is not, and the other way about; and in
        # "aliased" as a misspelling a club would alias.
        cls.rockets_round_three(cls.root / "first" / "data", cls.TUI)
        cls.rockets_round_three(cls.root / "later" / "data", "Tui Mae O'Dowell")
        cls.rockets_round_three(cls.root / "aliased" / "data", "Tui-Mae ODowel")
        # Round one, the first card read, misspells Bo Nakamura in the Rockets'
        # roster block, and every later card spells him right.
        cls.rockets_at_home(cls.root / "roster" / "data", 1, "Bo Nakamora",
                            rostered=True)
        cls.lent_between_grades(cls.root / "grades" / "data", cls.ROS)
        cls.lent_between_grades(cls.root / "shouted" / "data", "ROS D'ARCY")
        # Her one night a washout: the card counts for nothing, and is shown.
        washout = cls.root / "washout" / "data"
        write_season_cards(washout)
        (washout / "Round4").mkdir()
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        cw.write_card_file(washout / "Round4" / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B", court="07",
            match_id="2000007", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)),
                          (home_players[0], cls.TUI, *home_players[2:]),
                          away_players),
            home_roster=squad_for("riverside-rockets", home_players)[0],
            away_roster=squad_for("hilltop-aces", away_players)[0],
            cancelled=True, verb="VS", winner=cw.NO_TICK))
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in (*seasons, "grades", "shouted", "washout")}
        cls.configs = 0

    @staticmethod
    def rockets_round_three(data: Path, name: str) -> None:
        """Round three's Rockets v Wanderers card again, ``name`` in the Rockets'
        second seat in its set rows."""
        TestAFillInIsShownAsTheirSetRowPrintsThem.rockets_at_home(data, 5, name)

    @staticmethod
    def rockets_at_home(data: Path, index: int, name: str, *,
                        rostered: bool = False) -> None:
        """``FIXTURES[index - 1]``, a Rockets home card, again: ``name`` in the
        Rockets' second seat in its set rows, and in their roster block if
        ``rostered``."""
        rnd, home, away, scores = FIXTURES[index - 1]
        home_rows, home_on_court = squad_for(
            home, TEAMS[home][1], {home: Swap(1, name)} if rostered else None)
        away_rows, away_on_court = squad_for(away, TEAMS[away][1])
        cw.write_card_file(
            data / f"Round{rnd}" / (f"RiversideRockets-"
                                    f"{TEAMS[away][0].title().replace(' ', '')}.pdf"),
            cw.CardSpec(date=DATES[rnd], round_label=rnd, division="B",
                        court=f"{index:02d}", match_id=f"200{index:04d}",
                        home_name=TEAMS[home][0], away_name=TEAMS[away][0],
                        rows=rows_for(scores, (home_on_court[0], name,
                                               *home_on_court[2:]), away_on_court),
                        home_roster=home_rows, away_roster=away_rows))

    @staticmethod
    def lent_between_grades(data: Path, name: str) -> None:
        """The two grades, ``name`` opening grade A's first team and filling in for
        grade B's first home side in its second seat, on no roster of grade B."""
        write_two_grade_cards(data, shared=name, shared_in=(("A", 0),))
        write_grade_card(data, 2, seat=("home", 1, name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with

    def test_a_fill_in_for_either_side_is_shown_as_the_set_row_prints_them(self):
        for cards, slug, printed in (("outside", "tui-mae-o-dowell", self.TUI),
                                     ("visiting", "pim-arkwright-sole", self.PIM)):
            with self.subTest(cards):
                self.assertEqual(self.run_with(cards, "")["names"][slug], printed)

    def test_a_player_lent_by_another_grade_is_shown_by_one_spelling_in_both(self):
        # Shouted, the set row is shown as grade A's roster block shouting the same
        # name is, by the one personal-name rule, and not un-slugged.
        for cards, printed in (("grades", self.ROS), ("shouted", "ROS D'ARCY")):
            with self.subTest(cards):
                shown = self.run_with(cards, "")["shown"]
                self.assertEqual({comp: names["ros-d-arcy"]
                                  for comp, names in shown.items()
                                  if "ros-d-arcy" in names},
                                 {comp: slugs.person_name(printed) for comp in shown})
                self.assertEqual(len(shown), 2)

    def test_a_fill_in_a_split_made_is_shown_by_the_name_and_not_the_team(self):
        names = self.run_with("split", self.SPLIT)["names"]
        self.assertEqual({slug: name for slug, name in names.items()
                          if slug.startswith("pim-")},
                         {"pim-arkwright-sole-hilltop-aces": self.PIM,
                          "pim-arkwright-sole-riverside-rockets": self.PIM})

    def test_a_fill_in_spelt_two_ways_is_named_as_the_first_card_read_spells_them(self):
        for cards, shown in (("first", self.TUI), ("later", "Tui Mae O'Dowell")):
            with self.subTest(cards):
                run = self.run_with(cards, "")
                self.assertEqual(run["names"]["tui-mae-o-dowell"], shown)

    def test_a_spelling_an_alias_folds_gives_way_to_one_it_does_not(self):
        # A fill-in and a roster name alike, misspelt on the first card read: the
        # alias says which spelling is wrong. Folded everywhere, the first is kept.
        for label, cards, aliases, slug, shown in (
                ("fill-in", "aliased", '"tui-mae-odowel" = "tui-mae-o-dowell"\n',
                 "tui-mae-o-dowell", self.TUI),
                ("roster name", "roster", '"bo-nakamora" = "bo-nakamura"\n',
                 "bo-nakamura", "Bo Nakamura"),
                ("every spelling folded", "aliased",
                 '"tui-mae-odowel" = "tui-mo"\n"tui-mae-o-dowell" = "tui-mo"\n',
                 "tui-mo", "Tui-Mae ODowel")):
            with self.subTest(label):
                run = self.run_with(cards, f"[aliases]\n{aliases}")
                self.assertEqual(run["names"][slug], shown)

    def test_a_fill_in_only_a_washout_names_is_shown_as_its_set_row_prints_them(self):
        self.assertEqual(self.run_with("washout", "")["names"]["tui-mae-o-dowell"],
                         self.TUI)


class TestACorrectionThatRestatesTheNameShownIsToldToDeleteTheLine(unittest.TestCase):
    """A ``[player_names]`` line under the wrong key, giving the name the right key
    is already shown by.

    4.51's warning tells a line under a spelling ``[aliases]`` folds away, or under a
    key a letter out, the key to use, and offered the line keyed there whatever it
    gave. Measured through ``build.py`` on the tree before this pass
    (/tmp/t7/scratch/DW2/probe.py, over the four-round season whose round four's set
    rows print the Rockets' Bo Nakamura ``Bo Nakamora``): ``"bo-nakamora" = "Bo
    Nakamura"`` beside the alias folding that spelling (193 lines, 11 under
    ``--quiet``), and ``"bo-nakamuru" = "Bo Nakamura"`` with no alias (210 lines,
    11), were each offered ``bo-nakamura = "Bo Nakamura"``: the name every roster
    block already prints him by, as a line that gives no page a name it does not
    show already. Pasted, it made the season's player names ``mixed`` in
    ``season.json``, where they had been ``card``, and the page flags that ``part
    derived``.

    Told to delete the line where the name it gives is the one the key it belongs
    under is shown by without it, on every page that shows that player, compared as
    a page's names are, whitespace run together. Case is not run together: a line
    is shown as written, so ``BO NAKAMURA`` is a name no page shows, and is offered
    its line. Nor is a name one page shows and another does not, which the line
    would change there. Nor a registered player's name this program worked out,
    un-shouted from cards that print it in capitals: keyed right, the line makes
    the guess the club's word, which is what ``build.py``'s review asks of it.
    Measured through ``build.py`` on this pass's tree before that was added
    (/tmp/t7/scratch/DW2/shouted.txt): every card printing ``BO NAKAMURA``, with
    ``"bo-nakamuru" = "Bo Nakamura"``, was told to delete the line in a report
    whose review, ``1 player name(s) with no printed spelling``, offered
    ``bo-nakamura = "Bo Nakamura"``; keyed so, the review item went (194 lines to
    181). A fill-in's is judged as shown, since the review lists registered
    players alone: a Rockets fill-in whose set row prints ``TUI-MAE ODOWELL``,
    shown ``Tui-Mae Odowell``, with ``tui-mae-odowell = "Tui-Mae Odowell"`` gave a
    report and a site byte for byte those of no config (206 lines, 7 under
    ``--quiet``; /tmp/t7/scratch/DW2/review-base.txt, sf-right and sf-none).
    Rejected: comparing byte for byte, which offers a double-spaced value back as
    a line of its own; and leaving every worked-out name out, which offers a
    fill-in a line that changes nothing.
    """

    ALIAS = '[aliases]\n"bo-nakamora" = "bo-nakamura"\n\n'
    GO = ("The dashboard already shows bo-nakamura as Bo Nakamura, the name the "
          "line gives, so delete the line.")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        four = cls.root / "four" / "data"
        write_season_cards(four)
        write_round_four(four, {"riverside-rockets": "Bo Nakamora"})
        # Grade A registers Ros D'Arcy, and grade B's set row, filling in, prints
        # her Ros D Arcy: one player, shown two ways on two pages.
        grades = cls.root / "grades" / "data"
        write_two_grade_cards(grades, shared="Ros D'Arcy", shared_in=(("A", 0),))
        write_grade_card(grades, 2, seat=("home", 1, "Ros D Arcy"))
        write_season_cards(cls.root / "shouted" / "data",
                           rename={"riverside-rockets": Swap(1, "BO NAKAMURA")})
        # A Rockets fill-in whose one set row shouts her name.
        shoutfill = cls.root / "shoutfill" / "data"
        write_season_cards(shoutfill)
        write_round_four(shoutfill, {"riverside-rockets": "TUI-MAE ODOWELL"})
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("four", "grades", "shouted", "shoutfill")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    def test_a_line_under_a_folded_spelling_restating_the_name_is_told_to_go(self):
        flat = self.one(self.run_with(
            "four", self.ALIAS + '[player_names]\n"bo-nakamora" = "Bo Nakamura"\n'))
        self.assertEqual(
            flat, "[player_names] corrects bo-nakamora, but [aliases] folds that "
                  "name into bo-nakamura, and a name is looked up after its alias, "
                  "so the correction reaches nobody. " + self.GO)
        self.assertEqual(offered_lines(flat), [])

    def test_a_key_a_letter_out_restating_the_name_is_told_to_go_however_spaced(self):
        for value in ("Bo Nakamura", "Bo  Nakamura "):
            with self.subTest(value=value):
                flat = self.one(self.run_with(
                    "four", f'[player_names]\n"bo-nakamuru" = "{value}"\n'))
                self.assertTrue(flat.endswith(
                    "so the correction reaches nobody. Did you mean bo-nakamura? "
                    + self.GO), flat)
                self.assertEqual(offered_lines(flat), [])

    def test_a_line_told_to_go_would_change_no_page_keyed_where_it_belongs(self):
        # Wherever it says delete, the line keyed right gives every page the name
        # it shows already, as a page's names are compared, and deleted, it leaves
        # nothing said.
        cases = [("four", before, key, "bo-nakamura", value)
                 for value in ("Bo Nakamura", "Bo  Nakamura ", "BO NAKAMURA")
                 for before, key in ((self.ALIAS, "bo-nakamora"), ("", "bo-nakamuru"))]
        cases += [("grades", "", "ros-d-arci", "ros-d-arcy", value)
                  for value in ("Ros D'Arcy", "Ros D Arcy")]

        def pages(run: dict) -> dict:
            return {comp: {slug: " ".join(name.split()) for slug, name in names.items()}
                    for comp, names in run["shown"].items()}

        told = 0
        for cards, before, key, to, value in cases:
            with self.subTest(key=key, value=value):
                line = f"{before}[player_names]\n{{}} = {json.dumps(value)}\n"
                if "so delete the line" not in self.one(
                        self.run_with(cards, line.format(f'"{key}"'))):
                    continue
                told += 1
                deleted = self.run_with(cards, before)
                self.assertEqual(deleted["said"], [])
                self.assertEqual(pages(self.run_with(cards, line.format(to))),
                                 pages(deleted))
        self.assertEqual(told, 4)

    def test_a_name_the_page_does_not_show_is_still_offered_its_line(self):
        # Shouted, the line is shown as written; a name of its own is a new name.
        for value in ("BO NAKAMURA", "Bo Nakamura-Hill"):
            for before, key in ((self.ALIAS, "bo-nakamora"), ("", "bo-nakamuru")):
                with self.subTest(value=value, key=key):
                    flat = self.one(self.run_with(
                        "four", f'{before}[player_names]\n"{key}" = "{value}"\n'))
                    self.assertEqual(offered_corrections(flat),
                                     [("bo-nakamura", value)])
                    self.assertNotIn("delete", flat)
        # And the line it offers, pasted, is the name the page shows.
        run = self.run_with("four", '[player_names]\nbo-nakamura = "BO NAKAMURA"\n')
        self.assertEqual(run["said"], [])
        self.assertEqual(run["names"]["bo-nakamura"], "BO NAKAMURA")

    def test_a_name_worked_out_for_a_registered_player_is_still_offered_its_line(
            self):
        # Every card shouts him, so the name shown is un-shouted, a guess the line
        # keyed right settles; and pasted, it is the name shown, and nothing said.
        for before, key in ((self.ALIAS, "bo-nakamora"), ("", "bo-nakamuru")):
            with self.subTest(key=key):
                flat = self.one(self.run_with(
                    "shouted", f'{before}[player_names]\n"{key}" = "Bo Nakamura"\n'))
                self.assertEqual(offered_corrections(flat),
                                 [("bo-nakamura", "Bo Nakamura")])
                self.assertNotIn("delete", flat)
        run = self.run_with("shouted", '[player_names]\nbo-nakamura = "Bo Nakamura"\n')
        self.assertEqual(run["said"], [])
        self.assertEqual(run["names"]["bo-nakamura"],
                         self.run_with("shouted", "")["names"]["bo-nakamura"])

    def test_a_fill_in_s_worked_out_name_is_judged_as_the_page_shows_it(self):
        # No review asks a fill-in's line, and keyed right it changes nothing a
        # page shows, so a line restating her un-shouted name is told to go.
        flat = self.one(self.run_with(
            "shoutfill", '[player_names]\n"tui-mae-odowel" = "Tui-Mae Odowell"\n'))
        self.assertTrue(flat.endswith(
            "Did you mean tui-mae-odowell? The dashboard already shows "
            "tui-mae-odowell as Tui-Mae Odowell, the name the line gives, so "
            "delete the line."), flat)
        self.assertEqual(offered_lines(flat), [])
        keyed = self.run_with(
            "shoutfill", '[player_names]\ntui-mae-odowell = "Tui-Mae Odowell"\n')
        self.assertEqual(keyed["said"], [])
        self.assertEqual(keyed["shown"], self.run_with("shoutfill", "")["shown"])

    def test_a_name_one_page_shows_and_another_does_not_is_still_offered(self):
        # Grade A shows Ros D'Arcy and grade B Ros D Arcy: the line changes one.
        run = self.run_with("grades",
                            '[player_names]\n"ros-d-arci" = "Ros D\'Arcy"\n')
        self.assertEqual(sorted(names["ros-d-arcy"]
                                for names in run["shown"].values()),
                         ["Ros D Arcy", "Ros D'Arcy"])
        self.assertEqual(offered_corrections(self.one(run)),
                         [("ros-d-arcy", "Ros D'Arcy")])


class TestAPlayerShownByASpellingTheirAliasFoldsAwayIsToldTheLineToAdd(
        unittest.TestCase):
    """A player the dashboard shows by a spelling ``[aliases]`` says is wrong.

    An alias says which player a spelling is, and :func:`discover._spelling` shows
    them by a spelling it does not fold wherever a card they are spelt from gives
    one. Where none does, the first folded spelling is kept, the alias's target
    un-slugged being a name no card printed. Measured through ``build.py`` on the
    tree before this pass (/tmp/t7/scratch/DW2/probe.py): a Rockets fill-in printed
    only as ``Tui-Mae ODowel``, with ``"tui-mae-odowel" = "tui-mae-o-dowell"``, was
    ``tui-mae-o-dowell`` named ``Tui-Mae ODowel`` in ``season.json``, and a
    registered player whose every roster block and set row prints ``Bo Nakamora``,
    aliased to ``bo-nakamura``, was ``bo-nakamura`` named ``Bo Nakamora``. 206 and
    181 lines, 7 under ``--quiet`` each, what a build with no config prints of
    either, and not a word about the name: the club had said the spelling was
    wrong, and the page showed it.

    Said once a player, as a config warning, since the page it makes looks right
    and ``--quiet`` is the run a club schedules, naming the key the line goes under
    and leaving the name to the club: no card they are spelt from gives it, so any
    value offered would be a guess. Judged on the names each page shows, the
    careers page among them. A player with a ``[player_names]`` line is shown by it
    and is not judged, and nor is one that another warning already hands a line
    to, the line under the folded spelling, under a key a letter out, or under a
    name ``[namesakes]`` splits, so the one fix is asked for once. Handed over by
    the first two alone, a line under the split name and the Spinners' player it
    made printed ``Bo Nakamora`` were two warnings for the one line (194 lines, 20
    under ``--quiet``). A line told to go, giving the name shown, hands nobody one.
    Nor an alias keyed on the slug of the name it is printed by, which folds that
    spelling into itself. The key named is the player's, which for one the split
    made is the name and the team: the Spinners' player printed only ``Bo
    Nakamora``, folded and split, was quiet with
    ``bo-nakamura-lakeside-spinners = "Bo Nakamura"`` (181 lines, 7 under
    ``--quiet``), and a line under the alias's target was told that ``[namesakes]``
    splits that name (188, 14; /tmp/t7/scratch/DW2/review-base.txt).
    Rejected: a discovery note, which ``--quiet`` drops.
    """

    TUI_FOLD = '[aliases]\n"tui-mae-odowel" = "tui-mae-o-dowell"\n\n'
    BO_FOLD = '[aliases]\n"bo-nakamora" = "bo-nakamura"\n\n'
    MULTI = '[build]\nseasons = "multi"\n\n'
    NS = '[namesakes]\nbo-nakamura = ["Riverside Rockets", "Lakeside Spinners"]\n\n'

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        fillin = cls.root / "fillin" / "data"
        write_season_cards(fillin)
        write_round_four(fillin, {"riverside-rockets": "Tui-Mae ODowel"})
        everywhere = cls.root / "everywhere" / "data"
        write_season_cards(everywhere,
                           rename={"riverside-rockets": Swap(1, "Bo Nakamora")})
        for season in ("2025-winter", "2026-winter"):
            shutil.copytree(everywhere, cls.root / "two" / "data" / season)
        # Misspelt on every card of the older season, and right in the newer.
        shutil.copytree(everywhere, cls.root / "older" / "data" / "2025-winter")
        write_season_cards(cls.root / "older" / "data" / "2026-winter")
        four = cls.root / "four" / "data"
        write_season_cards(four)
        write_round_four(four, {"riverside-rockets": "Bo Nakamora"})
        # The Spinners' Bo Nakamura, split from the Rockets', printed Bo Nakamora.
        write_season_cards(cls.root / "split" / "data", rename={
            "lakeside-spinners": Swap(1, "Bo Nakamora", 4.0)})
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("fillin", "everywhere", "two", "older", "four",
                                  "split")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    @staticmethod
    def said(folded: str, target: str, name: str) -> str:
        return (f"[aliases] folds {folded} into {target}, but the dashboard still "
                f"shows {target} as {name}, the spelling the alias says is wrong: "
                f"an alias says who a spelling is, and not how to spell them. To "
                f"show the name as it should read, add a line for {target} under "
                f"[player_names], giving the name as the club spells it.")

    def test_a_fill_in_every_card_misspells_is_said_once_with_the_key_for_the_line(
            self):
        run = self.run_with("fillin", self.TUI_FOLD)
        flat = self.one(run)
        self.assertEqual(flat, self.said("tui-mae-odowel", "tui-mae-o-dowell",
                                         "Tui-Mae ODowel"))
        self.assertEqual(offered_lines(flat), [])
        self.assertEqual(run["names"]["tui-mae-o-dowell"], "Tui-Mae ODowel")
        self.assertEqual(self.run_with("fillin", "")["said"], [])

    def test_a_registered_player_every_card_misspells_is_said_as_a_fill_in_is(self):
        run = self.run_with("everywhere", self.BO_FOLD)
        self.assertEqual(self.one(run),
                         self.said("bo-nakamora", "bo-nakamura", "Bo Nakamora"))
        self.assertEqual(run["names"]["bo-nakamura"], "Bo Nakamora")

    def test_the_line_it_asks_for_quiets_it_and_the_name_arrives(self):
        # Keyed as the warning says, which is all a club has to go on: for a
        # player the split made, the name and the team, not the alias's target.
        for cards, fold, slug, name in (
                ("fillin", self.TUI_FOLD, "tui-mae-o-dowell", "Tui-Mae O'Dowell"),
                ("everywhere", self.BO_FOLD, "bo-nakamura", "Bo Nakamura"),
                ("split", self.BO_FOLD + self.NS, "bo-nakamura-lakeside-spinners",
                 "Bo Nakamura")):
            with self.subTest(cards):
                key = re.search(r"add a line for (\S+) under \[player_names\]",
                                self.one(self.run_with(cards, fold)))
                self.assertIsNotNone(key)
                run = self.run_with(cards, fold + f"[player_names]\n{key[1]} = "
                                                  f"{json.dumps(name)}\n")
                self.assertEqual(run["said"], [])
                self.assertEqual(run["names"][slug], name)

    def test_one_player_on_every_page_of_two_seasons_is_said_once(self):
        # Each season's page and the careers page show him by the misspelling.
        run = self.run_with("two", self.MULTI + self.BO_FOLD)
        self.assertEqual(len(run["shown"]), 2)
        self.assertEqual(self.one(run),
                         self.said("bo-nakamora", "bo-nakamura", "Bo Nakamora"))

    def test_a_season_s_page_is_judged_though_the_careers_page_spells_him_right(self):
        # The careers page shows the newest season's name; the older page does not.
        run = self.run_with("older", self.MULTI + self.BO_FOLD)
        self.assertEqual(sorted(names["bo-nakamura"]
                                for names in run["shown"].values()),
                         ["Bo Nakamora", "Bo Nakamura"])
        self.assertEqual(self.one(run),
                         self.said("bo-nakamora", "bo-nakamura", "Bo Nakamora"))

    def test_a_line_another_warning_hands_over_is_asked_for_once(self):
        # The line under the folded spelling, and one a letter out: each is told
        # the key the name goes under, which is the line this warning asks for.
        for key in ("bo-nakamora", "bo-nakamuru"):
            with self.subTest(key=key):
                flat = self.one(self.run_with(
                    "everywhere",
                    self.BO_FOLD + f'[player_names]\n"{key}" = "Bo Nakamura"\n'))
                self.assertTrue(flat.startswith(
                    f"[player_names] corrects {key}, but "), flat)
                self.assertEqual(offered_corrections(flat),
                                 [("bo-nakamura", "Bo Nakamura")])
        # A line told to go hands nobody a line, so the name is still asked for.
        run = self.run_with("everywhere", self.BO_FOLD + '[player_names]\n'
                                          '"bo-nakamora" = "Bo Nakamora"\n')
        self.assertEqual(len(run["said"]), 2, run["said"])
        self.assertTrue(" ".join(run["said"][0].split()).endswith(
            "The dashboard already shows bo-nakamura as Bo Nakamora, the name the "
            "line gives, so delete the line."), run["said"])
        self.assertEqual(" ".join(run["said"][1].split()),
                         self.said("bo-nakamora", "bo-nakamura", "Bo Nakamora"))

    def test_a_player_the_split_made_is_said_unless_the_split_s_line_hands_one_over(
            self):
        ns = self.BO_FOLD + ('[namesakes]\nbo-nakamura = ["Riverside Rockets", '
                             '"Lakeside Spinners"]\n\n')
        flat = self.one(self.run_with("split", ns))
        self.assertTrue(flat.startswith(
            "[aliases] folds bo-nakamora into bo-nakamura, but the dashboard still "
            "shows bo-nakamura-lakeside-spinners as Bo Nakamora,"), flat)
        # A line under the name the split made two of, under the spelling folded
        # into it, or a letter out, is told to write one for each player, the
        # Spinners' among them: that is the line asked for.
        for key, told in (("bo-nakamura", "but [namesakes] splits"),
                          ("bo-nakamora", "but [aliases] folds"),
                          ("bo-nakamuru", "but no card this run read")):
            with self.subTest(key=key):
                flat = self.one(self.run_with(
                    "split", ns + f'[player_names]\n"{key}" = "Bo Nakamura"\n'))
                self.assertTrue(flat.startswith(
                    f"[player_names] corrects {key}, {told}"), flat)
                self.assertIn(("bo-nakamura-lakeside-spinners", "Bo Nakamura"),
                              offered_corrections(flat))

    def test_a_spelling_a_card_gives_unfolded_or_one_folded_into_itself_is_quiet(
            self):
        cases = {
            "a roster block spells him right": ("four", self.BO_FOLD),
            "an alias onto its own key": (
                "fillin", '[aliases]\n"Tui-Mae ODowel" = "tui-mae-odowel"\n'),
            "a line of his own giving that spelling": (
                "everywhere",
                self.BO_FOLD + '[player_names]\nbo-nakamura = "Bo Nakamora"\n'),
        }
        for label, (cards, toml) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.run_with(cards, toml)["said"], [])


class TestAMisspeltNamesakeIsOfferedTheAliasWhereTheSplitReachesEveryCardOfIt(
        unittest.TestCase):
    """A ``[player_names]`` line giving a misspelling of a split name that name.

    4.84's warning offers the ``[aliases]`` line that joins a misspelling to the
    record it was meant for, and offered none wherever ``[namesakes]`` touched the
    records, because an alias is applied before the split. That holds of an alias
    keyed on a player the split made, which is nobody's slug until the split, and of
    one keyed on the name, which moves every player of it. An alias keyed on the
    misspelling is split by each side's team as the name is. Measured through
    ``build.py`` on the tree before this pass (/tmp/t7/scratch/DW2/probe.py, the
    four-round season with the Spinners' Bo Nakamura split from the Rockets' and
    printed ``Bo Nakamora`` in round four's set rows): ``"bo-nakamora" = "Bo
    Nakamura"`` was told no line is offered (230 lines, 17 under ``--quiet``), and
    ``"bo-nakamora" = "bo-nakamura"`` under ``[aliases]`` in its place made one
    Spinners record and said nothing (220 lines, 7).

    Offered where no record the line names is one the split made, or the name it
    splits, and every card printing the misspelling prints it for a team the entry
    lists, whose player the split made shares the name. Where a team the entry
    leaves out prints it too, no line is offered and that team is named: an alias
    applies to every card, so that team's player would become the bare name, the
    one the split leaves every other team. The same season with the Aces printing
    ``Bo Nakamora`` too, the alias pasted gave the Aces a ``bo-nakamura`` shown as
    ``Bo Nakamora`` and said nothing (236 lines, 7 under ``--quiet``), and so it did
    where the Aces printed it in a roster block alone, or in a washout's set rows
    alone (232 and 234 lines, 7 each; /tmp/t7/scratch/DW2/review-base.txt). Where
    the split made that team no player of the name, the Spinners printing only
    ``Bo Nakamora``, the alias would make them one of their own, still apart: on
    the tree before the review, which offered it, pasted with the line deleted it
    left two records and the Spinners' shown ``Bo Nakamora`` (187 lines, 13 under
    ``--quiet``, where the line alone gave 195 and 21;
    /tmp/t7/scratch/DW2/review-impl.txt). With two names split, 4.84's words stand:
    an alias keyed on ``cass-ellery-riverside-rockets`` changed no name. Rejected:
    offering the alias whenever the line names no player the split made; and
    wherever the entry lists every team printing the misspelling.
    """

    NS = ('[namesakes]\nbo-nakamura = ["Riverside Rockets", '
          '"Lakeside Spinners"]\n\n')
    ALIAS = '[aliases]\n"bo-nakamora" = "bo-nakamura"\n\n'
    LINE = '[player_names]\n"bo-nakamora" = "Bo Nakamura"\n'
    GIVEN = ("[player_names] gives bo-nakamora the name Bo Nakamura, which "
             "bo-nakamura-lakeside-spinners and bo-nakamura-riverside-rockets "
             "already have, so the dashboard shows them as separate players under "
             "that one name. If they are one person, ")
    APART = " If they are different people, give each a name of their own."
    LEFT_OUT = ("join them under [aliases], keyed on the wrong spelling as a card "
                "prints it. No line is offered: [namesakes] splits bo-nakamura after "
                "[aliases] is applied, and a card prints bo-nakamora for "
                "hilltop-aces, a team the entry does not list, so an alias would "
                "make that team's player the bo-nakamura of every team the entry "
                "leaves out.")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        for label, seconds in (
                ("split", {"lakeside-spinners": "Bo Nakamora"}),
                ("aces", {"lakeside-spinners": "Bo Nakamora",
                          "hilltop-aces": "Bo Nakamora"})):
            data = cls.root / label / "data"
            write_season_cards(
                data, rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
            write_round_four(data, seconds)
        # The Spinners print only the misspelling, so the split makes them nobody.
        write_season_cards(cls.root / "spinonly" / "data",
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamora", 4.0)})
        # The Aces print it too, in a roster block and nowhere else.
        rostered = cls.root / "rostered" / "data"
        write_season_cards(rostered, rename={
            "lakeside-spinners": Swap(1, "Bo Nakamura", 4.0),
            "hilltop-aces": Swap(2, "Bo Nakamora")})
        write_round_four(rostered, {"lakeside-spinners": "Bo Nakamora"})
        # The Aces print it too, in the set rows of a washout and nowhere else.
        washout = cls.root / "washout" / "data"
        write_season_cards(
            washout, rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        write_round_four(washout, {"lakeside-spinners": "Bo Nakamora"})
        (washout / "Round5").mkdir()
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        cw.write_card_file(washout / "Round5" / "washout.pdf", cw.CardSpec(
            date="2026-09-15", round_label="5", division="B", court="09",
            match_id="2000009", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)), home_players,
                          (away_players[0], "Bo Nakamora", *away_players[2:])),
            home_roster=squad_for("riverside-rockets", home_players)[0],
            away_roster=squad_for("hilltop-aces", away_players)[0],
            cancelled=True, verb="VS", winner=cw.NO_TICK))
        # Two names split, and neither misspelt.
        write_season_cards(cls.root / "twosplit" / "data",
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("split", "aces", "spinonly", "rostered", "washout",
                                  "twosplit")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    def bos(self, run: dict) -> dict[str, str]:
        return {slug: name for slug, name in run["names"].items()
                if slug.startswith("bo-")}

    def given(self, run: dict) -> str:
        """The one warning about a line giving a name another record has, unwrapped,
        beside the namesakes note a split that separated nobody is also given."""
        given = [flat for flat in (" ".join(said.split()) for said in run["said"])
                 if flat.startswith("[player_names] gives ")]
        self.assertEqual(len(given), 1, run["said"])
        return given[0]

    def test_a_misspelling_only_the_entry_s_teams_print_is_offered_the_alias(self):
        flat = self.one(self.run_with("split", self.NS + self.LINE))
        self.assertEqual(
            flat, self.GIVEN + 'join them under [aliases] with bo-nakamora = '
                  '"bo-nakamura", and delete the [player_names] line for '
                  'bo-nakamora. [namesakes] splits bo-nakamura after [aliases] is '
                  'applied, and the cards print bo-nakamora only for '
                  'lakeside-spinners, a team the entry lists, so the alias joins '
                  'the player the split made there.' + self.APART)
        self.assertEqual(offered_corrections(flat), [("bo-nakamora", "bo-nakamura")])

    def test_the_alias_pasted_and_the_line_deleted_is_one_spinners_record(self):
        # Pasted as the warning offers it, which is all a club has to go on.
        offered = offered_lines(self.one(self.run_with("split", self.NS + self.LINE)))
        self.assertNotEqual(offered, [])
        pasted = self.run_with("split", self.NS + "[aliases]\n"
                               + "".join(f"{line}\n" for line in offered))
        self.assertEqual(pasted["said"], [])
        self.assertEqual(self.bos(pasted),
                         {"bo-nakamura-lakeside-spinners": "Bo Nakamura",
                          "bo-nakamura-riverside-rockets": "Bo Nakamura"})

    def test_a_misspelling_a_team_the_entry_leaves_out_prints_is_offered_no_line(
            self):
        flat = self.one(self.run_with("aces", self.NS + self.LINE))
        self.assertEqual(flat, self.GIVEN + self.LEFT_OUT + self.APART)
        self.assertEqual(offered_lines(flat), [])
        # Pasted anyway, the Aces' player is the bare name, shown by the
        # misspelling the alias folds, and that is said.
        pasted = self.run_with("aces", self.NS + self.ALIAS)
        self.assertEqual(self.bos(pasted)["bo-nakamura"], "Bo Nakamora")
        self.assertIn("the dashboard still shows bo-nakamura as Bo Nakamora",
                      self.one(pasted))

    def test_a_team_left_out_is_named_where_its_roster_or_a_washout_alone_prints_it(
            self):
        # An alias applies to every name a card prints, so the Aces' player is
        # moved wherever their cards print the misspelling, a line of the roster
        # block or the set rows of a card that counts for nothing.
        for cards in ("rostered", "washout"):
            with self.subTest(cards):
                flat = self.one(self.run_with(cards, self.NS + self.LINE))
                self.assertEqual(flat, self.GIVEN + self.LEFT_OUT + self.APART)
                self.assertEqual(offered_lines(flat), [])
                pasted = self.run_with(cards, self.NS + self.ALIAS)
                self.assertEqual(self.bos(pasted)["bo-nakamura"], "Bo Nakamora")

    def test_a_misspelling_where_the_split_made_nobody_is_offered_no_line(self):
        # The Spinners print only Bo Nakamora, so the split made no player of
        # theirs for the alias to join it to.
        run = self.run_with("spinonly", self.NS + self.LINE)
        flat = self.given(run)
        self.assertEqual(
            flat, "[player_names] gives bo-nakamora the name Bo Nakamura, which "
                  "bo-nakamura-riverside-rockets already has, so the dashboard "
                  "shows them as separate players under that one name. If they are "
                  "one person, join them under [aliases], keyed on the wrong "
                  "spelling as a card prints it. No line is offered: [namesakes] "
                  "splits bo-nakamura after [aliases] is applied, and a card prints "
                  "bo-nakamora for lakeside-spinners, so an alias would make that "
                  "team's player bo-nakamura-lakeside-spinners, still apart from "
                  "bo-nakamura-riverside-rockets." + self.APART)
        self.assertEqual(offered_lines(flat), [])
        # Pasted anyway, with the line deleted, it is two records still, and the
        # Spinners' is shown by the spelling the alias folds.
        pasted = self.run_with("spinonly", self.NS + self.ALIAS)
        self.assertEqual(self.bos(pasted),
                         {"bo-nakamura-lakeside-spinners": "Bo Nakamora",
                          "bo-nakamura-riverside-rockets": "Bo Nakamura"})

    def test_a_line_among_the_players_of_two_split_names_is_offered_no_alias(self):
        # The line's record is a player the split of cass-ellery made, keyed on
        # which an alias reaches nobody: 4.84's reason, and it is 4.84's words.
        ns = ('[namesakes]\nbo-nakamura = ["Riverside Rockets", "Lakeside Spinners"]'
              '\ncass-ellery = ["Riverside Rockets", "Hilltop Aces"]\n\n')
        line = '[player_names]\n"cass-ellery-riverside-rockets" = "Bo Nakamura"\n'
        flat = self.given(self.run_with("twosplit", ns + line))
        self.assertEqual(
            flat, "[player_names] gives cass-ellery-riverside-rockets the name Bo "
                  "Nakamura, which bo-nakamura-lakeside-spinners and "
                  "bo-nakamura-riverside-rockets already have, so the dashboard "
                  "shows them as separate players under that one name. If they are "
                  "one person, join them under [aliases], keyed on the wrong "
                  "spelling as a card prints it. No line is offered: [namesakes] "
                  "splits bo-nakamura and cass-ellery after [aliases] is applied, so "
                  "an alias keyed on a player the split made reaches nobody, and one "
                  "keyed on bo-nakamura and cass-ellery moves every player of those "
                  "names." + self.APART)
        alias = '[aliases]\n"cass-ellery-riverside-rockets" = "bo-nakamura"\n\n'
        self.assertEqual(self.run_with("twosplit", ns + alias)["names"],
                         self.run_with("twosplit", ns)["names"])


class TestACorrectionUnderANameSplitAmongFillInsIsSaidWithALineForEach(
        unittest.TestCase):
    """A ``[player_names]`` line under a name ``[namesakes]`` split into fill-ins.

    The check that says a line under a split name reaches none of the players the
    split made looked those players up among the registered ones alone, and a
    fill-in is registered by no roster block. So a split made only of fill-ins had
    its line do nothing, and said nothing. Measured through ``build.py`` on the
    unfixed tree (/tmp/t6/scratch/m7/probe.py, p98-line): ``Pim Arkwright-Sole``
    filling in for both the Rockets and the Aces in round four, split between them,
    and ``"pim-arkwright-sole" = "Pim A-S"``, exited 0 with 202 lines, the only ones
    naming him ``ratings-missing`` and ``unknown-player`` about the keys the split
    made, and a ``--quiet`` rebuild printed 8 lines, none of them about him.

    The players the split made are found where a fill-in is, in the set rows, each
    under the side it played for, so the warning a registered split gets is given
    here too, the lines for each included. Every name a set row prints counts, a
    registered player's too: the player the entry leaves on the name, printed for
    another team as well, is still reached by the line there, and the build's
    ``player-two-teams`` error names both teams (/tmp/t6/scratch/m7/p98b). A
    washout's set rows count as well, since the page shows the fill-ins they name,
    where ``player-two-teams`` reads only the cards that count. Rejected: counting
    a set-row name only where no roster block registers it, which named their
    roster team alone, one team fewer than the error beside it.
    """

    SPLIT = ('[namesakes]\npim-arkwright-sole = ["Riverside Rockets", '
             '"Hilltop Aces"]\n\n')
    LINE = '[player_names]\n"pim-arkwright-sole" = "Pim A-S"\n'

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        data = cls.root / "split" / "data"
        write_season_cards(data)
        write_round_four(data, {"riverside-rockets": "Pim Arkwright-Sole",
                                "hilltop-aces": "Pim Arkwright-Sole"})
        # A Bo Nakamura for the Spinners too, and the Rockets' one printed in
        # Parkview's second seat in round four.
        lent = cls.root / "lent" / "data"
        write_season_cards(lent,
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        write_round_four(lent, {"parkview-wanderers": "Bo Nakamura"})
        # The same two fill-ins, their one night a washout, which is still shown.
        washout = cls.root / "washout" / "data"
        write_season_cards(washout)
        (washout / "Round4").mkdir()
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        cw.write_card_file(washout / "Round4" / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B", court="07",
            match_id="2000007", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)),
                          (home_players[0], "Pim Arkwright-Sole", *home_players[2:]),
                          (away_players[0], "Pim Arkwright-Sole", *away_players[2:])),
            home_roster=squad_for("riverside-rockets", home_players)[0],
            away_roster=squad_for("hilltop-aces", away_players)[0],
            cancelled=True, verb="VS", winner=cw.NO_TICK))
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("split", "lent", "washout")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    def test_a_split_made_only_of_fill_ins_is_given_a_line_for_each(self):
        run = self.run_with("split", self.SPLIT + self.LINE)
        flat = self.one(run)
        self.assertIn("[player_names] corrects pim-arkwright-sole, but [namesakes] "
                      "splits that name into one player per team, so the correction "
                      "is given to neither", flat)
        self.assertEqual(offered_corrections(flat),
                         [("pim-arkwright-sole-hilltop-aces", "Pim A-S"),
                          ("pim-arkwright-sole-riverside-rockets", "Pim A-S")])
        self.assertNotIn("still names", flat)
        pasted = self.run_with("split", self.SPLIT + "[player_names]\n" + "".join(
            f"{line}\n" for line in offered_lines(flat)))
        self.assertEqual(pasted["said"], [])
        self.assertEqual({pasted["names"][key] for key, _ in
                          offered_corrections(flat)}, {"Pim A-S"})

    def test_a_split_only_a_washout_prints_is_said_as_the_page_shows_it(self):
        # The washout's set rows are on the page, so the line reaches neither there.
        flat = self.one(self.run_with("washout", self.SPLIT + self.LINE))
        self.assertIn("so the correction is given to neither", flat)
        self.assertEqual(offered_corrections(flat),
                         [("pim-arkwright-sole-hilltop-aces", "Pim A-S"),
                          ("pim-arkwright-sole-riverside-rockets", "Pim A-S")])

    def test_a_team_the_entry_leaves_out_keeps_the_line_and_is_told_so(self):
        # The Aces' Pim is left on the name, so the line is still his. The entry's
        # second team never prints the name, which is the namesakes warning's to say.
        run = self.run_with("split", '[namesakes]\npim-arkwright-sole = ["Riverside '
                                     'Rockets", "Lakeside Spinners"]\n\n' + self.LINE)
        self.assertEqual(len(run["said"]), 2, run["said"])
        flat, split = (" ".join(said.split()) for said in run["said"])
        self.assertIn("no card puts that name in lakeside-spinners.", split)
        self.assertIn("so the correction is not given to the one player it made.",
                      flat)
        self.assertIn("It still names the pim-arkwright-sole of hilltop-aces, a team "
                      "the entry does not list, so leave it where it is.", flat)
        self.assertEqual(run["names"]["pim-arkwright-sole"], "Pim A-S")

    def test_the_player_it_leaves_alone_is_named_with_each_team_a_set_row_gives(self):
        # The Rockets' Bo Nakamura keeps the name, and round four prints them for
        # Parkview too: player-two-teams names both, and so does this.
        run = self.run_with("lent", '[namesakes]\nbo-nakamura = ["Lakeside Spinners", '
                                    '"Hilltop Aces"]\n\n[player_names]\n'
                                    'bo-nakamura = "Bo N"\n')
        self.assertEqual(len(run["said"]), 2, run["said"])
        flat, split = (" ".join(said.split()) for said in run["said"])
        self.assertIn("no card puts that name in hilltop-aces.", split)
        self.assertIn("so the correction is not given to the one player it made.",
                      flat)
        self.assertIn("It still names the bo-nakamura of parkview-wanderers and "
                      "riverside-rockets, teams the entry does not list, so leave it "
                      "where it is.", flat)


class TestANamesakesEntryForATeamOnlyAWashoutPrintsTheNameForIsSaidToWait(
        unittest.TestCase):
    """A ``[namesakes]`` team whose cards print the name only on one that counts for
    nothing is told it waits, and not that no card prints the name there.

    Since 4.44 such a card registers nobody, so the note that judges an entry by the
    players the cards built said no card puts the name in that team, which the card's
    roster block contradicts, and told the club to check the team against its banner,
    which is right. Measured through ``build.py`` on the unfixed tree
    (/tmp/t6/scratch/m7/probe.py, p143): Parkview's ``Kit Vasse`` also printed on the
    Rockets' roster of a round-four washout, and ``kit-vasse = ["parkview-wanderers",
    "riverside-rockets"]``, exited 0 with 170 lines and the note "no card puts that
    name in riverside-rockets ... check it against the banner it is printed under";
    8 lines under ``--quiet``, where notes are not printed.

    Said the way the ``[player_names]`` note says it of a line under such a name:
    the entry waits there for that player's first card that counts, and nothing is
    asked of the club. A team no card prints the name for is still told to check its
    banner, beside the one that waits. Rejected: saying nothing of a team that
    waits, which leaves a club with a washout and a misspelt team unable to tell
    from the note which is which.
    """

    WAITS = ("In riverside-rockets, that name is printed only on a card that counts "
             "for nothing so far (a washout, or a night not yet played), or on some "
             "but not all of the cards of a night that disagree. A card like that "
             "registers nobody, and a night whose cards disagree registers only the "
             "names they all print, so the entry waits there until a card that "
             "counts names that player.")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        data = cls.root / "washout" / "data"
        write_season_cards(data)
        (data / "Round4").mkdir()
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        cw.write_card_file(data / "Round4" / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B", court="07",
            match_id="2000007", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)), home_players,
                          away_players),
            home_roster=squad_for("riverside-rockets",
                                  (*home_players, "Kit Vasse"))[0],
            # The Aces' too, for an entry with two teams that wait.
            away_roster=squad_for("hilltop-aces", (*away_players, "Kit Vasse"))[0],
            cancelled=True, verb="VS", winner=cw.NO_TICK))
        cls.read = {"washout": (data, *scorecard.read_tree(data))}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with

    def notes_on(self, toml: str) -> list[str]:
        run = self.run_with("washout", toml)
        self.assertEqual(run["said"], [])
        return [" ".join(note.split()) for note in run["notes"]
                if note.startswith("[namesakes]")]

    def warned_on(self, toml: str) -> list[str]:
        """The warnings, for an entry with a team to check beside one that waits:
        that team is a mistake, which --quiet prints, and the entry is said once."""
        run = self.run_with("washout", toml)
        self.assertEqual([n for n in run["notes"] if n.startswith("[namesakes]")], [])
        return [" ".join(said.split()) for said in run["said"]]

    def test_a_team_only_a_washout_prints_the_name_for_is_said_to_wait(self):
        notes = self.notes_on('[namesakes]\nkit-vasse = ["parkview-wanderers", '
                              '"riverside-rockets"]\n')
        self.assertEqual(notes, [
            f"[namesakes] in {self.root}/config-{self.configs}.toml gives kit-vasse a "
            f"player of their own in parkview-wanderers and riverside-rockets. "
            f"{self.WAITS}"])

    def test_two_teams_that_wait_are_said_to_wait_for_those_players(self):
        notes = self.notes_on('[namesakes]\nkit-vasse = ["parkview-wanderers", '
                              '"riverside-rockets", "hilltop-aces"]\n')
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].endswith(
            "In riverside-rockets and hilltop-aces, that name is printed only on a "
            "card that counts for nothing so far (a washout, or a night not yet "
            "played), or on some but not all of the cards of a night that disagree. "
            "A card like that registers nobody, and a night whose cards disagree "
            "registers only the names they all print, so the entry waits there "
            "until a card that counts names those players."), notes[0])

    def test_a_team_no_card_prints_it_for_is_still_told_to_check_its_banner(self):
        notes = self.warned_on('[namesakes]\nkit-vasse = ["parkview-wanderers", '
                               '"riverside-rockets", "lakeside-spinners"]\n')
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("gives kit-vasse a player of their own in parkview-wanderers, "
                      "riverside-rockets and lakeside-spinners, and no card puts that "
                      "name in lakeside-spinners: check that team against the banner "
                      "it is printed under. " + self.WAITS, notes[0])

    def test_an_entry_that_waits_somewhere_is_never_said_to_separate_nobody(self):
        # Parkview's Kit Vasse is left as they were, so neither team named has
        # a player of that name: one waits and the other is missed.
        notes = self.warned_on('[namesakes]\nkit-vasse = ["riverside-rockets", '
                               '"lakeside-spinners"]\n')
        self.assertEqual(len(notes), 1, notes)
        self.assertNotIn("any of them", notes[0])
        self.assertNotIn("separated nobody", notes[0])
        self.assertTrue(notes[0].endswith(self.WAITS), notes[0])


class TestANamesakesEntryForATeamNoCardPrintsYetIsSaidToWait(unittest.TestCase):
    """A ``[namesakes]`` team a ``[competitions]`` section declares and no card
    prints yet is waited for, and is no team to check.

    With an entry's team to check made a warning, printed under ``--quiet``, a team
    on no card was a team to check. Measured through ``build.py`` on that tree
    (/tmp/t7/scratch/DW1/fix/declared.py): the test season with ``teams = [...]``
    declaring ``Quarry Quolls`` beside the four carded teams, and ``bo-nakamura =
    ["riverside-rockets", "quarry-quolls"]``, printed 12 lines under ``--quiet``
    where the unfixed tree printed 8, one of them telling the club to check
    ``quarry-quolls`` "against the banner it is printed under", which no card prints
    yet; with the Spinners' second also ``Bo Nakamura``, and the Spinners in the
    entry, 13, telling the club the Quolls "added nothing". Each entry was written
    ahead of the Quolls' first card, and right.

    Judged as a ``[team_names]`` line for a declared team is judged, as a line
    waiting and not a line wrong: the entry is a note that it waits for that team's
    first card, and a team beside it that no card puts the name in is still the one
    warning. Rejected: leaving it the warning, printed every week until the Quolls
    play; and passing over the team without a word, which leaves a club unable to
    tell a team the entry waits for from one it reached.
    """

    DECLARED = ('[competitions.tuesday-division-b]\nteams = ["Riverside Rockets", '
                '"Hilltop Aces", "Lakeside Spinners", "Parkview Wanderers", '
                '"Quarry Quolls", "Marina Magpies"]\n\n')
    ENTRY = '[namesakes]\nbo-nakamura = [{}]\n'

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        write_season_cards(cls.root / "alone" / "data")
        write_season_cards(cls.root / "beside" / "data",
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("alone", "beside")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    def notes_on(self, cards: str, teams: str) -> list[str]:
        run = self.run_with(cards, self.DECLARED + self.ENTRY.format(teams))
        self.assertEqual(run["said"], [])
        return [" ".join(note.split()) for note in run["notes"]
                if note.startswith("[namesakes]")]

    def test_a_declared_team_no_card_prints_is_a_note_that_it_waits(self):
        for cards, teams, listed in (
                ("alone", '"riverside-rockets", "quarry-quolls"',
                 "riverside-rockets and quarry-quolls"),
                ("beside", '"riverside-rockets", "lakeside-spinners", "quarry-quolls"',
                 "riverside-rockets, lakeside-spinners and quarry-quolls")):
            with self.subTest(cards):
                self.assertEqual(self.notes_on(cards, teams), [
                    f"[namesakes] in {self.root}/config-{self.configs}.toml gives "
                    f"bo-nakamura a player of their own in {listed}. No card this run "
                    f"read prints quarry-quolls, which [competitions] declares, so the "
                    f"entry waits there for that team's first card."])

    def test_two_declared_teams_no_card_prints_wait_for_their_first_cards(self):
        notes = self.notes_on("alone", '"riverside-rockets", "quarry-quolls", '
                                       '"marina-magpies"')
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].endswith(
            "No card this run read prints quarry-quolls and marina-magpies, which "
            "[competitions] declares, so the entry waits there for those teams' first "
            "cards."), notes[0])

    def test_a_declared_team_a_card_prints_is_still_a_team_to_check(self):
        # The Aces are declared and carded, and no Aces card prints the name.
        run = self.run_with("alone", self.DECLARED + self.ENTRY.format(
            '"riverside-rockets", "hilltop-aces"'))
        self.assertTrue(self.one(run).endswith(
            "no card puts that name in hilltop-aces, so the entry separated nobody. "
            "Check that team against the banner it is printed under."), run["said"])

    def test_beside_a_team_to_check_it_is_said_in_the_one_warning(self):
        run = self.run_with("alone", self.DECLARED + self.ENTRY.format(
            '"riverside-rockets", "lakeside-spinerz", "quarry-quolls"'))
        self.assertEqual([n for n in run["notes"] if n.startswith("[namesakes]")], [])
        self.assertTrue(self.one(run).endswith(
            "riverside-rockets, lakeside-spinerz and quarry-quolls, and no card puts "
            "that name in lakeside-spinerz: check that team against the banner it is "
            "printed under. No card this run read prints quarry-quolls, which "
            "[competitions] declares, so the entry waits there for that team's first "
            "card."), run["said"])


class TestANamesakesEntryWithATeamMissedSaysWhetherItSeparatedAnybody(
        unittest.TestCase):
    """A ``[namesakes]`` entry with a team no card puts the name in, said truly.

    The branch said "The rest were still separated; that one added nothing, so
    check it against the banner" whatever the rest were, and "that one" of three
    teams as of one. Measured through ``build.py`` on the unfixed tree
    (/tmp/t7/scratch/DW1/probe.py, t-alone and t-beside): on three rounds whose
    Rockets alone print ``Bo Nakamura``, ``bo-nakamura = ["riverside-rockets",
    "lakeside-spinners"]`` left one record, ``bo-nakamura-riverside-rockets``, and
    was told the rest were still separated; with the Spinners' second printed that
    way too and the entry's second team spelt ``lakeside-spinerz``, two records,
    ``bo-nakamura`` and ``bo-nakamura-riverside-rockets``, and the same sentence,
    true there. Three teams missed of four were "that one" in both.

    Judged on the records the entry leaves on one page, the name and the name with
    each of its teams: two or more on a competition's page, or on the careers page
    where one is written, and the entry parted somebody. Rejected: saying that an
    entry with one team left separated nobody, which the second season makes false,
    since the record the name keeps is the Spinners' player and the entry parted the
    two; and the missed teams worded by count alone, which leaves the first season
    told a split happened that did not.
    """

    ENTRY = '[namesakes]\nbo-nakamura = [{}]\n'
    TWO = '"riverside-rockets", "lakeside-spinerz"'
    FOUR = ('"riverside-rockets", "parkview-wandrers", "hilltop-acez", '
            '"lakeside-spinerz"')

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        write_season_cards(cls.root / "alone" / "data")
        write_season_cards(cls.root / "beside" / "data",
                           rename={"lakeside-spinners": Swap(1, "Bo Nakamura", 4.0)})
        write_two_grade_cards(cls.root / "grades" / "data", shared="Ada Renn")
        cls.read = {label: (cls.root / label / "data",
                            *scorecard.read_tree(cls.root / label / "data"))
                    for label in ("alone", "beside", "grades")}
        cls.configs = 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    run_with = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.run_with
    one = TestACorrectionThatReachesNobodyIsSaidOnceWithTheKeyToUse.one

    def records(self, run: dict) -> list[str]:
        return sorted(slug for slug in run["names"] if slug.startswith("bo-nakamura"))

    def test_one_record_left_is_said_to_have_separated_nobody(self):
        cases = {
            "one team missed": (self.TWO.replace("spinerz", "spinners"),
                                "lakeside-spinners, so the entry separated nobody. "
                                "Check that team against the banner it is printed "
                                "under."),
            "three missed": (self.FOUR, "parkview-wandrers, hilltop-acez and "
                                        "lakeside-spinerz, so the entry separated "
                                        "nobody. Check each of those against the "
                                        "banner it is printed under."),
        }
        for label, (teams, said) in cases.items():
            with self.subTest(label):
                run = self.run_with("alone", self.ENTRY.format(teams))
                self.assertEqual(self.records(run), ["bo-nakamura-riverside-rockets"])
                flat = self.one(run)
                self.assertTrue(flat.endswith(f"no card puts that name in {said}"),
                                flat)
                self.assertNotIn("still separated", flat)

    def test_two_records_left_are_said_to_be_still_separated_by_count(self):
        cases = {
            "one team missed": (self.TWO, "lakeside-spinerz. The rest were still "
                                          "separated; that one added nothing, so "
                                          "check it against the banner it is "
                                          "printed under."),
            "three missed": (self.FOUR, "parkview-wandrers, hilltop-acez and "
                                        "lakeside-spinerz. The rest were still "
                                        "separated; those added nothing, so check "
                                        "each of those against the banner it is "
                                        "printed under."),
        }
        for label, (teams, said) in cases.items():
            with self.subTest(label):
                run = self.run_with("beside", self.ENTRY.format(teams))
                self.assertEqual(self.records(run),
                                 ["bo-nakamura", "bo-nakamura-riverside-rockets"])
                flat = self.one(run)
                self.assertTrue(flat.endswith(f"no card puts that name in {said}"),
                                flat)
                self.assertNotIn("separated nobody", flat)

    def test_the_team_spelt_as_its_banner_is_splits_both_and_says_nothing(self):
        run = self.run_with("beside", self.ENTRY.format(
            self.TWO.replace("spinerz", "spinners")))
        self.assertEqual(run["said"], [])
        self.assertEqual(self.records(run), ["bo-nakamura-lakeside-spinners",
                                             "bo-nakamura-riverside-rockets"])

    def test_the_careers_page_is_a_page_the_entry_parts_people_on(self):
        # Ada Renn opens for grade A's Magpies and grade B's Starlings. Each grade's
        # page holds one record of the name whatever the entry says, but the careers
        # page joins grades by name, and there the entry is two careers or one.
        entry = '[namesakes]\nada-renn = ["marina-magpies", "quarry-quollz"]\n'
        self.assertIn("quarry-quollz, so the entry separated nobody.",
                      self.one(self.run_with("grades", entry)))
        with mock.patch.object(discover_mod.careers, "writes_careers",
                               return_value=True):
            flat = self.one(self.run_with("grades", entry))
        self.assertIn("quarry-quollz. The rest were still separated;", flat)


# --- rounding -----------------------------------------------------------------


class TestTheAverageIsRoundedTheWayASpreadsheetRounds(unittest.TestCase):
    """Half a cent, rounded up, in both the Python and the JavaScript.

    A club checks this dashboard against a figure it worked out in a spreadsheet,
    and a single last digit out of step is enough to make it distrust the whole
    table. Two things conspire: Python and JavaScript both round a half to even,
    and 6.225 is not really a half in binary -- it is stored a hair below, so even
    round-half-up on the float rounds it down. Quantising the shortest decimal that
    round-trips the float, which is what ``str()`` gives, makes it a half again.

    ``app.js`` reaches the same values by the same reasoning; the structural half
    of this check lives in ``test_web_assets.py``.
    """

    def test_a_half_cent_goes_up_not_to_even(self):
        self.assertEqual(stats.round_half_up(6.225), 6.23)
        self.assertEqual(stats.round_half_up(3.675), 3.68)
        self.assertEqual(stats.round_half_up(2.675), 2.68)
        self.assertEqual(stats.round_half_up(1.005), 1.01)

    def test_pythons_own_rounding_would_have_given_the_other_answer(self):
        # Named so that nobody 'simplifies' this back to round().
        self.assertEqual(round(6.225, 2), 6.22)
        self.assertEqual(round(3.675, 2), 3.67)
        self.assertEqual(f"{23.25:.1f}", "23.2")

    def test_the_points_column_rounds_the_same_way_at_one_place(self):
        self.assertEqual(stats.round_half_up(23.25, 1), 23.3)
        self.assertEqual(stats.round_display(23.25), 23.3)

    def test_it_rounds_a_negative_half_away_from_zero_too(self):
        self.assertEqual(stats.round_half_up(-6.225), -6.23)

    def test_a_value_that_needs_no_rounding_is_returned_unchanged(self):
        self.assertEqual(stats.round_half_up(6.2), 6.2)
        self.assertEqual(stats.round_half_up(0.0), 0.0)

    def test_nothing_to_round_stays_nothing(self):
        # A team with no decided match has no average, and '0.00' would be a
        # claim about it.
        self.assertIsNone(stats.round_half_up(None))
        self.assertIsNone(stats.round_display(None))


# --- the association's sheet ---------------------------------------------------


def typed_sheet(after_round: str, *lines) -> config_mod.ExpectedSheet:
    """``[competitions.<id>.expected]`` as the loader hands it on: (key, points, played)."""
    return config_mod.ExpectedSheet(after_round=after_round, lines=tuple(
        config_mod.ExpectedLine(key=key, points=points, played=played)
        for key, points, played in lines))


# The clean season's figures after round 3, from EXPECTED_POINTS (worked out on
# paper above), as a club would copy them off an association sheet that agrees.
AGREEING_SHEET = (("Riverside Rockets", "19.0", 3), ("Lakeside Spinners", "15.8", 3),
                  ("Parkview Wanderers", "11.1", 3), ("Hilltop Aces", "10.7", 3))
ROW_FOUR = (" It had no week off, so the difference is in one of its matches: "
            "compare them against the sheet's.")
SET_ASIDE_ONE = (" Nothing from round %s was counted for it, and the finding about "
                 "that round says why: deal with it first, then compare its matches "
                 "against the sheet's.")
NOTHING_READ_ZERO = (
    " Nothing was read for it in round 3. If it played that week, that card has not "
    "been exported. If it rested, a rest scores nothing under unplayed = \"zero\", so "
    "the rest is not the difference unless the association credits one: that is "
    "unplayed = \"average\", with the rest written in this competition's byes table.")


class SheetChecks:
    """The sheet held up against a season exactly as ``build.py`` holds it up.

    Compared once, from the matches as read, and the one comparison handed to the
    findings. ``lines`` are ``(key, points, played)`` as typed.
    """

    def raw_matches(self):
        return list(self.discovery.matches[self.comp_id])

    def checked(self, after_round, *lines, matches=None, competition=None):
        matches = self.raw_matches() if matches is None else list(matches)
        competition = competition or self.competition
        comparison = sheet_mod.compare(matches, competition,
                                       typed_sheet(after_round, *lines))
        report = validate.validate(matches, competition, club=self.discovery.club,
                                   cards=self.cards, rejected=self.rejected,
                                   sheet=comparison)
        return comparison, report

    @staticmethod
    def sheet_issues(report):
        return [issue for issue in report.issues if issue.rule.startswith("sheet-")]

    def only_finding(self, report):
        found = self.sheet_issues(report)
        self.assertEqual(len(found), 1, [i.message for i in found])
        return found[0]


class SheetSeasonCase(SheetChecks, SeasonCase):
    """The clean three-round season, four teams, no rest anywhere."""


class ByeSheetCase(SheetChecks, ByeSeasonCase):
    """The three-round, three-team season in which each team rests once."""


class TestASheetThatAgreesRaisesNothing(SheetSeasonCase):
    """A sheet typed from the paper sums agrees with every team, and says nothing.

    The figures are EXPECTED_POINTS, the per-match arithmetic above, never
    ``team_stats``: a table copied off the code would agree with a scoring bug.
    Rejected: a note for every team that agrees, which is a finding about nothing.
    """

    def test_every_team_is_compared_and_every_one_agrees(self):
        comparison, report = self.checked("3", *AGREEING_SHEET)
        self.assertEqual(comparison.status, sheet_mod.COMPARED)
        self.assertEqual([c.slug for c in comparison.agreeing], sorted(TEAMS))
        self.assertEqual(comparison.held, ())
        self.assertEqual(self.sheet_issues(report), [])


class TestATeamTheSheetDisagreesWithIsNamedWithBothFiguresAndItsRounds(SheetSeasonCase):
    """One team out is one warning, with both figures, the gap and what each round gave.

    Measured before this existed: the table was ignored with "expected is not a
    setting", so a club found a 0.2 gap only by holding the sheet up to the page.
    A warning and not an error: the cards are the result, the sheet is a claim about
    them, and a nightly build must not fail over the association's arithmetic.
    Rejected: printing only the two totals, which leaves the club to find the round.
    """

    def disagreeing(self, *aces):
        rest = [line for line in AGREEING_SHEET if line[0] != "Hilltop Aces"]
        return self.checked("3", *rest, ("Hilltop Aces",) + aces)

    def test_the_finding_names_the_team_both_figures_and_the_gap(self):
        _, report = self.disagreeing("10.9", 3)
        issue = self.only_finding(report)
        self.assertEqual(issue.rule, "sheet-mismatch")
        self.assertEqual(issue.severity, validate.WARNING)
        self.assertEqual(issue.subject, "hilltop-aces")
        self.assertEqual(issue.message, (
            "after round 3 the cards give Hilltop Aces 10.7 points where the "
            "association's sheet has 10.9, 0.2 under it." + ROW_FOUR))

    def test_the_detail_is_what_each_round_gave_the_team(self):
        # 2.6, 4.9 and 3.2 are the paper figures above, one per match.
        _, report = self.disagreeing("10.9", 3)
        self.assertEqual(self.only_finding(report).detail, "R1 2.6; R2 4.9; R3 3.2")

    def test_a_sheet_above_the_cards_is_over_it(self):
        _, report = self.disagreeing("10.5", 3)
        self.assertIn("10.7 points where the association's sheet has 10.5, 0.2 "
                      "over it.", self.only_finding(report).message)

    def test_both_figures_out_are_one_sentence(self):
        _, report = self.disagreeing("10.9", 4)
        self.assertEqual(self.only_finding(report).message, (
            "after round 3 the cards give Hilltop Aces 10.7 points where the "
            "association's sheet has 10.9, 0.2 under it, and 3 matches played "
            "where it has 4, 1 fewer." + ROW_FOUR))

    def test_matches_played_alone_out_names_the_sheet(self):
        _, report = self.disagreeing("", 2)
        self.assertEqual(self.only_finding(report).message, (
            "after round 3 the cards give Hilltop Aces 3 matches played where the "
            "association's sheet has 2, 1 more." + ROW_FOUR))

    def test_the_build_passes_or_fails_exactly_as_it_did_without_a_sheet(self):
        _, report = self.disagreeing("10.9", 3)
        plain = validate.validate(self.raw_matches(), self.competition,
                                  club=self.discovery.club, cards=self.cards,
                                  rejected=self.rejected)
        self.assertEqual(report.ok, plain.ok)
        self.assertEqual(len(report.errors), len(plain.errors))


class TestTheSheetIsComparedOnlyAsFarAsItsRound(SheetSeasonCase):
    """``after_round`` is the slice: the ladder after that round, whatever came later.

    A sheet is typed in once and cards keep arriving; compared against the whole
    season it would go out of date the week round 4's cards came in. Measured on
    paper: through round 2 the four teams stand at 12.6, 9.6, 8.4 and 7.5, two
    matches each. Rejected: comparing only the round's own cards, which is not what
    a ladder is.
    """

    ROUND_TWO = (("Riverside Rockets", "12.6", 2), ("Lakeside Spinners", "9.6", 2),
                 ("Parkview Wanderers", "8.4", 2), ("Hilltop Aces", "7.5", 2))

    def test_a_round_two_sheet_agrees_while_round_three_is_read(self):
        comparison, report = self.checked("2", *self.ROUND_TWO)
        self.assertEqual(len(comparison.agreeing), 4)
        self.assertEqual(self.sheet_issues(report), [])

    def test_a_disagreement_in_the_round_itself_is_caught(self):
        _, report = self.checked("2", *self.ROUND_TWO[:3], ("Hilltop Aces", "7.7", 2))
        issue = self.only_finding(report)
        self.assertEqual(issue.subject, "hilltop-aces")
        self.assertEqual(issue.detail, "R1 2.6; R2 4.9")
        self.assertIn("7.5 points where the association's sheet has 7.7, 0.2 under",
                      issue.message)


class TestASheetAheadOfTheCardsIsOneFindingNotOnePerTeam(SheetSeasonCase):
    """A sheet for a round nobody has a card for is one ``sheet-ahead``.

    Compared anyway, every team would disagree by a round's points and the report
    would read as four scoring faults. Reached is the rule the fixture list already
    uses (a round is gone past once any later round has a card), not the calendar.
    Rejected: a clock, which would make one folder of cards report differently each
    week.
    """

    def test_a_round_four_sheet_is_one_finding_with_the_latest_round_read(self):
        comparison, report = self.checked("4", *AGREEING_SHEET)
        self.assertEqual(comparison.status, sheet_mod.AHEAD)
        issue = self.only_finding(report)
        self.assertEqual(issue.rule, "sheet-ahead")
        self.assertEqual(issue.subject, "")
        self.assertEqual(issue.message, (
            "the sheet table runs to round 4, and no card for round 4 or a later "
            "round has been read, so nothing was compared. Export round 4's cards, "
            "or set after_round to the round the typed figures are from."))
        self.assertEqual(issue.detail, "latest round read: 3")

    def test_the_round_the_cards_have_reached_is_not_ahead(self):
        comparison, _ = self.checked("3", *AGREEING_SHEET)
        self.assertEqual(comparison.status, sheet_mod.COMPARED)

    def test_a_round_with_no_card_is_reached_once_a_later_round_has_one(self):
        # Round 2's cards gone and round 3's here: the season has gone past round 2,
        # so its sheet is compared, and every team's round 2 says nothing was read.
        without = [m for m in self.raw_matches() if m.round != "2"]
        comparison, _ = self.checked("2", ("Hilltop Aces", "7.5", 2),
                                     matches=without)
        self.assertEqual(comparison.status, sheet_mod.COMPARED)
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 nothing read"))

    def without_rounds(self, *gone):
        """The cards with every card of *gone* removed, and the draw derived then.

        A derived draw is the rounds with cards: with round 2's folder deleted
        discovery gives a round order of 1 and 3 (/tmp probe, review of 4.56).
        """
        competition = dataclasses.replace(self.competition, rounds={
            n: r for n, r in self.competition.rounds.items() if n not in gone})
        return ([m for m in self.raw_matches() if m.round not in gone],
                competition)

    def test_a_round_nobody_has_a_card_for_is_in_a_later_sheets_evidence(self):
        # Round 2 is in neither the cards nor the draw: it is found as a number
        # between the rounds that are.
        matches, competition = self.without_rounds("2")
        comparison, _ = self.checked("3", ("Hilltop Aces", "10.7", 3),
                                     matches=matches, competition=competition)
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 nothing read", "R3 3.2"))

    def test_every_round_up_to_the_sheets_is_evidence_with_a_later_one_read(self):
        # Only round 3 read, and a sheet after round 2: rounds 1 and 2 are the
        # season's, found below round 3, and round 3 is past the sheet.
        matches, competition = self.without_rounds("1", "2")
        comparison, _ = self.checked("2", ("Hilltop Aces", "7.5", 2),
                                     matches=matches, competition=competition)
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 nothing read", "R2 nothing read"))

    def test_a_round_the_draw_has_and_no_card_reached_is_in_the_evidence(self):
        # Round 1 read, and a final that reaches round 3: rounds 2 and 3 are the
        # competition's, and are evidence with no card read for either.
        raw = self.raw_matches()
        final = dataclasses.replace(next(m for m in raw if m.round == "3"),
                                    round="GF")
        comparison, _ = self.checked("3", ("Hilltop Aces", "10.7", 3), matches=[
            m for m in raw if m.round == "1"] + [final])
        check, = comparison.checks
        self.assertEqual(check.evidence,
                         ("R1 2.6", "R2 nothing read", "R3 nothing read"))


class TestARoundTypedAsADateIsOneRoundNotMillions(SheetSeasonCase):
    """``after_round = 20260915``, a date typed where the round goes, costs one round.

    Measured before this: the evidence was built for every whole number up to the
    sheet's round before the sheet was found to be ahead, so the demo's ``--check``
    took 77s and 4.6 GB to print the right ``sheet-ahead`` (0.5s and 28 MB now),
    and this season ran out of memory under a 6 GB cap (review of 4.56). A finals
    card reaches every numbered round, so after the finals the same table was
    compared, with a line of evidence per number (/tmp probe: 1000 lines for a
    round-1000 sheet). Now the sheet is asked first, and the evidence is filled
    only as far as the last round known. Rejected: the evidence from the rounds
    known alone, which loses a round nobody has a card for (above); and a ceiling on
    the number typed, which no association's sheet sets.
    """

    DATE = "20260915"

    def test_a_sheet_ahead_is_one_finding_and_no_team_is_priced(self):
        refused = AssertionError("a team was priced for a sheet no card has reached")
        with mock.patch.object(sheet_mod, "_evidence_rounds", side_effect=refused), \
                mock.patch.object(sheet_mod, "_checked", side_effect=refused):
            comparison, report = self.checked(self.DATE, *AGREEING_SHEET)
        self.assertEqual(comparison.status, sheet_mod.AHEAD)
        issue = self.only_finding(report)
        self.assertEqual(issue.rule, "sheet-ahead")
        self.assertIn(f"runs to round {self.DATE}, and no card", issue.message)
        self.assertEqual(issue.detail, "latest round read: 3")

    def test_after_the_finals_a_date_is_one_round_of_evidence(self):
        raw = self.raw_matches()
        final = dataclasses.replace(next(m for m in raw if m.round == "3"),
                                    round="GF")
        comparison, report = self.checked(self.DATE, ("Hilltop Aces", "10.7", 3),
                                          matches=raw + [final])
        self.assertEqual(comparison.status, sheet_mod.COMPARED)
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2",
                                          f"R{self.DATE} nothing read"))
        self.assertEqual(self.sheet_issues(report), [])


class TestARoundLabelledAMillionIsOneRunOfEvidenceNotAMillionLines(SheetSeasonCase):
    """Three or more whole numbers no card and no draw has are one entry, a run.

    The evidence named every whole number up to the last round known, and a card
    or a declared round labelled 1000000 is a round known. Measured through
    ``build.py`` on the demo season, its round-3 Magpies card printed as round N
    and a sheet line after round N (/tmp/t6/scratch/m4/p87.py): exit 0 every
    time, at 1000 in 0.49s and 32 MB with a report of 599 lines, at 100000 in
    0.85s and 88 MB with 43,417 lines (3,267,693 bytes), at 1000000 in 4.88s and
    540 MB with 455,917 lines, and the advice "rounds 3, 6, 7, 8, 9, 10, ...". So
    a gap of three numbers or more is one :class:`~matchcentre.sheet.Run`, said
    ``R4-R999999 nothing read``, and a gap of one or two is a round each, as 4.56
    printed it. Rejected: a ceiling on the label, which no card sets and which
    would refuse a round a card printed; and folding a round the season has
    cards in into the run beside it, which loses that the other teams have a card
    there and this one has not.
    """

    def relabelled(self, label):
        """The cards, with the Aces' round-3 card printed as round *label*."""
        return [dataclasses.replace(m, round=label)
                if m.round == "3" and m.involves("hilltop-aces") else m
                for m in self.raw_matches()]

    def test_a_card_labelled_a_million_is_five_entries_of_evidence(self):
        comparison, report = self.checked("1000000", ("Hilltop Aces", "10.9", None),
                                          matches=self.relabelled("1000000"))
        check, = comparison.checks
        self.assertEqual(len(check.evidence), 5)
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 nothing read",
                                          "R4-R999999 nothing read", "R1000000 3.2"))
        self.assertEqual(check.nothing_read, ("3", sheet_mod.Run(4, 999999)))
        self.assertEqual(self.only_finding(report).message, (
            "after round 1000000 the cards give Hilltop Aces 10.7 points where the "
            "association's sheet has 10.9, 0.2 under it. Nothing was read for it in "
            "rounds 3 and 4 to 999999. If it played those weeks, those cards have "
            "not been exported. If it rested, a rest scores nothing under unplayed "
            "= \"zero\", so the rest is not the difference unless the association "
            "credits one: that is unplayed = \"average\", with the rest written in "
            "this competition's byes table."))

    def test_three_numbers_nobody_has_are_a_run_and_two_are_a_round_each(self):
        for label, between in (("7", ("R4-R6 nothing read",)),
                               ("6", ("R4 nothing read", "R5 nothing read")),
                               ("4", ())):
            with self.subTest(label=label):
                comparison, _ = self.checked(label, ("Hilltop Aces", "10.9", None),
                                             matches=self.relabelled(label))
                check, = comparison.checks
                self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9",
                                                  "R3 nothing read", *between,
                                                  f"R{label} 3.2"))

    def test_a_declared_round_of_a_million_is_one_run_after_the_finals(self):
        raw = self.raw_matches()
        final = dataclasses.replace(next(m for m in raw if m.round == "3"),
                                    round="GF")
        competition = dataclasses.replace(self.competition, rounds={
            **self.competition.rounds, "1000000": Round(number="1000000")})
        comparison, _ = self.checked("1000000", ("Hilltop Aces", "10.7", None),
                                     matches=raw + [final], competition=competition)
        check, = comparison.checks
        self.assertEqual(len(check.evidence), 5)
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2",
                                          "R4-R999999 nothing read",
                                          "R1000000 nothing read"))


def write_round_six(data: Path) -> None:
    """Round 6's rockets v aces, played as round 1 was: the one card past round 3."""
    _, home, away, scores = FIXTURES[0]
    home_name, home_players = TEAMS[home]
    away_name, away_players = TEAMS[away]
    home_rows, home_on_court = squad_for(home, home_players)
    away_rows, away_on_court = squad_for(away, away_players)
    folder = data / "Round6"
    folder.mkdir(parents=True, exist_ok=True)
    cw.write_card_file(folder / "RiversideRockets-HilltopAces.pdf", cw.CardSpec(
        date="2026-09-22", round_label="6", division="B", court="07",
        match_id="2000007", home_name=home_name, away_name=away_name,
        rows=rows_for(scores, home_on_court, away_on_court),
        home_roster=home_rows, away_roster=away_rows))


class DeclaredDrawSheetCase(SheetChecks, SeasonCase):
    """The clean season and a round-6 card, under a typed-in draw with no round 4.

    Rounds 1 to 3 are typed as the cards give them. Round 5 is the rockets v the
    wanderers, never exported, with the spinners resting; round 6 is the rockets v
    the aces. So the aces have no fixture in round 5, the spinners none in round 6,
    and nobody has one in round 4.
    """

    @classmethod
    def extra_cards(cls):
        write_round_six(cls.data)

    @classmethod
    def make_config(cls):
        name = {slug: team[0].title() for slug, team in TEAMS.items()}
        fixtures: dict[str, list[tuple[str, str]]] = {}
        for rnd, home, away, _ in FIXTURES:
            fixtures.setdefault(rnd, []).append((home, away))
        fixtures["5"] = [("riverside-rockets", "parkview-wanderers")]
        fixtures["6"] = [("riverside-rockets", "hilltop-aces")]
        draw = "".join(
            f'\n[[competitions.tuesday-b.rounds]]\nnumber = "{n}"\nfixtures = ['
            + ", ".join(f'["{name[home]}", "{name[away]}"]' for home, away in pairs)
            + "]\n" + ('bye = "Lakeside Spinners"\n' if n == "5" else "")
            for n, pairs in fixtures.items())
        path = cls.root / "config.toml"
        path.write_text(
            '[competitions.tuesday-b]\nday = "Tuesday"\ndivision = "B"\nteams = ['
            + ", ".join(f'"{team}"' for team in name.values()) + "]\n" + draw,
            encoding="utf-8")
        return config_mod.load(path)

    def with_rounds(self, **rounds):
        """The competition with *rounds* in place of its own, ``None`` to drop one."""
        changed = {**self.competition.rounds, **rounds}
        return dataclasses.replace(self.competition, rounds={
            number: rnd for number, rnd in changed.items() if rnd is not None})


class TestUnderADeclaredDrawARoundWithoutTheTeamIsNoFixtureAndAsksForNoCard(
        DeclaredDrawSheetCase):
    """Under a typed-in draw the evidence says what the draw gives the team.

    Measured through ``build.py`` on the demo, its six teams declared, a draw of
    rounds 1-5, 7 (the Magpies v the Bunyips alone, no card) and GF, and a sheet
    line for the Quolls after round 7 (/tmp/t7/scratch/SH/probe/p167.py): exit 0,
    ``R1 6.3; R2 3.4; R3 6.1; R4 6.3; R5 3.4; R6 nothing read; R7 nothing read``
    and "Nothing was read for it in rounds 6 and 7. If it played those weeks,
    those cards have not been exported." The draw has no round 6 and round 7's
    fixtures leave the Quolls out, so no card was missing. Now a number the draw
    passes over is no entry, and a round whose typed fixtures leave the team out
    is ``no fixture``, a week off. A round typed bare, whose fixtures were read off
    its cards, says nothing about who played, and stays ``nothing read``, as does a
    number past the last round the club typed. Rejected: ``nothing read`` with a
    caveat, which still asks for a card; and leaving the round off the line, which
    hides a week the sheet covers.
    """

    def test_a_number_the_draw_passes_over_is_no_entry(self):
        comparison, _ = self.checked("6", ("Hilltop Aces", "10.9", None))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2",
                                          "R5 no fixture", "R6 2.6"))
        self.assertEqual(check.nothing_read, ())

    def test_a_round_whose_fixtures_leave_the_team_out_is_a_week_off(self):
        comparison, report = self.checked("6", ("Hilltop Aces", "10.9", None))
        check, = comparison.checks
        self.assertEqual(check.weeks_off, ("5",))
        self.assertEqual(self.only_finding(report).message, (
            "after round 6 the cards give Hilltop Aces 13.3 points where the "
            "association's sheet has 10.9, 2.4 over it. It had round 5 off: check "
            "that week against the sheet's byes first, then its matches."))

    def test_the_sheets_own_round_the_draw_passes_over_is_no_fixture(self):
        comparison, _ = self.checked("4", ("Hilltop Aces", "10.9", None))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2",
                                          "R4 no fixture"))
        self.assertEqual((check.nothing_read, check.weeks_off), ((), ("4",)))

    def test_a_rest_the_draw_declares_is_a_bye_though_nothing_credited_it(self):
        # Round 5's one card is missing, so nothing in it is counted, and a rest
        # earns nothing in a round nobody has played yet (stats.with_byes).
        comparison, report = self.checked("6", ("Lakeside Spinners", "15.9", None))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 6.1", "R2 3.5", "R3 6.2", "R5 bye",
                                          "R6 no fixture"))
        self.assertEqual((check.nothing_read, check.weeks_off), ((), ("5", "6")))
        self.assertTrue(self.only_finding(report).message.endswith(
            " It had rounds 5 and 6 off: check those weeks against the sheet's byes "
            "first, then its matches."))

    def test_a_round_whose_fixtures_came_from_its_cards_is_still_nothing_read(self):
        # Round 6 as a round typed bare, which takes its fixtures from its card
        # (discover._build_draw): the draw does not say who plays in it.
        bare = dataclasses.replace(self.competition.rounds["6"],
                                   source=DRAW_FROM_CARDS)
        comparison, _ = self.checked("6", ("Lakeside Spinners", "15.9", None),
                                     competition=self.with_rounds(**{"6": bare}))
        check, = comparison.checks
        self.assertEqual(check.evidence[-2:], ("R5 bye", "R6 nothing read"))
        self.assertEqual(check.nothing_read, ("6",))

    def test_past_the_last_round_typed_a_number_no_card_has_is_still_found(self):
        """The cards run past the draw, and the draw speaks only as far as it goes.

        Measured on the demo with a draw of rounds 1-3 and every round-4 card
        deleted (p167.py grown): ``R4 nothing read``, and F3 says the season ran
        past the draw. Filling no number at all under a declared draw gave
        ``R1 6.3; R2 3.4; R3 6.1; R5 3.4`` and "It had no week off" of a week
        nothing was read in. Nor does a round typed bare move the floor: one with
        cards is kept as read off them, like a round the draw never listed.
        """
        # Round 5 typed bare, with no card, and round 6 as a round the draw does not
        # list, read off its card: fixtures are typed up to round 3 alone.
        from_cards = dataclasses.replace(self.competition.rounds["6"],
                                         source=DRAW_FROM_CARDS)
        comparison, report = self.checked(
            "6", ("Hilltop Aces", "10.9", None), competition=self.with_rounds(**{
                "5": Round(number="5", source=DRAW_DECLARED), "6": from_cards}))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2",
                                          "R4 nothing read", "R5 nothing read",
                                          "R6 2.6"))
        self.assertIn(" Nothing was read for it in rounds 4 and 5.",
                      self.only_finding(report).message)

    def test_a_team_a_typed_fixture_has_playing_is_never_no_fixture(self):
        # compare holds the wanderers here, their round-5 card missing. A caller
        # that hands over these rounds as a draw read off the cards has them
        # compared, and their round 5 is still a card nobody exported.
        loose = dataclasses.replace(self.competition, draw_source=DRAW_FROM_CARDS)
        comparison, _ = self.checked("6", ("Parkview Wanderers", "11.1", None),
                                     competition=loose)
        check, = comparison.checks
        self.assertEqual(check.evidence[3:], ("R5 nothing read", "R6 no fixture"))
        self.assertEqual(check.nothing_read, ("5",))

    def test_a_typed_fixture_naming_nobody_leaves_its_round_nothing_read(self):
        """A name the competition does not have may be this team's, misspelt.

        Measured on this season with round 5 typed as the Rockets v "Parkview
        Wandrers" and the sheet after round 5 (/tmp/t7/scratch/SH/fix/probe.py
        P2TypoInTheDraw): the Wanderers' round 5 was ``no fixture``, and "It had
        round 5 off: check that week against the sheet's byes first", where the
        base said "Nothing was read for it in rounds 4 and 5". Beside it was only
        the card-missing warning for the Rockets v ``parkview-wandrers``: nothing
        checks a typed fixture's names against a typed team list. So a round whose
        fixtures name somebody the competition does not have says nothing about
        who had the week off. Rejected: taking the typed pairs as they stand, which
        records a typo as the team's night off.
        """
        typo = dataclasses.replace(self.competition.rounds["5"], fixtures=(
            ("riverside-rockets", "parkview-wandrers"),))
        comparison, report = self.checked(
            "6", ("Parkview Wanderers", "12.0", None),
            competition=self.with_rounds(**{"5": typo}))
        check, = comparison.checks
        self.assertEqual(check.evidence[3:], ("R5 nothing read", "R6 no fixture"))
        self.assertEqual((check.nothing_read, check.weeks_off), (("5",), ("6",)))
        self.assertIn(" Nothing was read for it in round 5.",
                      self.only_finding(report).message)

    def test_three_numbers_the_draw_passes_over_are_no_run_either(self):
        # The draw as rounds 1 to 3 and a round 7 of the rockets v the aces, whose
        # card is the round-6 one printed as round 7: rounds 4 to 6 are no week.
        seven = Round(number="7", fixtures=(("riverside-rockets", "hilltop-aces"),),
                      source=DRAW_DECLARED)
        matches = [dataclasses.replace(m, round="7") if m.round == "6" else m
                   for m in self.raw_matches()]
        comparison, _ = self.checked(
            "7", ("Hilltop Aces", "10.9", None), matches=matches,
            competition=self.with_rounds(**{"5": None, "6": None, "7": seven}))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2", "R7 2.6"))
        self.assertEqual((check.nothing_read, check.weeks_off), ((), ()))

    def test_a_final_typed_with_its_pairing_does_not_move_the_floor(self):
        # A grand final is the fourth week of the finals (round_sort_key), not
        # round 4: typed beside rounds 1 to 3 alone, it says nothing of round 4.
        final = Round(number="GF", fixtures=(("riverside-rockets", "hilltop-aces"),),
                      is_finals=True, source=DRAW_DECLARED)
        from_cards = dataclasses.replace(self.competition.rounds["6"],
                                         source=DRAW_FROM_CARDS)
        comparison, _ = self.checked(
            "6", ("Hilltop Aces", "10.9", None), competition=self.with_rounds(**{
                "5": None, "6": from_cards, "GF": final}))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 4.9", "R3 3.2",
                                          "R4 nothing read", "R5 nothing read",
                                          "R6 2.6"))


class TestARestTheByesTableStatesIsAByeUnderADerivedDrawToo(SheetChecks, SeasonCase):
    """A rest the club wrote down is a bye in the evidence, whatever the draw.

    Measured through ``build.py`` on the demo with no draw typed and the byes table
    resting every team in round 6 (/tmp/t7/scratch/SH/probe/p167.py wholebye):
    ``R6 nothing read``, and under ``unplayed = "average"`` "If it rested, write the
    rest in this competition's byes table", where it is written. The rounds a
    derived draw fills are filled as before. Rejected: keeping the bye to a declared
    draw, which leaves this advice about a table the club has already filled in.
    """

    @classmethod
    def extra_cards(cls):
        write_round_six(cls.data)

    @classmethod
    def make_config(cls):
        path = cls.root / "config.toml"
        path.write_text(
            '[competitions.tuesday-b]\nday = "Tuesday"\ndivision = "B"\n'
            'unplayed = "average"\nteams = ['
            + ", ".join(f'"{team[0].title()}"' for team in TEAMS.values())
            + ']\n\n[competitions.tuesday-b.byes]\n"Lakeside Spinners" = [4]\n',
            encoding="utf-8")
        return config_mod.load(path)

    def test_the_rest_is_a_bye_and_the_rounds_nobody_has_are_nothing_read(self):
        comparison, report = self.checked("6", ("Lakeside Spinners", "15.9", None))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 6.1", "R2 3.5", "R3 6.2", "R4 bye",
                                          "R5 nothing read", "R6 nothing read"))
        self.assertEqual((check.nothing_read, check.weeks_off), (("5", "6"), ("4",)))
        self.assertIn(" Nothing was read for it in rounds 5 and 6.",
                      self.only_finding(report).message)


class TestAWrongUnplayedIsOneFindingThatNamesTheLine(ByeSheetCase):
    """A sheet that credits a rest its average, under ``unplayed = "zero"``: one finding.

    Every team rests once in this season, so every team is out by its average, and
    three findings would send the club to three cards that are all right. The other
    convention is priced by the ladder's own ledger over the same matches: on paper
    18.9, 13.05 and 10.5 (BYE_AVERAGED_TOTALS). Decided on points alone, so ``played``
    typed as the sheet's P, the decided matches, does not stop the fit, and once the
    setting is changed that played agrees (below). Rejected: requiring played to
    agree under the other setting as well, which is a question nobody has measured
    an averaging association's sheet on.
    """

    UNPLAYED = "zero"
    AVERAGED = (("Sundown Larks", "18.9", 2), ("Tollgate Moths", "13.1", 2),
                ("Verge Newts", "10.5", 2))

    def test_one_grade_finding_names_the_setting_and_no_team_has_its_own(self):
        comparison, report = self.checked("3", *self.AVERAGED)
        self.assertTrue(comparison.convention_fits)
        issue = self.only_finding(report)
        self.assertEqual(issue.subject, "")
        self.assertEqual(issue.message, (
            "after round 3, 3 teams disagree with the association's sheet, and the "
            "points of every one of them match it under unplayed = \"average\": the "
            "association credits a week off with the team's own average. Set "
            "unplayed = \"average\" in this competition's section or under [rules]."))
        # Named as the team list was typed, which is in capitals here.
        self.assertEqual(issue.detail, "SUNDOWN LARKS 12.6 (sheet 18.9); "
                                       "TOLLGATE MOTHS 8.7 (sheet 13.1); "
                                       "VERGE NEWTS 7.0 (sheet 10.5)")

    def test_one_team_fixed_is_said_in_the_singular(self):
        _, report = self.checked("3", self.AVERAGED[0])
        self.assertTrue(self.only_finding(report).message.startswith(
            "after round 3, 1 team disagrees with the association's sheet, and its "
            "points match it under unplayed = \"average\":"))


class TestTheSettingTheGradeFindingNamesSettlesTheSheet(ByeSheetCase):
    """The averaged sheet above, once ``unplayed = "average"`` is set: nothing.

    Played typed as the sheet's P, two, where the average divides by three. Measured
    before this: the grade finding said to set ``"average"``, and under it the three
    teams were the every-team-out finding, sending the club to ``after_round`` and
    the rules with every points figure agreeing (review of 4.56). Rejected:
    deciding the fit on played as well, which would then offer no setting at all.
    """

    UNPLAYED = "average"

    def test_the_averaged_sheet_agrees_once_unplayed_is_average(self):
        comparison, report = self.checked(
            "3", *TestAWrongUnplayedIsOneFindingThatNamesTheLine.AVERAGED)
        self.assertEqual(len(comparison.agreeing), 3)
        self.assertEqual(self.sheet_issues(report), [])


class TestAZeroSheetUnderAverageIsOneFindingThatNamesTheLine(ByeSheetCase):
    """The mirror: a sheet that scores a rest as nothing, under ``"average"``.

    Played typed as the sheet's two, the decided matches, which agrees under either
    setting (an averaging sheet's played may be either figure, below). The fixed
    teams get no finding of their own: changing the setting is the fix.
    """

    UNPLAYED = "average"

    def test_one_grade_finding_names_zero(self):
        comparison, report = self.checked(
            "3", ("Sundown Larks", "12.6", 2), ("Tollgate Moths", "8.7", 2),
            ("Verge Newts", "7.0", 2))
        self.assertEqual(sorted(comparison.fixed), sorted(BYE_TEAMS))
        issue = self.only_finding(report)
        self.assertIn('match it under unplayed = "zero": the association scores a '
                      'week off as nothing. Set unplayed = "zero"', issue.message)


class TestAConventionThatFitsOneTeamAndBreaksAnotherIsNotOffered(ByeSheetCase):
    """The other setting is offered only when it puts nobody out who was right.

    Larks typed at their averaged 18.9, Moths at the 8.7 the cards give them now:
    ``"average"`` would fix one and break the other, so it is not the answer, and
    the Larks' own finding says so and names who it would put out. Rejected:
    offering the setting whenever it fixes somebody.
    """

    UNPLAYED = "zero"

    def test_no_grade_finding_and_the_fixed_team_is_told_who_it_would_break(self):
        comparison, report = self.checked("3", ("Sundown Larks", "18.9", None),
                                          ("Tollgate Moths", "8.7", None))
        self.assertFalse(comparison.convention_fits)
        self.assertEqual(comparison.broken, ("tollgate-moths",))
        issue = self.only_finding(report)
        self.assertEqual(issue.subject, "sundown-larks")
        self.assertEqual(issue.message, (
            "after round 3 the cards give SUNDOWN LARKS 12.6 points where the "
            "association's sheet has 18.9, 6.3 under it. With unplayed = "
            "\"average\" the cards give it 18.9, the sheet's figure, but that setting "
            "would put TOLLGATE MOTHS out, so check round 3 against the sheet's "
            "byes."))


class TestACardResidualBesideAWrongConventionIsStillItsOwnFinding(ByeSheetCase):
    """A wrong setting and one card out: two findings, one for each mistake.

    Larks and Moths typed at their averaged figures, Newts at 10.9 where both
    settings give 7.0 or 10.5. The grade finding covers the two the setting
    explains, and the Newts are left with their own: one mistake, one report.
    Rejected: letting the setting absorb every disagreeing team.
    """

    UNPLAYED = "zero"

    def test_the_setting_is_one_finding_and_the_residual_another(self):
        comparison, report = self.checked("3", ("Sundown Larks", "18.9", None),
                                          ("Tollgate Moths", "13.1", None),
                                          ("Verge Newts", "10.9", None))
        self.assertEqual(comparison.fixed, ("sundown-larks", "tollgate-moths"))
        grade, team = self.sheet_issues(report)
        self.assertEqual(grade.subject, "")
        # Three disagree, and the sheet sentence says 3 differ: the finding counts
        # the same three, and says how many of them the setting explains.
        self.assertIn("3 teams disagree with the association's sheet, and the points "
                      "of 2 of them match it under unplayed = \"average\"",
                      grade.message)
        self.assertEqual(team.subject, "verge-newts")
        self.assertEqual(team.message, (
            "after round 3 the cards give VERGE NEWTS 7.0 points where the "
            "association's sheet has 10.9, 3.9 under it. It had round 1 off: check "
            "that week against the sheet's byes first, then its matches."))
        self.assertEqual(team.detail, "R1 bye; R2 3.5; R3 3.5")


class TestEveryTeamOutIsOneFindingThatPointsAtTheRound(SheetSeasonCase):
    """Every team compared out, and no setting to explain it: the table is suspect.

    Round 2's figures typed under ``after_round = 3`` put all four out, and four
    findings would send the club to four cards. Rejected: firing it for a single
    team compared, which is one team out and has its own finding.
    """

    def test_a_round_behind_is_one_finding_naming_after_round(self):
        comparison, report = self.checked(
            "3", *TestTheSheetIsComparedOnlyAsFarAsItsRound.ROUND_TWO)
        self.assertTrue(comparison.every_team_out)
        issue = self.only_finding(report)
        self.assertEqual(issue.subject, "")
        self.assertEqual(issue.message, (
            "after round 3 every one of the 4 teams compared disagrees with the "
            "association's sheet, which points at the table or a rule rather than at "
            "a card. Check first that after_round is the round the sheet runs to, "
            "then the scoring and sets rules this dashboard is using."))
        self.assertEqual(issue.detail, (
            "Hilltop Aces 10.7, 3 played (sheet 7.5, 2 played); "
            "Lakeside Spinners 15.8, 3 played (sheet 9.6, 2 played); "
            "Parkview Wanderers 11.1, 3 played (sheet 8.4, 2 played); "
            "Riverside Rockets 19.0, 3 played (sheet 12.6, 2 played)"))

    def test_one_team_compared_and_out_is_that_teams_own_finding(self):
        comparison, report = self.checked("3", ("Hilltop Aces", "10.9", None))
        self.assertFalse(comparison.every_team_out)
        self.assertEqual(self.only_finding(report).subject, "hilltop-aces")


class TestAnAveragingSheetsPlayedMayBeCountedOrDecided(ByeSheetCase):
    """Under ``"average"`` a sheet's played agrees as the counted or the decided.

    A sheet whose average is its points over its played prints the divisor, three
    here with the bye; a sheet that prints the ladder's P prints two. Which one an
    averaging association prints has not been measured, and requiring the divisor
    sent a club round a loop: its sheet of P was out by one under ``"average"``,
    the grade finding said to set ``"average"``, and the setting kept the finding
    (review of 4.56). Under ``"zero"`` the two are one number. Rejected: the
    divisor alone, that loop; and P alone, which calls every divisor sheet wrong.
    """

    UNPLAYED = "average"

    def test_played_as_counted_agrees(self):
        comparison, report = self.checked("3", ("Sundown Larks", "", 3))
        self.assertEqual(len(comparison.agreeing), 1)
        self.assertEqual(self.sheet_issues(report), [])

    def test_played_as_decided_agrees(self):
        comparison, report = self.checked("3", ("Sundown Larks", "", 2))
        self.assertEqual(len(comparison.agreeing), 1)
        self.assertEqual(self.sheet_issues(report), [])

    def test_played_as_neither_is_out_and_says_both(self):
        _, report = self.checked("3", ("Sundown Larks", "", 4))
        self.assertEqual(self.only_finding(report).message, (
            "after round 3 the cards give SUNDOWN LARKS 3 matches counted (2 decided, "
            "1 credited its average) where the association's sheet has 4 played, 1 "
            "fewer. It had round 3 off: check that week against the sheet's byes "
            "first, then its matches."))

    def test_every_team_out_shows_the_played_each_agreed_with(self):
        # Two played agrees here, so it is not shown against a three it never
        # claimed: the points are the difference.
        _, report = self.checked("3", *((key, "1.0", 2) for key in (
            "Sundown Larks", "Tollgate Moths", "Verge Newts")))
        self.assertEqual(self.only_finding(report).detail, (
            "SUNDOWN LARKS 18.9, 2 played (sheet 1.0, 2 played); "
            "TOLLGATE MOTHS 13.1, 2 played (sheet 1.0, 2 played); "
            "VERGE NEWTS 10.5, 2 played (sheet 1.0, 2 played)"))


class TestPointsAreComparedToThePlacesTyped(ByeSheetCase):
    """``10.9`` is a claim to one place, ``"10.90"`` to two and ``19`` to none.

    Under ``"average"`` the Moths stand at 8.7 + 4.35, which the double holds as
    13.049999999999999: the stored figure is 13.05 and half up is 13.1, which is
    what a spreadsheet prints. Rejected: rounding the double, which gives 13.0, and
    comparing text, which calls ``013.1`` wrong.
    """

    UNPLAYED = "average"

    def agrees(self, key, typed):
        comparison, _ = self.checked("3", (key, typed, None))
        return bool(comparison.agreeing)

    def test_a_figure_agrees_at_the_places_it_was_typed_to(self):
        for key, typed in (("Sundown Larks", "18.9"), ("Sundown Larks", "18.90"),
                           ("Sundown Larks", "19"), ("Tollgate Moths", "13.1"),
                           ("Tollgate Moths", "13.05"), ("Tollgate Moths", "13"),
                           ("Tollgate Moths", "013.1")):
            with self.subTest(team=key, points=typed):
                self.assertTrue(self.agrees(key, typed))

    def test_a_figure_one_place_out_disagrees(self):
        for key, typed in (("Tollgate Moths", "13.0"), ("Tollgate Moths", "13.04"),
                           ("Sundown Larks", "18"), ("Sundown Larks", "18.8")):
            with self.subTest(team=key, points=typed):
                self.assertFalse(self.agrees(key, typed))

    def test_the_figure_is_rounded_as_the_points_column_rounds_it(self):
        self.assertEqual(sheet_mod.at_places(6.25, 1), "6.3")
        self.assertEqual(sheet_mod.at_places(6.225, 2), "6.23")
        self.assertEqual(sheet_mod.at_places(13.049999999999999, 1), "13.1")
        self.assertEqual(sheet_mod.places_of("19"), 0)
        self.assertEqual(sheet_mod.places_of("10.90"), 2)


class TestATeamWaitingOnACardIsHeldOnlyWhenTheDrawIsDeclared(SheetChecks, SeasonCase):
    """A declared fixture with no card: the team is not compared until the card comes.

    The missing card is already ``card-missing``, and a sheet finding for both of
    its teams would be the same mistake said three times. Rejected: comparing them
    and blaming the gap on a card the report already names.
    """

    @classmethod
    def extra_cards(cls):
        # Round 2's rockets v spinners, never exported. Round 3 is here, so the
        # season has gone past it.
        (cls.data / "Round2" / "RiversideRockets-LakesideSpinners.pdf").unlink()

    @classmethod
    def make_config(cls):
        fixtures = {}
        for rnd, home, away, _ in FIXTURES:
            fixtures.setdefault(rnd, []).append(
                f'["{TEAMS[home][0].title()}", "{TEAMS[away][0].title()}"]')
        draw = "".join(f'\n[[competitions.tuesday-b.rounds]]\nnumber = "{n}"\n'
                       f'fixtures = [{", ".join(pairs)}]\n'
                       for n, pairs in fixtures.items())
        path = cls.root / "config.toml"
        path.write_text(
            '[competitions.tuesday-b]\nday = "Tuesday"\ndivision = "B"\nteams = ['
            + ", ".join(f'"{name.title()}"' for name, _ in TEAMS.values()) + ']\n'
            + draw, encoding="utf-8")
        return config_mod.load(path)

    def test_both_teams_are_held_and_the_card_is_reported_once(self):
        comparison, report = self.checked("3", *AGREEING_SHEET)
        self.assertEqual(comparison.held, (("lakeside-spinners", "card missing"),
                                           ("riverside-rockets", "card missing")))
        self.assertEqual([c.slug for c in comparison.checks],
                         ["hilltop-aces", "parkview-wanderers"])
        self.assertEqual(self.sheet_issues(report), [])
        self.assertEqual(len([i for i in report.issues
                              if i.rule == "card-missing"]), 1)

    def test_a_team_not_typed_in_is_held_as_that_even_with_its_card_missing(self):
        comparison, _ = self.checked("3", ("Hilltop Aces", "10.7", 3))
        self.assertIn(("riverside-rockets", "not typed in"), comparison.held)

    def test_a_team_typed_in_and_waiting_on_a_card_is_held_for_the_card(self):
        """Card missing, although a misspelt line is nearest the same team.

        The team was typed in, so nothing about its line needs correcting: it
        waits on the card. Measured: holding it as nearly named first passed
        every test, since no case gave a typed team both. Rejected: nearly
        named, which points at a finding about another line.
        """
        comparison, report = self.checked("3", ("Riverside Rockets", "19.0", 3),
                                          ("Riverside Rocket", "19.0", 3))
        self.assertEqual(self.only_finding(report).message.split(". ")[-1],
                         "Did you mean Riverside Rockets?")
        self.assertIn(("riverside-rockets", "card missing"), comparison.held)

    def test_a_card_missing_after_the_sheets_round_holds_nobody(self):
        # Round 2's card is missing, and a sheet after round 1 does not need it.
        comparison, report = self.checked(
            "1", *((key, "", 1) for key, _, _ in AGREEING_SHEET))
        self.assertEqual(comparison.held, ())
        self.assertEqual(len(comparison.agreeing), 4)
        self.assertEqual(self.sheet_issues(report), [])


class TestASheetLineNamingNoTeamIsSaidOnce(SheetSeasonCase):
    """A key that names no team, or one already named, is one ``sheet-team``.

    Resolved where the teams are known, once, so a derived team list gets the same
    check a declared one does. Rejected: a config warning, which cannot know the
    teams the cards will give.
    """

    def test_a_misspelt_team_is_named_with_the_nearest_one(self):
        comparison, report = self.checked("3", ("Hilltop Acse", "10.7", 3))
        self.assertEqual(comparison.checks, ())
        issue = self.only_finding(report)
        self.assertEqual(issue.rule, "sheet-team")
        self.assertEqual(issue.subject, "Hilltop Acse")
        self.assertEqual(issue.message, (
            "\"Hilltop Acse\" in the sheet table names no team on this competition's "
            "cards, so that line was not compared. Did you mean Hilltop Aces?"))

    def test_a_name_nothing_is_near_has_no_suggestion(self):
        _, report = self.checked("3", ("Zebra Crossing", "1.0", None))
        self.assertTrue(self.only_finding(report).message.endswith(
            "so that line was not compared."))

    def test_a_name_with_no_letters_in_it_is_said_and_the_rest_is_compared(self):
        # "???" has nothing a team id can be made of, so the search for the nearest
        # name refuses it (ValueError); the line is still named, with no guess.
        comparison, report = self.checked("3", ("???", "1.0", None),
                                          ("Hilltop Aces", "10.7", 3))
        self.assertEqual([c.slug for c in comparison.checks], ["hilltop-aces"])
        self.assertEqual(self.only_finding(report).message, (
            "\"???\" in the sheet table names no team on this competition's cards, "
            "so that line was not compared."))

    def test_a_team_named_twice_is_compared_once_and_the_repeat_named(self):
        comparison, report = self.checked("3", ("Hilltop Aces", "10.7", 3),
                                          ("hilltop-aces", "12.0", 3))
        self.assertEqual([(c.slug, c.typed_points) for c in comparison.checks],
                         [("hilltop-aces", "10.7")])
        issue = self.only_finding(report)
        self.assertEqual(issue.subject, "hilltop-aces")
        self.assertEqual(issue.message, (
            "\"hilltop-aces\" in the sheet table is Hilltop Aces again, which "
            "\"Hilltop Aces\" already gave figures for, so only the first line was "
            "compared."))


class TestATeamALineNearlyNamesIsHeldAsNearlyNamed(SheetSeasonCase):
    """A team the finding suggests is held as nearly named, not as not typed in.

    Measured through ``build.py`` on the demo, a line keyed ``"Brindle Quols"``
    after round 5 (/tmp/t6/scratch/m4/psheet.py): the sentence said ``not
    compared: Brindle Bunyips, Brindle Kookaburras, Brindle Quolls, Brindle
    Rosellas and Brindle Wombats (not typed in)``, and the ``sheet-team`` finding
    under it ``Did you mean Brindle Quolls?``, about a line the club did type in.
    Rejected: leaving the team out of the sentence, which is the account of every
    team not compared, and would let "all agree" read as covering it.
    """

    def test_the_team_nearest_a_misspelling_is_held_as_nearly_named(self):
        comparison, _ = self.checked("3", ("Hilltop Acse", "10.7", 3),
                                     ("Riverside Rockets", "19.0", 3))
        self.assertEqual(comparison.held, (
            ("hilltop-aces", sheet_mod.NEARLY_NAMED),
            ("lakeside-spinners", sheet_mod.NOT_TYPED),
            ("parkview-wanderers", sheet_mod.NOT_TYPED)))

    def test_a_team_typed_rightly_beside_a_misspelling_of_it_is_compared(self):
        comparison, _ = self.checked("3", ("Hilltop Acse", "10.7", 3),
                                     ("Hilltop Aces", "10.7", 3))
        self.assertEqual([c.slug for c in comparison.checks], ["hilltop-aces"])
        self.assertNotIn("hilltop-aces", dict(comparison.held))

    def test_a_line_near_no_team_holds_nobody_as_nearly_named(self):
        comparison, _ = self.checked("3", ("Zebra Crossing", "1.0", None),
                                     ("???", "1.0", None))
        self.assertEqual({why for _, why in comparison.held}, {sheet_mod.NOT_TYPED})


class TestASheetLineAsNearTwoTeamsNamesBothAsACorrectionDoes(SheetSeasonCase):
    """A sheet key equally near two teams is asked about both, and neither is chosen.

    The sheet's check took the nearest team by :func:`~matchcentre.slugs.near_misses`
    with ``limit=1``, which on a tie is whichever sorts first. Measured through
    ``build.py`` on the unfixed tree, on the three-round season with the Rockets and
    the Aces printed as ``HILLTOP A`` and ``HILLTOP B`` and a sheet line keyed
    ``"Hilltop"`` (/tmp/t4/scratch/f4/probes/p_sheet_tie.py): exit 0 and ``Did you
    mean Hilltop a?``, with not a word about Hilltop B, which the line was as likely
    to be for. Since 4.51 the ``[player_names]`` check has answered a tie by naming
    both and choosing neither, in a sentence of its own.

    So both checks take :func:`~matchcentre.slugs.nearest`, and both say what it
    gave through :func:`~matchcentre.slugs.did_you_mean`, the one place the tie is
    worded. Rejected: a tie sentence of the sheet's own, a second wording of one
    answer. Three older suggestions that listed every name within two edits, in
    discovery's team note, F8 and the loader, were filed then, and take the nearest
    through it now (test_cli.TestABannerNobodyDeclaredIsAskedAboutTheTeamsNearestIt),
    which words a tie as ``a or b``, and three as ``a, b or c``.
    """

    @classmethod
    def make_config(cls):
        return config_mod.Config(
            path=cls.root / "config.toml",
            aliases={"riverside-rockets": "hilltop-a", "hilltop-aces": "hilltop-b"},
            team_names={"hilltop-a": "Hilltop A", "hilltop-b": "Hilltop B"})

    def test_both_teams_are_named_and_neither_is_chosen(self):
        comparison, report = self.checked("3", ("Hilltop", "19.0", 3),
                                          ("Lakeside Spinners", "15.8", 3))
        self.assertEqual([c.slug for c in comparison.checks], ["lakeside-spinners"])
        self.assertEqual(self.only_finding(report).message, (
            "\"Hilltop\" in the sheet table names no team on this competition's "
            "cards, so that line was not compared. Did you mean Hilltop A or "
            "Hilltop B? Key the line on the one it is for."))

    def test_both_teams_it_is_as_near_are_held_as_nearly_named(self):
        comparison, _ = self.checked("3", ("Hilltop", "19.0", 3),
                                     ("Lakeside Spinners", "15.8", 3))
        self.assertEqual(comparison.held, (
            ("hilltop-a", sheet_mod.NEARLY_NAMED),
            ("hilltop-b", sheet_mod.NEARLY_NAMED),
            ("parkview-wanderers", sheet_mod.NOT_TYPED)))

    def test_one_team_nearest_is_still_the_one_named(self):
        _, report = self.checked("3", ("Hilltop Bb", "10.7", 3))
        self.assertTrue(self.only_finding(report).message.endswith(
            "so that line was not compared. Did you mean Hilltop B?"))

    def test_the_tie_is_worded_once_for_both_checks(self):
        # Every string the shipped code writes the tie's advice in, by the function
        # it is in: one, and the one both checks reach through their suggestion.
        import ast
        advice = "Key the line on the one it is for."
        found = []
        for path in (ROOT / "build.py", *sorted((ROOT / "matchcentre").glob("*.py")),
                     *sorted((ROOT / "tools").glob("*.py"))):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                found += [f"{path.relative_to(ROOT).as_posix()}:{func.name}"
                          for node in ast.walk(func)
                          if isinstance(node, ast.Constant)
                          and isinstance(node.value, str) and advice in node.value]
        self.assertEqual(found, ["matchcentre/slugs.py:did_you_mean"])
        self.assertEqual(slugs.did_you_mean(["Hilltop A", "Hilltop B"]),
                         f" Did you mean Hilltop A or Hilltop B? {advice}")
        self.assertEqual(slugs.did_you_mean(["Hilltop A", "Hilltop B", "Hilltop C"]),
                         f" Did you mean Hilltop A, Hilltop B or Hilltop C? {advice}")
        self.assertEqual(slugs.did_you_mean(["Hilltop B"]), " Did you mean Hilltop B?")
        self.assertEqual(slugs.did_you_mean([]), "")

    def test_a_question_about_no_line_is_asked_without_the_advice(self):
        """A tie about a banner is asked without telling the club to key a line.

        Discovery's team note, the page's warning and F8 ask about a card's banner,
        where there is no line to key, and the loader and the sheet about a line the
        club typed. Measured before (/tmp/t6/scratch/m23/probe-before.txt), a
        banner as near two declared teams was asked ``Did you mean one of 'Hilltop
        A' (hilltop-a), 'Hilltop B' (hilltop-b)?`` by discovery and ``Did you mean
        hilltop-a, hilltop-b?`` by F8, neither with the advice. So the question
        takes ``keyed=False`` there, and keeps its one wording of the tie.

        Rejected: a second question function for banners, a second wording of the
        same tie.
        """
        self.assertEqual(slugs.did_you_mean(["Hilltop A", "Hilltop B"], keyed=False),
                         " Did you mean Hilltop A or Hilltop B?")
        self.assertEqual(slugs.did_you_mean(["Hilltop B"], keyed=False),
                         " Did you mean Hilltop B?")
        self.assertEqual(slugs.did_you_mean([], keyed=False), "")


class TestASheetLineNamesATeamTheWayTheByesTableDoes(SheetChecks, SeasonCase):
    """Every spelling the byes table takes, and no other, names a team here.

    One rule for every table keyed by team: ``Competition.team_by_name``, whose twin
    is the one a declared draw's names go through. Measured here under a club
    prefix and a renamed team, where the printed name, the id and the display name
    all differ. Rejected: a second path through the prefix and the aliases, which
    would let a sheet line take a spelling the byes line beside it refuses.
    """

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml",
                                 strip_team_prefix="Riverside",
                                 team_names={"hilltop-aces": "The Hill"})

    # Under the prefix the Rockets' id is "rockets" and their display name keeps
    # it, so the printed name resolves through the display name.
    KEYS = ("RIVERSIDE ROCKETS", "Riverside Rockets", "rockets", "HILLTOP ACES",
            "The Hill", "hilltop-aces", "Lakeside Spinners", "lakeside-spinners")

    def resolved(self, key):
        comparison, _ = self.checked("3", (key, "1.0", None))
        return comparison.checks[0].slug if comparison.checks else None

    def test_every_spelling_resolves_to_one_team(self):
        slugs = {key: self.resolved(key) for key in self.KEYS}
        self.assertNotIn(None, slugs.values(), slugs)
        self.assertEqual(slugs["The Hill"], slugs["hilltop-aces"])
        self.assertEqual(slugs["HILLTOP ACES"], slugs["hilltop-aces"])
        self.assertEqual(slugs["RIVERSIDE ROCKETS"], slugs["Riverside Rockets"])

    def test_the_rule_is_the_one_a_declared_draw_is_matched_by(self):
        for key in self.KEYS + ("Hilltop Acse", "Riverside Rocket"):
            with self.subTest(key=key):
                declared = discover_mod._declared_slug(key, self.competition.teams)
                self.assertEqual(self.resolved(key),
                                 declared if declared in self.competition.teams
                                 else None)


class TestTheSheetNeverMovesAFigure(SheetSeasonCase):
    """No figure, position, row or column comes from the sheet, agree or not.

    The cards are the only source of a result. An agreeing sheet leaves the season
    document exactly as it was, and a disagreeing one changes only its findings.
    Rejected: a ``meta.sheet`` field, which would move the page's bytes on every
    build that has a table and publish the association's figures where they agree.
    """

    def built(self, comparison):
        return export.build_season(
            self.raw_matches(), competition=self.competition, source_dir=self.data,
            generated="2026-09-10T12:00:00", club=self.discovery.club,
            cards=self.cards, rejected=self.rejected, sheet=comparison)

    def comparison(self, *lines):
        return sheet_mod.compare(self.raw_matches(), self.competition,
                                 typed_sheet("3", *lines))

    def test_an_agreeing_sheet_leaves_the_document_as_it_was(self):
        self.assertEqual(self.built(self.comparison(*AGREEING_SHEET)),
                         self.built(None))

    def test_a_disagreeing_sheet_changes_its_findings_and_nothing_else(self):
        plain = self.built(None)
        out = self.built(self.comparison(("Hilltop Aces", "10.9", 3)))
        self.assertEqual({k: v for k, v in out.items() if k != "validation"},
                         {k: v for k, v in plain.items() if k != "validation"})
        self.assertEqual(
            [w["rule"] for w in out["validation"]["warnings"]
             if w not in plain["validation"]["warnings"]], ["sheet-mismatch"])


class TestALadderThatCannotBeComputedIsNotComparedEither(SheetSeasonCase):
    """No ladder, no comparison: the missing ladder is already the finding.

    Reached the only way it can be, by making the ledger refuse, as the F10 sweep
    does: ``ladder_matches`` drops a level card a grade with no draws cannot price
    before a ledger is asked about it. Rejected: comparing against nothing, which
    would put every typed team out a second time over the same fault.

    4.127 looked for a real input that gets there and found none
    (/tmp/t6/scratch/m15/p127/sweep.py, an instrumented copy of the base): five
    seasons, among them level cards, forfeits, a washout, finals, a repeated card
    and byes, under the four schemes with draws on and off and either unplayed
    setting, 80 builds and 3,920 comparisons over seven rounds and seven sheets;
    the handler was entered 0 times, F10's 0 times, and no ``ScoringError`` was
    made. So the handler is kept as a defence and named one in ``compare``, and the
    last test holds that no scheme lets a folder's level card through to it.
    Rejected: deleting it, the one guard between a second reason to refuse and a
    build that dies over a table the club typed.
    """

    def test_a_ledger_that_refuses_gives_the_ladder_error_and_no_sheet_finding(self):
        refusal = scoring.ScoringError("games-bonus has no value for a draw")
        with mock.patch.object(stats, "points_ledger", side_effect=refusal), \
                mock.patch.object(validate, "points_ledger", side_effect=refusal):
            comparison, report = self.checked("3", *AGREEING_SHEET)
        self.assertEqual(comparison.status, sheet_mod.NO_LADDER)
        self.assertEqual(self.sheet_issues(report), [])
        self.assertEqual([i.rule for i in report.errors], ["F10"])

    def test_a_ledger_that_refuses_still_names_a_line_naming_nobody(self):
        refusal = scoring.ScoringError("games-bonus has no value for a draw")
        with mock.patch.object(stats, "points_ledger", side_effect=refusal):
            comparison, report = self.checked("3", ("Hilltop Acse", "10.7", 3),
                                              ("Riverside Rockets", "19.0", 3))
        self.assertEqual(comparison.status, sheet_mod.NO_LADDER)
        self.assertEqual([i.rule for i in self.sheet_issues(report)], ["sheet-team"])

    def test_a_competition_with_no_teams_is_not_compared(self):
        empty = dataclasses.replace(self.competition, teams={})
        comparison = sheet_mod.compare(self.raw_matches(), empty,
                                       typed_sheet("3", *AGREEING_SHEET))
        self.assertEqual(comparison.status, sheet_mod.NO_LADDER)
        self.assertEqual(comparison.unmatched, ())
        self.assertEqual(comparison.teams_total, 0)

    def test_no_scheme_lets_a_level_card_in_the_folder_reach_the_refusal(self):
        # Round 2's 19-19 under draws = false is the one card a grade's rules have no
        # price for; every scheme, either way unplayed is scored, compares past it.
        for scheme, unplayed, after in itertools.product(
                sorted(scoring.RULESETS), ("zero", "average"), ("1", "2", "3")):
            with self.subTest(scheme=scheme, unplayed=unplayed, after_round=after):
                config = config_mod.Config(path=self.root / "config.toml",
                                           scoring=scheme, draws=False,
                                           unplayed=unplayed)
                found = discover_mod.discover(self.cards, self.rejected,
                                              config=config, root=self.data)
                competition, = found.club.competitions
                matches = found.matches[competition.id]
                # The card the test is about, there and unpriceable, so a fixture that
                # stopped printing it could not leave this passing over nothing.
                self.assertEqual([m.round for m in matches if m.is_unscoreable_draw],
                                 ["2"])
                comparison, report = self.checked(
                    after, *AGREEING_SHEET, competition=competition, matches=matches)
                self.assertEqual(comparison.status, sheet_mod.COMPARED)
                self.assertEqual(len(comparison.checks), len(TEAMS))
                self.assertNotIn("F10", [i.rule for i in report.errors])



class TestACompetitionWithNoTeamsIsOneFindingAndNoLineIsAskedAbout(SheetSeasonCase):
    """A caller that hands over a competition with no teams is told so once.

    No build makes one. Measured on the demo through an instrumented copy of the
    base (/tmp/t7/scratch/SH/probe/p209.py): a config section with a sheet and no
    card filed is passed over, ``teams = []`` has its list filled from the cards
    (1 of 6 teams compared), and a list of one team no card names compares none of
    the 1 team; compare's no-teams branch was entered 0 times there, and 0 times in
    4.127's sweep of 80 builds rerun. But ``compare`` and ``validate`` are public.
    Measured with the branch taken out (/tmp/t7/scratch/SH/m209probe.py): the
    comparison was ``compared``, and each of the five lines was a ``sheet-team``
    finding beside ``no-teams``, one mistake said six times. Rejected: deleting
    the branch as a state nothing produces, since the API produces it.
    """

    def test_no_teams_is_the_finding_and_the_sheet_has_none(self):
        empty = dataclasses.replace(self.competition, teams={})
        comparison, report = self.checked("3", *AGREEING_SHEET,
                                          ("Nobody Much", "5.0", 1),
                                          competition=empty)
        self.assertEqual((comparison.status, comparison.unmatched),
                         (sheet_mod.NO_LADDER, ()))
        self.assertEqual([i.rule for i in report.issues].count("no-teams"), 1)
        self.assertEqual(self.sheet_issues(report), [])


class TestALevelCardAGradeCannotPriceIsItsOwnErrorAndTheLadderIsStillComputed(
        SeasonCase):
    """The ladder's F10 is a defence: no folder makes the ledger refuse.

    ``_check_ladder`` turns a ``ScoringError`` into F10, "the ladder cannot be
    computed", and its comment named a level match under a grade with no draws as
    the cause. That card never gets there: ``stats.ladder_matches`` drops it and
    ``Match.points_for`` fails closed on it. 4.127's sweep, rerun on an
    instrumented copy of the base (/tmp/t7/scratch/SH/sweep/sweep.py), made 80
    builds and 3,920 comparisons, entered F10's handler 0 times and made no
    ``ScoringError``. So the comment now says it is a defence, and this holds that
    a folder's level card is ``draw-not-allowed`` and nothing more, with no sheet
    typed. Rejected: searching on for an input that reaches it.
    """

    def test_every_scheme_either_unplayed_reports_the_card_and_no_f10(self):
        for scheme, unplayed in itertools.product(sorted(scoring.RULESETS),
                                                  ("zero", "average")):
            with self.subTest(scheme=scheme, unplayed=unplayed):
                config = config_mod.Config(path=self.root / "config.toml",
                                           scoring=scheme, draws=False,
                                           unplayed=unplayed)
                found = discover_mod.discover(self.cards, self.rejected,
                                              config=config, root=self.data)
                competition, = found.club.competitions
                matches = found.matches[competition.id]
                self.assertEqual([m.round for m in matches if m.is_unscoreable_draw],
                                 ["2"])
                report = validate.validate(matches, competition, club=found.club,
                                           cards=self.cards, rejected=self.rejected)
                self.assertEqual([i.rule for i in report.errors],
                                 ["draw-not-allowed"])


class TestACardTheLadderSetAsideIsNotCalledUnread(SheetChecks, SeasonCase):
    """A card the ladder refused was read, and the evidence says so.

    Under ``draws = false`` round 2's 19-19 is a result nobody recorded, and the
    ladder leaves it out (``priceable``). Measured while building this: the evidence
    said "R2 nothing read" and the advice "that card has not been exported" about a
    card sitting in the folder, with its own finding already. Rejected: counting the
    set-aside card as read-and-played, which is the figure the ladder refused.
    """

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml", draws=False)

    def test_the_round_is_a_card_set_aside_and_the_advice_names_it(self):
        # 2.6 + 3.2: the level night out, as TestAGradeWhoseNightsCannotEndLevel has.
        comparison, report = self.checked("3", ("Hilltop Aces", "10.7", None))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2.6", "R2 card set aside", "R3 3.2"))
        self.assertEqual(check.nothing_read, ())
        self.assertEqual(check.set_aside, ("2",))
        self.assertEqual(self.only_finding(report).message, (
            "after round 3 the cards give Hilltop Aces 5.8 points where the "
            "association's sheet has 10.7, 4.9 under it." + SET_ASIDE_ONE % "2"))


class ConflictingCardCase(SheetChecks, SeasonCase):
    """The clean season with a second card for round 1's rockets v aces.

    Every set the other way round and a match id of its own, so the two disagree
    about who won and the ladder counts neither (``stats.duplicates``, F9).
    """

    @classmethod
    def extra_cards(cls):
        rnd, home, away, scores = FIXTURES[0]
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on_court = squad_for(home, home_players)
        away_rows, away_on_court = squad_for(away, away_players)
        cw.write_card_file(
            cls.data / "Round1" / "RiversideRockets-HilltopAces-2.pdf", cw.CardSpec(
                date=DATES[rnd], round_label=rnd, division="B", court="07",
                match_id="2009001", home_name=home_name, away_name=away_name,
                rows=rows_for(tuple((a, h) for h, a in scores), home_on_court,
                              away_on_court),
                home_roster=home_rows, away_roster=away_rows))


class TestTwoCardsSetAsideAreCountedAndTheirRoundIsNamed(ConflictingCardCase):
    """Every card set aside in a round is counted, and the advice names the round.

    Measured through ``build.py`` on the demo, a second, reversed card for round
    4's quolls v magpies and a sheet line after round 5
    (/tmp/t6/scratch/m4/setup101.py): the detail read ``R1 6.3; R2 3.4; R3 6.1; R4
    card set aside; R5 3.4``, one card for two, and the advice ``It had no week
    off, so the difference is in one of its matches``, beside an F9 error that
    said which round the difference was in. Rejected: saying each card's own
    figures, which are the two results the ladder refused to choose between.
    """

    def test_both_cards_are_counted_and_the_advice_names_the_round(self):
        comparison, report = self.checked("3", ("Hilltop Aces", "10.7", None))
        check, = comparison.checks
        self.assertEqual(check.evidence, ("R1 2 cards set aside", "R2 4.9", "R3 3.2"))
        self.assertEqual(check.set_aside, ("1",))
        self.assertEqual(self.only_finding(report).message, (
            "after round 3 the cards give Hilltop Aces 8.1 points where the "
            "association's sheet has 10.7, 2.6 under it." + SET_ASIDE_ONE % "1"))

    def test_a_team_with_a_round_set_aside_and_a_week_off_is_sent_to_the_card(self):
        # A rest the ladder counted in round 2 and round 1 set aside: the card
        # has a finding of its own, and is the first thing to look at.
        rest = dataclasses.replace(
            next(m for m in self.raw_matches()
                 if m.round == "2" and m.involves("hilltop-aces")),
            status=MatchStatus.CANCELLED)
        matches = [rest if m.round == "2" and m.involves("hilltop-aces") else m
                   for m in self.raw_matches()]
        comparison, report = self.checked("3", ("Hilltop Aces", "10.7", None),
                                          matches=matches)
        check, = comparison.checks
        self.assertEqual(check.weeks_off, ("2",))
        self.assertTrue(self.only_finding(report).message.endswith(
            SET_ASIDE_ONE % "1"))

    def test_a_setting_that_gives_the_sheets_figure_is_still_said_first(self):
        # Built by hand: the other unplayed gives the sheet's 9.0 with round 1
        # counted for nothing under either, so the setting is the difference.
        check = sheet_mod.TeamCheck(
            slug="hilltop-aces", key="Hilltop Aces", typed_points="9.0",
            typed_played=None, points_now=8.1, counted_now=2, decided=1,
            points_alt=9.0, evidence=("R1 2 cards set aside", "R2 not played",
                                      "R3 3.2"),
            nothing_read=(), weeks_off=("2",), set_aside=("1",))
        comparison = sheet_mod.Comparison(
            after_round="3", status=sheet_mod.COMPARED, teams_total=4,
            checks=(check,), fixed=("hilltop-aces",),
            broken=("lakeside-spinners",))
        report = validate.validate(self.raw_matches(), self.competition,
                                   sheet=comparison)
        self.assertTrue(self.only_finding(report).message.endswith(
            " but that setting would put Lakeside Spinners out, so check round 2 "
            "against the sheet's byes."))


class TestRoundsSetAsideAreThoseRounds(ConflictingCardCase):
    """Two rounds with nothing counted are named together, in the plural.

    Round 1's two cards disagree, and under ``draws = false`` round 2's level
    card cannot be priced either.
    """

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml", draws=False)

    def test_both_rounds_are_named_and_so_are_their_findings(self):
        comparison, report = self.checked("3", ("Hilltop Aces", "10.7", None))
        check, = comparison.checks
        self.assertEqual(check.evidence,
                         ("R1 2 cards set aside", "R2 card set aside", "R3 3.2"))
        self.assertEqual(check.set_aside, ("1", "2"))
        self.assertTrue(self.only_finding(report).message.endswith(
            " Nothing from rounds 1 and 2 was counted for it, and the findings about "
            "those rounds say why: deal with them first, then compare its matches "
            "against the sheet's."))


class NothingReadCase(SheetChecks, SeasonCase):
    """The clean season with round 3's aces v spinners never exported, draw derived."""

    @classmethod
    def extra_cards(cls):
        (cls.data / "Round3" / "HilltopAces-LakesideSpinners.pdf").unlink()


class TestAMissingCardWithoutADeclaredDrawIsComparedAndNamesItsRound(NothingReadCase):
    """With no draw typed in, a missing card and a night off look the same.

    So nobody is held: both teams of the missing fixture are compared, and each
    finding names the round nothing was read for, which is where the card is.
    Rejected: holding every team with a round short, which would stop the check
    for a team that rested, and read a derived draw as a schedule.
    """

    def test_both_teams_are_out_and_named_to_the_round(self):
        comparison, report = self.checked("3", *AGREEING_SHEET)
        self.assertEqual(comparison.held, ())
        found = {issue.subject: issue for issue in self.sheet_issues(report)}
        self.assertEqual(sorted(found), ["hilltop-aces", "lakeside-spinners"])
        self.assertEqual(found["hilltop-aces"].detail,
                         "R1 2.6; R2 4.9; R3 nothing read")
        self.assertEqual(found["hilltop-aces"].message, (
            "after round 3 the cards give Hilltop Aces 7.5 points where the "
            "association's sheet has 10.7, 3.2 under it, and 2 matches played where "
            "it has 3, 1 fewer." + NOTHING_READ_ZERO))


class TestTheAdviceForARoundWithNothingReadFollowsTheWayARestIsScored(NothingReadCase):
    """Under ``"average"`` a rest written down is what credits it; under ``"zero"`` not.

    Measured: under the shipped ``zero`` a declared bye changes neither the points
    nor the matches counted, so "add the bye" could never close a gap there, and
    the zero sentence (above) says so. Here the other sentence, which is true.
    Rejected: one sentence for both, which is false under one of them.
    """

    @classmethod
    def make_config(cls):
        return config_mod.Config(path=cls.root / "config.toml", unplayed="average")

    def test_the_advice_is_to_write_the_rest_in_the_byes_table(self):
        _, report = self.checked("3", ("Hilltop Aces", "10.7", None))
        self.assertTrue(self.only_finding(report).message.endswith(
            " Nothing was read for it in round 3. If it played that week, that card "
            "has not been exported. If it rested, write the rest in this "
            "competition's byes table: under unplayed = \"average\" that is what "
            "credits it its average."))

    def test_two_rounds_with_nothing_read_are_those_weeks_here_too(self):
        without = [m for m in self.raw_matches()
                   if not (m.round == "2" and m.involves("hilltop-aces"))]
        _, report = self.checked("3", ("Hilltop Aces", "10.7", None),
                                 matches=without)
        self.assertTrue(self.only_finding(report).message.endswith(
            " Nothing was read for it in rounds 2 and 3. If it played those weeks, "
            "those cards have not been exported. If it rested, write the rest in "
            "this competition's byes table: under unplayed = \"average\" that is "
            "what credits it its average."))


class TestRoundsWithNothingReadAreThoseWeeksAndThoseCards(SheetSeasonCase):
    """The nothing-read advice is in the number of weeks it names, as its list is.

    Measured through ``build.py`` on the demo, the Quolls' round-2 and round-3
    cards deleted and a sheet line after round 5 (/tmp/t6/scratch/m4/setup110.py):
    ``Nothing was read for it in rounds 2 and 3. If it played that week, that card
    has not been exported.`` The list came from ``rounds_listed``, which counts,
    and the sentence after it was fixed in the singular, while the week-off
    sentence two branches down counted for itself. Now all three ask
    :func:`~matchcentre.sheet.by_weeks`, which counts a run by the weeks in it.
    Rejected: a count of the entries, which calls ``rounds 4 to 6`` one week.
    """

    def without_aces(self, *rounds):
        return [m for m in self.raw_matches()
                if not (m.round in rounds and m.involves("hilltop-aces"))]

    def test_two_rounds_are_those_weeks_and_those_cards(self):
        _, report = self.checked("3", ("Hilltop Aces", "10.7", None),
                                 matches=self.without_aces("2", "3"))
        self.assertEqual(self.only_finding(report).message, (
            "after round 3 the cards give Hilltop Aces 2.6 points where the "
            "association's sheet has 10.7, 8.1 under it. Nothing was read for it in "
            "rounds 2 and 3. If it played those weeks, those cards have not been "
            "exported. If it rested, a rest scores nothing under unplayed = "
            "\"zero\", so the rest is not the difference unless the association "
            "credits one: that is unplayed = \"average\", with the rest written in "
            "this competition's byes table."))

    def test_a_run_alone_is_those_weeks(self):
        check = sheet_mod.TeamCheck(
            slug="hilltop-aces", key="Hilltop Aces", typed_points="10.7",
            typed_played=None, points_now=2.6, counted_now=1, decided=1,
            points_alt=2.6, evidence=("R1 2.6", "R2-R4 nothing read"),
            nothing_read=(sheet_mod.Run(2, 4),), weeks_off=())
        comparison = sheet_mod.Comparison(after_round="4", status=sheet_mod.COMPARED,
                                          teams_total=4, checks=(check,))
        report = validate.validate(self.raw_matches(), self.competition,
                                   sheet=comparison)
        self.assertIn(" Nothing was read for it in rounds 2 to 4. If it played those "
                      "weeks, those cards have not been exported.",
                      self.only_finding(report).message)


class TestTheEvidenceIsWhatTheLadderCredited(ByeSheetCase):
    """Each figure in the detail is the ladder's own for that round, a rest included.

    Under ``"average"`` the Larks' round 3 rest is credited their 6.3, and the total
    is 18.9. Rejected: the evidence from each card's own points, which would print
    a rest as worth nothing beside a total that paid it.
    """

    UNPLAYED = "average"

    def test_the_rest_is_shown_with_what_it_was_credited(self):
        _, report = self.checked("3", ("Sundown Larks", "18.0", None))
        self.assertEqual(self.only_finding(report).detail,
                         "R1 6.4; R2 6.2; R3 bye, credited 6.3")

    def test_every_figure_is_the_round_points_entry_for_its_round(self):
        comparison, _ = self.checked("3", ("Sundown Larks", "18.0", None))
        ladder = stats.ladder_matches(self.raw_matches(), self.competition)
        paid = {entry.round: f"{stats.round_display(entry.points):.1f}"
                for entry in stats.round_points(ladder, "sundown-larks")}
        check, = comparison.checks
        self.assertEqual([entry.rsplit(" ", 1)[-1] for entry in check.evidence],
                         [paid[r] for r in ("1", "2", "3")])


class TestAWashedOutNightIsNotPlayedNotABye(SheetChecks, SeasonCase):
    """A washout is a card, and the evidence names it as a night not played.

    Rejected: calling it a bye, which sends the club to the byes table for a
    fixture that was on the draw and rained off.
    """

    @classmethod
    def extra_cards(cls):
        home_name, home_players = TEAMS["riverside-rockets"]
        away_name, away_players = TEAMS["hilltop-aces"]
        folder = cls.data / "Round4"
        folder.mkdir(parents=True, exist_ok=True)
        roster = lambda players: tuple(  # noqa: E731
            cw.RosterRow(name=n, singles=2.5, doubles=2.5, captain=(i == 0))
            for i, n in enumerate(players))
        cw.write_card_file(folder / "washout.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B", court="09",
            match_id="4000001", home_name=home_name, away_name=away_name,
            rows=rows_for(((6, 2), (6, 1), (0, 0), (0, 0)), home_players,
                          away_players),
            cancelled=True, verb="VS", winner=cw.NO_TICK,
            home_roster=roster(home_players), away_roster=roster(away_players)))

    def test_the_round_is_not_played_and_is_the_week_off(self):
        _, report = self.checked("4", ("Riverside Rockets", "19.5", None))
        issue = self.only_finding(report)
        self.assertEqual(issue.detail, "R1 6.4; R2 6.2; R3 6.4; R4 not played")
        self.assertTrue(issue.message.endswith(
            " It had round 4 off: check that week against the sheet's byes first, "
            "then its matches."))


class TestTwoWeeksOffAreThoseWeeks(SheetSeasonCase):
    """The row-3 sentence names every week off, and speaks of them in the plural.

    No fixture season rests a team twice, so the comparison is built by hand and
    handed to the findings as a build would hand it.
    """

    def test_two_weeks_off_are_named_as_those_weeks(self):
        check = sheet_mod.TeamCheck(
            slug="hilltop-aces", key="Hilltop Aces", typed_points="9.0",
            typed_played=None, points_now=7.5, counted_now=2, decided=2,
            points_alt=7.5, evidence=("R1 bye", "R2 4.9", "R3 bye"),
            nothing_read=(), weeks_off=("1", "3"))
        comparison = sheet_mod.Comparison(after_round="3", status=sheet_mod.COMPARED,
                                          teams_total=4, checks=(check,))
        report = validate.validate(self.raw_matches(), self.competition,
                                   sheet=comparison)
        self.assertTrue(self.only_finding(report).message.endswith(
            " It had rounds 1 and 3 off: check those weeks against the sheet's byes "
            "first, then its matches."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
