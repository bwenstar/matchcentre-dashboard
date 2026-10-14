"""``tools/release.py`` and ``.github/workflows/release.yml`` -- a pushed tag
makes the GitHub Release.

Before them a release was made by hand, in the five steps CONTRIBUTING.md's
*Making a release* listed: bump ``__version__``, write the changelog entry, run
the suite, regenerate the screenshots, ``git tag``. Then the maintainer pasted
notes into GitHub's *Draft a new release* form, or ran ``gh release create``
themselves. Measured on the tree before this file
(/tmp/mcf/rel/scratch/build/before.txt): the two workflows ran on pushes to
``main``, pull requests and ``workflow_dispatch``, no line of either named a
tag, and neither could be called by another, so pushing ``v0.2.0`` ran nothing
at all. The notes would have been the changelog's section as it is written,
hard-wrapped at about 85 columns, which GitHub's release page can show as a
broken line in the middle of each sentence.

Now a tag push runs three jobs. ``check`` refuses, in seconds, a tag the code
and the changelog do not both describe as released; ``tests`` is tests.yml
itself, called; ``publish`` zips the tagged tree and makes or updates the
release. The local half, the version bump and the changelog's move, is
``prepare``.

Almost every class runs ``release.main`` in-process against a project folder
of its own: a ``matchcentre/__init__.py`` holding only ``__version__``, a
``CHANGELOG.md``, and one file of each kind that quotes the report's first
line. The ``publish`` classes make that folder a git repository, tag it, and
put a stand-in ``gh`` first on PATH, a few lines of Python that log what they
were asked and the notes they were handed. Nothing here reaches GitHub, which
this machine cannot.

Rejected: tests that run ``gh`` against a real repository, which would need a
token and a network, and would make a release every time the suite ran.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import matchcentre                                            # noqa: E402
from matchcentre import NAME                                  # noqa: E402
import release                                                # noqa: E402

RELEASE = ROOT / "tools" / "release.py"
WORKFLOWS = ROOT / ".github" / "workflows"
# The three variables a workflow run sets that name it, which the suite may be
# running under: tests.yml runs it in Actions, where they are all set.
RUN_VARIABLES = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
REPOSITORY = "example-owner/example-repo"


def example(version: str) -> str:
    """The report's first line, as a form or the README writes it out.

    Built here and not written out, because the suite's own stale-version test
    reads this file, and a literal of any version but the current one fails it.
    """
    return f"{NAME} {version}  (python 3.12.3 on linux)"


UNRELEASED = """\
# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed

- A card dated off its night is filed under the night its competition line
  names, where it made a competition of its own.

### Added

- `tools/demo.py --day` chooses the night the fictional club plays, and the
  demo's cards print it.

## [0.1.0] — 2026-10-01

Initial release, whose report began `{example}`.

### Added

- `build.py`: the single command.
"""

# The same changelog once `prepare 0.2.0 --date 2026-10-08` has moved it.
RELEASED = """\
# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.2.0] — 2026-10-08

### Fixed

- A card dated off its night is filed under the night its competition line
  names, where it made a competition of its own.

### Added

- `tools/demo.py --day` chooses the night the fictional club plays, and the
  demo's cards print it.

## [0.1.0] — 2026-10-01

Initial release, whose report began `{example}`.

### Added

- `build.py`: the single command.
"""

# The body the release's notes are made from: the section above, each item one
# line however it was wrapped.
BODY = """\
### Fixed

- A card dated off its night is filed under the night its competition line \
names, where it made a competition of its own.

### Added

- `tools/demo.py --day` chooses the night the fictional club plays, and the \
demo's cards print it.
"""


# A private note in the scratch project. It is joined rather than written
# out, because the suite refuses a path under notes/ written into a shipped
# file: a club's download has no notes/ to follow it to.
NOTE = "/".join(("notes", "private.md"))


class Ran:
    """One in-process run of the tool: its exit, and what it printed where."""

    def __init__(self, code: int, out: str, err: str):
        self.code, self.out, self.err = code, out, err


def make_project(root: Path, *, version: str = "0.1.0",
                 changelog: str = UNRELEASED) -> None:
    """A project folder holding just what the tool reads and rewrites.

    One file of every kind the stale-version scan reads quotes the report's
    first line, and so do a private note and a built page, which it does not
    read and which the tool must leave alone. So do three lines that are not
    the current version at all, only like it. The changelog's [0.1.0] section
    quotes 0.1.0's own line, as the real one does: that is the version the
    prepare tests start from, so a rewrite of the current version that reached
    the changelog's history would be seen.
    """
    old = version
    files = {
        "matchcentre/__init__.py":
            f'"""A stand-in package."""\n\n__version__ = "{version}"\n',
        "CHANGELOG.md": changelog.format(example=example("0.1.0")),
        "README.md": (
            "# The project\n\n```bash\npython3 build.py --version\n"
            f"# {example(old)}\n```\n\n"
            f"Not this one: `{NAME} {old}1`, nor `{NAME} {old}-rc.1`, nor "
            f"`{NAME} {old}+build.5`.\nBut this one, at a sentence's end: "
            f"{NAME} {old}.\n"),
        ".github/ISSUE_TEMPLATE/form.yml":
            f"description: It looks like `{example(old)}`.\n",
        "tests/test_stand_in.py": f"# the report prints `{example(old)}`\n",
        "config.example.toml": f"# written by {example(old)}\n",
        NOTE: f"{example(old)}\n",
        "site/index.html": f"<p>{example(old)}</p>\n",
        "docs/picture.png": f"{example(old)}\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def fence_reader_after(text: str):
    """A fence reader that has read *text*, a line at a time."""
    fenced = release._Fenced()
    for line in text.splitlines():
        fenced(line)
    return fenced


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under *root* but git's own, by its path, with its bytes."""
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


