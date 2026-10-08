"""Anonymised output: ``[build] players``, ``--players``, the labels and the guard.

A dashboard names every player the way the cards do, and a club publishing one
where anybody can open it may not want that. ``[build] players = "numbered"`` or
``"initials"`` promises that no file this program writes under the output folder
holds a player's name, the front page included, and the build checks its own work
before it writes anything.

So the test that matters most here is the sweep: build a season with names, learn
every spelling, slug and word of every player from that build, build the same
cards withheld, and search every byte under ``site/`` for any of them, decoding the
page's HTML entities and its JSON islands first so an escape cannot hide one. It
runs over the demo season and over a fixture written to hold every way this was
measured to go wrong: a name printed in capitals on one card and with an accent on
another, a set row that spells a player differently from the roster, a surname of
two letters, two people with one name, a captain on no card, a card refused for a
banner that is a name, and a file named after a player of another competition.

The rest pins the parts of the promise a sweep cannot see: that the labels are
numbered in an order that is not the alphabet, that a withheld site is the same
bytes whoever played, that a name the build cannot withhold stops the run with a
report that names nobody, and that a ``players`` line the build cannot honour stops
it too, rather than being warned about and ignored.

Every name here is invented, and every card is written by
:mod:`matchcentre.cardwriter` or ``tools/demo.py``.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import hashlib
import html
import inspect
import io
import json
import os
import re
import shutil
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build as build_mod                                            # noqa: E402
from matchcentre import anonymise, cardwriter as cw, config as config_mod  # noqa: E402
from matchcentre.competition import Club                             # noqa: E402
from test_cli import (BUILD, STAMP, CliCase, PageParts, Run,         # noqa: E402
                      island_json, readme_block)
from test_pipeline import (DATES, FIXTURES, TEAMS, rows_for,           # noqa: E402
                           squad_for, write_round_four, write_season_cards)

DEMO = ROOT / "tools" / "demo.py"
COMP = "tuesday-division-b"

# --- the module's own projects ----------------------------------------------------
#
# Built once for the module, because the demo and the edge fixture are each three
# builds and a dozen classes read them. Nothing reads one to change it.

_HOME: tempfile.TemporaryDirectory | None = None
_BUILT: dict[str, Path] = {}


def setUpModule():
    global _HOME
    _HOME = tempfile.TemporaryDirectory()


def tearDownModule():
    # The projects go with the folder, so the paths to them go too: unittest sets
    # the module up again when a class of another module has run between two of
    # its own, and a path kept from the last folder names nothing.
    _HOME.cleanup()
    _BUILT.clear()


def run(cwd: Path, *argv: str, env: dict | None = None) -> Run:
    done = subprocess.run((sys.executable, str(BUILD), "--generated", STAMP, *argv),
                          cwd=cwd, capture_output=True, text=True, timeout=600,
                          env=env)
    return Run(done.returncode, done.stdout, done.stderr, cwd)


def project(name: str, write, *, config: str = "",
            builds: tuple[tuple[str, ...], ...] = ()) -> Path:
    """A project directory of this module's, written and built on first use."""
    if name not in _BUILT:
        cwd = Path(_HOME.name) / name
        cwd.mkdir()
        write(cwd / "data")
        if config:
            (cwd / "config.toml").write_text(config, encoding="utf-8")
        for argv in builds:
            run(cwd, *argv)
        _BUILT[name] = cwd
    return _BUILT[name]


def _demo_cards(data: Path) -> None:
    subprocess.run((sys.executable, str(DEMO), "--out", str(data)), check=True,
                   capture_output=True, timeout=600)


def demo() -> Path:
    """The demo season, built with names into ``named/`` and withheld into two more."""
    return project("demo", _demo_cards, builds=(
        ("--players", "names", "--out", "named"),
        ("--players", "numbered", "--single-file", "--out", "numbered"),
        ("--players", "initials", "--single-file", "--out", "initials")))


class TestAClassRunAfterAnotherModulesBuildsTheModulesProjectsAgain(unittest.TestCase):
    """A teardown forgets the projects it removed, so the next setup builds them anew.

    Unittest tears a module down when the next test is another module's, and sets
    it up again if one of its classes follows, as a command naming classes does.
    ``tearDownModule`` removed ``_HOME`` and kept ``_BUILT``, so the class after the
    other module's was handed a project that was no longer there. Measured on the
    base (/tmp/t7/scratch/CA2/probe/before-anon-order.log): the class of every field
    of the season document, then test_web_assets's slot table class, then the class
    of the withheld site whoever played gave 2 errors in 12 tests, each a
    FileNotFoundError on ``cast-a`` in the removed folder. Now the teardown empties
    ``_BUILT`` with the folder. Rejected: keeping ``_HOME`` for the whole process,
    which a teardown that removes it was written not to do.
    """

    def python(self, *argv: str, tmp: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            (sys.executable, *argv), cwd=ROOT / "tests", capture_output=True,
            text=True, timeout=600,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=tmp))

    def test_the_class_after_another_modules_finds_its_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self.python(
                "-m", "unittest",
                "test_anonymise.TestEveryFieldOfTheSeasonDocumentHasAClass",
                "test_web_assets.TestTheSlotTablePrintsTheRateThePythonPublishes",
                "test_anonymise.TestTheWithheldSiteIsTheSameWhoeverThePlayersAre",
                tmp=tmp)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        self.assertRegex(done.stderr, r"\nRan [1-9]\d* tests in [^\n]*\n\nOK\n")

    def test_the_teardown_forgets_the_projects_it_removed(self):
        probe = ("import unittest, test_anonymise as module\n"
                 "module.setUpModule()\n"
                 "module._BUILT['demo'] = module.Path(module._HOME.name) / 'demo'\n"
                 "module.tearDownModule()\n"
                 "unittest.case.doModuleCleanups()\n"
                 "print(len(module._BUILT))\n")
        with tempfile.TemporaryDirectory() as tmp:
            done = self.python("-c", probe, tmp=tmp)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        self.assertEqual(done.stdout.strip(), "0")


# --- the edge fixture -------------------------------------------------------------

EDGE_TEAMS = {
    "QUILLON HARRIERS": ("PRIYA HALLORAN", "Yusra Benítez", "Sam Whitlow",
                         "Tamsin Rook"),
    "MARLOW OTTERS": ("Elke Ot", "Sam Whitlow", "Idris Vane", "Noor Castellan"),
    "SALTASH KESTRELS": ("Wren Adebayo", "Oskar Lindqvist", "Hollis Brandt",
                         "Juno Farrant"),
}
EDGE_DATES = {"1": "2026-08-18", "2": "2026-08-25", "3": "2026-09-01"}
EDGE_FIXTURES = (
    ("1", "QUILLON HARRIERS", "MARLOW OTTERS", ((6, 2), (6, 3), (4, 6), (6, 1))),
    ("2", "MARLOW OTTERS", "SALTASH KESTRELS", ((6, 4), (2, 6), (6, 3), (6, 4))),
    ("3", "SALTASH KESTRELS", "QUILLON HARRIERS", ((3, 6), (6, 4), (2, 6), (6, 2))),
)
# On no scored card: a banner a card was refused for, a file named after a player of
# the other competition, and the captain the config declares for a team.
REFUSED_BANNER = "DMITRI FAIRWEATHER"
FILED_AS = "Desmond Featherstone"
DECLARED = "Marguerite Okonkwo"
EDGE_CONFIG = f'''[build]
players = "numbered"

[aliases]
"p-halloran" = "priya-halloran"

[namesakes]
"sam-whitlow" = ["Quillon Harriers", "Marlow Otters"]

[competitions.tuesday-division-e.captains]
"Saltash Kestrels" = "{DECLARED}"
'''


def _edge_printed(name: str, rnd: str) -> str:
    # Accented on the first card, in plain capitals on the others.
    return "YUSRA BENITEZ" if name == "Yusra Benítez" and rnd != "1" else name


def _edge_rows(scores, home: str, away: str, rnd: str):
    def on_court(team: str) -> list[str]:
        names = [_edge_printed(name, rnd) for name in EDGE_TEAMS[team]]
        if team == "QUILLON HARRIERS":
            names[0] = "P Halloran"     # the set rows spell her this way
        if team == "SALTASH KESTRELS" and rnd == "2":
            names[2] = "Tamsin Rook"    # filling in from Quillon Harriers
        return names
    home_names, away_names = on_court(home), on_court(away)
    pattern = ((True, (0, 1)), (False, (0,)), (False, (2,)), (True, (1, 2)))
    return tuple(cw.SetRow(home_games=h, away_games=a, doubles=pair,
                           home_players=tuple(home_names[i] for i in at),
                           away_players=tuple(away_names[i] for i in at))
                 for (h, a), (pair, at) in zip(scores, pattern))


def _edge_roster(team: str, rnd: str):
    return tuple(cw.RosterRow(name=_edge_printed(name, rnd), singles=2.5,
                              doubles=2.5, captain=(index == 0))
                 for index, name in enumerate(EDGE_TEAMS[team]))


THURSDAY = cw.CardSpec(
    date="2026-08-20", round_label="1", division="F", court="01",
    match_id="4000001", home_name="BRAMBLE FINCHES", away_name="CORVID LANE",
    rows=(cw.SetRow(6, 2, True, (FILED_AS, "Ottoline Brisk"),
                    ("Ferris Maund", "Galen Troake")),
          cw.SetRow(6, 3, False, (FILED_AS,), ("Ferris Maund",)),
          cw.SetRow(3, 6, False, ("Ottoline Brisk",), ("Galen Troake",)),
          cw.SetRow(6, 4, True, (FILED_AS, "Ottoline Brisk"),
                    ("Ferris Maund", "Galen Troake"))),
    home_roster=(cw.RosterRow(FILED_AS, 2.5, 2.5, True),
                 cw.RosterRow("Ottoline Brisk", 2.5, 2.5, False)),
    away_roster=(cw.RosterRow("Ferris Maund", 2.5, 2.5, True),
                 cw.RosterRow("Galen Troake", 2.5, 2.5, False)))


def write_thursday_card(data: Path) -> Path:
    """A second competition, one card, whose players are nobody else's."""
    (data / "Thursday").mkdir(parents=True, exist_ok=True)
    return cw.write_card_file(data / "Thursday" / "finches-corvid.pdf", THURSDAY)


def write_edge_cards(data: Path) -> None:
    for index, (rnd, home, away, scores) in enumerate(EDGE_FIXTURES, start=1):
        folder = data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        cw.write_card_file(
            folder / f"{home.split()[0].lower()}-{away.split()[0].lower()}.pdf",
            cw.CardSpec(date=EDGE_DATES[rnd], round_label=rnd, division="E",
                        court=f"{index:02d}", match_id=f"300{index:04d}",
                        home_name=home, away_name=away,
                        rows=_edge_rows(scores, home, away, rnd),
                        home_roster=_edge_roster(home, rnd),
                        away_roster=_edge_roster(away, rnd)))
    cw.write_card_file(
        data / "Round2" / f"{REFUSED_BANNER.lower().replace(' ', '-')}.pdf",
        cw.CardSpec(date=EDGE_DATES["2"], round_label="2", division="E",
                    court="09", match_id="3009999", home_name=REFUSED_BANNER,
                    away_name=REFUSED_BANNER,
                    rows=_edge_rows(EDGE_FIXTURES[0][3], "QUILLON HARRIERS",
                                    "MARLOW OTTERS", "1")))
    write_thursday_card(data)
    junk = data / FILED_AS.lower().replace(" ", "-")
    junk.mkdir()
    (junk / f"{junk.name}-r2.pdf").write_text("not a pdf\n", encoding="utf-8")
    # Round 1 again, its last rubber the other way, so two cards disagree about the
    # night and each names the other. Filed under a word of a player's name.
    rnd, home, away, scores = EDGE_FIXTURES[0]
    cw.write_card_file(
        data / f"Round{rnd}" / EDGE_CLASH,
        cw.CardSpec(date=EDGE_DATES[rnd], round_label=rnd, division="E",
                    court="01", match_id="3000001", home_name=home, away_name=away,
                    rows=_edge_rows((*scores[:3], (1, 6)), home, away, rnd),
                    home_roster=_edge_roster(home, rnd),
                    away_roster=_edge_roster(away, rnd)))


EDGE_CLASH = "halloran-copy.pdf"


def edge() -> Path:
    return project("edge", write_edge_cards, config=EDGE_CONFIG, builds=(
        ("--players", "names", "--out", "named"),
        ("--players", "numbered", "--single-file", "--out", "numbered"),
        ("--players", "initials", "--single-file", "--out", "initials")))


# --- a line the squads end at, and a night filed under its players ------------------
#
# Two teams whose players' surnames are what their cards are filed as, so a
# withheld build withholds each file's name whole.

NIGHT_HOME = ("Alpha Ashford", "Bravo Beeching", "Charlie Cotterill")
NIGHT_AWAY = ("Delta Dunmore", "Echo Elmsworth", "Foxtrot Fenwick")
# Printed under a squad and read as a heading, so on no roster and in no document
# but the card's note, which quotes it: a player's name, for all anybody can tell.
# One for each way the reader takes a line for one, so each is found on its own.
HEADING = "Robin Quaife"
BRACKETED = "Juniper Oakhanger"


def night_card(path: Path, *, rnd: str, date: str, match_id: str, home, away,
               last=(6, 1), under=((), ()), **spec) -> Path:
    """One card of the two teams above; *under* is printed below each squad."""
    def squad(names, extra):
        return (tuple(cw.RosterRow(name, 3.0, 3.0, captain=(index == 0))
                      for index, name in enumerate(names))
                + tuple(cw.RosterRow(line) for line in extra))
    names = {NIGHT_HOME: "EXAMPLE ALPHAS", NIGHT_AWAY: "EXAMPLE BRAVOS"}
    path.parent.mkdir(parents=True, exist_ok=True)
    return cw.write_card_file(path, cw.CardSpec(
        date=date, round_label=rnd, match_id=match_id, home_name=names[home],
        away_name=names[away],
        rows=(cw.SetRow(6, 2, True, home[:2], away[:2]),
              cw.SetRow(6, 3, False, home[:1], away[:1]),
              cw.SetRow(6, 0, False, home[1:2], away[1:2]),
              cw.SetRow(*last, True, home[:2], away[:2])),
        home_roster=squad(home, under[0]), away_roster=squad(away, under[1]),
        **spec))


def write_heading_cards(data: Path) -> None:
    """The line printed across both squads, and then under one, bracketed."""
    night_card(data / "Round1" / "r1.pdf", rnd="1", date="2026-08-18",
               match_id="3000001", home=NIGHT_HOME, away=NIGHT_AWAY,
               under=((HEADING,), (HEADING,)), fill_ins_headings=("", ""))
    night_card(data / "Round2" / "r2.pdf", rnd="2", date="2026-08-25",
               match_id="3000002", home=NIGHT_AWAY, away=NIGHT_HOME,
               under=((f"{BRACKETED} (sub)",), ()))


def headings() -> Path:
    return project("headings", write_heading_cards, builds=(
        ("--players", "names", "--out", "named"),
        ("--players", "numbered", "--single-file", "--out", "numbered"),
        ("--players", "initials", "--single-file", "--out", "initials")))


# Printed with no rating under a rated squad, over no heading this reader knows, and
# played by nobody: read as a player, and quoted by the card's note as one that may
# be a heading.
UNSURE = "Sorrel Pennycook"


def write_unsure_card(data: Path) -> None:
    night_card(data / "Round1" / "r1.pdf", rnd="1", date="2026-08-18",
               match_id="3000001", home=NIGHT_HOME, away=NIGHT_AWAY,
               under=((UNSURE,), ()), fill_ins_headings=("", ""))


def unsure() -> Path:
    return project("unsure", write_unsure_card, builds=(
        ("--players", "names", "--out", "named"),
        ("--players", "numbered", "--single-file", "--out", "numbered"),
        ("--players", "initials", "--single-file", "--out", "initials")))


def write_night_cards(data: Path) -> None:
    """Round 1 on two cards that disagree, round 2 on one, each filed by its players."""
    for name, last in (("ashford-v-dunmore.pdf", (6, 1)),
                       ("ashford-v-dunmore-copy.pdf", (6, 4))):
        night_card(data / "round-01" / name, rnd="1", date="2026-08-18",
                   match_id="3000001", home=NIGHT_HOME, away=NIGHT_AWAY, last=last)
    night_card(data / "round-02" / "dunmore-v-ashford.pdf", rnd="2",
               date="2026-08-25", match_id="3000002", home=NIGHT_AWAY,
               away=NIGHT_HOME)


def nights() -> Path:
    return project("nights", write_night_cards, builds=(
        ("--players", "names", "--out", "named"),
        ("--players", "numbered", "--out", "numbered")))


# Headings in the club's own words: with the teams called Example Alphas Mixed and
# Bravos Emergencies, every word of each is one the page prints anyway. The first
# two end in a colon, the third is printed across both squads and the last is
# bracketed, so each way the reader takes a line for a heading is here. Each piece
# of the last is a word of the home team's name as the club gives it. `Emergencies`
# and not `Reserves`, since the word `reserves`, a bracket or a colon round it or
# not, ends the block as `Fill-ins` does, before any line under it is taken for a
# heading and quoted.
ORDINARY_LINES = ("Emergencies:", "Bravos Emergencies:", "Club Players",
                  "Mixed (emergencies)")
ORDINARY_CONFIG = ('[team_names]\nexample-alphas = "Example Alphas Mixed"\n'
                   'example-bravos = "Bravos Emergencies"\n')


def write_ordinary_cards(data: Path) -> None:
    """Three cards, a heading of the lines above under a squad of each."""
    for rnd, (home, away), under in (
            ("1", (NIGHT_HOME, NIGHT_AWAY), (ORDINARY_LINES[:1], ORDINARY_LINES[1:2])),
            ("2", (NIGHT_AWAY, NIGHT_HOME), (ORDINARY_LINES[2:3],) * 2),
            ("3", (NIGHT_HOME, NIGHT_AWAY), (ORDINARY_LINES[3:], ()))):
        night_card(data / f"Round{rnd}" / f"r{rnd}.pdf", rnd=rnd,
                   date=EDGE_DATES[rnd], match_id=f"300000{rnd}", home=home,
                   away=away, under=under, fill_ins_headings=("", ""))


# Lines the page prints whole anyway: a slot's name under one squad, the away team's
# own name across both, and the site's title, which only the front page prints.
PRINTED_LINES = ("Doubles 1", "Bravos Emergencies", "Harbour Lights Tennis")
PRINTED_CONFIG = f'{ORDINARY_CONFIG}[build]\ntitle = "{PRINTED_LINES[2]}"\n'


def write_printed_cards(data: Path) -> None:
    """Three cards, a line above under a squad of each."""
    for rnd, (home, away), under in (
            ("1", (NIGHT_HOME, NIGHT_AWAY), (PRINTED_LINES[:1], ())),
            ("2", (NIGHT_AWAY, NIGHT_HOME), (PRINTED_LINES[1:2],) * 2),
            ("3", (NIGHT_HOME, NIGHT_AWAY), (PRINTED_LINES[2:],) * 2)):
        night_card(data / f"Round{rnd}" / f"r{rnd}.pdf", rnd=rnd,
                   date=EDGE_DATES[rnd], match_id=f"300000{rnd}", home=home,
                   away=away, under=under, fill_ins_headings=("", ""))


