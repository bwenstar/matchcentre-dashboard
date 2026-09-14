# Contract

The single source of truth for `matchcentre-dashboard`. Anything in the code that
disagrees with this file is a bug in the code.

This lives in `notes/`, which `.gitignore` excludes. It is a working document for
whoever maintains the tool, not part of what a club downloads.

---

## 0. What the product is

A club exports scorecard PDFs from <https://matchcentre.tennis.com.au>, drops them
in `data/`, runs one command, and gets a self-contained interactive dashboard.

Three hard constraints, inherited and kept:

1. **Standard library only.** No pip install, no build step, no server. `python3
   build.py` and then double-click an HTML file.
2. **The scorecards are the only source of truth for results.** Nothing about a
   result is ever taken from a filename, a directory name, or a config file.
3. **Deterministic output.** Same cards in, byte-identical `season.json` out, so
   `git diff` shows real result changes only. The one wall-clock value is
   `meta.generated`, and the caller can pin it.

Python 3.11+ (`tomllib`). `build.py` checks and says so plainly if older.

---

## 1. The failure this design exists to prevent

The predecessor project derived a competition from cards alone and looked like it
worked. Measured behaviour on real data:

| Symptom | Cause | Consequence |
|---|---|---|
| A team that played and won is awarded a **bye** | one missing card shrinks the discovered team set, so `absent[0]` is read as the resting team | that team is paid its own average for a match it actually played; validation reports zero errors |
| Renumbered draw, phantom teams | draw codes `1..n` appear on **no card**, so they were assigned alphabetically over discovered names | one typo'd banner adds a seventh team and shifts every code; `codes == 1..n` still passes |
| Confidently **reversed** scoreline | the column grid failed *open* to hardcoded pixel constants when the rule count was unexpected | `6-2` read as `2-6`; only a club-specific `TOTAL` row caught it |
| Two seasons merged into one ladder | the competition key was `(day, division)` with no season, and round labels restart at 1 | exporter raised `ValueError` on colliding fixture keys |
| Progress bar read 100% of a 10-round season | `rounds_total` was set to `rounds_played` when no draw was published | a third of the way through a season looked complete |

Every one of those is the same mistake: **absence of evidence recorded as
evidence.** The rules in §7 exist to make that impossible, and they outrank
convenience everywhere in this document.

---

## 2. Onboarding: two steps that feel like one

```
1.  copy PDFs into data/
2.  python3 build.py
```

On a run with no `config.toml`, `build.py`:

1. parses every PDF under `data/`,
2. groups cards into competitions,
3. **writes `config.toml`** pre-filled with everything it found, every derived
   value commented as needing review,
4. **builds the dashboard anyway**, so the user sees a working site immediately,
5. prints a review summary naming each derived identity and the file to edit.

The user edits `config.toml` (or does not) and re-runs. Values present in the
config are *declared* and used verbatim; values absent stay *derived* and stay
flagged. There is no separate `--discover` step to forget — discovery is what a
first run *is*. `--discover` exists only as an explicit "rewrite config.toml from
the cards" escape hatch, and it refuses to overwrite without `--force`.

Nothing in the config can change a *result*. It supplies identity (names,
captains, season labels), the closed team list, the draw, and the rules. A
scoreline only ever comes from a card.

---

## 3. Identity

**A team is identified by its slug, never by an integer.** The slug is derived
from the team name as printed on the card, so it is stable under a new card
arriving, a team being added, or cards being processed in a different order.

```python
team_slug("UQ IGUANAS")  # -> "iguanas"   with strip_team_prefix = "UQ"
team_slug("Kenmore 3")   # -> "kenmore-3"
```

Slug rules (`slugify`), applied in order: casefold; strip a configured
`strip_team_prefix` when present as a leading word; NFKD-normalise and drop
combining marks; map anything not `[a-z0-9]` to `-`; collapse runs; strip ends.
Empty result is an error, never a silent `""`.

The same function slugs player names. `aliases` maps slug → slug and is applied
after slugging, so two spellings of one person reconcile.

Consequences to hold to:

