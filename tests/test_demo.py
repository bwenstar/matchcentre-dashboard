"""``tools/demo.py --clean`` — the flag that deletes scorecards.

The demo exists so that a fresh clone can show a working dashboard before a club has
exported anything, and ``--clean`` takes it away again. That is the dangerous half.
The directory it is pointed at is the same directory a club keeps its real cards in —
``data/`` is where the README tells them to put them — and the two kinds of file are
indistinguishable from outside: both are PDFs, both sit in per-round folders, both
were written by a program.

The sequence that loses a season is ordinary. A club runs the demo into ``data/`` to
see what the dashboard looks like, exports its own cards in beside it, and then reruns
with ``--clean`` to be rid of the invented club. Deleting every PDF under ``--out``
deletes their season, from a flag whose help says "remove a previous demo", and there
is no undo: the files came from Match Centre and going back for them means exporting
every night again by hand.

So the question every test here asks is the one the function's own docstring already
promised an answer to: *did this script write this file?* Nothing is inferred from a
file name, which a club may change, or from a manifest beside the cards, which a club
may delete. It is read off the letterhead printed on the page — the one thing on a
demo card that a real card cannot carry, because the club printing it does not exist.
"""

from __future__ import annotations

import collections
import contextlib
import datetime
import inspect
import io
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from matchcentre import format as format_mod                         # noqa: E402
import matchcentre                                                   # noqa: E402
from matchcentre import pasteable                                    # noqa: E402
from matchcentre import pdf                                          # noqa: E402
from matchcentre import scorecard                                    # noqa: E402
from matchcentre.cardwriter import NO_TICK                           # noqa: E402
from matchcentre.model import SetStatus                              # noqa: E402

import build                                                         # noqa: E402
import demo                                                          # noqa: E402
from test_careers import CareersPage                                 # noqa: E402
from test_cli import STAMP, readme_block                             # noqa: E402
from test_pipeline import write_season_cards                         # noqa: E402
from test_project_complete import git, under_git                     # noqa: E402

DEMO = ROOT / "tools" / "demo.py"
TIMEOUT = 600


def cards(directory: Path) -> set[Path]:
    return {p for p in directory.rglob("*.pdf") if p.is_file()}


