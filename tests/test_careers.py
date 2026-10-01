"""Careers across seasons: one static page at the site root, in multi-season mode.

The rule the page exists under: a career figure is a sum of per-season figures a
reader can still open and check, and never a number with no season behind it. So
every figure here is asserted against the season document it was copied from --
in memory for the rules, and off disk for the build -- and every total against
the rows above it.

Before this page existed a multi-season site had one player page per season and
nothing that put them together; the README said career totals were "not built
yet". Measured on the two-season fixture below (/tmp/t4/scratch/y87/before): no
``careers.html``, no link to one on the front page, none in ``app.js``.

The fixture is two seasons of the four invented clubs of ``test_pipeline``, with
one roster line changed in the second: Parkview's Kit Vasse is replaced by Robin
Quaife. That gives 13 people named, 9 who played, 7 who played in both seasons,
four who are only ever on a roster, and one each who played in one season only.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from itertools import permutations
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build as build_mod                                            # noqa: E402
from matchcentre import cardwriter as cw                             # noqa: E402
from matchcentre import careers                                      # noqa: E402
from matchcentre import competition as competition_mod               # noqa: E402
from matchcentre import config as config_mod                         # noqa: E402
from matchcentre import discover as discover_mod                     # noqa: E402
from matchcentre import export, scorecard, site, stats               # noqa: E402
from matchcentre import slugs as slugs_mod                          # noqa: E402

from test_cli import BUILD, LATER_NIGHT, STAMP, Run, readme_block    # noqa: E402
from test_pipeline import (DATES, FIXTURES, ROUND_FOUR, TEAMS,       # noqa: E402
                           SeasonCase, Swap, rows_for, squad_for,
                           write_two_grade_cards)

MULTI = '[build]\nseasons = "multi"\n'

# The figures a career adds up, spelt out here rather than read from the module
# under test, so that a key dropped from `careers.SUMMED` is a failure and not a
# fixture that quietly stopped asking about it.
FIGURES = ("matches", "sets_won", "sets_lost", "games_won", "games_lost",
           "singles.won", "singles.lost", "doubles.won", "doubles.lost")

LAST_DATES = {"1": "2025-08-19", "2": "2025-08-26", "3": "2025-09-02"}
SEASON_DATES = {"2025-winter": LAST_DATES, "2026-winter": DATES}
# Position 1 plays every week, so the swap is a person with results behind them:
# Kit Vasse played 2025 only, Robin Quaife 2026 only.
SWAPS = {"2026-winter": {"parkview-wanderers": Swap(1, "Robin Quaife")}}
BOTH = ("2025-winter", "2026-winter")
COMP = {key: f"{key}-tuesday-division-b" for key in BOTH}
CAREERS_LINE = "  careers      7 of 9 player(s) played in more than one season"

SCRATCH = Path()                     # made by setUpModule, for these tests alone


def setUpModule():
    # Made here and not on import: test_demo imports this file for its page
    # reader, and a folder made on import is made by every process that imports
    # it. Unittest sets a module up again after another module's class has run
    # between two of its own, so the builds cached in the last folder go with it.
    global SCRATCH
    holder = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(holder.cleanup)
    unittest.addModuleCleanup(_BUILT.clear)
    unittest.addModuleCleanup(_DOCUMENTS.clear)
    unittest.addModuleCleanup(_ONE_PAGE.clear)
    SCRATCH = Path(holder.name)


# --- the fixture ------------------------------------------------------------


def season_cards(key: str) -> Path:
    """One season's six cards, written once per run and copied into each project."""
    folder = SCRATCH / "cards" / key
    if folder.is_dir():
        return folder
    rename = SWAPS.get(key)
    for index, (rnd, home, away, scores) in enumerate(FIXTURES, start=1):
        rounds = folder / f"Round{rnd}"
        rounds.mkdir(parents=True, exist_ok=True)
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on = squad_for(home, home_players, rename)
        away_rows, away_on = squad_for(away, away_players, rename)
        cw.write_card_file(rounds / f"{home}-{away}.pdf", cw.CardSpec(
            date=SEASON_DATES[key][rnd], round_label=rnd, division="B",
            court=f"{index:02d}", match_id=f"{key[2:4]}{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_on, away_on),
            home_roster=home_rows, away_roster=away_rows))
    return folder


def write_round_four(data: Path) -> None:
    """Two more cards in 2026, as a club would file the next night's."""
    folder = data / "2026-winter" / "Round4"
    folder.mkdir(parents=True, exist_ok=True)
    rename = SWAPS["2026-winter"]
    for index, (home, away, scores) in enumerate(ROUND_FOUR, start=7):
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on = squad_for(home, home_players, rename)
        away_rows, away_on = squad_for(away, away_players, rename)
        cw.write_card_file(folder / f"{home}-{away}.pdf", cw.CardSpec(
            date="2026-09-08", round_label="4", division="B",
            court=f"{index:02d}", match_id=f"26{index:04d}",
            home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_on, away_on),
            home_roster=home_rows, away_roster=away_rows))


def project(*keys: str, multi: bool = True, config: str = "") -> Path:
    """A throwaway project directory holding ``data/<season>/`` and a config."""
    cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
    (cwd / "data").mkdir()
    for key in keys:
        shutil.copytree(season_cards(key), cwd / "data" / key)
    text = (MULTI if multi else "") + config
    if text:
        (cwd / "config.toml").write_text(text, encoding="utf-8")
    return cwd


def run_build(cwd: Path, *argv: str, generated: str = STAMP) -> Run:
    done = subprocess.run((sys.executable, str(BUILD), "--generated", generated,
                           *argv), cwd=cwd, capture_output=True, text=True,
                          timeout=600)
    return Run(done.returncode, done.stdout, done.stderr, cwd)


def main_in_process(cwd: Path, *argv: str, generated: str = STAMP):
    """``build.main`` in this process, so a patch reaches every caller it has."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = build_mod.main(["--data", str(cwd / "data"),
                               "--out", str(cwd / "site"),
                               "--config", str(cwd / "config.toml"),
                               "--generated", generated, *argv])
    return code, out.getvalue() + err.getvalue()


_BUILT: dict = {}


def built_two_seasons() -> tuple[Path, Run]:
    """The two-season multi build, made once and only ever read."""
    if "two" not in _BUILT:
        cwd = project(*BOTH)
        _BUILT["two"] = (cwd, run_build(cwd))
    return _BUILT["two"]


_DOCUMENTS: dict = {}


def documents(*keys: str, multi: bool = True):
    """The season documents build.py would hand ``write_site``, built in process."""
    if (keys, multi) not in _DOCUMENTS:
        root = Path(tempfile.mkdtemp(dir=SCRATCH))
        data = root / "data"
        for key in keys:
            shutil.copytree(season_cards(key), data / key)
        cfg = config_mod.Config(path=root / "config.toml",
                                seasons_mode="multi" if multi else "single")
        cards, rejected = scorecard.read_tree(data, expect=cfg.declared_format())
        found = discover_mod.discover(cards, rejected, config=cfg, root=data)
        seasons = [export.build_season(
            found.matches.get(comp.id, []), competition=comp,
            source_dir=str(data), generated=STAMP, club=found.club,
            cards=found.cards.get(comp.id, []), rejected=found.rejected,
            seasons_mode=cfg.seasons_mode)
            for comp in sorted(found.club.competitions, key=lambda c: c.id)]
        _DOCUMENTS[(keys, multi)] = (seasons, found.club, cfg)
    seasons, club, cfg = _DOCUMENTS[(keys, multi)]
    return copy.deepcopy(seasons), club, cfg


def write(out: Path, *keys: str, multi: bool = True,
          single_file: bool = False) -> site.SiteWrite:
    seasons, club, cfg = documents(*keys, multi=multi)
    return site.write_site(out, seasons=seasons, club=club, config=cfg,
                           generated=STAMP, single_file=single_file)


def collapsed(text: str) -> str:
    return " ".join(text.split())


def section(text: str, head: str) -> list[str]:
    """The lines of one report section, from its ``== head ==`` bar to the next."""
    lines = text.splitlines()
    start = next(at for at, line in enumerate(lines)
                 if line.startswith(f"== {head} "))
    body = []
    for line in lines[start + 1:]:
        if line.startswith("== "):
            break
        body.append(line)
    return body


# The module in a process of its own: set up, its caches filled, torn down the way
# unittest tears a module down, and set up again. The folder is looked for before
# the second setup, which would let go of one the teardown had left behind.
TEARDOWN_PROBE = """
import json, unittest
import test_careers as module
def up():
    getattr(module, "setUpModule", lambda: None)()
def down():
    getattr(module, "tearDownModule", lambda: None)()
    unittest.case.doModuleCleanups()
up()
first = module.SCRATCH
made = first.is_dir()
module._BUILT["two"] = (first / "built", None)
module._DOCUMENTS[(("2025-winter",), True)] = ([], None, None)
module._ONE_PAGE["size"] = 1
down()
removed = not first.exists()
left = [len(module._BUILT), len(module._DOCUMENTS), len(module._ONE_PAGE)]
up()
print(json.dumps({"made": made, "removed": removed, "left": left,
                  "again": module.SCRATCH.is_dir() and module.SCRATCH != first}))
down()
"""


class TestTheModuleIsSetUpAgainWithAFolderOfItsOwnAndNothingCachedInTheLast(
        unittest.TestCase):
    """Each time unittest sets this module up it gets a new folder and empty caches.

    Unittest tears a module down when the next test is another module's, and sets
    it up again if one of its classes follows, as a command naming classes does.
    Before, the folder was made on import and removed by ``tearDownModule``, and the
    builds cached in it were kept. Measured on the base
    (/tmp/t7/scratch/CA2/probe/before-careers-order.log): this module's class that
    only ever writes the page in multi-season mode, then test_foundation's
    TestSlugs, then this module's page-without-JavaScript class gave 4 errors in 25
    tests, each a FileNotFoundError from ``mkdtemp(dir=SCRATCH)``; and with the
    report's class first and the figures class after TestSlugs
    (before-careers-order2.log), 5 in 23, the fifth the figures class's setUpClass
    reading ``careers.html`` in a build the teardown had removed. Importing the
    module made a folder too, in every process that imports test_demo, which
    imports this file for its page reader: the base's, imported, put a folder
    in an empty TMPDIR (/tmp/t7/scratch/CA2/red-204.log).

    Now it is made in ``setUpModule`` and removed, with the three caches of builds
    made in it emptied, by module cleanups, as test_demo's is. Rejected: one
    folder for the whole process, made on import and left to the interpreter's
    exit, which keeps the caches good but is still a folder made by every process
    that imports the page reader.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=SCRATCH))

    def python(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            (sys.executable, *argv), cwd=ROOT / "tests", capture_output=True,
            text=True, timeout=600,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(self.tmp)))

    def test_a_class_run_after_another_modules_finds_its_folder_and_its_builds(self):
        done = self.python(
            "-m", "unittest", "test_careers.TestTheReportSaysHowManyCareersSpanSeasons",
            "test_foundation.TestSlugs",
            "test_careers.TestTheCareersFiguresAreTheSeasonFilesAddedUp",
            "test_careers.TestTheCareersPageIsAPageWithoutJavaScript")
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        self.assertRegex(done.stderr, r"\nRan [1-9]\d* tests in [^\n]*\n\nOK\n")

    def test_the_teardown_removes_the_folder_and_forgets_the_builds_made_in_it(self):
        done = self.python("-c", TEARDOWN_PROBE)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        self.assertEqual(json.loads(done.stdout),
                         {"made": True, "removed": True, "left": [0, 0, 0],
                          "again": True})

    def test_importing_the_module_makes_no_folder(self):
        done = self.python("-c", "import os, test_careers\n"
                                 "print(sorted(os.listdir(os.environ['TMPDIR'])))")
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        self.assertEqual(done.stdout.strip(), "[]")


# --- reading the page back --------------------------------------------------


class CareersPage(HTMLParser):
    """The careers page as a reader meets it: links, anchors, and the tables' rows.

    Each table is filed under the section it sits in -- ``"index"`` for the list at
    the top, a player's slug for theirs -- and each row keeps its cells in order,
    with the text, the ``<small>`` note, the link and the attributes apart.
    """

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.ids: list[str] = []
        self.headings: dict[str, str] = {}
        self.captions: list[str] = []
        # Each table's caption as its spans part it, filed as the tables are:
        # [(class, text), ...].
        self.caption_parts: dict[str, list[tuple[str, str]]] = {}
        self._span = None
        self.paragraphs: list[tuple[str, str]] = []
        self.tables: dict[str, dict[str, list]] = {}
        self.body_class = None
        self._section = "index"
        self._part = None
        self._row = None
        self._cell = None
        self._small = False
        self._capture: list | None = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
            if self._cell is not None:
                self._cell["href"] = attrs["href"]
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "body":
            self.body_class = attrs.get("class")
        if tag == "section" and "career" in attrs.get("class", "").split():
            self._section = attrs["id"][len(careers.ANCHOR_PREFIX):]
        elif tag == "table":
            self.tables.setdefault(self._section,
                                   {"thead": [], "tbody": [], "tfoot": []})
        elif tag in ("thead", "tbody", "tfoot"):
            self._part = tag
        elif tag == "tr":
            self._row = {"attrs": attrs, "cells": []}
        elif tag in ("th", "td") and self._row is not None:
            self._cell = {"tag": tag, "attrs": attrs, "text": "", "small": "",
                          "href": None}
        elif tag == "small":
            self._small = True
        elif tag in ("p", "h2", "caption"):
            self._capture = [tag, attrs, ""]
            if tag == "caption":
                self.caption_parts.setdefault(self._section, [])
        elif tag == "span" and self._capture and self._capture[0] == "caption":
            self._span = [attrs.get("class", ""), ""]

    def handle_endtag(self, tag):
        if tag == "span" and self._span is not None:
            kind, text = self._span
            self.caption_parts[self._section].append((kind, " ".join(text.split())))
            self._span = None
        elif tag in ("th", "td") and self._cell is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            self._cell["small"] = " ".join(self._cell["small"].split())
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[self._section][self._part].append(self._row)
            self._row = None
        elif tag == "small":
            self._small = False
        elif tag == "section":
            self._section = None
        elif self._capture and tag == self._capture[0]:
            kind, attrs, text = self._capture
            text = " ".join(text.split())
            if kind == "p":
                self.paragraphs.append((attrs.get("class", ""), text))
            elif kind == "h2":
                self.headings[attrs.get("id", "")] = text
            else:
                self.captions.append(text)
            self._capture = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["small" if self._small else "text"] += data
        if self._capture is not None:
            self._capture[2] += data
        if self._span is not None:
            self._span[1] += data

    def rows(self, slug: str, part: str = "tbody") -> list[dict]:
        return self.tables[slug][part]

    def total(self, slug: str) -> dict:
        row, = self.tables[slug]["tfoot"]
        return row


def fields(row: dict) -> dict[str, str]:
    """A row's figures by the key its cells carry."""
    return {cell["attrs"]["data-field"]: cell["text"] for cell in row["cells"]
            if "data-field" in cell["attrs"]}


def cell(row: dict, key: str) -> dict:
    return next(c for c in row["cells"] if c["attrs"].get("data-field") == key)


def read_page(out: Path) -> CareersPage:
    return CareersPage((out / site.CAREERS_PAGE).read_text(encoding="utf-8"))


# --- hand-built documents, for the rules ------------------------------------


def player(slug: str, name: str | None = None, figures=None, *,
           team: str | None = "lakeside-spinners",
           team_name: str | None = "Lakeside Spinners",
           rating=(2.5, 2.5)) -> dict:
    """One ``players`` entry of a season document, as ``player_stats`` shapes it."""
    figures = dict(figures or {})
    entry = {"slug": slug, "name": name or slug.replace("-", " ").title(),
             "team": team, "team_code": "", "team_name": team_name,
             "is_captain": False,
             "rating": {"singles": rating[0], "doubles": rating[1]},
             "sets_played": 0, "games_diff": 0, "win_pct": 0.0}
    for key in FIGURES:
        head, _, tail = key.partition(".")
        if tail:
            entry.setdefault(head, {"played": 0, "games_won": 0,
                                    "games_lost": 0})[tail] = figures.get(key, 0)
        else:
            entry[key] = figures.get(key, 0)
    return entry


def document(season_key: str, *players: dict, competition: str | None = None,
             label: str = "Tuesday C", season_label: str | None = None) -> dict:
    comp = competition or (f"{season_key}-tuesday-c" if season_key
                           else "tuesday-c")
    shown = (season_label if season_label is not None
             else season_key.replace("-", " ").title())
    return {"meta": {"competition": comp, "label": label,
                     "season_key": season_key, "season_label": shown,
                     "generated": STAMP, "seasons_mode": "multi"},
            "players": list(players)}


def played(matches: int, won: int, lost: int, **more) -> dict:
    figures = {"matches": matches, "sets_won": won, "sets_lost": lost}
    figures.update(more)
    return figures


def render(documents_, href=None) -> CareersPage:
    href = href or (lambda row, slug: f"{row.competition}/index.html#/player/{slug}")
    return CareersPage(careers.body_html(careers.build(documents_), href=href))


# --- the rules --------------------------------------------------------------


class TestACareerTotalIsTheSumOfTheRowsAboveIt(unittest.TestCase):
    """Every total is the plain sum of the rows printed above it.

    Three seasons whose figures are all distinct primes, so a row dropped, doubled
    or swapped for another changes every total it touches, and a figure read from
    the wrong field of a row -- sets won for games won -- prints a number that is
    not the document's. The rejected alternative is a total worked out from
    anything but the rows: a second route to a number is a number that can
    disagree with the page it sits under.
    """

    PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59,
              61, 67, 71, 73, 79, 83, 89, 97, 101, 103)

    def setUp(self):
        primes = iter(self.PRIMES)
        self.figures = [{key: next(primes) for key in FIGURES} for _ in range(3)]
        self.docs = [document(key, player("bo-nakamura", "Bo Nakamura", figures))
                     for key, figures in zip(("2024-winter", "2025-winter",
                                              "2026-winter"), self.figures)]

    def test_every_summed_figure_is_the_sum_of_the_seasons(self):
        career, = careers.build(self.docs)
        for key in FIGURES:
            self.assertEqual(career.total[key],
                             sum(figures[key] for figures in self.figures), key)

    def test_the_total_row_prints_those_sums(self):
        page = render(self.docs)
        shown = fields(page.total("bo-nakamura"))
        for key in FIGURES:
            self.assertEqual(shown[key],
                             str(sum(figures[key] for figures in self.figures)),
                             key)

    def test_each_row_prints_its_own_documents_figures(self):
        page = render(self.docs)
        rows = page.rows("bo-nakamura")
        self.assertEqual(len(rows), 3)
        for row, figures in zip(rows, self.figures):
            shown = fields(row)
            for key in FIGURES:
                self.assertEqual(shown[key], str(figures[key]), key)


class TestTheWinRateIsWorkedOutFromTheSetsAndNotAveraged(SeasonCase):
    """A career's win rate is its added-up sets, and a dash when none was decided.

    1 of 1 and 1 of 4 is 2 of 5, which is 40.0%; the mean of the two seasons'
    rates is 62.5%, a figure no set behind it supports. A player with no decided
    set has no win rate, and the careers page says so with a dash. The season
    document says the same with null (4.43), so the page and the document agree.
    """

    def test_the_career_rate_is_the_pooled_sets(self):
        docs = [document("2025-winter", player("bo-nakamura", figures=played(1, 1, 0))),
                document("2026-winter", player("bo-nakamura", figures=played(2, 1, 3)))]
        shown = cell(render(docs).total("bo-nakamura"), "win_pct")["text"]
        self.assertEqual(shown, "40.0%")
        self.assertEqual(shown, careers.win_rate_text(2, 3))

    def test_no_set_decided_is_no_win_rate(self):
        self.assertIsNone(stats.set_win_pct(0, 0))
        self.assertIsNone(careers.win_rate_text(0, 0))

    def test_a_row_and_a_total_with_no_decided_set_print_a_muted_dash(self):
        docs = [document("2025-winter", player("cass-ellery")),
                document("2026-winter", player("cass-ellery"))]
        page = render(docs)
        for row in (*page.rows("cass-ellery"), page.total("cass-ellery")):
            shown = cell(row, "win_pct")
            self.assertEqual(shown["text"], "—")
            self.assertIn("muted", shown["attrs"]["class"].split())

    def test_a_player_who_won_none_of_the_sets_decided_is_nought_and_not_a_dash(self):
        """Losing every set decided is a win rate of 0.0%; the dash means none decided.

        Measured (/tmp/t4/scratch/y87/fix2/gaps_probe.before.txt): with
        ``set_win_pct`` returning None for a player who won no set, every test
        of this file passed, and the careers page would have printed a dash beside
        three sets lost, which says no set was decided.
        """
        docs = [document("2025-winter", player("bo-nakamura", figures=played(1, 0, 1))),
                document("2026-winter", player("bo-nakamura", figures=played(1, 0, 2)))]
        page = render(docs)
        for row in (*page.rows("bo-nakamura"), page.total("bo-nakamura")):
            shown = cell(row, "win_pct")
            self.assertEqual(shown["text"], "0.0%")
            self.assertNotIn("muted", shown["attrs"]["class"].split())
        self.assertEqual(stats.set_win_pct(0, 3), 0.0)

    def test_the_season_document_publishes_no_rate_for_no_decided_set(self):
        """4.43's null, beside the dash: 4.60 was written when the document said 0.0."""
        by_slug = {p["slug"]: p for p in self.season["players"]}
        self.assertEqual(by_slug["cass-ellery"]["sets_won"]
                         + by_slug["cass-ellery"]["sets_lost"], 0)
        self.assertIsNone(by_slug["cass-ellery"]["win_pct"])

    def test_the_season_figure_comes_from_the_one_rule(self):
        with mock.patch.object(stats, "set_win_pct", return_value=12.3):
            computed = stats.player_stats(self.matches, self.competition)
        self.assertTrue(computed)
        self.assertEqual({p.win_pct for p in computed.values()}, {12.3})