- `head_to_head` keys are `"slug-a|slug-b"` with the pair sorted lexically.
- `Match.home`/`away` are slugs. There is no `home_code`.
- Draw codes, if a club declares them, are **display only** (`Team.code`), never
  a key.
- A team appearing only in `config.toml` and never on a card is legal (it has an
  empty record); a team appearing on a card and not in a declared team list is an
  **error** (§7).

## 3.1 Display names

Cards SHOUT team names and print player names in mixed case. Resolution order for
any display name, first hit wins:

1. `[names]` in `config.toml` — declared, authoritative.
2. The mixed-case spelling printed on a card (player roster block prints
   `Guillaume Evrard`; keep it, do not discard it as the predecessor did).
3. Smart title case of the printed form: title-case each word, but leave a token
   unchanged when it is already all-caps and ≤ 4 characters (`ICBC`, `UQ`, `RTC`).
4. The slug, un-slugified and title-cased.

Every name resolved at level 3 or 4 is reported as derived. Level 2 is derived but
not reported — a card is real evidence.

---

## 4. Match format

The predecessor hardcoded four sets in a fixed `D1, S1, S2, D2` order and "first
to 6 games". Both are club-specific. They become data.

```python
@dataclass(frozen=True, slots=True)
class Slot:
    key: str          # "d1", "s1"  — stable id, used in JSON keys
    label: str        # "Doubles 1", "Singles 2"
    doubles: bool

@dataclass(frozen=True, slots=True)
class SetRule:
    name: str
    games_to_win: int      # 6
    win_by: int            # 1 when a 5-5 tiebreak decides at 6; 2 for advantage
    max_games: int | None   # 6, or 7 with a set tiebreak, or None for advantage

@dataclass(frozen=True, slots=True)
class MatchFormat:
    name: str
    slots: tuple[Slot, ...]
    set_rule: SetRule
    min_sets_for_result: int    # sets that must be complete before a result stands
```

Named set rules: `first-to-6` (6, 1, 6) — the default and the UQTC convention;
`advantage-6` (6, 2, None); `six-game-tiebreak` (6, 2, 7); `fast4` (4, 1, 4);
`pro-8` (8, 1, 8); `short-sets-4` (4, 2, None).