class DemoCase(unittest.TestCase):
    """A club's real season, with a demo season written on top of it.

    The real cards come from the pipeline fixture, which prints the card writer's
    default letterhead -- a different invented club from the demo's, which is what
    makes this a test of telling two clubs apart rather than of counting files.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "data"
        write_season_cards(self.out)
        self.real = cards(self.out)
        self.run_demo("--out", str(self.out))
        self.demo_cards = cards(self.out) - self.real

    def run_demo(self, *argv: str) -> str:
        done = subprocess.run((sys.executable, str(DEMO), *argv),
                              capture_output=True, text=True, timeout=TIMEOUT)
        if done.returncode != 0:
            self.fail(f"demo.py {' '.join(argv)} exited {done.returncode}:\n"
                      f"{done.stdout}{done.stderr}")
        return done.stdout + done.stderr


class TestCleanRemovesTheDemoAndNothingElse(DemoCase):

    def test_the_two_seasons_are_side_by_side_to_begin_with(self):
        # The pre-flight. Without this, every assertion below could pass against a
        # directory that never held a real card.
        self.assertTrue(self.real, "the fixture wrote no cards")
        self.assertTrue(self.demo_cards, "tools/demo.py wrote no cards")
        self.assertEqual(cards(self.out), self.real | self.demo_cards)

    def test_a_real_scorecard_survives_the_flag(self):
        demo.clean(self.out)
        for path in sorted(self.real):
            with self.subTest(card=path.name):
                self.assertTrue(path.exists(),
                                f"--clean deleted {path.name}, which came from "
                                f"Match Centre and cannot be got back")

    def test_every_card_the_demo_wrote_is_removed(self):
        result = demo.clean(self.out)
        for path in sorted(self.demo_cards):
            with self.subTest(card=path.name):
                self.assertFalse(path.exists())
        self.assertEqual(set(result.removed), self.demo_cards)

    def test_the_cards_it_would_not_touch_are_reported_as_scorecards(self):
        # Not folded into a count, and not called "not a PDF" -- they are PDFs, and
        # they are exactly the files a club would want to hear had been recognised.
        result = demo.clean(self.out)
        self.assertEqual(set(result.foreign), self.real)

    def test_the_run_says_what_it_kept_and_that_it_was_not_the_demos(self):
        text = self.run_demo("--clean", "--out", str(self.out))
        self.assertIn(str(len(self.demo_cards)), text)
        one = sorted(self.real)[0]
        self.assertIn(one.name, text)
        self.assertIn("not written by", text)

    def test_it_does_not_tell_a_club_to_delete_a_folder_holding_its_own_cards(self):
        # The last line of every run used to read "to remove them, delete <out>".
        # Sound advice for a folder holding only the demo, and a season lost for
        # anyone who pointed --out at data/ -- the same mistake as the flag itself,
        # in an instruction instead of in code.
        text = self.run_demo("--out", str(self.out))
        self.assertNotIn(f"delete {self.out}", text)
        self.assertIn("not its to touch", text)

    def test_it_still_names_the_folder_when_emptying_it_would_cost_nothing(self):
        alone = Path(self._tmp.name) / "nothing-else-here"
        text = self.run_demo("--out", str(alone))
        self.assertIn(f"delete {alone}", text)

    def test_a_pdf_it_cannot_read_is_left_alone(self):
        # The safe answer to "is this mine?" is no. A PDF this script cannot parse
        # is a PDF it knows nothing about, and half a download of a club's card is
        # more likely than a corrupted demo -- the demo can always be written again.
        broken = self.out / "half-a-download.pdf"
        broken.write_bytes(b"%PDF-1.4 and then the connection dropped")
        result = demo.clean(self.out)
        self.assertTrue(broken.exists())
        self.assertIn(broken, result.foreign)

    def test_a_file_that_is_not_a_pdf_is_left_alone(self):
        note = self.out / "byes-from-the-association.txt"
        note.write_text("Round 3: Magpies\n", encoding="utf-8")
        result = demo.clean(self.out)
        self.assertTrue(note.exists())
        self.assertIn(note, result.other)
        self.assertNotIn(note, result.foreign)

    def test_a_directory_holding_only_the_demo_is_removed_with_it(self):
        # The other half of --clean: run into an empty directory, the demo should
        # leave no trace of itself, including the folders it made.
        alone = Path(self._tmp.name) / "just-the-demo"
        self.run_demo("--out", str(alone))
        self.assertTrue(cards(alone))
        demo.clean(alone)
        self.assertFalse(alone.exists(), f"{alone} was left behind empty")

    def test_a_directory_holding_a_real_card_is_kept(self):
        demo.clean(self.out)
        self.assertTrue(self.out.is_dir())
        for path in sorted(self.real):
            with self.subTest(card=path.name):
                self.assertTrue(path.parent.is_dir())


class TestWhatCountsAsThisScriptsOwnCard(DemoCase):
    """``is_demo_card`` on its own, which is where the whole promise rests."""

    def test_it_recognises_a_card_it_wrote(self):
        for path in sorted(self.demo_cards):
            with self.subTest(card=path.name):
                self.assertTrue(demo.is_demo_card(path))

    def test_another_clubs_card_is_not_one_of_its_own(self):
        for path in sorted(self.real):
            with self.subTest(card=path.name):
                self.assertFalse(demo.is_demo_card(path))

    def test_it_reads_the_page_and_not_the_file_name(self):
        # A club that renamed its exports, or that files them under names of its own,
        # is the ordinary case rather than the awkward one.
        renamed = sorted(self.demo_cards)[0].rename(
            self.out / "Round1" / "brindle-vs-someone.pdf")
        self.assertTrue(demo.is_demo_card(renamed))
        real = sorted(self.real)[0]
        disguised = real.rename(real.parent / "demo-season-round-1.pdf")
        self.assertFalse(demo.is_demo_card(disguised))

    def test_part_of_a_matching_letterhead_is_not_enough(self):
        # "TENNIS CLUB INC" is how the demo's letterhead ends, and it is also how a
        # great many real clubs end their own name -- the fixture's invented club
        # among them. Recognising a card by any fragment of the letterhead rather
        # than the whole of it would take those clubs' seasons along with the demo.
        real = sorted(self.real)[0]
        mine = sorted(self.demo_cards)[0]
        theirs, ours = self.letterhead(real), self.letterhead(mine)
        self.assertEqual(ours, demo.LETTERHEAD)
        self.assertNotEqual(theirs, ours)
        tail = "TENNIS CLUB INC"
        self.assertTrue(theirs.endswith(tail) and ours.endswith(tail),
                        f"the two letterheads no longer share an ending, so this "
                        f"test proves nothing: {theirs!r} and {ours!r}")
        self.assertFalse(demo.is_demo_card(real))

    @staticmethod
    def letterhead(path: Path) -> str:
        card, = scorecard.read(path)[0]
        return " ".join(card.club_hint.split()).upper()


class TestTheCommandItPrintsNextRunsFromWhereItWasRun(unittest.TestCase):
    """`next: python3 build.py` — true from the project, false everywhere else.

    The cards go next to ``build.py`` by default, wherever the club is standing when
    they run this; the command printed under them names ``build.py`` with no path at
    all. From a home directory -- which is where a desktop shortcut, a pasted README
    line or a cron entry runs from -- that is fifteen scorecards written into the
    project and a command that answers `can't open file 'build.py'`, or, on a machine
    with a second checkout, silently builds the wrong one.

    Nothing here runs the demo with its default ``--out``: that writes into the
    project's own ``data/``, which ships empty and is tested for it, so a test that
    left cards behind would fail a different test in another file.
    """

    def next_line(self, cwd: Path, *argv: str) -> str:
        done = subprocess.run((sys.executable, str(DEMO), *argv), cwd=cwd,
                              capture_output=True, text=True, timeout=TIMEOUT)
        if done.returncode != 0:
            self.fail(f"demo.py exited {done.returncode}:\n{done.stdout}"
                      f"{done.stderr}")
        printed = [line for line in done.stdout.splitlines()
                   if line.startswith("next: ")]
        self.assertEqual(len(printed), 1, f"no single next: line in {done.stdout}")
        return printed[0]

    def test_run_from_somewhere_else_it_says_where_to_run_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Resolved, as the demo names it: on a Mac the temporary folder is
            # under /var, which is a link to /private/var.
            out = Path(tmp).resolve() / "cards"
            line = self.next_line(Path(tmp), "--out", str(out), "--teams", "4",
                                  "--rounds", "1")
            self.assertEqual(line, f"next: cd {pasteable(str(ROOT))} && python3 "
                                   f"build.py --data {pasteable(str(out))}")

    def test_run_from_the_project_it_says_nothing_of_the_kind(self):
        # The ordinary case, and the one that has to stay a single command: a `cd` to
        # where the club already is reads as though they had got something wrong.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve() / "cards"
            line = self.next_line(ROOT, "--out", str(out), "--teams", "4",
                                  "--rounds", "1")
            self.assertEqual(line, f"next: python3 build.py --data "
                                   f"{pasteable(str(out))}")

    def test_the_default_out_is_the_case_that_needed_saying(self):
        """The demo's own default: cards in the project, command with no path.

        Asked of the function rather than of a run, because the run that proves it
        is the one that writes fifteen cards into ``data/``. The command is the
        whole of what a club is given here -- there is no `--data` in it to notice
        is absolute -- so a bare one is unrunnable anywhere but the project.
        """
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(
                (sys.executable, "-c", "import demo; print(demo.next_command("
                                       "demo.DEFAULT_OUT))"),
                cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "tools")})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(),
                             f"cd {pasteable(str(ROOT))} && python3 build.py")


@unittest.skipIf(os.name == "nt", "the pasted line is run by a POSIX shell")
class TestTheCommandItPrintsNextWorksPastedWhereTheClubStood(unittest.TestCase):
    """Every `next:` line, run by the shell from the folder the demo was run in.

    The class above says where the ``cd`` goes and never ran the line. Run, a
    relative ``--out`` from outside the project named the folder the demo wrote into
    and not the one the build reads: `--out relseason` from another folder printed
    `cd <project> && python3 build.py --data relseason`, and pasted, the build looked
    for ``relseason`` inside the project and answered `error: no such directory:
    relseason`, then advised copying in the PDFs just written. Nothing was quoted
    either, so a path with a space was two arguments. `--out "our season"` pasted to
    `unrecognized arguments: season`, and a project under ``my club`` to `can't cd
    to .../my`. Measured on a copy of the tree before, six of the eleven lines failed
    when pasted (/tmp/t4/scratch/d448/measure.before.txt).

    So the folder is resolved against the working directory before the line is
    built, and both paths are quoted by the build's own rule. A folder inside the
    project is named from the project, as the default is, and one outside it in
    full. Each test has its own copy of the program under a folder with a space in
    it, because the pasted line runs a build that writes ``site/`` and
    ``config.toml`` into the project it goes to. That must not be this one. And the
    ``python3`` the line names is, on the ``PATH`` it is pasted under, the Python
    running these tests, which the one a machine calls ``python3`` need not be.

    Rejected: checking the printed line alone, which is how the line was pinned
    before and how it stayed unrunnable. Rejected too: ``shlex.quote``, a second
    rule beside the build's, which quotes a quote as `'"'"'` where the build writes
    `'\\''`, so the same folder would come out two ways.

    And quoted so that nothing inside is live. Double quotes kept a space, an
    apostrophe, ``;`` and ``&`` inside one word, and not ``$``, a backtick, ``"`` or
    ``\\``, which a POSIX shell still reads there, nor the ``!`` an interactive bash
    reads. Measured on a copy of the tree before (/tmp/t6/scratch/m12/before.txt),
    with `x=EXPANDED` set, `--out 'o$x'` from outside the project pasted to `error:
    no such directory: .../oEXPANDED`, ``back`echo`tick`` to `.../backtick`, `a"b`
    and `bs\\` to `Syntax error: Unterminated quoted string`, and `club!2026`, which
    ran under ``sh -c``, to `bash: !2026: event not found`. Single quotes, with
    `'\\''` for a quote inside, leave nothing for either shell to read. Rejected: a
    backslash before each of the four inside double quotes, which leaves the ``!``,
    since no escape of it works there: `"club\\!2026"` is `club\\!2026` to bash and
    to ``sh`` alike (/tmp/t6/scratch/m12/bang.txt).
    """

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="demo-next-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        self.project = copy_of_the_program(self.tmp / "our club" /
                                           "matchcentre-dashboard")
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()
        self.env = with_this_python(self.tmp / "bin",
                                    {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def printed(self, cwd: Path, *argv: str) -> str:
        """The copy's demo, two cards of it, and the one line it says to run next."""
        done = subprocess.run((sys.executable, str(self.project / "tools" / "demo.py"),
                               "--teams", "4", "--rounds", "1", *argv), cwd=cwd,
                              capture_output=True, text=True, timeout=TIMEOUT,
                              env=self.env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line[len("next: "):] for line in done.stdout.splitlines()
                 if line.startswith("next: ")]
        self.assertEqual(len(lines), 1, done.stdout)
        return lines[0]

    def pasted(self, command: str, cwd: Path) -> None:
        """The line, run by the shell from where the club was standing."""
        done = subprocess.run(("sh", "-c", command), cwd=cwd, capture_output=True,
                              text=True, timeout=TIMEOUT, env=self.env)
        self.assertEqual(done.returncode, 0,
                         f"{command}\n{done.stdout[-1500:]}{done.stderr}")
        self.assertIn("exit 0: all 2 card(s) scored, no errors", done.stdout)

    def test_a_folder_named_with_what_a_shell_reads_in_double_quotes_arrives(self):
        # With `x` set, so that a `$x` the shell reads is a folder that exists
        # nowhere rather than an empty word the build might yet find.
        self.env["x"] = "EXPANDED"
        for name in ("o$x", "back`echo`tick", 'a"b', "bs\\"):
            with self.subTest(name=name):
                command = self.printed(self.elsewhere, "--out", name)
                self.assertEqual(command, f"cd '{self.project}' && python3 build.py "
                                          f"--data '{self.elsewhere / name}'")
                self.pasted(command, self.elsewhere)

    @unittest.skipUnless(shutil.which("bash"), "there is no bash to paste into")
    def test_a_folder_named_with_a_bang_is_not_history_to_an_interactive_bash(self):
        # Typed at a prompt, not run as a script: history expansion is on only in
        # an interactive shell, which is what a club pastes into.
        command = self.printed(self.elsewhere, "--out", "club!2026")
        done = subprocess.run(("bash", "--norc", "-i"), input=command + "\n",
                              cwd=self.elsewhere, capture_output=True, text=True,
                              timeout=TIMEOUT, env=self.env)
        self.assertNotIn("event not found", done.stderr)
        self.assertIn("exit 0: all 2 card(s) scored, no errors", done.stdout,
                      f"{command}\n{done.stdout[-1500:]}{done.stderr}")

    def test_a_folder_named_from_somewhere_else_is_the_folder_the_build_reads(self):
        # In a copy under a folder with no space in its name, so that the `cd` needs
        # no quotes and what is left to be wrong is the folder: named as it was
        # typed, it was `--data season`, which the build looked for in the project.
        self.project = copy_of_the_program(self.tmp / "plain" /
                                           "matchcentre-dashboard")
        command = self.printed(self.elsewhere, "--out", "season")
        self.assertEqual(command, f"cd {pasteable(str(self.project))} && python3 "
                                  f"build.py --data "
                                  f"{pasteable(str(self.elsewhere / 'season'))}")
        self.pasted(command, self.elsewhere)

    def test_a_folder_with_a_space_in_its_name_is_one_word_to_the_shell(self):
        command = self.printed(self.elsewhere, "--out", "our season")
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py "
                                  f"--data '{self.elsewhere / 'our season'}'")
        self.pasted(command, self.elsewhere)

    def test_an_apostrophe_in_a_folder_s_name_is_quoted_as_a_space_is(self):
        # Unquoted, the shell reads the apostrophe as the start of a quotation that
        # never ends: `Syntax error: Unterminated quoted string`. Inside single
        # quotes it ends the quotation, is given as `\\'`, and starts another.
        command = self.printed(self.elsewhere, "--out", "vasse's")
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py "
                                  f"--data '{self.elsewhere}/vasse'\\''s'")
        self.pasted(command, self.elsewhere)

    def test_the_default_folder_from_somewhere_else_is_a_cd_that_arrives(self):
        # Into the copy's own data/, which is the copy's to fill.
        command = self.printed(self.elsewhere)
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py")
        self.pasted(command, self.elsewhere)

    def test_a_folder_named_from_inside_the_project_is_named_from_the_project(self):
        # Standing in tools/, where `python3 demo.py` is typed, is not standing in
        # the project, and the folder is not where the build will be standing.
        tools = self.project / "tools"
        command = self.printed(tools, "--out", "new season")
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py "
                                  f"--data 'tools/new season'")
        self.pasted(command, tools)

    def test_a_full_path_into_the_project_is_named_as_the_default_is(self):
        command = self.printed(self.elsewhere, "--out", str(self.project / "cards"))
        self.assertEqual(command,
                         f"cd '{self.project}' && python3 build.py --data cards")
        self.pasted(command, self.elsewhere)

    def test_a_folder_in_the_project_named_with_a_dash_first_is_not_a_flag(self):
        # Named in full it began with a /, and named from the project it began with
        # the dash, which the build read as a flag: `--data -cards` pasted to
        # `argument --data: expected one argument`.
        command = self.printed(self.elsewhere, "--out", str(self.project / "-cards"))
        self.assertEqual(command,
                         f"cd '{self.project}' && python3 build.py --data ./-cards")
        self.pasted(command, self.elsewhere)

    def test_a_folder_beside_data_whose_name_begins_with_data_is_not_data(self):
        # `data-old` is not in data/, however its name begins: taken for it, the
        # line is the bare command, and the build reads the empty data/ instead.
        command = self.printed(self.project, "--out", "data-old")
        self.assertEqual(command, "python3 build.py --data data-old")
        self.pasted(command, self.project)

    def test_from_the_project_itself_it_is_still_one_command(self):
        command = self.printed(self.project, "--out", "cards")
        self.assertEqual(command, "python3 build.py --data cards")
        self.pasted(command, self.project)

    def test_a_folder_climbing_out_of_the_project_is_named_in_full(self):
        # `../beside` from the project was printed as typed and worked, pasted from
        # there. It is outside the project, so it is named as every folder outside
        # the project is: the club who keeps the line has a folder it can find.
        command = self.printed(self.project, "--out", "../beside")
        self.assertEqual(command, f"python3 build.py --data "
                                  f"'{self.project.parent / 'beside'}'")
        self.pasted(command, self.project)

    def test_a_full_path_climbing_out_of_the_project_is_named_in_full_too(self):
        # The `..` is folded away whichever way the folder was typed. Kept, the
        # path reads as inside the project, and the line says `--data ../beside`.
        command = self.printed(self.elsewhere, "--out",
                               f"{self.project}{os.sep}..{os.sep}beside")
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py --data "
                                  f"'{self.project.parent / 'beside'}'")
        self.pasted(command, self.elsewhere)

    def test_a_folder_beside_the_project_named_like_it_is_outside_it(self):
        # A folder is inside the project by its path's parts, not by how its name
        # begins: read as inside, `<project>-cards` came out as `--data cards`.
        command = self.printed(self.elsewhere, "--out", f"{self.project}-cards")
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py "
                                  f"--data '{self.project}-cards'")
        self.pasted(command, self.elsewhere)

    def test_both_paths_are_quoted_by_the_function_the_build_quotes_with(self):
        """One rule for a path to paste, the build's, and not a copy of it.

        Asked in this process with the rule swapped for one that marks what it was
        handed, because a copy of the rule prints the same line as the rule until
        the day one of the two is changed. ``ROOT`` is swapped as well, for a folder
        this process is not standing in, so that the ``cd`` is always there.

        And the folder handed to ``--data`` is named by the function the build's
        hints name the folders of their flags with, ``pasteable_path``, which quotes
        by the same rule: swapped where that function finds it, the rule marks the
        folder, ``./`` and all.
        """
        self.assertIs(demo.pasteable, pasteable)
        self.assertIs(build._quote, pasteable)
        self.assertIs(demo.pasteable_path, matchcentre.pasteable_path)
        self.assertIs(build.pasteable_path, matchcentre.pasteable_path)
        marked = mock.Mock(side_effect=lambda path: f"<{path}>")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with mock.patch.object(demo, "ROOT", root), \
                    mock.patch.object(demo, "pasteable", marked), \
                    mock.patch.object(matchcentre, "pasteable", marked):
                inside = demo.next_command(root / "-cards")
                outside = demo.next_command(root.parent / "season")
        self.assertEqual(inside, f"cd <{root}> && python3 build.py --data <./-cards>")
        self.assertEqual(outside, f"cd <{root}> && python3 build.py "
                                  f"--data <{root.parent / 'season'}>")

    def test_whether_the_folder_is_data_s_is_asked_of_the_function_fetch_asks(self):
        """One answer to "is this folder inside ``data/``?", and not two rules.

        ``tools/fetch.py`` gives its note by ``within_data``, and this line leaves
        out its ``--data`` by it. Asked with that function swapped for one that
        says yes to everything, so that the line dropping its ``--data`` for a
        folder outside the project shows whose answer it was printed from: a
        second rule of its own, ``resolve`` and ``is_relative_to`` written out
        again, printed the same line for every folder the other tests try, and
        the two answered a link differently once already.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with mock.patch.object(demo, "ROOT", root), \
                    mock.patch.object(demo, "within_data", return_value=Path(".")):
                command = demo.next_command(root.parent / "season")
        self.assertEqual(command, f"cd {pasteable(str(root))} && python3 build.py")


def copy_of_the_program(project: Path) -> Path:
    """This checkout's program, copied to *project*, with an empty ``data/``.

    For a test that runs the line a tool prints: the build that line runs writes
    ``site/`` and ``config.toml`` into the project it goes to, and that must not be
    this one.
    """
    project.mkdir(parents=True)
    shutil.copy2(ROOT / "build.py", project / "build.py")
    for folder in ("matchcentre", "tools"):
        shutil.copytree(ROOT / folder, project / folder,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (project / "data").mkdir()
    return project


def with_this_python(folder: Path, env: dict[str, str]) -> dict[str, str]:
    """*env* with a ``python3`` first on its ``PATH`` that is this Python.

    The pasted line says ``python3``, as a club types it, and the one a machine
    finds by that name may be older than this project runs on, which would fail
    the line for a reason that is not in it. A script and not a link, so that a
    Python in a virtual environment is still that environment's.
    """
    folder.mkdir()
    shim = folder / "python3"
    shim.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    shim.chmod(0o755)
    return {**env, "PATH": f"{folder}{os.pathsep}{env.get('PATH', '')}"}


class TestTheWashoutTheDemoWritesIsTheOneWorthShipping(unittest.TestCase):
    """The rained-off night on the demo card, which used to be all zeros.

    An all-zero washout is the harmless shape of this fixture. Nothing on the card
    looks like a result, so nothing on the dashboard can mislead anybody, and the
    branch it exercises is the easy one. The shape that costs a reader something is
    the night that stopped partway: two rubbers finished, the rest abandoned, and a
    card left carrying 12-5 and "sets 2-0" that reads like a two-rubber win while
    being counted in nothing at all.

    That is the case the README describes and the case the dashboard has words for,
    and until this test the demo could not show it -- which mattered because the demo
    is the only washout most people reading this repo will ever see. So what is pinned
    here is the fixture's evidential value, not its arithmetic.

    Nothing here runs the demo: these are the functions that decide the card, called
    directly. The end-to-end run is test_cli's business and takes a minute.
    """

    FMT = format_mod.match_format(demo.DEFAULT_FORMAT)

    def rows(self, fmt=None):
        return demo.score_rows(demo.WASHOUT, fmt or self.FMT,
                               random.Random(20260910))

    def test_some_rubbers_finished_before_the_night_was_called_off(self):
        finished = [pair for pair in self.rows() if pair != (0, 0)]
        self.assertEqual(len(finished), 2,
                         f"the demo's washout shows {len(finished)} finished "
                         f"rubber(s), so the card it writes is not the case this "
                         f"fixture exists for")

    def test_the_finished_ones_come_first_because_that_is_when_the_rain_came(self):
        # A card with a blank row above a completed one is not a night that stopped,
        # it is a night somebody recorded out of order -- a different defect, and one
        # the reader would be right to report.
        pairs = self.rows()
        blanks = [i for i, pair in enumerate(pairs) if pair == (0, 0)]
        self.assertEqual(blanks, list(range(len(pairs) - len(blanks), len(pairs))),
                         f"the abandoned rubbers are not the last ones: {pairs}")

    def test_the_scoreline_reads_like_a_result_which_is_the_whole_point(self):
        # Level on games or on sets and there is nothing to misread, so there would be
        # nothing for the dashboard to correct.
        pairs = self.rows()
        home = sum(h for h, _ in pairs)
        away = sum(a for _, a in pairs)
        sets_home = sum(1 for h, a in pairs if h > a)
        sets_away = sum(1 for h, a in pairs if a > h)
        self.assertGreater(home, away, f"the card's games are not a lead: {pairs}")
        self.assertGreater(sets_home, sets_away,
                           f"the card's sets are not a lead: {pairs}")

    def test_the_card_still_cannot_name_a_winner(self):
        # The line this fixture must not cross. Enough finished rubbers and the card
        # ticks a winner and prints a verb (`decided`), and a cancelled card that
        # names a winner is a different fixture from the one being shipped -- an
        # abandoned night nobody won. Checked for every format the tool ships, since
        # `min_sets_for_result` is a config field and a club may set it to anything.
        for name in sorted(demo.FORMATS):
            with self.subTest(format=name):
                fmt = format_mod.match_format(name)
                self.assertLess(demo.washout_completed(fmt),
                                fmt.min_sets_for_result,
                                f"{name}: the washout finishes enough rubbers to "
                                f"decide the match, so the demo would write a "
                                f"cancelled card with a winner ticked on it")

    def test_a_format_with_no_room_to_show_it_gets_the_all_zero_card_instead(self):
        # A single-rubber format cannot have a finished rubber and an abandoned one,
        # and the answer to that is the card that shows nothing rather than a card
        # that shows a decided night marked cancelled.
        one = format_mod.from_rows([False])
        self.assertEqual(demo.washout_completed(one), 0)
        self.assertEqual(self.rows(one), [(0, 0)])

    def test_the_finished_rubbers_are_the_ones_the_card_ticks_as_played(self):
        # The games and the status column are written from two different places in
        # `build_card`, through one count, and this is the pairing that count exists
        # for: a row with games on it and `Incomplete` in its status column is a
        # contradiction on the page, whichever half is wrong.
        spec, notes = self.card()
        played = [row for row in spec.rows if row.status is SetStatus.PLAYED]
        blank = [row for row in spec.rows if row.status is not SetStatus.PLAYED]
        self.assertEqual(len(played), demo.washout_completed(self.FMT))
        for row in played:
            self.assertNotEqual((row.home_games, row.away_games), (0, 0),
                                "a rubber ticked as played has no games on it")
        for row in blank:
            self.assertEqual((row.home_games, row.away_games), (0, 0),
                             "a rubber nobody finished has games on it")
        self.assertIn("washed out after 2 of 4 sets", notes,
                      f"the run report does not say what the card shows: {notes!r}")

    def test_the_card_it_writes_is_marked_cancelled_and_ticks_nobody(self):
        spec, _ = self.card()
        self.assertTrue(spec.cancelled)
        self.assertEqual(spec.verb, "")
        self.assertEqual(spec.winner, NO_TICK)

    def card(self):
        """One washout card's spec, built the way the demo builds it."""
        rng = random.Random(20260910)
        teams = demo.make_teams(2, rng)
        fixture = demo.Fixture(round_index=4, round_label="5",
                               date=datetime.date(2026, 3, 4), court=1,
                               home=teams[0], away=teams[1],
                               flavour=demo.WASHOUT, match_id="4200012",
                               season=demo.SEASON_LABEL)
        return demo.build_card(fixture, self.FMT, "3", "Wednesday", rng)


