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

import ast
import contextlib
import dataclasses
import importlib
import io
import itertools
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from matchcentre import cardwriter as cw, discover, pdf, redact, scorecard  # noqa: E402
from matchcentre import cli, validate                                 # noqa: E402
from matchcentre.card import DATE_FORMS, verb_side, weekday_of      # noqa: E402
import doctor                                                        # noqa: E402
from test_hostile_input import (OTHER_TEMPLATE_LABELS,               # noqa: E402
                                box_edges_unpaired, box_parts_outlined,
                                boxes_moved, drawn_again, fewer_boxes,
                                relabelled, word_lost, written_with_a_box_more)
from test_hostile_input import _PAINT_RECT, _in_stroked_lines        # noqa: E402
from test_hostile_input import _repainted                            # noqa: E402
from test_parsing import _GREY, _inline, _typed, _with_an_image      # noqa: E402
from test_cli import past_the_width                                  # noqa: E402
from test_pipeline import cards_in_one_file                          # noqa: E402
from test_project_complete import python_sources                     # noqa: E402

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
        # export lands: a round folder is often named after the division and a
        # team, so `--redact` has to withhold the path, which it does whole.
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


class TestTheHeaderFieldsAreFoundOnEveryCardTheReaderPlaces(unittest.TestCase):
    """The checker called a division missing on a card the reader had just placed.

    Its header fields were found with patterns of its own, meant to be more
    forgiving than the reader's, and they knew `Division`, `Grade`, `Section` and
    `Round` followed by a space. Measured once the reader took a colon, `Rd`, `Rnd`
    and `C Grade`: a card printing `Division: C / Yellow Ball / Round: 1 / #3665611`
    was reported as `division not found` and `round not found`, above a verdict
    reading division `C` and round `1`; `C Grade / Yellow Ball / Round 4` as
    `division not found`; and `C Grade Round 4` as division `Round`, which is what
    the reader itself read before it took `C Grade`. When the colon card was
    refused for a column rule it had lost, the likeliest cause given was that "the
    header line is missing division, round", which sends a club to the one line
    that was fine. The same card in the plain wording was not blamed on its header.

    So the division and the round are looked for with the reader's own rule, on
    every line and the long forms before the short ones, as the reader prefers
    them, and the checker's pattern is tried only when the rule finds nothing.
    Rejected: widening the two patterns to match, a second list of wordings to keep
    in step with the reader's; the pattern tried first and the rule after it, which
    still says `Round`; and the rule with the short forms first, which reports the
    round of a card whose identity is split over two lines as `01`, off a
    letterhead's `14 Example Rd` beside the court number, where the reader reads
    `1` off the line that says `Round 1`.
    """

    CARDS = {
        "colon": ("Division: C / Yellow Ball / Round: 1 / #3665611", {}),
        "grade": ("C Grade / Yellow Ball / Round 4 / #3665611", {}),
        "run-on": ("C Grade Round 4 / #3665611", {}),
        "capitals": ("DIVISION C / YELLOW BALL / RD 1 / #3665611", {}),
        "split": ("Division C / Yellow Ball / #3665611",
                  {"season_line": "Round 1", "venue": "",
                   "club_lines": ("EXAMPLE DISTRICT TENNIS", "14 Example Rd")}),
        "refused": ("Division: C / Yellow Ball / Round: 1 / #3665611",
                    {"drop_column_rule": 3}),
    }

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.reports = {}
        for name, (line, spec) in cls.CARDS.items():
            path = Path(cls._tmp.name) / f"{name}.pdf"
            with mock.patch.object(cw, "_identity_line", lambda _spec, line=line: line):
                cw.write_card_file(path, cw.CardSpec(**spec))
            done = subprocess.run((sys.executable, str(DOCTOR), str(path)),
                                  capture_output=True, text=True, timeout=TIMEOUT)
            cls.reports[name] = done.stdout + done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def field(self, card: str, name: str) -> str:
        """What the report says of header field ``name`` on ``card``."""
        line = re.search(rf"^ *{name} +(found +'.*'|not found)$", self.reports[card],
                         re.MULTILINE)
        self.assertIsNotNone(line, self.reports[card])
        return re.sub(" +", " ", line.group(1))

    def test_every_wording_the_reader_reads_is_a_field_found(self):
        for card, division, round_ in (("colon", "C", "1"), ("grade", "C", "4"),
                                       ("run-on", "C", "4"), ("capitals", "C", "1")):
            with self.subTest(card=card):
                self.assertEqual(
                    (self.field(card, "division"), self.field(card, "round")),
                    (f"found '{division}'", f"found '{round_}'"))

    def test_the_round_found_is_the_one_the_reader_reads(self):
        self.assertIn("header: division 'C' round '1'", self.reports["split"])
        self.assertEqual(self.field("split", "round"), "found '1'")

    def test_a_card_refused_for_its_table_is_not_blamed_on_its_header(self):
        report = self.reports["refused"]
        self.assertIn("This page was refused: the column between", report)
        self.assertNotIn("the header line is missing", report)
        self.assertEqual(self.field("refused", "division"), "found 'C'")


class TestTheDoctorFindsEveryResultVerbTheReaderReads(unittest.TestCase):
    """The checker called a result verb missing on a card the reader had just read.

    Its header field was found with a pattern of its own, `DEFEATS`, `DRAWS WITH`
    or `LOSES TO`, a third list of verbs beside the reader's and validate's, which
    4.137 made one table. Measured by /tmp/t7/scratch/V1/probe/doctorverb.py:
    `result verb not found` on cards printing `DEF. BY`, `BEAT` and `DREW`, above
    a verdict reading each as the card's verb and holding it to the set rows.

    So the field is found by the one table, :func:`matchcentre.card.verb_side`,
    asked of every run of up to three words in capitals on a line, the longest
    first, so `DEF. BY` is found whole and not as `DEF.`. A separator states no
    result, as the verb check says of it, and is no result verb found. Rejected:
    widening the pattern, a third list to keep in step with the table; any word
    the table holds, in any case, which finds `draw` and `tie` in a rules sheet's
    prose; and the separators as a found verb, which would find the `v` of a
    fixture list as a card's word.
    """

    VERBS = ("DEFEATS", "DEF. BY", "BEAT", "LOSES TO", "DREW", "DRAWS WITH", "VS")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.reports = {}
        for index, verb in enumerate(cls.VERBS):
            path = Path(cls._tmp.name) / f"card{index}.pdf"
            cw.write_card_file(path, cw.CardSpec(verb=verb, winner=cw.NO_TICK))
            done = subprocess.run((sys.executable, str(DOCTOR), str(path)),
                                  capture_output=True, text=True, timeout=TIMEOUT)
            cls.reports[verb] = done.stdout + done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def field(self, verb: str) -> str:
        """What the report on the card printing ``verb`` says of its result verb."""
        line = re.search(r"^ *result verb +(found +'.*'|not found)$",
                         self.reports[verb], re.MULTILINE)
        self.assertIsNotNone(line, self.reports[verb])
        return re.sub(" +", " ", line.group(1))

    def test_every_verb_the_table_reads_is_found_whole(self):
        for verb in self.VERBS[:-1]:
            with self.subTest(verb=verb):
                self.assertIn(f"verb          {verb}\n", self.reports[verb])
                self.assertEqual(self.field(verb), f"found '{verb}'")

    def test_a_separator_is_no_result_verb_found(self):
        self.assertIn("verb          VS\n", self.reports["VS"])
        self.assertEqual(self.field("VS"), "not found")

    def test_prose_and_a_fixture_line_print_no_result_verb(self):
        for line in ("In the event of a draw the tie is level and halved",
                     "Round 1   Tue 18/08/2026   Riverside Rockets v Hilltop Aces",
                     "RIVERSIDE ROCKETS V HILLTOP ACES"):
            with self.subTest(line=line):
                self.assertEqual(doctor._result_verb_in(line), "")

    def test_the_field_is_found_by_the_one_table(self):
        # The spy: a word the table does not hold, read as a win by a stand-in
        # for it, is found, so the field asks nothing but the table.
        self.assertIs(doctor.verb_side, verb_side)
        path = Path(self._tmp.name) / "trounces.pdf"
        cw.write_card_file(path, cw.CardSpec(verb="TROUNCES", winner=cw.NO_TICK))
        page = pdf.extract(path)
        with mock.patch.object(doctor, "verb_side",
                               lambda verb: "home" if verb == "TROUNCES" else None), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            found, _missing = doctor._header_fields(page)
        self.assertIn("result verb", found)
        self.assertIn("'TROUNCES'", out.getvalue())


class TestTheDoctorFindsTheFillInsHeadingTheReaderEndsTheBlockAt(unittest.TestCase):
    """The checker called a fill-ins heading missing that the reader had ended at.

    Its field was found with a pattern of its own, ``Fill-in`` or ``Fill-ins``, a
    second copy of the reader's rule that 4.81 widened in the reader and not here.
    Measured (``/tmp/t7/scratch/R1/probe.py``): ``fill-ins not found`` on cards
    printing ``Reserves`` over both squads, over the home squad only or the away
    squad only, ``Substitutes`` over both, ``Reserves:``, ``Reserve Players`` or
    ``Bravos (Substitutes)``, each of which the reader read 3 and 3 with no note,
    the block ended at the heading. And the pattern found ``Fill-in`` on a fill-in's
    own line and ``Fill-ins`` in a comment, where the reader now reads past both.

    So the field is the word the reader ends the block at, the punctuation round it
    aside, asked of the reader itself, :func:`matchcentre.scorecard.fill_ins_heading`,
    which looks for it under ``Players`` below the set table as the reader does, and
    from the top of a page whose set table it refuses. Rejected: the pattern widened
    to the three words, a copy to keep in step, which is how this one fell behind;
    any word the reader knows as a heading's, which finds the comment's; and the
    reader's rule for a heading's line asked of every line of the page, which found
    ``RESERVES`` in the result line of a team named ``EXAMPLE BRAVOS (RESERVES)``,
    on the template's card and on one printing ``Emergencies``, where the reader
    ends the block at ``Fill-ins`` and at no fill-ins heading at all (measured,
    ``/tmp/t7/scratch/R1/fixup/rev/probe3b.py``). The same team under a letterhead
    printing ``PLAYERS`` is what holds the search below the set table: from the top
    of the page, that word is taken for the squads' ``Players`` and the result line
    under it for a heading's (``/tmp/t7/scratch/R1/fixup/floor/probe.py``).
    """

    CARDS = {
        "template": ({}, "Fill-ins"),
        "Reserves over both": ({"fill_ins_headings": ("Reserves",) * 2}, "Reserves"),
        "Reserves over home": ({"fill_ins_headings": ("Reserves", "")}, "Reserves"),
        "Reserves over away": ({"fill_ins_headings": ("", "Reserves")}, "Reserves"),
        "Substitutes": ({"fill_ins_headings": ("Substitutes",) * 2}, "Substitutes"),
        "colon": ({"fill_ins_headings": ("Reserves:", "")}, "Reserves"),
        "players": ({"fill_ins_headings": ("Reserve Players",) * 2}, "Reserve"),
        "bracketed": ({"fill_ins_headings": ("", "Bravos (Substitutes)")},
                      "Substitutes"),
        "side in words, home": ({"fill_ins_headings": ("Home Team Fill-ins", "")},
                                "Fill-ins"),
        "side in words, away": ({"fill_ins_headings": ("", "Visitors Reserves")},
                                "Reserves"),
        "refused": ({"drop_column_rule": 3}, "Fill-ins"),
        "refused, Reserves": ({"drop_column_rule": 3,
                               "fill_ins_headings": ("Reserves", "")}, "Reserves"),
        "comment": ({"fill_ins_headings": ("Emergencies",) * 2, "comments_heading": "",
                     "comments": "Fill-ins arrived late"}, ""),
        "fill-in": ({"fill_ins_headings": ("Emergencies",) * 2,
                     "home_fill_ins": ("Fill-in Golf Garside",)}, ""),
        "teams": ({"home_name": "EXAMPLE ALPHAS RESERVES",
                   "away_name": "EXAMPLE BRAVOS RESERVES"}, "Fill-ins"),
        "teams, Emergencies": ({"home_name": "EXAMPLE ALPHAS RESERVES",
                                "away_name": "EXAMPLE BRAVOS RESERVES",
                                "fill_ins_headings": ("Emergencies",) * 2}, ""),
        "team in brackets": ({"away_name": "EXAMPLE BRAVOS (RESERVES)"}, "Fill-ins"),
        "team in brackets, Emergencies": ({"away_name": "EXAMPLE BRAVOS (RESERVES)",
                                           "fill_ins_headings": ("Emergencies",) * 2},
                                          ""),
        "Players in the letterhead": ({"club_lines": ("EXAMPLE PLAYERS TENNIS",
                                                      "CLUB INC"),
                                       "away_name": "EXAMPLE BRAVOS (RESERVES)"},
                                      "Fill-ins"),
        "Players in the letterhead, Emergencies": (
            {"club_lines": ("EXAMPLE PLAYERS TENNIS", "CLUB INC"),
             "away_name": "EXAMPLE BRAVOS (RESERVES)",
             "fill_ins_headings": ("Emergencies",) * 2}, ""),
    }

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.reports = {}
        for name, (spec, _word) in cls.CARDS.items():
            path = Path(cls._tmp.name) / f"{name}.pdf"
            cw.write_card_file(path, cw.CardSpec(**spec))
            done = subprocess.run((sys.executable, str(DOCTOR), str(path)),
                                  capture_output=True, text=True, timeout=TIMEOUT)
            cls.reports[name] = done.stdout + done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def field(self, card: str) -> str:
        """What the report on ``card`` says of its fill-ins heading."""
        line = re.search(r"^ *fill-ins +(found +'.*'|not found)$", self.reports[card],
                         re.MULTILINE)
        self.assertIsNotNone(line, self.reports[card])
        return re.sub(" +", " ", line.group(1))

    def test_every_heading_the_reader_ends_the_block_at_is_found(self):
        for card, (_spec, word) in self.CARDS.items():
            if word:
                with self.subTest(card=card):
                    self.assertEqual(self.field(card), f"found '{word}'")

    def test_the_word_on_a_line_that_is_no_heading_is_not_found(self):
        for card in ("comment", "fill-in", "teams, Emergencies",
                     "team in brackets, Emergencies",
                     "Players in the letterhead, Emergencies"):
            with self.subTest(card=card):
                self.assertEqual(self.field(card), "not found")

    def test_the_field_is_found_by_the_readers_own_rule(self):
        # The spy: a heading the reader does not know, known by a stand-in for the
        # reader, is found, so the field asks nothing but the reader.
        self.assertIs(doctor.scorecard, scorecard)
        path = Path(self._tmp.name) / "emergencies.pdf"
        cw.write_card_file(path, cw.CardSpec(fill_ins_headings=("Emergencies",) * 2))
        page = pdf.extract(path)
        with mock.patch.object(scorecard, "fill_ins_heading",
                               lambda page: "Emergencies"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            found, _missing = doctor._header_fields(page)
        self.assertIn("fill-ins", found)
        self.assertIn("'Emergencies'", out.getvalue())


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


class TestAFolderNamedLikeAWordOfTheReportKeepsTheWordAndLosesThePath(
        unittest.TestCase):
    """A card in a folder named like a word the report prints keeps the word.

    Row 4.187. ``--redact`` withheld each folder of the path it was given, and
    the file's name, as a word of their own wherever the report printed them,
    though nothing prints a folder alone: a path is printed whole, as
    ``source:``. Measured by /tmp/t7/scratch/DRED/p187/probe187.py on this file's
    card and on the same card with its rules outlined, each under
    ``pages/after/`` and given by that path from the folder above: both reports
    read ``<5 chars>         1`` for ``pages         1``, and the outlined card,
    in ``outlined.pdf``, was told "32 <8 chars> rect(s)" and that another
    program "re-printed it <5 chars> it was exported". And
    /tmp/t7/scratch/DRED/probe.py found a card whose banner the reader could
    not split, in ``banner.pdf``, refused as "the team-name <6 chars> does not
    straddle the middle of the table".

    The path is now learned whole, as given and resolved, and withheld where it
    is printed, and its parts are not. Rejected: keeping the parts and adding
    the report's words to what is never shaped, which a folder named like the
    next word the report prints falls outside of.
    """

    OUTLINED = "outlined.pdf"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        folder = cls.root / "pages" / "after"
        folder.mkdir(parents=True)
        cw.write_card_file(folder / FILENAME, _spec())
        (folder / cls.OUTLINED).write_bytes(
            _repainted(cw.write_card(_spec()), lambda *rect: b"S"))
        cls.given = {name: f"pages/after/{name}" for name in (FILENAME, cls.OUTLINED)}
        cls.plain, cls.redacted = [
            {name: subprocess.run((sys.executable, str(DOCTOR), *flag, given),
                                  cwd=cls.root, capture_output=True, text=True,
                                  timeout=TIMEOUT).stdout
             for name, given in cls.given.items()}
            for flag in ((), ("--redact",))]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def redacted_line(self, name: str, said: str) -> str:
        """The redacted report's line where the plain report on ``name`` says
        ``said``, which the scrub leaves in its place."""
        plain = self.plain[name].splitlines()
        at = next(index for index, line in enumerate(plain) if said in line)
        return self.redacted[name].splitlines()[at]

    def test_a_word_its_folders_are_named_is_kept_where_the_report_prints_it(self):
        for name, said in ((FILENAME, "pages         1"),
                           (self.OUTLINED, "pages         1"),
                           (self.OUTLINED, " re-printed it after it was exported, ")):
            with self.subTest(name=name, said=said):
                self.assertIn(said, self.redacted_line(name, said))

    def test_the_word_its_name_is_made_of_is_kept_where_the_report_prints_it(self):
        said = "detail: 32 outlined rect(s) make 8 column rule(s)"
        self.assertIn(said, self.redacted_line(self.OUTLINED, said))

    def test_its_path_is_withheld_whole_where_the_report_prints_it(self):
        for name, given in self.given.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.redacted_line(name, f"source: {given} page 1 of 1"),
                    f"{doctor.INDENT}  source: {redact.shape(given)} page 1 of 1")
                for path in (given, self.root / given, (self.root / given).resolve()):
                    self.assertNotIn(str(path), self.redacted[name])


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


class TestTheRedactedReportSaysWhatToDoIfItStillNamesSomebody(DoctorCase):
    """The footer, printed where the reader is standing when a leak is noticeable.

    Everything above it is either a measurement or a shape, and the only person who
    can tell the two apart is the club that knows the names -- reading this report on
    their own machine, about to paste it somewhere public. That is the moment, and
    until this was written nothing at that moment said anything: the advice to read
    the report first lived in the README and in an issue form, both of which are read
    before the report exists, if they are read at all.

    Pinned as behaviour of a run rather than as a line of source, and pinned in four
    places the placement could go wrong: printed under the flag and not without it,
    once per run rather than once per file, and printed even when the page did not
    parse -- which is the run a leak is likeliest in, because a redactor that could
    learn nothing off an unreadable page is a redactor with nothing to shape.
    """

    # The pointer, not the sentence around it: what has to survive is that a reader
    # who sees a name knows where to take it, and the wording of the warning is the
    # kind of thing that gets improved.
    POINTER = "SECURITY.md"

    def test_the_redacted_report_says_where_a_leak_goes(self):
        self.assertIn(self.POINTER, self.report)

    def test_it_is_the_last_thing_printed_rather_than_a_preamble(self):
        # A warning above two hundred lines of census is read before the thing it is
        # about, and by the time the reader reaches the end they are in their
        # terminal's scrollback looking for the verdict. The end of the output is
        # where a paste starts.
        tail = [line for line in self.redacted.stdout.splitlines() if line.strip()]
        self.assertTrue(any(self.POINTER in line for line in tail[-5:]),
                        f"the pointer is not in the last lines of the report, "
                        f"which end:\n" + "\n".join(tail[-5:]))

    def test_the_unredacted_report_does_not_carry_it(self):
        # The plain report is for the machine that owns the card and is meant to
        # name people. A warning about leaks on a report that is not redacted at all
        # teaches a reader that the line is noise, which is the one thing it must
        # not be when it does appear.
        self.assertNotIn(self.POINTER, self.plain.stdout + self.plain.stderr)

    def test_it_is_printed_once_however_many_cards_were_examined(self):
        # `doctor.py --redact data/*.pdf` over a round is the documented way to use
        # this tool, and a footer repeated per file is a footer skimmed past.
        done = self.run_doctor("--redact", str(self.card), str(self.card))
        self.assertEqual(done.stdout.count(self.POINTER), 1,
                         f"two cards produced "
                         f"{done.stdout.count(self.POINTER)} copies of it")

    def test_a_card_that_names_a_team_after_the_policy_cannot_shape_it(self):
        # Why the footer is printed after the scrub rather than into the buffer the
        # scrub runs over. The redactor shapes every word the page carries, so a club
        # called SECURITY would have the pointer in its own footer rewritten to
        # `<8 chars>.md` -- the one line that has to survive a hostile card is the
        # line saying what to do about one.
        #
        # The deliberate cost, stated because the rest of this file exists to forbid
        # exactly this: that club's name does now appear in its redacted report, in
        # the footer's own filename. It appears there on every card whoever played,
        # so a reader learns nothing from it, which is the same bargain the
        # template's own vocabulary is kept under.
        with tempfile.TemporaryDirectory() as folder:
            card = cw.write_card_file(
                Path(folder) / "card.pdf",
                dataclasses.replace(_spec(), home_name="SECURITY TIGERS"))
            done = self.run_doctor("--redact", str(card))
        self.assertIn("SECURITY.md has a private route", done.stdout,
                      "the footer was scrubbed along with the team name, so the "
                      "report no longer says where a leak goes")
        self.assertNotIn("TIGERS", done.stdout,
                         "the team name survived, so the footer is exempt by "
                         "accident rather than by being printed after the scrub")

    def test_a_file_that_never_parsed_gets_it_too(self):
        # The likeliest leak of the lot, and the one the placement could most easily
        # miss: the run exits non-zero, there is no verdict block, and the redactor
        # learned no names to shape because no page came back. A footer printed only
        # on the happy path would be absent from exactly that report.
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "Nightjars-Kestrels.pdf"
            broken.write_bytes(b"%PDF-1.4\nnot really\n")
            done = self.run_doctor("--redact", str(broken))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(self.POINTER, done.stdout)


