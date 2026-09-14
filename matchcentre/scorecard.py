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
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import pdf
from .card import (Card, CardError, CrossCheck, ParsedSet, Rejection,
                   RosterEntry, Totals)
from .format import FormatError, MatchFormat, from_rows
from .model import SetStatus

__all__ = ["read", "read_page", "read_tree", "describe"]


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
_BOXES_PER_ROW = 2 * 3
_BOXES_PER_SIDE = 2
_BOXES_CENTRED = 2


def _expected_boxes(rows: int) -> int:
    """How many checkboxes a card with ``rows`` set rows prints."""
    return rows * _BOXES_PER_ROW + 2 * _BOXES_PER_SIDE + _BOXES_CENTRED


# --- printed text patterns -----------------------------------------------

# ``Division C / Yellow Ball / Round 1 / #3665611`` arrives as separate words, so
# these run against a re-joined line. Associations differ in wording -- Division,
# Grade, Section and Div all occur -- the ball colour is optional and so is the
# id, but a card with no round cannot be placed in a season, so the round stays
# mandatory.
_DIVISION_RE = re.compile(
    r"\b(?:Division|Div|Grade|Section)\.?\s+(?P<division>[^\s/,]+)",
    re.IGNORECASE)
_ROUND_RE = re.compile(r"\bRound\.?\s+(?P<round>[^\s/,]+)", re.IGNORECASE)
_MATCH_ID_RE = re.compile(r"#\s*(?P<id>\d{3,})")
_DAY_FIRST_DATE_RE = re.compile(r"\b(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})\b")
_ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_SEASON_RE = re.compile(
    r"\b(?:Season|Summer|Winter|Autumn|Spring)\b[ \w-]{0,24}", re.IGNORECASE)
_RATING_RE = re.compile(r"^\d+\.\d+$")
_CONTACT_RE = re.compile(r"@|www\.|https?:|^\+?\d[\d ()+-]{6,}$")

# A row marker of ``1&2`` is a pairing and therefore doubles; a bare ``1`` is
# singles. The separator varies between templates, so any of these joins two
# player numbers into a pair.
_PAIR_MARKER_RE = re.compile(r"^\d+(?:\s*[&+/,]\s*\d+)+$")
_SINGLE_MARKER_RE = re.compile(r"^\d+$")

# Result verbs. Real exported cards print DEFEATS and DEF. BY, the template
# documentation says DEFEATS / DRAWS WITH / LOSES TO, and clubs write both. The
# passive forms are tested first because "DEF. BY" also starts with "def".
_DRAW_MARKS = ("draw", "drew", "tie", "level", "halved")
_LOSS_MARKS = (" by", " to")
_WIN_MARKS = ("def", "beat", "won", "win", "d.")


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
        return [], [Rejection(str(target), "could not be opened", detail=str(exc))]
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
        rejections = [Rejection(str(target), reason, detail=detail, page=index)
                      for index, reason, detail in failures]
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


