# The Match Centre scorecard template, measured

Numbers below were measured off a real exported scorecard with a script, not read
off a spec. They are the shared reference for two modules that must agree:
`scorecard.py` (reads cards) and `cardwriter.py` (writes them for tests and demo
data). Neither may hardcode a *position* — see F5 — but both need to know what the
structure looks like.

Producer string on the real cards: **wkhtmltopdf 0.12.6 / Qt 5.15.13**. Match
Centre renders an HTML template, so every card from the site has the same
structure and only the club letterhead differs.

## Page

| Property | Value |
|---|---|
| MediaBox | `0 0 595 842` (A4 portrait) |
| Words on a full card | ~206 |
| Filled rects | ~200 |
| Coordinate system used by `pdf.py` | **top-left origin**, `y = page_height - y_pdf` |

Every number in this file is in that flipped top-left space, which is what
`Word.baseline`, `Rect.top` and friends report.

## Header block

| Element | x | baseline | size |
|---|---|---|---|
| Club letterhead lines | 80.95 | 28.73 / 39.10 / 49.47 / 59.85 | 7.49 |
| Phone / email | 92.18 | 71.95 / 84.05 | 6.34 |
| Date and time, e.g. `Tue 18/08/2026 7:00 PM` | 466.98 | 28.73 | 7.49 |
| Venue and court, e.g. `University 01` | 526.93 | 39.10 | 6.34 |
| `Court:` label | 471.42 | 59.85 | 7.49 |
| Competition line, centred | — | 57.54 | 7.49 |
| Season line, centred | — | 67.91 | 7.49 |
| `Division C / Yellow Ball / Round 1 / #3665611`, centred | — | 78.29 | 7.49 |

The identity line is the one that matters. It is the only place the division, the
round and the match id appear. Read it with a tolerant pattern: `Division`,
`Grade` and `Section` all occur across associations, the ball colour is optional,
and the `#id` is optional.

The **date is the only source of the weekday**. Never a filename, never a folder.

## Banner

| Element | x | baseline | size |
|---|---|---|---|
| Home team, left-aligned | 60.78 | 104.22 | 10.37 |
| Away team, right-aligned to 568.1 | — | 104.22 | 10.37 |
| Result verb, centred | — | 103.07 | 7.49 |

Team names are printed in caps. The verb is `DEFEATS`, `DRAWS WITH` or `LOSES TO`
and is the cheapest of the three cross-checks (F6).

## Set table

The table's own vertical rules define the grid. Read them; do not assume them.

- **Column rules**: filled rects with `w < 2.0` and `40.0 < h < 60.0`.
  On the real card there are 8, at x =
  `26.4, 61.6, 250.6, 281.1, 313.4, 344.0, 533.0, 568.1`.
- Those 8 rules make 7 cells: row marker | home names | home games | `SET` label |
  away games | away names | row marker.
- **Row bands** are the distinct `(top, top + h)` spans of those rules. On the real
  card: `(126.7, 174.0) (174.0, 221.2) (221.2, 268.5) (268.5, 315.7)`, row height
  **47.26**.
- Locate the games and label cells by the header words `Games` / `SET` printed
  above the table (x 253.34, 290.54, 319.64 at baseline 116.32) rather than by
  cell index, so a template with a different column count still resolves.

Rect census on the real card, which is how the counts below are derived:

| Size (w × h) | Count | What it is |
|---|---|---|
| 8.07 × 0.58 | 60 | horizontal hairlines of 30 checkboxes |
| 0.58 × 8.07 | 60 | vertical hairlines of the same 30 |
| 0.58 × 47.26 | 16 | 8 column rules × 4 rows, outer |
| 0.58 × 46.10 | 16 | the same rules, inner |
| 8.07 × 8.07 | 1 | the single ticked box (`Match Winner`, home) |

### Checkbox state is geometry, not text

