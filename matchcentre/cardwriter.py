"""Write scorecard PDFs, so the project can be exercised without real cards.

Two things need scorecards that no club has handed us. A test suite: the
predecessor's pinned six real PDFs of six real people, which cannot ship, cannot
be regenerated, and cover exactly one club's format. And a fresh clone with an
empty ``data/``, which has nothing to build a dashboard from and so cannot show a
new user what the tool does. Both are solved by writing cards instead of
collecting them — ``tools/demo.py`` for the season, ``tests/`` for the fixtures.

Standard library only, like everything else here: the PDF is assembled by hand,
including the ``/ToUnicode`` CMap. That CMap is not optional decoration. Without
it the reader decodes every glyph to nothing, the page comes back with no text at
all, and the card looks blank rather than broken.

**What a golden built from this module does and does not prove.** The geometry
below was measured off a real exported card, rectangle by rectangle. But the reader consumes the same structure this writer emits, so a
test that writes a card, reads it back and compares the numbers proves
*round-tripping* — that the two agree — and says nothing about whether either
matches Match Centre's actual template. Only a real card can show that, and that
is a one-off comparison done by hand, not something this suite can repeat.

**Why the deliberate defects exist.** A suite built only from cards this module
writes correctly would confirm itself: the reader would be tested against the one
input shape it already handles. The switches at the end of :class:`CardSpec` —
``page_offset``, ``drop_column_rule``, ``extra_tick``, ``omit_totals``,
``omit_verb``, ``omit_to_unicode`` — each produce a structurally plausible PDF
that is nonetheless wrong, so the suite can assert the reader *refuses* it (F5,
F6) rather than guessing. They are the part of this module that has teeth.

Nothing here is club-specific except the strings in :class:`CardSpec`'s defaults,
and none of those name a real person or a real club.
"""

from __future__ import annotations

import datetime
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .format import DEFAULT_SET_RULE, SetRule, set_rule as named_set_rule, set_winner
from .model import SetStatus, forfeit_set_winner, other_side, stopped_short

__all__ = ["CardSpec", "SetRow", "RosterRow", "write_card", "write_card_file",
           "MAX_SET_ROWS", "AUTO", "NO_TICK"]


# --- page -------------------------------------------------------------------
# Every measurement in this module is in the reader's coordinate space: origin at
# the top-left corner, y growing downward. The single conversion to PDF's
# bottom-left space happens in :meth:`_Painter.rect` and :meth:`_Painter.text`.

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0          # A4 portrait, as the real cards are

TEXT_SIZE_BODY = 7.49
TEXT_SIZE_SMALL = 6.34
TEXT_SIZE_BANNER = 10.37
TEXT_SIZE_ROW_NUMBER = 8.64

# --- letterhead and header --------------------------------------------------

LETTERHEAD_X = 80.95
LETTERHEAD_FIRST_BASELINE = 28.73
LETTERHEAD_LINE_PITCH = 10.37
CONTACT_X = 92.18
PHONE_BASELINE = 71.95
EMAIL_BASELINE = 84.05
DATE_TIME_X = 466.98
DATE_TIME_BASELINE = 28.73
VENUE_X = 526.93
VENUE_BASELINE = 39.10
COURT_LABEL_X = 471.42
COURT_LABEL_BASELINE = 59.85
# The write-on line after "Court:". The real card leaves the court to be filled in
# by hand here and prints it in the venue line above; the reader takes it from the
# venue line, and this rule is the reminder that the labelled space is empty.
COURT_RULE = (496.67, 63.88, 72.04)      # x, y, width
COMPETITION_BASELINE = 57.54
SEASON_BASELINE = 67.91
IDENTITY_BASELINE = 78.29

# --- banner -----------------------------------------------------------------

BANNER_HOME_X = 60.78
BANNER_AWAY_RIGHT_X = 568.10      # the away name is right-aligned to this edge
BANNER_BASELINE = 104.22
VERB_BASELINE = 103.07

# --- set table --------------------------------------------------------------
# The eight vertical rules the template paints. They are the grid: the reader
# derives its cell boundaries from these rects rather than from constants, which
# is the whole of F5, so this writer must paint every one of them.

COLUMN_RULES = (26.4, 61.6, 250.6, 281.1, 313.4, 344.0, 533.0, 568.1)

# Which of those rules the template insets by one hairline from the top of the
# band, measured off a real card: the four rules bounding the three score cells in
# the middle are drawn 46.10pt tall starting 0.58 below the band top, and the four
# at the ends are drawn 47.26pt tall flush with it. Every rule is painted once.
#
# The distinction is worth carrying because it is the shape of the real page: the
# middle cells sit inside a band whose own top rule is already there, the end ones
# do not. A writer that painted both heights at every rule would put 32 rects on
# the page that no club's card has, and a reader that came to depend on the count
# or on the flush start would pass its tests and fail on a real card.
INSET_COLUMN_RULES = (2, 3, 4, 5)

# Cell indices between consecutive rules, left to right.
CELL_HOME_MARKER = 0
CELL_HOME_NAMES = 1
CELL_HOME_GAMES = 2
CELL_SET_LABEL = 3
CELL_AWAY_GAMES = 4
CELL_AWAY_NAMES = 5
CELL_AWAY_MARKER = 6

TABLE_TOP = 126.7
ROW_HEIGHT = 47.26
HAIRLINE = 0.58              # the width of a rule and of a checkbox edge
CELL_TEXT_INSET = 2.0        # keeps centred text off the rule it would overrun

GAMES_HEADER_BASELINE = 116.32
HOME_GAMES_HEADER_X = 253.34
SET_HEADER_X = 290.54
AWAY_GAMES_HEADER_X = 319.64

# The band above the table carrying the "Games / SET / Games" headings, painted as
# three tinted cells -- a narrow one at each end, one wide one between them --
# with its bottom edge on the table top, so it moves with the table.
#
# Measured off a real card, colour included, because these are large filled rects
# and the reader has to know they are not part of the grid. A fixture without them
# cannot show that it does.
HEADER_BAND_HEIGHT = 39.76
HEADER_BAND_CELLS = ((26.42, 34.58), (61.00, 473.13), (534.13, 34.58))
HEADER_BAND_TINT = (0.678431372, 0.847058823, 0.901960784)      # a pale blue

