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
Measured over the corpus below: twelve of these files recover a genuine card
*unchanged*, because the reader trusts none of the bookkeeping they damage, and the
two that do produce a different card are both caught -- one by the printed TOTAL
row disagreeing with the set rows, one by having fewer set rows than the
competition's format has sets. Not one of them scores.

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
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from matchcentre import scorecard                                    # noqa: E402
from matchcentre.card import Card                                    # noqa: E402
from test_pipeline import write_season_cards                         # noqa: E402

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
        # The fractions matter: cut at 1% there is no object table at all, cut at
        # 95% everything is present except the trailer that says where to start,
        # and the ones between stop inside the page's content stream.
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
# The last two are worth reading twice. Reversing a card destroys it, but flipping
# one byte in the middle of its content stream costs a single checkbox and is
# reported as a warning; and a file holding two whole cards is read as the second
# one, which is what a PDF viewer shows too, because appending a document is how
# the format spells "revised".
RECOVERS_A_GENUINE_CARD = frozenset({
    "hostile-truncated-95pc",
    "hostile-startxref-past-eof",
    "hostile-startxref-negative",
    "hostile-no-xref",
    "hostile-no-trailer",
    "hostile-length-absurd",
    "hostile-length-negative",
    "hostile-page-count-lies",
    "hostile-root-object-gone",
    "hostile-nul-bytes-injected",
    "hostile-byte-flipped-mid-stream",
    "hostile-two-cards-concatenated",
})

# A *different* card comes back, and each is caught by a different one of the
# defences -- which is the argument for having more than one. Every name here is
# also a name that must never reach a ladder, which the build tests below check.
YIELDS_A_DIFFERENT_CARD = {
    # Half a card: three set rows instead of four, no printed TOTAL row, no
    # winner tick and no Players section. The verb still says DEFEATS and agrees
    # with 3-0 as readily as with 4-0, so the cross-checks alone would let this
    # through -- what stops it is the set count against the competition's format.
    "hostile-truncated-50pc": "set rows",
    # The set rows come from one card and the printed totals from another, so the
    # totals cross-check disagrees and rule F6 makes that an error.
    "hostile-spliced-cards": "totals cross-check",
}


def _comparable(card: Card) -> Card:
    """A card stripped of the two things that legitimately differ.

    ``source`` is the path it was read from, and warnings are *about* the damage
    rather than part of the transcription -- a warning is the reader saying so out
    loud, which is the behaviour wanted, not a difference in what was read.
    """
    return dataclasses.replace(card, source="", warnings=())


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
        classified = UNREADABLE | RECOVERS_A_GENUINE_CARD | set(YIELDS_A_DIFFERENT_CARD)
        self.assertEqual(sorted(classified), sorted(self.corpus),
                         "a file was added to the corpus without recording what "
                         "it does, or classified under a name that is not in it")
        self.assertEqual(len(classified),
                         len(UNREADABLE) + len(RECOVERS_A_GENUINE_CARD)
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
                         len(RECOVERS_A_GENUINE_CARD) + len(YIELDS_A_DIFFERENT_CARD))
        named = {Path(r.path).name for r in rejections}
        self.assertEqual(named, {f"{name}.pdf" for name in UNREADABLE},
                         "every file that produced no card must be reported by "
                         "path, and no other file should be (rule F4)")


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

    def test_a_half_read_card_never_claims_a_check_it_could_not_run(self):
        # Rule: absence of evidence is not evidence. Half the card is gone, so
        # two of the three cross-checks have nothing to run against, and each must
        # report itself unavailable rather than agreeing by default.
        card, = scorecard.read(self.paths["hostile-truncated-50pc"])[0]
        unavailable = {c.name for c in card.checks if not c.available}
        self.assertEqual(unavailable, {"totals", "winner-tick"})
        self.assertIsNone(card.totals.home_games)
        self.assertTrue(any("Players" in w for w in card.warnings),
                        "the whole Players section was truncated away, and a card "
                        "read without one must say so")


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
        code, report = self.build(self.project({"hostile-truncated-50pc"}))
        self.assertEqual(code, 2)
        self.assertIn("hostile-truncated-50pc.pdf", report)
        self.assertIn("do not agree on the shape of a match", report)

    def test_a_spliced_card_is_refused_by_its_own_printed_totals(self):
        # Same requirement, caught by a different defence: this card has the right
        # number of set rows, so only the TOTAL row printed on it gives the splice
        # away.
        code, report = self.build(self.project({"hostile-spliced-cards"}))
        self.assertNotEqual(code, 0)
        self.assertIn("cross-check", report)
        self.assertRegex(report, r"printed against|does not match|disagrees")

    def test_no_hostile_file_ever_reaches_a_published_ladder(self):
        # The end of the argument. Whatever any of these files parsed into, the
        # run either refused to write anything or exited nonzero -- so nothing
        # here can become a number on a page that a club believes.
        for name in sorted(set(YIELDS_A_DIFFERENT_CARD)):
            with self.subTest(file=name):
                code, _ = self.build(self.project({name}))
                self.assertNotEqual(code, 0, "a damaged card that parses must "
                                             "still fail the run")


if __name__ == "__main__":
    unittest.main()
