#!/usr/bin/env python3
"""Make a release: bump the version, check a tag, write the notes, publish.

    python3 tools/release.py prepare 0.2.0      # the version, the changelog
    python3 tools/release.py check v0.2.0       # is this tag ready?
    python3 tools/release.py notes              # as GitHub will show them
    python3 tools/release.py publish v0.2.0 --dry-run

This is a maintainer's tool, not something a club ever needs. A release is a
pushed tag: ``.github/workflows/release.yml`` runs ``check`` on it, then the
whole suite on every supported Python, then ``publish``, which makes the GitHub
Release a club downloads. The local half is ``prepare``, and CONTRIBUTING.md's
*Making a release* lists the steps between the two.

``prepare VERSION`` writes the new version into ``matchcentre/__init__.py``,
moves the changelog's ``[Unreleased]`` section under a heading of the version
and the date, and rewrites every example of the report's first line in the
files the suite's version test reads, which is how the issue forms used to go
stale: in a git work tree, the files git keeps or would add, and not what a
.gitignore leaves out. It commits nothing and tags nothing, and it prints what
is left.

``check TAG`` says whether a tag is ready: ``v`` and the version in the code,
the newest release the changelog describes, dated, with something in it, and
nothing left in ``[Unreleased]``. Every problem is given at once.

``notes [VERSION]`` prints the changelog's section for a version, each
hard-wrapped paragraph and item joined into one line, because GitHub's release
page can show a single newline as a break, and the changelog is wrapped at
about 85 columns.

``publish TAG`` zips the tagged tree with ``git archive``, writes the notes
beside it under ``dist/``, and makes the release with ``gh``, or updates it
when a re-run finds it there. It refuses a tag ``check`` refuses, a tag that
is not the commit checked out, a tag that origin, when there is one, has moved
to another commit since, and a changelog or a version changed since the tag.
``--dry-run`` does all of that but ask origin and run gh, and prints each gh
command. gh releases to the repository GH_REPO names, which the workflow sets,
and a publish without it is refused rather than left to gh to guess.

Exit status: 0 done, 1 refused or failed, 2 a command line argparse refused.
Nothing here reaches the network but what ``publish`` runs: ``git ls-remote``
of origin, and gh.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterator, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    # The only things this tool imports from the project: the name and the
    # version line every command answers ``--version`` with, and the parser
    # and the fill every command refuses by. Git and gh are run, not imported.
    sys.path.insert(0, str(ROOT))

from matchcentre import NAME, version_line                    # noqa: E402
from matchcentre.cli import Parser, fill                      # noqa: E402
from matchcentre.scope import in_words                        # noqa: E402

PACKAGE = "matchcentre/__init__.py"
CHANGELOG = "CHANGELOG.md"
DEFAULT_OUT = "dist"

# The files the suite's version test reads, which are the files an example of
# the report's first line is rewritten in: tests/test_project_complete.py's
# SHIPPED_SUFFIXES and NOT_SHIPPED, which a test holds these equal to. The
# changelog is left out by its name there and here, as a history of every
# version.
SHIPPED_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".toml", ".txt",
                    ".yml"}
NOT_SHIPPED = frozenset({"notes", ".git", "__pycache__", "site", "data",
                         "reports", "public", "reports-public", "dist"})

# Semantic Versioning 2.0.0's own expression, with [0-9] for its \d, which in
# Python also matches every other script's digits.
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PART = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(
    rf"({_NUMBER})\.({_NUMBER})\.({_NUMBER})"
    rf"(?:-({_PART}(?:\.{_PART})*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
VERSION_LINE = re.compile(r"""^__version__[ \t]*=[ \t]*(["'])([^"'\n]*)\1"""
                          r"[ \t]*\r?$", re.MULTILINE)
# The report line's version, read whole as semver writes it: the expression
# tests/test_project_complete.py's version test reads every example with,
# which a test holds this equal to. prepare rewrites exactly the examples it
# reads as the current version, so that the two cannot disagree on a line:
# `0.1.0.1` is read as 0.1.0 and more, and rewritten. NAME holds no character
# an expression reads as other than itself.
REPORT_LINE = re.compile(
    NAME + r" ([0-9]+\.[0-9]+\.[0-9]+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)")
HEADING = re.compile(r"## \[([^\]]+)\](.*)")
FENCE = re.compile(r"^ *(`{3,}|~{3,})")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
UNRELEASED = "Unreleased"
# GitHub refuses a release whose notes are longer, in characters, and the
# lines that close them (a rule, the SHA-256, the run) take no more than the
# margin.
NOTES_LIMIT = 125_000
CLOSING = 500

# Markdown's blocks, as far as reading a changelog needs them.
_BULLET = re.compile(r"( *)([-*+])( +)\S")
_ORDERED = re.compile(r"( *)([0-9]{1,9})[.)]( +)\S")
_BREAK = re.compile(r" {0,3}([-*_])(?: *\1){2,} *$")
_UNDERLINE = re.compile(r" {0,3}(?:=+|-+) *$")
_ATX = re.compile(r" {0,3}#{1,6}(?:\s|$)")
# A table's delimiter row, which GFM writes with or without its outer pipes:
# `|---|:--:|` or `--- | ---`.
_DELIMITER = re.compile(r" {0,3}\|? *:?-+:? *(?:\| *:?-+:? *)+\|? *$"
                        r"| {0,3}\| *:?-+:? *\|? *$")


# --- versions ---------------------------------------------------------------


def parse_version(text: str) -> tuple | None:
    """*text*'s precedence as a sortable tuple, or None if it is no version.

    semver.org's section 11: the three numbers, then a release above any of
    its pre-releases, whose dot-separated parts compare numbers as numbers,
    below words, and a longer list above a shorter one it starts with. Build
    metadata is read and does not count.
    """
    match = SEMVER.fullmatch(text)
    if not match:
        return None
    numbers = tuple(int(part) for part in match.group(1, 2, 3))
    if match.group(4) is None:
        return (*numbers, 1, ())
    parts = tuple((0, int(part)) if part.isdigit() else (1, part)
                  for part in match.group(4).split("."))
    return (*numbers, 0, parts)


def newer(a: str, b: str) -> bool:
    """Whether version *a* comes after version *b*; False if either is none."""
    first, second = parse_version(a), parse_version(b)
    return first is not None and second is not None and first > second


def is_pre_release(version: str) -> bool:
    key = parse_version(version)
    return key is not None and key[3] == 0


# --- the changelog ----------------------------------------------------------


class Section(NamedTuple):
    """One ``## [name] rest`` section of a changelog, by its line numbers."""

    name: str
    rest: str            # what follows the heading's brackets, stripped
    heading: int         # the index of its heading line
    end: int             # the index of the line after its last


def _expanded(line: str) -> str:
    """*line* with the whitespace that indents it as spaces, each tab going
    on to the next multiple of four columns, as CommonMark 0.31 reads it
    (2.2). Measured before this: a tab was one column, so a code block
    indented by one was read as a paragraph, and joined."""
    body = line.lstrip(" \t")
    return line[:len(line) - len(body)].expandtabs(4) + body


def _depth(line: str) -> int:
    """How many columns *line* is indented, a tab read as Markdown reads it."""
    shown = _expanded(line)
    return len(shown) - len(shown.lstrip(" "))


def _opens_fence(line: str) -> re.Match | None:
    """The fence *line* opens, or None: not a backtick fence whose info
    string holds a backtick, which CommonMark 0.31 reads as an inline code
    span (4.5). Measured before: ```` ```x``` is spelled out ```` opened a
    fence that nothing closed, and every older heading was hidden."""
    opened = FENCE.match(line)
    if opened and opened.group(1)[0] == "`" and "`" in line[opened.end():]:
        return None
    return opened


def _starts_block(line: str) -> bool:
    """Whether *line*, outside the item it follows, starts a block of its
    own rather than going on with the item's paragraph: a heading, a fence, a
    rule, a quote, or an item. Any number starts an item here, as the list it
    starts interrupts no paragraph of its own container (CommonMark 0.31,
    5.2)."""
    bare = _expanded(line).lstrip(" ")
    return bool(_ATX.match(bare) or _opens_fence(bare) or _BREAK.match(bare)
                or bare.startswith(">") or _BULLET.match(bare)
                or _ORDERED.match(bare))


class _Fenced:
    """Which lines are fenced code, read a line at a time.

    A fence closes at its closing line, or, as CommonMark 0.31 has it (4.5),
    at the end of the block that holds it: a fence opened in a list item ends
    at the first line indented less than the item's content, which ends the
    item. Measured before this: one closing fence left out of an
    ``[Unreleased]`` item hid every release heading below it, which GitHub
    still showed as headings, and ``check`` passed a release whose notes held
    every older section.

    A line indented less than the item, straight after a line of its
    paragraph, is the paragraph's still, and the item goes on, unless the
    line starts a block of its own (5.1): measured before that, one wrapped
    line written at the margin ended the item, a fence opened in it after
    was ended by nothing, and the older headings were hidden again.
    """

    def __init__(self) -> None:
        self.fence: str | None = None   # the open fence's backticks or tildes
        self.inside = 0                 # the content indent of its item
        self.items: list[int] = []      # the content indents of open items
        self.paragraph = False          # whether the last line was prose

    def __call__(self, line: str) -> bool:
        """Whether *line* is code: a fence, or a line between two."""
        line = _expanded(line.rstrip("\r\n"))
        depth = len(line) - len(line.lstrip(" "))
        if self.fence:
            if not line.strip() or depth >= self.inside:
                opened = FENCE.match(line)
                if (opened and opened.group(1)[0] == self.fence[0]
                        and len(opened.group(1)) >= len(self.fence)
                        and not line.strip().strip(self.fence[0])):
                    self.fence = None
                self.paragraph = False
                return True
            self.fence = None           # its item has ended, and it with it
            self.paragraph = False
        lazy = self.paragraph and not _starts_block(line)
        if line.strip() and not lazy:
            while self.items and depth < self.items[-1]:
                self.items.pop()
        margin = self.items[-1] if self.items else 0
        if line.strip() and depth >= margin + 4:
            # Four columns past the content of the item it is in, a line
            # starts no block: it is code, or after a paragraph's line, the
            # paragraph's (CommonMark 0.31, 4.4, 4.5, 5.2). Measured before:
            # its own `- ` was taken for an item, so code was joined.
            return False
        opened = _opens_fence(line)
        if opened:
            self.fence = opened.group(1)
            self.inside = margin
            self.paragraph = False
            return True
        # A heading, a rule or an underline is read from the content of the
        # item it is in, not from the margin.
        inner = line[min(depth, margin):]
        item = _BULLET.match(line) or _ORDERED.match(line)
        if item and not _BREAK.match(inner):
            self.items.append(item.end() - 1)
        self.paragraph = bool(
            line.strip() and not _ATX.match(inner) and not _BREAK.match(inner)
            and not _UNDERLINE.match(inner))
        return False


def _index(lines: list[str]) -> list[Section]:
    """Every bracketed level-two section of *lines*, a code block's excepted.

    A section runs to the next heading of level one or two, so that a link
    list or a closing ``#`` heading is nobody's notes.
    """
    headings, fenced = [], _Fenced()
    for number, line in enumerate(lines):
        if not fenced(line) and re.match(r"#{1,2}(?:\s|$)", line):
            headings.append(number)
    found = []
    for at, number in enumerate(headings):
        match = HEADING.match(lines[number].rstrip("\r\n"))
        if match:
            end = headings[at + 1] if at + 1 < len(headings) else len(lines)
            found.append(Section(match.group(1), match.group(2).strip(),
                                 number, end))
    return found


def sections(text: str) -> Iterator[tuple[str, str, str]]:
    """Each section of a changelog: its name, the rest of its heading, its
    body as written."""
    lines = text.splitlines(keepends=True)
    for section in _index(lines):
        yield (section.name, section.rest,
               "".join(lines[section.heading + 1:section.end]))


def _trimmed(body: str) -> str:
    """*body* without its blank lines at either end, ending in one newline."""
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "".join(line + "\n" for line in lines)


def holds_something(body: str) -> bool:
    """Whether a section says anything: a line that is not blank nor a
    heading. Headings with nothing under them are nothing to release."""
    return any(line.strip() and not re.match(r" {0,3}#{1,6}(?:\s|$)", line)
               for line in body.splitlines())


def _unreleased(name: str) -> bool:
    """Whether a section's name is ``Unreleased``, which Keep a Changelog
    writes capitalised, and others do not."""
    return name.casefold() == UNRELEASED.casefold()


def _core(version: str) -> str:
    """*version* without its pre-release and its build: 0.2.0 of 0.2.0-rc.1."""
    return re.split(r"[-+]", version, maxsplit=1)[0]


def gathered(lines: list[str], index: list[Section], version: str,
             current: str | None
             ) -> tuple[Section | None, list[Section], str]:
    """What ``prepare VERSION`` gathers: ``[Unreleased]``, which it moves,
    the sections of the pre-releases it ends, and the body they make
    together.

    A final release ends the pre-releases of its own version when the code
    says one of them: 0.2.0 after 0.2.0-rc.1. Their changes are gathered
    under it as well, so that its notes hold everything since the last
    final, which is the release a club upgrading from one comes from, and
    their own sections stay where they are, as their releases do.
    ``[Unreleased]`` goes first, then each pre-release, newest first,
    subsection by subsection. With no pre-release to end, the body is
    ``[Unreleased]``'s as written.
    """
    unreleased = next((each for each in index if _unreleased(each.name)),
                      None)
    ended: list[Section] = []
    if (current is not None and parse_version(version) is not None
            and not is_pre_release(version) and is_pre_release(current)
            and _core(current) == _core(version)):
        ended = [each for each in index
                 if is_pre_release(each.name)
                 and _core(each.name) == _core(version)]
    if unreleased is None:
        return None, ended, ""
    bodies = ["".join(lines[each.heading + 1:each.end])
              for each in (unreleased, *ended)]
    if not ended:
        return unreleased, ended, _trimmed(bodies[0])
    return unreleased, ended, _merged(bodies)


def _merged(bodies: list[str]) -> str:
    """*bodies* as one: what comes before any ``###`` heading, then each
    subsection in the order first met, its parts in the order given.

    Two parts that are each a tight list of the same marker are one list;
    any others are kept a blank line apart.
    """
    order: list[str | None] = [None]
    parts: dict[str | None, list[str]] = {None: []}

    def take(heading: str | None, held: list[str]) -> None:
        text = _trimmed("".join(line + "\n" for line in held))
        if text:
            if heading not in parts:
                order.append(heading)
                parts[heading] = []
            parts[heading].append(text)

    for body in bodies:
        fenced, heading, held = _Fenced(), None, []
        for line in body.splitlines():
            if not fenced(line) and line.startswith("### "):
                take(heading, held)
                heading, held = line.strip(), []
            else:
                held.append(line)
        take(heading, held)
    blocks = []
    for heading in order:
        if not parts[heading]:
            continue
        joined = parts[heading][0]
        for before, part in zip(parts[heading], parts[heading][1:]):
            joined += ("" if _one_list(before, part) else "\n") + part
        blocks.append(joined if heading is None
                      else f"{heading}\n\n{joined}")
    return "\n".join(blocks)


def _one_list(first: str, second: str) -> bool:
    """Whether *second* continues *first* as the same tight list."""
    one, two = _BULLET.match(first), _BULLET.match(second)
    return bool(one and two and not one.group(1) and not two.group(1)
                and one.group(2) == two.group(2)
                and "\n\n" not in first and "\n\n" not in second)


def dated(rest: str) -> tuple[str | None, str | None]:
    """A heading's date and the problem with it, of which one is None.

    The rest of the heading is ``— YYYY-MM-DD``, as this changelog writes it,
    or ``- YYYY-MM-DD``, as Keep a Changelog does.
    """
    given = rest.lstrip("—–- \t").strip()
    if not given:
        return None, "has no date"
    try:
        if not DATE.fullmatch(given):
            raise ValueError(given)
        date.fromisoformat(given)
    except ValueError:
        return None, (f"is dated {given}, which is not a real date: write "
                      f"it YYYY-MM-DD")
    return given, None


# --- unwrapping -------------------------------------------------------------


def unwrap(text: str) -> str:
    """*text* with each hard-wrapped paragraph and list item on one line.

    A line is joined to the one before it when Markdown reads it as that
    paragraph's continuation, and is left on its own line when Markdown reads
    it by its line: code fenced with backticks or tildes, a heading, a table
    row, a quote, a rule, a setext underline, an item's first line, and the
    line after a hard break, two spaces or a backslash. A numbered line starts
    an item only where CommonMark 0.31 lets it (5.2): after a blank line or a
    block, when its number is 1, or when it is indented less than the item it
    follows, whose list it then continues. Otherwise ``7. The exit`` is the
    rest of a sentence.

    A table is read as GFM writes it, its outer pipes optional: a delimiter
    row, the row above it, and each line below it, a ``|`` in it or not,
    until a blank line or the start of another block. Code indented four
    columns past the content of the item it is in, or past the margin, after
    anything but a paragraph's line, is code, as is each line after it
    indented as far, and blank lines between. A tab indents to the next
    multiple of four columns.
    """
    lines = text.splitlines()
    out: list[str] = []
    joinable = False     # whether the last line out is a paragraph's
    indent = 0           # the content indent of the paragraph being joined
    fenced = _Fenced()
    table = False        # whether the last line out is a table's row
    code = None          # the indent of the indented code being kept
    for at, line in enumerate(lines):
        if fenced(line):
            out.append(line)
            joinable = table = False
            code = None
            continue
        depth = _depth(line)
        margin = fenced.items[-1] if fenced.items else 0
        if code is not None and (not line.strip() or depth >= code):
            out.append(line)
            continue
        code = None
        if line.strip() and not joinable and depth >= margin + 4:
            code = margin + 4
            out.append(line)
            table = False
            continue
        row = (table and line.strip() and not _starts_block(line)) or (
            _DELIMITER.match(line) or "|" in line.replace("\\|", "")
            and at + 1 < len(lines) and _DELIMITER.match(lines[at + 1]))
        if row:
            out.append(line)
            joinable, table = False, True
            continue
        table = False
        # Read from the content of the item it is in, as _Fenced reads it.
        # Measured before: from the margin, so in an item whose content
        # starts four columns in, a heading was joined to the line above.
        inner = _expanded(line)[min(depth, margin):]
        if (not line.strip() or _ATX.match(inner)
                or line.lstrip().startswith(("|", ">"))
                or _BREAK.match(inner) or _UNDERLINE.match(inner)):
            out.append(line)
            joinable = False
            continue
        shown = _expanded(line)
        bullet, ordered = _BULLET.match(shown), _ORDERED.match(shown)
        item = bullet or ordered
        if item and (bullet or not joinable or ordered.group(2) == "1"
                     or len(ordered.group(1)) < indent):
            out.append(line)
            joinable = True
            indent = item.end() - 1
            continue
        if joinable and not re.search(r"(?:  |\\)$", out[-1]):
            # Its own trailing spaces are kept: two of them are a hard
            # break, which the next line's test reads.
            out[-1] = out[-1].rstrip() + " " + line.lstrip()
            continue
        if not joinable:
            indent = depth
        out.append(line)
        joinable = True
    return "".join(line + "\n" for line in out)


# --- reading the project ----------------------------------------------------


def _read(root: Path, name: str) -> tuple[str | None, str | None]:
    """A project file's text and the problem reading it, of which one is
    None."""
    try:
        return (root / name).read_bytes().decode("utf-8"), None
    except OSError as exc:
        return None, f"{name} cannot be read: {exc.strerror or exc}."
    except UnicodeDecodeError:
        return None, f"{name} is not UTF-8, which this tool reads it as."


def current_version(text: str) -> tuple[str | None, str | None]:
    """The version a package file holds, and the problem with it."""
    found = VERSION_LINE.findall(text)
    if len(found) != 1:
        return None, (f"{PACKAGE} has {len(found) or 'no'} __version__ "
                      f"line{'s' if len(found) != 1 else ''} where it should "
                      f"have one, so which version this is cannot be read.")
    return found[0][1], None


def problems(root: Path, tag: str | None
             ) -> tuple[list[str], str | None, str | None, tuple[str, ...]]:
    """Everything wrong with releasing *tag*, its version, its date, and
    what in the first is to be kept whole on its line.

    *tag* None is ``v`` and the version in the code.
    """
    said: list[str] = []
    keep: list[str] = []
    package, problem = _read(root, PACKAGE)
    current = None
    if problem:
        said.append(problem)
    else:
        current, problem = current_version(package)
        if problem:
            said.append(problem)
    if tag is None and current is not None:
        tag = f"v{current}"
    if tag is None:
        # The changelog is read all the same, as it is for a tag that is no
        # version, so that every problem is said at once. Measured before:
        # this returned, and the changelog's problems were said only by the
        # run after the version was mended.
        said.append("There is no tag to check: name one, such as v0.2.0.")
        version = None
    else:
        version = tag[1:] if tag.startswith("v") else None
        if version is None or parse_version(version) is None:
            said.append(f"{tag} is not a release tag, which is v and a "
                        f"semantic version, such as v0.2.0 or v0.2.0-rc.1.")
            version = None
        elif current is not None and current != version:
            said.append(f"The tag is {tag}, but {PACKAGE} says {current}.")
    text, problem = _read(root, CHANGELOG)
    if problem:
        return said + [problem], version, None, ()
    lines = text.splitlines(keepends=True)
    index = _index(lines)
    # A release is a section named by a version. Measured before: any name
    # but `Unreleased` was one, so `## [unreleased]` was taken for the
    # newest release, asked for a date, and its changes went unmentioned.
    released = [each for each in index
                if parse_version(each.name) is not None]
    for each in index:
        body = "".join(lines[each.heading + 1:each.end])
        if _unreleased(each.name):
            if holds_something(body):
                said.append(f"{CHANGELOG}'s [Unreleased] section still "
                            f"holds changes, and the release's notes would "
                            f"not mention them: move them under the "
                            f"release's heading, or out of the file.")
        elif each not in released:
            heading = f"## [{each.name}]"
            keep.append(heading)
            said.append(f"{CHANGELOG}'s {heading} is neither [Unreleased] "
                        f"nor a version, so nothing says whether it is "
                        f"released: name it one or the other.")
    if not released:
        said.append(f"{CHANGELOG} describes no release at all.")
        return said, version, None, ()
    newest = released[0]
    if version is not None and newest.name != version:
        # The command is offered only where it would run: prepare refuses a
        # version that is not newer than the one in the code, so it cannot
        # mend an old tag or a version that was bumped by hand, and refuses
        # to move an [Unreleased] that is not there or holds nothing, as in
        # every tree just after a release, where the mend for a mistyped
        # tag is to delete it.
        mend = ""
        if newer(version, current or "") and newer(version, newest.name):
            unreleased, ended, moved = gathered(lines, index, version,
                                                current)
            if unreleased is not None and holds_something(moved):
                command = f"python3 tools/release.py prepare {version}"
                keep.append(command)
                taken = in_words(f"[{each.name}]" for each in ended)
                mend = (f": {command} moves the [Unreleased] section under "
                        f"it" + (f", with the changes of {taken}" if ended
                                 else ""))
        said.append(f"The newest release {CHANGELOG} describes is "
                    f"{newest.name}, not {version}{mend}.")
    when, problem = dated(newest.rest)
    if problem:
        example = f"## [{newest.name}] — YYYY-MM-DD"
        keep.append(example)
        said.append(f"The heading of {CHANGELOG}'s [{newest.name}] section "
                    f"{problem}, as in {example}.")
    body = "".join(lines[newest.heading + 1:newest.end])
    if not holds_something(body):
        said.append(f"{CHANGELOG}'s [{newest.name}] section holds nothing, "
                    f"so the release would have no notes.")
    else:
        # Refused by GitHub only after the suite's 25 minutes otherwise.
        length = len(unwrap(_trimmed(body)))
        if length > NOTES_LIMIT - CLOSING:
            said.append(f"{CHANGELOG}'s [{newest.name}] section is "
                        f"{length:,} characters as release notes, and "
                        f"GitHub refuses notes over {NOTES_LIMIT:,} with "
                        f"the lines that close them: shorten it, or split "
                        f"the release.")
    if len(released) > 1 and not newer(newest.name, released[1].name):
        said.append(f"{newest.name} is not newer than {released[1].name}, the "
                    f"release before it in {CHANGELOG}.")
    return said, version, when, tuple(keep)


# --- running git and gh -----------------------------------------------------


class Done(NamedTuple):
    """What a program run by :func:`_run` exited with and printed."""

    code: int
    out: str
    err: str


def _run(argv: list[str], cwd: Path) -> Done:
    """*argv* run in *cwd*, a list and never a shell line, its output kept.

    Every git and gh this tool runs is run here. A program that is not there
    exits 127, as a shell says it.
    """
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True,
                              encoding="utf-8", errors="replace")
    except OSError as exc:
        return Done(127, "", f"{argv[0]}: {exc.strerror or exc}")
    return Done(done.returncode, done.stdout, done.stderr)


