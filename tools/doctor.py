#!/usr/bin/env python3
"""Explain what a PDF is, and why it did or did not read as a scorecard.

    python3 tools/doctor.py data/round-3/card.pdf
    python3 tools/doctor.py --page 2 "data/Rules 2026.pdf"
    python3 tools/doctor.py --redact data/round-3/card.pdf   # safe to paste

This is what a club runs when ``build.py`` refuses a card, so it has to be
genuinely diagnostic rather than a second opinion. It reports the file's own
metadata (producer, page count, page size, rotation, fonts and whether each font
has an encoding text can be recovered through), then the structure the page
actually contains (the rectangle census, the set table's column rules and row
bands, the checkbox census with tick state), then which header fields a tolerant
pattern can find, then what :mod:`~matchcentre.scorecard` itself makes of the
page, and finally a plain-language verdict.

**``--redact`` is for the report you send somebody else.** The full report names
every player on the card, both ratings for each of them, who the captain is, both
team names, the club on the letterhead and the path the file sits at -- and the
report ends by inviting whoever ran it to quote it if they want help. Pasted into
a public issue tracker, that publishes a club's membership. With ``--redact``, no
text the page carries is printed unless it is one of the Match Centre template's
own words; everything else is replaced by its shape, so ``Priya Halloran``
arrives as ``<2 words, 15 chars>``. The shape is what diagnoses a parsing fault
anyway: two names read as one cell shows up in the character count.

What ``--redact`` deliberately keeps, because none of it names a person and all of
it is the diagnosis: every measurement, every count, every score, the result verb,
the box each tick is read as, by its label, row and side, the grade, round and
court numbers, the weekday, and the producer line out of the file's metadata.
What it removes: names, teams, the club, the venue, the season label, dates,
times, the Match Centre match id, ratings, and the file's own name and path.

A redacted run ends by saying the other half of that out loud: if a name survived
it, the fault is in this flag rather than in the card, and ``SECURITY.md`` has a
private route for saying so. Pasting the line that leaked into a public issue is
the one thing in this project that cannot be undone.

Two deliberate choices about how it reads a page:

*The patterns here are looser than the reader's.* That is the point. "The doctor
found a date and a division but the reader refused the page" localises the
problem to the reader's stricter test, which is far more useful than two modules
agreeing.

*The geometry here is derived, never positional.* Column rules are found by shape
(much taller than wide), row bands by where those rules start and stop, and the
checkbox side by measuring the hairlines rather than by knowing that a real card's
boxes are 8.07pt. A doctor that knew the template's pixel positions would report
"looks fine" on exactly the offset card that F5 exists to refuse.

Exit status is 0 when the page read as a scorecard and 1 when it did not, so this
can be used in a loop over a directory.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from matchcentre import discover, pdf, redact, scorecard, version_line  # noqa: E402
from matchcentre.card import (Card, CardError, CrossCheck, verb_side,  # noqa: E402
                              weekday_of)
from matchcentre.cli import WIDTH, Parser, fill                 # noqa: E402
from matchcentre.config import DEFAULT_PATH, Config             # noqa: E402
from matchcentre.format import SET_RULES, from_rows             # noqa: E402
from matchcentre.model import forfeit_match_winner              # noqa: E402
from matchcentre.scope import in_words                          # noqa: E402
from matchcentre.scoring import RULESETS                        # noqa: E402
from matchcentre import validate                                 # noqa: E402

# Beyond this many pages the report is longer than anyone reads. A scorecard has
# one page; the club's rules and draw PDFs are the long ones, and for those the
# first few pages answer the question.
PAGE_LIMIT = 20

# Common paper sizes, in points, for naming the page rather than only measuring
# it. A card that is not A4 was probably printed "fit to page" from a browser,
# which rescales every coordinate on it.
PAPERS = {
    (595, 842): "A4 portrait",
    (842, 595): "A4 landscape",
    (612, 792): "US Letter portrait",
    (792, 612): "US Letter landscape",
    (595, 421): "A5 landscape",
    (420, 595): "A5 portrait",
}

# Encodings a simple font can be decoded through when it carries no /ToUnicode.
KNOWN_ENCODINGS = ("WinAnsiEncoding", "MacRomanEncoding", "MacExpertEncoding",
                   "StandardEncoding", "PDFDocEncoding")

# A date in any shape a card could print one, the whole of it in group 1: figures
# day or month first or year first with any of / . - between, and a month in words
# either side of the day, with or without an ordinal or a comma. Looser than the
# reader, which reads the forms card.DATE_FORMS names and no others, so that "the
# doctor found a date and the reader did not" can be said at all. One pattern for
# the header field below and for the redaction of a date, because a date this
# report can quote and --redact cannot see is a date published: the reader's own
# warnings quote a date it could not use, and ``08-18-2026`` went out whole. And a
# range of days printed in place of a date with no year, ``29/09 - Sun 04/10``,
# which the reader dates by its competition line's year; one with a year at its
# close is found as that end, as it was.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_SHAPE = (
    r"(?i)(?<![\d.])("
    r"\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"
    r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
    r"|\d{1,2}(?:st|nd|rd|th)?[\s-]+(?:of\s+)?" + _MONTH + r"[\s,-]+\d{2,4}"
    r"|" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{2,4}"
    r"|\d{1,2}/\d{1,2}\s*[-\u2013]\s*[^\W\d_]+\.?,?\s+\d{1,2}/\d{1,2}(?!/)"
    r")(?!\d|\.\d)")

# Header fields, each with a pattern deliberately more tolerant than the reader's.
# The division and the round are looked for with the reader's own rule first
# (READER_RULES, below); these two patterns only add to what it finds.
HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("date", DATE_SHAPE),
    ("time", r"\b(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)"),
    ("division", r"\b(?:Division|Grade|Section)\s+([A-Za-z0-9][-A-Za-z0-9]*)"),
    ("round", r"\bRound\s+([A-Za-z0-9][-A-Za-z0-9]*)"),
    ("match id", r"#\s*(\d{3,})"),
    # No pattern: the court is the one the reader reads, off the venue's line, and
    # not a "Court 3" in the comments (PAGE_RULES, 4.466).
    ("court", ""),
    # No pattern: a verb is one the one table reads (READER_RULES, below).
    ("result verb", ""),
    ("games header", r"\b(Games)\b"),
    ("set label", r"\b(SET)\b"),
    ("totals row", r"\b(TOTAL)\b"),
    ("match winner", r"\b(Match Winner)\b"),
    ("match forfeited", r"\b(Match Forfeited)\b"),
    ("per-set flags", r"\b(Forfeit|Retired|Incomplete)\b"),
    ("roster block", r"\b(Players)\b"),
    ("ratings", r"\b(Rating)\b"),
    ("signatures", r"\b(Signed by)"),
    # No pattern: a heading is one the reader ends the roster block at (PAGE_RULES).
    ("fill-ins", ""),
)

# Discipline markers in a row's outer marker column: `1&2` is a doubles pairing,
# a bare `1` is singles. This is what makes the match format readable off a card.
MARKER_RE = re.compile(r"^\d(?:\s*&\s*\d)?$")

INDENT = "    "
# One label column for the whole report, so the values line up whether or not the
# output is being read in a terminal. Plain ASCII throughout: this gets pasted into
# emails and issue trackers.
LABEL = 14

# The last thing a `--redact` run prints. The person reading it is the only one who
# can tell a shape from a name -- they know the names -- and this is the moment they
# are doing it: report finished, terminal scrolled to the bottom, about to select it
# and paste it somewhere public. Every other place that says "read it before you
# paste it" is read before the report exists, if at all.
#
# Printed after the scrub rather than inside the buffer it scrubs, for the reason
# every other line here is printed inside it: a card whose team is called Security
# would have this footer shaped along with the team name, and the one line that has
# to survive a hostile card is the line saying what to do about one.
REDACTION_FOOTER = (
    "Read this before you paste it. Every name, team, club, venue, date, rating",
    "and path above should be a shape like <2 words, 15 chars>. If one of them is",
    "still a name, do not post it: that is a fault in --redact itself, and",
    "SECURITY.md has a private route for reporting exactly that.",
)

# --- redaction ---------------------------------------------------------------

# The Match Centre template's own vocabulary: the words a card prints whoever
# played on it. ``--redact`` keeps these and shapes every other piece of text the
# page carries. Two consequences, both deliberate:
#
# * somebody actually named one of these words is not redacted. The list is short
#   and every entry is label text, so the hole is narrow.
# * a card carrying a word this report uses as a label -- a team called "Rules",
#   say -- has it shaped everywhere, including in the report's own headings. Ugly,
#   and safe, and that is the right way round for the two to fail.
#
# `Unspecified` and `Player` are in here on purpose. Match Centre's
# `Unspecified player` placeholder is one of the commonest things a club needs
# help with, and a report that hid the placeholder would hide the question.
TEMPLATE_WORDS = frozenset(word.casefold() for word in (
    "SET", "SETS", "TOTAL", "TOTALS", "Game", "Games", "Rating", "Ratings",
    "Player", "Players", "Unspecified", "Forfeit", "Retired", "Incomplete",
    "Match", "Winner", "Forfeited", "Cancelled", "Disputed", "Signed", "Sign",
    "Fill", "ins", "Court", "Round", "Division", "Grade", "Section", "Date",
    "Time", "Venue", "Season", "Captain", "Name", "Result", "Points", "No",
    "Home", "Away", "Singles", "Doubles", "Rubber", "Team", "Teams",
    # Hyphenated, because a token is tested whole: with only "Fill" and "ins" in
    # here the template's own "Fill-ins" heading was reported as an 8-character
    # secret, which reads as though the card had something to hide there.
    "Fill-in", "Fill-ins",
    # The captain mark, which the card prints beside a roster name. The name it
    # sits beside is shaped, so keeping the mark says a captain was read without
    # saying who: `<2 words, 19 chars> (C)`. It was coming out as `<3 chars>`.
    "(C)",
    # Weekday names. A card's weekday is what routes it to a competition -- this
    # tool's commonest real question is "why did my Tuesday card land in the
    # Wednesday division" -- and no weekday names a person. The date itself is
    # withheld by _IDENTIFYING regardless.
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "DEFEATS", "LOSES", "DRAWS", "WITH", "TO", "VS",
    "v", "vs", "by", "and", "of", "the", "at",
))

# Values that identify a fixture rather than describe a shape, matched by their
# form. They catch what the word pass cannot: a value this report *derives* is no
# longer any word on the page, so an ISO date and a match id captured out of
# "#4200000" are invisible to a word-by-word comparison.
#
# These run *before* the word pass, on untouched text, because a value made of
# more than one token is only recognisable while it is still whole. Measured: with
# the word pass first, "7:00 PM" had its PM shaped as a two-character secret,
# after which the time pattern no longer matched and the report published the
# start time as "7:00".
_IDENTIFYING: tuple[tuple[re.Pattern[str], object], ...] = (
    (re.compile(DATE_SHAPE), lambda m: "<date>"),
    # The am/pm is optional so that a 24-hour card is covered too. No structural
    # line in this report prints a colon between two numbers, so nothing
    # measured can be swallowed by this.
    (re.compile(r"\b\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]\.?)?"), lambda m: "<time>"),
    # Guarded against a decimal point on either side so that a coordinate, a
    # rectangle dimension and a column position all survive intact -- those are
    # the measurements the whole report is for.
    (re.compile(r"(?<![\d.])\d{5,}(?![\d.])"),
     lambda m: f"<{len(m.group())} digits>"),
)

# A roster entry's printed rating pair, `[2.34/4.23]`. Shaped rather than dropped:
# whether one rating, two or none were read is diagnostic, and the numbers
# themselves are not.
_RATING_RE = re.compile(r"\[(-|\d+(?:\.\d+)?)/(-|\d+(?:\.\d+)?)\]")


def _rating_shape(match: re.Match[str]) -> str:
    return "[" + "/".join("-" if side == "-" else "x.xx"
                          for side in match.groups()) + "]"


class _Redactor:
    """Everything the page said, so that none of it can be printed.

    Built the other way round from the obvious design. Rather than each print
    site deciding whether its value is safe -- which fails the moment a new line
    is added, or when :func:`matchcentre.scorecard.describe` grows a field this
    file does not know about -- the report is written normally into a buffer and
    then scrubbed against what the page turned out to contain. One mechanism
    therefore covers the header captures, the reader's own summary, the roster
    lines, the verdict, the card's warnings *and* the text quoted inside a
    refusal's detail, including whatever any of them prints next year.

    The secret set is gathered two ways at once, so an unreadable page -- the
    case this whole tool exists for, and the case where a field-by-field
    redactor has nothing to work from -- is covered as well as a clean one:

    * every word the page draws that is not in :data:`TEMPLATE_WORDS`. This
      needs nothing to parse.
    * the full multi-word values off the parsed card, when it parses, so that
      ``Priya Halloran`` is shaped as one name rather than as two words.

    Single characters are never secrets: shaping every "J" on the page would
    take the report's own punctuation with it. An initial standing alone
    therefore survives, which is the one thing here that is a compromise rather
    than a decision.

    What is printed inside :meth:`verbatim` is the one exception, published as
    it was written: the causes of a refusal, which are the tool's advice in its
    own words, and which a page printing "draw" or "it" had shaped out of it,
    the rotation and images rows they quote, and the reason for the refusal,
    which the reader words without quoting the page.
    """

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._kept: list[tuple[int, int]] = []      # spans of the report, verbatim
        self.shapes: set[str] = set()               # what the scrub printed instead

    # --- learning ---

    def learn_page(self, page: pdf.Page) -> None:
        for word in page.words:
            for token in word.text.split():
                self._add(token)
        if page.source:
            self._secrets.add(page.source)

    def learn_card(self, card: Card) -> None:
        for value in (card.club_hint, card.venue, card.season_hint,
                      card.home_name, card.away_name):
            self._add(value)
        for parsed in card.sets:
            for name in parsed.home_players + parsed.away_players:
                self._add(name)
        for entry in card.home_roster + card.away_roster:
            self._add(entry.name)
        # Added past _add's letter test rather than through it: both are pure
        # digits, and both name this fixture in a public database.
        for digits in (card.match_id, card.date):
            if len(digits) >= 3:
                self._secrets.add(digits)

    def learn_path(self, path: Path) -> None:
        """The file's own path, which the reader's summary prints as `source:`.

        Whole, as given and resolved, because it is printed whole there and in
        the reader's errors. Not its parts: a folder named ``pages`` would have
        that word shaped wherever the report says it.
        """
        self._secrets.add(str(path))
        self._secrets.add(str(Path(path).resolve()))

    def _add(self, text: str) -> None:
        # The rule the build withholds players' names by, so the two cannot drift.
        # Each also as repr quotes it, as the summary does a row's players: repr
        # escapes a soft hyphen or a zero-width space inside a name.
        for spelling in redact.candidates(text, TEMPLATE_WORDS):
            self._secrets.update((spelling, repr(spelling)[1:-1]))

    # --- using ---

    @contextlib.contextmanager
    def verbatim(self):
        """What the block prints is published as it was written, unscrubbed.

        So nothing read off the page may be printed inside it: not a word, a
        name or a number, nor the detail of what the reader said of the page,
        which can quote it.
        """
        start = sys.stdout.tell()
        yield
        self._kept.append((start, sys.stdout.tell()))

    def publish(self, report: str) -> str:
        """``report`` scrubbed, but for the spans printed inside :meth:`verbatim`."""
        out, at = [], 0
        for start, end in self._kept:
            out += [self.scrub(report[at:start]), report[start:end]]
            at = end
        return "".join(out) + self.scrub(report[at:])

    def scrub(self, text: str) -> str:
        for expression, replace in _IDENTIFYING:
            text = expression.sub(lambda match, replace=replace:
                                  self._held(replace(match)), text)
        text = _RATING_RE.sub(_rating_shape, text)
        # Case-sensitive, where the build ignores case: this report's own labels
        # sit beside the page's words (`club hint` beside `Club`), and a scrub that
        # ignored case would shape the report rather than the card.
        return redact.substitute(text, self._secrets,
                                 lambda secret: self._held(redact.shape(secret)),
                                 ignore_case=False)

    def _held(self, shape: str) -> str:
        """*shape*, to be held whole where its line is laid out."""
        self.shapes.add(shape)
        return shape


def _row(label: str, value: str, *, indent: str = "") -> None:
    _flow(indent, f"{label:<{LABEL}}{value}", indent + " " * LABEL)


# How a line is printed to be laid out to the width, by :func:`_laid_out` once
# the report is written: its opening, the indent it goes on at and its text,
# told apart by a noncharacter, which no line of the report prints.
_FLOW = "\ufdd0"
_FLOWED = re.compile(f"^( *){_FLOW}( *){_FLOW}(.*)$")


def _flow(opening: str, text: str, hang: str) -> None:
    """Print *text* after *opening*, to go on under *hang* past the width."""
    print(f"{opening}{_FLOW}{hang}{_FLOW}{text}")


def _laid_out(report: str, keep: tuple[str, ...] = ()) -> str:
    """*report* with every line past :data:`WIDTH` filled to it by :func:`fill`.

    A line :func:`_flow` printed goes on under its hang, and any other under its
    own indent; a line that fits is printed as it was. Laid out once the report
    is written, and under ``--redact`` once it is scrubbed, so that a line is
    measured as it is published and a name is never parted before its shape is
    known; each of *keep*, the path or a shape, is held whole (4.186). A word
    too long for any line, a path or a font's name, is printed whole on a line
    of its own: the report's words are what a club sends, and none is cut.
    """
    out = []
    for line in report.split("\n"):
        marked = _FLOWED.match(line)
        if marked:
            opening, hang, text = marked.groups()
        else:
            text = line.lstrip(" ")
            opening = hang = line[:len(line) - len(text)]
        out.append(opening + text if len(opening) + len(text) <= WIDTH
                   else fill(text, hang, first=opening, keep=keep))
    return "\n".join(out)


def _verbatim(redactor: _Redactor | None):
    """:meth:`_Redactor.verbatim` under ``--redact``, and nothing otherwise."""
    return redactor.verbatim() if redactor is not None else contextlib.nullcontext()


def main(argv: list[str] | None = None) -> int:
    ap = Parser(prog="tools/doctor.py", description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path, metavar="FILE",
                    help="the PDF to explain; repeatable")
    ap.add_argument("--page", type=int, default=None, metavar="N",
                    help="examine only this page, counting from 1")
    ap.add_argument("--redact", action="store_true",
                    help="replace every name, team, date and path with its "
                         "shape, so the report can be pasted into a public "
                         "issue without publishing who played")
    # Answered without a card, although `paths` is required: argparse acts on a
    # version action while parsing and exits before it asks what is missing. This
    # is the command a club is sent to when something is wrong, so it is the one
    # most likely to be asked which version it is.
    ap.add_argument("--version", action="version", version=version_line(),
                    help="print this build and the Python running it, then stop")
    args = ap.parse_args(argv)

    worst = 0
    try:
        for index, path in enumerate(args.paths):
            if index:
                print()
            worst = max(worst, _one_file(path, page=args.page,
                                        redact=args.redact, number=index + 1))
        if args.redact:
            # Once for the run and not once per file, so that the documented
            # `doctor.py --redact data/Round1/*.pdf` over a whole round ends with
            # one notice rather than with one per card, which is one to skim past.
            # Outside the per-file loop it also survives the case a leak is
            # likeliest in: a file that never parsed has no verdict block, and a
            # redactor that learned nothing off it has nothing to shape.
            print()
            for line in REDACTION_FOOTER:
                print(line)
        sys.stdout.flush()
    except BrokenPipeError:
        # `doctor.py card.pdf | head` closes the pipe while there is still report
        # left to write. Measured in this session: without this the failure lands on
        # the interpreter's own final flush instead, printing a traceback and
        # exiting 120 -- which, in the loop over a directory this tool is built for,
        # reads as the doctor having crashed on that card. Pointing the descriptor
        # at os.devnull is the documented way to make the rest of shutdown silent.
        # The status stays whatever the pages examined so far earned; the output was
        # truncated by the reader, not by a problem with the file.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return worst


def _surprise(exc: BaseException, doing: str, *, redacting: bool) -> None:
    """A failure the reader does not know how to refuse, said in words.

    ``scorecard.read`` already turns one of these into a rejection so that a bad byte
    cannot cost a build its season; this is the same guard for the tool a club is sent
    to when a card will not read. Without it, the one thing in the project that still
    printed a traceback was the diagnostic.

    Under ``--redact`` the message is withheld and only the type is printed. An
    exception raised inside the reader can quote bytes off the page, and the redactor
    knows only the values it managed to learn -- from a page that did not read, that
    may be none of them. Naming the flag that would show it keeps the report honest
    about what it is holding back.
    """
    print(f"{INDENT}The reader failed unexpectedly while {doing}.")
    if redacting:
        print(f"{INDENT}{type(exc).__name__}; the message is withheld, since it may "
              f"quote the page. Run again without --redact to read it.")
    else:
        print(f"{INDENT}{type(exc).__name__}: {exc}")
    print(f"{INDENT}That is a bug in this reader rather than a verdict on the file. "
          f"Please report it with the report above.")


def _one_file(path: Path, *, page: int | None, redact: bool = False,
              number: int = 1) -> int:
    """One file's report, redacted as a whole if that was asked for.

    Redaction cannot happen line by line as the report is written, because what
    has to be removed is only fully known once the page has been read -- so the
    report is written into a buffer, and scrubbed against what the page contained
    before any of it reaches the terminal. The exit status is decided by the
    report, not by the scrubbing, so ``--redact`` cannot change the verdict.
    """
    if not redact:
        return _report(path, page=page, label=str(path), redactor=None)

    redactor = _Redactor()
    redactor.learn_path(path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = _report(path, page=page, redactor=redactor,
                       label=f"#{number}  (name and path withheld by --redact)")
    print(_laid_out(redactor.publish(buffer.getvalue()),
                    keep=tuple(sorted(redactor.shapes))), end="")
    return code


def _report(path: Path, *, page: int | None, label: str,
            redactor: _Redactor | None) -> int:
    """The report on *path*, laid out to the width here unless it is to be
    scrubbed first, which :func:`_one_file` lays it out after."""
    if redactor is not None:
        return _written(path, page=page, label=label, redactor=redactor)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            return _written(path, page=page, label=label, redactor=None)
    finally:
        print(_laid_out(buffer.getvalue(), keep=(str(path),)), end="")


def _written(path: Path, *, page: int | None, label: str,
             redactor: _Redactor | None) -> int:
    print("=" * 72)
    _row("file", label)
    print("=" * 72)

    # Every branch below is a real thing a club types at the moment they already have
    # a problem, since build.py names this command beside every card it refused. One
    # of them used to be a traceback and two of them used to be the same wrong
    # sentence -- "There is no file at that path" is false about a directory, and
    # about a symlink whose target has gone. Each gets the answer it actually earns.
    if path.is_dir():
        print(f"{INDENT}That is a directory, not a file. This tool explains one "
              f"scorecard at a time; name the PDFs inside it instead, which a "
              f"shell will expand for you:")
        print(f"{INDENT}{INDENT}python3 tools/doctor.py data/Round1/*.pdf")
        return 1
    if path.is_symlink() and not path.exists():
        print(f"{INDENT}That is a symbolic link and there is nothing at the other "
              f"end of it. If the cards live on a share, it may not be mounted.")
        return 1
    if not path.exists():
        print(f"{INDENT}There is no file at that path.")
        return 1
    if not path.is_file():
        print(f"{INDENT}That path is not a regular file, so there is nothing to "
              f"read from it.")
        return 1

    try:
        raw = path.read_bytes()
    except OSError as exc:
        # The path exists and this process may not have it. Only the reason is
        # printed, never the errno's copy of the path, so this line is as safe under
        # --redact as the rest of the report.
        print(f"{INDENT}This file cannot be read: {exc.strerror or exc}.")
        print(f"{INDENT}A build cannot read it either, so it will be refused and "
              f"reported rather than scored.")
        return 1
    fonts_ok = _file_facts(path, raw)

    try:
        count = pdf.page_count(path)
    except pdf.PdfError as exc:
        # pdf.py's refusals already carry the fix, which is why they are quoted
        # rather than paraphrased.
        print()
        print("verdict")
        print(f"{INDENT}This file cannot be opened by the reader at all.")
        print(f"{INDENT}{exc}")
        return 1
    except OSError as exc:
        print()
        print("verdict")
        print(f"{INDENT}This file could not be read a second time: "
              f"{exc.strerror or exc}.")
        return 1
    except Exception as exc:                          # noqa: BLE001 - see below
        print()
        print("verdict")
        _surprise(exc, "counting the pages of this file",
                  redacting=redactor is not None)
        return 1

    print(f"pages         {count}")
    if page is not None:
        if not 1 <= page <= count:
            print(f"{INDENT}There is no page {page}; the file has {count}.")
            return 1
        wanted = [page - 1]
    else:
        wanted = list(range(min(count, PAGE_LIMIT)))
        if count > PAGE_LIMIT:
            print(f"{INDENT}examining the first {PAGE_LIMIT}; use --page N for "
                  f"one of the rest")

    worst = 0
    for index in wanted:
        print()
        worst = max(worst, _one_page(path, index, count, raw,
                                     fonts_ok=fonts_ok, redactor=redactor))
    return worst


# --- file-level metadata ----------------------------------------------------


def _file_facts(path: Path, raw: bytes) -> bool:
    """What the file says about itself. Returns whether every font can be decoded.

    Read straight out of the bytes rather than through :mod:`~matchcentre.pdf`,
    which exposes page content and not document metadata. The dictionaries this
    wants are uncompressed in every producer's output seen so far; when they are
    not, the field simply reports as unknown, which is honest and is also a
    finding in itself.

    The font verdict is returned rather than pronounced here because whether an
    undecodable font actually cost the page its text is only knowable once the page
    has been extracted and its words counted.
    """
    _row("size", f"{len(raw):,} bytes")
    header = raw[:16].split(b"\n", 1)[0].strip()
    _row("version", _ascii(header))

    creator = _text_field(raw, b"Creator")
    producer = _text_field(raw, b"Producer")
    both = " / ".join(p for p in (creator, producer) if p)
    _row("producer", both or "not declared")
    if "wkhtmltopdf" in both:
        print(f"{INDENT}Match Centre renders an HTML template through "
              f"wkhtmltopdf, so this is")
        print(f"{INDENT}a card exported from the site rather than a scan or a "
              f"re-print.")
    elif both:
        print(f"{INDENT}Not the producer Match Centre exports with "
              f"(wkhtmltopdf / Qt). A card")
        print(f"{INDENT}re-printed through another program may have had its "
              f"text and its")
        print(f"{INDENT}table rules rewritten or rasterised.")

    _row("encrypted", "yes" if _has(raw, rb"/Encrypt") else "no")
    objstm = _has(raw, rb"/Type\s*/ObjStm")
    xrefstm = _has(raw, rb"/Type\s*/XRef")
    classic = _has(raw, rb"(?<![a-zA-Z])xref[\r\n]")
    _row("obj streams", "yes" if objstm else "no")
    _row("xref", ("classic table" if classic else "none")
         + (", plus a stream" if xrefstm else ""))
    _row("form xobjects", "yes" if _has(raw, rb"/Subtype\s*/Form") else "no")

    fonts, descendants = _fonts(raw)
    if not fonts:
        _row("fonts", "none found")
        return True
    width = max(len(name) for name, _, _, _ in fonts)
    widest = max(len(encoding) for _, _, encoding, _ in fonts)
    for position, (name, subtype, encoding, usable) in enumerate(fonts):
        _row("fonts" if position == 0 else "",
             f"{name:<{width}}  {subtype:<9} {encoding:<{widest}} "
             f"{'has a decode table' if usable else 'no /ToUnicode, no /Encoding'}")
    if descendants:
        _row("", f"({descendants} CID descendant font object(s) folded into the "
                 f"above)")
    return all(usable for _, _, _, usable in fonts)


def _text_field(raw: bytes, key: bytes) -> str:
    """A string value out of the document information dictionary."""
    match = re.search(rb"/" + key + rb"\s*\((.*?)\)\s*(?:/|>>)", raw, re.S)
    if not match:
        return ""
    value = match.group(1)
    if value.startswith(b"\xfe\xff"):
        try:
            return value[2:].decode("utf-16-be").strip()
        except UnicodeDecodeError:
            return _ascii(value)
    return _ascii(value)


def _fonts(raw: bytes) -> tuple[list[tuple[str, str, str, bool]], int]:
    """The fonts text is drawn in, with whether that text can be decoded.

    Returns ``[(name, subtype, encoding, usable)]`` and the number of descendant
    CID font objects folded away. A font is usable when it carries a ``/ToUnicode``
    CMap, or a named base encoding, or an ``/Encoding`` with a ``/Differences``
    array -- the three things that let a byte code be turned back into a character.

    ``CIDFontType0``/``CIDFontType2`` objects are counted but not listed. They are
    the descendants a ``Type0`` font delegates glyphs to, they never carry an
    encoding of their own by design, and listing them made a perfectly readable
    card report two fonts with "no usable encoding".
    """
    out: dict[tuple[str, str], tuple[str, str, str, bool]] = {}
    descendants = 0
    for body in _objects(raw):
        if not re.search(rb"/Type\s*/Font\b", body):
            continue
        subtype = _name_field(body, b"Subtype") or "?"
        if subtype.startswith("CIDFont"):
            descendants += 1
            continue
        name = _name_field(body, b"BaseFont") or "(unnamed)"
        encoding = _name_field(body, b"Encoding")
        has_unicode = b"/ToUnicode" in body
        has_differences = b"/Differences" in body
        usable = bool(has_unicode or has_differences
                      or encoding in KNOWN_ENCODINGS)
        shown = encoding or ("built-in" if usable else "none")
        if has_unicode:
            shown = f"{shown} +ToUnicode" if encoding else "+ToUnicode"
        out[(name, subtype)] = (name, subtype, shown, usable)
    return [out[key] for key in sorted(out)], descendants


def _objects(raw: bytes):
    """Each indirect object's dictionary, stopping before any stream data.

    Streams are skipped rather than decompressed: nothing this module reports
    lives inside one, and a stream's bytes can contain anything, including the
    tokens a scan would mistake for structure.
    """
    for match in re.finditer(rb"(?<![0-9])\d+\s+\d+\s+obj\b", raw):
        start = match.end()
        stop = raw.find(b"endobj", start)
        stream = raw.find(b"stream", start)
        if stop < 0:
            stop = len(raw)
        if 0 <= stream < stop:
            stop = stream
        yield raw[start:stop]


def _name_field(body: bytes, key: bytes) -> str:
    match = re.search(rb"/" + key + rb"\s*/([A-Za-z0-9+.\-]+)", body)
    return _ascii(match.group(1)) if match else ""


def _has(raw: bytes, pattern: bytes) -> bool:
    return re.search(pattern, raw) is not None


def _ascii(raw: bytes) -> str:
    return raw.decode("latin-1", "replace").strip()


# --- one page ---------------------------------------------------------------


def _one_page(path: Path, index: int, count: int, raw: bytes, *,
              fonts_ok: bool = True, redactor: _Redactor | None = None) -> int:
    print(f"page {index + 1} of {count}")
    try:
        page = pdf.extract(path, page=index)
    except pdf.PdfError as exc:
        print(f"{INDENT}This page cannot be read: {exc}")
        return 1
    except OSError as exc:
        # The file was read whole a moment ago, so this means it changed underneath:
        # a share that unmounted, a folder mid-sync. Not the page's fault, and not to
        # be reported as though it were.
        print(f"{INDENT}This page could not be read from the file: "
              f"{exc.strerror or exc}.")
        return 1
    except Exception as exc:                          # noqa: BLE001 - see below
        _surprise(exc, f"reading page {index + 1}", redacting=redactor is not None)
        return 1

    if redactor is not None:
        # Before anything below it prints. The page is parsed a second time here,
        # purely to learn the full multi-word names, and the failure is ignored
        # because the word-level pass above it does not need the page to parse --
        # which is the case that matters, since a page that will not read is why
        # anybody is running this.
        redactor.learn_page(page)
        try:
            redactor.learn_card(scorecard.read_page(page))
        except CardError:
            pass

    paper = PAPERS.get((round(page.width), round(page.height)), "")
    _row("size", f"{page.width:g} x {page.height:g} pt  "
                 f"({paper or 'not a standard paper size'})", indent=INDENT)
    rotation = _rotation(raw)
    with _verbatim(redactor):
        # The tool's words and a number of degrees, as the causes quote it.
        _row("rotation", rotation, indent=INDENT)
    _row("words", str(len(page.words)), indent=INDENT)
    _row("rects", str(len(page.rects)), indent=INDENT)
    with _verbatim(redactor):
        # A count and a share in the tool's words, which a cause quotes.
        _row("images", _images(page), indent=INDENT)
    if not fonts_ok:
        # Ran both ways in this session on cards written with and without a
        # /ToUnicode map: the reader recovered all 196 words from the map-less one
        # through the font's own glyph order, so a missing map is a risk, not a
        # diagnosis. Which of the two it is depends entirely on this word count.
        if page.words:
            _row("", "a font here has no decode table, yet words came back "
                     "anyway: the reader fell back to the font's built-in "
                     "encoding, so this is not what is wrong with the page",
                 indent=INDENT)
        else:
            _row("", "no words, and a font with no decode table: byte codes map "
                     "to glyph numbers only, so the page reads as blank rather "
                     "than broken", indent=INDENT)

    _rect_census(page)
    rules, bands = _table(page)
    _checkboxes(page, bands)
    found, missing = _header_fields(page)
    try:
        taken, failure = _taken(page), None
    except Exception as exc:                          # noqa: BLE001
        # The verdict reads the page again and says it was refused, or raises
        # what the reader or the build's rules raised where it asked them
        # before they were asked here, so the summary is printed as it was:
        # raised here, a failure would take the summary with it (4.134).
        taken, failure = {}, exc
    _describe(page, redactor=redactor, taken=taken)
    return _verdict(page, path=path, index=index, rules=rules, bands=bands,
                    found=found, missing=missing, rotation=rotation,
                    redactor=redactor, taken=taken, failure=failure)


def _rect_census(page: pdf.Page) -> None:
    """Every distinct rectangle size and how many there are.

    The census is how the expected shape counts were derived in the first place,
    and it is
    the fastest way to see that a page has no rectangles at all -- which means it
    was rasterised, and no amount of parsing will find a table in it.
    """
    sizes = Counter((round(r.w, 2), round(r.h, 2), r.filled) for r in page.rects)
    if not sizes:
        _row("rect census", "none: this page paints no rectangles", indent=INDENT)
        return
    _row("rect census", f"{len(sizes)} distinct size(s)", indent=INDENT)
    for (w, h, filled) in sorted(sizes, key=lambda k: (-sizes[k], k)):
        count = sizes[(w, h, filled)]
        row = (f"{w:>8.2f} x {h:<8.2f} {count:>4}  "
               f"{'filled' if filled else 'stroked'}  ")
        _flow(f"{INDENT}  ", row + _rect_role(w, h, filled),
              f"{INDENT}  " + " " * len(row))


def _rect_role(w: float, h: float, filled: bool) -> str:
    """A guess at what a rectangle of this shape is for, by shape alone.

    Alone but for one question: a tick is the fill and nothing else, so a square
    of a box's size that is stroked is the box drawn again, and calling it a tick
    here would contradict the checkbox census below, which counts only fills.
    Measured, rasterised: the inside of a stroked one is not painted at all.
    """
    thin = min(w, h)
    long_side = max(w, h)
    if thin < 1.5 and long_side > 20:
        return "vertical rule" if h > w else "horizontal rule"
    if thin < 1.5:
        return "checkbox outline hairline"
    if abs(w - h) <= 0.35 * long_side and long_side <= 20:
        return ("ticked checkbox fill" if filled
                else "box-sized square, not a tick: its inside is not painted")
    if thin > 20:
        return "background panel"
    if long_side > 100 and 5 < thin < 30:
        return "row shading"
    return ""


def _table(page: pdf.Page) -> tuple[list[float], list[tuple[float, float]]]:
    """The set table's own vertical rules, and the row bands they describe.

    Found by shape, not position: a rule is much taller than it is wide and tall
    enough to span a row. The bands are the distinct vertical spans those rules
    occupy, which is what makes the row count -- and therefore the match format --
    a property of the card rather than a constant in the code.
    """
    candidates = [r for r in page.rects
                  if r.h > 20 and r.w > 0 and r.h / max(r.w, 0.01) > 8]
    verticals = _dominant_height(candidates)
    if not verticals:
        _row("column rules", "none found: without them the set table has no grid,",
             indent=INDENT)
        _row("", "and a reader that guessed at one would report a confident",
             indent=INDENT)
        _row("", "scoreline off whatever columns it assumed (F5)", indent=INDENT)
        return [], []

    xs = sorted({round(r.cx, 1) for r in verticals})
    bands = sorted({(round(r.y0, 1), round(r.y1, 1)) for r in verticals})
    _row("column rules", f"{len(xs)} at x = "
                         f"{', '.join(f'{x:g}' for x in xs)}", indent=INDENT)
    _row("", f"{len(xs)} rules make {max(0, len(xs) - 1)} cell(s) per row",
         indent=INDENT)

    # The widths, not just the count, because that is what a dropped rule looks
    # like: the rules either side of it survive and the cell between them comes
    # back as wide as the two it replaced. A card's cells are legitimately of very
    # different widths -- a name cell is six times a games cell -- so the outlier
    # is found by mirroring rather than by comparing widest to narrowest, which on
    # a sound card is a factor of six and means nothing. Checked in this session
    # against the real exported cards, whose widths mirror to within a point.
    widths = [round(b - a, 1) for a, b in zip(xs, xs[1:])]
    if widths:
        _row("", f"cell widths {', '.join(f'{w:g}' for w in widths)}",
             indent=INDENT)
    for left, right in zip(widths, reversed(widths)):
        if abs(left - right) > 2.0:
            _row("", f"the row is not symmetric about its centre: {left:g}pt "
                     f"against {right:g}pt on the mirroring cell, so the wider "
                     f"one is two cells with no rule between them", indent=INDENT)
            break
    if xs[0] < 0 or xs[-1] > page.width:
        _row("", f"rules run outside the 0 to {page.width:g}pt page: the table is "
                 f"shifted off the sheet, so a column is clipped and its column "
                 f"is unreadable", indent=INDENT)

    merged = _merge_bands(bands)
    heights = sorted(round(b - t, 2) for t, b in merged)
    _row("row bands", f"{len(merged)}: "
                      f"{' '.join(f'({t:g},{b:g})' for t, b in merged)}",
         indent=INDENT)
    _row("", f"height {heights[0]:g}"
             f"{f' to {heights[-1]:g}' if heights[-1] != heights[0] else ''}",
         indent=INDENT)
    # A hairline of slack: the two copies of each rule differ by about a point, so
    # only a real gap means a row is short.
    if heights[-1] - heights[0] > 2.0:
        _row("", "rows of unequal height: a rule is missing or short, so one row "
                 "is not bounded", indent=INDENT)
    _markers(page, merged, xs)
    return xs, merged


def _dominant_height(rects: list[pdf.Rect]) -> list[pdf.Rect]:
    """Keep only the vertical rules of the most common height.

    A card paints tall rules for the set table and a few shorter ones elsewhere --
    around the match-status checkboxes, for instance. Taking the modal height keeps
    the table and drops the strays without knowing where on the page either lives,
    so a template with taller or shorter rows still reads correctly.
    """
    if not rects:
        return []
    # Grouped in ascending height so the grouping does not depend on the order the
    # rectangles happened to be painted in. The 5% band is wide enough to hold the
    # inner and outer copy of one rule together and narrow enough to keep a
    # different run of rules apart.
    groups: list[list[pdf.Rect]] = []
    for r in sorted(rects, key=lambda r: r.h):
        if groups and r.h - groups[-1][0].h <= 0.05 * groups[-1][0].h:
            groups[-1].append(r)
        else:
            groups.append([r])
    return max(groups, key=lambda group: (len(group), group[0].h))


def _merge_bands(bands: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Collapse the near-identical spans of the inner and outer copies of a rule.

    The template paints each rule twice, a hairline apart, so the raw spans come
    in pairs about a point different. Merging by overlap rather than by an exact
    match is what makes the row count come out as four rather than eight.
    """
    out: list[tuple[float, float]] = []
    for top, bottom in bands:
        if out and top < out[-1][1] - 1.0:
            prev_top, prev_bottom = out[-1]
            out[-1] = (min(prev_top, top), max(prev_bottom, bottom))
        else:
            out.append((top, bottom))
    return out


