"""``tools/scheduled.py`` -- the command a scheduler runs instead of ``build.py``.

Before it, the README's advice for an unattended build was a cron entry or a
shortcut naming both folders in full, and three things went wrong with nobody
there to see them. Measured on the demo season before this file existed:

- an unchanged rebuild read every card again, 0.42 s for fifteen of them and
  proportionally more for an archive, every time the schedule fired;
- what the build said was printed to a terminal nobody had open, so a card that
  would not read on a Tuesday night was known about only if somebody ran it again
  by hand;
- a build that failed -- ``data/`` renamed away, emptied, or holding one card that
  would not read -- exited 2, 1 and 1 and left the published site exactly as it
  was, with nothing on it to say the figures were stale.

The wrapper notes the size and time of everything a build reads, skips when none
of it has moved (0.22 s on the same season, most of it the interpreter starting),
and keeps what each build printed in ``reports/``. The front-page sentence is
build.py's own (``tests/test_status.py``, and the build tests in
``tests/test_cli.py``); the failures only the wrapper can see, a build that
hung, one ended by a signal and one that never started, are recorded from here.

Almost every class runs ``scheduled.main`` in-process against a temp project
folder holding a stand-in ``build.py`` of a few lines, whose behaviour comes from a
plan file outside that folder: what to print, what to exit with, whether to sleep,
to add a card or to kill itself. Each run costs one tiny child process. Six
classes copy the real program into a folder whose name has a space in it, and
run the real thing: three over the demo season, and three over the six cards of
``tests/test_pipeline.py``, whose names the withholding tests know.

Rejected: putting the change detector in ``build.py`` behind an ``--if-changed``
flag. State inside the one command that has none, and a build.py that crashed or
hung would take down the code meant to record that it did.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import matchcentre                                            # noqa: E402
from matchcentre import pasteable, scorecard, site, status    # noqa: E402
import scheduled                                              # noqa: E402
from test_pipeline import write_season_cards                  # noqa: E402

SCHEDULED = ROOT / "tools" / "scheduled.py"
CLEAN = site.build_status_js(site.CLEAN).encode("utf-8")
SAID = "stand-in build.py: 1 card(s) read\n"
HEADER = re.compile(
    r"tools/scheduled\.py ran build\.py at \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\n"
    r"  because   (?P<because>.+)\n"
    r"  command   (?P<command>.+)\n"
    r"  took      (?P<took>\d+ (?:second|seconds|minutes))\n"
    r"  exit      (?P<exit>.+)\n\n")
SKIPPED = re.compile(r"nothing has changed since the build at "
                     r"\d{1,2} [A-Z][a-z]{2}, \d{1,2}:\d{2}[ap]m: exit (?P<exit>\d), "
                     r"report (?P<report>.+)\n")

# The stand-in. It never imports matchcentre, so a run costs an interpreter start
# and nothing else, and everything it does is read from the plan so that a test
# can change what the next build does without touching a fingerprinted file.
STUB = '''\
import json, os, sys, time
from pathlib import Path

plan = json.loads(Path(os.environ["STUB_PLAN"]).read_text(encoding="utf-8"))
with open(plan["log"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
for name in plan.get("add", []):
    Path(name).parent.mkdir(parents=True, exist_ok=True)
    Path(name).write_text("arrived while the build was running", encoding="utf-8")
for name, text in plan.get("write", {}).items():
    Path(name).write_text(text, encoding="utf-8")
sys.stdout.write(plan.get("say", ""))
if plan.get("utf8"):
    print("PYTHONUTF8=" + str(os.environ.get("PYTHONUTF8")), "\\u0141")
sys.stdout.flush()
if plan.get("warn"):
    sys.stderr.write(plan["warn"])
    sys.stderr.flush()
    sys.stdout.write(plan.get("then", ""))
    sys.stdout.flush()
if plan.get("sleep"):
    time.sleep(plan["sleep"])
if plan.get("kill"):
    os.kill(os.getpid(), 9)
sys.exit(plan.get("exit", 0))
'''


def _reports_listing() -> list[tuple[str, int]] | None:
    """The real checkout's reports/, which no test here may create or touch."""
    reports = ROOT / "reports"
    if not reports.exists():
        return None
    return sorted((p.name, p.stat().st_mtime_ns) for p in reports.iterdir())


class Wrapped:
    """One in-process run of the wrapper: its exit, and what it printed where."""

    def __init__(self, code: int, out: str, err: str):
        self.code, self.out, self.err = code, out, err


