"""The seven modules everything else is built on.

These are the vocabulary of the project — identity, match shape, scoring, and the
config that supplies the first two — so a mistake here is a mistake everywhere,
and it is the kind that produces a plausible wrong number rather than a crash.

Every assertion about a *rule* rather than a mechanism names the failure it
guards. Several of them are failures that were measured on the predecessor
project, and those are marked; the rest are failures this design makes possible
for the first time by having the concept at all.
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matchcentre import competition as comp_mod
from matchcentre import config as config_mod
from matchcentre import format as fmt_mod
from matchcentre import scoring, slugs
from matchcentre.competition import (Competition, Player, Provenance, Round, Team,
                                     competition_id, is_finals_label,
                                     merge_provenance, round_sort_key)
from matchcentre.model import (NO_OPPONENT, Match, MatchStatus, Outcome, Rating,
                               SetResult, SetStatus, other_side)

FIRST_TO_6 = fmt_mod.set_rule("first-to-6")
DSSD = fmt_mod.match_format("doubles-singles-doubles")
GAMES_BONUS = scoring.ruleset("games-bonus")


def make_sets(scores, fmt=DSSD, rule=FIRST_TO_6, status=SetStatus.PLAYED):
    """``[(6, 1), (2, 6), ...]`` -> set results in the format's slot order."""
    return tuple(
        SetResult(slot=fmt.slots[i], home_games=home, away_games=away,
                  home_players=(f"home-{i}",), away_players=(f"away-{i}",),
                  status=status, rule=rule)
        for i, (home, away) in enumerate(scores)
    )


def make_match(scores=((6, 1), (6, 2), (1, 6), (6, 0)), *, home="rockets",
               away="iguanas", rnd="1", fmt=DSSD, rules=GAMES_BONUS,
               status=MatchStatus.PLAYED, forfeited_by=None):
    return Match(competition="test", round=rnd, home=home, away=away, fmt=fmt,
                 rules=rules, sets=make_sets(scores, fmt=fmt), status=status,
                 forfeited_by=forfeited_by)


# --------------------------------------------------------------------------
# slugs
# --------------------------------------------------------------------------


class TestSlugs(unittest.TestCase):

    def test_accents_and_punctuation_fold_away(self):
        self.assertEqual(slugs.slugify("Guillaume Évrard"), "guillaume-evrard")
        self.assertEqual(slugs.slugify("O'Brien-Smith"), "o-brien-smith")
        self.assertEqual(slugs.slugify("  Spaced   Out  "), "spaced-out")

    def test_unsluggable_text_raises_rather_than_returning_empty(self):
        # An empty identifier would merge two different subjects into one key,
        # which is a wrong dashboard rather than a crash.
        for bad in ("", "   ", "---", "!!!"):
            with self.assertRaises(ValueError):
                slugs.slugify(bad)

    def test_prefix_strip_only_removes_a_whole_word(self):
        self.assertEqual(slugs.team_slug("RTC ROCKETS", strip_prefix="RTC"),
                         "rockets")
        self.assertEqual(slugs.team_slug("RTC-Rockets", strip_prefix="RTC"),
                         "rockets")
        # "RTCU" is not the word "RTC", so it survives intact.
        self.assertEqual(slugs.team_slug("RTCU Raiders", strip_prefix="RTC"),
                         "rtcu-raiders")

    def test_prefix_strip_never_erases_the_whole_name(self):
        # A team actually called by the prefix keeps its name instead of raising.
        self.assertEqual(slugs.team_slug("RTC", strip_prefix="RTC"), "rtc")

    def test_alias_chain_is_followed_to_its_end(self):
        self.assertEqual(slugs.apply_alias("a", {"a": "b", "b": "c"}), "c")

    def test_alias_cycle_is_survived_not_hung(self):
        # Clubs write aliases by hand and a -> b -> a is a typo, not a reason to
        # loop forever.
        self.assertEqual(slugs.apply_alias("a", {"a": "b", "b": "a"}), "b")

    def test_display_name_unshouts_without_mangling_abbreviations(self):
        self.assertEqual(slugs.display_name("RTC ROCKETS"), "RTC Rockets")
        self.assertEqual(slugs.display_name("Ana Fernandez"), "Ana Fernandez")

    def test_display_name_keeps_minor_words_lower(self):
        # The acronym rule fired before the minor-word rule in a first draft and
        # produced "Kings OF THE Court": "OF" is short enough to look like a club
        # abbreviation.
        self.assertEqual(slugs.display_name("KINGS OF THE COURT"),
                         "Kings of the Court")

    def test_display_name_keeps_a_leading_minor_word_capitalised(self):
        # Ordinary title case: lower in the middle, capitalised at the front.
        self.assertEqual(slugs.display_name("THE ACES"), "The Aces")
        self.assertEqual(slugs.display_name("ACES OF THE RIVER"),
                         "Aces of the River")

    def test_a_four_letter_acronym_is_a_known_ambiguity_not_a_bug(self):
        # "ACES" is a team and "WDTA" is an association: same shape, no rule
        # separates them. The cut is at three letters, so a four-letter acronym
        # comes out title-cased and the club corrects it in [team_names]. Asserted
        # so the behaviour is a decision on record rather than an accident.
        self.assertEqual(slugs.display_name("RTC"), "RTC")
        self.assertEqual(slugs.display_name("ACES"), "Aces")
        self.assertEqual(slugs.display_name("WDTA"), "Wdta")

    def test_unslug_is_the_last_resort_display_name(self):
        self.assertEqual(slugs.unslug("riverside-rockets"), "Riverside Rockets")

    def test_near_misses_finds_a_typo_and_ignores_a_different_word(self):
        known = ["iguanas", "rockets", "wanderers"]
        self.assertEqual(slugs.near_misses("igunas", known), ["iguanas"])
        self.assertEqual(slugs.near_misses("rockets", known), [])
        self.assertEqual(slugs.near_misses("zebras", known), [])


# --------------------------------------------------------------------------
# format
# --------------------------------------------------------------------------


