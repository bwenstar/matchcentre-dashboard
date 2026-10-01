"""One scorecard page, read the way the page itself describes it, or refused.

The predecessor of this module was a good reader wired to one club, and three
things made it so. All three are gone from here.

**Its grid failed open.** When the count of column rules was not exactly eight it
substituted eight hardcoded x positions. A card whose content sat 25-60pt away
from where those constants expected it then parsed as a confidently *reversed*
scoreline -- 6-2 read as 2-6 -- with no warning, and only a club-specific TOTAL
row caught it downstream. So no position is a constant here: the grid comes from
the table's own rules, the games and label columns are found by the headings
printed above them, and a grid that will not resolve raises :class:`CardError`
(rule F5). Sizes and tolerances are constants; positions never are.

**Its shape was fixed.** Four rows, in the order doubles, singles, singles,
doubles. The number of rows is now however many row bands the card draws, and each
row's discipline is read from its own marker column, so the match format is
evidence rather than an assumption -- see :func:`~matchcentre.format.from_rows`.
When a caller says which format it expects and the card describes another, that
is an error naming both readings, because a card with a row missed and a club that
changed format look identical and need opposite responses.

**One club's words were load-bearing.** The court was located by matching the
literal word ``University``, and the header line had to say ``Division``. Both are
read structurally now.

What comes out is a :class:`~matchcentre.card.Card`: a transcription in the page's
own vocabulary. Printed names, not slugs; no scoring, no competition, no
:class:`~matchcentre.model.Match`. The one judgement this module makes is whether
it believes the page at all, and it records that judgement twice over -- as a
:class:`~matchcentre.card.CrossCheck` for every independent confirmation the card
offers (rule F6), and as a :class:`~matchcentre.card.Rejection` for a file it
could not read at all (rule F4).
"""

from __future__ import annotations

import datetime
import errno
import os
import re
import stat
import string
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import pdf
from .card import (WEEKDAYS, Card, CardError, CrossCheck, ParsedSet, Rejection,
                   RosterEntry, Totals, date_forms, states_no_result,
                   thin_card_prints, verb_side)
from .competition import decimal_figure
from .format import SET_RULES, FormatError, MatchFormat, from_rows
from .model import (SetStatus, forfeit_match_winner, forfeit_set_winner,
                    stopped_short)
from .scope import echoed, in_words

__all__ = ["read", "read_page", "read_tree", "unread_files", "files_under",
           "open_folder", "describe"]


# --- template invariants -------------------------------------------------
# Every value below is a size, a count or a tolerance. There is deliberately no
# x or y position anywhere in this file: that is what rule F5 forbids, and what
# the predecessor's `_FALLBACK_COLUMNS` was.

_RULE_MAX_WIDTH = 2.0        # a table rule is a hairline, not a fill
_RULE_MIN_HEIGHT = 6.0       # below this a "rule" is a tick mark or a comma
_RULE_HEIGHT_TOL = 1.5       # outer and inner cell borders differ by about 1.2pt
_RULE_HEIGHT_SPREAD = 0.05   # ... or by 5%, whichever is the more generous
_BAND_JOIN_TOL = 1.5         # row bands of one table are drawn edge to edge
_BAND_NEST_TOL = 1.0         # an inner border sits this far inside its outer one
_CELL_INSET = 1.0            # clear of the rule when reading a cell's contents
_MIN_RULES = 4               # 4 rules make 3 cells; less is not a table at all.
                             # The real minimum comes out of resolving the named
                             # columns, which needs six cells and so seven rules.

_HEADING_REACH = 40.0        # a column heading sits this close above the table
_GAMES_CELL_RATIO = 3.0      # the numeric columns are all of a size

_HAIRLINE_MAX_HEIGHT = 1.5   # a checkbox edge is under a point thick
_BOX_MIN_SIDE = 3.0
_BOX_MAX_SIDE = 20.0
_BOX_TOLERANCE = 0.6         # measured float noise on the template's own boxes
_BOX_PAIR_TOLERANCE = 0.8    # top edge to bottom edge, allowing for the hairline
_BOX_LABEL_GAP = 2.0         # box sides, as a ratio, so it scales with the card:
                             # the template's 16.0pt gap for its 8.07pt box
_BOX_ROW_TOL = 1.5           # a box and its label share a vertical centre

_BASELINE_TOL = 0.6          # two words share a printed line this far apart
_TOTALS_REACH = 20.0         # headings above and figures below the TOTAL word
_TOTALS_COLUMN_TOL = 20.0    # a figure belongs to the heading it sits under
_COURT_REACH_ABOVE = 30.0    # the court value printed above its own label
_COURT_REACH_BELOW = 20.0    # ... or below it, on the ruled line

# The three per-set status labels and the four whole-match ones, as the template
# prints them. Matched case-insensitively and normalised back to these spellings
# so everything downstream can look a box up by name.
_SET_STATUS_LABELS = ("Forfeit", "Retired", "Incomplete")
_MATCH_FLAG_LABELS = ("Winner", "Forfeited", "Cancelled", "Disputed")


# Every card prints, per set row, three status boxes on each side; then Match
# Winner and Match Forfeited for each side, and one each of Match Cancelled and
# Match Disputed. Four rows make 30 boxes, six make 42. The predecessor hardcoded
# 30, which is the arithmetic below with the row count frozen at four.
_CENTRED_FLAGS = ("Match Cancelled", "Match Disputed")

# Where a box is on the card, as the reading asks about it: its label, the index
# of its set row and its side. A whole-match flag has no row, and the two centred
# ones no side either. A square no label names has the empty label.
_Place = tuple[str, int | None, str | None]


def _boxes_needed(rows: int) -> tuple[_Place, ...]:
    """Every box a card with ``rows`` set rows prints, by its place on the card.

    By place, and not by how many of each label, because a card can name as many
    boxes as it prints, and as many under every label, and not the ones it prints:
    with a square gone from row 1 and a box of the same label drawn again off the
    table, every label names as many as the card has. The places are the ones
    :func:`_read_row` and the flags read a tick from, so a place with no box
    named in it is a place a tick would go unseen.
    """
    return (tuple((label, row, side) for row in range(rows)
                  for side in ("home", "away") for label in _SET_STATUS_LABELS)
            + tuple((flag, None, side) for flag in ("Match Winner", "Match Forfeited")
                    for side in ("home", "away"))
            + tuple((flag, None, None) for flag in _CENTRED_FLAGS))


def _expected_boxes(rows: int) -> int:
    """How many checkboxes a card with ``rows`` set rows prints."""
    return len(_boxes_needed(rows))


def _in_band(rect: pdf.Rect, band: tuple[float, float]) -> bool:
    """Is ``rect`` in the set row whose band is ``band``? The one test of it."""
    return band[0] <= rect.cy <= band[1]


def _place(box: _Box, grid: _Grid) -> _Place | None:
    """The place on the card ``box`` is in, or ``None`` for a per-set box in no
    set row, which no row reads."""
    if box.label in _CENTRED_FLAGS:
        return (box.label, None, None)
    side = grid.side_of(box.rect.cx)
    if box.label not in _SET_STATUS_LABELS:
        return (box.label, None, side)
    return next(((box.label, index, side) for index, band in enumerate(grid.bands)
                 if _in_band(box.rect, band)), None)


def _unnamed(boxes: Sequence[_Box], grid: _Grid) -> list[_Place]:
    """Every place on the card that no box named is in, in the card's order."""
    named = {_place(box, grid) for box in boxes}
    return [place for place in _boxes_needed(len(grid.bands))
            if place not in named]


# --- printed text patterns -----------------------------------------------

# ``Division C / Yellow Ball / Round 1 / #3665611`` arrives as separate words, so
# these run against a re-joined line. Associations differ in wording -- Division,
# Grade, Section and Div all occur -- the ball colour is optional and so is the
# id, but a card with no round cannot be placed in a season, so the round stays
# mandatory.
#
# Some associations name a division twice over: ``Division 3 Grade B`` is one
# competition and ``Division 3 Grade A`` is another, so the grade is half of the
# name and dropping it merges two ladders into one. Hence the repeated group. It
# repeats only on another designation word, not on whatever else the line holds:
# taking the rest of the line would swallow the ball colour, the round and the id,
# and taking one more bare word would swallow ``Yellow`` off a card that prints
# ``Division C Yellow Ball``. `[^\s/,]+` also stops the first alternative from
# eating a comma or a slash, which is what separates the fields when a template
# leaves the spaces out. The *first* designation word is still dropped -- `Division
# C` is division `C`, as it always was -- and every later one is kept, because it is
# the only thing that tells the grades of one section apart.
#
# The word and its value are separated by a colon, with or without a space either
# side, or by a space with or without a dot: ``Division: C`` and ``Division C`` are
# one division, and inside the repeated group the separator is read as the space it
# stands for, so ``Division 3 Grade: B`` is ``3 Grade B`` and not a second ladder.
# So is a slash or a comma before the second designation, which a template printing
# the grade as a field of its own puts there: ``Division 3 / Grade A`` is ``3 Grade
# A``, and ``Division 3 / Grade B`` is not the same ladder.
# The separator is never optional -- without one ``Divisional`` is the word
# ``Division`` and the division ``al``. A value carries a letter or a digit and is
# never one of these label words, so ``Division : C`` is not the division ``:`` and
# ``Division: Round: 1``, a template with the field left blank, is not the division
# ``Round``. ``Rnd`` and ``Rd`` are rounds only before a number, because ``Rd`` is
# also a street on a letterhead; ``Round`` still takes any word, since a final is
# ``Round GF``, but not ``Round Robin``, which is a format a title names. ``C Grade``
# is division ``C``: a capital letter or a number standing alone and nothing longer,
# since a title such as ``Example District Pennant Grade`` puts a word before
# ``Grade`` that is no division, and ``a Grade event`` the article. A line is read
# from the left, so ``C Grade Yellow Ball`` is ``C`` and not ``Yellow``, and
# ``Division C Grade`` is still ``C``; a letter or a number after the word is kept,
# as it is after ``Division 3 Grade``, so ``3 Grade B`` is ``3 Grade B`` and not half
# of it. Those three short forms are trusted less than the long ones: a card that
# splits its identity over two lines is paired from the long forms alone, because the
# letterhead printing ``14 Example Rd`` beside court ``01`` would give a card that
# prints no round one, and a title ending ``B Grade`` a card that prints no division.
# And the identity line is the nearest line printing either half, because that is
# where the template prints it, below the letterhead, the title and the season line,
# and those can print both; the team names and a line of contact details are below
# it, and are not read for it. ``R1`` is not a round: as a short form it makes
# ``Division R1`` its own round. ``Pool`` is not a division: it is a word in clubs'
# names, and no card measured prints it.
_DIVISION_WORDS = ("Division", "Div", "Grade", "Section")
_GRADE_AFTER_WORD = "Grade"
_ROUND_WORD = "Round"
_ROUND_ABBREVIATIONS = ("Rnd", "Rd")
_NOT_A_ROUND = "Robin"
_SEPARATOR = r"(?:\s*:\s*|\.?\s+)"
_JOINER = r"(?:\s*[/,]\s*|\s+)"
_LABEL_WORDS = (*_DIVISION_WORDS, _ROUND_WORD, *_ROUND_ABBREVIATIONS)
_LABEL_WORD = rf"(?:{'|'.join(_LABEL_WORDS)})\b"
_LABEL_WORD_RE = re.compile(rf"\b{_LABEL_WORD}", re.IGNORECASE)
_VALUE = rf"(?!{_LABEL_WORD})(?=[^\s/,]*[^\W_])[^\s/,]+"
_DESIGNATION_WORD = rf"(?:{'|'.join(_DIVISION_WORDS)})"
_DESIGNATION = rf"{_DESIGNATION_WORD}{_SEPARATOR}"
_DIVISION_RE = re.compile(
    rf"\b{_DESIGNATION}(?P<division>{_VALUE}(?:{_JOINER}{_DESIGNATION}{_VALUE})*)",
    re.IGNORECASE)
_DESIGNATION_WORD_RE = re.compile(
    rf"{_JOINER}\b(?P<word>{_DESIGNATION_WORD}){_SEPARATOR}", re.IGNORECASE)
_SHORT_VALUE = r"(?:(?-i:[A-Z])|\d{1,2})\b"
_GRADE_AFTER_RE = re.compile(
    rf"\b(?P<division>{_SHORT_VALUE}(?=\s+{_GRADE_AFTER_WORD}\b)"
    rf"(?:\s+{_GRADE_AFTER_WORD}{_SEPARATOR}{_SHORT_VALUE})?)", re.IGNORECASE)
_ROUND_RE = re.compile(
    rf"\b{_ROUND_WORD}{_SEPARATOR}(?!{_NOT_A_ROUND}\b)(?P<round>{_VALUE})",
    re.IGNORECASE)
_ROUND_ABBREVIATED_RE = re.compile(
    rf"\b(?:{'|'.join(_ROUND_ABBREVIATIONS)}){_SEPARATOR}(?=\d)(?P<round>{_VALUE})",
    re.IGNORECASE)
_MATCH_ID_RE = re.compile(r"#\s*(?P<id>\d{3,})")
# Dates: the forms card.DATE_FORMS lists, and no others (see _find_date). The
# numeric one takes one separator used twice, and is fenced so that it cannot be
# cut out of a longer run of figures such as ``2026/08/18`` or ``1/18/08/2026``.
_DAY_FIRST_DATE_RE = re.compile(
    r"(?<![\d/.-])(?P<a>\d{1,2})(?P<sep>[/.-])(?P<b>\d{1,2})(?P=sep)"
    r"(?P<y>\d{4}|\d{2})(?!\d|[/.-]\d)")
_ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_WORDED_DATE_RE = re.compile(
    r"(?<![\w.])(?P<d>\d{1,2})\s+(?P<month>[A-Za-z]+)\.?,?\s+(?P<y>\d{4})\b")
_SEASON_RE = re.compile(
    r"\b(?:Season|Summer|Winter|Autumn|Spring)\b[ \w-]{0,24}", re.IGNORECASE)
_RATING_RE = re.compile(r"^\d+\.\d+$")
_CONTACT_RE = re.compile(r"@|www\.|https?:|^\+?\d[\d ()+-]{6,}$")

# A row marker of ``1&2`` is a pairing and therefore doubles; a bare ``1`` is
# singles. The separator varies between templates, so any of these joins two
# player numbers into a pair.
_PAIR_MARKER_RE = re.compile(r"^\d+(?:\s*[&+/,]\s*\d+)+$")
_SINGLE_MARKER_RE = re.compile(r"^\d+$")

# What Match Centre prints in a name cell nobody filled in. It is the template's
# own empty state, not a person, and it appears on a card for a fixture whose
# players were never entered -- most often one that was called off before the
# names went in. Read as a name it becomes a player: a squad of one, on no team,
# whose only opponent is itself.
_UNNAMED_MARKS = ("unspecified player", "unspecified players", "unspecified")

# The two words a heading in the foot of the card uses to say which team it is
# for, as in `Fill-ins (Home Team)` beside `Fill-ins (Away Team)`. The template
# prints each of those headings once per side, reading the same except for this
# word, and two squad members' lines never differ by exactly that. It is these two
# words and no others: `Visitors`, `Host` or a team's name is not a side word, and
# a heading that says one is caught instead by holding what no name holds (see
# _NOT_IN_A_NAME).
_SIDE_WORD_RE = re.compile(r"\b(?:home|away)\b")

# What no player's name on a roster line has in it, beside a word with no letter.
# The one bracket the template prints beside a name is the captain's `(C)`, which is
# read off before the name is; a heading's `(Visitors)`, `(Host)` or `Reserves:` is
# not. A heading is also never rated, so only an unrated line is judged by this.
_NOT_IN_A_NAME = frozenset("()[]{}:")

# The one warning for a roster block with nothing under it to say where it ends.
# Public because the tests and the hostile corpus name it, and one wording kept in
# one place is what stops them drifting apart.
ROSTER_BLOCK_UNBOUNDED = (
    "no 'Fill-ins' or 'Comments' heading below 'Players', and at least one squad "
    "has no heading under it, so nothing says where the roster block ends and "
    "neither team's registered squad was read from this card")


# --- geometry read off the card ------------------------------------------


@dataclass(frozen=True, slots=True)
class _Cell:
    """One column of the set table, between two of its own rules."""

    x0: float
    x1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def inner(self) -> tuple[float, float]:
        """Bounds inset clear of the rules, for selecting a cell's words."""
        return (self.x0 + _CELL_INSET, self.x1 - _CELL_INSET)

    def holds(self, x: float) -> bool:
        return self.x0 <= x <= self.x1


@dataclass(frozen=True, slots=True)
class _Grid:
    """The set table as the card drew it, with every column identified by role.

    Nothing in here is a constant. ``rules`` are the x positions of the table's
    own vertical hairlines, ``bands`` the vertical spans they cover, and the
    named cells are resolved from the headings printed above the table -- so a
    template with a different number of columns either resolves or is refused,
    and is never guessed at.
    """

    rules: tuple[float, ...]
    bands: tuple[tuple[float, float], ...]
    cells: tuple[_Cell, ...]
    home_names: _Cell
    home_games: _Cell
    label: _Cell
    away_games: _Cell
    away_names: _Cell
    home_marker: _Cell | None
    away_marker: _Cell | None

    @property
    def top(self) -> float:
        return self.bands[0][0]

    @property
    def bottom(self) -> float:
        return self.bands[-1][1]

    @property
    def divider(self) -> float:
        """The card's axis of symmetry: the middle of the SET label column.

        Everything the template prints in pairs -- the per-set status boxes, the
        Match Winner boxes, the two rosters -- is home on one side of this and
        away on the other, so it is the only test needed for a side.
        """
        return self.label.centre

    def side_of(self, x: float) -> str:
        return "home" if x < self.divider else "away"

    def marker(self, side: str) -> _Cell | None:
        return self.home_marker if side == "home" else self.away_marker


@dataclass(frozen=True, slots=True)
class _Box:
    """One checkbox: which label owns it, where it is, and whether it is filled."""

    label: str
    rect: pdf.Rect
    ticked: bool


@dataclass(frozen=True, slots=True)
class _Header:
    """The identity block: everything printed above the banner."""

    division: str
    round: str
    match_id: str
    date: str
    weekday: str
    court: int | None
    venue: str
    club_hint: str
    season_hint: str


@dataclass(frozen=True, slots=True)
class _Banner:
    """The two team names and the result verb printed between them."""

    home: str
    away: str
    verb: str


# --- entry points ---------------------------------------------------------


