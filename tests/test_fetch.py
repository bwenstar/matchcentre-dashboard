"""``tools/fetch.py`` -- the one program here that asks something else for cards.

Before it, the only way into ``data/`` was a person saving each export by hand, and
nothing checked the three things a copy can get wrong before the build reads it.
Measured on this tree before the fetcher existed, with the demo season built into a
folder a club might keep:

  another club's card saved in beside the season   exit 0, and it became a second
                                                   competition of its own with no
                                                   warning at all
  a sign-in page saved as the card                 exit 1, and the page was still
                                                   in data/ afterwards
  a corrected card copied over the old one         exit 0 twice; the old bytes
                                                   survived nowhere
  a round's third card never exported              exit 0, and one note

The fetcher checks those before a card lands: only cards printed under the
letterhead the club names are written, nothing already there is overwritten, and
every round is counted against the draw the index states. The builder keeps its
own later checks (the letterhead majority warning, and ``card-missing`` once the
draw is declared); these are the ones that belong before the folder.

And it can reach one computer, this one. Every command-line test runs the fetcher
under an audit hook that kills the process, exit 99, the moment anything looks up
a name, connects or sends a datagram to an address that is not a loopback number
-- and two tests prove the hook fires. Every server here binds ``127.0.0.1`` on a
port the system chose, and is shut down by the test that started it.

Rejected: testing against a stub source that returns bytes. It would prove the
checks and not the transport, and the transport is where the promise about other
computers is kept or broken.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import hashlib
import http.server
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from matchcentre import card as card_mod                             # noqa: E402
from matchcentre import format as fmt_mod, scoring, validate         # noqa: E402
from matchcentre import pasteable, scorecard, version_line           # noqa: E402
from matchcentre.card import Card, Rejection                         # noqa: E402
from matchcentre.cardwriter import CardSpec, SetRow, write_card      # noqa: E402
from matchcentre.competition import Competition                      # noqa: E402

import build                                                         # noqa: E402
import demo                                                          # noqa: E402
import fetch                                                         # noqa: E402
import matchcentre                                                   # noqa: E402
from test_demo import copy_of_the_program, with_this_python         # noqa: E402

FETCH = ROOT / "tools" / "fetch.py"
BUILD = ROOT / "build.py"
DEMO = ROOT / "tools" / "demo.py"
README = ROOT / "README.md"
SECTION = "## Collecting cards from an index"
TIMEOUT = 300
OURS = demo.LETTERHEAD
THEIRS = "EXAMPLE DISTRICT TENNIS CLUB INC"
PINNED = "2026-09-10T00:00:00"

# The page a site answers with when a session has run out, saved under a card's
# name. Invented, and short, and it has to be found nowhere it was not served from.
SIGN_IN = (b"<!DOCTYPE html>\n<html><head><title>Sign in</title></head>\n"
           b"<body><form method=\"post\"><input name=\"user\"></form>"
           b"<!-- sign-in 5c1e --></body></html>\n")
NOT_A_CARD = b"%% not a card 7f3a: round one results to follow %%\n"

# Run instead of the fetcher itself, with the fetcher's path and arguments after
# it. An audit hook sees every lookup and every connect however the socket module
# was reached; monkeypatching sees only the names it replaced.
TRIPWIRE = r'''
import ipaddress, os, runpy, sys
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))

def _loopback(host):
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False

def _hook(event, args):
    if event in ("socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyaddr"):
        host = args[0]
    elif event in ("socket.connect", "socket.sendto"):
        address = args[1]
        host = address[0] if isinstance(address, tuple) else address
    else:
        return
    if not _loopback(host):
        sys.stderr.write(f"TRIPWIRE {event} {host}\n")
        sys.stderr.flush()
        os._exit(99)

sys.addaudithook(_hook)
runpy.run_path(sys.argv[0], run_name="__main__")
'''

SEASON: Path = Path()
# (match id, round, path under the season), in the order an index lists them.
ENTRIES: list[tuple[str, str, str]] = []


def setUpModule():
    """One demo season for the whole module, and the fixtures checked against it.

    Checked here rather than in the tests, so that a change to the reader or the card
    writer fails as a fixture that no longer means what it says, not as twenty
    confusing failures further down.
    """
    global SEASON
    holder = tempfile.TemporaryDirectory(prefix="test-fetch-season-")
    unittest.addModuleCleanup(holder.cleanup)
    unittest.addModuleCleanup(ENTRIES.clear)
    SEASON = Path(holder.name).resolve() / "season"
    done = subprocess.run((sys.executable, str(DEMO), "--out", str(SEASON)),
                          capture_output=True, text=True, timeout=TIMEOUT,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if done.returncode != 0:
        raise RuntimeError(f"tools/demo.py failed:\n{done.stdout}{done.stderr}")
    found = []
    for path in sorted(SEASON.rglob("*.pdf")):
        cards, rejected = scorecard.read(path)
        if rejected or len(cards) != 1:
            raise RuntimeError(f"{path} does not read as one demo card: {rejected}")
        found.append((cards[0].match_id, cards[0].round.strip(),
                      path.relative_to(SEASON).as_posix()))
    ENTRIES[:] = sorted(found, key=lambda e: (int(e[1]), e[0]))
    if len(ENTRIES) != 15 or len({e[1] for e in ENTRIES}) != 5:
        raise RuntimeError(f"the demo season is not 15 cards in 5 rounds: {ENTRIES}")

    scratch = Path(holder.name) / "checks"
    scratch.mkdir()
    # A copy with a comment added at the end has new bytes and the same reading --
    # the shape of a card downloaded twice, which carries the moment it was made.
    for card_id, _, rel in ENTRIES:
        body = (SEASON / rel).read_bytes()
        copy = scratch / f"{card_id}.pdf"
        copy.write_bytes(re_exported(body))
        if copy.read_bytes() == body or (transcription(SEASON / rel)
                                         != transcription(copy)):
            raise RuntimeError(f"a re-exported {rel} is not new bytes that read the "
                               f"same, so the same-card tests mean nothing")
    readings = {}
    for name, body in (("a", card_a()), ("b", card_b()), ("c", card_c()),
                       ("foreign", foreign()), ("blank", blank())):
        (scratch / f"{name}.pdf").write_bytes(body)
        readings[name], rejected = scorecard.read(scratch / f"{name}.pdf")
        if rejected or len(readings[name]) != 1:
            raise RuntimeError(f"fixture {name} does not read as one card: {rejected}")
    a, b, c = readings["a"][0], readings["b"][0], readings["c"][0]
    if not (a.match_id == b.match_id == c.match_id
            and (a.home_name, a.away_name, a.date) == (b.home_name, b.away_name, b.date)
            and len({transcription(scratch / f"{n}.pdf")[0] for n in "abc"}) == 3):
        raise RuntimeError("A, B and C are not one fixture read three ways")
    if card_mod.letterhead_key(a.club_hint) != OURS:
        raise RuntimeError(f"A does not print the demo letterhead: {a.club_hint!r}")
    if card_mod.letterhead_key(readings["foreign"][0].club_hint) != THEIRS:
        raise RuntimeError(f"foreign() prints {readings['foreign'][0].club_hint!r}")
    if readings["blank"][0].club_hint != "":
        raise RuntimeError(f"blank() prints {readings['blank'][0].club_hint!r}")


# --- the cards ----------------------------------------------------------------


def transcription(path: Path) -> list[Card]:
    """What a card reads as, without where it was read from.

    The fixtures' own copy of the comparison, so that a change to the fetcher's
    shows up in the tests of it and not here.
    """
    return [dataclasses.replace(c, source="") for c in scorecard.read(path)[0]]


def re_exported(body: bytes) -> bytes:
    return body + b"\n% re-exported\n"


def spec(**changes) -> CardSpec:
    """One fixture of the invented club, printed under the demo's own letterhead.

    Only the card writer's placeholder names, and one match id outside the demo's
    range, so that A, B and C are one match read three ways.
    """
    return dataclasses.replace(CardSpec(club_lines=demo.CLUB_LINES,
                                        match_id="4200090", division="3"),
                               **changes)


def _rows(**swap) -> tuple[SetRow, ...]:
    rows = list(CardSpec().rows)
    for index, (home, away) in swap.items():
        position = int(index[1:])
        rows[position] = dataclasses.replace(rows[position], home_games=home,
                                             away_games=away)
    return tuple(rows)


def card_a() -> bytes:
    return write_card(spec())


def card_b() -> bytes:
    # The third set's games swapped: a different scoreline for the same fixture,
    # which is what the builder treats as two cards disagreeing about a result.
    return write_card(spec(rows=_rows(s2=(0, 6))))


def card_c() -> bytes:
    return write_card(spec(rows=_rows(s1=(3, 6))))


def foreign(match_id: str = "4200091") -> bytes:
    """The card writer's own default letterhead, which is another invented club."""
    return write_card(CardSpec(match_id=match_id, division="3"))


def blank(match_id: str = "4200092") -> bytes:
    return write_card(spec(club_lines=(), match_id=match_id))


def printing(round_label: str, match_id: str = "4200093") -> bytes:
    return write_card(spec(round_label=round_label, match_id=match_id))


# --- the index ----------------------------------------------------------------


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def index_text(rounds) -> str:
    """``rounds`` is ``(round, fixtures, {id: location})`` per ``[[round]]``."""
    lines = ["format = 1", ""]
    for number, fixtures, cards in rounds:
        lines += ["[[round]]", f"round = {toml_value(number)}",
                  f"fixtures = {fixtures}", ""]
        if cards:
            lines.append("[round.cards]")
            lines += [f"{key} = {toml_value(where)}" for key, where in cards.items()]
            lines.append("")
    return "\n".join(lines)


def write_index(directory: Path, rounds, name: str = "index.toml") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index_text(rounds), encoding="utf-8")
    return path


def season_rounds(entries=None) -> list:
    rounds: dict[str, dict[str, str]] = {}
    for card_id, number, rel in entries or ENTRIES:
        rounds.setdefault(number, {})[card_id] = rel
    return [(int(number), 3, cards) for number, cards in rounds.items()]


def ids_in(round_label: str) -> list[str]:
    return [card_id for card_id, number, _ in ENTRIES if number == round_label]


def rel_of(card_id: str) -> str:
    return next(rel for known, _, rel in ENTRIES if known == card_id)


# --- the server ---------------------------------------------------------------


class Handler(http.server.SimpleHTTPRequestHandler):
    """The folder as files, except where a route says the answer is something else.

    Every request path is logged on the server, which is how a test says what was
    and was not asked for.
    """

    def log_message(self, format, *args):     # noqa: A002 - the base's own name
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        route = self.server.routes.get(urllib.parse.urlsplit(self.path).path)
        if route is None:
            super().do_GET()
            return
        kind, value = route
        if kind == "hang up":
            self.close_connection = True
            return
        if kind == "garbage":
            # Bytes where the status line belongs, and nothing after them.
            self.wfile.write(value)
            self.close_connection = True
            return
        if kind == "cut short":
            # The length of the whole file promised, and the first part of it sent
            # before the connection closes, as a connection lost mid-card leaves it.
            body, promised = value
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(promised))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        body = b""
        if kind == "redirect":
            self.send_response(302)
            if value is not None:
                self.send_header("Location", value)
        elif kind == "status":
            # The server's own words after the code, which the report never repeats:
            # a phrase is the server's to fill, and the standard's is the one worth
            # printing.
            self.send_response(value, "Gone fishing 9d2b")
        elif kind == "html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            body = value
        else:
            raise AssertionError(f"no such route kind: {kind}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def loopback(directory: Path, routes: dict | None = None):
    """A web server on 127.0.0.1, on a port the system chose, for one test."""
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
    server.daemon_threads = True
    server.requests = []
    server.routes = dict(routes or {})
    server.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)