def _markers(page: pdf.Page, bands: list[tuple[float, float]],
             rules: list[float]) -> None:
    """Each row's discipline marker, read from its own leftmost cell.

    Restricted to the first cell of the grid on purpose. Searching the whole row
    finds the games columns instead, and a bare ``6`` in a games cell looks exactly
    like a singles marker -- which would report every row of a doubles card as
    singles and get the match format wrong with total confidence.
    """
    if not bands or len(rules) < 2:
        return
    left, right = rules[0], rules[1]
    rows: list[str] = []
    for top, bottom in bands:
        cell = [w.text for w in page.words_in_band(top, bottom, left, right)
                if MARKER_RE.match(w.text.replace(" ", ""))]
        rows.append(cell[0] if cell else "?")
    disciplines = "".join("d" if "&" in m else "s" if m != "?" else "?"
                          for m in rows)
    _row("row markers", ", ".join(rows), indent=INDENT)
    _row("", f"reads as format {disciplines} "
             f"(d = doubles pairing, s = singles)", indent=INDENT)
    if "?" in disciplines:
        _row("", "a row with no marker in its first cell cannot be classified,",
             indent=INDENT)
        _row("", "so the match format cannot be read off this card", indent=INDENT)


def _checkboxes(page: pdf.Page, bands: list[tuple[float, float]]) -> None:
    """The checkbox census, with tick state.

    There is no glyph for a tick. An unticked box is four hairline rects forming a
    square outline; a ticked one is that outline plus a solid fill at the same
    place. So the count is geometry, and the expected number scales with the row
    count rather than being the 30 a four-row card happens to have.
    """
    outlines = _outlines(page)
    ticks = page.square_fills()
    ticked = 0
    for box in outlines:
        if any(box[0] - 1.0 <= fill.cx <= box[2] + 1.0
               and box[1] - 1.0 <= fill.cy <= box[3] + 1.0 for fill in ticks):
            ticked += 1

    _row("checkboxes", f"{len(outlines)} outline(s), {len(ticks)} solid fill(s), "
                       f"{ticked} ticked", indent=INDENT)
    if bands:
        rows = len(bands)
        # rows x 2 sides x 3 per-set flags, + Match Winner and Match Forfeited
        # per side, + Match Cancelled and Match Disputed.
        expected = rows * 2 * 3 + 4 + 2
        agrees = len(outlines) == expected
        _row("", f"{rows} row(s) implies {expected} ({rows} x 2 x 3 + 4 + 2): "
                 f"{'as expected' if agrees else 'MISMATCH'}", indent=INDENT)
        if not agrees:
            _row("", "a box count that does not match the row count means a flag "
                     "was missed or invented, so the per-set status read off this "
                     "card cannot be trusted", indent=INDENT)
    # Each named by the box the reader reads it from, in the reader's words, and
    # not by the words beside it, which are a neighbour's label as often (4.467).
    in_order = sorted(ticks, key=lambda r: (round(r.cy, 1), r.cx))
    for fill, place in zip(in_order, scorecard.tick_places(page, in_order)):
        _row("", f"ticked at x {fill.cx:7.2f} y {fill.cy:7.2f}  {place}",
             indent=INDENT)