def read(path: str | Path, *,
         expect: MatchFormat | None = None) -> tuple[list[Card], list[Rejection]]:
    """Every page of one PDF, as cards and reasons why not.

    Never raises. An unreadable file, a PDF that is not a scorecard and a
    scorecard page that fails a structural check all come back as a
    :class:`~matchcentre.card.Rejection` carrying a reason a human can act on,
    because the caller is walking a directory and one bad file must not cost the
    season (rule F4). There is no partial card: a page either transcribes
    completely or it is refused.
    """
    target = Path(path)
    try:
        count = pdf.page_count(target)
    except pdf.PdfError as exc:
        return [], [Rejection(str(target), "not a readable PDF", detail=str(exc))]
    except OSError as exc:
        return [], [Rejection(str(target), "could not be opened",
                              detail=exc.strerror or str(exc))]
    except Exception as exc:                      # noqa: BLE001 - see below
        # pdf.py raises PdfError for the shapes it knows it cannot read, but a
        # truncated or hand-edited file can still trip an index or a conversion
        # deep inside it. One malformed byte must not abort a whole build, and a
        # rejection naming the exception is diagnosable; a traceback out of a
        # directory walk is not.
        return [], [Rejection(str(target), "could not be parsed as a PDF",
                              detail=f"{type(exc).__name__}: {exc}")]

    cards: list[Card] = []
    failures: list[tuple[int, str, str]] = []
    for index in range(count):
        try:
            page = pdf.extract(target, page=index)
            cards.append(read_page(page, expect=expect))
        except CardError as exc:
            failures.append((index, exc.reason, exc.detail))
        except pdf.PdfError as exc:
            failures.append((index, "the page could not be extracted", str(exc)))
        except Exception as exc:                  # noqa: BLE001 - as above
            failures.append((index, "the page could not be read",
                             f"{type(exc).__name__}: {exc}"))

    if cards or count == 1:
        rejections = []
        for index, reason, detail in failures:
            if index == 0 and count > 1:
                # Page 0 is the whole file to everything that prints a refusal, so
                # a first page among others is named in its detail or nowhere.
                detail = f"page 1: {detail}" if detail else "page 1"
            rejections.append(Rejection(str(target), reason, detail=detail,
                                        page=index))
    elif failures:
        # No page of this file was a scorecard, so it is one thing that is not a
        # card rather than N broken pages -- the club's rules and draw PDFs land
        # in data/ too, and reporting a four-page team list four times buries the
        # cards that really did fail.
        index, reason, detail = failures[0]
        pages = f"{count} pages, none of them a scorecard"
        rejections = [Rejection(str(target), reason,
                                detail=f"{pages}; page 1: {detail}" if detail
                                else pages)]
    else:
        rejections = []
    return cards, rejections


# Suffixes that mean "this file is still being downloaded". The browser writes the
# bytes under one of these and renames the file only when the transfer completes, so
# what is on disk is a fraction of a card. Chrome and Opera add their own extension,
# Edge uses .partial, Safari .download.
_DOWNLOADING = frozenset({".crdownload", ".opdownload", ".partial", ".download"})

# The same idea under a name that belongs to everybody. Firefox uses .part, and .tmp
# is written by every program there is, so for these two the rest of the name has to
# carry the evidence: `card.pdf.part` is a missing scorecard, `notes.tmp` is somebody's
# scratch file, and calling the second one a missing card would put a fault in the exit
# status that nothing in the folder supports.
_MAYBE_DOWNLOADING = frozenset({".part", ".tmp"})


def _is_pdf(path: Path) -> bool:
    """Whether the reader should be offered this file.

    Case-insensitively, because a Windows share hands back ``.PDF`` and a card nobody
    sees is worse than a card that fails.
    """
    return path.suffix.lower() == ".pdf"


def _unfinished_download(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in _DOWNLOADING:
        return True
    return suffix in _MAYBE_DOWNLOADING and path.stem.lower().endswith(".pdf")


def open_folder(root: str | Path) -> None:
    """Raise the ``OSError`` that listing ``root`` meets, if it meets one.

    ``Path.rglob`` does not raise it: it skips a directory it may not list, the
    one it was asked to walk included, so a folder this account may not read came
    back as an empty one and was reported as "no scorecards". Opened once here,
    before the walk, so the refusal is the errno itself.

    And its first entry looked at, because a folder whose names can be read and
    whose entries cannot be reached (mode 444) is no more open than one at 000,
    and the walk would refuse it as a folder under it, exit 1, where 000 exits 2.
    Said against ``root``, as 000 is, and not against whichever entry the disk
    lists first.
    """
    with os.scandir(root) as listing:
        for entry in listing:
            try:
                entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise OSError(exc.errno, exc.strerror, str(root)) from None
            break


def _unlisted(folder: str | Path, exc: OSError) -> Rejection:
    """A folder the walk could not list, refused by its own name.

    The detail is the error's own words, which are the same whichever entry it was
    met at: at mode 444 it is met at the first file inside, and which file that is
    depends on the order the disk lists them in. The folder is the refusal's path,
    so the detail does not say it again, as every refusal of an ``OSError`` here.
    """
    return Rejection(str(Path(folder)), "the directory could not be listed",
                     detail=exc.strerror or str(exc), folder=True)


# The errors that say an entry is not there to be a file, as pathlib said them until
# Python 3.14: a link to nothing or through a file, a link that loops, a name gone
# while the walk was under way.
_NOT_THERE = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})


def _is_file(path: Path) -> bool:
    """Whether ``path`` is a file, raising the ``OSError`` that says nobody can know.

    Not ``Path.is_file``, which raised ``EACCES`` until Python 3.14 and from 3.14
    answers False to every error: under 3.14 a folder at mode 444, whose entries
    cannot be reached, was a folder of cards skipped in silence again, exit 0. So
    the stat is this module's own, and the only errors it answers False to are
    the ones that say there is nothing there to be a file.
    """
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError as exc:
        if exc.errno in _NOT_THERE:
            return False
        raise


# The folders a drive keeps for itself at its top, beside the hidden ones, which a
# file browser does not show. A ``data/`` that is a drive's own top folder has them,
# and the account that runs the build is not meant to open them. Read when they can
# be listed, as any folder is.
_THE_SYSTEMS = frozenset({"lost+found", "System Volume Information", "$RECYCLE.BIN"})


def _entries(base: Path) -> tuple[list[Path], list[Rejection]]:
    """Every file under ``base`` in path order, and each folder it could not list.

    Order fixes tie-breaks and so fixes output, and it is the order the report names
    files in, so it is settled here rather than at each caller.

    Walked with ``os.walk`` and its ``onerror``, because ``Path.rglob`` skips a
    folder it may not list without a word: a round of cards at mode 000 was a
    season three cards short that exited 0. Every entry is stat'ed, the folders
    too, so a folder whose names can be read and whose entries cannot be reached
    (mode 444) is refused once, by its own name, and not once for each folder in
    it. A ``base`` that cannot be listed is the only refusal, as one that is not
    there is. A folder the system keeps for itself, or one inside it, is left
    unread and unsaid when it cannot be listed, as it always was: refused, it
    failed every run on a drive's top folder, with advice to open the system's own.
    """
    if not base.is_dir():
        return [], [Rejection(str(base), "no such directory to read cards from",
                              folder=True)]
    files: list[Path] = []
    unlisted: list[Rejection] = []

    def refuse(folder, exc: OSError) -> None:
        if not any(part.startswith(".") or part in _THE_SYSTEMS
                   for part in Path(folder).relative_to(base).parts):
            unlisted.append(_unlisted(folder, exc))

    for top, folders, names in os.walk(
            base, onerror=lambda exc: refuse(exc.filename, exc)):
        try:
            files.extend(p for p in (Path(top, n) for n in (*folders, *names))
                         if _is_file(p))
        except OSError as exc:
            refuse(top, exc)
            folders.clear()
    return sorted(files, key=str), unlisted


def read_tree(root: str | Path, *,
              expect: MatchFormat | None = None
              ) -> tuple[list[Card], list[Rejection]]:
    """Every ``*.pdf`` under ``root``, sorted by path so a run is reproducible.

    A file whose name says it is a download in progress is refused rather than
    skipped. It is the one non-PDF in the folder that stands for a card: the club
    exported it, the browser has not finished writing it, and every figure on the
    dashboard is a fixture short. Silence there is the failure this whole program is
    arranged against, so it is counted and named like any other refusal.

    Everything else that is not a PDF is left for :func:`unread_files`, which the
    report lists once without calling any of it a missing card.
    """
    base = Path(root)
    entries, unlisted = _entries(base)
    cards: list[Card] = []
    rejections: list[Rejection] = list(unlisted)
    for path in entries:
        if _is_pdf(path):
            found, refused = read(path, expect=expect)
            cards.extend(found)
            rejections.extend(refused)
        elif _unfinished_download(path):
            rejections.append(Rejection(
                str(path),
                "this looks like a download that never finished, so the card it "
                "would have been is not here",
                detail="A browser writes a download under a temporary name like "
                       "this one and renames it when the transfer completes. "
                       "Export the match again from matchcentre.tennis.com.au, let "
                       "it finish, and delete this file."))
    # In path order, the folders among the cards, as the report lists them: the page
    # lists them in this order, and the walk meets the folders in the disk's.
    return cards, sorted(rejections, key=lambda r: (r.path, r.page))


def unread_files(root: str | Path) -> list[Path]:
    """Files under ``root`` that the reader was never offered, in path order.

    Neither the PDFs nor the unfinished downloads: those are read and refused by name.
    What is left is a club's own furniture -- the draw as a spreadsheet, a `.DS_Store`,
    a photograph of the noticeboard -- and one case that matters, a club that saved the
    Match Centre page instead of exporting it and now has an `.html` or a `.png` and a
    run that read nothing from it.

    Returned rather than refused, and listed rather than counted into the exit status:
    a club is entitled to keep its own files here, and a run that exits 1 over a
    spreadsheet is a run whose exit status has stopped meaning anything. A missing
    directory returns nothing, because ``read_tree`` has already said so.
    """
    entries, _ = _entries(Path(root))
    return [p for p in entries
            if not _is_pdf(p) and not _unfinished_download(p)]


def cards_waiting(root: str | Path) -> int:
    """How many files under ``root`` this program would offer the reader.

    For one caller: a run that found no cards where it was told to look, deciding
    whether some *other* folder is worth pointing at. That is a question about a
    folder nobody asked to read, so it is counted and not read -- opening a club's
    whole season to print one number would be slower than the build it is declining
    to do, and a card that turns out to be unreadable is still a card waiting.

    Beside ``unread_files`` and sharing its two helpers, so that the count and the
    reader cannot disagree about what a scorecard looks like: a club whose exports
    arrive as ``.PDF`` is read by one and would have been invisible to the other.
    Unfinished downloads are not counted, for the same reason they are not read: a
    folder holding nothing but half-written files has no card in it yet, and a
    sentence claiming three would be wrong for as long as the browser is still
    writing them. A missing or unreadable directory is nothing waiting, which is
    the answer that makes the caller say nothing at all -- and needs no branch of
    its own, because a folder that cannot be listed comes back with no entries in
    it. The reason there is no list is the caller's business there and not here.
    """
    entries, _ = _entries(Path(root))
    return sum(1 for p in entries if _is_pdf(p))


def files_under(root: str | Path) -> list[Path] | None:
    """Every file under ``root`` the reader would be offered, in its order.

    None when there is no list -- no such folder, or one that cannot be listed --
    which is a different answer from an empty one. A folder under ``root`` that
    cannot be listed is not that: it is in the list, in its files' place. For
    ``tools/scheduled.py``, deciding whether anything a build reads has moved since
    the last one: the list :func:`read_tree`, :func:`unread_files` and
    :func:`cards_waiting` share, under a public name, so that a change to what the
    reader walks is a change to what the scheduler watches too. Every file and not
    only the PDFs, because the report names the others. A folder in its files'
    place is watched by its own name, so a mode that changes either way is a change.
    """
    base = Path(root)
    found, unlisted = _entries(base)
    if any(r.path == str(base) for r in unlisted):
        return None
    return sorted([*found, *(Path(r.path) for r in unlisted)], key=str)


def read_page(page: pdf.Page, *, expect: MatchFormat | None = None) -> Card:
    """Transcribe one page, or raise :class:`~matchcentre.card.CardError`.

    Used directly by the tests and by ``tools/doctor.py``. The order of the
    checks is the order of confidence: the set table first, because a page with
    no table is not a scorecard at all and saying so is the most useful thing
    this function can do for anything else that lands in ``data/``.
    """
    warnings: list[str] = []
    grid = _grid(page, warnings)
    header = _read_header(page, grid, warnings)
    banner = _read_banner(page, grid, warnings)

    side = _box_side(page)
    boxes = _read_boxes(page, side, grid) if side is not None else []
    if _unnamed(boxes, grid):
        # Every tick is geometry, not text: the labels print whether or not they
        # are ticked. A card whose boxes were not located would read a forfeit as
        # a played set with an odd score, so it is refused rather than scored.
        # Losing some of them is the same failure, only quieter: a box this
        # reader cannot name reads as a box nobody ticked, and a Match Forfeited
        # tick lost that way read a forfeit as a match played. Asked place by
        # place, because a box named somewhere else is not the one a row reads.
        raise _no_box_named(page, side, grid, boxes)
    # Every box the card needs is named, so any more are a layout this reader
    # was not written for: warned of, and refused where one nothing reads is
    # ticked.
    warnings.extend(_box_warnings(page, side, grid, boxes))

    sets = tuple(_read_row(page, grid, index, band, boxes, warnings)
                 for index, band in enumerate(grid.bands))
    if expect is not None:
        _check_expected_format(sets, expect)

    totals = _read_totals(page, grid, warnings)
    winner_tick = _sided_flag("Match Winner", boxes, grid, warnings)
    forfeited_by = _sided_flag("Match Forfeited", boxes, grid, warnings)
    dropped: list[str] = []
    home_roster, away_roster = _read_rosters(page, grid, warnings, sets, dropped)
    checks = (_totals_check(sets, totals, forfeited_by=forfeited_by),
              _verb_check(sets, banner.verb, forfeited_by=forfeited_by),
              _winner_tick_check(sets, winner_tick, boxes, grid,
                                 forfeited_by=forfeited_by, verb=banner.verb))
    if not any(check.available for check in checks):
        # A verb printed that confirms nothing is said as printed (4.133).
        nouns = {"totals": "no TOTAL row", "verb": "no result verb",
                 "winner-tick": "no Match Winner tick"}
        warnings.append(
            f"the card prints {thin_card_prints(checks, nouns)}, so nothing on it "
            "confirms the set rows were read correctly")

    return Card(
        source=page.source,
        page=page.index,
        match_id=header.match_id,
        date=header.date,
        weekday=header.weekday,
        division=header.division,
        round=header.round,
        court=header.court,
        venue=header.venue,
        season_hint=header.season_hint,
        club_hint=header.club_hint,
        home_name=banner.home,
        away_name=banner.away,
        verb=banner.verb,
        sets=sets,
        home_roster=home_roster,
        away_roster=away_roster,
        totals=totals,
        winner_tick=winner_tick,
        forfeited_by=forfeited_by,
        cancelled=_centred_flag("Match Cancelled", boxes),
        disputed=_centred_flag("Match Disputed", boxes),
        checks=checks,
        warnings=tuple(warnings),
        dropped=tuple(dropped),
    )


# What :func:`describe` says of a stage that refused the page. Its reason is in
# the reader's own words, and its detail is what the reader saw, which can quote
# the page, so each has a line of its own: the doctor's redacted report publishes
# the first as written and scrubs the second (4.185).
_NOT_READ = {"grid": "NOT RESOLVED", "header": "NOT READ", "banner": "NOT READ",
             "ticks": "NOT READ"}


class Reason(str):
    """A line of :func:`describe_lines` that is a stage's reason for refusing the
    page. Told apart by what it is, not by what it says: a page can print a line
    of its own inside a word, and a name that printed one saying this would be
    published with it."""


class CheckLine(str):
    """A line of :func:`describe_lines` that is a cross-check's, with the check
    it says. Told apart by what it is, as a :class:`Reason` is, so that the
    doctor can say a hand-off as the club's rules have it (4.134)."""

    check: CrossCheck

    @classmethod
    def of(cls, check: CrossCheck) -> CheckLine:
        line = cls(f"cross-check {check.name}: {check.state()}"
                   + (f" - {check.detail}" if check.detail else ""))
        line.check = check
        return line


def _not_read(stage: str, exc: CardError) -> list[str]:
    return ([Reason(f"{stage}: {_NOT_READ[stage]} - {exc.reason}")]
            + ([f"  detail: {exc.detail}"] if exc.detail else []))


def describe(page: pdf.Page) -> str:
    """A structural summary of one page, whether or not it parses.

    This is what a club stares at when a card will not read, so it reports what
    was *found* rather than what was expected, stage by stage, and keeps going
    after a stage fails.
    """
    return "\n".join(describe_lines(page))