def _say_errors(lines: list[str], ending: str, keep: tuple[str, ...] = ()
                ) -> int:
    # What was printed before it goes first: stdout is held until the
    # process exits when it is a pipe, as in a workflow's log, and stderr is
    # not, so a failed publish's error came before the archive it wrote.
    sys.stdout.flush()
    for line in lines:
        print(fill(f"error: {line}", first="", keep=keep), file=sys.stderr)
    print(fill(ending, "", keep=keep), file=sys.stderr)
    return 1


# --- the commands -----------------------------------------------------------


def prepare(args, root: Path) -> int:
    """The version, the changelog's move and every example, or nothing."""
    # An empty --date, as a script's unset variable gives, is a date given
    # and refused, not today's.
    version = args.version
    when = date.today().isoformat() if args.date is None else args.date
    said: list[str] = []
    if parse_version(version) is None:
        said.append(f"{version} is not a semantic version, which is three "
                    f"numbers with no v and no leading zero, such as 0.2.0, "
                    f"and may end in a pre-release, such as 0.2.0-rc.1.")
    package, problem = _read(root, PACKAGE)
    current = None
    if problem:
        said.append(problem)
    else:
        current, problem = current_version(package)
        if problem:
            said.append(problem)
        elif (parse_version(version) is not None
              and not newer(version, current)):
            said.append(f"{version} is not newer than {current}, the version "
                        f"{PACKAGE} says.")
    if not DATE.fullmatch(when) or _no_date(when):
        said.append(f"{when.strip() or 'An empty --date'} is not a real "
                    f"date, written YYYY-MM-DD.")
    text, problem = _read(root, CHANGELOG)
    if problem:
        said.append(problem)
    else:
        lines = text.splitlines(keepends=True)
        index = _index(lines)
        section, ended, moved = gathered(lines, index, version, current)
        taken = in_words(f"[{each.name}]" for each in ended)
        if section is None:
            said.append(f"{CHANGELOG} has no ## [Unreleased] section, whose "
                        f"changes would be the release's.")
        elif not holds_something(moved):
            said.append(f"{CHANGELOG}'s [Unreleased] section"
                        + (f" and {taken} hold" if ended else " holds")
                        + f" nothing, so {version} would be a release of no "
                          f"changes.")
        if any(each.name == version for each in index):
            said.append(f"{CHANGELOG} already has a [{version}] section.")
    if said:
        return _say_errors(said, "Nothing was written.")

    newline = "\r\n" if "\r\n" in text else "\n"
    # [Unreleased] is taken out whole, heading and all, and written again
    # empty above the new section. The pre-releases' sections stay: each is
    # a build that was published, and a re-run of its tag reads its notes.
    before = "".join(lines[:section.heading])
    after = "".join(lines[section.end:])
    gathering = (f", with the changes of {taken}" if ended else "")
    changes = {
        PACKAGE: (VERSION_LINE.sub(lambda m: _replaced(m, 2, version),
                                   package), 1, f"version, now {version}",
                  package),
        CHANGELOG: (before
                    + f"## [{UNRELEASED}]{newline}{newline}"
                    + f"## [{version}] — {when}{newline}{newline}"
                    + moved.replace("\n", newline)
                    + (newline + after if after else ""), 1,
                    f"section, [Unreleased] now [{version}]{gathering}", text),
    }

    def example(match: re.Match) -> str:
        """An example of the current version, rewritten; any other, kept."""
        if match.group(1) != current:
            return match.group(0)
        found.append(match)
        rewritten = _replaced(match, 1, version)
        # What follows it may go on with the new version where it did not
        # with the old: `0.1.0.1` read as 0.1.0, and rewritten for
        # 0.2.0-rc.1 it reads as 0.2.0-rc.1.1, which the suite then finds.
        again = REPORT_LINE.match(rewritten + match.string[match.end():])
        if again.group(1) != version:
            line = match.string.count("\n", 0, match.start()) + 1
            word = match.group(0) + re.match(
                r"\S*", match.string[match.end():]).group(0)
            said.append(f"{name}:{line} quotes `{word}`, which "
                        f"rewritten would read as {again.group(1)}, not "
                        f"{version}: set what follows the version apart "
                        f"from it there, with a space.")
        return rewritten

    for path in _scanned(root):
        name = path.relative_to(root).as_posix()
        # The package's examples are rewritten in its bumped text, and the
        # text it had is the one put back. Measured before: it was skipped
        # as already changed, so an example in it kept the old version.
        bumped = changes.get(name)
        if bumped:
            text, old = bumped[0], bumped[3]
        else:
            try:
                old = text = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        found: list[re.Match] = []
        new = REPORT_LINE.sub(example, text)
        if found:
            examples = ("example" + "s" * (len(found) != 1)
                        + " of the version line")
            if bumped:
                changes[name] = (new, bumped[1], f"{bumped[2]}, and "
                                 f"{len(found)} {examples}", old)
            else:
                changes[name] = (new, len(found), examples, old)
    if said:
        return _say_errors(said, "Nothing was written.")
    written: list[str] = []
    for name, (new, _, _, _) in changes.items():
        try:
            _write(root / name, new.encode("utf-8"))
        except OSError as exc:
            return _put_back(root, changes, written, [
                f"{name} cannot be written: {exc.strerror or exc}."])
        written.append(name)
    _say_prepared(version, when, changes)
    return 0