def _outlines(page: pdf.Page) -> list[tuple[float, float, float, float]]:
    """Bounding boxes of the hairline squares that make up unticked checkboxes.

    Matched by pairing the short horizontal hairlines with the short vertical ones
    that share a corner. The side length is measured from the hairlines rather than
    assumed, so a card scaled by a "fit to page" print is still counted correctly.
    """
    hair = [r for r in page.rects if min(r.w, r.h) < 1.5 <= max(r.w, r.h) <= 20.0]
    horizontals = [r for r in hair if r.w > r.h]
    verticals = [r for r in hair if r.h >= r.w]
    corners: dict[tuple[float, float], list[float]] = {}
    for r in horizontals:
        corners.setdefault((round(r.x0, 1), round(r.y0, 1)), []).append(r.w)
    boxes: list[tuple[float, float, float, float]] = []
    for r in verticals:
        key = (round(r.x0, 1), round(r.y0, 1))
        if key not in corners:
            continue
        side = max(r.h, max(corners[key]))
        boxes.append((r.x0, r.y0, r.x0 + side, r.y0 + side))
    return boxes


def _result_verb_in(line: str, *, short: bool = False) -> str:
    """The first run of words in capitals on ``line`` that the one table reads.

    Longest first, so ``DEF. BY`` is found whole and not as ``DEF.``. In capitals,
    as the template prints its verb, so a rules sheet's "a draw" or "the tie" is
    none; and a separator states no result, so it is no result verb either.
    """
    words = line.split()
    for start in range(len(words)):
        for size in (3, 2, 1):
            run = " ".join(words[start:start + size])
            if len(run.split()) == size and run.isupper() and verb_side(run):
                return run
    return ""


