#!/usr/bin/env python3
"""Copy the scorecards an index lists into one folder, keeping only the club's own.

    python3 tools/fetch.py --from INDEX --into data/2026-winter --letterhead "CLUB"

The contract, which every source of cards has to meet because the checks are made
here and not by the source:

  kept          a file is written only if every page in it reads as a scorecard
                printed under a letterhead the club named, and it is read for that
                in a private temporary folder, so another club's card or a sign-in
                page never lands where the build looks
  never lost    nothing already in the folder is overwritten or deleted; a card
                that comes back reading differently is kept beside the old one as
                ``<id>.rev<N>.pdf`` and reported, and one that only has new bytes
                is not written at all
  counted       every round is counted, by the cards that print it, against the
                number of fixtures the index says its draw has; short and over
                both fail

The gate. This release reads from exactly two places: files on this computer, and
``http://`` to an address written as a loopback number. A name is refused before
it is looked up -- ``localhost`` included, because asking a resolver is already
talking to the network -- and every card an index lists has to be at the index's
own origin. No redirect is followed, no proxy is used whatever the environment
says, no credential or cookie is sent, and ``urllib.request``'s opener, which does
all of those by default, is never built; the one name taken from that module is
``url2pathname``.

No source here reaches Match Centre. Whether a program may download a club's
scorecards from it with the club's login is the association's question to answer,
in writing, and until it has been answered this program does not try. Lifting the
gate is a release that adds a ``Source`` class, never a flag: a flag is a switch a
script flips.

Two fetches into one folder at the same moment are not supported. Each write
checks that its name is free just before moving the finished file into place, and
another process can take the name between the check and the move.

Exit status: 0 when every card listed is here and every round matches its draw, 1
when a round is short or over or a card was not kept, 2 when it could not run.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import errno
import http
import http.client
import ipaddress
import os
import re
import sys
import tempfile
import typing
import urllib.parse
from pathlib import Path
from urllib.request import url2pathname

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    # So that `python3 /somewhere/else/tools/fetch.py` finds its own package.
    sys.path.insert(0, str(ROOT))

from matchcentre import pasteable, scorecard, version_line        # noqa: E402
from matchcentre.card import Card, letterhead_key, round_key      # noqa: E402
from matchcentre.cli import WIDTH, Parser, fill                   # noqa: E402
from matchcentre.competition import round_sort_key                # noqa: E402
from matchcentre.config import NotToml, read_toml                 # noqa: E402
from matchcentre.scope import echoed, in_words                    # noqa: E402
# The command to run next is the demo's own rule, shared rather than copied, and
# handed the folder as typed: the rule resolves it and quotes it. A script's
# folder is the first entry on its path, so the sibling is found by name.
from demo import next_command, within_data                        # noqa: E402

LABEL = 13
CARD_LIMIT = 5 * 1024 * 1024
INDEX_LIMIT = 1024 * 1024
TIMEOUT = 30
INDEX_FORMAT = 1
GROUP_CAP = 8

# A match id is the file name a card is kept under, so it can hold nothing that
# means something to a file system: no dot, no slash, no drive.
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
# The fetcher's own unfinished writes, and nothing else. A browser's download of
# the same card is `<name>.pdf.part`, which this never matches and never removes.
PARTIAL = re.compile(r"[A-Za-z0-9_-]{1,64}(\.rev[1-9][0-9]*)?\.fetch\.pdf\.part\Z")

TOO_BIG = "larger than 5 MB, which no scorecard is"
WEB_PAGE = ("; it is a web page, which usually means a sign-in page came back "
            "instead of the card")
NUL = "the path it names holds a NUL character (%00), which no file name can"
NO_LETTERHEAD = ("prints no letterhead this could read, so which club it belongs "
                 "to is unknown")
GIVE = "Give a path, a file:/// URL, or http://127.0.0.1:PORT/..."
ELSEWHERE = (
    "  This release reads an index and its cards only from files on this computer, or",
    "  from a web server on it at a loopback address, such as http://127.0.0.1. It",
    "  cannot log in to Match Centre; see \"Collecting cards from an index\" in",
    "  README.md.")
DATA_NOTE = ("these cards go straight into data/, with no season folder. That is "
             "fine for one season; if you keep several, give each its own folder, "
             "as in --into data/2026-winter.")


# --- what a run is made of ----------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Entry:
    """One card the index lists: the round it is under, its id, where it is."""
    round: str
    card_id: str
    url: str


@dataclasses.dataclass(frozen=True)
class Round:
    label: str
    fixtures: int
    entries: tuple[Entry, ...]


@dataclasses.dataclass(frozen=True)
class Index:
    url: str
    rounds: tuple[Round, ...]


@dataclasses.dataclass(frozen=True)
class Fetched:
    """What a source brought back: a body, or the reason there is none.

    ``down`` says the source itself stopped answering, which ends the asking.
    ``too_big`` is kept apart from the words so that the index, which has its own
    limit, can say its own sentence about it.
    """
    body: bytes | None
    failure: str = ""
    down: bool = False
    web_page: bool = False
    too_big: bool = False


@dataclasses.dataclass(frozen=True)
class Verdict:
    kept: bool
    reason: str
    cards: tuple[Card, ...]


@dataclasses.dataclass(frozen=True)
class Outcome:
    """What became of one entry.

    ``kind`` is ``new``, ``unchanged``, ``same``, ``changed`` or ``not_kept``.
    ``counted`` are the cards that stand for the entry's round -- from the body that
    arrived, or from the copy already here when nothing usable did -- and
    ``elsewhere`` the ones that print another round. ``earlier`` names the copies a
    changed card was kept beside that read as cards, and ``unread`` the ones that do
    not; ``fallback`` the copy that still counts for a card that was not kept.
    """
    entry: Entry
    kind: str
    name: str = ""
    reason: str = ""
    counted: tuple[Card, ...] = ()
    elsewhere: tuple[Card, ...] = ()
    earlier: tuple[str, ...] = ()
    fallback: str = ""
    unread: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Result:
    source: str
    letterheads: tuple[str, ...]
    index: Index
    removed: tuple[str, ...]
    outcomes: tuple[Outcome, ...]
    write_error: tuple[str, str] | None = None


class CannotRun(Exception):
    """A reason the run cannot start, as the one ``error:`` sentence and its advice.

    The advice lines are printed as they are, indent included.
    """

    def __init__(self, sentence: str, advice: tuple[str, ...] = ()) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.advice = tuple(advice)


class Source(typing.Protocol):
    """Where cards come from. A source fetches; it never decides what is kept.

    Verification, naming, comparison and counting are the fetcher's, and happen
    after a source returns, so that no source -- including one written later for a
    sanctioned export -- can skip them. A source never writes a file and never
    raises from get(): a failure comes back as Fetched.failure.
    """
    def describe(self) -> str: ...          # the `from` line
    def index(self) -> Index: ...           # raises CannotRun
    def get(self, entry: Entry) -> Fetched: ...


# --- the gate -----------------------------------------------------------------


def index_url(value: str) -> str:
    """``--from`` as a URL: a path becomes a ``file:`` URL, anything else stays.

    A one-letter scheme is a Windows drive (``C:\\cards\\index.toml``), not a
    scheme, so it is a path too.
    """
    try:
        scheme = urllib.parse.urlsplit(value).scheme
    except ValueError:
        return value
    if len(scheme) <= 1:
        return Path(value).resolve().as_uri()
    return value


def check_origin(url: str) -> None:
    """The gate: a ``file:`` URL on this computer, or ``http:`` to a loopback number.

    Decided from the address as written and never from a name, so nothing is
    looked up to decide it -- a resolver is network I/O, and can be told anything.
    ``0.0.0.0``, ``127.1`` and ``2130706433`` are not written as loopback numbers
    and are refused, whatever an operating system might make of them.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("file", "http"):
            raise CannotRun(f"--from uses {parts.scheme}:, which this release does "
                            f"not read. {GIVE}")
        if scheme == "file":
            if parts.netloc:
                raise CannotRun("--from is a file: URL with a computer name in it; "
                                "write it as file:///path, or give the path itself.")
            if shared_folder(parts.path):
                raise CannotRun("--from is a path that begins with two slashes or "
                                "backslashes, which Windows reads as a folder shared "
                                "from another computer; copy the index and its cards "
                                "onto this computer, and give that path.")
            return
        if "@" in parts.netloc:
            raise CannotRun("--from has a user name or password in it. This release "
                            "sends no credentials anywhere; take them out.")
        try:
            port = parts.port
        except ValueError:
            raise CannotRun("--from has a port that is not a number from 0 to "
                            "65535.") from None
        host = parts.hostname
    except ValueError:
        raise CannotRun(f"--from is not an address this can read. {GIVE}") from None
    if not host:
        raise CannotRun(f"--from names no computer. {GIVE}")
    if host == "localhost":
        netloc = "127.0.0.1" if port is None else f"127.0.0.1:{port}"
        raise CannotRun("--from names localhost. This release reads only from this "
                        "computer, and checks that by the address rather than by a "
                        f"name: write {parts._replace(netloc=netloc).geturl()} "
                        f"instead.")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        # Not "which is not this computer": 127.1, 0.0.0.0 and this computer's own
        # address on its network all reach it, and are refused all the same.
        raise CannotRun(f"--from points at {host}, which is not written as a "
                        f"loopback address.", ELSEWHERE)