class ProjectCase(unittest.TestCase):
    """A project folder of its own per test, and the tool run against it."""

    VERSION = "0.1.0"
    CHANGELOG = UNRELEASED

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="release-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.project = self.base / "project"
        make_project(self.project, version=self.VERSION,
                     changelog=self.CHANGELOG)
        # The tool runs here, and its git with it, which asks the repository
        # os.environ names before the one at its root: under a contributor's
        # hook or rebase -x, theirs. Without it and git_env() in git(), 36 of
        # these errored and one failed, and the contributor's repository took
        # five commits and two tags (/tmp/mcf/ub/scratch/repair2-release.txt).
        # Rejected: an environment of its own for the tool's git, which a
        # program run by somebody who did set GIT_DIR rightly obeys.
        from test_project_complete import git_env
        patcher = mock.patch.dict(os.environ, git_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_(self, *argv: str) -> Ran:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = release.main(list(argv), root=self.project)
            except SystemExit as exc:        # argparse, which exits
                code = exc.code
        return Ran(code, out.getvalue(), err.getvalue())

    def read(self, name: str) -> str:
        return (self.project / name).read_text(encoding="utf-8")

    def write(self, name: str, text: str) -> None:
        (self.project / name).write_text(text, encoding="utf-8")


class TestAVersionIsReadAndOrderedAsSemanticVersioningSays(unittest.TestCase):
    """Which versions the tool takes, and which of two is newer.

    Before it nothing read a version but the suite's ``\\d+\\.\\d+\\.\\d+``,
    which takes ``01.2.3`` and orders nothing. A release that went backwards,
    or ``0.2.0`` after ``0.2.0-rc.1`` was compared as text, would have been
    published. The order is semver.org's section 11, and build metadata does
    not count. Rejected: comparing with ``tuple(map(int, ...))``, which cannot
    read a pre-release at all.
    """

    def test_a_version_is_three_numbers_with_an_optional_pre_release_and_build(
            self):
        for text in ("0.2.0", "1.0.0", "10.20.30", "1.0.0-rc.1",
                     "1.0.0-alpha.beta.1", "1.0.0-0.3.7", "1.0.0+build.5",
                     "1.0.0-rc.1+build.5", "1.0.0-x-y-z.--"):
            with self.subTest(version=text):
                self.assertIsNotNone(release.parse_version(text))
        for text in ("", "0.2", "0.2.0.1", "v0.2.0", "01.2.3", "1.02.3",
                     "1.2.03", "1.0.0-", "1.0.0-01", "1.0.0+", "1.0.0-rc..1",
                     "1.0.0 ", " 1.0.0", "1.0.0\n", "a.b.c",
                     # Another script's digit, first and after an ASCII one:
                     # semver.org's [1-9]\d* refuses only the first, and int()
                     # reads 1١ as 11.
                     "١.0.0", "1١.0.0", "0.1١.0", "1.0.0-1١"):
            with self.subTest(version=text):
                self.assertIsNone(release.parse_version(text))

    def test_the_order_is_the_order_semver_gives(self):
        ordered = ("1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta",
                   "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
                   "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0",
                   "10.0.0")
        for older, newer in zip(ordered, ordered[1:]):
            with self.subTest(older=older, newer=newer):
                self.assertTrue(release.newer(newer, older))
                self.assertFalse(release.newer(older, newer))

    def test_build_metadata_makes_no_version_newer(self):
        self.assertFalse(release.newer("1.0.0+build.2", "1.0.0"))
        self.assertFalse(release.newer("1.0.0", "1.0.0+build.2"))
        self.assertFalse(release.newer("1.0.0", "1.0.0"))


class TestPrepareWritesTheVersionTheChangelogAndTheExamples(ProjectCase):
    """``prepare VERSION`` does what CONTRIBUTING's first two steps did by
    hand.

    By hand, the version was bumped in ``matchcentre/__init__.py``, the
    ``[Unreleased]`` section was retitled with the version and a date, and
    every example of the report's first line was found and rewritten: five
    files on the tree measured (before.txt), three of them prose, which the
    suite found only after the bump, 25 minutes in. Now the three are written
    together and the rest is printed as a list. A version that merely starts
    with the old one is not rewritten, nor is anything the suite's scan does
    not read: the changelog, a private note, a built page. Rejected: having
    ``prepare`` commit and tag as well, which leaves no moment to read the
    notes, and makes the tool's one irreversible step a side effect.
    """

    def test_the_three_are_written_and_nothing_else_is(self):
        before = snapshot(self.project)
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        after = snapshot(self.project)
        changed = sorted(name for name in after if after[name] != before[name])
        self.assertEqual(changed, sorted([
            ".github/ISSUE_TEMPLATE/form.yml", "CHANGELOG.md", "README.md",
            "config.example.toml", "matchcentre/__init__.py",
            "tests/test_stand_in.py"]))
        self.assertEqual(sorted(after), sorted(before))

    def test_the_version_is_the_new_one(self):
        self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(self.read("matchcentre/__init__.py"),
                         '"""A stand-in package."""\n\n'
                         '__version__ = "0.2.0"\n')

    def test_the_unreleased_section_moves_under_the_version_and_its_date(self):
        self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(self.read("CHANGELOG.md"),
                         RELEASED.format(example=example("0.1.0")))

    def test_every_example_of_the_report_line_quotes_the_new_version(self):
        self.write("README.md", self.read("README.md") + "And this one, a "
                   "version the suite reads as the first three numbers: "
                   f"{NAME} 0.1.0.1\n")
        self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        readme = self.read("README.md")
        self.assertIn(f"# {example('0.2.0')}\n", readme)
        self.assertIn(f"at a sentence's end: {NAME} 0.2.0.\n", readme)
        # The suite reads `0.1.0.1` as 0.1.0 and a fourth part after it, so
        # it is rewritten as the suite would then read it stale.
        self.assertIn(f"three numbers: {NAME} 0.2.0.1\n", readme)
        # Each of these is a different version that starts with the old one.
        for kept in (f"{NAME} 0.1.01", f"{NAME} 0.1.0-rc.1",
                     f"{NAME} 0.1.0+build.5"):
            with self.subTest(kept=kept):
                self.assertIn(kept, readme)
        for name in (".github/ISSUE_TEMPLATE/form.yml",
                     "tests/test_stand_in.py", "config.example.toml"):
            with self.subTest(file=name):
                self.assertIn(example("0.2.0"), self.read(name))
                self.assertNotIn(example("0.1.0"), self.read(name))

    def test_what_the_scan_does_not_read_is_left_as_it_was(self):
        self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        for name in (NOTE, "site/index.html",
                     "docs/picture.png"):
            with self.subTest(file=name):
                self.assertIn(example("0.1.0"), self.read(name))
                self.assertNotIn(example("0.2.0"), self.read(name))
        # The changelog is a history of every version, and its examples are
        # history too, the current version's among them.
        self.assertIn(example("0.1.0"), self.read("CHANGELOG.md"))
        self.assertNotIn(example("0.2.0"), self.read("CHANGELOG.md"))

    def test_the_files_it_rewrites_are_the_files_the_suite_scans(self):
        import test_project_complete as complete
        self.assertEqual(release.SHIPPED_SUFFIXES, complete.SHIPPED_SUFFIXES)
        self.assertEqual(release.NOT_SHIPPED, complete.NOT_SHIPPED)
        # And it reads an example as the suite does.
        self.assertEqual(release.REPORT_LINE.pattern,
                         complete.REPORT_LINE.pattern)

    def test_it_rewrites_exactly_what_the_suite_reads_as_the_version(self):
        # Measured before: prepare took `0.1.0.1` for another version and
        # left it, while the suite read it as 0.1.0, so a tree that passed
        # before the bump failed after it, 25 minutes into the tests job.
        import test_project_complete as complete
        tails = ("", ".", ",", ")", "`", " (python", "_x", "a", "-", "+",
                 ".1", ".x", ".dev1", "1", "0", "-rc.1", "-x", "--x", "-rc.",
                 "+build.7", "+-", "+.")
        lines = [f"{at}: {NAME} 0.1.0{tail}" for at, tail in enumerate(tails)]
        self.write("docs/forms.md", "".join(line + "\n" for line in lines))
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        after = self.read("docs/forms.md").splitlines()
        for tail, line, now in zip(tails, lines, after):
            with self.subTest(tail=tail):
                read = complete.REPORT_LINE.findall(line) == ["0.1.0"]
                self.assertEqual(now != line, read, now)
                if read:
                    self.assertEqual(complete.REPORT_LINE.findall(now),
                                     ["0.2.0"])

    def test_the_suite_s_own_version_test_passes_on_what_prepare_wrote(self):
        # Measured before: the suite's scan read `(\d+\.\d+\.\d+)`, so after
        # prepare 0.3.0-rc.1 it read each example as 0.3.0, and the release's
        # tests job failed on every pre-release. The test itself is run here,
        # on the project prepare wrote, and not only the expression it reads
        # with, which a scan could stop using.
        import test_project_complete as complete
        name = ("test_every_example_of_the_report_line_quotes_the_version"
                "_in_the_code")
        for version in ("0.2.0", "0.2.0-rc.1", "0.2.0+build.5",
                        "1.0.0-rc.1+build.5"):
            with self.subTest(version=version):
                self.project = self.base / version
                make_project(self.project)
                # A fourth part the suite reads past, which only a final
                # can be followed by (see the test after this one).
                fourth = (f"And a fourth part: {NAME} 0.1.0.1\n"
                          if version == "0.2.0" else "")
                self.write("README.md", f"# {example('0.1.0')}\nAt a "
                                        f"sentence's end: {NAME} 0.1.0.\n"
                                        + fourth)
                results = []
                for _ in range(2):
                    result = unittest.TestResult()
                    with mock.patch.object(complete, "ROOT", self.project), \
                            mock.patch.object(matchcentre, "__version__",
                                              version):
                        complete.TestTheVersionNumberIsOneNumber(name).run(
                            result)
                    results.append(result)
                    if not _:
                        ran = self.run_("prepare", version, "--date",
                                        "2026-10-08")
                        self.assertEqual(ran.code, 0, ran.err)
                # It read this project: before prepare, every example was
                # stale.
                self.assertFalse(results[0].wasSuccessful())
                self.assertTrue(results[1].wasSuccessful(),
                                results[1].failures + results[1].errors)

    def test_an_example_in_the_package_itself_is_rewritten_too(self):
        # Measured before: the scan skipped every file whose version it had
        # bumped, so an example in matchcentre/__init__.py, where
        # version_line() is and its docstring would quote it, kept the old
        # version, and the suite's version test failed after the bump.
        import test_project_complete as complete
        name = ("test_every_example_of_the_report_line_quotes_the_version"
                "_in_the_code")
        package = "matchcentre/__init__.py"
        # The fixture's README quotes versions that only look like this one,
        # which the suite's test would read as stale whatever prepare did.
        self.write("README.md", f"# {example('0.1.0')}\n")
        self.write(package, self.read(package)
                   + f"#: Its line reads ``{example('0.1.0')}``.\n")
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertEqual(self.read(package),
                         '"""A stand-in package."""\n\n'
                         '__version__ = "0.2.0"\n'
                         f"#: Its line reads ``{example('0.2.0')}``.\n")
        self.assertIn(f"{package} 1 version, now 0.2.0, and 1 example of "
                      f"the version line CHANGELOG.md", " ".join(
                          ran.out.split()))
        result = unittest.TestResult()
        with mock.patch.object(complete, "ROOT", self.project), \
                mock.patch.object(matchcentre, "__version__", "0.2.0"):
            complete.TestTheVersionNumberIsOneNumber(name).run(result)
        self.assertTrue(result.wasSuccessful(),
                        result.failures + result.errors)

    def test_an_example_that_would_read_as_another_version_is_refused(self):
        # `0.1.0.1` is read as 0.1.0 and more. Rewritten for 0.2.0-rc.1, it
        # is read as 0.2.0-rc.1.1, which the suite would find stale, so it is
        # named and nothing is written. Measured on the first cut of this
        # round's fix, by the test before this one.
        self.write("README.md", self.read("README.md")
                   + f"A fourth part: {NAME} 0.1.0.1\n")
        before = snapshot(self.project)
        ran = self.run_("prepare", "0.2.0-rc.1", "--date", "2026-10-08")
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(snapshot(self.project), before)
        said = " ".join(ran.err.split())
        self.assertIn("README.md:10 quotes", said)
        self.assertIn("would read as 0.2.0-rc.1.1, not 0.2.0-rc.1", said)
        self.assertIn("Nothing was written.", said)
        self.assertEqual(ran.out, "")

    def test_the_date_is_today_when_none_is_given(self):
        first = date.today().isoformat()
        ran = self.run_("prepare", "0.2.0")
        last = date.today().isoformat()
        self.assertEqual(ran.code, 0, ran.err)
        headings = re.findall(r"(?m)^## \[0\.2\.0\] — (\S+)$",
                              self.read("CHANGELOG.md"))
        self.assertEqual(len(headings), 1)
        self.assertIn(headings[0], {first, last})

    def test_it_says_what_it_changed_and_what_is_left_in_order(self):
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        lines = ran.out.splitlines()
        for name, count in (("matchcentre/__init__.py", 1),
                            ("CHANGELOG.md", 1), ("README.md", 2),
                            (".github/ISSUE_TEMPLATE/form.yml", 1)):
            with self.subTest(file=name):
                said = [line for line in lines
                        if line.split()[:1] == [name]
                        or line.strip().startswith(name + " ")]
                self.assertEqual(len(said), 1, ran.out)
                self.assertRegex(said[0], rf"\s{count}\s")
        steps = ("CHANGELOG.md", "unittest discover", "tools/screenshots.py",
                 "git commit", f'git tag -a v0.2.0 -m "{NAME} 0.2.0"',
                 "git push origin main", "git push origin v0.2.0")
        tail = ran.out[ran.out.index("left"):]
        at = [tail.find(step) for step in steps]
        self.assertNotIn(-1, at, tail)
        self.assertEqual(at, sorted(at), tail)
        self.assertIn("Pushing the tag is what makes the release", tail)
        self.assertEqual(ran.err, "")

    def test_no_line_it_prints_runs_past_the_width(self):
        # Measured before: the rows of the table of files were printed raw,
        # 79 columns for 0.2.0, 84 for 0.2.0-rc.1, and 96 for 0.2.0 after
        # it, past cli.WIDTH, the width every other line keeps to.
        from matchcentre import cli
        form = ".github/ISSUE_TEMPLATE/card-not-read.yml"
        (self.project / form).write_text(
            f"description: `{example('0.1.0')}`.\n", encoding="utf-8")
        printed = []
        for version, when in (("0.2.0-rc.1", "2026-10-08"),
                              ("0.2.0", "2026-10-09")):
            ran = self.run_("prepare", version, "--date", when)
            self.assertEqual(ran.code, 0, ran.err)
            printed.append(ran.out)
            self.write("CHANGELOG.md", self.read("CHANGELOG.md").replace(
                "## [Unreleased]\n", "## [Unreleased]\n\n- One more.\n"))
        for out in printed:
            with self.subTest(out=out[:60]):
                long = [line for line in out.splitlines()
                        if len(line) > cli.WIDTH]
                self.assertEqual(long, [], out)
                self.assertIn(form, out.split())
        # The row that wraps goes on under its own column.
        self.assertIn("[0.2.0-rc.1]", printed[1])

    @unittest.skipUnless(shutil.which("git"), "there is no git to look at")
    def test_it_touches_no_git(self):
        repository = self.base / "repository"
        make_project(repository)
        git(repository, "init", "-q")
        git(repository, "add", "-A")
        git(repository, "commit", "-q", "-m", "the season so far")
        head = git(repository, "rev-parse", "HEAD")
        self.project = repository
        self.assertEqual(self.run_("prepare", "0.2.0").code, 0)
        self.assertEqual(git(repository, "rev-parse", "HEAD"), head)
        self.assertEqual(git(repository, "tag", "--list"), "")
        self.assertEqual(
            git(repository, "diff", "--cached", "--name-only"), "")

    # The two promises _write makes beside writing whole, which no test held:
    # measured by mutation, with the permissions not copied, or the link
    # replaced by the file, every test passed.
    @unittest.skipUnless(os.name == "posix", "a file's mode is POSIX's")
    def test_a_file_it_rewrites_keeps_its_permissions(self):
        target = self.project / "config.example.toml"
        target.chmod(0o640)
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        self.assertIn(example("0.2.0"), self.read("config.example.toml"))
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(os.name == "posix", "a link is POSIX's here")
    def test_a_link_is_followed_and_stays_a_link(self):
        elsewhere = self.base / "kept-elsewhere.md"
        elsewhere.write_text(self.read("README.md"), encoding="utf-8")
        link = self.project / "README.md"
        link.unlink()
        link.symlink_to(elsewhere)
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        self.assertTrue(link.is_symlink())
        self.assertIn(example("0.2.0"),
                      elsewhere.read_text(encoding="utf-8"))


class TestPrepareRefusesWhatWouldNotMakeARelease(ProjectCase):
    """Each refusal of ``prepare`` is a sentence, and it writes nothing.

    By hand, nothing stopped a version that was not a version (``0.2``), one
    older than the last, a release with nothing in its section, or a date
    that does not exist; the suite caught the first only in the issue forms,
    and none of the rest. Each is refused before any file is written, and
    every reason is given at once. Rejected: refusing in argparse's types,
    whose message names the argument twice and cannot give more than one.
    """

    def assertRefused(self, *argv: str, said: tuple[str, ...]) -> Ran:
        before = snapshot(self.project)
        ran = self.run_("prepare", *argv)
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(snapshot(self.project), before)
        for words in said:
            self.assertIn(words, " ".join(ran.err.split()))
        self.assertIn("Nothing was written.", ran.err)
        self.assertEqual(ran.out, "")
        return ran

    def test_a_version_that_is_not_a_semantic_version(self):
        for given in ("0.2", "v0.2.0", "0.2.0.1", "02.0.0", "next"):
            with self.subTest(version=given):
                self.assertRefused(given, said=("is not a semantic version",))

    def test_a_version_that_is_not_newer(self):
        for given in ("0.1.0", "0.0.9", "0.1.0-rc.1", "0.1.0+build.2"):
            with self.subTest(version=given):
                self.assertRefused(given, said=(
                    f"{given} is not newer than 0.1.0",))

    def test_a_pre_release_of_the_next_version_is_newer(self):
        ran = self.run_("prepare", "0.2.0-rc.1", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertIn("## [0.2.0-rc.1] — 2026-10-08\n",
                      self.read("CHANGELOG.md"))

    def test_an_unreleased_heading_in_another_case_is_moved_and_mended(self):
        self.write("CHANGELOG.md", self.read("CHANGELOG.md").replace(
            "## [Unreleased]\n", "## [unreleased]\n"))
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertEqual(self.read("CHANGELOG.md"),
                         RELEASED.format(example=example("0.1.0")))

    def test_a_changelog_with_no_unreleased_section(self):
        self.write("CHANGELOG.md", self.read("CHANGELOG.md").replace(
            "## [Unreleased]\n", ""))
        self.assertRefused("0.2.0", said=("no ## [Unreleased] section",))

    def test_an_unreleased_section_that_holds_nothing(self):
        # Its subsection headings, with nothing under them, are nothing.
        for held in ("", "\n\n", "### Added\n\n### Fixed\n"):
            with self.subTest(held=held):
                text = RELEASED.format(example=example("0.0.9"))
                self.write("CHANGELOG.md", text.replace(
                    "## [Unreleased]\n\n", f"## [Unreleased]\n\n{held}"))
                self.assertRefused("0.2.0", said=("holds nothing",))

    def test_a_date_that_is_not_a_real_date(self):
        # The last three are ISO dates that date.fromisoformat reads on 3.11
        # and later, and that no heading of this changelog is written as.
        for given in ("2026-02-30", "2026-13-01", "2026-1-5", "08/10/2026",
                      "yesterday", "2026-10-08T00:00:00", "20261008",
                      "2026-W41-4", "2026W414"):
            with self.subTest(date=given):
                self.assertRefused("0.2.0", "--date", given,
                                   said=("is not a real date",))

    def test_an_empty_date_is_refused_and_not_taken_for_today(self):
        # Measured before: `args.date or today` took an empty --date, as a
        # script passing an unset variable gives, for none, and dated the
        # release today with nothing said.
        for given in ("", " "):
            with self.subTest(date=given):
                ran = self.assertRefused("0.2.0", "--date", given, said=(
                    "error: An empty --date is not a real date, written "
                    "YYYY-MM-DD.",))
                self.assertNotIn("error:  ", ran.err)

    def test_a_package_with_no_version_line_or_two(self):
        for text in ('"""No version."""\n',
                     '__version__ = "0.1.0"\n__version__ = "0.1.0"\n'):
            with self.subTest(init=text):
                self.write("matchcentre/__init__.py", text)
                self.assertRefused("0.2.0", said=("__version__",))

    def test_a_version_the_changelog_already_has_a_section_for(self):
        # Measured before: no test wrote this case, so a prepare that let it
        # through would have written a second [0.2.0] section under the
        # first, and check would have read the old one as the release's.
        for version in ("0.2.0", "0.2.0-rc.1"):
            with self.subTest(version=version):
                self.write("CHANGELOG.md", UNRELEASED.replace(
                    "## [0.1.0]", f"## [{version}] — 2026-10-02\n\n- A "
                    f"change.\n\n## [0.1.0]").format(
                        example=example("0.1.0")))
                self.assertRefused(version, said=(
                    f"already has a [{version}] section",))

    def test_every_reason_is_given_at_once(self):
        self.write("CHANGELOG.md", self.read("CHANGELOG.md").replace(
            "## [Unreleased]\n", ""))
        ran = self.assertRefused("0.0.1", "--date", "2026-02-30", said=(
            "0.0.1 is not newer than 0.1.0", "no ## [Unreleased] section",
            "is not a real date"))
        self.assertEqual(ran.err.count("error: "), 3, ran.err)


@unittest.skipUnless(shutil.which("git"), "there is no git to ignore with")
class TestPrepareRewritesOnlyTheFilesGitKeeps(ProjectCase):
    """A file git ignores is not one of the examples ``prepare`` rewrites.

    Measured on the tree before this test: ``build.py --discover`` writes
    ``config.proposed.toml``, ignored by ``*.proposed.toml``, with a header
    naming the version that wrote it. prepare walked the whole folder, so it
    rewrote that header to a version that never ran, and listed the file
    among those the release changed, though ``git commit -am`` leaves it out.
    The suite's version test read the same ignored file, so a fix in one
    alone failed the other. Both now read the files git tracks, and the
    ones it would add, when the folder is a work tree, and walk it when it is
    not, as an unzipped release is. Rejected: a list of the ignored files'
    names, which is the .gitignore written out a second time.
    """

    def setUp(self):
        super().setUp()
        self.write(".gitignore", "*.proposed.toml\n/config.toml\n")
        git(self.project, "init", "-q")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "the season so far")
        self.ignored = {
            "config.proposed.toml": f"# Written by {example('0.1.0')} on "
                                    f"2026-10-01.\n",
            "config.toml": f"# {example('0.1.0')}\n"}
        for name, text in self.ignored.items():
            self.write(name, text)
        # A form not added yet, which the next commit is to carry.
        self.write(".github/ISSUE_TEMPLATE/new-form.yml",
                   f"description: `{example('0.1.0')}`.\n")

    def test_an_ignored_file_is_left_as_it_was_and_not_counted(self):
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        for name, text in self.ignored.items():
            with self.subTest(file=name):
                self.assertEqual(self.read(name), text)
                self.assertNotIn(name, ran.out.split())
        self.assertIn("in 7 files:", ran.out)

    def test_a_file_git_would_add_is_rewritten(self):
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        self.assertIn(example("0.2.0"),
                      self.read(".github/ISSUE_TEMPLATE/new-form.yml"))

    def test_the_suite_s_version_test_reads_the_same_files(self):
        import test_project_complete as complete
        plain = self.base / "unzipped"
        make_project(plain)
        for root in (self.project, plain):
            with self.subTest(root=root.name):
                self.assertEqual(sorted(release._scanned(root)),
                                 sorted(complete.version_scan(root)))
        self.assertNotIn(self.project / "config.proposed.toml",
                         list(complete.version_scan(self.project)))


class TestAPrepareThatCannotWriteAFileLeavesEveryFileAsItWas(ProjectCase):
    """A write that fails part way puts back the files written before it.

    Measured on the tree before this test, with the last file of six made
    unwritable: prepare exited 1 having written the version and the changelog
    and three examples, and said "The files listed before it were; put them
    back with git checkout", when no list had been printed, and when git
    checkout would also throw away any [Unreleased] entry not yet committed.
    Every file's text is already held to be rewritten, so the tool puts each
    back itself. Rejected: naming the files and leaving the rest to the
    maintainer, which leaves a half-bumped tree to be mended by hand.
    """

    def failing(self, *names: str, after: dict[str, int] | None = None):
        """The tool's one way of writing a file, failing for each of *names*
        once as many writes of it as *after* gives it, or none, have gone
        through."""
        real = release._write
        seen: dict[str, int] = {}
        after = after or {}

        def write(path, data):
            if path.name in names:
                seen[path.name] = seen.get(path.name, 0) + 1
                if seen[path.name] > after.get(path.name, 0):
                    raise PermissionError(13, "Permission denied")
            return real(path, data)
        return mock.patch.object(release, "_write", write)

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "the limit on a file's size is Linux's")
    def test_a_file_the_disk_takes_only_part_of_is_left_whole(self):
        # Measured before: each file was written over in place, so a write
        # the disk took part of left README.md cut short at the limit, and
        # the put-back, writing the same size again, failed the same way.
        # The limit is set in a child, so that this process keeps its own.
        self.write("README.md", self.read("README.md")
                   + "A long paragraph.\n" * 20000)
        # Compared by digest: compared as bytes, a README cut short sent
        # unittest's diff of the two into minutes of difflib, longer than
        # any run would wait to be told the test failed.
        def digests() -> dict[str, str]:
            return {name: hashlib.sha256(data).hexdigest()
                    for name, data in snapshot(self.project).items()}
        before = digests()
        limit = len(self.read("README.md")) // 2
        script = (
            "import resource, sys\n"
            f"sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'tools')!r}]\n"
            "import release\n"
            f"resource.setrlimit(resource.RLIMIT_FSIZE, ({limit}, {limit}))\n"
            f"sys.exit(release.main(['prepare', '0.2.0', '--date', "
            f"'2026-10-08'], root=release.Path({str(self.project)!r})))\n")
        done = subprocess.run(
            (sys.executable, "-c", script), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(digests(), before)
        said = " ".join(done.stderr.split())
        self.assertIn("README.md cannot be written", said)
        self.assertIn("nothing is changed", said)

    def test_the_files_written_before_it_are_put_back(self):
        before = snapshot(self.project)
        with self.failing("test_stand_in.py"):
            ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(snapshot(self.project), before)
        said = " ".join(ran.err.split())
        self.assertIn("tests/test_stand_in.py cannot be written: Permission "
                      "denied", said)
        self.assertIn("nothing is changed", said)
        self.assertNotIn("git checkout", said)
        self.assertEqual(ran.out, "")

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "a mode refuses nothing to root")
    def test_a_file_that_may_not_be_written_is_refused_not_replaced(self):
        # Its folder may be written, so a new file could be moved over it;
        # the tool refuses as a write in place would have. Measured by
        # mutation: without the check, the file was replaced and no test
        # failed.
        target = self.project / "tests" / "test_stand_in.py"
        target.chmod(0o444)
        self.addCleanup(target.chmod, 0o644)
        before = snapshot(self.project)
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(target.stat().st_mode & 0o777, 0o444)
        said = " ".join(ran.err.split())
        self.assertIn("tests/test_stand_in.py cannot be written: Permission "
                      "denied", said)
        self.assertIn("nothing is changed", said)

    def test_a_file_that_cannot_be_put_back_is_named(self):
        # README.md is written once, then refused when it is put back.
        with self.failing("test_stand_in.py", "README.md",
                          after={"README.md": 1}):
            ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        said = " ".join(ran.err.split())
        self.assertIn("README.md cannot be put back", said)
        self.assertNotIn("nothing is changed", said)
        self.assertEqual(self.read("matchcentre/__init__.py"),
                         '"""A stand-in package."""\n\n'
                         '__version__ = "0.1.0"\n')