class StubRootCase(unittest.TestCase):
    """A project folder of its own, with a stand-in build.py, per test."""

    def setUp(self):
        self._before = _reports_listing()
        tmp = tempfile.TemporaryDirectory(prefix="scheduled-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "root"
        self.data = self.root / "data"
        self.out = self.root / "site"
        self.reports = self.root / "reports"
        self.card = self.data / "Round1" / "hawks-v-owls.pdf"
        self.card.parent.mkdir(parents=True)
        self.card.write_bytes(b"%PDF-1.4 a stand-in card\n")
        self.out.mkdir()
        (self.out / "index.html").write_text("<p>the last good build</p>\n",
                                             encoding="utf-8")
        (self.root / "matchcentre").mkdir()
        (self.root / "matchcentre" / "x.py").write_text("", encoding="utf-8")
        (self.root / "build.py").write_text(STUB, encoding="utf-8")
        self.log = self.base / "calls.log"
        self.plan_file = self.base / "plan.json"
        self.plan()
        patcher = mock.patch.dict(os.environ, {"STUB_PLAN": str(self.plan_file)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.assertEqual(_reports_listing(), self._before,
                         "a test wrote into the real checkout's reports/")

    def plan(self, **plan) -> None:
        plan.setdefault("say", SAID)
        self.plan_file.write_text(json.dumps({"log": str(self.log), **plan}),
                                  encoding="utf-8")

    def wrapper(self, *argv: str) -> Wrapped:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = scheduled.main(list(argv), root=self.root)
        return Wrapped(code, out.getvalue(), err.getvalue())

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines()]

    def kept(self) -> list[str]:
        if not self.reports.is_dir():
            return []
        return sorted((p.name for p in self.reports.iterdir()
                       if scheduled.REPORT_NAME.match(p.name)),
                      key=scheduled._report_key)

    def record(self) -> dict:
        return json.loads((self.reports / scheduled.RECORD).read_text(encoding="utf-8"))

    def record_stat(self) -> tuple[int, int]:
        found = (self.reports / scheduled.RECORD).stat()
        return found.st_ino, found.st_mtime_ns

    def report(self, name: str) -> tuple[re.Match, str]:
        """A kept report's header, parsed, and its body."""
        text = (self.reports / name).read_text(encoding="utf-8")
        header = HEADER.match(text)
        self.assertIsNotNone(header, text[:600])
        return header, text[header.end():]

    def only_line(self, run: Wrapped) -> str:
        self.assertEqual(run.out.count("\n"), 1, run.out)
        self.assertTrue(run.out.endswith("\n"), run.out)
        return run.out

    def err_line(self, run: Wrapped) -> str:
        """The one line when it says names are back: on stderr, and none on stdout."""
        self.assertEqual(run.out, "")
        self.assertEqual(run.err.count("\n"), 1, run.err)
        self.assertTrue(run.err.endswith("\n"), run.err)
        return run.err

    def built_line(self, why: str, run: Wrapped, *, code: int = 0) -> None:
        self.assertEqual(self.only_line(run),
                         f"built because {why}: exit {code}, "
                         f"report {self.reports / self.kept()[-1]}\n")

    @staticmethod
    def touch(path: Path) -> None:
        found = path.stat()
        os.utime(path, ns=(found.st_atime_ns, found.st_mtime_ns + 1_000_000_000))

    def snapshot(self, folder: Path) -> dict[str, tuple[bytes, int]]:
        return {p.relative_to(folder).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in sorted(folder.rglob("*")) if p.is_file()}

    def front_page_says(self) -> str | None:
        """The sentence the site's status file carries, or None for the clean one."""
        text = (self.out / site.STATUS_FILE).read_text(encoding="utf-8")
        return json.loads(text.split(" = ", 1)[1].rstrip(";\n")).get("text")


class TestTheFirstScheduledRunBuildsAndKeepsOneReport(StubRootCase):
    """The first run builds, and what build.py printed is kept whole, under a header.

    The header is the wrapper's, five lines, and the body is build.py's own output
    and nothing else, so the report reads the same as the terminal would have.
    """

    def check_first_run(self):
        self.plan(exit=0)
        run = self.wrapper()
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.err, "")
        self.assertEqual(len(self.kept()), 1, sorted(os.listdir(self.reports)))
        header, body = self.report(self.kept()[0])
        self.assertEqual(header["because"], "there is no record of an earlier build")
        self.assertEqual(header["command"], " ".join(
            pasteable(part) for part in [sys.executable, str(self.root / "build.py")]))
        self.assertEqual(header["exit"], "0")
        self.assertEqual(body, SAID)
        self.built_line("there is no record of an earlier build", run)
        self.assertEqual(self.record()["format"], 2)
        self.assertEqual(self.record()["names_back"], "")
        self.assertEqual(self.record()["report"], self.kept()[0])

    def test_it_builds_and_keeps_the_report(self):
        self.check_first_run()

    def test_it_does_the_same_from_a_folder_that_is_not_the_project(self):
        # Where cron and Task Scheduler start. Relative to that, data/ is nothing
        # and reports/ would be written into somebody's home folder.
        elsewhere = self.base / "where the scheduler starts"
        elsewhere.mkdir()
        here = os.getcwd()
        os.chdir(elsewhere)
        self.addCleanup(os.chdir, here)
        self.check_first_run()
        self.assertEqual(list(elsewhere.iterdir()), [])


class TestAnUnchangedFolderIsNotBuiltAgain(StubRootCase):
    """A second run with nothing moved reads no card, runs no build, writes nothing.

    Nothing at all: not a report, not the record. A quiet week leaves reports/ as it
    was, so the folder stays a history of runs that had something to say.
    """

    def test_the_second_run_is_a_skip_that_touches_nothing(self):
        first = self.wrapper()
        self.assertEqual(first.code, 0, first.err)
        listing, record = self.kept(), self.record_stat()
        recorded = (self.reports / scheduled.RECORD).read_bytes()
        second = self.wrapper()
        self.assertEqual(second.code, 0, second.err)
        self.assertEqual(len(self.calls()), 1)
        found = SKIPPED.fullmatch(self.only_line(second))
        self.assertIsNotNone(found, second.out)
        self.assertEqual(found["report"], str(self.reports / listing[0]))
        self.assertEqual(self.kept(), listing)
        self.assertEqual(self.record_stat(), record)
        self.assertEqual((self.reports / scheduled.RECORD).read_bytes(), recorded)
        self.assertEqual(sorted(os.listdir(self.reports)),
                         sorted([*listing, scheduled.RECORD]))


class TestASkippedRunExitsWithTheStatusOfTheBuildItSkipped(StubRootCase):
    """A build with problems to look at stays exit 1 until something changes.

    A scheduler that alerts on a non-zero result then keeps alerting while the
    problem stands. Rejected: a status of its own for "skipped", which Task
    Scheduler shows as a failure and which hides whether the build it skipped
    worked.
    """

    def test_a_build_that_exited_1_is_skipped_with_1(self):
        self.plan(exit=1)
        self.assertEqual(self.wrapper().code, 1)
        second = self.wrapper()
        self.assertEqual(second.code, 1)
        self.assertEqual(SKIPPED.fullmatch(second.out)["exit"], "1")
        self.assertEqual(len(self.calls()), 1)


class TestEachKindOfChangeIsNoticed(StubRootCase):
    """Every input a build reads, moved one at a time, and the reason each is given.

    One project folder, built once, then changed and rebuilt case by case; after
    each rebuild the next case starts from a folder the record matches. The cases
    that only a size or only a time would catch are there because a club's copy
    tools preserve times and a download can be rewritten at the same length.
    """

    def test_each_change_is_built_and_named(self):
        self.assertEqual(self.wrapper().code, 0)
        config = self.root / "config.toml"
        index = self.out / "index.html"

        def appended_at_the_same_time():
            found = self.card.stat()
            with open(self.card, "ab") as handle:
                handle.write(b"%%EOF\n")
            os.utime(self.card, ns=(found.st_atime_ns, found.st_mtime_ns))

        folder = "the scorecard folder changed"
        cases = [
            ("a card's time moved", lambda: self.touch(self.card), folder),
            ("a card grew and kept its time", appended_at_the_same_time, folder),
            ("a file that is not a card arrived",
             lambda: (self.data / "notes.txt").write_text("draw", encoding="utf-8"),
             folder),
            ("a card arrived two folders down", lambda: (
                (self.data / "2026" / "Round3").mkdir(parents=True),
                (self.data / "2026" / "Round3" / "kites-v-wrens.pdf").write_bytes(
                    b"%PDF-1.4 another\n")), folder),
            ("a card was taken away", self.card.unlink, folder),
            ("a settings file was made where there was none",
             lambda: config.write_text("[club]\n", encoding="utf-8"),
             "the settings file changed"),
            ("the settings were edited",
             lambda: config.write_text("[club]\nname = 'Hawks'\n", encoding="utf-8"),
             "the settings file changed"),
            ("a dashboard file was edited",
             lambda: index.write_text("<p>edited by hand</p>\n", encoding="utf-8"),
             "the dashboard folder changed"),
            ("a dashboard file was deleted", index.unlink,
             "the dashboard folder changed"),
            ("the program changed",
             lambda: self.touch(self.root / "matchcentre" / "x.py"),
             "the program changed"),
        ]
        for name, change, why in cases:
            with self.subTest(name):
                calls = len(self.calls())
                change()
                self.plan(say=f"built after {name}\n")    # so each keeps a report
                run = self.wrapper()
                self.assertEqual(len(self.calls()), calls + 1, run.out)
                self.assertEqual(self.report(self.kept()[-1])[0]["because"], why)
                self.assertTrue(run.out.startswith(f"built because {why}: "), run.out)

        with self.subTest("it was given a different --out"):
            # A copy of the site, times and all, so that only the folder's name
            # differs and the site's own digest cannot be what noticed.
            shutil.copytree(self.out, self.root / "published")
            run = self.wrapper("--out", "published")
            self.assertTrue(run.out.startswith(
                "built because it was given different folders: "), run.out)

        with self.subTest("and then nothing moved"):
            calls = len(self.calls())
            self.assertRegex(self.wrapper("--out", "published").out,
                             "^nothing has changed")
            self.assertEqual(len(self.calls()), calls)


class TestTheDetectorWalksTheFilesTheReaderWalks(unittest.TestCase):
    """What is watched is the reader's own list, so the two cannot drift apart.

    Every file and not only the PDFs: a stray ``.txt`` or an unfinished download is
    named in the report, so a report that would change has to count as a change.
    Rejected: the wrapper's own ``rglob("*.pdf")``, a second walk that already
    differs from the reader's on ``.PDF``, on ``card.pdf.part`` and on strays.
    """

    def test_the_data_digest_lists_exactly_what_files_under_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            (data / "nested" / "deeper").mkdir(parents=True)
            for name in ("a.pdf", "B.PDF", "x.crdownload", "card.pdf.part",
                         "notes.txt", "nested/deeper/c.pdf"):
                (data / name).write_bytes(b"not really a card\n")
            elsewhere = Path(tmp) / "kept-elsewhere.pdf"
            elsewhere.write_bytes(b"not really a card either\n")
            try:
                (data / "linked.pdf").symlink_to(elsewhere)
            except (OSError, NotImplementedError):
                pass                                   # no symlinks here; the rest stands
            lines = scheduled._data_lines(data)
            listed = [line.split("\t", 1)[0] for line in lines]
            walked = [p.relative_to(data).as_posix()
                      for p in scorecard.files_under(data)]
            self.assertEqual(listed, walked)
            _, refused = scorecard.read_tree(data)
            named = {Path(r.path).relative_to(data).as_posix() for r in refused}
            named |= {p.relative_to(data).as_posix()
                      for p in scorecard.unread_files(data)}
            self.assertLessEqual(named, set(listed))
            self.assertIn("nested/deeper/c.pdf", listed)
            self.assertIn("notes.txt", listed)

    def test_a_folder_that_is_not_there_is_one_line_that_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scheduled._data_lines(Path(tmp) / "missing"),
                             ["unlisted\n"])

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0,
                     "a mode this account cannot list needs POSIX and a user not root")
    def test_a_folder_that_cannot_be_listed_is_unlisted_and_not_empty(self):
        # Measured before: ``files_under`` of a mode-000 folder was [], because
        # ``rglob`` skips what it may not list, so the folder was watched as an
        # empty one and a build that found it "empty" was skipped until the mode
        # changed. Now the reader opens it first and there is no list at all.
        with tempfile.TemporaryDirectory() as tmp:
            empty, shut = Path(tmp) / "empty", Path(tmp) / "shut"
            empty.mkdir()
            shut.mkdir()
            (shut / "a.pdf").write_bytes(b"not really a card\n")
            shut.chmod(0)
            try:
                self.assertIsNone(scorecard.files_under(shut))
                self.assertEqual(scheduled._data_lines(shut), ["unlisted\n"])
                self.assertEqual(scheduled._data_lines(empty), [])
            finally:
                shut.chmod(0o755)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0,
                     "a mode this account cannot list needs POSIX and a user not root")
    def test_a_folder_under_it_that_cannot_be_listed_is_watched_by_its_own_name(self):
        # Measured before on the demo, with an empty data/demo-season/spare at mode
        # 000: the digest lines were the same as with it listable, so a scheduled
        # run went on skipping a build that would now refuse it. The folder is in
        # the list in its files' place, so a change of mode is a change either way.
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            (data / "spare").mkdir(parents=True)
            (data / "a.pdf").write_bytes(b"not really a card\n")
            listable = scheduled._data_lines(data)
            (data / "spare").chmod(0)
            try:
                self.assertEqual(scorecard.files_under(data),
                                 [data / "a.pdf", data / "spare"])
                unlisted = scheduled._data_lines(data)
            finally:
                (data / "spare").chmod(0o755)
            self.assertEqual([line.split("\t", 1)[0] for line in listable],
                             ["a.pdf"])
            self.assertEqual([line.split("\t", 1)[0] for line in unlisted],
                             ["a.pdf", "spare"])

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0,
                     "a mode this account cannot list needs POSIX and a user not root")
    def test_a_folder_it_could_not_list_is_in_the_list_when_it_is_all_there_is(self):
        # Only data/ itself unlisted is no list. Tried as "no list when no file was
        # found" (/tmp/mutate292.py): data/ holding one shut folder was watched as
        # "unlisted", the line an unlistable data/ gets, and the folders were watched
        # after the files, out of the order the reader lists them in.
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            (data / "spare").mkdir(parents=True)
            (data / "spare").chmod(0)
            try:
                alone = scorecard.files_under(data)
                (data / "z.pdf").write_bytes(b"not really a card\n")
                beside = scorecard.files_under(data)
            finally:
                (data / "spare").chmod(0o755)
            self.assertEqual(alone, [data / "spare"])
            self.assertEqual(beside, [data / "spare", data / "z.pdf"])


class TestACardThatArrivesDuringABuildIsBuiltNextTime(StubRootCase):
    """The inputs are noted before the build, so a card landing mid-build is new.

    Rejected: noting them after, which records the late card as seen by a build
    that never read it, and skips it until something else happens to move.
    """

    def test_the_run_after_is_a_build_and_the_one_after_that_is_not(self):
        late = self.data / "Round2" / "late-v-card.pdf"
        self.plan(add=[str(late)])
        self.wrapper()
        self.assertTrue(late.exists())
        self.plan()
        run = self.wrapper()
        self.assertEqual(len(self.calls()), 2, run.out)
        self.assertTrue(run.out.startswith("built because the scorecard folder "
                                           "changed: "), run.out)
        self.assertRegex(self.wrapper().out, "^nothing has changed")


class TestABuildThatCouldNotRunIsRetriedEveryRun(StubRootCase):
    """An exit of 2 is never skipped, and saying the same thing again is not a report.

    A permission refused or a drive that did not mount moves no file, so a skip
    would repeat the failure until a card happened to change. Rejected: treating 2
    like 0 and 1.
    """

    def test_both_runs_build_and_the_second_report_is_the_first(self):
        self.plan(exit=2)
        self.assertEqual(self.wrapper().code, 2)
        first, record = self.kept(), self.record_stat()
        second = self.wrapper()
        self.assertEqual(second.code, 2)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.only_line(second),
                         f"built because the last build could not run: exit 2, "
                         f"same report as {self.reports / first[0]}\n")
        self.assertEqual(self.kept(), first)
        self.assertNotEqual(self.record_stat(), record)
        self.assertEqual(self.record()["report"], first[0])