# --- two casts of one season ------------------------------------------------------
#
# The same cards under a second cast of players, renamed by a bijection that reverses
# the alphabet and changes every name's length, so an order by name, a width or a
# hash is an order that differs between them.

CAST_B = {"Ana Fernandez": "Zed Yu", "Bo Nakamura": "Yarrow Blenkinsopp",
          "Cass Ellery": "Xan Ko", "Dev Raman": "Wilhelmina Oxtoby",
          "Elin Sotto": "Vi Lu", "Fay Bramwell": "Ulrika Tennantsmoor",
          "Gil Ahearn": "Tove Ek", "Hana Wex": "Sebastiano Quennell",
          "Ilya Modric": "Ros Ib", "Jo Calder": "Quintus Harrowgate",
          "Kit Vasse": "Pim Oa", "Lena Ordoy": "Ottavio Brackenridge"}


def write_cast(data: Path, cast: dict[str, str] | None = None, *,
               dates: dict[str, str] = DATES, match: str = "200") -> None:
    """test_pipeline's six cards, with every player renamed by *cast*."""
    cast = cast or {}
    for index, (rnd, home, away, scores) in enumerate(FIXTURES, start=1):
        folder = data / f"Round{rnd}"
        folder.mkdir(parents=True, exist_ok=True)
        home_name, home_players = TEAMS[home]
        away_name, away_players = TEAMS[away]
        home_rows, home_on = squad_for(home, tuple(cast.get(n, n)
                                                   for n in home_players))
        away_rows, away_on = squad_for(away, tuple(cast.get(n, n)
                                                   for n in away_players))
        cw.write_card_file(folder / f"{home}-{away}.pdf", cw.CardSpec(
            date=dates[rnd], round_label=rnd, division="B", court=f"{index:02d}",
            match_id=f"{match}{index:04d}", home_name=home_name, away_name=away_name,
            rows=rows_for(scores, home_on, away_on), home_roster=home_rows,
            away_roster=away_rows))


def cast(tag: str) -> Path:
    """Cast A or B, built with names and numbered, each under its own hash seed."""
    seed = {"a": "1", "b": "2"}[tag]
    env = {**os.environ, "PYTHONHASHSEED": seed}

    def write(data: Path) -> None:
        write_cast(data, CAST_B if tag == "b" else None)

    cwd = project(f"cast-{tag}", write, config='[build]\nplayers = "numbered"\n')
    if not (cwd / "site").is_dir():
        for argv in (("--single-file",), ("--players", "names", "--out", "named")):
            done = run(cwd, *argv, env=env)
            assert done.code == 0, done.text[-2000:]
    return cwd


# --- reading what was written -----------------------------------------------------


def fold(text: str) -> str:
    """This suite's own fold, so the sweep does not lean on the code it checks."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch)).casefold()


def fold_words(text: str) -> list[str]:
    return re.findall(r"\w+", fold(text))


def documents(site: Path) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(site.rglob("season.json"))]


def strings_of(value) -> list[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, dict):
        return [s for key, item in value.items()
                for s in [key, *strings_of(item)]]
    if isinstance(value, list):
        return [s for item in value for s in strings_of(item)]
    return [value] if isinstance(value, str) else []


def swept(named: list[dict], extra: tuple[str, ...] = ()) -> list[str]:
    """Everything a withheld site may not hold, folded: names, their words, slugs.

    Learned from the named build of the same cards. A word of a name is swept when
    it has two letters or more and is not a word of a team, the club or the
    competition, which the page prints whoever played.
    """
    ordinary: set[str] = {"player"}
    names: set[str] = set(extra)
    slugs: set[str] = set()
    for doc in named:
        for team in doc["teams"]:
            for key in ("name", "short", "code", "slug", "home_court"):
                ordinary.update(fold_words(str(team.get(key) or "")))
            if team.get("captain"):
                names.add(team["captain"])
            slugs.update(team["players"])
        for key in ("label", "club", "day", "division", "competition", "season"):
            ordinary.update(fold_words(str(doc["meta"].get(key) or "")))
        for player in doc["players"]:
            names.add(player["name"])
            slugs.add(player["slug"])
            for pair in player["partners"] + player["opponents"]:
                names.add(pair["name"])
                slugs.add(pair["slug"])
        names.update(doc["player_names"].values())
        slugs.update(doc["player_names"])
    out: set[str] = set()
    for name in names:
        out.add(fold(name))
        out.update(word for word in fold_words(name)
                   if len(word) >= 2 and word not in ordinary)
    for slug in slugs:
        out.update((slug, slug.replace("-", " ")))
    return sorted(out, key=lambda s: (-len(s), s))


def texts_of(path: Path) -> list[str]:
    """A file as a reader of it gets it: entities decoded, each island's strings."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out = [html.unescape(text)]
    if path.suffix == ".html":
        parts = PageParts()
        parts.feed(text)
        scripts = parts.scripts
    else:
        scripts = [text]
    for script in scripts:
        body = island_json(script)
        if body is not None:
            out.extend(strings_of(json.loads(body)))
    return out


def leaks(site: Path, secrets: list[str]) -> list[str]:
    """``file: what`` for every secret found word-bounded in any file under *site*."""
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, secrets)) + r")(?!\w)")
    found = []
    for path in sorted(p for p in site.rglob("*") if p.is_file()):
        for text in texts_of(path):
            for hit in pattern.finditer(fold(text)):
                found.append(f"{path.relative_to(site)}: {hit.group()!r}")
    return sorted(set(found))


def tree(site: Path) -> dict[str, str]:
    return {path.relative_to(site).as_posix():
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(site.rglob("*")) if path.is_file()}


def labels(doc: dict) -> dict[str, str]:
    return dict(doc["player_names"])


# --- the hand-written season the unit tests read ----------------------------------
#
# Real slugs chosen so the alphabet and the order the cards put players on court
# disagree everywhere: a number given by slug, by name or by roster is a number that
# differs from the one below.

MAGPIES, QUOLLS = "brindle-magpies", "brindle-quolls"
HAND_PLAYERS = (
    (MAGPIES, "priya-halloran", "Priya Halloran"),
    (MAGPIES, "elke-ot", "Elke Ot"),
    (MAGPIES, "rufus-brindle", "Rufus Brindle"),
    (MAGPIES, "sam-whitlow-brindle-magpies", "Sam Whitlow"),
    (MAGPIES, "amos-quell", "Amos Quell"),          # on the roster, on no set row
    (MAGPIES, "nell-pike", "Nell Pike"),            # first on court in round 2
    (QUOLLS, "dora-finch", "Dora Finch"),
    (QUOLLS, "dev-fairlie", "Dev Fairlie"),
    (QUOLLS, "emile-voss", "Émile Voss"),
    (QUOLLS, "sam-whitlow-brindle-quolls", "Sam Whitlow"),
    (QUOLLS, "no-12", "12"),                        # a name with no letter in it
    ("", "hugo-stray", "Hugo Stray"),               # no team in this competition
)


def hand_season(*, rounds: int = 2, warnings: tuple[str, ...] = (),
                team_names: dict[str, str] | None = None,
                comp: str = "tuesday-division-e") -> dict:
    """A season document cut down to the fields the labels are made from."""
    names = {MAGPIES: "Brindle Magpies", QUOLLS: "Brindle Quolls",
             **(team_names or {})}
    sets = [
        [(["priya-halloran", "elke-ot"], ["dora-finch", "dev-fairlie"]),
         (["rufus-brindle"], ["emile-voss"]),
         (["sam-whitlow-brindle-magpies"], ["sam-whitlow-brindle-quolls"])],
        [(["dora-finch", "no-12"], ["nell-pike", "priya-halloran"]),
         (["hugo-stray"], ["elke-ot"])],
        [(["dev-fairlie"], ["amos-quell"])],
    ]
    rosters = [
        (sorted(s for t, s, _ in HAND_PLAYERS if t == MAGPIES and s != "nell-pike"),
         sorted(s for t, s, _ in HAND_PLAYERS if t == QUOLLS and s != "no-12")),
        (sorted(s for t, s, _ in HAND_PLAYERS if t == QUOLLS),
         sorted(s for t, s, _ in HAND_PLAYERS if t == MAGPIES)),
        ([], []),
    ]
    matches = []
    for index in range(rounds):
        home, away = (MAGPIES, QUOLLS) if index % 2 == 0 else (QUOLLS, MAGPIES)
        matches.append({
            "id": f"m{index + 1}", "round": index + 1, "home": home, "away": away,
            "home_name": names[home], "away_name": names[away],
            "sets": [{"home_players": h, "away_players": a, "label": "Doubles 1"}
                     for h, a in sets[index]],
            "home_roster": rosters[index][0], "away_roster": rosters[index][1],
            "fill_ins": ["hugo-stray"] if index == 1 else [],
            "warnings": list(warnings) if index == 0 else [],
            "source": f"data/Round{index + 1}/card.pdf"})
    players = [{"slug": slug, "name": name, "team": team,
                "team_name": names.get(team, ""), "is_captain": False,
                "partners": [], "opponents": []}
               for team, slug, name in sorted(HAND_PLAYERS, key=lambda p: p[1])]
    teams = [{"slug": slug, "name": names[slug], "short": names[slug],
              "captain": None,
              "players": sorted(s for t, s, _ in HAND_PLAYERS if t == slug)}
             for slug in (MAGPIES, QUOLLS)]
    return {"meta": {"competition": comp, "label": "Tuesday Division E",
                     "provenance": {"players": "card"}},
            "matches": matches, "players": players, "teams": teams,
            "player_names": {slug: name for _, slug, name in HAND_PLAYERS},
            "validation": {"errors": [], "warnings": [], "rejected": [],
                           "duplicates": []}}


HAND_CONFIG = config_mod.Config(path=Path("config.toml"),
                                namesakes={"sam-whitlow": (MAGPIES, QUOLLS)})


def learned(*docs: dict):
    return anonymise.secrets(list(docs), HAND_CONFIG, Club())


def hand_labels(doc: dict, mode: str = "numbered") -> dict[str, str]:
    return {slug: who.label
            for slug, who in anonymise.pseudonyms(doc, mode, learned(doc)).items()}


# --- the sweep --------------------------------------------------------------------


class TestNoNameTheCardsGiveAPlayerReachesAnyFileUnderSite(unittest.TestCase):
    """Every file a withheld build writes, searched for every player it withholds.

    The defect this closes: a club publishing a dashboard had no way to keep its
    players' names off it; every page, the CSV downloads and ``season.json`` named
    each one as the cards spell them. Measured on the demo before: 18 players, each
    named in every file under ``site/`` that holds the season.

    Searched as a reader of each file gets it -- entities decoded, each JSON island
    parsed -- because an escape is the easiest way for a name to be in a file and
    not in a grep of it. Rejected: checking ``season.json`` alone, which is one file
    of the eight a build writes, and the folded page is a ninth.
    """

    def test_the_demo_withheld_in_either_mode_names_nobody(self):
        cwd = demo()
        secrets = swept(documents(cwd / "named"))
        self.assertGreater(len(secrets), 18 * 3, "the named build taught the sweep "
                                                 "too little to mean anything")
        comps = [doc["meta"]["competition"] for doc in documents(cwd / "named")]
        for mode in anonymise.WITHHOLDING:
            with self.subTest(mode=mode):
                site = cwd / mode
                self.assertTrue(all((site / comp / f"{comp}.html").is_file()
                                    for comp in comps), mode)
                self.assertEqual(leaks(site, secrets), [])

    def test_and_the_sweep_would_have_found_them(self):
        # The same sweep over the named build, so an empty list above is the sweep
        # finding nothing and not the sweep reading nothing.
        cwd = demo()
        docs = documents(cwd / "named")
        self.assertTrue(docs, "the named build wrote nothing for the sweep to read")
        found = leaks(cwd / "named", swept(docs))
        files = {line.split(":")[0] for line in found}
        for doc in docs:
            comp = doc["meta"]["competition"]
            self.assertLessEqual({f"{comp}/season.json", f"{comp}/season-data.js"},
                                 files)


class TestTheEdgeCasesOfANameAreWithheldToo(unittest.TestCase):
    """The same sweep over a fixture holding every way a name was measured to leak.

    A roster that shouts a name the set rows abbreviate, joined by ``[aliases]``; one
    player with and without an accent; a surname of two letters; two people with one
    name, split by ``[namesakes]``; a declared captain on no card; a fill-in from
    another team; a card refused for a banner that is a name nobody else has; a
    file named after a player who plays only in the other competition; a second
    card for a night, filed under a word of a name, which the other card names. Each
    is a way a name reaches a document other than through a player's own name
    field. Rejected: the demo alone, which has none of them.
    """

    EXTRA = (REFUSED_BANNER, FILED_AS, DECLARED, "P Halloran", "Yusra Benítez",
             "YUSRA BENITEZ", "PRIYA HALLORAN")

    def test_no_file_under_site_names_any_of_them(self):
        cwd = edge()
        secrets = swept(documents(cwd / "named"), self.EXTRA)
        for word in ("ot", "fairweather", "okonkwo", "benitez", "featherstone"):
            self.assertIn(word, secrets)
        for mode in anonymise.WITHHOLDING:
            with self.subTest(mode=mode):
                self.assertEqual(leaks(cwd / mode, secrets), [])

    def test_a_refused_card_keeps_its_folder_and_loses_its_file_name(self):
        doc = next(d for d in documents(edge() / "numbered")
                   if d["meta"]["competition"] == "tuesday-division-e")
        paths = sorted(card["path"] for card in doc["validation"]["rejected"])
        self.assertEqual(paths, ["data/Round2/(file name withheld)",
                                 "data/a player/(file name withheld)"])
        self.assertEqual({card["detail"] for card in doc["validation"]["rejected"]},
                         {anonymise.REFUSED_DETAIL})

    def test_the_folder_named_after_a_player_elsewhere_is_that_players_label(self):
        # In the competition Desmond plays in, his folder is his own pseudo slug.
        doc = next(d for d in documents(edge() / "numbered")
                   if d["meta"]["competition"] == "thursday-division-f")
        paths = sorted(card["path"] for card in doc["validation"]["rejected"])
        self.assertIn("data/bramble-finches-1/(file name withheld)", paths)


class TestAFillInIsWithheldUnderTheSpellingTheirSetRowPrints(unittest.TestCase):
    """A fill-in named as the set row spells them, and withheld under that spelling.

    4.85 names a fill-in from the set row's own spelling where the slug's was used
    before, so ``Tui-Mae O'Dowell``, on no roster, is no longer ``Tui Mae O Dowell``.
    The first assertion is that change. The rest is 4.58's guard held to the new
    spelling: the guard learns every name a season document gives a player, the set
    row's spelling now among them, so a withheld build either writes its page with
    the spelling in no file or refuses to write it. A withheld build that kept the
    names it was given was refused by the guard, so the page was missing; the sweep
    behind it is the second line, which nothing measured reached. Built with names,
    then numbered and as initials, and swept.
    """

    TUI = "Tui-Mae O'Dowell"

    def test_the_spelling_the_named_build_shows_is_in_no_withheld_file(self):
        def write(data: Path) -> None:
            write_season_cards(data)
            write_round_four(data, {"riverside-rockets": self.TUI})

        cwd = project("fill-in", write, builds=(
            ("--players", "names", "--out", "named"),
            ("--players", "numbered", "--single-file", "--out", "numbered"),
            ("--players", "initials", "--single-file", "--out", "initials")))
        named = documents(cwd / "named")
        self.assertEqual(named[0]["player_names"]["tui-mae-o-dowell"], self.TUI)
        secrets = swept(named)
        self.assertIn(fold(self.TUI), secrets)
        for mode in anonymise.WITHHOLDING:
            with self.subTest(mode=mode):
                self.assertTrue((cwd / mode / COMP / f"{COMP}.html").is_file(), mode)
                self.assertEqual(leaks(cwd / mode, secrets), [])


class TestALineTheSquadsWereEndedAtIsWithheldLikeAName(unittest.TestCase):
    """A roster line the reader took for a heading, which the card's note quotes.

    The reader leaves the line out of both squads and says so on the card, quoting
    it, because it may be a player. So it is in no roster and in no document but
    that note, and what the run must never write was learned from the documents and
    the config alone. Measured before with the review's two cards
    (``/tmp/t4/review-work`` p3, copied to ``/tmp/t4/scratch/f1/p3``): the line
    printed the same across both squads and the line bracketed under one, each
    built ``--players numbered`` and ``initials`` with ``--single-file``, exited 0
    with nothing on stderr, said ``players 6 withheld``, and wrote the name into
    ``season.json``, ``season-data.js`` and the one-file page, in the notes ``read
    'Kit Vasse', printed the same on one line across both squads, as a heading`` and
    ``read 'Kit Vasse (sub)' on the home side as a heading``.

    Now the card keeps the lines it quotes, and the run learns them: the guard looks
    for each, and a message holding a word of one is withheld whole, since no label
    can stand in for somebody nobody can pin. This module's cards give each of the
    two a name of its own: under one name, the second line's words withheld the
    first's note whole, and the first could go unlearned. Rejected: matching the
    note's wording in the rewrite, a second copy of the reader's sentences that the
    next note would not be in; dropping the note, which is the one thing that tells
    the club a squad may be short.
    """

    def named(self) -> dict:
        return documents(headings() / "named")[0]

    def test_no_file_a_withheld_build_writes_holds_it(self):
        cwd = headings()
        secrets = swept([self.named()], (HEADING, BRACKETED))
        self.assertLessEqual({"quaife", "oakhanger"}, set(secrets))
        comp = self.named()["meta"]["competition"]
        for mode in anonymise.WITHHOLDING:
            with self.subTest(mode=mode):
                self.assertTrue((cwd / mode / comp / f"{comp}.html").is_file())
                self.assertEqual(leaks(cwd / mode, secrets), [])

    def test_the_note_is_still_there_and_withheld_whole(self):
        quoted = [[HEADING in note or BRACKETED in note for note in match["warnings"]]
                  for match in self.named()["matches"]]
        self.assertEqual([sum(said) for said in quoted], [1, 1])
        for mode in anonymise.WITHHOLDING:
            doc, = documents(headings() / mode)
            with self.subTest(mode=mode):
                self.assertEqual(
                    [[note == anonymise.WITHHELD_TEXT for note in match["warnings"]]
                     for match in doc["matches"]], quoted)

    def test_a_names_build_quotes_it_where_it_always_did_and_nowhere_else(self):
        # The card keeps the lines for the run to learn, and the export writes
        # nothing of them: a field of its own would be one more place the name is.
        doc = self.named()
        self.assertEqual(
            [(pattern, value.split(",")[0]) for pattern, value in _leaf_values(doc)
             if isinstance(value, str) and (HEADING in value or BRACKETED in value)],
            [("matches[].warnings[]", f"read '{HEADING}'"),
             ("matches[].warnings[]",
              f"read '{BRACKETED} (sub)' on the home side as a heading")])

    def test_a_line_that_is_a_players_name_leaves_them_their_label(self):
        # Learned after the players' spellings, so a line that is somebody's name
        # does not make them nobody: the note names them as every other note does.
        said = "read 'Rufus Brindle' on the home side as a heading"
        doc = hand_season(warnings=(said,))
        found = anonymise.secrets([doc], HAND_CONFIG, Club(),
                                  dropped=("Rufus Brindle",))
        self.assertEqual(found, learned(doc))
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        label = hand_labels(doc)["rufus-brindle"]
        self.assertEqual(done.season["matches"][0]["warnings"],
                         [said.replace("Rufus Brindle", label)])

    def test_so_does_a_players_name_read_as_a_heading_for_its_colon(self):
        # The line is learned as printed, colon and all, and looked for once the
        # players' spellings are swapped for labels, so what is left is a label.
        said = "read 'Rufus Brindle:' on the home side as a heading"
        doc = hand_season(warnings=(said,))
        found = anonymise.secrets([doc], HAND_CONFIG, Club(),
                                  dropped=("Rufus Brindle:",))
        self.assertEqual(found.lines, ("rufus brindle:",))
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        label = hand_labels(doc)["rufus-brindle"]
        self.assertEqual(done.season["matches"][0]["warnings"],
                         [said.replace("Rufus Brindle", label)])

    def test_a_file_named_after_a_word_of_one_is_withheld_whole(self):
        # The note quotes the line whole, and so is withheld whether or not its
        # words are; a card filed under one word of it is found by the words rule,
        # as a card filed under a player's surname is.
        doc = hand_season()
        doc["matches"][1]["source"] = "data/Round2/oakhanger.pdf"
        found = anonymise.secrets([doc], HAND_CONFIG, Club(),
                                  dropped=(f"{BRACKETED} (sub)",))
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual([match["source"] for match in done.season["matches"]],
                         ["data/Round1/card.pdf", anonymise.WITHHELD_TEXT])