class TestSetRules(unittest.TestCase):
    """The completion test, per rule, for the scores that used to break it."""

    def test_first_to_six_completes_at_six(self):
        self.assertTrue(fmt_mod.completed(6, 1, FIRST_TO_6))
        self.assertTrue(fmt_mod.completed(6, 5, FIRST_TO_6))
        self.assertFalse(fmt_mod.completed(5, 3, FIRST_TO_6))

    def test_a_level_score_is_never_complete(self):
        # Without an explicit equality guard, 6-6 satisfied "reached the cap" and
        # was scored as a finished set with no winner.
        for rule_name in fmt_mod.SET_RULES:
            rule = fmt_mod.set_rule(rule_name)
            score = rule.games_to_win
            self.assertFalse(fmt_mod.completed(score, score, rule),
                             f"{rule_name}: {score}-{score} read as complete")

    def test_fast4_four_two_is_a_finished_set(self):
        # Measured on the predecessor: its hardcoded ">= 6" read every FAST4 match
        # as unplayed, and the ladder came out blank.
        self.assertTrue(fmt_mod.completed(4, 2, fmt_mod.set_rule("fast4")))

    def test_pro_eight_six_three_is_mid_set(self):
        # The same ">= 6" scored this as the final score of a set still in play.
        self.assertFalse(fmt_mod.completed(6, 3, fmt_mod.set_rule("pro-8")))
        self.assertTrue(fmt_mod.completed(8, 3, fmt_mod.set_rule("pro-8")))

    def test_advantage_sets_need_two_clear_games(self):
        rule = fmt_mod.set_rule("advantage-6")
        self.assertFalse(fmt_mod.completed(6, 5, rule))
        self.assertTrue(fmt_mod.completed(7, 5, rule))
        self.assertTrue(fmt_mod.completed(24, 22, rule))

    def test_tiebreak_rules_finish_at_the_cap(self):
        rule = fmt_mod.set_rule("six-game-tiebreak")
        self.assertFalse(fmt_mod.completed(6, 5, rule))
        self.assertTrue(fmt_mod.completed(7, 6, rule))
        self.assertTrue(fmt_mod.completed(6, 4, rule))

    def test_plausible_rejects_scores_the_rule_cannot_reach(self):
        # An impossible score is the signature of a misread card, which is a
        # different thing from an unfinished set and needs a different response.
        self.assertFalse(fmt_mod.plausible(6, 6, FIRST_TO_6))
        self.assertFalse(fmt_mod.plausible(7, 2, FIRST_TO_6))
        self.assertFalse(fmt_mod.plausible(4, 4, fmt_mod.set_rule("fast4")))
        self.assertFalse(fmt_mod.plausible(-1, 3, FIRST_TO_6))

    def test_plausible_allows_a_tiebreak_rules_level_score(self):
        rule = fmt_mod.set_rule("six-game-tiebreak")
        self.assertTrue(fmt_mod.plausible(6, 6, rule))
        self.assertFalse(fmt_mod.plausible(8, 6, rule))

    def test_advantage_sets_have_no_ceiling(self):
        self.assertTrue(fmt_mod.plausible(24, 22, fmt_mod.set_rule("advantage-6")))

    def test_set_winner_is_none_for_an_unfinished_set(self):
        self.assertIsNone(fmt_mod.set_winner(5, 3, FIRST_TO_6))
        self.assertEqual(fmt_mod.set_winner(6, 3, FIRST_TO_6), "home")
        self.assertEqual(fmt_mod.set_winner(3, 6, FIRST_TO_6), "away")


class TestMatchFormat(unittest.TestCase):

    def test_format_is_read_off_the_rows_a_card_shows(self):
        derived = fmt_mod.from_rows([True, False, False, True])
        self.assertEqual(derived.name, "doubles-singles-doubles")
        self.assertEqual(derived.pattern, "d1-s1-s2-d2")
        self.assertEqual(derived.min_sets_for_result, 3)

    def test_a_six_row_card_is_a_six_set_format(self):
        # Nothing may assume four sets: that was one club's convention.
        derived = fmt_mod.from_rows([True, False, False, False, True, True])
        self.assertEqual(len(derived.slots), 6)
        self.assertEqual(derived.pattern, "d1-s1-s2-s3-d2-d3")
        self.assertEqual(derived.min_sets_for_result, 5)

    def test_singles_only_and_doubles_only_cards_are_formats_too(self):
        self.assertEqual(fmt_mod.from_rows([False] * 4).name, "singles-4")
        self.assertEqual(fmt_mod.from_rows([True] * 3).name, "doubles-3")

    def test_a_card_with_no_rows_is_not_a_format(self):
        with self.assertRaises(fmt_mod.FormatError):
            fmt_mod.from_rows([])

    def test_slot_spec_accepts_the_words_a_club_would_write(self):
        spec = fmt_mod.from_slot_spec(["doubles", "singles", "singles", "doubles"])
        self.assertEqual(spec.pattern, "d1-s1-s2-d2")
        with self.assertRaises(fmt_mod.FormatError):
            fmt_mod.from_slot_spec(["doubles", "mixed"])

    def test_max_games_per_match_prices_a_clean_sweep(self):
        self.assertEqual(DSSD.max_games_per_match, 24)
        pro = DSSD.with_set_rule(fmt_mod.set_rule("pro-8"))
        self.assertEqual(pro.max_games_per_match, 32)

    def test_minimum_sets_cannot_exceed_the_sets_that_exist(self):
        with self.assertRaises(fmt_mod.FormatError):
            fmt_mod.MatchFormat(name="x", slots=DSSD.slots, set_rule=FIRST_TO_6,
                                min_sets_for_result=5)

    def test_unknown_names_are_refused_with_the_known_ones_listed(self):
        with self.assertRaises(fmt_mod.FormatError) as caught:
            fmt_mod.match_format("nope")
        self.assertIn("doubles-singles-doubles", str(caught.exception))
        with self.assertRaises(fmt_mod.FormatError):
            fmt_mod.set_rule("first-to-seven")


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


