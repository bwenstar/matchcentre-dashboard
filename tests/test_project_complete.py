"""Whether the thing a club downloads is actually complete and actually generic.

Every other test file checks that the code is right. This one checks the
*deliverable*: that a fresh clone contains what the README says it contains, needs
nothing that is not in the standard library, carries no leftover of the project it
was rewritten from, and does not ship one club's data or one machine's notes.

These are the failures a test suite normally never catches, because they are not
failures of logic. A tool that works perfectly on the machine it was written on and
cannot run anywhere else has still failed.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# What a club downloads. Each entry is a promise made by the README or by another
# module that reads it, so a missing one is a broken promise rather than an
# untidy tree.
REQUIRED = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    ".gitignore",
    "build.py",
    "config.example.toml",
    "matchcentre/__init__.py",
    "matchcentre/pdf.py",
    "matchcentre/card.py",
    "matchcentre/scorecard.py",
    "matchcentre/format.py",
    "matchcentre/scoring.py",
    "matchcentre/slugs.py",
    "matchcentre/model.py",
    "matchcentre/competition.py",
    "matchcentre/config.py",
    "matchcentre/discover.py",
    "matchcentre/stats.py",
    "matchcentre/validate.py",
    "matchcentre/export.py",
    "matchcentre/site.py",
    "matchcentre/changes.py",
    "matchcentre/status.py",
    "matchcentre/careers.py",
    "matchcentre/scope.py",
    "matchcentre/sheet.py",
    "matchcentre/redact.py",
    "matchcentre/anonymise.py",
    "matchcentre/cardwriter.py",
    "matchcentre/cli.py",
    "matchcentre/web/index.html",
    "matchcentre/web/app.js",
    "matchcentre/web/styles.css",
    "matchcentre/web/picker.html",
    "matchcentre/web/careers.html",
    "tools/demo.py",
    "tools/doctor.py",
    "tools/screenshots.py",
    "tools/fetch.py",
    "tools/scheduled.py",
    "tools/release.py",
    # The suite ships too, and the README tells a club how to run it, so a copy
    # that arrives without it has arrived incomplete.
    "tests/jslint.py",
    "tests/csslint.py",
    "tests/test_foundation.py",
    "tests/test_parsing.py",
    "tests/test_pipeline.py",
    "tests/test_web_assets.py",
    "tests/test_cli.py",
    "tests/test_demo.py",
    "tests/test_doctor.py",
    "tests/test_hostile_input.py",
    "tests/test_fetch.py",
    "tests/test_project_complete.py",
    "tests/test_status.py",
    "tests/test_scheduled.py",
    "tests/test_careers.py",
    "tests/test_anonymise.py",
    "tests/test_release.py",
)

# Code that ships and that nothing promises, each with the reason it ships anyway.
# Empty: every such file is read by another module or named by the README. An
# entry here is a decision, so a new file cannot join it by being forgotten.
PROMISED_BY_NOTHING: dict[str, str] = {}

# Design notes are written, and deliberately not published: they discuss the
# predecessor's real data by name. `notes/` is gitignored; nothing that documents
# the internals belongs at the top level.
#
# Spelled as stems with `.md` added below, rather than as filenames. This file is
# one of the files scanned for references to those notes, so a literal
# "CONTRACT.md" written here would be a hit on itself, and the only way to make
# the test pass would be to stop scanning the test.
NOTE_STEMS = ("ARCHITECTURE", "CONTRACT", "PLAN", "SCHEMA", "API", "TEMPLATE",
              "VERIFICATION", "ROADMAP")
MUST_NOT_BE_TRACKED = tuple(f"{stem}.md" for stem in NOTE_STEMS)

# Text files a club receives, and therefore text files that may not point at
# anything a club does not receive. `.yml` is in the list for `.github/`: the
# workflows and the issue templates are read by anybody who forks this, and an
# issue form that told a reporter to consult a design note they do not have would
# be the most annoying dead reference of the lot.
SHIPPED_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".toml", ".txt", ".yml"}

# Folders under the project that a club does not receive, for every scan of the
# whole tree to leave out: the private notes, git's own, bytecode, the club's
# cards, the dashboard built from them, and what tools/scheduled.py keeps of each
# build. One set rather than one per scan, because a report is a `.txt` holding
# the version line and every player's name, and a scan that had not been told
# about `reports/` failed after the next version bump on a developer's machine.
# The same goes for the copy without names that README.md builds into `public/`,
# and the `reports-public/` its scheduled line keeps, and for `dist/`,
# where `tools/release.py publish` writes a release's archive and its notes:
# the notes are a changelog section, and quote whatever version it quoted.
NOT_SHIPPED = frozenset({"notes", ".git", "__pycache__", "site", "data",
                         "reports", "public", "reports-public", "dist"})

# The report line's version, read whole as semver writes it. Read as three
# numbers alone, `0.3.0-rc.1` was taken for `0.3.0`, so that a tree whose code
# said the pre-release passed with every example quoting the final, or the
# other way round. `tools/release.py prepare` rewrites with the same
# expression, which tests/test_release.py holds equal to this one, and runs
# the test below on what prepare wrote.
REPORT_LINE = re.compile(
    r"matchcentre-dashboard ([0-9]+\.[0-9]+\.[0-9]+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)")

# Every program in the project that a person types the name of. Each has an
# argument parser of its own, which is the reason the tests below ask every one
# of them the same question instead of asking `build.py` and assuming.
ENTRY_POINTS = ("build.py", "tools/doctor.py", "tools/demo.py",
                "tools/screenshots.py", "tools/fetch.py",
                "tools/scheduled.py", "tools/release.py")


def git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
                          text=True).stdout


def under_git() -> bool:
    """Whether git would have an opinion about the files here.

    Asked of git rather than by looking for ``ROOT/.git``, because the project
    can legitimately sit inside a larger checkout -- which is how it looks while
    it is being written, before it becomes its own repository. Testing for the
    directory skips exactly the case where the ignore rules matter most: a folder
    whose parent is already a repository, where one `git add .` in the wrong place
    commits a club's scorecards.
    """
    done = subprocess.run(("git", "rev-parse", "--is-inside-work-tree"),
                          cwd=ROOT, capture_output=True, text=True)
    return done.returncode == 0 and done.stdout.strip() == "true"


def version_scan(root: Path) -> list[Path]:
    """The files the version test below reads: every shipped text file under
    *root* but the changelog, as git keeps them.

    In a git work tree, the files git tracks and the ones it would add, so that
    a file a .gitignore leaves out, such as the config.proposed.toml that
    `build.py --discover` writes with the version that wrote it, is not taken
    for an example nobody updated; elsewhere, as in an unzipped release, every
    file. ``tools/release.py prepare`` rewrites exactly these, and
    tests/test_release.py holds the two lists equal. *root* is read when this
    is called, so that test can run the version test on a project of its own.
    """
    try:
        done = subprocess.run(("git", "ls-files", "-z", "--cached", "--others",
                               "--exclude-standard"), cwd=root,
                              capture_output=True)
    except OSError:                     # no git, which a club may not have
        done = None
    if done is not None and done.returncode == 0:
        paths = [root / name for name in os.fsdecode(done.stdout).split("\0")
                 if name]
    else:
        paths = list(root.rglob("*"))
    return sorted(path for path in paths
                  if path.suffix in SHIPPED_SUFFIXES
                  # The changelog is a history of every version.
                  and path.name != "CHANGELOG.md"
                  and not set(path.relative_to(root).parts) & NOT_SHIPPED
                  and path.is_file())


class TestEverythingPromisedIsPresent(unittest.TestCase):

    def test_every_required_file_exists(self):
        missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
        self.assertEqual(missing, [], f"{len(missing)} promised file(s) absent: "
                                      f"{missing}")

    def test_no_required_file_is_empty(self):
        empty = [name for name in REQUIRED
                 if (ROOT / name).is_file() and (ROOT / name).stat().st_size == 0]
        self.assertEqual(empty, [])

    def test_the_package_is_a_real_package(self):
        # Not a namespace package by accident. Without __init__.py the imports
        # work here and break the moment the folder is nested or installed.
        self.assertTrue((ROOT / "matchcentre" / "__init__.py").is_file())


class TestEveryFileOfCodeThatShipsIsOnTheList(unittest.TestCase):
    """REQUIRED was kept by hand, and five shipped files had never been added.

    Measured before, by /tmp/t4/review-work/docs/required_diff.py:
    matchcentre/anonymise.py, redact.py and sheet.py, tests/csslint.py and
    tests/test_anonymise.py. A copy without the last two passed every test above
    (/tmp/t4/scratch/f5/p5-before), and the whole file caught only the missing
    test_anonymise.py, through the README's table of tests. Adding the five alone
    was rejected: the list goes stale with the next module, so the tree is read
    against it now.

    Every page is listed, so on the real tree the scan could leave out ``.html``, or
    any suffix, and pass: /tmp/mutate146.py's first run had it survive. The scan is
    run on a planted tree too, one stray file of each suffix. Rejected: a copy of
    the suffixes in the test, which pins their spelling rather than what is read.
    """

    # One of each kind of file a club downloads, where none is on the list; one that
    # is; one where nothing ships; and one of a kind the scan does not read.
    PLANTED = ("build.py", "notes/draft.py", "stray.py", "stray.txt", "tools/stray.js",
               "tools/stray.css", "tools/stray.html")

    def test_every_one_is_required_or_exempt_for_a_reason(self):
        unlisted = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".js", ".css", ".html"):
                continue
            relative = path.relative_to(ROOT)
            if set(relative.parts) & NOT_SHIPPED:
                continue
            if relative.as_posix() not in (*REQUIRED, *PROMISED_BY_NOTHING):
                unlisted.append(relative.as_posix())
        self.assertEqual(unlisted, [], f"{len(unlisted)} shipped file(s) that "
                                       f"nothing promises: {unlisted}")

    def test_a_stray_file_of_each_kind_is_named(self):
        with tempfile.TemporaryDirectory(prefix="test-planted-") as holder:
            root = Path(holder)
            for name in self.PLANTED:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("", encoding="utf-8")
            with mock.patch.dict(globals(), ROOT=root):
                with self.assertRaises(self.failureException) as caught:
                    self.test_every_one_is_required_or_exempt_for_a_reason()
        self.assertIn("4 shipped file(s) that nothing promises: ['stray.py', "
                      "'tools/stray.css', 'tools/stray.html', 'tools/stray.js']",
                      str(caught.exception))


def _imported_names(tree: ast.AST) -> dict[str, int]:
    """Every name an import binds in this module, and the line that binds it.

    ``import a.b`` binds ``a``; ``import a.b as c`` binds ``c``. ``from __future__``
    binds nothing a reader can use, and ``import *`` binds names this cannot see, so
    both are left out rather than guessed at.
    """
    bound: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name != "*":
                    bound[alias.asname or alias.name] = node.lineno
    return bound


def _names_used(tree: ast.AST) -> set[str]:
    """Every bare name the module reads, anywhere, including in annotations.

    ``a.b()`` reads ``a``, so an attribute call counts its own module. Annotations
    are AST whether or not they are evaluated, so a name used only as a type still
    counts -- and a name used only inside a quoted annotation does not, which is
    the one false alarm this can raise and has not.
    """
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _exported_names(tree: ast.AST) -> set[str]:
    """Whatever ``__all__`` lists, which a package re-exports without using."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__"
                   for t in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            out |= {e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return out


def python_sources() -> list[Path]:
    """Every Python file this project ships, package and scripts alike."""
    found = sorted((ROOT / "matchcentre").rglob("*.py"))
    found += sorted((ROOT / "tools").glob("*.py")) if (ROOT / "tools").is_dir() else []
    found += [ROOT / "build.py"]
    return [p for p in found if p.is_file() and "__pycache__" not in p.parts]


class TestItRunsOnAnyMachine(unittest.TestCase):

    def sources(self) -> list[Path]:
        return python_sources()

    def test_nothing_imports_anything_that_is_not_in_the_standard_library(self):
        # The whole promise is "no pip install". A club has an airgapped laptop or
        # a locked-down work machine; one third-party import makes the tool
        # unusable there, and the failure arrives as a traceback on first run.
        local = {p.stem for p in (ROOT / "matchcentre").glob("*.py")}
        local |= {"matchcentre", "jslint", "build"}   # build.py, which tools/ may import
        # A tool may import a sibling tool by name -- `tools/fetch.py` takes the
        # next command to run from `demo` -- which is still not a third-party import.
        local |= {p.stem for p in (ROOT / "tools").glob("*.py")}
        outside = []
        for path in self.sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:          # relative: within this package
                        continue
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    if name and name not in sys.stdlib_module_names \
                            and name not in local:
                        outside.append(f"{path.relative_to(ROOT)}:{node.lineno}: "
                                       f"{name}")
        self.assertEqual(outside, [], f"third-party import(s) break the "
                                      f"no-install promise: {outside}")

    def test_there_is_no_dependency_manifest_to_mislead_anyone(self):
        for name in ("requirements.txt", "Pipfile", "poetry.lock",
                     "environment.yml"):
            with self.subTest(manifest=name):
                self.assertFalse((ROOT / name).exists(),
                                 f"{name} implies an install step that does not "
                                 f"exist")

    def test_the_two_python_version_gates_agree(self):
        # build.py must check the version *before* importing the package, so it
        # cannot import the number from the package; the two literals are the
        # deliberate cost of a readable error message. This keeps them equal.
        import matchcentre
        source = (ROOT / "build.py").read_text(encoding="utf-8")
        found = re.search(r"MINIMUM_PYTHON\s*=\s*\((\d+),\s*(\d+)\)", source)
        self.assertIsNotNone(found, "build.py no longer declares MINIMUM_PYTHON, "
                                    "so nothing checks the version before the "
                                    "import that needs it")
        self.assertEqual((int(found.group(1)), int(found.group(2))),
                         tuple(matchcentre.REQUIRES_PYTHON))

    def test_the_readme_states_the_python_version_the_code_needs(self):
        # tomllib is 3.11+. A club on 3.9 gets ImportError on line one of
        # config.py, which is a bad first impression and an easy thing to say.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(readme, r"3\.1[1-9]")

    def test_no_module_keeps_an_import_it_stopped_using(self):
        # There is no linter in this project -- no pip, so no flake8 -- and an
        # import nothing uses is the cheapest kind of lie a file can tell. It says
        # this module still does that, and a reader who believes it looks for a call
        # that is not there. Found by exactly this test in validate.py, which
        # imported `with_byes` for a check that had moved into a docstring.
        #
        # A name mentioned only in prose counts as unused, which is the point: the
        # docstring can name the function without the module importing it.
        stale = []
        for path in python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            bound = _imported_names(tree)
            used = _names_used(tree) | _exported_names(tree)
            for name, line in sorted(bound.items(), key=lambda kv: kv[1]):
                if name not in used:
                    stale.append(f"{path.relative_to(ROOT)}:{line}: {name}")
        self.assertEqual(stale, [], f"{len(stale)} import(s) nothing uses: {stale}")

    def test_no_source_file_uses_a_syntax_older_pythons_reject(self):
        # Parsed with this interpreter's own grammar; the point is that every file
        # is at least syntactically loadable, so a typo cannot hide in a module
        # that nothing imports during the tests.
        for path in self.sources():
            with self.subTest(file=str(path.relative_to(ROOT))):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestAFreshCheckoutIsEmptyOfOnesOwnData(unittest.TestCase):

    def test_the_data_directory_exists_for_a_club_to_fill(self):
        self.assertTrue((ROOT / "data").is_dir(),
                        "data/ must exist so the README can say 'put your PDFs "
                        "here'")

    def test_the_data_directory_ships_with_no_scorecards_in_it(self):
        # The club's own scorecards go here. Shipping a directory with someone
        # else's PDFs in it would publish real players' names and produce a
        # dashboard of a competition the downloader has nothing to do with. A
        # `.gitkeep` and a note saying what goes here are the only exceptions.
        #
        # Asked of git, not of the working tree. By the time anybody runs this
        # suite there are usually cards in data/ -- the README sends them through
        # `tools/demo.py` before it mentions the tests at all -- and an earlier
        # version of this test walked the directory and failed on them, which
        # meant the documented order of the documented commands produced a red
        # suite and nothing wrong. What was ever at stake is whether a card gets
        # *published*, and that is a question about what is committed.
        #
        # `git ls-files` and not the ignore rules: a card force-added with
        # `git add -f` is ignored and tracked at the same time, and tracked is the
        # half that publishes it.
        if not under_git():
            self.skipTest("not inside a git checkout, so there is no way to tell "
                          "a shipped file from one a club added")
        allowed = {".gitkeep", "README.md"}
        tracked = sorted(name for name in git("ls-files", "data").splitlines()
                         if name and Path(name).name not in allowed)
        self.assertEqual(tracked, [], f"data/ has {len(tracked)} committed "
                                      f"file(s), which a fork would publish: "
                                      f"{tracked}")

    def test_git_ignores_whatever_a_club_puts_in_data(self):
        # `tools/demo.py` writes a fictional season into data/ by default, and a
        # club's own cards go in beside it. Neither belongs in a commit -- real
        # cards name real players, and a fork would publish them.
        if not under_git():
            self.skipTest("not inside a git checkout")
        probe = "data/round-01/some-club-v-another.pdf"
        result = subprocess.run(("git", "check-ignore", "-q", probe),
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0,
                         f"{probe} is not gitignored, so a club that runs "
                         f"tools/demo.py or drops its own cards in would commit "
                         f"them on the next `git add .`")

    def test_the_note_in_data_is_still_committable(self):
        # The ignore rule above must not swallow the note that explains what the
        # folder is for -- a fresh clone with an empty, unexplained data/ is the
        # thing it exists to prevent.
        if not under_git():
            self.skipTest("not inside a git checkout")
        for keeper in ("data/.gitkeep", "data/README.md"):
            with self.subTest(path=keeper):
                result = subprocess.run(("git", "check-ignore", "-q", keeper),
                                        cwd=ROOT, capture_output=True)
                self.assertNotEqual(result.returncode, 0,
                                    f"{keeper} is gitignored, so a fresh clone "
                                    f"would have no data/ at all")

    def test_git_ignores_the_config_a_run_writes_but_not_the_example(self):
        # config.toml is written by the first run and then edited by hand, and what
        # a club edits into it is [captains], [player_names] and [aliases] -- three
        # tables of real people's names. Same exposure as data/, same rule, same
        # one-line opt-out. config.example.toml is the opposite: every name in it is
        # invented, and it is the only documentation of the settings, so it ships.
        if not under_git():
            self.skipTest("not inside a git checkout")
        for path, ignored in (("config.toml", True), ("config.example.toml", False)):
            with self.subTest(path=path):
                result = subprocess.run(("git", "check-ignore", "-q", path),
                                        cwd=ROOT, capture_output=True)
                if ignored:
                    self.assertEqual(result.returncode, 0,
                                     f"{path} is not gitignored, so a club would "
                                     f"publish its members' names on the next "
                                     f"`git add .`")
                else:
                    self.assertNotEqual(result.returncode, 0,
                                        f"{path} is gitignored, so a fresh clone "
                                        f"would document no settings at all")

    def test_no_config_of_anybody_elses_ships_with_the_project(self):
        # A config.toml left in the tree is one machine's state, and it would
        # arrive in a club's copy naming somebody else's club, teams and captains.
        # The README's whole opening is that the first run writes this file.
        self.assertFalse((ROOT / "config.toml").exists(),
                         "config.toml is in the project tree; it is a build "
                         "artefact and the deliverable must not carry one")

    def test_no_pdf_is_tracked_anywhere_in_the_repository(self):
        tracked = [line for line in git("ls-files").splitlines()
                   if line.lower().endswith(".pdf")]
        self.assertEqual(tracked, [])

    def test_the_generated_site_is_not_tracked(self):
        tracked = [line for line in git("ls-files").splitlines()
                   if line.startswith("site/")]
        self.assertEqual(tracked, [], "the built dashboard is output, not source")


class TestTheReportsFolderIsNeitherShippedNorScanned(unittest.TestCase):
    """``reports/`` is what ``tools/scheduled.py`` keeps, and no club receives it.

    Every report holds the whole of a build's output: the version line, and the
    name of every player on every card. Measured before: the three scans of the
    whole tree here each left out their own hand-kept set of folders, and none of
    the three named ``reports/``, so a developer who had run the schedule once had
    a ``.txt`` (a shipped suffix) that failed the version scan after the next bump
    and the ``--redact`` scan besides. Nor did ``.gitignore`` name it, so one
    ``git add .`` published a season's worth of names.

    A scan is a function that walks ``ROOT``, or walks the root it is given, as
    ``version_scan`` does so that tests/test_release.py can run the version
    test on a project of its own. Read as only the first, moving the version
    test's walk into that function took the scan out of this test's sight, and
    a list of folders kept there would have passed unseen.
    """

    SCANS = ("test_nothing_published_points_a_reader_at_an_unpublished_file",
             "test_every_document_that_asks_for_the_flag_names_the_policy",
             "version_scan",
             "test_every_one_is_required_or_exempt_for_a_reason")

    def test_every_scan_of_the_tree_leaves_it_out(self):
        self.assertIn("reports", NOT_SHIPPED)
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        scans = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # ROOT, or a root the function is handed.
            roots = {"ROOT"} | {arg.arg for arg in node.args.args}
            walks = any(isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "rglob"
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id in roots
                        for call in ast.walk(node))
            if walks:
                scans[node.name] = node
        self.assertEqual(sorted(scans), sorted(self.SCANS),
                         "the scans of the whole tree are not the ones this test "
                         "was written for")
        for name, node in scans.items():
            with self.subTest(scan=name):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                self.assertIn("NOT_SHIPPED", names,
                              f"{name} keeps a list of its own of what to leave out")
                literals = [n for n in ast.walk(node) if isinstance(n, ast.Set)
                            and any(isinstance(e, ast.Constant) and e.value == "site"
                                    for e in n.elts)]
                self.assertEqual(literals, [],
                                 f"{name} still spells out the folders it skips")

    def test_the_name_scan_leaves_it_out(self):
        tree = ast.parse((ROOT / "tests" / "test_foundation.py")
                         .read_text(encoding="utf-8"))
        found = [ast.literal_eval(node.value) for node in ast.walk(tree)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "SKIP_DIRS"
                         for target in node.targets)]
        self.assertEqual(len(found), 1, "test_foundation.py has no SKIP_DIRS")
        self.assertIn("reports", found[0])

    def test_git_ignores_it_and_nothing_in_it_is_committed(self):
        if not under_git():
            # Outside a checkout git has no opinion to ask, but the file that gives
            # it one ships (REQUIRED), so the rule is read off the file instead of
            # the test being skipped wherever it has never been run.
            lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn("/reports/", [line.strip() for line in lines])
            return
        for path in ("reports/2026-09-24_040002.txt", "reports/last-run.json"):
            with self.subTest(path=path):
                result = subprocess.run(("git", "check-ignore", "-q", path),
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 f"{path} is not gitignored, and a report names "
                                 f"every player")
        self.assertEqual(git("ls-files", "reports").split(), [])


