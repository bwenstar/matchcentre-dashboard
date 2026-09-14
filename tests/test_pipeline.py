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

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matchcentre import (cardwriter as cw, config as config_mod,      # noqa: E402
                         discover as discover_mod, export, scorecard,
                         stats, validate)
from matchcentre.model import MatchStatus                             # noqa: E402

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


def write_season_cards(data_dir: Path) -> list[Path]:
    """One PDF per fixture, in per-round folders, as a club would export them."""
    written = []
    for index, (rnd, home, away, scores) in enumerate(FIXTURES, start=1):
        folder = data_dir / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        spec = cw.CardSpec(
            date=DATES[rnd], round_label=rnd, division="B",
            court=f"{index:02d}", match_id=f"200{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_players, away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)),
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
        cls.report = validate.validate(cls.matches, cls.competition,
                                       club=cls.discovery.club,
                                       cards=cls.cards, rejected=cls.rejected)
        cls.season = export.build_season(
            cls.matches, competition=cls.competition, source_dir=cls.data,
            generated="2026-09-10T12:00:00", club=cls.discovery.club,
            cards=cls.cards, rejected=cls.rejected)

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
        self.assertEqual(len(self.competition.regular_rounds), 3)


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


# --- one player, two spellings ------------------------------------------------

# The fourth round below exists so that "has played once or twice" means
# something: after three rounds every player has played at most three, and a
# player with one appearance is unremarkable.
ROUND_FOUR = (("riverside-rockets", "hilltop-aces",
               ((6, 2), (6, 3), (6, 4), (6, 1))),
              ("lakeside-spinners", "parkview-wanderers",
               ((6, 2), (6, 3), (2, 6), (6, 4))))


def build_season(root: Path, *, misspell: str = "",
                 aliases: dict[str, str] | None = None) -> dict:
    """The season document for the four-round competition.

    ``misspell`` replaces the second Rockets player's name **in the set rows of
    round four only**, leaving every roster block spelling it correctly -- which
    is what happens when whoever fills in a card writes a name from memory.
    ``aliases`` is the config remedy for it.
    """
    data = root / "data"
    write_season_cards(data)
    folder = data / "Round4"
    folder.mkdir(parents=True, exist_ok=True)
    for index, (home, away, scores) in enumerate(ROUND_FOUR, start=7):
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        on_court = list(home_players)
        if misspell and home == "riverside-rockets":
            on_court[1] = misspell
        spec = cw.CardSpec(
            date="2026-09-08", round_label="4", division="B",
            court=f"{index:02d}", match_id=f"200{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, tuple(on_court), away_players),
            home_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(home_players)),
            away_roster=tuple(cw.RosterRow(name=n, singles=2.5, doubles=2.5,
                                           captain=(i == 0))
                              for i, n in enumerate(away_players)))
        cw.write_card_file(folder / f"r4-{index}.pdf", spec)

    cards, rejected = scorecard.read_tree(data)
    config = config_mod.Config(path=root / "config.toml",
                               aliases=dict(aliases or {}))
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
    return season


class TestOnePlayerRecordedTwice(unittest.TestCase):
    """A name spelled two ways, which splits one player's record in half.

    Both halves are internally consistent, every set was still played and
    counted, and the team's points, games and ladder position are all exactly
    right -- so there is nothing here that can be called an error, and nothing
    that changes a figure a club looks at.

    Two things say so anyway, for two different readers. The build report warns
    whoever ran ``build.py``; the dashboard's "players to check" list is for
    whoever only ever opens the page, which at most clubs is nearly everybody.
    These tests cover the report, the facts the list rests on, and the remedy
    both of them recommend.

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
        self.assertEqual(self.split["teams"], self.control["teams"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