# A changelog whose newest release is a release candidate of 0.2.0.
CANDIDATE = """\
# Changelog

## [Unreleased]
{unreleased}
## [0.2.0-rc.1] — 2026-10-06

### Added

- `tools/demo.py --day` chooses the night the fictional club plays.

### Fixed

- A card dated off its night is filed under the night its competition line
  names.

## [0.1.0] — 2026-10-01

Initial release, whose report began `{example}`.
"""


class TestAFinalReleaseGathersItsReleaseCandidates(ProjectCase):
    """``prepare X.Y.Z`` after ``X.Y.Z-rc.N`` makes the final from them.

    Measured on the tree before this test: once 0.2.0-rc.1 was released,
    ``prepare 0.2.0`` refused because ``[Unreleased]`` held nothing, though
    0.2.0 holds every change since 0.1.0, and ``check v0.2.0`` offered that
    same command. Adding an entry to get past it published a 0.2.0 whose
    notes held only that entry, while a club upgrading from 0.1.0, as most do
    since a candidate is opt-in, needs everything since 0.1.0. Now the
    candidates' sections and anything in ``[Unreleased]`` are gathered under
    the one ``[0.2.0]`` heading, subsection by subsection. The candidates
    keep their own sections below it, as they keep their own pages on
    GitHub: a first cut of this took them out, and then their tags'
    ``notes`` and ``check`` found no section, and the changelog no longer
    said what a club running the candidate has. Rejected: appending the
    candidates' sections to the final's notes at publish time, which leaves
    the changelog's own [0.2.0] empty, so that check refuses it.
    """

    VERSION = "0.2.0-rc.1"
    CHANGELOG = CANDIDATE.replace("{unreleased}", "")

    def test_an_empty_unreleased_gathers_the_candidate_under_the_final(self):
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertEqual(self.read("CHANGELOG.md"), (
            "# Changelog\n\n## [Unreleased]\n\n## [0.2.0] — 2026-10-08\n\n"
            "### Added\n\n"
            "- `tools/demo.py --day` chooses the night the fictional club "
            "plays.\n\n"
            "### Fixed\n\n"
            "- A card dated off its night is filed under the night its "
            "competition line\n  names.\n\n"
            "## [0.2.0-rc.1] — 2026-10-06\n\n"
            "### Added\n\n"
            "- `tools/demo.py --day` chooses the night the fictional club "
            "plays.\n\n"
            "### Fixed\n\n"
            "- A card dated off its night is filed under the night its "
            "competition line\n  names.\n\n"
            "## [0.1.0] — 2026-10-01\n\n"
            f"Initial release, whose report began `{example('0.1.0')}`.\n"))
        self.assertIn("[0.2.0-rc.1]", ran.out)
        self.assertEqual(self.run_("check", "v0.2.0").code, 0)

    def test_the_candidate_s_own_notes_are_still_there_after_the_final(self):
        # Measured before: the final took the candidate's section out, so
        # its notes, asked for again by a re-run of its tag, were refused.
        before = self.run_("notes", "0.2.0-rc.1")
        self.assertEqual(before.code, 0, before.err)
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        after = self.run_("notes", "0.2.0-rc.1")
        self.assertEqual((after.code, after.out), (0, before.out), after.err)

    def test_what_unreleased_holds_goes_first_under_its_own_subsection(self):
        self.write("CHANGELOG.md", CANDIDATE.format(
            example=example("0.1.0"), unreleased=(
                "\n### Fixed\n\n- A fix made after the candidate.\n\n"
                "### Changed\n\n- A change made after it.\n")))
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        section = self.run_("notes", "0.2.0").out
        self.assertEqual(section, (
            "### Fixed\n\n- A fix made after the candidate.\n"
            "- A card dated off its night is filed under the night its "
            "competition line names.\n\n"
            "### Changed\n\n- A change made after it.\n\n"
            "### Added\n\n"
            "- `tools/demo.py --day` chooses the night the fictional club "
            "plays.\n"))

    def test_a_code_line_like_a_subsection_heading_stays_in_its_code(self):
        # A comment in a fenced block is not a subsection to gather under.
        # Measured by mutation: read as one, the block was split in two
        # across the merge, and no test failed.
        block = ("```bash\n### the comment, not a subsection\n"
                 "python3 build.py\n```\n")
        self.write("CHANGELOG.md", CANDIDATE.format(
            example=example("0.1.0"),
            unreleased=f"\n### Changed\n\n{block}"))
        self.assertEqual(self.run_("prepare", "0.2.0", "--date",
                                   "2026-10-08").code, 0)
        section = self.run_("notes", "0.2.0").out
        self.assertTrue(section.startswith(f"### Changed\n\n{block}\n"
                                           "### Added\n"), section)

    def test_a_candidate_of_an_older_version_stays_where_it_is(self):
        # Only the candidates of 0.2.0 are 0.2.0's; one of 0.1.0 is history.
        older = ("## [0.1.0-rc.1] — 2026-09-20\n\n"
                 "- An older candidate.\n")
        self.write("CHANGELOG.md", self.read("CHANGELOG.md") + "\n" + older)
        ran = self.run_("prepare", "0.2.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        text = self.read("CHANGELOG.md")
        self.assertTrue(text.endswith("\n\n" + older), text)
        self.assertEqual(text.count("An older candidate"), 1, text)

    def test_a_second_candidate_gathers_nothing(self):
        # 0.2.0-rc.2 is a release of its own, not the end of rc.1.
        self.write("CHANGELOG.md", CANDIDATE.format(
            example=example("0.1.0"),
            unreleased="\n- A fix for the second candidate.\n"))
        before = self.read("CHANGELOG.md")
        ran = self.run_("prepare", "0.2.0-rc.2", "--date", "2026-10-08")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertEqual(self.read("CHANGELOG.md"), before.replace(
            "## [Unreleased]\n\n- A fix for the second candidate.\n",
            "## [Unreleased]\n\n## [0.2.0-rc.2] — 2026-10-08\n\n"
            "- A fix for the second candidate.\n"))

    def test_check_offers_prepare_for_the_final(self):
        ran = self.run_("check", "v0.2.0")
        self.assertEqual(ran.code, 1)
        self.assertIn("python3 tools/release.py prepare 0.2.0", ran.err)

    def test_a_candidate_of_another_version_is_not_gathered(self):
        # 0.3.0 is no promotion of 0.2.0-rc.1, so [Unreleased] must hold it.
        ran = self.run_("prepare", "0.3.0", "--date", "2026-10-08")
        self.assertEqual(ran.code, 1, ran.out)
        self.assertIn("holds nothing", " ".join(ran.err.split()))


class TestCheckSaysWhetherATagIsReadyToRelease(ProjectCase):
    """``check TAG`` is the first job of a release, and a maintainer's preview.

    A tag pushed before the bump, or before the changelog was moved, or with a
    typo in it, was a tag nothing looked at: no workflow ran on a tag
    (before.txt). Now it fails in seconds, before the 25 minutes of the
    suite, with every reason at once, so that one push fixes them all.
    Rejected: leaving it to the suite, whose version test catches two of these
    after 25 minutes and the rest never: it does not read a tag, a date or the
    ``[Unreleased]`` section.
    """

    VERSION = "0.2.0"
    CHANGELOG = RELEASED

    def problems(self, *argv: str) -> Ran:
        ran = self.run_("check", *argv)
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(ran.out, "")
        return ran

    def flat(self, ran: Ran) -> str:
        return " ".join(ran.err.split())

    def test_a_ready_tag_is_one_line_naming_the_version_and_its_date(self):
        ran = self.run_("check", "v0.2.0")
        self.assertEqual((ran.code, ran.err), (0, ""))
        self.assertEqual(len(ran.out.splitlines()), 1, ran.out)
        self.assertIn("0.2.0", ran.out)
        self.assertIn("2026-10-08", ran.out)

    def test_the_tag_is_v_and_the_version_in_the_code_when_none_is_given(self):
        self.assertEqual(self.run_("check").code, 0)
        self.write("matchcentre/__init__.py", '__version__ = "0.3.0"\n')
        self.assertIn("v0.3.0", self.flat(self.problems()))

    def test_with_no_tag_and_no_version_in_the_code_it_asks_for_a_tag(self):
        for lines, held in (("", "no __version__ lines"),
                            ('__version__ = "0.2.0"\n' * 2,
                             "2 __version__ lines")):
            with self.subTest(held=held):
                self.write("matchcentre/__init__.py",
                           '"""A stand-in package."""\n\n' + lines)
                self.assertEqual(self.flat(self.problems()), (
                    f"error: matchcentre/__init__.py has {held} where it "
                    f"should have one, so which version this is cannot be "
                    f"read. error: There is no tag to check: name one, "
                    f"such as v0.2.0. 2 problems, so the tag is not ready "
                    f"to release."))

    def test_with_no_tag_the_changelog_is_checked_all_the_same(self):
        # Measured before: with no version to make the tag of, the check
        # stopped there, and the changelog's problems were said only by
        # the run after the version was mended, where a tag that is no
        # version has them all said at once.
        self.write("matchcentre/__init__.py", '"""A stand-in package."""\n')
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- Not yet released.\n"))
        self.assertEqual(self.flat(self.problems()), (
            "error: matchcentre/__init__.py has no __version__ lines where "
            "it should have one, so which version this is cannot be read. "
            "error: There is no tag to check: name one, such as v0.2.0. "
            "error: CHANGELOG.md's [Unreleased] section still holds "
            "changes, and the release's notes would not mention them: move "
            "them under the release's heading, or out of the file. "
            "3 problems, so the tag is not ready to release."))

    def test_a_tag_that_is_not_v_and_a_version(self):
        for tag in ("0.2.0", "v0.2", "version-0.2.0", "v0.2.0.1", "V0.2.0",
                    "vv0.2.0"):
            with self.subTest(tag=tag):
                self.assertIn("is not a release tag",
                              self.flat(self.problems(tag)))

    def test_a_tag_that_is_not_the_version_in_the_code(self):
        said = self.flat(self.problems("v0.3.0"))
        self.assertIn("matchcentre/__init__.py says 0.2.0", said)

    def test_a_changelog_whose_newest_release_is_not_the_version(self):
        self.write("CHANGELOG.md", UNRELEASED.format(example="x"))
        said = self.flat(self.problems("v0.2.0"))
        self.assertIn("The newest release CHANGELOG.md describes is 0.1.0",
                      said)

    def test_prepare_is_offered_only_where_it_would_run(self):
        # Offered for a tag pushed before the bump, the one case it mends:
        # a newer version, with changes in [Unreleased] to move under it.
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- Not yet released.\n"))
        self.assertIn("python3 tools/release.py prepare 0.3.0",
                      self.flat(self.problems("v0.3.0")))
        # Not for an old tag, nor a version bumped by hand: prepare refuses
        # a version that is not newer than the code's.
        self.assertNotIn("prepare", self.flat(self.problems("v0.1.0")))
        # Nor where [Unreleased] holds nothing, or is not there, which is
        # every tree just after a release: prepare refuses both, and the
        # mend for a mistyped tag there is to delete it.
        for text in (RELEASED, RELEASED.replace("## [Unreleased]\n\n", ""),
                     RELEASED.replace("## [Unreleased]\n",
                                      "## [Unreleased]\n\n### Added\n")):
            with self.subTest(changelog=text[:120]):
                self.write("CHANGELOG.md", text.format(example="x"))
                self.assertNotIn("prepare", self.flat(self.problems("v0.3.0")))
        self.write("CHANGELOG.md", UNRELEASED.format(example="x"))
        self.assertNotIn("prepare", self.flat(self.problems("v0.2.0")))

    def test_a_command_or_a_heading_it_gives_is_whole_on_its_line(self):
        # A command broken after `python3` pastes as a bare Python.
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- Not yet released.\n"))
        said = self.problems("v0.3.0").err
        self.assertIn("python3 tools/release.py prepare 0.3.0", said)
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            "## [0.2.0] — 2026-10-08", "## [0.2.0]"))
        said = self.problems("v0.2.0").err
        self.assertIn("## [0.2.0] — YYYY-MM-DD", said)

    def test_a_section_too_long_for_a_release_page_is_refused(self):
        # GitHub refuses a release's notes over 125,000 characters, which
        # publish would learn only after the suite's 25 minutes.
        item = "- " + "word " * 19 + "end.\n"
        for count, refused in ((1100, False), (1250, True)):
            with self.subTest(items=count):
                self.write("CHANGELOG.md", RELEASED.format(example="x")
                           .replace("### Fixed\n", "### Fixed\n\n"
                                    + item * count))
                ran = self.run_("check", "v0.2.0")
                self.assertEqual(ran.code, int(refused), ran.err)
                if refused:
                    said = self.flat(ran)
                    self.assertIn("[0.2.0] section is", said)
                    self.assertIn("125,000", said)

    def test_the_limit_leaves_room_for_the_lines_that_close_the_notes(self):
        # Measured before: the sections tried were 112,445 and 127,745
        # characters, either side of the margin, so a check without it
        # passed them both, and publish's notes went over GitHub's limit.
        allowed = release.NOTES_LIMIT - release.CLOSING
        for length, refused in ((allowed, False), (allowed + 1, True)):
            with self.subTest(length=length):
                # One line, and its newline, which is the length unwrapped.
                line = "A" + "b" * (length - 2)
                self.write("CHANGELOG.md", (
                    "# Changelog\n\n## [Unreleased]\n\n"
                    f"## [0.2.0] — 2026-10-08\n\n{line}\n\n"
                    "## [0.1.0] — 2026-10-01\n\n- first\n"))
                body = next(each for name, _, each in release.sections(
                    self.read("CHANGELOG.md")) if name == "0.2.0")
                self.assertEqual(len(release.unwrap(release._trimmed(body))),
                                 length)
                ran = self.run_("check", "v0.2.0")
                self.assertEqual(ran.code, int(refused), ran.err)
                if refused:
                    self.assertIn(f"{length:,} characters", self.flat(ran))
                    continue
                archive = self.base / f"{NAME}-0.2.0.zip"
                archive.write_bytes(b"bytes to hash")
                url = ("https://github.example/" + "an-owner-" * 4
                       + "/" + "a-repository-" * 4
                       + "/actions/runs/123456789012")
                notes = self.run_("notes", "--archive", str(archive),
                                  "--run-url", url).out
                self.assertLessEqual(len(notes), release.NOTES_LIMIT)
                self.assertGreater(len(notes), length)

    def test_a_heading_with_no_date_or_one_that_is_no_date(self):
        for heading, words in (("## [0.2.0]", "has no date"),
                               ("## [0.2.0] —", "has no date"),
                               ("## [0.2.0] — 2026-02-30",
                                "is not a real date"),
                               ("## [0.2.0] — 8 October 2026",
                                "is not a real date"),
                               ("## [0.2.0] — 20261008",
                                "is not a real date"),
                               ("## [0.2.0] — 2026-W41-4",
                                "is not a real date")):
            with self.subTest(heading=heading):
                self.write("CHANGELOG.md", RELEASED.format(
                    example="x").replace("## [0.2.0] — 2026-10-08", heading))
                self.assertIn(words, self.flat(self.problems("v0.2.0")))

    def test_a_section_that_holds_nothing(self):
        text = RELEASED.format(example="x")
        start = text.index("### Fixed")
        self.write("CHANGELOG.md",
                   text[:start] + text[text.index("## [0.1.0]"):])
        self.assertIn("[0.2.0] section holds nothing",
                      self.flat(self.problems("v0.2.0")))

    def test_a_changelog_that_describes_no_release_is_not_ready(self):
        # Measured by mutation: with this unsaid, a changelog of nothing but
        # an empty [Unreleased] was ready, and check said it would release
        # 0.2.0, "dated None".
        for text in ("# Changelog\n\n## [Unreleased]\n", "# Changelog\n"):
            with self.subTest(changelog=text):
                self.write("CHANGELOG.md", text)
                self.assertEqual(self.flat(self.problems("v0.2.0")),
                                 "error: CHANGELOG.md describes no release "
                                 "at all. 1 problem, so v0.2.0 is not ready "
                                 "to release.")

    def test_an_unreleased_section_that_still_holds_something(self):
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- Not yet released.\n"))
        said = self.flat(self.problems("v0.2.0"))
        self.assertIn("[Unreleased] section still holds", said)
        self.assertIn("would not mention them", said)

    def test_unreleased_is_read_whatever_its_case(self):
        # Measured before: `## [unreleased]` was taken for the newest
        # release, and check asked for a date on it and called it not newer
        # than 0.2.0, while the changes under it went unmentioned.
        text = RELEASED.format(example="x")
        self.write("CHANGELOG.md", text.replace(
            "## [Unreleased]\n", "## [unreleased]\n\n- Not yet released.\n"))
        said = self.flat(self.problems("v0.2.0"))
        self.assertIn("[Unreleased] section still holds", said)
        self.assertEqual(said.count("error: "), 1, said)
        # With nothing under it, the release is ready.
        self.write("CHANGELOG.md", text.replace("## [Unreleased]",
                                                "## [UNRELEASED]"))
        ran = self.run_("check", "v0.2.0")
        self.assertEqual(ran.code, 0, ran.err)

    def test_a_bracketed_heading_that_is_neither_unreleased_nor_a_version(
            self):
        for name, held in (("Next", "\n- A change for later.\n"),
                           ("Next", ""), ("v0.2.1", "")):
            with self.subTest(name=name, held=held):
                self.write("CHANGELOG.md", RELEASED.format(
                    example="x").replace("## [Unreleased]\n",
                                         f"## [Unreleased]\n\n## [{name}]"
                                         f"\n{held}"))
                said = self.flat(self.problems("v0.2.0"))
                self.assertIn(f"## [{name}] is neither [Unreleased] nor a "
                              f"version", said)
                self.assertEqual(said.count("error: "), 1, said)

    def test_a_version_that_is_not_newer_than_the_release_before_it(self):
        for previous in ("0.2.0", "0.3.0", "0.2.0+build.1"):
            with self.subTest(previous=previous):
                self.write("CHANGELOG.md", RELEASED.format(
                    example="x").replace("## [0.1.0]", f"## [{previous}]"))
                self.assertIn(f"0.2.0 is not newer than {previous}",
                              self.flat(self.problems("v0.2.0")))

    def test_a_heading_written_with_a_hyphen_is_read_too(self):
        # Keep a Changelog writes ` - `; this file writes an em dash.
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            " — ", " - "))
        ran = self.run_("check", "v0.2.0")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertIn("2026-10-08", ran.out)

    def test_every_problem_is_given_at_once(self):
        self.write("CHANGELOG.md", UNRELEASED.format(example="x").replace(
            "## [0.1.0] — 2026-10-01", "## [0.1.0]"))
        said = self.flat(self.problems("v0.3.0"))
        for words in ("matchcentre/__init__.py says 0.2.0",
                      "The newest release CHANGELOG.md describes is 0.1.0",
                      "has no date", "[Unreleased] section still holds"):
            with self.subTest(problem=words):
                self.assertIn(words, said)
        self.assertEqual(said.count("error: "), 4, said)