def read_tree(root: str | Path, *,
              expect: MatchFormat | None = None
              ) -> tuple[list[Card], list[Rejection]]:
    """Every ``*.pdf`` under ``root``, sorted by path so a run is reproducible.

    The extension is matched case-insensitively: ``rglob("*.pdf")`` would not see
    a card saved as ``.PDF`` by a Windows share, and a card nobody sees is worse
    than a card that fails, because the ladder still renders and is quietly a
    fixture short.
    """
    base = Path(root)
    if not base.is_dir():
        return [], [Rejection(str(base), "no such directory to read cards from")]
    try:
        paths = sorted((p for p in base.rglob("*")
                        if p.is_file() and p.suffix.lower() == ".pdf"), key=str)
    except OSError as exc:
        return [], [Rejection(str(base), "the directory could not be listed",
                              detail=str(exc))]
    cards: list[Card] = []
    rejections: list[Rejection] = []
    for path in paths:
        found, refused = read(path, expect=expect)
        cards.extend(found)
        rejections.extend(refused)
    return cards, rejections


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
    boxes = _read_boxes(page, side) if side is not None else []
    if not boxes:
        # Every tick is geometry, not text: the labels print whether or not they
        # are ticked. A card whose boxes were not located would read a forfeit as
        # a played set with an odd score, so it is refused rather than scored.
        raise CardError(
            "no checkboxes could be located, so a forfeit, retirement or "
            "cancellation on this card would be invisible",
            detail=f"expected {_expected_boxes(len(grid.bands))} boxes for "
                   f"{len(grid.bands)} set rows; "
                   + (f"the commonest hairline width on the page is "
                      f"{side:.2f}pt, and no pair of edges that far apart forms "
                      "a square" if side is not None
                      else "the page paints no hairline that could be the top "
                           "edge of a box"))
    want_boxes = _expected_boxes(len(grid.bands))
    if len(boxes) != want_boxes:
        # Partial loss is the quiet failure: the boxes that were found still read
        # correctly, so nothing else on the card looks wrong. A warning rather
        # than a refusal, because the scores are still legible.
        warnings.append(
            f"located {len(boxes)} checkboxes, expected {want_boxes} for "
            f"{len(grid.bands)} set rows; a tick on a missing box was not seen")

    sets = tuple(_read_row(page, grid, index, band, boxes, warnings)
                 for index, band in enumerate(grid.bands))
    if expect is not None:
        _check_expected_format(sets, expect)

    totals = _read_totals(page, grid, warnings)
    winner_tick = _sided_flag("Match Winner", boxes, grid, warnings)
    forfeited_by = _sided_flag("Match Forfeited", boxes, grid, warnings)
    home_roster, away_roster = _read_rosters(page, grid, warnings)
    checks = (_totals_check(sets, totals),
              _verb_check(sets, banner.verb),
              _winner_tick_check(sets, winner_tick, boxes, grid))
    if not any(check.available for check in checks):
        warnings.append(
            "the card prints no TOTAL row, no result verb and no Match Winner "
            "tick, so nothing on it confirms the set rows were read correctly")

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
    )


def describe(page: pdf.Page) -> str:
    """A structural summary of one page, whether or not it parses.

    This is what a club stares at when a card will not read, so it reports what
    was *found* rather than what was expected, stage by stage, and keeps going
    after a stage fails.
    """
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
        lines.append(f"grid: NOT RESOLVED - {exc.reason}"
                     + (f" ({exc.detail})" if exc.detail else ""))
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
    boxes = _read_boxes(page, side) if side is not None else []
    if grid is not None:
        lines.append(f"checkboxes: {len(boxes)} located, "
                     f"{_expected_boxes(len(grid.bands))} expected for "
                     f"{len(grid.bands)} rows")
    else:
        lines.append(f"checkboxes: {len(boxes)} located")
    centred = {f"Match {label}" for label in ("Cancelled", "Disputed")}
    for box in boxes:
        row = ""
        if grid is not None:
            row = next((f" row {i + 1}" for i, (top, bottom)
                        in enumerate(grid.bands) if top <= box.rect.cy <= bottom),
                       "")
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
        return "\n".join(lines)

    for stage, reader in (("header", lambda: _read_header(page, grid, warnings)),
                          ("banner", lambda: _read_banner(page, grid, warnings))):
        try:
            found = reader()
        except CardError as exc:
            lines.append(f"{stage}: NOT READ - {exc.reason}"
                         + (f" ({exc.detail})" if exc.detail else ""))
            continue
        if isinstance(found, _Header):
            lines.append(
                f"header: division {found.division!r} round {found.round!r} "
                f"match id {found.match_id!r} date {found.date or '-'} "
                f"({found.weekday or '-'}) court "
                f"{found.court if found.court is not None else '-'} "
                f"venue {found.venue!r}")
            lines.append(f"  club hint {found.club_hint!r}, "
                         f"season hint {found.season_hint!r}")
        else:
            lines.append(f"banner: home {found.home!r} away {found.away!r} "
                         f"verb {found.verb!r}")

    sets = tuple(_read_row(page, grid, index, band, boxes, warnings)
                 for index, band in enumerate(grid.bands))
    lines.append(f"set rows ({len(sets)}):")
    for parsed in sets:
        lines.append(
            f"  row {parsed.index + 1} marker {parsed.marker!r} "
            f"{'doubles' if parsed.doubles else 'singles'} "
            f"{parsed.home_games}-{parsed.away_games} {parsed.status.value} "
            f"home {list(parsed.home_players)} away {list(parsed.away_players)}")
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
    for check in (_totals_check(sets, totals), _verb_check(sets, verb),
                  _winner_tick_check(sets, winner, boxes, grid)):
        state = ("not printed" if not check.available
                 else "agrees" if check.agrees else "DISAGREES")
        lines.append(f"cross-check {check.name}: {state}"
                     + (f" - {check.detail}" if check.detail else ""))
    home_roster, away_roster = _read_rosters(page, grid, warnings)
    for label, roster in (("home", home_roster), ("away", away_roster)):
        lines.append(f"{label} roster: " + (", ".join(
            f"{e.name}{' (C)' if e.is_captain else ''} "
            f"[{_shown(e.singles)}/{_shown(e.doubles)}]" for e in roster)
            or "none read"))
    lines.append("warnings: " + (f"{len(warnings)}" if warnings else "none"))
    for warning in warnings:
        lines.append(f"  {warning}")
    return "\n".join(lines)