def closed_port() -> int:
    """A loopback port nothing is listening on, as of a moment ago."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def quiet_env(private: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.lower().endswith("_proxy")}
    env.update(TMPDIR=str(private), PYTHONDONTWRITEBYTECODE="1")
    return env


def flat(text: str) -> str:
    """The report with its wrapping undone, which is how a sentence is compared."""
    return " ".join(text.split())


def snapshot(root: Path, *, skip: tuple[Path, ...] = ()) -> dict:
    """Every file and folder under ``root``: kind, size, mtime_ns and contents."""
    if not root.exists():
        return {"": "absent"}
    out = {}
    for path in sorted([root, *root.rglob("*")]):
        if any(path == s or s in path.parents for s in skip):
            continue
        stat = path.stat()
        out[path.relative_to(root).as_posix()] = (
            path.is_dir(), stat.st_size if path.is_file() else 0, stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "")
    return out


class FetchCase(unittest.TestCase):
    """A served copy of the demo season, a folder to fetch into, and the fetcher."""

    INTO = "data/2026-winter"

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="test-fetch-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        self.served = self.tmp / "served"
        shutil.copytree(SEASON, self.served)
        (self.tmp / "data").mkdir()
        self.into = self.tmp / "data" / "2026-winter"
        self.private = self.tmp / "private"
        self.private.mkdir()
        self.env = quiet_env(self.private)

    def serve(self, routes: dict | None = None):
        return self.enterContext(loopback(self.served, routes))

    def fetch(self, *argv: str, env: dict | None = None,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
        done = subprocess.run((sys.executable, "-c", TRIPWIRE, str(FETCH), *argv),
                              cwd=cwd or self.tmp, env=env or self.env,
                              capture_output=True, text=True, timeout=TIMEOUT)
        self.assertNotEqual(done.returncode, 99, f"the fetcher asked a computer "
                                                 f"other than this one:\n"
                                                 f"{done.stderr}")
        return done

    def args(self, source: str, *more: str, letterhead: str = OURS) -> tuple:
        return ("--from", source, "--into", self.INTO, "--letterhead", letterhead,
                *more)

    def season_index(self, server) -> str:
        write_index(self.served, season_rounds())
        return f"{server.base}/index.toml"

    def written(self) -> set[str]:
        if not self.into.exists():
            return set()
        return {p.relative_to(self.into).as_posix() for p in self.into.rglob("*")}


def heading(title: str) -> str:
    return f"== {title} " + "=" * (78 - len(title) - 4)


def result_block(total: int, into: str, *, wrote: str) -> list[str]:
    return [heading("result"), f"  wrote        {wrote}",
            f"  exit 0: all {total} card(s) are here, and every round matches its "
            f"draw"]


# --- 1 ------------------------------------------------------------------------


class TestAFirstFetchCopiesEveryCardTheIndexLists(FetchCase):
    """Fifteen cards listed, fifteen files: each one ``<id>.pdf``, each byte-exact.

    Measured before: no program could put a card in ``data/``, so the only way a
    season arrived was a person saving fifteen exports by hand. Rejected: keeping the
    server's file names, which are the server's to choose and can collide or climb
    out of the folder; the index's id is the name, and ids cannot hold a slash.
    """

    def test_every_card_lands_as_its_id_and_nothing_else_lands(self):
        server = self.serve()
        self.assertFalse(self.into.exists())
        done = self.fetch(*self.args(self.season_index(server)))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.written(), {f"{i}.pdf" for i, _, _ in ENTRIES},
                         "something other than the cards landed: no partial, no "
                         "manifest, no subfolder")
        for card_id, _, rel in ENTRIES:
            self.assertEqual((self.into / f"{card_id}.pdf").read_bytes(),
                             (self.served / rel).read_bytes(), card_id)

    def test_the_report_is_the_one_the_readme_describes(self):
        server = self.serve()
        source = self.season_index(server)
        done = self.fetch(*self.args(source))
        rounds = [f"  round {n}      3 of 3" for n in range(1, 6)]
        expected = [
            version_line(), "",
            heading("fetching"),
            f"  from         {source}",
            "  into         data/2026-winter",
            f"  keeping      cards whose letterhead reads {OURS}",
            "  asked for    15 card(s) in 5 round(s)", "",
            heading("rounds"), *rounds, "",
            heading("arrived"),
            "  new          15 card(s)", "",
            heading("result"),
            "  wrote        15 file(s) into data/2026-winter",
            f"  next         cd {pasteable(str(ROOT))} && python3 build.py --data "
            f"{pasteable(str(self.into))}",
            "  exit 0: all 15 card(s) are here, and every round matches its draw"]
        self.assertEqual(done.stdout.splitlines(), expected)
        self.assertEqual(done.stderr, "")


# --- 2 ------------------------------------------------------------------------


def readme_blocks() -> list[list[str]]:
    lines = README.read_text(encoding="utf-8").splitlines()
    start = lines.index(SECTION)
    blocks, current = [], None
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append(current)
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def readme_block(first: str) -> list[str]:
    for block in readme_blocks():
        if block and block[0].startswith(first):
            return block
    raise AssertionError(f"no block in {SECTION!r} starts with {first!r}")


class TestTheReadmeQuotesARealRun(FetchCase):
    """The two runs the README shows are two runs this suite makes.

    A quoted report that drifts from the program is worse than none: it is the one a
    club compares its own run against. Compared from the heading to the exit line
    with only the port and the folder put back as the README writes them; the
    ``next`` line separately, because it depends on where the command was typed,
    and the README's is the one typed in the project folder. Rejected: quoting it
    once and leaving it, which is how the build's own quoted report went stale
    before its test existed.
    """

    def run_twice(self):
        server = self.serve()
        source = self.season_index(server)
        first = self.fetch(*self.args(source))
        second = self.fetch(*self.args(source))
        self.assertEqual((first.returncode, second.returncode), (0, 0),
                         first.stdout + first.stderr + second.stdout)
        def swap(line: str) -> str:
            return line.replace(server.base, "http://127.0.0.1:8000")
        return ([swap(line) for line in first.stdout.splitlines()],
                [swap(line) for line in second.stdout.splitlines()])

    @staticmethod
    def between(lines: list[str], first: str) -> list[str]:
        start = next(i for i, line in enumerate(lines) if line.startswith(first))
        end = next(i for i, line in enumerate(lines) if line.startswith("  exit "))
        return lines[start:end + 1]

    @staticmethod
    def without_next(lines: list[str]) -> list[str]:
        return [line for line in lines if not line.startswith("  next ")]

    def test_the_first_run_is_quoted_as_it_prints(self):
        run, _ = self.run_twice()
        quoted = readme_block("== fetching")
        self.assertEqual(self.without_next(quoted),
                         self.without_next(self.between(run, "== fetching")))

    def test_the_second_run_is_quoted_as_it_prints(self):
        _, run = self.run_twice()
        self.assertEqual(readme_block("== arrived"), self.between(run, "== arrived"))

    def test_the_next_command_quoted_is_the_one_the_project_folder_gets(self):
        run, _ = self.run_twice()
        asked = subprocess.run(
            (sys.executable, "-c", "import demo; print(demo.next_command("
                                   "demo.ROOT / 'data' / '2026-winter'))"),
            cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT,
            env={**self.env, "PYTHONPATH": str(ROOT / "tools")})
        self.assertEqual(asked.returncode, 0, asked.stderr)
        quoted = [line for line in readme_block("== fetching")
                  if line.startswith("  next ")]
        self.assertEqual(quoted, [f"  next         {asked.stdout.strip()}"])
        # And the run made from elsewhere offers the command that works from there.
        self.assertIn(f"  next         cd {pasteable(str(ROOT))} && python3 build.py "
                      f"--data {pasteable(str(self.into))}", run)

    def test_the_index_it_shows_is_the_demo_season(self):
        # The README's example lists real paths of the demo season under their real
        # ids, so a club can serve `tools/demo.py`'s output and fetch it with it.
        block = next(b for b in readme_blocks() if b and b[0] == "format = 1")
        index = fetch.parse_index("\n".join(block),
                                  (SEASON / "index.toml").as_uri(), "README.md")
        self.assertTrue(index.rounds)
        for listed in (entry for r in index.rounds for entry in r.entries):
            path = Path(urllib.parse.unquote(urllib.parse.urlsplit(listed.url).path))
            cards, _ = scorecard.read(path)
            self.assertEqual([(c.match_id, c.round.strip()) for c in cards],
                             [(listed.card_id, listed.round)], listed.url)


@unittest.skipIf(os.name == "nt", "the pasted line is run by a POSIX shell")
class TestTheCommandItPrintsNextWorksPastedWhereTheClubStood(FetchCase):
    """The fetcher's ``next`` line, run by the shell from where the fetch was run.

    The fetcher has used the demo's function for this line since it was written, and
    resolved its folder itself on the way in. So a relative ``--into`` was already
    the folder the build reads, and only the quoting was missing. Measured on a copy
    of the tree before, two of five lines failed when pasted
    (/tmp/t4/scratch/d448/measure.before.txt). `--into "our season"`, from outside
    the project, printed `--data /.../our season`, and pasted that is `unrecognized
    arguments: season`. A project under a folder with a space printed a ``cd`` that
    stops at the space, `can't cd to .../my`.

    The folder is now resolved for the line inside the demo's function, and not
    by the fetcher first, so the two programs cannot mean different folders by one
    relative name. A copy of the program is used here, under a folder with a space
    in it, as in the demo's own test, because the build the line runs writes into
    the project it goes to, and the line is pasted with this Python as ``python3``.
    Rejected: a line the fetcher builds for itself, which is where the demo's
    unquoted `--data` would have been fixed once and left in the other.
    """

    def setUp(self):
        super().setUp()
        self.project = copy_of_the_program(self.tmp / "our club" /
                                           "matchcentre-dashboard")

    def printed(self, into: str) -> str:
        """The copy's fetcher, run from outside the copy, and its one next line."""
        server = self.serve()
        done = subprocess.run(
            (sys.executable, "-c", TRIPWIRE, str(self.project / "tools" / "fetch.py"),
             "--from", self.season_index(server), "--into", into,
             "--letterhead", OURS),
            cwd=self.tmp, env=self.env, capture_output=True, text=True,
            timeout=TIMEOUT)
        self.assertNotEqual(done.returncode, 99, done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line[len("  next         "):] for line in done.stdout.splitlines()
                 if line.startswith("  next ")]
        self.assertEqual(len(lines), 1, done.stdout)
        return lines[0]

    def pasted(self, command: str) -> None:
        done = subprocess.run(("sh", "-c", command), cwd=self.tmp,
                              env=with_this_python(self.tmp / "bin", self.env),
                              capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(done.returncode, 0,
                         f"{command}\n{done.stdout[-1500:]}{done.stderr}")
        self.assertIn("exit 0: all 15 card(s) scored, no errors", done.stdout)

    def test_a_relative_folder_with_a_space_is_the_folder_the_build_reads(self):
        command = self.printed("our season")
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py "
                                  f"--data '{self.tmp / 'our season'}'")
        self.pasted(command)

    def test_the_projects_own_data_folder_is_a_cd_that_arrives(self):
        command = self.printed(str(self.project / "data" / "2026-winter"))
        self.assertEqual(command, f"cd '{self.project}' && python3 build.py")
        self.pasted(command)


# --- 3 ------------------------------------------------------------------------


# Refused, each for its own reason, and all listed under round 1 with our three.
REFUSED = {"4200100": ("foreign.pdf", foreign(match_id="4200100")),
           "4200101": ("blank.pdf", blank(match_id="4200101")),
           "4200102": ("signin.pdf", SIGN_IN),
           "4200103": ("notes.pdf", NOT_A_CARD)}


def corpus(served: Path, *, sign_in_as_file: bool = False) -> tuple[list, dict]:
    """Three of ours and the four refusals, all listed under round 1."""
    cards = {card_id: rel_of(card_id) for card_id in ids_in("1")}
    routes = {}
    for card_id, (name, body) in REFUSED.items():
        cards[card_id] = name
        if name == "signin.pdf" and not sign_in_as_file:
            routes["/signin.pdf"] = ("html", body)
        else:
            (served / name).write_bytes(body)
    return [(1, 7, cards)], routes


class TestOnlyCardsPrintedUnderYourLetterheadAreKept(FetchCase):
    """Another club's card, a card with no letterhead, a sign-in page, a text file.

    None of them is written, and none of their bytes is found anywhere outside the
    folder they were served from. Measured before: another club's card saved into
    the season built at exit 0 as a second competition, and a sign-in page saved as
    a card was still in ``data/`` after the build refused it. Rejected: downloading
    into the folder and deleting what fails, which leaves a moment in which a build
    reads it and a backup copies it.
    """

    def test_only_our_three_cards_are_written(self):
        rounds, routes = corpus(self.served)
        server = self.serve(routes)
        write_index(self.served, rounds)
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.written(), {f"{i}.pdf" for i in ids_in("1")})
        bodies = [body for _, body in REFUSED.values()]
        for path in self.tmp.rglob("*"):
            if path.is_file() and self.served not in path.parents:
                data = path.read_bytes()
                for body in bodies:
                    self.assertNotIn(body, data, f"a refused body is in {path}")

    def test_each_refusal_says_why_in_words(self):
        rounds, routes = corpus(self.served)
        server = self.serve(routes)
        write_index(self.served, rounds)
        said = flat(self.fetch(*self.args(f"{server.base}/index.toml")).stdout)
        self.assertIn(f"4200100 printed by another club: its letterhead reads "
                      f"{THEIRS}", said)
        self.assertIn("4200101 prints no letterhead this could read, so which club "
                      "it belongs to is unknown", said)
        self.assertIn("; it is a web page, which usually means a sign-in page came "
                      "back instead of the card", said)
        self.assertEqual(said.count("it is a web page"), 1)

    def test_the_round_it_leaves_short_names_the_refusals_as_the_cause(self):
        rounds, routes = corpus(self.served)
        server = self.serve(routes)
        write_index(self.served, rounds)
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertIn("  round 1      3 of 7; 4 short: 4 card(s) listed for it were "
                      "not kept", done.stdout.splitlines())
        self.assertIn("  exit 1: 1 round(s) short of the draw and 4 card(s) not kept",
                      done.stdout.splitlines())


# --- 4 ------------------------------------------------------------------------


class TestTheLetterheadIsComparedOneWay(FetchCase):
    """Case and spacing forgiven, spelling not -- and by one function, everywhere.

    The demo's own-card test, the fetcher's keep test and the build's letterhead
    check answer the same question about the same page, so they share the one
    definition. Rejected: a second normalisation in the fetcher, which is how two
    parts of one project come to disagree about whose card it is. The build's check
    had one, which forgave spacing and not case, and reported as possibly another
    club's a card the other two called the club's own.
    """

    def test_spacing_and_case_are_all_it_forgives(self):
        key = card_mod.letterhead_key
        self.assertEqual(key("  brindle   creek\ttennis club inc "), OURS)
        self.assertNotEqual(key("BRINDLE CRE EK TENNIS CLUB INC"), OURS)
        self.assertEqual(demo.LETTERHEAD, key(" ".join(demo.CLUB_LINES)))

    def test_there_is_one_function_and_every_reader_uses_it(self):
        self.assertIs(fetch.letterhead_key, card_mod.letterhead_key)
        self.assertIs(demo.letterhead_key, card_mod.letterhead_key)
        with mock.patch.object(demo, "letterhead_key",
                               wraps=card_mod.letterhead_key) as spy:
            self.assertTrue(demo.is_demo_card(SEASON / ENTRIES[0][2]))
        self.assertTrue(spy.called, "demo.is_demo_card compares letterheads its "
                                    "own way")
        self.assertIs(validate.letterhead_key, card_mod.letterhead_key)
        (card,), _ = scorecard.read(SEASON / ENTRIES[0][2])
        competition = Competition(
            id="wednesday-division-3", day="Wednesday", division="3",
            fmt=fmt_mod.match_format("doubles-singles-doubles"),
            rules=scoring.ruleset("games-bonus"))
        with mock.patch.object(validate, "letterhead_key",
                               wraps=card_mod.letterhead_key) as spy:
            validate.validate([], competition, cards=[card])
        self.assertIn(mock.call(card.club_hint), spy.call_args_list,
                      "the build's letterhead check compares letterheads its own "
                      "way")

    def test_a_letterhead_typed_loosely_keeps_every_card(self):
        server = self.serve()
        done = self.fetch(*self.args(self.season_index(server),
                                     letterhead="brindle creek   tennis club inc"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.written()), 15)

    def test_two_letterheads_keep_both_clubs_cards(self):
        server = self.serve()
        (self.served / "theirs.pdf").write_bytes(foreign(match_id="4200100"))
        cards = {card_id: rel_of(card_id) for card_id in ids_in("1")}
        write_index(self.served, [(1, 4, {**cards, "4200100": "theirs.pdf"})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"),
                          "--letterhead", "Example District Tennis Club Inc")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.written()), 4)
        self.assertIn(f"cards whose letterhead reads {OURS}, or {THEIRS}",
                      flat(done.stdout))


