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
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# What a club downloads. Each entry is a promise made by the README or by another
# module that reads it, so a missing one is a broken promise rather than an
# untidy tree.
REQUIRED = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
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
    "matchcentre/cardwriter.py",
    "matchcentre/web/index.html",
    "matchcentre/web/app.js",
    "matchcentre/web/styles.css",
    "matchcentre/web/picker.html",
    "tools/demo.py",
    "tools/doctor.py",
    "tools/screenshots.py",
    # The suite ships too, and the README tells a club how to run it, so a copy
    # that arrives without it has arrived incomplete.
    "tests/jslint.py",
    "tests/test_foundation.py",
    "tests/test_parsing.py",
    "tests/test_pipeline.py",
    "tests/test_web_assets.py",
    "tests/test_cli.py",
    "tests/test_doctor.py",
    "tests/test_hostile_input.py",
    "tests/test_project_complete.py",
)

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


class TestItRunsOnAnyMachine(unittest.TestCase):

    def sources(self) -> list[Path]:
        found = sorted((ROOT / "matchcentre").rglob("*.py"))
        found += sorted((ROOT / "tools").glob("*.py")) if (ROOT / "tools").is_dir() else []
        found += [ROOT / "build.py"]
        return [p for p in found if p.is_file() and "__pycache__" not in p.parts]

    def test_nothing_imports_anything_that_is_not_in_the_standard_library(self):
        # The whole promise is "no pip install". A club has an airgapped laptop or
        # a locked-down work machine; one third-party import makes the tool
        # unusable there, and the failure arrives as a traceback on first run.
        local = {p.stem for p in (ROOT / "matchcentre").glob("*.py")}
        local |= {"matchcentre", "jslint"}
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
            if set(path.relative_to(ROOT).parts) & {"notes", ".git",
                                                    "__pycache__", "site",
                                                    "data"}:
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
        # `matchcentre-dashboard 0.1.0 (python 3.12.3 on linux)` is written out by
        # hand in the issue forms, because a form has nothing to interpolate. This
        # is what makes bumping the version notice them.
        stale = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.suffix not in SHIPPED_SUFFIXES:
                continue
            if set(path.relative_to(ROOT).parts) & {"notes", ".git", "__pycache__",
                                                    "site", "data"}:
                continue
            if path.name == "CHANGELOG.md":     # a history of every version
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8")
                                          .splitlines(), start=1):
                for found in re.findall(r"matchcentre-dashboard (\d+\.\d+\.\d+)",
                                        line):
                    if found != self.version():
                        stale.append(f"{path.relative_to(ROOT)}:{number}: "
                                     f"{found}")
        self.assertEqual(stale, [], f"{len(stale)} place(s) quote a version that is "
                                    f"not {self.version()}: {stale}")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