def _put_back(root: Path, changes: dict, written: list[str],
              said: list[str]) -> int:
    """Each of *written* as it was before, for a prepare that failed part
    way, and what is left as it was not.

    Every file's text was read before any was written, so the tool puts each
    back itself. git checkout, which the message once offered, would also
    throw away whatever was not yet committed, such as the [Unreleased]
    section being released.
    """
    stuck = []
    for name in reversed(written):
        try:
            _write(root / name, changes[name][3].encode("utf-8"))
        except OSError as exc:
            stuck.append(name)
            said.append(f"{name} cannot be put back: {exc.strerror or exc}.")
    if not written:
        ending = "Nothing was written."
    elif not stuck:
        ending = ("The files written before it were put back, so nothing "
                  "is changed.")
    else:
        ending = (f"{in_words(stuck)} still hold{'s' * (len(stuck) == 1)} "
                  f"the new version; every other file is as it was.")
    return _say_errors(said, ending, keep=tuple(stuck))


def _replaced(match: re.Match, group: int, text: str) -> str:
    """*match*'s text with its *group* replaced by *text*, the rest kept."""
    start, end = match.span(group)
    whole = match.group(0)
    return (whole[:start - match.start()] + text
            + whole[end - match.start():])


def _no_date(when: str) -> bool:
    try:
        date.fromisoformat(when)
    except ValueError:
        return True
    return False