class TestARatingIsShownPerSeasonAndNeverAddedUp(unittest.TestCase):
    """Each row shows the rating printed that season; the total says "not added".

    A rating is the association's figure for one season, and a sum or an average of
    two of them is a number nobody publishes. Every field of ``PlayerStats`` is
    named in exactly one of the module's lists, so a field added to the season
    document later fails here until somebody decides whether a career adds it up.
    """

    def setUp(self):
        self.docs = [document("2025-winter", player("bo-nakamura", figures=played(1, 1, 0),
                                                   rating=(4.2, 3.95))),
                     document("2026-winter", player("bo-nakamura", figures=played(1, 0, 1),
                                                   rating=(None, 3.5)))]

    def test_each_row_carries_its_own_seasons_rating(self):
        rows = render(self.docs).rows("bo-nakamura")
        self.assertEqual([row["cells"][-1]["text"] for row in rows],
                         ["4.20 / 3.95", "— / 3.50"])

    def test_the_total_has_no_rating_and_says_so(self):
        career, = careers.build(self.docs)
        self.assertNotIn("rating", career.total)
        total = render(self.docs).total("bo-nakamura")
        self.assertEqual(total["cells"][-1]["text"], "not added")
        self.assertIn("muted", total["cells"][-1]["attrs"].get("class", ""))

    def test_every_player_field_is_decided_once(self):
        lists = {"SUMMED": careers.SUMMED, "RECOMPUTED": careers.RECOMPUTED,
                 "NOT_SUMMED": careers.NOT_SUMMED, "NOT_SHOWN": careers.NOT_SHOWN,
                 "IDENTITY": careers.IDENTITY}
        entries = [entry for names in lists.values() for entry in names]
        self.assertEqual(len(entries), len(set(entries)), "an entry named twice")
        whole = {entry for entry in entries if "." not in entry}
        dotted = {entry.partition(".")[0] for entry in entries if "." in entry}
        record = {f.name for f in dataclasses.fields(stats.DisciplineRecord)}
        for field in dataclasses.fields(stats.PlayerStats):
            with self.subTest(field=field.name):
                self.assertNotEqual(field.name in whole, field.name in dotted,
                                    "named neither whole nor by part, or both")
                if field.name in dotted:
                    self.assertEqual({entry.partition(".")[2] for entry in entries
                                      if entry.partition(".")[0] == field.name},
                                     record)
        names = {f.name for f in dataclasses.fields(stats.PlayerStats)}
        self.assertEqual(whole | dotted, names)


class TestEveryFigureTheCareersPageAddsIsAFieldTheSeasonDocumentHas(SeasonCase):
    """Each summed figure is read off the document, and a missing one stops the build.

    A document that does not carry ``sets_won`` is not a document that says nought,
    and a career built from a zero there would be a total with a season behind it
    that says nothing of the kind. The rejected alternative, ``player.get(key,
    0)``, is how a renamed field would turn every career into the sum of zeros.
    """

    def test_every_summed_key_reads_an_integer_off_a_real_document(self):
        comp = self.season["meta"]["competition"]
        for entry in self.season["players"]:
            for key in careers.SUMMED:
                value = careers.figure(entry, key, competition=comp)
                self.assertIs(type(value), int, (entry["slug"], key))

    def assert_refused(self, change):
        broken = copy.deepcopy(self.season)
        entry = next(p for p in broken["players"] if p["slug"] == "ana-fernandez")
        change(entry)
        with self.assertRaises(careers.CareersError) as caught:
            careers.build([broken])
        message = str(caught.exception)
        for part in (self.comp_id, "ana-fernandez", "sets_won"):
            self.assertIn(part, message)

    def test_a_missing_figure_is_refused_by_name(self):
        self.assert_refused(lambda entry: entry.pop("sets_won"))

    def test_a_figure_that_is_not_a_count_is_refused(self):
        self.assert_refused(lambda entry: entry.update(sets_won=True))
        self.assert_refused(lambda entry: entry.update(sets_won="3"))


class TestAPlayerIsJoinedOnTheSlugTheSeasonPagesUse(unittest.TestCase):
    """One slug is one career, across grades and seasons; nothing else joins two.

    The slug is what the season pages already use, after ``[aliases]`` and
    ``[namesakes]``. A career keyed on the printed name would undo a namesake split,
    and one that matched near-misses would merge two people on no evidence at all.
    """

    def slugs(self, *docs):
        return [career.slug for career in careers.build(docs)]

    def test_one_slug_in_two_grades_is_one_career(self):
        docs = [document("2026-winter", player("ada-renn", figures=played(2, 2, 1)),
                         competition="2026-winter-grade-a"),
                document("2026-winter", player("ada-renn", figures=played(1, 0, 2)),
                         competition="2026-winter-grade-b")]
        career, = careers.build(docs)
        self.assertEqual(len(career.rows), 2)

    def test_a_namesake_split_stays_two_careers(self):
        docs = [document("2025-winter",
                         player("bo-nakamura", "Bo Nakamura", played(1, 1, 0),
                                team="riverside-rockets",
                                team_name="Riverside Rockets"),
                         player("bo-nakamura-lakeside-spinners", "Bo Nakamura",
                                played(1, 0, 1)))]
        self.assertEqual(self.slugs(*docs),
                         ["bo-nakamura", "bo-nakamura-lakeside-spinners"])

    def test_two_spellings_nobody_joined_are_two_careers(self):
        docs = [document("2025-winter", player("jo-calder", figures=played(1, 1, 0))),
                document("2026-winter", player("jo-caldor", figures=played(1, 1, 0)))]
        self.assertEqual(self.slugs(*docs), ["jo-calder", "jo-caldor"])


class TestTwoGradesInOneSeasonAreTwoRowsAndOneSeason(unittest.TestCase):
    """A fill-in for another grade is a row of its own, summed, in the same season.

    Each row is one published document, and a match lives in exactly one of them,
    so adding both rows counts nothing twice. Merging a season's rows first would
    print a figure no single document holds.
    """

    def setUp(self):
        self.docs = [
            document("2026-winter", player("hana-wex", figures=played(3, 5, 2)),
                     competition="2026-winter-tuesday-c"),
            document("2026-winter", player("hana-wex", figures=played(1, 1, 1),
                                           team=None, team_name=None),
                     competition="2026-winter-tuesday-b", label="Tuesday B")]

    def test_both_rows_are_summed_and_count_as_one_season(self):
        career, = careers.build(self.docs)
        self.assertEqual(len(career.rows), 2)
        self.assertEqual(career.seasons_played, 1)
        self.assertEqual((career.total["matches"], career.total["sets_won"]), (4, 6))

    def test_the_fill_in_row_says_it_was_not_on_a_roster(self):
        rows = render(self.docs).rows("hana-wex")
        self.assertIn("(not on a roster)", [row["cells"][2]["text"] for row in rows])


class TestSomebodyOnARosterWhoNeverPlayedIsNotASeasonPlayed(unittest.TestCase):
    """Being on a roster is not playing: the row is shown, muted, and not counted.

    The zero row is still exactly what that season's page shows, and every season
    page links its players here, so it keeps an anchor; hiding it would make those
    links land nowhere.
    """

    def setUp(self):
        self.docs = [document("2025-winter", player("ilya-modric"),
                              player("lena-ordoy")),
                     document("2026-winter",
                              player("ilya-modric", figures=played(2, 3, 1)),
                              player("lena-ordoy"))]

    def test_a_zero_row_changes_no_total_and_no_season_count(self):
        ilya = next(c for c in careers.build(self.docs) if c.slug == "ilya-modric")
        self.assertEqual(len(ilya.rows), 2)
        self.assertEqual(ilya.seasons_played, 1)
        self.assertEqual(ilya.total["sets_won"], 3)

    def test_the_zero_row_is_muted_and_says_why(self):
        first, second = render(self.docs).rows("ilya-modric")
        self.assertIn("muted", first["attrs"].get("class", "").split())
        self.assertTrue(first["cells"][1]["text"].endswith(
            " (on the roster, no sets played)"))
        self.assertNotIn("muted", second["attrs"].get("class", "").split())

    def test_nobody_who_never_played_goes_without_an_anchor_or_is_counted(self):
        page = render(self.docs)
        self.assertEqual(page.ids.count("p-lena-ordoy"), 1)
        self.assertEqual(careers.counts(careers.build(self.docs)).players, 1)


class TestSeasonsReadOldestFirstInTheFrontPagesOrder(unittest.TestCase):
    """Rows run oldest first, by the one season order the front page sorts by.

    Digit runs compare as numbers, so season 9 comes before season 10, and two keys
    whose numbers are equal -- ``s01`` and ``s1`` -- are then put in key order, so
    the page does not depend on which document arrived first.
    """

    def keys(self, *docs):
        career, = careers.build(docs)
        return [row.season_key for row in career.rows]

    def one(self, key, competition=None):
        return document(key, player("gil-ahearn", figures=played(1, 1, 0)),
                        competition=competition)

    def test_numbers_in_a_key_compare_as_numbers(self):
        self.assertEqual(self.keys(self.one("10"), self.one("9")), ["9", "10"])
        self.assertEqual(self.keys(self.one("2026-finals"), self.one("2026")),
                         ["2026", "2026-finals"])

    def test_keys_whose_numbers_are_equal_are_put_in_key_order_every_time(self):
        docs = (self.one("s1", "a-tuesday-c"), self.one("s01", "z-tuesday-c"))
        for order in permutations(docs):
            self.assertEqual(self.keys(*order), ["s01", "s1"])

    def test_the_front_page_and_the_careers_page_share_one_order(self):
        self.assertIs(careers.season_order_key, competition_mod.season_order_key)
        self.assertIs(site.season_order_key, competition_mod.season_order_key)
        self.assertFalse(hasattr(site, "_season_order_key"))


class TestTheNameIsTheNewestSeasonsSpelling(unittest.TestCase):
    """The heading is the newest season's name; an older spelling says "as ..."."""

    def test_the_heading_is_the_newest_and_the_old_row_names_its_own(self):
        docs = [document("2025-winter", player("bo-nakamura", "B. Nakamura",
                                               played(1, 1, 0))),
                document("2026-winter", player("bo-nakamura", "Bo Nakamura",
                                               played(1, 1, 0)))]
        page = render(docs)
        self.assertEqual(page.headings["h-bo-nakamura"], "Bo Nakamura")
        old, new = page.rows("bo-nakamura")
        self.assertEqual(old["cells"][0]["small"], "as B. Nakamura")
        self.assertEqual(new["cells"][0]["small"], "")


class TestTheListIsAlphabeticalAndNotARanking(unittest.TestCase):
    """By name, ignoring case and accents, then by slug; never by any figure.

    A season in one grade and a season in another are not the same tennis, so a
    list ordered by wins would be a comparison the cards cannot support.
    """

    def test_the_order_is_the_name_then_the_slug_whatever_the_totals(self):
        docs = [document("2026-winter",
                         player("kit-vasse-parkview-wanderers", "Kit Vasse",
                                played(1, 1, 0)),
                         player("bo-nakamura", "bo Nakamura", played(9, 20, 0)),
                         player("kit-vasse", "Kit Vasse", played(2, 2, 0)),
                         player("ana-fernandez", "Ana Fernandez", played(1, 0, 3)))]
        expected = ["ana-fernandez", "bo-nakamura", "kit-vasse",
                    "kit-vasse-parkview-wanderers"]
        self.assertEqual([c.slug for c in careers.build(docs)], expected)
        index = render(docs).rows("index")
        self.assertEqual([row["cells"][0]["href"] for row in index],
                         [f"#p-{slug}" for slug in expected])


def listed(*names: tuple[str, str]) -> list[str]:
    """The slugs of a careers list of *names*, each ``(slug, name)``, in its order."""
    docs = [document("2025-winter", *(player(slug, name, played(1, 1, 0))
                                      for slug, name in names))]
    return [career.slug for career in careers.build(docs)]


class TestAnAccentedNameIsListedAmongTheNamesOfTheLetterItIsFiledUnder(
        unittest.TestCase):
    """The list is in the order of the fold a career's initial is taken from.

    Measured before, in process (/tmp/t7/scratch/CA2/probe/order218.py,
    before-order218.txt): Adam Renn, Eve Renn, Ezra Renn, Zoe Arden and Émile
    Renn were listed Adam, Eve, Ezra, Zoe and then Émile, and élodie Quaife and
    Åna Renn after Zoe too, each filed under its letter by ``careers.initial``
    while the list sorted on the name casefolded alone, whose accented letter
    sorts after z. A full-width ``Ｗei Calder``, filed under W, was listed last of
    all, after ``李 Wren``. So an accented name came after Z on the index and on a
    one-page careers.html, and after every plain name on its initial's page.

    Now one fold, :func:`matchcentre.slugs.fold`, takes the case and the accents
    off for the slug, for the initial and for the order, and the list is in the
    order of the pages the initials are filed on, a to z, 0-9, other, then the
    name folded from its initial on, and the slug breaks a tie. Sorting on the
    fold alone, as this unit first did, still put ``'Ofa Renn`` (filed under O)
    and ``4 Tien`` (under 0-9) before ``Adam``, so a split index's names were out
    of the order of the pages it links. Rejected: ``locale.strxfrm``, which
    collates as the machine's locale says, so the same cards would list in
    another order on another machine, and a rebuild would stop being the bytes
    it was.
    """

    def test_an_accented_name_is_listed_with_its_letter(self):
        self.assertEqual(listed(("adam-renn", "Adam Renn"), ("eve-renn", "Eve Renn"),
                                ("ezra-renn", "Ezra Renn"), ("zoe-arden", "Zoe Arden"),
                                ("emile-renn", "Émile Renn")),
                         ["adam-renn", "emile-renn", "eve-renn", "ezra-renn",
                          "zoe-arden"])

    def test_a_lower_case_accented_name_and_a_full_width_letter_are_too(self):
        self.assertEqual(listed(("wren", "李 Wren"), ("wei-calder", "Ｗei Calder"),
                                ("elodie-quaife", "élodie Quaife"),
                                ("ana-renn", "Åna Renn"), ("bo-vasse", "Bo Vasse"),
                                ("xan-renn", "Xan Renn")),
                         ["ana-renn", "bo-vasse", "elodie-quaife", "wei-calder",
                          "xan-renn", "wren"])

    def test_an_initials_page_lists_its_careers_in_that_order(self):
        docs = [document("2025-winter", *(player(slug, name, played(1, 1, 0))
                                          for slug, name in (
            ("ezra-renn", "Ezra Renn"), ("emile-renn", "Émile Renn"),
            ("eve-renn", "Eve Renn"))))]
        page = CareersPage(careers.initial_html(
            "e", careers.build(docs), href=lambda row, slug: "#",
            page=site.careers_page_name, index=site.CAREERS_PAGE))
        self.assertEqual(careers_on(page), ["emile-renn", "eve-renn", "ezra-renn"])
        self.assertEqual([row["cells"][0]["href"] for row in page.rows("index")],
                         ["#p-emile-renn", "#p-eve-renn", "#p-ezra-renn"])

    def test_names_that_fold_alike_are_ordered_by_slug_whichever_came_first(self):
        # The accented spelling has the smaller slug, so the plain one no longer
        # goes first because an unaccented e sorts before an accented one.
        names = (("emile-renn-lakeside", "Emile Renn"), ("emile-renn", "Émile Renn"),
                 ("emile-renn-quaife", "EMILE RENN"))
        for order in permutations(names):
            with self.subTest(order=[slug for slug, _ in order]):
                self.assertEqual(listed(*order), ["emile-renn", "emile-renn-lakeside",
                                                  "emile-renn-quaife"])

    def test_a_name_led_by_punctuation_or_a_digit_is_listed_with_its_page(self):
        # In the order of the pages the names are filed on, a to z, then 0-9, then
        # other, and alphabetical from the initial on each: ``'Ofa`` is among the
        # Os and ``4 Tien`` after the Zs, where the fold alone put both first.
        self.assertEqual(listed(("4-tien", "4 Tien"), ("ofa-renn", "'Ofa Renn"),
                                ("oba-vasse", "Oba Vasse"), ("adam-renn", "Adam Renn"),
                                ("zoe-arden", "Zoe Arden"), ("yna-vasse", "Øyna Vasse")),
                         ["adam-renn", "oba-vasse", "ofa-renn", "zoe-arden", "4-tien",
                          "yna-vasse"])

    def test_a_split_index_lists_its_names_in_the_order_of_the_pages_it_links(self):
        docs = [document("2025-winter", *(player(slug, name, played(1, 1, 0))
                                          for slug, name in (
            ("zoe-arden", "Zoe Arden"), ("4-tien", "4 Tien"), ("eve-renn", "Eve Renn"),
            ("ofa-renn", "'Ofa Renn"), ("emile-renn", "Émile Renn"))))]
        self.assertEqual([row["cells"][0]["href"]
                          for row in split_index(docs).rows("index")],
                         ["careers-e.html#p-emile-renn", "careers-e.html#p-eve-renn",
                          "careers-o.html#p-ofa-renn", "careers-z.html#p-zoe-arden",
                          "careers-0-9.html#p-4-tien"])

    def test_the_readme_says_accents_are_set_aside_in_the_order_too(self):
        prose = readme_prose("### Careers across seasons")
        self.assertIn("The list is alphabetical, case and accents set aside, so Émile "
                      "is among the other Es.", prose)
        self.assertIn("After Z come the names starting with a digit and then those "
                      "starting with any other letter", prose)

    def test_the_slug_the_initial_and_the_order_are_one_fold(self):
        # A fold that reads a name backwards, patched in where the slug's is made:
        # the slug, the initial and the order all follow it, so none has a fold of
        # its own.
        fold = slugs_mod.fold
        with mock.patch.object(slugs_mod, "fold", lambda text: fold(text)[::-1]):
            self.assertEqual(slugs_mod.slugify("Ada Zu"), "uz-ada")
            self.assertEqual(careers.initial("Ada Zu"), "u")
            self.assertEqual(listed(("ada-zu", "Ada Zu"), ("bo-renn", "Bo Renn")),
                             ["bo-renn", "ada-zu"])
        self.assertEqual(listed(("ada-zu", "Ada Zu"), ("bo-renn", "Bo Renn")),
                         ["ada-zu", "bo-renn"])


class TestTheReportCountsOnlyPeopleWhoPlayed(unittest.TestCase):
    """People who played, people who played in two seasons, and seasons with play."""

    def test_no_play_anywhere(self):
        docs = [document("2026-winter", player("fay-bramwell"))]
        self.assertEqual(careers.counts(careers.build(docs)), (0, 0, 0))

    def test_one_season_of_play(self):
        docs = [document("2026-winter", player("dev-raman", figures=played(1, 1, 0)),
                         player("fay-bramwell"))]
        self.assertEqual(careers.counts(careers.build(docs)), (1, 0, 1))

    def test_two_seasons_two_grades_in_one_of_them(self):
        docs = [document("2025-winter", player("dev-raman", figures=played(1, 1, 0))),
                document("2026-winter", player("dev-raman", figures=played(1, 1, 0)),
                         competition="2026-winter-tuesday-c"),
                document("2026-winter", player("elin-sotto", figures=played(1, 0, 1)),
                         competition="2026-winter-tuesday-b")]
        self.assertEqual(careers.counts(careers.build(docs)), (2, 1, 2))


class TestTwoSeasonsWithOneLabelAreToldApartByTheirKeys(unittest.TestCase):
    """A Season cell adds the key only when one label is shown for two seasons.

    Two seasons both labelled "Season 2" really happen (the front page groups by
    that label and already adds the key when it collides), and two identical Season
    cells would be two rows nobody could tell apart. Adding the key every time is
    noise the rest of the time.
    """

    def names(self, *docs):
        return careers.season_names(docs)

    def test_a_colliding_label_is_given_its_key(self):
        names = self.names(document("2025-winter", season_label="Season 2"),
                           document("2026-winter", season_label="Season 2"))
        self.assertEqual(names, {"2025-winter-tuesday-c": "Season 2 (2025-winter)",
                                 "2026-winter-tuesday-c": "Season 2 (2026-winter)"})

    def test_distinct_labels_are_shown_bare(self):
        names = self.names(document("2025-winter"), document("2026-winter"))
        self.assertEqual(names, {"2025-winter-tuesday-c": "2025 Winter",
                                 "2026-winter-tuesday-c": "2026 Winter"})

    def test_two_grades_of_one_season_are_one_season_and_stay_bare(self):
        names = self.names(document("2026-winter", competition="2026-winter-b"),
                           document("2026-winter", competition="2026-winter-c"))
        self.assertEqual(set(names.values()), {"2026 Winter"})

    def test_a_season_with_no_folder_says_so(self):
        names = self.names(document("", season_label=""))
        self.assertEqual(names, {"tuesday-c": "(no season folder)"})

    def test_a_season_with_no_label_is_shown_by_its_key(self):
        names = self.names(document("2026-winter", season_label=""))
        self.assertEqual(names, {"2026-winter-tuesday-c": "2026-winter"})


class TestTheCareersPageFormatsFiguresAsTheSeasonPageDoes(unittest.TestCase):
    """Half away from zero, through the one rounding rule the season page uses.

    ``round()`` rounds 6.25 to 6.2 and 0.125 to 0.12; the season page prints 6.3 and
    0.13, and a careers row printing another digit for the same two numbers would
    be a figure that does not match the page it links to.
    """

    def test_a_win_rate_rounds_half_up(self):
        self.assertEqual(careers.win_rate_text(1, 15), "6.3%")

    def test_a_rating_prints_two_places_half_up(self):
        self.assertEqual(careers.rating_text((0.125, 4.2)), "0.13 / 4.20")

    def test_a_missing_rating_is_a_dash(self):
        self.assertEqual(careers.rating_text((None, None)), "— / —")