class TestAFileThatCannotBeReadIsNamedAndNothingIsDone(ProjectCase):
    """A project file the tool cannot read is named, with why, by each
    command that reads it, and nothing is written or printed.

    ``prepare``, ``check`` and ``notes`` each read the package file and the
    changelog, and no test gave them one they could not read: measured by
    mutation, with either refusal gone, every test passed, and a changelog
    saved in Windows-1252, or a folder where a file should be, escaped from
    all three as a traceback. Each failure here is a real one: the file's
    text with an em dash in it, saved as such an editor saves it, where the
    dash is the byte 0x97, which UTF-8 has no reading for; and a folder in
    the file's place. Rejected: reading with ``errors="replace"``, which
    would publish notes with a replacement character where each dash was,
    and have prepare write the file back so.
    """

    # Each command, a project it would run on but for the file, and the line
    # its refusal ends with.
    COMMANDS = (
        (("prepare", "0.2.0", "--date", "2026-10-08"), "0.1.0", UNRELEASED,
         "Nothing was written."),
        (("check", "v0.2.0"), "0.2.0", RELEASED,
         "1 problem, so v0.2.0 is not ready to release."),
        (("notes",), "0.2.0", RELEASED, "No notes were printed."))

    def refused(self, spoil, said: str) -> None:
        """Each command refuses, as *said* says, each of the two files that
        *spoil* has made unreadable, and leaves the folder as it was."""
        for argv, version, changelog, ending in self.COMMANDS:
            for name in ("matchcentre/__init__.py", "CHANGELOG.md"):
                with self.subTest(command=argv[0], file=name):
                    self.project = Path(tempfile.mkdtemp(dir=self.base))
                    make_project(self.project, version=version,
                                 changelog=changelog)
                    spoil(self.project / name)
                    before = snapshot(self.project)
                    ran = self.run_(*argv)
                    self.assertEqual((ran.code, ran.out), (1, ""), ran.err)
                    self.assertEqual(" ".join(ran.err.split()),
                                     f"error: {name} {said} {ending}")
                    self.assertEqual(snapshot(self.project), before)

    def test_a_file_that_is_not_utf_8_is_named_and_nothing_is_written(self):
        def in_windows_1252(path: Path) -> None:
            text = path.read_text(encoding="utf-8").replace(
                "A stand-in package.", "A stand-in — the package.")
            path.write_bytes(text.encode("cp1252"))
            self.assertIn(b"\x97", path.read_bytes())    # the em dash
        self.refused(in_windows_1252,
                     "is not UTF-8, which this tool reads it as.")

    def test_a_file_that_cannot_be_read_is_named_with_why(self):
        def a_folder(path: Path) -> None:
            path.unlink()
            path.mkdir()
        # The reason is the system's own, as it gives it for reading a
        # folder: "Is a directory" on Linux and macOS, "Permission denied"
        # on Windows.
        with self.assertRaises(OSError) as raised:
            self.base.read_bytes()
        self.refused(a_folder,
                     f"cannot be read: {raised.exception.strerror}.")