class TestALineReadAsAPlayerThatMayBeAHeadingKeepsTheirLabel(unittest.TestCase):
    """A note quoting a squad member's line as a possible heading, in a withheld build.

    An unrated line under a squad's rated players, which nobody played, is read as a
    player, and the card says it may be a heading printed over that squad alone,
    quoting it. Unlike a line the squads were ended at, it is in a squad, so it is
    in the documents as a player, and a withheld build learns it from there: the
    note is kept, with the player's label where the name was, as every other note
    naming a player is. Rejected: putting it on the card's dropped lines as well,
    which withholds the whole note, and with it the one thing that tells the club
    its squad may hold a heading.
    """

    def named(self) -> dict:
        return documents(unsure() / "named")[0]

    def test_no_file_a_withheld_build_writes_holds_it(self):
        cwd = unsure()
        secrets = swept([self.named()], (UNSURE,))
        self.assertLessEqual({"sorrel", "pennycook"}, set(secrets))
        comp = self.named()["meta"]["competition"]
        for mode in anonymise.WITHHOLDING:
            with self.subTest(mode=mode):
                self.assertTrue((cwd / mode / comp / f"{comp}.html").is_file())
                self.assertEqual(leaks(cwd / mode, secrets), [])

    def test_the_note_is_kept_with_their_label_for_the_name(self):
        note, = self.named()["matches"][0]["warnings"]
        self.assertTrue(
            note.startswith(f"read '{UNSURE}' on the home side as a player"), note)
        for mode in anonymise.WITHHOLDING:
            doc, = documents(unsure() / mode)
            with self.subTest(mode=mode):
                said, = doc["matches"][0]["warnings"]
                self.assertNotIn(UNSURE, said)
                self.assertIn(said, [note.replace(UNSURE, label)
                                     for label in labels(doc).values()])


class TestTheBuildWritesNothingWhenALineTheSquadsWereEndedAtSurvives(
        unittest.TestCase):
    """A team named after the line: the guard finds it as it finds a player's name.

    Measured before on this module's cards with ``[team_names] example-bravos =
    "Robin Quaife Shield"``: ``--players numbered`` exited 0, said ``no player's full
    name is in any`` document, and wrote the name into ``season.json`` and
    ``season-data.js``, in the team's name and in the note. Rejected: withholding
    the note alone, which leaves the team's name, a label the club gave and the
    rewrite keeps, for the guard to miss.

    The line is searched for as the card printed it, brackets and all, and so is
    each piece of it left between a bracket, a colon or a word with no letter in
    it, where a name is not cut, unless the page prints the piece whole. Measured
    before with ``Kit Vasse (sub)`` under a squad beside a team called ``Kit Vasse
    Shield`` (``/tmp/t4/scratch/f1/fix/runs/cases-fixed2.txt``, case 7): exit 0,
    and the team's name printed the player's. The cost, taken: nothing in a line
    tells a team's name from a player's, so ``Example Alphas (emergencies)`` beside
    the team the club calls ``Example Alphas Mixed`` is refused, where it built
    before, and so is ``Mixed Doubles:`` beside a competition labelled ``Tuesday
    Mixed Doubles``. Each refusal names the card, since the line is the one place
    of it the club did not write, by its shape where its path holds a word of the
    line, and so does one of an id holding a word of the line, unless a player's
    name holds the word too; a player's own name is given no card. Rejected:
    searching for the whole line only, which prints the name inside it.
    """

    def refused(self, team: str, shape: str, card: str,
                write=write_heading_cards, name: str = "example-bravos") -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            write(cwd / "data")
            (cwd / "config.toml").write_text(
                f'[team_names]\n{name} = "{team}"\n', encoding="utf-8")
            done = run(cwd, "--players", "numbered")
            self.assertEqual(done.code, 2, done.text[-2000:])
            self.assertFalse((cwd / "site").exists())
            said = " ".join(done.out.split())
            self.assertIn(f"print a player's name, {shape}", said)
            self.assertIn(f"It is in a line under a squad on {card} that was read "
                          f"as a heading and may be a player's name.", said)
            self.assertIn("Rename it in config.toml: a team under [team_names]", said)
            section = done.out[done.out.index("== players: no dashboard written"):]
            self.assertNotRegex(fold(section), r"robin|quaife|juniper|oakhanger")

    def test_it_exits_2_and_writes_nothing(self):
        self.refused(f"{HEADING} Shield", "<2 words, 12 chars>", "data/Round1/r1.pdf")

    def test_so_does_a_team_named_after_the_bracketed_line_as_printed(self):
        self.refused(f"{BRACKETED} (sub) Shield", "<3 words, 23 chars>",
                     "data/Round2/r2.pdf")

    def test_and_after_what_is_inside_the_bracket(self):
        self.refused(f"{BRACKETED} Shield", "<2 words, 17 chars>", "data/Round2/r2.pdf")

    def test_and_on_either_side_of_any_bracket_a_colon_or_a_dash(self):
        # The reader's own list of what no name holds, the square and curly
        # brackets as well as the round, and each against the name itself.
        for line in (f"{BRACKETED}:", f"Sub: {BRACKETED}", f"{BRACKETED} - sub",
                     f"{BRACKETED} 2", f"{BRACKETED} [sub]", f"Sub ({BRACKETED})",
                     f"Sub [{BRACKETED}]", f"Sub {{{BRACKETED}}}"):
            def write(data: Path, line=line) -> None:
                night_card(data / "Round1" / "r1.pdf", rnd="1", date="2026-08-18",
                           match_id="3000001", home=NIGHT_HOME, away=NIGHT_AWAY,
                           under=((line,), ()))
            with self.subTest(line=line):
                self.refused(f"{BRACKETED} Shield", "<2 words, 17 chars>",
                             "data/Round1/r1.pdf", write)

    def test_and_where_it_is_the_cards_own_name_for_the_team(self):
        # The cost of the rule, pinned so that changing it is a decision.
        def write(data: Path) -> None:
            night_card(data / "Round1" / "r1.pdf", rnd="1", date="2026-08-18",
                       match_id="3000001", home=NIGHT_HOME, away=NIGHT_AWAY,
                       under=(("Example Alphas (emergencies)",), ()))
        self.refused("Example Alphas Mixed", "<2 words, 14 chars>",
                     "data/Round1/r1.pdf", write, "example-alphas")

    def test_and_where_it_is_a_competitions_label(self):
        """The same cost where the club's words are a label: named, and renamed.

        ``Mixed Doubles:`` under a squad, and the competition labelled ``Tuesday
        Mixed Doubles``, which built before and is refused now, the card named.
        Relabelled ``Tuesday Mixed``, it builds. Measured before this row: exit
        0 in 137 lines, the note withheld whole. This row's first fix refused it as
        now, and said the cost was a team's name only.
        """
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            night_card(cwd / "data" / "Round1" / "r1.pdf", rnd="1",
                       date="2026-08-18", match_id="3000001", home=NIGHT_HOME,
                       away=NIGHT_AWAY, under=((), ("Mixed Doubles:",)))
            section = ('[competitions.tuesday-division-c]\nlabel = "{}"\n'
                       'day = "Tuesday"\ndivision = "C"\n')
            config = cwd / "config.toml"
            config.write_text(section.format("Tuesday Mixed Doubles"), encoding="utf-8")
            done = run(cwd, "--players", "numbered")
            self.assertEqual(done.code, 2, done.text[-2000:])
            said = " ".join(done.out.split())
            self.assertIn("the label of competition tuesday-division-c and 1 other "
                          "place(s) print a player's name, <2 words, 13 chars>, as it "
                          "stands. It is in a line under a squad on data/Round1/r1.pdf "
                          "that was read as a heading and may be a player's name. "
                          "Rename it in config.toml:", said)
            config.write_text(section.format("Tuesday Mixed"), encoding="utf-8")
            done = run(cwd, "--players", "numbered")
            self.assertEqual(done.code, 0, done.text[-2000:])

    def test_a_card_filed_under_a_word_of_the_line_is_named_by_its_shape(self):
        # The report names nobody, and a word of the line no team's name prints may
        # be somebody's.
        def write(data: Path) -> None:
            night_card(data / "Round1" / "wren.pdf", rnd="1", date="2026-08-18",
                       match_id="3000001", home=NIGHT_HOME, away=NIGHT_AWAY,
                       under=((f"{BRACKETED} (Wren)",), ()))
        self.refused(f"{BRACKETED} Shield", "<2 words, 17 chars>", "<20 chars>", write)

    def test_and_where_a_word_of_it_is_in_a_competitions_id(self):
        """The id is refused as a key, and the line it came from is named too.

        Measured before this was said, with ``Juniors B:`` under a squad and
        ``[competitions.juniors-cup]``: exit 2, the id's shape and where to rename
        it, and nothing to say that a line on a card was why. The club was told a
        player's name was in an id it typed, and had nothing to look at.
        """
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            night_card(cwd / "data" / "Round1" / "r1.pdf", rnd="1",
                       date="2026-08-18", match_id="3000001", home=NIGHT_HOME,
                       away=NIGHT_AWAY, under=(("Juniors B:",), ()))
            (cwd / "config.toml").write_text(
                '[competitions.juniors-cup]\nlabel = "Winter Cup"\nday = "Tuesday"\n'
                'division = "C"\n', encoding="utf-8")
            done = run(cwd, "--players", "numbered")
            self.assertEqual(done.code, 2, done.text[-2000:])
            self.assertFalse((cwd / "site").exists())
            section = done.out[done.out.index("== players: no dashboard written"):]
            self.assertIn(
                "the id of competition <11 chars> and 1 other place(s) hold a player's "
                "name or a word of one, <7 chars>, and the dashboard's folder and web "
                "address are made from it. It is in a line under a squad on "
                "data/Round1/r1.pdf that was read as a heading and may be a player's "
                "name. Rename it: a competition's id",
                " ".join(section.split()))
            self.assertNotIn("juniors", fold(section))

    def test_but_a_word_a_players_name_holds_too_is_given_none(self):
        # A word of the line is the line's; a word a player's name holds as well
        # may be the roster's, and the card would send the club the wrong way.
        for word, card in (("Wren", "data/Round1/card.pdf"), ("Quell", "")):
            doc = hand_season(comp=f"{word.lower()}-cup",
                              warnings=(f"read '{word} B:' on the home side as a "
                                        f"heading",))
            found = anonymise.secrets([doc], HAND_CONFIG, Club(),
                                      dropped=(f"{word} B:",))
            done = anonymise.anonymise(doc, mode="numbered", secrets=found)
            with self.subTest(word=word):
                self.assertEqual(
                    [(r.kind, r.place, r.card) for r in anonymise.refusals(
                        [done.season], found, club=Club(), cfg=HAND_CONFIG,
                        named=[doc])],
                    [("key", "meta.competition", card)])

    def test_a_players_own_name_is_given_no_card_though_a_note_quotes_it(self):
        # Only a line is the card's: a player's name is the rosters', and a note
        # naming them says nothing of where the club wrote it.
        doc = hand_season(warnings=("Rufus Brindle played two rows",),
                          team_names={QUOLLS: "Rufus Brindle Shield"})
        found = anonymise.secrets([doc], HAND_CONFIG, Club())
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual(
            [(r.kind, r.place, r.card) for r in anonymise.refusals(
                [done.season], found, club=Club(), cfg=HAND_CONFIG, named=[doc])],
            [("label", "matches[0].away_name", "")])


class TestALineInWordsThePagePrintsAnywayIsWithheldAndStopsNothing(
        unittest.TestCase):
    """A heading in the club's own words: its note is withheld, and the build runs.

    Each line is in no squad and quoted in its card's note, and each of its words is
    one the page prints anyway, here in a team's name. Measured on the first fix
    for such a line (``/tmp/t4/scratch/f1/fix/runs/cases-mid.txt``, cases 1 to 6 and
    10): the line was learned for the guard and trimmed as a player's name is, and
    the words rule withholds only a note holding a word the page does not print, so
    the guard found the line in its own note and the build exited 2, ``That is a
    fault in this program``. ``Bravos Reserves:`` trimmed is the team's name, so
    that one was refused as a team named after a player. Before that fix each exited
    0 and printed the note. Now a note quoting a line as the card printed it is
    withheld whole, whatever its words, and a line is searched for as printed and
    never trimmed, so a heading holding a team's name is not the team's name. Its
    pieces are learned too, cut at a bracket or a colon, but not one the page prints
    whole, as it prints ``Bravos Emergencies`` and, for a competition's division,
    ``Division C``, or one word it prints. ``Example Alphas (emergencies)`` beside
    ``Example Alphas Mixed``, which this fixture held before, is now refused
    (:class:`TestTheBuildWritesNothingWhenALineTheSquadsWereEndedAtSurvives`).
    Those measurements were of ``Reserves``; the lines now say ``Emergencies``, a
    word the reader does not end the block at.
    """

    @classmethod
    def setUpClass(cls):
        cls.cwd = project("ordinary", write_ordinary_cards, config=ORDINARY_CONFIG)
        cls.runs = {mode: run(cls.cwd, "--players", mode, "--single-file",
                              "--out", mode)
                    for mode in ("names", *anonymise.WITHHOLDING)}

    def test_a_withheld_build_writes_the_site_and_counts_the_notes(self):
        for mode in ("names", *anonymise.WITHHOLDING):
            done = self.runs[mode]
            with self.subTest(mode=mode):
                self.assertEqual(done.code, 0, done.text[-2000:])
                self.assertEqual(done.err, "")
                said = " ".join(done.out.split())
                if mode != "names":
                    self.assertIn("3 message(s) on the page withheld whole", said)

    def test_each_note_that_quotes_one_is_withheld_whole(self):
        named, = documents(self.cwd / "names")
        quoted = [[any(line in note for line in ORDINARY_LINES)
                   for note in match["warnings"]] for match in named["matches"]]
        self.assertEqual([sum(said) for said in quoted], [1, 1, 1])
        for mode in anonymise.WITHHOLDING:
            doc, = documents(self.cwd / mode)
            with self.subTest(mode=mode):
                self.assertEqual(
                    [[note == anonymise.WITHHELD_TEXT for note in match["warnings"]]
                     for match in doc["matches"]], quoted)

    def test_no_file_a_withheld_build_writes_holds_one(self):
        lines = [fold(line) for line in ORDINARY_LINES]
        self.assertTrue(leaks(self.cwd / "names", lines))
        for mode in anonymise.WITHHOLDING:
            with self.subTest(mode=mode):
                self.assertTrue(documents(self.cwd / mode))
                self.assertEqual(leaks(self.cwd / mode, lines), [])

    def test_a_note_quoting_one_with_an_accent_is_withheld_whole_too(self):
        # The line is learned folded and the card's note quotes it as printed, so
        # the note is folded before it is searched, as the guard folds it.
        said = "read 'Réserves:' on the home side as a heading"
        doc = hand_season(warnings=(said,), team_names={QUOLLS: "Brindle Reserves"})
        found = anonymise.secrets([doc], HAND_CONFIG, Club(), dropped=("Réserves:",))
        self.assertIn("reserves", found.ordinary)
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual(done.season["matches"][0]["warnings"],
                         [anonymise.WITHHELD_TEXT])

    def test_and_its_piece_the_page_prints_without_the_accent_stops_nothing(self):
        # A piece is compared with the page's words folded, as the guard folds
        # them. The spelling rule compares a word by its case alone, so there
        # the card's accent would make the team's own word somebody's name.
        doc = hand_season(warnings=("read 'Réserves:' on the home side as a heading",),
                          team_names={QUOLLS: "Brindle Reserves"})
        found = anonymise.secrets([doc], HAND_CONFIG, Club(), dropped=("Réserves:",))
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual(anonymise.refusals([done.season], found, club=Club(),
                                            cfg=HAND_CONFIG, named=[doc]), [])

    def test_the_division_as_the_program_names_it_stops_nothing(self):
        """``Division C:`` under a squad, on a card of the competition it names.

        Measured on this row's first fix (``/tmp/t7/scratch/ANON1/fixup``): with an
        empty config, exit 2, the label of competition ``tuesday-division-c``
        refused for the piece ``Division C``, card named; labelled ``Tuesday C``,
        exit 2 at the id, for ``division``, and no card. The front page gave the
        division as config.toml does, ``C``, and not as the label and id the program
        makes of it print it. Rejected: leaving out a piece each of whose words the
        page prints, which learns no ``Kit Vasse`` beside a team called ``Kit Vasse
        Shield``.
        """
        labelled = ('[competitions.tuesday-division-c]\nlabel = "Tuesday C"\n'
                    'day = "Tuesday"\ndivision = "C"\n')
        for config in ("", labelled):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                cwd = Path(tmp)
                night_card(cwd / "data" / "Round1" / "r1.pdf", rnd="1",
                           date="2026-08-18", match_id="3000001", home=NIGHT_HOME,
                           away=NIGHT_AWAY, under=((), ("Division C:",)))
                (cwd / "config.toml").write_text(config, encoding="utf-8")
                done = run(cwd, "--players", "numbered")
                self.assertEqual(done.code, 0, done.text[-2000:])
                self.assertIn("1 message(s) on the page withheld whole",
                              " ".join(done.out.split()))

    def test_and_so_does_the_division_of_a_section_no_card_has_arrived_for(self):
        # Listed by the label a first run types, the program's own name for the
        # division in it, before any card is filed under the section.
        doc = hand_season(warnings=("read 'Division A:' on the home side as a "
                                    "heading",))
        cfg = config_mod.Config(
            path=Path("config.toml"), namesakes=HAND_CONFIG.namesakes,
            competitions={"thursday-division-a": config_mod.CompetitionConfig(
                id="thursday-division-a", label="Thursday Division A",
                day="Thursday", division="A")})
        found = anonymise.secrets([doc], cfg, Club(), dropped=("Division A:",))
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual(anonymise.refusals([done.season], found, club=Club(),
                                            cfg=cfg, named=[doc]), [])
        self.assertEqual(done.season["matches"][0]["warnings"],
                         [anonymise.WITHHELD_TEXT])