# A career's table and the list of names, headed as a reader sees them: each
# heading's text, the title behind it, and the key its caption prints.
CAREER_HEADS = (("Season", None), ("Competition", None), ("Team", None),
                ("M", "Matches played"), ("Won", "Sets won"), ("Lost", "Sets lost"),
                ("Win %", "Sets won as a percentage of completed sets"),
                ("GF", "Games for"), ("GA", "Games against"), ("SW", "Singles won"),
                ("SL", "Singles lost"), ("DW", "Doubles won"), ("DL", "Doubles lost"),
                ("Rating", "Singles / doubles rating"))
CAREER_KEY = ("M — Matches played · Won — Sets won · Lost — Sets lost · Win % — "
              "Sets won as a percentage of completed sets · GF — Games for · GA — "
              "Games against · SW — Singles won · SL — Singles lost · DW — Doubles "
              "won · DL — Doubles lost · Rating — Singles / doubles rating")
INDEX_HEADS = (("Player", None), ("Seasons played", None), ("M", "Matches played"),
               ("Won", "Sets won"), ("Lost", "Sets lost"),
               ("Win %", "Sets won as a percentage of completed sets"),
               ("GF", "Games for"), ("GA", "Games against"))
INDEX_KEY = ("M — Matches played · Won — Sets won · Lost — Sets lost · Win % — "
             "Sets won as a percentage of completed sets · GF — Games for · GA — "
             "Games against")
# The figures the season's players table heads too, by their key there: each is
# headed on a careers page as it is headed there.
SHARED_WITH_THE_PLAYERS_TABLE = {"matches", "sets_won", "win_pct", "games_won",
                                 "games_lost"}
CAREER_SENTENCE = ("Bo Nakamura, season by season, oldest first. Each season opens "
                   "that season's page for them.")


def heads(page: CareersPage, table: str) -> list[tuple[str, str | None]]:
    """A table's headings, each its text and the title it carries, if any."""
    row, = page.tables[table]["thead"]
    return [(cell["text"], cell["attrs"].get("title")) for cell in row["cells"]]


class TestACareersTableIsHeadedWithTheLettersItsCaptionSpellsOut(unittest.TestCase):
    """Short labels head the figures, their words in a title and in the caption's key.

    Measured before in headless Chrome over the DevTools pipe
    (/tmp/t7/scratch/CA2/probe/measure.py, before-chrome.txt): on the ten-season
    demo's careers page at 1280, 106 of the 107 table wraps overflowed, the widest
    table 1,410px in a 1,040px main, and on the two-season demo's 19 of 20, 1,369px;
    at 768 and narrower every one did. Headed in words, ``Rating (singles /
    doubles)`` alone was 169px and ``Doubles won`` 95px over figures of two digits.
    Headed ``M``, ``Won``, ``GF`` and the rest, none of the 107 overflows at 1280
    or 1024, nor any of the 20, the widest career table 907px left to its own
    width (/tmp/t7/scratch/CA2/fix/fix-chrome.txt); at 768 and narrower they
    scroll, as a season page's tables do there. The key is two lines under the
    sentence, a caption 63px high where it was 24, and the ten-season page 351,521
    bytes where it was 295,211.

    A figure the season's players table heads too is headed as it heads it, by
    its key there, label and words: ``M``, ``Won``, ``Win %``, ``GF`` and ``GA``.
    This unit first headed games ``GW`` and ``GL`` and the rate ``Win rate``,
    which a test matching labels could not see. ``Win %`` because both players
    tables head the rate so; ``Win rate`` is a tile's and the slot table's. And no
    label is one the season pages give another meaning: their ``W`` and ``L`` are
    matches won and lost. Rejected: the singles and doubles split moved
    to a second table under each career, which doubles the tables, halves the
    ``All seasons`` total across two footers and gives the eye a second table to
    cross-check against the first.
    """

    def setUp(self):
        self.docs = [document(key, player("bo-nakamura", "Bo Nakamura",
                                          played(3, 4, 2, games_won=40)))
                     for key in ("2025-winter", "2026-winter")]

    def test_each_heading_is_its_label_with_its_words_in_a_title(self):
        self.assertEqual(heads(render(self.docs), "bo-nakamura"), list(CAREER_HEADS))

    def test_the_caption_is_its_sentence_and_under_it_the_key_to_those_labels(self):
        self.assertEqual(render(self.docs).caption_parts["bo-nakamura"],
                         [("caption-text", CAREER_SENTENCE),
                          ("table-key", CAREER_KEY)])

    def test_a_name_in_the_caption_is_escaped_once(self):
        docs = [document(key, player("bo-o-hara", "Bo O'Hara & Co", played(1, 1, 0)))
                for key in ("2025-winter", "2026-winter")]
        self.assertEqual(render(docs).caption_parts["bo-o-hara"][0],
                         ("caption-text", "Bo O'Hara & Co, season by season, oldest "
                          "first. Each season opens that season's page for them."))

    def test_the_list_of_names_heads_the_same_figures_the_same_way(self):
        page = render(self.docs)
        self.assertEqual(heads(page, "index"), list(INDEX_HEADS))
        self.assertEqual(page.caption_parts["index"],
                         [("caption-text", careers._INDEX_CAPTION),
                          ("table-key", INDEX_KEY)])

    def test_an_initials_list_of_names_does_too(self):
        page = CareersPage(careers.initial_html(
            "b", careers.build(self.docs), href=lambda row, slug: "#",
            page=site.careers_page_name, index=site.CAREERS_PAGE))
        self.assertEqual(heads(page, "index"), list(INDEX_HEADS))
        self.assertEqual(page.caption_parts["index"][1], ("table-key", INDEX_KEY))
        self.assertEqual(heads(page, "bo-nakamura"), list(CAREER_HEADS))

    def test_a_split_indexs_list_heads_no_figure_and_so_has_no_key(self):
        page = split_index(self.docs)
        self.assertEqual(heads(page, "index"), [("Player", None)])
        self.assertEqual(page.caption_parts["index"],
                         [("caption-text", careers._INDEX_CAPTION)])

    def test_every_heading_title_and_key_comes_from_the_one_list(self):
        renamed = {**careers.COLUMNS, "sets_won": ("Xw", "Sets won, renamed"),
                   "rating": ("Xr", "Ratings, renamed")}
        with mock.patch.object(careers, "COLUMNS", renamed):
            page = render(self.docs)
            split = CareersPage(careers.initial_html(
                "b", careers.build(self.docs), href=lambda row, slug: "#",
                page=site.careers_page_name, index=site.CAREERS_PAGE))
        for shown in (page, split):
            for table, at in (("bo-nakamura", 4), ("index", 3)):
                with self.subTest(page=shown is page, table=table):
                    self.assertEqual(heads(shown, table)[at],
                                     ("Xw", "Sets won, renamed"))
                    self.assertIn(" · Xw — Sets won, renamed · ",
                                  shown.caption_parts[table][1][1])
            self.assertEqual(heads(shown, "bo-nakamura")[-1],
                             ("Xr", "Ratings, renamed"))
            self.assertTrue(shown.caption_parts["bo-nakamura"][1][1].endswith(
                " · Xr — Ratings, renamed"))

    def test_a_figure_the_season_players_table_heads_is_headed_as_it_heads_it(self):
        # By the figure's key in app.js's PLAYER_COLS, and never by its label, so
        # the test does not pass by a careers label that matches no season one.
        source = (ROOT / "matchcentre" / "web" / "app.js").read_text(encoding="utf-8")
        body = re.search(r"\nvar PLAYER_COLS = \[(.*?)\n\];", source, re.S).group(1)
        season = {}
        for spec in re.finditer(r"\{[^{}]*\}", body):
            key = re.search(r"key: '([^']*)'", spec.group(0)).group(1)
            label = re.search(r"label: '([^']*)'", spec.group(0)).group(1)
            title = re.search(r"title: ((?:'[^']*'\s*\+\s*)*'[^']*')", spec.group(0))
            season[key] = (label, "".join(re.findall(r"'([^']*)'", title.group(1)))
                           if title else label)
        shared = set(season) & set(careers.COLUMNS)
        self.assertLessEqual(SHARED_WITH_THE_PLAYERS_TABLE, shared)
        for key in sorted(shared):
            with self.subTest(key=key):
                self.assertEqual(careers.COLUMNS[key], season[key])

    def test_a_label_the_season_pages_print_too_means_there_what_it_means_here(self):
        # Each of app.js's heading specs, read as test_web_assets reads them, the
        # label and the words its title gives it wherever it is printed: a careers
        # label the dashboard prints is never there another figure, as its ``W``
        # and ``L`` are matches won and lost where a career's ``Won`` is sets.
        source = (ROOT / "matchcentre" / "web" / "app.js").read_text(encoding="utf-8")
        season: dict[str, set[str]] = {}
        for spec in re.finditer(r"\{[^{}]*\}", source):
            label = re.search(r"label: '([^']*)'", spec.group(0))
            title = re.search(r"title: ((?:'[^']*'\s*\+\s*)*'[^']*')", spec.group(0))
            if label and title:
                season.setdefault(label.group(1), set()).add(
                    "".join(re.findall(r"'([^']*)'", title.group(1))))
        printed = [(label, words) for label, words in careers.COLUMNS.values()
                   if label in season]
        self.assertGreaterEqual(len(printed), len(SHARED_WITH_THE_PLAYERS_TABLE))
        for label, words in printed:
            with self.subTest(label=label):
                self.assertEqual(season[label], {words})

    def test_a_figures_heading_is_classed_as_the_figures_under_it_are(self):
        # class="num", as every figure's cell is and as the headings were before
        # this unit; the Season, Competition, Team and Player headings are text.
        page = render(self.docs)
        for table, text in (("bo-nakamura", 3), ("index", 1)):
            row, = page.tables[table]["thead"]
            classes = [cell["attrs"].get("class") for cell in row["cells"]]
            with self.subTest(table=table):
                self.assertEqual(classes, [None] * text + ["num"] * (len(classes) - text))


# --- the site ---------------------------------------------------------------


class SiteCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "site"

    def tearDown(self):
        self._tmp.cleanup()


class TestTheCareersPageIsOnlyEverWrittenInMultiSeasonMode(SiteCase):
    """Every multi-season write printing names has a careers page; no single one has.

    Including a multi site with one season so far, because every season player page
    links its player's anchor there, and a link that sometimes lands on nothing is
    the thing JavaScript would then have to decide. And every caller asks the one
    decider: with it patched to say no, a multi build writes no page, no front-page
    link, no report line, refuses nothing, and keeps the namesake note.
    """

    def test_a_single_season_site_has_no_careers_page_and_no_link(self):
        write(self.out, "2026-winter", multi=False)
        self.assertFalse((self.out / site.CAREERS_PAGE).exists())
        self.assertNotIn("careers.html",
                         (self.out / "index.html").read_text(encoding="utf-8"))

    def test_one_multi_season_has_the_page_the_link_and_says_it_is_one(self):
        write(self.out, "2026-winter")
        page = (self.out / site.CAREERS_PAGE).read_text(encoding="utf-8")
        self.assertIn("Only one season has results so far", page)
        self.assertIn('<a href="careers.html">',
                      (self.out / "index.html").read_text(encoding="utf-8"))

    def test_two_multi_seasons_have_the_page_and_the_link(self):
        write(self.out, *BOTH)
        page = (self.out / site.CAREERS_PAGE).read_text(encoding="utf-8")
        self.assertNotIn("Only one season", page)
        self.assertIn('<a href="careers.html">',
                      (self.out / "index.html").read_text(encoding="utf-8"))

    def test_every_caller_of_the_build_asks_the_one_decider(self):
        cwd = project(*BOTH)
        with mock.patch.object(careers, "writes_careers", return_value=False):
            code, text = main_in_process(cwd)
        self.assertEqual(code, 0, text)
        self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())
        self.assertNotIn("careers.html", (cwd / "site" / "index.html")
                         .read_text(encoding="utf-8"))
        self.assertFalse([line for line in text.splitlines()
                          if line.startswith("  careers ")], text)

    def test_the_refusal_asks_the_one_decider(self):
        cwd = project(*BOTH)
        (cwd / "site").mkdir()
        (cwd / "site" / site.CAREERS_PAGE).write_text("<p>ours</p>")
        with mock.patch.object(careers, "writes_careers", return_value=False):
            code, text = main_in_process(cwd)
        self.assertEqual(code, 0, text)
        self.assertEqual((cwd / "site" / site.CAREERS_PAGE).read_text(), "<p>ours</p>")

    def test_the_left_behind_scan_asks_the_one_decider(self):
        cwd = project(*BOTH)
        code, text = main_in_process(cwd)
        self.assertEqual(code, 0, text)
        with mock.patch.object(careers, "writes_careers", return_value=False):
            code, text = main_in_process(cwd)
        self.assertIn(f"  {cwd / 'site' / site.CAREERS_PAGE}",
                      section(text, "left behind"))

    def test_the_namesake_note_asks_the_one_decider(self):
        data = self.tmp / "data" / "2026-winter"
        write_two_grade_cards(data, shared="Ada Renn")
        cfg = config_mod.Config(path=self.tmp / "config.toml", seasons_mode="multi",
                                namesakes={"ada-renn": ("marina-magpies",
                                                        "station-starlings")})
        cards, rejected = scorecard.read_tree(self.tmp / "data")
        with mock.patch.object(careers, "writes_careers", return_value=False):
            found = discover_mod.discover(cards, rejected, config=cfg,
                                          root=self.tmp / "data")
        self.assertTrue(any("separated nobody" in said
                            for said in found.config_warnings))


class TestTheCareersPageIsAPageWithoutJavaScript(SiteCase):
    """Static HTML: the figures are in the markup, and the only script reads a theme.

    The mark is on the second line, for the reason the one-file mark is: the head
    is all anyone reads back, and the stamp the left-behind report prints is read
    from the same window, so a documentation comment above either would push them
    out of it.
    """

    def setUp(self):
        super().setUp()
        write(self.out, *BOTH)
        self.page = (self.out / site.CAREERS_PAGE).read_text(encoding="utf-8")
        self.template = (site.WEB / site.CAREERS_PAGE).read_text(encoding="utf-8")

    def test_the_doctype_comes_first_and_the_mark_second(self):
        lines = self.template.splitlines()
        self.assertEqual(lines[0], "<!DOCTYPE html>")
        self.assertEqual(lines[1], site.CAREERS_MARK)

    def test_the_mark_and_the_stamp_are_inside_the_window_read_back(self):
        head = site._careers_head(self.out / site.CAREERS_PAGE)
        self.assertIn(site.CAREERS_MARK, head)
        self.assertIn('<meta name="generated" content="', head)
        self.assertEqual(site._careers_stamp(self.out / site.CAREERS_PAGE), STAMP)

    def test_there_is_no_script_but_the_inline_theme_reader(self):
        self.assertNotIn("<script src", self.page)
        self.assertEqual(self.page.count("<script"), 1)
        self.assertIn('data-field="sets_won"', self.page)

    def test_the_sentinels_are_the_listed_ones_and_none_survives(self):
        self.assertEqual(set(re.findall(r"<!--[A-Z-]+-->", self.template)),
                         set(site.CAREERS_SENTINELS))
        for sentinel in site.CAREERS_SENTINELS:
            self.assertNotIn(sentinel, self.page)


class TestEveryLinkOnTheCareersPageOpensAFileThatIsThere(SiteCase):
    """Every link names a file that is there, and each row names its own season's.

    The row's directory is worked out by the function that decided where that
    dashboard was written, so the two cannot disagree; a link that stops at a
    directory opens a file listing, or nothing, from file://.
    """

    def setUp(self):
        super().setUp()
        write(self.out, *BOTH)
        self.page = read_page(self.out)
        self.seasons, _, _ = documents(*BOTH)

    def test_every_link_opens_an_html_file_under_the_site(self):
        for href in self.page.hrefs:
            target = href.partition("#")[0]
            if not target:
                self.assertIn(href[1:], self.page.ids, href)
                continue
            self.assertTrue(target.endswith(".html"), href)
            self.assertTrue((self.out / target).is_file(), href)

    def test_each_row_links_the_dashboard_its_document_was_written_to(self):
        expected = {site.competition_dir(self.out, season, multi=True)
                    .relative_to(self.out).as_posix()
                    + "/index.html#/player/" + entry["slug"]
                    for season in self.seasons for entry in season["players"]}
        shown = {row["cells"][0]["href"] for slug, table in self.page.tables.items()
                 if slug != "index" for row in table["tbody"]}
        self.assertEqual(shown, expected)

    def test_every_slug_in_every_season_has_exactly_one_anchor(self):
        for season in self.seasons:
            for entry in season["players"]:
                self.assertEqual(self.page.ids.count(f"p-{entry['slug']}"), 1,
                                 entry["slug"])


class TestACareersPageSomebodyElseWroteIsNotOverwritten(SiteCase):
    """An unmarked ``careers.html`` stops the write, before anything is written.

    It is a new name at the root that a club may already have used for a page of
    its own. The complaint joins the stranger-file complaints in the one error, so
    a club with both problems hears of both in one run.
    """

    def message(self):
        return (f"{self.out / 'careers.html'} is not a page this tool wrote, and "
                f"the careers page needs that name; move it aside or build into a "
                f"different --out directory")

    def linked(self):
        """A link's refusal, in the words of every name a build writes."""
        return (f"{self.out / 'careers.html'} is a link, and the careers "
                f"page needs that name for a file of its own; move it aside "
                f"or build into a different --out directory")

    def test_an_unmarked_page_is_refused_and_nothing_is_written(self):
        self.out.mkdir()
        (self.out / "careers.html").write_text("<p>our own page</p>")
        with self.assertRaises(site.SiteError) as caught:
            write(self.out, *BOTH)
        self.assertEqual(str(caught.exception), self.message())
        self.assertEqual((self.out / "careers.html").read_text(), "<p>our own page</p>")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["careers.html"])

    def test_a_directory_of_that_name_is_refused_the_same_way(self):
        (self.out / "careers.html").mkdir(parents=True)
        with self.assertRaises(site.SiteError) as caught:
            write(self.out, *BOTH)
        self.assertEqual(str(caught.exception), self.message())
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["careers.html"])

    def test_a_stranger_file_and_the_page_are_named_in_one_error(self):
        comp = self.out / "2026-winter" / COMP["2026-winter"]
        comp.mkdir(parents=True)
        (comp / "notes.txt").write_text("ours")
        (self.out / "careers.html").write_text("<p>our own page</p>")
        with self.assertRaises(site.SiteError) as caught:
            write(self.out, *BOTH)
        self.assertIn("notes.txt", str(caught.exception))
        self.assertIn(self.message(), str(caught.exception))

    def test_a_link_to_nowhere_in_its_place_is_refused_and_not_written_through(self):
        """A dangling link is a name somebody took, and writing to it lands elsewhere.

        ``Path.exists()`` follows the link and says no, so asked alone it would
        let the write go through the link. Measured with the ``is_symlink()`` half
        taken out (/tmp/t4/scratch/y87/probe/guards/probe.txt): no error, and a
        careers page appeared at the link's target, outside ``--out``.
        """
        self.out.mkdir()
        elsewhere = self.tmp / "elsewhere.html"
        (self.out / "careers.html").symlink_to(elsewhere)
        with self.assertRaises(site.SiteError) as caught:
            write(self.out, *BOTH)
        self.assertEqual(str(caught.exception), self.linked())
        self.assertFalse(elsewhere.exists())
        self.assertTrue((self.out / "careers.html").is_symlink())
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["careers.html"])

    def test_a_link_to_a_page_this_tool_wrote_is_refused_too(self):
        """A link is a name somebody took, whatever it points at.

        This tool writes the page as a file and never makes a link, so a link in
        its place is somebody else's, and followed it rewrites whatever it points
        at. Measured before links were refused
        (/tmp/t4/scratch/y87/fix2/link_probe.before.txt): one project's
        ``site/careers.html`` linked to another's, and its one-season build
        rewrote the other project's page, 13 careers down to 12, each of them one
        season long. The rejected alternative is refusing only a link to nowhere,
        which is the one link whose mark cannot be read through it.
        """
        self.out.mkdir()
        elsewhere = self.tmp / "elsewhere"
        write(elsewhere, *BOTH)
        page = elsewhere / site.CAREERS_PAGE
        marked = page.read_bytes()
        (self.out / "careers.html").symlink_to(page)
        with self.assertRaises(site.SiteError) as caught:
            write(self.out, "2026-winter")
        self.assertEqual(str(caught.exception), self.linked())
        self.assertEqual(page.read_bytes(), marked)
        self.assertTrue((self.out / "careers.html").is_symlink())
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["careers.html"])