class TestTheNotesShowEachParagraphAsOneLine(ProjectCase):
    """``notes`` prints the release's notes: the changelog's section,
    unwrapped.

    The changelog is hard-wrapped at about 85 columns, and a release page that
    renders a single newline as a break shows each sentence broken where the
    editor broke it; before this, the notes were that text pasted. Each run of
    wrapped lines is joined into one, and what Markdown reads by its line is
    left alone: code, headings, tables, quotes, rules, the line that starts an
    item, and a line after a hard break. A numbered line inside a sentence,
    ``7. The exit``, is joined: Markdown starts no list there unless the number
    is 1 or a list is already running (CommonMark 0.31, 5.2). Rejected:
    writing the changelog unwrapped, which every other reader of the file, and
    the column rule the project keeps, would pay for.
    """

    VERSION = "0.2.0"
    CHANGELOG = RELEASED

    def test_the_notes_are_the_section_without_its_heading(self):
        ran = self.run_("notes", "0.2.0")
        self.assertEqual((ran.code, ran.err), (0, ""))
        self.assertEqual(ran.out, BODY)

    def test_the_version_is_the_one_in_the_code_when_none_is_given(self):
        self.assertEqual(self.run_("notes").out, BODY)

    def test_a_level_one_heading_ends_the_section_before_it(self):
        # What _index promises of a closing `#` heading, which no test held:
        # measured by mutation, with only level two ending a section, every
        # test passed and the oldest release's notes took in what followed.
        self.write("CHANGELOG.md", RELEASED.format(example=example("0.1.0"))
                   + "\n# Links\n\nA closing line, nobody's notes.\n")
        ran = self.run_("notes", "0.1.0")
        self.assertEqual((ran.code, ran.err), (0, ""))
        self.assertIn("`build.py`: the single command.", ran.out)
        self.assertNotIn("Links", ran.out)
        self.assertNotIn("nobody's notes", ran.out)

    def test_a_version_the_changelog_does_not_describe_is_refused(self):
        ran = self.run_("notes", "0.3.0")
        self.assertEqual((ran.code, ran.out), (1, ""))
        self.assertIn("no [0.3.0] section", " ".join(ran.err.split()))

    def test_what_markdown_reads_by_its_line_is_left_alone(self):
        given = (
            "A paragraph wrapped\n"
            "over three\n"
            "lines.\n"
            "\n"
            "```text\n"
            "code is\n"
            "  left alone\n"
            "```\n"
            "~~~\n"
            "and so\n"
            "~~~\n"
            "# A heading\n"
            "### Another\n"
            "| a | b |\n"
            "|---|---|\n"
            "| 1 | 2 |\n"
            "> a quote\n"
            "> goes on\n"
            "- an item\n"
            "  wrapped\n"
            "* a starred item\n"
            "+ a plus item\n"
            "  - an indented item\n"
            "    wrapped too\n"
            "1. one\n"
            "   wrapped\n"
            "2) two\n"
            "10. ten\n"
            "---\n"
            "* * *\n"
            "A setext heading\n"
            "================\n"
            "A hard break  \n"
            "after it\n"
            "and one\\\n"
            "after that\n"
            "- an item whose wrap\n"
            "  7. starts with a number\n"
            "- and one\n"
            "  1. that starts a list\n"
            "  2. that goes on\n")
        self.assertEqual(release.unwrap(given), (
            "A paragraph wrapped over three lines.\n"
            "\n"
            "```text\n"
            "code is\n"
            "  left alone\n"
            "```\n"
            "~~~\n"
            "and so\n"
            "~~~\n"
            "# A heading\n"
            "### Another\n"
            "| a | b |\n"
            "|---|---|\n"
            "| 1 | 2 |\n"
            "> a quote\n"
            "> goes on\n"
            "- an item wrapped\n"
            "* a starred item\n"
            "+ a plus item\n"
            "  - an indented item wrapped too\n"
            "1. one wrapped\n"
            "2) two\n"
            "10. ten\n"
            "---\n"
            "* * *\n"
            "A setext heading\n"
            "================\n"
            "A hard break  \n"
            "after it and one\\\n"
            "after that\n"
            "- an item whose wrap 7. starts with a number\n"
            "- and one\n"
            "  1. that starts a list\n"
            "  2. that goes on\n"))

    def test_a_hard_break_on_a_wrapped_line_is_kept(self):
        # Measured before: the join stripped the two spaces of the line it
        # joined, so the line after the break was joined too.
        for given, unwrapped in (
                ("A paragraph\nwith a break  \nafter it\n",
                 "A paragraph with a break  \nafter it\n"),
                ("- An item whose second line\n  ends in a break:  \n"
                 "  this line starts a new one.\n",
                 "- An item whose second line ends in a break:  \n"
                 "  this line starts a new one.\n")):
            with self.subTest(given=given):
                self.assertEqual(release.unwrap(given), unwrapped)

    def test_a_table_without_its_outer_pipes_is_left_alone(self):
        # GFM makes the outer pipes optional, and the delimiter row is
        # neither a rule nor an underline.
        given = ("Before it.\n\nName | Value\n--- | :---:\nx | y\nz | w\n\n"
                 "After\nit.\n")
        self.assertEqual(release.unwrap(given), (
            "Before it.\n\nName | Value\n--- | :---:\nx | y\nz | w\n\n"
            "After it.\n"))

    def test_an_indented_code_block_is_left_alone(self):
        for given in ("Text:\n\n    code one\n    code two\n",
                      "- An item:\n\n      code one\n      code two\n",
                      "# A heading\n    code one\n    code two\n"):
            with self.subTest(given=given):
                self.assertEqual(release.unwrap(given), given)
        # Four spaces inside an item are the item's own paragraph, joined.
        self.assertEqual(release.unwrap("- An item:\n\n    wrapped\n    on\n"),
                         "- An item:\n\n    wrapped on\n")
        # And an indented line straight after a paragraph's is its
        # continuation, which code cannot interrupt.
        self.assertEqual(release.unwrap("A paragraph\n    wrapped on\n"),
                         "A paragraph wrapped on\n")

    def test_indented_code_that_looks_like_an_item_or_a_fence_is_code(self):
        # Measured before: the line's own `- ` or `1. ` was taken for an
        # item before the code was looked for, so the code was measured
        # from its own content and its lines joined; and four spaces and a
        # fence at the top level, or a fence whose info string holds a
        # backtick, which CommonMark reads as code and as an inline span
        # (4.4, 4.5), opened a fence nothing closed, which hid every older
        # heading.
        for given in (
                "- The job checks out the whole history:\n\n"
                "      - uses: actions/checkout@v5\n"
                "        with:\n"
                "          fetch-depth: 0\n",
                "- The steps:\n\n      1. one\n         two\n",
                "A sample:\n\n    - item\n      more\n"):
            with self.subTest(given=given):
                self.assertEqual(release.unwrap(given), given)
        for opening in ("    ```", "```x``` is now spelled out."):
            with self.subTest(opening=opening):
                text = (f"## [0.2.0] — 2026-10-08\n\nA fence is opened "
                        f"with:\n\n{opening}\n\n## [0.1.0] — 2026-10-01"
                        f"\n\n- First.\n")
                self.assertEqual(
                    [name for name, _, _ in release.sections(text)],
                    ["0.2.0", "0.1.0"])

    def test_a_heading_or_a_rule_in_a_deeper_item_is_not_joined(self):
        # Measured before: they were matched with up to three spaces before
        # them from the margin, not from the item's content, so in an item
        # whose content starts four or more columns in they were joined
        # into the paragraph above them.
        for given in ("- Outer item:\n  - An inner item\n    ### Its heading"
                      "\n",
                      "10. An item\n    ### Its heading\n",
                      "10. An item\n    ***\n",
                      "10. An item\n    wrapped\n    =======\n"):
            with self.subTest(given=given):
                lines = release.unwrap(given).splitlines()
                self.assertEqual(lines[-1], given.splitlines()[-1])

    def test_a_fence_left_open_in_an_item_ends_with_the_item(self):
        # CommonMark ends an unclosed fence with the block that holds it,
        # here the list item, so the heading after it is a heading on GitHub.
        text = ("## [Unreleased]\n\n- An item quoting\n  ```\n  code\n"
                "- Another item\n\n## [0.1.0] — 2026-10-01\n\n- first\n")
        self.assertEqual([name for name, _, _ in release.sections(text)],
                         ["Unreleased", "0.1.0"])
        self.assertEqual(release.unwrap("- An item quoting\n  ```\n  code\n"
                                        "A paragraph\nwrapped\n"),
                         "- An item quoting\n  ```\n  code\n"
                         "A paragraph wrapped\n")
        # A fence indented past its item's content is still in the item, and
        # a line indented as the content is still in the fence.
        kept = "- item\n   ```\n  code\n  more\n   ```\n"
        self.assertEqual(release.unwrap(kept), kept)
        # A fence at the top level, indented, is ended only by its closing.
        text = "## [Unreleased]\n\n  ```\n## [0.1.0]\n  ```\n"
        self.assertEqual([name for name, _, _ in release.sections(text)],
                         ["Unreleased"])
        # And so is one opened after a list has ended.
        text = ("## [Unreleased]\n\n- An item\n\nA paragraph.\n\n```\n"
                "## [0.0.1] quoted, not a heading\n```\n\n"
                "## [0.1.0] — 2026-10-01\n\n- first\n")
        self.assertEqual([name for name, _, _ in release.sections(text)],
                         ["Unreleased", "0.1.0"])

    def test_a_fence_is_closed_only_by_one_at_least_as_long(self):
        # CommonMark 0.31 (4.5): a closing fence is at least as long as its
        # opening, so a longer fence can quote a shorter one. No test held it:
        # measured by mutation, with any fence closing any other, every test
        # passed and the quoted heading below was read as a release's.
        text = ("## [Unreleased]\n\n````markdown\n```\n"
                "## [0.0.1] quoted, not a heading\n```\n````\n\n"
                "## [0.1.0] — 2026-10-01\n\n- first\n")
        self.assertEqual([name for name, _, _ in release.sections(text)],
                         ["Unreleased", "0.1.0"])

    def test_a_deeper_item_s_heading_is_read_from_its_content(self):
        # A heading or rule four columns in, in an item whose content is
        # four columns in, ends the item's paragraph, so the line after it at
        # the margin is no lazy line: it ends the list, and a fence four
        # columns past the margin cannot interrupt its paragraph (CommonMark
        # 0.31, 4.5, 5.1). No test held the fence reader to it: measured by
        # mutation, with the heading read from the margin, every test passed
        # and the paragraph was kept broken as though it were code.
        for block in ("    ## A heading", "    - - -"):
            with self.subTest(block=block):
                given = (f"- outer\n  - inner\n\n{block}\ntext\n    ```\n"
                         f"    code a\n    code b\n")
                self.assertEqual(release.unwrap(given),
                                 f"- outer\n  - inner\n\n{block}\n"
                                 f"text ``` code a code b\n")

    def test_a_lazy_line_keeps_its_item_and_the_fence_in_it_open(self):
        # Measured before: a wrapped line of an item written at the margin,
        # which CommonMark reads as the item's still (5.1), ended the item,
        # so a fence opened in it after was ended by nothing, and the older
        # heading below it was hidden again.
        text = ("## [Unreleased]\n\n### Fixed\n\n- A fix whose example\n"
                "wraps:\n  ```\n  python3 build.py\n\n"
                "## [0.1.0] — 2026-10-01\n\n- old\n")
        self.assertEqual([name for name, _, _ in release.sections(text)],
                         ["Unreleased", "0.1.0"])
        # A paragraph four columns in, after a lazy line, is the item's, not
        # code.
        self.assertEqual(release.unwrap("- An item\nlazy\n\n    A "
                                        "paragraph\n    wrapped\n"),
                         "- An item lazy\n\n    A paragraph wrapped\n")
        # What starts a block is no lazy line, and ends the item: what is
        # left open is only the item the line starts, if it starts one.
        for start, items in (("# A heading", []), ("> A quote", []),
                             ("***", []), ("```", []), ("- Another", [2]),
                             ("1. One", [3]), ("lazy", [2])):
            with self.subTest(start=start):
                fenced = fence_reader_after("- An item\n" + start + "\n")
                self.assertEqual(fenced.items, items)

    def test_a_lazy_line_does_not_fold_the_older_section_into_the_new(self):
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n"
                   "- A fix whose example\nwraps:\n  ```\n  python3 "
                   "build.py\n\n## [0.2.0] — 2026-10-08\n\n- old\n")
        self.write("matchcentre/__init__.py", '__version__ = "0.2.0"\n')
        ran = self.run_("prepare", "0.3.0", "--date", "2026-10-09")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertEqual(
            [name for name, _, _ in release.sections(
                self.read("CHANGELOG.md"))],
            ["Unreleased", "0.3.0", "0.2.0"])
        self.assertNotIn("old", self.run_("notes", "0.3.0").out)

    def test_a_tab_is_four_columns_as_markdown_reads_it(self):
        # Measured before: indentation was counted in spaces, so a line
        # indented by a tab was read at the margin.
        for given in ("Para\n\n\tcode line 1\n\tcode line 2\n\nAfter\n",
                      "- An item:\n\n\t  code one\n\t  code two\n"):
            with self.subTest(given=given):
                self.assertEqual(release.unwrap(given), given)
        # A tab-indented line in a fence in an item is still in the fence,
        # so the fence's closing line closes it and opens nothing.
        text = ("## [Unreleased]\n\n- An item quoting\n  ```\n\tcode\n"
                "  ```\n\n## [0.1.0] — 2026-10-01\n\n- first\n")
        self.assertEqual([name for name, _, _ in release.sections(text)],
                         ["Unreleased", "0.1.0"])

    def test_a_row_without_a_pipe_after_a_table_is_a_row(self):
        # GFM ends a table at a blank line or another block, not at the
        # first line without a pipe (GFM 0.29, example 201).
        given = ("| a | b |\n|---|---|\n| 1 | 2 |\nbar\nbaz\n\nafter\n"
                 "it\n")
        self.assertEqual(release.unwrap(given), (
            "| a | b |\n|---|---|\n| 1 | 2 |\nbar\nbaz\n\nafter it\n"))
        given = "| a |\n|---|\n| 1 |\n> a quote\n> wrapped\n"
        self.assertEqual(release.unwrap(given), given)

    def test_every_word_of_every_real_section_is_kept_in_order(self):
        # The project's own changelog, every section of it, as a check that the
        # joins lose and add nothing on prose nobody wrote for this test.
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for name, _, body in release.sections(text):
            with self.subTest(section=name):
                self.assertEqual(release.unwrap(body).split(), body.split())

    def test_the_archive_line_names_the_file_and_its_sha_256(self):
        archive = self.base / "an archive.zip"
        archive.write_bytes(b"not really a zip, but bytes to hash")
        ran = self.run_("notes", "--archive", str(archive))
        self.assertEqual(ran.code, 0, ran.err)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertTrue(ran.out.startswith(BODY), ran.out)
        last = ran.out.rstrip("\n").splitlines()[-1]
        self.assertIn("an archive.zip", last)
        self.assertIn(digest, last)
        self.assertIn("SHA-256", last)

    def test_the_closing_lines_follow_a_rule_after_a_blank_line(self):
        # Measured before: each test read only the last line, so a rule
        # dropped, or the blank line before it, which makes `---` under the
        # last item a setext heading of it on GitHub, passed them all.
        archive = self.base / "an archive.zip"
        archive.write_bytes(b"not really a zip, but bytes to hash")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        url = ("https://github.example/example-owner/example-repo"
               "/actions/runs/7")
        sha = f"SHA-256 of `an archive.zip`: `{digest}`\n"
        run = f"This release was tested by the workflow run at {url}.\n"
        for argv, closing in (
                (("--archive", str(archive)), sha),
                (("--run-url", url), run),
                (("--archive", str(archive), "--run-url", url),
                 sha + "\n" + run)):
            with self.subTest(argv=argv[::2]):
                ran = self.run_("notes", *argv)
                self.assertEqual((ran.code, ran.err), (0, ""))
                self.assertEqual(ran.out, BODY + "\n---\n\n" + closing)

    def test_the_run_line_says_which_run_tested_it(self):
        url = ("https://github.example/example-owner/example-repo"
               "/actions/runs/7")
        ran = self.run_("notes", "--run-url", url)
        last = ran.out.rstrip("\n").splitlines()[-1]
        self.assertIn(url, last)
        self.assertIn("tested", last)

    def test_an_archive_that_cannot_be_read_is_refused(self):
        ran = self.run_("notes", "--archive", str(self.base / "absent.zip"))
        self.assertEqual((ran.code, ran.out), (1, ""))
        self.assertIn("absent.zip", ran.err)


