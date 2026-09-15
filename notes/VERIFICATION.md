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

## 6. Navigation over `file://`

Reported 2026-09-14 from use, not from testing: clicking a competition on the
front page showed a **directory listing**, and so did clicking "← All
competitions" to come back.

The cause is the one difference between the two ways this site can be opened. Over
http a server resolves `foo/` to `foo/index.html`; over `file://` there is no
server, so the browser lists the folder. Every navigation target was written as a
directory, which works perfectly through a local web server — which is how it was
being looked at — and fails on the one path `build.py` actually promises
("double-click works; no web server needed").

Confirmed in `/tmp/navbug`, then audited exhaustively rather than fixed at the two
places the report named, because a missed link site leaves the same broken page
somewhere else. **26 agents**, finders per surface piped into verifiers defaulting
to `real=false`. Three producers, covering every occurrence:

| producer | was | now |
|---|---|---|
| `site.py` `_href` | `<comp>/`, `<season>/<comp>/` | names `index.html`; fixes the front page's anchors *and* `picker-data.js`, its only two consumers |
| `app.js` `rootHref()` | `'../'` / `'../../'` | names `index.html`; fixes all three consumers (the season `<select>`, the "All seasons" anchor, and the `.home-link` retarget) |
| `web/index.html:61` | `href="../"` | `href="../index.html"` |

**The third fix is not sufficient on its own, and that is the part the report could
not have shown.** A competition in a multi-season site sits two levels down, so
`../index.html` from there resolves to `site/<season>/index.html` — a file no
build writes. `app.js` retargets the link at run time, but markup corrected only
by script is wrong for as long as the script takes to run and stays wrong if it
never runs. The depth is known when the page is written, so `write_competition`
now rewrites the link on the way out (`HOME_LINK`, `_root_depth`,
`_pointed_home`), from the same two `meta` fields that decide the nesting. A
template that no longer carries the link verbatim raises `SiteError` rather than
substituting nothing and shipping the wrong depth. Single-season sites still get
the template byte for byte, which a test asserts.

Pinned by `TestEveryLinkInTheBuiltSiteOpensWithoutAServer` in `tests/test_cli.py`,
which builds both site shapes and **resolves every `href`/`src` in every generated
page, plus every `href` in `picker-data.js`, against the filesystem** — so it also
fails on a link to a page that simply is not there, not just on one ending in a
slash. The links `app.js` builds at run time cannot be reached that way (no JS
engine, §3), so `TestNavigationNamesPagesRatherThanFolders` in
`tests/test_web_assets.py` reads `rootHref()`'s returns as source.

Six mutations, all caught: each of the three producers reverted to a directory;
the depth rewrite disabled; the depth rewrite made one level too deep; and the
fail-closed check on a missing template link removed. Suite 288 → 296 tests, and
`diff -r` of two builds is still empty, so the fix did not cost determinism.

One thing the audit got wrong, worth recording because it argues for checking a
verifier's reachability claims: two agents reported the `fillSeasonPicker` path
dead because nothing hooks `#season-picker`. `web/index.html:79` is
`<select id="season-picker" …>`. The fix is the same either way — every branch of
`rootHref()` now names a file — but the reasoning offered for it was false.

## 7. Two real seasons against the association's own points sheets

2026-09-14/15. The first measurement of this project's output against a published
ladder rather than against itself. Input, supplied as a zip: a `data/` tree of real
cards run through `build.py` on another machine, the `config.toml` it produced
(`rounds_total = 10` the only line edited by hand), and photographs of the
association's points sheets for both grades. All of it kept in `/tmp`; no card,
name or club identifier copied into this project. Teams below are lettered, not
named.

Two grades, deliberately unlike each other:

| grade | teams | byes on the sheet | washouts on the sheet |
|---|---|---|---|
| Tuesday, division C | 6 | none | none |
| Monday, division B2 | 5 | `B`, one per round | `W`, several |

**Tuesday matched to the digit**: pts `24.9 / 22.8 / 20.6 / 15.9 / 14.7 / 10.0`,
and the average column agreed everywhere including `6.23` and `3.68`. Nothing was
adjusted to get that; it is the six-team even-draw case, which is the case with no
byes in it.