@unittest.skipIf(os.name == "nt", "a POSIX signal")
class TestABuildThatEndedAbnormallyIsNeverSkipped(StubRootCase):
    """A build killed by a signal is a 2, is said to be one, and is built again.

    And the front page says it did not finish. Measured before: a build.py killed
    part-way (SIGKILL, as the out-of-memory killer or a shutdown sends it) left the
    status file on the clean ``{"level": "ok"}`` from the build before, because
    build.py never reached a line that could record anything and the wrapper
    recorded only the build it stopped itself. Rejected: "an internal error
    stopped it", which is a bug in this program by the README's own table, and
    nothing here says it was one.
    """

    def test_a_killed_build_is_2_and_the_next_run_builds(self):
        (self.out / site.STATUS_FILE).write_bytes(CLEAN)
        self.plan(kill=True)
        run = self.wrapper()
        self.assertEqual(self.front_page_says(), status.failed("unfinished"))
        self.assertEqual(run.code, 2)
        header, body = self.report(self.kept()[0])
        self.assertEqual(header["exit"], "2 (build.py ended with status -9)")
        self.assertEqual(body, SAID + "-- build.py ended here, with status -9 --\n")
        self.assertEqual(self.record()["exit"], -9)
        self.assertEqual(self.only_line(run),
                         f"built because there is no record of an earlier build: "
                         f"exit 2, report {self.reports / self.kept()[0]}\n")
        self.plan()
        self.assertTrue(self.wrapper().out.startswith(
            "built because the last build could not run: "))
        self.assertEqual(len(self.calls()), 2)


class TestARunThatHangsIsStoppedAndRecorded(StubRootCase):
    """A build past ``--timeout`` is stopped, reported, and written on the front page.

    A failure build.py cannot record itself. A hung read on a network share
    would otherwise stack one more process every fifteen minutes, and nobody would
    know. Measured: ``--timeout 0.01`` stops a 30-second stand-in in 0.6 s.
    """

    def test_it_is_stopped_and_every_trace_of_it_says_so(self):
        (self.out / site.STATUS_FILE).write_bytes(CLEAN)
        before = self.snapshot(self.out)
        self.plan(sleep=30)
        run = self.wrapper("--timeout", "0.01")
        self.assertEqual(run.code, 2, run.err)
        header, body = self.report(self.kept()[0])
        self.assertEqual(header["exit"], "2 (build.py was stopped after 0.01 minutes)")
        self.assertEqual(body, SAID + "-- build.py was stopped here, after 0.01 "
                                      "minutes --\n")
        self.assertEqual(self.only_line(run),
                         f"stopped a build that ran past 0.01 minutes: exit 2, "
                         f"report {self.reports / self.kept()[0]}\n")
        self.assertIsNone(self.record()["exit"])
        after = self.snapshot(self.out)
        self.assertEqual({name for name in {*before, *after}
                          if before.get(name) != after.get(name)},
                         {site.STATUS_FILE})
        said = json.loads(after[site.STATUS_FILE][0].decode("utf-8").split(" = ", 1)[1]
                          .rstrip(";\n"))
        self.assertEqual(said["text"], status.failed("too-long", 0.01))
        self.assertIn("it ran for more than 0.01 minutes and was stopped", said["text"])
        self.plan()
        again = self.wrapper("--timeout", "0.01")
        self.assertTrue(again.out.startswith(
            "built because the last build did not finish: "), again.out)


class TestAFailedStartIsRecorded(StubRootCase):
    """A Python that cannot be started is a 2 with a report, not a traceback.

    And a line on the front page: measured before, the status file stayed on the
    clean one, so a schedule whose Python had gone built nothing night after night
    under a page that said nothing.
    """

    def test_it_is_a_2_with_a_one_line_body_and_the_next_run_builds(self):
        (self.out / site.STATUS_FILE).write_bytes(CLEAN)
        missing = str(self.base / "no-such-python")
        with mock.patch.object(scheduled, "_child_python", return_value=missing):
            run = self.wrapper()
        self.assertEqual(self.front_page_says(), status.failed("unfinished"))
        self.assertEqual(run.code, 2, run.err)
        header, body = self.report(self.kept()[0])
        self.assertEqual(header["exit"], "2 (build.py could not be started)")
        self.assertEqual(body.count("\n"), 1, body)
        self.assertTrue(body.startswith("build.py could not be started: "), body)
        self.assertIsNone(self.record()["exit"])
        self.assertEqual(self.only_line(run),
                         f"could not start build.py: exit 2, "
                         f"report {self.reports / self.kept()[0]}\n")
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.wrapper().out.startswith(
            "built because the last build did not finish: "))


class TestAReportTheSameAsTheLastIsNotWrittenTwice(StubRootCase):
    """Two runs whose output differs only in the header are one report.

    Compared with the newest report's body and nothing else. Rejected: always
    writing, which fills thirty slots in eight hours with a failure retried every
    fifteen minutes and pushes out the last report that said anything different.
    """

    def test_the_same_body_is_named_and_a_different_one_is_written(self):
        config = self.root / "config.toml"
        config.write_text("[club]\n", encoding="utf-8")
        self.plan(say="the first thing it said\n")
        self.wrapper()
        first = self.kept()
        self.touch(config)
        run = self.wrapper()
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.only_line(run),
                         f"built because the settings file changed: exit 0, "
                         f"same report as {self.reports / first[0]}\n")
        self.assertEqual(self.kept(), first)
        self.assertEqual(self.record()["report"], first[0])

        self.plan(say="something new to say\n")
        self.touch(config)
        self.wrapper()
        second = self.kept()
        self.assertEqual(len(second), 2)
        self.assertEqual(self.report(second[-1])[1], "something new to say\n")

        # Against the newest, not the oldest: this body is the second's.
        self.touch(config)
        run = self.wrapper()
        self.assertEqual(self.kept(), second)
        self.assertTrue(run.out.endswith(f"same report as "
                                         f"{self.reports / second[-1]}\n"), run.out)


class TestOnlyTheNewestReportsAreKept(unittest.TestCase):
    """Retention by count, newest last by the name's time and then its number.

    ``_10`` is newer than ``_2``, which a sort by name gets backwards, and nothing
    the tool did not name is ever deleted: not the record, not a club's notes, not
    a file that merely looks like a date.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.reports = Path(tmp.name)
        self.older = [f"2026-09-01_00000{n}.txt" for n in (1, 2, 3)]
        self.same = ["2026-09-02_120000.txt", "2026-09-02_120000_2.txt",
                     "2026-09-02_120000_10.txt"]
        self.newer = [f"2026-09-03_{n:06d}.txt" for n in range(29)]
        self.others = ["notes.txt", "2026-09-24.txt", "last-run.json"]
        for name in [*self.older, *self.same, *self.newer, *self.others]:
            (self.reports / name).write_text(name, encoding="utf-8")

    def test_the_newest_thirty_remain_and_nothing_else_is_touched(self):
        scheduled.prune_reports(self.reports, 30)
        self.assertEqual(sorted(os.listdir(self.reports)),
                         sorted(["2026-09-02_120000_10.txt", *self.newer,
                                 *self.others]))

    def test_a_report_that_cannot_be_deleted_is_a_warning_and_the_rest_go(self):
        real = Path.unlink

        def unlink(path, missing_ok=False):
            if path.name == self.older[0]:
                raise PermissionError(13, "Permission denied", str(path))
            return real(path, missing_ok)

        err = io.StringIO()
        with mock.patch.object(Path, "unlink", unlink), contextlib.redirect_stderr(err):
            scheduled.prune_reports(self.reports, 30)
        self.assertEqual(err.getvalue(),
                         f"warning: could not remove old report "
                         f"{self.reports / self.older[0]}: [Errno 13] Permission "
                         f"denied: '{self.reports / self.older[0]}'\n")
        self.assertFalse((self.reports / self.older[1]).exists())
        self.assertTrue((self.reports / self.older[0]).exists())


class TestTwoBuildsInTheSameSecondKeepTwoReports(unittest.TestCase):
    """A second report stamped in the same second as the first is ``_2``, not it.

    The name is the time to the second, and a club that runs the command by hand
    just after the scheduler did can start two builds in one. The file is opened
    to be created, never to be replaced, so the first build's report is still the
    first build's. Rejected: opening it to write, which gives both runs the same
    name and keeps only what the second said.
    """

    def test_the_second_is_numbered_and_the_first_is_left_as_it_was(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            started = datetime(2026, 9, 24, 4, 0, 2)
            first = scheduled.write_report(reports, started=started, header="h\n",
                                           body="one\n")
            second = scheduled.write_report(reports, started=started, header="h\n",
                                            body="two\n")
            self.assertEqual(first, (reports / "2026-09-24_040002.txt", True))
            self.assertEqual(second, (reports / "2026-09-24_040002_2.txt", True))
            self.assertEqual(first[0].read_text(encoding="utf-8"), "h\n\none\n")
            self.assertEqual(scheduled.newest_report(reports), second[0])


class TestTheArgumentsItRefusesCreateNothing(StubRootCase):
    """A refused flag is a 2 with one sentence, before any folder is made.

    Checked after parsing rather than as argparse types, whose refusal reads
    ``argument --keep: --keep must ...``, the flag named twice in one line.
    """

    REFUSED = {
        ("--keep", "1"): "--keep must be at least 2: the report before the newest "
                         "is the one to compare it with",
        ("--keep", "0"): "--keep must be at least 2: the report before the newest "
                         "is the one to compare it with",
        ("--timeout", "0"): "--timeout must be more than 0 minutes",
        ("--timeout", "-5"): "--timeout must be more than 0 minutes",
        ("--timeout", "nan"): "--timeout must be a number of minutes, not nan",
        ("--timeout", "inf"): "--timeout must be a number of minutes, not inf",
    }

    @staticmethod
    def refusal(err: str) -> str:
        """The refusal *err* ends with, its lines put back on one: the parser fills
        it to the width, and ``--keep 1``'s was 112 columns on one line."""
        lines = err.splitlines()
        at = max(n for n, line in enumerate(lines)
                 if line.startswith("tools/scheduled.py: error: "))
        return " ".join(line.strip() for line in lines[at:])

    def test_each_is_refused_at_exit_2_and_nothing_is_created(self):
        for argv, said in self.REFUSED.items():
            with self.subTest(argv=argv):
                run = self.wrapper(*argv)
                self.assertEqual(run.code, 2)
                self.assertEqual(run.out, "")
                self.assertEqual(self.refusal(run.err),
                                 f"tools/scheduled.py: error: {said}")
                self.assertFalse(self.reports.exists())
                self.assertEqual(self.calls(), [])

    def test_the_real_command_refuses_too(self):
        done = subprocess.run((sys.executable, str(SCHEDULED), "--keep", "1"),
                              capture_output=True, text=True, timeout=120,
                              cwd=self.base,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertEqual(self.refusal(done.stderr),
                         f"tools/scheduled.py: error: {self.REFUSED[('--keep', '1')]}")


class TestAnEmptyPathIsRefusedBeforeAnythingIsMade(StubRootCase):
    """``--config ""`` and the other three are a 2 and one sentence, and no schedule.

    An empty path is the project folder itself (``Path("")`` is ``.``), and it is
    what a script passes when the variable it names a folder by came out empty.
    Measured before (/tmp/t7/scratch/SC/before.txt, before2.txt, before3.txt):
    ``--config ""`` exited 2 on every run, the report saying ``error: .: cannot be
    read — [Errno 21] Is a directory: '.'`` and the second run's line "built
    because the last build could not run; the settings file changed", and
    ``--print-schedule --config ""`` exited 0 with a line naming the project
    folder as the settings file. ``--data ""`` and ``--out ""`` were refused only
    because the project folder holds ``reports/``: beside a ``--reports`` of their
    own both exited 0, the one reading the program's own folder as its cards and
    the other writing the dashboard in beside build.py. ``--reports ""`` beside
    folders of their own kept the report and the record in the project folder.
    Refused as ``--keep`` is, once parsed, so nothing is made, no schedule is
    printed, and the overlap it once met is not said as a second mistake.
    Rejected: reading an empty flag as no flag, which builds from folders the club
    did not name; and refusing it once resolved, when it is the project folder and
    cannot be told from ``.`` typed on purpose.
    """

    REFUSED = {
        "--data": "--data is empty: name a folder, or leave it out",
        "--out": "--out is empty: name a folder, or leave it out",
        "--config": "--config is empty: name a file, or leave it out",
        "--reports": "--reports is empty: name a folder, or leave it out",
    }

    def refused(self, *argv: str, said: str) -> None:
        before = sorted(p.relative_to(self.base).as_posix()
                        for p in self.base.rglob("*"))
        run = self.wrapper(*argv)
        self.assertEqual(run.code, 2, run.out)
        self.assertEqual(run.out, "")
        self.assertEqual(run.err.splitlines()[-1], f"tools/scheduled.py: error: {said}")
        self.assertEqual(run.err.count("error:"), 1, run.err)
        self.assertEqual(sorted(p.relative_to(self.base).as_posix()
                                for p in self.base.rglob("*")), before)
        self.assertEqual(self.calls(), [])

    def test_each_is_refused_in_one_line_and_nothing_is_created(self):
        for flag, said in self.REFUSED.items():
            with self.subTest(flag):
                self.refused(flag, "", said=said)

    def test_beside_folders_of_their_own_they_are_refused_all_the_same(self):
        # Where each got through before: the overlap with reports/ was all that
        # refused --data and --out, and --reports got through with the cards and
        # the site outside the project folder.
        cards = self.base / "cards"
        shutil.copytree(self.data, cards)
        elsewhere = {"--data": ["--reports", str(self.base / "rep")],
                     "--out": ["--reports", str(self.base / "rep")],
                     "--config": ["--reports", str(self.base / "rep")],
                     "--reports": ["--out", str(self.base / "pub"),
                                   "--data", str(cards)]}
        for flag, said in self.REFUSED.items():
            with self.subTest(flag):
                self.refused(flag, "", *elsewhere[flag], said=said)

    def test_no_schedule_is_printed_for_one(self):
        for flag, said in self.REFUSED.items():
            with self.subTest(flag):
                self.refused("--print-schedule", flag, "", said=said)

    def test_a_dot_typed_on_purpose_is_not_called_empty(self):
        # "." names the project folder, and is judged by where it points: alone it
        # overlaps reports/, and beside reports of its own it is passed on as typed.
        run = self.wrapper("--data", ".")
        self.assertEqual(run.code, 2)
        self.assertEqual(run.err.splitlines()[0],
                         f"error: --reports {self.reports} and --data {self.root} "
                         f"overlap.")
        run = self.wrapper("--data", ".", "--reports", str(self.base / "rep"))
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.calls(), [["--data", "."]])

    def test_the_real_command_refuses_to_print_a_schedule_for_one(self):
        done = subprocess.run((sys.executable, str(SCHEDULED), "--print-schedule",
                               "--config", ""),
                              capture_output=True, text=True, timeout=120,
                              cwd=self.base,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr.splitlines()[-1],
                         f"tools/scheduled.py: error: {self.REFUSED['--config']}")