# A field the reader has a rule for is found by that rule, so the doctor cannot
# call a division missing that the reader goes on to read, or a result verb
# missing that the one table reads.
READER_RULES = {"division": scorecard.division_in, "round": scorecard.round_in,
                "result verb": _result_verb_in}


def _fill_ins_heading(page: pdf.Page) -> str:
    """The fill-ins heading the reader ends the roster block at, if the page prints
    one (:func:`scorecard.fill_ins_heading`), looked up when it is asked."""
    return scorecard.fill_ins_heading(page)


def _court(page: pdf.Page) -> str:
    """The court the reader reads off the page (:func:`scorecard.court_in`), or
    "" where it reads none, looked up when it is asked."""
    court = scorecard.court_in(page)
    return "" if court is None else str(court)


# A field the reader finds on the page and not on a line: the fill-ins heading is
# the one under `Players` it ends the roster block at, and not a line above that
# reads like one, as a team's name in brackets in the result line can; and the
# court is the one it reads by its label, off the venue's line (4.466).
PAGE_RULES = {"fill-ins": _fill_ins_heading, "court": _court}


def _as_read(name: str, text: str) -> str:
    """What the reader's own rule finds on any one line, long forms first."""
    rule = READER_RULES.get(name)
    if rule is None:
        return ""
    lines = text.splitlines()
    return next((value for short in (False, True)
                 for value in (rule(line, short=short) for line in lines)
                 if value), "")