class TestACareersPageThatCannotBeWrittenWholeStopsTheRunBeforeAnythingIs(SiteCase):
    """The page is checked and rendered before any dashboard is written, as a SiteError.

    Three ways the page cannot be made whole: a season document without a count it
    adds, a template missing a sentinel, and no template at all. Each stops the run
    the way a stranger file does, before the first write and as a ``SiteError``,
    which is the one exception ``build.py`` turns into a sentence and exit 2.

    Measured by switching each guard off in a throwaway copy
    (/tmp/t4/scratch/y87/probe/guards/probe.txt). Rendering after the dashboards
    left 13 files written: the dashboards were new, and the careers page that adds
    them up was not. Without the conversion the missing
    count reached ``build.py`` as a ``CareersError``, which it does not catch. With
    no sentinel check a template without ``<!--FOOTER-->`` was written, 19 files,
    and the page had no footer. And with the template left off the list of
    required ones, its absence was a ``FileNotFoundError`` from the read.

    The rejected alternative is to render the page after the dashboards, since
    then it can read the season files they wrote. But that is the page adding up
    files on disk, and a dashboard left behind would be added into careers.
    """

    def assert_nothing_written(self):
        self.assertFalse(self.out.exists(),
                         sorted(str(p) for p in self.out.rglob("*")))

    def test_a_document_without_a_count_the_page_adds_is_named(self):
        seasons, club, cfg = documents(*BOTH)
        entry = seasons[1]["players"][0]
        entry.pop("sets_won")
        with self.assertRaises(site.SiteError) as caught:
            site.write_site(self.out, seasons=seasons, club=club, config=cfg,
                            generated=STAMP)
        for part in (COMP["2026-winter"], entry["slug"], "sets_won"):
            self.assertIn(part, str(caught.exception))
        self.assert_nothing_written()

    def test_check_stops_on_that_document_as_a_build_does(self):
        """``--check`` says what a build would say, so it stops on the same document.

        The report's count is made from the same careers as the page, through the
        same conversion, before any section is composed. Measured before
        (/tmp/t4/scratch/y87/fix2/check_probe.before.txt): the build ended with
        exit 2, the version line and the sentence naming the key, and ``--check``
        over the same documents ended in a ``CareersError`` traceback.
        """
        cwd = project(*BOTH)
        real = export.build_season

        def without_sets_won(*args, **kwargs):
            season = real(*args, **kwargs)
            if season["meta"]["competition"] == COMP["2026-winter"]:
                season["players"][0].pop("sets_won")
            return season

        with mock.patch.object(export, "build_season", side_effect=without_sets_won):
            built = main_in_process(cwd)
            checked = main_in_process(cwd, "--check")
        self.assertEqual(built[0], 2, built[1])
        self.assertIn("sets_won is no such figure", built[1])
        self.assertEqual(checked, built)
        self.assertFalse((cwd / "site").exists())

    def web_without(self, change) -> Path:
        web = self.tmp / "web"
        shutil.copytree(site.WEB, web)
        change(web / site.CAREERS_PAGE)
        return web

    def test_a_template_missing_a_sentinel_is_named(self):
        web = self.web_without(lambda page: page.write_text(
            page.read_text(encoding="utf-8").replace("<!--FOOTER-->", ""),
            encoding="utf-8"))
        with mock.patch.object(site, "WEB", web):
            with self.assertRaises(site.SiteError) as caught:
                write(self.out, *BOTH)
        self.assertIn(f"{web / site.CAREERS_PAGE} is missing <!--FOOTER-->",
                      str(caught.exception))
        self.assert_nothing_written()

    def test_a_missing_template_is_named_with_the_others(self):
        web = self.web_without(Path.unlink)
        with mock.patch.object(site, "WEB", web):
            with self.assertRaises(site.SiteError) as caught:
                write(self.out, *BOTH)
        self.assertEqual(str(caught.exception),
                         f"missing page template(s): {web / site.CAREERS_PAGE}")
        self.assert_nothing_written()


class TestTheFrontPageLinksTheCareersPageByItsFileName(SiteCase):
    """The front-page link rides on the competitions list, and only in multi mode.

    Appended after the last season group rather than given a sentinel of its own,
    so the single-season front page is the same bytes it always was.
    """

    LINK = ('\n    <p class="careers-link"><a href="careers.html">Careers across '
            'seasons</a> <span class="muted">every player\'s seasons, added '
            'up</span></p>')

    def test_the_multi_front_page_has_the_link_after_the_last_group(self):
        self.assertEqual(site.CAREERS_LINK_HTML, self.LINK)
        write(self.out, *BOTH)
        front = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("    </div>" + self.LINK, front)

    def test_the_single_front_page_is_what_it_was_without_the_link(self):
        write(self.out, "2026-winter", multi=False)
        front = (self.out / "index.html").read_bytes()
        self.assertNotIn(b"careers.html", front)
        with mock.patch.object(site, "CAREERS_LINK_HTML", ""):
            write(self.tmp / "bare", "2026-winter", multi=False)
        self.assertEqual((self.tmp / "bare" / "index.html").read_bytes(), front)


class TestAnOutFolderNamedCareersDoesNotMistakeTheCareersPageForAOneFileDashboard(SiteCase):
    """``--out careers`` names a fold ``careers.html``; the page there is not one.

    A folded dashboard is the page named after its directory with the season
    document beside it, and the careers page has no document beside it.
    """

    def test_only_the_competition_folds_are_listed(self):
        out = self.tmp / "careers"
        written = write(out, *BOTH, single_file=True)
        self.assertEqual(sorted(site.folded_pages(written.paths)),
                         sorted(out / key / COMP[key] / f"{COMP[key]}.html"
                                for key in BOTH))


class TestARootFileIsListedOnceHoweverManyPagesNameIt(SiteCase):
    """The root ``styles.css`` serves the front page and the careers page, once."""

    def test_the_root_stylesheet_is_one_path(self):
        written = write(self.out, *BOTH)
        self.assertEqual(written.paths.count(self.out / "styles.css"), 1)


class TestNoShippedFileStillSaysSeasonsAreNeverAddedUp(unittest.TestCase):
    """The README, the example config and data/README say what the page does.

    Each of them said, before this page, that nothing is added across seasons or
    that one name in two grades needs no namesakes entry; both are false in
    multi-season mode now. The CHANGELOG's past entries are history and exempt.
    """

    BANNED = ("not built yet", "nothing is totalled across seasons",
              "seasons are never added together", "not a player's record",
              "needs no entry at all")

    def test_no_shipped_file_says_it(self):
        for name in ("README.md", "config.example.toml", "data/README.md"):
            text = collapsed((ROOT / name).read_text(encoding="utf-8")
                             .replace("#", " ")).casefold()
            for phrase in self.BANNED:
                self.assertNotIn(phrase, text, f"{name}: {phrase}")

    def test_the_faq_names_the_careers_exception(self):
        paragraphs = (ROOT / "README.md").read_text(encoding="utf-8").split("\n\n")
        faq, = [p for p in paragraphs if "two players already" in p]
        self.assertIn("careers page", collapsed(faq))


class TestAnOutFolderNamedIndexIsNotOfferedAsTheOneFileDashboard(unittest.TestCase):
    """``--out index`` made the front page the one-file dashboard (4.49).

    ``folded_pages`` went by name alone, and a folder named ``index`` makes a fold's
    name ``index.html`` -- the front page's. Measured before
    (/tmp/t4/scratch/y87/m_folded_index.py): a plain build printed ``one file
    index/index.html (14KB)``, offering a 14KB front page with no dashboard in it
    to email, and ``--single-file`` counted it as a second fold, ``2 written, one
    beside each dashboard (380KB in all)``. A fold now also needs its season
    document beside it; the rejected alternative, refusing ``--out index``, turns
    a folder name a club may already publish under into an error.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_front_page_in_a_folder_named_index_is_not_a_fold(self):
        out = self.tmp / "index"
        comp = out / "tuesday-c"
        paths = [out / "index.html", comp / "index.html", comp / "season-data.js",
                 comp / "tuesday-c.html"]
        self.assertEqual(site.folded_pages(paths), [comp / "tuesday-c.html"])
        self.assertEqual(site.folded_pages(paths[:2]), [])

    def one_file(self, text):
        return [line for line in text.splitlines() if line.startswith("  one file")]

    def test_a_plain_build_into_index_offers_no_one_file_dashboard(self):
        cwd = project("2026-winter", multi=False)
        run = run_build(cwd, "--out", "index")
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual(self.one_file(run.out), [])

    def test_a_one_file_build_into_index_offers_the_fold_alone(self):
        cwd = project("2026-winter", multi=False)
        run = run_build(cwd, "--out", "index", "--single-file")
        self.assertEqual(run.code, 0, run.text)
        line, = self.one_file(run.out)
        self.assertIn("index/tuesday-division-b/tuesday-division-b.html", line)
        self.assertNotIn("written", line)

    def test_a_rebuild_counts_the_one_fold_it_rewrote(self):
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        write_two_grade_cards(cwd / "data")
        first = run_build(cwd, "--out", "index", "--single-file")
        self.assertEqual(first.code, 0, first.text)
        line, = self.one_file(first.out)
        self.assertIn("2 written, one beside each dashboard", line)
        # The second run reads the config the first one wrote, which holds what the
        # first worked out commented out, so it is already the steady tree.
        steady = run_build(cwd, "--out", "index", "--single-file")
        line, = self.one_file(steady.out)
        self.assertIn("none written: all 2", line)
        fold = sorted((cwd / "index").glob("*/*.html"))
        fold = next(p for p in fold if p.name == f"{p.parent.name}.html")
        fold.write_bytes(fold.read_bytes() + b"\n")
        again = run_build(cwd, "--out", "index", "--single-file")
        self.assertEqual(again.code, 0, again.text)
        line, = self.one_file(again.out)
        self.assertIn("1 of 2 written, one beside each dashboard", line)


# --- the build --------------------------------------------------------------


class TestTheCareersFiguresAreTheSeasonFilesAddedUp(unittest.TestCase):
    """Every figure on the built page is traced to a ``season.json`` on disk.

    Each row is followed by its own link to the document it names, and its figures
    compared with that document's; each total with the sum of them, and the win
    rate with an independent half-up of the added-up sets. This is the rule the
    page exists under, proved from the files a club would open. A row's
    Competition cell is that document's ``label``, the grade a club reads, and
    not the id the document is filed under: with the id in its place every test
    of this file passed until this one read the cell
    (/tmp/t4/scratch/y87/fix2/gaps_probe.before.txt).
    """

    @classmethod
    def setUpClass(cls):
        cls.cwd, cls.built = built_two_seasons()
        assert cls.built.code == 0, cls.built.text
        cls.site = cls.cwd / "site"
        cls.page = read_page(cls.site)

    def season_for(self, href):
        path, _, _ = href.partition("#/player/")
        return json.loads(
            (self.site / path).parent.joinpath("season.json").read_text())

    def document_for(self, href):
        slug = href.partition("#/player/")[2]
        return next(p for p in self.season_for(href)["players"] if p["slug"] == slug)

    def value(self, entry, key):
        head, _, tail = key.partition(".")
        return entry[head][tail] if tail else entry[head]

    def test_each_row_is_its_season_file(self):
        seen = 0
        for slug, table in self.page.tables.items():
            if slug == "index":
                continue
            for row in table["tbody"]:
                entry = self.document_for(row["cells"][0]["href"])
                # The grade the file names, not the id it is filed under.
                label = self.season_for(row["cells"][0]["href"])["meta"]["label"]
                self.assertEqual(row["cells"][1]["text"], label if entry["matches"]
                                 else f"{label} (on the roster, no sets played)")
                shown = fields(row)
                for key in FIGURES:
                    self.assertEqual(shown[key], str(self.value(entry, key)),
                                     (slug, key))
                if entry["sets_won"] + entry["sets_lost"]:
                    self.assertEqual(shown["win_pct"], f"{entry['win_pct']:.1f}%")
                else:
                    self.assertEqual(shown["win_pct"], "—")
                seen += 1
        self.assertEqual(seen, 24)   # twelve named in each season

    def test_each_total_is_the_season_files_added_up(self):
        for slug, table in self.page.tables.items():
            if slug == "index":
                continue
            entries = [self.document_for(row["cells"][0]["href"])
                       for row in table["tbody"]]
            shown = fields(self.page.total(slug))
            for key in FIGURES:
                self.assertEqual(shown[key],
                                 str(sum(self.value(e, key) for e in entries)),
                                 (slug, key))
            won = sum(e["sets_won"] for e in entries)
            lost = sum(e["sets_lost"] for e in entries)
            pct = (Decimal(100 * won) / Decimal(won + lost)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP) if won + lost else None
            self.assertEqual(shown["win_pct"], f"{pct}%" if pct is not None else "—")

    def test_the_index_repeats_each_total(self):
        for row in self.page.rows("index"):
            slug = row["cells"][0]["href"][len("#p-"):]
            shown, total = fields(row), fields(self.page.total(slug))
            for key in ("matches", "sets_won", "sets_lost", "win_pct",
                        "games_won", "games_lost"):
                self.assertEqual(shown[key], total[key], (slug, key))


class TestADashboardLeftBehindIsNotAddedIn(unittest.TestCase):
    """A season archived out of data/ drops off the page and is listed as left behind.

    Built from this run's documents and nothing read off disk, so last season's
    dashboard, still sitting under site/, is not summed into anybody's career.
    """

    def test_an_archived_season_is_gone_from_the_page(self):
        cwd = project(*BOTH)
        self.assertEqual(run_build(cwd).code, 0)
        shutil.move(str(cwd / "data" / "2025-winter"), str(cwd / "archive"))
        run = run_build(cwd)
        self.assertEqual(run.code, 0, run.text)
        page = read_page(cwd / "site")
        self.assertNotIn("p-kit-vasse", page.ids)
        self.assertEqual(len(page.rows("ana-fernandez")), 1)
        self.assertIn("  site/2025-winter/2025-winter-tuesday-division-b",
                      section(run.out, "left behind"))


class TestARebuildWithNothingNewLeavesTheCareersPageAlone(unittest.TestCase):
    """A rebuild with no new card writes nothing, the careers page included.

    Stamped with the newest season's date rather than the run's, and written only
    when its bytes move, so a Tuesday with nothing filed does not re-date a page
    whose figures have not changed (4.12).
    """

    def test_the_second_run_wrote_nothing(self):
        cwd = project(*BOTH)
        self.assertEqual(run_build(cwd).code, 0)
        page = cwd / "site" / site.CAREERS_PAGE
        before = (page.stat().st_mtime_ns, page.read_bytes())
        run = run_build(cwd, generated=LATER_NIGHT)
        self.assertEqual(run.code, 0, run.text)
        total = len(run.files("site"))
        self.assertIn(f"wrote nothing: all {total} file(s) under site already held",
                      collapsed(run.out))
        self.assertEqual((page.stat().st_mtime_ns, page.read_bytes()), before)


class TestANewCardInOneSeasonRewritesNoOtherSeason(unittest.TestCase):
    """A new night in 2026 rewrites 2026, the front page and the careers page only."""

    def test_only_the_newest_season_and_the_root_move(self):
        cwd = project(*BOTH)
        self.assertEqual(run_build(cwd).code, 0)
        old = {p: (p.stat().st_mtime_ns, p.read_bytes())
               for p in (cwd / "site" / "2025-winter").rglob("*") if p.is_file()}
        page = cwd / "site" / site.CAREERS_PAGE
        was = page.read_bytes()
        write_round_four(cwd / "data")
        run = run_build(cwd, generated=LATER_NIGHT)
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual({p: (p.stat().st_mtime_ns, p.read_bytes()) for p in old}, old)
        self.assertNotEqual(page.read_bytes(), was)
        self.assertEqual(site._careers_stamp(page), LATER_NIGHT)
        self.assertIn(site._footer_html(LATER_NIGHT), page.read_text(encoding="utf-8"))
        season = json.loads(
            (cwd / "site" / "2026-winter" / COMP["2026-winter"] / "season.json")
            .read_text())
        ana = next(p for p in season["players"] if p["slug"] == "ana-fernandez")
        newest = read_page(cwd / "site").rows("ana-fernandez")[-1]
        self.assertEqual(fields(newest)["matches"], str(ana["matches"]))
        self.assertEqual(ana["matches"], 4)


class TestTheReportSaysHowManyCareersSpanSeasons(unittest.TestCase):
    """One line in the cards section, under ``seasons``, in multi mode only.

    A figure, so it is printed under ``--check`` and hidden by ``--quiet`` as the
    ``seasons`` line is. It counts people who played; somebody who is only ever on
    a roster has no season to span.
    """

    def lines(self, run):
        return [line for line in run.out.splitlines() if line.startswith("  careers ")]

    def test_the_line_sits_directly_under_the_seasons_line(self):
        _, run = built_two_seasons()
        lines = run.out.splitlines()
        at = lines.index("  seasons      multi: 2025-winter, 2026-winter")
        self.assertEqual(lines[at + 1], CAREERS_LINE)

    def test_one_season_says_so(self):
        run = run_build(project("2026-winter"))
        self.assertEqual(self.lines(run),
                         ["  careers      one season so far; totals add up from the second"])

    def test_single_mode_and_quiet_print_none(self):
        self.assertEqual(self.lines(run_build(project("2026-winter", multi=False))), [])
        self.assertEqual(self.lines(run_build(project(*BOTH), "--quiet")), [])

    def test_check_prints_it_and_writes_no_page(self):
        cwd = project(*BOTH)
        run = run_build(cwd, "--check")
        self.assertEqual(self.lines(run), [CAREERS_LINE])
        self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())

    def test_the_three_shapes(self):
        shapes = {careers.Counts(0, 0, 0): "no season has a result in it yet",
                  careers.Counts(9, 0, 1): "one season so far; totals add up from "
                                           "the second",
                  careers.Counts(9, 7, 2): "7 of 9 player(s) played in more than "
                                           "one season"}
        for counts, line in shapes.items():
            self.assertEqual(build_mod._careers_line(counts), line)


class TestASingleSeasonBuildNamesTheCareersPageItLeftBehind(unittest.TestCase):
    """A careers page a multi build wrote is listed, last, when a single build runs.

    It is a correct-looking page nothing rebuilds, which is the one way this tool
    can leave a wrong figure published (4.6). It gets its own reason, not the
    mode-switch sentence about depth, which is untrue of a page; the dashboard
    paragraph after it is today's, byte for byte; and ``--prune`` removes it only
    while it still carries the mark.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = project(*BOTH)
        run = run_build(cls.base)
        assert run.code == 0, run.text
        cls.marked = (cls.base / "site" / site.CAREERS_PAGE).read_bytes()

    def switched(self):
        """The multi tree, with 2025 archived and the config back to single."""
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        for name in ("data", "site"):
            shutil.copytree(self.base / name, cwd / name)
        shutil.move(str(cwd / "data" / "2025-winter"), str(cwd / "archive"))
        (cwd / "config.toml").write_text("", encoding="utf-8")
        return cwd

    def item(self, lines, path):
        at = lines.index(f"  {path}")
        body = []
        for line in lines[at + 1:]:
            if not line.startswith("      "):
                break
            body.append(line.strip())
        return " ".join(body)

    def test_the_page_is_listed_last_with_its_own_reason(self):
        run = run_build(self.switched())
        self.assertEqual(run.code, 0, run.text)
        lines = section(run.out, "left behind")
        listed = [line.strip() for line in lines
                  if line.startswith("  site/") and not line.startswith("   ")]
        self.assertEqual(listed, ["site/2025-winter/" + COMP["2025-winter"],
                                  "site/2026-winter/" + COMP["2026-winter"],
                                  "site/careers.html"])
        item = self.item(lines, "site/careers.html")
        self.assertEqual(item, "careers across seasons -- built 2026-09-10T12:00:00 "
                               "only a multi-season build writes a careers page, "
                               "and this run is in single-season mode")
        text = collapsed("\n".join(lines))
        lead = ("The careers page above adds up the seasons of the dashboards "
                "listed with it. A multi-season build wrote it and this run, in "
                "single-season mode, did not; the command below removes it with them.")
        self.assertIn(lead + " Every directory above is a complete dashboard", text)

    def test_prune_removes_it_with_the_dashboards(self):
        cwd = self.switched()
        files = sum(1 for key in BOTH for p in (cwd / "site" / key / COMP[key]).rglob("*")
                    if p.is_file())
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())
        self.assertIn("removed 1 file(s)", self.item(section(run.out, "pruned"),
                                                     "site/careers.html"))
        self.assertIn(f"{files + 1} file(s) removed from 2 dashboard(s) and the "
                      f"careers page. Nothing this run wrote was touched.",
                      collapsed(run.out))

    def test_the_page_alone_has_a_lead_of_its_own(self):
        cwd = self.switched()
        self.assertEqual(run_build(cwd, "--prune").code, 0)
        (cwd / "site" / site.CAREERS_PAGE).write_bytes(self.marked)
        run = run_build(cwd)
        text = collapsed("\n".join(section(run.out, "left behind")))
        self.assertIn("The careers page above was written by a multi-season build, "
                      "and this run, in single-season mode, did not write it. "
                      "Nothing under site links it any more, but it opens by "
                      "double-click and shows the figures it was built with, which "
                      "is why this run names it. To delete it: python3 build.py "
                      "--prune", text)
        self.assertNotIn("That refuses", text)
        self.assertNotIn("Every directory above", text)
        run = run_build(cwd, "--prune")
        self.assertIn("1 file(s) removed: the careers page. Nothing this run wrote "
                      "was touched.", collapsed(run.out))
        self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())

    def test_a_run_withholding_names_gives_it_the_single_season_leads(self):
        """The mode left the page, so the lead is the mode's, withheld or not.

        A withheld single-season run lists the page with the single-season reason,
        and its lead has to agree with that reason in both forms. Nothing held the
        lead to it until the review of z462: handed the section's own flag, which
        is true of any run that withholds names, in place of asking whether
        withholding is all that kept the page away, the lead said the page was
        left for its names, directly under a reason saying it was left for the mode
        (/tmp/t4/scratch/z462/review/lead.mutH.txt).
        """
        cwd = self.switched()
        run = run_build(cwd, "--players", "numbered")
        self.assertEqual(run.code, 1, run.text)
        text = collapsed("\n".join(section(run.out, "left behind")))
        self.assertIn("this run withholds them The careers page above adds up the "
                      "seasons of the dashboards listed with it. A multi-season build "
                      "wrote it and this run, in single-season mode, did not; the "
                      "command below removes it with them. Every directory above", text)
        self.assertEqual(run_build(cwd, "--prune").code, 0)
        (cwd / "site" / site.CAREERS_PAGE).write_bytes(self.marked)
        run = run_build(cwd, "--players", "numbered")
        self.assertEqual(run.code, 1, run.text)
        text = collapsed("\n".join(section(run.out, "left behind")))
        self.assertIn("this run withholds them The careers page above was written by "
                      "a multi-season build, and this run, in single-season mode, did "
                      "not write it.", text)

    def test_an_unmarked_page_is_neither_listed_nor_removed(self):
        cwd = self.switched()
        (cwd / "site" / site.CAREERS_PAGE).write_text("<p>our own page</p>")
        run = run_build(cwd)
        self.assertNotIn("careers.html", run.out)
        run = run_build(cwd, "--prune")
        self.assertEqual((cwd / "site" / site.CAREERS_PAGE).read_text(),
                         "<p>our own page</p>")

    def test_a_link_in_the_pages_place_is_neither_listed_nor_removed(self):
        """What a build refuses to write through, a prune does not remove.

        The rule ``_stranger_names`` keeps for a dashboard: a prune looser than the
        build would delete what the build had refused to touch. Measured before
        links were refused (/tmp/t4/scratch/y87/fix2/link_probe.before.txt): a link
        to a careers page this tool wrote was listed under left behind, and
        ``--prune`` removed the link and said it had removed the careers page,
        which was still there at the other end.
        """
        cwd = self.switched()
        elsewhere = cwd / "elsewhere.html"
        (cwd / "site" / site.CAREERS_PAGE).rename(elsewhere)
        (cwd / "site" / site.CAREERS_PAGE).symlink_to(elsewhere)
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertNotIn("careers.html", run.out)
        self.assertNotIn("the careers page", collapsed(run.out))
        self.assertTrue((cwd / "site" / site.CAREERS_PAGE).is_symlink())
        self.assertEqual(elsewhere.read_bytes(), self.marked)

    def test_a_multi_build_lists_its_own_page_nowhere_and_prune_keeps_it(self):
        """The scan names the page only when this run writes none.

        Asked without that condition it names the page every multi build has just
        written, and ``--prune`` deletes it, leaving the front page's link opening
        on nothing. Every test above switches the tree to single mode first, so the
        condition went unpinned until /tmp/mutate116.py's first run left that
        mutant standing. Rejected: holding the report alone to naming nothing,
        which a prune that deleted the page without a word would pass.
        """
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        for name in ("data", "site"):
            shutil.copytree(self.base / name, cwd / name)
        shutil.copy2(self.base / "config.toml", cwd / "config.toml")
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertNotIn("careers.html", run.out)
        self.assertEqual((cwd / "site" / site.CAREERS_PAGE).read_bytes(), self.marked)

    def orphan(self, name, **more):
        return site.Orphan(path=Path("site") / name, root=Path("site"),
                           competition=name, label="Tuesday C", generated=STAMP,
                           cards=6, mode="multi", duplicate_of=None, strangers=(),
                           **more)

    def lead(self, *orphans):
        report = build_mod._Report()
        build_mod._left_behind_section(
            report, args=build_mod._parse_args([]),
            cfg=config_mod.Config(path=Path("config.toml")), orphans=list(orphans),
            pruned={}, refusals={}, wrote=True)
        return report.render(quiet=False)

    def test_the_dashboard_paragraphs_are_unchanged_when_the_page_is_listed(self):
        dashboard = self.orphan("2025-winter/2025-winter-tuesday-c")
        page = self.orphan("careers.html", kind="careers")
        alone = self.lead(dashboard)
        both = self.lead(dashboard, page)
        tail = alone[alone.index("  Every directory above"):]
        self.assertTrue(both.endswith(tail), both)
        self.assertLess(both.index("The careers page above adds up"),
                        both.index("Every directory above"))

    def test_a_page_that_lost_its_mark_is_not_removed(self):
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        path = cwd / site.CAREERS_PAGE
        path.write_text("<p>our own page</p>")
        orphan = site.Orphan(path=path, root=cwd, competition="",
                             label="careers across seasons", generated="", cards=0,
                             mode="multi", duplicate_of=None, strangers=(),
                             kind="careers")
        with self.assertRaises(site.SiteError):
            site.remove_orphan(orphan)
        self.assertEqual(path.read_text(), "<p>our own page</p>")

    def test_a_page_prune_could_not_remove_is_its_own_clause(self):
        cwd = self.switched()
        real = site.remove_orphan

        def refuse(orphan):
            if orphan.kind == "careers":
                raise site.SiteError(f"{orphan.path} no longer carries the mark "
                                     f"this tool writes into its pages")
            return real(orphan)

        with mock.patch.object(site, "remove_orphan", side_effect=refuse):
            code, text = main_in_process(cwd, "--prune")
        self.assertEqual(code, 1, text)
        self.assertIn("kept: no longer carries the mark", text)
        self.assertIn("The careers page was not removed; the line under it says "
                      "why.", collapsed(text))
        self.assertIn("careers.html not removed", text)
        self.assertNotIn("dashboard(s) not removed", text)