class TestScoring(unittest.TestCase):

    def test_forfeit_value_is_derived_not_a_constant(self):
        # The predecessor hardcoded 6.4, which is 4 + 0.1 * (4 sets x 6 games) --
        # right for that one format and wrong for every other. Deriving it
        # reproduces the same number for the same format.
        self.assertAlmostEqual(GAMES_BONUS.forfeit_win_points(DSSD), 6.4)
        pro = DSSD.with_set_rule(fmt_mod.set_rule("pro-8"))
        self.assertAlmostEqual(GAMES_BONUS.forfeit_win_points(pro), 7.2)

    def test_points_add_the_game_bonus(self):
        self.assertAlmostEqual(
            GAMES_BONUS.points(outcome="win", games_won=15, sets_won=3), 5.5)
        self.assertAlmostEqual(
            GAMES_BONUS.points(outcome="loss", games_won=9, sets_won=1), 2.9)

    def test_rubbers_scheme_ignores_games_entirely(self):
        rubbers = scoring.ruleset("rubbers")
        self.assertEqual(rubbers.points(outcome="win", games_won=24, sets_won=3), 3)

    def test_decide_by_order_changes_who_won(self):
        # This is the setting that silently reorders a ladder, which is why it is
        # explicit on every ruleset and printed on every run.
        games_first = GAMES_BONUS.outcome(home_games=20, away_games=19,
                                          home_sets=1, away_sets=3)
        sets_first = scoring.ruleset("sets-first").outcome(
            home_games=20, away_games=19, home_sets=1, away_sets=3)
        self.assertEqual(games_first, "home")
        self.assertEqual(sets_first, "away")

    def test_a_wholly_level_match_is_a_draw(self):
        self.assertIsNone(GAMES_BONUS.outcome(home_games=12, away_games=12,
                                              home_sets=2, away_sets=2))

    def test_a_scheme_with_no_draw_refuses_to_score_one(self):
        # draw=None is not draw=0: a level match under such a scheme means
        # something was misread, and saying so beats inventing a value.
        no_draw = scoring.Ruleset(name="no-draw", win=2.0, draw=None, loss=0.0,
                                  ladder_by=("points", "name"))
        self.assertFalse(no_draw.allows_draw)
        with self.assertRaises(scoring.ScoringError):
            no_draw.base_points("draw")

    def test_ladder_order_must_end_in_a_total_tiebreak(self):
        # Without one the ladder is not reproducible, and every rebuild churns.
        with self.assertRaises(scoring.ScoringError):
            scoring.Ruleset(name="x", win=1, draw=0, loss=0,
                            ladder_by=("points", "wins"))

    def test_malformed_rulesets_are_refused_at_construction(self):
        with self.assertRaises(scoring.ScoringError):
            scoring.Ruleset(name="x", win=1, draw=0, loss=0, decide_by=())
        with self.assertRaises(scoring.ScoringError):
            scoring.Ruleset(name="x", win=1, draw=0, loss=0,
                            decide_by=("games", "games"))
        with self.assertRaises(scoring.ScoringError):
            scoring.Ruleset(name="x", win=1, draw=0, loss=0, decide_by=("rubbers",))
        with self.assertRaises(scoring.ScoringError):
            scoring.Ruleset(name="x", win=1, draw=0, loss=0, forfeit_award="fixed")
        with self.assertRaises(scoring.ScoringError):
            scoring.Ruleset(name="x", win=1, draw=0, loss=0,
                            ladder_by=("bananas", "name"))

    def test_a_ruleset_built_by_hand_pays_nothing_for_a_round_nobody_played(self):
        # The field's own default, which is what a scheme written for one club --
        # a preset added later, a ruleset assembled in a script -- inherits without
        # saying anything. Defaulting the other way puts points on a ladder for a
        # night off, which is the surprising half of the choice and so the half
        # that has to be asked for.
        plain = scoring.Ruleset(name="plain", win=2.0, draw=1.0, loss=0.0,
                                ladder_by=("points", "name"))
        self.assertFalse(plain.average_unplayed)

    def test_every_shipped_preset_describes_itself(self):
        for name, rules in scoring.RULESETS.items():
            with self.subTest(preset=name):
                described = rules.describe()
                self.assertTrue(described)
                self.assertIn("decided on", described)

    def test_unknown_preset_names_list_the_real_ones(self):
        with self.assertRaises(scoring.ScoringError) as caught:
            scoring.ruleset("bonus-points")
        self.assertIn("games-bonus", str(caught.exception))


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------


class TestSetResult(unittest.TestCase):

    def test_a_set_with_no_rule_attached_refuses_to_guess(self):
        naked = SetResult(slot=DSSD.slots[0], home_games=6, away_games=1)
        with self.assertRaises(ValueError):
            naked.completed

    def test_a_retired_set_is_not_complete_however_it_reads(self):
        retired = SetResult(slot=DSSD.slots[0], home_games=6, away_games=1,
                            status=SetStatus.RETIRED, rule=FIRST_TO_6)
        self.assertFalse(retired.completed)
        self.assertIsNone(retired.winner)

    def test_a_forfeited_set_is_complete_and_goes_to_the_other_side(self):
        forfeit = SetResult(slot=DSSD.slots[0], home_games=6, away_games=0,
                            status=SetStatus.FORFEIT, rule=FIRST_TO_6)
        self.assertTrue(forfeit.completed)
        self.assertEqual(forfeit.winner, "home")

    def test_margin_and_per_side_accessors(self):
        result = make_sets([(6, 2)])[0]
        self.assertEqual(result.margin, 4)
        self.assertEqual(result.games_for("home"), 6)
        self.assertEqual(result.games_for("away"), 2)
        self.assertEqual(result.players_for("home"), ("home-0",))


