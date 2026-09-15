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
        folder = data_dir / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        home_name, home_players = BYE_TEAMS[home]
        away_name, away_players = BYE_TEAMS[away]
        cw.write_card_file(folder / f"r{rnd}.pdf", cw.CardSpec(
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
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / "data"
        write_bye_season_cards(cls.data)
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

    def test_the_bye_counts_as_a_match_played(self):
        for slug in BYE_TEAMS:
            with self.subTest(team=slug):
                self.assertEqual(self.team[slug]["counted"], 3)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