def shared_folder(path: str) -> bool:
    """Whether a ``file:`` URL's path, unquoted, begins with two slashes or backslashes.

    Windows reads such a path as ``\\\\computer\\share``, another computer's
    folder, whatever ``urlsplit`` says about the URL's computer name: measured,
    ``file:////otherhost/share`` and ``file:///%5Cotherhost/share`` both have none.
    Decided from the text, the same on every system, so a card cannot pass here and
    reach the network there.
    """
    return re.match(r"[\\/]{2}", urllib.parse.unquote(path)) is not None


def _origin(url: str) -> tuple:
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "file":
        return ("file", parts.netloc, shared_folder(parts.path))
    try:
        port = parts.port
    except ValueError:
        port = "not a number"
    return (scheme, parts.hostname, 80 if port is None and scheme == "http" else port)


def same_origin(a: str, b: str) -> bool:
    """Whether two URLs are the same place: scheme, computer and port."""
    try:
        return _origin(a) == _origin(b)
    except ValueError:
        return False


# --- the transports -----------------------------------------------------------


def _web_page(body: bytes, content_type: str = "") -> bool:
    kind = content_type.split(";")[0].strip().lower()
    return kind == "text/html" or body.lstrip()[:1] == b"<"


def _status(code: int) -> str:
    # The phrase is the standard's, never the server's own text.
    try:
        return f"{code} {http.HTTPStatus(code).phrase}"
    except ValueError:
        return str(code)