class TestMatch(unittest.TestCase):

    def test_tallies_come_from_the_sets(self):
        match = make_match()
        self.assertEqual(match.home_games, 19)
        self.assertEqual(match.away_games, 9)
        self.assertEqual(match.home_sets, 3)
        self.assertEqual(match.away_sets, 1)
        self.assertEqual(match.completed_sets, 4)

    def test_a_decided_match_pays_both_sides(self):
        match = make_match()
        self.assertTrue(match.decided)
        self.assertEqual(match.winner, "home")
        self.assertEqual(match.outcome_for("rockets"), Outcome.WIN)
        self.assertEqual(match.outcome_for("iguanas"), Outcome.LOSS)
        self.assertAlmostEqual(match.points_for("rockets"), 4 + 0.1 * 19)
        self.assertAlmostEqual(match.points_for("iguanas"), 2 + 0.1 * 9)

    def test_a_forfeit_with_neither_side_ticked_is_not_decided(self):
        # Measured on the predecessor: it paid the forfeit-win value to BOTH
        # sides, because nothing checked that the card said who forfeited.
        murky = make_match(status=MatchStatus.FORFEIT, forfeited_by=None)
        self.assertFalse(murky.decided)
        self.assertIsNone(murky.winner)
        self.assertIsNone(murky.points_for("rockets"))
        self.assertIsNone(murky.points_for("iguanas"))

    def test_a_forfeit_with_a_side_named_goes_to_the_other_one(self):
        clear = make_match(status=MatchStatus.FORFEIT, forfeited_by="away")
        self.assertTrue(clear.decided)
        self.assertEqual(clear.winner, "home")

    def test_an_unfinished_match_earns_no_points_of_its_own(self):
        # None is not zero: it means this match produced nothing, and what
        # happens next is the ruleset's decision, taken in stats.PointsLedger.
        thin = make_match(scores=((6, 1), (5, 3)))
        self.assertFalse(thin.decided)
        self.assertIsNone(thin.points_for("rockets"))

    def test_a_cancelled_match_is_undecided_whatever_it_shows(self):
        washout = make_match(status=MatchStatus.CANCELLED)
        self.assertFalse(washout.decided)

    def test_a_cancelled_match_has_no_numbers_worth_counting(self):
        # The scores on it are real -- rain stops a night halfway through and the
        # rubbers already finished are on the card -- and they still belong nowhere.
        # Counting them pays the team twice: once in the games column, once in
        # whatever the ruleset gives it for a round nobody played.
        washout = make_match(status=MatchStatus.CANCELLED)
        self.assertEqual(washout.home_games, 19)        # the card does say so
        self.assertFalse(washout.counts_as_played)      # and it counts for nothing

    def test_an_unfinished_match_still_counts_as_played(self):
        # Weaker than `decided`, deliberately. A night whose last rubber ran out of
        # light decides nothing, and the sets that were played were played: those
        # games are in the ladder's games column on the association's sheet too.
        thin = make_match(scores=((6, 1), (5, 3)))
        self.assertFalse(thin.decided)
        self.assertTrue(thin.counts_as_played)

    def test_only_the_three_unplayed_statuses_are_left_out(self):
        # Spelled out as a set so that adding a status has to be a decision about
        # this line rather than a default.
        left_out = {s for s in MatchStatus
                    if not make_match(status=s).counts_as_played}
        self.assertEqual(left_out, {MatchStatus.BYE, MatchStatus.CANCELLED,
                                    MatchStatus.UNPLAYED})

    def test_a_bye_is_recognisable_and_scores_nothing_directly(self):
        bye = Match(competition="test", round="1", home="rockets", away=NO_OPPONENT,
                    fmt=DSSD, rules=GAMES_BONUS, status=MatchStatus.BYE)
        self.assertTrue(bye.is_bye)
        self.assertFalse(bye.decided)
        self.assertIsNone(bye.points_for("rockets"))

    def test_the_bye_opponent_cannot_collide_with_a_real_team(self):
        # NO_OPPONENT contains a character slugify can never emit, so no club can
        # name a team into a collision with it -- including a team called "Bye",
        # which slugs to "bye" and stays distinct from the marker.
        self.assertNotIn("*", slugs.slugify("*Bye* Rockets"))
        for name in ("Bye", "*bye*", "BYE", "The Byes"):
            self.assertNotEqual(slugs.slugify(name), NO_OPPONENT)

    def test_a_team_not_in_the_match_gets_nothing_rather_than_a_guess(self):
        match = make_match()
        self.assertIsNone(match.side_of("wanderers"))
        self.assertIsNone(match.opponent_of("wanderers"))
        self.assertIsNone(match.points_for("wanderers"))
        self.assertEqual(match.games_for("wanderers"), 0)

    def test_players_on_a_side_keep_slot_order_without_duplicates(self):
        fmt = DSSD
        sets = (
            SetResult(slot=fmt.slots[0], home_games=6, away_games=1,
                      home_players=("ana", "bea"), rule=FIRST_TO_6),
            SetResult(slot=fmt.slots[1], home_games=6, away_games=2,
                      home_players=("ana",), rule=FIRST_TO_6),
        )
        match = Match(competition="t", round="1", home="rockets", away="iguanas",
                      fmt=fmt, rules=GAMES_BONUS, sets=sets)
        self.assertEqual(match.players_on("home"), ("ana", "bea"))

    def test_fixture_identity_is_round_and_pairing(self):
        self.assertEqual(make_match().key, ("1", "rockets", "iguanas"))

    def test_other_side_refuses_anything_that_is_not_a_side(self):
        self.assertEqual(other_side("home"), "away")
        with self.assertRaises(ValueError):
            other_side("middle")


# --------------------------------------------------------------------------
# competition
# --------------------------------------------------------------------------


def make_competition(team_slugs, *, declared=False, rounds_total=None):
    teams = {slug: Team(slug=slug, name=slugs.unslug(slug)) for slug in team_slugs}
    return Competition(
        id=competition_id("Tuesday", "C"), day="Tuesday", division="C",
        fmt=DSSD, rules=GAMES_BONUS, teams=teams, teams_declared=declared,
        rounds_total=rounds_total,
    )


class TestCompetitionIdentity(unittest.TestCase):

    def test_ids_are_readable_and_stable(self):
        self.assertEqual(competition_id("Tuesday", "C"), "tuesday-division-c")
        self.assertEqual(competition_id("Tuesday", "2"), "tuesday-division-2")

    def test_the_season_is_part_of_the_id_whenever_there_is_one(self):
        # Measured on the predecessor: omitting the season merged two seasons of
        # the same day and division into one ladder, then raised from the
        # exporter when both round 1s collided.
        self.assertEqual(competition_id("Tuesday", "C", "2026-Winter"),
                         "2026-winter-tuesday-division-c")
        self.assertNotEqual(competition_id("Tuesday", "C", "2025-winter"),
                            competition_id("Tuesday", "C", "2026-winter"))

    def test_a_competition_needs_something_to_be_named_after(self):
        with self.assertRaises(comp_mod.CompetitionError):
            competition_id("", "")

    def test_display_label_words_a_bare_division_letter(self):
        comp = make_competition(["rockets"])
        self.assertEqual(comp.display_label, "Tuesday Division C")

    def test_display_label_leaves_an_already_wordy_division_alone(self):
        comp = Competition(id="x", day="Tuesday", division="Grade 3", fmt=DSSD,
                           rules=GAMES_BONUS)
        self.assertEqual(comp.display_label, "Tuesday Grade 3")


class TestProvenance(unittest.TestCase):

    def test_only_declared_and_printed_values_are_trusted(self):
        self.assertTrue(Provenance.DECLARED.trusted)
        self.assertTrue(Provenance.CARD.trusted)
        self.assertFalse(Provenance.DERIVED.trusted)
        self.assertFalse(Provenance.MIXED.trusted)

    def test_a_collection_is_mixed_unless_everything_agrees(self):
        self.assertIs(merge_provenance([Provenance.CARD, Provenance.CARD]),
                      Provenance.CARD)
        self.assertIs(merge_provenance([Provenance.CARD, Provenance.DECLARED]),
                      Provenance.MIXED)
        self.assertIs(merge_provenance([]), Provenance.DERIVED)

    def test_an_unrecorded_aspect_reads_as_derived_not_as_fact(self):
        self.assertIs(make_competition(["a"]).provenance_of("season"),
                      Provenance.DERIVED)

    def test_a_teams_draw_code_stays_empty_until_a_human_supplies_it(self):
        # It is printed on no scorecard, so any value this program chose would be
        # invention dressed as data.
        self.assertIsNone(Team(slug="rockets", name="Rockets").code)


class TestRoundOrder(unittest.TestCase):

    def test_rounds_run_numerically_then_finals(self):
        # Sorting these as strings gives 1, 10, 2; sorting them as integers dies
        # on "F".
        labels = ["10", "SF", "2", "F", "1"]
        self.assertEqual(sorted(labels, key=round_sort_key),
                         ["1", "2", "10", "F", "SF"])

    def test_finals_labels_are_recognised_across_the_words_clubs_use(self):
        for label in ("F", "final", "Grand Final", "SF", "semi", "QF", "PF"):
            self.assertTrue(is_finals_label(label), label)
        for label in ("1", "12", "Round 3"):
            self.assertFalse(is_finals_label(label), label)