def _write(path: Path, data: bytes) -> None:
    """*data* as the whole of *path*, or *path* left as it was.

    Written to a file beside it, then moved over it, so that a write the disk
    takes only part of, full or over a quota, leaves the old file whole.
    Measured before: the file was written over in place, which empties it
    first, so such a write left README.md cut short at the limit, and putting
    it back, the same size again, failed the same way. The new file keeps
    the old one's permissions, a link is followed to the file it names, and
    a file that may not be written is refused as before, rather than
    replaced because its folder may be.
    """
    target = Path(os.path.realpath(path))
    if not os.access(target, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES),
                              str(path))
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.",
                                         suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        shutil.copymode(target, temporary)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _scanned(root: Path) -> Iterator[Path]:
    """Every file the suite's version test reads, the changelog excepted.

    In a git work tree, the files git keeps and the ones it would add;
    elsewhere, as in an unzipped release, every file. Measured before: the
    whole folder was walked, so the config.proposed.toml that ``build.py
    --discover`` writes, and .gitignore leaves out, had its header rewritten
    to a version that never wrote it, and was listed among the files the
    release changed, which ``git commit -am`` does not commit.
    tests/test_project_complete.py's version_scan reads the same files, and a
    test holds the two equal.
    """
    listed = _run(["git", "ls-files", "-z", "--cached", "--others",
                   "--exclude-standard"], root)
    if listed.code == 0:
        paths = [root / name for name in listed.out.split("\0") if name]
    else:
        paths = [Path(folder) / name for folder, _, files in os.walk(root)
                 for name in files]
    for path in sorted(paths):
        if (path.suffix in SHIPPED_SUFFIXES and path.name != CHANGELOG
                and not set(path.relative_to(root).parts) & NOT_SHIPPED
                and path.is_file()):
            yield path