class TestALineThePagePrintsWholeAnywayIsLeftInItsNote(unittest.TestCase):
    """A heading that is word for word a slot's name, the team's own, or the title.

    Measured on the first fix (``/tmp/t4/scratch/f1/fix/runs/cases2.txt``, x1 and
    x2): the line was learned for the guard, which found it where the page prints
    it, in the slot's label and the team's name, and the build exited 2 telling the
    club to rename it in ``config.toml``, which names neither slot. Before that fix
    each exited 0. Now a line the page prints whole anyway, a label of the document
    or a string of the front page, is not learned, as a word of a team's name is
    not, and its note is left as a names build writes it. The title is in no
    document, so the front page is read as well. Rejected: withholding the note all
    the same, which says a player was named where the page prints the words anyway.
    """

    @classmethod
    def setUpClass(cls):
        cls.cwd = project("printed", write_printed_cards, config=PRINTED_CONFIG)
        cls.runs = {mode: run(cls.cwd, "--players", mode, "--out", mode)
                    for mode in ("names", "numbered")}

    def test_a_withheld_build_writes_the_site_and_withholds_nothing(self):
        done = self.runs["numbered"]
        self.assertEqual(done.code, 0, done.text[-2000:])
        self.assertNotIn("withheld whole", " ".join(done.out.split()))

    def test_each_note_is_as_a_names_build_writes_it(self):
        (named,), (numbered,) = (documents(self.cwd / mode)
                                 for mode in ("names", "numbered"))
        notes = [[note for note in match["warnings"]
                  if any(f"'{line}'" in note for line in PRINTED_LINES)]
                 for match in named["matches"]]
        self.assertEqual([len(said) for said in notes], [1, 1, 1])
        self.assertEqual([match["warnings"] for match in numbered["matches"]],
                         [match["warnings"] for match in named["matches"]])

    def test_a_line_a_rounds_note_holds_whole_is_learned_all_the_same(self):
        # Free text is withheld where it names somebody, not printed as it is, so
        # only a label or the front page makes a line the page's own.
        doc = hand_season()
        doc["rounds"] = [{"number": 1, "note": HEADING}]
        found = anonymise.secrets([doc], HAND_CONFIG, Club(), dropped=(HEADING,))
        self.assertEqual(found.lines, (fold(HEADING),))
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual(done.season["rounds"][0]["note"], anonymise.WITHHELD_TEXT)


class TestEachCardOfARepeatedNightFindsTheOthersWhenNamesAreWithheld(
        unittest.TestCase):
    """A night on two cards, each file named after its players, built withheld.

    A withheld build withholds a file name holding a word of a player's name whole,
    so every such card's ``source`` reads the same, and ``other_cards`` named each
    other card by its ``source``. Measured before with the review's cards
    (``/tmp/t4/review-work`` p4, copied to ``/tmp/t4/scratch/f1/p4``), headless
    Chrome on ``#/matches``: with names, 3 cards and ``All 12 rubbers in one table``;
    ``--players numbered``, 4 cards and 16 rubbers, the round 2 card shown a second
    time beside round 1's two, and each note reading ``The other card is (withheld:
    it named a player).`` So the field gives each other card's place in the
    document's ``matches``, the program's own number and the same in either mode.
    Rejected: numbering the withheld sources, which says two files differ and not
    which card is which; joining on the match id, which a re-export shares and a
    card may not print.
    """

    def docs(self) -> tuple[dict, dict]:
        (named,), (numbered,) = (documents(nights() / mode)
                                 for mode in ("named", "numbered"))
        return named, numbered

    def test_the_other_cards_are_places_and_the_same_in_either_mode(self):
        named, numbered = self.docs()
        # First that the cards are what this is about: three files' names, withheld
        # alike, so a source is no way to tell one of these cards from another.
        self.assertEqual(len({m["source"] for m in named["matches"]}), 3)
        self.assertEqual({m["source"] for m in numbered["matches"]},
                         {anonymise.WITHHELD_TEXT})
        places = [m["other_cards"] for m in named["matches"]]
        self.assertEqual([m["other_cards"] for m in numbered["matches"]], places)
        self.assertEqual(places, [[1], [0], []])
        for at, match in enumerate(named["matches"]):
            for other in match["other_cards"]:
                with self.subTest(at=at, other=other):
                    self.assertNotEqual(other, at)
                    self.assertEqual(
                        [named["matches"][other][key]
                         for key in ("round", "home", "away")],
                        [match[key] for key in ("round", "home", "away")])


class TestTheWithheldSiteIsTheSameWhoeverThePlayersAre(unittest.TestCase):
    """One season under two casts of players, withheld: the same bytes, file by file.

    What a sweep cannot see is order. The export sorts players, a team's squad, the
    fill-ins and each player's partners by slug, and an order by slug is an order by
    name, so a withheld page that kept it would say who was who to anybody who
    knew the squad. Built under two hash seeds as well, so no set is iterated in an
    order that differs between runs. Rejected: comparing the documents only, since
    the folded page and the CSVs are built from them by other code.
    """

    def test_every_file_is_the_same_under_either_cast(self):
        a, b = tree(cast("a") / "site"), tree(cast("b") / "site")
        self.assertIn(f"{COMP}/{COMP}.html", a)
        self.assertEqual(sorted(a), sorted(b))
        self.assertEqual([name for name in a if a[name] != b[name]], [])

    def test_while_the_named_builds_differ(self):
        a, b = tree(cast("a") / "named"), tree(cast("b") / "named")
        self.assertNotEqual(a[f"{COMP}/season.json"], b[f"{COMP}/season.json"])

    def test_a_list_of_players_is_in_the_order_of_their_labels(self):
        """Two fill-ins on one card, whose slugs and numbers disagree.

        No card of either cast has two fill-ins, so the casts cannot tell whether a
        match's fill-ins keep the export's order. Measured by leaving them in it:
        both casts built the same bytes, and this document's second match listed
        ``brindle-magpies-6`` before ``brindle-magpies-3``, which is the alphabet.
        Rejected: a cast with two fill-ins on a card, a third build of the cast
        fixture to pin one list.
        """
        doc = hand_season()
        doc["matches"][1]["fill_ins"] = ["nell-pike", "rufus-brindle"]
        found = learned(doc)
        names = anonymise.pseudonyms(doc, "numbered", found)
        done = anonymise.anonymise(doc, mode="numbered", secrets=found).season
        for place, before, after in (
                ("fill_ins", doc["matches"][1]["fill_ins"],
                 done["matches"][1]["fill_ins"]),
                ("players", doc["teams"][0]["players"], done["teams"][0]["players"])):
            with self.subTest(place=place):
                self.assertNotEqual([names[s].slug for s in before], after)
                self.assertEqual(after, [names[s].slug for s in
                                         sorted(before, key=lambda s: names[s].order)])


# --- the document -----------------------------------------------------------------


def _leaf_patterns(value, pattern: str = "") -> list[str]:
    """This suite's own walk of a document: ``[]`` for an index, ``{}`` for a data key.

    The keys of a dict in ``KEYS`` are data and classed there; a dict of data keys
    missing from it shows up here as a leaf per key, which the table has no row for.
    """
    if isinstance(value, dict):
        if pattern in anonymise.KEYS:
            return [p for item in value.values()
                    for p in _leaf_patterns(item, f"{pattern}{{}}")]
        return [p for key, item in value.items()
                for p in _leaf_patterns(item, f"{pattern}.{key}" if pattern else key)]
    if isinstance(value, list):
        return [p for item in value for p in _leaf_patterns(item, f"{pattern}[]")]
    return [pattern]


def _leaf_values(value, pattern: str = "") -> list[tuple[str, object]]:
    if isinstance(value, dict):
        keyed = pattern in anonymise.KEYS
        return [pv for key, item in value.items()
                for pv in _leaf_values(item, f"{pattern}{{}}" if keyed
                                       else f"{pattern}.{key}" if pattern else key)]
    if isinstance(value, list):
        return [pv for item in value for pv in _leaf_values(item, f"{pattern}[]")]
    return [(pattern, value)]


class TestEveryFieldOfTheSeasonDocumentHasAClass(unittest.TestCase):
    """Every field ``season.json`` has is in the table that says what it holds.

    An unclassified field is rewritten as free text, which is safe, and the guard
    reads it, which is safer; but a field that holds a player and is read as free
    text keeps a slug where a label belongs. So every leaf of the demo and the edge
    documents is in :data:`anonymise.FIELDS`, and the two classes the rewrite
    passes through untouched -- labels and the program's own values -- are shown
    to carry no player, by building one season under two casts and finding them
    equal. Rejected: trusting the table to be complete because it was complete on
    the day it was written; the export gains a field whenever an item needs one.
    """

    def test_every_leaf_of_a_real_document_is_classified(self):
        for doc in documents(demo() / "named") + documents(edge() / "named"):
            missing = sorted({p for p in _leaf_patterns(doc)
                              if p not in anonymise.FIELDS})
            with self.subTest(competition=doc["meta"]["competition"]):
                self.assertEqual(missing, [])

    def test_the_classes_kept_as_they_are_say_the_same_whoever_played(self):
        kept = (anonymise.LABEL, anonymise.PROGRAM)
        a = Counter((p, json.dumps(v)) for p, v in _leaf_values(
            documents(cast("a") / "named")[0]) if anonymise.classify(p) in kept)
        b = Counter((p, json.dumps(v)) for p, v in _leaf_values(
            documents(cast("b") / "named")[0]) if anonymise.classify(p) in kept)
        self.assertGreater(len(a), 100)
        self.assertEqual(sorted((a - b).elements()), [])

    def test_a_night_on_two_cards_is_read_too_and_its_places_are_the_programs(self):
        """The edge fixture's round 1 is on two cards, so ``other_cards`` has a leaf.

        A card alone on its night writes ``[]``, which is no leaf for the table to
        be asked about. Measured before: the field was in no row of the table, and
        this class passed, because neither document held a night on two cards. The
        places are this program's own numbers, kept as they are by a withheld
        build. Rejected: a fixture of its own for the clash, since the edge cards
        take one without another test's figures moving.
        """
        doc = next(d for d in documents(edge() / "named")
                   if d["meta"]["competition"] == "tuesday-division-e")
        self.assertIn("matches[].other_cards[]", _leaf_patterns(doc))
        self.assertEqual(anonymise.classify("matches[].other_cards[]"),
                         anonymise.PROGRAM)

    def test_a_round_s_flag_is_the_programs(self):
        """``rounds[].numbered`` is the build's answer, kept as it is by a withheld build.

        True or false by the round's label alone, which a withheld build keeps too
        (TestARoundSaysWhetherTheBuildReadItsLabelAsANumber). Rejected: a label, which
        is kept as well and is checked for a name, and a flag is no text to check.
        """
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        self.assertIn("rounds[].numbered", leaves)
        self.assertEqual(anonymise.classify("rounds[].numbered"), anonymise.PROGRAM)

    def test_a_round_s_count_of_cards_played_is_the_programs(self):
        """``rounds[].cards_played`` is a count of the round's own cards, as ``cards`` is.

        How many of them count as played, by ``Match.counts_as_played``, which a
        withheld build keeps as it is, as it keeps ``rounds[].cards``
        (TestARoundEveryCardOfWhichWasCalledOffSaysNoneOfItsCardsWasPlayed, 4.452).
        Rejected: free text, which is what an unclassed field is read as, and a
        count is no text to check.
        """
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        self.assertIn("rounds[].cards_played", leaves)
        self.assertEqual(anonymise.classify("rounds[].cards_played"), anonymise.PROGRAM)

    def test_a_team_s_win_rate_is_the_programs(self):
        """``teams[].win_pct`` is ``set_win_pct`` of the row's own sets, as a player's is.

        A number the build works out of two counts the table already keeps as the
        program's (TestATeamsRateOfSetsWonIsPublishedByTheRuleAPlayersIs). Rejected: a
        class of its own, where ``players[].win_pct``, the same rule, is the program's.
        """
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        self.assertIn("teams[].win_pct", leaves)
        self.assertEqual(anonymise.classify("teams[].win_pct"), anonymise.PROGRAM)

    def test_the_regular_rounds_count_of_fixtures_is_the_programs(self):
        """``meta.regular_fixtures`` is two counts of the document's own rows.

        The regular rounds' rows of ``fixtures`` and the matches among them the
        season's totals keep, which a withheld build keeps as they are, as it keeps
        ``meta.matches_total`` (TestARegularFixtureIsCountedOnceAndOnlyWhereItWasPlayed).
        Rejected: free text, which is what an unclassed field is read as, and a count
        is no text to check.
        """
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        for field in ("meta.regular_fixtures.played", "meta.regular_fixtures.total"):
            with self.subTest(field=field):
                self.assertIn(field, leaves)
                self.assertEqual(anonymise.classify(field), anonymise.PROGRAM)

    def test_the_ids_the_keys_replaced_are_out_of_the_table(self):
        """``rounds[].matches[]`` and ``fixtures[].match_id`` are fields no longer written.

        So they are out of the table too, as they are out of the document
        (TestNoRoundOrFixtureListsACardByTheIdItPrinted). This holds those two out of
        it and no more: a row for some other field no document writes would pass.
        Rejected: leaving them in, harmless to the rewrite and a table saying the
        document holds two fields it does not.
        """
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        for gone in ("rounds[].matches[]", "fixtures[].match_id"):
            with self.subTest(field=gone):
                self.assertNotIn(gone, leaves)
                self.assertNotIn(gone, anonymise.FIELDS)


class TestAPlayerIsNumberedInTheOrderTheirTeamsCardsFirstPutThemOnCourt(
        unittest.TestCase):
    """A team's #1 is the first name on its first set row, and nothing else decides.

    Not the alphabet, and not the roster block, whose order nobody here has checked
    is not the alphabet. Set rows top to bottom on the team's first card, then that
    card's roster players who played no set, then the next card. A card for a later
    round renumbers nobody. Rejected: a stored table of numbers, which a rebuild
    from the cards alone could not reproduce.
    """

    def test_set_rows_first_then_the_roster_then_the_next_card(self):
        got = hand_labels(hand_season())
        self.assertEqual([got[s] for s in ("priya-halloran", "elke-ot", "rufus-brindle",
                                           "sam-whitlow-brindle-magpies", "amos-quell",
                                           "nell-pike")],
                         [f"Brindle Magpies #{n}" for n in range(1, 7)])
        self.assertEqual([got[s] for s in ("dora-finch", "dev-fairlie", "emile-voss",
                                           "sam-whitlow-brindle-quolls", "no-12")],
                         [f"Brindle Quolls #{n}" for n in range(1, 6)])

    def test_a_later_card_renumbers_nobody(self):
        two = hand_labels(hand_season(rounds=2))
        three = hand_labels(hand_season(rounds=3))
        self.assertEqual({s: three[s] for s in two}, two)

    def test_the_slug_is_the_team_and_the_number(self):
        doc = hand_season()
        names = anonymise.pseudonyms(doc, "numbered", learned(doc))
        self.assertEqual(names["priya-halloran"].slug, "brindle-magpies-1")
        self.assertEqual(names["no-12"].slug, "brindle-quolls-5")

    def test_on_a_built_season_the_first_card_is_numbered_from_its_first_row(self):
        doc = documents(cast("a") / "site")[0]
        first = doc["matches"][0]
        self.assertEqual(first["sets"][0]["home_players"],
                         [f"{first['home']}-1", f"{first['home']}-2"])
        self.assertEqual(first["home_roster"][-1], f"{first['home']}-3")

    def test_players_on_no_card_come_after_them_by_slug(self):
        # Two Magpies on no card of a one-round season, listed out of the alphabet:
        # the order a document lists its players in numbers nobody.
        doc = hand_season(rounds=1)
        doc["matches"][0]["home_roster"].remove("amos-quell")
        doc["players"].reverse()
        got = hand_labels(doc)
        self.assertEqual([got[s] for s in ("amos-quell", "nell-pike")],
                         ["Brindle Magpies #5", "Brindle Magpies #6"])


class TestAPlayerWithNoTeamHereIsNumberedAsAPlayer(unittest.TestCase):
    """Somebody on a card of this competition with no team in it is ``Player #n``.

    And their slug is ``player-n``, which a team slugged ``player`` would share: so
    such a team is refused as a fault rather than renamed, because two things with
    one address on a page is the page linking one to the other.
    """

    def test_the_label_and_slug(self):
        doc = hand_season()
        who = anonymise.pseudonyms(doc, "numbered", learned(doc))["hugo-stray"]
        self.assertEqual((who.label, who.slug, who.team), ("Player #1", "player-1", ""))

    def test_a_team_slugged_player_stops_the_build(self):
        doc = hand_season()
        doc["teams"][0]["slug"] = "player"
        for player in doc["players"]:
            if player["team"] == MAGPIES:
                player["team"] = "player"
        known = learned(doc)
        done = anonymise.anonymise(doc, mode="numbered", secrets=known)
        self.assertEqual(done.season["teams"][0]["players"][0], "player-1")
        found = anonymise.refusals([done.season], known, club=Club(), cfg=HAND_CONFIG)
        self.assertEqual([(r.kind, r.place) for r in found],
                         [("fault", "teams[0].slug")])


class TestSharedInitialsAreNumberedInTheOrderThePlayersFirstAppear(unittest.TestCase):
    """``D.F. 1`` is whoever the walk met first, not whoever sorts first.

    Initials without their accents, so ``Émile`` is ``E.``; a name with no letter in
    it keeps its numbered label rather than printing an empty one.
    """

    def test_initials(self):
        got = hand_labels(hand_season(), "initials")
        self.assertEqual(got["dora-finch"], "D.F. 1")
        self.assertEqual(got["dev-fairlie"], "D.F. 2")
        self.assertEqual(got["emile-voss"], "E.V.")
        self.assertEqual(got["priya-halloran"], "P.H.")
        self.assertEqual(got["no-12"], "Brindle Quolls #5")

    def test_the_report_counts_them(self):
        doc = hand_season()
        done = anonymise.anonymise(doc, mode="initials", secrets=learned(doc))
        # D.F. twice and S.W. twice.
        self.assertEqual(done.shared, 4)