class TestDrawReconstruction(unittest.TestCase):
    """Rule F1: a bye may only be materialised from a closed team list."""

    def round_one(self):
        return [make_match(home="rockets", away="iguanas"),
                make_match(home="wanderers", away="aces")]

    def test_no_bye_is_invented_when_the_team_list_was_derived(self):
        # This is the predecessor's most expensive measured bug. Five declared
        # teams minus two in fixtures leaves one unaccounted for, and it
        # concluded that team was resting -- so it paid the team its own average
        # for a match it had actually played and won. With a derived list the
        # likelier explanation is a missing scorecard, so nothing is inferred.
        comp = make_competition(["rockets", "iguanas", "wanderers", "aces",
                                 "spinners"], declared=False)
        rounds = comp.rounds_from_matches(self.round_one())
        self.assertEqual(rounds["1"].byes, ())

    def test_a_bye_is_inferred_once_the_team_list_is_declared(self):
        comp = make_competition(["rockets", "iguanas", "wanderers", "aces",
                                 "spinners"], declared=True)
        rounds = comp.rounds_from_matches(self.round_one())
        self.assertEqual(rounds["1"].byes, ("spinners",))

    def test_two_teams_missing_is_a_missing_card_not_two_byes(self):
        # Refusing to pick one is the point: the shortfall is reported by
        # validate.py rather than resolved by guessing.
        comp = make_competition(["rockets", "iguanas", "wanderers", "aces",
                                 "spinners", "smashers"], declared=True)
        rounds = comp.rounds_from_matches(self.round_one())
        self.assertEqual(rounds["1"].byes, ())

    def test_a_derived_draw_is_a_history_not_a_schedule(self):
        # Only rounds that have cards appear (rule F2).
        comp = make_competition(["rockets", "iguanas"], declared=True)
        rounds = comp.rounds_from_matches(
            [make_match(rnd="1"), make_match(rnd="3")])
        self.assertEqual(sorted(rounds), ["1", "3"])
        for rnd in rounds.values():
            self.assertEqual(rnd.source, comp_mod.DRAW_FROM_CARDS)

    def test_a_finals_label_survives_draw_reconstruction(self):
        # Rule F11: a finals round must stay identifiable so it can be kept out
        # of the ladder.
        comp = make_competition(["rockets", "iguanas"], declared=True)
        rounds = comp.rounds_from_matches([make_match(rnd="SF")])
        self.assertTrue(rounds["SF"].is_finals)

    def test_a_materialised_bye_does_not_become_a_fixture(self):
        comp = make_competition(["rockets", "iguanas", "spinners"], declared=True)
        bye = Match(competition="test", round="1", home="spinners",
                    away=NO_OPPONENT, fmt=DSSD, rules=GAMES_BONUS,
                    status=MatchStatus.BYE)
        rounds = comp.rounds_from_matches([make_match(rnd="1"), bye])
        self.assertEqual(rounds["1"].fixtures, (("rockets", "iguanas"),))

    def test_rounds_total_stays_unknown_rather_than_becoming_rounds_played(self):
        # Rule F3, measured: the predecessor set it to the number of rounds
        # played, so a ten-round season read 100% complete at round three.
        comp = make_competition(["rockets", "iguanas"], declared=True)
        self.assertIsNone(comp.rounds_total)
        self.assertIsNone(comp.with_rounds(
            comp.rounds_from_matches(self.round_one())).rounds_total)

    def test_regular_rounds_exclude_the_finals(self):
        comp = make_competition(["rockets", "iguanas"], declared=True)
        comp = comp.with_rounds({
            "1": Round(number="1"),
            "SF": Round(number="SF", is_finals=True),
        })
        self.assertEqual([r.number for r in comp.regular_rounds], ["1"])

    def test_a_round_reports_every_team_it_touched_including_the_bye(self):
        rnd = Round(number="1", fixtures=(("rockets", "iguanas"),),
                    byes=("aces",))
        self.assertEqual(rnd.teams, ("rockets", "iguanas", "aces"))

    def test_a_whole_grade_resting_is_a_round_with_several_byes(self):
        # A public holiday, or a week called off before anybody travelled. One
        # team resting is the common case; it was for a while the only case the
        # model could hold, which meant the week a club had five byes to write
        # down it could write down one.
        rnd = Round(number="6", byes=("rockets", "iguanas", "aces"))
        self.assertEqual(rnd.teams, ("rockets", "iguanas", "aces"))


class TestClub(unittest.TestCase):

    def test_a_player_registered_elsewhere_is_findable(self):
        # This is how a fill-in borrowed from another grade is told apart from a
        # stranger, which changes what the dashboard should say about them.
        ana = Player(slug="ana-lee", name="Ana Lee", team="rockets")
        first = make_competition(["rockets"])
        first = first.with_teams({"rockets": Team(slug="rockets", name="Rockets",
                                                  players=(ana,))})
        second = Competition(id="wednesday-division-b", day="Wednesday",
                             division="B", fmt=DSSD, rules=GAMES_BONUS)
        club = comp_mod.Club(name="Test", competitions=(first, second))
        self.assertIs(club.competition_of("ana-lee"), first)
        self.assertIs(club.registered_elsewhere("ana-lee", "wednesday-division-b"),
                      first)
        self.assertIsNone(club.registered_elsewhere("ana-lee", first.id))

    def test_teams_are_findable_by_any_spelling(self):
        comp = make_competition(["riverside-rockets"])
        self.assertIsNotNone(comp.team_by_name("Riverside Rockets"))
        self.assertIsNotNone(comp.team_by_name("riverside rockets"))
        self.assertIsNone(comp.team_by_name("Nobody"))
        self.assertIsNone(comp.team_by_name("!!!"))


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