def _say_prepared(version: str, when: str, changes: dict) -> None:
    tag = f"v{version}"
    width = max(len(name) for name in changes) + 2
    print(f"Prepared {version}, dated {when}, in {len(changes)} files:")
    for name, (_, count, what, _) in changes.items():
        # Under its own column when it wraps, as doctor's table does.
        print(fill(f"{count} {what}", " " * (width + 2),
                   first=f"  {name:<{width}}"))
    print()
    print("What is left, in order:")
    notes_command = "python3 tools/release.py notes"
    suite = "python3 -m unittest discover -s tests -t tests"
    screenshots = "python3 tools/screenshots.py"
    check_command = f"python3 tools/release.py check {tag}"
    steps = (
        f"Read the new [{version}] section of {CHANGELOG}: it is the "
        f"release's notes, and {notes_command} prints them as GitHub will "
        f"show them.",
        f"Run the whole suite: {suite}",
        f"If anything visible changed, regenerate the screenshots: "
        f"{screenshots}",
        f'git commit -am "Release {version}"',
        f'git tag -a {tag} -m "{NAME} {version}"',
        "git push origin main",
        f"git push origin {tag}")
    # Each command whole on its line, as it is pasted.
    whole = (notes_command, suite, screenshots, check_command)
    for number, step in enumerate(steps, start=1):
        print(fill(step, "     ", first=f"  {number}. ", keep=whole))
    print()
    print(fill(f"Pushing the tag is what makes the release: "
               f".github/workflows/release.yml checks it, runs the suite on "
               f"every supported Python, and publishes it. {check_command} "
               f"says now whether the first of those would pass.", "",
               keep=whole))