class TestEveryPathItIsGivenGetsAnAnswerAndNotATraceback(unittest.TestCase):
    """The doctor is the tool a club is sent to when something has gone wrong.

    ``build.py`` prints "run python3 tools/doctor.py on a path to see why it was
    refused" beside every card it could not read, so this is the command a club types
    at the moment they already have a problem. A traceback there costs them the one
    explanation the project offered them, and reads as the diagnostic tool being
    broken too.

    Every path below is a real thing a club can type. Each has to come back as a
    sentence in the tool's own voice, and each has to exit non-zero, because a
    silent zero on a file that was never examined is worse than a crash.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def doctor(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run((sys.executable, str(DOCTOR), *argv),
                              capture_output=True, text=True, timeout=TIMEOUT)

    def assert_explained(self, done: subprocess.CompletedProcess) -> str:
        text = done.stdout + done.stderr
        self.assertNotIn("Traceback", text)
        self.assertEqual(done.returncode, 1,
                         f"expected a reported failure, got {done.returncode}")
        return text

    def test_a_file_it_is_not_allowed_to_read(self):
        # The commonest way: a card copied off a share, or exported by another user.
        card = self.dir / "locked.pdf"
        card.write_bytes(b"%PDF-1.4\n")
        card.chmod(0o000)
        self.addCleanup(card.chmod, 0o600)
        text = self.assert_explained(self.doctor(str(card)))
        self.assertIn("cannot be read", text)
        self.assertIn("Permission denied", text)

    def test_a_directory(self):
        # `python3 tools/doctor.py data/` is the obvious first guess, and the answer
        # used to be "There is no file at that path" about a folder plainly there.
        folder = self.dir / "data"
        folder.mkdir()
        text = self.assert_explained(self.doctor(str(folder)))
        self.assertIn("directory", text)
        self.assertNotIn("no file at that path", text)

    def test_a_path_with_nothing_at_it(self):
        text = self.assert_explained(self.doctor(str(self.dir / "nothing.pdf")))
        self.assertIn("no file at that path", text)

    def test_a_symbolic_link_pointing_at_nothing(self):
        # A club whose data/ lives on a share that is not mounted today. The path
        # exists; what it points at does not, and saying "no file at that path" of a
        # link sitting right there sends them looking for the wrong thing.
        link = self.dir / "card.pdf"
        link.symlink_to(self.dir / "gone.pdf")
        text = self.assert_explained(self.doctor(str(link)))
        self.assertIn("link", text)

    def test_it_keeps_going_to_the_next_file(self):
        # The tool takes several paths, and the loop over a folder of cards is what
        # it is for. One bad path must not cost the ones after it.
        good = self.dir / "good.pdf"
        cw.write_card_file(good, _spec())
        done = self.doctor(str(self.dir / "nothing.pdf"), str(good))
        self.assertNotIn("Traceback", done.stdout + done.stderr)
        self.assertIn("This page reads as a scorecard.", done.stdout)
        self.assertEqual(done.returncode, 1)


class TestAFailureNobodyForesawIsStillASentence(unittest.TestCase):
    """The guard ``scorecard.read`` has and the doctor did not.

    A build already survives a file that trips something deep inside the reader:
    ``scorecard.read`` catches everything and turns it into a rejection naming the
    exception, on the stated ground that one malformed byte must not abort a whole
    build. The doctor had no such guard, which made the tool a club is sent to when a
    card will not read the one place left that could still crash on it.

    Injected rather than provoked, and deliberately so. Four hundred random
    corruptions of a real card produced only refusals the reader knows how to
    make -- which is the point of the guard: an exception nobody foresaw cannot be
    produced to order, and a test that waited for a real one would be testing nothing
    until the day it mattered. So the failure is put where the guard is, and what is
    asserted is the sentence that comes out.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.card = cw.write_card_file(Path(self._tmp.name) / "card.pdf", _spec())

    def report(self, failure: Exception, *, redact: bool = False) -> str:
        buffer = io.StringIO()
        with mock.patch.object(doctor.pdf, "extract", side_effect=failure):
            with contextlib.redirect_stdout(buffer):
                if redact:
                    doctor._one_file(self.card, page=None, redact=True)
                else:
                    doctor._report(self.card, page=None, label=str(self.card),
                                   redactor=None)
        return buffer.getvalue()

    def test_the_status_says_the_page_was_not_examined(self):
        buffer = io.StringIO()
        with mock.patch.object(doctor.pdf, "extract",
                               side_effect=RuntimeError("kaboom")):
            with contextlib.redirect_stdout(buffer):
                code = doctor._report(self.card, page=None,
                                      label=str(self.card), redactor=None)
        self.assertEqual(code, 1)

    def test_it_names_the_kind_of_failure_and_what_it_said(self):
        text = self.report(RuntimeError("kaboom"))
        self.assertIn("RuntimeError", text)
        self.assertIn("kaboom", text)

    def test_a_file_that_goes_away_mid_report_is_not_a_crash(self):
        # The realistic version: a card on a share that unmounts, or a folder being
        # synced while the doctor walks it. An OSError is not the reader refusing the
        # page, so it must not be reported as though the page were at fault -- and it
        # is not a bug in the reader either, which is the sentence the catch-all ends
        # on. Sending a club to report an unmounted share as a bug wastes their time
        # and buries a real one.
        text = self.report(PermissionError(13, "Permission denied"))
        self.assertIn("Permission denied", text)
        self.assertNotIn("bug in this reader", text)

    def test_a_failure_counting_the_pages_is_also_a_sentence(self):
        # The page count happens before any page is read, so it is a second place the
        # same crash can come from, on the same file, for the same reason. It had the
        # same traceback and needs the same guard; a guard on one of the two would
        # only move the crash a line earlier.
        buffer = io.StringIO()
        with mock.patch.object(doctor.pdf, "page_count",
                               side_effect=RuntimeError("kaboom")):
            with contextlib.redirect_stdout(buffer):
                code = doctor._report(self.card, page=None,
                                      label=str(self.card), redactor=None)
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", buffer.getvalue())
        self.assertIn("counting the pages", buffer.getvalue())

    def test_redacting_withholds_the_message_and_says_so(self):
        # The one place honesty and --redact pull against each other. An exception
        # raised inside the reader can quote bytes off the page, and the redactor
        # only knows the values it managed to learn -- from a page that did not read,
        # that may be none of them. So the type is printed, the message is not, and
        # the report says which flag would show it.
        text = self.report(RuntimeError("HALLORAN, PRIYA"), redact=True)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("HALLORAN", text)
        self.assertIn("without --redact", text)


class TestTheVerdictOnAFileOrAPageItCannotReadNamesNeitherTwice(unittest.TestCase):
    """The reader's refusal, quoted under the doctor's own ``file`` and ``page`` lines.

    The doctor quotes a refusal from ``pdf.py`` rather than put it in other words,
    and each began with the path the doctor had just printed on its ``file`` line.
    The ones about a page numbered it from 0, under a ``page 1 of 1`` of the
    doctor's own. Measured (/tmp/t7/scratch/RF/probe_pages.py): ``page 1 of 1``
    over ``This page cannot be read: data/no-contents.pdf: page 0 has no
    /Contents``, and on a file of two such pages ``page 2 of 2`` over ``...: page 1
    has no /Contents``. What the reader says is pinned in
    ``test_parsing.TestARefusalLeavesItsFileAndItsPageToWhoeverPrintsIt``. The
    doctor needs no change of its own, and these check the lines it prints.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def report(self, path: Path) -> list[str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = doctor._report(path, page=None, label=str(path), redactor=None)
        self.assertEqual(code, 1, buffer.getvalue())
        return buffer.getvalue().splitlines()

    def test_a_file_that_is_no_pdf_is_named_on_its_file_line_alone(self):
        path = self.dir / "minutes.pdf"
        path.write_text("Minutes of the committee meeting\n", encoding="utf-8")
        lines = self.report(path)
        self.assertEqual(lines[lines.index("verdict"):], [
            "verdict", "    This file cannot be opened by the reader at all.",
            "    not a PDF"])
        self.assertEqual([line for line in lines if path.stem in line],
                         [f"file          {path}"])

    def test_each_page_it_cannot_read_is_numbered_once_and_from_one(self):
        # Two pages whose /Contents are blanked out byte for byte, so that the
        # cross-reference table still holds.
        both = re.sub(rb"/Contents \d+ 0 R", lambda m: b" " * len(m[0]),
                      cards_in_one_file(*[cw.write_card(_spec())] * 2))
        path = self.dir / "both.pdf"
        path.write_bytes(both)
        self.assertEqual([line for line in self.report(path)
                          if line.startswith("page ") or "cannot be read" in line], [
            "page 1 of 2", "    This page cannot be read: the page has no /Contents",
            "page 2 of 2", "    This page cannot be read: the page has no /Contents"])


class TestTheVerdictOnACheckboxRefusalIsTheReadersOwn(unittest.TestCase):
    """What the doctor says about a checkbox refusal is what the reader decided.

    It was not. On a card whose seven tick-box labels are worded as another
    template words them, the census printed "30 outline(s)" and "4 row(s) implies
    30: as expected", and the verdict under it repeated the reader's "no pair of
    edges that far apart forms a square" and then sent the club to "a stricter test
    than anything this tool applies" -- when the test that had failed was the
    label one, which the doctor's own header fields had just applied and printed as
    three lines of "not found". Measured by /tmp/t4/scratch/i416c/season.py.

    The squares' half had the same fallback, measured by
    /tmp/t4/scratch/i416c/fixup/probe.py: on a page whose box edges pair into no
    square, "the structure above looks like a scorecard" came straight under a
    census of "9 outline(s)" and a MISMATCH. And on a page whose box edges are
    stroked rather than filled the census counts 30 as expected and the reader 0,
    which is by design -- the census is the looser test -- and has to be said.

    Rejected: letting the doctor recognise the case from those header fields. They
    are its own patterns, case-sensitive where the reader is not, and they find the
    words on a page whose boxes are drawn away from them -- a second rule, which is
    a second answer. The reader refuses with a refusal of its own kind, one for
    each half, and the doctor asks which kind it was given. Rejected too: making
    the census count with the reader's rule, which would take away the one line
    that shows the stroked page's boxes are there.

    A card on which some boxes are named and the page lacks what the rest need is
    a third kind. Measured by /tmp/t4/scratch/y434/fix/probe.py, the doctor gave
    both corpus files that lost a box, and every cut of a genuine card from 30% to
    79%, the cause for another template, when each was a download that stopped
    early or a damaged copy.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.card = cw.write_card_file(root / "card.pdf", _spec())
        cls.relabelled = root / "relabelled.pdf"
        cls.relabelled.write_bytes(relabelled(cls.card.read_bytes(),
                                              OTHER_TEMPLATE_LABELS))
        cls.done = subprocess.run((sys.executable, str(DOCTOR), str(cls.relabelled)),
                                  capture_output=True, text=True, timeout=TIMEOUT)
        cls.report = cls.done.stdout + cls.done.stderr
        cls.verdict = cls.verdict_in(cls.report)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def verdict_in(report: str) -> str:
        """The verdict in *report*, its line breaks folded: its reason, its
        detail and its cause go on past the width under their label (4.186)."""
        return " ".join(report.partition("    verdict\n")[2].split())

    def report_on(self, page: pdf.Page) -> str:
        """The doctor's report on the real card, with ``page`` read off it."""
        buffer = io.StringIO()
        with mock.patch.object(doctor.pdf, "extract", return_value=page):
            with contextlib.redirect_stdout(buffer):
                doctor._report(self.card, page=None, label=str(self.card),
                               redactor=None)
        return buffer.getvalue()

    def test_the_verdict_counts_the_boxes_the_census_counted(self):
        self.assertEqual(self.done.returncode, 1)
        self.assertIn("30 outline(s)", self.report)
        self.assertIn("This page was refused: none of this card's checkboxes is "
                      "beside a label this reader knows", self.verdict)
        self.assertIn("this reader finds 30 checkbox squares of 8.07pt",
                      self.verdict)
        self.assertIn("Match Disputed", self.verdict)
        self.assertNotIn("no pair of edges", self.report)

    def test_the_cause_it_gives_is_the_labels_and_not_a_test_it_cannot_see(self):
        self.assertNotIn("stricter test than anything this tool applies",
                         self.report)
        self.assertIn("Likeliest cause:", self.verdict)
        self.assertIn("tick-box labels", self.verdict)
        # A card retyped by hand is one of the causes it names, and the club
        # can fix that one: its own export will read. So it is not told that
        # nothing on its side fixes this.
        self.assertIn("a card retyped by hand", self.verdict)
        self.assertIn("If Match Centre can export this match, use", self.verdict)
        self.assertNotIn("nothing on the club's side", self.verdict)

    def test_labels_off_their_boxes_line_get_that_cause_though_its_patterns_see_them(
            self):
        text = self.report_on(boxes_moved(pdf.extract(self.card), 2.0))
        # The premise: the doctor's own patterns find every label word here.
        for field in ("match winner", "match forfeited", "per-set flags"):
            with self.subTest(field=field):
                self.assertRegex(text, rf"{field}\s+found")
        self.assertIn("tick-box labels", text.partition("    verdict\n")[2])

    def test_edges_that_make_no_square_are_not_blamed_on_the_labels(self):
        text = self.report_on(box_edges_unpaired(pdf.extract(self.card)))
        verdict = self.verdict_in(text)
        # The premise: the census disagrees with the count a card this size wants.
        self.assertIn("9 outline(s)", text)
        self.assertIn("MISMATCH", text)
        self.assertIn("no pair of edges that far apart forms a square", verdict)
        self.assertNotIn("tick-box labels", verdict)
        self.assertIn("Likeliest cause: the tick boxes are not drawn the way "
                      "Match Centre draws", verdict)
        self.assertNotIn("looks like a scorecard", verdict)

    def test_a_card_short_of_a_square_or_a_word_is_told_to_download_it_again(self):
        # Another template prints every box and words a label the same way
        # everywhere, so it is the cause of neither: one card has lost row 1's
        # home Forfeit square, the other row 1's home Retired label.
        genuine = pdf.extract(self.card)
        for name, page in (("a square gone", fewer_boxes(genuine)),
                           ("a label gone", word_lost(genuine, "Retired"))):
            with self.subTest(card=name):
                verdict = self.verdict_in(self.report_on(page))
                self.assertIn("This page was refused: this reader could not name 1 "
                              "of the 30 checkboxes this card needs", verdict)
                self.assertIn("Likeliest cause: part of the card did not reach "
                              "this file", verdict)
                self.assertIn("Download it again from Match Centre", verdict)
                self.assertNotIn("tick-box labels", verdict)

    def test_boxes_the_census_sees_and_the_reader_does_not_are_said_to_be_that(self):
        text = self.report_on(box_parts_outlined(pdf.extract(self.card)))
        verdict = self.verdict_in(text)
        # The premise: the two counts differ, by design.
        self.assertIn("30 outline(s)", text)
        self.assertIn("implies 30 (4 x 2 x 3 + 4 + 2): as expected", text)
        self.assertIn("checkboxes: 0 squares found, 0 named by a label", text)
        self.assertIn("only 60 outlined in that shape", verdict)
        self.assertIn("The census above counts outlines by a looser", verdict)
        self.assertNotIn("stricter test than anything this tool applies", verdict)
        self.assertNotIn("tick-box labels", verdict)

    def test_a_tick_no_row_reads_is_given_its_own_cause(self):
        # Row 1's home Forfeit label and box drawn again off the table, ticked:
        # the page read, and "This page reads as a scorecard" (4.171). The
        # cause is the kind's, and sends the club to the box the detail names.
        text = self.report_on(drawn_again(pdf.extract(self.card), "Forfeit", 600.0,
                                          ticked=True))
        verdict = self.verdict_in(text)
        self.assertIn("This page was refused: this card ticks a checkbox this "
                      "reader reads nothing from", verdict)
        self.assertIn("the tick is in a home Forfeit box in no set row, which no "
                      "row reads", verdict)
        self.assertIn("Likeliest cause: the card draws a tick box that Match "
                      "Centre's card", verdict)
        # And what a club can do about it.
        self.assertIn("Export it again from Match Centre, and if that", verdict)
        self.assertIn("send this verdict (--redact takes the", verdict)
        for other in ("looks like a scorecard", "tick-box labels",
                      "did not reach this file"):
            with self.subTest(other=other):
                self.assertNotIn(other, verdict)


def _in_paths(painter, x, y, w, h, shade=(0.0, 0.0, 0.0)):
    """cardwriter's rect, filled as a closed path, ``m l l l h f``, not ``re f``.

    The same rectangle in the same place and the same colour, which ``pdf.py`` does
    not record, since it keeps rectangles only. The fixture card opened in
    LibreOffice Draw and exported again (/tmp/t4/scratch/y450/probe.py) is painted
    this way: 201 closed paths, its 200 rects and the page, and no rect.
    """
    _PAINT_RECT(painter, x, y, w, h, shade)
    parts = painter.ops[-1].split()
    x0, y0, w0, h0 = (float(n) for n in parts[4:8])
    painter.ops[-1] = (f"{' '.join(parts[:4])} {x0:.2f} {y0:.2f} m "
                       f"{x0 + w0:.2f} {y0:.2f} l {x0 + w0:.2f} {y0 + h0:.2f} l "
                       f"{x0:.2f} {y0 + h0:.2f} l h f")


def _text_only(path: Path, lines: tuple[tuple[str, float, float], ...]) -> Path:
    """A page of ``(text, x, baseline)`` lines and nothing else: no rect, no line."""
    art = cw._Painter()
    for text, x, baseline in lines:
        art.words(text, x, baseline, 8.0)
    path.write_bytes(cw._assemble("\n".join(art.ops).encode("latin-1"),
                                  with_to_unicode=True))
    return path


def _laid(opening: str, text: str, hang: str, keep: tuple[str, ...] = ()) -> str:
    """*text* after *opening* as a row of the doctor's report is laid out: going
    on under *hang* past the width, by the one fill, each of *keep* whole (4.186)."""
    if len(opening) + len(text) <= cli.WIDTH:
        return opening + text
    return cli.fill(text, hang, first=opening, keep=keep)