def describe_lines(page: pdf.Page) -> list[str]:
    """:func:`describe`, a line at a time, each stage's reason a :class:`Reason`
    and each cross-check's line a :class:`CheckLine`."""
    lines: list[str] = [
        f"source: {page.source or '<unnamed>'} "
        f"page {page.index + 1} of {page.page_count}",
        f"page box: {page.width:.1f} x {page.height:.1f} pt, "
        f"{len(page.words)} words, {len(page.rects)} rects",
    ]
    side = _box_side(page)
    cluster = _rule_cluster(page)
    lines.append("rect census (width x height: count):")
    for (width, height), count in _census(page):
        notes = []
        if side is not None:
            wide = abs(width - side) <= _BOX_TOLERANCE
            tall = abs(height - side) <= _BOX_TOLERANCE
            if wide and tall:
                notes.append("filled checkbox, i.e. a tick")
            elif wide and height < _HAIRLINE_MAX_HEIGHT:
                notes.append("checkbox top or bottom edge")
            elif tall and width < _HAIRLINE_MAX_HEIGHT:
                notes.append("checkbox left or right edge")
        if any(abs(r.w - width) < 0.01 and abs(r.h - height) < 0.01
               for r in cluster):
            notes.append("table rule")
        suffix = f"   <- {', '.join(notes)}" if notes else ""
        lines.append(f"  {width:8.2f} x {height:8.2f}: {count:4d}{suffix}")

    warnings: list[str] = []
    grid: _Grid | None = None
    try:
        grid = _grid(page, warnings)
    except CardError as exc:
        lines.extend(_not_read("grid", exc))
        bands = _bands(cluster)
        lines.append("  column rules found: "
                     + (" ".join(f"{x:.1f}" for x in _rule_positions(cluster))
                        or "none"))
        lines.append("  row bands found: "
                     + (" ".join(f"{t:.1f}-{b:.1f}" for t, b in bands) or "none"))
    if grid is not None:
        lines.append(f"column rules ({len(grid.rules)}): "
                     + " ".join(f"{x:.1f}" for x in grid.rules))
        lines.append("cells: " + " | ".join(
            f"{_role(grid, cell)} {cell.width:.1f}pt" for cell in grid.cells))
        lines.append(f"row bands ({len(grid.bands)}): "
                     + " ".join(f"{t:.1f}-{b:.1f}" for t, b in grid.bands))

    lines.append(f"checkbox side: "
                 + (f"{side:.2f}pt" if side is not None else "not found"))
    boxes = _read_boxes(page, side, grid) if side is not None else []
    # Both counts, because the gap between them is a finding of its own: a box
    # that is drawn and not named is where a tick goes unseen. "Found", because
    # it is this reader's count, and the doctor's census above it counts by a
    # looser test of its own.
    outlines = _box_geometry(page, side)[0] if side is not None else []
    squares = len(outlines)
    counted = (f"checkboxes: {squares} square{'' if squares == 1 else 's'} "
               f"found, {len(boxes)} named by a label")
    if grid is not None:
        # And how many of those the card needs are not named, by the reader's own
        # test, since a card naming as many as it needs and not the ones it needs
        # is refused for it, and the two counts above cannot say so. Which they
        # are, in the refusal's words, because a card refused first for its
        # header or banner has no other line that says so; and why, where
        # the page prints the set's boxes right of their labels, since the
        # boxes listed below are then named by their neighbours' labels.
        unnamed = _unnamed(boxes, grid)
        lines.append(f"{counted}, {_expected_boxes(len(grid.bands))} expected "
                     f"for {len(grid.bands)} rows"
                     + (f" and {len(unnamed)} of those not named: "
                        f"{in_words(_unnamed_in_words(unnamed, len(grid.bands)))}"
                        + (f"; {_RIGHT_OF_LABELS}"
                           if _printed_right(page, outlines) else "")
                        if unnamed else ""))
    else:
        lines.append(counted)
    centred = {f"Match {label}" for label in ("Cancelled", "Disputed")}
    for box in boxes:
        row = ""
        if grid is not None:
            row = next((f" row {i + 1}" for i, band in enumerate(grid.bands)
                        if _in_band(box.rect, band)), "")
            # A side is only meaningful for the labels the card prints twice;
            # Match Cancelled and Match Disputed are one box for the whole match.
            if box.label not in centred:
                row += f" {grid.side_of(box.rect.cx)}"
        lines.append(f"  {box.label:<16}{row:<12} "
                     f"at {box.rect.x0:.1f},{box.rect.y0:.1f} "
                     f"{'TICKED' if box.ticked else '-'}")

    if grid is None:
        lines.append("header, banner and set rows: not read, the grid did not "
                     "resolve")
        return lines

    for stage, reader in (("header", lambda: _read_header(page, grid, warnings)),
                          ("banner", lambda: _read_banner(page, grid, warnings))):
        try:
            found = reader()
        except CardError as exc:
            lines.extend(_not_read(stage, exc))
            continue
        if isinstance(found, _Header):
            lines.append(
                f"header: division {found.division!r} round {found.round!r} "
                f"match id {found.match_id!r} date {found.date or '-'} "
                f"(printed {found.weekday or '-'}) court "
                f"{found.court if found.court is not None else '-'} "
                f"venue {found.venue!r}")
            lines.append(f"  club hint {found.club_hint!r}, "
                         f"season hint {found.season_hint!r}")
        else:
            lines.append(f"banner: home {found.home!r} away {found.away!r} "
                         f"verb {found.verb!r}")
    if not unnamed:
        # The reading's own warnings about the boxes, from the function that
        # gives them, and its refusal of a tick nothing reads said as a stage's.
        try:
            warnings.extend(_box_warnings(page, side, grid, boxes))
        except CardError as exc:
            lines.extend(_not_read("ticks", exc))

    sets = tuple(_read_row(page, grid, index, band, boxes, warnings)
                 for index, band in enumerate(grid.bands))
    lines.append(f"set rows ({len(sets)}):")
    for parsed in sets:
        lines.append(
            f"  row {parsed.index + 1} marker {parsed.marker!r} "
            f"{'doubles' if parsed.doubles else 'singles'} "
            f"{parsed.home_games}-{parsed.away_games} {parsed.status.value} "
            f"home {list(parsed.home_players)} away {list(parsed.away_players)}"
            + (f" ({parsed.unnamed} cell(s) print no player, only Match "
               f"Centre's placeholder)" if parsed.unnamed else ""))
    try:
        detected = from_rows(s.doubles for s in sets)
        lines.append(f"format described by the card: {detected.name} "
                     f"({detected.pattern})")
    except FormatError as exc:
        lines.append(f"format described by the card: undetectable ({exc})")

    totals = _read_totals(page, grid, warnings)
    lines.append(f"printed TOTAL row: sets {_shown(totals.home_sets)}-"
                 f"{_shown(totals.away_sets)} games {_shown(totals.home_games)}-"
                 f"{_shown(totals.away_games)}")
    verb = ""
    try:
        verb = _read_banner(page, grid, warnings).verb
    except CardError:
        verb = ""
    winner = _sided_flag("Match Winner", boxes, grid, warnings)
    # Read as the reader reads it, for the two checks that ask whether the card is
    # a whole-match forfeit, and for nothing else: what it notes about the box is
    # left out, so the warnings below are the ones this summary always listed.
    forfeited_by = _sided_flag("Match Forfeited", boxes, grid, [])
    checks = [_totals_check(sets, totals, forfeited_by=forfeited_by),
              _verb_check(sets, verb, forfeited_by=forfeited_by)]
    # The Match Winner tick is checked only where both its boxes are named. Where
    # one is not, the checkbox line above names it, and the card is refused for it
    # or for something read before its boxes, such as the header; a check that
    # could only say the box is not named is left out rather than printed as "not
    # printed".
    if not any(label == "Match Winner" for label, _, _ in unnamed):
        checks.append(_winner_tick_check(sets, winner, boxes, grid,
                                         forfeited_by=forfeited_by, verb=verb))
    lines.extend(CheckLine.of(check) for check in checks)
    home_roster, away_roster = _read_rosters(page, grid, warnings, sets, [])
    for label, roster in (("home", home_roster), ("away", away_roster)):
        lines.append(f"{label} roster: " + (", ".join(
            f"{e.name}{' (C)' if e.is_captain else ''} "
            f"[{_shown(e.singles)}/{_shown(e.doubles)}]" for e in roster)
            or "none read"))
    lines.append("warnings: " + (f"{len(warnings)}" if warnings else "none"))
    for warning in warnings:
        lines.append(f"  {warning}")
    return lines


# --- the grid ------------------------------------------------------------


def _rule_cluster(page: pdf.Page, *, filled: bool = True) -> list[pdf.Rect]:
    """The set table's own vertical rules, told apart from every other hairline.

    A card paints thin vertical rects for three different things: the table's
    cell borders, the sides of every checkbox, and the boxes of the signature
    block. Rather than a hardcoded height window, they are separated by clustering
    on height and keeping the cluster with the most rule *ink* -- the set table is
    always the largest run of vertical line on the page, and that holds for a
    one-row card and a scaled rendering alike.

    Only fills are rules. ``filled=False`` asks the same question of the page's
    outlines, which are never read as rules: :func:`_grid` asks it only to tell a
    table painted in outlines from a page with no table, and asks it by this same
    clustering so that the two cannot disagree about which run is the table.
    """
    thin = [r for r in page.rects if r.filled == filled
            and r.w < _RULE_MAX_WIDTH and r.h >= _RULE_MIN_HEIGHT]
    if not thin:
        return []
    groups: list[list[pdf.Rect]] = []
    for rect in sorted(thin, key=lambda r: r.h):
        tolerance = max(_RULE_HEIGHT_TOL, rect.h * _RULE_HEIGHT_SPREAD)
        if groups and rect.h - groups[-1][-1].h <= tolerance:
            groups[-1].append(rect)
        else:
            groups.append([rect])
    return max(groups, key=_ink)


def _ink(rules: Sequence[pdf.Rect]) -> float:
    """How much vertical line a run of rules paints: the set table has the most."""
    return sum(r.h for r in rules)


def _rule_positions(rules: Sequence[pdf.Rect]) -> tuple[float, ...]:
    return tuple(sorted({round(r.x0, 1) for r in rules}))


def _bands(rules: Sequence[pdf.Rect]) -> tuple[tuple[float, float], ...]:
    """Top and bottom of each row of the table, from its full-height rules.

    Each row is drawn twice, an outer cell border and an inner one a fraction
    shorter, so a span nested inside the previous one is the same row.
    """
    bands: list[tuple[float, float]] = []
    for top, bottom in sorted({(round(r.y0, 1), round(r.y1, 1)) for r in rules}):
        if (bands and top >= bands[-1][0] - _BAND_NEST_TOL
                and bottom <= bands[-1][1] + _BAND_NEST_TOL):
            continue
        bands.append((top, bottom))
    return tuple(bands)


def _grid(page: pdf.Page, warnings: list[str]) -> _Grid:
    """Resolve the set table, or refuse the page (rule F5).

    Each refusal below is a way for a scoreline to come out reversed, truncated
    or attributed to the wrong side. The first of them -- a rule count that does
    not resolve into columns -- is the one the predecessor survived by
    substituting eight constants, and is why this function exists.
    """
    rules = _rule_cluster(page)
    outlines = _rule_cluster(page, filled=False)
    if (len(_rule_positions(outlines)) >= _MIN_RULES
            and _ink(outlines) > _ink(rules)):
        # The largest run of rules on the page, which is how the set table is told
        # from every other hairline, is outlined. That is a table painted another
        # way, as on a card another program re-saved, and not a page without one,
        # so it is not called one. Held to the bar filled rules are held to below,
        # so one thin outline on a rules sheet is not taken for a table either.
        # Still refused, because an outline is not a fill: a tick outlined is the
        # box it sits in drawn again, so a reader that took outlines for rules
        # would have to guess, box by box, whether the page still says what it
        # said before it was repainted.
        raise CardError(
            "the page paints its table rules as outlines rather than fills, "
            "which is not how Match Centre paints a card, so its set table is "
            "not read",
            detail=f"{len(outlines)} outlined rect(s) make "
                   f"{len(_rule_positions(outlines))} column rule(s) over "
                   f"{len(_bands(outlines))} row band(s); "
                   f"{sum(1 for r in page.rects if r.filled)} of the page's "
                   f"{len(page.rects)} rects are filled; if this is a scorecard, "
                   "another program re-saved or re-printed it after it was "
                   "exported, so export it again from Match Centre")
    if not rules:
        # Said by what was not found, not by what the page is. A draw or a rules
        # sheet paints no rules, and nor, to this reader, does a card whose rules
        # were drawn as paths or as stroked lines: pdf.py records rectangles only,
        # and such a card renders the same as the card it was redrawn from.
        raise CardError(
            "no table rules could be found on the page, so no set table was "
            "read",
            detail=f"looked for filled rects under {_RULE_MAX_WIDTH}pt wide and "
                   f"at least {_RULE_MIN_HEIGHT}pt tall among "
                   f"{len(page.rects)} rects; either this page is not a "
                   "scorecard, or another program re-saved it and drew its "
                   "rules some other way, and if it is one, export it again "
                   "from Match Centre")
    positions = _rule_positions(rules)
    bands = _bands(rules)
    if len(positions) < _MIN_RULES or not bands:
        raise CardError(
            f"the set table has {len(positions)} column rule(s) over "
            f"{len(bands)} row band(s), too few to be a scorecard table",
            detail="found at x " + " ".join(f"{x:.1f}" for x in positions))

    # A gap between two row bands means a row's rules were not found. That row
    # still happened, and a card read one row short is indistinguishable from a
    # club that plays one set fewer, so it cannot be allowed through.
    for (_, bottom), (top, _) in zip(bands, bands[1:]):
        if abs(top - bottom) > _BAND_JOIN_TOL:
            raise CardError(
                "the set table's row bands are not contiguous, so at least one "
                "row's rules were not found",
                detail=f"a {top - bottom:.1f}pt gap between a band ending at "
                       f"{bottom:.1f} and the next starting at {top:.1f}")
    if positions[0] < -0.5 or positions[-1] > page.width + 0.5:
        raise CardError(
            "the set table does not fit inside the page, so a column may have "
            "been clipped off the card",
            detail=f"rules run x {positions[0]:.1f} to {positions[-1]:.1f} on a "
                   f"{page.width:.1f}pt page")
    if bands[0][0] < -0.5 or bands[-1][1] > page.height + 0.5:
        raise CardError(
            "the set table does not fit inside the page, so a row may have been "
            "clipped off the card",
            detail=f"bands run y {bands[0][0]:.1f} to {bands[-1][1]:.1f} on a "
                   f"{page.height:.1f}pt page")

    cells = tuple(_Cell(x0, x1) for x0, x1 in zip(positions, positions[1:]))
    _warn_partial_rules(rules, positions, bands, warnings)

    headings = [w for w in page.words
                if _heading_band_top(bands[0][0]) <= w.cy < bands[0][0]]
    games = _heading_cells(headings, cells, "games")
    if len(games) < 2:
        raise CardError(
            f"found {len(games)} 'Games' column heading(s) above the set table, "
            "so the games columns cannot be identified",
            detail="printed above the table: "
                   + (", ".join(sorted({w.text for w in headings})[:12])
                      or "nothing"))
    if len(games) > 2:
        warnings.append(
            f"{len(games)} 'Games' headings sit above the set table; used the "
            "leftmost as home and the rightmost as away")
    home_games, away_games = min(games), max(games)

    labels = _heading_cells(headings, cells, "set")
    between = [i for i in labels if home_games < i < away_games]
    if between:
        label = between[0]
    elif away_games - home_games == 2:
        label = home_games + 1
        warnings.append(
            "no 'SET' heading above the set table; used the single column "
            "between the two games columns as the label column")
    else:
        raise CardError(
            "the column between the two games columns cannot be identified",
            detail=f"games headings in columns {home_games + 1} and "
                   f"{away_games + 1} of {len(cells)}, with "
                   f"{away_games - home_games - 1} column(s) between them and "
                   f"{'a' if labels else 'no'} 'SET' heading")

    numeric = [cells[i].width for i in (home_games, label, away_games)]
    if max(numeric) > min(numeric) * _GAMES_CELL_RATIO:
        # The two games columns and the label between them are all narrow. One of
        # them coming out wide means a rule between it and its neighbour was not
        # painted or not found, and the wide column holds two cells' contents.
        raise CardError(
            "the games and label columns are not of comparable width, so a "
            "column rule is missing between them",
            detail="widths " + ", ".join(f"{w:.1f}pt" for w in numeric))

    if home_games == 0:
        raise CardError(
            "there is no column left of the home games column, so the home "
            "player names cannot be read",
            detail=f"{len(cells)} columns, games heading in the first")
    if away_games == len(cells) - 1:
        raise CardError(
            "there is no column right of the away games column, so the away "
            "player names cannot be read",
            detail=f"{len(cells)} columns, games heading in the last")
    home_names, away_names = home_games - 1, away_games + 1
    home_marker = home_names - 1 if home_names > 0 else None
    away_marker = away_names + 1 if away_names < len(cells) - 1 else None
    if home_marker is None and away_marker is None:
        raise CardError(
            "the set table has no marker column outside the player names, so "
            "which rows are doubles cannot be read from the card",
            detail=f"{len(cells)} columns: names in {home_names + 1} and "
                   f"{away_names + 1}")

    return _Grid(
        rules=positions,
        bands=bands,
        cells=cells,
        home_names=cells[home_names],
        home_games=cells[home_games],
        label=cells[label],
        away_games=cells[away_games],
        away_names=cells[away_names],
        home_marker=cells[home_marker] if home_marker is not None else None,
        away_marker=cells[away_marker] if away_marker is not None else None,
    )


def _heading_band_top(top: float) -> float:
    """The highest a column heading may sit and still belong to the table."""
    return top - _HEADING_REACH


def _heading_cells(headings: Iterable[pdf.Word], cells: Sequence[_Cell],
                   want: str) -> list[int]:
    """Indices of the columns whose heading word is ``want``, left to right."""
    found: list[int] = []
    for word in headings:
        if word.text.strip().rstrip(":").casefold() != want:
            continue
        for index, cell in enumerate(cells):
            if cell.holds(word.cx):
                found.append(index)
                break
    return sorted(set(found))


def _warn_partial_rules(rules: Sequence[pdf.Rect], positions: tuple[float, ...],
                        bands: tuple[tuple[float, float], ...],
                        warnings: list[str]) -> None:
    """Note a column rule that is missing from some rows but not others.

    The columns are the union over every row, so one row missing a rule still
    parses -- against a grid that row does not actually draw. It is a warning
    rather than a refusal because the row's numbers are usually still in the
    right cells, but it is the shape of a template change and worth saying.
    """
    seen: dict[float, set[int]] = {x: set() for x in positions}
    for rect in rules:
        x = round(rect.x0, 1)
        for index, (top, bottom) in enumerate(bands):
            if top - _BAND_NEST_TOL <= rect.cy <= bottom + _BAND_NEST_TOL:
                seen[x].add(index)
                break
    ragged = sorted(x for x, rows in seen.items() if len(rows) < len(bands))
    if ragged:
        warnings.append(
            "column rule(s) at x " + " ".join(f"{x:.1f}" for x in ragged)
            + f" are drawn on fewer than the {len(bands)} row bands, so the "
            "table is not the same shape on every row")


# --- header --------------------------------------------------------------


def division_in(text: str, *, short: bool = True) -> str:
    """The division ``text`` prints, or ``""``.

    One function for every place that asks, so the line the identity is bound from,
    the line a split identity is paired from, the line a refusal quotes and the
    field the doctor reports are all chosen by the same rule. ``short=False`` leaves
    out ``C Grade``, which is trusted less: see the patterns.
    """
    patterns = (_DIVISION_RE, _GRADE_AFTER_RE) if short else (_DIVISION_RE,)
    found = min(filter(None, (pattern.search(text) for pattern in patterns)),
                key=lambda match: match.start(), default=None)
    if not found:
        return ""
    return _DESIGNATION_WORD_RE.sub(
        r" \g<word> ", found.group("division")).strip(".,;:")


def round_in(text: str, *, short: bool = True) -> str:
    """The round ``text`` prints, or ``""``; ``short`` as for the division."""
    found = (_ROUND_RE.search(text)
             or (short and _ROUND_ABBREVIATED_RE.search(text)))
    return found.group("round").strip(".,;:") if found else ""


def _identity_block(joined: Sequence[tuple[str, list[pdf.Word]]]
                    ) -> list[tuple[str, list[pdf.Word]]]:
    """The lines above the set table that can be the identity line.

    Not the one printing the team names, which are the largest print above the
    table, as the banner is read, and not one printing only contact details. Both
    are below the identity line, where it is looked for first, and a team named
    after its street or an address with ``division`` in it prints its words.
    """
    biggest = max((word for _, line in joined for word in line),
                  key=lambda word: word.size, default=None)
    return [(text, line) for text, line in joined
            if biggest not in line
            and not all(_CONTACT_RE.search(word.text) for word in line)]


def _nearest(texts: Sequence[str], rule: Callable[[str], object]) -> str:
    """The line nearest the set table that ``rule`` finds anything on, or ``""``.

    The template prints the identity line below every other line that can print a
    grade or a round: the letterhead, the title and the season line.
    """
    return next((text for text in reversed(texts) if rule(text)), "")


# Built from the words the patterns are built from, so a refusal names every word
# the reader reads, and none it does not.
_IDENTITY_WORDING = (
    "this reader takes a division printed as "
    + in_words([f"{word} C" for word in _DIVISION_WORDS], "or")
    + f" and a round printed as {_ROUND_WORD} 1, with a colon after the word or "
    "without one, and "
    + in_words([f"C {_GRADE_AFTER_WORD}"]
               + [f"{word} 1" for word in _ROUND_ABBREVIATIONS], "and")
    + " as well on a line that prints both")


def _read_header(page: pdf.Page, grid: _Grid, warnings: list[str]) -> _Header:
    """Division, round, match id, date, court and the two config-stub hints.

    Read from inside the card and nowhere else. A filename and a directory name
    are labels a human typed; a scorecard is a document.
    """
    lines = [line for line in page.lines() if line[0].cy < grid.top]
    joined = [(" ".join(w.text for w in line), line) for line in lines]

    division = round_number = match_id = ""
    block = _identity_block(joined)
    texts = [text for text, _ in block]
    # The identity line is the nearest line printing either half, and one printing
    # a single half is paired below. A line above it that prints both is a title or
    # a season line, and is not read in its place.
    text = _nearest(texts, lambda text: division_in(text) or round_in(text))
    if text:
        division, round_number = division_in(text), round_in(text)
        found_id = _MATCH_ID_RE.search(text)
        match_id = found_id.group("id") if found_id else ""
    if not (division and round_number):
        # Some associations split the identity fields over two printed lines.
        # Both values still come off the card, so this is allowed, but it is
        # reported: the risk is pairing a division with the word "Round" from
        # somewhere else entirely.
        division_line = _nearest(texts, lambda text: division_in(text, short=False))
        round_line = _nearest(texts, lambda text: round_in(text, short=False))
        if division_line and round_line:
            division = division_in(division_line, short=False)
            round_number = round_in(round_line, short=False)
            found_id = (_MATCH_ID_RE.search(round_line)
                        or _MATCH_ID_RE.search(division_line))
            match_id = found_id.group("id") if found_id else ""
            warnings.append(
                f"the division and the round are printed on different lines "
                f"({echoed(division_line, quoted=True)} and "
                f"{echoed(round_line, quoted=True)})")
        else:
            raise CardError(
                "no line above the set table names both a division and a round, "
                "so this page cannot be placed in a competition; "
                + _IDENTITY_WORDING,
                detail=_header_hint(block))

    date, weekday = _read_date(joined, warnings)
    court, venue = _read_court(page, warnings)
    return _Header(
        division=division,
        round=round_number,
        match_id=match_id,
        date=date,
        weekday=weekday,
        court=court,
        venue=venue,
        club_hint=_club_hint(page, grid),
        season_hint=_season_hint(joined),
    )