def check(args, root: Path) -> int:
    said, version, when, keep = problems(root, args.tag)
    tag = args.tag or (f"v{version}" if version else "the tag")
    if said:
        count = f"{len(said)} problem{'s' if len(said) != 1 else ''}"
        return _say_errors(said, f"{count}, so {tag} is not ready to "
                                 f"release.", keep=keep)
    print(f"{tag} is ready: it would release {version}, dated {when}.")
    return 0


def notes_text(root: Path, version: str | None, *, archive: Path | None = None,
               run_url: str | None = None) -> tuple[str | None, str | None]:
    """A release's notes and the problem making them, of which one is None.

    The changelog's section, unwrapped, and after a rule the archive's
    SHA-256 and the run that tested it, each when given.
    """
    if version is None:
        package, problem = _read(root, PACKAGE)
        if problem:
            return None, problem
        version, problem = current_version(package)
        if problem:
            return None, problem
    text, problem = _read(root, CHANGELOG)
    if problem:
        return None, problem
    bodies = [body for name, _, body in sections(text) if name == version]
    if not bodies:
        return None, f"{CHANGELOG} has no [{version}] section."
    notes = unwrap(_trimmed(bodies[0]))
    closing = []
    if archive is not None:
        try:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        except OSError as exc:
            return None, f"{archive} cannot be read: {exc.strerror or exc}."
        closing.append(f"SHA-256 of `{archive.name}`: `{digest}`")
    if run_url:
        closing.append(f"This release was tested by the workflow run at "
                       f"{run_url}.")
    if closing:
        notes += "\n---\n\n" + "\n\n".join(closing) + "\n"
    return notes, None


def notes(args, root: Path) -> int:
    archive = Path(args.archive) if args.archive else None
    text, problem = notes_text(root, args.version, archive=archive,
                               run_url=args.run_url)
    if problem:
        return _say_errors([problem], "No notes were printed.",
                           keep=(args.archive,) if args.archive else ())
    sys.stdout.write(text)
    return 0