class TestTheDemoTheFetcherAndTheBuildAgreeAboutACardPrintedInTitleCase(
        unittest.TestCase):
    """A demo card printing ``Brindle Creek Tennis Club Inc``: all three say ours.

    Measured before, on the demo with ``round-01/magpies-v-wombats.pdf``'s
    letterhead in title case (``/tmp/t4/scratch/d447/measure.py``): the demo's
    ``is_demo_card`` said it was its own, the fetcher under the demo's letterhead
    kept it, and the build, at exit 0, gave a seventh warning, ``foreign-card``.
    That warning said the card "reads 'Brindle Creek Tennis Club Inc', where the
    other 14 card(s) scored as wednesday-division-3 read 'BRINDLE CREEK TENNIS CLUB
    INC'", and that it might belong to another club's competition. It reached
    ``season.json`` and the two scripts written from it too
    (``/tmp/t4/scratch/d447/probe_site.py``).

    Both seasons are written by the demo in this process, one with
    ``write_card_file`` patched so that one card goes to disk under the letterhead
    in title case, and both are built by ``build.py``. The build of the one is
    compared whole with the build of the other: the report, the ``config.toml`` it
    started and the site.

    Rejected: changing the card after the demo wrote it, which is a second writer of
    the card; and a card of the card writer's own, which would test a card no club
    was given. The demo's is the one a club builds first.
    """

    CARD = "round-01/magpies-v-wombats.pdf"
    PRINTED = tuple(line.title() for line in demo.CLUB_LINES)

    @classmethod
    def setUpClass(cls):
        holder = tempfile.TemporaryDirectory(prefix="test-fetch-title-case-")
        cls.addClassCleanup(holder.cleanup)
        tmp = Path(holder.name).resolve()
        cls.plain, cls.plain_build = cls.season(tmp / "plain", changed=False)
        cls.varied, cls.varied_build = cls.season(tmp / "varied", changed=True)
        cls.card = cls.varied / "data" / "demo-season" / cls.CARD

    @classmethod
    def season(cls, project: Path, *, changed: bool):
        out = project / "data" / "demo-season"
        real = demo.write_card_file

        def write(path, card_spec):
            if changed and Path(path).as_posix().endswith("/" + cls.CARD):
                card_spec = dataclasses.replace(card_spec, club_lines=cls.PRINTED)
            return real(path, card_spec)

        with (mock.patch.object(demo, "write_card_file", write),
              contextlib.redirect_stdout(io.StringIO())):
            demo.main(["--out", str(out)])
        done = subprocess.run(
            (sys.executable, str(BUILD), "--data", "data", "--generated", PINNED),
            cwd=project, capture_output=True, text=True, timeout=TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return project, done

    @staticmethod
    def files(root: Path) -> dict[str, bytes]:
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()}

    def test_the_card_is_the_demos_own_with_its_letterhead_in_title_case(self):
        # The premise: of the fifteen, one card has other bytes, and it reads as the
        # demo's does in everything but the letterhead.
        plain = self.files(self.plain / "data")
        varied = self.files(self.varied / "data")
        self.assertEqual(sorted(plain), sorted(varied))
        self.assertEqual([rel for rel in plain if plain[rel] != varied[rel]],
                         [f"demo-season/{self.CARD}"])
        (ours,), _ = scorecard.read(self.plain / "data" / "demo-season" / self.CARD)
        (card,), _ = scorecard.read(self.card)
        self.assertEqual(card.club_hint, "Brindle Creek Tennis Club Inc")
        self.assertEqual(card_mod.letterhead_key(card.club_hint), OURS)
        self.assertEqual(dataclasses.replace(card, source="", club_hint=OURS),
                         dataclasses.replace(ours, source=""))

    def test_the_demo_takes_it_for_one_of_its_own(self):
        self.assertTrue(demo.is_demo_card(self.card))

    def test_the_fetcher_keeps_it_under_the_demos_letterhead(self):
        verdict = fetch.read_verdict(self.card, {OURS})
        self.assertTrue(verdict.kept, verdict.reason)

    def test_the_build_says_and_writes_what_it_does_for_the_demo_itself(self):
        plain, varied = self.plain_build, self.varied_build
        self.assertEqual(varied.returncode, 0, varied.stdout + varied.stderr)
        self.assertEqual([line for line in varied.stdout.splitlines()
                          if "foreign-card" in line or "letterhead reads" in line], [])
        self.assertEqual(varied.stdout + varied.stderr, plain.stdout + plain.stderr)
        self.assertEqual(self.files(self.varied / "site"),
                         self.files(self.plain / "site"))
        self.assertEqual((self.varied / "config.toml").read_bytes(),
                         (self.plain / "config.toml").read_bytes())


# --- 5 ------------------------------------------------------------------------


class TestAPageWithoutALetterheadIsNotPresumedOurs(unittest.TestCase):
    """Absence of a letterhead is not our letterhead; every page is checked.

    Rejected: keeping a card that prints no club because nothing says it is somebody
    else's. That is absence of evidence recorded as evidence, and it is the shape of
    a card whose top left the reader could not make out.
    """

    ours = Card(match_id="1", round="1", club_hint="Brindle Creek Tennis Club Inc")
    theirs = Card(match_id="2", round="1", club_hint="Example District Tennis Club Inc")

    def test_a_card_with_no_letterhead_is_not_kept(self):
        verdict = fetch.judge([Card(club_hint="")], [], {OURS})
        self.assertFalse(verdict.kept)
        self.assertEqual(verdict.reason, "prints no letterhead this could read, so "
                                         "which club it belongs to is unknown")

    def test_a_file_with_nothing_in_it_is_not_kept(self):
        verdict = fetch.judge([], [], {OURS})
        self.assertEqual((verdict.kept, verdict.reason),
                         (False, "no scorecard in it"))

    def test_a_refused_page_is_named_by_its_reason_and_never_its_private_path(self):
        private = "/tmp/matchcentre-fetch-x1/4200006.pdf"
        refused = Rejection(private, "the page is not a scorecard",
                            detail=f"{private}: no set rows")
        verdict = fetch.judge([self.ours], [refused], {OURS})
        self.assertFalse(verdict.kept)
        self.assertEqual(verdict.reason, "the page is not a scorecard")

    def test_every_page_is_checked_not_only_the_first(self):
        verdict = fetch.judge([self.ours, self.theirs], [], {OURS})
        self.assertFalse(verdict.kept)
        self.assertEqual(verdict.reason,
                         f"printed by another club: its letterhead reads {THEIRS}")

    def test_our_own_card_is_kept(self):
        self.assertTrue(fetch.judge([self.ours], [], {OURS}).kept)


# --- 6 ------------------------------------------------------------------------


class TestNothingUnverifiedIsEverPut(FetchCase):
    """The one writer is only ever handed a body that was read and kept.

    Asked in process, where the two functions can be watched: every body ``put`` is
    given is one ``judge`` keeps, and at the moment each body is read, the folder
    holds nothing but cards already kept. Rejected: trusting the CLI byte scan
    alone, which cannot see a file that was written and deleted between two looks.
    """

    def test_put_sees_only_kept_bodies_and_the_folder_never_holds_another(self):
        # The sign-in page as a file, since this runs from a file index.
        rounds, _ = corpus(self.served, sign_in_as_file=True)
        source = fetch.IndexSource(str(write_index(self.served, rounds)))
        seen, put_bodies = [], []
        real_verdict, real_put = fetch.read_verdict, fetch.put

        def watched_verdict(path, letterheads):
            seen.append((Path(path), sorted(p.name for p in self.into.iterdir())
                         if self.into.exists() else []))
            return real_verdict(path, letterheads)

        def watched_put(into, name, body):
            put_bodies.append(body)
            return real_put(into, name, body)

        with mock.patch.object(fetch, "read_verdict", side_effect=watched_verdict), \
                mock.patch.object(fetch, "put", side_effect=watched_put):
            fetch.run(source, self.into, {OURS})
        self.assertEqual(len(put_bodies), 3)
        ours = {(self.served / rel_of(i)).read_bytes() for i in ids_in("1")}
        self.assertEqual(set(put_bodies), ours)
        kept_names = {f"{i}.pdf" for i in ids_in("1")}
        for path, listing in seen:
            self.assertNotIn(self.into, path.parents,
                             f"{path} was read for its verdict inside --into")
            self.assertLessEqual(set(listing), kept_names)
        self.assertEqual([p.name for p in self.into.iterdir()
                          if p.name.endswith(".part")], [])


# --- 7 ------------------------------------------------------------------------