def _header_hint(joined: Sequence[tuple[str, list[pdf.Word]]]) -> str:
    """The line that looks like it was meant to be the identity line.

    "Not a scorecard" is the common and correct verdict when a directory walk
    reaches a club's rules PDF. It is also what a real card gets when its header
    is worded differently from the ones this reader has seen, and that reading is
    a dead end -- the file is named like every other card and the message points
    at the wrong thing. Quoting the closest line makes it actionable: the one
    nearest the table that prints any of the words the refusal lists, as the
    identity line is read, since a street or a title above it prints them too.
    """
    for text, _ in joined:
        stripped = text.strip()
        if stripped.count("/") >= 2 and _MATCH_ID_RE.search(stripped):
            return f'closest line: "{stripped[:80]}"'
    closest = _nearest([text for text, _ in joined], _LABEL_WORD_RE.search)
    if closest:
        return f'closest line: "{closest.strip()[:80]}"'
    return "no line above the table uses any of those words"


# Month names, spelled out for the reason card.WEEKDAYS is: strftime's are the
# machine's language, and a card's are English.
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")

# The words of a header line, without the punctuation around them: `Tue:`,
# `(Wed)` and `Tuesday,` are each one word, and `-` on its own is none.
_WORD_RE = re.compile(r"[^\W_]+")
# The one abbreviation of a name in common use that is not the front of it.
_NOT_A_PREFIX = {"weds": "wednesday"}
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def _read_date(joined: Sequence[tuple[str, list[pdf.Word]]],
               warnings: list[str]) -> tuple[str, str]:
    """The card's own date, as ISO, and the weekday it prints beside it.

    Two readings, kept apart. The weekday used to be worked out from the date,
    which made it impossible for the two to disagree and left a card whose date
    did not read with no weekday at all -- although the filing is written to fall
    back on the printed weekday, and to say when it and the date differ. Neither
    comes from anywhere but the card: a card in ``data/MondayResults/`` is filed
    under a label a human typed, and a competition keyed on that label puts a
    Tuesday fixture in a Monday ladder.

    When no date reads, the weekday is the one in front of the start time
    instead, over whatever is left of the date (``Tue 7:00 PM``, ``Tue August 18,
    2026 7:00 PM``), which on the measured template is on the same line. With no
    start time either there is no line to take one from: a weekday word anywhere
    else in the header is a team's or a competition's name rather than the night's.
    """
    for text, _ in joined:
        found = _find_date(text)
        if found is None:
            continue
        start, iso, printed, month_first = found
        weekday = _printed_weekday(text, start, start + len(printed))
        if month_first:
            warnings.append(
                f"date {printed!r} is not a valid day/month/year, so it was read "
                "as month/day/year")
        if not iso:
            # Day-first figures have two ways round to fail; ISO and a month in
            # words have one.
            either = (" either way round" if _DAY_FIRST_DATE_RE.fullmatch(printed)
                      else "")
            warnings.append(
                f"the date printed on this card, {printed!r}, is not a real "
                f"date{either}, {_undated(weekday)}")
        return iso, weekday
    weekday = next((_printed_weekday(text, time.start(), time.end(), unread=True)
                    for text, _ in joined if (time := _TIME_RE.search(text))), "")
    warnings.append(
        f"no date above the set table is printed in a form this reader accepts "
        f"({date_forms()}), {_undated(weekday)}")
    return "", weekday


def _undated(weekday: str) -> str:
    """The end of a warning about a date that did not read: what files the card."""
    if weekday:
        return f"so the match is undated and is filed by the {weekday} it prints"
    return ("and no weekday is printed beside a date or a start time, so the "
            "night it was played is unknown")


def _find_date(text: str) -> tuple[int, str, str, bool] | None:
    """The first date on one line, in a form :data:`~matchcentre.card.DATE_FORMS` names.

    ``(start, iso, printed, month_first)``: where it starts, the date as ISO --
    ``""`` when it is not a real date -- the text it was read from, and whether it
    had to be read month first. ``None`` when the line prints no date. One
    function because three things need the one answer: the header's date, the
    weekday printed beside it, and the court reader, which has to pass over the
    date's line rather than take its year for a court number.

    Deliberately short of every form a date can take. Figures are read day first,
    which is the convention these cards follow: ``05/08/2026`` is the fifth of
    August, and only a date that cannot be read that way is read month first. A
    two-digit year is 20yy -- no Match Centre export is from the last century, and
    refusing the card would leave a club whose template prints two digits with
    nothing it can change. A month in words is the day, then the month (``Aug.``
    and ``August,`` as well), then a four-digit year. Month-first words (``August
    18, 2026``), ordinals (``18th``), year-first figures other than ISO and mixed
    separators are not read at all, because each is either measured on no card or
    one more guess about an order, and the warning says which forms would have
    been.
    """
    iso = _ISO_DATE_RE.search(text)
    if iso:
        found_date = _date_or_none(int(iso.group("y")), int(iso.group("m")),
                                   int(iso.group("d")))
        return (iso.start(), found_date.isoformat() if found_date else "",
                iso.group(0), False)
    numeric = _DAY_FIRST_DATE_RE.search(text)
    if numeric:
        a, b = int(numeric.group("a")), int(numeric.group("b"))
        year = int(numeric.group("y"))
        if len(numeric.group("y")) == 2:
            year += 2000
        # Day-first is the convention on these cards, so it is tried first; a
        # date impossible read that way was written month-first, and saying so
        # beats discarding the date.
        found_date = _date_or_none(year, b, a)
        month_first = found_date is None
        if month_first:
            found_date = _date_or_none(year, a, b)
        return (numeric.start(), found_date.isoformat() if found_date else "",
                numeric.group(0), month_first and found_date is not None)
    for worded in _WORDED_DATE_RE.finditer(text):
        month = _spelled(worded.group("month"), _MONTHS)
        if month:
            found_date = _date_or_none(int(worded.group("y")),
                                       _MONTHS.index(month) + 1,
                                       int(worded.group("d")))
            return (worded.start(), found_date.isoformat() if found_date else "",
                    worded.group(0), False)
    return None


def _printed_weekday(text: str, start: int, end: int, *, unread: bool = False) -> str:
    """The weekday printed beside ``text[start:end]``, as its name, or ``""``.

    Beside is the word nearest in front of it -- ``Tue 18/08/2026``, ``Tuesday, 18
    August 2026`` -- or, with none there, the word straight after it --
    ``18/08/2026 Wed 7:00 PM``, ``18/08/2026 (Wed)``. Nearest, and not merely
    somewhere in front: the letterhead is printed on the date's own line, and a
    club can be called ``SAT 1 ...``. With ``unread`` the stamp is the start time
    of a card whose date did not read, and what is left of that date between the
    two is passed over to reach the word in front of it: a day, a month and a
    year at most, each a figure or a month's name (``Tue August 18, 2026 7:00
    PM``). At most three, because a letterhead can end ``... SAT 1`` in front of
    them, and that ``1`` is no part of a date.
    """
    before = _WORD_RE.findall(text, 0, start)
    if unread:
        for _ in range(3):
            if before and (before[-1][0].isdigit() or _spelled(before[-1], _MONTHS)):
                before.pop()
    after = _WORD_RE.findall(text, end)
    return (_spelled(before[-1], WEEKDAYS) if before else "") or (
        _spelled(after[0], WEEKDAYS) if after else "")


def _spelled(word: str, names: Sequence[str]) -> str:
    """The one of ``names`` that ``word`` spells or cuts short, or ``""``.

    Cut short means the front three letters or more, in any case: ``Tue``,
    ``Tues``, ``TUESDAY`` and ``Sept`` all name something. Three, because two do
    not (``Ju``, ``Ma``, ``Tu`` and ``Th`` each begin two names); from the front,
    because no two lists of abbreviations agree and nearly every one of them is a
    prefix. ``Weds`` is the one in common use that is not, and is read as well.
    """
    key = word.casefold()
    key = _NOT_A_PREFIX.get(key, key)
    if len(key) < 3:
        return ""
    return next((name for name in names if name.casefold().startswith(key)), "")


def _date_or_none(year: int, month: int, day: int) -> datetime.date | None:
    """``31/02`` is a typo, not a date. Never raises, so a mistyped card is a
    card with a warning rather than an exception out of the middle of a read."""
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _read_court(page: pdf.Page, warnings: list[str]) -> tuple[int | None, str]:
    """The court, from the ``Court:`` label's own cell rather than a club's venue.

    The predecessor found the court by looking for the literal word
    ``University`` and taking the token after it, which is one club's venue name
    and nothing else. Here the label is the anchor: the value is whatever is
    printed with it, on its line, just above it (which is where the measured
    template puts the venue and court together) or just below it on the ruled
    line a card can be filled in on.

    The court is the line's last word when
    :func:`~matchcentre.competition.decimal_figure` reads it. A ``³`` there gives
    no court, as a venue printed with no number does, and is not quoted: the line
    is the venue's too, and its last word is not always meant as a figure (4.216).
    """
    label = next((w for w in page.words
                  if w.text.strip().rstrip(":").casefold() == "court"), None)
    if label is None:
        warnings.append("no 'Court' label on the page, so the court is unknown")
        return None, ""

    column = label.x0 - _CELL_INSET
    candidates: list[tuple[list[pdf.Word], list[pdf.Word]]] = []
    level = [w for w in page.words
             if abs(w.baseline - label.baseline) <= _BASELINE_TOL]
    same = [w for w in level if w.x0 >= label.x1]
    if same:
        candidates.append((sorted(same, key=lambda w: w.x0), level))
    for line in page.lines():
        words = sorted((w for w in line if w.x0 >= column), key=lambda w: w.x0)
        if not words:
            continue
        above = label.baseline - words[0].baseline
        if 0 < above <= _COURT_REACH_ABOVE or -_COURT_REACH_BELOW <= above < 0:
            candidates.append((words, list(line)))
    for words, line in candidates:
        texts = [w.text for w in words]
        if _runs_into_a_date(words, line) or any(":" in t for t in texts):
            continue        # this is the date and time line, not the court
        court = decimal_figure(texts[-1].strip(".,;:"))
        if court is None:
            continue
        return court, " ".join(texts[:-1]).strip()
    warnings.append(
        f"the 'Court' label at x {label.x0:.1f} has no number printed with it, "
        "so the court is unknown")
    return None, ""


def _runs_into_a_date(words: Sequence[pdf.Word], line: Sequence[pdf.Word]) -> bool:
    """Whether a date printed on ``line`` takes in any of ``words``.

    Asked of the whole line, because the court's column can begin part-way into a
    date: ``18 Aug 2026`` printed where the date goes leaves ``Aug 2026`` inside
    the column, which is no date on its own and does end in a number.
    """
    text = ""
    spans: list[tuple[int, pdf.Word]] = []
    for word in sorted(line, key=lambda w: w.x0):
        text += " " if text else ""
        spans.append((len(text), word))
        text += word.text
    found = _find_date(text)
    if found is None:
        return False
    start, _, printed, _ = found
    end = start + len(printed)
    return any(at < end and start < at + len(word.text)
               for at, word in spans if word in words)


def _club_hint(page: pdf.Page, grid: _Grid) -> str:
    """The club letterhead, verbatim, to seed the config stub a first run writes.

    The letterhead is a *block*: several lines above the team-name banner, left of
    the middle of the card, all starting at the same x. Being a block is what
    tells it apart from the centred competition lines beside it and from the
    single-line banner below it, and taking the widest block rather than the
    leftmost matters -- the home team's name in the banner starts further left
    than the letterhead does. Contact details are dropped, because a phone number
    is not a club name. Nothing here is authoritative, which is what "hint" means:
    the club's real name is config, and this only saves the user typing it.
    """
    above = [w for w in page.words if w.cy < grid.top]
    if not above:
        return ""
    banner_size = max(w.size for w in above)
    banner_top = min(w.cy for w in above if abs(w.size - banner_size) < 0.2)
    upper = [w for w in above
             if w.cy < banner_top and w.size < banner_size - 0.2
             and w.x1 < grid.divider]
    if not upper:
        return ""

    blocks: list[list[list[pdf.Word]]] = []
    for line in sorted((sorted(line, key=lambda w: w.x0)
                        for line in pdf.group_lines(upper)),
                       key=lambda line: (line[0].x0, line[0].cy)):
        if blocks and abs(line[0].x0 - blocks[-1][0][0].x0) <= _CELL_INSET * 2.5:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    # Most lines wins; a tie goes to the block nearest the top of the page, which
    # is where a letterhead is, and then to the leftmost, so the answer never
    # depends on the order the content stream happened to be written in.
    chosen = max(blocks, key=lambda block: (len(block),
                                            -min(line[0].cy for line in block),
                                            -block[0][0].x0))
    out: list[str] = []
    for line in sorted(chosen, key=lambda line: line[0].cy):
        kept = [w.text for w in line if not _CONTACT_RE.search(w.text)]
        if kept:
            out.append(" ".join(kept))
    return " ".join(out).strip()


def _season_hint(joined: Sequence[tuple[str, list[pdf.Word]]]) -> str:
    """A season string if the header happens to print one, for the config stub.

    Whether a card belongs to a season is settled by the directory it arrives in
    or by its own date; this is only a label to offer the user, so it is taken
    verbatim and never parsed.
    """
    for text, _ in joined:
        found = _SEASON_RE.search(text)
        if found:
            return found.group(0).strip()
    return ""


# --- banner --------------------------------------------------------------


def _read_banner(page: pdf.Page, grid: _Grid, warnings: list[str]) -> _Banner:
    """The two team names and the verb printed between them.

    The names are the largest text above the table and share a baseline; the verb
    is smaller and sits in the gap. Splitting on the gap is what keeps a two-word
    team name from being read as part of its opponent's.
    """
    above = [w for w in page.words if w.cy < grid.top]
    if not above:
        raise CardError("nothing is printed above the set table, so the two "
                        "team names cannot be read")
    biggest = max(above, key=lambda w: w.size)
    names = sorted((w for w in above
                    if abs(w.size - biggest.size) < 0.2
                    and abs(w.baseline - biggest.baseline) <= _BASELINE_TOL * 3),
                   key=lambda w: w.x0)
    left = [w.x1 for w in names if w.cx < grid.divider]
    right = [w.x0 for w in names if w.cx > grid.divider]
    if not left or not right:
        raise CardError(
            "the team-name banner does not straddle the middle of the table, so "
            "home and away cannot be told apart",
            detail="largest text above the table: "
                   + " ".join(w.text for w in names))
    gap_start, gap_end = max(left), min(right)
    verb_words = sorted((w for w in above
                         if gap_start < w.cx < gap_end
                         and abs(w.cy - biggest.cy) < biggest.size),
                        key=lambda w: w.x0)
    home = " ".join(w.text for w in names if w.x1 <= gap_start)
    away = " ".join(w.text for w in names if w.x0 >= gap_end)
    if not home or not away:
        raise CardError(
            "the team-name banner could not be split into two sides",
            detail=f"home {home!r}, away {away!r}")
    verb = " ".join(w.text for w in verb_words)
    if not verb:
        warnings.append(
            "no result verb is printed between the team names, so one of the "
            "three cross-checks on this card is unavailable")
    return _Banner(home=home, away=away, verb=verb)


# --- checkboxes ----------------------------------------------------------


def _box_side(page: pdf.Page) -> float | None:
    """The side of this card's checkbox, measured off the card.

    An unticked box is four hairlines, and the horizontal pair are the only short
    wide rects a card paints in any number, so the most common such width is the
    box side. Measuring rather than assuming means a card rendered at another
    scale still reads.
    """
    widths = Counter(round(r.w, 2) for r in page.rects
                     if r.filled and _edge_shaped(r))
    if not widths:
        return None
    # Ties break towards the narrower width so the answer does not depend on
    # dict order, and therefore on the order the content stream was written.
    return max(widths.items(), key=lambda item: (item[1], -item[0]))[0]


def _edge_shaped(rect: pdf.Rect) -> bool:
    """The shape of a box's top or bottom edge, however it was painted.

    The shape alone, so :func:`_box_side` measuring the filled ones and the
    refusal counting the outlined ones are asking the same question.
    """
    return (rect.h < _HAIRLINE_MAX_HEIGHT
            and _BOX_MIN_SIDE <= rect.w <= _BOX_MAX_SIDE)


def _box_geometry(page: pdf.Page,
                  side: float) -> tuple[list[pdf.Rect], list[pdf.Rect]]:
    """Whole checkbox squares reassembled from their edges, plus the solid ticks.

    An empty box is four hairlines rather than one stroked rectangle, so a box has
    to be rebuilt from its top and bottom edge: the edge rects are a box wide but
    under a point tall, and using one as the box would put its centre half a box
    above the label it belongs to. A ticked box adds a solid fill over the same
    outline, which is all :func:`_is_ticked` looks for.
    """
    edges = [r for r in page.rects
             if r.filled and abs(r.w - side) <= _BOX_TOLERANCE
             and r.h < _HAIRLINE_MAX_HEIGHT]
    fills = [r for r in page.rects
             if r.filled and abs(r.w - side) <= _BOX_TOLERANCE
             and abs(r.h - side) <= _BOX_TOLERANCE]
    columns: dict[float, list[pdf.Rect]] = {}
    for edge in edges:
        columns.setdefault(round(edge.x0, 1), []).append(edge)
    boxes: list[pdf.Rect] = []
    for column in columns.values():
        column.sort(key=lambda r: r.y0)
        for index, top in enumerate(column):
            # The bottom edge is painted inside the box, so its origin sits one
            # hairline short of a full side below the top edge's.
            wanted = side - top.h
            for bottom in column[index + 1:]:
                if abs((bottom.y0 - top.y0) - wanted) <= _BOX_PAIR_TOLERANCE:
                    boxes.append(pdf.Rect(x0=top.x0, y0=top.y0,
                                          x1=max(top.x1, bottom.x1),
                                          y1=bottom.y1, filled=False))
                    break
    return boxes, fills


def _is_ticked(box: pdf.Rect, side: float, fills: Iterable[pdf.Rect]) -> bool:
    """Is this box filled? By area overlap, because the fill and the outline are
    separate path objects and agree only to within rounding."""
    return any(box.overlaps(fill) > side * side * 0.5 for fill in fills)


def _looks_left(label: str) -> bool:
    """Does ``label`` name only a square on its left? A set's label does, and a
    whole-match flag looks either side. The one test of it, for the reading and
    for the refusal that says it, so the two cannot give a label different sides.
    """
    return label in _SET_STATUS_LABELS