# A band's bottom edge, painted as three abutting segments and not as one line:
# the end cell at each side is stroked separately from the span between them.
# ``(x, width)`` each, measured; together they tile the same width a single line
# would cover, so the card looks no different and the page carries three rects
# where a naive writer puts one.
#
# There is no separator on the *top* edge of the table. The real card has none --
# the tinted heading band above it is the boundary -- and a fixture that painted
# one would let the reader come to rely on a line no club's card has.
BAND_SEPARATOR_SEGMENTS = ((26.42, 35.15), (61.58, 471.98), (533.56, 35.15))

# The white panel the real producer lays down under everything, inset from the
# page edge. Nothing is drawn on top of it that would not be there anyway, so it
# is invisible -- but it is the largest rect on the page by a wide margin, and a
# card that lacks it cannot demonstrate that the reader ignores it.
CARD_PANEL = (10.29, 10.29, 574.56, 602.22)      # x, y, width, height
WHITE = (1.0, 1.0, 1.0)

# Offsets down from the top of a row band. Laying the row out this way is what
# lets the table have any number of rows.
ROW_NUMBER_DY = 20.74            # "R1" in the marker column
ROW_MARKER_DY = 31.69            # "1&2" or "1" below it: the discipline marker
ROW_FIRST_PLAYER_DY = 13.83
ROW_PLAYER_PITCH = 14.98         # a doubles row prints two names this far apart
ROW_GAMES_DY = 25.93
ROW_STATUS_LABEL_DY = 43.79

# The template prints this literal string in the SET column of every row; it is
# not a set number. Measured on a real four-row card, where all four read "S1".
SET_CELL_TEXT = "S1"

# --- checkboxes -------------------------------------------------------------
# There is no glyph for a tick. An empty box is four hairlines forming a square
# outline; a ticked box is that outline plus a solid fill over it. The reader
# reassembles the outline from its edges and looks for the fill, so a text-only
# extraction cannot lose a forfeit — and this writer has to paint both parts.

BOX_SIDE = 8.07
BOX_LABEL_RISE = 6.35        # the box's top edge is this far above its label baseline
SET_STATUS_LABELS = ("Forfeit", "Retired", "Incomplete")
HOME_STATUS_LABEL_X = (78.65, 115.84, 155.61)
AWAY_STATUS_LABEL_X = (415.04, 452.23, 492.00)
# Each status box, measured, rather than derived as "so far left of its label".
# One offset put four of the six boxes a third of a point out, because the real
# template does not space them uniformly. Six numbers off a real card cost nothing
# and describe the page instead of a rule the page does not follow.
HOME_STATUS_BOX_X = (67.91, 104.80, 144.56)
AWAY_STATUS_BOX_X = (403.89, 441.35, 481.11)

# --- everything below the table ---------------------------------------------
# Offsets down from the *bottom of the last row band*, never absolute y. A card
# with six sets has a table two rows taller, and a prototype that positioned this
# block at fixed y had its TOTAL row painted straight through rows five and six.

TOTALS_HEADING_DY = 10.97
TOTAL_LABEL_DY = 18.46
TOTAL_VALUE_DY = 24.22
MATCH_WINNER_DY = 19.04
MATCH_FORFEITED_DY = 32.29
MATCH_CANCELLED_DY = 42.66
MATCH_DISPUTED_DY = 55.92
SIGNATURE_HEADING_DY = 96.26
SIGNED_BY_DY = 106.63
ROSTER_HEADING_DY = 146.97
ROSTER_FIRST_ROW_DY = 160.80
ROSTER_ROW_PITCH = 10.375
FILL_INS_DY = 205.91
COMMENTS_DY = 253.59

TOTALS_HEADING_X = (204.70, 247.16, 325.84, 376.74)
TOTAL_VALUE_CX = (211.5, 258.2, 336.9, 383.6)

# The grid around the totals row: two stacked cell rows -- the "Sets Games" /
# "Games Sets" headings, and the numbers under them -- with the "TOTAL" label in a
# single tall cell between the two sides. Boxed on a real card, and boxed here even
# when the numbers are omitted, because a blank card is a form with empty boxes and
# not a card with no boxes.
TOTALS_GRID_TOP_DY = 2.86
TOTALS_GRID_MIDDLE_DY = 14.39
TOTALS_GRID_ROW_HEIGHTS = (11.53, 14.98)          # heading row, then value row
TOTALS_GRID_CELLS = ((187.78, 47.26), (235.04, 46.10),
                     (313.99, 46.68), (360.67, 46.68))
TOTALS_GRID_EDGES = (187.78, 234.46, 360.09, 406.77)
TOTAL_LABEL_CELL = (281.14, 32.85)                # x, width; spans both rows
TOTAL_LABEL_CELL_EDGES = (281.14, 313.41)
TOTAL_LABEL_CELL_HEIGHT = 26.51

# Write-on lines under the foot of the card: two captains sign it, and the roster
# heading is underscored on each side. Ruled, never filled.
SIGNATURE_RULE_DY = 108.90
SIGNATURE_RULES = ((85.20, 148.11), (414.84, 153.87))
ROSTER_RULE_DY = 149.81
ROSTER_RULES = ((26.42, 141.19), (167.61, 64.54),
                (357.79, 145.22), (503.01, 65.12))
HOME_FLAG_LABEL_X = 40.61
HOME_FLAG_BOX_X = 26.42
AWAY_WINNER_LABEL_X = 502.67
AWAY_FORFEITED_LABEL_X = 495.76   # the longer label starts further left
AWAY_FLAG_BOX_X = 560.64          # the only box that sits right of its label
CENTRE_FLAG_LABEL_X = 264.42
CENTRE_FLAG_BOX_X = 250.02
HOME_SIGNATURE_X = 26.21
AWAY_SIGNATURE_X = 359.54
HOME_ROSTER_NAME_X = 26.21
HOME_ROSTER_RATING_X = 167.41
HOME_RATING_HEADING_X = 187.76
AWAY_ROSTER_NAME_X = 357.61
AWAY_ROSTER_RATING_X = 503.15
AWAY_RATING_HEADING_X = 523.51
HOME_FILL_INS_X = 67.22
AWAY_FILL_INS_X = 429.86