class TestAOneFileBuildLeavesTheCareersPageAsItIs(unittest.TestCase):
    """``--single-file`` folds the dashboards and never the careers page.

    Its value is its links into each season, which need the site around it; and
    ``--out careers`` gives a fold the page's name, which must not count it.
    """

    def test_the_page_is_written_unfolded_and_not_counted(self):
        cwd = project(*BOTH)
        run = run_build(cwd, "--out", "careers", "--single-file")
        self.assertEqual(run.code, 0, run.text)
        page = cwd / "careers" / site.CAREERS_PAGE
        self.assertTrue(site._is_careers_page(page))
        self.assertFalse(site._is_one_file(page))
        self.assertIn("2 written, one beside each dashboard", collapsed(run.out))


class TestANamesakesLineAcrossGradesIsNotCalledUselessInMultiSeasonMode(unittest.TestCase):
    """In multi mode a namesakes line naming two grades' teams separates two careers.

    The note that calls it useless is true of the season pages, where a name in two
    grades is two players already; on the careers page the two grades are one
    career until that line splits them, so the note's advice would merge two people.
    """

    NAMESAKES = '[namesakes]\n"ada-renn" = ["marina-magpies", "station-starlings"]\n'

    def run_mode(self, multi):
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        write_two_grade_cards(cwd / "data" / "2026-winter", shared="Ada Renn")
        (cwd / "config.toml").write_text((MULTI if multi else "") + self.NAMESAKES,
                                         encoding="utf-8")
        return cwd, run_build(cwd)

    def test_single_mode_still_says_it_separated_nobody(self):
        _, run = self.run_mode(False)
        self.assertIn("separated nobody", collapsed(run.out))

    def test_multi_mode_does_not_and_the_page_has_two_careers(self):
        cwd, run = self.run_mode(True)
        self.assertEqual(run.code, 0, run.text)
        self.assertNotIn("separated nobody", collapsed(run.out))
        ids = read_page(cwd / "site").ids
        self.assertIn("p-ada-renn-marina-magpies", ids)
        self.assertIn("p-ada-renn-station-starlings", ids)
        self.assertNotIn("p-ada-renn", ids)


NAMESAKES = TestANamesakesLineAcrossGradesIsNotCalledUselessInMultiSeasonMode.NAMESAKES


class TestACareersPageSomebodyElseWroteStopsTheRunAndSaysWhy(unittest.TestCase):
    """An unmarked ``site/careers.html`` in multi mode ends the run with exit 2.

    Handled as every ``SiteError`` is: the report so far on stdout, the sentence on
    stderr. Measured, the report so far is the version line alone, because the
    cards section is composed after the write; the spec's claim that stdout
    still carries the cards section is not what main() does.
    """

    def test_the_run_stops_and_touches_nothing(self):
        cwd = project(*BOTH)
        (cwd / "site").mkdir()
        (cwd / "site" / site.CAREERS_PAGE).write_text("<p>our own page</p>")
        run = run_build(cwd)
        self.assertEqual(run.code, 2, run.text)
        self.assertEqual(run.err, "error: site/careers.html is not a page this tool "
                                  "wrote, and the careers page needs that name; move "
                                  "it aside or build into a different --out "
                                  "directory\n")
        self.assertEqual(len(run.out.strip().splitlines()), 1, run.out)
        self.assertFalse((cwd / "site" / "2026-winter").exists())
        self.assertEqual((cwd / "site" / site.CAREERS_PAGE).read_text(),
                         "<p>our own page</p>")
        self.assertNotEqual(run_build(cwd, "--check").code, 2)


class TestTheReadmeQuotesTheCareersLineARunPrints(unittest.TestCase):
    """The README's quoted careers line is what a run prints, in both directions."""

    def test_the_quote_and_the_run_agree(self):
        quoted = [line for line in readme_block(
            self, "### Careers across seasons", contains="careers ")
            if line.startswith("  careers ")]
        _, run = built_two_seasons()
        printed = [line for line in run.out.splitlines()
                   if line.startswith("  careers ")]
        shape = [re.sub(r"\d+", "N", line) for line in printed]
        self.assertTrue(quoted)
        self.assertEqual([re.sub(r"\d+", "N", line) for line in quoted], shape)

    def test_the_readme_names_the_page(self):
        self.assertEqual(site.CAREERS_PAGE, "careers.html")
        self.assertIn("`site/careers.html`",
                      (ROOT / "README.md").read_text(encoding="utf-8"))



# --- a careers page too big for one page (4.116) ------------------------------

LETTERS = tuple("abcdefghijklmnopqrstuvwxyz")


def one_page_bytes(out: Path, name: str = site.CAREERS_PAGE) -> int:
    """The bytes a careers page, ``careers.html`` unless named, holds inside ``<main>``."""
    text = (out / name).read_text(encoding="utf-8")
    body = text[text.index('<main id="main" tabindex="-1">\n') + 31:
                text.index("\n</main>")]
    return len(body.encode("utf-8"))


_ONE_PAGE: dict = {}


def fixture_bytes() -> int:
    """The two-season fixture's careers as one page, written once and measured.

    43,293 bytes when this was written. Measured rather than spelt out, so a
    change to the page's wording moves the limit the tests below patch in with it.
    """
    if "size" not in _ONE_PAGE:
        out = Path(tempfile.mkdtemp(dir=SCRATCH)) / "site"
        write(out, *BOTH)
        _ONE_PAGE["size"] = one_page_bytes(out)
    return _ONE_PAGE["size"]


def over_the_limit():
    """The limit a byte under the fixture's one page, so the fixture is split."""
    return mock.patch.object(site, "CAREERS_LIMIT", fixture_bytes() - 1)


def split_bytes() -> dict[str, int]:
    """The two-season fixture's split, each page's body in bytes, written once.

    The largest were ``careers-c.html`` and ``careers-i.html``, 6,373 bytes
    each, when this was written, and the index 3,607; before it listed the names
    alone the index was the largest, at 8,921.
    """
    if "split" not in _ONE_PAGE:
        out = Path(tempfile.mkdtemp(dir=SCRATCH)) / "site"
        write_split(out, *BOTH)
        _ONE_PAGE["split"] = {name: one_page_bytes(out, name)
                              for name in split_names()}
    return dict(_ONE_PAGE["split"])


def over_the_largest():
    """The limit a byte under the fixture's largest page of the split, so it is past it too."""
    return mock.patch.object(site, "CAREERS_LIMIT", max(split_bytes().values()) - 1)


def write_split(out: Path, *keys: str, **more) -> site.SiteWrite:
    with over_the_limit():
        return write(out, *keys, **more)


def split_names() -> list[str]:
    return [site.CAREERS_PAGE, *site.CAREERS_SPLIT]


def page_of(out: Path, name: str) -> CareersPage:
    return CareersPage((out / name).read_text(encoding="utf-8"))


def careers_on(page: CareersPage) -> list[str]:
    """The slugs a page has a career section for, in the order it prints them."""
    return [slug for slug in page.tables if slug != "index"]


def careers_field(text: str) -> list[str]:
    """The report's careers field: its line and the lines wrapped under it."""
    lines = text.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("  careers "))
    field = [lines[at]]
    for line in lines[at + 1:]:
        if not line.startswith(" " * 15):
            break
        field.append(line)
    return field


class TestAnInitialIsTheFirstLetterTheNamePrints(unittest.TestCase):
    """Which page a career is on: the first letter or digit of the name it prints.

    Measured before (/tmp/t6/scratch/m17/before): the row's ten demo seasons make
    one careers.html of 294,713 bytes for 106 careers, and 30 demo seasons of 12
    teams one of 1,537,058 bytes for 462, past the spec's 1.5 MB, with nothing
    in the report saying so. Split, a career has to be on the page a reader
    would look for it on, from the name alone.

    The printed name and not the slug. Rejected: the slug's first character,
    which is ASCII already and so needs no rule of its own. ``Łuka Quaife`` slugs
    to ``uka-quaife`` and ``李 Wren`` to ``wren``, so both would be filed under a
    letter their names do not start with. And rejected, a table taking Latin
    letters that do not decompose (Ø, Ł) to the letter they look like: a second
    fold of names beside the one the slugs make, for a page the index already
    links every name to.
    """

    def test_a_latin_letter_is_its_own_page_whatever_its_case_or_accent(self):
        for name, key in (("Ana Fernandez", "a"), ("ana fernandez", "a"),
                          ("Émile Renn", "e"), ("émile renn", "e"),
                          ("Ｗei Calder", "w"), ("Zoe Vasse", "z"),
                          ("ẞara Renn", "s")):
            with self.subTest(name=name):
                self.assertEqual(careers.initial(name), key)

    def test_a_digit_is_the_digits_page(self):
        for name in ("4 Tien", "0 Nil", "9 Ling"):
            with self.subTest(name=name):
                self.assertEqual(careers.initial(name), "0-9")

    def test_any_other_letter_is_other_and_so_is_no_letter_at_all(self):
        for name in ("Øyna Vasse", "Łuka Quaife", "李 Wren", "٣ Renn", "—", ""):
            with self.subTest(name=name):
                self.assertEqual(careers.initial(name), "other")
        # The rejected rule, measured: the slug starts with a letter the name does not.
        from matchcentre.slugs import slugify
        self.assertEqual(slugify("Łuka Quaife")[0], "u")
        self.assertEqual(slugify("李 Wren")[0], "w")

    def test_punctuation_before_the_name_is_passed_over(self):
        self.assertEqual(careers.initial("'Ofa Renn"), "o")
        self.assertEqual(careers.initial("(Jo) Calder"), "j")

    def test_a_career_is_filed_by_the_letter_its_name_prints_and_not_its_slug(self):
        found = careers.build([document(
            "2025-winter", player("uka-quaife", "Łuka Quaife", played(1, 1, 0)),
            player("wren", "李 Wren", played(1, 0, 1)),
            player("emile-renn", "Émile Renn", played(1, 1, 1)))])
        page = site.careers_page_name
        index = CareersPage(careers.index_html(found, page=page,
                                               index=site.CAREERS_PAGE))
        self.assertEqual({row["attrs"]["id"]: row["cells"][0]["href"]
                          for row in index.tables["index"]["tbody"]},
                         {"p-emile-renn": "careers-e.html#p-emile-renn",
                          "p-uka-quaife": "careers-other.html#p-uka-quaife",
                          "p-wren": "careers-other.html#p-wren"})
        href = lambda row, slug: f"{row.competition}/index.html#/player/{slug}"
        for key, slugs in (("other", ["uka-quaife", "wren"]), ("e", ["emile-renn"]),
                           ("u", []), ("w", [])):
            shown = CareersPage(careers.initial_html(
                key, found, href=href, page=page, index=site.CAREERS_PAGE))
            self.assertEqual(sorted(careers_on(shown)), slugs, key)

    def test_every_page_has_a_fixed_safe_name_and_none_is_the_index(self):
        """28 names, every one ASCII and lower case, and the same on any file system.

        Fixed rather than made from the names a club has, so no part of a name
        ever reaches a file name, and a page's name is the same on every build.
        """
        self.assertEqual(careers.INITIALS, (*LETTERS, "0-9", "other"))
        self.assertEqual(site.CAREERS_SPLIT,
                         tuple(f"careers-{key}.html" for key in careers.INITIALS))
        for name in site.CAREERS_SPLIT:
            self.assertRegex(name, r"\Acareers-[a-z0-9-]+\.html\Z")
        self.assertEqual(len({name.casefold() for name in split_names()}), 29)


class TestACareersPagePastTheLimitIsAnIndexAndAPageForEachInitial(SiteCase):
    """Past the limit, ``careers.html`` lists everyone and each initial has a page.

    Decided on the bytes the careers come to as one page's body, the part that
    grows, and written for every initial whether anybody's name starts with it
    or not. Rejected: writing only the initials a club has, which leaves the page
    of an initial nobody has any more standing with last season's figures on it,
    linked from nothing.
    """

    def setUp(self):
        super().setUp()
        self.written = write_split(self.out, *BOTH)
        self.index = page_of(self.out, site.CAREERS_PAGE)
        self.names = {career.slug: career.name
                      for career in site.careers_of(documents(*BOTH)[0])}

    def test_the_site_root_holds_the_index_and_a_page_for_every_initial(self):
        self.assertEqual(sorted(p.name for p in self.out.glob("*.html")),
                         sorted(["index.html", *split_names()]))
        for name in split_names():
            self.assertIn(self.out / name, self.written.paths)

    def test_every_career_is_on_the_page_of_its_initial_and_on_no_other(self):
        seen = []
        for key, name in zip(careers.INITIALS, site.CAREERS_SPLIT):
            slugs = careers_on(page_of(self.out, name))
            seen += slugs
            for slug in slugs:
                self.assertEqual(careers.initial(self.names[slug]), key, slug)
        self.assertEqual(sorted(seen), sorted(self.names))
        self.assertEqual(careers_on(self.index), [])

    def test_the_index_links_every_name_to_its_career(self):
        rows = self.index.tables["index"]["tbody"]
        self.assertEqual([row["attrs"]["id"] for row in rows],
                         [f"p-{slug}" for slug in self.names])
        for row in rows:
            slug = row["attrs"]["id"][2:]
            href = row["cells"][0]["href"]
            self.assertEqual(href, f"careers-{careers.initial(self.names[slug])}"
                                   f".html#p-{slug}")
            target, _, anchor = href.partition("#")
            self.assertIn(anchor, page_of(self.out, target).ids)

    def test_every_link_on_every_page_opens_a_file_that_is_there(self):
        for name in split_names():
            page = page_of(self.out, name)
            for href in page.hrefs:
                target, _, anchor = href.partition("#")
                with self.subTest(page=name, href=href):
                    if not target:
                        self.assertIn(anchor, page.ids)
                    else:
                        self.assertTrue((self.out / target).is_file())

    def test_each_page_links_the_index_and_every_initial_anybody_has(self):
        having = {careers.initial(name) for name in self.names.values()}
        for key, name in [(None, site.CAREERS_PAGE),
                          *zip(careers.INITIALS, site.CAREERS_SPLIT)]:
            nav = re.search(r'<nav class="initials".*?</nav>',
                            (self.out / name).read_text(encoding="utf-8"), re.S)
            self.assertIsNotNone(nav, name)
            links = re.findall(r'<a href="([^"#]+)"', nav.group(0))
            self.assertEqual(links, [site.CAREERS_PAGE] + [
                f"careers-{each}.html" for each in careers.INITIALS
                if each in having or each == key], name)
            self.assertEqual(re.findall(r'<a href="([^"]+)" aria-current="page">',
                                        nav.group(0)), [name])

    def test_a_career_on_its_initials_page_is_the_career_one_page_prints(self):
        whole = self.tmp / "whole"
        write(whole, *BOTH)
        one = (whole / site.CAREERS_PAGE).read_text(encoding="utf-8")
        for slug, name in self.names.items():
            page = (self.out / f"careers-{careers.initial(name)}.html").read_text(
                encoding="utf-8")
            career = re.compile(rf'<section class="career" id="p-{slug}".*?'
                                rf'</section>', re.S)
            self.assertEqual(career.search(page).group(0),
                             career.search(one).group(0), slug)

    def test_a_page_with_nobody_under_its_initial_says_so(self):
        page = page_of(self.out, "careers-z.html")
        self.assertEqual(careers_on(page), [])
        # Only that: the seasons the site has are counted over every career on
        # it, so an empty initial is not told there is no season yet.
        self.assertEqual([p for p in page.paragraphs if p[0] == "empty"],
                         [("empty", "No name in the seasons built so far starts "
                                    "with Z.")])
        other = page_of(self.out, "careers-other.html")
        self.assertIn(("empty", "No name in the seasons built so far starts "
                                "with anything but a letter from A to Z or a "
                                "digit from 0 to 9."), other.paragraphs)

    def test_each_initials_page_says_which_names_it_holds(self):
        text = (self.out / "careers-a.html").read_text(encoding="utf-8")
        self.assertIn("<title>Careers across seasons: A · ", text)
        self.assertIn("<h1>Careers across seasons: A</h1>", text)
        self.assertIn('<p>Names starting with A. <a href="careers.html">The '
                      'index</a> lists everyone, each with the page their name '
                      'is on.</p>', text)
        digits = (self.out / "careers-0-9.html").read_text(encoding="utf-8")
        self.assertIn("<h1>Careers across seasons: 0-9</h1>", digits)
        self.assertIn("<p>Names starting with a digit from 0 to 9.", digits)
        other = (self.out / "careers-other.html").read_text(encoding="utf-8")
        self.assertIn("<h1>Careers across seasons: other initials</h1>", other)

    def test_the_index_says_why_it_is_only_an_index(self):
        self.assertIn(("", "There are too many careers here for one page, so "
                           "each initial has a page of its own. An accent is set "
                           "aside, so a name starting with É is under E; one "
                           "starting with any other letter, such as Ø or Ł, is "
                           "under Other. Every name below opens theirs."),
                      self.index.paragraphs)
        text = (self.out / site.CAREERS_PAGE).read_text(encoding="utf-8")
        self.assertIn("<title>Careers across seasons · ", text)

    def test_a_page_that_comes_to_the_limit_exactly_is_still_one_page(self):
        with mock.patch.object(site, "CAREERS_LIMIT", fixture_bytes()):
            write(self.tmp / "at", *BOTH)
        self.assertEqual(sorted(p.name for p in (self.tmp / "at").glob("*.html")),
                         ["careers.html", "index.html"])

    def test_every_page_of_the_split_is_measured_against_the_limit_too(self):
        """A split is not a page brought under the limit: each page is measured.

        The index cost about 455 bytes a name, so at the thirty demo seasons'
        shape of career it passed 1.5 MB alone at about 3,300 careers
        (/tmp/t6/scratch/m17/fix/scale.txt: 1,473,317 bytes at 3,234 careers,
        1,683,313 at 3,696), and nothing said so. Listing the names alone, it is
        the largest initial's page that passes first, ``careers-t.html`` at
        5,082 (/tmp/t7/scratch/CA1/after.txt). Rejected: measuring the one
        page alone and saying in the report that a page is kept to the limit.
        """
        seasons = documents(*BOTH)[0]
        sizes = {name: one_page_bytes(self.out, name) for name in split_names()}
        top = max(sizes.values())
        with over_the_largest():
            self.assertEqual(site.careers_summary(self.out, seasons).split.over,
                             tuple((name, top) for name in split_names()
                                   if sizes[name] == top))
        with mock.patch.object(site, "CAREERS_LIMIT", 0):
            over = site.careers_summary(self.out, seasons).split.over
        self.assertEqual([name for name, _ in over],
                         sorted(split_names(), key=lambda name: -sizes[name]))
        self.assertEqual(dict(over), sizes)