class PagesCase(unittest.TestCase):
    """The doctor run once on each page of :meth:`pages`, once for the class."""

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        raise NotImplementedError

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.paths = cls.pages(Path(cls._tmp.name))
        cls.reports = {
            name: subprocess.run((sys.executable, str(DOCTOR), str(path)),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            for name, path in cls.paths.items()}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def cause(self, name: str) -> str:
        """The likeliest cause given for ``name``, its line breaks folded."""
        done = self.reports[name]
        self.assertEqual(done.returncode, 1, done.stdout[-2000:])
        # One cause, the likeliest, and no second one after it.
        self.assertEqual(done.stdout.count("Likeliest cause: "), 1, done.stdout)
        return " ".join(done.stdout.partition("Likeliest cause: ")[2].split())

    def count(self, name: str, what: str) -> int:
        """How many ``what`` (``words`` or ``rects``) the report counts on ``name``."""
        return int(re.search(rf"\n +{what} +(\d+)\n", self.reports[name].stdout)[1])

    def fields(self, name: str) -> list[str]:
        """The header fields the report says it found on ``name``."""
        return re.findall(r"^ {6}(\S[\w -]*?) +found +'", self.reports[name].stdout,
                          re.MULTILINE)


# A draw as a word processor exports one with no table drawn round it.
_DRAW = (("Tuesday Division B draw, 2026", 60.0, 80.0),
         ("Round 1   Tue 18/08/2026   Riverside Rockets v Hilltop Aces", 60.0, 98.0),
         ("Round 2   Tue 25/08/2026   Hilltop Aces v Lakeside Spinners", 60.0, 116.0))

# What a page is told when an image covers much of it and it has no table, and
# when it prints words, none of a card's but a date or a time, and paints neither
# a rectangle nor a picture. Each ends with where a page that is no scorecard
# belongs.
_KEEP_IT_OUT = ("If the page is one of those, the file is fine, but keep it in a "
                "folder of its own outside data/: build.py reports every PDF under "
                "data/ that it cannot read as an error (F4), and exits 1.")
_PICTURE = ("an image covers much of the page, as the images line above shows, "
            "and the page has no table rules, so it is an image of a card rather "
            "than a card. A scan or a screenshot printed to PDF cannot be read: "
            "re-export the scorecard from Match Centre instead. A draw or a team "
            "list with a photograph on it can look like this too. " + _KEEP_IT_OUT)
_TYPED = ("the page prints words, but none of a card's other than a date or a "
          "time, as the header fields above show. It paints no rectangle, and no "
          "image large enough to be a picture of a card. A draw, a rules sheet, a "
          "fixture list or a team list typed out looks like this. " + _KEEP_IT_OUT)


def _pictured(path: Path, content: bytes, box: bytes) -> Path:
    """A page painting an image into ``box``, a ``cm`` matrix, then ``content``."""
    path.write_bytes(_with_an_image(b"q " + box + b" cm /Im1 Do Q\n" + content))
    return path


def _content(raw: bytes) -> bytes:
    """The content stream of a PDF cardwriter wrote, which it never compresses."""
    return re.search(rb"6 0 obj\n<< /Length \d+ >>\nstream\n(.*)\nendstream\n"
                     rb"endobj", raw, re.S)[1]


class TestAPageThatPrintsACardsWordsIsNotCalledAPictureOfOne(PagesCase):
    """A card whose lines are paths is told so, and not that it is a picture.

    The causes paragraph took any page with words and no rectangles for "an image
    of a card rather than a card", and told the club that a scan or a screenshot
    cannot be read and to re-export the scorecard from Match Centre. Measured
    (/tmp/t4/scratch/y450/probe.py) on the fixture card with every ``re f``
    painted instead as the same rectangle filled as a closed path: 197 words, 0
    rects, 16 of the 17 header fields found, as on the genuine card, and that
    sentence. The card LibreOffice Draw exported again was told the same, and so
    was a draw typed out. A plain scan never reached the sentence: the card
    rasterised by Ghostscript has no words, and was told the page is empty.

    So a page with no rectangles that prints a word of the template is told its
    shapes are drawn in a way this reader does not record, as a program saving
    the card again draws them. The sentence named a scanning app laying the
    card's words invisibly over a picture of the card as well, since such a
    picture measured 197 words, 0 rects and 16 fields too, and the reader then
    recorded no picture. It records where each image is painted now, and a
    picture is called one for the image it paints, the Ghostscript scan with it
    (TestAPageIsCalledAPictureForTheImageItPaintsAndNotForWhatItLacks), so the
    sentence names the program alone.

    The date and the time are not words of the template. Chrome, printing with its
    headers and footers on, prints both at the top of the page, and the card's
    screenshot on a web page, printed to PDF by Chrome, has 13 words, no rects and
    those two fields found, and nothing else. It is still called a picture, for
    the image it paints. Every other field is a word of the card's, the roster's
    headings as much as the division and the round: a team list printed under
    them was called a picture by a rule counting two fields or fewer, and by one
    taking the division and the round for the template's only words
    (/tmp/t4/scratch/y450/fix/probe2.py). A page that finds no field is not told
    that the fields show a card's words, and a card that paints rects and no rules
    is told of its rules, and not that it paints no rectangle.

    Rejected: any field found, which tells that screenshot its shapes are drawn
    another way; any of the words ``--redact`` keeps as the template's, which
    include the ``at`` in the name a Mac gives a screenshot; and the set table's
    own labels alone, which calls a draw typed out with its rounds and its
    division a picture of a card. And reading paths as rectangles, a decision
    about reading such cards that no Match Centre export has asked for.
    """

    # What Chrome printed round the screenshot, which is painted between them
    # over two thirds of the page: the date and time, the page's title, its
    # address and the page number. The title is the name a Mac gives a
    # screenshot.
    SCREENSHOT = (("9/23/26, 9:06 PM", 24.0, 20.0),
                  ("Screenshot 2026-08-18 at 9.05.12 pm.png (496x702)", 255.0, 20.0),
                  ("file:///home/club/cards/shot.html", 24.0, 780.0),
                  ("1/1", 576.0, 780.0))
    # A team list typed out, and the same under the headings the card's roster
    # block prints, with each player's rating beside the name.
    TEAM = (("Riverside Rockets 2026", 60.0, 80.0), ("Alex Example", 60.0, 98.0),
            ("Blair Sample", 60.0, 116.0), ("Casey Test", 60.0, 134.0))
    HEADED = (("Riverside Rockets 2026", 60.0, 80.0), ("Players   Rating", 60.0, 98.0),
              ("Alex Example   5.1", 60.0, 116.0), ("Blair Sample   4.8", 60.0, 134.0))

    PICTURE = _PICTURE

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        with mock.patch.object(cw._Painter, "rect", _in_paths):
            paths = cw.write_card_file(root / "paths.pdf", cw.CardSpec())
        with mock.patch.object(cw._Painter, "rect", _in_stroked_lines):
            lines = cw.write_card_file(root / "lines.pdf", cw.CardSpec())
        return {"genuine": cw.write_card_file(root / "genuine.pdf", cw.CardSpec()),
                "paths": paths,
                "lines": lines,
                "screenshot": _pictured(root / "screenshot.pdf",
                                        _typed(*cls.SCREENSHOT), b"420 0 0 800 88 21"),
                "draw": _text_only(root / "draw.pdf", _DRAW),
                "team list": _text_only(root / "team.pdf", cls.TEAM),
                "headed team list": _text_only(root / "headed.pdf", cls.HEADED)}

    def test_a_card_drawn_in_paths_is_told_its_shapes_are_drawn_another_way(self):
        # The premise: every word and every field the card prints, and no rect.
        self.assertEqual(self.count("paths", "words"), self.count("genuine", "words"))
        self.assertEqual(self.fields("paths"), self.fields("genuine"))
        self.assertEqual(self.count("paths", "rects"), 0)
        cause = self.cause("paths")
        self.assertTrue(cause.startswith(
            "the page prints a card's words, as the header fields above show, but "
            "paints no rectangle"), cause)
        for clause in (
                "So its shapes are drawn in a way this reader does not record.",
                "A program that opens a card and saves it again can draw them so.",
                "Export it again from Match Centre",
                "send this verdict (--redact takes the names out) to whoever "
                "maintains this tool"):
            with self.subTest(clause=clause):
                self.assertIn(clause, cause)
        self.assertNotIn("image of a card", cause)
        # A scan with the card's words laid over it paints its picture, and is
        # told it is one, so this sentence no longer names the scanning app.
        self.assertNotIn("scanning app", cause)

    def test_a_screenshot_printed_to_pdf_is_still_called_a_picture(self):
        self.assertEqual(self.count("screenshot", "rects"), 0)
        self.assertEqual(self.fields("screenshot"), ["date", "time"])
        self.assertEqual(self.cause("screenshot"), self.PICTURE)

    def test_a_draw_typed_out_is_not_called_a_picture_of_a_card(self):
        self.assertEqual(self.count("draw", "rects"), 0)
        self.assertEqual(self.fields("draw"), ["date", "division", "round"])
        cause = self.cause("draw")
        self.assertTrue(cause.startswith("the page prints a card's words"), cause)
        self.assertIn("A draw, a rules sheet or a team list typed out, with no "
                      "table, can look like this too.", cause)

    def test_a_team_list_under_the_rosters_headings_is_not_called_a_picture(self):
        # No division and no round, and no more fields than a browser prints: it
        # is the roster's own words that say this is no picture of a card.
        self.assertEqual(self.count("headed team list", "rects"), 0)
        self.assertEqual(self.fields("headed team list"), ["roster block", "ratings"])
        cause = self.cause("headed team list")
        self.assertTrue(cause.startswith("the page prints a card's words"), cause)

    def test_a_page_that_finds_no_field_is_not_told_the_fields_show_a_cards_words(
            self):
        # It paints no picture either, and is told it is a page typed out.
        self.assertEqual(self.count("team list", "rects"), 0)
        self.assertEqual(self.fields("team list"), [])
        self.assertNotIn("card's words", self.cause("team list"))

    def test_a_card_that_paints_rects_and_no_rules_is_told_of_its_rules(self):
        # pdf.py records the five fills this card still paints and none of its
        # stroked lines, so it has rects and no column rules, and prints every
        # field the card does.
        self.assertEqual(self.count("lines", "rects"), 5)
        self.assertEqual(self.fields("lines"), self.fields("genuine"))
        cause = self.cause("lines")
        self.assertTrue(cause.startswith("the set table's vertical rules are absent"),
                        cause)
        self.assertNotIn("paints no rectangle", cause)


class TestAPageThatIsNoScorecardIsToldToKeepItOutOfData(PagesCase):
    """A draw is told what build.py does with it, and not that nothing is wrong.

    The cause for a header line missing its division, its round or its date ends
    with a word for a page that is not a scorecard at all: "If this is one of
    those, nothing is wrong: build.py counts it as refused and carries on".
    Measured (/tmp/t4/scratch/y450/fix/probe2.py) on the fixture card printed with
    no division and no round, it was told that. Then build.py, run on four demo
    cards with that card and the draw typed out here beside them under data/,
    reported `refused 2` and `2 errors`, both F4, and ended `exit 1: 2 card(s)
    found and not scored, 2 validation error(s)`. With the two moved out it ended
    `exit 0`. F4 is an error every time, and the README tells a club to keep its
    draw, its rules and its team lists outside data/. The draw reaches the other
    cause, for a page that prints a card's words and paints no rectangle, and
    needs the same word.

    So both causes end with the same three lines: the file is fine, it belongs in
    a folder of its own outside data/, and why.

    Rejected: rewording only the newer cause, which tells a draw two different
    things by whether it happens to paint a table; and leaving the word off the
    newer cause, which tells a draw with no table nothing of the kind.
    """

    TRAILER = ("If the page is one of those, the file is fine, but keep it in a "
               "folder of its own outside data/: build.py reports every PDF under "
               "data/ that it cannot read as an error (F4), and exits 1.")

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        return {"draw": _text_only(root / "draw.pdf", _DRAW),
                "no header": cw.write_card_file(
                    root / "no-header.pdf", cw.CardSpec(division="", round_label=""))}

    def test_a_draw_with_no_table_is_told_to_keep_it_out_of_data(self):
        cause = self.cause("draw")
        self.assertTrue(cause.startswith("the page prints a card's words"), cause)
        self.assertTrue(cause.endswith(
            "can look like this too. " + self.TRAILER), cause)
        self.assertNotIn("nothing is wrong", cause)

    def test_a_card_with_no_header_line_is_told_the_same(self):
        cause = self.cause("no header")
        self.assertTrue(cause.startswith(
            "the header line is missing division and round."), cause)
        self.assertTrue(cause.endswith(
            "which is exactly what a draw, a rules sheet or a team list looks like. "
            + self.TRAILER), cause)
        self.assertNotIn("nothing is wrong", cause)

    def test_a_header_line_short_of_all_three_fields_lists_them_as_a_sentence(self):
        """The fields a header line lacks are listed as ``a, b and c``, in 78 columns.

        ``_causes`` joined them with commas alone. Measured before, through
        ``tools/doctor.py`` on the card printed with no division and no round
        (/tmp/t6/scratch/m23/probe-before.txt): ``Likeliest cause: the header line
        is missing division, round. That line``, 76 columns with its indent. A card
        cannot be written without a date, so all three are asked of ``_causes``
        with no field found, which is what a page with no header line at all gives:
        ``missing division, round, date. That line``, 82 columns
        (/tmp/t6/scratch/m23/doctor-causes-before.txt). ``That line`` goes down to
        the next line, which keeps every line within 78 for any of the three.

        Rejected: leaving ``That line`` where it was, 79 columns for two fields.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = cw.write_card_file(Path(tmp) / "no-header.pdf",
                                      cw.CardSpec(division="", round_label=""))
            page = pdf.extract(path)
        with self.assertRaises(scorecard.CardError) as refused:
            scorecard.read_page(page)
        with contextlib.redirect_stdout(io.StringIO()):
            rules, bands = doctor._table(page)
        for found, missing in (([], "division, round and date"),
                               (["date"], "division and round"),
                               (["round", "date"], "division")):
            with self.subTest(missing=missing):
                lines = doctor._causes(page, refusal=refused.exception, rules=rules,
                                       bands=bands, found=found, rotation="0")
                self.assertTrue(" ".join(lines).startswith(
                    f"Likeliest cause: the header line is missing {missing}. That "
                    f"line is the only place the division, the round and the date "
                    f"appear"), lines)
                self.assertLessEqual(
                    max(len(f"{doctor.INDENT}  {line}") for line in lines), 78)


class TestAPageIsCalledAPictureForTheImageItPaintsAndNotForWhatItLacks(PagesCase):
    """A page is called a picture of a card when an image covers much of it.

    The causes paragraph called a page a picture when it painted no rectangle
    and found no header field but the date and the time, which is to say from
    what the page lacked, since the reader recorded no image for a rule to ask
    about. Measured (/tmp/t6/scratch/m8/probe.py): a team list, a rules sheet
    with a start time in it and a fixture list, each typed out and painting no
    image, were each told it is "an image of a card rather than a card". The
    pictures were told otherwise. The fixture card rasterised by Ghostscript
    (pdfimage24), and a page that is one image and nothing else, 0 words and 0
    rects each, were told the page is empty or its content inside a structure
    this reader skips. A card's PNG opened in Chrome and printed to PDF, the
    picture 15.6 per cent of the page over the rect or two Chrome paints behind
    it, was told the set table's vertical rules are absent. And a scan with the
    card's words laid over it, as a scanning app that reads the text makes one,
    was told its shapes are drawn another way.

    So the reader records where each image is painted, and a page whose images
    cover a tenth of it or more, and which has no table rules, is called a
    picture, before it is called empty. A page with words and no rect, finding no
    field but a date or a time, and painting no picture, is told it is typed
    out, and the report prints an images row saying how much of the page its
    images cover. The share is the page's area the images overlap, added up
    image by image and at most the whole page, so that a scan painted in two
    strips that overlap covers the page once, and an image painted beside the
    page covers none of it, as an image covers none of a page of no size, which
    a damaged file can declare, and the row says so. A tenth lies between the
    largest mark a card carries that is no picture of it, a club's logo on the
    letterhead, 60 x 30 points and under 1 per cent of the page, and the
    smallest picture of a card a program made, that 15.6 per cent. A team list
    with a photograph on it is called a picture too, and the sentence says that
    it can be one, and where such a page belongs. The one here is sized to cover
    12 per cent of the page, between a tenth and an eighth, so that it holds the
    tenth where no page a program made does.

    Rejected: any image at all, which calls a card with its club's logo on it a
    picture, and a team list with an icon among its names; a quarter of the
    page, which misses the PNG Chrome printed; the images' own areas, which
    count a picture painted beside the page; and asking as well that the page's
    words lie on the picture, which no page measured needs.
    """

    SCREENSHOT = TestAPageThatPrintsACardsWordsIsNotCalledAPictureOfOne.SCREENSHOT
    TEAM = TestAPageThatPrintsACardsWordsIsNotCalledAPictureOfOne.TEAM
    RULES = (("Tuesday night competition rules", 60.0, 80.0),
             ("Play starts at 7:00 PM sharp.", 60.0, 98.0),
             ("A team more than fifteen minutes late forfeits the night.", 60.0,
              116.0))
    FIXTURES = (("Tuesday night fixtures, spring 2026", 60.0, 80.0),
                ("Tue 18/08/2026   7:00 PM   Riverside Rockets v Hilltop Aces",
                 60.0, 98.0),
                ("Tue 25/08/2026   7:00 PM   Hilltop Aces v Lakeside Spinners",
                 60.0, 116.0))
    PAGE = b"595 0 0 842 0 0"
    # A club's logo on the letterhead, 60 x 30 points: 0.36 per cent of the page.
    LOGO = b"60 0 0 30 40 790"

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        # The card's words and none of its rects, as a scanning app lays them
        # invisibly (render mode 3) over the picture it read them from.
        with mock.patch.object(cw._Painter, "rect", lambda *args, **kwargs: None):
            words = _content(cw.write_card(cw.CardSpec()))
        with mock.patch.object(cw._Painter, "rect", _in_paths):
            paths = _content(cw.write_card(cw.CardSpec()))
        no_header = _content(cw.write_card(cw.CardSpec(division="",
                                                       round_label="")))
        team = _typed(*cls.TEAM)
        inline = root / "inline.pdf"
        inline.write_bytes(cw._assemble(_inline(_GREY), with_to_unicode=True))
        # The PNG alone in Chrome: a dark page, the picture in the middle of it
        # over just under 16 per cent of the page, as Chrome's print covers 15.6
        # of its Letter page, and what Chrome prints round a page.
        small = root / "small.pdf"
        small.write_bytes(_with_an_image(
            b"0.2 g 0 0 595 842 re f\nq 160 0 0 500 217.5 171 cm /Im1 Do Q\n"
            + _typed(*cls.SCREENSHOT)))
        strips = root / "strips.pdf"
        strips.write_bytes(_with_an_image(b"q 595 0 0 506 0 336 cm /Im1 Do Q\n"
                                          b"q 595 0 0 506 0 0 cm /Im1 Do Q\n"))
        # The scan on a page of no size, the box written over at the same
        # length so that the cross-reference table still holds.
        no_size = root / "no-size.pdf"
        no_size.write_bytes(_with_an_image(b"q " + cls.PAGE + b" cm /Im1 Do Q")
                            .replace(b"/MediaBox [0 0 595 842]",
                                     b"/MediaBox [0 0 0 0]    "))
        return {
            "genuine": cw.write_card_file(root / "genuine.pdf", cw.CardSpec()),
            "scan": _pictured(root / "scan.pdf", b"", cls.PAGE),
            "scan in strips": strips,
            "no size": no_size,
            "inline scan": inline,
            "read scan": _pictured(root / "read.pdf",
                                   words.replace(b"BT ", b"BT 3 Tr "), cls.PAGE),
            "printed small": small,
            "team photo": _pictured(root / "photo.pdf", team,
                                    b"250 0 0 240 300 500"),
            "team list": _text_only(root / "team.pdf", cls.TEAM),
            "rules sheet": _text_only(root / "rules.pdf", cls.RULES),
            "fixture list": _text_only(root / "fixtures.pdf", cls.FIXTURES),
            "team list with a logo": _pictured(root / "logo-team.pdf", team,
                                               cls.LOGO),
            # A picture the size of the page, painted beside it.
            "team list beside a picture": _pictured(root / "beside.pdf", team,
                                                    b"595 0 0 842 595 0"),
            "card with a logo": _pictured(root / "logo-card.pdf", paths, cls.LOGO),
            "card over a picture": _pictured(root / "over.pdf", no_header,
                                             cls.PAGE),
        }

    def images(self, name: str) -> str:
        """What the report's images row says of ``name``."""
        row = re.search(r"\n +images +(.*)\n", self.reports[name].stdout)
        self.assertIsNotNone(row, self.reports[name].stdout[:2000])
        return row[1]

    def test_a_scan_is_called_a_picture_and_not_an_empty_page(self):
        for name in ("scan", "inline scan", "scan in strips"):
            with self.subTest(name=name):
                self.assertEqual(self.count(name, "words"), 0)
                self.assertEqual(self.count(name, "rects"), 0)
                self.assertEqual(self.cause(name), _PICTURE)

    def test_a_scan_with_the_cards_words_laid_over_it_is_called_a_picture(self):
        # Every field the card prints, and no rect, as on a card drawn in paths:
        # the picture is the difference between the two.
        self.assertEqual(self.fields("read scan"), self.fields("genuine"))
        self.assertEqual(self.count("read scan", "rects"), 0)
        self.assertEqual(self.cause("read scan"), _PICTURE)

    def test_a_picture_printed_small_over_a_background_is_still_a_picture(self):
        self.assertEqual(self.count("printed small", "rects"), 1)
        self.assertEqual(self.fields("printed small"), ["date", "time"])
        self.assertEqual(self.cause("printed small"), _PICTURE)

    def test_a_team_list_with_a_photograph_is_told_it_can_look_like_one(self):
        self.assertEqual(self.fields("team photo"), [])
        self.assertEqual(self.cause("team photo"), _PICTURE)

    def test_a_page_typed_out_is_told_so_and_not_that_it_is_a_picture(self):
        for name, found in (("team list", []), ("rules sheet", ["time"]),
                            ("fixture list", ["date", "time"]),
                            ("team list with a logo", []),
                            ("team list beside a picture", [])):
            with self.subTest(name=name):
                self.assertEqual(self.count(name, "rects"), 0)
                self.assertEqual(self.fields(name), found)
                self.assertEqual(self.cause(name), _TYPED)

    def test_a_logo_does_not_make_a_card_drawn_in_paths_a_picture(self):
        self.assertEqual(self.fields("card with a logo"), self.fields("genuine"))
        cause = self.cause("card with a logo")
        self.assertTrue(cause.startswith(
            "the page prints a card's words, as the header fields above show, but "
            "paints no rectangle"), cause)

    def test_a_card_printed_over_a_picture_is_told_what_the_card_lacks(self):
        # Its table rules say it is a card, whatever is painted under them.
        cause = self.cause("card over a picture")
        self.assertTrue(cause.startswith(
            "the header line is missing division and round."), cause)

    def test_the_report_says_how_much_of_the_page_its_images_cover(self):
        # The share is rounded down, so that no page is shown a tenth or more
        # that is less, and a mark too small to show is under 1%. A picture off
        # the page, and one on a page of no size, cover none of it.
        for name, row in (("genuine", "none"), ("team list", "none"),
                          ("scan", "1, covering 100% of the page"),
                          ("scan in strips", "2, covering 100% of the page"),
                          ("no size", "1, covering none of the page"),
                          ("team list beside a picture",
                           "1, covering none of the page"),
                          ("printed small", "1, covering 15% of the page"),
                          ("team photo", "1, covering 11% of the page"),
                          ("card with a logo", "1, covering under 1% of the page")):
            with self.subTest(name=name):
                self.assertEqual(self.images(name), row)


class TestUnderRedactTheCausesOfARefusalAreGivenInTheToolsOwnWords(PagesCase):
    """``--redact`` shapes the page's words, and not the advice the tool gives.

    Under ``--redact`` the whole report was scrubbed against the page's words,
    the causes paragraph with it, though nothing in that paragraph is read off
    the page. So a page printing a word the paragraph uses had it shaped out of
    the advice. Measured (/tmp/t6/scratch/m8/probe.py): the draw typed out was
    told a program that saves a card again "can <4 chars> them so", and "A <5
    chars> a rules sheet ... can look like this too", and the rules sheet that
    it is an image of a card "rather <4 chars> a card". The fixture card turned
    180 degrees by /Rotate, with no division and no round, and a comment saying
    the lights went on late, so it went on past ten
    (/tmp/t6/scratch/m8/turned.py), was told to "keep <2 chars> in a folder",
    and in the rotation's note that the page is "<6 chars>, and every
    coordinate <2 chars> <2 chars> with <2 chars>".

    So the paragraph is printed outside the scrub, as the footer is. Each line of
    it is the tool's own: the rotation it quotes is a number of degrees and the
    tool's words, and the fields it names come from a fixed list. The rotation
    row, which says what the note quotes, is kept with it, and so is the images
    row, a count and a share that the picture's cause quotes as "the images line
    above". Measured by this class on a copy of the tree before it was
    (/tmp/t6/scratch/m8/fix/prepass/), the team list under a photograph,
    printing "a covering note", read "1, <8 chars> 11% of the page" there, and
    the rules sheet printing "Byes: none" read "<4 chars>". The detail above the
    paragraph is still scrubbed: it is the reader's, and it quotes the page, the
    team name the banner prints among what it can. The reason, which quotes
    nothing off the page, is kept as written (4.185).

    Rejected: scrubbing the rotation apart from the paragraph and adding it
    after, which shapes "turned", "on" and "it" out of it on that card; and a
    list of words the scrub must never shape, which the next sentence added to
    the paragraph falls outside of.
    """

    TURNED = ("180 degrees -- the page is turned, and every coordinate on it with "
              "it")
    # A team list under a photograph and a rules sheet that paints no image,
    # which print "covering" and "none", as their images rows do.
    JUNIORS = (("Riverside Rockets 2026", 60.0, 80.0),
               ("Juniors need a covering note from a parent", 60.0, 98.0),
               ("Alex Example", 60.0, 116.0), ("Blair Sample", 60.0, 134.0))
    BYES = (("Tuesday night competition rules", 60.0, 80.0),
            ("Byes: none this season.", 60.0, 98.0))

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        # The fixture card turned by /Rotate and drawn turned the other way, so
        # that it reads upright, word for word as it does unturned.
        spec = dataclasses.replace(
            _spec(), division="", round_label="",
            comments="Court lights turned on late, so it went on past ten")
        drawn = (b"q -1 0 0 -1 %d %d cm\n" % (cw.PAGE_WIDTH, cw.PAGE_HEIGHT)
                 + _content(cw.write_card(spec)) + b"\nQ")
        turned = root / "turned.pdf"
        turned.write_bytes(cw._assemble(drawn, with_to_unicode=True).replace(
            b"/Type /Page /Parent", b"/Type /Page /Rotate 180 /Parent", 1))
        rules = TestAPageIsCalledAPictureForTheImageItPaintsAndNotForWhatItLacks
        return {"draw": _text_only(root / "draw.pdf", _DRAW),
                "rules sheet": _text_only(root / "rules.pdf", rules.RULES),
                "turned": turned,
                "no home team": cw.write_card_file(
                    root / "banner.pdf", dataclasses.replace(_spec(), home_name="")),
                "juniors": _pictured(root / "juniors.pdf", _typed(*cls.JUNIORS),
                                     b"250 0 0 240 300 500"),
                "byes": _text_only(root / "byes.pdf", cls.BYES)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redacted = {
            name: subprocess.run((sys.executable, str(DOCTOR), "--redact", str(path)),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            for name, path in cls.paths.items()}

    def causes(self, report: subprocess.CompletedProcess) -> list[str]:
        """The verdict's lines after its reason and its detail."""
        verdict = (report.stdout.partition(f"{doctor.INDENT}verdict\n")[2]
                   .partition("\n\n")[0])
        self.assertIn("This page was refused: ", verdict)
        return [line for line in verdict.splitlines()
                if not line.startswith(("      This page was refused: ",
                                        "      detail: ", " " * 10))]

    @staticmethod
    def unlaid(*argv: str) -> str:
        """The doctor's report on *argv*, in this process, with no line laid out
        to the width: a shape is not the length of what it withholds, so plain
        and redacted go line for line only as they are written (4.186)."""
        buffer = io.StringIO()
        with mock.patch.object(doctor, "WIDTH", sys.maxsize):
            with contextlib.redirect_stdout(buffer):
                doctor.main(list(argv))
        return buffer.getvalue()

    def test_the_causes_are_the_same_words_redacted_or_not(self):
        for name in self.paths:
            with self.subTest(name=name):
                plain = self.causes(self.reports[name])
                self.assertTrue(plain)
                self.assertEqual(self.causes(self.redacted[name]), plain)
                # And the report round them is all there, line for line, above
                # the footer --redact adds.
                body = self.unlaid("--redact", str(self.paths[name])).partition(
                    "\n" + doctor.REDACTION_FOOTER[0])[0]
                self.assertEqual(len(body.splitlines()),
                                 len(self.unlaid(str(self.paths[name])).splitlines()))

    def test_a_card_that_reads_is_published_whole_after_the_row_it_keeps(self):
        # Every page keeps its rotation row, so on a card that reads, all that
        # follows it, the verdict with it, is the report after the last span kept.
        with tempfile.TemporaryDirectory() as root:
            card = cw.write_card_file(Path(root) / "card.pdf", _spec())
            plain, redacted = [
                subprocess.run((sys.executable, str(DOCTOR), *flag, str(card)),
                               capture_output=True, text=True, timeout=TIMEOUT)
                for flag in ((), ("--redact",))]
            unlaid = [self.unlaid(*flag, str(card)) for flag in ((), ("--redact",))]
        self.assertEqual((plain.returncode, redacted.returncode), (0, 0))
        body = redacted.stdout.partition("\n" + doctor.REDACTION_FOOTER[0])[0]
        self.assertEqual(len(unlaid[1].partition(
            "\n" + doctor.REDACTION_FOOTER[0])[0].splitlines()),
            len(unlaid[0].splitlines()))
        self.assertIn("This page reads as a scorecard.", body)

    def test_each_page_prints_a_word_its_causes_use(self):
        # The premise, asked of the doctor's own scrub: shaping this page's words
        # would change the paragraph, on every page here but the last.
        for name in ("draw", "rules sheet", "turned"):
            with self.subTest(name=name):
                scrub = doctor._Redactor()
                scrub.learn_page(pdf.extract(self.paths[name]))
                paragraph = "\n".join(self.causes(self.reports[name]))
                self.assertNotEqual(scrub.scrub(paragraph), paragraph)

    def test_a_turned_page_is_told_its_rotation_in_full(self):
        report = self.redacted["turned"].stdout
        self.assertIn(f"Note: the page declares a rotation ({self.TURNED}).",
                      " ".join(report.split()))
        row = _laid(doctor.INDENT, f"{'rotation':<{doctor.LABEL}}{self.TURNED}",
                    doctor.INDENT + " " * doctor.LABEL)
        self.assertIn(f"\n{row}\n{doctor.INDENT}words ", report)

    def test_the_images_row_is_given_in_full_as_the_causes_quote_it(self):
        for name, row in (("juniors", "1, covering 11% of the page"),
                          ("byes", "none")):
            with self.subTest(name=name):
                line = f"\n{doctor.INDENT}{'images':<{doctor.LABEL}}{row}\n"
                self.assertIn(line, self.reports[name].stdout)
                self.assertIn(line, self.redacted[name].stdout)
                # The premise, asked of the doctor's own scrub: this page prints
                # a word of its row.
                scrub = doctor._Redactor()
                scrub.learn_page(pdf.extract(self.paths[name]))
                self.assertNotEqual(scrub.scrub(line), line)

    def test_nothing_off_the_card_survives_the_paragraph_kept(self):
        values = [MATCH_ID, "18/08/2026", VENUE, *HOME_PLAYERS, *AWAY_PLAYERS]
        values += [word for text in (HOME_TEAM, AWAY_TEAM, VENUE, *CLUB,
                                     *HOME_PLAYERS, *AWAY_PLAYERS)
                   for word in text.split() if word != "TENNIS"]
        for name in ("turned", "no home team"):
            report = self.redacted[name].stdout.casefold()
            for value in values:
                with self.subTest(name=name, value=value):
                    self.assertNotIn(value.casefold(), report)

    def test_the_detail_is_still_scrubbed_where_it_quotes_the_page(self):
        # The premise: the plain detail prints the away team's name.
        self.assertIn(f"detail: largest text above the table: {AWAY_TEAM}",
                      self.reports["no home team"].stdout)
        detail = re.search(r"\n +detail: (.*)\n", self.redacted["no home team"].stdout)
        for word in AWAY_TEAM.split():
            with self.subTest(word=word):
                self.assertNotIn(word, detail[1])


class TestUnderRedactARefusalsReasonIsGivenAsWrittenAndItsDetailIsScrubbed(
        PagesCase):
    """``--redact`` gives the reason a page was refused as the reader wrote it.

    Row 4.185. The reason and its detail were scrubbed with the rest of the
    report, the reason against the day one quoted the page, which none does. So
    a page printing a word the reason uses had it shaped out of the reason, in
    the verdict and in the summary's row for the stage that refused the page,
    which gave the reason and the detail on one line. Measured by
    /tmp/t7/scratch/DRED/probe.py: this file's card turned by /Rotate, with no
    division, no round and a comment that the lights went on late, so it went on
    past ten, was refused as a page on which "no line above the set table names
    both a division and a round, <2 chars> this page cannot be placed in a
    competition", and the card ticking a box nothing reads, with a comment that
    nothing reads a box ticks like that, so it would be odd, as one that "<5
    chars> a checkbox this reader <5 chars> nothing from, <2 chars> <2 chars>
    <5 chars> <2 chars> read as if <4 chars> <3 chars> were not ticked", its
    summary's row reading ``<5 chars>: NOT READ``. (The probe filed that card as
    ``ticked.pdf``, and the path's stem was withheld as a word, row 4.187, so its
    report shaped the last word of the reason as well.)

    The reason is now published as written, in the verdict and in the summary,
    which gives the detail on a line of its own beneath it, and the detail is
    scrubbed, since it quotes the page. That no reason quotes the page is held
    by :class:`TestEveryReasonTheReaderRefusesACardForIsInItsOwnWords`, which
    reads every one the reader raises, and that a page cannot print a line the
    summary gives as a reason by
    :class:`TestUnderRedactALineAPagePrintsInsideAWordIsNotTakenForAReason`.
    Rejected: a reason split into a template and the arguments it is filled
    with, a change to CardError and to every raise for what those hold of each.
    """

    # The stage each page is refused at, as the summary names it.
    STAGES = {"rotated": "header: NOT READ", "sheet": "grid: NOT RESOLVED",
              "unsplit": "banner: NOT READ", "unread": "ticks: NOT READ"}
    # Values off the card, none of which a redacted report may print.
    VALUES = (MATCH_ID, "18/08/2026", *HOME_PLAYERS, *AWAY_PLAYERS,
              *[word for text in (HOME_TEAM, AWAY_TEAM, VENUE, *CLUB)
                for word in text.split() if word != "TENNIS"])

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        # Named for no word of the reasons, and each page printing some of them.
        spec = dataclasses.replace(
            _spec(), division="", round_label="",
            comments="Court lights turned on late, so it went on past ten")
        drawn = (b"q -1 0 0 -1 %d %d cm\n" % (cw.PAGE_WIDTH, cw.PAGE_HEIGHT)
                 + _content(cw.write_card(spec)) + b"\nQ")
        rotated = root / "rotated.pdf"
        rotated.write_bytes(cw._assemble(drawn, with_to_unicode=True).replace(
            b"/Type /Page /Parent", b"/Type /Page /Rotate 180 /Parent", 1))
        rules = TestAPageIsCalledAPictureForTheImageItPaintsAndNotForWhatItLacks
        return {"rotated": rotated,
                "sheet": _text_only(root / "sheet.pdf", rules.RULES),
                "unsplit": cw.write_card_file(root / "unsplit.pdf", dataclasses.replace(
                    _spec(), home_name="",
                    comments="Courts could not be told apart, so the middle one "
                             "was used")),
                "unread": written_with_a_box_more(
                    root / "unread.pdf", dataclasses.replace(
                        _spec(), comments="Nothing reads a box ticks like that, so "
                                          "it would be odd"), ticked=True)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redacted = {
            name: subprocess.run((sys.executable, str(DOCTOR), "--redact", str(path)),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            for name, path in cls.paths.items()}

    def refusal(self, name: str) -> scorecard.CardError:
        with self.assertRaises(scorecard.CardError) as refused:
            scorecard.read_page(pdf.extract(self.paths[name]))
        return refused.exception

    def changed_by_the_scrub(self, name: str, text: str) -> bool:
        """Whether the doctor's scrub, learning page ``name``, would change ``text``."""
        scrub = doctor._Redactor()
        scrub.learn_page(pdf.extract(self.paths[name]))
        return scrub.scrub(text) != text

    def test_the_verdict_gives_the_reason_as_the_reader_wrote_it(self):
        for name in self.paths:
            with self.subTest(name=name):
                line = "\n" + _laid(f"{doctor.INDENT}  ", "This page was refused: "
                                     f"{self.refusal(name).reason}",
                                     f"{doctor.INDENT}      ") + "\n"
                self.assertTrue(self.changed_by_the_scrub(name, line))
                self.assertIn(line, self.reports[name].stdout)
                self.assertIn(line, self.redacted[name].stdout)

    def test_the_summary_gives_the_stage_that_refused_it_as_the_reader_wrote_it(self):
        for name, stage in self.STAGES.items():
            with self.subTest(name=name):
                line = "\n" + _laid(f"{doctor.INDENT}  ",
                                     f"{stage} - {self.refusal(name).reason}",
                                     f"{doctor.INDENT}      ") + "\n"
                self.assertTrue(self.changed_by_the_scrub(name, line))
                self.assertIn(line, self.reports[name].stdout)
                self.assertIn(line, self.redacted[name].stdout)

    def test_the_summary_gives_the_detail_on_a_line_of_its_own(self):
        for name, stage in self.STAGES.items():
            with self.subTest(name=name):
                refused = self.refusal(name)
                self.assertTrue(refused.detail)
                self.assertIn("\n" + _laid(f"{doctor.INDENT}  ",
                                            f"{stage} - {refused.reason}",
                                            f"{doctor.INDENT}      ") + "\n"
                              + _laid(f"{doctor.INDENT}    ",
                                      f"detail: {refused.detail}",
                                      f"{doctor.INDENT}        ") + "\n",
                              self.reports[name].stdout)

    def test_the_detail_and_the_rest_of_the_report_are_still_scrubbed(self):
        # The premise: the detail quotes the page, the closest line the card's id
        # and the banner the away team's name, in the verdict and in the summary.
        for name, quoted in (("rotated", MATCH_ID), ("unsplit", AWAY_TEAM)):
            with self.subTest(name=name):
                detail = self.refusal(name).detail
                self.assertIn(quoted, detail)
                self.assertEqual(self.reports[name].stdout.count(detail), 2)
        for name in ("rotated", "unsplit", "unread"):
            report = self.redacted[name].stdout.casefold()
            for value in self.VALUES:
                with self.subTest(name=name, value=value):
                    self.assertNotIn(value.casefold(), report)


class TestUnderRedactALineAPagePrintsInsideAWordIsNotTakenForAReason(PagesCase):
    """A name that prints a line of the summary is scrubbed with the rest.

    Row 4.185. The summary's row for a stage that refused the page is published
    under ``--redact`` as the reader wrote it, and the doctor told that row from
    the rest of the summary by how it began, ``ticks: NOT READ - `` and the like.
    A page's ToUnicode map can read one glyph as text with a line break in it,
    and the reader keeps that text in the word, so a name printing one could begin
    a line of the summary with those words. Measured by
    /tmp/t7/scratch/DRED/fix/forge_pdf.py on this file's card, its home captain
    printed ``Priya~ Halloran`` with ``~`` read as a line break and ``ticks: NOT
    READ -``: the card read, and its redacted report published "ticks: NOT READ -
    Halloran (C) [2.34/4.23], Desmond Okwuosa [3.11/3.98]" as written.

    The reader now says which lines are reasons, as a :class:`scorecard.Reason`,
    and the doctor publishes those and scrubs the rest, whatever they say.
    Rejected: telling a reason by more of its text, the whole reason the stage
    raised, say, which a page can print as well; and breaking a word at a line
    break it prints, a change to what the reader reads for what the doctor prints.
    """

    # How the summary begins a stage's reason, each printed by a card below.
    STAGES = ("grid: NOT RESOLVED", "header: NOT READ", "banner: NOT READ",
              "ticks: NOT READ")
    NAME = "Priya~ Halloran"
    # The home roster's names, a word at a time, and its ratings.
    VALUES = (*" ".join(HOME_PLAYERS).split(), "2.34/4.23", "3.11/3.98")

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        spec = _spec()
        spec = dataclasses.replace(spec, home_roster=(
            dataclasses.replace(spec.home_roster[0], name=cls.NAME),
            *spec.home_roster[1:]))
        paths = {}
        for index, stage in enumerate(cls.STAGES):
            read_as = f"\n{stage} -".encode("utf-16-be").hex().upper().encode()
            cmap = cw._TO_UNICODE_CMAP.replace(
                b"<20> <7E> <0020>\nendbfrange\n",
                b"<20> <7D> <0020>\nendbfrange\n1 beginbfchar\n<7E> <" + read_as
                + b">\nendbfchar\n")
            assert cmap != cw._TO_UNICODE_CMAP
            with mock.patch.object(cw, "_TO_UNICODE_CMAP", cmap):
                paths[stage] = cw.write_card_file(root / f"{index}.pdf", spec)
        return paths

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redacted = {
            name: subprocess.run((sys.executable, str(DOCTOR), "--redact", str(path)),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            for name, path in cls.paths.items()}

    def test_the_card_reads_and_prints_the_line_in_its_captains_name(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                card = scorecard.read_page(pdf.extract(self.paths[stage]))
                self.assertEqual(card.home_roster[0].name,
                                 f"Priya\n{stage} - Halloran")
                self.assertEqual(self.reports[stage].returncode, 0)
                self.assertIn(f"\n{doctor.INDENT}  {stage} - Halloran (C) [2.34/4.23]",
                              self.reports[stage].stdout)

    def test_no_name_on_the_line_is_published(self):
        for stage in self.STAGES:
            report = self.redacted[stage].stdout
            self.assertEqual(self.redacted[stage].returncode, 0, report[-2000:])
            for value in self.VALUES:
                with self.subTest(stage=stage, value=value):
                    self.assertEqual(
                        [line for line in report.splitlines() if value in line], [])

    def test_the_summary_says_no_stage_refused_the_card(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                reasons = [line for line in scorecard.describe_lines(
                    pdf.extract(self.paths[stage]))
                    if isinstance(line, scorecard.Reason)]
                self.assertEqual(reasons, [])


# What a refusal's reason may interpolate, read from the source by
# :func:`_refusals` for :class:`TestEveryReasonTheReaderRefusesACardForIsInItsOwnWords`.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


# What a parameter a position is read from is declared to be.
_DRAWN = frozenset(f"{module}{kind}" for module in ("", "pdf.")
                   for kind in ("Word", "Rect", "Page"))


def _refusal_geometry() -> frozenset[str]:
    """The fields and properties of a word, a rect or a page that are numbers."""
    names = set()
    for kind in (pdf.Word, pdf.Rect, pdf.Page):
        names.update(field.name for field in dataclasses.fields(kind)
                     if field.type in ("float", "int"))
        names.update(name for name, member in vars(kind).items()
                     if isinstance(member, property)
                     and member.fget.__annotations__.get("return") in ("float", "int"))
    return frozenset(names)


def _named(node: ast.AST) -> str | None:
    return (node.id if isinstance(node, ast.Name)
            else node.attr if isinstance(node, ast.Attribute) else None)


def _bindings(scope: ast.AST) -> Counter:
    """Every name bound in ``scope``, counted; a global or nonlocal counts twice."""
    bound = Counter()
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound[node.id] += 1
        elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
              and node is not scope):
            bound[node.name] += 1
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound[node.name] += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound[(alias.asname or alias.name).split(".")[0]] += 1
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                bound[name] += 2
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound[node.rest] += 1
    return bound


def _refusals(sources: dict[str, str], namespace, geometry: frozenset[str]):
    """``(problems, raised, reached)`` for every refusal raised in ``sources``.

    ``sources`` maps a file's path to its text, and ``namespace(path)`` gives the
    module's names as it runs. A refusal is CardError or a class descended from
    it; ``raised`` is where each is raised, ``reached`` the kinds raised, and
    ``problems`` each raise whose reason this cannot vouch for, and each use of
    a refusal it cannot follow to a raise.
    """
    trees = {rel: ast.parse(text) for rel, text in sources.items()}
    # A name any file may call a function by that is not its own: an attribute,
    # or a name imported, a star import importing every one.
    elsewhere = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                elsewhere.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                elsewhere.update(alias.name for alias in node.names)
    classes = [(rel, node) for rel, tree in trees.items()
               for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    family, grown = {"CardError"}, True
    while grown:
        grown = False
        for _, node in classes:
            if node.name not in family and any(_named(b) in family for b in node.bases):
                family.add(node.name)
                grown = True
    problems, raised, reached = [], [], set()
    for rel, node in classes:
        if node.name in family - {"CardError"}:
            extra = [statement for statement in node.body if not (
                isinstance(statement, ast.Pass)
                or (isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)))]
            if extra:
                problems.append(f"{rel}:{extra[0].lineno}: class {node.name} is a kind "
                                f"of refusal with more than a docstring, so this "
                                f"test cannot tell what reason it gives")
    for rel, tree in trees.items():
        _refusals_in(rel, tree, family, lambda rel=rel: namespace(rel), geometry,
                     elsewhere, problems, raised, reached)
    return problems, raised, reached


def _refusals_in(rel, tree, family, namespace, geometry, elsewhere, problems,
                 raised, reached):
    """:func:`_refusals` of one file."""
    parent = {child: node for node in ast.walk(tree)
              for child in ast.iter_child_nodes(node)}

    def scope_of(node):
        while node in parent:
            node = parent[node]
            if isinstance(node, _SCOPES):
                return node
        return tree

    everywhere = _bindings(tree)
    top = set()
    for statement in tree.body:
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)):
            top.add(statement.targets[0].id)
        elif (isinstance(statement, ast.AnnAssign) and statement.value is not None
              and isinstance(statement.target, ast.Name)):
            top.add(statement.target.id)
    functions = {node.name: node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    cache = {}

    def scope_info(scope):
        """The parameters of ``scope`` by name, and the names bound in it."""
        if scope not in cache:
            params = {}
            if isinstance(scope, _SCOPES):
                a = scope.args
                for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                    if arg is not None:
                        params[arg.arg] = arg
            cache[scope] = (params,
                            _bindings(scope) if scope is not tree else everywhere)
        return cache[scope]

    def assigned(name, scope):
        """The value ``name`` is bound to in ``scope``, if bound there once by `=`."""
        params, bound = scope_info(scope)
        if bound[name] != 1 or name in params:
            return None
        for node in ast.walk(scope):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == name and scope_of(node) is scope):
                return node.value
            if (isinstance(node, ast.AnnAssign) and node.value is not None
                    and isinstance(node.target, ast.Name)
                    and node.target.id == name and scope_of(node) is scope):
                return node.value
        return None

    def constant(name):
        """Whether ``name`` is bound once, atop the module, to text or a number."""
        if everywhere[name] != 1 or name not in top:
            return False
        return isinstance(namespace().get(name), (str, int, float))

    def called(function):
        """Every call of a function of the module's own, if it is only called, by
        its name, in this file; ``None`` if it is anything else."""
        if (everywhere[function.name] != 1 or function.decorator_list
                or function.name in elsewhere or "*" in elsewhere):
            return None
        uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                and node.id == function.name and isinstance(node.ctx, ast.Load)]
        if not all(isinstance(parent[use], ast.Call) and parent[use].func is use
                   for use in uses):
            return None
        return [parent[use] for use in uses]

    def passed(name, scope, seen):
        """Whether parameter ``name`` of ``scope``, never bound again, is passed
        what this vouches for by every call there is of ``scope``."""
        params, bound = scope_info(scope)
        key = ("passed", id(scope), name)
        if (bound[name] or functions.get(getattr(scope, "name", None)) is not scope
                or key in seen):
            return False
        calls = called(scope)
        if not calls:
            return False
        a = scope.args
        positional = [arg.arg for arg in a.posonlyargs + a.args]
        for call in calls:
            # Arguments unpacked can move it; keywords unpacked cannot give it
            # a value an argument gives, and with no argument it fails.
            if any(isinstance(arg, ast.Starred) for arg in call.args):
                return False
            at = positional.index(name) if name in positional else len(call.args)
            given = (call.args[at] if at < len(call.args) else
                     next((k.value for k in call.keywords if k.arg == name), None))
            if given is None or unvouched(given, scope_of(call), seen | {key}):
                return False
        return True

    def counter(name, scope, seen):
        """Whether ``name`` is bound in ``scope`` once, to the count that
        ``enumerate`` gives from a start this vouches for."""
        params, bound = scope_info(scope)
        if bound[name] != 1:
            return False
        for node in ast.walk(scope):
            if (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
                    and isinstance(node.target, ast.Tuple) and node.target.elts
                    and isinstance(node.target.elts[0], ast.Name)
                    and node.target.elts[0].id == name
                    and scope_of(node.target) is scope):
                count = node.iter
                return (isinstance(count, ast.Call) and isinstance(count.func, ast.Name)
                        and count.func.id == "enumerate"
                        and not everywhere["enumerate"] and "enumerate" not in params
                        and not any(isinstance(arg, ast.Starred) for arg in count.args)
                        and not any(unvouched(start, scope, seen) for start in
                                    count.args[1:] + [k.value for k in count.keywords]))
        return False

    def counts(name, seen):
        """Whether ``name`` is a function of the module's own that returns what
        this vouches for and nothing else."""
        function = functions.get(name)
        key = ("counts", name)
        if (function is None or everywhere[name] != 1 or function.decorator_list
                or key in seen):
            return False
        returns = [node for node in ast.walk(function)
                   if isinstance(node, ast.Return) and scope_of(node) is function]
        return bool(returns) and not any(
            node.value is None or unvouched(node.value, function, seen | {key})
            for node in returns)

    def drawn(node, scope):
        """Whether ``node`` is a parameter of ``scope`` declared a word, a rect or
        a page, and never bound again."""
        params, bound = scope_info(scope)
        name = node.id if isinstance(node, ast.Name) else None
        note = params[name].annotation if name in params else None
        return note is not None and ast.unparse(note) in _DRAWN and not bound[name]

    def members(node):
        """The kinds of refusal ``node`` is, if it is nothing else."""
        if _named(node) in family:
            return {_named(node)}
        if isinstance(node, ast.IfExp):
            body, orelse = members(node.body), members(node.orelse)
            return body | orelse if body and orelse else set()
        return set()

    def unvouched(node, scope, seen=frozenset()):
        """The first part of ``node`` not a count, position, index or constant."""
        if isinstance(node, ast.Constant):
            return None if isinstance(node.value, (str, int, float)) else node
        if isinstance(node, ast.JoinedStr):
            return next(filter(None, (unvouched(v, scope, seen) for v in node.values)),
                        None)
        if isinstance(node, ast.FormattedValue):
            return (unvouched(node.value, scope, seen)
                    or (node.format_spec and unvouched(node.format_spec, scope, seen)))
        if isinstance(node, ast.BinOp):
            return (unvouched(node.left, scope, seen)
                    or unvouched(node.right, scope, seen))
        if isinstance(node, ast.UnaryOp):
            return unvouched(node.operand, scope, seen)
        if isinstance(node, ast.IfExp):
            # Which of two wordings is said, and not what the choice was made on.
            return (unvouched(node.body, scope, seen)
                    or unvouched(node.orelse, scope, seen))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            params, bound = scope_info(scope)
            local = name in params or bound[name]
            if not local and (name == "len" and not everywhere[name]
                              or counts(name, seen)):
                return None
            return node
        if isinstance(node, ast.Attribute):
            return (None if node.attr in geometry and drawn(node.value, scope)
                    else node)
        if isinstance(node, ast.Name):
            params, bound = scope_info(scope)
            if (id(scope), node.id) in seen:
                return node
            if node.id in params:
                return None if passed(node.id, scope, seen) else node
            if scope is not tree and bound[node.id]:
                if counter(node.id, scope, seen):
                    return None
                value = assigned(node.id, scope)
                if (value is None
                        or unvouched(value, scope, seen | {(id(scope), node.id)})):
                    return node
                return None
            return None if constant(node.id) else node
        return node

    def where(node):
        scope = scope_of(node)
        name = ("<module>" if scope is tree
                else "<lambda>" if isinstance(scope, ast.Lambda) else scope.name)
        return f"{rel}:{node.lineno} in {name}"

    kept = set()          # the nodes a refusal may be named in without being raised
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            kept.update(id(base) for base in node.bases)
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            kept.update(id(n) for n in ast.walk(node.type))
        elif isinstance(node, ast.arg) and node.annotation is not None:
            kept.update(id(n) for n in ast.walk(node.annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            kept.update(id(n) for n in ast.walk(node.returns))
        elif isinstance(node, ast.AnnAssign):
            kept.update(id(n) for n in ast.walk(node.annotation))
        elif (isinstance(node, ast.Call) and len(node.args) == 2
              and _named(node.func) in ("isinstance", "issubclass")):
            kept.update(id(n) for n in ast.walk(node.args[1]))
        elif (isinstance(node, ast.Assign) and members(node.value)
              and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            kept.update(id(n) for n in ast.walk(node.value))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "reason"
                and not isinstance(node.ctx, ast.Load)):
            scope = scope_of(node)
            owner = parent.get(scope) if scope is not tree else None
            value = parent[node].value if isinstance(parent[node], ast.Assign) else None
            if not (isinstance(owner, ast.ClassDef) and owner.name == "CardError"
                    and isinstance(value, ast.Name) and value.id == "reason"):
                problems.append(f"{where(node)}: a refusal's reason is set other than "
                                f"by raising it, so this test cannot read it")
        if (isinstance(node, (ast.Name, ast.Attribute))
                and isinstance(node.ctx, ast.Load)
                and _named(node) in family and id(node) not in kept
                and not (isinstance(parent[node], ast.Call)
                         and parent[node].func is node)):
            problems.append(f"{where(node)}: {_named(node)} is used other than to "
                            f"raise, catch or name a refusal, so this test cannot "
                            f"see the reason it is given")
        if not isinstance(node, ast.Call):
            continue
        scope = scope_of(node)
        kinds = members(node.func)
        if not kinds and isinstance(node.func, ast.Name) and scope is not tree:
            value = assigned(node.func.id, scope)
            kinds = members(value) if value is not None else set()
        if not kinds:
            continue
        reached.update(kinds)
        raised.append(where(node))
        reason = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg == "reason"), None)
        if (reason is None or isinstance(reason, ast.Starred)
                or any(k.arg is None for k in node.keywords)):
            problems.append(f"{where(node)}: {ast.unparse(node.func)} is raised with a "
                            f"reason this test cannot read")
            continue
        bad = unvouched(reason, scope)
        if bad is not None:
            problems.append(f"{where(node)}: the reason interpolates "
                            f"{ast.unparse(bad)}, which this test cannot tell is a "
                            f"count, a position, an index or a module constant")


def _shipped_namespace(rel: str) -> dict:
    """The names a shipped module has as it runs; none for one this cannot import."""
    if rel.startswith("matchcentre/"):
        return vars(importlib.import_module(
            ".".join(Path(rel).with_suffix("").parts)))
    return vars(doctor) if rel == "tools/doctor.py" else {}


class TestEveryReasonTheReaderRefusesACardForIsInItsOwnWords(unittest.TestCase):
    """No reason a page is refused for quotes the page, and a new one cannot.

    Row 4.185. ``--redact`` now publishes a refusal's reason as written, which
    is safe only while no reason quotes the page: the detail is for what the
    reader saw. This reads every refusal raised in what ships, from the source,
    and fails a reason that interpolates anything but a count (``len``), a
    position or a size (a numeric field of a parameter declared a word, a rect
    or a page), an index (the count ``enumerate`` gives), text or a number bound
    once at the top of its module, or what is made of those alone: a name bound
    once to one, a function of the module's own that returns nothing else, and
    a parameter every call passes one, the function called by its name in its
    own file alone, and named as an attribute or imported in none. It fails
    closed: what it cannot follow, a kind with a body, a refusal stored or
    passed, a reason set afterwards or given unpacked, a parameter no call
    passes, fails, and the failure names the raise. Measured on this tree: 23
    raises in matchcentre/scorecard.py, of CardError and its four kinds, none
    failing; the reasons interpolate ``len(...)`` twice, ``_IDENTITY_WORDING``,
    counts bound once, ``_expected_boxes(rows)`` and the index of a marker,
    which ``read_page`` and ``describe_lines`` count with ``enumerate`` and pass
    through ``_read_row``. Before, a parameter was vouched for by being declared
    ``int`` or ``float`` and a function by being declared ``-> int``, so a
    rating passed as a float, or a match id through a function declared to
    give an int, passed (/tmp/t7/scratch/DRED/fix/guard_hole.py). Rejected: a
    reason split into a template and the arguments it is filled with, a change
    to CardError and to every raise for what this holds; and a list of the
    parameters that are indexes, which a new raise would have to be added to.
    """

    # The reader's CardError, as :mod:`matchcentre.card` defines it.
    CARD = ("class CardError(Exception):\n"
            "    def __init__(self, reason, *, detail=''):\n"
            "        super().__init__(reason)\n"
            "        self.reason = reason\n"
            "        self.detail = detail\n")
    KINDS = ("class Unplaced(CardError):\n"
             "    '''A kind with a docstring.'''\n"
             "class Unnamed(CardError):\n"
             "    pass\n")
    EXAMPLE = "matchcentre/example.py"
    CANNOT = ("which this test cannot tell is a count, a position, an index or a "
              "module constant")
    UNREAD = "CardError is raised with a reason this test cannot read"
    # A reason this cannot vouch for, and the one thing it says of it.
    UNVOUCHED = {
        "a word of the page":
            ("def f(word):\n    raise CardError(f'no {word.text} here')\n",
             f"{EXAMPLE}:2 in f: the reason interpolates word.text, {CANNOT}"),
        "a parameter no call passes":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter a call passes a word of the page":
            ("def f(name):\n    raise CardError('no ' + name)\n"
             "def g(word):\n    f(word.text)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates name, {CANNOT}"),
        "a number a call reads off the page":
            ("def f(rating: float):\n    raise CardError(f'a rating of {rating:.2f}')\n"
             "def g(rows):\n    f(1.0)\ndef h(entry):\n    f(entry.singles)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates rating, {CANNOT}"),
        "a parameter passed by keyword a word of the page":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(word):\n    f(index=word.text)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter a call leaves to its default":
            ("def f(index: int = 0):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f()\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter a call passes after arguments unpacked":
            ("def f(rows, index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(said):\n    f(*said, 0)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter a call passes in keywords unpacked":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(said):\n    f(**said)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter a function passes itself":
            ("def f(index: int):\n    if index:\n        f(index - 1)\n"
             "    raise CardError(f'row {index + 1}')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates index, {CANNOT}"),
        "a parameter of a function named other than to call it":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f(0)\nCALLBACKS = [f]\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter of a function another module may call":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f(0)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}",
             {"matchcentre/other.py":
                  "from matchcentre import example\n"
                  "def h(word):\n    example.f(word.text)\n"}),
        "a parameter of a function another module imports":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f(0)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}",
             {"matchcentre/other.py": "from matchcentre.example import f\n"}),
        "a parameter of a function any module may import":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f(0)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}",
             {"matchcentre/other.py": "from matchcentre.example import *\n"}),
        "a parameter of a function bound again":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f(0)\nf = print\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a parameter of a function decorated":
            ("@staticmethod\ndef f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g():\n    f(0)\n",
             f"{EXAMPLE}:3 in f: the reason interpolates index, {CANNOT}"),
        "a parameter of a function defined in another":
            ("def g(rows):\n    def f(index: int):\n"
             "        raise CardError(f'row {index + 1}')\n    f(0)\n",
             f"{EXAMPLE}:3 in f: the reason interpolates index, {CANNOT}"),
        "a count started at a word of the page":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(rows, word):\n    for index, row in enumerate(rows, int(word.text)):\n"
             "        f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a count bound again after enumerate gives it":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(rows):\n    for index, row in enumerate(rows):\n"
             "        index = row.text\n        f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a count started where a call unpacks":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(said):\n    for index, row in enumerate(*said):\n"
             "        f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "a name enumerate counts in a function inside":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(rows):\n    def h():\n        for index, row in enumerate(rows):\n"
             "            pass\n    f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "the first of what zip gives":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(words):\n    for index, mark in zip(words, 'abc'):\n"
             "        f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "an enumerate passed in":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(rows, enumerate):\n    for index, row in enumerate(rows):\n"
             "        f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates index, {CANNOT}"),
        "an enumerate of the module's own":
            ("def enumerate(rows):\n    return [(row.text, row) for row in rows]\n"
             "def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(rows):\n    for index, row in enumerate(rows):\n        f(index)\n",
             f"{EXAMPLE}:4 in f: the reason interpolates index, {CANNOT}"),
        "the second of what enumerate gives":
            ("def f(name):\n    raise CardError(f'no {name}')\n"
             "def g(words):\n    for name, index in enumerate(words):\n"
             "        f(index)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates name, {CANNOT}"),
        "a kind chosen by a name, raised quoting the page":
            (KINDS + "def f(word, few):\n    kind = Unplaced if few else Unnamed\n"
                     "    return kind(f'{word.text}')\n",
             f"{EXAMPLE}:7 in f: the reason interpolates word.text, {CANNOT}"),
        "a refusal kept in a list":
            ("REFUSALS = [CardError]\n",
             f"{EXAMPLE}:1 in <module>: CardError is used other than to raise, catch "
             f"or name a refusal, so this test cannot see the reason it is given"),
        "a kind that makes its own reason":
            ("class Named(CardError):\n    def __init__(self, word):\n"
             "        super().__init__(word.text)\n",
             f"{EXAMPLE}:2: class Named is a kind of refusal with more than a "
             f"docstring, so this test cannot tell what reason it gives"),
        "a reason set after the refusal is made":
            ("def f(word):\n    exc = CardError('no table')\n"
             "    exc.reason = word.text\n    raise exc\n",
             f"{EXAMPLE}:3 in f: a refusal's reason is set other than by raising it, "
             f"so this test cannot read it"),
        "a reason unpacked":
            ("def f(said):\n    raise CardError(*said)\n",
             f"{EXAMPLE}:2 in f: {UNREAD}"),
        "keywords unpacked":
            ("def f(said):\n    raise CardError('no table', **said)\n",
             f"{EXAMPLE}:2 in f: {UNREAD}"),
        "no reason":
            ("def f(word):\n    raise CardError(detail=word.text)\n",
             f"{EXAMPLE}:2 in f: {UNREAD}"),
        "a reason given by keyword":
            ("def f(word):\n    raise CardError(reason=word.text)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates word.text, {CANNOT}"),
        "an index bound again":
            ("def f(index: int, word):\n    index = word.text\n"
             "    raise CardError(f'row {index}')\n"
             "def g(rows):\n    for index, row in enumerate(rows):\n        f(index, row)\n",
             f"{EXAMPLE}:3 in f: the reason interpolates index, {CANNOT}"),
        "a name bound to itself":
            ("def f():\n    count = count + 1\n    raise CardError(f'{count} rows')\n",
             f"{EXAMPLE}:3 in f: the reason interpolates count, {CANNOT}"),
        "a count bound again":
            ("def f(rows, word):\n    count = len(rows)\n    if word:\n"
             "        count = word.text\n    raise CardError(f'{count} rows')\n",
             f"{EXAMPLE}:5 in f: the reason interpolates count, {CANNOT}"),
        "a count bound to the page":
            ("def f(word):\n    count = word.text\n"
             "    raise CardError(f'{count} rows')\n",
             f"{EXAMPLE}:3 in f: the reason interpolates count, {CANNOT}"),
        "a module constant bound again":
            ("_WORDING = 'a division'\ndef g(word):\n    global _WORDING\n"
             "    _WORDING = word.text\ndef f():\n"
             "    raise CardError('no ' + _WORDING)\n",
             f"{EXAMPLE}:6 in f: the reason interpolates _WORDING, {CANNOT}"),
        "a len of the module's own":
            ("def len(word):\n    return word.text\n"
             "def f(word):\n    raise CardError(f'{len(word)} rows')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates len(word), {CANNOT}"),
        "a module name holding neither text nor a number":
            ("_SEEN = []\ndef f():\n    raise CardError(f'no {_SEEN}')\n",
             f"{EXAMPLE}:3 in f: the reason interpolates _SEEN, {CANNOT}"),
        "a function giving text":
            ("def g(rows: int) -> str:\n    return str(rows)\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates g(rows), {CANNOT}"),
        "a function declared to give a number, giving the page's":
            ("def g(header) -> int:\n    return int(header.match_id)\n"
             "def f(header):\n    raise CardError(f'match {g(header)}')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates g(header), {CANNOT}"),
        "a function giving what a call passes it off the page":
            ("def g(rows: int) -> int:\n    return rows\n"
             "def f(word):\n    raise CardError(f'{g(int(word.text))} rows')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates g(int(word.text)), {CANNOT}"),
        "a function giving nothing on one path":
            ("def g(rows) -> int:\n    if rows:\n        return len(rows)\n    return\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n",
             f"{EXAMPLE}:6 in f: the reason interpolates g(rows), {CANNOT}"),
        "a function returning nothing":
            ("def g(rows) -> int:\n    pass\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates g(rows), {CANNOT}"),
        "a function that only calls itself":
            ("def g(rows) -> int:\n    return g(rows)\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n",
             f"{EXAMPLE}:4 in f: the reason interpolates g(rows), {CANNOT}"),
        "a function decorated":
            ("@staticmethod\ndef g(rows) -> int:\n    return len(rows)\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n",
             f"{EXAMPLE}:5 in f: the reason interpolates g(rows), {CANNOT}"),
        "a function bound again":
            ("def g(rows) -> int:\n    return len(rows)\ng = str\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n",
             f"{EXAMPLE}:5 in f: the reason interpolates g(rows), {CANNOT}"),
        "a position of what is declared nothing":
            ("def f(rect):\n    raise CardError(f'at x={rect.x0:.1f}')\n",
             f"{EXAMPLE}:2 in f: the reason interpolates rect.x0, {CANNOT}"),
        "a position of what is not a word, a rect or a page":
            ("def f(header: Header):\n    raise CardError(f'at x={header.x0:.1f}')\n",
             f"{EXAMPLE}:2 in f: the reason interpolates header.x0, {CANNOT}"),
        "a position of a rect bound again":
            ("def f(rect: pdf.Rect, header):\n    rect = header\n"
             "    raise CardError(f'at x={rect.x0:.1f}')\n",
             f"{EXAMPLE}:3 in f: the reason interpolates rect.x0, {CANNOT}"),
        "a position of a rect's attribute":
            ("def f(rect: pdf.Rect):\n    raise CardError(f'at x={rect.owner.x0:.1f}')\n",
             f"{EXAMPLE}:2 in f: the reason interpolates rect.owner.x0, {CANNOT}"),
        "a refusal named through its module":
            ("def f(word):\n    raise card.CardError(word.text)\n",
             f"{EXAMPLE}:2 in f: the reason interpolates word.text, {CANNOT}"),
        "a refusal made in a lambda":
            ("f = lambda word: CardError(word.text)\n",
             f"{EXAMPLE}:1 in <lambda>: the reason interpolates word.text, {CANNOT}"),
    }
    # A reason in the reader's own words, and how many refusals it raises.
    VOUCHED = {
        "text": ("def f():\n    raise CardError('no table rules')\n", 1),
        "a count": ("def f(rows):\n    raise CardError(f'{len(rows)} rows')\n", 1),
        "a count bound once":
            ("def f(rows):\n    count = len(rows) + 1\n"
             "    raise CardError(f'{count} rows')\n", 1),
        "a count a function gives":
            ("def g(rows: int) -> int:\n    return rows * 2\n"
             "def f(rows):\n    raise CardError(f'{g(len(rows))} boxes')\n", 1),
        "a position":
            ("def f(rect: pdf.Rect, word: Word):\n"
             "    raise CardError(f'at x={rect.x0:.1f}, {word.y0:.1f}')\n", 1),
        "an index":
            ("def f(index: int):\n    raise CardError(f'row {index + 1}')\n"
             "def g(rows):\n    for index, row in enumerate(rows):\n        f(index)\n",
             1),
        "a count a function gives, whatever a function inside it gives":
            ("def g(rows) -> int:\n    def h():\n        return rows.text\n"
             "    return len(rows)\n"
             "def f(rows):\n    raise CardError(f'{g(rows)} rows')\n", 1),
        "an index passed on by keyword from a count":
            ("def f(index: int, rows):\n    raise CardError(f'row {index + 1}')\n"
             "def g(index: int, rows):\n    return f(rows=rows, index=index)\n"
             "def h(rows):\n    return [g(index, row) for index, row"
             " in enumerate(rows, start=1)]\n", 1),
        "a module constant":
            ("_WORDING = 'a division'\ndef f():\n"
             "    raise CardError('no ' + _WORDING)\n", 1),
        "a choice of two wordings":
            ("def f(few):\n"
             "    raise CardError(f\"{'too few' if few else 'too many'}\")\n", 1),
        "a choice made on the page":
            ("def f(word):\n"
             "    raise CardError('named' if word.text else 'unnamed')\n", 1),
        "a kind chosen by a name, its detail quoting the page":
            (KINDS + "def f(word, few):\n    kind = Unplaced if few else Unnamed\n"
                     "    return kind('no box named', detail=word.text)\n", 1),
        "a refusal caught, tested for and declared":
            ("def f(word) -> CardError:\n    try:\n        pass\n"
             "    except (CardError, ValueError) as exc:\n"
             "        return isinstance(exc, CardError)\n", 0),
    }

    def swept(self, source: str, others: dict[str, str] | None = None):
        return _refusals({"matchcentre/card.py": self.CARD, self.EXAMPLE: source,
                          **(others or {})},
                         lambda rel: {"_WORDING": "a division", "_SEEN": []}
                         if rel == self.EXAMPLE else {}, _refusal_geometry())

    def test_no_reason_the_reader_gives_quotes_what_it_cannot_vouch_for(self):
        sources = {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
                   for path in python_sources()}
        problems, raised, reached = _refusals(sources, _shipped_namespace,
                                              _refusal_geometry())
        self.assertEqual(problems, [])
        # The premise: the sweep reads the raises of every kind of refusal.
        self.assertGreaterEqual(len(raised), 23)
        self.assertLessEqual({"CardError", "UnlocatedCheckboxes",
                              "UnlabelledCheckboxes", "MissingCheckboxes",
                              "UnreadTick"}, reached)

    def test_a_reason_it_cannot_vouch_for_is_failed_where_it_is_raised(self):
        for case, (source, said, *others) in self.UNVOUCHED.items():
            with self.subTest(case=case):
                self.assertEqual(self.swept(source, *others)[0], [said])

    def test_a_reason_in_the_readers_own_words_is_passed(self):
        for case, (source, many) in self.VOUCHED.items():
            with self.subTest(case=case):
                problems, raised, _ = self.swept(source)
                self.assertEqual(problems, [])
                self.assertEqual(len(raised), many)