# How many rows fit before the roster and comments block would run off the page.
# Computed rather than asserted, so changing ROW_HEIGHT cannot make it a lie.
MAX_SET_ROWS = int((PAGE_HEIGHT - TABLE_TOP - COMMENTS_DY) // ROW_HEIGHT)

# Sentinels for the two fields that have three states: derive it from the rows,
# print nothing, or print exactly what the caller said.
AUTO = "auto"
NO_TICK = "none"


# --- glyph metrics ----------------------------------------------------------
# Coarse Helvetica-ish width classes. The absolute values do not matter; what
# matters is that the same function feeds both the layout here and the /Widths
# array in the font object, because the reader derives every word's x1 from those
# widths. A writer whose declared widths disagreed with its own layout would
# produce words that overlap the wrong cell.

_NARROW_GLYPHS = frozenset("iljtfr.,;:'!|()[] ")


def glyph_width_em(char: str) -> float:
    """Width of one glyph as a fraction of the em."""
    if char == " ":
        return 0.278
    if char in _NARROW_GLYPHS:
        return 0.30
    if char.isupper():
        return 0.667
    return 0.556


def text_width(text: str, size: float) -> float:
    return sum(glyph_width_em(char) for char in text) * size


def _printable(text: str) -> str:
    """Fold text into the 0x20..0x7E range the embedded CMap covers.

    Accents are decomposed and dropped rather than replaced wholesale, so
    ``Jose`` survives where a blind latin-1 encode would have emitted a byte the
    CMap does not map and the reader would have decoded to nothing.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c if " " <= c <= "~" else "?" for c in stripped)


# The CMap that makes the text layer readable: a one-byte codespace over the
# printable ASCII range, mapping each code to the same Unicode point.
_TO_UNICODE_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CMapName /MatchcentreSynthetic def
/CMapType 2 def
1 begincodespacerange
<20> <7E>
endcodespacerange
1 beginbfrange
<20> <7E> <0020>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""


# --- the spec ---------------------------------------------------------------


@dataclass
class SetRow:
    """One set row of the card.

    ``doubles`` is the row's discipline, and it is written into the marker column
    as ``1&2`` or a bare ``1`` because that column is where the reader gets the
    match format from. ``marker`` overrides the derived text, for fixtures that
    need a marker the row contradicts.

    ``status_side`` names the side whose box carries a non-played status: the
    forfeiting side for :data:`SetStatus.FORFEIT`, and for a retirement the side
    that retired. ``"both"`` ticks both, which is what a card called off mid-match
    normally shows.
    """

    home_games: int = 0
    away_games: int = 0
    doubles: bool = True
    home_players: tuple[str, ...] = ()
    away_players: tuple[str, ...] = ()
    marker: str = ""
    status: SetStatus = SetStatus.PLAYED
    status_side: str = "home"


@dataclass
class RosterRow:
    """One line of a team's roster block, as printed at the foot of the card."""

    name: str = ""
    singles: float | None = None
    doubles: float | None = None
    captain: bool = False


def _default_rows() -> tuple[SetRow, ...]:
    """A four-row doubles-singles-singles-doubles card, the shape most clubs run.

    The names are obvious placeholders. Every fixture and every demo card
    overrides them; they exist so ``write_card(CardSpec())`` is a complete,
    readable card rather than an empty table.
    """
    first, second = "Alpha Ashford", "Bravo Beeching"
    third, fourth = "Delta Dunmore", "Echo Elmsworth"
    pair_home, pair_away = (first, second), (third, fourth)
    return (
        SetRow(6, 2, True, pair_home, pair_away),
        SetRow(6, 3, False, (first,), (third,)),
        SetRow(6, 0, False, (second,), (fourth,)),
        SetRow(6, 1, True, pair_home, pair_away),
    )


def _default_home_roster() -> tuple[RosterRow, ...]:
    return (RosterRow("Alpha Ashford", 3.10, 3.25, captain=True),
            RosterRow("Bravo Beeching", 2.80, 2.95),
            RosterRow("Charlie Cotterill", 4.05, 3.90))


def _default_away_roster() -> tuple[RosterRow, ...]:
    return (RosterRow("Delta Dunmore", 2.60, 2.40, captain=True),
            RosterRow("Echo Elmsworth", 3.35, 3.55),
            RosterRow("Foxtrot Fenwick", 1.95, 2.15))


@dataclass
class CardSpec:
    """Everything one synthetic scorecard needs. Every field has a default.

    The defaults alone make a valid, internally consistent card, which is what
    lets a fixture say only what it is testing.
    """

    # letterhead and the three centred header lines
    club_lines: tuple[str, ...] = ("EXAMPLE DISTRICT TENNIS", "CLUB INC")
    club_web: str = "www.example.invalid"
    club_phone: str = "0700000000"
    club_email: str = "secretary@example.invalid"
    competition_line: str = "Example District Tennis Club Night Fixtures 2026"
    season_line: str = "Season 1"

    # the identity line; the date is printed with its own weekday unless `when` is set
    date: str = "2026-08-18"          # ISO; printed as dd/mm/yyyy with its weekday
    start_time: str = "7:00 PM"
    # The date-and-time line verbatim, for a card that prints its date some other
    # way (a worded month, a two-digit year, no weekday, the wrong weekday). AUTO
    # prints ``date`` and ``start_time`` the way every measured card does; "" prints
    # no date line at all.
    when: str = AUTO
    venue: str = "Example Courts"
    court: str = "01"
    division: str = "C"
    ball: str = "Yellow Ball"         # optional on real cards; "" omits it
    round_label: str = "1"
    match_id: str = "1000001"
    # What separates the identity fields. Every real card measured prints " / ",
    # which is the default; " " is here because a template that leaves the slashes
    # out is the case where a division has to end at the right word rather than at
    # the next punctuation mark, and nothing else can produce that line.
    identity_separator: str = " / "

    # the banner
    home_name: str = "EXAMPLE ALPHAS"
    away_name: str = "EXAMPLE BRAVOS"
    verb: str = AUTO                  # AUTO derives it; "" prints none

    # the table
    rows: tuple[SetRow, ...] = field(default_factory=_default_rows)
    set_rule: str = DEFAULT_SET_RULE  # only used to decide which sets are complete

    # the printed TOTAL row; None derives it from the rows
    totals: tuple[int, int, int, int] | None = None

    # whole-match flags
    winner: str = AUTO                # AUTO, NO_TICK, "home" or "away"
    forfeited_by: str | None = None   # "home", "away" or "both"
    cancelled: bool = False
    disputed: bool = False

    # the foot of the card
    home_roster: tuple[RosterRow, ...] = field(default_factory=_default_home_roster)
    away_roster: tuple[RosterRow, ...] = field(default_factory=_default_away_roster)
    home_fill_ins: tuple[str, ...] = ()
    away_fill_ins: tuple[str, ...] = ()
    comments: str = ""
    # The headings under the roster block. Every real card measured prints
    # `Fill-ins (Home Team)` beside `Fill-ins (Away Team)`, then `Comments`; these
    # are here because a template that words any of them differently, or leaves one
    # out, is the case where the roster block has to end somewhere other than at a
    # word the reader knows, and nothing else can produce that page. The fill-ins
    # headings are home's then away's, each printed over its own column exactly as
    # given; "" prints no heading there.
    fill_ins_headings: tuple[str, str] = ("Fill-ins (Home Team)",
                                          "Fill-ins (Away Team)")
    comments_heading: str = "Comments"

    # --- deliberate defects, for the anti-fixtures -------------------------
    # Each keeps the PDF structurally valid and makes the card wrong in one
    # specific way, so a test can assert the reader refuses it instead of
    # inventing a reading. See the module docstring.

    # Shift every painted element, text and rule alike, by this many points in
    # both x and y. A reader that takes its grid from the card reads a small
    # offset correctly and a reader that fell back to hardcoded pixels does not,
    # so a modest offset is the positive test that no absolute constant is
    # load-bearing. A large one is the refusal test: measured against the
    # predecessor's reader, +40 parsed to identical values, +200 was refused
    # because the banner no longer straddled the page centre.
    page_offset: float = 0.0

    # Omit one of the eight vertical rules on every row, so the grid describes
    # six cells instead of seven. This is F5 in one field: the predecessor
    # substituted hardcoded columns whenever the rule count surprised it, and was
    # measured reading this card with no warning at all.
    drop_column_rule: int | None = None

    extra_tick: bool = False          # one more filled square than the state implies
    omit_totals: bool = False         # print no TOTAL row at all
    omit_verb: bool = False           # print no result verb between the team names
    omit_to_unicode: bool = False     # embed the font with no CMap
    # Print the fill-ins headings where a template of three roster rows does,
    # however long the squads, as this writer did before it moved them under the
    # longer squad: a fifth row then prints too close above them to be read.
    fixed_fill_ins: bool = False

    @property
    def rule(self) -> SetRule:
        return named_set_rule(self.set_rule)


# --- derived values ---------------------------------------------------------


def _row_status_sides(row: SetRow) -> tuple[bool, bool]:
    """Which sides' status box this row ticks, as ``(home, away)``."""
    if row.status is SetStatus.PLAYED:
        return False, False
    if row.status_side == "both":
        return True, True
    return row.status_side == "home", row.status_side == "away"


def _row_set_winner(row: SetRow, rule: SetRule) -> str | None:
    """Who won this set, by exactly the rule ``model.SetResult.winner`` applies.

    Kept deliberately identical: the printed TOTAL row is one of the reader's
    cross-checks (F6), so a writer that counted sets differently from the model
    would make every card it wrote fail its own validation. A forfeited row is the
    one where "identical" is not a matter of writing the same three lines twice —
    the answer is in the ticks as well as the games — so that part is one shared
    function and not a copy.
    """
    if row.status is SetStatus.FORFEIT:
        home_forfeit, away_forfeit = _row_status_sides(row)
        return forfeit_set_winner(home_forfeit=home_forfeit,
                                  away_forfeit=away_forfeit,
                                  home_games=row.home_games,
                                  away_games=row.away_games)
    if stopped_short(row.status):
        return None
    return set_winner(row.home_games, row.away_games, rule)


def _tallies(spec: CardSpec) -> tuple[int, int, int, int]:
    """``(home sets, home games, away games, away sets)`` from the rows."""
    rule = spec.rule
    winners = [_row_set_winner(row, rule) for row in spec.rows]
    return (winners.count("home"),
            sum(row.home_games for row in spec.rows),
            sum(row.away_games for row in spec.rows),
            winners.count("away"))


def _decided_side(spec: CardSpec) -> str | None:
    """The side this card says won, or ``None`` for a draw or no result.

    Games first and sets second, which is the ``games-bonus`` default ruleset's
    order. A club scoring ``sets-first`` has cards where those two orders can
    disagree, and passes ``verb`` and ``winner`` explicitly.
    """
    if spec.forfeited_by in ("home", "away"):
        return other_side(spec.forfeited_by)
    if spec.cancelled or spec.forfeited_by == "both":
        return None
    home_sets, home_games, away_games, away_sets = _tallies(spec)
    if home_games != away_games:
        return "home" if home_games > away_games else "away"
    if home_sets != away_sets:
        return "home" if home_sets > away_sets else "away"
    return None


def _verb_text(spec: CardSpec) -> str:
    if spec.omit_verb:
        return ""
    if spec.verb != AUTO:
        return spec.verb
    side = _decided_side(spec)
    if side is None:
        return "DRAWS WITH"
    return "DEFEATS" if side == "home" else "LOSES TO"


def _winner_side(spec: CardSpec) -> str | None:
    """Which ``Match Winner`` box is ticked."""
    if spec.winner == NO_TICK:
        return None
    if spec.winner != AUTO:
        return spec.winner
    return _decided_side(spec)


def _row_markers(spec: CardSpec) -> tuple[str, ...]:
    """The discipline marker for each row: ``1&2`` for a pairing, ``1`` for singles.

    Positions are the home side's players numbered by first appearance, which is
    what the template does: a four-row ``dssd`` card with two players reads
    ``1&2, 1, 2, 1&2``, and a six-row card with three reads
    ``1&2, 1, 2, 3, 1&3, 2&3``. A fill-in taking a court gets the next number,
    so the marker shows the substitution.
    """
    positions: dict[str, int] = {}
    for row in spec.rows:
        for name in row.home_players:
            positions.setdefault(name, len(positions) + 1)

    markers: list[str] = []
    singles_seen = 0
    for row in spec.rows:
        if row.marker:
            markers.append(row.marker)
            continue
        numbers = sorted({positions[name] for name in row.home_players})
        if row.doubles:
            # The marker has to say "doubles" even when the row names fewer than
            # two players, or the format read off the card would be wrong.
            markers.append("&".join(str(n) for n in numbers)
                           if len(numbers) >= 2 else "1&2")
        elif numbers:
            markers.append(str(numbers[0]))
        else:
            singles_seen += 1
            markers.append(str(singles_seen))
    return tuple(markers)


def _when(spec: CardSpec) -> str:
    """``Tue 18/08/2026 7:00 PM``: the card's only statement of when it was played."""
    if spec.when != AUTO:
        return spec.when
    day = datetime.date.fromisoformat(spec.date)
    stamp = f"{day.strftime('%a')} {day.day:02d}/{day.month:02d}/{day.year}"
    return f"{stamp} {spec.start_time}".strip()


def _identity_line(spec: CardSpec) -> str:
    parts = [f"Division {spec.division}"]
    if spec.ball:
        parts.append(spec.ball)
    parts.append(f"Round {spec.round_label}")
    if spec.match_id:
        parts.append(f"#{spec.match_id}")
    return spec.identity_separator.join(parts)


# --- painting ---------------------------------------------------------------


class _Painter:
    """Content-stream operators, in top-left coordinates.

    ``offset`` is the ``page_offset`` defect: it shifts every element, text and
    rule alike, so the card is a faithful rendering of the wrong place on the
    page. The PDF stays valid and the MediaBox does not move with it, so content
    shifted far enough simply stops being on the page — which is what makes the
    result plausible enough to be worth testing against.
    """

    def __init__(self, *, offset: float = 0.0) -> None:
        self.ops: list[str] = []
        self.offset = offset

    def rect(self, x: float, y: float, w: float, h: float,
             shade: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        """A filled rectangle, black unless a colour is given.

        ``shade`` exists for the two things on a real card that are large filled
        rects and not rules: the white panel under everything and the tinted
        heading band. The reader is colour-blind -- it never reads the content
        stream's colour operators -- so these arrive looking exactly like a very
        large hairline, which is precisely why a fixture should have them.
        """
        x += self.offset
        y += self.offset
        red, green, blue = shade
        self.ops.append(f"{red:.9g} {green:.9g} {blue:.9g} rg "
                        f"{x:.2f} {PAGE_HEIGHT - y - h:.2f} "
                        f"{w:.2f} {h:.2f} re f")

    def text(self, text: str, x: float, baseline: float, size: float) -> None:
        if not text:
            return
        encoded = _printable(text).encode("latin-1")
        hexed = "".join(f"{byte:02X}" for byte in encoded)
        self.ops.append(f"BT 0 0 0 rg /F1 {size:.2f} Tf "
                        f"{x + self.offset:.2f} "
                        f"{PAGE_HEIGHT - baseline - self.offset:.2f} Td "
                        f"<{hexed}> Tj ET")

    def words(self, text: str, x: float, baseline: float, size: float) -> None:
        """One ``Tj`` per word, each with its own position.

        The real producer does this, and the reader's word splitting depends on
        it: a single ``Tj`` holding a whole line would come back as one word and
        every name on the card would be unsplittable.
        """
        cursor = x
        for word in text.split():
            self.text(word, cursor, baseline, size)
            cursor += text_width(word, size) + glyph_width_em(" ") * size

    def centred(self, text: str, centre_x: float, baseline: float,
                size: float) -> None:
        self.words(text, centre_x - text_width(text, size) / 2.0, baseline, size)

    def right_aligned(self, text: str, right_x: float, baseline: float,
                      size: float) -> None:
        self.words(text, right_x - text_width(text, size), baseline, size)

    def in_cell(self, text: str, cell: int, baseline: float, size: float) -> None:
        """Centred within one column of the table, clamped to stay inside it.

        Clamping matters on the left: a name wide enough to overflow into the
        marker column would put stray words where the reader looks for the
        discipline marker, and the format read off the card would change.
        """
        left, right = COLUMN_RULES[cell], COLUMN_RULES[cell + 1]
        width = text_width(text, size)
        x = (left + right) / 2.0 - width / 2.0
        x = min(x, right - CELL_TEXT_INSET - width)
        self.words(text, max(x, left + CELL_TEXT_INSET), baseline, size)

    def checkbox(self, x: float, y: float, *, ticked: bool = False) -> None:
        """Four hairline edges, plus a solid fill when ticked."""
        self.rect(x, y, BOX_SIDE, HAIRLINE)
        self.rect(x, y + BOX_SIDE - HAIRLINE, BOX_SIDE, HAIRLINE)
        self.rect(x, y, HAIRLINE, BOX_SIDE)
        self.rect(x + BOX_SIDE - HAIRLINE, y, HAIRLINE, BOX_SIDE)
        if ticked:
            self.rect(x, y, BOX_SIDE, BOX_SIDE)

    def labelled_box(self, label: str, *, label_x: float, box_x: float,
                     baseline: float, ticked: bool = False,
                     size: float = TEXT_SIZE_BODY) -> None:
        self.words(label, label_x, baseline, size)
        self.checkbox(box_x, baseline - BOX_LABEL_RISE, ticked=ticked)


@dataclass(frozen=True, slots=True)
class _Layout:
    """Where the rows are, and therefore where everything under them is."""

    row_count: int

    def band(self, index: int) -> tuple[float, float]:
        top = TABLE_TOP + index * ROW_HEIGHT
        return top, top + ROW_HEIGHT

    @property
    def table_bottom(self) -> float:
        return TABLE_TOP + self.row_count * ROW_HEIGHT

    def below(self, delta: float) -> float:
        """A baseline ``delta`` points under the last row band."""
        return self.table_bottom + delta

    @property
    def centre_x(self) -> float:
        return (COLUMN_RULES[0] + COLUMN_RULES[-1]) / 2.0


def _paint_header(art: _Painter, spec: CardSpec, layout: _Layout) -> None:
    for index, line in enumerate(spec.club_lines):
        art.words(line, LETTERHEAD_X,
                  LETTERHEAD_FIRST_BASELINE + index * LETTERHEAD_LINE_PITCH,
                  TEXT_SIZE_BODY)
    if spec.club_web:
        art.text(spec.club_web, LETTERHEAD_X,
                 LETTERHEAD_FIRST_BASELINE
                 + len(spec.club_lines) * LETTERHEAD_LINE_PITCH,
                 TEXT_SIZE_BODY)
    art.text(spec.club_phone, CONTACT_X, PHONE_BASELINE, TEXT_SIZE_SMALL)
    art.text(spec.club_email, CONTACT_X, EMAIL_BASELINE, TEXT_SIZE_SMALL)

    art.words(_when(spec), DATE_TIME_X, DATE_TIME_BASELINE, TEXT_SIZE_BODY)
    art.words(f"{spec.venue} {spec.court}", VENUE_X, VENUE_BASELINE,
              TEXT_SIZE_SMALL)
    art.text("Court:", COURT_LABEL_X, COURT_LABEL_BASELINE, TEXT_SIZE_BODY)
    art.rect(COURT_RULE[0], COURT_RULE[1], COURT_RULE[2], HAIRLINE)

    art.centred(spec.competition_line, layout.centre_x, COMPETITION_BASELINE,
                TEXT_SIZE_BODY)
    art.centred(spec.season_line, layout.centre_x, SEASON_BASELINE, TEXT_SIZE_BODY)
    art.centred(_identity_line(spec), layout.centre_x, IDENTITY_BASELINE,
                TEXT_SIZE_BODY)


def _paint_banner(art: _Painter, spec: CardSpec, layout: _Layout) -> None:
    art.words(spec.home_name, BANNER_HOME_X, BANNER_BASELINE, TEXT_SIZE_BANNER)
    art.right_aligned(spec.away_name, BANNER_AWAY_RIGHT_X, BANNER_BASELINE,
                      TEXT_SIZE_BANNER)
    art.centred(_verb_text(spec), layout.centre_x, VERB_BASELINE, TEXT_SIZE_BODY)


def _paint_table(art: _Painter, spec: CardSpec, layout: _Layout) -> None:
    for x, width in HEADER_BAND_CELLS:
        art.rect(x, TABLE_TOP - HEADER_BAND_HEIGHT, width, HEADER_BAND_HEIGHT,
                 shade=HEADER_BAND_TINT)
    art.text("Games", HOME_GAMES_HEADER_X, GAMES_HEADER_BASELINE, TEXT_SIZE_SMALL)
    art.text("SET", SET_HEADER_X, GAMES_HEADER_BASELINE, TEXT_SIZE_BODY)
    art.text("Games", AWAY_GAMES_HEADER_X, GAMES_HEADER_BASELINE, TEXT_SIZE_SMALL)

    markers = _row_markers(spec)
    for index, row in enumerate(spec.rows):
        top, _bottom = layout.band(index)
        for rule_index, x in enumerate(COLUMN_RULES):
            if rule_index == spec.drop_column_rule:
                continue        # the deliberate hole in the grid
            # Once each, at the height the template uses for that rule. The four
            # in the middle are inset by a hairline; see INSET_COLUMN_RULES.
            if rule_index in INSET_COLUMN_RULES:
                art.rect(x, top + HAIRLINE, HAIRLINE, ROW_HEIGHT - 2 * HAIRLINE)
            else:
                art.rect(x, top, HAIRLINE, ROW_HEIGHT)
        for segment_x, segment_w in BAND_SEPARATOR_SEGMENTS:
            art.rect(segment_x, top + ROW_HEIGHT - HAIRLINE, segment_w, HAIRLINE)

        for cell in (CELL_HOME_MARKER, CELL_AWAY_MARKER):
            art.in_cell(f"R{index + 1}", cell, top + ROW_NUMBER_DY,
                        TEXT_SIZE_ROW_NUMBER)
            art.in_cell(markers[index], cell, top + ROW_MARKER_DY,
                        TEXT_SIZE_SMALL)

        for cell, names in ((CELL_HOME_NAMES, row.home_players),
                            (CELL_AWAY_NAMES, row.away_players)):
            for line, name in enumerate(names):
                art.in_cell(name, cell,
                            top + ROW_FIRST_PLAYER_DY + line * ROW_PLAYER_PITCH,
                            TEXT_SIZE_BODY)

        art.in_cell(str(row.home_games), CELL_HOME_GAMES, top + ROW_GAMES_DY,
                    TEXT_SIZE_BODY)
        art.in_cell(SET_CELL_TEXT, CELL_SET_LABEL, top + ROW_GAMES_DY,
                    TEXT_SIZE_BODY)
        art.in_cell(str(row.away_games), CELL_AWAY_GAMES, top + ROW_GAMES_DY,
                    TEXT_SIZE_BODY)

        home_ticks, away_ticks = _row_status_sides(row)
        wanted = row.status.value.capitalize()
        for side_x, side_box_x, side_ticked in (
                (HOME_STATUS_LABEL_X, HOME_STATUS_BOX_X, home_ticks),
                (AWAY_STATUS_LABEL_X, AWAY_STATUS_BOX_X, away_ticks)):
            for label, label_x, box_x in zip(SET_STATUS_LABELS, side_x,
                                             side_box_x):
                art.labelled_box(label, label_x=label_x,
                                 box_x=box_x,
                                 baseline=top + ROW_STATUS_LABEL_DY,
                                 ticked=side_ticked and label == wanted,
                                 size=TEXT_SIZE_SMALL)


def _paint_totals_grid(art: _Painter, layout: _Layout) -> None:
    """The boxes, which the form has whether or not anybody filled them in."""
    top = layout.below(TOTALS_GRID_TOP_DY)
    middle = layout.below(TOTALS_GRID_MIDDLE_DY)
    heading_height, value_height = TOTALS_GRID_ROW_HEIGHTS
    # Three horizontals per cell and not four: the heading row's floor *is* the
    # value row's ceiling, which is how the real card paints it and why a naive
    # box-per-row writer puts four rects here where a club's card has three.
    for x, width in TOTALS_GRID_CELLS:
        for y in (top, middle - HAIRLINE, middle + value_height - HAIRLINE):
            art.rect(x, y, width, HAIRLINE)
    for row_top, height in ((top, heading_height), (middle, value_height)):
        for x in TOTALS_GRID_EDGES:
            art.rect(x, row_top, HAIRLINE, height)

    # The TOTAL cell is one box across both rows, so it has no middle rule.
    label_x, label_width = TOTAL_LABEL_CELL
    art.rect(label_x, top, label_width, HAIRLINE)
    art.rect(label_x, top + TOTAL_LABEL_CELL_HEIGHT - HAIRLINE, label_width,
             HAIRLINE)
    for x in TOTAL_LABEL_CELL_EDGES:
        art.rect(x, top, HAIRLINE, TOTAL_LABEL_CELL_HEIGHT)


def _paint_totals(art: _Painter, spec: CardSpec, layout: _Layout) -> None:
    _paint_totals_grid(art, layout)
    if spec.omit_totals:
        return
    values = spec.totals if spec.totals is not None else _tallies(spec)
    for heading, x in zip(("Sets", "Games", "Games", "Sets"), TOTALS_HEADING_X):
        art.text(heading, x, layout.below(TOTALS_HEADING_DY), TEXT_SIZE_SMALL)
    art.centred("TOTAL", layout.centre_x, layout.below(TOTAL_LABEL_DY),
                TEXT_SIZE_SMALL)
    for value, centre in zip(values, TOTAL_VALUE_CX):
        art.centred(str(value), centre, layout.below(TOTAL_VALUE_DY),
                    TEXT_SIZE_SMALL)


def _paint_match_flags(art: _Painter, spec: CardSpec, layout: _Layout) -> None:
    winner = _winner_side(spec)
    forfeited = spec.forfeited_by
    winner_baseline = layout.below(MATCH_WINNER_DY)
    art.labelled_box("Match Winner", label_x=HOME_FLAG_LABEL_X,
                     box_x=HOME_FLAG_BOX_X, baseline=winner_baseline,
                     ticked=winner == "home")
    art.labelled_box("Match Winner", label_x=AWAY_WINNER_LABEL_X,
                     box_x=AWAY_FLAG_BOX_X, baseline=winner_baseline,
                     ticked=winner == "away")

    forfeit_baseline = layout.below(MATCH_FORFEITED_DY)
    art.labelled_box("Match Forfeited", label_x=HOME_FLAG_LABEL_X,
                     box_x=HOME_FLAG_BOX_X, baseline=forfeit_baseline,
                     ticked=forfeited in ("home", "both"))
    art.labelled_box("Match Forfeited", label_x=AWAY_FORFEITED_LABEL_X,
                     box_x=AWAY_FLAG_BOX_X, baseline=forfeit_baseline,
                     ticked=forfeited in ("away", "both"))

    art.labelled_box("Match Cancelled", label_x=CENTRE_FLAG_LABEL_X,
                     box_x=CENTRE_FLAG_BOX_X,
                     baseline=layout.below(MATCH_CANCELLED_DY),
                     ticked=spec.cancelled)
    art.labelled_box("Match Disputed", label_x=CENTRE_FLAG_LABEL_X,
                     box_x=CENTRE_FLAG_BOX_X,
                     baseline=layout.below(MATCH_DISPUTED_DY),
                     ticked=spec.disputed)

    if spec.extra_tick:
        # One fill too many, over the Match Winner box of the side this card says
        # did *not* win. The result is a card claiming two winners, or one winner
        # its own scores contradict: a state no real card can be in, and one the
        # reader has to refuse rather than resolve (F6).
        loser = other_side(winner) if winner in ("home", "away") else "home"
        box_x = HOME_FLAG_BOX_X if loser == "home" else AWAY_FLAG_BOX_X
        art.rect(box_x, winner_baseline - BOX_LABEL_RISE, BOX_SIDE, BOX_SIDE)


def _rating_text(entry: RosterRow) -> str:
    parts = []
    if entry.singles is not None:
        parts.append(f"{entry.singles:.2f} (S)")
    if entry.doubles is not None:
        parts.append(f"{entry.doubles:.2f} (D)")
    return " ".join(parts)


def _paint_foot(art: _Painter, spec: CardSpec, layout: _Layout) -> None:
    heading_baseline = layout.below(SIGNATURE_HEADING_DY)
    art.words("Home Team", HOME_SIGNATURE_X, heading_baseline, TEXT_SIZE_BODY)
    art.words("Away Team", AWAY_SIGNATURE_X, heading_baseline, TEXT_SIZE_BODY)
    signed_baseline = layout.below(SIGNED_BY_DY)
    art.words("Signed by:", HOME_SIGNATURE_X, signed_baseline, TEXT_SIZE_BODY)
    art.words("Signed by:", AWAY_SIGNATURE_X, signed_baseline, TEXT_SIZE_BODY)
    for x, width in SIGNATURE_RULES:
        art.rect(x, layout.below(SIGNATURE_RULE_DY), width, HAIRLINE)

    roster_heading = layout.below(ROSTER_HEADING_DY)
    art.text("Players", HOME_ROSTER_NAME_X, roster_heading, TEXT_SIZE_BODY)
    art.text("Rating", HOME_RATING_HEADING_X, roster_heading, TEXT_SIZE_BODY)
    art.text("Players", AWAY_ROSTER_NAME_X, roster_heading, TEXT_SIZE_BODY)
    art.text("Rating", AWAY_RATING_HEADING_X, roster_heading, TEXT_SIZE_BODY)
    for x, width in ROSTER_RULES:
        art.rect(x, layout.below(ROSTER_RULE_DY), width, HAIRLINE)

    sides =((spec.home_roster, HOME_ROSTER_NAME_X, HOME_ROSTER_RATING_X),
             (spec.away_roster, AWAY_ROSTER_NAME_X, AWAY_ROSTER_RATING_X))
    for roster, name_x, rating_x in sides:
        for index, entry in enumerate(roster):
            baseline = layout.below(ROSTER_FIRST_ROW_DY + index * ROSTER_ROW_PITCH)
            # "(C)" is how the card states the captain, and it is the club's own
            # record of it. The predecessor discarded it and then required the
            # captain from configuration.
            art.words(entry.name + (" (C)" if entry.captain else ""),
                      name_x, baseline, TEXT_SIZE_BODY)
            art.words(_rating_text(entry), rating_x, baseline, TEXT_SIZE_BODY)

    # Where the template prints the headings, or a pitch under the longer squad's
    # last row if that is lower: printed at the template's place, a fifth row is too
    # close above them for the reader's band to hold it.
    longer = max(len(spec.home_roster), len(spec.away_roster))
    fill_in_baseline = layout.below(FILL_INS_DY if spec.fixed_fill_ins else max(
        FILL_INS_DY, ROSTER_FIRST_ROW_DY + longer * ROSTER_ROW_PITCH))
    for heading, x in zip(spec.fill_ins_headings,
                          (HOME_FILL_INS_X, AWAY_FILL_INS_X)):
        if heading:
            art.words(heading, x, fill_in_baseline, TEXT_SIZE_BODY)
    for names, x in ((spec.home_fill_ins, HOME_FILL_INS_X),
                     (spec.away_fill_ins, AWAY_FILL_INS_X)):
        for index, name in enumerate(names):
            art.words(name, x,
                      fill_in_baseline + (index + 1) * ROSTER_ROW_PITCH,
                      TEXT_SIZE_BODY)

    comments_baseline = layout.below(COMMENTS_DY)
    art.centred(spec.comments_heading, layout.centre_x, comments_baseline,
                TEXT_SIZE_BODY)
    art.centred(spec.comments, layout.centre_x,
                comments_baseline + ROSTER_ROW_PITCH, TEXT_SIZE_BODY)


# --- the file itself --------------------------------------------------------


def _stream(data: bytes) -> bytes:
    return (b"<< /Length " + str(len(data)).encode("ascii") + b" >>\nstream\n"
            + data + b"\nendstream")


def _font_widths() -> bytes:
    """The /Widths array, from the same metrics the layout used."""
    widths = (str(int(round(glyph_width_em(chr(code)) * 1000)))
              for code in range(32, 127))
    return " ".join(widths).encode("ascii")


def _assemble(content: bytes, *, with_to_unicode: bool) -> bytes:
    """A minimal, uncompressed, single-page PDF around one content stream.

    Uncompressed on purpose: a fixture that fails is read by a human with a text
    editor, and ``tools/doctor.py`` should never be the only way to see what a
    card says. No date, no producer string, nothing else that varies between
    runs, so two runs of the same spec are byte-identical.
    """
    font = (b"<< /Type /Font /Subtype /TrueType /BaseFont /AAAAAA+Scorecard"
            b" /FirstChar 32 /LastChar 126 /Widths [ " + _font_widths()
            + b" ] /FontDescriptor 5 0 R")
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [ 3 0 R ] /Count 1 >>",
        3: (f"<< /Type /Page /Parent 2 0 R /MediaBox "
            f"[0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] /Resources "
            f"<< /Font << /F1 4 0 R >> >> /Contents 6 0 R >>").encode("ascii"),
        5: (b"<< /Type /FontDescriptor /FontName /AAAAAA+Scorecard"
            b" /MissingWidth 556 >>"),
        6: _stream(content),
    }
    if with_to_unicode:
        objects[4] = font + b" /ToUnicode 7 0 R >>"
        objects[7] = _stream(_TO_UNICODE_CMAP)
    else:
        # No CMap and no /Encoding: the font says nothing about what its codes
        # mean. A reader with no fallback returns empty text for every glyph and
        # sees a blank page, which is the failure this anti-fixture pins.
        objects[4] = font + b" >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode("ascii") + objects[number] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for number in range(1, size):
        kind = "n" if number in offsets else "f"
        out += f"{offsets.get(number, 0):010d} 00000 {kind} \n".encode("ascii")
    out += (f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n"
            f"{xref_at}\n%%EOF\n").encode("ascii")
    return bytes(out)


def write_card(spec: CardSpec) -> bytes:
    """One scorecard PDF as bytes. Deterministic: same spec, same bytes."""
    if not spec.rows:
        raise ValueError("a scorecard needs at least one set row")
    if len(spec.rows) > MAX_SET_ROWS:
        raise ValueError(
            f"{len(spec.rows)} set rows do not fit on one page: the roster and "
            f"comments block would start below y={PAGE_HEIGHT:.0f} "
            f"(at most {MAX_SET_ROWS} rows)")
    if spec.drop_column_rule is not None and not (
            0 <= spec.drop_column_rule < len(COLUMN_RULES)):
        raise ValueError(
            f"drop_column_rule={spec.drop_column_rule} is not one of the "
            f"{len(COLUMN_RULES)} column rules")
    # Parsed here rather than at paint time so a bad date names the spec, not a
    # line of painting code.
    datetime.date.fromisoformat(spec.date)

    layout = _Layout(row_count=len(spec.rows))
    art = _Painter(offset=spec.page_offset)
    art.rect(*CARD_PANEL, shade=WHITE)      # first, so everything sits on it
    _paint_header(art, spec, layout)
    _paint_banner(art, spec, layout)
    _paint_table(art, spec, layout)
    _paint_totals(art, spec, layout)
    _paint_match_flags(art, spec, layout)
    _paint_foot(art, spec, layout)
    return _assemble("\n".join(art.ops).encode("latin-1"),
                     with_to_unicode=not spec.omit_to_unicode)


def write_card_file(path: str | Path, spec: CardSpec) -> Path:
    """Write one card to ``path``, creating parent directories. Returns the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(write_card(spec))
    return target