class TestTheCopyWithoutNamesIsNeitherCommittedNorScanned(unittest.TestCase):
    """What the README's copy without names writes is ignored, as ``site/`` is.

    The README builds that copy into ``public/``, and schedules it with a
    ``public.toml`` and reports of its own in ``reports-public/``. Measured before,
    on a project that followed the recipe (/tmp/t4/scratch/g2/d-measure.txt):
    ``.gitignore`` named none of the three, and ``git add -n .`` added all five of
    their files. ``public.toml``, a copy of ``config.toml``, held the six captains
    the first run read, behind a #, and the kept report named a player twice. The
    tree's scans read the copy too: the scan for code nothing promises failed on its
    8 files, and the version scan on the kept report once its version line was an
    old one. Now ``.gitignore`` names all three, and ``NOT_SHIPPED`` and
    ``SKIP_DIRS`` the two folders.

    Rejected: ``/public/`` alone. The copy holds no name, and the two things beside
    it that the recipe makes are the ones that do.
    """

    # The README's commands that build into a folder of the project, and every
    # path of the project they name.
    COMMAND = re.compile(r"python3 (?:build\.py|tools/scheduled\.py) .*--out (?![/~])")
    PATH = re.compile(r"--(out|reports|config) ([^\s/~]\S*)")

    def written(self) -> dict[str, str]:
        found = {}
        for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
            if self.COMMAND.match(line):
                for flag, path in self.PATH.findall(line):
                    found[path] = "file" if flag == "config" else "folder"
        return found

    def test_the_readme_writes_three_things_into_the_project(self):
        self.assertEqual(self.written(), {"public": "folder", "public.toml": "file",
                                          "reports-public": "folder"})

    def test_git_ignores_all_of_it(self):
        if not under_git():
            # Read off the file, as for reports/, where git has no opinion to ask.
            lines = [line.strip() for line in
                     (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
            for path, kind in self.written().items():
                with self.subTest(path=path):
                    self.assertIn(f"/{path}/" if kind == "folder" else f"/{path}",
                                  lines)
            return
        for path, kind in self.written().items():
            probe = f"{path}/last-run.json" if kind == "folder" else path
            with self.subTest(path=probe):
                result = subprocess.run(("git", "check-ignore", "-q", probe),
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 f"{probe} is not gitignored")
            self.assertEqual(git("ls-files", path).split(), [])

    def test_no_scan_of_the_tree_reads_it(self):
        tree = ast.parse((ROOT / "tests" / "test_foundation.py")
                         .read_text(encoding="utf-8"))
        skipped, = [ast.literal_eval(node.value) for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "SKIP_DIRS"
                            for target in node.targets)]
        folders = {path for path, kind in self.written().items() if kind == "folder"}
        self.assertEqual({"NOT_SHIPPED": sorted(folders - NOT_SHIPPED),
                          "SKIP_DIRS": sorted(folders - skipped)},
                         {"NOT_SHIPPED": [], "SKIP_DIRS": []})


class TestGitIgnoresTheProposalDiscoverWritesBesideAConfig(unittest.TestCase):
    """The proposal ``--discover`` writes beside a config is ignored, as it is.

    The proposal is the file ``--discover --force`` would write, so it holds every
    captain, spelling and alias the config beside it holds, and the captains the
    (C) marks give besides; it is beside ``config.toml``, and beside
    ``public.toml`` for the README's copy without names. Measured before this,
    ``.gitignore`` had no line naming a proposal (/tmp/mcf/scratch/CONFIG/fixup/
    before.txt), so the first ``git add .`` after a ``--discover`` committed the
    members' names ``/config.toml`` is ignored for. Rejected: a line for each of
    ``/config.proposed.toml`` and ``/public.proposed.toml``, which misses a
    config named with ``--config`` anywhere else, ``conf/club.toml`` among them.
    """

    CONFIGS = ("config.toml", "public.toml", "conf/club.toml")

    @staticmethod
    def ignored_by_its_lines(path: str) -> bool:
        """Whether .gitignore's lines ignore the file *path*, read as git reads
        them: a pattern with no slash in it matches a file of that name in any
        folder, one with a slash the path from the root, and the last to match
        decides."""
        ignored = False
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.endswith("/"):
                continue
            pattern = line.lstrip("!")
            if "/" in pattern:
                matched = fnmatch.fnmatchcase(path, pattern.lstrip("/"))
            else:
                matched = fnmatch.fnmatchcase(Path(path).name, pattern)
            if matched:
                ignored = not line.startswith("!")
        return ignored

    def test_git_ignores_the_proposal_beside_each_config_a_club_names(self):
        from matchcentre import config
        proposals = [config.proposal_path(name).as_posix() for name in self.CONFIGS]
        self.assertEqual(proposals, ["config.proposed.toml", "public.proposed.toml",
                                     "conf/club.proposed.toml"])
        if not under_git():
            # Read off the file, as for the copy without names.
            for path in proposals:
                with self.subTest(path=path):
                    self.assertTrue(self.ignored_by_its_lines(path),
                                    f"{path} is not gitignored")
            self.assertFalse(self.ignored_by_its_lines("config.example.toml"))
            return
        for path in proposals:
            with self.subTest(path=path):
                result = subprocess.run(("git", "check-ignore", "-q", path),
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 f"{path} is not gitignored, so a club would "
                                 f"publish its members' names on the next "
                                 f"`git add .`")
        self.assertEqual(git("ls-files", f"*{config.PROPOSAL_SUFFIX}").split(), [])


class TestTheReadmeQuotesTheScheduleAsItRuns(unittest.TestCase):
    """What ``## Running it on a schedule`` shows is what the code says.

    Each quote is one a club compares with its own screen: the sentence on the
    front page, the table that explains it, the one line a scheduled run prints,
    and the line or the fields it pastes into a scheduler. Each is asked of the
    code rather than retyped here, so a reworded sentence is a failure here and not
    a README that describes last month's program.
    """

    @classmethod
    def setUpClass(cls):
        tools = str(ROOT / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import scheduled
        from matchcentre import site, status
        cls.scheduled, cls.site, cls.status = scheduled, site, status
        cls.lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()

    def section(self, heading: str) -> list[str]:
        """The lines under *heading*, up to the next heading at its depth or above."""
        start = self.lines.index(heading)
        depth = len(heading) - len(heading.lstrip("#"))
        out, fenced = [], False
        for line in self.lines[start + 1:]:
            fenced ^= line.startswith("```")
            level = len(line) - len(line.lstrip("#"))
            if not fenced and 0 < level <= depth and line[level:level + 1] == " ":
                break
            out.append(line)
        return out

    def fenced(self, heading: str) -> list[list[str]]:
        blocks, block = [], None
        for line in self.section(heading):
            if line.startswith("```"):
                if block is None:
                    block = []
                else:
                    blocks.append(block)
                    block = None
            elif block is not None:
                block.append(line)
        return blocks

    def test_the_front_page_sentence(self):
        quoted = " ".join(line[2:] for line in
                          self.section("### When a build fails, the front page says so")
                          if line.startswith("> "))
        self.assertEqual(quoted, self.status.failed("no-cards"))

    def test_the_troubleshooting_table_names_every_failure(self):
        rows = [line.split("|")[1].strip() for line in
                self.section("### The front page says the last build failed")
                if line.startswith("| ") and not line.startswith("| The page says")]
        elided = [template.replace("{n}", "…").replace("{minutes}", "… minutes")
                  for template in self.status.FAILURES.values()]
        self.assertEqual(rows, elided)

    def test_the_two_lines_a_run_prints(self):
        (built, skipped), = self.fenced("### Checking it is running")
        report = "/opt/matchcentre-dashboard/reports/2026-09-24_040002.txt"
        name = self.scheduled.REPORT_NAME.match(Path(report).name)
        self.assertIsNotNone(name, report)
        why = dict(self.scheduled.INPUT_REASONS)["data"]
        self.assertEqual(built, f"built because {why}: exit 0, report {report}")
        day, clock = name.group(1).split("_")
        when = self.site.pretty_date(
            f"{day}T{clock[:2]}:{clock[2:4]}:{clock[4:]}", with_time=True)
        self.assertEqual(skipped, f"nothing has changed since the build at {when}: "
                                  f"exit 0, report {report}")

    def test_the_cron_line(self):
        printed = self.scheduled.schedule_text(
            windows=False, python="/usr/bin/python3",
            script="/opt/matchcentre-dashboard/tools/scheduled.py", extra=[],
            mac_protected=False)
        cron = [line for line in printed.splitlines() if line.startswith("*/")]
        quoted = [line for block in self.fenced("### Setting it up") for line in block
                  if line.startswith("*/")]
        self.assertEqual(len(cron), 1, printed)
        self.assertEqual(quoted, cron)

    def test_the_task_scheduler_fields(self):
        printed = self.scheduled.schedule_text(
            windows=True,
            python=r"C:\Users\club\AppData\Local\Programs\Python\Python312\pythonw.exe",
            script=r"C:\Users\club\matchcentre-dashboard\tools\scheduled.py",
            extra=[], mac_protected=False)
        fields = ("  Program/script:", "  Add arguments:", "  Start in:")
        wanted = [line for line in printed.splitlines() if line.startswith(fields)]
        quoted = [line for block in self.fenced("### Setting it up") for line in block
                  if line.startswith(fields)]
        self.assertEqual(len(wanted), 3, printed)
        self.assertEqual(quoted, wanted)


def sentences(text: str) -> list[str]:
    """Every sentence of *text*, each paragraph's lines joined into one first."""
    paragraphs = (" ".join(chunk.split()) for chunk in re.split(r"\n\s*\n", text))
    return [sentence for paragraph in paragraphs
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph) if sentence]


class TestTheReadmeSchedulesOnlyWhatTheWrapperRuns(unittest.TestCase):
    """A scheduled run carries the flags ``tools/scheduled.py`` takes, and no other.

    Measured before: ``### One season at a time`` said "If a scheduled run carries
    `--only`, change it when the season changes" and "so a scheduled run can be
    changed the week before the first card arrives", and the docstrings of
    build.py's ``_known_seasons`` and ``_not_listed_section`` said the same, while
    the wrapper refuses ``--only`` and ``--players`` as unrecognized arguments
    (r83: a schedule's settings live in ``config.toml``, where the club and the
    doctor can read them). And ``## Publishing without names`` offered
    ``--players`` for a public copy with nothing to say that a schedule of that
    copy needs ``[build] players`` in the settings file it is given, so scheduling
    it published every name at exit 0. The flags are read from the wrapper's own
    ``--help``, so a flag it gains is one the prose may then name. Rejected:
    adding the two flags to the wrapper, a setting in a crontab line that the club
    cannot see in config.toml and an ``--only`` that goes stale with the season.
    """

    SCHEDULED = re.compile(r"\bscheduled (?:run|build|copy)s?\b|\bon a schedule\b")
    FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
    # The functions behind --only, whose docstrings say what it is for.
    ONLY = ("_known_seasons", "_only_scope", "_refuse_only_single",
            "_not_listed_section")

    @classmethod
    def setUpClass(cls):
        import build
        cls.docs = {f"build.{name}": getattr(build, name).__doc__ for name in cls.ONLY}
        cls.lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()

    def section(self, heading: str) -> str:
        return "\n".join(TestTheReadmeQuotesTheScheduleAsItRuns.section(self, heading))

    def test_every_flag_a_scheduled_run_is_said_to_carry_is_one_it_takes(self):
        takes = TestEveryFlagACommandAcceptsIsInTheListOfCommands.flags(
            self, "tools/scheduled.py")
        texts = {"README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
                 **self.docs}
        said = [(where, sentence) for where, text in texts.items()
                for sentence in sentences(text) if self.SCHEDULED.search(sentence)
                and not set(self.FLAG.findall(sentence)) <= takes]
        self.assertEqual(said, [])

    def test_the_season_filter_is_never_said_to_be_on_a_schedule(self):
        texts = {"### One season at a time": self.section("### One season at a time"),
                 **self.docs}
        said = [(where, sentence) for where, text in texts.items()
                for sentence in sentences(text) if self.SCHEDULED.search(sentence)]
        self.assertEqual(said, [])

    def test_a_copy_without_names_is_told_how_to_schedule_it(self):
        setting_up = self.section("### Setting it up")
        self.assertIn("`[build] players`", setting_up)
        self.assertIn("--config public.toml", setting_up)
        # Two files that nothing keeps alike. Measured before: "A change you make to
        # `config.toml` afterwards goes into `public.toml` too", which reads as if
        # something copied it; nothing does, and the copy goes on building with
        # the settings it was given.
        self.assertIn("Nothing copies a change you make to `config.toml` into "
                      "`public.toml`", " ".join(setting_up.split()))
        self.assertIn("[Setting it up](#setting-it-up)",
                      self.section("## Publishing without names"))


class TestTheLicenceSaysWhenThePublishedSiteHoldsNames(unittest.TestCase):
    """The Licence's warning about a published ``site/`` says when it does not apply.

    Measured before: "If you publish the built `site/` folder anywhere, remember
    that it contains those names too", with no exception, while ``[build]
    players`` and ``--players`` exist to make it untrue. Worded to promise no more
    than the build keeps: the pointer is to ``## Publishing without names``, which
    says what is left on the page and what the search cannot find. Rejected: "it
    contains no names" for a withheld build, when a competition's id is published
    as it was typed (4.58, left).
    """

    def test_the_sentence_names_both_settings_and_the_section(self):
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        licence = "\n".join(lines[lines.index("## Licence"):])
        sentence, = [sentence for sentence in sentences(licence)
                     if "contains those names" in sentence]
        for wanted in ("`[build] players`", "`--players`",
                       "(#publishing-without-names)"):
            self.assertIn(wanted, sentence)


class TestTheDesignNotesStayPrivate(unittest.TestCase):

    def test_no_design_document_sits_at_the_top_level(self):
        present = [name for name in MUST_NOT_BE_TRACKED
                   if (ROOT / name).exists()]
        self.assertEqual(present, [], f"internal design docs must live in notes/, "
                                      f"not at the top level: {present}")

    def test_git_ignores_the_notes_directory(self):
        # Checked by asking git rather than by reading .gitignore, because the
        # question is whether the file would actually be published.
        if not under_git():
            self.skipTest("not inside a git checkout")
        if not (ROOT / "notes").is_dir():
            self.skipTest("no notes/ directory")
        for path in sorted((ROOT / "notes").rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(note=relative):
                result = subprocess.run(("git", "check-ignore", "-q", relative),
                                        cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 0,
                                 f"{relative} is not gitignored and would be "
                                 f"published to GitHub")

    def test_no_note_is_tracked(self):
        tracked = [line for line in git("ls-files").splitlines()
                   if line.startswith("notes/")]
        self.assertEqual(tracked, [])

    def test_nothing_published_points_a_reader_at_an_unpublished_file(self):
        # A dangling reference is worse than no reference. Twenty of these existed
        # when this test was written, two of them in build.py's --help text -- so
        # `python3 build.py --help` sent a club off to read a design note that is
        # not in what they downloaded, and thirteen more cited a section number of
        # one. Docstrings and comments count: they ship to GitHub and get read.
        #
        # A path and a bare filename are both caught, because a citation like
        # "see <note> section 4" is exactly as dead as a path and reads as more
        # authoritative.
        dangling = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.suffix not in SHIPPED_SUFFIXES:
                continue
            if set(path.relative_to(ROOT).parts) & NOT_SHIPPED:
                continue
            source = path.read_text(encoding="utf-8")
            # A test may name a note it reads, as long as it copes with the note
            # being absent -- which is the state a club downloads it in. That is
            # a whole-file allowance rather than a per-line one on purpose: it is
            # the file's behaviour that makes the reference safe.
            if "tests" in path.relative_to(ROOT).parts and "skipTest" in source:
                continue
            for number, line in enumerate(source.splitlines(), start=1):
                for hit in self.unpublished_pattern().findall(line):
                    dangling.append(f"{path.relative_to(ROOT)}:{number}: {hit}")
        self.assertEqual(dangling, [], f"{len(dangling)} reference(s) to files "
                                       f"that are gitignored and will not be in "
                                       f"a club's download:\n  "
                                       + "\n  ".join(dangling))

    @staticmethod
    def unpublished_pattern() -> re.Pattern[str]:
        """Anything under ``notes/``, and each design note by bare filename.

        The bare names are built from stems for the reason given where they are
        declared. Whatever ``notes/`` actually holds is added too, so a note
        invented later is covered without anybody remembering to list it.
        """
        names = set(MUST_NOT_BE_TRACKED)
        if (ROOT / "notes").is_dir():
            names |= {p.name for p in (ROOT / "notes").rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts}
        alternatives = "|".join(sorted((re.escape(n) for n in names), key=len,
                                       reverse=True))
        return re.compile(r"notes/[A-Za-z0-9_-]+\.\w+|\b(?:" + alternatives
                          + r")\b")

    def test_no_python_bytecode_is_tracked(self):
        tracked = [line for line in git("ls-files").splitlines()
                   if line.endswith(".pyc") or "__pycache__" in line]
        self.assertEqual(tracked, [])


class TestTheReadmeSaysWhereTheDataComesFrom(unittest.TestCase):

    def readme(self) -> str:
        return (ROOT / "README.md").read_text(encoding="utf-8")

    def test_it_names_the_website_the_pdfs_come_from(self):
        # Without this the tool looks like it accepts any tennis PDF, and the
        # first thing a club tries is a scorecard from somewhere else.
        self.assertIn("matchcentre.tennis.com.au", self.readme())

    def test_it_explains_where_to_put_the_pdfs(self):
        readme = self.readme()
        self.assertIn("data/", readme)
        self.assertIn("build.py", readme)

    def test_it_mentions_the_licence_that_is_actually_shipped(self):
        licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT", licence)
        self.assertIn("MIT", self.readme())

    def test_every_test_file_it_names_is_actually_here(self):
        # The README lists the suite file by file and says what each one covers.
        # A renamed or deleted file leaves a club running a command that reports
        # fewer tests than the table promises, with nothing to say why.
        # Anywhere at all, not only in the table: the section also gives a
        # single-file command, and a command naming a file that is gone is the
        # more annoying of the two.
        named = sorted(set(re.findall(r"\b(test_[a-z0-9_]+\.py)\b", self.readme())))
        self.assertGreaterEqual(len(named), 6,
                                f"the README names {named}, which is too few to "
                                f"be the file-by-file table it claims")
        missing = [name for name in named
                   if not (ROOT / "tests" / name).is_file()]
        self.assertEqual(missing, [], f"the README names test file(s) that do not "
                                      f"exist: {missing}")

    def test_the_command_it_gives_for_running_them_is_the_one_that_works(self):
        # `discover -s tests -t tests` and not `-t .`: the test files import each
        # other as top-level modules, which needs tests/ to be the top-level
        # directory. With `-t .` discovery fails on the first import, and the
        # club's reasonable conclusion is that the tool is broken.
        readme = self.readme()
        self.assertIn("unittest discover -s tests -t tests", readme)
        self.assertFalse((ROOT / "tests" / "__init__.py").exists(),
                         "tests/ became a package, so `-t tests` no longer "
                         "imports test_pipeline the way the other files do")

    def test_every_link_it_makes_to_itself_lands_on_a_heading(self):
        # The README is long enough to need a table of contents and cross
        # references, and a dead one is silent: GitHub renders `](#nowhere)` as a
        # perfectly ordinary link that scrolls nowhere. One was written by hand
        # here and pointed at a section whose title had changed.
        #
        # Fences are tracked because a code block in this README contains lines
        # beginning with `#` -- the commented-out `teams = [...]` in the
        # config.toml sample -- and counting those as headings would let a link
        # to a comment pass.
        readme = self.readme()
        headings, fenced = set(), False
        for line in readme.splitlines():
            if line.startswith("```"):
                fenced = not fenced
            elif not fenced and line.startswith("#"):
                headings.add(self.anchor(line))
        dead = sorted({target for target in re.findall(r"\]\(#([^)]+)\)", readme)
                       if target not in headings})
        self.assertEqual(dead, [], f"the README links to {len(dead)} anchor(s) "
                                   f"that are not headings in it: {dead}\n"
                                   f"headings present: {sorted(headings)}")

    @staticmethod
    def anchor(heading: str) -> str:
        """A heading line, as GitHub turns it into a fragment id."""
        text = heading.lstrip("#").strip().lower()
        text = re.sub(r"[^\w\s-]", "", text)    # backticks, commas, full stops
        return re.sub(r"\s+", "-", text)

    def test_every_section_it_has_is_reachable_from_the_contents_list(self):
        # The other direction of the test above, and the one that goes wrong by
        # addition rather than by editing: a new section is written, the list three
        # screens up is not touched, and the section exists only for somebody who
        # scrolls the whole file. Nothing about it looks broken, which is why it
        # survives -- an unlisted section renders perfectly.
        #
        # `##` only. The 29 `###` headings under them are the steps inside a
        # section, and listing those would make the contents longer than the part
        # of the README that fits on a screen.
        readme = self.readme()
        listed = set(re.findall(r"\]\(#([^)]+)\)", readme))
        fenced, missing = False, []
        for line in readme.splitlines():
            if line.startswith("```"):
                fenced = not fenced
            elif not fenced and line.startswith("## "):
                if self.anchor(line) not in listed | {"contents"}:
                    missing.append(line.strip())
        self.assertEqual(missing, [], f"{len(missing)} README section(s) are in no "
                                      f"link anywhere in the file, so the contents "
                                      f"list is not a list of the sections: "
                                      f"{missing}")

    def test_what_it_says_about_ci_matches_what_ci_there_is(self):
        # Both directions, because both have been wrong here. This test used to
        # assert that `.github/` did not exist, on the strength of a decision that
        # automation would be local scripts only -- so the day the workflows were
        # added it failed, having caught nothing but the change it was written to
        # forbid. A rule about which answer is correct goes stale; a rule that the
        # README and the repository give the *same* answer does not.
        readme = self.readme().lower()
        mentions = [phrase for phrase in ("github actions", ".github/workflows",
                                          "workflow")
                    if phrase in readme]
        if self.workflows():
            self.assertTrue(mentions,
                            f"{len(self.workflows())} workflow(s) run on every "
                            f"push and the README does not mention them, so a "
                            f"contributor cannot know their branch is being "
                            f"tested for them")
        else:
            self.assertEqual(mentions, [], f"the README talks about CI and there "
                                           f"is no workflow file, so it sends a "
                                           f"club looking for one that is absent")

    def test_it_does_not_cite_the_predecessors_ci(self):
        # This project was rewritten out of a GitLab monorepo whose pipeline did
        # the releasing. None of that came with it, and a stray mention would send
        # a reader looking for a .gitlab-ci.yml that is not here and never was.
        self.assertNotIn("gitlab", self.readme().lower())

    def test_every_picture_it_shows_is_a_file_that_is_actually_here(self):
        # A README's images are the first thing anybody looks at and the only part
        # of it that fails visibly: GitHub renders a missing one as a broken-image
        # icon at full width. They are generated by `tools/screenshots.py` and
        # committed for exactly this reason -- there is nowhere else they could be
        # served from, since this project has no site of its own.
        embedded = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", self.readme(),
                              flags=re.DOTALL)
        self.assertTrue(embedded, "the README shows no pictures of the dashboard "
                                  "at all, which is the one thing a dashboard "
                                  "needs shown")
        missing = [target for target in embedded
                   if not target.startswith(("http://", "https://"))
                   and not (ROOT / target).is_file()]
        self.assertEqual(missing, [], f"the README embeds image(s) that are not in "
                                      f"the repository: {missing}")

    def test_every_workflow_the_readme_names_is_a_workflow_that_exists(self):
        # A status badge is a URL built out of a workflow's filename. Rename the
        # file and the badge does not break loudly: it renders as a grey "no
        # status", which reads as "the tests are not run" rather than as a typo.
        named = sorted(set(re.findall(r"workflows/([\w.-]+\.yml)", self.readme())))
        missing = [name for name in named
                   if not (ROOT / ".github" / "workflows" / name).is_file()]
        self.assertEqual(missing, [], f"the README points at workflow file(s) "
                                      f"that do not exist: {missing}")

    @staticmethod
    def workflows() -> list[str]:
        directory = ROOT / ".github" / "workflows"
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.iterdir()
                      if p.suffix in (".yml", ".yaml"))