def _redirected(url: str, location: str | None) -> str:
    """Where a redirect pointed, as scheme, computer, port and path only.

    The query and fragment are dropped, because that is where a sign-in ticket
    rides, and so is anything before an ``@``.
    """
    tail = "which was not followed: a redirect is usually a sign-in page"
    if not location:
        return f"redirected, {tail}"
    try:
        target = urllib.parse.urlsplit(urllib.parse.urljoin(url, location))
        host = target.hostname or ""
        port = target.port
    except ValueError:
        return f"redirected, {tail}"
    if ":" in host:
        host = f"[{host}]"
    place = f"{host}:{port}" if port is not None else host
    return f"redirected to {target.scheme}://{place}{target.path}, {tail}"


def _why(exc: BaseException) -> str:
    """Why a server did not answer, in the words the report uses for it."""
    if isinstance(exc, http.client.RemoteDisconnected):
        return "the connection was closed without an answer"
    if isinstance(exc, http.client.IncompleteRead):
        return "the answer was cut short"
    if isinstance(exc, http.client.HTTPException):
        # Never its own words, which are the server's: a status line that is not
        # one is repeated as it came, carriage return and all.
        return "what came back was not an HTTP answer"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, TimeoutError):
        return f"timed out after {TIMEOUT} seconds"
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror.lower()
    return (str(exc) or type(exc).__name__).lower()


def fetch_http(url: str, limit: int) -> Fetched:
    """One GET, straight to the server named, with nothing added to it.

    ``http.client`` and not an opener: it has no proxy handler to read the
    environment and no redirect handler to follow a ``Location``, so neither can be
    misconfigured. No cookie, no authorisation and no user agent are sent -- the
    module sends none unless told to. Only a 200 is a body, and only one as long
    as it said it would be.
    """
    parts = urllib.parse.urlsplit(url)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    connection = http.client.HTTPConnection(parts.hostname, parts.port or 80,
                                            timeout=TIMEOUT)
    try:
        connection.request("GET", urllib.parse.quote(
            target, safe="/%:@!$&'()*+,;=-._~?"))
        response = connection.getresponse()
        if 300 <= response.status < 400:
            return Fetched(None, _redirected(url, response.getheader("Location")))
        if response.status != 200:
            return Fetched(None, f"the server answered {_status(response.status)}")
        body = response.read(limit + 1)
        if len(body) > limit:
            return Fetched(None, TOO_BIG, too_big=True)
        if response.length:
            # What is left of the length it promised. A read of a size stops
            # quietly where the connection ends, as a read of the whole never does.
            raise http.client.IncompleteRead(body, response.length)
        return Fetched(body, web_page=_web_page(
            body, response.getheader("Content-Type") or ""))
    except (OSError, http.client.HTTPException) as exc:
        return Fetched(None, _why(exc), down=True)
    finally:
        connection.close()


def fetch_file(url: str, limit: int) -> Fetched:
    """A file on this computer, read no further than one byte past the limit."""
    path = Path(url2pathname(urllib.parse.urlsplit(url).path))
    if "\0" in str(path):
        # `%00` in the URL. open() refuses it with a ValueError, not an OSError.
        return Fetched(None, NUL)
    try:
        with open(path, "rb") as handle:
            body = handle.read(limit + 1)
    except FileNotFoundError:
        return Fetched(None, f"no such file: {path}")
    except OSError as exc:
        return Fetched(None, f"could not be read: {exc.strerror or exc}")
    if len(body) > limit:
        return Fetched(None, TOO_BIG, too_big=True)
    return Fetched(body, web_page=_web_page(body))


# --- the index ----------------------------------------------------------------