class TestADateIsFoundAndWithheldInEveryFormACardPrintsIt(unittest.TestCase):
    """The doctor knew one shape of date, ``dd/mm/yyyy``, and no other.

    Measured on this file's own card restamped once per form
    (``/tmp/t4/scratch/i416b/probe_doctor3.py``): the header field said the date
    was "not found" on cards printing ``18-08-2026``, ``18.08.2026``, ``18 August
    2026``, ``18 Aug 2026`` and ``2026-08-18``, which the reader dates -- the doctor
    contradicting the reader in the one direction its looser patterns exist to rule
    out. Once the reader read those forms its warnings quoted a date it could not
    use, and ``--redact`` withheld only slashes and ISO: ``08-18-2026``,
    ``08.18.26`` and ``32-13-2026`` were printed whole, and ``31 February 2026``
    as its day and year either side of a shaped month. And the verdict set the
    printed weekday beside the date as the date's, so a card dated a Tuesday that
    prints ``Wed`` read ``2026-08-18 Wednesday``.

    One pattern now finds a date for the header field and withholds it under
    ``--redact``, looser than the reader on purpose. Rejected: the reader's own
    date finder, which would make "the doctor found a date and the reader did not"
    impossible to say; and new forms added to the redaction beside a field
    pattern of its own, which is two lists of what a date looks like and the
    arrangement that let the reader's warnings print what neither list held.
    """

    # Spellings of the listed forms that the reader reads as well, which the list
    # names once each by notation.
    READ_TOO = ("18 Aug. 2026", "18 August, 2026")
    # Forms the reader does not read, which the doctor has to find so that it can
    # say so, and which a reader warning may quote.
    UNREAD = ("August 18, 2026", "18th August 2026", "18th of August 2026",
              "2026/08/18", "18-Aug-2026", "08-18-2026", "08.18.26", "32-13-2026",
              "31 February 2026")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def report(self, when: str, *, redact: bool = False) -> str:
        card = cw.write_card_file(Path(self._tmp.name) / FILENAME,
                                  dataclasses.replace(_spec(), when=when))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            if redact:
                doctor._one_file(card, page=None, redact=True)
            else:
                doctor._report(card, page=None, label=str(card), redactor=None)
        return buffer.getvalue()

    def test_the_date_field_finds_every_form_the_reader_reads(self):
        for form in tuple(form for _, form in DATE_FORMS) + self.READ_TOO:
            with self.subTest(form=form):
                self.assertRegex(self.report(f"Tue {form} 7:30 PM"),
                                 rf"date +found +{re.escape(repr(form))}")

    def test_the_date_field_finds_a_date_the_reader_does_not_read(self):
        for form in self.UNREAD:
            with self.subTest(form=form):
                self.assertRegex(self.report(f"Tue {form} 7:30 PM"),
                                 rf"date +found +{re.escape(repr(form))}")

    def test_no_printed_date_survives_redaction_in_any_form(self):
        # The year is checked on its own as well, since it is what a partial
        # redaction leaves standing beside a shaped month; this card's report
        # prints 2026 nowhere else (TestNothingOffTheCardSurvivesRedaction).
        for form in (tuple(form for _, form in DATE_FORMS) + self.READ_TOO
                     + self.UNREAD):
            with self.subTest(form=form):
                report = self.report(f"Tue {form} 7:30 PM", redact=True)
                self.assertNotIn(form, report)
                self.assertNotIn("2026", report)

    def test_the_verdict_names_the_dates_weekday_and_any_other_the_card_prints(self):
        for when, said in (
                ("Tue 18/08/2026 7:30 PM", "2026-08-18 Tuesday\n"),
                ("Wed 18/08/2026 7:30 PM",
                 "2026-08-18 Tuesday (the card prints Wednesday; the date is what "
                 "files it)\n"),
                ("Tue August 18, 2026 7:30 PM",
                 "(none read; filed by the Tuesday it prints)\n"),
                ("7:30 PM", "(none read)\n")):
            with self.subTest(when=when):
                self.assertRegex(self.report(when), r"\n +date {10}" + r"\s+".join(
                    map(re.escape, said.split(" "))))

    def test_the_verdict_files_a_card_by_the_rule_the_build_files_it_by(self):
        # Asked of the filing rather than restated, so that the doctor cannot tell
        # a club the date files a card the build files by its printed weekday.
        # The rule is swapped for the one the filing rejected, to show the verdict
        # follows it.
        def printed_first(card):
            return card.weekday or weekday_of(card.date)
        with mock.patch.object(discover, "filing_day", printed_first):
            self.assertRegex(self.report("Wed 18/08/2026 7:30 PM"),
                             r"\n +date {10}" + r"\s+".join(map(re.escape, (
                                 "2026-08-18 Tuesday (the card prints Wednesday; "
                                 "filed by the Wednesday it prints)\n").split(" "))))