Named formats: `doubles-singles-doubles` (d1 s1 s2 d2 — the predecessor's), plus
`singles-4`, `doubles-4`, `singles-6`, `doubles-3`, `mixed-3`. A club can also
spell slots out in the config.

**Detection.** The card supplies both facts and they are read from it:

- the number of set rows = the number of row bands the grid rules describe;
- each row's discipline from its own marker column: `1&2` (or `1&3`, `2&3`) is a
  doubles row, a bare `1` / `2` / `3` is singles.

Detection produces a format; a declared format is used instead. When both exist
and they **disagree**, that is an error, not a warning, and neither wins — the run
stops and names the card. A silent mismatch here is how a reversed or truncated
card gets scored.

`min_sets_for_result` cannot be detected and defaults to
`ceil(len(slots) * 3 / 4)` (3 of 4, 5 of 6), overridable.

**Set completion** is `SetRule`, not `>= 6`:

```python
def completed(a: int, b: int, rule: SetRule) -> bool
```
`max(a,b) >= games_to_win` and `abs(a-b) >= win_by`, or `max(a,b) == max_games`
when `max_games` is set. Verified failures of the old `>= 6` test that this
fixes: FAST4 `4-2` read as unplayed (blank ladder); pro-8 `6-3` read as a
completed set (a mid-set score scored as final).

---

## 5. Scoring

A `Ruleset` carries everything that turns sets into ladder points. Named presets,
with `games-bonus` the default:

| preset | win / draw / loss | bonus | decided on | draws |
|---|---|---|---|---|
| `games-bonus` | 4 / 3 / 2 | 0.1 per game won | games, then sets | yes |
| `sets-first` | 2 / 1 / 0 | none | sets, then games | yes |
| `rubbers` | sets won | none | sets, then games | yes |
| `simple` | 3 / 1 / 0 | none | sets, then games | yes |

```python
@dataclass(frozen=True, slots=True)
class Ruleset:
    name: str
    win: float
    draw: float | None          # None = draws impossible; a level match is an error
    loss: float
    game_bonus: float           # points per game won
    set_bonus: float
    points_are_sets_won: bool   # `rubbers`
    decide_by: tuple[str, ...]  # ("games", "sets") or ("sets", "games")
    forfeit_award: str          # "as-played" | "full-games" | "fixed"
    forfeit_points: float | None
    ladder_by: tuple[str, ...]  # ("points_average", "points", "games_diff", ...)
    average_unplayed: bool      # score a bye/washout at the team's own average
```

`forfeit_win_points` is **not** a constant. The predecessor's `6.4` is
`win + game_bonus * (slots * games_to_win)` — correct for its own format and
silently wrong for any other. It is computed from the format when
`forfeit_award = "full-games"`.

Two forfeit bugs to fix, both live in the predecessor: a match flagged
`FORFEIT` with *neither* side marked paid **both** sides the forfeit win; and the
value was mispriced under any other format.

Every run prints the active ruleset, set rule and match format, and all three
appear in `season.json` under `meta.rules` and on the dashboard. A club running
the wrong preset must be able to see it without reading code.

---

## 6. Seasons

`[build] seasons = "single" | "multi"`, chosen by the user.

**single** — every card belongs to one season. Output at
`site/<competition-id>/`, competition id `<day>-division-<division>` slugged.

**multi** — the season key is the **first path segment under `data/`**:
`data/2026-winter/round-3/card.pdf` → season `2026-winter`. A card directly in
`data/` falls back to the year on its own date, reported as derived. Output at
`site/<season>/<competition-id>/`, and competition ids carry the season, so the
`(day, division)` collision that raised in the predecessor cannot occur.
`[seasons]` maps a key to a pretty label. Multi mode adds a season picker and
cross-season player career totals; single mode never shows either.

Seasons are never merged into one ladder.

---

## 7. Rules against fabrication

Numbered so code and tests can cite them. Every one is a `check` in
`validate.py`, and each maps to a real failure observed in the predecessor.

- **F1 — No inferred bye without a closed team list.** A bye may be materialised
  only when the competition's teams are *declared* (config `teams` or a declared
  draw). With a derived team list, a round short of cards produces a warning
  naming the round and the count, never a bye. *Prevents: paying a team that
  played.*
- **F2 — No unplayed fixture invented from a derived draw.** A derived draw
  describes only rounds that happened; it never claims a future fixture.
- **F3 — `rounds_total` is null unless declared.** The dashboard shows
  "N rounds played" and no percentage when it is null. *Prevents: 100% of a
  season at round 3.*
- **F4 — Every card is accounted for.** A PDF found under `data/` is parsed and
  scored, or it is reported by path with a reason. Exit status is nonzero if any
  card was seen and not scored. Silence is not an option.
- **F5 — Grid failures fail closed.** If the column grid or row bands cannot be
  read, the card is refused with a diagnosable reason. There is no fallback to
  hardcoded pixel geometry. *Prevents: reversed scorelines.*
- **F6 — At least one independent cross-check must agree.** A parsed card must
  reproduce at least one of: the printed `TOTAL` games/sets row, the result verb
  (`DEFEATS`/`DRAWS WITH`/`LOSES TO`), the `Match Winner` tick. Zero available
  cross-checks is a warning; an available cross-check that *disagrees* is an
  error naming both readings.
- **F7 — Derived identity is marked.** Team names, player names, captains,
  season labels, draw codes and `rounds_total` each carry `declared` or
  `derived` provenance into `season.json`, and the dashboard renders derived
  values with a visible marker and a one-line explanation.
- **F8 — A card for an undeclared team is an error.** When a team list is
  declared, a card naming a team outside it stops the build rather than adding a
  seventh team. When it is derived, it is a warning with the near-miss
  suggestion (edit distance ≤ 2 against known slugs), which is how a typo'd
  banner gets caught.