def _header_fields(page: pdf.Page) -> tuple[list[str], list[str]]:
    """Which header fields the reader's rule or a tolerant pattern can find."""
    text = page.text()
    found: list[str] = []
    missing: list[str] = []
    print(f"{INDENT}header fields")
    for name, pattern in HEADER_FIELDS:
        match = re.search(pattern, text) if pattern else None
        rule = PAGE_RULES.get(name)
        value = ((rule(page) if rule else _as_read(name, text))
                 or (match.group(1) if match else ""))
        if value:
            found.append(name)
            # Past the width it goes on under the name: a value with no space
            # in it, a match id of fifty digits, is as wide under itself as
            # beside the name (4.186).
            print(f"{INDENT}  {name:<16} found     {value!r}")
        else:
            missing.append(name)
            print(f"{INDENT}  {name:<16} not found")
    return found, missing


def _describe(page: pdf.Page, redactor: _Redactor | None = None, *,
              taken: dict[tuple[str, str], tuple[tuple[str, str], ...]]) -> None:
    """What the reader itself makes of the page structure.

    A stage's reason for refusing the page is the reader's own words, and is
    published as written under ``--redact``; the rest, its detail with it, is
    scrubbed. The reader says which lines are reasons: a line is not taken for
    one by what it says, which a page can print inside a word.

    The reader hands a check on without the club's rules, so a hand-off is said
    as the verdict says it, under the rules *taken* (4.134), and every line goes
    on under its label past the width (4.186).
    """
    print(f"{INDENT}reader's own summary")
    try:
        summary = scorecard.describe_lines(page)
    except Exception as exc:                     # noqa: BLE001
        # The one place a bare catch is right: the reader raising while describing
        # a page is itself the diagnosis, and swallowing it into a traceback would
        # hide every section above from whoever needs them.
        print(f"{INDENT}  scorecard.describe() raised "
              f"{type(exc).__name__}: {exc}")
        return
    for entry in summary:
        if isinstance(entry, scorecard.CheckLine):
            entry = scorecard.CheckLine.of(_as_reported(entry.check, taken))
        with _verbatim(redactor if isinstance(entry, scorecard.Reason) else None):
            for line in entry.splitlines():
                text = line.lstrip(" ")
                opening = f"{INDENT}  {line[:len(line) - len(text)]}"
                _flow(opening, text, opening + "    ")