class TestDoctorAndTheBuildWithholdByTheSameRule(unittest.TestCase):
    """``--redact`` and ``[build] players`` find a name by one function, not two copies.

    Both promise a name does not get out, and both used to be one regular
    expression each: the day one was fixed and the other was not would have been a
    leak nobody could see from the fixed one. So the doctor's scrub is
    :mod:`matchcentre.redact`, and its policy is stated where it asks: it matches
    case-sensitively, because its own labels sit beside the page's words in the
    report it scrubs (``club hint`` beside ``Club``). Measured: the doctor's
    ``--redact`` report over the fixture cards is byte-identical before and after
    the move. Rejected: ignoring case in the doctor as the build does, which would
    shape the report's own labels.
    """

    def source(self) -> str:
        return DOCTOR.read_text(encoding="utf-8")

    def test_the_doctor_calls_the_shared_rule_and_has_none_of_its_own(self):
        source = self.source()
        for call in ("redact.candidates(", "redact.substitute(", "redact.shape"):
            self.assertIn(call, source)
        self.assertNotIn("def _shape", source)
        self.assertNotIn('"|".join', source)
        self.assertIn("ignore_case=False", source)

    def test_the_rule_it_asks_for_leaves_another_case_alone(self):
        text = "Club: HALLORAN, club hint Halloran"
        self.assertEqual(redact.substitute(text, ("Halloran", "Club"), redact.shape,
                                           ignore_case=False),
                         "<4 chars>: HALLORAN, club hint <8 chars>")
        self.assertEqual(redact.substitute(text, ("Halloran",), redact.shape,
                                           ignore_case=True),
                         "Club: <8 chars>, club hint <8 chars>")


