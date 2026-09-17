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
the tick labels, the grade, round and court numbers, the weekday, and the
producer line out of the file's metadata. What it removes: names, teams, the club,
the venue, the season label, dates, times, the Match Centre match id, ratings, and
the file's own name and path.

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

import argparse
import contextlib
import io
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from matchcentre import pdf, scorecard                        # noqa: E402
from matchcentre.card import Card, CardError                  # noqa: E402

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

# Header fields, each with a pattern deliberately more tolerant than the reader's.
# `Division`, `Grade` and `Section` all occur across associations, the ball colour
# is optional and so is the `#id`.
HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("date", r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
    ("time", r"\b(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)"),
    ("division", r"\b(?:Division|Grade|Section)\s+([A-Za-z0-9][-A-Za-z0-9]*)"),
    ("round", r"\bRound\s+([A-Za-z0-9][-A-Za-z0-9]*)"),
    ("match id", r"#\s*(\d{3,})"),
    # Digits required: the real template prints the "Court:" label whether or not a
    # court was entered, and a label with nothing after it is not a found field.
    ("court", r"\bCourt:?\s*(\d+)\b"),
    ("result verb", r"\b(DEFEATS|DRAWS WITH|LOSES TO)\b"),
    ("games header", r"\b(Games)\b"),
    ("set label", r"\b(SET)\b"),
    ("totals row", r"\b(TOTAL)\b"),
    ("match winner", r"\b(Match Winner)\b"),
    ("match forfeited", r"\b(Match Forfeited)\b"),
    ("per-set flags", r"\b(Forfeit|Retired|Incomplete)\b"),
    ("roster block", r"\b(Players)\b"),
    ("ratings", r"\b(Rating)\b"),
    ("signatures", r"\b(Signed by)"),
    ("fill-ins", r"\b(Fill-ins?)"),
)

# Discipline markers in a row's outer marker column: `1&2` is a doubles pairing,
# a bare `1` is singles. This is what makes the match format readable off a card.
MARKER_RE = re.compile(r"^\d(?:\s*&\s*\d)?$")

INDENT = "    "
# One label column for the whole report, so the values line up whether or not the
# output is being read in a terminal. Plain ASCII throughout: this gets pasted into
# emails and issue trackers.
LABEL = 14

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
    (re.compile(r"\d{4}-\d{2}-\d{2}"), lambda m: "<date>"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), lambda m: "<date>"),
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