class TestConfigLoading(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_missing_file_is_a_first_run_not_an_error(self):
        loaded = config_mod.load(self.dir / "nothing.toml")
        self.assertFalse(loaded.exists)
        self.assertEqual(loaded.scoring, scoring.DEFAULT_RULESET)
        self.assertEqual(loaded.sets, fmt_mod.DEFAULT_SET_RULE)
        self.assertEqual(loaded.competitions, {})

    def test_a_malformed_file_is_an_error_rather_than_a_silent_default(self):
        # Falling back to defaults would mean a club's carefully entered team
        # list quietly stopped taking effect.
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write("[club\nname = "))

    def test_a_mistyped_section_is_named_on_every_run(self):
        loaded = config_mod.load(self.write('[clubb]\nname = "Riverside"\n'))
        self.assertTrue(any("clubb" in w for w in loaded.warnings))

    def test_a_mistyped_setting_is_named_too(self):
        loaded = config_mod.load(self.write('[club]\nnaem = "Riverside"\n'))
        self.assertTrue(any("naem" in w for w in loaded.warnings))

    def test_an_unknown_scheme_is_refused_while_we_can_still_name_the_file(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write('[rules]\nscoring = "bonus-points"\n'))
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write('[rules]\nsets = "first-to-seven"\n'))

    def test_an_unknown_season_mode_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write('[build]\nseasons = "yearly"\n'))

    def test_a_declared_team_list_closes_the_roster(self):
        loaded = config_mod.load(self.write(
            '[competitions."tuesday-division-c"]\n'
            'teams = ["Riverside Rockets", "Iguanas"]\n'))
        comp = loaded.competition("tuesday-division-c")
        self.assertTrue(comp.teams_declared)

    def test_no_team_list_leaves_the_roster_open(self):
        loaded = config_mod.load(self.write(
            '[competitions."tuesday-division-c"]\nlabel = "Tuesday C"\n'))
        self.assertFalse(loaded.competition("tuesday-division-c").teams_declared)

    def test_a_team_listed_twice_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[competitions."tuesday-division-c"]\n'
                'teams = ["Rockets", "Rockets"]\n'))

    def test_a_round_declared_twice_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[[competitions."tuesday-division-c".rounds]]\nnumber = "1"\n'
                '[[competitions."tuesday-division-c".rounds]]\nnumber = "1"\n'))

    def test_a_round_with_no_number_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[[competitions."tuesday-division-c".rounds]]\ndate = "2026-08-18"\n'))

    def test_a_fixture_that_is_not_a_pair_is_refused(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[[competitions."tuesday-division-c".rounds]]\nnumber = "1"\n'
                'fixtures = [["Rockets"]]\n'))

    def test_name_overrides_are_accepted_by_slug_or_by_printed_name(self):
        # Discovery writes slugs, but a club member editing by hand will type the
        # name as it appears on the card.
        loaded = config_mod.load(self.write(
            '[club]\nstrip_team_prefix = "Riverside"\n'
            '[team_names]\n"RIVERSIDE ROCKETS" = "Riverside Rockets"\n'))
        self.assertEqual(loaded.team_display("riverside-rockets"),
                         "Riverside Rockets")
        self.assertEqual(loaded.team_display("rockets"), "Riverside Rockets")

    def test_aliases_are_normalised_to_slugs_both_sides(self):
        loaded = config_mod.load(self.write(
            '[aliases]\n"A Fernandez" = "Ana Fernandez"\n'))
        self.assertEqual(loaded.aliases, {"a-fernandez": "ana-fernandez"})

    def test_a_declared_format_is_built_from_either_spelling(self):
        by_name = config_mod.load(self.write(
            '[rules]\nformat = "singles-4"\nsets = "fast4"\n'))
        self.assertEqual(by_name.declared_format().name, "singles-4")
        self.assertEqual(by_name.declared_format().set_rule.name, "fast4")
        by_slots = config_mod.load(self.write(
            '[rules]\nslots = ["doubles", "singles", "singles", "doubles"]\n'))
        self.assertEqual(by_slots.declared_format().pattern, "d1-s1-s2-d2")

    def test_no_declared_format_means_read_it_off_the_cards(self):
        self.assertIsNone(config_mod.load(self.dir / "nothing.toml").declared_format())

    def test_a_per_competition_scheme_overrides_the_global_one(self):
        loaded = config_mod.load(self.write(
            '[rules]\nscoring = "games-bonus"\n'
            '[competitions."tuesday-division-c"]\nscoring = "rubbers"\n'))
        comp = loaded.competition("tuesday-division-c")
        self.assertEqual(loaded.ruleset_for(comp).name, "rubbers")
        self.assertEqual(loaded.ruleset_for(None).name, "games-bonus")