def _acute(name: str) -> str:
    """``name`` with an acute accent on its first vowel, as the vowel and its mark."""
    return re.sub("[aeiou]", "\\g<0>\u0301", name, count=1)


class TestUnderRedactANameIsWithheldInEveryFormTheReportPrintsIt(unittest.TestCase):
    """``--redact`` withholds a name as the report prints it, escaped or not.

    Row 4.105 asked whether the doctor's case-sensitive scrub lets a name out
    that the report prints in a case, or with accents, other than the page's.
    None does: every page string it prints is printed in the form it learned,
    measured by /tmp/t7/scratch/DRED/p105b.py on this file's card with each
    surname in capitals, with an accent composed or combining, and by
    /tmp/t7/scratch/DRED/p105.py on the demo season's 15 cards, whose redacted
    report prints no team or player word in any case. So the scrub keeps to
    case, which keeps ``club hint`` and ``(d1-s1-s2-d2)`` beside the page's
    ``Club`` and ``S1``.

    One form did get out. The summary quotes the players of a set row as a list,
    so through :func:`repr`, which escapes a soft hyphen or a zero-width space a
    card can carry inside a name: ``Hal\\xadloran``, not the form learned from
    the page, and each surname was published whole three times. The doctor now
    learns each thing it withholds as :func:`repr` quotes it as well, which for
    a name with no such character is the name: the page's every word, too, since
    the summary quotes the players of a card it refused, and no card is learned
    from that. Rejected: ignoring case or accents in the doctor's scrub as the
    build does, for a leak no card shows, and the report's own labels shaped for
    it; and a table of the characters :func:`repr` escapes, which is repr's to
    keep.
    """

    SURNAMES = tuple(name.split()[1] for name in HOME_PLAYERS + AWAY_PLAYERS)
    CHANGED = {"capitals": str.upper,
               "an accent": lambda name: unicodedata.normalize("NFC", _acute(name)),
               "an accent's mark": _acute}
    # Characters a name can carry that :func:`repr` escapes.
    ESCAPED = {"a soft hyphen": "\xad", "a zero-width space": "\u200b",
               "a word joiner": "\u2060"}

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.card = cw.write_card_file(Path(cls._tmp.name) / FILENAME, _spec())
        cls.page = pdf.extract(cls.card)
        # The card refused, for a tick in a square no row reads.
        cls.refused = drawn_again(cls.page, "Forfeit", 600.0, ticked=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def reports(self, change, page: pdf.Page | None = None) -> tuple[str, str]:
        """The doctor's report, plain and redacted, of the card (or ``page``) with
        each surname changed by ``change``: cardwriter writes ASCII alone, so the
        words are changed after the page is read."""
        swap = {name: change(name) for name in self.SURNAMES}
        page = page or self.page
        page = dataclasses.replace(page, words=tuple(
            dataclasses.replace(word, text=swap.get(word.text, word.text))
            for word in page.words))
        said = []
        for redacted in (False, True):
            buffer = io.StringIO()
            with mock.patch.object(doctor.pdf, "extract", return_value=page), \
                    contextlib.redirect_stdout(buffer):
                doctor._one_file(self.card, page=None, redact=redacted)
            said.append(buffer.getvalue())
        return said[0], said[1]

    def test_the_reports_own_labels_are_kept_beside_the_pages_words(self):
        tokens = {word.text for word in self.page.words}
        self.assertLessEqual({"Club", "S1"}, tokens)
        redacted = self.reports(lambda name: name)[1]
        self.assertIn("\n        club hint '", redacted)
        self.assertIn(" (d1-s1-s2-d2)\n", redacted)

    def test_a_name_in_another_case_or_with_an_accent_is_withheld_as_printed(self):
        for form, change in self.CHANGED.items():
            plain, redacted = self.reports(change)
            for name in self.SURNAMES:
                with self.subTest(form=form, name=name):
                    self.assertNotEqual(change(name), name)
                    self.assertIn(change(name), plain)
                    self.assertNotIn(redact.fold(name), redact.fold(redacted))

    def test_a_name_the_report_escapes_is_withheld_as_escaped(self):
        for (form, character), (card, page) in itertools.product(
                self.ESCAPED.items(), (("read", self.page), ("refused", self.refused))):
            change = (lambda name, character=character:
                      name[:3] + character + name[3:])
            plain, redacted = self.reports(change, page)
            self.assertEqual("This page was refused: " in plain, card == "refused")
            for name in self.SURNAMES:
                escaped = repr(change(name))[1:-1]
                with self.subTest(form=form, card=card, name=name):
                    self.assertIn(escaped, plain)
                    self.assertNotIn(escaped, redacted)
                    self.assertNotIn(change(name), redacted)
                    self.assertNotIn(redact.fold(name), redact.fold(redacted))


class _VerdictLinesCase(unittest.TestCase):
    """This file's card with fields changed, and the doctor's verdict on it by line."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def verdict(self, **fields) -> list[str]:
        card = cw.write_card_file(Path(self._tmp.name) / FILENAME,
                                  dataclasses.replace(_spec(), **fields))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor._report(card, page=None, label=str(card), redactor=None)
        return buffer.getvalue().split("\n    verdict\n")[-1].splitlines()

    @staticmethod
    def check(lines: list[str], name: str) -> str:
        """The cross-checks table's line for *name*, from its state on, with the
        lines it goes on to under its state joined to it (4.186)."""
        prefix = f"          {name:<14}"
        at = next(at for at, line in enumerate(lines) if line.startswith(prefix))
        going_on = itertools.takewhile(
            lambda line: line.startswith(" " * len(prefix)), lines[at + 1:])
        return " ".join([lines[at][len(prefix):],
                         *(line.lstrip() for line in going_on)])


class TestTheDoctorSaysAVerbNobodyCanUseIsPrinted(_VerdictLinesCase):
    """The doctor calls a verb the card prints but nothing can use printed.

    Row 4.133. Measured by /tmp/t7/scratch/V3/probe/probe.py on the tree before
    this, cards with no TOTAL row and no Match Winner tick: printing ``VS`` or
    ``TROUNCES`` between the team names, the table said the verb was "not printed
    on this card", over the reader's detail quoting the word it printed, and the
    closing paragraph that "No independent cross-check is printed on this card".
    It now says "printed, not usable", and the paragraph that nothing the card
    prints can serve as a cross-check, which the table above it does not
    contradict. A card printing no verb is described as it always was.

    Rejected: leaving the paragraph as it was, true of a verb only by reading
    "cross-check" narrowly, beside a table line saying the verb is printed.
    """

    BARE = dict(omit_totals=True, winner=cw.NO_TICK)
    OLD = ["      No independent cross-check is printed on this card, so its numbers",
           "      rest on the set rows alone (F6 warns rather than refuses)."]
    NEW = ["      Nothing this card prints can serve as a cross-check, so its numbers",
           "      rest on the set rows alone (F6 warns rather than refuses)."]

    def test_a_verb_nobody_can_use_is_printed_not_usable(self):
        for verb, detail in (
                ("VS", "the card prints 'VS' between the team names, which states "
                       "no result to check against"),
                ("TROUNCES", "the verb 'TROUNCES' is not one this reader recognises")):
            with self.subTest(verb=verb):
                lines = self.verdict(verb=verb, **self.BARE)
                self.assertEqual(self.check(lines, "verb"),
                                 f"printed, not usable  {detail}")
                self.assertEqual(lines[-2:], self.NEW)

    def test_what_the_card_does_not_print_is_still_not_printed(self):
        lines = self.verdict(verb="VS", **self.BARE)
        self.assertEqual(self.check(lines, "totals"),
                         "not printed on this card  the card prints no TOTAL row")
        self.assertEqual(self.check(lines, "winner-tick"), "not printed on this "
                         "card  neither Match Winner box is ticked")

    def test_a_card_printing_no_verb_is_described_as_it_always_was(self):
        lines = self.verdict(omit_verb=True, **self.BARE)
        self.assertEqual(self.check(lines, "verb"), "not printed on this card  "
                         "the card prints no result verb")
        self.assertEqual(lines[-2:], self.OLD)


class TestTheDoctorSaysWhatADisagreeingCheckWasHeldTo(_VerdictLinesCase):
    """The verdict names the Match Forfeited box a check disagrees with.

    Row 4.152. Measured by /tmp/t7/scratch/V3/probe/probe.py on the tree before
    this, a whole-match forfeit with its box ticked against away, four 0-0 rows,
    no verb and the away tick: the table said the tick disagrees with the box,
    and the paragraph under it "A cross-check disagrees with the set rows", rows
    which give no result. It now names what the table does, taken from the check.
    A played card whose rows the tick contradicts is told what it always was,
    word for word, laid out to the report's width as every line of it is (4.186).

    Rejected: "disagrees with the card", which says less and changes every played
    card's verdict.
    """

    ROWS = ["      A cross-check disagrees with the set rows. The card states its result",
            "      more than once and the readings differ, so something was misread;",
            "      build.py reports this as an error rather than scoring it."]

    @staticmethod
    def blank(**fields) -> dict:
        """A whole-match forfeit's card, every row 0-0, with *fields*."""
        return dict(rows=tuple(dataclasses.replace(row, home_games=0, away_games=0)
                               for row in _spec().rows),
                    omit_verb=True, **fields)

    def box(self, side: str) -> list[str]:
        return [f"      A cross-check disagrees with the {side} Match Forfeited box. "
                "The card",
                "      states its result more than once and the readings differ, so "
                "something",
                "      was misread; build.py reports this as an error rather than "
                "scoring it."]

    def test_a_tick_held_to_the_box_is_said_to_disagree_with_the_box(self):
        for side in ("away", "home"):
            with self.subTest(forfeited_by=side):
                lines = self.verdict(**self.blank(forfeited_by=side, winner=side))
                self.assertEqual(lines[-3:], self.box(side))

    def test_two_checks_held_to_one_box_name_it_once(self):
        lines = self.verdict(**self.blank(forfeited_by="away", winner="away",
                                          totals=(0, 0, 24, 4)))
        self.assertEqual(self.check(lines, "totals")[:9], "DISAGREES")
        self.assertEqual(lines[-3:], self.box("away"))

    def test_a_played_card_is_told_what_it_always_was(self):
        self.assertEqual(self.verdict(winner="away")[-3:], self.ROWS)


class TestTheDoctorSaysUnderWhichRulesTheBuildFailsRowsAgainstTheBox(
        _VerdictLinesCase):
    """The verdict names the set rules and scorings the build fails such rows on.

    Row 4.134. The build reports a whole-match forfeit whose set rows give the
    match to the side that forfeited as ``forfeit-contradicted``, asked of the
    rows under the club's set rule and ``decide_by``, which the doctor is not
    given. Measured on this file's card with the home box, no verb and the home
    tick, the card 4.134 was filed for: on the base the table said "2 available,
    0 disagreeing" and the verdict ended there, over a card the build fails.
    Measured by /tmp/t7/scratch/V4/fix3/probe on the tree before this, whose
    doctor asked its own rule of the rows: it said the build fails rows 0-4 4-3
    4-3 4-2 that no set rule but fast4 finishes, which the build passes under the
    default config. The verdict now asks
    :func:`~matchcentre.validate.rows_against_box`, the rule the build asks, of
    every set rule and scoring there is, and names those it is true under, so it
    never says the build fails a card the build passes.

    Rejected: asking it under the default config alone, which says nothing of a
    fast4 club's card the build fails and says the build fails a pro-8 club's
    card it passes; and saying the build fails the card under any rule, without
    naming which.

    Then what it said of the checks beside that paragraph went on as the base
    said it, whatever the rules. Under those it named the build names a verb, a
    tick and a TOTAL row restating the box in ``forfeit-contradicted`` and F6
    passes them by, but the table said the tick was "reported by the verb
    cross-check" and the paragraph above that build.py "reports this as an
    error", unqualified. Measured by /tmp/t7/scratch/V4/fix4/analyse.py over the
    101 distinct cards of /tmp/t7/scratch/V4/fix4/cards, each built under every
    set rule and scoring: 15 cards whose paragraph was false under a set rule
    the doctor named, 5 of them with the tick's line false too, and none after. The
    verdict now asks :func:`~matchcentre.validate.restating_box`, what F6 passes
    by and ``forfeit-contradicted`` names, under each set rule it names: a check
    it names is said to be reported by forfeit-contradicted there and by the
    check the reader names elsewhere, the paragraph is said only where another
    check still reports, and the forfeit-contradicted paragraph says what it
    names. A TOTAL row totalling the six-game award is named under the six-game
    rules and not under short-sets-4, whose award it is not.

    Rejected: folding every disagreement into the forfeit-contradicted
    paragraph, which says nothing of the F6 error the build reports under fast4;
    and leaving the reader's hand-off out of the table, which is the reading the
    doctor exists to show.
    """

    FILED = [
        "      Where the club's set rule is first-to-6, advantage-6, six-game-tiebreak",
        "      or short-sets-4, the set rows give the match to home, and the home Match",
        "      Forfeited box gives it to away: build.py reports that once, as",
        "      forfeit-contradicted, an error, which fails the run."]
    # The card writer's own verb and tick for a home forfeit, which restate the
    # box: under the six-game rules forfeit-contradicted names them.
    RESTATED = [
        "      Where the club's set rule is first-to-6, advantage-6, six-game-tiebreak",
        "      or short-sets-4, the set rows give the match to home, and the home Match",
        "      Forfeited box gives it to away, as the verb 'LOSES TO' and the away",
        "      Match Winner tick do: build.py reports that once, as",
        "      forfeit-contradicted, an error, which fails the run."]
    SCOPED = [
        "      A cross-check disagrees with the set rows. The card states its result",
        "      more than once and the readings differ, so something was misread; where",
        "      the club's set rule is fast4, pro-8, pro-8-tiebreak or pro-10, build.py",
        "      reports this as an error rather than scoring it."]
    # The tick alone restating it.
    TICKED = [
        "      Where the club's set rule is first-to-6, advantage-6, six-game-tiebreak",
        "      or short-sets-4, the set rows give the match to home, and the home Match",
        "      Forfeited box gives it to away, as the away Match Winner tick does:",
        "      build.py reports that once, as forfeit-contradicted, an error, which",
        "      fails the run."]
    # Both, and a TOTAL row totalling the six-game award.
    TOTALLED = [
        "      Where the club's set rule is first-to-6, advantage-6 or",
        "      six-game-tiebreak, the set rows give the match to home, and the home",
        "      Match Forfeited box gives it to away, as the verb 'LOSES TO', the away",
        "      Match Winner tick and the printed TOTAL row do: build.py reports that",
        "      once, as forfeit-contradicted, an error, which fails the run.",
        "      Where the club's set rule is short-sets-4, the set rows give the match",
        "      to home, and the home Match Forfeited box gives it to away, as the verb",
        "      'LOSES TO' and the away Match Winner tick do: build.py reports that",
        "      once, as forfeit-contradicted, an error, which fails the run."]
    # Rows only fast4 finishes, and both.
    FAST4 = [
        "      A cross-check disagrees with the set rows. The card states its result",
        "      more than once and the readings differ, so something was misread; where",
        "      the club's set rule is first-to-6, advantage-6, six-game-tiebreak,",
        "      short-sets-4, pro-8, pro-8-tiebreak or pro-10, build.py reports this as",
        "      an error rather than scoring it.",
        "      Where the club's set rule is fast4, the set rows give the match to home,",
        "      and the home Match Forfeited box gives it to away, as the verb 'LOSES",
        "      TO' and the away Match Winner tick do: build.py reports that once, as",
        "      forfeit-contradicted, an error, which fails the run."]
    TICK = ("disagrees, reported by the verb cross-check  the away Match Winner "
            "tick restates the home Match Forfeited box, as 'LOSES TO' does, so "
            "the set rows disagreeing with both are reported once, by the verb")

    @staticmethod
    def rows(*games: tuple[int, int]) -> tuple:
        return tuple(dataclasses.replace(row, home_games=home, away_games=away)
                     for row, (home, away) in zip(_spec().rows, games))

    def test_the_filed_card_is_said_to_fail_under_the_rules_that_finish_it(self):
        lines = self.verdict(forfeited_by="home", verb="", winner="home")
        self.assertIn("        cross-checks  2 available, 0 disagreeing", lines)
        self.assertEqual(lines[-4:], self.FILED)

    def test_a_check_forfeit_contradicted_names_is_said_to_be_reported_by_it_there(
            self):
        # The reader holds the verb and tick to the rows, and hands the tick to
        # the verb, which the build reports only where it does not fail the rows.
        lines = self.verdict(forfeited_by="home")
        self.assertTrue(self.check(lines, "verb").startswith("DISAGREES  "))
        self.assertEqual(self.check(lines, "winner-tick"), (
            "disagrees, reported by the verb cross-check or forfeit-contradicted  "
            "the away Match Winner tick restates the home Match Forfeited box, as "
            "'LOSES TO' does, so the set rows disagreeing with both are reported "
            "once, by the verb, where the club's set rule is fast4, pro-8, "
            "pro-8-tiebreak or pro-10, and by forfeit-contradicted where the club's "
            "set rule is first-to-6, advantage-6, six-game-tiebreak or "
            "short-sets-4"))
        self.assertEqual(lines[-9:], self.SCOPED + self.RESTATED)

    def test_a_total_row_is_named_where_it_totals_the_box_s_award(self):
        # 4-24-0-0 to away totals the six-game rules' award, not short-sets-4's
        # of 4-16-0-0, which totals-mismatch holds to the rows there.
        lines = self.verdict(forfeited_by="home", totals=(0, 0, 24, 4))
        self.assertTrue(self.check(lines, "totals").startswith("DISAGREES  "))
        self.assertEqual(lines[-13:], [
            "      A cross-check disagrees with the set rows. The card states its result",
            "      more than once and the readings differ, so something was misread; where",
            "      the club's set rule is fast4, short-sets-4, pro-8, pro-8-tiebreak or",
            "      pro-10, build.py reports this as an error rather than scoring it."]
            + self.TOTALLED)

    def test_a_tick_alone_restating_the_box_is_named_and_the_paragraph_scoped(self):
        lines = self.verdict(forfeited_by="home", omit_verb=True, winner="away")
        self.assertTrue(self.check(lines, "winner-tick").startswith("DISAGREES  "))
        self.assertEqual(lines[-9:], self.SCOPED + self.TICKED)

    def test_a_draw_verb_the_build_reports_everywhere_is_said_as_it_was(self):
        # forfeit-contradicted never names a draw: verb-mismatch reports the verb
        # under every rule, so the paragraph holds unqualified.
        lines = self.verdict(forfeited_by="home", verb="DRAWS WITH", winner="away")
        self.assertTrue(self.check(lines, "winner-tick").startswith("DISAGREES  "))
        self.assertEqual(lines[-8:], TestTheDoctorSaysWhatADisagreeingCheckWasHeldTo
                         .ROWS + self.TICKED)

    def test_rows_only_fast4_finishes_are_said_to_fail_under_fast4_alone(self):
        lines = self.verdict(forfeited_by="home",
                             rows=self.rows((0, 4), (4, 3), (4, 3), (4, 2)))
        self.assertTrue(self.check(lines, "winner-tick").endswith(
            ", by the verb, where the club's set rule is first-to-6, advantage-6, "
            "six-game-tiebreak, short-sets-4, pro-8, pro-8-tiebreak or pro-10, and "
            "by forfeit-contradicted where the club's set rule is fast4"))
        self.assertEqual(lines[-9:], self.FAST4)

    def test_rows_a_scoring_decides_for_the_side_that_came_name_the_others(self):
        # Sets 3-1 home and games 18-19 away: games-bonus gives away the match.
        lines = self.verdict(forfeited_by="home",
                             rows=self.rows((0, 6), (6, 4), (6, 4), (6, 5)))
        self.assertEqual(lines[-5:], [
            "      Where the club's set rule is first-to-6 and its scoring is sets-first,",
            "      rubbers or simple, the set rows give the match to home, and the home",
            "      Match Forfeited box gives it to away, as the verb 'LOSES TO' and the",
            "      away Match Winner tick do: build.py reports that once, as",
            "      forfeit-contradicted, an error, which fails the run."])

    def test_a_forfeit_given_up_part_way_is_said_as_it_was(self):
        lines = self.verdict(forfeited_by="home",
                             rows=self.rows((6, 2), (3, 3), (0, 0), (0, 0)))
        self.assertNotIn("forfeit-contradicted", "\n".join(lines))
        self.assertEqual(self.check(lines, "winner-tick"), self.TICK)
        self.assertEqual(lines[-3:], TestTheDoctorSaysWhatADisagreeingCheckWasHeldTo
                         .ROWS)

    def test_where_forfeit_contradicted_names_every_disagreement_nothing_else_is(
            self):
        # No card: no rows are finished sets under both fast4 and first-to-6, so
        # the build fails none under every set rule. A spy says it does.
        with mock.patch.object(validate, "rows_against_box", return_value="rows"):
            lines = self.verdict(forfeited_by="home")
        self.assertEqual(self.check(lines, "winner-tick"),
                         "disagrees, reported by forfeit-contradicted")
        self.assertNotIn("A cross-check disagrees", "\n".join(lines))
        self.assertEqual(lines[-5:], [
            "      Where the club's set rule is first-to-6, advantage-6, six-game-tiebreak,",
            "      fast4, short-sets-4, pro-8, pro-8-tiebreak or pro-10, the set rows give",
            "      the match to home, and the home Match Forfeited box gives it to away, as",
            "      the verb 'LOSES TO' and the away Match Winner tick do: build.py reports",
            "      that once, as forfeit-contradicted, an error, which fails the run."])

    def test_the_verdict_asks_the_builds_rule_of_what_restates_the_box(self):
        # A spy rather than a card, as for rows_against_box below.
        with mock.patch.object(validate, "restating_box",
                               wraps=validate.restating_box) as rule:
            self.verdict(forfeited_by="home")
        self.assertEqual({id(call.args[1].set_rule) for call in rule.call_args_list},
                         {id(doctor.SET_RULES[name]) for name in (
                             "first-to-6", "advantage-6", "six-game-tiebreak",
                             "short-sets-4")})
        self.assertEqual({call.args[0].forfeited_by for call in rule.call_args_list},
                         {"home"})
        # Told nothing restates the box, it says what the base said.
        with mock.patch.object(validate, "restating_box", return_value=()):
            lines = self.verdict(forfeited_by="home")
        self.assertEqual(self.check(lines, "winner-tick"), self.TICK)
        self.assertEqual(lines[-7:], TestTheDoctorSaysWhatADisagreeingCheckWasHeldTo
                         .ROWS + self.FILED)

    def test_the_verdict_asks_the_builds_rule_of_every_set_rule_and_scoring(self):
        # A spy rather than a card: a copy of the rule agreeing today cannot be
        # told apart from it by any card.
        with mock.patch.object(validate, "rows_against_box",
                               wraps=validate.rows_against_box) as rule:
            self.verdict(forfeited_by="home", verb="", winner="home")
        self.assertEqual({(id(call.args[1]), id(call.args[2]))
                          for call in rule.call_args_list},
                         {(id(set_rule), id(rules))
                          for set_rule in doctor.SET_RULES.values()
                          for rules in doctor.RULESETS.values()})
        # Told the rows are not against the box, it says nothing of them.
        with mock.patch.object(validate, "rows_against_box", return_value=""):
            lines = self.verdict(forfeited_by="home", verb="", winner="home")
        self.assertNotIn("forfeit-contradicted", "\n".join(lines))
        last = [line for line in lines if not line.startswith(" " * 20)][-1]
        self.assertTrue(last.startswith("        warning     "), lines)


class TestTheVerdictOnAWashoutSaysWhatTheBuildDoesWithIt(unittest.TestCase):
    """The doctor told a washout's owner what ``build.py`` did with its cross-checks.

    Measured on the R6 washout of the pipeline tests, stopped after two rubbers and
    printing ``DRAWS WITH`` (/tmp/t6/scratch/m22/fix/probe.py): the verdict ended
    "A cross-check disagrees with the set rows ... build.py reports this as an error
    rather than scoring it.", and on one printing no TOTAL row, verb or tick "rest
    on the set rows alone (F6 warns rather than refuses)". Since ``build.py``
    holds a card its Match Cancelled box calls off to nothing it prints beside its
    rows, it does neither, and a club reading the one would look for an error that
    is not there. The verdict now says what the build does with it, asking
    :func:`~matchcentre.discover.called_off`, the function the build asks.
    Rejected: ``card.cancelled`` asked here, which is also the box of a forfeit
    that ticks Match Cancelled, which counts and is checked, and a second copy of
    the rule; and leaving the cross-checks' own lines out for a washout, which are
    the reading the doctor exists to show.

    Then the build began to fail a washout whose card names a winner, and the
    verdict went on saying the match was reported once, as cancelled. Measured on
    the tree before this (/tmp/t7/scratch/VW/fix/doc_probe.py), on this file's card
    called off and ticking home beside ``DEFEATS``: exit 0 and that sentence, the
    one ``build.py`` gives nothing to look for, where the build exits 1 on the
    ``no-result`` error. The verdict now names what the card claims, asked of
    :func:`~matchcentre.validate.winner_claims`, the function the build asks, and
    says the one report is an error that fails the run. Rejected: the doctor
    reading the tick and the verb itself, a copy of the rule; and exiting 1, which
    the doctor keeps for a page that does not read as a scorecard.
    """

    WASHOUT = ("Match Cancelled is ticked, so build.py counts nothing on this card "
               "and holds nothing it prints to the set rows: it reports the match "
               "once, as cancelled.")
    CLAIMS = ("Match Cancelled is ticked, so build.py counts nothing on this card "
              "and holds nothing it prints to the set rows. But {}, and a match "
              "called off has no winner, so the card contradicts itself: build.py "
              "reports the match once, as cancelled, and as an error, which fails "
              "the run.")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def verdict(self, **fields) -> str:
        """The verdict block on this file's card with *fields* changed, one line."""
        return " ".join(self.block(**fields).split())

    def block(self, **fields) -> str:
        """The verdict block on this file's card with *fields* changed, as printed."""
        card = cw.write_card_file(Path(self._tmp.name) / FILENAME,
                                  dataclasses.replace(_spec(), **fields))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor._report(card, page=None, label=str(card), redactor=None)
        return buffer.getvalue().split("\n    verdict\n")[-1]

    def test_a_washout_is_told_it_is_held_to_nothing_it_prints(self):
        for label, fields in (
                ("its verb disagreeing", dict(cancelled=True)),
                ("nothing to check it by", dict(cancelled=True, verb="VS",
                                                winner=cw.NO_TICK,
                                                omit_totals=True))):
            with self.subTest(card=label):
                said = self.verdict(**fields)
                self.assertIn(self.WASHOUT, said)
                self.assertNotIn("build.py reports this as an error", said)
                self.assertNotIn("F6 warns", said)

    def test_a_forfeit_with_its_cancelled_box_ticked_too_is_not_told_so(self):
        said = self.verdict(cancelled=True, forfeited_by="away")
        self.assertNotIn("Match Cancelled is ticked", said)
        self.assertIn("cross-checks", said)

    def test_the_verdict_asks_the_function_the_build_asks(self):
        # Told the card is not called off, the doctor says what it says of any
        # other card whose verb disagrees with its rows.
        with mock.patch.object(discover, "called_off", return_value=False):
            said = self.verdict(cancelled=True)
        self.assertNotIn("Match Cancelled is ticked", said)
        self.assertIn("build.py reports this as an error rather than scoring it.",
                      said)

    def test_a_washout_naming_a_winner_is_told_the_build_fails_it(self):
        for label, fields, claims in (
                ("by tick and verb", dict(cancelled=True, verb="DEFEATS",
                                          winner="home"),
                 "the card ticks home as Match Winner and the verb 'DEFEATS' "
                 "means home won"),
                ("by a tick beside a drawn verb", dict(cancelled=True,
                                                       winner="away"),
                 "the card ticks away as Match Winner")):
            with self.subTest(card=label):
                said = self.verdict(**fields)
                self.assertIn(self.CLAIMS.format(claims), said)
                self.assertNotIn(self.WASHOUT, said)
        with self.subTest(card="naming a winner, not called off"):
            said = self.verdict(verb="DEFEATS", winner="home")
            self.assertNotIn("Match Cancelled is ticked", said)

    def test_the_longest_claims_are_told_in_78_columns(self):
        # A tick away beside LOSES TO, the longest pair of claims. The rest of the
        # verdict is wrapped by hand within the same 78.
        lines = self.block(cancelled=True, verb="LOSES TO", winner="away").splitlines()
        told = lines[next(n for n, line in enumerate(lines)
                          if "Match Cancelled" in line):]
        self.assertIn("the card ticks away as Match Winner and the verb 'LOSES TO' "
                      "means away won", " ".join(" ".join(told).split()))
        self.assertLessEqual(max(len(line) for line in told), 78, told)

    def test_the_claims_are_asked_of_the_function_the_build_asks(self):
        # Told the card names no winner, the doctor says what it says of any
        # washout that names none.
        with mock.patch.object(validate, "winner_claims", return_value=[]):
            said = self.verdict(cancelled=True, verb="DEFEATS", winner="home")
        self.assertIn(self.WASHOUT, said)


# An "R" and a box round it, as a filled and a stroked path: a letter and a line
# drawn as outlines, which pdf.py keeps neither of (4.183).
_OUTLINED = (b"0 g 60 700 m 60 760 l 90 760 l 100 745 l 90 730 l 75 730 l 100 700 l "
             b"90 700 l 68 728 l 68 700 l h f\n"
             b"1 w 50 690 m 560 690 l 560 770 l 50 770 l h S\n")


def _with_a_form_field(path: Path, lines: tuple[tuple[str, float, float], ...]) -> Path:
    """A page whose content stream is empty and whose only words, ``(text, x,
    baseline)`` lines, are a filled-in form field's: a widget annotation, whose
    appearance a viewer paints and pdf.py never reads (4.183)."""
    art = cw._Painter()
    for text, x, baseline in lines:
        art.words(text, x, baseline, 8.0)
    shown = "\n".join(art.ops).encode("latin-1")
    objects = {int(number): body for number, body in re.findall(
        rb"(?s)(\d+) 0 obj\n(.*?)\nendobj\n", cw._assemble(b"", with_to_unicode=True))}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [ 8 0 R ] >> >>"
    objects[3] = objects[3].replace(b"/Contents 6 0 R",
                                    b"/Contents 6 0 R /Annots [ 8 0 R ]")
    objects[8] = (b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (draw) /F 4 /P 3 0 R"
                  b" /Rect [ 0 0 595 842 ] /AP << /N 9 0 R >> >>")
    objects[9] = (b"<< /Type /XObject /Subtype /Form /BBox [ 0 0 595 842 ] /Resources"
                  b" << /Font << /F1 4 0 R >> >> /Length %d >>\nstream\n" % len(shown)
                  + shown + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
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
    path.write_bytes(bytes(out))
    return path


class TestEveryLineOfTheDoctorsReportKeepsToTheWidth(PagesCase):
    """Every line the doctor prints fits the report's 78 columns, redacted or not,
    but a word too long for any line, printed whole on a line of its own.

    Row 4.186. The doctor printed a value at any length: a row, a line of the
    reader's own summary, a refusal and its detail, a check of the verdict's
    table. Measured on these pages by /tmp/t7/scratch/DWID/probe/width.py on the
    tree before this, 20 reports, each page plain and redacted: 268 lines past
    78 (267 on another run, as the count moves with the length of the temporary
    folder's path), the widest 433 columns, the tick of a card the verdict hands
    to the verb or to forfeit-contradicted by set rule. After, none on these
    pages but a path alone on its line. A word longer than a line is never cut,
    since the report's words are what a club sends: a font's name of 67 letters
    is a line of 81, and a team's name with no space in it, printed by the page,
    is as long as the page makes it (/tmp/t7/scratch/DWID/fx/long/probe.py).
    Each report is laid out by :func:`matchcentre.cli.fill`, the one fill, once it
    is written, and under ``--redact`` once it is scrubbed, so that a line is
    measured as it is published and a name is not parted before its shape is
    known. A row goes on under its value, a line of the summary under its label,
    and a check of the table under its state, so that each line still begins
    with its label for a grep to find. A header field found goes on under its
    name: a value with no space in it is as wide under itself as beside the
    name, and a match id of fifty digits under its value was a line of 85.
    A cause broken by hand past the width is one sentence to be laid out, since
    laid out again it left words alone on a line: "box", "detail", "the", "if"
    and "the", in the cause a card short of a square is given.

    Rejected: leaving the summary's rows whole so that each is one line to grep,
    by name a second width rule beside the one a path alone is held to; and
    laying the report out before the scrub, which measured 41 redacted lines
    past the width, the widest 87, where a name became its longer shape, and
    parted the card's path in the summary across two lines, so that the scrub
    missed it and published it.
    """

    SPACED = Path("Round 4 Nightjars at Marrowfield",
                  "Nightjars v Kestrels round four.pdf")

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        spec = _spec()
        (root / "data" / cls.SPACED.parent).mkdir(parents=True)
        (root / "folder").mkdir()
        cw.write_card_file(root / "folder" / FILENAME, spec)
        (root / "relabelled.pdf").write_bytes(relabelled(cw.write_card(spec),
                                                         OTHER_TEMPLATE_LABELS))
        (root / "undecoded.pdf").write_bytes(cw._assemble(
            _content(cw.write_card(spec)), with_to_unicode=False))
        (root / "packed.pdf").write_bytes(cw.write_card(spec).replace(
            b"/Type /Catalog", b"/Type /ObjStm /Type /Catalog", 1))
        rules = TestAPageIsCalledAPictureForTheImageItPaintsAndNotForWhatItLacks.RULES
        return {
            "spaced": cw.write_card_file(root / "data" / cls.SPACED, spec),
            # The tick the verdict hands to the verb or to forfeit-contradicted by
            # set rule, and a card with no division and no round.
            "handed": cw.write_card_file(root / "handed.pdf", dataclasses.replace(
                spec, forfeited_by="home")),
            "unheaded": cw.write_card_file(root / "unheaded.pdf", dataclasses.replace(
                spec, division="", round_label="")),
            # A header field's value with no space in it, too long for its line
            # beside the field's name.
            "long id": cw.write_card_file(root / "long-id.pdf", dataclasses.replace(
                spec, match_id="4" * 50)),
            "relabelled": root / "relabelled.pdf",
            "box more": written_with_a_box_more(root / "box-more.pdf", spec,
                                                ticked=True),
            "undecoded": root / "undecoded.pdf",
            "rules sheet": _text_only(root / "rules.pdf", rules),
            "packed": root / "packed.pdf",
            "folder": root / "folder"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redacted = {
            name: subprocess.run((sys.executable, str(DOCTOR), "--redact", str(path)),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            for name, path in cls.paths.items()}

    def both(self):
        """Each page's report, plain with its path and redacted with none."""
        for name, path in self.paths.items():
            yield name, "plain", self.reports[name], (str(path),)
            yield name, "--redact", self.redacted[name], ()

    def test_no_line_runs_past_the_width_but_a_path_alone_on_its_own(self):
        for name, mode, done, kept in self.both():
            with self.subTest(page=name, mode=mode):
                self.assertIn(done.returncode, (0, 1), done.stderr)
                self.assertEqual(
                    past_the_width((done.stdout + done.stderr).splitlines(), kept), [])

    def test_a_withheld_name_or_number_is_never_parted_across_two_lines(self):
        # A number is its count of digits, and the unheaded card's detail quotes
        # its match id where the width falls, "#<7 digits>".
        told = {name: done.stdout for name, done in self.redacted.items()}
        self.assertTrue(any(re.search(r"<\d+ words, \d+ chars>", report)
                            for report in told.values()))
        self.assertIn("#<7 digits>", told["unheaded"])
        for name, report in told.items():
            with self.subTest(page=name):
                self.assertEqual(
                    len(re.findall(r"<\d+ (?:words, \d+ )?chars>", report)),
                    report.count(" chars>"), report)
                self.assertEqual(len(re.findall(r"<\d+ digits>", report)),
                                 report.count(" digits>"), report)

    def test_under_redact_a_path_with_spaces_in_it_is_withheld_whole(self):
        report = self.redacted["spaced"].stdout.casefold()
        for word in ("marrowfield", "nightjars", "kestrels"):
            self.assertNotIn(word, report)

    def test_a_path_with_spaces_in_it_is_one_word_on_a_line_of_its_own(self):
        path = str(self.paths["spaced"])
        row = f"{'file':<{doctor.LABEL}}{path}"
        self.assertGreater(len(row), cli.WIDTH)
        self.assertEqual(self.reports["spaced"].stdout.splitlines()[1:3],
                         ["file", " " * doctor.LABEL + path])

    def test_the_readers_summary_goes_on_under_each_label(self):
        path = self.paths["spaced"]
        report = self.reports["spaced"].stdout
        summary = report.partition(f"{doctor.INDENT}reader's own summary\n")[2]
        expected = []
        for entry in scorecard.describe_lines(pdf.extract(path)):
            for line in entry.splitlines():
                text = line.lstrip(" ")
                opening = f"{doctor.INDENT}  {line[:len(line) - len(text)]}"
                expected.append(_laid(opening, text, opening + "    ", (str(path),)))
        told = "\n".join(expected)
        self.assertGreaterEqual(told.count("\n"), len(expected))
        self.assertEqual(summary.partition(f"\n{doctor.INDENT}verdict\n")[0], told)

    def test_a_check_in_the_verdicts_table_goes_on_under_its_state(self):
        lines = self.reports["handed"].stdout.splitlines()
        state = _VerdictLinesCase.check(lines, "winner-tick")
        self.assertIn("and by forfeit-contradicted where", state)
        laid = _laid(" " * 10, f"{'winner-tick':<14}{state}", " " * 24).splitlines()
        at = lines.index(laid[0])
        self.assertEqual(lines[at:at + len(laid)], laid)

    def test_a_header_field_found_goes_on_under_its_name(self):
        # A line printed with no hang goes on under its own indent, and a value
        # with no space in it is as wide there as beside the name. Plain: under
        # --redact the id is its count of digits, and fits.
        value = repr("4" * 50)
        field = f"{doctor.INDENT}  {'match id':<16} found"
        self.assertGreater(len(f"{field}     {value}"), cli.WIDTH)
        lines = self.reports["long id"].stdout.splitlines()
        at = lines.index(field)
        self.assertEqual(lines[at + 1], f"{doctor.INDENT}  {value}")
        self.assertIn(f"{field}     '<50 digits>'",
                      self.redacted["long id"].stdout.splitlines())

    def test_each_font_is_named_on_a_line_of_its_own(self):
        for name in ("spaced", "undecoded"):
            fonts, _ = doctor._fonts(self.paths[name].read_bytes())
            self.assertTrue(fonts)
            for font, subtype, encoding, _ in fonts:
                with self.subTest(page=name, font=font):
                    self.assertRegex(
                        self.reports[name].stdout,
                        rf"(?m)^(fonts|     ) {{9}}{re.escape(font)}  "
                        rf"{re.escape(subtype)} +{re.escape(encoding)} "
                        rf"(has a decode table|no /ToUnicode, no /Encoding)$")

    def test_the_fonts_are_listed_in_columns_as_wide_as_their_widest(self):
        # A second font, whose encoding is the longer, so that what each font is
        # decoded by is read down one column.
        path = Path(self._tmp.name) / "two-fonts.pdf"
        path.write_bytes((self.paths["folder"] / FILENAME).read_bytes()
                         + b"99 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont "
                           b"/Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n")
        fonts, _ = doctor._fonts(path.read_bytes())
        self.assertEqual([len(encoding) for _, _, encoding, _ in fonts], [10, 15])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor._report(path, page=None, label=str(path), redactor=None)
        rows = [line for line in buffer.getvalue().splitlines()
                if line.endswith(" has a decode table")]
        self.assertEqual(len(rows), 2, buffer.getvalue())
        self.assertEqual({line.index(" has a decode table") for line in rows},
                         {max(map(len, rows)) - len(" has a decode table")})

    def test_a_verdict_goes_on_under_its_own_indent(self):
        for name, mode, done, _ in self.both():
            lines = done.stdout.partition(doctor.REDACTION_FOOTER[0])[0].splitlines()
            heads = [at for at, line in enumerate(lines) if line.strip() == "verdict"]
            if name == "folder":
                self.assertEqual(heads, [])
                continue
            with self.subTest(page=name, mode=mode):
                told = [line for line in lines[heads[0] + 1:] if line]
                indent = " " * (len(told[0]) - len(told[0].lstrip(" ")))
                self.assertGreater(len(told), 1)
                self.assertEqual([line for line in told
                                  if not line.startswith(indent)], [])

    def test_no_word_of_a_cause_is_left_alone_on_a_line(self):
        # A cause a hand broke past the width is broken again where the width
        # falls, and a word or two of it is left alone: the card short of a
        # square was told "box", "detail", "the", "if" and "the" each on a line
        # of its own, and the relabelled card's cause so broken "its" and
        # "words". A line of one or two words ending no sentence, with its
        # cause going on under it, is such a word.
        card = self.paths["folder"] / FILENAME
        buffer = io.StringIO()
        with mock.patch.object(doctor.pdf, "extract",
                               return_value=fewer_boxes(pdf.extract(card))), \
                contextlib.redirect_stdout(buffer):
            doctor._report(card, page=None, label=str(card), redactor=None)
        self.assertIn("Likeliest cause: part of the card did not reach this file",
                      " ".join(buffer.getvalue().split()))
        reports = {(name, mode): done.stdout for name, mode, done, _ in self.both()}
        reports["a square gone", "plain"] = buffer.getvalue()
        at = re.escape(f"{doctor.INDENT}  ")
        for (name, mode), report in reports.items():
            lines = report.partition(f"\n{doctor.INDENT}verdict\n")[2].split("\n")
            with self.subTest(page=name, mode=mode):
                self.assertEqual(
                    [line for line, below in zip(lines, lines[1:])
                     if re.fullmatch(rf"{at}\S+( \S+)?(?<![.:)])", line)
                     and re.match(rf"{at}\S", below)], [])


# What a page is told that draws no text and no rectangle: not that it is empty,
# which this reader cannot know, nor that it hides a card in a structure it reads,
# or one it refuses before a page is read (4.183).
_DRAWS_NOTHING = (
    "the page draws nothing this reader reads a card by: no text, and no "
    "rectangle. It may still show a card, in what this reader never reads: "
    "annotations, where a form filled in on screen keeps its fields, or letters "
    "and lines drawn as outlines. Export the card again from Match Centre, and if "
    "that export is refused the same way, send this verdict (--redact takes the "
    "names out) to whoever maintains this tool.")


class TestAPageDrawingNothingTheReaderReadsIsNotCalledEmpty(PagesCase):
    """A page with no text and no rectangle is not told that it is empty.

    Row 4.183. Its cause said "the page is empty, or its content is inside a
    structure this reader skips (a form XObject, or a compressed object
    stream)", and neither half was true. Measured by /tmp/t7/scratch/DWID/probe
    on the tree before this: a page whose only words are a filled-in form
    field's, a widget annotation, reads as 0 words and 0 rects and was told so,
    though pdftotext reads its three lines and pdftoppm paints them; a page of
    paths alone, an outlined letter and a stroked box, was told the same; the
    field's appearance drawn by the page itself instead, a form XObject, reads
    its 23 words; and a compressed object stream is refused before a page is
    read. pdf.py never reads an annotation, nor keeps a path that is not a
    rectangle. The cause now says the page draws nothing this reader reads,
    names what it never reads, and gives the advice a club can take. It does not
    say what draws such a page: the one re-print measured, Ghostscript's with its
    letters as outlines, reads as 200 rects and is given another cause.

    Rejected: "the page is empty" alone, which states as found an absence the
    reader never looked for; and naming annotations as the one thing it skips,
    which the page of paths alone measured false.
    """

    @classmethod
    def pages(cls, root: Path) -> dict[str, Path]:
        (root / "empty.pdf").write_bytes(cw._assemble(b"", with_to_unicode=True))
        (root / "outlined.pdf").write_bytes(cw._assemble(_OUTLINED,
                                                         with_to_unicode=True))
        return {"empty": root / "empty.pdf", "outlined": root / "outlined.pdf",
                "form field": _with_a_form_field(root / "field.pdf", _DRAW)}

    def assert_draws_nothing(self, name: str):
        self.assertEqual((self.count(name, "words"), self.count(name, "rects")),
                         (0, 0))
        self.assertEqual(self.cause(name), _DRAWS_NOTHING)

    def test_a_page_whose_only_words_are_a_form_fields_is_not_called_empty(self):
        self.assertIn(b"/Annots [ 8 0 R ]", self.paths["form field"].read_bytes())
        self.assert_draws_nothing("form field")

    def test_a_page_drawn_in_outlines_is_not_called_empty(self):
        self.assertIn(b" l h f\n", self.paths["outlined"].read_bytes())
        self.assert_draws_nothing("outlined")

    def test_a_page_that_draws_nothing_at_all_is_told_the_same(self):
        self.assert_draws_nothing("empty")


class TestTheReadersSummarySaysAHandOffAsTheVerdictDoes(_VerdictLinesCase):
    """The reader's own summary says a check is reported where the build reports it.

    A follow-up to 4.134. The doctor printed the reader's summary as the reader
    wrote it, and the reader, which is given no club rules, hands a forfeit's tick
    to the verb: "reported by the verb cross-check - ... reported once, by the
    verb", a few lines above a verdict naming the rules under which the build
    reports it as forfeit-contradicted and F6 passes it by. Measured by
    /tmp/t7/scratch/DWID/truth/truth2.py, a copy of V4's truth.py that holds each
    hand-off of the summary to the test it holds the table's to, on the tree
    before this: of 679 cards, 95 hand-offs in the summary, 79 of them false
    under some set rule and scoring, on 79 cards; of 96 cards with every rubber
    forfeited, 12 of 12, 4 of them false under all 32; after, none, and no false
    claim in the verdict before or after. On a card with every rubber forfeited,
    at 0-6, by the side the box says came, the build reports forfeit-contradicted
    alone under every one of the 32 set rules and scorings, and the summary said
    the verb reports it. The summary now says each hand-off in the verdict's
    words, from the one map the verdict asks of the build's rules, and a hand-off
    true under every rule as the reader wrote it, with --redact and without,
    since the redacted report is the one a club is told to send. What asking
    the build's rules raises, the reader's failure or the rules' own, is kept
    and raised by the verdict after its cross-checks row, where the tree before
    this asked the rules, so the summary is printed as it was, with what the
    reader raised: asked above the summary, a failure took the summary with it
    (measured by /tmp/t7/scratch/DWID/resume/surprise/probe.py, 74 lines of the
    report before the traceback, where there were 152, and a bug in the rules
    by /tmp/t7/scratch/DWID/fx/rules_fail.py, 78, where there were 169).

    Rejected: dropping ", reported by ..." and its detail where the verdict
    qualifies it, which leaves the summary saying less than the table beneath it,
    or, with the check's hand-off gone, calling it a disagreement of its own that
    the build reports under no rule; and having the reader say it, which has no
    club rules to say it under.
    """

    MODES = ("plain", "--redact")

    def said(self, mode: str = "plain",
             **fields) -> tuple[pdf.Page, list[str], list[str]]:
        """The page this file's card makes with *fields* changed, and the doctor's
        summary and verdict on it, by line, plain or as ``--redact`` publishes
        them."""
        card = cw.write_card_file(Path(self._tmp.name) / FILENAME,
                                  dataclasses.replace(_spec(), **fields))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            if mode == "--redact":
                doctor._one_file(card, page=None, redact=True)
            else:
                doctor._report(card, page=None, label=str(card), redactor=None)
        summary, _, verdict = buffer.getvalue().partition(
            f"\n{doctor.INDENT}verdict\n")
        summary = summary.partition(f"{doctor.INDENT}reader's own summary\n")[2]
        return pdf.extract(card), summary.splitlines(), verdict.splitlines()

    @staticmethod
    def summary_of(lines: list[str], name: str) -> str:
        """The summary's line for the check *name*, the lines it goes on to joined."""
        opening = f"{doctor.INDENT}  cross-check {name}: "
        at = next(at for at, line in enumerate(lines) if line.startswith(opening))
        going_on = itertools.takewhile(
            lambda line: line.startswith(f"{doctor.INDENT}      "), lines[at + 1:])
        return " ".join([lines[at].lstrip(), *(line.lstrip() for line in going_on)])

    @staticmethod
    def against_the_box(page: pdf.Page) -> set[tuple[str, str]]:
        card = scorecard.read_page(page)
        return {(name, scoring) for name, rule in doctor.SET_RULES.items()
                for scoring, rules in doctor.RULESETS.items()
                if validate.rows_against_box(card, rule, rules)}

    def test_a_tick_the_build_reports_only_as_forfeit_contradicted_is_said_so(self):
        forfeited = tuple(dataclasses.replace(
            row, home_games=0, away_games=6, status=cw.SetStatus.FORFEIT,
            status_side="home") for row in _spec().rows)
        for mode in self.MODES:
            with self.subTest(mode=mode):
                page, summary, verdict = self.said(
                    mode, rows=forfeited, forfeited_by="away", verb="DEFEATS",
                    winner="home")
                self.assertEqual(self.against_the_box(page), {
                    (name, scoring) for name in doctor.SET_RULES
                    for scoring in doctor.RULESETS})
                self.assertIn("cross-check winner-tick: disagrees, reported by the "
                              "verb cross-check",
                              "\n".join(scorecard.describe_lines(page)))
                self.assertEqual(self.summary_of(summary, "winner-tick"),
                                 "cross-check winner-tick: disagrees, reported by "
                                 "forfeit-contradicted")
                self.assertEqual(self.check(verdict, "winner-tick"),
                                 "disagrees, reported by forfeit-contradicted")
                self.assertNotIn("by the verb", " ".join([*summary, *verdict]))

    def test_a_tick_handed_on_by_rule_is_said_in_the_tables_words(self):
        for mode in self.MODES:
            with self.subTest(mode=mode):
                page, summary, verdict = self.said(mode, forfeited_by="home")
                self.assertTrue(0 < len(self.against_the_box(page)) < 32)
                state, _, detail = self.check(verdict, "winner-tick").partition("  ")
                self.assertIn("and by forfeit-contradicted where", detail)
                self.assertEqual(self.summary_of(summary, "winner-tick"),
                                 f"cross-check winner-tick: {state} - {detail}")

    def test_a_hand_off_true_under_every_rule_is_said_as_the_reader_wrote_it(self):
        # A forfeit given up part-way, whose rows no rule holds against the box.
        rows = TestTheDoctorSaysUnderWhichRulesTheBuildFailsRowsAgainstTheBox.rows(
            (6, 2), (3, 3), (0, 0), (0, 0))
        page, summary, _ = self.said(forfeited_by="home", rows=rows)
        self.assertEqual(self.against_the_box(page), set())
        written, = [line for line in scorecard.describe_lines(page)
                    if line.startswith("cross-check winner-tick: ")]
        self.assertTrue(written.startswith("cross-check winner-tick: disagrees, "
                                           "reported by the verb cross-check - "))
        laid = _laid(f"{doctor.INDENT}  ", written,
                     f"{doctor.INDENT}      ").splitlines()
        at = summary.index(laid[0])
        self.assertEqual(summary[at:at + len(laid)], laid)

    def test_a_page_the_reader_fails_on_without_refusing_it_keeps_its_summary(self):
        # A failure in a stage the summary and the card are both read by.
        card = cw.write_card_file(Path(self._tmp.name) / FILENAME, _spec())
        buffer = io.StringIO()
        with mock.patch.object(scorecard, "_read_rosters",
                               side_effect=RuntimeError("a bug in the reader")), \
                contextlib.redirect_stdout(buffer), self.assertRaises(RuntimeError):
            doctor._report(card, page=None, label=str(card), redactor=None)
        self.assertIn(f"{doctor.INDENT}reader's own summary\n"
                      f"{doctor.INDENT}  scorecard.describe() raised "
                      f"RuntimeError: a bug in the reader\n"
                      f"{doctor.INDENT}verdict\n", buffer.getvalue())

    def test_a_failure_in_the_builds_rules_is_raised_after_the_rows_it_follows(self):
        # The build's rules raising where the verdict asks them, which it asked
        # after its table's cross-checks row, and the summary is now told by.
        card = cw.write_card_file(Path(self._tmp.name) / FILENAME, _spec())
        buffer = io.StringIO()
        with mock.patch.object(validate, "rows_against_box",
                               side_effect=RuntimeError("a bug in the rules")), \
                contextlib.redirect_stdout(buffer), \
                self.assertRaisesRegex(RuntimeError, "a bug in the rules"):
            doctor._report(card, page=None, label=str(card), redactor=None)
        summary, _, verdict = buffer.getvalue().partition(
            f"\n{doctor.INDENT}verdict\n")
        summary = summary.partition(f"{doctor.INDENT}reader's own summary\n")[2]
        self.assertIn("cross-check winner-tick: ", summary)
        self.assertNotIn("raised", summary)
        lines = verdict.splitlines()
        self.assertEqual(lines[0], f"{doctor.INDENT}  This page reads as a scorecard.")
        self.assertTrue(lines[-1].startswith(f"{doctor.INDENT}    cross-checks  "),
                        lines[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