There is no glyph for a tick. An **unticked** box is four hairline rects forming a
square outline: two of `8.07 × 0.58` and two of `0.58 × 8.07`. A **ticked** box is
that outline *plus* a solid `8.07 × 8.07` fill at the same origin.

Constants: side `8.07`, hairline `0.58`, tolerance `0.6`, label gap `16.0` (the
label sits to the right of its box, on the same baseline).

Expected box count scales with the row count — do not hardcode 30:

```
boxes = rows * 2 sides * 3 per-set flags     (Forfeit, Retired, Incomplete)
      + 4                                    (Match Winner ×2, Match Forfeited ×2)
      + 2                                    (Match Cancelled, Match Disputed)
```
Four rows → 30. Six rows → 42, which was verified by generating a six-row card and
reading it back.

### Per-set status labels

Three per side, per row, each with its box 10.7pt to its left and 6.35pt above the
label baseline:

| Label | home x | away x |
|---|---|---|
| `Forfeit` | 78.65 | 415.04 |
| `Retired` | 115.84 | 452.23 |
| `Incomplete` | 155.61 | 492.00 |

### Discipline markers

Each row prints its marker twice, in the outer marker columns: `1&2` for a doubles
pairing, `1` or `2` alone for singles. A six-row card reads `1&2, 1, 2, 3, 1&3,
2&3`. This is what makes the match format derivable (see `format.from_rows`).

## Totals and match flags

| Element | x | baseline |
|---|---|---|
| `Sets` / `Games` / `Games` / `Sets` labels | 204.70 / 247.16 / 325.84 / 376.74 | 326.67 |
| `TOTAL`, centred | — | 334.16 |
| values, centred | 211.5 / 258.2 / 336.9 / 383.6 | 339.92 |
| `Match Winner` (home) label / box | 40.61 / 26.42 | 334.74 / 328.39 |
| `Match Winner` (away) label / box | 502.67 / 560.64 | 334.74 / 328.39 |
| `Match Forfeited` (home) label / box | 40.61 / 26.42 | 347.99 / 341.65 |
| `Match Forfeited` (away) label / box | 495.76 / 560.64 | 347.99 / 341.65 |
| `Match Cancelled` label / box | 264.42 / 250.02 | 358.36 / 352.03 |
| `Match Disputed` label / box | 264.42 / 250.02 | 371.62 / 365.28 |

On a taller table every y here shifts down by `(rows - 4) * 47.26`. Lay the block
out relative to the last row band, not at a fixed y — a prototype that used fixed
positions had its TOTAL row collide with rows 5 and 6 of a six-row card.

## Signatures and rosters

| Element | home x | away x | baseline |
|---|---|---|---|
| `Home Team` / `Away Team` | 26.21 | 359.54 | 411.96 |
| `Signed by:` | 26.21 | 359.54 | 422.33 |
| `Players` / `Rating` headers | 26.21 / 187.76 | 357.61 / 523.51 | 462.67 |
| roster rows (3) | 26.21 | 357.61 | 476.50, 486.88, 497.25 |
| ratings | 167.41 | 503.15 | same baselines |
| `Fill-ins (Home/Away Team)` | 67.22 | 429.86 | 522.61 |
| `Comments`, centred | — | — | 569.29 |

Roster rows print `Name (C)` for the captain and `3.82 (S) 3.97 (D)` for ratings.
Row pitch is 10.375. Both facts the predecessor threw away and then required from
configuration: the captain, and the properly-cased name.

## Text extraction

`pdf.py` decodes glyphs through the font's `/ToUnicode` CMap. The real cards embed
one; a card written by `cardwriter.py` must ship one too (a 1-byte
`<20> <7E>` codespace with a matching `bfrange` is enough). Without it, the old
reader returned `""` for every glyph and the page looked empty — which is why
`pdf.py` also needs the `/Encoding` and WinAnsi fallbacks listed in the contract.

Word splitting relies on `Td`/`TJ` offsets: the producer emits one `Tj` per word
with its own positioning, so a word-per-`Tj` writer round-trips exactly.