def _read_boxes(page: pdf.Page, side: float, grid: _Grid | None) -> list[_Box]:
    """Every checkbox on the card, each named by the label that owns it.

    Labels drive the search rather than positions, because which side of its
    label a box sits on varies -- the away column's ``Match Winner`` and ``Match
    Forfeited`` boxes are to the right of their text and every other box is to the
    left. So a whole-match flag names the nearest square on either side, as it
    has only one within reach on its line, and a set's label names only a square
    on its left. It has two within reach, measured on the template: its own
    2.67pt to 3.08pt to its left, and the next label's 7.26pt to 8.37pt to its
    right. A label whose own square was gone took the next one's, and the card
    that had lost that square and the next label was refused naming only the box
    whose label was lost, as a page on which every square found is named. The gap
    is measured from the label's left edge to the square's right edge, and each of
    the three words is wider than the reach, so a square to its right is out of it.

    The nearest label and square are paired first, and not each label in turn in
    the order the page prints them. Taken in turn, when a label could look either
    side, a label whose own square was gone took the next one's, and that label
    the one after, a Retired square named Forfeit and a retirement ticked in it
    read as a forfeit. Looking one side, each label on the template has one
    square within reach, so neither the order nor naming a square once decides
    anything on a card as printed. They decide where a word read as a label is
    printed within reach of another label's square, as a second ``Match
    Disputed`` 10pt left of the Match Cancelled square is: taken in turn, the
    word read first took the square, and the card was refused for a Match
    Cancelled box whose square it could not find; named twice, the one square
    was both boxes, and a cancellation ticked in it read as a dispute as well.

    A set's label in no set row names a square only after every other label has
    named its own, nearest first among them too, since the box it names is one
    no row reads. Nearest first alone, a stray ``Retired`` printed 3.3pt right of
    the away Match Winner square took it from the flag's label, 14.17pt off, and
    the card was refused for a Match Winner box whose square it "cannot find". It
    still names a square nothing else reaches, as a set's box drawn again off the
    table is, so a tick there is still refused as one no row reads. Whether the
    box is in a set row is :func:`_place`'s test of its square, the one the rows
    are read by; with no ``grid``, as the reader's summary has for a page whose
    table it cannot find, there is no row to ask about.
    """
    outlines, fills = _box_geometry(page, side)
    labels = _box_labels(page)
    reach = side * _BOX_LABEL_GAP
    pairs = sorted(
        (grid is not None
         and _place(_Box(label=label, rect=rect, ticked=False), grid) is None,
         gaps[0] if _looks_left(label) else min(gaps), index, number)
        for index, (label, anchor) in enumerate(labels)
        for number, rect in enumerate(outlines)
        if (gaps := _gaps(anchor, rect)) is not None)
    named: dict[int, pdf.Rect] = {}
    used: set[tuple[float, float]] = set()
    for _, gap, index, number in pairs:
        key = _square_key(outlines[number])
        if gap < reach and index not in named and key not in used:
            named[index] = outlines[number]
            used.add(key)
    # In the order the labels are printed, as it always was, so that everything
    # which lists the boxes lists them in the same order it did.
    return [_Box(label=labels[index][0], rect=rect,
                 ticked=_is_ticked(rect, side, fills))
            for index, rect in sorted(named.items())]


def _gaps(anchor: pdf.Word, rect: pdf.Rect) -> tuple[float, float] | None:
    """How far the square ``rect`` is from the label ``anchor``, on the label's
    left and on its right, or None where its centre is not within
    :data:`_BOX_ROW_TOL` of the label's, up or down.

    The one measure of where a square sits beside its label, for the reading
    and for the refusal that says a page prints the set's squares on the other
    side of their labels.
    """
    if abs(rect.cy - anchor.cy) > _BOX_ROW_TOL:
        return None
    return abs(anchor.x0 - rect.x1), abs(rect.x0 - anchor.x1)


# What the refusal and the reader's summary say of a page on which
# :func:`_printed_right` finds the set's squares right of their labels.
_RIGHT_OF_LABELS = "this page prints the set's boxes right of their labels"


def _printed_right(page: pdf.Page, outlines: Sequence[pdf.Rect]) -> list[str]:
    """The set's labels this page prints with a square on their line, where the
    square nearest every one of them on its line is on the label's right;
    otherwise none.

    A set's label names only a square on its left, so on a page that prints each
    set's square right of its label the Retired label names the Forfeit square,
    the Incomplete label the Retired one, and only the Forfeit boxes are short.
    Every label, and not only the one left short, because a Forfeit label whose
    own square is 2pt off its line has none on its left and the Retired square
    7.26pt to its right, as it does on such a page; the Retired and Incomplete
    labels beside it still have their own squares nearest, on their left. A
    label with no square on its line says nothing of the side a square is on,
    as a Forfeit in the card's comments does not, and is left out, so a page
    whose set's labels have none has no side to say.

    Not asked to be within the label's reach as well, which the refusal does not
    say: the rightmost label of a row has a square on its right only on a page
    that prints the boxes there, however far off it prints them.
    """
    places = [(label, anchor) for label, anchor in _box_labels(page)
              if _looks_left(label)]
    printed = set()
    for label, anchor in places:
        on = [gaps for rect in outlines
              if (gaps := _gaps(anchor, rect)) is not None]
        if not on:
            continue
        left, right = min(on, key=min)
        if not right < left:
            return []
        printed.add(label)
    return [label for label in _SET_STATUS_LABELS if label in printed]


def _square_key(rect: pdf.Rect) -> tuple[float, float]:
    """Where a square is, to a tenth of a point: a square rebuilt twice is one."""
    return (round(rect.x0, 1), round(rect.y0, 1))


def _box_labels(page: pdf.Page) -> list[tuple[str, pdf.Word]]:
    """Each checkbox label and the word to anchor its box search on.

    Two-word labels anchor on a box covering both words, so the gap to the
    checkbox is measured from the label's real edge.
    """
    labels: list[tuple[str, pdf.Word]] = []
    statuses = {label.casefold(): label for label in _SET_STATUS_LABELS}
    flags = {label.casefold(): label for label in _MATCH_FLAG_LABELS}
    for line in page.lines():
        words = sorted(line, key=lambda w: w.x0)
        index = 0
        while index < len(words):
            word = words[index]
            key = word.text.strip().casefold()
            if key in statuses:
                labels.append((statuses[key], word))
                index += 1
                continue
            if key == "match" and index + 1 < len(words):
                following = words[index + 1]
                flag = flags.get(following.text.strip().casefold())
                if flag is not None:
                    labels.append((f"Match {flag}", _span(word, following)))
                    index += 2
                    continue
            index += 1
    return labels


def _span(first: pdf.Word, last: pdf.Word) -> pdf.Word:
    """One word-shaped box covering ``first`` through ``last``."""
    return pdf.Word(text=f"{first.text} {last.text}",
                    x0=min(first.x0, last.x0), y0=min(first.y0, last.y0),
                    x1=max(first.x1, last.x1), y1=max(first.y1, last.y1),
                    size=first.size, bold=first.bold, baseline=first.baseline)


class UnlocatedCheckboxes(CardError):
    """No checkbox square could be rebuilt from what the page paints.

    One of the two kinds of refusal a card on which no box was named is given;
    :class:`UnlabelledCheckboxes` is the other. The squares are the half missing
    here, and a club can see that by looking, unlike the labels' half. A card on
    which some boxes are named had squares rebuilt, so it is never given this.
    """


class UnlabelledCheckboxes(CardError):
    """A square this reader rebuilt is not beside a label it knows.

    On a card naming none of its boxes, or only some: a card naming 24 of its 30
    is refused as one naming none is, because a box this reader cannot name reads
    as a box nobody ticked. It says a square is drawn and its label is not where
    this reader looks, or not in the words it looks for, and not that every square
    is drawn. A refusal of its own kind because it is the checkbox refusal a club
    cannot see the cause of by looking. The doctor explains a refusal by asking
    which kind it was given, so there is one decision about it, and this module
    makes it.
    """


class MissingCheckboxes(CardError):
    """Some boxes are named, and the page lacks what the rest need.

    Either every square this reader rebuilt is named, so a box it could not name
    has no square, or a label it needs is printed fewer times than the card has
    boxes for it, which is a word lost and not a word changed: a template that
    words a label differently words it so everywhere. Measured, it is what a
    download that stopped early leaves, at every cut of a genuine card from 30% to
    79%, and what one byte damaged in the file left. Its own kind because the
    doctor's cause for the labels' kind, another template, is the wrong one here,
    and a copy downloaded again is the right one.

    So too where every label it could not name is printed fewer times than the
    card has boxes for it or is ``Match Cancelled`` or ``Match Disputed``, each
    printed once for its one box, printed nowhere, and not printed beside a
    square in another spelling, as ``Disputed:``. A copy that lost a word of that
    label and a template that words it differently look the same, and a copy
    downloaded again tells them apart.
    """


class UnreadTick(CardError):
    """Every box the card needs is named, and a tick is in one more that nothing
    reads: a set's box in no set row, or a square no label names.

    Read, the card is read as if that box were not ticked, and a forfeit ticked
    there was published as a match played. Its own kind because none of the
    doctor's causes for the other three fits a card whose every box is whole.
    """


def _no_box_named(page: pdf.Page, side: float | None, grid: _Grid,
                  boxes: Sequence[_Box]) -> CardError:
    """The refusal for a card on which a checkbox it needs could not be named, and
    why.

    A box is named in two steps, and either can come up empty: its square is
    rebuilt from the hairlines, then the label printed beside it is read. The two
    look alike from outside -- no box was named -- and a refusal that blames the
    wrong one sends whoever reads it after the half of the box that was never
    missing. So this says which step it was and what that step looked for; it is
    the only place that decides. A card on which some boxes were named is refused
    by :func:`_some_box_unnamed`, which only this calls.

    Every count and every "no" in it is this reader's: what it found by its own
    tests, and never a claim that the page has nothing else on it. A card that
    prints ``Forfeit:`` does print the word, and a card whose boxes sit 2pt off
    their labels' line does draw them beside their labels.
    """
    rows = len(grid.bands)
    want = _expected_boxes(rows)
    absent = ("no checkboxes could be located, so a forfeit, retirement or "
              "cancellation on this card would be invisible")
    if side is None:
        # A box edge outlined rather than filled is still the shape of one, and
        # "paints no hairline" would be as false of that page as the geometry
        # sentence was of a relabelled one. It is not read, because a tick is a
        # fill, and outlined it is only the box drawn again.
        outlined = sum(1 for r in page.rects if not r.filled and _edge_shaped(r))
        return UnlocatedCheckboxes(
            absent, detail=f"expected {want} boxes for {rows} set rows; "
                           + (f"the page paints no filled hairline that could be "
                              f"the top edge of a box, only {outlined} outlined "
                              "in that shape, and a box is read only from "
                              "filled ones" if outlined
                              else "the page paints no hairline that could be "
                                   "the top edge of a box"))
    outlines, _ = _box_geometry(page, side)
    if not outlines:
        return UnlocatedCheckboxes(absent, detail=f"expected {want} boxes for "
                                                  f"{rows} set rows; the commonest "
                                                  "hairline width on the page is "
                                                  f"{side:.2f}pt, and no pair of "
                                                  "edges that far apart forms a "
                                                  "square")
    if boxes:
        return _some_box_unnamed(page, side, grid, boxes, outlines)
    # The squares are there, so it is the labels. Every label the page does print
    # is named, because "prints none of them" would be as false of a card whose
    # labels sit off their boxes' line as the geometry sentence was of this one.
    flags = tuple(f"Match {flag}" for flag in _MATCH_FLAG_LABELS)
    printed = {label for label, _ in _box_labels(page)}
    found = [label for label in _SET_STATUS_LABELS + flags if label in printed]
    if found:
        # Both tests a box has to pass to be a label's, because the one it did
        # pass is no help to whoever has to move it for the one it did not.
        where = (f"this page prints {in_words(found)}, but no square is within "
                 f"{side * _BOX_LABEL_GAP:.1f}pt of "
                 f"{'it' if len(found) == 1 else 'one of them'} with its centre "
                 f"within {_BOX_ROW_TOL:g}pt of the label's, up or down"
                 + _on_the_left(found))
    else:
        near = _near_labels(page)
        where = ("no word on this page is read as one of them"
                 + (f", and the nearest it prints "
                    f"{'is' if len(near) == 1 else 'are'} {in_words(near)}"
                    if near else ""))
    return UnlabelledCheckboxes(
        "none of this card's checkboxes is beside a label this reader knows, so "
        "a forfeit, retirement or cancellation on this card would be invisible",
        detail=f"this reader finds {len(outlines)} checkbox "
               f"square{'' if len(outlines) == 1 else 's'} of {side:.2f}pt where "
               f"{rows} set rows need {want}, and names a box only by the label "
               "printed beside it; the labels it looks for, as words of their own "
               f"in any capitalisation, are {in_words(_SET_STATUS_LABELS)} for each "
               f"set and {in_words(flags)} for the match; {where}")


def _some_box_unnamed(page: pdf.Page, side: float, grid: _Grid,
                      boxes: Sequence[_Box],
                      outlines: Sequence[pdf.Rect]) -> CardError:
    """The refusal for a card on which some of the boxes it needs are named and
    some are not: which are not, and why, as far as this reader can tell.

    Each box it could not name is named by its place, which is how the boxes are
    counted: ``row 1's home Forfeit box``, and a label's every box at once when it
    names none of them. Why is three facts of this reader's own, each said only of
    the labels it is true of: a label not read anywhere on the page, a label read
    fewer times than the card has boxes for it, and a square that no label names.
    With no square left unnamed, the boxes it could not name are boxes whose square
    it cannot find, and the two tests a square has to pass to be a label's are no
    help to anyone, so they are not given. Where a whole-match flag's box is one it
    could not name, a set's box named in no set row, on that flag's side where it
    has one, is said as the other place to look: its square is in no set row,
    where a flag's box is and a set's is not.
    """
    rows = len(grid.bands)
    want = _expected_boxes(rows)
    unnamed = _unnamed(boxes, grid)
    short = len(unnamed)
    needed = Counter(label for label, _, _ in _boxes_needed(rows))
    lost = Counter(label for label, _, _ in unnamed)
    printed = Counter(label for label, _ in _box_labels(page))
    spare = ({_square_key(rect) for rect in outlines}
             - {_square_key(box.rect) for box in boxes})

    which = _unnamed_in_words(unnamed, rows)

    labels = list(lost)
    few = [label for label in labels if 0 < printed[label] < needed[label]]
    unseen = [label for label in labels if not printed[label]]
    seen = [label for label in labels if printed[label] >= needed[label]]
    why = []
    if few:
        why.append("this page prints " + in_words([
            f"{label} {'once' if printed[label] == 1 else f'{printed[label]} times'}"
            f" for its {needed[label]} boxes" for label in few]))
    if unseen:
        near = _near_labels(page, unseen)
        why.append(f"no word on this page is read as {in_words(unseen, 'or')}, "
                   "which it looks for as words of their own in any capitalisation"
                   + (f", and the nearest it prints "
                      f"{'is' if len(near) == 1 else 'are'} {in_words(near)}"
                      if near else ""))
    # Said in place of the reach test for the set's labels, which on such a page
    # is only the one label left with nothing on its left.
    right = _printed_right(page, outlines)
    told = [label for label in seen if not (right and _looks_left(label))]
    if right:
        why.append(f"{_RIGHT_OF_LABELS}: the square nearest every "
                   f"{in_words(right)} label on its line is on the label's "
                   "right, and a set's label names only a square on its left")
    if told and spare:
        why.append(f"this page prints {in_words(told)}, and a square is named by "
                   f"{'it' if len(told) == 1 else 'one of them'} only within "
                   f"{side * _BOX_LABEL_GAP:.1f}pt of the label and with its "
                   f"centre within {_BOX_ROW_TOL:g}pt of the label's, up or down"
                   + _on_the_left(told))
    # The square of a set's box in no set row can be a whole-match flag's on its
    # side, or a centred flag's, and a stray set's label beside a flag's square
    # took it where the flag's own label could not reach it: the card was told
    # that square cannot be found (4.173). A set's box is in its own row.
    flags = {flag_side for _, row, flag_side in unnamed if row is None}
    astray = [place for place in (_place_in_no_row(box, grid) for box in boxes
                                  if _place(box, grid) is None)
              if None in flags or place[2] in flags]
    squares = (f"this reader finds {len(outlines)} checkbox "
               f"square{'' if len(outlines) == 1 else 's'} of {side:.2f}pt where "
               f"{rows} set rows need {want}, and "
               + ("names a box only by the label printed beside it" if spare
                  else "every square it finds is named by the label printed "
                       "beside it, so a box it could not name is one whose square "
                       "it cannot find"
                       + (" or one whose square is named as "
                          + ("" if len(astray) == 1 else "one of ")
                          + in_words(_in_places(astray)) if astray else "")))
    # The page's kind where every square is named, where a label it needs is
    # short of a word, or where every label it could not name is one of the two
    # printed once for one box, printed nowhere, and not printed beside a square
    # in another spelling: a template's other word for that one label cannot be
    # told from a copy that lost a word of it, and a copy downloaded again tells
    # them apart. The labels' kind is what is left: a square drawn with a label
    # printed as often as the card has boxes for it and not named by it, a label
    # of two boxes read nowhere, or a label printed beside a square with
    # something else on it, as Disputed: is, which is a word changed.
    changed = _near_labels(page, unseen, beside=outlines) if unseen else []
    kind = (MissingCheckboxes
            if few or not spare or not seen and not changed
            and all(label in _CENTRED_FLAGS for label in unseen)
            else UnlabelledCheckboxes)
    return kind(
        f"this reader could not name {short} of the {want} checkboxes this card "
        f"needs, so a tick in {'it' if short == 1 else 'any of them'} would be "
        "invisible",
        detail=f"{squares}; "
               + (f"the one it could not name is {which[0]}" if short == 1
                  else f"the {short} it could not name are {in_words(which)}")
               + "".join(f"; {clause}" for clause in why))


def _box_warnings(page: pdf.Page, side: float, grid: _Grid,
                  boxes: Sequence[_Box]) -> list[str]:
    """The reader's warnings about the boxes of a card on which every box it
    needs is named, or, raised, its refusal of one with a tick nothing reads.

    The one source of both, for :func:`read_page` and :func:`describe`, so that
    the reader's summary gives the warnings its reading gives. A box drawn again
    in its row, or a whole-match flag's anywhere, is read, ticked or not. A set's
    box in no set row is read by no row, and a square no label names by nothing,
    so a tick in either is read as no tick: a forfeit ticked there published the
    match as played. So it is refused, as a box the reader cannot name is, naming
    where the box is. Unticked, each is only a layout this reader was not written
    for, and is warned of.
    """
    outlines, fills = _box_geometry(page, side)
    squares = _unnamed_squares(outlines, boxes)
    astray = [_place_in_no_row(box, grid) for box in boxes
              if box.ticked and _place(box, grid) is None]
    lost = [_square_place(rect, grid)
            for rect in _ticked_squares(squares, boxes, side, fills)]
    if astray or lost:
        ticks = len(astray) + len(lost)
        which = _in_places(astray) + _in_places(lost)
        raise UnreadTick(
            "this card ticks "
            + ("a checkbox" if ticks == 1 else f"{ticks} checkboxes")
            + " this reader reads nothing from, so it would be read as if "
            + ("that box were" if ticks == 1 else "those boxes were")
            + " not ticked",
            detail=f"{_squares_counted(grid, boxes, outlines)}; "
                   + (f"the tick is in {which[0]}" if ticks == 1
                      else f"the {ticks} ticks are in {in_words(which)}"))
    if len(boxes) == _expected_boxes(len(grid.bands)) and not squares:
        return []
    return [_more_named(grid, boxes, outlines, squares)]