**Monday came out `25.6 / 14.1 / 12.9 / 11.7 / 9.9` against the sheet's
`25.6 / 14.1 / 12.9 / 11.9 / 9.9`.** Four of five exact. The fifth is not this
tool's arithmetic: team D's round 5 card prints `TOTAL 9-24`, and the result verb
between the team names and the `Match Winner` tick both agree with it, so the three
independent statements on the page give 2.9 and the sheet's total implies 3.1. A
card that disagrees with the sheet is the situation this program is built to
surface rather than smooth over, so it is left standing and recorded here.

Getting there took four changes, all of which the sheets argued for:

1. **`[rules] unplayed`.** The five inferred byes were being paid the team's own
   average, because `games-bonus` averages by default. This association scores them
   as nothing. Two associations on the same points scheme differ here, so it is a
   setting rather than a preset detail — and it can only move a points total, never
   the ladder's order, since crediting a team its own average cannot move that
   average.
2. **The `[competitions.<id>.byes]` table**, keyed by team. `W` and `B` are marks on
   the association's sheet and appear nowhere on any card; a five-team grade's bye
   is inferable only while exactly one team is missing from a round, which a missing
   card or a washed-out week breaks. The question that prompted this was "surely I
   don't have to enter every match?" — and the answer is that no fixture, date or
   draw is needed, just the sheet's own bye column.
3. **A declared round no longer discards the byes inferred in the others.** Writing
   one `[[rounds]]` block to pin a date silently emptied every other round's byes,
   which is the worst possible shape for the bug: the more a club configures, the
   less it gets.
4. **Half-up rounding.** `6.225` is held as 6.22499999999999964, so `round()` gives
   `6.22` where the sheet prints `6.23`. Fixed in the Python with
   `Decimal(str(value))` and in the page with the shortest-round-tripping-string
   exponent trick, which is why the two agree by construction. `f"{23.25:.1f}"` →
   `23.2` was the same fault one column over.

Tests: **297 → 356**, run byte-identically twice. **34 mutations across three
drivers, all caught** — 11 in `config.py`'s reading of the two new settings, 13
across `discover.py`, `competition.py`, `stats.py` and `export.py`, 10 on the
browser side and the exporter fields the page reads.

Three things the mutation run taught, each of which changed how the run is done:

**A same-length edit restored inside one second can leave a `.pyc` Python thinks is
current.** `stats.round_display(23.25)` returned `23.25` while the source read
`round_half_up(value, 1)`; `co_consts` held `(None, 2)`. Source mtime is
1-second granular and the mutation `1` → `2` did not change the file's size, so the
cached bytecode still validated. Every driver now runs with
`PYTHONDONTWRITEBYTECODE=1` and clears `__pycache__` before the run and after each
restore, and both earlier drivers were re-run clean to be sure no result had been
distorted. A mutation driver that patches and restores fast is precisely the
workload that defeats mtime-and-size validation.

**Two guards each masked the other.** `bool` is an `int` in Python, so
`Rockets = true` in a byes table would have become a bye in a round called `"True"`
— a week no draw has, paying a team for resting in it. The scalar shortcut and the
per-item check both reject it, so mutating either one alone left the other to
catch the case and the test passed. The test now asserts each guard's own message
(`"should be a list"` against `"not a round number"`) and adds a wrapped
`[[6]]`, which is the only shape neither guard reaches by accident.

**A test that checks a word appears somewhere in a function is not checking the
sentence it appears in.** Removing `unplayed` from the rules banner's footnote
survived, because `r.unplayed` occurs elsewhere in the same function. The
replacement extracts the banner's row labels by regex and asserts each one appears
inside the footnote sentence itself, so the footnote cannot fall behind the rows it
describes.

## 8. `VS` on a real card: what the report said, and what was true

2026-09-15. Prompted by a question about the dashboard's Data health panel, which
carried three warnings reading `verb-unknown: result verb 'VS' is not one this
program recognises` — and the fair question that follows: *does that mean this
scorecard is not recognised or processed at all?*

Measured over the same 22 real cards as section 7, still in `/tmp`:

| printed between the team names | cards | `Match Winner` tick | cross-checks available |
|---|---|---|---|
| `DEFEATS` / `DEF. BY` | 19 | present, every one | 3 of 3 |
| `VS` | 3 | absent, every one | 1 of 3 |

The correspondence is exact in both directions, which is what settles it: `VS` is
not a word the reader failed to learn. It is what Match Centre prints when it holds
no result for the fixture, and the tick is missing for the same reason the verb is.

What the three cards actually did: two carry a cancellation tick, are correctly
`decided: false`, and are reported once each as `no-result`. The third was read,
scored and counted — `season.json` records `decided: true`, `result: home`, points
5.6 to 3.3, games 16-13, sets 1-2, a home win because `games-bonus` decides on games
and uses sets only to break a tie — and its printed `TOTAL` row agrees with its four
set rows. Nothing was lost but one redundant confirmation of numbers that were
already confirmed.

So the defect was the sentence, not the reading. A warning that reads as *this card
was not understood* invites a club to go looking for a match that is already on its
ladder, and the two findings it conflated point at different people: an unknown word
is this program's to learn, a separator is the card saying it has nothing to state.
Both halves changed:

- `scorecard.states_no_result`, and a verb cross-check whose detail distinguishes
  three cases — the separator, the unknown word, and no verb printed at all.
- `validate.verb-absent`: a warning that names what could not run *and* says the set
  rows were read and scored anyway. Suppressed entirely on a cancelled card, where
  `no-result` has already reported the fact and a match nobody played has no result
  for a verb to state.

On the same real input, Monday's warnings went **9 → 6**: two duplicates on the
cancelled cards gone, and the third now accurate.

Tests: **359 → 376**. **14 mutations, all caught** — the separator vocabulary and
its normalisation on both sides, the branch that tells the two findings apart, the
cancelled-card suppression in both directions, the rule name, the severity, and the
sentence that says the card still counted.

**The gap that mutation run found.** Dropping the punctuation-stripping from
`_verb_key` survived every test in the suite. Nothing had ever put `DEF. BY` — the
away verb the real exports print — through the validator, because `cardwriter`
derives `LOSES TO`, which needs no normalisation. Read with the full stop left in,
`DEF. BY` matches nothing; read by prefix, it matches the home verb `DEF` and says
the loser won. A card printing it exactly as Match Centre does now sits in the
fixture season, and the whole verb vocabulary is asserted over all ten cards at
once: only the two findings that were meant to be reported are.

## 9. A washed-out round on real cards, and the fabricated player it produced

2026-09-15. Prompted by a question about one real card: *the scorecard is basically
incomplete with the players as "Unspecified Player", so you know that something must
have happened and this match was either cancelled or something happened with the
scorecard, therefore it's not counted — which you can cross check against the
association's points sheet and validate against the scores listed for winner vs
loser.*

Same input as sections 7 and 8, still in `/tmp`, still nothing copied into the
project. Teams are lettered by their finishing position in the Monday B2 grade, as
in section 7, so A is the leader and D is the team whose round 5 card disagrees with
the sheet.

Staged as `/tmp/v9`: the zip's `data/` tree, the `config.toml` that an earlier
`build.py` wrote on the club's own machine (`rounds_total = 10` its only hand edit),
plus a five-line `[competitions.monday-division-b2.byes]` table transcribed from the
sheet's `B` column. That config has no `unplayed` line, which is the point — the run
below is what a club gets from the defaults.

```
cd /tmp/v9 && python3 .../build.py --check --generated 2026-09-15T00:00:00
```

### What the two cards actually are

Both round 2 cards, read structurally — games and cell counts only, no names:

| card | `Match Cancelled` | verb | `Match Winner` | printed `TOTAL` | rubber games | placeholder cells |
|---|---|---|---|---|---|---|
| B v D | ticked | `VS` | absent | 15-1 | 6-1, 6-0, 3-0, 0-0 | none |
| C v E | ticked | `VS` | absent | 0-0 | 0-0, 0-0, 0-0, 0-0 | 4, 2, 2, 4 = 12 |

So the two failure modes the design had been reasoning about hypothetically are both
real, on the same night, in the same grade:

- **B v D is a night that stopped partway.** Two rubbers finished, a third was
  abandoned at 3-0, the fourth never started, and Match Centre kept all of it. The
  card carries 16 real games and no result.
- **C v E is a night that never started.** Every rubber 0-0, and every one of the
  twelve on-court name cells printing `Unspecified player`. The roster block on that
  card *does* carry three real names a side; it is only the on-court cells that were
  never filled in.

The association's sheet marks the whole round `W` for all four teams — its legend
reads `W = wet, unplayed or incomplete match` — and states outright that "cards
missing & cards incomplete have been scored as 0 points for the calculation of the
Points and Positions." A had the bye that round.

### The sheet's own two columns settle the convention

The sheet publishes Prog. Points, Matches Played and Prog. Average, and the three
agree with each other only if a `W` and a `B` are both uncounted:

| team | its numbered rounds | sum | sheet Prog. Points | sheet Matches Played | sheet Average |
|---|---|---|---|---|---|
| A | 6.4, 6.4, 6.4, 6.4 | 25.6 | 25.6 | 4 | 6.40 |
| B | 2.7, 5.6, 5.8 | 14.1 | 14.1 | 3 | 4.70 |
| C | 6.2, 3.4, 3.3 | 12.9 | 12.9 | 3 | 4.30 |
| D | 2.8, 6.0, 3.1 | 11.9 | 11.9 | 3 | 3.97 |
| E | 3.4, 2.8, 3.7 | 9.9 | 9.9 | 3 | 3.30 |

That is what promoted `zero` from "this club's setting" to the shipped default. It is
not only that a bye is worth nothing here; the *washout* is worth nothing too, and
the Matches Played column of `4 / 3 / 3 / 3 / 3` is a second, independent statement
of the same rule. Section 7 had inferred it from the points column alone.

### Before and after, on the same real cards

Run twice with only three lines changed — `Match.counts_as_played` letting a
cancelled match through, `scorecard.names_nobody` returning `False`, and
`games-bonus` averaging again — which is the state the club's own run was in:

|  | before | after | sheet |
|---|---|---|---|
| points | 32.0 / 23.5 / 21.5 / 19.5 / 16.5 | 25.6 / 14.1 / 12.9 / 11.7 / 9.9 | 25.6 / 14.1 / 12.9 / 11.9 / 9.9 |
| B's games | 56-55 | 41-54 | — |
| D's games | 38-75 | 37-60 | — |
| ladder state | `complete: every team has played the same number` | `in progress` | 5 of 10 rounds |
| players | 16 | 15 | — |

Four of five points totals now match to the digit, all five Matches Played match, and
the order is the sheet's order. The remaining 0.2 is D's round 5 card, already
recorded in section 7: the card's set rows, its `TOTAL 9-24` and its `Match Winner`
tick all agree on 2.9 and the sheet implies 3.1. A card that disagrees with the sheet
is left standing.

Three separate defects show up in that table, and only the first was the one asked
about:

**The cancelled card's games were counted while the match was not.** B's 56-55
included the 15-1 it was leading by when the rain came, and D's 38-75 the 1-15 it was
losing by — but neither team's `P` went up, because a cancelled match is not
`decided`. Games from a match that never happened, in a column beside a played count
that excludes it. Nothing reported it.

**The ladder said `complete: every team has played the same number` directly above a
`P` column reading 4 / 3 / 3 / 3 / 3.** Averaging made every team's *counted* total 4
while the printed `P` stayed on matches actually played, so the one sentence that
exists to say "this order is not settled yet" said the opposite, in a grade five
rounds into ten. Flipping the default fixed this as a side effect; it had been
recorded as a smaller finding before this session and was never reproduced this
cleanly.

**A player named `Unspecified Player` was on the dashboard, and nothing said so.**
The before run has 16 players to the after run's 15. The extra one is slug
`unspecified-player`, on no team, with 1 match and 8 sets played — 4 rubbers × 2
sides of the same match, so it played itself. Not one of the 14 warnings mentioned
it. It surfaced in exactly one place: the "players to check" list, which flags anyone
with one or two appearances — which is how the club found it, and why the question
arrived as *why are these people here*.

That last one is the argument for the `players-unrecorded` warning being a warning
rather than a silent drop. On this input it reads:

```
players-unrecorded
    monday-division-b2: data/.../R2/match_scorecard_XXXXXXX.pdf: 12 name cell(s)
    across 4 of 4 rubber(s) print Match Centre's 'Unspecified player' placeholder
    rather than a person, so no name was recorded for them; this match was called off.
        12 placeholder cell(s) in rubber(s) 1, 2, 3, 4
```

One line for twelve cells, and the closing clause tells the club there is nothing to
do. The B v D card gets no such line: it is cancelled too, but its names are real, so
the check is answering "were names recorded" and not "was this card any good".

Monday's warnings went 6 → 8: the two round 2 cards keep their `no-result` lines, and
the placeholder card gains one.

### Two smaller findings this run surfaced, not yet actioned

- `set-tied` says *level at 0-0, so nobody won the set and nothing on the card says
  why it stopped* on rubbers that were plainly never played. Three of them here.
  "Tied" is the wrong word for a rubber with no games in it.
- `app.js` labels `fill_in_appearances` as sets in the players-to-check table, while
  `stats.py` counts matches.

### One documented feature that was never built

Checked while sweeping the docs for the default flip. `README.md` and
`config.example.toml` both promised that multi-season mode gives "player career
totals across seasons"; `CHANGELOG.md` claimed it as shipped. It does not exist.
There is no cross-season aggregation anywhere: `_write_picker` builds a list of
competitions and nothing else, and `app.js` carries the opposite comment — *the only
season this page can name from evidence is its own*.

Corrected in all three files rather than built, and stated as a limitation with its
reason: the season boundary is the line every figure on a page can be traced to a
card across, and totalling over it would put numbers on a page that no one season's
cards account for. Worth building; not worth claiming first.

### Two `config.toml` exposures, closed

- The `config.toml` a run writes holds `[captains]`, `[player_names]` and
  `[aliases]` — three tables whose whole purpose is real people's names — and it was
  not gitignored, so `git add .` after a build would publish a club's membership to
  GitHub. Same exposure as `data/`, which has been ignored for exactly this reason
  since the first commit. Now ignored with the same one-line opt-out, and asserted by
  `git check-ignore` in both directions so `config.example.toml` cannot be swept up
  with it.
- A stray `config.toml` was sitting in the project tree — one machine's state, which
  would have arrived in a club's copy naming somebody else's club. Removed, and a
  test now fails if one is there.

### Tests and mutations

**376 → 419 tests**, all passing:

```
cd tests && rm -rf __pycache__ ../matchcentre/__pycache__ &&
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -t .
```

**23 mutations, all caught** (`/tmp/mutate6.py`) — 8 on the abandonment predicate and
the five aggregation sites that consult it, 8 on the placeholder (its vocabulary, the
punctuation strip, exact-match versus substring, the per-row count, the roster guard,
`describe()`), 4 on the validator's new check including the branch that tells a
called-off card from a played one, and 3 on the browser mirror.

**The mutation run's own bug, which is the lesson worth keeping.** Three model
mutations were reported caught by test classes named `TestOneMatch` and
`TestScoringRulesets`. Neither exists; the real names are `TestMatch` and
`TestScoring`. `python3 -m unittest NoSuchClass` exits nonzero, and a driver that
reads nonzero as "the test caught it" reads a typo as proof. Every driver now
pre-flights each distinct target set unmutated and refuses to start if any of them
fails, so a target that cannot fail for the right reason cannot pass for the wrong
one.

Two real survivors before that fix:

- **Exact match weakened to a substring.** The near-miss name in the test was
  `Spec Unspecified-Jones`, which does not *start* with the placeholder, so a
  prefix-match mutation survived. Rather than invent a name contrived to kill it, the
  mutation was replaced with the implementation somebody would actually write by
  mistake — `"unspecified" in text` — which the existing test does kill, and
  punctuation-strip and singular-only mutations were added beside it.
- **The `average_unplayed` field default.** All four presets now set the flag
  explicitly, house style being explicit-though-redundant, so nothing exercised the
  dataclass default and flipping it survived. Covered by a test that builds a
  `Ruleset` by hand and asserts it pays nothing for a round nobody played.