# --- the grid ------------------------------------------------------------


def _rule_cluster(page: pdf.Page) -> list[pdf.Rect]:
    """The set table's own vertical rules, told apart from every other hairline.

    A card paints thin vertical rects for three different things: the table's
    cell borders, the sides of every checkbox, and the boxes of the signature
    block. Rather than a hardcoded height window, they are separated by clustering
    on height and keeping the cluster with the most rule *ink* -- the set table is
    always the largest run of vertical line on the page, and that holds for a
    one-row card and a scaled rendering alike.
    """
    thin = [r for r in page.rects
            if r.filled and r.w < _RULE_MAX_WIDTH and r.h >= _RULE_MIN_HEIGHT]
    if not thin:
        return []
    groups: list[list[pdf.Rect]] = []
    for rect in sorted(thin, key=lambda r: r.h):
        tolerance = max(_RULE_HEIGHT_TOL, rect.h * _RULE_HEIGHT_SPREAD)
        if groups and rect.h - groups[-1][-1].h <= tolerance:
            groups[-1].append(rect)
        else:
            groups.append([rect])
    return max(groups, key=lambda group: sum(r.h for r in group))


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
    if not rules:
        raise CardError(
            "the page paints no table rules, so it has no set table and is not "
            "a scorecard",
            detail=f"looked for filled rects under {_RULE_MAX_WIDTH}pt wide and "
                   f"at least {_RULE_MIN_HEIGHT}pt tall among "
                   f"{len(page.rects)} rects")
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