def _rotation(raw: bytes) -> str:
    """Every ``/Rotate`` the document declares.

    Document-wide rather than per-page because ``/Rotate`` is an inheritable
    attribute: a page can be turned by an entry on an ancestor node it never
    mentions. :mod:`~matchcentre.pdf` resolves that inheritance and reports
    ``width``/``height`` already swapped, so this line exists to say *why* a page's
    dimensions look transposed.
    """
    values = sorted({int(m.group(1)) % 360
                     for m in re.finditer(rb"/Rotate\s+(-?\d+)", raw)})
    if not values:
        return "0 (none declared)"
    if values == [0]:
        return "0"
    turned = in_words(f"{v}" for v in values)
    return (f"{turned} degrees -- the page is turned, and every coordinate on it "
            f"with it")


# --- verdict ----------------------------------------------------------------


def _date_fact(card: Card) -> str:
    """The date the reader read with its weekday, and what files the card if not that.

    The card's weekday used to be the date's own, so the two could not disagree
    here; it is now what the card prints, and a card dated a Tuesday that prints
    ``Wed`` is the question this tool is asked most. So both are named, with which
    of them files the card -- or the night its competition line names, which
    files it before either, and is named wherever the date's weekday is not it.
    Where it is, the date is what files the card, as the build's note says of it.
    That and the disagreement are asked of :mod:`matchcentre.discover`, whose rule
    they are, so that the verdict cannot say one night while the build files the
    card under another.
    """
    dated, day = weekday_of(card.date), discover.filing_day(card)
    if card.night and day == card.night and day != dated:
        files = f"filed by the {day} its competition line names"
    elif dated and day == dated:
        files = "the date is what files it"
    else:
        files = f"filed by the {day} it prints"
    if not dated:
        return f"(none read; {files})" if day else "(none read)"
    disputed = discover.disputed_weekday(card)
    if disputed:
        return f"{card.date} {dated} (the card prints {disputed}; {files})"
    if day != dated:
        return f"{card.date} {dated} ({files})"
    return f"{card.date} {dated}"


def _rules_where(combos: set[tuple[str, str]]) -> str:
    """Where the club's set rule and scoring are one of *combos*, in words.

    "the club's set rule is fast4 or pro-8 or where it is first-to-6 and its
    scoring is simple": the set rules sharing their scorings named together, in
    the order :data:`SET_RULES` holds them, and the scorings only where not all.
    """
    by_scorings: dict[tuple[str, ...], list[str]] = {}
    for name in SET_RULES:
        scorings = tuple(scoring for scoring in RULESETS
                         if (name, scoring) in combos)
        if scorings:
            by_scorings.setdefault(scorings, []).append(name)
    clauses = []
    for scorings, names in by_scorings.items():
        clause = "where it is " if clauses else "the club's set rule is "
        clause += in_words(names, "or")
        if len(scorings) < len(RULESETS):
            clause += f" and its scoring is {in_words(scorings, 'or')}"
        clauses.append(clause)
    return in_words(clauses, "or")


def _as_reported(check: CrossCheck, taken: dict[tuple[str, str], tuple[
        tuple[str, str], ...]]) -> CrossCheck:
    """*check* as the table says it, a hand-off said as reported where it is.

    The reader hands a check to another without the club's rules, and under the
    rules the build fails a forfeit's rows under, *taken*, ``forfeit-contradicted``
    reports what it names, and F6 passes it by (4.134).
    """
    onward = {combo for combo, named in taken.items()
              if check.name in {name for name, _ in named}}
    if not check.reported_by or not onward:
        return check
    own = {(name, scoring) for name in SET_RULES for scoring in RULESETS} - onward
    if not own:
        return dataclasses.replace(check, reported_by="forfeit-contradicted",
                                   detail="")
    return dataclasses.replace(
        check, reported_by=in_words([check.reported_by, "forfeit-contradicted"],
                                    "or"),
        detail=f"{check.detail}, where {_rules_where(own)}, and by "
               f"forfeit-contradicted where {_rules_where(onward)}")


def _taken(page: pdf.Page) -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    """Under each set rule and scoring the build fails a forfeit's rows under,
    what ``forfeit-contradicted`` names there, which nothing else reports.

    The build holds a forfeit's rows to its box under the club's set rule and
    scoring, which this is not given, so it asks the build's rules under each
    (4.134): once, so that the reader's summary and the verdict say one thing.
    What the reader raises of the page, its refusal with the rest, is raised.
    """
    card = scorecard.read_page(page)
    taken: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    for name, rule in SET_RULES.items():
        for scoring, rules in RULESETS.items():
            if validate.rows_against_box(card, rule, rules):
                taken[name, scoring] = validate.restating_box(
                    card, from_rows(card.doubles_flags, rule=rule))
    return taken


def _records_nothing(card: Card) -> bool:
    """Whether build.py tells *card* its ``no-result`` alone (4.424).

    The build's own question, :func:`~matchcentre.validate.records_nothing`,
    asked of the match the build makes of the card. It is made under the
    default rules, which is enough: rows that are all 0-0 finish no set under
    any set rule and decide no match under any scoring, and a card whose rows
    hold games, or one the build gives a forfeit, is not one whatever the rules.
    Rejected: a sentence worded from the card's rows, which would say the build
    stood a finding down that the build had not.
    """
    config = Config(path=Path(DEFAULT_PATH))
    match = discover._build_match(
        discover._Sided(card=card, home="home", away="away"), comp_id="",
        fmt=from_rows(card.doubles_flags), rules=config.ruleset_for(),
        config=config, unknown={})
    return validate.records_nothing(match, card)