def _squares_counted(grid: _Grid, boxes: Sequence[_Box],
                     outlines: Sequence[pdf.Rect]) -> str:
    """How many squares the labels name, of how many found and how many needed:
    the one wording of the counts, for the warning and the refusal."""
    rows = len(grid.bands)
    return (f"labels name {len(boxes)} of the {len(outlines)} checkbox squares "
            f"found, where {rows} set rows need {_expected_boxes(rows)}")


def _unnamed_squares(outlines: Sequence[pdf.Rect],
                     boxes: Sequence[_Box]) -> list[pdf.Rect]:
    """Every square rebuilt that no label names, top to bottom and then left to
    right, which is the order a club finds them in."""
    named = {_square_key(box.rect) for box in boxes}
    return sorted((rect for rect in outlines if _square_key(rect) not in named),
                  key=lambda rect: _square_key(rect)[::-1])


def _ticked_squares(squares: Sequence[pdf.Rect], boxes: Sequence[_Box],
                    side: float, fills: Sequence[pdf.Rect]) -> list[pdf.Rect]:
    """Each of ``squares``, which no label names, that a tick no named box reads
    is in, once for each such tick.

    By the tick, because a tick is one fill however many outlines are painted
    round it. A fill a named box reads is that box's tick: a ticked box whose
    outline is painted again a point off is a square no label names over a tick
    its box reads, and asking whether any fill on the page is in that square
    would refuse a card that is read correctly. And a square rebuilt twice over
    one fill holds one tick, not the two it would hold asked square by square.
    """
    unread = [fill for fill in fills
              if not any(_is_ticked(box.rect, side, (fill,)) for box in boxes)]
    ticked = []
    for rect in squares:
        mine = [fill for fill in unread if _is_ticked(rect, side, (fill,))]
        if mine:
            ticked.append(rect)
            unread = [fill for fill in unread if fill not in mine]
    return ticked


def _place_in_no_row(box: _Box, grid: _Grid) -> _Place:
    """The place of a set's box in no set row, which no row reads: its label and
    its side, as :func:`_in_places` counts it. For the warning and the refusal."""
    return (box.label, None, grid.side_of(box.rect.cx))


def _square_place(rect: pdf.Rect, grid: _Grid) -> _Place:
    """The place of a square no label names: its row, if it is in one, and its
    side, as :func:`_in_places` words them."""
    return ("", next((index for index, band in enumerate(grid.bands)
                      if _in_band(rect, band)), None), grid.side_of(rect.cx))


def _more_named(grid: _Grid, boxes: Sequence[_Box],
                outlines: Sequence[pdf.Rect], squares: Sequence[pdf.Rect]) -> str:
    """The warning for a card whose labels name every box it needs, and which
    draws more squares than those: how many more, and where each of them is.

    By place, in the refusal's words for a box, because "one more" alone sends
    whoever reads it through every box on the card. A box in a place another box
    is named in first is that place's box again, or twice more, a set's box in no
    set row is one no row reads, and a square no label names is said by its row
    and side, as the box whose label is lost is (4.170). ``outlines`` are every
    square found, and ``squares`` the ones no label names.
    """
    rows = len(grid.bands)
    want = _expected_boxes(rows)
    first: set[_Place] = set()
    again: list[_Place] = []
    astray: list[_Place] = []
    for box in boxes:
        place = _place(box, grid)
        if place is None:
            astray.append(_place_in_no_row(box, grid))
        elif place in first:
            again.append(place)
        else:
            first.add(place)
    # Places named again as many times over are worded together, as the refusal
    # words a row's side's boxes together.
    by_times: dict[int, list[_Place]] = {}
    for place, times in Counter(again).items():
        by_times.setdefault(times, []).append(place)
    which = [f"{phrase} " + ("again" if times == 1 else "twice more" if times == 2
                             else f"{times} times more")
             for times, places in by_times.items()
             for phrase in _in_places(places)] + _in_places(astray)
    which += _in_places([_square_place(rect, grid) for rect in squares])
    extra = len(boxes) - want + len(squares)
    return (f"{_squares_counted(grid, boxes, outlines)}; "
            + (f"the one more is {which[0]}" if extra == 1
               else f"the {extra} more are {in_words(which)}"))


def _on_the_left(labels: Sequence[str]) -> str:
    """The side test :func:`_read_boxes` holds ``labels`` to, as a clause to end
    the tests a square has to pass, or nothing when none of them has one.

    A set's label names only a square on its left, and a whole-match flag one on
    either side, so the test is said of the set's labels alone, and by name where
    the flags are in the same sentence. Which labels those are is
    :func:`_looks_left`'s to say, as it is for the reading.
    """
    left = [label for label in labels if _looks_left(label)]
    if not left:
        return ""
    return (", and on the label's left" if len(left) == len(labels)
            else f", and on the label's left for {in_words(left)}")


def _unnamed_in_words(unnamed: Sequence[_Place], rows: int) -> list[str]:
    """Each of the places ``unnamed`` on a card with ``rows`` set rows, as
    :func:`_in_places` words it, and every box of a label at once where none of
    them is named.

    For the refusal's boxes it could not name and for the line of the reader's
    own summary that counts them, so the two give a lost box the same words. The
    two centred flags are one box each, and are named as that.
    """
    needed = Counter(label for label, _, _ in _boxes_needed(rows))
    lost = Counter(label for label, _, _ in unnamed)
    return _in_places(unnamed, {label for label in lost
                                if label not in _CENTRED_FLAGS
                                and lost[label] == needed[label]})