class TestReportsThatOverlapTheSiteOrTheCardsAreRefused(StubRootCase):
    """No report inside what is published, and none inside what is read.

    In either direction: a reports folder that holds the site is a web root, and is
    likely published too. Rejected: checking only "inside".
    """

    def refusal(self, reports: Path, flag: str, other: Path) -> str:
        why = ("A report names every player, and --out is the folder that gets "
               "published." if flag == "--out" else
               "That folder is read on every build.")
        import textwrap
        return (f"error: --reports {reports} and {flag} {other} overlap.\n"
                + textwrap.fill(f"{why} Keep the reports in a folder of their own "
                                f"beside it.", width=78, initial_indent="  ",
                                subsequent_indent="  ") + "\n")

    def test_each_overlap_is_refused_with_both_folders_named(self):
        cases = [
            ("the site itself", ["--reports", "site"], "--out", "site"),
            ("inside the site", ["--reports", "site/reports"], "--out", "site"),
            ("holding the site", ["--out", "site/pub", "--reports", "site"],
             "--out", "site/pub"),
            ("the cards themselves", ["--reports", "data"], "--data", "data"),
            ("inside the cards", ["--reports", "data/reports"], "--data", "data"),
            ("holding the cards", ["--data", "cards/2026", "--reports", "cards"],
             "--data", "cards/2026"),
        ]
        for name, argv, flag, other in cases:
            with self.subTest(name):
                before = sorted(p.relative_to(self.root).as_posix()
                                for p in self.root.rglob("*"))
                run = self.wrapper(*argv)
                self.assertEqual(run.code, 2)
                self.assertEqual(run.out, "")
                reports = self.root / argv[argv.index("--reports") + 1]
                self.assertEqual(run.err, self.refusal(reports, flag,
                                                       self.root / other))
                self.assertEqual(sorted(p.relative_to(self.root).as_posix()
                                        for p in self.root.rglob("*")), before)
                self.assertEqual(self.calls(), [])

    def test_the_schedule_is_not_printed_for_a_line_that_would_be_refused(self):
        # A line refused on every run is a line that fails every fifteen minutes
        # into a mailbox nobody reads, so it is refused before it is printed.
        run = self.wrapper("--print-schedule", "--reports", "site")
        self.assertEqual(run.code, 2)
        self.assertEqual(run.out, "")
        self.assertTrue(run.err.startswith("error: --reports "), run.err)


class TestAnUnreadableRecordCostsOneRebuild(StubRootCase):
    """Anything that is not a whole record this version wrote is no record at all.

    None costs one build. Anything else, trusted wrongly, could skip one.
    """

    def test_each_bad_record_is_a_build_with_no_record_and_no_traceback(self):
        self.wrapper()
        path = self.reports / scheduled.RECORD
        good = self.record()
        bad = {
            "not JSON": "{\"exit\": 0,",
            "a format from a later version": json.dumps(
                {**good, "format": scheduled.FORMAT + 1}),
            "a record of the format before, which says nothing of names": json.dumps(
                {**{key: value for key, value in good.items() if key != "names_back"},
                 "format": 1}),
            "where names went back not a string": json.dumps(
                {**good, "names_back": None}),
            "a format that is true": json.dumps({**good, "format": True}),
            "an exit that is a word": json.dumps({**good, "exit": "0"}),
            "an exit that is false": json.dumps({**good, "exit": False}),
            "a digest missing": json.dumps({**good, "inputs": {
                key: value for key, value in good["inputs"].items()
                if key != "program"}}),
            "a report that is not a report's name": json.dumps(
                {**good, "report": "../../elsewhere.txt"}),
            "a time that is not a time": json.dumps({**good, "finished": "soon"}),
            "a list": json.dumps([good]),
        }
        for name, text in bad.items():
            with self.subTest(name):
                calls = len(self.calls())
                path.write_text(text, encoding="utf-8")
                run = self.wrapper()
                self.assertEqual(run.code, 0, run.err)
                self.assertEqual(run.err, "")
                self.assertEqual(len(self.calls()), calls + 1)
                self.assertTrue(run.out.startswith(
                    "built because there is no record of an earlier build: "), run.out)

        with self.subTest("a folder where the record should be"):
            path.unlink()
            path.mkdir()
            run = self.wrapper()
            self.assertEqual(run.code, 0)
            self.assertTrue(run.out.startswith(
                "built because there is no record of an earlier build: "), run.out)
            self.assertTrue(run.err.startswith(
                f"warning: {path}: cannot be written — "), run.err)
            self.assertEqual(run.err.count("\n"), 1, run.err)
            # The half-written copy goes too, or every run leaves one behind.
            self.assertFalse((self.reports / f"{scheduled.RECORD}.tmp").exists())


class TestWhatBuildPyPrintsOnStderrIsKeptInTheReportInItsPlace(StubRootCase):
    """Every ``error:`` line build.py prints is on stderr, and the report keeps it.

    Nothing asked before: the stand-in printed only to stdout, and a wrapper that
    threw build.py's stderr away passed every test here, while the one line a
    report exists to keep -- why the build stopped -- went nowhere. In the place it
    was printed, so the report reads as the terminal would have.
    """

    def test_the_error_line_is_in_the_report_between_what_came_either_side(self):
        warn, then = "error: the card that stopped it\n", "after the error\n"
        self.plan(warn=warn, then=then, exit=2)
        run = self.wrapper()
        self.assertEqual(run.code, 2, run.err)
        _, body = self.report(self.kept()[0])
        self.assertEqual(body, SAID + warn + then)
        self.assertNotIn(warn, run.out + run.err)


class TestTheReportsFolderThatCannotBeUsedIsAPlainError(StubRootCase):
    """A reports folder that cannot be made, or written in, is a 2 and one line."""

    def test_a_file_where_the_folder_should_be_is_refused_before_building(self):
        self.reports.write_text("somebody's file\n", encoding="utf-8")
        run = self.wrapper()
        self.assertEqual(run.code, 2)
        self.assertEqual(run.out, "")
        self.assertTrue(run.err.startswith(f"error: {self.reports}: cannot be "
                                           f"created — "), run.err)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.reports.read_text(encoding="utf-8"), "somebody's file\n")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root writes through permissions")
    def test_a_folder_it_cannot_write_in_is_an_error_not_a_traceback(self):
        self.reports.mkdir()
        self.reports.chmod(0o555)
        self.addCleanup(self.reports.chmod, 0o755)
        run = self.wrapper()
        self.assertEqual(run.code, 2)
        self.assertEqual(run.out, "")
        self.assertTrue(run.err.startswith(f"error: {self.reports}: cannot be "
                                           f"written — "), run.err)