class TestTheDemosNightTheClockStoppedIsLevelOnGamesAndWonOnItsSets(unittest.TestCase):
    """The seventh special: a last rubber the clock stopped, with no box ticked.

    Row 4.425. A club whose play stops at a fixed hour leaves its last rubber where
    it stood, 5-4 or 4-2, ticks nothing, and gives a night level on games to the
    side with more finished sets. The reader failed that card, F6 on the verb and
    F6 on the tick, and the demo never wrote one, so no demo build showed it. Of
    24000 cards over 1600 seasons, every format, 2 to 9 teams and seeds 0 to 19
    (/tmp/mcf/scratch/F6/probe/demo_sweep.py on the tree before this), 0 had a
    played rubber neither level nor finished: the demo's stopped rubbers were the
    retired set, its box ticked, and the washout's, ticked Incomplete.

    So the demo writes one. The last rubber is left at ``unfinished_pair`` with no
    box, the home side leading it, and the finished rubbers go one more to the away
    side, closest margins first, so the games are level and the card is the away
    side's on sets: on the plain demo, round 2's second card, 5-6, 6-5, 5-6 and 5-4.
    Its rows are drawn as an ordinary card's are and then set aside, so every card
    after it is the card it was.

    Rejected: rows drawn from the generator, which vary with the seed and change
    every later card of the season, and the README samples with them. Rejected: an
    ordinary card with its last rubber left unfinished, the retired set without its
    box, whose games mostly back the side its rows favour, so it would not be the
    card the reader failed.
    """

    FMT = format_mod.match_format(demo.DEFAULT_FORMAT)

    def card(self, fmt=None):
        """One such card's spec, built the way the demo builds it."""
        rng = random.Random(20260910)
        teams = demo.make_teams(2, rng)
        fixture = demo.Fixture(round_index=1, round_label="2",
                               date=datetime.date(2026, 2, 11), court=2,
                               home=teams[0], away=teams[1],
                               flavour=demo.OUT_OF_TIME, match_id="4200004",
                               season=demo.SEASON_LABEL)
        return demo.build_card(fixture, fmt or self.FMT, "3", "Wednesday", rng)

    @staticmethod
    def games(pairs) -> tuple[int, int]:
        return sum(h for h, _ in pairs), sum(a for _, a in pairs)

    def test_its_last_rubber_stops_where_the_clock_stopped_it_with_no_box_ticked(self):
        spec, _ = self.card()
        rule = self.FMT.set_rule
        *finished, last = spec.rows
        self.assertEqual((last.home_games, last.away_games), demo.unfinished_pair(rule))
        self.assertIs(last.status, SetStatus.PLAYED)
        self.assertFalse(format_mod.completed(last.home_games, last.away_games, rule))
        for row in finished:
            self.assertIs(row.status, SetStatus.PLAYED)
            self.assertTrue(format_mod.completed(row.home_games, row.away_games, rule))

    def test_its_games_are_level_and_its_finished_sets_the_away_sides(self):
        spec, notes = self.card()
        pairs = [(row.home_games, row.away_games) for row in spec.rows]
        self.assertEqual(pairs, [(5, 6), (6, 5), (5, 6), (5, 4)])
        self.assertEqual(self.games(pairs), (21, 21))
        self.assertEqual(notes, "Doubles 2 ran out of court time at 5-4 with no box "
                                "ticked, leaving the games level at 21-21 and the "
                                "finished sets 1-2")
        # Three finished rubbers are enough to decide it, so the writer prints the
        # verb and ticks the side it decides for.
        self.assertEqual((spec.verb, spec.winner), (demo.AUTO, demo.AUTO))

    def test_the_card_it_writes_reads_back_agreeing_with_itself(self):
        spec, _ = self.card()
        with tempfile.TemporaryDirectory() as folder:
            path = demo.write_card_file(Path(folder) / "card.pdf", spec)
            card = scorecard.read_page(pdf.extract(path))
        self.assertEqual((card.verb, card.winner_tick), ("LOSES TO", "away"))
        self.assertEqual({check.name: check.agrees for check in card.checks},
                         {"totals": True, "verb": True, "winner-tick": True})

    def test_under_every_set_rule_the_card_reads_back_agreeing_with_itself(self):
        # Each rule's card is level on games and the away side's on its finished
        # sets, and the reader, which knows no rule, asks every rule: a reader
        # asking first-to-6 alone, or the rules of six games, failed the others.
        for name, rule in sorted(format_mod.SET_RULES.items()):
            with self.subTest(rule=name):
                spec, _ = self.card(format_mod.match_format(demo.DEFAULT_FORMAT,
                                                            rule=rule))
                pairs = [(row.home_games, row.away_games) for row in spec.rows]
                home, away = self.games(pairs)
                self.assertEqual(home, away, pairs)
                with tempfile.TemporaryDirectory() as folder:
                    path = demo.write_card_file(Path(folder) / "card.pdf", spec)
                    card = scorecard.read_page(pdf.extract(path))
                self.assertEqual((card.verb, card.winner_tick), ("LOSES TO", "away"))
                self.assertEqual(
                    {check.name: (check.agrees, check.held_to)
                     for check in card.checks},
                    {check: (True, "the set rows")
                     for check in ("totals", "verb", "winner-tick")})

    def test_every_format_stops_its_last_rubber_and_levels_the_games_where_it_can(self):
        # Three rubbers of first to 6, two finished, cannot make up the leader's
        # game, so those formats keep an ordinary card's finished rows.
        uneven = set()
        for name in sorted(demo.FORMATS):
            with self.subTest(format=name):
                fmt = format_mod.match_format(name)
                spec, notes = self.card(fmt)
                rule = fmt.set_rule
                pairs = [(row.home_games, row.away_games) for row in spec.rows]
                self.assertEqual(pairs[-1], demo.unfinished_pair(rule))
                self.assertTrue(all(format_mod.plausible(h, a, rule)
                                    for h, a in pairs), pairs)
                self.assertTrue(all(format_mod.completed(h, a, rule)
                                    for h, a in pairs[:-1]), pairs)
                self.assertTrue(all(row.status is SetStatus.PLAYED
                                    for row in spec.rows))
                home, away = self.games(pairs)
                if home != away:
                    uneven.add(name)
                    self.assertNotIn("level", notes)
                else:
                    self.assertGreater(sum(a > h for h, a in pairs[:-1]),
                                       sum(h > a for h, a in pairs[:-1]), pairs)
        self.assertEqual(uneven, {"doubles-3", "mixed-3", "singles-3"})

    def test_the_cards_after_it_are_the_cards_they_were(self):
        drawn = {}
        for flavour in (demo.NORMAL, demo.OUT_OF_TIME):
            rng = random.Random(20260818)
            demo.score_rows(flavour, self.FMT, rng)
            drawn[flavour] = rng.getstate()
        self.assertEqual(drawn[demo.OUT_OF_TIME], drawn[demo.NORMAL])

    def test_the_plain_demo_writes_it_once_as_round_2s_second_card(self):
        rng = random.Random(demo.DEFAULT_SEED)
        teams = demo.make_teams(6, rng)
        fixtures, _ = demo.build_fixtures(teams, len(demo.round_robin(6)), "Wednesday")
        found = [(f.round_label, f.court, f.home.slug, f.away.slug)
                 for f in fixtures if f.flavour == demo.OUT_OF_TIME]
        self.assertEqual(found, [("2", 2, "wombats", "bunyips")])