def _shape(text: str) -> str:
    """What a redacted string is replaced by: its size, not its content."""
    words = len(text.split())
    if words > 1:
        return f"<{words} words, {len(text)} chars>"
    return f"<{len(text)} chars>"


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
    refusal reason, including whatever any of them prints next year.

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
    """

    def __init__(self) -> None:
        self._secrets: set[str] = set()

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
        """The file's own path, which the reader's summary prints as `source:`."""
        self._secrets.add(str(path))
        self._secrets.add(str(Path(path).resolve()))
        for part in Path(path).parts:
            self._add(part)
        self._add(Path(path).stem)

    def _add(self, text: str) -> None:
        for candidate in (text.strip(), text.strip("'\"()[]{},.:;!?-_ ")):
            if len(candidate) < 2:
                continue
            if not any(ch.isalpha() for ch in candidate):
                continue                      # a number is a score, or an id
            if candidate.casefold() in TEMPLATE_WORDS:
                continue
            self._secrets.add(candidate)

    # --- using ---

    def scrub(self, text: str) -> str:
        for expression, replace in _IDENTIFYING:
            text = expression.sub(replace, text)
        text = _RATING_RE.sub(_rating_shape, text)
        if self._secrets:
            # Longest first, so a full name is shaped as a name and not as two
            # shaped words; one pass, so a replacement cannot be re-matched.
            # Lookarounds rather than \b because a secret can begin or end with
            # punctuation, where \b means the opposite of what is wanted.
            ordered = sorted(self._secrets, key=len, reverse=True)
            pattern = re.compile(r"(?<!\w)(?:"
                                 + "|".join(re.escape(s) for s in ordered)
                                 + r")(?!\w)")
            text = pattern.sub(lambda m: _shape(m.group()), text)
        return text


def _row(label: str, value: str, *, indent: str = "") -> None:
    print(f"{indent}{label:<{LABEL}}{value}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/doctor.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, metavar="FILE",
                    help="the PDF to explain; repeatable")
    ap.add_argument("--page", type=int, default=None, metavar="N",
                    help="examine only this page, counting from 1")
    ap.add_argument("--redact", action="store_true",
                    help="replace every name, team, date and path with its "
                         "shape, so the report can be pasted into a public "
                         "issue without publishing who played")
    args = ap.parse_args(argv)

    worst = 0
    try:
        for index, path in enumerate(args.paths):
            if index:
                print()
            worst = max(worst, _one_file(path, page=args.page,
                                        redact=args.redact, number=index + 1))
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
    print(redactor.scrub(buffer.getvalue()), end="")
    return code


def _report(path: Path, *, page: int | None, label: str,
            redactor: _Redactor | None) -> int:
    print("=" * 72)
    print(f"file          {label}")
    print("=" * 72)

    if not path.is_file():
        print(f"{INDENT}There is no file at that path.")
        return 1

    raw = path.read_bytes()
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
    for position, (name, subtype, encoding, usable) in enumerate(fonts):
        _row("fonts" if position == 0 else "",
             f"{name:<{width}}  {subtype:<9} {encoding:<20} "
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
    _row("rotation", rotation, indent=INDENT)
    _row("words", str(len(page.words)), indent=INDENT)
    _row("rects", str(len(page.rects)), indent=INDENT)
    if not fonts_ok:
        # Ran both ways in this session on cards written with and without a
        # /ToUnicode map: the reader recovered all 196 words from the map-less one
        # through the font's own glyph order, so a missing map is a risk, not a
        # diagnosis. Which of the two it is depends entirely on this word count.
        if page.words:
            _row("", "a font here has no decode table, yet words came back "
                     "anyway:", indent=INDENT)
            _row("", "the reader fell back to the font's built-in encoding, so "
                     "this is", indent=INDENT)
            _row("", "not what is wrong with the page", indent=INDENT)
        else:
            _row("", "no words, and a font with no decode table: byte codes map "
                     "to", indent=INDENT)
            _row("", "glyph numbers only, so the page reads as blank rather than "
                     "broken", indent=INDENT)

    _rect_census(page)
    rules, bands = _table(page)
    _checkboxes(page, bands)
    found, missing = _header_fields(page)
    _describe(page)
    return _verdict(page, path=path, index=index, rules=rules, bands=bands,
                    found=found, missing=missing, rotation=rotation)


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
        print(f"{INDENT}  {w:>8.2f} x {h:<8.2f} {count:>4}  "
              f"{'filled' if filled else 'stroked'}  {_rect_role(w, h)}")


def _rect_role(w: float, h: float) -> str:
    """A guess at what a rectangle of this shape is for, by shape alone."""
    thin = min(w, h)
    long_side = max(w, h)
    if thin < 1.5 and long_side > 20:
        return "vertical rule" if h > w else "horizontal rule"
    if thin < 1.5:
        return "checkbox outline hairline"
    if abs(w - h) <= 0.35 * long_side and long_side <= 20:
        return "ticked checkbox fill"
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
                     f"against {right:g}pt", indent=INDENT)
            _row("", "on the mirroring cell, so the wider one is two cells with "
                     "no rule", indent=INDENT)
            _row("", "between them", indent=INDENT)
            break
    if xs[0] < 0 or xs[-1] > page.width:
        _row("", f"rules run outside the 0 to {page.width:g}pt page: the table is "
                 f"shifted", indent=INDENT)
        _row("", "off the sheet, so a column is clipped and its column is "
                 "unreadable", indent=INDENT)

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
                     "was", indent=INDENT)
            _row("", "missed or invented, so the per-set status read off this card",
                 indent=INDENT)
            _row("", "cannot be trusted", indent=INDENT)
    for fill in sorted(ticks, key=lambda r: (round(r.cy, 1), r.cx)):
        label = _label_right_of(page, fill)
        _row("", f"ticked at x {fill.cx:7.2f} y {fill.cy:7.2f}  "
                 f"{label or '(no label to its right)'}", indent=INDENT)


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


def _label_right_of(page: pdf.Page, fill: pdf.Rect,
                    gap: float = 40.0) -> str:
    """The nearest word to a tick's right on the same line, which is its label."""
    candidates = [w for w in page.words
                  if abs(w.cy - fill.cy) <= max(4.0, fill.h)
                  and fill.x1 <= w.x0 <= fill.x1 + gap]
    if not candidates:
        return ""
    nearest = min(candidates, key=lambda w: w.x0)
    row = [w.text for w in page.words
           if abs(w.cy - nearest.cy) <= 2.0
           and nearest.x0 <= w.x0 <= nearest.x0 + gap * 2]
    return " ".join(row[:3])


def _header_fields(page: pdf.Page) -> tuple[list[str], list[str]]:
    """Which header fields a tolerant pattern can find, and which it cannot."""
    text = page.text()
    found: list[str] = []
    missing: list[str] = []
    print(f"{INDENT}header fields")
    for name, pattern in HEADER_FIELDS:
        match = re.search(pattern, text)
        if match:
            found.append(name)
            print(f"{INDENT}  {name:<16} found     {match.group(1)!r}")
        else:
            missing.append(name)
            print(f"{INDENT}  {name:<16} not found")
    return found, missing