def _in_places(places: Sequence[_Place],
               whole: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Each of ``places`` as a phrase, in the order they come: ``row 1's home
    Forfeit box``, one phrase for each row's side, one for each whole-match box,
    and one for every box of a label in ``whole`` at once.

    The one wording of a box's place, for the refusal's boxes it could not name
    and the warning's boxes more than a card needs, and of a square no label
    names. A set's box in no set row is counted by its side, since no row reads
    it and so it has no row to be named by, and so is a square in no set row.
    """
    groups: dict[tuple[str | int | None, ...], list[str]] = {}
    for label, row, box_side in places:
        key = ((label,) if label in whole
               else (row, box_side, None) if not label
               else (row, box_side, "" if row is not None else label))
        groups.setdefault(key, []).append(label)
    which = []
    for key, labels in groups.items():
        if len(key) == 1:
            which.append(f"both {key[0]} boxes" if len(labels) == 2
                         else f"all {len(labels)} {key[0]} boxes")
            continue
        row, box_side, label = key
        if label is None:
            where = (f"on row {row + 1}'s {box_side} side" if row is not None
                     else f"on the {box_side} side in no set row")
            which.append(("a square" if len(labels) == 1
                          else f"{len(labels)} squares")
                         + f" {where}, which no label names")
            continue
        plural = "es" if len(labels) > 1 else ""
        if row is None and label in _SET_STATUS_LABELS:
            count = (len(labels) if len(labels) > 1
                     else "an" if box_side == "away" else "a")
            which.append(f"{count} {box_side} {label} box{plural} in no set row, "
                         "which no row reads")
            continue
        where = (f"row {row + 1}'s {box_side} " if row is not None
                 else f"the {box_side} " if box_side is not None else "the ")
        which.append(f"{where}{in_words(labels)} box{plural}")
    return which


def _near_labels(page: pdf.Page, labels: Sequence[str] = (),
                 beside: Sequence[pdf.Rect] | None = None) -> list[str]:
    """Each word that spells a label with something else printed on it, quoted.

    Not a second test of whether a label is printed -- :func:`_box_labels` is the
    one test, and nothing is named by this -- but the answer to the question a
    refusal over the labels raises next: what does the card print instead.
    ``Forfeit:`` is that, and so is ``Winner`` without ``Match`` before it. Only
    for ``labels``, when they are given, because a label that was read is not one
    the card prints something else instead of.

    Only the words on the line of one of the squares ``beside``, when they are
    given, and not a label's word printed as a word of its own: the question a
    refusal's kind asks, whether a label is printed beside a square in another
    spelling. ``Disputed:`` there is a word changed. ``Disputed`` alone is the
    word a copy that lost ``Match`` kept, and a word in the card's comments is
    on the line of no square.
    """
    asked = {label.split()[-1] for label in labels}
    words = [label.casefold() for label in _SET_STATUS_LABELS + _MATCH_FLAG_LABELS
             if not asked or label in asked]

    def letters(text: str) -> str:
        return "".join(ch for ch in text if ch.isalpha()).casefold()

    printed = {w.text.strip() for w in page.words if letters(w.text) in words
               and (beside is None or w.text.strip().casefold() not in words
                    and any(_gaps(w, rect) is not None for rect in beside))}
    return [f"'{text}'" for text in sorted(
        printed, key=lambda text: (words.index(letters(text)), text))]


def _ticked(boxes: Iterable[_Box], label: str, *,
            band: tuple[float, float] | None = None,
            side: str | None = None, grid: _Grid | None = None) -> bool:
    """Is ``label``'s box ticked, optionally within one row and on one side?"""
    for box in boxes:
        if box.label != label or not box.ticked:
            continue
        if band is not None and not _in_band(box.rect, band):
            continue
        if side is not None and grid is not None:
            if grid.side_of(box.rect.cx) != side:
                continue
        return True
    return False


def _sided_flag(label: str, boxes: Sequence[_Box], grid: _Grid,
                warnings: list[str]) -> str | None:
    """Which side's ``label`` box is ticked, if exactly one of them is.

    Both ticked is the case that mattered: the predecessor treated a match
    flagged as a forfeit by both sides as a forfeit *won by both*, and paid the
    forfeit value twice. Neither this nor an ambiguous tick names a side, and
    both are said out loud instead.

    How many of the boxes were found is not this function's to say.
    :func:`read_page` asks it only of a card with every box it needs named,
    having refused any other, for its header or banner where either fails first
    and otherwise by :func:`_no_box_named` for each box it could not name; and
    :func:`describe`, which asks it of a refused card too, names each box not
    named in its checkbox line. So a count here only said that loss again, and
    "may be incomplete" of a card that is refused.
    """
    ticked = {grid.side_of(box.rect.cx) for box in boxes
              if box.label == label and box.ticked}
    if len(ticked) == 1:
        return next(iter(ticked))
    if len(ticked) > 1:
        warnings.append(
            f"both sides' '{label}' boxes are ticked, so which side it applies "
            "to cannot be read from this card")
    return None


def _centred_flag(label: str, boxes: Iterable[_Box]) -> bool:
    return _ticked(boxes, label)


# --- set rows ------------------------------------------------------------


def _read_row(page: pdf.Page, grid: _Grid, index: int,
              band: tuple[float, float], boxes: Sequence[_Box],
              warnings: list[str]) -> ParsedSet:
    """One row of the set table, transcribed in the row's own terms."""
    marker, doubles = _read_marker(page, grid, index, band, warnings)
    home_games = _games(page, band, grid.home_games, index, "home", warnings)
    away_games = _games(page, band, grid.away_games, index, "away", warnings)
    home_players, home_unnamed = _named(
        _players(page, band, grid.home_names, boxes))
    away_players, away_unnamed = _named(
        _players(page, band, grid.away_names, boxes))

    wanted = 2 if doubles else 1
    for side, players in (("home", home_players), ("away", away_players)):
        if players and len(players) != wanted:
            warnings.append(
                f"row {index + 1}: read {len(players)} {side} player name(s) "
                f"from a row its own marker {marker!r} says is "
                f"{'doubles' if doubles else 'singles'}")

    home_forfeit = _ticked(boxes, "Forfeit", band=band, side="home", grid=grid)
    away_forfeit = _ticked(boxes, "Forfeit", band=band, side="away", grid=grid)
    if home_forfeit or away_forfeit:
        status = SetStatus.FORFEIT
    elif _ticked(boxes, "Retired", band=band):
        status = SetStatus.RETIRED
    elif _ticked(boxes, "Incomplete", band=band):
        status = SetStatus.INCOMPLETE
    else:
        # Whether an untidied score is a finished set is a question about the set
        # rule, which is configuration and not on the card. The predecessor
        # answered it here with a bare `< 6`, which read a completed FAST4 set as
        # unplayed; SetResult.completed answers it later, with the rule in hand.
        status = SetStatus.PLAYED
    return ParsedSet(
        index=index,
        doubles=doubles,
        marker=marker,
        home_games=home_games,
        away_games=away_games,
        home_players=home_players,
        away_players=away_players,
        status=status,
        home_forfeit=home_forfeit,
        away_forfeit=away_forfeit,
        unnamed=home_unnamed + away_unnamed,
    )


def _read_marker(page: pdf.Page, grid: _Grid, index: int,
                 band: tuple[float, float],
                 warnings: list[str]) -> tuple[str, bool]:
    """The row's discipline, from the row's own marker column.

    ``1&2`` is a pairing and therefore a doubles row; a bare ``1`` is singles.
    This is the card stating its own format, one row at a time, and it is the
    reason this reader does not need to be told how many sets a match has. A row
    that states nothing is refused rather than assumed: a card with a row's
    marker missing and a club that changed format read the same way, and they need
    opposite responses.
    """
    readings: dict[str, list[str]] = {}
    for side in ("home", "away"):
        cell = grid.marker(side)
        if cell is None:
            continue
        found = page.words_in_band(band[0], band[1], *cell.inner)
        # One printed line at a time, because the cell holds two: the row number
        # above and the discipline marker below it.
        for line in pdf.group_lines(found):
            words = [word.text.strip() for word in line if word.text.strip()]
            # The line whole, first. A template that puts spaces around the
            # ampersand prints `1 & 2` as three words, and read one word at a time
            # that is a `1`, a nothing and a `2`: singles, on a row naming two
            # players a side. Read whole it is the pairing it looks like, and a
            # tight `1&2` -- one word -- comes through here to the same answer, so
            # there is no `len(words) > 1` guard: it would be a branch no card can
            # tell the difference across. Only a pairing can match here --
            # `^\d+$` cannot span a space -- so nothing that used to read as
            # singles changes meaning.
            if _PAIR_MARKER_RE.match(" ".join(words)):
                readings.setdefault("doubles", []).append(" ".join(words))
                continue
            # Otherwise a word at a time, as before: a line can hold more than the
            # marker -- a template that labels the rubber `Doubles 1&2` means nothing
            # as one string -- and a line holding a pairing beside a stray number
            # must still produce both readings, because that disagreement is
            # reported rather than resolved here.
            for text in words:
                if _PAIR_MARKER_RE.match(text):
                    readings.setdefault("doubles", []).append(text)
                elif _SINGLE_MARKER_RE.match(text):
                    readings.setdefault("singles", []).append(text)
    if not readings:
        cells = [c for c in (grid.home_marker, grid.away_marker) if c is not None]
        printed = sorted({w.text for cell in cells
                          for w in page.words_in_band(band[0], band[1],
                                                      *cell.inner)})
        raise CardError(
            f"row {index + 1} of the set table prints no discipline marker, so "
            "whether it is singles or doubles cannot be read from the card",
            detail="marker column(s) hold " + (", ".join(printed) or "nothing"))
    if len(readings) > 1:
        warnings.append(
            f"row {index + 1}: the two marker columns disagree "
            f"({readings}); read as doubles")
    doubles = "doubles" in readings
    marker = readings["doubles" if doubles else "singles"][0]
    return marker, doubles


def _games(page: pdf.Page, band: tuple[float, float], cell: _Cell, index: int,
           side: str, warnings: list[str]) -> int:
    """The games figure in one cell of a set row.

    A blank cell reads as zero with a warning rather than raising: a card left
    partly unfilled is a real thing, and the cross-checks will catch it if the
    zero is wrong. So does a cell printing no figure
    :func:`~matchcentre.competition.decimal_figure` reads, a ``³`` say, and its
    warning quotes what the cell prints (4.216).
    """
    printed = [w.text for w in page.words_in_band(band[0], band[1], *cell.inner)]
    figures = [(text, figure) for text in printed
               if (figure := decimal_figure(text)) is not None]
    if len(figures) == 1:
        return figures[0][1]
    if not figures:
        warnings.append(
            f"row {index + 1}: the {side} games figure printed, "
            f"{' '.join(printed)!r}, is not a number, read as 0" if printed else
            f"row {index + 1}: no {side} games figure printed, read as 0")
        return 0
    warnings.append(
        f"row {index + 1}: {len(figures)} numbers in the {side} games column "
        f"({in_words([text for text, _ in figures])}); used the first")
    return figures[0][1]


def _players(page: pdf.Page, band: tuple[float, float], cell: _Cell,
             boxes: Sequence[_Box]) -> tuple[str, ...]:
    """Printed player names from one side of a set row, in the printed order.

    Kept exactly as the card spells them, mixed case and all: turning them into
    slugs here is what left the predecessor unable to display a name with
    internal capitals anywhere in the dashboard.

    The per-set status labels share this column, and they are recognised by
    geometry -- a line with a checkbox beside it is a label, not a person -- so a
    template that words them differently does not turn ``Retired`` into a player.
    """
    beside = [box.rect.cy for box in boxes if cell.holds(box.rect.cx)]
    out: list[str] = []
    for line in _lines_in(page, band, cell):
        centre = sum(w.cy for w in line) / len(line)
        if any(abs(cy - centre) <= _BOX_ROW_TOL * 2 for cy in beside):
            continue
        if any(w.text.strip().casefold() in
               {label.casefold() for label in _SET_STATUS_LABELS} for w in line):
            continue
        name = " ".join(w.text for w in line).strip()
        if name:
            out.append(name)
    return tuple(out)


def _named(printed: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
    """The names from one cell, and how many of them named nobody.

    Splitting rather than filtering keeps the absence reportable: a card can then
    be told the difference between *this rubber's names were never entered* and
    *this rubber's name cell was empty*, which read the same way once the
    placeholder is gone.
    """
    kept = tuple(name for name in printed if not names_nobody(name))
    return kept, len(printed) - len(kept)


def _lines_in(page: pdf.Page, band: tuple[float, float],
              cell: _Cell | tuple[float, float]) -> list[list[pdf.Word]]:
    """Words inside a cell, grouped into printed lines by shared baseline."""
    bounds = cell.inner if isinstance(cell, _Cell) else cell
    lines: list[list[pdf.Word]] = []
    for word in page.words_in_band(band[0], band[1], *bounds):
        if lines and abs(lines[-1][-1].baseline - word.baseline) <= _BASELINE_TOL:
            lines[-1].append(word)
        else:
            lines.append([word])
    return lines


def _check_expected_format(sets: Sequence[ParsedSet],
                           expect: MatchFormat) -> None:
    """Refuse a card that describes a different match than the caller expects.

    Neither reading wins. A declared format quietly overriding the card scores a
    truncated or misread card as though it were whole; the card quietly
    overriding the config hides a competition that changed shape mid-season. Both
    are named and the run stops.
    """
    described = tuple(s.doubles for s in sets)
    declared = tuple(slot.doubles for slot in expect.slots)
    if described == declared:
        return
    raise CardError(
        "the card and the declared match format describe different matches",
        detail=f"the card's {len(described)} row(s) read "
               f"{_shape(described)}; the declared format {expect.name} is "
               f"{_shape(declared)}")


def _shape(flags: Sequence[bool]) -> str:
    spelled = "-".join("doubles" if flag else "singles" for flag in flags)
    try:
        return f"{from_rows(flags).pattern} ({spelled})"
    except FormatError:
        return spelled or "no rows"


# --- the printed TOTAL row -----------------------------------------------


def _read_totals(page: pdf.Page, grid: _Grid, warnings: list[str]) -> Totals:
    """The card's own ``TOTAL`` row, as far as it prints one.

    Each figure is assigned to the heading it sits under and then to a side by
    which half of the table it is in, so the four numbers cannot be swapped by
    reading order -- which is the failure this row exists to catch.

    A figure is what :func:`~matchcentre.competition.decimal_figure` reads. Where a
    heading has none and prints something else, a ``³`` or a ``-`` say, the warning
    quotes every word of it and stands for the one saying that figure is missing. A
    games figure left blank beside it is still warned about (4.216).
    """
    total = next((w for w in page.words
                  if w.text.strip().rstrip(":").casefold() in ("total", "totals")
                  and w.cy > grid.bottom), None)
    if total is None:
        return Totals()
    headings = [w for w in page.words
                if w.text.strip().casefold() in ("sets", "games")
                and 0 < total.cy - w.cy < _TOTALS_REACH]
    below = [w for w in page.words
             if 0 < w.cy - total.cy < _TOTALS_REACH
             and grid.rules[0] <= w.cx <= grid.rules[-1]]
    if not headings:
        warnings.append(
            "the card prints a TOTAL row with no Sets or Games headings above "
            "it, so its own totals were not read")
        return Totals()

    values: dict[tuple[str, str], int] = {}
    unread: dict[tuple[str, str], list[str]] = {}
    for word in below:
        heading = min(headings, key=lambda w: abs(w.cx - word.cx))
        if abs(heading.cx - word.cx) > _TOTALS_COLUMN_TOL:
            continue
        key = (grid.side_of(word.cx), heading.text.strip().casefold())
        figure = decimal_figure(word.text)
        if figure is None:
            unread.setdefault(key, []).append(word.text)
        else:
            values.setdefault(key, figure)
    totals = Totals(home_sets=values.get(("home", "sets")),
                    home_games=values.get(("home", "games")),
                    away_games=values.get(("away", "games")),
                    away_sets=values.get(("away", "sets")))
    named = [key for key in unread if key not in values]
    for side, heading in named:
        warnings.append(
            f"the card's TOTAL row prints {' '.join(unread[side, heading])!r} as the "
            f"{side} {heading}, which is not a number, so that figure was not read")
    if not totals.present and not named:
        warnings.append(
            "the card's TOTAL row has no figures under its headings, so its own "
            "totals were not read")
    elif any((side, "games") not in values and (side, "games") not in named
             for side in ("home", "away")):
        warnings.append(
            "the card's TOTAL row prints no games figure for one side, so the "
            "strongest cross-check on this card is only partly available")
    return totals


# --- cross-checks (rule F6) ----------------------------------------------


def _row_claims(sets: Sequence[ParsedSet]) -> tuple[int, int]:
    """The most sets each side can possibly have won, from the rows alone.

    A side cannot win a set it did not lead, and cannot win one it forfeited, so
    this is a true upper bound however the club's set rule works. It is an upper
    bound rather than an exact count on purpose: whether ``2-4`` is a finished
    set depends on the set rule, which is configuration and not printed on the
    card, and a real card in the reference data ran out of court time at ``2-4``
    with nobody ticking ``Incomplete``. Bounding instead of guessing means this
    check never contradicts a legitimately untidy card, while still catching a
    read where the two games columns were swapped.

    A forfeited row is not bounded but answered, by the rule the model scores it
    with: a bound that credited a forfeited rubber differently from the model would
    be this check reporting a correct card as misread. So is a row whose box says
    it stopped short, retired or incomplete, which is nobody's by
    :func:`~matchcentre.model.stopped_short`. Its leader was credited, and a card
    won on its finished sets had its verb and tick reported as misread (4.217).
    """
    home = away = 0
    for parsed in sets:
        if parsed.status is SetStatus.FORFEIT:
            side = forfeit_set_winner(home_forfeit=parsed.home_forfeit,
                                      away_forfeit=parsed.away_forfeit,
                                      home_games=parsed.home_games,
                                      away_games=parsed.away_games)
        elif stopped_short(parsed.status):
            side = None
        elif parsed.home_games != parsed.away_games:
            side = "home" if parsed.home_games > parsed.away_games else "away"
        else:
            side = None
        home += side == "home"
        away += side == "away"
    return home, away


def _readings(sets: Sequence[ParsedSet]) -> tuple[str | None, str | None]:
    """Who the rows say won, on games and on sets, without a ruleset.

    Both bases are returned because a club's rules may decide on either, and this
    module has no business knowing which. A claim supported by neither is a
    contradiction whatever the rules are.
    """
    home_games = sum(s.home_games for s in sets)
    away_games = sum(s.away_games for s in sets)
    by_games = ("home" if home_games > away_games
                else "away" if away_games > home_games else None)
    home_sets, away_sets = _row_claims(sets)
    by_sets = ("home" if home_sets > away_sets
               else "away" if away_sets > home_sets else None)
    return by_games, by_sets


def _supported(claim: str, sets: Sequence[ParsedSet]) -> bool:
    by_games, by_sets = _readings(sets)
    if claim == "draw":
        return by_games is None or by_sets is None
    return claim in (by_games, by_sets)


def _left_blank(sets: Sequence[ParsedSet], forfeited_by: str | None) -> bool:
    """Whether the card is a whole-match forfeit whose set rows give no result.

    Nobody came, so nobody played, and a card for that night is written with every
    row 0-0 and the Match Forfeited box saying why. Rows that give no result on
    either basis neither confirm the box's result nor contradict it, so a verb or
    tick is held to the box instead: one that restates it agrees, since the box is
    what such a card is scored on.
    """
    return (forfeit_match_winner(forfeited_by) is not None
            and _readings(sets) == (None, None))


def forfeit_award(sets: Sequence[ParsedSet], forfeited_by: str | None,
                  fmt: MatchFormat) -> Totals | None:
    """The TOTAL row of a whole-match forfeit that totals what its box awards.

    ``None`` unless the card is one whose rows give no result (:func:`_left_blank`).
    Otherwise every rubber of *fmt* to the side that came, each a love set, as
    :attr:`MatchFormat.forfeit_worth` prices it, and nothing to the side that
    forfeited. The reader asks it under every set rule, knowing none, and validate
    under the club's, so the two cannot come to award one forfeit two ways.
    """
    if not _left_blank(sets, forfeited_by):
        return None
    return box_award(forfeited_by, fmt)


def box_award(forfeited_by: str, fmt: MatchFormat) -> Totals:
    """What a whole-match forfeit's box awards, whatever its rows say.

    Every rubber of *fmt* to the side that came, each a love set, and nothing to
    the side named, as :func:`forfeit_award` awards rows that give no result.
    Validate asks it too, of a TOTAL row printed over rows that give the match
    to the side that forfeited, so one forfeit is priced one way (4.134).
    """
    won, games = fmt.forfeit_worth
    if forfeit_match_winner(forfeited_by) == "home":
        return Totals(home_sets=won, home_games=games, away_games=0, away_sets=0)
    return Totals(home_sets=0, home_games=0, away_games=games, away_sets=won)


def _awards(sets: Sequence[ParsedSet], forfeited_by: str | None) -> dict[str, Totals]:
    """:func:`forfeit_award` under each set rule a config may name, by name.

    Over the format the card's rows describe, of which there is at least one: a
    table with no row bands is refused before any check is made of it. Empty for
    a card that is no such forfeit.
    """
    if not _left_blank(sets, forfeited_by):
        return {}
    flags = [s.doubles for s in sets]
    return {name: forfeit_award(sets, forfeited_by, from_rows(flags, rule=rule))
            for name, rule in SET_RULES.items()}


def _forfeit_text(forfeited_by: str) -> str:
    return (f"the {forfeited_by} Match Forfeited box is ticked and the set rows "
            "give no result, which is how a whole-match forfeit is written")


def _box(forfeited_by: str) -> str:
    """What a check held to the box rather than to the rows is held to, as named."""
    return f"the {forfeited_by} Match Forfeited box"


def _reading_text(sets: Sequence[ParsedSet]) -> str:
    by_games, by_sets = _readings(sets)
    home_games = sum(s.home_games for s in sets)
    away_games = sum(s.away_games for s in sets)
    home_sets, away_sets = _row_claims(sets)
    return (f"the set rows give games {home_games}-{away_games} "
            f"({by_games or 'level'}) and at most {home_sets}-{away_sets} sets "
            f"({by_sets or 'level'})")


def _totals_check(sets: Sequence[ParsedSet], totals: Totals, *,
                  forfeited_by: str | None) -> CrossCheck:
    """The printed ``TOTAL`` row against the sum of the set rows.

    A whole-match forfeit whose rows give no result (:func:`_left_blank`) may
    total what its box awards instead (:func:`forfeit_award`), as its verb is held
    to the box. What the award is worth takes the club's set rule, which validate
    holds such a row to and this cannot see, so here the row agrees if it totals
    the award under any set rule, and a row totalling another rule's is left to
    validate. One that totals neither the rows nor any award disagrees, and says
    why the rows alone fault it. No report is named: ``totals-mismatch`` reports
    what this faults, but not on a card its Match Cancelled box calls off, which F6
    asks of it instead.
    """
    if not totals.present:
        return CrossCheck("totals", False,
                          detail="the card prints no TOTAL row")
    home_games = sum(s.home_games for s in sets)
    away_games = sum(s.away_games for s in sets)
    home_claim, away_claim = _row_claims(sets)
    problems: list[str] = []
    for name, printed, summed in (("home", totals.home_games, home_games),
                                  ("away", totals.away_games, away_games)):
        if printed is not None and printed != summed:
            problems.append(f"{name} games {printed} printed against {summed} "
                            "in the rows")
    for name, printed, bound in (("home", totals.home_sets, home_claim),
                                 ("away", totals.away_sets, away_claim)):
        if printed is not None and not 0 <= printed <= bound:
            problems.append(f"{name} sets {printed} printed, but the rows allow "
                            f"at most {bound}")
    if (totals.home_sets is not None and totals.away_sets is not None
            and totals.home_sets + totals.away_sets > len(sets)):
        problems.append(f"{totals.home_sets} + {totals.away_sets} sets printed "
                        f"over {len(sets)} rows")
    awards = _awards(sets, forfeited_by) if problems else {}
    fits = [name for name, award in awards.items() if not totals.differs_on(award)]
    came = forfeit_match_winner(forfeited_by)
    if fits:
        under = "any set rule" if len(fits) == len(awards) else in_words(fits, "or")
        return CrossCheck(
            "totals", True, held_to=_box(forfeited_by),
            detail=f"{_forfeit_text(forfeited_by)}, so the printed TOTAL row is "
                   f"held to the box rather than to the rows, and totals its "
                   f"award under {under}: every rubber to {came}, each a love set")
    if awards:
        problems.append(f"nor does it total what the {forfeited_by} Match "
                        f"Forfeited box awards under any set rule, every rubber "
                        f"to {came} as a love set")
        return CrossCheck("totals", True, agrees=False, held_to=_box(forfeited_by),
                          detail="; ".join(problems))
    if problems:
        return CrossCheck("totals", True, agrees=False,
                          detail="; ".join(problems))
    return CrossCheck(
        "totals", True,
        detail=f"printed games {_shown(totals.home_games)}-"
               f"{_shown(totals.away_games)} and sets "
               f"{_shown(totals.home_sets)}-{_shown(totals.away_sets)} match the "
               f"{len(sets)} set rows")


def _verb_check(sets: Sequence[ParsedSet], verb: str, *,
                forfeited_by: str | None) -> CrossCheck:
    """The result verb printed between the team names against the set rows.

    Read through ``card.verb_side``, the one reading validate asks too, so a word
    it does not hold is no verb here either. Not against the rows of a whole-match
    forfeit that give no result (:func:`_left_blank`), but against its Match
    Forfeited box, through the function ``Match.winner`` asks, and the box is
    asked first: level rows nobody played are no evidence of a draw. A verb
    naming the side that turned up agrees. A draw, or the side that forfeited,
    disagrees, and ``verb-mismatch``, which holds every forfeit's verb to the box
    through the same reading, reports it, so here it names that report rather
    than make a second one.
    """
    claim = verb_side(verb)
    if claim is None:
        unusable = ""
        if states_no_result(verb):
            detail = (f"the card prints {verb!r} between the team names, which "
                      f"states no result to check against")
            unusable = f"it prints {verb!r} between the team names instead of a result"
        elif verb:
            detail = f"the verb {verb!r} is not one this reader recognises"
            unusable = f"its result verb {verb!r} is not one this program recognises"
        else:
            detail = "the card prints no result verb"
        return CrossCheck("verb", False, detail=detail, unusable=unusable)
    if _left_blank(sets, forfeited_by):
        held = (f"{_forfeit_text(forfeited_by)}, so {verb!r} is held to the box "
                "rather than to the rows")
        if claim == forfeit_match_winner(forfeited_by):
            return CrossCheck("verb", True, detail=f"{held}, and restates it",
                              held_to=_box(forfeited_by))
        said = ("makes the match a draw" if claim == "draw"
                else "names the side that forfeited")
        return CrossCheck("verb", True, agrees=False, reported_by="verb-mismatch",
                          detail=f"{held}, and {said}", held_to=_box(forfeited_by))
    if _supported(claim, sets):
        return CrossCheck("verb", True,
                          detail=f"{verb!r} agrees with the set rows")
    said = ("the match a draw" if claim == "draw"
            else f"{claim} the winner")
    return CrossCheck("verb", True, agrees=False,
                      detail=f"the card prints {verb!r}, making {said}, but "
                             f"{_reading_text(sets)}")


def names_nobody(text: str) -> bool:
    """Whether a printed name cell holds Match Centre's placeholder for nobody.

    The distinction this draws is between *no name was recorded* and *a person
    called this*. It matters because everything downstream identifies a player by
    the name printed in a set row: the placeholder read as a name registers a
    phantom who has played, has a rubber record, and turns up in the dashboard's
    list of players worth checking — while the club's report says nothing about it,
    because a cancelled card is exempt from the roster checks that would.
    """
    return " ".join(text.split()).casefold().strip(".") in _UNNAMED_MARKS


def _winner_tick_check(sets: Sequence[ParsedSet], winner: str | None,
                       boxes: Sequence[_Box], grid: _Grid, *,
                       forfeited_by: str | None, verb: str) -> CrossCheck:
    """The ``Match Winner`` checkbox against the set rows.

    On a whole-match forfeit whose rows give no result (:func:`_left_blank`) the
    tick is held to the Match Forfeited box, as the verb is. A tick for the side
    that turned up restates the box and agrees. One for the side that forfeited
    disagrees with it. When the verb names the same side, read through
    ``card.verb_side`` as the verb check reads it, ``verb-mismatch`` reports the
    verb and so the tick. When it does not, this check reports it. On rows
    the verb already disagrees with for the side that turned up, a tick for that
    side is the same contradiction, and the verb check reports it. Any other tick
    is held to the rows.

    Asked only of a card whose two Match Winner boxes are both named: by
    :func:`read_page` of one that reads, and by :func:`describe` of any card it
    does not name a Match Winner box as lost on, refused or not. So how many
    were found is the refusal's and that line's to say, and not this check's.
    """
    ticked = [box for box in boxes if box.label == "Match Winner" and box.ticked]
    if not ticked:
        return CrossCheck("winner-tick", False,
                          detail="neither Match Winner box is ticked")
    if winner is None:
        return CrossCheck("winner-tick", True, agrees=False,
                          detail="both Match Winner boxes are ticked, so the "
                                 "card does not say who won")
    if _supported(winner, sets):
        return CrossCheck("winner-tick", True,
                          detail=f"the {winner} Match Winner tick agrees with "
                                 "the set rows")
    turned_up = forfeit_match_winner(forfeited_by)
    if _left_blank(sets, forfeited_by):
        if winner == turned_up:
            return CrossCheck("winner-tick", True, held_to=_box(forfeited_by),
                              detail=f"the {winner} Match Winner tick restates the "
                                     f"box: {_forfeit_text(forfeited_by)}")
        if winner == verb_side(verb):
            return CrossCheck("winner-tick", True, agrees=False,
                              reported_by="verb-mismatch", held_to=_box(forfeited_by),
                              detail=f"{_forfeit_text(forfeited_by)}, so the "
                                     f"{winner} Match Winner tick is held to the "
                                     "box, and names the side that forfeited, as "
                                     f"{verb!r} does")
        return CrossCheck("winner-tick", True, agrees=False, held_to=_box(forfeited_by),
                          detail=f"the card ticks {winner} as Match Winner, but "
                                 f"the {forfeited_by} Match Forfeited box is "
                                 f"ticked, giving the match to {turned_up}, and "
                                 "the set rows give no result")
    if winner == turned_up and verb_side(verb) == winner:
        return CrossCheck("winner-tick", True, agrees=False,
                          reported_by="the verb cross-check",
                          detail=f"the {winner} Match Winner tick restates the "
                                 f"{forfeited_by} Match Forfeited box, as "
                                 f"{verb!r} does, so the set rows disagreeing "
                                 "with both are reported once, by the verb")
    return CrossCheck("winner-tick", True, agrees=False,
                      detail=f"the card ticks {winner} as Match Winner, but "
                             f"{_reading_text(sets)}")


# --- roster block --------------------------------------------------------


def _read_rosters(page: pdf.Page, grid: _Grid, warnings: list[str],
                  sets: Sequence[ParsedSet], dropped: list[str]
                  ) -> tuple[tuple[RosterEntry, ...], tuple[RosterEntry, ...]]:
    """The two squads printed at the foot of the card, as printed.

    Both the properly-cased name and the ``(C)`` captain mark are kept. The
    predecessor slugged the name and dropped the mark, after which it had to
    demand captains from configuration and could not render a name with internal
    capitals. Ratings are per card, so a mid-season re-rating is captured as at
    that round, and a player with no rating still appears on the roster.

    The block ends at the first heading under it: a word beginning ``comment``, or
    ``fill-in``, ``reserve`` or ``substitute`` on a fill-ins heading's line
    (:func:`_heads_fill_ins`), over either squad, a line printed for both teams that
    reads the same but for ``home`` and ``away`` (:func:`_heads_both_sides`), or an
    unrated line whose name could not be anybody's (:func:`_could_be_a_name`), which
    the card is told about because it may be a player. When none of those ends them,
    or only a line with no letter in it does (:attr:`_SquadEnd.at_heading`), and the
    block ends at ``comment`` or the foot of the page, one line printed word for word
    the same across both squads that nobody played ends them too
    (:func:`_printed_over_both`), and the card is told that as well. Unless one of
    those words is on the page, both squads need one of those headings under them,
    or nothing says where they stop and neither is read, as with no ``Players``
    heading at all. A line printed too close above the heading the block ends at to
    be in the block, under a squad that has not ended, is left out, and the card is
    told (:func:`_too_close_above`). Where the block does not end at a fill-ins
    heading, a line a heading over one squad alone would be is kept in the squad,
    and the card is told it may be one (:func:`_maybe_a_heading`).

    Each line a warning quotes as a heading or as left out goes on *dropped* too, as
    it is quoted: it is in neither squad, so the run learns it from there to keep it
    off a page that withholds names. A line kept in a squad is learned as a player.
    """
    header = _players_heading(page, grid.bottom)
    if header is None:
        warnings.append(
            "no 'Players' heading below the set table, so neither team's "
            "registered squad was read from this card")
        return (), ()
    # Bound the block by the *top* of the next heading, not its baseline: rows
    # are selected on their vertical centre, which sits above the baseline, so a
    # baseline bound lets the heading itself in as a roster line. With no heading
    # the reader knows, the band runs to the foot of the page and only a heading
    # under each squad can end it.
    marker = _block_end(page, header)
    marked = None if marker is None else marker.y0
    band = (header.y1 + _CELL_INSET,
            (page.height if marked is None else marked) - _CELL_INSET)
    columns = ((float("-inf"), grid.divider), (grid.divider, float("inf")))
    lines = [_lines_in(page, band, bounds) for bounds in columns]
    left, right = ([[w.text for w in line] for line in side] for side in lines)
    (home, home_end, home_rows), (away, away_end, away_rows) = (_squad(left, right),
                                                                _squad(right, left))
    home_at, away_at = ([side[k][0].baseline for k in rows]
                        for side, rows in zip(lines, (home_rows, away_rows)))
    # Only the word the block ends at says the card prints a fill-ins heading: under
    # `Comments` the same word is what somebody wrote. A squad that ended at a `-`
    # printed under a heading has not ended at the heading, so that is still asked.
    fill_ins = marker is not None and _heading_word(marker.text) == "fill-ins"
    if not (fill_ins or home_end.at_heading or away_end.at_heading):
        over_both = _printed_over_both(home, home_at, away, away_at, sets)
        if over_both is not None:
            i, j = over_both
            warnings.append(_taken_as_printed_over_both(home[i].name))
            dropped.append(home[i].name)
            home, away = home[:i], away[:j]
            home_end = away_end = _SquadEnd(ended=True)
    if marked is None and not (home_end.ended and away_end.ended):
        warnings.append(ROSTER_BLOCK_UNBOUNDED)
        return (), ()
    taken = [(side, end.stray) for side, end in (("home", home_end),
                                                 ("away", away_end)) if end.stray]
    if taken:
        warnings.append(_taken_for_a_heading(taken))
        dropped.extend(line for _, line in taken)
    if marker is not None:
        unread = [(side, name) for side, end, bounds in zip(
                      ("home", "away"), (home_end, away_end), columns)
                  if not end.ended
                  for name in _too_close_above(page, band, marker, bounds)]
        if unread:
            warnings.append(_left_out_too_close_above(unread, _heading_text(marker)))
            dropped.extend(name for _, name in unread)
    if not fill_ins:
        maybe = [(side, line) for side, end, line in (
                     ("home", home_end, _maybe_a_heading(home, home_at, away, away_at,
                                                         sets)),
                     ("away", away_end, _maybe_a_heading(away, away_at, home, home_at,
                                                         sets)))
                 if line and not end.at_heading]
        if maybe:
            warnings.append(_taken_as_a_player(maybe))
    if not home or not away:
        warnings.append(
            f"read {len(home)} home and {len(away)} away roster "
            "entries, so one side's registered squad is missing from this card")
    return home, away


# Each word besides the template's `Fill-ins` that a fill-ins heading has been
# printed in, casefolded: `Reserves` and `Substitutes`, with or without a side named,
# and in the singular. Whole words, since a comment's `reserved` or `substituted`
# is not one.
_FILL_INS_WORDS = ("reserve", "reserves", "substitute", "substitutes")

# What a fill-ins heading prints beside its word to say whose it is, besides `home`
# and `away` (_SIDE_WORD_RE), casefolded: `Fill-ins (Home Team)`, `Visitors
# Reserves`, `Reserve Players`. Side words to a heading's line only: two copies of
# one heading differ by `home` and `away` alone (_heads_both_sides).
_HEADING_SIDE_WORDS = ("team", "visitors", "visiting", "host", "players", "player")


def _heading_word(text: str) -> str:
    """Which heading under the roster block a printed word marks, if any.

    ``"fill-ins"`` for a word beginning ``fill-in``, or one of
    :data:`_FILL_INS_WORDS` whole but for the punctuation round it, ``Reserves:`` or
    ``(Substitutes)``; ``"comments"`` for one beginning ``comment``; and ``""`` for
    any other. A word: whether the line it is on is a heading is
    :func:`_heads_fill_ins`'s to say.
    """
    word = text.strip().casefold()
    if (word.startswith("fill-in")
            or word.strip(string.punctuation) in _FILL_INS_WORDS):
        return "fill-ins"
    return "comments" if word.startswith("comment") else ""


def _heads_fill_ins(line: str) -> bool:
    """Whether a printed line, judged whole across the card, is a fill-ins heading.

    Whole, since the template prints its fill-ins headings on one row, so a fill-in
    printed under the other column, with no heading over it, is under that row too.
    The line holds a fill-ins word (:func:`_heading_word`), and each other word is
    another, a side word (:data:`_SIDE_WORD_RE`, :data:`_HEADING_SIDE_WORDS`), one
    with a bracket in it, as a side named in brackets is, one with no letter or digit
    in it, or one printed before a bracketed heading word, as a team's name is in
    ``Bravos (Substitutes)``. So ``Reserves:``, ``Home Team Fill-ins``, ``Fill-ins
    (Alphas)`` and ``Reserve Players`` are headings, and ``Reserves arrived late``,
    ``Substitutes: 2``, ``Substitute Golf Garside`` and a squad member rated as
    ``Reserve Quill`` are not: a rating is digits.
    """
    words = line.split()
    heads = [_heading_word(word) == "fill-ins" for word in words]
    bracketed = max((k for k, word in enumerate(words)
                     if heads[k] and word[:1] in "([{"), default=-1)
    for k, word in enumerate(words):
        bare = word.casefold().strip(string.punctuation)
        if not (heads[k] or k < bracketed or not any(c.isalnum() for c in word)
                or any(c in "()[]{}" for c in word)
                or _SIDE_WORD_RE.fullmatch(bare) or bare in _HEADING_SIDE_WORDS):
            return False
    return any(heads)


def _heading_on_line(page: pdf.Page, word: pdf.Word) -> str:
    """:func:`_heading_word` for a word on the page, but a fill-ins word only on a
    line that is a fill-ins heading (:func:`_heads_fill_ins`)."""
    heading = _heading_word(word.text)
    if heading != "fill-ins":
        return heading
    line = " ".join(w.text for w in sorted(page.words, key=lambda w: w.x0)
                    if abs(w.baseline - word.baseline) <= _BASELINE_TOL)
    return heading if _heads_fill_ins(line) else ""


def _players_heading(page: pdf.Page, floor: float) -> pdf.Word | None:
    """The roster block's ``Players`` heading, the first printed below *floor*."""
    return next((w for w in page.words
                 if w.text.strip().casefold() in ("players", "player")
                 and w.cy > floor), None)


def _block_end(page: pdf.Page, header: pdf.Word) -> pdf.Word | None:
    """The word the roster block under *header* ends at, if the page prints one.

    The topmost under it that :func:`_heading_on_line` marks: ``Comments``, or a
    fill-ins word on a heading's line.
    """
    return min((w for w in page.words
                if _heading_on_line(page, w) and w.y0 > header.y0),
               key=lambda w: w.y0, default=None)


def fill_ins_heading(page: pdf.Page) -> str:
    """The fill-ins heading the reader ends a page's roster block at, or "".

    The word :func:`_read_rosters` ends the block at, the punctuation round it aside:
    ``Reserves`` for ``Reserves:``, ``Substitutes`` for ``Bravos (Substitutes)``, and
    "" where the block ends at ``Comments`` or at nothing. It is under the ``Players``
    heading below the set table, so a line above it that reads like a heading, the
    result line of a team named ``Bravos (Reserves)``, is not the one. A page whose set
    table does not resolve, which the reader refuses, is asked from its top, so that
    what the page prints can be shown. Public because tools/doctor.py asks it, and
    what the doctor finds is then what the reader ends the block at.
    """
    try:
        floor = _grid(page, []).bottom
    except CardError:
        floor = float("-inf")
    header = _players_heading(page, floor)
    marker = None if header is None else _block_end(page, header)
    if marker is None or _heading_word(marker.text) != "fill-ins":
        return ""
    return _heading_text(marker)


def _heading_text(marker: pdf.Word) -> str:
    """How a note and the doctor name the heading the roster block ends at.

    By its word, the punctuation round it aside: ``Reserves`` for ``Reserves:``.
    """
    return marker.text.strip(string.punctuation)


@dataclass(frozen=True, slots=True)
class _SquadEnd:
    """Where one side's squad stopped: at a heading, and if so whether a named one.

    ``stray`` is the line taken for a heading because no name could hold it, kept
    to quote, since it may be a player; a heading printed for both teams leaves it
    empty.
    """

    ended: bool
    stray: str = ""

    @property
    def at_heading(self) -> bool:
        """Whether the squad ended at a line that heads what is under it.

        A line with no letter in it, ``-``, ``--`` or ``...``, heads nothing: it is
        what a template prints under a fill-ins heading nobody filled in, so a
        squad that ended there may have a heading above it, still in the squad.
        """
        return self.ended and (not self.stray
                               or any(c.isalpha() for c in self.stray))


def _squad(own: Sequence[Sequence[str]], other: Sequence[Sequence[str]]
           ) -> tuple[tuple[RosterEntry, ...], _SquadEnd, tuple[int, ...]]:
    """``own``'s squad members down to the first heading, how it ended, and where.

    A heading is a line the other side prints too, as its own team's copy, or an
    unrated line whose name no player's could be. The squad ends at it rather than
    skipping it, because the names printed under a fill-ins heading are the people
    who are not in the squad. The last item is the index in ``own`` of each member's
    line.
    """
    entries: list[RosterEntry] = []
    rows: list[int] = []
    for k, line in enumerate(own):
        if any(_heads_both_sides(line, theirs) for theirs in other):
            return tuple(entries), _SquadEnd(ended=True), tuple(rows)
        entry = _roster_entry(line)
        if entry is None:
            continue
        if (entry.singles is None and entry.doubles is None
                and not _could_be_a_name(entry.name)):
            return tuple(entries), _SquadEnd(ended=True, stray=entry.name), tuple(rows)
        entries.append(entry)
        rows.append(k)
    return tuple(entries), _SquadEnd(ended=False), tuple(rows)


def _heads_both_sides(home: Sequence[str], away: Sequence[str]) -> bool:
    """Whether two lines are one heading printed for each team, not two people.

    They read the same except that one says ``home`` where the other says ``away``
    (:data:`_SIDE_WORD_RE`). Reading exactly the same is not enough: two players who
    share a printed name can stand on the same row of opposing squads.
    """
    left, right = (" ".join(texts).casefold() for texts in (home, away))
    return (left != right
            and _SIDE_WORD_RE.sub("side", left) == _SIDE_WORD_RE.sub("side", right))


def _could_be_a_name(name: str) -> bool:
    """Whether a roster line's name is one a person could have.

    Every word holds a letter and none holds a bracket or a colon. ``Reserves
    (Visitors)``, ``Reserves:`` and ``Reserves - Alphas`` fail; ``O'Brien``,
    ``Smith-Jones`` and ``St. John`` pass.
    """
    return all(any(c.isalpha() for c in word) and _NOT_IN_A_NAME.isdisjoint(word)
               for word in name.split())


def _quoted_by_side(taken: Sequence[tuple[str, str]]) -> str:
    """Each quoted line with the side it is printed on: ``'-' on the home side``."""
    return in_words(f"'{line}' on the {side} side" for side, line in taken)


def _taken_for_a_heading(taken: Sequence[tuple[str, str]]) -> str:
    """The one warning for every line a card's squads were ended at unasked."""
    lines = _quoted_by_side(taken)
    if len(taken) == 1:
        (side, _), = taken
        return (f"read {lines} as a heading, not a player, since no name has a "
                "bracket, a colon or a word with no letter in it, and read that "
                f"squad no further; if it is a player, the {side} squad is short on "
                "this card")
    return (f"read {lines} as headings, not players, since no name has a bracket, "
            "a colon or a word with no letter in it, and read neither squad further; "
            "if either is a player, its squad is short on this card")


def _printed_over_both(home: Sequence[RosterEntry], home_at: Sequence[float],
                       away: Sequence[RosterEntry], away_at: Sequence[float],
                       sets: Sequence[ParsedSet]) -> tuple[int, int] | None:
    """Where the two squads end at a heading printed word for word over both, if any.

    Such a heading reads exactly like two people who share a name, one in each
    squad, so it takes the rest of the card to tell them apart. Only asked of a card
    whose squads nothing else has ended but ``Comments`` or the foot of the page.
    The line is the same name in both squads, and is what such a heading is and two
    namesakes need not be: on one printed line across the card (``home_at`` and
    ``away_at`` are each member's baseline), since the heading is one printed row;
    under at least one member of either squad, since it is printed under them; in
    both squads' runs of unrated lines at their foot, since a fill-ins heading stands
    over names printed with no rating and is never rated itself; and named by neither
    side of any set row, since a heading never plays. Of several, the first, since
    what is printed under both copies of a heading is in neither squad. ``None`` when
    no line is all of that.
    """
    named = _named_by_a_set(sets)
    return next(((i, j) for i in range(_unrated_from(home), len(home))
                 for j in range(_unrated_from(away), len(away))
                 if (i or j)
                 and _printed_level(home[i], home_at[i], away[j], away_at[j])
                 and home[i].name not in named), None)


def _printed_level(entry: RosterEntry, y: float, theirs: RosterEntry,
                   their_y: float) -> bool:
    """Whether two squads' lines are one line printed the same across the card.

    The same name, on baselines *y* and *their_y* no further apart than the words of
    one printed line are.
    """
    return entry.name == theirs.name and abs(y - their_y) <= _BASELINE_TOL


def _unrated_from(squad: Sequence[RosterEntry]) -> int:
    """Where the run of unrated lines at the foot of a squad starts."""
    start = len(squad)
    while (start and squad[start - 1].singles is None
           and squad[start - 1].doubles is None):
        start -= 1
    return start


def _taken_as_printed_over_both(line: str) -> str:
    """The one warning for a card whose squads ended at a line printed over both."""
    return (f"read '{line}', printed the same on one line across both squads, as a "
            "heading, not two players who share a name, since neither copy is rated, "
            "no set row names it and nothing else ends the squads, and read neither "
            "squad further; if they are two players, both squads are short on this "
            "card")


def _named_by_a_set(sets: Sequence[ParsedSet]) -> set[str]:
    """Every name either side of any set row prints: who played, so no heading."""
    return {name for row in sets for name in (*row.home_players, *row.away_players)}


def _maybe_a_heading(squad: Sequence[RosterEntry], at: Sequence[float],
                     other: Sequence[RosterEntry], other_at: Sequence[float],
                     sets: Sequence[ParsedSet]) -> str:
    """The line of *squad* a heading printed over it alone would be, or "".

    Such a heading reads exactly like a squad member printed with no rating, so the
    line is kept as one and only quoted. It is in the run of unrated lines at the
    squad's foot, with somebody rated above it, since a heading is never rated and
    is printed under the squad; no set row names it, since a heading never plays;
    and no line of *other* level with it reads the same, since one line printed the
    same across the card is :func:`_printed_over_both`'s to judge. Of several, the
    first, since a heading holds what is under it. ``at`` and ``other_at`` are each
    member's baseline.
    """
    start = _unrated_from(squad)
    if not start:
        return ""
    named = _named_by_a_set(sets)
    return next((entry.name for entry, y in zip(squad[start:], at[start:])
                 if entry.name not in named
                 and not any(_printed_level(entry, y, theirs, their_y)
                             for theirs, their_y in zip(other, other_at))), "")


def _taken_as_a_player(taken: Sequence[tuple[str, str]]) -> str:
    """The one warning for every line kept in a squad that may be a heading."""
    lines = _quoted_by_side(taken)
    if len(taken) == 1:
        (side, _), = taken
        return (f"read {lines} as a player, though it may be a heading printed over "
                "that squad alone, since it has no rating, is printed under the "
                "squad's rated players and no set row names it; if it is a heading, "
                f"the {side} squad on this card wrongly holds it and each line under "
                "it")
    return (f"read {lines} as players, though each may be a heading printed over its "
            "squad alone, since neither has a rating, each is printed under its "
            "squad's rated players and no set row names either; if either is a "
            "heading, its squad on this card wrongly holds it and each line under it")


def _too_close_above(page: pdf.Page, band: tuple[float, float], marker: pdf.Word,
                     bounds: tuple[float, float]) -> list[str]:
    """Each name in one column printed too close above *marker* to be in *band*.

    The band ends a point above the top of the heading the block ends at, and a line
    is in it by its centre, so a line printed closer above the heading than that is
    in no band. It is under the band and above the heading's line, and names
    somebody, rated or not.
    """
    return [entry.name
            for line in _lines_in(page, (band[1], marker.baseline), bounds)
            if line[0].cy > band[1]
            and line[0].baseline < marker.baseline - _BASELINE_TOL
            and (entry := _roster_entry([w.text for w in line])) is not None]


def _left_out_too_close_above(unread: Sequence[tuple[str, str]], heading: str) -> str:
    """The one warning for every roster line printed too close above the heading."""
    lines = _quoted_by_side(unread)
    if len(unread) == 1:
        (side, _), = unread
        return (f"left {lines} out of the squads, since it is printed too close above "
                f"'{heading}' to be read as a roster line; if it is a player, the "
                f"{side} squad is short on this card")
    return (f"left {lines} out of the squads, since they are printed too close above "
            f"'{heading}' to be read as roster lines; if either is a player, its squad "
            "is short on this card")


def _roster_entry(texts: Sequence[str]) -> RosterEntry | None:
    """One roster line, e.g. ``Ada Marchetti (C) 3.82 (S) 3.97 (D)``."""
    name_parts: list[str] = []
    singles: float | None = None
    doubles: float | None = None
    captain = False
    index = 0
    while index < len(texts):
        token = texts[index]
        if _RATING_RE.match(token):
            kind = texts[index + 1] if index + 1 < len(texts) else ""
            if kind.upper() == "(S)":
                singles = float(token)
                index += 2
                continue
            if kind.upper() == "(D)":
                doubles = float(token)
                index += 2
                continue
            # A rating printed without its (S)/(D) marker: still a rating, and
            # still not part of anybody's name. Singles is printed first.
            if singles is None:
                singles = float(token)
            elif doubles is None:
                doubles = float(token)
            index += 1
            continue
        if token.upper() == "(C)":
            captain = True
            index += 1
            continue
        if singles is None and doubles is None:
            name_parts.append(token)
        index += 1
    name = " ".join(name_parts).strip()
    if not name or names_nobody(name):
        # A roster line naming nobody registers nobody. Worse than the same
        # placeholder in a set row, this one would put the phantom on a team.
        return None
    return RosterEntry(name=name, singles=singles, doubles=doubles,
                       is_captain=captain)


# --- small shared helpers -------------------------------------------------


def _census(page: pdf.Page) -> list[tuple[tuple[float, float], int]]:
    """Every rect size on the page and how many there are, commonest first."""
    counted = Counter((round(r.w, 2), round(r.h, 2)) for r in page.rects)
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))


def _role(grid: _Grid, cell: _Cell) -> str:
    for name, known in (("marker", grid.home_marker), ("names", grid.home_names),
                        ("games", grid.home_games), ("SET", grid.label),
                        ("games", grid.away_games), ("names", grid.away_names),
                        ("marker", grid.away_marker)):
        if known is not None and known == cell:
            return name
    return "unused"


def _shown(value: float | int | None) -> str:
    return "-" if value is None else f"{value:g}"