class TestTheDemosRetiredSetCardSaysWhoWonTheNightTheRetirementDecides(
        unittest.TestCase):
    """The retired-set card prints a verb and ticks a winner where it has a result.

    4.422: a set one side retired from is the other side's, so the demo's card
    with the away side retiring in the last rubber has every rubber decided. Its
    ``decided`` counted the retired row unfinished, which was the model's count
    and is no longer, so on a format whose result needs every rubber the card left
    its verb and tick blank over a night the build scores. Measured on
    ``--format doubles-3`` through ``build.py`` (/tmp/mcf/scratch/RUBBER/demo3/,
    base and mine): quolls v magpies, 2-6 1-6 5-4 with the away side retiring,
    was ``no-result`` on the base and is now counted to magpies on sets 2-1, under
    a card that says nothing of who won. Now ``decided`` counts a row one side
    retired from as finished, as ``SetResult.completed`` does. Rejected: retiring
    the home side instead, which would only move the night the card is silent on.
    """

    def card(self, name: str):
        fmt = format_mod.match_format(name)
        rng = random.Random(20260910)
        teams = demo.make_teams(2, rng)
        fixture = demo.Fixture(round_index=3, round_label="4",
                               date=datetime.date(2026, 2, 25), court=1,
                               home=teams[0], away=teams[1],
                               flavour=demo.RETIRED, match_id="4200013",
                               season=demo.SEASON_LABEL)
        spec, _ = demo.build_card(fixture, fmt, "3", "Wednesday", rng)
        return spec

    def test_every_formats_retired_card_prints_a_verb_and_ticks_a_winner(self):
        for name in sorted(demo.FORMATS):
            with self.subTest(format=name):
                spec = self.card(name)
                self.assertEqual(spec.rows[-1].status, SetStatus.RETIRED)
                self.assertEqual((spec.verb, spec.winner), (demo.AUTO, demo.AUTO))

    def test_the_card_it_writes_reads_back_with_every_check_agreeing(self):
        with tempfile.TemporaryDirectory() as tmp:
            demo.write_card_file(Path(tmp) / "retired.pdf", self.card("doubles-3"))
            (card,), _ = scorecard.read_tree(tmp)
        self.assertEqual([(c.name, c.available, c.agrees) for c in card.checks],
                         [("totals", True, True), ("verb", True, True),
                          ("winner-tick", True, True)],
                         [c.detail for c in card.checks])


# --- two seasons, for the careers page --------------------------------------

ARCHIVE = "demo-seasons"             # beside build.py, as the README names it
ARCHIVE_CONFIG = "demo-seasons.toml"
FIRST, SECOND = "2026-summer", "2026-winter"
LABELS = {FIRST: "Season 1", SECOND: "Season 2"}
CAREERS = "  careers      17 of 19 player(s) played in more than one season"
QUIET = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

SCRATCH = Path()                     # made by setUpModule, for these tests alone
_RUNS: dict[tuple[str, ...], "TwoSeasons"] = {}


def setUpModule():
    # Made here and not on import: test_fetch imports this file for its helpers,
    # and a folder made on import is made by every process that imports it,
    # whether or not it runs these tests.
    global SCRATCH
    holder = tempfile.TemporaryDirectory(prefix="demo-seasons-")
    unittest.addModuleCleanup(holder.cleanup)
    unittest.addModuleCleanup(_RUNS.clear)
    SCRATCH = Path(holder.name).resolve()


class TwoSeasons:
    """One run of ``--seasons multi`` in a copy of the program, and its build."""

    def __init__(self, project: Path, printed: str, command: str, report: str,
                 code: int):
        self.project = project
        self.printed = printed           # everything the demo printed
        self.command = command           # its `next:` line, without the `next: `
        self.report = report             # what that line printed, run
        self.code = code
        self.archive = project / ARCHIVE
        self.site = project / "site"


def two_seasons(*argv: str) -> TwoSeasons:
    """``tools/demo.py --seasons multi``, from a copy of the program, then the
    line it said to run next, run from there as well. Once for each argv.

    In a copy because both write into the project they are run in: the demo its
    folder and its config beside ``build.py``, and the build its ``site/``.
    """
    if argv not in _RUNS:
        project = copy_of_the_program(
            Path(tempfile.mkdtemp(prefix="run-", dir=SCRATCH)) /
            "matchcentre-dashboard")
        made = subprocess.run((sys.executable, str(project / "tools" / "demo.py"),
                               "--seasons", "multi", *argv), cwd=project,
                              capture_output=True, text=True, timeout=TIMEOUT,
                              env=QUIET)
        if made.returncode != 0:
            raise AssertionError(f"demo.py --seasons multi {' '.join(argv)} exited "
                                 f"{made.returncode}:\n{made.stdout}{made.stderr}")
        lines = [line[len("next: "):] for line in made.stdout.splitlines()
                 if line.startswith("next: ")]
        if len(lines) != 1:
            raise AssertionError(f"no single next: line in\n{made.stdout}")
        words = shlex.split(lines[0])
        if words[0] != "python3":
            raise AssertionError(f"the next: line does not run python3: {lines[0]}")
        built = subprocess.run((sys.executable, *words[1:], "--generated", STAMP),
                               cwd=project, capture_output=True, text=True,
                               timeout=TIMEOUT, env=QUIET)
        _RUNS[argv] = TwoSeasons(project, made.stdout, lines[0],
                                 built.stdout + built.stderr, built.returncode)
    return _RUNS[argv]