class TestNoWorkflowRunsAnActionGitHubWarnedIsOnNode20(unittest.TestCase):
    """Both workflows run the versions of their two actions that moved to Node 24.

    The owner's first run of ``tests.yml`` on GitHub warned that Node.js 20 actions
    are deprecated, and named ``actions/checkout@v4`` and ``actions/setup-python@v5``.
    Measured before, by reading the ``uses:`` lines: both workflows ran both, at those
    versions (tests.yml:33 and 35, demo.yml:35 and 37), and nothing in the suite read a
    ``uses:`` line. This machine cannot fetch an action, so the versions held below
    come from the warning and from the actions' release notes as known here, not from
    a run. Every demo, build and ``--check`` command the two workflows run was run
    here, on Python 3.12, before and after the bump, and exits, prints and writes as it
    did, but for the time each build stamps; the suite step was counted, not run
    (/tmp/t7/scratch/CI/wf-before.txt, wf-after.txt, wf-compare.txt).

    Read from the ``uses:`` lines, not from the whole file: a string match for ``@v4``
    passes a step taken back to ``@v3``, and fails a comment that says which version
    was replaced. A floor, not a pin, so a later version passes, and a ref that names
    no version, a commit or a branch, is reported, because nothing here can say what
    it runs on. That refuses a commit with its version in a comment after it, the pin
    GitHub's hardening guide advises: the comment is a claim nothing here checks.
    ``upload-pages-artifact`` and ``deploy-pages`` are not held: the warning did not
    name them, and the run that warned was ``tests.yml``'s, which runs neither, so its
    silence says nothing about them.
    """

    # The first version of each that runs on Node 24, by its release notes as known.
    NODE_24 = {"actions/checkout": 5, "actions/setup-python": 6}

    # One of each way of writing a step's action: a step that is only its action,
    # one under the step's name, one quoted with a comment after it, a full version,
    # a later major of two digits, a commit, an owner and name in capitals, which
    # GitHub takes for the same action, and two lines that are not a step's action --
    # one commented out, and a command that prints the words. Not read: a step in
    # flow style, ``- {uses: ...}``, or with a space before its colon, which no
    # workflow here writes.
    PLANTED = (
        "      - uses: actions/checkout@v4\n"
        "      - name: python\n"
        "        uses: actions/setup-python@v5\n"
        "      - uses: 'actions/checkout@v4'  # quoted\n"
        '      - uses: "actions/setup-python@v6.0.0"\n'
        "      - uses: actions/checkout@v10\n"
        "      - uses: actions/setup-python@0123456789abcdef0123456789abcdef01234567\n"
        "      - uses: Actions/Checkout@v4\n"
        "      # - uses: actions/checkout@v3\n"
        "        run: echo uses: actions/checkout@v2\n")

    @staticmethod
    def uses(text: str) -> list[tuple[int, str, str]]:
        """Each step's action in *text*: its line, the action, and its ref."""
        found = []
        for number, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"\s*(?:-\s+)?uses:\s*['\"]?([\w.-]+/[\w.-]+)@([\w.-]+)",
                             line)
            if match:
                found.append((number, match.group(1).lower(), match.group(2)))
        return found

    @classmethod
    def below(cls, text: str) -> list[str]:
        """Each step of *text* that runs one of the two actions below its Node 24
        version, or at a ref that names no version."""
        stale = []
        for number, action, ref in cls.uses(text):
            if action in cls.NODE_24:
                major = re.fullmatch(r"v(\d+)(?:\.\d+)*", ref)
                if not major or int(major.group(1)) < cls.NODE_24[action]:
                    stale.append(f"{number}: {action}@{ref}")
        return stale

    def test_neither_action_is_run_below_the_version_that_moved_to_node_24(self):
        stale, seen = [], set()
        for name in TestTheReadmeSaysWhereTheDataComesFrom.workflows():
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            seen |= {action for _, action, _ in self.uses(text)}
            stale += [f"{name}:{step}" for step in self.below(text)]
        self.assertEqual(stale, [], f"{len(stale)} step(s) run an action below the "
                                    f"version that moved to Node 24, or at a ref that "
                                    f"names no version: {stale}. checkout runs on "
                                    f"Node 24 from v5, and setup-python from v6. A "
                                    f"commit or a branch names no version this test "
                                    f"can read, so a step pinned to a commit needs "
                                    f"the test taught to read one first")
        missing = sorted(set(self.NODE_24) - seen)
        self.assertEqual(missing, [], f"the scan found no step running {missing}: "
                                      f"either it read no workflow, or no workflow "
                                      f"runs it any more and it comes out of NODE_24")

    def test_the_scan_reads_each_planted_way_of_writing_a_step(self):
        self.assertEqual(self.below(self.PLANTED), [
            "1: actions/checkout@v4", "3: actions/setup-python@v5",
            "4: actions/checkout@v4",
            "7: actions/setup-python@0123456789abcdef0123456789abcdef01234567",
            "8: actions/checkout@v4"])