class TestAFetchThatFindsNothingNewTouchesNothing(FetchCase):
    """The second run of an unchanged season writes nothing -- not a timestamp.

    The folder is the only state there is, so a run that changed nothing must leave
    it exactly as it was, or every change detector downstream fires every week.
    Rejected: rewriting identical bytes, which is invisible to a diff and not to a
    timestamp, a sync client or the build's own "wrote nothing" sentence.
    """

    def test_nothing_under_the_folder_changes_at_all(self):
        server = self.serve()
        source = self.season_index(server)
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        before = snapshot(self.into)
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(snapshot(self.into), before)
        lines = done.stdout.splitlines()
        self.assertIn("  unchanged    15 card(s), byte for byte", lines)
        self.assertIn("  wrote        nothing: all 15 card(s) were already in "
                      "data/2026-winter", lines)
        self.assertNotIn("  next", done.stdout)

    def test_a_copy_named_in_capitals_is_the_copy_already_here(self):
        # The build reads `.PDF` as it reads `.pdf`, so a copy saved that way is one
        # the build already has; a second, lower-case copy beside it would be two
        # files claiming one match.
        one = ids_in("1")[0]
        write_index(self.served, [(1, 1, {one: rel_of(one)})])
        self.into.mkdir()
        shutil.copy(self.served / rel_of(one), self.into / f"{one}.PDF")
        before = snapshot(self.into)
        done = self.fetch(*self.args(f"{self.serve().base}/index.toml"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("  unchanged    1 card(s), byte for byte", done.stdout.splitlines())
        self.assertEqual(snapshot(self.into), before)

    def test_quiet_leaves_the_version_and_the_result(self):
        server = self.serve()
        source = self.season_index(server)
        self.fetch(*self.args(source))
        done = self.fetch(*self.args(source, "--quiet"))
        self.assertEqual(done.stdout.splitlines(), [
            version_line(), "",
            *result_block(15, self.INTO, wrote="nothing: all 15 card(s) were "
                                              "already in data/2026-winter")])


# --- 8 ------------------------------------------------------------------------


class TestANothingNewFetchLeavesTheBuildNothingToWrite(FetchCase):
    """Fetch, build, fetch, build: the second build says it wrote nothing.

    The rule that an unchanged season rebuilds to identical files only holds if
    nothing upstream of the build moves. Every path the builder is given is under
    this test's own folder; the checkout's ``config.toml``, ``data/`` and ``site/``
    are never named. The first build is run twice before the second fetch: with no
    config it writes a starter ``config.toml``, and the build after that reads it and
    rewrites 2 of its 8 files (measured: the provenance of teams and captains moves
    from read-off-the-cards to declared), which is the config settling and not the
    fetch. Rejected: asserting the build's report text beyond that one sentence,
    which other work is changing.
    """

    def build(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            (sys.executable, str(BUILD), "--data", str(self.into),
             "--out", str(self.tmp / "site"), "--config", str(self.tmp / "config.toml"),
             "--generated", PINNED),
            cwd=self.tmp, capture_output=True, text=True, timeout=TIMEOUT,
            env=self.env)

    def test_the_second_build_writes_nothing(self):
        server = self.serve()
        source = self.season_index(server)
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        first = self.build()
        self.assertIn(first.returncode, (0, 1), first.stdout + first.stderr)
        settled = self.build()
        self.assertIn(settled.returncode, (0, 1), settled.stdout + settled.stderr)
        key = {p: (s[1], s[2]) for p, s in snapshot(self.into).items()}
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        self.assertEqual({p: (s[1], s[2]) for p, s in snapshot(self.into).items()},
                         key)
        second = self.build()
        self.assertIn("wrote        nothing", second.stdout, second.stdout)


# --- 9 ------------------------------------------------------------------------


class TestACardThatOnlyHasNewBytesIsNotWrittenAgain(FetchCase):
    """New bytes that read exactly as the copy here are not a new card.

    A PDF carries the moment it was made, so two downloads of an unchanged card
    rarely match byte for byte. Rejected: comparing bytes alone, which would keep a
    duplicate of every card every week and bury the one real correction under
    fifteen "same match" warnings.
    """

    def test_a_re_exported_season_writes_nothing(self):
        server = self.serve()
        source = self.season_index(server)
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        before = snapshot(self.into)
        for _, _, rel in ENTRIES:
            path = self.served / rel
            path.write_bytes(re_exported(path.read_bytes()))
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(snapshot(self.into), before)
        said = flat(done.stdout)
        self.assertIn("same card 15 card(s) came back as new bytes that read exactly "
                      "as the copy already here, so none was written again.", said)
        self.assertIn("wrote nothing: all 15 card(s) were already in "
                      "data/2026-winter", said)


# --- 10 -----------------------------------------------------------------------


class TestACardThatReadsDifferentlyIsKeptBesideTheOld(FetchCase):
    """A corrected card is kept beside the old one, and the run says so, loudly.

    Measured before: a corrected card copied over the old one built at exit 0 both
    times and the old bytes survived nowhere. Rejected: overwriting, with or without
    a backup, which makes a changed result an event nobody is told about; and
    keeping only the newest, which lets a wrong correction win.
    """

    def serve_one(self, body: bytes):
        server = self.serve()
        (self.served / "a.pdf").write_bytes(body)
        write_index(self.served, [(1, 1, {"4200090": "a.pdf"})])
        return server, f"{server.base}/index.toml"

    def test_both_are_kept_and_the_old_one_is_untouched(self):
        server, source = self.serve_one(card_a())
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        (self.served / "a.pdf").write_bytes(card_b())
        done = self.fetch(*self.args(source, "--quiet"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.into / "4200090.pdf").read_bytes(), card_a())
        self.assertEqual((self.into / "4200090.rev2.pdf").read_bytes(), card_b())
        # Under --quiet, and under its heading.
        self.assertIn(f"{heading('arrived')} changed 4200090: reads differently from "
                      f"4200090.pdf, so it was kept beside it as 4200090.rev2.pdf. "
                      f"build.py will report the two as claiming the same match until "
                      f"you delete the one that is wrong.", flat(done.stdout))

    def test_the_build_then_names_both_until_one_is_deleted(self):
        server, source = self.serve_one(card_a())
        self.fetch(*self.args(source))
        (self.served / "a.pdf").write_bytes(card_b())
        self.fetch(*self.args(source))
        done = subprocess.run(
            (sys.executable, str(BUILD), "--check", "--data", str(self.into),
             "--config", str(self.tmp / "config.toml"), "--out", str(self.tmp / "site")),
            cwd=self.tmp, capture_output=True, text=True, timeout=TIMEOUT,
            env=self.env)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        said = done.stdout + done.stderr
        self.assertIn("4200090.pdf", said)
        self.assertIn("4200090.rev2.pdf", said)

    def test_a_copy_here_that_is_not_a_card_is_said_as_one(self):
        # Measured: a copy that reads as no card at all was said to "read
        # differently", and the build to report two cards claiming the match, when
        # the build refuses the one and scores the other.
        self.into.mkdir()
        (self.into / "4200090.pdf").write_bytes(NOT_A_CARD)
        server, source = self.serve_one(card_a())
        done = self.fetch(*self.args(source, "--quiet"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.into / "4200090.pdf").read_bytes(), NOT_A_CARD)
        self.assertEqual((self.into / "4200090.rev2.pdf").read_bytes(), card_a())
        said = flat(done.stdout)
        self.assertIn("changed 4200090: the copy already here, 4200090.pdf, does not "
                      "read as a card, so this one was kept beside it as "
                      "4200090.rev2.pdf. build.py will refuse 4200090.pdf until you "
                      "delete it.", said)
        self.assertNotIn("reads differently", said)
        built = subprocess.run(
            (sys.executable, str(BUILD), "--check", "--data", str(self.into),
             "--config", str(self.tmp / "config.toml"), "--out", str(self.tmp / "site")),
            cwd=self.tmp, capture_output=True, text=True, timeout=TIMEOUT,
            env=self.env)
        self.assertEqual(built.returncode, 1, built.stdout + built.stderr)
        lines = built.stdout.splitlines()
        self.assertIn("  scored       1", lines)
        self.assertIn("  refused      1", lines)

    def test_copies_that_do_not_read_are_said_apart_from_those_that_do(self):
        holder = tempfile.TemporaryDirectory(prefix="test-fetch-unread-")
        self.addCleanup(holder.cleanup)
        into = Path(holder.name)
        (into / "4200094.pdf").write_bytes(write_card(spec(match_id="4200094")))
        (into / "4200094.rev2.pdf").write_bytes(NOT_A_CARD)
        (into / "4200095.pdf").write_bytes(NOT_A_CARD)
        (into / "4200095.rev2.pdf").write_bytes(NOT_A_CARD + b"%% again %%\n")
        entries = tuple(fetch.Entry("1", i, f"memory:/{i}.pdf")
                        for i in ("4200094", "4200095"))
        source = _FakeSource([fetch.Round("1", 2, entries)], {
            "4200094": fetch.Fetched(write_card(spec(match_id="4200094",
                                                     rows=_rows(s2=(0, 6))))),
            "4200095": fetch.Fetched(write_card(spec(match_id="4200095")))})
        result = fetch.run(source, into, (OURS,))
        said = flat(fetch.report(result, argparse.Namespace(into="into", quiet=True))
                    .render(quiet=True))
        self.assertIn("changed 4200094: reads differently from 4200094.pdf, and "
                      "4200094.rev2.pdf does not read as a card, so it was kept "
                      "beside them as 4200094.rev3.pdf. build.py will report the two "
                      "as claiming the same match until you delete the one that is "
                      "wrong, and will refuse 4200094.rev2.pdf until you delete it.",
                      said)
        self.assertIn("changed 4200095: the copies already here, 4200095.pdf and "
                      "4200095.rev2.pdf, do not read as cards, so this one was kept "
                      "beside them as 4200095.rev3.pdf. build.py will refuse them "
                      "until you delete them.", said)

    def test_three_copies_either_way_are_listed_as_a_sentence(self):
        """Three copies that read and three that do not are each listed as a sentence.

        ``_changed`` joined both lists with `` and `` alone. Measured before, with
        ``Outcome``s of three of each (/tmp/t6/scratch/m23/probe-before.txt): ``reads
        differently from a.pdf and b.pdf and c.pdf, and x.pdf and y.pdf and z.pdf do
        not read as cards``, and ``the copies already here, x.pdf and y.pdf and
        z.pdf, do not read as cards``. Both list by ``scope.in_words`` now, which
        gives two as it did.

        Rejected: a count, ``three copies``, in place of the names. The sentence goes
        on to tell the club to delete the ones that do not read, and the names are
        what it deletes.
        """
        holder = tempfile.TemporaryDirectory(prefix="test-fetch-three-")
        self.addCleanup(holder.cleanup)
        into = Path(holder.name)
        for name, rows in (("4200096.pdf", _rows()), ("4200096.rev2.pdf",
                                                     _rows(s1=(0, 6))),
                           ("4200096.rev3.pdf", _rows(s2=(0, 6)))):
            (into / name).write_bytes(write_card(spec(match_id="4200096", rows=rows)))
        for card_id, first in (("4200096", 4), ("4200097", 1)):
            for n in range(first, first + 3):
                name = f"{card_id}.pdf" if n == 1 else f"{card_id}.rev{n}.pdf"
                (into / name).write_bytes(NOT_A_CARD + b"%% copy %d %%\n" % n)
        entries = tuple(fetch.Entry("1", i, f"memory:/{i}.pdf")
                        for i in ("4200096", "4200097"))
        source = _FakeSource([fetch.Round("1", 2, entries)], {
            "4200096": fetch.Fetched(write_card(spec(match_id="4200096",
                                                     rows=_rows(s3=(0, 6))))),
            "4200097": fetch.Fetched(write_card(spec(match_id="4200097")))})
        result = fetch.run(source, into, (OURS,))
        said = flat(fetch.report(result, argparse.Namespace(into="into", quiet=True))
                    .render(quiet=True))
        self.assertIn("changed 4200096: reads differently from 4200096.pdf, "
                      "4200096.rev2.pdf and 4200096.rev3.pdf, and 4200096.rev4.pdf, "
                      "4200096.rev5.pdf and 4200096.rev6.pdf do not read as cards, so "
                      "it was kept beside them as 4200096.rev7.pdf. build.py will "
                      "report the copies as claiming the same match until you delete "
                      "the one that is wrong, and will refuse them until you delete "
                      "them.", said)
        self.assertIn("changed 4200097: the copies already here, 4200097.pdf, "
                      "4200097.rev2.pdf and 4200097.rev3.pdf, do not read as cards, so "
                      "this one was kept beside them as 4200097.rev4.pdf. build.py "
                      "will refuse them until you delete them.", said)


# --- 11 -----------------------------------------------------------------------


class TestRevisionsAreNumberedAndNeverReused(FetchCase):
    """A, B, C, A: three files, and the fourth fetch writes nothing.

    Every copy is compared, not only the newest, so a correction that was undone
    finds the copy it went back to. A number below the highest is never handed out
    again, so ``rev2`` always means the second version that arrived, whatever was
    deleted since. Rejected: timestamped names, which do not sort as versions.
    """

    def test_the_sequence_writes_one_file_per_reading(self):
        server = self.serve()
        write_index(self.served, [(1, 1, {"4200090": "a.pdf"})])
        source = f"{server.base}/index.toml"
        names, said = [], []
        for body in (card_a(), card_b(), card_c(), card_a()):
            (self.served / "a.pdf").write_bytes(body)
            done = self.fetch(*self.args(source))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            names.append(sorted(self.written()))
            said.append(flat(done.stdout))
        self.assertEqual(names, [
            ["4200090.pdf"],
            ["4200090.pdf", "4200090.rev2.pdf"],
            ["4200090.pdf", "4200090.rev2.pdf", "4200090.rev3.pdf"],
            ["4200090.pdf", "4200090.rev2.pdf", "4200090.rev3.pdf"]])
        # The third reading is kept beside two copies, and the sentence counts them.
        self.assertIn("changed 4200090: reads differently from 4200090.pdf and "
                      "4200090.rev2.pdf, so it was kept beside them as "
                      "4200090.rev3.pdf. build.py will report the copies as claiming "
                      "the same match until you delete the one that is wrong.",
                      said[2])

        (self.into / "4200090.rev2.pdf").unlink()
        (self.served / "a.pdf").write_bytes(card_c())
        self.fetch(*self.args(source))
        self.assertEqual(sorted(self.written()), ["4200090.pdf", "4200090.rev3.pdf"])
        (self.served / "a.pdf").write_bytes(card_b())
        self.fetch(*self.args(source))
        self.assertEqual(sorted(self.written()),
                         ["4200090.pdf", "4200090.rev3.pdf", "4200090.rev4.pdf"])
        self.assertEqual((self.into / "4200090.rev4.pdf").read_bytes(), card_b())


# --- 12 -----------------------------------------------------------------------


class TestAnUnfinishedWriteIsWhatTheBuilderCallsAnUnfinishedDownload(FetchCase):
    """A card half written is refused by the build, and cleared by the next fetch.

    The builder already refuses a ``.pdf.part`` as a download that never finished,
    so a build that runs mid-fetch fails loudly instead of reading half a card. The
    fetcher's own infix keeps it off a browser's download of the same name.
    Rejected: writing in place, which a build can read half of; and ``.tmp``, which
    the build would list as an unread file and not as a missing card.
    """

    def test_the_builder_refuses_the_name_a_write_in_progress_has(self):
        partial = fetch.partial_name("4200006.pdf")
        self.assertRegex(partial, fetch.PARTIAL)
        self.assertEqual(fetch.partial_name("4200006.rev2.pdf"),
                         "4200006.rev2.fetch.pdf.part")
        (self.tmp / "folder").mkdir()
        (self.tmp / "folder" / partial).write_bytes(
            (SEASON / ENTRIES[6][2]).read_bytes())
        cards, rejected = scorecard.read_tree(self.tmp / "folder")
        self.assertEqual(cards, [])
        self.assertEqual([Path(r.path).name for r in rejected], [partial])

    def test_a_leftover_is_cleared_and_said_and_a_browsers_own_is_not(self):
        server = self.serve()
        self.into.mkdir()
        (self.into / "4200006.fetch.pdf.part").write_bytes(b"half a card")
        (self.into / "4200006.pdf.part").write_bytes(b"the browser's own")
        done = self.fetch(*self.args(self.season_index(server), "--quiet"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse((self.into / "4200006.fetch.pdf.part").exists())
        self.assertEqual((self.into / "4200006.pdf.part").read_bytes(),
                         b"the browser's own")
        # Under --quiet too, and under its heading, so it is not a line on its own;
        # and the command to run next, which is part of the result.
        self.assertIn(f"{heading('fetching')} removed 1 unfinished file(s) an "
                      f"earlier run left behind: 4200006.fetch.pdf.part",
                      flat(done.stdout))
        self.assertIn(f"  next         cd {pasteable(str(ROOT))} && python3 build.py "
                      f"--data {pasteable(str(self.into))}", done.stdout.splitlines())

    def test_three_leftovers_are_listed_as_a_sentence(self):
        # Joined with commas alone after the colon, where the tools' other lists
        # are sentences: "4200000.fetch.pdf.part, 4200001.fetch.pdf.part,
        # 4200002.fetch.pdf.part" (/tmp/t7/scratch/LISTS-A/before.txt).
        server = self.serve()
        self.into.mkdir()
        left = [fetch.partial_name(f"420000{number}.pdf") for number in (6, 7, 8)]
        for name in left:
            (self.into / name).write_bytes(b"half a card")
        done = self.fetch(*self.args(self.season_index(server), "--quiet"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"{heading('fetching')} removed 3 unfinished file(s) an "
                      f"earlier run left behind: {left[0]}, {left[1]} and {left[2]}",
                      flat(done.stdout))


# --- 13 -----------------------------------------------------------------------


class TestEveryRoundIsCountedAgainstItsDraw(FetchCase):
    """Short fails and over fails, and a round with no fixtures is 0 of 0.

    From the cards alone a missing card and a team with the week off look alike;
    the draw is what tells them apart, and the index is where the fetcher reads it.
    Rejected: failing only when short, which lets another division's card, or one
    match listed twice, through to the build.
    """

    def test_each_round_line_and_the_exit_line(self):
        server = self.serve()
        one, two = ids_in("1"), ids_in("2")
        write_index(self.served, [
            (1, 3, {i: rel_of(i) for i in one[:2]}),
            (2, 2, {i: rel_of(i) for i in two}),
            ("F", 0, {})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertIn("  asked for    5 card(s) in 3 round(s)", lines)
        self.assertIn("  round 1      2 of 3; 1 short: the index lists 2 card(s)",
                      lines)
        self.assertIn("round 2 3 of 2; 1 more than the draw has fixtures: a card from "
                      "another division, or one match listed twice", flat(done.stdout))
        self.assertIn("  round F      0 of 0", lines)
        self.assertIn("exit 1: 1 round(s) short of the draw and 1 round(s) with more "
                      "cards than fixtures", flat(done.stdout))


# --- 14 -----------------------------------------------------------------------


class TestACardPrintingAnotherRoundIsKeptButNotCounted(FetchCase):
    """Listed under round 3, printing round 4: on disk, and not evidence for 3.

    Kept, because it is our card and the build files it by what it prints; not
    counted, because it is not the card round 3 is missing. Rejected: counting what
    the index says, which is the index vouching for itself.
    """

    def test_it_is_written_and_the_round_says_what_it_printed(self):
        server = self.serve()
        stray = ids_in("4")[0]
        cards = {i: rel_of(i) for i in ids_in("3")[:2]}
        cards[stray] = rel_of(stray)
        write_index(self.served, [(3, 3, cards)])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"{stray}.pdf", self.written())
        lines = done.stdout.splitlines()
        at = lines.index("  round 3      2 of 3; 1 short")
        self.assertEqual(lines[at + 1],
                         f"{' ' * 15}{stray} prints round 4, so it is not counted "
                         f"here")


# --- 15 -----------------------------------------------------------------------


class _FakeSource:
    """A source that hands back what it was given, for the tests of the checks."""

    def __init__(self, rounds, bodies):
        self.rounds, self.bodies = rounds, bodies

    def describe(self) -> str:
        return "a source in this test"

    def index(self):
        return fetch.Index("memory:/index", tuple(self.rounds))

    def get(self, entry):
        return self.bodies[entry.card_id]


class TestCardsAreCountedNotFiles(unittest.TestCase):
    """A file holding three cards of the round counts three; a roundless card none.

    A club's export can be one PDF per match or one per night, so the count is of
    cards. Rejected: counting entries or files, which is wrong the first time a
    night arrives as one PDF.
    """

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="test-fetch-count-")
        self.addCleanup(holder.cleanup)
        self.into = Path(holder.name) / "into"

    def fetched(self, cards: tuple[Card, ...], fixtures: int):
        entry = fetch.Entry("1", "4200050", "memory:/4200050.pdf")
        source = _FakeSource([fetch.Round("1", fixtures, (entry,))],
                             {"4200050": fetch.Fetched(b"%PDF-1.4 counted\n")})
        with mock.patch.object(fetch, "read_verdict",
                               return_value=fetch.Verdict(True, "", cards)):
            result = fetch.run(source, self.into, {OURS})
        args = argparse.Namespace(into="into", quiet=False)
        return result, fetch.report(result, args).render(quiet=False).splitlines()

    def test_three_cards_in_one_file_count_three(self):
        cards = tuple(Card(match_id=str(n), round="1", club_hint=OURS)
                      for n in range(3))
        result, lines = self.fetched(cards, 3)
        self.assertIn("  round 1      3 of 3", lines)
        self.assertEqual(fetch.exit_status(result), 0)

    def test_a_card_that_prints_no_round_counts_none_and_says_so(self):
        result, lines = self.fetched((Card(match_id="9", round="", club_hint=OURS),),
                                     1)
        at = lines.index("  round 1      0 of 1; 1 short")
        self.assertEqual(lines[at + 1],
                         f"{' ' * 15}4200050 prints no round, so it is not counted "
                         f"here")
        self.assertEqual(fetch.exit_status(result), 1)


# --- 16 -----------------------------------------------------------------------


class TestARoundIsTheBuildersRound(FetchCase):
    """``3`` is ``"3"``, and ``03`` is not ``3``, and ``f`` is not ``F``.

    The builder keys a round by the text the card prints, stripped, and orders by
    ``round_sort_key`` without ever equating by it. The fetcher counts by the same
    key, so it never passes a card the build files under a different round.
    Rejected: equality by the week and the folded label, the first three places of
    ``round_sort_key``, which read ``03`` as ``3`` -- the draft's rule, and one the
    build does not share.
    """

    def refused(self, rounds) -> str:
        server = self.serve()
        write_index(self.served, rounds)
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(server.requests, ["/index.toml"])
        return done.stderr

    def test_a_number_and_the_same_number_as_text_are_one_round(self):
        self.assertIn("  round 3: listed twice\n", self.refused([(3, 1, {}), ("3", 1, {})]))

    def test_a_padded_number_is_another_round(self):
        server = self.serve()
        cards = {i: rel_of(i) for i in ids_in("3")}
        write_index(self.served, [(3, 3, cards), ("03", 0, {})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertLess(lines.index("  round 03     0 of 0"),
                        lines.index("  round 3      3 of 3"))

    def test_a_card_printing_a_padded_round_is_not_counted_under_the_bare_one(self):
        server = self.serve()
        (self.served / "padded.pdf").write_bytes(printing("03"))
        (self.served / "final.pdf").write_bytes(printing("F", match_id="4200094"))
        write_index(self.served, [(3, 1, {"4200093": "padded.pdf"}),
                                  ("f", 1, {"4200094": "final.pdf"})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        said = flat(done.stdout)
        self.assertIn("round 3 0 of 1; 1 short 4200093 prints round 03, so it is not "
                      "counted here", said)
        self.assertIn("round f 0 of 1; 1 short 4200094 prints round F, so it is not "
                      "counted here", said)

    def test_true_is_not_a_round(self):
        self.assertIn("  [[round]] number 1: round must be text or a whole number, "
                      "not True\n", self.refused([(True, 1, {})]))

    def test_the_rule_strips_and_nothing_more(self):
        self.assertEqual(fetch.round_label(3), "3")
        self.assertEqual(fetch.round_label(" 3 "), "3")
        self.assertEqual(fetch.round_label("03"), "03")
        for value in (True, 3.0, "  ", None, []):
            self.assertIsNone(fetch.round_label(value), value)

    def test_an_index_listing_a_round_in_superscript_is_read(self):
        """An index listing round ``³``: read, and the round listed after round 1.

        Measured before (``/tmp/t6/scratch/m16/before/probe_fetch.py``,
        ``fetch.log``): the fetcher's sort of the index's rounds stopped it with
        ``ValueError`` from ``round_sort_key``, exit 1, the index unread. The label
        is a round with a name, as it is to the build (4.151).
        """
        server = self.serve()
        cards = {i: rel_of(i) for i in ids_in("1")}
        write_index(self.served, [(1, 3, cards), ("\u00b3", 0, {})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertLess(lines.index("  round 1      3 of 3"),
                        lines.index("  round \u00b3      0 of 0"))


class TestARoundIsKeyedOneWayByDiscoveryTheFetcherAndTheDeclaredDraw(unittest.TestCase):
    """The round a card is filed under is ``card.round_key``, and every caller asks.

    Measured before (``/tmp/t6/scratch/m16/before/rule_sites.log``): the rule was
    not a function but ``.strip()``, written out in discovery's filing
    (discover.py:1112) and twice in the fetcher, in ``prints_round`` (fetch.py:634)
    and in ``round_label``'s reading of the index (fetch.py:442). With discovery's
    alone changed to drop a leading ``R``, the row's own example, discovery filed a
    card printing ``R3`` under ``3`` and the fetcher did not count it for round 3
    (``probe_onesided.py``, ``onesided.log``). The declared draw's rounds and the
    byes table are compared with the rounds discovery files cards under, and each
    stripped its own too (config.py:1306, :1119): with the key alone changed that
    way, a card printing ``R4`` under a declared ``R4`` built as two rounds, ``4``
    from the card and ``R4`` from the draw, with a note for each
    (``/tmp/t6/scratch/m16/fx/declared-before.log``). So each caller is given here,
    through its own name for the function, a rule none of them states, and has to
    follow it. Rejected: the fetcher importing discovery's filing, a tool reaching
    into the builder for a rule about a card; and comparing the callers' answers on
    sample labels, which two copies of ``strip`` pass. The report's ``prints round
    03, so it is not counted here`` quotes what the card prints, which is not its
    key under a rule that drops an ``R``, so it asks nothing of the rule.
    """

    @staticmethod
    def dropping_r(printed: str) -> str:
        """A round key no caller states: the text stripped, a leading R dropped."""
        return printed.strip().removeprefix("R")

    def test_there_is_one_function_and_every_caller_has_it(self):
        from matchcentre import config as config_mod, discover as discover_mod
        self.assertIs(discover_mod.round_key, card_mod.round_key)
        self.assertIs(fetch.round_key, card_mod.round_key)
        self.assertIs(config_mod.round_key, card_mod.round_key)
        self.assertEqual(card_mod.round_key(" 03 \t"), "03")
        self.assertEqual(card_mod.round_key("  "), "")
        self.assertEqual(card_mod.round_key(" Round  2 "), "Round  2")

    def test_discovery_files_a_card_under_the_round_it_gives(self):
        from matchcentre import config as config_mod, discover as discover_mod
        (card,), _ = scorecard.read(SEASON / ENTRIES[0][2])
        card = dataclasses.replace(card, round=" R3 ")
        config = config_mod.Config(path=SEASON / "config.toml")
        with mock.patch.object(discover_mod, "round_key", self.dropping_r):
            found = discover_mod.discover([card], [], config=config, root=SEASON)
        self.assertEqual([m.round for ms in found.matches.values() for m in ms],
                         ["3"])

    def test_the_fetcher_counts_a_card_and_reads_the_index_by_it(self):
        card = Card(match_id="9", round=" R3 ", club_hint=OURS)
        with mock.patch.object(fetch, "round_key", self.dropping_r):
            self.assertTrue(fetch.prints_round(card, "3"))
            self.assertEqual(fetch.round_label(" R3 "), "3")
        self.assertFalse(fetch.prints_round(card, "3"))
        self.assertEqual(fetch.round_label(" R3 "), "R3")

    def test_the_declared_draw_and_its_byes_are_keyed_by_it(self):
        from matchcentre import config as config_mod
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text('[competitions."tuesday-division-b".byes]\n'
                            '"Lakeside Spinners" = [" R4 "]\n'
                            '[[competitions."tuesday-division-b".rounds]]\n'
                            'number = " R4 "\n', encoding="utf-8")

            def declared():
                comp = config_mod.load(path).competition("tuesday-division-b")
                return [r.number for r in comp.rounds], comp.byes

            self.assertEqual(declared(), (["R4"], {"Lakeside Spinners": ("R4",)}))
            with mock.patch.object(config_mod, "round_key", self.dropping_r):
                self.assertEqual(declared(), (["4"], {"Lakeside Spinners": ("4",)}))


# --- 17 -----------------------------------------------------------------------


class TestACardTheSourceNoLongerGivesStillCountsIfItIsHere(FetchCase):
    """A card already verified in the folder counts, whatever the server says now.

    Rejected: letting a 404 empty the round, which would read as a card missing when
    the card is on disk, read and kept -- one server's bad afternoon reported as a
    hole in the season.
    """

    def test_the_round_is_whole_and_the_refusal_says_the_copy_counts(self):
        server = self.serve()
        source = self.season_index(server)
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        before = snapshot(self.into)
        gone = ids_in("2")[1]
        server.routes[f"/{rel_of(gone)}"] = ("status", 404)
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("  round 2      3 of 3", done.stdout.splitlines())
        self.assertIn(f"{gone} the server answered 404 Not Found; the copy already in "
                      f"data/2026-winter still counts", flat(done.stdout))
        self.assertNotIn("9d2b", done.stdout, "the server's own phrase was repeated")
        self.assertIn("  exit 1: 1 card(s) not kept", done.stdout.splitlines())
        self.assertEqual(snapshot(self.into), before)

    def test_several_that_all_still_count_are_one_line_that_says_so(self):
        server = self.serve()
        source = self.season_index(server)
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        gone = ids_in("2")[:2]
        for card_id in gone:
            server.routes[f"/{rel_of(card_id)}"] = ("status", 404)
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("  round 2      3 of 3", done.stdout.splitlines())
        self.assertIn(f"2 card(s) the server answered 404 Not Found: {gone[0]} and "
                      f"{gone[1]}; for all 2 of them the copy already in "
                      f"data/2026-winter still counts", flat(done.stdout))


# --- 18 -----------------------------------------------------------------------


class TestAnIndexWithMistakesIsRefusedBeforeAnythingIsFetched(FetchCase):
    """Every mistake in the index, each said once, and not one card asked for.

    Rejected: stopping at the first mistake, which turns a list of five into five
    runs; and fetching the rounds that were fine, which writes a half season from an
    index the club is about to correct.
    """

    def test_every_mistake_is_listed_once_and_nothing_is_requested(self):
        server = self.serve()
        other = server.base.replace("127.0.0.1", "127.0.0.2")
        text = "\n".join([
            "format = 2", 'colour = "blue"', "",
            "[[round]]", "round = 1", "fixtures = 3", 'venue = "x"',
            "[round.cards]", '"42 00" = "a.pdf"', '"../4200009" = "a.pdf"',
            '"4200003\\n" = "a.pdf"',
            '4200000 = "round-01/a.pdf"',
            "4200001 = 7", f'4200002 = "{other}/x.pdf"', 'ABC = "x.pdf"', "",
            "[[round]]", 'round = "1"', "fixtures = 3", "",
            "[[round]]", "fixtures = 2", "",
            "[[round]]", "round = true", "fixtures = 1", "",
            "[[round]]", "round = 2", "",
            "[[round]]", "round = 3", "fixtures = -1",
            "[round.cards]", '4200000 = "round-03/b.pdf"', 'abc = "y.pdf"', "",
            "[[round]]", "round = 4", "fixtures = true", ""])
        (self.served / "index.toml").write_text(text, encoding="utf-8")
        source = f"{server.base}/index.toml"
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        expected = [
            "format must be 1, the only index format this release reads",
            "unknown setting 'colour'",
            "round 1: unknown setting 'venue'",
            "card '42 00': a match id is letters, digits, - and _ only, at most 64 "
            "of them",
            # The id is the file name, so an id that climbs is refused as one.
            "card '../4200009': a match id is letters, digits, - and _ only, at "
            "most 64 of them",
            # And one that ends in a line break, which `$` would have let through
            # into a file name; shown escaped, so it is one line of the list.
            "card '4200003\\n': a match id is letters, digits, - and _ only, at "
            "most 64 of them",
            "card 4200001: the location must be text",
            f"card 4200002: {other}/x.pdf is not where the index is; every card is "
            f"read from the same place as the index",
            "round 1: listed twice",
            "[[round]] number 3: round is missing",
            "[[round]] number 4: round must be text or a whole number, not True",
            "round 2: fixtures is missing",
            "round 3: fixtures must be a whole number, 0 or more, not -1",
            "card 4200000: listed under round 1 and round 3",
            "card ABC and card abc: the same file name on a computer that ignores "
            "case",
            "round 4: fixtures must be a whole number, 0 or more, not True"]
        lines = done.stderr.splitlines()
        self.assertEqual(lines[0], f"error: the index at {source} has "
                                   f"{len(expected)} mistake(s), and nothing was "
                                   f"fetched:")
        self.assertEqual(sorted(lines[1:]), sorted(f"  {m}" for m in expected))
        self.assertEqual(server.requests, ["/index.toml"])
        self.assertFalse(self.into.exists())

    def test_an_index_that_is_not_toml_says_so(self):
        server = self.serve()
        (self.served / "index.toml").write_text("format = 1\n[[round]\n")
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("has 1 mistake(s), and nothing was fetched:", done.stderr)
        self.assertIn("  the index is not TOML this can read: ", done.stderr)

    def test_an_index_with_a_number_too_long_to_read_says_so(self):
        # Measured before (/tmp/t6/scratch/m4/w88f): a 5000-digit number was exit 1
        # and tomllib's ValueError, and the same round in hexadecimal was read and
        # then the same ValueError where round_label printed it. One function
        # reads both TOML files, config.read_toml, and refuses both as one mistake.
        server = self.serve()
        limit = sys.get_int_max_str_digits()
        for text in ("format = 1\nbig = " + "1" * 5000 + "\n",
                     "format = 1\n[[round]]\nround = 0x" + "f" * 5000 + "\n"
                     "fixtures = 1\n[round.cards]\n4200000 = \"a.pdf\"\n"):
            with self.subTest(text=text[:20]):
                (self.served / "index.toml").write_text(text)
                done = self.fetch(*self.args(f"{server.base}/index.toml"))
                self.assertEqual(done.returncode, 2, done.stderr[-300:])
                self.assertNotIn("Traceback", done.stderr)
                self.assertIn("has 1 mistake(s), and nothing was fetched:\n"
                              "  the index is not TOML this can read: a whole "
                              f"number in it is more than {limit} decimal digits "
                              "long\n",
                              done.stderr)
        self.assertEqual(server.requests, ["/index.toml"] * 2)
        self.assertFalse(self.into.exists())

    def test_an_index_with_no_rounds_says_so(self):
        server = self.serve()
        (self.served / "index.toml").write_text("format = 1\n")
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("  there is no [[round]] in it\n", done.stderr)

    def test_an_index_that_comes_back_as_a_web_page_says_so(self):
        # Two ways to know, and each case here has only one of them: a page the
        # server calls text/html that opens with words, and a sign-in page saved
        # as the index, which has no type to go by and is known by its first byte.
        # Without the check both are read as TOML and refused as a syntax error on
        # line 1, which sends a club to look for a typo.
        words = b"Your session has ended. Sign in again to continue.\n"
        server = self.serve({"/index.toml": ("html", words)})
        saved = self.served / "saved.toml"
        saved.write_bytes(SIGN_IN)
        for source in (f"{server.base}/index.toml", saved.as_uri()):
            with self.subTest(source=source):
                done = self.fetch(*self.args(source))
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertEqual(done.stderr, f"error: the index could not be read "
                                              f"from {source}: it is a web page, not "
                                              f"an index\n")
        self.assertEqual(server.requests, ["/index.toml"])
        self.assertFalse(self.into.exists())

    def test_an_index_path_no_file_can_have_is_said_and_not_raised(self):
        # Measured: `%00` in a file: URL reached open() as a NUL, and the run ended
        # in a ValueError traceback, exit 1, which is the status for a short round.
        write_index(self.served, season_rounds())
        source = (self.served / "index.toml").as_uri() + "%00"
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stderr, f"error: the index could not be read from "
                                      f"{source}: the path it names holds a NUL "
                                      f"character (%00), which no file name can\n")
        self.assertFalse(self.into.exists())

    def test_a_file_index_cannot_send_it_to_a_web_server(self):
        server = self.serve()
        path = write_index(self.served, [(1, 1, {"4200000": f"{server.base}/x.pdf"})])
        done = self.fetch(*self.args(str(path)))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(f"  card 4200000: {server.base}/x.pdf is not where the index "
                      f"is", done.stderr)
        self.assertEqual(server.requests, [])


# --- 19 -----------------------------------------------------------------------


class TestLocationsAreResolvedAgainstTheIndex(FetchCase):
    """A location is a web address relative to the index, the way a page's is.

    So ``../`` climbs, ``%20`` is a space, and a subfolder is a subfolder -- and a
    location that resolves anywhere but the index's own origin is refused before a
    card is asked for. Rejected: reading locations as paths, which works on a USB
    stick and means something else the day the index is served.
    """

    def test_a_file_index_resolves_up_across_and_down(self):
        (self.served / "round 02").mkdir()
        two = ids_in("2")[0]
        shutil.copy(self.served / rel_of(two), self.served / "round 02" / "card.pdf")
        (self.served / "idx" / "sub").mkdir(parents=True)
        three = ids_in("3")[0]
        shutil.copy(self.served / rel_of(three), self.served / "idx" / "sub" / "c.pdf")
        one = ids_in("1")[0]
        path = write_index(self.served / "idx", [
            (1, 1, {one: f"../{rel_of(one)}"}),
            (2, 1, {two: "../round%2002/card.pdf"}),
            (3, 1, {three: "sub/c.pdf"})])
        done = self.fetch(*self.args(str(path)))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.written(), {f"{one}.pdf", f"{two}.pdf", f"{three}.pdf"})

    def test_a_location_no_file_can_have_is_a_card_not_kept(self):
        # Measured: the same traceback, from one card's location, with the cards
        # before it written and the ones after it never read.
        one, two = ids_in("1")[:2]
        path = write_index(self.served, [(1, 2, {one: f"{rel_of(one)}%00",
                                                 two: rel_of(two)})])
        done = self.fetch(*self.args(str(path)))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertIn(f"{one} the path it names holds a NUL character (%00), which "
                      f"no file name can", flat(done.stdout))
        self.assertEqual(self.written(), {f"{two}.pdf"})

    def test_an_http_index_in_a_subfolder_stays_on_its_server(self):
        server = self.serve()
        one = ids_in("1")[0]
        write_index(self.served / "idx", [(1, 1, {one: f"../{rel_of(one)}"})])
        done = self.fetch(*self.args(f"{server.base}/idx/index.toml"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(server.requests, ["/idx/index.toml", f"/{rel_of(one)}"])

    def test_a_location_on_a_shared_folder_is_another_origin(self):
        # `//` makes the second a computer name to urlsplit; the first has none, and
        # is `\\\otherhost\share\x.pdf` to Windows all the same.
        path = write_index(self.served, [(1, 2, {
            "4200000": "/%5C%5Cotherhost/share/x.pdf",
            "4200001": "//otherhost/share/x.pdf"})])
        done = self.fetch(*self.args(str(path)))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for card_id, where in (("4200000", "file:///%5C%5Cotherhost/share/x.pdf"),
                               ("4200001", "file://otherhost/share/x.pdf")):
            self.assertIn(f"  card {card_id}: {where} is not where the index is; "
                          f"every card is read from the same place as the index\n",
                          done.stderr)
        self.assertFalse(self.into.exists())

    def test_another_loopback_address_is_another_origin(self):
        server = self.serve()
        other = server.base.replace("127.0.0.1", "127.0.0.2")
        write_index(self.served, [(1, 1, {"4200000": f"{other}/{rel_of('4200000')}"})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("is not where the index is", done.stderr)
        self.assertEqual(server.requests, ["/index.toml"])


# --- 20 -----------------------------------------------------------------------


class TestNothingButThisComputerIsEverAsked(FetchCase):
    """Every address that is not a loopback number is refused before a byte is sent.

    Checked by the address and never by a name, because a name is answered by a
    resolver, and asking one is already talking to the network. Every value below is
    refused with its own sentence and exit 2, under the tripwire, with a server
    listening that hears nothing. Rejected: allowing other hosts behind a flag, which
    is a switch a script flips.
    """

    ADVICE = ("  This release reads an index and its cards only from files on this "
              "computer, or\n"
              "  from a web server on it at a loopback address, such as "
              "http://127.0.0.1. It\n"
              "  cannot log in to Match Centre; see \"Collecting cards from an index\" "
              "in\n"
              "  README.md.\n")

    def cases(self, port: int) -> list[tuple[str, str]]:
        scheme = ("--from uses {0}:, which this release does not read. Give a path, "
                  "a file:/// URL, or http://127.0.0.1:PORT/...\n")
        # Not "which is not this computer", as it was: 127.1 and 0.0.0.0 both reach
        # this one, and measured, so did its own address on its network, refused in
        # those words.
        elsewhere = ("--from points at {0}, which is not written as a loopback "
                     "address.\n" + self.ADVICE)
        port_range = "--from has a port that is not a number from 0 to 65535.\n"
        share = ("--from is a path that begins with two slashes or backslashes, which "
                 "Windows reads as a folder shared from another computer; copy the "
                 "index and its cards onto this computer, and give that path.\n")
        return [
            (f"http://localhost:{port}/i.toml",
             f"--from names localhost. This release reads only from this computer, "
             f"and checks that by the address rather than by a name: write "
             f"http://127.0.0.1:{port}/i.toml instead.\n"),
            ("http://192.0.2.1/i.toml", elsewhere.format("192.0.2.1")),
            ("http://example.invalid/i.toml", elsewhere.format("example.invalid")),
            (f"https://127.0.0.1:{port}/i.toml", scheme.format("https")),
            ("ftp://127.0.0.1/i.toml", scheme.format("ftp")),
            ("file://otherhost/i.toml",
             "--from is a file: URL with a computer name in it; write it as "
             "file:///path, or give the path itself.\n"),
            # Measured: both have no computer name to urlsplit, and Windows reads
            # each as \\otherhost\share\i.toml, a folder shared from another
            # computer, which is the network.
            ("file:////otherhost/share/i.toml", share),
            ("file:///%5Cotherhost/share/i.toml", share),
            (f"http://u:p@127.0.0.1:{port}/i.toml",
             "--from has a user name or password in it. This release sends no "
             "credentials anywhere; take them out.\n"),
            (f"http://0.0.0.0:{port}/i.toml", elsewhere.format("0.0.0.0")),
            (f"http://127.1:{port}/i.toml", elsewhere.format("127.1")),
            ("http://127.0.0.1:eighty/i.toml", port_range),
            # Measured: said as "not a number", which 99999 is.
            ("http://127.0.0.1:99999/i.toml", port_range),
            # Measured: urlsplit gives no hostname for either, and the second is
            # not even an address, so the port check never sees them.
            ("http:///i.toml",
             "--from names no computer. Give a path, a file:/// URL, or "
             "http://127.0.0.1:PORT/...\n"),
            (f"http://[::1:{port}/i.toml",
             "--from is not an address this can read. Give a path, a file:/// "
             "URL, or http://127.0.0.1:PORT/...\n")]

    def test_every_other_address_is_refused_in_words(self):
        server = self.serve()
        port = server.server_address[1]
        for value, sentence in self.cases(port):
            with self.subTest(source=value):
                done = self.fetch(*self.args(value))
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertEqual(done.stderr, f"error: {sentence}")
                self.assertEqual(done.stdout, version_line() + "\n")
                self.assertNotIn("u:p", done.stdout + done.stderr)
        self.assertEqual(server.requests, [])
        self.assertFalse(self.into.exists())

    def test_the_gate_lets_through_this_computer_and_nothing_else(self):
        for url in ("http://127.0.0.1:8000/i", "http://127.8.9.1/i",
                    "http://[::1]:8000/i", "file:///x"):
            with self.subTest(url=url):
                fetch.check_origin(url)
        for value, _ in self.cases(8000):
            with self.subTest(url=value), self.assertRaises(fetch.CannotRun):
                fetch.check_origin(value)

    def test_a_windows_drive_is_a_path_and_not_a_scheme(self):
        # Measured: urlsplit reads `C:\cards\index.toml` as scheme `c`, and the gate
        # then refuses it as "--from uses c:" on any system -- a club on Windows
        # typing the path to its own index would be told it is an address.
        for value in (r"C:\cards\index.toml", "C:/cards/index.toml"):
            with self.subTest(value=value):
                url = fetch.index_url(value)
                self.assertTrue(url.startswith("file:///"), url)
                fetch.check_origin(url)


# --- 21 -----------------------------------------------------------------------


class TestARedirectIsReportedAndNotFollowed(FetchCase):
    """A redirect is said, and where it pointed is not asked for.

    A card's address that answers with a redirect is almost always a session that
    ran out and a sign-in page behind it. Rejected: following it, which is the first
    step of a fetcher that logs in.
    """

    def test_the_target_is_named_without_its_query_and_never_requested(self):
        one = ids_in("1")
        (self.served / "elsewhere").mkdir()
        shutil.copy(self.served / rel_of(ids_in("2")[0]),
                    self.served / "elsewhere" / "real.pdf")
        server = self.serve({f"/{rel_of(one[1])}": ("redirect", None)})
        # Absolute, and with a user name and password before the host, which the
        # report drops with the query: both are where a sign-in rides.
        server.routes[f"/{rel_of(one[0])}"] = (
            "redirect", f"http://u:secret@{server.base.removeprefix('http://')}"
                        f"/elsewhere/real.pdf?ticket=5e1f#top")
        write_index(self.served, [(1, 3, {i: rel_of(i) for i in one})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        said = flat(done.stdout)
        self.assertIn(f"{one[0]} redirected to {server.base}/elsewhere/real.pdf, "
                      f"which was not followed: a redirect is usually a sign-in page",
                      said)
        self.assertIn(f"{one[1]} redirected, which was not followed: a redirect is "
                      f"usually a sign-in page", said)
        self.assertNotIn("ticket", done.stdout)
        self.assertNotIn("secret", done.stdout)
        self.assertFalse([p for p in server.requests if "elsewhere" in p],
                         server.requests)
        self.assertEqual(self.written(), {f"{one[2]}.pdf"})


# --- 22 -----------------------------------------------------------------------


class TestProxySettingsAreIgnored(FetchCase):
    """A proxy in the environment is not used, whatever it says.

    The control is the point of the test: the same environment makes the standard
    library's ordinary opener fail against the same card, so the setting would have
    mattered. Rejected: ``urllib.request.urlopen``, which honours ``http_proxy`` and
    follows redirects unless told not to, each by default.
    """

    def test_the_fetch_goes_straight_to_the_server(self):
        server = self.serve()
        source = self.season_index(server)
        proxy = f"http://127.0.0.1:{closed_port()}"
        env = {key: value for key, value in self.env.items()
               if key.lower() != "no_proxy"}
        env.update(http_proxy=proxy, HTTP_PROXY=proxy)
        done = self.fetch(*self.args(source), env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.written()), 15)

        asked = len(server.requests)
        control = subprocess.run(
            (sys.executable, "-c",
             "import sys, urllib.request\n"
             "try:\n"
             f"    urllib.request.urlopen({source!r}, timeout=30)\n"
             "except OSError as exc:\n"
             "    print(exc)\n"
             "    sys.exit(3)\n"),
            env=env, capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(control.returncode, 3, control.stdout + control.stderr)
        self.assertIn("refused", control.stdout.lower())
        self.assertEqual(len(server.requests), asked)


# --- 23 -----------------------------------------------------------------------


class TestAServerThatStopsAnsweringIsReportedOnce(FetchCase):
    """One line for the card it stopped at and every card after, and no more asking.

    A dead server at thirty seconds a card over a two-hundred-card season is an
    hour and a half of the same sentence. Rejected: trying every card, which reports
    one mistake two hundred times.
    """

    def test_it_stops_at_the_first_and_says_so_in_one_line(self):
        first, second = ids_in("1")[:2]
        server = self.serve({f"/{rel_of(second)}": ("hang up", None)})
        done = self.fetch(*self.args(self.season_index(server)))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(server.requests,
                         ["/index.toml", f"/{rel_of(first)}", f"/{rel_of(second)}"])
        later = [i for i, _, _ in ENTRIES][1:]
        said = flat(done.stdout)
        self.assertIn(f"14 card(s) the server stopped answering at {second} (the "
                      f"connection was closed without an answer), so it and the 13 "
                      f"after it were not fetched: {', '.join(later[:8])} and 6 more",
                      said)
        self.assertEqual(said.count("stopped answering"), 1)
        self.assertEqual(self.written(), {f"{first}.pdf"})

    def test_it_is_one_line_when_some_of_the_cards_are_already_here(self):
        # Measured: with round 5 missing from the folder and the rest there, the one
        # stop was said twice -- eleven cards "and the copy still counts", three
        # without -- because the line was chosen by the reason and whether a copy
        # counted. It is one reason, and the copies that count are its detail.
        server = self.serve()
        source = self.season_index(server)
        self.assertEqual(self.fetch(*self.args(source)).returncode, 0)
        for card_id in ids_in("5"):
            (self.into / f"{card_id}.pdf").unlink()
        before = snapshot(self.into)
        first, second = ids_in("1")[:2]
        server.routes[f"/{rel_of(second)}"] = ("hang up", None)
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        later = [i for i, _, _ in ENTRIES][1:]
        said = flat(done.stdout)
        self.assertIn(f"14 card(s) the server stopped answering at {second} (the "
                      f"connection was closed without an answer), so it and the 13 "
                      f"after it were not fetched: {', '.join(later[:8])} and 6 "
                      f"more; for 11 of them the copy already in data/2026-winter "
                      f"still counts", said)
        self.assertEqual(said.count("stopped answering"), 1)
        self.assertIn("  round 5      0 of 3; 3 short: 3 card(s) listed for it were "
                      "not kept", done.stdout.splitlines())
        self.assertEqual(snapshot(self.into), before)

    def test_a_card_cut_short_is_the_server_stopping_and_is_never_kept(self):
        # Measured: the fetcher took the first 6,000 of a card's 21,327 bytes as the
        # card, since a read of a size stops quietly where the connection ends; the
        # builder read them as the first of its four sets, 6-2, under this club's
        # letterhead, and they were written into --into as the result, at exit 0.
        # Rejected: leaving it to the keep test, which is a test of the club and not
        # of the bytes: cut every 50 bytes from 5,350 on, where a cut first reads,
        # 287 of the 317 cuts of the round-1 card 4200000 still read as a card.
        first, second = ids_in("1")[:2]
        whole = (self.served / rel_of(second)).read_bytes()
        server = self.serve({f"/{rel_of(second)}": ("cut short",
                                                    (whole[:6000], len(whole)))})
        done = self.fetch(*self.args(self.season_index(server)))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"14 card(s) the server stopped answering at {second} (the "
                      f"answer was cut short), so it and the 13 after it were not "
                      f"fetched", flat(done.stdout))
        self.assertEqual(self.written(), {f"{first}.pdf"})

    def test_an_answer_that_is_not_http_is_said_in_the_reports_own_words(self):
        # Measured: the fetcher repeated the server's bad status line, lower-cased
        # and with its carriage return, into a report that promises none.
        first, second = ids_in("1")[:2]
        server = self.serve({f"/{rel_of(second)}": ("garbage", b"SIGN IN 7E2C\r\n")})
        done = self.fetch(*self.args(self.season_index(server)))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"14 card(s) the server stopped answering at {second} (what "
                      f"came back was not an HTTP answer), so it and the 13 after it were not "
                      f"fetched", flat(done.stdout))
        self.assertNotIn("7e2c", done.stdout.lower())
        self.assertNotIn("\r", done.stdout)

    def test_an_index_nobody_is_serving_is_said_and_nothing_is_written(self):
        source = f"http://127.0.0.1:{closed_port()}/index.toml"
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stderr, f"error: the index could not be read from "
                                      f"{source}: the server did not answer "
                                      f"(connection refused)\n")
        self.assertFalse(self.into.exists())


# --- 24 -----------------------------------------------------------------------


class TestAnOversizedAnswerIsNotKept(FetchCase):
    """Five megabytes for a card and one for an index, and a byte over is refused.

    A demo card is about 21 KB. Rejected: reading whatever arrives, which lets one
    wrong address fill a disk.
    """

    def test_a_card_over_the_limit_is_refused_by_either_transport(self):
        one = ids_in("1")[0]
        size = (self.served / rel_of(one)).stat().st_size
        path = write_index(self.served, [(1, 1, {one: rel_of(one)})])
        with loopback(self.served) as server:
            url = f"{server.base}/{rel_of(one)}"
            self.assertIsNotNone(fetch.fetch_http(url, size).body)
            refused = fetch.fetch_http(url, size - 1)
        self.assertIsNone(refused.body)
        self.assertEqual(refused.failure, "larger than 5 MB, which no scorecard is")
        self.assertIsNotNone(fetch.fetch_file((self.served / rel_of(one)).as_uri(),
                                              size).body)
        with mock.patch.object(fetch, "CARD_LIMIT", 1000):
            result = fetch.run(fetch.IndexSource(str(path)), self.into, {OURS})
        self.assertEqual([(o.kind, o.reason) for o in result.outcomes],
                         [("not_kept", "larger than 5 MB, which no scorecard is")])
        self.assertFalse(self.into.exists())

    def test_an_index_over_the_limit_stops_the_run(self):
        path = write_index(self.served, season_rounds())
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fetch, "INDEX_LIMIT", 100), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = fetch.main(["--from", str(path), "--into", str(self.into),
                                 "--letterhead", OURS])
        self.assertEqual(status, 2)
        self.assertIn("it is larger than 1 MB, which no index is", err.getvalue())


# --- 25 -----------------------------------------------------------------------


class TestAnySourceGetsTheSameChecks(unittest.TestCase):
    """A source fetches; what is kept is decided after it returns, by the fetcher.

    So a source written later, for a sanctioned export, cannot skip the letterhead
    check by returning something it believes. Rejected: each source verifying its
    own downloads, which is one check per source to get wrong.
    """

    def test_a_source_of_its_own_still_keeps_only_our_cards(self):
        holder = tempfile.TemporaryDirectory(prefix="test-fetch-source-")
        self.addCleanup(holder.cleanup)
        into = Path(holder.name) / "into"
        ours = (SEASON / ENTRIES[0][2]).read_bytes()
        entries = tuple(fetch.Entry("1", card_id, f"memory:/{card_id}")
                        for card_id in ("4200000", "4200100", "4200101"))
        source = _FakeSource([fetch.Round("1", 3, entries)], {
            "4200000": fetch.Fetched(ours),
            "4200100": fetch.Fetched(foreign(match_id="4200100")),
            "4200101": fetch.Fetched(None, "the server answered 404 Not Found")})
        result = fetch.run(source, into, {OURS})
        self.assertEqual(sorted(p.name for p in into.iterdir()), ["4200000.pdf"])
        self.assertEqual([o.kind for o in result.outcomes],
                         ["new", "not_kept", "not_kept"])
        self.assertEqual(result.outcomes[1].reason,
                         f"printed by another club: its letterhead reads {THEIRS}")


# --- 26 -----------------------------------------------------------------------


class TestTheLetterheadMustBeGiven(FetchCase):
    """No letterhead, no run -- and the refusal offers the one already in the folder.

    The whole privacy check rests on it. Rejected: defaulting to what the folder
    already holds, which is nothing on the first run and can be one stray card on
    any other.
    """

    MISSING = ("error: --letterhead is missing. It is how this tells your club's "
               "cards from another club's, and nothing is kept without it.\n")

    def test_it_is_refused_and_told_where_to_find_the_name(self):
        server = self.serve()
        done = self.fetch("--from", self.season_index(server), "--into", self.INTO)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stderr, self.MISSING + "  Copy your club's name from "
                         "the top left of one of your cards, as printed, and give it "
                         "in quotes.\n")
        self.assertEqual(server.requests, [])
        self.assertFalse(self.into.exists())

    def test_the_cards_already_there_are_offered_most_common_first(self):
        server = self.serve()
        self.into.mkdir()
        for card_id in ids_in("1"):
            shutil.copy(self.served / rel_of(card_id), self.into / f"{card_id}.pdf")
        # Four of theirs to three of ours, so that most common first is neither
        # alphabetical order nor the order the files are listed in, which both
        # put ours first.
        for number in range(4):
            match_id = f"42001{number:02d}"
            (self.into / f"{match_id}.pdf").write_bytes(foreign(match_id=match_id))
        before = snapshot(self.into)
        done = self.fetch("--from", self.season_index(server), "--into", self.INTO)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stderr, self.MISSING + (
            f"  The cards already in data/2026-winter print {THEIRS} (4 card(s)), "
            f"{OURS} (3 card(s)). To keep cards like them, add:\n"
            f"    --letterhead '{THEIRS}'\n"
            f"    --letterhead '{OURS}'\n"))
        self.assertEqual(snapshot(self.into), before)
        self.assertEqual(server.requests, [])

    def test_a_letterhead_a_shell_would_rewrite_is_offered_as_it_is_printed(self):
        """The offer is a line to paste, so the shell must hand back the letterhead.

        It was written in double quotes by hand, a third rule beside the build's
        and the schedule's, and a letterhead is copied off a card, where ``'``,
        ``"``, ``$``, a backtick and ``!`` can all be printed. Measured before on a
        copy of the tree (/tmp/t6/scratch/m12/before.txt), each offered line after
        `printf '[%s]'` under ``sh -c``: `"O$X CLUB"` came back `[O CLUB]`,
        ``"BACK`ECHO`TICK"`` `[BACKTICK]`, `"A"B CLUB"` a `Syntax error:
        Unterminated quoted string`, and `"CLUB!2026"`, which ``sh`` handed back,
        `event not found` in an interactive bash. Now the build's rule quotes it.
        Rejected: single quotes written round it by hand, which a quote in the
        letterhead, as in ST MARY'S, ends halfway.
        """
        letterhead = "ST MARY'S \"$X\" `ECHO` CLUB!2026"
        self.into.mkdir()
        (self.into / "4200100.pdf").write_bytes(write_card(CardSpec(
            match_id="4200100", division="3", club_lines=(letterhead,))))
        done = self.fetch("--from", self.season_index(self.serve()),
                          "--into", self.INTO)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        offered = [line.strip() for line in done.stderr.splitlines()
                   if line.startswith("    --letterhead ")]
        # Spelt out, because shlex.quote hands every shell below the same words
        # and is a second rule: it writes the quote in ST MARY'S as '"'"'.
        self.assertEqual(offered, ["--letterhead 'ST MARY'\\''S \"$X\" `ECHO` "
                                   "CLUB!2026'"], done.stderr)
        env = {**self.env, "X": "EXPANDED"}
        shells = [("sh", "-c", f"printf '[%s]\\n' {offered[0]}")]
        if shutil.which("bash"):
            shells.append(("bash", "--norc", "-i"))
        for shell in shells:
            with self.subTest(shell=shell[0]):
                said = subprocess.run(
                    shell, input=f"printf '[%s]\\n' {offered[0]}\n", cwd=self.tmp,
                    env=env, capture_output=True, text=True, timeout=TIMEOUT)
                self.assertNotIn("event not found", said.stderr)
                self.assertEqual(said.stdout.splitlines(),
                                 ["[--letterhead]", f"[{letterhead}]"], said.stderr)

    def test_on_windows_the_offer_keeps_the_double_quotes_windows_reads(self):
        """cmd reads no single quote, so the offer a Windows club pastes does not
        have one round it.

        Measured on the reviewed tree with Windows' documented rule for splitting
        a command line into ``sys.argv``
        (/tmp/t6/scratch/m12/measure-implementer.txt, section K), the offer
        `--letterhead 'QUOLL'\\''S CROSSING TENNIS CLUB'` reached a Windows Python
        as ``--letterhead`` and four words, ``'QUOLL'\\''S`` to ``CLUB'``, where
        the double quotes it had before give one, the letterhead. Asked in this
        process with ``matchcentre.WINDOWS`` set, and not ``os.name``, which
        pathlib reads. Rejected: single quotes on every machine, with a README
        telling a Windows club to change them by hand.
        """
        letterhead = "QUOLL'S CROSSING TENNIS CLUB"
        self.into.mkdir()
        (self.into / "4200100.pdf").write_bytes(write_card(CardSpec(
            match_id="4200100", division="3", club_lines=(letterhead,))))
        args = argparse.Namespace(letterhead=None, into=str(self.into))
        for windows, offer in ((True, f'"{letterhead}"'),
                               (False, "'QUOLL'\\''S CROSSING TENNIS CLUB'")):
            with self.subTest(windows=windows), \
                    mock.patch.object(matchcentre, "WINDOWS", windows):
                with self.assertRaises(fetch.CannotRun) as refused:
                    fetch._letterheads(args)
                self.assertEqual(refused.exception.advice[1:],
                                 (f"    --letterhead {offer}",))

    def test_an_empty_letterhead_names_no_club(self):
        server = self.serve()
        for value in ("", "   "):
            with self.subTest(letterhead=value):
                done = self.fetch(*self.args(self.season_index(server),
                                             letterhead=value))
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stderr,
                                 'error: --letterhead "" names no club.\n')
        self.assertEqual(server.requests, [])


# --- 27 -----------------------------------------------------------------------


class TestIntoMustBeAFolderWhoseParentExists(FetchCase):
    """``--into`` is made if its parent exists, and refused before anything if not.

    A mistyped path should not quietly become a new tree of folders somewhere, and
    it should not cost a fetch to find out. Rejected: ``mkdir -p``, which turns a
    typo into a season nobody can find.
    """

    def test_a_missing_parent_is_refused_and_nothing_is_created(self):
        server = self.serve()
        source = self.season_index(server)
        before = snapshot(self.tmp)
        done = self.fetch("--from", source, "--into", "nowhere/deeper/cards",
                          "--letterhead", OURS)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "error: cannot put cards into "
                                      "nowhere/deeper/cards: nowhere/deeper does not "
                                      "exist.\n  Create it first, or check the "
                                      "path.\n")
        self.assertEqual(snapshot(self.tmp, skip=(self.private,)),
                         {k: v for k, v in before.items()
                          if not k.startswith("private")})
        self.assertEqual(server.requests, [])

    def test_a_file_is_not_a_folder(self):
        server = self.serve()
        (self.tmp / "notes.txt").write_text("not a folder\n")
        done = self.fetch("--from", self.season_index(server), "--into", "notes.txt",
                          "--letterhead", OURS)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stderr, "error: cannot put cards into notes.txt: it is "
                                      "a file, not a folder.\n")
        self.assertEqual(server.requests, [])

    def test_a_parent_that_is_a_file_is_said_as_a_file(self):
        # Measured: said as "notes.txt does not exist", beside the file itself.
        server = self.serve()
        (self.tmp / "notes.txt").write_text("not a folder\n")
        done = self.fetch("--from", self.season_index(server), "--into",
                          "notes.txt/2026-winter", "--letterhead", OURS)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "error: cannot put cards into "
                                      "notes.txt/2026-winter: notes.txt is a file, "
                                      "not a folder.\n")
        self.assertEqual((self.tmp / "notes.txt").read_text(), "not a folder\n")
        self.assertEqual(server.requests, [])


# --- 28 -----------------------------------------------------------------------


class TestTheFetcherWritesOnlyIntoItsFolder(FetchCase):
    """Outside ``--into``: nothing, and its private folder gone when it is done.

    No manifest, no log, no copy of the index, nothing under the checkout. Rejected:
    a state file beside the cards, which changes every run and is one more file the
    build lists as unread.
    """

    def project(self) -> dict:
        return {name: snapshot(ROOT / name) for name in ("data", "site")} | {
            "config.toml": (ROOT / "config.toml").stat().st_mtime_ns
            if (ROOT / "config.toml").exists() else "absent"}

    def test_nothing_outside_the_folder_changes(self):
        server = self.serve()
        source = self.season_index(server)
        self.into.mkdir()
        outside = snapshot(self.tmp, skip=(self.into, self.private))
        project = self.project()
        done = self.fetch(*self.args(source))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(snapshot(self.tmp, skip=(self.into, self.private)), outside)
        self.assertEqual(self.project(), project)
        self.assertEqual(list(self.private.iterdir()), [],
                         "the fetcher's private folder is still there")


# --- 29 -----------------------------------------------------------------------


class TestAFolderStraightInDataSaysSo(unittest.TestCase):
    """``--into data`` works, and is told what a season folder would have bought.

    In a season-per-folder project, a card directly in ``data/`` takes its season
    from the year of its date. Rejected: refusing it, when one season in ``data/``
    is the README's own first example.
    """

    def test_the_note_is_for_data_itself_and_not_a_folder_in_it(self):
        self.assertEqual(
            fetch.data_note(ROOT / "data"),
            "these cards go straight into data/, with no season folder. That is fine "
            "for one season; if you keep several, give each its own folder, as in "
            "--into data/2026-winter.")
        self.assertEqual(fetch.data_note(ROOT / "data" / "2026-winter"), "")
        # And as typed in the project folder, which is how the README runs it.
        with contextlib.chdir(ROOT):
            self.assertEqual(fetch.data_note("data"), fetch.DATA_NOTE)
            self.assertEqual(fetch.data_note("data/2026-winter"), "")

    def test_a_link_to_data_is_data_and_gets_the_note(self):
        """``--into`` a link to ``data/`` is told, as ``--into data`` is.

        Measured before 4.157 (/tmp/t6/scratch/m12/before.txt, part H): the note
        compared ``os.path.abspath``, which leaves a link as it is, while the next
        line resolved it, so ``--into cardslink`` printed ``cd ... && python3
        build.py`` with no ``--data``, the line for data/ itself, and no note that
        these cards had no season folder. The two decisions are now one question,
        asked of one function. Rejected: resolving inside ``data_note`` alone,
        which fixes the link and leaves two rules to drift again.
        """
        with tempfile.TemporaryDirectory(prefix="fetch-link-") as tmp:
            link = Path(tmp) / "cardslink"
            link.symlink_to(ROOT / "data", target_is_directory=True)
            self.assertEqual(fetch.data_note(link), fetch.DATA_NOTE)
            self.assertEqual(fetch.data_note(link / "2026-winter"), "")
            self.assertNotIn("--data", demo.next_command(link))
        self.assertIs(fetch.within_data, demo.within_data)

    def test_the_folder_data_links_to_is_data_and_gets_the_note(self):
        """A project whose ``data/`` is a link: the folder it names is data/.

        Measured before 4.157 (part H): with ``L/data`` a link to ``store``,
        ``--into store`` printed the bare ``python3 build.py`` and no note, since
        ``store`` is not ``L/data`` as spelled, though it is the same folder.
        Rejected: comparing spellings, which answers differently for one folder.
        """
        with tempfile.TemporaryDirectory(prefix="fetch-link-") as tmp:
            project, store = Path(tmp) / "linked", Path(tmp) / "store"
            project.mkdir()
            store.mkdir()
            (project / "data").symlink_to(store, target_is_directory=True)
            with mock.patch.object(demo, "ROOT", project), \
                    mock.patch.object(fetch, "ROOT", project):
                self.assertEqual(fetch.data_note(store), fetch.DATA_NOTE)
                self.assertEqual(fetch.data_note(project / "data"), fetch.DATA_NOTE)
                self.assertEqual(fetch.data_note(store / "2026-winter"), "")
                self.assertNotIn("--data", demo.next_command(store))


# --- 30 -----------------------------------------------------------------------


class TestTheReportIsShapedLikeTheBuilds(unittest.TestCase):
    """Same calls, same text: the fetcher's report is the build's report.

    A club reads both, often one after the other in the same log. The class is a
    copy rather than an import so that the fetcher does not load the builder; the
    copy is pinned here so it cannot drift. Rejected: extracting a shared module
    while other work is changing the build's report.
    """

    def drive(self, report) -> tuple[str, str]:
        report.line("matchcentre-dashboard", always=True)
        report.head("fetching")
        report.field("from", "http://127.0.0.1:8000/index.toml")
        report.sentence("changed", "4200006: reads differently from 4200006.pdf, so "
                        "it was kept beside it as 4200006.rev2.pdf. build.py will "
                        "report the two as claiming the same match until you delete "
                        "the one that is wrong.", always=True)
        report.note("these cards go straight into data/, with no season folder. "
                    "That is fine for one season; if you keep several, give each its "
                    "own folder.")
        report.flow(" " * 15, "4200009 prints round 4, so it is not counted here, "
                              "and this line is long enough to wrap once")
        # A URL with a hyphen in it at the wrap point: measured, wrapping that
        # breaks on hyphens cuts it after `magpies-`, and a cut URL cannot be copied.
        report.sentence("4200001", "redirected to http://127.0.0.1:8000/round-01/"
                        "magpies-v-wombats.pdf, which was not followed: a redirect is "
                        "usually a sign-in page", always=True)
        report.flow("  exit 1: ", "one reason, " * 9, under="    ", always=True)
        # A first word too long to share its opening's line: the build starts the
        # next line with it, under the opening, and a copy that left it behind the
        # opening printed the line 89 columns wide.
        report.flow("      2026-winter-wednesday-division-3: ",
                    "data/2026-winter/round-01/magpies-v-rosellas.pdf: the card "
                    "prints 'VS' between the team names", under=" " * 8)
        # The same with a no-break space in a folder's name: one word to textwrap,
        # two to str.split, and the move is measured as textwrap measures it.
        report.flow("      2026-winter-wednesday-division-3: ",
                    "data/2026\N{NO-BREAK SPACE}winter/round-01/magpies-v-rosellas.pdf: "
                    "the card prints 'VS'", under=" " * 8)
        # One too long for any line, which moves all the same, and one behind an
        # opening of spaces alone, which has nothing to move away from.
        far = "http://127.0.0.1:8000/" + "a-folder-a-club-named-at-length/" * 2
        report.note(f"{far}index.toml was not fetched")
        report.flow("      ", f"{far}round-01.pdf: not a PDF", under=" " * 8)
        # A field whose value cannot share its label's line, which the build prints
        # on the next, and one with nothing in it, which is the label alone and is
        # still printed by --quiet.
        report.field("from", f"{far}index.toml")
        report.field("reason", "", always=True)
        # A path with spaces in it, kept one word: a copy that dropped ``keep``, or
        # gave the spaces back before the wrap, parted it where the build does not.
        spaced = ("the folder this club keeps every exported season in/2026 season "
                  "two tuesday night division c")
        report.field("into", spaced, keep=(spaced,))
        report.sentence("wrote", f"3 cards into {spaced}, and the rest was already "
                        f"there.", always=True, keep=(spaced,))
        # A text holding the first private-use character, which is the club's and
        # not a space, and a path kept inside a longer one: a copy that held by that
        # character gave it back as a space, and one that held the shorter first
        # parted the longer.
        report.flow("  ", f"\ue000 is its own, and {spaced}/round one is in {spaced}.",
                    keep=(spaced, f"{spaced}/round one"))
        report.head("quiet one")
        # A blank line of its own, as the build writes after a section: the fetcher
        # never writes one, so without it nothing here held the collapse of the blank
        # runs --quiet leaves behind, and a copy that kept them passed.
        report.line(always=True)
        report.head("result", always=True)
        report.field("wrote", "nothing", always=True)
        return report.render(quiet=False), report.render(quiet=True)

    def test_the_same_calls_print_the_same_text(self):
        self.assertEqual(self.drive(fetch._Report()), self.drive(build._Report()))

    def test_the_measures_and_the_methods_are_the_builds(self):
        self.assertEqual(fetch.WIDTH, build.WIDTH)
        self.assertEqual(fetch._Report.LABEL, build._Report.LABEL)
        self.assertEqual(fetch.LABEL, build._Report.LABEL)
        public = lambda cls: {n for n in vars(cls) if not n.startswith("_")}  # noqa: E731
        self.assertEqual(public(fetch._Report), public(build._Report) - {"detail", "VALUE"})


class TestAPathTooLongToShareItsLabelsLineStartsTheNext(FetchCase):
    """``from`` and ``into`` print a path that cannot follow the label on the next line.

    Fetched from a deep folder into a deep folder, ``from`` printed its URL behind
    the label at 194 columns and ``into`` its folder at 135
    (/tmp/t7/scratch/WID/p232-before.txt). A field flows its value now, the build's
    and this copy alike, so the path starts the next line, in the value column,
    whole. The ``next`` command is not a field, since a command wrapped without a
    `` \\`` is two commands to a shell. Rejected: cutting the path short with an
    ellipsis, which leaves one nobody can paste or find.
    """

    DEEP = ("the-folder-this-club-keeps-every-exported-season-in/"
            "2026-season-two-tuesday-night-division-c")

    def test_the_url_and_the_folder_each_start_the_line_after_their_label(self):
        server = self.serve()
        shutil.copytree(SEASON, self.served / self.DEEP)
        write_index(self.served / self.DEEP, season_rounds())
        source = f"{server.base}/{self.DEEP}/index.toml"
        into = f"{self.DEEP}/the-cards-this-club-fetched"
        (self.tmp / self.DEEP).mkdir(parents=True)
        done = self.fetch("--from", source, "--into", into, "--letterhead", OURS)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        pairs = [lines[at:at + 2] for at in range(len(lines))]
        for label, value in (("from", source), ("into", into)):
            with self.subTest(label):
                self.assertIn([f"  {label}", " " * 15 + value], pairs)
        fetching = lines[:lines.index(heading("result"))]
        self.assertEqual([line for line in fetching
                          if len(line) > 78 and " " in line.strip()], [])
        # And the command to run next is one line however long, as it was.
        at = [n for n, line in enumerate(lines) if line.startswith("  next ")]
        self.assertEqual(len(at), 1, done.stdout)
        self.assertFalse(lines[at[0] + 1:] and lines[at[0] + 1].startswith(" " * 15),
                         done.stdout)

    def test_a_path_with_spaces_in_it_is_one_word_of_the_value_too(self):
        # 4.232, the review's: flowed as words, a --from under a folder named with
        # spaces was parted at them, and --into at 78 and 71 columns, as was the
        # wrote line's folder (/tmp/t7/scratch/WID/spaced-impl.txt). Each is kept
        # one word, as the build's fields keep theirs.
        deep = self.DEEP.replace("-", " ")
        served = self.served / deep / "the index this club was handed"
        shutil.copytree(SEASON, served)
        write_index(served, season_rounds())
        source = str(served / "index.toml")
        into = f"{deep}/the cards this club fetched"
        (self.tmp / deep).mkdir(parents=True)
        done = self.fetch("--from", source, "--into", into, "--letterhead", OURS)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        pairs = [lines[at:at + 2] for at in range(len(lines))]
        for label, value in (("from", source), ("into", into)):
            with self.subTest(label):
                self.assertIn([f"  {label}", " " * 15 + value], pairs)
        wrote, = [at for at, line in enumerate(lines) if line.startswith("  wrote ")]
        self.assertEqual(lines[wrote + 1], " " * 15 + into, done.stdout)


class TestARoundLabelledAtLengthIsNamedByItsStartAndItsLength(FetchCase):
    """An index's round of 5000 characters is named by its first dozen and its length.

    The rounds section named a round by its label whole: labelled with 5000 ones,
    the label was a line of 5008 columns and each word of its count a line of its
    own under it, indented 5008 columns, eleven lines past the width; and a mistake
    in that round was a line of 5065 (/tmp/t7/scratch/WID/p168-before.txt). A card
    printing such a round was named the same way under the round it was listed for.
    Each is named by ``scope.echoed`` now, as the build's warnings name a value the
    club typed. Rejected: refusing the index for it, which turns away a file for the
    length of a label.
    """

    LONG = "1" * 5000
    NAMED = "111111111111... (5000 characters)"

    def test_the_rounds_section_names_the_round_and_what_a_card_printed(self):
        server = self.serve()
        (self.served / "long.pdf").write_bytes(printing(self.LONG))
        listed = ids_in("3")[0]
        write_index(self.served, [(self.LONG, 3, {listed: rel_of(listed)}),
                                  (3, 3, {"4200093": "long.pdf"})])
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        rounds = lines[lines.index(heading("rounds")):lines.index(heading("arrived"))]
        self.assertEqual([line for line in rounds if len(line) > 78], [])
        said = flat("\n".join(rounds))
        self.assertTrue(any(line.startswith(f"  round {self.NAMED}")
                            for line in rounds), "\n".join(rounds))
        self.assertIn("0 of 3; 3 short", said)
        self.assertIn(f"4200093 prints round {self.NAMED}, so it is not counted here",
                      said)
        self.assertIn(f"{listed} prints round 3, so it is not counted here", said)

    def test_a_mistake_in_that_round_names_it_the_same_way(self):
        server = self.serve()
        (self.served / "index.toml").write_text(
            f'format = 1\n\n[[round]]\nround = "{self.LONG}"\nfixtures = "three"\n',
            encoding="utf-8")
        done = self.fetch(*self.args(f"{server.base}/index.toml"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(f"  round {self.NAMED}: fixtures must be a whole number, 0 or "
                      f"more, not 'three'", done.stderr.splitlines())

    def test_every_other_value_a_mistake_repeats_is_named_the_same_way(self):
        # A setting, a match id, a count of fixtures, each typed 5000 characters
        # long, and a round that is a list of a hundred numbers: each mistake
        # repeated the value whole, lines of 5020, 5029, 5061, 5074 and 454
        # columns (/tmp/t7/scratch/WID/p168-review.txt). Measured against the
        # parser, as the run prints these lines.
        values = ", ".join(str(n) for n in range(100))
        text = (f'format = 1\n{"a" * 5000} = 1\n\n[[round]]\nround = 3\n'
                f'fixtures = "{self.LONG}"\n{"b" * 5000} = 1\n\n[round.cards]\n'
                f'{"c" * 5000} = "x.pdf"\n\n[[round]]\nround = [{values}]\n'
                f'fixtures = 3\n')
        with self.assertRaises(fetch.CannotRun) as refused:
            fetch.parse_index(text, (self.served / "index.toml").as_uri(),
                              "index.toml")
        self.assertEqual(refused.exception.advice, (
            "  unknown setting 'aaaaaaaaaaaa...' (5000 characters)",
            "  round 3: unknown setting 'bbbbbbbbbbbb...' (5000 characters)",
            "  round 3: fixtures must be a whole number, 0 or more, not "
            "'111111111111...' (5000 characters)",
            "  card 'cccccccccccc...' (5000 characters): a match id is letters, "
            "digits, - and _ only, at most 64 of them",
            "  [[round]] number 2: round must be text or a whole number, not "
            "[0, 1, 2, 3,... (390 characters)"))


# --- 31 -----------------------------------------------------------------------


class TestAWriteThatFailsStopsAndSaysSo(FetchCase):
    """A refused write ends the run, says which file, and leaves no half of it.

    A name another run took while this one was writing is a refused write too, and
    the file there is left as it was. Rejected: carrying on past it, which reports a
    season fetched when the disk said no; and leaving the partial for the next run,
    which a build would refuse loudly for a reason that has already been said.
    """

    def test_the_second_write_fails_and_the_first_stands(self):
        one = ids_in("1")
        path = write_index(self.served, [(1, 3, {i: rel_of(i) for i in one})])
        real, calls = os.replace, []

        def refusing(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise PermissionError(13, "Permission denied")
            return real(src, dst)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fetch.os, "replace", side_effect=refusing), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = fetch.main(["--from", str(path), "--into", str(self.into),
                                 "--letterhead", OURS])
        self.assertEqual(status, 2)
        self.assertEqual(err.getvalue(),
                         f"error: could not write {one[1]}.pdf into {self.into}: "
                         f"Permission denied. Nothing else was written after it.\n")
        self.assertIn(f"wrote 1 file(s) into {self.into}", flat(out.getvalue()))
        self.assertNotIn("  exit ", out.getvalue())
        # Nor a count of rounds, which would be a count of a run that did not finish.
        self.assertNotIn("== rounds", out.getvalue())
        self.assertEqual(sorted(p.name for p in self.into.iterdir()), [f"{one[0]}.pdf"])

    def test_a_name_taken_while_the_card_was_written_is_left_alone(self):
        # Taken here during the flush to disk, the moment between choosing the name
        # and moving the finished file onto it. os.replace alone moves over whatever
        # is there without a word, so the name is checked again just before.
        self.into.mkdir()
        taken = self.into / "4200090.pdf"
        real = os.fsync

        def racing(descriptor):
            real(descriptor)
            taken.write_bytes(b"another run's card")

        with mock.patch.object(fetch.os, "fsync", side_effect=racing), \
                self.assertRaises(FileExistsError):
            fetch.put(self.into, "4200090.pdf", card_a())
        self.assertEqual(taken.read_bytes(), b"another run's card")
        self.assertEqual([p.name for p in self.into.iterdir()], ["4200090.pdf"])


# --- 32 -----------------------------------------------------------------------


class TestTheTripwireTrips(FetchCase):
    """The hook every command-line test runs under does kill a run that reaches out.

    Without this, a tripwire that silently stopped working would make every test
    above pass for the wrong reason. Rejected: trusting it because it was written
    carefully.
    """

    def wrapped(self, code: str) -> subprocess.CompletedProcess:
        script = self.tmp / "reach.py"
        script.write_text(code)
        return subprocess.run((sys.executable, "-c", TRIPWIRE, str(script)),
                              env=self.env, capture_output=True, text=True,
                              timeout=TIMEOUT)

    def test_a_connection_to_another_computer_is_stopped_before_it_is_made(self):
        done = self.wrapped("import socket\n"
                            "socket.create_connection(('192.0.2.1', 9), timeout=5)\n")
        self.assertEqual(done.returncode, 99, done.stderr)
        self.assertIn("TRIPWIRE socket.getaddrinfo 192.0.2.1", done.stderr)

    def test_a_connect_or_a_datagram_to_another_computer_is_stopped(self):
        # The events raised by hand, so that no socket is opened at all: the test
        # above trips on the lookup first, and without these the branch for a
        # connect, and for a datagram sent with no connect, would be held by nothing.
        for event in ("socket.connect", "socket.sendto"):
            with self.subTest(event=event):
                done = self.wrapped(f"import sys\n"
                                    f"sys.audit({event!r}, None, ('192.0.2.1', 9))\n"
                                    f"print('reached')\n")
                self.assertEqual(done.returncode, 99, done.stderr)
                self.assertIn(f"TRIPWIRE {event} 192.0.2.1", done.stderr)
                self.assertEqual(done.stdout, "")
                done = self.wrapped(f"import sys\n"
                                    f"sys.audit({event!r}, None, ('127.0.0.1', 9))\n"
                                    f"print('reached')\n")
                self.assertEqual((done.returncode, done.stdout), (0, "reached\n"),
                                 done.stderr)

    def test_a_connection_to_this_computer_is_let_through(self):
        server = self.serve()
        done = self.wrapped(
            "import http.client\n"
            f"c = http.client.HTTPConnection('127.0.0.1', {server.server_address[1]})\n"
            "c.request('GET', '/index.toml')\n"
            "print(c.getresponse().status)\n")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "404"),
                         done.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