def round_label(value) -> str | None:
    """The round rule: what the index writes, as the builder keys a round.

    A whole number is its decimal text, and either is keyed by ``card.round_key``,
    as a card's printed round is, and nothing else is done to it -- so ``3`` and
    ``"3"`` are one round, and ``"03"`` and ``"3"`` are two, because the build files
    them as two. ``None`` for anything that is not a round at all, ``true`` included.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = round_key(str(value))
    elif isinstance(value, str):
        text = round_key(value)
    else:
        return None
    return text or None


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _mistaken(shown: str, mistakes: list[str]) -> CannotRun:
    return CannotRun(f"the index at {shown} has {len(mistakes)} mistake(s), and "
                     f"nothing was fetched:", tuple(f"  {m}" for m in mistakes))


def parse_index(text: str, url: str, shown: str) -> Index:
    """The index, or every mistake in it at once.

    Collected rather than raised one at a time, so that an index with five mistakes
    is one correction and not five runs; and all of them before any card is asked
    for, so that a half-right index never becomes half a season on disk.
    """
    try:
        data = read_toml(text)
    except NotToml as exc:
        raise _mistaken(shown, [f"the index is not TOML this can read: {exc}"])
    mistakes: list[str] = []
    form = data.get("format")
    if not (_whole(form) and form == INDEX_FORMAT):
        mistakes.append("format must be 1, the only index format this release reads")
    # Keys and ids are shown as repr() gives them, so that one with a line break in
    # it is still one line of the list, and by echoed, so that a long one fits it.
    mistakes += [f"unknown setting {echoed(key, quoted=True)}" for key in data
                 if key not in ("format", "round")]
    tables = data.get("round")
    if not (isinstance(tables, list) and tables
            and all(isinstance(t, dict) for t in tables)):
        mistakes.append("there is no [[round]] in it")
        tables = []

    rounds: list[Round] = []
    labels: set[str] = set()
    under: dict[str, str] = {}       # card id -> the round it was first listed under
    folded: dict[str, str] = {}      # casefolded id -> the id as first written
    for number, table in enumerate(tables, 1):
        label = round_label(table.get("round"))
        if "round" not in table:
            mistakes.append(f"[[round]] number {number}: round is missing")
        elif label is None:
            mistakes.append(f"[[round]] number {number}: round must be text or a "
                            f"whole number, not "
                            f"{echoed(table['round'], quoted=True)}")
        name = (f"[[round]] number {number}" if label is None
                else f"round {echoed(label)}")
        mistakes += [f"{name}: unknown setting {echoed(key, quoted=True)}"
                     for key in table if key not in ("round", "fixtures", "cards")]
        fixtures = table.get("fixtures")
        if "fixtures" not in table:
            mistakes.append(f"{name}: fixtures is missing")
        elif not _whole(fixtures):
            mistakes.append(f"{name}: fixtures must be a whole number, 0 or more, "
                            f"not {echoed(fixtures, quoted=True)}")
        if label is not None:
            if label in labels:
                mistakes.append(f"{name}: listed twice")
            labels.add(label)
        cards = table.get("cards", {})
        if not isinstance(cards, dict):
            mistakes.append(f"{name}: cards must be a table of match ids and where "
                            f"each card is")
            cards = {}
        entries = []
        for card_id, where in cards.items():
            if not ID.match(card_id):
                mistakes.append(f"card {echoed(card_id, quoted=True)}: a match id is "
                                f"letters, digits, - and _ only, at most 64 of them")
                continue
            if card_id in under:
                mistakes.append(f"card {card_id}: listed under {under[card_id]} and "
                                f"{name}")
                continue
            if card_id.casefold() in folded:
                mistakes.append(f"card {folded[card_id.casefold()]} and card "
                                f"{card_id}: the same file name on a computer that "
                                f"ignores case")
                continue
            under[card_id] = name
            folded[card_id.casefold()] = card_id
            if not isinstance(where, str) or not where.strip():
                mistakes.append(f"card {card_id}: the location must be text")
                continue
            resolved = urllib.parse.urljoin(url, where.strip())
            if not same_origin(url, resolved):
                mistakes.append(f"card {card_id}: {resolved} is not where the index "
                                f"is; every card is read from the same place as the "
                                f"index")
                continue
            entries.append(Entry(label or "", card_id, resolved))
        rounds.append(Round(label or "", fixtures if _whole(fixtures) else 0,
                            tuple(entries)))
    if mistakes:
        raise _mistaken(shown, mistakes)
    rounds.sort(key=lambda r: round_sort_key(r.label))
    return Index(url, tuple(rounds))


class IndexSource:
    """The index and its cards, from a path, a ``file:`` URL or loopback ``http:``.

    The gate runs when the source is made, before it has read anything.
    """

    def __init__(self, value: str) -> None:
        self.value = value
        self.url = index_url(value)
        check_origin(self.url)

    def describe(self) -> str:
        return self.value

    def _fetch(self, url: str, limit: int) -> Fetched:
        if urllib.parse.urlsplit(url).scheme.lower() == "http":
            return fetch_http(url, limit)
        return fetch_file(url, limit)

    def index(self) -> Index:
        got = self._fetch(self.url, INDEX_LIMIT)
        unread = f"the index could not be read from {self.url}: "
        if got.body is None:
            if got.too_big:
                raise CannotRun(unread + "it is larger than 1 MB, which no index is")
            if got.down:
                raise CannotRun(unread + f"the server did not answer ({got.failure})")
            raise CannotRun(unread + got.failure)
        if got.web_page:
            raise CannotRun(unread + "it is a web page, not an index")
        try:
            text = got.body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise _mistaken(self.value, [f"the index is not TOML this can read: it "
                                         f"is not UTF-8 text ({exc.reason} at byte "
                                         f"{exc.start})"]) from None
        return parse_index(text, self.url, self.value)

    def get(self, entry: Entry) -> Fetched:
        return self._fetch(entry.url, CARD_LIMIT)


# --- what is kept -------------------------------------------------------------


def judge(cards, rejections, letterheads) -> Verdict:
    """The letterhead rule: every page a scorecard, every one printed by us.

    Every card in the file is checked, not only the first, because a file is kept
    whole or not at all. A card whose letterhead did not read is not ours: absence
    of a letterhead is not evidence of our letterhead.
    """
    cards = tuple(cards)
    if rejections:
        # The reason only. The detail and describe() carry the private path the
        # body was read from, which means nothing to a club and changes every run.
        return Verdict(False, rejections[0].reason, cards)
    if not cards:
        return Verdict(False, "no scorecard in it", cards)
    keys = [letterhead_key(card.club_hint or "") for card in cards]
    if not all(keys):
        return Verdict(False, NO_LETTERHEAD, cards)
    for key in keys:
        if key not in letterheads:
            return Verdict(False, f"printed by another club: its letterhead reads "
                                  f"{key}", cards)
    return Verdict(True, "", cards)


def read_verdict(path, letterheads) -> Verdict:
    return judge(*scorecard.read(path), letterheads)


def same_transcription(a, b) -> bool:
    """Whether two files read as the same cards, wherever each was read from.

    Defined by the reader the build uses, so "the card changed" means what the
    build would see change. ``source`` is the one field ignored.
    """
    return ([dataclasses.replace(card, source="") for card in a]
            == [dataclasses.replace(card, source="") for card in b])


def prints_round(card: Card, label: str) -> bool:
    """Whether a card is evidence for a round: the round ``round_key`` files it under.

    ``round_sort_key`` orders rounds and never equates them, in the build or here.
    """
    return round_key(card.round) == label


# --- the folder ---------------------------------------------------------------


def copies(into: Path, card_id: str) -> list[tuple[int, Path]]:
    """The copies of one card already here, by revision; the plain file is 1."""
    if not into.is_dir():
        return []
    pattern = re.compile(rf"{re.escape(card_id.casefold())}"
                         rf"(?:\.rev([1-9][0-9]*))?\.pdf\Z")
    found = []
    for path in into.iterdir():
        match = pattern.match(path.name.casefold())
        if match and path.is_file():
            found.append((int(match.group(1) or 1), path))
    return sorted(found)


def next_name(card_id: str, existing) -> str:
    """The name a new reading of a card is kept under: one past the highest."""
    if not existing:
        return f"{card_id}.pdf"
    return f"{card_id}.rev{max(number for number, _ in existing) + 1}.pdf"


def partial_name(name: str) -> str:
    """The name a card has while it is being written -- one the build refuses."""
    return f"{name.removesuffix('.pdf')}.fetch.pdf.part"


def put(into: Path, name: str, body: bytes) -> None:
    """The one function that writes under ``--into``, and it never overwrites.

    Written whole under a name the build refuses as an unfinished download, flushed
    to the disk, and only then moved to its own name, which is checked to be free
    just before. So a build running at the same moment sees a whole card or none.
    """
    into.mkdir(exist_ok=True)
    final = into / name
    partial = into / partial_name(name)
    handle = open(partial, "xb")
    try:
        with handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if final.exists():
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(final))
        os.replace(partial, final)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def stale_partials(into: Path) -> list[Path]:
    if not into.is_dir():
        return []
    return sorted(path for path in into.iterdir()
                  if PARTIAL.match(path.name) and path.is_file())


def letterheads_in(into: Path) -> collections.Counter:
    """The letterheads the cards already here print, for the missing-flag advice."""
    found: collections.Counter = collections.Counter()
    for path in sorted(into.iterdir()):
        if path.is_file() and path.suffix.lower() == ".pdf":
            for card in scorecard.read(path)[0]:
                key = letterhead_key(card.club_hint or "")
                if key:
                    found[key] += 1
    return found


# --- the run ------------------------------------------------------------------


def _stopped(card_id: str, why: str, after: int) -> str:
    rest = (f"so it and the {after} after it were not fetched" if after
            else "so it was not fetched")
    return f"the server stopped answering at {card_id} ({why}), {rest}"


def _split(cards, label: str) -> tuple[tuple[Card, ...], tuple[Card, ...]]:
    return (tuple(c for c in cards if prints_round(c, label)),
            tuple(c for c in cards if not prints_round(c, label)))


def _not_kept(entry: Entry, reason: str, existing, letterheads) -> Outcome:
    """A card not kept, and the verified copy already here that counts instead."""
    for _, path in reversed(existing):
        verdict = read_verdict(path, letterheads)
        if verdict.kept:
            counted, elsewhere = _split(verdict.cards, entry.round)
            return Outcome(entry, "not_kept", reason=reason, counted=counted,
                           elsewhere=elsewhere, fallback=path.name)
    return Outcome(entry, "not_kept", reason=reason)


def run(source: Source, into: Path, letterheads) -> Result:
    """Ask the source for every card the index lists, and keep what is ours."""
    into = Path(into)
    letterheads = tuple(letterheads)
    index = source.index()
    removed = []
    for path in stale_partials(into):
        path.unlink()
        removed.append(path.name)
    entries = [entry for r in index.rounds for entry in r.entries]
    outcomes: list[Outcome] = []
    down = ""

    def result(write_error=None) -> Result:
        return Result(source.describe(), letterheads, index, tuple(removed),
                      tuple(outcomes), write_error)

    with tempfile.TemporaryDirectory(prefix="matchcentre-fetch-") as holder:
        private = Path(holder)
        for position, entry in enumerate(entries):
            existing = copies(into, entry.card_id)
            if down:
                # One mistake, said once: the cards after it are not asked for.
                outcomes.append(_not_kept(entry, down, existing, letterheads))
                continue
            got = source.get(entry)
            if got.body is None:
                reason = got.failure
                if got.down:
                    reason = down = _stopped(entry.card_id, got.failure,
                                             len(entries) - position - 1)
                outcomes.append(_not_kept(entry, reason, existing, letterheads))
                continue
            # Read where the build never looks, so that what is not ours never
            # lands under --into, not even for a moment.
            looked_at = private / f"{entry.card_id}.pdf"
            looked_at.write_bytes(got.body)
            try:
                verdict = read_verdict(looked_at, letterheads)
            finally:
                looked_at.unlink()
            if not verdict.kept:
                reason = verdict.reason + (WEB_PAGE if got.web_page else "")
                outcomes.append(_not_kept(entry, reason, existing, letterheads))
                continue
            counted, elsewhere = _split(verdict.cards, entry.round)
            kept = _keeper(entry, counted, elsewhere)
            match = next((path for _, path in existing
                          if path.read_bytes() == got.body), None)
            if match is not None:
                outcomes.append(kept("unchanged", match.name))
                continue
            match = next((path for _, path in existing
                          if _reads_as(path, verdict.cards)), None)
            if match is not None:
                outcomes.append(kept("same", match.name))
                continue
            name = next_name(entry.card_id, existing)
            try:
                put(into, name, got.body)
            except OSError as exc:
                return result((name, exc.strerror or str(exc)))
            reads = {path.name: bool(_reading(path)) for _, path in existing}
            outcomes.append(dataclasses.replace(
                kept("changed" if existing else "new", name),
                earlier=tuple(n for n, card in reads.items() if card),
                unread=tuple(n for n, card in reads.items() if not card)))
    return result()


def _keeper(entry: Entry, counted, elsewhere):
    """An outcome for a card that was ours, whichever way it turned out."""
    def kept(kind: str, name: str) -> Outcome:
        return Outcome(entry, kind, name=name, counted=counted, elsewhere=elsewhere)
    return kept


def _reading(path: Path) -> tuple[Card, ...]:
    """The cards a copy already here reads as, or none if it is not a card the build
    reads: a file it refuses is not a reading a new card could differ from."""
    theirs, rejected = scorecard.read(path)
    return () if rejected else tuple(theirs)


def _reads_as(path: Path, cards) -> bool:
    theirs = _reading(path)
    return bool(theirs) and same_transcription(cards, theirs)


# --- the report ---------------------------------------------------------------


class _Report:
    """The run's output, collected then printed once.

    Collected rather than printed as it happens for two reasons: the sections
    have to come out in a fixed order even though the work does not happen in
    that order, and ``--quiet`` has to be able to drop the middle of the report
    without leaving the blank lines that surrounded it. Plain ASCII, no colour, no
    carriage returns -- this is read as often in a piped file as on a terminal.

    A copy of ``build.py``'s, method for method, so that the two reports a club
    reads one after the other are one shape; a test holds the copy to the original.
    """

    # The width of the label column, so that a sentence wrapped under a field lines
    # up with the value above it rather than with the margin.
    LABEL = LABEL

    def __init__(self) -> None:
        self._lines: list[tuple[bool, str]] = []

    def head(self, title: str, *, always: bool = False) -> None:
        bar = "=" * max(3, WIDTH - len(title) - 4)
        self.line(always=always)
        self.line(f"== {title} {bar}", always=always)

    def line(self, text: str = "", *, always: bool = False) -> None:
        self._lines.append((always, text.rstrip()))

    def field(self, label: str, value: str, *, always: bool = False,
              keep: tuple[str, ...] = ()) -> None:
        """``label``, and ``value`` flowed from the column after it, as the build's.

        A value too long to share the label's line starts the next one, in the
        value column: behind its label, a deep ``--into`` ran to 135 columns. An
        empty value is the label alone.
        """
        if not value.strip():
            self.line(f"  {label}", always=always)
            return
        self.flow(f"  {label:<{self.LABEL}}", value, always=always, keep=keep)

    def flow(self, opening: str, text: str, *, under: str | None = None,
             always: bool = False, keep: tuple[str, ...] = ()) -> None:
        """``opening``, then ``text`` wrapped to the width the report declares.

        The one place this report wraps anything, so that there is one answer to
        what a folded line looks like. Continuations land under the text by default
        -- under the value of a field, under the word after ``note:`` -- because a
        second line that starts at the margin is indistinguishable from a new item.
        ``under`` overrides that.

        Long words are left running over rather than cut. Only paths and URLs reach
        that length, which is to say only the words that have to be exact, and a
        club can scroll a line it cannot retype. A first word that cannot share the
        opening's line starts the next one instead, under it, so a line this prints
        past the width holds one word, textwrap's, and nothing else; an opening of
        spaces alone is not printed as a line of its own. Each of ``keep`` is one
        word, spaces and all, as ``fill`` has it: a ``--from`` under a folder with
        a space in it was parted there. Wrapped by the build's own ``fill``, which
        the package holds, and not by a copy of it.
        """
        indent = " " * len(opening) if under is None else under

        def wrapped(first: str) -> list[str]:
            return [line for line in fill(text, indent, first=first,
                                          keep=keep).split("\n") if line]

        lines = wrapped(opening)
        if opening.strip() and lines and len(lines[0]) > WIDTH:
            self.line(opening, always=always)
            lines = wrapped(indent)
        for line in lines:
            self.line(line, always=always)

    def note(self, text: str, *, always: bool = False) -> None:
        """A ``note:`` line: something the run worked out, in prose."""
        self.flow("  note: ", text, always=always)

    def sentence(self, label: str, text: str, *, always: bool = False,
                 keep: tuple[str, ...] = ()) -> None:
        """A field whose value is a sentence rather than a value."""
        self.flow(f"  {label:<{self.LABEL}}", text, always=always, keep=keep)

    def render(self, *, quiet: bool) -> str:
        kept = [text for always, text in self._lines if always or not quiet]
        out: list[str] = []
        for text in kept:
            # Collapse the blank runs that filtering leaves behind, and never
            # start the report with one.
            if not text and (not out or not out[-1]):
                continue
            out.append(text)
        while out and not out[-1]:
            out.pop()
        return "\n".join(out)


def data_note(into) -> str:
    """The note for ``--into data`` itself, and nothing for a folder inside it.

    Asked of the demo's ``within_data``, the question ``next_command`` asks, so
    that the note and the next line agree about which folder is ``data/``.
    """
    return DATA_NOTE if within_data(into) == Path(".") else ""


def _tally(result: Result):
    """Each round with its count and its outcomes: the one reading of the draw."""
    for rnd in result.index.rounds:
        outs = [o for o in result.outcomes if o.entry in rnd.entries]
        yield rnd, sum(len(o.counted) for o in outs), outs


def _problems(result: Result) -> list[str]:
    short = sum(count < rnd.fixtures for rnd, count, _ in _tally(result))
    over = sum(count > rnd.fixtures for rnd, count, _ in _tally(result))
    lost = sum(o.kind == "not_kept" for o in result.outcomes)
    return [part for n, part in (
        (short, f"{short} round(s) short of the draw"),
        (over, f"{over} round(s) with more cards than fixtures"),
        (lost, f"{lost} card(s) not kept")) if n]


def _listed(ids: list[str]) -> str:
    more = len(ids) - GROUP_CAP
    return in_words([*ids[:GROUP_CAP], *([f"{more} more"] if more > 0 else [])])


def _changed(o: Outcome) -> str:
    """A card kept beside the copies already here, and what the build makes of them.

    A copy that reads as a card is one the new one reads differently from, and the
    build reports the two as claiming one match; a copy that does not is one the
    build refuses, and saying the card "reads differently" from it would be false.
    """
    beside = "them" if len(o.earlier) + len(o.unread) > 1 else "it"
    unread = in_words(o.unread)
    many = len(o.unread) > 1
    refuse = ("will refuse them until you delete them" if many
              else f"will refuse {unread} until you delete it")
    if not o.earlier:
        return (f"{o.entry.card_id}: the cop{'ies' if many else 'y'} already here, "
                f"{unread}, do{'' if many else 'es'} not read as "
                f"{'cards' if many else 'a card'}, so this one was kept beside "
                f"{beside} as {o.name}. build.py {refuse}.")
    several = len(o.earlier) > 1
    said = f"{o.entry.card_id}: reads differently from {in_words(o.earlier)}"
    if o.unread:
        said += (f", and {unread} do{'' if many else 'es'} not read as "
                 f"{'cards' if many else 'a card'}")
    said += (f", so it was kept beside {beside} as {o.name}. build.py will report "
             f"{'the copies' if several else 'the two'} as claiming the same match "
             f"until you delete the one that is wrong")
    return said + (f", and {refuse}." if o.unread else ".")


def _still_counts(into: str, counting: int, of: int) -> str:
    """How many of a group of cards not kept have a copy here that counts instead."""
    if not counting:
        return ""
    if of == 1:
        return f"; the copy already in {into} still counts"
    return (f"; for {'all ' if counting == of else ''}{counting} of them the copy "
            f"already in {into} still counts")


def report(result: Result, args: argparse.Namespace) -> _Report:
    out = _Report()
    out.line(version_line(), always=True)
    into = str(args.into)
    entries = [e for r in result.index.rounds for e in r.entries]

    out.head("fetching", always=bool(result.removed))
    out.field("from", result.source, keep=(result.source,))
    out.field("into", into, keep=(into,))
    out.sentence("keeping", "cards whose letterhead reads "
                 + ", or ".join(result.letterheads))
    out.field("asked for", f"{len(entries)} card(s) in "
                           f"{len(result.index.rounds)} round(s)")
    if result.removed:
        out.sentence("removed", f"{len(result.removed)} unfinished file(s) an earlier "
                                f"run left behind: {in_words(result.removed)}",
                     always=True)
    note = data_note(into)
    if note:
        out.note(note)

    if result.write_error is None:
        lines = []
        for rnd, count, outs in _tally(result):
            said = f"{count} of {rnd.fixtures}"
            if count < rnd.fixtures:
                said += f"; {rnd.fixtures - count} short"
                causes = []
                if len(rnd.entries) < rnd.fixtures:
                    causes.append(f"the index lists {len(rnd.entries)} card(s)")
                lost = sum(o.kind == "not_kept" and not o.fallback for o in outs)
                if lost:
                    causes.append(f"{lost} card(s) listed for it were not kept")
                if causes:
                    said += ": " + "; ".join(causes)
            elif count > rnd.fixtures:
                said += (f"; {count - rnd.fixtures} more than the draw has fixtures: "
                         f"a card from another division, or one match listed twice")
            details = [f"{o.entry.card_id} prints round {echoed(c.round.strip())}, so "
                       f"it is not counted here" if c.round.strip() else
                       f"{o.entry.card_id} prints no round, so it is not counted here"
                       for o in outs for c in o.elsewhere]
            lines.append((f"round {echoed(rnd.label)}", said, details,
                          count != rnd.fixtures or bool(details)))
        if lines:
            out.head("rounds", always=any(loud for *_, loud in lines))
            for label, said, details, loud in lines:
                out.sentence(label, said, always=loud)
                for detail in details:
                    out.flow(" " * (2 + LABEL), detail, always=loud)

    kinds = collections.Counter(o.kind for o in result.outcomes)
    changed = [o for o in result.outcomes if o.kind == "changed"]
    if kinds["new"] or kinds["unchanged"] or kinds["same"] or changed:
        out.head("arrived", always=bool(changed))
        if kinds["new"]:
            out.field("new", f"{kinds['new']} card(s)")
        if kinds["unchanged"]:
            out.field("unchanged", f"{kinds['unchanged']} card(s), byte for byte")
        if kinds["same"]:
            out.sentence("same card", f"{kinds['same']} card(s) came back as new bytes "
                                      f"that read exactly as the copy already here, "
                                      f"so none was written again.")
        for o in changed:
            out.sentence("changed", _changed(o), always=True)

    # Grouped by the reason alone: whether a copy still counts is a detail of the
    # one mistake, and a server that stopped answering is said once however many
    # of the cards after it are already here.
    groups: dict[str, list[Outcome]] = {}
    for o in result.outcomes:
        if o.kind == "not_kept":
            groups.setdefault(o.reason, []).append(o)
    if groups:
        out.head("not kept", always=True)
        for reason, outs in groups.items():
            ids = [o.entry.card_id for o in outs]
            counts = _still_counts(into, sum(bool(o.fallback) for o in outs), len(ids))
            if len(ids) == 1:
                out.sentence(ids[0], reason + counts, always=True)
            else:
                out.sentence(f"{len(ids)} card(s)", f"{reason}: {_listed(ids)}{counts}",
                             always=True)

    out.head("result", always=True)
    written = kinds["new"] + kinds["changed"]
    already = kinds["unchanged"] + kinds["same"]
    if written:
        wrote = f"{written} file(s) into {into}"
        if already:
            wrote += f"; the other {already} card(s) were already there"
    elif already and already == len(entries):
        wrote = f"nothing: all {already} card(s) were already in {into}"
    else:
        wrote = "nothing"
    out.sentence("wrote", wrote, always=True, keep=(into,))
    if result.write_error is not None:
        return out
    if written:
        # A line and not a field: a command wrapped with no `` \`` is two to a shell.
        out.line(f"  {'next':<{LABEL}}{next_command(Path(into))}", always=True)
    problems = _problems(result)
    if problems:
        out.flow("  exit 1: ", in_words(problems), always=True)
    else:
        out.flow("  exit 0: ", f"all {len(entries)} card(s) are here, and every "
                               f"round matches its draw", always=True)
    return out


def exit_status(result: Result) -> int:
    if result.write_error is not None:
        return 2
    return 1 if _problems(result) else 0


# --- entry point --------------------------------------------------------------

DESCRIPTION = """\
Copy the scorecards an index lists into one folder, keeping only your club's.