def _verdict(page: pdf.Page, *, path: Path, index: int,
             rules: list[float], bands: list[tuple[float, float]],
             found: list[str], missing: list[str], rotation: str,
             redactor: _Redactor | None = None,
             taken: dict[tuple[str, str], tuple[tuple[str, str], ...]],
             failure: Exception | None = None) -> int:
    """Whether the page reads as a scorecard, and if not, the likeliest cause.

    The causes are the tool's own words, and are published as they are written
    under ``--redact``, as the footer is, and so is the reason above them, which
    the reader words without quoting the page. The detail can quote it, and is
    scrubbed with the rest. *failure* is what asking the build's rules raised,
    raised here, after the rows it follows (4.134).
    """
    print(f"{INDENT}verdict")
    try:
        card = scorecard.read_page(page)
    except CardError as exc:
        with _verbatim(redactor):
            _flow(f"{INDENT}  ", f"This page was refused: {exc.reason}",
                  f"{INDENT}      ")
        if exc.detail:
            _flow(f"{INDENT}  ", f"detail: {exc.detail}", f"{INDENT}      ")
        with _verbatim(redactor):
            for line in _causes(page, refusal=exc, rules=rules, bands=bands,
                                found=found, rotation=rotation):
                print(f"{INDENT}  {line}")
        return 1

    print(f"{INDENT}  This page reads as a scorecard.")
    facts = [
        ("division", card.division or "(none printed)"),
        ("round", card.round or "(none printed)"),
        ("date", _date_fact(card)),
        ("home", card.home_name or "(none printed)"),
        ("away", card.away_name or "(none printed)"),
        ("verb", card.verb or "(none printed)"),
        ("sets", f"{len(card.sets)} row(s): "
                 + ", ".join(f"{s.home_games}-{s.away_games}"
                             for s in card.sets)),
        ("format", "".join("d" if s.doubles else "s" for s in card.sets)),
        ("totals row", "present" if card.totals.present else "not printed"),
        ("winner tick", card.winner_tick or "none"),
    ]
    for label, value in facts:
        _row(label, value, indent=f"{INDENT}    ")

    available = card.cross_checks_available
    failed = card.cross_checks_failed
    # A printed check whose disagreement another check reports is neither "not
    # printed" nor a failure of its own; it is counted apart only where there is
    # one, so every other card is described in the same words as before.
    reported = [check for check in available if check.reported_by]
    _row("cross-checks", f"{len(available)} available, {len(failed)} disagreeing"
         + (f", {len(reported)} reported by another check" if reported else ""),
         indent=f"{INDENT}    ")
    if failure is not None:
        raise failure
    # Where the rows are against the box, and what forfeit-contradicted names
    # there, as _taken asked it of the build's rules (4.134).
    combos = {(name, scoring) for name in SET_RULES for scoring in RULESETS}
    others = {combo for combo in combos for check in failed
              if check.name not in {name for name, _ in taken.get(combo, ())}}
    for check in (_as_reported(check, taken) for check in card.checks):
        state = check.state("not printed on this card")
        _row(check.name, f"{state}{'  ' + check.detail if check.detail else ''}",
             indent=f"{INDENT}      ")
    for warning in card.warnings:
        _flow(f"{INDENT}    ", f"warning     {warning}", f"{INDENT}    " + " " * 12)
    claims = validate.winner_claims(card) if discover.called_off(card) else []
    if card.walkover_by:
        # Ticked Cancelled and counted: what makes it a walkover, in the words its
        # checks are held to it by, and what the build does with it (4.418).
        print(f"{INDENT}  {scorecard._walkover_text(card.walkover_by)}. So build.py "
              f"scores the match as a forfeit by {card.walkover_by}, as it would a "
              f"card ticking the {card.walkover_by} Match Forfeited box, and holds "
              f"what the card prints to the walkover rather than to the set rows.")
        # Under the rules its rows are not against it by, as forfeit-contradicted
        # holds them (below), the build says once that they hold play, where they
        # hold play the TOTAL row leaves out, as validate asks it.
        rest = combos - set(taken)
        if validate.uncounted_play(card) and rest:
            its = "Its" if rest == combos else f"Where {_rules_where(rest)}, its"
            print(f"{INDENT}  {its} set rows hold games {card.home_games}-"
                  f"{card.away_games} that the TOTAL row does not count: build.py "
                  f"says so once, as walkover-rows-played, a warning.")
    elif claims:
        # The one finding the build makes of it is a warning, and the verdict says
        # so in the words the report names the claims by (4.419).
        print(f"{INDENT}  Match Cancelled is ticked, so build.py counts nothing on "
              f"this card and holds nothing it prints to the set rows. "
              f"{validate.overruled(claims)}, so build.py reports the match once, "
              f"as cancelled, in a warning that says so.")
    elif discover.called_off(card):
        print(f"{INDENT}  Match Cancelled is ticked, so build.py counts nothing on "
              f"this card")
        print(f"{INDENT}  and holds nothing it prints to the set rows: it reports "
              f"the match")
        print(f"{INDENT}  once, as cancelled.")
    elif not available:
        # Beside a table line saying a verb is printed, not that none is (4.133).
        first = ("Nothing this card prints can serve as a cross-check"
                 if any(check.unusable for check in card.checks) else
                 "No independent cross-check is printed on this card")
        if _records_nothing(card):
            # Its one finding, as the build makes it, in the build's words: no
            # figure rests on rows that read 0-0, so neither the report nor the
            # page says what the reader's warnings above say of it (4.424).
            print(f"{INDENT}  {first}, and every set row reads 0-0: there is "
                  f"nothing for one to confirm, so build.py reports the match as "
                  f"no-result, a warning, and nowhere says a cross-check is "
                  f"missing, in its report or on its page.")
        else:
            print(f"{INDENT}  {first}, so its numbers")
            print(f"{INDENT}  rest on the set rows alone (F6 warns rather than "
                  f"refuses).")
    elif failed and not others:
        # forfeit-contradicted names every one, under every rule (4.134).
        pass
    elif failed:
        # What each was held to, from the check (4.152), laid out to the width
        # as every line is (4.186), and where only under some rules, the rules
        # (4.134); and what the build does with it, which is to fail the run and
        # still record the match as its rows or its box give it, a result or none
        # (4.426).
        held = in_words(sorted({check.held_to for check in failed}))
        where = "" if others == combos else f"Where {_rules_where(others)}, "
        print(f"{INDENT}  A cross-check disagrees with {held}. The card states its "
              "result more than once and the readings differ: something was "
              "misread or misprinted, or the card counts its rows by a rule the "
              f"cross-check does not. {where}build.py reports this as an error, "
              "which fails the run, and still records the match as its set rows "
              "give it under the club's rules, or as its Match Forfeited box gives "
              "it if one is ticked.")
    elif reported:
        print(f"{INDENT}  A cross-check disagrees with what the card is scored on, "
              f"and")
        print(f"{INDENT}  build.py reports that once, as "
              f"{in_words(sorted({c.reported_by for c in reported}))}.")
    # It names the rules it fails the card under, and none it does not, and
    # under each what the finding names.
    by_words: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for combo, named in taken.items():
        by_words.setdefault(tuple(what for _, what in named), set()).add(combo)
    for words, under in by_words.items():
        print(f"{INDENT}  Where {_rules_where(under)}, the set rows give the match "
              f"to {card.forfeit_side}, and {scorecard._given_up(card)} "
              f"gives it to {forfeit_match_winner(card.forfeit_side)}"
              f"{validate.as_restating(words)}: build.py reports that once, as "
              "forfeit-contradicted, an error, which fails the run.")
    return 0


# How much of a page its images must cover for it to be a picture of a card,
# when it has no table rules. Measured: a card's PNG that Chrome printed to PDF
# covers 15.6 per cent of the page, a screenshot of it on a web page 71, and a
# scan all of it, while a club's logo on the letterhead, 60 x 30 points, covers
# under 1 per cent. A tenth sits between the two.
_PICTURE_SHARE = 0.1


def _picture_share(page: pdf.Page) -> float:
    """The share of the page its images cover, at most the whole of it.

    Added up image by image, so a scan painted in strips covers the page as one
    painted whole does; an image off the page covers none of it.
    """
    whole = pdf.Rect(0.0, 0.0, page.width, page.height, True)
    area = page.width * page.height
    if area <= 0:
        return 0.0
    return min(1.0, sum(whole.overlaps(image) for image in page.images) / area)


def _images(page: pdf.Page) -> str:
    """The images row: how many the page paints, and how much of it they cover.

    Rounded down, so that no page is shown covering the share that makes it a
    picture when it covers less. An image painted beside the page, or on a page
    of no size, covers none of it, and is shown so.
    """
    if not page.images:
        return "none"
    cover = _picture_share(page)
    share = int(cover * 100)
    shown = f"{share}%" if share else "under 1%" if cover else "none"
    return f"{len(page.images)}, covering {shown} of the page"


# The header fields a page can print without a word of the card's. Both are
# figures, and Chrome, printing with its headers and footers on, prints both at
# the top of the page: a screenshot of a card on a web page, printed to PDF by
# Chrome, was measured with no rects and these two fields found, and nothing
# else. A fixture list prints both too, and a rules sheet a start time. Every
# other field is found by a word of the card's, or for the match id by the
# card's own `#`. Chrome prints the page's title too, so a screenshot whose
# title names a round finds that round as well.
_FIELDS_A_BROWSER_PRINTS = ("date", "time")