class TestAPlayerPagesLinkLandsOnTheirCareerOnceThePageIsSplit(SiteCase):
    """A season player page links ``careers.html#p-<slug>``, and that still lands.

    ``app.js`` cannot know which initial a name went under, and is not asked:
    Python decides, so the index carries every career's anchor on its row and a
    script of four lines there follows the row's own link on. Opened in headless
    Chrome from file:// and from 127.0.0.1, a player page's link lands on the
    career's heading on its initial's page (/tmp/t6/scratch/m17/chrome-check).
    Rejected: working the initial out again in ``app.js``, a second copy of the
    rule in the one language this suite cannot run; and putting each career's
    page into the season documents, which would rewrite every season's files on
    the night the page splits.
    """

    def setUp(self):
        super().setUp()
        write_split(self.out, *BOTH)
        self.index = (self.out / site.CAREERS_PAGE).read_text(encoding="utf-8")

    def test_the_index_carries_the_anchor_every_player_page_links(self):
        seasons, _, _ = documents(*BOTH)
        ids = CareersPage(self.index).ids
        for season in seasons:
            for entry in season["players"]:
                self.assertEqual(ids.count(f"p-{entry['slug']}"), 1, entry["slug"])

    def test_the_index_follows_that_anchor_on_to_the_career(self):
        from jslint import balance, unterminated, uses_template_literals
        scripts = re.findall(r"<script>(.*?)</script>", self.index, re.S)
        self.assertEqual(len(scripts), 2)
        forward = scripts[1]
        for needed in ("location.hash.slice(1)", "document.getElementById(id)",
                       f".indexOf('{careers.ANCHOR_PREFIX}') === 0",
                       "getElementsByTagName('a')[0]",
                       "location.replace(link.href)"):
            self.assertIn(needed, forward)
        self.assertIsNone(unterminated(forward))
        self.assertIsNone(balance(forward))
        self.assertFalse(uses_template_literals(forward))
        self.assertLess(self.index.index("</table></div>"),
                        self.index.index(forward))

    def test_an_initials_page_has_no_script_but_the_theme_reader(self):
        for name in site.CAREERS_SPLIT:
            text = (self.out / name).read_text(encoding="utf-8")
            self.assertEqual(text.count("<script"), 1, name)
            self.assertNotIn("location.replace", text, name)


class TestASplitCareersPagesListOfPagesWrapsAndItsLandingRowIsMarked(unittest.TestCase):
    """Two rules of the split's styles that nothing in its HTML shows.

    Measured in headless Chrome at 360 wide on thirty demo seasons' split site
    (/tmp/t6/scratch/m17/fix/css-*): as built, the index and ``careers-t.html``
    lay out 360 wide. Without the list's ``flex-wrap`` its 18 links run on in one
    line, and the pages lay out 911 and 907 wide, so a phone opens them zoomed
    out to under half size. Without the ``tr:target`` rule, a player page's link
    followed with scripts off lands on the name's row in a 213KB index with
    nothing to mark it: the row's cells were rgb(35, 35, 34) as built and
    rgba(0, 0, 0, 0) without it.
    """

    def rule(self, selector: str):
        import csslint
        styles = (ROOT / "matchcentre" / "web" / "styles.css").read_text(encoding="utf-8")
        found = [rule for rule in csslint.rules(styles)
                 if rule.selector == selector and not rule.at_rule]
        self.assertTrue(found, f"styles.css has no {selector} rule")
        return found[-1]

    def test_the_list_of_pages_wraps_at_any_width(self):
        rule = self.rule("body.careers .initials ul")
        self.assertEqual(rule.value("display"), "flex")
        self.assertEqual(rule.value("flex-wrap"), "wrap")

    def test_the_row_a_link_lands_on_is_marked(self):
        self.assertEqual(self.rule("body.careers tr:target > *").value("background"),
                         "var(--surface-2)")


class TestTheReportGivesTheCareersPagesSizeOncePastTheLimit(unittest.TestCase):
    """The careers line says what one page would come to, once that is too much.

    Before, the line was the count and nothing else, at 294,713 bytes and at
    1,537,058 alike. Only past the limit, so a club whose page fits reads the
    line it always has; rounded up to a tenth, so a page over the limit never
    reads as one at it. Rejected: a size on every line, a number a club has
    nothing to do about until it is too big.
    """

    COUNTS = careers.Counts(9, 7, 2)
    SAID = "7 of 9 player(s) played in more than one season"

    def test_past_the_limit_the_line_gives_the_size_and_the_split(self):
        split = site.CareersSplit(size=1_534_041, kept=False)
        self.assertEqual(build_mod._careers_line(self.COUNTS, split),
                         f"{self.SAID}. As one page it would be 1.6 MB, over the "
                         f"1.5 MB the page is split at, so careers.html is an index "
                         f"and each initial has a page of its own")

    def test_a_page_of_the_split_still_past_the_limit_is_named_with_its_size(self):
        """The largest page still past the limit, and how many more there are.

        Measured on the thirty seasons' careers copied eleven times over
        (/tmp/t7/scratch/CA1/fix/over.txt), 5,082 careers: careers-t.html, the
        largest initial's page, is 1,511,176 bytes, and the only page over.
        Copied twelve times, 5,544 careers, four more are over too. The index
        passed first while it gave every name its figures, 1,683,313 bytes at
        3,696 careers (/tmp/t6/scratch/m17/fix/scale.txt). Before, the line said
        the page was split and a page kept to 1.5 MB. Rejected: naming every
        page, a list of up to 29 names where the largest and a count say what
        there is to know.
        """
        said = (f"{self.SAID}. As one page it would be 17.0 MB, over the 1.5 MB "
                f"the page is split at, so careers.html is an index and each "
                f"initial has a page of its own. Even split, careers-t.html comes "
                f"to 1.6 MB, over 1.5 MB")
        split = site.CareersSplit(16_955_477, kept=False,
                                  over=(("careers-t.html", 1_511_176),))
        self.assertEqual(build_mod._careers_line(self.COUNTS, split), said)
        split = site.CareersSplit(18_496_744, kept=False,
                                  over=(("careers-t.html", 1_648_284),
                                        ("careers-m.html", 1_572_540),
                                        ("careers-c.html", 1_531_080),
                                        ("careers-b.html", 1_517_244),
                                        ("careers-r.html", 1_505_760)))
        self.assertTrue(build_mod._careers_line(self.COUNTS, split).endswith(
            ". As one page it would be 18.5 MB, over the 1.5 MB the page is split "
            "at, so careers.html is an index and each initial has a page of its "
            "own. Even split, careers-t.html comes to 1.7 MB, over 1.5 MB, and 4 "
            "more of its page(s) do too"))

    def test_a_page_kept_split_under_the_limit_says_why(self):
        split = site.CareersSplit(size=43_293, kept=True)
        self.assertEqual(build_mod._careers_line(self.COUNTS, split),
                         f"{self.SAID}. An earlier build split the page, so "
                         f"careers.html stays an index and each initial keeps a "
                         f"page of its own")

    def test_one_season_and_none_keep_their_words_before_it(self):
        split = site.CareersSplit(size=2_000_000, kept=False)
        self.assertTrue(build_mod._careers_line(careers.Counts(9, 0, 1), split)
                        .startswith("one season so far; totals add up from the "
                                    "second. As one page it would be 2.0 MB"))
        self.assertTrue(build_mod._careers_line(careers.Counts(0, 0, 0), split)
                        .startswith("no season has a result in it yet. As one"))

    def test_a_size_is_rounded_up_to_a_tenth(self):
        self.assertEqual(build_mod._megabytes(1_500_000), "1.5 MB")
        self.assertEqual(build_mod._megabytes(1_500_001), "1.6 MB")
        self.assertEqual(build_mod._megabytes(1_400_000), "1.4 MB")
        self.assertEqual(build_mod._megabytes(43_293), "0.1 MB")
        self.assertEqual(site.CAREERS_LIMIT, 1_500_000)

    def test_a_run_prints_it_wrapped_under_its_label_and_check_says_the_same(self):
        cwd = project(*BOTH)
        with over_the_limit():
            checked = main_in_process(cwd, "--check")
            self.assertFalse((cwd / "site").exists())
            built = main_in_process(cwd)
        self.assertEqual((checked[0], built[0]), (0, 0), built[1])
        field = careers_field(built[1])
        self.assertEqual(careers_field(checked[1]), field)
        self.assertGreater(len(field), 1)
        self.assertLessEqual(max(len(line) for line in field), 78)
        self.assertEqual(collapsed(" ".join(field)),
                         collapsed(CAREERS_LINE) + ". As one page it would be "
                         "0.1 MB, over the 0.1 MB the page is split at, so "
                         "careers.html is an index and each initial has a page "
                         "of its own")

    def test_a_run_names_the_largest_page_once_it_is_past_the_limit_itself(self):
        cwd = project(*BOTH)
        sizes = split_bytes()
        largest = [name for name in split_names()
                   if sizes[name] == max(sizes.values())]
        with over_the_largest():
            code, text = main_in_process(cwd)
        self.assertEqual(code, 0, text)
        field = careers_field(text)
        self.assertLessEqual(max(len(line) for line in field), 78)
        self.assertTrue(collapsed(" ".join(field)).endswith(
            f"each initial has a page of its own. Even split, {largest[0]} comes "
            f"to 0.1 MB, over 0.1 MB" + (f", and {len(largest) - 1} more of its "
                                         f"page(s) do too" if largest[1:] else "")),
            field)

    def test_quiet_hides_it_as_it_hides_the_count(self):
        cwd = project(*BOTH)
        with over_the_limit():
            code, text = main_in_process(cwd, "--quiet")
        self.assertEqual(code, 0, text)
        self.assertFalse([line for line in text.splitlines()
                          if line.startswith("  careers ")], text)
        self.assertNotIn("one page it would be", text)
        self.assertTrue((cwd / "site" / "careers-a.html").is_file())


class TestASplitCareersPageStaysSplitAndRebuildsToTheSameBytes(SiteCase):
    """Once split, a site keeps its pages, and a rebuild changes none of them.

    A page that shrinks back under the limit -- a season taken off the site --
    stays split rather than going back to one, for the reason a one-file page is
    kept up to date once it exists: somebody may have bookmarked or sent
    ``careers-k.html``, and a run that stopped writing it would leave it there
    with old figures on it and nothing linking it. Rejected: going back to one
    page and listing the others as left behind, for a club to prune pages a link
    of theirs may still open.
    """

    def test_a_rebuild_with_nothing_new_writes_nothing(self):
        write_split(self.out, *BOTH)
        before = site_bytes(self.out)
        again = write_split(self.out, *BOTH)
        self.assertEqual(site_bytes(self.out), before)
        self.assertEqual(again.written, ())

    def test_a_page_back_under_the_limit_stays_split(self):
        write_split(self.out, *BOTH)
        before = {name: (self.out / name).read_bytes() for name in split_names()}
        again = write(self.out, *BOTH)
        self.assertEqual({name: (self.out / name).read_bytes()
                          for name in split_names()}, before)
        self.assertEqual(again.written, ())

    def test_the_report_says_why_it_stayed_split(self):
        cwd = project(*BOTH)
        with over_the_limit():
            self.assertEqual(main_in_process(cwd)[0], 0)
        run = run_build(cwd)
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual(collapsed(" ".join(careers_field(run.out))),
                         collapsed(CAREERS_LINE) + ". An earlier build split "
                         "the page, so careers.html stays an index and each "
                         "initial keeps a page of its own")
        self.assertTrue((cwd / "site" / "careers-a.html").is_file())

    def test_a_file_of_the_clubs_under_an_initials_name_does_not_keep_it_split(self):
        seasons = documents(*BOTH)[0]
        self.out.mkdir()
        (self.out / "careers-a.html").write_text("<p>our own page</p>")
        self.assertIsNone(site.careers_summary(self.out, seasons).split)
        write_split(self.tmp / "marked", *BOTH)
        self.assertEqual(site.careers_summary(self.tmp / "marked", seasons).split,
                         site.CareersSplit(size=fixture_bytes(), kept=True))

    def test_one_page_taken_away_is_written_again_and_the_rest_keep_it_split(self):
        """Any page of the split still marked keeps it split, not all of them.

        A club that deletes ``careers-q.html`` by hand, and then builds under
        the limit, would otherwise be given one page again, with the other 27
        left beside it with old figures and nothing linking them, and listed
        nowhere, because the left-behind scan does not look at a build that
        writes the page.
        """
        write_split(self.out, *BOTH)
        before = site_bytes(self.out)
        (self.out / "careers-q.html").unlink()
        again = write(self.out, *BOTH)
        self.assertEqual(site_bytes(self.out), before)
        self.assertEqual(again.written, (self.out / "careers-q.html",))

    def test_a_one_page_careers_html_of_its_own_keeps_nothing_split(self):
        """Only an initial's page keeps a site split, never the one page it had."""
        write(self.out, *BOTH)
        self.assertIsNone(site.careers_summary(self.out, documents(*BOTH)[0]).split)


class TestAnInitialsPageSomebodyElseWroteStopsTheRunBeforeAnythingIs(SiteCase):
    """Every name the careers page may need is checked on every multi-season build.

    The 28 names are refused up front with ``careers.html``, whether tonight's
    page is split or not, so the refusal comes on a night a club is looking and
    in the one error that names every stranger. Rejected: checking them only
    once the page passes the limit, which refuses the unattended run on the
    night the page grows past it, in a second error after the first is fixed.
    """

    def message(self, name):
        return (f"{self.out / name} is not a page this tool wrote, and the careers "
                f"page needs that name; move it aside or build into a different "
                f"--out directory")

    def test_a_file_of_theirs_under_an_initials_name_is_refused_unsplit(self):
        self.out.mkdir()
        (self.out / "careers-q.html").write_text("<p>our own page</p>")
        with self.assertRaises(site.SiteError) as caught:
            write(self.out, *BOTH)
        self.assertEqual(str(caught.exception), self.message("careers-q.html"))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["careers-q.html"])

    def test_every_name_is_refused_in_one_error_with_the_index(self):
        self.out.mkdir()
        for name in ("careers.html", "careers-a.html", "careers-other.html"):
            (self.out / name).write_text("<p>our own page</p>")
        with self.assertRaises(site.SiteError) as caught:
            write_split(self.out, *BOTH)
        self.assertEqual(str(caught.exception).splitlines(),
                         [self.message(name) for name in
                          ("careers.html", "careers-a.html", "careers-other.html")])

    def test_a_link_under_an_initials_name_is_refused_and_not_written_through(self):
        self.out.mkdir()
        elsewhere = self.tmp / "elsewhere.html"
        (self.out / "careers-b.html").symlink_to(elsewhere)
        with self.assertRaises(site.SiteError) as caught:
            write_split(self.out, *BOTH)
        self.assertEqual(str(caught.exception),
                         f"{self.out / 'careers-b.html'} is a link, and "
                         f"the careers page needs that name for a file of "
                         f"its own; move it aside or build into a "
                         f"different --out directory")
        self.assertFalse(elsewhere.exists())

    def test_a_withheld_build_refuses_none_of_them(self):
        self.out.mkdir()
        (self.out / "careers-q.html").write_text("<p>our own page</p>")
        seasons, club, cfg = documents(*BOTH)
        site.write_site(self.out, seasons=seasons, club=club, config=cfg,
                        generated=STAMP, players="numbered")
        self.assertEqual((self.out / "careers-q.html").read_text(),
                         "<p>our own page</p>")