class TestNoScheduleIsPrintedForAReportsFolderEveryRunWouldRefuse(StubRootCase):
    """``--print-schedule`` refuses the reports folder the run itself would refuse.

    Measured before: ``--print-schedule --reports afile``, with ``afile`` a file,
    exited 0 and printed a cron line, and every run of that line then exited 2 with
    "cannot be created -- [Errno 17] File exists"; so did a folder under a parent
    this account may not write in, with [Errno 13]. Only the overlap refusal ran
    before printing. Now the same ``error:`` line the run would print comes first,
    found without creating anything, since printing a schedule makes no folder.
    Rejected: creating reports/ to see whether it can be, which leaves a folder
    behind a command that promises only to print.
    """

    def refused(self, reports: Path, verb: str) -> None:
        before = sorted(p.relative_to(self.base).as_posix()
                        for p in self.base.rglob("*"))
        run = self.wrapper("--print-schedule", "--reports", str(reports))
        self.assertEqual(run.code, 2)
        self.assertEqual(run.out, "")
        self.assertEqual(run.err.count("\n"), 1, run.err)
        self.assertTrue(run.err.startswith(f"error: {reports}: cannot be {verb} — "),
                        run.err)
        self.assertEqual(sorted(p.relative_to(self.base).as_posix()
                                for p in self.base.rglob("*")), before)

    def test_a_file_where_the_folder_should_be(self):
        self.reports.write_text("somebody's file\n", encoding="utf-8")
        self.refused(self.reports, "created")
        self.refused(self.reports / "under-it", "created")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root writes through permissions")
    def test_a_folder_it_could_not_create_or_could_not_write_in(self):
        shut = self.base / "shut"
        shut.mkdir()
        shut.chmod(0o555)
        self.addCleanup(shut.chmod, 0o755)
        self.refused(shut / "reports", "created")
        self.refused(shut / "reports" / "deeper", "created")
        self.refused(shut, "written")

    def test_a_folder_that_is_not_there_yet_is_printed_and_not_made(self):
        run = self.wrapper("--print-schedule", "--reports",
                           str(self.base / "new" / "reports"))
        self.assertEqual(run.code, 0, run.err)
        self.assertFalse((self.base / "new").exists())


class TestTheWrapperPrintsExactlyOneLine(StubRootCase):
    """One line whatever happened, so a scheduler's log can grep it.

    On stdout, but for the line that says players' names are back, which is on
    stderr (:class:`TestABuildThatPutNamesBackExits1WithItsLineOnStderr`).
    """

    def test_a_build_a_skip_a_repeat_a_timeout_and_a_failed_start(self):
        self.only_line(self.wrapper())
        self.only_line(self.wrapper())
        self.touch(self.card)
        self.assertIn("same report as", self.only_line(self.wrapper()))
        self.touch(self.card)
        self.plan(sleep=30)
        self.assertIn("stopped a build", self.only_line(self.wrapper("--timeout",
                                                                     "0.01")))
        with mock.patch.object(scheduled, "_child_python",
                               return_value=str(self.base / "no-such-python")):
            self.assertIn("could not start", self.only_line(self.wrapper()))


class NamesBackCase(StubRootCase):
    """A stub project whose site holds dashboards that withheld names, or did not."""

    def dashboard(self, name: str, players: str | None) -> Path:
        folder = self.out / name
        folder.mkdir()
        (folder / "season-data.js").write_text(site.GENERATED_MARK + "\n",
                                               encoding="utf-8")
        (folder / "season.json").write_text(self.document(players), encoding="utf-8")
        return folder / "season.json"

    @staticmethod
    def document(players: str | None) -> str:
        meta = {"competition": "x", **({"players": players} if players else {})}
        return json.dumps({"meta": meta})


class TestTheLineSaysWhenABuildPutNamesBack(NamesBackCase):
    """A build that prints names where a dashboard withheld them says so on the line.

    Measured before (the README's public copy, /tmp/t4/scratch/f2/before-probe.log):
    a folder built with ``--players numbered`` and then scheduled with a settings
    file that withholds nothing exited 0 with the plain line, while two of its files
    named every player. Only the report's ``players`` line said "withheld them
    until this run", and nobody opens the report of a build that exited 0. The
    line reads it from what the build records where a program can read it, each
    dashboard's ``meta.players`` before the build and after, by
    :func:`site.players_of`, the rule the report's own line is read by. Rejected:
    parsing the report's prose, which wraps. The line is on stderr and the exit 1:
    see :class:`TestABuildThatPutNamesBackExits1WithItsLineOnStderr`.
    """

    def test_only_the_dashboards_that_withheld_them_are_counted(self):
        # Withheld either way: a rule that counted only "numbered" would name "a"
        # alone. Measured before, with every dashboard put back a numbered one, that
        # rule passed this whole file (/tmp/t4/scratch/f2/fixup/before-J.log).
        back = [self.dashboard("a", "numbered"), self.dashboard("b", "initials")]
        still = self.dashboard("c", "initials")
        named = self.dashboard("d", None)
        self.plan(write={str(path): self.document(None) for path in (*back, named)})
        run = self.wrapper()
        self.assertEqual(run.code, 1, run.err)
        self.assertEqual(self.err_line(run),
                         f"built because there is no record of an earlier build: "
                         f"exit 1, report {self.reports / self.kept()[-1]}; players' "
                         f"names are back in 2 dashboard(s) under {self.out}, which "
                         f"withheld them until this run\n")
        self.assertEqual(json.loads(still.read_text(encoding="utf-8"))["meta"]
                         ["players"], "initials")

    def test_one_dashboard_is_named(self):
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): self.document(None)})
        self.assertTrue(self.err_line(self.wrapper()).endswith(
            f"; players' names are back in {back.parent}, which withheld them until "
            f"this run\n"))

    def test_a_document_that_no_longer_reads_is_not_said_to_print_names(self):
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): "{"})
        run = self.wrapper()
        self.assertNotIn("names are back", self.only_line(run))
        self.assertEqual(run.code, 0, run.err)

    def test_the_readme_quotes_the_clause_as_it_is_printed(self):
        # "Checking it is running" quotes it for a project in /opt, as it quotes
        # the two lines above it.
        back = self.dashboard("tuesday-division-b", "numbered")
        self.plan(write={str(back): self.document(None)})
        line = self.err_line(self.wrapper())
        said = line[line.index("; players'"):].rstrip("\n").replace(
            str(self.out), "/opt/matchcentre-dashboard/public")
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn(f"`{said}`", readme)


class TestABuildThatPutNamesBackExits1WithItsLineOnStderr(NamesBackCase):
    """Names printed where a dashboard withheld them are exit 1, said on stderr.

    Measured before (/tmp/t7/scratch/SC/before.txt), on the README's public copy
    built by hand with ``--players numbered`` and then scheduled with a settings
    file that sets no ``[build] players``: exit 0, the line and its clause on
    stdout, stderr empty and ``build-status.js`` at ``{"level": "ok"}``. Run
    twice as cron runs the printed line, which ends ``>/dev/null``, it exited 0
    and left cron nothing to mail; under ``pythonw.exe`` there is no stream at
    all. Settled by the owner as a non-zero exit: 1, a build with problems to
    look at, whatever build.py found besides, and 2 still for a build that could
    not run. On stderr, which ``>/dev/null`` keeps and cron mails; the exit is
    what Task Scheduler shows. The report's header gives the wrapper's exit and
    why it is not build.py's, as it does for a build it stopped. Rejected: an
    exit of 3, outside the three the README promises; and marking only the front
    page, whose sentence is for a build that failed.
    """

    def test_a_clean_build_that_put_them_back_is_1(self):
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): self.document(None)})
        run = self.wrapper()
        self.assertEqual(run.code, 1)
        self.assertEqual(self.err_line(run),
                         f"built because there is no record of an earlier build: "
                         f"exit 1, report {self.reports / self.kept()[-1]}; players' "
                         f"names are back in {back.parent}, which withheld them until "
                         f"this run\n")
        header, body = self.report(self.kept()[-1])
        self.assertEqual(header["exit"],
                         "1 (build.py exited 0, but players' names are back)")
        self.assertEqual(body, SAID)
        self.assertEqual(self.record()["exit"], 0)
        self.assertEqual(self.record()["names_back"], str(back.parent))

    def test_a_build_with_problems_stays_1_and_one_that_could_not_run_stays_2(self):
        for code in (1, 2):
            with self.subTest(code=code):
                back = self.dashboard(f"exit-{code}", "numbered")
                self.plan(write={str(back): self.document(None)}, exit=code,
                          say=f"stand-in build.py: exit {code}\n")
                run = self.wrapper()
                self.assertEqual(run.code, code)
                line = self.err_line(run)
                self.assertIn(f": exit {code}, report {self.reports / self.kept()[-1]}"
                              f"; players' names are back in {back.parent}, which "
                              f"withheld them until this run\n", line)
                self.assertEqual(self.report(self.kept()[-1])[0]["exit"], str(code))

    @unittest.skipIf(os.name == "nt", "a POSIX signal")
    def test_a_build_killed_after_it_put_them_back_stays_2(self):
        # build.py's -9 is a 2 before names are counted: max(-9, 1) would make a
        # build that did not finish a 1, "problems to look at". It was killed
        # after it wrote the page, so the names are back all the same.
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): self.document(None)}, kill=True)
        run = self.wrapper()
        self.assertEqual(run.code, 2)
        self.assertEqual(self.err_line(run),
                         f"built because there is no record of an earlier build: "
                         f"exit 2, report {self.reports / self.kept()[-1]}; players' "
                         f"names are back in {back.parent}, which withheld them until "
                         f"this run\n")
        self.assertEqual(self.report(self.kept()[-1])[0]["exit"],
                         "2 (build.py ended with status -9)")
        self.assertEqual(self.record()["exit"], -9)

    def test_a_dashboard_that_still_withholds_them_is_a_clean_0_on_stdout(self):
        self.dashboard("a", "numbered")
        run = self.wrapper()
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.err, "")
        self.built_line("there is no record of an earlier build", run)
        self.assertEqual(self.report(self.kept()[-1])[0]["exit"], "0")
        self.assertEqual(self.record()["names_back"], "")

    def test_a_windowless_run_still_exits_1(self):
        # Task Scheduler runs pythonw.exe, which has no stream to print on, so the
        # exit is all a club sees: the task's Last Run Result.
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): self.document(None)})
        with mock.patch.object(sys, "stdout", None), \
                mock.patch.object(sys, "stderr", None):
            self.assertEqual(scheduled.main([], root=self.root), 1)
            self.assertEqual(scheduled.main([], root=self.root), 1)
        self.assertEqual(len(self.calls()), 1)