def demo_in(project: Path, cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    """The demo of the copy of the program in *project*, run from *cwd*."""
    return subprocess.run((sys.executable, str(project / "tools" / "demo.py"), *argv),
                          cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
                          env=QUIET)


def tree_bytes(folder: Path) -> dict[str, bytes]:
    """Every file under *folder*, by its path from there."""
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def read_cards(folder: Path) -> list:
    out = []
    for path in sorted(folder.rglob("*.pdf")):
        card, = scorecard.read(path)[0]
        out.append(card)
    return out


def rosters(folder: Path) -> dict[str, frozenset[str]]:
    """Each team's registered players, as the cards under *folder* print them."""
    teams: dict[str, set[str]] = {}
    for card in read_cards(folder):
        for team, roster in ((card.home_name, card.home_roster),
                             (card.away_name, card.away_roster)):
            teams.setdefault(team, set()).update(entry.name for entry in roster)
    return {team: frozenset(names) for team, names in teams.items()}


def careers_by_name(site: Path) -> dict[str, list[str]]:
    """Every career on ``careers.html``, by name: its Season cells, top to bottom."""
    page = CareersPage((site / "careers.html").read_text(encoding="utf-8"))
    return {page.headings[f"h-{slug}"]: [row["cells"][0]["text"]
                                         for row in page.rows(slug)]
            for slug in page.tables if slug != "index"}


class TestTwoSeasonsAreTwoFoldersWithALabelDatesAndMatchIdsOfTheirOwn(
        unittest.TestCase):
    """``--seasons multi``: the second season the demo had no way to write.

    The careers page is built only in multi-season mode, and it has something to
    say only about a player seen in two seasons. The demo wrote one season,
    labelled "Season 1" in a constant, and had no option to write another. So the
    only way to see the page on invented cards was to run the demo twice into two
    season folders by hand, and that gives two copies of one season. Measured on a
    copy of the tree before: 30 cards in two folders, both labelled "Season 1",
    both dated 4 February to 4 March 2026, all 15 match ids in both, and a careers
    page of 18 careers for 18 players, every one of them two rows long. Seeds 1
    and 2 instead gave 36 players and no career of two seasons at all
    (/tmp/t6/scratch/m19/before/measure.txt).

    So one run writes the archive: ``demo-seasons/2026-summer/``, which is the
    season the plain demo writes, and ``demo-seasons/2026-winter/``, played later
    in the year under "Season 2" and match ids of its own, with one player gone from
    one team and a newcomer in their place.

    Rejected: ``--season <key> <label>``, one season a run. The club still runs it
    twice, and the two runs share their dates, their match ids and every player,
    unless the club also changes the seed, and then a player in both is a namesake:
    of 112 names that seeds 1 to 200 share with the default seed's club, none is on
    the same team at the same ratings (/tmp/t6/scratch/m19/fix/seeds2.txt).
    """

    @classmethod
    def setUpClass(cls):
        cls.two = two_seasons()
        cls.cards = {key: read_cards(cls.two.archive / key) for key in LABELS}

    def test_the_archive_is_one_folder_a_season_and_nothing_else(self):
        self.assertEqual(sorted(path.name for path in self.two.archive.iterdir()),
                         [FIRST, SECOND])
        for key in LABELS:
            with self.subTest(season=key):
                self.assertEqual(len(self.cards[key]), 15)
                self.assertEqual(
                    {path.suffix for path in (self.two.archive / key).rglob("*")
                     if path.is_file()}, {".pdf"})

    def test_each_season_s_cards_print_that_season_s_label(self):
        for key, label in LABELS.items():
            with self.subTest(season=key):
                self.assertEqual({card.season_hint for card in self.cards[key]},
                                 {label})

    def test_the_second_season_starts_after_the_first_has_finished(self):
        first = {card.date for card in self.cards[FIRST]}
        second = {card.date for card in self.cards[SECOND]}
        self.assertLess(max(first), min(second))
        for date in sorted(first | second):
            with self.subTest(date=date):
                self.assertEqual(
                    datetime.date.fromisoformat(date).strftime("%A"), "Wednesday")

    def test_no_match_id_is_in_both_seasons(self):
        ids = {key: {card.match_id for card in cards}
               for key, cards in self.cards.items()}
        self.assertEqual([len(ids[key]) for key in LABELS], [15, 15])
        self.assertEqual(ids[FIRST] & ids[SECOND], set())

    def test_no_match_id_or_date_is_shared_at_the_largest_draw_either(self):
        """The most the demo writes a season: every team, two passes of the draw.

        Fifteen cards a season is the default and not the most. The largest draw
        the demo accepts, twelve teams over 22 rounds, is 132 cards a season, so
        a second season numbered from sixteen past the first shared 117 of its
        match ids with it, which the default run cannot tell. Asked in this
        process of the functions that number and date the fixtures, for every
        night the demo can be played on; the last of the second season's nights
        is in late December.
        """
        teams = demo.make_teams(len(demo.TEAM_NICKNAMES), random.Random(1))
        rounds = 2 * len(demo.round_robin(len(teams)))
        for day in demo.WEEKDAYS:
            with self.subTest(day=day):
                first, second = (demo.build_fixtures(teams, rounds, day, season)[0]
                                 for season in demo.SEASONS)
                ids = [{fixture.match_id for fixture in fixtures}
                       for fixtures in (first, second)]
                self.assertEqual([len(found) for found in ids], [132, 132])
                self.assertEqual(ids[0] & ids[1], set())
                self.assertLess(max(fixture.date for fixture in first),
                                min(fixture.date for fixture in second))
                self.assertEqual(max(fixture.date for fixture in second).year, 2026)

    def test_the_first_season_is_the_season_the_plain_demo_writes(self):
        # Byte for byte: a club that has run the plain demo has seen this season,
        # and the second is the one thing added to it.
        plain = SCRATCH / "plain-first-season"
        done = demo_in(self.two.project, self.two.project, "--out", str(plain))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(tree_bytes(plain), tree_bytes(self.two.archive / FIRST))

    def test_every_card_and_every_name_on_it_is_one_the_demo_invented(self):
        for key in LABELS:
            for path in sorted((self.two.archive / key).rglob("*.pdf")):
                with self.subTest(card=path.relative_to(self.two.archive)):
                    self.assertTrue(demo.is_demo_card(path))
        for key, cards in self.cards.items():
            names = {entry.name for card in cards
                     for entry in (*card.home_roster, *card.away_roster)}
            names |= {name for card in cards for row in card.sets
                      for name in (*row.home_players, *row.away_players)}
            for name in sorted(names):
                with self.subTest(season=key, name=name):
                    first, last = name.split(" ", 1)
                    self.assertIn(first, demo.FIRST_NAMES)
                    self.assertIn(last, demo.LAST_NAMES)


class TestOneRosterChangeGivesCareersThatSpanTheSeasonsAndCareersThatDoNot(
        unittest.TestCase):
    """The page's two cases on one page: a player in both seasons, and one in one.

    The runs by hand gave both only by a coincidence of names. The same seed made
    every career two seasons long, 18 of 18, and seeds 1 and 2 made none, 0 of 36
    (/tmp/t6/scratch/m19/before/measure.txt). The default seed and seed 5 made 2
    careers of two seasons among 34 players, and each was two invented people with
    one name, on other teams at other ratings, which the page shows as one player
    (/tmp/t6/scratch/m19/fix/twoseeds). 88 seeds of the first 200 share a name with
    the default seed's club like that (/tmp/t6/scratch/m19/fix/seeds.txt). So after
    the first season one team's last player leaves and a newcomer takes the place:
    17 careers of two seasons, of the same player, and two of one season each.

    Rejected: more than one change, or ratings that move between seasons. A club
    would see them on the page, and needs neither to see the page work.
    """

    @classmethod
    def setUpClass(cls):
        cls.two = two_seasons()
        cls.careers = careers_by_name(cls.two.site)
        cls.rosters = {key: rosters(cls.two.archive / key) for key in LABELS}

    def changed(self) -> tuple[str, str, str]:
        """The one team whose roster changed, who left it and who joined it."""
        before, after = self.rosters[FIRST], self.rosters[SECOND]
        self.assertEqual(set(before), set(after))
        changed = [team for team in sorted(before) if before[team] != after[team]]
        self.assertEqual(len(changed), 1, f"rosters changed: {changed}")
        team, = changed
        gone, = before[team] - after[team]
        joined, = after[team] - before[team]
        return team, gone, joined

    def test_one_team_loses_one_player_and_gains_one(self):
        team, gone, joined = self.changed()
        self.assertEqual(len(self.rosters[FIRST][team]), demo.PLAYERS_PER_TEAM)
        self.assertEqual(len(self.rosters[SECOND][team]), demo.PLAYERS_PER_TEAM)
        everyone = set().union(*self.rosters[FIRST].values())
        self.assertNotIn(joined, everyone, "the newcomer already played")

    def test_everyone_who_stays_keeps_the_ratings_they_had(self):
        """As the cards print them, in both seasons: one change and no more.

        A club re-rated between seasons moves every row of the page, and the one
        change the second season exists for is then one difference of nineteen.
        """
        ratings = {}
        for key in LABELS:
            seen: dict[str, set[tuple]] = {}
            for card in read_cards(self.two.archive / key):
                for entry in (*card.home_roster, *card.away_roster):
                    seen.setdefault(entry.name, set()).add(
                        (entry.singles, entry.doubles))
            for name, values in sorted(seen.items()):
                with self.subTest(season=key, player=name):
                    self.assertEqual(len(values), 1, values)
            ratings[key] = {name: values.pop() for name, values in seen.items()}
        stayed = sorted(ratings[FIRST].keys() & ratings[SECOND].keys())
        self.assertEqual(len(stayed), 17)
        for name in stayed:
            with self.subTest(player=name):
                self.assertEqual(ratings[SECOND][name], ratings[FIRST][name])

    def test_the_run_says_how_many_careers_span_the_seasons(self):
        self.assertEqual(self.two.code, 0, self.two.report[-3000:])
        self.assertIn(CAREERS, self.two.report.splitlines())

    def test_the_player_who_left_has_the_first_season_only(self):
        _, gone, _ = self.changed()
        self.assertEqual(self.careers[gone], ["Season 1"])

    def test_the_player_who_joined_has_the_second_season_only(self):
        _, _, joined = self.changed()
        self.assertEqual(self.careers[joined], ["Season 2"])

    def test_everyone_else_has_both_seasons_oldest_first(self):
        _, gone, joined = self.changed()
        others = {name: cells for name, cells in self.careers.items()
                  if name not in (gone, joined)}
        self.assertEqual(len(others), 17)
        for name, cells in sorted(others.items()):
            with self.subTest(player=name):
                self.assertEqual(cells, ["Season 1", "Season 2"])

    def test_the_demo_says_who_left_and_who_joined(self):
        team, gone, joined = self.changed()
        said = [line for line in self.two.printed.splitlines()
                if gone in line and joined in line]
        self.assertEqual(len(said), 1, self.two.printed)
        self.assertIn(team.upper(), said[0].upper())


class TestEachSeasonsLabelReachesEveryFileTheBuildWrites(unittest.TestCase):
    """"Season 1" and "Season 2", from the cards to every page that names a season.

    The runs by hand printed "Season 1" on both seasons, and the site could tell
    them apart only by their folders: "Season 1 - Wednesday (2025-winter)" on the
    front page, and "Season 1 (2025-winter)" or "(2026-winter)" in all 36 Season
    cells of the careers page (/tmp/t6/scratch/m19/before/measure.txt). Asked of
    each file the label is written into, because a different function writes each
    one, and a label that reached one says nothing of the others.
    """

    @classmethod
    def setUpClass(cls):
        cls.two = two_seasons()
        cls.competition = {key: next((cls.two.site / key).iterdir())
                           for key in LABELS}

    def season(self, key: str) -> dict:
        return json.loads((self.competition[key] / "season.json")
                          .read_text(encoding="utf-8"))

    def test_the_build_reads_the_archive_as_two_seasons_and_every_card(self):
        lines = self.two.report.splitlines()
        self.assertEqual(self.two.code, 0, self.two.report[-3000:])
        self.assertIn(f"  seasons      multi: {FIRST}, {SECOND}", lines)
        self.assertIn("  exit 0: all 30 card(s) scored, no errors", lines)

    def test_each_season_json_carries_its_label_and_its_key(self):
        for key, label in LABELS.items():
            with self.subTest(season=key):
                meta = self.season(key)["meta"]
                self.assertEqual((meta["season_key"], meta["season_label"]),
                                 (key, label))

    def test_each_season_data_js_is_its_season_json(self):
        prefix, suffix = "window.SEASON_DATA = JSON.parse(", ");"
        for key, label in LABELS.items():
            with self.subTest(season=key):
                text = (self.competition[key] / "season-data.js").read_text(
                    encoding="utf-8")
                line, = [ln for ln in text.splitlines() if ln.startswith(prefix)]
                self.assertTrue(line.endswith(suffix), line[-40:])
                data = json.loads(json.loads(line[len(prefix):-len(suffix)]))
                self.assertEqual(data["meta"]["season_label"], label)
                self.assertEqual(data, self.season(key))

    def test_the_picker_lists_each_competition_under_its_own_label(self):
        text = (self.two.site / "picker-data.js").read_text(encoding="utf-8")
        body = text.split("window.PICKER_DATA = ", 1)[1].rstrip().removesuffix(";")
        seen = sorted((entry["season_key"], entry["season_label"])
                      for entry in json.loads(body)["competitions"])
        self.assertEqual(seen, sorted(LABELS.items()))

    def test_the_front_page_heads_each_season_with_its_label_alone(self):
        page = (self.two.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(sorted(re.findall(r"<h2>([^<]*)<span", page)),
                         ["Season 1 - Wednesday", "Season 2 - Wednesday"])

    def test_every_season_cell_on_the_careers_page_is_the_label_alone(self):
        cells = collections.Counter(cell for cells in
                                    careers_by_name(self.two.site).values()
                                    for cell in cells)
        self.assertEqual(dict(cells), {"Season 1": 18, "Season 2": 18})


class TestTheSameSeedWritesTheSameTwoSeasons(unittest.TestCase):
    """Byte for byte, as the plain demo's one season is.

    The plain demo promises the same bytes for the same seed, which is what makes
    its dashboard a fixture: the pictures in the README, and every test that quotes
    a report. The archive keeps the promise, and a different seed still gives a
    different club with one change to its roster.
    """

    def test_a_second_run_writes_the_same_bytes(self):
        run = two_seasons()
        again = SCRATCH / "the-same-seed-again"
        done = demo_in(run.project, run.project, "--seasons", "multi",
                          "--out", str(again))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(tree_bytes(again), tree_bytes(run.archive))

    def test_another_seed_writes_other_cards_with_the_same_shape(self):
        run, other = two_seasons(), two_seasons("--seed", "7")
        mine, theirs = tree_bytes(run.archive), tree_bytes(other.archive)
        self.assertEqual(sorted(mine), sorted(theirs))
        self.assertNotEqual(mine, theirs)
        self.assertEqual(other.code, 0, other.report[-3000:])
        self.assertIn(CAREERS, other.report.splitlines())

    def test_the_newcomer_is_named_by_the_seed_s_own_generator(self):
        """The one draw a second run could get right by luck.

        A newcomer named from a generator of its own is somebody else each run,
        which the first test here catches unless two such draws name the same
        person: about one run in 560 (558 names a newcomer could have, measured
        over 200,000 draws), and a full run of /tmp/mutate203.py saw it happen.
        So this test asks what the name was drawn from, which luck cannot pass.
        The club's names are drawn by ``player_names`` from the run's generator,
        and the newcomer's must be drawn as theirs were. Rejected: the default
        seed's newcomer spelt out here, which luck passes as often, and which a
        change to the lists of names would break with the rule still kept.
        """
        rng = random.Random(demo.DEFAULT_SEED)
        teams = demo.make_teams(6, rng)
        signature = inspect.signature(demo.player_names)
        with mock.patch.object(demo, "player_names",
                               wraps=demo.player_names) as drawn:
            demo.change_roster(teams, rng)
        self.assertTrue(drawn.called, "the newcomer's name was not drawn")
        for call in drawn.call_args_list:
            given = signature.bind(*call.args, **call.kwargs).arguments["rng"]
            self.assertIs(given, rng, "the newcomer was named from a generator "
                                      "other than the run's")


@unittest.skipIf(os.name == "nt", "the pasted line is run by a POSIX shell")
class TestTheCommandItPrintsForTwoSeasonsBuildsThemAsTwo(unittest.TestCase):
    """The ``next:`` line names the config that reads the folder as seasons.

    Without ``[build] seasons = "multi"`` a build reads one season, and it reads
    the two runs by hand as one season played twice: 15 F9 duplicate-card
    findings, and no careers page (/tmp/t6/scratch/m19/probe1/report.txt). A club's
    own ``config.toml`` is not the demo's to change, so the demo writes
    ``demo-seasons.toml`` beside ``build.py`` and the line names it. The rest of
    the line is ``next_command``'s, the plain demo's, so it is right wherever the
    demo was run from and however the folder was typed.

    Rejected: telling the club to set ``seasons`` in ``config.toml``, which leaves
    the line they paste building the wrong thing; a config inside the folder, which
    the build lists as a file it did not read; and a config beside ``--out``, which
    would want a path rule of its own beside ``next_command``'s.
    """

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="demo-seasons-next-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        self.project = copy_of_the_program(self.tmp / "our club" /
                                           "matchcentre-dashboard")
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()
        self.env = with_this_python(self.tmp / "bin", QUIET)

    def printed(self, cwd: Path, *argv: str) -> str:
        """Two seasons of two cards each, and the one line it says to run next."""
        done = subprocess.run((sys.executable, str(self.project / "tools" / "demo.py"),
                               "--seasons", "multi", "--teams", "4", "--rounds", "1",
                               *argv), cwd=cwd, capture_output=True, text=True,
                              timeout=TIMEOUT, env=self.env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line[len("next: "):] for line in done.stdout.splitlines()
                 if line.startswith("next: ")]
        self.assertEqual(len(lines), 1, done.stdout)
        return lines[0]

    def pasted(self, command: str, cwd: Path) -> None:
        done = subprocess.run(("sh", "-c", command), cwd=cwd, capture_output=True,
                              text=True, timeout=TIMEOUT, env=self.env)
        self.assertEqual(done.returncode, 0,
                         f"{command}\n{done.stdout[-1500:]}{done.stderr}")
        lines = done.stdout.splitlines()
        self.assertIn(f"  seasons      multi: {FIRST}, {SECOND}", lines)
        self.assertIn("  exit 0: all 4 card(s) scored, no errors", lines)

    def test_from_the_project_it_is_one_command(self):
        command = self.printed(self.project)
        self.assertEqual(command, f"python3 build.py --data {ARCHIVE} "
                                  f"--config {ARCHIVE_CONFIG}")
        self.pasted(command, self.project)

    def test_from_somewhere_else_it_is_a_cd_that_arrives(self):
        command = self.printed(self.elsewhere)
        self.assertEqual(command, f"cd {pasteable(str(self.project))} && "
                                  f"python3 build.py --data {ARCHIVE} "
                                  f"--config {ARCHIVE_CONFIG}")
        self.pasted(command, self.elsewhere)

    def test_a_folder_outside_the_project_is_named_in_full(self):
        command = self.printed(self.elsewhere, "--out", "our seasons")
        outside = pasteable(str(self.elsewhere / "our seasons"))
        self.assertEqual(command, f"cd {pasteable(str(self.project))} && "
                                  f"python3 build.py --data {outside} "
                                  f"--config {ARCHIVE_CONFIG}")
        self.pasted(command, self.elsewhere)

    def test_the_bye_note_names_the_config_the_line_reads(self):
        """An odd draw's note says where a declared team list turns a bye into a
        scored one, and it said ``config.toml`` above a line that reads
        ``demo-seasons.toml`` and never ``config.toml`` (/tmp/t6/scratch/m19/fix/
        before.txt, case D). A team list put where the note said changed nothing
        the line built.

        Then it said "only a declared team list in demo-seasons.toml turns it
        into a scored bye", of a file the demo writes again each run, so a list
        typed there went with the next demo (/tmp/t7/scratch/DEMO/byes/base). It
        names that file as declaring none. Rejected: leaving the file unnamed,
        which says nothing of the config the line reads."""
        done = demo_in(self.project, self.project, "--seasons", "multi",
                       "--teams", "5", "--rounds", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        note, = [line for line in done.stdout.splitlines() if "(F1)" in line]
        self.assertIn(f"with no team list declared in {ARCHIVE_CONFIG} that is a "
                      f"warning, not a scored bye", note)
        self.assertNotIn("turns it", note)
        self.assertNotIn("config.toml", note)

    def test_the_club_s_own_config_is_neither_changed_nor_read(self):
        """One that says ``seasons = "single"``: the line still builds two seasons,
        and the file is the same bytes after the demo and the build as before."""
        own = self.project / "config.toml"
        own.write_text("# the club's own\n[build]\nseasons = \"single\"\n",
                       encoding="utf-8")
        before = own.read_bytes()
        self.pasted(self.printed(self.project), self.project)
        self.assertEqual(own.read_bytes(), before)

    def test_the_line_is_next_command_s_with_the_config_after_it(self):
        """Asked in this process, with both rules swapped for ones that mark what
        they were handed, so that a copy of either is told from a call to it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            out = root / "cards"
            said = io.StringIO()
            with mock.patch.object(demo, "ROOT", root), \
                    mock.patch.object(demo, "next_command",
                                      side_effect=lambda f: f"<next {f}>") as told, \
                    mock.patch.object(demo, "pasteable",
                                      side_effect=lambda p: f"<{p}>"), \
                    contextlib.redirect_stdout(said):
                code = demo.main(["--seasons", "multi", "--teams", "2",
                                  "--rounds", "1", "--out", str(out)])
            self.assertEqual(code, 0, said.getvalue())
            told.assert_called_once_with(out)
            self.assertEqual([line for line in said.getvalue().splitlines()
                              if line.startswith("next: ")],
                             [f"next: <next {out}> --config <{ARCHIVE_CONFIG}>"])
            self.assertTrue((root / ARCHIVE_CONFIG).is_file())


class TestNoNoteOrReviewOfTheDemosSeasonsAsksForALineOfTheConfigTheDemoRewrites(
        unittest.TestCase):
    """No note or review of a build that reads ``demo-seasons.toml`` sends the club
    to a line of it.

    The demo writes that file again on every run, as its first line says, so a
    line typed into it is gone the next time the demo runs. Measured before
    (/tmp/t7/scratch/DEMO/probe/before.txt), the build the demo's ``next:`` line
    runs named the file on 25 of its 438 lines, and so did the same build after
    each of five other demos. 14 of its 16 notes gave a line to type there or
    uncomment: the season labels, the club name, and for each season the team
    list, the team names, the captains, the match shape, the rules and the
    season's length. The review listed 12 values to add to it, under an offer to
    rewrite it with ``--discover --force``. The loader knows the file by its name
    and the line ``tools/demo.py`` begins it with, and a config that is both is
    given none of it. The notes that ask for nothing are said as before, and each
    competition's own block still says its teams are derived and its season's
    length undeclared. What the notes that asked began with goes with them: the
    club name read off the letterhead, the team names un-shouted, the captains
    read off the (C) marks, and that no bye is credited without a team list. The
    first three are still on the pages the build writes.

    Rejected: the same advice with a caveat that the demo writes over the file,
    which is still advice nobody can keep; pointing it at ``config.toml``, which
    this build never reads; taking away the rewrite offer alone, which left the
    review asking for the same twelve values to be typed in by hand; and knowing
    the file by its first line alone, which a copy made the club's
    ``config.toml`` keeps: a build with that copy was given 2 notes and no review,
    where before it was given 16 notes and a review of 12
    (/tmp/t7/scratch/DEMO/copied/).
    """

    @classmethod
    def setUpClass(cls):
        holder = tempfile.TemporaryDirectory(prefix="demo-seasons-own-")
        cls.addClassCleanup(holder.cleanup)
        cls.project = copy_of_the_program(Path(holder.name).resolve() /
                                          "matchcentre-dashboard")
        made = demo_in(cls.project, cls.project, "--seasons", "multi",
                       "--teams", "4", "--rounds", "1")
        if made.returncode != 0:
            raise AssertionError(made.stdout + made.stderr)
        cls.demo = cls.built()
        cls.demo_one = cls.built("--only", FIRST)
        # The same settings, under the same name, as a club's own file: the
        # demo's opening comment is all that is taken off.
        config = cls.project / ARCHIVE_CONFIG
        kept = [line for line in config.read_text(encoding="utf-8")
                .splitlines(keepends=True) if not line.startswith("#")]
        config.write_text("".join(kept), encoding="utf-8")
        cls.own = cls.built()

    @classmethod
    def built(cls, *argv: str) -> str:
        done = subprocess.run((sys.executable, "build.py", "--data", ARCHIVE,
                               "--config", ARCHIVE_CONFIG, "--generated", STAMP,
                               *argv), cwd=cls.project, capture_output=True,
                              text=True, timeout=TIMEOUT, env=QUIET)
        if done.returncode != 0:
            raise AssertionError(done.stdout[-3000:] + done.stderr)
        return done.stdout + done.stderr

    @staticmethod
    def asking(report: str) -> list[str]:
        """The notes that send the club to a line of a config."""
        from test_cli import report_notes
        return [note for note in report_notes(report)
                if ARCHIVE_CONFIG in note or "under [" in note]

    def test_no_note_sends_the_club_to_a_line_of_it(self):
        self.assertEqual(self.asking(self.demo), [])

    def test_nor_does_a_review_of_it_or_an_offer_to_rewrite_it(self):
        self.assertNotIn("\n== review", self.demo)
        self.assertNotIn("--discover", self.demo)
        self.assertNotIn(ARCHIVE_CONFIG, self.demo)

    def test_with_one_season_listed_the_other_s_review_is_not_counted(self):
        # "not listed" counts what the review would list for the season left out,
        # and a count of values nobody is shown would send the club to --only's
        # absence for a list that is not there either.
        self.assertIn("\n== not listed: ", self.demo_one)
        self.assertNotIn("value(s) worked out", self.demo_one)

    def test_the_notes_that_ask_for_nothing_are_said_and_the_facts_are_shown(self):
        # Each of them a note a club's own file of that name is given too: a note
        # on the season's length asked of a length nobody declared would say it
        # was declared as None rounds long, and a club's own file is told the
        # length was not declared.
        from test_cli import report_notes
        notes = report_notes(self.demo)
        self.assertEqual([note.split(":")[0] for note in notes
                          if "filed under season" in note],
                         [f"{season}-wednesday-division-3"
                          for season in (FIRST, SECOND)])
        self.assertLessEqual(set(notes), set(report_notes(self.own)))
        lines = self.demo.splitlines()
        self.assertEqual(lines.count("  teams        4 (derived)"), 2)
        self.assertEqual(lines.count("  rounds       1 played; season length not "
                                     "declared, so no percentage is shown"), 2)

    def test_a_club_s_file_of_the_same_name_and_settings_is_told_all_of_it(self):
        # Counted against the demo's own report rather than pinned: every note the
        # demo's file is not given is one that asks for a line, and a club's own
        # file is given it, with the review and its offer.
        from test_cli import report_notes
        withheld = [note for note in report_notes(self.own)
                    if note not in report_notes(self.demo)]
        self.assertTrue(withheld)
        self.assertEqual(withheld, self.asking(self.own))
        self.assertRegex(self.own, r"\n== review: \d+ value\(s\) worked out, "
                                   r"not declared")
        self.assertIn(f"--discover --force --data {ARCHIVE} \\", self.own)

    def test_nor_does_any_sentence_of_it_name_a_config_this_build_did_not_read(self):
        """The rules' footer and F8 named config.toml under ``--config
        demo-seasons.toml``.

        Measured before (/tmp/t7/scratch/CF1/probe/before/archive.txt): the build
        the demo's ``next:`` line runs named config.toml on lines 13, 75 and 80 of
        its 136, the footer's "These come from config.toml" and F8's "Uncomment
        `teams` in config.toml" for each season, and demo-seasons.toml on none.
        F8 now says only what the missing list costs, and the footer names no file
        to a build of the demo's own, as no note does. Rejected: the footer naming
        demo-seasons.toml, a line of which the demo writes over on its next run.
        """
        from test_cli import flat
        self.assertNotIn("config.toml", self.demo)
        self.assertIn("The scoring and the sets are never read from a card",
                      flat(self.demo))
        self.assertNotIn("set yours under [rules]", flat(self.demo))

    def test_while_a_club_s_file_of_that_name_is_sent_to_its_rules(self):
        from test_cli import flat
        self.assertIn(f"set yours under [rules] in {ARCHIVE_CONFIG} and re-run.",
                      flat(self.own))
        self.assertNotIn("config.toml", self.own)

    def test_the_loader_knows_the_demo_s_file_by_its_name_and_its_first_line(self):
        # In process: the text the demo writes, loaded under its name, and each
        # thing a club's own file can share with it: that name, without the line;
        # the text, copied to config.toml; the line, below one of the club's own;
        # and a first line that begins as the demo's does.
        from matchcentre import config as config_mod
        self.assertTrue(demo.ARCHIVE_CONFIG_TEXT.startswith(config_mod.DEMO_MARKER))
        self.assertEqual(demo.ARCHIVE_CONFIG, config_mod.DEMO_CONFIG)
        text = demo.ARCHIVE_CONFIG_TEXT
        rest = text.split("\n", 1)[1]
        cases = (
            (ARCHIVE_CONFIG, text, True),
            (ARCHIVE_CONFIG, rest, False),
            (config_mod.DEFAULT_PATH, text, False),
            (ARCHIVE_CONFIG, "# our committee's settings\n" + text, False),
            (ARCHIVE_CONFIG, "# Written by the committee, March\n" + rest, False),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for name, body, written in cases:
                with self.subTest(name=name, begins=body.split("\n", 1)[0]):
                    path = Path(tmp) / name
                    path.write_text(body, encoding="utf-8")
                    self.assertIs(config_mod.load(path).demo, written)
            self.assertIs(config_mod.load(Path(tmp) / "absent.toml").demo, False)


class TestTwoSeasonsAreRefusedWhereAClubsOwnCardsAreOrGo(unittest.TestCase):
    """``--seasons multi`` into ``data/``, inside it, around it, or into a folder
    holding a club's cards: exit 2, and nothing written.

    The line the demo prints builds everything under ``--out`` with
    ``demo-seasons.toml`` and not with the club's config, and ``data/`` is where
    the README tells a club its cards go. Measured on the tree before
    (/tmp/t6/scratch/m19/fix/before.txt): a club with a season in
    ``data/2025-winter`` and ``players = "numbered"``, whose own build named
    nobody, ran ``--out data`` and pasted the line, and the build read three
    seasons and named the club's players in three files of ``site/``. The same
    club with its cards in a folder of their own did the same with ``--out``
    pointed at it. ``--out .`` read ``data/`` as a third season and wrote two
    season folders into the project, and ``--out data`` after the quick start
    put "Season 1 (demo-season)" in 18 cells of the careers page. One level inside
    ``data/`` the line is the bare build, which read the two seasons as one and
    found fifteen matches claimed twice (/tmp/t6/scratch/m19/inside-data.txt).
    Each is refused now, before anything is written or cleaned
    (/tmp/t6/scratch/m19/fix/after1.txt).

    Rejected: ``data/`` itself allowed, as a folder of seasons, which is the first
    case above; the club's ``players`` and ``[player_names]`` copied into
    ``demo-seasons.toml``, a second reading of a club's config beside the build's
    own; and a warning, which leaves below it the line that publishes the names.
    """

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="demo-seasons-refused-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        self.project = copy_of_the_program(self.tmp / "matchcentre-dashboard")
        self.data = self.project / "data"
        self.inside = self.data / "archive"

    def refused(self, out: str, *argv: str) -> str:
        """What the demo said, refusing ``--out out`` from the project."""
        done = demo_in(self.project, self.project, "--seasons", "multi",
                       "--teams", "2", "--rounds", "1", *argv, "--out", out)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertTrue(done.stderr.startswith("demo: "), done.stderr)
        self.assertEqual(len(done.stderr.splitlines()), 1, done.stderr)
        self.assertFalse((self.project / ARCHIVE_CONFIG).exists())
        return done.stderr

    def everything(self, folder: Path) -> tuple[dict[str, bytes], list[str]]:
        """Every file under *folder* with its bytes, and every folder too."""
        return tree_bytes(folder), sorted(path.relative_to(folder).as_posix()
                                          for path in folder.rglob("*")
                                          if path.is_dir())

    def test_a_folder_inside_data_is_refused_and_left_unmade(self):
        said = self.refused("data/archive")
        self.assertIn(f"--out data/archive is inside {self.data},", said)
        self.assertFalse(self.inside.exists())

    def test_data_itself_is_refused_while_it_is_empty_too(self):
        said = self.refused("data")
        self.assertIn(f"--out data is {self.data},", said)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_a_club_s_season_in_data_is_left_as_it_was(self):
        write_season_cards(self.data / "2025-winter")
        (self.project / "config.toml").write_text(
            '[build]\nseasons = "multi"\nplayers = "numbered"\n', encoding="utf-8")
        before = self.everything(self.project)
        self.refused("data")
        self.assertEqual(self.everything(self.project), before)

    def test_a_folder_around_data_is_refused_and_the_project_left_as_it_was(self):
        before = self.everything(self.project)
        said = self.refused(".")
        self.assertIn(f"--out . holds {self.data},", said)
        self.assertEqual(self.everything(self.project), before)

    def test_a_folder_of_the_club_s_cards_outside_data_is_refused_by_its_first(self):
        theirs = self.tmp / "cards"
        write_season_cards(theirs / "2025-winter")
        before = self.everything(theirs)
        found = sorted(cards(theirs))
        said = self.refused(str(theirs))
        self.assertIn(f"--out {theirs} holds {len(found)} scorecard(s) this script "
                      f"did not write, the first {found[0]}.", said)
        self.assertEqual(self.everything(theirs), before)

    def test_the_refusal_comes_before_clean_deletes_anything(self):
        done = demo_in(self.project, self.project, "--out", "data/archive",
                       "--teams", "4", "--rounds", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = tree_bytes(self.inside)
        self.assertEqual(len(before), 2)
        self.refused("data/archive", "--clean")
        self.assertEqual(tree_bytes(self.inside), before)

    def test_the_plain_demo_may_still_write_there(self):
        done = demo_in(self.project, self.project, "--out", "data/archive",
                       "--teams", "4", "--rounds", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(cards(self.inside)), 2)

    def test_whether_it_is_data_or_inside_it_is_asked_of_within_data(self):
        """One answer to "is this folder ``data/``, or inside it?", the one
        ``next_command`` and ``tools/fetch.py`` ask.

        Measured before (/tmp/t7/scratch/DEMO/m4228/before.txt), the refusal
        resolved ``data/`` and asked ``is_relative_to`` itself, and asked
        ``within_data`` nothing for ``data/``, a folder inside it, the project or
        a folder outside it. Asked here with that function swapped for one that
        answers the same of every folder, so that the refusal of a folder
        outside the project shows whose answer it was given from. Around
        ``data/`` is asked by the refusal alone, and is not swapped.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            outside = root.parent / "season"
            for answer, where in ((Path("archive"), "is inside"), (Path("."), "is")):
                with self.subTest(within_data=str(answer)), \
                        mock.patch.object(demo, "ROOT", root), \
                        mock.patch.object(demo, "within_data",
                                          return_value=answer) as asked:
                    said = demo.archive_refusal(outside)
                    asked.assert_called_once_with(outside)
                    self.assertTrue(str(said).startswith(
                        f"--out {outside} {where} {root / 'data'}, where"), said)

    def test_a_folder_further_around_data_is_refused_as_holding_it(self):
        """Two levels above ``data/``, as a folder that holds the project is.

        ``--out .``, one level above, was the only folder around ``data/`` the
        class asked about, so a refusal narrowed to ``data/``'s own parent passed
        every test of it (/tmp/mutate265.py, #7). In process, with the project
        one level down in a scratch folder, and that folder given as ``--out``.
        """
        with tempfile.TemporaryDirectory() as tmp:
            around = Path(tmp).resolve()
            root = around / "matchcentre-dashboard"
            with mock.patch.object(demo, "ROOT", root):
                said = demo.archive_refusal(around)
            self.assertTrue(str(said).startswith(
                f"--out {around} holds {root / 'data'}, where"), said)


class TestARerunReplacesTheSeasonsAndTheConfigItWroteBefore(unittest.TestCase):
    """A second ``--seasons multi`` into the folder a first one wrote.

    ``--clean`` removes a previous demo before the run writes, and the config is
    written again each run, so that the line printed next builds what this run
    wrote. Neither was asked by a test of two seasons: a ``--clean`` that did
    nothing under ``--seasons multi``, and a config written only where there was
    none, both passed all of them (the review's mutants R3 and R4, run by
    /tmp/mutate203.py). A folder holding only cards the demo wrote is its own to
    write into again, and is not refused.

    Rejected: leaving it to the plain demo's tests of ``--clean``, which call the
    same function and never with two seasons, and never ask of a config.
    """

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="demo-seasons-again-")
        self.addCleanup(holder.cleanup)
        self.project = copy_of_the_program(Path(holder.name).resolve() /
                                           "matchcentre-dashboard")

    def run_demo(self, *argv: str) -> str:
        done = demo_in(self.project, self.project, "--seasons", "multi", *argv)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def everything(self, folder: Path) -> tuple[dict[str, bytes], list[str]]:
        return tree_bytes(folder), sorted(path.relative_to(folder).as_posix()
                                          for path in folder.rglob("*"))

    def test_clean_leaves_the_folder_as_a_first_run_writes_it(self):
        self.run_demo("--teams", "4", "--rounds", "2")
        said = self.run_demo("--teams", "2", "--rounds", "1", "--clean")
        self.assertIn(f"cleaned 8 card(s) from {self.project / ARCHIVE}",
                      said.splitlines())
        self.run_demo("--teams", "2", "--rounds", "1", "--out", "fresh")
        self.assertEqual(self.everything(self.project / ARCHIVE),
                         self.everything(self.project / "fresh"))

    def test_an_edited_config_is_written_again_as_a_first_run_writes_it(self):
        # Edited to what a club might think it wants, and what builds the two
        # seasons as one; the rerun is without --clean, into its own cards.
        self.run_demo("--teams", "2", "--rounds", "1")
        config = self.project / ARCHIVE_CONFIG
        first = config.read_bytes()
        config.write_text('[build]\nseasons = "single"\n', encoding="utf-8")
        self.run_demo("--teams", "2", "--rounds", "1")
        self.assertEqual(config.read_bytes(), first)


class TestGitIgnoresWhatTwoSeasonsWriteBesideBuildPy(unittest.TestCase):
    """``demo-seasons/`` and ``demo-seasons.toml``, as the plain demo's season is.

    The plain demo writes into ``data/``, which ``.gitignore`` has always kept
    out of a commit. Two seasons write beside ``build.py``, and ``.gitignore``
    named neither (/tmp/t6/base/matchcentre-dashboard/.gitignore), so a ``git add
    .`` after the README's two commands would take thirty invented cards and a
    config that is not the project's. Asked of git where there is one, and read
    off the file where there is not, as ``reports/`` is. The paths are the demo's
    own constants, so a default moved is a rule missing.

    Rejected: writing the seasons into ``data/``, which is ignored already and is
    the club's (TestTwoSeasonsAreRefusedWhereAClubsOwnCardsAreOrGo).
    """

    def test_git_ignores_both_and_neither_is_committed(self):
        folder = demo.DEFAULT_ARCHIVE.relative_to(demo.ROOT).as_posix()
        if not under_git():
            lines = [line.strip() for line in
                     (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
            for rule in (f"/{folder}/", f"/{demo.ARCHIVE_CONFIG}"):
                with self.subTest(rule=rule):
                    self.assertIn(rule, lines)
            return
        for probe in (f"{folder}/{FIRST}/round-01/magpies-v-rosellas.pdf",
                      demo.ARCHIVE_CONFIG):
            with self.subTest(path=probe):
                result = subprocess.run(("git", "check-ignore", "-q", probe),
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0, f"{probe} is not gitignored")
        self.assertEqual(git("ls-files", folder, demo.ARCHIVE_CONFIG).split(), [])


class TestThePlainDemoIsStillOneSeasonAndWritesNoConfig(unittest.TestCase):
    """Without ``--seasons multi`` the demo writes what it always wrote.

    Its season is the one the README's quick start builds and quotes, so its cards,
    what it prints and where it writes are pinned by tests elsewhere. Measured on
    six sets of arguments before and after this option, the cards and the printed
    run were the same bytes, and ``--seasons single`` wrote the default's cards
    (/tmp/t6/scratch/m19/bytes).
    """

    @classmethod
    def setUpClass(cls):
        cls.project = copy_of_the_program(SCRATCH / "plain" / "matchcentre-dashboard")
        cls.default = demo_in(cls.project, cls.project)
        cls.single = demo_in(cls.project, cls.project, "--seasons", "single",
                             "--out", str(cls.project / "single"))

    def test_seasons_single_is_the_plain_demo(self):
        self.assertEqual(self.default.returncode, 0, self.default.stderr)
        self.assertEqual(self.single.returncode, 0, self.single.stderr)
        season = self.project / "data" / "demo-season"
        self.assertEqual(tree_bytes(self.project / "single"), tree_bytes(season))
        self.assertEqual(
            self.single.stdout.replace(str(self.project / "single"), "<out>")
                              .replace("--data single", "<data>"),
            self.default.stdout.replace(str(season), "<out>")
                               .replace("python3 build.py\n",
                                        "python3 build.py <data>\n"))

    def test_it_writes_one_season_under_the_one_label(self):
        season = self.project / "data" / "demo-season"
        self.assertEqual({card.season_hint for card in read_cards(season)},
                         {"Season 1"})
        self.assertTrue(all(path.name.startswith("round-")
                            for path in season.iterdir()))
        self.assertTrue(self.default.stdout.splitlines()[0].endswith(", Season 1"))

    def test_it_writes_no_config_and_names_none(self):
        self.assertFalse((self.project / ARCHIVE_CONFIG).exists())
        self.assertFalse((self.project / ARCHIVE).exists())
        self.assertIn("next: python3 build.py\n", self.default.stdout)

    def test_its_bye_note_still_names_config_toml(self):
        # The line it prints builds with the club's config.toml, so that is where
        # a declared team list goes: the note two seasons correct is still right
        # here, and a note naming the demo's config would send a club to a file
        # its build never reads.
        done = demo_in(self.project, self.project, "--teams", "5", "--rounds", "1",
                       "--out", str(self.project / "odd"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        note, = [line for line in done.stdout.splitlines() if "(F1)" in line]
        self.assertIn(f" in {build.config_mod.DEFAULT_PATH} turns it", note)


class TestThePlainDemosDashboardIsClearedAsTheReadmeSays(unittest.TestCase):
    """What the README says becomes of the demo's dashboard is what the builds do.

    Of deleting ``data/demo-season/``, the README said "the next run says so, under
    ``left behind``, and ``python3 build.py --prune`` clears it". With no cards of
    the club's in ``data/`` yet that next run reads none and builds nothing:
    measured, it exited 1 in 38 lines ending ``exit 1: nothing was built``, listed
    nothing under ``left behind``, and left ``site/wednesday-division-3`` where it
    was, and ``--prune`` did the same (/tmp/t7/scratch/DEMO/m4229/before). The
    first build of the club's own cards is the one that lists it, and ``--prune``
    then clears it. So the plain demo's paragraph says both, as the two-season
    paragraph after it already did. Rejected: an empty build listing what it left
    behind, which the build declines on purpose, since every dashboard is one a
    run that read no cards is not writing.

    Those steps left ``config.toml``, which the demo's build writes when there is
    none, with ``[competitions.wednesday-division-3]`` in it live: after them the
    front page still listed Wednesday Division 3, greyed, ``No results yet``,
    ``declared in config.toml``. Deleted, the next build wrote one naming only
    the club's own division, and the card went (/tmp/t7/scratch/DEMO/m4229cfg/).
    So the README says to delete it. Rejected: deleting the section, which is
    five tables to find by hand in a file the demo's cards wrote all of.
    """

    SECTION = "## See it work before you have any cards"

    @classmethod
    def setUpClass(cls):
        project = copy_of_the_program(SCRATCH / "deleted" / "matchcentre-dashboard")
        made = demo_in(project, project)
        first = cls.build(project)
        if made.returncode or first.returncode:
            raise AssertionError(f"the demo and its build exited {made.returncode} "
                                 f"and {first.returncode}:\n{first.out}")
        # Read off the tree, so that a change to how a competition is named cannot
        # leave these tests asking after a folder nothing writes.
        cls.lost, = [path for path in (project / "site").iterdir() if path.is_dir()]
        shutil.rmtree(project / "data" / "demo-season")
        cls.empty = cls.build(project)
        cls.empty_pruned = cls.build(project, "--prune")
        cls.kept = cls.lost.is_dir()
        own = demo_in(project, project, "--day", "Tuesday", "--division", "B",
                      "--out", str(project / "data" / "our-cards"))
        if own.returncode:
            raise AssertionError(f"the club's stand-in cards:\n{own.stderr}")
        cls.own = cls.build(project)
        cls.own_pruned = cls.build(project, "--prune")
        cls.cleared = not cls.lost.exists()
        cls.ours = sorted(path.name for path in (project / "site").iterdir()
                          if path.is_dir())
        # What the demo's build wrote into config.toml, and the front page it
        # still makes; then the same with that file deleted.
        read = build.config_mod.load(project / "config.toml").competitions
        cls.declared = sorted(read)
        cls.label = read[cls.lost.name].label if cls.lost.name in read else None
        cls.front = (project / "site" / "index.html").read_text(encoding="utf-8")
        (project / "config.toml").unlink(missing_ok=True)
        cls.fresh = cls.build(project)
        cls.redeclared = sorted(
            build.config_mod.load(project / "config.toml").competitions)
        cls.fresh_front = (project / "site" / "index.html").read_text(
            encoding="utf-8")

    @staticmethod
    def build(project: Path, *argv: str) -> subprocess.CompletedProcess:
        done = subprocess.run((sys.executable, "build.py", *argv,
                               "--generated", STAMP), cwd=project,
                              capture_output=True, text=True, timeout=TIMEOUT,
                              env=QUIET)
        done.out = done.stdout + done.stderr
        return done

    def clauses(self) -> list[str]:
        """The plain demo's prose, clause by clause, before the two-season block.

        Cut there because the two-season paragraph says the same of what its build
        left, and already said to delete ``site/``: read whole, the section would
        pass on that paragraph whatever this one said.
        """
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertIn(self.SECTION, lines)
        body = lines[lines.index(self.SECTION) + 1:]
        two = [n for n, line in enumerate(body)
               if "tools/demo.py --seasons multi" in line]
        self.assertTrue(two, "the section no longer shows the two-season demo")
        return re.split(r"(?<=[.;])\s+", " ".join(" ".join(body[:two[0]]).split()))

    def test_with_no_cards_of_its_own_a_club_is_told_to_delete_site(self):
        self.assertEqual(self.empty.returncode, 1, self.empty.out)
        self.assertIn("exit 1: nothing was built", self.empty.out)
        for run in (self.empty, self.empty_pruned):
            self.assertNotIn("== left behind", run.out)
            self.assertNotIn("== pruned", run.out)
        self.assertTrue(self.kept, f"{self.lost} went with no card read")
        self.assertTrue(any("delete" in clause and "`site/`" in clause
                            for clause in self.clauses()), self.clauses())

    def test_the_first_build_of_its_own_cards_lists_it_for_prune_to_clear(self):
        self.assertEqual(self.own.returncode, 0, self.own.out)
        self.assertIn("== left behind", self.own.out)
        listed = self.own.out.split("== left behind", 1)[1].split("\n== ", 1)[0]
        self.assertIn(f"  {Path('site') / self.lost.name}\n", listed)
        self.assertEqual(self.own_pruned.returncode, 0, self.own_pruned.out)
        self.assertTrue(self.cleared, self.own_pruned.out)
        said = [clause for clause in self.clauses() if "`left behind`" in clause]
        self.assertTrue(said, self.clauses())
        for clause in said:
            self.assertIn("first build of your own cards", clause)
            self.assertIn("--prune", clause)

    def test_the_config_the_demo_s_build_wrote_is_deleted_or_its_division_stays(self):
        self.assertIn(self.lost.name, self.declared)
        self.assertIn(f">{self.label}<", self.front)
        self.assertIn("No results yet", self.front)
        self.assertEqual(self.fresh.returncode, 0, self.fresh.out)
        self.assertEqual(self.redeclared, self.ours)
        self.assertNotIn(self.label, self.fresh_front)
        self.assertNotIn("No results yet", self.fresh_front)
        self.assertTrue(any("delete" in clause and "`config.toml`" in clause
                            and "front page" in clause
                            for clause in self.clauses()), self.clauses())


class TestTheReadmeShowsAClubTheCareersPageOnInventedCards(unittest.TestCase):
    """Where the README describes the careers page, it says how to see one.

    "Careers across seasons" described a page that only a multi-season build
    writes, and the one demo the README gave a club wrote one season. So the demo
    section gives the two commands, as the demo prints them, and says what to
    delete after; the careers section points there, and the list of commands
    carries the option.
    """

    SECTION = "## See it work before you have any cards"

    def prose(self, heading: str) -> str:
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertIn(heading, lines)
        depth = len(heading) - len(heading.lstrip("#"))
        body = []
        for line in lines[lines.index(heading) + 1:]:
            if line.startswith("#") and len(line) - len(line.lstrip("#")) <= depth:
                break
            body.append(line)
        return " ".join(" ".join(body).split())

    def test_the_demo_section_gives_the_two_commands_the_demo_prints(self):
        block = readme_block(self, self.SECTION, contains="--seasons multi")
        commands = [line.split("  #")[0].strip() for line in block
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(commands, ["python3 tools/demo.py --seasons multi",
                                    two_seasons().command])

    def sentences(self, heading: str) -> list[str]:
        return re.split(r"(?<=[.:])\s+", self.prose(heading))

    def test_it_names_the_page_to_open(self):
        self.assertTrue(any("open" in sentence.lower()
                            and "`site/careers.html`" in sentence
                            for sentence in self.sentences(self.SECTION)))

    def test_it_names_both_things_to_delete_afterwards(self):
        self.assertTrue(any("delete" in sentence.lower()
                            and f"`{ARCHIVE}/`" in sentence
                            and f"`{ARCHIVE_CONFIG}`" in sentence
                            for sentence in self.sentences(self.SECTION)))

    def test_the_careers_section_says_where_to_see_it(self):
        text = self.prose("### Careers across seasons")
        self.assertIn("tools/demo.py --seasons multi", text)
        self.assertIn("(#see-it-work-before-you-have-any-cards)", text)

    def test_the_list_of_commands_carries_the_option(self):
        block = readme_block(self, "## All the commands",
                             contains="tools/demo.py --seasons multi")
        self.assertTrue(any(line.startswith("python3 tools/demo.py --seasons multi")
                            for line in block))


if __name__ == "__main__":
    unittest.main()