class TestTheUnplayedConvention(unittest.TestCase):
    """What a bye or a washout is worth, which no preset name can settle.

    Two grades can award points identically and still disagree about the week
    nobody played: one credits the resting team its own average, the other credits
    it nothing. Both are real, the association's sheet says which, and the
    difference is the whole points column -- so it is a setting of its own rather
    than a fifth and sixth preset.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_it_overrides_the_presets_own_convention_without_replacing_it(self):
        plain = config_mod.load(self.write('[rules]\nscoring = "games-bonus"\n'))
        self.assertFalse(plain.ruleset_for(None).average_unplayed)
        averaged = config_mod.load(self.write(
            '[rules]\nscoring = "games-bonus"\nunplayed = "average"\n'))
        self.assertTrue(averaged.ruleset_for(None).average_unplayed)
        # Still games-bonus: only the one clause moved.
        self.assertEqual(averaged.ruleset_for(None).name, "games-bonus")

    def test_no_preset_pays_for_a_round_nobody_played_unless_asked(self):
        # The surprising convention is the one a club has to ask for. Shipping a
        # preset that credits a team its average for a washout puts points on a
        # ladder the association's sheet does not, and the club finds out by
        # comparing totals rather than by reading a default.
        for name in scoring.RULESETS:
            with self.subTest(name):
                self.assertFalse(scoring.RULESETS[name].average_unplayed)

    def test_one_competition_may_score_a_bye_differently_from_the_rest(self):
        loaded = config_mod.load(self.write(
            '[rules]\nunplayed = "average"\n'
            '[competitions."tuesday-division-c"]\nunplayed = "zero"\n'))
        comp = loaded.competition("tuesday-division-c")
        self.assertFalse(loaded.ruleset_for(comp).average_unplayed)
        self.assertTrue(loaded.ruleset_for(None).average_unplayed)

    def test_a_misspelling_stops_the_run_rather_than_being_warned_about(self):
        # Warned about and ignored, this would fall back to the preset's own
        # convention: a points column quietly not the one the club asked for. The
        # club asked precisely because it had noticed the difference.
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write('[rules]\nunplayed = "averaged"\n'))
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[competitions."tuesday-division-c"]\nunplayed = "nothing"\n'))

    def test_leaving_it_out_leaves_the_preset_alone(self):
        for name in scoring.RULESETS:
            with self.subTest(preset=name):
                loaded = config_mod.load(self.write(f'[rules]\nscoring = "{name}"\n'))
                self.assertEqual(loaded.ruleset_for(None),
                                 scoring.ruleset(name))


class TestTheByesShorthand(unittest.TestCase):
    """``[competitions.<id>.byes]``: the byes a club has to type, and no more.

    A club should not transcribe a whole draw to record five byes. Two things make
    that possible -- inference covers the rounds whose cards have arrived, and this
    table covers the rest -- and the shape of the table is the shape of the sheet
    the association publishes: one row per team, rounds across the top.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def declared(self, byes):
        return config_mod.load(self.write(
            '[competitions."monday-division-b"]\n'
            'teams = ["Rockets", "Iguanas", "Aces"]\n'
            '[competitions."monday-division-b".byes]\n' + byes)
        ).competition("monday-division-b")

    def test_it_is_written_by_team_and_read_back_by_round(self):
        # Read off by team it is one pass down a column. By round it would be a
        # pass across every row per line typed, which is where transcription
        # errors come from.
        comp = self.declared('Rockets = [1, 6]\nIguanas = [2, 6]\nAces = 6\n')
        self.assertEqual(comp.byes, {"Rockets": ("1", "6"),
                                     "Iguanas": ("2", "6"),
                                     "Aces": ("6",)})
        self.assertEqual(comp.byes_by_round(),
                         {"1": ("Rockets",), "2": ("Iguanas",),
                          "6": ("Aces", "Iguanas", "Rockets")})

    def test_a_single_round_needs_no_brackets(self):
        # The commonest line in the table is one round, and requiring `[6]` for it
        # is the kind of friction that ends in the table not being filled in.
        self.assertEqual(self.declared('Rockets = 6\n').byes, {"Rockets": ("6",)})
        self.assertEqual(self.declared('Rockets = "SF"\n').byes,
                         {"Rockets": ("SF",)})

    def test_a_round_written_twice_for_one_team_is_one_bye(self):
        self.assertEqual(self.declared('Rockets = [6, 6]\n').byes,
                         {"Rockets": ("6",)})

    def test_a_team_the_declared_list_does_not_name_stops_the_run(self):
        # A bye is the one thing that pays a team points for playing nothing, so a
        # typo here would credit a team that does not exist and quietly leave the
        # real one a match short. Closing the team list is what makes it an error.
        with self.assertRaises(config_mod.ConfigError) as caught:
            self.declared('Rokcets = [1]\n')
        self.assertIn("Rokcets", str(caught.exception))
        self.assertIn("Rockets", str(caught.exception))

    def test_a_spelling_the_team_list_uses_is_accepted_however_it_is_cased(self):
        self.assertEqual(self.declared('"ROCKETS" = [1]\n').byes,
                         {"ROCKETS": ("1",)})

    def test_rounds_that_are_not_round_numbers_are_refused(self):
        # `bool` is an `int` in Python, so left to `str()` a `true` here becomes a
        # bye in a round called "True": a round on the dashboard that no draw has,
        # paying a team for resting in a week that does not exist. Each shape is
        # turned away where it can be described, so the message names the fix.
        with self.assertRaises(config_mod.ConfigError) as scalar:
            self.declared('Rockets = true\n')
        self.assertIn("should be a list", str(scalar.exception))
        with self.assertRaises(config_mod.ConfigError) as inside:
            self.declared('Rockets = [1, true]\n')
        self.assertIn("not a round number", str(inside.exception))
        with self.assertRaises(config_mod.ConfigError):
            self.declared('Rockets = [[6]]\n')

    def test_byes_are_taken_on_trust_while_the_team_list_is_still_open(self):
        # There is nothing to check the name against yet, and refusing every name
        # would make the shorthand unusable until the roster has been reviewed.
        loaded = config_mod.load(self.write(
            '[competitions."monday-division-b".byes]\nRockets = [1]\n'))
        self.assertEqual(loaded.competition("monday-division-b").byes,
                         {"Rockets": ("1",)})

    def test_a_round_block_may_rest_one_team_or_the_whole_grade(self):
        # `bye = "Rockets"` was the only shape this held for a while, so a club
        # writing down the week its whole grade rested got one team named after
        # all five of them -- `str()` of a list, slugged.
        loaded = config_mod.load(self.write(
            '[[competitions."monday-division-b".rounds]]\n'
            'number = "1"\nbye = "Rockets"\n'
            '[[competitions."monday-division-b".rounds]]\n'
            'number = "6"\nbyes = ["Rockets", "Iguanas"]\n'))
        one, whole = loaded.competition("monday-division-b").rounds
        self.assertEqual(one.byes, ("Rockets",))
        self.assertEqual(whole.byes, ("Rockets", "Iguanas"))

    def test_a_round_block_that_uses_both_spellings_is_refused(self):
        # Two lists of resting teams in one round is a contradiction to resolve by
        # asking, not by picking one.
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[[competitions."monday-division-b".rounds]]\n'
                'number = "1"\nbye = "Rockets"\nbyes = ["Iguanas"]\n'))

    def test_a_round_block_bye_that_is_not_a_team_name_is_refused(self):
        # `str()` of whatever was written would make a team out of it, and the
        # team would be a real row on a real ladder.
        for written in ('bye = [1]', 'bye = true', 'bye = 6'):
            with self.subTest(written=written):
                with self.assertRaises(config_mod.ConfigError):
                    config_mod.load(self.write(
                        '[[competitions."monday-division-b".rounds]]\n'
                        f'number = "1"\n{written}\n'))