def git(root: Path, *argv: str) -> str:
    """git in *root*, as somebody with no settings of their own runs it, and
    with no repository of theirs named: see test_project_complete.git_env."""
    from test_project_complete import git_env
    done = subprocess.run(
        ("git", "-c", "init.defaultBranch=main", "-c", "commit.gpgSign=false",
         "-c", "tag.gpgSign=false", *argv),
        cwd=root, capture_output=True, text=True, check=True,
        env=git_env(GIT_AUTHOR_NAME="Stand-in Maintainer",
                    GIT_AUTHOR_EMAIL="maintainer@example.invalid",
                    GIT_COMMITTER_NAME="Stand-in Maintainer",
                    GIT_COMMITTER_EMAIL="maintainer@example.invalid",
                    GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                    PYTHONDONTWRITEBYTECODE="1"))
    return done.stdout.strip()


# The stand-in gh. It logs what it was asked, and the notes file it was handed
# as it was when it was handed it, and answers `release view` as the test says:
# as gh does, with the release's fields and its draft state when it is there,
# and with "release not found" or another error when it is not.
STAND_IN = '''\
#!{python}
import json, os, sys
argv = sys.argv[1:]
entry = {{"argv": argv}}
if "--notes-file" in argv:
    with open(argv[argv.index("--notes-file") + 1], encoding="utf-8") as f:
        entry["notes"] = f.read()
with open(os.environ["STAND_IN_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(entry) + "\\n")
if argv[:2] == ["release", "view"]:
    code = int(os.environ.get("STAND_IN_VIEW", "1"))
    if code:
        print(os.environ.get("STAND_IN_VIEW_SAYS", "release not found"),
              file=sys.stderr)
    else:
        print("tag:\\t" + argv[2] + "\\ndraft:\\t"
              + os.environ.get("STAND_IN_DRAFT", "false"))
    sys.exit(code)
if argv[1:2] == [os.environ.get("STAND_IN_FAIL")]:
    print(os.environ.get("STAND_IN_FAIL_SAYS", ""), file=sys.stderr)
    sys.exit(1)
'''


@unittest.skipIf(os.name == "nt", "the stand-in gh is a POSIX script")
@unittest.skipUnless(shutil.which("git"), "there is no git to tag with")
class PublishCase(ProjectCase):
    """A tagged repository, and a stand-in gh first on PATH, per test."""

    VERSION = "0.2.0"
    CHANGELOG = RELEASED
    TAG = "v0.2.0"

    def setUp(self):
        super().setUp()
        (self.project / ".gitignore").write_text("/dist/\nignored.txt\n",
                                                 encoding="utf-8")
        git(self.project, "init", "-q")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "the release")
        git(self.project, "tag", "-a", self.TAG, "-m", "the release")
        (self.project / "ignored.txt").write_text("ignored\n",
                                                  encoding="utf-8")
        (self.project / "untracked.txt").write_text("not added\n",
                                                    encoding="utf-8")
        bin_ = self.base / "bin"
        bin_.mkdir()
        gh = bin_ / "gh"
        gh.write_text(STAND_IN.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        self.log = self.base / "gh.log"
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}",
            "STAND_IN_LOG": str(self.log), "STAND_IN_VIEW": "1",
            # The repository gh releases to, as the workflow names it.
            "GH_REPO": REPOSITORY})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Run by hand, as the tool is outside a workflow: GITHUB_ACTIONS,
        # which tests.yml's own run sets, is what says a run is one.
        for name in (*RUN_VARIABLES, "GITHUB_ACTIONS"):
            os.environ.pop(name, None)
        self.zip = self.project / "dist" / f"{NAME}-{self.version()}.zip"

    def version(self) -> str:
        return self.TAG[1:]

    def asked(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines()]

    def argvs(self) -> list[list[str]]:
        return [entry["argv"] for entry in self.asked()]

    def title(self) -> str:
        return f"{NAME} {self.version()}"


class TestPublishMakesTheReleaseFromTheTaggedCommit(PublishCase):
    """``publish TAG`` zips the tagged tree and makes the release with ``gh``.

    By hand, the archive a club downloaded was GitHub's own *Source code* zip,
    named after the repository rather than the project, with no checksum
    anywhere, and the notes were whatever was pasted. Now the archive is
    ``git archive`` of the tag, named ``matchcentre-dashboard-X.Y.Z.zip``, and
    the notes are the changelog's section with the archive's SHA-256 and the
    run that tested it. Rejected: an action from the marketplace to make the
    release, which is a third party's code run with write access to the
    repository, where ``gh`` is already on every runner.
    """

    def test_a_new_release_is_created_with_the_archive_and_the_notes(self):
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        self.assertEqual(self.argvs(), [
            ["release", "view", self.TAG],
            ["release", "create", self.TAG, f"dist/{self.zip.name}",
             "--verify-tag", "--title", self.title(),
             "--notes-file", "dist/notes.md"]])
        digest = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        notes = self.asked()[1]["notes"]
        self.assertTrue(notes.startswith(BODY), notes)
        self.assertIn(self.zip.name, notes)
        self.assertIn(digest, notes)
        self.assertNotIn("actions/runs", notes)
        self.assertEqual(notes, (self.project / "dist" / "notes.md")
                         .read_text(encoding="utf-8"))

    def test_a_re_run_updates_the_release_that_is_there(self):
        # A job that failed after `create` is re-run, and must not need the
        # release deleted by hand first.
        os.environ["STAND_IN_VIEW"] = "0"
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        self.assertEqual(self.argvs(), [
            ["release", "view", self.TAG],
            ["release", "upload", self.TAG, f"dist/{self.zip.name}",
             "--clobber"],
            ["release", "edit", self.TAG, "--title", self.title(),
             "--notes-file", "dist/notes.md", "--prerelease=false",
             "--draft=false"]])
        self.assertTrue(self.asked()[2]["notes"].startswith(BODY))

    def test_a_draft_the_re_run_finds_is_published(self):
        # gh's view finds a draft by the tag it will have, and the update
        # path is where a draft is met: GitHub turns a release into one when
        # its tag is deleted, which is CONTRIBUTING's mend for a wrong tag,
        # gh create leaves one when it is killed while uploading, and the
        # old way of releasing saved one by hand. Measured before: the edit
        # gave no --draft, so the draft stayed a draft and the run said
        # "Updated". gh edit changes only what it is given, and
        # --draft=false is nothing to a release already published.
        os.environ.update({"STAND_IN_VIEW": "0", "STAND_IN_DRAFT": "true"})
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        edit = self.argvs()[2]
        self.assertEqual(edit[:3], ["release", "edit", self.TAG])
        self.assertIn("--draft=false", edit)

    def test_a_view_that_failed_is_said_when_what_follows_it_fails(self):
        # A view that fails for any reason but absence (a 502, a rate limit)
        # sends publish to create, which then fails on the release that is
        # there. Measured before: only create's error was printed, which
        # points at deleting the release by hand; the 502 was nowhere.
        os.environ.update({
            "STAND_IN_VIEW_SAYS": "HTTP 502: Server Error",
            "STAND_IN_FAIL": "create",
            "STAND_IN_FAIL_SAYS": "a release with the same tag name already "
                                  "exists: v0.2.0"})
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        said = " ".join(ran.err.split())
        self.assertIn("already exists", said)
        self.assertIn("gh release view exited 1: HTTP 502: Server Error",
                      said)

    def test_the_run_that_tested_it_is_named_when_the_workflow_says_which(
            self):
        os.environ.update({"GITHUB_SERVER_URL": "https://github.example",
                           "GITHUB_REPOSITORY": "example-owner/example-repo",
                           "GITHUB_RUN_ID": "4242"})
        self.assertEqual(self.run_("publish", self.TAG).code, 0)
        self.assertIn("https://github.example/example-owner/example-repo"
                      "/actions/runs/4242", self.asked()[1]["notes"])

    def test_no_run_is_named_when_any_of_the_three_is_missing(self):
        os.environ.update({"GITHUB_SERVER_URL": "https://github.example",
                           "GITHUB_REPOSITORY": "example-owner/example-repo"})
        self.assertEqual(self.run_("publish", self.TAG).code, 0)
        self.assertNotIn("actions/runs", self.asked()[1]["notes"])

    def test_the_archive_holds_exactly_the_tracked_files_under_its_prefix(
            self):
        self.assertEqual(self.run_("publish", self.TAG).code, 0)
        tracked = git(self.project, "ls-files").splitlines()
        with zipfile.ZipFile(self.zip) as archive:
            names = [name for name in archive.namelist()
                     if not name.endswith("/")]
            prefix = f"{NAME}-0.2.0/"
            self.assertEqual(sorted(names),
                             sorted(prefix + name for name in tracked))
            self.assertEqual(archive.read(prefix + "CHANGELOG.md"),
                             (self.project / "CHANGELOG.md").read_bytes())
        for absent in ("ignored.txt", "untracked.txt", "dist/notes.md"):
            self.assertNotIn(prefix + absent, names)

    def test_out_puts_the_archive_and_the_notes_elsewhere(self):
        out = self.base / "elsewhere"
        ran = self.run_("publish", self.TAG, "--out", str(out))
        self.assertEqual(ran.code, 0, ran.err)
        self.assertTrue((out / self.zip.name).is_file())
        self.assertTrue((out / "notes.md").is_file())
        self.assertFalse((self.project / "dist").exists())
        self.assertIn(str(out / "notes.md"), self.argvs()[1])

    def test_a_relative_out_is_taken_from_the_current_folder(self):
        # As build.py's paths and this tool's own --archive are. Measured
        # before: it was taken from the project folder, so a run from
        # elsewhere left an untracked folder in the project and said it had
        # written a path that was not there from where it was run.
        here = self.base / "elsewhere"
        here.mkdir()
        before = os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, before)
        ran = self.run_("publish", self.TAG, "--out", "made", "--dry-run")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertTrue((here / "made" / self.zip.name).is_file())
        self.assertFalse((self.project / "made").exists())
        self.assertIn(f"made/{self.zip.name}", ran.out)
        # The default is the project's dist/, wherever it is run from.
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertEqual(ran.code, 0, ran.err)
        self.assertTrue(self.zip.is_file())
        self.assertFalse((here / "dist").exists())

    def test_a_failed_gh_is_a_failed_publish(self):
        os.environ["STAND_IN_FAIL"] = "create"
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertIn("gh release create", " ".join(ran.err.split()))

    def test_a_failed_upload_or_edit_on_a_re_run_is_a_failed_publish(self):
        # Measured before: no test made either fail, so a loop that went on
        # past a failed upload, or said "Updated" after one, passed.
        for failing, asked in (("upload", 2), ("edit", 3)):
            with self.subTest(failing=failing):
                if self.log.exists():
                    self.log.unlink()
                os.environ.update({"STAND_IN_VIEW": "0",
                                   "STAND_IN_FAIL": failing,
                                   "STAND_IN_FAIL_SAYS": "HTTP 422"})
                ran = self.run_("publish", self.TAG)
                self.assertEqual(ran.code, 1, ran.out + ran.err)
                said = " ".join(ran.err.split())
                self.assertIn(f"gh release {failing} exited 1: HTTP 422",
                              said)
                self.assertIn("not made or not finished", said)
                self.assertNotIn("gh release view", said)
                self.assertNotIn("Updated", ran.out)
                self.assertEqual(len(self.argvs()), asked, self.argvs())

    def test_what_it_wrote_comes_before_an_error_in_one_log(self):
        # Measured before: in the workflow, stdout is a pipe, which Python
        # holds until it exits, and stderr is not, so the log of a failed
        # publish showed the error first and the archive it wrote after.
        os.environ["STAND_IN_FAIL"] = "create"
        script = (
            "import sys\n"
            f"sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'tools')!r}]\n"
            "import release\n"
            f"sys.exit(release.main(['publish', {self.TAG!r}], "
            f"root=release.Path({str(self.project)!r})))\n")
        done = subprocess.run(
            (sys.executable, "-c", script), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
            timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        log = done.stdout
        self.assertEqual(done.returncode, 1, log)
        self.assertLess(log.index("Wrote "), log.index("error: "), log)
        self.assertLess(log.index("SHA-256 "), log.index("error: "), log)

    def test_a_dry_run_from_elsewhere_says_where_gh_would_run(self):
        # Measured before: the gh lines' paths are the project's, and the
        # Wrote line's are the current folder's, and nothing said so.
        here = self.base / "elsewhere"
        here.mkdir()
        before = os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, before)
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertEqual(ran.code, 0, ran.err)
        flat = " ".join(ran.out.split())
        self.assertIn(f"publish would run, in {self.project}:", flat)
        self.assertIn(f"dist/{self.zip.name}", ran.out)
        # From the project folder, the folder is its own.
        os.chdir(self.project)
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertIn("publish would run:", " ".join(ran.out.split()))

    def test_no_gh_is_said_with_the_paths_from_where_it_was_run(self):
        # Measured before: the paths were gh's, from the project folder,
        # which from anywhere else name nothing that is there.
        bin_ = self.base / "only-git"
        bin_.mkdir()
        (bin_ / "git").symlink_to(shutil.which("git"))
        os.environ["PATH"] = str(bin_)
        here = self.base / "elsewhere"
        here.mkdir()
        before = os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, before)
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        flat = " ".join(ran.err.split())
        self.assertIn("gh cannot be run", flat)
        self.assertIn(str(self.zip), flat)
        self.assertIn(str(self.project / "dist" / "notes.md"), flat)

    def test_the_whole_release_from_prepare_to_publish(self):
        # From the changelog a maintainer writes to the release a club sees:
        # prepare, commit, tag, publish, with nothing edited between.
        git(self.project, "tag", "-d", self.TAG)
        self.write("CHANGELOG.md", RELEASED.format(example=example("0.0.9"))
                   .replace("## [Unreleased]\n\n", "## [Unreleased]\n\n"
                            "- A third change, made after 0.2.0.\n\n"))
        git(self.project, "commit", "-q", "-am", "a change for 0.2.1")
        self.assertEqual(self.run_("prepare", "0.2.1", "--date",
                                   "2026-10-09").code, 0)
        git(self.project, "commit", "-q", "-am", "0.2.1")
        git(self.project, "tag", "-a", "v0.2.1", "-m", f"{NAME} 0.2.1")
        ran = self.run_("publish", "v0.2.1")
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        self.assertEqual(self.argvs()[1][:3], ["release", "create", "v0.2.1"])
        notes = self.asked()[1]["notes"]
        self.assertTrue(notes.startswith("- A third change, made after "
                                         "0.2.0.\n"), notes)