def _describe(page: pdf.Page) -> None:
    """What the reader itself makes of the page structure."""
    print(f"{INDENT}reader's own summary")
    try:
        summary = scorecard.describe(page)
    except Exception as exc:                     # noqa: BLE001
        # The one place a bare catch is right: the reader raising while describing
        # a page is itself the diagnosis, and swallowing it into a traceback would
        # hide every section above from whoever needs them.
        print(f"{INDENT}  scorecard.describe() raised "
              f"{type(exc).__name__}: {exc}")
        return
    for line in summary.splitlines():
        print(f"{INDENT}  {line}")


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
    turned = ", ".join(f"{v}" for v in values)
    return (f"{turned} degrees -- the page is turned, and every coordinate on it "
            f"with it")


# --- verdict ----------------------------------------------------------------


def _verdict(page: pdf.Page, *, path: Path, index: int,
             rules: list[float], bands: list[tuple[float, float]],
             found: list[str], missing: list[str], rotation: str) -> int:
    """Whether the page reads as a scorecard, and if not, the likeliest cause."""
    print(f"{INDENT}verdict")
    try:
        card = scorecard.read_page(page)
    except CardError as exc:
        print(f"{INDENT}  This page was refused: {exc.reason}")
        if exc.detail:
            print(f"{INDENT}  detail: {exc.detail}")
        for line in _causes(page, rules=rules, bands=bands, found=found,
                            rotation=rotation):
            print(f"{INDENT}  {line}")
        return 1

    print(f"{INDENT}  This page reads as a scorecard.")
    facts = [
        ("division", card.division or "(none printed)"),
        ("round", card.round or "(none printed)"),
        ("date", f"{card.date} {card.weekday}".strip() or "(none printed)"),
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
        print(f"{INDENT}    {label:<14}{value}")

    available = card.cross_checks_available
    failed = card.cross_checks_failed
    print(f"{INDENT}    {'cross-checks':<14}"
          f"{len(available)} available, {len(failed)} disagreeing")
    for check in card.checks:
        state = ("not printed on this card" if not check.available
                 else "agrees" if check.agrees else "DISAGREES")
        print(f"{INDENT}      {check.name:<14}{state}"
              f"{'  ' + check.detail if check.detail else ''}")
    for warning in card.warnings:
        print(f"{INDENT}    warning     {warning}")
    if not available:
        print(f"{INDENT}  No independent cross-check is printed on this card, so "
              f"its numbers")
        print(f"{INDENT}  rest on the set rows alone (F6 warns rather than "
              f"refuses).")
    elif failed:
        print(f"{INDENT}  A cross-check disagrees with the set rows. The card "
              f"states its")
        print(f"{INDENT}  result more than once and the readings differ, so "
              f"something was")
        print(f"{INDENT}  misread; build.py reports this as an error rather than "
              f"scoring it.")
    return 0


def _causes(page: pdf.Page, *, rules: list[float],
            bands: list[tuple[float, float]], found: list[str],
            rotation: str) -> list[str]:
    """The likeliest explanations for a refusal, most likely first.

    Ordered by how completely each one accounts for the refusal: a page with no
    text at all has one explanation, and a page with text but no table rules has a
    different one. Anything left over is a page that is simply not a scorecard,
    which is the common and harmless case -- a club keeps its draw and its rules
    sheet in the same folder as its cards.
    """
    out: list[str] = []
    if not page.words and not page.rects:
        out.append("Likeliest cause: the page is empty, or its content is inside "
                   "a structure")
        out.append("this reader skips (a form XObject, or a compressed object "
                   "stream).")
        return out
    if not page.words:
        out.append("Likeliest cause: no text could be decoded. Check the font "
                   "table above:")
        out.append("a font with no usable encoding yields an empty string for "
                   "every glyph,")
        out.append("so the page has rectangles and no words.")
        return out
    if not page.rects:
        out.append("Likeliest cause: the page paints no rectangles, so it is an "
                   "image of a")
        out.append("card rather than a card. A scan or a screenshot printed to "
                   "PDF cannot be")
        out.append("read: re-export the scorecard from Match Centre instead.")
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
                   f"position on it is")
        out.append("measured after that turn is applied.")
    missing_core = [name for name in ("division", "round", "date")
                    if name not in found]
    if missing_core:
        out.append(f"Likeliest cause: the header line is missing "
                   f"{', '.join(missing_core)}. That line")
        out.append("is the only place the division, the round and the date "
                   "appear, and a")
        out.append("page without it cannot be routed to a competition -- which "
                   "is exactly")
        out.append("what a draw, a rules sheet or a team list looks like. If "
                   "this is one of")
        out.append("those, nothing is wrong: build.py counts it as refused and "
                   "carries on.")
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