def _read_header(page: pdf.Page, grid: _Grid, warnings: list[str]) -> _Header:
    """Division, round, match id, date, court and the two config-stub hints.

    Read from inside the card and nowhere else. A filename and a directory name
    are labels a human typed; a scorecard is a document.
    """
    lines = [line for line in page.lines() if line[0].cy < grid.top]
    joined = [(" ".join(w.text for w in line), line) for line in lines]

    division = round_number = match_id = ""
    for text, _ in joined:
        found_division = _DIVISION_RE.search(text)
        found_round = _ROUND_RE.search(text)
        if found_division and found_round:
            division = found_division.group("division").strip(".,;:")
            round_number = found_round.group("round").strip(".,;:")
            found_id = _MATCH_ID_RE.search(text)
            match_id = found_id.group("id") if found_id else ""
            break
    if not (division and round_number):
        # Some associations split the identity fields over two printed lines.
        # Both values still come off the card, so this is allowed, but it is
        # reported: the risk is pairing a division with the word "Round" from
        # somewhere else entirely.
        division_line = next((t for t, _ in joined if _DIVISION_RE.search(t)), "")
        round_line = next((t for t, _ in joined if _ROUND_RE.search(t)), "")
        if division_line and round_line:
            division = _DIVISION_RE.search(division_line).group(
                "division").strip(".,;:")
            round_number = _ROUND_RE.search(round_line).group("round").strip(".,;:")
            found_id = (_MATCH_ID_RE.search(round_line)
                        or _MATCH_ID_RE.search(division_line))
            match_id = found_id.group("id") if found_id else ""
            warnings.append(
                f"the division and the round are printed on different lines "
                f"({division_line[:40]!r} and {round_line[:40]!r})")
        else:
            raise CardError(
                "no line above the set table names both a division and a round, "
                "so this page cannot be placed in a competition",
                detail=_header_hint(joined))

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
    at the wrong thing. Quoting the closest line makes it actionable.
    """
    for text, _ in joined:
        stripped = text.strip()
        if stripped.count("/") >= 2 and _MATCH_ID_RE.search(stripped):
            return f'closest line: "{stripped[:80]}"'
    for text, _ in joined:
        if _DIVISION_RE.search(text) or _ROUND_RE.search(text):
            return f'closest line: "{text.strip()[:80]}"'
    return "no line above the table mentions a division, a grade or a round"


def _read_date(joined: Sequence[tuple[str, list[pdf.Word]]],
               warnings: list[str]) -> tuple[str, str]:
    """The card's own date, as ISO, plus the weekday it implies.

    The weekday has no other source: a card in ``data/MondayResults/`` is filed
    under a label a human typed, and a competition keyed on that label instead of
    on the date puts a Tuesday fixture in a Monday ladder.
    """
    for text, _ in joined:
        iso = _ISO_DATE_RE.search(text)
        if iso:
            found_date = _date_or_none(int(iso.group("y")), int(iso.group("m")),
                                       int(iso.group("d")))
            if found_date is not None:
                return found_date.isoformat(), found_date.strftime("%A")
        found = _DAY_FIRST_DATE_RE.search(text)
        if not found:
            continue
        a, b = int(found.group("a")), int(found.group("b"))
        year = int(found.group("y"))
        # Day-first is the convention on these cards, so it is tried first; a
        # date impossible read that way was written month-first, and saying so
        # beats discarding the date.
        found_date = _date_or_none(year, b, a)
        if found_date is None:
            found_date = _date_or_none(year, a, b)
            if found_date is not None:
                warnings.append(
                    f"date {found.group(0)!r} is not a valid day/month/year, so "
                    "it was read as month/day/year")
        if found_date is not None:
            return found_date.isoformat(), found_date.strftime("%A")
        warnings.append(
            f"the date printed on this card, {found.group(0)!r}, is not a real "
            "date either way round, so the weekday and the season are unknown")
        return "", ""
    warnings.append("no date printed above the set table, so the weekday and "
                    "the season of this card are unknown")
    return "", ""


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
    """
    label = next((w for w in page.words
                  if w.text.strip().rstrip(":").casefold() == "court"), None)
    if label is None:
        warnings.append("no 'Court' label on the page, so the court is unknown")
        return None, ""

    column = label.x0 - _CELL_INSET
    candidates: list[list[pdf.Word]] = []
    same = [w for w in page.words
            if abs(w.baseline - label.baseline) <= _BASELINE_TOL
            and w.x0 >= label.x1]
    if same:
        candidates.append(sorted(same, key=lambda w: w.x0))
    for line in page.lines():
        words = sorted((w for w in line if w.x0 >= column), key=lambda w: w.x0)
        if not words:
            continue
        above = label.baseline - words[0].baseline
        if 0 < above <= _COURT_REACH_ABOVE or -_COURT_REACH_BELOW <= above < 0:
            candidates.append(words)
    for words in candidates:
        texts = [w.text for w in words]
        if any(_DAY_FIRST_DATE_RE.search(t) or ":" in t for t in texts):
            continue        # this is the date and time line, not the court
        digits = texts[-1].strip(".,;:")
        if not digits.isdigit():
            continue
        return int(digits), " ".join(texts[:-1]).strip()
    warnings.append(
        f"the 'Court' label at x {label.x0:.1f} has no number printed with it, "
        "so the court is unknown")
    return None, ""


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
                     if r.filled and r.h < _HAIRLINE_MAX_HEIGHT
                     and _BOX_MIN_SIDE <= r.w <= _BOX_MAX_SIDE)
    if not widths:
        return None
    # Ties break towards the narrower width so the answer does not depend on
    # dict order, and therefore on the order the content stream was written.
    return max(widths.items(), key=lambda item: (item[1], -item[0]))[0]


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


def _read_boxes(page: pdf.Page, side: float) -> list[_Box]:
    """Every checkbox on the card, each named by the label that owns it.

    Labels drive the search rather than positions, because which side of its
    label a box sits on varies -- the away column's ``Match Winner`` box is to the
    right of its text and every other box is to the left. Anchoring on the label
    means this reader never has to encode that asymmetry.
    """
    outlines, fills = _box_geometry(page, side)
    used: set[tuple[float, float]] = set()
    found: list[_Box] = []
    for label, anchor in _box_labels(page):
        best: pdf.Rect | None = None
        best_gap = side * _BOX_LABEL_GAP
        for rect in outlines:
            key = (round(rect.x0, 1), round(rect.y0, 1))
            if key in used or abs(rect.cy - anchor.cy) > _BOX_ROW_TOL:
                continue
            gap = min(abs(rect.x0 - anchor.x1), abs(anchor.x0 - rect.x1))
            if gap < best_gap:
                best, best_gap = rect, gap
        if best is None:
            continue
        used.add((round(best.x0, 1), round(best.y0, 1)))
        found.append(_Box(label=label, rect=best,
                          ticked=_is_ticked(best, side, fills)))
    return found


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