class TestEachPartOfAHyphenatedNameHasItsOwnInitial(unittest.TestCase):
    """``Tui-Mae O'Dowell`` is ``T.M.O.``, and ``Pim Arkwright-Sole`` is ``P.A.S.``.

    The defect: a name had an initial to each word its spaces left, so a given
    name or a surname a hyphen joins had one. Measured before, built ``--players
    initials`` from test_pipeline's cards (``/tmp/t7/scratch/ANON2/p180/probe.py``):
    ``Tui-Mae O'Dowell`` filling in for the Rockets was ``T.O.``, ``Pim
    Arkwright-Sole`` for the Aces ``P.A.``, and on a roster ``Jo-Ann Smith-Jones``
    ``J.S.``; and spelt in ``[player_names]`` with an en dash
    (``/tmp/t7/scratch/ANON2/p180/endash.py``), ``Mary–Kate Ashby`` was ``M.A.``.
    Now each part a character of Unicode's dash punctuation parts is initialled as
    a word is, by a capital however the part is spelt, in the one rule a roster's
    players and a fill-in share. An apostrophe parts nothing: ``O'Dowell`` is one
    surname, one initial, and ``Ros D'Arcy`` is ``R.D.`` as before. Rejected:
    ``T-M.O.``, a hyphen in a label of initials; and ``T.M.O.D.``, an initial for
    ``O'`` as though it were a word.
    """

    TUI, PIM = "Tui-Mae O'Dowell", "Pim Arkwright-Sole"

    def labels(self, **names: str) -> dict[str, str]:
        doc = hand_season()
        doc["player_names"].update(names)
        return hand_labels(doc, "initials")

    def test_each_part_a_hyphen_joins_has_an_initial(self):
        got = self.labels(**{"priya-halloran": self.TUI, "elke-ot": self.PIM,
                             "rufus-brindle": "Jo-Ann Smith-Jones"})
        self.assertEqual((got["priya-halloran"], got["elke-ot"], got["rufus-brindle"]),
                         ("T.M.O.", "P.A.S.", "J.A.S.J."))

    def test_a_dash_of_another_kind_parts_a_name_as_a_hyphen_does(self):
        # An en dash, and a hyphen that does not break, as a config.toml may spell.
        got = self.labels(**{"priya-halloran": "Mary–Kate Ashby",
                             "elke-ot": "Jo‑Ann Smith‑Jones"})
        self.assertEqual((got["priya-halloran"], got["elke-ot"]),
                         ("M.K.A.", "J.A.S.J."))

    def test_a_part_spelt_in_lower_case_has_a_capital_initial(self):
        # As a shouted MARY–KATE ASHBY is un-shouted, where an en dash joins it,
        # and as a club may type a name.
        got = self.labels(**{"priya-halloran": "Mary–kate Ashby",
                             "elke-ot": "jo-ann smith-jones"})
        self.assertEqual((got["priya-halloran"], got["elke-ot"]),
                         ("M.K.A.", "J.A.S.J."))

    def test_an_apostrophe_parts_nothing(self):
        got = self.labels(**{"priya-halloran": "Ros D'Arcy",
                             "elke-ot": "Tui O’Dowell"})
        self.assertEqual((got["priya-halloran"], got["elke-ot"]), ("R.D.", "T.O."))

    def test_two_players_share_initials_only_where_every_part_is_the_same(self):
        got = self.labels(**{"priya-halloran": self.TUI, "elke-ot": "Tui O'Dowell"})
        self.assertEqual((got["priya-halloran"], got["elke-ot"]), ("T.M.O.", "T.O."))

    def test_a_build_by_initials_prints_them_for_a_fill_in_on_no_roster(self):
        def write(data: Path) -> None:
            write_season_cards(data)
            write_round_four(data, {"riverside-rockets": self.TUI,
                                    "hilltop-aces": self.PIM})

        cwd = project("hyphens", write, builds=(
            ("--players", "names", "--out", "named"),
            ("--players", "initials", "--out", "initials")))
        named, = documents(cwd / "named")
        self.assertLessEqual({self.TUI, self.PIM}, set(named["player_names"].values()))
        printed = set(documents(cwd / "initials")[0]["player_names"].values())
        self.assertLessEqual({"T.M.O.", "P.A.S."}, printed)
        self.assertEqual(printed & {"T.O.", "P.A."}, set())


class TestACaptainKeepsTheMarkAndLosesTheName(unittest.TestCase):
    """A team's captain line prints the captain's label; ``is_captain`` stays true.

    The captain line prints the captain's name, whoever named them: the cards' (C),
    or the club's ``[competitions.x.captains]``, which outranks it. A declared
    captain on no card is somebody new, with the team's next number. Measured in a
    draft that took the card-marked player first: the edge fixture's Saltash
    Kestrels, whose declared captain is on no card, printed its (C) player's label
    as captain, which is a different person from the one the named page prints.
    """

    def docs(self) -> tuple[dict, dict]:
        pick = lambda site: next(d for d in documents(site)
                                 if d["meta"]["competition"] == "tuesday-division-e")
        return pick(edge() / "named"), pick(edge() / "numbered")

    def test_the_mark_is_kept(self):
        named, withheld = self.docs()
        def count(doc):
            return Counter(p["team"] for p in doc["players"] if p["is_captain"])
        self.assertEqual(count(withheld), count(named))
        self.assertTrue(count(named))

    def test_the_line_prints_the_captains_label(self):
        named, withheld = self.docs()
        by_team = {t["slug"]: t for t in withheld["teams"]}
        for team in named["teams"]:
            marked = [p for p in withheld["players"]
                      if p["team"] == team["slug"] and p["is_captain"]]
            captain = by_team[team["slug"]]["captain"]
            with self.subTest(team=team["slug"]):
                if team["captain"] == DECLARED:
                    self.assertEqual(captain, "Saltash Kestrels #5")
                    self.assertNotIn(captain, withheld["player_names"].values())
                else:
                    self.assertEqual(captain, marked[0]["name"])
                self.assertNotIn("-", captain)

    def test_a_captain_typed_in_another_case_is_the_player_it_names(self):
        """``sam whitlow`` under ``captains``, where the cards print Sam Whitlow.

        Two players share the name, so the spelling table calls it either of them,
        and only the team's own players can say which. Compared by case, the line
        named nobody on the team and gave the captain a number of their own, a
        thirteenth player who never played.
        """
        doc = hand_season()
        doc["teams"][0]["captain"] = "sam whitlow"
        done = anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        self.assertEqual(done.season["teams"][0]["captain"],
                         hand_labels(doc)["sam-whitlow-brindle-magpies"])
        self.assertEqual(done.players, len(HAND_PLAYERS))

    def test_fill_ins_are_the_same_fill_ins(self):
        named, withheld = self.docs()
        self.assertEqual([len(m["fill_ins"]) for m in withheld["matches"]],
                         [len(m["fill_ins"]) for m in named["matches"]])
        self.assertTrue(any(m["fill_ins"] for m in withheld["matches"]))
        self.assertTrue(all(s in withheld["player_names"]
                            for m in withheld["matches"] for s in m["fill_ins"]))
        self.assertEqual(sorted(p["fill_in_appearances"] for p in withheld["players"]),
                         sorted(p["fill_in_appearances"] for p in named["players"]))


# --- messages ---------------------------------------------------------------------


class TestAMessageNamingAPlayerNamesTheirLabelInstead(unittest.TestCase):
    """A problem on the health panel that names a player names the label.

    Every spelling of them, ignoring case; a slug is answered with a pseudo slug;
    the longest spelling first, so a split namesake's slug is one player and not
    the name inside it; and a spelling that is two people, or somebody of another
    competition, is ``a player``. Rejected: withholding every message that names
    anybody, which empties the panel of the one thing it is for.
    """

    def withhold(self, text: str, *others: dict) -> str:
        doc = hand_season()
        found = learned(doc, *others)
        return anonymise.withhold(text, found,
                                  anonymise.pseudonyms(doc, "numbered", found))

    def test_a_name_in_any_case_or_a_slug(self):
        self.assertEqual(self.withhold("Priya Halloran played up"),
                         "Brindle Magpies #1 played up")
        self.assertEqual(self.withhold("PRIYA HALLORAN on court 2"),
                         "Brindle Magpies #1 on court 2")
        self.assertEqual(self.withhold("see [player_names] priya-halloran"),
                         "see [player_names] brindle-magpies-1")

    def test_the_longest_spelling_first(self):
        self.assertEqual(self.withhold("sam-whitlow-brindle-quolls is one person"),
                         "brindle-quolls-4 is one person")

    def test_two_people_or_somebody_elsewhere_is_a_player(self):
        other = hand_season(comp="thursday-division-f")
        other["players"].append({"slug": "ines-marlow", "name": "Ines Marlow",
                                 "team": "", "partners": [], "opponents": []})
        self.assertEqual(self.withhold("Sam Whitlow is on both rosters"),
                         "a player is on both rosters")
        self.assertEqual(self.withhold("Ines Marlow filled in", other),
                         "a player filled in")


class TestAMessageThatNamesAPlayerAnyOtherWayIsWithheldWhole(unittest.TestCase):
    """A surname alone, a name the other way round: the whole message goes.

    No label can stand in for a spelling nobody can pin to a player, so the message
    is replaced and its rule and severity kept, and the report counts it. Measured
    on the demo: none. Rejected: replacing the word alone, which leaves a sentence
    saying which word it was by its shape.
    """

    def test_any_word_of_any_name_left_withholds_it(self):
        doc = hand_season()
        found = learned(doc)
        names = anonymise.pseudonyms(doc, "numbered", found)
        for text in ("Halloran again", "Halloran, Priya", "Ot was late",
                     "Émile on court", "Voss played"):
            with self.subTest(text=text):
                self.assertEqual(anonymise.withhold(text, found, names),
                                 anonymise.WITHHELD_TEXT)
        self.assertEqual(anonymise.withhold("Emile Voss played", found, names),
                         "Brindle Quolls #3 played")
        # A word only the club's own spelling holds, typed with its accent under
        # [player_names] and printed without it: found all the same. Every other
        # accented word here is in a slug without its accent, which hides the rule.
        doc["player_names"]["dev-fairlie"] = "Dévorah Fairlie"
        found = learned(doc)
        self.assertEqual(anonymise.withhold(
            "Devorah was late", found, anonymise.pseudonyms(doc, "numbered", found)),
            anonymise.WITHHELD_TEXT)

    def test_the_finding_keeps_its_rule_and_the_report_counts_it(self):
        doc = hand_season()
        doc["validation"]["warnings"] = [{"rule": "F9", "severity": "warning",
                                          "message": "Halloran again", "subject": "",
                                          "detail": "Voss"}]
        done = anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        self.assertEqual(done.season["validation"]["warnings"],
                         [{"rule": "F9", "severity": "warning",
                           "message": anonymise.WITHHELD_TEXT, "subject": "",
                           "detail": anonymise.WITHHELD_TEXT}])
        self.assertEqual(done.withheld, 2)


class TestAFindingWithheldWholeHasItsSubjectPrintedAgain(unittest.TestCase):
    """``subject_said`` is asked again of the message the page will print.

    The build writes into each finding whether its message begins with its subject,
    and the page leaves the subject out where it does. A message withheld whole no
    longer begins with anything. Carried over unchanged, ``subject_said`` would have
    the page print ``withheld`` and nothing else, and the card the finding was about
    would be lost. So the anonymiser answers it again, by the same function, from
    the words it wrote. Rejected: keeping the build's answer and having the page
    compare the two strings itself, which puts a second copy of the rule in app.js.
    Each finding comes in with the opposite of the answer it should leave with, so
    an answer asked again cannot pass for one carried over.
    """

    CARD = "data/Round1/card.pdf"

    def findings(self, message: str, subject: str, said: bool) -> dict:
        doc = hand_season()
        for severity, rule in (("errors", "F4"), ("warnings", "fill-in")):
            doc["validation"][severity] = [{
                "rule": rule, "severity": severity[:-1], "message": message,
                "subject": subject, "detail": "", "subject_said": said}]
        done = anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        return {severity: [(finding["message"], finding["subject"],
                            finding["subject_said"])
                           for finding in done.season["validation"][severity]]
                for severity in ("errors", "warnings")}

    def test_a_message_withheld_whole_no_longer_says_the_subject_it_kept(self):
        found = self.findings(f"{self.CARD}: Halloran again", self.CARD, True)
        for severity in ("errors", "warnings"):
            with self.subTest(severity=severity):
                self.assertEqual(found[severity],
                                 [(anonymise.WITHHELD_TEXT, self.CARD, False)])

    def test_a_message_given_a_label_still_says_it(self):
        found = self.findings(f"{self.CARD}: Emile Voss played", self.CARD, False)
        self.assertEqual(found["errors"],
                         [(f"{self.CARD}: Brindle Quolls #3 played", self.CARD, True)])

    def test_a_subject_given_a_label_is_said_by_the_message_given_the_same_one(self):
        found = self.findings("priya-halloran played for two teams", "priya-halloran",
                              False)
        self.assertEqual(found["warnings"],
                         [("brindle-magpies-1 played for two teams",
                           "brindle-magpies-1", True)])


class TestAWordThePageMustPrintAnywayIsNeverTreatedAsAName(unittest.TestCase):
    """Rufus Brindle plays for Brindle Magpies, and the team keeps its name.

    A word the page prints whoever played -- a team's, the club's, the
    competition's -- is never a secret on its own. The documented limit, pinned so
    that changing it is a decision: a message holding that surname alone keeps it,
    while the full name is still replaced.
    """

    def test_the_team_and_its_messages_keep_the_word(self):
        doc = hand_season(warnings=("Brindle Magpies won the night", "Brindle again",
                                    "Rufus Brindle won"))
        found = learned(doc)
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual([t["name"] for t in done.season["teams"]],
                         ["Brindle Magpies", "Brindle Quolls"])
        self.assertEqual(done.season["matches"][0]["warnings"],
                         ["Brindle Magpies won the night", "Brindle again",
                          "Brindle Magpies #3 won"])
        self.assertEqual(anonymise.refusals([done.season], found, club=Club(),
                                            cfg=HAND_CONFIG), [])

    def test_a_word_only_the_front_page_prints_is_kept_too(self):
        """A title no document holds prints its words all the same.

        Amos Quell's surname in the club's title, and nowhere in the document: the
        front page prints it whoever played, so a message holding it alone keeps
        it, as it would if a team were called that. Without the title, the same
        message is withheld whole.
        """
        doc = hand_season(warnings=("Quell again",))
        cfg = config_mod.Config(path=Path("config.toml"),
                                namesakes=HAND_CONFIG.namesakes,
                                title="Quell Memorial Pennant")
        for config, kept in ((cfg, "Quell again"),
                             (HAND_CONFIG, anonymise.WITHHELD_TEXT)):
            with self.subTest(title=config.title):
                found = anonymise.secrets([doc], config, Club())
                done = anonymise.anonymise(doc, mode="numbered", secrets=found)
                self.assertEqual(done.season["matches"][0]["warnings"], [kept])
                self.assertEqual(anonymise.refusals([done.season], found, club=Club(),
                                                    cfg=config), [])

    def test_a_team_the_club_aliased_is_not_a_player(self):
        """``[aliases]`` folds a team's spellings too, and only a player's is learned.

        Measured on this document with every alias learned: the team's slug was
        taken for a player's, the message became ``a player won the night``, and
        ``matches[0].home`` and 18 other places were refused. Rejected: learning
        every alias, which withholds the team a club aliased.
        """
        doc = hand_season(warnings=("Brindle Magpies won the night",))
        cfg = config_mod.Config(path=Path("config.toml"),
                                namesakes=HAND_CONFIG.namesakes,
                                aliases={"brindle-mags": MAGPIES,
                                         "p-halloran": "priya-halloran"})
        found = anonymise.secrets([doc], cfg, Club())
        self.assertEqual(found.table.get("p-halloran"), "priya-halloran")
        self.assertNotIn(MAGPIES, found.table)
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        self.assertEqual(done.season["matches"][0]["warnings"],
                         ["Brindle Magpies won the night"])
        self.assertEqual(anonymise.refusals([done.season], found, club=Club(),
                                            cfg=cfg), [])


class TestAWordOfANameInACompetitionsIdOrSeasonKeyIsRefused(unittest.TestCase):
    """An id and a season's key name the dashboard's folder, and are no page's words.

    Measured before on the demo with ``[competitions.<surname>-cup]``, labelled
    ``Winter Cup`` with a day, a division and no cards, the surname a player's:
    ``--players numbered`` exited 0, said ``no player's full name is in any``
    dashboard, and wrote the id to ``site/picker-data.js``; a season folder under
    ``data`` so named, in multi mode, was in two folder names, the page and the
    front page's data. The guard looked for whole spellings only, and each word of
    an id was counted among those the page prints anyway, so the surname was
    nobody's. Now neither the id nor the key is read for those words, and both are
    searched for every word of a name that is no page's own as well as for the
    whole spellings. Rejected: publishing under an id the program makes up, which
    moves the folder and its address whenever the verdict changes and hides from
    the club a name only the club can change.
    """

    def refused(self, doc: dict, cfg=HAND_CONFIG) -> list[tuple[str, ...]]:
        found = anonymise.secrets([doc], cfg, Club())
        done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        return [(r.kind, r.owner, r.place, r.shape) for r in anonymise.refusals(
            [done.season], found, club=Club(), cfg=cfg)]

    def listing(self, section: config_mod.CompetitionConfig) -> config_mod.Config:
        return config_mod.Config(path=Path("config.toml"),
                                 namesakes=HAND_CONFIG.namesakes,
                                 competitions={section.id: section})

    def test_a_message_holding_the_word_alone_is_withheld_whole(self):
        doc = hand_season(comp="quell-cup", warnings=("Quell again",))
        self.assertNotIn("quell", learned(doc).ordinary)
        done = anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        self.assertEqual(done.season["matches"][0]["warnings"],
                         [anonymise.WITHHELD_TEXT])

    def test_the_document_is_refused_at_its_id(self):
        self.assertEqual(self.refused(hand_season(comp="quell-cup")),
                         [("key", "<9 chars>", "meta.competition", "<5 chars>")])

    def test_and_at_its_season_key(self):
        doc = hand_season()
        doc["meta"]["season_key"] = "2026-quell"
        self.assertEqual(self.refused(doc), [("key", "tuesday-division-e",
                                              "meta.season_key", "<5 chars>")])

    def test_a_name_whole_in_an_id_is_one_refusal_and_not_three(self):
        # Found whole first, as everywhere, and its words not looked for again.
        for comp in ("amos-quell-cup", "amos-quell_cup"):
            with self.subTest(comp=comp):
                self.assertEqual(self.refused(hand_season(comp=comp)), [
                    ("key", "<14 chars>", "meta.competition", "<10 chars>")])

    def test_and_where_an_underscore_or_a_digit_joins_it_to_the_rest(self):
        """An id may hold an underscore, and a folder's name a year run on.

        Measured on this row's first fix, with the demo and a section called
        ``<surname>_cup`` or ``<surname>2026``, the surname a player's: exit 0, and
        the id in ``site/picker-data.js``, as before the row. A word was a run of
        ``\\w``, which takes a letter, a digit and an underscore alike, so the
        surname was in no word of the id. Rejected: parting words so in
        :func:`matchcentre.redact.words`, which every rule reads a message by, and
        so a change to what the free-text rule withholds that this row does not make.
        """
        for comp in ("quell_cup", "quell2026", "2026quell"):
            with self.subTest(comp=comp):
                self.assertEqual(self.refused(hand_season(comp=comp)), [
                    ("key", "<9 chars>", "meta.competition", "<5 chars>")])
        doc = hand_season()
        doc["meta"]["season_key"] = "2026quell"
        self.assertEqual(self.refused(doc), [("key", "tuesday-division-e",
                                              "meta.season_key", "<5 chars>")])

    def test_the_front_page_is_refused_at_a_section_it_lists(self):
        cfg = self.listing(config_mod.CompetitionConfig(
            id="quell-cup", label="Winter Cup", day="Thursday", division="1"))
        self.assertEqual(self.refused(hand_season(), cfg), [
            ("key", "front page", "the id of competition <9 chars>", "<5 chars>")])

    def test_a_section_it_lists_nowhere_is_not_read_at_all(self):
        """No day and division: its id and label are in no file, nor its words.

        Measured before with ``[competitions.<id>]`` given only a label holding a
        player's full name: refused, ``the label of competition <id> prints a
        player's name``, for a label no file holds, while the report says the
        section is not listed anywhere. And a word of that label was the page's own,
        so a message holding it alone was kept. Rejected: reading every section,
        which is what did both.
        """
        doc = hand_season(warnings=("Quell again",))
        # A day or a division alone files no card either (``can_claim_cards``).
        for given in ({}, {"day": "Thursday"}, {"division": "1"}):
            cfg = self.listing(config_mod.CompetitionConfig(
                id="quell-cup", label="Amos Quell Cup", **given))
            with self.subTest(**given):
                self.assertEqual(self.refused(doc, cfg), [])
                found = anonymise.secrets([doc], cfg, Club())
                done = anonymise.anonymise(doc, mode="numbered", secrets=found)
                self.assertEqual(done.season["matches"][0]["warnings"],
                                 [anonymise.WITHHELD_TEXT])

    def test_a_word_the_page_prints_anyway_is_not_refused(self):
        # Rufus Brindle's surname is his team's, which the page prints whoever played.
        self.assertEqual(self.refused(hand_season(comp="brindle-cup")), [])