- **F9 — Duplicate cards are reported, never silently overwritten.** Two cards
  with the same `(round, home, away)` are an error listing both source paths and
  match ids. *The predecessor raised an uncaught `ValueError` here.*
- **F10 — Ladder positions state their basis.** `meta.ladder_complete` is true
  only when every team has the same number of counted matches. When false the
  dashboard labels the ladder "in progress — teams have played unequal numbers of
  matches" instead of implying a settled order.
- **F11 — No finals in the ladder.** A round can only be finals if declared, or
  if the card's own round label is non-numeric (`F`, `SF`, `GF`). Numeric rounds
  are regular. A finals round never contributes ladder points.

---

## 8. Modules

```
build.py                  the one command; arg parsing, orchestration, reporting
matchcentre/
  slugs.py                slugify, team_slug, alias application, smart title case
  format.py               Slot, SetRule, MatchFormat, presets, detection, completed()
  scoring.py              Ruleset, presets, points/result arithmetic
  config.py               read config.toml, write it back with comments, defaults
  pdf.py                  stdlib PDF reader: objects, streams, text, rects
  scorecard.py            one PDF page -> Card (format-agnostic, fails closed)
  model.py                SetResult, Match; scoring applied via Ruleset + format
  competition.py          Team, Player, Round, Competition, Club; slug identity
  discover.py             cards -> competitions; provenance tracking
  stats.py                team and player statistics; ladder; ladder history
  validate.py             the F-rules plus internal consistency checks
  export.py               season.json
  site.py                 write the site tree; picker; asset copy
  cardwriter.py           synthetic scorecard PDF writer (demo data + fixtures)
  web/                    index.html, app.js, styles.css, picker.html
tools/
  demo.py                 generate a fictional season of PDFs into data/
  doctor.py               explain what a PDF is and why it did or did not parse
tests/                    stdlib unittest, no real PDFs, no real people
```

`pdf.py` carries forward unchanged in behaviour plus: `/Encoding` with
`/Differences`, WinAnsi/Standard fallback when a font has no `/ToUnicode` (today
it returns `""` and the card silently loses all text), `code_bytes` inferred
rather than defaulting to 2, and an explicit unsupported-feature error for
`/ObjStm`, xref streams, encryption and form XObjects instead of a wrong parse.

---

## 9. `season.json`

One document per competition. Same shape as the predecessor's, with these
deliberate differences:

- `home_code`/`away_code` → `home`/`away` (slugs). `teams[].code` is optional
  display metadata.
- `head_to_head` keys `"slug|slug"`.
- `by_slot` keys are format slot keys, not the fixed four.
- `meta.rules` = `{scoring, set_rule, format, slots: [...]}`.
- `meta.provenance` = `{teams, players, captains, season, rounds_total, draw,
  format}` each `"declared" | "derived" | "mixed"`.
- `meta.ladder_complete` (F10), `meta.rounds_total` nullable (F3).
- `meta.cards_seen` / `cards_scored` / `cards_rejected` (F4), with
  `validation.rejected[] = {path, reason}`.
- `meta.season_key` and `meta.seasons_mode` for the picker.

`validation.errors[]` / `warnings[]` entries are
`{rule, severity, message, subject, detail}` where `rule` is an F-number or a
consistency-check name.

---

## 10. Verification

The predecessor's tests pinned six real PDFs of real people. Neither ships here.

- `cardwriter.py` writes cards the parser reads; fixtures are fictional clubs and
  fictional players, generated in-process, never committed as binaries.
- The generator emits geometry from the **same named constants** the reader
  consumes, so a golden built from it proves round-tripping, not correctness of
  the geometry. Guard against that self-confirmation with explicit
  *anti*-fixtures: cards written with a deliberate page offset, a missing rule, a
  wrong tick count, an extra row — each asserted to be *refused* (F5, F6), never
  silently scored.
- The equivalence test that matters cannot be committed: run the finished tool
  over the predecessor's real `data/` directory and assert the ladder, points and
  per-player numbers match the known-good output. Done once, by hand, before
  release; recorded in `notes/VERIFICATION.md`.