class TestAPreReleaseIsPublishedAsOne(PublishCase):
    """A version with a pre-release part is marked a pre-release on GitHub.

    Without the flag, ``0.2.0-rc.1`` would be shown as the repository's latest
    release, and the README sends a club to that page for its download.
    Rejected: marking it by hand afterwards, in the minutes it is listed as
    latest.
    """

    VERSION = "0.2.0-rc.1"
    CHANGELOG = RELEASED.replace("[0.2.0]", "[0.2.0-rc.1]")
    TAG = "v0.2.0-rc.1"

    def test_it_is_created_as_a_pre_release(self):
        self.assertEqual(self.run_("publish", self.TAG).code, 0)
        self.assertEqual(self.argvs()[1], [
            "release", "create", self.TAG, f"dist/{self.zip.name}",
            "--verify-tag", "--title", self.title(),
            "--notes-file", "dist/notes.md", "--prerelease"])

    def test_a_re_run_keeps_it_one(self):
        os.environ["STAND_IN_VIEW"] = "0"
        self.assertEqual(self.run_("publish", self.TAG).code, 0)
        self.assertIn("--prerelease=true", self.argvs()[2])
        self.assertNotIn("--prerelease=false", self.argvs()[2])


class TestPublishRefusesWhatItCannotStandBehind(PublishCase):
    """``publish`` makes nothing when the release would not be what it says.

    A tag that is not at the commit checked out would publish notes read from
    one tree and an archive of another; a changelog edited since the tag would
    do the same from a clean checkout. A tag ``check`` refuses is refused here
    too, so a ``publish`` run by hand cannot skip the job that runs first in
    the workflow. Each is refused before any archive is written or any gh is
    run. Rejected: archiving HEAD rather than the tag, which hides the
    mismatch instead of reporting it.
    """

    def assertNothingPublished(self, ran: Ran, *words: str) -> None:
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(self.asked(), [])
        self.assertFalse((self.project / "dist").exists())
        for each in words:
            self.assertIn(each, " ".join(ran.err.split()))
        self.assertIn("Nothing was published.", ran.err)

    def test_a_tag_that_is_not_at_the_checkout(self):
        self.write("README.md", "# A change after the tag\n")
        git(self.project, "commit", "-q", "-am", "after the tag")
        self.assertNothingPublished(self.run_("publish", self.TAG),
                                    "is not the commit checked out")

    def test_a_run_by_hand_that_does_not_name_gh_s_repository(self):
        # Measured before: with no GH_REPO, gh chose the repository itself,
        # which in a fork's clone is the parent, while the tag was checked
        # on origin, so a hand run could replace the parent's archive and
        # notes. The workflow sets GH_REPO; a hand run must say it too.
        for given in (None, ""):
            with self.subTest(gh_repo=given):
                if given is None:
                    os.environ.pop("GH_REPO")
                else:
                    os.environ["GH_REPO"] = given
                self.assertNothingPublished(
                    self.run_("publish", self.TAG),
                    "GH_REPO is not set, so gh would choose the repository "
                    "to release to itself",
                    "set it to the repository's OWNER/REPO, as the release "
                    "workflow does.")

    def test_a_tag_that_does_not_exist(self):
        self.write("matchcentre/__init__.py", '__version__ = "0.3.0"\n')
        self.write("CHANGELOG.md", RELEASED.format(example="x")
                   .replace("[0.2.0]", "[0.3.0]"))
        git(self.project, "commit", "-q", "-am", "0.3.0, untagged")
        # Said as what it is: measured by mutation, with the missing tag
        # left unsaid, the refusal was a tag "not the commit checked out",
        # naming an empty commit, and the test, which asked only for the
        # tag's name, passed.
        ran = self.run_("publish", "v0.3.0")
        self.assertNothingPublished(
            ran, "There is no tag v0.3.0 in this repository.")
        self.assertNotIn("not the commit checked out", ran.err)

    def test_a_tag_check_refuses(self):
        self.write("CHANGELOG.md", RELEASED.format(example="x").replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- Not yet released.\n"))
        git(self.project, "commit", "-q", "-am", "an unreleased change")
        git(self.project, "tag", "-f", "-a", self.TAG, "-m", "moved")
        self.assertNothingPublished(self.run_("publish", self.TAG),
                                    "[Unreleased] section still holds")

    def test_a_changelog_that_describes_no_release_is_refused(self):
        # Measured by mutation: with this unsaid, publish wrote the archive
        # and stopped only at the notes, for want of a [0.2.0] section.
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n")
        git(self.project, "commit", "-q", "-am", "no release")
        git(self.project, "tag", "-f", "-a", self.TAG, "-m", "moved")
        ran = self.run_("publish", self.TAG)
        self.assertNothingPublished(ran)
        self.assertEqual(" ".join(ran.err.split()),
                         "error: CHANGELOG.md describes no release at all. "
                         "Nothing was published.")

    def test_a_changelog_changed_since_the_tag(self):
        self.write("CHANGELOG.md", self.read("CHANGELOG.md").replace(
            "the single command", "the one command"))
        self.assertNothingPublished(self.run_("publish", self.TAG),
                                    "CHANGELOG.md")


class TestPublishRefusesATagMovedSinceTheRunBegan(PublishCase):
    """A run publishes its tag only while origin's tag is the commit it has.

    Measured before: actions/checkout makes the runner's tag the commit the
    run began at, so the guard that the tag is the commit checked out could
    never fail there. A tag deleted and pushed again while its first run was
    going, or a re-run of that first run, published the old commit's archive
    and notes under the tag that now names another, and went green. Now
    publish asks origin, when there is one, which commit the tag is, and
    refuses when it is not this one: the run of the tag as it is now
    publishes it. Rejected: a ``git fetch --force`` of the tag in the
    workflow before publish, which no test here can run, and which leaves the
    tool run by hand as blind as before.
    """

    def setUp(self):
        super().setUp()
        self.origin = self.base / "origin.git"
        git(self.base, "init", "-q", "--bare", str(self.origin))
        git(self.project, "remote", "add", "origin", str(self.origin))
        git(self.project, "push", "-q", "origin", "main", self.TAG)

    def move_on_origin(self, *, annotated: bool = True) -> str:
        """The tag moved on origin, from another clone, to a commit after."""
        other = Path(tempfile.mkdtemp(dir=self.base)) / "clone"
        git(self.base, "clone", "-q", str(self.origin), str(other))
        (other / "README.md").write_text("# Moved\n", encoding="utf-8")
        git(other, "commit", "-q", "-am", "after the tag")
        git(other, "tag", "-f", *(("-a", "-m", "moved") if annotated
                                   else ()), self.TAG)
        git(other, "push", "-q", "-f", "origin", self.TAG)
        return git(other, "rev-parse", "HEAD")

    def assertNothingPublished(self, ran: Ran, *words: str) -> None:
        self.assertEqual(ran.code, 1, ran.out + ran.err)
        self.assertEqual(self.asked(), [])
        self.assertFalse((self.project / "dist").exists())
        for each in words:
            self.assertIn(each, " ".join(ran.err.split()))
        self.assertIn("Nothing was published.", ran.err)

    def test_a_tag_origin_still_has_here_is_published(self):
        ran = self.run_("publish", self.TAG)
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        self.assertEqual(self.argvs()[1][:2], ["release", "create"])

    def in_a_run(self) -> None:
        """What follows runs as a workflow run's step does."""
        patcher = mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_tag_moved_on_origin_is_refused(self):
        self.in_a_run()
        for annotated in (True, False):
            with self.subTest(annotated=annotated):
                moved = self.move_on_origin(annotated=annotated)
                self.assertNothingPublished(
                    self.run_("publish", self.TAG),
                    f"{self.TAG} has moved to {moved[:12]}",
                    "the run of the tag as it is now publishes it")

    def test_a_tag_deleted_from_origin_is_refused(self):
        self.in_a_run()
        git(self.project, "push", "-q", "origin", f":refs/tags/{self.TAG}")
        self.assertNothingPublished(self.run_("publish", self.TAG),
                                    f"origin has no tag {self.TAG}",
                                    "deleted since this run began")

    def test_by_hand_a_tag_not_pushed_yet_is_refused_and_says_push_it(self):
        # Measured before: run by hand, as the README offers, a tag made
        # here and not pushed yet was said to be "deleted since this run
        # began", when nothing was deleted and there was no run.
        git(self.project, "push", "-q", "origin", f":refs/tags/{self.TAG}")
        ran = self.run_("publish", self.TAG)
        self.assertNothingPublished(
            ran, f"origin has no tag {self.TAG}: push it, with git push "
                 f"origin {self.TAG}, and the release workflow that starts "
                 f"publishes it.")
        self.assertNotIn("deleted", ran.err)
        self.assertNotIn("this run", ran.err)
        # The command is whole on its line, as it is pasted.
        self.assertTrue(any(f"git push origin {self.TAG}" in line
                            for line in ran.err.splitlines()), ran.err)

    def test_by_hand_a_tag_moved_here_is_refused_the_right_way_round(self):
        # Measured before: a tag moved here, to a fix, while origin's was
        # not, was said to have "moved to" origin's commit "since this run
        # began at" this one: the wrong way round, and no run.
        pushed = git(self.project, "rev-parse", "HEAD")
        self.write("README.md", "# The fix\n")
        git(self.project, "commit", "-q", "-am", "the fix")
        git(self.project, "tag", "-f", "-a", self.TAG, "-m", "moved here")
        here = git(self.project, "rev-parse", "HEAD")
        ran = self.run_("publish", self.TAG)
        self.assertNothingPublished(
            ran, f"origin's {self.TAG} is {pushed[:12]}, not {here[:12]}, "
                 f"the commit here:",
            f"git push -f origin {self.TAG}")
        self.assertNotIn("this run", ran.err)
        self.assertNotIn("has moved to", ran.err)
        # Each command is whole on its line, as it is pasted. Measured by
        # mutation: with the commands not kept whole, the first was parted
        # across two lines here, and every test passed, since each compares
        # the message with its line breaks folded.
        for command in (f"git push -f origin {self.TAG}",
                        f"git fetch -f origin tag {self.TAG}"):
            with self.subTest(command=command):
                self.assertTrue(any(command in line
                                    for line in ran.err.splitlines()),
                                ran.err)

    def test_an_origin_that_cannot_be_asked_is_refused(self):
        git(self.project, "remote", "set-url", "origin",
            str(self.base / "absent.git"))
        self.assertNothingPublished(self.run_("publish", self.TAG),
                                    "git ls-remote origin exited")

    def test_a_dry_run_does_not_ask_origin(self):
        # It is the preview before the tag is pushed, when origin has no
        # tag yet, or the one it is to replace.
        self.move_on_origin()
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertEqual(ran.code, 0, ran.out + ran.err)


# A stand-in git, put before the real one on PATH. It runs the real git for
# everything but `git archive`, which fails as git fails when it cannot write
# the archive: it says a fatal line and exits 128.
STAND_IN_GIT = '''\
#!{python}
import os, sys
if sys.argv[1:2] == ["archive"]:
    print("fatal: the stand-in git writes no archive", file=sys.stderr)
    sys.exit(128)
os.execv({git!r}, [{git!r}, *sys.argv[1:]])
'''


class TestWhenGitOrTheDiskFailsPublishSaysWhichAndStops(PublishCase):
    """``publish`` names what failed, git or the folder it writes to, and
    publishes nothing.

    Three refusals that no test held: measured by mutation, with each one
    gone, every test passed. Without the first, a PATH with no git on it was
    said to have no tag v0.2.0, which sends the maintainer looking for a tag
    that is there. Without the second, an ``--out`` under a file escaped as
    a traceback. Without the third, a ``git archive`` that failed was said
    to be a zip that cannot be read, with git's own reason lost. The
    failures are real ones: a PATH that holds only the stand-in gh, a folder
    asked for under a file, and a git that exits 128 on ``archive``.
    Rejected: a read-only folder for the last, which root, as a container's
    runner is, writes all the same, and whose refusal is in git's words,
    which change with its version and its language.
    """

    def assertRefused(self, ran: Ran, said: str) -> None:
        self.assertEqual((ran.code, ran.out), (1, ""), ran.err)
        self.assertEqual(" ".join(ran.err.split()),
                         f"error: {said} Nothing was published.")
        self.assertEqual(self.asked(), [])

    def test_a_git_that_cannot_be_run_is_not_taken_for_a_missing_tag(self):
        os.environ["PATH"] = str(self.base / "bin")
        self.assertRefused(self.run_("publish", self.TAG),
                           f"git cannot be run: git: "
                           f"{os.strerror(errno.ENOENT)}.")
        self.assertFalse((self.project / "dist").exists())

    def test_an_out_folder_that_cannot_be_made_is_named_with_why(self):
        blocker = self.base / "a-file"
        blocker.write_text("A file, where --out asks for a folder.\n",
                           encoding="utf-8")
        out = blocker / "out"
        self.assertRefused(self.run_("publish", self.TAG, "--out", str(out)),
                           f"{out.resolve()} cannot be made: "
                           f"{os.strerror(errno.ENOTDIR)}.")
        self.assertEqual(blocker.read_text(encoding="utf-8"),
                         "A file, where --out asks for a folder.\n")
        self.assertFalse((self.project / "dist").exists())

    def test_a_git_archive_that_fails_is_said_in_git_s_own_words(self):
        # The real git, found while the stand-in is not yet on PATH.
        real = shutil.which("git")
        stand_in = self.base / "bin" / "git"
        stand_in.write_text(STAND_IN_GIT.format(python=sys.executable,
                                                git=real), encoding="utf-8")
        stand_in.chmod(0o755)
        self.assertRefused(self.run_("publish", self.TAG),
                           "git archive exited 128: fatal: the stand-in git "
                           "writes no archive")
        self.assertEqual(list((self.project / "dist").iterdir()), [])


class TestADryRunRunsNoGh(PublishCase):
    """``publish --dry-run`` writes the archive and the notes, and runs no gh.

    The release is the one step that is public the moment it happens, and
    before this there was no way to see its notes or its archive first. Each
    gh command is printed as it would run, shell-quoted, one to a line.
    Rejected: a separate ``preview`` command, which would be a second path
    through the same steps that could drift from the one the workflow runs.
    """

    def test_nothing_is_asked_of_gh(self):
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        self.assertEqual(self.asked(), [])
        self.assertTrue(self.zip.is_file())
        self.assertTrue((self.project / "dist" / "notes.md").is_file())

    def test_it_says_which_repository_gh_would_release_to(self):
        # The gh lines name no repository, which GH_REPO gives them, so the
        # preview says it, or that a publish would refuse without it.
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertIn(f"in {REPOSITORY}, as GH_REPO says", " ".join(
            ran.out.split()))
        os.environ.pop("GH_REPO")
        ran = self.run_("publish", self.TAG, "--dry-run")
        self.assertEqual(ran.code, 0, ran.out + ran.err)
        self.assertIn("GH_REPO is not set, and publish runs none of these "
                      "until it names the repository to release to",
                      " ".join(ran.out.split()))

    def test_each_gh_command_is_printed_one_to_a_line_as_a_shell_reads_it(
            self):
        ran = self.run_("publish", self.TAG, "--dry-run")
        lines = ran.out.splitlines()
        title = self.title()
        for argv in (
                ["gh", "release", "view", self.TAG],
                ["gh", "release", "create", self.TAG,
                 f"dist/{self.zip.name}", "--verify-tag", "--title", title,
                 "--notes-file", "dist/notes.md"],
                ["gh", "release", "upload", self.TAG,
                 f"dist/{self.zip.name}", "--clobber"],
                ["gh", "release", "edit", self.TAG, "--title", title,
                 "--notes-file", "dist/notes.md", "--prerelease=false",
                 "--draft=false"]):
            with self.subTest(command=argv[2]):
                self.assertIn(shlex.join(argv), lines)
                self.assertEqual(shlex.split(shlex.join(argv)), argv)


class TestTheToolRunsGitAndGhOneWay(unittest.TestCase):
    """Every git and gh the tool runs goes through one function.

    So a stand-in gh on PATH is the gh every call reaches, and no call can be
    a shell line a tag's name could break out of. Rejected: ``os.system`` or a
    shell string per call, which is how a version with a ``+`` in it, or a
    path with a space, comes apart.
    """

    def test_there_is_one_call_and_it_is_a_list_with_no_shell(self):
        tree = ast.parse(RELEASE.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name)
                 and (node.func.value.id == "subprocess"
                      or node.func.value.id == "os" and node.func.attr
                      .startswith(("system", "popen", "spawn", "exec",
                                   "posix_spawn", "fork")))]
        self.assertEqual([(call.func.value.id, call.func.attr)
                          for call in calls], [("subprocess", "run")])
        self.assertNotIn("shell", [word.arg for word in calls[0].keywords])

    def test_every_subcommand_answers_version_as_every_command_does(self):
        for command in ("prepare", "check", "notes", "publish", None):
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as empty:
                    done = subprocess.run(
                        (sys.executable, str(RELEASE),
                         *((command,) if command else ()), "--version"),
                        cwd=empty, capture_output=True, text=True, timeout=120,
                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                    self.assertEqual(os.listdir(empty), [])
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                self.assertEqual(done.stdout,
                                 matchcentre.version_line() + "\n")


class TestWhatPublishWritesIsNeitherCommittedNorScanned(unittest.TestCase):
    """``dist/``, where ``publish`` writes, is ignored as ``site/`` is.

    Its ``notes.md`` is a changelog section, so it quotes whatever version
    that section quoted, and the suite's version scan, which leaves out the
    changelog by its name, would read the copy: a maintainer who ran
    ``publish --dry-run`` for 0.2.0 would fail the scan after the bump to
    0.2.1, as ``reports/`` once did. Rejected: writing it under the system's
    temporary folder, where a maintainer previewing a release cannot find it.
    """

    def test_git_ignores_it_and_every_scan_leaves_it_out(self):
        import test_foundation
        import test_project_complete as complete
        self.assertEqual(release.DEFAULT_OUT, "dist")
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/dist/", [line.strip() for line in lines])
        self.assertIn("dist", complete.NOT_SHIPPED)
        leaked = test_foundation.TestNoClubSpecificsLeaked
        self.assertIn("dist", leaked.SKIP_DIRS)


def jobs(text: str) -> dict[str, str]:
    """Each job of a workflow, by its name, as the text indented under it.

    Read as text, because the suite is standard-library only. A job is a key
    two spaces in under ``jobs:``, and its block is every line after it that
    is blank or indented deeper.
    """
    lines = text.splitlines()
    start = lines.index("jobs:")
    found: dict[str, list[str]] = {}
    name = None
    for line in lines[start + 1:]:
        match = re.match(r"^  ([\w-]+):\s*$", line)
        if match:
            name = match.group(1)
            found[name] = []
        elif line and not line.startswith(" "):
            break
        elif name:
            found[name].append(line)
    return {key: "\n".join(value) for key, value in found.items()}


def runs(block: str) -> list[str]:
    """Each ``run:`` line of a block, its command as written."""
    return [line.split("run:", 1)[1].strip() for line in block.splitlines()
            if re.match(r"^\s*(?:-\s+)?run:\s*\S", line)]


class TestAPushedVersionTagRunsTheReleaseWorkflow(unittest.TestCase):
    """``release.yml`` runs on a ``v*`` tag: check, the suite, then publish.

    Measured before (before.txt): no workflow named a tag, so a pushed tag ran
    nothing, and ``tests.yml`` could not be called by another workflow. The
    release is tested by tests.yml itself, called, not by a copy of its matrix,
    so the two cannot drift; and only the job that publishes may write.
    Rejected: adding ``tags:`` to tests.yml's own trigger, since one workflow
    cannot make a job wait on another workflow's run; and copying the matrix
    into release.yml.
    """

    def text(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_it_runs_on_a_pushed_tag_that_starts_with_v_and_on_nothing_else(
            self):
        text = self.text("release.yml")
        block = re.search(r"(?m)^on:\n((?:  .*\n|\n)+)", text).group(1)
        self.assertEqual([line for line in block.splitlines() if
                          line.strip() and not line.strip().startswith("#")],
                         ["  push:", '    tags: ["v*"]'])

    def test_check_runs_first_and_publish_last(self):
        found = jobs(self.text("release.yml"))
        self.assertEqual(sorted(found), ["check", "publish", "tests"])
        self.assertNotIn("needs:", found["check"])
        self.assertIn("    needs: check", found["tests"].splitlines())
        self.assertIn("    needs: [check, tests]",
                      found["publish"].splitlines())
        # A job that needs another still runs after it failed when its own
        # if: holds a status function, always() or !cancelled() or failure():
        # one line under publish would release a tag whose suite failed.
        for name in ("tests", "publish"):
            with self.subTest(job=name):
                self.assertNotRegex(
                    found[name],
                    r"(?m)^    if:.*\b(?:always|cancelled|failure)\s*\(")
        # continue-on-error lets a failed check, or a failed step of the
        # suite, through the needs: gate the same way: on a job or a step,
        # the job is then a success to the jobs that need it, so a wrong
        # tag ran the whole matrix, and a failing suite reached publish.
        # Measured before this: either, added to check or to tests.yml's
        # suite, left every test here passing.
        suite = jobs(self.text("tests.yml"))
        for workflow, each in (("release.yml", found), ("tests.yml", suite)):
            for name, block in each.items():
                with self.subTest(workflow=workflow, job=name):
                    self.assertNotRegex(
                        block, r"(?m)^\s*(?:-\s+)?continue-on-error\s*:")

    def test_check_and_publish_run_the_tool_on_the_tag_pushed(self):
        found = jobs(self.text("release.yml"))
        self.assertEqual(runs(found["check"]),
                         ['python tools/release.py check "$GITHUB_REF_NAME"'])
        self.assertEqual(
            runs(found["publish"]),
            ['python tools/release.py publish "$GITHUB_REF_NAME"'])
        # Each a line of its own, not a comment: measured before, the
        # substring passed with the line commented out, `# fetch-depth: 0`.
        self.assertRegex(found["publish"], r"(?m)^ +fetch-depth: 0[ \t]*$")
        for name in ("check", "publish"):
            with self.subTest(job=name):
                self.assertRegex(found[name],
                                 r'(?m)^ +python-version: "3\.12"[ \t]*$')

    def test_the_suite_is_tests_yml_called_and_tests_yml_can_be_called(self):
        found = jobs(self.text("release.yml"))
        self.assertIn("    uses: ./.github/workflows/tests.yml",
                      found["tests"].splitlines())
        self.assertEqual(runs(found["tests"]), [])
        tests = self.text("tests.yml")
        block = re.search(r"(?m)^on:\n((?:  .*\n|\n)+)", tests).group(1)
        self.assertIn("  workflow_call:", block.splitlines())

    def test_only_publish_may_write_and_it_is_given_gh_s_two_variables(self):
        text = self.text("release.yml")
        top = text[:text.index("\njobs:\n")]
        self.assertRegex(top, r"(?m)^permissions:\n  contents: read$")
        found = jobs(text)
        self.assertRegex(found["publish"],
                         r"(?m)^    permissions:\n      contents: write$")
        for name in ("check", "tests"):
            self.assertNotIn("write", found[name])
        self.assertIn("      GH_TOKEN: ${{ github.token }}", found["publish"])
        self.assertIn("      GH_REPO: ${{ github.repository }}",
                      found["publish"])
        self.assertEqual(re.findall(r"(?m)^ *[\w-]+: write *$", text),
                         ["      contents: write"])

    def test_one_release_of_a_tag_at_a_time_and_none_cancelled(self):
        text = self.text("release.yml")
        self.assertRegex(text, r"(?m)^concurrency:\n"
                               r"  group: release-\$\{\{ github\.ref \}\}\n"
                               r"  cancel-in-progress: false$")

    def test_its_header_says_what_it_does_and_that_nothing_needs_enabling(
            self):
        header = []
        for line in self.text("release.yml").splitlines():
            if not line.startswith("#"):
                break
            header.append(line.lstrip("# "))
        said = " ".join(" ".join(header).split())
        for words in ("tools/release.py", "tests.yml", "GITHUB_TOKEN",
                      "contents: write", "nothing to enable"):
            with self.subTest(words=words):
                self.assertIn(words, said)


class TestTheDocsSayHowAReleaseIsMadeNow(unittest.TestCase):
    """CONTRIBUTING and the README describe the release as it is now made.

    Before, CONTRIBUTING's *Making a release* ended at ``git tag`` and said
    nothing of notes, archives or GitHub, and the README's *Doing the upgrade*
    said "download the new release" without saying where. Rejected: an
    absolute GitHub address for the download, which is wrong in every fork
    (see ``.github/ISSUE_TEMPLATE/config.yml``).
    """

    def section(self, path: Path, heading: str) -> str:
        text = path.read_text(encoding="utf-8")
        start = text.index(heading)
        end = text.find("\n## ", start + len(heading))
        return text[start:end if end != -1 else None]

    def test_contributing_starts_with_prepare_and_ends_with_the_tag_push(self):
        steps = self.section(ROOT / "CONTRIBUTING.md", "## Making a release")
        order = ("tools/release.py prepare", "git tag -a",
                 "git push origin main", "git push origin v")
        at = [steps.find(each) for each in order]
        self.assertNotIn(-1, at, steps)
        self.assertEqual(at, sorted(at))
        for words in ("release.yml", "tools/release.py check",
                      "publish v", "--dry-run", "re-run", "delete the tag",
                      # A re-run runs the tagged commit again, so a cause in
                      # the commit needs the tag moved, not a re-run.
                      "same tagged commit",
                      # And a deleted tag leaves its release a draft.
                      "draft",
                      # A run of the old tag still going is cancelled first,
                      # and fails at publish if it is not.
                      "Cancel a run of the old tag",
                      "its `publish` fails rather than publishing the old",
                      # A final's pre-releases keep their sections.
                      "sections stay where they are"):
            with self.subTest(words=words):
                self.assertIn(words, " ".join(steps.split()))

    def test_the_workflow_says_what_a_re_run_mends_and_what_it_cannot(self):
        header = []
        for line in (WORKFLOWS / "release.yml").read_text(
                encoding="utf-8").splitlines():
            if not line.startswith("#"):
                break
            header.append(line.lstrip("# "))
        said = " ".join(" ".join(header).split())
        self.assertIn("same tagged commit", said)
        self.assertIn("draft", said)
        self.assertIn("A run whose tag has moved since it began", said)
        self.assertIn("git ls-remote", said)

    def test_the_upgrade_says_how_the_notes_close(self):
        # Every release the workflow makes ends its notes with the run that
        # tested it, after the SHA-256.
        upgrade = " ".join(self.section(ROOT / "README.md",
                                        "### Doing the upgrade").split())
        self.assertIn("the zip's SHA-256 and the run that tested it", upgrade)

    def test_the_upgrade_says_where_a_release_is_downloaded(self):
        upgrade = self.section(ROOT / "README.md", "### Doing the upgrade")
        self.assertIn("Releases", upgrade)
        self.assertIn(f"{NAME}-X.Y.Z.zip", upgrade)
        self.assertNotIn("github.com", upgrade)

    def test_the_readme_says_the_suite_runs_before_every_release(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        paragraph = next(block for block in text.split("\n\n")
                         if "workflows/tests.yml" in block)
        self.assertIn("(.github/workflows/release.yml)", paragraph)
        self.assertIn("release", " ".join(paragraph.split()).replace(
            "release.yml", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
