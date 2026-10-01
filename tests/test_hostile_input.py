"""What happens when the file in ``data/`` is not the file we were promised.

Every other test in this suite feeds the readers a scorecard. This one feeds them
things that are not: empty files, HTML sign-in pages saved with a ``.pdf`` name,
cards cut off half way through a download, cards whose cross-reference table has
been mangled, two different cards spliced together, and twenty kilobytes of random
bytes. None of it is exotic. A club's ``data/`` folder collects whatever the club
puts in it, and the ways it goes wrong are always the same three: a download that
died, a file that was never a scorecard, and a PDF whose internal bookkeeping does
not describe its own contents.

What is defended here is not the parse. Most of these files cannot be parsed and
are not meant to be. It is the four promises around the parse:

**Nothing raises.** A directory walk that dies on the ninth file has cost the club
the season, and a traceback tells them nothing they can act on. Rule F4 -- every
PDF found is either scored or reported by path -- is only possible if a malformed
file is a *result* rather than an exception. So ``scorecard.read`` is asserted
never to raise, and neither ``build.py`` nor ``tools/doctor.py`` -- the tool
somebody runs precisely *because* a file is broken -- may print a traceback.

**No damaged file yields a silently different card.** This is the one that
matters. A file this suite can refuse is harmless; a file that parses into a
plausible wrong scoreline is the failure this whole project is built to prevent.
Measured over the corpus below: ten of these files recover a genuine card
*unchanged*, because the reader trusts none of the bookkeeping they damage; one,
cut off inside its roster block, keeps every score and reads neither squad, and
says so; two lose a checkbox the card needs and are refused for it, since a box
the reader cannot name reads as a box nobody ticked; and the one that does produce
a different card is caught by the printed TOTAL row disagreeing with the set rows.
Not one of those three scores.

**Nothing hangs.** Every subprocess here runs under a timeout, so a corrupt length
field that sends a reader round a loop fails this file rather than a club's
afternoon.

**Redaction holds on a page that will not parse.** ``--redact`` learns the words it
must hide by reading the page, so the interesting case is the page it cannot read.
The file's own name is checked to be absent from every redacted report, because a
scorecard is normally named after the two teams that played.

Most of this held on the first run. One thing did not: a card that had lost a set
row made ``discover`` refuse the competition, and the refusal came out of
``build.py`` as a traceback with the report swallowed, so the message naming the
disagreeing files was never printed. That is fixed, and
:meth:`TestABuildSurvivesThem.test_a_card_that_lost_a_set_row_is_refused_in_words`
is why it stays fixed.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from matchcentre import cardwriter as cw, pdf, scorecard             # noqa: E402
from matchcentre.card import Card, CardError                         # noqa: E402
from test_pipeline import TEAMS, squad_for, write_season_cards       # noqa: E402

BUILD = ROOT / "build.py"
DOCTOR = ROOT / "tools" / "doctor.py"

# Long enough that a hang is unambiguous, short enough that the suite still
# finishes: no case here does more than read a handful of small files.
TIMEOUT = 300

TRACEBACK = "Traceback (most recent call last)"

# Seeded, so the "random bytes" cases are the same random bytes on every machine
# and in every run. A fuzz case that cannot be reproduced from a failure message
# is a fuzz case nobody can fix.
SEED = 20260916


def _garbage(count: int, seed: int) -> bytes:
    return bytes(random.Random(seed).getrandbits(8) for _ in range(count))


def _mangle(raw: bytes, pattern: bytes, replacement: bytes) -> bytes:
    r"""Replace the first match, and insist there was one.

    A mutation that silently matched nothing would leave a *valid* card in the
    corpus under a name claiming it was broken, and the case would then pass by
    describing the wrong file. So this raises instead, and it raises in
    ``setUpClass``, where it cannot be mistaken for a failure of the reader. The
    replacement may use ``\1`` to keep part of what it overwrote.
    """
    changed, count = re.subn(pattern, replacement, raw, count=1)
    if not count:
        raise AssertionError(f"the generated card contains no {pattern!r}, so "
                             f"this corpus entry would not be malformed")
    return changed


def build_corpus(card: bytes, other: bytes) -> dict[str, bytes]:
    """Every hostile file, by the name it is written under.

    ``card`` and ``other`` are two genuine, different scorecards. The cases fall
    into three kinds, and all three happen to real clubs: files that were never
    PDFs, PDFs that stop early, and PDFs whose bookkeeping lies about their own
    contents.
    """
    size = len(card)
    corpus = {
        # --- never a PDF ---
        "hostile-empty": b"",
        "hostile-blank-lines": b"\n\n\n",
        "hostile-plain-text": b"Round 3 results\nGnats 6 Challengers 3\n",
        # What a failed download actually leaves on disk: the site answers with a
        # sign-in page and the browser saves it under the link's name.
        "hostile-html-error-page": (b"<!doctype html><html><head><title>Sign "
                                    b"in</title></head><body>Your session has "
                                    b"expired.</body></html>"),
        "hostile-random-bytes": _garbage(20_000, SEED),
        "hostile-random-with-pdf-header": b"%PDF-1.4\n" + _garbage(8_000, SEED + 1),
        "hostile-zip-not-pdf": b"PK\x03\x04" + _garbage(2_000, SEED + 2),
        # --- stops early ---
        # The fractions matter: cut at 1% there is no object table at all, and the
        # others stop inside the page's content stream -- at 95%, inside the roster
        # block at the foot of the page, after the last name and before the
        # fill-ins heading that ends the block.
        "hostile-truncated-1pc": card[:size // 100],
        "hostile-truncated-25pc": card[:size // 4],
        "hostile-truncated-50pc": card[:size // 2],
        "hostile-truncated-95pc": card[:size * 95 // 100],
        "hostile-header-removed": card[card.index(b"\n") + 1:],
        # --- bookkeeping that lies ---
        # Every one of these is a well-formed PDF as far as a text editor is
        # concerned, which is what makes them the interesting half of the corpus.
        "hostile-startxref-past-eof": _mangle(card, rb"startxref\s*\d+",
                                              b"startxref\n999999999"),
        "hostile-startxref-negative": _mangle(card, rb"startxref\s*\d+",
                                              b"startxref\n-1"),
        "hostile-no-xref": _mangle(card, rb"\bxref\b", b"xrEf"),
        "hostile-no-trailer": _mangle(card, rb"\btrailer\b", b"trailAr"),
        "hostile-length-absurd": _mangle(card, rb"/Length \d+", b"/Length 99999999"),
        "hostile-length-negative": _mangle(card, rb"/Length \d+", b"/Length -8"),
        "hostile-page-count-lies": _mangle(card, rb"/Count \d+", b"/Count 90000"),
        # Added rather than corrupted: cardwriter's streams are uncompressed, so
        # there is no /Filter to break. A stream claiming a codec the reader does
        # not have is what a change at the website's end looks like from here.
        "hostile-unsupported-filter": _mangle(card, rb"/Length (\d+)",
                                              rb"/Filter /JPXDecode /Length \1"),
        "hostile-root-object-gone": _mangle(card, rb"/Type\s*/Catalog",
                                            b"/Type /Nonsense"),
        "hostile-nul-bytes-injected": (card[:size * 3 // 10] + b"\0" * 100
                                       + card[size * 3 // 10:]),
        "hostile-reversed": card[::-1],
        # Two cards where one was expected. Splicing at the same fraction of each
        # leaves a file whose first half's offsets all point into the second half's
        # bytes, which is the shape a resumed download produces.
        "hostile-spliced-cards": card[:size // 2] + other[len(other) // 2:],
        "hostile-two-cards-concatenated": card + other,
        # Deep nesting, in case anything in the reader recurses per bracket. If it
        # does, this is a RecursionError in the middle of a directory walk rather
        # than a rejection, and one file has cost the whole season.
        "hostile-deeply-nested": (b"%PDF-1.4\n1 0 obj\n" + b"[" * 20_000
                                  + b"]" * 20_000 + b"\nendobj\n"),
    }
    flipped = bytearray(card)
    flipped[size * 2 // 5] ^= 0xFF
    corpus["hostile-byte-flipped-mid-stream"] = bytes(flipped)
    return corpus


# How each file behaves, measured against the readers as they stand. This is the
# interesting content of this file: it is not a guess at what *ought* to happen but
# a record of what does, so that a change in either direction fails a test and has
# to be argued for.
#
# Nothing readable comes back. Not one byte of any of these files reaches a card.
UNREADABLE = frozenset({
    "hostile-empty",
    "hostile-blank-lines",
    "hostile-plain-text",
    "hostile-html-error-page",
    "hostile-random-bytes",
    "hostile-random-with-pdf-header",
    "hostile-zip-not-pdf",
    "hostile-truncated-1pc",
    "hostile-truncated-25pc",
    "hostile-header-removed",
    "hostile-unsupported-filter",
    "hostile-reversed",
    "hostile-deeply-nested",
})

# A genuine card comes back, *unchanged*. Surprising once and then obvious: the
# reader locates objects by scanning for them and measures the page from the marks
# on it, so it never consults the xref table, the trailer, ``startxref``,
# ``/Length``, ``/Count`` or the catalog -- which is everything these files damage.
# The last is worth reading twice. A file holding two whole cards is read as the
# second one, which is what a PDF viewer shows too, because appending a document is
# how the format spells "revised".
RECOVERS_A_GENUINE_CARD = frozenset({
    "hostile-startxref-past-eof",
    "hostile-startxref-negative",
    "hostile-no-xref",
    "hostile-no-trailer",
    "hostile-length-absurd",
    "hostile-length-negative",
    "hostile-page-count-lies",
    "hostile-root-object-gone",
    "hostile-nul-bytes-injected",
    "hostile-two-cards-concatenated",
})

# The scores come back unchanged and the squads do not come back at all. The page
# stops inside its roster block, so nothing under the block says where it ends, and
# a squad read from it would be as much of one as the download happened to keep.
# This one kept all six names, which is luck and not something the page can show:
# see ``test_no_cut_inside_the_roster_block_reads_part_of_a_squad``.
LOSES_ITS_SQUADS = frozenset({
    "hostile-truncated-95pc",
})

# The page parses and a checkbox the card needs is not on it, so no card comes
# back, and the refusal counts the boxes it could not name and says what the page
# lacks. Both were read once, with the loss a note on the dashboard and the
# missing boxes read as unticked. Every name here is also a name that must never
# reach a ladder.
LOSES_A_CHECKBOX = {
    # Half a card: three set rows instead of four, fifteen of their eighteen
    # boxes, none of the six whole-match boxes, no printed TOTAL row and no
    # Players section. Read, it was a 3-0 win that only the competition's set
    # count stopped, and a forfeit ticked in the half that is gone would have
    # been invisible.
    "hostile-truncated-50pc": (
        "9 of the 24", "this page prints Forfeit 5 times for its 6 boxes, Retired 5 "
                       "times for its 6 boxes and Incomplete 5 times for its 6 "
                       "boxes"),
    # One byte flipped in the middle of the content stream costs one Retired
    # label, row 2's away one, so its square is drawn and nothing names it.
    # Read, it matched the card it was made from, because that box was not
    # ticked -- which the reader could not have known.
    "hostile-byte-flipped-mid-stream": (
        "1 of the 30", "the one it could not name is row 2's away Retired box; "
                       "this page prints Retired 7 times for its 8 boxes"),
}

# A *different* card comes back, and is caught by one of the defences. Every name
# here is also a name that must never reach a ladder, which the build tests below
# check.
YIELDS_A_DIFFERENT_CARD = {
    # The set rows come from one card and the printed totals from another, so the
    # totals cross-check disagrees and totals-mismatch makes that an error.
    "hostile-spliced-cards": "totals cross-check",
}


def _comparable(card: Card) -> Card:
    """A card stripped of the two things that legitimately differ.

    ``source`` is the path it was read from, and warnings are *about* the damage
    rather than part of the transcription -- a warning is the reader saying so out
    loud, which is the behaviour wanted, not a difference in what was read.
    """
    return dataclasses.replace(card, source="", warnings=())


def round_four_card(**fields) -> cw.CardSpec:
    """A seventh card for the season :func:`write_season_cards` writes.

    Riverside Rockets against Hilltop Aces a week after its third round, with the
    squads the season's own cards print, won 6-2, 6-3, 2-6, 6-1 unless ``fields``
    say otherwise.
    """
    home_name, home_players = TEAMS["riverside-rockets"]
    away_name, away_players = TEAMS["hilltop-aces"]
    home_rows, home = squad_for("riverside-rockets", home_players, None)
    away_rows, away = squad_for("hilltop-aces", away_players, None)
    return cw.CardSpec(**{
        "date": "2026-09-08", "round_label": "4", "division": "B", "court": "07",
        "match_id": "2000099", "home_name": home_name, "away_name": away_name,
        "rows": (cw.SetRow(6, 2, True, home[:2], away[:2]),
                 cw.SetRow(6, 3, False, home[:1], away[:1]),
                 cw.SetRow(2, 6, False, home[1:2], away[1:2]),
                 cw.SetRow(6, 1, True, home[:2], away[:2])),
        "home_roster": home_rows, "away_roster": away_rows, **fields})


class HostileCase(unittest.TestCase):
    """The corpus, written to disk once for the whole class.

    Built by mutating two genuine cards from :mod:`matchcentre.cardwriter`, so no
    real scorecard is needed and every hostile file is damage done to something
    that works rather than a guess at what a scorecard looks like.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.genuine = write_season_cards(cls.root / "genuine")
        cls.corpus = build_corpus(cls.genuine[0].read_bytes(),
                                  cls.genuine[1].read_bytes())
        cls.corpus_dir = cls.root / "hostile"
        cls.corpus_dir.mkdir()
        cls.paths = {}
        for name, raw in cls.corpus.items():
            path = cls.corpus_dir / f"{name}.pdf"
            path.write_bytes(raw)
            cls.paths[name] = path

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def doctor(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run((sys.executable, str(DOCTOR), *argv),
                              capture_output=True, text=True, timeout=TIMEOUT)


class TestTheCorpusIsActuallyHostile(HostileCase):
    """The pre-flight. Without it, every test below could pass over nothing."""

    def test_the_cards_the_corpus_was_built_from_read_cleanly(self):
        # If the generator broke, every mutation of its output would be refused
        # for the wrong reason and this whole file would be testing nothing.
        for path in self.genuine[:2]:
            with self.subTest(card=path.name):
                cards, rejections = scorecard.read(path)
                self.assertEqual(rejections, [])
                self.assertEqual(len(cards), 1)

    def test_every_file_is_classified_and_no_file_is_classified_twice(self):
        classified = (UNREADABLE | RECOVERS_A_GENUINE_CARD | LOSES_ITS_SQUADS
                      | set(LOSES_A_CHECKBOX) | set(YIELDS_A_DIFFERENT_CARD))
        self.assertEqual(sorted(classified), sorted(self.corpus),
                         "a file was added to the corpus without recording what "
                         "it does, or classified under a name that is not in it")
        self.assertEqual(len(classified),
                         len(UNREADABLE) + len(RECOVERS_A_GENUINE_CARD)
                         + len(LOSES_ITS_SQUADS) + len(LOSES_A_CHECKBOX)
                         + len(YIELDS_A_DIFFERENT_CARD))

    def test_the_corpus_covers_every_kind_of_damage_it_claims_to(self):
        # Named kinds rather than a count, because a count is satisfied by twenty
        # variations of the same truncation.
        for kind in ("empty", "plain-text", "html-error-page", "random-bytes",
                     "truncated-50pc", "startxref-past-eof", "length-negative",
                     "unsupported-filter", "spliced-cards", "deeply-nested",
                     "byte-flipped-mid-stream"):
            with self.subTest(kind=kind):
                self.assertIn(f"hostile-{kind}", self.corpus)


class TestTheReadersRefuseWithoutRaising(HostileCase):

    def test_reading_one_file_never_raises(self):
        for name, path in self.paths.items():
            with self.subTest(file=name):
                try:
                    scorecard.read(path)
                except Exception as exc:                          # noqa: BLE001
                    self.fail(f"scorecard.read raised "
                              f"{type(exc).__name__}: {exc}")

    def test_a_file_that_yields_no_card_yields_a_reason_instead(self):
        for name in sorted(UNREADABLE):
            with self.subTest(file=name):
                cards, rejections = scorecard.read(self.paths[name])
                self.assertEqual(cards, [])
                self.assertTrue(rejections, "a file that produced no cards must "
                                            "produce a reason (rule F4)")
                for rejection in rejections:
                    self.assertEqual(rejection.path, str(self.paths[name]))
                    self.assertTrue(rejection.reason.strip())

    def test_reading_a_tree_of_nothing_but_hostile_files_never_raises(self):
        # The walk, not the file: read_tree is what build.py calls, and one bad
        # file must cost that file only.
        try:
            cards, rejections = scorecard.read_tree(self.corpus_dir)
        except Exception as exc:                                  # noqa: BLE001
            self.fail(f"scorecard.read_tree raised {type(exc).__name__}: {exc}")
        self.assertEqual(len(cards),
                         len(RECOVERS_A_GENUINE_CARD) + len(LOSES_ITS_SQUADS)
                         + len(YIELDS_A_DIFFERENT_CARD))
        named = {Path(r.path).name for r in rejections}
        self.assertEqual(named, {f"{name}.pdf"
                                 for name in UNREADABLE | set(LOSES_A_CHECKBOX)},
                         "every file that produced no card must be reported by "
                         "path, and no other file should be (rule F4)")


@unittest.skipIf(os.name == "nt" or os.geteuid() == 0,
                 "a mode this account cannot list needs POSIX and a user not root")
class TestAFolderTheWalkCannotListIsOneRefusalByItsOwnName(unittest.TestCase):
    """A scorecard folder under ``data/`` that cannot be listed is refused, by name.

    Measured before on the demo season, rebuilt after ``chmod 000
    data/demo-season/round-05``: exit 0, ``pages seen 12, scored 12, refused 0``,
    nothing on stderr, and three cards gone from the dashboard with only three
    ``gone`` lines to say so. ``Path.rglob`` skips a directory it may not list
    without raising, and ``open_folder`` opens only the root. At ``chmod 444`` the
    same folder cost the whole season instead: its names could be read and its
    files not reached, ``is_file`` raised, and the root was refused, ``pages seen
    1, refused 1``. Now the walk is ``os.walk`` with an ``onerror``, and each
    folder it could not list or could not enter is one refusal, with the root's
    reason, the error's own words as detail, and ``folder`` set so no reader counts it as a
    card. Rejected: a section and an exit status of its own, a second kind every
    reader of the refused list would have to learn.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.cards = write_season_cards(self.data)

    def shut(self, name: str, mode: int = 0) -> Path:
        folder = self.data / name
        folder.chmod(mode)
        self.addCleanup(folder.chmod, 0o755)
        return folder

    def test_a_folder_with_no_permissions_is_one_refusal_and_the_rest_is_read(self):
        shut = self.shut("Round3")
        cards, rejected = scorecard.read_tree(self.data)
        self.assertEqual([(r.path, r.reason, r.detail) for r in rejected],
                         [(str(shut), "the directory could not be listed",
                           "Permission denied")])
        self.assertEqual([r.folder for r in rejected], [True])
        self.assertEqual(sorted(c.source for c in cards),
                         sorted(str(p) for p in self.cards[:4]))

    def test_a_folder_it_may_list_and_not_enter_is_one_refusal_and_not_the_season(
            self):
        # Mode 444: the names are there and nothing under them can be reached. One
        # refusal for the folder, and none for the folder inside it, which is the
        # same missing permission a second time.
        (self.data / "Round3" / "late").mkdir()
        (self.data / "Round3" / "late" / "copy.pdf").write_bytes(
            self.cards[0].read_bytes())
        shut = self.shut("Round3", 0o444)
        cards, rejected = scorecard.read_tree(self.data)
        self.assertEqual([(r.path, r.reason, r.detail, r.folder) for r in rejected],
                         [(str(shut), "the directory could not be listed",
                           "Permission denied", True)])
        self.assertEqual(len(cards), 4)

    def test_a_folder_of_folders_it_may_list_and_not_enter_is_one_refusal(self):
        # With no file of its own to be refused at, it would otherwise be refused
        # once for each folder inside it, all for the one mode on it.
        for name in ("early", "late"):
            (self.data / "Extra" / name).mkdir(parents=True)
            (self.data / "Extra" / name / "copy.pdf").write_bytes(
                self.cards[0].read_bytes())
        shut = self.shut("Extra", 0o444)
        cards, rejected = scorecard.read_tree(self.data)
        self.assertEqual([(r.path, r.detail) for r in rejected],
                         [(str(shut), "Permission denied")])
        self.assertEqual(len(cards), 6)

    def test_a_refusal_says_whether_it_is_a_folders_or_a_cards(self):
        cut = self.data / "Round1" / "cut.pdf"
        cut.write_bytes(self.cards[0].read_bytes()[:1024])
        shut = self.shut("Round3")
        _, missing = scorecard.read_tree(self.data / "nowhere")
        _, rejected = scorecard.read_tree(self.data)
        self.assertEqual([(r.path, r.folder) for r in [*missing, *rejected]],
                         [(str(self.data / "nowhere"), True), (str(cut), False),
                          (str(shut), True)])

    def test_the_refusals_are_in_path_order_with_the_folders_among_the_cards(self):
        # Without the sort (/tmp/mutate292.py) the folders come in the order the
        # disk lists them, and before every card; the page lists the refusals in
        # the document's order, and the report in path order.
        (self.data / "Extra").mkdir()
        cut = self.data / "Round1" / "cut.pdf"
        cut.write_bytes(self.cards[0].read_bytes()[:1024])
        extra, shut = self.shut("Extra"), self.shut("Round3")
        _, rejected = scorecard.read_tree(self.data)
        self.assertEqual([r.path for r in rejected], [str(extra), str(cut), str(shut)])

    def test_a_folder_the_system_keeps_for_itself_is_left_unread_and_unsaid(self):
        """The folders a drive's top has that nobody files a card in, as before.

        Measured (``/tmp/t7/scratch/FOLDERS/probe2.py``) on the demo, with
        ``lost+found``, ``.Spotlight-V100``, ``.Trashes`` and ``System Volume
        Information`` under ``data/`` at mode 000, as the top of an ext4 drive, a
        Mac's and a Windows one keep them from the account that builds: the
        season built, and the run exited 1 over ``4 folder(s) not opened``,
        telling the club to give that account permission to read the system's
        own folders. Before the reader refused a folder they were skipped in
        silence, exit 0; they still are when they cannot be listed, and one that
        can is read as it always was. Rejected: refusing them, a failed run on
        every such drive; skipping them whether or not they can be listed, which
        loses in silence a card someone did file there, the very thing a folder
        is refused to prevent; and a README line alone, which leaves the run
        failing.
        """
        for name in ("lost+found", ".Spotlight-V100", "System Volume Information",
                     "$RECYCLE.BIN/S-1-5-21"):
            (self.data / name).mkdir(parents=True)
            self.shut(name)
        (self.data / ".sync").mkdir()
        (self.data / ".sync" / "kept.pdf").write_bytes(self.cards[0].read_bytes())
        cards, rejected = scorecard.read_tree(self.data)
        self.assertEqual(rejected, [])
        self.assertEqual(len(cards), 7)
        self.assertEqual(scorecard.files_under(self.data),
                         sorted([*self.cards, self.data / ".sync" / "kept.pdf"],
                                key=str))

    def test_a_data_folder_that_is_hidden_or_under_a_hidden_one_is_still_refused(
            self):
        # Only the folders under data/ are the system's: data/ itself is the club's
        # whatever it is called, and so is the folder it sits in, as a
        # ~/.local/share would be.
        data = self.data.parent / ".club" / ".cards"
        cards = write_season_cards(data)
        shut = data / "Round3"
        shut.chmod(0)
        self.addCleanup(shut.chmod, 0o755)
        self.assertEqual([r.path for r in scorecard.read_tree(data)[1]], [str(shut)])
        shut.chmod(0o755)
        data.chmod(0)
        self.addCleanup(data.chmod, 0o755)
        self.assertEqual([(r.path, r.folder) for r in scorecard.read_tree(data)[1]],
                         [(str(data), True)])
        self.assertIsNone(scorecard.files_under(data))
        self.assertEqual(len(cards), 6)

    def test_a_link_to_nothing_is_no_file_and_no_refusal(self):
        """A link that leads nowhere is skipped, as ``Path.is_file`` skipped it.

        The reader's own stat, for Python 3.14's ``is_file``, answers False only
        to the errors that say nothing is there. Tried with that set empty
        (``/tmp/mutate292.py``): a link to a card since deleted, a link to itself
        and a link through a file each cost the whole round it sat in, refused as
        a folder that could not be listed. Rejected: answering False to every
        error, which is 3.14's own rule and the one a folder at mode 444 was
        skipped in silence under.
        """
        round1 = self.data / "Round1"
        (round1 / "gone.pdf").symlink_to(self.data / "deleted.pdf")
        (round1 / "loop.pdf").symlink_to(round1 / "loop.pdf")
        (round1 / "under.pdf").symlink_to(self.cards[0] / "inner.pdf")
        cards, rejected = scorecard.read_tree(self.data)
        self.assertEqual(rejected, [])
        self.assertEqual(len(cards), 6)

    def test_the_files_beside_a_folder_it_could_not_list_are_still_accounted_for(
            self):
        # The listed part of the tree is listed as it was: a stray there is still
        # named, and the cards there are still counted as waiting.
        (self.data / "Round1" / "draw.xlsx").write_bytes(b"not a pdf")
        self.shut("Round3")
        self.assertEqual(scorecard.unread_files(self.data),
                         [self.data / "Round1" / "draw.xlsx"])
        self.assertEqual(scorecard.cards_waiting(self.data), 4)


class TestNoDamagedFileYieldsASilentlyDifferentCard(HostileCase):
    """The property the rest of this file exists to support."""

    def genuine_cards(self) -> list[Card]:
        return [_comparable(scorecard.read(p)[0][0]) for p in self.genuine[:2]]

    def test_a_recovered_card_is_a_genuine_card_and_not_an_approximation(self):
        genuine = self.genuine_cards()
        for name in sorted(RECOVERS_A_GENUINE_CARD):
            with self.subTest(file=name):
                cards, _ = scorecard.read(self.paths[name])
                self.assertEqual(len(cards), 1)
                self.assertIn(_comparable(cards[0]), genuine,
                              "this file's damage was expected to cost nothing, "
                              "so the card must match one of the two it was made "
                              "from exactly -- every score, name and rating")

    def test_a_card_cut_off_in_its_roster_block_keeps_its_scores_and_no_squad(self):
        squadless = [dataclasses.replace(card, home_roster=(), away_roster=())
                     for card in self.genuine_cards()]
        for name in sorted(LOSES_ITS_SQUADS):
            with self.subTest(file=name):
                (card,), _ = scorecard.read(self.paths[name])
                self.assertEqual((card.home_roster, card.away_roster), ((), ()))
                self.assertIn(_comparable(card), squadless,
                              "everything above the roster block was in the file, "
                              "so everything above it must be read unchanged")
                self.assertEqual(card.warnings, (scorecard.ROSTER_BLOCK_UNBOUNDED,))

    def test_no_cut_inside_the_roster_block_reads_part_of_a_squad(self):
        """A download that dies in the roster block, cut after every word drawn in it.

        Measured on this card while the block could run to the foot of the page: of
        the 42 places to cut, 14 read a wrong squad and said nothing -- the home
        squad whole, the away squad short, and in three of them a first name cut
        off from its surname standing as a player -- 19 read a short squad with a
        warning about the other side only, 4 read none, and 5 read both squads
        whole because of where the cut happened to fall. No page among them says
        where the block ends, so the five cannot be told from the fourteen.
        """
        raw = self.genuine[0].read_bytes()

        def shown(text: str) -> bytes:
            return b"<" + text.encode("latin-1").hex().upper().encode() + b">"

        start, stop = raw.index(shown("Players")), raw.index(shown("Fill-ins"))
        cuts = [m.end() for m in re.finditer(rb"> Tj", raw) if start < m.end() <= stop]
        # One per word at the very least, or the block was not where this looked.
        self.assertGreaterEqual(len(cuts), 12, "the roster block was not found in "
                                               "the card's content stream")
        folder = self.root / "cut-in-the-roster-block"
        folder.mkdir(exist_ok=True)
        for index, cut in enumerate(cuts):
            with self.subTest(cut=index):
                path = folder / f"cut-{index:02d}.pdf"
                path.write_bytes(raw[:cut])
                (card,), _ = scorecard.read(path)
                self.assertEqual((card.home_roster, card.away_roster), ((), ()))
                self.assertEqual(card.warnings, (scorecard.ROSTER_BLOCK_UNBOUNDED,))

    def test_a_card_that_differs_says_so_by_failing_a_check_or_a_count(self):
        genuine = self.genuine_cards()
        for name, caught_by in sorted(YIELDS_A_DIFFERENT_CARD.items()):
            with self.subTest(file=name):
                cards, _ = scorecard.read(self.paths[name])
                self.assertEqual(len(cards), 1)
                card = cards[0]
                self.assertNotIn(_comparable(card), genuine)
                failed = [c.name for c in card.checks
                          if c.available and not c.agrees]
                short = len(card.sets) != len(genuine[0].sets)
                self.assertTrue(failed or short or card.warnings,
                                f"{name} produced a card that differs from every "
                                f"genuine one with nothing to show it: no failing "
                                f"cross-check, the expected number of set rows, "
                                f"and no warning. That is a plausible wrong "
                                f"scoreline, which is the one outcome this "
                                f"project may not have.")
                if caught_by == "totals cross-check":
                    self.assertIn("totals", failed)
                else:
                    self.assertTrue(short)

    def test_a_card_that_lost_a_checkbox_is_refused_saying_how_many(self):
        # Rule: absence of evidence is not evidence. Both were read, the half card
        # with the two cross-checks it lost reported unavailable, and each with the
        # boxes it lost read as unticked. A box that is not there cannot say that
        # nobody ticked it, so neither card comes back, and the reason counts the
        # boxes the reader could not name against the boxes the card needs. Of
        # the kind for a page short of what its boxes need, because both were
        # once given the labels' kind, and the doctor told the club the card was
        # printed from another template.
        for name, (lost, which) in sorted(LOSES_A_CHECKBOX.items()):
            with self.subTest(file=name):
                cards, rejections = scorecard.read(self.paths[name])
                self.assertEqual(cards, [])
                rejection, = rejections
                self.assertTrue(rejection.reason.startswith(
                    f"this reader could not name {lost} checkboxes this card "
                    f"needs, so a tick in "), rejection.reason)
                self.assertIn(which, rejection.detail)
                with self.assertRaises(scorecard.MissingCheckboxes):
                    scorecard.read_page(pdf.extract(self.paths[name]))


class TestTheDoctorSurvivesEveryOneOfThem(HostileCase):
    """The tool whose entire job is the files in this corpus."""

    def test_it_never_prints_a_traceback(self):
        for name, path in self.paths.items():
            with self.subTest(file=name):
                done = self.doctor(str(path))
                self.assertNotIn(TRACEBACK, done.stdout + done.stderr,
                                 f"doctor.py died on {name}.pdf:\n"
                                 f"{(done.stdout + done.stderr)[-2000:]}")

    def test_it_always_exits_with_a_status_it_documents(self):
        for name, path in self.paths.items():
            with self.subTest(file=name):
                done = self.doctor(str(path))
                self.assertIn(done.returncode, (0, 1),
                              f"doctor.py exited {done.returncode} on "
                              f"{name}.pdf; 0 and 1 are the documented statuses")

    def test_it_says_which_file_it_is_talking_about(self):
        for name, path in self.paths.items():
            with self.subTest(file=name):
                done = self.doctor(str(path))
                self.assertIn(f"{name}.pdf", done.stdout,
                              "a report that does not name the file cannot be "
                              "acted on when several were passed")

    def test_it_survives_being_given_all_of_them_at_once(self):
        done = self.doctor(*(str(p) for p in self.paths.values()))
        self.assertNotIn(TRACEBACK, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 1)
        for name in self.corpus:
            with self.subTest(file=name):
                self.assertIn(f"{name}.pdf", done.stdout)


class TestRedactionHoldsOnFilesThatDoNotParse(HostileCase):
    """``--redact`` when there is no page to learn the secrets from."""

    def test_it_never_prints_a_traceback(self):
        for name, path in self.paths.items():
            with self.subTest(file=name):
                done = self.doctor("--redact", str(path))
                self.assertNotIn(TRACEBACK, done.stdout + done.stderr,
                                 f"doctor.py --redact died on {name}.pdf:\n"
                                 f"{(done.stdout + done.stderr)[-2000:]}")

    def test_it_never_publishes_the_file_name_or_its_path(self):
        # The case redaction exists for. A scorecard is named after the two teams
        # that played, and a file this broken is exactly the one a club is about to
        # paste into a public issue.
        for name, path in self.paths.items():
            with self.subTest(file=name):
                done = self.doctor("--redact", str(path))
                printed = done.stdout + done.stderr
                self.assertNotIn(name, printed, "the file's own name survived "
                                                "redaction")
                self.assertNotIn(str(path), printed)
                self.assertNotIn(str(self.corpus_dir), printed)

    def test_redacting_does_not_change_the_verdict(self):
        for name, path in self.paths.items():
            with self.subTest(file=name):
                plain = self.doctor(str(path))
                redacted = self.doctor("--redact", str(path))
                self.assertEqual(plain.returncode, redacted.returncode,
                                 "--redact is about what is printed, not about "
                                 "whether the file could be read")


class TestABuildSurvivesThem(HostileCase):
    """``build.py``, which is what a club actually runs."""

    def project(self, files: set[str], *, with_cards: bool = True) -> Path:
        """A throwaway project directory whose ``data/`` holds ``files``."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd = Path(tmp.name)
        data = cwd / "data"
        if with_cards:
            write_season_cards(data)
        data.mkdir(parents=True, exist_ok=True)
        for name in files:
            (data / f"{name}.pdf").write_bytes(self.corpus[name])
        return cwd

    def build(self, cwd: Path) -> tuple[int, str]:
        done = subprocess.run(
            (sys.executable, str(BUILD), "--generated", "2026-09-16T12:00:00"),
            cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
        report = done.stdout + done.stderr
        self.assertNotIn(TRACEBACK, report,
                         f"build.py printed a traceback:\n{report[-3000:]}")
        return done.returncode, report

    def test_unreadable_files_beside_a_good_season_are_all_named(self):
        code, report = self.build(self.project(set(UNREADABLE)))
        self.assertEqual(code, 1, "cards found but not scored means the ladder is "
                                  "short, which is a failure and not a smaller "
                                  "season (rule F4)")
        for name in sorted(UNREADABLE):
            with self.subTest(file=name):
                self.assertIn(f"{name}.pdf", report)

    def test_a_data_folder_of_nothing_but_rubbish_fails_without_crashing(self):
        code, report = self.build(self.project(set(UNREADABLE),
                                              with_cards=False))
        self.assertEqual(code, 1)
        # And says so in words, rather than presenting an empty dashboard as a
        # season in which nobody happened to play. Both halves matter: how many
        # files were found, and that none of them was a scorecard.
        self.assertIn("no scorecards were read", report.lower())
        self.assertIn(f"{len(UNREADABLE)} card(s) found and not scored", report)

    def test_a_card_that_lost_a_set_row_is_refused_in_words(self):
        # The regression. This card reads as a complete three-set match, and the
        # competition is scored as four, so `discover` refuses to score either
        # reading -- correctly. What went wrong was the telling: the refusal left
        # build.py as an uncaught DiscoveryError, so a club saw a traceback and
        # never saw the sentence naming the file.
        #
        # Written rather than cut from the corpus card, which it once was. Every
        # cut of that card that loses a set row loses checkboxes with it, and the
        # reader refuses those before `discover` sees them: measured over each
        # whole percent from 30 to 79 by /tmp/t4/scratch/y434/m3/cuts.py.
        cwd = self.project(set())
        spec = round_four_card()
        cw.write_card_file(cwd / "data" / "lost-a-row.pdf",
                           dataclasses.replace(spec, rows=spec.rows[:3]))
        code, report = self.build(cwd)
        self.assertEqual(code, 2)
        self.assertIn("lost-a-row.pdf", report)
        self.assertIn("do not agree on the shape of a match", report)

    def test_a_spliced_card_is_refused_by_its_own_printed_totals(self):
        # Same requirement, caught by a different defence: this card has the right
        # number of set rows, so only the TOTAL row printed on it gives the splice
        # away. The reader's totals check disagrees, and totals-mismatch, which
        # faults every row that check does and names the cells, reports it once
        # (4.135), where F6 reported the check beside it.
        code, report = self.build(self.project({"hostile-spliced-cards"}))
        self.assertNotEqual(code, 0)
        words = " ".join(report.split())
        self.assertIn("totals-mismatch", words)
        self.assertIn("the printed TOTAL row and the set rows disagree", words)
        self.assertNotIn("the totals cross-check disagrees", words)

    def test_no_hostile_file_ever_reaches_a_published_ladder(self):
        # The end of the argument. Whatever any of these files parsed into, the
        # run either refused to write anything or exited nonzero -- so nothing
        # here can become a number on a page that a club believes.
        for name in sorted(set(YIELDS_A_DIFFERENT_CARD) | set(LOSES_A_CHECKBOX)):
            with self.subTest(file=name):
                code, _ = self.build(self.project({name}))
                self.assertNotEqual(code, 0, "a damaged card that parses must "
                                             "still fail the run")


# --- a refusal over the checkboxes -------------------------------------------
#
# Not damage to a file but a different template: the same card with its tick-box
# labels in another template's words. Kept here because it is the same question as
# the rest of this file -- a file that is not the one we were promised -- and because
# the answer has to be a sentence a club can act on rather than a wrong one.

# The seven words the template prints beside its boxes, each with another
# template's word for it. Invented, and chosen to be nothing this reader looks for.
OTHER_TEMPLATE_LABELS = {
    "Forfeit": "Walkover", "Retired": "Withdrawn", "Incomplete": "Unfinished",
    "Winner": "Won", "Forfeited": "Conceded", "Cancelled": "Abandoned",
    "Disputed": "Contested",
}


def relabelled(raw: bytes, words: dict[str, str]) -> bytes:
    """``raw`` with every one of ``words`` printed as its replacement instead.

    :mod:`~matchcentre.cardwriter` shows each word as its own hex string, so a
    word is replaced where it is printed and nothing else on the page moves but
    the words themselves. Raises if a word is not on the card, for the reason
    :func:`_mangle` does: a replacement that matched nothing leaves the genuine
    card under a name that says otherwise.
    """
    def hexed(word: str) -> bytes:
        return b"<" + word.encode("latin-1").hex().upper().encode() + b">"

    for old, new in words.items():
        raw, count = re.subn(re.escape(hexed(old)), hexed(new), raw)
        if not count:
            raise AssertionError(f"the generated card does not print {old!r}")
    return raw


def _box_part(rect: pdf.Rect) -> bool:
    """One of the five rects cardwriter paints for a box: an edge or a tick."""
    def near(value: float) -> bool:
        return any(abs(value - size) < 0.05 for size in (cw.BOX_SIDE, cw.HAIRLINE))
    return (rect.filled and near(rect.w) and near(rect.h)
            and max(rect.w, rect.h) > cw.HAIRLINE + 0.05)


def _box_edge(rect: pdf.Rect) -> bool:
    """A box's top or bottom edge: a box side wide and a hairline tall."""
    return (_box_part(rect) and abs(rect.w - cw.BOX_SIDE) < 0.05
            and abs(rect.h - cw.HAIRLINE) < 0.05)


def boxes_moved(page: pdf.Page, down: float) -> pdf.Page:
    """``page`` with every box, edges and ticks together, moved ``down`` points.

    Whole squares still, and still on the page, but off the line their labels are
    printed on -- which no template this project writes can be asked to do.
    """
    def moved(rect: pdf.Rect) -> pdf.Rect:
        if not _box_part(rect):
            return rect
        return dataclasses.replace(rect, y0=rect.y0 + down, y1=rect.y1 + down)
    return dataclasses.replace(page, rects=tuple(moved(r) for r in page.rects))


def box_edges_unpaired(page: pdf.Page) -> pdf.Page:
    """``page`` keeping one top-or-bottom edge in each column of boxes.

    The side edges and every hairline's width are as they were, so the page still
    measures a box side; what it has lost is any top edge with a bottom to pair
    with, which is what a square is rebuilt from.
    """
    seen: set[float] = set()
    kept = []
    for rect in sorted(page.rects, key=lambda r: r.y0):
        if _box_edge(rect):
            if round(rect.x0, 1) in seen:
                continue
            seen.add(round(rect.x0, 1))
        kept.append(rect)
    return dataclasses.replace(page, rects=tuple(kept))


def fewer_boxes(page: pdf.Page, count: int = 1) -> pdf.Page:
    """``page`` without its ``count`` topmost boxes: their edges and any tick in them.

    The topmost box edge left is always a top edge, since its bottom is lower, so
    taking the box under it ``count`` times over takes whole boxes and only those.
    """
    for _ in range(count):
        top = min((r for r in page.rects if _box_edge(r)),
                  key=lambda r: (r.y0, r.x0))

        def inside(rect: pdf.Rect, top: pdf.Rect = top) -> bool:
            return (_box_part(rect)
                    and top.x0 - 0.1 <= rect.x0 <= top.x0 + cw.BOX_SIDE
                    and top.y0 - 0.1 <= rect.y0 <= top.y0 + cw.BOX_SIDE)
        page = dataclasses.replace(
            page, rects=tuple(r for r in page.rects if not inside(r)))
    return page


def row_one_box_lost(page: pdf.Page, label: str) -> pdf.Page:
    """``page`` without row 1's home ``label`` box, one of the set's three: its
    edges and any tick in it, as :func:`fewer_boxes` takes the Forfeit one."""
    x = cw.HOME_STATUS_BOX_X[scorecard._SET_STATUS_LABELS.index(label)]
    top = min((r for r in page.rects if _box_edge(r) and abs(r.x0 - x) < 0.1),
              key=lambda r: r.y0)

    def inside(rect: pdf.Rect) -> bool:
        return (_box_part(rect)
                and top.x0 - 0.1 <= rect.x0 <= top.x0 + cw.BOX_SIDE
                and top.y0 - 0.1 <= rect.y0 <= top.y0 + cw.BOX_SIDE)
    return dataclasses.replace(
        page, rects=tuple(r for r in page.rects if not inside(r)))


def box_edges_removed(page: pdf.Page) -> pdf.Page:
    """``page`` with no box's top or bottom edge on it at all."""
    return dataclasses.replace(
        page, rects=tuple(r for r in page.rects if not _box_edge(r)))


def box_parts_outlined(page: pdf.Page) -> pdf.Page:
    """``page`` with every box's edges and ticks stroked instead of filled.

    Each is the same shape in the same place, so a looser eye than the reader's
    -- the doctor's census is one -- still sees every square. The reader does
    not, because it measures a box side from filled hairlines only.
    """
    return dataclasses.replace(page, rects=tuple(
        dataclasses.replace(r, filled=False) if _box_part(r) else r
        for r in page.rects))


def scaled(page: pdf.Page, factor: float) -> pdf.Page:
    """``page`` printed ``factor`` times the size: every word and rect with it."""
    def word(w: pdf.Word) -> pdf.Word:
        return dataclasses.replace(w, x0=w.x0 * factor, y0=w.y0 * factor,
                                   x1=w.x1 * factor, y1=w.y1 * factor,
                                   size=w.size * factor,
                                   baseline=w.baseline * factor)

    def rect(r: pdf.Rect) -> pdf.Rect:
        return dataclasses.replace(r, x0=r.x0 * factor, y0=r.y0 * factor,
                                   x1=r.x1 * factor, y1=r.y1 * factor)
    return dataclasses.replace(page, width=page.width * factor,
                               height=page.height * factor,
                               words=tuple(word(w) for w in page.words),
                               rects=tuple(rect(r) for r in page.rects))


class TestACheckboxRefusalSaysWhichHalfOfTheBoxIsMissing(unittest.TestCase):
    """A box is a square and the label beside it, and a refusal names the half
    that is not there.

    It used to name the square whichever it was. A card whose seven tick-box labels
    are worded as another template words them -- ``Walkover`` for ``Forfeit``,
    ``Match Won`` for ``Match Winner`` -- draws all 30 of its boxes, and was refused
    with "the commonest hairline width on the page is 8.07pt, and no pair of edges
    that far apart forms a square", while the doctor printed "30 outline(s)" above
    it. Measured by /tmp/t4/scratch/i416c/probe.py and synthetic.py: the same
    sentence came out for a card whose boxes had been moved 4pt off their labels'
    line, which is the labels' half as well. Neither said which labels were looked
    for, and that is the one thing whoever has to fix it needs.

    Every "no" in it is this reader's, because the first cut of the fix said four
    more false things of the same kind, measured by
    /tmp/t4/scratch/i416c/fixup/probe.py: "this page prints none of them" of a card
    that prints ``Forfeit:``; "no box sits within 16.1pt of any of them on its line"
    of boxes 2.67pt from their labels and 1.78pt below them, which is the test they
    passed and not the 1.5pt one they failed; "paints no hairline" of a page whose
    box edges are all there, stroked rather than filled; and, of a card with
    every box drawn and 6 of them unnamed, "a tick on a missing box".

    Rejected: naming the vocabulary and no count. "None of these labels is
    printed" says what is absent and not what is present, and it is the count of
    squares found that tells a reader there is nothing wrong with the drawing.
    Rejected too: counting the squares with the doctor's census, which is looser by
    design and counts 30 on the stroked page, where this reader reads none.
    """

    SEVEN = ("Forfeit", "Retired", "Incomplete", "Match Winner", "Match Forfeited",
             "Match Cancelled", "Match Disputed")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        raw = cw.write_card(cw.CardSpec())

        def page(name: str, content: bytes) -> pdf.Page:
            (root / name).write_bytes(content)
            return pdf.extract(root / name)

        cls.genuine = page("genuine.pdf", raw)
        cls.relabelled = page("relabelled.pdf", relabelled(raw, OTHER_TEMPLATE_LABELS))
        # Only the four whole-match flags reworded, so the three per-set labels
        # are still printed -- and, once the boxes are moved, are printed beside
        # nothing.
        cls.set_labels_only = page("set-labels-only.pdf", relabelled(raw, {
            word: OTHER_TEMPLATE_LABELS[word]
            for word in ("Winner", "Forfeited", "Cancelled", "Disputed")}))
        # Every label with a colon printed after it, as a form often has.
        cls.colons = page("colons.pdf", relabelled(
            raw, {word: word + ":" for word in OTHER_TEMPLATE_LABELS}))
        # One of the seven left as it is, and then that one with a colon on it.
        others = {word: other for word, other in OTHER_TEMPLATE_LABELS.items()
                  if word != "Forfeit"}
        cls.forfeit_only = page("forfeit-only.pdf", relabelled(raw, others))
        cls.forfeit_colon = page("forfeit-colon.pdf", relabelled(
            raw, {"Forfeit": "Forfeit:", **others}))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception

    def box_warnings(self, page: pdf.Page) -> list[str]:
        return [w for w in scorecard.read_page(page).warnings if "checkbox" in w]

    # --- the labels' half ------------------------------------------------------

    def test_boxes_under_other_words_are_not_called_missing_squares(self):
        refused = self.refusal(self.relabelled)
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertNotIn("no pair of edges", refused.detail)
        self.assertNotIn("no checkboxes could be located", refused.reason)
        self.assertIn("beside a label this reader knows", refused.reason)
        # The count is what says the drawing is fine: 30 found, 30 wanted.
        self.assertIn("this reader finds 30 checkbox squares of 8.07pt where 4 set "
                      "rows need 30", refused.detail)

    def test_the_count_is_of_the_boxes_drawn_and_not_the_boxes_wanted(self):
        # Every other card here draws exactly the 30 it needs, which a sentence
        # printing the number wanted twice would pass.
        refused = self.refusal(fewer_boxes(self.relabelled))
        self.assertIn("this reader finds 29 checkbox squares of 8.07pt where 4 set "
                      "rows need 30", refused.detail)

    def test_one_square_is_still_a_square_and_the_labels_are_still_the_trouble(self):
        # A threshold -- too few squares to be a card's, so call them missing --
        # would give this page the square sentence, and its square is drawn.
        refused = self.refusal(fewer_boxes(self.relabelled, 29))
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertIn("this reader finds 1 checkbox square of 8.07pt where 4 set "
                      "rows need 30", refused.detail)

    def test_the_refusal_names_every_label_it_looked_for(self):
        refused = self.refusal(self.relabelled)
        for label in self.SEVEN:
            with self.subTest(label=label):
                self.assertRegex(refused.detail, rf"\b{label}\b")
        self.assertIn("as words of their own in any capitalisation", refused.detail)
        self.assertIn("no word on this page is read as one of them", refused.detail)
        # Walkover, Won and the rest spell none of the seven.
        self.assertNotIn("nearest", refused.detail)

    def test_a_label_with_something_printed_on_it_is_quoted_as_it_is_printed(self):
        # "Prints none of them" was false here: the page prints every one of the
        # words, and the doctor's header fields said so above the verdict. It
        # does not print them as words of their own, which is the reader's test.
        refused = self.refusal(self.colons)
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertIn("no word on this page is read as one of them, and the nearest "
                      "it prints are 'Forfeit:', 'Retired:', 'Incomplete:', "
                      "'Winner:', 'Forfeited:', 'Cancelled:' and 'Disputed:'",
                      refused.detail)
        self.assertNotIn("this page prints", refused.detail)

    def test_labels_printed_off_their_boxes_line_are_named_as_printed(self):
        # Three of the seven are printed and none has a box beside it. Saying
        # "prints none of them" here would be the same kind of false sentence
        # as the one this class was written against. 2pt, because the boxes are
        # then 2.67pt from their labels: well inside the reach, so it is the
        # tolerance on the line that fails, and that is the one to name.
        refused = self.refusal(boxes_moved(self.set_labels_only, 2.0))
        self.assertIn("this page prints Forfeit, Retired and Incomplete, but no "
                      "square is within 16.1pt of one of them with its centre within "
                      "1.5pt of the label's, up or down", refused.detail)
        self.assertNotIn("no pair of edges", refused.detail)

    def test_a_single_label_is_spoken_of_as_one(self):
        # The sentences for several would say "within 16.1pt of one of them" and
        # "the nearest it prints are" of a single word.
        refused = self.refusal(boxes_moved(self.forfeit_only, 2.0))
        self.assertIn("this page prints Forfeit, but no square is within 16.1pt of "
                      "it with its centre within 1.5pt of the label's",
                      refused.detail)
        refused = self.refusal(self.forfeit_colon)
        self.assertIn("no word on this page is read as one of them, and the nearest "
                      "it prints is 'Forfeit:'", refused.detail)

    def test_the_reach_is_measured_off_the_page_and_the_line_is_not(self):
        # The reach is two box sides and the tolerance a fixed 1.5pt, so a card
        # printed larger has a longer reach and the same tolerance. The premise
        # first: the larger card, unmoved, reads without a word about its boxes.
        self.assertEqual(self.box_warnings(scaled(self.genuine, 1.25)), [])
        refused = self.refusal(scaled(boxes_moved(self.genuine, 2.0), 1.25))
        self.assertIn("this reader finds 30 checkbox squares of 10.09pt",
                      refused.detail)
        self.assertIn("but no square is within 20.2pt of one of them with its "
                      "centre within 1.5pt of the label's", refused.detail)

    # --- the squares' half -----------------------------------------------------

    def test_edges_that_pair_into_no_square_keep_the_geometry_reason(self):
        refused = self.refusal(box_edges_unpaired(self.genuine))
        self.assertIsInstance(refused, scorecard.UnlocatedCheckboxes)
        self.assertIn("no checkboxes could be located", refused.reason)
        self.assertIn("the commonest hairline width on the page is 8.07pt, and no "
                      "pair of edges that far apart forms a square", refused.detail)
        self.assertNotIn("label", refused.reason + refused.detail)

    def test_a_page_with_no_box_edge_at_all_says_that_instead(self):
        refused = self.refusal(box_edges_removed(self.genuine))
        self.assertIsInstance(refused, scorecard.UnlocatedCheckboxes)
        self.assertIn("no checkboxes could be located", refused.reason)
        self.assertIn("the page paints no hairline that could be the top edge of a "
                      "box", refused.detail)
        self.assertNotIn("outlined", refused.detail)

    def test_box_edges_stroked_rather_than_filled_are_counted_as_what_they_are(self):
        # "Paints no hairline" was false here: all 60 top and bottom edges are
        # on the page, in the shape of one, outlined instead of filled.
        refused = self.refusal(box_parts_outlined(self.genuine))
        self.assertIsInstance(refused, scorecard.UnlocatedCheckboxes)
        self.assertIn("the page paints no filled hairline that could be the top "
                      "edge of a box, only 60 outlined in that shape, and a box is "
                      "read only from filled ones", refused.detail)
        self.assertNotIn("paints no hairline", refused.detail)

    # --- the counts, where the card is still read --------------------------------

    def test_the_readers_own_summary_counts_the_squares_it_could_not_name(self):
        # The doctor prints this summary above its verdict, so it is the third
        # place the page is described and has to agree with the other two.
        self.assertIn("checkboxes: 30 squares found, 0 named by a label, 30 "
                      "expected for 4 rows", scorecard.describe(self.relabelled))
        self.assertIn("checkboxes: 30 squares found, 30 named by a label, 30 "
                      "expected for 4 rows", scorecard.describe(self.genuine))
        self.assertIn("checkboxes: 1 square found, 0 named by a label",
                      scorecard.describe(fewer_boxes(self.relabelled, 29)))

    def test_the_summary_of_a_partly_named_card_gives_both_counts(self):
        # The only card on which the two counts are different and neither is 0 or
        # the number wanted, so the only one a summary that prints one count
        # twice cannot pass.
        self.assertIn("checkboxes: 30 squares found, 24 named by a label, 30 "
                      "expected for 4 rows", scorecard.describe(self.set_labels_only))

    def test_a_partly_named_card_is_refused_with_both_counts_and_no_box_called_missing(
            self):
        # Refused, where it used to be read: the 24 per-set boxes are named, and
        # the 6 whole-match ones are not. Every one of the 30 is drawn, so
        # nothing in the refusal may call a box missing.
        refused = self.refusal(self.set_labels_only)
        self.assertIn("this reader finds 30 checkbox squares of 8.07pt where 4 set "
                      "rows need 30", refused.detail)
        self.assertNotIn("missing", refused.reason + refused.detail)
        # And one that is missing a square, where the squares found are not the
        # number wanted either.
        self.assertIn("this reader finds 29 checkbox squares of 8.07pt where 4 set "
                      "rows need 30", self.refusal(fewer_boxes(self.genuine)).detail)


def box_added(page: pdf.Page, label: str, down: float, *,
              side: str = "home") -> pdf.Page:
    """``page`` with its first ``label`` on ``side`` and the box beside it drawn
    again, ``down`` points lower.

    One box more than the card needs: in no row of the set table when drawn off
    it, and a second box in the row below when drawn a few points down. With a
    box taken off somewhere else as well, the page names as many boxes as the
    card needs, and not the ones it needs. The away side's first is the last on
    the topmost line that prints the label. A label of two words, ``Match
    Winner``, is found by its last and drawn again whole, the square being the
    nearest on its line to that word.
    """
    *before, last = label.split()
    word = min((w for w in page.words if w.text == last),
               key=lambda w: (w.y0, w.x0 if side == "home" else -w.x0))
    words = [min((w for w in page.words if w.text == text
                  and abs(w.baseline - word.baseline) < 0.1 and w.x1 <= word.x0),
                 key=lambda w: word.x0 - w.x1) for text in before] + [word]
    top = min((r for r in page.rects if _box_edge(r)
               and abs(r.y0 - (word.baseline - cw.BOX_LABEL_RISE)) < 0.1),
              key=lambda r: min(abs(r.x0 - word.x1), abs(word.x0 - r.x1)))
    parts = [r for r in page.rects if _box_part(r)
             and top.x0 - 0.1 <= r.x0 <= top.x0 + cw.BOX_SIDE
             and top.y0 - 0.1 <= r.y0 <= top.y0 + cw.BOX_SIDE]
    again = tuple(dataclasses.replace(w, y0=w.y0 + down, y1=w.y1 + down,
                                      baseline=w.baseline + down) for w in words)
    return dataclasses.replace(
        page, words=page.words + again,
        rects=page.rects + tuple(dataclasses.replace(r, y0=r.y0 + down,
                                                     y1=r.y1 + down)
                                 for r in parts))


def first_box_moved(page: pdf.Page, down: float) -> pdf.Page:
    """``page`` with its topmost box alone, edges and any tick together, moved
    ``down`` points.

    The box :func:`fewer_boxes` would take, still drawn, and off the line its label
    is printed on while every other box is on it.
    """
    top = min((r for r in page.rects if _box_edge(r)), key=lambda r: (r.y0, r.x0))

    def moved(rect: pdf.Rect) -> pdf.Rect:
        if not (_box_part(rect)
                and top.x0 - 0.1 <= rect.x0 <= top.x0 + cw.BOX_SIDE
                and top.y0 - 0.1 <= rect.y0 <= top.y0 + cw.BOX_SIDE):
            return rect
        return dataclasses.replace(rect, y0=rect.y0 + down, y1=rect.y1 + down)
    return dataclasses.replace(page, rects=tuple(moved(r) for r in page.rects))


def word_lost(page: pdf.Page, text: str) -> pdf.Page:
    """``page`` without the topmost word printed as ``text``, and with every box.

    What a card damaged in the file can be: one of its labels gone and the square
    beside it still drawn.
    """
    word = min((w for w in page.words if w.text == text),
               key=lambda w: (w.y0, w.x0))
    return dataclasses.replace(
        page, words=tuple(w for w in page.words if w is not word))


def drawn_again(page: pdf.Page, label: str, down: float, *, side: str = "home",
                labelled: bool = True, ticked: bool = False) -> pdf.Page:
    """:func:`box_added`'s page, with a tick in the square it draws again where
    ``ticked`` asks, and without the label beside it where ``labelled`` is false,
    so that the square is one no label names.

    A square drawn again from a ticked box is ticked already, as the home Match
    Winner box is on the card writer's card.
    """
    again = box_added(page, label, down, side=side)
    if not labelled:
        again = dataclasses.replace(again, words=page.words)
    if ticked:
        top = min((r for r in again.rects[len(page.rects):] if _box_edge(r)),
                  key=lambda r: r.y0)
        again = dataclasses.replace(again, rects=again.rects + (pdf.Rect(
            x0=top.x0, y0=top.y0, x1=top.x0 + cw.BOX_SIDE,
            y1=top.y0 + cw.BOX_SIDE, filled=True),))
    return again


def written_with_a_box_more(path: Path, spec: cw.CardSpec, *,
                            ticked: bool) -> Path:
    """``spec``'s card written to ``path`` with row 1's home Forfeit label and box
    painted again 600pt lower, off the set table, as :func:`drawn_again` draws
    them on a page already read: a file, for the build and the doctor to read.
    """
    paint = cw._paint_foot

    def foot(art, spec: cw.CardSpec, layout) -> None:
        paint(art, spec, layout)
        art.labelled_box("Forfeit", label_x=cw.HOME_STATUS_LABEL_X[0],
                         box_x=cw.HOME_STATUS_BOX_X[0],
                         baseline=cw.TABLE_TOP + cw.ROW_STATUS_LABEL_DY + 600.0,
                         ticked=ticked, size=cw.TEXT_SIZE_SMALL)

    with mock.patch.object(cw, "_paint_foot", foot):
        return cw.write_card_file(path, spec)


def summary_warnings(text: str) -> list[str]:
    """The warnings the reader's own summary ``text`` lists, as it lists them."""
    return [line[2:] for line in text.partition("\nwarnings: ")[2].splitlines()[1:]]


class TestACardShortOfABoxItNeedsIsRefusedRatherThanReadAsUnticked(
        unittest.TestCase):
    """A box the card needs and this reader cannot name is refused, not read as
    unticked.

    It was read as unticked, and the card scored. Measured by
    /tmp/t4/scratch/y434/before/partial.py on cards the card writer wrote: boxes
    moved 1.3pt off their labels' line are all named, and from 1.4pt to 1.7pt 24
    of the 30 are, the six lost being every whole-match box. At 1.8pt the card was
    refused. /tmp/t4/scratch/y434/m1/builds.py built them: a card ticking
    ``Match Forfeited`` against the away side over rows of 6-2, 6-3, 2-6 and 6-1,
    written 1.5pt low, read ``played`` where the same card as written reads
    ``forfeit``, at exit 0 and ``0 error(s), 1 warning(s)``, the one being F8. The
    three notes the reader wrote about it reached the dashboard and not the
    report. A cancelled card lost its cancellation the same way and read as a
    match played, and a card with the per-set labels reworded named 6 of 30 and
    read a retired fourth row as a finished 4-2.

    The boxes are asked for place by place, a place being a label, a set row and
    a side, because a card can name as many boxes as it needs under every label
    and not the ones it needs. Measured by /tmp/t4/scratch/y434/fix/probe.py: with
    row 1's home Forfeit square taken off and an Incomplete box drawn again off the
    table, a count label by label found every label full, and the card was read
    with no word about its boxes, a Forfeit ticked in row 1 read as a set played,
    a Retired as a forfeit and an Incomplete as a retirement. That was the pairing
    as well: each label in turn took the nearest square it could still have, so
    the Forfeit label whose square was gone took the Retired one, and so on along
    the row. The nearest label and square are paired first now, and the refusal
    names the box that is gone, row 1's home Forfeit, where it named an Incomplete
    one. Still no label takes a square beyond its reach: measured by
    /tmp/t4/scratch/y434/fix3/reach.py, with that row's Incomplete label gone as
    well, the Forfeit label's nearest free square is the Incomplete one, 47.1pt
    off, and a pairing with no reach named it Forfeit.

    The kind says which part of the page is short. A card on which every square
    found is named, or which prints a label fewer times than it has boxes for,
    or a label of one box nowhere and in no other spelling beside a square,
    lacks part of itself, as a download that stopped early does; any other is the
    labels' kind, a square drawn and no label this reader knows beside it. The
    doctor gives its cause by kind, and it gave both corpus files that lost a box
    the cause for another template.

    Rejected: scoring the card and saying the loss in the report. A note that the
    figures may be wrong is an error by validate's own rule, so it would have
    failed the build either way, and the page would still have shown the status
    an unseen tick implied. Rejected too: one count of all the boxes together, or
    one for each label, which the added box passes; and sparing the two ``Match
    Winner`` boxes, which decide no status, because they are one of the card's
    three cross-checks and every layout measured here that lost them lost the
    flags beside them too.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        raw = cw.write_card(cw.CardSpec())

        def page(name: str, content: bytes) -> pdf.Page:
            (root / name).write_bytes(content)
            return pdf.extract(root / name)

        cls.genuine = page("genuine.pdf", raw)
        cls.per_set_reworded = page("per-set-reworded.pdf", relabelled(raw, {
            word: OTHER_TEMPLATE_LABELS[word]
            for word in ("Forfeit", "Retired", "Incomplete")}))
        cls.flags_with_colons = page("flags-with-colons.pdf", relabelled(raw, {
            word: word + ":"
            for word in ("Winner", "Forfeited", "Cancelled", "Disputed")}))
        cls.disputed_with_colon = page("disputed-with-colon.pdf", relabelled(
            raw, {"Disputed": "Disputed:"}))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception

    def test_boxes_moved_far_enough_to_lose_the_whole_match_flags_are_refused(self):
        refused = self.refusal(boxes_moved(self.genuine, 1.5))
        # The labels' kind, because a square is drawn that no label names, and
        # the doctor gives the cause from the kind.
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertEqual(refused.reason,
                         "this reader could not name 6 of the 30 checkboxes this "
                         "card needs, so a tick in any of them would be invisible")

    def test_the_refusal_names_each_box_it_could_not_name_and_the_test_it_failed(
            self):
        refused = self.refusal(boxes_moved(self.genuine, 1.5))
        # Each clause as it is said, and not the whole detail, which would fail
        # for a clause added about something else.
        for clause in (
                "this reader finds 30 checkbox squares of 8.07pt where 4 set rows "
                "need 30, and names a box only by the label printed beside it; ",
                "; the 6 it could not name are both Match Winner boxes, both Match "
                "Forfeited boxes, the Match Cancelled box and the Match Disputed "
                "box; ",
                "; this page prints Match Winner, Match Forfeited, Match Cancelled "
                "and Match Disputed, and a square is named by one of them only "
                "within 16.1pt of the label and with its centre within 1.5pt of "
                "the label's, up or down"):
            with self.subTest(clause=clause[:40]):
                self.assertIn(clause, refused.detail)

    def test_every_box_of_a_label_is_counted_and_a_label_never_read_is_said_to_be(
            self):
        refused = self.refusal(self.per_set_reworded)
        # A template's own words, so the labels' kind: every square is drawn.
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertEqual(refused.reason,
                         "this reader could not name 24 of the 30 checkboxes this "
                         "card needs, so a tick in any of them would be invisible")
        self.assertIn("the 24 it could not name are all 8 Forfeit boxes, all 8 "
                      "Retired boxes and all 8 Incomplete boxes; no word on this "
                      "page is read as Forfeit, Retired or Incomplete, which it "
                      "looks for as words of their own in any capitalisation",
                      refused.detail)
        # Walkover, Withdrawn and Unfinished spell none of the three.
        self.assertNotIn("nearest", refused.detail)
        self.assertNotIn("this page prints", refused.detail)

    def test_a_label_that_names_some_of_its_boxes_is_not_said_to_have_no_square(
            self):
        # "No square is within 16.1pt of it", the sentence for a card on which no
        # box is named, is false here: seven of the eight Forfeit labels have
        # one. The eighth's square is drawn, 2pt below its label's line. A set's
        # label names only a square on its left, and that is said as well.
        refused = self.refusal(first_box_moved(self.genuine, 2.0))
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertTrue(refused.detail.endswith(
            "; the one it could not name is row 1's home Forfeit box; this page "
            "prints Forfeit, and a square is named by it only within 16.1pt of the "
            "label and with its centre within 1.5pt of the label's, up or down, "
            "and on the label's left"),
            refused.detail)
        self.assertNotIn("no square is within", refused.detail)

    def test_the_boxes_it_could_not_name_are_named_by_their_row_and_side(self):
        # By the places they are counted by, and not as some of a label's eight:
        # "1 of the 8 Forfeit boxes" is no box a club can find on the card.
        refused = self.refusal(fewer_boxes(self.genuine, 7))
        self.assertIn("the 7 it could not name are row 1's home Forfeit, Retired "
                      "and Incomplete boxes, row 1's away Forfeit, Retired and "
                      "Incomplete boxes and row 2's home Forfeit box",
                      refused.detail)

    def test_a_card_short_of_a_square_is_refused_as_a_page_short_of_part_of_itself(
            self):
        # Every square this reader finds is named, so the box it could not name
        # is one whose square it cannot find, and the two tests a square passes
        # beside its label are no help to anyone. The labels' kind would send the
        # club after another template, as the doctor sent it for both corpus
        # files that lost a box.
        refused = self.refusal(fewer_boxes(self.genuine))
        self.assertIsInstance(refused, scorecard.MissingCheckboxes)
        self.assertTrue(refused.detail.startswith(
            "this reader finds 29 checkbox squares of 8.07pt where 4 set rows need "
            "30, and every square it finds is named by the label printed beside "
            "it, so a box it could not name is one whose square it cannot find; "),
            refused.detail)
        self.assertNotIn("a square is named by", refused.detail)

    def test_a_label_printed_fewer_times_than_its_boxes_is_said_to_be_a_word_short(
            self):
        # A word lost, as one damaged byte lost one in the corpus, and not a word
        # changed: a template that words a label differently words it so
        # everywhere. The square beside it is drawn and named by nothing.
        for word, which, printed in (
                ("Retired", "row 1's home Retired box",
                 "Retired 7 times for its 8 boxes"),
                ("Winner", "the home Match Winner box",
                 "Match Winner once for its 2 boxes")):
            with self.subTest(word=word):
                refused = self.refusal(word_lost(self.genuine, word))
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                self.assertTrue(refused.detail.endswith(
                    f"; the one it could not name is {which}; this page prints "
                    f"{printed}"), refused.detail)

    def test_one_box_it_could_not_name_is_spoken_of_as_one(self):
        refused = self.refusal(fewer_boxes(self.genuine))
        self.assertEqual(refused.reason,
                         "this reader could not name 1 of the 30 checkboxes this "
                         "card needs, so a tick in it would be invisible")
        self.assertTrue(refused.detail.endswith(
            "; the one it could not name is row 1's home Forfeit box"),
            refused.detail)
        refused = self.refusal(self.disputed_with_colon)
        self.assertIn("the one it could not name is the Match Disputed box; no word "
                      "on this page is read as Match Disputed, which it looks for as "
                      "words of their own in any capitalisation, and the nearest it "
                      "prints is 'Disputed:'", refused.detail)

    def test_only_the_words_of_the_labels_it_could_not_read_are_quoted(self):
        # The per-set labels are read, so quoting them as "the nearest it prints"
        # would say they were not.
        refused = self.refusal(self.flags_with_colons)
        self.assertIn("no word on this page is read as Match Winner, Match "
                      "Forfeited, Match Cancelled or Match Disputed, which it looks "
                      "for as words of their own in any capitalisation, and the "
                      "nearest it prints are 'Winner:', 'Forfeited:', 'Cancelled:' "
                      "and 'Disputed:'", refused.detail)
        self.assertNotIn("'Forfeit'", refused.detail)

    def test_a_card_naming_as_many_boxes_as_it_needs_but_not_the_ones_is_refused(
            self):
        # Row 1's home Forfeit square taken off, and a box drawn again off the
        # table: counted all together, 30 of 30. With a Forfeit box drawn again,
        # every label names eight, and with an Incomplete one the labels of row
        # 1 taken in turn named the Retired square Forfeit and the Incomplete
        # square Retired, and the card was read.
        for label in ("Forfeit", "Incomplete"):
            with self.subTest(label=label):
                page = fewer_boxes(box_added(self.genuine, label, 600.0))
                # The premise, and the summary saying what the refusal says.
                self.assertIn("checkboxes: 30 squares found, 30 named by a label, "
                              "30 expected for 4 rows and 1 of those not named",
                              scorecard.describe(page))
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                self.assertTrue(refused.detail.endswith(
                    "; the one it could not name is row 1's home Forfeit box"),
                    refused.detail)

    def test_a_label_whose_square_is_gone_takes_no_square_beyond_its_reach(self):
        # Row 1's home Forfeit square gone, and the Incomplete label beside that
        # row's Incomplete square. The Forfeit label's nearest free square is
        # then the Incomplete one, 47.1pt off: taken, it named that box Forfeit,
        # and the refusal counted one box it could not name, the Incomplete, and
        # said every square it found was named. How the two are worded together
        # is the test of their row and side above.
        refused = self.refusal(fewer_boxes(word_lost(self.genuine, "Incomplete")))
        self.assertIn("could not name 2 of the 30", refused.reason)
        self.assertIn("names a box only by the label printed beside it; the 2 it "
                      "could not name are row 1's home Forfeit", refused.detail)

    def test_a_card_naming_more_boxes_than_it_needs_is_still_read(self):
        # The extra box is in no row, and every box the card needs is named, so
        # nothing on it is unseen but a tick in the extra box, which refuses it.
        card = scorecard.read_page(box_added(self.genuine, "Forfeit", 600.0))
        self.assertEqual([w for w in card.warnings if "checkbox" in w], [
            "labels name 31 of the 31 checkbox squares found, where 4 set rows "
            "need 30; the one more is a home Forfeit box in no set row, which no "
            "row reads"])

    def test_a_square_no_label_names_is_counted_among_the_squares_found(self):
        # The card above names every square it finds, so it cannot tell the two
        # counts apart: with the warning giving the boxes named as the squares
        # found, it still read 31 of the 31, and test_hostile_input, test_doctor
        # and test_parsing passed (/tmp/t4/scratch/f6/before/count-modules.log).
        # A Retired box drawn again as well, and the label drawn beside it, the
        # last word box_added() adds, taken off: a square nothing names.
        page = box_added(box_added(self.genuine, "Forfeit", 600.0), "Retired", 600.0)
        page = dataclasses.replace(page, words=page.words[:-1])
        # The premise, as the reader's own summary gives it.
        self.assertIn("checkboxes: 32 squares found, 31 named by a label",
                      scorecard.describe(page))
        card = scorecard.read_page(page)
        # And the square is said where it is, among the squares more than the
        # card needs, as a box more is (4.170).
        self.assertEqual([w for w in card.warnings if "checkbox" in w], [
            "labels name 31 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are a home Forfeit box in no set row, which no "
            "row reads and a square on the home side in no set row, which no "
            "label names"])


def written_without_labels(path: Path, spec: cw.CardSpec, *labels: str) -> Path:
    """``spec``'s card written to ``path`` with every one of ``labels`` printed
    nowhere, and each square beside one still drawn, ticked where ``spec`` ticks
    it: a card whose file lost those words and nothing else.

    Not :func:`word_lost`, which takes one word off a page already read and
    leaves ``Match`` where ``Match Disputed`` was: a file, for the doctor to
    read, with both words gone.
    """
    paint = cw._Painter.labelled_box

    def labelled_box(art, label: str, *, box_x: float, baseline: float,
                     ticked: bool = False, **rest) -> None:
        if label in labels:
            art.checkbox(box_x, baseline - cw.BOX_LABEL_RISE, ticked=ticked)
        else:
            paint(art, label, box_x=box_x, baseline=baseline, ticked=ticked,
                  **rest)

    with mock.patch.object(cw._Painter, "labelled_box", labelled_box):
        return cw.write_card_file(path, spec)


class TestALabelOfOneBoxLostWholeIsRefusedAsACopyShortOfAWord(unittest.TestCase):
    """A card that lost a label printed once for its one box is refused as a
    copy short of part of itself, and the doctor sends the club for a new copy.

    It was refused as another template's. Measured by /tmp/t7/scratch/B2/probe.py
    on the card writer's card with ``Match Disputed`` printed nowhere and its
    square drawn: ``UnlabelledCheckboxes``, "the one it could not name is the
    Match Disputed box; no word on this page is read as Match Disputed", and the
    doctor, which gives its cause from the kind alone, said "the card was printed
    from another template". So it was with ``Match Cancelled`` gone, with both
    gone, and with only the word ``Disputed`` gone. A copy damaged on the way can
    lose a word and keep the square beside it, and a template that words that one
    label differently and every other label as this one does looks the same.

    So a page on which every label it could not name is printed too few times,
    or is one of the two labels printed once for one box, printed nowhere and not
    printed beside a square in another spelling, is refused as a page short of
    part of itself, and the doctor gives the cause for that: download it again,
    and if the new copy is refused the same way, send the verdict on. The second
    half is the answer for the other template, whose new copy is refused the same
    way. A label printed beside its square with something else on it,
    ``Disputed:``, is a word changed and not a word lost; a label of two boxes
    read nowhere is two words lost in two places; and a square drawn off its
    label's line is not what a damaged copy leaves. Each is still the labels'
    kind.

    Beside a square, because the first rule asked for any word on the page that
    spells the label, and the card that lost ``Match`` and kept ``Disputed``, and
    the card that lost ``Match Cancelled`` with its box ticked and comments
    saying "cancelled at 8pm", were both still another template's. Measured by
    /tmp/t7/scratch/B2/fix/siblings.py. ``Disputed`` alone is the word a copy
    that lost ``Match`` kept, as ``Match`` alone is the one a copy that lost
    ``Disputed`` kept, and a word in the comments is on the line of no square.

    Rejected: keeping the labels' kind, whose cause tells a club its card is
    another association's, a translation or retyped by hand, where the likelier
    cause is a copy that lost a word. Rejected too: telling the two apart by what
    is printed beside the square, ``Match`` alone or ``Match Contested``, since a
    label lost whole leaves nothing there, and a guess at what a word beside a
    square was meant to say is a second reading of the labels.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.genuine = pdf.extract(cw.write_card_file(cls.root / "genuine.pdf",
                                                     cw.CardSpec()))
        cls.no_disputed = written_without_labels(
            cls.root / "no-disputed.pdf", cw.CardSpec(), "Match Disputed")
        cls.disputed_with_colon = cls.root / "disputed-with-colon.pdf"
        cls.disputed_with_colon.write_bytes(relabelled(
            (cls.root / "genuine.pdf").read_bytes(), {"Disputed": "Disputed:"}))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception

    def without(self, *labels: str, spec: cw.CardSpec | None = None) -> pdf.Page:
        name = "-".join(label.split()[-1] for label in labels) + ".pdf"
        return pdf.extract(written_without_labels(
            self.root / name, spec or cw.CardSpec(), *labels))

    def doctor(self, path: Path) -> tuple[str, str]:
        """The doctor's summary and verdict on ``path``, each with its lines
        joined, so that a sentence is found wherever the doctor breaks it."""
        done = subprocess.run((sys.executable, str(DOCTOR), str(path)),
                              capture_output=True, text=True, timeout=TIMEOUT,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertNotIn(TRACEBACK, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 1, done.stdout[-2000:])
        summary, verdict = done.stdout.split("\n    verdict\n")
        return " ".join(summary.split()), " ".join(verdict.split())

    def test_a_card_that_lost_a_label_printed_once_is_refused_as_a_copy_short_of_it(
            self):
        for labels, which in (
                (("Match Disputed",), "the one it could not name is the Match "
                                      "Disputed box; no word on this page is read "
                                      "as Match Disputed"),
                (("Match Cancelled",), "the one it could not name is the Match "
                                       "Cancelled box; no word on this page is "
                                       "read as Match Cancelled"),
                (("Match Cancelled", "Match Disputed"),
                 "the 2 it could not name are the Match Cancelled box and the "
                 "Match Disputed box; no word on this page is read as Match "
                 "Cancelled or Match Disputed")):
            with self.subTest(lost=labels):
                page = self.without(*labels)
                # The premise: the square is drawn, and named by nothing.
                self.assertIn("checkboxes: 30 squares found, "
                              f"{30 - len(labels)} named by a label",
                              scorecard.describe(page))
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                # The words are the ones they were: the kind is what changed.
                self.assertTrue(refused.detail.endswith(
                    f"; {which}, which it looks for as words of their own in any "
                    "capitalisation"), refused.detail)

    def test_a_ticked_box_whose_label_is_lost_is_refused_so_too(self):
        # A cancellation is what that box is there for, and the tick is drawn.
        refused = self.refusal(self.without("Match Cancelled",
                                            spec=cw.CardSpec(cancelled=True)))
        self.assertIsInstance(refused, scorecard.MissingCheckboxes)

    def test_a_card_that_lost_only_the_word_after_match_is_refused_so_too(self):
        # "Match" is still printed beside the square, and is no label.
        refused = self.refusal(word_lost(self.genuine, "Disputed"))
        self.assertIsInstance(refused, scorecard.MissingCheckboxes)
        self.assertIn("the one it could not name is the Match Disputed box",
                      refused.detail)

    def test_a_card_that_lost_only_the_word_match_before_it_is_refused_so_too(
            self):
        # The flag's own word is still printed beside the square, and the
        # refusal still says so.
        for word in ("Cancelled", "Disputed"):
            with self.subTest(kept=word):
                kept = next(w for w in self.genuine.words if w.text == word)
                page = dataclasses.replace(self.genuine, words=tuple(
                    w for w in self.genuine.words
                    if not (w.text == "Match" and abs(w.cy - kept.cy) < 1)))
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                self.assertTrue(refused.detail.endswith(
                    f"; the one it could not name is the Match {word} box; no "
                    f"word on this page is read as Match {word}, which it looks "
                    "for as words of their own in any capitalisation, and the "
                    f"nearest it prints is '{word}'"), refused.detail)

    def test_the_word_in_the_comments_is_not_a_label_printed_beside_the_square(
            self):
        # The comments say why the box is ticked, in its label's word alone and
        # with a full stop on it; neither is on the line of a square.
        for comments, word in (
                ("Rain - cancelled at 8pm, to be replayed", "cancelled"),
                ("Rain stopped play and the match was cancelled.", "cancelled.")):
            with self.subTest(comments=comments):
                page = self.without("Match Cancelled", spec=cw.CardSpec(
                    cancelled=True, comments=comments))
                self.assertIn(word, [w.text for w in page.words])
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)

    def test_a_label_printed_with_something_else_on_it_is_still_another_templates(
            self):
        refused = self.refusal(pdf.extract(self.disputed_with_colon))
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertTrue(refused.detail.endswith(", and the nearest it prints is "
                                                "'Disputed:'"), refused.detail)

    def test_a_label_of_two_boxes_read_nowhere_is_still_another_templates(self):
        # Both Match Winner labels in another template's word, alone and with
        # the Match Disputed label lost as well: neither is one word lost.
        winner_won = relabelled((self.root / "genuine.pdf").read_bytes(),
                                {"Winner": "Won"})
        both = relabelled(written_without_labels(
            self.root / "both.pdf", cw.CardSpec(), "Match Disputed").read_bytes(),
            {"Winner": "Won"})
        for name, raw, which in (
                ("winner reworded", winner_won, "both Match Winner boxes"),
                ("and disputed lost", both,
                 "both Match Winner boxes and the Match Disputed box")):
            with self.subTest(name):
                path = self.root / "reworded.pdf"
                path.write_bytes(raw)
                refused = self.refusal(pdf.extract(path))
                self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
                self.assertIn(f"could not name are {which}; no word on this page "
                              "is read as Match Winner", refused.detail)

    def test_a_label_lost_where_a_square_sits_off_its_line_is_still_the_labels(
            self):
        # Row 1's home Forfeit square 2pt below its label's line, which no
        # damaged copy does, and the Match Disputed label gone as well.
        refused = self.refusal(first_box_moved(self.without("Match Disputed"), 2.0))
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        for clause in ("; no word on this page is read as Match Disputed, ",
                       "; this page prints Forfeit, and a square is named by it "
                       "only within 16.1pt"):
            with self.subTest(clause=clause[:40]):
                self.assertIn(clause, refused.detail)

    def test_the_doctor_sends_the_club_for_a_new_copy(self):
        summary, verdict = self.doctor(self.no_disputed)
        self.assertIn("This page was refused: this reader could not name 1 of "
                      "the 30 checkboxes this card needs", verdict)
        self.assertIn("Likeliest cause: part of the card did not reach this file",
                      verdict)
        self.assertIn("Download it again from Match Centre, and if", verdict)
        self.assertNotIn("another template", verdict)
        self.assertIn("1 of those not named: the Match Disputed box", summary)

    def test_the_doctor_still_gives_a_changed_word_to_another_template(self):
        _, verdict = self.doctor(self.disputed_with_colon)
        self.assertIn("Likeliest cause: the card was printed from another "
                      "template", verdict)
        self.assertNotIn("did not reach this file", verdict)


class TestALabelWhoseSquareIsGoneDoesNotTakeTheNextLabelsSquare(unittest.TestCase):
    """A set's label whose square is gone does not take the next label's, and the
    refusal names each box by what it lost.

    It took it. Measured by /tmp/t6/scratch/m5/before.py on the card writer's
    card with row 1's home Forfeit square (x=67.9) and row 1's home Retired label
    taken off: refused ``MissingCheckboxes``, "the one it could not name is row
    1's home Retired box; this page prints Retired 7 times for its 8 boxes", after
    "every square it finds is named by the label printed beside it, so a box it
    could not name is one whose square it cannot find". The square that is gone
    is the Forfeit one. The Forfeit label had taken the Retired square, 7.26pt to
    its right, since no label nearer to it was left to, and the reader's own
    summary counted 29 squares found and 29 named.

    A set's label names only a square on its left now, which is where the
    template prints it: measured by /tmp/t6/scratch/m5/gaps.py, every set's label
    has its own square 2.67pt to 3.08pt to its left, and the only other square
    within reach is the next label's, to its right. So the refusal counts two
    boxes, row 1's home Forfeit and Retired, and says of each what it lost: the
    Retired label a word, and the Forfeit label any square on its left. That test
    is said wherever the others are, since a square that passes the two it was
    told of and not the third is no help to whoever has to move it.

    Rejected: every square to the left of its label, as filed. The away column's
    Match Winner and Match Forfeited squares are 14.17pt and 12.42pt to the right
    of their labels, and measured by /tmp/t6/scratch/m5/leftall.py that rule
    refused all 197 cards, the card writer's and the 196 of thirteen demo
    seasons, for those two boxes. A whole-match flag keeps either side: each has
    one square within reach on its line, so no pairing can give it another's.
    Rejected too: a side for each label in each column, which needs the set
    table the reader's own summary may not have, and names the same square as
    this on every card measured.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        raw = cw.write_card(cw.CardSpec())

        def page(name: str, content: bytes) -> pdf.Page:
            (root / name).write_bytes(content)
            return pdf.extract(root / name)

        cls.genuine = page("genuine.pdf", raw)
        cls.set_labels_only = page("set-labels-only.pdf", relabelled(raw, {
            word: OTHER_TEMPLATE_LABELS[word]
            for word in ("Winner", "Forfeited", "Cancelled", "Disputed")}))
        # Row 1's home Forfeit square and the Retired label beside the next one.
        cls.both_lost = word_lost(fewer_boxes(cls.genuine), "Retired")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception

    def test_the_box_whose_square_is_gone_and_the_one_whose_label_is_are_both_named(
            self):
        # And the same one label along: row 1's home Retired square gone and the
        # Incomplete label beside the next one, measured by
        # /tmp/t6/scratch/m5/fx/probe4.py. A Retired label let look either side
        # takes the Incomplete square, as the Forfeit label did the Retired one.
        for lost, label, page in (
                ("Forfeit", "Retired", self.both_lost),
                ("Retired", "Incomplete",
                 word_lost(row_one_box_lost(self.genuine, "Retired"),
                           "Incomplete"))):
            with self.subTest(square_lost=lost, label_lost=label):
                refused = self.refusal(page)
                # A word short, so the page's kind, as a card missing a label
                # always was.
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                self.assertEqual(refused.reason,
                                 "this reader could not name 2 of the 30 "
                                 "checkboxes this card needs, so a tick in any of "
                                 "them would be invisible")
                self.assertTrue(refused.detail.endswith(
                    f"; the 2 it could not name are row 1's home {lost} and "
                    f"{label} boxes; this page prints {label} 7 times for its 8 "
                    f"boxes; this page prints {lost}, and a square is named by it "
                    "only within 16.1pt of the label and with its centre within "
                    "1.5pt of the label's, up or down, and on the label's left"),
                    refused.detail)

    def test_the_square_the_forfeit_label_took_is_named_by_nothing(self):
        # "Every square it finds is named" was false: the Retired square is
        # drawn, and the summary counts it among the squares no label names.
        self.assertIn("checkboxes: 29 squares found, 28 named by a label, 30 "
                      "expected for 4 rows and 2 of those not named: row 1's "
                      "home Forfeit and Retired boxes\n",
                      scorecard.describe(self.both_lost))
        self.assertTrue(self.refusal(self.both_lost).detail.startswith(
            "this reader finds 29 checkbox squares of 8.07pt where 4 set rows need "
            "30, and names a box only by the label printed beside it; "))

    def test_a_card_naming_no_box_says_a_sets_label_looks_only_on_its_left(self):
        # The sentence for a card on which no box is named, of the three set's
        # labels alone.
        refused = self.refusal(boxes_moved(self.set_labels_only, 2.0))
        self.assertTrue(refused.detail.endswith(
            "this page prints Forfeit, Retired and Incomplete, but no square is "
            "within 16.1pt of one of them with its centre within 1.5pt of the "
            "label's, up or down, and on the label's left"), refused.detail)

    def test_the_side_is_said_of_the_sets_labels_and_not_the_whole_match_ones(
            self):
        refused = self.refusal(boxes_moved(self.genuine, 2.0))
        self.assertTrue(refused.detail.endswith(
            "and Match Disputed, but no square is within 16.1pt of one of them "
            "with its centre within 1.5pt of the label's, up or down, and on the "
            "label's left for Forfeit, Retired and Incomplete"), refused.detail)

    def test_a_whole_match_flag_is_not_said_to_look_on_one_side(self):
        # The six whole-match boxes are the ones lost at 1.5pt, and the away
        # column's Match Winner square is to the right of its label.
        refused = self.refusal(boxes_moved(self.genuine, 1.5))
        self.assertTrue(refused.detail.endswith(
            "and Match Disputed, and a square is named by one of them only within "
            "16.1pt of the label and with its centre within 1.5pt of the label's, "
            "up or down"), refused.detail)


def label_printed_again(page: pdf.Page, label: str, beside: str,
                        gap: float) -> pdf.Page:
    """``page`` with its ``label`` printed a second time on the line of its
    ``beside`` label, ``gap`` points left of that label's square.

    Both are two-word labels of the whole match, as ``Match Disputed`` and
    ``Match Cancelled`` are: a word the reader takes for a label, within reach of
    a square that is another label's.
    """
    def words_of(text: str) -> tuple[pdf.Word, pdf.Word]:
        first, last = text.split()
        end = next(w for w in page.words if w.text == last)
        start = min((w for w in page.words if w.text == first
                     and abs(w.baseline - end.baseline) < 0.1 and w.x1 <= end.x0),
                    key=lambda w: end.x0 - w.x1)
        return start, end

    start, end = words_of(beside)
    square = min((r for r in page.rects if _box_edge(r)
                  and abs(r.y0 - (end.baseline - cw.BOX_LABEL_RISE)) < 0.1),
                 key=lambda r: abs(start.x0 - r.x1))
    moved = words_of(label)
    dx = (square.x0 - gap) - moved[1].x1
    dy = end.baseline - moved[1].baseline
    return dataclasses.replace(page, words=page.words + tuple(
        dataclasses.replace(w, x0=w.x0 + dx, x1=w.x1 + dx, y0=w.y0 + dy,
                            y1=w.y1 + dy, baseline=w.baseline + dy)
        for w in moved))


class TestASquareTwoLabelsCanReachIsNamedOnceByTheNearer(unittest.TestCase):
    """A square two labels can reach is named by the nearer of them, and by it
    alone.

    These are 4.34's two rules for pairing labels with squares, and once a set's
    label looked left no test held them: measured by
    /tmp/t6/scratch/m5/pairing.py, a pairing that took each label in turn, and one
    that let a square be named twice, each passed every class it was run
    against. Each label the template prints has one square within its reach, so
    on a card as printed neither rule decides anything.

    They decide on a card that prints a word the reader takes for a label within
    reach of another label's square. Measured by /tmp/t6/scratch/m5/fx/stray2.py
    on the card writer's card with a second ``Match Disputed`` printed on the
    Match Cancelled line, 10pt left of the square whose own label is 6.33pt to
    its right: read, with every box named once, and with Match Cancelled ticked,
    cancelled and not disputed. Taking each label in turn, the stray, read first,
    took the square, and the card was refused for a Match Cancelled box whose
    square it "cannot find". Letting a square be named twice, the card was read
    with the one square as both boxes, and the cancellation read as a dispute
    too.

    Rejected: taking the two rules out, since nothing held them. The template
    places a label by its distance from its square and not by the order a page
    is read in, and a square named twice reads one tick as two. Distance is not
    the only guide: a set's label printed in no set row is paired after the
    rest, so a stray one nearer a flag's square than the flag's own label no
    longer takes that square, as the class below holds (4.173).
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.stray, cls.stray_cancelled = (
            label_printed_again(pdf.extract(cw.write_card_file(root / name, spec)),
                                "Match Disputed", "Match Cancelled", 10.0)
            for name, spec in (("genuine.pdf", cw.CardSpec()),
                               ("cancelled.pdf", cw.CardSpec(cancelled=True))))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_nearer_label_names_the_square_and_the_card_reads(self):
        # The premise: the stray is on the line and within reach.
        self.assertEqual(
            sum(1 for label, _ in scorecard._box_labels(self.stray)
                if label == "Match Disputed"), 2)
        card = scorecard.read_page(self.stray)
        self.assertEqual([w for w in card.warnings if "checkbox" in w], [])
        self.assertIn("checkboxes: 30 squares found, 30 named by a label, 30 "
                      "expected for 4 rows\n", scorecard.describe(self.stray))

    def test_a_tick_in_that_square_is_read_once_as_the_nearer_labels_box(self):
        card = scorecard.read_page(self.stray_cancelled)
        self.assertTrue(card.cancelled)
        self.assertFalse(card.disputed)


def square_beside(page: pdf.Page, word: pdf.Word) -> pdf.Rect:
    """The top edge of the square nearest ``word`` on its line, either side."""
    return min((r for r in page.rects if _box_edge(r)
                and abs(r.y0 - (word.baseline - cw.BOX_LABEL_RISE)) < 0.1),
               key=lambda r: min(abs(r.x0 - word.x1), abs(word.x0 - r.x1)))


def printed_again_at(page: pdf.Page, label: str, *, baseline: float,
                     left: float | None = None,
                     right: float | None = None) -> pdf.Page:
    """``page`` with ``label`` printed a second time on ``baseline``, starting at
    ``left`` or ending at ``right``: the words of the topmost place the page
    prints it, moved there, as a word printed where the template prints none.
    """
    *before, last = label.split()
    end = min((w for w in page.words if w.text == last), key=lambda w: (w.y0, w.x0))
    words = [min((w for w in page.words if w.text == text
                  and abs(w.baseline - end.baseline) < 0.1 and w.x1 <= end.x0),
                 key=lambda w: end.x0 - w.x1) for text in before] + [end]
    dx = left - words[0].x0 if left is not None else right - end.x1
    dy = baseline - end.baseline
    return dataclasses.replace(page, words=page.words + tuple(
        dataclasses.replace(w, x0=w.x0 + dx, x1=w.x1 + dx, y0=w.y0 + dy,
                            y1=w.y1 + dy, baseline=w.baseline + dy)
        for w in words))


def away_flag(page: pdf.Page, flag: str) -> tuple[pdf.Word, pdf.Rect]:
    """The last word of the away ``flag``'s label, and the top edge of its
    square, which the template prints right of the label."""
    word = min((w for w in page.words if w.text == flag.split()[-1]),
               key=lambda w: abs(w.x1 - cw.AWAY_FLAG_BOX_X))
    return word, square_beside(page, word)


def set_label_beside_flag(page: pdf.Page, flag: str, label: str = "Retired",
                          *, first: bool = False) -> pdf.Page:
    """``page`` with a set's ``label`` printed again 3.3pt right of the away
    ``flag``'s square, on its line, as /tmp/t6/scratch/m5/fx/stray.py printed
    it: in no set row, and nearer the square than the flag's own label.
    ``first`` puts it first among the page's words, where it is otherwise last.
    """
    word, square = away_flag(page, flag)
    page = printed_again_at(page, label, baseline=word.baseline,
                            left=square.x1 + 3.3)
    return (dataclasses.replace(page, words=page.words[-1:] + page.words[:-1])
            if first else page)


class TestASetsLabelInNoSetRowTakesNoSquareALabelWhoseBoxIsReadCanReach(
        unittest.TestCase):
    """A set's label printed in no set row names a square only after every label
    whose box a row or the match reads has named its own.

    It took the square. Measured by /tmp/t7/scratch/B3/probe.py on the card
    writer's card with a stray ``Retired`` printed 3.3pt right of the away Match
    Winner square, whose own label is 14.17pt to its left: refused
    ``MissingCheckboxes``, saying "every square it finds is named by the label
    printed beside it, so a box it could not name is one whose square it cannot
    find; the one it could not name is the away Match Winner box", and the
    reader's summary counted 30 found and 30 named, with the stray first or last
    among the page's words. So it was with a stray Forfeit or Incomplete, with
    the away side's tick in the square, and with the stray beside the away Match
    Forfeited square, 12.42pt from its own label. The nearest label and square
    are paired first, and the stray was nearer; the box it named was a Retired
    box in no set row, which no row reads.

    Now a pair whose box would be a set's in no set row, by :func:`_place`'s
    test of its square, is taken after every other pair, nearest first within
    each. A set's box drawn again off the table is still its label's box, and
    warned of as one no row reads (4.113), since no other label reaches its
    square. With no rows to ask about, as the reader's summary has none for a
    page whose table it cannot find, no label is taken to be in none of them.

    And a refusal no longer says only that a box it could not name is one whose
    square it cannot find, where that box is a whole-match flag's and a set's
    label in no set row named a square: with the away Match Winner label lost
    and the stray beside its square, the card was told the drawn square is gone.
    The square of a set's box in no set row is in no set row, where a flag's box
    is and a set's box is not, so the refusal names it as the other place to
    look, on the flag's side where the flag has one. Asked of the box it could
    not name, and not of whether its label is a word short: with the lost word
    printed again beside no square, the page prints Match Winner as often as it
    has boxes for it, and the square is still drawn. And 4.34's card, whose row
    1 Forfeit square is gone, is still told its square cannot be found, since
    the square the Forfeit label off the table names cannot be a set's box's.

    Rejected: a set's label in no set row naming no square, the default this was
    planned with. Measured on a scratch tree, 15 failures in 12 tests of six
    classes (/tmp/t7/scratch/B3/excl-classes.log): 4.34's card with row 1's home
    Forfeit square gone and a Forfeit box drawn again off the table named 29 and
    not 30, and was refused ``UnlabelledCheckboxes``, the doctor's cause for
    another template, with a reach test the square off the table passes; and
    4.113's box drawn again was warned of, and refused ticked, as a square no
    label names, beside the Forfeit label printed at it. Rejected too: 4.34's
    alternatives, each label in turn and a square named twice, which break the
    class above; and every set's label paired after every flag's, which lets a
    flag's word printed in a set row take the square of the set's label nearer
    it. Rejected too, of the refusal: never saying the square is gone where such
    a label named a square, as this was first written. It took from 4.34's card
    the one true thing it is told of its box, where the doctor's cause for the
    kind says the detail line says which part is not on the page.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.genuine, cls.away_won, cls.forfeited, cls.no_table = (
            pdf.extract(cw.write_card_file(root / name, spec))
            for name, spec in (("genuine.pdf", cw.CardSpec()),
                               ("away-won.pdf", cw.CardSpec(winner="away")),
                               ("forfeited.pdf", cw.CardSpec(forfeited_by="away")),
                               ("no-table.pdf", cw.CardSpec(drop_column_rule=3))))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def boxes(self, page: pdf.Page) -> str:
        """The reader's summary's count of the boxes."""
        return next(line for line in scorecard.describe(page).splitlines()
                    if line.startswith("checkboxes: "))

    def test_the_flags_own_label_names_the_square_and_the_card_reads(self):
        for first in (False, True):
            with self.subTest(first=first):
                page = set_label_beside_flag(self.genuine, "Match Winner",
                                             first=first)
                # The premise: the stray is read as a label, on the square's line
                # and nearer it than the flag's own label.
                word, square = away_flag(page, "Match Winner")
                stray = next(w for w in page.words if w.text == "Retired"
                             and abs(w.baseline - word.baseline) < 0.1)
                self.assertEqual(sum(1 for label, _ in scorecard._box_labels(page)
                                     if label == "Retired"), 9)
                self.assertEqual((round(stray.x0 - square.x1, 2),
                                  round(square.x0 - word.x1, 2)), (3.3, 14.17))
                card = scorecard.read_page(page)
                self.assertEqual([w for w in card.warnings if "checkbox" in w], [])
                self.assertEqual(self.boxes(page), "checkboxes: 30 squares found, "
                                 "30 named by a label, 30 expected for 4 rows")

    def test_a_tick_in_that_square_is_read_as_the_flags(self):
        card = scorecard.read_page(set_label_beside_flag(self.away_won,
                                                         "Match Winner"))
        self.assertEqual(card.winner_tick, "away")
        # And a forfeit, which the stray had refused, as a forfeit.
        card = scorecard.read_page(set_label_beside_flag(self.forfeited,
                                                         "Match Forfeited"))
        self.assertEqual(card.forfeited_by, "away")

    def test_each_of_the_sets_labels_gives_the_square_up_alike(self):
        for label in ("Forfeit", "Incomplete"):
            with self.subTest(label=label):
                page = set_label_beside_flag(self.genuine, "Match Winner", label)
                self.assertEqual(self.boxes(page), "checkboxes: 30 squares found, "
                                 "30 named by a label, 30 expected for 4 rows")

    def test_a_sets_box_drawn_again_off_the_table_is_still_named_by_its_label(self):
        # Taken last, and still taken: nothing else reaches its square, and a
        # tick in it is one no row reads, which 4.113 refuses by that name.
        page = box_added(set_label_beside_flag(self.genuine, "Match Winner"),
                         "Forfeit", 600.0)
        card = scorecard.read_page(page)
        self.assertEqual([w for w in card.warnings if "checkbox" in w], [
            "labels name 31 of the 31 checkbox squares found, where 4 set rows "
            "need 30; the one more is a home Forfeit box in no set row, which no "
            "row reads"])

    def test_a_flags_label_in_a_set_row_takes_no_square_from_the_nearer_sets_label(
            self):
        # By the square's place and not by the label's kind: a flag's box is
        # read wherever it is, so a Match Disputed printed 5pt left of row 1's
        # home Forfeit square, whose own label is 2.67pt to its right, is paired
        # with the Forfeit label's pairs, and the nearer takes it.
        forfeit = min((w for w in self.genuine.words if w.text == "Forfeit"),
                      key=lambda w: (w.y0, w.x0))
        square = square_beside(self.genuine, forfeit)
        page = printed_again_at(self.genuine, "Match Disputed",
                                baseline=forfeit.baseline, right=square.x0 - 5.0)
        self.assertEqual(self.boxes(page), "checkboxes: 30 squares found, 30 "
                         "named by a label, 30 expected for 4 rows")

    def test_a_refusal_says_where_else_a_lost_flags_square_can_be(self):
        # The away Match Winner label's last word lost, and the stray beside its
        # square: the square is drawn, and the stray names it. Then the lost word
        # printed 300pt lower, beside no square, so that the page prints Match
        # Winner as often as it has boxes for it; the Match Cancelled label lost
        # whole and the stray beside its square, a box with no side; and a set's
        # box drawn again off the table as well, on the away side, where it can
        # be the square too, and on the home side, where it cannot.
        word, _ = away_flag(self.genuine, "Match Winner")
        stray = set_label_beside_flag(self.genuine, "Match Winner")
        lost = dataclasses.replace(
            stray, words=tuple(w for w in stray.words if w is not word))
        cancelled = next(w for w in self.genuine.words if w.text == "Cancelled")
        centred = printed_again_at(
            self.genuine, "Retired", baseline=cancelled.baseline,
            left=square_beside(self.genuine, cancelled).x1 + 3.3)
        centred = dataclasses.replace(centred, words=tuple(
            w for w in centred.words if not (w.text in ("Match", "Cancelled")
                                             and abs(w.baseline
                                                     - cancelled.baseline) < 0.1)))
        away = "an away Retired box in no set row, which no row reads"
        for name, page, found, named, which in (
                ("the away Match Winner label lost", lost, 30, away,
                 "the away Match Winner box"),
                ("its lost word printed again beside no square",
                 printed_again_at(lost, "Match Winner",
                                  baseline=word.baseline + 300.0, left=200.0),
                 30, away, "the away Match Winner box"),
                ("the Match Cancelled label lost whole", centred, 30,
                 "a home Retired box in no set row, which no row reads",
                 "the Match Cancelled box"),
                ("an away box off the table as well",
                 box_added(lost, "Forfeit", 600.0, side="away"), 31,
                 f"one of {away} and an away Forfeit box in no set row, which "
                 "no row reads", "the away Match Winner box"),
                ("a home box off the table as well",
                 box_added(lost, "Forfeit", 600.0), 31, away,
                 "the away Match Winner box")):
            with self.subTest(name):
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                self.assertTrue(refused.detail.startswith(
                    f"this reader finds {found} checkbox squares of 8.07pt where "
                    "4 set rows need 30, and every square it finds is named by "
                    "the label printed beside it, so a box it could not name is "
                    "one whose square it cannot find or one whose square is "
                    f"named as {named}; the one it could not name is {which}"),
                    refused.detail)

    def test_a_refusal_still_says_a_sets_square_is_gone_where_such_a_label_named_one(
            self):
        # 4.34's card: row 1's home Forfeit square gone, and a Forfeit box drawn
        # again off the table, whose square, in no set row, is no set's box's.
        # And the card short of that square alone, where no such label names one.
        for name, page, found in (
                ("4.34's card", fewer_boxes(box_added(self.genuine, "Forfeit",
                                                      600.0)), 30),
                ("row 1's home Forfeit square gone", fewer_boxes(self.genuine),
                 29)):
            with self.subTest(name):
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.MissingCheckboxes)
                self.assertTrue(refused.detail.startswith(
                    f"this reader finds {found} checkbox squares of 8.07pt where "
                    "4 set rows need 30, and every square it finds is named by "
                    "the label printed beside it, so a box it could not name is "
                    "one whose square it cannot find; the one it could not name "
                    "is row 1's home Forfeit box"), refused.detail)

    def test_the_summary_of_a_page_whose_table_it_cannot_find_takes_no_label_off_it(
            self):
        # With no rows to ask about, no label is taken to be in none of them, as
        # an absence of rows is no evidence of it: 4.34's rule, nearest first, so
        # the stray names the square it is nearest, as it did before this.
        page = set_label_beside_flag(self.no_table, "Match Winner")
        _, square = away_flag(page, "Match Winner")
        lines = scorecard.describe(page).splitlines()
        self.assertIn("header, banner and set rows: not read, the grid did not "
                      "resolve", lines)
        self.assertIn("checkboxes: 30 squares found, 30 named by a label", lines)
        self.assertIn(f"  {'Retired':<28} at {square.x0:.1f},{square.y0:.1f} -",
                      lines)

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception


def written_with_set_boxes_right(path: Path, spec: cw.CardSpec) -> Path:
    """``spec``'s card written to ``path`` with each set's box 2.67pt right of its
    label, on both sides, where the template prints it 2.67pt to 3.08pt left of
    it: a template printing the set's boxes the other way about, as
    /tmp/t6/scratch/m5/contend.py wrote it."""
    def right_of(labels_x: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(x + cw.text_width(label, cw.TEXT_SIZE_SMALL) + 2.67
                     for label, x in zip(cw.SET_STATUS_LABELS, labels_x))

    with mock.patch.object(cw, "HOME_STATUS_BOX_X",
                           right_of(cw.HOME_STATUS_LABEL_X)), \
            mock.patch.object(cw, "AWAY_STATUS_BOX_X",
                              right_of(cw.AWAY_STATUS_LABEL_X)):
        return cw.write_card_file(path, spec)


def squares_moved(page: pdf.Page, columns: tuple[float, ...],
                  down: float | None) -> pdf.Page:
    """``page`` with every box whose left edge is at one of ``columns``, edges
    and ticks together, moved ``down`` points, or taken off where ``down`` is
    None."""
    def inside(rect: pdf.Rect) -> bool:
        return _box_part(rect) and any(x - 0.1 <= rect.x0 <= x + cw.BOX_SIDE
                                       for x in columns)
    return dataclasses.replace(page, rects=tuple(
        rect if not inside(rect)
        else dataclasses.replace(rect, y0=rect.y0 + down, y1=rect.y1 + down)
        for rect in page.rects if down is not None or not inside(rect)))


class TestACardPrintingEachSetsBoxRightOfItsLabelIsToldSo(unittest.TestCase):
    """A card that prints each set's box right of its label is refused saying so,
    and not for one label's boxes alone.

    It was refused naming the wrong boxes. Measured by /tmp/t7/scratch/B2/probe.py
    on the card writer's card with each set's box 2.67pt right of its label:
    ``UnlabelledCheckboxes``, "the 8 it could not name are all 8 Forfeit boxes;
    this page prints Forfeit, and a square is named by it only within 16.1pt of
    the label and with its centre within 1.5pt of the label's, up or down, and on
    the label's left", and the reader's summary "30 squares found, 22 named by a
    label, 30 expected for 4 rows and 8 of those not named: all 8 Forfeit
    boxes". A set's label names only a square on its left, so the Retired label
    named the Forfeit square and the Incomplete label the Retired one, and the
    one label left with nothing on its left was the only one said to be short.

    So where the square nearest every set's label the page prints, on its line,
    is on the label's right, the refusal says the page prints the set's boxes
    right of their labels, in place of the reach clause for the set's labels,
    and the summary says it on the line that counts the boxes not named. The
    kind is the labels': another template's layout. Measured by
    /tmp/t7/scratch/B2/sides.py: on that card all 24 of the set's labels have
    their nearest square on the right, and on the card as written none do. A
    set's label with no square on its line says nothing of the side and is left
    out: asked of it as well, a card whose comments say "forfeit" or "Retired"
    was told of the reach again, measured by /tmp/t7/scratch/B2/fix/siblings.py.

    Rejected: the test as filed, a Forfeit label with no square on its left and
    one on its right in every row. Every Forfeit label on the mirrored card
    passes it, and so does every Forfeit label on a card whose Forfeit squares
    are all 2pt below their line, which it would have told that its boxes are
    printed right of their labels, since the Retired square is 7.26pt right of
    each; 8 of that card's 24 labels have their nearest square on the right.
    Rejected too: reading such a card, with a set's label looking right where the
    page prints its boxes so. No template measured prints them so, and a box is
    its label's because the template puts the two together; a layout nobody has
    measured is for whoever maintains this tool, which is where the doctor's
    cause for the labels' kind sends it.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.genuine = pdf.extract(cw.write_card_file(cls.root / "genuine.pdf",
                                                     cw.CardSpec()))
        cls.right_file = written_with_set_boxes_right(cls.root / "right.pdf",
                                                      cw.CardSpec())
        cls.right = pdf.extract(cls.right_file)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception

    SAID = ("this page prints the set's boxes right of their labels: the square "
            "nearest every Forfeit, Retired and Incomplete label on its line is "
            "on the label's right, and a set's label names only a square on its "
            "left")

    def test_the_refusal_says_the_page_prints_the_sets_boxes_right_of_their_labels(
            self):
        refused = self.refusal(self.right)
        self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
        self.assertEqual(refused.reason,
                         "this reader could not name 8 of the 30 checkboxes this "
                         "card needs, so a tick in any of them would be invisible")
        self.assertTrue(refused.detail.endswith(
            f"; the 8 it could not name are all 8 Forfeit boxes; {self.SAID}"),
            refused.detail)
        # Said once, in place of the reach clause it would have repeated.
        self.assertNotIn("a square is named by it only", refused.detail)

    def test_the_summary_says_so_where_it_counts_the_boxes_not_named(self):
        self.assertIn("checkboxes: 30 squares found, 22 named by a label, 30 "
                      "expected for 4 rows and 8 of those not named: all 8 "
                      "Forfeit boxes; this page prints the set's boxes right of "
                      "their labels\n", scorecard.describe(self.right))

    def test_a_whole_match_flag_lost_as_well_keeps_its_own_clause(self):
        # The two centred flags' squares 2pt below their line on that card: the
        # flags are said to fail the reach test, and the set's labels are not.
        refused = self.refusal(squares_moved(self.right, (cw.CENTRE_FLAG_BOX_X,),
                                             2.0))
        self.assertTrue(refused.detail.endswith(
            f"; {self.SAID}; this page prints Match Cancelled and Match Disputed, "
            "and a square is named by one of them only within 16.1pt of the label "
            "and with its centre within 1.5pt of the label's, up or down"),
            refused.detail)

    def test_a_sets_label_the_page_does_not_print_is_left_out_of_it(self):
        # One of the set's labels printed in another template's word as well:
        # the labels the page prints are the ones said, and the one it does not
        # is said as a word it could not read. Forfeit is the label left short,
        # so with it gone no label printed as often as it needs is short.
        for label, which, said in (
                ("Incomplete", "the 16 it could not name are all 8 Forfeit boxes "
                               "and all 8 Incomplete boxes", "Forfeit and Retired"),
                ("Forfeit", "the 8 it could not name are all 8 Forfeit boxes",
                 "Retired and Incomplete")):
            with self.subTest(reworded=label):
                path = self.root / f"right-{label}.pdf"
                path.write_bytes(relabelled(self.right_file.read_bytes(), {
                    label: OTHER_TEMPLATE_LABELS[label]}))
                refused = self.refusal(pdf.extract(path))
                self.assertIsInstance(refused, scorecard.UnlabelledCheckboxes)
                self.assertTrue(refused.detail.endswith(
                    f"; {which}; no word on this page is read as {label}, which "
                    "it looks for as words of their own in any capitalisation; "
                    "this page prints the set's boxes right of their labels: the "
                    f"square nearest every {said} label on its line is on the "
                    "label's right, and a set's label names only a square on its "
                    "left"), refused.detail)

    def test_a_sets_word_in_the_comments_does_not_take_it_back(self):
        # Read as a set's label, as any word of its own is, with no square on
        # its line.
        for comments in ("The home pair forfeit set 4",
                         "Home player Retired hurt in set 3"):
            with self.subTest(comments=comments):
                page = pdf.extract(written_with_set_boxes_right(
                    self.root / "right-comments.pdf",
                    cw.CardSpec(comments=comments)))
                refused = self.refusal(page)
                self.assertTrue(refused.detail.endswith(
                    f"; the 8 it could not name are all 8 Forfeit boxes; "
                    f"{self.SAID}"), refused.detail)
                self.assertIn("8 of those not named: all 8 Forfeit boxes; this "
                              "page prints the set's boxes right of their labels\n",
                              scorecard.describe(page))

    def test_forfeit_squares_off_their_line_are_not_said_to_be_right_of_it(self):
        # Every Forfeit label has no square on its left and the Retired one on
        # its right, as on the card above, and the other two labels have their
        # own squares on their left.
        page = squares_moved(self.genuine, (cw.HOME_STATUS_BOX_X[0],
                                            cw.AWAY_STATUS_BOX_X[0]), 2.0)
        refused = self.refusal(page)
        self.assertTrue(refused.detail.endswith(
            "; the 8 it could not name are all 8 Forfeit boxes; this page prints "
            "Forfeit, and a square is named by it only within 16.1pt of the label "
            "and with its centre within 1.5pt of the label's, up or down, and on "
            "the label's left"), refused.detail)
        self.assertNotIn("right of their labels", scorecard.describe(page))

    def test_a_card_that_lost_every_sets_square_is_not_said_to_print_them_right(
            self):
        # No set's label has a square on its line at all.
        page = squares_moved(self.genuine,
                             cw.HOME_STATUS_BOX_X + cw.AWAY_STATUS_BOX_X, None)
        refused = self.refusal(page)
        self.assertIsInstance(refused, scorecard.MissingCheckboxes)
        self.assertTrue(refused.detail.endswith(
            "; the 24 it could not name are all 8 Forfeit boxes, all 8 Retired "
            "boxes and all 8 Incomplete boxes"), refused.detail)
        self.assertNotIn("right of their labels", scorecard.describe(page))

    def test_the_doctor_gives_another_templates_layout_as_the_cause(self):
        done = subprocess.run((sys.executable, str(DOCTOR), str(self.right_file)),
                              capture_output=True, text=True, timeout=TIMEOUT,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertNotIn(TRACEBACK, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 1, done.stdout[-2000:])
        summary, verdict = (" ".join(half.split())
                            for half in done.stdout.split("\n    verdict\n"))
        self.assertEqual(verdict.count("right of their labels"), 1, verdict)
        self.assertEqual(summary.count("right of their labels"), 1, summary)
        self.assertIn("Likeliest cause: the card was printed from another "
                      "template", verdict)
        # Its labels are beside their boxes, on the other side of them.
        self.assertIn("tick-box labels differently or lays them out another way;",
                      verdict)
        self.assertNotIn("away from their boxes", verdict)


class TestTheSummaryOfACardRefusedForAMatchWinnerBoxDoesNotSayItAgain(
        unittest.TestCase):
    """The reader's own summary of a card refused for a Match Winner box does not
    count the boxes again, nor call the card incomplete.

    It did both. Measured by /tmp/t6/scratch/m5/before.py on the card writer's
    card with both ``Winner`` words taken off: the refusal says "the 2 it could
    not name are both Match Winner boxes", and the summary printed "cross-check
    winner-tick: not printed - only 0 Match Winner box(es) were located" and the
    warning "only 0 'Match Winner' box(es) were located, so this card's match
    winner state may be incomplete". The doctor prints the summary above its
    verdict, so its report said the loss three times and the third called a
    refused card one that may be incomplete. With one ``Winner`` taken off, both
    lines said "only 1".

    Neither line can be printed of a card the reader reads, because one with a
    Match Winner box it cannot name is refused before any flag is read. So the
    count warning is gone, and the summary leaves the Match Winner check out of a
    card on which a Match Winner box is not named, and names each box that is
    not in its checkbox line, in the refusal's words. That line is the one that
    says it of a card refused first for something else: measured by
    /tmp/t6/scratch/m5/fx/probe3.py, with ``Round`` taken off as well, the card
    is refused for its header, and the summary said "2 of those not named" and
    nothing of which two. A card refused for another box, or whose Match
    Forfeited box is not named, keeps the check, since its Match Winner boxes are
    named and the check is what the reader found.

    Rejected: leaving every cross-check out of the summary of a refused card.
    The totals and the verb are read from the rows and the banner and not from a
    box, and a card refused for row 1's home Forfeit box still has a true Match
    Winner check, "the home Match Winner tick agrees with the set rows". Rejected
    too: rewording the check's line to point at the refusal, which is the loss
    said a third time.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        raw = cw.write_card(cw.CardSpec())
        cls.genuine = pdf.extract(cw.write_card_file(root / "genuine.pdf",
                                                     cw.CardSpec()))
        cls.no_winner = word_lost(word_lost(cls.genuine, "Winner"), "Winner")
        cls.one_winner = word_lost(cls.genuine, "Winner")
        # The file the doctor is run on: both words printed as another word.
        cls.no_winner_file = root / "no-winner.pdf"
        cls.no_winner_file.write_bytes(relabelled(raw, {"Winner": "Won"}))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_summary_prints_no_match_winner_check_for_a_card_refused_for_one(
            self):
        for name, page, unnamed in (("both", self.no_winner,
                                     "2 of those not named: both Match Winner "
                                     "boxes"),
                                    ("one", self.one_winner,
                                     "1 of those not named: the home Match "
                                     "Winner box")):
            with self.subTest(winner_words_lost=name):
                text = scorecard.describe(page)
                # The premise, as the summary counts it, and the checks it has.
                self.assertIn(f"expected for 4 rows and {unnamed}\n", text)
                self.assertIn("cross-check totals: agrees", text)
                self.assertIn("cross-check verb: agrees", text)
                self.assertNotIn("cross-check winner-tick", text)

    def test_the_summary_warns_of_no_box_count_the_refusal_already_gives(self):
        for name, page in (("both", self.no_winner), ("one", self.one_winner)):
            with self.subTest(winner_words_lost=name):
                text = scorecard.describe(page)
                self.assertNotIn("box(es) were located", text)
                self.assertNotIn("may be incomplete", text)
                self.assertIn("\nwarnings: none", text)

    def test_a_card_refused_for_another_box_keeps_its_match_winner_check(self):
        # The Match Forfeited box as well as a set's, because the two sided flags
        # are read by one function and a gate on either passed the set's card.
        for lost, page, unnamed in (
                ("row 1's home Forfeit", fewer_boxes(self.genuine),
                 "row 1's home Forfeit box"),
                ("home Match Forfeited", word_lost(self.genuine, "Forfeited"),
                 "the home Match Forfeited box")):
            with self.subTest(lost=lost):
                text = scorecard.describe(page)
                self.assertIn("expected for 4 rows and 1 of those not named: "
                              f"{unnamed}\n", text)
                self.assertIn("cross-check winner-tick: agrees - the home Match "
                              "Winner tick agrees with the set rows", text)

    def test_a_card_refused_first_for_its_header_names_the_boxes_in_the_summary(
            self):
        page = dataclasses.replace(self.no_winner, words=tuple(
            w for w in self.no_winner.words if w.text != "Round"))
        # The premise: refused for the header, before a box is asked after.
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        self.assertNotIsInstance(caught.exception, (
            scorecard.MissingCheckboxes, scorecard.UnlabelledCheckboxes,
            scorecard.UnlocatedCheckboxes))
        self.assertIn("names both a division and a round", caught.exception.reason)
        text = scorecard.describe(page)
        self.assertIn("checkboxes: 30 squares found, 28 named by a label, 30 "
                      "expected for 4 rows and 2 of those not named: both Match "
                      "Winner boxes\n", text)
        self.assertNotIn("cross-check winner-tick", text)

    def test_the_doctor_gives_the_loss_once_in_its_verdict(self):
        # And once among the boxes the summary lists, where the checkbox line
        # names the boxes it counts as not named.
        done = subprocess.run((sys.executable, str(DOCTOR),
                               str(self.no_winner_file)),
                              capture_output=True, text=True, timeout=TIMEOUT)
        self.assertNotIn(TRACEBACK, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 1, done.stdout[-2000:])
        summary, verdict = done.stdout.split("\n    verdict\n")
        self.assertEqual(verdict.count("both Match Winner boxes"), 1, verdict)
        # Its lines joined, as the summary goes on past the width (4.186).
        summary = " ".join(summary.split())
        self.assertEqual(summary.count("both Match Winner boxes"), 1, summary)
        self.assertIn("checkboxes: 30 squares found, 28 named by a label, 30 "
                      "expected for 4 rows and 2 of those not named: both Match "
                      "Winner boxes", summary)
        for said_again in ("box(es) were located", "may be incomplete",
                           "cross-check winner-tick"):
            with self.subTest(said_again=said_again):
                self.assertNotIn(said_again, done.stdout)


class TestABoxNamedBeyondTheOnesACardNeedsIsSaidWhereItIs(unittest.TestCase):
    """A box named beyond the ones a card needs is said where it is, as the
    refusal says where a box it could not name is.

    It was said as a count. Measured by /tmp/t6/scratch/m5/before.py and
    surplus.py on the card writer's card with its first Forfeit label and box
    drawn again 600pt lower, off the table: read, with "labels name 31 of the 31
    checkbox squares found, where 4 set rows need 30; a tick on any box they do
    not name was not seen". The last clause is about a square no label names,
    and every square on that card is named. What it did not say is where the one
    more box is: in no set row, where a tick is read by nothing, and the card
    ticked there read four sets played. Drawn 12pt lower instead, the box is in
    row 2, a second home Forfeit box there, and ticked it read that set as a
    forfeit, under the same warning.

    Each box more than the card needs is now named by the function the refusal
    names a box by, the clause about a square no label names is kept for a card
    that has one, and the count stays, since it is what says the drawing is
    otherwise the template's. A box in no set row is given the side it is on,
    since that is how a club finds it, and a place's box drawn twice more is
    counted, as a label printed short is. Places drawn again as many times over
    are named together, as the refusal names a row's side's boxes together.

    Rejected: naming the extra box by its label alone, "a Forfeit box", which is
    any of eight. Rejected too: a warning for each box, which says one layout
    once for each of its boxes; and a phrase for each place with its count after
    it, "row 2's home Forfeit box again, 2 times", which splits one place over
    two clauses.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.genuine = pdf.extract(cw.write_card_file(
            Path(cls._tmp.name) / "genuine.pdf", cw.CardSpec()))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def box_warnings(self, page: pdf.Page) -> list[str]:
        return [w for w in scorecard.read_page(page).warnings if "checkbox" in w]

    def test_a_second_box_in_a_set_row_is_named_as_the_box_it_repeats(self):
        # 12pt below row 1's first Forfeit box is inside row 2.
        page = box_added(self.genuine, "Forfeit", 12.0)
        self.assertEqual(self.box_warnings(page), [
            "labels name 31 of the 31 checkbox squares found, where 4 set rows "
            "need 30; the one more is row 2's home Forfeit box again"])

    def test_two_boxes_in_no_set_row_are_counted_where_they_are(self):
        page = box_added(box_added(self.genuine, "Forfeit", 600.0), "Forfeit", 620.0)
        self.assertEqual(self.box_warnings(page), [
            "labels name 32 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are 2 home Forfeit boxes in no set row, which no "
            "row reads"])

    def test_boxes_more_of_both_kinds_are_each_named(self):
        page = box_added(box_added(self.genuine, "Retired", 600.0), "Incomplete",
                         12.0)
        self.assertEqual(self.box_warnings(page), [
            "labels name 32 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are row 2's home Incomplete box again and a home "
            "Retired box in no set row, which no row reads"])

    def test_a_box_in_no_set_row_is_given_the_side_it_is_on(self):
        # Row 1's away Forfeit label and box drawn again: every other card here
        # adds a home one, so a side said as home whatever it is passed them.
        page = box_added(self.genuine, "Forfeit", 600.0, side="away")
        self.assertEqual(self.box_warnings(page), [
            "labels name 31 of the 31 checkbox squares found, where 4 set rows "
            "need 30; the one more is an away Forfeit box in no set row, which no "
            "row reads"])

    def test_a_place_with_two_boxes_more_is_counted_as_two(self):
        # 12pt and 14pt below row 1's, both in row 2, and 2pt apart, so neither
        # label is on the other's square's line; and 16pt for a third.
        for downs, more in (((12.0, 14.0), "the 2 more are row 2's home Forfeit "
                                           "box twice more"),
                            ((12.0, 14.0, 16.0), "the 3 more are row 2's home "
                                                 "Forfeit box 3 times more")):
            with self.subTest(more=len(downs)):
                page = self.genuine
                for down in downs:
                    page = box_added(page, "Forfeit", down)
                self.assertEqual(self.box_warnings(page), [
                    f"labels name {30 + len(downs)} of the {30 + len(downs)} "
                    "checkbox squares found, where 4 set rows need 30; " + more])

    def test_boxes_again_on_one_rows_side_are_named_together(self):
        # As the refusal names a row's side's boxes it could not name.
        page = box_added(box_added(self.genuine, "Forfeit", 12.0), "Retired", 12.0)
        self.assertEqual(self.box_warnings(page), [
            "labels name 32 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are row 2's home Forfeit and Retired boxes "
            "again"])

    def test_a_whole_match_box_drawn_again_is_named_as_that_box_again(self):
        # Anywhere on the page, since a flag has no row to be out of: the home
        # Match Winner, a sided one, and Match Cancelled, which has no side.
        page = box_added(box_added(self.genuine, "Match Winner", 400.0),
                         "Match Cancelled", 400.0)
        self.assertEqual(self.box_warnings(page), [
            "labels name 32 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are the home Match Winner box again and the "
            "Match Cancelled box again"])


class TestAForfeitTickedInABoxTheReaderCannotNameFailsTheBuildOnce(
        unittest.TestCase):
    """A forfeit ticked in a box this reader cannot name fails the build, once.

    It built at exit 0 and published the forfeit as a match played. Measured by
    /tmp/t4/scratch/y434/m2/season.py on the suite's own six-card season and a
    seventh card, a forfeit ticked against the away side over 6-2, 6-3, 2-6 and
    6-1, written with its boxes 1.5pt below their labels: exit 0, ``0 error(s),
    2 warning(s)`` and neither about the card, and the page showed it ``played``
    with three notes about its boxes. The card as written reads ``forfeit``.

    Refused, the card is one F4 error: the loss is said once for the card, and not
    once for each of the six boxes, nor once for each of the reader's three notes
    about them. Rejected: sending the three notes to the report as warnings, which
    says the loss three times and leaves the forfeit published as played.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        write_season_cards(cls.root / "data")
        spec = round_four_card(forfeited_by="away")
        cls.as_written = cw.write_card_file(cls.root / "as-written.pdf", spec)
        with mock.patch.object(cw, "BOX_LABEL_RISE", cw.BOX_LABEL_RISE - 1.5):
            cls.moved = cw.write_card_file(
                cls.root / "data" / "Round4" / "forfeit.pdf", spec)
        done = subprocess.run(
            (sys.executable, str(BUILD), "--generated", "2026-09-23T09:00:00"),
            cwd=cls.root, capture_output=True, text=True, timeout=TIMEOUT)
        cls.code = done.returncode
        cls.report = done.stdout + done.stderr
        cls.words = " ".join(cls.report.split())
        cls.matches = {
            match["id"]: match
            for path in sorted((cls.root / "site").rglob("season.json"))
            for match in json.loads(path.read_text(encoding="utf-8"))["matches"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_build_fails_and_names_the_card_and_why(self):
        # The premise: the card as written is a forfeit.
        cards, rejections = scorecard.read(self.as_written)
        self.assertEqual((rejections, cards[0].forfeited_by), ([], "away"))
        self.assertNotIn(TRACEBACK, self.report)
        self.assertEqual(self.code, 1, self.report[-3000:])
        self.assertIn("data/Round4/forfeit.pdf: this reader could not name 6 of the "
                      "30 checkboxes this card needs", self.words)

    def test_the_card_is_one_error_and_not_one_for_each_box(self):
        # One error under its rule, which the competition's own line counts as
        # the run's one refused card and not as an error of its own.
        self.assertRegex(self.report, r"(?m)^== 1 error =")
        self.assertRegex(self.report, r"problems\s+0 error\(s\), \d+ warning\(s\) "
                                      r"and the run's 1 refused card\(s\)")
        self.assertEqual(len(re.findall(r"^  F4$", self.report, re.M)), 1)
        for note in ("box(es) were located", "labels name"):
            with self.subTest(note=note):
                self.assertEqual(self.report.count(note), 0)

    def test_the_page_shows_no_reading_of_the_card(self):
        self.assertIn("2000001", self.matches)  # the premise: the season is there
        self.assertNotIn("2000099", self.matches)


class TestTheReadersSummaryGivesTheBoxWarningsItsReadingGives(unittest.TestCase):
    """The reader's own summary gives the warnings its reading gives a card's
    boxes, from the one function that writes them.

    It gave none of them. Measured by /tmp/t7/scratch/B1/probe/probe.py on the
    card writer's card with its first Forfeit label and square drawn again 600pt
    lower, off the table: read, with "labels name 31 of the 31 checkbox squares
    found, where 4 set rows need 30; the one more is a home Forfeit box in no set
    row, which no row reads", while the summary counted "31 squares found, 31
    named by a label, 30 expected for 4 rows" and said "warnings: none". The
    doctor prints the summary above its verdict, so its report said "warnings:
    none" and then gave the warning. The summary gathered its own warnings and
    never asked the function that wrote this one, which only the reading called.

    Both now ask one function, which holds every warning the reading gives a
    card's boxes and the refusal of a tick nothing reads. Rejected: the summary
    calling the reading, which raises at the first refusal, where the summary has
    to keep going after a stage fails.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.genuine = pdf.extract(cw.write_card_file(root / "genuine.pdf",
                                                     cw.CardSpec()))
        cls.box_more = written_with_a_box_more(root / "box-more.pdf",
                                               cw.CardSpec(), ticked=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_summary_lists_each_box_warning_the_reading_gives(self):
        for name, page in (
                ("in no set row", box_added(self.genuine, "Forfeit", 600.0)),
                ("in row 2", box_added(self.genuine, "Forfeit", 12.0)),
                ("whole-match", box_added(box_added(self.genuine, "Match Winner",
                                                    400.0),
                                          "Match Cancelled", 400.0))):
            with self.subTest(box_more=name):
                read = list(scorecard.read_page(page).warnings)
                # The premise: the reading warns of the boxes, and of nothing else.
                self.assertEqual(len(read), 1)
                self.assertIn("checkbox squares found", read[0])
                text = scorecard.describe(page)
                self.assertIn("\nwarnings: 1\n", text)
                self.assertEqual(summary_warnings(text), read)

    def test_the_summary_gives_them_where_the_reading_does_after_the_header(self):
        # A card with no Court label and a square no label names: the reading
        # warns of the header first and the boxes after, and so does the summary.
        page = drawn_again(word_lost(self.genuine, "Court:"), "Forfeit", 600.0,
                           labelled=False)
        read = list(scorecard.read_page(page).warnings)
        # The premise: one warning of each, the header's first.
        self.assertEqual(len(read), 2)
        self.assertIn("'Court'", read[0])
        self.assertEqual(summary_warnings(scorecard.describe(page)), read)

    def test_a_card_drawn_as_the_template_draws_it_is_warned_of_no_box(self):
        self.assertEqual(scorecard.read_page(self.genuine).warnings, ())
        self.assertTrue(scorecard.describe(self.genuine).endswith(
            "\nwarnings: none"))

    def test_a_card_refused_for_a_box_it_cannot_name_is_not_told_of_boxes_more(
            self):
        # Its checkbox line names the box it cannot name. The box warnings are of a
        # card whose every needed box is named, so asked of this one they would
        # count it as having boxes more: "the -1 more are", with the square gone.
        for name, page in (("square", row_one_box_lost(self.genuine, "Forfeit")),
                           ("label", word_lost(self.genuine, "Forfeit"))):
            with self.subTest(forfeit_lost=name):
                with self.assertRaises(CardError):
                    scorecard.read_page(page)
                text = scorecard.describe(page)
                self.assertIn("1 of those not named: row 1's home Forfeit box",
                              text)
                self.assertTrue(text.endswith("\nwarnings: none"), text[-500:])

    def test_the_doctor_gives_it_once_in_its_summary_and_once_in_its_verdict(self):
        done = subprocess.run((sys.executable, str(DOCTOR), str(self.box_more)),
                              capture_output=True, text=True, timeout=TIMEOUT)
        self.assertNotIn(TRACEBACK, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        summary, verdict = done.stdout.split("\n    verdict\n")
        warning = ("labels name 31 of the 31 checkbox squares found, where 4 set "
                   "rows need 30; the one more is a home Forfeit box in no set "
                   "row, which no row reads")
        # Each with its lines joined, as both go on past the width (4.186).
        summary, verdict = (" ".join(text.split()) for text in (summary, verdict))
        self.assertEqual(summary.count("labels name"), 1, summary)
        self.assertIn(warning, summary)
        self.assertEqual(verdict.count("labels name"), 1, verdict)
        self.assertIn(f" warning {warning}", verdict)


class TestASquareNoLabelNamesIsSaidWhereItIs(unittest.TestCase):
    """A square no label names, on a card whose labels name every box it needs,
    is said where it is.

    Nothing was said of it. Measured by /tmp/t7/scratch/B1/probe/probe.py on the
    card writer's card with row 1's home Forfeit square drawn again and no label
    beside it, 600pt lower, off the table, or 12pt lower, in row 2: read, four
    sets played, with no warning, while the summary counted "31 squares found, 30
    named by a label". Ticked, either read the same, and said nothing. Only the
    warning for a card whose labels name more boxes than it needs spoke of such a
    square, "a tick on any box they do not name was not seen", and only on a card
    whose labels named a box more as well.

    It is now one of the squares more than the card needs, said by its place in
    the words the reader gives every box's place: "a square on row 2's home side,
    which no label names", or "on the home side in no set row", since a square in
    no row is found by its side, as a box in no row is. A tick in one refuses the
    card, by the rule for a tick no row reads. Rejected: refusing every card that
    draws such a square, which would stop a card that reads correctly for a square
    a printing artefact left on it.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.genuine = pdf.extract(cw.write_card_file(
            Path(cls._tmp.name) / "genuine.pdf", cw.CardSpec()))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_a_square_in_no_set_row_is_said_with_the_side_it_is_on(self):
        for side in ("home", "away"):
            with self.subTest(side=side):
                card = scorecard.read_page(drawn_again(
                    self.genuine, "Forfeit", 600.0, side=side, labelled=False))
                self.assertEqual([s.status.value for s in card.sets],
                                 ["played"] * 4)
                self.assertEqual(list(card.warnings), [
                    "labels name 30 of the 31 checkbox squares found, where 4 set "
                    f"rows need 30; the one more is a square on the {side} side in "
                    "no set row, which no label names"])

    def test_a_square_in_a_set_row_is_said_with_its_row_and_side(self):
        card = scorecard.read_page(drawn_again(self.genuine, "Forfeit", 12.0,
                                               labelled=False))
        self.assertEqual(list(card.warnings), [
            "labels name 30 of the 31 checkbox squares found, where 4 set rows "
            "need 30; the one more is a square on row 2's home side, which no "
            "label names"])

    def test_squares_in_one_place_are_counted_there(self):
        page = drawn_again(drawn_again(self.genuine, "Forfeit", 600.0,
                                       labelled=False),
                           "Forfeit", 620.0, labelled=False)
        self.assertEqual(list(scorecard.read_page(page).warnings), [
            "labels name 30 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are 2 squares on the home side in no set row, "
            "which no label names"])

    def test_squares_in_two_places_are_each_named(self):
        page = drawn_again(drawn_again(self.genuine, "Forfeit", 600.0,
                                       labelled=False),
                           "Retired", 12.0, side="away", labelled=False)
        self.assertEqual(list(scorecard.read_page(page).warnings), [
            "labels name 30 of the 32 checkbox squares found, where 4 set rows "
            "need 30; the 2 more are a square on row 2's away side, which no label "
            "names and a square on the home side in no set row, which no label "
            "names"])

    def test_the_readers_summary_says_it_too(self):
        page = drawn_again(self.genuine, "Forfeit", 600.0, labelled=False)
        text = scorecard.describe(page)
        # The premise, as the summary has always counted it.
        self.assertIn("checkboxes: 31 squares found, 30 named by a label, 30 "
                      "expected for 4 rows\n", text)
        self.assertIn("\nwarnings: 1\n", text)
        self.assertEqual(summary_warnings(text),
                         list(scorecard.read_page(page).warnings))


class TestATickInABoxNoRowReadsRefusesTheCard(unittest.TestCase):
    """A card with a tick in a box no row reads, or in a square no label names, is
    refused, naming the box and where it is.

    It was read as though nothing were ticked there. Measured by
    /tmp/t7/scratch/B1/probe/probe.py on the card writer's card with row 1's home
    Forfeit label and box drawn again 600pt lower, off the table, and ticked:
    read, four sets played and won at home, with "the one more is a home Forfeit
    box in no set row, which no row reads", word for word the warning the card
    has unticked. Only the summary's list of boxes said TICKED. Ticked in a square
    with no label beside it, in row 2 or off the table, the card read the same,
    with no warning at all.

    A tick in either is refused, as a tick in a box the reader cannot name is: the
    cards are the only source of a result, and one the card may contradict is not
    published. The box is named by its place, in the words a box more than the
    card needs is named by, since that is how a club finds it on the card. A box
    a row or a flag reads, drawn again inside its row or anywhere for a
    whole-match flag, is read as it was, ticked or not. Rejected: saying in the
    warning that the box is ticked, which still publishes the match as played.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.genuine = pdf.extract(cw.write_card_file(
            Path(cls._tmp.name) / "genuine.pdf", cw.CardSpec()))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def refusal(self, page: pdf.Page) -> CardError:
        with self.assertRaises(CardError) as caught:
            scorecard.read_page(page)
        return caught.exception

    def test_a_forfeit_ticked_in_a_box_no_row_reads_is_refused_naming_it(self):
        for side, article in (("home", "a"), ("away", "an")):
            with self.subTest(side=side):
                refused = self.refusal(drawn_again(self.genuine, "Forfeit", 600.0,
                                                   side=side, ticked=True))
                self.assertIsInstance(refused, scorecard.UnreadTick)
                self.assertEqual(refused.reason,
                                 "this card ticks a checkbox this reader reads "
                                 "nothing from, so it would be read as if that box "
                                 "were not ticked")
                self.assertEqual(refused.detail,
                                 "labels name 31 of the 31 checkbox squares found, "
                                 "where 4 set rows need 30; the tick is in "
                                 f"{article} {side} Forfeit box in no set row, "
                                 "which no row reads")

    def test_any_sets_box_ticked_above_or_below_the_table_is_refused_naming_it(
            self):
        # Not a Forfeit's alone, and not below the table alone: a set's box in no
        # set row is one no row reads, whatever its label and wherever it is.
        for label, down, side, box in (
                ("Retired", 600.0, "away", "an away Retired box"),
                ("Forfeit", -60.0, "home", "a home Forfeit box")):
            with self.subTest(label=label, down=down):
                page = drawn_again(self.genuine, label, down, side=side,
                                   ticked=True)
                refused = self.refusal(page)
                self.assertIsInstance(refused, scorecard.UnreadTick)
                self.assertEqual(refused.detail,
                                 "labels name 31 of the 31 checkbox squares found, "
                                 f"where 4 set rows need 30; the tick is in {box} "
                                 "in no set row, which no row reads")

    def test_a_tick_in_a_square_no_label_names_is_refused_naming_it(self):
        for down, place in ((12.0, "a square on row 2's home side"),
                            (600.0, "a square on the home side in no set row")):
            with self.subTest(down=down):
                refused = self.refusal(drawn_again(self.genuine, "Forfeit", down,
                                                   labelled=False, ticked=True))
                self.assertIsInstance(refused, scorecard.UnreadTick)
                self.assertEqual(refused.detail,
                                 "labels name 30 of the 31 checkbox squares found, "
                                 f"where 4 set rows need 30; the tick is in {place}, "
                                 "which no label names")

    def test_ticks_in_two_such_boxes_are_counted_together(self):
        page = drawn_again(drawn_again(self.genuine, "Forfeit", 600.0, ticked=True),
                           "Retired", 12.0, labelled=False, ticked=True)
        refused = self.refusal(page)
        self.assertEqual(refused.reason,
                         "this card ticks 2 checkboxes this reader reads nothing "
                         "from, so it would be read as if those boxes were not "
                         "ticked")
        self.assertEqual(refused.detail,
                         "labels name 31 of the 32 checkbox squares found, where 4 "
                         "set rows need 30; the 2 ticks are in a home Forfeit box in "
                         "no set row, which no row reads and a square on row 2's "
                         "home side, which no label names")

    def test_ticks_in_two_squares_in_one_place_are_counted_there(self):
        # One phrase for the place, and the ticks still counted as two.
        page = drawn_again(drawn_again(self.genuine, "Forfeit", 600.0,
                                       labelled=False, ticked=True),
                           "Forfeit", 620.0, labelled=False, ticked=True)
        refused = self.refusal(page)
        self.assertEqual(refused.reason,
                         "this card ticks 2 checkboxes this reader reads nothing "
                         "from, so it would be read as if those boxes were not "
                         "ticked")
        self.assertEqual(refused.detail,
                         "labels name 30 of the 32 checkbox squares found, where 4 "
                         "set rows need 30; the 2 ticks are in 2 squares on the "
                         "home side in no set row, which no label names")

    def test_one_tick_under_a_square_painted_twice_is_one_tick(self):
        # A bare square 600pt lower, its outline painted twice at one point over
        # one fill: two squares found, as the counts have always had it, and one
        # tick, since a tick is its fill however many outlines are painted round
        # it. Counted by square, it was "this card ticks 2 checkboxes".
        once = drawn_again(self.genuine, "Forfeit", 600.0, labelled=False,
                           ticked=True)
        outline = box_added(self.genuine, "Forfeit",
                            600.0).rects[len(self.genuine.rects):]
        refused = self.refusal(dataclasses.replace(once,
                                                   rects=once.rects + outline))
        self.assertEqual(refused.reason,
                         "this card ticks a checkbox this reader reads nothing "
                         "from, so it would be read as if that box were not "
                         "ticked")
        self.assertEqual(refused.detail,
                         "labels name 30 of the 32 checkbox squares found, where 4 "
                         "set rows need 30; the tick is in a square on the home "
                         "side in no set row, which no label names")

    def test_a_ticked_box_painted_again_a_little_off_is_read_as_it_was(self):
        # The home Match Winner box, ticked on every card won at home, with its
        # square painted again 0.3pt or 2pt lower and no label beside it, and its
        # tick painted again with it or not. The tick is in the named box, which
        # reads it, so nothing is lost, and the square is warned of as any square
        # no label names is. Rejected: asking whether any fill on the page is in
        # the square, which refused this card, one the base read correctly, as a
        # tick "read as if that box were not ticked".
        for down in (0.3, 2.0):
            again = drawn_again(self.genuine, "Match Winner", down,
                                labelled=False)
            edges = tuple(r for r in again.rects[len(self.genuine.rects):]
                          if min(r.w, r.h) < cw.BOX_SIDE - 0.05)
            for tick, page in (
                    ("painted again", again),
                    ("painted once", dataclasses.replace(
                        again, rects=self.genuine.rects + edges))):
                with self.subTest(down=down, tick=tick):
                    card = scorecard.read_page(page)
                    self.assertEqual(card.winner_tick, "home")
                    self.assertEqual(list(card.warnings), [
                        "labels name 30 of the 31 checkbox squares found, where 4 "
                        "set rows need 30; the one more is a square on the home "
                        "side in no set row, which no label names"])

    def test_a_tick_in_a_box_a_row_or_a_flag_reads_is_read_as_it_was(self):
        # Row 2's home Forfeit box drawn again in row 2, the home Match Winner box
        # drawn again with the tick it has on this card, and Match Cancelled drawn
        # again and ticked.
        forfeit = scorecard.read_page(drawn_again(self.genuine, "Forfeit", 12.0,
                                                  ticked=True))
        self.assertEqual([s.status.value for s in forfeit.sets],
                         ["played", "forfeit", "played", "played"])
        winner = scorecard.read_page(box_added(self.genuine, "Match Winner", 400.0))
        self.assertEqual(winner.winner_tick, "home")
        cancelled = scorecard.read_page(drawn_again(self.genuine, "Match Cancelled",
                                                    400.0, ticked=True))
        self.assertTrue(cancelled.cancelled)

    def test_the_summary_keeps_going_and_says_it_once(self):
        page = drawn_again(self.genuine, "Forfeit", 600.0, ticked=True)
        refused = self.refusal(page)
        text = scorecard.describe(page)
        # The reason on its row and the detail on a line of its own beneath it,
        # which the doctor's --redact scrubs and the reason it does not (4.185).
        self.assertEqual([line for line in text.splitlines()
                          if refused.reason in line or refused.detail in line],
                         [f"ticks: NOT READ - {refused.reason}",
                          f"  detail: {refused.detail}"])
        self.assertIn("\nset rows (4):\n", text)
        self.assertIn("cross-check winner-tick: agrees", text)
        # Not said again as the warning the card has unticked.
        self.assertTrue(text.endswith("\nwarnings: none"), text[-500:])


class TestATickInABoxNoRowReadsFailsTheBuildOnce(unittest.TestCase):
    """A card with a tick in a box no row reads fails the build, once.

    It built at exit 0 and published the match as played. Measured by
    /tmp/t7/scratch/B1/build/season.py on the suite's own six-card season and a
    seventh card, won 6-2, 6-3, 2-6 and 6-1, written with row 1's home Forfeit
    label and box painted again 600pt lower, off the table, and ticked: exit 0,
    "all 7 card(s) scored, no errors", and the match on the page as four sets
    played, with the note the card has unticked. So it was with the tick in a
    square no label names, with no note.

    Refused, the card is one F4 error, which names the box the tick is in.
    Rejected: a warning, which leaves the match published as played.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        write_season_cards(cls.root / "data")
        written_with_a_box_more(cls.root / "data" / "Round4" / "stray.pdf",
                                round_four_card(), ticked=True)
        done = subprocess.run(
            (sys.executable, str(BUILD), "--generated", "2026-09-23T09:00:00"),
            cwd=cls.root, capture_output=True, text=True, timeout=TIMEOUT)
        cls.code = done.returncode
        cls.report = done.stdout + done.stderr
        cls.words = " ".join(cls.report.split())
        cls.matches = {
            match["id"]: match
            for path in sorted((cls.root / "site").rglob("season.json"))
            for match in json.loads(path.read_text(encoding="utf-8"))["matches"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_build_fails_and_names_the_card_and_the_box(self):
        self.assertNotIn(TRACEBACK, self.report)
        self.assertEqual(self.code, 1, self.report[-3000:])
        self.assertIn("data/Round4/stray.pdf: this card ticks a checkbox this "
                      "reader reads nothing from, so it would be read as if that "
                      "box were not ticked", self.words)
        self.assertIn("the tick is in a home Forfeit box in no set row, which no "
                      "row reads", self.words)

    def test_the_card_is_one_error(self):
        # One error under its rule, which the competition's own line counts as
        # the run's one refused card and not as an error of its own.
        self.assertRegex(self.report, r"(?m)^== 1 error =")
        self.assertRegex(self.report, r"problems\s+0 error\(s\), \d+ warning\(s\) "
                                      r"and the run's 1 refused card\(s\)")
        self.assertEqual(len(re.findall(r"^  F4$", self.report, re.M)), 1)

    def test_the_page_shows_no_reading_of_the_card(self):
        self.assertIn("2000001", self.matches)  # the premise: the season is there
        self.assertNotIn("2000099", self.matches)


# A rect as cardwriter paints one, `x y w h re f`, with its four numbers kept.
_RECT = rb"(?<![\d.])(-?\d+\.\d+) (-?\d+\.\d+) (\d+\.\d+) (\d+\.\d+) re f"

_PAINT_RECT = cw._Painter.rect


def _repainted(raw: bytes, paint) -> bytes:
    """The same card with each rect painted as ``paint(x, y, w, h)`` says.

    ``paint`` answers ``b"S"`` to outline the rect, ``b"f"`` to leave it filled or
    ``b"n"`` to paint nothing. Each is one byte for the one byte of ``f``, so every
    offset in the file still points where it did and the only thing that differs
    is how each rect is painted. Raises if the card paints nothing to rewrite, for
    the reason :func:`_mangle` does.
    """
    def repaint(match: re.Match[bytes]) -> bytes:
        return match[0][:-1] + paint(*(float(n) for n in match.groups()))

    changed, count = re.subn(_RECT, repaint, raw)
    if not count or count != raw.count(b" re f"):
        raise AssertionError(f"rewrote {count} of the card's "
                             f"{raw.count(b' re f')} filled rects, so this "
                             f"fixture would not be the card it claims to be")
    return changed


def _in_stroked_lines(painter, x, y, w, h, shade=(0.0, 0.0, 0.0)):
    """cardwriter's rect, except that a hairline becomes a stroked line.

    A line down the middle of the hairline, as thick as the hairline is, which is
    the other way a table grid is drawn -- and which ``pdf.py`` does not record,
    since it keeps rectangles only. Rendered (/tmp probe, pdftoppm at 144dpi in
    grey), a card painted this way differs from the card it was drawn from in 86
    of 2,003,960 pixels, and in none of them by more than a quarter of the scale.
    """
    _PAINT_RECT(painter, x, y, w, h, shade)
    if min(w, h) < 1.5:
        x0, y0, w0, h0 = (float(n) for n in painter.ops[-1].split()[4:8])
        if w0 < h0:
            line = f"{x0 + w0 / 2:.2f} {y0:.2f} m {x0 + w0 / 2:.2f} {y0 + h0:.2f} l"
        else:
            line = f"{x0:.2f} {y0 + h0 / 2:.2f} m {x0 + w0:.2f} {y0 + h0 / 2:.2f} l"
        painter.ops[-1] = f"{min(w0, h0):.2f} w {line} S"


class TestAStrokedTableIsRefusedForHowItIsDrawnAndNotAsAStranger(HostileCase):
    """A card whose table rules are outlined rather than filled is told so.

    Measured on a genuine card with every ``re f`` rewritten to ``re S``: the same
    200 rects at the same places, every one recorded by the reader with ``filled``
    false, and a refusal reading "the page paints no table rules, so it has no set
    table and is not a scorecard" -- which a club reads as "this is not the file
    you exported from Match Centre", the one thing about the file that was
    measurably untrue. The doctor, run on it next, found eight column rules over
    four row bands and then printed the same sentence as its verdict. A card whose
    hairlines are stroked lines rather than rects was told the same, and renders
    the same as the card it was drawn from.

    It is still refused. Reading it was measured and rejected, because this rewrite
    is not the same card painted another way: rasterised (/tmp probe, pdftoppm at
    144dpi), a tick's interior is 100% dark on the genuine card and 0% on the
    rewritten one, since an outlined square over a box is the box drawn again. On a
    card with a forfeited set, a reader taking every outline as a fill reads the
    forfeit and the winner tick the page no longer shows, one taking only the thin
    outlines as rules reads the forfeit as a played 0-0, and one taking only the
    rules gets as far as the checkboxes and refuses there instead, with a second
    sentence that is false about this page. No real card has arrived drawn this
    way, and the one real export measured paints its rules and its tick as fills.

    The outlines are taken for a table by the rule the reader's own rules are:
    the largest run of line on the page, held to the same minimum of rules, so
    that one thin outline on a rules sheet is not a table and a card whose filled
    table outweighs a stray outlined run is read exactly as before.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        raw = cls.genuine[0].read_bytes()
        cls.rects = raw.count(b" re f")
        folder = cls.root / "stroked"
        folder.mkdir()

        def write(name: str, paint) -> Path:
            (folder / name).write_bytes(_repainted(raw, paint))
            return folder / name

        cls.stroked = write("card.pdf", lambda x, y, w, h: b"S")
        # The table's own rules outlined and nothing else, so the box edges are
        # still filled rects of a rule's shape and the reader's own rules, taken
        # alone, are theirs.
        cls.table_only = write("table-only.pdf", lambda x, y, w, h:
                               b"S" if w < 2 and h > 20 else b"f")
        # The ten short uprights round the TOTAL row outlined, four rules of one
        # height at four places, and a filled table that outweighs them.
        cls.total_only = write("total-only.pdf", lambda x, y, w, h:
                               b"S" if w < 2 and 10 < h < 30 else b"f")
        # The table's rules at their first three places, and then four, outlined
        # and alone on the page: one short of the minimum a table needs, and it.
        places = sorted({float(m[1]) for m in re.finditer(_RECT, raw)
                         if float(m[3]) < 2 and float(m[4]) > 40})
        cls.three, cls.four = (
            write(f"{len(kept)}-rules.pdf", lambda x, y, w, h, kept=kept:
                  b"S" if w < 2 and h > 40 and x in kept else b"n")
            for kept in (places[:3], places[:4]))
        with mock.patch.object(cw._Painter, "rect", _in_stroked_lines):
            cls.lines = write_season_cards(cls.root / "lines")[0]

    def refusal(self, path: Path):
        cards, rejections = scorecard.read(path)
        self.assertEqual(cards, [])
        rejection, = rejections
        return rejection

    def test_the_reason_names_the_painting_and_not_the_file(self):
        reason = self.refusal(self.stroked).reason
        self.assertEqual(reason, "the page paints its table rules as outlines "
                                 "rather than fills, which is not how Match "
                                 "Centre paints a card, so its set table is not "
                                 "read")

    def test_the_detail_counts_the_table_it_outlined_and_says_what_to_do(self):
        # 32 on paper: the table's 8 rules on each of its 4 rows. The other 70
        # outlines of a rule's shape -- both sides of the 30 checkboxes and the 10
        # uprights round the TOTAL row -- are shorter runs, and not the table.
        detail = self.refusal(self.stroked).detail
        self.assertIn(f"32 outlined rect(s) make 8 column rule(s) over 4 row "
                      f"band(s); 0 of the page's {self.rects} rects are filled",
                      detail)
        self.assertIn("if this is a scorecard, another program re-saved or "
                      "re-printed it after it was exported, so export it again "
                      "from Match Centre", detail)

    def test_a_table_outlined_among_filled_box_edges_is_told_the_same(self):
        # Before, the 60 filled box sides were the largest run of filled rules and
        # were taken for the table: "the set table's row bands are not
        # contiguous", a 39.2pt gap. 166 = 200 less the 32 rules and the 2
        # uprights of the TOTAL row tall enough to be outlined with them.
        rejection = self.refusal(self.table_only)
        self.assertTrue(rejection.reason.startswith(
            "the page paints its table rules as outlines"), rejection.reason)
        self.assertIn(f"32 outlined rect(s) make 8 column rule(s) over 4 row "
                      f"band(s); 166 of the page's {self.rects} rects are filled",
                      rejection.detail)

    def test_an_outlined_run_is_a_table_only_with_the_rules_a_table_needs(self):
        three = self.refusal(self.three)
        self.assertEqual(three.reason, "no table rules could be found on the "
                                       "page, so no set table was read")
        four = self.refusal(self.four)
        self.assertTrue(four.reason.startswith(
            "the page paints its table rules as outlines"), four.reason)
        self.assertIn("16 outlined rect(s) make 4 column rule(s) over 4 row "
                      "band(s); 0 of the page's 16 rects are filled", four.detail)

    def test_a_card_whose_filled_table_outweighs_its_outlines_reads_as_before(self):
        cards, rejections = scorecard.read(self.total_only)
        self.assertEqual(rejections, [])
        genuine, = scorecard.read(self.genuine[0])[0]
        self.assertEqual([_comparable(card) for card in cards],
                         [_comparable(genuine)])

    def test_a_card_drawn_in_stroked_lines_is_not_called_a_stranger_either(self):
        # pdf.py records the five fills this card still paints and none of its
        # lines, so the reader can say what it did not find and nothing more.
        rejection = self.refusal(self.lines)
        self.assertEqual(rejection.reason, "no table rules could be found on the "
                                           "page, so no set table was read")
        self.assertIn("among 5 rects; either this page is not a scorecard, or "
                      "another program re-saved it and drew its rules some other "
                      "way, and if it is one, export it again from Match Centre",
                      rejection.detail)

    def test_the_build_report_tells_the_club_the_same(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd = Path(tmp.name)
        (cwd / "data").mkdir()
        (cwd / "data" / "card.pdf").write_bytes(self.stroked.read_bytes())
        done = subprocess.run(
            (sys.executable, str(BUILD), "--generated", "2026-09-16T12:00:00"),
            cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
        report = " ".join((done.stdout + done.stderr).split())
        self.assertEqual(done.returncode, 1, report[-2000:])
        self.assertIn("the page paints its table rules as outlines", report)
        self.assertIn("export it again from Match Centre", report)
        self.assertNotIn("not a scorecard", report)

    def test_the_doctors_verdict_is_the_readers_and_calls_it_no_stranger(self):
        # Read with its line breaks folded, as the build report is above. The
        # doctor prints its causes in lines broken by hand, and a doctor finding its
        # rules the reader's way was measured (a mutant in /tmp/mutate82.py) to end
        # one line "Either this page is not a" and begin the next "scorecard".
        done = self.doctor(str(self.stroked))
        verdict = " ".join(done.stdout.split())
        self.assertEqual(done.returncode, 1)
        self.assertIn("This page was refused: the page paints its table rules as "
                      "outlines", verdict)
        self.assertNotIn("not a scorecard", verdict)

    def test_the_doctor_calls_no_stroked_square_a_tick(self):
        # Measured before: `8.07 x 8.07  1  stroked  ticked checkbox fill` in the
        # census, and seventeen lines below it `0 solid fill(s), 0 ticked`.
        done = self.doctor(str(self.stroked))
        self.assertNotIn("ticked checkbox fill", done.stdout)
        self.assertIn("0 solid fill(s), 0 ticked", done.stdout)
        # Past the width the census row goes on under what it is (4.186).
        lines = done.stdout.splitlines()
        at = next(at for at, line in enumerate(lines)
                  if "  stroked  box-sized square" in line)
        under = " " * lines[at].index("box-sized square")
        going_on = [line[len(under):] for line in lines[at + 1:at + 3]
                    if line.startswith(under)]
        self.assertEqual(" ".join([lines[at][len(under):], *going_on]),
                         "box-sized square, not a tick: its inside is not painted")

    def test_the_doctor_still_calls_a_filled_square_a_tick(self):
        done = self.doctor(str(self.genuine[0]))
        self.assertRegex(done.stdout, r"filled\s+ticked checkbox fill\n")
        self.assertIn("1 solid fill(s), 1 ticked", done.stdout)


if __name__ == "__main__":
    unittest.main()