# --- the guard --------------------------------------------------------------------


class TestTheBuildWritesNothingWhenANameSurvives(CliCase):
    """A team named after a player: the build writes nothing and says where, not who.

    Every value the build was about to write is searched for every spelling of
    every player, ignoring case and accents, and one hit stops the run before a
    byte of the dashboard is written. The report gives the place and the name's
    shape, never the name, so the section can be pasted into a report of its own;
    and it ends on what is on disk, because nothing written by this run is not the
    same as no name published when an earlier build printed them. Rejected:
    renaming the team, which is the club's name for its team and not this
    program's to change.

    The one file it does write is ``build-status.js``, over an earlier build's,
    so the front page says the last build failed; the section says so, and
    ``--check`` writes not even that. Measured before: the file was left at
    ``{"level": "ok"}`` and the section said nothing was written.
    """

    BLOCK = """\
== players: no dashboard written =============================================
  tuesday-division-b
      teams[3].name and 10 other place(s) print a player's name, <2 words, 13
      chars>, as it stands. Rename it in config.toml: a team under
      [team_names], a competition under its [competitions] section.
"""

    def rename(self, team: str) -> None:
        path = self.cwd / "config.toml"
        if path.exists():
            # A first run writes the guess behind a #, so renaming it is removing the
            # # as well, which is what the file tells a club to do. Either way round,
            # so that a first run writing the guess live fails on what the build then
            # does, and not on this setup.
            text, found = re.subn(r'(?m)^(# )?riverside-rockets = "Riverside Rockets"$',
                                  f'riverside-rockets = "{team}"',
                                  path.read_text(encoding="utf-8"))
            self.assertEqual(found, 1, "no riverside-rockets line to rename")
            path.write_text(text, encoding="utf-8")
        else:
            path.write_text(f'[team_names]\nriverside-rockets = "{team}"\n',
                            encoding="utf-8")

    def section(self, text: str, heading: str = "players: no dashboard written") -> str:
        return text[text.index(f"== {heading}"):text.index("== result")]

    def test_over_a_named_build_it_writes_nothing_and_says_what_is_there(self):
        self.build()
        before = tree(self.cwd / "site")
        self.rename("Ana Fernandez Shield")
        done = self.build("--players", "numbered", expect=2)
        after = tree(self.cwd / "site")
        self.assertEqual(sorted(name for name in {*before, *after}
                                if before.get(name) != after.get(name)),
                         ["build-status.js"])
        self.assertEqual(self.section(done.out), self.BLOCK + """
  Nothing was written to site by this run but site/build-status.js, so the
  front page now says the last build failed. site already holds 1 of its
  dashboards from an earlier build, and 1 of them prints players' names:
  site/tuesday-division-b. The lines above give each name's shape and never
  the name, so this section can be quoted as it is.

""")
        self.assertIn("  exit 2: a player's name survived withholding, no dashboard "
                      "written", done.out)
        self.assertNotRegex(fold(self.section(done.out)), r"ana|fernandez")

    def test_check_writes_not_even_the_status(self):
        self.build()
        before = tree(self.cwd / "site")
        self.rename("Ana Fernandez Shield")
        done = self.build("--check", "--players", "numbered", expect=2)
        self.assertEqual(tree(self.cwd / "site"), before)
        self.assertEqual(self.section(done.out), self.BLOCK + """
  Nothing was written to site by this run. site already holds 1 of its
  dashboards from an earlier build, and 1 of them prints players' names:
  site/tuesday-division-b. The lines above give each name's shape and never
  the name, so this section can be quoted as it is.

""")

    def test_a_refusal_repeated_writes_not_even_the_status(self):
        # The file already says the last build failed, so the refusal after it
        # writes those bytes no more than any other file, and the section says only
        # what this run did. Every scheduled run after the first refusal is this
        # one, because the wrapper keeps exit 2 and builds again. Measured before:
        # a section told the status was written whenever the file was there passed
        # every test of the refusal (/tmp/t4/scratch/f2/fixup/before-L.log).
        self.build()
        self.rename("Ana Fernandez Shield")
        self.build("--players", "numbered", expect=2)
        status = self.cwd / "site" / "build-status.js"
        before = tree(self.cwd / "site"), status.stat().st_mtime_ns
        done = self.build("--players", "numbered", expect=2)
        self.assertEqual((tree(self.cwd / "site"), status.stat().st_mtime_ns), before)
        self.assertEqual(self.section(done.out), self.BLOCK + """
  Nothing was written to site by this run. site already holds 1 of its
  dashboards from an earlier build, and 1 of them prints players' names:
  site/tuesday-division-b. The lines above give each name's shape and never
  the name, so this section can be quoted as it is.

""")

    def test_the_security_policy_asks_for_the_section_by_its_heading(self):
        # SECURITY.md tells whoever reports a leak to quote this section by the
        # heading the run prints, so a heading renamed without it sends them looking
        # for one the report no longer has.
        heading = self.BLOCK.splitlines()[0].rstrip("= ")
        policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn(f"`{heading}`", policy)

    def test_the_readme_says_what_a_refused_run_writes(self):
        # As the tests above measure it: no dashboard, the status file alone, and
        # not even that under --check. Measured before: "If one is still there,
        # this run writes nothing at all."
        text = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        start = text.index("If one is still there,")
        said = text[start:text.index("The report says where", start)]
        for wanted in ("writes no dashboard", "`build-status.js`", "`--check`"):
            self.assertIn(wanted, said)

    def test_with_nothing_there_it_says_nothing_was_written(self):
        self.rename("Ana Fernandez Shield")
        done = self.build("--players", "numbered", expect=2)
        self.assertFalse((self.cwd / "site").exists())
        self.assertEqual(self.section(done.out), self.BLOCK + """
  Nothing was written to site by this run.

""")

    def test_an_accent_does_not_hide_the_name(self):
        self.rename("Ana Fernández Shield")
        done = self.build("--players", "numbered", expect=2)
        self.assertFalse((self.cwd / "site").exists())
        self.assertIn("teams[3].name and 10 other place(s) print", done.out)

    def test_a_name_the_rewrite_missed_is_a_fault_in_this_program(self):
        doc = hand_season(warnings=("Priya Halloran played up",))
        found = learned(doc)
        with mock.patch.object(anonymise, "withhold",
                               side_effect=lambda text, *a, **k: text):
            done = anonymise.anonymise(doc, mode="numbered", secrets=found)
        found_ = anonymise.refusals([done.season], found, club=Club(), cfg=HAND_CONFIG)
        self.assertEqual([(r.kind, r.place, r.shape) for r in found_],
                         [("fault", "matches[0].warnings[0]", "<2 words, 14 chars>")])

    def test_the_rule_is_built_once_for_every_string_it_reads(self):
        """The guard asks about every string with the same secrets: one pattern.

        Measured before the pattern was kept: 4,505 strings of the 10-team demo each
        sorted, escaped and joined every secret again, and the guard took 567 ms of
        a 2.9 s build, a fifth of it. Rejected: a pattern held on ``Secrets``, which
        would be a second place the rule is written down.
        """
        doc = hand_season(warnings=("Priya Halloran played up",))
        done = anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        # A spelling no other test learns, so the pattern is new to this process.
        cfg = config_mod.Config(path=Path("config.toml"),
                                namesakes=HAND_CONFIG.namesakes,
                                player_names={"wynn-tolliver": "Wynn Tolliver"})
        found = anonymise.secrets([doc], cfg, Club())
        with mock.patch.object(anonymise.redact.re, "compile",
                               wraps=anonymise.redact.re.compile) as built:
            self.assertEqual(anonymise.refusals([done.season] * 3, found, club=Club(),
                                                cfg=cfg), [])
        self.assertEqual(built.call_count, 1)


def _two_divisions(data: Path) -> None:
    """The demo on a Tuesday, and a smaller one of its own on a Wednesday."""
    for night, argv in (("tue", ("--day", "Tuesday", "--division", "A")),
                        ("wed", ("--teams", "4", "--seed", "11"))):
        subprocess.run((sys.executable, str(DEMO), "--out", str(data / night), *argv),
                       check=True, capture_output=True, timeout=600)


def divisions() -> Path:
    """Two competitions of one season, of six teams and of four, three players each."""
    return project("divisions", _two_divisions)


class TestAWithheldBuildWalksEachDocumentOnce(unittest.TestCase):
    """The document the build names, walked once, and the one it writes, once.

    The defect this closes: the words the page prints anyway, the spellings and the
    pseudonyms each walked the named document leaf by leaf for themselves, the last
    on the rewrite's copy of it, and the labels it prints whole walked it a fourth
    time on a run where a card left a squad line out. With the guard's walk of what
    the build writes, a numbered build walked each competition four times, or five,
    and spent more than a tenth of the build withholding names. Measured in
    process, the median of 9 demo builds six times over, the trees in turn: 11.1%
    to 11.4% of the build before (61 to 67 ms), over 19,370 leaves, and 7.4% to 8.1%
    after (40 to 47 ms), over 9,686. Now one walk of each named document serves all
    four, and the guard walks each document the build writes, once: a run of two
    competitions walked eight times, and walks four.

    Rejected: sharing the named walk with the guard too, which is one walk in all
    but has the guard search what the rewrite says it wrote and not what it wrote;
    and a walk kept on the side by the document's identity, which a document
    changed after it was walked would read stale.
    """

    WALKER = inspect.signature(anonymise._leaves)

    @classmethod
    def top(cls, walked: mock.MagicMock) -> list:
        """The documents walked, in order: a walk's own recursion is not a walk.

        Only the recursion names a pattern or a place, so each call is bound as the
        walker binds it. Counting only a call handed just the document let a reader
        walk again with the defaults spelt out, or by keyword, and not be counted.
        """
        found = []
        for call in walked.call_args_list:
            bound = cls.WALKER.bind(*call.args, **call.kwargs)
            bound.apply_defaults()
            if bound.arguments["pattern"] == bound.arguments["place"] == "":
                found.append(bound.arguments["value"])
        return found

    def walks(self, data: Path) -> list[bool]:
        """Whether each document a numbered build of *data* walked is one it writes."""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(anonymise, "_leaves",
                                  wraps=anonymise._leaves) as walked, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = build_mod.main(["--data", str(data),
                                   "--out", str(Path(tmp) / "site"),
                                   "--config", str(Path(tmp) / "config.toml"),
                                   "--generated", STAMP, "--players", "numbered"])
        self.assertEqual(code, 0)
        return [doc["meta"]["provenance"]["players"] == anonymise.WITHHELD
                for doc in self.top(walked)]

    def test_the_demo_is_walked_named_once_and_as_written_once(self):
        self.assertEqual(self.walks(demo() / "data"), [False, True])

    def test_a_squad_line_a_card_left_out_costs_no_walk_of_its_own(self):
        self.assertEqual(self.walks(headings() / "data"), [False, True])

    def test_learning_from_a_document_walks_it_once(self):
        doc = hand_season()
        with mock.patch.object(anonymise, "_leaves",
                               wraps=anonymise._leaves) as walked:
            anonymise.secrets([doc], HAND_CONFIG, Club(), dropped=(HEADING,))
        self.assertEqual(len(self.top(walked)), 1)

    def test_two_competitions_are_each_walked_named_once_and_as_written_once(self):
        # Each document the build writes is searched, and not only the first.
        self.assertEqual(self.walks(divisions() / "data"), [False, False, True, True])


class TestTheGuardSearchesEachStringItReadsOnceARun(unittest.TestCase):
    """A string the guard reads again in a run is not searched for names again.

    The defect: :func:`anonymise.refusals` searched every string it read, although
    most repeat: a team's name is in every match it played, and a pseudo slug in
    every set row. Measured before, the median of 25 numbered builds in process
    taken in turn with the fixed tree's (``/tmp/t7/scratch/ANON2/meas/interleave.sh``):
    the demo's guard searched 1,120 times for 107 strings, 108 with a string read as
    an id counted apart from the same string read as words, and the search was
    18.0% of withholding; a ten-team demo of 90 cards searched 4,505 times for 245
    strings, or 246, and 20.2%. Now each is searched once a run, and a string read
    as an id apart from the same string read as words, since what an id is searched
    for is more. So is an id or a card a refusal prints, however many refusals
    print it, and a name found is reported at every place it is, as before.
    Rejected: ``functools.lru_cache`` on the search, which cannot hash the run's
    secrets and, held by the module, would keep the names a run withholds after it.
    """

    SEARCH = inspect.signature(anonymise._hits)

    def asked(self, data: Path, config: str = "", code: int = 0
              ) -> list[tuple[str, bool]]:
        """``(text, words)`` of each search a numbered build of *data* makes."""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(anonymise, "_hits",
                                  wraps=anonymise._hits) as searched, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            if config:
                (Path(tmp) / "config.toml").write_text(config, encoding="utf-8")
            done = build_mod.main(["--data", str(data),
                                   "--out", str(Path(tmp) / "site"),
                                   "--config", str(Path(tmp) / "config.toml"),
                                   "--generated", STAMP, "--players", "numbered"])
        self.assertEqual(done, code)
        asked = []
        for call in searched.call_args_list:
            bound = self.SEARCH.bind(*call.args, **call.kwargs)
            bound.apply_defaults()
            asked.append((bound.arguments["text"], bound.arguments["words"]))
        return asked

    def again(self, asked: list[tuple[str, bool]],
              least: int = 100) -> list[tuple[str, bool]]:
        self.assertGreater(len(asked), least)
        return sorted(key for key, times in Counter(asked).items() if times > 1)

    def test_no_string_of_the_demo_is_searched_twice(self):
        self.assertEqual(self.again(self.asked(demo() / "data")), [])

    def test_nor_one_that_two_competitions_both_hold(self):
        self.assertEqual(self.again(self.asked(divisions() / "data")), [])

    def test_nor_the_id_and_the_card_a_refused_build_prints(self):
        """A team and a competition's label named after a line under both squads.

        Two refusals, each naming the card the line is on, and the label's naming
        the competition by its id: the card is searched once, and the id not again
        after the front page's id was. The searches of a refusal are made only
        where a name survives, which no build of the demo does.
        """
        config = ('[team_names]\nexample-bravos = "Robin Quaife Shield"\n\n'
                  '[competitions.tuesday-division-c]\nlabel = "Robin Quaife Cup"\n'
                  'day = "Tuesday"\ndivision = "C"\n')
        with tempfile.TemporaryDirectory() as tmp:
            write_heading_cards(Path(tmp) / "data")
            asked = self.asked(Path(tmp) / "data", config, code=2)
        self.assertIn(("data/Round1/r1.pdf", True), asked)
        self.assertIn(("tuesday-division-c", True), asked)
        self.assertEqual(self.again(asked, least=20), [])

    def refused(self, published: list[dict], found) -> list[tuple[str, ...]]:
        return [(r.kind, r.owner, r.place, r.shape) for r in anonymise.refusals(
            published, found, club=Club(), cfg=HAND_CONFIG)]

    def test_a_string_read_as_an_id_and_as_words_is_searched_as_each(self):
        """An id's words are refused, and the same string in a message's are not.

        So the one string has two answers in a run, and neither may be taken for
        the other: whichever is read first, the key is refused and the message is
        not. ``quell-cup`` is planted in the other competition's rewritten
        message, where the rewrite would withhold it whole, because the guard
        reads what the build writes and not what the rewrite meant to write.
        """
        doc = hand_season(comp="quell-cup")
        found = learned(doc)
        keyed = anonymise.anonymise(doc, mode="numbered", secrets=found).season
        other = anonymise.anonymise(hand_season(), mode="numbered",
                                    secrets=found).season
        other["matches"][0]["warnings"] = ["quell-cup"]
        refusal = ("key", "<9 chars>", "meta.competition", "<5 chars>")
        self.assertEqual(self.refused([other, keyed], found), [refusal])
        self.assertEqual(self.refused([keyed, other], found), [refusal])

    def test_a_run_finds_what_its_own_secrets_hold_whatever_ran_before(self):
        doc = hand_season(comp="quell-cup")
        found = learned(doc)
        published = [anonymise.anonymise(doc, mode="numbered", secrets=found).season]
        nobody = anonymise.secrets([], HAND_CONFIG, Club())
        self.assertEqual(self.refused(published, nobody), [])
        self.assertEqual(self.refused(published, found), [
            ("key", "<9 chars>", "meta.competition", "<5 chars>")])


class TestEachCompetitionsPlayersRowCountsItsOwnPlayers(unittest.TestCase):
    """Two competitions withheld in one run: each one's ``players`` row is its own.

    Each rewritten document is filed under the competition its own ``meta`` names.
    Measured on the first version of 4.121, which paired the rewrites with the
    named documents by position: with the rewrites handed back in the other order,
    no test failed, every file under the site was the same, and the report said
    the Tuesday division, six teams of three, had 12 players withheld and the
    Wednesday's, four teams, 18 (``/tmp/t6/scratch/m18/meas/pairing.log``).
    Rejected: pairing by position and holding the run to its order, which nothing
    else reads.
    """

    @classmethod
    def setUpClass(cls):
        cls.done = run(divisions(), "--players", "numbered", "--out", "numbered")

    def row(self, competition: str) -> str:
        """The ``players`` row of *competition*'s section of the report."""
        out = self.done.out
        start = out.index(f"\n== {competition} =")
        section = out[start:out.index("\n== ", start + 1)]
        return re.search(r"^  players +(.+)$", section, re.M).group(1)

    def test_each_counts_the_players_of_its_own_teams(self):
        self.assertEqual(self.done.code, 0, self.done.text[-2000:])
        self.assertEqual([self.row(competition) for competition
                          in ("tuesday-division-a", "wednesday-division-3")],
                         ["18 withheld, numbered", "12 withheld, numbered"])