class TestTheRunsAfterNamesWentBackSayItAgainUntilABuildFindsThemThere(NamesBackCase):
    """A skip repeats the 1 and the clause; the next build, finding names, is 0.

    Measured before (/tmp/t7/scratch/SC/before.txt): the run after the build that
    put names back was a skip at exit 0 whose line said nothing of them. A skip
    exits with the status of the build it skipped, which is the wrapper's, so the
    record keeps what that is worked out from, build.py's exit as before and where
    the names went back, and the skip works it out by the same rule. A build
    after it reads the dashboards before it runs, finds none withholding names,
    and exits by build.py alone: the alert lasts until something moves, as a
    build with problems does. Rejected: recording the wrapper's own exit in place
    of build.py's, which would make a stopped build's ``None`` a 2 and its next
    reason "could not run" where it is "did not finish".
    """

    def test_the_skip_is_1_on_stderr_and_the_build_after_it_is_0(self):
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): self.document(None)})
        self.assertEqual(self.wrapper().code, 1)
        skipped = self.wrapper()
        self.assertEqual(skipped.code, 1)
        found = SKIPPED.fullmatch(self.err_line(skipped))
        self.assertIsNotNone(found, skipped.err)
        self.assertEqual(found["exit"], "1")
        self.assertEqual(found["report"],
                         f"{self.reports / self.kept()[0]}; players' names are back "
                         f"in {back.parent}, which withheld them until that build")
        self.assertEqual(len(self.calls()), 1)

        self.touch(self.card)
        self.plan(say="stand-in build.py: the build after\n")
        built = self.wrapper()
        self.assertEqual(built.code, 0, built.err)
        self.assertEqual(built.err, "")
        self.built_line("the scorecard folder changed", built)
        self.assertEqual(self.record()["names_back"], "")
        again = self.wrapper()
        self.assertEqual(again.code, 0, again.err)
        self.assertEqual(again.err, "")
        self.assertEqual(SKIPPED.fullmatch(self.only_line(again))["exit"], "0")

    def test_a_build_with_problems_is_skipped_with_its_1_and_says_it_again(self):
        # A build.py exit of 1 is skipped as one of 0 is, so the record keeps where
        # whatever build.py exited, and the skip reads it whatever it exited.
        back = self.dashboard("a", "numbered")
        self.plan(write={str(back): self.document(None)}, exit=1)
        self.assertEqual(self.wrapper().code, 1)
        skipped = self.wrapper()
        self.assertEqual(skipped.code, 1)
        found = SKIPPED.fullmatch(self.err_line(skipped))
        self.assertIsNotNone(found, skipped.err)
        self.assertEqual(found["exit"], "1")
        self.assertEqual(found["report"],
                         f"{self.reports / self.kept()[0]}; players' names are back "
                         f"in {back.parent}, which withheld them until that build")
        self.assertEqual(len(self.calls()), 1)