# What a page that is no scorecard is told to do with it, after each cause that
# says it may be one. build.py reports every PDF under data/ that it cannot read
# as an error (F4) and exits 1 while the file is there, so the file is fine and
# its place is not.
_NOT_A_SCORECARD = (
    "If the page is one of those, the file is fine, but keep it in a folder",
    "of its own outside data/: build.py reports every PDF under data/ that it",
    "cannot read as an error (F4), and exits 1.",
)


def _causes(page: pdf.Page, *, refusal: CardError, rules: list[float],
            bands: list[tuple[float, float]], found: list[str],
            rotation: str) -> list[str]:
    """The likeliest explanations for a refusal, most likely first.

    Ordered by how completely each one accounts for the refusal: a page with no
    text at all has one explanation, and a page with text but no table rules has a
    different one. Anything left over is a page that is simply not a scorecard,
    which is the common and harmless case -- a club keeps its draw and its rules
    sheet in the same folder as its cards.

    Four causes are not inferred at all. A card on which a checkbox it needs
    could not be named is refused with one of three kinds of refusal, one for the
    squares, one for the labels beside them and one for a page that lacks what
    some of its boxes need, and a card that ticks a box nothing reads with a
    fourth, and each is explained first, from the kind, whatever this tool's own
    tests above made of the page.

    A line that fits is printed as it is given and a longer one is laid out, so
    no sentence is broken by hand past the width: laid out again where the width
    falls, a word or two of it was left alone on a line (4.186).
    """
    out: list[str] = []
    if isinstance(refusal, scorecard.MissingCheckboxes):
        # A download that stopped early, measured, and not another template:
        # the reader gives this kind only when what the page lacks is a square
        # or a word some of its own boxes have, so it is the copy that is short,
        # or the word of a flag printed once for its one box, which a template
        # that words that flag another way leaves the same, and which a copy
        # downloaded again tells apart.
        out.append("Likeliest cause: part of the card did not reach this file "
                   "-- a download that stopped early, or a copy damaged on the "
                   "way -- so the square of a box the card needs, or the label "
                   "that names it, is not on the page; the detail line says "
                   "which. A tick in a box that is not there cannot be seen, so "
                   "the card cannot be read as it is. Download it again from "
                   "Match Centre, and if the new copy is refused the same way, "
                   "send this verdict (--redact takes the names out) to whoever "
                   "maintains this tool.")
        return out
    if isinstance(refusal, scorecard.UnlabelledCheckboxes):
        # Asked of the reader, not worked out here. The header fields above
        # look for the same words with this tool's own patterns, and a second
        # test of whether a label is there is a second answer to it -- one
        # that finds the words on a page whose boxes sit off their line.
        out.append("Likeliest cause: the card was printed from another "
                   "template -- another association's, a translation, or a "
                   "card retyped by hand -- which words its tick-box labels "
                   "differently or lays them out another way; the detail line "
                   "says which. A box is only what its label says it is, so the "
                   "card cannot be read as it is. If Match Centre can export "
                   "this match, use that export instead; if not, send this "
                   "verdict (--redact takes the names out) to whoever "
                   "maintains this tool, which has to be taught the new words "
                   "or the new layout.")
        return out
    if isinstance(refusal, scorecard.UnlocatedCheckboxes):
        # The census above can disagree with the reader here, by design: it is
        # the looser test, and a page it finds boxes on and the reader does not
        # is the reason it exists. So the cause names both counts' sources
        # rather than leaving "looks like a scorecard" under a MISMATCH.
        out.append("Likeliest cause: the tick boxes are not drawn the way "
                   "Match Centre draws")
        out.append("them, as filled hairlines whose top and bottom are a box "
                   "side apart, so")
        out.append("no box could be rebuilt. The census above counts outlines "
                   "by a looser")
        out.append("test, and the reader's own count is the one in its "
                   "summary. Another")
        out.append("program re-saving or re-printing the card is the usual "
                   "reason: export it")
        out.append("again from Match Centre, and if that export is refused "
                   "too, send this")
        out.append("verdict (--redact takes the names out) to whoever "
                   "maintains this tool.")
        return out
    if isinstance(refusal, scorecard.UnreadTick):
        # Every box the card needs is named, so none of the three causes above
        # fits: the card has a box more, and the tick is in it.
        out.append("Likeliest cause: the card draws a tick box that Match "
                   "Centre's card")
        out.append("does not have, and it is ticked: a set's box away from "
                   "the set rows, or")
        out.append("a square no label names; the detail line says where. "
                   "Nothing is read")
        out.append("from that box, so what the tick records cannot be known "
                   "and the card")
        out.append("cannot be read as it is. Export it again from Match "
                   "Centre, and if that")
        out.append("export is refused the same way, send this verdict "
                   "(--redact takes the")
        out.append("names out) to whoever maintains this tool.")
        return out
    if _picture_share(page) >= _PICTURE_SHARE and not rules:
        # Before the empty page: a scan has no words and no rects, and a scan a
        # scanning app laid the card's words over has every field of the card's.
        out.append("Likeliest cause: an image covers much of the page, as the "
                   "images line")
        out.append("above shows, and the page has no table rules, so it is an "
                   "image of a")
        out.append("card rather than a card. A scan or a screenshot printed to "
                   "PDF cannot be")
        out.append("read: re-export the scorecard from Match Centre instead. A "
                   "draw or a")
        out.append("team list with a photograph on it can look like this too.")
        out.extend(_NOT_A_SCORECARD)
        return out
    if not page.words and not page.rects:
        # Not that the page is empty: its annotations are never read, nor a
        # path that is not a rectangle, so this reader cannot know. A form
        # XObject it reads, and a compressed object stream it refuses before a
        # page gets here (4.183).
        out.append("Likeliest cause: the page draws nothing this reader reads "
                   "a card by: no text, and no rectangle. It may still show a "
                   "card, in what this reader never reads: annotations, where a "
                   "form filled in on screen keeps its fields, or letters and "
                   "lines drawn as outlines. Export the card again from Match "
                   "Centre, and if that export is refused the same way, send "
                   "this verdict (--redact takes the names out) to whoever "
                   "maintains this tool.")
        return out
    if not page.words:
        out.append("Likeliest cause: no text could be decoded. Check the font "
                   "table above:")
        out.append("a font with no usable encoding yields an empty string for "
                   "every glyph,")
        out.append("so the page has rectangles and no words.")
        return out
    if not page.rects and set(found) <= set(_FIELDS_A_BROWSER_PRINTS):
        out.append("Likeliest cause: the page prints words, but none of a "
                   "card's other than")
        out.append("a date or a time, as the header fields above show. It "
                   "paints no")
        out.append("rectangle, and no image large enough to be a picture of a "
                   "card. A draw,")
        out.append("a rules sheet, a fixture list or a team list typed out "
                   "looks like this.")
        out.extend(_NOT_A_SCORECARD)
        return out
    if not page.rects:
        # The card's words and none of its rectangles. Measured on a card whose
        # every rect is filled as a closed path instead, as LibreOffice Draw
        # exports one: 16 of the 17 fields found, and no rect recorded. A
        # picture of the card with its words laid invisibly over it, as a
        # scanning app that reads the text makes one, reads alike, and is told
        # above that it is a picture, since this reader records where one is.
        out.append("Likeliest cause: the page prints a card's words, as the "
                   "header fields")
        out.append("above show, but paints no rectangle, and rectangles are "
                   "all this reader")
        out.append("records of a card's lines and boxes. So its shapes are "
                   "drawn in a way")
        out.append("this reader does not record. A program that opens a card "
                   "and saves it")
        out.append("again can draw them so. Export it again from Match Centre, "
                   "and if that")
        out.append("export is refused too, send this verdict (--redact takes "
                   "the names out)")
        out.append("to whoever maintains this tool. A draw, a rules sheet or a "
                   "team list")
        out.append("typed out, with no table, can look like this too.")
        out.extend(_NOT_A_SCORECARD)
        return out
    if not rules:
        out.append("Likeliest cause: the set table's vertical rules are absent, "
                   "so there is")
        out.append("no grid to read the games columns from. Either this page is "
                   "not a")
        out.append("scorecard, or its rules were dropped by whatever re-saved it.")
        return out
    if not bands:
        out.append("Likeliest cause: the column rules do not describe any row "
                   "bands, so the")
        out.append("number of sets cannot be established.")
        return out
    if not rotation.startswith("0"):
        out.append(f"Note: the page declares a rotation ({rotation}). Every "
                   f"position on it is measured after that turn is applied.")
    missing_core = [name for name in ("division", "round", "date")
                    if name not in found]
    if missing_core:
        out.append(f"Likeliest cause: the header line is missing "
                   f"{in_words(missing_core)}.")
        out.append("That line is the only place the division, the round and the "
                   "date")
        out.append("appear, and a page without it cannot be routed to a "
                   "competition --")
        out.append("which is exactly what a draw, a rules sheet or a team list "
                   "looks like.")
        out.extend(_NOT_A_SCORECARD)
        return out
    out.append("The structure above looks like a scorecard, so the refusal came "
               "from a")
    out.append("stricter test than anything this tool applies. Read the reason "
               "line and")
    out.append("the reader's own summary above together: they name the structure "
               "that")
    out.append("was looked for and where.")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