def _ticked(boxes: Iterable[_Box], label: str, *,
            band: tuple[float, float] | None = None,
            side: str | None = None, grid: _Grid | None = None) -> bool:
    """Is ``label``'s box ticked, optionally within one row and on one side?"""
    for box in boxes:
        if box.label != label or not box.ticked:
            continue
        if band is not None and not band[0] <= box.rect.cy <= band[1]:
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
    """
    present = [box for box in boxes if box.label == label]
    ticked = {grid.side_of(box.rect.cx) for box in present if box.ticked}
    if len(present) < 2:
        warnings.append(
            f"only {len(present)} '{label}' box(es) were located, so this card's "
            f"{label.lower()} state may be incomplete")
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
    home_players = _players(page, band, grid.home_names, boxes)
    away_players = _players(page, band, grid.away_names, boxes)

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
        for word in page.words_in_band(band[0], band[1], *cell.inner):
            text = word.text.strip()
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
    zero is wrong.
    """
    digits = [w.text for w in page.words_in_band(band[0], band[1], *cell.inner)
              if w.text.isdigit()]
    if len(digits) == 1:
        return int(digits[0])
    if not digits:
        warnings.append(
            f"row {index + 1}: no {side} games figure printed, read as 0")
        return 0
    warnings.append(
        f"row {index + 1}: {len(digits)} numbers in the {side} games column "
        f"({', '.join(digits)}); used the first")
    return int(digits[0])


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
    """
    total = next((w for w in page.words
                  if w.text.strip().rstrip(":").casefold() in ("total", "totals")
                  and w.cy > grid.bottom), None)
    if total is None:
        return Totals()
    headings = [w for w in page.words
                if w.text.strip().casefold() in ("sets", "games")
                and 0 < total.cy - w.cy < _TOTALS_REACH]
    figures = [w for w in page.words
               if w.text.isdigit() and 0 < w.cy - total.cy < _TOTALS_REACH
               and grid.rules[0] <= w.cx <= grid.rules[-1]]
    if not headings:
        warnings.append(
            "the card prints a TOTAL row with no Sets or Games headings above "
            "it, so its own totals were not read")
        return Totals()

    values: dict[tuple[str, str], int] = {}
    for figure in figures:
        heading = min(headings, key=lambda w: abs(w.cx - figure.cx))
        if abs(heading.cx - figure.cx) > _TOTALS_COLUMN_TOL:
            continue
        key = (grid.side_of(figure.cx), heading.text.strip().casefold())
        values.setdefault(key, int(figure.text))
    totals = Totals(home_sets=values.get(("home", "sets")),
                    home_games=values.get(("home", "games")),
                    away_games=values.get(("away", "games")),
                    away_sets=values.get(("away", "sets")))
    if not totals.present:
        warnings.append(
            "the card's TOTAL row has no figures under its headings, so its own "
            "totals were not read")
    elif None in (totals.home_games, totals.away_games):
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
    """
    home = away = 0
    for parsed in sets:
        if parsed.home_forfeit != parsed.away_forfeit:
            away += parsed.home_forfeit
            home += parsed.away_forfeit
        elif parsed.home_games > parsed.away_games:
            home += 1
        elif parsed.away_games > parsed.home_games:
            away += 1
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


def _reading_text(sets: Sequence[ParsedSet]) -> str:
    by_games, by_sets = _readings(sets)
    home_games = sum(s.home_games for s in sets)
    away_games = sum(s.away_games for s in sets)
    home_sets, away_sets = _row_claims(sets)
    return (f"the set rows give games {home_games}-{away_games} "
            f"({by_games or 'level'}) and at most {home_sets}-{away_sets} sets "
            f"({by_sets or 'level'})")


