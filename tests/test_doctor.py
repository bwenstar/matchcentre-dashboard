"""``tools/doctor.py --redact`` — the report a club can paste in public.

The doctor exists for one situation: a card that will not read, on a machine that
is not the maintainer's. Diagnosing it needs the page's measurements -- the column
rules, the row bands, the checkbox census, which header fields were found -- and
none of that names anybody. Getting those measurements to a maintainer, though,
normally means attaching the PDF to a public issue, and a scorecard names both
teams, every player who took a court, their ratings and the two captains. A tool
whose only support route publishes a club's membership is a tool a club is right to
stop using.

So ``--redact`` prints the same report with every name, team, club, venue, date and
path replaced by its shape: ``Priya Halloran`` becomes ``<2 words, 15 chars>``.
This file holds it to both halves of that promise, because either half failing
makes the flag useless:

**Nothing off the card survives.** Checked against a card whose every value is
written here in this file, so the check knows what was on the page independently of
what the redactor thought it learned. A hand-written list is the only kind that can
catch a value the redactor never learned about, which is exactly the failure mode
that matters.

**Everything that is not a name does survive.** A report that shaped the geometry
too would be safe and useless, and the club would send the PDF instead. The
measurements, the scores, the counts, the cross-check verdicts, the template's own
vocabulary and the weekday are all asserted present -- the weekday because "why did
my Tuesday card land in the Wednesday division" is the commonest real question this
tool is asked, and the answer is unreadable without it.

The exemptions are deliberate and are listed as such below. Nothing here is a
guess at what redaction ought to do; it is what it does, written down so that a
change in either direction has to be argued for.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matchcentre import cardwriter as cw, pdf, scorecard             # noqa: E402

DOCTOR = ROOT / "tools" / "doctor.py"
TIMEOUT = 300

# The card, written out here rather than imported from another fixture, because
# this file's whole method is knowing what was on the page without asking the code
# that redacts it. Every name is invented and every one is distinctive enough that
# a stray occurrence in the report cannot be a coincidence: no real word of the
# Match Centre template, and nothing that could be mistaken for a measurement.
CLUB = ("QUILLBOROUGH LAWN TENNIS", "ASSOCIATION INCORPORATED")
VENUE = "Marrowfield Gardens"
HOME_TEAM = "QUILLBOROUGH NIGHTJARS"
AWAY_TEAM = "TARLOW HEATH KESTRELS"
HOME_PLAYERS = ("Priya Halloran", "Desmond Okwuosa")
AWAY_PLAYERS = ("Yusra Benitez", "Marcelo Fenwick")
DATE = "2026-08-18"                 # a Tuesday, which is the routing fact
START_TIME = "7:30 PM"
MATCH_ID = "4200731"
DIVISION = "G2"
ROUND = "4"

# The rating pairs as the card prints them, `[2.34/4.23]`. Asserted as the printed
# pair rather than as single numbers: one number with two decimals could in
# principle also be a coordinate in the geometry, and shaping the geometry is what
# this file is here to prevent.
RATINGS = ((2.34, 4.23), (3.11, 3.98), (2.87, 4.01), (3.45, 3.22))

# Written under a name of the kind Match Centre gives its exports -- the two teams
# -- because the file's own path is printed as `source:` and is therefore one of
# the things that has to be shaped.
FILENAME = "Nightjars-Kestrels.pdf"


def _spec() -> cw.CardSpec:
    rows = (
        cw.SetRow(home_games=6, away_games=1, doubles=True,
                  home_players=HOME_PLAYERS, away_players=AWAY_PLAYERS),
        cw.SetRow(home_games=4, away_games=6, doubles=False,
                  home_players=HOME_PLAYERS[:1], away_players=AWAY_PLAYERS[:1]),
        cw.SetRow(home_games=6, away_games=3, doubles=False,
                  home_players=HOME_PLAYERS[1:], away_players=AWAY_PLAYERS[1:]),
        cw.SetRow(home_games=6, away_games=2, doubles=True,
                  home_players=HOME_PLAYERS, away_players=AWAY_PLAYERS),
    )
    roster = tuple(cw.RosterRow(name=name, singles=singles, doubles=doubles,
                                captain=(index == 0))
                   for index, (name, (singles, doubles))
                   in enumerate(zip(HOME_PLAYERS + AWAY_PLAYERS, RATINGS)))
    return cw.CardSpec(
        club_lines=CLUB, venue=VENUE, court="07", division=DIVISION,
        round_label=ROUND, match_id=MATCH_ID, date=DATE, start_time=START_TIME,
        home_name=HOME_TEAM, away_name=AWAY_TEAM, rows=rows,
        home_roster=roster[:2], away_roster=roster[2:])


class DoctorCase(unittest.TestCase):
    """One genuine card, read by the doctor both ways, once for the class."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        # Two directories deep and named after the fixture, the way a club's own
        # export lands: `--redact` has to shape the folder names as well, since a
        # round folder is often named after the division and a team.
        folder = Path(cls._tmp.name) / "data" / "Round4-Nightjars"
        folder.mkdir(parents=True)
        cls.card = cw.write_card_file(folder / FILENAME, _spec())
        cls.plain = cls.run_doctor(str(cls.card))
        cls.redacted = cls.run_doctor("--redact", str(cls.card))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def run_doctor(cls, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run((sys.executable, str(DOCTOR), *argv),
                              capture_output=True, text=True, timeout=TIMEOUT)

    @property
    def report(self) -> str:
        return self.redacted.stdout + self.redacted.stderr

    def assert_absent(self, value: str, why: str):
        """``value`` nowhere in the redacted report, case folded.

        Case folded because the report prints some of what it read in its own
        casing -- a team name reaches the verdict block through
        :func:`str.title` -- and a leak in a different case is still a leak.
        """
        self.assertNotIn(value.casefold(), self.report.casefold(),
                         f"--redact published {value!r}, which is {why}")


class TestTheCardItReadsIsAGenuineOne(DoctorCase):
    """The pre-flight. A card that did not read would pass everything below."""

    def test_the_page_parses_as_a_scorecard(self):
        cards, rejections = scorecard.read(self.card)
        self.assertEqual(rejections, [])
        self.assertEqual(len(cards), 1)

    def test_the_names_this_file_claims_are_the_names_on_the_page(self):
        # Guards the method rather than the code: every assertion below is a
        # string written at the top of this file, and if the generator stopped
        # printing one of them the assertion would pass by looking for something
        # that was never there.
        card, = scorecard.read(self.card)[0]
        self.assertEqual(card.home_name, HOME_TEAM)
        self.assertEqual(card.away_name, AWAY_TEAM)
        self.assertEqual(card.date, DATE)
        self.assertEqual(card.match_id, MATCH_ID)
        roster = [entry.name for entry in card.home_roster + card.away_roster]
        self.assertEqual(roster, list(HOME_PLAYERS + AWAY_PLAYERS))

    def test_both_runs_succeeded(self):
        for label, done in (("plain", self.plain), ("redacted", self.redacted)):
            with self.subTest(run=label):
                self.assertEqual(done.returncode, 0,
                                 done.stdout + done.stderr)

    def test_the_unredacted_report_does_name_people(self):
        # The default has to stay useful: this is the report a club reads on its
        # own machine, about its own members, and shaping it there would be
        # security theatre paid for in legibility. It is also what makes the
        # assertions below meaningful -- both reports are of the same page, so
        # every name absent from one and present in the other was removed.
        printed = self.plain.stdout + self.plain.stderr
        for name in HOME_PLAYERS + AWAY_PLAYERS:
            with self.subTest(name=name):
                self.assertIn(name, printed)
        self.assertIn(HOME_TEAM, printed)


class TestNothingOffTheCardSurvivesRedaction(DoctorCase):

    def test_no_players_name_survives(self):
        for name in HOME_PLAYERS + AWAY_PLAYERS:
            with self.subTest(name=name):
                self.assert_absent(name, "a player's name")

    def test_no_half_of_a_players_name_survives_either(self):
        # The half that matters. A surname alone identifies somebody in a club of
        # two hundred, and shaping "Priya Halloran" while leaving a stray
        # "Halloran" in a warning line would look redacted and not be.
        for name in HOME_PLAYERS + AWAY_PLAYERS:
            for part in name.split():
                with self.subTest(part=part):
                    self.assert_absent(part, "part of a player's name")

    def test_neither_team_survives_whole_or_in_part(self):
        for team in (HOME_TEAM, AWAY_TEAM):
            with self.subTest(team=team):
                self.assert_absent(team, "a team name")
            for word in team.split():
                if word == "TENNIS":
                    continue            # in TEMPLATE_WORDS; see the class below
                with self.subTest(word=word):
                    self.assert_absent(word, "a word of a team name")

    def test_the_club_and_the_venue_do_not_survive(self):
        # The club identifies the competition even with every player shaped, and
        # the venue is often the club's own address in all but name.
        for line in CLUB:
            for word in line.split():
                if word == "TENNIS":
                    continue
                with self.subTest(word=word):
                    self.assert_absent(word, "part of the club's name")
        for word in VENUE.split():
            with self.subTest(word=word):
                self.assert_absent(word, "part of the venue")

    def test_the_date_survives_in_no_form_it_was_printed_or_derived_in(self):
        # Three forms: the ISO date the reader derives, the dd/mm/yyyy the card
        # prints, and the bare year. A date plus a division is enough to find one
        # fixture in a public draw, and from there the two team names.
        #
        # One honest limit, found by deleting each of the redactor's defences in
        # turn and re-running this file. The dd/mm/yyyy pattern is load-bearing:
        # remove it and the printed date leaks past this test, because a date has
        # no letters in it and the word pass therefore never learns it. The ISO
        # pattern is not, and no test here can be made to fail by removing it -- on
        # a card that parses, ``learn_card`` records the same string outright, and
        # on a card that does not parse the report has no derived date to print.
        # It is kept as defence in depth for a future report line, not because
        # anything below proves it works. Said out loud so that nobody reads a
        # green suite as evidence that it does.
        for form in (DATE, "18/08/2026", "2026"):
            with self.subTest(form=form):
                self.assert_absent(form, "the date of the match")

    def test_the_match_id_does_not_survive(self):
        # The id is a key into a public database: with it, everything shaped in
        # this report can be looked up in full.
        self.assert_absent(MATCH_ID, "the id of this fixture")

    def test_the_start_time_does_not_survive(self):
        self.assert_absent("7:30", "the start time")

    def test_the_ratings_do_not_survive_as_numbers(self):
        for singles, doubles in RATINGS:
            with self.subTest(pair=(singles, doubles)):
                self.assert_absent(f"{singles}/{doubles}",
                                   "a player's rating pair")

    def test_the_files_own_path_does_not_survive(self):
        # `source:` prints the path, and the path is the leakiest thing here: a
        # Match Centre export is named after the two teams, and it usually sits in
        # a folder named after the round, the division or the club.
        self.assert_absent(FILENAME, "the name of the file")
        self.assert_absent(Path(FILENAME).stem, "the name of the file")
        self.assert_absent(str(self.card), "the path of the file")
        self.assert_absent(self.card.parent.name, "the folder the card is in")

    def test_no_word_the_page_draws_survives_unless_the_template_prints_it(self):
        # The sweep, next to the hand-written list rather than instead of it. This
        # one cannot catch a value the redactor failed to learn -- it asks the same
        # question the redactor asked -- but it does catch the other half: a value
        # learned and then printed by a line that is composed after the scrub, or a
        # word the shaping rules quietly skip.
        #
        # Matched whole and case-sensitively, which is a real limitation and worth
        # stating: a leak in a different case escapes this test, which is why the
        # names, teams, club and venue above are checked case-folded by hand. Both
        # relaxations were forced by measurement, and all three near-misses were
        # the report printing a word of its own rather than a value of the card's:
        # the page draws `Tue` and the report deliberately prints `Tuesday`; it
        # draws `S1` and the report's derived format line says `d1-s1-s2-d2`; it
        # draws `Club` and the report has a label called `club hint`.
        from importlib import util
        spec = util.spec_from_file_location("doctor_module", DOCTOR)
        doctor = util.module_from_spec(spec)
        spec.loader.exec_module(doctor)

        leaked = []
        for word in pdf.extract(self.card).words:
            for token in word.text.split():
                candidate = token.strip("'\"()[]{},.:;!?-_ ")
                if len(candidate) < 2 or not any(c.isalpha() for c in candidate):
                    continue
                if candidate.casefold() in doctor.TEMPLATE_WORDS:
                    continue
                if re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", self.report):
                    leaked.append(candidate)
        self.assertEqual(sorted(set(leaked)), [],
                         f"{len(set(leaked))} word(s) the page draws are in the "
                         f"redacted report and are not template vocabulary: "
                         f"{sorted(set(leaked))}")


class TestEverythingThatIsNotAboutAPersonIsKept(DoctorCase):
    """The other half. A safe report nobody can act on is not a support route.

    Each of these is an exemption, and each is deliberate. Together they are the
    reason a redacted report is worth asking for at all: the whole diagnosis lives
    in the geometry, and the geometry is the half that names nobody.
    """

    def test_the_pages_geometry_is_intact(self):
        for measurement in ("595", "842", "A4 portrait", "column rules",
                            "row bands", "checkbox"):
            with self.subTest(measurement=measurement):
                self.assertIn(measurement, self.report)

    def test_the_set_scores_and_totals_are_intact(self):
        # A score is not a name. Withholding the scores would remove the one thing
        # that says whether the page was read correctly.
        for score in ("6-1", "4-6", "6-3", "6-2"):
            with self.subTest(score=score):
                self.assertIn(score, self.report)
        self.assertIn("games 22-12", self.report)
        self.assertIn("sets 3-1", self.report)

    def test_the_cross_checks_still_report_their_verdicts(self):
        for phrase in ("cross-check totals", "cross-check verb",
                       "cross-check winner-tick", "agrees"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.report)

    def test_the_weekday_is_kept_because_it_is_what_routes_the_card(self):
        # The single commonest real question -- "why is my Tuesday card in the
        # Wednesday division" -- is unanswerable without it, and no weekday names
        # a person. The date itself is withheld regardless.
        self.assertIn("Tuesday", self.report)

    def test_the_templates_own_vocabulary_is_kept(self):
        # These words are on every card whoever played, so shaping them would hide
        # the structure of the page and identify nobody by keeping them. The cost
        # is stated where the list is declared: a club whose team really is called
        # one of these words is not shaped.
        for word in ("TOTAL", "SET", "Match Winner", "Rating", "Fill-ins",
                     "DEFEATS", "Signed by"):
            with self.subTest(word=word):
                self.assertIn(word, self.report)

    def test_the_round_and_the_format_are_kept(self):
        # A round number and a match shape are facts about the competition, not
        # about anybody in it, and the format is what a mis-set `format` in
        # config.toml is diagnosed from.
        self.assertRegex(self.report, r"round\s+'?4'?")
        self.assertIn("dssd", self.report)

    def test_what_was_withheld_says_what_kind_of_thing_it_was(self):
        # Shapes, not deletions. "<2 words, 15 chars>" says a two-word name was
        # read, which is what distinguishes a card whose roster was read from one
        # whose roster was missed -- and that distinction is often the whole
        # diagnosis. `<date>`, `<time>` and `<7 digits>` do the same for the
        # values the reader derives rather than reads.
        for shape in ("<2 words,", "chars>", "<date>", "<time>", "<7 digits>",
                      "[x.xx/x.xx]"):
            with self.subTest(shape=shape):
                self.assertIn(shape, self.report)

    def test_the_captain_mark_is_kept_beside_the_shape_of_the_name(self):
        # `<2 words, 14 chars> (C)` says a captain was read without saying who.
        self.assertRegex(self.report, r"<\d+ words, \d+ chars> \(C\)")

    def test_redacting_reaches_the_same_verdict(self):
        # --redact is about what is printed, not about what is concluded. If the
        # two runs could differ, a club would have to send the unredacted report
        # to be believed.
        self.assertEqual(self.plain.returncode, self.redacted.returncode)
        for run in (self.plain.stdout, self.redacted.stdout):
            self.assertIn("This page reads as a scorecard.", run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
