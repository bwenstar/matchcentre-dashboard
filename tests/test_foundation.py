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

import dataclasses
import itertools
import random
import re
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matchcentre import competition as comp_mod
from matchcentre import config as config_mod
from matchcentre import format as fmt_mod
from matchcentre import scoring, slugs
from matchcentre.competition import (Competition, Player, Provenance, Round, Team,
                                     competition_id, is_finals_label,
                                     merge_provenance, round_sort_key,
                                     season_order_key)
from matchcentre import model as model_mod
from matchcentre.model import (NO_OPPONENT, Match, MatchStatus, Outcome, Rating,
                               SetResult, SetStatus, forfeit_match_winner,
                               other_side)

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
        self.assertEqual(slugs.slugify("Margaux Écrin"), "margaux-ecrin")
        self.assertEqual(slugs.slugify("O'Brien-Smith"), "o-brien-smith")
        self.assertEqual(slugs.slugify("  Spaced   Out  "), "spaced-out")

    def test_unsluggable_text_raises_rather_than_returning_empty(self):
        # An empty identifier would merge two different subjects into one key,
        # which is a wrong dashboard rather than a crash.
        for bad in ("", "   ", "---", "!!!"):
            with self.assertRaises(ValueError):
                slugs.slugify(bad)

    def test_a_label_key_folds_the_spellings_of_one_label(self):
        self.assertEqual(slugs.label_key("Div C"), slugs.label_key("div c"))
        self.assertEqual(slugs.label_key("Tuesday"), slugs.label_key("TUESDAY "))
        self.assertNotEqual(slugs.label_key("B"), slugs.label_key("C"))

    def test_a_label_key_never_raises_because_a_label_is_not_an_identifier(self):
        # A division printed as "-" is nothing to file a subject under, which is why
        # slugify refuses it, but it still has to compare equal to itself: the config
        # load asks whether two sections claim one competition, and raising there
        # turns a club's odd division name into a traceback.
        for odd in ("", "   ", "---", "!!!"):
            self.assertEqual(slugs.label_key(odd), slugs.label_key(odd))
        self.assertNotEqual(slugs.label_key("---"), slugs.label_key("!!!"))

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

    def test_the_abbreviations_the_check_s_comment_names_are_ones_it_keeps(self):
        """The comment on the abbreviation check names only words the check keeps.

        It named a four-letter acronym beside RTC as a word the check keeps, over a
        check that keeps a word of three letters or fewer, so a reader of that line
        was told the acronym stays in capitals where the test above pins it title
        cased. Measured on the base (/tmp/mcf/scratch/CASE/before.txt): RTC and TC
        are kept, and the comment's four-letter acronym and ABCD are title-cased.
        Rejected: a cut of four, which keeps the comment true by changing NORTHSIDE
        NETS to ``Northside NETS``.
        """
        source = (ROOT / "matchcentre" / "slugs.py").read_text(encoding="utf-8")
        said = re.findall(r"#\s*(.*?)\s*—\s*an abbreviation, keep it", source)
        self.assertEqual(len(said), 1, said)
        # Abbreviations and nothing else, so no other word claims one the check
        # title-cases: "any four-letter one" beside them read as one would.
        self.assertRegex(said[0], r"^[A-Z]{2,}(?:, [A-Z]{2,})+$")
        for word in said[0].split(", "):
            with self.subTest(word):
                self.assertEqual(slugs.display_name(word), word)

    def test_unslug_is_the_last_resort_display_name(self):
        self.assertEqual(slugs.unslug("riverside-rockets"), "Riverside Rockets")

    def test_near_misses_finds_a_typo_and_ignores_a_different_word(self):
        known = ["iguanas", "rockets", "wanderers"]
        self.assertEqual(slugs.near_misses("igunas", known), ["iguanas"])
        self.assertEqual(slugs.near_misses("rockets", known), [])
        self.assertEqual(slugs.near_misses("zebras", known), [])


class TestAFoldSetsTheCaseAndTheAccentsAsideAndNothingElse(unittest.TestCase):
    """``slugs.fold``: NFKD with its combining marks dropped, then casefolded.

    The fold a slug is made of, taken out of ``slugify`` so that the careers list is
    ordered by it too. Before, ``slugify`` folded inline, ``careers.initial`` folded
    the same way a second time, and the careers list sorted on the name casefolded
    alone, which listed Émile Renn after Zoe Arden though ``initial`` filed him
    under E (/tmp/t7/scratch/CA2/probe/before-order218.txt; test_careers's
    ``TestAnAccentedNameIsListedAmongTheNamesOfTheLetterItIsFiledUnder``).

    And ``redact.fold`` was a second fold, casefolding last where the slug's
    casefolded first. Over every code point the two differed on 700
    (/tmp/t7/scratch/CA2/fix/census.py, census.txt): casefolding first leaves
    uncased the capital NFKD makes of ``ᴬ``, a mathematical ``𝐁`` or ``ℝ``, so
    492 lost their letter from a slug, and a second fold changed 636. Now there is
    one, in redact's order, which loses none and is its own fold's fold; every
    slug fragment it moves only gains the letter the old one dropped. The
    dashboard's theme key, app.js's ``slugifyName``, lowers after the marks go
    too, or it would part from ``slugify`` on 494 code points where it parted on
    ß and ẞ alone (run in Chrome, /tmp/t7/scratch/CA2/fix/jschrome.txt).
    Rejected: a fold of the careers module's own, a third copy of the rule to
    drift from the slug's; and the one fold in the slug's order, which would
    change how redact matches a withheld name on 700 code points.
    """

    def test_the_case_and_the_accents_come_off(self):
        self.assertEqual(slugs.fold("Émile ÅNA élodie"), "emile ana elodie")

    def test_a_compatibility_form_is_the_letter_it_stands_for(self):
        self.assertEqual(slugs.fold("Ｗei STRAẞE"), "wei strasse")

    def test_a_letter_with_no_accent_to_take_off_and_the_rest_are_kept(self):
        self.assertEqual(slugs.fold("Łuka Øyna 李 '4-"), "łuka øyna 李 '4-")

    def test_a_capital_that_nfkd_makes_is_folded_too(self):
        self.assertEqual(slugs.fold("ᴬda ℝenn 𝐁o"), "ada renn bo")
        self.assertEqual(slugs.slugify("ᴬda ℝenn"), "ada-renn")

    def test_folding_a_folded_name_again_changes_nothing(self):
        for name in ("ᴬda", "ℝenn", "𝐁o", "Ｗei", "Émile", "STRAẞE", "Øyna"):
            with self.subTest(name=name):
                self.assertEqual(slugs.fold(slugs.fold(name)), slugs.fold(name))

    def test_redact_compares_two_spellings_by_this_one_fold(self):
        from matchcentre import redact
        self.assertIs(redact.fold, slugs.fold)

    def test_the_dashboards_theme_key_lowers_after_the_marks_go_as_the_fold_does(self):
        # site.py's theme key is slugify's, and app.js must make the same one.
        source = (ROOT / "matchcentre" / "web" / "app.js").read_text(encoding="utf-8")
        body = re.search(r"\nfunction slugifyName\(text\) \{\n(.*?)\n\}\n",
                         source, re.S).group(1)
        self.assertEqual(body.count("toLowerCase()"), 1)
        self.assertLess(body.index("normalize('NFKD')"), body.index("toLowerCase()"))


class TestAOneLetterAIsATeamsLetterUnlessItReadsAsTheArticle(unittest.TestCase):
    """A shouted ``A`` is lowered only where it is the article inside a phrase.

    A lone capital is shouted and title case at once, and ``a`` is a minor word,
    so every ``A`` past the first word was lowered. Measured on the base:
    HILLTOP A beside HILLTOP B was ``Hilltop a`` beside ``Hilltop B``, DIVISION A
    ``Division a``, THE A TEAM ``The a Team``, AN A TEAM ``An a Team``, HILLTOP A
    2 ``Hilltop a 2``, HILLTOP A (W) ``Hilltop a (W)``, a name already printed
    ``Hilltop A`` ``Hilltop a``, and the slugs ``hilltop-a`` and ``the-a-team``
    came back the same way. Now it stays ``A`` closing a name, before anything
    that does not begin with a letter, after an article, and in a name printed in
    mixed case, and KINGS OF A COURT still reads ``Kings of a Court``.

    Rejected: taking ``a`` out of the minor words, which gives ``Kings of A
    Court``; and keeping every one-letter word, which is the same thing, since
    ``a`` is the only one of the minor words that has one letter.
    """

    def test_an_a_that_closes_a_name_is_its_letter(self):
        self.assertEqual(slugs.display_name("HILLTOP A"), "Hilltop A")
        self.assertEqual(slugs.display_name("HILLTOP B"), "Hilltop B")
        self.assertEqual(slugs.display_name("DIVISION A"), "Division A")
        self.assertEqual(slugs.display_name("RTC A"), "RTC A")

    def test_an_a_before_anything_that_does_not_begin_with_a_letter_is_its_letter(self):
        self.assertEqual(slugs.display_name("HILLTOP A 2"), "Hilltop A 2")
        self.assertEqual(slugs.display_name("HILLTOP A (2)"), "Hilltop A (2)")
        self.assertEqual(slugs.display_name("HILLTOP A (W)"), "Hilltop A (W)")

    def test_an_a_after_an_article_is_a_letter(self):
        self.assertEqual(slugs.display_name("THE A TEAM"), "The A Team")
        self.assertEqual(slugs.display_name("AN A TEAM"), "An A Team")
        self.assertEqual(slugs.display_name("A A TEAM"), "A A Team")

    def test_a_name_printed_in_mixed_case_keeps_the_a_it_was_printed_with(self):
        self.assertEqual(slugs.display_name("Hilltop A"), "Hilltop A")
        self.assertEqual(slugs.display_name("Jo A Smith"), "Jo A Smith")
        self.assertEqual(slugs.display_name("Kings of a Court"), "Kings of a Court")

    def test_an_a_inside_a_name_that_reads_as_a_phrase_is_still_the_article(self):
        self.assertEqual(slugs.display_name("KINGS OF A COURT"), "Kings of a Court")
        self.assertEqual(slugs.display_name("ONCE IN A BLUE MOON"),
                         "Once in a Blue Moon")
        self.assertEqual(slugs.display_name("A TEAM OF A KIND"), "A Team of a Kind")

    def test_a_slug_s_last_letter_comes_back_a_capital(self):
        self.assertEqual(slugs.unslug("hilltop-a"), "Hilltop A")
        self.assertEqual(slugs.unslug("the-a-team"), "The A Team")

    def test_an_a_before_a_word_a_team_s_letter_stands_before_is_its_letter(self):
        """HILLTOP A GRADE beside HILLTOP B GRADE is Hilltop A Grade beside Hilltop B
        Grade.

        An A before a word was the article unless an article stood before it, and a B
        is kept as a short word, so one club's grades printed two shapes. Measured on
        the base (/tmp/t7/scratch/N1/names.py): HILLTOP A GRADE gave ``Hilltop a
        Grade`` beside ``Hilltop B Grade``, and HILLTOP A TEAM, A TEAMS, A RESERVE, A
        RESERVES, A SIDE and A SQUAD each gave the same lower ``a``. Now an A before
        one of those words is a letter, unless a minor word stands before the A.
        WHAT A TEAM and FIVE A SIDE keep a capital too, the list's known cost.
        Rejected: leaving these names to ``[team_names]``, when the B beside the A is
        kept already.
        """
        for word in ("TEAM", "TEAMS", "GRADE", "RESERVE", "RESERVES", "SIDE",
                     "SQUAD"):
            with self.subTest(word):
                self.assertEqual(slugs.display_name(f"HILLTOP A {word}"),
                                 f"Hilltop A {word.capitalize()}")
                self.assertEqual(slugs.display_name(f"HILLTOP B {word}"),
                                 f"Hilltop B {word.capitalize()}")
        self.assertEqual(slugs.display_name("WHAT A TEAM"), "What A Team")
        self.assertEqual(slugs.display_name("FIVE A SIDE"), "Five A Side")

    def test_after_a_minor_word_an_a_before_such_a_word_is_still_the_article(self):
        # A word not on the list keeps the reading it had: LOVE A LOB.
        self.assertEqual(slugs.display_name("PART OF A TEAM"), "Part of a Team")
        self.assertEqual(slugs.display_name("ACES IN A SQUAD"), "Aces in a Squad")
        # An ampersand is the minor word it stands for, as ACES AND A TEAM's is.
        self.assertEqual(slugs.display_name("ACES & A TEAM"), "Aces & a Team")
        self.assertEqual(slugs.display_name("KINGS OF A COURT"), "Kings of a Court")
        self.assertEqual(slugs.display_name("LOVE A LOB"), "Love a LOB")

    def test_a_count_after_the_article_reads_as_a_team_s_number_does(self):
        """WORTH A 1000 WORDS keeps its A, the known cost of HILLTOP A 2 keeping its.

        An A before anything that does not begin with a letter is a team's letter,
        so the article before a count is read as one too. Measured on the base
        (/tmp/t7/scratch/N1/names.py): ``Worth A 1000 Words`` beside ``Hilltop A
        2``. No demo season or fixture prints such a name, so nothing but a guess
        tells a count from a team's number, and ``[team_names]`` spells the name.
        The two are pinned together, so a change to the number test is seen at
        both. Rejected: reading a count of three digits or more as the article's,
        a rule no fixture asks for.
        """
        self.assertEqual(slugs.display_name("WORTH A 1000 WORDS"),
                         "Worth A 1000 Words")
        self.assertEqual(slugs.display_name("HILLTOP A 2"), "Hilltop A 2")

    def test_a_letter_after_a_word_that_names_one_reads_as_the_article(self):
        """RIVERSIDE VITAMIN A ROCKETS is ``Riverside Vitamin a Rockets``, the known
        cost of KINGS OF A COURT reading ``Kings of a Court``.

        An A before a word is the article unless an article stands before it or the
        word is one a team's letter stands before, so a letter after a word that names
        one is lowered before any other word. Measured on the base
        (/tmp/mcf/scratch/CASE/before.txt): ``Riverside Vitamin a Rockets`` in
        ``season.json``, the report and the starter config's offer, PLAN A ROCKETS
        ``Plan a Rockets`` and PLAN A PARTY ``Plan a Party``, while VITAMIN A, the A
        closing the name, keeps it. ``[team_names]`` spells the name, and the note
        the first run writes over that table names this guess. Pinned, as the
        four-letter cut is, so the reading is a decision on record. Rejected: a list
        of words before the A that name a letter (VITAMIN, PLAN), which makes PLAN A
        PARTY ``Plan A Party``; and putting ROCKETS on the words a team's letter
        stands before, a word of one team's name.
        """
        self.assertEqual(slugs.display_name("RIVERSIDE VITAMIN A ROCKETS"),
                         "Riverside Vitamin a Rockets")
        self.assertEqual(slugs.display_name("PLAN A ROCKETS"), "Plan a Rockets")
        self.assertEqual(slugs.display_name("PLAN A PARTY"), "Plan a Party")
        self.assertEqual(slugs.display_name("VITAMIN A"), "Vitamin A")


class TestAShoutedWordIsCapitalisedAtItsFirstLetterAndNotAtAMarkBeforeIt(
        unittest.TestCase):
    """A shouted word takes its capital at its first letter, and a surname's prefix
    past its apostrophe, while a possessive keeps the letters after it lower.

    ``display_name`` un-shouted a word with ``str.capitalize``, which capitalises
    the first character and lowers every other, so a bracket took the capital and
    nothing after an apostrophe, a hyphen or a full stop kept one. Measured on the
    base (/tmp/t7/scratch/N1/names.py): HILLTOP (WOMENS) gave ``Hilltop
    (womens)``, HILLTOP [RESERVES] ``Hilltop [reserves]``, O'BRIEN ``O'brien``, the
    same with a curly apostrophe ``O’brien``, D'ARCY'S DRIVERS ``D'arcy's
    Drivers``, NORTH-WEST ROCKETS ``North-west Rockets`` and MT.ORRIN ACES
    ``Mt.orrin Aces``. KING'S ACES gave ``King's Aces``, KINGS' ACES ``Kings'
    Aces`` and QUOLL'S CROSSING ``Quoll's Crossing``, which were right and are
    still. Now a word is capitalised at its first letter or digit, each part
    between hyphens and full stops is a word of its own, and an apostrophe with one
    letter before it and two or more after is a surname's prefix.

    Rejected: capitalising after every apostrophe, which gives ``King'S``; a list
    of surname prefixes, which the word's own shape makes unneeded; and leaving
    O'BRIEN to ``[team_names]``, which is for what the cards cannot settle.
    """

    def test_a_word_in_brackets_is_capitalised_inside_them(self):
        self.assertEqual(slugs.display_name("HILLTOP (WOMENS)"), "Hilltop (Womens)")
        self.assertEqual(slugs.display_name("HILLTOP [RESERVES]"),
                         "Hilltop [Reserves]")
        self.assertEqual(slugs.display_name("HILLTOP A (WOMENS)"),
                         "Hilltop A (Womens)")

    def test_one_letter_and_an_apostrophe_before_a_name_is_a_surname_s_prefix(self):
        self.assertEqual(slugs.display_name("O'BRIEN"), "O'Brien")
        self.assertEqual(slugs.display_name("O’BRIEN"), "O’Brien")
        self.assertEqual(slugs.display_name("O'BRIENS ACES"), "O'Briens Aces")
        self.assertEqual(slugs.display_name("D'ARCY'S DRIVERS"), "D'Arcy's Drivers")
        # The shape and no list: any one letter is a prefix, and two letters after
        # it are a name. Only a player's name reaches D'OR, which the team rule
        # keeps whole as a word of three letters.
        self.assertEqual(slugs.display_name("L'ESTRANGE ACES"), "L'Estrange Aces")
        self.assertEqual(slugs.person_name("KIT D'OR"), "Kit D'Or")

    def test_a_possessive_keeps_the_letters_after_its_apostrophe_lower(self):
        self.assertEqual(slugs.display_name("KING'S ACES"), "King's Aces")
        self.assertEqual(slugs.display_name("KINGS' ACES"), "Kings' Aces")
        self.assertEqual(slugs.display_name("QUOLL'S CROSSING"), "Quoll's Crossing")
        # More than one letter before it is a word, and no surname's prefix.
        self.assertEqual(slugs.display_name("ROCK'N'ROLL ACES"), "Rock'n'roll Aces")
        # One letter after it is a letter's plural or possessive, and a surname
        # has more. Only a player's name reaches it: the team rule keeps a word of
        # three letters or fewer whole.
        self.assertEqual(slugs.person_name("O'S"), "O's")

    def test_each_part_between_hyphens_and_full_stops_is_capitalised(self):
        self.assertEqual(slugs.display_name("NORTH-WEST ROCKETS"),
                         "North-West Rockets")
        self.assertEqual(slugs.display_name("MT.ORRIN ACES"), "Mt.Orrin Aces")
        self.assertEqual(slugs.display_name("O'BRIEN NORTH-WEST"),
                         "O'Brien North-West")


class TestAPlayersNamePrintedInCapitalsIsShownWithACapitalOnEveryPart(
        unittest.TestCase):
    """A shouted player's name is un-shouted whole: Bo Nakama, and not BO Nakama.

    A player's name went through ``display_name``, the team rule, which keeps a
    shouted word of three letters or fewer as a club's abbreviation and lowers a
    minor word, and so kept the given names Bo, Kit, Ros and Ana shouted and
    lowered an initial. Measured on the base (/tmp/t7/scratch/N1/names.py): BO
    NAKAMA gave ``BO Nakama``, KIT VASSE ``KIT Vasse``, ROS D'ARCY ``ROS D'arcy``,
    JO A SMITH ``JO a Smith``, TUI-MAE O'DOWELL ``Tui-mae O'dowell`` and PIM
    ARKWRIGHT-SOLE ``PIM Arkwright-sole``. Now ``person_name`` un-shouts every
    word of a name printed wholly in capitals by the team rule's one capitalising
    step, keeps no abbreviation and lowers no minor word, and leaves a name printed
    in mixed case to the team rule, as it was.

    Rejected: keeping a shouted word of three letters or fewer in a person's name
    as an abbreviation. Bo, Kit, Ros and Jo are given names, and a club's
    abbreviation belongs in a team's name. What that costs, a shouted TJ and DE, is
    pinned below, and ``[player_names]`` spells them.
    """

    def test_a_short_given_name_is_a_name_and_not_an_abbreviation(self):
        self.assertEqual(slugs.person_name("BO NAKAMA"), "Bo Nakama")
        self.assertEqual(slugs.person_name("KIT VASSE"), "Kit Vasse")
        self.assertEqual(slugs.person_name("ANA FERNANDEZ"), "Ana Fernandez")
        self.assertEqual(slugs.person_name("ROS D'ARCY"), "Ros D'Arcy")

    def test_every_part_after_a_hyphen_and_a_surname_s_prefix_is_capitalised(self):
        self.assertEqual(slugs.person_name("TUI-MAE O'DOWELL"), "Tui-Mae O'Dowell")
        self.assertEqual(slugs.person_name("PIM ARKWRIGHT-SOLE"),
                         "Pim Arkwright-Sole")
        self.assertEqual(slugs.person_name("ROS D'ARCY-VALE"), "Ros D'Arcy-Vale")

    def test_an_initial_is_a_capital_and_a_suffix_is_a_word(self):
        self.assertEqual(slugs.person_name("JO A SMITH"), "Jo A Smith")
        self.assertEqual(slugs.person_name("T.J. NAKAMA"), "T.J. Nakama")
        self.assertEqual(slugs.person_name("BO NAKAMA 2ND"), "Bo Nakama 2nd")

    def test_a_name_printed_in_mixed_case_is_taken_as_printed(self):
        for printed in ("Ana Fernandez", "Gil McAhearn", "Jo A Smith",
                        "Tui-Mae O'Dowell", "Hana de Varre"):
            with self.subTest(printed):
                self.assertEqual(slugs.person_name(printed), printed)
        # A longer shouted word in a name otherwise mixed is un-shouted alone, as
        # the team rule un-shouts it.
        self.assertEqual(slugs.person_name("Bo NAKAMA"), "Bo Nakama")

    def test_a_short_word_in_capitals_in_a_mixed_case_name_is_the_printer_s(self):
        """TJ Nakama on a card is TJ Nakama, and Bo Vasse III keeps the III.

        A name printed in mixed case is read by ``display_name``, as it was, because
        its printer chose the capitals and 4.144 made that rule the one answer to what
        they mean. Measured with ``person_name`` un-shouting every shouted word, in a
        mixed-case name too (/tmp/t7/scratch/N1/fix/shapes.py): TJ Nakama gave ``Tj
        Nakama``, Bo Vasse III ``Bo Vasse Iii`` and Kit JH Vasse ``Kit Jh Vasse``.
        Built with two roster names so printed (/tmp/t7/scratch/N1/mixed_probe.py),
        the players' provenance went from ``card`` to ``mixed``, and the review list
        offered both as names no card printed in mixed case.
        Rejected: keeping every short shouted word as printed, which gives ``Hana DE
        Varre`` where ``display_name`` lowers the minor word, a second answer to what
        the same capitals mean.
        """
        for printed in ("TJ Nakama", "Bo Vasse III", "Kit JH Vasse"):
            with self.subTest(printed):
                self.assertEqual(slugs.person_name(printed), printed)
        self.assertEqual(slugs.person_name("Hana DE Varre"), "Hana de Varre")

    def test_a_shouted_abbreviation_or_particle_is_the_known_cost(self):
        # Asserted so the reading is a decision on record: the card's capitals
        # cannot tell TJ from Tj, or a particle from a name.
        self.assertEqual(slugs.person_name("TJ NAKAMA"), "Tj Nakama")
        self.assertEqual(slugs.person_name("HANA DE VARRE"), "Hana De Varre")


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

    def test_a_forfeit_is_worth_every_rubber_as_a_love_set(self):
        # A set tiebreak is only played at 6-all, so a forfeit's set is 6-0 under
        # six-game-tiebreak, not 7-0: 24 games over four rubbers, as first to 6.
        for rule, worth in (("first-to-6", (4, 24)), ("six-game-tiebreak", (4, 24)),
                            ("advantage-6", (4, 24)), ("fast4", (4, 16)),
                            ("pro-8", (4, 32)), ("pro-8-tiebreak", (4, 32)),
                            ("pro-10", (4, 40))):
            with self.subTest(rule=rule):
                fmt = DSSD.with_set_rule(fmt_mod.set_rule(rule))
                self.assertEqual(fmt.forfeit_worth, worth)
        self.assertEqual(fmt_mod.from_rows([False] * 3).forfeit_worth, (3, 18))

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


class TestAShapeDeclaredByItsSlotsIsNamedAsACardOfThatShapeNamesIt(
        unittest.TestCase):
    """``from_slot_spec`` names a shape it has a name for by that name.

    Measured on the tree before this, by /tmp/t7/scratch/RU2/p189/probe.py: every
    one of the ten named shapes, declared by its slots, was named by its pattern,
    ``d1-s1-s2-d2`` for doubles-singles-doubles, and a build of the demo under
    ``[rules] slots`` printed that in the report's format row and rules line and
    wrote it to season.json, where ``[rules] format`` and a card read with no
    declaration at all wrote ``doubles-singles-doubles``. ``from_rows`` asks
    ``shape_name`` and ``from_slot_spec`` did not. Now both ask it. The name only
    labels: a card is checked against the declared slots, not the name.
    Rejected: leaving it, which calls one shape by two names on one page's
    history, as a club moves from reading the cards to pinning the slots.
    """

    def test_every_named_shape_declared_by_its_slots_is_called_by_its_name(self):
        for name, fmt in sorted(fmt_mod.FORMATS.items()):
            with self.subTest(shape=name):
                words = ["doubles" if slot.doubles else "singles"
                         for slot in fmt.slots]
                self.assertEqual(fmt_mod.from_slot_spec(words).name, name)
                self.assertEqual(fmt_mod.from_rows([slot.doubles for slot in
                                                    fmt.slots]).name, name)

    def test_a_shape_with_no_name_is_still_called_by_its_pattern(self):
        for words, pattern in ((["d", "d", "s"], "d1-d2-s1"),
                               (["doubles", "singles", "singles", "doubles",
                                 "singles"], "d1-s1-s2-d2-s3")):
            with self.subTest(pattern=pattern):
                self.assertEqual(fmt_mod.from_slot_spec(words).name, pattern)

    def test_a_name_the_caller_gives_is_the_name_it_keeps(self):
        self.assertEqual(fmt_mod.from_slot_spec(
            ["doubles", "singles", "singles", "doubles"], name="club-four").name,
            "club-four")


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


class TestAForfeitAwardWithNoRuleIsRefusedWithTheThreeThatHaveOne(
        unittest.TestCase):
    """A ruleset refused its ``forfeit_award`` lists the three as the config does.

    Measured on the tree before this, by /tmp/t7/scratch/RU2/p242/probe.py:
    ``forfeit_award="fixd"`` was refused ``forfeit_award must be one of
    ('as-played', 'full-games', 'fixed'), got 'fixd'``, a Python tuple and no
    question, the way ``[build] seasons`` was. No config line sets it, so this is
    said to whoever builds a ruleset in code, but in the words the loader's
    refusals use for a value no rule reads. Rejected: keeping the tuple for a
    message only code can reach, which is a second wording of one rule.
    """

    def test_each_is_refused_with_the_value_the_three_and_the_nearest(self):
        for award, meant in (("fixd", " Did you mean fixed?"),
                             ("as-playd", " Did you mean as-played?"),
                             ("forfeit", "")):
            with self.subTest(award=award):
                with self.assertRaises(scoring.ScoringError) as caught:
                    scoring.Ruleset(name="x", win=1, draw=0, loss=0,
                                    forfeit_award=award)
                self.assertEqual(str(caught.exception),
                                 f'forfeit_award = "{award}" is not "as-played", '
                                 f'"full-games" or "fixed".{meant}')


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

    def test_a_rubber_both_sides_forfeited_goes_by_the_games_printed_on_it(self):
        # Both boxes ticked says it was forfeited and not by whom, so the printed
        # scoreline is the only evidence of a result left on the row -- and on a
        # card filled in as played, that is where the answer is. Read back off a
        # card, 6-0 with both boxes ticked came out home before ticks decided
        # anything, and still does. Crediting nobody whatever the games say would
        # lose a set somebody won, over one stray tick on a rubber that was played.
        both = SetResult(slot=DSSD.slots[0], home_games=6, away_games=0,
                         status=SetStatus.FORFEIT, home_forfeit=True,
                         away_forfeit=True, rule=FIRST_TO_6)
        self.assertTrue(both.completed)
        self.assertEqual(both.winner, "home")

    def test_a_rubber_nobody_came_for_goes_to_the_side_that_did(self):
        """0-0 with one Forfeit box ticked: the case that loses a set.

        A rubber one side did not turn up for has no games to print, so a winner
        read off the games alone credits nobody -- and the side that turned up
        finishes a set short of what its own card's TOTAL row says, which the
        totals cross-check then reports as a misread card.
        """
        forfeit = SetResult(slot=DSSD.slots[0], home_games=0, away_games=0,
                            status=SetStatus.FORFEIT, away_forfeit=True,
                            rule=FIRST_TO_6)
        self.assertTrue(forfeit.completed)
        self.assertEqual(forfeit.winner, "home")

    def test_the_ticked_box_decides_whatever_the_games_say(self):
        # A row that ticks one side's Forfeit box and prints the games in that
        # side's favour contradicts itself, and the box is the half of it that is
        # about forfeiting: same rule as a whole-match forfeit, where `Match.winner`
        # takes the result off the tick. The validator reports the row, once, as
        # `set-forfeit-contradicted`, whatever the TOTAL row says; a TOTAL row
        # counted off the tick agrees with the set rows, so it reports nothing.
        odd = SetResult(slot=DSSD.slots[0], home_games=6, away_games=0,
                        status=SetStatus.FORFEIT, home_forfeit=True,
                        rule=FIRST_TO_6)
        self.assertEqual(odd.winner, "away")

    def test_a_rubber_both_sides_forfeited_credits_nobody(self):
        # Both boxes ticked names nobody, and 0-0 leaves nothing to fall back on.
        # The pair of ticks is kept as the card printed them rather than reduced to
        # a forfeiting side, because this is the row that has no such side.
        both = SetResult(slot=DSSD.slots[0], home_games=0, away_games=0,
                         status=SetStatus.FORFEIT, home_forfeit=True,
                         away_forfeit=True, rule=FIRST_TO_6)
        self.assertTrue(both.completed)
        self.assertIsNone(both.winner)

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

    def test_a_forfeit_names_no_basis_for_its_result(self):
        # `decided_by` is the reason the page gives a reader for a winner, and a
        # forfeit's winner comes off a ticked box rather than off anything either
        # side did on court. Read the other way round -- the side in front on the
        # card forfeiting -- naming games as the basis would name the loser.
        odd = make_match(status=MatchStatus.FORFEIT, forfeited_by="home")
        self.assertEqual(odd.winner, "away")
        self.assertGreater(odd.home_games, odd.away_games)
        self.assertIsNone(odd.decided_by)

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


class TestUnderRubbersAWholeMatchForfeitPaysTheSideThatCameEveryRubber(
        unittest.TestCase):
    """Under ``rubbers``, a whole-match forfeit is worth every rubber of the format.

    Measured on the tree before this, on the filed card: rows left 0-0, and Match
    Forfeited ticked against the away side. It paid 4.0/2.0 under games-bonus,
    2.0/0.0 under sets-first and 3.0/0.0 under simple, and 0.0/0.0 under rubbers.
    The other three presets pay the outcome, and the box decides the outcome.
    Rubbers pays only the sets won, and nobody wins a set on an empty row, so the
    side that turned up was paid what the side that stayed at home was. The same
    night written the other way, every rubber ticked Forfeit against the away side
    and the box left unticked, paid 4.0/0.0. And a card with the rows filled in
    for the side that forfeited paid that side 4.0.

    ``Match.points_for`` is the one place a whole-match forfeit is priced. A
    points-are-sets-won scheme's price for one was already written there, in
    ``Ruleset.forfeit_win_points`` and ``forfeit_loss_points``: every rubber of the
    format to the side that turned up, and none to the side that did not. It was
    reached only when ``forfeit_award`` is not ``as-played``, which no preset
    sets. Now such a scheme reaches it for every whole-match forfeit.

    Rejected: 0.0/0.0, which pays the side that turned up nothing. Also rejected: a
    setting for what a forfeit is worth. This is a defect in one scheme, and no
    club can set ``forfeit_award`` today. (4.420 later made it a ``[rules]``
    setting, ``full-games`` by default, which pays these the same.)
    """

    RUBBERS = scoring.ruleset("rubbers")
    EMPTY = ((0, 0),) * 4

    def paid(self, match: Match) -> tuple[float | None, float | None]:
        return match.points_for("rockets"), match.points_for("iguanas")

    def boxed(self, scores=EMPTY, *, rules=RUBBERS, fmt=DSSD, by="away") -> Match:
        return make_match(scores, rules=rules, fmt=fmt,
                          status=MatchStatus.FORFEIT, forfeited_by=by)

    def test_the_side_that_turned_up_is_paid_every_rubber(self):
        self.assertEqual(self.paid(self.boxed()), (4.0, 0.0))

    def test_every_rubber_is_the_count_of_the_matchs_own_format(self):
        five = fmt_mod.match_format("mixed-5")
        self.assertEqual(self.paid(self.boxed(((0, 0),) * 5, fmt=five, by="home")),
                         (0.0, 5.0))

    def test_rows_filled_in_for_the_forfeiter_pay_the_forfeiter_nothing(self):
        filled = ((6, 2), (6, 3), (6, 4), (6, 1))
        self.assertEqual(self.paid(self.boxed(filled, by="home")), (0.0, 4.0))

    def test_both_tellings_of_one_night_are_paid_alike_under_every_preset(self):
        conceded = tuple(dataclasses.replace(s, status=SetStatus.FORFEIT,
                                             away_forfeit=True)
                         for s in make_sets(self.EMPTY))
        for name, rules in scoring.RULESETS.items():
            with self.subTest(preset=name):
                told = Match(competition="test", round="1", home="rockets",
                             away="iguanas", fmt=DSSD, rules=rules, sets=conceded)
                self.assertEqual(self.paid(self.boxed(rules=rules)),
                                 self.paid(told))

    def test_a_played_match_is_still_paid_the_sets_it_won(self):
        self.assertEqual(self.paid(make_match(rules=self.RUBBERS)), (3.0, 1.0))


class TestARubbersSchemeSaysWhatItPaysAMatchForfeitedWhole(unittest.TestCase):
    """A scheme whose points are the sets won says what a whole-match forfeit pays.

    Measured on the tree before this, by /tmp/t7/scratch/RU2/p136/probe.py: the
    filed card, Match Forfeited ticked against the away side and every row 0-0,
    built under ``rubbers`` at exit 0 and was paid 4.0/0.0 with 0 sets each, and
    the same build described the scheme as ``points = sets won; decided on sets
    then games``, in the report, in season.json's ``scoring_detail`` and so on the
    page. By that sentence the side that turned up won no set and was paid nothing.
    ``Ruleset.describe`` is the one sentence every one of those prints, and the
    rendered config.toml's option table too.

    Now such a scheme adds what its forfeit fields pay, which is every rubber
    unless ``forfeit_award`` is ``fixed``, whose points it names. A scheme that pays
    the outcome says what it said: a forfeit is a win and a loss there, and the
    sentence already prices both. Rejected: leaving the words, which kept the bytes
    of every rubbers site and a sentence on each that its own ladder contradicts.
    """

    FORFEIT = ("a match forfeited whole pays the side that turned up every rubber")

    def sets_won(self, **fields) -> scoring.Ruleset:
        return scoring.Ruleset(**{"name": "x", "win": 1.0, "draw": 0.0, "loss": 0.0,
                                  "points_are_sets_won": True,
                                  "decide_by": ("sets", "games"), **fields})

    def test_rubbers_says_the_side_that_turned_up_is_paid_every_rubber(self):
        self.assertEqual(scoring.ruleset("rubbers").describe(),
                         f"points = sets won; {self.FORFEIT}; decided on sets then "
                         f"games")

    def test_a_fixed_award_is_said_as_the_points_it_pays(self):
        # 1.5 is not one point for being one and a bit, and 0 is a price, not a
        # missing one: forfeit_win_points pays it, not every rubber.
        for points, said in ((3.0, "3 points"), (1.0, "1 point"),
                             (0.5, "0.5 points"), (1.5, "1.5 points"),
                             (0.0, "0 points")):
            with self.subTest(points=points):
                rules = self.sets_won(forfeit_award="fixed", forfeit_points=points)
                self.assertEqual(rules.forfeit_win_points(DSSD), points)
                self.assertEqual(
                    rules.describe(),
                    f"points = sets won; a match forfeited whole pays the side that "
                    f"turned up {said}; decided on sets then games")

    def test_a_full_games_award_still_pays_every_rubber_and_says_so(self):
        rules = self.sets_won(forfeit_award="full-games")
        self.assertEqual(rules.forfeit_win_points(DSSD), 4.0)
        self.assertEqual(rules.describe(), f"points = sets won; {self.FORFEIT}; "
                                           f"decided on sets then games")

    def test_the_forfeit_is_said_after_the_rest_of_the_scheme(self):
        rules = self.sets_won(draw=None)
        self.assertEqual(rules.describe(), f"points = sets won, no draw; "
                                           f"{self.FORFEIT}; decided on sets then "
                                           f"games")

    def test_a_scheme_that_pays_the_outcome_says_what_it_said(self):
        for name, said in (
                ("games-bonus", "win 4, draw 3, loss 2, 0.1 per game; decided on "
                                "games then sets"),
                ("sets-first", "win 2, draw 1, loss 0; decided on sets then games"),
                ("simple", "win 3, draw 1, loss 0; decided on sets then games")):
            # 4.420 moved this: what it said is said under as-played alone, the
            # award that pays a forfeit as a played match, since full-games pays
            # the side that gave the match up nothing and now says so
            # (TestAForfeitPaidOtherwiseThanAsPlayedIsSaidBesideTheScheme).
            for award in ("as-played",):
                with self.subTest(preset=name, award=award):
                    self.assertEqual(dataclasses.replace(
                        scoring.ruleset(name), forfeit_award=award).describe(),
                        said)


class TestAForfeitPaidOtherwiseThanAsPlayedIsSaidBesideTheScheme(unittest.TestCase):
    """``Ruleset.describe`` says what a match forfeited whole pays, unless as played.

    4.305: measured on the tree before this (/tmp/mcf/scratch/CANCEL/probe/
    describe.py, output describe-before.txt), games-bonus paid a forfeit 5.0 under
    ``fixed`` at 5 points and 6.4 under ``full-games``, and described itself both
    times as "win 4, draw 3, loss 2, 0.1 per game; decided on games then sets",
    the sentence it has as played, where it pays 4.0; sets-first and simple said
    nothing of a fixed 5 points either. Only a scheme paying the sets won had a
    forfeit clause. The sentence is the one the run report, season.json's
    ``scoring_detail``, the page and config.toml's option table print, and with
    ``full-games`` every preset's award (4.420) a scheme saying nothing would say
    a forfeiter is paid a loss it is not paid.

    Now the clause is written whenever the award is not ``as-played`` or the
    points are the sets won: a fixed award as its points, ``full-games`` as a win,
    with every rubber a love set where the scheme pays games or sets on top, and,
    where the scheme's loss is not 0, what the side that gave the match up is paid.
    Rejected: writing it only where the price differs from a played match's, which
    makes the sentence depend on arithmetic a club cannot see, so that sets-first
    and simple would say nothing of an award they apply.
    """

    WHOLE = "a match forfeited whole pays the side that turned up"

    def test_each_preset_says_what_its_forfeit_pays(self):
        for name, said in (
                ("games-bonus", f"win 4, draw 3, loss 2, 0.1 per game; {self.WHOLE} "
                                f"a win with every rubber a love set, and the side "
                                f"that gave it up 0; decided on games then sets"),
                ("sets-first", f"win 2, draw 1, loss 0; {self.WHOLE} a win; "
                               f"decided on sets then games"),
                ("rubbers", f"points = sets won; {self.WHOLE} every rubber; "
                            f"decided on sets then games"),
                ("simple", f"win 3, draw 1, loss 0; {self.WHOLE} a win; decided "
                           f"on sets then games")):
            with self.subTest(preset=name):
                self.assertEqual(scoring.ruleset(name).describe(), said)

    def test_a_fixed_award_is_said_as_its_points_under_every_preset(self):
        for name, said in (
                ("games-bonus", f"win 4, draw 3, loss 2, 0.1 per game; {self.WHOLE} "
                                f"3 points, and the side that gave it up 0; "
                                f"decided on games then sets"),
                ("sets-first", f"win 2, draw 1, loss 0; {self.WHOLE} 3 points; "
                               f"decided on sets then games"),
                ("rubbers", f"points = sets won; {self.WHOLE} 3 points; decided "
                            f"on sets then games"),
                ("simple", f"win 3, draw 1, loss 0; {self.WHOLE} 3 points; "
                           f"decided on sets then games")):
            with self.subTest(preset=name):
                rules = dataclasses.replace(scoring.ruleset(name),
                                            forfeit_award="fixed",
                                            forfeit_points=3.0)
                self.assertEqual(rules.forfeit_win_points(DSSD), 3.0)
                self.assertEqual(rules.describe(), said)

    def test_a_scheme_paying_a_set_bonus_says_every_rubber_a_love_set_too(self):
        rules = scoring.Ruleset(name="x", win=2.0, draw=1.0, loss=1.0,
                                set_bonus=0.5, decide_by=("sets", "games"))
        self.assertEqual(rules.describe(),
                         f"win 2, draw 1, loss 1, 0.5 per set; {self.WHOLE} a win "
                         f"with every rubber a love set, and the side that gave it "
                         f"up 0; decided on sets then games")
        self.assertEqual(rules.forfeit_win_points(DSSD), 4.0)

    def test_as_played_says_nothing_of_a_forfeit_but_where_sets_won_are_paid(self):
        for name in scoring.RULESETS:
            rules = dataclasses.replace(scoring.ruleset(name),
                                        forfeit_award="as-played")
            with self.subTest(preset=name):
                self.assertEqual(self.WHOLE in rules.describe(),
                                 rules.points_are_sets_won)


class TestAMatchGivenUpWholeIsPaidWhatTheConfigSaysAForfeitIsWorth(
        unittest.TestCase):
    """What a whole-match forfeit pays is the scheme's ``forfeit_award``, never a card's.

    Measured on the tree before this (/tmp/mcf/scratch/CANCEL/probe/describe.py,
    output describe-before.txt; and probe.py's box-forfeit, rows-ticked and
    box-forfeit-over-play cases beside the season through build.py): every
    preset's ``forfeit_award`` was ``as-played``, so a Match Forfeited box over
    rows left 0-0 paid 4.0/2.0 under games-bonus, 2.0/0.0 under sets-first,
    4.0/0.0 under rubbers and 3.0/0.0 under simple, and the box over 6-2, 6-3, 2-6
    and 6-1 paid 6.0/3.2 under games-bonus, the side that gave the match up being
    paid more than a loss. Under ``full-games`` the box paid 6.4 and still 2.0, as
    ``forfeit_loss_points`` paid the scheme's loss whatever the award, against the
    FORFEIT_AWARDS comment's "the forfeiter nothing"; and the night told by its
    rows alone, every row 0-0 and ticked Forfeit against the away side, paid
    4.0/2.0 under every award, as the played match its status says. The owner's
    club pays a forfeit 6.4 to the side that came and 0 to the side that gave it
    up, which is ``forfeit_win_points`` under ``full-games`` over four first-to-6
    rubbers, and Match Centre's own TOTAL row of a walkover is that award.

    Now every preset's award is ``full-games``, the side that gave the match up is
    paid nothing outside ``as-played``, and one reading of who gave it up,
    ``Match.given_up_by``, answers for the box, for a walkover's card (4.418,
    whose match the box's status carries) and for the night told by its rows, so
    the three tellings of 4.33 are paid alike under every award. Rejected:
    keeping ``as-played`` the default and adding only the key, which leaves the
    owner's walkovers paid 2.4 short and their forfeiters 2.0 over until the club
    finds the line; pricing from the TOTAL row, which a card may leave out and
    which 4.135 holds only as a cross-check; and a ``forfeit_loss`` key, which no
    club on record asks for.
    """

    EMPTY = ((0, 0),) * 4
    OVER_PLAY = ((6, 2), (6, 3), (2, 6), (6, 1))
    AWARDS = {"as-played": None, "full-games": None, "fixed": 5.0}
    # (preset, award) -> paid (home, away) when the away side gives the night up,
    # told by the box or by its rows; then the box over OVER_PLAY.
    PRICES = {
        ("games-bonus", "as-played"): ((4.0, 2.0), (6.0, 3.2)),
        ("games-bonus", "full-games"): ((6.4, 0.0), (6.4, 0.0)),
        ("games-bonus", "fixed"): ((5.0, 0.0), (5.0, 0.0)),
        ("sets-first", "as-played"): ((2.0, 0.0), (2.0, 0.0)),
        ("sets-first", "full-games"): ((2.0, 0.0), (2.0, 0.0)),
        ("sets-first", "fixed"): ((5.0, 0.0), (5.0, 0.0)),
        ("rubbers", "as-played"): ((4.0, 0.0), (4.0, 0.0)),
        ("rubbers", "full-games"): ((4.0, 0.0), (4.0, 0.0)),
        ("rubbers", "fixed"): ((5.0, 0.0), (5.0, 0.0)),
        ("simple", "as-played"): ((3.0, 0.0), (3.0, 0.0)),
        ("simple", "full-games"): ((3.0, 0.0), (3.0, 0.0)),
        ("simple", "fixed"): ((5.0, 0.0), (5.0, 0.0)),
    }

    @staticmethod
    def paid(match: Match) -> tuple[float | None, float | None]:
        home, away = match.points_for("rockets"), match.points_for("iguanas")
        return (None if home is None else round(home, 6),
                None if away is None else round(away, 6))

    @classmethod
    def priced(cls, name: str, award: str) -> scoring.Ruleset:
        return dataclasses.replace(scoring.ruleset(name), forfeit_award=award,
                                   forfeit_points=cls.AWARDS[award])

    def boxed(self, rules, scores=EMPTY, by="away") -> Match:
        return make_match(scores, rules=rules, status=MatchStatus.FORFEIT,
                          forfeited_by=by)

    def told(self, rules, by="away") -> Match:
        return dataclasses.replace(make_match(self.EMPTY, rules=rules), sets=tuple(
            dataclasses.replace(s, status=SetStatus.FORFEIT,
                                **{f"{by}_forfeit": True})
            for s in make_sets(self.EMPTY)))

    def test_every_preset_pays_a_forfeit_in_full_unless_the_club_says_otherwise(self):
        self.assertEqual({name: rules.forfeit_award
                          for name, rules in scoring.RULESETS.items()},
                         dict.fromkeys(scoring.RULESETS, "full-games"))
        self.assertEqual(scoring.Ruleset(name="x", win=1, draw=0, loss=0)
                         .forfeit_award, "full-games")

    def test_the_price_under_each_preset_and_award_is_one_however_it_is_told(self):
        self.assertEqual(set(self.PRICES),
                         set(itertools.product(scoring.RULESETS, self.AWARDS)))
        for (name, award), (empty, over_play) in self.PRICES.items():
            rules = self.priced(name, award)
            with self.subTest(preset=name, award=award):
                self.assertEqual(self.paid(self.boxed(rules)), empty)
                self.assertEqual(self.paid(self.told(rules)), empty)
                self.assertEqual(self.paid(self.boxed(rules, self.OVER_PLAY)),
                                 over_play)

    def test_the_home_side_giving_it_up_is_paid_the_mirror(self):
        for (name, award), (empty, _) in self.PRICES.items():
            rules = self.priced(name, award)
            with self.subTest(preset=name, award=award):
                self.assertEqual(self.paid(self.boxed(rules, by="home")),
                                 empty[::-1])
                self.assertEqual(self.paid(self.told(rules, by="home")),
                                 empty[::-1])

    def test_the_side_that_gave_it_up_is_paid_a_loss_only_as_played(self):
        for name in scoring.RULESETS:
            for award in self.AWARDS:
                rules = self.priced(name, award)
                with self.subTest(preset=name, award=award):
                    self.assertEqual(
                        rules.forfeit_loss_points(DSSD),
                        rules.loss if award == "as-played"
                        and not rules.points_are_sets_won else 0.0)

    def test_a_night_neither_box_nor_rows_give_up_is_paid_as_played(self):
        # One row left 0-0 and ticked Forfeit, three played: a rubber given up, not
        # the match, though the one row alone says what a forfeit's rows say.
        rows = make_sets(((0, 0),) + self.OVER_PLAY[1:])
        rows = (dataclasses.replace(rows[0], status=SetStatus.FORFEIT,
                                    away_forfeit=True),) + rows[1:]
        for name in scoring.RULESETS:
            rules = self.priced(name, "full-games")
            with self.subTest(preset=name):
                match = dataclasses.replace(make_match(rules=rules), sets=rows)
                self.assertIsNone(match.given_up_by)
                self.assertEqual(self.paid(match), (
                    rules.points(outcome="win", games_won=match.games_for("rockets"),
                                 sets_won=match.sets_for("rockets")),
                    rules.points(outcome="loss",
                                 games_won=match.games_for("iguanas"),
                                 sets_won=match.sets_for("iguanas"))))


class TestWhoGaveAMatchUpIsOneReadingOfEveryTellingOfIt(unittest.TestCase):
    """``Match.given_up_by``: the side that gave the whole match up, however told.

    4.33 found three tellings of one night: the Match Forfeited box over rows left
    0-0; the box over rows naming nobody; and no box, every row 0-0 and ticked
    Forfeit against the one side. 4.418 adds a fourth, the walkover Match Centre
    writes as Match Cancelled beside a winner and the award, which discover
    builds with the box's status. Before this the box was read by status alone,
    so the night told by its rows was priced as played (the class above
    measures it) and asked for the roster of the side that never came (4.288).
    Now one derived reading answers for all of them, and the status and
    ``forfeited_by`` stay as the card printed them. Rejected: promoting the rows'
    telling to status FORFEIT, which records a box nobody ticked (4.15).
    """

    EMPTY = ((0, 0),) * 4

    def told(self, *flags, scores=EMPTY, status=MatchStatus.PLAYED) -> Match:
        """A night whose rows are ticked Forfeit, row by row, against *flags*."""
        rows = tuple(dataclasses.replace(
            s, status=SetStatus.FORFEIT, home_forfeit="home" in flag,
            away_forfeit="away" in flag)
            for s, flag in zip(make_sets(scores), flags))
        return dataclasses.replace(make_match(scores, status=status), sets=rows)

    def test_the_box_names_the_side_that_gave_it_up(self):
        for side in ("home", "away"):
            with self.subTest(side=side):
                match = make_match(self.EMPTY, status=MatchStatus.FORFEIT,
                                   forfeited_by=side)
                self.assertEqual(match.given_up_by, side)

    def test_a_box_naming_no_one_side_names_nobody(self):
        for by in (None, "both"):
            with self.subTest(by=by):
                self.assertIsNone(make_match(self.EMPTY, status=MatchStatus.FORFEIT,
                                             forfeited_by=by).given_up_by)

    def test_rows_each_left_empty_and_ticked_against_one_side_name_it(self):
        for side in ("home", "away"):
            with self.subTest(side=side):
                match = self.told(*([(side,)] * 4))
                self.assertEqual(match.given_up_by, side)
                self.assertIs(match.status, MatchStatus.PLAYED)
                self.assertIsNone(match.forfeited_by)

    def test_rows_that_do_not_all_say_so_name_nobody(self):
        for name, match in (
                ("one row ticked against the other side",
                 self.told(("away",), ("away",), ("away",), ("home",))),
                ("one row ticked against both", self.told(
                    ("away",), ("away",), ("away",), ("home", "away"))),
                ("every row ticked against both",
                 self.told(*([("home", "away")] * 4))),
                ("every row ticked against nobody", self.told(*([()] * 4))),
                ("one row not ticked", dataclasses.replace(
                    self.told(*([("away",)] * 4)), sets=self.told(
                        *([("away",)] * 4)).sets[:3] + make_sets(self.EMPTY)[3:])),
                ("rows ticked over games", self.told(
                    *([("away",)] * 4), scores=((6, 0),) * 4)),
                ("a washout", self.told(*([("away",)] * 4),
                                        status=MatchStatus.CANCELLED)),
                ("a played night", make_match())):
            with self.subTest(night=name):
                self.assertIsNone(match.given_up_by)


class TestAWholeMatchForfeitGoesToTheSideTheBoxDoesNotName(unittest.TestCase):
    """``forfeit_match_winner``: who a whole-match forfeit went to, in one place.

    The rule was written four times: in ``Match.winner``, in validate's check that
    holds a forfeit's verb to the box, and in the reader's verb and Match Winner
    tick cross-checks, each as ``other_side`` of the box behind a test of its own
    that the box names one side. The four agreed, on every card, which is why no
    card could tell them apart and why the first sign of one changing would have
    been the report contradicting the ladder. Each now asks this function; the
    reader's and validate's callers are spied on beside their own cards.

    Rejected: leaving the copies because they agree today.
    """

    def test_the_side_the_box_does_not_name_is_the_winner(self):
        self.assertEqual([forfeit_match_winner(side) for side in ("home", "away")],
                         ["away", "home"])

    def test_a_box_naming_no_one_side_gives_nobody_the_match(self):
        for side in (None, "both", ""):
            with self.subTest(side=side):
                self.assertIsNone(forfeit_match_winner(side))

    def test_the_model_asks_it_who_won(self):
        match = make_match(((0, 0),) * 4, status=MatchStatus.FORFEIT,
                           forfeited_by="away")
        with mock.patch.object(model_mod, "forfeit_match_winner",
                               wraps=forfeit_match_winner) as rule:
            self.assertEqual(match.winner, "home")
        rule.assert_called_once_with("away")


class TestAMatchTheModelDoesNotCountDecidesNothing(unittest.TestCase):
    """A result is only ever taken from a match whose numbers count.

    :attr:`Match.decided` kept its own copy of the three statuses
    :attr:`Match.counts_as_played` leaves out, one screen below it in the same
    class, so the model answered "did this happen" twice. Measured, the two agreed
    on all 216 combinations of status, scoreline, forfeit tick, Disputed box and
    opponent. Parted by one status in a copy of the tree -- ``cancelled`` taken out
    of ``counts_as_played`` and not out of the copy -- the washout's 12-3 went into
    the ladder's games column, Rockets 94-28 to 106-31 and Aces 37-65 to 40-77,
    while both points totals stayed where they were: one card, counted by one
    column and not by the one beside it.

    And ``decided`` ruled on a forfeit before it asked anything, before the change
    and in the first version of it, so a forfeit's result came from its ticked box
    whatever the rule said. Parted on ``forfeit``, Rockets kept four wins and 25.4
    points while the forfeit's 24-0 left their games, 94-28 to 70-28. The rule is
    now asked first, forfeit included, and parted the same way the forfeit leaves
    every column at once: 3 played, 19.0 points, 70-28.

    The test failed first on the tree before the change, ``True is not false`` on
    the played night and the forfeit and ``False is not true`` on the washout, and
    again on the first version of the change, on the forfeit alone.

    Rejected: a test that the two tuples are equal. It holds them level without
    making them one, and the next status added has two places to go.
    """

    def test_decided_asks_counts_as_played_rather_than_keeping_a_list_of_its_own(self):
        # The model's answer turned round both ways; `decided` has to follow it. A
        # forfeit with a side ticked is on the "not counted" side as well as a
        # played night, because its result comes from the box and not from the
        # sets, so a branch of its own is the one place the rule could go unasked.
        with mock.patch.object(Match, "counts_as_played",
                               new_callable=mock.PropertyMock, return_value=False):
            for match in (make_match(),
                          make_match(status=MatchStatus.FORFEIT, forfeited_by="away")):
                with self.subTest(status=match.status.value):
                    self.assertFalse(match.decided)
                    self.assertIsNone(match.points_for("rockets"))
        with mock.patch.object(Match, "counts_as_played",
                               new_callable=mock.PropertyMock, return_value=True):
            washout = make_match(status=MatchStatus.CANCELLED)
            self.assertTrue(washout.decided)
            self.assertEqual(washout.winner, "home")


class TestOnlyAPlayedMatchCanBeShortOfAResult(unittest.TestCase):
    """``Match.short_of_result``: played, and undecided only for want of sets.

    Nothing in the model named this. ``not decided`` is true of it, and also of a
    bye, a washout, an unplayed card and a forfeit nobody is ticked for, and none of
    those would get a result from a lower ``min_sets_for_result``. The review list
    asks about that number only on a match this answers for, and validate words its
    no-result warning by it, so the two cannot disagree about which matches are the
    number's. Measured before this: a disputed card 2 sets of 3 short was "recorded
    as disputed", which is a status and not the reason.

    Rejected: ``status is PLAYED and not decided``, which is what validate asked,
    and which is false of the disputed card that is short.
    """

    def match(self, finished: int, **fields) -> Match:
        """Four sets, ``finished`` of them finished 6-1 and the rest left at 3-2."""
        return make_match(((6, 1),) * finished + ((3, 2),) * (4 - finished),
                          **fields)

    def test_one_set_short_of_the_number_is_short_and_the_number_is_not(self):
        need = DSSD.min_sets_for_result
        for status in (MatchStatus.PLAYED, MatchStatus.DISPUTED):
            with self.subTest(status=status.value):
                self.assertTrue(self.match(need - 1, status=status).short_of_result)
                self.assertFalse(self.match(need, status=status).short_of_result)

    def test_a_match_that_did_not_happen_is_never_short_whatever_its_card_holds(self):
        for status in (MatchStatus.CANCELLED, MatchStatus.UNPLAYED, MatchStatus.BYE):
            with self.subTest(status=status.value):
                self.assertFalse(self.match(2, status=status).short_of_result)

    def test_a_forfeit_is_never_short_whoever_its_box_names(self):
        for side in ("home", "away", None):
            with self.subTest(forfeited_by=side):
                self.assertFalse(self.match(2, status=MatchStatus.FORFEIT,
                                            forfeited_by=side).short_of_result)


class TestASetStoppedWithNoBoxTickedIsOneRule(unittest.TestCase):
    """``SetResult.unfinished_unmarked``: a set that did not finish, and no box why.

    validate's set checks asked it inline, as a status test and then three
    branches, and the review list's sets-first clause needs the same count. Two
    copies of one rule would be two answers the first time a status is added.

    Rejected: ``not completed`` alone, which is true of a retired set, and a
    retired set is one the card has already explained.
    """

    def row(self, home: int, away: int, status=SetStatus.PLAYED) -> SetResult:
        return make_sets(((home, away),), status=status)[0]

    def test_a_played_row_short_of_its_rule_is_unfinished_and_unmarked(self):
        for games in ((4, 3), (5, 5), (0, 0)):
            with self.subTest(games=games):
                self.assertTrue(self.row(*games).unfinished_unmarked)
        self.assertFalse(self.row(6, 3).unfinished_unmarked)

    def test_a_row_whose_box_says_why_is_never_unmarked_whatever_its_games(self):
        for status in (SetStatus.RETIRED, SetStatus.INCOMPLETE, SetStatus.FORFEIT):
            for games in ((4, 3), (6, 3), (0, 0)):
                with self.subTest(status=status.value, games=games):
                    self.assertFalse(self.row(*games, status=status)
                                     .unfinished_unmarked)


class TestARubberNotPlayedOutIsScoredByOneRuleBesideWhoWonIt(unittest.TestCase):
    """``model.rubber_award``: the side and the games a rubber not played out scores.

    4.421: a rubber ticked Forfeit against one side kept the games printed on its
    row, so under ``games-bonus``, decided on games, the side whose rubbers were
    awarded against it won the night on the games it won on court in them.
    Measured (``/tmp/mcf/scratch/RUBBER/probe/before.txt``, case A): rows 2-6,
    6-2, 1-6, 3-6, the away box ticked on three, were scored away 12-20 and paid
    3.2 to 6.0, where the card's TOTAL row, Match Centre's own arithmetic, says
    sets 4-0 and games 24-2. 4.422: a rubber whose Retired box is ticked went to
    nobody at its printed games, where Match Centre gives the side that did not
    retire the set, its games raised to the set's ``games_to_win``, and leaves the
    retiring side its own. One function prices both, beside
    ``forfeit_set_winner``, so discover, validate, the reader and the writer ask
    one rule of a row and of a match given up whole.

    Rejected: crediting a retired set to the side ahead, since a real card's home
    side retired leading 4-2 and the away side was given the set; raising a
    retired rubber's games under ``as-played``, whose point is that the printed
    games stand; and keeping a match given up whole at the games its rows print,
    since Match Centre totals such a night 24-0 even over rows that hold play.
    """

    def award(self, status, games, **flags):
        return model_mod.rubber_award(status=status, home_games=games[0],
                                      away_games=games[1], **flags)

    def test_a_rubber_ticked_against_one_side_is_a_love_set_to_the_side_that_came(
            self):
        for gtw in (6, 4, 8, 10):
            for award in ("full-games", "fixed"):
                with self.subTest(games_to_win=gtw, award=award):
                    self.assertEqual(
                        self.award(SetStatus.FORFEIT, (2, 6), away_forfeit=True,
                                   games_to_win=gtw, award=award),
                        ("home", gtw, 0))
                    self.assertEqual(
                        self.award(SetStatus.FORFEIT, (6, 2), home_forfeit=True,
                                   games_to_win=gtw, award=award),
                        ("away", 0, gtw))

    def test_as_played_names_the_side_that_came_at_the_printed_games(self):
        self.assertEqual(self.award(SetStatus.FORFEIT, (2, 6), away_forfeit=True,
                                    games_to_win=6, award="as-played"),
                         ("home", 2, 6))
        self.assertEqual(self.award(SetStatus.FORFEIT, (0, 0), home_forfeit=True,
                                    games_to_win=6, award="as-played"),
                         ("away", 0, 0))

    def test_a_rubber_ticked_against_both_sides_or_neither_keeps_its_games(self):
        for award in ("full-games", "fixed", "as-played"):
            for ticks in ({"home_forfeit": True, "away_forfeit": True}, {}):
                with self.subTest(award=award, ticks=ticks):
                    self.assertIsNone(self.award(SetStatus.FORFEIT, (2, 6),
                                                 games_to_win=6, award=award,
                                                 **ticks))

    def test_a_retired_rubber_goes_to_the_side_that_stayed_raised_to_win_it(self):
        for games, flags, gtw, scored in (
                ((5, 1), {"away_retired": True}, 6, ("home", 6, 1)),
                ((4, 2), {"home_retired": True}, 6, ("away", 4, 6)),
                ((0, 0), {"away_retired": True}, 6, ("home", 6, 0)),
                ((5, 3), {"home_retired": True}, 6, ("away", 5, 6)),
                ((7, 6), {"away_retired": True}, 6, ("home", 7, 6)),
                ((3, 1), {"away_retired": True}, 4, ("home", 4, 1)),
                ((1, 7), {"home_retired": True}, 8, ("away", 1, 8))):
            for award in ("full-games", "fixed"):
                with self.subTest(games=games, flags=flags, award=award):
                    self.assertEqual(self.award(SetStatus.RETIRED, games,
                                                games_to_win=gtw, award=award,
                                                **flags), scored)

    def test_as_played_gives_a_retired_rubber_to_the_side_that_stayed_as_printed(
            self):
        self.assertEqual(self.award(SetStatus.RETIRED, (4, 2), home_retired=True,
                                    games_to_win=6, award="as-played"),
                         ("away", 4, 2))
        self.assertEqual(self.award(SetStatus.RETIRED, (5, 1), away_retired=True,
                                    games_to_win=6, award="as-played"),
                         ("home", 5, 1))

    def test_a_retired_rubber_with_both_boxes_or_neither_is_nobodys(self):
        for award in ("full-games", "fixed", "as-played"):
            for boxes in ({"home_retired": True, "away_retired": True}, {}):
                with self.subTest(award=award, boxes=boxes):
                    self.assertIsNone(self.award(SetStatus.RETIRED, (5, 1),
                                                 games_to_win=6, award=award,
                                                 **boxes))

    def test_a_rubber_played_or_left_incomplete_is_not_priced(self):
        for status, games in ((SetStatus.PLAYED, (6, 2)),
                              (SetStatus.INCOMPLETE, (5, 1))):
            for award in ("full-games", "as-played"):
                with self.subTest(status=status.value, award=award):
                    self.assertIsNone(self.award(status, games, games_to_win=6,
                                                 award=award))

    def test_a_match_given_up_whole_makes_every_rubber_a_love_set_to_who_came(
            self):
        rows = ((SetStatus.PLAYED, (2, 6), {}), (SetStatus.PLAYED, (0, 0), {}),
                (SetStatus.FORFEIT, (3, 6), {"home_forfeit": True,
                                             "away_forfeit": True}),
                (SetStatus.RETIRED, (4, 2), {"home_retired": True}),
                (SetStatus.INCOMPLETE, (3, 3), {}))
        for status, games, flags in rows:
            for award in ("full-games", "fixed"):
                with self.subTest(status=status.value, games=games, award=award):
                    self.assertEqual(self.award(status, games, games_to_win=6,
                                                award=award, given_up_by="away",
                                                **flags), ("home", 6, 0))
                    self.assertEqual(self.award(status, games, games_to_win=4,
                                                award=award, given_up_by="home",
                                                **flags), ("away", 0, 4))

    def test_as_played_a_match_given_up_whole_keeps_every_row_as_printed(self):
        self.assertIsNone(self.award(SetStatus.PLAYED, (2, 6), games_to_win=6,
                                     award="as-played", given_up_by="away"))
        self.assertEqual(self.award(SetStatus.FORFEIT, (0, 0), away_forfeit=True,
                                    games_to_win=6, award="as-played",
                                    given_up_by="away"), ("home", 0, 0))


class TestARetiredSetGoesToTheSideWhoseRetiredBoxIsNotTicked(unittest.TestCase):
    """``retired_set_winner`` and ``SetResult``: a retirement is read off its box.

    4.422: the reader marked a row RETIRED from either side's box and kept no
    side, and ``SetResult`` gave every retired set to nobody, however the card
    ticked it. Measured (``/tmp/mcf/scratch/RUBBER/probe/before.txt``, case C):
    rows 6-3, 6-0, 6-4 and 5-1 with the away Retired box ticked on the last, and a
    TOTAL row of 4-0 24-8, were recorded 23-8 and 3-0 beside a totals-mismatch
    error. Now one box names the side that stayed, and the set is complete and
    theirs whatever the games read; both boxes, or none read, name nobody.

    Rejected: the side ahead on the games, which on a real card is the side that
    retired, leading 4-2.
    """

    def retired(self, games, **boxes) -> SetResult:
        return SetResult(slot=DSSD.slots[0], home_games=games[0],
                         away_games=games[1], status=SetStatus.RETIRED,
                         rule=FIRST_TO_6, **boxes)

    def test_one_box_names_the_other_side(self):
        self.assertEqual(model_mod.retired_set_winner(home_retired=True,
                                                      away_retired=False), "away")
        self.assertEqual(model_mod.retired_set_winner(home_retired=False,
                                                      away_retired=True), "home")

    def test_both_boxes_or_neither_name_nobody(self):
        for ticked in (True, False):
            with self.subTest(both=ticked):
                self.assertIsNone(model_mod.retired_set_winner(
                    home_retired=ticked, away_retired=ticked))

    def test_a_set_one_side_retired_from_is_complete_and_the_other_sides(self):
        for games, boxes, winner in (((4, 2), {"home_retired": True}, "away"),
                                     ((4, 6), {"home_retired": True}, "away"),
                                     ((1, 5), {"home_retired": True}, "away"),
                                     ((6, 1), {"away_retired": True}, "home")):
            with self.subTest(games=games, boxes=boxes):
                result = self.retired(games, **boxes)
                self.assertTrue(result.completed)
                self.assertEqual(result.winner, winner)
                self.assertFalse(result.unfinished_unmarked)

    def test_a_set_retired_with_both_boxes_ticked_is_nobodys(self):
        result = self.retired((5, 1), home_retired=True, away_retired=True)
        self.assertFalse(result.completed)
        self.assertIsNone(result.winner)


class TestASetScoredByAnAwardKeepsTheGamesPrintedOnItsRow(unittest.TestCase):
    """``SetResult.printed`` and ``awarded_to``: the award, and the row as printed.

    4.421 scores a rubber given up as the love set Match Centre totals it, so the
    games on the row are no longer the games the set is scored at. They are kept
    beside the scored ones, as the record of what was played: the match page and
    a player's list show them, Games played sums them, and the duplicate check and
    the checks of a forfeit's rows read them. ``awarded_to`` is who the award gave
    the set, which decides it whatever the box: a match given up whole over a
    rubber left incomplete scores that rubber a love set too.

    Rejected: keeping the printed games in ``home_games`` and adding scored
    properties beside them, where every reader left on the printed ones remakes
    the split (C-1's player records, sets from the tick beside games from the
    print).
    """

    def test_the_printed_games_are_the_scored_ones_until_an_award_rescored_them(
            self):
        plain = make_sets([(6, 2)])[0]
        self.assertEqual((plain.printed_home_games, plain.printed_away_games),
                         (6, 2))
        awarded = dataclasses.replace(plain, home_games=6, away_games=0,
                                      printed=(2, 6), status=SetStatus.FORFEIT,
                                      away_forfeit=True, awarded_to="home")
        self.assertEqual((awarded.home_games, awarded.away_games), (6, 0))
        self.assertEqual((awarded.printed_home_games, awarded.printed_away_games),
                         (2, 6))

    def test_an_awarded_set_is_complete_and_the_side_it_was_awarded_to(self):
        for status in (SetStatus.INCOMPLETE, SetStatus.RETIRED, SetStatus.PLAYED):
            with self.subTest(status=status.value):
                result = dataclasses.replace(
                    make_sets([(0, 6)], status=status)[0], printed=(3, 2),
                    awarded_to="away")
                self.assertTrue(result.completed)
                self.assertEqual(result.winner, "away")


class TestARubberNobodyPlayedIsOneRuleOfTheMatch(unittest.TestCase):
    """``Match.nobody_played``: a row no player is credited with, asked once.

    4.289: a whole-match forfeit's 0-0 rows credited the players named on them a
    match and sets played. Measured (``/tmp/mcf/scratch/RUBBER/probe/before.txt``,
    case box over 0-0): the four players of each side were credited 4 matches and
    12 sets where they played 3 nights. 4.421 scores such a row a love set, so the
    question is asked of the games printed on it: a row printed 0-0 under a
    forfeit, of the row or of the whole match, was played by nobody. validate's
    passes-by (set-no-games, set-players-missing, roster-missing), the players'
    figures, the contribution gap and the page's list all ask this. Asked of the
    printed games too, ``given_up_by`` still names the side that gave up a night
    told by its rows once those rows are scored as love sets.

    Rejected: a retired row left 0-0, which somebody came to play.
    """

    def scored(self, result: SetResult, home: int, away: int) -> SetResult:
        return dataclasses.replace(result, home_games=home, away_games=away,
                                   printed=(result.home_games, result.away_games))

    def test_a_rubber_ticked_and_left_empty_was_played_by_nobody(self):
        rows = list(make_sets(((0, 0), (2, 6), (6, 2), (0, 0))))
        rows[0] = self.scored(dataclasses.replace(
            rows[0], status=SetStatus.FORFEIT, away_forfeit=True), 6, 0)
        rows[1] = self.scored(dataclasses.replace(
            rows[1], status=SetStatus.FORFEIT, away_forfeit=True), 6, 0)
        rows[3] = dataclasses.replace(rows[3], status=SetStatus.RETIRED,
                                      away_retired=True)
        match = dataclasses.replace(make_match(), sets=tuple(rows))
        self.assertEqual([match.nobody_played(r) for r in match.sets],
                         [True, False, False, False])
        self.assertFalse(match.no_rubber_played)
        unticked = dataclasses.replace(match, sets=make_sets(((0, 0),) * 4))
        self.assertEqual([unticked.nobody_played(r) for r in unticked.sets],
                         [False] * 4)

    def test_every_empty_row_of_a_match_given_up_whole_was_played_by_nobody(self):
        for scores, played in ((((0, 0),) * 4, [True] * 4),
                               (((2, 6), (6, 4), (3, 6), (0, 0)),
                                [False, False, False, True])):
            with self.subTest(scores=scores):
                box = make_match(scores, status=MatchStatus.FORFEIT,
                                 forfeited_by="away")
                box = dataclasses.replace(box, sets=tuple(
                    self.scored(r, 6, 0) for r in box.sets))
                self.assertEqual([box.nobody_played(r) for r in box.sets], played)
                self.assertEqual(box.no_rubber_played, all(played))

    def test_a_night_given_up_by_its_rows_is_still_given_up_once_they_are_scored(
            self):
        rows = tuple(self.scored(dataclasses.replace(
            r, status=SetStatus.FORFEIT, away_forfeit=True), 6, 0)
            for r in make_sets(((0, 0),) * 4))
        match = dataclasses.replace(make_match(), sets=rows)
        self.assertEqual(match.given_up_by, "away")
        self.assertTrue(match.no_rubber_played)
        self.assertEqual((match.home_games, match.away_games), (24, 0))
        self.assertEqual((match.printed_home_games, match.printed_away_games),
                         (0, 0))

    def test_a_match_prints_the_sum_of_its_rows_as_printed(self):
        rows = list(make_sets(((2, 6), (6, 2), (1, 6), (3, 6))))
        for i in (0, 2, 3):
            rows[i] = self.scored(dataclasses.replace(
                rows[i], status=SetStatus.FORFEIT, away_forfeit=True), 6, 0)
        match = dataclasses.replace(make_match(), sets=tuple(rows))
        self.assertEqual((match.home_games, match.away_games), (24, 2))
        self.assertEqual((match.printed_home_games, match.printed_away_games),
                         (12, 20))
        self.assertFalse(make_match().no_rubber_played)


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

    def test_display_label_words_a_division_that_names_its_grade_second(self):
        # `Division 3 Grade B` on the card. What is read off it is the part after
        # the first word, so the word has to go back on: deciding on *any* word in
        # the value left this one as "Tuesday 3 Grade B".
        comp = Competition(id="x", day="Tuesday", division="3 Grade B", fmt=DSSD,
                           rules=GAMES_BONUS)
        self.assertEqual(comp.display_label, "Tuesday Division 3 Grade B")

    def test_display_label_leaves_a_division_that_leads_with_a_word_alone(self):
        # `Div. 2` is in the list because a club types the full stop and a card
        # prints it: "Division Div. 2" is the reading that gets it wrong.
        for division in ("Section 3 Grade B", "Div 2", "Div. 2", "grade b"):
            with self.subTest(division=division):
                comp = Competition(id="x", day="Tuesday", division=division,
                                   fmt=DSSD, rules=GAMES_BONUS)
                self.assertEqual(comp.display_label, f"Tuesday {division}")


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


class TestAnAspectThatNamesNothingIsEmptyAndNoProvenance(unittest.TestCase):
    """4.463, 4.464 and 4.462 in process: ``export._provenance`` over teams built
    by hand, which is the fallback where discovery recorded nothing.

    Measured on the base through the same calls: a competition with no captain
    gave captains "derived", one registering nobody players "derived", and a
    season with no name "derived", each a value the page lists as worked out; and
    a captain the club declared who is on no team's squad counted for nothing, so
    one declared beside a (C) read "card". Now an aspect that names nothing is
    ``export.EMPTY``, ahead of discovery's record and of the fallback, and a
    team's captain counts as the team says it came. Rejected: changing
    ``merge_provenance([])``, which is pinned above and answers for the teams too.
    """

    @staticmethod
    def provenance(comp: Competition) -> dict:
        from matchcentre import export
        return export._provenance(comp)

    @staticmethod
    def captained(comp: Competition, **teams: Team) -> Competition:
        return dataclasses.replace(comp, teams={**comp.teams, **teams})

    def setUp(self):
        self.comp = make_competition(["rockets", "iguanas"])
        self.marked = Player(slug="ann-ash", name="Ann Ash",
                             name_source=Provenance.CARD, team="rockets",
                             is_captain=True, captain_source=Provenance.CARD)

    def test_empty_is_a_value_of_the_document_and_not_a_provenance(self):
        from matchcentre import anonymise, export
        self.assertNotIn(export.EMPTY, {member.value for member in Provenance})
        self.assertNotEqual(export.EMPTY, anonymise.WITHHELD)

    def test_no_captain_and_nobody_registered_are_empty(self):
        from matchcentre import export
        said = self.provenance(self.comp)
        self.assertEqual((said["captains"], said["players"]),
                         (export.EMPTY, export.EMPTY))

    def test_discoverys_record_of_an_empty_collection_is_not_published(self):
        from matchcentre import export
        comp = dataclasses.replace(self.comp, provenance={
            "captains": merge_provenance([]), "players": merge_provenance([])})
        said = self.provenance(comp)
        self.assertEqual((said["captains"], said["players"]),
                         (export.EMPTY, export.EMPTY))

    def test_fill_ins_shown_are_not_nobody(self):
        comp = dataclasses.replace(self.comp, fill_in_names={"cy-wren": "Cy Wren"})
        self.assertEqual(self.provenance(comp)["players"], "derived")

    def test_a_captain_declared_off_every_squad_reads_declared(self):
        comp = self.captained(self.comp, rockets=Team(
            slug="rockets", name="Rockets", captain="zed-zane",
            captain_declared="Zed Zane", players=(self.marked,)))
        self.assertEqual(self.provenance(comp)["captains"], "declared")

    def test_a_declared_captain_beside_a_marked_one_reads_as_both_sources(self):
        comp = self.captained(
            self.comp,
            rockets=Team(slug="rockets", name="Rockets", captain="zed-zane",
                         captain_declared="Zed Zane", players=(self.marked,)),
            iguanas=Team(slug="iguanas", name="Iguanas", captain="bo-bell",
                         players=(Player(slug="bo-bell", name="Bo Bell",
                                         name_source=Provenance.CARD,
                                         team="iguanas", is_captain=True,
                                         captain_source=Provenance.CARD),)))
        self.assertEqual(self.provenance(comp)["captains"],
                         merge_provenance([Provenance.DECLARED,
                                           Provenance.CARD]).value)

    def test_a_captain_from_a_mark_reads_card(self):
        comp = self.captained(self.comp, rockets=Team(
            slug="rockets", name="Rockets", captain="ann-ash",
            players=(self.marked,)))
        said = self.provenance(comp)
        self.assertEqual((said["captains"], said["players"]), ("card", "card"))

    def test_a_season_no_page_names_is_empty_and_a_folders_name_derived(self):
        from matchcentre import export
        self.assertEqual(self.provenance(self.comp)["season"], export.EMPTY)
        folder = dataclasses.replace(self.comp, season_key="2026-winter")
        self.assertEqual(self.provenance(folder)["season"], "derived")
        named = dataclasses.replace(self.comp, season="Winter",
                                    provenance={"season": Provenance.CARD})
        self.assertEqual(self.provenance(named)["season"], "card")


class TestEveryOfferOfASeasonsNameIsTheOneItsPagesShow(unittest.TestCase):
    """4.461 in process: ``export.season_offer`` and the writer's two places.

    Measured on the base by ``config.render``: a competition with no season
    offered ``# season = "2026 Season 2"`` in its section, and a season folder's
    [seasons] line offered its key as it stands, ``# 2026-winter =
    "2026-winter"``, where its pages read "2026 Winter". Now both ask the one
    rule: the name the pages show, else the year or years of the matches' dates,
    else nothing, and the section then writes no line. Rejected: the sample name
    kept as an example, which deleting its # declares.
    """

    @staticmethod
    def offer(comp: Competition, dates=()) -> str:
        from matchcentre import export
        return export.season_offer(comp, dates)

    def setUp(self):
        self.comp = make_competition(["rockets", "iguanas"])
        self.multi = config_mod.Config(path=Path("config.toml"),
                                       seasons_mode="multi")

    def test_the_name_shown_is_offered_ahead_of_any_year(self):
        named = dataclasses.replace(self.comp, season="Season 1")
        self.assertEqual(self.offer(named, ["2026-08-18"]), "Season 1")
        folder = dataclasses.replace(self.comp, season_key="2026-winter")
        self.assertEqual(self.offer(folder, ["2027-08-18"]), "2026 Winter")

    def test_failing_that_the_year_or_years_its_dates_give(self):
        self.assertEqual(self.offer(self.comp, ["2026-08-18", "2026-08-25"]), "2026")
        self.assertEqual(self.offer(self.comp, ["2027-01-05", "2026-12-15"]),
                         "2026 to 2027")

    def test_and_failing_that_nothing(self):
        self.assertEqual(self.offer(self.comp), "")
        self.assertEqual(self.offer(self.comp, ["", "26"]), "")

    def test_the_seasons_table_offers_a_folder_the_name_its_pages_show(self):
        folder = dataclasses.replace(self.comp, season_key="2026-winter")
        lines = config_mod._seasons_section(self.multi, [folder])
        self.assertIn('# 2026-winter = "2026 Winter"', lines)
        self.assertEqual(config_mod._seasons_section(self.multi, [folder]), lines)
        text = config_mod.render(self.multi, competitions=[folder])
        self.assertIn('# 2026-winter = "2026 Winter"', text.splitlines())
        self.assertNotIn("2026-winter = \"2026-winter\"", text)

    def test_a_section_with_no_name_to_offer_writes_no_season_line(self):
        text = config_mod.render(config_mod.Config(path=Path("config.toml")),
                                 competitions=[self.comp])
        self.assertNotRegex(text, r"(?m)^#? ?season = ")
        self.assertNotIn("2026 Season 2", text)
        self.assertIn("No card printed it and no date gave a year", " ".join(
            line.lstrip("# ") for line in text.splitlines()))


class TestRoundOrder(unittest.TestCase):

    # The four weeks of a finals series, every label the program reads as a final
    # in the week it is played. An elimination final is played the week the
    # qualifying finals are, and a final that ends a series is its last night
    # whichever of the words a club uses for it.
    FINALS_WEEKS = (
        ("QF", "Quarter Final", "Qualifying Final", "quarters",
         "E", "EF", "elim", "Elimination Final"),
        ("SF", "semi", "Semi Final", "semis"),
        ("PF", "prelim final", "Preliminary Final"),
        ("F", "final", "Finals", "GF", "Grand Final"),
    )

    def test_rounds_run_numerically_then_finals(self):
        # Sorting these as strings gives 1, 10, 2; sorting them as integers dies
        # on "F". And the semi-final is played before the final, which sorted as
        # text it is not (4.37).
        labels = ["10", "SF", "2", "F", "1"]
        self.assertEqual(sorted(labels, key=round_sort_key),
                         ["1", "2", "10", "SF", "F"])

    def test_a_finals_series_runs_in_the_order_it_is_played(self):
        """QF, SF, PF, then GF or F, in every spelling the program reads as a final.

        Measured before (``/tmp/t4/scratch/y437/c2_rounds.py``): ``QF``, ``SF``,
        ``GF`` sorted as ``GF``, ``QF``, ``SF``; ``SF``, ``F`` as ``F``, ``SF``; and
        ``Semi Final``, ``Grand Final`` as ``Grand Final``, ``Semi Final``, because
        every round that is not a number was ordered by its letters. This key is the
        order of the season document's rounds, the report's lists, the front page's
        first round, a player's latest rating and the rule that decides whether the
        season has gone past a round, so a grand final sorted first reported its
        semi-final's card as not yet due and its own as missing.

        By the label, which says where a final comes in a series, and not by the
        round's date. Rejected: the date, which a declared round may not have
        (``date`` is optional, and a finals draw is typed before its nights are
        fixed), may have as words (``6 & 7 Oct``), and would take a typo on one card
        for a grand final played before its semi-final.
        """
        for week, labels in enumerate(self.FINALS_WEEKS):
            for label in labels:
                with self.subTest(label=label):
                    self.assertTrue(is_finals_label(label))
                    for later in (x for w in self.FINALS_WEEKS[week + 1:] for x in w):
                        self.assertLess(round_sort_key(label), round_sort_key(later),
                                        f"{label} is not before {later}")
                    self.assertEqual(round_sort_key(f"  {label} "),
                                     round_sort_key(label))
                    self.assertEqual(round_sort_key(label.upper())[:2],
                                     round_sort_key(label.lower())[:2])

    def test_every_label_read_as_a_final_has_a_week_in_the_series(self):
        """The weeks above are every finals label there is, so the test above is whole.

        The labels the program reads as finals and the weeks it orders them by are
        one table, and this holds it to that: a label read as a final with no week
        would be ordered as a round nobody can place, ahead of the semi-final.
        Rejected: a list of finals labels kept beside the table, which is two lists
        to keep in step for one question.
        """
        from matchcentre import competition
        self.assertEqual(set(competition._FINALS_LABELS),
                         {competition._finals_word(label) for week in self.FINALS_WEEKS
                          for label in week})

    def test_a_final_is_the_same_final_however_it_is_spelt(self):
        """``Semi-Final``, ``SemiFinal``, ``Semi Finals`` and ``SF1`` are the semi-final.

        Measured (``/tmp/t4/scratch/y437/fix/spellings.py``): not one of the
        spellings below was read as a final, so each was scored as a regular round,
        with F11's warning, and ordered as a name with no week in the series. That
        put it ahead of every final the table did know: ``QF``, ``Semi-Final``,
        ``Grand Final`` ran ``Semi-Final``, ``QF``, ``Grand Final``, and
        ``EF``, ``QF``, ``SF1``, ``SF2`` ran ``EF``, ``SF1``, ``SF2``, ``QF``. Text
        order had put the ``QF`` first in both by the luck of its letters. So a
        label is looked up by its letters: case, spaces and hyphens aside, a plural
        as its singular, and a number after the word as one of that week's finals.
        Rejected: asking the competition whether the round was declared finals,
        which the callers holding only a label cannot, and a club that declared a
        ``Semi-Final`` finals had its card reported missing all the same; and
        writing each spelling into the table, which is a list nobody finishes.
        """
        spelt = (("Quarter-Final", "quarter finals", "Qualifying Finals", "QF1",
                  "EF 2"),
                 ("Semi-Final", "semi-final", "SemiFinal", "Semi  Final",
                  "Semi Finals", "SF1", "SF 2", "Semi Final 1"),
                 ("Prelim-Final", "Preliminary Finals", "PF1"),
                 ("Grand-Final", "Grand Finals", "Grandfinal", "GF 1"))
        series = ("QF", "SF", "PF", "GF")
        for week, labels in enumerate(spelt):
            for label in labels:
                with self.subTest(label=label):
                    self.assertTrue(is_finals_label(label))
                    others = [x for i, x in enumerate(series) if i != week]
                    self.assertEqual(sorted([label, *others], key=round_sort_key),
                                     [*others[:week], label, *others[week:]])
        self.assertEqual(sorted(["GF", "SF2", "EF", "PF", "SF1", "QF"],
                                key=round_sort_key),
                         ["EF", "QF", "SF1", "SF2", "PF", "GF"])

    def test_a_named_round_nobody_can_place_follows_the_numbers_and_leads_the_finals(self):
        """``Catch-up`` and ``Plate`` after round 3 and before the semi-final.

        A label that is neither a number nor a final is scored as a regular round
        (F11 says so, and says how to declare it otherwise), and the regular season
        is played before its finals. Among themselves they keep their order as text,
        which is the one order such labels have. Rejected: after the finals, which
        is where text order put ``Plate`` and would make a catch-up round with no
        card look to the rule that decides whether the season has gone past a round
        like one nobody has reached, however many finals had cards; and among the
        finals by their letters, which is the defect.
        """
        self.assertEqual(sorted(["GF", "Plate", "Catch-up", "SF", "3"],
                                key=round_sort_key),
                         ["3", "Catch-up", "Plate", "SF", "GF"])

    def test_finals_labels_are_recognised_across_the_words_clubs_use(self):
        for label in ("F", "final", "Grand Final", "SF", "semi", "QF", "PF"):
            self.assertTrue(is_finals_label(label), label)
        for label in ("1", "12", "Round 3"):
            self.assertFalse(is_finals_label(label), label)


class TestAFinalDeclaredUnderANameOfItsOwnIsPlayedWhereTheDrawPutsIt(
        unittest.TestCase):
    """``Major Semi`` typed ``finals = true`` between the QF and the GF: played there.

    Measured before (``/tmp/t7/scratch/ROUNDS/probe.py``, ``before.out``): the key
    sorted ``1, 2, 3, QF, Major Semi, GF`` as ``1, 2, 3, Major Semi, QF, GF``, and on
    the demo with that draw declared, all three finals typed true and the QF's card
    alone, the season document's rounds ran in that order, ``card-missing`` said the
    season was past the Major Semi and its fixture was ``card_missing``. The key
    read the label alone, and ``Major Semi`` names no week of a series, so it went
    with the names, after the regular rounds and ahead of every final. Only the
    draw says it is a final, and where it comes.

    So the key is asked with the draw, the rounds by label in the order the club
    declared them: a round the draw types a final whose label gives no week goes
    among the finals, after every final the draw lists above it, then by its place
    in the draw. A number keeps its week and a finals label its stage, whatever is
    typed on them, and a name the draw does not call a final keeps its place among
    the names. Rejected: the draw's order for every round, which would move every
    season whose rounds are typed out of turn, where the numbers already say the
    week.
    """

    @staticmethod
    def draw(*labels: str) -> dict[str, Round]:
        """The rounds *labels* name, in order; one ending ``*`` is typed a final."""
        return {label.rstrip("*"): Round(label.rstrip("*"),
                                         is_finals=label.endswith("*"))
                for label in labels}

    @staticmethod
    def ordered(draw: dict[str, Round]) -> list[str]:
        # Handed over backwards, so that an order that came out as typed was sorted
        # into it and not left in it.
        return sorted(reversed(list(draw)),
                      key=lambda number: round_sort_key(number, draw))

    def test_the_rows_draw_runs_in_the_order_it_was_declared(self):
        draw = self.draw("1", "2", "3", "QF*", "Major Semi*", "GF*")
        self.assertEqual(self.ordered(draw),
                         ["1", "2", "3", "QF", "Major Semi", "GF"])
        # Asked of the label alone, as a caller with no competition asks it, the
        # order is 4.37's: a name the finals table does not hold is a name.
        self.assertEqual(sorted(draw, key=round_sort_key),
                         ["1", "2", "3", "Major Semi", "QF", "GF"])

    def test_it_comes_after_every_final_the_draw_lists_above_it(self):
        """A draw typed ``SF``, ``QF``, ``Major Semi``: the Major Semi after the SF.

        Rejected: after the nearest final the draw lists above it, the QF, which
        would sort the Major Semi ahead of an SF the club typed above it.
        """
        draw = self.draw("1", "SF*", "QF*", "Major Semi*", "GF*")
        self.assertEqual(self.ordered(draw), ["1", "QF", "SF", "Major Semi", "GF"])

    def test_it_comes_after_a_finals_label_typed_regular_above_it(self):
        """A GF typed ``finals = false`` above it: the Decider after the GF.

        The GF keeps the GF's place, which is its label's, so a final the club typed
        below it is played after it. Rejected: counting only the rounds typed
        final, which sorts the Decider ahead of a GF typed above it.
        """
        draw = self.draw("1", "SF*", "GF", "Decider*")
        self.assertEqual(self.ordered(draw), ["1", "SF", "GF", "Decider"])

    def test_one_typed_above_every_staged_final_leads_them(self):
        # And a name the draw does not type a final is a regular round's name,
        # after the numbers and ahead of every final as it was.
        draw = self.draw("1", "2", "Playoff*", "QF*", "GF*", "Catch-up")
        self.assertEqual(self.ordered(draw),
                         ["1", "2", "Catch-up", "Playoff", "QF", "GF"])

    def test_two_of_them_run_in_the_draws_order_whatever_their_letters(self):
        self.assertEqual(self.ordered(self.draw("1", "Playoff*", "Decider*")),
                         ["1", "Playoff", "Decider"])
        self.assertEqual(self.ordered(self.draw("1", "Decider*", "Playoff*")),
                         ["1", "Decider", "Playoff"])

    def test_a_number_and_a_finals_label_keep_their_places_whatever_is_typed(self):
        """Round 3 typed a final and a GF typed regular: 3 after 2, GF after SF.

        Rejected: round 3 among the finals, after round 4, which the draw lists
        after it and the number says comes after it; and a GF typed ``finals =
        false`` put by its place in the draw, ahead of the SF, where its label
        says the week it is played and 4.37 put it.
        """
        draw = self.draw("1", "2", "3*", "4", "GF", "SF*")
        self.assertEqual(self.ordered(draw), ["1", "2", "3", "4", "SF", "GF"])
        for number in draw:
            with self.subTest(number=number):
                self.assertEqual(round_sort_key(number, draw), round_sort_key(number))

    def test_the_order_it_gives_is_the_order_it_gives_again(self):
        """Sorted, and the draw typed again in that order: the same order.

        The exporter writes the rounds in this order, and the change list reads the
        draw back from the document it wrote, so a draw in the order it gave has to
        give it again, or the two would list one season two ways.
        """
        draw = self.draw("GF*", "Playoff*", "1", "SF*", "Major Semi*", "2", "QF*",
                         "Catch-up")
        first = self.ordered(draw)
        self.assertEqual(first, ["1", "2", "Catch-up", "QF", "SF", "GF", "Playoff",
                                 "Major Semi"])
        self.assertEqual(self.ordered({number: draw[number] for number in first}),
                         first)

    def test_it_is_one_place_however_it_is_spaced_and_shares_it_with_nobody(self):
        draw = self.draw("QF*", "Major Semi*", "major semi*", "GF*")
        self.assertEqual(round_sort_key("  Major Semi ", draw),
                         round_sort_key("Major Semi", draw))
        for one, other in itertools.combinations(draw, 2):
            with self.subTest(one=one, other=other):
                self.assertNotEqual(round_sort_key(one, draw),
                                    round_sort_key(other, draw))

    def test_a_competition_orders_its_rounds_by_its_own_draw(self):
        # Its rounds are its draw, in the order it was declared; the rounds with
        # cards are said in that order too, a final with a card and one without.
        comp = dataclasses.replace(
            make_competition(["rockets", "iguanas"]),
            rounds=self.draw("1", "QF*", "Major Semi*", "GF*"))
        self.assertEqual(comp.round_order, ["1", "QF", "Major Semi", "GF"])
        matches = [make_match(rnd=number) for number in ("GF", "Major Semi", "QF", "1")]
        self.assertEqual(comp.rounds_played(matches, finals=True),
                         ["QF", "Major Semi", "GF"])
        self.assertEqual(comp.round_key("Major Semi"),
                         round_sort_key("Major Semi", comp.rounds))


class TestTwoRoundsPrintedDifferentlyNeverShareAPlace(unittest.TestCase):
    """``3`` and ``03``, ``SF`` and ``sf``: two rounds, so two places in the order.

    A round is its label as printed, less the spaces round it (4.32), so a card
    printing ``03`` and one printing ``3`` are two rounds, and so are ``SF`` and
    ``sf``. The key gave each pair one place: ``(0, 3, '')`` for ``3``, ``03``
    and ``３``, ``(2, 2, 'sf')`` for ``SF``, ``sf`` and ``Sf``
    (``/tmp/t4/scratch/f3/probes/keys.py``, ``before-keys.log``). The rounds are
    collected in a set, so a tie came out in the order the hash seed left them:
    the suite's season with ``03``, ``SF`` and ``sf`` cards added built a season
    document unlike seed 0's under six of seeds 1 to 7, and its cards line read
    ``3, 03`` or ``03, 3`` and ``SF, sf`` or ``sf, SF`` by the seed
    (``tieseeds.py``, ``before-tieseeds.log``); with the seed unset, a rebuild
    with nothing new rewrote ``season.json`` in 5 runs of 8 (``unset.py``,
    ``before-unset-tfin.log``). The label breaks the tie, as text: labels of one
    week keep the week's order and run by their characters within it, so ``SF``
    comes before ``sf``, except that a number printed plainly comes last in its
    week. Text alone put ``3`` before ``３`` and ``٣``, and ``0`` before ``00``,
    so the ladder after round 3 left out two cards the tied key had taken in
    (``/tmp/t4/scratch/f3/fx/probes/plain.py``, ``before-plain.log``).

    Rejected: breaking the tie at each caller, as ``tools/fetch.py`` did, which
    is a second rule of round order in every place that sorts rounds, and the
    report's lists and the season document's rounds had none; the shorter label
    first, which would put ``3`` before ``03`` and so leave the ``03`` card out of
    the ladder after round 3; and text alone, which does the same to ``３``.
    """

    LABELS = ("0", "00", "1", "01", "2", "10", "3", "03", "003", "\uff13", "\u0663",
              "Catch-up", "catch-up", "CATCH-UP", "Plate", "\u00df", "ss", "SF",
              "sf", "Sf", "SF1", "sf1", "Semi Final", "semi final", "Semi-Final",
              "QF", "qf", "PF", "GF", "gf", "F", "f", "Final", "final",
              "Grand Final")

    def test_no_two_labels_share_a_key(self):
        for one, other in itertools.combinations(self.LABELS, 2):
            with self.subTest(one=one, other=other):
                self.assertNotEqual(round_sort_key(one), round_sort_key(other))

    def test_the_spaces_round_a_label_are_not_part_of_it(self):
        # Round identity is the printed label stripped (4.32), so these are one
        # round and must stay one place.
        for label in self.LABELS:
            with self.subTest(label=label):
                self.assertEqual(round_sort_key(f"  {label} "),
                                 round_sort_key(label))

    def test_every_order_the_labels_arrive_in_sorts_to_one_order(self):
        labels = list(self.LABELS)
        first = sorted(labels, key=round_sort_key)
        shuffler = random.Random(144)
        for attempt in range(50):
            shuffler.shuffle(labels)
            with self.subTest(attempt=attempt):
                self.assertEqual(sorted(labels, key=round_sort_key), first)

    def test_labels_of_one_week_run_by_their_text(self):
        self.assertEqual(sorted(["3", "003", "03", "4", "2"], key=round_sort_key),
                         ["2", "003", "03", "3", "4"])
        self.assertEqual(sorted(["sf", "GF", "Sf", "SF", "QF"], key=round_sort_key),
                         ["QF", "SF", "Sf", "sf", "GF"])
        self.assertEqual(sorted(["catch-up", "Catch-up", "3", "SF"],
                                key=round_sort_key),
                         ["3", "Catch-up", "catch-up", "SF"])

    def test_a_number_printed_plainly_comes_last_in_its_week(self):
        # A sheet's after_round is always a plain number, so the ladder after
        # round 3 is the slice up to "3" and has to take in every other way of
        # printing week 3, whatever its characters sort as.
        for label in ("03", "003", "\uff13", "\u0663", "00", "010"):
            plain = str(int(label))
            with self.subTest(label=label):
                self.assertLess(round_sort_key(label), round_sort_key(plain))
        self.assertEqual(sorted(["3", "\uff13", "03", "4", "\u0663", "2"],
                                key=round_sort_key),
                         ["2", "03", "\u0663", "\uff13", "3", "4"])


class TestARoundInSuperscriptOrCircledDigitsIsANameAndNotANumber(unittest.TestCase):
    """``³``, ``③`` and ``¹²`` are labels that are not numbers; ``３`` and ``٣`` are.

    ``str.isdigit`` is true of 128 characters ``int`` refuses, every one of them
    Unicode's other numbers (No): superscripts, circled and parenthesised digits.
    The key asked ``isdigit`` and then ``int``, so it raised ``ValueError`` on
    ``'³'``, ``'③'`` and ``'¹²'`` (``/tmp/t6/scratch/m16/before/key.log``,
    ``unicode.log``), and F11 asked ``isdigit`` on its own. A number is a label of
    decimal digits, the 680 characters ``int`` reads, which keeps ``3``, the
    fullwidth ``３``, the Arabic-Indic ``٣`` and the mathematical ``𝟑`` week 3; and
    one function, ``competition.round_number``, says so for the key and for F11.
    Any other label is a round with a name, placed and warned about as
    ``Catch-up`` is, and so is a label of more digits than ``int`` will read, which
    raised the same way past 4300 of them (``/tmp/t6/scratch/m16/fx/before.log``).
    The number after a finals word is read by the same function: it stripped ASCII
    digits alone, so ``SF３`` was no final, scored into the ladder and warned about
    as a name, where ``SF3`` is a semi-final. So is a bye written back by
    ``--discover``, which asked ``isdecimal`` and then ``int`` on its own and raised
    on a declared bye of 4301 digits
    (``/tmp/t6/scratch/m16/fx/byes4301-teams.log``).

    Rejected: reading ``³`` as round 3, by its digit value or by NFKD, which guesses
    what a club meant and would have ``①`` and ``1`` share a week; and ``isnumeric``,
    which takes in ``½`` and ``Ⅻ`` as well as every character ``isdigit`` does.
    """

    def test_they_are_not_numbers_and_the_key_places_them_as_names(self):
        for label in ("\u00b3", "\u2462", "\u00b9\u00b2", " \u00b3 "):
            with self.subTest(label=label):
                self.assertIsNone(comp_mod.round_number(label))
                self.assertEqual(round_sort_key(label)[:2], (1, 0))
        self.assertEqual(sorted(["SF", "\u2462", "Catch-up", "10", "\u00b3", "3"],
                                key=round_sort_key),
                         ["3", "10", "Catch-up", "\u00b3", "\u2462", "SF"])

    def test_every_decimal_digit_is_still_a_number(self):
        for label, week in (("3", 3), (" 03 ", 3), ("\uff13", 3), ("\u0663", 3),
                            ("\U0001d7d1", 3), ("12", 12), ("\u0661\u0662", 12)):
            with self.subTest(label=label):
                self.assertEqual(comp_mod.round_number(label), week)
                self.assertEqual(round_sort_key(label)[:2], (0, week))
        for label in ("", "SF", "3a", "R3", "1.5", "-3"):
            with self.subTest(label=label):
                self.assertIsNone(comp_mod.round_number(label))

    def test_a_label_of_more_digits_than_int_reads_is_a_name(self):
        limit = sys.get_int_max_str_digits()
        if not limit:
            self.skipTest("int reads any number of digits in this interpreter")
        self.assertEqual(comp_mod.round_number("3" * limit), int("3" * limit))
        for label in ("3" * (limit + 1), "\uff13" * (limit + 1)):
            with self.subTest(digits=len(label)):
                self.assertIsNone(comp_mod.round_number(label))
                self.assertEqual(round_sort_key(label)[:2], (1, 0))

    def test_the_byes_written_back_ask_the_one_function(self):
        limit = sys.get_int_max_str_digits()
        if not limit:
            self.skipTest("int reads any number of digits in this interpreter")
        long = "3" * (limit + 1)

        def written(*rounds):
            lines = config_mod._bye_lines("Rockets", rounds)
            return tomllib.loads("\n".join(lines))["Rockets"]

        self.assertEqual(written("4", "04", "\uff14", "\u00b3", long),
                         [4, "04", "\uff14", "\u00b3", long])
        with mock.patch.object(comp_mod, "round_number", lambda number: None), \
                mock.patch.object(config_mod, "round_number", lambda number: None,
                                  create=True):
            self.assertEqual(written("4"), ["4"])

    def test_a_final_numbered_in_any_decimal_digits_is_a_final(self):
        for label in ("SF3", "SF\uff13", "SF\u0663", "Semi Final \uff12"):
            with self.subTest(label=label):
                self.assertTrue(is_finals_label(label))
                self.assertEqual(round_sort_key(label)[:2], (2, 2))
        for label in ("SF\u00b3", "SF\u2462"):
            with self.subTest(label=label):
                self.assertFalse(is_finals_label(label))
                self.assertEqual(round_sort_key(label)[:2], (1, 0))

    def test_the_key_the_finals_word_and_f11_follow_the_one_function(self):
        from matchcentre import validate as validate_mod
        stated = comp_mod.round_number

        def reading_superscripts(number):
            """A rule nobody states: ``³`` read as 3, the alternative rejected."""
            return 3 if number.strip() == "\u00b3" else stated(number)

        matches = [make_match(rnd="1"),
                   make_match(rnd="\u00b3", home="iguanas", away="rockets")]

        def named():
            report = validate_mod.validate(
                matches, make_competition(["rockets", "iguanas"]))
            return [i.message for i in report.issues
                    if i.rule == "F11" and "\u00b3" in i.message]

        self.assertEqual(named(),
                         ["round label '\u00b3' is neither a number nor a finals "
                          "label this program recognises; it is being scored as a "
                          "regular round."])
        # Given to both names, so that F11 follows it however it reaches the rule.
        with mock.patch.object(comp_mod, "round_number", reading_superscripts), \
                mock.patch.object(validate_mod, "round_number", reading_superscripts,
                                  create=True):
            self.assertEqual(round_sort_key("\u00b3")[:2], (0, 3))
            self.assertTrue(is_finals_label("SF\u00b3"))
            self.assertEqual(named(), [], "F11 asks whether a label is a number "
                                          "its own way")


class TestTwoSeasonsWithDifferentKeysNeverShareAPlace(unittest.TestCase):
    """``season-1`` and ``season-01`` are two seasons, so two places in the order.

    A season's key is its folder's name, and two folders are two seasons. The key
    read digit runs as numbers and folded case, so ``s-1``, ``s-01`` and ``S-1``
    had one key, and so did ``1``, ``01`` and ``001`` (``before-keys.log``). The
    seasons are found in a set, so a tie came out in the order the hash seed left
    them: of seeds 0 to 7, the not-listed section of ``build.py --only`` read
    ``season-01, season-1`` under 0 and 3 and the other way under the rest, and
    the refusal of an unknown season put ``season-01`` first under 0, 3, 6 and 7
    (``/tmp/t4/scratch/f3/probes/onlyseeds.py``, ``before-onlyseeds.log``). On
    the front page the tie was worse than an order: two such seasons with a
    Tuesday and a Wednesday each came out Tuesday of one, Tuesday of the other,
    Wednesday of one, Wednesday of the other (``probes/front``). The key itself
    breaks the tie, last, as text.

    Rejected: breaking the tie at each sort, as the careers page did, which is
    the same rule written again at every place that orders seasons; the front
    page sorts twice by it and the second sort would have to carry it too.
    """

    KEYS = ("1", "01", "001", "9", "10", "2026", "2026-winter", "2026-finals",
            "2026-summer", "season-1", "season-01", "season-001", "season-2",
            "season-10", "s-1", "s-01", "S-1", "spring", "autumn", "Spring")

    def test_no_two_keys_share_a_place(self):
        for one, other in itertools.combinations(self.KEYS, 2):
            with self.subTest(one=one, other=other):
                self.assertNotEqual(season_order_key(one), season_order_key(other))

    def test_every_order_the_seasons_arrive_in_sorts_to_one_order(self):
        keys = list(self.KEYS)
        first = sorted(keys, key=season_order_key)
        shuffler = random.Random(144)
        for attempt in range(50):
            shuffler.shuffle(keys)
            with self.subTest(attempt=attempt):
                self.assertEqual(sorted(keys, key=season_order_key), first)

    def test_seasons_whose_numbers_are_equal_run_by_their_text(self):
        self.assertEqual(sorted(["season-10", "season-1", "season-2", "season-01"],
                                key=season_order_key),
                         ["season-01", "season-1", "season-2", "season-10"])


class TestASeasonKeyInDigitsIntCannotReadIsOrderedAsText(unittest.TestCase):
    """``2026³``, ``¹²`` and 4301 digits order as text; ``٣`` and ``２０２６`` as numbers.

    The key split a season's key into runs of ``\\d`` and the rest, and read a run
    as a number when it passed ``str.isdigit``, by ``int``. A run of the rest can be
    superscript or dingbat digits, which ``isdigit`` takes in and ``int`` refuses,
    and a run of ``\\d`` can be longer than ``int`` will read. Asked directly
    (``/tmp/t7/scratch/F1/before/seasons.log``), ``'¹²'``, ``'2026³'`` and ``'❸'``
    raised ``ValueError: invalid literal for int()``, and 4301 digits raised
    ``Exceeds the limit (4300 digits)``, where 4300 did not. Through ``build.py`` a
    section's ``season_key`` of 4301 digits stopped the build with that traceback
    from ``site._entries``, exit 1. That is the one a club can reach: a folder or a
    section's key is slugified before it is ordered, and the slug is ASCII, so
    ``2026³`` and ``¹²`` reach the order as ``20263`` and ``12`` and build
    (``before/seasons2.log``, ``/tmp/t7/scratch/F1/fixup/folder-base.log``). A figure
    is now what ``competition.decimal_figure`` reads, the test ``round_number``
    already made (4.151), so a run it does not read is text, after every key that
    starts with a number.

    Rejected: reading ``³`` as 3 by its digit value, which guesses what a club
    meant; and a second decimal test here beside the round's, which is two rules
    for one question the day one of them is edited.
    """

    def test_superscript_and_dingbat_digits_are_text(self):
        for key, parts in (("¹²", ((1, 0, "¹²"),)),
                           ("2026³", ((0, 2026, ""), (1, 0, "³"))),
                           ("❸", ((1, 0, "❸"),)),
                           ("season-³", ((1, 0, "season-³"),))):
            with self.subTest(key=key):
                self.assertEqual(season_order_key(key), (parts, key))
        self.assertEqual(sorted(["¹²", "2026³", "12", "2026"],
                                key=season_order_key),
                         ["12", "2026", "2026³", "¹²"])

    def test_a_key_of_more_digits_than_int_reads_is_text(self):
        limit = sys.get_int_max_str_digits()
        if not limit:
            self.skipTest("int reads any number of digits in this interpreter")
        self.assertEqual(season_order_key("3" * limit)[0], ((0, int("3" * limit), ""),))
        long = "3" * (limit + 1)
        self.assertEqual(season_order_key(long), (((1, 0, long),), long))
        self.assertEqual(sorted([long, "2026-winter", "9"], key=season_order_key),
                         ["9", "2026-winter", long])

    def test_every_decimal_digit_is_still_a_number(self):
        for key, parts in (("٣", ((0, 3, ""),)),
                           ("２０２６-winter",
                            ((0, 2026, ""), (1, 0, "-winter"))),
                           ("2026-winter", ((0, 2026, ""), (1, 0, "-winter")))):
            with self.subTest(key=key):
                self.assertEqual(season_order_key(key)[0], parts)

    def test_a_figure_is_decimal_digits_int_reads_and_nothing_round_them(self):
        limit = sys.get_int_max_str_digits()
        for text, figure in (("3", 3), ("03", 3), ("３", 3), ("٣", 3),
                             ("\U0001d7d1", 3), ("24", 24)):
            with self.subTest(text=text):
                self.assertEqual(comp_mod.decimal_figure(text), figure)
        for text in ("", " 3", "3 ", "-3", "+3", "3.0", "³", "③",
                     "¹²", "❸", "½", "III", "3a"):
            with self.subTest(text=text):
                self.assertIsNone(comp_mod.decimal_figure(text))
        if limit:
            self.assertEqual(comp_mod.decimal_figure("3" * limit), int("3" * limit))
            self.assertIsNone(comp_mod.decimal_figure("3" * (limit + 1)))

    def test_the_round_and_the_season_order_ask_the_one_function(self):
        stated = comp_mod.decimal_figure

        def reading_superscripts(text):
            """A rule nobody states: ``³`` read as 3, the alternative rejected."""
            return 3 if text == "³" else stated(text)

        self.assertIsNone(comp_mod.round_number(" ³ "))
        with mock.patch.object(comp_mod, "decimal_figure", reading_superscripts):
            self.assertEqual(comp_mod.round_number(" ³ "), 3)
            self.assertEqual(season_order_key("2026³")[0],
                             ((0, 2026, ""), (0, 3, "")))


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

    def test_the_finals_test_asks_the_round_the_competition_knows(self):
        # F11. A declared round carries the answer a human gave it, whatever its
        # label looks like; the label decides only for a round the draw never
        # reached. One method, because two would eventually disagree.
        comp = make_competition(["rockets", "iguanas"], declared=True)
        comp = comp.with_rounds({
            "1": Round(number="1"),
            "SF": Round(number="SF", is_finals=True),
            "GF": Round(number="GF", is_finals=False),   # declared regular
        })
        self.assertFalse(comp.is_finals_round("1"))
        self.assertTrue(comp.is_finals_round("SF"))
        self.assertFalse(comp.is_finals_round("GF"))
        # Not in the draw at all: judged by its own label, and a missing round
        # number is not a final.
        self.assertTrue(comp.is_finals_round("QF"))
        self.assertFalse(comp.is_finals_round("7"))
        self.assertFalse(comp.is_finals_round(None))

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

    def test_two_cards_for_one_night_are_one_fixture_in_the_draw(self):
        # The draw is a list of fixtures, and this was a list of cards: the
        # commonest folder mistake there is -- a card exported twice -- put the
        # night in the draw twice, so a seven-fixture season had an eighth
        # fixture nobody could ever play.
        comp = make_competition(["rockets", "iguanas", "wanderers", "aces"],
                                declared=True)
        rounds = comp.rounds_from_matches(
            self.round_one() + [make_match(home="rockets", away="iguanas")])
        self.assertEqual(rounds["1"].fixtures,
                         (("rockets", "iguanas"), ("wanderers", "aces")))

    def test_the_night_two_cards_disagree_about_is_still_a_fixture(self):
        # Two cards that disagree are not counted anywhere (F9), and the fixture
        # was still played: they disagree about the score, not about who played
        # whom. Dropping it would take the night off the draw page -- the one
        # page a club goes to when a night is missing -- and shrink the "of N"
        # a completed season is measured against.
        comp = make_competition(["rockets", "iguanas"], declared=True)
        rounds = comp.rounds_from_matches(
            [make_match(), make_match(scores=((6, 0), (6, 0), (6, 0), (6, 0)))])
        self.assertEqual(rounds["1"].fixtures, (("rockets", "iguanas"),))

    def test_the_sides_the_other_way_round_stay_two_fixtures(self):
        # Deliberately not merged. Which side was at home is a fact off the card,
        # and merging these would have this program pick one of two answers to a
        # question it refuses to guess about everywhere else. A team down to play
        # twice in a night is an error validate.py raises by name.
        comp = make_competition(["rockets", "iguanas"], declared=True)
        rounds = comp.rounds_from_matches(
            [make_match(), make_match(home="iguanas", away="rockets")])
        self.assertEqual(rounds["1"].fixtures,
                         (("iguanas", "rockets"), ("rockets", "iguanas")))


class TestAFixtureAppearsOnceInARound(unittest.TestCase):
    """`dedupe_fixtures`, the one place that rule lives.

    Three code paths reconstruct a draw and each built its fixtures one per card,
    which is one per fixture only until a club exports a night twice. Written once
    here for the same reason `infer_byes` is written once: three copies of a rule
    eventually become three rules.
    """

    # Deliberately not in sorted order: the order to keep is the order the caller
    # handed over -- card order, or the order the club wrote its draw in -- and a
    # sorted list would pass a test whose fixtures happened to be alphabetical.
    LISTED = [("c", "d"), ("a", "b"), ("c", "d"), ("e", "f")]

    def test_a_pairing_is_kept_where_it_first_appears(self):
        kept, _ = comp_mod.dedupe_fixtures(self.LISTED)
        self.assertEqual(kept, (("c", "d"), ("a", "b"), ("e", "f")))

    def test_the_repeats_are_handed_back_so_a_caller_can_say_so(self):
        _, repeats = comp_mod.dedupe_fixtures(self.LISTED)
        self.assertEqual(repeats, (("c", "d"),))

    def test_a_pairing_listed_three_times_is_reported_once(self):
        # A club that pasted a line twice is one mistake, not two.
        kept, repeats = comp_mod.dedupe_fixtures(
            [("a", "b"), ("a", "b"), ("a", "b")])
        self.assertEqual(kept, (("a", "b"),))
        self.assertEqual(repeats, (("a", "b"),))

    def test_a_clean_round_reports_nothing(self):
        kept, repeats = comp_mod.dedupe_fixtures([("a", "b"), ("c", "d")])
        self.assertEqual(kept, (("a", "b"), ("c", "d")))
        self.assertEqual(repeats, ())

    def test_an_empty_round_is_an_empty_round(self):
        self.assertEqual(comp_mod.dedupe_fixtures([]), ((), ()))

    def test_the_sides_the_other_way_round_are_a_different_fixture(self):
        kept, repeats = comp_mod.dedupe_fixtures([("a", "b"), ("b", "a")])
        self.assertEqual(kept, (("a", "b"), ("b", "a")))
        self.assertEqual(repeats, ())


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
        # Nothing declared, and the defaults in force where a rule is asked for.
        self.assertEqual((loaded.scoring, loaded.sets), ("", ""))
        self.assertEqual(loaded.ruleset_for().name, scoring.DEFAULT_RULESET)
        self.assertEqual(loaded.set_rule_for().name, fmt_mod.DEFAULT_SET_RULE)
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

    def test_namesakes_are_normalised_to_slugs_on_both_sides_too(self):
        # The report offers a line carrying slugs and a club editing by hand types
        # the name and the banners as the card prints them, so both have to load the
        # same. Team names go through `team_slug`, not `slugify`, because that is
        # what every banner on every card went through.
        loaded = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = ["RIVERSIDE ROCKETS", "hilltop-aces"]\n'))
        self.assertEqual(loaded.namesakes,
                         {"j-smith": ("riverside-rockets", "hilltop-aces")})
        # The club's own prefix included, because every banner on every card is
        # stripped of it on the way to a slug. An entry typed out in full has to
        # land on the team it names or it separates nobody.
        stripped = config_mod.load(self.write(
            '[club]\nstrip_team_prefix = "Riverside"\n'
            '[namesakes]\n"J Smith" = ["Riverside Rockets", "Riverside Aces"]\n'))
        self.assertEqual(stripped.namesakes, {"j-smith": ("rockets", "aces")})

    def test_a_namesake_team_named_twice_is_one_team_not_two(self):
        # Two spellings of one banner is the likeliest way to write two entries that
        # are the same team, and counting it as two would pass the two-team check
        # below while separating nothing.
        loaded = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = ["RIVERSIDE ROCKETS", "Riverside Rockets"]\n'))
        self.assertEqual(loaded.namesakes, {})
        self.assertTrue([w for w in loaded.warnings if "at least two teams" in w])

    def test_a_namesake_with_one_team_is_refused_and_said_out_loud(self):
        # It would split nothing: one team's roster row is already a player of its
        # own. So the club has either mistyped a team or misread the setting, and
        # accepting it silently leaves them believing it took effect.
        loaded = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = ["Riverside Rockets"]\n'))
        self.assertEqual(loaded.namesakes, {})
        self.assertTrue([w for w in loaded.warnings if "at least two teams" in w])

    def test_a_namesake_given_one_name_instead_of_a_list_is_warned_about(self):
        # `"j-smith" = "Riverside Rockets"` is the shape of an [aliases] entry, and
        # a string is iterable, so the forgiving reading of it would split the name
        # across sixteen teams called R, i, v, e, r...
        loaded = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = "Riverside Rockets"\n'))
        self.assertEqual(loaded.namesakes, {})
        self.assertTrue([w for w in loaded.warnings if "should be a list" in w])

    def test_a_namesakes_section_that_is_not_a_section_is_warned_about(self):
        loaded = config_mod.load(self.write('namesakes = ["J Smith"]\n'))
        self.assertEqual(loaded.namesakes, {})
        self.assertTrue([w for w in loaded.warnings
                         if "[namesakes] should be a section" in w])

    def test_a_namesake_key_or_team_with_no_letters_in_it_is_warned_about(self):
        loaded = config_mod.load(self.write(
            '[namesakes]\n"--" = ["Riverside Rockets", "Hilltop Aces"]\n'))
        self.assertEqual(loaded.namesakes, {})
        self.assertTrue([w for w in loaded.warnings if "no letters or digits" in w])
        teams = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = ["Riverside Rockets", "--", "Hilltop Aces"]\n'))
        self.assertEqual(teams.namesakes,
                         {"j-smith": ("riverside-rockets", "hilltop-aces")})
        self.assertTrue([w for w in teams.warnings
                         if "names a team with no letters" in w])

    def test_a_declared_namesake_becomes_one_player_slug_per_team(self):
        loaded = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = ["Riverside Rockets", "Hilltop Aces"]\n'))
        self.assertEqual(loaded.split_namesake("j-smith", "riverside-rockets"),
                         "j-smith-riverside-rockets")
        self.assertEqual(loaded.split_namesake("j-smith", "hilltop-aces"),
                         "j-smith-hilltop-aces")

    def test_and_a_name_or_team_nobody_declared_comes_back_untouched(self):
        # A team the declaration did not name is the case that must not move: one
        # name in two roster blocks is equally a player who changed teams, and
        # splitting that would invent somebody who does not exist.
        loaded = config_mod.load(self.write(
            '[namesakes]\n"J Smith" = ["Riverside Rockets", "Hilltop Aces"]\n'))
        self.assertEqual(loaded.split_namesake("j-smith", "lakeside-spinners"),
                         "j-smith")
        self.assertEqual(loaded.split_namesake("j-smith", ""), "j-smith")
        self.assertEqual(loaded.split_namesake("a-nother", "riverside-rockets"),
                         "a-nother")

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


class TestAWholeNumberPythonCannotPrintIsNotTomlThisReads(unittest.TestCase):
    """``config.read_toml`` refuses a number exactly where ``str()`` would.

    Python prints a whole number of up to ``sys.get_int_max_str_digits()`` digits
    and raises ValueError past it. tomllib reads a decimal one through that limit,
    so it raises there, and reads a hexadecimal one without it, so the ValueError
    came later, from whichever line printed it (/tmp/t6/scratch/m4/w88h: the
    sheet's ``after_round``, a round's ``number``, ``season``, ``rounds_total``,
    and an index's ``round``). So every number read is held to the limit once,
    here, at the place both TOML files are read. Rejected: TOML's own limit of 64
    bits, which would refuse a config that builds today; and no limit when Python
    has none, which the last test measures.
    """

    def test_tomllibs_own_mistakes_keep_their_line_and_column(self):
        with self.assertRaises(config_mod.NotToml) as caught:
            config_mod.read_toml("[club\n")
        self.assertEqual(str(caught.exception), "Expected ']' at the end of a table "
                                                "declaration (at line 1, column 6)")
        self.assertTrue(caught.exception.invalid)

    def test_the_largest_number_python_prints_is_read_and_one_more_is_not(self):
        limit = sys.get_int_max_str_digits()
        widest = 10 ** limit - 1
        self.assertEqual(config_mod.read_toml(f"n = {hex(widest)}"), {"n": widest})
        self.assertEqual(config_mod.read_toml(f"n = {'9' * limit}"), {"n": widest})
        for text in (f"n = {hex(widest + 1)}", f"t = [{{ n = {hex(widest + 1)} }}]",
                     f"n = 1{'0' * limit}"):
            with self.subTest(text=text[:12]):
                with self.assertRaises(config_mod.NotToml) as caught:
                    config_mod.read_toml(text)
                self.assertEqual(str(caught.exception), f"a whole number in it is "
                                                        f"more than {limit} decimal "
                                                        f"digits long")
                self.assertFalse(caught.exception.invalid, "it is valid TOML")

    def test_with_no_limit_set_every_number_is_read(self):
        self.addCleanup(sys.set_int_max_str_digits, sys.get_int_max_str_digits())
        sys.set_int_max_str_digits(0)
        self.assertEqual(config_mod.read_toml("n = 0x" + "f" * 5000),
                         {"n": 16 ** 5000 - 1})


class TestZeroSetsForAResultIsRefusedByName(unittest.TestCase):
    """``min_sets_for_result = 0`` was read as leaving the line out.

    Measured before this: ``[rules] min_sets_for_result = 0`` on a season with two
    matches short of a result ran exactly as the file with no such line, exit 1
    and the same two errors, because the threshold was resolved with ``or``. And a
    number that is declared now silences the review list's question about it, so a
    0 would have hidden the question while the default it was taken for stayed in
    force. -1 did stop the run, as "min_sets_for_result=-1 is outside 1..4",
    naming neither the file nor the section it was typed in.

    Rejected: reading 0 as "every match has a result", which gives a card with no
    set finished a winner off its games.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, text: str) -> Path:
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    SECTION = '[competitions.tuesday-division-c]\nday = "Tuesday"\ndivision = "C"\n'

    def places(self, value: int):
        yield (f"[rules]\nmin_sets_for_result = {value}\n",
               "[rules] min_sets_for_result")
        yield (f"{self.SECTION}min_sets_for_result = {value}\n",
               "[competitions.tuesday-division-c] min_sets_for_result")

    def test_nothing_or_less_is_refused_naming_the_file_and_the_line(self):
        for value in (0, -1):
            for text, label in self.places(value):
                with self.subTest(value=value, label=label):
                    path = self.write(text)
                    with self.assertRaises(config_mod.ConfigError) as caught:
                        config_mod.load(path)
                    said = str(caught.exception)
                    self.assertIn(str(path), said)
                    self.assertIn(f"{label} must be 1 or more, got {value}", said)

    def test_one_set_is_a_number_a_result_can_need(self):
        loaded = config_mod.load(self.write(
            f"[rules]\nmin_sets_for_result = 1\n{self.SECTION}"
            f"min_sets_for_result = 1\n"))
        self.assertEqual(loaded.min_sets_for_result, 1)
        self.assertEqual(
            loaded.competition("tuesday-division-c").min_sets_for_result, 1)


class TestOneRulesNumberIsOfferedOnlyWhereItChangesNoResult(unittest.TestCase):
    """``rules_minimum`` and ``rules_format``: one ``[rules]`` line, or none.

    A ``[rules]`` value is every competition's that does not set its own. Measured
    on a four-set grade beside a two-set one, the file offered ``# min_sets_for_result
    = 3`` and ``# format = "doubles-singles-doubles"`` under ``[rules]`` for both:
    the first stopped the run at the two-set grade, the second refused all six of
    its cards. So a number or a shape is offered there only when every competition
    it would reach already has it, which is when adopting it changes nothing.

    Rejected: the smallest number, which would give the four-set grade results off
    two sets; and the first competition's shape, which is the defect.
    """

    def setUp(self):
        self.fours = make_competition(["riverside-rockets", "iguanas"])
        self.other_fours = dataclasses.replace(self.fours, id="thursday-division-c")
        self.pairs = dataclasses.replace(self.fours, id="wednesday-division-c",
                                         fmt=fmt_mod.match_format("doubles-2"))
        self.blank = config_mod.Config(path=Path("config.toml"))

    def declaring(self, **fields) -> config_mod.Config:
        return config_mod.Config(path=Path("config.toml"), competitions={
            self.pairs.id: config_mod.CompetitionConfig(id=self.pairs.id, **fields)})

    def test_two_four_set_grades_are_offered_what_they_both_have(self):
        both = [self.fours, self.other_fours]
        self.assertEqual(self.blank.rules_minimum(both), 3)
        self.assertEqual(self.blank.rules_format(both), "doubles-singles-doubles")

    def test_a_four_set_grade_and_a_two_set_grade_are_offered_nothing(self):
        both = [self.fours, self.pairs]
        self.assertIsNone(self.blank.rules_minimum(both))
        self.assertIsNone(self.blank.rules_format(both))

    def test_a_grade_that_says_its_own_is_not_asked(self):
        both = [self.fours, self.pairs]
        self.assertEqual(self.declaring(min_sets_for_result=2).rules_minimum(both), 3)
        for fields in ({"format": "doubles-2"}, {"slots": ("doubles", "doubles")}):
            with self.subTest(**fields):
                self.assertEqual(self.declaring(**fields).rules_format(both),
                                 "doubles-singles-doubles")
        # Its own shape is not its own number: a [rules] 3 would still reach it.
        self.assertIsNone(self.declaring(format="doubles-2").rules_minimum(both))

    def test_no_competition_is_no_answer(self):
        self.assertIsNone(self.blank.rules_minimum([]))
        self.assertIsNone(self.blank.rules_format([]))


class TestAnIgnoredKeyIsToldWhichOneItNearlyIs(unittest.TestCase):
    """Naming the key a club typed is half the job; naming the one they meant is the rest.

    Every ignored key is already reported on every run, which is the hard half. What
    the report could not do was end the search: `[clubb] is not a section this program
    understands` leaves somebody reading the documentation for a section that is one
    keystroke from the one they wanted, and the settings are near-misses of each other
    too -- `slets` sits one edit from both `slots` and `sets`, and a club cannot be
    expected to know which of the two it was aiming at.

    The distance comes from :func:`slugs.nearest`, which answers the same
    problem in team names, and its limit of two edits is what keeps the guess honest:
    `[club] email` is nothing like `name`, so nothing is offered. A wrong guess is
    worse than none, because it sends a reader to a setting that will not do what they
    asked either, and they have no way to tell that from the report.

    Suggested, never applied. Reading `naem` as `name` would mean a club's config took
    effect in a spelling nobody wrote, and the next hand-edit of that line would
    quietly stop working -- which is the silence this whole file exists to avoid.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def load(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return config_mod.load(path)

    def warning_about(self, text, key) -> str:
        """The one warning that names ``key``.

        Asked for by key rather than by position, so a suggestion attached to some
        other warning cannot be read as this one's.
        """
        found = [w for w in self.load(text).warnings if key in w]
        self.assertEqual(len(found), 1,
                         f"expected one warning naming {key!r}, got {found}")
        return found[0]

    def test_a_mistyped_section_is_told_which_section_it_nearly_is(self):
        warning = self.warning_about('[clubb]\nname = "Riverside"\n', "clubb")
        self.assertIn("Did you mean club?", warning)

    def test_a_mistyped_setting_is_told_too(self):
        # Drawn from the settings of the section it was written in: `scorring` is
        # nothing like a section name, so a guess taken from those finds nothing.
        warning = self.warning_about('[rules]\nscorring = "rubbers"\n', "scorring")
        self.assertIn("Did you mean scoring?", warning)

    def test_a_mistyped_setting_inside_a_competition_is_told_too(self):
        # The longest list of settings in the file, and the section a club edits by
        # hand most often, because it is the one discovery writes for them.
        warning = self.warning_about(
            '[competitions."tuesday-division-c"]\nrounds_totl = 10\n', "rounds_totl")
        self.assertIn("Did you mean rounds_total?", warning)

    def test_two_settings_equally_close_are_both_offered(self):
        # Both are real settings and both are one edit away, so choosing between them
        # would be inventing an intention. Offer the pair and let the club pick.
        warning = self.warning_about('[rules]\nslets = ["doubles"]\n', "slets")
        self.assertIn("Did you mean sets or slots? Key the line on the one it is for.",
                      warning)

    def test_a_setting_nearer_one_than_the_other_is_told_the_nearer_alone(self):
        """``lets`` is one edit from ``sets`` and two from ``slots``, and names sets.

        The loader's copy of the question named every setting within two edits.
        Measured before, loading ``[rules]`` with ``lets``: ``Did you mean one of
        sets, slots?``, a tie where there is none. Over every one-edit slip of every
        setting and section the loader knows, 21,480, that list and the nearest
        differed for 827 (/tmp/t6/scratch/m23/vocab-before.txt). The README says the
        nearest is named, and both only when two are equally close, which is
        ``slugs.nearest``, and the question is ``slugs.did_you_mean``'s now.

        Rejected: the team rule, ``discover.nearest_team``. A setting has no
        siblings, no setting or section has a hyphen or a digit, and over the same
        21,480 slips the sibling rule changed the answer for none of them
        (/tmp/t6/scratch/m23/questions-before.txt).
        """
        warning = self.warning_about('[rules]\nlets = ["doubles"]\n', "lets")
        self.assertIn("was ignored. Did you mean sets?", warning)
        self.assertNotIn("slots", warning)

    def test_a_key_that_is_nothing_like_a_setting_gets_no_guess(self):
        warning = self.warning_about(
            '[club]\nemail = "captain@example.org"\n', "email")
        self.assertIn("was ignored", warning)
        self.assertNotIn("Did you mean", warning)

    def test_the_guess_is_added_to_the_report_rather_than_replacing_it(self):
        # The key as typed is what a club searches its own file for, and the sentence
        # saying it did nothing is why they are searching.
        for text, key in (('[clubb]\nname = "Riverside"\n', "clubb"),
                          ('[rules]\nscorring = "rubbers"\n', "scorring")):
            with self.subTest(key=key):
                warning = self.warning_about(text, key)
                # Two sentences, in that order: what happened, then what to try.
                self.assertIn("was ignored. Did you mean", warning)

    def test_the_guess_is_offered_and_not_applied(self):
        loaded = self.load('[club]\nnaem = "Riverside Tennis Club"\n')
        self.assertIn("Did you mean name?", " ".join(loaded.warnings))
        self.assertEqual(loaded.club_name, "")


class TestTheModeAskedForWhereTheLabelsGoTakesEffectNowhere(unittest.TestCase):
    """Four ways to ask for a ladder per season, three of which did nothing quietly.

    ``seasons`` under ``[build]`` is the mode -- one ladder per season, or the whole
    archive on one -- and ``[seasons]`` is a table of folder name to readable label.
    Two spellings one word apart, and a club that has just read about seasons reaches
    for whichever it saw last: ``seasons = "multi"`` at the top of the file, or
    ``mode = "multi"`` inside ``[seasons]``. Neither sets anything. The first was
    reported as `[seasons] should be a section of key = "value" pairs; ignored`, which
    never mentions ``[build]``; the second was accepted in silence as the label of a
    season folder called ``mode``.

    The third silence is the one a club is likeliest to reach with real cards behind
    it: a ``[seasons]`` table of correct labels while the mode is still ``single``.
    Every label is ignored, because in single-season mode there is no season to label
    -- and the table is the part of seasons a club can see working, so writing it
    looks like turning seasons on.

    All three matter because of what the run does next. Every card under ``data/``
    goes onto one ladder, so a second season lands on the first season's fixtures,
    and what a club is shown is either a ladder missing the fixtures two cards
    disagree about or one counting teams twice -- for a setting they believe they
    have set.

    Warned, never applied, which is the rule the near-misses above follow for the
    same reason: a mode read off a line no documentation mentions makes the shape of
    a dashboard depend on a spelling, and the club that copies that config next gets
    an answer the README does not promise. So each warning names the line to write
    instead, and the labels a club has already typed are kept exactly as typed.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def load(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return config_mod.load(path)

    def advice(self, text) -> str:
        """The one warning that names the section the mode really lives in.

        Asked for by ``[build]`` rather than by position: a warning about some other
        setting must not be able to stand in for this one, and the whole complaint
        about the message this replaces is that it never said ``[build]``.
        """
        found = [w for w in self.load(text).warnings if "[build]" in w]
        self.assertEqual(len(found), 1,
                         f"expected one warning naming [build], got {found}")
        return found[0]

    def seasons_warnings(self, text) -> list[str]:
        return [w for w in self.load(text).warnings if "seasons" in w]

    def test_a_label_table_that_cannot_be_used_yet_says_so(self):
        loaded = self.load('[seasons]\n"2025" = "2025 season"\n'
                           '"2026" = "2026 season"\n')
        warning, = [w for w in loaded.warnings if "[build]" in w]
        self.assertIn('seasons = "multi"', warning)
        # And the labels are kept, so adding that one line is the whole edit.
        self.assertEqual(loaded.season_labels,
                         {"2025": "2025 season", "2026": "2026 season"})

    def test_the_same_table_with_the_mode_set_is_not_warned_about_at_all(self):
        text = '[build]\nseasons = "multi"\n\n[seasons]\n"2025" = "2025 season"\n'
        self.assertEqual(self.seasons_warnings(text), [])
        self.assertTrue(self.load(text).multi_season)

    def test_the_mode_written_at_the_top_of_the_file_names_where_it_belongs(self):
        self.assertIn('seasons = "multi"',
                      self.advice('seasons = "multi"\n\n[club]\nname = "Quolls"\n'))

    def test_and_is_offered_there_rather_than_read_from_where_it_was_written(self):
        self.assertEqual(
            self.load('seasons = "multi"\n\n[club]\nname = "Quolls"\n').seasons_mode,
            "single")

    def test_a_top_level_seasons_that_is_no_mode_is_still_just_a_bad_table(self):
        # The guess is keyed on the value, not on the key: `seasons = 4` is somebody
        # mid-thought about their own file, and telling them about [build] would send
        # them to a setting that takes no number either.
        warning, = self.seasons_warnings("seasons = 4\n")
        self.assertIn("section of key", warning)
        self.assertNotIn("[build]", warning)

    def test_the_mode_written_inside_the_label_table_names_where_it_belongs(self):
        # Three keys, because a club reaching into this table for the mode writes
        # whichever word it has just read: the key it is documented under, or the
        # section's own name repeated inside itself.
        for key in ("mode", "season", "seasons"):
            with self.subTest(key=key):
                text = f'[seasons]\n{key} = "multi"\n'
                warning = self.advice(text)
                self.assertIn('seasons = "multi"', warning)
                # And it quotes the line it is about. The other warning this file can
                # get -- a table of labels the mode has not turned on -- also names
                # [build] and also asks for exactly this line, so a test that looks
                # only for the line to add passes while a club is told its folder
                # called `mode` is a season whose label will start working.
                self.assertIn(f'[seasons] {key} = "multi"', warning)
                self.assertEqual(self.load(text).seasons_mode, "single")

    def test_that_line_is_not_also_reported_as_a_table_doing_nothing(self):
        # One line, one complaint. Both checks can see this file, and a club told
        # twice goes looking for two mistakes.
        self.assertEqual(len(self.seasons_warnings('[seasons]\nmode = "multi"\n')), 1)

    def test_a_season_folder_a_club_really_called_mode_is_labelled_not_corrected(self):
        # Multi-season mode, so the only check that could fire here is the one about
        # a misplaced mode -- and this value is nobody's mode.
        loaded = self.load('[build]\nseasons = "multi"\n\n[seasons]\n'
                           'mode = "Modest Cup"\n')
        self.assertEqual(loaded.season_labels, {"mode": "Modest Cup"})
        self.assertEqual([w for w in loaded.warnings if "[build]" in w], [])

    def test_the_misplaced_line_is_left_in_the_table_it_was_written_in(self):
        # Not dropped. A club reading the warning has to find the line it is about,
        # and a table the program has quietly edited is one they cannot match to it.
        self.assertEqual(self.load('[seasons]\nmode = "multi"\n').season_labels,
                         {"mode": "multi"})

    def test_the_singular_spelling_under_build_is_told_which_key_it_nearly_is(self):
        # The fourth way, and the one that already worked: `season` is an unknown
        # [build] setting, so the near-miss machinery above names `seasons`.
        loaded = self.load('[build]\nseason = "multi"\n')
        self.assertIn("Did you mean seasons?", " ".join(loaded.warnings))
        self.assertEqual(loaded.seasons_mode, "single")


class TestTwoSectionsCannotClaimOneCompetition(unittest.TestCase):
    """A config that contradicts itself about where cards go is refused, not resolved.

    Cards are matched to a section by its ``day`` and ``division`` fields rather than by
    the section's name, which is what lets a club name its sections whatever it likes --
    and also what lets two of them claim the same competition. The easy way in is to
    rename a section and leave the old one behind, or to copy one as a template and edit
    only the label.

    Whichever section wins, the other's team list, captains, byes, round dates and
    ``rounds_total`` quietly stop taking effect, and there is nothing on the page to say
    so: a closed roster reverts to an open one, a bye stops being credited, a season
    three rounds into ten can read as complete. There is no defensible way to choose,
    either -- both sections are equally about this competition and their settings
    disagree -- so the run stops before it builds anything and names the two, alongside
    the club's other self-contradictions (a team listed twice, a round declared twice).
    Ten seconds to fix; the alternative is a dashboard whose figures a club cannot
    account for.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def load(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return config_mod.load(path)

    def refusal(self, text) -> str:
        with self.assertRaises(config_mod.ConfigError) as caught:
            self.load(text)
        return str(caught.exception)

    def test_two_sections_claiming_one_day_and_division_are_refused(self):
        message = self.refusal(
            '[competitions."tuesday-division-c"]\n'
            'day = "Tuesday"\ndivision = "C"\nteams = ["Rockets", "Aces"]\n'
            '[competitions."tuesday-c-old"]\n'
            'day = "Tuesday"\ndivision = "C"\nrounds_total = 14\n')
        # Both, because deleting one is the fix and a club cannot delete what it
        # cannot find.
        self.assertIn("tuesday-division-c", message)
        self.assertIn("tuesday-c-old", message)

    def test_the_refusal_says_what_the_two_agree_on(self):
        message = self.refusal(
            '[competitions."a"]\nday = "Tuesday"\ndivision = "C"\n'
            '[competitions."b"]\nday = "Tuesday"\ndivision = "C"\n')
        self.assertIn("Tuesday", message)
        self.assertIn("C", message)

    def test_two_spellings_of_one_night_are_still_one_night(self):
        # The router matches on slugs, so this pair collides there too. A check that
        # compared the raw strings would pass the config and then file every card
        # under one of them.
        message = self.refusal(
            '[competitions."a"]\nday = "Tuesday"\ndivision = "C"\n'
            '[competitions."b"]\nday = "TUESDAY "\ndivision = "c"\n')
        self.assertIn("both claim", message)

    def test_two_seasons_of_one_division_are_not_a_collision(self):
        # Multi-season mode exists for exactly this, and the season key is what tells
        # them apart -- in the router and so here too.
        loaded = self.load(
            '[build]\nseasons = "multi"\n'
            '[competitions."winter-tuesday-c"]\n'
            'day = "Tuesday"\ndivision = "C"\nseason_key = "2026-winter"\n'
            '[competitions."summer-tuesday-c"]\n'
            'day = "Tuesday"\ndivision = "C"\nseason_key = "2026-summer"\n')
        self.assertEqual(len(loaded.competitions), 2)

    def test_a_section_missing_one_of_the_two_fields_claims_nothing(self):
        # The router skips those sections, so they cannot be in a collision. Refusing
        # them here would refuse a config that works -- a section holding only a label
        # and a team list, matched by nothing, is already reported as unused.
        loaded = self.load(
            '[competitions."tuesday-division-c"]\n'
            'day = "Tuesday"\ndivision = "C"\n'
            '[competitions."half-filled"]\nday = "Tuesday"\n'
            '[competitions."empty"]\nlabel = "Someday"\n')
        self.assertEqual(len(loaded.competitions), 3)

    def test_two_half_filled_sections_are_incomplete_rather_than_in_conflict(self):
        # The likeliest half-filled config there is: the day is easy to fill in and the
        # division is the line people leave for later. Neither section claims a card,
        # so neither can be taking cards from the other, and the report already says
        # they match nothing. Refusing this would refuse a config that works.
        loaded = self.load(
            '[competitions."tuesday-b"]\nday = "Tuesday"\nrounds_total = 14\n'
            '[competitions."tuesday-c"]\nday = "Tuesday"\nrounds_total = 10\n')
        self.assertEqual(len(loaded.competitions), 2)

    def test_a_division_that_is_only_punctuation_is_compared_not_crashed_on(self):
        # There is no filing a subject under "-", so the slug helper refuses it; a
        # club's odd division label still has to be comparable, and the club needs a
        # sentence rather than a traceback.
        message = self.refusal(
            '[competitions."a"]\nday = "Tuesday"\ndivision = "-"\n'
            '[competitions."b"]\nday = "Tuesday"\ndivision = "-"\n')
        self.assertIn("both claim", message)

    def test_a_club_keeping_last_season_is_told_how_to_keep_both(self):
        # "Delete one" is the wrong advice for two real seasons of one division, and
        # this is the shape a club arrives in with them.
        message = self.refusal(
            '[competitions."tuesday-c-2025"]\nday = "Tuesday"\ndivision = "C"\n'
            '[competitions."tuesday-c-2026"]\nday = "Tuesday"\ndivision = "C"\n')
        self.assertIn("season_key", message)
        self.assertIn('seasons = "multi"', message)

    def test_a_club_already_in_multi_season_mode_is_told_only_the_line_it_lacks(self):
        """The same pair, in the mode the advice asks for: the half left to do.

        This is the file ``--discover`` wrote in multi-season mode for two seasons of
        one division, before it wrote ``season_key``, and the refusal told the club to
        set ``seasons = "multi"`` -- the line three lines up in the file it was
        refusing. Measured on /tmp/t4/scratch/i417/m417. A club told to do what it has
        done concludes the rest of the sentence is as wrong, and the rest is the fix.

        What the key is has to be in the sentence, because a club in this mode never
        typed one: it is the folder under data/ the section's cards are in.
        Rejected: dropping the second sentence in this mode, which leaves "delete
        one, or merge them" as the only advice about two real seasons.
        """
        message = self.refusal(
            '[build]\nseasons = "multi"\n'
            '[competitions."2025-tuesday-division-c"]\nday = "Tuesday"\n'
            'division = "C"\n'
            '[competitions."2026-tuesday-division-c"]\nday = "Tuesday"\n'
            'division = "C"\n')
        self.assertNotIn('seasons = "multi"', message)
        self.assertIn("give each section its own season_key", message)
        self.assertIn("folder under data/", message)

    def test_two_spellings_of_one_season_folder_are_one_claim(self):
        """A card's season key is its folder's name slugged; a section's was not.

        So ``season_key = "2026-Winter"``, the folder's name as the multi-season
        refusal tells a club to type it, matched no card filed from
        ``data/2026-Winter/``: measured on /tmp/t4/scratch/i417/fix2, both sections
        were reported as having nothing filed under them and the cards were built
        under generated ids beside them. A declared key is slugged as the folder is,
        and so two spellings of one folder are one claim, refused like any other.
        Rejected: telling the club to type the slug, which is a second spelling of a
        name it can already see.
        """
        message = self.refusal(
            '[build]\nseasons = "multi"\n'
            '[competitions."a"]\n'
            'day = "Tuesday"\ndivision = "C"\nseason_key = "2026-Winter"\n'
            '[competitions."b"]\n'
            'day = "Tuesday"\ndivision = "C"\nseason_key = "2026 winter"\n')
        self.assertIn("both claim", message)
        self.assertIn("'2026-winter'", message)

    def test_a_pair_that_already_agrees_on_a_season_key_is_not_offered_that(self):
        # They are not two seasons; they are one section written twice. In neither
        # wording: the multi-season sentence asks for a key each, and these two
        # already agree on one.
        message = self.refusal(
            '[build]\nseasons = "multi"\n'
            '[competitions."a"]\n'
            'day = "Tuesday"\ndivision = "C"\nseason_key = "2026-winter"\n'
            '[competitions."b"]\n'
            'day = "Tuesday"\ndivision = "C"\nseason_key = "2026-winter"\n')
        self.assertIn("2026-winter", message)
        self.assertNotIn('seasons = "multi"', message)
        self.assertNotIn("its own season_key", message)

    def test_the_same_day_in_two_divisions_is_two_competitions(self):
        loaded = self.load(
            '[competitions."tuesday-division-b"]\nday = "Tuesday"\ndivision = "B"\n'
            '[competitions."tuesday-division-c"]\nday = "Tuesday"\ndivision = "C"\n')
        self.assertEqual(len(loaded.competitions), 2)


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

    def test_a_credited_round_is_not_described_as_a_match_played(self):
        # This sentence is printed twice -- in the run report and in the
        # dashboard's rules table -- and on the dashboard it sits a scroll above a
        # ladder whose P column counts the matches a team's result was decided in,
        # with W, L and D adding up to it. Calling the credited round a match
        # played contradicts the column beside it and leaves the reader with no
        # word at all for the figure the average really divides by.
        averaged = scoring.unplayed_detail(True)
        self.assertNotIn("match played", averaged)
        self.assertIn("counts in that average", averaged)

    def test_the_other_convention_still_says_it_because_there_it_is_true(self):
        # Under `zero` the round is in neither the played count nor the divisor,
        # so "not counted as a match played" is the whole truth about it -- and it
        # is the line the README quotes.
        self.assertIn("not counted as a match played",
                      scoring.unplayed_detail(False))

    def test_the_sentence_is_built_from_the_clause_and_cannot_drift_from_it(self):
        # Three places say what an unplayed round is worth: the run report and the
        # dashboard's rules banner print the sentence, and the dashboard also says
        # it twice inside its own content -- on a bye's row and on a washed-out
        # card, where a whole sentence beginning "a bye or a washout" would be
        # saying the obvious back to a reader already looking at one. So there is a
        # clause as well as a sentence, and the sentence is built from the clause.
        # Asserted rather than trusted because two returns in one function is
        # exactly how the dashboard came to word this rule for itself: before this,
        # `byeRow` had its own pair of strings, and they had already drifted -- one
        # said "scored at the team's own average" and dropped what the round counts
        # in, the other said "no points, by this competition's rules" and dropped
        # that it is not a match played.
        for flag in (True, False):
            with self.subTest(average_unplayed=flag):
                self.assertIn(scoring.unplayed_worth(flag),
                              scoring.unplayed_detail(flag))

    def test_the_clause_reads_as_a_clause_so_a_caller_can_give_it_a_subject(self):
        # What makes it reusable: no subject, no capital, no full stop. The callers
        # supply "a bye or a washout is", "bye -- " and "The round itself is", and
        # any of the three in front of a sentence would read as a stammer.
        for flag in (True, False):
            with self.subTest(average_unplayed=flag):
                clause = scoring.unplayed_worth(flag)
                self.assertNotIn("bye", clause)
                self.assertNotIn("washout", clause)
                self.assertFalse(clause.endswith("."), clause)
                self.assertEqual(clause[0], clause[0].lower(), clause)

    def test_the_two_clauses_disagree_about_the_thing_that_matters(self):
        # Polarity read off both branches rather than assumed of one, because the
        # cost of swapping them is a page that tells a club the opposite of its own
        # by-laws in a register that looks authoritative.
        credited = scoring.unplayed_worth(True)
        nothing = scoring.unplayed_worth(False)
        self.assertNotEqual(credited, nothing)
        self.assertIn("average", credited)
        self.assertNotIn("average", nothing)
        self.assertIn("not counted as a match played", nothing)


class TestACompetitionThatCannotDraw(unittest.TestCase):
    """``[rules] draws = false``: a grade where a night cannot end level.

    Plenty of associations decide every match -- a tiebreak, a countback on sets,
    a coin toss written into the by-laws -- so a card that comes out level is a
    result somebody failed to record rather than a draw. ``Ruleset`` has been able
    to express that since the first commit (``draw = None``) and until this setting
    nothing could reach it: no preset sets it and the config had no key for it, so
    a capability the docstring describes was decoration.

    A boolean rather than a fifth preset, for the same reason as ``unplayed``: two
    grades can award points identically and differ on this one line.
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

    def test_every_preset_allows_a_draw_until_a_club_says_otherwise(self):
        # The permissive convention is the default because it is the recoverable
        # one: a competition that does decide every match reads a draw on its
        # ladder and goes looking, where a club whose grade genuinely draws would
        # have every level night reported as a missing result.
        for name in scoring.RULESETS:
            with self.subTest(name):
                self.assertTrue(scoring.RULESETS[name].allows_draw)

    def test_saying_no_takes_the_draw_out_of_the_preset_without_replacing_it(self):
        plain = config_mod.load(self.write('[rules]\nscoring = "games-bonus"\n'))
        self.assertTrue(plain.ruleset_for(None).allows_draw)
        none = config_mod.load(self.write(
            '[rules]\nscoring = "games-bonus"\ndraws = false\n'))
        scheme = none.ruleset_for(None)
        self.assertFalse(scheme.allows_draw)
        # Still games-bonus, to the letter: only the one clause moved.
        base = scoring.ruleset("games-bonus")
        self.assertEqual(scheme, dataclasses.replace(base, draw=None))

    def test_saying_yes_is_the_same_as_leaving_it_out(self):
        for text in ('[rules]\ndraws = true\n', '[rules]\n'):
            with self.subTest(text=text):
                loaded = config_mod.load(self.write(text))
                self.assertEqual(loaded.ruleset_for(None),
                                 scoring.ruleset(scoring.DEFAULT_RULESET))

    def test_it_stacks_with_the_unplayed_convention_rather_than_cancelling_it(self):
        # Both settings edit the same preset, one clause each. An implementation
        # that returned early after the first would silently drop the second, and
        # which one it dropped would depend on the order they are read in.
        loaded = config_mod.load(self.write(
            '[rules]\nunplayed = "average"\ndraws = false\n'))
        scheme = loaded.ruleset_for(None)
        self.assertTrue(scheme.average_unplayed)
        self.assertFalse(scheme.allows_draw)

    def test_one_competition_may_forbid_a_draw_while_the_rest_allow_one(self):
        loaded = config_mod.load(self.write(
            '[competitions."tuesday-division-c"]\ndraws = false\n'))
        # No warning: the loader has to list the key among the ones a competition
        # section may carry, and that list is typed in a second place. Left out of
        # it, the line is honoured at the top of the file and reported as a
        # misspelling one section down -- the worst of both answers, and invisible
        # to an assertion about the ruleset alone, which passes either way.
        self.assertEqual(loaded.warnings, ())
        self.assertFalse(loaded.ruleset_for(
            loaded.competition("tuesday-division-c")).allows_draw)
        self.assertTrue(loaded.ruleset_for(None).allows_draw)

    def test_a_grade_that_says_nothing_still_takes_the_setting_from_the_top(self):
        # The commonest arrangement of all: one line under [rules], and a
        # [competitions.<id>] section that is there for its team list. Read as
        # `comp.draws if comp else self.draws`, the grade's silence overrides the
        # club's instruction and the setting does nothing wherever it is needed.
        loaded = config_mod.load(self.write(
            '[rules]\ndraws = false\n'
            '[competitions."tuesday-division-c"]\nday = "Tuesday"\n'))
        self.assertFalse(loaded.ruleset_for(
            loaded.competition("tuesday-division-c")).allows_draw)

    def test_and_the_other_way_round_which_is_the_harder_half(self):
        # `false` at the top and `true` for one grade. Read for truth rather than
        # for presence -- `comp.draws or self.draws` -- this is the case that
        # breaks: the grade's own `true` looks like nothing at all.
        loaded = config_mod.load(self.write(
            '[rules]\ndraws = false\n'
            '[competitions."tuesday-division-c"]\ndraws = true\n'))
        self.assertTrue(loaded.ruleset_for(
            loaded.competition("tuesday-division-c")).allows_draw)
        self.assertFalse(loaded.ruleset_for(None).allows_draw)

    def test_anything_that_is_not_a_yes_or_a_no_stops_the_run(self):
        # Refused rather than warned about, like `unplayed`: ignored, a typo here
        # falls back to a scheme that does allow draws, and every level night is
        # then scored as one in a competition that cannot have them. TOML's own
        # `false` is the only spelling accepted -- "false" and 0 are values a
        # reader would take for the setting working.
        for bad in ('draws = "false"', 'draws = "no"', "draws = 0",
                    'draws = ["false"]'):
            with self.subTest(bad=bad):
                with self.assertRaises(config_mod.ConfigError):
                    config_mod.load(self.write(f"[rules]\n{bad}\n"))
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.write(
                '[competitions."tuesday-division-c"]\ndraws = "nope"\n'))

    def test_the_run_that_fails_names_the_file_and_the_key(self):
        # This is the first thing a club sees when it mistypes, and "must be true
        # or false" is only useful beside the line it is about.
        with self.assertRaises(config_mod.ConfigError) as caught:
            config_mod.load(self.write('[rules]\ndraws = "no"\n'))
        said = str(caught.exception)
        self.assertIn("config.toml", said)
        self.assertIn("draws", said)
        self.assertIn("true or false", said)

    def test_a_near_miss_of_the_key_is_named_rather_than_ignored(self):
        loaded = config_mod.load(self.write('[rules]\ndraw = false\n'))
        self.assertTrue(loaded.ruleset_for(None).allows_draw)
        said = " ".join(loaded.warnings)
        self.assertIn("draw", said)
        self.assertIn("draws", said)

    def test_the_scheme_says_out_loud_that_it_has_no_draw(self):
        # The banner on every page prints this string, and it is the only place a
        # club can confirm the setting took effect. Before this, a scheme with no
        # draw described itself as "win 4, loss 0" -- indistinguishable from one
        # whose draw is worth nothing, which is the other thing entirely.
        for name in scoring.RULESETS:
            with self.subTest(name):
                base = scoring.RULESETS[name]
                self.assertNotIn("no draw", base.describe())
                said = dataclasses.replace(base, draw=None).describe()
                self.assertIn("no draw", said)
                # And the rest of the sentence survives: the club is still told
                # what a win is worth and what decides a level card.
                self.assertIn("decided on", said)

    def test_the_first_run_writes_the_setting_where_a_club_will_read_it(self):
        blank = config_mod.Config(path=self.dir / "config.toml")
        text = config_mod.render(blank, competitions=())
        self.assertIn("draws", text)
        self.assertIn("# draws = false", text)     # commented: unset
        loaded = config_mod.load(self.write(text))
        self.assertEqual(loaded.warnings, ())
        self.assertTrue(loaded.ruleset_for(None).allows_draw)

    def test_a_club_that_has_set_it_gets_it_written_back(self):
        # config.toml is rewritten on runs that discover something new, and a
        # setting dropped on the rewrite is a ladder that changes on a build
        # nobody asked to change anything.
        set_already = config_mod.Config(path=self.dir / "config.toml",
                                        exists=True, draws=False)
        text = config_mod.render(set_already, competitions=())
        self.assertRegex(text, r"(?m)^draws = false$")
        self.assertFalse(config_mod.load(self.write(text))
                         .ruleset_for(None).allows_draw)

    def test_the_setting_explains_itself_in_a_sentence_like_unplayed_does(self):
        # "no draw" inside the scoring line says the scheme has no value for one.
        # What it does not say is what then happens to a card that came out level,
        # which is the part a club has to act on -- and the part that surprises
        # them, because the night leaves the ladder. One sentence, in scoring.py
        # beside the rule, so the terminal and the page cannot word it differently.
        said = scoring.draws_detail(False)
        self.assertIn("level", said)
        self.assertIn("ladder", said)
        self.assertIn("draws = false", said,
                      "the sentence does not name the setting that caused it, so a "
                      "club cannot find the line to change")

    def test_nothing_is_said_where_the_preset_has_a_draw_of_its_own(self):
        # Silence rather than "draws are allowed". The scoring line one row up
        # already prints `draw 3`, and a banner row that repeats it in words costs
        # every club a line to tell almost none of them anything. The empty string
        # is what the banner and the report both test for to decide whether to
        # print the row at all, so this is load-bearing, not a formatting choice.
        self.assertEqual(scoring.draws_detail(True), "")

    def test_it_is_asked_of_the_ruleset_and_not_of_the_config(self):
        # Every consumer of this sentence has a Ruleset in hand and may not have a
        # Config: the page has neither, and a competition can override the setting.
        # So it is keyed on `allows_draw`, which is the fact, rather than on
        # `Config.draws`, which is one of the ways to reach it.
        for name in scoring.RULESETS:
            with self.subTest(name):
                base = scoring.RULESETS[name]
                self.assertEqual(scoring.draws_detail(base.allows_draw), "")
                stripped = dataclasses.replace(base, draw=None)
                self.assertEqual(scoring.draws_detail(stripped.allows_draw),
                                 scoring.draws_detail(False))


class TestWhatAForfeitIsWorthIsSetUnderRulesLikeTheOtherRules(unittest.TestCase):
    """``[rules] forfeit_award`` and ``forfeit_points``, read beside unplayed and draws.

    Measured on the tree before this (/tmp/mcf/scratch/CANCEL/probe/
    config_probe.py, output config-before.txt): ``forfeit_award = "full-games"``,
    ``"fixed"`` with ``forfeit_points = 5``, ``"fixed"`` alone, ``"full-game"``
    and ``forfeit_points = 5`` alone each loaded with the warning "[rules]
    forfeit_award is not a setting this program understands and was ignored." (or
    forfeit_points), and the scheme in force was ``as-played`` with no points
    every time, in a competition's section too. So no club could say what its
    forfeits are worth, though ``Ruleset`` had the fields.

    Now both are RULES_SETTINGS: read under [rules] and in a competition's section,
    applied over the preset by ``ruleset_for``, and written back. An award the
    program does not know is refused as ``unplayed`` is, with the three and the
    nearest. ``fixed`` with no points under it or under [rules] is refused in
    config terms, since the scheme cannot be built; points no ``fixed`` award reads
    are warned about, since the ladder is the one the club had either way.
    Rejected: refusing points without ``fixed``, which stops a build over a line
    that changes nothing; and reading points as choosing ``fixed``, a setting
    taking effect in a word nobody wrote.
    """

    COMP = "tuesday-division-c"

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def refused(self, text) -> str:
        path = self.write(text)
        with self.assertRaises(config_mod.ConfigError) as caught:
            config_mod.load(path)
        return str(caught.exception).replace(f"{path}: ", "", 1)

    def test_both_are_rules_a_competition_may_override(self):
        self.assertIn("forfeit_award", config_mod.RULES_SETTINGS)
        self.assertIn("forfeit_points", config_mod.RULES_SETTINGS)

    def test_each_award_is_applied_over_the_preset_and_nothing_else_moves(self):
        for award, points, extra in (("as-played", None, ""),
                                     ("full-games", None, ""),
                                     ("fixed", 5.0, "forfeit_points = 5\n"),
                                     ("fixed", 6.4, "forfeit_points = 6.4\n")):
            with self.subTest(award=award, points=points):
                loaded = config_mod.load(self.write(
                    f'[rules]\nscoring = "games-bonus"\nforfeit_award = "{award}"\n'
                    + extra))
                self.assertEqual(loaded.warnings, ())
                self.assertEqual(loaded.forfeit_award, award)
                self.assertEqual(loaded.forfeit_points, points)
                self.assertEqual(loaded.ruleset_for(None), dataclasses.replace(
                    scoring.ruleset("games-bonus"), forfeit_award=award,
                    forfeit_points=points))

    def test_left_out_the_preset_s_award_stands(self):
        loaded = config_mod.load(self.write('[rules]\nscoring = "simple"\n'))
        self.assertEqual((loaded.forfeit_award, loaded.forfeit_points), ("", None))
        self.assertEqual(loaded.ruleset_for(None), scoring.ruleset("simple"))

    def test_the_award_is_read_in_any_case_as_unplayed_is(self):
        loaded = config_mod.load(self.write('[rules]\nforfeit_award = " As-Played"\n'))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.ruleset_for(None).forfeit_award, "as-played")

    def test_a_competition_overrides_the_award_and_the_points(self):
        loaded = config_mod.load(self.write(
            '[rules]\nforfeit_award = "as-played"\n'
            f'[competitions."{self.COMP}"]\nforfeit_award = "fixed"\n'
            'forfeit_points = 4.5\n'))
        comp = loaded.competitions[self.COMP]
        self.assertEqual(loaded.warnings, ())
        self.assertEqual((loaded.ruleset_for(comp).forfeit_award,
                          loaded.ruleset_for(comp).forfeit_points), ("fixed", 4.5))
        self.assertEqual(loaded.ruleset_for(None).forfeit_award, "as-played")

    def test_a_competition_s_points_price_the_award_it_inherits(self):
        loaded = config_mod.load(self.write(
            '[rules]\nforfeit_award = "fixed"\nforfeit_points = 5\n'
            f'[competitions."{self.COMP}"]\nforfeit_points = 3\n'
            '[competitions."monday-division-a"]\nscoring = "simple"\n'
            '[competitions."wednesday-division-b"]\nforfeit_points = 0\n'))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.ruleset_for(loaded.competitions[self.COMP])
                         .forfeit_points, 3.0)
        self.assertEqual(loaded.ruleset_for(
            loaded.competitions["monday-division-a"]).forfeit_points, 5.0)
        # 0 is a price, asked with `is None` as draws is: not [rules]'s 5.
        self.assertEqual(loaded.ruleset_for(
            loaded.competitions["wednesday-division-b"]).forfeit_points, 0.0)

    def test_an_award_it_does_not_know_is_refused_with_the_three_and_the_nearest(self):
        for where, text in (
                ("[rules]", '[rules]\nforfeit_award = "full-game"\n'),
                (f"[competitions.{self.COMP}]",
                 f'[competitions."{self.COMP}"]\nforfeit_award = "full-game"\n')):
            with self.subTest(where=where):
                self.assertEqual(self.refused(text),
                                 f'{where} forfeit_award = "full-game" is not '
                                 '"as-played", "full-games" or "fixed". Did you '
                                 'mean full-games?')
        self.assertEqual(self.refused('[rules]\nforfeit_award = "walkover"\n'),
                         '[rules] forfeit_award = "walkover" is not "as-played", '
                         '"full-games" or "fixed".')

    def test_fixed_with_no_points_is_refused_in_config_terms(self):
        self.assertEqual(
            self.refused('[rules]\nforfeit_award = "fixed"\n'),
            '[rules] forfeit_award = "fixed" pays a flat forfeit_points, and no '
            'forfeit_points is set. Add one: the points a match forfeited whole '
            'pays the side that turned up.')
        self.assertEqual(
            self.refused(f'[competitions."{self.COMP}"]\nforfeit_award = "fixed"\n'),
            f'[competitions.{self.COMP}] forfeit_award = "fixed" pays a flat '
            'forfeit_points, and no forfeit_points is set there or under [rules]. '
            'Add one: the points a match forfeited whole pays the side that turned '
            'up.')

    def test_points_that_are_not_a_price_are_refused(self):
        for typed in ('"5"', "true", "-1", "nan", "inf", "[5]"):
            with self.subTest(typed=typed):
                said = self.refused(f"[rules]\nforfeit_award = \"fixed\"\n"
                                    f"forfeit_points = {typed}\n")
                self.assertTrue(said.startswith(
                    "[rules] forfeit_points must be a number of points, 0 or more, "
                    "got "), said)

    def test_points_no_fixed_award_reads_are_warned_about_and_not_applied(self):
        for where, text, award in (
                ("[rules]", "[rules]\nforfeit_points = 5\n", "full-games"),
                ("[rules]", '[rules]\nforfeit_award = "as-played"\n'
                            'forfeit_points = 5\n', "as-played"),
                (f"[competitions.{self.COMP}]",
                 f'[competitions."{self.COMP}"]\nforfeit_points = 5\n',
                 "full-games")):
            with self.subTest(where=where, award=award):
                loaded = config_mod.load(self.write(text))
                self.assertEqual(loaded.warnings, (
                    f'{where} forfeit_points = 5 was not read: it prices a forfeit '
                    f'only where forfeit_award = "fixed", and forfeit_award is '
                    f'"{award}" here.',))
                comp = loaded.competitions.get(self.COMP)
                self.assertEqual(loaded.ruleset_for(comp),
                                 dataclasses.replace(scoring.ruleset("games-bonus"),
                                                     forfeit_award=award))

    def test_points_a_competition_s_fixed_award_reads_are_not_warned_about(self):
        loaded = config_mod.load(self.write(
            '[rules]\nforfeit_points = 5\n'
            f'[competitions."{self.COMP}"]\nforfeit_award = "fixed"\n'))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.ruleset_for(loaded.competitions[self.COMP])
                         .forfeit_points, 5.0)

    def test_points_every_fixed_award_sets_its_own_beside_are_warned_about(self):
        # [rules]'s points are read only by a "fixed" section with none of its
        # own, so a section pricing itself leaves them unread. Asking whether any
        # section is "fixed" survived every test of the unit as reviewed
        # (/tmp/mcf/scratch/CANCEL-review/own/run.py, #0).
        loaded = config_mod.load(self.write(
            '[rules]\nforfeit_points = 5\n'
            f'[competitions."{self.COMP}"]\nforfeit_award = "fixed"\n'
            'forfeit_points = 4\n'))
        self.assertEqual(loaded.warnings, (
            '[rules] forfeit_points = 5 was not read: it prices a forfeit only '
            'where forfeit_award = "fixed", and forfeit_award is "full-games" '
            'here.',))
        self.assertEqual(loaded.ruleset_for(loaded.competitions[self.COMP])
                         .forfeit_points, 4.0)

    def test_the_first_run_writes_both_behind_a_hash_under_the_draws_line(self):
        text = config_mod.render(config_mod.Config(path=self.dir / "config.toml"),
                                 competitions=())
        rules = text.split("[rules]\n", 1)[1]
        draws = rules.index("# draws = false")
        award = rules.index('# forfeit_award = "full-games"')
        points = rules.index("# forfeit_points = ")
        self.assertLess(draws, award)
        self.assertLess(award, points)
        self.assertLess(points, rules.index("# The shape of a match"))
        self.assertNotRegex(text, r"(?m)^forfeit_(award|points) = ")
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 78)
        loaded = config_mod.load(self.write(text))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.ruleset_for(None).forfeit_award, "full-games")

    def test_the_first_run_says_what_each_award_pays(self):
        # The option table is the one place the file a club edits says what
        # each award does, as unplayed's does for a bye.
        text = config_mod.render(config_mod.Config(path=self.dir / "config.toml"),
                                 competitions=())
        table = text.split("# What a match forfeited whole is worth", 1)[1]
        table = table.split("# forfeit_award = ", 1)[0]
        said = " ".join(line.lstrip("#").strip() for line in table.splitlines())
        said = " ".join(said.split())
        for award, pays in (
                ("as-played", "paid as a match played, on the games the rows "
                              "print"),
                ("full-games", "the side that turned up is paid a win with "
                               "every rubber a love set, and the side that "
                               "gave the match up 0"),
                ("fixed", "the side that turned up is paid forfeit_points, "
                          "and the side that gave the match up 0")):
            with self.subTest(award=award):
                self.assertIn(f"{award} {pays}", said)

    def test_a_club_that_set_them_gets_them_written_back_live(self):
        for award, points in (("as-played", None), ("fixed", 5.0),
                              ("fixed", 6.4)):
            with self.subTest(award=award, points=points):
                text = config_mod.render(config_mod.Config(
                    path=self.dir / "config.toml", exists=True,
                    forfeit_award=award, forfeit_points=points), competitions=())
                self.assertRegex(text, rf'(?m)^forfeit_award = "{award}"$')
                loaded = config_mod.load(self.write(text))
                self.assertEqual(loaded.warnings, ())
                self.assertEqual((loaded.forfeit_award, loaded.forfeit_points),
                                 (award, points))


class TestTheFinalsAreSetUnderRulesLikeTheOtherRules(unittest.TestCase):
    """``[rules] finals``: what the season's finals are, read beside the other rules.

    Measured on the tree before this (/tmp/mcw/finals/probe/config-before.txt):
    ``finals = "placings"`` under [rules] and in a competition's section each
    loaded with the warning "finals is not a setting this program understands and
    was ignored", so no club could say its season ends in placing matches (4.537).

    Now it is one of RULES_SETTINGS: ``"placings"`` or ``"none"``, read in any
    case, under [rules] and in a section, which overrides it either way, and
    written back. ``"none"`` plays as a club that says nothing does, and is kept as
    typed, so a section can turn [rules]'s placings off and F3 knows the club has
    answered the question it would ask. Anything else is refused, as an
    ``unplayed`` the program does not know is: the club wrote the line to change
    what its finals mean. Rejected: a boolean ``placings = true``, which a second
    format would need a second key beside, and the two could then disagree.
    """

    COMP = "tuesday-division-c"

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, text):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def refused(self, text) -> str:
        path = self.write(text)
        with self.assertRaises(config_mod.ConfigError) as caught:
            config_mod.load(path)
        return str(caught.exception).replace(f"{path}: ", "", 1)

    def test_it_is_a_rule_a_competition_may_override(self):
        self.assertIn("finals", config_mod.RULES_SETTINGS)

    def test_placings_is_read_and_none_is_no_format(self):
        for typed, read, applied in (("placings", "placings", "placings"),
                                     ("none", "none", "none"),
                                     (" Placings", "placings", "placings")):
            with self.subTest(typed=typed):
                loaded = config_mod.load(self.write(f'[rules]\nfinals = "{typed}"\n'))
                self.assertEqual(loaded.warnings, ())
                self.assertEqual(loaded.finals, read)
                self.assertEqual(loaded.finals_for(None), applied)

    def test_left_out_there_is_no_format(self):
        loaded = config_mod.load(self.write('[rules]\nscoring = "simple"\n'))
        self.assertEqual((loaded.finals, loaded.finals_for(None)), ("", ""))

    def test_a_competition_overrides_it_either_way(self):
        for rules, own, applied in (("placings", "none", "none"),
                                    ("none", "placings", "placings"),
                                    ("", "placings", "placings"),
                                    ("placings", "", "placings")):
            with self.subTest(rules=rules, own=own):
                text = f'[rules]\nfinals = "{rules}"\n' if rules else ""
                text += f'[competitions."{self.COMP}"]\nday = "Tuesday"\n'
                text += f'finals = "{own}"\n' if own else ""
                loaded = config_mod.load(self.write(text))
                self.assertEqual(loaded.warnings, ())
                self.assertEqual(
                    loaded.finals_for(loaded.competitions[self.COMP]), applied)

    def test_a_format_it_does_not_know_is_refused_with_both_and_the_nearest(self):
        for where, text in (
                ("[rules]", '[rules]\nfinals = "placing"\n'),
                (f"[competitions.{self.COMP}]",
                 f'[competitions."{self.COMP}"]\nfinals = "placing"\n')):
            with self.subTest(where=where):
                self.assertEqual(self.refused(text),
                                 f'{where} finals = "placing" is not "none" or '
                                 '"placings". Did you mean placings?')
        self.assertEqual(self.refused("[rules]\nfinals = true\n"),
                         '[rules] finals = true is not "none" or "placings".')

    def test_the_first_run_writes_it_behind_a_hash_under_the_forfeit_lines(self):
        text = config_mod.render(config_mod.Config(path=self.dir / "config.toml"),
                                 competitions=())
        rules = text.split("[rules]\n", 1)[1]
        points = rules.index("# forfeit_points = ")
        finals = rules.index('# finals = "placings"')
        self.assertLess(points, finals)
        self.assertLess(finals, rules.index("# The shape of a match"))
        self.assertNotRegex(text, r"(?m)^finals = ")
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 78)
        loaded = config_mod.load(self.write(text))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.finals_for(None), "")

    def test_the_first_run_says_what_each_format_does(self):
        text = config_mod.render(config_mod.Config(path=self.dir / "config.toml"),
                                 competitions=())
        table = text.split("# What your finals are", 1)[1]
        table = table.split('# finals = "placings"', 1)[0]
        said = " ".join(line.lstrip("#").strip() for line in table.splitlines())
        said = " ".join(said.split())
        for mode in scoring.FINALS_FORMATS:
            with self.subTest(mode=mode):
                self.assertIn(f"{mode} {scoring.finals_detail(mode)}", said)

    def test_a_club_that_set_it_gets_it_written_back_live(self):
        for mode in scoring.FINALS_FORMATS:
            with self.subTest(mode=mode):
                text = config_mod.render(config_mod.Config(
                    path=self.dir / "config.toml", exists=True, finals=mode),
                    competitions=())
                self.assertRegex(text, rf'(?m)^finals = "{mode}"$')
                loaded = config_mod.load(self.write(text))
                self.assertEqual(loaded.warnings, ())
                self.assertEqual(loaded.finals, mode)

class TestTheLengthsUnderPlacingsLeaveThePlacingWeekOut(unittest.TestCase):
    """4.537, its review: a draw's length, and the one ``--force`` writes, under placings.

    The draw gives the season its length, finals left out, and under placings the
    placing week is left out with them: a Playoff whether or not it was typed a
    final, and the draw's last round, numbered past the rest and typed ``finals =
    true``, since the run after ``--force`` reads it as the first week past the five
    rounds written. Measured on the tree the review read: a draw of five rounds and
    a Playoff read ``5 of 6 rounds played`` for good, and ``--force`` over five
    rounds and a sixth typed a final wrote six, so the run after scored the placing
    week into the ladder. With no format the labels alone decide, as before.
    """

    def config(self, rounds, typed=(), finals="placings"):
        import tempfile
        text = (f'[rules]\nfinals = "{finals}"\n\n' if finals else "")
        text += '[competitions.monday-division-b]\nday = "Monday"\n'
        for number in rounds:
            text += (f'[[competitions.monday-division-b.rounds]]\nnumber = "{number}"\n'
                     + ("finals = true\n" if number in typed else ""))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def section(self, rounds, typed=()):
        return self.config(rounds, typed).competition("monday-division-b")

    def test_a_playoff_is_no_round_of_the_draws_length(self):
        section = self.section(["1", "2", "3", "Playoff"])
        self.assertEqual((section.season_length_for("placings"),
                          section.season_length_for("")), (3, 4))
        self.assertEqual((section.length_by_labels_for("placings"),
                          section.length_by_labels_for("")), (3, 4))
        self.assertEqual(section.finals_after_force("placings"), {"Playoff"})

    def test_the_last_round_typed_a_final_is_the_placing_week_by_its_number(self):
        section = self.section(["1", "2", "3", "4"], typed=("4",))
        self.assertEqual(section.length_by_labels_for("placings"), 3)
        self.assertEqual(section.finals_after_force("placings"), {"4"})
        self.assertEqual(section.length_by_labels_for(""), 4)
        self.assertEqual(section.finals_after_force(""), frozenset())

    def test_a_round_typed_a_final_mid_draw_is_no_placing_week(self):
        section = self.section(["1", "2", "3", "4"], typed=("2",))
        self.assertEqual(section.length_by_labels_for("placings"), 4)
        self.assertEqual(section.finals_after_force("placings"), frozenset())

    def test_two_rounds_typed_finals_are_no_one_week(self):
        section = self.section(["1", "2", "3", "4", "5"], typed=("4", "5"))
        self.assertEqual(section.length_by_labels_for("placings"), 5)

    def test_a_section_no_card_was_filed_under_is_written_with_it(self):
        text = config_mod.render(self.config(["1", "2", "3", "4"], typed=("4",)),
                                 competitions=())
        self.assertIn("\nrounds_total = 3\n", text)
        text = config_mod.render(self.config(["1", "2", "3", "4"], typed=("4",),
                                             finals=""), competitions=())
        self.assertIn("\nrounds_total = 4\n", text)


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


class TestAWashoutIsTypedByRoundAsTheWholeRoundOrSomeOfItsFixtures(unittest.TestCase):
    """``[competitions.<id>.washouts]``: a night rained off, whole or in part (4.535).

    Before it, the loader said ``[competitions.tue-c] washouts is not a setting this
    program understands and was ignored``, and the only line a club had for a round
    rained off with no card to show it was a rest for every team in the byes table,
    which keeps the round out of the rounds played: measured on a fictional season
    of five rounds whose third was washed out (/tmp/mcw/scratch/probe.py), ``4 of 5
    played``. Keyed by round, as a washout is a night and not a team's: ``9 = true``
    is the whole round, and a list of ``[home, away]`` pairs is the fixtures of it
    the rain stopped, the rest having been played. Rejected: a ``washed_out`` key in
    the ``[[...rounds]]`` block, which makes a draw of the one block, a season a
    round long where no ``rounds_total`` is typed; one line per team, the byes
    table's shape, which cannot say which of a round's fixtures it was; and a time of
    night, which no card left behind by a washout has to be held against.
    """

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    HEAD = ('[competitions."monday-division-b"]\n'
            'teams = ["Rockets", "Iguanas", "Aces", "Comets"]\n')

    def loaded(self, text, *, head=HEAD):
        path = self.dir / "config.toml"
        path.write_text(head + text, encoding="utf-8")
        return config_mod.load(path)

    def declared(self, washouts, *, head=HEAD):
        return self.loaded('[competitions."monday-division-b".washouts]\n' + washouts,
                           head=head).competition("monday-division-b")

    def test_a_whole_round_is_true_and_some_of_it_is_its_fixtures(self):
        # An empty tuple is the whole round: there is no fixture to name when the
        # round left no card and no draw is typed.
        comp = self.declared('9 = true\n'
                             '7 = [["Rockets", "Iguanas"], ["Aces", "Comets"]]\n')
        self.assertEqual(comp.washouts, {"9": (),
                                         "7": (("Rockets", "Iguanas"),
                                               ("Aces", "Comets"))})

    def test_one_fixture_needs_no_outer_brackets_and_a_label_may_be_a_word(self):
        # The commonest line is one fixture, and `[["A", "B"]]` for it is the
        # friction the byes table's `Rockets = 6` spares a club too.
        self.assertEqual(self.declared('SF = ["Rockets", "Aces"]\n').washouts,
                         {"SF": (("Rockets", "Aces"),)})

    def test_false_or_an_empty_list_says_nothing_was_washed_out(self):
        # A line a club turns off without deleting it, as `9 = false`, declares
        # nothing; it is not refused, and it washes nothing out.
        self.assertEqual(self.declared('9 = false\n8 = []\n').washouts, {})

    def test_a_team_the_declared_list_does_not_name_stops_the_run(self):
        # Under `unplayed = "average"` a washout credits each of its teams, so a
        # misspelt one would credit nobody and leave the real team a round short.
        with self.assertRaises(config_mod.ConfigError) as caught:
            self.declared('7 = [["Rokcets", "Iguanas"]]\n')
        self.assertIn("Rokcets", str(caught.exception))
        self.assertIn("Rockets", str(caught.exception))

    def test_a_team_twice_in_one_round_or_against_itself_is_refused(self):
        # Either is a line that cannot be a night's fixtures, and which pairing was
        # meant is the club's to say; each is told which it is.
        for written, said in (
                ('7 = [["Rockets", "Iguanas"], ["Rockets", "Aces"]]\n', "two fixtures"),
                ('7 = [["Rockets", "rockets"]]\n', "playing itself")):
            with self.subTest(written=written):
                with self.assertRaises(config_mod.ConfigError) as caught:
                    self.declared(written)
                self.assertIn("round 7", str(caught.exception))
                self.assertIn(said, str(caught.exception))

    def test_a_value_that_is_neither_shape_is_refused_with_the_two_it_may_be(self):
        # `bool` is an `int` in Python and a string is a sequence, so each shape is
        # turned away by name rather than read as something it is not.
        # A string as short as "no" would read as two teams of one letter each if
        # it were taken for a list, so it is turned away as the string it is.
        for written in ('9 = 1\n', '9 = "all"\n', '9 = "no"\n', '9 = [1, 2]\n',
                        '9 = [["Rockets"]]\n',
                        '9 = [["Rockets", "Iguanas", "Aces"]]\n', '9 = [true]\n',
                        '9 = ["Rockets", ["Iguanas", "Aces"]]\n',
                        '9 = { home = "Rockets" }\n'):
            with self.subTest(written=written):
                with self.assertRaises(config_mod.ConfigError) as caught:
                    self.declared(written)
                self.assertIn("9 = true", str(caught.exception))

    def test_a_round_on_two_lines_is_refused(self):
        # `9` and `" 9"` are one round, the spaces round a label being no part of it,
        # and the second line silently replaced the first (the review's #6).
        for written in ('9 = true\n" 9" = [["Rockets", "Iguanas"]]\n',
                        '9 = false\n" 9" = true\n'):
            with self.subTest(written=written):
                with self.assertRaises(config_mod.ConfigError) as caught:
                    self.declared(written)
                self.assertIn("round 9 on two lines", str(caught.exception))

    def test_a_line_with_no_round_is_refused(self):
        # `"" = true` is valid TOML, and a round of no label would be one no card
        # is filed under and a heading of nothing, as a draw block with no number.
        for written in ('"" = true\n', '" " = true\n'):
            with self.subTest(written=written):
                with self.assertRaises(config_mod.ConfigError) as caught:
                    self.declared(written)
                self.assertIn("no round", str(caught.exception))

    def test_a_team_rested_and_washed_out_in_one_round_is_refused(self):
        # Two answers to what one team did that night, and both are the club's own
        # lines; but a whole round washed out over a team resting in it is one: in
        # a five-team grade somebody rests every week, rain or not.
        for head in (self.HEAD + '[competitions."monday-division-b".byes]\n'
                                 'Rockets = 7\n',
                     self.HEAD + '[[competitions."monday-division-b".rounds]]\n'
                                 'number = "7"\nbye = "Rockets"\n'):
            with self.subTest(head=head):
                with self.assertRaises(config_mod.ConfigError) as caught:
                    self.declared('7 = [["Rockets", "Iguanas"]]\n', head=head)
                self.assertIn("Rockets", str(caught.exception))
                self.assertIn("round 7", str(caught.exception))
                self.assertEqual(self.declared('7 = true\n', head=head).washouts,
                                 {"7": ()})

    def test_it_is_taken_on_trust_while_the_team_list_is_still_open(self):
        # As the byes table is: there is nothing to check a name against yet. The
        # run reports a name no card has (`washout-team`).
        comp = self.declared('7 = [["Rokcets", "Iguanas"]]\n',
                             head='[competitions."monday-division-b"]\n')
        self.assertEqual(comp.washouts, {"7": (("Rokcets", "Iguanas"),)})

    def test_the_table_is_no_longer_a_setting_that_was_ignored(self):
        config = self.loaded('[competitions."monday-division-b".washouts]\n9 = true\n')
        self.assertEqual(config.warnings, ())
        self.assertEqual(config.competition("monday-division-b").washouts, {"9": ()})


class TestTheWashoutsTableIsWrittenBackAsItWasRead(unittest.TestCase):
    """``--discover --force`` keeps the washouts, as it keeps the byes (4.535).

    The writer drops the draw and keeps every table a club typed, so a table it did
    not know would be gone after the rewrite and the round with it from the rounds
    played. Written by round, in the order the season runs, each fixture as typed, a
    single fixture with its outer brackets as the loader gives it back. A section the
    cards built and one no card was filed under alike.
    """

    COMP = "tuesday-division-c"          # make_competition's id
    TYPED = '10 = true\n2 = ["Riverside Rockets", "aces"]\n"SF" = false\n'
    WRITTEN = ["[competitions.tuesday-division-c.washouts]",
               '2 = [["Riverside Rockets", "aces"]]',
               "10 = true"]

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.comp = make_competition(["riverside-rockets", "iguanas", "aces"])

    def loaded(self, text: str):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return config_mod.load(path)

    def typed(self):
        return self.loaded(f'[competitions.{self.COMP}]\nday = "Tuesday"\n'
                           f'[competitions.{self.COMP}.washouts]\n{self.TYPED}')

    @staticmethod
    def table(text: str, comp_id: str) -> list[str]:
        lines = text.splitlines()
        start = lines.index(f"[competitions.{comp_id}.washouts]")
        end = next((i for i in range(start, len(lines)) if not lines[i].strip()),
                   len(lines))
        return [line for line in lines[start:end] if not line.startswith("#")]

    def test_each_line_is_written_back_in_the_order_the_season_runs(self):
        for built in ([self.comp], []):
            with self.subTest(cards=bool(built)):
                text = config_mod.render(self.typed(), competitions=built)
                self.assertEqual(self.table(text, self.COMP), self.WRITTEN)

    def test_it_loads_back_as_it_was_read(self):
        config = self.typed()
        for built in ([self.comp], []):
            with self.subTest(cards=bool(built)):
                again = self.loaded(config_mod.render(config, competitions=built))
                self.assertEqual(again.warnings, ())
                self.assertEqual(again.competition(self.COMP).washouts,
                                 config.competition(self.COMP).washouts)

    def test_a_round_toml_cannot_leave_bare_is_written_quoted(self):
        # A fullwidth figure is a round label like any other (4.151), and TOML's
        # bare keys are ASCII alone: written bare, `３ = true` was a file the next run
        # could not load (the review's #3).
        config = self.loaded(f'[competitions.{self.COMP}]\nday = "Tuesday"\n'
                             f'[competitions.{self.COMP}.washouts]\n"３" = true\n')
        text = config_mod.render(config, competitions=[self.comp])
        self.assertIn('"３" = true', text.splitlines())
        self.assertEqual(self.loaded(text).competition(self.COMP).washouts, {"３": ()})

    def test_a_section_with_none_writes_no_table(self):
        # Nothing to keep, and no template either: the README and the example file
        # say how one is typed, and a run that met a gap in the rounds says it.
        text = config_mod.render(self.loaded(f'[competitions.{self.COMP}]\n'
                                             f'day = "Tuesday"\n'),
                                 competitions=[self.comp])
        self.assertNotIn(".washouts]", text)


class TestAKeyIsWrittenBareOnlyWhereTOMLAllowsIt(unittest.TestCase):
    """``config.toml_key``: bare for ASCII letters, digits, ``-`` and ``_``, else quoted.

    TOML's bare keys are those characters alone. Asked by ``str.isalnum``, which is
    true of every letter and figure in Unicode, a key of one word in another script
    was written bare, a team called ``Ñandúes`` in the byes table as a round ``３``
    in the washouts table, and the file the writer had just written would not load
    (4.535's review, #3).
    """

    def test_keys_toml_reads_bare_are_bare_and_the_rest_quoted(self):
        for key, written in (("rockets", "rockets"), ("Rocket-2_b", "Rocket-2_b"),
                             ("９", '"９"'), ("Ñandúes", '"Ñandúes"'),
                             ("Riverside Rockets", '"Riverside Rockets"')):
            with self.subTest(key=key):
                self.assertEqual(config_mod.toml_key(key), written)
                self.assertEqual(tomllib.loads(f"{written} = 1"), {key: 1})


class SheetTableCase(unittest.TestCase):
    """``[competitions.<id>.expected]``, loaded from a real file as a club types it."""

    HEAD = ('[competitions.monday-division-b]\n'
            'teams = ["Rockets", "Iguanas", "Aces"]\n'
            '[competitions.monday-division-b.expected]\n')
    WHERE = "[competitions.monday-division-b.expected]"

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def loaded(self, table):
        """The table as the loader returns it, and every warning the load gave."""
        path = self.dir / "config.toml"
        path.write_text(self.HEAD + table, encoding="utf-8")
        config = config_mod.load(path)
        return config.competition("monday-division-b").expected, list(config.warnings)

    @staticmethod
    def sheet(after_round, *lines):
        return config_mod.ExpectedSheet(after_round=after_round, lines=tuple(
            config_mod.ExpectedLine(key=key, points=points, played=played)
            for key, points, played in lines))


class TestTheSheetTableIsReadAsTheClubTypedIt(SheetTableCase):
    """Every figure is kept as the text the club typed, because that text is the claim.

    The sheet prints its points to some number of places, and the comparison is made
    to exactly that many: ``10.9`` is a claim to one place, ``"10.90"`` to two, and
    ``19`` to none. So a float is kept as ``repr`` gives it back -- ``12.0`` stays
    ``"12.0"``, one place -- and a quoted figure is kept as quoted. Rejected: storing
    a float, which throws the places away before anything can compare at them, and
    ``:g``, which turns ``12.0`` into ``12`` and a one-place claim into a whole number.
    """

    def test_after_round_is_a_round_number_however_it_was_written(self):
        for written in ('3', '"3"', '"03"', '" 3 "'):
            with self.subTest(after_round=written):
                expected, warnings = self.loaded(
                    f'after_round = {written}\nRockets = {{ points = 10.9 }}\n')
                self.assertEqual(expected.after_round, "3")
                self.assertEqual(warnings, [])

    def test_points_keep_the_places_they_were_typed_to(self):
        for written, stored in (('10.9', "10.9"), ('12.0', "12.0"), ('19', "19"),
                                ('"10.90"', "10.90"), ('" 10.9 "', "10.9"),
                                ('0', "0")):
            with self.subTest(points=written):
                expected, warnings = self.loaded(
                    f'after_round = 3\nRockets = {{ points = {written} }}\n')
                self.assertEqual(expected, self.sheet("3", ("Rockets", stored, None)))
                self.assertEqual(warnings, [])

    def test_matches_played_is_a_whole_number_or_its_digits(self):
        for written in ('3', '"3"'):
            with self.subTest(played=written):
                expected, warnings = self.loaded(
                    f'after_round = 3\nRockets = {{ played = {written} }}\n')
                self.assertEqual(expected, self.sheet("3", ("Rockets", "", 3)))
                self.assertEqual(warnings, [])

    def test_a_sub_table_line_is_read_like_an_inline_one(self):
        inline, _ = self.loaded('after_round = 3\n'
                                '"Hilltop Aces" = { points = 10.9, played = 3 }\n')
        path = self.dir / "config.toml"
        path.write_text(self.HEAD + 'after_round = 3\n'
                        '[competitions.monday-division-b.expected."Hilltop Aces"]\n'
                        'points = 10.9\nplayed = 3\n', encoding="utf-8")
        config = config_mod.load(path)
        self.assertEqual(config.competition("monday-division-b").expected, inline)
        self.assertEqual(list(config.warnings), [])
        self.assertEqual(inline, self.sheet("3", ("Hilltop Aces", "10.9", 3)))

    def test_the_lines_keep_the_order_and_the_keys_they_were_typed_in(self):
        # Keys are not resolved to teams here: a derived team list only exists once
        # the cards have been read, so the name is checked where the teams are.
        expected, warnings = self.loaded(
            'after_round = 5\n'
            'Rockets = { points = 31.4, played = 5 }\n'
            '"aces" = { points = "24.60" }\n'
            '"The Iguanas" = { played = "5" }\n')
        self.assertEqual(expected, self.sheet("5", ("Rockets", "31.4", 5),
                                              ("aces", "24.60", None),
                                              ("The Iguanas", "", 5)))
        self.assertEqual(warnings, [])


class TestASheetTableWithNoUsableRoundIsNotComparedAndSaysSo(SheetTableCase):
    """Without ``after_round`` there is nothing the typed figures can be compared to.

    A sheet is a statement about the rounds up to one round. Compared against every
    card read, it goes out of date the week the next card arrives, and every team
    disagrees at once. So the table is dropped and the warning says what to add.
    Rejected: defaulting to the latest round read, which is right for a week and then
    wrong on every team; and a ConfigError, which would stop an unattended build over
    a check that credits nothing. A finals label is refused rather than guessed at:
    finals are not on the ladder (F11), so the ladder after a final is the ladder
    after the last regular round, and that round's number is what the warning asks
    for.
    """

    def test_figures_with_no_round_are_not_compared(self):
        for table in ('Rockets = { points = 10.9 }\n',
                      'after_round = ""\nRockets = { points = 10.9 }\n',
                      'Rockets = { points = "abc" }\n'):
            with self.subTest(table=table):
                expected, warnings = self.loaded(table)
                self.assertIsNone(expected)
                self.assertEqual(warnings[0], (
                    f"{self.WHERE} has no after_round, so it was not compared. Add "
                    f"the last round your association's sheet covers — after_round "
                    f"= 5 — so the check stays right when later cards arrive."))

    def test_a_round_that_is_not_a_whole_number_is_named_as_written(self):
        # Quoted digits past Python's 4300-digit limit are no number int() will
        # give: measured before this, a traceback out of the loader. Echoed whole,
        # they were a warning of 5017 columns; now the start and the length.
        huge = "1" * 5000
        for written, shown in (('true', 'True'), ('5.5', '5.5'), ('[5]', '[5]'),
                               ('0', '0'), ('-1', '-1'), ('"SF"', "'SF'"),
                               ('"R5"', "'R5'"), ('"0"', "'0'"),
                               (f'"{huge}"', "'111111111111...' (5000 characters)")):
            with self.subTest(after_round=written):
                expected, warnings = self.loaded(
                    f'after_round = {written}\nRockets = {{ points = 10.9 }}\n')
                self.assertIsNone(expected)
                self.assertEqual(warnings, [
                    f"{self.WHERE} after_round = {shown} is not a round number, so "
                    f"the table was not compared. Write the last regular round the "
                    f"sheet covers as a whole number, after_round = 5: finals are "
                    f"not on a ladder."])

    def test_the_round_is_named_first_and_a_bad_line_is_still_named(self):
        # Both mistakes are the club's to fix, so both are said; the round first,
        # because it is the one that stopped the whole table.
        expected, warnings = self.loaded(
            'after_round = "SF"\nRockets = { points = "abc" }\n')
        self.assertIsNone(expected)
        self.assertEqual(len(warnings), 2)
        self.assertIn("after_round = 'SF' is not a round number", warnings[0])
        self.assertIn("'Rockets' points = 'abc' is not a number", warnings[1])

    def test_a_table_that_is_not_a_table_is_named(self):
        path = self.dir / "config.toml"
        path.write_text('[competitions.monday-division-b]\nexpected = 5\n',
                        encoding="utf-8")
        config = config_mod.load(path)
        self.assertIsNone(config.competition("monday-division-b").expected)
        self.assertEqual(list(config.warnings), [
            f'{self.WHERE} should be a section of "Team name" = '
            f'{{ points = …, played = … }} lines, so it was not compared.'])


class TestAFigureTheSheetTableCannotUseIsNamedAndTheRestIsKept(SheetTableCase):
    """A bad figure costs that figure, and nothing else in the table.

    The table credits nothing, so a typo in it cannot be allowed to cost the club the
    rest of its check. Refused: a bool (``true`` is an ``int`` in Python), NaN and
    infinity (TOML floats, and never a sheet's figure), a negative, a comma decimal,
    and a float for matches played. Rejected: a ConfigError, as the byes table uses --
    a bye pays points and this table pays none.
    """

    def test_points_that_are_not_a_number_are_dropped_and_played_is_kept(self):
        for written, shown in (('true', 'True'), ('nan', 'nan'), ('inf', 'inf'),
                               ('-1.0', '-1.0'), ('-3', '-3'), ('"10,9"', "'10,9'"),
                               ('"abc"', "'abc'"), ('"-1"', "'-1'"),
                               ('1e20', '1e+20'), ('[1]', '[1]')):
            with self.subTest(points=written):
                expected, warnings = self.loaded(
                    f'after_round = 3\nRockets = {{ points = {written}, played = 3 }}\n'
                    f'Aces = {{ points = 10.9 }}\n')
                self.assertEqual(expected, self.sheet("3", ("Rockets", "", 3),
                                                      ("Aces", "10.9", None)))
                self.assertEqual(warnings, [
                    f"{self.WHERE} 'Rockets' points = {shown} is not a number, so "
                    f"its points were not compared. Type it as the sheet prints it: "
                    f"points = 10.9."])

    def test_played_that_is_not_a_whole_number_is_dropped_and_points_are_kept(self):
        huge = "1" * 5000     # past int()'s digit limit, as the round above
        for written, shown in (('3.0', '3.0'), ('-1', '-1'), ('true', 'True'),
                               ('"3.5"', "'3.5'"), ('"three"', "'three'"),
                               (f'"{huge}"', "'111111111111...' (5000 characters)")):
            with self.subTest(played=written):
                expected, warnings = self.loaded(
                    f'after_round = 3\nRockets = {{ points = 10.9, played = {written} }}\n')
                self.assertEqual(expected, self.sheet("3", ("Rockets", "10.9", None)))
                self.assertEqual(warnings, [
                    f"{self.WHERE} 'Rockets' played = {shown} is not a whole number "
                    f"of matches, so its matches played were not compared."])

    def test_a_team_named_at_length_is_named_by_its_start_and_its_length(self):
        # The key is typed by the club as much as the figures are, and was echoed
        # whole: a team named with 5000 k's put each warning about its line past
        # 5000 columns, 5156, 5132 and 10191 (/tmp/t7/scratch/WID/p168-review.txt).
        # What to write stays whole, being the line to paste. Rejected: capping that
        # as well, a line to paste that does not name the team.
        k, m = "k" * 5000, "m" * 5000
        expected, warnings = self.loaded(
            f'after_round = 3\n"{k}" = {{ points = "abc", played = "x" }}\n'
            f'"{m}" = 10.9\n')
        self.assertEqual(expected, self.sheet("3"))
        self.assertEqual(warnings, [
            f"{self.WHERE} 'kkkkkkkkkkkk...' (5000 characters) points = 'abc' is "
            f"not a number, so its points were not compared. Type it as the sheet "
            f"prints it: points = 10.9.",
            f"{self.WHERE} 'kkkkkkkkkkkk...' (5000 characters) played = 'x' is not "
            f"a whole number of matches, so its matches played were not compared.",
            f"{self.WHERE} 'mmmmmmmmmmmm...' (5000 characters) = 10.9 does not say "
            f"which figure it is, so that team was not compared. Write it as "
            f"{m} = {{ points = 10.9, played = 3 }}, with the sheet's own "
            f"figures."])

    def test_a_line_that_is_only_a_number_says_how_to_write_it(self):
        expected, warnings = self.loaded(
            'after_round = 3\n"Hilltop Aces" = 10.9\nRockets = { points = 19.0 }\n')
        self.assertEqual(expected, self.sheet("3", ("Rockets", "19.0", None)))
        self.assertEqual(warnings, [
            f"{self.WHERE} 'Hilltop Aces' = 10.9 does not say which figure it is, so "
            f"that team was not compared. Write it as \"Hilltop Aces\" = "
            f"{{ points = 10.9, played = 3 }}, with the sheet's own figures."])

    def test_a_key_the_line_does_not_have_is_named_with_the_nearest_one(self):
        expected, warnings = self.loaded(
            'after_round = 3\n"Hilltop Aces" = { point = 10.9, played = 3 }\n'
            'Rockets = { points = 19.0, average = 6.33 }\n')
        self.assertEqual(expected, self.sheet("3", ("Hilltop Aces", "", 3),
                                              ("Rockets", "19.0", None)))
        self.assertEqual(warnings, [
            '[competitions.monday-division-b.expected."Hilltop Aces"] point is not '
            'a setting this program understands and was ignored. Did you mean '
            'points?',
            '[competitions.monday-division-b.expected.Rockets] average is not a '
            'setting this program understands and was ignored.'])

    def test_a_line_left_with_no_figure_it_can_use_is_left_out(self):
        # The table stays in use: the round is right and another line may be too,
        # and the report says which teams were not compared.
        expected, warnings = self.loaded(
            'after_round = 3\nRockets = { points = "abc" }\n')
        self.assertEqual(expected, self.sheet("3"))
        self.assertEqual(len(warnings), 1)


class TestAnEmptySheetTableIsNoTableAtAll(SheetTableCase):
    """A table with nothing typed in it is a table nobody has filled in yet.

    That is what the template a later ``--discover`` writes looks like once its lines
    are uncommented, and a club that has not got round to the figures is owed no
    warning about a check it has not asked for. Rejected: warning on it, which would
    be a warning on every night's run for a table that says nothing.
    """

    def test_nothing_typed_is_no_table_and_no_warning(self):
        for table in ('', 'after_round = ""\n', 'after_round = 3\n',
                      'after_round = ""\nRockets = { points = "", played = "" }\n'
                      'Aces = { points = "" }\n',
                      'Rockets = { points = " " }\n', 'Rockets = {}\n'):
            with self.subTest(table=table):
                expected, warnings = self.loaded(table)
                self.assertIsNone(expected)
                self.assertEqual(warnings, [])

    def test_a_competition_with_no_table_has_none(self):
        path = self.dir / "config.toml"
        path.write_text('[competitions.monday-division-b]\nday = "Monday"\n',
                        encoding="utf-8")
        self.assertIsNone(config_mod.load(path).competition("monday-division-b")
                          .expected)


class SheetWriteCase(unittest.TestCase):
    """A file with a sheet table, loaded, written by ``config.render`` and loaded again."""

    COMP = "tuesday-division-c"          # make_competition's id

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.comp = make_competition(["riverside-rockets", "iguanas", "aces"])

    def loaded(self, text: str):
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return config_mod.load(path)

    @staticmethod
    def table(text: str, comp_id: str) -> list[str]:
        """The lines of ``[competitions.<id>.expected]``, heading first, to the blank."""
        lines = text.splitlines()
        start = lines.index(f"[competitions.{comp_id}.expected]")
        end = next((i for i in range(start, len(lines)) if not lines[i].strip()),
                   len(lines))
        return lines[start:end]


class TestTheSheetTableIsWrittenBackAsTyped(SheetWriteCase):
    """``--discover --force`` writes a typed sheet back live, each figure as it was read.

    It wrote no table at all. Measured on the demo with a table after round 5, the
    build compared one team, ``--force`` exited 0, and the run after it printed no
    ``sheet`` line (/tmp/t6/scratch/m2/p465). Points are the text the club typed, and
    the places it typed them to are the claim, so a figure is written unquoted only
    where TOML reads the same text back -- ``12.0`` as ``12.0`` -- and quoted where it
    would not, as ``"10.90"`` and ``"07"``. Rejected: ``toml_value``, whose ``:g``
    writes ``12.0`` as ``12``, a claim to one place made a whole number; and quoting
    every figure, which loads back the same but hands a club that typed ``19`` a
    ``"19"`` it never wrote.
    """

    TYPED = ('after_round = "03"\n'
             '"Riverside Rockets" = { points = 12.0, played = "3" }\n'
             'Iguanas = { points = "10.90" }\n'
             'aces = { played = 2, points = 19 }\n'
             '"Mudlarks" = { points = "07" }\n'
             'Wanderers = { points = 0, played = 0 }\n')
    WRITTEN = ["after_round = 3",
               '"Riverside Rockets" = { points = 12.0, played = 3 }',
               'Iguanas = { points = "10.90" }',
               "aces = { points = 19, played = 2 }",
               'Mudlarks = { points = "07" }',
               "Wanderers = { points = 0, played = 0 }"]

    def typed(self, comp_id: str):
        return self.loaded(f"[competitions.{comp_id}]\nday = \"Thursday\"\n"
                           f"[competitions.{comp_id}.expected]\n{self.TYPED}")

    def test_each_figure_is_written_as_toml_gives_back_the_text_it_was_read_as(self):
        # Keys as typed, in the order typed: a sorted table would put Iguanas first.
        text = config_mod.render(self.typed(self.COMP), competitions=[self.comp])
        self.assertEqual(self.table(text, self.COMP),
                         [f"[competitions.{self.COMP}.expected]", *self.WRITTEN])

    def test_it_loads_back_as_the_sheet_it_was_read_as(self):
        # A section the cards built, and one no card was filed under this run.
        for comp_id, built in ((self.COMP, [self.comp]), ("thursday-a", [])):
            with self.subTest(section=comp_id):
                config = self.typed(comp_id)
                self.assertEqual(config.warnings, ())
                self.assertIsNotNone(config.competition(comp_id).expected)
                again = self.loaded(config_mod.render(config, competitions=built))
                self.assertEqual(again.warnings, ())
                self.assertEqual(again.competition(comp_id).expected,
                                 config.competition(comp_id).expected)

    def test_a_section_no_card_was_filed_under_writes_it_as_typed_too(self):
        text = config_mod.render(self.typed("thursday-a"), competitions=())
        self.assertEqual(self.table(text, "thursday-a"),
                         ["[competitions.thursday-a.expected]", *self.WRITTEN])

    def test_a_table_it_could_compare_no_line_of_is_marked_as_one_it_drops(self):
        # Read as no table, so written back as the template; the refusal names it
        # off this. A table nobody has filled in is no table and is not marked.
        head = f"[competitions.{self.COMP}]\nday = \"Thursday\"\n"
        under = f"{head}[competitions.{self.COMP}.expected]\n"
        for name, text, marked in (
                ("no after_round", under + "Iguanas = { points = 1.0 }\n", True),
                ("every line refused",
                 under + 'after_round = 3\nIguanas = { points = "one" }\n', True),
                ("not a table", head + "expected = 5\n", True),
                ("after_round alone",
                 under + 'after_round = 3\nIguanas = { points = "" }\n', False),
                ("a line it compares", under + self.TYPED, False),
                ("no table", head, False)):
            with self.subTest(table=name):
                self.assertIs(self.loaded(text).competition(self.COMP)
                              .expected_unusable, marked)

    def test_a_figure_too_long_for_int_is_written_quoted_and_loads_back(self):
        # int() refuses a figure of more than 4300 digits, so whether it reads back
        # bare cannot be asked of it; quoted, it loads back as the text it was.
        big = "1" * 4301
        config = self.loaded(f"[competitions.{self.COMP}]\nday = \"Thursday\"\n"
                             f"[competitions.{self.COMP}.expected]\nafter_round = 3\n"
                             f'Big = {{ points = "{big}" }}\n')
        text = config_mod.render(config, competitions=[self.comp])
        self.assertIn(f'Big = {{ points = "{big}" }}', self.table(text, self.COMP))
        self.assertEqual(self.loaded(text).competition(self.COMP).expected,
                         config.competition(self.COMP).expected)

    def test_writing_what_it_wrote_writes_the_same_bytes(self):
        for comp_id, built in ((self.COMP, [self.comp]), ("thursday-a", [])):
            with self.subTest(section=comp_id):
                first = config_mod.render(self.typed(comp_id), competitions=built)
                self.assertEqual(config_mod.render(self.loaded(first),
                                                   competitions=built), first)


class TestASheetLineTheLoaderRefusedIsMarkedAsOneForceDrops(SheetWriteCase):
    """A line with a figure refused, in a table it compares, is marked for the refusal.

    The table is written back as it was read, and a refused figure was read as
    nothing, so ``--discover --force`` wrote the rest of the table and lost it.
    Measured on the demo with ``after_round = 5``, a Quolls line and ``"Brindle
    Magpies" = { points = "lots" }`` (/tmp/t7/scratch/CFORCE/p161): the refusal said
    everything declared is written back, ``--force`` kept the Quolls line alone, and
    the run after it compared 1 of 6 teams and said nothing of the Magpies. Each
    such line is now marked, once and in the order typed, for the refusal to name.
    A table it can compare no line of is marked whole (4.65), and not line by line
    as well: one mistake is said once. Rejected: a second reader of the table.
    """

    HEAD = (f"[competitions.{SheetWriteCase.COMP}]\nday = \"Thursday\"\n"
            f"[competitions.{SheetWriteCase.COMP}.expected]\n")

    def refused(self, lines: str, *, after_round: str = "after_round = 3\n"):
        loaded = self.loaded(self.HEAD + after_round + lines)
        return loaded.competition(self.COMP).expected_refused

    def test_each_figure_the_loader_refuses_marks_its_line(self):
        for name, line in (("points", 'Iguanas = { points = "lots" }\n'),
                           ("played", 'Iguanas = { played = "four" }\n'),
                           ("a negative played", "Iguanas = { played = -1 }\n"),
                           ("not a table", "Iguanas = 10.7\n"),
                           ("one figure of two",
                            'Iguanas = { points = "lots", played = 3 }\n'),
                           ("both figures",
                            'Iguanas = { points = "lots", played = "four" }\n')):
            with self.subTest(refused=name):
                self.assertEqual(self.refused("aces = { points = 19.0 }\n" + line),
                                 ("Iguanas",))

    def test_the_lines_are_marked_in_the_order_typed_as_the_keys_were_typed(self):
        self.assertEqual(self.refused('"Riverside Rockets" = { points = "x" }\n'
                                      "aces = { points = 19.0 }\n"
                                      "Iguanas = 1.0\n"),
                         ("Riverside Rockets", "Iguanas"))

    def test_the_figure_it_read_beside_a_refused_one_is_still_compared(self):
        loaded = self.loaded(self.HEAD + 'after_round = 3\n'
                             'Iguanas = { points = "lots", played = 3 }\n')
        self.assertEqual(loaded.competition(self.COMP).expected.lines,
                         (config_mod.ExpectedLine(key="Iguanas", played=3),))

    def test_a_line_it_reads_whole_or_that_is_blank_is_not_marked(self):
        # A key the table does not know is the file-wide rule's, said as that.
        for name, line in (("both figures", "Iguanas = { points = 1.0, played = 3 }\n"),
                           ("a blank figure", 'Iguanas = { points = "" }\n'),
                           ("an unknown key", "Iguanas = { pts = 12.0 }\n")):
            with self.subTest(line=name):
                self.assertEqual(self.refused("aces = { points = 19.0 }\n" + line), ())

    def test_a_table_it_compares_no_line_of_is_marked_whole_and_not_by_line(self):
        for name, after_round in (("no after_round", ""),
                                  ("after_round refused", 'after_round = "SF"\n')):
            with self.subTest(table=name):
                text = self.HEAD + after_round + ("aces = { points = 19.0 }\n"
                                                  'Iguanas = { points = "lots" }\n')
                section = self.loaded(text).competition(self.COMP)
                self.assertTrue(section.expected_unusable)
                self.assertEqual(section.expected_refused, ())
        section = self.loaded(self.HEAD + 'after_round = 3\n'
                              'Iguanas = { points = "lots" }\n').competition(self.COMP)
        self.assertTrue(section.expected_unusable)
        self.assertEqual(section.expected_refused, ())

    def test_the_mark_leaves_the_warnings_as_they_were(self):
        # The same reader marks them, so the warnings keep their words and order.
        # With a line it reads whole, so that the table is compared and marked.
        warnings = self.loaded(self.HEAD + 'after_round = 3\nIguanas = 1.0\n'
                               'aces = { points = "x" }\n'
                               '"Riverside Rockets" = { points = 19.0 }\n').warnings
        self.assertEqual(len(warnings), 2)
        self.assertIn("'Iguanas' = 1.0 does not say which figure it is", warnings[0])
        self.assertIn("'aces' points = 'x' is not a number", warnings[1])


class TestTheSheetTemplateNamesEveryTeamAndCarriesNoFigure(SheetWriteCase):
    """With no table typed, the file gets one to fill in, and no figure in it.

    A live heading with every line under it commented: each team once, in the order
    the file lists the teams, with its points and matches played left blank. The
    figures are the club's to copy off the sheet. A template holding the dashboard's
    own would agree with it by definition, and a club that took the # away without
    retyping them would be told every week that all agree. Uncommented as written, it
    is a table nobody has filled in yet, which is no table and no warning. Rejected:
    leaving the table out until a club types one, as the first run did, so a club
    that never read that part of the README never met the check.
    """

    TEAMS = ["Riverside Rockets", "Iguanas", "Aces"]      # as typed, not sorted

    def written(self) -> str:
        config = self.loaded(f"[competitions.{self.COMP}]\nday = \"Tuesday\"\n"
                             f"teams = {config_mod.toml_value(self.TEAMS)}\n")
        return config_mod.render(config, competitions=[self.comp])

    def test_each_team_is_named_once_and_commented_with_no_figure(self):
        table = self.table(self.written(), self.COMP)
        self.assertEqual(table[0], f"[competitions.{self.COMP}.expected]")
        self.assertTrue(all(line.startswith("# ") for line in table[1:]), table)
        self.assertEqual([line for line in table if line.endswith(('""', "}"))],
                         ['# after_round = ""',
                          '# "Riverside Rockets" = { points = "", played = "" }',
                          '# Iguanas = { points = "", played = "" }',
                          '# Aces = { points = "", played = "" }'])

    def test_a_team_list_read_from_the_cards_is_named_as_the_file_lists_it(self):
        text = config_mod.render(config_mod.Config(path=Path("config.toml")),
                                 competitions=[self.comp])
        self.assertEqual(
            [line.split(" = ")[0][2:] for line in self.table(text, self.COMP)
             if line.endswith("}")],
            ["Aces", "Iguanas", '"Riverside Rockets"'])

    def test_a_section_no_card_was_filed_under_is_given_none(self):
        # Written back as the club had it, and it had no table: the template is for
        # a section the cards have just given a team list.
        config = self.loaded("[competitions.thursday-a]\nday = \"Thursday\"\n"
                             "teams = [\"Aces\"]\n")
        text = config_mod.render(config, competitions=())
        self.assertIn("\n[competitions.thursday-a]\n", text)
        self.assertNotIn("expected]", text)

    def test_uncommented_as_written_it_is_no_table_and_no_warning(self):
        text = self.written()
        loaded = self.loaded("\n".join(
            line[2:] if line.startswith("# ") and line.endswith(('""', "}"))
            and " = " in line else line for line in text.splitlines()) + "\n")
        self.assertEqual(loaded.warnings, ())
        self.assertIsNone(loaded.competition(self.COMP).expected)
        # And as written, heading live, it is no table either.
        self.assertIsNone(self.loaded(text).competition(self.COMP).expected)


class TestARoundTypedFinalsFalseIsRegularWhateverItsLabel(unittest.TestCase):
    """``finals`` under ``[[…rounds]]`` is true, false or not typed, and false counts.

    The loader read it as ``bool(entry.get("finals", False))``, so false and not
    typed were one value, and a round labelled as a final was a final however the
    club declared it -- while the run told the club to set ``finals = false`` if it
    was really a regular round. Measured on the demo with a declared draw of rounds
    1 to 3 and ``SF`` typed ``finals = false`` (/tmp/t6/scratch/m2/p470): the SF's
    three matches were out of the ladder, and the draw was three regular rounds long.
    Now a typed value decides, and a round with none is read by its label. A value
    that is not TOML's true or false is refused, as ``draws`` is: ``bool("false")``
    made ``finals = "false"`` a final. Rejected: warning and going by the label,
    which scores the round on the side of the line the club wrote the key to move it
    off.
    """

    DRAW = ('[competitions.monday-division-b]\nday = "Monday"\n'
            '[[competitions.monday-division-b.rounds]]\nnumber = "1"\n'
            '[[competitions.monday-division-b.rounds]]\nnumber = "2"\nfinals = true\n'
            '[[competitions.monday-division-b.rounds]]\nnumber = "3"\n'
            '[[competitions.monday-division-b.rounds]]\nnumber = "SF"\nfinals = false\n'
            '[[competitions.monday-division-b.rounds]]\nnumber = "GF"\n')

    def loaded(self, text: str):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path).competition("monday-division-b")

    def test_the_key_is_read_as_true_false_or_not_typed(self):
        rounds = self.loaded(self.DRAW).rounds
        self.assertEqual([(r.number, r.finals) for r in rounds],
                         [("1", None), ("2", True), ("3", None), ("SF", False),
                          ("GF", None)])

    def test_a_typed_value_decides_and_the_label_decides_the_rest(self):
        rounds = self.loaded(self.DRAW).rounds
        self.assertEqual([(r.number, r.is_finals) for r in rounds],
                         [("1", False), ("2", True), ("3", False), ("SF", False),
                          ("GF", True)])

    def test_a_round_typed_false_is_one_of_the_seasons_regular_rounds(self):
        # 1, 3 and SF: round 2 typed a final, and GF read as one by its label.
        self.assertEqual(self.loaded(self.DRAW).season_length, 3)

    def test_a_value_that_is_not_true_or_false_is_refused(self):
        for typed in ('"false"', '"true"', "0", "1"):
            with self.subTest(finals=typed):
                with self.assertRaises(config_mod.ConfigError) as caught:
                    self.loaded(self.DRAW.replace("finals = false",
                                                  f"finals = {typed}"))
                self.assertIn("round SF: finals must be true or false, got ",
                              str(caught.exception))


class TestTheLengthForceWritesIsCountedUnderTheLabelsTheRunAfterItReads(
        unittest.TestCase):
    """``length_by_labels`` is ``season_length`` with each round a final by its label.

    ``--discover --force`` drops the draw, and with it every ``finals`` typed there,
    so the run after it tells a final by its label alone (4.66). It wrote the length
    counted under the typed flags. Measured on the suite's season with SF cards
    (/tmp/t7/scratch/CFORCE/p162): round 3 typed a final made it ``rounds_total =
    2``, and the run after exited 1 on F3 with three regular rounds played; SF typed
    regular made it 4, and the run after read ``3 of 4`` with SF a final. So the
    length written is the same rule over the same rounds, each read by its label.
    A ``rounds_total`` the club typed is theirs, and comes back as typed.
    """

    ROUNDS = "".join(f'[[competitions.monday-division-b.rounds]]\nnumber = "{n}"\n'
                     for n in ("1", "2", "3", "SF"))

    def config(self, finals: dict[str, str], total: int | None = None):
        import tempfile
        text = self.ROUNDS
        for number, typed in finals.items():
            text = text.replace(f'number = "{number}"\n',
                                f'number = "{number}"\nfinals = {typed}\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[competitions.monday-division-b]\nday = "Monday"\n'
                            + (f"rounds_total = {total}\n" if total is not None
                               else "") + text, encoding="utf-8")
            return config_mod.load(path)

    def lengths(self, finals: dict[str, str], total: int | None = None):
        section = self.config(finals, total).competition("monday-division-b")
        return section.season_length, section.length_by_labels

    def test_a_round_typed_a_final_is_counted_as_its_label_reads(self):
        self.assertEqual(self.lengths({"3": "true"}), (2, 3))

    def test_a_final_typed_regular_is_counted_as_its_label_reads(self):
        self.assertEqual(self.lengths({"SF": "false"}), (4, 3))

    def test_flags_the_labels_agree_with_leave_the_length_alone(self):
        for finals in ({}, {"1": "false", "SF": "true"}, {"3": "true", "SF": "false"}):
            with self.subTest(finals=finals):
                self.assertEqual(self.lengths(finals), (3, 3))

    def test_a_draw_whose_every_regular_round_was_typed_a_final_has_one_now(self):
        self.assertEqual(self.lengths({"1": "true", "2": "true", "3": "true"}),
                         (None, 3))

    def test_a_draw_of_finals_labels_typed_regular_has_none_now(self):
        config = self.config({"SF": "false"})
        section = config.competition("monday-division-b")
        section = dataclasses.replace(section, rounds=section.rounds[3:])
        self.assertEqual((section.season_length, section.length_by_labels), (1, None))

    def test_a_typed_rounds_total_is_the_length_either_way(self):
        for finals in ({"3": "true"}, {"SF": "false"}):
            with self.subTest(finals=finals):
                self.assertEqual(self.lengths(finals, total=9), (9, 9))

    def test_a_section_no_card_was_filed_under_is_written_with_it(self):
        for finals in ({"3": "true"}, {"SF": "false"}):
            with self.subTest(finals=finals):
                text = config_mod.render(self.config(finals), competitions=())
                self.assertIn("\nrounds_total = 3\n", text)
                self.assertEqual(text.count("rounds_total"), 1)


class TestTheLadderAfterARoundIsOneSlice(unittest.TestCase):
    """"The ladder after round N" is stated once, in ``stats.through_round``.

    The sheet check compares figures after one round and the ladder history ranks the
    teams after each one, and a second copy of the slice is the one that would sort
    ``"10"`` before ``"2"``. The history below is pinned from the code as it stood
    before the slice moved (measured, not worked out), so the move is shown to change
    nothing. Rejected: an inline comprehension in the sheet check, a second rule.
    """

    MATCHES = (
        make_match(((6, 1), (6, 2), (1, 6), (6, 0)), home="rockets", away="iguanas",
                   rnd="1"),
        make_match(((6, 4), (3, 6), (6, 3), (6, 2)), home="aces", away="falcons",
                   rnd="1"),
        make_match(((2, 6), (6, 4), (1, 6), (3, 6)), home="rockets", away="aces",
                   rnd="2"),
        make_match(((6, 3), (6, 4), (4, 6), (6, 1)), home="iguanas", away="falcons",
                   rnd="2"),
        make_match(((6, 0), (6, 0), (6, 1), (6, 2)), home="falcons", away="rockets",
                   rnd="10"),
        make_match(((6, 3), (3, 6), (6, 3), (3, 6)), home="iguanas", away="aces",
                   rnd="10"),
    )

    def test_the_history_is_what_it_was_before_the_slice_moved(self):
        from matchcentre import stats
        comp = make_competition(["rockets", "iguanas", "aces", "falcons"],
                                declared=True)
        history = stats.ladder_history(list(self.MATCHES), comp)
        self.assertEqual(
            {slug: [(e.round, e.position, e.points_average) for e in entries]
             for slug, entries in history.items()},
            {"aces": [("1", 1, 6.1), ("2", 1, 6.15), ("10", 1, 5.7)],
             "falcons": [("1", 3, 3.5), ("2", 4, 3.45), ("10", 3, 4.433333)],
             "iguanas": [("1", 4, 2.9), ("2", 3, 4.55), ("10", 2, 4.633333)],
             "rockets": [("1", 2, 5.9), ("2", 2, 4.55), ("10", 4, 3.8)]})

    def test_a_slice_keeps_its_own_round_and_every_one_before_it(self):
        from matchcentre import stats
        self.assertEqual([m.round for m in stats.through_round(self.MATCHES, "2")],
                         ["1", "1", "2", "2"])
        self.assertEqual([m.round for m in stats.through_round(self.MATCHES, "10")],
                         ["1", "1", "2", "2", "10", "10"])
        self.assertEqual(stats.through_round(self.MATCHES, "0"), [])


class TestARoundPrintedPaddedIsAStepOfItsOwnInTheLadder(unittest.TestCase):
    """A card printing round ``03`` is its own round, so its own step in the line.

    ``03`` and ``3`` are two rounds (4.32), and the ladder history takes one step
    for each round it has a card for. The key put them in one place, so the slice
    after ``03`` was the slice after ``3``: the two steps were the same ladder,
    every team at the same position on the same average in both. Measured on a
    ten-round demo season with one fixture of round 3 printed ``03``
    (``/tmp/t4/scratch/f3/rw/before-tnum.log`` and its probe): all six teams'
    ``3`` and ``03`` entries were equal, and seeds 3, 4, 6 and 7 wrote a season
    document unlike seed 0's. Now ``03`` is a step before ``3``, as every other
    printing of a week is, and the ladder after round 3 has the ``03`` card in it
    too, which a sheet after round 3 (``after_round`` is a whole number) needs.
    By text alone ``３`` and ``٣`` came after ``3``, and that ladder left them out
    (``/tmp/t4/scratch/f3/fx/probes/before-plain.log``).

    Rejected: slicing by the week alone, the key without its label, which is a
    second rule of round order beside the one everything else sorts by, and
    leaves the two steps equal.
    """

    MATCHES = (
        make_match(((6, 1), (6, 2), (1, 6), (6, 0)), home="rockets", away="aces",
                   rnd="2"),
        make_match(((2, 6), (6, 4), (1, 6), (3, 6)), home="rockets", away="iguanas",
                   rnd="3"),
        make_match(((6, 4), (3, 6), (6, 3), (6, 2)), home="aces", away="falcons",
                   rnd="03"),
        make_match(((6, 3), (6, 4), (4, 6), (6, 1)), home="iguanas", away="falcons",
                   rnd="4"),
    )

    def test_a_slice_after_the_padded_round_stops_before_the_plain_one(self):
        from matchcentre import stats
        self.assertEqual([m.round for m in stats.through_round(self.MATCHES, "03")],
                         ["2", "03"])
        self.assertEqual([m.round for m in stats.through_round(self.MATCHES, "3")],
                         ["2", "3", "03"])

    def test_the_ladder_after_a_week_has_every_printing_of_it(self):
        from matchcentre import stats
        matches = (*self.MATCHES,
                   make_match(((6, 2), (6, 2), (2, 6), (6, 2)), home="rockets",
                              away="falcons", rnd="\uff13"),
                   make_match(((6, 2), (6, 2), (2, 6), (6, 2)), home="aces",
                              away="iguanas", rnd="\u0663"))
        self.assertEqual(sorted(m.round for m in stats.through_round(matches, "3")),
                         sorted(["2", "3", "03", "\uff13", "\u0663"]))
        self.assertEqual([m.round for m in stats.through_round(matches, "\u0663")],
                         ["2", "03", "\u0663"])

    def test_the_padded_round_is_its_own_step(self):
        from matchcentre import stats
        comp = make_competition(["rockets", "iguanas", "aces", "falcons"],
                                declared=True)
        history = stats.ladder_history(list(self.MATCHES), comp)
        rockets = {e.round: e for e in history["rockets"]}
        self.assertEqual(list(rockets), ["2", "03", "3", "4"])
        # Rockets did not play in 03, and did in 3, so the two steps differ.
        self.assertNotEqual(rockets["03"], dataclasses.replace(rockets["3"],
                                                               round="03"))


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
        # The letterhead's name is written commented, so it comes back undeclared:
        # see TestAFirstRunWritesWhatItWorkedOutCommentedOut.
        self.assertEqual(loaded.club_name, "")
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
        # Moved by 4.465: the line has nothing after its =, for the club to type
        # the season's length into, where it offered the rounds played.
        self.assertRegex(text, r"(?m)^# rounds_total =$")

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
        # Declared, because only a declared name is written live for the parser to
        # read; the letterhead's is written behind a #.
        named = dataclasses.replace(self.blank, club_name='The "Real" Club')
        text = config_mod.render(named, competitions=[self.comp],
                                 club_name="THE REAL CLUB")
        self.assertEqual(tomllib.loads(text)["club"]["name"], 'The "Real" Club')

    def test_multi_season_mode_writes_the_seasons_table(self):
        multi = config_mod.Config(path=Path("config.toml"), seasons_mode="multi")
        text = config_mod.render(multi, competitions=[self.comp])
        self.assertIn("[seasons]", text)
        tomllib.loads(text)

    def test_single_season_mode_does_not_mention_seasons(self):
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertNotIn("\n[seasons]", text)

    def test_two_seasons_of_one_grade_are_written_so_the_loader_can_tell_them_apart(self):
        """Each section's ``season_key``, written live, and the file loaded back whole.

        In multi-season mode a card is filed on its day, its division and its season
        key, and the renderer wrote the first two and not the third. Two seasons of
        one grade then came out as two sections claiming the same day and division,
        and the loader of the program that wrote them refused the file: measured,
        exit 2 on the first run after ``--discover``. Live rather than commented,
        because it is not a guess awaiting review -- it is the folder the club filed
        the cards in -- and commented it would leave the file refused exactly as
        before. Rejected: loosening the refusal for sections with no key, which loads
        the file and leaves each section claiming no card with a folder.
        """
        multi = config_mod.Config(path=Path("config.toml"), seasons_mode="multi")
        text = config_mod.render(multi, competitions=[
            self.sharing(f"{year}-tuesday-division-c", "C",
                         "riverside-rockets", "hilltop-aces", season_key=year)
            for year in ("2025", "2026")])
        loaded = self.loaded(text)
        self.assertEqual({cid: comp.season_key
                          for cid, comp in loaded.competitions.items()},
                         {"2025-tuesday-division-c": "2025",
                          "2026-tuesday-division-c": "2026"})
        # The key written is the folder's name as the run slugs it, "2026-winter" for
        # data/2026-Winter/, and a comment calling it the folder's name was untrue of
        # every folder with a capital in it.
        self.assertIn("capitals and spaces do not matter", self.prose(text))

    def test_a_single_season_file_has_no_season_key_to_fill_in(self):
        # Single-season mode files every card with no key at all, so a section given
        # one there claims nothing. An empty line of it would be an invitation to do
        # exactly that, in the one mode where it disconnects the section.
        text = config_mod.render(self.blank, competitions=[self.comp])
        self.assertNotIn("season_key", text)

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
        """The block is there with its key taught in words, and no bye behind a #.

        Its example was the first team's name resting ``[1, 6]``, a bye nobody said
        that team had. Measured on the demo's first run (/tmp/t7/scratch/RU1/b140):
        with that # deleted the next run ended "exit 1: 5 validation error(s)", five
        F8 errors reading "declared teams: brindle-bunyips", because a byes table's
        keys close the team list. The words have to be enough to type a line: typed
        under the demo's table, ``Brindle Bunyips [6]`` and ``"Brindle Bunyips" [6]``
        are each "not valid TOML — Expected '=' after a key", and ``"Brindle
        Bunyips" = [6]`` loads (/tmp/t7/scratch/RU1/fix/probe.py). Rejected: a
        placeholder team name, which is a bye for a team no card names.
        """
        header = f"[competitions.{self.comp.id}.byes]"
        for config in (self.blank, self.declaring(byes={"Iguanas": ("6",)})):
            with self.subTest(stated=bool(config.competitions)):
                text = config_mod.render(config, competitions=[self.comp])
                self.assertIn(header, text)
                block = (text.split(f"\n{header}\n", 1)[1].split("\n\n", 1)[0]
                         .splitlines())
                behind = []
                for line in block:
                    if not line.startswith("#"):
                        continue
                    try:
                        behind.append(tomllib.loads(line.lstrip("#")))
                    except tomllib.TOMLDecodeError:
                        continue
                self.assertEqual(behind, [])
                prose = " ".join(line.lstrip("#").strip() for line in block
                                 if line.startswith("#"))
                # The three things a line is made of, and where one is worked.
                for said in ("double quotes", " = ", "square brackets",
                             "config.example.toml"):
                    self.assertIn(said, prose)
                self.assertLessEqual(max(map(len, block)), config_mod.WIDTH)
                tomllib.loads(text)

    def test_an_inferred_bye_is_written_commented_so_it_is_not_typed_twice(self):
        # It is already worked out from the cards. Shown so a club can see the
        # program agrees with its sheet, commented so declaring it is a choice --
        # and written as the club would type it, a number, so that deleting the #
        # declares the line a rewrite would write back.
        comp = self.comp.with_rounds({
            "1": Round(number="1", fixtures=(("iguanas", "aces"),),
                       byes=("riverside-rockets",))})
        text = config_mod.render(self.blank, competitions=[comp])
        self.assertRegex(text, r'(?m)^# "Riverside Rockets" = \[1\]')
        self.assertNotRegex(text, r'(?m)^"Riverside Rockets" = ')

    # --- [namesakes]: one printed name in two roster blocks -----------------

    def sharing(self, comp_id: str, division: str, *team_slugs: str,
                season_key: str = ""):
        """A competition whose every named team rosters one J Smith."""
        teams = {slug: Team(slug=slug, name=slugs.unslug(slug),
                            players=(Player(slug="j-smith", name="J Smith"),))
                 for slug in team_slugs}
        return dataclasses.replace(make_competition([]).with_teams(teams),
                                   id=comp_id, division=division,
                                   season_key=season_key)

    def shared(self):
        """Two teams whose roster blocks both carry one name, which is the finding."""
        return self.sharing("tuesday-division-c", "C",
                            "riverside-rockets", "hilltop-aces")

    def live(self, text: str, key: str) -> str:
        """The rendered file with one commented entry's hashes deleted.

        The whole entry, however many lines it wrapped to. A club deletes the hashes
        from the line *and its continuations*, so an entry that only round-trips
        while it fits on one line stops working at the fourth team -- which is
        precisely the case below.
        """
        out, taking = [], False
        for line in text.splitlines():
            if line.startswith(f"# {key} = ["):
                taking = True
            if taking:
                line = line[2:] if line.startswith("# ") else line
                if "]" in line:
                    taking = False
            out.append(line)
        return "\n".join(out) + "\n"

    def loaded(self, text: str):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_a_name_in_two_roster_blocks_is_written_out_commented(self):
        # The club's job is deciding whether it is two people; typing the line is
        # not part of that job, the same reason the teams array is written out
        # rather than described.
        text = config_mod.render(self.blank, competitions=[self.shared()])
        self.assertIn("[namesakes]", text)
        self.assertRegex(
            text, r'(?m)^# j-smith = \["Hilltop Aces", "Riverside Rockets"\]')
        self.assertNotRegex(text, r'(?m)^j-smith = ')
        tomllib.loads(text)

    def test_and_deleting_its_hashes_declares_exactly_those_two_teams(self):
        # The one thing the commented line has to be: correct when uncommented.
        # The teams are written as their banners print them and the key as a slug,
        # so this is also what proves the loader forgives both.
        text = config_mod.render(self.blank, competitions=[self.shared()])
        loaded = self.loaded(self.live(text, "j-smith"))
        self.assertEqual(loaded.namesakes,
                         {"j-smith": ("hilltop-aces", "riverside-rockets")})
        self.assertEqual(loaded.warnings, ())

    def test_one_name_shared_in_two_grades_gives_one_line_naming_all_four(self):
        # [namesakes] is keyed on the name, so one key has to carry every team that
        # printed it. This was written a competition at a time into a dict keyed on
        # the name alone, so the second grade overwrote the first: a club with the
        # same merge in two grades was offered a line that fixed one of them and
        # left the other merged, with nothing on the page to say a grade had been
        # dropped. Four teams also takes the line past the width it is wrapped to,
        # which is why it is uncommented and loaded rather than matched.
        text = config_mod.render(self.blank, competitions=[
            self.sharing("tuesday-division-c", "C",
                         "riverside-rockets", "hilltop-aces"),
            self.sharing("tuesday-division-d", "D",
                         "lakeside-spinners", "parkview-wanderers")])
        loaded = self.loaded(self.live(text, "j-smith"))
        self.assertEqual(
            loaded.namesakes,
            {"j-smith": ("hilltop-aces", "riverside-rockets",
                         "lakeside-spinners", "parkview-wanderers")})
        self.assertEqual(loaded.warnings, ())

    def test_a_team_in_two_seasons_of_one_grade_is_named_once(self):
        # Multi-season mode renders one file over every season, and the same two
        # teams merging the same name in both is the ordinary case there -- not four
        # registrations. Named twice, the loader would refuse the duplicate and the
        # club would be told their own file was wrong.
        multi = config_mod.Config(path=Path("config.toml"), seasons_mode="multi")
        text = config_mod.render(multi, competitions=[
            self.sharing("2026-a-tuesday-division-c", "C",
                         "riverside-rockets", "hilltop-aces",
                         season_key="2026-a"),
            self.sharing("2026-b-tuesday-division-c", "C",
                         "riverside-rockets", "hilltop-aces",
                         season_key="2026-b")])
        # Loaded whole, the file and not the one table: until each season's section
        # carried its season_key the loader refused these two before it reached
        # [namesakes], so this test read that table on its own and the file it was
        # testing could not be used.
        loaded = self.loaded(self.live(text, "j-smith"))
        self.assertEqual(loaded.namesakes,
                         {"j-smith": ("hilltop-aces", "riverside-rockets")})

    def test_a_namesake_already_declared_is_written_back_live(self):
        # Rendering runs again on every build, and a section that came back
        # commented would undo the club's decision the next time they looked.
        declared = config_mod.Config(
            path=Path("config.toml"),
            namesakes={"j-smith": ("riverside-rockets", "hilltop-aces")})
        text = config_mod.render(declared, competitions=[self.shared()])
        self.assertRegex(
            text, r'(?m)^j-smith = \["riverside-rockets", "hilltop-aces"\]')
        # And the same name is not offered back commented underneath it, which
        # would read as though the decision had not been made.
        self.assertNotRegex(text, r'(?m)^# j-smith = ')
        tomllib.loads(text)

    def test_a_season_with_no_shared_name_gets_the_syntax_and_nothing_else(self):
        # Nothing to confirm, so nothing is written out as though it were a
        # finding -- but the section is still there with an example in it, because
        # a club that hits this next season should not have to learn the syntax.
        # Every team here has a squad, and no two of them share a name: a section
        # rendered from teams with no players at all would agree with every reading
        # of what "shared" means, including "everybody".
        apart = make_competition([]).with_teams({
            "riverside-rockets": Team(
                slug="riverside-rockets", name="Riverside Rockets",
                players=(Player(slug="j-smith", name="J Smith"),)),
            "hilltop-aces": Team(
                slug="hilltop-aces", name="Hilltop Aces",
                players=(Player(slug="k-jones", name="K Jones"),)),
        })
        text = config_mod.render(self.blank, competitions=[apart])
        self.assertIn("[namesakes]", text)
        self.assertIn('# "j-smith" = ["Riverside Rockets", "Hilltop Aces"]', text)
        self.assertNotRegex(text, r'(?m)^"')

    # --- a rewrite keeps what the club declared ------------------------------

    def declared_everything(self) -> config_mod.Config:
        """A config with every setting outside a competition section filled in.

        Every one of them, deliberately, and none of them left at its default:
        the point of the test below is that it fails for a field nobody thought
        of, including one added after it was written.
        """
        return config_mod.Config(
            path=Path("config.toml"), exists=True,
            club_name="Quarry Quolls", strip_team_prefix="Quarry",
            seasons_mode="multi", title="Our dashboards",
            scoring="rubbers", sets="pro-8", unplayed="average", draws=False,
            format="doubles-3", slots=("doubles", "doubles", "doubles"),
            min_sets_for_result=2,
            season_labels={"2026-winter": "2026 Winter"},
            team_names={"riverside-rockets": "Riverside FC"},
            player_names={"j-smith": "Jo Smith"},
            aliases={"a-fernandez": "ana-fernandez"},
            namesakes={"b-lee": ("riverside-rockets", "hilltop-aces")})

    def rewritten(self, config: config_mod.Config) -> config_mod.Config:
        """What is left of a config after ``--discover --force`` rewrites it.

        Through a real file and a real load, because "written back" is a claim
        about what the *next* run reads and not about what the text looks like:
        a value written into a comment is in the file and gone from the program.
        """
        import tempfile
        text = config_mod.render(config, competitions=(), club_name="Quarry Quolls")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_every_setting_outside_a_competition_survives_a_rewrite(self):
        """`--discover --force` dropped three of the fourteen, silently.

        Measured on the unfixed tree: `format`, `slots` and `min_sets_for_result`
        went in declared and came back unset, because the renderer wrote those
        three lines commented whatever the config said -- the only three of the
        block that did not follow the `unplayed` and `draws` lines above them.
        The consequence is visible and unexplained: a club that declared
        `format = "doubles-3"` so that a card with a row missed is an error reads
        `format  read from each card` in the next run's own rules section.

        Field by field off `dataclasses.fields` rather than a list written out
        here, so a setting added later is covered by this test on the day it is
        added and not on the day somebody remembers it.
        """
        before = self.declared_everything()
        after = self.rewritten(before)
        # Like warnings, team_name_keys is what the loader made of the file, which
        # a config built by hand has none of. The team_names it was made from are
        # compared like everything else.
        skip = {"path", "exists", "warnings", "competitions", "team_name_keys"}
        lost = {f.name: (getattr(before, f.name), getattr(after, f.name))
                for f in dataclasses.fields(before)
                if f.name not in skip
                and getattr(before, f.name) != getattr(after, f.name)}
        self.assertEqual(lost, {}, "a rewrite dropped what the club declared")

    def test_a_declared_match_format_is_written_back_live(self):
        # The one of the three with a check hanging off it: declaring the shape is
        # what turns a card with a row missed from "a different format" into an
        # error, so losing it loosens a check the club asked for.
        declared = dataclasses.replace(self.blank, format="doubles-3")
        text = config_mod.render(declared, competitions=[self.comp])
        self.assertRegex(text, r'(?m)^format = "doubles-3"')

    def test_declared_slots_are_written_back_live(self):
        declared = dataclasses.replace(
            self.blank, slots=("doubles", "singles", "doubles"))
        text = config_mod.render(declared, competitions=[self.comp])
        self.assertRegex(text, r'(?m)^slots = \["doubles", "singles", "doubles"\]')

    def test_a_declared_minimum_for_a_result_is_written_back_live(self):
        declared = dataclasses.replace(self.blank, min_sets_for_result=2)
        text = config_mod.render(declared, competitions=[self.comp])
        self.assertRegex(text, r"(?m)^min_sets_for_result = 2")

    def test_and_the_three_of_them_stay_commented_when_nobody_declared_them(self):
        # The other half of the rule, and the reason the fix is conditional: the
        # shape is read from the cards, so writing a format live on a first run
        # would pin every later card to the first card's shape without being asked.
        text = config_mod.render(self.blank, competitions=[self.comp])
        for key in ("format", "slots", "min_sets_for_result"):
            self.assertRegex(text, rf"(?m)^# {key} = ")
            self.assertNotRegex(text, rf"(?m)^{key} = ")

    # --- a rewrite keeps what a competition section declared -----------------

    def declaring(self, **fields) -> config_mod.Config:
        """A config declaring this class's competition, with ``fields`` set on it."""
        return config_mod.Config(
            path=Path("config.toml"), exists=True,
            competitions={self.comp.id: config_mod.CompetitionConfig(
                id=self.comp.id, day="Tuesday", division="C", **fields)})

    # A night whose cards have not started arriving, declared down to its draw.
    WAITING = config_mod.CompetitionConfig(
        id="thursday-a", label="Thursday A", day="Thursday", division="A",
        season="2026 Winter", rounds_total=7,
        teams=("Quarry Quolls", "Meadow Magpies"),
        captains={"Quarry Quolls": "Rae Tolland"}, courts={"Quarry Quolls": 2},
        codes={"Meadow Magpies": 4}, byes={"Quarry Quolls": ("3",)},
        rounds=(config_mod.RoundConfig(
            number="1", date="2026-08-20",
            fixtures=(("Quarry Quolls", "Meadow Magpies"),)),),
        scoring="simple")

    def test_a_bye_is_written_back_as_the_club_typed_it(self):
        """A round typed as a number came back quoted.

        Measured on /tmp/t4/scratch/i417/m418b: ``"Parkview Wanderers" = [4]`` was
        rewritten ``["4"]``. The loader keeps every round as text, as it has to, since
        a round can be "SF"; the renderer wrote that text back. A round that is a
        whole number is written as one and anything else as the string it was, so
        "SF" stays a string and so does "04", which no TOML integer can spell.
        Rejected: quoting every round, which is correct TOML and not what the byes
        example in config.example.toml, ``[1, 6]``, tells a club to type.
        """
        declared = self.declaring(byes={"Riverside Rockets": ("4", "SF"),
                                        "Iguanas": ("04",)})
        raw = tomllib.loads(config_mod.render(declared, competitions=[self.comp]))
        self.assertEqual(raw["competitions"][self.comp.id]["byes"],
                         {"Riverside Rockets": [4, "SF"], "Iguanas": ["04"]})

    def test_what_a_club_declared_for_a_team_no_card_names_yet_comes_back(self):
        """With the team list open, a section's teams are the ones its cards name.

        So a captain, a court or a draw code the club had already typed in for a team
        whose first card has not arrived belonged to no team this renderer iterated,
        and a rewrite dropped all three. They are declarations, and they are written
        back from the declaration, keyed as the club keyed them.
        """
        declared = self.declaring(captains={"Hilltop Aces": "Devi O'Raman"},
                                  courts={"Hilltop Aces": 3},
                                  codes={"Hilltop Aces": 7})
        loaded = self.loaded(config_mod.render(declared, competitions=[self.comp]))
        section = loaded.competition(self.comp.id)
        self.assertEqual((section.captains, section.courts, section.codes),
                         ({"Hilltop Aces": "Devi O'Raman"}, {"Hilltop Aces": 3},
                          {"Hilltop Aces": 7}))

    def test_a_declared_team_list_comes_back_as_the_club_spelt_and_ordered_it(self):
        """The list was written from the names the page shows, sorted.

        Those are ``[team_names]`` where the club set one, so a club that declared
        "Riverside Rockets" and showed it as "The Rockets" got ``"The Rockets"`` in
        its team list, which slugs to a team no card names: measured on
        /tmp/t4/scratch/i417/fix2, the next plain run exited 1 with an F8 for
        riverside-rockets and put the-rockets on the ladder on 0 points, where it
        had been on 9. And the club's order went, which the file had no reason to
        change. Written from the declaration, as the courts and codes already are.
        Rejected: slugging the page's names back through ``[team_names]``, which
        recovers the slug and not the spelling or the order the club typed.
        """
        comp = self.comp.with_teams({
            "riverside-rockets": Team(slug="riverside-rockets", name="The Rockets"),
            "iguanas": Team(slug="iguanas", name="Iguanas")})
        declared = dataclasses.replace(
            self.declaring(teams=("Riverside Rockets", "Iguanas")),
            team_names={"riverside-rockets": "The Rockets"})
        raw = tomllib.loads(config_mod.render(declared, competitions=[comp]))
        self.assertEqual(raw["competitions"][self.comp.id]["teams"],
                         ["Riverside Rockets", "Iguanas"])

    def test_a_captain_the_club_keyed_its_own_way_is_written_once(self):
        """A club's key for a team need not be the card's spelling of it.

        ``"riverside rockets"`` finds Riverside Rockets, so the captain declared under
        it is the team's. Writing the cards' reading of that team beside the club's
        line named the team twice in one table, the reading first in sort order and
        so the one the next run reads -- and what it holds is the slug, since a
        declared captain on no card has no player to take a name from.

        Read as text as well as parsed, since the (C) marks are written behind a #
        (4.38) and the loader cannot see a commented line. Parsed alone, the table
        passed with ``# "Riverside Rockets" = "devi-o-raman"`` written over the
        club's line, a slug the note over it says to remove the # from and use
        (/tmp/t4/scratch/d438/probe84). Rejected: dropping the text check as about
        a comment, when that comment is the line the file asks the club to adopt.
        """
        comp = self.comp.with_teams({
            "riverside-rockets": Team(slug="riverside-rockets",
                                      name="Riverside Rockets",
                                      captain="devi-o-raman",
                                      captain_declared="Devi O'Raman"),
            "iguanas": Team(slug="iguanas", name="Iguanas")})
        declared = self.declaring(captains={"riverside rockets": "Devi O'Raman"})
        text = config_mod.render(declared, competitions=[comp])
        raw = tomllib.loads(text)
        self.assertEqual(raw["competitions"][self.comp.id]["captains"],
                         {"riverside rockets": "Devi O'Raman"})
        table = text.split(f"[competitions.{self.comp.id}.captains]\n", 1)[1]
        offered = [line for line in table.split("\n\n", 1)[0].splitlines()
                   if " = " in line]
        self.assertEqual(offered, ['"riverside rockets" = "Devi O\'Raman"'],
                         "the table names the team a second time")

    def test_the_captains_table_says_where_its_names_came_from(self):
        """One sentence over the table whatever it held, and it said (C) marks.

        So a club that corrected a captain was told the correction had been read off
        a card. Both halves here, because the table holds both: the captains the file
        named, and the (C) marks for the teams it named nobody for -- which are
        written either way, and which nothing else in the suite checks are written.

        "The file", and not "you": a file an earlier version wrote holds the (C)
        marks live, its next rewrite reads them back as named, and a header saying
        "As you wrote them" told a club that had named no captain at all that it had
        typed what the cards marked (a second ``--discover --force``, measured on
        /tmp/t4/scratch/i417/fix2). The marks themselves are now written commented,
        per the rule for what was worked out, so a first run's file no longer makes
        its next run a different build (/tmp/t4/scratch/d438/m/before2: the captains
        went from card to declared on the second run). Rejected, as 4.17 had it:
        writing the marks live and only the header true of them.
        """
        marked = self.comp.with_teams({
            "riverside-rockets": Team(slug="riverside-rockets",
                                      name="Riverside Rockets"),
            "iguanas": Team(slug="iguanas", name="Iguanas", captain="rae-tolland",
                            players=(Player(slug="rae-tolland", name="Rae Tolland",
                                            team="iguanas", is_captain=True,
                                            captain_source=Provenance.CARD),))})
        named = self.declaring(captains={"Riverside Rockets": "Ana Fernandez"})
        for config, said, unsaid in (
                (self.blank, "# Read from the (C) marks", "As this file had them"),
                (named, "# As this file had them", "# Read from the (C) marks")):
            text = config_mod.render(config, competitions=[marked])
            block = text.split(f"[competitions.{self.comp.id}.captains]", 1)[1]
            block = block.split("\n\n", 1)[0]
            with self.subTest(said=said):
                self.assertIn(said, block)
                self.assertNotIn(unsaid, block)
                self.assertRegex(block, r'(?m)^# Iguanas = "Rae Tolland"$')
        named_block = config_mod.render(named, competitions=[marked]).split(
            f"[competitions.{self.comp.id}.captains]", 1)[1].split("\n\n", 1)[0]
        self.assertRegex(named_block, r'(?m)^"Riverside Rockets" = "Ana Fernandez"$')

    def test_every_rule_a_competition_overrides_is_written_back(self):
        """A section's own ``scoring`` and ``min_sets_for_result`` were not written.

        Measured on m418b: a Tuesday night declared ``scoring = "simple"`` scored 9, 6,
        1 and 1, and after ``--discover --force`` the same cards scored 19.0, 15.8,
        11.1 and 10.7 under the preset, with exit 0. Every setting in RULES_SETTINGS,
        off the tuple rather than a list here, on a section built from cards and on
        one carried over with none, since both are written by the one function.
        """
        # 4.420 moved this: forfeit_award and forfeit_points are rules too, and
        # 4.537 finals.
        rules = dict(scoring="rubbers", sets="pro-8", unplayed="average",
                     draws=False, format="doubles-3",
                     slots=("doubles", "doubles", "doubles"),
                     min_sets_for_result=2, forfeit_award="fixed",
                     forfeit_points=5.0, finals="placings")
        self.assertEqual(set(rules), set(config_mod.RULES_SETTINGS),
                         "a rule this test does not set is a rule it does not check")
        on_cards = config_mod.CompetitionConfig(
            id=self.comp.id, day="Tuesday", division="C", **rules)
        waiting = dataclasses.replace(self.WAITING, **rules)
        config = config_mod.Config(
            path=Path("config.toml"), exists=True,
            competitions={self.comp.id: on_cards, waiting.id: waiting})
        loaded = self.loaded(config_mod.render(config, competitions=[self.comp]))
        for section in (on_cards, waiting):
            for name in config_mod.RULES_SETTINGS:
                with self.subTest(section=section.id, rule=name):
                    self.assertEqual(getattr(loaded.competition(section.id), name),
                                     getattr(section, name))

    def test_a_section_no_card_was_filed_under_is_written_back_but_its_draw(self):
        """Not written at all: the renderer walked the competitions the cards built.

        Measured on m418b, where ``[competitions.thursday-a]`` -- teams, a captain and
        a season length, and a first round next week -- was gone from the rewritten
        file. It is written back as declared, less its draw, which is the one thing
        ``--force`` drops from every section. Rejected: keeping the draw here and
        nowhere else, which makes what the flag drops depend on whether a card had
        arrived, and makes the refusal's one sentence about it two.
        """
        for mode, waiting in (("single", self.WAITING),
                              ("multi", dataclasses.replace(self.WAITING,
                                                            season_key="2026"))):
            config = config_mod.Config(path=Path("config.toml"), exists=True,
                                       seasons_mode=mode,
                                       competitions={waiting.id: waiting})
            text = config_mod.render(config, competitions=[self.comp])
            with self.subTest(mode=mode):
                self.assertEqual(self.loaded(text).competition(waiting.id),
                                 dataclasses.replace(waiting, rounds=()))
                # And the file says so where the draw was, rather than leaving a
                # club to find it missing.
                self.assertIn("less the draw you declared in it", self.prose(text))
                if waiting.season_key:
                    # With the line a built section's key carries, since it is the
                    # same key and renaming this section disconnects it the same way.
                    self.assertIn("it is how a card finds this one", self.prose(text))
        # A section that declared no draw is not told it lost one.
        undrawn = dataclasses.replace(self.WAITING, rounds=())
        text = config_mod.render(
            config_mod.Config(path=Path("config.toml"), exists=True,
                              competitions={undrawn.id: undrawn}),
            competitions=[self.comp])
        with self.subTest(mode="single, no draw"):
            self.assertEqual(self.loaded(text).competition(undrawn.id), undrawn)
            self.assertNotIn("draw you declared", self.prose(text))

    def test_the_length_a_declared_draw_pins_is_kept_when_its_rounds_are_not(self):
        """A draw with no ``rounds_total`` beside it is how long the season is.

        Its regular rounds are the season's length, and ``--force`` drops the draw,
        so a length only the draw stated went with it. Measured on
        /tmp/t4/scratch/i417/fix2: nine rounds declared read "3 of 9" before the
        rewrite and "3 rounds played" after, with ``# rounds_total = 9`` commented
        in the file and nothing in the refusal about it. The length is written live
        -- the club declared it, through the draw -- for a section the cards built
        and one they did not, finals left out whether the draw marks them
        ``finals = true`` or they are known by their label. Rejected: saying in the
        refusal that the length goes too, which leaves what ``--force`` drops
        depending on whether a club wrote the length twice. A final typed against
        its label is counted as the run after reads it, with no draw to type it in.
        """
        # A semi-final the club typed a final, and a grand final known by its label.
        draw = (tuple(config_mod.RoundConfig(number=str(n)) for n in range(1, 10))
                + (config_mod.RoundConfig(number="SF", finals=True),
                   config_mod.RoundConfig(number="GF")))
        on_cards = config_mod.CompetitionConfig(
            id=self.comp.id, day="Tuesday", division="C", rounds=draw)
        waiting = dataclasses.replace(self.WAITING, rounds_total=None, rounds=draw)
        config = config_mod.Config(
            path=Path("config.toml"), exists=True,
            competitions={on_cards.id: on_cards, waiting.id: waiting})
        loaded = self.loaded(config_mod.render(config, competitions=[self.comp]))
        for section in (on_cards, waiting):
            with self.subTest(section=section.id):
                self.assertEqual(loaded.competition(section.id).rounds_total, 9)

    def test_a_file_that_carries_a_section_over_is_not_written_as_a_stub(self):
        # The stub says there is nothing to describe yet, which stops being true the
        # moment a section is written under it.
        config = config_mod.Config(path=Path("config.toml"), exists=True,
                                   competitions={self.WAITING.id: self.WAITING})
        text = config_mod.render(config, competitions=(), cards=6)
        self.assertIn(f"[competitions.{self.WAITING.id}]", text)
        self.assertNotIn("nothing to describe yet", self.prose(text))

    # --- a file written before the first card --------------------------------

    def test_a_config_written_before_any_card_does_not_say_cards_wrote_it(self):
        """The header was a fixed string, and on this file it was false.

        A run on an empty `data/` writes this file -- the report says `pages seen
        0` -- and the first sentence of it read "Written for you from the
        scorecards in data/". Nothing in it came from a scorecard, because there
        were none to read.
        """
        text = config_mod.render(self.blank, competitions=(), cards=0)
        self.assertNotIn("from the scorecards in data/", text)
        self.assertIn("before any scorecard", text)

    def test_and_a_config_written_from_cards_still_says_that(self):
        text = config_mod.render(self.blank, competitions=[self.comp], cards=6)
        self.assertIn("from the scorecards in data/", text)
        self.assertNotIn("before any scorecard", text)

    def test_the_stub_names_a_command_that_will_fill_it_in(self):
        """It named the one command that cannot: `build.py` on its own.

        The advice under the empty competitions block read "Add your PDFs and run
        build.py again", and a plain run does not touch a `config.toml` that
        exists -- that is what `--discover` is for. So the file told a club to
        wait for something no run would ever do, while the dashboard was built
        beside it from cards this file said nothing about.
        """
        text = config_mod.render(self.blank, competitions=(), cards=0)
        self.assertIn("build.py --discover --force", text)
        self.assertNotIn("Add your PDFs and run build.py again", text)

    def test_and_the_stub_says_the_dashboard_does_not_wait_for_this_file(self):
        # First, because it is what a club wants first and what is already true:
        # nothing in this file is needed to read cards and build a dashboard.
        text = config_mod.render(self.blank, competitions=(), cards=0)
        advice = text[text.index("No scorecards were found"):]
        self.assertIn("dashboard", advice)
        self.assertLess(advice.index("dashboard"),
                        advice.index("--discover --force"),
                        "the rewrite is offered before the run that needs nothing")

    def prose(self, text: str) -> str:
        """The file's comment text as one line, so a needle can span a wrap.

        The sentences below are flowed to the file's width, and where they break
        depends on the number printed in them. A test that searched the text as
        written would be a test that passes or fails on the arithmetic of a
        single-digit count.
        """
        return " ".join(line.lstrip("#").strip() for line in text.splitlines())

    def test_a_config_written_where_every_card_was_refused_does_not_say_none_was_found(self):
        """The other way to this block: cards were read and none built anything.

        "No scorecards were found under data/" sends a club to look for its PDFs.
        They are where it put them -- all six of them -- and the report above this
        file says what each one was refused for, which is the thing to read next.
        """
        text = config_mod.render(self.blank, competitions=(), cards=6)
        self.assertIn("from the 6 scorecards under data/", self.prose(text))
        self.assertNotIn("No scorecards were found", text)
        one = config_mod.render(self.blank, competitions=(), cards=1)
        self.assertIn("from the 1 scorecard under data/", self.prose(one))



class TestAHomeCourtIsTheOneItsHomeCardsPrintMost(unittest.TestCase):
    """4.469: a team's court is offered from its home cards, and a tie offers none.

    The writer offered no court the cards gave, and wrote ``# "<first team>" = 1``
    for every competition (/tmp/mcf/scratch/CONFIG/probes/out-before.txt). Every
    card prints the court it was played on, and a team's home cards print its home
    court: on a real season of 201 cards, the court printed most at home is the
    court the club typed for each of the 54 teams with a home card. So the court
    printed most is offered, and where two courts are printed as often neither
    is: one of them picked by its number would be a guess the comment could not
    stand behind.
    Rejected: the lower number of a tie, and the court of the latest card, which a
    night moved to another court for one week would give.
    """

    def team(self, slug: str, *courts: tuple[int, int]) -> Team:
        return Team(slug=slug, name=slugs.unslug(slug), courts_printed=courts)

    def test_the_court_printed_most_is_the_teams_and_a_tie_is_nobodys(self):
        self.assertEqual(self.team("iguanas", (3, 3)).court_printed, 3)
        self.assertEqual(self.team("iguanas", (2, 2), (4, 1)).court_printed, 2)
        self.assertIsNone(self.team("iguanas", (2, 1), (3, 1)).court_printed)
        self.assertIsNone(self.team("iguanas").court_printed)

    def test_each_home_card_counts_once_and_a_tie_reads_in_the_courts_order(self):
        # Discovery's own count: a card at home once, a washout with the rest, a
        # card at away for nobody, the court printed most first though its number
        # is the higher, and a tie in order of the courts' numbers and not of the
        # cards, so the comment is the same whichever was read first.
        from types import SimpleNamespace

        from matchcentre import discover as discover_mod

        def card(home: str, court: int | None, *, cancelled: bool = False):
            away = "riverside-rockets" if home == "iguanas" else "iguanas"
            return discover_mod._Sided(
                SimpleNamespace(court=court, cancelled=cancelled), home, away)

        sided = [card("iguanas", 3), card("iguanas", 2, cancelled=True),
                 card("riverside-rockets", None), card("riverside-rockets", 5),
                 card("riverside-rockets", 4), card("riverside-rockets", 5)]
        self.assertEqual(discover_mod._home_courts(sided),
                         {"iguanas": ((2, 1), (3, 1)),
                          "riverside-rockets": ((5, 2), (4, 1))})

    def courts_block(self, comp, config=None) -> list[str]:
        text = config_mod.render(config or config_mod.Config(path=Path("config.toml")),
                                 competitions=[comp], cards=6)
        lines = text.splitlines()
        at = lines.index(f"[competitions.{comp.id}.courts]")
        return lines[at + 1:lines.index("", at)]

    def test_a_tie_is_named_in_a_comment_and_given_no_line(self):
        comp = dataclasses.replace(make_competition([]), teams={
            "iguanas": self.team("iguanas", (2, 1), (3, 1)),
            "riverside-rockets": self.team("riverside-rockets", (4, 2))})
        block = self.courts_block(comp)
        self.assertEqual([line for line in block if '" = ' in line],
                         ['# "Riverside Rockets" = 4'])
        self.assertIn("The home cards of Iguanas print court 2 on 1 card and court 3 "
                      "on 1, so no court is offered for it.",
                      " ".join(line.lstrip("# ") for line in block))

    def test_a_court_the_club_declares_beside_a_tie_is_kept_as_it_was(self):
        # A tie's comment is for a team the club gives no court. A court the club
        # declares is its own line, and stays the only one whatever the cards print.
        comp = dataclasses.replace(make_competition([]), teams={
            "iguanas": self.team("iguanas", (2, 1), (3, 1))})
        declared = config_mod.Config(
            path=Path("config.toml"), exists=True,
            competitions={comp.id: config_mod.CompetitionConfig(
                id=comp.id, courts={"Iguanas": 7})})
        block = self.courts_block(comp, declared)
        self.assertEqual([line for line in block if " = " in line], ["Iguanas = 7"])
        self.assertNotIn("no court is offered", " ".join(block))

    def test_with_no_court_on_any_card_the_table_keeps_its_example(self):
        # No card here printed a court, so there is nothing to read one from: the
        # example line a club types over stays, under the comment it had.
        block = self.courts_block(make_competition(["riverside-rockets", "iguanas"]))
        self.assertEqual(block, ["# Each team's home court, if your club plays on "
                                 "numbered courts.", "# Iguanas = 1"])


class TestAProposalIsNamedAfterItsConfigAndKnownByItsFirstLine(unittest.TestCase):
    """4.431: the proposal ``--discover`` writes beside a config, and how it is known.

    Named after the config it proposes for and put beside it, so ``--config
    other.toml`` has ``other.proposed.toml``; and known as this program's by its
    first line, as the demo's config is, so a file a club wrote under that name is
    never written over. Rejected: one ``config.proposed.toml`` whatever the config
    was called, which two configs in one folder would share; and the proposal under
    ``--out``, which is published.
    """

    def test_it_is_named_after_the_config_and_put_beside_it(self):
        for config, proposed in (("config.toml", "config.proposed.toml"),
                                 ("other.toml", "other.proposed.toml"),
                                 ("notes.txt", "notes.proposed.toml"),
                                 ("a/b/club.toml", "a/b/club.proposed.toml")):
            self.assertEqual(config_mod.proposal_path(Path(config)), Path(proposed))

    def proposed(self) -> str:
        return config_mod.proposal("[club]\n", config_name="club.toml",
                                   written="10 Sep 2026, 12:00pm", cards=8,
                                   drops="Everything you have declared in it is "
                                         "written back.")

    def test_one_this_program_wrote_is_known_by_its_first_line(self):
        text = self.proposed()
        self.assertTrue(config_mod.written_as_proposal(text))
        self.assertTrue(text.endswith("\n[club]\n"))
        self.assertFalse(config_mod.written_as_proposal("# Our own draft.\n"))
        self.assertFalse(config_mod.written_as_proposal("\n" + text))
        self.assertFalse(config_mod.written_as_proposal(text[1:]))
        # The marker's words are its whole first line, and not the start of one.
        self.assertFalse(config_mod.written_as_proposal(
            config_mod.PROPOSAL_MARKER + " (edited)\n# Our own notes.\n"))

    def test_the_date_it_was_written_on_is_read_back_from_its_header(self):
        # What a run renders the proposal with to ask whether the file holds it
        # already, as the site's pages keep the stamp they were written with.
        # Read off the header however it was flowed, the version's length moving
        # the date from line to line.
        for length in (0, 7, 19, 29):
            version = "0." + "1" * length
            for written in ("8 Oct 2026, 12:00am", "10 Sep 2026, 12:00pm"):
                with self.subTest(version=version, written=written), \
                        mock.patch.object(config_mod, "__version__", version):
                    text = config_mod.proposal(
                        "[club]\n", config_name="club.toml", written=written,
                        cards=1, drops="Everything you have declared in it is "
                                       "written back.")
                    self.assertEqual(config_mod.proposal_written(text), written)
        self.assertIsNone(config_mod.proposal_written("# Our own draft.\n"))
        self.assertIsNone(config_mod.proposal_written(self.proposed()[1:]))
        self.assertIsNone(config_mod.proposal_written(
            config_mod.PROPOSAL_MARKER + "\n[club]\n"))

    def test_its_header_says_what_it_is_and_keeps_to_the_width(self):
        text = self.proposed()
        said = " ".join(line.lstrip("# ") for line in text.splitlines())
        for words in ("from the 8 scorecards it read", "club.toml already exists",
                      "No run reads this file.", "copy it over club.toml",
                      "Everything you have declared in it is written back."):
            self.assertIn(words, said)
        self.assertEqual([line for line in text.splitlines() if len(line) > 78], [])

    def test_the_date_it_was_written_is_never_split_over_two_lines(self):
        # Flowed at its spaces, the header 0.1.0 wrote on 8 Oct 2026 ended a line
        # "on 8" and began the next "Oct 2026, 12:00am" (/tmp/mcf/scratch/CONFIG/
        # probes/after/club/config.proposed.toml): 13 of the 90 versions and dates
        # here split it. Rejected: a no-break space written in the file, which an
        # editor shows as a space and a search for the date typed does not find.
        for length in range(30):
            version = "0." + "1" * length
            for written in ("8 Oct 2026, 12:00am", "10 Sep 2026, 12:00pm",
                            "28 Feb 2027, 11:59pm"):
                with self.subTest(version=version, written=written), \
                        mock.patch.object(config_mod, "__version__", version):
                    lines = config_mod.proposal(
                        "[club]\n", config_name="club.toml", written=written,
                        cards=8, drops="Everything you have declared in it is "
                                       "written back.").splitlines()
                    self.assertTrue(any(written in line for line in lines), lines[:6])
                    self.assertEqual([line for line in lines
                                      if len(line) > 78 or "\0" in line], [])


class TestAFirstRunWritesWhatItWorkedOutCommentedOut(unittest.TestCase):
    """Every value the program worked out goes into the file with a # in front of it.

    The first run wrote five kinds of value live that it had only worked out: the
    team names it un-shouted, the captains the (C) marks gave, the club name off the
    letterhead, a title made from that name, and in multi-season mode each season's
    label as its cards print it. The next run read every one of them back as the
    club's own. Measured on the demo season (/tmp/t4/scratch/d438/m/before2): the
    second run with no new card wrote 4 of 9 files, and built into a fresh folder
    under the first run's date it differed from the first in season.json, whose
    captains went from card to declared and teams from derived to mixed. So a line
    is written live only when the club declared it, and a value becomes declared
    when the club removes its #, which is what the file tells a club to do.

    Rejected: reading a live value back as worked out while it still holds what the
    program wrote. That needs a record of what was written, and a club that typed
    the same value by hand could then never declare it.
    """

    LETTERHEAD = "RIVERSIDE TENNIS CLUB INC"
    CLUB = "Riverside Tennis Club"

    def setUp(self):
        # Each way a value is worked out, once: two names un-shouted and one the
        # card printed in mixed case, a (C) mark, a season its cards name and one
        # only its folder does. With neither the mixed-case print nor the folder's
        # name in the fixture, a mutant writing either live survived every test
        # (/tmp/t4/scratch/d438-review/mut).
        marked = Player(slug="rae-tolland", name="Rae Tolland", team="iguanas",
                        is_captain=True, captain_source=Provenance.CARD)
        self.comp = dataclasses.replace(make_competition([]).with_teams({
            "riverside-rockets": Team(slug="riverside-rockets",
                                      name="Riverside Rockets"),
            "harbour-herons": Team(slug="harbour-herons", name="Harbour Herons",
                                   name_source=Provenance.CARD),
            "iguanas": Team(slug="iguanas", name="Iguanas", captain="rae-tolland",
                            players=(marked,))}),
            id="2026-tuesday-division-c", season_key="2026", season="Season 1")
        self.last = dataclasses.replace(make_competition([]),
                                        id="2025-tuesday-division-c", season_key="2025")
        self.multi = config_mod.Config(path=Path("config.toml"), seasons_mode="multi")

    # What the program worked out from the two competitions above, as live lines.
    WORKED_OUT = (f'name = "{LETTERHEAD}"',
                  f'title = "{LETTERHEAD} season dashboards"',
                  'riverside-rockets = "Riverside Rockets"',
                  'harbour-herons = "Harbour Herons"',
                  'iguanas = "Iguanas"',
                  'Iguanas = "Rae Tolland"',
                  '2025 = "2025"',
                  '2026 = "Season 1"')

    def render(self, config, comp=None) -> str:
        return config_mod.render(config, competitions=[self.last, comp or self.comp],
                                 club_name=self.LETTERHEAD, cards=6)

    @staticmethod
    def loaded(text: str) -> config_mod.Config:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_every_value_it_worked_out_is_written_behind_a_hash(self):
        text = self.render(self.multi)
        for line in self.WORKED_OUT:
            with self.subTest(line=line):
                self.assertRegex(text, rf"(?m)^# {re.escape(line)}$")
                self.assertNotRegex(text, rf"(?m)^{re.escape(line)}$")

    def test_so_the_file_it_wrote_is_read_back_with_nothing_declared(self):
        loaded = self.loaded(self.render(self.multi))
        section = loaded.competition(self.comp.id)
        self.assertEqual((loaded.club_name, loaded.title, loaded.team_names,
                          loaded.season_labels, section.captains),
                         ("", "", {}, {}, {}))

    def test_removing_the_hash_from_a_line_declares_it(self):
        text = self.render(self.multi)
        for line in self.WORKED_OUT:
            text = re.sub(rf"(?m)^# {re.escape(line)}$", lambda _, line=line: line,
                          text)
        loaded = self.loaded(text)
        self.assertEqual(
            (loaded.club_name, loaded.title, loaded.team_names, loaded.season_labels,
             loaded.competition(self.comp.id).captains),
            (self.LETTERHEAD, f"{self.LETTERHEAD} season dashboards",
             {"riverside-rockets": "Riverside Rockets",
              "harbour-herons": "Harbour Herons", "iguanas": "Iguanas"},
             {"2025": "2025", "2026": "Season 1"}, {"Iguanas": "Rae Tolland"}))

    def test_what_the_club_declared_is_written_live_beside_what_was_worked_out(self):
        """Both halves in one file: the club's lines live, the program's commented.

        Each table can hold both, so each is asked for both: a club that named one
        team, one captain and one season keeps those lines as it wrote them, and
        what the cards gave for the others is written behind a #.
        """
        iguanas = dataclasses.replace(self.comp.teams["iguanas"], name="The Iguanas",
                                      name_source=Provenance.DECLARED)
        comp = dataclasses.replace(self.comp, teams={**self.comp.teams,
                                                     "iguanas": iguanas})
        declared = dataclasses.replace(
            self.multi, exists=True, club_name=self.CLUB, title="Riverside Nights",
            team_names={"iguanas": "The Iguanas"},
            season_labels={"2025": "Winter 2025"},
            competitions={comp.id: config_mod.CompetitionConfig(
                id=comp.id, day="Tuesday", division="C", season_key="2026",
                captains={"Riverside Rockets": "Ana Fernandez"})})
        text = self.render(declared, comp)
        for line in (f'name = "{self.CLUB}"', 'title = "Riverside Nights"',
                     'iguanas = "The Iguanas"', '"Riverside Rockets" = "Ana Fernandez"',
                     '2025 = "Winter 2025"'):
            with self.subTest(live=line):
                self.assertRegex(text, rf"(?m)^{re.escape(line)}$")
        for line in ('riverside-rockets = "Riverside Rockets"',
                     '"The Iguanas" = "Rae Tolland"', '2026 = "Season 1"'):
            with self.subTest(commented=line):
                self.assertRegex(text, rf"(?m)^# {re.escape(line)}$")
        loaded = self.loaded(text)
        self.assertEqual(
            (loaded.club_name, loaded.title, loaded.team_names, loaded.season_labels,
             loaded.competition(comp.id).captains),
            (self.CLUB, "Riverside Nights", {"iguanas": "The Iguanas"},
             {"2025": "Winter 2025"}, {"Riverside Rockets": "Ana Fernandez"}))

    def test_a_file_with_no_letterhead_keeps_the_empty_name_a_club_types_into(self):
        """With no name to offer, the empty one is live, as it always was.

        The stub a run writes before the first card has no letterhead to put behind
        a #, and the rewrite it offers expects the club to type its name into
        ``name = ""``. Measured: written as ``# name = ""`` under the letterhead's
        note, the name a club typed there was still commented, and the rewrite read
        no name. An empty name declares nothing, so live it changes nothing either.
        """
        # And the same where cards were read but none printed a letterhead, which the
        # README allows for: a gate on the cards gave that file the letterhead's note
        # over `# name = ""` (/tmp/t4/scratch/d438-review/probe/no_letterhead.py).
        for cards in (0, 6):
            with self.subTest(cards=cards):
                text = config_mod.render(self.multi, competitions=[self.comp],
                                         cards=cards)
                self.assertRegex(text, r'(?m)^name = ""$')
                self.assertNotIn("letterhead", text)
                typed = self.loaded(text.replace('name = ""', f'name = "{self.CLUB}"'))
                self.assertEqual(typed.club_name, self.CLUB)

    def test_the_title_it_suggests_is_the_one_the_page_shows(self):
        """Made from the club's name where the club gave one, as the page's is.

        The title was made from the letterhead whatever the file said, and written
        live: measured, ``--discover --force`` over a file declaring the name and no
        title wrote ``title = "BRINDLE CREEK TENNIS CLUB INC season dashboards"``,
        and the plain run after it retitled the site in capitals.
        """
        named = dataclasses.replace(self.multi, exists=True, club_name=self.CLUB)
        self.assertRegex(self.render(named),
                         rf'(?m)^# title = "{self.CLUB} season dashboards"$')

    def test_a_file_an_earlier_version_wrote_is_read_as_it_always_was(self):
        """Live lines the old writer put there are the club's now, and stay live.

        Nothing records which live lines a club typed, so every one is read as
        declared, as it was before, and a rewrite writes each back live. The text is
        the shape the old writer gave a multi-season file.
        """
        old = ('[club]\nname = "RIVERSIDE TENNIS CLUB INC"\n\n'
               '[build]\nseasons = "multi"\n'
               'title = "RIVERSIDE TENNIS CLUB INC season dashboards"\n\n'
               '[seasons]\n2025 = "2025"\n2026 = "Season 1"\n\n'
               '[team_names]\nriverside-rockets = "Riverside Rockets"\n'
               'harbour-herons = "Harbour Herons"\niguanas = "Iguanas"\n\n'
               '[competitions.2026-tuesday-division-c]\nlabel = "Tuesday Division C"\n'
               'day = "Tuesday"\ndivision = "C"\nseason_key = "2026"\n\n'
               '[competitions.2026-tuesday-division-c.captains]\n'
               'Iguanas = "Rae Tolland"\n')
        loaded = self.loaded(old)
        self.assertEqual(
            (loaded.club_name, loaded.title, loaded.team_names, loaded.season_labels,
             loaded.competition(self.comp.id).captains),
            (self.LETTERHEAD, f"{self.LETTERHEAD} season dashboards",
             {"riverside-rockets": "Riverside Rockets",
              "harbour-herons": "Harbour Herons", "iguanas": "Iguanas"},
             {"2025": "2025", "2026": "Season 1"}, {"Iguanas": "Rae Tolland"}))
        declared = {slug: dataclasses.replace(team, name_source=Provenance.DECLARED)
                    for slug, team in self.comp.teams.items()}
        text = self.render(loaded, dataclasses.replace(self.comp, teams=declared))
        for line in self.WORKED_OUT:
            with self.subTest(line=line):
                self.assertRegex(text, rf"(?m)^{re.escape(line)}$")


    def test_each_line_behind_a_hash_is_under_a_note_saying_how_to_use_it(self):
        """A # a club is not told to remove is a line it will never use.

        The notes over these tables said "Fix any that read wrongly" and "Correct as
        needed", which were true of a live line; over a commented one a club that
        corrects it has still declared nothing. So the comment block over each line
        the program worked out says to remove the #, including a captains table the
        club had begun, where the (C) marks sit behind a # among its own lines.
        """
        begun = dataclasses.replace(
            self.multi, exists=True,
            competitions={self.comp.id: config_mod.CompetitionConfig(
                id=self.comp.id, day="Tuesday", division="C", season_key="2026",
                captains={"Riverside Rockets": "Ana Fernandez"})})
        for name, config in (("nothing declared", self.multi),
                             ("captains begun", begun)):
            lines = self.render(config).splitlines()
            for line in self.WORKED_OUT:
                with self.subTest(config=name, line=line):
                    at = lines.index(f"# {line}")
                    above = []
                    while at > 0 and lines[at - 1].startswith("#"):
                        at -= 1
                        above.insert(0, lines[at].lstrip("#").strip())
                    self.assertIn("remove the #", " ".join(above).lower(),
                                  f"nothing above {line!r} says how to use it")

    def test_the_docs_no_longer_make_an_exception_of_the_run_after_a_first(self):
        """The README, the example config and the recipe say what the file now does.

        The README said the run straight after a first run was the exception to a
        rebuild writing nothing, CONTRIBUTING built the "before" site twice to get
        past it, and config.example.toml said the first run writes the team names
        and (C) marks in, as if live.
        """
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("`--generated` sets the date")
        paragraph = " ".join(readme[start:readme.index("\n\n", start)].split())
        self.assertIn("That holds for the run straight after a first run too",
                      paragraph)
        self.assertNotIn("exception", paragraph)
        recipe = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertEqual(recipe.count("--out /tmp/before"), 1)
        example = " ".join(
            line.lstrip("#").strip() for line in
            (ROOT / "config.example.toml").read_text(encoding="utf-8").splitlines()
            if line.startswith("#"))
        for said in ("A first run writes each guess in for you with a # in front",
                     "a first run writes those marks here with a # in front"):
            with self.subTest(said=said):
                self.assertIn(said, example)


class TestTheRulesNoCardCanGiveStayBehindAHashUntilTheClubChoosesThem(unittest.TestCase):
    """``scoring`` and ``sets`` are written live only where the club declared them.

    Both were filled in with the program's defaults as the file was read, and written
    back live, so a first run's file said ``scoring = "games-bonus"`` and ``sets =
    "first-to-6"`` as if the club had chosen them, and the note asking a club to
    check them was printed only while there was no file at all. Measured on the demo
    season (/tmp/t7/scratch/RU1/b140): the first run's report had the note once and
    the second's no times, and a file holding only ``[club] name`` had it no times
    while "rules in force" printed games-bonus and first-to-6. Now ``""`` is
    undeclared, as it already was in a competition's own section, and the default is
    filled in where a rule is asked for.

    Rejected: a declared flag beside each filled-in default, which is two fields
    that can disagree.
    """

    def setUp(self):
        self.blank = config_mod.Config(path=Path("config.toml"))
        self.comp = make_competition(["riverside-rockets", "iguanas"])

    @staticmethod
    def loaded(text: str) -> config_mod.Config:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_a_file_that_chose_neither_is_written_with_both_behind_a_hash(self):
        text = config_mod.render(self.blank, competitions=[self.comp])
        for line in (f'scoring = "{scoring.DEFAULT_RULESET}"',
                     f'sets = "{fmt_mod.DEFAULT_SET_RULE}"'):
            with self.subTest(line=line):
                self.assertRegex(text, rf"(?m)^# {re.escape(line)}$")
        self.assertNotRegex(text, r"(?m)^(scoring|sets) = ")

    def test_each_is_under_a_note_saying_to_remove_the_hash(self):
        lines = config_mod.render(self.blank, competitions=[self.comp]).splitlines()
        for key in ("scoring", "sets"):
            with self.subTest(key=key):
                at = next(i for i, line in enumerate(lines)
                          if line.startswith(f"# {key} = "))
                above = []
                while lines[at - 1].startswith("#"):
                    at -= 1
                    above.insert(0, lines[at].lstrip("#").strip())
                self.assertIn("remove the #", " ".join(above).lower())

    def test_read_back_it_declares_neither_and_the_defaults_are_in_force(self):
        loaded = self.loaded(config_mod.render(self.blank, competitions=[self.comp]))
        self.assertEqual((loaded.scoring, loaded.sets), ("", ""))
        self.assertEqual((loaded.ruleset_for().name, loaded.set_rule_for().name),
                         (scoring.DEFAULT_RULESET, fmt_mod.DEFAULT_SET_RULE))
        self.assertTrue(loaded.rules_left_to_default())

    def test_removing_each_hash_declares_the_rule_that_was_in_force(self):
        text = re.sub(r"(?m)^# ((?:scoring|sets) = )", r"\1",
                      config_mod.render(self.blank, competitions=[self.comp]))
        loaded = self.loaded(text)
        self.assertEqual((loaded.scoring, loaded.sets),
                         (scoring.DEFAULT_RULESET, fmt_mod.DEFAULT_SET_RULE))
        self.assertFalse(loaded.rules_left_to_default())

    def test_what_the_club_chose_is_written_back_live(self):
        chosen = dataclasses.replace(self.blank, exists=True, scoring="rubbers",
                                     sets="pro-8")
        text = config_mod.render(chosen, competitions=[self.comp])
        self.assertRegex(text, r'(?m)^scoring = "rubbers"$')
        self.assertRegex(text, r'(?m)^sets = "pro-8"$')
        loaded = self.loaded(text)
        self.assertEqual((loaded.scoring, loaded.sets), ("rubbers", "pro-8"))

    def test_the_default_the_club_chose_is_written_back_live_too(self):
        """A club that deleted both #s, as the file says to, keeps them over a rewrite.

        The line it chose is the same text as the default's, so a writer asking
        "is this the default?" in place of "was this declared?" would put the #
        back, and the note with it, on the club that did what it was asked.
        """
        chosen = dataclasses.replace(self.blank, exists=True,
                                     scoring=scoring.DEFAULT_RULESET,
                                     sets=fmt_mod.DEFAULT_SET_RULE)
        text = config_mod.render(chosen, competitions=[self.comp])
        self.assertRegex(text, rf'(?m)^scoring = "{scoring.DEFAULT_RULESET}"$')
        self.assertRegex(text, rf'(?m)^sets = "{fmt_mod.DEFAULT_SET_RULE}"$')
        self.assertNotIn("in force until you choose", text)
        self.assertEqual(self.loaded(text).rules_left_to_default(), ())

    def own(self, **rules) -> config_mod.CompetitionConfig:
        return config_mod.CompetitionConfig(id=self.comp.id, day="Tuesday",
                                            division="C", **rules)

    @staticmethod
    def rules_block(text: str) -> str:
        return text.split("\n[rules]\n", 1)[1].split("\n[", 1)[0]

    def test_where_every_competition_sets_its_own_rules_none_is_offered(self):
        """``[rules]`` reaches no competition here, so its default is in force for none.

        Measured over a demo file whose one section declares ``scoring`` and ``sets``
        (/tmp/t7/scratch/RU1-review/pa/r140/sec-both2.toml): after ``--discover
        --force`` the report had no note, and ``[rules]`` still said of the default
        "in force until you choose. Remove the # once it is your competition's."
        Deleting that # changed no result and declared a rule for no competition.
        """
        own = self.own(scoring="rubbers", sets="pro-8")
        config = dataclasses.replace(self.blank, exists=True,
                                     competitions={own.id: own})
        text = config_mod.render(config, competitions=[self.comp])
        rules = self.rules_block(text)
        self.assertNotRegex(rules, r"(?m)^#? ?(scoring|sets) = ")
        self.assertNotIn("in force until you choose", rules)
        prose = " ".join(line.lstrip("#").strip() for line in rules.splitlines())
        for key in ("scoring", "sets"):
            with self.subTest(key=key):
                self.assertIn(f"{key} is set in each [competitions] section below",
                              prose)
        self.assertRegex(text, r'(?m)^scoring = "rubbers"$')
        self.assertRegex(text, r'(?m)^sets = "pro-8"$')

    def test_a_competition_that_sets_one_of_its_own_is_offered_the_other(self):
        """Each key is asked of every section alone, with cards this run or without.

        A section that sets ``scoring`` and not ``sets`` is still reached by the
        [rules] sets line, so that line is offered and the scoring one is not,
        whether the section is the one the cards built or one carried with none.
        """
        carried = config_mod.CompetitionConfig(id="friday-division-a", day="Friday",
                                               division="A", scoring="rubbers")
        for sections in ((self.own(scoring="rubbers"),),
                         (self.own(scoring="rubbers", sets="pro-8"), carried)):
            config = dataclasses.replace(
                self.blank, exists=True,
                competitions={section.id: section for section in sections})
            with self.subTest(sections=[section.id for section in sections]):
                rules = self.rules_block(config_mod.render(config,
                                                           competitions=[self.comp]))
                self.assertNotRegex(rules, r"(?m)^#? ?scoring = ")
                self.assertRegex(rules, rf'(?m)^# sets = "{fmt_mod.DEFAULT_SET_RULE}"$')

    def test_one_competition_that_sets_none_keeps_the_default_offered(self):
        own = self.own(scoring="rubbers", sets="pro-8")
        other = dataclasses.replace(self.comp, id="thursday-division-a",
                                    day="Thursday", division="A")
        # With cards this run, and declared with none: [rules] reaches either.
        waiting = config_mod.CompetitionConfig(id="friday-division-a", day="Friday",
                                               division="A")
        for comps, sections in (([self.comp, other], (own,)),
                                ([self.comp], (own, waiting))):
            config = dataclasses.replace(
                self.blank, exists=True,
                competitions={section.id: section for section in sections})
            with self.subTest(sections=[section.id for section in sections],
                              cards=[comp.id for comp in comps]):
                rules = self.rules_block(config_mod.render(config, competitions=comps))
                for key, default in (("scoring", scoring.DEFAULT_RULESET),
                                     ("sets", fmt_mod.DEFAULT_SET_RULE)):
                    self.assertRegex(rules, rf'(?m)^# {key} = "{default}"$')
                self.assertEqual(rules.count("in force until you choose"), 2)

    def test_a_competition_is_asked_before_rules_and_the_default_comes_last(self):
        own = config_mod.CompetitionConfig(id="thursday-a", scoring="rubbers")
        both = dataclasses.replace(own, sets="pro-8")
        under_rules = dataclasses.replace(self.blank, sets="fast4")
        both_under_rules = dataclasses.replace(under_rules, scoring="simple")
        for config, comp, rules, sets, left in (
                (self.blank, None, "games-bonus", "first-to-6", ("scoring", "sets")),
                (self.blank, own, "rubbers", "first-to-6", ("sets",)),
                (self.blank, both, "rubbers", "pro-8", ()),
                (under_rules, own, "rubbers", "fast4", ()),
                (under_rules, None, "games-bonus", "fast4", ("scoring",)),
                (both_under_rules, own, "rubbers", "fast4", ()),
                (both_under_rules, None, "simple", "fast4", ())):
            with self.subTest(rules=(config.scoring, config.sets),
                              comp=comp and (comp.scoring, comp.sets)):
                self.assertEqual((config.ruleset_for(comp).name,
                                  config.set_rule_for(comp).name,
                                  config.rules_left_to_default(comp)),
                                 (rules, sets, left))

    def test_an_empty_line_is_one_left_off_and_a_misspelt_one_is_still_refused(self):
        loaded = self.loaded('[rules]\nscoring = ""\nsets = ""\n')
        self.assertEqual((loaded.scoring, loaded.sets), ("", ""))
        for text in ('[rules]\nscoring = "bonus-points"\n',
                     '[rules]\nsets = "first-to-seven"\n'):
            with self.subTest(text=text), self.assertRaises(config_mod.ConfigError):
                self.loaded(text)


class TestTheWrittenConfigSuggestsTheNumberAlreadyInForce(unittest.TestCase):
    """The commented ``format`` and ``min_sets_for_result`` lines are the cards'.

    The review list says of both "commented out; delete the # to adopt it", so the
    line in the file has to be the one it printed. Measured before this on a
    doubles-2 season from tools/demo.py: the file said ``# format =
    "doubles-singles-doubles"`` and ``# min_sets_for_result = 3`` whatever the cards
    were. Adopting the number stopped the run with "min_sets_for_result=3 is
    outside 1..2"; adopting the shape refused all six cards.

    Rejected: a third review status, "not in the file", which is true and leaves a
    club typing a line this program could have written.
    """

    RULES_MIN = ("# min_sets_for_result = 3   # sets that must finish before a "
                 "result counts")

    def setUp(self):
        self.blank = config_mod.Config(path=Path("config.toml"))
        self.fours = make_competition(["riverside-rockets", "iguanas"])
        self.pairs = dataclasses.replace(self.fours, id="wednesday-division-c",
                                         day="Wednesday",
                                         fmt=fmt_mod.match_format("doubles-2"))

    @staticmethod
    def rules_block(text: str) -> str:
        return text.split("\n[rules]\n", 1)[1].split("\n[", 1)[0]

    @staticmethod
    def loaded(text: str) -> config_mod.Config:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_a_four_set_grade_is_offered_the_lines_it_always_was(self):
        text = config_mod.render(self.blank, competitions=[self.fours])
        rules = self.rules_block(text)
        self.assertIn('\n# format = "doubles-singles-doubles"\n', rules)
        self.assertTrue(rules.endswith("\n" + self.RULES_MIN + "\n"), rules[-200:])
        self.assertEqual(text.count("min_sets_for_result"), 1)
        self.assertEqual(len(re.findall(r"(?m)^# format = ", text)), 1)

    def test_a_two_set_grade_is_offered_its_own_number_and_its_own_shape(self):
        text = config_mod.render(self.blank, competitions=[self.pairs])
        rules = self.rules_block(text)
        self.assertRegex(rules, r"(?m)^# min_sets_for_result = 2   # sets that must "
                                r"finish before a result counts$")
        self.assertRegex(rules, r'(?m)^# format = "doubles-2"$')
        adopted = re.sub(r"(?m)^# (min_sets_for_result|format) = ", r"\1 = ", text)
        pinned = self.loaded(adopted).declared_format()
        self.assertEqual((pinned.name, pinned.min_sets_for_result), ("doubles-2", 2))

    def test_grades_that_differ_are_each_offered_their_own_lines_in_their_own_section(self):
        text = config_mod.render(self.blank, competitions=[self.fours, self.pairs])
        self.assertNotRegex(self.rules_block(text),
                            r"(?m)^#? ?(min_sets_for_result|format) = ")
        adopted = re.sub(r"(?m)^# (min_sets_for_result|format) = ", r"\1 = ", text)
        parsed = tomllib.loads(adopted)
        self.assertEqual(sorted(set(parsed["rules"]) & {"format", "min_sets_for_result"}),
                         [])
        for comp in (self.fours, self.pairs):
            with self.subTest(comp=comp.id):
                section = parsed["competitions"][comp.id]
                self.assertEqual(section.get("min_sets_for_result"),
                                 comp.fmt.min_sets_for_result)
                self.assertEqual(section.get("format"), comp.fmt.name)
        loaded = self.loaded(adopted)
        for comp in (self.fours, self.pairs):
            pinned = loaded.declared_format(loaded.competition(comp.id))
            self.assertEqual((pinned.name, pinned.min_sets_for_result),
                             (comp.fmt.name, comp.fmt.min_sets_for_result))

    def test_a_grade_that_says_its_own_is_left_out_of_the_question(self):
        # The two-set grade declares both, so [rules] is the four-set grade's.
        own = config_mod.CompetitionConfig(id=self.pairs.id, day="Wednesday",
                                           division="C", format="doubles-2",
                                           min_sets_for_result=2)
        config = config_mod.Config(path=Path("config.toml"), exists=True,
                                   competitions={own.id: own})
        text = config_mod.render(config, competitions=[self.fours, self.pairs])
        rules = self.rules_block(text)
        self.assertIn('\n# format = "doubles-singles-doubles"\n', rules)
        self.assertTrue(rules.endswith("\n" + self.RULES_MIN + "\n"), rules[-200:])
        self.assertEqual(len(re.findall(r"(?m)^#? ?format = ", text)), 2)
        self.assertEqual(len(re.findall(r"(?m)^#? ?min_sets_for_result = ", text)), 2)

    SHAPE_LINE = r"(?m)^#? ?(format|slots|min_sets_for_result) = "

    def test_with_no_competition_no_shape_is_offered_as_if_a_card_had_given_it(self):
        """A file written over no card has no cards' shape to offer, so it offers none.

        It offered the four-set shape and its number as it offers a card's. Measured
        on a file written over an empty folder (/tmp/t7/scratch/RU1/b191): over the
        demo's doubles-2 cards, deleting the # of the format line or of the slots
        line refused all 15 cards, "exit 1: 15 card(s) found and not scored", and of
        the min_sets_for_result line stopped the run at exit 2, "min_sets_for_result
        = 3, but the cards of wednesday-division-3 have 2 set row(s)". The keys are
        taught in words instead. Rejected: keeping the lines with a warning over
        them, which is still a line a club deletes the # of as the file says to.

        Written where six cards were read and every one refused, the same words
        said "No card has been read yet" above a last section that said "No
        competition could be built from the 6 scorecards under data/".
        """
        for cards in (0, 6):
            with self.subTest(cards=cards):
                text = config_mod.render(self.blank, competitions=(), cards=cards)
                rules = self.rules_block(text)
                self.assertNotRegex(rules, self.SHAPE_LINE)
                prose = " ".join(line.lstrip("#").strip()
                                 for line in rules.splitlines())
                for key in ("format", "slots", "min_sets_for_result"):
                    self.assertIn(key, prose)
                # A file with no [competitions] section is never sent to one.
                self.assertNotIn("[competitions]", prose)
                self.assertNotIn("has been read", prose)
                adopted = re.sub(r"(?m)^# ", "", rules)
                self.assertNotRegex(adopted,
                                    r"(?m)^(format|slots|min_sets_for_result) = ")
                loaded = self.loaded(text)
                self.assertEqual(
                    (loaded.format, loaded.slots, loaded.min_sets_for_result),
                    ("", (), None))

    def test_where_every_competition_says_its_own_none_is_offered_under_rules(self):
        # The same fallback: a [rules] line reaches no competition here, and the one
        # it offered was the four-set default, a shape none of these cards has
        # (/tmp/t7/scratch/RU1/alldeclared.py).
        own = config_mod.CompetitionConfig(id=self.pairs.id, day="Wednesday",
                                           division="C", format="doubles-2",
                                           min_sets_for_result=2)
        config = config_mod.Config(path=Path("config.toml"), exists=True,
                                   competitions={own.id: own})
        text = config_mod.render(config, competitions=[self.pairs])
        rules = self.rules_block(text)
        self.assertNotRegex(rules, self.SHAPE_LINE)
        # Said of the shape and of the number, each.
        self.assertEqual(" ".join(line.lstrip("#").strip() for line in
                                  rules.splitlines()).count(
                                      "each [competitions] section below"), 2)
        pinned = self.loaded(text).declared_format(self.loaded(text).competition(own.id))
        self.assertEqual((pinned.name, pinned.min_sets_for_result), ("doubles-2", 2))


class TestEveryShapeIsWrittenWithTheLineThatDeclaresIt(unittest.TestCase):
    """``shape_line``: ``format = <name>`` where format.py has one, else ``slots``.

    Measured before this: cards of two doubles and then a singles were written
    ``# format = "d1-d2-s1"``, a pattern the loader refuses, so deleting its ``#``
    stopped the next run at exit 2, "unknown match format 'd1-d2-s1'"; and every
    shape was written ``# slots = ["doubles", "singles", "singles", "doubles"]``,
    which refused all 15 cards of the demo's doubles-2 season once its ``#`` went.
    A club that had declared ``slots`` was offered ``# format = "d1-s1-s2-d2"``.

    A shape with a name is offered both lines, its slots as its cards have them, as
    every other line written behind a ``#`` is: the slots key is taught by a line of
    the club's own that loads. Rejected: a new name for each unnamed shape, a list
    of only the shapes somebody thought of for what ``slots`` declares already, and
    a slots line for unnamed shapes only, which leaves every other club the four-set
    list or no line of the key.
    """

    UNNAMED = ("dds", "sd", "dsds", "s", "sdd")

    def setUp(self):
        self.blank = config_mod.Config(path=Path("config.toml"))
        self.comp = make_competition(["riverside-rockets", "iguanas"])

    def shaped(self, fmt, **change):
        return dataclasses.replace(self.comp, fmt=fmt, **change)

    @staticmethod
    def unnamed(spec: str) -> fmt_mod.MatchFormat:
        return fmt_mod.from_rows([letter == "d" for letter in spec])

    @staticmethod
    def rules_lines(text: str) -> list[str]:
        block = text.split("\n[rules]\n", 1)[1].split("\n\n", 1)[0]
        return re.findall(r"(?m)^#? ?(?:format|slots) = .*", block)

    @staticmethod
    def loaded(text: str) -> config_mod.Config:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_a_named_shape_is_its_name_and_any_other_its_slots(self):
        for name, fmt in fmt_mod.FORMATS.items():
            with self.subTest(shape=name):
                self.assertEqual(config_mod.shape_line(fmt), f'format = "{name}"')
                self.assertEqual(fmt_mod.from_rows([s.doubles for s in fmt.slots]).name,
                                 name)
        for spec in self.UNNAMED:
            with self.subTest(shape=spec):
                words = ["doubles" if letter == "d" else "singles" for letter in spec]
                self.assertEqual(config_mod.shape_line(self.unnamed(spec)),
                                 f"slots = {config_mod.toml_value(words)}")

    def test_each_shape_s_lines_under_rules_load_as_its_own_shape(self):
        shapes = [*fmt_mod.FORMATS.values(), *map(self.unnamed, self.UNNAMED)]
        for fmt in shapes:
            with self.subTest(shape=fmt.name):
                text = config_mod.render(self.blank, competitions=[self.shaped(fmt)])
                offered = self.rules_lines(text)
                named = fmt_mod.shape_name(fmt.slots)
                self.assertEqual(offered, ([f"# {config_mod.shape_line(fmt)}"]
                                           if named else [])
                                 + [f"# {config_mod.slots_line(fmt)}"])
                adopted = re.sub(r"(?m)^# ((?:format|slots) = )", r"\1", text)
                self.assertEqual(self.loaded(adopted).declared_format().pattern,
                                 fmt.pattern)

    def test_declared_slots_are_offered_a_format_line_only_by_its_name(self):
        for slots, lines in (
                (("doubles", "singles", "singles", "doubles"),
                 ['# format = "doubles-singles-doubles"',
                  'slots = ["doubles", "singles", "singles", "doubles"]']),
                (("doubles", "doubles", "singles"),
                 ['slots = ["doubles", "doubles", "singles"]'])):
            with self.subTest(slots=slots):
                declared = dataclasses.replace(self.blank, slots=slots)
                fmt = fmt_mod.from_slot_spec(slots)
                text = config_mod.render(declared,
                                         competitions=[self.shaped(fmt)])
                self.assertEqual(self.rules_lines(text), lines)

    def test_a_declared_format_is_offered_the_slots_of_its_own_shape(self):
        # With no card read as well: the shape offered is the one declared, and
        # not doubles-singles-doubles because no competition had one to give.
        declared = dataclasses.replace(self.blank, format="doubles-3")
        fmt = fmt_mod.match_format("doubles-3")
        for label, comps in (("cards", [self.shaped(fmt)]), ("no cards", [])):
            with self.subTest(label):
                text = config_mod.render(declared, competitions=comps)
                self.assertEqual(self.rules_lines(text),
                                 ['format = "doubles-3"',
                                  '# slots = ["doubles", "doubles", "doubles"]'])

    def test_grades_of_two_shapes_are_each_offered_their_own_line_and_no_slots(self):
        other = self.shaped(self.unnamed("dds"), id="wednesday-division-c",
                            day="Wednesday")
        text = config_mod.render(self.blank, competitions=[self.comp, other])
        self.assertEqual(self.rules_lines(text), [])
        self.assertIn("format, or slots for a shape with no name, is set in each",
                      " ".join(text.split()))
        self.assertEqual(re.findall(r"(?m)^# (?:format|slots) = .*", text),
                         ['# format = "doubles-singles-doubles"',
                          '# slots = ["doubles", "doubles", "singles"]'])
        loaded = self.loaded(re.sub(r"(?m)^# ((?:format|slots) = )", r"\1", text))
        for comp in (self.comp, other):
            with self.subTest(comp=comp.id):
                pinned = loaded.declared_format(loaded.competition(comp.id))
                self.assertEqual(pinned.pattern, comp.fmt.pattern)


class TestOneFunctionSaysWhereTheFileHoldsAReviewValue(unittest.TestCase):
    """``held_in``: live, behind a ``#``, or not in the file, asked in process.

    The review asks it of every item on every run, so these are files a run could
    not write itself: a value behind a ``#`` over as many lines as it was written
    on, under a live header or a commented one, and the same keys with other
    values, which are not the value the item offers.
    """

    ITEM = ["[competitions.tuesday-division-c]",
            'teams = ["Northside Nets", "Southside Sets"]']
    ROUND = ["[[competitions.tuesday-division-c.rounds]]", 'number = "1"',
             'fixtures = [["Northside Nets", "Southside Sets"]]']

    def held(self, lines, *text):
        return config_mod.held_in(lines, "".join(line + "\n" for line in text))

    def test_a_value_live_in_the_file_is_live_whatever_it_says(self):
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-c]",
                                   'teams = ["Northside Nets", "Southside Sets"]'),
                         "live")
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-c]",
                                   'teams = ["Eastside Aces"]'), "live")
        self.assertEqual(self.held(self.ROUND,
                                   "[[competitions.tuesday-division-c.rounds]]",
                                   'number = "2"'), "live")

    def test_a_value_behind_a_hash_is_found_over_the_lines_it_was_written_on(self):
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-c]",
                                   "# teams = [", '#     "Northside Nets",',
                                   '#     "Southside Sets",', "# ]"), "commented")
        self.assertEqual(self.held(self.ROUND,
                                   "# [[competitions.tuesday-division-c.rounds]]",
                                   '# number = "1"', '# date = "2026-07-21"',
                                   "# " + self.ROUND[2]), "commented")

    def test_a_line_of_prose_with_an_equals_sign_hides_nothing_below_it(self):
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-c]",
                                   "# teams = what the cards named, for you to check",
                                   '# teams = ["Northside Nets", "Southside Sets"]'),
                         "commented")

    def test_the_same_keys_with_other_values_or_elsewhere_are_not_the_value(self):
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-c]",
                                   '# teams = ["Northside Nets", "Eastside Aces"]'),
                         "absent")
        self.assertEqual(self.held(self.ROUND,
                                   "# [[competitions.tuesday-division-c.rounds]]",
                                   '# number = "1"',
                                   '# fixtures = [["Home Team", "Away Team"]]'),
                         "absent")
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-b]",
                                   '# teams = ["Northside Nets", "Southside Sets"]'),
                         "absent")

    def test_a_file_that_holds_none_of_it_does_not_have_it(self):
        self.assertEqual(self.held(self.ITEM, "[rules]", '# format = "doubles-2"'),
                         "absent")
        self.assertEqual(self.held(self.ITEM), "absent")
        # A header is not a value, and nor is an item with no line at all.
        self.assertEqual(self.held(self.ITEM[:1], self.ITEM[0]), "absent")
        self.assertEqual(self.held([], *self.ITEM), "absent")


class TestALineLeftForTheClubToTypeIsHeldOnlyAsWritten(unittest.TestCase):
    """4.465 in process: ``held_in`` of an item whose line has nothing after its =.

    The review offers the season's length as ``rounds_total =`` for the club to
    finish, and ``held_in`` read the item's lines as TOML, which that line is not,
    so the item held no value and the run after said "not in it" of a file
    holding the line it wrote, and of one where the club had typed the length in.
    Measured on the base by these calls: ``# rounds_total =`` under the live
    header, and ``rounds_total = 10`` there, both "absent". Now a bare line is
    held where the file holds that line bare behind a ``#``, and live where the
    key is live, whatever it says, as any other value. Rejected: holding it by
    the key alone behind the ``#``, which would call a number the club typed
    there, and has not yet deleted the # of, the line that asks for one.
    """

    ITEM = ["[competitions.tuesday-division-c]", "rounds_total ="]
    SEASON = ["[competitions.tuesday-division-c]", "season ="]

    def held(self, lines, *text):
        return config_mod.held_in(lines, "".join(line + "\n" for line in text))

    def test_the_line_as_written_is_behind_a_hash(self):
        self.assertEqual(self.held(self.ITEM, self.ITEM[0], "# rounds_total ="),
                         "commented")
        self.assertEqual(self.held(self.SEASON, self.SEASON[0], "# season ="),
                         "commented")

    def test_and_the_prose_under_it_is_not_its_value(self):
        self.assertEqual(self.held(self.ITEM, self.ITEM[0], "# rounds_total =",
                                   "# rounds_total = what your draw gives",
                                   '# season = "Season 1"'), "commented")

    def test_a_length_the_club_made_live_is_live(self):
        self.assertEqual(self.held(self.ITEM, self.ITEM[0], "rounds_total = 10"),
                         "live")

    def test_a_number_behind_the_hash_or_another_header_is_not_the_line(self):
        self.assertEqual(self.held(self.ITEM, self.ITEM[0], "# rounds_total = 7"),
                         "absent")
        self.assertEqual(self.held(self.ITEM, "[competitions.tuesday-division-b]",
                                   "# rounds_total ="), "absent")
        self.assertEqual(self.held(self.ITEM, self.ITEM[0], "# season ="), "absent")


class TestAnItemOfSeveralBlocksIsHeldOnlyWhereEachIsHeldWhole(unittest.TestCase):
    """``held_in`` over an item of two rounds, and over a file of two blocks.

    Measured on the first cut: three changes to it passed every test written with
    it, asking any of a draw's rounds rather than each, dropping the check that a
    round is an array and not a table, and keeping only a table's last block. And
    it said "commented" of two names held in two ``# [team_names]`` blocks, and of
    a season under a ``# [competitions.x]`` in a file with that table live, where
    deleting the ``#`` in front of either header stops the load, "Cannot declare
    ... twice". Now each of an item's blocks has to be held whole by one block of
    the file, of the same table and kind, under a header that is live or that
    ``opens`` says can be. Rejected: merging a table's values across its blocks,
    the first cut, which counts values that no one ``#`` deleted adopts.
    """

    ROUNDS = ["[[competitions.tuesday-division-c.rounds]]", 'number = "1"',
              "[[competitions.tuesday-division-c.rounds]]", 'number = "2"']
    NAMES = ["[team_names]", 'northside-nets = "Northside Nets"',
             'southside-sets = "Southside Sets"']

    def held(self, lines, *text):
        return config_mod.held_in(lines, "".join(line + "\n" for line in text))

    def test_each_round_of_a_draw_has_to_be_behind_a_hash(self):
        one = ["# [[competitions.tuesday-division-c.rounds]]", '# number = "1"']
        two = ["# [[competitions.tuesday-division-c.rounds]]", '# number = "2"']
        self.assertEqual(self.held(self.ROUNDS, *one), "absent")
        self.assertEqual(self.held(self.ROUNDS, *two), "absent")
        self.assertEqual(self.held(self.ROUNDS, *one, *two), "commented")

    def test_a_round_is_not_held_by_a_table_of_the_same_name(self):
        self.assertEqual(self.held(self.ROUNDS[:2],
                                   "# [competitions.tuesday-division-c.rounds]",
                                   '# number = "1"'), "absent")
        self.assertEqual(self.held(self.NAMES[:2], "# [[team_names]]",
                                   '# northside-nets = "Northside Nets"'), "absent")

    def test_every_value_of_a_table_has_to_be_in_one_block(self):
        # Two blocks hold it between them, which no one deleted # adopts, and which
        # is not the same as its not being in the file.
        self.assertEqual(self.held(self.NAMES, "# [team_names]", "# " + self.NAMES[1],
                                   "# [team_names]", "# " + self.NAMES[2]), "split")
        self.assertEqual(self.held(self.NAMES, "[team_names]", "# " + self.NAMES[1]),
                         "absent")
        self.assertEqual(self.held(self.NAMES, "[team_names]", "# " + self.NAMES[1],
                                   "# " + self.NAMES[2]), "commented")
        self.assertEqual(self.held(self.NAMES, "# [team_names]", "# " + self.NAMES[1],
                                   "# " + self.NAMES[2]), "commented")

    def test_a_commented_header_the_file_has_live_already_holds_nothing(self):
        # Deleting its # declares the table twice, so what is under it is not a
        # value a deleted # adopts; under the live header, it is.
        item = ["[competitions.tuesday-division-c]", 'season = "2026"']
        self.assertEqual(self.held(item, "[competitions.tuesday-division-c]",
                                   'label = "Tuesday C"', "",
                                   "# [competitions.tuesday-division-c]",
                                   '# season = "2026"'), "absent")
        self.assertEqual(self.held(item, "[competitions.tuesday-division-c]",
                                   'label = "Tuesday C"', '# season = "2026"'),
                         "commented")


class TestOneTableOverTwoCommentedBlocksIsSaidToBeSplitAtEachHeader(unittest.TestCase):
    """``holding``: an item whose values two ``# [x]`` blocks of its table share.

    Measured before this (/tmp/t7/scratch/CB/probe/before.txt): two names under one
    ``# [team_names]`` were ``commented``, and the same two names split over two
    ``# [team_names]`` blocks were ``absent``, so the review told a club the values
    were not in a file it could see them in. Deleting the ``#`` of both blocks
    stopped the next run at exit 2, "Cannot declare ('team_names',) twice", so
    ``commented`` would be false too. Now the answer is ``split``, with the line of
    each header whose block holds a part, which is what the review needs to say
    which lines to gather under one header. Only a plain ``[x]`` table is one table
    over several blocks. Rejected: joining ``[[x]]`` blocks as well, which are two
    entries of an array, so that one entry's values split over them would be said
    to be one entry the file holds.
    """

    NAMES = ["[team_names]", 'northside-nets = "Northside Nets"',
             'southside-sets = "Southside Sets"']

    def holding(self, lines, *text):
        return config_mod.holding(lines, "".join(line + "\n" for line in text))

    def test_values_split_over_two_blocks_of_one_table_are_split_at_both_headers(self):
        text = ("# [team_names]", "# " + self.NAMES[1], "",
                "# [team_names]", "# " + self.NAMES[2])
        self.assertEqual(self.holding(self.NAMES, *text), ("split", (1, 4)))
        self.assertEqual(config_mod.held_in(
            self.NAMES, "".join(line + "\n" for line in text)), "split")
        # Each header is named by its own line in the file, counted from 1.
        self.assertEqual(self.holding(self.NAMES, "[club]", 'name = "Tennis"', "",
                                      "# [team_names]", "# " + self.NAMES[2], "",
                                      "# Kept apart to check.",
                                      '# ["team_names"]', "# " + self.NAMES[1]),
                         ("split", (4, 8)))

    def test_two_entries_of_an_array_are_never_one_table(self):
        item = ["[[competitions.tuesday-division-c.rounds]]", 'number = "1"',
                'date = "2026-07-21"']
        self.assertEqual(self.holding(item,
                                      "# [[competitions.tuesday-division-c.rounds]]",
                                      '# number = "1"',
                                      "# [[competitions.tuesday-division-c.rounds]]",
                                      '# date = "2026-07-21"'), ("absent", ()))

    def test_a_value_no_block_of_its_table_holds_leaves_it_not_in_the_file(self):
        names = self.NAMES + ['eastside-aces = "Eastside Aces"']
        for text in (("# [team_names]", "# " + names[1],
                      "# [team_names]", "# " + names[2]),
                     ("# [team_names]", "# " + names[1], "# [team_names]",
                      "# " + names[2], "# [player_names]", "# " + names[3]),
                     ("# [team_names]", "# " + names[1], "# [team_names]",
                      "# " + names[2], '# eastside-aces = "Eastside Ace"')):
            with self.subTest(text=text):
                self.assertEqual(self.holding(names, *text), ("absent", ()))
        # And an item of no lines is in no file, split or not, nor is an item one
        # of whose blocks is split and another in no block at all.
        self.assertEqual(self.holding([], "# [team_names]", "# " + names[1],
                                      "# [team_names]", "# " + names[2]),
                         ("absent", ()))
        self.assertEqual(self.holding(["[competitions.tuesday-division-c]",
                                       'season = "2026"'] + self.NAMES,
                                      "# [team_names]", "# " + names[1],
                                      "# [team_names]", "# " + names[2]),
                         ("absent", ()))

    def test_only_the_blocks_that_hold_a_part_of_it_are_named(self):
        self.assertEqual(self.holding(self.NAMES, "# [team_names]", "# " + self.NAMES[1],
                                      "# [team_names]", '# eastside-aces = "Eastside"',
                                      "# [team_names]", "# " + self.NAMES[2]),
                         ("split", (1, 5)))
        # Nor is a block that holds a key of it under another value: gathered with
        # the parts, that key would be in the table twice, which the loader refuses.
        self.assertEqual(self.holding(self.NAMES, "# [team_names]", "# " + self.NAMES[1],
                                      "# [team_names]", "# " + self.NAMES[2],
                                      "# [team_names]", '# northside-nets = "Nets"'),
                         ("split", (1, 3)))
        # A block of the item held whole by one block of the file is not split.
        for whole in (["[[competitions.tuesday-division-c.rounds]]", 'number = "1"'],
                      ["[competitions.tuesday-division-c]", 'season = "2026"']):
            with self.subTest(whole=whole[0]):
                self.assertEqual(self.holding(whole + self.NAMES,
                                              "# " + whole[0], "# " + whole[1],
                                              "# [team_names]", "# " + self.NAMES[1],
                                              "# [team_names]", "# " + self.NAMES[2]),
                                 ("split", (3, 5)))

    def test_one_block_that_holds_it_whole_is_commented_whatever_else_holds_a_part(self):
        self.assertEqual(self.holding(self.NAMES, "# [team_names]", "# " + self.NAMES[1],
                                      "# [team_names]", "# " + self.NAMES[1],
                                      "# " + self.NAMES[2]), ("commented", ()))

    def test_a_header_the_file_has_live_already_is_no_part_of_it(self):
        # Deleting the # of the second declares the table twice, as it does for
        # the item held whole under such a header, so neither is behind a #.
        self.assertEqual(self.holding(self.NAMES, "[team_names]", "# " + self.NAMES[1],
                                      "", "# [team_names]", "# " + self.NAMES[2]),
                         ("absent", ()))
        # And two of them hold nothing, though the live table has none of it.
        self.assertEqual(self.holding(self.NAMES, "[team_names]",
                                      'eastside-aces = "Eastside Aces"', "",
                                      "# [team_names]", "# " + self.NAMES[1], "",
                                      "# [team_names]", "# " + self.NAMES[2]),
                         ("absent", ()))
        # Nor is a block under no # at all, which has no header line to be named
        # by: measured, it was named as line 0, next to line 7. A [x] line in a
        # string is one, read as a header line by line and as a string by the loader.
        self.assertEqual(self.holding(self.NAMES, "[club]", 'name = """',
                                      "[team_names]", '"""', "# " + self.NAMES[1],
                                      "", "# [team_names]", "# " + self.NAMES[2]),
                         ("absent", ()))


class TestTheTeamListNoteIsToldWhatTheFileHoldsOfTheList(unittest.TestCase):
    """``teams_instruction``: the step the team-list note gives, worded from the file.

    Measured before this (/tmp/t7/scratch/CF1/probe/held-before.txt): of the texts a
    run can leave, ``held_in`` finds the team list ``absent`` in no file and in a
    config.toml written before any card was read, ``commented`` in the file a run
    writes from the cards, and ``live`` in a ``teams = []``. The note said "remove
    the # from" in all four, because it was worded by ``commented_teams`` from the
    list's width alone, so three of them sent a club to a # that is not there. It
    now asks ``held_in`` of the lines the review offers, and a run that writes the
    file asks it of the lines that run writes; a file without the list is told to
    add the table as well where the file has none (``opens``), as the review is.
    Rejected: ``Config.exists``, which a file written before any card passes as
    well as one written from the cards.
    """

    COMP = "tuesday-division-b"
    TABLE = "[competitions.tuesday-division-b]"
    NAMES = ["Hilltop Aces", "Lakeside Spinners", "Parkview Wanderers",
             "Riverside Rockets"]

    def said(self, text, path="config.toml"):
        return config_mod.teams_instruction(self.COMP, self.NAMES, path, text)

    def test_the_run_that_writes_the_file_is_told_of_the_lines_it_writes(self):
        _, uncomment = config_mod.commented_teams(self.NAMES)
        self.assertEqual(self.said(None), f"{uncomment} teams = [...] under "
                                          f"{self.TABLE} in config.toml")

    def test_a_file_holding_the_list_behind_a_hash_is_told_to_remove_it(self):
        block, uncomment = config_mod.commented_teams(self.NAMES)
        self.assertEqual(self.said("\n".join([self.TABLE, *block]) + "\n"),
                         f"{uncomment} teams = [...] under {self.TABLE} in "
                         f"config.toml")

    def test_a_file_without_the_table_is_told_to_add_the_table_and_the_list(self):
        # A table behind a # is not in the file, though its text is: measured,
        # asking whether the header's text is in the file sent this one to add
        # teams under a header the loader never reads.
        for text in ("", '[club]\nname = "Riverside Tennis"\n',
                     f'# {self.TABLE}\n# day = "Tuesday"\n'):
            with self.subTest(text=text):
                self.assertEqual(self.said(text), f"add {self.TABLE} to config.toml, "
                                                  f"and teams = [...] under it")

    def test_a_file_with_the_table_and_not_the_list_is_told_to_add_the_list(self):
        """The table the club has already, which a second header would break.

        Measured: ``opens`` is False for a file holding the table live with a day
        and no teams, and that file with a second header and the list under it
        does not parse, "Cannot declare ('competitions', 'tuesday-division-b')
        twice". The table quoted, ``[competitions."tuesday-division-b"]``, is the
        same table, which the header's text is not in (/tmp/t7/scratch/CF1-review/
        mut/m2probe.py). Rejected: one wording for both, which sent the club that
        has the table to add it, and the header's text looked for in the file.
        """
        for table in (self.TABLE, '[competitions."tuesday-division-b"]'):
            with self.subTest(table=table):
                self.assertEqual(self.said(f'{table}\nday = "Tuesday"\n'),
                                 f"add teams = [...] under {self.TABLE} in "
                                 f"config.toml")

    def test_a_live_line_that_lists_no_team_is_told_to_fill_it_in(self):
        self.assertEqual(self.said(f"{self.TABLE}\nteams = []\n"),
                         f"fill in teams under {self.TABLE} in config.toml: the "
                         f"line is there, and lists no team")

    def test_a_list_split_over_two_headers_is_told_to_gather_it_first(self):
        """``split``, which a one-key item cannot be, worded all the same.

        Measured: 50 ways of putting the written block under up to three ``# [x]``
        headers gave ``absent`` or ``commented`` and never ``split``, since
        ``_split_over`` splits an item by key and ``teams`` is one key. It is
        worded because every status ``held_in`` can answer has words wherever its
        answer is worded, so a second key in the item cannot reach a note that has
        none. Rejected: leaving it to fall through to "add", which tells a club
        holding the whole list to type it again.
        """
        _, uncomment = config_mod.commented_teams(self.NAMES)
        with mock.patch.object(config_mod, "held_in", return_value="split"):
            said = self.said("")
        self.assertEqual(said, f"gather teams = [...] under one # {self.TABLE} in "
                               f"config.toml, as a file declares a table once, and "
                               f"{uncomment} it")

    def test_a_list_behind_a_hash_under_a_header_behind_one_is_told_to_remove_both(self):
        """The header's # as well as the list's, or the list lands in another table.

        Measured (/tmp/t7/scratch/CF1-review/mut/probe3.py): a file holding the
        written block under ``# [competitions.tuesday-division-b]``, below a live
        ``[club]``, is ``commented``, and the note said to remove the # from the
        list's lines alone, which puts ``teams`` under ``[club]``. It now names the
        header too wherever ``opens`` says the table is not live, and the file with
        both #s taken out declares the list. Rejected: filing it beside 4.193, as
        the note is the step's one source now.
        """
        block, _ = config_mod.commented_teams(self.NAMES)
        text = "\n".join(['[club]', 'name = "Riverside Tennis"', "",
                          f"# {self.TABLE}", *block]) + "\n"
        self.assertEqual(self.said(text), f"remove the # from {self.TABLE} in "
                                          f"config.toml, and from every line of "
                                          f"teams = [...] under it")
        done = text.replace(f"# {self.TABLE}", self.TABLE)
        for line in block:
            done = done.replace(line, line.removeprefix("# "))
        loaded = tomllib.loads(done)
        self.assertEqual(loaded["competitions"]["tuesday-division-b"]["teams"],
                         self.NAMES)
        self.assertNotIn("teams", loaded["club"])

    def test_a_published_sentence_names_the_file_by_its_name_and_never_its_folder(self):
        for path in (Path("/srv/club files/other.toml"), "club files/other.toml",
                     "other.toml"):
            with self.subTest(path=path):
                self.assertEqual(config_mod.named(path), "other.toml")

    def test_the_note_names_the_file_as_it_was_handed_as_the_report_does(self):
        """The note is the report's, whose other notes give ``--config`` as typed.

        Measured (/tmp/t7/scratch/CF1-review/runs/rel-fixed/out.txt): with the
        note on ``config.named``, ``--config "club files/our.toml"`` had it say
        our.toml between notes saying club files/our.toml, of one file. Rejected:
        the name, which the notes around it do not give.
        """
        for path in (Path("/srv/club files/other.toml"), "club files/other.toml"):
            with self.subTest(path=path):
                self.assertTrue(self.said(None, path=path).endswith(
                    f"under {self.TABLE} in {path}"), self.said(None, path=path))


class TestAHeaderIsOfferedOnlyWhereTheLoaderTakesIt(unittest.TestCase):
    """``opens``: whether a ``[x]`` line can be added to a config file as it stands.

    Measured before this, on the pairs cards' own written file: the review said of
    their 8 players' spellings "not in it; add the lines below to adopt it", the
    lines began ``[player_names]``, which the file already had, and adding them as
    printed stopped the next run at exit 2, "Cannot declare ('player_names',)
    twice". The review asks the loader, by parsing the file with the header added,
    rather than keeping its own copy of TOML's rules, which differ by case: a
    table may follow its own subtable, and may not follow a dotted key that made
    it. Rejected: comparing the header's text with the file's lines, which misses
    ``[competitions."x"]`` against ``[competitions.x]`` and every dotted key.
    """

    def test_a_table_the_file_declares_cannot_be_opened_again(self):
        for header, text in (("[player_names]", "[player_names]\n"),
                             ("[competitions.x]", '[competitions."x"]\nlabel = "X"\n'),
                             ("[a.b]", "[a]\nb.x = 1\n"),
                             ("[a]", "a.x = 1\n")):
            with self.subTest(header=header, text=text):
                self.assertFalse(config_mod.opens(header, text))

    def test_any_other_table_can(self):
        for header, text in (("[player_names]", ""),
                             ("[a]", "[a.b]\nx = 1\n"),
                             ("[a]", "[[a.rounds]]\nnumber = 1\n"),
                             ("[[a.rounds]]", "[a]\n[[a.rounds]]\n"),
                             ("[competitions.x]", "# [competitions.x]\n")):
            with self.subTest(header=header, text=text):
                self.assertTrue(config_mod.opens(header, text))


class TestALongArrayIsBrokenAtTheItem(unittest.TestCase):
    """``toml_lines``: an assignment too wide for a terminal, split where it can be.

    The report offers these lines for a club to paste into ``config.toml``, so the
    only acceptable break is one that leaves the paste working. Breaking a
    ``teams`` list mid-name gives a club a file that will not parse, and the error
    it gets back names a line this program wrote.
    """

    WIDTH = 72

    # Short enough that any one of them fits inside the narrowest width swept
    # below, so the only thing the sweep can find is bad arithmetic.
    NAMES = ["Aces", "Bunyips", "Cormorants", "Dingoes", "Emus", "Falcons",
             "Gannets", "Herons", "Ibises", "Jacanas"]

    def lines(self, assignment: str, width: int | None = None) -> list[str]:
        out = config_mod.toml_lines(assignment,
                                   width=self.WIDTH if width is None else width)
        self.assertIsInstance(out, list)
        return out

    def parsed(self, out: list[str]) -> dict:
        """What TOML makes of the lines, which is the whole point of them."""
        return tomllib.loads("\n".join(out))

    def test_an_assignment_that_already_fits_is_returned_untouched(self):
        self.assertEqual(self.lines('teams = ["Aces", "Iguanas"]'),
                         ['teams = ["Aces", "Iguanas"]'])

    def test_a_long_list_is_split_and_every_line_fits(self):
        names = [f"Riverside Rockets {n}" for n in range(1, 7)]
        out = self.lines(f"teams = {config_mod.toml_value(names)}")
        self.assertGreater(len(out), 1, "a 170-column line was left as one line")
        for line in out:
            self.assertLessEqual(len(line), self.WIDTH, f"still too wide: {line}")

    def test_and_the_split_list_is_still_the_same_list(self):
        names = [f"Riverside Rockets {n}" for n in range(1, 7)]
        out = self.lines(f"teams = {config_mod.toml_value(names)}")
        self.assertEqual(self.parsed(out)["teams"], names)

    def test_a_list_of_lists_breaks_between_pairs_and_not_inside_one(self):
        # The draw. A fixture split down the middle would parse as two teams
        # playing nobody, which is the one wrong answer that still loads.
        draw = [["Aces", "Iguanas"], ["Rockets", "Spinners"],
                ["Wanderers", "Bunyips"], ["Quolls", "Magpies"]]
        out = self.lines(f"fixtures = {config_mod.toml_value(draw)}")
        self.assertEqual(self.parsed(out)["fixtures"],
                         [list(pair) for pair in draw])
        # The item lines only: the first and last carry the array's own brackets.
        for line in out[1:-1]:
            self.assertEqual(line.count("["), line.count("]"),
                             f"a fixture was split in half: {line}")

    def test_a_comma_inside_a_name_is_not_a_place_to_break(self):
        names = ["Rockets, Reserves", "Iguanas, Seconds", "Aces, Thirds",
                 "Spinners, Fourths"]
        out = self.lines(f"teams = {config_mod.toml_value(names)}")
        self.assertEqual(self.parsed(out)["teams"], names)

    def test_a_name_with_a_quote_in_it_survives_the_break(self):
        # One of them carries a single quote, not a pair, and a comma after it. An
        # even number of quotes hides the bug, because they still close each other;
        # so does an odd one with nothing after it, because a split that lands late
        # only makes a line long. It takes both to move a break inside a string.
        names = ['The "Real" Rockets', 'Aces "B, Reserves', "Iguanas", "Spinners",
                 "Wanderers", "Bunyips", "Quolls"]
        out = self.lines(f"teams = {config_mod.toml_value(names)}")
        self.assertEqual(self.parsed(out)["teams"], names)

    def test_one_item_wider_than_the_line_goes_on_its_own_line_whole(self):
        # Left over rather than chopped: a club can scroll, and cannot retype a
        # name this program cut in half.
        long = "R" * (self.WIDTH + 20)
        out = self.lines(f'teams = ["{long}", "Aces"]')
        self.assertEqual(self.parsed(out)["teams"], [long, "Aces"])
        self.assertTrue(any(long in line for line in out),
                        "the long name was broken up")

    def test_a_long_value_that_is_not_a_list_is_left_alone(self):
        # A TOML basic string cannot be continued on the next line, so there is
        # nothing honest to do with it. Returned whole, and the caller prints it
        # whole, rather than emitting something that does not parse.
        one = f'name = "{"Riverside " * 12}Rockets"'
        self.assertEqual(self.lines(one), [one])

    def test_a_table_header_is_left_alone(self):
        header = f"[competitions.{'very-long-division-name-' * 4}b]"
        self.assertEqual(self.lines(header), [header])

    def test_the_lines_it_returns_carry_no_indent_unless_one_is_asked_for(self):
        # The config file writes them at the margin, so no indent is the default.
        # Only the items are indented, relative to the key they belong to.
        names = [f"Riverside Rockets {n}" for n in range(1, 7)]
        out = self.lines(f"teams = {config_mod.toml_value(names)}")
        self.assertEqual(out[0], "teams = [")
        self.assertEqual(out[-1], "]")
        for line in out[1:-1]:
            self.assertTrue(line.startswith("    "),
                            f"an item line is not indented under its key: {line}")

    def test_the_indent_it_is_asked_for_is_on_every_line_it_returns(self):
        # The report prints these six columns in. It hands the indent over rather
        # than adding it afterwards, so every line has to come back with it --
        # including the bracket that closes the array, which would otherwise sit at
        # the margin and read as the end of the report's own section.
        names = [f"Riverside Rockets {n}" for n in range(1, 7)]
        out = config_mod.toml_lines(f"teams = {config_mod.toml_value(names)}",
                                   width=self.WIDTH, indent=" " * 6)
        self.assertEqual(out[0], "      teams = [")
        self.assertEqual(out[-1], "      ]")
        for line in out[1:-1]:
            self.assertTrue(line.startswith(" " * 10) and line[10] != " ",
                            f"an item line is not under its key by four: {line}")

    def test_every_width_it_is_given_is_a_width_it_keeps(self):
        # Swept rather than sampled. An indent left out of the arithmetic shows up
        # only at the widths where a line would land within it, and those are a
        # handful of columns out of eighty: the fixture this program ships happens
        # to miss all of them, so the first version of this could lose the four
        # columns of an item's indent and still look right.
        assignment = f"teams = {config_mod.toml_value(self.NAMES)}"
        for width in range(24, len(assignment) + 8):
            for indent in ("", " " * 6):
                with self.subTest(width=width, indent=len(indent)):
                    out = config_mod.toml_lines(assignment, width=width,
                                                indent=indent)
                    for line in out:
                        self.assertLessEqual(len(line), width, f"too wide: {line}")
                    # Indented and still parsing: TOML allows leading whitespace
                    # before a key, so what the report prints is what a club pastes.
                    self.assertEqual(self.parsed(out)["teams"], self.NAMES)


class TestTheFileItWritesFitsTheWidthItPrintsIn(unittest.TestCase):
    """``config.toml`` is written by the program that declares a width, and ignored it.

    Six lines of the demo season's file were over, the widest 165 columns: the commented
    ``format`` line, which listed all ten match shapes in a trailing comment after the
    value. Two more were option descriptions in the ``[rules]`` block -- the three tables
    a club reads to choose a scoring preset, which is the setting the report says on every
    run is the one that reorders a ladder if it is wrong. One was the commented ``teams``
    array, one a line of the header's own prose.

    The width is the report's own, and deliberately: several of these lines are printed
    twice, once into this file and once by the review section through ``toml_lines``, and a
    file wrapped to some other number would break the same team list in two places in the
    two places a club reads it.

    What the fit costs is in here too. A ``teams`` array that no longer fits on one line is
    commented on all four of its lines, so adopting it means deleting four ``#`` rather
    than one -- and deleting one of the four leaves a file that will not load. The prose
    above it says which of those two a club is looking at.
    """

    NAMES = ["Hilltop Aces", "Lakeside Spinners", "Parkview Wanderers",
             "Riverside Rockets"]

    def setUp(self):
        self.blank = config_mod.Config(path=Path("config.toml"))
        self.comp = make_competition([slugs.team_slug(name)
                                      for name in self.NAMES])

    def render(self, config=None, comps=None, **kw) -> str:
        return config_mod.render(self.blank if config is None else config,
                                 competitions=[self.comp] if comps is None
                                 else comps, **kw)

    def over(self, text: str) -> list[str]:
        return [line for line in text.splitlines()
                if len(line) > config_mod.WIDTH]

    def flowed(self, text: str) -> str:
        """The file with every comment continuation joined back on to its own line.

        So that a description this program wrapped to fit can be checked for in the
        words it was written in: a fit is only a fit if nothing was cut to get it.
        """
        return " ".join(line.lstrip("#").strip() for line in text.splitlines())

    def teams_block(self, text: str) -> list[str]:
        """Every line of the ``teams`` array, however many it takes."""
        lines = text.splitlines()
        start = next(i for i, line in enumerate(lines)
                     if line.lstrip("# ").startswith("teams = "))
        if not lines[start].rstrip().endswith("["):
            return [lines[start]]
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i].lstrip("# ").strip() == "]")
        return lines[start:end + 1]

    def test_no_line_of_the_file_a_first_run_writes_is_over_the_width(self):
        self.assertEqual(self.over(self.render(club_name="Riverside Tennis Club",
                                               cards=6)), [])

    def test_including_the_option_tables_a_club_chooses_a_preset_from(self):
        # Wrapped, not shortened. These sentences are the only place the file says what
        # a preset does, and the scoring one is the setting the report reprints on every
        # run because getting it wrong reorders the ladder.
        blob = self.flowed(self.render())
        for name, rules in sorted(scoring.RULESETS.items()):
            with self.subTest(scoring=name):
                self.assertIn(rules.describe(), blob)
        for name, rule in sorted(fmt_mod.SET_RULES.items()):
            with self.subTest(sets=name):
                self.assertIn(rule.describe(), blob)
        for mode in scoring.UNPLAYED_MODES:
            with self.subTest(unplayed=mode):
                self.assertIn(
                    scoring.unplayed_detail(mode == scoring.UNPLAYED_AVERAGE), blob)

    def test_a_wrapped_description_cannot_be_read_as_another_option(self):
        # The same rule the report's notes follow. These tables are a column of names
        # with a sentence beside each, and a sentence that comes back to the names'
        # column reads as a fifth preset with no name -- which, in the one table a club
        # has to choose from correctly, is worse than a line running off the edge.
        lines = self.render().splitlines()
        rows = [i for i, line in enumerate(lines)
                if re.match(r"#   \S", line) and " = " not in line]
        self.assertGreaterEqual(len(rows), len(scoring.RULESETS),
                                "the option tables are no longer written as rows, so "
                                "this test has stopped reading the file it is for")
        wrapped = 0
        for i in rows:
            name = lines[i][4:].split()[0]
            column = lines[i].index(name) + len(name)
            for line in lines[i + 1:]:
                # A continuation is the only thing indented further than a row's own
                # name column; a row puts its name at column four, and every other
                # comment in the file sits one space in.
                if not re.match(r"#    +\S", line):
                    break
                wrapped += 1
                self.assertTrue(line[1:].strip() and line[1:column].isspace(),
                                f"a description wrapped back to the name column: "
                                f"{line}")
        self.assertGreater(wrapped, 0, "nothing in the option tables wrapped, so this "
                                       "test is measuring nothing")

    def test_and_every_match_shape_it_offers_is_still_named(self):
        # These used to sit in a trailing comment on the `format` line, which is how it
        # reached 165 columns. Wherever they are written now, all ten have to be there:
        # the value is a name a club has to spell exactly.
        blob = self.flowed(self.render())
        for name in sorted(fmt_mod.FORMATS):
            with self.subTest(format=name):
                self.assertIn(name, blob)

    def test_a_club_whose_team_names_are_long_gets_the_same_promise(self):
        # Long enough that the team list, the draw example and the byes block all have
        # to fold, and no longer: one item too long to break is left whole on purpose
        # (`test_one_item_wider_than_the_line_goes_on_its_own_line_whole` above), and a
        # fixture built to trip that exception would be testing the exception.
        long = [f"{word} District Association" for word in
                ("Northern", "Southern", "Eastern", "Western", "Central")]
        team_slugs = [slugs.team_slug(name) for name in long]
        comp = make_competition(team_slugs)
        # An odd number of teams gives one of them a bye every round, so a long season
        # writes that team a long list -- which is the only way the byes block reaches
        # the margin, and it was the last line of the file still over it.
        comp = comp.with_rounds({
            str(number): Round(number=str(number), fixtures=(tuple(team_slugs[1:3]),),
                               byes=(team_slugs[0],))
            for number in range(1, 13)})
        text = self.render(comps=[comp],
                           club_name="Northern District Association Incorporated")
        self.assertIn("Already worked out", text, "this fixture no longer has a bye "
                                                  "to write, so it is not measuring "
                                                  "the byes block")
        self.assertEqual(self.over(text), [])

    def test_the_file_still_parses_after_every_break_taken_in_it(self):
        # The only break worth taking is one TOML allows. A `teams` array split down the
        # middle of a name loads as nothing at all, and the error names a line this
        # program wrote.
        text = self.render(club_name="Riverside Tennis Club", cards=6)
        tomllib.loads(text)

    def test_the_team_list_it_cannot_fit_is_commented_on_every_line(self):
        block = self.teams_block(self.render())
        self.assertGreater(len(block), 1, "this fixture's team list fits on one line, "
                                          "so it is testing nothing")
        for line in block:
            self.assertTrue(line.startswith("#"),
                            f"a line of the commented team list is live TOML: {line}")

    def test_and_deleting_those_hashes_declares_the_list(self):
        # The whole point of writing it commented is that a club can adopt it. Four
        # lines to uncomment instead of one is only acceptable if uncommenting them
        # works, so this does it the way a club would and loads the result.
        import tempfile
        text = self.render()
        block = self.teams_block(text)
        live = text
        for line in block:
            live = live.replace(line, line[2:] if line.startswith("# ")
                                else line.lstrip("#"), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(live, encoding="utf-8")
            loaded = config_mod.load(path)
        self.assertEqual(loaded.warnings, ())
        comp = loaded.competition(self.comp.id)
        self.assertTrue(comp.teams_declared)
        self.assertEqual(sorted(comp.teams), sorted(self.NAMES))

    def test_the_instruction_says_how_many_hashes_that_is(self):
        # A club reading "remove the #" in front of four commented lines has been told
        # something that does not work: one of the four leaves `teams = [` on its own.
        wrapped = self.render()
        self.assertIn("# from every line", wrapped)
        short = self.render(comps=[make_competition(["aces", "iguanas"])])
        self.assertEqual(len(self.teams_block(short)), 1,
                         "two short team names no longer fit on one line")
        self.assertNotIn("# from every line", short)

    def test_the_width_is_the_one_the_report_declares(self):
        # Two constants, because a package module cannot import the script that runs
        # it, and one number: the review section prints these same lines at that width.
        import build as build_mod
        self.assertEqual(config_mod.WIDTH, build_mod.WIDTH)


class TestNoClubSpecificsLeaked(unittest.TestCase):
    """This project is published for any club. The predecessor's was not."""

    SOURCES = sorted((ROOT / "matchcentre").glob("*.py"))

    # Everything that gets published, not just the engine: a club name in the
    # README or in the dashboard's own JavaScript is more visible than one in a
    # docstring, not less. `notes/` is deliberately excluded -- it is gitignored,
    # and it is where the predecessor's specifics are allowed to be discussed.
    # `tests/` is *not* skipped: it is published like everything else, and a
    # fixture is the likeliest place for a real player's name to survive. `data/`
    # and `site/` are a club's own inputs and generated output, neither tracked,
    # and so is `reports/`, what tools/scheduled.py kept of each build, and so are
    # the `public/` and `reports-public/` of the README's copy without names,
    # and `dist/`, a release's archive and its notes, a changelog section.
    SKIP_DIRS = {".git", "__pycache__", "notes", "data", "site", "reports",
                 "public", "reports-public", "dist"}
    # `.yml` covers `.github/`: the issue templates quote example names, and an
    # example is exactly where a real one gets copied in by accident.
    SHIPPED_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".toml", ".yml"}

    @classmethod
    def shipped_files(cls):
        # In a work tree, the files git tracks and the ones it would add, and not
        # whatever else the folder holds (4.524). A copy that has built a
        # dashboard for a club these terms name has that club's config.toml in
        # it, gitignored as it should be, and the scan read it: measured on a
        # clone whose config.toml names an invented club, with the terms
        # patched to that name, 1 club-specific reference
        # (/tmp/mcf/ub/scratch/before-leakscan.txt). Outside a work tree, as in
        # an unzipped release, every file. Rejected: config.toml left out by
        # name, which still reads public.toml, the proposals and any file
        # --config names.
        from test_project_complete import files_git_would_ship
        paths = files_git_would_ship(ROOT)
        if paths is None:
            paths = ROOT.rglob("*")
        for path in sorted(paths):
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
            text = path.read_text(encoding="utf-8")
            # Folded whole, a file holds every term any of its lines does, so a
            # term it does not hold is not asked of each line.
            whole = slugs.fold(text)
            present = [term for term in banned if term in whole]
            if not present:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                folded = slugs.fold(line)
                for term in present:
                    if term in folded:
                        where = (path.relative_to(ROOT)
                                 if path.is_relative_to(ROOT) else path.name)
                        hits.append(f"  {where}:{number}: {term!r} in "
                                    f"{line.strip()[:90]}")
        if hits:
            self.fail(f"{len(hits)} club-specific reference(s):\n"
                      + "\n".join(hits))

    @staticmethod
    def forms(terms):
        """Each term as :meth:`assert_absent` reads a line, and as its slug.

        Folded (:func:`~matchcentre.slugs.fold`), accents set aside with the case,
        because a casefold alone passed a banned name written with an accent its
        listed spelling has not, and as a slug, because the same name sat beside
        it as one (4.470).
        """
        found = set()
        for term in terms:
            found.add(slugs.fold(term))
            try:
                found.add(slugs.slugify(term))
            except ValueError:
                pass            # no letter or digit: nothing a slug could spell
        return sorted(found)

    # The strings that must not appear anywhere published: the club this project
    # was rewritten away from, and the real players named in its config. They are
    # read from a gitignored file rather than written here, because a test that
    # lists them publishes them -- which is the exact thing it exists to prevent.
    # A fresh clone has no such file and no such history, so the check skips.
    LEAK_TERMS = ROOT / "notes" / "leak-terms.txt"

    def banned_terms(self):
        if not self.LEAK_TERMS.is_file():
            self.skipTest(f"{self.LEAK_TERMS.name} absent: nothing to leak")
        return self.forms(
            line.strip()
            for line in self.LEAK_TERMS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#"))

    def test_a_banned_name_is_found_with_an_accent_or_as_a_slug(self):
        # Fictional. 0.1.0 casefolded the line alone: a banned name was published
        # in a slug example with an accent added, which no casefold takes off, and
        # its slug beside it (4.470).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.py"
            path.write_text('slugify("Odile Écrin")\n'
                            '"odile-ecrin"\n'
                            'ZELIE MARCHETTI\n'
                            'Odile and Écrin\n', encoding="utf-8")
            with self.assertRaises(AssertionError) as caught:
                self.assert_absent(self.forms(["Odile Ecrin", "Zélie Marchetti"]),
                                   [path])
        self.assertEqual(str(caught.exception).splitlines(), [
            "3 club-specific reference(s):",
            "  example.py:1: 'odile ecrin' in slugify(\"Odile Écrin\")",
            "  example.py:2: 'odile-ecrin' in \"odile-ecrin\"",
            "  example.py:3: 'zelie marchetti' in ZELIE MARCHETTI"])

    def planted(self, root: Path, *, git: bool) -> list[str]:
        """What :meth:`shipped_files` reads of a planted project at *root*: a
        README.md and a build.py, and a config.toml and a public.toml that each
        name an invented club, where .gitignore names only the first. In a git
        work tree of its own if *git*, with the first two committed.
        """
        from test_project_complete import git_in
        (root / ".gitignore").write_text("/config.toml\n", encoding="utf-8")
        for name in ("README.md", "build.py", "config.toml", "public.toml"):
            (root / name).write_text('name = "Wattle Hollow Tennis Club"\n',
                                     encoding="utf-8")
        if git:
            git_in(root, "init", "-q")
            git_in(root, "add", "README.md", "build.py")
            git_in(root, "commit", "-q", "-m", "A planted project")
        with mock.patch.dict(globals(), ROOT=root):
            return [path.relative_to(root).as_posix()
                    for path in self.shipped_files()]

    def test_a_file_git_ignores_is_not_read_and_one_it_would_add_is(self):
        # Fictional. The config a build writes is not what a clone receives, and
        # a public.toml the rules forgot is, as are the files committed (4.524).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.planted(Path(tmp), git=True),
                             ["README.md", "build.py", "public.toml"])

    def test_outside_a_work_tree_every_file_is_read(self):
        # An unzipped release: no git to ask, so the folder is what ships.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.planted(Path(tmp), git=False),
                             ["README.md", "build.py", "config.toml", "public.toml"])

    def test_inside_a_checkout_that_ignores_it_every_file_is_read_too(self):
        # A release unzipped into a home folder kept in git, whose .gitignore is
        # "*": git would add nothing here, so asked alone it read nothing, and
        # the scan passed on no file at all (/tmp/mcf/ub/scratch/fail-first.txt).
        import tempfile
        from test_project_complete import git_in
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".gitignore").write_text("*\n", encoding="utf-8")
            git_in(Path(tmp), "init", "-q")
            root = Path(tmp) / "matchcentre-dashboard"
            root.mkdir()
            self.assertEqual(self.planted(root, git=False),
                             ["README.md", "build.py", "config.toml", "public.toml"])

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