def _totals_check(sets: Sequence[ParsedSet], totals: Totals) -> CrossCheck:
    """The printed ``TOTAL`` row against the sum of the set rows."""
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
    if problems:
        return CrossCheck("totals", True, agrees=False,
                          detail="; ".join(problems))
    return CrossCheck(
        "totals", True,
        detail=f"printed games {_shown(totals.home_games)}-"
               f"{_shown(totals.away_games)} and sets "
               f"{_shown(totals.home_sets)}-{_shown(totals.away_sets)} match the "
               f"{len(sets)} set rows")


def _verb_check(sets: Sequence[ParsedSet], verb: str) -> CrossCheck:
    """The result verb printed between the team names against the set rows."""
    claim = _verb_claim(verb)
    if claim is None:
        return CrossCheck(
            "verb", False,
            detail=f"the verb {verb!r} is not one this reader recognises"
                   if verb else "the card prints no result verb")
    if _supported(claim, sets):
        return CrossCheck("verb", True,
                          detail=f"{verb!r} agrees with the set rows")
    said = ("the match a draw" if claim == "draw"
            else f"{claim} the winner")
    return CrossCheck("verb", True, agrees=False,
                      detail=f"the card prints {verb!r}, making {said}, but "
                             f"{_reading_text(sets)}")


def _verb_claim(verb: str) -> str | None:
    """``DEFEATS`` -> home, ``DEF. BY`` -> away, ``DRAWS WITH`` -> draw.

    Real exported cards print ``DEFEATS`` and ``DEF. BY``; the template
    documentation says ``LOSES TO`` for the second. The passive forms are tested
    before the active ones because ``DEF. BY`` also begins with ``def``.
    """
    text = " ".join(verb.split()).casefold()
    if not text:
        return None
    if any(mark in text for mark in _DRAW_MARKS):
        return "draw"
    if any(mark in text for mark in _LOSS_MARKS):
        return "away"
    if any(text.startswith(mark) for mark in _WIN_MARKS):
        return "home"
    return None


def _winner_tick_check(sets: Sequence[ParsedSet], winner: str | None,
                       boxes: Sequence[_Box], grid: _Grid) -> CrossCheck:
    """The ``Match Winner`` checkbox against the set rows."""
    present = [box for box in boxes if box.label == "Match Winner"]
    ticked = [box for box in present if box.ticked]
    if len(present) < 2:
        return CrossCheck("winner-tick", False,
                          detail=f"only {len(present)} Match Winner box(es) "
                                 "were located")
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
    return CrossCheck("winner-tick", True, agrees=False,
                      detail=f"the card ticks {winner} as Match Winner, but "
                             f"{_reading_text(sets)}")


# --- roster block --------------------------------------------------------


def _read_rosters(page: pdf.Page, grid: _Grid, warnings: list[str]
                  ) -> tuple[tuple[RosterEntry, ...], tuple[RosterEntry, ...]]:
    """The two squads printed at the foot of the card, as printed.

    Both the properly-cased name and the ``(C)`` captain mark are kept. The
    predecessor slugged the name and dropped the mark, after which it had to
    demand captains from configuration and could not render a name with internal
    capitals. Ratings are per card, so a mid-season re-rating is captured as at
    that round, and a player with no rating still appears on the roster.
    """
    header = next((w for w in page.words
                   if w.text.strip().casefold() in ("players", "player")
                   and w.cy > grid.bottom), None)
    if header is None:
        warnings.append(
            "no 'Players' heading below the set table, so neither team's "
            "registered squad was read from this card")
        return (), ()
    # Bound the block by the *top* of the next heading, not its baseline: rows
    # are selected on their vertical centre, which sits above the baseline, so a
    # baseline bound lets the heading itself in as a roster line.
    end = min((w.y0 for w in page.words
               if w.text.strip().casefold().startswith(("fill-in", "comment"))
               and w.y0 > header.y0), default=page.height)
    band = (header.y1 + _CELL_INSET, end - _CELL_INSET)
    sides: list[tuple[RosterEntry, ...]] = []
    for bounds in ((float("-inf"), grid.divider), (grid.divider, float("inf"))):
        entries: list[RosterEntry] = []
        for line in _lines_in(page, band, bounds):
            entry = _roster_entry([w.text for w in line])
            if entry is not None:
                entries.append(entry)
        sides.append(tuple(entries))
    if not sides[0] or not sides[1]:
        warnings.append(
            f"read {len(sides[0])} home and {len(sides[1])} away roster "
            "entries, so one side's registered squad is missing from this card")
    return sides[0], sides[1]


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
    if not name:
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
