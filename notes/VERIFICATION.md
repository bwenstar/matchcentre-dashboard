# Verification log

What has actually been measured, as opposed to designed. Every entry names the
command that produced it so it can be re-run. Kept out of git with the rest of
`notes/`.

Dates are 2026-09-10 unless stated.

---

## 1. `pdf.py` against the predecessor's six real scorecards

Source: `tennis-scorecard-generator/data/TuesdayResults/**/*.pdf` — six cards
exported from `matchcentre.tennis.com.au`, produced by `wkhtmltopdf 0.12.6 /
Qt 5.15.13`. Read-only reference; never copied into this project.

| card | page | rects | column rules | row bands | checkbox groups | ticks |
|---|---|---|---|---|---|---|
| all six | 595×842 | 200 | **8 / 8** | **4 / 4** | **30** | 1 |

Maximum deviation of any recovered column rule from the position recorded in
`TEMPLATE.md`: **0.04 pt**.

Two findings worth keeping:

**The column rules are drawn per row band, not as full-height lines.** Each is a
`0.58 × 47.26` or `0.58 × 46.10` segment repeated once per band. A first attempt to
find them with "tall and thin" (`h > 100`) found *zero* rules on every card and
looked like a `pdf.py` bug; it was a bad heuristic. Column recovery must cluster
x-positions across short segments. The eight clusters:

```
26.42  61.58  250.60  281.14  313.41  343.96  532.98  568.13
```

The four outer rules (`26.42, 61.58, 532.98, 568.13`) are `47.26` tall and the four
inner ones `46.10`, four bands each: 16 + 16 = 32 vertical segments.

**The checkbox count invariant holds exactly.** `rows * 2 * 3 + 4 + 2` = `4*6+4+2`
= **30** on all six cards, confirming the geometry model in `CONTRACT.md`: three
per-set status boxes per side per row, four match-level boxes, two signature boxes.
Each unticked box is four hairlines (`8.07 × 0.58` and `0.58 × 8.07`); a tick adds
an `8.07 × 8.07` fill. Every real card carries exactly one fill — the Match Winner
box — which is why `square_fills()` returning 1 is the expected reading and not a
sign that ticks were missed.

## 2. `cardwriter.py` fidelity: synthetic card vs real card

Updated 2026-09-14. `write_card(CardSpec(rows=<a four-row home win>))` measured
against `Iguanas-ICBC.pdf` by the same code, rect by rect: each real rect paired
with the nearest unclaimed synthetic one, comparing `x`, `y`, `w`, `h`.

```
real 200 rects   synthetic 200 rects
matched within 0.1pt: 200/200   max deviation 0.043 pt
unmatched real: 0   left-over synthetic: 0
```

**The generated card is now a structural replica**, not an approximation: 24
distinct shapes, the same count of each, every one within a twentieth of a point
of where the real producer puts it. Also matching, and checked separately: page
size 595×842, 30 checkbox groups, one tick, and the printed text line for line in
the same order — including the details a reader would not guess (`S1` in the set
column of *every* row, `R1..R4` on both sides, `1&2` / `1` / `2` markers, the
`Sets Games | Games Sets` header, the `TOTAL` row).

Pinned by `TestTheFixtureCardHasTheShapeOfARealOne` in `tests/test_parsing.py`,
which carries the measurements as literals rather than reading them back out of
`cardwriter`. That distinction is not pedantry: the first draft of that class took
the inset-rule set from `cardwriter.INSET_COLUMN_RULES`, and emptying that
constant — which paints every rule at the wrong height — left the class green.

### What was wrong before, and what it would have cost

Six differences, all found by measurement rather than by reading the code:

| what the real card does | what this writer did | why it mattered |
|---|---|---|
| lays a white `574.56 × 602.22` panel under everything | nothing | the largest rect on the page by far, and not part of the grid. A reader that took the biggest rect for the table would have passed here and failed on a club's card. Earlier notes called this an "outer table border" — it is `1 1 1 scn … re f`, a white background, and the mislabel is what made it look safe to skip |
| three tinted cells above the table carrying the `Games / SET / Games` headings | nothing | same argument: two large filled rects the reader must know to ignore |
| paints each column rule **once**, at one of two heights | painted every rule at **both** heights | 32 vertical rects no club's card has. Any assertion about the count was wrong, and a reader keying on a rule starting flush with the band top would have been fine here and wrong there |
| closes each band with **three** abutting segments | one full-width line | five rects of a width the real card never draws. A reader could have come to depend on a single line spanning the table |
| boxes the totals row, three horizontals per cell (the heading row's floor is the value row's ceiling) | printed the numbers as text, no boxes | the roster and totals are read from words, so nothing keyed on these — *today*. A fixture missing shapes cannot fail the day something does |
| rules four write-on lines: beside `Court:`, under each `Signed by:`, under each `Players  Rating` | nothing | as above |
| spaces the six status boxes unevenly from their labels | derived all six from one offset | four of the six landed a third of a point out. Measured beats derived |

Everything below the table is placed as an offset from the bottom of the last row
band, never at absolute `y`, so a card with six set rows carries the same block two
rows further down. `test_the_block_below_the_table_moves_down_with_a_longer_table`
compares the two as offsets and is what stops that regressing — an earlier
prototype ruled its `TOTAL` row straight through rows five and six.

### Still not claimed: text advance widths

The rects are a replica; the *text* is the same strings in the same order, but not
to the same precision. Every template string is there, `Fill-ins (Home Team)` and
`Comments` included, and each is anchored within about **1.2 pt** of the real one:

```
real  Players 39.80   Rating 199.92        real  Fill-ins 81.36  (Home 111.95  Team) 141.64
synth Players 39.28   Rating 198.75        synth Fill-ins 80.46  (Home 105.65  Team) 127.47
```

The second column is the gap. A word placed at its own measured x lands within a
point; a word that *follows* another in the same drawn string is placed by adding
this writer's own advance widths, which are not the real producer's font metrics,
so the drift accumulates — 14 pt by the third word of that line.

Inert for the reader, and worth knowing why rather than trusting it: the reader
assigns words to cells by x, and every value it reads from inside the table (games,
markers, row numbers, set labels) is a single short string placed at its own x. The
drift only appears in multi-word prose below the table, which nothing keys on. A
future reader that started reading a multi-word *phrase* by column would need real
font metrics here first.

## 3. `jslint.py`, the JavaScript checker

There is no JS engine on this machine (`which node nodejs deno bun qjs` → nothing),
so the browser code cannot be executed by its own tests. `jslint.py` tokenises and
checks bracket balance instead. Validated against the predecessor's real
`dashboard/shared/app.js` — **2364 lines, 94,813 characters**:

- reported balanced; no unterminated literal; no template literals
- blanked output preserves length and every newline, so reported line and column
  numbers are the file's own
- 12 `getElementById` calls with literal ids recovered, 7 distinct

Six kinds of injected damage, all caught with the right line and column:

| injected | reported |
|---|---|
| extra `}` at end | `line 2365, column 1: closing '}' with nothing open` |
| first `}` deleted | `line 37, column 31: '{' … is never closed` |
| trailing `/* oops` | `line 2365, column 1: '/*' … never closed by '*/'` |
| unterminated `'` | `line 2365, column 9` |
| unterminated `` ` `` | `line 2365, column 9` |
| `[1,2)` | `closing ')' but '[' … needs ']'` |

The unterminated-comment case is why `Fault.kind` exists. Blanking an unclosed
`/*` runs to end of file, which **hides every bracket after it** — appending
`/* oops` to a balanced file left it still looking balanced. A bracket check alone
called a file healthy that a browser would refuse to load, so an unclosed literal
is now reported in its own right rather than inferred.

Thirteen false-positive traps all pass clean: braces inside regexes, strings, line
comments and block comments; division after `)`; escaped quotes; a `/` inside a
regex character class; a URL in a string; a string line continuation; an
apostrophe in a comment; `*/` inside a string.

## 4. Club-specific and personal references

`notes/leak-terms.txt` holds 217 strings generated from the predecessor's
`data/competitions.json` — every team name and every real player's name — plus the
club identifiers by hand. `tests/test_foundation.py` reads that file and scans every
publishable file. Gitignored on purpose: a tracked test listing the names would
publish the very names the check exists to keep private, and a fresh clone has no
predecessor to leak from, so the check skips there.

Found and fixed by this check, none of it by inspection:

- `slugs.py`, `scoring.py`, `config.py`, `model.py` — club names in docstring and
  comment examples, all rewritten around an invented club (Riverside Rockets, RTC)
- `slugs.py` and `stats.py` — **a real player's name**, used as the accented-name
  example. It had been assumed invented; it is in the predecessor's data. Replaced
  with `Ana Fernández`, which is not.

Three were outstanding when the above was written, in files a workflow agent still
held. Re-checked 2026-09-14, all three closed:

- `discover.py` — the real team name is gone from the `[captains]` example; the
  scan over every publishable file passes (109 tests in `test_foundation.py`, OK)
- `site.py` — `_theme_key` derives the key from the club name and the comment no
  longer names the predecessor's; it explains the collision instead
- `cardwriter.py:403` — `venue` now defaults to `"Example Courts"`

Two echoes the leak list could never have caught, because they were not names:
`tools/demo.py` defaulted to `--day Tuesday --division C` and
`config.example.toml`'s worked example was `[competitions.tuesday-division-c]` —
the predecessor's own competition, shipped as the thing a club sees first. Now
Wednesday, Division 3, which also puts a numeric division through the whole
pipeline. Found by reading the shipped files for the scenario rather than for the
names.

## 5. The README quotes real output, not remembered output

Measured 2026-09-14. Three of the README's sample blocks were hand-written and
two of them were wrong:

| Block | Was | Now |
|---|---|---|
| `== cards` / `== result` / ladder | described 12 cards and a 3-team ladder; the demo writes 15 and 6 | verbatim output of `tools/demo.py` then `build.py` |
| `tools/doctor.py` verdict | `division C`, a Tuesday date, and a set of teams that never met in the demo | verbatim verdict for `data/demo-season/round-01/rosellas-v-kookaburras.pdf` |
| in-page links | one pointed at a heading whose title had changed | all 22 resolve |

`tools/demo.py` output is byte-identical run to run (two fresh copies, `md5sum`
over the whole tree — no difference), which is what makes quoting it testable at
all. Guarded by `TestTheReadmeQuotesWhatThisProgramPrints` in `tests/test_cli.py`,
which runs the documented commands in a temporary directory, and by
`test_every_link_it_makes_to_itself_lands_on_a_heading`.

Both new tests were mutation-tested, six mutations, all caught: division changed
back to `C`; two verdict lines swapped; one deleted; a cross-check reworded; a link
to a section that does not exist; and a link to a `#` comment *inside a code
fence*, which is the case that proves the heading collector tracks fences — this
README has commented-out TOML in a fenced block, and counting those lines as
headings would let a link to a comment pass.

The verdict is matched as a **contiguous** block against a **single** card, on
purpose. Line by line across all fifteen would accept a verdict assembled from six
different matches — the division from one, the scoreline from another — which is
the exact failure mode the hand-written block had.