class TestTheFrontPageIsCheckedLikeTheDashboards(CliCase):
    """The title, the club, every competition's label: the front page is a file too.

    Checked from the picker's inputs, and a competition the config declares is
    checked whether or not a card has arrived for it, because the front page lists
    it either way. Rejected: checking the documents only, which leaves the one page
    everybody opens first.
    """

    def refused(self, config: str) -> str:
        (self.cwd / "config.toml").write_text(config, encoding="utf-8")
        done = self.build("--players", "numbered", expect=2)
        self.assertFalse((self.cwd / "site").exists())
        said = done.out[done.out.index("== players"):]
        return said[:said.index("\n\n")]

    def test_the_title(self):
        self.assertEqual(self.refused('[build]\ntitle = "Ana Fernandez Memorial '
                                      'Pennant"\n'), """\
== players: no dashboard written =============================================
  front page
      the title prints a player's name, <2 words, 13 chars>, as it stands.
      Rename it in config.toml: the title under [build], the club under
      [club], a competition under its [competitions] section.""")

    def test_a_competition_no_card_has_arrived_for(self):
        said = self.refused('[competitions."fernandez-cup"]\nlabel = "Ana Fernandez '
                            'Cup"\nday = "Saturday"\ndivision = "A"\n')
        self.assertIn("the label of competition fernandez-cup prints a player's "
                      "name, <2 words,", said)

    def test_a_competition_that_has_cards_is_reported_once(self):
        """Its label is in its own document and on the front page: one line of it.

        Measured before: ``tuesday-division-b`` / ``meta.label`` under the advice for
        a team, and then ``front page`` / ``the label of competition
        tuesday-division-b`` for the same line of config.toml. Rejected: reporting
        the document's copy, whose place a club cannot find in anything it wrote.
        """
        self.assertEqual(self.refused('[competitions."tuesday-division-b"]\n'
                                      'label = "Ana Fernandez Cup"\n'), """\
== players: no dashboard written =============================================
  front page
      the label of competition tuesday-division-b and 1 other place(s) print a
      player's name, <2 words, 13 chars>, as it stands. Rename it in
      config.toml: the title under [build], the club under [club], a
      competition under its [competitions] section.""")

    def test_a_label_is_reported_before_the_front_page(self):
        self.assertEqual(self.refused('[build]\ntitle = "Bo Nakamura Memorial '
                                      'Pennant"\n\n[team_names]\nriverside-rockets = '
                                      '"Ana Fernandez Shield"\n'),
                         TestTheBuildWritesNothingWhenANameSurvives.BLOCK + """\
  front page
      the title prints a player's name, <2 words, 11 chars>, as it stands.
      Rename it in config.toml: the title under [build], the club under
      [club], a competition under its [competitions] section.""")


class TestAConfigFileNamedForAPlayerIsToldToBeRenamedAndIsNotNamed(CliCase):
    """A config file whose name is a player's is sent to ``--config``, by its shape.

    Measured before (/tmp/t7/scratch/CF1/probe/before2/numbered.txt): ``--config
    priya-halloran.toml --players numbered`` over the demo's cards stopped as it
    should, and told the club "the config file name prints a player's name, <14
    chars>, as it stands. Rename it in config.toml: the title under [build], the
    club under [club], a competition under its [competitions] section." None of
    those is the file's name, and config.toml was not the file read. The file's
    name is a place of its own now, with the one step that moves it, and while it
    holds a name the other places' advice says "your config file", as the section
    gives every name by its shape. Rejected: the front page's advice with the
    file's name put into it, which prints the name the section is refusing.
    """

    FILE = "ana-fernandez.toml"
    TITLE = '[build]\ntitle = "Ana Fernandez Memorial Pennant"\n'

    def refused(self, name: str, config: str = "") -> str:
        (self.cwd / name).write_text(config, encoding="utf-8")
        done = self.build("--config", name, "--players", "numbered", expect=2)
        self.assertFalse((self.cwd / "site").exists())
        said = done.out[done.out.index("== players"):]
        return said[:said.index("\n\n")]

    def test_the_file_s_name_is_told_to_be_renamed_and_given_to_config(self):
        self.assertEqual(self.refused(self.FILE), """\
== players: no dashboard written =============================================
  front page
      the config file name prints a player's name, <13 chars>, as it stands.
      Rename the file, and give its new name to --config.""")

    def test_and_no_place_s_advice_beside_it_names_the_file(self):
        said = self.refused(self.FILE, self.TITLE)
        self.assertIn("the title prints a player's name", said)
        self.assertIn("the config file name prints a player's name", said)
        self.assertIn("Rename it in your config file: the title under [build]",
                      " ".join(said.split()))
        self.assertNotIn("fernandez", said.lower())

    def test_a_file_of_another_name_is_the_one_the_advice_names(self):
        said = " ".join(self.refused("other.toml", self.TITLE).split())
        self.assertIn("as it stands. Rename it in other.toml: the title under "
                      "[build], the club under [club], a competition under its "
                      "[competitions] section.", said)
        self.assertNotIn("config.toml", said)


class TestACompetitionFiledUnderAPlayersSurnameWritesNothing(CliCase):
    """Kit Vasse plays here, and a competition is filed under ``vasse-cup``.

    Each case below built before and wrote ``vasse`` to the front page's data, the
    second and third to a dashboard's folder and address as well, as the demo did
    (:class:`TestAWordOfANameInACompetitionsIdOrSeasonKeyIsRefused`). The key is
    given by its shape, with where the club changes it and what that moves.
    Rejected: the advice for a label, which sends the club to ``[team_names]``,
    and printing the key, which is the name.
    """

    BLOCK = """\
== players: no dashboard written =============================================
  front page
      the id of competition <9 chars> holds a player's name or a word of one,
      <5 chars>, and the dashboard's folder and web address are made from it.
      Rename it: a competition's id is the name of its [competitions] section
      in config.toml, and a season's key the name of its folder under data."""

    def refused(self, config: str, *argv: str) -> str:
        (self.cwd / "config.toml").write_text(config, encoding="utf-8")
        done = self.build("--players", "numbered", *argv, expect=2)
        self.assertFalse((self.cwd / "site").exists())
        said = done.out[done.out.index("== players"):]
        return said[:said.index("\n\n")]

    def test_a_section_the_front_page_lists_before_its_cards_arrive(self):
        self.assertEqual(self.refused('[competitions."vasse-cup"]\nlabel = "Winter '
                                      'Cup"\nday = "Thursday"\ndivision = "A"\n'),
                         self.BLOCK)

    def test_and_one_whose_name_runs_it_on_with_an_underscore_or_a_year(self):
        # Measured before, through a build: exit 0 and the id in the picker's data
        # (TestAWordOfANameInACompetitionsIdOrSeasonKeyIsRefused).
        for key in ("vasse_cup", "vasse2026"):
            with self.subTest(key=key):
                self.assertEqual(self.refused(
                    f'[competitions.{key}]\nlabel = "Winter Cup"\nday = "Thursday"\n'
                    f'division = "A"\n'), self.BLOCK)

    def test_the_advice_names_the_folder_the_cards_were_read_from(self):
        # Where --data sends the build, and not where a first run puts the cards.
        (self.cwd / "data").rename(self.cwd / "cards")
        self.assertEqual(self.refused('[competitions."vasse-cup"]\nlabel = "Winter '
                                      'Cup"\nday = "Thursday"\ndivision = "A"\n',
                                      "--data", "cards"),
                         self.BLOCK.replace("under data.", "under cards."))

    def test_a_section_its_cards_are_filed_under(self):
        said = self.refused('[competitions."vasse-cup"]\nday = "Tuesday"\n'
                            'division = "B"\n')
        self.assertIn("the id of competition <9 chars> and 1 other place(s) hold a "
                      "player's name or a word of one, <5 chars>,",
                      " ".join(said.split()))

    def test_a_season_folder_in_multi_mode(self):
        """And where the cards print no season, so the page's is the folder's name.

        Measured before with no season line on the cards: exit 0, and the season
        on the page and in the front page's data ``2026 Vasse``, the key unslugged,
        which made the key's words the page's own.
        """
        write = cw.write_card_file
        for printed in ("Season 1", ""):
            with self.subTest(season=printed), mock.patch.object(
                    cw, "write_card_file", lambda path, spec, printed=printed: write(
                        path, dataclasses.replace(spec, season_line=printed))):
                shutil.rmtree(self.cwd / "data")
                write_season_cards(self.cwd / "data" / "2026-vasse")
                said = " ".join(self.refused('[build]\nseasons = "multi"\n').split())
                self.assertIn("the id of competition <29 chars> and ", said)
                self.assertIn("hold a player's name or a word of one, <5 chars>,",
                              said)
                self.assertNotIn("vasse", fold(said))

    def test_a_section_listed_nowhere_is_not_read(self):
        """No day and division: nothing is filed under it and no file holds it.

        Measured before: this label, a player's full name, was refused for the
        front page, though the report says the section is listed nowhere and
        nothing under ``site`` held a word of it.
        """
        (self.cwd / "config.toml").write_text(
            '[competitions."vasse-cup"]\nlabel = "Kit Vasse Cup"\n', encoding="utf-8")
        self.build("--players", "numbered")
        held = [path for path in (self.cwd / "site").rglob("*") if path.is_file()
                and "vasse" in fold(path.read_text(encoding="utf-8",
                                                   errors="replace"))]
        self.assertEqual(held, [])


class TestAWithheldBuildPublishesItsCardsAsANamesBuildDoes(CliCase):
    """The folder the cards are in is named once, by the rule a names build names it.

    ``export.published`` names the data folder by what it resolves to. A withheld
    build published each document before the rewrite, and the write published it
    again, from a folder name that was no longer a path, so it was resolved against
    the folder the build was run in. Measured with ``data`` a link to
    ``store/season26`` and a link ``season26`` to ``store/old`` beside it: names
    wrote ``source_dir`` ``season26``, numbered wrote ``old``, and with the second
    link to a folder named after a player the numbered build was refused as a fault
    in this program. Rejected: a ``published`` that recognises a document it has
    already published, which is a second rule for what a published document is.
    """

    def test_a_link_beside_the_data_folder_changes_nothing(self):
        store = self.cwd / "store"
        store.mkdir()
        shutil.move(self.cwd / "data", store / "season26")
        os.symlink(Path("store") / "season26", self.cwd / "data")
        for decoy in ("old", "Ana Fernandez"):
            with self.subTest(decoy=decoy):
                (store / decoy).mkdir()
                beside = self.cwd / "season26"
                if beside.is_symlink():
                    beside.unlink()
                os.symlink(Path("store") / decoy, beside)
                cards = {}
                for mode in ("names", "numbered"):
                    self.build("--players", mode, "--out", f"site-{mode}")
                    doc = json.loads(next((self.cwd / f"site-{mode}").rglob(
                        "season.json")).read_text(encoding="utf-8"))
                    self.assertEqual(doc["meta"]["source_dir"], "season26")
                    cards[mode] = sorted(m["source"] for m in doc["matches"])
                    shutil.rmtree(self.cwd / f"site-{mode}")
                self.assertEqual(cards["numbered"], cards["names"])
                self.assertTrue(all(card.startswith("season26/Round")
                                    for card in cards["names"]), cards["names"])


# --- the setting ------------------------------------------------------------------


class TestPlayersIsABuildSettingWithThreeValues(unittest.TestCase):
    """``[build] players``: names, initials or numbered, and names when it is missing.

    Read without regard to case or the spaces round it. Anything else stops the
    build with the line it read and the nearest value, because this is the one line
    in the file whose mistake publishes something. Rejected: falling back to names,
    which is the mistake the line was written to prevent.
    """

    def load(self, text: str | None) -> config_mod.Config:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            if text is not None:
                path.write_text(text, encoding="utf-8")
            return config_mod.load(path)

    def test_the_three_values(self):
        for written, mode in (("names", "names"), ("Initials", "initials"),
                              (" NUMBERED ", "numbered")):
            with self.subTest(written=written):
                self.assertEqual(self.load(f'[build]\nplayers = "{written}"\n')
                                 .players_mode, mode)
        self.assertEqual(self.load("[build]\n").players_mode, "names")
        self.assertEqual(self.load(None).players_mode, "names")

    def test_anything_else_stops_the_build(self):
        # The three as [build] seasons lists its two, where they were "one of" them,
        # which offers the choice twice.
        with self.assertRaises(config_mod.ConfigError) as caught:
            self.load('[build]\nplayers = "initial"\n')
        self.assertTrue(str(caught.exception).endswith(
            '[build] players = "initial" is not "names", "initials" or '
            '"numbered". Did you mean initials? Nothing was built: this line decides '
            "whether players' names are published, so a value this program cannot "
            "read is not guessed at."))


class TestAPlayersLineTheBuildCannotHonourStopsTheBuild(CliCase):
    """A players line misspelt or in the wrong table stops the build, and says where.

    Every other mistake in the file costs a wrong label and is warned about; this
    one, ignored, publishes the names it was written to withhold, and a weekly
    script does not read warnings. Even beside ``[build] players = "names"``,
    because a club withholding one division is a club whose other divisions a build
    would publish. A value one slip from a mode, or none a mode at all, is refused
    as the mode is, because it is still a line asking for something. Rejected: the
    warning every other misplaced key gets.
    """

    WRONG_TABLE = ('config.toml: players = "numbered" is written {place}, where it '
                   'sets nothing. It is a [build] setting, for every dashboard at '
                   'once: write players = "numbered" under [build]. Nothing was '
                   'built, because building would publish the names this line was '
                   'written to withhold.')
    MISSPELT = ("config.toml: [build] {key} is not a setting this program "
                "understands. Did you mean players? Nothing was built: a misspelt "
                "players line would publish the names it was written to withhold.")
    # The three listed as the line in [build] lists them, and [build] seasons its two.
    UNREAD = ('config.toml: players = "{value}" is written {place}, where it sets '
              'nothing, and "{value}" is not "names", "initials" or '
              '"numbered". It is a [build] setting, for every dashboard at once.'
              '{meant} Nothing was built: a players line this program cannot read '
              'is not guessed at, and ignoring it would publish the names it may '
              'have been written to withhold.')

    def test_each_one_stops_it(self):
        valid = '[build]\nplayers = "names"\n'
        cases = {
            '[build]\nplayer = "numbered"\n': self.MISSPELT.format(key="player"),
            '[build]\nplayres = "numbered"\n': self.MISSPELT.format(key="playres"),
            '[build]\nplayers_mode = "numbered"\n':
                self.MISSPELT.format(key="players_mode"),
            'players = "numbered"\n' + valid:
                self.WRONG_TABLE.format(place="at the top of the file"),
            '[rules]\nplayers = "numbered"\n':
                self.WRONG_TABLE.format(place="under [rules]"),
            '[player_names]\nplayers = "numbered"\n':
                self.WRONG_TABLE.format(place="under [player_names]"),
            '[biuld]\nplayers = "numbered"\n':
                self.WRONG_TABLE.format(place="under [biuld]"),
            valid + '[competitions."juniors"]\nplayers = "numbered"\n':
                self.WRONG_TABLE.format(place="under [competitions.juniors]"),
            # Misspelt and misplaced at once: each was a warning, exit 0 and every
            # name published.
            '[club]\nplayers = "initals"\n': self.UNREAD.format(
                value="initals", place="under [club]",
                meant=' Did you mean players = "initials" under [build]?'),
            '[rules]\nplayers = "Initial"\n': self.UNREAD.format(
                value="Initial", place="under [rules]",
                meant=' Did you mean players = "initials" under [build]?'),
            'players = "numberd"\n': self.UNREAD.format(
                value="numberd", place="at the top of the file",
                meant=' Did you mean players = "numbered" under [build]?'),
            'players = "anonymous"\n': self.UNREAD.format(
                value="anonymous", place="at the top of the file", meant=""),
        }
        for config, said in cases.items():
            with self.subTest(config=config):
                (self.cwd / "config.toml").write_text(config, encoding="utf-8")
                done = self.build(expect=2)
                self.assertEqual(done.err, f"error: {said}\n")
                self.assertFalse((self.cwd / "site").exists())

    def test_a_line_that_asks_for_nothing_keeps_its_warning(self):
        """A value that is not text, or a team whose name holds the word.

        A key in a table of the club's own names is a team or a player, and only a
        mode or a slip from one is read as a players line there. A key this program
        reads where it is written is no players line at all: a team's ``played`` in
        the sheet table is two slips from ``players``, and is the matches the sheet
        says it has played. Rejected: refusing every value of every key that says
        player, which refuses a team called the Players.
        """
        sheet = '[competitions."tuesday-division-b".expected]\nafter_round = 3\n'
        for config, warned in (
                ("[rules]\nplayers = 3\n",
                 "[rules] players is not a setting this program understands"),
                ('[team_names]\nthe-players = "The Players"\n', ""),
                ('[competitions."tuesday-division-b".captains]\n'
                 '"The Players" = "Bo Nakamura"\n', ""),
                (sheet + '"The Players" = "10.9"\n',
                 "'The Players' = '10.9' does not say which figure it is"),
                (sheet + '"Hilltop Aces" = { points = 10.9, played = "numbered" }\n',
                 "'Hilltop Aces' played = 'numbered' is not a whole number")):
            with self.subTest(config=config):
                (self.cwd / "config.toml").write_text(config, encoding="utf-8")
                done = self.build()
                self.assertIn(warned, " ".join(done.out.split()))

    def test_a_line_that_asks_for_names_keeps_its_warning(self):
        """Ignored, it prints the names it asked for, or withholds them anyway.

        Measured before: ``[club] players = "names"`` stopped the build because
        building "would publish the names this line was written to withhold", and
        beside ``[build] players = "initials"`` told the club to write ``players =
        "names"`` under ``[build]``, which publishes every name. Rejected: the
        refusal a mode gets, whose reason is untrue of this one.
        """
        (self.cwd / "config.toml").write_text('[club]\nplayers = "names"\n',
                                              encoding="utf-8")
        done = self.build()
        self.assertIn("[club] players is not a setting this program understands and "
                      "was ignored.", " ".join(done.out.split()))
        for build in ('players = "initials"', 'players = "initials"\nplayer = "names"'):
            with self.subTest(build=build):
                (self.cwd / "config.toml").write_text(
                    f'[build]\n{build}\n\n[club]\nplayers = "names"\n',
                    encoding="utf-8")
                # Moved by 4.430: exit 1, since the first build printed names and
                # wrote careers.html, which a run withholding names lists as left
                # behind until it is pruned.
                done = self.build(expect=1)
                said = " ".join(done.out.split())
                self.assertIn("[club] players is not a setting this program "
                              "understands and was ignored.", said)
                self.assertIn("players withheld (initials):", said)
                self.assertNotIn('players = "names" under [build]', done.text)

    def test_beside_a_build_that_withholds_a_line_is_only_warned_about(self):
        """``[build] players`` already withholds: ignoring another line publishes no name.

        It costs a label the club did not ask for, which is the kind of mistake
        this file warns about. Rejected: refusing it, with a reason that is untrue
        whenever ``[build]`` withholds.
        """
        (self.cwd / "config.toml").write_text(
            '[build]\nplayers = "initials"\n\n[rules]\nplayers = "numbered"\n',
            encoding="utf-8")
        said = " ".join(self.build().out.split())
        self.assertIn("[rules] players is not a setting this program understands and "
                      "was ignored.", said)
        self.assertIn("players withheld (initials):", said)


