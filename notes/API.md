# Pinned module APIs

These signatures are fixed so the modules can be written independently and still
fit together. If a module needs a different signature, that is a change to this
file first.

Already written and **not to be modified** by anyone implementing against them:
`slugs.py`, `format.py`, `scoring.py`, `model.py`, `competition.py`, `config.py`,
`card.py`. Read them — they are the vocabulary.

---

## `pdf.py` — unchanged from the predecessor, plus hardening

```python
class PdfError(Exception): ...

@dataclass(frozen=True, slots=True)
class Word:
    text: str; x0: float; y0: float; x1: float; y1: float
    size: float; bold: bool; baseline: float = 0.0
    # properties: cx, cy, width

@dataclass(frozen=True, slots=True)
class Rect:
    x0: float; y0: float; x1: float; y1: float; filled: bool
    # properties: w, h, cx, cy; methods: contains(x, y, pad=0.0), overlaps(other)

@dataclass(frozen=True, slots=True)
class Page:
    width: float; height: float
    words: tuple[Word, ...]; rects: tuple[Rect, ...]
    source: str = ""; index: int = 0; page_count: int = 1
    def lines(self, tol: float = 2.0) -> list[list[Word]]
    def text(self, tol: float = 2.0) -> str
    def words_in_band(self, top, bottom, x0=None, x1=None) -> list[Word]
    def find(self, text: str) -> list[Word]
    def square_fills(self, min_side=2.5, max_side=20.0, tol=0.35) -> list[Rect]

def extract(path, *, page: int = 0) -> Page
def page_count(path) -> int
def group_lines(words, tol: float = 2.0) -> list[list[Word]]
```

All coordinates are **top-left origin** (`y = page_height - y_pdf`).

## `scorecard.py`

```python
def read(path, *, expect: MatchFormat | None = None) -> tuple[list[Card], list[Rejection]]
    """Every page of one PDF. A page that cannot be read becomes a Rejection,
    never an exception and never a partial Card."""

def read_page(page: pdf.Page, *, expect: MatchFormat | None = None) -> Card
    """Raises CardError. Used directly by tests and by tools/doctor.py."""

def read_tree(root, *, expect=None) -> tuple[list[Card], list[Rejection]]
    """Every *.pdf under root, sorted by path for reproducibility."""

def describe(page: pdf.Page) -> str
    """Human-readable structural summary, for tools/doctor.py."""
```

## `discover.py`

```python
@dataclass(frozen=True, slots=True)
class Discovery:
    club: Club
    matches: dict[str, list[Match]]      # competition id -> matches
    cards: dict[str, list[Card]]         # competition id -> source cards
    rejected: list[Rejection]
    notes: list[str]                     # human-facing provenance remarks
    club_name: str                       # letterhead guess, for the config stub

def discover(cards, rejected, *, config: Config, root: Path) -> Discovery
def season_key_for(card: Card, *, root: Path, multi: bool) -> str
def route(card: Card, *, config: Config, multi: bool) -> str   # competition id
```

Rules it must honour: F1, F2, F3, F7, F8, F11 from `CONTRACT.md`. Team identity is
`team_slug(printed_name, strip_prefix=config.strip_team_prefix)` then
`apply_alias`. Player identity is `slugify(printed_name)` then `apply_alias`.

## `stats.py`

Same shape as the predecessor's, with team keys as slugs and `by_slot` keyed by
format slot keys. Public surface:

```python
PRECISION = 6
def match_sort_key(match) -> tuple
def chronological(matches) -> list[Match]
def rounds_present(matches) -> list[str]
def round_display(v) / round_stored(v)
def with_byes(matches, competition) -> list[Match]      # honours F1
class PointsLedger: earned, decided, averaged_matches, base_average
    # properties averaged, counted, total, average; method breakdown()
def points_ledger(matches, team: str) -> PointsLedger
def round_points(matches, team) -> list[RoundPoints]
def head_to_head(matches) -> dict[str, HeadToHead]      # keys "slug|slug"
def ladder(matches, competition) -> list[str]           # honours rules.ladder_by
def ladder_history(matches, competition) -> dict[str, list[LadderEntry]]
def ladder_complete(matches, competition) -> bool       # F10
def team_stats(matches, competition) -> dict[str, TeamStats]
def player_stats(matches, competition) -> dict[str, PlayerStats]
def player_names(matches, competition) -> dict[str, str]
def is_fill_in(slug, registered, roster, side_team: str) -> bool
```

`TeamStats` keeps the predecessor's fields with `code: int | None` demoted to
optional display metadata and `slug` as the key. `PlayerStats` keeps its fields
with `team: str | None` holding the team **slug** and `team_name: str | None` the
display name.

## `validate.py`

```python
@dataclass(frozen=True, slots=True)
class Issue:
    rule: str          # "F1".."F11", or a check name like "totals-mismatch"
    severity: str      # "error" | "warning"
    message: str
    subject: str = ""
    detail: str = ""

@dataclass(frozen=True, slots=True)
class Report:
    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    @property
    def ok(self) -> bool

def validate(matches, competition, *, club=None, cards=(), rejected=()) -> Report
```

## `export.py`

```python
def build_season(matches, *, competition, source_dir, generated=None, club=None,
                 cards=(), rejected=(), seasons_mode="single") -> dict
def write_season(path, season: dict) -> None
```

Document shape is `CONTRACT.md` §9.

## `site.py`

```python
def write_site(out_dir, *, seasons: list[dict], club: Club, config: Config,
               generated: str | None = None) -> list[Path]
    """Writes one directory per competition plus the picker at the root."""
def write_competition(out_dir, season: dict) -> list[Path]
```

Each competition directory gets `index.html`, `app.js`, `styles.css`,
`season.json` and `season-data.js`. `season-data.js` is
`window.SEASON_DATA = {...};` and exists because a browser refuses to `fetch()` a
sibling file over `file://` — it is what makes double-clicking `index.html` work.

## `cardwriter.py`

```python
@dataclass
class CardSpec:      # everything a synthetic card needs; all fields defaulted
    ...
def write_card(spec: CardSpec) -> bytes
def write_card_file(path, spec: CardSpec) -> Path
```

Geometry comes from named constants shared with nothing else, laid out per
`TEMPLATE.md`, with the totals/signature block positioned **relative to the last
row band** so a card with more or fewer than four sets stays legible.

Must support, for the anti-fixtures the test suite needs:
`page_offset`, `drop_column_rule`, `extra_tick`, `omit_totals`, `omit_verb`,
`omit_to_unicode`, `rows` of any length.

## `build.py`

```
python3 build.py [--data DIR] [--out DIR] [--config FILE] [--check]
                 [--discover] [--force] [--quiet] [--generated ISO]
```
Exit 0 only when every card found was scored and validation has no errors.
Prints the active rules, the provenance summary, and the review checklist.