class TestASplitCareersPageLeftBehindIsNamedAndPrunedWhole(unittest.TestCase):
    """The pages a split wrote are one careers page to the left-behind report.

    Listed once, under ``careers.html``, with the number of pages it is, and
    removed whole by ``--prune``: every file of it while each still carries the
    mark, or none of them. Under withheld names each one prints the names the
    run withholds (4.58), so the run exits 1 until they are gone. Rejected:
    listing the 29 files one by one, 29 entries saying the same thing about one
    page; and scanning ``careers.html`` alone, as before, which would leave the 28
    others unnamed, each printing every name it was built with.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = project(*BOTH)
        with over_the_limit():
            code, text = main_in_process(cls.base)
        assert code == 0, text
        assert (cls.base / "site" / "careers-a.html").is_file()

    def copied(self, *, single: bool) -> Path:
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        for name in ("data", "site"):
            shutil.copytree(self.base / name, cwd / name)
        if single:
            shutil.move(str(cwd / "data" / "2025-winter"), str(cwd / "archive"))
            (cwd / "config.toml").write_text("", encoding="utf-8")
        else:
            shutil.copy2(self.base / "config.toml", cwd / "config.toml")
        return cwd

    def pages(self, cwd: Path) -> list[str]:
        return sorted(p.name for p in (cwd / "site").glob("careers*.html"))

    def item(self, lines, path):
        return TestASingleSeasonBuildNamesTheCareersPageItLeftBehind.item(
            self, lines, path)

    def listed(self, lines):
        return [line.strip() for line in lines
                if line.startswith("  site/") and not line.startswith("   ")]

    def test_a_single_season_build_lists_them_as_one_page(self):
        run = run_build(self.copied(single=True))
        self.assertEqual(run.code, 0, run.text)
        lines = section(run.out, "left behind")
        self.assertEqual(self.listed(lines),
                         ["site/2025-winter/" + COMP["2025-winter"],
                          "site/2026-winter/" + COMP["2026-winter"],
                          "site/careers.html"])
        self.assertEqual(self.item(lines, "site/careers.html"),
                         f"careers across seasons, 29 pages -- built {STAMP} only "
                         f"a multi-season build writes a careers page, and this "
                         f"run is in single-season mode")

    def test_prune_removes_every_page_and_counts_them(self):
        cwd = self.copied(single=True)
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual(self.pages(cwd), [])
        self.assertIn("removed 29 file(s)", self.item(section(run.out, "pruned"),
                                                      "site/careers.html"))

    def test_the_pages_alone_are_counted_as_the_files_they_are(self):
        cwd = self.copied(single=True)
        for key in BOTH:
            shutil.rmtree(cwd / "site" / key)
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertIn("29 file(s) removed: the careers page. Nothing this run "
                      "wrote was touched.", collapsed(run.out))
        self.assertEqual(self.pages(cwd), [])

    def test_a_withheld_build_names_them_and_exits_1_until_they_are_gone(self):
        cwd = self.copied(single=False)
        run = run_build(cwd, "--players", "numbered")
        self.assertEqual(run.code, 1, run.text)
        lines = section(run.out, "left behind")
        self.assertEqual(self.listed(lines), ["site/careers.html"])
        self.assertEqual(self.item(lines, "site/careers.html"),
                         f"careers across seasons, 29 pages -- built {STAMP} "
                         f"{WITHHELD_REASON} {NAMES_LINE}")
        self.assertEqual(last_line(run.out),
                         "exit 1: all 12 card(s) scored, no errors and "
                         "careers.html still prints players' names")
        run = run_build(cwd, "--players", "numbered", "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual(self.pages(cwd), [])

    def test_the_initials_pages_without_the_index_are_still_named(self):
        cwd = self.copied(single=True)
        (cwd / "site" / site.CAREERS_PAGE).unlink()
        run = run_build(cwd)
        self.assertEqual(self.item(section(run.out, "left behind"),
                                   "site/careers-a.html"),
                         f"careers across seasons, 28 pages -- built {STAMP} only "
                         f"a multi-season build writes a careers page, and this "
                         f"run is in single-season mode")
        run_build(cwd, "--prune")
        self.assertEqual(self.pages(cwd), [])

    def test_without_the_index_the_last_line_names_the_page_it_listed(self):
        """The exit line names the file the entry is listed under, which is there.

        Measured on the thirty seasons built ``--players initials`` with
        careers.html deleted (/tmp/t6/scratch/m17/noindex.txt): the pages were
        listed under site/careers-a.html, and the run ended ``careers.html
        still prints players' names``, a file that was not there. Rejected:
        ``the careers page still prints players' names``, which would reword
        every withheld run's line for the one case that had it wrong.
        """
        cwd = self.copied(single=False)
        (cwd / "site" / site.CAREERS_PAGE).unlink()
        run = run_build(cwd, "--players", "numbered")
        self.assertEqual(run.code, 1, run.text)
        self.assertEqual(self.listed(section(run.out, "left behind")),
                         ["site/careers-a.html"])
        self.assertEqual(last_line(run.out),
                         "exit 1: all 12 card(s) scored, no errors and "
                         "careers-a.html still prints players' names")

    def test_without_the_index_a_page_prune_could_not_remove_is_named_so(self):
        cwd = self.copied(single=True)
        (cwd / "site" / site.CAREERS_PAGE).unlink()
        real = site.remove_orphan

        def refuse(orphan):
            if orphan.kind == "careers":
                raise site.SiteError(f"{orphan.path} no longer carries the mark "
                                     f"this tool writes into its pages")
            return real(orphan)

        with mock.patch.object(site, "remove_orphan", side_effect=refuse):
            code, text = main_in_process(cwd, "--prune")
        self.assertEqual(code, 1, text)
        self.assertTrue(last_line(text).endswith(" and careers-a.html not removed"),
                        last_line(text))

    def test_a_page_of_the_clubs_among_them_is_neither_counted_nor_removed(self):
        cwd = self.copied(single=True)
        (cwd / "site" / "careers-c.html").write_text("<p>our own page</p>")
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertIn("removed 28 file(s)", self.item(section(run.out, "pruned"),
                                                      "site/careers.html"))
        self.assertEqual(self.pages(cwd), ["careers-c.html"])

    def test_a_page_that_lost_its_mark_since_the_scan_keeps_every_page(self):
        cwd = self.copied(single=True)
        orphan, = [orphan for orphan in site.find_orphans(cwd / "site", keep={})
                   if orphan.kind == "careers"]
        self.assertEqual(orphan.pages, tuple(cwd / "site" / name
                                             for name in split_names()))
        (cwd / "site" / "careers-m.html").write_text("<p>ours now</p>")
        with self.assertRaises(site.SiteError) as caught:
            site.remove_orphan(orphan)
        self.assertEqual(str(caught.exception),
                         f"{cwd / 'site' / 'careers-m.html'} no longer carries the "
                         f"mark this tool writes into its pages")
        self.assertEqual(len(self.pages(cwd)), 29)


class TestACareersPagePastTheSpecsFigureIsSplitByARealBuild(unittest.TestCase):
    """Forty demo seasons of twelve teams, built by build.py with nothing patched.

    The one class that holds a real build to the spec's 1,500,000 bytes: every
    other one patches the limit down to the two-season fixture. Measured on base
    (/tmp/t6/scratch/m17/big/d): 30 such seasons came to one careers.html of
    1,537,058 bytes, too near the limit for a test to lean on. Forty, measured
    with the split (/tmp/t6/scratch/m17/big40), come to 1.9 MB as one page, clear
    of it by more than any change of wording could take off. The run's exit
    status is asked too. Seeds 30 and 34 of that shape each built with two F6
    errors, the reader crediting a retired set to its leader where the model gives
    it to nobody (/tmp/t7/scratch/F1/before/sweep.log), and a test that passed
    while the build it measures exited 1 said nothing of it.
    """

    @classmethod
    def setUpClass(cls):
        cls.cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        for seed in range(1, 41):
            subprocess.run((sys.executable, str(ROOT / "tools" / "demo.py"),
                            "--seed", str(seed), "--teams", "12", "--rounds", "1",
                            "--out", str(cls.cwd / "data" / f"{1980 + seed}-winter")),
                           check=True, capture_output=True, timeout=600)
        (cls.cwd / "config.toml").write_text(MULTI, encoding="utf-8")
        cls.built = run_build(cls.cwd)
        cls.root = cls.cwd / "site"

    def test_the_build_it_measures_is_clean(self):
        self.assertEqual(self.built.code, 0, self.built.out[-1500:])

    def test_the_report_gives_the_size_within_the_width(self):
        field = careers_field(self.built.out)
        self.assertRegex(collapsed(" ".join(field)),
                         r"\Acareers \d+ of \d+ player\(s\) played in more than "
                         r"one season\. As one page it would be \d\.\d MB, over "
                         r"the 1\.5 MB the page is split at, so careers\.html is "
                         r"an index and each initial has a page of its own\Z")
        self.assertLessEqual(max(len(line) for line in field), 78)

    def test_every_page_it_writes_is_under_the_limit(self):
        self.assertEqual(sorted(p.name for p in self.root.glob("careers*.html")),
                         sorted(split_names()))
        for name in split_names():
            self.assertLess((self.root / name).stat().st_size, site.CAREERS_LIMIT)

    def test_every_name_on_the_index_lands_on_its_career(self):
        rows = page_of(self.root, site.CAREERS_PAGE).tables["index"]["tbody"]
        self.assertGreater(len(rows), 400)
        pages = {}
        for row in rows:
            target, _, anchor = row["cells"][0]["href"].partition("#")
            self.assertTrue(target, row["cells"][0]["href"])
            if target not in pages:
                pages[target] = page_of(self.root, target)
            self.assertEqual(anchor, row["attrs"]["id"])
            self.assertIn(anchor[len(careers.ANCHOR_PREFIX):], pages[target].tables)


class TestTheReadmeSaysWhenTheCareersPageIsSplit(unittest.TestCase):
    """The README gives the limit the code splits at, and the pages it splits into."""

    def test_the_readme_gives_the_limit_and_the_names(self):
        prose = readme_prose("### Careers across seasons")
        self.assertIn(f"more than {build_mod._megabytes(site.CAREERS_LIMIT)} as one "
                      f"page", prose)
        self.assertNotIn("kept to", prose)
        self.assertIn(f"`{site.CAREERS_SPLIT[0]}` to `{site.CAREERS_SPLIT[25]}`, "
                      f"`{site.CAREERS_SPLIT[26]}` and `{site.CAREERS_SPLIT[27]}`",
                      prose)


# --- a split careers page's index, its lead, and the report on it -------------

INDEX_LEAD = ("Each player's seasons are added up on the page for their initial, "
              "where every season opens the page its row was copied from. This "
              "page lists the names only.")


def split_index(documents_) -> CareersPage:
    """The split index of hand-built documents, rendered in memory."""
    return CareersPage(careers.index_html(careers.build(documents_),
                                          page=site.careers_page_name,
                                          index=site.CAREERS_PAGE))


class TestASplitCareersIndexListsEachNameAndTheLinkToTheirCareerAlone(SiteCase):
    """A split index row is the name, its link and the anchor, and no figure.

    Measured before on thirty demo seasons of twelve teams, 462 careers
    (/tmp/t7/scratch/CA1/probe.py, before.txt): the index was 213,592 bytes, 462
    rows of the one page's seven figure cells, 3,234 cells and 448.5 bytes a row,
    and copied k times over it passed 1.5 MB by itself at eight, 3,696 careers
    and 1,683,313 bytes, before ``careers-t.html``, the largest initial, did at
    eleven, 5,082. On the two-season fixture the index was the largest page of
    the split, 8,921 bytes to the largest initial's 6,373. Now a row is 127.0
    bytes, the index 63,950 and the fixture's 3,607, and it passes 1.5 MB at 25
    copies, 11,550 careers, after ``careers-t.html`` at the eleven it did
    (after.txt, after2.txt). The figures a row gave are on the initial's page it
    links, in that page's own list and under the career. Rejected:
    ``careers.html`` as a list of the 28 pages alone, since a season page links
    ``careers.html#p-<slug>`` and a slug does not give the initial, which is
    folded from the name; and splitting the index again, a second split for the
    page the first one made.
    """

    def setUp(self):
        super().setUp()
        write_split(self.out, *BOTH)
        self.index = page_of(self.out, site.CAREERS_PAGE)
        self.names = {career.slug: career.name
                      for career in site.careers_of(documents(*BOTH)[0])}

    def test_a_row_is_the_name_its_link_and_the_anchor_a_season_page_links(self):
        text = (self.out / site.CAREERS_PAGE).read_text(encoding="utf-8")
        rows = re.findall(r"^    <tr[ >].*$", text, re.M)
        self.assertEqual(rows, [
            f'    <tr id="p-{slug}"><th scope="row"><a href="careers-'
            f'{careers.initial(name)}.html#p-{slug}">{name}</a></th></tr>'
            for slug, name in self.names.items()])
        self.assertEqual([[cell["text"] for cell in row["cells"]]
                          for row in self.index.tables["index"]["thead"]],
                         [["Player"]])

    def test_the_figures_a_row_gave_are_on_the_initials_page_it_links(self):
        whole = self.tmp / "whole"
        write(whole, *BOTH)
        one = {row["cells"][0]["href"][1:]: fields(row)
               for row in read_page(whole).tables["index"]["tbody"]}
        self.assertEqual(len(one), len(self.names))
        for row in self.index.tables["index"]["tbody"]:
            target, _, anchor = row["cells"][0]["href"].partition("#")
            listed = {each["cells"][0]["href"][1:]: fields(each)
                      for each in page_of(self.out, target).tables["index"]["tbody"]}
            self.assertEqual(listed[anchor], one[anchor], anchor)
            self.assertEqual(len(one[anchor]), len(careers.INDEX_COLUMNS), anchor)

    def test_the_one_pages_list_keeps_every_figure(self):
        whole = self.tmp / "whole"
        write(whole, *BOTH)
        head, = read_page(whole).tables["index"]["thead"]
        self.assertEqual([cell["text"] for cell in head["cells"]],
                         ["Player", *(careers.COLUMNS[key][0]
                                      for key in careers.INDEX_COLUMNS)])
        for row in read_page(whole).tables["index"]["tbody"]:
            self.assertEqual(list(fields(row)), list(careers.INDEX_COLUMNS))

    def test_the_index_is_no_longer_the_largest_page_of_the_split(self):
        sizes = {name: one_page_bytes(self.out, name) for name in split_names()}
        self.assertLess(sizes[site.CAREERS_PAGE],
                        max(sizes[name] for name in site.CAREERS_SPLIT))

    def test_the_readme_says_the_index_lists_the_names(self):
        prose = readme_prose("### Careers across seasons")
        self.assertIn("`careers.html` becomes an index of every name, each linking "
                      "the page for their initial", prose)
        self.assertIn("names any page of the split still more than 1.5 MB on its "
                      "own, as the largest page is at a few thousand careers.", prose)


class TestASplitCareersIndexOpensWithWordsTrueOfTheNamesItLists(unittest.TestCase):
    """The index says it lists names, and the pages that print careers keep the lead.

    Measured before (/tmp/t7/scratch/CA1/before.txt): the thirty seasons' index
    opened with the one page's three paragraphs and then the split's, so a page
    of names said every total was the sum of the rows above it and every row
    named its team and grade, with no season row, team or grade on it; and, one
    season in, ``every total below is that season's`` over a list of no total.
    Now it opens with a paragraph of its own and then the split's (after.txt).
    Each initial's page, which prints the careers, opened with the lead too, and
    keeps it. Rejected: one lead for every view, which the lighter index makes
    false; and the second paragraph kept on the index for the matching of
    names, which the page the name opens says, beside the careers it matched.
    """

    DOCUMENTS = (document("2025-winter", player("ada-renn", "Ada Renn", played(2, 3, 1))),
                 document("2026-winter", player("ada-renn", "Ada Renn", played(1, 2, 0)),
                          player("zoe-vasse", "Zoe Vasse", played(1, 0, 2))))

    def test_the_index_says_it_lists_names_and_where_their_seasons_are(self):
        self.assertEqual(split_index(self.DOCUMENTS).paragraphs,
                         [("", INDEX_LEAD), ("", careers._SPLIT)])

    def test_one_season_in_the_index_says_nothing_of_a_total(self):
        one = self.DOCUMENTS[1:]
        self.assertEqual(split_index(one).paragraphs,
                         [("", INDEX_LEAD), ("", careers._SPLIT)])
        found = careers.build(one)
        for view in (careers.body_html(found, href=lambda row, slug: "x"),
                     careers.initial_html("a", found, href=lambda row, slug: "x",
                                          page=site.careers_page_name,
                                          index=site.CAREERS_PAGE)):
            self.assertIn(("muted", careers._ONE_SEASON), CareersPage(view).paragraphs)

    def test_no_season_yet_is_still_said_on_the_index(self):
        empty = document("2026-winter", player("ada-renn", "Ada Renn"))
        self.assertEqual(split_index([empty]).paragraphs,
                         [("", INDEX_LEAD), ("", careers._SPLIT),
                          ("empty", careers._NO_SEASON)])

    def test_the_one_page_and_each_initials_page_keep_the_whole_lead(self):
        found = careers.build(self.DOCUMENTS)
        lead = [("", text) for text in careers._INTRO]
        one = CareersPage(careers.body_html(found, href=lambda row, slug: "x"))
        self.assertEqual(one.paragraphs, lead)
        for key in ("a", "z"):
            page = CareersPage(careers.initial_html(
                key, found, href=lambda row, slug: "x",
                page=site.careers_page_name, index=site.CAREERS_PAGE))
            self.assertEqual(page.paragraphs[1:len(lead) + 1], lead, key)
            self.assertTrue(page.paragraphs[0][1].startswith("Names starting with "))
            self.assertNotIn(("", INDEX_LEAD), page.paragraphs, key)
        self.assertNotIn(("", INDEX_LEAD), one.paragraphs)


class TestABuildWorksTheCareersOutOnceAndReportsTheSplitItWrote(unittest.TestCase):
    """The report's careers line is read off what the write returned.

    Measured before, in process on thirty demo seasons (/tmp/t7/scratch/CA1/
    probe.py, before.txt): a build called ``site.careers_of`` twice and rendered
    all 29 pages twice, 0.132 s and 0.120 s of a 6.05 s build, once for the page
    and again for the report's line, which read the split's mark back off the
    disk the write had just changed; ``--check`` did each once. The two agreed,
    by being the same function run twice. Now ``write_site`` returns the count
    and the split it wrote, and only a run that wrote nothing, ``--check`` or a
    write that stopped, works them out for the line: a build calls each once
    (after.txt). Rejected: keeping the second rendering, one rule run twice for
    one answer.
    """

    def calls(self, cwd: Path, *argv: str):
        with over_the_limit(), \
                mock.patch.object(site, "careers_of",
                                  wraps=site.careers_of) as found, \
                mock.patch.object(site, "_careers_rendered",
                                  wraps=site._careers_rendered) as rendered:
            code, text = main_in_process(cwd, *argv)
        self.assertEqual(code, 0, text)
        return found.call_count, rendered.call_count, careers_field(text)

    def test_a_build_works_the_careers_out_and_renders_them_once(self):
        cwd = project(*BOTH)
        checked = self.calls(cwd, "--check")
        self.assertEqual(checked[:2], (1, 1))
        self.assertEqual(self.calls(cwd), (1, 1, checked[2]))
        self.assertEqual(self.calls(cwd), (1, 1, checked[2]))
        self.assertTrue(collapsed(" ".join(checked[2])).endswith(
            "so careers.html is an index and each initial has a page of its own"))

    def test_the_write_returns_the_count_and_the_split_it_wrote(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            out = Path(tmp)
            counts = careers.Counts(9, 7, 2)
            self.assertEqual(write(out / "one", *BOTH).careers,
                             site.CareersSummary(counts, None))
            self.assertEqual(write_split(out / "split", *BOTH).careers,
                             site.CareersSummary(counts, site.CareersSplit(
                                 fixture_bytes(), kept=False)))
            self.assertEqual(write(out / "split", *BOTH).careers,
                             site.CareersSummary(counts, site.CareersSplit(
                                 fixture_bytes(), kept=True)))
            self.assertEqual(site.careers_summary(out / "split", documents(*BOTH)[0]),
                             write(out / "split", *BOTH).careers)
            self.assertIsNone(write(out / "single", "2026-winter",
                                    multi=False).careers)

    def test_the_line_says_what_the_write_returned(self):
        # A count and a split the fixture cannot give, where it gives 7 of 9 players
        # over 2 seasons and no page over, so the line can only be what was returned.
        told = site.CareersSummary(careers.Counts(11, 5, 3), site.CareersSplit(
            2_000_000, kept=False, over=(("careers-q.html", 1_600_000),)))
        real = site.write_site

        def wrote(*args, **kwargs):
            return real(*args, **kwargs)._replace(careers=told)

        cwd = project(*BOTH)
        with mock.patch.object(site, "write_site", side_effect=wrote):
            code, text = main_in_process(cwd)
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(p.name for p in (cwd / "site").glob("*.html")),
                         ["careers.html", "index.html"])
        self.assertEqual(collapsed(" ".join(careers_field(text))),
                         "careers 5 of 11 player(s) played in more than one "
                         "season. As one page it would be "
                         "2.0 MB, over the 1.5 MB the page is split at, so "
                         "careers.html is an index and each initial has a page "
                         "of its own. Even split, careers-q.html comes to 1.6 MB, "
                         "over 1.5 MB")

    def test_a_write_that_stopped_still_says_the_split(self):
        cwd = project(*BOTH)
        checked = self.calls(cwd, "--check")[2]
        with over_the_limit(), mock.patch.object(
                site, "write_site", side_effect=OSError(28, "No space left on device")):
            code, text = main_in_process(cwd)
        self.assertEqual(code, 2, text)
        self.assertEqual(careers_field(text), checked)


# --- a build that withholds players' names ------------------------------------

# Each way a run can withhold names, as (mode, how): the line in [build], or the
# flag for one run.
WITHHELD = (("numbered", "--players"), ("initials", "--players"),
            ("numbered", "[build] players"), ("initials", "[build] players"))
WITHHELD_LINE = ("  careers      no page: this run withholds names, and a label is one "
                 "season's")
WITHHELD_REASON = ("only a build that prints players' names writes a careers page: "
                   "a withheld label belongs to one season, not to a person")
NAMES_LINE = "it prints players' names, and this run withholds them"


def withheld_project(mode: str, how: str, *keys: str) -> tuple[Path, tuple]:
    """A multi project whose run withholds names by *how*, and the argv it takes."""
    if how == "--players":
        return project(*keys), ("--players", mode)
    return project(*keys, config=f'players = "{mode}"\n'), ()


def last_line(text: str) -> str:
    """The report's last line, from ``  exit`` to the end, run together."""
    lines = text.splitlines()
    at = max(i for i, line in enumerate(lines) if line.startswith("  exit "))
    return collapsed("\n".join(lines[at:]))


def site_bytes(out: Path) -> dict[str, bytes]:
    return {str(path.relative_to(out)): path.read_bytes()
            for path in sorted(out.rglob("*")) if path.is_file()}


def readme_prose(heading: str) -> str:
    """One README section run together, to the next heading as deep or deeper."""
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    depth = len(heading) - len(heading.lstrip("#"))
    body, fenced = [], False
    for line in lines[lines.index(heading) + 1:]:
        if line.startswith("```"):
            fenced = not fenced
        elif (not fenced and line.startswith("#")
              and len(line) - len(line.lstrip("#")) <= depth):
            break
        body.append(line)
    return collapsed("\n".join(body))


class TestABuildThatWithholdsNamesWritesNoCareersPage(unittest.TestCase):
    """Under ``players = "numbered"`` or ``"initials"`` there is no careers page.

    A person on the careers page is a slug, and under withholding the slug is a
    label counted per season, so one label can be two people: on this fixture
    Parkview Wanderers #2 is Kit Vasse in 2025 and Robin Quaife in 2026, and
    nothing on either season's pages says whether they are one person or two.
    Measured before (/tmp/t4/scratch/z462/probe.before.txt): every withheld
    two-season build, numbered or initials, by ``[build] players`` or by
    ``--players``, exited 0, wrote careers.html, linked it from the front page,
    reported "7 of 9 player(s) played in more than one season", and summed those
    two people into one career, headed "Parkview Wanderers #2", or "R.Q." under
    initials.

    Rejected: joining on the real slugs and heading each career with a pseudonym
    of its own, whose labels could match no season page and which would publish a
    link between two seasons that no withheld season page makes; and writing the
    page with a warning, which publishes the wrong figure with a caveat.
    """

    def test_the_decider_writes_it_only_for_a_multi_build_printing_names(self):
        multi = config_mod.Config(path=Path("config.toml"), seasons_mode="multi")
        single = config_mod.Config(path=Path("config.toml"))
        self.assertTrue(careers.writes_careers(multi, "names"))
        for mode in ("numbered", "initials"):
            self.assertFalse(careers.writes_careers(multi, mode), mode)
        for mode in ("names", "numbered", "initials"):
            self.assertFalse(careers.writes_careers(single, mode), mode)

    def test_each_way_of_withholding_writes_no_page_and_links_none(self):
        for mode, how in WITHHELD:
            with self.subTest(mode=mode, how=how):
                cwd, argv = withheld_project(mode, how, *BOTH)
                run = run_build(cwd, *argv)
                self.assertEqual(run.code, 0, run.text)
                self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())
                self.assertNotIn(site.CAREERS_PAGE, (cwd / "site" / "index.html")
                                 .read_text(encoding="utf-8"))
                self.assertIn("  players      withheld", run.out)

    def test_nobody_is_added_up_across_seasons_at_all(self):
        """No career is built, for the page or for the report's count.

        So no two people can be summed, whatever their labels: ``careers.build`` is
        the one place rows are joined, and a withheld run never reaches it. The names
        build first, so the spy is shown to see the join when there is one.
        """
        cwd = project(*BOTH)
        with mock.patch.object(careers, "build", wraps=careers.build) as spy:
            code, text = main_in_process(cwd)
        self.assertEqual(code, 0, text)
        self.assertTrue(spy.called)
        for mode, how in WITHHELD:
            with self.subTest(mode=mode, how=how):
                cwd, argv = withheld_project(mode, how, *BOTH)
                with mock.patch.object(careers, "build", wraps=careers.build) as spy:
                    code, text = main_in_process(cwd, *argv)
                self.assertEqual(code, 0, text)
                spy.assert_not_called()

    def test_a_names_build_over_a_withholding_config_still_writes_it(self):
        cwd = project(*BOTH, config='players = "initials"\n')
        run = run_build(cwd, "--players", "names")
        self.assertEqual(run.code, 0, run.text)
        self.assertIn("Robin Quaife", read_page(cwd / "site").headings.values())
        self.assertIn(CAREERS_LINE, run.out.splitlines())

    def test_a_page_of_the_clubs_own_by_that_name_is_left_alone(self):
        """As in single-season mode: no page to write is no name to need.

        Before, the refusal was asked of the same decider, so a withheld run over a
        ``careers.html`` of the club's own stopped with exit 2 for a page it was
        never going to write.
        """
        cwd, argv = withheld_project("numbered", "--players", *BOTH)
        (cwd / "site").mkdir()
        (cwd / "site" / site.CAREERS_PAGE).write_text("<p>our own page</p>")
        run = run_build(cwd, *argv)
        self.assertEqual(run.code, 0, run.text)
        self.assertNotIn("careers.html", run.out + run.err)
        self.assertEqual((cwd / "site" / site.CAREERS_PAGE).read_text(),
                         "<p>our own page</p>")

    def test_the_readme_says_so_where_the_page_and_the_setting_are_described(self):
        for heading in ("### Careers across seasons", "## Publishing without names"):
            with self.subTest(heading=heading):
                prose = readme_prose(heading)
                self.assertIn("withholds players' names writes no careers page",
                              prose)
                self.assertIn("belongs to one season", prose)

    def test_no_shipped_promise_of_the_page_leaves_withholding_out(self):
        """Each place that says multi-season mode brings the page says when it does not.

        Three said it without a limit after the two sections above gained theirs:
        the README's list of what multi-season mode changes, the first sentence of
        its careers section, and the ``seasons`` comment in config.example.toml,
        the file a club copies its settings from.
        """
        readme = collapsed((ROOT / "README.md").read_text(encoding="utf-8"))
        example = collapsed((ROOT / "config.example.toml").read_text(encoding="utf-8")
                            .replace("\n#", "\n"))
        for text, promise in ((readme, "- the site gains a careers page"),
                              (readme, "In multi-season mode the site has one more "
                                       "page"),
                              (example, "a season picker and a careers page")):
            with self.subTest(promise=promise):
                said = re.split(r"[.;](?:\s|$)", text[text.index(promise):], 1)[0]
                self.assertIn("withholds players' names", said)