class TestConfigRendering(unittest.TestCase):
    """The first run writes this file, so it has to be valid and reviewable."""

    def setUp(self):
        self.blank = config_mod.Config(path=Path("config.toml"))
        self.comp = make_competition(["riverside-rockets", "iguanas"])

    def test_an_empty_discovery_still_writes_usable_toml(self):
        text = config_mod.render(self.blank, competitions=(), club_name="")
        tomllib.loads(text)
        self.assertIn("No scorecards were found", text)

    def test_a_rendered_config_round_trips_without_warnings(self):
        import tempfile
        text = config_mod.render(self.blank, competitions=[self.comp],
                                 club_name="Riverside Tennis Club", cards=6)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            loaded = config_mod.load(path)
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.club_name, "Riverside Tennis Club")
        self.assertIn(self.comp.id, loaded.competitions)

    def test_the_team_list_is_written_commented_out(self):
        # Rule F1's onboarding half: adopting a derived value is a deliberate act
        # -- deleting a '#' -- because an uncommented list closes the roster and
        # switches on bye handling, which is right only after a human has looked.
        import tempfile
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertIn("# teams = [", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            loaded = config_mod.load(path)
        self.assertFalse(loaded.competition(self.comp.id).teams_declared)

    def test_rounds_total_is_written_commented_out_too(self):
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertIn("# rounds_total = ", text)

    def test_every_preset_is_listed_where_a_club_will_read_it(self):
        text = config_mod.render(self.blank, competitions=[self.comp])
        for name in scoring.RULESETS:
            self.assertIn(name, text)
        for name in fmt_mod.SET_RULES:
            self.assertIn(name, text)

    def test_discovered_team_names_are_pre_filled_for_review(self):
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertIn("riverside-rockets", text)

    def test_a_declared_team_list_is_written_back_uncommented(self):
        declared = config_mod.Config(
            path=Path("config.toml"), exists=True,
            competitions={self.comp.id: config_mod.CompetitionConfig(
                id=self.comp.id, teams=("Riverside Rockets", "Iguanas"))})
        text = config_mod.render(declared, competitions=[self.comp])
        self.assertRegex(text, r"(?m)^teams = \[")

    def test_awkward_keys_and_values_survive_the_emitter(self):
        self.assertEqual(config_mod.toml_key("plain-key_1"), "plain-key_1")
        self.assertEqual(config_mod.toml_key("has space"), '"has space"')
        self.assertEqual(config_mod.toml_value(True), "true")
        self.assertEqual(config_mod.toml_value(3), "3")
        self.assertEqual(config_mod.toml_value(["a", "b"]), '["a", "b"]')
        self.assertEqual(config_mod.toml_value('quote"and\\slash'),
                         '"quote\\"and\\\\slash"')

    def test_a_club_name_with_a_quote_in_it_still_parses(self):
        text = config_mod.render(self.blank, competitions=[self.comp],
                                 club_name='The "Real" Club')
        self.assertEqual(tomllib.loads(text)["club"]["name"], 'The "Real" Club')

    def test_multi_season_mode_writes_the_seasons_table(self):
        multi = config_mod.Config(path=Path("config.toml"), seasons_mode="multi")
        text = config_mod.render(multi, competitions=[self.comp])
        self.assertIn("[seasons]", text)
        tomllib.loads(text)

    def test_single_season_mode_does_not_mention_seasons(self):
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertNotIn("\n[seasons]", text)

    def test_both_unplayed_conventions_are_named_and_neither_is_chosen(self):
        # Written commented out for the same reason the team list is: the preset
        # already has a convention, and overriding it is a deliberate act.
        text = config_mod.render(self.blank, competitions=[self.comp])
        for mode in scoring.UNPLAYED_MODES:
            self.assertIn(mode, text)
        self.assertRegex(text, r"(?m)^# unplayed = ")
        self.assertNotRegex(text, r"(?m)^unplayed = ")

    def test_a_chosen_unplayed_convention_is_written_back_live(self):
        chosen = config_mod.Config(path=Path("config.toml"), exists=True,
                                   unplayed=scoring.UNPLAYED_ZERO)
        text = config_mod.render(chosen, competitions=[self.comp])
        self.assertRegex(text, r'(?m)^unplayed = "zero"')

    def test_the_byes_table_is_written_ready_for_the_association_sheet(self):
        # A club with a sheet in front of it should find the block already there
        # with an example in it, rather than having to learn the syntax.
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertIn(f"[competitions.{self.comp.id}.byes]", text)
        self.assertIn("= [1, 6]", text)
        tomllib.loads(text)

    def test_an_inferred_bye_is_written_commented_so_it_is_not_typed_twice(self):
        # It is already worked out from the cards. Shown so a club can see the
        # program agrees with its sheet, commented so declaring it is a choice.
        comp = self.comp.with_rounds({
            "1": Round(number="1", fixtures=(("iguanas", "aces"),),
                       byes=("riverside-rockets",))})
        text = config_mod.render(self.blank, competitions=[comp])
        self.assertRegex(text, r'(?m)^# "Riverside Rockets" = \["1"\]')
        self.assertNotRegex(text, r'(?m)^"Riverside Rockets" = ')


class TestNoClubSpecificsLeaked(unittest.TestCase):
    """This project is published for any club. The predecessor's was not."""

    SOURCES = sorted((ROOT / "matchcentre").glob("*.py"))

    # Everything that gets published, not just the engine: a club name in the
    # README or in the dashboard's own JavaScript is more visible than one in a
    # docstring, not less. `notes/` is deliberately excluded -- it is gitignored,
    # and it is where the predecessor's specifics are allowed to be discussed.
    # `tests/` is *not* skipped: it is published like everything else, and a
    # fixture is the likeliest place for a real player's name to survive. `data/`
    # and `site/` are a club's own inputs and generated output, neither tracked.
    SKIP_DIRS = {".git", "__pycache__", "notes", "data", "site"}
    # `.yml` covers `.github/`: the issue templates quote example names, and an
    # example is exactly where a real one gets copied in by accident.
    SHIPPED_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".toml", ".yml"}

    @classmethod
    def shipped_files(cls):
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            if set(path.relative_to(ROOT).parts) & cls.SKIP_DIRS:
                continue
            if path.suffix in cls.SHIPPED_SUFFIXES:
                yield path

    def assert_absent(self, banned, paths):
        """Report every offending line at once, and none of the file's contents.

        ``assertNotIn`` on a whole source file prints the whole source file, which
        buried the two real hits this check found in 80KB of scrollback. A test
        that is unpleasant to read is a test people stop running.
        """
        hits = []
        for path in paths:
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1):
                folded = line.casefold()
                for term in banned:
                    if term in folded:
                        where = path.relative_to(ROOT)
                        hits.append(f"  {where}:{number}: {term!r} in "
                                    f"{line.strip()[:90]}")
        if hits:
            self.fail(f"{len(hits)} club-specific reference(s):\n"
                      + "\n".join(hits))

    # The strings that must not appear anywhere published: the club this project
    # was rewritten away from, and the real players named in its config. They are
    # read from a gitignored file rather than written here, because a test that
    # lists them publishes them -- which is the exact thing it exists to prevent.
    # A fresh clone has no such file and no such history, so the check skips.
    LEAK_TERMS = ROOT / "notes" / "leak-terms.txt"

    def banned_terms(self):
        if not self.LEAK_TERMS.is_file():
            self.skipTest(f"{self.LEAK_TERMS.name} absent: nothing to leak")
        return [line.strip().casefold()
                for line in self.LEAK_TERMS.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]

    def test_the_foundation_modules_name_no_particular_club(self):
        # A generic tool that ships one club's name in its examples reads as
        # somebody else's tool. The predecessor's defaults were full of them.
        self.assert_absent(self.banned_terms(), self.SOURCES)

    def test_nothing_published_names_a_particular_club_or_player(self):
        # The same check over every file that will exist in the GitHub repo,
        # including the dashboard's own JavaScript, the README and the example
        # config -- a club name in a page heading is more visible than one in a
        # docstring, and a real player's name in a public repo is a privacy
        # problem rather than an aesthetic one.
        self.assert_absent(self.banned_terms(), self.shipped_files())

    def test_no_module_hardcodes_the_predecessors_four_set_shape(self):
        # D1/S1/S2/D2 was one club's format. It may appear as a *derived* value
        # but never as a literal in the engine.
        for path in self.SOURCES:
            if path.name == "format.py":
                continue        # the named presets legitimately live here
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn('"d1-s1-s2-d2"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