class TestALeakInTheRedactorHasSomewhereToGoThatIsNotPublic(unittest.TestCase):
    """The one bug that could not be reported where this project says to report bugs.

    ``--redact`` exists because an issue tracker is public forever, and the
    card-not-read form says so itself: a reporter who finds a real name in the
    redacted report is told it is "a bug in the redactor and a more urgent one than
    yours" -- and then handed the public tracker to say it in. Naming the line that
    leaked republishes the leak to the same audience it was published to; not naming
    it wastes the report. Measured before this was written: no shipped file
    contained the string ``SECURITY``, and the only two routes named anywhere were
    the two public issue forms.

    A policy nobody is sent to is the same as no policy, so what is pinned below is
    reachability and honesty rather than wording: that the flag the policy is about
    is named, that the route it offers is not an issue, that every file it cites is
    one a club actually downloaded, and that it names nobody's particular copy of
    the repository -- a club that forks this needs its own reporters reaching its
    own maintainer, not the original one, who cannot fix their copy and should not
    be reading their members' names.
    """

    POLICY = "SECURITY.md"

    def policy(self) -> str:
        return (ROOT / self.POLICY).read_text(encoding="utf-8")

    def test_it_says_which_promise_it_is_about(self):
        # A policy about "vulnerabilities" in the abstract would never tell a
        # reporter that a diagnostic printing a name counts as one, which is the
        # only reason this file exists rather than being boilerplate.
        policy = self.policy()
        self.assertIn("--redact", policy)
        self.assertIn("tools/doctor.py", policy)

    def test_the_route_it_offers_is_not_the_public_tracker(self):
        # "Report a vulnerability" is the label on GitHub's own button, quoted so
        # that a reporter looks for something that exists rather than for "the
        # security contact". That it is private is asserted separately: the button
        # is worth nothing to somebody who does not know the draft advisory behind
        # it is not published.
        #
        # Asked of the text with its line breaks collapsed, which is the form the
        # reporter reads: markdown joins a wrapped line, so a label split across two
        # source lines is still a label on the page, and rewrapping a paragraph is
        # not a defect worth a red suite.
        flat = " ".join(self.policy().split())
        self.assertIn("Report a vulnerability", flat)
        self.assertIn("privat", flat.lower())

    def test_it_names_nobodys_copy_of_the_repository(self):
        # The advisory form's URL has an owner and a repository name in it. Written
        # down here it would travel into every fork unchanged. The Security tab sits
        # in the same place in every fork, so the route is named by where it is.
        self.assertNotIn("github.com", self.policy())

    def test_it_promises_no_version_it_will_stop_shipping(self):
        # A supported-versions table is the convention and would be a lie here:
        # one branch, no backports, and the supported version is whatever the newest
        # release is. Written as a number it goes stale at the next bump and does so
        # silently -- the version test below catches the
        # `matchcentre-dashboard X.Y.Z` form and not a bare number in prose.
        stale = re.findall(r"\b\d+\.\d+\.\d+\b", self.policy())
        self.assertEqual(stale, [], f"the policy quotes version number(s) "
                                    f"{stale}, which nothing updates on a release")

    def test_it_says_what_to_send_instead_of_the_card(self):
        # A reporter who does as they are told has no card to attach, so the policy
        # has to say what a reproduction looks like without one. `cardwriter` writes
        # a PDF the reader reads exactly like a genuine export, so a leak can be
        # demonstrated on a card whose every name is invented -- and the script that
        # demonstrates it becomes the regression test.
        self.assertIn("cardwriter", self.policy())

    def test_every_file_it_points_at_is_in_the_download(self):
        dangling = [target for target in self.pointers()
                    if not (ROOT / target).exists()]
        self.assertEqual(dangling, [], f"the policy cites {len(dangling)} path(s) "
                                       f"that are not in a club's download: "
                                       f"{dangling}")

    def pointers(self) -> list[str]:
        """Repository paths the policy cites, as links and as code spans.

        Both forms, because the two fail the same way and only one of them is a
        link: a citation of a tool by path is what a reporter types, and a typo in
        it is a command that does nothing.
        """
        policy = self.policy()
        links = [target for target in re.findall(r"\]\(([^)]+)\)", policy)
                 if not target.startswith(("http://", "https://", "#"))]
        spans = re.findall(r"`([\w./-]+\.(?:py|md|toml|html|js|css))`", policy)
        return sorted(set(links + spans))

    def test_the_readme_section_that_asks_for_the_report_is_the_one_that_says_it(self):
        # In "Reporting a problem" itself, not merely somewhere in a file this long.
        # That section is where a club is standing when it runs the flag, and a
        # pointer three screens away is not a pointer.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("## Reporting a problem")
        section = readme[start:readme.index("\n## ", start + 1)]
        self.assertIn(self.POLICY, section,
                      "the README asks for a redacted report and does not say "
                      "where to go when the redaction is what failed")

    def test_every_document_that_asks_for_the_flag_names_the_policy(self):
        # Four files send somebody to `--redact`: the README, CONTRIBUTING, the
        # card-not-read form and the tool's own report. A reporter reads exactly one
        # of them, so the pointer belongs in all four rather than in the likeliest.
        #
        # `tests/` is excluded because a test runs the flag rather than recommends
        # it, and the changelog because it records the release the flag arrived in.
        asks, silent = [], []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.suffix not in SHIPPED_SUFFIXES:
                continue
            relative = path.relative_to(ROOT)
            if set(relative.parts) & (NOT_SHIPPED | {"tests"}):
                continue
            if relative.name in ("CHANGELOG.md", self.POLICY):
                continue
            text = path.read_text(encoding="utf-8")
            if "--redact" not in text:
                continue
            asks.append(relative.as_posix())
            if self.POLICY not in text:
                silent.append(relative.as_posix())
        self.assertEqual(silent, [], f"{len(silent)} file(s) ask for a redacted "
                                     f"report and name nowhere to go if the "
                                     f"redaction leaked: {silent}")
        self.assertGreaterEqual(len(asks), 4,
                                f"only {asks} ask for the flag, so this rule is "
                                f"passing on too small a set to mean anything")


class TestTheVersionNumberIsOneNumber(unittest.TestCase):
    """``__version__`` is quoted in four places a club reads, and one it edits.

    The report prints it, ``season.json`` records it, both pages' footers show it,
    and the issue forms show an example of the line it appears on. All of those are
    generated from the one constant except the last, which is prose -- so the one
    that can drift is the one that tells a reporter what to paste. A stale example
    there teaches somebody to quote a version they are not running.
    """

    def version(self) -> str:
        import matchcentre
        return matchcentre.__version__

    def test_it_looks_like_a_version(self):
        self.assertRegex(self.version(), r"^\d+\.\d+\.\d+([-.+].+)?$")

    def test_it_is_the_newest_version_the_changelog_describes(self):
        # The changelog is the only record of what changed between two builds, so
        # a release whose number is not in it is a build nobody can look up. Newest
        # first is the Keep a Changelog order the file's own header claims.
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        released = re.findall(r"(?m)^##\s*\[(\d+\.\d+\.\d+[^\]]*)\]", text)
        self.assertTrue(released, "the changelog lists no released version, so "
                                  "nothing says what this build contains")
        self.assertEqual(released[0], self.version(),
                         f"the code says {self.version()} and the newest "
                         f"changelog entry says {released[0]}")

    def test_every_example_of_the_report_line_quotes_the_version_in_the_code(self):
        # `matchcentre-dashboard 0.2.0-rc.1 (python 3.12.3 on linux)` is written out by
        # hand in the issue forms, because a form has nothing to interpolate. This
        # is what makes bumping the version notice them.
        stale = []
        for path in version_scan(ROOT):
            for number, line in enumerate(path.read_text(encoding="utf-8")
                                          .splitlines(), start=1):
                for found in REPORT_LINE.findall(line):
                    if found != self.version():
                        stale.append(f"{path.relative_to(ROOT)}:{number}: "
                                     f"{found}")
        self.assertEqual(stale, [], f"{len(stale)} place(s) quote a version that is "
                                    f"not {self.version()}: {stale}")