class TestThePlayersFlagWinsForOneRunAndIsNeverSaved(CliCase):
    """``--players`` for one run: a public copy beside the club's own, and no trace.

    Rejected: writing the flag back into the config on a first run or under
    ``--discover``, which would turn one withheld copy into every later build's
    setting without anybody having written it.
    """

    def test_one_run_withholds_and_the_next_does_not(self):
        self.build()
        own = tree(self.cwd / "site")
        done = self.build("--out", "public", "--players", "numbered")
        self.assertEqual(tree(self.cwd / "site"), own)
        public = json.loads((self.cwd / "public" / COMP / "season.json").read_text())
        self.assertEqual(public["meta"]["players"], "numbered")
        self.assertIn("players      withheld (numbered, --players): no player's full "
                      "name is in any", done.out)
        self.assertNotIn("players", self.season()["meta"])

    def test_discover_does_not_save_it(self):
        self.build()
        # Moved by 4.430: exit 1, since the names build wrote careers.html, which
        # a run withholding names lists as left behind until it is pruned.
        self.build("--discover", "--force", "--players", "numbered", expect=1)
        self.assertEqual(config_mod.load(self.cwd / "config.toml").players_mode,
                         "names")


class TestDiscoverWritesThePlayersSettingBack(CliCase):
    """A config rewritten from the cards keeps the club's ``players`` line."""

    def test_initials_survive_a_rewrite(self):
        (self.cwd / "config.toml").write_text('[build]\nplayers = "initials"\n',
                                              encoding="utf-8")
        self.build("--discover", "--force")
        self.assertEqual(config_mod.load(self.cwd / "config.toml").players_mode,
                         "initials")

    def test_a_first_run_offers_the_line_commented_out(self):
        self.build()
        written = (self.cwd / "config.toml").read_text(encoding="utf-8")
        self.assertIn('\n# players = "numbered"\n', written)
        self.assertEqual(config_mod.load(self.cwd / "config.toml").players_mode,
                         "names")


# --- the report -------------------------------------------------------------------


class TestTheReportSaysNamesWereWithheldEvenUnderQuiet(CliCase):
    """A withheld run says so in its result, every time, ``--quiet`` included.

    It is a promise about every file the run wrote, and an unattended weekly run is
    the run that most needs to make it. The README quotes the line, so it is held
    to what the program prints. It promises no full name, which is what the search
    looks for: every whole spelling a player was given. Measured with a competition
    filed under one player's surname, the line said ``no player's name`` while the
    front page's ``picker-data.js`` carried the surname in the id. Rejected: the
    promise of no name at all, which that run broke.
    """

    LINE = ("  players      withheld (numbered): no player's full name is in any "
            "dashboard\n               this run {built}\n")

    def test_quiet_and_check(self):
        (self.cwd / "config.toml").write_text('[build]\nplayers = "numbered"\n',
                                              encoding="utf-8")
        self.assertIn(self.LINE.format(built="built"), self.build("--quiet").out)
        self.assertIn(self.LINE.format(built="would build"),
                      self.build("--check", "--quiet").out)

    def test_the_readme_quotes_it(self):
        block = readme_block(self, "## Publishing without names",
                             contains="players      withheld")
        self.assertEqual("\n".join(block) + "\n", self.LINE.format(built="built"))

    def test_each_competition_says_how_many(self):
        (self.cwd / "config.toml").write_text('[build]\nplayers = "initials"\n',
                                              encoding="utf-8")
        done = self.build()
        self.assertIn("\n  players      12 withheld, by initials\n",
                      self.section(done.out, COMP))


class TestANamesBuildReportsWhatItAlwaysDid(CliCase):
    """A club that never set the option sees no new line anywhere in its report."""

    def test_no_players_line_and_the_same_rules(self):
        named = self.build()
        self.assertNotRegex(named.out, r"(?m)^\s+players\s")
        # Moved by 4.430: exit 1, for the careers page the names build wrote.
        withheld = self.build("--players", "numbered", expect=1)
        self.assertEqual(self.section(withheld.out, "rules in force"),
                         self.section(named.out, "rules in force"))


class TestADashboardLeftBehindWithNamesFailsAWithheldRun(CliCase):
    """A withheld run over an old named dashboard it no longer builds exits 1.

    The dashboard is not linked any more, and it opens by double-click like every
    other copy, with every name in it: a run that promised no names under
    ``site/`` has not kept the promise while it is there. Rejected: exit 0 with a
    line, which is the unattended run publishing names and saying so to nobody.
    """

    def test_it_says_so_until_it_is_pruned(self):
        write_thursday_card(self.cwd / "data")
        self.build()
        shutil.rmtree(self.cwd / "data" / "Thursday")
        done = self.build("--players", "numbered", expect=1)
        self.assertIn("      no card under data belongs to it any more\n"
                      "      it prints players' names, and this run withholds them\n",
                      done.out)
        # Moved by 4.430: the names build wrote careers.html too, which still
        # prints names and is the line's second clause; the prune removes it.
        self.assertIn("exit 1: all 6 card(s) scored, no errors, 1 dashboard(s) "
                      "still print players'\n          names and careers.html "
                      "still prints players' names\n", done.out)
        self.build("--players", "numbered", "--prune")
        self.assertFalse((self.cwd / "site" / "thursday-division-f").exists())

    def test_a_withheld_one_left_behind_is_only_left_behind(self):
        write_thursday_card(self.cwd / "data")
        self.build("--players", "numbered")
        shutil.rmtree(self.cwd / "data" / "Thursday")
        done = self.build("--players", "numbered")
        self.assertIn("site/thursday-division-f", done.out)
        self.assertNotIn("it prints players' names", done.out)


class TestANamedDashboardPruneKeptIsSaidOnceOnTheLastLine(CliCase):
    """A dashboard ``--prune`` could not remove, printing names, is one clause.

    Measured before (/tmp/t6/scratch/m6/before.txt): the Thursday dashboard a
    names build left, with a notes.txt of the club's in it, run with
    ``--players numbered --prune``, ended "exit 1: all 6 card(s) scored, no
    errors, 1 dashboard(s) not removed, 1 dashboard(s) still print players'
    names": one directory, counted twice, on the line a script reads. With a
    withheld copy kept beside it the line said 2 not removed and 1 printing
    names, three for two directories.

    The careers page's rule, for the dashboards: a copy still printing names is
    said as the names it prints, and "not removed" counts the others. The pruned
    section above still says how many --prune kept and why, since that is where
    the club reads what to move. Rejected: one clause naming both, "1 dashboard(s)
    not removed and still printing players' names", a third form of the same
    fact beside the two a script already matches.
    """

    NAMED = ("exit 1: all 6 card(s) scored, no errors and 1 dashboard(s) still "
             "print players' names")

    def last_line(self, run: Run) -> str:
        lines = run.out.splitlines()
        at = max(i for i, line in enumerate(lines) if line.startswith("  exit "))
        return " ".join(" ".join(lines[at:]).split())

    def thursday_left(self) -> Path:
        """A names build of Thursday, its cards gone, a file of the club's in it."""
        write_thursday_card(self.cwd / "data")
        self.build()
        shutil.rmtree(self.cwd / "data" / "Thursday")
        stale = self.cwd / "site" / "thursday-division-f"
        (stale / "notes.txt").write_text("club notes\n", encoding="utf-8")
        return stale

    def test_the_copy_is_said_by_the_names_it_prints(self):
        stale = self.thursday_left()
        run = self.build("--players", "numbered", "--prune", expect=1)
        self.assertTrue((stale / "notes.txt").is_file())
        self.assertEqual(self.last_line(run), self.NAMED)
        pruned = " ".join(self.section(run.out, "pruned").split())
        self.assertIn("1 dashboard(s) not removed: deleting one", pruned)

    def test_a_withheld_copy_kept_beside_it_is_still_not_removed(self):
        write_thursday_card(self.cwd / "data")
        self.build()
        shutil.copytree(self.cwd / "site" / "thursday-division-f",
                        self.cwd / "site" / "thursday-division-f-old")
        self.build("--players", "numbered", expect=1)
        shutil.rmtree(self.cwd / "data" / "Thursday")
        for name in ("thursday-division-f", "thursday-division-f-old"):
            (self.cwd / "site" / name / "notes.txt").write_text(
                "club notes\n", encoding="utf-8")
        run = self.build("--players", "numbered", "--prune", expect=1)
        self.assertEqual(self.last_line(run),
                         "exit 1: all 6 card(s) scored, no errors, 1 dashboard(s) "
                         "not removed and 1 dashboard(s) still print players' names")

    def test_a_names_run_says_not_removed(self):
        self.thursday_left()
        run = self.build("--prune", expect=1)
        self.assertEqual(self.last_line(run),
                         "exit 1: all 6 card(s) scored, no errors and 1 dashboard(s) "
                         "not removed")


class TestRebuildingWithNamesOverAWithheldDashboardIsSaidOutLoud(CliCase):
    """Names put back on a dashboard that withheld them: allowed, and said.

    Deliberate, often -- a club deciding to publish after all -- but it is also what
    a lost config line looks like, so it is printed whatever ``--quiet`` says.
    """

    def test_the_line(self):
        self.build("--players", "numbered")
        line = ("  players      names: site/tuesday-division-b withheld them until "
                "this run\n")
        self.assertIn(line, self.build("--quiet", "--players", "names",
                                       "--out", "site").out)
        # Moved by 4.430: exit 1, for the careers page the names build wrote.
        self.build("--players", "numbered", expect=1)
        self.assertIn("  players      names: site/tuesday-division-b withholds them, "
                      "and a build\n               would not\n",
                      self.build("--check").out)


class TestSwitchingToWithheldNamesChangesNoResult(CliCase):
    """Withholding names changes what the page calls people and no figure on it.

    So ``== since the last build`` finds nothing to say, and a second withheld
    build writes nothing and keeps the first one's date, as any rebuild of the same
    cards does.
    """

    def test_no_change_and_the_same_bytes(self):
        self.build()
        # Moved by 4.430: both exit 1, for the careers page the names build wrote,
        # which neither prunes.
        first = self.build("--players", "numbered", expect=1)
        self.assertNotIn("== since the last build", first.out)
        again = self.build("--players", "numbered",
                           "--generated", "2026-09-17T12:00:00", expect=1)
        self.assertIn("wrote        nothing", again.out)
        self.assertEqual(self.season()["meta"]["generated"], STAMP)


class TestANamesBuildWritesTheDocumentItAlwaysDid(CliCase):
    """Names mode never reaches the rewrite, and the rewrite never changes its input.

    The report is composed from the named document after the rewrite has run, and
    it is for the person fixing the cards, so a rewrite that changed its input would
    put labels in the one place names are needed.
    """

    def test_names_is_not_a_mode_the_rewrite_takes(self):
        doc = hand_season()
        with self.assertRaises(ValueError):
            anonymise.anonymise(doc, mode="names", secrets=learned(doc))

    def test_the_input_is_untouched(self):
        doc = hand_season(warnings=("Priya Halloran played up",))
        doc["teams"][0]["captain"] = "Priya Halloran"
        kept = copy.deepcopy(doc)
        anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        self.assertEqual(doc, kept)

    def test_a_names_build_is_a_build_with_no_setting(self):
        self.build()
        plain = tree(self.cwd / "site")
        (self.cwd / "config.toml").write_text(
            (self.cwd / "config.toml").read_text(encoding="utf-8").replace(
                '# players = "numbered"', 'players = "names"'), encoding="utf-8")
        self.assertIn("wrote        nothing", self.build().out)
        self.assertEqual(tree(self.cwd / "site"), plain)
        self.assertNotIn("players", self.season()["meta"])


class TestTheRewriteChangesNoFieldOfItsInputAndCopiesNone(unittest.TestCase):
    """The rewrite builds the withheld document new, and copies nothing to do it.

    The defect: :func:`anonymise.anonymise` deep-copied each named document and only
    read the copy, since the rewrite builds every dict and list it returns. Measured
    before, the median of 25 numbered builds in process taken in turn with the fixed
    tree's (``/tmp/t7/scratch/ANON2/meas/interleave.sh``): a copy a competition,
    11.8% of withholding on the demo and 12.0% on a ten-team demo of 90 cards. Now
    the rewrite reads the named document itself, and that it never changes it is
    held across every class of field the table has, each in the demo's documents or
    the edge fixture's, and not by the hand-written season alone, whose partners and
    opponents are empty; and across the one field the rewrite answers again, a
    finding's ``subject_said``, by a hand-made finding withheld whole, since the
    rewrite answers none of their 17 findings otherwise
    (``/tmp/t7/scratch/ANON2/p223/findings.py``). Rejected: keeping the copy as a
    safety net, which would hide a rewrite that changed its input rather than show
    it.
    """

    def test_no_document_is_copied(self):
        doc = hand_season()
        with mock.patch.object(copy, "deepcopy", wraps=copy.deepcopy) as copied:
            anonymise.anonymise(doc, mode="numbered", secrets=learned(doc))
        self.assertEqual(copied.call_count, 0)

    def test_every_class_of_field_is_left_as_it_was(self):
        docs = documents(demo() / "named") + documents(edge() / "named")
        held = {anonymise.classify(p) for doc in docs for p in _leaf_patterns(doc)}
        self.assertEqual(held, set(anonymise.FIELDS.values()))
        found = anonymise.secrets(docs, HAND_CONFIG, Club())
        card = TestAFindingWithheldWholeHasItsSubjectPrintedAgain.CARD
        asked = hand_season(comp="friday-division-a")
        asked["validation"]["warnings"] = [{
            "rule": "fill-in", "severity": "warning", "message": f"{card}: Halloran",
            "subject": card, "detail": "", "subject_said": True}]
        for doc, secrets in [*((doc, found) for doc in docs), (asked, learned(asked))]:
            for mode in anonymise.WITHHOLDING:
                kept = copy.deepcopy(doc)
                anonymise.anonymise(doc, mode=mode, secrets=secrets)
                with self.subTest(competition=doc["meta"]["competition"], mode=mode):
                    self.assertEqual(doc, kept)


class TestTheWithheldPageDoesNotSendAReaderToPlayerNames(CliCase):
    """``provenance.players`` is ``withheld``, and the folded page carries the same.

    The page's advice for a derived name sends a club to ``[player_names]``, which on
    a page printing no name is advice about somebody it does not show.
    """

    def test_provenance_and_the_folded_page(self):
        self.build("--players", "numbered", "--single-file")
        self.assertEqual(self.season()["meta"]["provenance"]["players"],
                         anonymise.WITHHELD)
        data = (self.cwd / "site" / COMP / "season-data.js").read_text(encoding="utf-8")
        line = next(ln for ln in data.splitlines() if ln.startswith("window.SEASON"))
        page = (self.cwd / "site" / COMP / f"{COMP}.html").read_text(encoding="utf-8")
        self.assertIn(line, page)


class TestCheckWithholdsTheSameWayAndWritesNothing(CliCase):
    """``--check`` runs the rewrite and the guard, and writes nothing either way."""

    def test_check(self):
        (self.cwd / "config.toml").write_text('[build]\nplayers = "numbered"\n',
                                              encoding="utf-8")
        done = self.build("--check")
        self.assertFalse((self.cwd / "site").exists())
        self.assertIn("\n  players      12 withheld, numbered\n", done.out)
        (self.cwd / "config.toml").write_text(
            '[build]\nplayers = "numbered"\ntitle = "Kit Vasse Plate"\n',
            encoding="utf-8")
        self.build("--check", expect=2)
        self.assertFalse((self.cwd / "site").exists())


class TestEachSeasonIsNumberedOnItsOwn(CliCase):
    """Two seasons of one division: each numbers its teams from #1.

    Numbers belong to one season, so nothing on either dashboard links a player to
    last year's. Rejected: numbering across seasons, which is a link.
    """

    def test_two_seasons(self):
        shutil.rmtree(self.cwd / "data")
        write_cast(self.cwd / "data" / "2025", dates={"1": "2025-08-19",
                                                      "2": "2025-08-26",
                                                      "3": "2025-09-02"}, match="2025")
        write_cast(self.cwd / "data" / "2026", match="2026")
        (self.cwd / "config.toml").write_text(
            '[build]\nseasons = "multi"\nplayers = "numbered"\n', encoding="utf-8")
        self.build()
        found = documents(self.cwd / "site")
        self.assertEqual(len(found), 2)
        for doc in found:
            with self.subTest(season=doc["meta"]["competition"]):
                self.assertEqual(sorted(doc["player_names"].values()),
                                 sorted(f"{name.title()} #{n}"
                                        for name, _ in TEAMS.values()
                                        for n in (1, 2, 3)))


class TestTheLaddersStateItsLeaderAndTheNightsPlayedAreThePrograms(unittest.TestCase):
    """``meta.ladder_state``, ``meta.leader`` and ``meta.matches_played`` (4.428-4.440).

    The state and the count are the build's own answers, kept as a withheld build
    keeps ``meta.ladder_complete`` and ``meta.matches_total``; the leader is a team's
    slug, a label as ``teams[].slug`` is, and its figure is the row's
    ``points_average``. Rejected: free text, which is what an unclassed field is
    read as, and none of them is text to check for a name.
    """

    def test_each_is_classed_as_the_field_it_comes_from(self):
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        for field, cls in (("meta.ladder_state", anonymise.PROGRAM),
                           ("meta.matches_played", anonymise.PROGRAM),
                           ("meta.leader.slug", anonymise.LABEL),
                           ("meta.leader.value", anonymise.PROGRAM)):
            with self.subTest(field=field):
                self.assertIn(field, leaves)
                self.assertEqual(anonymise.classify(field), cls)

    def test_a_season_that_names_no_leader_is_classed_too(self):
        # Null where the ladder is not settled, so the field is a leaf of its own.
        self.assertEqual(anonymise.classify("meta.leader"), anonymise.PROGRAM)



class TestTheLaddersSentenceIsTheProgramsToo(unittest.TestCase):
    """``meta.ladder_detail``, the words F10's state is said in (4.458).

    Built from counts and nothing a club typed, as ``meta.rules.unplayed_detail``
    is, so it is classed with it and kept. Rejected: leaving it unclassed, which
    reads it as free text to check for names it cannot hold.
    """

    def test_it_is_classed_as_the_program_s_own(self):
        leaves = set().union(*(_leaf_patterns(doc) for doc in
                               documents(demo() / "named") + documents(edge() / "named")))
        self.assertIn("meta.ladder_detail", leaves)
        self.assertEqual(anonymise.classify("meta.ladder_detail"), anonymise.PROGRAM)


if __name__ == "__main__":
    unittest.main()