class TestANamesMultiSeasonBuildIsTheBuildItWas(unittest.TestCase):
    """Printing names, a multi build writes what it always wrote, careers page and all.

    Measured across the fix (/tmp/t4/scratch/z462/trees-before and trees-after):
    ``diff -r`` of the names builds, multi and single, and of their reports, finds
    only the copied app.js, whose one change is the careers link's guard and the
    comment over it (a one-file build's page, which embeds the script, differs by
    the same lines). Pinned here
    the way ``test_anonymise`` pins a single-season names build:
    ``players = "names"``, and ``--players names`` over a config that withholds,
    are each the bytes of a build with no setting.
    """

    def test_every_way_of_printing_names_is_the_same_site(self):
        plain = project(*BOTH)
        self.assertEqual(run_build(plain).code, 0)
        expected = site_bytes(plain / "site")
        self.assertIn(site.CAREERS_PAGE, expected)
        for config, argv in (('players = "names"\n', ()),
                             ('players = "numbered"\n', ("--players", "names"))):
            with self.subTest(config=config, argv=argv):
                cwd = project(*BOTH, config=config)
                run = run_build(cwd, *argv)
                self.assertEqual(run.code, 0, run.text)
                self.assertEqual(site_bytes(cwd / "site"), expected)


class TestAWithheldMultiSeasonReportSaysWhyThereIsNoCareersPage(unittest.TestCase):
    """Where the careers line goes, a withheld multi run says it wrote no page, and why.

    A club that switches withholding on in multi mode loses a page, and a page that
    goes without a word looks like a fault. So the line takes the place of the
    count, under ``seasons``, under ``--check`` as well, and is hidden by
    ``--quiet`` as the count is. Single-season mode has no careers line either way.
    Measured before: every withheld two-season run printed the count, "7 of 9
    player(s) played in more than one season", for a page that summed two people.
    """

    def lines(self, run):
        return [line for line in run.out.splitlines() if line.startswith("  careers ")]

    def test_the_line_sits_directly_under_the_seasons_line(self):
        for mode, how in (("numbered", "--players"), ("initials", "[build] players")):
            with self.subTest(mode=mode, how=how):
                cwd, argv = withheld_project(mode, how, *BOTH)
                run = run_build(cwd, *argv)
                lines = run.out.splitlines()
                at = lines.index("  seasons      multi: 2025-winter, 2026-winter")
                self.assertEqual(lines[at + 1], WITHHELD_LINE)
                self.assertEqual(self.lines(run), [WITHHELD_LINE])

    def test_check_says_it_too(self):
        cwd, argv = withheld_project("numbered", "--players", *BOTH)
        run = run_build(cwd, "--check", *argv)
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual(self.lines(run), [WITHHELD_LINE])

    def test_one_season_says_it_too(self):
        run = run_build(project("2026-winter"), "--players", "initials")
        self.assertEqual(self.lines(run), [WITHHELD_LINE])

    def test_quiet_and_single_mode_print_none(self):
        cwd, argv = withheld_project("numbered", "--players", *BOTH)
        self.assertEqual(self.lines(run_build(cwd, "--quiet", *argv)), [])
        run = run_build(project("2026-winter", multi=False), "--players", "numbered")
        self.assertEqual(run.code, 0, run.text)
        self.assertEqual(self.lines(run), [])

    def test_a_run_with_no_season_says_nothing_of_careers(self):
        """No season is no careers page in either mode, and withholding is not why.

        A names run with nothing scored prints no careers line, so a withheld one
        does not print a reason for a page it would not have had.
        """
        for mode in ("names", "numbered"):
            with self.subTest(mode=mode):
                cwd = project()
                (cwd / "data" / "2026-winter").mkdir()
                (cwd / "data" / "2026-winter" / "bad.pdf").write_text("not a pdf")
                run = run_build(cwd, "--players", mode)
                self.assertIn("  seasons      multi: none", run.out.splitlines())
                self.assertEqual(self.lines(run), [])


class TestAWithheldBuildNamesTheCareersPageANamesBuildLeft(unittest.TestCase):
    """A careers page a names build wrote is left behind by a withheld build, loudly.

    The withheld build writes no page, so the one the names build left stays, with
    every name in it, and it opens by double-click: the promise a withheld run
    makes about ``--out`` is not kept while it is there. So it is listed under left
    behind with a reason of its own, and y85's names line, and the run exits 1
    until ``--prune`` removes it. The last line gives it a clause of its own and
    does not count it as a dashboard, and when ``--prune`` could not remove it that
    is still one clause, because it is one file still printing names.

    Measured before (/tmp/t4/scratch/z462/probe.before.txt): the numbered build
    wrote over the page with labels, summing two people, and exited 0 with nothing
    listed. And (probe2.before.txt) a withheld single-season build over a names
    multi site counted the page as one of "3 dashboard(s) still print players'
    names". And (probe3.before.txt) the command a ``--players numbered`` run gave
    to paste had no ``--players`` in it: pasted, it was a names build, which
    wrote every name back into the dashboard and the careers page and exited 0.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = project(*BOTH)
        run = run_build(cls.base)
        assert run.code == 0, run.text
        cls.marked = (cls.base / "site" / site.CAREERS_PAGE).read_bytes()

    def copied(self, config: str = "", archive: bool = False) -> Path:
        """The names tree, *config* added to its [build], and 2025 archived if asked."""
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        for name in ("data", "site"):
            shutil.copytree(self.base / name, cwd / name)
        (cwd / "config.toml").write_text(MULTI + config, encoding="utf-8")
        if archive:
            shutil.move(str(cwd / "data" / "2025-winter"), str(cwd / "archive"))
        return cwd

    def item(self, lines, path):
        return TestASingleSeasonBuildNamesTheCareersPageItLeftBehind.item(
            self, lines, path)

    def listed(self, lines):
        return [line.strip() for line in lines
                if line.startswith("  site/") and not line.startswith("   ")]

    def test_the_page_is_listed_printing_names_and_the_run_exits_1(self):
        for mode, how in (("numbered", "--players"), ("initials", "[build] players")):
            with self.subTest(mode=mode, how=how):
                config = f'players = "{mode}"\n' if how != "--players" else ""
                cwd = self.copied(config)
                argv = ("--players", mode) if how == "--players" else ()
                run = run_build(cwd, *argv)
                self.assertEqual(run.code, 1, run.text)
                lines = section(run.out, "left behind")
                self.assertEqual(self.listed(lines), ["site/careers.html"])
                self.assertEqual(self.item(lines, "site/careers.html"),
                                 f"careers across seasons -- built {STAMP} "
                                 f"{WITHHELD_REASON} {NAMES_LINE}")
                self.assertEqual(last_line(run.out),
                                 "exit 1: all 12 card(s) scored, no errors and "
                                 "careers.html still prints players' names")
                self.assertEqual((cwd / "site" / site.CAREERS_PAGE).read_bytes(),
                                 self.marked)

    def test_the_page_alone_has_a_lead_of_its_own(self):
        """What to do, and why it matters although nothing links it: said once.

        The reason and y85's line above the lead already say that only a build
        printing names writes the page and that this run withholds them. The
        lead as first written said both again, so the one page carried the same
        point three times in six lines (the review of z462).
        """
        run = run_build(self.copied('players = "numbered"\n'))
        text = collapsed("\n".join(section(run.out, "left behind")))
        self.assertIn("it prints players' names, and this run withholds them "
                      "Nothing under site links the careers page above any more, "
                      "but it opens by double-click and shows every name it was "
                      "built with, which is why this run names it. To delete it: "
                      "python3 build.py --prune", text)
        self.assertEqual(text.count("withholds"), 1, text)
        self.assertNotIn("single-season", text)
        self.assertNotIn("Every directory above", text)

    def test_prune_removes_it_and_the_run_exits_0(self):
        cwd = self.copied('players = "initials"\n')
        run = run_build(cwd, "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())
        self.assertIn("removed 1 file(s)", self.item(section(run.out, "pruned"),
                                                     "site/careers.html"))
        self.assertIn("1 file(s) removed: the careers page. Nothing this run wrote "
                      "was touched.", collapsed(run.out))
        self.assertEqual(last_line(run.out), "exit 0: all 12 card(s) scored, no errors")
        again = run_build(cwd)
        self.assertEqual(again.code, 0, again.text)
        self.assertNotIn("== left behind", again.out)

    def test_the_command_it_gives_withholds_names_as_this_run_did(self):
        """Pasted as printed, the command prunes, and puts no name back."""
        cwd = self.copied(archive=True)
        run = run_build(cwd, "--players", "numbered")
        self.assertEqual(run.code, 1, run.text)
        hint = "python3 build.py --prune --players numbered"
        self.assertIn(f"    {hint}", run.out.splitlines())
        pasted = run_build(cwd, *hint.split()[2:])
        self.assertEqual(pasted.code, 0, pasted.text)
        self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())
        self.assertFalse((cwd / "site" / "2025-winter").exists())
        document = json.loads((cwd / "site" / "2026-winter" / COMP["2026-winter"]
                               / "season.json").read_text(encoding="utf-8"))
        self.assertEqual(document["meta"]["players"], "numbered")

    def test_with_a_dashboard_left_too_each_is_its_own_clause(self):
        cwd = self.copied('players = "numbered"\n', archive=True)
        run = run_build(cwd)
        self.assertEqual(run.code, 1, run.text)
        lines = section(run.out, "left behind")
        self.assertEqual(self.listed(lines), ["site/2025-winter/" + COMP["2025-winter"],
                                              "site/careers.html"])
        text = collapsed("\n".join(lines))
        self.assertIn("The command below removes the careers page above with the "
                      "dashboards. Every directory above is a complete dashboard",
                      text)
        self.assertNotIn("single-season", text)
        self.assertEqual(last_line(run.out),
                         "exit 1: all 6 card(s) scored, no errors, 1 dashboard(s) "
                         "still print players' names and careers.html still prints "
                         "players' names")

    def test_a_page_prune_could_not_remove_is_one_clause(self):
        cwd = self.copied('players = "numbered"\n')
        real = site.remove_orphan

        def refuse(orphan):
            if orphan.kind == "careers":
                raise site.SiteError(f"{orphan.path} no longer carries the mark "
                                     f"this tool writes into its pages")
            return real(orphan)

        with mock.patch.object(site, "remove_orphan", side_effect=refuse):
            code, text = main_in_process(cwd, "--prune")
        self.assertEqual(code, 1, text)
        self.assertIn("kept: no longer carries the mark", text)
        self.assertEqual(last_line(text), "exit 1: all 12 card(s) scored, no errors "
                                          "and careers.html still prints players' "
                                          "names")

    def test_a_withheld_single_build_does_not_count_the_page_as_a_dashboard(self):
        cwd = self.copied(archive=True)
        (cwd / "config.toml").write_text("", encoding="utf-8")
        run = run_build(cwd, "--players", "numbered")
        self.assertEqual(run.code, 1, run.text)
        lines = section(run.out, "left behind")
        self.assertEqual(self.item(lines, "site/careers.html"),
                         f"careers across seasons -- built {STAMP} only a "
                         f"multi-season build writes a careers page, and this run "
                         f"is in single-season mode {NAMES_LINE}")
        self.assertEqual(last_line(run.out),
                         "exit 1: all 6 card(s) scored, no errors, 2 dashboard(s) "
                         "still print players' names and careers.html still prints "
                         "players' names")

    def test_a_names_build_over_it_lists_nothing(self):
        run = run_build(self.copied(), "--prune")
        self.assertEqual(run.code, 0, run.text)
        self.assertNotIn("careers.html", run.out)


class TestACheckRunSaysABuildWouldLeaveNothingLinkingTheCareersPage(unittest.TestCase):
    """Both leads of a careers page listed alone, in the tense of ``--check``.

    Measured before (/tmp/t6/scratch/m6/before.txt): a withheld multi run with
    ``--check`` said "Nothing under site links the careers page above any more",
    and a single-season ``--check`` over a multi site whose dashboards had been
    deleted by hand said "Nothing under site links it any more", each over a
    site/index.html that still linked careers.html, since ``--check`` rewrites
    no front page. Rejected: a lead of its own for ``--check`` beside each of the
    three a copy left behind can get, which would be six leads where there are
    three.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = project(*BOTH)
        run = run_build(cls.base)
        assert run.code == 0, run.text

    def copied(self, config: str) -> Path:
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        for name in ("data", "site"):
            shutil.copytree(self.base / name, cwd / name)
        (cwd / "config.toml").write_text(config, encoding="utf-8")
        return cwd

    def lead(self, cwd: Path, *argv: str, code: int) -> str:
        run = run_build(cwd, "--check", *argv)
        self.assertEqual(run.code, code, run.text)
        front = (cwd / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn(site.CAREERS_PAGE, front,
                      "the front page does not link the page, so the fixture "
                      "cannot tell the two sentences apart")
        return collapsed("\n".join(section(run.out, "left behind")))

    def test_the_withheld_lead(self):
        text = self.lead(self.copied(MULTI), "--players", "numbered", code=1)
        self.assertIn("A build would leave nothing under site linking the careers "
                      "page above, but it opens by double-click and shows every "
                      "name it was built with, which is why this run names it. To "
                      "delete it: python3 build.py --prune --players numbered", text)
        self.assertNotIn("any more", text)

    def single(self) -> Path:
        """A multi site whose dashboards were deleted by hand, built as one season."""
        cwd = self.copied("")
        for key in BOTH:
            shutil.rmtree(cwd / "site" / key)
        shutil.move(str(cwd / "data" / "2025-winter"), str(cwd / "archive"))
        return cwd

    def test_the_single_season_lead(self):
        text = self.lead(self.single(), code=0)
        self.assertIn("did not write it. A build would leave nothing under site "
                      "linking it, but it opens by double-click and shows the "
                      "figures it was built with, which is why this run names "
                      "it. To delete it: python3 build.py --prune", text)
        self.assertNotIn("any more", text)

    def test_a_build_of_the_same_copy_says_nothing_links_it_any_more(self):
        """The other tense of both leads, from the run that rewrote the page."""
        for what, cwd, argv, code in (
                ("the careers page above", self.copied(MULTI),
                 ("--players", "numbered"), 1),
                ("it", self.single(), (), 0)):
            with self.subTest(what=what):
                run = run_build(cwd, *argv)
                self.assertEqual(run.code, code, run.text)
                front = (cwd / "site" / "index.html").read_text(encoding="utf-8")
                self.assertNotIn(site.CAREERS_PAGE, front)
                self.assertIn(f"Nothing under site links {what} any more, but it "
                              f"opens by double-click",
                              collapsed("\n".join(section(run.out, "left behind"))))


class TestUnderWithheldNamesTheNamesakesNoteStaysTheMultiSeasonOne(unittest.TestCase):
    """A withheld multi build keeps the multi-season silence about such a line.

    It writes no careers page, so in this run the line renames a player and
    separates nobody. But the note is advice about a line of the config, and the
    same config built with ``--players names``, the one-run flag the README
    offers, writes a careers page, where the line is what gives two people two
    careers. As first written the fix gave the single-season note here, "Leave it
    off". Measured after the review of z462 found it
    (/tmp/t4/scratch/z462/fixup/probe.before.txt): under ``[build] players =
    "numbered"`` and under ``--players numbered`` alike, a club that left the line
    off as told, then built with ``--players names``, got one career for the two
    Ada Renns (``p-ada-renn``), where the line kept gave two. Rejected: a note of
    its own saying the line matters only to a names build, which asks the club to
    do nothing; and the single-season note, true of the run and not of the config
    it advises on.
    """

    def run_with(self, config: str, *argv: str):
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        write_two_grade_cards(cwd / "data" / "2026-winter", shared="Ada Renn")
        (cwd / "config.toml").write_text(MULTI + config + NAMESAKES,
                                         encoding="utf-8")
        return cwd, run_build(cwd, *argv)

    def test_a_withheld_multi_build_does_not_call_it_useless(self):
        for config, argv in (("", ("--players", "numbered")),
                             ('players = "initials"\n', ())):
            with self.subTest(config=config, argv=argv):
                cwd, run = self.run_with(config, *argv)
                self.assertEqual(run.code, 0, run.text)
                self.assertNotIn("separated nobody", collapsed(run.out))
                self.assertFalse((cwd / "site" / site.CAREERS_PAGE).exists())

    def test_a_names_build_of_the_same_config_is_told_nothing_and_splits_them(self):
        cwd, run = self.run_with('players = "initials"\n', "--players", "names")
        self.assertEqual(run.code, 0, run.text)
        self.assertNotIn("separated nobody", collapsed(run.out))
        ids = read_page(cwd / "site").ids
        self.assertIn("p-ada-renn-marina-magpies", ids)
        self.assertIn("p-ada-renn-station-starlings", ids)
        self.assertNotIn("p-ada-renn", ids)


class TestEveryCommandTheReportHandsOverCarriesThisRunsPlayers(unittest.TestCase):
    """A command the report gives a ``--players`` run to paste says ``--players`` too.

    Before, a run given ``--players numbered`` was told to paste ``python3 build.py
    --prune``, and pasted it: that run built with the config's names, found
    nothing to prune, rewrote ``careers.html`` with Robin Quaife on it and left
    the 2026 dashboard printing names, under the folder the first run had just
    withheld them from, and it exited 0. The ``--discover --force`` offer was the
    same command without the flag, and ``--discover`` builds as well.

    One function writes every such command, so the flag is added there once. Only
    a flag given: ``[build] players`` is read by the pasted command as it was by
    this one. Rejected: saying in words that the flag has to be added by hand,
    which is a second sentence to read next to a line that is meant to be pasted.
    """

    OFFER = "    python3 build.py --discover --force"

    def offers(self, config: str, *argv: str) -> list[str]:
        cwd = project(*BOTH, config=config)
        run = run_build(cwd, *argv)
        self.assertEqual(run.code, 0, run.text)
        return [line for line in run.out.splitlines()
                if line.startswith(self.OFFER)]

    def test_the_discover_offer_to_a_withheld_run_carries_its_flag(self):
        self.assertEqual(self.offers("", "--players", "numbered"),
                         [f"{self.OFFER} --players numbered"])

    def test_names_for_one_run_is_carried_as_well(self):
        self.assertEqual(self.offers('players = "initials"\n', "--players", "names"),
                         [f"{self.OFFER} --players names"])

    def test_a_setting_in_the_config_adds_nothing(self):
        self.assertEqual(self.offers('players = "numbered"\n'), [self.OFFER])
        self.assertEqual(self.offers(""), [self.OFFER])


class TestEveryCallerOfTheDeciderAsksAboutTheBuildItSpeaksFor(unittest.TestCase):
    """Five callers of ``writes_careers`` are given how this run prints players.

    The site write, the refusal of an unmarked page, the front-page link, the
    report's line and the left-behind scan: each decides what this run writes or
    says. The mode is resolved once, by ``build._players_mode``, so ``--players``
    for one run wins over ``[build] players`` at every one of them, and none reads
    the config's setting, or the flag, for itself: two callers deciding
    differently is a front page linking a page that was not written, or a page
    the scan then offers to delete.

    The sixth, discover's namesakes note, is given ``names`` in every run, since
    what it advises on is a line of the config, and a build of that config printing
    names writes the page. It asks the decider all the same, and not
    ``config.multi_season``, so that there is one rule. ``build._careers_withheld``
    is left out of the record: it asks the decider about a build printing names as
    well as about this one, since what it answers is whether withholding is the
    only thing that kept the page away.
    """

    CALLERS = ["_compose", "_run", "_write_picker", "write_site"]

    def calls(self, config: str, *argv: str):
        cwd = Path(tempfile.mkdtemp(dir=SCRATCH))
        write_two_grade_cards(cwd / "data" / "2026-winter", shared="Ada Renn")
        (cwd / "config.toml").write_text(MULTI + config + NAMESAKES,
                                         encoding="utf-8")
        real = careers.writes_careers
        seen = []

        def record(*args, **kwargs):
            frame = sys._getframe(1)
            while frame.f_code.co_filename == mock.__file__:
                frame = frame.f_back
            given = args[1] if len(args) > 1 else kwargs.get("players")
            if frame.f_code.co_name != "_careers_withheld":
                seen.append((frame.f_code.co_name, given))
            return real(*args, **kwargs)

        with mock.patch.object(careers, "writes_careers", side_effect=record):
            code, text = main_in_process(cwd, *argv)
        self.assertEqual(code, 0, text)
        return cwd, seen

    def test_each_caller_is_given_the_mode_of_the_build_it_speaks_for(self):
        for config, argv, mode in (("", (), "names"),
                                   ("", ("--players", "numbered"), "numbered"),
                                   ('players = "initials"\n', (), "initials"),
                                   ('players = "initials"\n', ("--players", "names"),
                                    "names")):
            with self.subTest(config=config, argv=argv):
                cwd, seen = self.calls(config, *argv)
                run = [(caller, given) for caller, given in seen
                       if caller != "_namesake_notes"]
                self.assertEqual(sorted({caller for caller, _ in run}), self.CALLERS)
                self.assertEqual({given for _, given in run}, {mode}, seen)
                self.assertEqual([given for caller, given in seen
                                  if caller == "_namesake_notes"], ["names"], seen)
                self.assertEqual((cwd / "site" / site.CAREERS_PAGE).exists(),
                                 mode == "names")


if __name__ == "__main__":
    unittest.main()