class TestAWindowlessRunPrintsNothingAndDoesNotCrash(StubRootCase):
    """Under ``pythonw.exe`` there is no stdout and no stderr, and it still works.

    Measured: ``print`` to a None stream is already silent when ``sys.stdout`` is
    None too, but with only ``sys.stderr`` gone (``2>&-``, or a service that gives a
    process one stream) ``print(..., file=None)`` falls back to stdout, and a
    refusal lands in the one line a scheduler greps for success.
    """

    def test_a_build_a_skip_and_refusals_return_their_exits(self):
        with mock.patch.object(sys, "stdout", None), \
                mock.patch.object(sys, "stderr", None):
            self.assertEqual(scheduled.main([], root=self.root), 0)
            self.assertEqual(scheduled.main([], root=self.root), 0)
            self.assertEqual(scheduled.main(["--reports", "site"], root=self.root), 2)
            self.assertEqual(scheduled.main(["--keep", "1"], root=self.root), 2)
        self.assertEqual(len(self.calls()), 1)

    def test_with_only_stderr_gone_a_refusal_is_not_printed_as_the_one_line(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", None):
            code = scheduled.main(["--reports", "site"], root=self.root)
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")


class TestTheChildIsGivenUTF8AndNoWindow(StubRootCase):
    """The child's names arrive as written, and on Windows it opens no window."""

    def test_the_report_holds_what_a_utf8_child_printed(self):
        self.plan(say="", utf8=True)
        self.wrapper()
        self.assertEqual(self.report(self.kept()[0])[1], "PYTHONUTF8=1 \u0141\n")

    def test_the_spawn_options_are_no_window_on_windows_only(self):
        self.assertEqual(scheduled._popen_options(True), {"creationflags": 0x08000000})
        self.assertEqual(scheduled._popen_options(False), {})


class TestTheReportHeaderNamesOnlyTheFlagsItWasGiven(StubRootCase):
    """build.py gets its own three flags as typed, and nothing it was not given.

    As typed, because build.py runs in the project folder and resolves them to the
    same paths, so the report says ``site`` where the club wrote ``site``. Nothing
    else: an unattended build never prunes, never rewrites the settings, and always
    prints the whole report.
    """

    def test_a_flag_given_is_passed_as_typed(self):
        run = self.wrapper("--data", "cards", "--out", "pub", "--config", "club.toml",
                           "--keep", "5", "--timeout", "3")
        self.assertEqual(run.code, 0, run.err)
        header, _ = self.report(self.kept()[0])
        self.assertTrue(header["command"].endswith(
            "build.py --data cards --out pub --config club.toml"), header["command"])
        self.assertEqual(self.calls(),
                         [["--data", "cards", "--out", "pub", "--config", "club.toml"]])

    def test_no_flag_given_is_none_passed(self):
        self.wrapper()
        header, _ = self.report(self.kept()[0])
        self.assertTrue(header["command"].endswith("build.py"), header["command"])
        self.assertEqual(self.calls(), [[]])

    def test_an_empty_argument_is_recorded_as_a_word_and_not_as_nothing(self):
        """``--config ""`` is recorded as ``--config ''``, the command that ran.

        The header was joined with shlex, which writes an empty argument as
        ``''``. Every line a club might paste now takes the build's one rule
        (4.154), and that rule returned an empty argument as nothing at all,
        measured on the tree before: ``pasteable("")`` was ``""``. Moved onto it
        unchanged, the header would have recorded ``--config`` with no word
        after it, a command that runs differently from the one that ran.
        Rejected: keeping shlex for the header alone, a second rule for one job.
        """
        ran = scheduled.Ran(code=0, output="", stopped=False, started=True,
                            seconds=1.0)
        header = scheduled._header(
            started=datetime(2026, 9, 25, 10, 0), why="a test", ran=ran,
            argv=[sys.executable, str(self.root / "build.py"), "--config", ""],
            timeout=60.0, windows=False)
        self.assertIn(f"\n  command   {pasteable(sys.executable)} "
                      f"{pasteable(str(self.root / 'build.py'))} --config ''\n",
                      header)
        self.assertEqual(pasteable(""), "''")

    def test_a_windows_header_keeps_the_quoting_windows_reads(self):
        """On Windows the header is quoted for Windows, in double quotes.

        The POSIX lines moved onto the build's one rule (4.154), which writes
        single quotes. Windows does not read those: cmd keeps a single quote as
        part of the word, so the path would name a file nobody has. The header
        was ``subprocess.list2cmdline`` on the tree before and passes there; it
        pins what the fix must leave alone. Rejected: one POSIX rule for every
        platform's header.
        """
        argv = ["C:\\Python312\\python.exe", "C:\\Users\\club\\my dashboard\\build.py"]
        ran = scheduled.Ran(code=0, output="", stopped=False, started=True,
                            seconds=1.0)
        header = scheduled._header(
            started=datetime(2026, 9, 25, 10, 0), why="a test", ran=ran,
            argv=argv, timeout=60.0, windows=True)
        self.assertIn('\n  command   C:\\Python312\\python.exe '
                      '"C:\\Users\\club\\my dashboard\\build.py"\n', header)
        self.assertIn(f"\n  command   {subprocess.list2cmdline(argv)}\n", header)


class TestTheHeaderSaysHowLongInWords(unittest.TestCase):
    """Seconds up to two minutes, then minutes, and never "1 seconds"."""

    def test_the_durations(self):
        for seconds, said in ((1, "1 second"), (7.4, "7 seconds"),
                              (119.4, "119 seconds"), (125, "2 minutes"),
                              (600, "10 minutes")):
            with self.subTest(seconds=seconds):
                self.assertEqual(scheduled._took(seconds), said)


class TestAnEnormousTimeoutIsAWaitAndNotACrash(unittest.TestCase):
    """``--timeout 50000000`` was an OverflowError after the build had started.

    Measured: ``communicate(timeout=3e9)`` raises "timeout is too large", because
    the wait ends in a ``poll()`` that takes milliseconds as a C int. The wait is
    capped at a week instead. Rejected: a new refusal for large timeouts, which is
    one more sentence to say something no club will type.
    """

    def test_a_build_given_a_timeout_past_the_wait_limit_still_runs(self):
        ran = scheduled._run_build([sys.executable, "-c", "print('done')"],
                                   cwd=Path(tempfile.gettempdir()),
                                   timeout=50_000_000 * 60, windows=False)
        self.assertEqual((ran.code, ran.output, ran.stopped), (0, "done\n", False))


class TestTheWindowsRecipeIsQuotedAndWindowless(unittest.TestCase):
    """The task runs ``pythonw.exe``; the build it starts runs ``python.exe``.

    ``pythonw`` so no window flashes up every fifteen minutes; ``python`` for the
    child, which needs real streams to fill the pipe the report is read from.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        (self.bin / "python.exe").write_bytes(b"")

    def test_the_scheduler_is_given_pythonw_when_it_is_there(self):
        python = str(self.bin / "python.exe")
        self.assertEqual(scheduled._scheduler_python(python, True), python)
        (self.bin / "pythonw.exe").write_bytes(b"")
        self.assertEqual(scheduled._scheduler_python(python, True),
                         str(self.bin / "pythonw.exe"))
        self.assertEqual(scheduled._scheduler_python(python, False), python)

    def test_the_child_is_given_python_beside_pythonw(self):
        pythonw = str(self.bin / "pythonw.exe")
        self.assertEqual(scheduled._child_python(pythonw, True),
                         str(self.bin / "python.exe"))
        self.assertEqual(scheduled._child_python(pythonw, False), pythonw)
        (self.bin / "python.exe").unlink()
        self.assertEqual(scheduled._child_python(pythonw, True), pythonw)

    def test_every_argument_is_quoted_and_every_step_is_there(self):
        text = scheduled.schedule_text(
            windows=True, python="C:\\Python312\\pythonw.exe",
            script="C:\\Users\\club\\my dashboard\\tools\\scheduled.py",
            extra=["--data", "D:\\cards\\", "--keep", "40"], mac_protected=False)
        self.assertIn("  Program/script:  C:\\Python312\\pythonw.exe\n", text)
        self.assertIn('  Add arguments:   "C:\\Users\\club\\my dashboard\\tools\\'
                      'scheduled.py" "--data" "D:\\cards\\\\" "--keep" "40"\n', text)
        self.assertIn("  Start in:        leave it empty\n", text)
        for line in ('  Triggers     edit the trigger, tick "Repeat task every", '
                     'choose 15 minutes,',
                     '  Settings     leave "If the task is already running" at '
                     '"Do not start a new',
                     '  Conditions   untick "Start the task only if the computer '
                     'is on AC power"',
                     '  General      if the computer sits at the sign-in screen, '
                     'choose "Run whether'):
            self.assertIn(line + "\n", text)


class TestTheCronLineIsQuotedEscapedAndSilent(StubRootCase):
    """cron ends a command at an unescaped ``%`` and mails whatever a job prints."""

    def cron_line(self, text: str) -> str:
        return next(line for line in text.splitlines() if line.startswith("*/15 "))

    def test_a_space_and_a_percent_come_out_quoted_and_escaped(self):
        text = scheduled.schedule_text(
            windows=False, python="/usr/bin/python3",
            script="/opt/club dash%board/tools/scheduled.py", extra=[],
            mac_protected=False)
        self.assertEqual(self.cron_line(text),
                         "*/15 * * * * /usr/bin/python3 "
                         "'/opt/club dash\\%board/tools/scheduled.py' >/dev/null")
        self.assertNotIn("On a Mac", text)

    def test_the_cron_line_and_its_header_are_posix_whatever_machine_quotes_them(
            self):
        """sh reads a cron line, so it is quoted for sh on any machine.

        The build's one rule quotes in double quotes on Windows, where cmd reads
        them and no single quote, and these two lines ask it for POSIX by name.
        Asked with ``matchcentre.WINDOWS`` set, as on a Windows machine running
        this suite: left to the machine, the cron line came out
        `"/opt/club $dash/tools/scheduled.py"`, measured, with ``$`` live again.
        """
        script = "/opt/club $dash/tools/scheduled.py"
        ran = scheduled.Ran(code=0, output="", stopped=False, started=True,
                            seconds=1.0)
        with mock.patch.object(matchcentre, "WINDOWS", True):
            text = scheduled.schedule_text(
                windows=False, python="/usr/bin/python3", script=script,
                extra=["--config", ""], mac_protected=False)
            header = scheduled._header(
                started=datetime(2026, 9, 25, 10, 0), why="a test", ran=ran,
                argv=["/usr/bin/python3", script], timeout=60.0, windows=False)
        self.assertEqual(self.cron_line(text),
                         "*/15 * * * * /usr/bin/python3 "
                         "'/opt/club $dash/tools/scheduled.py' --config '' "
                         ">/dev/null")
        self.assertIn("\n  command   /usr/bin/python3 "
                      "'/opt/club $dash/tools/scheduled.py'\n", header)

    def test_a_mac_folder_cron_cannot_enter_gets_the_paragraph(self):
        text = scheduled.schedule_text(windows=False, python="/usr/bin/python3",
                                       script="/x/tools/scheduled.py", extra=[],
                                       mac_protected=True)
        self.assertTrue(text.endswith(
            "\n\nOn a Mac, cron is not allowed into Documents, Desktop or Downloads, "
            "and this\nschedule uses a folder in one of them. Move it somewhere "
            "else, or give cron\nFull Disk Access: the README section \"Running it "
            "on a schedule\" has the steps.\n"), text)

    def test_a_folder_it_was_given_in_downloads_gets_the_paragraph_too(self):
        # Measured before: only the project folder was asked, so with the project
        # in the home folder and ``--data ~/Downloads/cards`` the schedule printed
        # no paragraph, and every run cron started was refused the cards.
        downloads = self.base / "Downloads"
        with mock.patch.object(scheduled.sys, "platform", "darwin"), \
                mock.patch.object(scheduled.Path, "home", return_value=self.base):
            self.assertNotIn("Full Disk Access",
                             self.wrapper("--print-schedule").out)
            for flag, path in (("--data", downloads / "cards"),
                               ("--out", downloads / "site"),
                               ("--config", downloads / "club.toml"),
                               ("--reports", downloads / "reports")):
                with self.subTest(flag):
                    run = self.wrapper("--print-schedule", flag, str(path))
                    self.assertEqual(run.code, 0, run.err)
                    self.assertIn("Full Disk Access", run.out)

    def test_the_folders_it_was_given_come_out_absolute(self):
        run = self.wrapper("--print-schedule", "--data", "cards", "--out", "pub",
                           "--config", "club.toml", "--reports", "kept",
                           "--keep", "40", "--timeout", "12.5")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.cron_line(run.out), "*/15 * * * * " + " ".join(
            pasteable(part) for part in [
                sys.executable, str(self.root / "tools" / "scheduled.py"),
                "--data", str(self.root / "cards"), "--out", str(self.root / "pub"),
                "--config", str(self.root / "club.toml"),
                "--reports", str(self.root / "kept"),
                "--keep", "40", "--timeout", "12.5"]) + " >/dev/null")
        self.assertFalse((self.root / "kept").exists())
        self.assertEqual(self.calls(), [])

    def test_only_a_mac_folder_under_documents_desktop_or_downloads_is_protected(self):
        home = Path("/Users/club")
        with mock.patch.object(scheduled.sys, "platform", "darwin"), \
                mock.patch.object(scheduled.Path, "home", return_value=home):
            self.assertTrue(scheduled._mac_protected(home / "Documents" / "dash"))
            self.assertTrue(scheduled._mac_protected(home / "Desktop"))
            self.assertFalse(scheduled._mac_protected(home / "dashboard"))
        with mock.patch.object(scheduled.sys, "platform", "linux"):
            self.assertFalse(scheduled._mac_protected(Path.home() / "Documents"))


class TestTheWrapperNeverWritesIntoTheCheckout(unittest.TestCase):
    """With the default root it names this checkout, and still creates nothing."""

    @staticmethod
    def entries(root: Path) -> list[tuple[str, int, int | None]]:
        """Each entry at the top of *root*: its name, its own time, and the time
        of what it leads to, or None where it leads nowhere.

        Both times (4.524). Followed alone, as base had it, an editor's lock file
        beside build.py, a link to a name that is not a file, raised in a club's
        copy before anything was asked (/tmp/mcf/ub/scratch/before-symlink.txt).
        Read alone, as 4.524 first had it, a link's own time missed a report
        written through a reports/ that links to a folder kept elsewhere, which
        base caught (/tmp/mcf/ub/scratch/repair1-wrapper.txt). Rejected: leaving
        links out, which would miss one the wrapper wrote.
        """
        return sorted((p.name, p.lstat().st_mtime_ns,
                       p.stat().st_mtime_ns if p.exists() else None)
                      for p in root.iterdir() if p.name != "__pycache__")

    def test_print_schedule_names_the_real_script_and_writes_nothing(self):
        before = self.entries(ROOT)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(scheduled.main(["--print-schedule"]), 0)
        self.assertIn(f" {pasteable(str(ROOT / 'tools' / 'scheduled.py'))} ",
                      out.getvalue())
        self.assertEqual(self.entries(ROOT), before)


class TestTheWrapperTestReadsALinkAsALinkAndAsWhereItLeads(unittest.TestCase):
    """The test above, asked of a planted checkout with a link of each kind in it.

    A class of their own (4.524, repair 2). In the test's class, which
    test_project_complete runs in a club's copy and asks to end "OK", their
    skip where a lock is not a link ended that run "OK (skipped=2)" on any
    system but a POSIX one, and failed it there
    (/tmp/mcf/ub/scratch/repair2-windows.txt). What they ask is of the test,
    and not of the copy it runs in. Rejected: dropping the skip, when Windows
    makes a link only for an account allowed to; and "OK (skipped=2)" taken
    as a pass in the copy, which passes the next test that skips there too.
    """

    TEST = "test_print_schedule_names_the_real_script_and_writes_nothing"

    def planted(self) -> Path:
        """A checkout with a lock beside build.py that links to nothing, and a
        reports/ that links to a folder kept elsewhere, unchanged since 2001."""
        if os.name != "posix":
            self.skipTest("a lock that is a link is a POSIX editor's")
        holder = tempfile.TemporaryDirectory(prefix="test-planted-wrapper-")
        self.addCleanup(holder.cleanup)
        root = Path(holder.name) / "dashboard"
        elsewhere = Path(holder.name) / "reports-kept-elsewhere"
        root.mkdir()
        elsewhere.mkdir()
        (root / "build.py").write_text("", encoding="utf-8")
        os.symlink("secretary@clubhouse.4242:1760000000", root / ".#build.py")
        os.symlink(elsewhere, root / "reports", target_is_directory=True)
        os.utime(elsewhere, ns=(10**18, 10**18))
        return root

    def asked_of(self, root: Path, *, writes: bool) -> str:
        """What the test above says of *root*, with a wrapper standing in that
        prints the line, and first writes a report if *writes*: nothing when it
        passes."""
        def wrapper(argv):
            if writes:
                (root / "reports" / "build-1.txt").write_text("A report.\n",
                                                              encoding="utf-8")
            print(f" {pasteable(str(root / 'tools' / 'scheduled.py'))} ")
            return 0
        case = TestTheWrapperNeverWritesIntoTheCheckout(self.TEST)
        with mock.patch.dict(globals(), ROOT=root), \
                mock.patch.object(scheduled, "main", wrapper):
            try:
                getattr(case, self.TEST)()
            except case.failureException as caught:
                return str(caught)
        return ""

    def test_a_lock_that_links_to_nothing_is_not_followed(self):
        self.assertEqual(self.asked_of(self.planted(), writes=False), "")

    def test_a_report_written_through_a_linked_folder_is_seen(self):
        self.assertIn("'reports'", self.asked_of(self.planted(), writes=True))


# --- the real program -------------------------------------------------------

_TEMPLATE: list[Path] = []


def _real_copy(into: Path) -> Path:
    """The program and the demo season, in a folder whose name has a space in it.

    The season is made once per run of this file and copied for each class.
    """
    if not _TEMPLATE:
        tmp = tempfile.TemporaryDirectory(prefix="scheduled-real-")
        unittest.addModuleCleanup(tmp.cleanup)
        # Forgotten with the folder, which goes each time a run moves on to
        # another module: one that comes back here makes it again.
        unittest.addModuleCleanup(_TEMPLATE.clear)
        template = Path(tmp.name) / "club dashboard"
        template.mkdir()
        skip = shutil.ignore_patterns("__pycache__")
        shutil.copy2(ROOT / "build.py", template / "build.py")
        for folder in ("matchcentre", "tools"):
            shutil.copytree(ROOT / folder, template / folder, ignore=skip)
        subprocess.run((sys.executable, str(template / "tools" / "demo.py")),
                       check=True, capture_output=True, timeout=300, env=_ENV)
        _TEMPLATE.append(template)
    copy = into / "club dashboard"
    shutil.copytree(_TEMPLATE[0], copy)
    return copy


_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


class RealCopyCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="scheduled-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.copy = _real_copy(self.base)
        self.elsewhere = self.base / "somewhere else"
        self.elsewhere.mkdir()

    def scheduled(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run((sys.executable, str(self.copy / "tools" / "scheduled.py"),
                               *argv), cwd=self.elsewhere, capture_output=True,
                              text=True, timeout=600, env=_ENV)

    def files(self, folder: Path) -> dict[str, int]:
        return {p.relative_to(folder).as_posix(): p.stat().st_mtime_ns
                for p in folder.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts}


class TestTheWrapperFindsItsFoldersFromAnyStartingDirectory(RealCopyCase):
    """From a folder that is not the project, it builds the project's own data/.

    The first run writes ``config.toml`` (build.py's first-run discovery), which the
    second run sees as a change to the settings and builds again; the third skips.
    """

    def test_it_builds_the_copy_and_then_skips(self):
        first = self.scheduled()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(first.stdout.startswith(
            "built because there is no record of an earlier build: exit 0, report "
            f"{self.copy / 'reports'}"), first.stdout)
        report = sorted((self.copy / "reports").glob("*.txt"))[-1]
        self.assertEqual(report.read_text(encoding="utf-8").splitlines()[-1].strip(),
                         "exit 0: all 15 card(s) scored, no errors")
        self.assertEqual((self.copy / "site" / site.STATUS_FILE).read_bytes(), CLEAN)
        self.assertTrue((self.copy / "config.toml").exists())
        second = self.scheduled()
        self.assertTrue(second.stdout.startswith(
            "built because the settings file changed: exit 0, "), second.stdout)
        third = self.scheduled()
        self.assertEqual(third.returncode, 0)
        self.assertRegex(third.stdout, "^nothing has changed since the build at ")
        self.assertEqual(list(self.elsewhere.iterdir()), [])


@unittest.skipIf(os.name == "nt", "cron")
class TestTheScheduleItPrintsRunsAsPrinted(RealCopyCase):
    """The cron line, run the way cron runs it, from where cron starts, works.

    The copy's folder has a space in it, which a line joined with spaces splits in
    two, and the line prints nothing, because cron mails whatever a job prints.
    """

    def test_the_printed_line_builds_and_prints_nothing(self):
        printed = self.scheduled("--print-schedule")
        self.assertEqual(printed.returncode, 0, printed.stderr)
        line = next(line for line in printed.stdout.splitlines()
                    if line.startswith("*/15 "))
        command = line.split(" ", 5)[5].replace("\\%", "%")
        done = subprocess.run(("sh", "-c", command), cwd=self.elsewhere,
                              capture_output=True, text=True, timeout=600, env=_ENV)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertEqual(len(list((self.copy / "reports").glob("*.txt"))), 1)

    def test_a_folder_named_with_a_quote_is_quoted_as_the_build_quotes_it(self):
        """A copy under ``st mary's`` is written with the build's ``'\\''``, and runs.

        Measured before 4.154 (/tmp/t6/scratch/m12/before.txt, part E): the cron
        line and the report's header were joined with shlex, which writes the
        quote as ``'"'"'`` where the build's, the demo's and the fetcher's lines
        write ``'\\''``. Both ran pasted, for every name measured, ``$``, a
        backtick, a double quote and ``!`` among them; the defect was a second
        rule for the same job, the one a fix to the first would miss. Rejected:
        leaving the schedule on shlex because its lines already worked.
        """
        copy = _real_copy(self.base / "st mary's")
        printed = subprocess.run(
            (sys.executable, str(copy / "tools" / "scheduled.py"), "--print-schedule"),
            cwd=self.elsewhere, capture_output=True, text=True, timeout=600, env=_ENV)
        self.assertEqual(printed.returncode, 0, printed.stderr)
        line = next(line for line in printed.stdout.splitlines()
                    if line.startswith("*/15 "))
        self.assertIn(f" {pasteable(str(copy / 'tools' / 'scheduled.py'))} ", line)
        self.assertIn("/st mary'\\''s/club dashboard/tools/scheduled.py' ", line)
        command = line.split(" ", 5)[5].replace("\\%", "%")
        done = subprocess.run(("sh", "-c", command), cwd=self.elsewhere,
                              capture_output=True, text=True, timeout=600, env=_ENV)
        self.assertEqual(done.returncode, 0, done.stderr)
        [report] = (copy / "reports").glob("*.txt")
        header = next(line for line in report.read_text(encoding="utf-8").splitlines()
                      if line.startswith("  command "))
        self.assertEqual(header, "  command   " + " ".join(
            pasteable(part) for part in [sys.executable, str(copy / "build.py")]))
        # And the header's command is one a club can paste in the project.
        again = subprocess.run(("sh", "-c", header.split(None, 1)[1]), cwd=copy,
                               capture_output=True, text=True, timeout=600, env=_ENV)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout.splitlines()[-1].strip(),
                         "exit 0: all 15 card(s) scored, no errors")


class TestPrintScheduleWritesNothing(RealCopyCase):
    """Printing the line runs no build and makes no folder, not even reports/."""

    def test_nothing_under_the_copy_changes(self):
        before = self.files(self.copy)
        printed = self.scheduled("--print-schedule")
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertEqual(self.files(self.copy), before)
        self.assertFalse((self.copy / "reports").exists())
        self.assertFalse((self.copy / "site").exists())


class WithheldCopyCase(RealCopyCase):
    """The real program over the six cards of tests/test_pipeline.py, not the demo.

    Their players and team names are the ones the withholding tests use, so a team
    renamed "Ana Fernandez Shield" is a name the guard finds.
    """

    NUMBERED = '[build]\nplayers = "numbered"\n'

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.copy / "data")
        write_season_cards(self.copy / "data")
        self.config = self.copy / "config.toml"
        self.config.write_text(self.NUMBERED, encoding="utf-8")
        self.status_file = self.copy / "site" / site.STATUS_FILE

    def run_clean(self) -> subprocess.CompletedProcess:
        done = self.scheduled()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.status_file.read_bytes(), CLEAN)
        return done


class TestAWithheldBuildTheGuardRefusedMarksThePageOnEveryRun(WithheldCopyCase):
    """The wrapper over a build that stopped rather than print a name.

    Measured before (/tmp/t4/scratch/f2/before-probe.log): over a numbered build,
    a team renamed "Ana Fernandez Shield" made the wrapper's builds exit 2, the
    first "because there is no record of an earlier build" and the next "because
    the last build could not run", and ``build-status.js`` stayed
    ``{"level": "ok"}`` through both. build.py records the refusal itself now, so
    the wrapper needs nothing of its own: it records only what a build cannot (one
    it stopped, one that ended on a signal or never started), and it keeps exit 2
    in its record, so every run after builds again and finds the page already
    marked. Rejected: recording it from here as well, two writers of one sentence.
    """

    def test_the_refusal_and_the_run_after_it(self):
        self.run_clean()
        self.config.write_text(self.NUMBERED + '\n[team_names]\nriverside-rockets = '
                               '"Ana Fernandez Shield"\n', encoding="utf-8")
        failed = site.build_status_js({"level": "fail",
                                       "text": status.failed("withheld")})
        for why in ("the settings file changed", "the last build could not run"):
            with self.subTest(why=why):
                done = self.scheduled()
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertTrue(
                    done.stdout.startswith(f"built because {why}: exit 2, "),
                    done.stdout)
                self.assertEqual(self.status_file.read_text(encoding="utf-8"), failed)


class TestTheLineAndTheReportAgreeAboutNamesPutBack(WithheldCopyCase):
    """The real build: the wrapper's clause is the report's ``players`` line, in full.

    See :class:`TestTheLineSaysWhenABuildPutNamesBack`. The public copy the
    README describes, scheduled with a settings file that sets no ``[build]
    players``: build.py exits 0 and prints the names, the wrapper exits 1, and the
    line says where, on stderr. The run after is a skip that says it again, and
    once a card has changed the build after that finds nothing withheld and is 0.
    """

    def test_the_settings_file_stops_withholding(self):
        self.run_clean()
        self.config.write_text("", encoding="utf-8")
        done = self.scheduled()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")
        folder = self.copy / "site" / "tuesday-division-b"
        self.assertTrue(done.stderr.endswith(
            f"; players' names are back in {folder}, which withheld them until this "
            f"run\n"), done.stderr)
        report = sorted((self.copy / "reports").glob("*.txt"))[-1]
        said = " ".join(report.read_text(encoding="utf-8").split())
        self.assertIn("players names: site/tuesday-division-b withheld them until "
                      "this run", said)
        self.assertIn("exit 1 (build.py exited 0, but players' names are back) ", said)
        again = self.scheduled()
        self.assertEqual(again.returncode, 1, again.stdout + again.stderr)
        self.assertEqual(again.stdout, "")
        self.assertTrue(again.stderr.endswith(
            f"; players' names are back in {folder}, which withheld them until that "
            f"build\n"), again.stderr)
        os.utime(sorted((self.copy / "data").rglob("*.pdf"))[0], None)
        after = self.scheduled()
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        self.assertEqual(after.stderr, "")
        self.assertTrue(after.stdout.startswith(
            "built because the scorecard folder changed: exit 0, report "),
            after.stdout)


@unittest.skipIf(os.name == "nt", "cron")
class TestTheCronLineHandsCronTheLineThatSaysNamesAreBack(WithheldCopyCase):
    """Run as cron runs it, a build that put names back prints past ``>/dev/null``.

    Measured before (/tmp/t7/scratch/SC/before.txt): the line ``--print-schedule``
    printed, run twice by ``sh -c`` after names were put back, exited 0 both times
    and printed nothing on either stream, so cron had nothing to mail. The line is
    on stderr now, which that line keeps, and both runs exit 1.
    """

    def test_both_runs_exit_1_with_one_line_on_stderr(self):
        self.run_clean()
        self.config.write_text("", encoding="utf-8")
        printed = self.scheduled("--print-schedule")
        self.assertEqual(printed.returncode, 0, printed.stderr)
        line = next(line for line in printed.stdout.splitlines()
                    if line.startswith("*/15 "))
        self.assertTrue(line.endswith(" >/dev/null"), line)
        command = line.split(" ", 5)[5].replace("\\%", "%")
        folder = self.copy / "site" / "tuesday-division-b"
        for until in ("this run", "that build"):
            with self.subTest(until=until):
                done = subprocess.run(("sh", "-c", command), cwd=self.elsewhere,
                                      capture_output=True, text=True, timeout=600,
                                      env=_ENV)
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertEqual(done.stdout, "")
                self.assertEqual(done.stderr.count("\n"), 1, done.stderr)
                self.assertTrue(done.stderr.endswith(
                    f"; players' names are back in {folder}, which withheld them "
                    f"until {until}\n"), done.stderr)


if __name__ == "__main__":
    unittest.main()