class TestEveryCommandCanSayWhichVersionItIs(unittest.TestCase):
    """``--version``: the question a copy of this project has to answer on its own.

    The number is printed on the first line of every report, recorded in every
    ``season.json`` and shown in both footers -- and all three of those need a run
    that got far enough to produce output. The copy somebody actually has a question
    about is the one that cannot: a folder handed on by last year's secretary, a
    checkout whose first run cannot find ``data/``, an archive on a machine with no
    browser. Before this flag the only way to answer *which version is this* was to
    open a Python file and read a constant, which is not a thing a club does.

    A parser per command, and a chance per parser to disagree, so every one is asked and
    their answers compared with each other. That the answer is also byte-identical to
    the report's first line is pinned in ``test_cli.py``, where there is a real report
    to compare against: two people quoting "the version" must be quoting the same
    string, or the first reply to every issue is a question about which one they mean.

    Rejected: a ``VERSION`` file at the top of the project. It reads well from a
    shell, and it is a second place for the number to be wrong -- and it cannot say
    which Python is running, which is the other half of what the line is for.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.empty = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def ask(self, entry: str) -> subprocess.CompletedProcess:
        # Run from a directory with no `data/`, no `config.toml` and nothing else
        # in it, because that is the state of the copy this flag exists for.
        return subprocess.run((sys.executable, str(ROOT / entry), "--version"),
                              cwd=self.empty, capture_output=True, text=True,
                              timeout=120)

    def version(self) -> str:
        import matchcentre
        return matchcentre.__version__

    def test_every_one_of_them_answers_it(self):
        for entry in ENTRY_POINTS:
            with self.subTest(command=entry):
                done = self.ask(entry)
                self.assertEqual(done.returncode, 0,
                                 f"python3 {entry} --version exited "
                                 f"{done.returncode}: {done.stderr[-800:]}")
                self.assertTrue(done.stdout.strip(),
                                f"python3 {entry} --version printed nothing to "
                                f"stdout")

    def test_the_answer_is_one_line_and_nothing_else(self):
        # A club is asked to paste this into an issue, and a paste that arrives as
        # a usage message with a version buried in it is a paste somebody has to
        # read before they can use it. Nothing on stderr either: a line printed
        # there is a line a shell redirect loses.
        for entry in ENTRY_POINTS:
            with self.subTest(command=entry):
                done = self.ask(entry)
                self.assertEqual(len(done.stdout.splitlines()), 1,
                                 f"python3 {entry} --version printed "
                                 f"{len(done.stdout.splitlines())} lines: "
                                 f"{done.stdout!r}")
                self.assertEqual(done.stderr, "")

    def test_they_all_give_the_same_answer(self):
        answers = {entry: self.ask(entry).stdout.strip() for entry in ENTRY_POINTS}
        self.assertEqual(len(set(answers.values())), 1,
                         f"the commands describe this build "
                         f"{len(set(answers.values()))} ways, so "
                         f"which one a report quotes changes what it says: "
                         f"{answers}")

    def test_the_answer_names_this_release_and_the_python_running_it(self):
        running = ".".join(str(part) for part in sys.version_info[:3])
        for entry in ENTRY_POINTS:
            with self.subTest(command=entry):
                line = self.ask(entry).stdout.strip()
                self.assertIn(self.version(), line)
                # "works on mine, not on yours" is answered by the interpreter and
                # the platform together and by nothing else in the report.
                self.assertIn(running, line)
                self.assertIn(sys.platform, line)

    def test_the_doctor_does_not_demand_a_card_before_it_will_say(self):
        # `tools/doctor.py` takes one required positional -- the PDF to explain --
        # and a version flag that inherits that requirement answers the question
        # only for somebody who already has a scorecard to hand. Argparse acts on
        # the flag while parsing and exits before it checks what is missing, which
        # is the behaviour being relied on here rather than assumed.
        done = self.ask("tools/doctor.py")
        self.assertEqual(done.returncode, 0,
                         f"the doctor refused to say which version it is without "
                         f"a PDF: {done.stderr[-800:]}")
        self.assertNotIn("required", done.stderr.lower())

    def test_none_of_them_needs_a_project_to_run_in_or_leaves_one_behind(self):
        # The whole point of the flag: no cards, no config file, no browser, no
        # output folder. A command that writes a `config.toml` or a `site/` on its
        # way to printing one line is one that cannot be run on a folder somebody
        # else owns.
        for entry in ENTRY_POINTS:
            with self.subTest(command=entry):
                self.ask(entry)
                left = sorted(p.name for p in self.empty.iterdir())
                self.assertEqual(left, [], f"python3 {entry} --version wrote "
                                           f"{left} into the directory it was run "
                                           f"in")
        # And nowhere else either, which the check above cannot see: the two
        # commands that write do not write where they are run. `tools/demo.py`
        # writes into the project's own `data/demo-season`, so a version flag
        # handled after the work instead of during the parse -- which is how one
        # gets written by hand -- prints the right line and leaves a fictional
        # season behind it.
        #
        # Asserted as an absence rather than as a difference from a baseline taken
        # at the top of this test, because the tests above have already asked every
        # command the same question: by the time a baseline was measured the
        # season would already be there, and every later run would write the same
        # bytes to the same paths and look like a difference of nothing.
        cards = sorted(p.relative_to(ROOT).as_posix()
                       for p in (ROOT / "data").rglob("*.pdf"))
        self.assertEqual(cards, [], f"asking for a version left {len(cards)} "
                                    f"scorecard(s) in the project's data folder: "
                                    f"{cards}")

    def test_no_command_builds_the_line_for_itself(self):
        # The equality test above catches the copies drifting apart only while every
        # one is exercised, and the cheapest way to add another is to write the
        # f-string again in the next tool. One function owns the format; an entry
        # point asks for it.
        restated = []
        for entry in ENTRY_POINTS:
            source = (ROOT / entry).read_text(encoding="utf-8")
            if "version_line" not in source:
                restated.append(f"{entry}: does not call version_line()")
            if "matchcentre-dashboard" in source:
                restated.append(f"{entry}: spells the line out itself")
        self.assertEqual(restated, [], f"{len(restated)} entry point(s) own a copy "
                                       f"of the version line: {restated}")


class TestTheVersionNumberPromisesSomethingInWriting(unittest.TestCase):
    """What the three numbers mean, for a program that has no API to break.

    ``CHANGELOG.md`` links to semver.org, which defines a MAJOR bump as a breaking
    change to the public API. This project does not have one: it is a script a club
    runs over a folder of PDFs. So the link on its own promises nothing -- a club
    reading it cannot tell whether the next version will still read the
    ``config.toml`` they spent an evening filling in, and a maintainer has no rule
    for which number to move. What stands in for the interface is written down
    instead: the config file loading, ``data/`` being found, the flags, the keys in
    ``season.json``, the dashboard's own URLs, and the figures the same cards
    produce.

    Rejected: leaving it to the changelog entries. They say what changed, which is a
    different question from whether the files a club already has still work.
    """

    def readme(self) -> str:
        return (ROOT / "README.md").read_text(encoding="utf-8")

    def section(self, text: str, word: str) -> str:
        """One ``##`` section of a document, found by a word in its heading."""
        blocks = re.split(r"(?m)^## ", text)
        found = [block for block in blocks[1:]
                 if word in block.splitlines()[0].lower()]
        self.assertEqual(len(found), 1, f"expected one '## ...{word}...' section, "
                                       f"found {len(found)}")
        return "## " + found[0]

    def versions(self) -> str:
        return self.section(self.readme(), "version")

    def test_it_says_how_to_do_the_upgrade_and_not_only_what_it_means(self):
        # A policy with no instruction under it leaves the club to guess, and the
        # guess that loses a season is "download the new one and start again" --
        # there is nothing in the tree that says `data/` and `config.toml` are the
        # two things to carry across, or that `--check` will tell you whether this
        # release still likes your settings before it touches your `site/`.
        steps = [block for block in re.split(r"(?m)^### ", self.versions())[1:]
                 if "upgrade" in block.splitlines()[0].lower()]
        self.assertEqual(len(steps), 1, "the versioning section explains what the "
                                        "numbers mean and never says what to do "
                                        "about one")
        for needed in ("data/", "config.toml", "--check"):
            with self.subTest(names=needed):
                self.assertIn(needed, steps[0])

    def test_each_of_the_three_numbers_has_a_row_of_its_own(self):
        # A table rather than a paragraph, and one row each, because the question a
        # club asks is about one of the three: "0.2.0 is out, is that safe for me?"
        # A prose paragraph covering two of them together reads as though the third
        # never happens.
        rows = re.findall(r"(?m)^\|\s*(MAJOR|MINOR|PATCH)\b", self.versions())
        self.assertEqual(sorted(rows), ["MAJOR", "MINOR", "PATCH"],
                         f"the versioning table has rows for {rows}, so a club "
                         f"looking up one of the three numbers may not find it")

    def test_it_says_which_bump_can_move_a_figure_a_club_already_published(self):
        # The exception that matters most and reads as the least important: a card
        # that was being mis-read, now read correctly, changes a ladder somebody has
        # already emailed round. That is a PATCH by every other measure -- nothing a
        # club types changes -- so the section has to say it in the PATCH row, where
        # somebody deciding whether to bother upgrading will see it.
        lines = [line for line in self.versions().splitlines()
                 if "corrected" in line.lower()]
        self.assertTrue(lines, "nothing in the section says that a corrected "
                               "reading moves numbers a club may have published")
        self.assertIn("PATCH", lines[0],
                      f"a corrected number is filed under something other than "
                      f"PATCH, so the row a club reads as safe does not mention "
                      f"it: {lines[0]!r}")

    def test_it_says_how_to_ask_a_copy_which_version_it_is(self):
        self.assertIn("--version", self.versions())

    def test_the_list_of_commands_offers_the_flag_too(self):
        # A flag documented only in the section about versioning is a flag found by
        # somebody who already knew it existed.
        commands = self.section(self.readme(), "all the commands")
        self.assertIn("--version", commands)

    def test_the_changelog_says_where_the_three_numbers_are_defined(self):
        # The one sentence that makes the semver.org link mean something here. It is
        # in the changelog because that is where somebody comparing two versions is
        # already reading.
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        claim = [block for block in text.split("\n\n") if "semver.org" in block]
        self.assertTrue(claim, "the changelog no longer claims Semantic "
                               "Versioning, so this rule has nothing to hold")
        self.assertIn("README.md", claim[0],
                      f"the changelog claims Semantic Versioning and does not say "
                      f"where this project's version of it is written down: "
                      f"{claim[0]!r}")

    def test_contributing_says_how_a_release_is_actually_made(self):
        release = self.section(
            (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"), "release")
        for needed in ("matchcentre/__init__.py", "CHANGELOG.md", "tag"):
            with self.subTest(step=needed):
                self.assertIn(needed, release)

    def test_the_release_steps_are_in_the_order_they_have_to_happen(self):
        # Tagging first ships a tag whose code still says the old number. The suite
        # catches that -- the changelog's newest entry and the constant have to
        # agree -- but it catches it after the tag exists, and a tag is the one
        # thing in this process that other people may already have fetched.
        release = self.section(
            (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"), "release")
        self.assertLess(release.index("matchcentre/__init__.py"),
                        release.index("git tag"),
                        "the release steps put the tag before the version bump")


class TestEveryFlagACommandAcceptsIsInTheListOfCommands(unittest.TestCase):
    """``## All the commands`` is the list, and a list is only useful if it is whole.

    A flag that exists and is documented nowhere is a feature nobody finds. This
    one was written for `--prune`, which a club has no reason to guess at and every
    reason to want: without it the only way to delete a stale dashboard is `rm -rf`
    aimed at a folder inside a published site.

    Both directions, because both have happened to this file. A flag added to
    ``build.py`` and not to the README is invisible; a flag the README still lists
    after it was renamed is worse, because a club types it and gets an argparse
    error about a program that is supposedly its own documentation.

    Read out of ``--help`` rather than out of the source, so that what is checked is
    the surface a club actually meets. Every flag of the two commands a club runs
    week to week, ``build.py`` and ``tools/scheduled.py``, has to be in the list.
    The other way round is asked of every command, so that a flag of a tool's is
    excused because that tool has it rather than because somebody wrote it into a
    set here. Measured when ``tools/scheduled.py`` arrived: the hand-kept excuses
    were ``--page``, ``--redact`` and ``--clean``, and its ``--reports``,
    ``--keep``, ``--timeout`` and ``--print-schedule`` would have made seven.
    """

    HEADING = "## All the commands"
    LISTED = ("build.py", "tools/scheduled.py")

    def flags(self, entry: str) -> set[str]:
        """Every long option ``python3 <entry> --help`` offers, and every one
        each of its subcommands' ``--help`` offers.

        ``tools/release.py`` is a command of four subcommands, each with
        flags of its own that its ``--help`` alone lists. They are read from
        the list the command's own ``--help`` gives, so that a fifth is read
        without this being told.
        """
        # Named through the class, because a test of another class borrows
        # this method with its own self.
        options = TestEveryFlagACommandAcceptsIsInTheListOfCommands.options
        found, text = options(self, entry)
        listed = text.split("positional arguments:\n", 1)[1:]
        for command in re.findall(r"(?m)^ {4}([a-z][a-z-]*)(?: {2,}|$)",
                                  listed[0].split("\n\n", 1)[0]
                                  if listed else ""):
            found |= options(self, entry, command)[0]
        return found

    def options(self, entry: str, *command: str) -> tuple[set[str], str]:
        """Every long option ``python3 <entry> [command] --help`` offers,
        and the whole of what it printed."""
        done = subprocess.run((sys.executable, str(ROOT / entry), *command,
                               "--help"),
                              capture_output=True, text=True, timeout=120,
                              env={"PATH": "/usr/bin:/bin", "COLUMNS": "80",
                                   # tools/scheduled.py imports build.py, and a
                                   # test leaves no bytecode in the checkout.
                                   "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, done.stderr)
        found = set()
        for line in done.stdout.splitlines():
            # An option entry starts at two spaces with a dash. Its help text is
            # indented far deeper and wraps without one, so the flags inside a help
            # sentence -- `--force` inside what `--discover` says -- are not counted
            # as entries of their own.
            if not re.match(r"^ {2}-", line):
                continue
            found.update(re.findall(r"--[a-z][a-z-]*",
                                    re.split(r"\s{2,}", line.strip())[0]))
        # Every entry point has `--version`, which the class above holds them
        # to, and so does every subcommand of one.
        self.assertIn("--version", found, f"nothing was parsed out of {entry} "
                                          f"{' '.join(command)} --help:\n"
                                          f"{done.stdout}")
        return found, done.stdout

    def commands(self) -> str:
        """The fenced block under the heading that promises to list them all."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        start = readme.index(self.HEADING)
        block, inside = [], False
        for line in readme[start + 1:]:
            if line.startswith("```"):
                if inside:
                    break
                inside = True
            elif inside:
                block.append(line)
        self.assertTrue(block, f"the README has no command block under "
                              f"{self.HEADING!r}")
        return "\n".join(block)

    def test_every_flag_it_accepts_is_in_the_list(self):
        # `--help` excepted, and only that one: the block ends by saying every
        # command takes it, which is a better line than eleven repetitions of it.
        block = self.commands()
        for entry in self.LISTED:
            with self.subTest(command=entry):
                self.assertIn(entry, block, f"{entry} is not in the list at all")
                # A read of no flags would pass the check below with none to
                # miss. Measured by mutation: with flags() reading only the
                # subcommands', of which these two have none, this test passed.
                accepted = self.flags(entry)
                self.assertIn("--version", accepted,
                              f"no flags were read out of {entry} --help")
                missing = sorted(flag for flag in accepted - {"--help"}
                                 if flag not in block)
                self.assertEqual(missing, [],
                                 f"{entry} accepts flag(s) the README's list of "
                                 f"commands does not mention: {missing}")

    def test_every_flag_the_list_offers_is_one_it_accepts(self):
        offered = set(re.findall(r"--[a-z][a-z-]*", self.commands()))
        accepted = set().union(*(self.flags(entry) for entry in ENTRY_POINTS))
        unknown = sorted(offered - accepted)
        self.assertEqual(unknown, [], f"the README's list of commands offers "
                                      f"flag(s) no command accepts: {unknown}")


class TestEveryFetchFlagIsInTheReadme(TestEveryFlagACommandAcceptsIsInTheListOfCommands):
    """The fetcher's flags are in the list of commands, on its own lines, both ways.

    Held to its own lines rather than to the block, because the block is mostly
    ``build.py``'s: a fetcher flag that appeared only in a ``build.py`` line would
    pass a block-wide check and send a club to the wrong program. ``--version`` is
    excused as ``--help`` is: the block says every command takes it. Rejected:
    listing the fetcher's flags only in its own README section, which is not the
    list a club reads to find out what there is.
    """

    PROGRAM = "tools/fetch.py"

    def lines(self) -> str:
        return "\n".join(line for line in self.commands().splitlines()
                         if line.startswith(f"python3 {self.PROGRAM}"))

    def test_every_flag_it_accepts_is_in_the_list(self):
        on = self.lines()
        self.assertTrue(on, f"no line of the command list runs {self.PROGRAM}")
        missing = sorted(flag for flag in self.flags(self.PROGRAM)
                         - {"--help", "--version"}
                         if not re.search(rf"{re.escape(flag)}(?![a-z-])", on))
        self.assertEqual(missing, [], f"{self.PROGRAM} accepts flag(s) its lines in "
                                      f"the list of commands do not mention: "
                                      f"{missing}")

    def test_every_flag_the_list_offers_is_one_it_accepts(self):
        offered = set(re.findall(r"--[a-z][a-z-]*", self.lines()))
        unknown = sorted(offered - self.flags(self.PROGRAM))
        self.assertEqual(unknown, [], f"the list of commands offers {self.PROGRAM} "
                                      f"flag(s) it does not accept: {unknown}")


class TestEveryReleaseFlagIsInTheReadme(TestEveryFetchFlagIsInTheReadme):
    """The release tool's flags are in the list of commands, on its own lines.

    Held as the fetcher's are, and for the same reason: the block is mostly
    ``build.py``'s, and a release flag on a ``build.py`` line, ``--out`` among
    them, would pass a block-wide check. Its flags are its subcommands', which
    its own ``--help`` does not list, so they are read from each subcommand's.
    Rejected: leaving a maintainer's tool out of the list, as
    ``tools/screenshots.py`` is, which leaves ``--dry-run`` to be found by
    reading the source.
    """

    PROGRAM = "tools/release.py"


# The standard library's modules whose own work is a connection, a server or a name
# lookup. Not every module that can reach one: measured, `xml.sax.saxutils`,
# `email.message`, `mailbox`, `importlib.metadata` and `unittest.mock` each load
# `socket` on import, so what the commands actually load is checked by running them,
# below. `urllib.parse`, and `http` on its own for `HTTPStatus`, are string and enum
# code and stay allowed everywhere.
NETWORK_MODULES = frozenset({
    "socket", "_socket", "ssl", "http.client", "http.server", "urllib.request",
    "urllib.robotparser", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
    "xmlrpc", "asyncio", "selectors", "socketserver", "logging.handlers",
    "multiprocessing.connection", "multiprocessing.managers", "wsgiref",
    "webbrowser"})

# Runs a command, then says on its last line whether `socket` was ever loaded.
LOADED = r'''
import os, runpy, sys
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    loaded = [name for name in ("socket", "_socket") if name in sys.modules]
    sys.stderr.write(f"\nLOADED {' '.join(loaded) or 'nothing'}\n")
'''


def network_imports(path: Path) -> list[tuple[int, str]]:
    """Each network module a file imports, with its line: ``from http import
    client`` counts as ``http.client``, and ``xmlrpc.client`` as ``xmlrpc``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            names = [module] + [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            for depth in (1, 2):
                prefix = ".".join(name.split(".")[:depth])
                if prefix in NETWORK_MODULES:
                    found.append((node.lineno, prefix))
    return sorted(set(found))


class TestOnlyTheFetcherHasNetworkCode(unittest.TestCase):
    """One program can open a connection, and the build is not it.

    The promise the README makes is that the build never touches a network, and
    the fetcher that now can is a separate program that the build does not import.
    Read from the import statements rather than from the text, so that a comment
    naming a module is not code that uses it, and then by running the commands,
    because a module can load ``socket`` without being one the list names. Rejected:
    allowing a network module anywhere a reviewer happened to agree to it, which is
    how the second one arrives.
    """

    ALLOWED = {"tools/fetch.py": {"http.client", "urllib.request"},
               "tests/test_fetch.py": {"http.server", "socket", "urllib.request"}}
    OPENERS = {"urlopen", "build_opener", "OpenerDirector", "Request"}

    def sources(self) -> list[Path]:
        return python_sources() + sorted((ROOT / "tests").glob("*.py"))

    def test_no_other_file_imports_a_module_that_can_open_a_connection(self):
        found = []
        for path in self.sources():
            rel = path.relative_to(ROOT).as_posix()
            allowed = self.ALLOWED.get(rel, set())
            found += [f"{rel}:{line}: {name}" for line, name in network_imports(path)
                      if name not in allowed]
        self.assertEqual(found, [], f"network code outside the fetcher: {found}")

    def test_the_build_and_the_package_have_none_at_all(self):
        paths = [ROOT / "build.py"] + sorted((ROOT / "matchcentre").rglob("*.py"))
        found = [f"{p.relative_to(ROOT).as_posix()}: {name}"
                 for p in paths for _, name in network_imports(p)]
        self.assertEqual(found, [])

    def test_the_scan_sees_the_fetcher_s_own_connection(self):
        # Without this the two tests above pass on a scan that finds nothing.
        self.assertIn("http.client",
                      {name for _, name in network_imports(ROOT / "tools/fetch.py")})

    def loaded(self, where: Path, *command: str) -> str:
        done = subprocess.run((sys.executable, "-c", LOADED, *command), cwd=where,
                              capture_output=True, text=True, timeout=300,
                              env={"PATH": "/usr/bin:/bin",
                                   "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertIn(done.returncode, (0, 1), done.stdout + done.stderr)
        return done.stderr.splitlines()[-1]

    def test_the_demo_the_build_and_the_doctor_never_load_socket(self):
        # The import scan reads a list, and a list can miss a module that loads
        # `socket` for its own reasons; measured, none of these three loads it.
        # The fetcher, measured the same way, loads `socket`, `ssl` and
        # `http.client`, and is the control that the probe sees a load at all.
        said = {}
        with tempfile.TemporaryDirectory(prefix="test-no-socket-") as holder:
            where = Path(holder)
            said["demo"] = self.loaded(where, str(ROOT / "tools/demo.py"),
                                       "--out", "season")
            said["build"] = self.loaded(where, str(ROOT / "build.py"), "--data",
                                        "season", "--out", "site", "--config",
                                        "config.toml", "--generated",
                                        "2026-09-10T00:00:00")
            card = sorted((where / "season").rglob("*.pdf"))[0]
            said["doctor"] = self.loaded(where, str(ROOT / "tools/doctor.py"),
                                         "--redact", str(card))
            said["fetch"] = self.loaded(where, str(ROOT / "tools/fetch.py"),
                                        "--version")
        self.assertEqual(said, {"demo": "LOADED nothing", "build": "LOADED nothing",
                                "doctor": "LOADED nothing",
                                "fetch": "LOADED socket _socket"})

    def test_the_fetcher_never_names_an_opener(self):
        # An opener reads proxies from the environment and follows redirects; the
        # fetcher's one connection is `http.client`, which does neither.
        tree = ast.parse((ROOT / "tools/fetch.py").read_text(encoding="utf-8"))
        named = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
            elif isinstance(node, ast.alias):
                named.add(node.name.split(".")[-1])
        self.assertEqual(sorted(named & self.OPENERS), [])


class TestTheBuildHasNoWayToFetch(unittest.TestCase):
    """``build.py`` reads a folder, and has no flag or import that fetches one.

    A fetch folded into the build would make every weekly build a network run, and
    the build's promise is that it never is one. Rejected: a ``--fetch`` flag on the
    build for convenience, which is two lines in a script instead.
    """

    def test_its_help_offers_no_address(self):
        done = subprocess.run((sys.executable, str(ROOT / "build.py"), "--help"),
                              capture_output=True, text=True, timeout=120,
                              env={"PATH": "/usr/bin:/bin", "COLUMNS": "80",
                                   "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, done.stderr)
        for flag in ("--from", "--fetch", "--url"):
            self.assertNotIn(flag, done.stdout)

    def test_it_does_not_import_the_fetcher(self):
        tree = ast.parse((ROOT / "build.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
                imported |= {alias.name for alias in node.names}
        self.assertNotIn("fetch", imported)


# The functions that put rounds in order. Each takes a draw after what it orders,
# and handed one it is a competition's order and not the labels'.
ROUND_KEYS = frozenset({"round_sort_key", "fixture_sort_key", "match_sort_key",
                        "chronological", "rounds_present"})


def rounds_ordered_by_label(path: Path) -> set[str]:
    """Every function in *path* that asks a round key of the labels alone.

    A call handing it a second argument or a ``draw`` asks it with the draw; a call
    with the one argument, or the key handed on uncalled (``key=round_sort_key``),
    asks it without. Named as ``file:function``, or ``file:Class.function``.
    """
    found: set[str] = set()
    where = path.relative_to(ROOT).as_posix()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        called = node.func if isinstance(node, ast.Call) else None
        for child in ast.iter_child_nodes(node):
            name = (child.id if isinstance(child, ast.Name)
                    else child.attr if isinstance(child, ast.Attribute) else None)
            if name in ROUND_KEYS and not (
                    child is called and (len(node.args) > 1 or any(
                        k.arg == "draw" for k in node.keywords))):
                found.add(f"{where}:{'.'.join(scope) or '<module>'}")
            visit(child, scope)

    visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), ())
    return found


class TestEveryListAFinalCanBeInIsOrderedByItsDraw(unittest.TestCase):
    """A round key asked without the draw is asked only of a list no final is in.

    The key puts a final the draw declares under a name of its own -- ``Major
    Semi`` between the QF and the GF -- where the draw puts it only when it is
    handed the draw, and a caller that asks it of the label alone puts the Major
    Semi ahead of the QF, as every caller did before (4.71; measured,
    ``/tmp/t7/scratch/ROUNDS/probe.py``). Most of the lists it orders are ones no
    test builds a season to look at: the order each of validate's checks names its
    findings in, the order discovery reads its matches in. So they are read here: a
    function that asks it of labels is one of those below, with the reason no
    declared final can be in what it orders, and a new one fails this until it
    hands the key the draw or is added with its reason.

    Rejected: a draw every caller must pass, which is an edit to every caller of the
    label key and a draw made up for the fetcher, which has none; and a season built
    with a fault on two finals' cards for each check that names one.
    """

    LADDER = "the ladder's matches, which hold no final"
    LABEL_ONLY = {
        "matchcentre/discover.py:_add_bye_only_rounds": "rounds the draw does not list",
        "matchcentre/discover.py:_build_draw": "rounds the draw does not list",
        "matchcentre/sheet.py:_checked": "a sheet's numbered round",
        "matchcentre/sheet.py:_evidence_rounds": "a sheet's numbered rounds",
        "matchcentre/sheet.py:compare": "rounds up to a sheet's numbered round",
        "matchcentre/stats.py:_share_walk": LADDER,
        "matchcentre/stats.py:head_to_head": "a count, which has no order",
        "matchcentre/stats.py:ladder_history": LADDER,
        "matchcentre/stats.py:player_names": "a name for each player, in any order",
        "matchcentre/stats.py:round_points": LADDER,
        "matchcentre/stats.py:team_stats": LADDER,
        # The sheet asks it of every fixture without a card, finals among them,
        # but only ever up to a numbered round, and every final comes after one.
        "matchcentre/stats.py:through_round": "the ladder's matches, or any up to "
                                              "a sheet's numbered round",
        "matchcentre/stats.py:with_byes": "a set",
        "matchcentre/validate.py:_check_byes": "a set",
        "tools/fetch.py:parse_index": "an index page's rounds: no competition, no draw",
    }

    def test_every_other_list_of_rounds_is_ordered_by_its_draw(self):
        found = set().union(*map(rounds_ordered_by_label, python_sources()))
        self.assertEqual(sorted(found - set(self.LABEL_ONLY)), [],
                         "these order rounds by their labels alone, so a final the "
                         "draw declares under a name of its own goes ahead of the QF")
        self.assertEqual(sorted(set(self.LABEL_ONLY) - found), [],
                         "these are listed as ordering by label and no longer do")


# The default config's name as a sentence gives it, and not followed by more of a
# name: config.toml.example is some other file.
DEFAULT_CONFIG_SAID = re.compile(r"config\.toml(?!\.?\w)")


def sentences_naming_the_default_config(source: str, where: str) -> list[str]:
    """Each string of *source* that names config.toml in a sentence, with its line.

    A sentence is a string with a space in it, so the bare value ``"config.toml"``
    is not one. A docstring is not either, since no run prints it, and nor is a
    comment, which ``ast`` never sees. A part of an f-string is a string of its own.
    """
    tree = ast.parse(source, filename=where)
    docstrings = {id(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                  and isinstance(node.value.value, str)}
    found = [node for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and id(node) not in docstrings and " " in node.value
             and DEFAULT_CONFIG_SAID.search(node.value)]
    return [f"{where}:{node.lineno}: {node.value[:70]!r}"
            for node in sorted(found, key=lambda n: (n.lineno, n.col_offset))]


class TestNoSentenceNamesTheDefaultConfigByItsLiteralName(unittest.TestCase):
    """No string the program prints names config.toml whatever file was read.

    Measured before (/tmp/t7/scratch/CF1/sweep.py over the shipped sources): 13
    strings named it outright, the rules' footer, F8, the advice for a name found
    on the front page, a label or a key, the byes, [namesakes], a player on two
    teams, a season across years, Python's tomllib, the demo's own config and a
    help line, and under ``--config other.toml`` each of them sent the club to a
    file the build had not read. Each now gives the file read, by ``config.named``
    where the sentence is published, or ``DEFAULT_PATH`` where it means the
    default. Rejected: a test per sentence, which leaves the fourteenth free to be
    written; the default's name as a value is left alone, since that is what a
    default is. The scan reads the name as written and no further: a sentence
    built on ``DEFAULT_PATH`` itself, ``f"... in {DEFAULT_PATH} and re-run."``, is
    not seen (/tmp/t7/scratch/CF1-review/mut/probe3.py), and those there are,
    build.py's and tools/scheduled.py's ``--config`` help, the demo file's own
    comment and the plain demo's line on byes, mean the default.
    """

    def test_no_shipped_string_names_it_in_a_sentence(self):
        found = [hit for path in python_sources()
                 for hit in sentences_naming_the_default_config(
                     path.read_text(encoding="utf-8"),
                     path.relative_to(ROOT).as_posix())]
        self.assertEqual(found, [], "these name config.toml whatever --config says")

    def test_the_scan_finds_a_sentence_and_passes_a_value_and_a_docstring(self):
        planted = ('"""The module, which reads config.toml by default."""\n'
                   'DEFAULT = "config.toml"\n'
                   'EXAMPLE = "see config.toml.example for more"\n'
                   'def told(name):\n'
                   '    """Says where, in config.toml or wherever."""\n'
                   '    plain = "Rename it in config.toml: the title"\n'
                   '    return f"Set {name} in config.toml and re-run."\n')
        found = sentences_naming_the_default_config(planted, "planted.py")
        self.assertEqual([hit.split(":")[1] for hit in found], ["6", "7"], found)


class TestAChildGivenAnEnvironmentOfItsOwnWritesNoBytecode(unittest.TestCase):
    """Every environment a test writes out whole says ``PYTHONDONTWRITEBYTECODE``.

    A child handed an ``env`` that does not spread ``os.environ`` does not inherit
    the variable, so under ``PYTHONDONTWRITEBYTECODE=1 python3 -m unittest`` it still
    writes bytecode for the modules it imports from the checkout. Measured before:
    a full run of the suite left one folder, ``matchcentre/__pycache__`` with 22
    files, and ``TestTheBuildHasNoWayToFetch`` alone made it again. Its
    ``build.py --help`` was the one environment written out whole without the
    variable. The two others written out whole had it, and the ten more are made from
    ``os.environ``, which passes the parent's on (/tmp/t4/scratch/g2/envs-before.txt).

    Held by a scan of the tests' source. It reads a call's ``env=``, and a name
    bound to a dict or to ``dict()``, annotated or not, that ends in ``env`` or is
    handed to a call as its ``env=``. It does not read an ``env`` passed through
    ``**``, which no test does. Rejected: one helper that builds every such
    environment, which a test written by hand need not call, so a scan would still be
    what holds the rule. And a test that the tree holds no ``__pycache__`` after the
    run, which depends on the order the tests run in, and fails for anybody who ran
    the suite without the variable.
    """

    # One of each way of writing an environment: out whole and without the
    # variable, spread, out whole through dict(), out whole with it, spread through
    # dict(), out whole with the variable empty, annotated, and under a name that
    # says nothing of an env until a call is handed it.
    PLANTED = (
        'subprocess.run(argv, env={"PATH": "/usr/bin:/bin"})\n'
        'subprocess.run(argv, env={**os.environ, "COLUMNS": "80"})\n'
        'child_env = dict(PATH="/usr/bin:/bin")\n'
        'ENV = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}\n'
        'subprocess.run(argv, env=dict(os.environ, COLUMNS="80"))\n'
        'subprocess.run(argv, env={"PATH": "/bin", "PYTHONDONTWRITEBYTECODE": ""})\n'
        'env: dict = {"PATH": "/usr/bin:/bin"}\n'
        'SETTINGS = {"PATH": "/usr/bin:/bin"}\n'
        'subprocess.run(argv, env=SETTINGS)\n')

    @staticmethod
    def written_whole(source: str) -> tuple[int, list[int]]:
        """How many environments *source* writes out whole, and the line of each one
        that does not set ``PYTHONDONTWRITEBYTECODE`` to ``"1"``."""
        whole, bare = 0, []
        tree = ast.parse(source)
        handed = {kw.value.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                  for kw in node.keywords
                  if kw.arg == "env" and isinstance(kw.value, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                values = [kw.value for kw in node.keywords if kw.arg == "env"]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                    isinstance(target, ast.Name)
                    and (target.id.lower().endswith("env") or target.id in handed)
                    for target in (node.targets if isinstance(node, ast.Assign)
                                   else [node.target])):
                values = [node.value]
            else:
                continue
            for value in values:
                if isinstance(value, ast.Dict) and None not in value.keys:
                    given = {key.value: said for key, said in zip(value.keys,
                                                                  value.values)
                             if isinstance(key, ast.Constant)}
                elif (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                      and value.func.id == "dict" and not value.args
                      and None not in [kw.arg for kw in value.keywords]):
                    given = {kw.arg: kw.value for kw in value.keywords}
                else:
                    continue
                whole += 1
                said = given.get("PYTHONDONTWRITEBYTECODE")
                if not (isinstance(said, ast.Constant) and said.value == "1"):
                    bare.append(value.lineno)
        return whole, sorted(bare)

    def test_every_one_a_test_writes_out_whole_says_it(self):
        whole, bare = 0, []
        for path in sorted((ROOT / "tests").glob("*.py")):
            count, lines = self.written_whole(path.read_text(encoding="utf-8"))
            whole += count
            bare += [f"tests/{path.name}:{line}" for line in lines]
        self.assertEqual(bare, [])
        self.assertGreaterEqual(whole, 3, "the scan found none of the environments "
                                          "it was written for")

    def test_the_scan_reads_each_way_of_writing_one(self):
        self.assertEqual(self.written_whole(self.PLANTED), (6, [1, 3, 6, 7, 8]))


class TestAModuleCacheIsClearedWithTheFolderItsCleanupRemoves(unittest.TestCase):
    """A module-level list, dict or set a test fills is emptied by the cleanup too.

    unittest runs a module's cleanups each time a run moves on to another module,
    and sets the module up again if it comes back. A cache filled once per run and
    never emptied outlives the folder the cleanup removed, and the next class
    copies from nothing. Measured before, on a copy of the tree: ``test_scheduled``'s
    ``_TEMPLATE`` (``_real_copy``), through eight of its classes, then
    ``test_cli.TestARefusalOnStderrNeverCutsAPath`` and ``test_project_complete``,
    then ``test_scheduled`` whole, gave 203 tests and 7 errors, each
    ``FileNotFoundError`` on ``/tmp/scheduled-real-*/club dashboard``; each module
    named once, 186 OK. ``test_cli``'s ``_SETTLED`` (``_settled_project``), through
    a status class, a class of this module and two more status classes, gave 4 of
    12 in error on ``/tmp/status-*/project``; each module named once, 12 OK
    (/tmp/t7/scratch/INT/r245-*.log). ``test_fetch``'s ``ENTRIES`` had the shape
    and did not fail, since its ``setUpModule`` fills it whole each time it runs; it
    is cleared with the rest so that the rule has no exception. Of the eight
    module-level containers tests/ fills in nine places
    (/tmp/t7/scratch/INT/p245/survey2.py), the other five were cleared already:
    ``test_demo``'s and ``test_careers``' three beside the cleanup, and
    ``test_anonymise``'s ``_BUILT`` in the ``tearDownModule`` that removes its folder.

    Held by a scan of the tests' source: a container bound at module level to a
    display or to ``list()``, ``dict()`` or ``set()``, which a function fills
    (``append``, ``extend``, ``insert``, ``add``, ``update``, ``setdefault``, an
    item or an augmented assignment), in a module where that function or
    ``setUpModule`` registers a module cleanup, or ``tearDownModule`` removes
    something, is cleared by ``unittest.addModuleCleanup(<name>.clear)`` beside
    the cleanup of something else, in the function that registers it, or in
    ``tearDownModule``. A cleanup that only empties a container removes nothing. A
    clear registered as the module is imported runs when the run leaves its first
    module, whichever that is, and never again: ``_TEMPLATE``'s moved there passed
    a scan of any clear, and gave 7 errors in 101 tests run with a class of this
    module first (/tmp/t7/scratch/INT/fix/r245-module-level-2.log). Rejected: only
    the function that fills it, or a ``setUpModule`` that calls that function.
    ``test_demo``'s ``_RUNS`` and ``test_careers``' three caches are filled by the
    tests, never by ``setUpModule``, which makes their folder, so that rule would
    pass them with their clears taken out. And a test that runs modules
    interleaved, which is most of a minute and holds only the modules it names.
    """

    # Each source, and the containers the scan finds and those it finds uncleared.
    PLANTED = {
        # A helper that makes the folder once, and forgets to empty its list.
        "_A: list = []\n"
        "def helper():\n"
        "    if not _A:\n"
        "        tmp = tempfile.TemporaryDirectory()\n"
        "        unittest.addModuleCleanup(tmp.cleanup)\n"
        "        _A.append(Path(tmp.name))\n"
        "    return _A[0]\n": ({"_A"}, [(1, "_A")]),
        # The same helper, clearing it beside the cleanup.
        "_A: list = []\n"
        "def helper():\n"
        "    if not _A:\n"
        "        tmp = tempfile.TemporaryDirectory()\n"
        "        unittest.addModuleCleanup(tmp.cleanup)\n"
        "        unittest.addModuleCleanup(_A.clear)\n"
        "        _A.append(Path(tmp.name))\n"
        "    return _A[0]\n": ({"_A"}, []),
        # The same helper, its list's clear registered as the module is imported,
        # and then in a function that registers no cleanup of its own.
        "_A: list = []\n"
        "unittest.addModuleCleanup(_A.clear)\n"
        "def helper():\n"
        "    if not _A:\n"
        "        tmp = tempfile.TemporaryDirectory()\n"
        "        unittest.addModuleCleanup(tmp.cleanup)\n"
        "        _A.append(Path(tmp.name))\n"
        "    return _A[0]\n": ({"_A"}, [(1, "_A")]),
        "_A: list = []\n"
        "def helper():\n"
        "    if not _A:\n"
        "        tmp = tempfile.TemporaryDirectory()\n"
        "        unittest.addModuleCleanup(tmp.cleanup)\n"
        "        _A.append(Path(tmp.name))\n"
        "    return _A[0]\n"
        "def forget():\n"
        "    unittest.addModuleCleanup(_A.clear)\n": ({"_A"}, [(1, "_A")]),
        # The folder made by setUpModule, and the caches filled by the tests, one
        # way each; the last cleared in tearDownModule, and a list nobody fills.
        "_B = []\n_C = {}\n_D = set()\n_E: dict = dict()\n_F = []\n_G = []\n"
        "_U = {}\n_KEPT = []\n_READ = []\n"
        "def setUpModule():\n"
        "    holder = tempfile.TemporaryDirectory()\n"
        "    addModuleCleanup(holder.cleanup)\n"
        "    _B[:] = found\n"
        "def tearDownModule():\n"
        "    _KEPT.clear()\n"
        "class Case:\n"
        "    def built(self, key):\n"
        "        _C.setdefault(key, []).append(key)\n"
        "        _D.add(key)\n"
        "        _E[key] = len(_READ)\n"
        "        _F.extend(key)\n"
        "        _G += [key]\n"
        "        _U.update(key=key)\n"
        "        _KEPT.insert(0, key)\n":
            ({"_B", "_C", "_D", "_E", "_F", "_G", "_U", "_KEPT"},
             [(1, "_B"), (2, "_C"), (3, "_D"), (4, "_E"), (5, "_F"), (6, "_G"),
              (7, "_U")]),
        # A folder tearDownModule removes, and the dict of paths into it kept.
        "_I: dict = {}\n"
        "def tearDownModule():\n"
        "    _HOME.cleanup()\n"
        "def project(name):\n"
        "    _I[name] = build(name)\n": ({"_I"}, [(1, "_I")]),
        # A module whose cleanups empty a list and remove nothing a cache could hold.
        "_H = []\n_J = []\n"
        "def setUpModule():\n"
        "    unittest.addModuleCleanup(_J.clear)\n"
        "def tearDownModule():\n"
        "    _J.clear()\n"
        "def remember(x):\n"
        "    _H.append(x)\n": (set(), []),
    }

    @staticmethod
    def caches(source: str) -> tuple[set[str], list[tuple[int, str]]]:
        """The containers *source* fills where a module cleanup can remove what they
        hold, and the line and name of each one no cleanup empties."""
        tree = ast.parse(source)
        bound = {}
        for node in tree.body:
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if isinstance(value, (ast.List, ast.Dict, ast.Set)) or (
                    isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id in {"list", "dict", "set"}):
                for target in (node.targets if isinstance(node, ast.Assign)
                               else [node.target]):
                    if isinstance(target, ast.Name):
                        bound[target.id] = node.lineno

        def named(node) -> str:
            return getattr(node, "attr", None) or getattr(node, "id", "")

        def cleans(scope) -> bool:
            # A module cleanup registered for anything but a container's clear.
            return any(isinstance(node, ast.Call) and named(node.func) == "addModuleCleanup"
                       and not (node.args and named(node.args[0]) == "clear")
                       for node in ast.walk(scope))

        def removes(scope) -> bool:
            # A call in tearDownModule to anything but a container's clear.
            return any(isinstance(node, ast.Call) and named(node.func) != "clear"
                       for node in ast.walk(scope))

        top = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        cleared = set()
        for node in (node for function in functions if cleans(function)
                     for node in ast.walk(function)):
            if (isinstance(node, ast.Call) and named(node.func) == "addModuleCleanup"
                    and node.args and isinstance(node.args[0], ast.Attribute)
                    and node.args[0].attr == "clear"
                    and isinstance(node.args[0].value, ast.Name)):
                cleared.add(node.args[0].value.id)
        for node in ast.walk(top.get("tearDownModule", ast.Module(body=[]))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "clear" and isinstance(node.func.value, ast.Name)):
                cleared.add(node.func.value.id)
        module_cleans = ("setUpModule" in top and cleans(top["setUpModule"])
                         or "tearDownModule" in top and removes(top["tearDownModule"]))

        found = set()
        for function in functions:
            if not (module_cleans or cleans(function)):
                continue
            for node in ast.walk(function):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"append", "extend", "insert", "add",
                                               "update", "setdefault"}
                        and isinstance(node.func.value, ast.Name)):
                    found.add(node.func.value.id)
                elif isinstance(node, ast.Assign):
                    # An item of it, or a slice; a name bound anew is not a fill.
                    found |= {target.value.id for target in node.targets
                              if isinstance(target, ast.Subscript)
                              and isinstance(target.value, ast.Name)}
                elif isinstance(node, ast.AugAssign):
                    target = node.target
                    target = target.value if isinstance(target, ast.Subscript) else target
                    if isinstance(target, ast.Name):
                        found.add(target.id)
        found &= set(bound)
        return found, sorted((bound[name], name) for name in found - cleared)

    def test_every_cache_a_module_cleanup_can_empty_is_emptied_with_it(self):
        held, uncleared = set(), []
        for path in sorted((ROOT / "tests").glob("*.py")):
            names, lines = self.caches(path.read_text(encoding="utf-8"))
            held |= {f"{path.stem}.{name}" for name in names}
            uncleared += [f"tests/{path.name}:{line} {name}" for line, name in lines]
        self.assertEqual(uncleared, [])
        self.assertLessEqual(
            {"test_scheduled._TEMPLATE", "test_cli._SETTLED", "test_fetch.ENTRIES",
             "test_demo._RUNS", "test_careers._BUILT", "test_careers._DOCUMENTS",
             "test_careers._ONE_PAGE", "test_anonymise._BUILT"}, held,
            "the scan found none of the caches it was written for")

    def test_the_scan_reads_each_way_of_filling_and_emptying_one(self):
        for number, (source, expected) in enumerate(self.PLANTED.items(), start=1):
            with self.subTest(planted=number):
                self.assertEqual(self.caches(source), expected)


class TestTheExampleConfigIsARealConfig(unittest.TestCase):
    """``config.example.toml`` is documentation, and documentation drifts.

    It is the one file a club reads to find out what a setting is called and what
    it may be set to, and nothing else in the suite reads it, so a preset renamed
    in `scoring.py` or a table the loader stopped accepting would leave a club
    copying lines that no longer work. Loading it is the cheapest way to hold the
    prose to the code.
    """

    PATH = ROOT / "config.example.toml"

    def load(self):
        from matchcentre import config as config_mod
        return config_mod.load(self.PATH)

    def test_the_example_a_club_copies_from_actually_loads(self):
        config = self.load()
        # A warning here is the loader saying it met a section or a key it does not
        # know: the example naming something that no longer exists.
        self.assertEqual(list(config.warnings), [])
        self.assertTrue(config.competitions, "the example documents "
                                             "[competitions.<id>] and the loader "
                                             "found none")

    def test_the_byes_table_it_documents_is_the_table_the_loader_reads(self):
        # Written by team, read by round. The example shows a whole grade resting
        # in one round, which is the case no scorecard can record and therefore the
        # reason the table exists at all.
        comp = self.load().competition("wednesday-division-3")
        self.assertIsNotNone(comp, "the example's worked competition is gone, so "
                                   "the rest of this class is testing nothing")
        self.assertTrue(comp.byes, "the example no longer shows a byes table")
        by_round = comp.byes_by_round()
        self.assertEqual(sorted(by_round), sorted({r for rounds in comp.byes.values()
                                                   for r in rounds}))
        self.assertTrue(all(name in comp.teams for name in comp.byes),
                        f"the byes table rests a team the example's own teams list "
                        f"does not have: {sorted(set(comp.byes) - set(comp.teams))}")

    def test_the_sheet_table_it_documents_is_the_table_the_loader_reads(self):
        # A live table, so a club copying it gets lines the loader keeps: one with
        # both figures and one with points only, which is the "only what you type is
        # checked" the comment above it promises. A key the example's own teams list
        # does not have would be a sheet-team warning on the first build.
        config = self.load()
        self.assertEqual(list(config.warnings), [])
        comp = config.competition("wednesday-division-3")
        self.assertIsNotNone(comp.expected, "the example no longer shows a sheet table")
        self.assertEqual(comp.expected.after_round, "5")
        self.assertTrue(comp.expected.lines)
        keys = [line.key for line in comp.expected.lines]
        self.assertEqual([key for key in keys if key not in comp.teams], [],
                         f"the sheet table names a team the example's own teams list "
                         f"does not have, of {keys}")
        self.assertIn(None, [line.played for line in comp.expected.lines])

    def test_every_section_the_loader_reads_is_shown_here(self):
        # The same drift as the [rules] test below, one level up and harder to
        # notice: a whole section added to the loader and never to this file is a
        # section whose only documentation is the source. [competitions] is checked
        # by its own header form, since it is written `[competitions.<id>]` and never
        # bare, and `seasons` may be `[seasons]` or a `[build]` key.
        from matchcentre import config as config_mod
        text = self.PATH.read_text(encoding="utf-8")
        missing = [name for name in config_mod.SECTIONS
                   if f"\n[{name}" not in text and f"\n# {name} =" not in text]
        self.assertEqual(missing, [], f"the loader reads {missing} and this file "
                                      f"never shows the section, so the only way "
                                      f"to learn it exists is to read the source")

    def test_the_two_people_it_tells_apart_are_in_its_own_team_list(self):
        from matchcentre.slugs import team_slug
        # [namesakes] is keyed on teams, and an example naming teams the rest of the
        # file has never heard of is one a club cannot read back against anything.
        config = self.load()
        self.assertTrue(config.namesakes, "the example no longer shows a namesake, "
                                         "so the section is undocumented")
        comp = config.competition("wednesday-division-3")
        declared = {team_slug(name) for name in comp.teams}
        for slug, teams in sorted(config.namesakes.items()):
            with self.subTest(player=slug):
                self.assertEqual(sorted(set(teams) - declared), [],
                                 f"{slug} is split across teams the example's own "
                                 f"teams list does not have")

    def test_a_name_it_splits_is_corrected_under_the_key_the_split_gives_it(self):
        # A [player_names] line under a split name is given to none of its players,
        # and the one place a club would copy the working form from is this file.
        # So the example corrects one of its own namesakes, keyed the way
        # split_namesake keys them, and loads without a warning about it: the first
        # test above holds the second half, this one the first.
        config = self.load()
        split = sorted(config.split_namesake(slug, team)
                       for slug, teams in config.namesakes.items() for team in teams)
        self.assertTrue(set(split) & set(config.player_names),
                        f"the example corrects none of {split}, so the only form "
                        f"of [player_names] that reaches a split name is shown "
                        f"nowhere a club would look")

    def test_every_preset_it_names_is_a_preset_that_exists(self):
        # Commented lines count. `# sets = "fast4"` is there to be uncommented, so a
        # set rule renamed in the code and not here is a line that stops the run the
        # first time a club takes the example at its word.
        from matchcentre import format as fmt_mod, scoring
        known = {"scoring": set(scoring.RULESETS),
                 "sets": set(fmt_mod.SET_RULES),
                 "unplayed": set(scoring.UNPLAYED_MODES),
                 "format": set(fmt_mod.FORMATS)}
        text = self.PATH.read_text(encoding="utf-8")
        found = 0
        for setting, value in re.findall(r'(?m)^\s*#?\s*([a-z_]+)\s*=\s*"([^"]+)"',
                                        text):
            if setting not in known:
                continue
            found += 1
            with self.subTest(setting=setting, value=value):
                self.assertIn(value, known[setting])
        self.assertGreaterEqual(found, 4, f"only {found} preset assignment(s) were "
                                          f"found to check, so this test has "
                                          f"stopped reading the file it is for")

    def block(self, header: str) -> str:
        """One table of the example, from its header to the next one."""
        text = self.PATH.read_text(encoding="utf-8")
        start = text.index(header)
        rest = text[start + len(header):]
        end = re.search(r"(?m)^\[", rest)
        return rest[:end.start()] if end else rest

    def test_every_rule_the_loader_honours_is_written_down_here(self):
        # The other direction from the test above, and the one that catches the
        # commoner drift: a setting added to the loader and never to the file a club
        # copies from is a setting nobody outside this repository can find out about.
        # Commented or not -- `# unplayed = "average"` documents it perfectly -- but
        # the name has to appear at the start of a line as an assignment, so a rule
        # merely mentioned in a paragraph does not count as documented.
        #
        # Looked for inside [rules] and not anywhere in the file: every one of these
        # names appears again in the per-competition override block below, so a
        # whole-file search called the setting documented while the section a club
        # actually edits had lost the line.
        from matchcentre import config as config_mod
        written = set(re.findall(r'(?m)^\s*#?\s*([a-z_]+)\s*=',
                                 self.block("\n[rules]\n")))
        missing = sorted(set(config_mod.RULES_SETTINGS) - written)
        self.assertEqual(missing, [], f"[rules] accepts {missing} and the example's "
                                      f"own [rules] block never shows the line, so "
                                      f"the only way to learn the setting exists is "
                                      f"to read the source")

    def test_it_shows_the_rules_a_competition_can_override(self):
        # The per-competition block is where a club with one division under other
        # rules looks, and it earns its keep only by being complete: a rule listed
        # under [rules] and left out down there reads as one that cannot be
        # overridden, which is not true of any of them.
        from matchcentre import config as config_mod
        text = self.PATH.read_text(encoding="utf-8")
        marker = "# A competition can override any of the [rules] settings"
        self.assertIn(marker, text, "the per-competition override block has been "
                                    "reworded, and this test can no longer find the "
                                    "part of the file it is about")
        block = text[text.index(marker):]
        block = block[:block.index("\n[")]
        shown = set(re.findall(r'(?m)^\s*#?\s*([a-z_]+)\s*=', block))
        missing = sorted(set(config_mod.RULES_SETTINGS) - shown)
        self.assertEqual(missing, [], f"the override block says 'any of the [rules] "
                                      f"settings' and then does not show {missing}")

    # Eighty, where the file this program *writes* is held to seventy-eight. The two
    # numbers are not a disagreement: the generated file's lines are also printed by
    # the run's review section, so they have to fit the report, while this file is only
    # ever opened in an editor, where eighty is the convention it was written to. What
    # matters is that it has a number at all -- seven lines had drifted past this one,
    # and a paragraph rewritten in place is exactly how the eighth would.
    MARGIN = 80

    def test_no_line_of_it_runs_past_the_margin_it_is_written_to(self):
        over = [(number, len(line)) for number, line
                in enumerate(self.PATH.read_text(encoding="utf-8").splitlines(), 1)
                if len(line) > self.MARGIN]
        self.assertEqual(over, [], f"config.example.toml is hand-wrapped to "
                                   f"{self.MARGIN} columns, and these (line, width) "
                                   f"pairs are past it: {over}")


class TestTheExampleSaysWhatNamesASeasonInEachCase(unittest.TestCase):
    """``config.example.toml`` says where a season's name comes from.

    Its ``[seasons]`` comment said "Any folder without an entry here is shown by its
    folder name", and measured in /tmp/t7/scratch/SEASON/before.txt only a section
    with no card yet was, by its key as the folder has it: three seasons whose cards
    print "Season 1" were headed ``Season 1``, and one whose cards print no season
    ``2019 Winter``, the key made readable. The comment gives the order the build
    asks, and quotes the last of it as the code makes it. It says too that a night
    on the front page is headed by its first competition's name, since a section
    with no card sorted first heads a night of carded divisions whose cards print
    another (/tmp/t7/scratch/SEASON/fix/mid.txt, ``2021 Winter - Tuesday`` over the
    cards' ``Season 1``). Rejected: sending the reader to the README, which a club
    editing this file has not got open.
    """

    PATH = ROOT / "config.example.toml"
    # Where each name comes from, in the order the build asks for one.
    ORDER = ("its own section's season =", "its season's line here",
             "the season its cards print", "its folder's name made readable",
             "no card has reached yet")
    NO_CARD = "A section no card has reached yet is named the same way"
    NIGHT = ("each night on the front page is headed by the name of its first "
             "competition.")

    def comment(self) -> str:
        """The ``[seasons]`` paragraphs as one line, from the banner to the table."""
        text = self.PATH.read_text(encoding="utf-8")
        start = text.index("# [seasons] —")
        end = text.index("\n[seasons]\n", start)
        return " ".join(line[1:].strip() for line in text[start:end].splitlines()
                        if not line.startswith("# ---"))

    def test_it_names_where_the_name_comes_from_in_the_order_it_is_asked(self):
        said = self.comment()
        at = [said.find(words) for words in self.ORDER]
        self.assertNotIn(-1, at, said)
        self.assertEqual(at, sorted(at), said)
        self.assertIn(self.NO_CARD, said)
        self.assertIn(self.NIGHT, said)

    def test_the_folder_name_it_quotes_is_the_one_the_page_shows(self):
        from matchcentre import export
        said = self.comment()
        self.assertIn(f'"{export.season_label("", "2026-winter")}" of 2026-winter',
                      said)
        self.assertNotIn("shown by its folder name", said)


class TestTheExampleAndTheReadmeNameTheTeamNameGuessesTheFirstRunNames(
        unittest.TestCase):
    """``config.example.toml`` and the README name the guesses the first run does.

    The note a first run writes over ``[team_names]`` names the two guesses most
    likely to need a human, an acronym of four letters or more read as a word and
    a letter A read as the word a. The example's ``[team_names]`` comment and the
    README's section on reviewing the file named neither (measured on the base,
    /tmp/mcf/scratch/CASE/before.txt), so a club reading either was told every
    offer was a guess and not which to read first. Each now quotes the same two,
    held to the writer's words here, and each of the three is held to the rule:
    every length it states is the cut's, each guess is said in the rule's terms,
    and each example reads as ``display_name`` gives it. Held to its examples
    alone, a note saying "five letters or more", or an A "kept as a team letter"
    beside its own lowered example, passed (/tmp/mutate626.py).
    Rejected: sending the example's reader to the README, which a club editing
    this file has not got open.
    """

    GUESS = re.compile(r'\b([A-Z]{2,}(?: [A-Z]+)*) is offered as ["`]([^"`]+)["`]')
    # A length stated, which is the cut's on its side: kept at three or fewer,
    # read as a word at four or more.
    CUT = re.compile(r"\b(\w+) letters or (fewer|more)\b", re.I)
    NUMBERS = ("no", "one", "two", "three", "four", "five", "six")
    # Each guess in the terms of the rule that makes it.
    ACRONYM = re.compile(r"\b[Aa]n acronym of \w+ letters or more is read as a word, "
                         r'so ([A-Z]+) is offered as ["`]([^"`]+)["`]')
    ARTICLE = re.compile(r'\bA before another word can be read as the word "a", '
                         r'so ([A-Z]+(?: [A-Z]+)+) is offered as ["`]([^"`]+)["`]')

    def said(self, text: str) -> str:
        return " ".join(text.replace("#", " ").split())

    def named(self, text: str) -> list[tuple[str, str]]:
        from matchcentre import slugs
        said = self.said(text)
        cuts = self.CUT.findall(said)
        self.assertTrue(cuts, f"no length stated in {said}")
        for number, side in cuts:
            self.assertEqual(number.casefold(), self.NUMBERS[
                slugs._ACRONYM_MAX + (side.casefold() == "more")], said)
        guesses = []
        for rule in (self.ACRONYM, self.ARTICLE):
            found = rule.findall(said)
            self.assertEqual(len(found), 1, f"{rule.pattern!r} in {said}")
            guesses.extend(found)
        self.assertEqual(self.GUESS.findall(said), guesses, said)
        for shouted, reading in guesses:
            self.assertEqual(slugs.display_name(shouted), reading, said)
        return guesses

    def written(self) -> list[tuple[str, str]]:
        from matchcentre import config as config_mod
        text = config_mod.render(config_mod.Config(path=Path("config.toml")))
        start = text.index("[team_names]\n")
        return self.named(text[start:text.index("\n[", start)])

    def readme(self) -> str:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        start = text.index("\n## Reviewing `config.toml`\n")
        return text[start:text.index("\n## ", start + 1)]

    def test_the_example_s_team_names_comment_names_the_same_two(self):
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        start = text.index("# [team_names] —")
        self.assertEqual(self.named(text[start:text.index("# [player_names] —")]),
                         self.written())

    def test_the_readme_s_section_on_reviewing_the_file_names_the_same_two(self):
        self.assertEqual(self.named(self.readme()), self.written())

    def test_the_readme_says_the_cut_keeps_a_word_of_three_letters_too(self):
        """The README says the cut's cost the other way, in words the rule keeps.

        A shouted word of three letters is kept as an abbreviation is, so OLD TOWN
        ACE is offered as ``OLD Town ACE`` and NET SETTERS as ``NET Setters``
        (/tmp/mcf/scratch/CASE/three.txt). Said only as "four are not, because ACES
        and NETS are words", the cut read as though no word had three letters. It
        is not one of the two guesses the note names, since it shows in capitals in
        an offer where those two read as names. Rejected: a cut of two, which makes
        RTC ``Rtc``; and title-casing a short word that holds a vowel, which makes
        an abbreviation such as ABC ``Abc``.
        """
        from matchcentre import slugs
        kept = re.findall(r"\bso is a word of (\w+) letters such as ([A-Z]+) or "
                          r"([A-Z]+)\b", self.said(self.readme()))
        self.assertEqual(len(kept), 1, kept)
        number, *words = kept[0]
        self.assertEqual(number, self.NUMBERS[slugs._ACRONYM_MAX])
        for word in words:
            with self.subTest(word):
                self.assertEqual(slugs.display_name(word), word)


class TestALeakFromADashboardWithoutNamesHasSomewhereToGo(unittest.TestCase):
    """A name on a dashboard built without names is reported privately, like a redact leak.

    The README's section on withholding names promises that no file under the
    output folder holds a name, and a promise with no route for the day it breaks
    sends the reporter to the public tracker with the name in hand. Rejected: a
    line in the README alone, which a reporter of a security bug does not read.
    """

    def test_the_policy_names_the_setting_and_the_readme_sends_readers_to_it(self):
        policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("`[build] players`", policy)
        self.assertIn("`--players`", policy)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("\n## Publishing without names\n"):]
        section = section[:section.index("\n## ", 1)]
        self.assertIn("[SECURITY.md](SECURITY.md)", section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