The index is a small TOML file giving, for each round, how many fixtures the draw
has and where each card is. This release reads the index and the cards from files
on this computer, or from a web server running on this same computer at a
loopback address, such as http://127.0.0.1. It does not log in to Match Centre,
and it does not reach the internet.

Nothing already in DIR is overwritten or deleted. A card that comes back reading
differently from the copy already there is kept beside it, and reported."""

EPILOG = """\
The index format, a worked example and a weekly script are in README.md, under
"Collecting cards from an index"."""


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        # `prog` spelled out, because argparse would take it from argv[0] and print
        # `usage: fetch.py` -- which is not a command that works from the project
        # folder, where the README tells a club to stand.
        prog="tools/fetch.py",
        description=DESCRIPTION, epilog=EPILOG)
    parser.add_argument("--from", dest="source", required=True, metavar="INDEX",
                        help="the index: a path, a file:/// URL, or "
                             "http://127.0.0.1:PORT/...")
    parser.add_argument("--into", required=True, metavar="DIR",
                        help="the folder the cards go in, e.g. data/2026-winter; "
                             "created if its parent exists")
    # Not required by argparse, so that its refusal can offer the letterheads the
    # folder's cards already print.
    parser.add_argument("--letterhead", action="append", metavar="TEXT",
                        help="your club's name as the top left of its cards prints "
                             "it; case and spacing are ignored. Give it twice if "
                             "your cards have printed two versions")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="print only problems and the final result")
    parser.add_argument("--version", action="version", version=version_line(),
                        help="print this build and the Python running it, then "
                             "stop")
    return parser


def _letterheads(args: argparse.Namespace) -> tuple[str, ...]:
    if not args.letterhead:
        into = Path(args.into)
        found = letterheads_in(into) if into.is_dir() else collections.Counter()
        if found:
            ranked = found.most_common()
            advice = (f"  The cards already in {args.into} print "
                      + ", ".join(f"{key} ({n} card(s))" for key, n in ranked)
                      + ". To keep cards like them, add:",
                      *(f"    --letterhead {pasteable(key)}" for key, _ in ranked))
        else:
            advice = ("  Copy your club's name from the top left of one of your "
                      "cards, as printed, and give it in quotes.",)
        raise CannotRun("--letterhead is missing. It is how this tells your club's "
                        "cards from another club's, and nothing is kept without it.",
                        advice)
    keys = tuple(dict.fromkeys(letterhead_key(value) for value in args.letterhead))
    if not all(keys):
        raise CannotRun('--letterhead "" names no club.')
    return keys


def _check_into(into: str) -> None:
    """``--into`` is a folder, or can be made as one without making its parent."""
    path = Path(into)
    if path.is_dir():
        return
    if path.exists():
        raise CannotRun(f"cannot put cards into {into}: it is a file, not a folder.")
    if path.parent.exists() and not path.parent.is_dir():
        raise CannotRun(f"cannot put cards into {into}: {path.parent} is a file, not "
                        f"a folder.")
    if not path.parent.is_dir():
        raise CannotRun(f"cannot put cards into {into}: {path.parent} does not exist.",
                        ("  Create it first, or check the path.",))


def _flush(out: _Report, quiet: bool) -> None:
    """Print the whole report at once, at the very end, as ``build.py`` does."""
    text = out.render(quiet=quiet)
    if not text:
        return
    try:
        print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # `fetch.py | head` closes the pipe early; pointing the descriptor at
        # os.devnull keeps the interpreter's own final flush from printing a
        # traceback over a run that finished.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        letterheads = _letterheads(args)
        source = IndexSource(args.source)
        _check_into(args.into)
        result = run(source, Path(args.into), letterheads)
    except CannotRun as refusal:
        stamp = _Report()
        stamp.line(version_line(), always=True)
        _flush(stamp, args.quiet)
        print(f"error: {refusal.sentence}", file=sys.stderr)
        for line in refusal.advice:
            print(line, file=sys.stderr)
        return 2
    _flush(report(result, args), args.quiet)
    if result.write_error is not None:
        name, why = result.write_error
        print(f"error: could not write {name} into {args.into}: {why}. Nothing else "
              f"was written after it.", file=sys.stderr)
    return exit_status(result)


if __name__ == "__main__":
    sys.exit(main())