def run_url() -> str | None:
    """The workflow run this is, when all three variables naming it are set."""
    parts = [os.environ.get(name) for name in
             ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")]
    if not all(parts):
        return None
    server, repository, run = parts
    return f"{server.rstrip('/')}/{repository}/actions/runs/{run}"


def publish(args, root: Path) -> int:
    """The archive, the notes, and the release, or nothing at all."""
    tag = args.tag
    said, version, _, keep = problems(root, tag)
    commit = head = None
    if version is not None:
        commit = _run(["git", "rev-parse", "--verify", "--quiet",
                       f"{tag}^{{commit}}"], root)
        if commit.code == 127:
            said.append(f"git cannot be run: {commit.err.strip()}.")
        elif commit.code:
            said.append(f"There is no tag {tag} in this repository.")
        else:
            head = _run(["git", "rev-parse", "HEAD"], root)
            if head.out.strip() != commit.out.strip():
                said.append(f"{tag} is {commit.out.strip()[:12]}, which is "
                            f"not the commit checked out "
                            f"({head.out.strip()[:12] or 'none'}): check out "
                            f"{tag} and run this again.")
            changed = _run(["git", "diff", "--name-only", tag, "--",
                            CHANGELOG, PACKAGE], root).out.split()
            if changed:
                said.append(f"{in_words(changed)} changed since {tag} "
                            f"was made, and the notes and the version are "
                            f"read from the files checked out: commit and "
                            f"tag again, or put them back.")
            if not args.dry_run:
                said += _moved(tag, commit.out.strip(), root)
                keep = (*keep, *_commands(tag))
    # The repository gh releases to is the one GH_REPO names, as the
    # workflow sets it to the repository whose origin was asked. Measured
    # before: run by hand without it, gh chose for itself, which in a
    # fork's clone is the parent, not the origin whose tag was checked.
    repository = os.environ.get("GH_REPO", "").strip()
    if not args.dry_run and not repository:
        said.append("GH_REPO is not set, so gh would choose the repository "
                    "to release to itself, which in a fork's clone is the "
                    "parent and not origin: set it to the repository's "
                    "OWNER/REPO, as the release workflow does.")
    if said:
        return _say_errors(said, "Nothing was published.", keep=keep)

    # A folder given is read from where the command is run, as build.py's
    # paths and --archive are; the default is the project's own dist/.
    out = root / DEFAULT_OUT if args.out is None else Path(args.out).resolve()
    archive = out / f"{NAME}-{version}.zip"
    notes_file = out / "notes.md"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _say_errors([f"{out} cannot be made: {exc.strerror or exc}."],
                           "Nothing was published.", keep=(str(out),))
    made = _run(["git", "archive", "--format=zip", f"--prefix={NAME}-"
                 f"{version}/", "-o", str(archive), tag], root)
    if made.code:
        return _say_errors([f"git archive exited {made.code}: "
                            f"{made.err.strip()}"], "Nothing was published.")
    text, problem = notes_text(root, version, archive=archive,
                               run_url=run_url())
    if problem:
        return _say_errors([problem], "Nothing was published.")
    notes_file.write_bytes(text.encode("utf-8"))

    shown_zip, shown_notes = _shown(archive, root), _shown(notes_file, root)
    title = f"{NAME} {version}"
    pre = is_pre_release(version)
    view = ["gh", "release", "view", tag]
    create = ["gh", "release", "create", tag, shown_zip, "--verify-tag",
              "--title", title, "--notes-file", shown_notes,
              *(["--prerelease"] if pre else [])]
    upload = ["gh", "release", "upload", tag, shown_zip, "--clobber"]
    # A release found may be a draft: GitHub makes one of a release whose
    # tag is deleted, and gh leaves one when it stops while uploading. gh
    # edit changes only what it is given, so the draft is published by
    # saying so, which is nothing to a release published already.
    edit = ["gh", "release", "edit", tag, "--title", title, "--notes-file",
            shown_notes, f"--prerelease={'true' if pre else 'false'}",
            "--draft=false"]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    here_zip, here_notes = _here(archive), _here(notes_file)
    # Flushed as they go, so that a run stopped part way still shows them.
    print(fill(f"Wrote {here_zip} and {here_notes}.", "",
               keep=(here_zip, here_notes)), flush=True)
    print(f"  SHA-256 {digest}", flush=True)
    if args.dry_run:
        # gh's paths are the project folder's, which the Wrote line's are
        # not when it is run from elsewhere, so the folder is named then.
        try:
            where = "" if Path.cwd() == root.resolve() else f", in {root}"
        except OSError:                  # the folder run from is gone
            where = f", in {root}"
        named = (f" in {repository}, as GH_REPO says," if repository
                 else ",")
        unset = ("" if repository else " GH_REPO is not set, and publish "
                 "runs none of these until it names the repository to "
                 "release to.")
        for line in (f"A dry run runs no gh.{unset} To ask whether the "
                     f"release is there{named} publish would run{where}:",
                     shlex.join(view),
                     "If it is not, to make it:", shlex.join(create),
                     "If it is, to replace its archive and its notes:",
                     shlex.join(upload), shlex.join(edit)):
            print(line if line.startswith("gh ")
                  else fill(line, "", keep=(str(root),)))
        return 0
    there = _run(view, root)
    if there.code == 127:
        return _say_errors([f"gh cannot be run: {there.err.strip()}."],
                           f"Nothing was published; {here_zip} and "
                           f"{here_notes} are written.",
                           keep=(here_zip, here_notes))
    # A view that fails is taken for a release not there, whatever gh said,
    # rather than reading its English; when the create it leads to fails,
    # what the view said goes with it, as it may be the cause: a 502 on
    # view, then "already exists" on create.
    for argv in ((create,) if there.code else (upload, edit)):
        done = _run(argv, root)
        if done.code:
            said = [f"gh release {argv[2]} exited {done.code}: "
                    f"{done.err.strip() or 'it said nothing'}"]
            if there.code:
                said.append(f"gh release view exited {there.code}: "
                            f"{there.err.strip() or 'it said nothing'}")
            return _say_errors(
                said,
                f"The release of {tag} is not made or not finished; once "
                f"the cause is fixed, the same command finishes it.")
        if done.out.strip():
            print(done.out.strip(), flush=True)
    print(f"{'Published' if there.code else 'Updated'} the release of {tag}"
          f"{', a pre-release' if pre else ''}.")
    return 0


def _commands(tag: str) -> tuple[str, ...]:
    """The git commands _moved may name, each to be kept whole on its line."""
    return (f"git push origin {tag}", f"git push -f origin {tag}",
            f"git fetch -f origin tag {tag}")


def _moved(tag: str, commit: str, root: Path) -> list[str]:
    """Why origin's *tag* is not *commit*, the commit here, if it is not;
    nothing when there is no origin to ask.

    A run's checkout makes its tag the commit the run began at, so only
    origin can say that the tag has been deleted and pushed again since, to
    another commit, or that this is a re-run of a run whose tag has moved
    on. Measured before this: either published the old commit's archive and
    notes under the tag that names the new one, and went green.

    Run by hand, which GITHUB_ACTIONS not being ``true`` says, there is no
    run, and it is the tag here that is new: not pushed yet, or moved to a
    fix while origin's was not. Measured before: both were said as a run's,
    the second the wrong way round.
    """
    if _run(["git", "remote", "get-url", "origin"], root).code:
        return []
    asked = _run(["git", "ls-remote", "origin", f"refs/tags/{tag}",
                  f"refs/tags/{tag}^{{}}"], root)
    if asked.code:
        return [f"git ls-remote origin exited {asked.code}: "
                f"{asked.err.strip() or 'it said nothing'}, so whether {tag} "
                f"is still this run's commit cannot be known."]
    refs = dict(reversed(line.split("\t", 1))
                for line in asked.out.splitlines() if "\t" in line)
    # An annotated tag's commit is the line git peels it to.
    there = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    in_a_run = os.environ.get("GITHUB_ACTIONS") == "true"
    push, force, fetch = _commands(tag)
    if there is None and in_a_run:
        return [f"origin has no tag {tag}: it was deleted since this run "
                f"began, and a run of it pushed again publishes it."]
    if there is None:
        return [f"origin has no tag {tag}: push it, with {push}, and the "
                f"release workflow that starts publishes it."]
    if there != commit and in_a_run:
        return [f"{tag} has moved to {there[:12]} since this run began at "
                f"{commit[:12]}: the run of the tag as it is now publishes "
                f"it."]
    if there != commit:
        return [f"origin's {tag} is {there[:12]}, not {commit[:12]}, the "
                f"commit here: if this is the commit to release, move "
                f"origin's tag to it, with {force}, and the release workflow "
                f"that starts publishes it; if origin's is, fetch it, with "
                f"{fetch}."]
    return []


def _here(path: Path) -> str:
    """*path* as the person running the command reads it: from where they
    are when it is under it, otherwise whole."""
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except (ValueError, OSError):
        return str(path)


def _shown(path: Path, root: Path) -> str:
    """*path* as gh is given it: from the project folder when it is in it."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# --- the command line -------------------------------------------------------

COMMANDS = {"prepare": prepare, "check": check, "notes": notes,
            "publish": publish}


def _parser() -> Parser:
    ap = Parser(prog="tools/release.py", description=__doc__)
    ap.add_argument("--version", action="version", version=version_line(),
                    help="print this build and the Python running it, then "
                         "stop")
    # The subparsers are made by the same class, cli.Parser, which argparse
    # takes from the parser they belong to.
    commands = ap.add_subparsers(dest="command", metavar="COMMAND",
                                 required=True)
    each = {
        "prepare": commands.add_parser(
            "prepare", help="write a new version, and move the changelog's "
                            "[Unreleased] under it",
            description="Write VERSION into matchcentre/__init__.py, move "
                        "CHANGELOG.md's [Unreleased]\nsection under a heading "
                        "of it, and rewrite every example of the version\n"
                        "line. Commits nothing; prints what is left."),
        "check": commands.add_parser(
            "check", help="say whether a tag is ready to release",
            description="Say whether TAG is ready to release, giving every "
                        "problem at once.\nExits 1 if it is not."),
        "notes": commands.add_parser(
            "notes", help="print a release's notes, as GitHub shows them",
            description="Print CHANGELOG.md's section for VERSION, each "
                        "wrapped paragraph on one line."),
        "publish": commands.add_parser(
            "publish", help="zip the tagged tree and make the GitHub release",
            description="Zip TAG with git archive, write its notes beside "
                        "it, and make or update\nits GitHub release with gh. "
                        "Run by .github/workflows/release.yml."),
    }
    each["prepare"].add_argument("version", metavar="VERSION",
                                 help="the new version, such as 0.2.0")
    each["prepare"].add_argument("--date", metavar="YYYY-MM-DD",
                                 help="the release's date (default today)")
    each["check"].add_argument("tag", metavar="TAG", nargs="?",
                               help="the tag (default v and the version in "
                                    "the code)")
    each["notes"].add_argument("version", metavar="VERSION", nargs="?",
                               help="the version (default the one in the "
                                    "code)")
    each["notes"].add_argument("--archive", metavar="FILE",
                               help="end with this file's SHA-256")
    each["notes"].add_argument("--run-url", metavar="URL",
                               help="end by naming the run that tested it")
    each["publish"].add_argument("tag", metavar="TAG",
                                 help="the tag to release, such as v0.2.0")
    each["publish"].add_argument("--out", metavar="DIR",
                                 help="where to write the archive and the "
                                      f"notes (default {DEFAULT_OUT} in the "
                                      "project folder)")
    each["publish"].add_argument("--dry-run", action="store_true",
                                 help="write them and print each gh command, "
                                      "running none")
    for parser in each.values():
        parser.add_argument("--version", action="version",
                            version=version_line(),
                            help="print this build and the Python running "
                                 "it, then stop")
    return ap


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    """Run one command against *root*, the project folder.

    *root* is a parameter so that the tests can run this against a folder of
    their own; the workflow runs it in the checkout.
    """
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:            # --help, --version, or a refused one
        return exc.code if isinstance(exc.code, int) else 2
    return COMMANDS[args.command](args, Path(root))


if __name__ == "__main__":
    raise SystemExit(main())
